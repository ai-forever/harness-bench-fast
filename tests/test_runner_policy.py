from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from langgraph.errors import GraphRecursionError

from harness_bench import __main__ as bench_main
from harness_bench.core import Task, VerifyResult
from harness_bench.runner import TaskRun, run_task
from harness_bench.tasks import get_task
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


def test_main_keyboard_interrupt_returns_130(monkeypatch, capsys) -> None:
    def _interrupting_run_all_cli(**_kwargs: object) -> list[TaskRun]:
        raise KeyboardInterrupt

    monkeypatch.setattr(bench_main, "run_all_cli", _interrupting_run_all_cli)

    assert bench_main.main(["run-cli", "--task", "task_fake"]) == 130
    assert "Interrupted by user; shutdown complete." in capsys.readouterr().err


def test_openrouter_fail_on_runtime_error_returns_nonzero_with_allowed_task_failures(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_run_all_openrouter(**kwargs: object) -> list[TaskRun]:
        captured.update(kwargs)
        return [
            TaskRun(
                "task_fake",
                False,
                "",
                0.01,
                error="Traceback\nRuntimeError: endpoint down",
            )
        ]

    monkeypatch.setattr(bench_main, "run_all_openrouter", _fake_run_all_openrouter)
    monkeypatch.setattr(bench_main, "summarize", lambda _results: None)

    assert (
        bench_main.main(
            [
                "run-openrouter",
                "--task",
                "task_fake",
                "--allow-task-failures",
                "--fail-on-runtime-error",
            ]
        )
        == 1
    )
    assert captured["fail_on_runtime_error"] is True


def test_openrouter_run_all_stops_on_runtime_error(monkeypatch) -> None:
    from harness_bench import runner_openrouter

    fake_tasks = [
        SimpleNamespace(id="task_01_fake", name="Fake one"),
        SimpleNamespace(id="task_02_fake", name="Fake two"),
    ]
    calls: list[str] = []

    def _fake_run_task(task: object, **_kwargs: object) -> TaskRun:
        task_id = cast(SimpleNamespace, task).id
        calls.append(task_id)
        if task_id == "task_01_fake":
            return TaskRun(
                task_id,
                False,
                "",
                0.01,
                error="Traceback\nRuntimeError: endpoint down",
            )
        raise AssertionError("must not run the second task after runtime error")

    monkeypatch.setattr(runner_openrouter, "_load_env_from_dotenv", lambda: None)
    monkeypatch.setattr(runner_openrouter, "_ensure_openrouter_key", lambda: None)
    monkeypatch.setattr(
        runner_openrouter,
        "get_task",
        lambda task_id: next(task for task in fake_tasks if task.id == task_id),
    )
    monkeypatch.setattr(runner_openrouter, "run_task", _fake_run_task)

    results = runner_openrouter.run_all(
        task_ids=["task_01_fake", "task_02_fake"],
        concurrency=1,
        fail_on_runtime_error=True,
    )

    assert calls == ["task_01_fake"]
    assert [result.task_id for result in results] == ["task_01_fake"]


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


def test_cli_keyboard_interrupt_terminates_process(monkeypatch, tmp_path: Path) -> None:
    from harness_bench import runner_cli

    terminated_pids: list[int] = []

    class _InterruptingProcess:
        pid = 4242
        returncode = None

        def communicate(self, timeout: int | None = None) -> tuple[str, str]:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        runner_cli.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _InterruptingProcess(),
    )
    monkeypatch.setattr(
        runner_cli,
        "_terminate_process_tree",
        lambda proc: terminated_pids.append(proc.pid),
    )

    with pytest.raises(KeyboardInterrupt):
        runner_cli._run_cli_subprocess(
            ["fake-cli"],
            cwd=tmp_path,
            timeout=600,
            env=None,
        )

    assert terminated_pids == [4242]


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


def test_cli_injects_codex_json_for_step_metrics() -> None:
    from harness_bench import runner_cli

    argv = runner_cli._ensure_codex_json_events(
        ["codex", "exec", "-m", "gpt-5.5", "--dangerously-bypass-approvals-and-sandbox"]
    )

    assert argv == [
        "codex",
        "exec",
        "--json",
        "-m",
        "gpt-5.5",
        "--dangerously-bypass-approvals-and-sandbox",
    ]
    assert runner_cli._ensure_codex_json_events(["codex", "exec", "--json"]) == [
        "codex",
        "exec",
        "--json",
    ]
    assert runner_cli._ensure_codex_json_events(["python", "-c", "pass"]) == [
        "python",
        "-c",
        "pass",
    ]


