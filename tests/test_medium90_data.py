from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

from harness_bench.tasks import SUITES
from harness_bench.versioning import task_number

ROOT = Path(__file__).resolve().parents[1]
MODULE = runpy.run_path(str(ROOT / "scripts/build_medium90_data.py"))
build = MODULE["build"]
assemble_row = MODULE["assemble_row"]


def test_published_medium90_is_reproducible_and_exact_suite():
    data = build()
    assert json.loads((ROOT / "docs/medium90-results.json").read_text(encoding="utf-8")) == data
    assert data["task_numbers"] == [task_number(t.id) for t in SUITES["medium"]]
    assert data["summary"] == {"rows": 124, "ranked_rows": 7, "coverage": {"1": 1, "10": 8, "40": 89, "80": 19, "90": 7}}
    assert all((r["score"] is None) == (r["measured_tasks"] != 90) for r in data["rows"])
    assert all(len(set(r["missing_task_numbers"])) + r["measured_tasks"] == 90 for r in data["rows"])


def test_profiles_preserved_without_name_based_joins_or_wave19_doublecount():
    old = json.loads((ROOT / "docs/leaderboard-results.json").read_text(encoding="utf-8"))
    rows = {r["id"]: r for r in build()["rows"]}
    assert set(r["id"] for r in old["rows"]) < set(rows)
    for original in old["rows"]:
        row = rows[original["id"]]
        assert row["source_version"] == original["source_version"]
        assert row["measurement_valid"] == original.get("measurement_valid")
        if original.get("medium"):
            assert row["previous"]["passed"] == original["medium"]["passed"]
            assert row["measured_tasks"] == (90 if original.get("wave21") else 80)
        if original.get("wave21") and not original.get("medium"):
            assert row["previous"] is None and row["measured_tasks"] == 10
        if original.get("medium") and original.get("wave21"):
            assert row["measured_tasks"] == 90 and row["components"][-1]["source_version"] == "0.21.3"
    opus = [r for r in rows.values() if r["measurement_type"] == "targeted_partial"]
    assert len(opus) == 1 and opus[0]["observed_passed"] == 1
    assert opus[0]["measured_tasks"] == 1 and opus[0]["wave21"]["measured"] == 1


def test_complete_compatible_components_score_but_missing_or_unverified_do_not():
    old = {"task_numbers": list(range(392, 472)), "passed": 40, "note": "old", "usage": None}
    new = {"task_numbers": MODULE["SELECTED"], "passed": 5, "note": "new", "usage": None}
    complete = assemble_row({"measurement_valid": True}, [old, new])
    assert complete["score"] == .5 and complete["overall_passed"] == 45
    assert complete["usage"]["tokens"]["value"] is None
    assert assemble_row({"measurement_valid": True}, [old])["score"] is None
    assert assemble_row({"measurement_valid": None}, [old, new])["score"] is None
    with pytest.raises(ValueError, match="overlapping"):
        assemble_row({"measurement_valid": True}, [old, old])


def test_payload_provenance_is_portable_across_line_endings(tmp_path):
    (tmp_path / "docs").mkdir()
    for name in ("leaderboard-results.json", "wave21-results.json", "haiku-medium90.json", "codex-deepseek-medium90-20261027.json", "miniswe-deepseek-medium90-20261027.json"):
        content = (ROOT / "docs" / name).read_text(encoding="utf-8")
        (tmp_path / "docs" / name).write_bytes(content.replace("\n", "\r\n").encode("utf-8"))
    assert build(tmp_path) == build()


def test_incomplete_request_accounting_remains_partial_even_with_full_task_coverage():
    components = []
    for numbers in (list(range(392, 472)), MODULE["SELECTED"]):
        components.append({"task_numbers": numbers, "passed": 0, "note": "source",
                           "usage": {"tokens": {"value": 100, "covered_tasks": len(numbers),
                                                "status": "partial", "unmetered_requests": 2,
                                                "completed_responses": 3}}})
    result = assemble_row({"measurement_valid": True}, components)
    tokens = result["usage"]["tokens"]
    assert tokens["covered_tasks"] == 90 and tokens["status"] == "partial"
    assert tokens["lower_bound"] is True and tokens["unmetered_requests"] == 4
    assert tokens["completed_responses"] == 6
    sonnet = next(r for r in build()["rows"] if r["id"] == "sonnet55-pi-medium")
    assert sonnet["usage"]["tokens"]["unmetered_requests"] == 4
    assert sonnet["usage"]["tokens"]["lower_bound"] is True


def test_fresh_haiku_has_exact_outcomes_and_partial_retained_counters():
    data = build()
    expected = {"mini": (63, 2777, 493730606, 88), "deepagents": (60, 4078, 191772875, 89)}
    for profile, (passed, steps, tokens, token_coverage) in expected.items():
        row = next(r for r in data["rows"] if r["id"] == f"haiku55-{profile}-medium90-20261026")
        assert row["measurement_type"] == "fresh_medium90"
        assert row["overall_passed"] == passed and row["score"] == passed / 90
        assert row["previous"]["passed"] + row["wave21"]["passed"] == passed
        assert row["usage"]["steps"]["value"] == steps
        assert row["usage"]["tokens"]["value"] == tokens
        assert row["usage"]["tokens"]["covered_tasks"] == token_coverage
        assert row["usage"]["tokens"]["status"] == "partial"
        assert row["usage"]["tokens"]["lower_bound"] is True


def test_fresh_codex90_uses_disjoint_audited_subsets_and_retained_counters():
    row = next(r for r in build()["rows"] if r["id"] == "codex-deepseek-medium90-20261027")
    source = json.loads((ROOT / "docs/codex-deepseek-medium90-20261027.json").read_text())
    assert row["overall_passed"] == source["passed"] and row["score"] == source["passed"] / 90
    assert row["previous"]["passed"] + row["wave21"]["passed"] == source["passed"]
    assert row["previous"]["measured"] == 80 and row["wave21"]["measured"] == 10
    assert row["usage"]["tokens"]["value"] == source["tokens"]
    assert row["usage"]["tokens"]["covered_tasks"] == source["token_coverage"]
    assert row["usage"]["steps"]["value"] == source["steps"]
    assert row["source_version"] == "0.21.3" and row["reasoning_effort"] == "high"


def test_fresh_miniswe90_uses_disjoint_audited_subsets_and_retained_counters():
    row = next(r for r in build()["rows"] if r["id"] == "miniswe-deepseek-medium90-20261027")
    source = json.loads((ROOT / "docs/miniswe-deepseek-medium90-20261027.json").read_text())
    assert row["overall_passed"] == source["passed"] and row["score"] == source["passed"] / 90
    assert row["previous"]["passed"] + row["wave21"]["passed"] == source["passed"]
    assert row["previous"]["measured"] == 80 and row["wave21"]["measured"] == 10
    assert row["usage"]["tokens"]["value"] == source["tokens"]
    assert row["usage"]["tokens"]["covered_tasks"] == source["token_coverage"]
    assert row["usage"]["steps"]["value"] == source["steps"]
    assert row["source_version"] == "0.21.3" and row["reasoning_effort"] == "high"
