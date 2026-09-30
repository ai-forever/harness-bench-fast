"""Self-check for the tool-reflection suite (no model calls).

For every selected task:
  1. untouched workspace must FAIL;
  2. setup + gold must PASS;
  3. every near miss from the module's ``NEAR_MISSES`` (plausible call sequences that
     follow the documentation or skip one deviation) must FAIL, each in a fresh workspace;
  4. the verifier ignores the state file (gold still passes with it deleted) and rejects
     a journal with a dropped entry;
  5. setup is deterministic.

Usage: python scripts/check_reflect_tasks.py [--task reflect_01_supplier_minimum ...]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import sys
import tempfile
import time
from pathlib import Path

from harness_bench.reflect_tasks import _MODULES, REFLECT_TASKS
from harness_bench.reflect_tasks.common import JOURNAL_FILE, STATE_FILE, replay


def _digest(ws: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in ws.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(ws)).encode() + b"\0" + path.read_bytes())
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", action="append", help="task id (repeatable)")
    args = parser.parse_args()
    modules = {importlib.import_module(f"harness_bench.reflect_tasks.{name}").TASK.id: name for name in _MODULES}
    tasks = [t for t in REFLECT_TASKS if not args.task or t.id in args.task]
    failures = 0
    for task in tasks:
        module = importlib.import_module(f"harness_bench.reflect_tasks.{modules[task.id]}")
        problems: list[str] = []
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            wa, wb = Path(a), Path(b)
            task.setup(wa)
            task.setup(wb)
            if _digest(wa) != _digest(wb):
                problems.append("setup is not deterministic")
            untouched = task.verify(wa)
            if untouched.passed:
                problems.append("untouched workspace passes")
            started = time.monotonic()
            task.apply_gold(wa)
            gold_seconds = time.monotonic() - started
            gold = task.verify(wa)
            if not gold.passed:
                problems.append(f"gold fails: {gold.message}")
            result = replay(wa, module.SERVICE)
            gold_calls = len(result.calls) if not isinstance(result, str) else -1
            (wa / STATE_FILE).unlink()
            if not task.verify(wa).passed:
                problems.append("verifier depends on the state file")
            journal = wa / JOURNAL_FILE
            lines = journal.read_text(encoding="utf-8").splitlines()
            if len(lines) > 2:
                journal.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")
                if task.verify(wa).passed:
                    problems.append("journal with a dropped entry still passes")
        near_results = []
        for near in getattr(module, "NEAR_MISSES", []):
            with tempfile.TemporaryDirectory() as c:
                wc = Path(c)
                task.setup(wc)
                near(wc)
                outcome = task.verify(wc)
                near_results.append(f"{near.__name__}: {outcome.message}")
                if outcome.passed:
                    problems.append(f"near miss passes: {near.__name__}")
        status = "OK " if not problems else "BAD"
        failures += bool(problems)
        print(f"{status} {task.id}: gold calls={gold_calls} gold={gold_seconds:.1f}s near_misses={len(near_results)}")
        print(f"    untouched: {untouched.message}")
        print(f"    gold:      {gold.message}")
        for line in near_results:
            print(f"    near miss  {line}")
        for problem in problems:
            print(f"    PROBLEM: {problem}")
    print(f"\n{len(tasks) - failures}/{len(tasks)} tasks OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
