"""Host-owned wave 19 service. Only its public socket enters the agent sandbox.

This module is imported by the trusted runner, never by the task client. A
filesystem sandbox is required for scored agents: native Python callers and
gold checks are trusted diagnostics, not an isolation boundary.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
import socket
import socketserver
import stat
import tempfile
import threading
import time
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from pathlib import Path

from harness_bench.feasibility_tasks.io_utils import _read_regular
from harness_bench.reflect_tasks.common import Replay, Service

PROTOCOL = "wave19-host-service-v2-completion"
MAX_REQUEST_BYTES = 65_536
MAX_REQUESTS = 4096
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_REQUEST_BYTES = 2_000_000
MAX_TOTAL_FILE_BYTES = 4_000_000
MAX_HISTORY_BYTES = 8_000_000
MAX_EVIDENCE_BYTES = 16_000_000
CONNECTION_FILE = ".svc/connection.json"


def trusted_service_available() -> bool:
    """Whether this Python provides the Unix-socket diagnostic transport."""
    return hasattr(socket, "AF_UNIX") and hasattr(socketserver, "UnixStreamServer")

# No task source, hidden configuration, target, signing key, or editable history.
# The socket location is public. Reading or reimplementing this transport is safe:
# every request still goes through the same host-side state machine.
CLIENT_SOURCE = '''#!/usr/bin/env python3
"""Public transport for the task service; usage: taskctl COMMAND [OPTIONS]."""
import json
import socket
import sys
from pathlib import Path

try:
    root = Path(__file__).resolve().parent.parent
    connection = json.loads((root / ".svc/connection.json").read_text(encoding="utf-8"))
    request = json.dumps({"argv": sys.argv[1:]}, ensure_ascii=False).encode("utf-8") + b"\\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(10)
        sock.connect(connection["socket"])
        sock.sendall(request)
        with sock.makefile("rb") as stream:
            raw = stream.readline(2000001)
        result = json.loads(raw)
    print(json.dumps(result["response"], ensure_ascii=False, indent=2))
    sys.exit(result["code"])
except (OSError, ValueError, KeyError) as exc:
    print(json.dumps({"status": "error", "message": "task service unavailable: " + str(exc)}))
    sys.exit(2)
'''


def _decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def service_fingerprint(service: Service, task_id: str, fixed: dict[str, str]) -> str:
    payload = {"protocol": PROTOCOL, "task_id": task_id, "source": service.source, "fixed": fixed}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _positive_seconds(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _terminal_record(namespace, argv, code):
    words, _ = namespace["_parse"](argv)
    return code == 0 and words in (["submit"], ["finish"])


def _completion_receipt(payload):
    """Read host timing, never workspace mtimes or agent-reported timestamps.

    A receipt proves a timely terminal service call, not that its answer is
    correct. The task verifier must still validate the entire trusted history.
    """
    attempt = payload.get("attempt")
    if attempt is None:
        return None  # Unarmed sessions are gold/diagnostic checks only.
    if (
        not isinstance(attempt, dict)
        or set(attempt) != {"timeout_seconds"}
        or not _positive_seconds(attempt["timeout_seconds"])
    ):
        raise ValueError("invalid trusted attempt timing")
    timeout = attempt["timeout_seconds"]
    previous = 0.0
    receipt = None
    for index, record in enumerate(payload["records"]):
        elapsed = record["elapsed_seconds"]
        if (
            type(elapsed) not in (int, float)
            or not math.isfinite(elapsed)
            or elapsed < previous
        ):
            raise ValueError("invalid trusted request timing")
        previous = elapsed
        if type(record["terminal"]) is not bool:
            raise ValueError("invalid trusted completion marker")
        if record["terminal"] and record["code"] == 0 and elapsed <= timeout:
            receipt = {
                "record_index": index,
                "elapsed_seconds": elapsed,
                "timeout_seconds": timeout,
            }
    return receipt


class WorkspaceIO:
    """Read a bounded regular file beneath a pinned workspace directory.

    Resolve documented in-workspace symlinks first, then open every resolved
    path component with O_NOFOLLOW and dir_fd. A concurrent symlink replacement
    can fail the call but cannot redirect the trusted server into host files.
    """

    def __init__(self, workspace: Path, remaining_bytes=MAX_TOTAL_FILE_BYTES):
        self.workspace = workspace
        self.files: dict[str, str] = {}
        self.remaining_bytes = remaining_bytes

    def read(self, relative):
        name = str(relative)
        descriptors = []
        maximum = min(MAX_FILE_BYTES, self.remaining_bytes)
        try:
            resolved = (self.workspace / name).resolve(strict=True)
            parts = resolved.relative_to(self.workspace).parts
            if not parts:
                raise ValueError("not a file")
            if os.open not in os.supports_dir_fd:
                # Windows gold diagnostics only; scored service requires Linux/bwrap.
                data = _read_regular(resolved, maximum)
            else:
                directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                directory = os.open(self.workspace, directory_flags)
                descriptors.append(directory)
                for part in parts[:-1]:
                    directory = os.open(part, directory_flags, dir_fd=directory)
                    descriptors.append(directory)
                fd = os.open(
                    parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                )
                descriptors.append(fd)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                    raise ValueError("expected a bounded regular file")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    data = stream.read(maximum + 1)
                if len(data) > maximum:
                    raise ValueError("file too large")
            text = data.decode("utf-8")
            if len(data) > self.remaining_bytes:
                raise ValueError("total submission file budget exceeded")
            self.remaining_bytes -= len(data)
            self.files[name] = text
            return text
        except (OSError, ValueError, RuntimeError) as exc:
            raise FileNotFoundError(name) from exc
        finally:
            for fd in reversed(descriptors):
                os.close(fd)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        session = self.server.session
        self.connection.settimeout(0.5)
        try:
            raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
                raise ValueError("invalid or oversized request")
            request = _decode(raw)
            if (
                not isinstance(request, dict)
                or set(request) != {"argv"}
                or not isinstance(request["argv"], list)
                or len(request["argv"]) > 128
                or not all(isinstance(arg, str) for arg in request["argv"])
            ):
                raise ValueError("request must contain only an argv string array")
            result = session.dispatch(request["argv"], len(raw))
        except (OSError, ValueError, TypeError, RecursionError):
            result = session.protocol_error("malformed or incomplete transport request")
        # The request remains recorded even if its caller disconnects.
        with suppress(OSError):
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b"\n")


class HostSession:
    def __init__(self, workspace: Path, service: Service, task_id: str, fingerprint: str):
        if not trusted_service_available():
            raise RuntimeError(
                "Wave 19 requires Unix sockets (AF_UNIX); this Python/platform does not "
                "support its host service. Scored runs require Linux/bubblewrap."
            )
        self.workspace = workspace.resolve()
        self.service = service
        self.task_id = task_id
        self.fingerprint = fingerprint
        self.namespace = service.namespace()
        self.state = service.initial_state()
        self.records: list[dict] = []
        self.protocol_errors: list[str] = []
        self.requests_seen = 0
        self.request_bytes = 0
        self.file_bytes = 0
        self.history_bytes = 0
        self._started_at = None
        self._timeout_seconds = None
        self.frozen = False
        self._closed = False
        self._lock = threading.RLock()
        # Keep AF_UNIX addresses short even on macOS or under long job paths.
        self._directory = tempfile.TemporaryDirectory(
            prefix="hbs-", dir="/tmp" if os.name == "posix" else None
        )
        self.socket_path = Path(self._directory.name) / "task.sock"
        self.connection = {"protocol": PROTOCOL, "socket": str(self.socket_path)}
        self._server = socketserver.UnixStreamServer(str(self.socket_path), _Handler)
        self._server.session = self
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()

    def start_attempt(self, timeout_seconds, *, started_at=None):
        """Arm once from the host runner at its exact process-start clock."""
        now = time.monotonic()
        if started_at is None:
            started_at = now
        if (
            not _positive_seconds(timeout_seconds)
            or type(started_at) not in (int, float)
            or not math.isfinite(started_at)
            or started_at > now
        ):
            raise ValueError("invalid trusted attempt clock")
        with self._lock:
            if self.frozen or self._started_at is not None or self.requests_seen:
                raise RuntimeError("trusted attempt clock must be armed once before requests")
            self._started_at = started_at
            self._timeout_seconds = timeout_seconds

    def completion_receipt(self):
        with self._lock:
            return _completion_receipt(self._payload())

    def protocol_error(self, reason, *, count_request=True):
        with self._lock:
            if self.frozen:
                return {"response": {"status": "error", "message": "session closed"}, "code": 2}
            if count_request:
                self.requests_seen += 1
            if reason not in self.protocol_errors:
                self.protocol_errors.append(reason)
            return {"response": {"status": "error", "message": reason}, "code": 2}

    def dispatch(self, argv, size):
        with self._lock:
            if self.frozen:
                return {"response": {"status": "error", "message": "session closed"}, "code": 2}
            if (
                self._started_at is not None
                and time.monotonic() - self._started_at > self._timeout_seconds
            ):
                return self.protocol_error("sanitary task timeout elapsed")
            self.requests_seen += 1
            self.request_bytes += size
            if self.requests_seen > MAX_REQUESTS or self.request_bytes > MAX_TOTAL_REQUEST_BYTES:
                return self.protocol_error(
                    "transport resource budget exceeded", count_request=False
                )
            io = WorkspaceIO(self.workspace, MAX_TOTAL_FILE_BYTES - self.file_bytes)
            try:
                response, code = self.namespace["_dispatch"](self.state, argv, io)
            except Exception:  # noqa: BLE001 - never leak host internals to the client
                return self.protocol_error("service rejected invalid request", count_request=False)
            record = {
                "argv": list(argv),
                "files": dict(io.files),
                "response": response,
                "code": code,
                "terminal": _terminal_record(self.namespace, argv, code),
                "elapsed_seconds": (
                    None if self._started_at is None else time.monotonic() - self._started_at
                ),
            }
            self.file_bytes += sum(len(text.encode()) for text in io.files.values())
            size = len(json.dumps(record, ensure_ascii=False).encode())
            if self.history_bytes + size > MAX_HISTORY_BYTES:
                return self.protocol_error(
                    "trusted history resource budget exceeded", count_request=False
                )
            self.history_bytes += size
            self.records.append(record)
            return {"response": response, "code": code}

    def _payload(self):
        return {
            "protocol": PROTOCOL,
            "task_id": self.task_id,
            "fingerprint": self.fingerprint,
            "frozen": self.frozen,
            "records": self.records,
            "connection": self.connection,
            "protocol_errors": self.protocol_errors,
            "requests_seen": self.requests_seen,
            "request_bytes": self.request_bytes,
            "file_bytes": self.file_bytes,
            "history_bytes": self.history_bytes,
            "attempt": (
                None if self._started_at is None
                else {"timeout_seconds": self._timeout_seconds}
            ),
        }

    def replay(self, *, service=None, task_id=None, fingerprint=None):
        with self._lock:
            payload = json.loads(json.dumps(self._payload()))
        return _replay(
            payload,
            service or self.service,
            task_id or self.task_id,
            fingerprint or self.fingerprint,
        )

    def freeze(self):
        with self._lock:
            if self.frozen:
                return
            self.frozen = True
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def export(self, path: Path):
        self.freeze()
        path = Path(path)
        if path.resolve().is_relative_to(self.workspace):
            raise ValueError("trusted evidence must be outside the agent workspace")
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = json.dumps(self._payload(), ensure_ascii=False, indent=2) + "\n"
        if len(data.encode()) > MAX_EVIDENCE_BYTES:
            raise ValueError("trusted evidence exceeds its bounded artifact limit")
        path.write_text(data, encoding="utf-8")

    def close(self):
        if not self._closed:
            self.freeze()
            self._directory.cleanup()
            self._closed = True


def _replay(payload, service, task_id, fingerprint):
    if (
        not isinstance(payload, dict)
        or payload.get("protocol") != PROTOCOL
        or payload.get("task_id") != task_id
        or payload.get("fingerprint") != fingerprint
    ):
        raise ValueError("trusted evidence does not match this task/protocol")
    ns = service.namespace()
    state = service.initial_state()
    calls = []
    records = payload["records"]
    if not isinstance(records, list) or len(records) > MAX_REQUESTS:
        raise ValueError("invalid trusted history")
    _completion_receipt(payload)  # Validate host timing, even when no terminal call succeeded.
    for record in records:
        argv, files = record["argv"], record["files"]
        response, code = ns["_dispatch"](state, argv, ns["_IO"](recorded=files))
        if response != record["response"] or code != record["code"]:
            raise ValueError("trusted replay mismatch")
        if record["terminal"] is not _terminal_record(ns, argv, code):
            raise ValueError("trusted completion marker mismatch")
        attempt = payload.get("attempt")
        if attempt is None:
            if record["elapsed_seconds"] is not None:
                raise ValueError("unarmed trusted session has timed requests")
        elif record["elapsed_seconds"] > attempt["timeout_seconds"]:
            raise ValueError("service request completed after sanitary timeout")
        calls.append((argv, response, code))
    for error in payload["protocol_errors"]:
        calls.append((["<transport>"], {"status": "error", "message": error}, 2))
    return Replay(state=state, calls=calls)


class _Evidence:
    def __init__(self, workspace, path):
        path = Path(path).resolve()
        if path.is_relative_to(workspace):
            raise ValueError("trusted evidence must be supplied by the host, outside the workspace")
        self.payload = _decode(_read_regular(path, MAX_EVIDENCE_BYTES))
        if self.payload.get("frozen") is not True:
            raise ValueError("trusted evidence is not finalized")
        self.connection = self.payload["connection"]

    def replay(self, *, service, task_id, fingerprint):
        return _replay(self.payload, service, task_id, fingerprint)

    def completion_receipt(self):
        return _completion_receipt(self.payload)


_sessions: dict[Path, HostSession] = {}
_registry_lock = threading.RLock()
_evidence = ContextVar("wave19_trusted_evidence", default=None)


def start_session(workspace: Path, service: Service, task_id: str, fingerprint: str):
    workspace = workspace.resolve()
    with _registry_lock:
        close_session(workspace)
        session = HostSession(workspace, service, task_id, fingerprint)
        _sessions[workspace] = session
    connection = workspace / CONNECTION_FILE
    connection.parent.mkdir(parents=True, exist_ok=True)
    connection.write_text(json.dumps(session.connection) + "\n", encoding="utf-8", newline="")
    return session


def get_session(workspace: Path):
    workspace = workspace.resolve()
    evidence = _evidence.get()
    if evidence is not None and evidence[0] == workspace:
        return evidence[1]
    with _registry_lock:
        session = _sessions.get(workspace)
    if session is None or session._closed:
        raise RuntimeError("trusted task service/evidence missing; use the isolated CLI runner")
    return session


def close_session(workspace: Path):
    with _registry_lock:
        session = _sessions.pop(workspace.resolve(), None)
    if session is not None:
        session.close()


def close_all_sessions():
    with _registry_lock:
        workspaces = list(_sessions)
    for workspace in workspaces:
        close_session(workspace)


@contextmanager
def evidence_context(workspace: Path, path: Path):
    workspace = workspace.resolve()
    token = _evidence.set((workspace, _Evidence(workspace, path)))
    try:
        yield
    finally:
        _evidence.reset(token)


atexit.register(close_all_sessions)
