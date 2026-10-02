"""Tool-reflection suite: registry placement and the replay verifier."""

from __future__ import annotations

import json

import pytest

from harness_bench.reflect_tasks import REFLECT_TASKS
from harness_bench.reflect_tasks.common import JOURNAL_FILE, STATE_FILE
from harness_bench.tasks import ALL_TASKS, SUITES, get_task
from harness_bench.versioning import CURRENT_TASK_SET_REVISION, task_number


def test_reflect_wave_is_tasks_412_to_431() -> None:
    ids = {task.id for task in REFLECT_TASKS}
    assert len(ids) == 20
    assert [task_number(t.id) for t in REFLECT_TASKS] == list(range(412, 432))
    assert SUITES["reflect"] == REFLECT_TASKS
    assert ids <= {task.id for task in ALL_TASKS}
    assert ALL_TASKS[411:431] == REFLECT_TASKS
    assert len(ALL_TASKS) == CURRENT_TASK_SET_REVISION.total_tasks
    assert all(get_task(i).id == i for i in ids)


@pytest.mark.parametrize("task", REFLECT_TASKS, ids=lambda t: t.id)
def test_untouched_fails_gold_passes_and_journal_is_authoritative(task, tmp_path) -> None:
    task.setup(tmp_path)
    assert not task.verify(tmp_path).passed
    task.apply_gold(tmp_path)
    assert task.verify(tmp_path).passed, task.verify(tmp_path).message

    (tmp_path / STATE_FILE).unlink()
    assert task.verify(tmp_path).passed

    journal = tmp_path / JOURNAL_FILE
    lines = journal.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    entry["argv"] = [*entry["argv"], "--forged"]
    journal.write_text("\n".join([*lines[:-1], json.dumps(entry)]) + "\n", encoding="utf-8")
    result = task.verify(tmp_path)
    assert not result.passed and "altered" in result.message


def test_pre_publication_ids_resolve_to_the_scored_tasks() -> None:
    for task in REFLECT_TASKS:
        number, suffix = task.id.removeprefix("task_").split("_", 1)
        alias = f"reflect_{int(number) - 411:02d}_{suffix}"
        assert get_task(alias) is task
