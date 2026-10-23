"""Offline Light/Medium reports; never modify or rerun the source measurement.

python -m harness_bench.suite_reports results.json --output-dir derived
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from harness_bench.runner import results_to_payload, task_run_from_payload
from harness_bench.tasks import SUITES, get_task
from harness_bench.versioning import TASK_SET_VERSION, task_number


def _wilson(passed: int, total: int) -> list[float]:
    """Descriptive Bernoulli interval, not a confidence interval over run repeats."""
    z = 1.959963984540054
    p = passed / total
    scale = 1 + z * z / total
    mid = (p + z * z / (2 * total)) / scale
    width = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / scale
    return [mid - width, mid + width]


def split_reports(payload: dict, *, source_sha256: str, source_path: str) -> dict:
    if not isinstance(payload.get("tasks"), list) or not payload.get("task_set_version"):
        raise ValueError("source must contain tasks and task_set_version")
    entries = {}
    for item in payload["tasks"]:
        task = get_task(item["task_id"])
        number = task_number(task.id)
        if item.get("number") not in (None, number):
            raise ValueError("task number disagrees with task ID")
        if type(item.get("passed")) is not bool:
            raise ValueError("passed must be a boolean")
        attempt = item.get("attempt", 1)
        attempts = item.get("attempts", 1)
        if type(attempt) is not int or type(attempts) is not int or not 1 <= attempt <= attempts:
            raise ValueError("invalid attempt metadata")
        key = (task.id, attempt)
        if key in entries:
            raise ValueError("duplicate task/attempt; retries must be resolved in the source")
        entries[key] = {**item, "task_id": task.id, "number": number}
    attempt_counts = {item.get("attempts", 1) for item in entries.values()}
    if len(attempt_counts) > 1:
        raise ValueError("inconsistent attempts count in source")
    attempt_count = next(iter(attempt_counts), 1)
    known_infrastructure = any(item.get("failure_kind") == "infrastructure" or item.get("rerun_on_continue", False) for item in entries.values())
    unexplained_invalidity = payload.get("measurement_valid") is False and not known_infrastructure
    source_validity_known = type(payload.get("measurement_valid")) is bool
    reports = {}
    for name in ("light", "medium"):
        expected = {t.id for t in SUITES[name]}
        selected = [item for (task_id, _), item in entries.items() if task_id in expected]
        runs = [task_run_from_payload(item) for item in selected]
        summary = results_to_payload(runs, command=payload.get("command"))
        summary["task_set_version"] = payload["task_set_version"]
        attempts = []
        for index in range(1, attempt_count + 1):
            group = [item for item in selected if item.get("attempt", 1) == index]
            observed = {item["task_id"] for item in group}
            infrastructure = sum(item.get("failure_kind") == "infrastructure" or item.get("rerun_on_continue", False) for item in group)
            complete = observed == expected
            passed = sum(item["passed"] for item in group)
            attempts.append({
                "attempt": index, "observed_tasks": len(observed), "passed": passed,
                "missing_task_ids": sorted(expected - observed),
                "infrastructure_failures": infrastructure,
                "complete": complete,
                "score": passed / len(expected) if complete and not infrastructure and not unexplained_invalidity and source_validity_known else None,
                "wilson95": _wilson(passed, len(expected)) if complete and not infrastructure and not unexplained_invalidity and source_validity_known else None,
            })
        complete = all(a["complete"] for a in attempts)
        valid = not unexplained_invalidity and not any(a["infrastructure_failures"] for a in attempts)
        if valid and not source_validity_known:
            valid = None
        summary.update(
            suite=name, suite_definition_version=TASK_SET_VERSION,
            expected_tasks=len(expected), expected_attempts=attempt_count,
            measured_tasks=len({item["task_id"] for item in selected}),
            complete=complete, measurement_valid=valid,
            infrastructure_failures=sum(a["infrastructure_failures"] for a in attempts),
            status=("infrastructure_invalid" if valid is False else "validity_unverified" if valid is None
                    else "complete" if complete else "partial"),
            # Native `pass_rate` only uses observed entries; suppress it on incomplete reports.
            pass_rate=summary["pass_rate"] if complete and valid else None,
            attempts=attempts,
            provenance={"source_path": source_path, "source_sha256": source_sha256,
                        "source_task_set_version": payload["task_set_version"],
                        "source_measurement_valid": payload.get("measurement_valid"),
                        "source_run_status": payload.get("run_status"),
                        "method": "disjoint offline rescore; no new model calls"},
            limitations=[
                "Raw steps/tokens retain source accounting defects; coverage is reported separately.",
                "Wilson95 describes a Bernoulli task model; fixed heterogeneous tasks are not an IID sample. It does not estimate repeat variability.",
                "Mean over attempts is not pass@k; no attempt or task is silently dropped.",
                "Scores retain source task definitions; suite membership does not upgrade an old protocol.",
            ],
        )
        # Preserve the original task records verbatim apart from canonical aliases.
        summary["tasks"] = selected
        if pairs := summary.get("feasibility_pairs"):
            # The native pair metric uses the full denominator even in partial
            # checkpoints. Derived reports expose counts until every pair is
            # observed, just as they withhold the main suite's partial score.
            for metric in [pairs, *pairs["cohorts"]]:
                metric["complete"] = metric["incomplete_pairs"] == 0
                if metric["measurement_valid"] and valid is not True:
                    metric["measurement_valid"] = valid
                metric["status"] = (
                    "infrastructure_invalid" if metric["measurement_valid"] is False
                    else "validity_unverified" if metric["measurement_valid"] is None
                    else "complete" if metric["complete"] else "partial"
                )
                if metric["status"] != "complete":
                    metric["score"] = None
        reports[name] = summary
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    reports = split_reports(json.loads(raw), source_sha256=digest, source_path=str(source))
    snapshot = args.output_dir / ("source-" + digest + ".json")
    targets = [args.output_dir / (name + ".json") for name in reports]
    if source in [path.resolve() for path in [snapshot, *targets]]:
        raise ValueError("output must not overwrite source")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # A running checkpoint may change after this read; retain the exact scored bytes.
    snapshot.write_bytes(raw)
    for name, report in reports.items():
        target = args.output_dir / (name + ".json")
        report["provenance"]["source_snapshot"] = str(snapshot.resolve())
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"{name}: {report['status']}, {report['measured_tasks']}/{report['expected_tasks']} tasks → {target}")


if __name__ == "__main__":
    main()
