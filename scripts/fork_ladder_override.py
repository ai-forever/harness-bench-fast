#!/usr/bin/env python3
"""Data override for reconciled ladder runs (OpenSpec reconcile-premium-ladder-cost).

The premium-ladder runs were published before the runner's token accounting was
fixed to single-count, so their published tokens/₽ are stale. The provider's
activity export is the source of truth for ₽; ``scripts/reconcile_ladder_billing.py``
writes one sidecar per run to ``results/reconciled/<run-stem>.json`` with
``{model, input_tokens, output_tokens, cost_rub}``.

Rather than re-running any benchmark, the fork generators consult this module:
for a run whose file stem has a sidecar, the *row's* tokens become
``input+output`` (single-count) and the ₽ become the provider-billed value.
Pass-rate, time and steps are untouched.

Public API:
    override_for(stem)      -> dict | None   ({tokens, cost_rub, input_tokens, output_tokens})
    apply(stem, tokens, cost) -> (tokens, cost)   # overridden when a sidecar exists
    load_overrides()        -> dict[str, dict]    # {stem: {...}} (cached)
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RECONCILED_DIR = REPO_ROOT / "results" / "reconciled"

_CACHE: dict[str, dict] | None = None


def load_overrides(reconciled_dir: Path | None = None) -> dict[str, dict]:
    """Load ``{run_stem: {tokens, cost_rub, input_tokens, output_tokens}}``.

    Unreadable / malformed sidecars are skipped; a missing directory yields ``{}``.
    """
    global _CACHE
    if reconciled_dir is None and _CACHE is not None:
        return _CACHE
    directory = reconciled_dir if reconciled_dir is not None else RECONCILED_DIR
    overrides: dict[str, dict] = {}
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            try:
                inp = int(data["input_tokens"])
                out = int(data["output_tokens"])
                cost = float(data["cost_rub"])
            except (KeyError, TypeError, ValueError):
                continue
            # Guard: the sidecar is only valid for the run whose stem carries the
            # exact run_date it was reconciled for (stem ends with "_<run_date>").
            # Prevents a stale sidecar from silently overriding a fresh re-run that
            # reuses the same stem.
            run_date = data.get("run_date")
            if not run_date or not path.stem.endswith(f"_{run_date}"):
                continue
            overrides[path.stem] = {
                "tokens": inp + out,
                "cost_rub": cost,
                "input_tokens": inp,
                "output_tokens": out,
            }
    if reconciled_dir is None:
        _CACHE = overrides
    return overrides


def override_for(stem: str) -> dict | None:
    return load_overrides().get(stem)


def apply(stem: str, tokens, cost):
    """Return ``(tokens, cost)`` with the reconciled sidecar substituted when present."""
    override = load_overrides().get(stem)
    if override is None:
        return tokens, cost
    return override["tokens"], override["cost_rub"]
