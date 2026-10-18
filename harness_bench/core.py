"""Core types for the benchmark: Task, VerifyResult, Verifier."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of a verifier."""

    passed: bool
    message: str = ""


Verifier = Callable[[Path], VerifyResult]


def effective_task_timeout(
    configured_seconds: float,
    min_timeout_seconds: float | None = None,
    max_timeout_seconds: float | None = None,
) -> float:
    """Resolve a runner timeout with an optional task floor and hard ceiling.

    Only host-side execution time is budgeted; setup and verification are
    outside the agent budget. Workspace timestamps are never authoritative.
    """
    for name, value in (
        ("configured_seconds", configured_seconds),
        ("min_timeout_seconds", min_timeout_seconds),
        ("max_timeout_seconds", max_timeout_seconds),
    ):
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError(f"{name} must be finite and positive")
    if (min_timeout_seconds is not None and max_timeout_seconds is not None
            and min_timeout_seconds > max_timeout_seconds):
        raise ValueError("min_timeout_seconds cannot exceed max_timeout_seconds")
    timeout = max(configured_seconds, min_timeout_seconds or 0.0)
    return min(timeout, max_timeout_seconds) if max_timeout_seconds is not None else timeout


@dataclass
class Task:
    """A single benchmark task.

    Attributes:
        id: Stable identifier (e.g. `"task_01_create_hello"`).
        name: Short human-readable description.
        prompt: Instruction handed to the agent verbatim.
        verifier: Callable that inspects the workspace and returns a
            `VerifyResult`. Wrapped in `verify` so verifier exceptions become
            failures.
        setup_files: Text files written to the workspace before the agent
            runs. Keys are relative paths; values are file contents.
        setup_callback: Optional hook called after `setup_files` are written.
            Use this to create binary files (xlsx, sqlite) or to generate
            large procedural fixtures that don't fit a literal string dict.
        gold_files: Files written to the workspace by `apply_gold` to simulate
            a perfect solution. Values that are `None` mean the file should
            be deleted (useful for "remove file" tasks).
        gold_callback: Optional hook called after `gold_files` are applied.
            Use this when the gold state includes binary files (xlsx, sqlite).
        tags: Free-form labels (e.g. `("create", "easy")`).
        min_timeout_seconds: Per-task wall-clock floor. Runners use
            `max(configured timeout, this)`, so long tasks are not cut off by
            the short default meant for the regular set.
        min_recursion_limit: Same kind of floor for the deepagents graph
            recursion limit.
        max_timeout_seconds: Hard ceiling on host-measured agent execution
            time, applied after the configured timeout and task floor.
        requires_trusted_service: Measured runs require the isolated CLI so
            the agent cannot inspect or replace host-owned service state.
        runtime_protocol: Versioned execution protocol, included in resume
            identity for tasks with a trusted host service.
        cleanup_callback: Release task-owned host resources after verification
            and artifact recording, including failed or timed-out executions.
    """

    id: str
    name: str
    prompt: str
    verifier: Verifier
    setup_files: dict[str, str] = field(default_factory=dict)
    gold_files: dict[str, str | None] = field(default_factory=dict)
    setup_callback: Callable[[Path], None] | None = None
    gold_callback: Callable[[Path], None] | None = None
    tags: tuple[str, ...] = ()
    min_timeout_seconds: float | None = None
    min_recursion_limit: int | None = None
    max_timeout_seconds: float | None = None
    requires_trusted_service: bool = False
    runtime_protocol: str = ""
    cleanup_callback: Callable[[Path], None] | None = None

    def setup(self, workspace: Path) -> None:
        """Write the task's setup files into `workspace`.

        Written with an explicit LF terminator and UTF-8 encoding so a fixture
        is byte-identical on every platform. Without `newline=""` Python
        translates `\\n` to `\\r\\n` on Windows, which silently changes file
        sizes and hashes, breaks fixtures handed to real shell tools, and makes
        byte-level tasks unwinnable there; without `encoding` the writes follow
        the locale codec and Cyrillic fixtures raise on a cp1251 console.
        """
        for rel, content in self.setup_files.items():
            target = workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="")
        if self.setup_callback is not None:
            self.setup_callback(workspace)

    def apply_gold(self, workspace: Path) -> None:
        """Lay down the task's gold solution over `workspace`.

        A `None` value removes the file *and* prunes empty parent directories
        up to `workspace` so the gold state matches a literal "this path must
        not exist" requirement without leaving behind empty directories.
        """
        for rel, content in self.gold_files.items():
            target = workspace / rel
            if content is None:
                target.unlink(missing_ok=True)
                # Walk up and drop any empty parents (but never the workspace itself).
                parent = target.parent
                while parent != workspace and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="")
        if self.gold_callback is not None:
            self.gold_callback(workspace)

    def verify(self, workspace: Path) -> VerifyResult:
        """Run the verifier, converting unexpected exceptions to failures."""
        try:
            return self.verifier(workspace)
        except UnicodeDecodeError as exc:
            return VerifyResult(False, f"verifier failed to decode text as UTF-8: {exc}")
        except Exception as exc:  # noqa: BLE001 — verifier robustness
            return VerifyResult(False, f"verifier raised: {type(exc).__name__}: {exc}")

    def cleanup(self, workspace: Path) -> None:
        """Release host resources; callers must invoke this even after failure."""
        if self.cleanup_callback is not None:
            self.cleanup_callback(workspace)
