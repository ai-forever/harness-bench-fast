"""Persistent per-execution CLI evidence, stored outside scored workspaces."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp, mkstemp

CAS_MIN_BYTES = 1024 * 1024


def _copy_evidence(source: Path, destination: Path, objects: Path) -> dict | None:
    """Copy bytes first, then atomically publish an immutable content object.

    Never link a live workspace file into the evidence store. Large runtime
    caches can be hundreds of MB per task; archived task paths share only our
    verified immutable copies. Storage failures propagate to the run result.
    """
    if source.stat().st_size < CAS_MIN_BYTES:
        shutil.copyfile(source, destination)
        return None
    objects.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A closed mkstemp file, not NamedTemporaryFile: Windows cannot reopen an
    # open temporary file or delete a read-only one, so the pending copy is
    # unlinked before the published object is made read-only.
    handle, name = mkstemp(prefix=".pending_", dir=objects)
    candidate = Path(name)
    try:
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(handle, "wb") as temporary, source.open("rb") as incoming:
            while block := incoming.read(1024 * 1024):
                temporary.write(block)
                digest.update(block)
                size += len(block)
            temporary.flush()
            os.fsync(temporary.fileno())
        sha256 = digest.hexdigest()
        with candidate.open("rb") as copied:
            if hashlib.file_digest(copied, "sha256").hexdigest() != sha256:
                raise OSError("evidence copy failed SHA256 verification")
        obj = objects / sha256
        try:
            os.link(candidate, obj)
            created = True
        except FileExistsError:
            created = False
            with obj.open("rb") as existing:
                if hashlib.file_digest(existing, "sha256").hexdigest() != sha256:
                    raise OSError(f"evidence content object failed SHA256 verification: {sha256}") from None
        candidate.unlink()
        if created:
            obj.chmod(0o444)
        os.link(obj, destination)
    finally:
        candidate.unlink(missing_ok=True)
    return {"sha256": sha256, "bytes": size, "object": str(obj.relative_to(objects.parent))}

TRACE_PATTERNS = (
    "*.traj.json",
    "trajectory.json",
    "*session*.jsonl",
    "*payload*.jsonl",
    "process.log",
)


def new_execution(root: Path, task_id: str, prompt: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = Path(mkdtemp(prefix=re.sub(r"[^A-Za-z0-9_.-]", "_", task_id) + "_", dir=root))
    target.chmod(0o700)
    (target / "prompt.txt").write_text(prompt, encoding="utf-8")
    return target


def text_output(value: str | bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


def record_execution(
    target: Path,
    *,
    stdout: str | bytes | None,
    stderr: str | bytes | None,
    metadata: dict,
    trace_roots: tuple[Path, ...],
    state_roots: tuple[Path, ...] = (),
) -> None:
    (target / "stdout.log").write_text(text_output(stdout), encoding="utf-8")
    (target / "stderr.log").write_text(text_output(stderr), encoding="utf-8")
    metadata = {**metadata, "recorded_at": datetime.now(UTC).isoformat()}
    (target / "execution.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    seen = set()
    content_objects = []
    roots = [("traces", index, root, TRACE_PATTERNS) for index, root in enumerate(trace_roots)]
    roots += [("private_state", index, root, ("*",)) for index, root in enumerate(state_roots)]
    for category, index, root, patterns in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            for path in root.rglob(pattern):
                if path in seen or path.is_symlink() or not path.is_file():
                    continue
                seen.add(path)
                # Do not follow a symlinked parent to unrelated task/host data.
                if not path.resolve().is_relative_to(root.resolve()):
                    continue
                dest = target / category / str(index) / path.relative_to(root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if reference := _copy_evidence(path, dest, target.parent / "_objects"):
                    content_objects.append({"path": str(dest.relative_to(target)), **reference})
    (target / "content_objects.json").write_text(
        json.dumps({"format_version": 1, "objects_relative_to": "execution_parent", "files": content_objects}, indent=2),
        encoding="utf-8",
    )
