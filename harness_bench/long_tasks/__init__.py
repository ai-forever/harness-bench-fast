"""Long-context wave: tasks 392-411 of the scored task set.

These tasks run for up to two hours each and are meant to push a harness
through context compaction. They are part of ``ALL_TASKS`` (task-set v0.17.0).
``--suite long`` selects only this wave. Registry ids are ``task_392_*`` …
``task_411_*``; generator seeds and ``_texts`` fixtures keep the earlier
``long_NN_*`` names so the generated workspaces do not change. Each module
``tNN_*.py`` defines one ``TASK``; see ``common.py`` for the conventions.
"""

from __future__ import annotations

import importlib
import pkgutil

from harness_bench.core import Task

_MODULES = sorted(
    info.name
    for info in pkgutil.iter_modules(__path__)
    if info.name[:1] == "t" and info.name[1:3].isdigit()
)

LONG_TASKS: list[Task] = [
    importlib.import_module(f"{__name__}.{name}").TASK for name in _MODULES
]
