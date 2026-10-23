"""Project audited calibration onto the selected wave, without new model calls."""
from __future__ import annotations

import json
from pathlib import Path


def append_wave21(rows: list[dict], root: Path) -> None:
    payload = json.loads((root / "docs/wave21-results.json").read_text(encoding="utf-8"))
    assert payload["task_numbers"] == [512, 518, 520, 522, 523, 524, 525, 526, 527, 530]
    for run in payload["configurations"]:
        if not run["complete"]:
            assert run["configuration_id"] == "opus55_high_160_partial" and run["measured_task_count"] == 1
            continue
        tasks = run["outcomes"]
        assert len(tasks) == len({task["number"] for task in tasks}) == 10
        assert {task["number"] for task in tasks} == set(payload["task_numbers"])
        assert sum(task["outcome"] == "pass" for task in tasks) == run["passed"]
        budget = run["budget"]
        unit = "graph steps" if budget["graph_supersteps"] else "model requests"
        cap = budget["graph_supersteps"] or budget["model_api_requests"]
        usage = {"tasks": 10, "observed_tasks": 10, "sources_sha256": [run["source_digest"]],
                 "notes": [payload["protocol"]["native_counters"], payload["protocol"]["comparability"]]}
        for key in ("steps", "tokens"):
            native = run["native_" + key]
            covered = native["coverage"]
            usage[key] = {
                "value": native["total"] if native["total"] is not None else native["observed_subtotal"] if covered else None,
                "covered_tasks": covered,
                "status": "complete" if covered == 10 else "partial" if covered else "unavailable",
            }
            if key == "steps":
                usage[key]["unit"] = "native_steps"
        rows.append({
            "id": "wave21-" + run["configuration_id"], "model": run["model"] + " (" + run["reasoning_effort"] + ")",
            "harness": run["harness"] + " · " + str(cap) + " " + unit,
            "runs": 1, "measurement_type": "post_selection_rescore", "measurement_valid": True,
            "source_version": run["source_task_set_version"], "display_version": "0.21.2",
            "measurement_date": run["measurement_date"],
            "reasoning_effort": run["reasoning_effort"], "source_sha256": run["source_digest"],
            "light": None, "medium": None, "medium_historical": None, "wave19": None,
            "wave21": {"passed": run["passed"], "total": 10, "note": "calibration rescore"},
            "telemetry": {"wave21": usage}, "details_url": "wave21-measurements.html#" + run["configuration_id"],
        })
