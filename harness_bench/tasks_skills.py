"""Skill-aware tasks for harness-bench-fast.

These tasks ship an Agent Skills folder at `.agents/skills/<name>/SKILL.md`
(the cross-harness standard path; `build_agent` wires SkillsMiddleware in when
present). They measure how well an agent works WITH skills — finds the right
one, reads it, and applies it. Verification stays mechanical (no LLM judge),
offline, and deterministic so it passes the `verify-gold` CI gate.

The carried knowledge is intentionally NON-recoverable: a fictional company's
exact brand tokens that are not in any model's weights, not on the web, and not
introspectable. The agent can only get them by reading the skill.

Not yet registered in `ALL_TASKS` (that needs a TASK_SET_VERSION bump); kept
importable for review and validation runs.
"""

from __future__ import annotations

import re

from harness_bench.core import Task
from harness_bench.verifiers import all_of, file_contains, file_matches_regex

# ---------------------------------------------------------------------------
# R1 — brand-styling under a fictional company's brand guide
# ---------------------------------------------------------------------------

# Fictional company. Tokens are invented => not recoverable without the skill.
_BRAND_SKILL = """\
---
name: halcyon-brand
description: Official brand guidelines for Halcyon Freight — exact colors,
  typography, and styling rules. Use whenever producing any visual artifact
  (web page, slide, document) that should carry the Halcyon Freight look.
---

# Halcyon Freight — Brand Guidelines

Apply these EXACTLY when building any Halcyon Freight artifact.

## Colors

- Primary (Deep Pine): `#0B3D2E` — headings, primary text, primary buttons
- Accent (Ember): `#E0552B` — links, call-to-action, highlights
- Canvas (Bone): `#F4F1EA` — page background

## Typography

- Headings: **Space Grotesk** (with `sans-serif` fallback)
- Body text: **Source Sans 3** (with `sans-serif` fallback)

## Rules

- Use Deep Pine `#0B3D2E` for body text — never pure black `#000000`.
- Page background must be Bone `#F4F1EA`.
- Do not introduce blue tones; the palette is pine + ember only.
"""

_BRAND_GOLD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Halcyon Freight</title>
<style>
  body {
    background: #F4F1EA;
    color: #0B3D2E;
    font-family: "Source Sans 3", sans-serif;
    margin: 0;
    padding: 48px;
  }
  h1 {
    font-family: "Space Grotesk", sans-serif;
    color: #0B3D2E;
  }
  a, .cta { color: #E0552B; }
</style>
</head>
<body>
  <h1>Halcyon Freight</h1>
  <p>Reliable logistics that move with you. <a href="#">Get a quote</a>.</p>
</body>
</html>
"""

R1_BRAND = Task(
    id="skill_r1_brand_landing",
    name="Build a landing page following Halcyon Freight brand guidelines",
    tags=("skill", "brand", "office", "html", "medium"),
    prompt=(
        "Создай в корне рабочей директории файл index.html — простую посадочную"
        " страницу компании Halcyon Freight: один заголовок (h1) с названием"
        " компании и короткий абзац с описанием и ссылкой. Свёрстай страницу"
        " СТРОГО по фирменному стилю Halcyon Freight (цвета, шрифты, правила)."
        " Всё в одном файле, стили инлайн в <style>."
    ),
    setup_files={".agents/skills/halcyon-brand/SKILL.md": _BRAND_SKILL},
    gold_files={"index.html": _BRAND_GOLD_HTML},
    verifier=all_of(
        # фирменные токены — их неоткуда взять, кроме скилла (hex регистронезависимо)
        file_matches_regex("index.html", r"#0b3d2e", flags=re.IGNORECASE),  # Deep Pine
        file_matches_regex("index.html", r"#e0552b", flags=re.IGNORECASE),  # Ember
        file_matches_regex("index.html", r"#f4f1ea", flags=re.IGNORECASE),  # Bone bg
        file_contains("index.html", "Space Grotesk", "Source Sans 3"),       # шрифты
        # это действительно страница, а не заглушка
        file_matches_regex("index.html", r"<h1[ >]", flags=re.IGNORECASE),
    ),
)


SKILL_TASKS = [R1_BRAND]
