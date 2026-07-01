"""Skill-aware tasks for harness-bench-fast.

These tasks ship an Agent Skills folder at `.agents/skills/<name>/SKILL.md`
(the cross-harness standard path; `build_agent` wires SkillsMiddleware in when
present). They measure how well an agent works WITH skills — finds the right
one, reads it, and applies it. Verification stays mechanical (no LLM judge),
offline, and deterministic so it passes the `verify-gold` CI gate.

The carried knowledge is intentionally NON-recoverable: a fictional company's
exact brand tokens that are not in any model's weights, not on the web, and not
introspectable. The agent can only get them by reading the skill.

Registered in `ALL_TASKS` as task_314..task_330 (task-set v0.10.0). The
C1/C1b debugging prototypes remain defined for local controls, but are not part
of `SKILL_TASKS` because no-skill runs showed no skill uplift.
"""

from __future__ import annotations

import csv
import os
import re
import subprocess
import sys

from harness_bench.core import Task, VerifyResult
from harness_bench.verifiers import (
    all_of,
    file_contains,
    file_exists,
    file_matches_regex,
    file_text_equals,
    python_callable_returns,
)

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
    id="task_314_skill_r1_brand_landing",
    name="Build a landing page following Halcyon Freight brand guidelines",
    tags=("skill", "brand", "office", "html", "medium"),
    prompt=(
        "Создай в корне рабочей директории файл index.html — простую посадочную"
        " страницу компании Halcyon Freight: один заголовок (h1) с названием"
        " компании и короткий абзац с описанием и ссылкой. Свёрстай страницу"
        " СТРОГО по фирменному стилю Halcyon Freight (цвета, шрифты, правила)."
        " Всё в одном файле, стили инлайн в <style>."
    ),
    # Ship the skill in BOTH standard discovery dirs so every harness sees it:
    # `.claude/skills` (Claude Code — verified it ignores `.agents/skills`) and
    # `.agents/skills` (codex/openhands; opencode reads either). Same content.
    setup_files={
        ".claude/skills/halcyon-brand/SKILL.md": _BRAND_SKILL,
        ".agents/skills/halcyon-brand/SKILL.md": _BRAND_SKILL,
    },
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


# ---------------------------------------------------------------------------
# B1 — normalize free-text to a fictional company's internal codebook
# ---------------------------------------------------------------------------

# Invented taxonomy: codes, synonyms, and a non-obvious precedence rule for
# ambiguous descriptions. Not recoverable without the skill.
_CODEBOOK_SKILL = """\
---
name: nordwind-failure-codebook
description: Nordwind Mfg internal codebook for normalizing free-text equipment
  failure reasons to canonical codes. Use when mapping failure descriptions to
  Nordwind standard failure codes.
---

# Nordwind Mfg — Failure-Reason Codebook

Map each free-text failure description to exactly one canonical code.

## Canonical codes and their synonyms

- `TH-OVR` (Thermal overload): overheated, ran too hot, thermal trip,
  temperature alarm, overtemperature
- `BRG-WEAR` (Bearing wear): noisy bearing, spindle vibration, rumbling noise,
  play in shaft, worn bearing
- `ELE-FLT` (Electrical fault): short circuit, blown fuse, tripped breaker,
  power surge, earth fault
- `LUB-FAIL` (Lubrication failure): no grease, dry running, oil starvation,
  low lubricant
- `CNT-CONTAM` (Contamination): dust ingress, dirty coolant, swarf in housing,
  particle contamination

## Precedence rules for ambiguous descriptions

Apply in this order (a description matching two categories):

1. If it mentions BOTH an electrical symptom and a thermal symptom, code it
   `ELE-FLT` — at Nordwind electrical is treated as the root cause.
2. If it mentions BOTH a bearing symptom and a lubrication symptom, code it
   `LUB-FAIL` — lubrication failure is the root cause of the bearing damage.

If still ambiguous after the rules, pick the category with the earliest match
in the text.
"""

# Rows the agent sees. Rows 4 & 5 trigger the precedence rules.
_CODEBOOK_INPUT = (
    "id,free_text\n"
    "1,Motor overheated during a long production run\n"
    "2,Loud rumbling noise coming from the spindle\n"
    "3,Blown fuse on the main control panel\n"
    "4,Bearing seized up and no grease was found inside\n"
    "5,Spindle overheated and tripped the breaker\n"
    "6,Dust ingress into the gearbox housing\n"
    "7,Dry running damaged the pump\n"
    "8,Temperature alarm triggered an emergency shutdown\n"
)

# Hidden gold codes (the agent never sees these).
_CODEBOOK_GOLD = {
    "1": "TH-OVR",
    "2": "BRG-WEAR",
    "3": "ELE-FLT",
    "4": "LUB-FAIL",     # bearing + lubrication -> LUB-FAIL (rule 2)
    "5": "ELE-FLT",      # thermal + electrical -> ELE-FLT (rule 1)
    "6": "CNT-CONTAM",
    "7": "LUB-FAIL",
    "8": "TH-OVR",
}

_CODEBOOK_GOLD_CSV = "id,code\n" + "".join(
    f"{k},{v}\n" for k, v in _CODEBOOK_GOLD.items()
)


def _codebook_check(ws) -> VerifyResult:
    p = ws / "normalized.csv"
    if not p.exists():
        return VerifyResult(False, "normalized.csv missing")
    got: dict[str, str] = {}
    try:
        with p.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                norm = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                rid, code = norm.get("id"), norm.get("code")
                if rid is None or code is None:
                    return VerifyResult(False, "normalized.csv must have columns id,code")
                got[rid] = code.upper()
    except (csv.Error, UnicodeDecodeError) as exc:
        return VerifyResult(False, f"could not read normalized.csv: {exc}")
    wrong = [f"{k}={got.get(k)!r}≠{v}" for k, v in _CODEBOOK_GOLD.items() if got.get(k) != v]
    if wrong:
        return VerifyResult(False, "wrong codes: " + "; ".join(wrong[:6]))
    return VerifyResult(True, "all 8 failure reasons normalized to the correct codes")


B1_CODEBOOK = Task(
    id="task_315_skill_b1_failure_codebook",
    name="Normalize failure reasons to the Nordwind internal codebook",
    tags=("skill", "data-cleaning", "codebook", "medium"),
    prompt=(
        "В рабочей директории есть файл failures.csv (колонки id, free_text) —"
        " вольные описания отказов оборудования. Приведи каждое описание к"
        " каноническому коду отказа по внутреннему кодбуку компании Nordwind Mfg"
        " и запиши результат в normalized.csv с колонками id,code (по одной"
        " строке на каждую запись из failures.csv). Учитывай правила приоритета"
        " для неоднозначных описаний."
    ),
    setup_files={
        "failures.csv": _CODEBOOK_INPUT,
        ".claude/skills/nordwind-failure-codebook/SKILL.md": _CODEBOOK_SKILL,
        ".agents/skills/nordwind-failure-codebook/SKILL.md": _CODEBOOK_SKILL,
    },
    gold_files={"normalized.csv": _CODEBOOK_GOLD_CSV},
    verifier=_codebook_check,
)


# ---------------------------------------------------------------------------
# B3 — policy as a decision function (fictional insurer, precedence rules)
# ---------------------------------------------------------------------------

# Invented thresholds + a strict precedence order. Not recoverable: the agent
# must read the skill to get the numbers AND the order they apply in.
_POLICY_SKILL = """\
---
name: helios-claim-triage
description: Helios Insurance auto-triage policy for motor claims. Use to decide
  whether a claim is auto-approved, sent to manual review, or auto-rejected.
---

# Helios Insurance — Motor Claim Auto-Triage Policy

You receive one claim as a dict with these keys:

- `claim_amount` (int, RUB)
- `incident_type` (str): one of `collision`, `theft`, `flood`, `vandalism`
- `policy_age_days` (int): days since the policy started
- `prior_claims` (int): number of previous claims on this policy
- `documentation_complete` (bool)

Return exactly one decision string: `AUTO_APPROVE`, `MANUAL_REVIEW`, or
`AUTO_REJECT`.

## Rules — apply STRICTLY in this order (1 wins over 2, etc.)

1. If `documentation_complete` is false → `MANUAL_REVIEW` (nothing else matters
   until paperwork is complete).
2. Else if `incident_type` is `flood` → `AUTO_REJECT` (flood is not covered).
3. Else if `policy_age_days` < 30 → `MANUAL_REVIEW` (new-policy fraud check).
4. Else if `claim_amount` <= 1000 and `prior_claims` == 0 → `AUTO_APPROVE`.
5. Else if `claim_amount` <= 5000 and `prior_claims` <= 2 → `AUTO_APPROVE`.
6. Else → `MANUAL_REVIEW`.
"""

_POLICY_GOLD = '''\
def decide(case: dict) -> str:
    if not case.get("documentation_complete", False):
        return "MANUAL_REVIEW"
    if case["incident_type"] == "flood":
        return "AUTO_REJECT"
    if case["policy_age_days"] < 30:
        return "MANUAL_REVIEW"
    if case["claim_amount"] <= 1000 and case["prior_claims"] == 0:
        return "AUTO_APPROVE"
    if case["claim_amount"] <= 5000 and case["prior_claims"] <= 2:
        return "AUTO_APPROVE"
    return "MANUAL_REVIEW"
'''

