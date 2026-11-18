"""Regression for issue #449: a run that executed but reported no usage.

`openclaw agent exec --json` omits the `usage` field entirely when token
accounting is unavailable (cli/docs: "usage is omitted when unavailable"). The
runner used to default an omitted usage to 0 tokens, so OC-medium tasks
393/398/431 (and others) were recorded as 0-token runs even though they executed
dozens of turns/tool calls. An unavailable measurement must never be written as
zero; it is now stored as ``None`` and surfaced in the summary.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "oc_runner_usage", ROOT / "run_openclaw_bench.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["oc_runner_usage"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── _reported_usage ────────────────────────────────────────────────────

@pytest.mark.parametrize("usage", [None, {}, [], "n/a", 0])
def test_reported_usage_is_none_when_absent(usage):
    assert _load()._reported_usage(usage) is None


def test_reported_usage_returns_mapping_when_present():
    mod = _load()
    usage = {"input": 10, "output": 2, "total": 12}
    assert mod._reported_usage(usage) == usage


# ── _write_results ─────────────────────────────────────────────────────

def test_write_results_marks_unavailable_and_excludes_from_totals(tmp_path):
    mod = _load()
    out = tmp_path / "res.json"
    rows = [
        {
            "task_id": "task_398_card_game_engine", "passed": False,
            "elapsed_seconds": 636.3, "agent_usage_available": False,
            "agent_input_tokens": None, "agent_output_tokens": None,
            "agent_total_tokens": None, "agent_reasoning_tokens": None,
            "agent_cache_read_tokens": None, "agent_cache_write_tokens": None,
            "agent_turns": 59, "agent_steps": 49,
        },
        {
            "task_id": "task_392_manuscript_continuity", "passed": False,
            "elapsed_seconds": 848.4, "agent_usage_available": True,
            "agent_input_tokens": 100, "agent_output_tokens": 20,
            "agent_total_tokens": 120, "agent_reasoning_tokens": 5,
            "agent_cache_read_tokens": 0, "agent_cache_write_tokens": 0,
            "agent_turns": 47, "agent_steps": 50,
        },
    ]
    mod._write_results(rows, str(out), model="m/x", harness_version="v",
                       thinking="off", command="cmd")
    payload = json.loads(out.read_text())

    # Unavailable buckets stay None — never coerced to 0.
    unavailable = next(t for t in payload["tasks"]
                       if t["task_id"] == "task_398_card_game_engine")
    assert unavailable["agent_total_tokens"] is None
    assert unavailable["agent_usage_available"] is False

    # Totals only count the reported row; the unavailable run is surfaced.
    assert payload["summary"]["total_tokens"] == 120
    assert payload["summary"]["total_input_tokens"] == 100
    assert payload["summary"]["usage_unavailable_count"] == 1
    assert payload["summary"]["usage_unavailable_tasks"] == ["task_398_card_game_engine"]


def test_write_results_without_flag_keeps_legacy_rows_counted(tmp_path):
    """Old artifacts have no availability flag -> treated as available."""
    mod = _load()
    out = tmp_path / "res.json"
    rows = [{
        "task_id": "legacy", "passed": True, "elapsed_seconds": 1.0,
        "agent_input_tokens": 10, "agent_output_tokens": 5,
        "agent_total_tokens": 15,
    }]
    mod._write_results(rows, str(out), model="m/x")
    payload = json.loads(out.read_text())
    assert payload["summary"]["total_tokens"] == 15
    assert payload["summary"]["usage_unavailable_count"] == 0


# ── run_one_task: envelope -> token stats ──────────────────────────────

class _FakeTask:
    id = "task_fake_usage"
    prompt = "do the thing"

    def setup(self, workspace):
        (workspace / "input.txt").write_text("fixture", encoding="utf-8")

    def verify(self, workspace):
        return SimpleNamespace(passed=True, message="ok")


def _run_with_envelope(monkeypatch, envelope):
    mod = _load()
    stdout = json.dumps(envelope)
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=stdout, stderr=""),
    )
    return mod.run_one_task(_FakeTask(), model="m/x", timeout=5)


def test_run_one_task_omitted_usage_is_unavailable_not_zero(monkeypatch):
    # Envelope WITHOUT `usage` (docs: omitted when unavailable) but the run
    # clearly executed (assistantTurns/toolSummary present).
    result = _run_with_envelope(monkeypatch, {
        "ok": False, "status": "error",
        "assistantTurns": 35,
        "toolSummary": {"calls": 2},
        "sessionId": "669af08e-33eb-4f31-8d6f-fec39071558d",
    })
    assert result["agent_usage_available"] is False
    assert result["agent_total_tokens"] is None
    assert result["agent_input_tokens"] is None
    assert result["agent_output_tokens"] is None
    # Run-stat fields are still recorded.
    assert result["agent_turns"] == 35
    assert result["agent_steps"] == 2
    assert result["agent_session_id"] == "669af08e-33eb-4f31-8d6f-fec39071558d"


def test_run_one_task_reported_usage_is_parsed(monkeypatch):
    result = _run_with_envelope(monkeypatch, {
        "ok": True, "status": "ok",
        "usage": {"input": 120, "output": 8, "total": 128,
                  "cacheRead": 30, "reasoningTokens": 3},
        "assistantTurns": 2,
        "toolSummary": {"calls": 4},
        "costUsd": 0.0021,
        "sessionId": "abc",
    })
    assert result["agent_usage_available"] is True
    assert result["agent_input_tokens"] == 120
    assert result["agent_output_tokens"] == 8
    assert result["agent_total_tokens"] == 128
    assert result["agent_cache_read_tokens"] == 30
    assert result["agent_reasoning_tokens"] == 3
    assert result["agent_turns"] == 2
    assert result["agent_steps"] == 4


def test_run_one_task_garbage_stdout_is_unavailable(monkeypatch):
    mod = _load()
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="not json", stderr=""),
    )
    result = mod.run_one_task(_FakeTask(), model="m/x", timeout=5)
    assert result["agent_usage_available"] is False
    assert result["agent_total_tokens"] is None


# ── nested `usageReport.state` marker (fix-oc-usage-unavailable-marker) ─

def test_reported_usage_unavailable_nested_marker_is_none():
    # New envelope path (retry/continuation) marks usage unavailable with a
    # non-empty dict that carries NO flat token buckets.
    mod = _load()
    usage = {"usageReport": {"state": "unavailable"},
             "contextUsage": {"state": "unavailable"}}
    assert mod._reported_usage(usage) is None


def test_reported_usage_available_flat_with_usageReport_is_returned():
    mod = _load()
    usage = {"usageReport": {"state": "available"}, "input": 10421, "output": 161,
             "cacheRead": 20352, "cacheWrite": 0, "reasoningTokens": 19, "total": 30934}
    assert mod._reported_usage(usage) == usage


def test_reported_usage_missing_flat_buckets_is_unavailable():
    mod = _load()
    assert mod._reported_usage({"contextUsage": {"state": "available"}}) is None


def test_run_one_task_nested_unavailable_is_unavailable_not_zero(monkeypatch):
    mod = _load()
    envelope = {"ok": True, "status": "ok",
                "usage": {"usageReport": {"state": "unavailable"},
                          "contextUsage": {"state": "unavailable"}},
                "assistantTurns": 12, "toolSummary": {"calls": 3}}
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a, 0, stdout=json.dumps(envelope), stderr=""))
    result = mod.run_one_task(_FakeTask(), model="m/x", timeout=5)
    assert result["agent_usage_available"] is False
    assert result["agent_total_tokens"] is None
    assert result["agent_input_tokens"] is None
