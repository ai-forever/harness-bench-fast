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


def test_agentic_tasks_belong_to_their_revisions() -> None:
    # The agentic wave spans three revisions, tasks 254-298.
    assert revision_for_task_id("task_254_terminal_log_status_matrix").version == "0.5.0"
    assert revision_for_task_id("task_262_swe_csv_top_customers").version == "0.5.0"
    assert revision_for_task_id("task_263_terminal_du_top_dirs").version == "0.6.0"
    assert revision_for_task_id("task_283_swe_inventory_allocate").version == "0.6.0"
    assert revision_for_task_id("task_284_terminal_json_config_inventory").version == "0.7.0"
    assert revision_for_task_id("task_298_swe_median_even_empty").version == "0.7.0"


def test_vcs_tasks_belong_to_their_revisions() -> None:
    # 0.8.0 adds version-control tasks 299-308.
    assert revision_for_task_id("task_299_resolve_conflict_take_both").version == "0.8.0"
    assert revision_for_task_id("task_308_detect_unresolved_conflicts").version == "0.8.0"


def test_current_multifile_vcs_tasks_belong_to_their_revision() -> None:
    # 0.9.0 adds multi-file / multi-step VCS workflows 309-313.
    assert revision_for_task_id("task_309_rename_refactor_scale").version == "0.9.0"
    assert revision_for_task_id("task_313_aggregate_config_fragments").version == "0.9.0"


def test_current_skill_tasks_belong_to_current_revision() -> None:
    assert TASK_SET_VERSION == "0.10.0"
    # 0.10.0 adds skill-discriminator workflows 314-330.
    assert revision_for_task_id("task_314_skill_r1_brand_landing").version == "0.10.0"
    assert revision_for_task_id("task_330_skill_d1b_arcflux_multifile").version == "0.10.0"
