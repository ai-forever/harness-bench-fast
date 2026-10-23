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


def prompt_with_execution_budget(
    prompt: str, *, recursion_limit: int, max_tokens: int | None = None,
) -> str:
    """Disclose effective runtime caps, including an explicit unlimited override."""
    effective_recursion_limit(recursion_limit)
    if recursion_limit:
        notice = (
            f"This run has a hard limit of {recursion_limit} graph supersteps. "
            "Model, tool and middleware nodes consume this budget; it is NOT "
            f"{recursion_limit} model calls or tool calls. The run fails if the limit "
            "is reached before termination. Finish with a final answer before "
            "exhausting the budget. Do not attempt to change or bypass the limit."
        )
    else:
        notice = "This run has no benchmark-imposed graph-step cap."
    if max_tokens is not None:
        notice += f" Each model response is capped at {max_tokens} output tokens."
    return prompt + "\n\n[Effective execution budget]\n" + notice
