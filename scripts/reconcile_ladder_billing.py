#!/usr/bin/env python3
"""Reconcile published fork ₽ with the provider's actual billing.

The premium ladder (Shot List 52 x 6 Anthropic models on key ``kap-hermes``,
2026-11-14) was published with ₽ derived from the runner's
``agent_input_tokens``/``agent_output_tokens``. On the runner revision used for
those runs ``_extract_usage`` walked the whole Pi-NDJSON recursively and counted
the same ``usage`` block three times, so the runner ₽ were inflated ~x3.

This script treats the Cloud.ru activity export as the source of truth for ₽:

* reads one or more Cloud.ru activity CSV exports
  (columns: ``date,department,api_key,model,input_tokens,output_tokens,cost``);
* de-duplicates rows by ``(date, api_key, model, input_tokens, output_tokens,
  cost)`` *before* summing — the two provided exports fully overlap;
* filters by ``--key`` (default ``kap-hermes``);
* aggregates per model: row count, summed input/output tokens and ₽;
* writes a per-run sidecar ``results/reconciled/pi_shotlist_<token>_<date>.json``
  with ``{model, input_tokens, output_tokens, cost_rub}`` that the fork
  generators read as a data override;
* prints the "published vs provider" diff.

Usage:
    python3 scripts/reconcile_ladder_billing.py [CSV ...] [options]

Options:
    --key KEY        billing key to reconcile (default: kap-hermes)
    --run-date DATE  ladder run date suffix for sidecar names (default: 20261114)
    --out-dir DIR    sidecar output dir (default: results/reconciled)
    --json           print the aggregate as JSON and do not write sidecars
    --check          do not modify files; exit 0 iff every sidecar is current
    -h, --help

With no CSV paths the vendored fixtures in ``tools/billing-fixtures/`` are used.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "results" / "reconciled"
DEFAULT_FIXTURES = [
    REPO_ROOT / "tools" / "billing-fixtures" / "bill_old.csv",
    REPO_ROOT / "tools" / "billing-fixtures" / "bill_new.csv",
]

# Cloud.ru provider model id -> short fork token.
PROVIDER_MODEL_TO_TOKEN: dict[str, str] = {
    "anthropic/claude-sonnet-4.5": "sonnet45",
    "anthropic/claude-sonnet-4.6": "sonnet46",
    "anthropic/claude-opus-5.5": "opus55",
    "anthropic/claude-opus-4.8": "opus48",
    "anthropic/claude-fable-5.1": "fable51",
    "anthropic/claude-fable-5": "fable5",
}

# Tokens that belong to the premium ladder (one published run each).
LADDER_TOKENS = tuple(PROVIDER_MODEL_TO_TOKEN.values())

REQUIRED_COLUMNS = ("date", "department", "api_key", "model", "input_tokens", "output_tokens", "cost")


def _read_rows(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise SystemExit(f"error: {path}: missing columns: {', '.join(missing)}")
            for row in reader:
                rows.append(row)
    return rows


def _dedupe(rows: list[dict]) -> list[dict]:
    """Drop exact duplicate billing rows (same day, key, model, tokens, cost)."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in rows:
        key = (
            r["date"],
            r["api_key"],
            r["model"],
            r["input_tokens"],
            r["output_tokens"],
            r["cost"],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _aggregate(rows: list[dict], key: str) -> dict[str, dict]:
    """Return {provider_model: {rows, input_tokens, output_tokens, cost_rub}}."""
    agg: dict[str, dict] = {}
    for r in rows:
        if r["api_key"] != key:
            continue
        bucket = agg.setdefault(
            r["model"], {"rows": 0, "input_tokens": 0, "output_tokens": 0, "cost_rub": 0.0}
        )
        bucket["rows"] += 1
        bucket["input_tokens"] += int(r["input_tokens"] or 0)
        bucket["output_tokens"] += int(r["output_tokens"] or 0)
        bucket["cost_rub"] += float(r["cost"] or 0.0)
    return agg


def _run_stem(token: str, run_date: str) -> str:
    return f"pi_shotlist_{token}_{run_date}"


def _sidecar_payload(provider_model: str, bucket: dict, token: str, run_date: str) -> dict:
    return {
        "model": provider_model,
        "input_tokens": bucket["input_tokens"],
        "output_tokens": bucket["output_tokens"],
        "cost_rub": round(bucket["cost_rub"], 2),
        "token": token,
        "run_date": run_date,
        "rows": bucket["rows"],
    }


def _expected_sidecars(agg: dict[str, dict], run_date: str) -> dict[str, dict]:
    """{sidecar filename: payload} for every ladder model present in the billing."""
    out: dict[str, dict] = {}
    for provider_model, token in PROVIDER_MODEL_TO_TOKEN.items():
        bucket = agg.get(provider_model)
        if bucket is None:
            continue
        payload = _sidecar_payload(provider_model, bucket, token, run_date)
        out[f"{_run_stem(token, run_date)}.json"] = payload
    return out


def _published(run_stem: str) -> dict | None:
    """Published tokens/₽ for a run stem, from results/<stem>.json (may be absent)."""
    path = REPO_ROOT / "results" / f"{run_stem}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return None
    return {
        "tokens": summary.get("total_tokens"),
        "cost_rub": summary.get("estimated_cost_rub"),
    }


def _fmt_int(value) -> str:
    return "\u2014" if value is None else f"{int(value):,}".replace(",", " ")


def _fmt_rub(value) -> str:
    if value is None:
        return "\u2014"
    return f"{float(value):,.2f}".replace(",", " ") + "\u20bd"


def _print_report(expected: dict[str, dict]) -> None:
    print(f"{'token':10s} {'model':30s} {'rows':>5s} {'input':>10s} {'output':>9s} "
          f"{'provider ₽':>12s} {'published ₽':>12s} {'x':>5s}")
    total_provider = 0.0
    for name in sorted(expected):
        payload = expected[name]
        token = payload["token"]
        run_stem = name[:-5]
        pub = _published(run_stem)
        provider = payload["cost_rub"]
        total_provider += provider
        pub_cost = pub["cost_rub"] if pub else None
        ratio = f"{provider and (pub_cost / provider):.2f}" if pub_cost and provider else "\u2014"
        print(
            f"{token:10s} {payload['model']:30s} {expected[name].get('rows', ''):>5} "
            f"{_fmt_int(payload['input_tokens']):>10s} {_fmt_int(payload['output_tokens']):>9s} "
            f"{_fmt_rub(provider):>12s} {_fmt_rub(pub_cost):>12s} {ratio:>5s}"
        )
    print(f"{'TOTAL':10s} {'':30s} {'':>5s} {'':>10s} {'':>9s} {_fmt_rub(total_provider):>12s}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("csv", nargs="*", type=Path, help="Cloud.ru activity CSV export(s)")
    parser.add_argument("--key", default="kap-hermes", help="billing key (default: kap-hermes)")
    parser.add_argument("--run-date", default="20261114", help="ladder run date suffix")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="sidecar output dir")
    parser.add_argument("--json", action="store_true", help="print aggregate JSON, do not write")
    parser.add_argument("--check", action="store_true", help="verify sidecars; never write")
    args = parser.parse_args(argv)

    paths = args.csv or DEFAULT_FIXTURES
    for path in paths:
        if not path.is_file():
            print(f"error: CSV not found: {path}", file=sys.stderr)
            return 2

    rows = _read_rows(paths)
    deduped = _dedupe(rows)
    agg = _aggregate(deduped, args.key)
    expected = _expected_sidecars(agg, args.run_date)
    unmapped = sorted(m for m in agg if m not in PROVIDER_MODEL_TO_TOKEN)

    if args.json:
        payload = {
            "key": args.key,
            "run_date": args.run_date,
            "source_rows": len(rows),
            "deduped_rows": len(deduped),
            "total_cost_rub": round(sum(b["cost_rub"] for b in agg.values()), 2),
            "models": expected,
            "unmapped_models": unmapped,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.check:
        stale: list[str] = []
        for name, payload in expected.items():
            path = args.out_dir / name
            want = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            if not path.is_file() or path.read_text(encoding="utf-8") != want:
                stale.append(name)
        # Extra sidecars for this run-date are stale too (idempotence).
        known = set(expected)
        for path in sorted(args.out_dir.glob(f"*_{args.run_date}.json")):
            if path.name not in known:
                stale.append(path.name)
        if stale:
            print("STALE: " + ", ".join(sorted(set(stale))) + " \u2014 run: python3 scripts/reconcile_ladder_billing.py")
            return 1
        print(f"reconciled sidecars up to date ({len(expected)} runs)")
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, payload in expected.items():
        path = args.out_dir / name
        want = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if not path.is_file() or path.read_text(encoding="utf-8") != want:
            path.write_text(want, encoding="utf-8")
            written += 1
    _print_report(expected)
    if unmapped:
        print(f"note: {len(unmapped)} non-ladder model(s) for key {args.key}: {', '.join(unmapped)}")
    print(f"sidecars: {written} written, {len(expected) - written} unchanged -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
