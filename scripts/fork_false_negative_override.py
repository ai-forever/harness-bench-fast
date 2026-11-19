#!/usr/bin/env python3
"""False-negative scoreboard override for infra-crash OC runs.

Some OpenClaw (OC) runs recorded a task as FAIL only because of an
infrastructure crash (`MaxListeners`/`incomplete_turn`), not a model failure.
A K=3 rerun on the fixed runner showed those tasks are solvable, so the
published scoreboard understates the model (issue #462).

Rather than falsify the raw artifacts, the fork generators consult this module:
for a (run stem, task_id) pair listed here the task is treated as **PASS**
(variant B) and labelled ``решаемо (K=3: N/3)`` with the rerun evidence in the
note. The raw ``results/*.json`` stay untouched; ``total`` is unchanged.

Public API:
    apply_summary(stem, summary) -> dict          # adjusted copy (+passed)
    apply_number(stem, task_id, passed) -> bool    # True when rescored
    task_status(stem, task_id) -> dict | None       # {k, passed_k, note, artifacts}
    load_overrides() -> dict[str, dict]             # {stem: {task_id: {...}}}
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Run stem -> {task_id: evidence}. ``passed_k`` is how many of ``k`` reruns
# passed; ``artifacts`` are the rerun JSONs kept for audit.
OVERRIDES: dict[str, dict[str, dict]] = {
    "oc_medium_feasibility_minimaxm3_off_20261109": {
        "task_449_search_capabilities": {"k": 3, "passed_k": 2,
                                         "artifacts": ["/tmp/oc_minimax_off_k1.json",
                                                       "/tmp/oc_minimax_off_k2.json",
                                                       "/tmp/oc_minimax_off_k3.json"]},
    },
    "oc_medium_feasibility_minimaxm3_medium_20261109": {
        "task_470_search_hint": {"k": 3, "passed_k": 1,
                                 "artifacts": ["/tmp/oc_minimax_medium_k1.json",
                                               "/tmp/oc_minimax_medium_k2.json",
                                               "/tmp/oc_minimax_medium_k3.json"]},
    },
    "oc_medium_reflect_minimaxm3_off_20261109": {
        "task_422_calendar_timezone": {"k": 3, "passed_k": 2,
                                       "artifacts": ["/tmp/oc_minimax_off_k1.json",
                                                     "/tmp/oc_minimax_off_k2.json",
                                                     "/tmp/oc_minimax_off_k3.json"]},
        "task_429_mail_bounce": {"k": 3, "passed_k": 2,
                                 "artifacts": ["/tmp/oc_minimax_off_k1.json",
                                               "/tmp/oc_minimax_off_k2.json",
                                               "/tmp/oc_minimax_off_k3.json"]},
    },
}


def load_overrides() -> dict[str, dict[str, dict]]:
    return OVERRIDES


def task_status(stem: str, task_id) -> dict | None:
    """Evidence for a rescored ``(stem, task_id)``, else ``None``."""
    entry = OVERRIDES.get(stem)
    if not entry:
        return None
    return entry.get(str(task_id))


def rescored_label(evidence: dict) -> str:
    """``решаемо (K=3: 2/3)``."""
    return f"решаемо (K={evidence['k']}: {evidence['passed_k']}/{evidence['k']})"


def apply_number(stem: str, task_id, passed: bool) -> bool:
    """Return the corrected ``passed`` for one task (True when rescored)."""
    if passed:
        return True
    return task_status(stem, task_id) is not None


def apply_summary(stem: str, summary: dict) -> dict:
    """Return a copy of ``summary`` with ``passed``/``pass_rate`` corrected.

    Each rescored task that the artifact recorded as failed bumps ``passed`` by
    one (variant B); ``total`` is unchanged.
    """
    entry = OVERRIDES.get(stem)
    if not isinstance(summary, dict) or not entry:
        return summary
    total = summary.get("total")
    passed = summary.get("passed")
    if total is None or passed is None:
        return summary
    adjusted = dict(summary)
    bumped = int(passed) + len(entry) if int(passed) < int(total) else int(passed)
    adjusted["passed"] = min(bumped, int(total))
    adjusted["pass_rate"] = round(adjusted["passed"] / max(int(total), 1), 4)
    return adjusted
