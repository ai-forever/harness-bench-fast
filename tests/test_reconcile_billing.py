#!/usr/bin/env python3
"""Tests for scripts/reconcile_billing.py (OpenSpec add-billing-reconciliation).

Covers the provider-export reconciliation contract:

  * dedup of two exports sharing a window (each full row counted once),
  * aggregation of actual ₽ / tokens by (api_key, model) and by department,
  * ``--key`` / ``--model`` / ``--since`` / ``--until`` filters,
  * ``--json`` output and the ``--compare`` discrepancy factor.

All fixtures are tiny inline CSVs written to ``tmp_path`` -- the tool never
depends on the big provider dumps.

Run: python3 -m pytest tests/test_reconcile_billing.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
for _p in (str(_ROOT), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import reconcile_billing as rb  # noqa: E402

HEADER = '"date","department","api_key","model","input_tokens","output_tokens","cost"\n'


def _write(path: Path, rows: list[str]) -> Path:
    path.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


# Two exports that overlap on row C (same full tuple appears in both).
OLD_ROWS = [
    "2026-11-14 10:00:00,kap-hermes,kap-hermes,anthropic/claude-sonnet-4.6,1000,100,5.00",
    "2026-11-14 10:05:00,kap-hermes,kap-hermes,anthropic/claude-opus-5.5,2000,200,9.00",
    "2026-11-15 09:00:00,kap-openclaw,kap-openclaw,anthropic/claude-sonnet-4.6,3000,300,7.00",
]
NEW_ROWS = [
    "2026-11-15 09:00:00,kap-openclaw,kap-openclaw,anthropic/claude-sonnet-4.6,3000,300,7.00",  # dup of OLD
    "2026-11-15 09:30:00,kap-openclaw,kap-openclaw,ai-sage/GigaChat3.5-432B-A28B,4000,400,1.50",
]


def _fixtures(tmp_path: Path) -> tuple[Path, Path]:
    return _write(tmp_path / "old.csv", OLD_ROWS), _write(tmp_path / "new.csv", NEW_ROWS)


# --------------------------------------------------------------------------- #
# dedup
# --------------------------------------------------------------------------- #
def test_dedupe_overlapping_exports_counts_each_row_once(tmp_path):
    old, new = _fixtures(tmp_path)
    rows = rb.load_rows([old, new])
    assert len(rows) == 5  # 3 + 2 raw rows

    unique = rb.dedupe(rows)
    assert len(unique) == 4  # overlapping row C collapses

    summary = rb.build_summary(unique, dedup_total_rows=len(unique))
    # 5.00 + 9.00 + 7.00 + 1.50 = 22.50 (row C not doubled)
    assert summary["total_cost_rub"] == 22.5
    assert summary["rows"] == 4

    naive = sum(float(r["cost"]) for r in rows)
    assert naive == 29.5  # without dedup the overlapping window double-counts
    assert naive > summary["total_cost_rub"]


def test_dedupe_is_idempotent(tmp_path):
    old, new = _fixtures(tmp_path)
    once = rb.dedupe(rb.load_rows([old, new]))
    twice = rb.dedupe(once)
    assert once == twice


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def test_aggregate_by_key_and_model(tmp_path):
    old, new = _fixtures(tmp_path)
    summary = rb.build_summary(rb.dedupe(rb.load_rows([old, new])), dedup_total_rows=4)

    km = summary["by_key_model"]
    hermes_sonnet = km["kap-hermes|anthropic/claude-sonnet-4.6"]
    assert hermes_sonnet == {"input_tokens": 1000, "output_tokens": 100, "cost_rub": 5.0, "rows": 1}

    oc_sonnet = km["kap-openclaw|anthropic/claude-sonnet-4.6"]
    assert oc_sonnet["cost_rub"] == 7.0
    assert oc_sonnet["input_tokens"] == 3000 and oc_sonnet["output_tokens"] == 300

    # department rollup
    deps = summary["by_department"]
    assert deps["kap-hermes"]["cost_rub"] == 14.0
    assert deps["kap-openclaw"]["cost_rub"] == 8.5


def test_aggregate_tokens_totals(tmp_path):
    old, new = _fixtures(tmp_path)
    summary = rb.build_summary(rb.dedupe(rb.load_rows([old, new])), dedup_total_rows=4)
    assert summary["total_input_tokens"] == 10_000
    assert summary["total_output_tokens"] == 1_000


# --------------------------------------------------------------------------- #
# filters
# --------------------------------------------------------------------------- #
def test_filter_by_key(tmp_path):
    old, new = _fixtures(tmp_path)
    rows = rb.dedupe(rb.load_rows([old, new]))
    got = rb.apply_filters(rows, key="kap-hermes")
    assert len(got) == 2
    assert sum(float(r["cost"]) for r in got) == 14.0


def test_filter_by_model(tmp_path):
    old, new = _fixtures(tmp_path)
    rows = rb.dedupe(rb.load_rows([old, new]))
    got = rb.apply_filters(rows, model="anthropic/claude-sonnet-4.6")
    assert len(got) == 2  # hermes + openclaw sonnet
    assert sum(float(r["cost"]) for r in got) == 12.0


def test_filter_since_until(tmp_path):
    old, new = _fixtures(tmp_path)
    rows = rb.dedupe(rb.load_rows([old, new]))

    since = rb.apply_filters(rows, since="2026-11-15")
    assert len(since) == 2 and sum(float(r["cost"]) for r in since) == 8.5

    # date-only upper bound is inclusive of the whole day
    until = rb.apply_filters(rows, until="2026-11-14")
    assert len(until) == 2 and sum(float(r["cost"]) for r in until) == 14.0

    window = rb.apply_filters(rows, since="2026-11-15 09:15", until="2026-11-15 09:45")
    assert len(window) == 1 and window[0]["model"] == "ai-sage/GigaChat3.5-432B-A28B"


def test_filter_combined(tmp_path):
    old, new = _fixtures(tmp_path)
    rows = rb.dedupe(rb.load_rows([old, new]))
    got = rb.apply_filters(rows, key="kap-openclaw", since="2026-11-15 09:15")
    assert len(got) == 1 and got[0]["cost"] == "1.50"


def test_bad_timestamp_exits():
    try:
        rb.parse_ts("not-a-date")
    except SystemExit as exc:
        assert "bad timestamp" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("parse_ts should SystemExit on garbage")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_json(tmp_path, capsys):
    old, new = _fixtures(tmp_path)
    rc = rb.main(["--key", "kap-hermes", "--json", str(old), str(new)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_cost_rub"] == 14.0
    assert payload["filters"]["key"] == "kap-hermes"
    assert payload["window"] == {"start": "2026-11-14 10:00:00", "end": "2026-11-14 10:05:00"}


def test_cli_text_shows_total(tmp_path, capsys):
    old, new = _fixtures(tmp_path)
    rb.main([str(old), str(new)])
    out = capsys.readouterr().out
    assert "TOTAL: 22.50" in out
    assert "by (api_key, model)" in out


def test_cli_compare(tmp_path, capsys):
    old, new = _fixtures(tmp_path)
    results = tmp_path / "results"
    results.mkdir()
    (results / "a.json").write_text(json.dumps({"summary": {"estimated_cost_rub": 10.0}}), encoding="utf-8")
    (results / "b.json").write_text(json.dumps({"summary": {"estimated_cost_rub": 5.0}}), encoding="utf-8")
    (results / "c_nosummary.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")

    rc = rb.main(["--key", "kap-hermes", "--compare", str(results), "--json", str(old), str(new)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    cmp = payload["comparison"]
    assert cmp["estimated_total_rub"] == 15.0
    assert cmp["fact_total_rub"] == 14.0
    assert cmp["abs_diff_rub"] == -1.0
    assert cmp["ratio_fact_over_estimate"] == round(14.0 / 15.0, 4)
    assert cmp["artifact_count"] == 2  # c_nosummary.json ignored
