#!/usr/bin/env python3
"""Generate docs/fork-medium90-results.json + docs/fork-run-<id>.html.

Scans run JSON in ``<repo>/results`` (checkout wins) then ``<repo>/../results``
(de-duplicated by file name), keeps only runs whose file-name stem belongs to one
of the Medium umbrella waves, aggregates them per (harness, model, profile) and
writes the *frozen* schema from
``openspec/changes/add-fork-results-pages/design.md``.

On write (not ``--check`` / ``--json``) it also (re)generates one static
per-run detail page ``docs/fork-run-<id>.html`` per row, per
``openspec/changes/add-fork-run-details/design.md``: waves, per-task entries
(from each run's top-level ``results`` array) and sources. Every dynamic text
value is HTML-escaped.

Waves (umbrella suite "Medium (392-471)"):
    long        392-411  20 tasks   (stem token "long")
    reflect     412-431  20 tasks   (stem token "reflect")
    feasibility 432-471  40 tasks   (stem token "feasibility" or "wave19")

Usage:
    python3 scripts/build_fork_pages.py                 # write JSON + detail pages
    python3 scripts/build_fork_pages.py --check         # exit 1 if the JSON is stale
    python3 scripts/build_fork_pages.py --json          # print the JSON to stdout
    python3 scripts/build_fork_pages.py --out PATH      # write/compare against PATH
"""
from __future__ import annotations

import html
import json
import math
import re
import statistics
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fork_i18n import DEFAULT_LOCALE, LOCALES, locale_filename, other_locale, t  # noqa: E402
from fork_metrics import steps_from_json  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "docs" / "fork-medium90-results.json"


GENERATED_BY = "scripts/build_fork_pages.py"
TASK_SET_VERSION = "v0.21.3"
MEDIUM_TASKS = 90
PROVENANCE = (
    "Fork run artifacts scanned from results/ (checkout first) + ../results/, "
    "de-duplicated by file name; k=1 (no repeats per config); no infra failures "
    "recorded; partial = summed total < expected tasks for the wave."
)

# key -> (label, task_range, expected tasks)
# The fork's Medium suite is the full 90-task set: historical 80 (392-471) plus the
# ten selected wave-21 coupled workflows (512-530).
WAVES = {
    "long": ("Long context", "392\u2013411", 20),
    "reflect": ("Tool reflection", "412\u2013431", 20),
    "feasibility": ("Feasibility", "432\u2013471", 40),
    "composite_hard": ("Composite hard (wave 21)", "512\u2013530", 10),
}
WAVE_ORDER = ["long", "reflect", "feasibility", "composite_hard"]

# task_id integer -> wave (per design: 392-411 long, 412-431 reflect, 432-471
# feasibility, 512-530 wave-21 composite-hard).
TASK_ID_WAVES = (
    (392, 411, "long"),
    (412, 431, "reflect"),
    (432, 471, "feasibility"),
    (512, 530, "composite_hard"),
)

# Frozen upstream medium90 snapshots with per-task elapsed_seconds (tasks 392-471).
# Read-only: the generator never writes these files.
UPSTREAM_DURATION_SOURCES = (
    ("Codex + DeepSeek", "codex-deepseek-medium90-20261027.json"),
    ("mini-SWE + DeepSeek", "miniswe-deepseek-medium90-20261027.json"),
    ("Claude Code + Sonnet", "claudecode-sonnet-medium90-20261027.json"),
)
OURS_DURATION_LABEL = "Pi (ours)"

REQUIRED_SUMMARY_FIELDS = (
    "passed",
    "total",
    "pass_rate",
    "total_seconds",
    "total_steps",
    "total_tokens",
    "estimated_cost_rub",
)

# Metadata constants and classifiers imported from the single-source module.
from fork_metadata import (  # noqa: E402
    DASH,
    EXCLUDE_KEYWORDS,
    RESULT_DIRS,
    classify_harness,
    classify_model,
    classify_profile,
    collect_paths,
    harness_version,
)

# Known fork wave artifacts whose file name lacks the harness/model token.
# (Explicit, documented: avoids a bogus "unknown" row for real runs.)
RUN_META_OVERRIDES = {
    "pi_pilot_long3": {"model": "DeepSeek-V4.1-Flash"},
    "wave19_full_ds41flash": {"harness": "Pi"},
}

WILSON_Z = 1.96

SOURCE_BASE_URL = "https://github.com/Alex-Kul777/harness-bench-fast/blob/main/results/"

# ---- capability sample (docs/fork-sample.html + docs/fork-sample-results.json) --
SAMPLE_MANIFEST = REPO_ROOT / "docs" / "fork-sample-manifest.json"
SAMPLE_OUT_NAME = "fork-sample-results.json"
SAMPLE_HTML_NAME = "fork-sample.html"
SAMPLE_MANIFEST_NAME = "fork-sample-manifest.json"
SAMPLE_CONFIG = {"harness": "Pi", "model": "DeepSeek-V4.1-Flash"}
# Frozen allow-list of raw artifacts backing the sample (resolved in RESULT_DIRS order).
SAMPLE_SOURCES = {
    "long": ["pi_sample_long_20261106.json"],
    "reflect": ["pi_sample_reflect_20261106.json", "pi_reflect_ds41flash_20261106.json"],
    "feasibility": ["wave19_full_ds41flash.json"],
}
SAMPLE_INVALID_REASON = "timeout 2400 < wave floor 7200"
SAMPLE_ADR = "adr/0003-capability-sample.md"


def _override(stem: str, field: str) -> str | None:
    lower = stem.lower()
    for prefix, meta in RUN_META_OVERRIDES.items():
        if prefix in lower and field in meta:
            return meta[field]
    return None


