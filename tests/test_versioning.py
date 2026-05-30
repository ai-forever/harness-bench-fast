from __future__ import annotations

from harness_bench.tasks import ALL_TASKS
from harness_bench.versioning import (
    EXPECTED_TASK_COUNT,
    TASK_SET_VERSION,
    revision_for_task_id,
    validate_task_set_metadata,
)


def test_task_set_metadata_matches_registry() -> None:
    assert validate_task_set_metadata(ALL_TASKS) == []
    assert len(ALL_TASKS) == EXPECTED_TASK_COUNT


def test_current_memory_tasks_belong_to_current_revision() -> None:
    assert TASK_SET_VERSION == "0.3.0"
    assert revision_for_task_id("task_222_memory_name_pyproject").version == "0.3.0"
    assert revision_for_task_id("task_231_memory_refuse_secrets").version == "0.3.0"