# Held-out cases the agent never sees. Cases 4-8 probe the precedence order.
_POLICY_CASES = [
    ({"claim_amount": 500, "incident_type": "collision", "policy_age_days": 200, "prior_claims": 0, "documentation_complete": True}, "AUTO_APPROVE"),
    ({"claim_amount": 3000, "incident_type": "theft", "policy_age_days": 400, "prior_claims": 1, "documentation_complete": True}, "AUTO_APPROVE"),
    ({"claim_amount": 8000, "incident_type": "collision", "policy_age_days": 400, "prior_claims": 0, "documentation_complete": True}, "MANUAL_REVIEW"),
    ({"claim_amount": 500, "incident_type": "flood", "policy_age_days": 400, "prior_claims": 0, "documentation_complete": True}, "AUTO_REJECT"),       # flood overrides auto-approve
    ({"claim_amount": 8000, "incident_type": "flood", "policy_age_days": 400, "prior_claims": 3, "documentation_complete": True}, "AUTO_REJECT"),      # flood overrides manual review
    ({"claim_amount": 500, "incident_type": "collision", "policy_age_days": 400, "prior_claims": 0, "documentation_complete": False}, "MANUAL_REVIEW"),# doc overrides auto-approve
    ({"claim_amount": 500, "incident_type": "collision", "policy_age_days": 10, "prior_claims": 0, "documentation_complete": True}, "MANUAL_REVIEW"),  # new policy overrides auto-approve
    ({"claim_amount": 200, "incident_type": "flood", "policy_age_days": 10, "prior_claims": 0, "documentation_complete": False}, "MANUAL_REVIEW"),     # doc (rule 1) overrides flood (rule 2)
    ({"claim_amount": 3000, "incident_type": "theft", "policy_age_days": 400, "prior_claims": 5, "documentation_complete": True}, "MANUAL_REVIEW"),
    ({"claim_amount": 1000, "incident_type": "vandalism", "policy_age_days": 400, "prior_claims": 0, "documentation_complete": True}, "AUTO_APPROVE"),
]


def _policy_check(ws) -> VerifyResult:
    sol = ws / "solution.py"
    if not sol.exists():
        return VerifyResult(False, "solution.py missing")
    import importlib.util as _ilu

    for name in [m for m in list(sys.modules) if m == "solution"]:
        del sys.modules[name]
    spec = _ilu.spec_from_file_location("solution", sol)
    mod = _ilu.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001
        return VerifyResult(False, f"solution.py failed to import: {type(exc).__name__}: {exc}")
    if not hasattr(mod, "decide"):
        return VerifyResult(False, "function decide(case) not defined")
    wrong = []
    for case, expected in _POLICY_CASES:
        try:
            got = mod.decide(dict(case))
        except Exception as exc:  # noqa: BLE001
            return VerifyResult(False, f"decide() raised on a case: {type(exc).__name__}: {exc}")
        if got != expected:
            wrong.append(f"{case['incident_type']}/{case['claim_amount']}/doc={case['documentation_complete']}: {got!r}≠{expected}")
    if wrong:
        return VerifyResult(False, "wrong decisions: " + "; ".join(wrong[:4]))
    return VerifyResult(True, f"all {len(_POLICY_CASES)} triage decisions correct (incl. precedence)")


B3_POLICY = Task(
    id="task_316_skill_b3_claim_triage_policy",
    name="Implement Helios claim-triage decision function from policy skill",
    tags=("skill", "domain-procedure", "policy", "medium"),
    prompt=(
        "Создай в корне рабочей директории файл solution.py с функцией"
        " decide(case: dict) -> str, которая классифицирует страховую заявку"
        " согласно политике авто-триажа компании Helios Insurance. На вход —"
        " словарь с ключами claim_amount, incident_type, policy_age_days,"
        " prior_claims, documentation_complete. Верни ровно одну из строк:"
        " AUTO_APPROVE, MANUAL_REVIEW, AUTO_REJECT. Строго соблюдай порядок"
        " приоритета правил из политики."
    ),
    setup_files={
        ".claude/skills/helios-claim-triage/SKILL.md": _POLICY_SKILL,
        ".agents/skills/helios-claim-triage/SKILL.md": _POLICY_SKILL,
    },
    gold_files={"solution.py": _POLICY_GOLD},
    verifier=_policy_check,
)


# ---------------------------------------------------------------------------
# G2 — repair a broken code-skill (the skill is the deliverable)
# ---------------------------------------------------------------------------

_PHONE_SKILL_MD = """\
---
name: phone-normalizer
description: Normalize messy phone numbers to E.164 (+<country><number>).
allowed-tools: Read Edit
---

# Phone Normalizer

`scripts/normalize.py` exposes `normalize_phone(s: str) -> str`.

Specification:
- Strip all non-digit characters.
- If the input begins with `+`, keep its digits as-is: `+<digits>`.
- Else if there are 11 digits starting with `1`, it's US with country code: `+<digits>`.
- Else if there are exactly 10 digits, assume US: `+1<digits>`.
- Else prepend `+` to the digits.
"""

# Broken: always prepends +1, ignores a leading + / country code.
_PHONE_BUGGY = '''\
import re


def normalize_phone(s: str) -> str:
    digits = re.sub(r"\\D", "", s)
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+1" + digits
'''

_PHONE_GOLD = '''\
import re


def normalize_phone(s: str) -> str:
    s = s.strip()
    has_plus = s.startswith("+")
    digits = re.sub(r"\\D", "", s)
    if has_plus:
        return "+" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    return "+" + digits
'''

_PHONE_SCRIPT = "skills/phone-normalizer/scripts/normalize.py"

G2_SKILL_REPAIR = Task(
    id="task_317_skill_g2_repair_phone_skill",
    name="Repair the phone-normalizer skill's script (E.164, international)",
    tags=("skill", "edit", "code-skill", "medium"),
    prompt=(
        "В каталоге skills/phone-normalizer лежит скилл нормализации телефонных"
        " номеров к формату E.164. Его скрипт scripts/normalize.py работает"
        " неверно для международных номеров (с явным кодом страны или ведущим"
        " '+'): он всегда подставляет код США. Почини scripts/normalize.py"
        " строго по спецификации из SKILL.md. Сигнатуру функции не меняй."
    ),
    setup_files={
        "skills/phone-normalizer/SKILL.md": _PHONE_SKILL_MD,
        _PHONE_SCRIPT: _PHONE_BUGGY,
    },
    gold_files={_PHONE_SCRIPT: _PHONE_GOLD},
    verifier=all_of(
        # форма: фронтматтер скилла остался валиден
        file_matches_regex("skills/phone-normalizer/SKILL.md", r"^name:\s*phone-normalizer\s*$"),
        # функция: на скрытых входах (включая международные)
        python_callable_returns(_PHONE_SCRIPT, "mod.normalize_phone('(415) 555-2671')", "+14155552671"),
        python_callable_returns(_PHONE_SCRIPT, "mod.normalize_phone('1-415-555-2671')", "+14155552671"),
        python_callable_returns(_PHONE_SCRIPT, "mod.normalize_phone('+1 (415) 555-2671')", "+14155552671"),
        python_callable_returns(_PHONE_SCRIPT, "mod.normalize_phone('+44 20 7946 0958')", "+442079460958"),
        python_callable_returns(_PHONE_SCRIPT, "mod.normalize_phone('+49-30-901820')", "+4930901820"),
    ),
)


# ---------------------------------------------------------------------------
# R2 — format a report to a fictional company's style guide (exact conventions)
# ---------------------------------------------------------------------------

_STYLE_SKILL = """\
---
name: vortex-style-guide
description: Vortex Corp document style guide — exact date, money, heading, and
  footer conventions. Use when formatting any Vortex Corp report or document.
---

# Vortex Corp — Document Style Guide

Apply these conventions EXACTLY.

- **Dates**: `YYYY.MM.DD` (dot-separated). Example: `2026.03.14`.
- **Money**: digits grouped in threes with an apostrophe, suffixed ` USD`.
  Example: `1'234'567 USD`.
- **Section title**: rendered in ALL CAPS on its own first line.
- **Mandatory footer** (exact, last line): `— Vortex Corp · Confidential`
"""

_STYLE_INPUT = "title: quarterly results\ndate: 2026-03-14\nrevenue: 1234567\n"

_STYLE_GOLD = (
    "QUARTERLY RESULTS\n"
    "Date: 2026.03.14\n"
    "Revenue: 1'234'567 USD\n"
    "\n"
    "— Vortex Corp · Confidential\n"
)

R2_STYLE = Task(
    id="task_318_skill_r2_style_guide_report",
    name="Format a report to the Vortex Corp style guide",
    tags=("skill", "office", "formatting", "medium"),
    prompt=(
        "В рабочей директории есть draft.txt с полями title, date, revenue."
        " Свёрстай из него report.txt строго по фирменному style-guide компании"
        " Vortex Corp: заголовок секции, формат даты, формат денежной суммы и"
        " обязательный футер — всё ровно по правилам гайда."
    ),
    setup_files={
        "draft.txt": _STYLE_INPUT,
        ".claude/skills/vortex-style-guide/SKILL.md": _STYLE_SKILL,
        ".agents/skills/vortex-style-guide/SKILL.md": _STYLE_SKILL,
    },
    gold_files={"report.txt": _STYLE_GOLD},
    verifier=all_of(
        # style rule: a section title rendered in ALL CAPS on its own line
        # (content-agnostic — the skill is the formatting, not which title)
        file_matches_regex("report.txt", r"^[A-Z][A-Z ]+$", flags=re.MULTILINE),
        # the non-recoverable Vortex conventions (the actual discriminators)
        file_contains("report.txt", "2026.03.14"),
        file_contains("report.txt", "1'234'567 USD"),
        file_contains("report.txt", "— Vortex Corp · Confidential"),
    ),
)


# ---------------------------------------------------------------------------
# B2 — parse a bespoke fixed-width format with an invented sign-overpunch
# ---------------------------------------------------------------------------

