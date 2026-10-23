"""Wave 20 interactive workflows over the existing trusted service boundary.

Only this builder runs on the host. Agents receive the public transport,
complete task documentation and local fixtures, never service source/state.
Recoverable application errors are evidence to reason about, not automatic
failures; the task's final invariant and documented safety rules are scored.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from harness_bench.core import Task, VerifyResult
from harness_bench.feasibility_tasks.trusted_service import (
    CLIENT_SOURCE,
    CONNECTION_FILE,
    MAX_FILE_BYTES,
    MAX_HISTORY_BYTES,
    MAX_REQUEST_BYTES,
    MAX_TOTAL_FILE_BYTES,
    MAX_TOTAL_REQUEST_BYTES,
    PROTOCOL,
    close_session,
    get_session,
    service_fingerprint,
    start_session,
)
from harness_bench.general_hard.common import read_file
from harness_bench.reflect_tasks.common import Service, call

_PUBLIC_TOOL = Service("taskctl", "public workflow transport", "", b"")


@dataclass(frozen=True)
class WorkflowDefinition:
    """Host-only ingredients for composing existing interactive workflows."""

    source: str
    check: Callable[[dict[str, Any]], str | None]
    gold: Callable[[Path], None]
    docs: str
    files: dict[str, str]
    max_calls: int


_DEFINITIONS: dict[str, WorkflowDefinition] = {}


def workflow_definition(task: Task) -> WorkflowDefinition:
    """Look up by complete identity, so constructing variants cannot collide."""
    return _DEFINITIONS[task.runtime_protocol]


def request(workspace: Path, *argv: str) -> tuple[dict[str, Any], int]:
    """Gold/test helper that invokes exactly the agent's public CLI transport."""
    return call(workspace, _PUBLIC_TOOL, *argv)


