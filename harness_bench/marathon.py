"""Marathon task order: every eligible task, one session, one fixed order.

The solo runners give every task a clean session and answer *can the model do
this task*. The marathon hands an agent the whole task set at once and answers
*how far does it get* — the only long-horizon question whose answer is not
already implied by the solo score.

The order is fixed by a seed and versioned by ``MARATHON_VERSION`` so every
model walks the same list, and pinned by a hash test so it cannot drift
silently when the task registry changes.

Order is shuffled across waves on purpose. The registry runs roughly
easy-to-hard, so on a registry-ordered list "reached position 60" would mean
"did the 60 easiest tasks" and the depth an agent survived would be
indistinguishable from the difficulty it happened to face.

Excluded waves: ``memory`` (222-253) tests cross-session persistence through a
workspace-root ``AGENTS.md`` convention, and ``skills`` (314-330) relies on
root-level ``.agents/skills`` discovery wired at agent build time. A marathon
gives each task its own subdirectory of one shared root — fixtures collide by
filename otherwise — so neither wave can be scoped to its own task, and both
would additionally collide with each other over the single root. That caps the
marathon at 342 of the 391 tasks; marathon scores are therefore not comparable
with the solo leaderboard.
"""

from __future__ import annotations

import random

from harness_bench.tasks import ALL_TASKS
from harness_bench.versioning import TASK_WAVES, task_number

MARATHON_VERSION = "v1"

_SEED = "harness-bench-chainset-v1:marathon"
"""Kept verbatim from the first implementation: the seed *is* the published
order, and rewording it would silently renumber every position and orphan the
runs already measured against it."""

_EXCLUDED_WAVE_NAMES = ("memory", "skills")

_ORDER: tuple[str, ...] | None = None


def _eligible_task_ids() -> list[str]:
    """Eligible tasks in registry order, grouped by wave."""
    by_wave: dict[str, list[str]] = {}
    for task in ALL_TASKS:
        number = task_number(task.id)
        if number is None:
            continue
        wave = next((w for w in TASK_WAVES if w.contains(number)), None)
        if wave is None or wave.name in _EXCLUDED_WAVE_NAMES:
            continue
        by_wave.setdefault(wave.name, []).append(task.id)
    return [
        task_id
        for wave in TASK_WAVES
        if wave.name in by_wave
        for task_id in by_wave[wave.name]
    ]


def marathon_task_ids() -> tuple[str, ...]:
    """Every eligible task, in the one published marathon order (cached)."""
    global _ORDER
    if _ORDER is None:
        order = _eligible_task_ids()
        random.Random(_SEED).shuffle(order)
        if len(set(order)) != len(order):
            raise RuntimeError("marathon order contains duplicate task ids")
        _ORDER = tuple(order)
    return _ORDER


def marathon_length() -> int:
    return len(marathon_task_ids())
