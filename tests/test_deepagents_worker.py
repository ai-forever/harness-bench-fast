"""Offline contract of the native deepagents process boundary."""

import importlib.util
import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

from harness_bench import runner_openrouter


def test_isolated_route_preserves_model_configuration_and_exposes_only_runtime(monkeypatch):
    from harness_bench import runner_cli

    captured = {}
    monkeypatch.setattr(runner_openrouter, "_load_env_from_dotenv", lambda: None)
    monkeypatch.setattr(runner_openrouter, "_ensure_openrouter_key", lambda: None)
    monkeypatch.setattr(runner_openrouter, "_openrouter_api_key", lambda: "offline")

    def launch(*args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(runner_cli, "run_all_cli", launch)
    runner_openrouter.run_all(
        ["task_01_fake"],
        isolation="bwrap",
        model_name="original",
        recursion_limit=80,
        max_tokens=16384,
        harness_profile="gigachat",
        forward_reasoning_history=True,
        compact_at_tokens=100000,
        prompt_cache=True,
        no_subagents=True,
        responses_api=True,
        transient_attempts=5,
    )
    argv = shlex.split(captured["cli_command"])
    assert argv[argv.index("--model") + 1] == "original"
    assert argv[argv.index("--max-tokens") + 1] == "16384"
    assert argv[argv.index("--harness-profile") + 1] == "gigachat"
    assert all(
        flag in argv
        for flag in ("--forward-reasoning-history", "--prompt-cache", "--no-subagents", "--responses-api")
    )
    assert captured["transient_retries"] == 4
    paths = captured["runtime_paths"]
    package = Path(runner_openrouter.__file__).parent
    assert str(package) not in paths and str(package.parent) not in paths
    assert not any(Path(p).name.startswith(("tasks", "runner", "verifiers")) for p in paths)
    assert captured["extra_env_factory"]() == {"HBF_WORKER_API_KEY": "offline"}


def test_worker_invokes_same_builder_without_task_registry(monkeypatch, tmp_path, capsys):
    package = Path(runner_openrouter.__file__).parent
    monkeypatch.syspath_prepend(str(package))
    spec = importlib.util.spec_from_file_location(
        "offline_deepagents_worker", package / "deepagents_worker.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = {}

    class FakeAgent:
        def invoke(self, payload, config):
            captured["payload"] = payload
            assert config["callbacks"]
            return {"messages": []}

    def build(workspace, **kwargs):
        captured.update(kwargs)
        return FakeAgent()

    monkeypatch.setitem(sys.modules, "openrouter_agent", SimpleNamespace(build_agent=build))
    monkeypatch.setenv("HBF_WORKER_API_KEY", "offline")
    monkeypatch.setenv("HBF_TASK_MIN_RECURSION_LIMIT", "90")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "worker",
            "--model",
            "test",
            "--max-tokens",
            "16384",
            "--forward-reasoning-history",
            "fixture",
        ],
    )
    assert module.main() == 0
    assert captured["forward_reasoning_history"] is True and captured["recursion_limit"] == 90
    assert captured["payload"] == {"messages": [{"role": "user", "content": "fixture"}]}
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["type"] == "hbf_deepagents_result"
    assert events[-1]["stats"]["agent_compactions"] == 0


def test_explicit_reasoning_effort_reaches_native_model(monkeypatch, tmp_path):
    from harness_bench import chat_openai, openrouter_agent
    captured = {}
    class ModelBuilt(Exception):
        pass
    def model(**kwargs):
        captured.update(kwargs)
        raise ModelBuilt
    monkeypatch.setattr(chat_openai, "ReasoningAwareChatOpenAI", model)
    monkeypatch.setenv("OPENROUTER_REASONING_EFFORT", "medium")
    import pytest
    with pytest.raises(ModelBuilt):
        openrouter_agent.build_agent(tmp_path, api_key="offline", forward_reasoning_history=True)
    assert captured["reasoning_effort"] == "medium"
    assert captured["forward_reasoning_history"] is True
    monkeypatch.setenv("OPENROUTER_REASONING_EFFORT", "misspelled")
    with pytest.raises(ValueError, match="Unsupported OPENROUTER_REASONING_EFFORT"):
        openrouter_agent.build_agent(tmp_path, api_key="offline")


def test_responses_api_moves_effort_into_encrypted_reasoning(monkeypatch, tmp_path):
    import deepagents

    from harness_bench import openrouter_agent

    captured = {}

    class Agent:
        def with_config(self, config):
            return self

    def create(model, **kwargs):
        captured["model"] = model
        return Agent()

    monkeypatch.setattr(deepagents, "create_deep_agent", create)
    monkeypatch.setenv("OPENROUTER_REASONING_EFFORT", "high")
    openrouter_agent.build_agent(tmp_path, api_key="offline", model_name="openai/gpt-6-luna", responses_api=True)
    model = captured["model"]
    assert model.use_responses_api is True
    assert model.reasoning == {"effort": "high"} and model.reasoning_effort is None
    assert model.store is False and model.include == ["reasoning.encrypted_content"]

    openrouter_agent.build_agent(tmp_path, api_key="offline", model_name="z-ai/glm-5.3")
    model = captured["model"]
    assert not model.use_responses_api and model.reasoning_effort == "high"
