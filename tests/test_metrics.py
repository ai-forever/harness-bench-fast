from __future__ import annotations

from types import SimpleNamespace

import pytest

from harness_bench.__main__ import _resolve_metric_ks, build_parser
from harness_bench.metrics import compute_pass_metrics, pass_at_k, pass_hat_k
from harness_bench.runner import TaskRun, summarize


def test_pass_at_k_and_pass_hat_k_formulas() -> None:
    assert pass_at_k(num_samples=10, num_correct=3, k=1) == pytest.approx(0.3)
    assert pass_at_k(num_samples=10, num_correct=3, k=2) == pytest.approx(24 / 45)
    assert pass_hat_k(num_samples=10, num_correct=3, k=2) == pytest.approx(3 / 45)


def test_compute_pass_metrics_groups_attempts_by_task() -> None:
    results = [
        SimpleNamespace(task_id="task_1", passed=True),
        SimpleNamespace(task_id="task_1", passed=False),
        SimpleNamespace(task_id="task_1", passed=False),
        SimpleNamespace(task_id="task_2", passed=True),
        SimpleNamespace(task_id="task_2", passed=True),
        SimpleNamespace(task_id="task_2", passed=False),
    ]

    metrics = compute_pass_metrics(results, pass_at_ks=(2,), pass_hat_ks=(2,))

    assert [m.label for m in metrics] == ["pass@2", "pass^2"]
    assert [m.value for m in metrics] == pytest.approx([5 / 6, 1 / 6])


def test_cli_metric_defaults_for_repeated_attempts() -> None:
    parser = build_parser()
    args = parser.parse_args(["run-cli", "--attempts", "5"])

    assert _resolve_metric_ks(args) == ((1, 5), (5,))


def test_cli_accepts_symbolic_metric_aliases() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["run-cli", "--attempts", "5", "--pass@", "2", "--pass^", "3"]
    )

    assert _resolve_metric_ks(args) == ((2,), (3,))


def test_summary_prints_pass_metrics_for_attempts(capsys: pytest.CaptureFixture[str]) -> None:
    results = [
        TaskRun("task_1", True, "ok", 0.1, attempt=1, attempts=2),
        TaskRun("task_1", False, "bad", 0.1, attempt=2, attempts=2),
        TaskRun("task_2", True, "ok", 0.1, attempt=1, attempts=2),
        TaskRun("task_2", True, "ok", 0.1, attempt=2, attempts=2),
    ]

    summarize(results, pass_at_ks=(1, 2), pass_hat_ks=(2,))

    out = capsys.readouterr().out
    assert "Passed attempts: 3/4" in out
    assert "pass@1: 75.0%" in out
    assert "pass@2: 100.0%" in out
    assert "pass^2: 50.0%" in out
    assert "task_1 #2/2: bad" in out
