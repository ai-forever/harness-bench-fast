from __future__ import annotations

import copy
import json
import runpy
from pathlib import Path

import pytest

from harness_bench.__main__ import _task_ids, build_parser
from harness_bench.suite_reports import split_reports
from harness_bench.tasks import ALL_TASKS, SUITES
from harness_bench.versioning import task_number


def source(tasks=ALL_TASKS):
    return {"task_set_version": "0.19.0", "measurement_valid": True, "tasks": [
        {"task_id": task.id, "passed": task_number(task.id) in {391, 392, 431, 432, 471},
         "attempt": 1, "attempts": 1, "agent_steps": 2, "agent_total_tokens": 10}
        for task in tasks
    ]}


def split(payload):
    return split_reports(payload, source_sha256="test", source_path="/read-only/results.json")


@pytest.mark.parametrize("command", ["list", "run", "run-cli", "run-openrouter", "run-pure", "verify-gold"])
def test_named_suites_are_disjoint_complete_and_available_on_runners(command):
    light = _task_ids(build_parser().parse_args([command, "--suite", "light"]))
    medium = _task_ids(build_parser().parse_args([command, "--suite", "medium"]))
    assert len(light) == 391 and len(medium) == 80
    assert not set(light) & set(medium)
    assert light + medium == [t.id for t in ALL_TASKS]
    assert [task_number(x) for x in medium] == list(range(392, 472))


def test_split_recomputes_counts_telemetry_and_boundaries_without_mutating_source():
    payload = source()
    original = copy.deepcopy(payload)
    reports = split(payload)
    assert payload == original
    light, medium = reports["light"], reports["medium"]
    assert (light["passed"], light["total"], light["steps"], light["tokens"]) == (1, 391, 782, 3910)
    assert (medium["passed"], medium["total"], medium["steps"], medium["tokens"]) == (4, 80, 160, 800)
    assert all(r["complete"] and r["status"] == "complete" for r in reports.values())
    assert medium["attempts"][0]["wilson95"][0] < 4 / 80 < medium["attempts"][0]["wilson95"][1]


def test_old431_source_stays_partial_medium_with_original_version():
    payload = source(ALL_TASKS[:431])
    payload["task_set_version"] = "0.18.0"
    reports = split(payload)
    assert reports["light"]["status"] == "complete"
    medium = reports["medium"]
    assert medium["task_set_version"] == "0.18.0"
    assert (medium["measured_tasks"], medium["expected_tasks"]) == (40, 80)
    assert medium["pass_rate"] is None
    assert medium["attempts"][0]["score"] is None
    assert len(medium["attempts"][0]["missing_task_ids"]) == 40


def test_infrastructure_invalidity_is_local_to_affected_suite_and_no_score():
    payload = source()
    payload["measurement_valid"] = False
    payload["tasks"][-1].update(failure_kind="infrastructure", passed=False)
    reports = split(payload)
    assert reports["light"]["status"] == "complete"
    assert reports["medium"]["status"] == "infrastructure_invalid"
    assert reports["medium"]["pass_rate"] is None


def test_timeout_is_normal_failure_and_missing_telemetry_stays_missing():
    payload = source()
    payload["tasks"][-1].update(failure_kind="timeout", passed=False, agent_steps=None, agent_total_tokens=None)
    medium = split(payload)["medium"]
    assert medium["status"] == "complete"
    assert medium["step_metric_coverage"] == medium["token_metric_coverage"] == 79


def test_unexplained_source_invalidity_cannot_be_cleared_by_split():
    payload = source()
    payload["measurement_valid"] = False
    for report in split(payload).values():
        assert report["status"] == "infrastructure_invalid"
        assert report["pass_rate"] is None
        assert report["attempts"][0]["score"] is None


def test_missing_source_validity_is_not_upgraded_to_verified():
    payload = source()
    del payload["measurement_valid"]
    for report in split(payload).values():
        assert report["status"] == "validity_unverified"
        assert report["measurement_valid"] is None
        assert report["pass_rate"] is None
        assert report["attempts"][0]["score"] is None


