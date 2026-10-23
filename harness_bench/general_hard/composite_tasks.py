"""Wave 21: ten selected workflows and the original twenty-task calibration."""

from .composition import make_task

COMPOSITE_CALIBRATION_TASKS = [make_task(number) for number in range(512, 532)]

# At most two successful model+harness pairs among the seven full calibrations.
# Keep IDs and task objects unchanged so historical witnesses remain identifiable.
SELECTED_COMPOSITE_NUMBERS = (512, 518, 520, 522, 523, 524, 525, 526, 527, 530)
COMPOSITE_HARD_TASKS = [
    task for task in COMPOSITE_CALIBRATION_TASKS
    if int(task.id.split("_")[1]) in SELECTED_COMPOSITE_NUMBERS
]
