"""Runtime limits shared with isolated workers; contains no task data."""

from __future__ import annotations

import sys

DEFAULT_TASK_TIMEOUT_SECONDS = 3600
"""Sanitary wall-clock guard against a stuck task, in seconds."""


def effective_recursion_limit(configured: int, minimum: int | None = None) -> int:
    """Resolve an optional step cap; zero means no benchmark-imposed cap.

    A task's floor only raises an explicitly finite cap. It must not turn an
    unlimited run into a capped run.
    """
    for name, value in (("recursion_limit", configured), ("min_recursion_limit", minimum)):
        if value is not None and (not isinstance(value, int) or value < 0):
            raise ValueError(f"{name} must be a nonnegative integer (0 means unlimited)")
    return max(configured, minimum or 0) if configured else 0


def langgraph_recursion_limit(configured: int) -> int:
    """Translate our unlimited sentinel to the positive integer LangGraph needs."""
    return effective_recursion_limit(configured) or sys.maxsize