_NDR7_SKILL = """\
---
name: ndr7-format
description: Parser spec for the Nordwind NDR-7 fixed record format with
  sign-overpunch on the amount. Use when reading NDR-7 records.
---

# Nordwind NDR-7 Record Format

Each record is one line, no separators:

- chars 1-3: record id (3 digits)
- chars 4..end: amount field

The amount field's LAST character is a **sign-overpunch letter** that encodes
the final digit AND the sign. The characters before it are literal digits.

Overpunch letters:

- `A`..`J` → final digit `0`..`9`, **positive**
- `K`..`T` → final digit `0`..`9`, **negative**

Example: `00112C` → id `001`, amount digits `12` + overpunch `C` (=digit 2,
positive) → `+122`. `00245M` → id `002`, `45` + `M` (=digit 2, negative) →
`-452`.
"""

_NDR7_INPUT = "00112C\n00245M\n00307A\n01199T\n"

# id -> signed amount
_NDR7_GOLD = {"001": 122, "002": -452, "003": 70, "011": -999}
_NDR7_GOLD_CSV = "id,amount\n" + "".join(f"{k},{v}\n" for k, v in _NDR7_GOLD.items())


def _ndr7_check(ws) -> VerifyResult:
    p = ws / "parsed.csv"
    if not p.exists():
        return VerifyResult(False, "parsed.csv missing")
    got: dict[int, int] = {}
    try:
        with p.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                norm = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                rid, amt = norm.get("id"), norm.get("amount")
                if rid is None or amt is None:
                    return VerifyResult(False, "parsed.csv must have columns id,amount")
                got[int(rid)] = int(amt)  # normalize ids (leading zeros) by int
    except (csv.Error, ValueError, UnicodeDecodeError) as exc:
        return VerifyResult(False, f"could not read parsed.csv: {exc}")
    exp = {int(k): v for k, v in _NDR7_GOLD.items()}
    wrong = [f"{k}={got.get(k)}≠{v}" for k, v in exp.items() if got.get(k) != v]
    if wrong:
        return VerifyResult(False, "wrong amounts: " + "; ".join(wrong))
    return VerifyResult(True, "all NDR-7 records parsed with correct signed amounts")


B2_NDR7 = Task(
    id="task_319_skill_b2_ndr7_parse",
    name="Parse Nordwind NDR-7 records with sign-overpunch",
    tags=("skill", "file-format", "parsing", "medium"),
    prompt=(
        "В рабочей директории есть records.txt — записи во внутреннем формате"
        " Nordwind NDR-7. Распарси их согласно спецификации формата и запиши"
        " результат в parsed.csv с колонками id,amount (amount — целое число со"
        " знаком). По одной строке на запись."
    ),
    setup_files={
        "records.txt": _NDR7_INPUT,
        ".claude/skills/ndr7-format/SKILL.md": _NDR7_SKILL,
        ".agents/skills/ndr7-format/SKILL.md": _NDR7_SKILL,
    },
    gold_files={"parsed.csv": _NDR7_GOLD_CSV},
    verifier=_ndr7_check,
)


# ---------------------------------------------------------------------------
# E1 — B1 with irrelevant distractor skills present (robustness/selection)
# ---------------------------------------------------------------------------

_DISTRACTOR_WEATHER = """\
---
name: weather-forecast-api
description: Fetch and format weather forecasts from the SkyCast API. Use when
  the user asks about weather, temperature, or forecasts.
---

# SkyCast Weather

Call `GET /v1/forecast?city=...` and format the daily highs and lows.
"""

_DISTRACTOR_COLORS = """\
---
name: palette-picker
description: Generate accessible color palettes for UI design. Use when choosing
  colors, contrast ratios, or theme tokens for an interface.
---

# Palette Picker

Pick WCAG-AA color pairs and emit hex tokens for background/foreground.
"""

E1_DISTRACTOR = Task(
    id="task_320_skill_e1_codebook_with_distractors",
    name="Normalize failure reasons with irrelevant skills also present",
    tags=("skill", "data-cleaning", "distractor", "medium"),
    prompt=B1_CODEBOOK.prompt,
    setup_files={
        "failures.csv": _CODEBOOK_INPUT,
        # релевантный скилл
        ".claude/skills/nordwind-failure-codebook/SKILL.md": _CODEBOOK_SKILL,
        ".agents/skills/nordwind-failure-codebook/SKILL.md": _CODEBOOK_SKILL,
        # дистракторы — должны быть проигнорированы
        ".claude/skills/weather-forecast-api/SKILL.md": _DISTRACTOR_WEATHER,
        ".agents/skills/weather-forecast-api/SKILL.md": _DISTRACTOR_WEATHER,
        ".claude/skills/palette-picker/SKILL.md": _DISTRACTOR_COLORS,
        ".agents/skills/palette-picker/SKILL.md": _DISTRACTOR_COLORS,
    },
    gold_files={"normalized.csv": _CODEBOOK_GOLD_CSV},
    verifier=_codebook_check,
)


# ---------------------------------------------------------------------------
# E2 — negative control: a misleading skill that must NOT be applied
# ---------------------------------------------------------------------------

_E2_DATA = (
    "item,amount\nwidget,120\ngadget,80\nbolt,250\nnut,75\ngear,1000\n"
    "pin,45\nclip,320\ncap,90\nrod,660\nscrew,15\n"
)  # plain sum = 2655

_E2_ROUNDING_SKILL = """\
---
name: acme-invoice-rounding
description: Acme Corp invoicing policy — how to round line items on Acme
  customer invoices before billing. Use when preparing an Acme invoice.
---

# Acme Invoice Rounding

When preparing an **Acme customer invoice**, always round each line-item amount
UP to the nearest 100 before summing. (This applies to Acme invoices only.)
"""

E2_NEG_CONTROL = Task(
    id="task_321_skill_e2_negative_control_sum",
    name="Plain column sum with a tempting-but-irrelevant rounding skill present",
    tags=("skill", "negative-control", "axis", "easy"),
    prompt=(
        "В рабочей директории есть data.csv (колонки item, amount). Посчитай"
        " сумму столбца amount и запиши её одной строкой (целое число) в файл"
        " total.txt."
    ),
    setup_files={
        "data.csv": _E2_DATA,
        ".claude/skills/acme-invoice-rounding/SKILL.md": _E2_ROUNDING_SKILL,
        ".agents/skills/acme-invoice-rounding/SKILL.md": _E2_ROUNDING_SKILL,
    },
    gold_files={"total.txt": "2655\n"},
    # correct answer is the plain sum; applying the rounding skill yields 3100
    verifier=file_text_equals("total.txt", "2655"),
)


# ---------------------------------------------------------------------------
# E3 — skill selection: 3 codebooks, only the matching company's is correct
# ---------------------------------------------------------------------------

_ACME_CODEBOOK = """\
---
name: acme-failure-codebook
description: Acme Corp codebook for normalizing equipment failure reasons to
  Acme failure codes.
---

# Acme Corp — Failure Codebook

- `THERMAL`: overheated, thermal trip, temperature alarm
- `BEARING`: rumbling noise, spindle vibration, worn bearing
- `ELECTRICAL`: blown fuse, tripped breaker, short circuit
- `LUBE`: no grease, dry running, oil starvation
- `CONTAM`: dust ingress, dirty coolant
"""

_GLOBEX_CODEBOOK = """\
---
name: globex-failure-codebook
description: Globex Industries codebook for normalizing equipment failure
  reasons to Globex failure codes.
---

# Globex Industries — Failure Codebook

- `FX-01`: overheated, thermal trip, temperature alarm
- `FX-02`: rumbling noise, spindle vibration, worn bearing
- `FX-03`: blown fuse, tripped breaker, short circuit
- `FX-04`: no grease, dry running, oil starvation
- `FX-05`: dust ingress, dirty coolant
"""

E3_SELECTION = Task(
    id="task_322_skill_e3_codebook_selection",
    name="Pick the Nordwind codebook among several companies' codebooks",
    tags=("skill", "selection", "axis", "medium"),
    prompt=B1_CODEBOOK.prompt,  # explicitly references Nordwind Mfg
    setup_files={
        "failures.csv": _CODEBOOK_INPUT,
        ".claude/skills/nordwind-failure-codebook/SKILL.md": _CODEBOOK_SKILL,
        ".agents/skills/nordwind-failure-codebook/SKILL.md": _CODEBOOK_SKILL,
        ".claude/skills/acme-failure-codebook/SKILL.md": _ACME_CODEBOOK,
        ".agents/skills/acme-failure-codebook/SKILL.md": _ACME_CODEBOOK,
        ".claude/skills/globex-failure-codebook/SKILL.md": _GLOBEX_CODEBOOK,
        ".agents/skills/globex-failure-codebook/SKILL.md": _GLOBEX_CODEBOOK,
    },
    gold_files={"normalized.csv": _CODEBOOK_GOLD_CSV},  # Nordwind codes
    verifier=_codebook_check,  # only Nordwind codes pass
)


# ---------------------------------------------------------------------------
# G1 — create a code-skill following a bespoke skill-authoring standard
# ---------------------------------------------------------------------------

_AUTHORING_STANDARD = """\
---
name: acme-skill-standard
description: Acme's internal standard for authoring agent skills. Use whenever
  creating a new skill inside an Acme repository.
---

# Acme Skill-Authoring Standard

Every skill created at Acme MUST satisfy ALL of the following:

1. The skill folder name MUST equal the frontmatter `name`.
2. The frontmatter MUST include `metadata.review-status` set to `draft`.
3. The skill folder MUST contain a `TESTS.md` file with at least one
   worked input → output example.
4. Executable code goes under `scripts/`.
"""

