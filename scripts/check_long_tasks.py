"""Self-check for the long-task suite (no model calls).

For every selected task:
  1. untouched workspace must FAIL;
  2. setup + gold must PASS;
  3. every near miss from the module's optional ``NEAR_MISSES`` (callables that
     damage a gold workspace in a plausible way) must FAIL;
  4. setup must be deterministic (two setups give identical files);
  5. report the text volume an agent has to read.

Usage: python scripts/check_long_tasks.py [--task task_392_manuscript_continuity ...]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import sys
import tempfile
import time
from pathlib import Path

from harness_bench.long_tasks import _MODULES, LONG_TASKS


def _tree_digest(ws: Path) -> tuple[str, int, int]:
    h = hashlib.sha256()
    chars = files = 0
    for path in sorted(p for p in ws.rglob("*") if p.is_file() and ".git" not in p.parts):
        data = path.read_bytes()
        h.update(str(path.relative_to(ws)).encode() + b"\0" + data)
        files += 1
        try:
            chars += len(data.decode("utf-8"))
        except UnicodeDecodeError:
            chars += len(data)
    return h.hexdigest(), files, chars


def check(task, module) -> list[str]:
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        wa, wb = Path(a), Path(b)
        t0 = time.monotonic()
        task.setup(wa)
        t_setup = time.monotonic() - t0
        task.setup(wb)
        da, files, chars = _tree_digest(wa)
        if da != _tree_digest(wb)[0]:
            problems.append("setup is not deterministic")
        t0 = time.monotonic()
        untouched = task.verify(wa)
        t_verify_empty = time.monotonic() - t0
        if untouched.passed:
            problems.append("untouched workspace passes")
        task.apply_gold(wb)
        t0 = time.monotonic()
        gold = task.verify(wb)
        t_verify_gold = time.monotonic() - t0
        if not gold.passed:
            problems.append(f"gold fails: {gold.message}")
    near = getattr(module, "NEAR_MISSES", [])
    for miss in near:
        with tempfile.TemporaryDirectory() as c:
            wc = Path(c)
            task.setup(wc)
            task.apply_gold(wc)
            miss(wc)
            result = task.verify(wc)
            if result.passed:
                problems.append(f"near miss {miss.__name__} passes")
    print(
        f"{'OK ' if not problems else 'BAD'} {task.id}: files={files} chars={chars:,} "
        f"setup={t_setup:.1f}s verify(empty)={t_verify_empty:.1f}s "
        f"verify(gold)={t_verify_gold:.1f}s near_misses={len(near)}"
    )
    print(f"    untouched: {untouched.message[:160]}")
    print(f"    gold:      {gold.message[:160]}")
    for problem in problems:
        print(f"    !! {problem}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", action="append", help="registered task id")
    parser.add_argument(
        "--module",
        action="append",
        help="module name inside harness_bench.long_tasks, e.g. wip_t01_foo (work in progress)",
    )
    args = parser.parse_args()
    names = args.module or _MODULES
    modules = {}
    for name in names:
        module = importlib.import_module(f"harness_bench.long_tasks.{name}")
        modules[module.TASK.id] = module
    if args.module:
        selected = [m.TASK for m in modules.values()]
    else:
        selected = [t for t in LONG_TASKS if not args.task or t.id in args.task]
    bad = 0
    for task in selected:
        bad += bool(check(task, modules[task.id]))
    print(f"\n{len(selected) - bad}/{len(selected)} tasks OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
