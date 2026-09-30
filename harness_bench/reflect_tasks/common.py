"""Shared machinery for the tool-reflection suite (``--suite reflect``).

Each task ships a closed command-line client, ``tools/<name>``, for a small
stateful service, plus documentation in ``docs/<name>.md``. The documentation
describes how the service is *supposed* to behave; the service deviates from it
in two or three ways that show up only in its responses: an ambiguous refusal
for correct arguments, a ``"status": "ok"`` that did less than asked, a field
the docs never mention. Following the docs literally fails; the agent has to
read what the tool actually returned and adapt.

How a task is built:

- The service is Python source (standard library only) defining
  ``initial_state() -> dict`` and ``handle(state, words, opts, io) -> (dict, int)``.
  ``handle`` mutates ``state`` and returns the JSON response and exit code. It
  must be deterministic: no randomness, clock or environment. ``state["clock"]``
  is a logical clock, incremented once per call before ``handle`` runs.
- ``io.read(path)`` reads a workspace file for commands that take one; its
  content is recorded in the journal so a replay sees the same bytes.
- The client appends every call (argv plus files read) to
  ``.svc/journal.jsonl``, each entry chained with an HMAC under a key compiled
  into the client. The verifier replays the journal through the same source
  from ``initial_state()`` and checks the resulting state. Editing
  ``.svc/state.bin`` changes nothing; editing or dropping journal entries fails
  the integrity check.
- A check returns ``None`` on success or a short failure reason. It must not
  leak hidden values beyond what the prompt and the tool already show.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import subprocess
import sys
import textwrap
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness_bench.core import Task, VerifyResult

SVC_DIR = ".svc"
STATE_FILE = f"{SVC_DIR}/state.bin"
JOURNAL_FILE = f"{SVC_DIR}/journal.jsonl"
REFLECT_TIMEOUT_SECONDS = 1800.0
REFLECT_RECURSION_LIMIT = 400

# Shared by the client and the verifier: argv parsing, file IO, dispatch.
_RUNTIME = r'''
import json as _json


def _parse(argv):
    """`verb sub --key value --flag --key=value` -> (words, opts); repeated keys become lists."""
    words, opts, i = [], {}, 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("--") and len(arg) > 2:
            key = arg[2:]
            if "=" in key:
                key, val = key.split("=", 1)
            elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                val = argv[i + 1]
                i += 1
            else:
                val = True
            if key in opts:
                prev = opts[key] if isinstance(opts[key], list) else [opts[key]]
                opts[key] = prev + [val]
            else:
                opts[key] = val
        else:
            words.append(arg)
        i += 1
    return words, opts


class _IO:
    def __init__(self, root=None, recorded=None):
        self.root = root
        self.replay = recorded is not None
        self.files = dict(recorded or {})

    def read(self, rel):
        rel = str(rel)
        if self.replay:
            if rel not in self.files:
                raise FileNotFoundError(rel)
            return self.files[rel]
        base = self.root.resolve()
        path = (base / rel).resolve()
        if path != base and base not in path.parents:
            raise FileNotFoundError(rel)
        text = path.read_text(encoding="utf-8")
        self.files[rel] = text
        return text


def _dispatch(state, argv, io):
    state["clock"] = state.get("clock", 0) + 1
    words, opts = _parse(list(argv))
    try:
        return handle(state, words, opts, io)
    except FileNotFoundError as exc:
        return {"status": "error", "error": f"file not found: {exc}"}, 2
'''

# Client only: load state, record the call, save state, print the response.
_LIVE = r'''
import base64 as _b64, hashlib as _hl, hmac as _hm, sys as _sys, zlib as _zl
from pathlib import Path as _Path


def _main():
    root = _Path(__file__).resolve().parent.parent
    svc = root / ".svc"
    lock = None
    try:
        import fcntl as _fcntl
        lock = open(svc / ".lock", "w")
        _fcntl.flock(lock, _fcntl.LOCK_EX)
    except ImportError:
        pass
    state = _json.loads(_zl.decompress(_b64.b85decode((svc / "state.bin").read_bytes())))
    jpath = svc / "journal.jsonl"
    lines = jpath.read_text(encoding="utf-8").splitlines() if jpath.exists() else []
    prev = _json.loads(lines[-1])["mac"] if lines else ""
    argv = _sys.argv[1:]
    io = _IO(root=root)
    resp, code = _dispatch(state, argv, io)
    entry = {"n": len(lines), "argv": argv, "files": io.files}
    body = _json.dumps(entry, ensure_ascii=False, sort_keys=True)
    entry["mac"] = _hm.new(_KEY, (prev + body).encode(), _hl.sha256).hexdigest()
    with jpath.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(_json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    (svc / "state.bin").write_bytes(_b64.b85encode(_zl.compress(_json.dumps(state).encode())))
    if lock is not None:
        lock.close()
    print(_json.dumps(resp, ensure_ascii=False, indent=2))
    _sys.exit(code)


_main()
'''


@dataclass(frozen=True)
class Service:
    """One simulated service: its client name, a title, the source and the journal key."""

    tool: str
    title: str
    source: str
    key: bytes

    def namespace(self) -> dict[str, Any]:
        ns: dict[str, Any] = {"__name__": f"reflect_service_{self.tool}"}
        exec(compile(_RUNTIME + textwrap.dedent(self.source), f"<{self.tool}>", "exec"), ns)  # noqa: S102
        return ns

    def initial_state(self) -> dict[str, Any]:
        state = self.namespace()["initial_state"]()
        state.setdefault("clock", 0)
        return json.loads(json.dumps(state))

    def client_script(self) -> str:
        code = _RUNTIME + textwrap.dedent(self.source) + f"\n_KEY = {self.key!r}\n" + _LIVE
        blob = base64.b85encode(zlib.compress(code.encode("utf-8"), 9)).decode("ascii")
        chunks = "\n".join(f'    "{blob[i:i + 96]}"' for i in range(0, len(blob), 96))
        return (
            "#!/usr/bin/env python3\n"
            f'"""{self.tool}: command-line client for {self.title}.\n\n'
            f"Usage: python3 tools/{self.tool} <command> [--option value ...]\n"
            f"Documentation: docs/{self.tool}.md. The client is distributed closed;\n"
            'use it only through its commands."""\n'
            "import base64, zlib\n"
            "exec(zlib.decompress(base64.b85decode(\n"
            f"{chunks}\n"
            ")))\n"
        )

    def state_blob(self) -> str:
        return base64.b85encode(zlib.compress(json.dumps(self.initial_state()).encode())).decode("ascii")


