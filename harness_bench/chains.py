"""Chain-mode ("marathon") benchmark: deterministic multi-task chains.

Chain mode measures long-horizon, single-context endurance: one agent session
receives N tasks sequentially (or as one batched prompt) and must solve them
all without a context reset. Solo runs answer "can the model do the task";
chain runs answer "can it still do the task after an hour of session history,
and does its later work break what it already finished".

Chains are versioned separately from the task set. ``CHAINSET_VERSION`` v1 is
pinned to the current task-set ids and a fixed shuffle seed, so every model
runs the exact same chains in the exact same task order:

- ``S5``          — 5-task smoke chain (core wave) for debugging the runner.
- ``M1``..``M10`` — ten standard 20-task chains, stratified across waves,
                    non-overlapping (each eligible task appears in at most one
                    M-chain).
- ``P2``, ``P3``  — position permutations: the same 20 tasks as ``M1`` rotated
                    by 7 and 13. Comparing M1/P2/P3 separates position effects
                    (context length) from task difficulty.
- ``M1a``/``M1b`` .. ``M10a``/``M10b`` — 10-task half-chains: the first and
                    second halves of each M-chain in the same order. Same 200
                    tasks as M1-M10, so half-chain runs are directly comparable
                    to full-chain runs at half the context depth.
- ``E100``        — one 100-task endurance chain (sampled independently, may
                    overlap M-chains) for "how far until the context dies".
- ``ALL``         — every eligible task in one session (marathon). ``A50``,
                    ``A100``, ``A200`` are *prefixes* of ``ALL``, so the ladder
                    is nested: a task at position 40 sits at position 40 in all
                    four, and "died at 63" means the same thing in each.

Excluded waves: ``memory`` (222-253) tests cross-session persistence via a
workspace-root AGENTS.md convention that is orthogonal to single-context
endurance and breaks under per-task subdirectories; ``skills`` (314-330)
relies on root-level ``.agents/skills`` discovery wired at agent build time,
which likewise cannot be scoped to one subdirectory of a shared session.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from harness_bench.tasks import ALL_TASKS
from harness_bench.versioning import TASK_WAVES, task_number

CHAINSET_VERSION = "v1"
_SEED = "harness-bench-chainset-v1"

CHAIN_LENGTH = 20
_N_STANDARD_CHAINS = 10
_ROTATIONS = {"P2": 7, "P3": 13}
_ENDURANCE_LENGTH = 100
_SMOKE_LENGTH = 5

MARATHON_ID = "ALL"
MARATHON_LADDER = (50, 100, 200)
"""Prefix lengths of ``ALL`` published as their own chains (``A50``...)."""

_EXCLUDED_WAVE_NAMES = ("memory", "skills")


@dataclass(frozen=True)
class ChainSpec:
    """An ordered, immutable sequence of task ids executed in one session."""

    chain_id: str
    task_ids: tuple[str, ...]
    note: str = ""

    @property
    def length(self) -> int:
        return len(self.task_ids)


def _eligible_tasks_by_wave() -> dict[str, list[str]]:
    """Map eligible wave name -> task ids in registry order."""
    excluded = {name for name in _EXCLUDED_WAVE_NAMES}
    by_wave: dict[str, list[str]] = {}
    for task in ALL_TASKS:
        number = task_number(task.id)
        if number is None:
            continue
        wave = next((w for w in TASK_WAVES if w.contains(number)), None)
        if wave is None or wave.name in excluded:
            continue
        by_wave.setdefault(wave.name, []).append(task.id)
    return by_wave


def _largest_remainder_quotas(sizes: dict[str, int], total: int) -> dict[str, int]:
    """Apportion ``total`` slots across waves proportionally to ``sizes``."""
    pool = sum(sizes.values())
    exact = {name: total * count / pool for name, count in sizes.items()}
    quotas = {name: int(value) for name, value in exact.items()}
    shortfall = total - sum(quotas.values())
    by_remainder = sorted(
        sizes,
        key=lambda name: (exact[name] - quotas[name], name),
        reverse=True,
    )
    for name in by_remainder[:shortfall]:
        quotas[name] += 1
    return quotas


def _rotate(items: tuple[str, ...], offset: int) -> tuple[str, ...]:
    offset %= len(items)
    return items[offset:] + items[:offset]


def _build_chainset() -> dict[str, ChainSpec]:
    by_wave = _eligible_tasks_by_wave()
    wave_order = [w.name for w in TASK_WAVES if w.name in by_wave]

    # One seeded shuffle per wave for the M-chains; a chain takes a disjoint
    # slice of each wave's shuffled list, so M-chains never share a task.
    rng = random.Random(f"{_SEED}:waves")
    shuffled: dict[str, list[str]] = {}
    for name in wave_order:
        items = list(by_wave[name])
        rng.shuffle(items)
        shuffled[name] = items

    quotas = _largest_remainder_quotas(
        {name: len(ids) for name, ids in by_wave.items()}, CHAIN_LENGTH
    )
    for name, quota in quotas.items():
        if quota * _N_STANDARD_CHAINS > len(shuffled[name]):
            raise RuntimeError(
                f"chainset {CHAINSET_VERSION}: wave {name!r} too small for "
                f"{_N_STANDARD_CHAINS} disjoint chains × quota {quota}"
            )

    chains: dict[str, ChainSpec] = {}

    smoke_ids = tuple(shuffled["core"][:_SMOKE_LENGTH])
    chains["S5"] = ChainSpec("S5", smoke_ids, note="smoke chain (runner debugging)")

    for index in range(_N_STANDARD_CHAINS):
        picked: list[str] = []
        for name in wave_order:
            quota = quotas[name]
            picked.extend(shuffled[name][index * quota : (index + 1) * quota])
        order_rng = random.Random(f"{_SEED}:M{index + 1}")
        order_rng.shuffle(picked)
        chain_id = f"M{index + 1}"
        chains[chain_id] = ChainSpec(chain_id, tuple(picked), note="standard 20-task chain")

    half = CHAIN_LENGTH // 2
    for index in range(_N_STANDARD_CHAINS):
        base = chains[f"M{index + 1}"]
        for suffix, task_ids in (("a", base.task_ids[:half]), ("b", base.task_ids[half:])):
            chain_id = f"{base.chain_id}{suffix}"
            chains[chain_id] = ChainSpec(
                chain_id,
                task_ids,
                note=f"{'first' if suffix == 'a' else 'second'} half of "
                f"{base.chain_id} (10-task chunk)",
            )

    for chain_id, offset in _ROTATIONS.items():
        chains[chain_id] = ChainSpec(
            chain_id,
            _rotate(chains["M1"].task_ids, offset),
            note=f"M1 rotated by {offset} (position-effect control)",
        )

    endurance_quotas = _largest_remainder_quotas(
        {name: len(ids) for name, ids in by_wave.items()}, _ENDURANCE_LENGTH
    )
    endurance_rng = random.Random(f"{_SEED}:E100")
    picked = []
    for name in wave_order:
        items = list(by_wave[name])
        endurance_rng.shuffle(items)
        picked.extend(items[: endurance_quotas[name]])
    endurance_rng.shuffle(picked)
    chains["E100"] = ChainSpec(
        "E100", tuple(picked), note="100-task endurance chain (may overlap M-chains)"
    )

    # Marathon: every eligible task in one session. Built last with its own
    # seeded generator so adding it leaves every chain above bit-identical.
    # Shuffled across waves on purpose — the registry order runs easy-to-hard,
    # which would make "reached position 60" mean "did the 60 easiest tasks"
    # and confound session depth with task difficulty.
    marathon_rng = random.Random(f"{_SEED}:marathon")
    marathon = [task_id for name in wave_order for task_id in by_wave[name]]
    marathon_rng.shuffle(marathon)
    chains[MARATHON_ID] = ChainSpec(
        MARATHON_ID, tuple(marathon), note="every eligible task in one session"
    )
    for length in MARATHON_LADDER:
        chain_id = f"A{length}"
        chains[chain_id] = ChainSpec(
            chain_id,
            tuple(marathon[:length]),
            note=f"first {length} of {MARATHON_ID} (nested ladder)",
        )

    for spec in chains.values():
        if len(set(spec.task_ids)) != spec.length:
            raise RuntimeError(f"chain {spec.chain_id} contains duplicate task ids")
    return chains


_CHAINS: dict[str, ChainSpec] | None = None


def all_chains() -> dict[str, ChainSpec]:
    """All chains of the current chainset, keyed by chain id (cached)."""
    global _CHAINS
    if _CHAINS is None:
        _CHAINS = _build_chainset()
    return _CHAINS


STANDARD_CHAIN_IDS: tuple[str, ...] = tuple(
    f"M{i + 1}" for i in range(_N_STANDARD_CHAINS)
)

HALF_CHAIN_IDS: tuple[str, ...] = tuple(
    f"M{i + 1}{suffix}" for i in range(_N_STANDARD_CHAINS) for suffix in ("a", "b")
)


def get_chain(chain_id: str) -> ChainSpec:
    chains = all_chains()
    if chain_id not in chains:
        known = ", ".join(sorted(chains))
        raise SystemExit(f"Unknown chain id: {chain_id!r}. Known chains: {known}")
    return chains[chain_id]
