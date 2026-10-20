"""Verification must not execute generated Python in the runner process."""

import shutil
import subprocess
import sys

import pytest

from harness_bench.tasks import get_task
from harness_bench.verifier_process import verify_bwrap

LINUX_BWRAP = sys.platform == "linux" and shutil.which("bwrap") is not None


@pytest.mark.skipif(not LINUX_BWRAP, reason="requires Linux bubblewrap")
def test_gold_verifies_in_bwrap(tmp_path):
    task = get_task("task_358_range_parser")
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    assert verify_bwrap(task.id, tmp_path).passed


@pytest.mark.skipif(not LINUX_BWRAP, reason="requires Linux bubblewrap")
def test_unbounded_allocation_is_task_failure(tmp_path):
    task = get_task("task_358_range_parser")
    task.setup(tmp_path)
    (tmp_path / "ranges.py").write_text(
        "def parse_ranges(spec):\n"
        "    values = []\n"
        "    while True:\n"
        "        values.append('x' * 1000 + str(len(values)))\n"
    )
    outcome = verify_bwrap(task.id, tmp_path, timeout=20, memory_bytes=256 * 1024**2)
    assert not outcome.passed
    assert "MemoryError" in outcome.message
    # Parent remains usable and can complete the subsequent task.
    task.apply_gold(tmp_path)
    assert verify_bwrap(task.id, tmp_path).passed


@pytest.mark.skipif(not LINUX_BWRAP, reason="requires Linux bubblewrap")
def test_infinite_loop_has_wall_timeout(tmp_path):
    task = get_task("task_358_range_parser")
    task.setup(tmp_path)
    (tmp_path / "ranges.py").write_text("def parse_ranges(spec):\n    while True: pass\n")
    outcome = verify_bwrap(task.id, tmp_path, timeout=3)
    assert not outcome.passed
    assert "timed out" in outcome.message


def test_runner_dispatches_bwrap_verification_without_parent_execution(tmp_path, monkeypatch):
    import harness_bench.runner_cli as runner
    import harness_bench.verifier_process as worker
    from harness_bench.core import Task, VerifyResult

    def forbidden(_):
        raise AssertionError("verifier executed in parent")

    task = Task("unit_bwrap", "test", "test", forbidden)
    calls = []
    monkeypatch.setattr(runner, "_resolved_isolation", lambda mode: mode, raising=False)
    monkeypatch.setattr(
        runner, "_require_supported_task_isolation", lambda *args: None, raising=False
    )
    monkeypatch.setattr(runner, "sandbox_argv", lambda argv, **kwargs: argv)
    monkeypatch.setattr(runner, "_subprocess_env_with_token", lambda: {})
    monkeypatch.setattr(
        runner,
        "_run_cli_subprocess",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    monkeypatch.setattr(
        worker,
        "verify_bwrap",
        lambda task_id, workspace: calls.append(task_id) or VerifyResult(True, "isolated"),
    )
    run = runner.run_task_cli(
        task,
        cli_command="true",
        isolation="bwrap",
        artifacts_root=tmp_path / "evidence",
        transient_retries=0,
    )
    assert run.passed
    assert calls == [task.id]


def test_long_verifier_budget_uses_registry_not_id_prefix(tmp_path, monkeypatch):
    import harness_bench.verifier_process as worker

    calls = []
    monkeypatch.setattr(worker, "_verify_bwrap", lambda task_id, workspace, **kw: calls.append(kw))
    worker.verify_bwrap("task_401_review_comments", tmp_path)
    worker.verify_bwrap("task_358_range_parser", tmp_path)
    worker.verify_bwrap("task_401_review_comments", tmp_path, timeout=3)
    assert [call["timeout"] for call in calls] == [900, 120, 3]
