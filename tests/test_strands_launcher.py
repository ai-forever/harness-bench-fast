"""Offline launcher contract checks; the stub never loads an SDK or calls a model."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "scripts" / "hb-strands"


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "runtime with spaces"
    root.mkdir()
    runner = root / "runner.mjs"
    runner.write_text("// fixture runner\n")
    node = root / "node"
    node.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "if sys.argv[1:] == ['-p', 'process.versions.node']:\n"
        "    if os.environ.get('FAKE_NODE_VERSION_EXIT'):\n"
        "        print('fixture-node-sensitive-diagnostic', file=sys.stderr)\n"
        "        sys.exit(int(os.environ['FAKE_NODE_VERSION_EXIT']))\n"
        "    print(os.environ.get('FAKE_NODE_VERSION', '22.13.0'))\n"
        "else:\n"
        "    print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "          'env': {k: v for k, v in os.environ.items()\n"
        "                  if k.startswith(('STRANDS_', 'OPENAI_'))}}))\n"
        "    sys.exit(int(os.environ.get('FAKE_RUNNER_EXIT', '0')))\n"
    )
    node.chmod(0o755)
    workspace = tmp_path / "task workspace"
    workspace.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("STRANDS_", "OPENAI_"))}
    env.update(STRANDS_RUNNER=str(runner), STRANDS_NODE=str(node))
    return workspace, runner, node, env


def invoke(runtime, args=("task prompt",), **updates):
    workspace, _, _, env = runtime
    return subprocess.run(
        [str(LAUNCHER), *args], cwd=workspace,
        env={**env, **updates}, capture_output=True, text=True, timeout=5,
    )


def test_literal_prompt_cwd_and_smoke_defaults(runtime):
    prompt = 'Write "hello"\n$(touch injected); `touch injected2` \\ \'quoted\' ☃\n\n'
    result = invoke(runtime, (prompt,), OPENAI_API_KEY="fixture-key")
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    workspace, runner, _, _ = runtime
    assert output["argv"] == [str(runner)]
    assert Path(output["cwd"]).resolve() == workspace.resolve()
    instruction = output["env"]["STRANDS_INSTRUCTION"]
    header, task_prompt = instruction.split("\n\n", 1)
    assert header == (
        f"Working directory: {workspace.resolve()}\n"
        "Resolve all relative task paths against this directory."
    )
    assert task_prompt == prompt
    assert output["env"]["STRANDS_MAX_STEPS"] == "32"
    assert output["env"]["STRANDS_TIMEOUT_SECONDS"] == "600"
    assert output["env"]["STRANDS_REQUEST_TIMEOUT_SECONDS"] == "120"
    assert json.loads(output["env"]["STRANDS_MODEL_PARAMS_JSON"]) == {"temperature": 0, "max_tokens": 4096}
    assert not list(workspace.iterdir())


def test_preserves_explicit_configuration_and_runner_exit(runtime):
    overrides = {
        "STRANDS_MAX_STEPS": "7", "STRANDS_TIMEOUT_SECONDS": "40",
        "STRANDS_REQUEST_TIMEOUT_SECONDS": "9", "STRANDS_MODEL_PARAMS_JSON": "{}",
        "STRANDS_MODEL_ID": "fixture-model", "OPENAI_API_KEY": "fixture-key",
        "OPENAI_BASE_URL": "http://127.0.0.1:9/v1",
    }
    result = invoke(runtime, FAKE_RUNNER_EXIT="4", **overrides)
    assert result.returncode == 4
    env = json.loads(result.stdout)["env"]
    assert all(env[key] == value for key, value in overrides.items())


@pytest.mark.parametrize("setting", [
    "STRANDS_MODEL_PARAMS_JSON", "STRANDS_MAX_STEPS", "STRANDS_TIMEOUT_SECONDS",
    "STRANDS_REQUEST_TIMEOUT_SECONDS",
])
def test_empty_explicit_configuration_is_not_silently_defaulted(runtime, setting):
    result = invoke(runtime, **{setting: ""})
    assert result.returncode == 0
    assert json.loads(result.stdout)["env"][setting] == ""


@pytest.mark.parametrize("args", [(), ("a", "b"), ("",)])
def test_rejects_invalid_prompt_arguments(runtime, args):
    result = invoke(runtime, args)
    assert result.returncode == 2
    assert json.loads(result.stdout)["type"] == "strands_result"
    assert result.stderr == ""


@pytest.mark.parametrize("overrides,code", [
    ({"STRANDS_RUNNER": "runner.mjs"}, "launcher_requires_absolute_STRANDS_RUNNER"),
    ({"STRANDS_RUNNER": "/does-not-exist/runner.mjs"}, "launcher_unreadable_STRANDS_RUNNER"),
    ({"STRANDS_NODE": "/does-not-exist/node"}, "launcher_node_not_found"),
    ({"STRANDS_NODE": "relative/node"}, "launcher_node_not_found"),
    ({"FAKE_NODE_VERSION": "20.19.0"}, "launcher_requires_node_22"),
    ({"FAKE_NODE_VERSION": "not-node"}, "launcher_invalid_node_version"),
    ({"FAKE_NODE_VERSION_EXIT": "9"}, "launcher_node_version_failed"),
])
def test_fails_closed_before_runner(runtime, overrides, code):
    result = invoke(runtime, OPENAI_API_KEY="do-not-print-secret", **overrides)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "type": "strands_result", "schema_version": 1, "strands_status": "failed",
        "failure_kind": "infrastructure", "retryable": False, "error": code,
        "stats": {}, "usage_complete": False,
    }
    assert "do-not-print-secret" not in result.stdout + result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize("node_setting", ["", "node"])
def test_node_lookup_on_path(runtime, node_setting):
    _, _, node, _ = runtime
    result = invoke(runtime, STRANDS_NODE=node_setting, PATH=str(node.parent) + os.pathsep + os.environ["PATH"])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["env"]["STRANDS_INSTRUCTION"].endswith(
        "\n\ntask prompt"
    )


def test_workspace_header_uses_physical_path(runtime):
    workspace, runner, node, env = runtime
    alias = workspace.parent / "workspace alias"
    alias.symlink_to(workspace, target_is_directory=True)
    result = invoke((alias, runner, node, env), PWD=str(alias))
    assert result.returncode == 0, result.stderr
    instruction = json.loads(result.stdout)["env"]["STRANDS_INSTRUCTION"]
    assert instruction.startswith(f"Working directory: {workspace.resolve()}\n")
