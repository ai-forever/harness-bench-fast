#!/usr/bin/env python3
"""Single source of truth for fork run metadata classification.

All three generators (build_fork_pages.py, build_fork_home.py,
build_fork_results.py) and the orchestrator (publish_fork.py) import this
module; per-module copies of these constants and classifiers are removed.

Public API:
    HARNESS_PREFIXES     -- ordered list of (prefix, harness_label)
    MODEL_TOKENS         -- ordered list of (token, model_label)
    THINKING_LEVEL_RE    -- compiled regex matching ``_<level>_<YYYYMMDD>``
    PROFILE_RULES        -- ordered list of (token, profile_label)
    RESULT_DIRS          -- [checkout results/, sibling ../results/]
    EXCLUDE_KEYWORDS     -- artifact stems that must never reach the page

    DASH                            -- canonical null-cell marker (U+2014)
    harness_version(data)           -> str | None

    resolve_stem(stem)              -> (harness, model, reasoning_level)
    classify_suite(stem)            -> str | None
    classify_harness(stem)          -> str | None
    classify_model(stem)            -> str | None
    classify_profile(stem)          -> str | None
    collect_paths(result_dirs=None) -> dict[str, Path]
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Canonical lists — longest / most-specific prefixes first within each group.
# ---------------------------------------------------------------------------

HARNESS_PREFIXES: tuple[tuple[str, str], ...] = (
    ("opencode_", "opencode"),
    ("openclaw_", "OpenClaw"),
    ("opencode",  "opencode"),   # bare stem fallback (no underscore)
    ("dsh_",      "DeepSeek Harness"),
    ("hermes_",   "Hermes"),
    ("pi_",       "Pi"),
    ("oc_",       "OpenClaw"),
)

MODEL_TOKENS: tuple[tuple[str, str], ...] = (
    # Cloud.ru new-model batch (2026-11-19) — specific tokens BEFORE their bases
    ("glm53flashx",  "GLM-5.3-FlashX"),
    ("gpt61sol",     "GPT-6.1-Sol"),
    ("kimik3",       "Kimi-K3"),
    ("gem38flash",   "Gemini-3.8-Flash"),
    ("sonnet46",     "Claude Sonnet 4.6"),
    ("sonnet45",     "Claude Sonnet 4.5"),
    ("opus55",       "Claude Opus 5.5"),
    ("opus48",       "Claude Opus 4.8"),
    ("fable51",      "Claude Fable 5.1"),
    ("fable5",       "Claude Fable 5"),
    ("ds41flash",    "DeepSeek-V4.1-Flash"),
    ("gpt55",        "GPT-5.5"),
    ("gigachat3pro", "GigaChat 3 Pro"),
    ("minimaxm3",    "MiniMax-M3"),
    ("giga35base",   "GigaChat 3.5-432B-A28B"),
    ("giga35",       "GigaChat 3.5 Reasoning"),
    ("glm53",        "GLM-5.3"),
    ("glm52",        "GLM-5.2"),
    ("qwen36",       "Qwen3.6-35B-A3B"),
    ("oss120b",      "GPT-OSS-120B"),
)

# Reasoning-sweep artifacts end with ``_<level>_<YYYYMMDD>``.
THINKING_LEVEL_RE: re.Pattern[str] = re.compile(r"_(off|minimal|low|medium|high|xhigh|max|ultra)_\d{8}$")

# Non-reasoning special profiles (ordered; first match wins).
PROFILE_RULES: tuple[tuple[str, str], ...] = (
    ("thinking_off", "thinking: off"),
    ("mcpoff",       "MCP off"),
)

# Canonical null-cell marker shared by every fork generator (single source).
DASH: str = "\u2014"

# Run artifacts record their harness version under one of these keys.
HARNESS_VERSION_KEYS: tuple[str, ...] = (
    "harness_version",
    "openclaw_version",
    "cli_version",
    "runner_version",
)

# Non-publishable artifacts: these keywords in a stem mean "skip".
EXCLUDE_KEYWORDS: tuple[str, ...] = (
    "smoke", "sample", "diag", "part2", "rerun",
)

# ---------------------------------------------------------------------------
# Default result directories (checkout wins on name clash).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
RESULT_DIRS: list[Path] = [_REPO_ROOT / "results", _REPO_ROOT.parent / "results"]

# ---------------------------------------------------------------------------
# Classifiers
# ---------------------------------------------------------------------------

def harness_version(data) -> str | None:
    """Return the harness version a run artifact records, or None."""
    for key in HARNESS_VERSION_KEYS:
        value = (data or {}).get(key)
        if value:
            return str(value)
    return None


def classify_harness(stem: str) -> str | None:
    """Return harness label for *stem*, or None if unrecognised."""
    lower = stem.lower()
    for prefix, harness in HARNESS_PREFIXES:
        if lower.startswith(prefix):
            return harness
    return None


def classify_model(stem: str) -> str | None:
    """Return model label for *stem*, or None if unrecognised."""
    lower = stem.lower()
    for token, model in MODEL_TOKENS:
        if token in lower:
            return model
    return None


def classify_profile(stem: str) -> str | None:
    """Return profile string for *stem* (e.g. 'thinking: high'), or None."""
    m = THINKING_LEVEL_RE.search(stem)
    if m:
        return f"thinking: {m.group(1)}"
    lower = stem.lower()
    for token, label in PROFILE_RULES:
        if token in lower:
            return label
    return None


def classify_suite(stem: str) -> str | None:
    """Map a stem to a suite display name, or None if not a known fork suite."""
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


def resolve_stem(stem: str) -> tuple[str | None, str | None, str | None]:
    """Return (harness, model, reasoning_level) for *stem*.

    reasoning_level is the raw level token ('off'/'low'/'medium'/'high') or
    None when the stem does not contain a reasoning-sweep suffix.
    """
    harness = classify_harness(stem)
    model = classify_model(stem)
    m = THINKING_LEVEL_RE.search(stem)
    level = m.group(1) if m else None
    return harness, model, level


# ---------------------------------------------------------------------------
# Path collection
# ---------------------------------------------------------------------------

def collect_paths(result_dirs: list[Path] | None = None) -> dict[str, Path]:
    """Return ``{filename: Path}`` across *result_dirs* (first dir wins on clash).

    If *result_dirs* is None, the module-level :data:`RESULT_DIRS` is used.
    """
    dirs = result_dirs if result_dirs is not None else RESULT_DIRS
    seen: dict[str, Path] = {}
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            seen.setdefault(path.name, path)
    return seen