_SLUG_GOLD_SKILL_MD = """\
---
name: slugify-tool
description: Turn arbitrary text into URL-safe slugs.
metadata:
  review-status: draft
---

# Slugify Tool

`scripts/slugify.py` exposes `slugify(text: str) -> str`.
"""

_SLUG_GOLD_TESTS = "# Tests\n\n- `slugify('Hello World')` -> `hello-world`\n"

_SLUG_GOLD_PY = '''\
import re


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\\s+", "-", text)
    text = re.sub(r"[^a-z0-9-]", "", text)
    return text.strip("-")
'''

_SLUG_SKILL = "skills/slugify-tool"

G1_CREATE_SKILL = Task(
    id="task_323_skill_g1_create_slugify_skill",
    name="Create a slugify skill following the Acme authoring standard",
    tags=("skill", "create", "code-skill", "medium"),
    prompt=(
        "Создай новый скилл в каталоге skills/slugify-tool, который умеет"
        " приводить произвольный текст к URL-безопасному slug. Скрипт"
        " scripts/slugify.py должен экспортировать функцию slugify(text: str)"
        " -> str: привести к нижнему регистру, заменить любые пробелы на дефис,"
        " удалить все символы кроме латиницы a-z, цифр 0-9 и дефиса, и убрать"
        " дефисы по краям. Оформи скилл строго по внутреннему стандарту"
        " авторинга скиллов Acme."
    ),
    setup_files={
        ".claude/skills/acme-skill-standard/SKILL.md": _AUTHORING_STANDARD,
        ".agents/skills/acme-skill-standard/SKILL.md": _AUTHORING_STANDARD,
    },
    gold_files={
        f"{_SLUG_SKILL}/SKILL.md": _SLUG_GOLD_SKILL_MD,
        f"{_SLUG_SKILL}/TESTS.md": _SLUG_GOLD_TESTS,
        f"{_SLUG_SKILL}/scripts/slugify.py": _SLUG_GOLD_PY,
    },
    verifier=all_of(
        # форма + бэспоук-стандарт авторинга (неискомо без скилла-стандарта)
        file_matches_regex(f"{_SLUG_SKILL}/SKILL.md", r"^name:\s*slugify-tool\s*$"),
        file_matches_regex(f"{_SLUG_SKILL}/SKILL.md", r"review-status:\s*draft"),
        file_exists(f"{_SLUG_SKILL}/TESTS.md"),
        # функция на скрытых входах
        python_callable_returns(f"{_SLUG_SKILL}/scripts/slugify.py", "mod.slugify('Hello, World!')", "hello-world"),
        python_callable_returns(f"{_SLUG_SKILL}/scripts/slugify.py", "mod.slugify('Foo  Bar Baz')", "foo-bar-baz"),
    ),
)

# ---------------------------------------------------------------------------
# A1 — fictional DSL/config language: Lumen Recipe DSL
# ---------------------------------------------------------------------------

_LUMEN_SKILL = """\
---
name: lumen-recipe-dsl
description: Specification for the fictional Lumen Recipe DSL (LQ1). Use when
  creating .lq recipe files for Lumen manifest calculations.
---

# Lumen Recipe DSL — LQ1

A valid recipe file is named `recipe.lq` and has exactly these command forms,
one per line:

1. `BEGIN LQ1`
2. `LOAD start=<integer>`
3. `APPLY routine=<routine-name>`
4. `SEAL token=<seal-name>`
5. `END`

## Routine table

- `crane`: multiply the loaded start value by 13, then add 5.
- `moth`: multiply by 7, then subtract 4.
- `tide`: add 19, then multiply by 2.

## Seal table

- `amber` emits seal code `S-47K`.
- `cobalt` emits seal code `S-22C`.
- `ivory` emits seal code `S-91I`.

The interpreter output is a manifest with `score=<number>` and `seal=<code>`.
"""

_LUMEN_REQUEST = "start=7\nroutine=crane\nseal=amber\n"
_LUMEN_GOLD = "BEGIN LQ1\nLOAD start=7\nAPPLY routine=crane\nSEAL token=amber\nEND\n"