@dataclass
class Replay:
    state: dict[str, Any]
    calls: list[tuple[list[str], dict[str, Any], int]]


def replay(ws: Path, service: Service) -> Replay | str:
    """Re-run the journal from the initial state; a string is an integrity error."""
    ns = service.namespace()
    state = service.initial_state()
    path = ws / JOURNAL_FILE
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prev = ""
    calls: list[tuple[list[str], dict[str, Any], int]] = []
    for index, line in enumerate(lines):
        try:
            entry = json.loads(line)
            mac = entry.pop("mac")
        except (ValueError, KeyError):
            return f"service journal entry {index + 1} is not a recorded call"
        body = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        expected = hmac.new(service.key, (prev + body).encode(), hashlib.sha256).hexdigest()
        if entry.get("n") != index or not hmac.compare_digest(str(mac), expected):
            return f"service journal was altered at entry {index + 1}; only calls through tools/{service.tool} count"
        prev = mac
        resp, code = ns["_dispatch"](state, entry["argv"], ns["_IO"](recorded=entry.get("files", {})))
        state = json.loads(json.dumps(state))
        calls.append((list(entry["argv"]), resp, code))
    return Replay(state=state, calls=calls)


def call(ws: Path, service: Service, *argv: str) -> tuple[dict[str, Any], int]:
    """Invoke the shipped client the way an agent would (used by gold solutions)."""
    proc = subprocess.run(
        [sys.executable, str(ws / "tools" / service.tool), *argv],
        cwd=ws,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    try:
        return json.loads(proc.stdout), proc.returncode
    except ValueError as exc:
        raise RuntimeError(f"{service.tool} {' '.join(argv)} printed no JSON: {proc.stderr[-500:]}") from exc


Check = Callable[[Replay, Path], str | None]


def reflect_task(
    *,
    id: str,  # noqa: A002 — mirrors Task.id
    name: str,
    prompt: str,
    service: Service,
    docs: str,
    check: Check,
    gold: Callable[[Path], None],
    extra_files: dict[str, str] | None = None,
    tags: Sequence[str] = (),
    min_calls: int = 1,
) -> Task:
    """Assemble a reflection task: client, docs, initial state, replay verifier."""
    files = {
        f"tools/{service.tool}": service.client_script(),
        f"docs/{service.tool}.md": textwrap.dedent(docs).lstrip(),
        STATE_FILE: service.state_blob(),
        **(extra_files or {}),
    }

    def verify(ws: Path) -> VerifyResult:
        result = replay(ws, service)
        if isinstance(result, str):
            return VerifyResult(False, result)
        if len(result.calls) < min_calls:
            return VerifyResult(False, f"tools/{service.tool} was called {len(result.calls)} times")
        reason = check(result, ws)
        return VerifyResult(reason is None, reason or "service state matches the goal")

    return Task(
        id=id,
        name=name,
        prompt=textwrap.dedent(prompt).strip(),
        verifier=verify,
        setup_files=files,
        gold_callback=gold,
        tags=("reflect", "tools", *tags),
        min_timeout_seconds=REFLECT_TIMEOUT_SECONDS,
        min_recursion_limit=REFLECT_RECURSION_LIMIT,
    )


PROMPT_RULES = """
Работайте с сервисом только через `python3 tools/{tool} …`; описание команд — в
`docs/{tool}.md`. Не читайте и не меняйте каталог `.svc/` и внутренности клиента:
засчитываются только вызовы через клиент, их журнал проверяется.
"""
