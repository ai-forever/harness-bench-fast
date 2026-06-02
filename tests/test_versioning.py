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


def test_memory_tasks_belong_to_their_revisions() -> None:
    # 0.3.0 memory tasks keep their original revision.
    assert revision_for_task_id("task_222_memory_name_pyproject").version == "0.3.0"
    assert revision_for_task_id("task_231_memory_refuse_secrets").version == "0.3.0"
    # 0.4.0 extends the memory suite with tasks 232-253.
    assert revision_for_task_id("task_232_memory_focus_day_schedule_rules").version == "0.4.0"
    assert revision_for_task_id("task_253_memory_mixed_save_refuse_use").version == "0.4.0"


def test_current_agentic_tasks_belong_to_current_revision() -> None:
    assert TASK_SET_VERSION == "0.7.0"
    # The agentic wave spans three revisions, tasks 254-298.
    assert revision_for_task_id("task_254_terminal_log_status_matrix").version == "0.5.0"
    assert revision_for_task_id("task_262_swe_csv_top_customers").version == "0.5.0"
    assert revision_for_task_id("task_263_terminal_du_top_dirs").version == "0.6.0"
    assert revision_for_task_id("task_283_swe_inventory_allocate").version == "0.6.0"
    assert revision_for_task_id("task_284_terminal_json_config_inventory").version == "0.7.0"
    assert revision_for_task_id("task_298_swe_median_even_empty").version == "0.7.0"
