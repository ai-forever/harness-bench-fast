"""Actual transport-client execution, host action replay and paired registry."""

from __future__ import annotations

import json
import os
from fractions import Fraction
from threading import Thread

import pytest

from harness_bench.feasibility_tasks import (
    FEASIBILITY_CASES,
    FEASIBILITY_PAIRS,
    FEASIBILITY_TASKS,
    legacy_registry,
)
from harness_bench.feasibility_tasks.hard_search import service as hard_search_service
from harness_bench.feasibility_tasks.search_cases import target_id
from harness_bench.feasibility_tasks.service import contract_service, search_service
from harness_bench.feasibility_tasks.trusted_service import get_session, trusted_service_available
from harness_bench.reflect_tasks.common import JOURNAL_FILE, STATE_FILE, call
from harness_bench.tasks import ALL_TASKS, SUITES, get_task

LEGACY_TASKS, LEGACY_CASES = legacy_registry()


@pytest.fixture(autouse=True)
def require_unix_service(request):
    if request.node.name != "test_registry_and_no_half_labels" and not trusted_service_available():
        pytest.skip("Unix-socket host service unavailable")


def entry_for(task):
    return (LEGACY_CASES if any(task is t for t in LEGACY_TASKS) else FEASIBILITY_CASES)[task.id]


def pick(slug, side):
    return next(
        t
        for t in LEGACY_TASKS
        if LEGACY_CASES[t.id]["pair"]["slug"] == slug and LEGACY_CASES[t.id]["side"] == side
    )


def service_for(task):
    entry = entry_for(task)
    identity = task.id.split("_")[1]
    if entry["kind"] == "contract":
        return contract_service(identity)
    spec = entry["pair"][entry["side"]]
    if not any(task is t for t in LEGACY_TASKS):
        return hard_search_service(identity, spec)
    return search_service(identity, spec, target_id(spec))


def send_file(ws, task, command, value):
    (ws / "input.json").write_text(json.dumps(value), encoding="utf-8")
    return call(ws, service_for(task), command, "--file", "input.json")


@pytest.mark.parametrize("command", ["submit", "plan", "finish"])
@pytest.mark.parametrize(
    "location",
    [
        "relative_inside",
        "absolute_inside",
        "symlink_inside",
        "absolute_outside",
        "relative_outside",
        "symlink_outside",
    ],
)
def test_file_paths_are_documented_and_replayed(tmp_path, command, location):
    task = (
        pick("aggregate_identifiability", "positive")
        if command == "submit"
        else pick("search_pool", "negative")
    )
    ws = tmp_path / "workspace"
    ws.mkdir()
    task.setup(ws)
    plan = {"decision": "stop", "method": "scan", "max_reads": 0, "success_probability": [1, 1000]}
    answer = {
        "status": "solved",
        "solution": {
            "world": [3, 7, 11, 5, 8, 4, 9, 6],
            "target": 3,
            "observation_weights": {"aggregate-24": 1},
        },
    }
    payload = {"submit": answer, "plan": plan, "finish": {"found_id": None}}[command]
    if command == "finish":
        send_file(ws, task, "plan", plan)
    inside, outside = ws / "answer.json", tmp_path / "outside.json"
    inside.write_text(json.dumps(payload))
    outside.write_text(json.dumps(payload))
    link = ws / "linked.json"
    if location.startswith("symlink"):
        link.symlink_to(outside if location.endswith("outside") else inside)
    argument = {
        "relative_inside": "./answer.json",
        "absolute_inside": str(inside),
        "symlink_inside": "linked.json",
        "absolute_outside": str(outside),
        "relative_outside": "../outside.json",
        "symlink_outside": "linked.json",
    }[location]
    response, code = call(ws, service_for(task), command, "--file", argument)
    rejected = location.endswith("outside")
    if rejected:
        assert code == 2
        assert "workspace" in response["message"]
        assert "/tmp" in response["message"]
        # Recover the payload, but preserve the documented failed-call penalty.
        response, code = call(ws, service_for(task), command, "--file", "./answer.json")
    assert code == 0, response
    if command == "plan":
        send_file(ws, task, "finish", {"found_id": None})
    result = task.verify(ws)
    assert result.passed is not rejected, result.message
    if rejected:
        assert json.loads(result.message)["wave19"]["invalid_calls"] == 1