def interactive_task(
    base: Task,
    *,
    prompt: str,
    docs: str,
    source: str,
    check: Callable[[dict[str, Any]], str | None],
    gold: Callable[[Path], None],
    files: dict[str, str] | None = None,
    tags: tuple[str, ...] = (),
    max_calls: int = 160,
    name: str | None = None,
    revision: str = "wave20-interactive-v8",
    suite_tags: tuple[str, ...] = ("general-hard", "wave20", "interactive", "hardening-v8"),
) -> Task:
    """Build a replayable task; ``check`` returns None iff its goal is met.

    Source defines initial_state() and handle(state, words, opts, io).
    Mutable facts must be exposed by documented commands. No check may require
    one particular trace, unless that action is itself an explicit task goal.
    All service requests, including unsuccessful calls, count toward max_calls.
    """
    if type(max_calls) is not int or not 1 <= max_calls <= 500:
        raise ValueError("max_calls must be between 1 and 500")
    boundary = (
        f"\n\nService call limit: {max_calls}, including reads, help and failed "
        "calls. It is separate from harness graph/model/tool-call limits. "
        "Calls are deterministic; real waiting does not change service state. "
        "Any logical time or visibility changes are described below. "
        "You may write scripts and use the public client in loops. "
        "Reading or replacing the transport does not reset service history. "
        "A command may return invalid_arguments for malformed arguments or "
        "unreadable request files; in that case domain state is unchanged "
        "(the call still counts). "
        "Malformed transport requests or exceeding its byte/resource limits "
        "invalidate execution; documented command-level errors are recoverable. "
        f"Transport limits (UTF-8 bytes): {MAX_REQUEST_BYTES} per request, "
        f"{MAX_TOTAL_REQUEST_BYTES} across request envelopes, {MAX_FILE_BYTES} "
        f"per submitted file read, {MAX_TOTAL_FILE_BYTES} across submitted file "
        f"reads, and {MAX_HISTORY_BYTES} for serialized history. Re-reading a "
        "submission counts its bytes again. File reads refused by these bounds "
        "return recoverable invalid_arguments.\n"
    )
    fixed = {"tools/taskctl": CLIENT_SOURCE, "docs/taskctl.md": docs + boundary}
    for relative, text in (files or {}).items():
        path = PurePosixPath(relative)
        if (
            not path.parts or path.is_absolute() or ".." in path.parts
            or path.parts[0] == ".svc" or relative in fixed
        ):
            raise ValueError(f"reserved fixture path: {relative}")
        fixed[relative] = text
    limited_source = source + f'''

import copy as _workflow_copy

_workflow_handle = handle
def handle(state, words, opts, io):
    if state["clock"] > {max_calls}:
        return {{"status": "budget_exhausted", "limit": {max_calls}}}, 2
    before = _workflow_copy.deepcopy(state)
    try:
        response, code = _workflow_handle(state, words, opts, io)
        # A later state mutation must never rewrite an earlier recorded response.
        return _workflow_copy.deepcopy(response), code
    except (KeyError, IndexError, TypeError, ValueError, OverflowError, FileNotFoundError):
        state.clear()
        state.update(before)
        return {{"status": "invalid_arguments", "documentation": "docs/taskctl.md"}}, 2
'''
    service = Service("taskctl", name or base.name, limited_source, b"")
    fingerprint = service_fingerprint(service, base.id, fixed)

    def setup(workspace: Path) -> None:
        start_session(workspace, service, base.id, fingerprint)

    def verify(workspace: Path) -> VerifyResult:
        try:
            session = get_session(workspace)
            connection = json.dumps(session.connection) + "\n"
            expected = {**fixed, CONNECTION_FILE: connection}
            if (workspace / ".svc").is_symlink():
                return VerifyResult(False, "service directory must remain unchanged")
            if {p.name for p in (workspace / ".svc").iterdir()} != {"connection.json"}:
                return VerifyResult(False, "unexpected service connection files")
            for relative, text in expected.items():
                if read_file(workspace, relative) != text.encode("utf-8"):
                    return VerifyResult(False, f"immutable input modified: {relative}")
            replayed = session.replay(service=service, task_id=base.id, fingerprint=fingerprint)
            if any(argv == ["<transport>"] for argv, _, _ in replayed.calls):
                return VerifyResult(False, "malformed transport or transport resource limit exceeded")
            if len(replayed.calls) > max_calls:
                return VerifyResult(False, f"service call budget exceeded: {max_calls}")
            reason = check(replayed.state)
            if reason is not None:
                return VerifyResult(False, reason)
            return VerifyResult(True, f"goal and safety invariants met; {len(replayed.calls)} calls")
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            return VerifyResult(False, "invalid trusted history, service connection or immutable input")

    task = Task(
        id=base.id,
        name=name or base.name,
        prompt=prompt + (
            "\n\nЭто интерактивная задача. Начни с docs/taskctl.md и файлов условия. "
            "Клиент: python3 tools/taskctl. Все необходимые правила доступны локально; "
            "внешние знания и сеть не нужны. Можно писать свои скрипты. "
            f"Лимит сервиса — {max_calls} запросов, включая неудачные и read-only. "
            "Это не число шагов графа харнесса: его эффективный лимит сообщается "
            "отдельно в конфигурации запуска. Максимум времени агента — 900 секунд. "
            "После достижения цели заверши диалог в пределах этого времени. "
            "Проверяется состояние сервиса и история явно запрещённых действий, "
            "а не текст финального ответа. Исходные файлы, документацию, tools/taskctl "
            "и .svc/connection.json менять нельзя; собственные файлы создавай отдельно. "
            "Состояние и история находятся вне рабочей папки; подмена локальных "
            "файлов не отменяет совершённых действий. Неудачный запрос сам по себе "
            "не означает проигрыш, если не нарушено явно указанное ограничение."
        ),
        setup_files=fixed,
        setup_callback=setup,
        gold_callback=gold,
        verifier=verify,
        cleanup_callback=close_session,
        requires_trusted_service=True,
        runtime_protocol=f"{PROTOCOL}:{revision}:{fingerprint}",
        min_timeout_seconds=900,
        max_timeout_seconds=900,
        tags=(*suite_tags,
              *(tag for tag in base.tags if tag.startswith("parent-")), *tags),
    )
    _DEFINITIONS[task.runtime_protocol] = WorkflowDefinition(
        source=source, check=check, gold=gold, docs=docs,
        files=dict(files or {}), max_calls=max_calls,
    )
    return task
