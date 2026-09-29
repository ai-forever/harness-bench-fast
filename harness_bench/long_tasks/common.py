"""Shared helpers for the long-task suite.

Every long task lives in its own module and exposes a module-level ``TASK``
built with :func:`long_task`. The registry id (``task_392_*`` … ``task_411_*``)
is separate from the ``TASK_ID`` string, which seeds generation and names the
``_texts`` fixture; do not point ``rng`` / ``rewritten`` at the registry id.
Conventions:

- Generation is deterministic: seed a ``random.Random`` with :func:`rng` and
  never touch the global RNG, the clock or the environment.
- Heavy generation is lazy (``functools.cache`` on a ``build()`` function), so
  importing the registry stays fast.
- Hidden answers, hidden test data and gold solutions are computed in the
  module and never written into the workspace by ``setup``.
- A check function raises ``AssertionError`` with a short reason on failure;
  :func:`long_task` turns that into a failing ``VerifyResult``. The reason
  must not leak expected values beyond what the prompt already tells.
"""

from __future__ import annotations

import csv
import functools
import gzip
import hashlib
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from harness_bench.core import Task, VerifyResult

TEXTS_DIR = Path(__file__).parent / "_texts"
LONG_TIMEOUT_SECONDS = 7200.0
LONG_RECURSION_LIMIT = 3000


def rng(name: str) -> random.Random:
    """Deterministic RNG for one task (seeded by a stable hash of `name`)."""
    return random.Random(int(hashlib.sha256(name.encode()).hexdigest()[:16], 16))


def long_task(
    *,
    id: str,  # noqa: A002 — mirrors Task.id
    name: str,
    prompt: str,
    setup: Callable[[Path], None],
    gold: Callable[[Path], None],
    check: Callable[[Path], str | None],
    tags: Sequence[str] = (),
    timeout_seconds: float = LONG_TIMEOUT_SECONDS,
    recursion_limit: int = LONG_RECURSION_LIMIT,
) -> Task:
    """Build a long `Task` from setup / gold / check callables.

    `check(ws)` returns an optional success note or raises `AssertionError`.
    """

    def verifier(ws: Path) -> VerifyResult:
        try:
            note = check(ws)
        except AssertionError as exc:
            return VerifyResult(False, str(exc) or "check failed")
        return VerifyResult(True, note or "ok")

    return Task(
        id=id,
        name=name,
        prompt=prompt,
        verifier=verifier,
        setup_callback=setup,
        gold_callback=gold,
        tags=("long", *tags),
        min_timeout_seconds=timeout_seconds,
        min_recursion_limit=recursion_limit,
    )


# --------------------------------------------------------------------------
# LLM-rewritten texts
# --------------------------------------------------------------------------
#
# Procedurally generated prose is built from a finite set of templates, and a
# strong agent notices that: it deduplicates the sentences and labels the
# templates instead of reading the documents. Tasks whose point is reading
# therefore ship a fixture of rewritten texts: each generated draft was
# paraphrased by one LLM and accepted only when an independent LLM, given just
# the task's rules, recovered the draft's ground truth from the rewrite
# (`scripts/rewrite_long_texts.py`). Ground truth still comes from the
# generator; the fixture only changes the wording.


def text_sha(draft: str) -> str:
    return hashlib.sha256(draft.encode("utf-8")).hexdigest()[:16]


@functools.cache
def load_texts(task_id: str) -> dict[str, dict[str, str]]:
    path = TEXTS_DIR / f"{task_id}.json.gz"
    if not path.is_file():
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def rewritten(task_id: str, key: str, draft: str) -> str:
    """The accepted rewrite of `draft`, or `draft` itself when none is stored.

    A stored rewrite is used only if it was made from this exact draft, so a
    change in the generator can never pair new ground truth with old text.
    """
    entry = load_texts(task_id).get(key)
    if entry and entry.get("sha") == text_sha(draft):
        return entry["text"]
    return draft


# --------------------------------------------------------------------------
# Writing fixtures
# --------------------------------------------------------------------------


def write(ws: Path, rel: str, content: str | bytes, *, executable: bool = False) -> Path:
    """Write a UTF-8 text (LF) or bytes file under `ws`, creating parents."""
    target = ws / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content, encoding="utf-8", newline="")
    if executable:
        target.chmod(0o755)
    return target


