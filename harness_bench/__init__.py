"""Simple file-operation benchmark for the GigaChat harness profile.

Public entry points:

- `ALL_TASKS`: ordered list of `Task` instances.
- `get_task`: look up a task by id.
- `run_task`, `run_all`: execute the benchmark.
- `Task`, `VerifyResult`: building blocks for new tasks.
"""

from __future__ import annotations

from harness_bench.core import Task, VerifyResult
from harness_bench.runner import TaskRun, run_all, run_task, summarize
from harness_bench.tasks import ALL_TASKS, get_task
from harness_bench.versioning import (
    CURRENT_TASK_SET_REVISION,
    TASK_SET_REVISIONS,
    TASK_SET_VERSION,
)

__all__ = [
    "ALL_TASKS",
    "CURRENT_TASK_SET_REVISION",
    "Task",
    "TaskRun",
    "TASK_SET_REVISIONS",
    "TASK_SET_VERSION",
    "VerifyResult",
    "get_task",
    "run_all",
    "run_task",
    "summarize",
]
