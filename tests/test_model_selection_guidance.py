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


def test_lone_set_is_not_a_frontier_point():
    """A lone (bucket × total) member is trivially non-dominated → not ✅ (F2)."""
    rows = _rows()
    lone = [r for r in rows if r.get("singleton")]
    assert lone, "expected at least one lone task set"
    for r in lone:
        assert r["pareto"] is False, r["stem"]
        assert r["dominated_by"] == [], r["stem"]


def test_zero_solved_runs_are_kept():
    rows = _rows()
    zeros = [r for r in rows if r["passed"] == 0]
    assert zeros, "zero-solved runs must not be silently excluded (F-D3)"
    for r in zeros:
        assert r["cost_per_solved"] is None
        assert r["pareto"] is False
        assert r["tier"] is None


def test_tiers_are_per_comparable_subset():
    """Tiers come from terciles within a (bucket × total) set, so partial waves get
    their own tiers instead of "no tier" (F3)."""
    rows = _rows()
    assert any(r["tier"] is not None
               and r["total"] != bms.BUCKET_FULL[r["situation_bucket"]] for r in rows), \
        "partial waves should now carry a tier"
    for r in rows:
        if r["tier"] is not None:
            assert r["cost_per_solved"] is not None, r["stem"]


def test_recommended_pick_per_full_bucket():
    rows = _rows()
    rec = [r for r in rows if r.get("recommended")]
    assert rec, "expected at least one recommended pick"
    for r in rec:
        assert r["pareto"] and r["coverage"]["full"] and not r["singleton"], r["stem"]


def test_reasoning_uses_na_placeholder():
    rows = _rows()
    assert not any("(thinking: \u2014)" in (r["model"] or "") for r in rows)
    assert any("(thinking: n/a)" in (r["model"] or "") for r in rows)


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
