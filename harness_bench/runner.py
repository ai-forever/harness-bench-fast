"""Run a single task or the whole benchmark against a GigaChat-powered deep agent."""

from __future__ import annotations

import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from harness_bench.core import Task, VerifyResult
from harness_bench.metrics import PassMetric, compute_pass_metrics
from harness_bench.tasks import ALL_TASKS, get_task


def _is_graph_recursion_error(exc: BaseException) -> bool:
    """Return True when ``exc`` is LangGraph's recursion-limit failure."""
    try:
        from langgraph.errors import GraphRecursionError
    except ImportError:  # pragma: no cover - langgraph is an indirect runtime dep.
        return exc.__class__.__name__ == "GraphRecursionError"
    return isinstance(exc, GraphRecursionError)


def _agent_exception_task_run(
    exc: BaseException,
    *,
    task_id: str,
    elapsed_seconds: float,
    recursion_limit: int,
    workspace: Path | None,
) -> TaskRun:
    """Convert an agent/runtime exception into a normal task failure."""
    if _is_graph_recursion_error(exc):
        return TaskRun(
            task_id=task_id,
            passed=False,
            message=f"graph recursion limit reached after {recursion_limit} steps",
            elapsed_seconds=elapsed_seconds,
            workspace=workspace,
        )
    return TaskRun(
        task_id=task_id,
        passed=False,
        message="",
        elapsed_seconds=elapsed_seconds,
        error=traceback.format_exc(),
        workspace=workspace,
    )


@dataclass
class TaskRun:
    """The outcome of running a single task."""

    task_id: str
    passed: bool
    message: str
    elapsed_seconds: float
    error: str | None = None
    workspace: Path | None = None
    attempt: int = 1
    attempts: int = 1


