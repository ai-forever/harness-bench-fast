"""Rebuild public Light/Medium aggregates from the frozen published wave snapshot.

No model calls. The archive supplies public labels, the JS supplies per-wave scores.
Unknown waves stay unmeasured; a missing row is an error, never a full-score fallback.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PublishedRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = {}
        self.row = None
        self.field = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article" and "data-wave-id" in attrs:
            self.row = {"id": attrs["data-wave-id"], "harness": "", "model": "", "score": "", "meta": ""}
        if self.row is not None:
            if tag == "h3":
                self.field = "harness"
            elif tag == "p":
                self.field = "model"
            elif attrs.get("class") in ("score", "meta"):
                self.field = attrs["class"]

    def handle_data(self, text):
        if self.row is not None and self.field:
            self.row[self.field] += text

    def handle_endtag(self, tag):
        if tag == "article" and self.row is not None:
            row = {key: value.strip() for key, value in self.row.items()}
            if row["id"] in self.rows:
                raise ValueError("duplicate published row: " + row["id"])
            self.rows[row["id"]] = row
            self.row = None
        if tag in ("h3", "p", "div"):
            self.field = None


def build():
    archive = ROOT / "docs/archive.html"
    waves = ROOT / "docs/wave-results.js"
    parser = PublishedRows()
    parser.feed(archive.read_text())
    snapshot = json.loads(waves.read_text().split("=", 1)[1].strip().rstrip(";"))
    if set(parser.rows) != set(snapshot["rows"]):
        raise ValueError("archive and per-wave snapshot have different result IDs")
    # Revisions with zero added tasks are metadata, not a selectable task range.
    light = [i for i, w in enumerate(snapshot["waves"]) if w["count"] and w["end"] <= 391]
    medium = [i for i, w in enumerate(snapshot["waves"]) if w["count"] and w["start"] > 391]
    assert sum(snapshot["waves"][i]["count"] for i in light) == 391
    assert sum(snapshot["waves"][i]["count"] for i in medium) == 40
    rows = []
    for key, row in parser.rows.items():
        item = snapshot["rows"][key]
        passed = item["passed"]
        if len(passed) != len(snapshot["waves"]):
            raise ValueError("incomplete per-wave array: " + key)
        if any(not math.isfinite(p) or p < 0 or p > w["count"] for p, w in zip(passed, snapshot["waves"], strict=True)):
            raise ValueError("invalid per-wave score: " + key)
        lp, mp = sum(passed[i] for i in light), sum(passed[i] for i in medium)
        published, denominator = map(float, row["score"].split("/"))
        if denominator != 431 or abs(lp + mp - published) > 0.051:
            raise ValueError("split does not reconstruct published score: " + key)
        rows.append({
            "id": key, "harness": re.sub(r"[*‡]", "", row["harness"]).strip(),
            "model": row["model"], "runs": item["runs"],
            "light": {"passed": lp, "total": 391},
            "medium_historical": {"passed": mp, "total": 40},
            "medium": None,
            "source_version": "0.18.0", "source_passed": published,
            "telemetry_note": row["meta"],
        })
    current = json.loads((ROOT / "docs/wave19-results.json").read_text())["rows"]
    for row in current:
        assert row["source_version"] == "0.19.0"
        assert row["light"]["total"] == 391 and row["medium"]["total"] == 80
        assert row["light"]["passed"] + row["medium"]["passed"] == row["source_passed"]
    matrix = json.loads((ROOT / "docs/wave19-harness-results.json").read_text(encoding="utf-8"))
    for row in current:
        row["measurement_type"] = "full_run"
        row["wave19"] = matrix["baseline_wave19"][row["id"]]
    rows.extend(current)
    by_id = {row["id"]: row for row in rows}
    for row in matrix["rows"]:
        assert row["wave19"]["total"] == 40
        if row.get("medium"):
            assert row["medium"]["total"] == 80
            assert row["medium"]["passed"] == row["medium_historical"]["passed"] + row["wave19"]["passed"]
        if row.get("extends_id"):
            target = by_id[row["extends_id"]]
            assert target["light"]["passed"] == row["light"]["passed"]
            assert target["medium_historical"]["passed"] == row["medium_historical"]["passed"]
            target.update({key: value for key, value in row.items() if key not in ("id", "extends_id")})
        else:
            assert row["id"] not in by_id
            rows.append(row)
            by_id[row["id"]] = row
    secret_path = ROOT / "docs/secret-700b-v019.json"
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    assert secret["ranked"] is True and secret["measurement_valid"] is True
    assert secret["validation_basis"] == "operator_acceptance"
    assert secret["recorded_tasks"] == secret["total_tasks"] == 471
    passed_numbers = secret["passed_task_numbers"]
    assert len(set(passed_numbers)) == len(passed_numbers) == secret["passed_tasks"]
    subsets = {}
    for name, source, start, end in (
        ("light", "light", 1, 391), ("medium", "medium", 392, 471),
        ("wave19", "wave19_feasibility", 432, 471),
    ):
        count = secret["counts"][source]
        assert count["total"] == end - start + 1
        assert count["passed"] == sum(start <= number <= end for number in passed_numbers)
        subsets[name] = {"passed": count["passed"], "total": count["total"]}
    rows.append({
        "id": "secret-700b-v019", "harness": secret["harness"], "model": secret["model"],
        "runs": 1, "measurement_type": "operator_accepted", "measurement_valid": True,
        "validation_basis": secret["validation_basis"],
        "source_version": secret["task_set_version"], "source_passed": secret["passed_tasks"],
        "medium_historical": None, "details_url": "secret-700b.html", **subsets,
    })
    sonnet_path = ROOT / "docs/sonnet55-medium.json"
    sonnet = json.loads(sonnet_path.read_text(encoding="utf-8"))
    assert sonnet["measurement_valid"] is True and sonnet["infrastructure_failures"] == 0
    assert sonnet["task_set_version"] == "0.19.0" and sonnet["total"] == 80
    passed_numbers = sonnet["passed_task_numbers"]
    assert len(set(passed_numbers)) == len(passed_numbers) == sonnet["passed"]
    assert all(392 <= number <= 471 for number in passed_numbers)

    def metric(passed, total):
        z = 1.959963984540054
        p = passed / total
        denominator = 1 + z * z / total
        midpoint = (p + z * z / (2 * total)) / denominator
        half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
        return {"passed": passed, "total": total, "wilson95": [midpoint - half, midpoint + half]}

    medium_result = metric(sonnet["passed"], 80)
    medium_result["note"] = "includes 2 provider content-filter failures"
    rows.append({
        "id": "sonnet55-pi-medium", "harness": sonnet["harness"], "model": sonnet["model"],
        "runs": 1, "measurement_type": "medium_run", "measurement_valid": True,
        "source_version": sonnet["task_set_version"], "source_passed": sonnet["passed"],
        "source_sha256": sonnet["source_sha256"], "reasoning_effort": sonnet["reasoning_effort"],
        "measurement_date": sonnet["measurement_date"], "light": None, "medium_historical": None,
        "medium": medium_result,
        "wave19": metric(sum(number >= 432 for number in passed_numbers), 40),
        "details_url": "sonnet55-medium.html",
    })
    astra_path = ROOT / "docs/astra-medium.json"
    astra = json.loads(astra_path.read_text(encoding="utf-8"))
    assert astra["measurement_valid"] is True and astra["infrastructure_failures"] == 0
    assert astra["task_set_version"] == "0.19.0" and astra["total"] == 80
    assert astra["reasoning_effort"] == "medium"
    passed_numbers = astra["passed_task_numbers"]
    assert len(set(passed_numbers)) == len(passed_numbers) == astra["passed"]
    assert all(392 <= number <= 471 for number in passed_numbers)
    rows.append({
        "id": "astra-pi-medium", "harness": astra["harness"], "model": astra["model"],
        "runs": 1, "measurement_type": "medium_run", "measurement_valid": True,
        "source_version": astra["task_set_version"], "source_passed": astra["passed"],
        "source_sha256": astra["source_sha256"], "reasoning_effort": astra["reasoning_effort"],
        "measurement_date": astra["measurement_date"], "light": None, "medium_historical": None,
        "medium": metric(astra["passed"], 80),
        "wave19": metric(sum(number >= 432 for number in passed_numbers), 40),
        "details_url": "astra-medium.html",
    })
    mini_path = ROOT / "docs/secret-sft-mini-v019.json"
    mini = json.loads(mini_path.read_text(encoding="utf-8"))
    assert mini["measurement_valid"] is True and mini["infrastructure_failures"] == 0
    assert mini["validation"]["valid"] is True and mini["validation"]["errors"] == []
    assert mini["task_set_version"] == "0.19.0"
    assert mini["recorded_tasks"] == mini["total_tasks"] == 471
    passed_numbers = mini["passed_task_numbers"]
    assert len(set(passed_numbers)) == len(passed_numbers) == mini["passed_tasks"]
    assert all(1 <= number <= 471 for number in passed_numbers)
    subsets = {}
    for name, source, start, end in (
        ("light", "light", 1, 391), ("medium", "medium", 392, 471),
        ("wave19", "wave19_feasibility", 432, 471),
    ):
        count = mini["counts"][source]
        assert count["total"] == end - start + 1
        assert count["passed"] == sum(start <= number <= end for number in passed_numbers)
        subsets[name] = metric(count["passed"], count["total"])
    rows.append({
        "id": "secret-sft-mini-v019", "harness": mini["harness"], "model": mini["model"],
        "runs": 1, "measurement_type": "full_run", "measurement_valid": True,
        "validation_basis": mini["validation_basis"], "reasoning_effort": "default",
        "source_version": mini["task_set_version"], "source_passed": mini["passed_tasks"],
        "source_sha256": mini["source_report_sha256"], "measurement_date": mini["run_dates"],
        "medium_historical": None, "details_url": "secret-sft-mini.html", **subsets,
    })
    return {
        "task_set_version": "0.19.0", "light_tasks": 391, "medium_tasks": 80,
        "provenance": {
            "method": "Historical rescores, five complete v0.19.0 runs, sixteen new wave19 harness configurations and fresh Sonnet/Astra Medium runs; composites explicitly labeled; secret sft deepagents accepted by the operator",
            "source_commit": "3796b9084093e5f9383077d7bde8f1ce1f0d57b5",
            # Hash canonical UTF-8/LF text, independent of Git checkout CRLF.
            "wave_results_sha256": hashlib.sha256(waves.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
            "secret_700b_sha256": hashlib.sha256(secret_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
            "secret_sft_mini_sha256": hashlib.sha256(mini_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
            "sonnet55_medium_sha256": hashlib.sha256(sonnet_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
            "astra_medium_sha256": hashlib.sha256(astra_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
            "archive_sha256": hashlib.sha256(archive.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
            "limitation": "Missing old coverage stays null; historical+wave19 composites are not fresh full runs. Wilson intervals do not estimate repeat variability.",
        },
        "rows": rows,
    }


if __name__ == "__main__":
    target = ROOT / "docs/leaderboard-results.json"
    target.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n")
    print(target)
