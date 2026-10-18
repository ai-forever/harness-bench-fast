from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from harness_bench.feasibility_tasks.pair_metrics import compute_pair_metrics

# Deliberately interleaved: adjacency and odd/even task IDs cannot recover pairs.
PAIRS = {
    f"scenario_{index}": (f"task_{432 + index}_example", f"task_{452 + index}_example")
    for index in range(20)
}
LEFT, RIGHT = PAIRS["scenario_0"]


def record(task_id, passed=True, **kwargs):
    return {"task_id": task_id, "passed": passed, **kwargs}


def test_complete_pair_uses_full_denominator_and_exact_membership():
    metric = compute_pair_metrics([record(LEFT), record(RIGHT)], PAIRS)
    assert metric["passed_pairs"] == metric["completed_pairs"] == 1
    assert metric["total_pairs"] == 20
    assert metric["incomplete_pairs"] == 19
    assert metric["score"] == 0.05
    adjacent = compute_pair_metrics([record(LEFT), record(PAIRS["scenario_1"][0])], PAIRS)
    assert adjacent["passed_pairs"] == adjacent["completed_pairs"] == 0


@pytest.mark.parametrize(
    "records,completed", [([record(LEFT)], 0), ([record(LEFT), record(RIGHT, False)], 1)]
)
def test_missing_and_failed_halves(records, completed):
    metric = compute_pair_metrics(records, PAIRS)
    assert metric["score"] == 0
    assert metric["completed_pairs"] == completed
    assert metric["incomplete_pairs"] == 20 - completed


def test_explicit_repeats_never_combine_successes_across_attempts():
    metric = compute_pair_metrics(
        [
            record(LEFT, True, attempt=1, attempts=2),
            record(LEFT, False, attempt=2, attempts=2),
            record(RIGHT, False, attempt=1, attempts=2),
            record(RIGHT, True, attempt=2, attempts=2),
        ],
        PAIRS,
    )
    assert metric["pairing"] == "attempt"
    assert metric["passed_pairs"] == 0
    assert metric["total_pairs"] == 40
    assert metric["completed_pairs"] == 2
    assert [c["score"] for c in metric["cohorts"]] == [0, 0]


def test_legacy_occurrences_are_labeled_and_not_best_of_repeats():
    metric = compute_pair_metrics(
        [
            record(LEFT),
            record(LEFT, False),
            record(RIGHT, False),
            record(RIGHT),
        ],
        PAIRS,
    )
    assert metric["pairing"] == "per_task_occurrence_order"
    assert metric["repeat_count"] == 2
    assert metric["passed_pairs"] == 0


def test_missing_declared_repeats_remain_in_denominator():
    metric = compute_pair_metrics(
        [
            record(LEFT, attempt=1, attempts=3),
            record(RIGHT, attempt=1, attempts=3),
        ],
        PAIRS,
    )
    assert metric["score"] == pytest.approx(1 / 60)
    assert metric["incomplete_pairs"] == 59


@pytest.mark.parametrize(
    "extra",
    [
        {"failure_kind": "infrastructure"},
        {"measurement_valid": False},
        {"passed": "true"},
    ],
)
def test_invalid_records_never_pass(extra):
    bad = record(RIGHT)
    bad.update(extra)
    metric = compute_pair_metrics([record(LEFT), bad], PAIRS)
    assert metric["passed_pairs"] == 0
    assert metric["score"] is None
    assert metric["measurement_valid"] is False
    assert metric["invalid_pairs"] == 1


def test_duplicate_explicit_attempts_cannot_inflate_score():
    metric = compute_pair_metrics(
        [
            record(LEFT, attempt=1),
            record(LEFT, attempt=1),
            record(RIGHT, attempt=1),
        ],
        PAIRS,
    )
    assert metric["passed_pairs"] == 0
    assert metric["score"] is None
    assert metric["repeat_count"] == 1


def test_mixed_explicit_and_implicit_attempts_are_invalid():
    metric = compute_pair_metrics([record(LEFT, attempt=1), record(RIGHT)], PAIRS)
    assert metric["passed_pairs"] == 0
    assert metric["score"] is None


@pytest.mark.parametrize("attempt", [None, True, 0, -1, "1", 1.5, 10001])
def test_malformed_attempt_is_invalid(attempt):
    metric = compute_pair_metrics([record(LEFT, attempt=attempt), record(RIGHT)], PAIRS)
    assert metric["passed_pairs"] == 0
    assert metric["score"] is None
    assert metric["malformed_records"] == 1


def test_timeout_is_failure_but_not_infrastructure_invalidity():
    metric = compute_pair_metrics(
        [
            record(LEFT),
            record(RIGHT, False, failure_kind="timeout"),
        ],
        PAIRS,
    )
    assert metric["score"] == 0
    assert metric["measurement_valid"] is True


def test_unknown_old_or_malformed_task_ids_do_not_create_pairs():
    assert (
        compute_pair_metrics(
            [
                record("task_431_example"),
                record("task_432_wrong_slug"),
                record(None),
                record([]),
                {},
            ],
            PAIRS,
        )
        is None
    )
    metric = compute_pair_metrics([record(LEFT), record("task_431_example")], PAIRS)
    assert metric["total_pairs"] == 20
    assert metric["passed_pairs"] == 0