def test_codex_json_event_stats_count_agent_steps() -> None:
    from harness_bench import runner_cli

    stdout = "\n".join(
        [
            '{"type":"thread.started","thread_id":"t"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"Working"}}',
            '{"type":"item.completed","item":{"type":"file_change","changes":[]}}',
            (
                '{"type":"item.completed","item":{"type":"command_execution",'
                '"command":"pytest","exit_code":0}}'
            ),
            (
                '{"type":"turn.completed","usage":{"input_tokens":10,'
                '"output_tokens":5,"total_tokens":15}}'
            ),
            "plain text footer",
        ]
    )

    assert runner_cli._codex_json_event_stats(stdout) == {
        "agent_steps": 2,
        "agent_tool_calls": 1,
        "agent_shell_commands": 1,
        "agent_events": 6,
        "agent_input_tokens": 10,
        "agent_output_tokens": 5,
        "agent_total_tokens": 15,
    }
    assert runner_cli._codex_json_event_stats("plain text only") is None


def test_deepagents_result_stats_count_steps_and_tokens() -> None:
    from harness_bench.runner import agent_stats_from_result

    result = {
        "messages": [
            {"role": "user", "content": "do it"},
            {
                "type": "ai",
                "content": "",
                "tool_calls": [{"name": "execute"}, {"name": "write_file"}],
                "usage_metadata": {
                    "input_tokens": 12,
                    "output_tokens": 4,
                    "total_tokens": 16,
                },
            },
            {"type": "tool", "content": "ok"},
            {
                "type": "ai",
                "content": "done",
                "response_metadata": {
                    "token_usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "total_tokens": 5,
                    }
                },
            },
        ]
    }

    assert agent_stats_from_result(result) == {
        "agent_input_tokens": 15,
        "agent_output_tokens": 6,
        "agent_total_tokens": 21,
        "agent_events": 4,
        "agent_steps": 3,
        "agent_tool_calls": 2,
        "agent_shell_commands": 1,
        "agent_llm_calls": 2,
    }


def test_results_json_includes_agent_step_metrics() -> None:
    from harness_bench.runner import results_to_payload

    payload = results_to_payload(
        [
            TaskRun(
                "task_fake",
                True,
                "ok",
                0.01,
                agent_steps=2,
                agent_tool_calls=1,
                agent_shell_commands=1,
                agent_events=6,
                agent_llm_calls=2,
                agent_input_tokens=10,
                agent_output_tokens=5,
                agent_total_tokens=15,
            )
        ]
    )

    task = payload["tasks"][0]
    assert task["agent_steps"] == 2
    assert task["agent_tool_calls"] == 1
    assert task["agent_shell_commands"] == 1
    assert task["agent_events"] == 6
    assert task["agent_llm_calls"] == 2
    assert task["agent_input_tokens"] == 10
    assert task["agent_output_tokens"] == 5
    assert task["agent_total_tokens"] == 15


