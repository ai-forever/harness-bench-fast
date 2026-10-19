"""Task budgets are enforced by the host before inspecting agent artifacts."""

import importlib
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from types import SimpleNamespace

import pytest

from harness_bench import runner, runner_cli
from harness_bench.core import Task, VerifyResult, effective_task_timeout
from harness_bench.harbor_export import _task_toml


def budget_task(**kwargs):
    return Task("task_budget_test", "Budget", "Finish in the stated budget.",
                lambda path: VerifyResult((path / "done").exists()), **kwargs)


@pytest.mark.parametrize("configured,floor,ceiling,expected", [
    (600, None, None, 600), (60, 120, None, 120),
    (600, 120, 120, 120), (30, 120, 120, 120),
    (600, None, 120, 120), (30, None, 120, 30),
    (600, None, .02, .02),
])
def test_resolve_timeout(configured, floor, ceiling, expected):
    assert effective_task_timeout(configured, floor, ceiling) == expected


@pytest.mark.parametrize("configured,floor,ceiling", [
    (0, None, None), (float("inf"), None, None), (600, 0, None),
    (600, None, -1), (600, float("nan"), None),
    (600, None, float("inf")), (600, 121, 120),
])
def test_reject_invalid_budgets(configured, floor, ceiling):
    with pytest.raises(ValueError):
        effective_task_timeout(configured, floor, ceiling)


@pytest.mark.parametrize("module_name", ["runner", "runner_pure", "runner_openrouter"])
def test_native_runners_reject_late_artifact(monkeypatch, module_name):
    module = importlib.import_module(f"harness_bench.{module_name}")
    finished = threading.Event()
    release = threading.Event()
    workspace = None

    def build(path, **kwargs):
        nonlocal workspace
        workspace = path

        def invoke(*args, **kwargs):
            release.wait(2)
            (path / "done").write_text("correct but late")
            finished.set()

        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr(module, "build_agent", build)
    monkeypatch.setenv("HARNESS_BENCH_TASK_TIMEOUT", "600")
    task = budget_task(max_timeout_seconds=.02)
    task.verifier = lambda path: pytest.fail("must not verify after the deadline")
    try:
        kwargs = {"transient_attempts": 1} if module_name == "runner_openrouter" else {}
        result = module.run_task(task, keep_workspace=True, **kwargs)
        assert not result.passed
        assert result.failure_kind == "timeout"
        assert result.elapsed_seconds < 1
        # Native invocation uses a daemon thread: late writes must never turn
        # this already-scored timeout into a passing result.
        release.set()
        assert finished.wait(2)
        assert (workspace / "done").exists()
        assert not result.passed
    finally:
        release.set()
        finished.wait(2)
        if workspace is not None:
            shutil.rmtree(workspace)


@pytest.mark.parametrize("module_name", ["runner", "runner_pure", "runner_openrouter"])
def test_native_budget_excludes_setup_and_build(monkeypatch, module_name):
    module = importlib.import_module(f"harness_bench.{module_name}")
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def setup(path):
        clock[0] += 60
        (path / "clock.json").write_text('{"elapsed_seconds": 999999}')

    def build(path, **kwargs):
        clock[0] += 60

        def invoke(*args, **kwargs):
            clock[0] += .01
            (path / "done").write_text("ok")

        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr(module, "build_agent", build)
    result = module.run_task(budget_task(setup_callback=setup, max_timeout_seconds=.05))
    assert result.passed
    assert result.elapsed_seconds == pytest.approx(.01)


