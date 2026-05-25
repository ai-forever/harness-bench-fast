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


def test_current_agentic_tasks_belong_to_current_revision() -> None:
    assert TASK_SET_VERSION == "0.6.0"
    assert revision_for_task_id("task_222_memory_name_pyproject").version == "0.3.0"
    assert revision_for_task_id("task_231_memory_refuse_secrets").version == "0.3.0"
    assert revision_for_task_id("task_232_terminal_log_status_matrix").version == "0.4.0"
    assert revision_for_task_id("task_240_swe_csv_top_customers").version == "0.4.0"
    assert revision_for_task_id("task_241_terminal_du_top_dirs").version == "0.5.0"
    assert revision_for_task_id("task_261_swe_inventory_allocate").version == "0.5.0"
    assert revision_for_task_id("task_262_terminal_json_config_inventory").version == "0.6.0"
    assert revision_for_task_id("task_276_swe_median_even_empty").version == "0.6.0"
