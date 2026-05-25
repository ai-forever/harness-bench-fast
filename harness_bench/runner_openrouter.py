"""Run benchmark tasks against a deep agent backed by an OpenRouter model.

This runner deliberately does NOT register the `deepagents-gigachat` harness
profile — the goal is to measure `deepagents` with a third-party model
(via OpenRouter's OpenAI-compatible API) without any GigaChat-specific prompt
or tool-description overrides.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from harness_bench.core import Task
from harness_bench.runner import (
    TaskRun,
    _attempt_suffix,
    _load_env_from_dotenv,
    _mark_attempt,
    _one_line_detail,
    _task_attempt_label,
    _task_sort_key,
)
from harness_bench.tasks import ALL_TASKS, get_task

DEFAULT_OPENROUTER_MODEL = "qwen/qwen3.6-plus"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def _ensure_openrouter_key() -> None:
    if not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit(
            "OPENROUTER_API_KEY is not set. Put it in .env or export it before running."
        )


def build_agent(
    workspace: Path,
    *,
    model_name: str = DEFAULT_OPENROUTER_MODEL,
    recursion_limit: int = 80,
    max_tokens: int | None = None,
) -> Any:
    """Build a stock `deepagents` agent backed by an OpenRouter model.

    No `register_harness()` call — the GigaChat-specific prompt / overrides are
    intentionally bypassed so we measure `deepagents` defaults against the
    chosen model.
    """
    from deepagents import create_deep_agent
    from deepagents.backends import LocalShellBackend
    from langchain_openai import ChatOpenAI

    backend = LocalShellBackend(
        root_dir=workspace,
        virtual_mode=True,
        inherit_env=True,
    )
    model_kwargs: dict[str, Any] = {}
    if max_tokens is not None:
        model_kwargs["max_tokens"] = max_tokens
    model = ChatOpenAI(
        model=model_name,
        base_url=os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        timeout=600,
        **model_kwargs,
    )
    # Memory tasks (222–231) ship an AGENTS.md fixture; pre-existing 221
    # tasks do not. `LocalShellBackend(virtual_mode=True)` maps
    # `/AGENTS.md` to `<workspace>/AGENTS.md`.
    memory_sources = ["/AGENTS.md"] if (workspace / "AGENTS.md").exists() else None
    agent = create_deep_agent(model=model, backend=backend, memory=memory_sources)
    return agent.with_config({"recursion_limit": recursion_limit})


def run_task(
    task: Task,
    *,
    model_name: str = DEFAULT_OPENROUTER_MODEL,
    keep_workspace: bool = False,
    recursion_limit: int = 80,
    max_tokens: int | None = None,
) -> TaskRun:
    workspace_keepalive: TemporaryDirectory | None = None
    try:
        if keep_workspace:
            workspace_path = Path(__import__("tempfile").mkdtemp(prefix=f"hb_or_{task.id}_"))
        else:
            workspace_keepalive = TemporaryDirectory(prefix=f"hb_or_{task.id}_")
            workspace_path = Path(workspace_keepalive.name)

        task.setup(workspace_path)
        started = time.monotonic()
        try:
            agent = build_agent(
                workspace_path,
                model_name=model_name,
                recursion_limit=recursion_limit,
                max_tokens=max_tokens,
            )
            agent.invoke({"messages": [{"role": "user", "content": task.prompt}]})
        except Exception:  # noqa: BLE001 — surface as failure
            return TaskRun(
                task_id=task.id,
                passed=False,
                message="",
                elapsed_seconds=time.monotonic() - started,
                error=traceback.format_exc(),
                workspace=workspace_path if keep_workspace else None,
            )
        result = task.verify(workspace_path)
        return TaskRun(
            task_id=task.id,
            passed=result.passed,
            message=result.message,
            elapsed_seconds=time.monotonic() - started,
            workspace=workspace_path if keep_workspace else None,
        )
    finally:
        if workspace_keepalive is not None:
            workspace_keepalive.cleanup()


def run_all(
    task_ids: list[str] | None = None,
    *,
    model_name: str = DEFAULT_OPENROUTER_MODEL,
    keep_workspace: bool = False,
    recursion_limit: int = 80,
    max_tokens: int | None = None,
    concurrency: int = 1,
    attempts: int = 1,
) -> list[TaskRun]:
    _load_env_from_dotenv()
    _ensure_openrouter_key()

    if attempts < 1:
        raise ValueError("attempts must be positive")

    targets = [get_task(tid) for tid in task_ids] if task_ids else list(ALL_TASKS)

    if concurrency <= 1:
        results: list[TaskRun] = []
        for task in targets:
            for attempt in range(1, attempts + 1):
                print(f"→ {task.id}: {task.name}{_attempt_suffix(attempt, attempts)}")
                run = run_task(
                    task,
                    model_name=model_name,
                    keep_workspace=keep_workspace,
                    recursion_limit=recursion_limit,
                    max_tokens=max_tokens,
                )
                run = _mark_attempt(run, attempt, attempts)
                results.append(run)
                status = "PASS" if run.passed else "FAIL"
                print(f"  [{status}] {run.elapsed_seconds:5.1f}s — {_one_line_detail(run)}")
                if keep_workspace and run.workspace:
                    print(f"  workspace: {run.workspace}")
        return results

    print_lock = threading.Lock()
    completed = 0
    total = len(targets) * attempts
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_task = {
            executor.submit(
                run_task,
                task,
                model_name=model_name,
                keep_workspace=keep_workspace,
                recursion_limit=recursion_limit,
                max_tokens=max_tokens,
            ): (task, attempt)
            for task in targets
            for attempt in range(1, attempts + 1)
        }
        for future in as_completed(future_to_task):
            _task, attempt = future_to_task[future]
            run = _mark_attempt(future.result(), attempt, attempts)
            results.append(run)
            with print_lock:
                completed += 1
                status = "PASS" if run.passed else "FAIL"
                print(
                    f"[{completed:3d}/{total}] [{status}] "
                    f"{_task_attempt_label(run):40s} "
                    f"{run.elapsed_seconds:5.1f}s — {_one_line_detail(run)}"
                )
                if keep_workspace and run.workspace:
                    print(f"           workspace: {run.workspace}")
    results.sort(key=lambda r: (*_task_sort_key(r.task_id), r.attempt))
    return results
