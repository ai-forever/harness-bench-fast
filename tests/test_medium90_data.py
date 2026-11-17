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
    assert data["summary"] == {"rows": 129, "ranked_rows": 12, "coverage": {"1": 1, "10": 8, "40": 89, "80": 19, "90": 12}}
    assert all((r["score"] is None) == (r["measured_tasks"] != 90) for r in data["rows"])
    assert all(len(set(r["missing_task_numbers"])) + r["measured_tasks"] == 90 for r in data["rows"])


def test_profiles_preserved_without_name_based_joins_or_wave19_doublecount():
    old = json.loads((ROOT / "docs/leaderboard-results.json").read_text(encoding="utf-8"))
    rows = {r["id"]: r for r in build()["rows"]}
    assert set(r["id"] for r in old["rows"] if r.get("medium") or r.get("medium_historical") or r.get("wave19") or r.get("wave21")) < set(rows)
    for original in old["rows"]:
        if not any(original.get(key) for key in ("medium", "medium_historical", "wave19", "wave21")):
            continue
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


def test_ouroboros_harness_errors_count_as_failures_in_full_suite():
    source = json.loads((ROOT / "docs/secretmodel-1114-ouroboros.json").read_text())
    row = next(r for r in build()["rows"] if r["id"] == "secretmodel-1114-ouroboros")
    assert row["secret_model"] and row["model"] == "SecretModel-1114"
    assert row["reasoning_effort"] == "medium"
    assert len(source["tasks"]) == row["measured_tasks"] == 90
    assert sum(t["passed"] for t in source["tasks"]) == row["overall_passed"] == 25
    errors = [t for t in source["tasks"] if t.get("classification") == "harness_error"]
    assert len(errors) == 14 and not any(t["passed"] for t in errors)
    assert [t["number"] for t in errors] == source["harness_error_task_numbers"]
    assert row["missing_task_numbers"] == [] and row["status"] == "complete"
    assert row["score"] == 25 / 90 and row["rank"] is not None
    assert row["usage"]["tokens"]["status"] == "partial"
    assert source["scoring_revision"]["new_model_calls"] == 0
    assert source["scoring_revision"]["previous_measured_tasks"] == 90


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
    for name in ("secretmodel-1114-ouroboros.json", "pi-deepseek0731-medium90-20261114.json", "secretmodel-1114.json", "leaderboard-results.json", "wave21-results.json", "haiku-medium90.json", "codex-deepseek-medium90-20261027.json", "miniswe-deepseek-medium90-20261027.json", "claudecode-sonnet-medium90-20261027.json"):
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


def test_fresh_claudecode_sonnet90_uses_disjoint_audited_subsets_and_retained_counters():
    row = next(r for r in build()["rows"] if r["id"] == "claudecode-sonnet-medium90-20261027")
    source = json.loads((ROOT / "docs/claudecode-sonnet-medium90-20261027.json").read_text())
    assert row["overall_passed"] == source["passed"] and row["score"] == source["passed"] / 90
    assert row["previous"]["passed"] + row["wave21"]["passed"] == source["passed"]
    assert row["previous"]["measured"] == 80 and row["wave21"]["measured"] == 10
    assert row["usage"]["tokens"]["value"] == source["tokens"]
    assert row["usage"]["tokens"]["covered_tasks"] == source["token_coverage"]
    assert row["usage"]["steps"]["value"] == source["steps"]
    assert row["source_version"] == "0.21.3" and row["reasoning_effort"] == "high"


def test_fresh_pi_deepseek0731_preserves_score_refusal_and_lower_bound():
    source = json.loads((ROOT / "docs/pi-deepseek0731-medium90-20261114.json").read_text())
    row = next(r for r in build()["rows"] if r["id"] == "pi-deepseek0731-medium90-20261114")
    assert row["overall_passed"] == source["passed"] == 65
    assert row["previous"]["passed"] == 56 and row["wave21"]["passed"] == 9
    assert row["measured_tasks"] == 90 and row["source_version"] == "0.21.3"
    assert row["harness"] == "Pi 1.5.2" and row["reasoning_effort"] == "high"
    assert row["usage"]["steps"]["value"] == source["steps"] == 4542
    assert row["usage"]["tokens"]["value"] == source["tokens"] == 445223867
    assert row["usage"]["tokens"]["lower_bound"] and row["usage"]["tokens"]["status"] == "partial"
    assert row["usage"]["tokens"]["unmetered_requests"] == 10
    refusal = next(t for t in source["outcomes"] if t["number"] == 404)
    assert not refusal["passed"] and refusal["classification"] == "provider_refusal"
    assert source["provider_policy_blocks"] == 1 and source["timeouts"] == 10


def test_ouroboros_usage_matches_last_attempts_without_cache_double_count():
    source = json.loads((ROOT / "docs/secretmodel-1114-ouroboros.json").read_text())
    row = next(r for r in build()["rows"] if r["id"] == "secretmodel-1114-ouroboros")
    tasks = source["tasks"]
    assert source["tokens"] == sum(t["total_tokens"] for t in tasks)
    assert all(t["total_tokens"] == t["input_tokens"] + t["output_tokens"] for t in tasks)
    assert all(t["cached_input_tokens"] <= t["input_tokens"] for t in tasks)
    assert row["usage"]["tokens"]["value"] == source["tokens"]
    assert row["usage"]["tokens"]["lower_bound"]
    assert row["usage"]["tokens"]["unmetered_requests"] == sum(t["unmetered_requests"] for t in tasks)
    assert row["usage"]["tokens"]["covered_tasks"] == sum(t["usage_responses"] > 0 for t in tasks)
    assert row["usage"]["tokens"]["input_tokens"] == sum(t["input_tokens"] for t in tasks)
    assert row["usage"]["tokens"]["output_tokens"] == sum(t["output_tokens"] for t in tasks)
    by_number = {t["number"]: t for t in tasks}
    for number, attempt in ((437, 8), (442, 4), (443, 8), (449, 5), (454, 5)):
        assert by_number[number]["selected_attempt"] == attempt
    assert not any(by_number[n]["passed"] for n in (437, 442, 449))


def test_secret_pi_usage_preserves_score_and_marks_missing_native_episodes():
    source = json.loads((ROOT / "docs/secretmodel-1114.json").read_text())
    measurement = next(m for m in source["measurements"] if m["harness"] == "pi-mono" and m["suite"] == "medium")
    row = next(r for r in build()["rows"] if r["id"] == "secretmodel-1114-pi")
    assert row["overall_passed"] == 40 and row["measured_tasks"] == 90
    assert row["usage"]["tokens"]["value"] == measurement["tokens"] == 271297256
    assert sum(t["agent_total_tokens"] for t in measurement["tasks"]) == 271297256
    assert row["usage"]["tokens"]["input_tokens"] == 267790775
    assert row["usage"]["tokens"]["output_tokens"] == 3506481
    assert row["usage"]["tokens"]["covered_tasks"] == 90
    assert row["usage"]["tokens"]["status"] == "partial"
    assert row["usage"]["tokens"]["lower_bound"]
    assert row["usage"]["tokens"]["unmetered_requests"] == 14
    assert row["usage"]["tokens"]["completed_responses"] == 3314
    assert row["usage"]["steps"]["status"] == "unavailable"
    assert sum(t["token_usage_complete"] for t in measurement["tasks"]) == 76
    assert all(t["agent_total_tokens"] == t["input_tokens"] + t["output_tokens"] for t in measurement["tasks"])
