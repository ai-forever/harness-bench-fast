#!/usr/bin/env python3
"""Build the model/harness selection guidance (OpenSpec-adjacent research layer).

Reads ``results/*.json`` (the same published set the fork table renders) and
derives an evidence-based selection layer per
``docs/adr/0006-model-selection-guidance.md``:

* ``docs/model-selection-matrix.json`` — machine-readable recommendation table.
* ``docs/model-selection-guidance.md`` (+ ``.en.md``) — the guarded auto-block
  with the recommendation table, Pareto flags and named anti-patterns.

Numbers reuse the repo's own classifiers (``fork_metadata``) and the reconciled
premium ₽ (``fork_ladder_override``) so they agree with the published fork rows.

Usage:
    python3 scripts/build_model_selection.py            # rewrite artifacts
    python3 scripts/build_model_selection.py --check    # exit 1 if stale
    python3 scripts/build_model_selection.py --lint     # exit 1 on column drift
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fork_ladder_override import apply as _ladder_apply  # noqa: E402
from fork_metrics import steps_from_json  # noqa: E402
from fork_metadata import classify_harness, collect_paths  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_RU = REPO_ROOT / "docs" / "model-selection-guidance.md"
DOC_EN = REPO_ROOT / "docs" / "model-selection-guidance.en.md"
MATRIX = REPO_ROOT / "docs" / "model-selection-matrix.json"

BEGIN_MARKER = "<!-- MSEL:BEGIN -->"
END_MARKER = "<!-- MSEL:END -->"
DASH = "\u2014"
COST_UNIT = "\u20bd"

INCLUDE_RE_SUBSTR = ("_shotlist_", "_full_391", "wave19", "long", "reflect", "feasibility")
EXCLUDE_KEYWORDS = ("part2", "rerun", "pilot", "smoke", "diag", "sample")

# Situation buckets (R2-Q3) and their full task counts.
BUCKET_FULL = {
    "shot-list": 52,
    "light-391": 391,
    "wave19-feasibility": 40,
    "medium-reflect": 20,
    "long-context": 20,
    "hard-coupled": 10,
}
BUCKET_ORDER = ["shot-list", "medium-reflect", "long-context", "wave19-feasibility",
                "light-391", "hard-coupled"]
BUCKET_LABEL = {
    "shot-list": "Shot List (52)",
    "medium-reflect": "Medium · reflect (20)",
    "long-context": "Medium · long (20)",
    "wave19-feasibility": "Medium · feasibility (40)",
    "light-391": "Light (391)",
    "hard-coupled": "Wave 21 · hard-coupled (10)",
}

# Premium models (by published model label) — used for the anti-pattern section.
PREMIUM_LABELS = ("Claude", "GPT-5.5", "GigaChat 3 Pro", "GPT-OSS-120B", "MiniMax", "GLM-5.2")

REQUIRED_COLUMNS = ["Bucket", "Harness", "Model", "Reasoning", "Result", "%",
                    "Cost/solved", "Pareto", "Stem"]

VERSION_KEYS = ("task_set_version", "taskset_version", "suite_version", "version")


def _situation_bucket(stem: str, suite: str) -> str | None:
    lower = stem.lower()
    if "wave19_full_" in stem or suite == "Wave 19 (40)":
        return "wave19-feasibility"
    if "_full_391" in stem or suite == "Light (391)":
        return "light-391"
    if "_shotlist_" in stem or suite == "Shot List (52)":
        return "shot-list"
    if "long" in lower:
        return "long-context"
    if "reflect" in lower:
        return "medium-reflect"
    if "feasibility" in lower or "wave19" in lower:
        return "wave19-feasibility"
    return None


def _version(data: dict) -> str | None:
    for key in VERSION_KEYS:
        value = data.get(key)
        if value:
            return str(value)
    return None


def _confidence(runs: int, full: bool) -> str:
    if runs >= 2:
        return f"high ({runs} runs)"
    if full:
        return "medium (single run, full suite)"
    return "low (single run, partial suite)"


def build_rows() -> list[dict]:
    rows: list[dict] = []
    for name, path in collect_paths().items():
        stem = path.stem
        lower = stem.lower()
        if not any(s in stem for s in INCLUDE_RE_SUBSTR):
            continue
        if any(k in lower for k in EXCLUDE_KEYWORDS):
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
        passed, total = summary.get("passed"), summary.get("total")
        if not isinstance(passed, int) or not isinstance(total, int) or total <= 0:
            print(f"[skip] {name}: bad passed/total", file=sys.stderr)
            continue
        if passed <= 0:
            print(f"[skip] {name}: zero solved (excluded from cost/solved)", file=sys.stderr)
            continue
        harness = classify_harness(stem)
        if harness is None:
            continue
        # reconcile premium ₽ from provider billing when a sidecar exists
        tokens, cost = _ladder_apply(
            stem, summary.get("total_tokens"), summary.get("estimated_cost_rub")
        )
        if not isinstance(cost, (int, float)) or cost <= 0:
            print(f"[skip] {name}: no usable ₽", file=sys.stderr)
            continue
        suite = _suite_label(stem)
        bucket = _situation_bucket(stem, suite)
        if bucket is None:
            continue
        seconds = summary.get("total_seconds")
        rows.append({
            "harness": harness,
            "model": _model_label(stem),
            "reasoning": _reasoning(stem),
            "suite": suite,
            "situation_bucket": bucket,
            "passed": passed,
            "total": total,
            "pass_rate": round(passed / total, 4),
            "cost_rub": round(float(cost), 2),
            "cost_per_solved": round(float(cost) / passed, 4),
            "time_min": round(float(seconds) / 60, 3) if isinstance(seconds, (int, float)) else None,
            "steps": steps_from_json(data),
            "coverage": {
                "runs": 1,
                "full": total >= BUCKET_FULL.get(bucket, total),
                "version": _version(data),
            },
            "stem": stem,
            "tokens": tokens,
        })
    # de-duplicate exact alias rows (same cell + numbers), keep the first stem
    seen: dict[tuple, dict] = {}
    for r in sorted(rows, key=lambda r: r["stem"]):
        key = (r["harness"], r["model"], r["reasoning"], r["suite"], r["situation_bucket"],
               r["passed"], r["total"], r["cost_rub"])
        seen.setdefault(key, r)
    rows = list(seen.values())

    # tiers by cost/solved terciles (R2-Q6), computed over the measured set
    vals = sorted(r["cost_per_solved"] for r in rows)
    n = len(vals)
    p33 = vals[max(0, int(n / 3) - 1)] if n else 0.0
    p66 = vals[max(0, int(2 * n / 3) - 1)] if n else 0.0
    for r in rows:
        cps = r["cost_per_solved"]
        r["tier"] = "Default" if cps <= p33 else ("Balanced" if cps <= p66 else "Premium")

    # Pareto frontier per bucket (pass up, cost/solved down)
    for r in rows:
        r["pareto"] = True
        r["dominated_by"] = []
    for bucket in BUCKET_ORDER:
        cell = [r for r in rows if r["situation_bucket"] == bucket]
        for a in cell:
            for b in cell:
                if a is b:
                    continue
                if (b["passed"] >= a["passed"] and b["cost_per_solved"] <= a["cost_per_solved"]
                        and (b["passed"] > a["passed"] or b["cost_per_solved"] < a["cost_per_solved"])):
                    a["pareto"] = False
                    a["dominated_by"].append(b["stem"])
    for r in rows:
        r["dominated_by"] = sorted(set(r["dominated_by"]))
        r["confidence"] = _confidence(r["coverage"]["runs"], r["coverage"]["full"])
    rows.sort(key=lambda r: (BUCKET_ORDER.index(r["situation_bucket"]),
                             not r["pareto"], -r["passed"], r["cost_per_solved"]))
    return rows, {"p33": round(p33, 2), "p66": round(p66, 2)}


def _suite_label(stem: str) -> str:
    if "wave19_full_" in stem:
        return "Wave 19 (40)"
    if "_full_391" in stem:
        return "Light (391)"
    if "_shotlist_" in stem:
        return "Shot List (52)"
    lower = stem.lower()
    if "long" in lower:
        return "Medium (392-471) · long"
    if "reflect" in lower:
        return "Medium (392-471) · reflect"
    if "feasibility" in lower or "wave19" in lower:
        return "Medium (392-471) · feasibility"
    return "Medium (392-471)"


def _model_label(stem: str) -> str:
    # reuse the fork results model labelling for consistency
    from fork_metadata import MODEL_TOKENS  # noqa: E402
    if "thinking_off" in stem:
        return "GLM-5.2 (thinking: off)"
    level = _reasoning(stem)
    for token, model in MODEL_TOKENS:
        if token in stem:
            return f"{model} (thinking: {level})" if level else model
    return DASH


def _reasoning(stem: str) -> str:
    from fork_metadata import THINKING_LEVEL_RE  # noqa: E402
    m = THINKING_LEVEL_RE.search(stem)
    return m.group(1) if m else DASH


def _fmt_result(r: dict) -> str:
    return f"{r['passed']}/{r['total']}"


def _fmt_pct(r: dict) -> str:
    return f"{r['pass_rate'] * 100:.1f}%"


def _fmt_cps(r: dict) -> str:
    return f"{r['cost_per_solved']:.2f}{COST_UNIT}"


def _is_premium(r: dict) -> bool:
    return any(label in (r["model"] or "") for label in PREMIUM_LABELS)


def render_block(rows: list[dict], locale: str = "ru") -> str:
    ru = locale == "ru"
    out: list[str] = []
    if ru:
        out.append(f"Автосинк: {len(rows)} измеренных прогонов · "
                   f"тиры по ₽/решённую задачу (p33/p66 = "
                   f"{_terciles['p33']:.2f}/{_terciles['p66']:.2f}₽)")
    else:
        out.append(f"Auto-sync: {len(rows)} measured runs · tiers by ₽/solved "
                   f"(p33/p66 = {_terciles['p33']:.2f}/{_terciles['p66']:.2f}₽)")
    out.append("")
    header = ("| Bucket | Harness | Model | Reasoning | Result | % | Cost/solved | Pareto | Stem |"
              if ru else
              "| Bucket | Harness | Model | Reasoning | Result | % | Cost/solved | Pareto | Stem |")
    out.append(header)
    out.append("| --- | --- | --- | --- | ---: | ---: | ---: | :-: | --- |")
    for r in rows:
        cells = [
            BUCKET_LABEL[r["situation_bucket"]],
            r["harness"] or DASH,
            r["model"] or DASH,
            r["reasoning"],
            _fmt_result(r),
            _fmt_pct(r),
            _fmt_cps(r),
            "✅" if r["pareto"] else DASH,
            f"`{r['stem']}`",
        ]
        out.append("| " + " | ".join(cells) + " |")

    # anti-patterns: premium, priced, but dominated on a full (comparable) suite
    anti = [r for r in rows if _is_premium(r) and not r["pareto"]
            and r["dominated_by"] and r["coverage"]["full"]]
    if anti:
        out.append("")
        head = ("**Anti-patterns — переплата без выигрыша** (доминируемы более дешёвой "
                "конфигурацией с тем же/лучшим результатом):" if ru else
                "**Anti-patterns — paying without winning** (dominated by a cheaper config "
                "with equal/better result):")
        out.append(head)
        out.append("")
        for r in sorted(anti, key=lambda r: -r["cost_per_solved"])[:12]:
            dom = r["dominated_by"][0]
            out.append(f"- `{r['model']}` @ {_fmt_cps(r)} ({r['situation_bucket']}) "
                       f"← `{dom}`" if ru else
                       f"- `{r['model']}` @ {_fmt_cps(r)} ({r['situation_bucket']}) "
                       f"← `{dom}`")
    return "\n".join(out)


def render_matrix(rows: list[dict], terciles: dict) -> str:
    payload = {
        "generated": "auto",
        "branch": "research/model-selection-guidance",
        "note": "READ-ONLY analysis layer; results/*.json; premium ₽ from provider billing.",
        "terciles_cps": terciles,
        "buckets_full": BUCKET_FULL,
        "rows": rows,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def splice(doc: str, block: str, path: Path) -> str:
    begin = doc.find(BEGIN_MARKER)
    end = doc.find(END_MARKER)
    if begin == -1 or end == -1 or end < begin:
        raise SystemExit(f"error: markers not found in {path}")
    return f"{doc[: begin + len(BEGIN_MARKER)]}\n{block}\n{doc[end:]}"


def lint_docs() -> list[str]:
    problems: list[str] = []
    for path in (DOC_RU, DOC_EN):
        if not path.exists():
            problems.append(f"{path.name}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        if BEGIN_MARKER not in text or END_MARKER not in text:
            problems.append(f"{path.name}: markers missing")
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("|") and "Pareto" in stripped and "Stem" in stripped:
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                missing = [c for c in REQUIRED_COLUMNS if c not in cells]
                if missing:
                    problems.append(f"{path.name}: table missing {', '.join(missing)}")
    return problems


_terciles: dict = {"p33": 0.0, "p66": 0.0}


def main(argv: list[str]) -> int:
    if "--lint" in argv:
        problems = lint_docs()
        if problems:
            for p in problems:
                print(p, file=sys.stderr)
            return 1
        print("lint: guidance tables have canonical columns")
        return 0

    rows, terciles = build_rows()
    _terciles.update(terciles)

    check = "--check" in argv
    stale: list[str] = []

    # matrix JSON
    matrix_text = render_matrix(rows, terciles)
    if check:
        if not MATRIX.exists() or MATRIX.read_text(encoding="utf-8") != matrix_text:
            stale.append(MATRIX.name)
    else:
        MATRIX.write_text(matrix_text, encoding="utf-8")

    for path, locale in ((DOC_RU, "ru"), (DOC_EN, "en")):
        try:
            doc = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot read {path}: {exc}", file=sys.stderr)
            return 2
        updated = splice(doc, render_block(rows, locale), path)
        if check:
            if updated != doc:
                stale.append(path.name)
        elif updated != doc:
            path.write_text(updated, encoding="utf-8")
            print(f"{path.name}: updated ({len(rows)} rows)")
        else:
            print(f"{path.name}: up to date ({len(rows)} rows)")

    if check:
        if stale:
            print("STALE: " + ", ".join(stale) + " — run build_model_selection.py")
            return 1
        print(f"up to date ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
