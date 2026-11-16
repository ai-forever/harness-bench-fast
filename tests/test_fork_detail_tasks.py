#!/usr/bin/env python3
"""Regression: fork-detail pages render their OWN run's per-task rows.

Guards the bilingual-refactor regression (commit ee69ecc): ``main()`` passed the
aggregated fork-home model to ``render_detail`` instead of each run's payload, so
every ``fork-detail-*`` page shipped an empty "Задачи (0)" table while the
clean-archive ``--check`` gate stayed green (it only compares generator output to
the committed pages, so a deterministically-empty generator is self-consistent).

Run:  python3 -m pytest tests/test_fork_detail_tasks.py -q
"""
from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

# run_path returns the module globals; build() resolves its helpers from that
# dict at call time, so monkeypatching it lets us stub the other suites.
HOME = runpy.run_path(str(SCRIPTS / "build_fork_home.py"))
GLOBALS = HOME["build"].__globals__

STEM = "dsh_shotlist_ds41flash_off_20261111"
DETAIL = f"fork-detail-{STEM}.html"


def _artifact(entries: list[dict]) -> dict:
    passed = sum(1 for e in entries if e["passed"])
    return {
        "task_set_version": "0.16.0",
        "harness": "dsh",
        "harness_version": "0.2.2-rc.1",
        "thinking": "none",
        "model": "deepseek-ai/DeepSeek-V4.1-Flash",
        "results": entries,
        "summary": {
            "total": len(entries),
            "passed": passed,
            "pass_rate": passed / len(entries) if entries else 0.0,
            "total_seconds": 3.0,
            "total_steps": 2,
            "total_tokens": 30,
            "estimated_cost_rub": 1.0,
        },
    }


def test_detail_page_contains_per_task_rows(tmp_path, monkeypatch):
    entries = [
        {"task_id": "task_01_create_hello", "passed": True, "elapsed_seconds": 1.0, "agent_total_tokens": 10},
        {"task_id": "task_02_other_thing", "passed": False, "elapsed_seconds": 2.0, "agent_total_tokens": 20},
    ]
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / (STEM + ".json")).write_text(json.dumps(_artifact(entries)), encoding="utf-8")

    monkeypatch.setitem(GLOBALS, "RESULT_DIRS", [results_dir])
    monkeypatch.setitem(GLOBALS, "_DETAIL_JOBS", [])
    # Stub suites we are not exercising so the test stays hermetic (no repo docs
    # / README / sibling dirs needed).
    for name in (
        "ours_light",
        "ours_medium",
        "sample_rows",
        "upstream_shotlist",
        "upstream_light",
        "upstream_medium",
        "upstream_subset_rows",
    ):
        monkeypatch.setitem(GLOBALS, name, lambda: [])

    HOME["build"]()
    details = HOME["render_details"]("ru")

    page = details[DETAIL]
    # Header count and both task ids must be present -> not an empty table.
    assert "Задачи (2)" in page, page
    assert "task_01_create_hello" in page
    assert "task_02_other_thing" in page
    # Two data rows, plus exactly one header row.
    assert page.count("<tr>") == 3, page