def test_openrouter_password_auth_fallback(monkeypatch) -> None:
    from harness_bench import runner_openrouter

    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"access_token":"token-123","expires_in":3600}'

    calls = 0

    def _fake_urlopen(*_args: object, **_kwargs: object) -> _FakeResponse:
        nonlocal calls
        calls += 1
        return _FakeResponse()

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_AUTH_URL", "https://auth.example/token")
    monkeypatch.setenv("OPENROUTER_AUTH_USERNAME", "user")
    monkeypatch.setenv("OPENROUTER_AUTH_PASSWORD", "pass")
    monkeypatch.setattr(runner_openrouter.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(runner_openrouter, "_OPENROUTER_AUTH_TOKEN", None)

    assert runner_openrouter._openrouter_api_key() == "token-123"
    assert runner_openrouter._openrouter_api_key() == "token-123"
    assert calls == 1


def test_openrouter_internal_tagme_shortcut(monkeypatch, tmp_path: Path) -> None:
    from harness_bench import runner_openrouter

    token_script = tmp_path / "get_token.sh"
    token_script.write_text(
        'USER="user@example.com"\nPASS="secret"\n',
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_AUTH_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("OPENROUTER_AUTH_PASSWORD", raising=False)
    monkeypatch.setenv("OPENROUTER_USE_INTERNAL_TAGME", "1")
    monkeypatch.setenv("OPENROUTER_INTERNAL_TAGME_TOKEN_SCRIPT", str(token_script))

    runner_openrouter._apply_internal_tagme_defaults()

    assert os.environ["OPENROUTER_BASE_URL"] == "https://tagme.sberdevices.ru/x/ai/llm/v1"
    assert (
        os.environ["OPENROUTER_AUTH_URL"]
        == "https://tagme.sberdevices.ru/auth/realms/tagme-public/protocol/openid-connect/token"
    )
    assert os.environ["OPENROUTER_AUTH_USERNAME"] == "user@example.com"
    assert os.environ["OPENROUTER_AUTH_PASSWORD"] == "secret"


def test_openrouter_run_task_retries_transient_model_errors(monkeypatch) -> None:
    from harness_bench import runner_openrouter

    calls = 0

    class _EventuallyPassingAgent:
        def invoke(self, _payload: object, **_kwargs: object) -> dict[str, list[dict[str, str]]]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary transport reset")
            return {"messages": [{"role": "assistant", "content": "done"}]}

    class _PassingTask:
        id = "task_fake_openrouter_retry"
        prompt = "write output"

        def setup(self, workspace: Path) -> None:
            (workspace / "input.txt").write_text("fixture", encoding="utf-8")

        def verify(self, workspace: Path) -> VerifyResult:
            assert (workspace / "input.txt").read_text(encoding="utf-8") == "fixture"
            return VerifyResult(True, "ok")

    monkeypatch.setattr(
        runner_openrouter,
        "build_agent",
        lambda *_args, **_kwargs: _EventuallyPassingAgent(),
    )
    monkeypatch.setattr(runner_openrouter, "_is_transient_model_error", lambda _exc: True)

    result = runner_openrouter.run_task(cast(Task, _PassingTask()), transient_attempts=5)

    assert result.passed is True
    assert result.message == "ok"
    assert calls == 2


def test_run_all_writes_incremental_json(monkeypatch, tmp_path: Path) -> None:
    import json

    from harness_bench import runner

    out = tmp_path / "results.json"
    fake_tasks = [
        SimpleNamespace(id="task_01_fake", name="Fake one"),
        SimpleNamespace(id="task_02_fake", name="Fake two"),
    ]
    calls = 0

    def _fake_run_task(task: object, **_kwargs: object) -> TaskRun:
        nonlocal calls
        calls += 1
        if calls == 2:
            payload = json.loads(out.read_text(encoding="utf-8"))
            assert payload["total"] == 1
            assert payload["tasks"][0]["task_id"] == "task_01_fake"
        return TaskRun(
            task_id=task.id,
            passed=True,
            message="ok",
            elapsed_seconds=0.01,
        )

    monkeypatch.setattr(runner, "_load_env_from_dotenv", lambda: None)
    monkeypatch.setattr(runner, "_ensure_credentials", lambda: None)
    monkeypatch.setattr(runner, "get_task", lambda tid: next(t for t in fake_tasks if t.id == tid))
    monkeypatch.setattr(runner, "run_task", _fake_run_task)

    runner.run_all(
        task_ids=["task_01_fake", "task_02_fake"],
        concurrency=1,
        json_output=out,
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["total"] == 2
    assert [task["task_id"] for task in payload["tasks"]] == ["task_01_fake", "task_02_fake"]


def test_task_203_accepts_valid_markdown_alignment(tmp_path: Path) -> None:
    (tmp_path / "team_report.md").write_text(
        "| team | open | closed | closed_hours |\n"
        "| --- | ---: | ---: | ---: |\n"
        "| alpha | 1 | 2 | 8 |\n"
        "| beta | 2 | 1 | 2 |\n"
        "| gamma | 0 | 2 | 8 |\n"
        "\n"
        "TOTAL_OPEN=3\n",
        encoding="utf-8",
    )

    result = get_task("task_203_sqlite_team_markdown_report").verify(tmp_path)

    assert result.passed is True


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
