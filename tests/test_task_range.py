"""`--from-task` / `--to-task` pick a contiguous block of task numbers."""

from __future__ import annotations

import pytest

from harness_bench.__main__ import _task_ids, build_parser, main
from harness_bench.tasks import ALL_TASKS
from harness_bench.versioning import task_number


def _ids(*argv: str) -> list[str] | None:
    return _task_ids(build_parser().parse_args(list(argv)))


def test_without_range_the_default_suite_stays_unfiltered() -> None:
    assert _ids("run-openrouter") is None


def test_to_task_leaves_out_the_long_wave() -> None:
    ids = _ids("run-openrouter", "--to-task", "391")
    assert len(ids) == 391 and ids[-1].startswith("task_391_")
    assert ids == [t.id for t in ALL_TASKS if task_number(t.id) <= 391]


@pytest.mark.parametrize("command", ["run", "run-pure", "run-cli", "run-openrouter", "verify-gold", "export-harbor"])
def test_both_bounds_are_inclusive_on_every_runner(command: str) -> None:
    extra = ("--output", "out") if command == "export-harbor" else ()
    ids = _ids(command, *extra, "--from-task", "100", "--to-task", "105")
    assert [task_number(i) for i in ids] == [100, 101, 102, 103, 104, 105]


def test_range_narrows_the_long_suite() -> None:
    ids = _ids("run", "--suite", "long", "--from-task", "400", "--to-task", "402")
    assert [task_number(i) for i in ids] == [400, 401, 402]


@pytest.mark.parametrize(
    "argv",
    [
        ["run-openrouter", "--task", "task_01_create_hello", "--to-task", "3"],
        ["run-openrouter", "--from-task", "10", "--to-task", "5"],
        ["run-openrouter", "--from-task", "0"],
        ["list", "--suite", "long", "--to-task", "100"],
    ],
)
def test_invalid_ranges_are_rejected_before_anything_runs(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
