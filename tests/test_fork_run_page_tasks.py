"""Regression: fork-run-<id> pages list per-task rows for ALL our suites.

Covers OpenSpec change ``add-fork-run-per-task-suites``: previously the per-run
detail page filtered out every entry whose task id mapped to no Medium wave, so
Shot List / Light rows (e.g. ``fork-run-openclaw-gigachat-3-5-432b-a28b-thinking-off``)
shipped an EMPTY tasks table while their run JSON carried a full ``tasks`` array.

``build_fork_pages.build_model`` must keep such entries in ``_page_details`` and
tag them with the source artifact's suite label (Shot List/Light/Medium), and
``render_run_page`` must render them in the Wave column. New entries go ONLY to
the internal ``_page_details`` — rows / suites / usage stay untouched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_fork_pages as bfp  # noqa: E402

RID = bfp.row_id("OpenClaw", "GigaChat 3.5-432B-A28B", "thinking: off")
MEDIUM_STEM = "oc_medium_long_giga35base_off_20261113"
SHOTLIST_STEM = "oc_shotlist_giga35base_off_20261113"

MEDIUM = [{"task_id": "task_392_manuscript_continuity", "passed": True}]
SHOTLIST = [
    {"task_id": "task_01_create_hello", "passed": True, "elapsed_seconds": 0.5},
    {"task_id": "task_03_slugify", "passed": False, "elapsed_seconds": 1.5,
     "agent_total_tokens": 120, "error": "boom"},
]


def _summary(passed: int, total: int) -> dict:
    return {
        "passed": passed,
        "total": total,
        "pass_rate": (passed / total) if total else 0.0,
        "total_seconds": 1.0,
        "total_steps": 1,
        "total_tokens": 1,
        "estimated_cost_rub": 1.0,
    }


def _artifact(task_key: str, entries: list[dict]) -> dict:
    return {"summary": _summary(sum(1 for e in entries if e["passed"]), len(entries)), task_key: entries}


def _write(tmp_path: Path, name: str, artifact: dict) -> None:
    (tmp_path / name).write_text(json.dumps(artifact), encoding="utf-8")


def _row(model: dict) -> dict:
    matches = [r for r in model["rows"] if r["id"] == RID]
    assert matches, "row missing"
    return matches[0]


def test_shotlist_tasks_reach_the_detail_page(tmp_path):
    """Shot List entries (ids outside every Medium wave) are kept and labelled."""
    _write(tmp_path, MEDIUM_STEM + ".json", _artifact("tasks", MEDIUM))
    _write(tmp_path, SHOTLIST_STEM + ".json", _artifact("tasks", SHOTLIST))

    model = bfp.build_model(result_dirs=[tmp_path])
    row = _row(model)
    details = model["_page_details"][RID]

    labels = {t["task_id"]: t["label"] for t in details["tasks"]}
    assert labels["task_01_create_hello"] == "Shot List", labels
    assert labels["task_03_slugify"] == "Shot List", labels
    # Medium entries keep their wave label.
    assert labels["task_392_manuscript_continuity"] == "Long context", labels

    # The page renders the per-task rows (Wave column falls back to the suite).
    page = bfp.render_run_page(row, details)
    assert "task_01_create_hello" in page
    assert "task_03_slugify" in page
    assert "Shot List" in page
    assert "boom" in page  # error still surfaced in the Note cell


def test_shotlist_entries_do_not_change_wave_aggregates(tmp_path):
    """Only ``_page_details`` grows; suites/usage/sources stay Medium-only."""
    _write(tmp_path, MEDIUM_STEM + ".json", _artifact("tasks", MEDIUM))
    _write(tmp_path, SHOTLIST_STEM + ".json", _artifact("tasks", SHOTLIST))

    row = _row(bfp.build_model(result_dirs=[tmp_path]))
    # Long wave totals come from the Medium artifact alone (1 task), not 1 + 2.
    assert row["suites"]["long"]["total"] == 1, row["suites"]
    assert row["suites"]["long"]["passed"] == 1, row["suites"]
    # The Shot List artifact is not a source of the Medium row.
    assert SHOTLIST_STEM + ".json" not in row["sources"], row["sources"]


def test_shotlist_only_config_creates_no_row(tmp_path):
    """A configuration with only Shot List runs adds no Medium leaderboard row."""
    _write(tmp_path, SHOTLIST_STEM + ".json", _artifact("tasks", SHOTLIST))
    model = bfp.build_model(result_dirs=[tmp_path])
    assert model["rows"] == [], model["rows"]


def test_reads_results_array_too(tmp_path):
    """Pi-style artifacts keep their per-task entries from ``results``."""
    _write(tmp_path, MEDIUM_STEM + ".json", _artifact("results", MEDIUM))
    _write(tmp_path, SHOTLIST_STEM + ".json", _artifact("results", SHOTLIST))
    details = bfp.build_model(result_dirs=[tmp_path])["_page_details"][RID]
    ids = {t["task_id"] for t in details["tasks"]}
    assert {"task_01_create_hello", "task_03_slugify", "task_392_manuscript_continuity"} <= ids, ids
