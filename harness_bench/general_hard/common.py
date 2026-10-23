"""Small mechanical task builders; reference answers stay outside workspaces."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from harness_bench.core import Task, VerifyResult

MAX_FILE_BYTES = 4_000_000


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def loads(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_pairs,
        parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"non-finite JSON: {x}")),
    )


def read_file(ws: Path, rel: str) -> bytes:
    path = ws / rel
    for item in (path, *path.parents):
        if item == ws:
            break
        if item.is_symlink():
            raise ValueError(f"symlink not allowed: {rel}")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ValueError(f"not a bounded regular file: {rel}")
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"file too large: {rel}")
    return data


def same_json(actual: Any, expected: Any) -> bool:
    """Do not silently accept bools in integer fields."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            same_json(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            same_json(a, b) for a, b in zip(actual, expected, strict=True)
        )
    return actual == expected


def artifact_task(
    *,
    number: int,
    slug: str,
    name: str,
    parent: int,
    prompt: str,
    files: dict[str, str],
    expected: Any,
    output: str = "answer.json",
    tags: tuple[str, ...] = (),
) -> Task:
    """A fixed multi-file investigation, with all source fixtures protected."""
    original = {rel: text.encode("utf-8") for rel, text in files.items()}

    def verify(ws):
        for rel, content in original.items():
            if read_file(ws, rel) != content:
                return VerifyResult(False, f"source fixture changed: {rel}")
        actual = loads(read_file(ws, output).decode("utf-8"))
        if not same_json(actual, expected):
            return VerifyResult(False, f"{output}: incorrect result")
        return VerifyResult(True, "all result fields and source fixtures match")

    return Task(
        id=f"task_{number}_{slug}",
        name=name,
        prompt=prompt + (
            f"\n\nЗапиши результат в {output}. Исходные файлы менять нельзя. "
            "JSON сравнивается структурно: порядок ключей и отступы не важны; "
            "порядок массивов, типы и отсутствие лишних полей важны. "
            "Максимум времени агента — 900 секунд. Решение полностью локальное."
        ),
        setup_files=files,
        gold_files={output: dumps(expected)},
        verifier=verify,
        tags=("general-hard", "wave20", f"parent-{parent}", *tags),
        max_timeout_seconds=900,
        runtime_protocol="general-hard-artifact-v1",
    )


def program_task(
    *,
    number: int,
    slug: str,
    name: str,
    parent: int,
    prompt: str,
    cases: list[tuple[Any, Any]],
    gold: str,
    files: dict[str, str] | None = None,
    tags: tuple[str, ...] = (),
) -> Task:
    """JSON-lines CLI, tested against independent unseen inputs in a fresh sandbox.

    The evaluator only copies solution.py, never its source tree or expectations.
    Linux uses the same allowlisted bwrap boundary as scored HBF execution.
    Non-Linux execution is provided for trusted reference/CI checks.
    """
    setup = dict(files or {})
    setup["examples.json"] = dumps([{"input": a, "output": b} for a, b in cases[:2]])
    setup["solution.py"] = "# Implement the JSON-lines program described in the task.\n"
    original = {rel: text.encode("utf-8") for rel, text in setup.items() if rel != "solution.py"}
    inputs = "".join(json.dumps(a, ensure_ascii=False, allow_nan=False) + "\n" for a, _ in cases)
    expected = [b for _, b in cases]

    def verify(ws):
        for rel, content in original.items():
            if read_file(ws, rel) != content:
                return VerifyResult(False, f"source fixture changed: {rel}")
        source = read_file(ws, "solution.py")
        with tempfile.TemporaryDirectory(prefix="hbf-general-verify-") as raw:
            root = Path(raw)
            workspace = root / "work"
            workspace.mkdir()
            (workspace / "solution.py").write_bytes(source)
            argv = [sys.executable, "-I", "-S", str(workspace / "solution.py")]
            if sys.platform == "linux":
                from harness_bench.cli_isolation import sandbox_argv

                scratch, home = root / "scratch", root / "home"
                scratch.mkdir()
                home.mkdir()
                argv = sandbox_argv(
                    ["/usr/bin/python3", "-I", "-S", str(workspace / "solution.py")],
                    workspace=workspace,
                    scratch=scratch,
                    home=home,
                    protect_benchmark=True,
                )
                argv.insert(1, "--unshare-net")
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                try:
                    proc = subprocess.run(
                        argv, input=inputs.encode(), stdout=stdout, stderr=stderr,
                        cwd=workspace, timeout=25,
                        env={"PATH": os.defpath, "PYTHONIOENCODING": "utf-8", "LANG": "C.UTF-8"},
                    )
                except subprocess.TimeoutExpired:
                    return VerifyResult(False, "solution exceeded 25 s verifier budget")
                if proc.returncode:
                    return VerifyResult(False, f"solution process exited {proc.returncode}")
                stdout.seek(0)
                raw_output = stdout.read(MAX_FILE_BYTES + 1)
        if len(raw_output) > MAX_FILE_BYTES:
            return VerifyResult(False, "solution output too large")
        # U+0085/U+2028/U+2029 are legal inside JSON strings, not JSONL delimiters.
        lines = raw_output.decode("utf-8").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if len(lines) != len(expected):
            return VerifyResult(False, "expected one output JSON line for every input line")
        for index, (line, answer) in enumerate(zip(lines, expected, strict=True)):
            if not same_json(loads(line), answer):
                return VerifyResult(False, f"incorrect output for case {index + 1}")
        return VerifyResult(True, f"{len(cases)} functional cases passed")

    return Task(
        id=f"task_{number}_{slug}",
        name=name,
        prompt=prompt + (
            "\n\nРеализуй solution.py: Python 3.12, только стандартная библиотека. "
            "Каждая строка stdin — отдельное JSON-значение; на каждую выведи ровно "
            "одну строку JSON в stdout, без пояснений. Программа обрабатывает поток "
            "до EOF. Примеры в examples.json; проверка использует также другие "
            "входы по тому же контракту, поэтому нельзя подставлять готовые ответы. "
            "Оценивается только solution.py, вспомогательные файлы при проверке "
            "не доступны. Исходные файлы кроме solution.py менять нельзя. "
            "Порядок ключей JSON не важен; типы, массивы и набор полей важны. "
            "Максимум времени агента — 900 секунд; проверка всех входов — 25 секунд. "
            "Решение полностью локальное."
        ),
        setup_files=setup,
        gold_files={"solution.py": gold},
        verifier=verify,
        tags=("general-hard", "wave20", f"parent-{parent}", "program", *tags),
        max_timeout_seconds=900,
        runtime_protocol="general-hard-program-v1",
    )