def test_actual_sparse_448_submission_passes_host_replay(tmp_path):
    task = pick("aggregate_identifiability", "positive")
    task.setup(tmp_path)
    send_file(
        tmp_path,
        task,
        "submit",
        {
            "status": "solved",
            "solution": {
                "world": [3, 7, 11, 5, 8, 4, 9, 6],
                "target": 3,
                "observation_weights": {"aggregate-24": 1},
            },
        },
    )
    result = task.verify(tmp_path)
    assert result.passed, result.message


@pytest.mark.parametrize("task", FEASIBILITY_TASKS, ids=lambda t: t.id)
def test_gold_and_idle_workspace(task, tmp_path):
    task.setup(tmp_path)
    assert not task.verify(tmp_path).passed
    task.apply_gold(tmp_path)
    result = task.verify(tmp_path)
    assert result.passed, result.message
    assert "120 секунд" in task.prompt
    for visible in (task.prompt, (tmp_path / "docs/taskctl.md").read_text()):
        assert "Файлы для --file должны находиться внутри рабочего каталога задачи" in visible
        assert "/tmp/answer.json" in visible
        assert "лишает зачёта даже после исправления" in visible
    assert task.min_timeout_seconds == task.max_timeout_seconds == 120
    source = json.loads((tmp_path / "contract.json").read_text())
    assert "status" not in source and "kind" not in source
    metrics = json.loads(result.message)["wave19"]
    assert metrics["decision_correct"]
    assert metrics["extra_requests"] == 0


@pytest.mark.parametrize("task", FEASIBILITY_TASKS, ids=lambda t: t.id)
def test_writing_a_report_without_tool_actions_never_passes(task, tmp_path):
    task.setup(tmp_path)
    (tmp_path / "report.json").write_text('{"status":"solved"}')
    assert not task.verify(tmp_path).passed


@pytest.mark.parametrize("side", ["positive", "negative"])
def test_modified_public_inputs_and_client_fail_verification(tmp_path, side):
    task = pick("clock_contract", side)
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    for path in ("contract.json", "tools/taskctl", "docs/taskctl.md", ".svc/connection.json"):
        p = tmp_path / path
        original = p.read_bytes()
        p.write_bytes(original + b"tamper")
        assert not task.verify(tmp_path).passed
        p.write_bytes(original)
    assert task.verify(tmp_path).passed


def test_workspace_history_rollback_cannot_erase_an_invalid_call(tmp_path):
    task = pick("search_count", "negative")
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    assert task.verify(tmp_path).passed
    _, code = call(tmp_path, service_for(task), "bogus")
    assert code != 0
    # The old client stored authoritative history here. Empty or fabricated
    # workspace history must no longer be able to hide the failed real call.
    for path in (JOURNAL_FILE, STATE_FILE):
        (tmp_path / path).write_text("")
    replayed = get_session(tmp_path).replay()
    assert any(code != 0 for _, _, code in replayed.calls)
    assert not task.verify(tmp_path).passed
    # Remove the forged legacy files as well: rejection must persist because
    # of the real invalid call, not merely the unexpected .svc directory items.
    for path in (JOURNAL_FILE, STATE_FILE):
        (tmp_path / path).unlink()
    result = task.verify(tmp_path)
    assert not result.passed
    assert json.loads(result.message)["wave19"]["invalid_calls"] >= 1


