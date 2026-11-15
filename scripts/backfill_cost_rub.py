#!/usr/bin/env python3
"""Recompute summary.estimated_cost_rub for committed result artifacts.

Usage:
    python3 scripts/backfill_cost_rub.py [results_dir] [--dry-run]

Finds all results/*.json that have a top-level 'summary' block.
Recomputes estimated_cost_rub using the per-model cost resolver.
Skips files without a top-level summary.

Token fields checked:
  summary.total_input_tokens   → input
  summary.total_output_tokens  → output
  summary.total_cache_read_tokens  (preferred) → cacheRead
  summary.total_cache_write_tokens (preferred) → cacheWrite

Explicit cache buckets are PREFERRED; they are only derived as
``total_tokens - input - output`` for older artifacts that lack them, and the
derived values are then written back so artifacts never re-derive at read time.

Model is detected from the top-level 'model' field, the summary's 'model', the first
task's 'model', the command line, and -- as a last resort when those are absent --
inferred from the artifact FILENAME/family (e.g. ``*_ds41flash_*`` ->
DeepSeek-V4.1-Flash; ``*_giga35_*`` / ``*_gigachat35_*`` -> GigaChat 3.5 Reasoning).
Only a genuinely undetectable artifact is reported as ``(unknown -> fallback)`` and
priced with the documented GLM-5.2 fallback (with a WARNING from the resolver).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))
from harness_bench.cost_resolver import (
    compute_cost_rub,
    model_hint_from_filename,
    resolve_model_rates,
)

# Filename/family -> canonical model id. Used ONLY when the artifact exposes no
# model field at all (Pi artifacts carry just ``results`` + ``summary``). Ordered:
# the first matching token wins. Keep this list small and explicit -- add a family
# only when its canonical model id is known and resolvable by the cost resolver.
_FILENAME_MODEL_TOKENS: tuple[tuple[str, str], ...] = (
    ("ds41flash",  "custom-foundation-models-api-cloud-ru/deepseek-ai/DeepSeek-V4.1-Flash"),
    ("gigachat35", "ai-sage/GigaChat3.5-432B-A28B-Reasoning"),
    ("giga35",     "ai-sage/GigaChat3.5-432B-A28B-Reasoning"),
)


def _detect_model_from_name(name: str) -> str | None:
    """Infer a canonical model id from an artifact filename/stem, or None."""
    stem = Path(name).stem.lower()
    for token, model_id in _FILENAME_MODEL_TOKENS:
        if token in stem:
            return model_id
    return None


def _detect_model(data: dict, name: str = "") -> str | None:
    """Try to detect model from result file.

    Fields first (``model`` -> ``summary.model`` -> per-task ``model`` -> ``command``),
    then -- only when every field is absent -- the artifact FILENAME/family via
    :func:`_detect_model_from_name`. Returns None when the model is genuinely
    undeterminable (caller then uses the documented resolver fallback + WARNING).
    """
    if m := data.get("model"):
        return m
    if m := data.get("summary", {}).get("model"):
        return m
    tasks = data.get("tasks") or data.get("results") or []
    for task in tasks:
        if isinstance(task, dict) and (m := task.get("model")):
            return m
    # Try command field
    cmd = data.get("command") or ""
    for part in cmd.split():
        if "/" in part and not part.startswith("-"):
            return part
    # Last resort: infer from the artifact filename/family
    if name:
        return _detect_model_from_name(name)
    return None


def _recompute_file(path: Path, *, dry_run: bool, config_path: str | None) -> bool:
    """Process one result file. Returns True if changed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  SKIP {path.name}: cannot parse ({exc})")
        return False

    summary = data.get("summary")
    if not isinstance(summary, dict):
        print(f"  SKIP {path.name}: no top-level summary")
        return False

    model = _detect_model(data, name=path.name)
    # Pass the artifact filename so the resolver can fall back to its filename-hint
    # map (gigachat3pro, glm52, ...) as a LAST resort before the GLM-5.2 fallback.
    rates = resolve_model_rates(model or "", config_path=config_path, filename=path.name)

    total_in = int(summary.get("total_input_tokens", 0) or 0)
    total_out = int(summary.get("total_output_tokens", 0) or 0)
    total_tok = int(summary.get("total_tokens", 0) or 0)
    # PREFER explicit cache buckets recorded at write time. Only for older
    # artifacts that lack them do we derive total_tokens = input + output +
    # cacheRead + cacheWrite once; the derived values are written back below.
    has_explicit_cache = (
        summary.get("total_cache_read_tokens") is not None
        or summary.get("total_cache_write_tokens") is not None
    )
    if has_explicit_cache:
        total_cr = int(summary.get("total_cache_read_tokens") or 0)
        total_cw = int(summary.get("total_cache_write_tokens") or 0)
    else:
        total_cr = max(total_tok - total_in - total_out, 0) if total_tok else 0
        total_cw = 0

    old_cost = summary.get("estimated_cost_rub")
    new_cost = round(compute_cost_rub(total_in, total_out, total_cr, total_cw, rates), 2)

    resolved_model = model or model_hint_from_filename(path.name)
    model_str = resolved_model or "(unknown → fallback)"
    old_str = f"{old_cost:.2f} ₽" if old_cost is not None else "—"
    print(f"  {path.name}: {old_str} → {new_cost:.2f} ₽  [model={model_str}]")

    buckets_missing = (
        summary.get("total_cache_read_tokens") != total_cr
        or summary.get("total_cache_write_tokens") != total_cw
    )
    if old_cost == new_cost and not buckets_missing:
        return False

    if not dry_run:
        summary["estimated_cost_rub"] = new_cost
        # Record the explicit cache buckets so artifacts never re-derive them.
        summary["total_cache_read_tokens"] = total_cr
        summary["total_cache_write_tokens"] = total_cw
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_dir", nargs="?", default="results",
                        help="Directory with result JSON files (default: results/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing")
    parser.add_argument("--config", metavar="PATH",
                        help="Path to openclaw.json (default: ~/.openclaw/openclaw.json)")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    if not results_dir.is_dir():
        print(f"ERROR: {results_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    files = sorted(results_dir.glob("*.json"))
    if not files:
        print(f"No JSON files found in {results_dir}")
        return

    mode = "DRY RUN" if args.dry_run else "REWRITING"
    print(f"=== backfill_cost_rub ({mode}) — {len(files)} files in {results_dir} ===\n")

    changed = 0
    skipped = 0
    for f in files:
        if _recompute_file(f, dry_run=args.dry_run, config_path=args.config):
            changed += 1
        else:
            skipped += 1

    print(f"\nDone: {changed} updated, {skipped} unchanged/skipped.")
    if args.dry_run:
        print("(dry run — no files written)")


if __name__ == "__main__":
    main()
