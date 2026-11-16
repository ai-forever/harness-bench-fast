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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fork_ladder_override import apply as _ladder_apply  # noqa: E402
from fork_ladder_override import override_for
from fork_metadata import classify_harness, collect_paths  # noqa: E402
from fork_metrics import steps_from_json  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_RU = REPO_ROOT / "docs" / "model-selection-guidance.md"
DOC_EN = REPO_ROOT / "docs" / "model-selection-guidance.en.md"
HTML_RU = REPO_ROOT / "docs" / "model-selection-guidance.html"
HTML_EN = REPO_ROOT / "docs" / "model-selection-guidance.en.html"
MATRIX = REPO_ROOT / "docs" / "model-selection-matrix.json"

BEGIN_MARKER = "<!-- MSEL:BEGIN -->"
END_MARKER = "<!-- MSEL:END -->"
DASH = "\u2014"
NA = "\u043d/\u0434"  # no data (distinct from "—" = not Pareto)
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

REQUIRED_COLUMNS = ["Bucket", "Harness", "Model", "Tier", "Result",
                    "Cost/solved", "Basis", "Pareto", "Stem"]

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
        # NOTE: zero-solved runs are KEPT (shown as 0/N failures, F-D3); they are
        # excluded only from ₽/solved, tiers and the Pareto frontier.
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
        # ₽ basis: provider billing (reconciled sidecar) vs tariff estimate (F-D4).
        basis = "billing" if override_for(stem) else "estimate"
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
            "cost_per_solved": round(float(cost) / passed, 4) if passed > 0 else None,
            "time_min": round(float(seconds) / 60, 3) if isinstance(seconds, (int, float)) else None,
            "steps": steps_from_json(data),
            "coverage": {
                "runs": 1,
                "full": total >= BUCKET_FULL.get(bucket, total),
                "version": _version(data),
            },
            "stem": stem,
            "tokens": tokens,
            "basis": basis,
        })
    # de-duplicate exact alias rows (same cell + numbers), keep the first stem
    seen: dict[tuple, dict] = {}
    for r in sorted(rows, key=lambda r: r["stem"]):
        key = (r["harness"], r["model"], r["reasoning"], r["suite"], r["situation_bucket"],
               r["passed"], r["total"], r["cost_rub"])
        seen.setdefault(key, r)
    rows = list(seen.values())

    # tiers by cost/solved terciles, computed PER BUCKET over comparable rows
    # only (full suite, same denominator). Denominators differ across buckets, so
    # a single global tercile is misleading (F-D1/F-D5).
    for r in rows:
        r["tier"] = None
    for bucket in BUCKET_ORDER:
        full_total = BUCKET_FULL.get(bucket)
        cell = [r for r in rows
                if r["situation_bucket"] == bucket
                and r["cost_per_solved"] is not None
                and r["total"] == full_total]
        vals = sorted(r["cost_per_solved"] for r in cell)
        n = len(vals)
        if not n:
            continue
        p33 = vals[max(0, int(n / 3) - 1)]
        p66 = vals[max(0, int(2 * n / 3) - 1)]
        for r in cell:
            r["tier"] = ("Default" if r["cost_per_solved"] <= p33
                         else "Balanced" if r["cost_per_solved"] <= p66 else "Premium")
    # global terciles kept for the machine-readable summary only
    allv = sorted(r["cost_per_solved"] for r in rows if r["cost_per_solved"] is not None)
    p33 = allv[max(0, int(len(allv) / 3) - 1)] if allv else 0.0
    p66 = allv[max(0, int(2 * len(allv) / 3) - 1)] if allv else 0.0

    # Pareto frontier per (bucket, total) subgroup — only rows measured on the
    # SAME task set are comparable. Compare pass_rate (not absolute passed), so a
    # partial 5/5 cannot be beaten by 17/20 (F-D2). Zero-solved rows stay off the
    # frontier.
    for r in rows:
        r["pareto"] = r["cost_per_solved"] is not None
        r["dominated_by"] = []
    groups: dict[tuple, list] = {}
    for r in rows:
        if r["cost_per_solved"] is None:
            continue
        groups.setdefault((r["situation_bucket"], r["total"]), []).append(r)
    for cell in groups.values():
        for a in cell:
            for b in cell:
                if a is b:
                    continue
                if (b["pass_rate"] >= a["pass_rate"] and b["cost_per_solved"] <= a["cost_per_solved"]
                        and (b["pass_rate"] > a["pass_rate"] or b["cost_per_solved"] < a["cost_per_solved"])):
                    a["pareto"] = False
                    a["dominated_by"].append(b["stem"])
    for r in rows:
        r["dominated_by"] = sorted(set(r["dominated_by"]))
        r["confidence"] = _confidence(r["coverage"]["runs"], r["coverage"]["full"])
    rows.sort(key=lambda r: (BUCKET_ORDER.index(r["situation_bucket"]),
                             not r["pareto"], r["cost_per_solved"] is None,
                             -(r["pass_rate"] or 0),
                             r["cost_per_solved"] if r["cost_per_solved"] is not None else float("inf")))
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


