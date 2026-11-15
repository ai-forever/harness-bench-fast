#!/usr/bin/env python3
"""Guard tests for the canonical #medium-sample columns (fix-medium-sample-columns).

Covers:
  3.1 sample rows carry date (Profile), provider + reasoning level (Model),
      harness version (Harness) and a Steps value;
  3.2 the pilot Time/Tokens/Steps come from the manifest / its source runs
      (never a hardcoded dash), and an empty pilot manifest emits no "0/0" row;
  3.3 RU/EN sample-table rows are structurally identical.

Run:  python3 -m pytest tests/test_fork_sample_columns.py -q
"""
from __future__ import annotations

import json
import re
import runpy
import sys
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

# run_path returns the module globals; monkeypatching that dict lets us swap the
# manifest / result dirs that sample_rows() reads at call time.
HOME = runpy.run_path(str(SCRIPTS / "build_fork_home.py"))
sample_rows = HOME["sample_rows"]
# run_path hands back a copy of the globals; the functions execute against the
# original dict, so monkeypatch that.
GLOBALS = sample_rows.__globals__

DATE_RE = re.compile(r"\(\d{4}-\d{2}-\d{2}\)")
DASH = "\u2014"


# ---- 3.1 canonical metadata -------------------------------------------------
def test_sample_rows_carry_canonical_metadata():
    rows = sample_rows()
    assert rows, "sample_rows() must emit at least one row"

    ours = [r for r in rows if r["kind"] in ("coverage", "pilot")]
    dated = [r for r in ours if DATE_RE.search(r["profile"])]
    assert dated, f"no sample row has a run date in Profile: {[r['profile'] for r in ours]}"

    prov = [r for r in ours if " (cloud.ru)" in r["model"]]
    assert prov, f"no sample row carries the provider in Model: {[r['model'] for r in ours]}"

    oc = next(r for r in rows
              if r["harness"].startswith("OpenClaw") and "thinking: high" in r["profile"])
    assert oc["model"] == "DeepSeek-V4.1-Flash (cloud.ru) (thinking: high)"
    # Harness = "<name> <version>", and the name must not be duplicated.
    assert re.match(r"OpenClaw \d", oc["harness"]), oc["harness"]
    assert oc["harness"].count("OpenClaw") == 1, oc["harness"]
    assert oc["harness"] != "OpenClaw", "harness version missing from Harness cell"

    for r in rows:
        assert "steps" in r, f"row {r.get('harness')} lacks a Steps value"


def test_with_reasoning_provider_and_level():
    with_level = HOME["_with_reasoning"]("DeepSeek-V4.1-Flash", "thinking: high")
    assert with_level == "DeepSeek-V4.1-Flash (cloud.ru) (thinking: high)"
    # Non-reasoning profile -> no level suffix (provider still shown for our rows).
    assert HOME["_with_reasoning"]("GLM-5.2", "MCP off") == "GLM-5.2 (cloud.ru)"
    # Unknown provider -> no provider suffix (value never invented).
    assert HOME["_with_reasoning"]("GLM-5.2", "high", provider=None) == "GLM-5.2 (high)"


# ---- Steps column -----------------------------------------------------------
def test_sample_head_and_rows_have_steps_column():
    head = HOME["SAMPLE_HEAD"]
    assert ">Steps</th>" in head
    assert head.count("<th") == 12, head

    body = HOME["_sample_rows_html"](sample_rows())
    first = body.splitlines()[0]
    assert first.startswith("<tr")
    assert first.count("<td") == 12, first


# ---- 3.2 pilot Time/Tokens from the manifest --------------------------------
def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_pilot_time_tokens_read_from_sources(tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    _write(results / "pi_pilot_x_sub1_20261107.json", {
        "summary": {"total": 2, "passed": 0, "total_seconds": 300.0,
                    "total_tokens": 1234, "estimated_cost_rub": 5.5, "total_steps": 12},
    })
    manifest = _write(tmp_path / "manifest.json", {
        "id": "p", "size": 2, "created": "2026-11-07",
        "config": {"harness": "Pi", "model": "GigaChat 3 Pro", "profile": ""},
        "sources": ["pi_pilot_x_sub1_20261107.json"],
        "results": [{"source": "pi_pilot_x_sub1_20261107.json", "wave": "long",
                     "passed": 0, "total": 2}],
    })
    monkeypatch.setitem(GLOBALS, "PILOT_MANIFEST", manifest)
    monkeypatch.setitem(GLOBALS, "MEDIUM_JSON", tmp_path / "absent.json")
    monkeypatch.setitem(GLOBALS, "RESULT_DIRS", [results])

    pilot = next(r for r in sample_rows() if r["kind"] == "pilot")
    assert pilot["time"] == "5.0m", pilot  # 300s from the source summary, not a dash
    assert pilot["steps"] == "12", pilot
    assert pilot["tokens_cost"].startswith("1 234") and "\u20bd" in pilot["tokens_cost"], pilot
    assert pilot["profile"] == f"{DASH} (2026-11-07)"
    assert pilot["model"] == "GigaChat 3 Pro (cloud.ru)"


def test_empty_pilot_manifest_emits_no_zero_row(tmp_path, monkeypatch):
    manifest = _write(tmp_path / "manifest.json", {
        "id": "p", "size": 0, "config": {"harness": "Pi", "model": "M", "profile": ""},
        "sources": [], "results": [],
    })
    monkeypatch.setitem(GLOBALS, "PILOT_MANIFEST", manifest)
    monkeypatch.setitem(GLOBALS, "MEDIUM_JSON", tmp_path / "absent.json")
    monkeypatch.setitem(GLOBALS, "RESULT_DIRS", [tmp_path / "results"])

    rows = sample_rows()
    assert not any(r["kind"] == "pilot" for r in rows)
    assert all(r["result"] != "0/0" for r in rows)


# ---- 3.3 RU/EN sample-table parity ------------------------------------------
def _strip(cell: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", cell)).strip()


def _sample_cells(path: Path):
    text = path.read_text(encoding="utf-8")
    start = text.index('data-suite-panel="medium-sample"')
    end = text.index("</section>", start)
    chunk = text[start:end]
    rows = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", chunk, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)
        if len(tds) == 12:  # skip the <thead> (no <td>) and group rows (colspan)
            rows.append([_strip(c) for c in tds])
    return rows


def test_ru_en_sample_rows_parity():
    ru = _sample_cells(ROOT / "docs/fork.html")
    en = _sample_cells(ROOT / "docs/fork.en.html")
    assert ru and en, "sample table missing from a locale page"
    assert len(ru) == len(en), (ru, en)
    # Columns: 0 #, 1 Harness, 2 Profile, 3 Model, 4 Subset, 5 Result, 6 %,
    # 7 Coverage, 8 Time, 9 Steps, 10 Tokens (cost), 11 Status.
    # Subset (4) and Status (11) are localized; the canonical cells must match.
    canonical = [0, 1, 2, 3, 5, 6, 7, 8, 9, 10]
    for a, b in zip(ru, en, strict=True):
        assert len(a) == 12 and len(b) == 12, (a, b)
        for i in canonical:
            assert a[i] == b[i], (i, a, b)
