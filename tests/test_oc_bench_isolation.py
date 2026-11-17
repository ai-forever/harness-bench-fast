"""Regression for `isolate-bench-openclaw-instance`.

`openclaw agent exec` otherwise runs against the operator's ambient config
(production plugins/MCP/skills) — not reproducible, and it dropped the
`contextPruning`/`compaction` history-management knobs. The runner now pins
`bench/openclaw.bench.json`, keeps those knobs, and retries a retryable infra
crash (`incomplete_turn`) instead of recording a false model failure.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
BENCH_CONFIG = ROOT / "bench" / "openclaw.bench.json"


def _load():
    spec = importlib.util.spec_from_file_location(
        "oc_runner_isolation", ROOT / "run_openclaw_bench.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["oc_runner_isolation"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeTask:
    id = "task_fake_iso"
    prompt = "do the thing"

    def setup(self, workspace):
        (workspace / "input.txt").write_text("fixture", encoding="utf-8")

    def verify(self, workspace):
        return SimpleNamespace(passed=True, message="ok")


# ── bench config shape ─────────────────────────────────────────────────

def test_bench_config_is_minimal_and_env_keyed():
    cfg = json.loads(BENCH_CONFIG.read_text())
    for section in ("plugins", "mcp", "skills", "channels", "memory"):
        assert section not in cfg, f"bench config must not define `{section}`"
    prov = cfg["models"]["providers"]["custom-foundation-models-api-cloud-ru"]
    # Key comes from env; no literal secret in the repo.
    assert prov["apiKey"] == {
        "source": "env", "provider": "default", "id": "CLOUD_RU_BENCH_API_KEY"}
    # History management is retained (avoids unbounded ~2MB requests).
    assert "contextPruning" in cfg["agents"]["defaults"]
    assert "compaction" in cfg["agents"]["defaults"]


def test_resolve_bench_config_fails_when_missing(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setenv("OPENCLAW_BENCH_CONFIG", str(tmp_path / "nope.json"))
    with pytest.raises(FileNotFoundError):
        mod.resolve_bench_config()


def test_resolve_bench_config_default_path():
    assert _load().resolve_bench_config() == BENCH_CONFIG


# ── argv pinning ───────────────────────────────────────────────────────

def test_agent_exec_pins_bench_config(monkeypatch):
    mod = _load()
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=json.dumps({"ok": True, "usage": {"input": 1, "output": 1, "total": 2}}),
            stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod.run_one_task(_FakeTask(), model="m/x", timeout=5)
    cmd = seen["cmd"]
    assert "--config" in cmd, "agent exec must be pinned to the bench config"
    cfg_path = cmd[cmd.index("--config") + 1]
    assert Path(cfg_path).as_posix().endswith("bench/openclaw.bench.json")


# ── error classification + retry ───────────────────────────────────────

def test_envelope_error_kind_detects_incomplete_turn():
    mod = _load()
    # From the envelope's error.kind.
    assert mod._envelope_error_kind(
        json.dumps({"error": {"kind": "incomplete_turn"}}), "", 1) == "incomplete_turn"
    # From the message text.
    assert mod._envelope_error_kind(json.dumps(
        {"error": {"message": "Provider returned an incomplete or malformed tool call"}}),
        "", 1) == "incomplete_turn"
    # From stderr tail.
    assert mod._envelope_error_kind(
        "", "Provider returned an incomplete or malformed tool call", 1) == "incomplete_turn"
    # Success and unrelated failures are not retryable.
    assert mod._envelope_error_kind("", "", 0) is None
    assert mod._envelope_error_kind(
        json.dumps({"error": {"kind": "some_other"}}), "", 1) == "some_other"
    assert "some_other" not in mod.RETRYABLE_ERROR_KINDS
    assert "incomplete_turn" in mod.RETRYABLE_ERROR_KINDS


def test_run_one_task_retries_on_incomplete_turn(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)  # no retry backoff in tests
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            env = json.dumps({"ok": False, "status": "error",
                              "error": {"kind": "incomplete_turn",
                                        "message": "Provider returned an incomplete or malformed tool call"}})
            return subprocess.CompletedProcess(cmd, 1, stdout=env, stderr="boom")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=json.dumps({"ok": True, "usage": {"input": 10, "output": 2, "total": 12}}),
            stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    res = mod.run_one_task(_FakeTask(), model="m/x", timeout=5, retries=1)
    assert calls["n"] == 2, "retryable crash must re-run the task once"
    assert res["passed"] is True
    assert res["error"] is None


def test_run_one_task_no_retry_without_retries(monkeypatch):
    mod = _load()
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        env = json.dumps({"ok": False, "error": {"kind": "incomplete_turn"}})
        return subprocess.CompletedProcess(cmd, 1, stdout=env, stderr="boom")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    res = mod.run_one_task(_FakeTask(), model="m/x", timeout=5, retries=0)
    assert calls["n"] == 1
    assert res["error"] is not None
