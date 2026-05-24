from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from langgraph.errors import GraphRecursionError

from harness_bench import __main__ as bench_main
from harness_bench.core import Task, VerifyResult
from harness_bench.runner import TaskRun, run_task
from harness_bench.verifiers import file_text_equals


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


def test_cli_timeout_kills_windows_process_tree(monkeypatch, tmp_path: Path) -> None:
    from harness_bench import runner_cli

    taskkill_calls: list[list[str]] = []

    class _TimeoutThenClosedProcess:
        pid = 4242
        returncode = None

        def communicate(self, timeout: int | None = None) -> tuple[str, str]:
            if timeout == 600:
                raise subprocess.TimeoutExpired(cmd=["cmd", "/c", "gigacode"], timeout=timeout)
            return "", ""

        def kill(self) -> None:
            raise AssertionError("taskkill should close the process tree before fallback kill")

    monkeypatch.setattr(runner_cli.os, "name", "nt")
    monkeypatch.setattr(
        runner_cli.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _TimeoutThenClosedProcess(),
    )

    def _fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        taskkill_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(runner_cli.subprocess, "run", _fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        runner_cli._run_cli_subprocess(
            ["cmd", "/c", "gigacode", "--approval-mode=auto-edit"],
            cwd=tmp_path,
            timeout=600,
            env=None,
        )

    assert taskkill_calls == [["taskkill", "/F", "/T", "/PID", "4242"]]


def test_cli_subprocess_reader_replaces_invalid_utf8(monkeypatch, tmp_path: Path) -> None:
    from harness_bench import runner_cli

    popen_kwargs: dict[str, object] = {}

    class _CompletedProcess:
        pid = 4242
        returncode = 0

        def communicate(self, timeout: int | None = None) -> tuple[str, str]:
            return "ok", ""

    def _fake_popen(*_args: object, **kwargs: object) -> _CompletedProcess:
        popen_kwargs.update(kwargs)
        return _CompletedProcess()

    monkeypatch.setattr(runner_cli.subprocess, "Popen", _fake_popen)

    result = runner_cli._run_cli_subprocess(
        ["fake-cli"],
        cwd=tmp_path,
        timeout=600,
        env=None,
    )

    assert result.stdout == "ok"
    assert popen_kwargs["encoding"] == "utf-8"
    assert popen_kwargs["errors"] == "replace"


def test_text_verifier_reports_non_utf8_files_without_traceback(tmp_path: Path) -> None:
    (tmp_path / "out.txt").write_bytes(b"\xff\xfe\x00")

    result = file_text_equals("out.txt", "expected")(tmp_path)

    assert result.passed is False
    assert "out.txt is not valid UTF-8" in result.message


def test_task_verify_reports_decode_errors_as_clean_failures(tmp_path: Path) -> None:
    (tmp_path / "out.txt").write_bytes(b"\xff\xfe\x00")

    task = Task(
        id="task_fake_decode",
        name="decode",
        prompt="decode",
        verifier=lambda ws: VerifyResult(
            True, (ws / "out.txt").read_text(encoding="utf-8")
        ),
    )

    result = task.verify(tmp_path)

    assert result.passed is False
    assert result.message.startswith("verifier failed to decode text as UTF-8:")


def test_cli_temp_workspace_cleanup_failure_does_not_abort_task(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    from harness_bench import runner_cli

    workspace = tmp_path / "hb_cli_task_fake_cleanup"

    class _CleanupFailingTemporaryDirectory:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            workspace.mkdir()
            self.name = str(workspace)

        def cleanup(self) -> None:
            raise OSError(145, "The directory is not empty", self.name)

    class _PassingCliTask:
        id = "task_fake_cleanup"
        prompt = "write done.txt"

        def setup(self, path: Path) -> None:
            (path / "input.txt").write_text("fixture", encoding="utf-8")

        def verify(self, path: Path) -> VerifyResult:
            assert path == workspace
            return VerifyResult(True, "ok")

    monkeypatch.setattr(runner_cli, "_load_env_from_dotenv", lambda: None)
    monkeypatch.setattr(runner_cli, "_subprocess_env_with_token", lambda: None)
    monkeypatch.setattr(runner_cli, "_CLEANUP_RETRY_DELAYS", ())
    monkeypatch.setattr(runner_cli, "TemporaryDirectory", _CleanupFailingTemporaryDirectory)
    monkeypatch.setattr(
        runner_cli,
        "_run_cli_subprocess",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(args=[], returncode=0),
    )

    result = runner_cli.run_task_cli(
        cast(Task, _PassingCliTask()),
        cli_command="python -c pass",
        timeout=1,
    )

    assert result.passed is True
    assert result.message == "ok"
    assert result.workspace is None
    assert "[WARN] cleanup failed for task_fake_cleanup workspace" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("module_name", "run_all_name", "run_task_name", "extra_kwargs"),
    [
        ("harness_bench.runner", "run_all", "run_task", {"recursion_limit": 3}),
        ("harness_bench.runner_pure", "run_all", "run_task", {"recursion_limit": 3}),
        (
            "harness_bench.runner_openrouter",
            "run_all",
            "run_task",
            {"model_name": "test-model", "recursion_limit": 3},
        ),
        (
            "harness_bench.runner_cli",
            "run_all_cli",
            "run_task_cli",
            {"cli_command": "python -c pass", "timeout": 1},
        ),
    ],
)
def test_sequential_progress_output_is_cp1251_safe(
    module_name: str,
    run_all_name: str,
    run_task_name: str,
    extra_kwargs: dict[str, object],
    monkeypatch,
    capsys,
) -> None:
    module = pytest.importorskip(module_name)
    fake_task = SimpleNamespace(id="task_fake", name="Fake task")

    monkeypatch.setattr(module, "_load_env_from_dotenv", lambda: None)
    if hasattr(module, "_ensure_credentials"):
        monkeypatch.setattr(module, "_ensure_credentials", lambda: None)
    if hasattr(module, "_ensure_openrouter_key"):
        monkeypatch.setattr(module, "_ensure_openrouter_key", lambda: None)
    monkeypatch.setattr(module, "get_task", lambda _task_id: fake_task)
    monkeypatch.setattr(
        module,
        run_task_name,
        lambda *_args, **_kwargs: TaskRun("task_fake", True, "ok", 0.01),
    )

    getattr(module, run_all_name)(task_ids=["task_fake"], concurrency=1, **extra_kwargs)

    output = capsys.readouterr().out
    assert "[START] task_fake: Fake task" in output
    output.encode("cp1251")
