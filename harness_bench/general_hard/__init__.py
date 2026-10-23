"""Wave 20 v8: coupled interactive descendants of the first Lite wave."""

from harness_bench.general_hard.interactive_data import TASKS as DATA_TASKS
from harness_bench.general_hard.interactive_systems import TASKS as SYSTEMS_TASKS
from harness_bench.general_hard.interactive_workflows import TASKS as WORKFLOW_TASKS

GENERAL_HARD_TASKS = sorted(
    [*DATA_TASKS, *SYSTEMS_TASKS, *WORKFLOW_TASKS], key=lambda task: task.id
)