def _fmt_num(value: float) -> str:
    """Thousands grouping + 2 decimals: 55280.97 -> '55 280.97'."""
    return f"{value:,.2f}".replace(",", "\u2009")


def _fmt_cps(r: dict) -> str:
    cps = r.get("cost_per_solved")
    return f"{_fmt_num(cps)}{COST_UNIT}" if cps is not None else NA


def _fmt_basis(r: dict) -> str:
    return "billing" if r.get("basis") == "billing" else ("est." if r.get("basis") else DASH)


def _fmt_tier(r: dict) -> str:
    return r.get("tier") or NA


COLUMNS_MD = ("| Bucket | Harness | Model | Tier | Result | Cost/solved | Basis | Pareto | Stem |"
              "\n| --- | --- | --- | :-: | ---: | ---: | :-: | :-: | --- |")


def _row_cells(r: dict) -> list[str]:
    return [
        BUCKET_LABEL[r["situation_bucket"]],
        r["harness"] or NA,
        r["model"] or NA,
        _fmt_tier(r),
        _fmt_result(r),
        _fmt_cps(r),
        _fmt_basis(r),
        "\u2705" if r["pareto"] else DASH,
        f"`{r['stem']}`",
    ]


def _is_premium(r: dict) -> bool:
    return any(label in (r["model"] or "") for label in PREMIUM_LABELS)


def render_block(rows: list[dict], locale: str = "ru") -> str:
    ru = locale == "ru"
    out: list[str] = []
    if ru:
        out.append(f"Автосинк: {len(rows)} строк (вкл. 0/N) · тиры — пер-бакет по ₽/решённую "
                   f"задачу · ✅ — точка на фронте Парето (внутри одного набора)")
    else:
        out.append(f"Auto-sync: {len(rows)} rows (incl. 0/N) · tiers per bucket by ₽/solved · "
                   f"✅ — Pareto point (within one task set)")
    out.append("")
    out.append(COLUMNS_MD)
    for r in rows:
        out.append("| " + " | ".join(_row_cells(r)) + " |")

    # anti-patterns: premium, priced, dominated on a full (comparable) suite.
    # One row per model (the most expensive offender) keeps the list meaningful.
    anti = [r for r in rows if _is_premium(r) and not r["pareto"]
            and r["dominated_by"] and r["coverage"]["full"]
            and r["cost_per_solved"] is not None]
    if anti:
        out.append("")
        head = ("**Anti-patterns — переплата без выигрыша** (доминируемы более дешёвой "
                "конфигурацией с тем же/лучшим результатом):" if ru else
                "**Anti-patterns — paying without winning** (dominated by a cheaper config "
                "with equal/better result):")
        out.append(head)
        out.append("")
        best: dict[str, dict] = {}
        for r in sorted(anti, key=lambda r: -r["cost_per_solved"]):
            best.setdefault(r["model"], r)
        for r in list(best.values())[:12]:
            dom = r["dominated_by"][0]
            out.append(f"- `{r['model']}` @ {_fmt_cps(r)} ({r['situation_bucket']}) "
                       f"← `{dom}`")
    return "\n".join(out)