def write_csv(ws: Path, rel: str, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> Path:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return write(ws, rel, buf.getvalue())


def write_json(ws: Path, rel: str, data: Any) -> Path:
    return write(ws, rel, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Bench",
    "GIT_AUTHOR_EMAIL": "bench@example.com",
    "GIT_COMMITTER_NAME": "Bench",
    "GIT_COMMITTER_EMAIL": "bench@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(ws: Path, *args: str, date: str | None = None, author: str | None = None) -> str:
    """Run git in `ws` with a fixed identity (and optional fixed ISO date)."""
    env = {**os.environ, **_GIT_ENV, "HOME": str(ws)}
    if author:
        name, _, email = author.partition(" <")
        env["GIT_AUTHOR_NAME"] = name
        env["GIT_AUTHOR_EMAIL"] = email.rstrip(">") or "dev@example.com"
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    proc = subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", *args],
        cwd=ws,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


# --------------------------------------------------------------------------
# Reading agent output (every reader fails with a readable AssertionError)
# --------------------------------------------------------------------------


def read_text(ws: Path, rel: str) -> str:
    path = ws / rel
    assert path.is_file(), f"нет файла {rel}"
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AssertionError(f"{rel} не в UTF-8: {exc}") from exc


def read_json(ws: Path, rel: str) -> Any:
    text = read_text(ws, rel)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{rel}: невалидный JSON: {exc}") from exc


def read_jsonl(ws: Path, rel: str) -> list[Any]:
    rows = []
    for n, line in enumerate(read_text(ws, rel).splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{rel}:{n}: невалидный JSON: {exc}") from exc
    return rows


def read_csv(ws: Path, rel: str, required: Sequence[str] = ()) -> list[dict[str, str]]:
    """Read a CSV with a header; header names are stripped and case-folded."""
    text = read_text(ws, rel)
    sample = text[:4096]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    assert reader.fieldnames, f"{rel}: пустой файл или нет заголовка"
    fields = [(f or "").strip().casefold() for f in reader.fieldnames]
    missing = [c for c in required if c.casefold() not in fields]
    assert not missing, f"{rel}: нет столбцов {missing}"
    rows = []
    for raw in reader:
        rows.append(
            {
                (k or "").strip().casefold(): (v or "").strip() if isinstance(v, str) else ""
                for k, v in raw.items()
            }
        )
    return rows


# --------------------------------------------------------------------------
# Normalisation and comparison
# --------------------------------------------------------------------------


def norm(value: Any) -> str:
    """Case/space/ё-insensitive string form for comparing free-text answers."""
    text = str(value).replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text.strip(" .;,\"'«»")


def num(value: Any) -> float | None:
    """Parse numbers like `1 234,5`, `-12.0`, `1e3`; None when not a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def close(a: Any, b: float, *, abs_tol: float = 1e-6, rel_tol: float = 0.0) -> bool:
    x = num(a)
    if x is None:
        return False
    return abs(x - b) <= max(abs_tol, rel_tol * abs(b))


def require_share(
    total: int,
    correct: int,
    *,
    min_share: float,
    what: str,
    errors: Sequence[str] = (),
) -> str:
    """Assert `correct/total >= min_share`; show up to 5 example errors."""
    share = correct / total if total else 0.0
    note = f"{what}: верно {correct}/{total} ({share:.1%})"
    if share + 1e-12 < min_share:
        examples = "; ".join(errors[:5])
        raise AssertionError(
            f"{note}, нужно не меньше {min_share:.0%}" + (f". Примеры: {examples}" if examples else "")
        )
    return note


def precision_recall(expected: set, got: set) -> tuple[float, float]:
    hit = len(expected & got)
    precision = hit / len(got) if got else 0.0
    recall = hit / len(expected) if expected else 1.0
    return precision, recall


# --------------------------------------------------------------------------
# Running agent code against hidden checks
# --------------------------------------------------------------------------


def run_python(
    ws: Path,
    code_or_args: str | Sequence[str],
    *,
    stdin: str | None = None,
    timeout: float = 120,
    extra_path: Sequence[Path] = (),
) -> subprocess.CompletedProcess[str]:
    """Run `python -c code` (str) or `python args...` (list) with cwd=`ws`."""
    argv = [sys.executable, "-c", code_or_args] if isinstance(code_or_args, str) else [
        sys.executable,
        *code_or_args,
    ]
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = os.pathsep.join([str(ws), *map(str, extra_path)])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        return subprocess.run(
            argv,
            cwd=ws,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(f"проверка не уложилась в {timeout:.0f} с") from exc


def run_hidden_pytest(
    ws: Path,
    files: dict[str, str],
    *,
    timeout: float = 600,
    min_passed: int | None = None,
) -> tuple[int, int, str]:
    """Run hidden pytest files (kept outside `ws`) against the workspace code.

    Returns `(passed, failed, tail_of_output)`. The workspace is on
    `PYTHONPATH` and is the cwd, so tests import the agent's modules.
    """
    tmp = Path(tempfile.mkdtemp(prefix="hb_hidden_"))
    try:
        for rel, text in files.items():
            write(tmp, rel, text)
        proc = run_python(
            ws,
            ["-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp), str(tmp)],
            timeout=timeout,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = proc.stdout + proc.stderr
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    failed = sum(int(x) for x in re.findall(r"(\d+) (?:failed|error)", out))
    if min_passed is not None:
        assert passed >= min_passed and not failed, (
            f"скрытые тесты: прошло {passed}, упало {failed}; "
            + " | ".join(out.strip().splitlines()[-6:])
        )
    return passed, failed, out[-2000:]


def fingerprint(ws: Path, rels: Iterable[str]) -> dict[str, str]:
    """sha256 of files (missing → ''), to check that inputs were left untouched."""
    result = {}
    for rel in rels:
        path = ws / rel
        result[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
    return result
