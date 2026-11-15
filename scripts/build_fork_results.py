#!/usr/bin/env python3
"""Rebuild the auto-synced fork results block in docs/fork-results.md.

Scans ``results/*.json`` (the repo root is resolved from this script's own
location: ``<repo>/scripts/..``), keeps only the "main" runs, and regenerates
the markdown summary table between the ``FORK:RESULTS`` markers.

Usage:
    python3 scripts/build_fork_results.py           # rewrite the block
    python3 scripts/build_fork_results.py --check   # exit 1 if the block is stale
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fork_i18n import t  # noqa: E402
from fork_metrics import steps_from_json  # noqa: E402
from fork_ladder_override import apply as _ladder_apply  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = REPO_ROOT / "docs" / "fork-results.md"
DOC_EN_PATH = REPO_ROOT / "docs" / "fork-results.en.md"

BEGIN_MARKER = "<!-- FORK:RESULTS:BEGIN -->"
END_MARKER = "<!-- FORK:RESULTS:END -->"

# The fork keeps its published runs in ``<repo>/results``; the shared project
# run store lives one level up and uses the same file names. Aggregate both,
# de-duplicated by file name (the checkout's own copy wins on a tie).

from fork_metadata import (  # noqa: E402
    HARNESS_PREFIXES,
    MODEL_TOKENS,
    THINKING_LEVEL_RE,
    PROFILE_RULES as _PROFILE_RULES_META,
    RESULT_DIRS,

    classify_harness,
    classify_profile,
    collect_paths,
)

# build_fork_results embeds thinking level and thinking_off fallback in model label.
THINKING_OFF_SUFFIX = "thinking_off"
THINKING_OFF_MODEL = "GLM-5.2 (thinking: off)"


def _thinking_level(stem: str) -> str | None:
    m = THINKING_LEVEL_RE.search(stem)
    return m.group(1) if m else None

# suite key -> (display name, sort order)
SUITES = {
    "shotlist": ("Shot List (52)", 0),
    "full_391": ("Light (391)", 1),
    "wave19_full": ("Wave 19 (40)", 2),
    "medium_392_471": ("Medium (392-471)", 3),
}
SUITE_ORDER = {name: order for name, order in SUITES.values()}

INCLUDE_RE = re.compile(r"(_shotlist_|_full_391|wave19|long|reflect|feasibility)")
EXCLUDE_KEYWORDS = ("part2", "rerun", "pilot", "smoke", "diag", "sample")

DASH = "\u2014"
COST_UNIT = "\u20bd"  # ₽

# --- lint: canonical columns in the fork's result tables -------------------
LINT_FILES = [REPO_ROOT / "FORK.md", REPO_ROOT / "FORK.en.md", DOC_PATH, DOC_EN_PATH]
REQUIRED_COLUMNS = ["Harness", "Profile", "Model", "Result", "%", "Time", "Steps", "Tokens"]


def _is_results_header(cells: list[str]) -> bool:
    low = [c.lower() for c in cells]
    return "model" in low and "result" in low


def _missing_columns(cells: list[str]) -> list[str]:
    missing: list[str] = []
    for req in REQUIRED_COLUMNS:
        if req == "Tokens":
            ok = any(c.lower().startswith("tokens") for c in cells)
        else:
            ok = any(c.lower() == req.lower() for c in cells)
        if not ok:
            missing.append(req)
    return missing


def lint_docs() -> list[tuple[str, int, list[str]]]:
    problems: list[tuple[str, int, list[str]]] = []
    for path in LINT_FILES:
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if _is_results_header(cells):
                missing = _missing_columns(cells)
                if missing:
                    problems.append((path.name, lineno, missing))
    return problems


def classify_suite(stem: str) -> str | None:
    if "wave19_full_" in stem:
        return "Wave 19 (40)"
    if "_full_391" in stem:
        return "Light (391)"
    if "_shotlist_" in stem:
        return "Shot List (52)"
    lower = stem.lower()
    if "long" in lower or "reflect" in lower or "feasibility" in lower or "wave19" in lower:
        return "Medium (392-471)"
    return None


def classify_model(stem: str) -> str | None:
    if THINKING_OFF_SUFFIX in stem:
        return THINKING_OFF_MODEL
    level = _thinking_level(stem)
    for token, model in MODEL_TOKENS:
        if token in stem:
            return f"{model} (thinking: {level})" if level else model
    return None





def fmt_result(passed, total) -> str:
    if passed is None or total is None:
        return DASH
    return f"{int(passed)}/{int(total)}"


def fmt_pct(pass_rate) -> str:
    if pass_rate is None:
        return DASH
    pct = pass_rate * 100 if pass_rate <= 1 else float(pass_rate)
    return f"{pct:.1f}%"


def fmt_time(seconds) -> str:
    if seconds is None:
        return DASH
    return f"{float(seconds) / 60:.1f}m"


def fmt_int(value) -> str:
    if value is None:
        return DASH
    return f"{int(value):,}".replace(",", " ")


def fmt_cost(value) -> str:
    if value is None:
        return DASH
    return f"{float(value):,.0f}".replace(",", " ") + COST_UNIT


def build_rows() -> list[dict]:
    rows: list[dict] = []
    for name, path in collect_paths().items():
        stem = path.stem
        lower = stem.lower()
        if not INCLUDE_RE.search(stem):
            continue
        if any(keyword in lower for keyword in EXCLUDE_KEYWORDS):
            continue
        suite = classify_suite(stem)
        if suite is None:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[skip] {name}: unreadable ({exc})", file=sys.stderr)
            continue
        summary = data.get("summary")
        if not isinstance(summary, dict):
            print(f"[skip] {name}: no summary object", file=sys.stderr)
            continue
        # Reconciled ladder runs: replace published tokens/₽ with provider billing.
        tokens, cost = _ladder_apply(
            stem, summary.get("total_tokens"), summary.get("estimated_cost_rub")
        )
        rows.append(
            {
                "harness": classify_harness(stem),
                "suite": suite,
                "profile": classify_profile(stem),
                "model": classify_model(stem),
                "result": fmt_result(summary.get("passed"), summary.get("total")),
                "pct": fmt_pct(summary.get("pass_rate")),
                "time": fmt_time(summary.get("total_seconds")),
                "steps": fmt_int(steps_from_json(data)),
                "tokens": fmt_int(tokens),
                "cost": fmt_cost(cost),
                "_passed": summary.get("passed") or 0,
            }
        )
    rows.sort(
        key=lambda r: (
            (0, r["harness"]) if r["harness"] else (1, ""),
            SUITE_ORDER[r["suite"]],
            r["model"] or "",
            -(r["_passed"] or 0),
        )
    )
    return rows


def render_block(rows: list[dict], locale: str = "ru") -> str:
    lines = [
        t(locale, "autosync_header", n=len(rows)),
        "",
        "| Harness | Suite | Profile | Model | Result | % | Time | Steps | Tokens (cost) |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        cells = [
            r["harness"] or DASH,
            r["suite"],
            r["profile"] or DASH,
            r["model"] or DASH,
            r["result"],
            r["pct"],
            r["time"],
            r["steps"],
            f"{r['tokens']} ({r['cost']})",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def splice(doc: str, block: str, path: Path = DOC_PATH) -> str:
    begin = doc.find(BEGIN_MARKER)
    end = doc.find(END_MARKER)
    if begin == -1 or end == -1 or end < begin:
        raise SystemExit(f"error: markers not found in {path}")
    head = doc[: begin + len(BEGIN_MARKER)]
    tail = doc[end:]
    return f"{head}\n{block}\n{tail}"


def main(argv: list[str]) -> int:
    if "--lint" in argv:
        problems = lint_docs()
        if problems:
            for name, lineno, missing in problems:
                print(
                    f"{name}:{lineno}: result table missing columns: {', '.join(missing)}",
                    file=sys.stderr,
                )
            return 1
        print("lint: all result tables have canonical columns")
        return 0
    check = "--check" in argv
    rows = build_rows()
    stale: list[str] = []
    for path, locale in ((DOC_PATH, "ru"), (DOC_EN_PATH, "en")):
        block = render_block(rows, locale)
        try:
            doc = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot read {path}: {exc}", file=sys.stderr)
            return 2
        updated = splice(doc, block, path)
        if check:
            if updated != doc:
                stale.append(path.name)
        elif updated != doc:
            path.write_text(updated, encoding="utf-8")
            print(f"{path.name}: updated ({len(rows)} runs)")
        else:
            print(f"{path.name}: already up to date ({len(rows)} runs)")
    if check:
        if stale:
            print("STALE: " + ", ".join(stale) + " \u2014 run: python3 scripts/build_fork_results.py")
            return 1
        print(f"up to date ({len(rows)} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
