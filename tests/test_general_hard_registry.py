"""The new wave has two descendants per original Lite task, without overlap."""

from collections import Counter

from harness_bench.feasibility_tasks.trusted_service import PROTOCOL
from harness_bench.general_hard import GENERAL_HARD_TASKS
from harness_bench.tasks import ALL_TASKS, SUITES
from harness_bench.versioning import task_number


def test_general_hard_registry_and_parent_coverage():
    assert [task_number(task.id) for task in GENERAL_HARD_TASKS] == list(range(472, 512))
    parents = Counter(
        tag for task in GENERAL_HARD_TASKS for tag in task.tags if tag.startswith("parent-")
    )
    assert parents == Counter({f"parent-{number}": 2 for number in range(352, 372)})
    for task in GENERAL_HARD_TASKS:
        assert f"parent-{352 + (task_number(task.id) - 472) // 2}" in task.tags
        assert task.min_timeout_seconds == 900
        assert task.max_timeout_seconds == 900
        assert task.requires_trusted_service
        assert task.runtime_protocol.startswith(f"{PROTOCOL}:wave20-interactive-v8:")
        assert {"interactive", "hardening-v8"} <= set(task.tags)
        assert "docs/taskctl.md" in task.setup_files
        assert "160" in task.prompt


def test_general_hard_suite_preserves_the_original_wave():
    assert SUITES["general-hard"] == GENERAL_HARD_TASKS == ALL_TASKS[471:511]
    assert sum("interactive" in task.tags for task in GENERAL_HARD_TASKS) == 40
    assert all(task in SUITES["medium"] for task in GENERAL_HARD_TASKS)
    assert not {task.id for task in GENERAL_HARD_TASKS} & {
        task.id for task in SUITES["light"]
    }
