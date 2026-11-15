#!/usr/bin/env python3
"""Generate docs/fork.html (tabbed) + docs/fork-home-results.json — the single fork page.

One page, one dataset, three tabs: Shot List (52) / Light (391) / Medium (80).
Each suite table uses exactly the canonical nine columns:

    Harness · Profile · Model · Result · % · Time · Steps · Tokens (cost) · Version

Ours vs upstream are separated by group header rows (not a column). The Medium
wave breakdown (long / reflect / feasibility) lives in a collapsible <details>
block so the main table keeps the nine columns. Tab state follows the URL hash
(#shotlist / #light / #medium), so the old redirect stubs keep working.

Usage:
    python3 scripts/build_fork_home.py            # write JSON + HTML
    python3 scripts/build_fork_home.py --check    # exit 1 if outputs are stale
"""
from __future__ import annotations

import contextlib
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fork_i18n import DEFAULT_LOCALE, LOCALES, locale_filename, other_locale, t  # noqa: E402
from fork_ladder_override import apply as _ladder_apply  # noqa: E402
from fork_metrics import steps_from_json  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
OUT_JSON = DOCS / "fork-home-results.json"
OUT_HTML = DOCS / "fork.html"
LEADERBOARD = DOCS / "leaderboard-results.json"
MEDIUM_JSON = DOCS / "fork-medium90-results.json"
MEDIUM90_JSON = DOCS / "medium90-results.json"
PILOT_MANIFEST = DOCS / "fork-medium-pilot-manifest.json"

GENERATED_BY = "scripts/build_fork_home.py"
RUB = "\u20bd"

from fork_metadata import (  # noqa: E402
    DASH,
    HARNESS_PREFIXES,
    MODEL_TOKENS,
    PROFILE_RULES,
    RESULT_DIRS,
    THINKING_LEVEL_RE,
)

# Upstream writes the reasoning level inside the Model cell, e.g.
# "Claude Haiku 5.5 (OpenRouter) (default)". We mirror that by surfacing our
# profile's reasoning token: "thinking: off" -> "(thinking: off)", "high" -> "(high)".
# Non-reasoning profiles (e.g. "MCP off") get no suffix.
REASON_LEVELS = ("high", "medium", "low", "minimal")


def _reasoning_suffix(profile):
    p = (profile or "").strip()
    if not p or p == DASH:
        return ""
    if p.startswith("thinking") or p in REASON_LEVELS:
        return f"({p})"
    return ""


# Cloud.ru Foundation Models is the provider behind every *our* run on the fork
# page; upstream rows keep whatever provider their source already carries.
OUR_PROVIDER = "cloud.ru"


def _with_reasoning(model, profile, provider=OUR_PROVIDER):
    """`model (provider) (level)` for our rows, mirroring upstream's Model cell."""
    parts = []
    if provider:
        parts.append(f"({provider})")
    suffix = _reasoning_suffix(profile)
    if suffix:
        parts.append(suffix)
    base = model if model else DASH
    return f"{base} {' '.join(parts)}".strip() if parts else base


def _harness_version(data):
    """Harness version, if the run artifact records it (upstream writes
    'pi-mono 0.73.1'; our artifacts currently do not record it)."""
    for k in ("harness_version", "openclaw_version", "cli_version", "runner_version"):
        v = (data or {}).get(k)
        if v:
            return str(v)
    return None


def _with_version(harness, version):
    """Attach a run's harness version without repeating the harness name.

    Artifacts record the version already prefixed with the harness
    ("OpenClaw 2026.10.9 (8b18b55)"), so joining blindly would duplicate it.
    """
    if not version:
        return harness
    base = (harness or "").strip()
    ver = str(version).strip()
    if base and ver.lower().startswith(base.lower()):
        return ver
    return f"{base} {ver}".strip() if base else ver



def _thinking_level(stem):
    m = THINKING_LEVEL_RE.search(stem)
    return m.group(1) if m else None
EXCLUDE = ("part2", "rerun", "smoke", "sample", "diag", "pilot")

OURS_LIGHT = [
    ("Hermes", "MCP off", "GLM-5.2", "results/hermes_full_391.json"),
    ("Pi", "high", "GLM-5.2", "results/glm52_full_391.json"),
    ("OpenClaw", "", "GLM-5.2", "results/oc_full_391.json"),
]

# (detail_filename, suite_key, row, run_data) collected while building; written on run.
_DETAIL_JOBS: list = []
WAVE_LABELS = (("long", "Long"), ("reflect", "Reflect"), ("feasibility", "Feasibility"), ("composite_hard", "Wave 21"))

# Harnesses that are OUR runs; anything else in the README shot-list table is upstream.
UPSTREAM_OURS = {"pi", "openclaw", "hermes"}
README = REPO_ROOT / "README.md"


def _collect() -> dict[str, Path]:
    seen: dict[str, Path] = {}
    for d in RESULT_DIRS:
        if d.is_dir():
            for p in sorted(d.glob("*.json")):
                seen.setdefault(p.name, p)
    return seen


def _harness(stem):
    for pre, h in HARNESS_PREFIXES:
        if stem.startswith(pre):
            return h
    return DASH


def _model(stem):
    for tok, m in MODEL_TOKENS:
        if tok in stem:
            return m
    if "thinking_off" in stem.lower():
        return "GLM-5.2"
    return DASH


def _profile(stem):
    level = _thinking_level(stem)
    if level:
        return f"thinking: {level}"
    low = stem.lower()
    for tok, lab in PROFILE_RULES:
        if tok in low:
            return lab
    return ""


def _fmt_int(n):
    return DASH if n in (None, "") else f"{int(round(n)):,}".replace(",", " ")


def _fmt_pct(rate):
    return DASH if rate is None else f"{rate * 100:.1f}%"


def _fmt_min(secs):
    return DASH if secs is None else f"{float(secs) / 60:.1f}m"


def _fmt_cost(tokens, cost):
    tok = _fmt_int(tokens)
    if cost:
        return f"{tok} ({float(cost):,.0f}".replace(",", " ") + f"{RUB})"
    return tok


_SESSION_RE = re.compile(r"(\d{8})_")
_NAME_DATE_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")


def _with_date(profile, date):
    """Append a run date to a profile label, README-style: 'thinking: off (2026-11-04)'."""
    if not date:
        return profile
    return f"{(profile or DASH)} ({date})"


