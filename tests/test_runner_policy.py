from __future__ import annotations

from pathlib import Path

from langgraph.errors import GraphRecursionError

from harness_bench import __main__ as bench_main
from harness_bench.core import VerifyResult
from harness_bench.runner import TaskRun, run_task


class _FakeAgent:
    def invoke(self, _payload: object) -> None:
        raise GraphRecursionError("Recursion limit of 3 reached without hitting a stop condition")


class _FakeTask:
    id = "task_fake_recursion"
    name = "Fake recursion task"
    prompt = "Do something that loops"

    def setup(self, workspace: Path) -> None:
        (workspace / "input.txt").write_text("fixture", encoding="utf-8")

    def verify(self, _workspace: Path) -> VerifyResult:
        raise AssertionError("verify should not run after agent recursion failure")


def test_graph_recursion_limit_is_task_failure_without_traceback(monkeypatch) -> None:
    monkeypatch.setattr("harness_bench.runner.build_agent", lambda *_args, **_kwargs: _FakeAgent())

    result = run_task(_FakeTask(), recursion_limit=3)

    assert result.passed is False
    assert result.message == "graph recursion limit reached after 3 steps"
    assert result.error is None


def test_strict_run_returns_nonzero_on_task_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        bench_main,
        "run_all",
        lambda **_kwargs: [
            TaskRun("task_fake", False, "expected verifier failure", 0.01),
        ],
    )
    monkeypatch.setattr(bench_main, "summarize", lambda _results: None)

    assert bench_main.main(["run", "--task", "task_fake"]) == 1


def test_allow_task_failures_returns_zero_when_harness_completed(monkeypatch) -> None:
    monkeypatch.setattr(
        bench_main,
        "run_all",
        lambda **_kwargs: [
            TaskRun("task_fake", False, "expected verifier failure", 0.01),
        ],
    )
    monkeypatch.setattr(bench_main, "summarize", lambda _results: None)

    assert bench_main.main(["run", "--task", "task_fake", "--allow-task-failures"]) == 0
