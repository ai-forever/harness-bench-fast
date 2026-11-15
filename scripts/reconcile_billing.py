#!/usr/bin/env python3
"""Reconcile Cloud.ru Foundation Models billing exports with our benchmark runs.

Provider export ("Raw activity rows (MSK)") is CSV with columns::

    "date","department","api_key","model","input_tokens","output_tokens","cost"

``cost`` is actual ₽ charged for that activity row.  Exports are *windows* and
two exports routinely overlap, so naive concatenation double-counts.  This tool
therefore DEDUPES by the full row tuple
``(date, department, api_key, model, input_tokens, output_tokens, cost)``
before any summing, then aggregates actual ₽ + tokens by ``(api_key, model)``
and by ``department``.

Usage:
    python3 scripts/reconcile_billing.py /tmp/bill_old.csv /tmp/bill_new.csv
    python3 scripts/reconcile_billing.py --key kap-hermes /tmp/bill_*.csv
    python3 scripts/reconcile_billing.py --key kap-openclaw --model anthropic/claude-sonnet-4.6 --json
    python3 scripts/reconcile_billing.py --since "2026-11-14 17:48" --until "2026-11-14 19:00" bill.csv
    python3 scripts/reconcile_billing.py --compare results/ --model anthropic/claude-sonnet-4.6 bill.csv

Filters: ``--key``, ``--model``, ``--since``, ``--until`` (ISO date or datetime).
Output: human-readable tables, or ``--json``.
``--compare DIR`` additionally diffs the filtered actual ₽ against the sum of
``summary.estimated_cost_rub`` of the result artifacts in ``DIR`` and prints the
discrepancy (absolute, %, and ×-factor) for diagnostics.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELDS = ("date", "department", "api_key", "model", "input_tokens", "output_tokens", "cost")

_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Loading / dedup
# --------------------------------------------------------------------------- #
def _norm_cost(value: object) -> Decimal:
    """Normalise a ₽ cost to a Decimal (0 on garbage, trailing zeros removed)."""
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return Decimal(0)


def row_key(row: dict) -> tuple:
    """Full-row dedup key: the complete field tuple, cost normalised."""
    return (
        str(row.get("date", "")).strip(),
        str(row.get("department", "")).strip(),
        str(row.get("api_key", "")).strip(),
        str(row.get("model", "")).strip(),
        int(str(row.get("input_tokens", "0")).strip() or 0),
        int(str(row.get("output_tokens", "0")).strip() or 0),
        _norm_cost(row.get("cost", "0")),
    )


def load_rows(paths) -> list[dict]:
    """Read one or more CSV exports into normalised dict rows."""
    rows: list[dict] = []
    for path in paths:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            missing = [f for f in FIELDS if f not in (reader.fieldnames or [])]
            if missing:
                raise ValueError(f"{path}: missing column(s) {missing}")
            for raw in reader:
                if not (raw.get("date") or "").strip():
                    continue
                rows.append({f: str(raw.get(f, "")).strip() for f in FIELDS})
    return rows


def dedupe(rows) -> list[dict]:
    """Drop rows whose full tuple was already seen (overlapping exports)."""
    seen: set = set()
    unique: list[dict] = []
    for row in rows:
        key = row_key(row)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
def parse_ts(value: str) -> datetime:
    text = value.strip()
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise SystemExit(f"reconcile_billing: bad timestamp {value!r} (use ISO date/datetime)")


def _until_bound(value: str) -> datetime:
    bound = parse_ts(value)
    # date-only upper bound is inclusive of the whole day
    if len(value.strip()) <= len("YYYY-MM-DD"):
        bound = bound + timedelta(days=1) - timedelta(seconds=1)
    return bound


def apply_filters(rows, key=None, model=None, since=None, until=None) -> list[dict]:
    out = rows
    if key:
        out = [r for r in out if r["api_key"] == key]
    if model:
        out = [r for r in out if r["model"] == model]
    if since:
        lo = parse_ts(since)
        out = [r for r in out if parse_ts(r["date"]) >= lo]
    if until:
        hi = _until_bound(until)
        out = [r for r in out if parse_ts(r["date"]) <= hi]
    return out


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _bucket(rows, key_fn) -> dict:
    agg: dict = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "cost": Decimal(0), "rows": 0})
    for row in rows:
        b = agg[key_fn(row)]
        b["input_tokens"] += int(row["input_tokens"])
        b["output_tokens"] += int(row["output_tokens"])
        b["cost"] += _norm_cost(row["cost"])
        b["rows"] += 1
    return agg


def aggregate(rows) -> tuple[dict, dict]:
    """Return ({ (api_key, model): bucket }, { department: bucket })."""
    by_key_model = _bucket(rows, lambda r: (r["api_key"], r["model"]))
    by_department = _bucket(rows, lambda r: r["department"])
    return by_key_model, by_department


def _materialise(agg) -> dict:
    return {
        "|".join(k) if isinstance(k, tuple) else k: {
            "input_tokens": b["input_tokens"],
            "output_tokens": b["output_tokens"],
            "cost_rub": round(float(b["cost"]), 2),
            "rows": b["rows"],
        }
        for k, b in agg.items()
    }


def build_summary(rows, dedup_total_rows: int) -> dict:
    by_key_model, by_department = aggregate(rows)
    total = sum((b["cost"] for b in by_key_model.values()), Decimal(0))
    total_in = sum(b["input_tokens"] for b in by_key_model.values())
    total_out = sum(b["output_tokens"] for b in by_key_model.values())
    dates = sorted(r["date"] for r in rows)
    return {
        "rows": len(rows),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_cost_rub": round(float(total), 2),
        "window": {"start": dates[0], "end": dates[-1]} if dates else None,
        "by_key_model": _materialise(by_key_model),
        "by_department": _materialise(by_department),
        "input_rows": dedup_total_rows,
    }


# --------------------------------------------------------------------------- #
# Optional: compare against result artifacts
# --------------------------------------------------------------------------- #
def collect_estimates(results_dir: str, model: str | None = None) -> dict:
    """Sum ``summary.estimated_cost_rub`` across result JSONs in results_dir."""
    root = Path(results_dir)
    artifacts: list[dict] = []
    total = Decimal(0)
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        summary = data.get("summary")
        if not isinstance(summary, dict):
            continue
        est = summary.get("estimated_cost_rub")
        if est is None:
            continue
        if model and not _artifact_matches(data, model):
            continue
        est_d = _norm_cost(est)
        total += est_d
        artifacts.append({"file": path.name, "estimated_cost_rub": round(float(est_d), 2)})
    return {
        "dir": str(root),
        "model_filter": model,
        "artifacts": artifacts,
        "count": len(artifacts),
        "estimated_total_rub": round(float(total), 2),
    }


def _artifact_matches(data: dict, model: str) -> bool:
    """Best-effort match of a result artifact to a model id (command/filename)."""
    blob = json.dumps(data, ensure_ascii=False).lower()
    leaf = model.split("/")[-1].lower()
    return model.lower() in blob or leaf in blob


def compare_with_estimates(fact_total: float, estimates: dict) -> dict:
    est = estimates["estimated_total_rub"]
    diff = round(fact_total - est, 2)
    pct = round((diff / est * 100.0), 2) if est else None
    ratio = round(fact_total / est, 4) if est else None
    return {
        "fact_total_rub": round(fact_total, 2),
        "estimated_total_rub": est,
        "abs_diff_rub": diff,
        "pct_diff": pct,
        "ratio_fact_over_estimate": ratio,
        "artifact_count": estimates["count"],
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_text(summary: dict, comparison: dict | None = None) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("Cloud.ru billing reconciliation (deduped)")
    lines.append("=" * 72)
    if summary.get("window"):
        lines.append(f"rows: {summary['rows']}   window: {summary['window']['start']} .. {summary['window']['end']}")
    else:
        lines.append("rows: 0  (no data after filters)")
    lines.append(f"tokens: in={summary['total_input_tokens']:,} out={summary['total_output_tokens']:,}")
    lines.append("")
    lines.append("-- by (api_key, model) --")
    if summary["by_key_model"]:
        lines.append(f"{'api_key':<16} {'model':<42} {'in':>14} {'out':>11} {'cost ₽':>12}")
        for k, b in sorted(summary["by_key_model"].items(), key=lambda kv: -kv[1]["cost_rub"]):
            api_key, model = k.split("|", 1)
            lines.append(f"{api_key:<16} {model:<42} {b['input_tokens']:>14,} {b['output_tokens']:>11,} {b['cost_rub']:>12,.2f}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("-- by department --")
    lines.append(f"{'department':<20} {'cost ₽':>12}")
    for dep, b in sorted(summary["by_department"].items(), key=lambda kv: -kv[1]["cost_rub"]):
        lines.append(f"{dep:<20} {b['cost_rub']:>12,.2f}")
    lines.append("")
    lines.append(f"TOTAL: {summary['total_cost_rub']:,.2f} ₽")
    if comparison is not None:
        lines.append("")
        lines.append("-- compare vs results/ summary.estimated_cost_rub --")
        lines.append(f"artifacts matched: {comparison['artifact_count']}")
        lines.append(f"fact:      {comparison['fact_total_rub']:,.2f} ₽")
        lines.append(f"estimated: {comparison['estimated_total_rub']:,.2f} ₽")
        lines.append(f"diff:      {comparison['abs_diff_rub']:,.2f} ₽ ({comparison['pct_diff']}%)")
        lines.append(f"ratio fact/estimate: ×{comparison['ratio_fact_over_estimate']}")
    lines.append("=" * 72)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", nargs="+", help="one or more Cloud.ru CSV exports")
    parser.add_argument("--key", help="filter by api_key (exact)")
    parser.add_argument("--model", help="filter by model (exact)")
    parser.add_argument("--since", help="include rows at/after this ISO date/datetime")
    parser.add_argument("--until", help="include rows at/before this ISO date/datetime (date-only = whole day)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of tables")
    parser.add_argument("--compare", metavar="RESULTS_DIR", help="diff vs summary.estimated_cost_rub of results/*.json")
    args = parser.parse_args(argv)

    rows = load_rows(args.csv)
    unique = dedupe(rows)
    filtered = apply_filters(unique, key=args.key, model=args.model, since=args.since, until=args.until)
    summary = build_summary(filtered, dedup_total_rows=len(unique))

    comparison = None
    if args.compare:
        estimates = collect_estimates(args.compare, model=args.model)
        comparison = compare_with_estimates(summary["total_cost_rub"], estimates)

    if args.json:
        payload = dict(summary)
        payload["filters"] = {"key": args.key, "model": args.model, "since": args.since, "until": args.until}
        if comparison is not None:
            payload["comparison"] = comparison
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(summary, comparison))
    return 0


if __name__ == "__main__":
    sys.exit(main())
