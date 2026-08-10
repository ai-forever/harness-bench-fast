"""Tests for chain-mode composition (chains.py) and result payloads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import harness_bench.chains as chains_module
from harness_bench.chains import (
    CHAIN_LENGTH,
    CHAINSET_VERSION,
    HALF_CHAIN_IDS,
    STANDARD_CHAIN_IDS,
    all_chains,
    get_chain,
)
from harness_bench.runner import set_results_json_command
from harness_bench.runner_chain import (
    ChainRunResult,
    TurnRecord,
    _batch_prompt,
    _planned_chain,
    _resume_completed_chains,
    _subdir_name,
    _turn_prompt,
    _write_chain_results_json,
    chain_results_to_payload,
    run_chains,
)
from harness_bench.tasks import get_task
from harness_bench.versioning import task_number


def test_chainset_is_deterministic() -> None:
    first = {cid: spec.task_ids for cid, spec in all_chains().items()}
    chains_module._CHAINS = None  # force a rebuild
    second = {cid: spec.task_ids for cid, spec in all_chains().items()}
    assert first == second


def test_chainset_is_pinned_to_its_version() -> None:
    """Chains come out of a seeded shuffle over the task registry, so adding or
    removing one task reshuffles most of them — 21 of 38 when this was written
    — while CHAINSET_VERSION still says v1. Runs from either side would then
    carry the same stamp without being the same chains. Changing the set is
    fine; changing it silently is not, so bump CHAINSET_VERSION and this hash
    together."""
    digest = hashlib.sha256(
        json.dumps(
            {cid: list(spec.task_ids) for cid, spec in all_chains().items()},
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]

    assert (CHAINSET_VERSION, digest) == ("v1", "a7c266e04f150206")


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


def _one_turn_chain(**overrides: object) -> ChainRunResult:
    turn_fields: dict[str, object] = {
        "position": 1,
        "task_id": "task_05_greet",
        "subdir": "t1_task_05_greet",
    }
    turn_fields.update(overrides.pop("turn", {}))  # type: ignore[arg-type]
    return ChainRunResult(
        chain_id="S5",
        task_ids=("task_05_greet",),
        delivery="file",
        turns=[TurnRecord(**turn_fields)],  # type: ignore[arg-type]
        **overrides,  # type: ignore[arg-type]
    )


def test_resume_preserves_unmeasured_retention_and_context_size(tmp_path: Path) -> None:
    """A resume rewrites the whole artifact out of what it read back, so a
    field dropped on the way in is gone from the published run."""
    written = _one_turn_chain(
        completed=True,
        turn={
            "reached": True,
            "passed_final": True,
            "message_final": "ok",
            "context_messages_before": 12,
            "context_chars_before": 3456,
            "stats": {"agent_steps": 4, "agent_tool_calls_by_name": {"execute": 3}},
        },
    )
    path = tmp_path / "chain.json"
    _write_chain_results_json(
        [written], path, delivery="file", backend_label="stub", command=None
    )

    turn = _resume_completed_chains(path, ["S5"])["S5"].turns[0]

    assert turn.passed_immediate is None  # "not measured" must survive the trip
    assert turn.context_chars_before == 3456
    assert turn.stats["agent_tool_calls_by_name"] == {"execute": 3}


def test_a_chain_scored_against_a_live_agent_is_re_run_not_resumed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "chain.json"
    _write_chain_results_json(
        [_one_turn_chain(scoring_raced=True, completed=False)],
        path,
        delivery="file",
        backend_label="stub",
        command=None,
    )

    assert _resume_completed_chains(path, ["S5"]) == {}


def test_planned_chains_state_the_plan_before_anything_runs() -> None:
    """batch/file deliveries produce nothing until a chain ends — hours on a
    marathon — so the artifact has to describe the run from the start."""
    spec = get_chain("S5")

    payload = chain_results_to_payload(
        [_planned_chain(spec, "file")], delivery="file", backend_label="stub"
    )

    chain = payload["chains"][0]
    assert chain["completed"] is False
    assert chain["task_ids"] == list(spec.task_ids)
    assert chain["turns"][0]["subdir"] == f"t01_{spec.task_ids[0]}"


def test_chain_artifact_records_the_command(tmp_path: Path) -> None:
    """Chain runs turn a dozen knobs — delivery, window, step cap, reasoning —
    and the artifact is the only place the combination is written down."""
    command = "python -m harness_bench run-chain --chain ALL --delivery file"
    path = tmp_path / "chain.json"
    set_results_json_command(command)
    try:
        run_chains([], delivery="file", json_output=path)
    finally:
        set_results_json_command(None)

    assert json.loads(path.read_text(encoding="utf-8"))["command"] == command


def test_turn_only_flags_are_called_out_on_a_batch_delivery(
    tmp_path: Path, capsys: object
) -> None:
    run_chains(
        [], delivery="file", turn_timeout=30.0, json_output=tmp_path / "chain.json"
    )

    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "--turn-timeout" in out
    assert "turns delivery only" in out
