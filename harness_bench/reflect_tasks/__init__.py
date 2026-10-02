"""Tool-reflection suite: services that do not behave as documented.

Twenty tasks (412-431) in the scored ``ALL_TASKS`` (task-set v0.18.0),
also selected with ``--suite reflect``. In each one a small stateful service, reached only through a
closed client in ``tools/``, deviates from its documentation in ways that show
up only in its responses: ambiguous refusals for correct arguments, an ``ok``
that did less than asked, fields the documentation never mentions. The agent
has to read what the tool returned and adapt. Each module ``tNN_*.py`` defines
one ``TASK``; see ``common.py`` for the conventions and the replay verifier.
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

REFLECT_TASKS: list[Task] = [
    importlib.import_module(f"{__name__}.{name}").TASK for name in _MODULES
]