def _esc(value) -> str:
    return (str(value if value is not None else DASH)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


GUIDANCE_TEXT = {
    "ru": {
        "title": "Выбор модели/харнесса — что × чем × когда · harness_bench_fast",
        "eyebrow": "Руководство по выбору · форк",
        "h1": "Что × чем × когда: выбор модели и харнесса",
        "lead": (
            "Практическое руководство по выбору <strong>харнесса × модели</strong> для задач "
            "harness-bench-fast, построенное <strong>только на уже выполненных прогонах</strong>. "
            "Для премиум-моделей ₽ — <strong>факт биллинга провайдера</strong>, а не оценка по тарифу."
        ),
        "back": "Результаты форка",
        "matrix_note": ("Автосинк из <code>results/*.json</code> генератором "
                        "<code>scripts/build_model_selection.py</code>. Тиры — <b>пер-бакет</b> "
                        "по ₽/решённую задачу; ✅ — точка на фронте Парето внутри одного набора; "
                        "<code>0/N</code> — прогон сделан и провален."),
        "h2_tldr": "Выводы (TL;DR)",
        "h2_howto": "Как читать (по ситуациям)",
        "tldr_head": ("## TL;DR",),
        "howto_head": ("## Как читать",),
        "legend": ("Легенда: <b>Tier</b> — класс по ₽/решённую задачу (пер-бакет); "
                   "<b>billing</b> — ₽ = факт биллинга провайдера, <b>est.</b> — оценка по тарифу; "
                   "<b>✅</b> — точка на фронте Парето; <b>—</b> — не Pareto; <b>н/д</b> — нет данных."),
        "h2_matrix": "Матрица рекомендаций",
        "h2_anti": "Anti-patterns — переплата без выигрыша",
        "h2_cov": "Покрытие и уверенность",
        "h2_method": "Методология",
        "anti_note": ("Премиум-модели, доминируемые более дешёвой конфигурацией с тем же/лучшим "
                      "результатом (полные наборы). Рядом — конфигурация-доминанта."),
        "cov": [
            "Плотно сравним только <code>shot-list</code> (4 харнесса); <code>long</code> покрыт частично (6/20); "
            "<code>Light-391</code> — 3 прогона; <code>wave21 (hard-coupled)</code> не измерен (unmeasured, не fail).",
            "<code>Cost/solved</code> = ₽ / число решённых задач (включая переплату за провалы); "
            "<code>0/N</code> — прогон сделан и провален; <code>н/д</code> — нет данных.",
        ],
        "method": [
            "Источник — <code>results/*.json</code>; для премиум-лестницы ₽ из биллинга провайдера (<code>results/reconciled/</code>).",
            "Классификация — общий модуль <code>scripts/fork_metadata.py</code> (стемы совпадают с форк-таблицами).",
            "Фронт Парето — по <code>pass_rate</code> внутри одного набора (bucket × total); "
            "доминируемые → anti-patterns. Абсолютный <code>passed</code> — только для отображения x/y.",
            "Тиры — пер-бакет (терцили ₽/решённую задачу по полному набору), т.к. знаменатели разные.",
            "Правило «premium оправдан»: не доминируем и (≥ +3 п.п. pass или закрыта ≥1 floor/boundary-задача).",
            "Сравнение только внутри одной task-set версии; partial = unmeasured.",
        ],
        "fine": "Без LLM-судьи; числа пере-выведены из артефактов прогонов.",
        "footer": "harness_bench_fast · форк Alex-Kul777",
        "fork_index": "Индекс форка",
        "switch_label": "EN",
        "repos_label": "Репозитории:",
        "repo_fork": "Форк ↗",
        "repo_upstream": "Upstream ↗",
    },
    "en": {
        "title": "Model/harness selection — what × with what × when · harness_bench_fast",
        "eyebrow": "Selection guidance · fork",
        "h1": "What × with what × when: choosing a model and harness",
        "lead": (
            "Practical guidance for choosing a <strong>harness × model</strong> for "
            "harness-bench-fast tasks, built <strong>only from completed runs</strong>. "
            "For premium models the ₽ figures are <strong>actual provider billing</strong>, not tariff estimates."
        ),
        "back": "Fork results",
        "matrix_note": ("Auto-sync from <code>results/*.json</code> by "
                        "<code>scripts/build_model_selection.py</code>. Tiers <b>per bucket</b> by "
                        "₽/solved; ✅ — Pareto point within one task set; "
                        "<code>0/N</code> — run done and failed."),
        "h2_tldr": "Conclusions (TL;DR)",
        "h2_howto": "How to read (by situation)",
        "tldr_head": ("## TL;DR",),
        "howto_head": ("## How to read",),
        "legend": ("Legend: <b>Tier</b> — class by ₽/solved (per bucket); "
                   "<b>billing</b> — ₽ = provider billing, <b>est.</b> — tariff estimate; "
                   "<b>✅</b> — Pareto point; <b>—</b> — not Pareto; <b>n/a</b> — no data."),
        "h2_matrix": "Recommendation matrix",
        "h2_anti": "Anti-patterns — paying without winning",
        "h2_cov": "Coverage & confidence",
        "h2_method": "Methodology",
        "anti_note": ("Premium models dominated by a cheaper config with equal/better result "
                      "(full suites). The dominating config is shown."),
        "cov": [
            "Only <code>shot-list</code> is densely comparable (4 harnesses); <code>long</code> is partial (6/20); "
            "<code>Light-391</code> has 3 runs; <code>wave21 (hard-coupled)</code> is unmeasured (not a fail).",
            "<code>Cost/solved</code> = ₽ / solved tasks (includes spend wasted on failures); "
            "<code>0/N</code> — run done and failed; <code>n/a</code> — no data.",
        ],
        "method": [
            "Source — <code>results/*.json</code>; premium-ladder ₽ from provider billing (<code>results/reconciled/</code>).",
            "Classification — shared <code>scripts/fork_metadata.py</code> (stems match the fork tables).",
            "Pareto frontier — by <code>pass_rate</code> within one task set (bucket × total); "
            "dominated → anti-patterns. Absolute <code>passed</code> is display-only (x/y).",
            "Tiers — per bucket (terciles of ₽/solved over the full suite), because denominators differ.",
            "\"Premium justified\" rule: not dominated and (≥ +3 pp pass or closes ≥1 floor/boundary task).",
            "Comparison only within one task-set version; partial = unmeasured.",
        ],
        "fine": "No LLM judge; figures re-derived from run artifacts.",
        "footer": "harness_bench_fast · Alex-Kul777 fork",
        "fork_index": "Fork index",
        "switch_label": "RU",
        "repos_label": "Repositories:",
        "repo_fork": "Fork ↗",
        "repo_upstream": "Upstream ↗",
    },
}

def _md_inline(s: str) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def _md_to_html(fragment: str) -> str:
    """Minimal Markdown -> HTML for hand-written narrative (lists, bold, code, links)."""
    blocks: list[list] = []
    cur: list | None = None
    for raw in fragment.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            cur = None
            continue
        if stripped.startswith("- "):
            if not (cur and cur[0] == "ul"):
                cur = ["ul", []]
                blocks.append(cur)
            cur[1].append(stripped[2:].strip())
        elif line.startswith("  ") and cur and cur[0] == "ul" and cur[1]:
            cur[1][-1] = f"{cur[1][-1]} {stripped}"
        elif cur and cur[0] == "p":
            cur[1] = f"{cur[1]} {stripped}"
        else:
            cur = ["p", stripped]
            blocks.append(cur)
    parts: list[str] = []
    for kind, val in blocks:
        if kind == "ul":
            parts.append("<ul>" + "".join(f"<li>{_md_inline(i)}</li>" for i in val) + "</ul>")
        else:
            parts.append(f"<p>{_md_inline(val)}</p>")
    return "\n".join(parts)


def _md_section_html(path: Path, headings: tuple[str, ...]) -> str:
    """Render a hand-written ``##``-section of the .md as HTML (one narrative source)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    for heading in headings:
        i = text.find("\n" + heading)
        if i == -1:
            continue
        body_start = text.find("\n", i + 1) + 1
        rest = text[body_start:]
        j = rest.find("\n## ")
        return _md_to_html(rest[: j if j != -1 else len(rest)])
    return ""


def _stem_link(r: dict, locale: str) -> str:
    suffix = ".en" if locale == "en" else ""
    label = f'<code>{_esc(r["stem"])}</code>'
    target = REPO_ROOT / "docs" / f"fork-detail-{r['stem']}{suffix}.html"
    if target.exists():
        return f'<a class="stem" href="fork-detail-{_esc(r["stem"])}{suffix}.html">{label}</a>'
    return label


HTML_CSS = (
    ".msel-wrap{overflow-x:auto} .msel-wrap table{border-collapse:collapse;width:100%}"
    " .msel-wrap th,.msel-wrap td{border:1px solid #d8d8d8;padding:.35rem .5rem;"
    "text-align:left;font-size:.88rem}"
    " .msel-wrap td.num{text-align:right;white-space:nowrap}"
    " .msel-wrap code{font-size:.82rem} .msel-wrap tr.bucket th{background:#eef2eb}"
    " .tier{font-size:.8rem} .tier-default{color:#0a8a2a} .tier-premium{color:#a33}"
    " .pareto{color:#0a8a2a;font-weight:700} .anti li{margin:.15rem 0}"
    " .legend{font-size:12px;color:#687571;margin:.3rem 0 0}"
    " .tldr ul,.howto ul{margin:.3rem 0}"
    " footer{display:flex;flex-wrap:wrap;gap:.5rem 1.2rem;align-items:center}"
    " .repos a{margin-left:.6rem}"
)


def render_html(rows: list[dict], locale: str) -> str:
    x = GUIDANCE_TEXT[locale]
    doc_path = DOC_RU if locale == "ru" else DOC_EN
    other = "en" if locale == "ru" else "ru"
    switch = f"model-selection-guidance{'.en' if other == 'en' else ''}.html"
    out = [
        "<!doctype html>",
        f'<html lang="{"ru" if locale == "ru" else "en"}">',
        "<head>",
        '  <meta charset="utf-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1">',
        f"  <title>{_esc(x['title'])}</title>",
        '  <link rel="stylesheet" href="landing.css">',
        f"  <style>{HTML_CSS}</style>",
        "</head>",
        "<body>",
        "  <header>",
        '    <a class="brand" href="index.html">harness_bench_fast<span class="brand-dot">.</span></a>',
        f'    <nav aria-label="Primary"><a href="fork{".en" if locale == "en" else ""}.html">← {_esc(x["back"])}</a> '
        f'<a class="lang-switch" href="{switch}">{_esc(x["switch_label"])}</a></nav>',
        "  </header>",
        "  <main>",
        '    <section class="intro">',
        f'      <p class="eyebrow">{_esc(x["eyebrow"])}</p>',
        f'      <h1>{_esc(x["h1"])}</h1>',
        f'      <p class="lead">{x["lead"]}</p>',
        "    </section>",
        f'    <section class="tldr"><h2>{_esc(x["h2_tldr"])}</h2>',
        _md_section_html(doc_path, x["tldr_head"]),
        "    </section>",
        f'    <section class="howto"><h2>{_esc(x["h2_howto"])}</h2>',
        _md_section_html(doc_path, x["howto_head"]),
        "    </section>",
        f'    <h2>{_esc(x["h2_matrix"])}</h2>',
        f'    <p class="fine-print">{x["matrix_note"]}</p>',
        '    <div class="msel-wrap"><table><thead><tr>',
        '      <th>Harness</th><th>Model</th><th class="num">Tier</th>'
        '<th class="num">Result</th><th class="num">Cost/solved</th>'
        '<th>Basis</th><th>Pareto</th><th>Stem</th>',
        "    </tr></thead><tbody>",
    ]
    last_bucket = None
    for r in rows:
        if r["situation_bucket"] != last_bucket:
            last_bucket = r["situation_bucket"]
            out.append(f'<tr class="bucket"><th colspan="8">'
                       f'{_esc(BUCKET_LABEL[r["situation_bucket"]])}</th></tr>')
        tier = r.get("tier")
        tier_html = (f'<span class="tier tier-{tier.lower()}">{tier}</span>' if tier else NA)
        pareto = '<span class="pareto">✅</span>' if r["pareto"] else DASH
        out.append(
            "<tr>"
            f'<td>{_esc(r["harness"] or NA)}</td>'
            f'<td>{_esc(r["model"] or NA)}</td>'
            f'<td class="num">{tier_html}</td>'
            f'<td class="num">{_fmt_result(r)}</td>'
            f'<td class="num">{_fmt_cps(r)}</td>'
            f'<td>{_fmt_basis(r)}</td>'
            f'<td>{pareto}</td>'
            f'<td>{_stem_link(r, locale)}</td>'
            "</tr>"
        )
    out += ["    </tbody></table></div>"]
    out.append(f'    <p class="legend">{x["legend"]}</p>')

    anti = [r for r in rows if _is_premium(r) and not r["pareto"]
            and r["dominated_by"] and r["coverage"]["full"]
            and r["cost_per_solved"] is not None]
    out.append(f'    <h2>{_esc(x["h2_anti"])}</h2>')
    out.append(f'    <p class="fine-print">{x["anti_note"]}</p>')
    if anti:
        out.append('    <ul class="anti">')
        best: dict[str, dict] = {}
        for r in sorted(anti, key=lambda r: -r["cost_per_solved"]):
            best.setdefault(r["model"], r)
        for r in list(best.values())[:12]:
            out.append(f'      <li><code>{_esc(r["model"])}</code> @ {_fmt_cps(r)} '
                       f'({_esc(r["situation_bucket"])}) ← <code>{_esc(r["dominated_by"][0])}</code></li>')
        out.append("    </ul>")
    else:
        out.append(f"    <p>{DASH}</p>")

    out.append(f'    <h2>{_esc(x["h2_cov"])}</h2>')
    out.append("    <ul>")
    out += [f"      <li>{i}</li>" for i in x["cov"]]
    out.append("    </ul>")
    out.append(f'    <h2>{_esc(x["h2_method"])}</h2>')
    out.append("    <ol>")
    out += [f"      <li>{i}</li>" for i in x["method"]]
    out.append("    </ol>")
    out.append(f'    <p class="fine-print">{_esc(x["fine"])} '
               f'<a href="fork{".en" if locale == "en" else ""}.html">{_esc(x["back"])}</a></p>')
    out.append("  </main>")
    out.append(f'  <footer><span>{_esc(x["footer"])}</span>'
               f'<span class="repos">{_esc(x["repos_label"])}'
               f'<a href="https://github.com/Alex-Kul777/harness-bench-fast" target="_blank" rel="noopener">{_esc(x["repo_fork"])}</a>'
               f'<a href="https://github.com/ai-forever/harness-bench-fast" target="_blank" rel="noopener">{_esc(x["repo_upstream"])}</a></span>'
               f'<a href="FORK{".en" if locale == "en" else ""}.md">{_esc(x["fork_index"])}</a></footer>')
    out.append("</body>")
    out.append("</html>")
    return "\n".join(out) + "\n"


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

    for path, locale in ((HTML_RU, "ru"), (HTML_EN, "en")):
        html_text = render_html(rows, locale)
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if check:
            if current != html_text:
                stale.append(path.name)
        elif current != html_text:
            path.write_text(html_text, encoding="utf-8")
            print(f"{path.name}: updated (html)")
        else:
            print(f"{path.name}: up to date (html)")

    if check:
        if stale:
            print("STALE: " + ", ".join(stale) + " — run build_model_selection.py")
            return 1
        print(f"up to date ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
