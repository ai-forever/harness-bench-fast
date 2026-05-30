"""Export benchmark tasks into Harbor's local task format.

The exporter is intentionally additive: it materializes Harbor task directories
from the existing in-process Task registry, but the registry and local runners
remain the source of truth.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from harness_bench.core import Task
from harness_bench.tasks import ALL_TASKS, get_task
from harness_bench.versioning import TASK_SET_VERSION

DEFAULT_HARBOR_ORG = "ai-forever"
DEFAULT_HARBOR_DATASET = "harness-bench-fast"

_PACKAGE_FILES = ("pyproject.toml", "README.md", "LICENSE", "LEGACY_RESULTS.md")
_DEFAULT_IGNORES = ("__pycache__/", ".DS_Store", "*.pyc", "*.swp", "*.swo", "*~")


@dataclass(frozen=True)
class HarborExportResult:
    """Summary of a Harbor dataset export."""

    output_dir: Path
    task_count: int
    task_ids: tuple[str, ...]


def export_harbor_dataset(
    output_dir: Path | str,
    *,
    task_ids: Iterable[str] | None = None,
    org: str = DEFAULT_HARBOR_ORG,
    dataset: str = DEFAULT_HARBOR_DATASET,
    clean: bool = False,
) -> HarborExportResult:
    """Write a local Harbor dataset directory for the selected benchmark tasks."""
    output_path = Path(output_dir)
    if clean and output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)

    tasks = _select_tasks(task_ids)
    task_refs: list[tuple[str, str]] = []
    for task in tasks:
        task_name = f"{org}/{dataset}__{task.id}"
        task_dir = output_path / task.id
        _write_task(task_dir, task, harbor_name=task_name, dataset=dataset)
        task_refs.append((task_name, f"sha256:{_compute_task_content_hash(task_dir)}"))
    _write_dataset_files(output_path, org=org, dataset=dataset, tasks=tasks, task_refs=task_refs)

    return HarborExportResult(
        output_dir=output_path,
        task_count=len(tasks),
        task_ids=tuple(task.id for task in tasks),
    )


def _select_tasks(task_ids: Iterable[str] | None) -> list[Task]:
    if task_ids is None:
        return list(ALL_TASKS)
    return [get_task(task_id) for task_id in task_ids]


def _write_dataset_files(
    output_dir: Path,
    *,
    org: str,
    dataset: str,
    tasks: list[Task],
    task_refs: list[tuple[str, str]],
) -> None:
    dataset_name = f"{org}/{dataset}"
    lines = [
        'schema_version = "1.0"',
        "",
        "[dataset]",
        f"name = {_toml_string(dataset_name)}",
        f"description = {_toml_string('Self-contained file-operation agent benchmark')}",
        'keywords = ["benchmark", "agent", "evaluation", "harness"]',
        "",
        "[[dataset.authors]]",
        'name = "AI Forever"',
        "",
    ]
    for task_name, digest in task_refs:
        lines.extend(
            [
                "[[tasks]]",
                f"name = {_toml_string(task_name)}",
                f"digest = {_toml_string(digest)}",
                "",
            ]
        )
    output_dir.joinpath("dataset.toml").write_text("\n".join(lines))
    output_dir.joinpath("README.md").write_text(
        "\n".join(
            [
                "# harness-bench-fast Harbor export",
                "",
                f"Task-set version: `{TASK_SET_VERSION}`",
                f"Tasks exported: `{len(tasks)}`",
                "",
                "This directory is generated from the Python task registry. "
                "Regenerate it with `python -m harness_bench export-harbor`.",
                "",
            ]
        )
    )


def _write_task(task_dir: Path, task: Task, *, harbor_name: str, dataset: str) -> None:
    if task_dir.exists():
        shutil.rmtree(task_dir)

    environment_dir = task_dir / "environment"
    solution_dir = task_dir / "solution"
    tests_dir = task_dir / "tests"
    environment_dir.mkdir(parents=True)
    solution_dir.mkdir()
    tests_dir.mkdir()

    task_dir.joinpath("instruction.md").write_text(task.prompt.rstrip() + "\n")
    task_dir.joinpath("README.md").write_text(_task_readme(task))
    task_dir.joinpath("task.toml").write_text(
        _task_toml(task, harbor_name=harbor_name, dataset=dataset)
    )
    solution_dir.joinpath("solve.sh").write_text(_solution_script(task))
    tests_dir.joinpath("test.sh").write_text(_test_script(task))
    tests_dir.joinpath("test_outputs.py").write_text(_test_outputs_stub())
    environment_dir.joinpath("Dockerfile").write_text(_dockerfile())

    _write_setup_tar(environment_dir / "setup.tar", task)
    _copy_benchmark_runtime(solution_dir / "benchmark")
    _copy_benchmark_runtime(tests_dir / "benchmark")


def _task_readme(task: Task) -> str:
    tags = ", ".join(task.tags) if task.tags else "none"
    return "\n".join(
        [
            f"# {task.id}",
            "",
            task.name,
            "",
            f"Task-set version: `{TASK_SET_VERSION}`",
            f"Tags: `{tags}`",
            "",
        ]
    )


def _task_toml(task: Task, *, harbor_name: str, dataset: str) -> str:
    tags = list(task.tags)
    keywords = sorted({"harness-bench-fast", "agent", "benchmark", *tags})
    metadata = {
        "benchmark": dataset,
        "task_id": task.id,
        "task_set_version": TASK_SET_VERSION,
        "tags": tags,
    }
    lines = [
        'schema_version = "1.2"',
        "artifacts = []",
        "",
        "[task]",
        f"name = {_toml_string(harbor_name)}",
        f"description = {_toml_string(task.name)}",
        f"keywords = {_toml_array(keywords)}",
        "",
        "[[task.authors]]",
        'name = "AI Forever"',
        "",
        "[metadata]",
    ]
    lines.extend(f"{key} = {_toml_value(value)}" for key, value in metadata.items())
    lines.extend(
        [
            "",
            "[verifier]",
            "timeout_sec = 600.0",
            "",
            "[verifier.env]",
            "",
            "[agent]",
            "timeout_sec = 600.0",
            "",
            "[environment]",
            "build_timeout_sec = 600.0",
            'os = "linux"',
            "cpus = 1",
            "memory_mb = 2048",
            "storage_mb = 10240",
            "gpus = 0",
            "allow_internet = true",
            'workdir = "/app"',
            "mcp_servers = []",
            "",
            "[environment.env]",
            "",
            "[solution.env]",
            "",
        ]
    )
    return "\n".join(lines)


def _solution_script(task: Task) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            'WORKSPACE="${HARBOR_WORKSPACE:-/app}"',
            'PYTHON_BIN="${PYTHON_BIN:-python}"',
            'BENCHMARK_PYTHONPATH="${HARBOR_BENCHMARK_PYTHONPATH:-/solution/benchmark}"',
            (
                "PYTHONPATH=\"$BENCHMARK_PYTHONPATH${PYTHONPATH:+:$PYTHONPATH}\" "
                f"\"$PYTHON_BIN\" -m harness_bench apply-gold --task {json.dumps(task.id)} --workspace \"$WORKSPACE\""
            ),
            "",
        ]
    )


def _test_script(task: Task) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            'WORKSPACE="${HARBOR_WORKSPACE:-/app}"',
            'PYTHON_BIN="${PYTHON_BIN:-python}"',
            'VERIFIER_LOG_DIR="${HARBOR_VERIFIER_LOG_DIR:-/logs/verifier}"',
            'mkdir -p "$VERIFIER_LOG_DIR"',
            'BENCHMARK_PYTHONPATH="${HARBOR_BENCHMARK_PYTHONPATH:-/tests/benchmark}"',
            (
                "if PYTHONPATH=\"$BENCHMARK_PYTHONPATH${PYTHONPATH:+:$PYTHONPATH}\" "
                f"\"$PYTHON_BIN\" -m harness_bench verify-task --task {json.dumps(task.id)} --workspace \"$WORKSPACE\"; then"
            ),
            '  echo 1 > "$VERIFIER_LOG_DIR/reward.txt"',
            "else",
            '  echo 0 > "$VERIFIER_LOG_DIR/reward.txt"',
            "fi",
            "",
        ]
    )


def _test_outputs_stub() -> str:
    return "\n".join(
        [
            '"""Harbor verifier compatibility stub.',
            "",
            "The actual check is implemented by tests/test.sh, which delegates to",
            "the benchmark's Python verifier and writes /logs/verifier/reward.txt.",
            '"""',
            "",
        ]
    )


