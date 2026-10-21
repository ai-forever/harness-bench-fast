"""Solution-first defaults must survive every native/isolated runtime boundary."""

import importlib
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import TypedDict

import pytest

from harness_bench.__main__ import build_parser
from harness_bench.core import Task, VerifyResult
from harness_bench.harbor_export import _task_toml
from harness_bench.runtime_limits import (
    DEFAULT_TASK_TIMEOUT_SECONDS,
    effective_recursion_limit,
    langgraph_recursion_limit,
)


@pytest.mark.parametrize("configured,floor,expected", [
    (0, None, 0), (0, 400, 0), (80, None, 80), (80, 400, 400), (500, 400, 500),
])
def test_unlimited_step_cap_stays_unlimited(configured, floor, expected):
    assert effective_recursion_limit(configured, floor) == expected
    assert langgraph_recursion_limit(expected) == (expected or sys.maxsize)


@pytest.mark.parametrize("configured,floor", [(-1, None), (0, -1), (1.5, None)])
def test_invalid_step_caps_rejected(configured, floor):
    with pytest.raises(ValueError, match="nonnegative integer"):
        effective_recursion_limit(configured, floor)


def test_entrypoints_use_sanitary_timeout_and_unlimited_steps(monkeypatch):
    from harness_bench import runner, runner_cli

    monkeypatch.delenv("HARNESS_BENCH_TASK_TIMEOUT", raising=False)
    assert DEFAULT_TASK_TIMEOUT_SECONDS == runner._task_timeout_seconds() == 3600
    assert runner_cli.DEFAULT_TIMEOUT_SECONDS == 3600
    parser = build_parser()
    for command in ("run", "run-pure", "run-openrouter"):
        assert parser.parse_args([command]).recursion_limit == 0
        assert parser.parse_args([command, "--recursion-limit", "123"]).recursion_limit == 123
    assert parser.parse_args(["run-cli"]).timeout == 3600
    assert parser.parse_args(["run-cli", "--timeout", "900"]).timeout == 900
    monkeypatch.setenv("HARNESS_BENCH_TASK_TIMEOUT", "900")
    assert runner._task_timeout_seconds() == 900


@pytest.mark.parametrize("module_name", ["runner", "runner_pure", "openrouter_agent"])
def test_real_graph_can_finish_after_old_80_step_cap(monkeypatch, tmp_path, module_name):
    import deepagents
    import deepagents.backends
    import langchain_gigachat
    from langgraph.errors import GraphRecursionError
    from langgraph.graph import END, START, StateGraph

    from harness_bench import chat_openai, openrouter_agent, runner

    class State(TypedDict):
        count: int

    graph = StateGraph(State)
    graph.add_node("step", lambda state: {"count": state["count"] + 1})
    graph.add_edge(START, "step")
    graph.add_conditional_edges("step", lambda state: END if state["count"] == 125 else "step")
    monkeypatch.setattr(deepagents, "create_deep_agent", lambda **kwargs: graph.compile())

    model_options = []
    backend_options = []

    class Model:
        def __init__(self, **kwargs):
            model_options.append(kwargs)

    monkeypatch.setattr(langchain_gigachat, "GigaChat", Model)
    monkeypatch.setattr(chat_openai, "ReasoningAwareChatOpenAI", Model)
    monkeypatch.setattr(deepagents.backends, "LocalShellBackend",
                        lambda **kwargs: backend_options.append(kwargs))
    monkeypatch.setattr(runner, "_ensure_harness_profile", lambda workspace: None)
    monkeypatch.setattr(openrouter_agent, "_apply_execute_cwd_fix", lambda model: None)
    monkeypatch.delenv("HARNESS_BENCH_REQUEST_TIMEOUT", raising=False)
    monkeypatch.delenv("HBF_TASK_TIMEOUT_SECONDS", raising=False)
    module = importlib.import_module(f"harness_bench.{module_name}")
    kwargs = {"api_key": "offline"} if module_name == "openrouter_agent" else {}
    agent = module.build_agent(tmp_path, **kwargs)
    assert agent.invoke({"count": 0}) == {"count": 125}
    assert model_options[-1]["timeout"] == 3600
    assert backend_options[-1]["timeout"] == 3600
    with pytest.raises(GraphRecursionError):
        module.build_agent(tmp_path, recursion_limit=80, **kwargs).invoke({"count": 0})