def test_pending_retry_counts_as_invalid_in_both_status_and_counter():
    payload = source()
    payload["tasks"][-1]["rerun_on_continue"] = True
    medium = split(payload)["medium"]
    assert medium["status"] == "infrastructure_invalid"
    assert medium["infrastructure_failures"] == 1
    assert medium["pass_rate"] is None
    assert medium["feasibility_pairs"]["score"] is None


def test_partial_pair_scores_are_withheld_and_complete_pairs_remain_available():
    partial = split(source(SUITES["feasibility"][:1]))["medium"]["feasibility_pairs"]
    assert partial["status"] == "partial" and partial["score"] is None
    assert partial["cohorts"][0]["score"] is None
    # Pair coverage can complete before the longer Medium tasks finish.
    medium = split(source(SUITES["feasibility"]))["medium"]
    assert medium["status"] == "partial" and medium["pass_rate"] is None
    assert medium["feasibility_pairs"]["status"] == "complete"
    assert medium["feasibility_pairs"]["score"] is not None


def test_unknown_validity_also_withholds_secondary_pair_scores():
    payload = source()
    del payload["measurement_valid"]
    pairs = split(payload)["medium"]["feasibility_pairs"]
    assert pairs["status"] == "validity_unverified"
    assert pairs["score"] is None
    assert all(cohort["score"] is None for cohort in pairs["cohorts"])


def test_repeats_stay_separate_and_missing_attempt_cannot_make_complete():
    payload = source(SUITES["medium"])
    for row in payload["tasks"]:
        row["attempts"] = 2
    payload["tasks"] += [{**row, "attempt": 2, "passed": False} for row in payload["tasks"]]
    medium = split(payload)["medium"]
    assert [a["passed"] for a in medium["attempts"]] == [4, 0]
    assert medium["status"] == "complete"
    payload["tasks"].pop()
    assert split(payload)["medium"]["status"] == "partial"


@pytest.mark.parametrize("change", ["duplicate", "number", "passed", "attempts"])
def test_malformed_or_duplicate_source_is_rejected(change):
    payload = source(ALL_TASKS[:1])
    if change == "duplicate":
        payload["tasks"].append(dict(payload["tasks"][0]))
    else:
        payload["tasks"][0][change] = {"number": 471, "passed": "false", "attempts": 0}[change]
    with pytest.raises(ValueError):
        split(payload)


def test_published_landing_scores_preserve_all_source_totals_and_missing_wave19():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "docs/leaderboard-results.json").read_text())
    assert data == runpy.run_path(str(root / "scripts/build_landing_data.py"))["build"]()
    snapshot = json.loads((root / "docs/wave-results.js").read_text().split("=", 1)[1].strip().rstrip(";"))
    historical = [row for row in data["rows"] if row["id"] in snapshot["rows"]]
    full = [row for row in data["rows"] if row.get("measurement_type") == "full_run"]
    new_only = [row for row in data["rows"] if row.get("measurement_type") == "wave19_only"]
    composites = [row for row in data["rows"] if row.get("measurement_type") == "historical_plus_wave19"]
    assert len(historical) == 78 and len(full) == 11
    assert len(new_only) == 13 and len(composites) == 3
    assert len(data["rows"]) == len({row["id"] for row in data["rows"]}) == 107
    astra = next(row for row in data["rows"] if row["id"] == "astra-pi-medium")
    assert astra["light"] is None and astra["medium_historical"] is None
    assert astra["medium"]["passed"] == 58 and astra["medium"]["total"] == 80
    assert astra["wave19"]["passed"] == 23 and astra["wave19"]["total"] == 40
    assert astra["reasoning_effort"] == "medium"
    assert (root / "docs" / astra["details_url"]).exists()
    sonnet = next(row for row in data["rows"] if row["id"] == "sonnet55-pi-medium")
    assert sonnet["light"] is None and sonnet["medium_historical"] is None
    assert sonnet["medium"]["passed"] == 53 and sonnet["medium"]["total"] == 80
    assert sonnet["wave19"]["passed"] == 32 and sonnet["wave19"]["total"] == 40
    assert sonnet["reasoning_effort"] == "medium"
    assert "2 provider content-filter failures" in sonnet["medium"]["note"]
    assert (root / "docs" / sonnet["details_url"]).exists()
    accepted = [row for row in data["rows"] if row.get("validation_basis") == "operator_acceptance"]
    assert len(accepted) == 1 and accepted[0]["id"] == "secret-700b-v019"
    assert accepted[0]["measurement_valid"] is True
    assert accepted[0]["measurement_type"] == "operator_accepted"
    assert accepted[0]["light"] == {"passed": 370, "total": 391}
    assert accepted[0]["medium"] == {"passed": 7, "total": 80}
    assert accepted[0]["source_passed"] == 377
    assert sum(row["runs"] for row in historical) == 80
    for row in full:
        assert row["light"]["total"] == 391 and row["medium"]["total"] == 80
        assert row["light"]["passed"] + row["medium"]["passed"] == row["source_passed"]
    for row in historical:
        assert row["light"]["total"] == 391
        assert row["medium_historical"]["total"] == 40
        assert abs(row["light"]["passed"] + row["medium_historical"]["passed"] - row["source_passed"]) <= .051
    for row in new_only:
        assert row["light"] is None and row["medium"] is None
        assert row["medium_historical"] is None and row["source_passed"] is None
        assert row["wave19"]["total"] == 40
    for row in composites:
        assert row["medium"]["total"] == 80
        assert row["medium"]["passed"] == row["medium_historical"]["passed"] + row["wave19"]["passed"]
    assert 'type="range"' not in (root / "docs/index.html").read_text()
    archive = (root / "docs/archive.html").read_text()
    assert all(f'id="measurement-{row["id"]}"' in archive for row in historical)
    protocol = (root / "docs/harness-wave19.html").read_text()
    for row in [*new_only, *composites]:
        assert row["details_url"].split("#")[0] == "harness-wave19.html"
        assert 'id="' + row["details_url"].split("#")[1] + '"' in protocol


