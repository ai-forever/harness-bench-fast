from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory

from harness_bench.core import VerifyResult
from harness_bench.runner import TaskRun
from harness_bench.tasks import ALL_TASKS, get_task


def _task_sort_key(task_id: str) -> tuple[int, str]:
    rest = task_id.removeprefix("task_")
    head, _, _ = rest.partition("_")
    try:
        return (int(head), task_id)
    except ValueError:
        return (10**9, task_id)


def _annotation_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _emit_error(title: str, message: str) -> None:
    print(f"::error title={_annotation_escape(title)}::{_annotation_escape(message)}")


def _one_line(message: str) -> str:
    lines = [line for line in message.splitlines() if line.strip()]
    return lines[0] if lines else "(no detail)"


def verify_gold_with_annotations(task_ids: list[str] | None = None) -> int:
    targets = [get_task(tid) for tid in task_ids] if task_ids else list(ALL_TASKS)
    results: list[TaskRun] = []

    for task in targets:
        started = time.monotonic()
        try:
            with TemporaryDirectory(prefix=f"hb_gold_{task.id}_") as tmp:
                ws = Path(tmp)
                task.setup(ws)
                task.apply_gold(ws)
                outcome: VerifyResult = task.verify(ws)
            elapsed = time.monotonic() - started
            result = TaskRun(
                task_id=task.id,
                passed=outcome.passed,
                message=outcome.message,
                elapsed_seconds=elapsed,
            )
        except Exception:  # noqa: BLE001 - CI diagnostic wrapper must keep going
            elapsed = time.monotonic() - started
            result = TaskRun(
                task_id=task.id,
                passed=False,
                message=traceback.format_exc(),
                elapsed_seconds=elapsed,
            )

        results.append(result)
        status = "OK  " if result.passed else "BAD "
        print(f"[{status}] {task.id} ({result.elapsed_seconds * 1000:.1f}ms) — {_one_line(result.message)}")
        if not result.passed:
            _emit_error(f"verify-gold failed: {task.id}", result.message)

    results.sort(key=lambda run: _task_sort_key(run.task_id))
    failed = [run for run in results if not run.passed]
    print()
    print("=" * 64)
    print(f"Gold-verification: {len(results) - len(failed)}/{len(results)} OK")
    if failed:
        print()
        print("Verifier failures (likely bugs in the verifier or gold solution):")
        for run in failed:
            print(f"  - {run.task_id}: {_one_line(run.message)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(verify_gold_with_annotations(sys.argv[1:] or None))