@pytest.mark.parametrize("module_name", ["runner", "runner_pure", "runner_openrouter"])
@pytest.mark.parametrize("configured,expected", [(0, 0), (80, 400), (500, 500)])
def test_task_floor_only_applies_to_explicit_finite_steps(monkeypatch, module_name, configured, expected):
    module = importlib.import_module(f"harness_bench.{module_name}")
    seen = []

    def build(path, **kwargs):
        seen.append(kwargs["recursion_limit"])
        return SimpleNamespace(invoke=lambda *a, **kw: {})

    monkeypatch.setattr(module, "build_agent", build)
    task = Task("task_floor", "Floor", "Solve", lambda path: VerifyResult(True), min_recursion_limit=400)
    assert module.run_task(task, recursion_limit=configured).passed
    assert seen == [expected]


@pytest.mark.parametrize("floor,ceiling,expected", [
    (None, None, 3600), (1800, None, 3600), (7200, None, 7200), (120, 120, 120),
])
def test_harbor_sanitary_default_preserves_long_task_floor(floor, ceiling, expected):
    task = Task("task_timeout", "Timeout", "Solve", lambda path: VerifyResult(True),
                min_timeout_seconds=floor, max_timeout_seconds=ceiling)
    config = tomllib.loads(_task_toml(task, harbor_name="timeout", dataset="hbf"))
    assert config["agent"]["timeout_sec"] == expected
    assert config["verifier"]["timeout_sec"] == 600


def test_rli_does_not_reintroduce_shorter_default_command_deadline(monkeypatch):
    from harness_bench.rli_backend import RLIBackend

    captured = {}
    monkeypatch.setitem(sys.modules, "gigarli", SimpleNamespace(
        RLIClient=lambda **kwargs: captured.update(kwargs),
        RequestLimits=SimpleNamespace, StepAction=SimpleNamespace,
        StepActionArgs=SimpleNamespace, StepRequest=SimpleNamespace,
    ))
    backend = RLIBackend("task_timeout", timeout=7200)
    assert captured["timeout"] == 7260
    backend.session_id = "offline"

    async def step(request):
        captured["request"] = request
        return SimpleNamespace(timed_out=False, exit_code=0, stderr="", stdout='{"output":"ok","exit_code":0}')

    monkeypatch.setattr(backend, "_step", step)
    assert backend.execute("slow solver").exit_code == 0
    assert captured["request"].limits.deadline_ms == 7260000

    calls = []
    monkeypatch.setattr(backend, "_call", lambda operation, **kwargs:
                        calls.append(kwargs) or {"output": "ok", "exit_code": 0})
    backend.execute("slow solver")
    backend.execute("explicit short command", timeout=30)
    assert [call["timeout"] for call in calls] == [7200, 30]


@pytest.mark.parametrize("task_timeout,explicit_timeout,expected", [
    (None, None, 3600), ("7200.0", None, 7200), ("7200", "30", 30),
])
def test_mini_swe_wrapper_keeps_unlimited_steps_and_generous_commands(
    monkeypatch, task_timeout, explicit_timeout, expected,
):
    source = Path("scripts/hb-mini-swe-agent").read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    captured = {}

    def create_agent(model, env, config, **kwargs):
        captured["agent"] = config
        return SimpleNamespace(run=lambda task: {"exit_status": "complete"})

    def create_environment(config, **kwargs):
        captured["environment"] = config

    for name, module in {
        "minisweagent.agents": SimpleNamespace(get_agent=create_agent),
        "minisweagent.config": SimpleNamespace(builtin_config_dir=Path("unused"), get_config_from_spec=lambda path: {}),
        "minisweagent.environments": SimpleNamespace(get_environment=create_environment),
        "minisweagent.models": SimpleNamespace(get_model=lambda **kwargs: None),
        "minisweagent.utils.serialize": SimpleNamespace(recursive_merge=lambda base, override: override),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    for variable in ("MSWEA_STEP_LIMIT", "MSWEA_COST_LIMIT", "MSWEA_WALL_TIME_LIMIT_SECONDS"):
        monkeypatch.delenv(variable, raising=False)
    for variable, value in (("HBF_TASK_TIMEOUT_SECONDS", task_timeout),
                            ("MSWEA_COMMAND_TIMEOUT", explicit_timeout)):
        if value is None:
            monkeypatch.delenv(variable, raising=False)
        else:
            monkeypatch.setenv(variable, value)
    monkeypatch.setattr(sys, "argv", ["hb-mini-swe-agent", "Solve"])
    namespace = {"__name__": "offline_wrapper"}
    exec(compile(source, "hb-mini-swe-agent", "exec"), namespace)
    assert namespace["main"]() == 0
    assert captured["agent"]["step_limit"] == 0
    assert captured["agent"]["wall_time_limit_seconds"] == 0
    assert captured["environment"]["timeout"] == expected
