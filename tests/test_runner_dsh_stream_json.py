"""Regression tests for run_dsh_bench.py (DeepSeek Harness / dsh).

Change: add-dsh-harness.

The runner boots ``dsh --profile headless --patch <patch.yml>
--permission-mode workspace-write --json '<task>'`` and reads usage from
``status.step_end.usage`` NDJSON events. These tests cover the pure helpers only
(command build, provider patch, NDJSON parser) — no network, no credential.

Run: python3 -m pytest tests/test_runner_dsh_stream_json.py -q
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RAW_PREFIXED = "custom-foundation-models-api-cloud-ru/deepseek-ai/DeepSeek-V4.1-Flash"
BARE = "deepseek-ai/DeepSeek-V4.1-Flash"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


import pytest  # noqa: E402


@pytest.fixture(scope="module")
def dsh():
    return _load("dsh_runner_under_test", "run_dsh_bench.py")


# Real capture: `dsh --profile headless --json '<task>'` on a trivial task.
_EVENTS = [
    {"type": "session", "sessionId": "session-0f413544-ab05-4560-8b7a-cad16b1f8db8", "cwd": "/tmp/ws"},
    {"type": "status", "phase": "turn_start", "turn": 1},
    {"type": "status", "phase": "step_start", "turn": 1, "step": 1},
    {"type": "text", "text": "I'll create the file now.\n\n"},
    {"type": "tool_call", "callId": "c1", "tool": "write", "input": {"file_path": "/tmp/ws/hello.txt"}},
    {"type": "tool_result", "callId": "c1", "status": "completed", "result": "<path>hello.txt</path>"},
    {"type": "status", "phase": "step_end", "turn": 1, "step": 1,
     "usage": {"inputTokens": 5284, "outputTokens": 74, "totalTokens": 5486, "cacheReadTokens": 128}},
    {"type": "status", "phase": "step_start", "turn": 1, "step": 2},
    {"type": "tool_call", "callId": "c2", "tool": "bash", "input": {"command": "ls"}},
    {"type": "status", "phase": "step_end", "turn": 1, "step": 2,
     "usage": {"inputTokens": 5399, "outputTokens": 94, "totalTokens": 5621, "cacheReadTokens": 128}},
    {"type": "status", "phase": "turn_end", "turn": 1, "reason": {"kind": "completed"}},
    {"type": "final", "sessionId": "session-0f413544-ab05-4560-8b7a-cad16b1f8db8",
     "reason": {"kind": "completed"}, "text": "Created hello.txt."},
]


def _stdout(*events) -> str:
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"


# ---------------------------------------------------------------------------
# Command build (no secret on the argv)
# ---------------------------------------------------------------------------

def test_command_shape(dsh):
    cmd = dsh.build_headless_command("do the task", patch_path="/tmp/p.yml")
    assert cmd[0] == dsh.DSH_BIN
    assert cmd[1:2] == ["--profile"]
    assert cmd[cmd.index("--profile") + 1] == "headless"
    assert cmd[cmd.index("--patch") + 1] == "/tmp/p.yml"
    assert cmd[cmd.index("--permission-mode") + 1] == "workspace-write"
    assert cmd[cmd.index("--json") + 1] == "do the task"


def test_command_default_permission_mode(dsh):
    cmd = dsh.build_headless_command("x", patch_path="/tmp/p.yml")
    assert cmd[cmd.index("--permission-mode") + 1] == "workspace-write"


def test_command_patch_precedes_app_args(dsh):
    # --patch is a launcher flag and must appear BEFORE the app arguments (--json).
    cmd = dsh.build_headless_command("x", patch_path="/tmp/p.yml")
    assert cmd.index("--patch") < cmd.index("--json")


def test_command_carries_no_secret(dsh):
    cmd = dsh.build_headless_command("x", patch_path="/tmp/p.yml")
    assert "DSH_BENCH_API_KEY" not in cmd
    assert all("sk-" not in part for part in cmd)


# ---------------------------------------------------------------------------
# Provider patch
# ---------------------------------------------------------------------------

def test_patch_uses_bare_model_id(dsh):
    patch = dsh.build_provider_patch(RAW_PREFIXED)
    assert BARE in patch
    assert "custom-foundation-models-api-cloud-ru" not in patch


def test_patch_route_fields(dsh):
    patch = dsh.build_provider_patch(BARE)
    assert "apiKeyEnv: DSH_BENCH_API_KEY" in patch
    assert "api: openai-completions" in patch
    assert "baseURL: https://foundation-models.api.cloud.ru/v1/" in patch
    assert "provider: cloudru" in patch
    assert "model: " + BARE in patch


def test_patch_reasoning_defaults_off(dsh):
    assert "reasoning: off" in dsh.build_provider_patch(BARE)
    assert "reasoning: off" in dsh.build_provider_patch(BARE, "none")
    assert "reasoning: off" in dsh.build_provider_patch(BARE, "off")


def test_patch_reasoning_level_passthrough(dsh):
    for level in ("minimal", "low", "medium", "high", "max", "ultra"):
        assert f"reasoning: {level}" in dsh.build_provider_patch(BARE, level)


def test_patch_declares_reasoning_efforts(dsh):
    # Without reasoningEfforts pi-ai treats the model as non-reasoning and any
    # effort beyond `off` fails with UNSUPPORTED_REASONING_EFFORT.
    patch = dsh.build_provider_patch(BARE, "medium")
    assert "reasoningEfforts:" in patch
    for level in ("off", "minimal", "low", "medium", "high", "xhigh", "max"):
        assert f"              {level}:" in patch
    # `off` carries no wire value; other levels map to their own name.
    assert "              off:\n" in patch
    assert "              medium: medium" in patch
    assert "              max: max" in patch
    # `ultra` is not a valid dsh level and must not be declared.
    assert "ultra" not in patch


def test_patch_disables_developer_role(dsh):
    # Cloud.ru rejects the OpenAI `developer` role (only user/tool/system/assistant).
    patch = dsh.build_provider_patch(BARE, "medium")
    assert "compat:" in patch
    assert "supportsDeveloperRole: false" in patch


def test_bare_model_id_strips_provider_prefix(dsh):
    assert dsh._bare_model_id(RAW_PREFIXED) == BARE
    assert dsh._bare_model_id(BARE) == BARE
    assert dsh._bare_model_id("zai-org/GLM-5.2") == "zai-org/GLM-5.2"


# ---------------------------------------------------------------------------
# NDJSON parser
# ---------------------------------------------------------------------------

def test_parser_sums_usage_over_step_end(dsh):
    stats = dsh.parse_dsh_jsonl(_stdout(*_EVENTS))
    assert stats["agent_input_tokens"] == 5284 + 5399
    assert stats["agent_output_tokens"] == 74 + 94
    assert stats["agent_total_tokens"] == 5486 + 5621
    assert stats["agent_cache_read_tokens"] == 128 + 128
    assert stats["agent_cache_write_tokens"] == 0
    assert stats["agent_steps"] == 2
    assert stats["agent_tool_calls"] == 2
    assert stats["session_id"] == "session-0f413544-ab05-4560-8b7a-cad16b1f8db8"
    assert stats["final_text"] == "Created hello.txt."
    assert stats["reason"] == "completed"


def test_parser_total_consistent_with_buckets(dsh):
    stats = dsh.parse_dsh_jsonl(_stdout(*_EVENTS))
    assert stats["agent_total_tokens"] == (
        stats["agent_input_tokens"]
        + stats["agent_output_tokens"]
        + stats["agent_cache_read_tokens"]
        + stats["agent_cache_write_tokens"]
    )


def test_parser_reads_cache_write_when_present(dsh):
    ev = dict(_EVENTS[6])
    ev["usage"] = {"inputTokens": 10, "outputTokens": 5, "totalTokens": 120,
                   "cacheReadTokens": 100, "cacheWriteTokens": 5}
    stats = dsh.parse_dsh_jsonl(_stdout(ev))
    assert stats["agent_cache_write_tokens"] == 5


def test_parser_ignores_non_json_and_noise(dsh):
    noisy = "booting\ndsh: diagnostics\nnot json\n" + _stdout(*_EVENTS)
    stats = dsh.parse_dsh_jsonl(noisy)
    assert stats["agent_total_tokens"] == 5486 + 5621


def test_parser_missing_final_reports_none(dsh):
    stats = dsh.parse_dsh_jsonl(_stdout(*_EVENTS[:-1]))
    assert stats["reason"] is None
    assert stats["final_text"] is None
    # usage still captured from the step_end events
    assert stats["agent_total_tokens"] == 5486 + 5621


def test_parser_empty_stream(dsh):
    stats = dsh.parse_dsh_jsonl("")
    assert stats["agent_total_tokens"] == 0
    assert stats["agent_steps"] == 0
    assert stats["session_id"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
