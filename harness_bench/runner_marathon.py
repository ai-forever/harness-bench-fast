"""Marathon runner: one agent session is handed the whole task set at once.

The agent gets every task's workspace already set up, a list of them in
``TASKS.md``, and one instruction: work through it. Nothing paces it and
nothing hands control back — the runner does not see the agent move from task
to task, and only looks at the workspace once the session is over.

That shape is the point. Two numbers come out of it that a per-task runner
cannot produce:

- **how far it got** — the furthest task whose directory it touched. Recovered
  from a fingerprint of every task directory taken before the run: no task in
  the set is solvable without writing something, so an untouched directory was
  never worked on. Without this, "solved 50 of 342" reads the same whether the
  agent worked through everything and failed 292 or stopped at task 50.
- **why it stopped** — running out of context, looping until the step cap, or
  simply announcing it was finished at task 12. The last is not an error, and
  is the most common way a long horizon ends.

The task prompts are delivered as a file rather than in the prompt because
they do not fit one: 342 of them come to ~31k tokens, more than a 32k window
holds before any work has started. Paging through the list is part of what is
being measured.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from typing import Any

from harness_bench.core import Task
from harness_bench.marathon import MARATHON_VERSION, marathon_task_ids
from harness_bench.runner import (
    AgentRunStatsCollector,
    TaskTimeoutError,
    _is_graph_recursion_error,
    _load_env_from_dotenv,
    normalize_json_output_path,
    results_json_command,
)
from harness_bench.tasks import get_task
from harness_bench.versioning import TASK_SET_VERSION, TASK_WAVES, task_number

DEFAULT_SECONDS_PER_TASK = 300
"""Whole-run timeout defaults to this many seconds times the task count."""

_ORPHAN_GRACE_SECONDS = 30.0
"""How long to wait for a timed-out invocation to finish before scoring anyway."""

TASK_LIST_FILENAME = "TASKS.md"

BACKENDS = ("gigachat", "gigachat-pure", "openrouter")


class MarathonTimeout(TaskTimeoutError):
    """The run hit its wall-clock cap.

    Carries the worker thread because giving up on an invocation does not end
    it: the agent runs on until its graph does, and everything it writes after
    the timeout lands in a workspace that is being scored.
    """

    def __init__(self, message: str, thread: threading.Thread) -> None:
        super().__init__(message)
        self.thread = thread


def _is_context_overflow_error(exc: BaseException) -> bool:
    """Heuristic: the request failed because the session no longer fits."""
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "context length",
        "context_length",
        "maximum context",
        "context window",
        "context size",
        # GigaChat: `422 CONTEXT_TOO_LONG: context too long 264065, maximum
        # allowed context is 261120`. Matches none of the OpenAI-flavoured
        # wordings above, so without these two the most expected ending of a
        # marathon would be filed as an unexplained runtime error.
        "context_too_long",
        "context too long",
        "token limit",
        "tokens exceed",
        "exceeds the limit",
        "too many tokens",
        "prompt is too long",
        "input is too long",
        "input too long",
        "payload too large",
        "request entity too large",
    )
    return any(marker in text for marker in markers)


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------


@dataclass
class TaskRecord:
    """Outcome of one task inside the marathon."""

    position: int  # 1-based
    task_id: str
    subdir: str
    reached: bool = False
    passed: bool = False
    message: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "task_id": self.task_id,
            "number": task_number(self.task_id),
            "subdir": self.subdir,
            "reached": self.reached,
            "passed": self.passed,
            "message": self.message,
        }


@dataclass
class MarathonResult:
    task_ids: tuple[str, ...]
    tasks: list[TaskRecord]
    stop_reason: str | None = None
    elapsed_seconds: float = 0.0
    error: str | None = None
    workspace: Path | None = None
    scoring_raced: bool = False
    """Scored while a timed-out agent was still running, so the workspace could
    change under the verifier."""
    completed: bool = False
    """The run was scored and the numbers are usable. An agent that failed,
    looped or quit early still completed — those are results, and the reason is
    in ``stop_reason``. False means the verdicts themselves are untrustworthy."""
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def solved(self) -> int:
        return sum(1 for t in self.tasks if t.passed)

    @property
    def reached(self) -> int:
        return sum(1 for t in self.tasks if t.reached)

    @property
    def frontier(self) -> int:
        """Position of the furthest task the agent touched (0 if it never
        started). Not the same as ``reached``: an agent may skip tasks it does
        not like and still get further down the list."""
        touched = [t.position for t in self.tasks if t.reached]
        return max(touched) if touched else 0

    def to_payload(self) -> dict[str, Any]:
        total = len(self.tasks)
        reached = self.reached
        payload: dict[str, Any] = {
            "task_set_version": TASK_SET_VERSION,
            "marathon_version": MARATHON_VERSION,
            "mode": "marathon",
        }
        if command := results_json_command():
            payload["command"] = command
        payload.update(
            {
                "total": total,
                "passed": self.solved,
                "pass_rate": (self.solved / total) if total else 0.0,
                "reached": reached,
                "frontier": self.frontier,
                # Two different questions: pass_rate is "how much of the set did
                # it deliver", this one is "how good was it where it did work".
                "pass_rate_of_reached": (self.solved / reached) if reached else 0.0,
                "stop_reason": self.stop_reason,
                "error": self.error,
                "scoring_raced": self.scoring_raced,
                "completed": self.completed,
                "elapsed_seconds": self.elapsed_seconds,
                "workspace": str(self.workspace) if self.workspace else None,
                "stats": self.stats or None,
                "tasks": [task.to_payload() for task in self.tasks],
            }
        )
        return payload


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------


def _subdir_name(position: int, task_id: str, total: int) -> str:
    width = max(2, len(str(total)))
    return f"t{position:0{width}d}_{task_id}"


def _task_list_document(tasks: list[Task], subdirs: list[str]) -> str:
    """The whole task list as a file the agent reads instead of a giant prompt."""
    total = len(tasks)
    lines = [
        f"# Задачи ({total})",
        "",
        "Каждая задача независима и выполняется в своём подкаталоге, указанном "
        "в её заголовке. Относительные пути из условия отсчитывай от этого "
        "подкаталога. Каталоги других задач не изменяй.",
        "",
    ]
    for index, (task, subdir) in enumerate(zip(tasks, subdirs, strict=True), start=1):
        lines += [f"## Задача {index}/{total} — каталог `{subdir}/`", "", task.prompt, ""]
    return "\n".join(lines)


def _kickoff_prompt(total: int, list_path: str) -> str:
    return (
        f"В файле {list_path} лежит список из {total} независимых задач.\n"
        f"Реши их ВСЕ, строго по порядку, одну за другой: не переходи к следующей, "
        f"пока не закончишь текущую, и не останавливайся, пока не выполнишь все {total}.\n"
        f"Файл длинный — читай его частями по мере продвижения, не пытайся "
        f"загрузить целиком.\n"
        f"Каждая задача выполняется в своём подкаталоге (указан в её заголовке); "
        f"относительные пути из условия отсчитывай от её подкаталога. "
        f"Shell-команды выполняй с префиксом `cd <каталог задачи> && ...`. "
        f"Каталоги других задач не изменяй."
    )


def _dir_fingerprint(path: Path) -> str:
    """Content hash of a task subdirectory (names + mode + size + bytes).

    Mode and size come from ``lstat`` and cover files the process cannot read:
    the adversarial wave ships deliberately unreadable fixtures (``task_334``),
    and a chmod is itself agent activity worth noticing.
    """
    digest = hashlib.sha256()
    for entry in sorted(path.rglob("*")):
        digest.update(str(entry.relative_to(path)).encode())
        info = entry.lstat()
        digest.update(f"{info.st_mode}:{info.st_size}".encode())
        if entry.is_file() and not entry.is_symlink():
            try:
                digest.update(entry.read_bytes())
            except OSError:
                digest.update(b"<unreadable>")
    return digest.hexdigest()


def _mark_reached(result: MarathonResult, root: Path, before: dict[str, str]) -> None:
    """Split "worked on it and failed" from "never got there".

    Must run *before* verification: ``pytest_passes`` tasks leave
    ``__pycache__`` behind, which would look like agent activity.
    """
    for task in result.tasks:
        task.reached = _dir_fingerprint(root / task.subdir) != before[task.subdir]
        if not task.reached:
            task.message = "not reached: task directory untouched"


def _setup_task_subdir(task: Task, root: Path, subdir: str) -> Path:
    workspace = root / subdir
    workspace.mkdir(parents=True, exist_ok=True)
    task.setup(workspace)
    return workspace


# ---------------------------------------------------------------------------
# Agent factories
# ---------------------------------------------------------------------------


def make_agent_factory(
    backend: str,
    *,
    model_name: str | None = None,
    recursion_limit: int = 5000,
    max_tokens: int | None = None,
    harness_profile: str | None = None,
    context_window: int | None = None,
    forward_reasoning_history: bool = False,
) -> Any:
    """Return ``factory(workspace) -> agent`` for a deepagents backend."""
    if backend == "gigachat":
        from harness_bench.runner import _ensure_credentials, build_agent

        _ensure_credentials()
        return lambda workspace: build_agent(
            workspace, recursion_limit=recursion_limit, context_window=context_window
        )
    if backend == "gigachat-pure":
        from harness_bench.runner_pure import _ensure_credentials, build_agent

        _ensure_credentials()
        return lambda workspace: build_agent(workspace, recursion_limit=recursion_limit)
    if backend == "openrouter":
        from harness_bench.runner_openrouter import (
            DEFAULT_OPENROUTER_MODEL,
            _ensure_openrouter_key,
            build_agent,
        )

        _ensure_openrouter_key()
        model = model_name or DEFAULT_OPENROUTER_MODEL
        return lambda workspace: build_agent(
            workspace,
            model_name=model,
            recursion_limit=recursion_limit,
            max_tokens=max_tokens,
            harness_profile=harness_profile,
            context_window=context_window,
            # A marathon is where dropping reasoning hurts most: the loss
            # compounds over every step of one session.
            forward_reasoning_history=forward_reasoning_history,
        )
    raise SystemExit(f"Unknown marathon backend: {backend!r}. Choose from {BACKENDS}.")


def _invoke_with_timeout(
    agent: Any, payload: dict[str, Any], stats: AgentRunStatsCollector, timeout: float
) -> Any:
    callbacks = [cb] if (cb := stats.as_callback()) is not None else None

    def _call() -> Any:
        if not callbacks:
            return agent.invoke(payload)
        try:
            return agent.invoke(payload, config={"callbacks": callbacks})
        except TypeError as exc:
            if "config" not in str(exc):
                raise
            return agent.invoke(payload)

    box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["result"] = _call()
        except BaseException as exc:  # noqa: BLE001 — re-raised in the caller thread
            box["error"] = exc

    thread = threading.Thread(target=_worker, name="hb-marathon-invoke", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise MarathonTimeout(f"run exceeded its {timeout:.0f}s timeout", thread)
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _settle_invocation(exc: MarathonTimeout, progress: Any) -> bool:
    """Wait out a timed-out invocation. Returns True if it is still running.

    Python cannot kill a thread, so a timeout does not stop the agent: it keeps
    writing into the very workspace about to be fingerprinted, verified and
    (with ``--keep``) handed back for offline re-checking. Most timeouts are
    near-misses that a short grace period turns into no race at all; when it
    does not, the caller must say the score was taken against a moving target.
    """
    exc.thread.join(_ORPHAN_GRACE_SECONDS)
    if not exc.thread.is_alive():
        return False
    progress(
        f"  ! the timed-out agent is still running after {_ORPHAN_GRACE_SECONDS:.0f}s "
        f"— scoring may race its writes"
    )
    return True


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _prepare(
    root: Path, first: int | None
) -> tuple[list[Task], list[str], MarathonResult]:
    task_ids = marathon_task_ids()[: first or None]
    tasks = [get_task(task_id) for task_id in task_ids]
    total = len(tasks)
    subdirs = [_subdir_name(i + 1, t.id, total) for i, t in enumerate(tasks)]
    result = MarathonResult(
        task_ids=tuple(task_ids),
        tasks=[
            TaskRecord(position=i + 1, task_id=t.id, subdir=subdirs[i])
            for i, t in enumerate(tasks)
        ],
    )
    return tasks, subdirs, result


def _score(
    result: MarathonResult, tasks: list[Task], root: Path, before: dict[str, str], progress: Any
) -> None:
    if result.scoring_raced:
        # Fingerprints and verdicts would both be racing the orphaned agent.
        # Score anyway — a provisional number beats none — but the caller marks
        # the run untrustworthy so a reader does not take it as final.
        progress("  ! scoring against a workspace that is still being written to")
    _mark_reached(result, root, before)
    for task, record in zip(tasks, result.tasks, strict=True):
        if not record.reached:
            continue
        verify = task.verify(root / record.subdir)
        record.passed = verify.passed
        record.message = verify.message
    if result.stop_reason is None and result.frontier < len(result.tasks):
        # No exception, no timeout: the agent decided it was done. That is its
        # own long-horizon failure and must not read as a crash.
        result.stop_reason = "agent stopped before the end of the list"


def _workspace_root(keep_workspace: bool) -> tuple[Path, TemporaryDirectory | None]:
    if keep_workspace:
        return Path(mkdtemp(prefix="hb_marathon_")), None
    keepalive = TemporaryDirectory(prefix="hb_marathon_")
    return Path(keepalive.name), keepalive


def run_marathon(
    *,
    backend: str = "gigachat",
    model_name: str | None = None,
    cli_command: str | None = None,
    first: int | None = None,
    recursion_limit: int | None = None,
    max_tokens: int | None = None,
    harness_profile: str | None = None,
    context_window: int | None = None,
    forward_reasoning_history: bool = False,
    keep_workspace: bool = False,
    timeout: float | None = None,
    json_output: str | Path | None = None,
    progress: Any = print,
) -> MarathonResult:
    """Hand one agent session the whole task set and see how far it gets."""
    _load_env_from_dotenv()
    json_path = normalize_json_output_path(json_output)

    root, keepalive = _workspace_root(keep_workspace)
    tasks, subdirs, result = _prepare(root, first)
    total = len(tasks)
    result.workspace = root if keep_workspace else None
    run_timeout = timeout or DEFAULT_SECONDS_PER_TASK * total

    def _write() -> None:
        if json_path is None:
            return
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(result.to_payload(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    started = time.monotonic()
    try:
        progress(f"[MARATHON] {total} tasks in one session, backend={backend}")
        for task, subdir in zip(tasks, subdirs, strict=True):
            _setup_task_subdir(task, root, subdir)
        before = {subdir: _dir_fingerprint(root / subdir) for subdir in subdirs}
        (root / TASK_LIST_FILENAME).write_text(
            _task_list_document(tasks, subdirs), encoding="utf-8"
        )
        # Nothing else lands on disk until the session ends, which is hours on a
        # full run. Put the plan there now so a killed run still says what it
        # was running and in what order.
        _write()

        if cli_command is not None:
            _run_cli(result, cli_command, root, run_timeout, progress)
        else:
            _run_agent(
                result,
                root,
                run_timeout,
                progress,
                backend=backend,
                model_name=model_name,
                recursion_limit=recursion_limit or 60 * total,
                max_tokens=max_tokens,
                harness_profile=harness_profile,
                context_window=context_window,
                forward_reasoning_history=forward_reasoning_history,
            )

        _score(result, tasks, root, before, progress)
        result.completed = not result.scoring_raced
        return result
    finally:
        result.elapsed_seconds = time.monotonic() - started
        _write()
        if keepalive is not None:
            keepalive.cleanup()


def _run_agent(
    result: MarathonResult,
    root: Path,
    timeout: float,
    progress: Any,
    **factory_kwargs: Any,
) -> None:
    backend = factory_kwargs.pop("backend")
    agent = make_agent_factory(backend, **factory_kwargs)(root)
    prompt = _kickoff_prompt(len(result.tasks), f"/{TASK_LIST_FILENAME}")
    stats = AgentRunStatsCollector()
    try:
        invocation = _invoke_with_timeout(
            agent, {"messages": [{"role": "user", "content": prompt}]}, stats, timeout
        )
    except Exception as exc:  # noqa: BLE001 — every ending is a result here
        if isinstance(exc, TaskTimeoutError):
            result.error = f"timed out after {timeout:.0f}s"
            if isinstance(exc, MarathonTimeout):
                result.scoring_raced = _settle_invocation(exc, progress)
        elif _is_graph_recursion_error(exc):
            result.error = "step limit reached (--recursion-limit)"
        elif _is_context_overflow_error(exc):
            result.error = f"context overflow: {exc}"
        else:
            result.error = f"runtime error: {traceback.format_exc()}"
        result.stop_reason = result.error
        result.stats = dict(stats.stats)
    else:
        result.stats = stats.merged(invocation)


def _run_cli(
    result: MarathonResult,
    cli_command: str,
    root: Path,
    timeout: float,
    progress: Any,
) -> None:
    from harness_bench.runner_cli import (
        _argv_for_workspace,
        _claude_json_event_stats,
        _codex_json_event_stats,
        _ensure_cli_json_events,
        _gemini_json_event_stats,
        _grok_json_event_stats,
        _mini_swe_agent_traj_stats,
        _run_cli_subprocess,
        _subprocess_env_with_token,
    )

    argv = _argv_for_workspace(_ensure_cli_json_events(shlex.split(cli_command)), root)
    argv = [*argv, _kickoff_prompt(len(result.tasks), f"./{TASK_LIST_FILENAME}")]
    try:
        completed = _run_cli_subprocess(
            argv, cwd=root, timeout=int(timeout), env=_subprocess_env_with_token()
        )
    except subprocess.TimeoutExpired:
        result.error = f"CLI timed out after {timeout:.0f}s"
    except FileNotFoundError as exc:
        result.error = f"CLI executable not found: {exc}"
    else:
        if completed.returncode != 0:
            tail = (completed.stderr or "").strip().splitlines()[-3:]
            result.error = f"CLI exit={completed.returncode}: {' | '.join(tail)[:300]}"
        stdout = completed.stdout or ""
        for extract in (
            _codex_json_event_stats,
            _claude_json_event_stats,
            _gemini_json_event_stats,
        ):
            if (stats := extract(stdout)) is not None:
                result.stats = stats
                break
        else:
            result.stats = (
                _grok_json_event_stats(stdout, workspace=root)
                or _mini_swe_agent_traj_stats(root)
                or {}
            )
    if result.error:
        result.stop_reason = result.error


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _quartiles(result: MarathonResult) -> list[tuple[str, int, int]]:
    """Solved counts by position quartile — the depth-degradation read.

    The order is shuffled by seed, so the quartiles hold equally hard tasks and
    a falling tail is the long horizon rather than the task mix.
    """
    total = len(result.tasks)
    if not total:
        return []
    buckets: dict[int, list[TaskRecord]] = {1: [], 2: [], 3: [], 4: []}
    for task in result.tasks:
        buckets[min(4, (task.position - 1) * 4 // total + 1)].append(task)
    return [
        (f"Q{q}", sum(1 for t in buckets[q] if t.passed), len(buckets[q]))
        for q in (1, 2, 3, 4)
        if buckets[q]
    ]


def summarize_marathon(result: MarathonResult) -> None:
    total = len(result.tasks)
    print()
    print("=" * 64)
    print(f"Marathon — {total} tasks in one session")
    print(f"  solved            : {result.solved}/{total} "
          f"({result.solved / total * 100:.1f}%)" if total else "  solved: 0/0")
    print(f"  reached           : {result.reached}/{total}")
    print(f"  got as far as     : task {result.frontier} of {total}")
    if result.reached:
        print(f"  solved of reached : {result.solved}/{result.reached} "
              f"({result.solved / result.reached * 100:.1f}%)")
    print(f"  wall clock        : {result.elapsed_seconds / 60:.1f} min")
    print(f"  stopped because   : {result.stop_reason or 'reached the end of the list'}")

    if quartiles := _quartiles(result):
        print()
        print("Solved by position quartile:")
        print("  " + "   ".join(
            f"{label}: {passed}/{count} ({passed / count * 100:.0f}%)"
            for label, passed, count in quartiles
        ))

    by_wave: dict[str, list[TaskRecord]] = {}
    for task in result.tasks:
        number = task_number(task.task_id)
        wave = next((w for w in TASK_WAVES if number is not None and w.contains(number)), None)
        if wave is not None:
            by_wave.setdefault(wave.name, []).append(task)
    if by_wave:
        print()
        print("Solved by wave:")
        for name, items in by_wave.items():
            solved = sum(1 for t in items if t.passed)
            print(f"  {name:16s} {solved:3d}/{len(items):3d}")

    if result.scoring_raced:
        print()
        print("WARNING: scored while a timed-out agent was still running. The "
              "workspace could move under the verifier, so treat these numbers "
              "as provisional.")