def _load_env_from_dotenv() -> None:
    """Best-effort load of .env from the repository root."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # Find a .env next to the package — fall back to CWD.
    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def build_agent(workspace: Path, *, recursion_limit: int = 80) -> Any:
    """Build a deep agent backed by GigaChat and rooted at `workspace`.

    Imports happen here so `--gold` / `list` modes can run without GigaChat
    credentials configured.
    """
    from deepagents import create_deep_agent
    from deepagents.backends import LocalShellBackend
    from langchain_gigachat import GigaChat

    # The deepagents-gigachat harness profile is optional. When installed,
    # register it explicitly so editable/local installs work the same way as
    # entry-point installs and so AgentsMdInjectMiddleware knows this task's
    # workspace. Without it the agent runs on stock deepagents defaults.
    try:
        from deepagents_gigachat import register_harness, set_workspace_path
        register_harness()
        set_workspace_path(workspace)
    except ImportError:
        pass

    backend = LocalShellBackend(
        root_dir=workspace,
        virtual_mode=True,
        inherit_env=True,
    )
    model = GigaChat(
        model=os.getenv("GIGACHAT_MODEL", "GigaChat-3-Ultra"),
        # Let gigachat-sdk use its current default base URL unless the caller
        # explicitly overrides it. Hard-coding the old IFT URL here breaks
        # CORP/PERS credentials that expect the SDK default endpoint.
        base_url=os.getenv("GIGACHAT_BASE_URL") or None,
        verify_ssl_certs=os.getenv("GIGACHAT_VERIFY_SSL_CERTS", "false").lower()
        not in ("false", "0", "no"),
        profanity_check=False,
        timeout=600,
        # Transient backend errors (403/429/5xx from IFT endpoint under
        # concurrency) are not "the model misbehaving" — they're rate-limit /
        # connectivity blips. Retry transparently so the agent trace survives.
        max_retries=20,
        retry_backoff_factor=1.0,
        retry_on_status_codes=(403, 429, 500, 502, 503, 504),
    )
    # Memory tasks (222–231) ship an AGENTS.md fixture; pre-existing 221
    # tasks do not, so MemoryMiddleware is wired in only when the fixture is
    # present. `LocalShellBackend(virtual_mode=True)` maps `/AGENTS.md` to
    # `<workspace>/AGENTS.md`.
    memory_sources = ["/AGENTS.md"] if (workspace / "AGENTS.md").exists() else None
    agent = create_deep_agent(model=model, backend=backend, memory=memory_sources)
    return agent.with_config({"recursion_limit": recursion_limit})


def _ensure_credentials() -> None:
    if os.getenv("GIGACHAT_CREDENTIALS"):
        return
    if os.getenv("GIGACHAT_USER") and os.getenv("GIGACHAT_PASSWORD"):
        return
    raise SystemExit(
        "GigaChat credentials are not configured. "
        "Set GIGACHAT_CREDENTIALS or both GIGACHAT_USER and GIGACHAT_PASSWORD."
    )


def run_task(
    task: Task,
    *,
    keep_workspace: bool = False,
    recursion_limit: int = 80,
) -> TaskRun:
    """Run a single task end-to-end and return its outcome.

    Args:
        task: The benchmark task to execute.
        keep_workspace: When `True`, the temp workspace directory is not
            deleted after the run — handy for debugging a failure.
        recursion_limit: Cap on agent loop iterations.
    """
    workspace_keepalive: TemporaryDirectory | None = None
    try:
        if keep_workspace:
            workspace_path = Path(__import__("tempfile").mkdtemp(prefix=f"hb_{task.id}_"))
        else:
            workspace_keepalive = TemporaryDirectory(prefix=f"hb_{task.id}_")
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
    attempts: int = 1,
) -> list[TaskRun]:
    """Run a subset (or all) of the benchmark tasks.

    When `concurrency == 1` (default) tasks run sequentially and progress is
    printed in two lines per task (`[START] task_id: name` then `[PASS] ...`).
    When `concurrency > 1` tasks run in a `ThreadPoolExecutor` (each task is
    fully isolated in its own `TemporaryDirectory`, so no synchronization is
    required around the workspace); progress is printed as a single line per
    task in completion order. The returned list is sorted by task id so the
    summary block is deterministic regardless of completion order.
    """
    _load_env_from_dotenv()
    _ensure_credentials()

    if attempts < 1:
        raise ValueError("attempts must be positive")

    targets = [get_task(tid) for tid in task_ids] if task_ids else list(ALL_TASKS)

    if concurrency <= 1:
        results: list[TaskRun] = []
        for task in targets:
            for attempt in range(1, attempts + 1):
                label = _task_attempt_label_for(task.id, attempt, attempts)
                print(f"[START] {label}: {task.name}")
                run = run_task(
                    task,
                    keep_workspace=keep_workspace,
                    recursion_limit=recursion_limit,
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
                keep_workspace=keep_workspace,
                recursion_limit=recursion_limit,
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


def _task_sort_key(task_id: str) -> tuple[int, str]:
    """Sort task ids by their leading numeric component (`task_03_*` < `task_10_*`)."""
    # Strip the leading "task_" prefix and grab digits up to the next underscore.
    rest = task_id.removeprefix("task_")
    head, _, _ = rest.partition("_")
    try:
        return (int(head), task_id)
    except ValueError:
        return (10**9, task_id)


def _one_line_detail(run: TaskRun) -> str:
    """Squash a `TaskRun`'s message/error into a single informative line.

    For verifier failures the message itself is one line and is used as-is.
    For agent exceptions we surface the last non-empty traceback line — that
    is the actual exception type and message, which is much more useful than
    the leading "Traceback (most recent call last):".
    """
    if run.message:
        first = run.message.splitlines()[0]
        return first
    if run.error:
        lines = [line for line in run.error.splitlines() if line.strip()]
        if not lines:
            return "(unknown error)"
        return lines[-1]
    return "(no detail)"


def _mark_attempt(run: TaskRun, attempt: int, attempts: int) -> TaskRun:
    return replace(run, attempt=attempt, attempts=attempts)


def _task_attempt_label_for(task_id: str, attempt: int, attempts: int) -> str:
    if attempts == 1:
        return task_id
    return f"{task_id} #{attempt}/{attempts}"


def _task_attempt_label(run: TaskRun) -> str:
    return _task_attempt_label_for(run.task_id, run.attempt, run.attempts)


def summarize(
    results: list[TaskRun],
    *,
    pass_at_ks: tuple[int, ...] = (1,),
    pass_hat_ks: tuple[int, ...] = (),
) -> None:
    """Print a pass/fail summary block at the end of a run."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print()
    print("=" * 64)
    label = "Passed" if all(r.attempts == 1 for r in results) else "Passed attempts"
    print(f"{label}: {passed}/{total}")
    metrics = compute_pass_metrics(results, pass_at_ks=pass_at_ks, pass_hat_ks=pass_hat_ks)
    if metrics:
        print()
        print("Metrics:")
        for line in _format_metrics_table(metrics):
            print(f"  {line}")
    if passed < total:
        print()
        print("Failures:")
        show_tracebacks = os.getenv("HARNESS_BENCH_SHOW_TRACEBACK", "").lower() in (
            "1",
            "true",
            "yes",
        )
        for r in results:
            if r.passed:
                continue
            print(f"  - {_task_attempt_label(r)}: {_one_line_detail(r)}")
            if show_tracebacks and r.error:
                print("    Traceback:")
                for line in r.error.rstrip().splitlines():
                    print(f"      {line}")


