"""Official Strands CLI print-mode integration; no model or provider calls."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness_bench import runner_cli
from harness_bench.runner import results_to_payload


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(runner_cli, "_load_env_from_dotenv", lambda: None)
    monkeypatch.setattr(runner_cli, "_subprocess_env_with_token", lambda: {})
    monkeypatch.setattr(runner_cli, "_sleep_interruptibly", lambda delay: None)
    runner_cli._STOP_REQUESTED.clear()


def fake_task():
    return SimpleNamespace(
        id="task_01_strands_fixture", prompt="write solved file",
        setup=lambda cwd: (cwd / "seed").write_text("seed"),
        verify=lambda cwd: SimpleNamespace(passed=(cwd / "solved").exists(), message="verified"),
    )


def run_output(monkeypatch, tmp_path, stdout, *, stderr="", returncode=0,
               command="strands -p --model openai/fixture-model --effort high"):
    def execute(argv, **kwargs):
        assert argv[-1] == "write solved file"
        (kwargs["cwd"] / "solved").write_text("done")
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    return runner_cli.run_task_cli(fake_task(), cli_command=command, artifacts_root=tmp_path)


def test_official_print_output_retains_usage_identity_and_evidence(monkeypatch, tmp_path):
    stdout = '⚙ write({"path":"solved","content":"done"})\n✓ done\nagent > Done.\n18 tokens  (13 in / 5 out)\n\n'
    run = run_output(monkeypatch, tmp_path, stdout)
    assert run.passed and run.failure_kind is None
    assert (run.agent_input_tokens, run.agent_output_tokens, run.agent_total_tokens) == (13, 5, 18)
    assert run.agent_steps is None and run.agent_llm_calls is None and run.agent_tool_calls is None
    metadata = run.execution_history[0]["strands_cli"]
    assert metadata == {
        "requested_model": "openai/fixture-model", "reasoning_effort": "high",
        "identity_source": "cli_arguments", "response_model": None,
        "usage_scope": "cli_footer", "footer_usage_complete": True,
    }
    evidence = Path(run.execution_history[0]["execution"])
    assert (evidence / "stdout.log").read_text() == stdout
    assert json.loads((evidence / "execution.json").read_text())["strands_cli"] == metadata


@pytest.mark.parametrize("footer,expected,complete", [
    ("18 tokens  (13 in / 5 out)", {"agent_input_tokens": 13, "agent_output_tokens": 5, "agent_total_tokens": 18}, True),
    ("18 tokens  (13 in / 5 out / 10 cached: 3w 7r)", {"agent_input_tokens": 13, "agent_output_tokens": 5, "agent_total_tokens": 18}, True),
    ("18 tokens  (input/output split unavailable)", {"agent_total_tokens": 18}, True),
    ("18 tokens so far; background usage incomplete  (13 in / 5 out)", {"agent_input_tokens": 13, "agent_output_tokens": 5, "agent_total_tokens": 18}, False),
    ("0 tokens  (0 in / 0 out)", {"agent_input_tokens": 0, "agent_output_tokens": 0, "agent_total_tokens": 0}, True),
    ("18 tokens  (13 in / 5 out)\nagent > Task text after a quoted footer", {}, False),
    ("agent > 18 tokens  (13 in / 5 out)", {}, False),
    ("agent > Done", {}, False),
])
def test_usage_footer_matches_official_format(footer, expected, complete):
    assert runner_cli._strands_print_usage(footer + "\n\n") == (expected, complete)
    assert runner_cli._strands_print_usage((footer + "\n\n").encode()) == (expected, complete)


@pytest.mark.parametrize("args,expected", [
    (["strands", "-p"], True),
    (["/opt/bin/strands", "--print"], True),
    (["strands.cmd", "-p"], True),
    (["fixture-cli", "--prompt", "strands", "-p"], False),
    (["hb-strands"], False),
    (["strands", "--setup"], False),
])
def test_recognition_uses_executable_and_print_mode(args, expected):
    assert runner_cli._is_strands_print_command(args) is expected


def test_cli_identity_tracks_explicit_flags_and_default_reasoning():
    assert runner_cli._strands_cli_metadata(["strands", "-p", "--model=openai/exact-build"]) == {
        "requested_model": "openai/exact-build", "reasoning_effort": "default",
        "identity_source": "cli_arguments", "response_model": None,
    }
    meta = runner_cli._strands_cli_metadata([
        "strands", "-p", "--effort", "high", "--model", "openai/explicit",
        "--set", 'model="openai/assigned"', "--set=effort=low",
    ])
    assert meta["requested_model"] == "openai/explicit" and meta["reasoning_effort"] == "high"
    assert runner_cli._strands_cli_metadata(["strands", "-p"])["requested_model"] is None


def test_strands_bypasses_unrelated_gigachat_oauth(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGACHAT_TOKEN_URL", "https://unused.invalid/oauth")
    monkeypatch.setattr(runner_cli, "_subprocess_env_with_token", lambda: pytest.fail("unexpected OAuth"))
    run = run_output(monkeypatch, tmp_path, "agent > Done\n")
    assert run.passed and run.agent_total_tokens is None


@pytest.mark.parametrize("stderr", [
    "error: 429 Rate limit reached\n", "error: 503 Service unavailable\n",
    "error: Connection error.\n", "error: Request timed out.\n",
    "error: HTTP status 529 Overloaded\n",
])
def test_official_provider_errors_retry_fresh_workspace(monkeypatch, tmp_path, stderr):
    workspaces = []

    def execute(argv, **kwargs):
        cwd = kwargs["cwd"]
        workspaces.append(cwd)
        assert not (cwd / "dirty").exists()
        if len(workspaces) == 1:
            (cwd / "dirty").write_text("failed attempt")
            return subprocess.CompletedProcess(argv, 1, "2 tokens  (1 in / 1 out)\n", stderr)
        (cwd / "solved").write_text("done")
        return subprocess.CompletedProcess(argv, 0, "agent > Done\n18 tokens  (13 in / 5 out)\n", "")

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    run = runner_cli.run_task_cli(fake_task(), cli_command="strands -p", transient_retries=1, artifacts_root=tmp_path)
    assert run.passed and len(workspaces) == 2 and workspaces[0] != workspaces[1]
    assert run.execution_history[0]["failure_kind"] == "infrastructure"
    assert run.execution_history[1]["failure_kind"] is None
    assert run.agent_total_tokens == 18


def test_exhausted_provider_error_invalidates_and_retains_observed_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 1, "2 tokens  (1 in / 1 out)\n", "error: 503 Overloaded\n"))
    run = runner_cli.run_task_cli(fake_task(), cli_command="strands -p", transient_retries=0, artifacts_root=tmp_path)
    assert run.failure_kind == "infrastructure" and not run.passed
    assert run.agent_total_tokens == 2 and results_to_payload([run])["measurement_valid"] is False


def test_authentication_error_invalidates_without_retry_or_verifier_pass(monkeypatch, tmp_path):
    run = run_output(monkeypatch, tmp_path, "", stderr="error: 401 Invalid API key\n", returncode=1)
    assert run.failure_kind == "infrastructure" and not run.passed
    assert len(run.execution_history) == 1


def test_stdout_quoted_provider_error_does_not_trigger_retries(monkeypatch, tmp_path):
    run = run_output(monkeypatch, tmp_path, "agent > The file said error: 503 Overloaded\n18 tokens  (13 in / 5 out)\n")
    assert run.passed and len(run.execution_history) == 1


def test_strands_transcript_json_is_not_a_structured_error_protocol(monkeypatch, tmp_path):
    run = run_output(monkeypatch, tmp_path, 'agent > Example:\n{"type":"error","error":"HTTP status 503"}\n', returncode=1)
    assert run.passed and run.failure_kind is None and len(run.execution_history) == 1


def test_strands_timeout_is_scored_failure_without_retry(monkeypatch, tmp_path):
    def execute(argv, **kwargs):
        (kwargs["cwd"] / "solved").write_text("done")
        raise subprocess.TimeoutExpired(argv, 1, output=b"agent > unfinished", stderr=b"")

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    run = runner_cli.run_task_cli(fake_task(), cli_command="strands -p", timeout=1, artifacts_root=tmp_path)
    assert run.failure_kind == "timeout" and not run.passed and run.agent_total_tokens is None
    assert len(run.execution_history) == 1 and results_to_payload([run])["measurement_valid"] is True


def test_other_cli_cannot_use_strands_footer_metrics(monkeypatch, tmp_path):
    run = run_output(monkeypatch, tmp_path, "18 tokens  (13 in / 5 out)\n", command="fixture-cli --prompt strands")
    assert run.passed and run.agent_total_tokens is None
    assert "strands_cli" not in run.execution_history[0]