@pytest.mark.parametrize(
    "pairs",
    [
        {"pair": (LEFT, LEFT)},
        {"pair": (LEFT,)},
        {"pair": (LEFT, None)},
        {"p1": (LEFT, RIGHT), "p2": (LEFT, "other")},
    ],
)
def test_bad_registry_is_rejected(pairs):
    with pytest.raises(ValueError, match="registry"):
        compute_pair_metrics([], pairs)


def test_runner_payload_and_summary_integration(monkeypatch, capsys):
    import harness_bench.feasibility_tasks as package
    from harness_bench.runner import TaskRun, results_to_payload, summarize

    monkeypatch.setattr(package, "FEASIBILITY_PAIRS", PAIRS, raising=False)
    runs = [TaskRun(LEFT, True, "ok", 0.1), TaskRun(RIGHT, True, "ok", 0.1)]
    payload = results_to_payload(runs)
    assert payload["passed"] == 2
    assert payload["feasibility_pairs"]["passed_pairs"] == 1
    assert payload["feasibility_pairs"]["total_pairs"] == 20
    summarize(runs)
    assert "Attempt 1: 1/20 (5.0%); incomplete 19" in capsys.readouterr().out
    old_runs = [TaskRun("task_01_example", True, "ok", 0.1)]
    assert "feasibility_pairs" not in results_to_payload(old_runs)
    summarize(old_runs)
    assert "strict pairs" not in capsys.readouterr().out


def test_objects_and_all_pairs_pass():
    results = [
        SimpleNamespace(task_id=task_id, passed=True, attempt=1)
        for pair in PAIRS.values()
        for task_id in pair
    ]
    metric = compute_pair_metrics(results, PAIRS)
    assert metric["score"] == 1
    assert metric["passed_pairs"] == metric["total_pairs"] == 20
    assert metric["incomplete_pairs"] == 0


def test_native_registry_has_twenty_pairs_and_all_forty_tasks():
    from harness_bench.feasibility_tasks import FEASIBILITY_PAIRS, FEASIBILITY_TASKS

    assert len(FEASIBILITY_PAIRS) == 20
    assert {task_id for halves in FEASIBILITY_PAIRS.values() for task_id in halves} == {
        task.id for task in FEASIBILITY_TASKS
    }
    runs = [SimpleNamespace(task_id=task.id, passed=True, attempt=1) for task in FEASIBILITY_TASKS]
    metric = compute_pair_metrics(runs)
    assert metric["passed_pairs"] == metric["total_pairs"] == 20
    assert metric["score"] == 1


@pytest.mark.parametrize("passed", [True, False])
@pytest.mark.parametrize("suffix", ["", " | CLI exit=1", " | CLI exit=2 (failure)"])
def test_export_retains_verifier_telemetry_without_changing_score(passed, suffix):
    from harness_bench.feasibility_tasks import FEASIBILITY_PAIRS
    from harness_bench.runner import TaskRun, results_to_payload, task_run_from_payload

    task_id = next(iter(FEASIBILITY_PAIRS.values()))[0]
    metrics = {
        "decision_correct": True,
        "extra_requests": 0,
        "query_count": 95,
        "diagnostic_requests": 0,
        "search_requests": 95,
        "unique_reads": 95,
        "target_found": False,
        "probability_error": [0, 1],
    }
    message = json.dumps({"wave19": metrics}) + suffix
    run = TaskRun(task_id, passed, message, 0.1)
    payload = results_to_payload([run])
    task_payload = payload["tasks"][0]
    assert task_payload["task_metrics"] == metrics
    assert task_payload["message"] == message
    assert task_payload["passed"] is passed
    # Resume preserves the message, so another export reconstructs the same metrics.
    resumed = task_run_from_payload(task_payload)
    assert results_to_payload([resumed])["tasks"][0]["task_metrics"] == metrics


@pytest.mark.parametrize(
    "message",
    [
        "",
        "ordinary failure",
        '{"wave19":',
        "[]",
        '{"wave19": []}',
        '{"wave19": {}}',
        '{"other": {"decision_correct": true}}',
        '{"wave19": {"query_count": NaN}}',
        '{"wave19": {"query_count": 0, "query_count": 1}}',
        '{"wave19": {"query_count": 0}} trailing garbage',
    ],
)
def test_missing_or_malformed_telemetry_does_not_invent_metrics(message):
    from harness_bench.feasibility_tasks import FEASIBILITY_PAIRS
    from harness_bench.runner import TaskRun, results_to_payload

    task_id = next(iter(FEASIBILITY_PAIRS.values()))[0]
    task_payload = results_to_payload([TaskRun(task_id, False, message, 0.1)])["tasks"][0]
    assert "task_metrics" not in task_payload
    assert task_payload["message"] == message


@pytest.mark.parametrize("task_id", ["task_01_example", "task_432_wrong_slug"])
def test_telemetry_requires_exact_wave19_membership(task_id):
    from harness_bench.runner import TaskRun, results_to_payload

    message = '{"wave19": {"decision_correct": true}}'
    payload = results_to_payload([TaskRun(task_id, True, message, 0.1)])
    assert "task_metrics" not in payload["tasks"][0]