def _format_metrics_table(metrics: list[PassMetric]) -> list[str]:
    metric_by_key = {(metric.kind, metric.k): metric for metric in metrics}
    ks = sorted({metric.k for metric in metrics})
    kinds = [kind for kind in ("pass@", "pass^") if any(m.kind == kind for m in metrics)]
    headers = ["K", *(f"{kind}K" for kind in kinds)]
    rows = [
        [
            str(k),
            *[
                _format_metric_value(metric_by_key.get((kind, k)))
                for kind in kinds
            ],
        ]
        for k in ks
    ]
    widths = [
        max(len(row[i]) for row in [headers, *rows])
        for i in range(len(headers))
    ]
    lines = [
        _format_table_row(headers, widths),
        _format_table_row(["-" * width for width in widths], widths),
    ]
    lines.extend(_format_table_row(row, widths) for row in rows)
    return lines


def _format_metric_value(metric: PassMetric | None) -> str:
    if metric is None:
        return "-"
    task_equivalent = metric.value * metric.task_count
    rounded = round(task_equivalent)
    count = str(rounded) if abs(task_equivalent - rounded) < 1e-9 else f"{task_equivalent:.1f}"
    return f"{count}/{metric.task_count}"


def _format_table_row(values: list[str], widths: list[int]) -> str:
    cells = [value.rjust(width) for value, width in zip(values, widths, strict=True)]
    return "  ".join(cells)


def results_to_payload(results: list[TaskRun]) -> dict[str, Any]:
    """Build a JSON-serializable payload describing a benchmark run.

    The payload carries an aggregate (``passed``/``total``/``pass_rate``) plus a
    per-task breakdown including each task's free-form ``tags`` and numeric id,
    so downstream tooling can compute per-category metrics without re-importing
    the task registry.
    """
    from harness_bench.versioning import TASK_SET_VERSION, task_number

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    tasks_payload: list[dict[str, Any]] = []
    for r in results:
        try:
            tags = list(get_task(r.task_id).tags)
        except Exception:  # noqa: BLE001 — tags are best-effort metadata
            tags = []
        tasks_payload.append(
            {
                "task_id": r.task_id,
                "number": task_number(r.task_id),
                "passed": r.passed,
                "message": r.message,
                "elapsed_seconds": r.elapsed_seconds,
                "error": r.error,
                "tags": tags,
                "attempt": r.attempt,
                "attempts": r.attempts,
            }
        )
    return {
        "task_set_version": TASK_SET_VERSION,
        "total": total,
        "passed": passed,
        "pass_rate": (passed / total) if total else 0.0,
        "tasks": tasks_payload,
    }


def write_results_json(results: list[TaskRun], path: str | Path) -> None:
    """Serialize a run's results to ``path`` as JSON (parents are created)."""
    import json

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(results_to_payload(results), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Gold sanity check (no LLM): exercises the verifiers themselves.
# ---------------------------------------------------------------------------


def verify_gold(task_ids: list[str] | None = None) -> list[TaskRun]:
    """Apply each task's gold solution to a temp workspace and run the verifier.

    Useful for catching off-by-one bugs in verifier code without spending any
    LLM tokens.
    """
    targets = [get_task(tid) for tid in task_ids] if task_ids else list(ALL_TASKS)

    results: list[TaskRun] = []
    for task in targets:
        with TemporaryDirectory(prefix=f"hb_gold_{task.id}_") as tmp:
            ws = Path(tmp)
            task.setup(ws)
            task.apply_gold(ws)
            start = time.monotonic()
            outcome: VerifyResult = task.verify(ws)
            elapsed = time.monotonic() - start
        run = TaskRun(
            task_id=task.id,
            passed=outcome.passed,
            message=outcome.message,
            elapsed_seconds=elapsed,
        )
        results.append(run)
        status = "OK  " if outcome.passed else "BAD "
        first = outcome.message.splitlines()[0] if outcome.message else ""
        print(f"[{status}] {task.id} ({elapsed * 1000:.1f}ms) — {first}")
    return results
