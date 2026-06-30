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

import csv
import re
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
    id="skill_b1_failure_codebook",
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
    id="skill_b3_claim_triage_policy",
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
    id="skill_g2_repair_phone_skill",
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
    id="skill_r2_style_guide_report",
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
        file_matches_regex("report.txt", r"^QUARTERLY RESULTS\s*$"),
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
    got: dict[str, int] = {}
    try:
        with p.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                norm = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                rid, amt = norm.get("id"), norm.get("amount")
                if rid is None or amt is None:
                    return VerifyResult(False, "parsed.csv must have columns id,amount")
                got[rid] = int(amt)
    except (csv.Error, ValueError, UnicodeDecodeError) as exc:
        return VerifyResult(False, f"could not read parsed.csv: {exc}")
    wrong = [f"{k}={got.get(k)}≠{v}" for k, v in _NDR7_GOLD.items() if got.get(k) != v]
    if wrong:
        return VerifyResult(False, "wrong amounts: " + "; ".join(wrong))
    return VerifyResult(True, "all NDR-7 records parsed with correct signed amounts")


B2_NDR7 = Task(
    id="skill_b2_ndr7_parse",
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
    id="skill_e1_codebook_with_distractors",
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
    id="skill_e2_negative_control_sum",
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
    id="skill_e3_codebook_selection",
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
    id="skill_g1_create_slugify_skill",
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


SKILL_TASKS = [
    R1_BRAND, B1_CODEBOOK, B3_POLICY, G2_SKILL_REPAIR, R2_STYLE, B2_NDR7,
    E1_DISTRACTOR, E2_NEG_CONTROL, E3_SELECTION, G1_CREATE_SKILL,
]
