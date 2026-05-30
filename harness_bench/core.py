"""Core types for the benchmark: Task, VerifyResult, Verifier."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of a verifier."""

    passed: bool
    message: str = ""


Verifier = Callable[[Path], VerifyResult]


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

    def setup(self, workspace: Path) -> None:
        """Write the task's setup files into `workspace`."""
        for rel, content in self.setup_files.items():
            target = workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
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
                target.write_text(content)
        if self.gold_callback is not None:
            self.gold_callback(workspace)

    def verify(self, workspace: Path) -> VerifyResult:
        """Run the verifier, converting unexpected exceptions to failures."""
        try:
            return self.verifier(workspace)
        except Exception as exc:  # noqa: BLE001 — verifier robustness
            return VerifyResult(False, f"verifier raised: {type(exc).__name__}: {exc}")