def classify_wave(stem: str) -> str | None:
    lower = stem.lower()
    if "composite" in lower or "wave21" in lower:
        return "composite_hard"
    if "long" in lower:
        return "long"
    if "reflect" in lower:
        return "reflect"
    if "feasibility" in lower or "wave19" in lower:
        return "feasibility"
    return None


def classify_task_wave(task_id) -> str | None:
    """Map a task_id (e.g. 'task_393_intranet_multihop') to a wave, else None."""
    match = re.search(r"\d+", str(task_id))
    if match is None:
        return None
    number = int(match.group())
    for low, high, wave in TASK_ID_WAVES:
        if low <= number <= high:
            return wave
    return None


def wilson_interval(passed: int, total: int, z: float = WILSON_Z) -> list[float]:
    """95% Wilson score interval for passed/total, rounded to 3 decimals."""
    if total <= 0:
        return [0.0, 0.0]
    n = float(total)
    p = passed / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (z / math.sqrt(n)) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    lo = max(0.0, center - half)
    hi = min(1.0, center + half)
    return [round(lo, 3), round(hi, 3)]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def row_id(harness, model, profile) -> str:
    parts = [_slug(p) for p in (harness, model, profile) if p]
    parts = [p for p in parts if p]
    return "-".join(parts) if parts else "unknown"


