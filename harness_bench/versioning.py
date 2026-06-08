"""Benchmark task-set version metadata.

Package releases and benchmark task-set revisions are intentionally separate:
the package can receive runner or documentation fixes without changing the
task set, while every added/removed/changed task should bump
``TASK_SET_VERSION`` and append a ``TaskSetRevision`` entry.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskSetRevision:
    """A historical benchmark task-set revision."""

    version: str
    introduced: str
    total_tasks: int
    added_task_numbers: tuple[int, int]
    modules: tuple[str, ...]
    notes: str

    @property
    def added_range(self) -> str:
        """Human-readable inclusive range of task numbers added in this revision."""
        start, end = self.added_task_numbers
        return f"{start}" if start == end else f"{start}-{end}"


TASK_SET_REVISIONS: tuple[TaskSetRevision, ...] = (
    TaskSetRevision(
        version="0.1.0",
        introduced="2026-05-13",
        total_tasks=200,
        added_task_numbers=(1, 200),
        modules=(
            "tasks.py",
            "tasks_extra.py",
            "tasks_more.py",
            "tasks_hard.py",
            "tasks_extreme.py",
        ),
        notes="Initial extracted file/code/data benchmark.",
    ),
    TaskSetRevision(
        version="0.2.0",
        introduced="2026-05-19",
        total_tasks=221,
        added_task_numbers=(201, 221),
        modules=("tasks_extreme.py", "tasks_diagnostic.py"),
        notes="Added advanced composites and diagnostic hard tasks.",
    ),
    TaskSetRevision(
        version="0.3.0",
        introduced="2026-05-21",
        total_tasks=231,
        added_task_numbers=(222, 231),
        modules=("tasks_memory.py",),
        notes="Added memory-discipline tasks using AGENTS.md and MEMORY.md.",
    ),
    TaskSetRevision(
        version="0.4.0",
        introduced="2026-06-02",
        total_tasks=253,
        added_task_numbers=(232, 253),
        modules=("tasks_memory.py",),
        notes=(
            "Extended the memory suite: knowledge update / contradiction "
            "resolution, temporal reasoning, abstention, preference-following, "
            "multi-hop and multi-session reasoning, information extraction."
        ),
    ),
    TaskSetRevision(
        version="0.5.0",
        introduced="2026-06-02",
        total_tasks=262,
        added_task_numbers=(254, 262),
        modules=("tasks_agentic.py",),
        notes=(
            "Added synthetic Terminal-Bench-like, tau-like, and "
            "SWE-bench-like agentic tasks."
        ),
    ),
    TaskSetRevision(
        version="0.6.0",
        introduced="2026-06-02",
        total_tasks=283,
        added_task_numbers=(263, 283),
        modules=("tasks_agentic.py",),
        notes=(
            "Expanded the agentic wave to 10 Terminal-Bench-like, "
            "10 tau-like, and 10 SWE-bench-like tasks."
        ),
    ),
    TaskSetRevision(
        version="0.7.0",
        introduced="2026-06-02",
        total_tasks=298,
        added_task_numbers=(284, 298),
        modules=("tasks_agentic.py",),
        notes=(
            "Expanded the agentic wave to 15 Terminal-Bench-like, "
            "15 tau-like, and 15 SWE-bench-like tasks."
        ),
    ),
    TaskSetRevision(
        version="0.8.0",
        introduced="2026-06-05",
        total_tasks=308,
        added_task_numbers=(299, 308),
        modules=("tasks_vcs.py",),
        notes=(
            "Added version-control tasks: Git merge-conflict resolution "
            "(ours/theirs/both/manual, diff3 base sections, multi-hunk, "
            "multi-file), multi-hunk unified-diff apply/revert, and "
            "unresolved-conflict detection with false-positive traps. Strict "
            "exact-content verifiers catch dropped markers and stray "
            "special characters."
        ),
    ),
    TaskSetRevision(
        version="0.9.0",
        introduced="2026-06-05",
        total_tasks=313,
        added_task_numbers=(309, 313),
        modules=("tasks_vcs.py",),
        notes=(
            "Added five multi-file / multi-step version-control workflows that "
            "stress step-budget and cross-file propagation: scale rename "
            "refactor across twelve conflicted call sites, module split into a "
            "package, an ordered nine-patch stack across four files plus a "
            "synthesised summary, policy-manifest-driven resolution of sixteen "
            "modules, and precedence deep-merge of five config fragments. All "
            "frontier-solvable (Opus 4.8 and GPT-5.5 pass); gold verified."
        ),
    ),
)

CURRENT_TASK_SET_REVISION = TASK_SET_REVISIONS[-1]
TASK_SET_VERSION = CURRENT_TASK_SET_REVISION.version
EXPECTED_TASK_COUNT = CURRENT_TASK_SET_REVISION.total_tasks


def task_number(task_id: str) -> int | None:
    """Extract the numeric component from ids like ``task_042_name``."""
    rest = task_id.removeprefix("task_")
    head, _, _tail = rest.partition("_")
    try:
        return int(head)
    except ValueError:
        return None


def revision_for_task_id(task_id: str) -> TaskSetRevision | None:
    """Return the revision that introduced ``task_id``."""
    number = task_number(task_id)
    if number is None:
        return None
    for revision in TASK_SET_REVISIONS:
        start, end = revision.added_task_numbers
        if start <= number <= end:
            return revision
    return None


def validate_task_set_metadata(tasks: Iterable[Any]) -> list[str]:
    """Check that task registry shape matches the current version metadata."""
    task_list = list(tasks)
    errors: list[str] = []
    ids = [getattr(task, "id", "") for task in task_list]
    numbers = [task_number(task_id) for task_id in ids]

    if len(task_list) != EXPECTED_TASK_COUNT:
        errors.append(
            f"task count is {len(task_list)}, but version metadata expects "
            f"{EXPECTED_TASK_COUNT}"
        )
    if len(set(ids)) != len(ids):
        errors.append("task ids are not unique")
    if any(number is None for number in numbers):
        bad = [task_id for task_id, number in zip(ids, numbers, strict=True) if number is None]
        errors.append(f"task ids without numeric component: {bad!r}")
    else:
        expected_numbers = list(range(1, EXPECTED_TASK_COUNT + 1))
        if numbers != expected_numbers:
            errors.append(
                f"task numbers are not continuous 1..{EXPECTED_TASK_COUNT}: "
                f"got first={numbers[:5]!r}, last={numbers[-5:]!r}"
            )
    return errors
