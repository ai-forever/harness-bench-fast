"""Run benchmark tasks against stock deepagents + GigaChat (no harness profile)."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from harness_bench.core import Task
from harness_bench.runner import (
    TaskRun,
    _agent_exception_task_run,
    _load_env_from_dotenv,
    _one_line_detail,
    _task_sort_key,
)
from harness_bench.tasks import ALL_TASKS, get_task


def _ensure_credentials() -> None:
    if os.getenv("GIGACHAT_CREDENTIALS"):
        return
    if os.getenv("GIGACHAT_USER") and os.getenv("GIGACHAT_PASSWORD"):
        return
    raise SystemExit(
        "GigaChat credentials are not configured. "
        "Set GIGACHAT_CREDENTIALS or both GIGACHAT_USER and GIGACHAT_PASSWORD."
    )


def build_agent(workspace: Path, *, recursion_limit: int = 80) -> Any:
    """Build stock deepagents agent without the deepagents-gigachat profile.

    `deepagents` discovers harness profiles through entry points. If
    `deepagents-gigachat` is installed, a normal `GigaChat` instance resolves
    provider `"gigachat"` and receives that profile automatically. A tiny
    subclass changes only LangSmith/provider metadata, which is enough for the
    profile lookup to miss while preserving the same API client behavior.
    """
    from deepagents import create_deep_agent
    from deepagents.backends import LocalShellBackend
    from langchain_gigachat import GigaChat

    class ProfilelessGigaChat(GigaChat):
        """GigaChat client whose provider metadata does not match profile keys."""

    backend = LocalShellBackend(
        root_dir=workspace,
        virtual_mode=True,
        inherit_env=True,
    )
    model = ProfilelessGigaChat(
        model=os.getenv("GIGACHAT_MODEL", "GigaChat-3-Ultra"),
        # Let gigachat-sdk use its current default base URL unless the caller
        # explicitly overrides it. Hard-coding the old IFT URL here breaks
        # CORP/PERS credentials that expect the SDK default endpoint.
        base_url=os.getenv("GIGACHAT_BASE_URL") or None,
        verify_ssl_certs=os.getenv("GIGACHAT_VERIFY_SSL_CERTS", "false").lower()
        not in ("false", "0", "no"),
        profanity_check=False,
        timeout=600,
        max_retries=20,
        retry_backoff_factor=1.0,
        retry_on_status_codes=(403, 429, 500, 502, 503, 504),
    )
    agent = create_deep_agent(model=model, backend=backend)
    return agent.with_config({"recursion_limit": recursion_limit})


def run_task(
    task: Task,
    *,
    keep_workspace: bool = False,
    recursion_limit: int = 80,
) -> TaskRun:
    workspace_keepalive: TemporaryDirectory | None = None
    try:
        if keep_workspace:
            workspace_path = Path(
                __import__("tempfile").mkdtemp(prefix=f"hb_pure_{task.id}_")
            )
        else:
            workspace_keepalive = TemporaryDirectory(prefix=f"hb_pure_{task.id}_")
            workspace_path = Path(workspace_keepalive.name)

        task.setup(workspace_path)
        started = time.monotonic()
        try:
            agent = build_agent(workspace_path, recursion_limit=recursion_limit)
            agent.invoke({"messages": [{"role": "user", "content": task.prompt}]})
        except Exception as exc:  # noqa: BLE001 — log and surface as task failure
            return _agent_exception_task_run(
                exc,
                task_id=task.id,
                elapsed_seconds=time.monotonic() - started,
                recursion_limit=recursion_limit,
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
    keep_workspace: bool = False,
    recursion_limit: int = 80,
    concurrency: int = 1,
) -> list[TaskRun]:
    _load_env_from_dotenv()
    _ensure_credentials()

    targets = [get_task(tid) for tid in task_ids] if task_ids else list(ALL_TASKS)

    if concurrency <= 1:
        results: list[TaskRun] = []
        for task in targets:
            print(f"[START] {task.id}: {task.name}")
            run = run_task(task, keep_workspace=keep_workspace, recursion_limit=recursion_limit)
            results.append(run)
            status = "PASS" if run.passed else "FAIL"
            print(f"  [{status}] {run.elapsed_seconds:5.1f}s — {_one_line_detail(run)}")
            if keep_workspace and run.workspace:
                print(f"  workspace: {run.workspace}")
        return results

    print_lock = threading.Lock()
    completed = 0
    total = len(targets)
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_task = {
            executor.submit(
                run_task,
                task,
                keep_workspace=keep_workspace,
                recursion_limit=recursion_limit,
            ): task
            for task in targets
        }
        for future in as_completed(future_to_task):
            run = future.result()
            results.append(run)
            with print_lock:
                completed += 1
                status = "PASS" if run.passed else "FAIL"
                print(
                    f"[{completed:3d}/{total}] [{status}] {run.task_id:32s} "
                    f"{run.elapsed_seconds:5.1f}s — {_one_line_detail(run)}"
                )
                if keep_workspace and run.workspace:
                    print(f"           workspace: {run.workspace}")
    results.sort(key=lambda r: _task_sort_key(r.task_id))
    return results
