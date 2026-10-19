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
    rows.extend(current)
    return {
        "task_set_version": "0.19.0", "light_tasks": 391, "medium_tasks": 80,
        "provenance": {
            "method": "Historical v0.18.0 rescores plus four complete v0.19.0 measurements dated 2026-10-19",
            "source_commit": "3796b9084093e5f9383077d7bde8f1ce1f0d57b5",
            "wave_results_sha256": hashlib.sha256(waves.read_bytes()).hexdigest(),
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "limitation": "Historical Medium covers only 40/80; current measurements cover all 80. Single-run intervals do not estimate repeat variability.",
        },
        "rows": rows,
    }


if __name__ == "__main__":
    target = ROOT / "docs/leaderboard-results.json"
    target.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n")
    print(target)
