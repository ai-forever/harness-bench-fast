"""Regression tests: Pi runner resolves per-task timeout floors/ceilings.

Mirrors openspec/changes/add-pi-runner-task-timeout-floors (tasks 2.1-2.3).
`run_pi_bench.py` lives at the project root, one level above this checkout.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_BENCH_DIR = Path(__file__).resolve().parents[1]  # harness-bench-fast/
# The Pi runner lives at the repo root (it was moved into this repo); keep a
# fallback to the legacy "one level above the checkout" layout.
_RUNNER_CANDIDATES = (
    _BENCH_DIR / "run_pi_bench.py",
    _BENCH_DIR.parent / "run_pi_bench.py",
)
_RUNNER_PATH = next((p for p in _RUNNER_CANDIDATES if p.exists()), _RUNNER_CANDIDATES[0])

# Make the shared package importable regardless of the invocation cwd.
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))

# Load the runner module by path (it is not an installed package).
_spec = importlib.util.spec_from_file_location("run_pi_bench", _RUNNER_PATH)
assert _spec is not None and _spec.loader is not None, _RUNNER_PATH
module = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("run_pi_bench", module)
_spec.loader.exec_module(module)

from harness_bench.tasks import get_task  # noqa: E402  (needs sys.path above)


def test_long_task_floor_raises_below_floor_timeout():
    """(a) long task (min=7200) with --timeout 2400 resolves to 7200."""
    task = get_task("task_392_manuscript_continuity")
    assert task.min_timeout_seconds == 7200
    assert module.effective_timeout_for_task(2400, task) == 7200


def test_task_432_max_clamps_above_ceiling_timeout():
    """(b) task_432 (min=max=3600) with --timeout 7200 resolves to 3600."""
    task = get_task("task_432_clock_contract")
    assert task.min_timeout_seconds == 3600
    assert task.max_timeout_seconds == 3600
    assert module.effective_timeout_for_task(7200, task) == 3600


def test_task_without_floors_is_unchanged():
    """(c) a task with no floors keeps the configured timeout."""
    task = get_task("task_01_create_hello")
    assert getattr(task, "min_timeout_seconds", None) is None
    assert getattr(task, "max_timeout_seconds", None) is None
    assert module.effective_timeout_for_task(2400, task) == 2400
