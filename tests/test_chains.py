"""Tests for chain-mode composition (chains.py) and result payloads."""

from __future__ import annotations

import harness_bench.chains as chains_module
from harness_bench.chains import (
    CHAIN_LENGTH,
    HALF_CHAIN_IDS,
    STANDARD_CHAIN_IDS,
    all_chains,
    get_chain,
)
from harness_bench.runner_chain import (
    ChainRunResult,
    TurnRecord,
    _batch_prompt,
    _subdir_name,
    _turn_prompt,
    chain_results_to_payload,
)
from harness_bench.tasks import get_task
from harness_bench.versioning import task_number


def test_chainset_is_deterministic() -> None:
    first = {cid: spec.task_ids for cid, spec in all_chains().items()}
    chains_module._CHAINS = None  # force a rebuild
    second = {cid: spec.task_ids for cid, spec in all_chains().items()}
    assert first == second


def test_standard_chains_shape() -> None:
    chains = all_chains()
    assert tuple(f"M{i}" for i in range(1, 11)) == STANDARD_CHAIN_IDS
    for chain_id in STANDARD_CHAIN_IDS:
        spec = chains[chain_id]
        assert spec.length == CHAIN_LENGTH
        assert len(set(spec.task_ids)) == CHAIN_LENGTH


def test_standard_chains_are_disjoint() -> None:
    chains = all_chains()
    seen: set[str] = set()
    for chain_id in STANDARD_CHAIN_IDS:
        ids = set(chains[chain_id].task_ids)
        assert not (ids & seen)
        seen |= ids


def test_half_chains_cover_standard_chains() -> None:
    chains = all_chains()
    assert tuple(
        f"M{i}{suffix}" for i in range(1, 11) for suffix in ("a", "b")
    ) == HALF_CHAIN_IDS
    half = CHAIN_LENGTH // 2
    for chain_id in STANDARD_CHAIN_IDS:
        base = chains[chain_id].task_ids
        assert chains[f"{chain_id}a"].task_ids == base[:half]
        assert chains[f"{chain_id}b"].task_ids == base[half:]


def test_excluded_waves_never_appear() -> None:
    for spec in all_chains().values():
        for task_id in spec.task_ids:
            number = task_number(task_id)
            assert number is not None
            assert not 222 <= number <= 253, f"memory task in {spec.chain_id}"
            assert not 314 <= number <= 330, f"skills task in {spec.chain_id}"


def test_all_chain_tasks_exist_in_registry() -> None:
    for spec in all_chains().values():
        for task_id in spec.task_ids:
            get_task(task_id)  # raises on unknown id


def test_rotations_share_m1_tasks_in_different_order() -> None:
    chains = all_chains()
    m1 = chains["M1"].task_ids
    for chain_id in ("P2", "P3"):
        rotated = chains[chain_id].task_ids
        assert set(rotated) == set(m1)
        assert rotated != m1
    assert chains["P2"].task_ids != chains["P3"].task_ids


def test_endurance_chain_length() -> None:
    assert get_chain("E100").length == 100


def test_subdir_and_prompts_mention_position_and_dir() -> None:
    spec = get_chain("S5")
    task = get_task(spec.task_ids[0])
    subdir = _subdir_name(1, task.id, spec.length)
    assert subdir == f"t01_{task.id}"
    prompt = _turn_prompt(task, 1, spec.length, subdir, virtual=True)
    assert f"/{subdir}/" in prompt
    assert "Задача 1 из 5" in prompt
    assert task.prompt in prompt

    tasks = [get_task(tid) for tid in spec.task_ids]
    subdirs = [_subdir_name(i + 1, t.id, spec.length) for i, t in enumerate(tasks)]
    batch = _batch_prompt(tasks, subdirs, virtual=False)
    for index, (t, sd) in enumerate(zip(tasks, subdirs, strict=True), start=1):
        assert f"Задача {index}/{spec.length} — каталог {sd}/" in batch
        assert t.prompt in batch


def _fake_result() -> ChainRunResult:
    turns = [
        TurnRecord(
            position=1,
            task_id="task_05_greet",
            subdir="t01_task_05_greet",
            reached=True,
            passed_immediate=True,
            message_immediate="ok",
            passed_final=True,
            message_final="ok",
            stats={"agent_input_tokens": 1000, "agent_steps": 4},
        ),
        TurnRecord(
            position=2,
            task_id="task_07_rename_function",
            subdir="t02_task_07_rename_function",
            reached=True,
            passed_immediate=True,
            message_immediate="ok",
            passed_final=False,
            message_final="file clobbered",
        ),
        TurnRecord(
            position=3,
            task_id="task_10_bump_pyproject",
            subdir="t03_task_10_bump_pyproject",
            reached=False,
            passed_final=False,
            message_final="not reached: chain terminated at position 2",
        ),
    ]
    return ChainRunResult(
        chain_id="TEST",
        task_ids=tuple(t.task_id for t in turns),
        delivery="turns",
        turns=turns,
        terminated_at_position=2,
        termination_reason="context overflow at position 2: boom",
        completed=True,
    )


def test_chain_payload_shape_and_aggregates() -> None:
    result = _fake_result()
    assert result.immediate_passed == 2
    assert result.final_passed == 1
    assert result.retention_broken == 1

    payload = chain_results_to_payload(
        [result], delivery="turns", backend_label="gigachat"
    )
    assert payload["mode"] == "chain"
    assert payload["chainset_version"]
    assert payload["total"] == 3
    assert payload["passed"] == 1
    assert payload["immediate_passed"] == 2
    assert payload["retention_broken"] == 1
    assert payload["marathon_score"] == 1 / 3

    chain_payload = payload["chains"][0]
    assert chain_payload["terminated_at_position"] == 2
    assert len(chain_payload["turns"]) == 3

    flattened = payload["tasks"]
    assert [entry["passed"] for entry in flattened] == [True, False, False]
    assert flattened[0]["chain_id"] == "TEST"
    assert flattened[0]["position"] == 1
    assert flattened[1]["passed_immediate"] is True
    assert flattened[2]["reached"] is False
