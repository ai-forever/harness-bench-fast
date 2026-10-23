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


def test_range_selects_the_reflect_wave_in_default_and_named_suites() -> None:
    expected = [t.id for t in ALL_TASKS[411:431]]
    assert _ids("run-openrouter", "--from-task", "412", "--to-task", "431") == expected
    assert _ids("run", "--suite", "reflect", "--from-task", "412", "--to-task", "431") == expected


def test_wave19_and_open_ended_ranges() -> None:
    assert _ids("run", "--suite", "feasibility") == [t.id for t in ALL_TASKS[431:471]]
    assert _ids("run-openrouter", "--from-task", "432") == [t.id for t in ALL_TASKS[431:]]
    assert _ids("run-openrouter", "--from-task", "412") == [t.id for t in ALL_TASKS[411:]]


def test_wave20_named_and_open_ended_ranges_agree() -> None:
    expected = [t.id for t in ALL_TASKS[471:511]]
    assert _ids("run", "--suite", "general-hard") == expected
    assert _ids("run-openrouter", "--from-task", "472", "--to-task", "511") == expected


def test_composite_calibration_named_and_open_ended_ranges_agree() -> None:
    expected = [t.id for t in ALL_TASKS[511:531]]
    assert len(expected) == 20
    assert _ids("run", "--suite", "composite-hard-calibration") == expected
    assert _ids("run-openrouter", "--from-task", "512") == expected


def test_selected_composite_wave_keeps_sparse_original_numbers() -> None:
    selected = (512, 518, 520, 522, 523, 524, 525, 526, 527, 530)
    assert _ids("run", "--suite", "composite-hard") == [ALL_TASKS[n - 1].id for n in selected]
    assert _ids("run-openrouter", "--suite", "composite-hard", "--from-task", "523", "--to-task", "527") == [
        ALL_TASKS[n - 1].id for n in selected if 523 <= n <= 527
    ]


def test_medium90_keeps_historical80_and_selected_ten_only() -> None:
    historical = [t.id for t in ALL_TASKS[391:471]]
    selected = _ids("run", "--suite", "composite-hard")
    assert _ids("run", "--suite", "medium") == historical + selected
    assert _ids("run", "--suite", "medium-historical80") == historical
    assert _ids("run", "--suite", "medium", "--from-task", "472") == selected
    assert _ids("run", "--suite", "light") == [t.id for t in ALL_TASKS[:391]]
