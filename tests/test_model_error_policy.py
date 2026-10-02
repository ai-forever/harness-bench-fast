"""Agent failures are scores; only errors from the model endpoint invalidate runs."""

import importlib.util
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest
from gigachat.exceptions import ResponseError
from langchain_core.language_models import BaseChatModel

from harness_bench import runner, runner_cli, runner_openrouter, runner_pure
from harness_bench.__main__ import _exit_code
from harness_bench.agent_stats import AgentRunStatsCollector


def task(number=1):
    return SimpleNamespace(
        id=f"task_{number:02d}_fake", name="fake", prompt="solve fixture",
        setup=lambda cwd: None,
        verify=lambda cwd: SimpleNamespace(passed=True, message="ok"),
    )


def api_error(status):
    return openai.APIStatusError(
        f"HTTP {status}", body=None,
        response=httpx.Response(status, request=httpx.Request("POST", "https://model.invalid")),
    )


@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 409, 413, 422, 429, 500, 503, 529])
@pytest.mark.parametrize("sdk", ["openai", "httpx", "gigachat"])
def test_endpoint_status_policy(status, sdk):
    error = api_error(status)
    if sdk == "httpx":
        error = httpx.HTTPStatusError(str(error), request=error.request, response=error.response)
    elif sdk == "gigachat":
        error = ResponseError("https://model.invalid", status, b"failure", {})
    stats = AgentRunStatsCollector()
    stats.as_callback().on_llm_error(error)
    assert stats.endpoint_unavailable(error) == (status not in {400, 413, 422})


def test_actual_model_callback_and_exception_provenance():
    error = httpx.ReadTimeout("model unavailable")

    class FailingModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "offline"

        def _generate(self, *args, **kwargs):
            raise error

    stats = AgentRunStatsCollector()
    with pytest.raises(httpx.ReadTimeout):
        FailingModel().invoke("fixture", config={"callbacks": [stats.as_callback()]})
    assert stats.endpoint_unavailable(error)
    wrapped = RuntimeError("wrapped")
    wrapped.__cause__ = error
    assert stats.endpoint_unavailable(wrapped)
    # A recovered model outage must not taint a later, unrelated tool failure.
    assert not stats.endpoint_unavailable(httpx.ReadTimeout("tool timeout"))


CASES = [
    (ValueError("path traversal"), False, False),
    (RuntimeError("unknown agent error"), False, False),
    (SystemExit("tool exited"), False, False),
    (httpx.ReadTimeout("tool timeout"), False, False),
    (httpx.ConnectError("model disconnected"), True, True),
    (httpx.ReadTimeout("model timeout"), True, True),
    (api_error(400), True, False),
    (api_error(401), True, True),
    (api_error(529), True, True),
]


def failing_agent(error, from_model):
    class Agent:
        def invoke(self, payload, config):
            if from_model:
                for callback in config["callbacks"]:
                    callback.on_llm_error(error, run_id=None)
            raise error
    return Agent()


@pytest.mark.parametrize("module", [runner, runner_openrouter, runner_pure])
@pytest.mark.parametrize("error,from_model,endpoint", CASES)
def test_native_attempt_policy(monkeypatch, module, error, from_model, endpoint):
    calls = []

    def build(*args, **kwargs):
        calls.append(1)
        return failing_agent(error, from_model)

    monkeypatch.setattr(module, "build_agent", build)
    kwargs = {"transient_attempts": 2} if module is runner_openrouter else {}
    result = module.run_task(task(), **kwargs)
    assert not result.passed
    assert result.failure_kind == ("infrastructure" if endpoint else "model_error")
    assert bool(result.error) == endpoint
    assert "Traceback" in (result.error or result.message)
    expected_calls = 2 if module is runner_openrouter and endpoint and runner_openrouter._is_transient_model_error(error) else 1
    assert len(calls) == expected_calls
    payload = runner.results_to_payload([result])
    assert payload["measurement_valid"] == (not endpoint)
    assert payload["pass_rate"] == (None if endpoint else 0.0)
    assert runner._payload_reruns_on_continue(payload["tasks"][0]) == endpoint
    assert _exit_code([result], allow_task_failures=True, fail_on_runtime_error=True) == int(endpoint)


@pytest.mark.parametrize("error,from_model,endpoint", CASES)
def test_worker_policy(monkeypatch, tmp_path, capsys, error, from_model, endpoint):
    package = Path(runner.__file__).parent
    monkeypatch.syspath_prepend(str(package))
    spec = importlib.util.spec_from_file_location("offline_worker", package / "deepagents_worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "openrouter_agent", SimpleNamespace(
        build_agent=lambda *a, **kw: failing_agent(error, from_model),
        is_transient_model_error=runner_openrouter._is_transient_model_error,
    ))
    monkeypatch.setenv("HBF_WORKER_API_KEY", "offline")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["worker", "--model", "fixture", "solve"])
    assert module.main() == 1
    event = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert event["failure_kind"] == ("infrastructure" if endpoint else "model_error")
    assert event["retryable"] == (endpoint and runner_openrouter._is_transient_model_error(error))
    assert "Traceback" in event["message"] and str(error) in event["message"]


