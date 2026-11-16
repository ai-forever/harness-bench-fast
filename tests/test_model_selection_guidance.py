"""Guard tests for the model-selection guidance generator (v2).

Pins the review fixes (ADR-0007 / PR: feat/guidance-v2):
  * Pareto dominance only within the SAME task set (bucket × total) and by
    pass_rate — a 5/5 run must not be dominated by a 17/20 run.
  * zero-solved runs are kept (shown as 0/N), never silently dropped.
  * tiers are assigned per bucket and only to full-suite (comparable) rows.
  * the published tables use the canonical columns.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location("build_model_selection", _SCRIPTS / "build_model_selection.py")
assert _spec and _spec.loader
bms = importlib.util.module_from_spec(_spec)
sys.modules["build_model_selection"] = bms
_spec.loader.exec_module(bms)


def _rows():
    rows, _ = bms.build_rows()
    return rows


def test_no_cross_task_set_dominance():
    rows = _rows()
    by_stem = {r["stem"]: r for r in rows}
    for r in rows:
        for dom in r["dominated_by"]:
            other = by_stem[dom]
            assert other["total"] == r["total"], (r["stem"], dom)
            assert other["situation_bucket"] == r["situation_bucket"], (r["stem"], dom)
            # pass_rate comparison, not absolute passed
            assert other["pass_rate"] >= r["pass_rate"], (r["stem"], dom)


def test_perfect_run_is_not_dominated():
    rows = _rows()
    best = [r for r in rows if r["pass_rate"] == 1.0 and r["total"] == 5]
    assert best, "expected the 5/5 reflect run in the measured set"
    for r in best:
        assert r["pareto"] is True, r["stem"]


def test_zero_solved_runs_are_kept():
    rows = _rows()
    zeros = [r for r in rows if r["passed"] == 0]
    assert zeros, "zero-solved runs must not be silently excluded (F-D3)"
    for r in zeros:
        assert r["cost_per_solved"] is None
        assert r["pareto"] is False
        assert r["tier"] is None


def test_tiers_only_for_full_comparable_sets():
    rows = _rows()
    for r in rows:
        if r["tier"] is None:
            continue
        assert r["total"] == bms.BUCKET_FULL[r["situation_bucket"]], r["stem"]
        assert r["cost_per_solved"] is not None, r["stem"]


def test_basis_is_billing_or_estimate():
    rows = _rows()
    assert {r["basis"] for r in rows} <= {"billing", "estimate"}


def test_published_tables_have_canonical_columns():
    for name in ("model-selection-guidance.md", "model-selection-guidance.en.md"):
        text = (bms.REPO_ROOT / "docs" / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("|") and "Pareto" in line and "Stem" in line:
                cells = [c.strip() for c in line.strip("|").split("|")]
                for col in bms.REQUIRED_COLUMNS:
                    assert col in cells, (name, col)
                break
        else:
            raise AssertionError(f"{name}: header row not found")