def _dockerfile() -> str:
    return "\n".join(
        [
            "FROM python:3.12-slim",
            "",
            "ENV PYTHONDONTWRITEBYTECODE=1",
            "",
            "RUN pip install --no-cache-dir pytest openpyxl PyYAML python-dotenv",
            "",
            "WORKDIR /app",
            "COPY setup.tar /tmp/harness_setup.tar",
            "RUN tar -xf /tmp/harness_setup.tar -C /app && rm /tmp/harness_setup.tar",
            "",
        ]
    )


def _write_setup_tar(tar_path: Path, task: Task) -> None:
    with TemporaryDirectory(prefix=f"hb_harbor_setup_{task.id}_") as tmp:
        workspace = Path(tmp)
        task.setup(workspace)
        with tarfile.open(tar_path, "w") as archive:
            for path in sorted(workspace.rglob("*")):
                archive.add(path, arcname=path.relative_to(workspace))


def _copy_benchmark_runtime(target_dir: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)

    package_dst = target_dir / "harness_bench"
    shutil.copytree(
        repo_root / "harness_bench",
        package_dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "runs", ".free-code-logs"),
    )
    for rel in _PACKAGE_FILES:
        source = repo_root / rel
        if source.exists():
            shutil.copy2(source, target_dir / rel)


def _compute_task_content_hash(task_dir: Path) -> str:
    files = _collect_hashable_files(task_dir)
    outer = hashlib.sha256()
    for file_path in files:
        rel = file_path.relative_to(task_dir).as_posix()
        file_hash = _compute_file_hash(file_path)
        outer.update(f"{rel}\0{file_hash}\n".encode())
    return outer.hexdigest()


def _collect_hashable_files(task_dir: Path) -> list[Path]:
    files = [path for path in task_dir.rglob("*") if path.is_file()]
    files = [
        path
        for path in files
        if not _matches_default_ignore(path.relative_to(task_dir).as_posix())
    ]
    return sorted(files, key=lambda path: path.relative_to(task_dir).as_posix())


def _matches_default_ignore(rel_path: str) -> bool:
    from fnmatch import fnmatch

    for pattern in _DEFAULT_IGNORES:
        if pattern.endswith("/") and (
            rel_path == pattern.rstrip("/") or rel_path.startswith(pattern)
        ):
            return True
        if fnmatch(rel_path, pattern):
            return True
    return False


def _compute_file_hash(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: Iterable[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list | tuple):
        return _toml_array(str(item) for item in value)
    return json.dumps(value, ensure_ascii=False)