def _date_from_name(name):
    m = _NAME_DATE_RE.search(name or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _date_from_sessions(data):
    dates = []
    for key in ("tasks", "results"):
        for task in (data.get(key) or []):
            if not isinstance(task, dict):
                continue
            m = _SESSION_RE.match(task.get("session_id") or "")
            if m:
                dates.append(m.group(1))
    if not dates:
        return None
    d = min(dates)
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"


def _readme_our_dates():
    """Map (harness, model) -> [(profile_text, date)] from the README shot-list table (our rows)."""
    out = {}
    if not README.exists():
        return out
    m = re.search(r"## Shot List Leaderboard \(52 tasks\)(.*?)(?=\n## )", README.read_text(encoding="utf-8"), re.S)
    if not m:
        return out
    for line in m.group(1).splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 8 or cells[0].lower() not in UPSTREAM_OURS:
            continue
        dm = re.search(r"\((\d{4}-\d{2}-\d{2})\)", cells[1])
        out.setdefault((cells[0].lower(), cells[2].lower()), []).append((cells[1], dm.group(1) if dm else None))
    return out


def _readme_date_for(map_, harness, model, profile):
    cands = map_.get(((harness or "").lower(), (model or "").lower()), [])
    if profile:
        for text, date in cands:
            if profile.lower() in text.lower() and date:
                return date
    for _, date in cands:
        if date:
            return date
    return None


def _date_from_source_files(names):
    dates = []
    for n in names or []:
        dates.append(_date_from_name(n))
        for base in RESULT_DIRS:
            p = base / n
            if p.exists():
                with contextlib.suppress(OSError, json.JSONDecodeError):
                    dates.append(_date_from_sessions(json.loads(p.read_text(encoding="utf-8"))))
                break
    dates = [d for d in dates if d]
    return min(dates) if dates else None


def _row(harness, profile, model, passed, total, rate, secs, steps, tokens, cost, version, source, waves=None, detail=None):
    return {
        "harness": harness or DASH, "profile": profile or DASH, "model": model or DASH,
        "result": f"{int(passed)}/{int(total)}" if passed is not None and total else DASH,
        "pct": _fmt_pct(rate), "time": _fmt_min(secs), "steps": _fmt_int(steps),
        "tokens_cost": _fmt_cost(tokens, cost), "version": version or DASH,
        "source": source, "waves": waves, "detail": detail,
    }


def _from_summary_file(harness, profile, model, path: Path, source="ours", date_hint=None, suite=None, detail=None):
    d = json.loads(path.read_text(encoding="utf-8"))
    s = d.get("summary") or {}
    rate = s.get("pass_rate")
    if rate is None and s.get("passed") is not None and s.get("total"):
        rate = s["passed"] / s["total"]
    date = _date_from_sessions(d) or _date_from_name(path.name) or date_hint
    # Reconciled ladder runs: replace published tokens/₽ with provider billing.
    tokens, cost = _ladder_apply(
        path.stem, s.get("total_tokens", d.get("tokens")), s.get("estimated_cost_rub")
    )
    row = _row(_with_version(harness, _harness_version(d)), _with_date(profile, date),
               _with_reasoning(model, profile),
               s.get("passed", d.get("passed")), s.get("total", d.get("total")),
               rate, s.get("total_seconds"), steps_from_json(d),
               tokens, cost,
               d.get("task_set_version"), source, detail=detail)
    if detail:
        _DETAIL_JOBS.append((detail, suite, row, d))
    return row


def ours_shotlist():
    out = []
    rmap = _readme_our_dates()
    for name, path in _collect().items():
        if "_shotlist_" not in name:
            continue
        low = name.lower()
        if any(k in low for k in EXCLUDE):
            continue
        stem = path.stem
        harness, profile, model = _harness(stem), _profile(stem), _model(stem)
        hint = _readme_date_for(rmap, harness, model, profile)
        out.append(_from_summary_file(harness, profile, model, path, date_hint=hint,
                                      suite="shotlist", detail=f"fork-detail-{path.stem}.html"))
    out.sort(key=lambda r: -_pct_val(r["pct"]))
    return out


def ours_light():
    out = []
    for harness, profile, model, rel in OURS_LIGHT:
        path = None
        for base in RESULT_DIRS:
            cand = base / rel.split("results/", 1)[-1]
            if cand.exists():
                path = cand
                break
        if path is None:
            raise SystemExit(f"missing light source: {rel}")
        out.append(_from_summary_file(harness, profile, model, path,
                                      suite="light", detail=f"fork-detail-{path.stem}.html"))
    return out


def ours_medium():
    if not MEDIUM_JSON.exists():
        return []
    d = json.loads(MEDIUM_JSON.read_text(encoding="utf-8"))
    ver = d.get("task_set_version")
    suite_total = int(d.get("medium_tasks") or 90)
    out = []
    for r in d.get("rows", []):
        suites = r.get("suites") or {}
        passed = sum(((s or {}).get("passed") or 0) for s in suites.values())
        total = sum(((s or {}).get("total") or 0) for s in suites.values())
        u = r.get("usage") or {}
        waves = {k: {"passed": (suites.get(k) or {}).get("passed"),
                     "total": (suites.get(k) or {}).get("total")} for k, _ in WAVE_LABELS}
        date = _date_from_source_files(r.get("sources"))
        row = _row(_with_version(r.get("harness"), _harness_version(r)),
                   _with_date(r.get("profile"), date),
                   _with_reasoning(r.get("model"), r.get("profile")),
                   passed, total, (passed / total) if total else None, u.get("seconds"),
                   u.get("steps"), u.get("tokens"), u.get("cost_rub"), ver, "ours", waves,
                   detail=r.get("details_url"))
        # Truncation vs the full 90-task suite must stay visible in the Result cell.
        row["coverage"] = f"{total}/{suite_total}"
        if total and total < suite_total:
            row["result"] = f"{passed}/{total} (partial {total}/{suite_total})"
        out.append(row)
    return out


def upstream_light():
    if not LEADERBOARD.exists():
        return []
    d = json.loads(LEADERBOARD.read_text(encoding="utf-8"))
    out = []
    for r in d.get("rows", []):
        lt = r.get("light")
        if not lt:
            continue
        out.append(_row(r.get("harness"), _with_date("", r.get("measurement_date")), r.get("model"),
                        lt.get("passed"), lt.get("total"),
                        (lt["passed"] / lt["total"]) if lt.get("total") else None,
                        None, None, None, None, r.get("source_version"), "upstream"))
    out.sort(key=lambda r: -_pct_val(r["pct"]))
    return out


def _pct_val(s):
    try:
        return float(str(s).rstrip("%"))
    except ValueError:
        return -1.0


def _norm_cell(s):
    """Tidy a raw markdown cell: bare dash -> em dash, strip space thousands."""
    s = (s or "").strip()
    if s in ("-", "\u2013", ""):
        return DASH
    return s.replace(",", " ")


def _raw_row(harness, profile, model, result, pct, time, steps, tokens_cost, version, source):
    return {"harness": harness or DASH, "profile": profile or DASH, "model": model or DASH,
            "result": result or DASH, "pct": pct or DASH, "time": time or DASH,
            "steps": steps or DASH, "tokens_cost": tokens_cost or DASH,
            "version": version or DASH, "source": source, "waves": None, "detail": None}


def upstream_shotlist():
    """Upstream rows of the fork README shot-list table (our harnesses excluded)."""
    if not README.exists():
        return []
    import re
    txt = README.read_text(encoding="utf-8")
    m = re.search(r"## Shot List Leaderboard \(52 tasks\)(.*?)(?=\n## )", txt, re.S)
    if not m:
        return []
    out = []
    for line in m.group(1).splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 8 or cells[0].lower() == "harness" or set(cells[0]) <= set("-: "):
            continue
        if cells[0].lower() in UPSTREAM_OURS:
            continue
        out.append(_raw_row(cells[0], cells[1], cells[2], cells[3], cells[4],
                            _norm_cell(cells[5]), _norm_cell(cells[6]), _norm_cell(cells[7]),
                            DASH, "upstream"))
    return out


def upstream_medium():
    """Upstream Medium (90) rows from the published 90-task board.

    Only complete (measured_tasks == 90) rows are taken, so the full-suite table
    never mixes in partial 80/40-task measurements.
    """
    if not MEDIUM90_JSON.exists():
        return []
    d = json.loads(MEDIUM90_JSON.read_text(encoding="utf-8"))
    out = []
    for r in d.get("rows", []):
        if (r.get("measured_tasks") or 0) != 90:
            continue
        passed = r.get("observed_passed")
        if passed is None:
            continue
        out.append(_row(r.get("harness"), _with_date("", None), r.get("model"),
                        int(passed), 90, (int(passed) / 90),
                        None, None, None, None, r.get("source_version"), "upstream"))
    out.sort(key=lambda x: -_pct_val(x["pct"]))
    return out


def _pilot_usage(man):
    """Aggregate pilot Time/Tokens/Steps from the manifest or its source runs.

    An explicit ``usage`` block on the manifest wins; otherwise the ``summary``
    fields of every resolvable source artifact are summed (missing files and
    unreadable JSON contribute nothing). Returns None when nothing is found.
    """
    explicit = man.get("usage")
    if isinstance(explicit, dict):
        return explicit
    names = list(man.get("sources") or [])
    for item in man.get("results") or []:
        if isinstance(item, dict) and item.get("source"):
            names.append(item["source"])
    names = list(dict.fromkeys(names))  # a source may appear in both lists
    totals = {"seconds": 0.0, "steps": 0, "tokens": 0, "cost_rub": 0.0}
    found = False
    for name in names:
        for base in RESULT_DIRS:
            path = base / name
            if not path.exists():
                continue
            with contextlib.suppress(OSError, json.JSONDecodeError):
                data = json.loads(path.read_text(encoding="utf-8"))
                summary = data.get("summary") or {}
                totals["seconds"] += float(summary.get("total_seconds") or 0.0)
                totals["tokens"] += int(summary.get("total_tokens") or 0)
                totals["cost_rub"] += float(summary.get("estimated_cost_rub") or 0.0)
                totals["steps"] += int(steps_from_json(data) or 0)
                found = True
            break
    return totals if found else None


def _pilot_date(man):
    """Run date for the pilot row: source filenames first, then manifest `created`."""
    date = _date_from_source_files(man.get("sources"))
    if date:
        return date
    created = man.get("created")
    if isinstance(created, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", created):
        return created
    return None


def sample_rows():
    """Our truncated Medium subsets + pilot mini-sets (coverage vs the full 90).

    Rows pass through the same canonical metadata transforms as #medium:
    date in Profile, provider + reasoning level in Model, harness version in
    Harness, and a Steps column (from usage.steps / the source summary).
    """
    out = []
    if MEDIUM_JSON.exists():
        d = json.loads(MEDIUM_JSON.read_text(encoding="utf-8"))
        for r in d.get("rows", []):
            suites = r.get("suites") or {}
            passed = sum(((s or {}).get("passed") or 0) for s in suites.values())
            total = sum(((s or {}).get("total") or 0) for s in suites.values())
            if not total:
                continue
            u = r.get("usage") or {}
            out.append({
                "kind": "coverage",
                "harness": _with_version(r.get("harness"), r.get("harness_version")),
                "profile": _with_date(r.get("profile"), _date_from_source_files(r.get("sources"))),
                "model": _with_reasoning(r.get("model"), r.get("profile")),
                "subset_key": "subset_our_runs",
                "subset_args": {"n": total},
                "result": f"{passed}/{total}",
                "pct": f"{100 * passed / total:.1f}%",
                "coverage": f"{total}/90",
                "time": _fmt_min(u.get("seconds")),
                "steps": _fmt_int(u.get("steps")),
                "tokens_cost": _fmt_cost(u.get("tokens"), u.get("cost_rub")),
                "status_key": "status_partial" if total < 90 else "status_complete",
                "detail": r.get("details_url"),
            })
    if PILOT_MANIFEST.exists():
        man = json.loads(PILOT_MANIFEST.read_text(encoding="utf-8"))
        cfg = man.get("config") or {}
        res = man.get("results") or []
        passed = sum((x.get("passed") or 0) for x in res)
        total = sum((x.get("total") or 0) for x in res)
        # Guard an empty pilot manifest: never emit a bogus "0/0" row.
        if total > 0:
            u = _pilot_usage(man) or {}
            out.append({
                "kind": "pilot",
                "harness": _with_version(cfg.get("harness"), _harness_version(man)),
                "profile": _with_date(cfg.get("profile"), _pilot_date(man)),
                "model": _with_reasoning(cfg.get("model"), cfg.get("profile")),
                "subset_key": "subset_pilot",
                "subset_args": {"n": man.get("size")},
                "result": f"{passed}/{total}",
                "pct": f"{100 * passed / total:.1f}%",
                "coverage": f"{total}/90",
                "time": _fmt_min(u.get("seconds")),
                "steps": _fmt_int(u.get("steps")),
                "tokens_cost": _fmt_cost(u.get("tokens"), u.get("cost_rub")),
                "status_key": "status_pilot",
                "detail": None,
            })
    return out


# ---- upstream restricted to OUR covered Medium subset -------------------------
_TASK_NUM_RE = re.compile(r"task_(\d+)")


def _task_numbers(data):
    nums = set()
    for task in (data.get("results") or data.get("tasks") or []):
        if not isinstance(task, dict):
            continue
        n = task.get("number")
        if n is None:
            m = _TASK_NUM_RE.search(str(task.get("task_id") or ""))
            n = int(m.group(1)) if m else None
        if n is not None:
            nums.add(int(n))
    return nums


def _covered_task_numbers():
    """Union of task numbers across our Medium aggregate row's source runs."""
    if not MEDIUM_JSON.exists():
        return set()
    d = json.loads(MEDIUM_JSON.read_text(encoding="utf-8"))
    nums = set()
    for r in d.get("rows", []):
        for name in (r.get("sources") or []):
            for base in RESULT_DIRS:
                p = base / name
                if p.exists():
                    with contextlib.suppress(OSError, json.JSONDecodeError):
                        nums |= _task_numbers(json.loads(p.read_text(encoding="utf-8")))
                    break
    return nums


def _outcome_map(path):
    if not path.exists():
        return None
    d = json.loads(path.read_text(encoding="utf-8"))
    m = {}
    for o in d.get("outcomes") or []:
        n = o.get("number")
        if n is None:
            mm = _TASK_NUM_RE.search(str(o.get("task_id") or ""))
            n = int(mm.group(1)) if mm else None
        if n is not None:
            m[int(n)] = bool(o.get("passed"))
    return m or None


def _haiku_maps():
    out = {}
    p = DOCS / "haiku-medium90.json"
    if not p.exists():
        return out
    d = json.loads(p.read_text(encoding="utf-8"))
    for r in d.get("rows") or []:
        m = {}
        for task in r.get("tasks") or []:
            n = task.get("number")
            if n is None:
                mm = _TASK_NUM_RE.search(str(task.get("task_id") or ""))
                n = int(mm.group(1)) if mm else None
            if n is not None:
                m[int(n)] = bool(task.get("passed"))
        out[r.get("harness")] = m or None
    return out


def upstream_subset_rows():
    """Upstream Medium-board configs scored on exactly the tasks WE covered.

    Per-task outcomes are published only for some configs (codex/miniswe/
    claudecode outcome files + the two haiku rows); the Qwen/GPT-OSS small
    models expose aggregates only, so they are listed with a dash.
    """
    cov = _covered_task_numbers()
    if not cov:
        return []
    haiku = _haiku_maps()
    configs = (
        ("Codex CLI 0.164.0", "DeepSeek V4.1 Flash 1017 UNCENSORED (high)", _outcome_map(DOCS / "codex-deepseek-medium90-20261027.json")),
        ("mini-SWE-agent 2.4.6", "DeepSeek V4.1 Flash 1017 UNCENSORED (high)", _outcome_map(DOCS / "miniswe-deepseek-medium90-20261027.json")),
        ("mini-SWE-agent 2.4.6", "Claude Haiku 5.5 (default)", haiku.get("mini-SWE-agent 2.4.6")),
        ("DeepAgents 0.6.12", "Claude Haiku 5.5 (default)", haiku.get("DeepAgents 0.6.12")),
        ("Claude Code 2.1.300", "Claude Sonnet 5.5 (high)", _outcome_map(DOCS / "claudecode-sonnet-medium90-20261027.json")),
        ("DeepAgents 0.6.12", "Qwen 3.8 27B (default)", None),
        ("DeepAgents 0.6.12", "Qwen 3.6 35B A3B (default)", None),
        ("DeepAgents 0.6.12", "GPT-OSS 20B (default)", None),
    )
    rows = []
    for harness, model, m in configs:
        n = len(cov & set(m)) if m else 0
        if n:
            passed = sum(1 for k in cov if m.get(k))
            rows.append({
                "kind": "upstream", "harness": harness, "profile": DASH, "model": model,
                "subset_key": "subset_same_tasks", "subset_args": {"n": n},
                "result": f"{passed}/{n}", "pct": f"{100 * passed / n:.1f}%",
                "coverage": f"{n}/90", "time": DASH, "tokens_cost": DASH,
                "status_key": "status_upstream_subset",
            })
        else:
            rows.append({
                "kind": "upstream", "harness": harness, "profile": DASH, "model": model,
                "subset_key": "subset_our_tasks", "subset_args": {"n": len(cov)},
                "result": DASH, "pct": DASH, "coverage": DASH,
                "time": DASH, "tokens_cost": DASH,
                "status_key": "status_upstream_notask",
            })
    rows.sort(key=lambda r: -_pct_val(r["pct"]))
    return rows


def build():
    return {
        "generated_by": GENERATED_BY,
        "id": "fork-home",
        "version_semantics": "ours: task-set version · upstream: source_version",
        "suites": [
            {"key": "shotlist", "label": "Shot List (52)", "tasks": 52, "ours": ours_shotlist(), "upstream": upstream_shotlist()},
            {"key": "light", "label": "Light (391)", "tasks": 391, "ours": ours_light(), "upstream": upstream_light()},
            {"key": "medium", "label": "Medium (90)", "tasks": 90, "ours": ours_medium(), "upstream": upstream_medium()},
            {"key": "medium-sample", "label": "Medium sample", "tasks": 0, "ours": sample_rows(), "upstream": upstream_subset_rows()},
        ],
    }


def _rows_html(rows):
    tr = []
    for i, r in enumerate(rows, 1):
        d = r.get("detail")
        attrs = f' class="detail-row" data-detail="{html.escape(d)}"' if d else ""
        harness = (f'<a href="{html.escape(d)}">{html.escape(r["harness"])}</a>'
                   if d else html.escape(r['harness']))
        tr.append(
            f"<tr{attrs}>"
            f'<td class="numeric place">{i}</td>'
            f"<td>{harness}</td>"
            f"<td>{html.escape(r['profile'])}</td>"
            f"<td>{html.escape(r['model'])}</td>"
            f'<td class="numeric">{html.escape(r["result"])}</td>'
            f'<td class="numeric">{html.escape(r["pct"])}</td>'
            f'<td class="numeric">{html.escape(r["time"])}</td>'
            f'<td class="numeric">{html.escape(r["steps"])}</td>'
            f'<td class="numeric">{html.escape(r["tokens_cost"])}</td>'
            f"<td>{html.escape(r['version'])}</td>"
            "</tr>"
        )
    return "\n".join(tr)


HEAD = """<tr>
  <th scope="col" class="numeric place">#</th>
  <th scope="col" class="sortable" data-sort="text">Harness</th><th scope="col" class="sortable" data-sort="text">Profile</th><th scope="col" class="sortable" data-sort="text">Model</th>
  <th scope="col" class="numeric sortable" data-sort="ratio">Result</th><th scope="col" class="numeric sortable" data-sort="number">%</th>
  <th scope="col" class="numeric sortable" data-sort="number">Time</th><th scope="col" class="numeric sortable" data-sort="number">Steps</th>
  <th scope="col" class="numeric sortable" data-sort="number">Tokens (cost)</th><th scope="col" class="sortable" data-sort="text">Version</th>
</tr>"""


def _table(rows, group_ours):
    parts = ['<table class="leaderboard-table"><thead>' + HEAD + '</thead><tbody>',
             f'<tr class="group"><td colspan="10"><strong>{html.escape(group_ours)}</strong></td></tr>',
             _rows_html(rows)]
    parts.append('</tbody></table>')
    return "\n".join(parts)


def _table_groups(ours, upstream, g_ours, g_up):
    parts = ['<table class="leaderboard-table"><thead>' + HEAD + '</thead><tbody>',
             f'<tr class="group"><td colspan="10"><strong>{html.escape(g_ours)}</strong></td></tr>',
             _rows_html(ours)]
    if upstream:
        parts.append(f'<tr class="group"><td colspan="10"><strong>{html.escape(g_up)}</strong></td></tr>')
        parts.append(_rows_html(upstream))
    parts.append('</tbody></table>')
    return "\n".join(parts)


def _waves_html(rows, locale=DEFAULT_LOCALE):
    body = []
    for r in rows:
        w = r.get("waves") or {}
        cells = []
        for k, _ in WAVE_LABELS:
            c = w.get(k) or {}
            p, tot = c.get("passed"), c.get("total")
            cells.append(f'<td class="numeric">{f"{p}/{tot}" if p is not None and tot else DASH}</td>')
        body.append(f"<tr><td>{html.escape(r['harness'])} · {html.escape(r['model'])}</td>" + "".join(cells) + "</tr>")
    head = "".join(f'<th scope="col" class="numeric">{lab}</th>' for _, lab in WAVE_LABELS)
    return (
        f'<details class="wave-breakdown"><summary>{html.escape(t(locale, "waves_summary"))}</summary>'
        '<div class="table-wrap"><table class="leaderboard-table"><thead>'
        f'<tr><th scope="col">{html.escape(t(locale, "wave_col_config"))}</th>{head}</tr></thead><tbody>'
        + "\n".join(body) + "</tbody></table></div></details>"
    )


SAMPLE_HEAD = """<tr>
  <th scope="col" class="numeric place">#</th>
  <th scope="col" class="sortable" data-sort="text">Harness</th><th scope="col" class="sortable" data-sort="text">Profile</th><th scope="col" class="sortable" data-sort="text">Model</th>
  <th scope="col" class="sortable" data-sort="text">Subset</th><th scope="col" class="numeric sortable" data-sort="ratio">Result</th>
  <th scope="col" class="numeric sortable" data-sort="number">%</th><th scope="col" class="numeric sortable" data-sort="ratio">Coverage</th>
  <th scope="col" class="numeric sortable" data-sort="number">Time</th><th scope="col" class="numeric sortable" data-sort="number">Steps</th>
  <th scope="col" class="numeric sortable" data-sort="number">Tokens (cost)</th>
  <th scope="col" class="sortable" data-sort="text">Status</th>
</tr>"""


def _sample_rows_html(rows, locale=DEFAULT_LOCALE):
    tr = []
    for i, r in enumerate(rows, 1):
        d = r.get("detail")
        attrs = f' class="detail-row" data-detail="{html.escape(d)}"' if d else ""
        harness = (f'<a href="{html.escape(d)}">{html.escape(r["harness"])}</a>'
                   if d else html.escape(r["harness"]))
        subset = t(locale, r["subset_key"], **(r.get("subset_args") or {}))
        status = t(locale, r["status_key"])
        tr.append(
            f"<tr{attrs}>"
            f'<td class="numeric place">{i}</td>'
            f"<td>{harness}</td>"
            f"<td>{html.escape(r['profile'])}</td>"
            f"<td>{html.escape(r['model'])}</td>"
            f"<td>{html.escape(subset)}</td>"
            f'<td class="numeric">{html.escape(r["result"])}</td>'
            f'<td class="numeric">{html.escape(r["pct"])}</td>'
            f'<td class="numeric">{html.escape(r["coverage"])}</td>'
            f'<td class="numeric">{html.escape(r["time"])}</td>'
            f'<td class="numeric">{html.escape(r.get("steps") or DASH)}</td>'
            f'<td class="numeric">{html.escape(r["tokens_cost"])}</td>'
            f"<td>{html.escape(status)}</td>"
            "</tr>"
        )
    return "\n".join(tr)


def _sample_table(rows, upstream=None, locale=DEFAULT_LOCALE):
    parts = ['<table class="leaderboard-table"><thead>' + SAMPLE_HEAD + '</thead><tbody>',
             f'<tr class="group"><td colspan="12"><strong>{html.escape(t(locale, "group_sample_ours"))}</strong></td></tr>',
             _sample_rows_html(rows, locale)]
    if upstream:
        parts.append(f'<tr class="group"><td colspan="12"><strong>{html.escape(t(locale, "group_sample_upstream"))}</strong></td></tr>')
        parts.append(_sample_rows_html(upstream, locale))
    parts.append("</tbody></table>")
    return "\n".join(parts)


def _panel(suite, locale=DEFAULT_LOCALE):
    key = suite["key"]
    if key == "medium-sample":
        note = f'<p class="coverage-note">{t(locale, "note_sample")} '
        note += ('<a href="fork-medium-pilot-manifest.json">fork-medium-pilot-manifest.json</a> · '
                 f'<a href="{locale_filename("fork", locale)}.html#medium">{t(locale, "link_full_90")}</a></p>')
        return (
            f'      <section data-suite-panel="{key}" aria-label="{html.escape(suite["label"])}">\n'
            f'        {note}\n'
            f'        <div class="table-wrap">{_sample_table(suite["ours"], suite.get("upstream"), locale)}</div>\n'
            "      </section>"
        )
    inner = (_table_groups(suite["ours"], suite["upstream"], t(locale, "group_our_runs"), t(locale, "group_upstream"))
             if suite["upstream"] else _table(suite["ours"], t(locale, "group_our_runs")))
    extra = _waves_html(suite["ours"], locale) if key == "medium" and suite["ours"] else ""
    return (
        f'      <section data-suite-panel="{key}" aria-label="{html.escape(suite["label"])}">\n'
        f'        <div class="table-wrap">{inner}</div>\n'
        + (extra + "\n" if extra else "")
        + "      </section>"
    )


STYLE = """<style>
  .suite-switch button[aria-pressed="true"]{font-weight:700;text-decoration:underline}
  tr.group td{background:rgba(127,127,127,.12);font-size:.92em}
  [data-suite-panel][hidden]{display:none}
  .wave-breakdown{margin-top:.6rem}
  tr[data-detail]{cursor:pointer}
  tr[data-detail]:hover td{background:rgba(127,127,127,.08)}
  .filters{display:flex;flex-wrap:wrap;gap:.8rem;align-items:center;margin:.9rem 0}
  .filters .search{display:flex;gap:.5rem;align-items:center}
  #row-count{font-size:.9em;opacity:.8}
  th.sortable{cursor:pointer;user-select:none;white-space:nowrap}
  th.sortable:hover{text-decoration:underline}
  th.sortable::after{content:"↕";opacity:.3;font-size:.85em;margin-left:.3em}
  th.sortable.asc::after{content:"↑";opacity:.9}
  th.sortable.desc::after{content:"↓";opacity:.9}
  td.place{opacity:.6;font-variant-numeric:tabular-nums}
</style>"""

SCRIPT = r"""<script>
(function(){
  var btns=[].slice.call(document.querySelectorAll('.suite-switch button'));
  var panels=[].slice.call(document.querySelectorAll('[data-suite-panel]'));
  var search=document.getElementById('search');
  var count=document.getElementById('row-count');
  var empty=document.getElementById('empty');
  function activePanel(){return document.querySelector('[data-suite-panel]:not([hidden])');}
  function applyFilter(){
    var panel=activePanel(); if(!panel) return;
    var tbody=panel.querySelector('.table-wrap table.leaderboard-table tbody');
    if(!tbody) return;
    var rows=[].slice.call(tbody.querySelectorAll('tr'));
    var q=(search.value||'').trim().toLocaleLowerCase();
    var shown=0, curGroup=null, groupHas=new Map();
    rows.forEach(function(r){
      if(r.classList.contains('group')){curGroup=r; groupHas.set(r,false); return;}
      var match=!q || r.textContent.toLocaleLowerCase().indexOf(q)>=0;
      r.hidden=!match;
      if(match){shown++; if(curGroup) groupHas.set(curGroup,true);}
    });
    rows.forEach(function(r){ if(r.classList.contains('group')) r.hidden=!groupHas.get(r); });
    count.textContent = q ? (shown+' match'+(shown===1?'':'es')) : (shown+' rows');
    empty.hidden = !(q && shown===0);
  }
  function activate(key){
    btns.forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.suite===key));});
    panels.forEach(function(p){p.hidden=(p.dataset.suitePanel!==key);});
    applyFilter();
  }
  function keyFromHash(){var h=(location.hash||'').replace('#','');
    var keys=btns.map(function(b){return b.dataset.suite;});
    return keys.indexOf(h)>=0?h:keys[0];}
  btns.forEach(function(b){b.addEventListener('click',function(){activate(b.dataset.suite);history.replaceState(null,'','#'+b.dataset.suite);});});
  window.addEventListener('hashchange',function(){activate(keyFromHash());});
  search.addEventListener('input',applyFilter);
  document.querySelectorAll('tr[data-detail]').forEach(function(tr){
    tr.addEventListener('click',function(e){
      if(e.target.closest('a')) return;
      location.href=tr.dataset.detail;
    });
  });
  // --- place numbering + column sorting ---
  function numDataRows(table){
    var tbody=table.querySelector('tbody'); if(!tbody) return;
    var n=0;
    [].slice.call(tbody.children).forEach(function(r){
      if(r.tagName!=='TR'||r.classList.contains('group')) return;
      n++; var c=r.querySelector('td.place'); if(c) c.textContent=n;
    });
  }
  function parseVal(td, type){
    if(!td) return null;
    var s=(td.textContent||'').trim();
    if(!s||s==='—'||s==='-'||s==='–') return null;
    if(type==='text') return s.toLocaleLowerCase();
    if(type==='ratio'){
      var parts=s.split('/');
      if(parts.length===2){
        var a=parseFloat(parts[0].replace(/[^0-9.,-]/g,'').replace(',','.'));
        var b=parseFloat(parts[1].replace(/[^0-9.,-]/g,'').replace(',','.'));
        if(!isNaN(a)&&!isNaN(b)&&b) return a/b;
        if(!isNaN(a)) return a;
        return null;
      }
    }
    var m=s.match(/[0-9][0-9 .,]*/);
    if(!m) return null;
    var t=m[0].replace(/ /g,'').replace(',','.');
    return parseFloat(t);
  }
  function sortTable(table, idx, type, dir){
    var tbody=table.querySelector('tbody'); if(!tbody) return;
    var out=[], seg=[];
    function flush(){
      if(!seg.length) return;
      seg.sort(function(a,b){
        var va=parseVal(a.children[idx],type), vb=parseVal(b.children[idx],type);
        if(va===null&&vb===null) return 0;
        if(va===null) return 1;
        if(vb===null) return -1;
        if(va<vb) return dir==='asc'?-1:1;
        if(va>vb) return dir==='asc'?1:-1;
        return 0;
      });
      out=out.concat(seg); seg=[];
    }
    [].slice.call(tbody.children).forEach(function(r){
      if(r.tagName!=='TR') return;
      if(r.classList.contains('group')){ flush(); out.push(r); }
      else seg.push(r);
    });
    flush();
    out.forEach(function(r){ tbody.appendChild(r); });
    numDataRows(table);
  }
  [].slice.call(document.querySelectorAll('table.leaderboard-table')).forEach(function(table){
    numDataRows(table);
    var ths=[].slice.call(table.querySelectorAll('thead th'));
    ths.forEach(function(th, idx){
      var type=th.getAttribute('data-sort'); if(!type) return;
      th.addEventListener('click',function(){
        var dir=th.getAttribute('data-dir')==='asc'?'desc':'asc';
        ths.forEach(function(o){o.removeAttribute('data-dir');o.classList.remove('asc','desc');});
        th.setAttribute('data-dir',dir);
        th.classList.add(dir);
        sortTable(table, idx, type, dir);
      });
    });
  });
  activate(keyFromHash());
})();
</script>"""

PAGE_HEAD = """<!doctype html>
<html lang="__LANG__">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="__DESC__">
  <title>__TITLE__</title>
  <link rel="stylesheet" href="landing.css">
__STYLE__
</head>
<body>
  <header>
    <a class="brand" href="index.html">harness_bench_fast<span class="brand-dot">.</span></a>
    <nav aria-label="Primary"><a href="__FORKMD__">__FORK_INDEX__</a> <a href="__SAMPLE__">__CAP_SAMPLE__</a> <a class="lang-switch" href="__SWITCH__" title="__SWITCH_TITLE__">__SWITCH_LABEL__</a></nav>
  </header>
  <main>
    <section class="intro">
      <p class="eyebrow">__EYEBROW__</p>
      <h1>__H1__</h1>
      <p class="lead">__LEAD__</p>
    </section>
    <div class="suite-switch" role="group" aria-label="Benchmark suite">
      <button type="button" data-suite="shotlist" aria-pressed="true"><strong>Shot List (52)</strong><span>__TAB_SHOTLIST_SUB__</span></button>
      <button type="button" data-suite="light" aria-pressed="false"><strong>Light (391)</strong><span>__TAB_LIGHT_SUB__</span></button>
      <button type="button" data-suite="medium" aria-pressed="false"><strong>Medium (90)</strong><span>__TAB_MEDIUM_SUB__</span></button>
      <button type="button" data-suite="medium-sample" aria-pressed="false"><strong>Medium sample</strong><span>__TAB_MEDIUM_SAMPLE_SUB__</span></button>
    </div>
    <div class="filters">
      <label class="search">__SEARCH_LABEL__<input id="search" type="search" placeholder="__SEARCH_PH__" autocomplete="off"></label>
      <span id="row-count" aria-live="polite"></span>
    </div>
__PANELS__
    <p id="empty" role="status" hidden>__EMPTY__</p>
    <p class="fine-print">__FINE__ <a href="__FORKMD__">__FORK_INDEX__</a> · <a href="__TABLES__">__FORK_TABLES__</a></p>
  </main>
  <footer><span>__FOOTER_NOTE__</span><a href="__FORKMD__">__FOOTER_LINK__</a></footer>
__SCRIPT__
<script>document.querySelectorAll('a.lang-switch').forEach(function(a){if(location.hash)a.href+=location.hash;});</script>
</body>
</html>
"""


def render_html(data, locale=DEFAULT_LOCALE):
    other = other_locale(locale)
    fork_md = "../FORK.en.md" if locale == "en" else "../FORK.md"
    return (PAGE_HEAD
            .replace("__LANG__", t(locale, "html_lang"))
            .replace("__DESC__", html.escape(t(locale, "home_desc")))
            .replace("__TITLE__", html.escape(t(locale, "home_title")))
            .replace("__STYLE__", STYLE)
            .replace("__FORKMD__", fork_md)
            .replace("__FORK_INDEX__", html.escape(t(locale, "fork_index")))
            .replace("__SAMPLE__", locale_filename("fork-sample", locale) + ".html")
            .replace("__CAP_SAMPLE__", html.escape(t(locale, "capability_sample")))
            .replace("__SWITCH__", locale_filename("fork", other) + ".html")
            .replace("__SWITCH_TITLE__", html.escape(t(locale, "lang_switch_title")))
            .replace("__SWITCH_LABEL__", html.escape(t(locale, "lang_switch")))
            .replace("__EYEBROW__", html.escape(t(locale, "home_eyebrow")))
            .replace("__H1__", t(locale, "home_h1"))
            .replace("__LEAD__", html.escape(t(locale, "home_lead")))
            .replace("__TAB_SHOTLIST_SUB__", html.escape(t(locale, "tab_shotlist_sub")))
            .replace("__TAB_LIGHT_SUB__", html.escape(t(locale, "tab_light_sub")))
            .replace("__TAB_MEDIUM_SUB__", html.escape(t(locale, "tab_medium_sub")))
            .replace("__TAB_MEDIUM_SAMPLE_SUB__", html.escape(t(locale, "tab_medium_sample_sub")))
            .replace("__SEARCH_LABEL__", html.escape(t(locale, "search_label")))
            .replace("__SEARCH_PH__", html.escape(t(locale, "search_placeholder")))
            .replace("__PANELS__", "\n".join(_panel(s, locale) for s in data["suites"]))
            .replace("__EMPTY__", html.escape(t(locale, "empty_search")))
            .replace("__FINE__", t(locale, "home_fine_print"))
            .replace("__TABLES__", locale_filename("fork-results", locale) + ".md")
            .replace("__FORK_TABLES__", html.escape(t(locale, "link_fork_tables")))
            .replace("__FOOTER_NOTE__", html.escape(t(locale, "footer_note")))
            .replace("__FOOTER_LINK__", html.escape(t(locale, "footer_link")))
            .replace("__SCRIPT__", SCRIPT))


DETAIL_HEAD = """<!doctype html>
<html lang="__LANG__">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <link rel="stylesheet" href="landing.css">
  <style>.msg pre{white-space:pre-wrap;word-break:break-word;max-height:280px;overflow:auto;margin:.3rem 0 0}</style>
</head>
<body>
  <header>
    <a class="brand" href="index.html">harness_bench_fast<span class="brand-dot">.</span></a>
    <nav aria-label="Primary"><a href="__BACK_FORK__">__BACK_LABEL__</a> <a class="lang-switch" href="__SWITCH__" title="__SWITCH_TITLE__">__SWITCH_LABEL__</a></nav>
  </header>
  <main>
    <section class="intro">
      <p class="eyebrow">__EYEBROW__</p>
      <h1>__H1__</h1>
      <p class="lead">__META__</p>
    </section>
    <h2>__H2__</h2>
    <div class="table-wrap">
      <table class="leaderboard-table"><thead><tr>
        <th scope="col" class="numeric">#</th><th scope="col">__TH_TASK__</th><th scope="col">__TH_RESULT__</th>
        <th scope="col" class="numeric">__TH_SECONDS__</th><th scope="col" class="numeric">__TH_TOKENS__</th><th scope="col">__TH_MESSAGE__</th>
      </tr></thead><tbody>
__ROWS__
      </tbody></table>
    </div>
    <p class="fine-print">__FINE__ <code>__SRC__</code>. <a href="__BACK_FORK__">__BACK_LABEL__</a></p>
  </main>
</body>
</html>
"""


def _task_message(text):
    """Render a task message/error for the detail pages (escaped)."""
    if not text:
        return DASH
    return html.escape(str(text))


def render_detail(suite, row, data, base, locale=DEFAULT_LOCALE):
    tasks = [x for x in (data.get("tasks") or data.get("results") or []) if isinstance(x, dict)]
    body = []
    for i, task in enumerate(tasks, 1):
        p = task.get("passed")
        res = "PASS" if p in (True, 1) else ("FAIL" if p in (False, 0) else DASH)
        secs = task.get("elapsed_seconds")
        toks = task.get("agent_total_tokens", task.get("tokens"))
        body.append(
            "<tr>"
            f'<td class="numeric">{i}</td>'
            f"<td>{html.escape(str(task.get('task_id') or task.get('id') or DASH))}</td>"
            f"<td>{res}</td>"
            f'<td class="numeric">{DASH if secs is None else f"{float(secs):.1f}"}</td>'
            f'<td class="numeric">{_fmt_int(toks)}</td>'
            f"<td>{_task_message(task.get('message') or task.get('error'))}</td>"
            "</tr>"
        )
    meta = " \u00b7 ".join([
        f"{t(locale, 'detail_meta_profile')}: {html.escape(row['profile'])}",
        f"{t(locale, 'detail_meta_version')}: {html.escape(row['version'])}",
        f"{t(locale, 'detail_meta_result')}: {html.escape(row['result'])} ({html.escape(row['pct'])})",
        f"{t(locale, 'detail_meta_time')}: {html.escape(row['time'])}",
        f"{t(locale, 'detail_meta_steps')}: {html.escape(row['steps'])}",
        f"{t(locale, 'detail_meta_tokens_cost')}: {html.escape(row['tokens_cost'])}",
    ])
    src = row.get("source_file") or ""
    back = f"{locale_filename('fork', locale)}.html#{suite or ''}"
    switch = locale_filename(base, other_locale(locale)) + ".html"
    return (DETAIL_HEAD
            .replace("__LANG__", t(locale, "html_lang"))
            .replace("__TITLE__", html.escape(f"{row['harness']} \u00b7 {row['model']} \u00b7 {t(locale, 'detail_title_suffix')}"))
            .replace("__EYEBROW__", html.escape(t(locale, "detail_eyebrow")))
            .replace("__H1__", html.escape(f"{row['harness']} \u00b7 {row['model']}"))
            .replace("__META__", meta)
            .replace("__H2__", html.escape(t(locale, "detail_h2", n=len(tasks))))
            .replace("__TH_TASK__", html.escape(t(locale, "detail_th_task")))
            .replace("__TH_RESULT__", html.escape(t(locale, "detail_th_result")))
            .replace("__TH_SECONDS__", html.escape(t(locale, "detail_th_seconds")))
            .replace("__TH_TOKENS__", html.escape(t(locale, "detail_th_tokens")))
            .replace("__TH_MESSAGE__", html.escape(t(locale, "detail_th_message")))
            .replace("__ROWS__", "\n".join(body) or f'<tr><td colspan="6">{DASH}</td></tr>')
            .replace("__FINE__", t(locale, "detail_fine_print"))
            .replace("__SRC__", html.escape(src))
            .replace("__BACK_FORK__", back)
            .replace("__BACK_LABEL__", html.escape(t(locale, "detail_back")))
            .replace("__SWITCH__", switch)
            .replace("__SWITCH_TITLE__", html.escape(t(locale, "lang_switch_title")))
            .replace("__SWITCH_LABEL__", html.escape(t(locale, "lang_switch"))))


def main(argv):
    data = build()
    blob = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    pages = {loc: render_html(data, loc) for loc in LOCALES}
    details: dict[str, str] = {}
    for loc in LOCALES:
        for detail, suite, row, _d in _DETAIL_JOBS:
            base = detail[:-5] if detail.endswith(".html") else detail
            name = locale_filename(base, loc) + ".html"
            details[name] = render_detail(suite, row, data, base, loc)
    if "--check" in argv:
        stale = [OUT_JSON.name] if not OUT_JSON.exists() or OUT_JSON.read_text(encoding="utf-8") != blob else []
        for loc in LOCALES:
            p = DOCS / (locale_filename("fork", loc) + ".html")
            if not p.exists() or p.read_text(encoding="utf-8") != pages[loc]:
                stale.append(p.name)
        for name, text in details.items():
            p = DOCS / name
            if not p.exists() or p.read_text(encoding="utf-8") != text:
                stale.append(name)
        if stale:
            print("stale: " + ", ".join(sorted(set(stale))))
            return 1
        print("up to date")
        return 0
    OUT_JSON.write_text(blob, encoding="utf-8")
    for loc in LOCALES:
        (DOCS / (locale_filename("fork", loc) + ".html")).write_text(pages[loc], encoding="utf-8")
    for name, text in details.items():
        (DOCS / name).write_text(text, encoding="utf-8")
    counts = ", ".join(f"{s['label']}: {len(s['ours'])}+{len(s['upstream'])}" for s in data["suites"])
    print(f"wrote {OUT_JSON.name} + fork[-.en].html + {len(details)} detail page(s) ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