def _lumen_interpret(text: str) -> tuple[int, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 5 or lines[0] != "BEGIN LQ1" or lines[-1] != "END":
        raise ValueError("recipe.lq must have BEGIN LQ1, three commands, END")
    if not lines[1].startswith("LOAD start="):
        raise ValueError("missing LOAD start=<integer>")
    start = int(lines[1].split("=", 1)[1])
    if not lines[2].startswith("APPLY routine="):
        raise ValueError("missing APPLY routine=<name>")
    routine = lines[2].split("=", 1)[1]
    if not lines[3].startswith("SEAL token="):
        raise ValueError("missing SEAL token=<name>")
    seal_name = lines[3].split("=", 1)[1]
    if routine == "crane":
        score = start * 13 + 5
    elif routine == "moth":
        score = start * 7 - 4
    elif routine == "tide":
        score = (start + 19) * 2
    else:
        raise ValueError(f"unknown routine {routine!r}")
    seals = {"amber": "S-47K", "cobalt": "S-22C", "ivory": "S-91I"}
    if seal_name not in seals:
        raise ValueError(f"unknown seal {seal_name!r}")
    return score, seals[seal_name]


def _lumen_check(ws) -> VerifyResult:
    p = ws / "recipe.lq"
    if not p.exists():
        return VerifyResult(False, "recipe.lq missing")
    try:
        score, seal = _lumen_interpret(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return VerifyResult(False, f"recipe.lq invalid: {type(exc).__name__}: {exc}")
    if (score, seal) != (96, "S-47K"):
        return VerifyResult(False, f"wrong manifest: score={score}, seal={seal}")
    return VerifyResult(True, "LQ1 recipe produces score=96 and seal=S-47K")


A1_LUMEN_DSL = Task(
    id="task_324_skill_a1_lumen_recipe_dsl",
    name="Write a Lumen Recipe DSL program from the fictional LQ1 spec",
    tags=("skill", "dsl", "file-format", "medium"),
    prompt=(
        "В рабочей директории есть request.txt с параметрами Lumen manifest. "
        "Создай файл recipe.lq на выдуманном языке Lumen Recipe DSL версии LQ1, "
        "который реализует этот запрос. Синтаксис, routine table и seal table "
        "бери строго из скилла Lumen Recipe DSL."
    ),
    setup_files={
        "request.txt": _LUMEN_REQUEST,
        ".claude/skills/lumen-recipe-dsl/SKILL.md": _LUMEN_SKILL,
        ".agents/skills/lumen-recipe-dsl/SKILL.md": _LUMEN_SKILL,
    },
    gold_files={"recipe.lq": _LUMEN_GOLD},
    verifier=_lumen_check,
)


# ---------------------------------------------------------------------------
# A2 — fictional binary protocol: Q9 frames
# ---------------------------------------------------------------------------

_Q9_SKILL = """\
---
name: q9-binary-protocol
description: Encoder/decoder specification for Quasar Q9 binary frames. Use
  when implementing Q9 frame codecs.
---

# Quasar Q9 Binary Frame Protocol

Implement two Python functions in `codec.py`:

- `encode_message(kind: str, seq: int, payload: bytes) -> bytes`
- `decode_message(frame: bytes) -> dict`

## Kind codes

- `PING` -> `0x10`
- `DATA` -> `0x20`
- `HALT` -> `0x7F`

## Frame layout

```
byte 0      magic `0x51` (`Q`)
byte 1      magic `0x39` (`9`)
byte 2      encoded sequence: `seq XOR 0xA5` (seq is 0..255)
byte 3      kind code
byte 4      payload length in bytes
bytes 5..N  payload bytes in REVERSE order
last byte   checksum
```

Checksum is `(encoded_seq + kind_code + length + sum(reversed_payload)) % 256`,
then XOR with `0x5A`.

The decoder must validate magic and checksum, undo the payload reversal, and
return `{"kind": <kind string>, "seq": <int>, "payload": <bytes>}`.
"""

_Q9_GOLD = '''\
_KIND_TO_CODE = {"PING": 0x10, "DATA": 0x20, "HALT": 0x7F}
_CODE_TO_KIND = {v: k for k, v in _KIND_TO_CODE.items()}


def _checksum(encoded_seq: int, kind_code: int, payload_rev: bytes) -> int:
    return ((encoded_seq + kind_code + len(payload_rev) + sum(payload_rev)) % 256) ^ 0x5A


def encode_message(kind: str, seq: int, payload: bytes) -> bytes:
    kind_code = _KIND_TO_CODE[kind]
    encoded_seq = seq ^ 0xA5
    payload_rev = bytes(payload[::-1])
    chk = _checksum(encoded_seq, kind_code, payload_rev)
    return bytes([0x51, 0x39, encoded_seq, kind_code, len(payload_rev)]) + payload_rev + bytes([chk])


def decode_message(frame: bytes) -> dict:
    if len(frame) < 6 or frame[:2] != b"Q9":
        raise ValueError("bad magic")
    encoded_seq, kind_code, length = frame[2], frame[3], frame[4]
    payload_rev = frame[5:-1]
    if len(payload_rev) != length:
        raise ValueError("bad length")
    if frame[-1] != _checksum(encoded_seq, kind_code, payload_rev):
        raise ValueError("bad checksum")
    return {"kind": _CODE_TO_KIND[kind_code], "seq": encoded_seq ^ 0xA5, "payload": bytes(payload_rev[::-1])}
'''


def _q9_reference_encode(kind: str, seq: int, payload: bytes) -> bytes:
    kind_code = {"PING": 0x10, "DATA": 0x20, "HALT": 0x7F}[kind]
    encoded_seq = seq ^ 0xA5
    payload_rev = payload[::-1]
    chk = ((encoded_seq + kind_code + len(payload_rev) + sum(payload_rev)) % 256) ^ 0x5A
    return bytes([0x51, 0x39, encoded_seq, kind_code, len(payload_rev)]) + payload_rev + bytes([chk])


def _q9_check(ws) -> VerifyResult:
    sol = ws / "codec.py"
    if not sol.exists():
        return VerifyResult(False, "codec.py missing")
    import importlib.util as _ilu

    sys.modules.pop("codec", None)
    spec = _ilu.spec_from_file_location("codec", sol)
    mod = _ilu.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001
        return VerifyResult(False, f"codec.py failed to import: {type(exc).__name__}: {exc}")
    for name in ("encode_message", "decode_message"):
        if not hasattr(mod, name):
            return VerifyResult(False, f"{name} missing")
    cases = [("PING", 0, b""), ("DATA", 42, b"abc"), ("HALT", 255, b"stop"), ("DATA", 7, bytes([1, 2, 250]))]
    wrong = []
    for kind, seq, payload in cases:
        exp = _q9_reference_encode(kind, seq, payload)
        try:
            got = mod.encode_message(kind, seq, payload)
            decoded = mod.decode_message(exp)
        except Exception as exc:  # noqa: BLE001
            return VerifyResult(False, f"codec raised on {kind}/{seq}: {type(exc).__name__}: {exc}")
        if got != exp:
            wrong.append(f"encode {kind}/{seq}: {got!r} != {exp!r}")
        if decoded != {"kind": kind, "seq": seq, "payload": payload}:
            wrong.append(f"decode {kind}/{seq}: {decoded!r}")
    if wrong:
        return VerifyResult(False, "; ".join(wrong[:3]))
    try:
        bad = bytearray(_q9_reference_encode("DATA", 3, b"xy"))
        bad[-1] ^= 1
        mod.decode_message(bytes(bad))
    except Exception:
        pass
    else:
        return VerifyResult(False, "decode_message must reject a bad checksum")
    return VerifyResult(True, "Q9 codec encodes/decodes hidden frames and rejects bad checksum")


A2_Q9_PROTOCOL = Task(
    id="task_325_skill_a2_q9_binary_protocol",
    name="Implement a codec for the fictional Quasar Q9 binary protocol",
    tags=("skill", "binary-protocol", "code", "hard"),
    prompt=(
        "Создай codec.py с функциями encode_message(kind: str, seq: int, payload: bytes) "
        "-> bytes и decode_message(frame: bytes) -> dict для выдуманного бинарного "
        "протокола Quasar Q9. Все байтовые поля, таблицы kind-кодов, reversal и "
        "checksum бери строго из скилла Q9 binary protocol."
    ),
    setup_files={
        ".claude/skills/q9-binary-protocol/SKILL.md": _Q9_SKILL,
        ".agents/skills/q9-binary-protocol/SKILL.md": _Q9_SKILL,
    },
    gold_files={"codec.py": _Q9_GOLD},
    verifier=_q9_check,
)


# ---------------------------------------------------------------------------
# A3 — fake library with a non-obvious protocol quirk
# ---------------------------------------------------------------------------

_QUOKKA_SKILL = """\
---
name: quokka-meter-protocol
description: Internal protocol for using the fictional quokka_meter library.
  Use when measuring Quokka Flux readings.
---

# Quokka Meter Protocol

The local `quokka_meter.Meter` API is quirky. To compute a calibrated flux:

1. Create `Meter()`.
2. Call `meter.arm("solstice")` before reading. Any other token is invalid.
3. Discard exactly one warm-up reading from `read_centi_flux()`.
4. Read the requested number of samples with `read_centi_flux()`.
5. Convert centi-flux to flux by dividing each sample by 100.
6. Return the arithmetic mean of the samples, then apply calibration:
   `mean * 0.82 + 3.0`.
7. Round the final value to 3 decimal places.
"""

_QUOKKA_LIB = '''\
class Meter:
    def __init__(self):
        self._armed = False
        self._i = 0
        self._values = [9999, 1200, 1250, 1300, 1350, 1400, 1450]

    def arm(self, token):
        if token != "solstice":
            raise RuntimeError("bad arm token")
        self._armed = True

    def read_centi_flux(self):
        if not self._armed:
            raise RuntimeError("meter is not armed")
        value = self._values[self._i % len(self._values)]
        self._i += 1
        return value
'''

_QUOKKA_GOLD = '''\
from quokka_meter import Meter


def calibrated_flux(samples: int) -> float:
    meter = Meter()
    meter.arm("solstice")
    meter.read_centi_flux()  # discard warm-up
    vals = [meter.read_centi_flux() / 100 for _ in range(samples)]
    return round((sum(vals) / len(vals)) * 0.82 + 3.0, 3)
'''


def _quokka_expected(samples: int) -> float:
    vals = [1200, 1250, 1300, 1350, 1400, 1450][:samples]
    return round((sum(v / 100 for v in vals) / samples) * 0.82 + 3.0, 3)


def _quokka_check(ws) -> VerifyResult:
    sol = ws / "solution.py"
    if not sol.exists():
        return VerifyResult(False, "solution.py missing")
    import importlib.util as _ilu

    sys.path.insert(0, str(ws))
    try:
        sys.modules.pop("solution", None)
        sys.modules.pop("quokka_meter", None)
        spec = _ilu.spec_from_file_location("solution", sol)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "calibrated_flux"):
            return VerifyResult(False, "calibrated_flux(samples) missing")
        wrong = []
        for samples in (3, 5):
            got = mod.calibrated_flux(samples)
            exp = _quokka_expected(samples)
            if got != exp:
                wrong.append(f"samples={samples}: {got!r} != {exp!r}")
        if wrong:
            return VerifyResult(False, "; ".join(wrong))
    except Exception as exc:  # noqa: BLE001
        return VerifyResult(False, f"solution failed: {type(exc).__name__}: {exc}")
    finally:
        if str(ws) in sys.path:
            sys.path.remove(str(ws))
    return VerifyResult(True, "quokka_meter protocol followed for hidden sample counts")


A3_QUOKKA_LIBRARY = Task(
    id="task_326_skill_a3_quokka_library_quirk",
    name="Use a fictional library with a non-obvious warm-up/calibration protocol",
    tags=("skill", "library-api", "code", "medium"),
    prompt=(
        "В рабочей директории есть локальная библиотека quokka_meter.py. Создай "
        "solution.py с функцией calibrated_flux(samples: int) -> float, которая "
        "считает калиброванный Quokka Flux. Протокол использования библиотеки, "
        "warm-up, единицы и формулу калибровки бери из скилла Quokka Meter Protocol."
    ),
    setup_files={
        "quokka_meter.py": _QUOKKA_LIB,
        ".claude/skills/quokka-meter-protocol/SKILL.md": _QUOKKA_SKILL,
        ".agents/skills/quokka-meter-protocol/SKILL.md": _QUOKKA_SKILL,
    },
    gold_files={"solution.py": _QUOKKA_GOLD},
    verifier=_quokka_check,
)


# ---------------------------------------------------------------------------
# R3 — compliance template with exact blocks and order
# ---------------------------------------------------------------------------

_ACT4_SKILL = """\
---
name: aster-compliance-template
description: Aster Labs ACT-4 compliance notice template. Use when drafting any
  Aster compliance notice.
---

# Aster Labs ACT-4 Compliance Notice Template

The output file must be `notice.md` and must use exactly this block order:

1. First line: `COMPLIANCE NOTICE`
2. `Record ID: <record id>`
3. blank line
4. heading `SCOPE`
5. heading `CONTROL SUMMARY`
6. heading `OWNER`
7. heading `DEADLINE`
8. heading `RETENTION`
9. final line exactly: `This notice follows Aster Compliance Template ACT-4.`

Rules:

- Use the record id from the source facts.
- The RETENTION block must contain exactly `Retention class: R-9 / 7 years`.
- Do not rename headings and do not add extra headings.
"""

_ACT4_FACTS = "record_id=QX-17\nscope=Edge telemetry export\nowner=Mira Chen\ndeadline=2026-07-15\ncontrol=Disable raw-token logging before pilot expansion\n"
_ACT4_GOLD = """\
COMPLIANCE NOTICE
Record ID: QX-17

SCOPE
Edge telemetry export

CONTROL SUMMARY
Disable raw-token logging before pilot expansion

OWNER
Mira Chen

DEADLINE
2026-07-15

RETENTION
Retention class: R-9 / 7 years

This notice follows Aster Compliance Template ACT-4.
"""


def _act4_check(ws) -> VerifyResult:
    p = ws / "notice.md"
    if not p.exists():
        return VerifyResult(False, "notice.md missing")
    text = p.read_text(encoding="utf-8").strip()
    required = [
        "COMPLIANCE NOTICE",
        "Record ID: QX-17",
        "SCOPE",
        "CONTROL SUMMARY",
        "OWNER",
        "DEADLINE",
        "RETENTION",
        "Retention class: R-9 / 7 years",
        "This notice follows Aster Compliance Template ACT-4.",
    ]
    missing = [s for s in required if s not in text]
    if missing:
        return VerifyResult(False, "missing required text: " + "; ".join(missing))
    positions = [text.index(s) for s in required[:7]]
    if positions != sorted(positions):
        return VerifyResult(False, "ACT-4 headings are not in required order")
    if not text.endswith("This notice follows Aster Compliance Template ACT-4."):
        return VerifyResult(False, "mandatory ACT-4 final line missing or not last")
    headings = re.findall(r"^(COMPLIANCE NOTICE|SCOPE|CONTROL SUMMARY|OWNER|DEADLINE|RETENTION|[A-Z][A-Z -]{2,})$", text, flags=re.MULTILINE)
    allowed = ["COMPLIANCE NOTICE", "SCOPE", "CONTROL SUMMARY", "OWNER", "DEADLINE", "RETENTION"]
    extra = [h for h in headings if h not in allowed]
    if extra:
        return VerifyResult(False, "extra heading(s): " + "; ".join(extra))
    return VerifyResult(True, "ACT-4 notice has exact required blocks, order, and final line")


R3_ACT4_TEMPLATE = Task(
    id="task_327_skill_r3_act4_compliance_notice",
    name="Draft an Aster ACT-4 compliance notice with exact block order",
    tags=("skill", "template", "compliance", "medium"),
    prompt=(
        "В рабочей директории есть facts.txt. Создай notice.md — compliance notice "
        "для Aster Labs строго по шаблону ACT-4: точный порядок блоков, record id, "
        "retention rule и обязательная последняя строка должны соответствовать скиллу."
    ),
    setup_files={
        "facts.txt": _ACT4_FACTS,
        ".claude/skills/aster-compliance-template/SKILL.md": _ACT4_SKILL,
        ".agents/skills/aster-compliance-template/SKILL.md": _ACT4_SKILL,
    },
    gold_files={"notice.md": _ACT4_GOLD},
    verifier=_act4_check,
)


# ---------------------------------------------------------------------------
# D2 — bespoke spreadsheet reconciliation rules
# ---------------------------------------------------------------------------

_MERIDIAN_SKILL = """\
---
name: meridian-reconciliation-rules
description: Meridian Finance bespoke spreadsheet reconciliation rules. Use
  when reconciling Meridian ledger exports.
---

# Meridian Ledger Reconciliation Rules

Input files are `ledger_a.xlsx` and `ledger_b.xlsx`, each with columns:
`invoice_id`, `amount_cents`, `currency`, `date`.

Produce `reconciliation.csv` with columns:
`invoice_id,status,matched_id,variance_cents`.

Rules:

1. Normalize invoice IDs by removing hyphens and spaces and uppercasing.
   Example: `INV-001` and `inv001` are the same invoice.
2. Match each row in ledger A to the row in ledger B with the same normalized id
   and same currency.
3. `variance_cents = amount_b - amount_a`.
4. If no B row exists, status is `MISSING`, matched_id is blank, variance is blank.
5. If a B row exists and `abs(variance_cents) <= 2`, status is `MATCH`.
6. If a B row exists but the absolute variance is greater than 2, status is
   `REVIEW`.
7. Output rows in the same order as ledger A.
"""


def _write_meridian_xlsx(path, rows):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "ledger"
    ws.append(["invoice_id", "amount_cents", "currency", "date"])
    for row in rows:
        ws.append(row)
    wb.save(path)


def _meridian_setup(ws) -> None:
    _write_meridian_xlsx(
        ws / "ledger_a.xlsx",
        [
            ["INV-001", 10000, "USD", "2026-06-01"],
            ["INV-002", 7550, "USD", "2026-06-02"],
            ["INV-003", 12000, "EUR", "2026-06-03"],
            ["INV-004", 9999, "USD", "2026-06-04"],
        ],
    )
    _write_meridian_xlsx(
        ws / "ledger_b.xlsx",
        [
            ["inv001", 10001, "USD", "2026-06-01"],
            ["INV002", 7600, "USD", "2026-06-02"],
            ["INV 003", 11998, "EUR", "2026-06-03"],
            ["INV-004", 9999, "EUR", "2026-06-04"],
        ],
    )


_MERIDIAN_GOLD = "invoice_id,status,matched_id,variance_cents\nINV-001,MATCH,inv001,1\nINV-002,REVIEW,INV002,50\nINV-003,MATCH,INV 003,-2\nINV-004,MISSING,,\n"


def _meridian_check(ws) -> VerifyResult:
    p = ws / "reconciliation.csv"
    if not p.exists():
        return VerifyResult(False, "reconciliation.csv missing")
    expected = {
        "INV-001": ("MATCH", "inv001", "1"),
        "INV-002": ("REVIEW", "INV002", "50"),
        "INV-003": ("MATCH", "INV 003", "-2"),
        "INV-004": ("MISSING", "", ""),
    }
    try:
        with p.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except (csv.Error, UnicodeDecodeError) as exc:
        return VerifyResult(False, f"could not read reconciliation.csv: {exc}")
    if [c.strip() for c in (rows[0].keys() if rows else [])] != ["invoice_id", "status", "matched_id", "variance_cents"]:
        return VerifyResult(False, "reconciliation.csv must have columns invoice_id,status,matched_id,variance_cents")
    if [r.get("invoice_id", "").strip() for r in rows] != list(expected):
        return VerifyResult(False, "rows must be in ledger A order")
    wrong = []
    for row in rows:
        inv = row.get("invoice_id", "").strip()
        got = (row.get("status", "").strip().upper(), row.get("matched_id", "").strip(), row.get("variance_cents", "").strip())
        if got != expected.get(inv):
            wrong.append(f"{inv}: {got!r} != {expected.get(inv)!r}")
    if wrong:
        return VerifyResult(False, "; ".join(wrong))
    return VerifyResult(True, "Meridian ledgers reconciled with normalized ids, tolerance, and same-currency rule")


D2_MERIDIAN_RECONCILE = Task(
    id="task_328_skill_d2_meridian_reconcile_xlsx",
    name="Reconcile two xlsx ledgers with Meridian bespoke matching rules",
    tags=("skill", "spreadsheet", "data-cleaning", "hard"),
    prompt=(
        "В рабочей директории есть ledger_a.xlsx и ledger_b.xlsx. По правилам "
        "Meridian Finance из скилла сверни их в reconciliation.csv с колонками "
        "invoice_id,status,matched_id,variance_cents. Соблюдай нормализацию id, "
        "same-currency matching, tolerance и порядок строк ledger A."
    ),
    setup_files={
        ".claude/skills/meridian-reconciliation-rules/SKILL.md": _MERIDIAN_SKILL,
        ".agents/skills/meridian-reconciliation-rules/SKILL.md": _MERIDIAN_SKILL,
    },
    setup_callback=_meridian_setup,
    gold_files={"reconciliation.csv": _MERIDIAN_GOLD},
    verifier=_meridian_check,
)

# ---------------------------------------------------------------------------
# D1 — bespoke scientific/engineering calculation method
# ---------------------------------------------------------------------------

_ARCFLUX_SKILL = """\
---
name: arcflux-exposure-method
description: Valeo ArcFlux AF-3 exposure calculation method. Use when computing
  calibrated exposure from ArcFlux sensor CSV readings.
---

# Valeo ArcFlux AF-3 Exposure Method

Implement `compute_exposure(csv_path: str) -> float` for CSV files with columns:
`minute,raw,dark,temp_c,gain_code`.

For each row:

1. Look up gain multiplier:
   - `L` -> `0.75`
   - `M` -> `1.10`
   - `H` -> `1.60`
2. Compute corrected intensity:
   `intensity = (raw - dark) * gain * (1 - 0.004 * (temp_c - 20))`
3. Integrate intensity over time using the trapezoidal rule over `minute`.
4. Convert integrated intensity to exposure by dividing by `60`.
5. Return the final exposure rounded to 3 decimal places.

Rows may be irregularly spaced in time and are already in chronological order.
"""

_ARCFLUX_INPUT = """\
minute,raw,dark,temp_c,gain_code
0,120,10,20,L
10,150,12,25,M
25,170,11,18,H
40,160,10,21,M
"""

_ARCFLUX_GOLD = '''\
import csv


_GAIN = {"L": 0.75, "M": 1.10, "H": 1.60}


def _intensity(row: dict) -> float:
    raw = float(row["raw"])
    dark = float(row["dark"])
    temp_c = float(row["temp_c"])
    gain = _GAIN[row["gain_code"]]
    return (raw - dark) * gain * (1 - 0.004 * (temp_c - 20))


def compute_exposure(csv_path: str) -> float:
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if len(rows) < 2:
        return 0.0
    points = [(float(row["minute"]), _intensity(row)) for row in rows]
    area = 0.0
    for (t0, y0), (t1, y1) in zip(points, points[1:]):
        area += (t1 - t0) * (y0 + y1) / 2
    return round(area / 60, 3)
'''


def _arcflux_expected(csv_path) -> float:
    gain = {"L": 0.75, "M": 1.10, "H": 1.60}
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    points = []
    for row in rows:
        intensity = (float(row["raw"]) - float(row["dark"])) * gain[row["gain_code"]] * (
            1 - 0.004 * (float(row["temp_c"]) - 20)
        )
        points.append((float(row["minute"]), intensity))
    area = 0.0
    for (t0, y0), (t1, y1) in zip(points, points[1:], strict=False):
        area += (t1 - t0) * (y0 + y1) / 2
    return round(area / 60, 3)


def _arcflux_check(ws) -> VerifyResult:
    sol = ws / "solution.py"
    if not sol.exists():
        return VerifyResult(False, "solution.py missing")
    hidden = ws / "hidden_arcflux.csv"
    hidden.write_text(
        "minute,raw,dark,temp_c,gain_code\n"
        "0,210,14,19,M\n"
        "7,240,15,24,H\n"
        "19,230,13,22,L\n"
        "31,260,16,18,H\n",
        encoding="utf-8",
    )
    import importlib.util as _ilu

    sys.modules.pop("solution", None)
    spec = _ilu.spec_from_file_location("solution", sol)
    mod = _ilu.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001
        return VerifyResult(False, f"solution.py failed to import: {type(exc).__name__}: {exc}")
    if not hasattr(mod, "compute_exposure"):
        return VerifyResult(False, "compute_exposure(csv_path) missing")
    wrong = []
    for rel in ("readings.csv", "hidden_arcflux.csv"):
        path = ws / rel
        try:
            got = mod.compute_exposure(str(path))
        except Exception as exc:  # noqa: BLE001
            return VerifyResult(False, f"compute_exposure raised for {rel}: {type(exc).__name__}: {exc}")
        exp = _arcflux_expected(path)
        if got != exp:
            wrong.append(f"{rel}: {got!r} != {exp!r}")
    if wrong:
        return VerifyResult(False, "; ".join(wrong))
    return VerifyResult(True, "ArcFlux exposure matches trapezoidal AF-3 method on visible and hidden data")


D1_ARCFLUX_EXPOSURE = Task(
    id="task_329_skill_d1_arcflux_exposure",
    name="Compute ArcFlux exposure with a bespoke calibration method",
    tags=("skill", "mathematical-method", "calculation", "medium"),
    prompt=(
        "В рабочей директории есть readings.csv с показаниями ArcFlux AF-3. "
        "Создай solution.py с функцией compute_exposure(csv_path: str) -> float, "
        "которая считает calibrated exposure строго по методу Valeo ArcFlux AF-3 "
        "из скилла: gain table, temperature correction, trapezoidal integration, "
        "conversion и rounding."
    ),
    setup_files={
        "readings.csv": _ARCFLUX_INPUT,
        ".claude/skills/arcflux-exposure-method/SKILL.md": _ARCFLUX_SKILL,
        ".agents/skills/arcflux-exposure-method/SKILL.md": _ARCFLUX_SKILL,
    },
    gold_files={"solution.py": _ARCFLUX_GOLD},
    verifier=_arcflux_check,
)


# ---------------------------------------------------------------------------
# C1 — repair a flaky test by applying a debugging heuristic
# ---------------------------------------------------------------------------

_DET_SELECT_SKILL = """\
---
name: deterministic-tie-debugging
description: Debugging checklist for flaky Python tests caused by unordered
  collections, hash seed, and missing tie-breakers.
---

# Deterministic Tie Debugging

When a test is flaky across runs or environments:

1. Look for unordered collections (`set`, plain iteration over set-like data,
   dicts built from sets) in selection/ranking code.
2. Reproduce with different `PYTHONHASHSEED` values.
3. Any ranking function must define an explicit total order.
4. For candidate selection, sort by the primary score first, then by the
   documented tie-breaker. For this repository's router, ties on score MUST be
   broken by lexicographically smallest candidate name.
5. Do not weaken tests; make production code deterministic.
"""

_SELECTOR_BUGGY = '''\
def choose_primary(candidates):
    """Return the primary candidate name from a list of {'name', 'score'} dicts."""
    best_score = max(c["score"] for c in candidates)
    tied_names = {c["name"] for c in candidates if c["score"] == best_score}
    return next(iter(tied_names))
'''

_SELECTOR_TEST = '''\
from selector import choose_primary


def test_unique_best_candidate():
    assert choose_primary([
        {"name": "gamma", "score": 4},
        {"name": "alpha", "score": 9},
        {"name": "beta", "score": 5},
    ]) == "alpha"


def test_tie_breaks_lexicographically_smallest_name():
    assert choose_primary([
        {"name": "zulu", "score": 8},
        {"name": "alpha", "score": 8},
        {"name": "mango", "score": 8},
    ]) == "alpha"
'''

_SELECTOR_GOLD = '''\
def choose_primary(candidates):
    """Return the primary candidate name from a list of {'name', 'score'} dicts."""
    if not candidates:
        raise ValueError("candidates must not be empty")
    return sorted(candidates, key=lambda c: (-c["score"], c["name"]))[0]["name"]
'''


def _seeded_pytest_check(ws) -> VerifyResult:
    seeds = ["1", "2", "3", "4", "5", "123"]
    failures = []
    for seed in seeds:
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        try:
            result = subprocess.run(  # noqa: S603 — benchmark only
                [sys.executable, "-m", "pytest", "-q", "tests"],
                cwd=ws,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return VerifyResult(False, f"pytest timed out with PYTHONHASHSEED={seed}")
        if result.returncode != 0:
            failures.append(f"seed={seed}: {(result.stdout + result.stderr).strip()[-240:]}")
    if failures:
        return VerifyResult(False, "flaky pytest failure(s): " + " | ".join(failures[:2]))
    return VerifyResult(True, f"pytest passed under {len(seeds)} PYTHONHASHSEED values")


C1_FLAKY_TIE_BREAK = Task(
    id="skill_c1_deterministic_tie_break",
    name="Fix a flaky selector test by adding an explicit deterministic tie-breaker",
    tags=("skill", "debugging-heuristic", "pytest", "flaky", "medium"),
    prompt=(
        "В проекте есть selector.py и tests/test_selector.py. Тест на выбор primary "
        "candidate флакает между окружениями. Почини production code, не ослабляя "
        "тесты: выбор должен быть детерминированным по диагностическому скиллу. "
        "Сохрани публичную функцию choose_primary(candidates)."
    ),
    setup_files={
        "selector.py": _SELECTOR_BUGGY,
        "tests/test_selector.py": _SELECTOR_TEST,
        ".claude/skills/deterministic-tie-debugging/SKILL.md": _DET_SELECT_SKILL,
        ".agents/skills/deterministic-tie-debugging/SKILL.md": _DET_SELECT_SKILL,
    },
    gold_files={"selector.py": _SELECTOR_GOLD},
    verifier=_seeded_pytest_check,
)

# ---------------------------------------------------------------------------
# D1b — harder multi-file scientific calculation with units and piecewise rules
# ---------------------------------------------------------------------------

_ARCFLUX4_SKILL = """\
---
name: arcflux-af4-multifile-method
description: Valeo ArcFlux AF-4 multi-sensor exposure method. Use when computing
  exposure from ArcFlux readings plus calibration files.
---

# Valeo ArcFlux AF-4 Multi-Sensor Exposure Method

Implement `compute_total_exposure(readings_csv: str, calibration_csv: str) -> float`.

`readings_csv` columns:
`minute,sensor_id,raw,dark,unit,temp_c`

`calibration_csv` columns:
`sensor_id,gain,offset`

## Method

1. Join readings to calibration by `sensor_id`.
2. Convert `raw` and `dark` to base flux units before subtracting:
   - `base` -> value as-is
   - `centi` -> value / 100
   - `milli` -> value / 1000
3. Compute base signal: `signal = max(0, raw_base - dark_base)`.
4. Apply calibration: `calibrated = signal * gain + offset`.
5. Apply temperature factor:
   - if `temp_c < 15`: `factor = 1 + 0.006 * (15 - temp_c)`
   - if `15 <= temp_c <= 25`: `factor = 1 - 0.003 * (temp_c - 20)`
   - if `temp_c > 25`: `factor = max(0.85, 1 - 0.008 * (temp_c - 25))`
6. Corrected intensity is `calibrated * factor`.
7. Integrate with the trapezoidal rule **separately per sensor_id** using that
   sensor's chronological readings. Sum sensor areas.
8. Convert total area to exposure by dividing by `60`.
9. Return the final value rounded to 4 decimal places.
"""

_AF4_READINGS = """\
minute,sensor_id,raw,dark,unit,temp_c
0,S-A,1200,100,centi,14
10,S-A,15.0,1.0,base,20
25,S-A,17000,900,milli,28
0,S-B,900,100,centi,18
12,S-B,11.5,0.5,base,26
30,S-B,15000,2000,milli,12
"""

_AF4_CALIBRATION = """\
sensor_id,gain,offset
S-A,1.20,0.50
S-B,0.85,-0.20
"""

_AF4_GOLD = '''\
import csv
from collections import defaultdict


def _unit_value(value: str, unit: str) -> float:
    value_f = float(value)
    if unit == "base":
        return value_f
    if unit == "centi":
        return value_f / 100
    if unit == "milli":
        return value_f / 1000
    raise ValueError(f"unknown unit {unit!r}")


def _temp_factor(temp_c: float) -> float:
    if temp_c < 15:
        return 1 + 0.006 * (15 - temp_c)
    if temp_c <= 25:
        return 1 - 0.003 * (temp_c - 20)
    return max(0.85, 1 - 0.008 * (temp_c - 25))


def compute_total_exposure(readings_csv: str, calibration_csv: str) -> float:
    with open(calibration_csv, newline="", encoding="utf-8") as f:
        calibration = {row["sensor_id"]: row for row in csv.DictReader(f)}
    by_sensor = defaultdict(list)
    with open(readings_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cal = calibration[row["sensor_id"]]
            raw = _unit_value(row["raw"], row["unit"])
            dark = _unit_value(row["dark"], row["unit"])
            signal = max(0.0, raw - dark)
            calibrated = signal * float(cal["gain"]) + float(cal["offset"])
            corrected = calibrated * _temp_factor(float(row["temp_c"]))
            by_sensor[row["sensor_id"]].append((float(row["minute"]), corrected))
    total_area = 0.0
    for points in by_sensor.values():
        points.sort(key=lambda p: p[0])
        for (t0, y0), (t1, y1) in zip(points, points[1:], strict=False):
            total_area += (t1 - t0) * (y0 + y1) / 2
    return round(total_area / 60, 4)
'''


def _af4_reference(readings_csv, calibration_csv) -> float:
    from collections import defaultdict

    def unit_value(value, unit):
        value = float(value)
        return value if unit == "base" else value / 100 if unit == "centi" else value / 1000

    def temp_factor(temp):
        temp = float(temp)
        if temp < 15:
            return 1 + 0.006 * (15 - temp)
        if temp <= 25:
            return 1 - 0.003 * (temp - 20)
        return max(0.85, 1 - 0.008 * (temp - 25))

    with open(calibration_csv, newline="", encoding="utf-8") as f:
        cal = {r["sensor_id"]: r for r in csv.DictReader(f)}
    by_sensor = defaultdict(list)
    with open(readings_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            signal = max(0.0, unit_value(row["raw"], row["unit"]) - unit_value(row["dark"], row["unit"]))
            corrected = (signal * float(cal[row["sensor_id"]]["gain"]) + float(cal[row["sensor_id"]]["offset"])) * temp_factor(row["temp_c"])
            by_sensor[row["sensor_id"]].append((float(row["minute"]), corrected))
    area = 0.0
    for pts in by_sensor.values():
        pts.sort(key=lambda p: p[0])
        for (t0, y0), (t1, y1) in zip(pts, pts[1:], strict=False):
            area += (t1 - t0) * (y0 + y1) / 2
    return round(area / 60, 4)


def _af4_check(ws) -> VerifyResult:
    sol = ws / "solution.py"
    if not sol.exists():
        return VerifyResult(False, "solution.py missing")
    hidden_r = ws / "hidden_af4_readings.csv"
    hidden_c = ws / "hidden_af4_calibration.csv"
    hidden_r.write_text(
        "minute,sensor_id,raw,dark,unit,temp_c\n"
        "0,S-X,20000,3000,milli,10\n"
        "8,S-X,18.0,1.5,base,20\n"
        "21,S-X,1900,250,centi,31\n"
        "3,S-Y,500,900,centi,18\n"
        "17,S-Y,22000,1000,milli,27\n",
        encoding="utf-8",
    )
    hidden_c.write_text("sensor_id,gain,offset\nS-X,1.05,0.30\nS-Y,0.70,0.10\n", encoding="utf-8")
    import importlib.util as _ilu

    sys.modules.pop("solution", None)
    spec = _ilu.spec_from_file_location("solution", sol)
    mod = _ilu.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001
        return VerifyResult(False, f"solution.py failed to import: {type(exc).__name__}: {exc}")
    if not hasattr(mod, "compute_total_exposure"):
        return VerifyResult(False, "compute_total_exposure(readings_csv, calibration_csv) missing")
    wrong = []
    for readings, calibration in [(ws / "readings.csv", ws / "calibration.csv"), (hidden_r, hidden_c)]:
        try:
            got = mod.compute_total_exposure(str(readings), str(calibration))
        except Exception as exc:  # noqa: BLE001
            return VerifyResult(False, f"compute_total_exposure raised: {type(exc).__name__}: {exc}")
        exp = _af4_reference(readings, calibration)
        if got != exp:
            wrong.append(f"{readings.name}: {got!r} != {exp!r}")
    if wrong:
        return VerifyResult(False, "; ".join(wrong))
    return VerifyResult(True, "AF-4 exposure matches multi-file unit conversion and per-sensor integration")


D1B_ARCFLUX_AF4 = Task(
    id="task_330_skill_d1b_arcflux_multifile",
    name="Compute AF-4 multi-sensor exposure with units and calibration joins",
    tags=("skill", "mathematical-method", "calculation", "hard"),
    prompt=(
        "В рабочей директории есть readings.csv и calibration.csv. Создай solution.py "
        "с функцией compute_total_exposure(readings_csv: str, calibration_csv: str) "
        "-> float. Метод AF-4 бери строго из скилла: join по sensor_id, unit "
        "conversion, max(0) signal, piecewise temperature factor, trapezoidal "
        "integration separately per sensor_id, sum и rounding."
    ),
    setup_files={
        "readings.csv": _AF4_READINGS,
        "calibration.csv": _AF4_CALIBRATION,
        ".claude/skills/arcflux-af4-multifile-method/SKILL.md": _ARCFLUX4_SKILL,
        ".agents/skills/arcflux-af4-multifile-method/SKILL.md": _ARCFLUX4_SKILL,
    },
    gold_files={"solution.py": _AF4_GOLD},
    verifier=_af4_check,
)


# ---------------------------------------------------------------------------
# C1b — harder flaky async/order bug
# ---------------------------------------------------------------------------

_STABLE_ASYNC_SKILL = """\
---
name: stable-async-debugging
description: Debugging checklist for flaky concurrent Python results caused by
  as_completed ordering and missing output ordering contracts.
---

# Stable Async Debugging

When tests fail intermittently around concurrent code:

1. Look for `concurrent.futures.as_completed`, queues, callbacks, or worker
   completion order being used as output order.
2. Reproduce with several input orders and delay patterns, not just one run.
3. Preserve the caller's input order unless the function contract explicitly
   says completion order is desired.
4. Do not remove concurrency just to make the test pass. Keep `ThreadPoolExecutor`
   but store each future's input index and reassemble results by that index.
5. Do not weaken or rewrite tests.
"""

_PIPELINE_BUGGY = '''\
from concurrent.futures import ThreadPoolExecutor, as_completed
import time


def _render(item):
    name, delay = item
    time.sleep(delay)
    return name.upper()


def render_all(items):
    """Render all (name, delay) items concurrently, preserving input order."""
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_render, item) for item in items]
        for fut in as_completed(futures):
            results.append(fut.result())
    return results
'''

_PIPELINE_TEST = '''\
from pipeline import render_all


def test_render_all_preserves_input_order_despite_delays():
    items = [("slow", 0.04), ("fast", 0.0), ("mid", 0.02), ("last", 0.01)]
    assert render_all(items) == ["SLOW", "FAST", "MID", "LAST"]


def test_render_all_preserves_input_order_for_reverse_delays():
    items = [("a", 0.03), ("b", 0.02), ("c", 0.01), ("d", 0.0)]
    assert render_all(items) == ["A", "B", "C", "D"]
'''

_PIPELINE_GOLD = '''\
from concurrent.futures import ThreadPoolExecutor, as_completed
import time


def _render(item):
    name, delay = item
    time.sleep(delay)
    return name.upper()


def render_all(items):
    """Render all (name, delay) items concurrently, preserving input order."""
    ordered = [None] * len(items)
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_index = {pool.submit(_render, item): idx for idx, item in enumerate(items)}
        for fut in as_completed(future_to_index):
            ordered[future_to_index[fut]] = fut.result()
    return ordered
'''


def _stable_async_check(ws) -> VerifyResult:
    test_path = ws / "tests" / "test_pipeline.py"
    if not test_path.exists() or test_path.read_text(encoding="utf-8") != _PIPELINE_TEST:
        return VerifyResult(False, "tests/test_pipeline.py was changed or is missing")
    failures = []
    for _i in range(4):
        try:
            result = subprocess.run(  # noqa: S603 — benchmark only
                [sys.executable, "-m", "pytest", "-q", "tests"],
                cwd=ws,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return VerifyResult(False, "pytest timed out")
        if result.returncode != 0:
            failures.append((result.stdout + result.stderr).strip()[-300:])
    if failures:
        return VerifyResult(False, "pytest failed: " + failures[0])
    import importlib.util as _ilu

    sys.modules.pop("pipeline", None)
    spec = _ilu.spec_from_file_location("pipeline", ws / "pipeline.py")
    mod = _ilu.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        got = mod.render_all([("z", 0.02), ("a", 0.0), ("m", 0.01)])
    except Exception as exc:  # noqa: BLE001
        return VerifyResult(False, f"render_all hidden check raised: {type(exc).__name__}: {exc}")
    if got != ["Z", "A", "M"]:
        return VerifyResult(False, f"hidden order check returned {got!r}")
    return VerifyResult(True, "pytest and hidden order checks preserve input order under concurrent completion")


C1B_STABLE_ASYNC_ORDER = Task(
    id="skill_c1b_stable_async_order",
    name="Fix flaky concurrent output ordering without removing ThreadPoolExecutor",
    tags=("skill", "debugging-heuristic", "pytest", "concurrency", "hard"),
    prompt=(
        "В проекте есть pipeline.py и tests/test_pipeline.py. Тесты флакают из-за "
        "порядка завершения concurrent workers. Почини production code, не меняя "
        "tests/test_pipeline.py и не удаляя ThreadPoolExecutor: render_all(items) "
        "должен сохранять порядок входного списка. Используй debugging skill."
    ),
    setup_files={
        "pipeline.py": _PIPELINE_BUGGY,
        "tests/test_pipeline.py": _PIPELINE_TEST,
        ".claude/skills/stable-async-debugging/SKILL.md": _STABLE_ASYNC_SKILL,
        ".agents/skills/stable-async-debugging/SKILL.md": _STABLE_ASYNC_SKILL,
    },
    gold_files={"pipeline.py": _PIPELINE_GOLD},
    verifier=_stable_async_check,
)


SKILL_TASKS = [
    R1_BRAND, B1_CODEBOOK, B3_POLICY, G2_SKILL_REPAIR, R2_STYLE, B2_NDR7,
    E1_DISTRACTOR, E2_NEG_CONTROL, E3_SELECTION, G1_CREATE_SKILL,
    A1_LUMEN_DSL, A2_Q9_PROTOCOL, A3_QUOKKA_LIBRARY, R3_ACT4_TEMPLATE,
    D2_MERIDIAN_RECONCILE, D1_ARCFLUX_EXPOSURE, D1B_ARCFLUX_AF4,
    # C1_FLAKY_TIE_BREAK and C1B_STABLE_ASYNC_ORDER are intentionally not in
    # SKILL_TASKS: no-skill control runs showed both are solvable without the
    # skill, so they are standard debugging controls rather than discriminators.
]