def _num(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _finite_floats(values):
    """Keep only finite floats from an iterable of possibly-missing numbers."""
    out = []
    for value in values:
        num = _num(value)
        if num is not None and math.isfinite(num):
            out.append(num)
    return out


def duration_stats(values) -> dict | None:
    """{n, mean, median, max} in seconds (1 dp) or None when nothing to measure."""
    clean = _finite_floats(values)
    if not clean:
        return None
    return {
        "n": len(clean),
        "mean": round(statistics.mean(clean), 1),
        "median": round(statistics.median(clean), 1),
        "max": round(max(clean), 1),
    }


def build_duration_by_wave(ours_durations: dict) -> dict:
    """Per wave: ``Pi (ours)`` first, then each available upstream configuration.

    A configuration with no measured task for a wave is omitted (never 0).
    """
    upstream = load_upstream_durations()
    by_wave: dict[str, list] = {}
    for wave in WAVE_ORDER:
        entries: list[dict] = []
        ours = duration_stats(ours_durations.get(wave, []))
        if ours is not None:
            entries.append({"label": OURS_DURATION_LABEL, **ours})
        for label, _filename in UPSTREAM_DURATION_SOURCES:
            stats = duration_stats(upstream.get(label, {}).get(wave, []))
            if stats is not None:
                entries.append({"label": label, **stats})
        by_wave[wave] = entries
    return by_wave


def load_upstream_durations() -> dict:
    """{label: {wave: [elapsed_seconds, ...]}} from the frozen upstream snapshots.

    Only tasks in the Medium range (392-471) are kept; a task outside every wave
    is ignored. Missing/unreadable files degrade to empty buckets.
    """
    docs_dir = REPO_ROOT / "docs"
    result: dict[str, dict[str, list[float]]] = {}
    for label, filename in UPSTREAM_DURATION_SOURCES:
        buckets: dict[str, list[float]] = {wave: [] for wave in WAVE_ORDER}
        path = docs_dir / filename
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[skip] {filename}: unreadable ({exc})", file=sys.stderr)
            result[label] = buckets
            continue
        outcomes = data.get("outcomes")
        if isinstance(outcomes, list):
            for entry in outcomes:
                if not isinstance(entry, dict):
                    continue
                wave = classify_task_wave(entry.get("number"))
                if wave is None:
                    continue
                seconds = _num(entry.get("elapsed_seconds"))
                if seconds is not None and math.isfinite(seconds):
                    buckets[wave].append(seconds)
        result[label] = buckets
    return result


def _task_entry(entry: dict) -> dict | None:
    """Normalise one ``results[]`` entry; None when its task_id maps to no wave."""
    if not isinstance(entry, dict):
        return None
    task_id = entry.get("task_id")
    wave = classify_task_wave(task_id)
    if wave is None:
        return None
    message = entry.get("message")
    return {
        "task_id": str(task_id),
        "wave": wave,
        "passed": bool(entry.get("passed")),
        "message": "" if message is None else str(message),
        "error": "" if entry.get("error") is None else str(entry.get("error")),
        "elapsed_seconds": _num(entry.get("elapsed_seconds")),
        "agent_steps": _int(entry.get("agent_steps")),
        "agent_total_tokens": _int(entry.get("agent_total_tokens")),
    }


def _empty_usage() -> dict:
    return {"seconds": 0.0, "steps": 0, "tokens": 0, "cost_rub": 0.0}


_TASK_NUM_RE = re.compile(r"task_(\d+)")


def _artifact_task_numbers(data: dict) -> dict[int, bool]:
    r"""Map task number -> passed for one run artifact ({} when none parseable).

    Mirrors ``build_fork_home._TASK_NUM_RE``: the number comes from the entry's
    ``number`` field, else from ``task_(\d+)`` inside ``task_id``. Both
    OpenClaw-runner (top-level ``tasks``) and Pi-runner (top-level ``results``)
    artifacts are supported.
    """
    entries = data.get("results") or data.get("tasks") or []
    numbers: dict[int, bool] = {}
    if not isinstance(entries, list):
        return numbers
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        number = entry.get("number")
        if number is None:
            match = _TASK_NUM_RE.search(str(entry.get("task_id") or ""))
            number = int(match.group(1)) if match else None
        if number is not None:
            numbers[int(number)] = bool(entry.get("passed"))
    return numbers


def _aggregate_wave_suite(artifacts: list[dict]) -> dict:
    """Union a wave's task numbers across artifacts; fall back to summed totals.

    Artifacts are processed in ascending file-name order (stems end ``_YYYYMMDD``,
    so lexicographic == chronological) and a later artifact supersedes an earlier
    one for the same task number. When no artifact yields a parseable number the
    legacy behaviour (sum of per-file ``summary`` totals) is kept.
    """
    numbers: dict[int, bool] = {}
    sum_passed = 0
    sum_total = 0
    for artifact in sorted(artifacts, key=lambda a: a["name"]):
        sum_passed += artifact["summary_passed"]
        sum_total += artifact["summary_total"]
        for number, passed in artifact["numbers"].items():
            numbers[number] = passed
    if numbers:
        return {
            "passed": sum(1 for passed in numbers.values() if passed),
            "total": len(numbers),
        }
    return {"passed": sum_passed, "total": sum_total}


def build_model(result_dirs: list[Path] | None = None) -> dict:
    groups: dict[tuple, dict] = {}
    tasks_by_id: dict[str, list] = {}

    for name, path in collect_paths(result_dirs).items():
        stem = path.stem
        lower = stem.lower()
        wave = classify_wave(stem)
        if wave is None:
            continue
        if any(keyword in lower for keyword in EXCLUDE_KEYWORDS):
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
        if any(field not in summary for field in REQUIRED_SUMMARY_FIELDS):
            print(f"[skip] {name}: summary missing required fields", file=sys.stderr)
            continue

        harness = classify_harness(stem) or _override(stem, "harness")
        model = classify_model(stem) or _override(stem, "model")
        if harness is None or model is None:
            print(f"[skip] {name}: unresolved harness/model", file=sys.stderr)
            continue

        key = (harness, model, classify_profile(stem))
        group = groups.setdefault(
            key,
            {
                "suites": {w: {"passed": 0, "total": 0} for w in WAVE_ORDER},
                "wave_artifacts": {w: [] for w in WAVE_ORDER},
                "usage": _empty_usage(),
                "wave_usage": {w: _empty_usage() for w in WAVE_ORDER},
                "durations": {w: [] for w in WAVE_ORDER},
                "sources": [],
                "versions": set(),
            },
        )
        version = harness_version(data)
        if version:
            group["versions"].add(version)

        group["wave_artifacts"][wave].append(
            {
                "name": name,
                "numbers": _artifact_task_numbers(data),
                "summary_passed": int(summary.get("passed") or 0),
                "summary_total": int(summary.get("total") or 0),
            }
        )

        usage = group["usage"]
        usage["seconds"] += float(summary.get("total_seconds") or 0.0)
        usage["steps"] += int(steps_from_json(data) or 0)
        usage["tokens"] += int(summary.get("total_tokens") or 0)
        usage["cost_rub"] += float(summary.get("estimated_cost_rub") or 0.0)

        # Per-wave usage: each run file belongs to exactly one wave (by stem).
        wu = group["wave_usage"][wave]
        wu["seconds"] += float(summary.get("total_seconds") or 0.0)
        wu["steps"] += int(steps_from_json(data) or 0)
        wu["tokens"] += int(summary.get("total_tokens") or 0)
        wu["cost_rub"] += float(summary.get("estimated_cost_rub") or 0.0)

        group["sources"].append(name)

        # Per-task entries from the run's top-level ``results`` array.
        results = data.get("results")
        if isinstance(results, list):
            rid = row_id(harness, model, classify_profile(stem))
            bucket = tasks_by_id.setdefault(rid, [])
            for entry in results:
                normalised = _task_entry(entry)
                if normalised is not None:
                    bucket.append(normalised)
                # Per-task wall-clock time for this run's wave (ours).
                if isinstance(entry, dict):
                    seconds = _num(entry.get("elapsed_seconds"))
                    if seconds is not None and math.isfinite(seconds):
                        group["durations"][wave].append(seconds)

    rows: list[dict] = []
    page_details: dict[str, dict] = {}
    ours_durations: dict[str, list[float]] = {w: [] for w in WAVE_ORDER}
    for (harness, model, profile), group in groups.items():
        suites: dict[str, object] = {}
        wilson: dict[str, list[float]] = {}
        for wave in WAVE_ORDER:
            # Union wave task numbers across this config's artifacts (legacy sum
            # fallback when no artifact yields a parseable task number).
            group["suites"][wave] = _aggregate_wave_suite(group["wave_artifacts"][wave])
        for wave in WAVE_ORDER:
            # Our per-task times pool across every measured run for this wave.
            ours_durations[wave].extend(group["durations"][wave])
            agg = group["suites"][wave]
            if not _wave_present(group, wave):
                # No run contributed to this wave => unmeasured (not a failure).
                suites[wave] = None
                continue
            expected = WAVES[wave][2]
            measurement = "partial" if agg["total"] < expected else "complete"
            suite = {
                "passed": agg["passed"],
                "total": agg["total"],
                "measurement": measurement,
            }
            duration = duration_stats(group["durations"][wave])
            if duration is not None:
                suite["duration"] = duration
            suites[wave] = suite
            wilson[wave] = wilson_interval(agg["passed"], agg["total"])

        rid = row_id(harness, model, profile)
        rows.append(
            {
                "id": rid,
                "details_url": f"fork-run-{rid}.html",
                "harness": harness,
                "model": model,
                "profile": profile,
                "harness_version": (sorted(group["versions"])[0] if group["versions"] else None),
                "runs": len(group["sources"]),
                "suites": suites,
                "usage": {
                    "seconds": round(group["usage"]["seconds"], 1),
                    "steps": group["usage"]["steps"],
                    "tokens": group["usage"]["tokens"],
                    "cost_rub": round(group["usage"]["cost_rub"], 1),
                },
                "wilson": wilson,
                "sources": sorted(group["sources"]),
            }
        )
        page_details[rid] = {
            "tasks": sorted(
                tasks_by_id.get(rid, []),
                key=lambda t: (WAVE_ORDER.index(t["wave"]), t["task_id"]),
            ),
            "wave_usage": group["wave_usage"],
        }

    rows.sort(
        key=lambda r: (
            r["harness"] is None,
            r["harness"] or "",
            r["model"] or "",
            r["profile"] or "",
        )
    )

    duration_by_wave = build_duration_by_wave(ours_durations)

    return {
        "generated_by": GENERATED_BY,
        "task_set_version": TASK_SET_VERSION,
        "medium_tasks": MEDIUM_TASKS,
        "provenance": PROVENANCE,
        "waves": [
            {
                "key": key,
                "label": WAVES[key][0],
                "task_range": WAVES[key][1],
                "tasks": WAVES[key][2],
            }
            for key in WAVE_ORDER
        ],
        "rows": rows,
        "duration_by_wave": duration_by_wave,
        "_page_details": page_details,
    }


def _wave_present(group: dict, wave: str) -> bool:
    agg = group["suites"][wave]
    return agg["total"] > 0 or agg["passed"] > 0


# ---- formatting helpers (mirror docs/fork-results.js conventions) -------------

def fmt_compact(value) -> str:
    """1321 -> '1,321'; 48463320 -> '48.46M'; None -> em dash."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DASH
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M")):
        if abs(number) >= divisor:
            text = f"{number / divisor:.2f}".rstrip("0").rstrip(".")
            return text + suffix
    return f"{int(round(number)):,}"


def fmt_cost(value) -> str:
    """7188.5 -> '7 189\u20bd'."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DASH
    return f"{int(round(number)):,}".replace(",", " ") + "\u20bd"


def fmt_duration(seconds) -> str:
    """17835.9 -> '297.3m'."""
    try:
        number = float(seconds)
    except (TypeError, ValueError):
        return DASH
    return f"{number / 60:.1f}m"


def fmt_seconds(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DASH
    return f"{number:.1f}"


def fmt_percent(passed, total) -> str:
    if not total:
        return DASH
    return f"{100 * passed / total:.1f}%"


def fmt_interval(range_value) -> str:
    if not isinstance(range_value, list) or len(range_value) < 2:
        return DASH
    return f"{100 * range_value[0]:.1f}\u2013{100 * range_value[1]:.1f}%"


def fmt_tokens_cost(tokens, cost) -> str:
    return f"{fmt_compact(tokens)} ({fmt_cost(cost)})"


def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def trunc(text) -> str:
    """Frozen: keep s if len<=120, else s[:119] + ellipsis."""
    s = "" if text is None else str(text)
    return s if len(s) <= 120 else s[:119] + "\u2026"


def _unwrap_single_key(obj):
    """If obj is a single-key dict whose only value is a dict, return that inner dict."""
    if isinstance(obj, dict) and len(obj) == 1:
        only = next(iter(obj.values()))
        if isinstance(only, dict):
            return only
    return obj


def task_reason(message, error):
    """Short, human-readable reason for a task row (frozen algorithm)."""
    if error:
        return error
    m = (message or "").strip()
    if not m.startswith("{"):
        return trunc(m)
    try:
        obj = json.loads(m)
    except Exception:
        return trunc(m)
    obj = _unwrap_single_key(obj)
    if isinstance(obj, dict):
        if isinstance(obj.get("reason"), str) and obj["reason"].strip():
            return obj["reason"]
        bits = [
            f"{key}={obj[key]}"
            for key in (
                "decision_correct",
                "response_correct",
                "invalid_calls",
                "extra_requests",
                "status",
                "submission_count",
            )
            if key in obj
        ]
        if bits:
            return ", ".join(bits)
    return trunc(m)


def _message_obj(message):
    """Return (obj, True) for parsed (unwrapped) JSON, else (None, False)."""
    m = (message or "").strip()
    if not m.startswith("{"):
        return None, False
    try:
        obj = json.loads(m)
    except Exception:
        return None, False
    return _unwrap_single_key(obj), True


def task_note_cell(message, error) -> str:
    """Escaped Note cell: reason plus a collapsible verifier output for fails."""
    cell = _esc(task_reason(message, error))
    if not (error or message.strip().startswith("{")):
        return cell
    obj, parsed = _message_obj(message)
    pre_text = json.dumps(obj, ensure_ascii=False, indent=2) if parsed else message
    detail = (
        '<details class="task-detail"><summary>output</summary>'
        f"<pre>{_esc(pre_text)}</pre>"
    )
    if error:
        detail += f"<p>error: {_esc(error)}</p>"
    detail += "</details>"
    return cell + detail


# ---- capability sample builder + renderer ------------------------------------


def _resolve_result_path(name: str):
    """First existing path for ``name`` across RESULT_DIRS (checkout first)."""
    for directory in RESULT_DIRS:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _load_json_lenient(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[skip] {path.name}: unreadable ({exc})", file=sys.stderr)
        return None


def collect_sample_entries() -> dict:
    """task_id -> first ``results[]`` entry across the frozen allow-list (in order)."""
    entries: dict[str, dict] = {}
    for wave in WAVE_ORDER:
        for name in SAMPLE_SOURCES.get(wave, []):
            path = _resolve_result_path(name)
            if path is None:
                print(f"[skip] sample source missing: {name}", file=sys.stderr)
                continue
            data = _load_json_lenient(path)
            if not isinstance(data, dict):
                continue
            results = data.get("results")
            if not isinstance(results, list):
                continue
            for entry in results:
                if not isinstance(entry, dict):
                    continue
                task_id = entry.get("task_id")
                if task_id is None:
                    continue
                entries.setdefault(str(task_id), entry)
    return entries


def _band_mix(tasks) -> str:
    """Human band breakdown, e.g. 'ceiling \u00d73, boundary \u00d72'."""
    order = ("ceiling", "boundary", "floor")
    counts: dict[str, int] = {}
    for task in tasks:
        band = task.get("band") or "unknown"
        counts[band] = counts.get(band, 0) + 1
    parts = []
    for band in list(order) + [b for b in counts if b not in order]:
        if counts.get(band):
            parts.append(f"{band} \u00d7{counts[band]}")
    return ", ".join(parts) or DASH


def _sample_provenance() -> str:
    parts = [f"{wave}={' + '.join(SAMPLE_SOURCES.get(wave, []))}" for wave in WAVE_ORDER if SAMPLE_SOURCES.get(wave)]
    return (
        "Capability sample assembled from docs/fork-sample-manifest.json; per-task results "
        "resolved from the frozen allow-list artifacts in results/ (checkout first, then "
        "../results/), first matching task_id wins: " + "; ".join(parts) + "."
    )


def build_sample_model() -> dict:
    manifest = _load_json_lenient(SAMPLE_MANIFEST)
    if not isinstance(manifest, dict):
        raise SystemExit(f"error: cannot read {SAMPLE_MANIFEST}")
    entries = collect_sample_entries()

    tasks: list[dict] = []
    for item in manifest.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        wave = item.get("wave")
        task_id = str(item.get("task_id") or "")
        entry = entries.get(task_id)
        invalid = wave == "long"
        record = {
            "number": item.get("number"),
            "task_id": task_id,
            "wave": wave,
            "band": item.get("band"),
            "passed": None,
            "measured": False,
            "elapsed_seconds": None,
            "agent_steps": None,
            "agent_total_tokens": None,
            "invalid": invalid,
            "note": "",
        }
        if invalid:
            record["invalid_reason"] = SAMPLE_INVALID_REASON
        if entry is not None:
            message = entry.get("message")
            error = entry.get("error")
            message = "" if message is None else str(message)
            error = "" if error is None else str(error)
            record["passed"] = bool(entry.get("passed"))
            record["measured"] = True
            record["elapsed_seconds"] = _num(entry.get("elapsed_seconds"))
            record["agent_steps"] = _int(entry.get("agent_steps"))
            record["agent_total_tokens"] = _int(entry.get("agent_total_tokens"))
            record["note"] = task_reason(message, error)
            record["_message"] = message
            record["_error"] = error
        tasks.append(record)

    waves = []
    for key in WAVE_ORDER:
        wave_tasks = [t for t in tasks if t["wave"] == key]
        if not wave_tasks:
            continue
        info = {
            "key": key,
            "label": WAVES[key][0],
            "task_range": WAVES[key][1],
            "tasks": len(wave_tasks),
            "passed": sum(1 for t in wave_tasks if t.get("passed")),
            "invalid": key == "long",
        }
        if key == "long":
            info["invalid_reason"] = SAMPLE_INVALID_REASON
        waves.append(info)

    return {
        "generated_by": GENERATED_BY,
        "id": manifest.get("id"),
        "size": manifest.get("size"),
        "config": dict(SAMPLE_CONFIG),
        "provenance": _sample_provenance(),
        "manifest": SAMPLE_MANIFEST_NAME,
        "waves": waves,
        "tasks": [{k: v for k, v in t.items() if not k.startswith("_")} for t in tasks],
        "_tasks_full": tasks,
    }


def render_sample_json(model: dict) -> str:
    clean = {key: value for key, value in model.items() if not key.startswith("_")}
    return json.dumps(clean, ensure_ascii=False, indent=2) + "\n"


def _sample_result_label(task: dict) -> str:
    if task.get("invalid"):
        return "invalid"
    if not task.get("measured"):
        return "not measured"
    return "pass" if task.get("passed") else "fail"


def render_sample_html(model: dict, locale: str = DEFAULT_LOCALE) -> str:
    tasks = model.get("_tasks_full") or []
    config = model.get("config") or {}
    model_name = config.get("model") or DASH
    harness = config.get("harness") or DASH
    size = model.get("size")

    # ---- waves summary ----
    wave_rows = []
    for wave in model["waves"]:
        key = wave["key"]
        wave_tasks = [w for w in tasks if w["wave"] == key]
        mix = _band_mix(wave_tasks)
        status = (
            t(locale, "status_invalid", reason=SAMPLE_INVALID_REASON)
            if wave.get("invalid")
            else t(locale, "status_measured")
        )
        wave_rows.append(
            "<tr>"
            f'<td>{_esc(wave["label"])}<span class="harness">{_esc(wave["task_range"])}</span></td>'
            f'<td class="numeric">{_esc(wave["tasks"])}</td>'
            f'<td class="numeric">{_esc(wave["passed"])}</td>'
            f'<td class="numeric">{_esc(fmt_percent(wave["passed"], wave["tasks"]))}</td>'
            f"<td>{_esc(mix)}</td>"
            f"<td>{_esc(status)}</td>"
            "</tr>"
        )
    waves_table = (
        '<div class="table-wrap"><table class="leaderboard-table"><thead><tr>'
        '<th scope="col">Wave</th><th scope="col" class="numeric">Tasks</th>'
        '<th scope="col" class="numeric">Passed</th><th scope="col" class="numeric">%</th>'
        '<th scope="col">Band mix</th><th scope="col">Status</th>'
        "</tr></thead><tbody>" + "".join(wave_rows) + "</tbody></table></div>"
    )

    # ---- per-task ----
    task_rows = []
    for task in tasks:
        result = _sample_result_label(task)
        note = task_note_cell(task.get("_message", ""), task.get("_error", ""))
        task_rows.append(
            "<tr>"
            f'<td class="numeric">{_esc(task.get("number"))}</td>'
            f'<td><code>{_esc(task.get("task_id"))}</code></td>'
            f'<td>{_esc(task.get("wave"))}</td>'
            f'<td>{_esc(task.get("band"))}</td>'
            f'<td>{_esc(result)}</td>'
            f'<td class="numeric">{_esc(fmt_seconds(task.get("elapsed_seconds")))}</td>'
            f'<td class="numeric">{_esc(fmt_compact(task.get("agent_steps")))}</td>'
            f'<td class="numeric">{_esc(fmt_compact(task.get("agent_total_tokens")))}</td>'
            f'<td>{note}</td>'
            "</tr>"
        )
    tasks_table = (
        '<div class="table-wrap"><table class="leaderboard-table"><thead><tr>'
        '<th scope="col" class="numeric">#</th><th scope="col">Task</th>'
        '<th scope="col">Wave</th><th scope="col">Band</th><th scope="col">Result</th>'
        '<th scope="col" class="numeric">Time (s)</th><th scope="col" class="numeric">Steps</th>'
        '<th scope="col" class="numeric">Tokens</th><th scope="col">Note</th>'
        "</tr></thead><tbody>" + "".join(task_rows) + "</tbody></table></div>"
    )

    # ---- sources / links ----
    source_items = []
    for wave in WAVE_ORDER:
        for name in SAMPLE_SOURCES.get(wave, []):
            url = SOURCE_BASE_URL + urllib.parse.quote(name)
            source_items.append(
                f'<li>{_esc(wave)}: <a href="{_esc(url)}"><code>{_esc(name)}</code></a></li>'
            )
    sources_section = (
        f'<section aria-label="Sources"><h2>{_esc(t(locale, "sample_sources_title"))}</h2>'
        f'<p class="coverage-note">{t(locale, "sample_sources_note")}</p>'
        '<ul class="run-sources">' + "".join(source_items) + "</ul></section>"
    )

    description = (
        f"Cost-bounded capability sample ({size} tasks = 25% of the Medium 80 suite, 392-471) "
        f"run by {harness} on {model_name}: long context, tool reflection and feasibility."
    )

    fork_href = f"{locale_filename('fork', locale)}.html#medium"
    switch = locale_filename(SAMPLE_HTML_NAME[:-5], other_locale(locale)) + ".html"
    return f"""<!doctype html>
<html lang="{_esc(t(locale, 'html_lang'))}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{_esc(description)}">
  <title>{_esc(t(locale, 'sample_title', n=size))}</title>
  <link rel="stylesheet" href="landing.css">
  <style>.task-detail pre{{max-height:320px;overflow:auto;white-space:pre-wrap;word-break:break-word}}</style>
</head>
<body>
  <header>
    <a class="brand" href="index.html">harness_bench_fast<span class="brand-dot">.</span></a>
    <nav aria-label="Primary"><a href="{fork_href}">{_esc(t(locale, 'nav_fork_results'))}</a> <a href="{SAMPLE_MANIFEST_NAME}">{_esc(t(locale, 'nav_manifest'))}</a> <a class="lang-switch" href="{switch}" title="{_esc(t(locale, 'lang_switch_title'))}">{_esc(t(locale, 'lang_switch'))}</a></nav>
  </header>
  <main>
    <section class="intro">
      <p class="eyebrow">{_esc(t(locale, 'sample_eyebrow'))}</p>
      <h1>{_esc(t(locale, 'sample_h1', n=size))}</h1>
      <p class="lead">{_esc(t(locale, 'sample_lead', model=model_name, harness=harness, n=size))}</p>
      <p class="coverage-note">{t(locale, 'sample_rule_note', manifest=SAMPLE_MANIFEST_NAME, adr=SAMPLE_ADR)} · <a href="{fork_href}">{_esc(t(locale, 'nav_fork_results'))} \u2192</a></p>
    </section>

    <section aria-label="Waves">
      <h2>{_esc(t(locale, 'sample_waves_title'))}</h2>
      <p class="coverage-note">{t(locale, 'sample_waves_note')}</p>
      {waves_table}
    </section>

    <section aria-label="Tasks">
      <h2>{_esc(t(locale, 'sample_tasks_title'))}</h2>
      <p class="coverage-note">{t(locale, 'sample_tasks_note', n=size)}</p>
      {tasks_table}
    </section>

    {sources_section}

    <p class="fine-print">{t(locale, 'sample_fine_print')} <a href="{SAMPLE_MANIFEST_NAME}">{_esc(t(locale, 'nav_manifest'))}</a> · <a href="{fork_href}">\u2190 {_esc(t(locale, 'nav_fork_results'))}</a></p>
  </main>
  <footer><span>{_esc(t(locale, 'footer_note'))}</span><a href="{fork_href}">{_esc(t(locale, 'sample_footer_link'))}</a></footer>
  <script>document.querySelectorAll('a.lang-switch').forEach(function(a){{if(location.hash)a.href+=location.hash;}});</script>
</body>
</html>
"""


def write_sample_outputs(model: dict, text: str, out_dir: Path) -> None:
    (out_dir / SAMPLE_OUT_NAME).write_text(text, encoding="utf-8")
    for loc in LOCALES:
        name = locale_filename(SAMPLE_HTML_NAME[:-5], loc) + ".html"
        (out_dir / name).write_text(render_sample_html(model, loc), encoding="utf-8")


def _run_page_names(row: dict) -> list[str]:
    base = row["details_url"][:-5] if row["details_url"].endswith(".html") else row["details_url"]
    return [locale_filename(base, loc) + ".html" for loc in LOCALES]


# ---- per-run detail page renderer --------------------------------------------

def _source_item(name: str) -> str:
    safe = _esc(name)
    if (REPO_ROOT / "results" / name).is_file():
        url = SOURCE_BASE_URL + urllib.parse.quote(name)
        return f'<li><a href="{_esc(url)}"><code>{safe}</code></a></li>'
    return f"<li><code>{safe}</code></li>"


def render_run_page(row: dict, details: dict, locale: str = DEFAULT_LOCALE) -> str:
    model_name = row.get("model") or DASH
    harness = row.get("harness") or DASH
    profile = row.get("profile")
    runs = int(row.get("runs") or 0)

    title_text = f"{model_name} {DASH} {harness}"
    if profile:
        title_text += f" ({profile})"

    measured_waves = [w for w in WAVE_ORDER if row["suites"].get(w)]
    wave_count = len(measured_waves)

    # ---- Waves table ----
    wave_usage = details.get("wave_usage", {})
    wave_rows = []
    for wave in WAVE_ORDER:
        suite = row["suites"].get(wave)
        if not suite:
            continue
        label, task_range, _expected = WAVES[wave]
        usage = wave_usage.get(wave) or {}
        wave_rows.append(
            "<tr>"
            f'<td>{_esc(label)}<span class="harness">{_esc(task_range)}</span></td>'
            f'<td class="numeric">{_esc(suite["total"])}</td>'
            f'<td class="numeric">{_esc(suite["passed"])}</td>'
            f'<td class="numeric">{_esc(fmt_percent(suite["passed"], suite["total"]))}</td>'
            f'<td class="numeric">{_esc(fmt_interval(row["wilson"].get(wave)))}</td>'
            f'<td class="numeric">{_esc(fmt_duration(usage.get("seconds")))}</td>'
            f'<td class="numeric">{_esc(fmt_compact(usage.get("steps")))}</td>'
            f'<td class="numeric">{_esc(fmt_tokens_cost(usage.get("tokens"), usage.get("cost_rub")))}</td>'
            "</tr>"
        )
    if wave_rows:
        waves_table = (
            '<div class="table-wrap"><table class="leaderboard-table"><thead><tr>'
            '<th scope="col">Wave</th><th scope="col" class="numeric">Tasks</th>'
            '<th scope="col" class="numeric">Passed</th><th scope="col" class="numeric">%</th>'
            '<th scope="col" class="numeric">95% Wilson</th><th scope="col" class="numeric">Time</th>'
            '<th scope="col" class="numeric">Steps</th><th scope="col" class="numeric">Tokens (cost)</th>'
            "</tr></thead><tbody>" + "".join(wave_rows) + "</tbody></table></div>"
        )
    else:
        waves_table = f'<p id="empty">{_esc(t(locale, "run_waves_empty"))}</p>'

    # ---- Tasks table ----
    tasks = details.get("tasks") or []
    if tasks:
        task_rows = []
        for task in tasks:
            message = task.get("message") or ""
            error = task.get("error") or ""
            result = "pass" if task.get("passed") else "fail"
            task_rows.append(
                "<tr>"
                f'<td>{_esc(task.get("task_id"))}</td>'
                f'<td>{_esc(WAVES[task["wave"]][0])}</td>'
                f'<td>{_esc(result)}</td>'
                f'<td class="numeric">{_esc(fmt_seconds(task.get("elapsed_seconds")))}</td>'
                f'<td class="numeric">{_esc(fmt_compact(task.get("agent_steps")))}</td>'
                f'<td class="numeric">{_esc(fmt_compact(task.get("agent_total_tokens")))}</td>'
                f'<td>{task_note_cell(message, error)}</td>'
                "</tr>"
            )
        tasks_section = (
            f'<section aria-label="Tasks"><h2>{_esc(t(locale, "run_tasks_title"))}</h2>'
            f'<p class="coverage-note">{_esc(t(locale, "run_tasks_note"))}</p>'
            '<div class="table-wrap"><table class="leaderboard-table"><thead><tr>'
            '<th scope="col">Task</th><th scope="col">Wave</th><th scope="col">Result</th>'
            '<th scope="col" class="numeric">Time (s)</th><th scope="col" class="numeric">Steps</th>'
            '<th scope="col" class="numeric">Tokens</th><th scope="col">Note</th>'
            "</tr></thead><tbody>" + "".join(task_rows) + "</tbody></table></div></section>"
        )
    else:
        tasks_section = (
            f'<section aria-label="Tasks"><h2>{_esc(t(locale, "run_tasks_title"))}</h2>'
            f'<p id="empty">{_esc(t(locale, "run_tasks_empty"))}</p></section>'
        )

    # ---- Sources ----
    sources = row.get("sources") or []
    source_items = "".join(_source_item(name) for name in sources) or f"<li>{DASH}</li>"
    sources_section = (
        f'<section aria-label="Sources"><h2>{_esc(t(locale, "run_sources_title"))}</h2>'
        f'<p class="coverage-note">{t(locale, "run_sources_note")}</p>'
        f'<ul class="run-sources">{source_items}</ul></section>'
    )

    description = f"Run details for {model_name} on {harness}" + (f" ({profile})" if profile else "") + ": waves, tasks and sources."

    base = row["details_url"][:-5] if row["details_url"].endswith(".html") else row["details_url"]
    switch = locale_filename(base, other_locale(locale)) + ".html"
    fork_href = f"{locale_filename('fork', locale)}.html#medium"
    run_word = t(locale, "run_word_plural" if runs != 1 else "run_word")
    wave_word = t(locale, "wave_word_plural" if wave_count != 1 else "wave_word")
    return f"""<!doctype html>
<html lang="{_esc(t(locale, 'html_lang'))}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{_esc(description)}">
  <title>{_esc(t(locale, 'run_title', title=title_text))}</title>
  <link rel="stylesheet" href="landing.css">
  <style>.task-detail pre{{max-height:320px;overflow:auto;white-space:pre-wrap;word-break:break-word}}</style>
</head>
<body>
  <header>
    <a class="brand" href="index.html">harness_bench_fast<span class="brand-dot">.</span></a>
    <nav aria-label="Primary"><a href="{fork_href}">{_esc(t(locale, 'run_back_medium'))}</a> <a class="lang-switch" href="{switch}" title="{_esc(t(locale, 'lang_switch_title'))}">{_esc(t(locale, 'lang_switch'))}</a></nav>
  </header>
  <main>
    <section class="intro">
      <p class="eyebrow">{_esc(t(locale, 'run_eyebrow'))}</p>
      <h1>{_esc(title_text)}</h1>
      <p class="lead">{_esc(t(locale, 'run_lead', runs=runs, run_word=_esc(run_word), waves=wave_count, wave_word=_esc(wave_word)))}</p>
    </section>
    <section aria-label="Waves">
      <h2>{_esc(t(locale, 'run_waves_title'))}</h2>
      <p class="coverage-note">{_esc(t(locale, 'run_waves_note'))}</p>
      {waves_table}
    </section>
    {tasks_section}
    {sources_section}
    <p class="fine-print">{t(locale, 'run_fine_print')} <a href="{fork_href}">\u2190 {_esc(t(locale, 'nav_fork_results'))}</a></p>
  </main>
  <footer><span>{_esc(t(locale, 'footer_note'))}</span><a href="{fork_href}">{_esc(t(locale, 'sample_footer_link'))}</a></footer>
  <script>document.querySelectorAll('a.lang-switch').forEach(function(a){{if(location.hash)a.href+=location.hash;}});</script>
</body>
</html>
"""


def write_pages(model: dict, out_dir: Path) -> list[str]:
    details = model.get("_page_details", {})
    expected = set()
    written: list[str] = []
    for row in model["rows"]:
        for loc in LOCALES:
            name = locale_filename(row["details_url"][:-5] if row["details_url"].endswith(".html") else row["details_url"], loc) + ".html"
            expected.add(name)
            (out_dir / name).write_text(render_run_page(row, details.get(row["id"], {}), loc), encoding="utf-8")
            written.append(name)
    # Drop stale generated pages that no longer correspond to a row.
    for stale in sorted(out_dir.glob("fork-run-*.html")):
        if stale.name not in expected:
            stale.unlink()
    return written


def render(model: dict) -> str:
    clean = {key: value for key, value in model.items() if not key.startswith("_")}
    return json.dumps(clean, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str]) -> int:
    out_path = DEFAULT_OUT
    if "--out" in argv:
        idx = argv.index("--out")
        try:
            out_path = Path(argv[idx + 1])
        except IndexError:
            print("error: --out requires a PATH", file=sys.stderr)
            return 2

    emit_json = "--json" in argv
    check = "--check" in argv

    model = build_model()
    text = render(model)

    if emit_json:
        sys.stdout.write(text)
        return 0

    if check:
        try:
            current = out_path.read_text(encoding="utf-8")
        except OSError:
            current = None
        stale: list[str] = []
        if current != text:
            stale.append(out_path.name)
        details = model.get("_page_details", {})
        for row in model["rows"]:
            for loc in LOCALES:
                name = locale_filename(row["details_url"][:-5] if row["details_url"].endswith(".html") else row["details_url"], loc) + ".html"
                want = render_run_page(row, details.get(row["id"], {}), loc)
                p = out_path.parent / name
                if not p.exists() or p.read_text(encoding="utf-8") != want:
                    stale.append(name)
        sample_model = build_sample_model()
        for loc in LOCALES:
            name = locale_filename(SAMPLE_HTML_NAME[:-5], loc) + ".html"
            want = render_sample_html(sample_model, loc)
            p = out_path.parent / name
            if not p.exists() or p.read_text(encoding="utf-8") != want:
                stale.append(name)
        if stale:
            print(f"STALE: {', '.join(sorted(set(stale)))} {DASH} run: python3 scripts/build_fork_pages.py")
            return 1
        print(f"{out_path.name}: up to date ({len(model['rows'])} rows)")
        return 0

    try:
        current = out_path.read_text(encoding="utf-8")
    except OSError:
        current = None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pages = write_pages(model, out_path.parent)

    sample_model = build_sample_model()
    write_sample_outputs(sample_model, render_sample_json(sample_model), out_path.parent)
    sample_note = f"{SAMPLE_OUT_NAME} + {SAMPLE_HTML_NAME} regenerated"

    if current == text:
        print(
            f"{out_path.name}: already up to date ({len(model['rows'])} rows); "
            f"{len(pages)} detail page(s) regenerated; {sample_note}"
        )
        return 0
    out_path.write_text(text, encoding="utf-8")
    print(
        f"{out_path.name}: written ({len(model['rows'])} rows); "
        f"{len(pages)} detail page(s); {sample_note}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
