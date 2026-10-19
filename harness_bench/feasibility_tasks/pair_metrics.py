"""Strict wave-19 pair scores, with both halves matched within a repeat.

Pair membership comes from the registry, never from task numbers or tags.
The denominator includes every registered pair, even in partial runs. Repeated
runs use ``attempt``; legacy records without it use per-task occurrence order.
This is not pass@k: successes from different attempts cannot complete a pair.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


def _field(record: Any, name: str, default: Any = None) -> Any:
    return (
        record.get(name, default) if isinstance(record, Mapping) else getattr(record, name, default)
    )


def _positive_int(value: Any) -> bool:
    return type(value) is int and 0 < value <= 10_000


def compute_pair_metrics(
    results: Iterable[Any], pairs: Mapping[str, tuple[str, str]] | None = None
) -> dict[str, Any] | None:
    """Return JSON-ready scores, or None when no registered task is present.

    ``passed`` must be a literal boolean. Infrastructure errors, duplicate
    explicit attempts and malformed records invalidate the score (None).
    Counts remain diagnostic; invalid records can never pass a pair. Missing
    halves count against the full denominator and are reported as incomplete.
    Each cohort is one attempt index; its expected size is the full registry.
    """
    if pairs is None:
        from harness_bench.feasibility_tasks import FEASIBILITY_PAIRS

        pairs = FEASIBILITY_PAIRS
    task_ids: set[str] = set()
    for pair_id, halves in pairs.items():
        if (
            not isinstance(pair_id, str)
            or not pair_id
            or not isinstance(halves, (tuple, list))
            or len(halves) != 2
            or any(not isinstance(task_id, str) or not task_id for task_id in halves)
            or len(set(halves)) != 2
            or task_ids.intersection(halves)
        ):
            raise ValueError("pair registry must contain disjoint pairs of two task IDs")
        task_ids.update(halves)
    relevant = [
        r
        for r in results
        if isinstance(_field(r, "task_id"), str) and _field(r, "task_id") in task_ids
    ]
    if not relevant:
        return None

    missing = object()
    explicit = [_field(r, "attempt", missing) is not missing for r in relevant]
    mixed = any(explicit) and not all(explicit)
    pairing = "attempt" if any(explicit) else "per_task_occurrence_order"
    grouped: dict[tuple[int, str], list[Any]] = defaultdict(list)
    occurrences: dict[str, int] = defaultdict(int)
    malformed = 0
    repeat_count = 1
    for record in relevant:
        task_id = _field(record, "task_id")
        occurrences[task_id] += 1
        attempt = _field(record, "attempt", occurrences[task_id])
        expected = _field(record, "attempts", attempt)
        if not _positive_int(attempt) or not _positive_int(expected):
            malformed += 1
            continue
        repeat_count = max(repeat_count, attempt, expected)
        grouped[attempt, task_id].append(record)

    cohorts = []
    for attempt in range(1, repeat_count + 1):
        details = []
        for pair_id, halves in pairs.items():
            records = [grouped.get((attempt, task_id), []) for task_id in halves]
            complete = all(records)
            invalid = mixed or any(
                len(items) > 1
                or any(
                    type(_field(r, "passed")) is not bool
                    or _field(r, "failure_kind") == "infrastructure"
                    or _field(r, "measurement_valid", True) is not True
                    for r in items
                )
                for items in records
            )
            passed = (
                complete
                and not invalid
                and all(
                    _field(items[0], "passed") is True
                    and not _field(items[0], "failure_kind")
                    and not _field(items[0], "error")
                    for items in records
                )
            )
            details.append(
                {
                    "pair_id": pair_id,
                    "task_ids": list(halves),
                    "passed": passed,
                    "complete": complete,
                    "measurement_valid": not invalid,
                }
            )
        passed_count = sum(d["passed"] for d in details)
        completed = sum(d["complete"] for d in details)
        valid = not malformed and all(d["measurement_valid"] for d in details)
        cohorts.append(
            {
                "attempt": attempt,
                "passed_pairs": passed_count,
                "total_pairs": len(pairs),
                "completed_pairs": completed,
                "incomplete_pairs": len(pairs) - completed,
                "invalid_pairs": sum(not d["measurement_valid"] for d in details),
                "measurement_valid": valid,
                "score": passed_count / len(pairs) if valid else None,
                "pairs": details,
            }
        )
    valid = all(c["measurement_valid"] for c in cohorts)
    totals = {
        key: sum(c[key] for c in cohorts)
        for key in (
            "passed_pairs",
            "total_pairs",
            "completed_pairs",
            "incomplete_pairs",
            "invalid_pairs",
        )
    }
    return {
        "metric": "both_halves_pass_same_attempt",
        "pairing": pairing,
        "pairs_per_attempt": len(pairs),
        "repeat_count": repeat_count,
        **totals,
        "measurement_valid": valid,
        "score": totals["passed_pairs"] / totals["total_pairs"] if valid else None,
        "malformed_records": malformed,
        "cohorts": cohorts,
    }
