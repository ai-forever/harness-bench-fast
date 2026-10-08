"""Smoke helper gates and evidence handling; provider calls are never made here."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("strands_smoke", Path(__file__).parents[1] / "scripts/strands_smoke.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


@pytest.fixture
def env(monkeypatch, tmp_path):
    runner = tmp_path / "runner.mjs"
    runner.write_text("// fixture")
    monkeypatch.setenv("STRANDS_RUNNER", str(runner))
    monkeypatch.setenv("STRANDS_NODE", "")
    monkeypatch.setattr(smoke.shutil, "which", lambda command: "/fixture/node" if command == "node" else None)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENAI_API_KEY", 'secret"123')
    monkeypatch.setenv("STRANDS_MODEL_ID", "test-model")
    for key in ("STRANDS_MODEL_PARAMS_JSON", "STRANDS_MAX_STEPS", "STRANDS_TIMEOUT_SECONDS", "STRANDS_REQUEST_TIMEOUT_SECONDS"):
        monkeypatch.delenv(key, raising=False)


def test_offline_configuration_overrides_provider_and_budgets(env, monkeypatch):
    monkeypatch.setenv("STRANDS_MAX_STEPS", "999")
    monkeypatch.setenv("STRANDS_MODEL_PARAMS_JSON", '{"reasoning_effort":"high"}')
    config = smoke.configuration(False)
    assert "OPENAI_BASE_URL" not in config
    assert config["OPENAI_API_KEY"] == "fixture-key"
    assert config["STRANDS_MODEL_ID"] == "fixture-strands"
    assert config["STRANDS_MAX_STEPS"] == "4"
    assert config["STRANDS_NODE"] == "/fixture/node"


def test_unset_runner_uses_repository_bundle(env, monkeypatch, tmp_path):
    monkeypatch.delenv("STRANDS_RUNNER")
    monkeypatch.setattr(smoke, "ROOT", tmp_path)
    bundled_runner = tmp_path / "harness_bench" / "strands" / "runner.mjs"
    bundled_runner.parent.mkdir(parents=True)
    bundled_runner.write_text("// bundled fixture")
    assert smoke.configuration(False)["STRANDS_RUNNER"] == str(bundled_runner)


def test_explicit_runner_override_is_preserved(env, monkeypatch, tmp_path):
    override = tmp_path / "custom-runtime" / "runner.mjs"
    override.parent.mkdir()
    override.write_text("// custom fixture")
    monkeypatch.setenv("STRANDS_RUNNER", str(override))
    assert smoke.configuration(False)["STRANDS_RUNNER"] == str(override)


def test_explicit_empty_runner_does_not_fall_back(env, monkeypatch):
    monkeypatch.setenv("STRANDS_RUNNER", "")
    with pytest.raises(ValueError, match="STRANDS_RUNNER"):
        smoke.configuration(False)


def test_live_preserves_exact_caller_params(env, monkeypatch):
    params = '{ "reasoning_effort": "high", "chat_template_kwargs": {"reasoning": true} }'
    monkeypatch.setenv("STRANDS_MODEL_PARAMS_JSON", params)
    config = smoke.configuration(True)
    assert config["STRANDS_MODEL_PARAMS_JSON"] == params
    assert config["STRANDS_MAX_STEPS"] == "16"
    assert config["STRANDS_TIMEOUT_SECONDS"] == "240"
    assert config["STRANDS_REQUEST_TIMEOUT_SECONDS"] == "90"


@pytest.mark.parametrize("key,value", [
    ("STRANDS_MAX_STEPS", "0"), ("STRANDS_MAX_STEPS", "1.5"),
    ("STRANDS_TIMEOUT_SECONDS", "nan"), ("STRANDS_TIMEOUT_SECONDS", "inf"),
    ("STRANDS_REQUEST_TIMEOUT_SECONDS", "-1"), ("STRANDS_MODEL_PARAMS_JSON", "[]"),
])
def test_invalid_configuration_fails_before_provider_probe(env, monkeypatch, tmp_path, key, value):
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(smoke, "probe_models", lambda config: pytest.fail("provider must not be called"))
    assert smoke.main(["--live", "--output-dir", str(tmp_path / "evidence")]) == 1


@pytest.mark.parametrize("key", ['secret"123', "123", "\\"])
def test_key_redaction_preserves_json_types(tmp_path, key):
    path = tmp_path / "private.json"
    smoke.write_json(path, {"count": 123, "nested": [key, {"value": f"before {key} after"}]}, key)
    data = json.loads(path.read_text())
    assert data["count"] == 123
    assert data["nested"][0] == "[REDACTED]"
    assert data["nested"][1]["value"] == "before [REDACTED] after"


def test_probe_failure_stops_before_task_execution(env, monkeypatch, tmp_path):
    def failed_probe(config):
        raise ValueError("model unavailable")

    monkeypatch.setattr(smoke, "probe_models", failed_probe)
    monkeypatch.setattr(smoke.runner_cli, "run_task_cli", lambda *a, **kw: pytest.fail("must not run a task"))
    output = tmp_path / "evidence"
    assert smoke.main(["--live", "--output-dir", str(output)]) == 1
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "error" and manifest["hashes"]
    assert "OPENAI_API_KEY" not in manifest["configuration"]


def test_live_sequential_tasks_respect_session_budget_and_result_failure(env, monkeypatch, tmp_path):
    monkeypatch.setenv("STRANDS_TIMEOUT_SECONDS", "600")
    monkeypatch.setattr(smoke, "probe_models", lambda config: {"data": [{"id": "test-model"}]})
    calls = []

    def execute(task, **kwargs):
        calls.append((task.id, kwargs["timeout"]))
        return SimpleNamespace(passed=task.id != smoke.TASKS[-1])

    monkeypatch.setattr(smoke.runner_cli, "run_task_cli", execute)
    monkeypatch.setattr(smoke, "results_to_payload", lambda results: {
        "measurement_valid": True, "passed": sum(result.passed for result in results)})
    output = tmp_path / "evidence"
    assert smoke.main(["--live", "--output-dir", str(output)]) == 1
    assert calls == [(task, 630) for task in smoke.TASKS]
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["reasoning_effort"] == "default"
    assert manifest["status"] == "failed" and manifest["timeout_seconds"] == 630


def test_refuses_to_overwrite_previous_evidence(tmp_path):
    with pytest.raises(FileExistsError):
        smoke.main(["--output-dir", str(tmp_path)])