def test_finished_but_over_budget_native_call_is_rejected(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def invoke(*args, **kwargs):
        clock[0] += 121

    with pytest.raises(runner.TaskTimeoutError):
        runner.invoke_agent_with_stats(SimpleNamespace(invoke=invoke), {},
                                       runner.AgentRunStatsCollector(),
                                       min_timeout_seconds=120, max_timeout_seconds=120)


@pytest.fixture
def offline_cli(monkeypatch):
    monkeypatch.setattr(runner_cli, "_load_env_from_dotenv", lambda: None)
    monkeypatch.setattr(runner_cli, "_subprocess_env_with_token", lambda: {})
    runner_cli._STOP_REQUESTED.clear()


def test_cli_ceiling_kills_process_and_rejects_existing_good_artifact(offline_cli, tmp_path):
    import shlex

    code = "from pathlib import Path; import time; Path('done').write_text('ok'); time.sleep(10)"
    task = budget_task(max_timeout_seconds=.2)
    task.verifier = lambda path: pytest.fail("must not verify a timed-out process")
    run = runner_cli.run_task_cli(task, timeout=600,
                                 cli_command=shlex.join([sys.executable, "-c", code]),
                                 artifacts_root=tmp_path)
    assert not run.passed
    assert run.failure_kind == "timeout"
    assert len(run.execution_history) == 1
    assert run.execution_history[0]["timeout"] == .2


@pytest.mark.parametrize("late", [False, True])
def test_cli_monotonic_budget_excludes_setup(offline_cli, monkeypatch, tmp_path, late):
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def setup(path):
        clock[0] += 600
        (path / "clock.json").write_text('{"elapsed_seconds": 0}')

    def execute(argv, *, cwd, timeout, **kwargs):
        assert timeout == 120
        clock[0] += 121 if late else 1
        (cwd / "done").write_text("ok")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    run = runner_cli.run_task_cli(
        budget_task(setup_callback=setup, min_timeout_seconds=120, max_timeout_seconds=120),
        cli_command="fixture", timeout=600, artifacts_root=tmp_path,
    )
    assert run.passed is not late
    assert run.failure_kind == ("timeout" if late else None)
    assert run.elapsed_seconds == (121 if late else 1)


def test_isolated_deepagents_dispatch_applies_task_ceiling(offline_cli, monkeypatch, tmp_path):
    from harness_bench import runner_openrouter, verifier_process

    task = budget_task(min_timeout_seconds=120, max_timeout_seconds=120)
    monkeypatch.setattr(verifier_process, "verify_bwrap", lambda task_id, workspace: task.verify(workspace))
    monkeypatch.setenv("HARNESS_BENCH_TASK_TIMEOUT", "600")
    monkeypatch.setattr(runner_openrouter, "_load_env_from_dotenv", lambda: None)
    monkeypatch.setattr(runner_openrouter, "_ensure_openrouter_key", lambda: None)
    monkeypatch.setattr(runner_openrouter, "_openrouter_api_key", lambda: "test-fixture")
    monkeypatch.setattr(runner_cli, "get_task", lambda tid: task)
    monkeypatch.setattr(runner_cli, "sandbox_argv", lambda argv, **kw: argv)

    def execute(argv, *, cwd, timeout, **kwargs):
        assert any("deepagents_worker.py" in arg for arg in argv)
        assert timeout == 120
        (cwd / "done").write_text("ok")
        return subprocess.CompletedProcess(argv, 0, '{"type":"hbf_deepagents_result","stats":{}}', "")

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    results = runner_openrouter.run_all([task.id], isolation="bwrap", artifacts_root=tmp_path)
    assert len(results) == 1
    assert results[0].passed
    assert results[0].execution_history[0]["timeout"] == 120


@pytest.mark.parametrize("late", [False, True])
def test_rli_receives_task_ceiling(monkeypatch, late):
    from harness_bench import runner_openrouter

    calls = []
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    class Backend:
        error = None

        def __init__(self, task_id, *, timeout):
            calls.append((task_id, timeout))

        def start(self):
            calls.append("start")
            clock[0] += 600

        def verify(self):
            calls.append("verify")
            return VerifyResult(True)

        def close(self):
            calls.append("close")

    monkeypatch.setitem(sys.modules, "harness_bench.rli_backend", SimpleNamespace(RLIBackend=Backend))

    def invoke(*args, **kwargs):
        clock[0] += 121 if late else 1
        return {}

    monkeypatch.setattr(runner_openrouter, "build_agent",
                        lambda *a, **kw: SimpleNamespace(invoke=invoke))
    monkeypatch.setenv("HARNESS_BENCH_TASK_TIMEOUT", "600")
    task = budget_task(min_timeout_seconds=120, max_timeout_seconds=120)
    run = runner_openrouter.run_task(task, rli=True)
    assert run.passed is not late
    assert run.failure_kind == ("timeout" if late else None)
    assert calls == [(task.id, 120), "start", *([] if late else ["verify"]), "close"]


@pytest.mark.parametrize("floor,ceiling,expected", [
    (None, None, 600), (1800, None, 600), (120, 120, 120), (None, 90, 90),
])
def test_harbor_agent_ceiling_preserves_legacy_export(floor, ceiling, expected):
    task = budget_task(min_timeout_seconds=floor, max_timeout_seconds=ceiling)
    config = tomllib.loads(_task_toml(task, harbor_name="budget", dataset="hbf"))
    assert config["agent"]["timeout_sec"] == expected
    assert config["verifier"]["timeout_sec"] == 600
    assert config["environment"]["build_timeout_sec"] == 600