@pytest.mark.parametrize("filename", ["contract.json", "tools/taskctl", ".svc/connection.json"])
def test_fifo_is_rejected_without_hanging(filename, tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO unavailable")
    task = pick("clock_contract", "negative")
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    path = tmp_path / filename
    path.unlink()
    os.mkfifo(path)
    results = []
    worker = Thread(target=lambda: results.append(task.verify(tmp_path)), daemon=True)
    worker.start()
    worker.join(3)
    assert not worker.is_alive(), "verification blocked on an agent-created FIFO"
    assert len(results) == 1 and not results[0].passed


def test_oversized_input_is_rejected(tmp_path):
    task = pick("clock_contract", "negative")
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    with (tmp_path / "contract.json").open("ab") as f:
        f.truncate(100_000_000)
    assert not task.verify(tmp_path).passed


@pytest.mark.parametrize(
    "task",
    [t for t in FEASIBILITY_TASKS if FEASIBILITY_CASES[t.id]["kind"] == "contract"],
    ids=lambda t: t.id,
)
def test_opposite_half_answer_is_rejected(task, tmp_path):
    entry = entry_for(task)
    other = entry["pair"]["positive" if entry["side"] == "negative" else "negative"]
    field = "solution" if other["spec"]["status"] == "solved" else "certificate"
    task.setup(tmp_path)
    send_file(tmp_path, task, "submit", {"status": other["spec"]["status"], field: other["gold"]})
    assert not task.verify(tmp_path).passed


def test_contract_multiple_submissions_rejected(tmp_path):
    task = pick("clock_contract", "positive")
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    call(tmp_path, service_for(task), "submit", "--file", "answer.json")
    assert not task.verify(tmp_path).passed


def test_missing_required_diagnosis_is_rejected_even_with_correct_hidden_value(tmp_path):
    task = pick("search_count", "negative")
    task.setup(tmp_path)
    send_file(
        tmp_path,
        task,
        "plan",
        {"decision": "stop", "method": "scan", "max_reads": 0, "success_probability": [1, 1000]},
    )
    send_file(tmp_path, task, "finish", {"found_id": None})
    result = task.verify(tmp_path)
    assert not result.passed
    assert "diagnostics" in result.message


def test_diagnostic_cost_changes_probability(tmp_path):
    task = pick("search_count", "negative")
    task.setup(tmp_path)
    call(tmp_path, service_for(task), "count")
    send_file(
        tmp_path,
        task,
        "plan",
        {"decision": "stop", "method": "scan", "max_reads": 0, "success_probability": [10, 10000]},
    )
    send_file(tmp_path, task, "finish", {"found_id": None})
    result = task.verify(tmp_path)
    assert not result.passed
    metrics = json.loads(result.message)["wave19"]
    assert metrics["max_success_probability"] == [9, 10000]
    assert Fraction(*metrics["probability_error"]) == Fraction(1, 10000)


def test_unlucky_95_percent_execution_passes(tmp_path):
    task = pick("search_boundary", "positive")
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    result = task.verify(tmp_path)
    assert result.passed, result.message
    metrics = json.loads(result.message)["wave19"]
    assert metrics["decision_correct"] and not metrics["target_found"]
    assert metrics["unique_reads"] == 95
    assert metrics["declared_probability"] == [19, 20]
    assert metrics["success_basis"] == "strategy"


def test_lucky_hit_rescues_bad_94_percent_decision(tmp_path):
    task = pick("search_boundary", "negative")
    task.setup(tmp_path)
    send_file(
        tmp_path,
        task,
        "plan",
        {"decision": "search", "method": "scan", "max_reads": 94, "success_probability": [94, 100]},
    )
    call(tmp_path, service_for(task), "read", "--id", "r00099")
    send_file(tmp_path, task, "finish", {"found_id": "r00099"})
    result = task.verify(tmp_path)
    assert result.passed, result.message
    metrics = json.loads(result.message)["wave19"]
    assert metrics["target_found"] and not metrics["decision_correct"]
    assert metrics["success_basis"] == "verified_find"
    assert not metrics["strategy_correct"]


@pytest.mark.parametrize(
    "plan",
    [
        None,
        {},
        {"decision": "stop"},
        {"decision": "search", "method": "scan", "max_reads": 1, "success_probability": [0, 1]},
    ],
)
def test_verified_find_overrides_missing_or_wrong_plan_and_diagnosis(tmp_path, plan):
    task = pick("search_count", "negative")
    spec = entry_for(task)["pair"]["negative"]
    task.setup(tmp_path)
    if plan is not None:
        _, code = send_file(tmp_path, task, "plan", plan)
        assert code == 0
    found = target_id(spec)
    _, code = call(tmp_path, service_for(task), "read", "--id", found)
    assert code == 0
    send_file(tmp_path, task, "finish", {"found_id": found})
    result = task.verify(tmp_path)
    assert result.passed, result.message
    metrics = json.loads(result.message)["wave19"]
    assert metrics["success_basis"] == "verified_find"
    assert not metrics["decision_correct"]
    assert not metrics["strategy_correct"]


def test_unnecessary_diagnostics_do_not_veto_verified_find(tmp_path):
    task = pick("search_pool", "positive")
    spec = entry_for(task)["pair"]["positive"]
    task.setup(tmp_path)
    send_file(tmp_path, task, "plan", {})
    for _ in range(2):
        _, code = call(tmp_path, service_for(task), "count")
        assert code == 0
    found = target_id(spec)
    call(tmp_path, service_for(task), "read", "--id", found)
    send_file(tmp_path, task, "finish", {"found_id": found})
    result = task.verify(tmp_path)
    assert result.passed, result.message
    metrics = json.loads(result.message)["wave19"]
    assert metrics["diagnostic_requests"] == 2
    assert not metrics["strategy_correct"]


@pytest.mark.parametrize("read_target", [False, True])
def test_unsupported_found_id_cannot_receive_find_credit(tmp_path, read_target):
    task = pick("search_boundary", "negative")
    task.setup(tmp_path)
    if read_target:
        call(tmp_path, service_for(task), "read", "--id", "r00099")
    send_file(tmp_path, task, "finish", {"found_id": "r00098" if read_target else "r00099"})
    result = task.verify(tmp_path)
    assert not result.passed
    assert json.loads(result.message)["wave19"]["success_basis"] == "none"


def test_verified_find_cannot_override_query_budget(tmp_path):
    task = pick("search_pool", "positive")
    spec = entry_for(task)["pair"]["positive"]
    task.setup(tmp_path)
    found = target_id(spec)
    for _ in range(spec["budget"]):
        _, code = call(tmp_path, service_for(task), "read", "--id", found)
        assert code == 0
    _, code = call(tmp_path, service_for(task), "read", "--id", found)
    assert code != 0
    send_file(tmp_path, task, "finish", {"found_id": found})
    result = task.verify(tmp_path)
    assert not result.passed
    assert json.loads(result.message)["wave19"]["success_basis"] == "none"


def test_verified_find_cannot_override_illegal_command(tmp_path):
    task = pick("search_boundary", "negative")
    task.setup(tmp_path)
    call(tmp_path, service_for(task), "read", "--id", "r00099")
    _, code = call(tmp_path, service_for(task), "read", "--id", "r０００９９")
    assert code != 0
    send_file(tmp_path, task, "finish", {"found_id": "r00099"})
    result = task.verify(tmp_path)
    assert not result.passed
    assert json.loads(result.message)["wave19"]["success_basis"] == "none"


def test_late_plan_without_find_cannot_claim_strategy_credit(tmp_path):
    task = pick("search_boundary", "positive")
    task.setup(tmp_path)
    call(tmp_path, service_for(task), "read", "--id", "r00000")
    send_file(
        tmp_path,
        task,
        "plan",
        {"decision": "search", "method": "scan", "max_reads": 95, "success_probability": [95, 100]},
    )
    for number in range(1, 95):
        call(tmp_path, service_for(task), "read", "--id", f"r{number:05}")
    send_file(tmp_path, task, "finish", {"found_id": None})
    result = task.verify(tmp_path)
    assert not result.passed
    metrics = json.loads(result.message)["wave19"]
    assert metrics["success_basis"] == "none"
    assert "search before plan" in metrics["strategy_reason"]


def test_always_refuse_fails_positive_search(tmp_path):
    task = pick("search_pool", "positive")
    task.setup(tmp_path)
    send_file(
        tmp_path,
        task,
        "plan",
        {"decision": "stop", "method": "scan", "max_reads": 0, "success_probability": [1, 1]},
    )
    send_file(tmp_path, task, "finish", {"found_id": None})
    assert not task.verify(tmp_path).passed


def test_read_before_plan_or_search_after_stop_fails(tmp_path):
    task = pick("search_pool", "negative")
    task.setup(tmp_path)
    call(tmp_path, service_for(task), "read", "--id", "r00000")
    send_file(
        tmp_path,
        task,
        "plan",
        {"decision": "stop", "method": "scan", "max_reads": 0, "success_probability": [9, 10000]},
    )
    send_file(tmp_path, task, "finish", {"found_id": None})
    assert not task.verify(tmp_path).passed


def test_exact_lookup_uses_one_real_call(tmp_path):
    task = pick("search_index", "positive")
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    result = task.verify(tmp_path)
    assert result.passed
    metrics = json.loads(result.message)["wave19"]
    assert metrics["query_count"] == 1 and metrics["target_found"]


def test_registry_and_no_half_labels():
    assert len(FEASIBILITY_TASKS) == 40 and len(FEASIBILITY_PAIRS) == 20
    assert FEASIBILITY_TASKS == ALL_TASKS[431:471] == SUITES["feasibility"]
    assert [int(t.id.split("_")[1]) for t in FEASIBILITY_TASKS] == list(range(432, 472))
    assert all(get_task(t.id) is t for t in FEASIBILITY_TASKS)
    assert (
        len(
            {
                t.setup_files["CERTIFICATES.md"]
                for t in FEASIBILITY_TASKS
                if "CERTIFICATES.md" in t.setup_files
            }
        )
        == 1
    )
    for ids in FEASIBILITY_PAIRS.values():
        first, second = map(get_task, ids)
        assert first.name == second.name
        assert first.tags == second.tags
        assert {FEASIBILITY_CASES[i]["side"] for i in ids} == {"positive", "negative"}
    for parity in (0, 1):
        assert {
            FEASIBILITY_CASES[t.id]["side"]
            for t in FEASIBILITY_TASKS
            if int(t.id.split("_")[1]) % 2 == parity
        } == {"positive", "negative"}


def test_unicode_aliases_are_not_distinct_candidates(tmp_path):
    task = pick("search_boundary", "positive")
    task.setup(tmp_path)
    send_file(
        tmp_path,
        task,
        "plan",
        {"decision": "search", "method": "scan", "max_reads": 95, "success_probability": [95, 100]},
    )
    _, code = call(tmp_path, service_for(task), "read", "--id", "r０００００")
    assert code != 0
    send_file(tmp_path, task, "finish", {"found_id": None})
    assert not task.verify(tmp_path).passed


def test_null_plan_does_not_allow_a_free_replan(tmp_path):
    task = pick("search_pool", "positive")
    task.setup(tmp_path)
    _, code = send_file(tmp_path, task, "plan", None)
    assert code != 0
    task.apply_gold(tmp_path)
    result = task.verify(tmp_path)
    assert not result.passed
    assert json.loads(result.message)["wave19"]["extra_requests"] >= 1


@pytest.mark.parametrize(
    "slug,side,command",
    [
        ("clock_contract", "positive", "submit"),
        ("search_pool", "positive", "plan"),
        ("search_pool", "positive", "finish"),
    ],
)
def test_missing_input_file_cannot_be_erased_by_later_success(slug, side, command, tmp_path):
    task = pick(slug, side)
    task.setup(tmp_path)
    _, code = call(tmp_path, service_for(task), command, "--file", "missing.json")
    assert code != 0
    task.apply_gold(tmp_path)
    result = task.verify(tmp_path)
    assert not result.passed
    assert json.loads(result.message)["wave19"]["extra_requests"] >= 1


def test_extra_reads_counted_once_even_after_success_and_over_plan(tmp_path):
    task = pick("search_pool", "positive")
    spec = entry_for(task)["pair"]["positive"]
    task.setup(tmp_path)
    send_file(
        tmp_path,
        task,
        "plan",
        {"decision": "search", "method": "scan", "max_reads": 8, "success_probability": [1, 1]},
    )
    found = target_id(spec)
    call(tmp_path, service_for(task), "read", "--id", found)
    call(tmp_path, service_for(task), "read", "--id", found)
    send_file(tmp_path, task, "finish", {"found_id": found})
    result = task.verify(tmp_path)
    assert result.passed, result.message
    assert json.loads(result.message)["wave19"]["extra_requests"] == 1
    assert json.loads(result.message)["wave19"]["success_basis"] == "verified_find"
