"""Keep reported usage attached to the published attempt and task subset."""
import copy
import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ATTACH = runpy.run_path(str(ROOT / "scripts/build_landing_data.py"))["attach_telemetry"]


def example():
    rows = [{"id": "test", "source_sha256": "a" * 64, "medium": {"total": 80}}]
    usage = {
        "tasks": 80, "observed_tasks": 80, "sources_sha256": ["a" * 64],
        "steps": {"value": 80, "covered_tasks": 80, "status": "complete"},
        "tokens": {"value": 30, "input_tokens": 20, "output_tokens": 10,
                   "covered_tasks": 80, "status": "partial"},
    }
    return rows, {"schema_version": 1, "rows": {"test": {"medium": usage}}}


@pytest.mark.parametrize("defect", ["scope", "source", "coverage", "components", "missing"])
def test_reject_misleading_usage(defect):
    rows, telemetry = example()
    usage = telemetry["rows"]["test"]["medium"]
    if defect == "scope":
        usage["tasks"] = 40
    elif defect == "source":
        usage["sources_sha256"] = ["b" * 64]
    elif defect == "coverage":
        usage["steps"]["covered_tasks"] = 79
    elif defect == "components":
        usage["tokens"]["value"] += 10
    else:
        usage["tokens"]["status"] = "unavailable"
    with pytest.raises(ValueError):
        ATTACH(rows, telemetry)


def test_telemetry_does_not_change_scores_or_fabricate_other_scopes():
    rows, telemetry = example()
    before = copy.deepcopy(rows)
    ATTACH(rows, telemetry)
    assert rows[0].pop("telemetry") == telemetry["rows"]["test"]
    assert rows == before


def test_audited_medium_totals_match_existing_response_audits():
    usage = json.loads((ROOT / "docs/leaderboard-telemetry.json").read_text())["rows"]
    astra = json.loads((ROOT / "docs/astra-medium.json").read_text())
    assert usage["astra-pi-medium"]["medium"]["tokens"]["value"] == astra["usage_successful_responses"]["total_tokens"]
    assert usage["astra-pi-medium"]["medium"]["tokens"]["unmetered_requests"] == 7
    assert usage["sonnet55-pi-medium"]["medium"]["tokens"]["value"] == 118517782
    assert usage["sonnet55-pi-medium"]["wave19"]["tokens"]["status"] == "complete"
    assert usage["sonnet55-pi-medium"]["medium"]["tokens"]["status"] == "partial"
