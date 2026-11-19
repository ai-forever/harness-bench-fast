"""Recovery of a run's usage from the OpenClaw trajectory store.

When `openclaw agent exec --json` reports usage unavailable
(`usageReport.state == "unavailable"`), the tokens are still recorded in the
run's state dir (`<state-dir>/agents/<agent>/agent/openclaw-agent.sqlite`, table
`trajectory_runtime_events`). `run_openclaw_bench` now preserves `--state-dir`
and recovers the usage there (`agent_usage_source == "trajectory"`).
"""

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "oc_runner_recover", ROOT / "run_openclaw_bench.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["oc_runner_recover"] = mod
    spec.loader.exec_module(mod)
    return mod


def _state_dir(tmp_path: Path, events: list[dict]) -> Path:
    db = tmp_path / "agents" / "main" / "agent" / "openclaw-agent.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE trajectory_runtime_events "
        "(session_id TEXT, seq INTEGER, run_id TEXT, event_json TEXT, created_at TEXT)"
    )
    for i, ev in enumerate(events):
        con.execute(
            "INSERT INTO trajectory_runtime_events VALUES (?,?,?,?,?)",
            (ev.pop("_sid"), i, "r", json.dumps(ev), "t"),
        )
    con.commit()
    con.close()
    return tmp_path


def _ev(sid, total, input=1, output=1, **kw):
    return {"_sid": sid, "type": "model.completed",
            "data": {"usage": {"input": input, "output": output,
                               "total": total, **kw}}}


# ── _find_trajectory_db ────────────────────────────────────────────────

def test_find_db_none_for_missing_dir(tmp_path):
    mod = _load()
    assert mod._find_trajectory_db(tmp_path / "nope") is None
    assert mod._find_trajectory_db(None) is None


def test_find_db_locates_state_sqlite(tmp_path):
    mod = _load()
    state = _state_dir(tmp_path, [_ev("s1", 100)])
    assert mod._find_trajectory_db(state).name == "openclaw-agent.sqlite"


# ── _trajectory_usage ──────────────────────────────────────────────────

def test_trajectory_usage_picks_largest_total(tmp_path):
    mod = _load()
    state = _state_dir(tmp_path, [
        _ev("s1", 100, input=10, output=5),
        _ev("s1", 900, input=800, output=100),
    ])
    assert mod._trajectory_usage(state, "s1") == {
        "input": 800, "output": 100, "total": 900,
        "cacheRead": None, "cacheWrite": None, "reasoningTokens": None,
    }


def test_trajectory_usage_ignores_other_sessions(tmp_path):
    mod = _load()
    state = _state_dir(tmp_path, [_ev("other", 500), _ev("s1", 7)])
    assert mod._trajectory_usage(state, "s1")["total"] == 7


def test_trajectory_usage_none_for_unknown_or_empty(tmp_path):
    mod = _load()
    state = _state_dir(tmp_path, [_ev("s1", 100)])
    assert mod._trajectory_usage(state, "missing") is None
    assert mod._trajectory_usage(state, "") is None
    assert mod._trajectory_usage(tmp_path / "nope", "s1") is None


def test_trajectory_usage_survives_bad_json(tmp_path):
    mod = _load()
    db = tmp_path / "agent" / "openclaw-agent.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE trajectory_runtime_events "
                "(session_id TEXT, event_json TEXT)")
    con.execute("INSERT INTO trajectory_runtime_events VALUES ('s1','{not json')")
    con.execute("INSERT INTO trajectory_runtime_events VALUES ('s1', ?)",
                (json.dumps({"data": {"usage": {"input": 3, "output": 4,
                                                "total": 7}}}),))
    con.commit()
    con.close()
    assert mod._trajectory_usage(tmp_path, "s1")["total"] == 7


# ── run_one_task wiring ────────────────────────────────────────────────

class _FakeTask:
    id = "task_x"
    prompt = "do it"

    def setup(self, path):
        pass

    def verify(self, path):
        return SimpleNamespace(passed=True, message="ok")


def test_run_one_task_recovers_usage_from_trajectory(monkeypatch, tmp_path):
    mod = _load()
    envelope = {"ok": True, "status": "ok", "assistantTurns": 5,
                "toolSummary": {"calls": 2}, "sessionId": "sess-1",
                "usage": {"usageReport": {"state": "unavailable"},
                          "contextUsage": {"state": "unavailable"}}}
    captured = {}

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(envelope),
                                           stderr="")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    monkeypatch.setattr(mod, "_trajectory_usage",
                        lambda state, sid: {"input": 111, "output": 22,
                                            "total": 133, "cacheRead": 5,
                                            "cacheWrite": 0,
                                            "reasoningTokens": 3})
    result = mod.run_one_task(_FakeTask(), model="m/x", timeout=5)
    assert "--state-dir" in captured["cmd"]
    assert result["agent_usage_available"] is True
    assert result["agent_usage_source"] == "trajectory"
    assert result["agent_total_tokens"] == 133


def test_run_one_task_envelope_usage_wins(monkeypatch):
    mod = _load()
    envelope = {"ok": True, "status": "ok", "assistantTurns": 1,
                "toolSummary": {"calls": 1}, "sessionId": "s",
                "usage": {"input": 10, "output": 2, "total": 12}}

    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(
                            cmd, 0, stdout=json.dumps(envelope), stderr=""))
    monkeypatch.setattr(mod, "_trajectory_usage",
                        lambda state, sid: (_ for _ in ()).throw(
                            AssertionError("trajectory must not be consulted")))
    result = mod.run_one_task(_FakeTask(), model="m/x", timeout=5)
    assert result["agent_usage_source"] == "envelope"
    assert result["agent_total_tokens"] == 12


def test_run_one_task_no_source_marks_unavailable(monkeypatch):
    mod = _load()
    envelope = {"ok": True, "status": "ok", "sessionId": "s",
                "usage": {"usageReport": {"state": "unavailable"}}}
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(
                            cmd, 0, stdout=json.dumps(envelope), stderr=""))
    monkeypatch.setattr(mod, "_trajectory_usage", lambda state, sid: None)
    result = mod.run_one_task(_FakeTask(), model="m/x", timeout=5)
    assert result["agent_usage_available"] is False
    assert result["agent_usage_source"] is None
    assert result["agent_total_tokens"] is None