@pytest.mark.parametrize("module", [runner, runner_openrouter, runner_pure, runner_cli])
@pytest.mark.parametrize("endpoint", [False, True])
def test_scheduling_json_and_resume(monkeypatch, tmp_path, module, endpoint):
    tasks = [task(1), task(2)]
    monkeypatch.setattr(module, "_load_env_from_dotenv", lambda: None)
    for name in ("_ensure_credentials", "_ensure_openrouter_key"):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, lambda: None)
    monkeypatch.setattr(module, "get_task", lambda tid: next(t for t in tasks if t.id == tid))
    calls = []

    def run(t, **kwargs):
        calls.append(t.id)
        return runner.TaskRun(t.id, False, "traceback", 0.01,
            error="endpoint down" if endpoint else None,
            failure_kind="infrastructure" if endpoint else "model_error")

    monkeypatch.setattr(module, "run_task_cli" if module is runner_cli else "run_task", run)
    run_all = module.run_all_cli if module is runner_cli else module.run_all
    kwargs = {"json_output": tmp_path / "results.json"}
    if module in (runner_cli, runner_openrouter):
        # Model failures continue even with the flag; endpoint failures stop without it.
        kwargs.update(isolation="none", fail_on_runtime_error=not endpoint)
    results = run_all([t.id for t in tasks], **kwargs)
    assert len(results) == (1 if endpoint else 2)
    saved = json.loads(kwargs["json_output"].read_text())
    assert saved["measurement_valid"] == (not endpoint)
    assert saved["tasks"][0]["message"] == "traceback"
    calls.clear()
    run_all([t.id for t in tasks], **kwargs)
    assert calls == ([tasks[0].id] if endpoint else [])


@pytest.mark.parametrize("module", [runner, runner_openrouter, runner_pure, runner_cli])
def test_verifier_exception_is_scored_failure(monkeypatch, tmp_path, module):
    if module is runner_cli:
        import subprocess
        monkeypatch.setattr(module, "_run_cli_subprocess", lambda *a, **kw: subprocess.CompletedProcess(a, 0, "", ""))
        monkeypatch.setattr(module, "_subprocess_env_with_token", lambda: {})
        module._STOP_REQUESTED.clear()
    else:
        monkeypatch.setattr(module, "build_agent", lambda *a, **kw: SimpleNamespace(invoke=lambda *a, **kw: {}))
    fixture = task()

    def verify(cwd):
        raise FileNotFoundError("model deleted an expected file")

    fixture.verify = verify
    result = module.run_task_cli(fixture, cli_command="fixture", artifacts_root=tmp_path) if module is runner_cli else module.run_task(fixture)
    assert result.failure_kind == "model_error" and result.error is None
    assert "FileNotFoundError" in result.message


@pytest.mark.parametrize("module", [runner, runner_openrouter, runner_pure, runner_cli])
def test_parallel_endpoint_failure_stops_without_waiting(monkeypatch, tmp_path, module):
    tasks = [task(1), task(2)]
    monkeypatch.setattr(module, "_load_env_from_dotenv", lambda: None)
    for name in ("_ensure_credentials", "_ensure_openrouter_key"):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, lambda: None)
    monkeypatch.setattr(module, "get_task", lambda tid: next(t for t in tasks if t.id == tid))
    blocked = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def run(t, **kwargs):
        if t.id == tasks[0].id:
            assert blocked.wait(5)
            return runner.TaskRun(t.id, False, "endpoint down", 0.01,
                error="endpoint down", failure_kind="infrastructure")
        blocked.set()
        release.wait(5)
        finished.set()
        return runner.TaskRun(t.id, True, "done", 1)

    monkeypatch.setattr(module, "run_task_cli" if module is runner_cli else "run_task", run)
    run_all = module.run_all_cli if module is runner_cli else module.run_all
    kwargs = {"concurrency": 2, "json_output": tmp_path / "results.json"}
    if module is runner_openrouter:
        kwargs["isolation"] = "none"
    try:
        results = run_all([t.id for t in tasks], **kwargs)
        assert len(results) == 1 and results[0].failure_kind == "infrastructure"
        assert not finished.is_set()
        if module is runner_cli:
            assert module._STOP_REQUESTED.is_set()
    finally:
        release.set()
        assert finished.wait(5)
        if module is runner_cli:
            module._STOP_REQUESTED.clear()
