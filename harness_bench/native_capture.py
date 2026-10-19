"""Disk-backed stdout/stderr for all CLI harness evidence streams.

Only bounded events and diagnostic tails enter the parent heap. Full model
inputs, outputs and tool traces remain in execution artifacts.
"""
from __future__ import annotations

import json
from pathlib import Path

CHUNK_BYTES = 64 * 1024
MAX_TERMINAL_BYTES = 256 * 1024
MAX_TERMINALS = 2  # Enough to detect a broken duplicate-terminal contract.
DIAGNOSTIC_BYTES = 64 * 1024


def terminal_output(path: Path) -> str:
    terminal = []
    with path.open("rb") as stream:
        while first := stream.readline(CHUNK_BYTES):
            # Events are JSONL; even a single model-input line can be hundreds
            # of MB. Never use unbounded readline/splitlines on this stream.
            candidate = b'"hbf_deepagents_result"' in first
            parts = [first] if candidate else []
            size = len(first)
            last = first
            while not last.endswith(b"\n"):
                last = stream.readline(CHUNK_BYTES)
                if not last:
                    break
                size += len(last)
                if candidate and size <= MAX_TERMINAL_BYTES:
                    parts.append(last)
                else:
                    candidate = False
                    parts.clear()
            if not candidate or size > MAX_TERMINAL_BYTES:
                continue
            try:
                event = json.loads(b"".join(parts))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(event, dict) and event.get("type") == "hbf_deepagents_result":
                terminal.append(json.dumps(event, ensure_ascii=False))
                if len(terminal) == MAX_TERMINALS:
                    break
    return "\n".join(terminal)


def diagnostic_tail(path: Path) -> str:
    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - DIAGNOSTIC_BYTES))
        return stream.read(DIAGNOSTIC_BYTES).decode("utf-8", errors="replace")


MAX_EVENT_BYTES = 1024 * 1024


class DiskOutput(str):
    """Bounded diagnostic string with a re-readable, disk-backed event stream.

    Full output is never embedded in TaskRun, exceptions or executor futures.
    Oversized strings are compacted for metric extraction only. A structure
    exceeding the parser bound fails explicitly rather than dropping counts.
    """
    def __new__(cls, path: Path, *, native: bool = False):
        value = terminal_output(path) if native else ""
        obj = super().__new__(cls, value or diagnostic_tail(path))
        obj.path = path
        return obj


def _compact_json_line(first, stream):
    """Keep metric structure while bounding verbose JSON string values.

    This projection is used only by metric readers. The original bytes are
    untouched. Numeric usage and all event/tool names remain intact. Strings
    over 4 KiB are diagnostic/model content, never numeric usage counters.
    """
    output = bytearray()
    string = bytearray()
    quoted = escaped = False
    chunk = first
    while chunk:
        for byte in chunk:
            if quoted:
                if not escaped and byte == 34:
                    # A prefix may end inside a UTF-8 character or escape.
                    # Remove only that unfinished suffix before re-encoding.
                    while True:
                        try:
                            value = json.loads(b'"' + string + b'"')
                            break
                        except (ValueError, UnicodeDecodeError):
                            if not string:
                                value = ""
                                break
                            del string[-1:]
                    output.extend(json.dumps(value, ensure_ascii=True).encode())
                    string.clear()
                    quoted = False
                else:
                    if len(string) < 4096:
                        string.append(byte)
                    if escaped:
                        escaped = False
                    elif byte == 92:
                        escaped = True
            elif byte == 34:
                quoted = True
            else:
                output.append(byte)
            if len(output) > MAX_EVENT_BYTES:
                raise ValueError("CLI JSON metric structure exceeds 1 MiB; full output preserved on disk")
        if chunk.endswith(b"\n"):
            break
        chunk = stream.readline(CHUNK_BYTES)
    return output.decode("utf-8", errors="replace")


def output_lines(output):
    if not isinstance(output, DiskOutput):
        yield from (output or "").splitlines()
        return
    with output.path.open("rb") as stream:
        while line := stream.readline(MAX_EVENT_BYTES + 1):
            if len(line) > MAX_EVENT_BYTES:
                if line.lstrip().startswith((b"{", b"[")):
                    yield _compact_json_line(line, stream)
                else:
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(CHUNK_BYTES)
                continue
            yield line.decode("utf-8", errors="replace").rstrip("\r\n")