def test_landing_provenance_is_independent_of_checkout_line_endings(tmp_path):
    root = Path(__file__).resolve().parents[1]
    build = runpy.run_path(str(root / "scripts/build_landing_data.py"))["build"]
    expected = build()
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("archive.html", "wave-results.js", "wave19-results.json", "wave19-harness-results.json", "secret-700b-v019.json", "secret-sft-mini-v019.json", "sonnet55-medium.json", "astra-medium.json", "universal-agent-haiku.json", "giga35-ultra-reasoning-v019.json", "leaderboard-telemetry.json", "secret-sft-codex-v019.json", "brain-haiku-experiment.json", "haiku-three-harnesses-v0191.json"):
        text = (root / "docs" / name).read_text(encoding="utf-8")
        assert "\n" in text
        (docs / name).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    build.__globals__["ROOT"] = tmp_path
    assert build() == expected


def test_codex_final_wire_audit_and_subset_token_sums():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "docs/leaderboard-results.json").read_text())
    codex = json.loads((root / "docs/secret-sft-codex-v019.json").read_text())
    assert codex["pending_task_numbers"] == []
    assert codex["measurement_valid"] and codex["ranked"]
    assert any(row["id"] == "secret-sft-codex-v019" for row in data["rows"])
    usage = codex["telemetry"]["selected_api"]
    assert usage["total_tokens"] == 112031097
    assert usage["requests_with_usage"] == 3776
    assert usage["requests_missing_usage"] == 89
    assert usage["lower_bound"] is True
    for key in ("input_tokens", "output_tokens", "total_tokens", "requests", "requests_with_usage", "requests_missing_usage"):
        assert usage[key] == sum(codex["counts"][name]["tokens"][key] for name in ("light", "medium"))
    assert codex["counts"]["wave19_feasibility"]["tokens"]["total_tokens"] <= codex["counts"]["medium"]["tokens"]["total_tokens"]

    row = next(row for row in data["rows"] if row["id"] == "secret-sft-codex-v019")
    for scope, source in (("light", "light"), ("medium", "medium"), ("wave19", "wave19_feasibility")):
        public = row["telemetry"][scope]
        audited = codex["counts"][source]["tokens"]
        assert public["sources_sha256"] == [codex["source_report_sha256"]]
        assert public["tokens"]["value"] == audited["total_tokens"]
        assert public["tokens"]["unmetered_requests"] == audited["requests_missing_usage"]
        assert public["tokens"]["status"] == "partial"
    assert "all_attempts_api" not in codex["telemetry"]
