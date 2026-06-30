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


SKILL_TASKS = [R1_BRAND, B1_CODEBOOK, B3_POLICY]
