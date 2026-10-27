"""Build the cumulative Medium90 board from retained public measurements only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELECTED = [512, 518, 520, 522, 523, 524, 525, 526, 527, 530]
NUMBERS = list(range(392, 472)) + SELECTED


def component(numbers, result, usage, source_version, note):
    if len(numbers) != result["total"]:
        raise ValueError("measurement task subset disagrees with denominator")
    return {"task_numbers": numbers, "passed": result["passed"], "usage": usage,
            "source_version": source_version, "note": note}


def assemble_row(identity, components):
    """Components must already belong to one admitted measured configuration.

    Never infer profile equivalence from display names. The legacy row identity
    supplies only already-published joins; wave21 configurations stay separate.
    """
    seen = set()
    for item in components:
        numbers = set(item["task_numbers"])
        if len(numbers) != len(item["task_numbers"]) or seen & numbers or not numbers <= set(NUMBERS):
            raise ValueError("overlapping or out-of-scope measurement")
        if not 0 <= item["passed"] <= len(numbers):
            raise ValueError("invalid pass count")
        seen.update(numbers)
    previous = [c for c in components if set(c["task_numbers"]) <= set(range(392, 472))]
    wave21 = [c for c in components if set(c["task_numbers"]) <= set(SELECTED)]
    if len(previous) + len(wave21) != len(components):
        raise ValueError("component spans different board columns")
    def subtotal(items, total):
        if not items:
            return None
        return {"passed": sum(c["passed"] for c in items),
                "measured": sum(len(c["task_numbers"]) for c in items), "total": total,
                "scope_note": "; ".join(c["note"] for c in items)}
    valid = identity.get("measurement_valid")
    complete = seen == set(NUMBERS)
    observed = sum(c["passed"] for c in components)
    ranked = complete and valid is True
    usage = {"tasks": 90, "observed_tasks": len(seen), "notes": [
        "Retained public measurement counters only. Missing tasks are unmeasured, not failures.",
        "Task counts show counter availability, not full request-level accounting."]}
    for c in components:
        source = c.get("usage") or {}
        usage["notes"].extend(source.get("notes", []))
    usage["sources_sha256"] = sorted({sha for c in components for sha in (c.get("usage") or {}).get("sources_sha256", [])})
    for metric in ("steps", "tokens"):
        partial = False
        accounting = []
        values, units = [], set()
        covered = 0
        raw = False
        lower_bound = False
        for c in components:
            m = (c.get("usage") or {}).get(metric)
            if not m or m.get("value") is None or m.get("status") == "unavailable":
                continue
            values.append(m["value"])
            covered += m["covered_tasks"]
            raw |= m["status"] == "raw"
            partial |= m["status"] == "partial"
            accounting.append(m)
            lower_bound |= m.get("lower_bound", False) or bool(m.get("unmetered_requests", 0))
            if m.get("unit"):
                units.add(m["unit"])
        mixed = metric == "steps" and len(units) > 1
        usage[metric] = {"value": sum(values) if values and not mixed else None,
                         "covered_tasks": covered if not mixed else 0,
                         "status": "unavailable" if not values or mixed else "raw" if raw else "complete" if covered == 90 and not partial else "partial",
                         "lower_bound": lower_bound}
        for field in ("input_tokens", "output_tokens", "completed_responses", "unmetered_requests"):
            known = [m[field] for m in accounting if m.get(field) is not None]
            if known:
                usage[metric][field] = sum(known)
                usage[metric][field + "_accounting_complete"] = len(known) == len(components)
        if units and not mixed:
            usage[metric]["unit"] = next(iter(units))
        if mixed:
            usage["notes"].append("Different native step units cannot be summed.")
    return {**identity, "previous": subtotal(previous, 80), "wave21": subtotal(wave21, 10),
            "components": [{k: v for k, v in c.items() if k != "usage"} for c in components],
            "measured_tasks": len(seen), "target_tasks": 90, "missing_task_numbers": sorted(set(NUMBERS) - seen),
            "observed_passed": observed, "overall_passed": observed if ranked else None,
            "score": observed / 90 if ranked else None, "rank": None,
            "status": "complete" if ranked else "invalid" if valid is False else "partial" if not complete else "unverified",
            "usage": usage}


def build(root=ROOT):
    legacy_path = root / "docs/leaderboard-results.json"
    wave_path = root / "docs/wave21-results.json"
    old = json.loads(legacy_path.read_text(encoding="utf-8"))
    wave = json.loads(wave_path.read_text(encoding="utf-8"))
    assert wave["task_numbers"] == SELECTED
    rows = []
    for row in old["rows"]:
        components = []
        telemetry = row.get("telemetry") or {}
        if row.get("medium"):
            components.append(component(list(range(392, 472)), row["medium"], telemetry.get("medium"), row["source_version"], "historical Medium80"))
        else:
            for key, numbers, note in (("medium_historical", list(range(392, 432)), "tasks392–431 only"),
                                       ("wave19", list(range(432, 472)), "wave19 only · tasks432–471")):
                if row.get(key):
                    components.append(component(numbers, row[key], telemetry.get(key), row["source_version"], note))
        if row.get("wave21"):
            components.append(component(
                SELECTED, row["wave21"], telemetry.get("wave21"),
                row.get("wave21_source_version") or row["source_version"],
                row["wave21"].get("note") or "selected wave21"))
        if not components:
            continue
        keys = ("id", "model", "harness", "runs", "source_version", "reasoning_effort", "measurement_type", "measurement_valid", "details_url")
        identity = {key: row.get(key) for key in keys}
        identity["profile_id"] = row["id"]
        rows.append(assemble_row(identity, components))
    for run in wave["configurations"]:
        if run["complete"]:
            continue
        outcomes = run["outcomes"]
        numbers = [r["number"] for r in outcomes]
        usage = {"tasks": len(numbers)}
        for metric in ("steps", "tokens"):
            m = run["native_" + metric]
            usage[metric] = {"value": m["total"] if m["total"] is not None else m["observed_subtotal"],
                             "covered_tasks": m["coverage"], "status": "complete" if m["coverage"] == len(numbers) else "partial"}
        usage["steps"]["unit"] = "native_steps"
        identity = {"id": "wave21-" + run["configuration_id"], "profile_id": "wave21-" + run["configuration_id"],
                    "model": run["model"] + " (" + run["reasoning_effort"] + ")", "harness": run["harness"] + " · 160 graph steps",
                    "runs": 1, "source_version": run["source_task_set_version"], "reasoning_effort": run["reasoning_effort"],
                    "measurement_type": "targeted_partial", "measurement_valid": True,
                    "details_url": "wave21-measurements.html#" + run["configuration_id"]}
        rows.append(assemble_row(identity, [component(numbers, {"total": len(numbers), "passed": run["passed"]}, usage, run["source_task_set_version"], "targeted wave21 follow-up")]))
    fresh_path = root / "docs/haiku-medium90.json"
    fresh = json.loads(fresh_path.read_text(encoding="utf-8"))
    assert fresh["task_set_version"] == "0.21.3" and fresh["task_count"] == 90
    for run in fresh["rows"]:
        tasks = run["tasks"]
        assert sorted(t["number"] for t in tasks) == sorted(NUMBERS)
        assert sum(t["passed"] for t in tasks) == run["passed"]
        components = []
        for numbers, note in ((list(range(392, 472)), "fresh Medium90 run · historical80 subset"),
                              (SELECTED, "fresh Medium90 run · selected wave21 subset")):
            subset = [t for t in tasks if t["number"] in numbers]
            usage = {"sources_sha256": [run["source_sha256"]], "notes": [
                "One retained attempt per task; superseded infrastructure attempts excluded.",
                "Native counters only; missing counters and request-level accounting are unaudited."]}
            for metric, field in (("steps", "agent_steps"), ("tokens", "agent_total_tokens")):
                values = [t[field] for t in subset if t[field] is not None]
                usage[metric] = {"value": sum(values), "covered_tasks": len(values),
                                 "status": "partial", "lower_bound": True}
            usage["steps"]["unit"] = "native_steps"
            components.append(component(numbers, {"total": len(subset), "passed": sum(t["passed"] for t in subset)},
                                        usage, fresh["task_set_version"], note))
        identity = {"id": run["id"], "profile_id": run["id"], "model": "Claude Haiku 5.5 (default)",
                    "harness": run["harness"], "runs": 1, "source_version": fresh["task_set_version"],
                    "reasoning_effort": "default", "measurement_type": "fresh_medium90",
                    "measurement_valid": run["measurement_valid"], "details_url": "haiku-medium90.html#" + run["profile"]}
        rows.append(assemble_row(identity, components))
    codex90_path = root / "docs/codex-deepseek-medium90-20261027.json"
    codex = json.loads(codex90_path.read_text(encoding="utf-8"))
    assert codex["measurement_valid"] and codex["total"] == 90
    assert [r["number"] for r in codex["outcomes"]] == sorted(NUMBERS)
    assert sum(r["passed"] for r in codex["outcomes"]) == codex["passed"]
    components = []
    for numbers, note in ((list(range(392, 472)), "fresh Medium90 run · earlier80 subset"),
                          (SELECTED, "fresh Medium90 run · selected wave21 subset")):
        subset = [r for r in codex["outcomes"] if r["number"] in numbers]
        usage = {"sources_sha256": [codex["source_digest"]], "notes": [
            "One fresh90 cohort; first retained clean attempt per task, including failures. Superseded transport attempts excluded.",
            "Tokens are captured API input + output usage; cached input and reasoning output are counted once."]}
        for metric, field in (("steps", "agent_steps"), ("tokens", "agent_total_tokens")):
            values = [r[field] for r in subset if r[field] is not None]
            covered = len(values) if metric == "steps" else sum(r["token_usage_present"] for r in subset)
            partial = covered != len(subset) or (metric == "tokens" and not all(r["token_usage_complete"] for r in subset))
            usage[metric] = {"value": sum(values), "covered_tasks": covered,
                             "status": "unavailable" if not values else "partial" if partial else "complete", "lower_bound": partial}
        usage["steps"]["unit"] = "native_steps"
        usage["tokens"]["unmetered_requests"] = sum(r["unmetered_requests"] for r in subset)
        usage["tokens"]["completed_responses"] = sum(r["completed_responses"] for r in subset)
        usage["tokens"]["input_tokens"] = sum(r["input_tokens"] or 0 for r in subset)
        usage["tokens"]["output_tokens"] = sum(r["output_tokens"] or 0 for r in subset)
        components.append(component(numbers, {"total": len(subset), "passed": sum(r["passed"] for r in subset)},
                                    usage, codex["task_set_version"], note))
    identity = {"id": "codex-deepseek-medium90-20261027", "profile_id": "codex-deepseek-medium90-20261027",
                "model": codex["model"], "harness": codex["harness"], "runs": 1,
                "source_version": codex["task_set_version"], "reasoning_effort": "high",
                "measurement_type": "fresh_medium90", "measurement_valid": True,
                "details_url": "codex-deepseek-medium90.html"}
    rows.append(assemble_row(identity, components))
    miniswe90_path = root / "docs/miniswe-deepseek-medium90-20261027.json"
    miniswe = json.loads(miniswe90_path.read_text(encoding="utf-8"))
    assert miniswe["measurement_valid"] and miniswe["total"] == 90
    assert [r["number"] for r in miniswe["outcomes"]] == sorted(NUMBERS)
    assert sum(r["passed"] for r in miniswe["outcomes"]) == miniswe["passed"]
    components = []
    for numbers, note in ((list(range(392, 472)), "fresh Medium90 run · earlier80 subset"),
                          (SELECTED, "fresh Medium90 run · selected wave21 subset")):
        subset = [r for r in miniswe["outcomes"] if r["number"] in numbers]
        usage = {"sources_sha256": [miniswe["source_digest"]], "notes": [
            "One fresh90 cohort; first retained clean attempt per task, including failures. Superseded transport attempts excluded.",
            "Tokens are captured API input + output usage; cached input and reasoning output are counted once."]}
        for metric, field in (("steps", "agent_steps"), ("tokens", "agent_total_tokens")):
            values = [r[field] for r in subset if r[field] is not None]
            covered = len(values) if metric == "steps" else sum(r["token_usage_present"] for r in subset)
            partial = covered != len(subset) or (metric == "tokens" and not all(r["token_usage_complete"] for r in subset))
            usage[metric] = {"value": sum(values), "covered_tasks": covered,
                             "status": "unavailable" if not values else "partial" if partial else "complete", "lower_bound": partial}
        usage["steps"]["unit"] = "native_steps"
        usage["tokens"]["unmetered_requests"] = sum(r["unmetered_requests"] for r in subset)
        usage["tokens"]["completed_responses"] = sum(r["completed_responses"] for r in subset)
        usage["tokens"]["input_tokens"] = sum(r["input_tokens"] or 0 for r in subset)
        usage["tokens"]["output_tokens"] = sum(r["output_tokens"] or 0 for r in subset)
        components.append(component(numbers, {"total": len(subset), "passed": sum(r["passed"] for r in subset)},
                                    usage, miniswe["task_set_version"], note))
    identity = {"id": "miniswe-deepseek-medium90-20261027", "profile_id": "miniswe-deepseek-medium90-20261027",
                "model": miniswe["model"], "harness": miniswe["harness"], "runs": 1,
                "source_version": miniswe["task_set_version"], "reasoning_effort": "high",
                "measurement_type": "fresh_medium90", "measurement_valid": True,
                "details_url": "miniswe-deepseek-medium90.html"}
    rows.append(assemble_row(identity, components))
    assert len({r["id"] for r in rows}) == len(rows)
    complete = sorted((r for r in rows if r["score"] is not None), key=lambda r: -r["score"])
    for r in complete:
        r["rank"] = 1 + next(i for i, other in enumerate(complete) if other["score"] == r["score"])
    def fingerprint(path):
        return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    return {"schema_version": 1, "suite": "medium", "task_set_version": "0.21.3", "task_count": 90,
            "task_numbers": NUMBERS, "frozen_light_tasks": 391,
            "policy": {"combination": "Only disjoint components within the same published measured profile; display-name matches are not profile equivalence.",
                       "missing": "Unmeasured, never a failure. Partial rows have no overall score or rank.",
                       "protocols": "Historical source versions and per-wave settings retained; these are reused measurements, not new complete runs. A wave21 component taken from a fresh run carries its own source version and profile note.",
                       "incompatible_pairs": "Historical DeepAgents GLM/DeepSeek enabled native subagents; new wave21 disabled them. Keep separate profiles. 80/160 graph steps and model API request caps are distinct.",
                       "controls": "Forty wave20 controls and ten unselected calibration tasks excluded from Medium90.",
                       "new_model_calls": 0},
            "provenance": {"legacy_results_sha256": fingerprint(legacy_path), "wave21_results_sha256": fingerprint(wave_path), "haiku_medium90_sha256": fingerprint(fresh_path), "codex_medium90_sha256": fingerprint(codex90_path), "miniswe_medium90_sha256": fingerprint(miniswe90_path)},
            "summary": {"rows": len(rows), "ranked_rows": len(complete),
                        "coverage": {str(n): sum(r["measured_tasks"] == n for r in rows) for n in sorted({r["measured_tasks"] for r in rows})}},
            "rows": rows}


if __name__ == "__main__":
    target = ROOT / "docs/medium90-results.json"
    target.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)
