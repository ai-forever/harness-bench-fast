"""Chain-mode ("marathon") runner: one agent session solves N tasks in a row.

Two delivery modes (see ``chains.py`` for what chains are):

- ``turns``  — multi-turn conversation. Each task arrives as a new user
  message appended to the same growing message history; the agent is built
  ONCE per chain, so the whole chain shares one context. Available for the
  deepagents backends (``gigachat``, ``gigachat-pure``, ``openrouter``).
- ``batch``  — all N tasks are delivered in a single combined prompt and the
  agent works through them autonomously in one invocation. Available for the
  deepagents backends and for external CLI agents (``--cli-command``), which
  makes marathons possible for harnesses without a resume API.

Every task runs in its own subdirectory of the chain workspace
(``t01_<task_id>/``), because task fixtures collide by filename. Verification
is two-phase: ``passed_immediate`` right after the task's turn, and
``passed_final`` re-checking every subdirectory after the whole chain — the
difference (retention) is how much previously-solved work the session broke
while working on later tasks.

Failure policy: a failed turn does NOT stop the chain (recovering after a
failure is part of what is measured). The chain terminates early only on
session-fatal conditions — context overflow, exhausted transient retries, or
an unexpected runtime error — and the remaining tasks are recorded as
``not reached`` failures in the denominator, with ``terminated_at_position``
as the survival signal.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from typing import Any

from harness_bench.chains import CHAINSET_VERSION, ChainSpec, get_chain
from harness_bench.core import Task
from harness_bench.runner import (
    AgentRunStatsCollector,
    TaskTimeoutError,
    _add_usage_counts,
    _is_graph_recursion_error,
    _load_env_from_dotenv,
    _message_role,
    _message_tool_calls,
    _message_usage,
    _tool_call_name,
    normalize_json_output_path,
    results_json_command,
)
from harness_bench.tasks import get_task
from harness_bench.versioning import TASK_SET_VERSION, task_number

DEFAULT_TURN_TIMEOUT_SECONDS = 900
"""Per-turn wall-clock cap (turns delivery). Longer than the solo 600s cap
because late turns legitimately pay for a much larger prompt."""

DEFAULT_CHAIN_SECONDS_PER_TASK = 300
"""Batch delivery: default whole-chain timeout is this many seconds × length."""

DEFAULT_TRANSIENT_ATTEMPTS = 5
_TRANSIENT_BACKOFF_SCHEDULE = (15, 30, 60, 120)

_ORPHAN_GRACE_SECONDS = 30.0
"""How long to wait for a timed-out invocation to finish before scoring anyway."""


class ChainInvokeTimeout(TaskTimeoutError):
    """An invocation hit its wall-clock cap.

    Carries the worker thread because giving up on an invocation does not end
    it: the agent runs on until its graph does, and everything it writes after
    the timeout lands in a workspace that is being scored.
    """

    def __init__(self, message: str, thread: threading.Thread) -> None:
        super().__init__(message)
        self.thread = thread

_AGENT_TOKEN_KEYS = ("agent_input_tokens", "agent_output_tokens", "agent_total_tokens")

_TURN_STAT_KEYS = (
    "agent_steps",
    "agent_tool_calls",
    "agent_shell_commands",
    "agent_events",
    "agent_llm_calls",
    # Per-tool counts answer what the totals cannot: whether the session
    # delegated to subagents (`task` has its own context, so a marathon routed
    # through it is not one conversation) and which call an agent repeated
    # when it got stuck — the Lightning marathon died on 188 identical
    # `write_file` refusals, which is invisible in the totals.
    "agent_tool_calls_by_name",
    *_AGENT_TOKEN_KEYS,
)

_RESULTS_JSON_LOCK = threading.Lock()


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
        # wordings above, so without these two the marathon's most expected
        # ending would be filed as an unexplained runtime error.
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


def _is_transient_model_error(exc: BaseException) -> bool:
    from harness_bench.runner_openrouter import (
        _is_transient_model_error as _openrouter_transient,
    )

    return _openrouter_transient(exc)


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------


@dataclass
class TurnRecord:
    """Outcome of one task inside a chain."""

    position: int  # 1-based
    task_id: str
    subdir: str
    reached: bool = False
    passed_immediate: bool | None = None
    """Verdict taken right after this task's turn, or ``None`` when the
    delivery cannot take one. Only ``turns`` hands control back between tasks;
    ``batch``/``file`` run the whole chain in a single invocation and can only
    verify once, at the end. ``None`` means "not measured" and must never be
    collapsed into ``False`` — the difference against ``passed_final`` is the
    retention signal, and a delivery that cannot measure it has to say so."""
    message_immediate: str = ""
    passed_final: bool = False
    message_final: str = ""
    elapsed_seconds: float = 0.0
    error: str | None = None
    context_messages_before: int | None = None
    context_chars_before: int | None = None
    """Total characters of session history handed to this turn — a
    provider-neutral context-size proxy. ``agent_input_tokens`` is NOT that:
    providers with prompt caching (e.g. the GigaChat IFT stand) report only
    the non-cached tokens in ``usage.prompt_tokens``, so late turns can show
    tiny input counts while the real context keeps growing."""
    stats: dict[str, int] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "position": self.position,
            "task_id": self.task_id,
            "number": task_number(self.task_id),
            "subdir": self.subdir,
            "reached": self.reached,
            "passed_immediate": self.passed_immediate,
            "message_immediate": self.message_immediate,
            "passed_final": self.passed_final,
            "message_final": self.message_final,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
            "context_messages_before": self.context_messages_before,
            "context_chars_before": self.context_chars_before,
        }
        for key in _TURN_STAT_KEYS:
            payload[key] = self.stats.get(key)
        return payload


@dataclass
class ChainRunResult:
    """Outcome of one whole chain."""

    chain_id: str
    task_ids: tuple[str, ...]
    delivery: str
    turns: list[TurnRecord]
    terminated_at_position: int | None = None
    termination_reason: str | None = None
    elapsed_seconds: float = 0.0
    error: str | None = None
    workspace: Path | None = None
    scoring_raced: bool = False
    """The chain was scored while a timed-out invocation was still running, so
    the workspace could change under the verifier. See ``_settle_invocation``."""
    chain_stats: dict[str, int] = field(default_factory=dict)
    completed: bool = False
    """The chain was scored and the numbers are usable. False means the run
    was cut short in a way that makes its verdicts untrustworthy, not merely
    that the agent failed or stopped early — those are results, and keep
    ``completed`` True with ``termination_reason`` set."""

    @property
    def retention_measured(self) -> bool:
        """Whether this chain has a per-task verdict to compare the final one
        against. Only ``turns`` verifies between tasks."""
        return any(turn.passed_immediate is not None for turn in self.turns)

    @property
    def immediate_passed(self) -> int | None:
        if not self.retention_measured:
            return None
        return sum(1 for t in self.turns if t.passed_immediate)

    @property
    def final_passed(self) -> int:
        return sum(1 for t in self.turns if t.passed_final)

    @property
    def retention_broken(self) -> int | None:
        """Tasks solved at their own turn and broken by later work — ``None``
        when the delivery never took a per-task verdict. Reporting 0 there
        would read as "nothing was broken" when nothing was watched."""
        if not self.retention_measured:
            return None
        return sum(1 for t in self.turns if t.passed_immediate and not t.passed_final)

    def to_payload(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "length": len(self.task_ids),
            "task_ids": list(self.task_ids),
            "delivery": self.delivery,
            "completed": self.completed,
            "terminated_at_position": self.terminated_at_position,
            "termination_reason": self.termination_reason,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
            "workspace": str(self.workspace) if self.workspace else None,
            "scoring_raced": self.scoring_raced,
            "retention_measured": self.retention_measured,
            "immediate_passed": self.immediate_passed,
            "final_passed": self.final_passed,
            "retention_broken": self.retention_broken,
            "chain_stats": self.chain_stats or None,
            "turns": [turn.to_payload() for turn in self.turns],
        }


# ---------------------------------------------------------------------------
# Workspace / prompt helpers
# ---------------------------------------------------------------------------


def _subdir_name(position: int, task_id: str, chain_length: int) -> str:
    width = max(2, len(str(chain_length)))
    return f"t{position:0{width}d}_{task_id}"


def _turn_prompt(task: Task, position: int, total: int, subdir: str, *, virtual: bool) -> str:
    """Wrap the task prompt with per-task working-directory instructions."""
    root = f"/{subdir}/" if virtual else f"{subdir}/"
    example = f"/{subdir}/README.md" if virtual else f"{subdir}/README.md"
    return (
        f"Задача {position} из {total}.\n"
        f"Рабочий каталог этой задачи: {root} — все относительные пути из условия "
        f"ниже отсчитывай от него (например, README.md из условия — это {example}). "
        f"Shell-команды выполняй с префиксом `cd {subdir} && ...`, потому что "
        f"рабочий каталог shell — корень воркспейса. "
        f"Каталоги других задач (t01_*, t02_*, ...) не читай и не изменяй.\n"
        f"---\n"
        f"{task.prompt}"
    )


def _batch_prompt(tasks: list[Task], subdirs: list[str], *, virtual: bool) -> str:
    total = len(tasks)
    head = (
        f"Тебе дан список из {total} НЕЗАВИСИМЫХ задач. Реши их ВСЕ, строго по порядку, "
        f"одну за другой; не переходи к следующей, пока не закончишь текущую, и не "
        f"останавливайся, пока не выполнишь все {total}.\n"
        f"Каждая задача выполняется в своём подкаталоге (указан в её заголовке); все "
        f"относительные пути из условия задачи отсчитывай от её подкаталога. "
        f"Shell-команды выполняй с префиксом `cd <каталог задачи> && ...`. "
        f"Каталоги других задач не изменяй.\n"
    )
    blocks = []
    for index, (task, subdir) in enumerate(zip(tasks, subdirs, strict=True), start=1):
        root = f"/{subdir}/" if virtual else f"{subdir}/"
        blocks.append(f"=== Задача {index}/{total} — каталог {root} ===\n{task.prompt}")
    return head + "\n" + "\n\n".join(blocks)


TASK_LIST_FILENAME = "TASKS.md"


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


def _marathon_prompt(total: int, list_path: str) -> str:
    """Short kickoff prompt: the tasks themselves live in a file.

    All 342 eligible prompts total ~31k tokens, which does not fit a 32k window
    before any work starts. Handing over a path instead makes the agent page
    through the list itself, which is part of what long-horizon work measures.
    """
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

    Mode and size come from `lstat` and cover files the process cannot read:
    the adversarial wave ships deliberately unreadable fixtures (`task_334`),
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


def _mark_untouched_as_not_reached(
    result: ChainRunResult, chain_root: Path, before: dict[str, str]
) -> None:
    """Split "failed" from "never got there" on a batch/marathon run.

    Batch delivery hands over every task at once, so the runner cannot see the
    agent walk the list and marks all positions ``reached``. Then "solved 50 of
    342" reads the same whether the agent worked through everything and failed
    292, or stopped at task 50 — opposite findings. A subdirectory whose bytes
    never changed was never worked on: no task in the set is solvable without
    writing something (the audit's noop probe is 0/391).

    Must run *before* verification: `pytest_passes` tasks leave `__pycache__`
    behind, which would look like agent activity.
    """
    for turn in result.turns:
        if _dir_fingerprint(chain_root / turn.subdir) == before[turn.subdir]:
            turn.reached = False
            turn.message_final = "not reached: task directory untouched"
    touched = [turn.position for turn in result.turns if turn.reached]
    if touched and max(touched) < len(result.turns):
        result.terminated_at_position = max(touched) + 1
        if result.termination_reason is None:
            # No exception, no timeout — the agent decided it was done. That is
            # its own long-horizon failure and must not be read as a crash.
            result.termination_reason = "agent stopped before the end of the list"


def _setup_task_subdir(task: Task, chain_root: Path, subdir: str) -> Path:
    workspace = chain_root / subdir
    workspace.mkdir(parents=True, exist_ok=True)
    task.setup(workspace)
    return workspace


# ---------------------------------------------------------------------------
# Agent factories (deepagents backends)
# ---------------------------------------------------------------------------

BACKENDS = ("gigachat", "gigachat-pure", "openrouter")


def make_agent_factory(
    backend: str,
    *,
    model_name: str | None = None,
    recursion_limit: int = 80,
    max_tokens: int | None = None,
    harness_profile: str | None = None,
    context_window: int | None = None,
    forward_reasoning_history: bool = False,
) -> Any:
    """Return ``factory(chain_root) -> agent`` for a deepagents backend."""
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
            # Chains are where dropping reasoning hurts most: the loss compounds
            # over every turn of one session, and on our SGLang stand replaying
            # it was worth 9.6pp on the solo set alone.
            forward_reasoning_history=forward_reasoning_history,
        )
    raise SystemExit(f"Unknown chain backend: {backend!r}. Choose from {BACKENDS}.")


def _invoke_with_timeout(
    agent: Any,
    payload: dict[str, Any],
    stats: AgentRunStatsCollector,
    timeout: float,
) -> Any:
    """Like ``runner.invoke_agent_with_stats`` but with an explicit timeout."""
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

    thread = threading.Thread(target=_worker, name="hb-chain-invoke", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise ChainInvokeTimeout(
            f"invocation exceeded its {timeout:.0f}s timeout", thread
        )
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _settle_invocation(exc: ChainInvokeTimeout, progress: Any) -> bool:
    """Wait out a timed-out invocation. Returns True if it is still running.

    Python cannot kill a thread, so a timeout does not stop the agent: it keeps
    holding its connection and writing into the very workspace the runner is
    about to fingerprint, verify and (with ``--keep``) hand back for offline
    re-checking. Most timeouts are near-misses, so a short grace period usually
    turns the race into no race at all; when it does not, the caller must say
    the score was taken against a moving target rather than quietly publish it.
    """
    exc.thread.join(_ORPHAN_GRACE_SECONDS)
    if not exc.thread.is_alive():
        return False
    progress(
        f"  ! the timed-out agent is still running after "
        f"{_ORPHAN_GRACE_SECONDS}s — scoring may race its writes"
    )
    return True


def _history_chars(messages: list[Any]) -> int:
    """Character count of message contents — provider-neutral context proxy."""
    total = 0
    for message in messages:
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, str):
            total += len(content)
        elif content is not None:
            total += len(str(content))
    return total


def _turn_delta_stats(
    collector: AgentRunStatsCollector, delta_messages: list[Any]
) -> dict[str, Any]:
    """Per-turn effort metrics: message-delta counts + callback token usage."""
    steps = 0
    tool_calls = 0
    shell_commands = 0
    llm_calls = 0
    usage_stats: dict[str, int] = {}
    by_tool: dict[str, int] = {}
    for message in delta_messages:
        role = _message_role(message)
        if role not in ("human", "user", "system"):
            steps += 1
        calls = _message_tool_calls(message)
        if calls:
            tool_calls += len(calls)
            llm_calls += 1
            for call in calls:
                name = _tool_call_name(call)
                by_tool[name] = by_tool.get(name, 0) + 1
                if name == "execute":
                    shell_commands += 1
        elif role in ("ai", "assistant"):
            llm_calls += 1
        for usage in _message_usage(message):
            _add_usage_counts(usage_stats, usage)

    stats: dict[str, Any] = dict(collector.stats)
    stats["agent_events"] = len(delta_messages)
    stats["agent_steps"] = steps
    stats["agent_tool_calls"] = tool_calls
    stats["agent_shell_commands"] = shell_commands
    stats["agent_llm_calls"] = max(stats.get("agent_llm_calls", 0), llm_calls)
    stats["agent_tool_calls_by_name"] = by_tool
    for key in _AGENT_TOKEN_KEYS:
        if key in usage_stats or key in stats:
            stats[key] = max(stats.get(key, 0), usage_stats.get(key, 0))
    return stats


# ---------------------------------------------------------------------------
# Chain execution — turns delivery (deepagents)
# ---------------------------------------------------------------------------


def run_chain_turns(
    spec: ChainSpec,
    agent_factory: Any,
    *,
    keep_workspace: bool = False,
    turn_timeout: float = DEFAULT_TURN_TIMEOUT_SECONDS,
    transient_attempts: int = DEFAULT_TRANSIENT_ATTEMPTS,
    progress: Any = print,
    on_turn: Any = None,
) -> ChainRunResult:
    started = time.monotonic()
    tasks = [get_task(task_id) for task_id in spec.task_ids]
    total = len(tasks)
    subdirs = [_subdir_name(i + 1, t.id, total) for i, t in enumerate(tasks)]

    workspace_keepalive: TemporaryDirectory | None = None
    if keep_workspace:
        chain_root = Path(mkdtemp(prefix=f"hb_chain_{spec.chain_id}_"))
    else:
        workspace_keepalive = TemporaryDirectory(prefix=f"hb_chain_{spec.chain_id}_")
        chain_root = Path(workspace_keepalive.name)

    result = ChainRunResult(
        chain_id=spec.chain_id,
        task_ids=spec.task_ids,
        delivery="turns",
        turns=[
            TurnRecord(position=i + 1, task_id=t.id, subdir=subdirs[i])
            for i, t in enumerate(tasks)
        ],
        workspace=chain_root if keep_workspace else None,
    )

    try:
        agent = agent_factory(chain_root)
        messages: list[Any] = []

        for index, task in enumerate(tasks):
            turn = result.turns[index]
            position = index + 1
            turn_started = time.monotonic()
            task_workspace = _setup_task_subdir(task, chain_root, turn.subdir)
            turn.reached = True
            turn.context_messages_before = len(messages)
            turn.context_chars_before = _history_chars(messages)

            user_message = {
                "role": "user",
                "content": _turn_prompt(task, position, total, turn.subdir, virtual=True),
            }
            input_messages = [*messages, user_message]

            terminate_reason: str | None = None
            for attempt in range(1, transient_attempts + 1):
                stats = AgentRunStatsCollector()
                try:
                    invocation = _invoke_with_timeout(
                        agent, {"messages": input_messages}, stats, turn_timeout
                    )
                except TaskTimeoutError as exc:
                    turn.error = str(exc)
                    turn.stats = _turn_delta_stats(stats, [])
                    if isinstance(exc, ChainInvokeTimeout) and _settle_invocation(
                        exc, progress
                    ):
                        # The agent is still writing into this workspace, so
                        # every later turn would be verified against a tree
                        # that moves on its own. Stop here: a short chain with
                        # honest numbers beats a long one nobody can reproduce.
                        result.scoring_raced = True
                        terminate_reason = (
                            f"turn timeout at position {position} left the agent "
                            f"running; chain stopped so scoring stays meaningful"
                        )
                    break  # otherwise the turn failed and the chain goes on
                except Exception as exc:  # noqa: BLE001 — classified below
                    if _is_context_overflow_error(exc):
                        turn.error = f"context overflow: {exc}"
                        terminate_reason = (
                            f"context overflow at position {position}: {exc}"
                        )
                        break
                    if _is_graph_recursion_error(exc):
                        turn.error = "graph recursion limit reached"
                        turn.stats = _turn_delta_stats(stats, [])
                        break  # turn failed; chain continues
                    if _is_transient_model_error(exc):
                        if attempt < transient_attempts:
                            backoff = _TRANSIENT_BACKOFF_SCHEDULE[
                                min(attempt - 1, len(_TRANSIENT_BACKOFF_SCHEDULE) - 1)
                            ]
                            progress(
                                f"  [{spec.chain_id} {position:02d}/{total}] transient "
                                f"model error (attempt {attempt}/{transient_attempts}), "
                                f"retrying in {backoff}s: {exc}"
                            )
                            time.sleep(backoff)
                            continue
                        turn.error = (
                            f"transient model error after {transient_attempts} attempts: {exc}"
                        )
                        terminate_reason = turn.error
                        break
                    turn.error = f"runtime error: {traceback.format_exc()}"
                    terminate_reason = f"runtime error at position {position}: {exc}"
                    break
                else:
                    new_messages = (
                        invocation.get("messages") if isinstance(invocation, dict) else None
                    )
                    if isinstance(new_messages, list) and new_messages:
                        delta = new_messages[len(input_messages) :]
                        # Middleware may rewrite/trim history; whatever the graph
                        # returns is the session's canonical state going forward.
                        messages = new_messages
                    else:
                        delta = []
                        messages = input_messages
                    turn.stats = _turn_delta_stats(stats, delta)
                    verify = task.verify(task_workspace)
                    turn.passed_immediate = verify.passed
                    turn.message_immediate = verify.message
                    break

            turn.elapsed_seconds = time.monotonic() - turn_started
            status = "PASS" if turn.passed_immediate else "FAIL"
            notes = []
            if in_tokens := turn.stats.get("agent_input_tokens"):
                notes.append(f"in={in_tokens:,}tok")
            if turn.context_chars_before:
                notes.append(f"hist={turn.context_chars_before:,}ch")
            note = f" {' '.join(notes)}" if notes else ""
            progress(
                f"  [{spec.chain_id} {position:02d}/{total}] [{status}] "
                f"{task.id:42s} {turn.elapsed_seconds:6.1f}s{note}"
                + (f" — {turn.error.splitlines()[0][:100]}" if turn.error else "")
            )

            if on_turn is not None:
                on_turn(result)

            if terminate_reason is not None:
                result.terminated_at_position = position
                result.termination_reason = terminate_reason
                break

        _final_verify(result, tasks, chain_root)
        result.completed = not result.scoring_raced
        return result
    finally:
        result.elapsed_seconds = time.monotonic() - started
        if workspace_keepalive is not None:
            workspace_keepalive.cleanup()


# ---------------------------------------------------------------------------
# Chain execution — batch delivery (deepagents / CLI)
# ---------------------------------------------------------------------------


def run_chain_batch_agent(
    spec: ChainSpec,
    agent_factory: Any,
    *,
    keep_workspace: bool = False,
    chain_timeout: float | None = None,
    via_file: bool = False,
    progress: Any = print,
) -> ChainRunResult:
    started = time.monotonic()
    tasks = [get_task(task_id) for task_id in spec.task_ids]
    total = len(tasks)
    subdirs = [_subdir_name(i + 1, t.id, total) for i, t in enumerate(tasks)]
    timeout = chain_timeout or DEFAULT_CHAIN_SECONDS_PER_TASK * total

    workspace_keepalive: TemporaryDirectory | None = None
    if keep_workspace:
        chain_root = Path(mkdtemp(prefix=f"hb_chain_{spec.chain_id}_"))
    else:
        workspace_keepalive = TemporaryDirectory(prefix=f"hb_chain_{spec.chain_id}_")
        chain_root = Path(workspace_keepalive.name)

    result = ChainRunResult(
        chain_id=spec.chain_id,
        task_ids=spec.task_ids,
        delivery="batch",
        turns=[
            TurnRecord(position=i + 1, task_id=t.id, subdir=subdirs[i], reached=True)
            for i, t in enumerate(tasks)
        ],
        workspace=chain_root if keep_workspace else None,
    )

    try:
        for task, subdir in zip(tasks, subdirs, strict=True):
            _setup_task_subdir(task, chain_root, subdir)
        before = {subdir: _dir_fingerprint(chain_root / subdir) for subdir in subdirs}

        agent = agent_factory(chain_root)
        if via_file:
            (chain_root / TASK_LIST_FILENAME).write_text(
                _task_list_document(tasks, subdirs), encoding="utf-8"
            )
            prompt = _marathon_prompt(total, f"/{TASK_LIST_FILENAME}")
        else:
            prompt = _batch_prompt(tasks, subdirs, virtual=True)
        stats = AgentRunStatsCollector()
        try:
            invocation = _invoke_with_timeout(
                agent,
                {"messages": [{"role": "user", "content": prompt}]},
                stats,
                timeout,
            )
        except Exception as exc:  # noqa: BLE001 — chain-level failure, still verify
            if isinstance(exc, TaskTimeoutError):
                result.error = f"chain timed out after {timeout:.0f}s"
                if isinstance(exc, ChainInvokeTimeout):
                    result.scoring_raced = _settle_invocation(exc, progress)
            elif _is_graph_recursion_error(exc):
                result.error = "graph recursion limit reached"
            elif _is_context_overflow_error(exc):
                result.error = f"context overflow: {exc}"
            else:
                result.error = f"runtime error: {traceback.format_exc()}"
            result.termination_reason = result.error
            result.chain_stats = dict(stats.stats)
        else:
            messages = (
                invocation.get("messages") if isinstance(invocation, dict) else None
            )
            result.chain_stats = _turn_delta_stats(
                stats, messages if isinstance(messages, list) else []
            )

        _mark_untouched_as_not_reached(result, chain_root, before)
        _verify_batch_chain(result, tasks, chain_root, progress=progress)
        result.completed = not result.scoring_raced
        return result
    finally:
        result.elapsed_seconds = time.monotonic() - started
        if workspace_keepalive is not None:
            workspace_keepalive.cleanup()


def _verify_batch_chain(
    result: ChainRunResult,
    tasks: list[Task],
    chain_root: Path,
    *,
    progress: Any,
) -> None:
    """Score a batch/file chain: one verdict per task, taken at the chain's end.

    Deliberately leaves ``passed_immediate`` unset. The whole chain ran inside
    a single invocation, so there was never a moment between tasks at which to
    take a first verdict; copying the final one into it would manufacture a
    perfect retention score out of an unasked question.
    """
    total = len(result.turns)
    for task, turn in zip(tasks, result.turns, strict=True):
        head = f"  [{result.chain_id} {turn.position:02d}/{total}]"
        if not turn.reached:
            progress(f"{head} [----] {task.id}")
            continue
        verify = task.verify(chain_root / turn.subdir)
        turn.passed_final = verify.passed
        turn.message_final = verify.message
        progress(f"{head} [{'PASS' if verify.passed else 'FAIL'}] {task.id}")


def run_chain_batch_cli(
    spec: ChainSpec,
    cli_command: str,
    *,
    keep_workspace: bool = False,
    chain_timeout: float | None = None,
    via_file: bool = False,
    progress: Any = print,
) -> ChainRunResult:
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

    started = time.monotonic()
    tasks = [get_task(task_id) for task_id in spec.task_ids]
    total = len(tasks)
    subdirs = [_subdir_name(i + 1, t.id, total) for i, t in enumerate(tasks)]
    timeout = chain_timeout or DEFAULT_CHAIN_SECONDS_PER_TASK * total

    workspace_keepalive: TemporaryDirectory | None = None
    if keep_workspace:
        chain_root = Path(mkdtemp(prefix=f"hb_chain_{spec.chain_id}_"))
    else:
        workspace_keepalive = TemporaryDirectory(prefix=f"hb_chain_{spec.chain_id}_")
        chain_root = Path(workspace_keepalive.name)

    result = ChainRunResult(
        chain_id=spec.chain_id,
        task_ids=spec.task_ids,
        delivery="batch",
        turns=[
            TurnRecord(position=i + 1, task_id=t.id, subdir=subdirs[i], reached=True)
            for i, t in enumerate(tasks)
        ],
        workspace=chain_root if keep_workspace else None,
    )

    try:
        for task, subdir in zip(tasks, subdirs, strict=True):
            _setup_task_subdir(task, chain_root, subdir)
        before = {subdir: _dir_fingerprint(chain_root / subdir) for subdir in subdirs}

        base_argv = _ensure_cli_json_events(shlex.split(cli_command))
        argv = _argv_for_workspace(base_argv, chain_root)
        if via_file:
            (chain_root / TASK_LIST_FILENAME).write_text(
                _task_list_document(tasks, subdirs), encoding="utf-8"
            )
            prompt = _marathon_prompt(total, f"./{TASK_LIST_FILENAME}")
        else:
            prompt = _batch_prompt(tasks, subdirs, virtual=False)
        argv = [*argv, prompt]

        completed: subprocess.CompletedProcess[str] | None = None
        try:
            completed = _run_cli_subprocess(
                argv,
                cwd=chain_root,
                timeout=int(timeout),
                env=_subprocess_env_with_token(),
            )
        except subprocess.TimeoutExpired:
            result.error = f"chain CLI timed out after {timeout:.0f}s"
            result.termination_reason = result.error
        except FileNotFoundError as exc:
            result.error = f"CLI executable not found: {exc}"
            result.termination_reason = result.error
        else:
            if completed.returncode != 0:
                stderr_tail = (completed.stderr or "").strip().splitlines()[-3:]
                result.error = (
                    f"CLI exit={completed.returncode}: {' | '.join(stderr_tail)[:300]}"
                )
            stats = _codex_json_event_stats(completed.stdout or "")
            if stats is None:
                stats = _claude_json_event_stats(completed.stdout or "")
            if stats is None:
                stats = _gemini_json_event_stats(completed.stdout or "")
            if stats is None:
                stats = _grok_json_event_stats(
                    completed.stdout or "", workspace=chain_root
                )
            if stats is None:
                stats = _mini_swe_agent_traj_stats(chain_root)
            result.chain_stats = stats or {}

        _mark_untouched_as_not_reached(result, chain_root, before)
        _verify_batch_chain(result, tasks, chain_root, progress=progress)
        result.completed = True
        return result
    finally:
        result.elapsed_seconds = time.monotonic() - started
        if workspace_keepalive is not None:
            workspace_keepalive.cleanup()


def _final_verify(result: ChainRunResult, tasks: list[Task], chain_root: Path) -> None:
    """End-of-chain re-verification of every reached task (retention check)."""
    for task, turn in zip(tasks, result.turns, strict=True):
        if not turn.reached:
            turn.passed_final = False
            turn.message_final = (
                f"not reached: chain terminated at position "
                f"{result.terminated_at_position}"
            )
            continue
        verify = task.verify(chain_root / turn.subdir)
        turn.passed_final = verify.passed
        turn.message_final = verify.message


# ---------------------------------------------------------------------------
# Orchestration, JSON payload, summary
# ---------------------------------------------------------------------------


def chain_results_to_payload(
    results: list[ChainRunResult],
    *,
    delivery: str,
    backend_label: str,
    command: str | None = None,
) -> dict[str, Any]:
    turns = [turn for result in results for turn in result.turns]
    total = len(turns)
    final_passed = sum(1 for t in turns if t.passed_final)
    immediate_passed = sum(1 for t in turns if t.passed_immediate)
    retention_measured = any(result.retention_measured for result in results)
    chain_rates = [
        r.final_passed / len(r.task_ids) for r in results if r.task_ids
    ]

    flattened: list[dict[str, Any]] = []
    for result in results:
        for turn in result.turns:
            entry = turn.to_payload()
            entry["passed"] = turn.passed_final
            entry["message"] = turn.message_final or turn.message_immediate
            entry["chain_id"] = result.chain_id
            flattened.append(entry)

    payload: dict[str, Any] = {
        "task_set_version": TASK_SET_VERSION,
        "chainset_version": CHAINSET_VERSION,
        "mode": "chain",
        "delivery": delivery,
        "backend": backend_label,
    }
    if command:
        payload["command"] = command
    payload.update(
        {
            "chain_count": len(results),
            "total": total,
            "passed": final_passed,
            "pass_rate": (final_passed / total) if total else 0.0,
            "scoring_raced": any(result.scoring_raced for result in results),
            # None, not 0: batch/file deliveries verify once at the end, so
            # there is no earlier verdict to compare against. A zero here would
            # be read as "nothing was broken" when nothing was watched.
            "retention_measured": retention_measured,
            "immediate_passed": immediate_passed if retention_measured else None,
            "immediate_pass_rate": (
                (immediate_passed / total) if retention_measured and total else None
            ),
            "retention_broken": (
                sum(r.retention_broken or 0 for r in results)
                if retention_measured
                else None
            ),
            "marathon_score": (
                sum(chain_rates) / len(chain_rates) if chain_rates else 0.0
            ),
            "chains": [result.to_payload() for result in results],
            "tasks": flattened,
        }
    )
    return payload


def _write_chain_results_json(
    results: list[ChainRunResult],
    path: Path | None,
    *,
    delivery: str,
    backend_label: str,
    command: str | None,
) -> None:
    if path is None:
        return
    with _RESULTS_JSON_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                chain_results_to_payload(
                    results,
                    delivery=delivery,
                    backend_label=backend_label,
                    command=command,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def _turn_outcome(turn: TurnRecord) -> bool:
    """The verdict to score a turn by: its own if the delivery took one."""
    return turn.passed_final if turn.passed_immediate is None else turn.passed_immediate


def _position_quartile_rates(results: list[ChainRunResult]) -> list[tuple[str, int, int]]:
    """Pass counts bucketed by position quartile across chains.

    This is the depth-degradation read: with the order shuffled by seed, the
    quartiles hold equally hard tasks, so a falling tail is the long horizon
    and not the task mix.
    """
    buckets: dict[int, list[TurnRecord]] = {1: [], 2: [], 3: [], 4: []}
    for result in results:
        length = len(result.task_ids)
        if length == 0:
            continue
        for turn in result.turns:
            quartile = min(4, (turn.position - 1) * 4 // length + 1)
            buckets[quartile].append(turn)
    rows = []
    for quartile in (1, 2, 3, 4):
        turns = buckets[quartile]
        if turns:
            rows.append((f"Q{quartile}", sum(1 for t in turns if _turn_outcome(t)), len(turns)))
    return rows


def summarize_chains(results: list[ChainRunResult]) -> None:
    print()
    print("=" * 64)
    header = ["Chain", "Len", "Immediate", "Final", "Broken", "Died@", "Time"]
    def _cell(value: int | None) -> str:
        return "-" if value is None else str(value)

    rows = []
    for result in results:
        rows.append(
            [
                result.chain_id,
                str(len(result.task_ids)),
                _cell(result.immediate_passed),
                str(result.final_passed),
                _cell(result.retention_broken),
                str(result.terminated_at_position or "-"),
                f"{result.elapsed_seconds:.0f}s",
            ]
        )
    turns = [turn for result in results for turn in result.turns]
    total = len(turns)
    retention_measured = any(result.retention_measured for result in results)
    immediate = sum(1 for t in turns if t.passed_immediate)
    final = sum(1 for t in turns if t.passed_final)
    rows.append(
        [
            "Total",
            str(total),
            _cell(immediate if retention_measured else None),
            str(final),
            _cell(
                sum(r.retention_broken or 0 for r in results)
                if retention_measured
                else None
            ),
            "-",
            f"{sum(r.elapsed_seconds for r in results):.0f}s",
        ]
    )
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]

    def _fmt(row: list[str]) -> str:
        cells = [
            row[0].ljust(widths[0]),
            *[value.rjust(width) for value, width in zip(row[1:], widths[1:], strict=True)],
        ]
        return "  ".join(cells)

    print(_fmt(header))
    print(_fmt(["-" * w for w in widths]))
    for row in rows:
        print(_fmt(row))

    chain_rates = [r.final_passed / len(r.task_ids) for r in results if r.task_ids]
    marathon = sum(chain_rates) / len(chain_rates) if chain_rates else 0.0
    print()
    print(f"Marathon score (mean final pass over chains): {marathon * 100:.1f}%")
    if total:
        if retention_measured:
            print(
                f"Immediate pass: {immediate}/{total} ({immediate / total * 100:.1f}%)   "
                f"Final pass: {final}/{total} ({final / total * 100:.1f}%)"
            )
        else:
            print(f"Final pass: {final}/{total} ({final / total * 100:.1f}%)")
    if retention_measured:
        if immediate:
            survived = sum(1 for t in turns if t.passed_immediate and t.passed_final)
            print(
                f"Retention: {survived}/{immediate} solved tasks survived to chain end "
                f"({survived / immediate * 100:.1f}%)"
            )
    else:
        print(
            "Retention: not measured — this delivery verifies once, at the end of "
            "the chain, so there is no earlier verdict to break."
        )

    raced = [result for result in results if result.scoring_raced]
    if raced:
        print()
        print(
            f"WARNING: {', '.join(r.chain_id for r in raced)} scored while a "
            "timed-out agent was still running. The workspace could move under "
            "the verifier, so treat these numbers as provisional."
        )

    quartiles = _position_quartile_rates(results)
    if quartiles:
        print()
        label = "Immediate" if retention_measured else "Final"
        print(f"{label} pass by position quartile:")
        print(
            "  "
            + "   ".join(
                f"{label}: {passed}/{count} ({passed / count * 100:.0f}%)"
                for label, passed, count in quartiles
            )
        )

    failures = [
        (result.chain_id, turn)
        for result in results
        for turn in result.turns
        if not turn.passed_final
    ]
    if failures:
        print()
        print("Failures (final):")
        for chain_id, turn in failures:
            if not turn.reached:
                detail = turn.message_final
            elif turn.passed_immediate:
                detail = f"broken later: {turn.message_final or 'final verify failed'}"
            elif turn.error:
                detail = turn.error.splitlines()[0][:160]
            else:
                detail = turn.message_immediate or turn.message_final or "failed"
            print(f"  - {chain_id}#{turn.position:02d} {turn.task_id}: {detail}")

    terminations = [r for r in results if r.termination_reason]
    if terminations:
        print()
        print("Terminated chains:")
        for result in terminations:
            print(
                f"  - {result.chain_id} died at position "
                f"{result.terminated_at_position}: {result.termination_reason[:200]}"
            )


def _resume_completed_chains(
    json_output: Path | None, requested: list[str]
) -> dict[str, ChainRunResult]:
    """Load chains already completed in a previous run of the same output file.

    Keyed on ``completed``, which now means "scored and trustworthy" rather
    than "the runner reached the end of the function". A chain that hit its
    step cap or that the agent abandoned early is a result and is kept; one
    scored against a workspace a timed-out agent was still writing to is not,
    and comes back as pending so the resume re-runs it.
    """
    if json_output is None or not json_output.exists():
        return {}
    try:
        payload = json.loads(json_output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    completed: dict[str, ChainRunResult] = {}
    for chain_payload in payload.get("chains") or []:
        chain_id = chain_payload.get("chain_id")
        if chain_id not in requested or not chain_payload.get("completed"):
            continue
        turns = []
        for turn_payload in chain_payload.get("turns") or []:
            stats = {
                key: value
                for key in _TURN_STAT_KEYS
                if (value := turn_payload.get(key)) is not None
            }
            immediate = turn_payload.get("passed_immediate")
            turns.append(
                TurnRecord(
                    position=turn_payload.get("position", 0),
                    task_id=turn_payload.get("task_id", ""),
                    subdir=turn_payload.get("subdir", ""),
                    reached=bool(turn_payload.get("reached")),
                    # Kept nullable on purpose: collapsing "not measured" into
                    # False here would resurrect the fake-100%-retention bug on
                    # every resumed run.
                    passed_immediate=None if immediate is None else bool(immediate),
                    message_immediate=turn_payload.get("message_immediate") or "",
                    passed_final=bool(turn_payload.get("passed_final")),
                    message_final=turn_payload.get("message_final") or "",
                    elapsed_seconds=turn_payload.get("elapsed_seconds") or 0.0,
                    error=turn_payload.get("error"),
                    context_messages_before=turn_payload.get("context_messages_before"),
                    context_chars_before=turn_payload.get("context_chars_before"),
                    stats=stats,
                )
            )
        completed[chain_id] = ChainRunResult(
            chain_id=chain_id,
            task_ids=tuple(chain_payload.get("task_ids") or ()),
            delivery=chain_payload.get("delivery") or "turns",
            turns=turns,
            terminated_at_position=chain_payload.get("terminated_at_position"),
            termination_reason=chain_payload.get("termination_reason"),
            elapsed_seconds=chain_payload.get("elapsed_seconds") or 0.0,
            error=chain_payload.get("error"),
            scoring_raced=bool(chain_payload.get("scoring_raced")),
            chain_stats=chain_payload.get("chain_stats") or {},
            completed=True,
        )
    return completed


def _planned_chain(spec: ChainSpec, delivery: str) -> ChainRunResult:
    """A not-yet-run chain, so a partial artifact still states the plan."""
    subdirs = [
        _subdir_name(position, task_id, spec.length)
        for position, task_id in enumerate(spec.task_ids, start=1)
    ]
    return ChainRunResult(
        chain_id=spec.chain_id,
        task_ids=spec.task_ids,
        delivery=delivery,
        turns=[
            TurnRecord(position=position, task_id=task_id, subdir=subdirs[position - 1])
            for position, task_id in enumerate(spec.task_ids, start=1)
        ],
    )


def run_chains(
    chain_ids: list[str],
    *,
    delivery: str = "turns",
    backend: str = "gigachat",
    model_name: str | None = None,
    cli_command: str | None = None,
    recursion_limit: int | None = None,
    max_tokens: int | None = None,
    harness_profile: str | None = None,
    keep_workspace: bool = False,
    turn_timeout: float | None = None,
    chain_timeout: float | None = None,
    transient_attempts: int | None = None,
    concurrency: int = 1,
    context_window: int | None = None,
    forward_reasoning_history: bool = False,
    json_output: str | Path | None = None,
) -> list[ChainRunResult]:
    """Run the requested chains and return their results (see module docstring)."""
    _load_env_from_dotenv()
    json_path = normalize_json_output_path(json_output)

    specs = [get_chain(chain_id) for chain_id in chain_ids]
    via_file = delivery == "file"
    # Both settings are per-turn, and batch/file deliveries have exactly one
    # turn. Saying so beats letting an operator set a timeout that never
    # applies and read the resulting run as if it had.
    if delivery in ("batch", "file"):
        ignored = [
            name
            for name, value in (
                ("--turn-timeout", turn_timeout),
                ("--transient-attempts", transient_attempts),
            )
            if value is not None
        ]
        if ignored:
            print(
                f"Note: {' and '.join(ignored)} — turns delivery only, ignored "
                f"here. This chain is a single invocation: cap it with "
                f"--chain-timeout, and transient errors are retried inside the "
                f"model client."
            )
    turn_timeout = (
        DEFAULT_TURN_TIMEOUT_SECONDS if turn_timeout is None else turn_timeout
    )
    transient_attempts = (
        DEFAULT_TRANSIENT_ATTEMPTS if transient_attempts is None else transient_attempts
    )
    if cli_command is not None:
        if delivery not in ("batch", "file"):
            raise SystemExit(
                "--cli-command supports only --delivery batch/file (turns delivery "
                "needs a resumable in-process agent; that is a later phase)."
            )
        backend_label = f"cli:{cli_command}"
    else:
        backend_label = (
            f"openrouter:{model_name}" if backend == "openrouter" else backend
        )

    completed = _resume_completed_chains(json_path, [s.chain_id for s in specs])
    if completed:
        print(
            f"Resuming: {len(completed)} chain(s) already completed in "
            f"{json_path} — {', '.join(sorted(completed))}"
        )
    pending = [spec for spec in specs if spec.chain_id not in completed]
    results: list[ChainRunResult] = [
        completed[spec.chain_id] for spec in specs if spec.chain_id in completed
    ]

    def _write_partial(extra: ChainRunResult | None = None) -> None:
        done = {result.chain_id: result for result in results}
        if extra is not None:
            done[extra.chain_id] = extra
        # Chains that have not produced a result yet go in as placeholders, so
        # the file always describes the whole plan. batch/file deliveries write
        # nothing until a chain ends — hours on a marathon — and a run killed
        # before that used to leave no record of what it was even running.
        snapshot = [
            done.get(spec.chain_id) or _planned_chain(spec, delivery) for spec in specs
        ]
        _write_chain_results_json(
            snapshot,
            json_path,
            delivery=delivery,
            backend_label=backend_label,
            command=results_json_command(),
        )

    def _run_one(spec: ChainSpec) -> ChainRunResult:
        print(f"[CHAIN {spec.chain_id}] {spec.length} tasks, delivery={delivery}")
        if cli_command is not None:
            return run_chain_batch_cli(
                spec,
                cli_command,
                keep_workspace=keep_workspace,
                chain_timeout=chain_timeout,
                via_file=via_file,
            )
        if delivery in ("batch", "file"):
            # ~4-8 agent steps per task empirically; the 1200 cap is what a
            # 20-task chain needs many times over but starves a 342-task
            # marathon, so long chains must raise it explicitly.
            batch_recursion = recursion_limit or min(1200, 60 * spec.length)
            factory = make_agent_factory(
                backend,
                model_name=model_name,
                recursion_limit=batch_recursion,
                max_tokens=max_tokens,
                harness_profile=harness_profile,
                context_window=context_window,
                forward_reasoning_history=forward_reasoning_history,
            )
            return run_chain_batch_agent(
                spec,
                factory,
                keep_workspace=keep_workspace,
                chain_timeout=chain_timeout,
                via_file=via_file,
            )
        factory = make_agent_factory(
            backend,
            model_name=model_name,
            recursion_limit=recursion_limit or 80,
            max_tokens=max_tokens,
            harness_profile=harness_profile,
            context_window=context_window,
            forward_reasoning_history=forward_reasoning_history,
        )
        # Per-turn checkpointing matters on long chains (E100 runs for over an
        # hour): a crash mid-chain must not lose the finished turns. Only safe
        # single-threaded — with chain concurrency the callback would race.
        on_turn = _write_partial if concurrency <= 1 else None
        return run_chain_turns(
            spec,
            factory,
            keep_workspace=keep_workspace,
            turn_timeout=turn_timeout,
            transient_attempts=transient_attempts,
            on_turn=on_turn,
        )

    _write_partial()  # the plan on disk before the first agent starts
    if concurrency <= 1 or len(pending) <= 1:
        for spec in pending:
            result = _run_one(spec)
            results.append(result)
            _write_partial()
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(_run_one, spec): spec for spec in pending}
            for future in as_completed(futures):
                results.append(future.result())
                _write_partial()

    results.sort(key=lambda r: chain_ids.index(r.chain_id))
    _write_partial()
    return results
