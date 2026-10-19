"""Disk-backed stdout/stderr for the native worker's verbose evidence stream.

Only terminal protocol events and a bounded diagnostic tail enter the parent
heap. Full model inputs, outputs and tool traces remain in execution artifacts.
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
