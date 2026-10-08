"""Native Strands protocol and retry regressions, without model calls."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness_bench import runner_cli
from harness_bench.runner import results_to_payload, write_results_json


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(runner_cli, "_load_env_from_dotenv", lambda: None)
    monkeypatch.setattr(runner_cli, "_subprocess_env_with_token", lambda: {})
    monkeypatch.setattr(runner_cli, "_sleep_interruptibly", lambda delay: None)
    runner_cli._STOP_REQUESTED.clear()


def terminal(**updates):
    event = {
        "type": "strands_result", "schema_version": 1,
        "strands_status": "completed", "failure_kind": None, "retryable": False,
        "error": None, "usage_complete": True,
        "stats": {"agent_steps": 2, "agent_llm_calls": 2, "agent_tool_calls": 1,
                  "agent_input_tokens": 13, "agent_output_tokens": 5, "agent_total_tokens": 18},
        "requested_model": "fixture-model", "model": "fixture-model",
        "response_models": ["fixture-build"], "reasoning_effort": "default",
        "reasoning_enabled": None, "model_params": {"temperature": 0},
    }
    event.update(updates)
    return event


def fake_task():
    return SimpleNamespace(
        id="task_01_strands_fixture", prompt="solve fixture",
        setup=lambda cwd: (cwd / "seed").write_text("seed"),
        verify=lambda cwd: SimpleNamespace(passed=(cwd / "solved").exists(), message="verified"),
    )


def run_event(monkeypatch, tmp_path, event, *, returncode=0, stderr="", command="hb-strands"):
    stdout = event if isinstance(event, str) else json.dumps(event)

    def execute(argv, **kwargs):
        (kwargs["cwd"] / "solved").write_text("done")
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    return runner_cli.run_task_cli(fake_task(), cli_command=command, artifacts_root=tmp_path)


def test_strands_complete_retains_metrics_identity_and_private_evidence(monkeypatch, tmp_path):
    run = run_event(monkeypatch, tmp_path, terminal(), command="bash /opt/runtime/hb-strands")
    assert run.passed and run.failure_kind is None
    assert (run.agent_steps, run.agent_llm_calls, run.agent_tool_calls) == (2, 2, 1)
    assert (run.agent_input_tokens, run.agent_output_tokens, run.agent_total_tokens) == (13, 5, 18)
    meta = run.execution_history[0]["strands"]
    assert meta["usage_complete"] is True
    assert meta["response_models"] == ["fixture-build"]
    assert meta["reasoning_effort"] == "default"
    assert meta["reasoning_enabled"] is None and meta["model_params"] == {"temperature": 0}
    assert json.loads(Path(run.execution_history[0]["execution"], "stdout.log").read_text())["type"] == "strands_result"


@pytest.mark.parametrize("extra_env", [None, {"OPENAI_API_KEY": "explicit-fixture-key"}])
def test_strands_does_not_refresh_unrelated_gigachat_credentials(monkeypatch, tmp_path, extra_env):
    monkeypatch.setenv("GIGACHAT_TOKEN_URL", "https://unused.invalid/oauth")
    monkeypatch.setenv("OPENAI_API_KEY", "inherited-fixture-key")

    def unexpected_refresh():
        pytest.fail("Strands must not refresh GigaChat OAuth credentials")

    def execute(argv, **kwargs):
        assert kwargs["env"]["OPENAI_API_KEY"] == (
            extra_env["OPENAI_API_KEY"] if extra_env else "inherited-fixture-key"
        )
        (kwargs["cwd"] / "solved").write_text("done")
        return subprocess.CompletedProcess(argv, 0, json.dumps(terminal()), "")

    monkeypatch.setattr(runner_cli, "_subprocess_env_with_token", unexpected_refresh)
    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    run = runner_cli.run_task_cli(
        fake_task(), cli_command="hb-strands", extra_env=extra_env, artifacts_root=tmp_path,
    )
    assert run.passed and run.failure_kind is None


def test_strands_429_retries_fresh_workspace_preserving_each_attempt(monkeypatch, tmp_path):
    workspaces = []

    def execute(argv, **kwargs):
        cwd = kwargs["cwd"]
        workspaces.append(cwd)
        assert not (cwd / "dirty").exists()
        if len(workspaces) == 1:
            (cwd / "dirty").write_text("failed attempt")
            event = terminal(strands_status="failed", failure_kind="infrastructure", retryable=True,
                             error="model_http_429", usage_complete=False)
            return subprocess.CompletedProcess(argv, 1, json.dumps(event), "")
        (cwd / "solved").write_text("done")
        return subprocess.CompletedProcess(argv, 0, json.dumps(terminal()), "")

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    run = runner_cli.run_task_cli(fake_task(), cli_command="hb-strands", transient_retries=1, artifacts_root=tmp_path)
    assert run.passed and len(workspaces) == 2 and workspaces[0] != workspaces[1]
    assert run.execution_history[0]["failure_kind"] == "infrastructure"
    assert run.execution_history[0]["strands"]["stats"]["agent_input_tokens"] == 13
    assert run.execution_history[1]["failure_kind"] is None
    assert run.agent_total_tokens == 18  # Final scored attempt; prior spend remains in history.


def test_strands_timeout_is_scored_failure_without_retry_or_verifier_pass(monkeypatch, tmp_path):
    run = run_event(monkeypatch, tmp_path, terminal(
        strands_status="failed", failure_kind="timeout", error="session_timeout",
        usage_complete=False), returncode=5)
    assert not run.passed and run.failure_kind == "timeout"
    assert len(run.execution_history) == 1 and run.agent_steps == 2
    assert results_to_payload([run])["measurement_valid"] is True


def test_strands_exhausted_infrastructure_retains_usage(monkeypatch, tmp_path):
    event = terminal(strands_status="failed", failure_kind="infrastructure", retryable=True,
                     error="model_http_503", usage_complete=False)
    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, json.dumps(event), ""))
    run = runner_cli.run_task_cli(fake_task(), cli_command="hb-strands", transient_retries=0, artifacts_root=tmp_path)
    assert not run.passed and run.failure_kind == "infrastructure"
    assert run.agent_input_tokens == 13 and results_to_payload([run])["measurement_valid"] is False


def test_strands_default_transport_retries_are_distinct_from_model_calls(monkeypatch, tmp_path):
    workspaces, delays = [], []
    event = terminal(strands_status="failed", failure_kind="infrastructure", retryable=True,
                     error="model_transport_failed", usage_complete=False)

    def execute(argv, **kwargs):
        workspaces.append(kwargs["cwd"])
        return subprocess.CompletedProcess(argv, 1, json.dumps(event), "")

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    monkeypatch.setattr(runner_cli, "_sleep_interruptibly", delays.append)
    run = runner_cli.run_task_cli(fake_task(), cli_command="hb-strands", artifacts_root=tmp_path)
    assert run.failure_kind == "infrastructure" and not run.passed
    assert len(set(workspaces)) == len(run.execution_history) == 6
    assert [entry["retry"] for entry in run.execution_history] == list(range(6))
    assert delays == [30, 60, 120, 240, 300]
    assert run.agent_llm_calls == 2
    assert results_to_payload([run])["measurement_valid"] is False


@pytest.mark.parametrize("details", [
    {"phase": "body", "cause_codes": ["UND_ERR_SOCKET", "ECONNRESET"]},
    {"phase": "completion", "finish_reason": None, "content_type": "string",
     "content_length": 0, "reasoning_length": 317, "tool_call_count": 0},
])
def test_strands_failure_details_survive_result_and_execution_evidence(monkeypatch, tmp_path, details):
    run = run_event(monkeypatch, tmp_path, terminal(
        strands_status="failed", failure_kind="infrastructure", error="invalid_model_finish",
        error_details=details), returncode=1)
    entry = run.execution_history[0]
    assert entry["strands"]["error_details"] == details
    payload = results_to_payload([run])
    assert payload["tasks"][0]["execution_history"][0]["strands"]["error_details"] == details
    saved = json.loads(Path(entry["execution"], "execution.json").read_text())
    assert saved["strands"]["error_details"] == details
    assert not run.passed and payload["measurement_valid"] is False


def test_strands_diagnostic_metadata_never_copies_raw_errors_or_provider_text():
    meta = runner_cli._strands_execution_metadata(terminal(error_details={
        "message": "secret request body", "stack": "secret stack", "phase": "body",
        "cause_codes": ["secret", {}, "ECONNRESET"] * 6,
        "finish_reason": "secret", "content_type": [], "content_length": True,
        "reasoning_length": -1, "tool_call_count": 0,
    }))
    assert meta["error_details"] == {
        "phase": "body", "cause_codes": ["ECONNRESET"] * 5, "tool_call_count": 0,
    }
    assert "secret" not in json.dumps(meta)


def test_strands_resume_replaces_infrastructure_and_retains_evidence(monkeypatch, tmp_path):
    prior = run_event(monkeypatch, tmp_path / "old", terminal(
        strands_status="failed", failure_kind="infrastructure", retryable=False,
        error="invalid_model_finish", error_details={"phase": "completion", "finish_reason": None}), returncode=1)
    path = tmp_path / "results.json"
    write_results_json([prior], path)
    task = fake_task()
    task.name = "fixture"
    monkeypatch.setattr(runner_cli, "get_task", lambda task_id: task)

    def execute(argv, **kwargs):
        (kwargs["cwd"] / "solved").write_text("done")
        return subprocess.CompletedProcess(argv, 0, json.dumps(terminal()), "")

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    runs = runner_cli.run_all_cli(
        [task.id], cli_command="hb-strands", json_output=path, artifacts_root=tmp_path / "new",
    )
    assert len(runs) == 1 and runs[0].passed
    history = runs[0].execution_history
    assert history[0] == prior.execution_history[0]
    assert history[0]["strands"]["error_details"] == {"phase": "completion", "finish_reason": None}
    assert history[1]["resume"] is True
    assert history[2]["strands"]["error"] is None
    assert results_to_payload(runs)["measurement_valid"] is True
    assert runs[0].agent_total_tokens == 18


def test_strands_resume_keeps_scored_timeout(monkeypatch, tmp_path):
    prior = run_event(monkeypatch, tmp_path / "old", terminal(
        strands_status="failed", failure_kind="timeout", error="session_timeout"), returncode=124)
    path = tmp_path / "results.json"
    write_results_json([prior], path)
    task = fake_task()
    monkeypatch.setattr(runner_cli, "get_task", lambda task_id: task)
    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", lambda *args, **kwargs: pytest.fail(
        "Resume must not grant a new attempt to a scored timeout"
    ))
    runs = runner_cli.run_all_cli([task.id], cli_command="hb-strands", json_output=path)
    assert len(runs) == 1 and runs[0].failure_kind == "timeout"
    assert runs[0].execution_history == prior.execution_history


@pytest.mark.parametrize("returncode,error", [(3, "model_call_limit"), (4, "model_token_limit")])
def test_strands_limits_are_graded_and_quoted_error_text_does_not_retry(monkeypatch, tmp_path, returncode, error):
    run = run_event(monkeypatch, tmp_path, terminal(
        strands_status="limited", error=error, output="Task says HTTP status 429 and ECONNRESET"),
        returncode=returncode, stderr="tool output quotes HTTP status 503")
    assert run.passed and run.failure_kind is None and len(run.execution_history) == 1


def test_strands_unknown_usage_is_absent_not_zero(monkeypatch, tmp_path):
    run = run_event(monkeypatch, tmp_path, terminal(
        usage_complete=False, stats={"agent_steps": 2, "agent_llm_calls": 2, "agent_tool_calls": 1}))
    assert run.passed and run.agent_input_tokens is None and run.agent_total_tokens is None
    assert run.execution_history[0]["strands"]["usage_complete"] is False


@pytest.mark.parametrize("stdout", [
    "", "{broken", json.dumps(terminal()) + "\n" + json.dumps(terminal()),
    json.dumps({"type": "tool_result", "content": terminal()}),
    json.dumps(terminal(strands_status=[])), json.dumps(terminal(failure_kind={})),
    json.dumps(terminal(schema_version=2)), json.dumps(terminal(schema_version=True)),
    json.dumps(terminal(stats={"agent_steps": -1})),
    json.dumps(terminal(stats={"agent_steps": True})),
    json.dumps(terminal(usage_complete="yes")),
    json.dumps(terminal(retryable=True)),
    json.dumps(terminal(strands_status="failed")),
    json.dumps(terminal(strands_status="limited")),
    json.dumps(terminal(strands_status="limited", error="runtime_failure")),
    json.dumps(terminal(error="request contained a secret")),
    json.dumps(terminal(model=None)), json.dumps(terminal(requested_model="")),
    json.dumps(terminal(reasoning_effort=None)),
    json.dumps(terminal(response_models="fixture-build")),
])
def test_strands_malformed_protocol_invalidates_even_if_files_pass(monkeypatch, tmp_path, stdout):
    run = run_event(monkeypatch, tmp_path, stdout)
    assert not run.passed and run.failure_kind == "infrastructure"
    assert run.error == "strands_invalid_terminal_event" and len(run.execution_history) == 1


def test_strands_launcher_failure_accepts_empty_stats(monkeypatch, tmp_path):
    run = run_event(monkeypatch, tmp_path, terminal(
        strands_status="failed", failure_kind="infrastructure", error="missing_OPENAI_API_KEY",
        stats={}, usage_complete=False), returncode=2)
    assert run.failure_kind == "infrastructure" and run.agent_steps is None


def test_strands_local_agent_failure_is_scored_without_verifying_success(monkeypatch, tmp_path):
    run = run_event(monkeypatch, tmp_path, terminal(
        strands_status="failed", error="agent_failed"), returncode=1)
    assert not run.passed and run.failure_kind == "agent_error" and len(run.execution_history) == 1


def test_strands_missing_tool_action_is_scored_with_diagnostics_without_retry(monkeypatch, tmp_path):
    details = {"phase": "completion", "finish_reason": "tool_calls",
               "content_type": "string", "content_length": 820,
               "reasoning_length": 0, "tool_call_count": 0}
    run = run_event(monkeypatch, tmp_path, terminal(
        strands_status="failed", error="invalid_tool_call", error_details=details), returncode=1)
    assert not run.passed and run.failure_kind == "agent_error"
    assert len(run.execution_history) == 1
    assert run.execution_history[0]["strands"]["error_details"] == details
    assert run.agent_total_tokens == 18
    assert results_to_payload([run])["measurement_valid"] is True


def test_strands_failed_exit_contradiction_invalidates(monkeypatch, tmp_path):
    run = run_event(monkeypatch, tmp_path, terminal(), returncode=1)
    assert not run.passed and run.failure_kind == "infrastructure"


@pytest.mark.parametrize("returncode", [0, 4])
def test_strands_limit_exit_contradiction_invalidates(monkeypatch, tmp_path, returncode):
    run = run_event(monkeypatch, tmp_path, terminal(
        strands_status="limited", error="model_call_limit"), returncode=returncode)
    assert not run.passed and run.failure_kind == "infrastructure"


def test_strands_outer_timeout_preserves_complete_terminal_usage(monkeypatch, tmp_path):
    def execute(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1, output=json.dumps(terminal()).encode(), stderr=b"child hung")

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    run = runner_cli.run_task_cli(fake_task(), cli_command="hb-strands", timeout=1, artifacts_root=tmp_path)
    assert run.failure_kind == "timeout" and not run.passed and run.agent_total_tokens == 18
    assert len(run.execution_history) == 1


def test_strands_event_in_other_cli_output_is_not_native_protocol(monkeypatch, tmp_path):
    run = run_event(monkeypatch, tmp_path, terminal(
        strands_status="failed", failure_kind="infrastructure", error="model_http_429", retryable=True),
        returncode=1, command="fixture-cli --prompt hb-strands")
    assert run.passed and run.failure_kind is None and len(run.execution_history) == 1
    assert "strands" not in run.execution_history[0]
