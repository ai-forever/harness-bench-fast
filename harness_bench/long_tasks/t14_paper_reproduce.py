"""long_14_paper_reproduce — воспроизвести числа статьи по её методам и сырым данным.

The workspace holds a Russian observational-cohort paper split into sections, its
appendices (protocol amendments, site laboratory methods, a two-part data-query log
with corrections and supplements, committee minutes, analyst Q&A, site correspondence
that must NOT be applied), a codebook, per-site CSV exports (participants, visits,
laboratory) and ``results_template.json`` with 50 keys. The agent must recompute every
number following the methods *as amended*.

Ground truth comes from the world model (true attributes, native lab units and
exported errors are kept as separate fields), analysed by ``analyze()`` below —
it never parses the rendered documents or CSVs. Near misses reuse ``analyze()``
with one rule broken (amendment skipped, units not converted, wrong exclusion
order, corrections ignored, age at enrolment, naive pipeline).

The query log (about 600 entries, split into files by whole quarters) and the
rule-carrying sections of appendices A, B, D and J have ``REWRITE`` hooks: an LLM
paraphrase is used only when an independent reader recovers every entry's
correction (field, old/new value, date, applied or not) or the section's rule
parameters (``_SECTION_TRUTH``); see ``scripts/rewrite_long_texts.py``.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import math
import random
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .common import (
    long_task,
    read_json,
    require_share,
    rewritten,
    rng,
    write,
    write_csv,
    write_json,
)

TASK_ID = "long_14_paper_reproduce"
D = dt.date

SITES = {
    1: ("Казань", "Республиканский клинический диабетологический центр"),
    2: ("Екатеринбург", "Областная клиническая больница № 2"),
    3: ("Томск", "Клиника эндокринологии медицинского университета"),
    4: ("Самара", "Городская поликлиника № 7, эндокринологическое отделение"),
    5: ("Ярославль", "Областной эндокринологический диспансер"),
    6: ("Новосибирск", "Центр профилактической медицины"),
}
SITE_WEIGHTS = {1: 0.2, 2: 0.19, 3: 0.15, 4: 0.16, 5: 0.14, 6: 0.16}
SITE2_SWITCH = D(2022, 3, 1)      # site 2 HbA1c: % before, mmol/mol from this date
SITE5_CUTOFF = D(2021, 9, 1)      # amendment 3
IFCC_A, IFCC_B = 0.09148, 2.152   # % = A * mmol/mol + B
LDL_MGDL = 38.67
CREAT_MGDL = 88.4
N_PEOPLE = 2960


def hba1c_unit(site: int, day: dt.date) -> str:
    if site == 6 or (site == 2 and day >= SITE2_SWITCH):
        return "mmol"
    return "pct"


def ldl_unit(site: int) -> str:
    return "mgdl" if site == 3 else "mmol"


def creat_unit(site: int) -> str:
    return "mgdl" if site in (5, 6) else "umol"


def full_years(birth: dt.date, day: dt.date) -> int:
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def _add_years(d: dt.date, years: int) -> dt.date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


# ==========================================================================
# World model
# ==========================================================================

class P:  # participant record (true and exported values kept separately)
    __slots__ = ("pid", "site", "num", "enroll", "enroll_exp", "birth", "birth_exp", "sex", "sex_exp",
                 "height_cm", "smoking", "group", "withdrawn", "withdrawn_exp", "nid", "ghost_of",
                 "has_v0", "base", "smoking_exp", "group_exp", "height_exp")


class V:
    __slots__ = ("vid", "pid", "code", "date", "date_exp", "weight", "sbp", "hypo", "weight_exp", "hypo_exp")


class L:
    __slots__ = ("lid", "pid", "date", "test", "native", "unit", "value_exp")


def _new(cls, **kw):
    obj = cls.__new__(cls)
    for k in cls.__slots__:
        setattr(obj, k, kw.get(k))
    return obj


def _r1(x: float) -> float:
    return float(Decimal(repr(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


@functools.cache
def build_world() -> dict:
    r = rng(TASK_ID)
    people: list[P] = []
    visits: list[V] = []
    labs: list[L] = []
    counters = {s: 0 for s in SITES}

    def new_pid(site: int) -> str:
        counters[site] += 1
        return f"{site:02d}-{counters[site]:04d}"

    lab_seq = [0]
    vis_seq = [0]

    def add_lab(p: P, day: dt.date, test: str, value_pct_or_native: float, *, native_unit: str | None = None):
        """Store a lab value measured in the site's native unit for that date."""
        lab_seq[0] += 1
        if test == "HBA1C":
            unit = native_unit or hba1c_unit(p.site, day)
            if unit == "mmol":
                native = float(round((value_pct_or_native - IFCC_B) / IFCC_A))
            else:
                native = _r1(value_pct_or_native)
        elif test == "LDL":
            unit = ldl_unit(p.site)
            native = float(round(value_pct_or_native * LDL_MGDL)) if unit == "mgdl" else round(value_pct_or_native, 2)
        else:
            unit = creat_unit(p.site)
            native = round(value_pct_or_native / CREAT_MGDL, 2) if unit == "mgdl" else float(round(value_pct_or_native))
        lab = _new(L, lid=lab_seq[0], pid=p.pid, date=day, test=test, native=native, unit=unit, value_exp=native)
        labs.append(lab)
        return lab

    def add_visit(p: P, code: str, day: dt.date, hypo_rate: float):
        vis_seq[0] += 1
        hypo = -99 if r.random() < 0.04 else min(6, int(r.expovariate(1.0) * hypo_rate * 3))
        v = _new(V, vid=vis_seq[0], pid=p.pid, code=code, date=day, date_exp=day,
                 weight=-99 if r.random() < 0.03 else round(r.gauss(p.base["w"], 1.5), 1),
                 sbp=r.randint(112, 168), hypo=hypo)
        v.weight_exp, v.hypo_exp = v.weight, v.hypo
        visits.append(v)
        return v

    sites = list(SITE_WEIGHTS)
    wts = [SITE_WEIGHTS[s] for s in sites]
    for _ in range(N_PEOPLE):
        site = r.choices(sites, wts)[0]
        p = _new(P, pid=new_pid(site), site=site)
        p.enroll = D(2020, 3, 1) + dt.timedelta(days=r.randrange(852))
        age = min(88.0, max(15.5, r.gauss(61, 11.5)))
        p.birth = p.enroll - dt.timedelta(days=int(age * 365.25) + r.randrange(0, 200))
        p.sex = 2 if r.random() < 0.54 else 1
        p.height_cm = round(r.gauss(176 if p.sex == 1 else 163, 7))
        p.smoking = r.choices([0, 1, 2, 9], [52, 24, 19, 5])[0]
        a = full_years(p.birth, p.enroll)
        pb = 1 / (1 + math.exp(-(-0.1 - 0.035 * (a - 60) + 0.35 * (p.sex == 2) + 0.15 * (site in (2, 3)))))
        p.group = 2 if r.random() < pb else 1
        p.withdrawn = r.random() < 0.035
        p.nid = f"{r.getrandbits(40):010x}"
        p.has_v0 = not (p.withdrawn and r.random() < 0.5) and r.random() > 0.015
        base_hba = min(13.8, max(5.6, r.gauss(8.4, 1.15)))
        p.base = {"w": round(r.gauss(92 if p.sex == 1 else 81, 15), 1), "hba": base_hba}
        people.append(p)

    # a handful of people whose 18th/80th birthday falls between enrolment and baseline
    boundary = r.sample(people, 24)
    for k, p in enumerate(boundary):
        years = 18 if k % 2 == 0 else 80
        p.birth = _add_years(p.enroll + dt.timedelta(days=r.randint(1, 9)), -years)

    for p in people:
        a = full_years(p.birth, p.enroll)
        p.base["delay"] = r.randint(0, 21) if r.random() < 0.9 else r.randint(22, 40)
        base_date = p.enroll + dt.timedelta(days=p.base["delay"])
        p.base["date"] = base_date
        hypo_rate = 0.12 if p.group == 1 else 0.075
        if p.has_v0:
            add_visit(p, "V0", base_date, hypo_rate)
        # baseline labs
        if not (p.withdrawn and not p.has_v0):
            x = r.random()
            if x < 0.935:
                bl = base_date - dt.timedelta(days=r.randint(0, 26))
                add_lab(p, bl, "HBA1C", p.base["hba"])
                if r.random() < 0.08:  # an earlier screening value in the window too
                    add_lab(p, bl - dt.timedelta(days=r.randint(1, 4)), "HBA1C", p.base["hba"] + r.gauss(0, 0.3))
                ldl = min(7.4, max(1.1, r.gauss(3.2, 0.9)))
                if r.random() < 0.013:
                    ldl = r.uniform(9.0, 14.0)
                if r.random() < 0.97:
                    add_lab(p, bl, "LDL", ldl)
                cr = math.exp(r.gauss(4.43, 0.22))
                y = r.random()
                if y < 0.035:
                    cr = r.uniform(178, 199)
                elif y < 0.06:
                    cr = r.uniform(205, 320)
                if r.random() < 0.95:
                    add_lab(p, bl, "CREAT", cr)
            elif x < 0.965:
                add_lab(p, base_date + dt.timedelta(days=r.randint(2, 10)), "HBA1C", p.base["hba"])
        if p.withdrawn and not p.has_v0:
            continue
        # follow-up
        stop = 400
        if p.withdrawn:
            stop = r.randint(20, 300)
        age_eff = 0.004 * (a - 60)
        drops = {1: (-0.55, -0.8, -0.7), 2: (-0.75, -1.1, -0.95)}[p.group]
        for code, target, sd, miss, drop in (("V1", 91, 8, 0.06, drops[0]), ("V2", 182, 9.5, 0.09, drops[1]),
                                             ("V3", 365, 19, 0.14, drops[2])):
            off = round(r.gauss(target, sd))
            if off > stop or r.random() < miss:
                continue
            vday = base_date + dt.timedelta(days=off)
            if vday > D(2023, 8, 31):
                continue
            add_visit(p, code, vday, hypo_rate)
            hb = p.base["hba"] + drop - age_eff + r.gauss(0, 0.75)
            hb = min(14.0, max(4.8, hb))
            lday = vday + dt.timedelta(days=r.choice([0, 0, 0, 1, 2, 3, -1, -2]))
            if r.random() < 0.95:
                add_lab(p, lday, "HBA1C", hb)
            if code in ("V2", "V3") and r.random() < 0.85:
                add_lab(p, lday, "LDL", min(7.0, max(1.0, r.gauss(2.8, 0.85))))
            if code in ("V2", "V3") and r.random() < 0.7:
                add_lab(p, lday, "CREAT", math.exp(r.gauss(4.45, 0.22)))
        for _ in range(r.choices([0, 1, 2, 3, 4, 5], [10, 20, 25, 22, 14, 9])[0]):
            off = r.randint(8, 410)
            if off > stop:
                continue
            uday = base_date + dt.timedelta(days=off)
            if uday > D(2023, 8, 31):
                continue
            add_visit(p, "U", uday, hypo_rate)
            if r.random() < 0.45:
                frac = min(1.0, off / 182)
                add_lab(p, uday, "HBA1C", p.base["hba"] + frac * drops[1] + r.gauss(0, 0.8))
            if r.random() < 0.3:
                add_lab(p, uday, "CREAT", math.exp(r.gauss(4.45, 0.22)))
            if r.random() < 0.3:
                add_lab(p, uday, "LDL", min(7.0, max(1.0, r.gauss(2.9, 0.85))))

    # same-day re-runs (replicates) of HbA1c
    for lab in list(labs):
        if lab.test == "HBA1C" and r.random() < 0.025:
            pct = lab_to_pct(lab.native, lab.unit)
            lab_seq[0] += 1
            twin = _new(L, lid=lab_seq[0], pid=lab.pid, date=lab.date, test="HBA1C", unit=lab.unit)
            val = pct + r.choice([-0.2, -0.1, 0.1, 0.2, 0.3])
            twin.native = float(round((val - IFCC_B) / IFCC_A)) if lab.unit == "mmol" else _r1(val)
            twin.value_exp = twin.native
            labs.append(twin)

    by_pid = {p.pid: p for p in people}

    # ghost duplicate registrations (later enrolment, same national id)
    ghosts = []
    for p in r.sample([p for p in people if p.has_v0], 38):
        g = _new(P, pid=new_pid(p.site), site=p.site, birth=p.birth, birth_exp=p.birth, sex=p.sex,
                 sex_exp=p.sex, height_cm=p.height_cm, smoking=p.smoking, group=p.group, withdrawn=False,
                 withdrawn_exp=False, nid=p.nid, ghost_of=p.pid, has_v0=True, base=dict(p.base))
        g.enroll = p.enroll + dt.timedelta(days=r.randint(3, 60))
        g.enroll_exp = g.enroll
        bd = g.enroll + dt.timedelta(days=r.randint(0, 5))
        g.base["date"] = bd
        add_visit(g, "V0", bd, 0.1)
        add_lab(g, bd - dt.timedelta(days=1), "HBA1C", p.base["hba"] + r.gauss(0, 0.2))
        ghosts.append(g)

    for p in people + ghosts:
        p.enroll_exp, p.birth_exp, p.sex_exp, p.withdrawn_exp = p.enroll, p.birth, p.sex, p.withdrawn
        p.smoking_exp, p.group_exp, p.height_exp = p.smoking, p.group, p.height_cm

    corrections = _inject_errors(r, people, visits, labs, by_pid)
    return {"people": people, "ghosts": ghosts, "visits": visits, "labs": labs,
            "corrections": corrections, "by_pid": by_pid}


def lab_to_pct(native: float, unit: str) -> float:
    return _r1(IFCC_A * native + IFCC_B) if unit == "mmol" else native


DB_LOCK = D(2023, 10, 16)


def _inject_errors(r: random.Random, people: list[P], visits: list[V], labs: list[L],
                   by_pid: dict[str, P]) -> list[dict]:
    """Make exported values differ from true ones; return the data-query log entries."""
    log: list[dict] = []
    used: set[str] = set()
    v0 = {v.pid: v for v in visits if v.code == "V0"}
    usable = [p for p in people if p.pid in v0 and not p.withdrawn]

    def pick(pool, k):
        out = [p for p in r.sample(pool, min(len(pool), k * 3)) if p.pid not in used][:k]
        used.update(p.pid for p in out)
        return out

    # birth dates: near age boundaries (so the exclusion flips) and random typos
    near = [p for p in usable if full_years(p.birth, v0[p.pid].date) in (18, 19, 78, 79)]
    for p in pick(near, 12):
        a = full_years(p.birth, v0[p.pid].date)
        p.birth_exp = _add_years(p.birth, 2 if a <= 19 else -2)
        log.append({"kind": "birth", "pid": p.pid, "old": p.birth_exp, "new": p.birth})
    for p in pick(usable, 11):
        y = p.birth.year
        swapped = int(str(y)[:2] + str(y)[3] + str(y)[2])
        if swapped == y or not 1930 < swapped < 2006:
            swapped = y + 10
        p.birth_exp = p.birth.replace(year=swapped, day=min(p.birth.day, 28))
        log.append({"kind": "birth", "pid": p.pid, "old": p.birth_exp, "new": p.birth})
    # two-step corrections (a first wrong correction, later clarified)
    for e in [x for x in log if x["kind"] == "birth"][:5]:
        wrong = e["new"] + dt.timedelta(days=r.choice([-400, 365, 730]))
        e["first"] = wrong
    # sex
    for p in pick(usable, 21):
        p.sex_exp = 3 - p.sex
        log.append({"kind": "sex", "pid": p.pid, "old": p.sex_exp, "new": p.sex})
    # consent withdrawn but form arrived late
    for p in pick(usable, 11):
        p.withdrawn = True
        p.withdrawn_exp = False
        day = min(D(2023, 6, 30), v0[p.pid].date + dt.timedelta(days=r.randint(30, 200)))
        log.append({"kind": "consent", "pid": p.pid, "day": day})
    # enrolment dates around the site-5 cut-off
    s5 = [p for p in usable if p.site == 5 and abs((p.enroll - SITE5_CUTOFF).days) <= 25]
    for p in pick(s5, 6):
        if p.enroll >= SITE5_CUTOFF:
            p.enroll_exp = SITE5_CUTOFF - dt.timedelta(days=r.randint(1, 20))
        else:
            p.enroll_exp = SITE5_CUTOFF + dt.timedelta(days=r.randint(0, 20))
        log.append({"kind": "enroll", "pid": p.pid, "old": p.enroll_exp, "new": p.enroll})
    # HbA1c entered in % at a site/period that reports mmol/mol
    cand = [lab for lab in labs if lab.test == "HBA1C" and lab.unit == "mmol" and lab.pid in by_pid
            and lab.pid not in used]
    for lab in r.sample(cand, 9):
        used.add(lab.pid)
        lab.unit = "pct"
        lab.native = lab_to_pct(lab.native, "mmol")
        lab.value_exp = lab.native
        log.append({"kind": "unit", "pid": lab.pid, "day": lab.date, "value": lab.native})
    # decimal point lost
    cand = [lab for lab in labs if lab.test == "HBA1C" and lab.unit == "pct" and lab.pid in by_pid
            and lab.pid not in used]
    for lab in r.sample(cand, 9):
        used.add(lab.pid)
        lab.value_exp = round(lab.native * 10)
        log.append({"kind": "typo", "pid": lab.pid, "day": lab.date, "old": lab.value_exp, "new": lab.native})
    # visit date typos (baseline and 6-month visits)
    cand = [v for v in visits if v.code in ("V0", "V2") and v.pid in by_pid and v.pid not in used
            and v.date.day <= 12 and v.date.month != v.date.day]
    for v in r.sample(cand, 18):
        used.add(v.pid)
        v.date_exp = v.date.replace(month=v.date.day, day=v.date.month)
        log.append({"kind": "vdate", "pid": v.pid, "code": v.code, "old": v.date_exp, "new": v.date})
    # baseline weight with a lost decimal point
    cand = [v for v in visits if v.code == "V0" and v.weight != -99 and v.pid in by_pid and v.pid not in used]
    for v in r.sample(cand, 16):
        used.add(v.pid)
        v.weight_exp = round(v.weight * 10)
        log.append({"kind": "weight", "pid": v.pid, "day": v.date, "old": v.weight_exp, "new": v.weight})
    # smoking status
    for p in pick(usable, 19):
        p.smoking_exp = {0: 2, 1: 2, 2: 1, 9: 2}[p.smoking]
        log.append({"kind": "smoking", "pid": p.pid, "old": p.smoking_exp, "new": p.smoking})
    # therapy code
    for p in pick(usable, 13):
        p.group_exp = 3 - p.group
        log.append({"kind": "therapy", "pid": p.pid, "old": p.group_exp, "new": p.group})
    # height typed wrongly
    for p in pick([p for p in usable if p.site != 4], 12):
        p.height_exp = round(p.height_cm / 10) if r.random() < 0.5 else p.height_cm + r.choice([-30, 30, 100])
        log.append({"kind": "height", "pid": p.pid, "old": p.height_exp, "new": p.height_cm})
    # hypoglycaemia count entered in a wrong field / missed
    base = {v.pid: v.date for v in visits if v.code == "V0"}
    cand = [v for v in visits if v.code in ("V1", "V2", "U") and v.pid in by_pid and v.pid not in used
            and v.pid in base and 1 <= (v.date - base[v.pid]).days <= 196 and v.hypo != -99]
    for v in r.sample(cand, 21):
        used.add(v.pid)
        if v.hypo >= 1:
            v.hypo_exp = 0
        else:
            v.hypo_exp = r.choice([2, 3, 4])
        log.append({"kind": "hypo", "pid": v.pid, "day": v.date, "code": v.code, "old": v.hypo_exp, "new": v.hypo})
    # creatinine entered in mg/dL at a site that reports µmol/L (baseline samples; two of them high)
    def base_creat(pool_people, lo, hi):
        out = []
        for lab in labs:
            if lab.test != "CREAT" or lab.unit != "umol" or lab.pid not in v0 or lab.pid in used:
                continue
            day = (lab.date - v0[lab.pid].date).days
            if -30 <= day <= 0 and lo <= lab.native <= hi and lab.pid in pool_people:
                out.append(lab)
        return out
    pool_ids = {p.pid for p in usable}
    for lo, hi, k in ((201, 330, 3), (60, 140, 7)):
        for lab in r.sample(base_creat(pool_ids, lo, hi), k):
            used.add(lab.pid)
            lab.unit = "mgdl"
            lab.native = round(lab.native / CREAT_MGDL, 2)
            lab.value_exp = lab.native
            log.append({"kind": "creat_unit", "pid": lab.pid, "day": lab.date, "value": lab.native})
    # LDL with a lost decimal separator (mmol/L sites), mostly in the 12-month window
    cand12, cand0 = [], []
    for lab in labs:
        if lab.test != "LDL" or lab.unit != "mmol" or lab.pid not in v0 or lab.pid in used \
                or lab.pid not in pool_ids:
            continue
        day = (lab.date - v0[lab.pid].date).days
        (cand12 if 335 <= day <= 395 else cand0 if -30 <= day <= 0 else []).append(lab)
    for lab in r.sample(cand12, 10) + r.sample(cand0, 3):
        used.add(lab.pid)
        lab.value_exp = round(lab.native * 100)
        log.append({"kind": "ldl_typo", "pid": lab.pid, "day": lab.date, "old": lab.value_exp, "new": lab.native})
    # baseline weight not entered (-99) but found in the source documents
    cand = [v for v in visits if v.code == "V0" and v.weight == -99 and v.pid in pool_ids and v.pid not in used]
    for v in r.sample(cand, min(9, len(cand))):
        used.add(v.pid)
        v.weight = round(r.gauss(by_pid[v.pid].base["w"], 1.5), 1)
        log.append({"kind": "weight_fill", "pid": v.pid, "day": v.date, "new": v.weight})
    # consent withdrawal logged, then cancelled by a supplement (the last one arrives after lock)
    for k, p in enumerate(pick(usable, 6)):
        day = min(D(2023, 6, 30), v0[p.pid].date + dt.timedelta(days=r.randint(30, 200)))
        e = {"kind": "consent", "pid": p.pid, "day": day, "cancel": True, "cancel_late": k in (3, 5)}
        if k in (3, 5):
            p.withdrawn = True
            p.withdrawn_exp = False
        log.append(e)
    # birth date corrected before lock, with a further "supplement" that arrived after lock
    near2 = [p for p in usable if p.pid not in used and full_years(p.birth, v0[p.pid].date) in (19, 78, 79)]
    for p in pick(near2, 4):
        a = full_years(p.birth, v0[p.pid].date)
        p.birth_exp = p.birth + dt.timedelta(days=r.choice([-3650, 3650]))
        late = _add_years(p.birth, 2 if a <= 19 else -2)
        log.append({"kind": "birth", "pid": p.pid, "old": p.birth_exp, "new": p.birth, "late_supp": late})
    # visit re-coded (no effect on the analysis: points are assigned by sample date)
    cand = [v for v in visits if v.code == "U" and v.pid in pool_ids and v.pid not in used]
    for v in r.sample(cand, 7):
        used.add(v.pid)
        log.append({"kind": "vcode", "pid": v.pid, "day": v.date, "new": r.choice(["V1", "V2", "V3"])})
    # corrections that arrived after database lock: logged, NOT applied (export value stands)
    for p in pick(usable, 6):
        if r.random() < 0.5:
            log.append({"kind": "late_sex", "pid": p.pid, "new": 3 - p.sex, "cur": p.sex})
        else:
            log.append({"kind": "late_birth", "pid": p.pid, "cur": p.birth,
                        "new": p.birth + dt.timedelta(days=r.choice([-1100, 1500, 2200]))})
    # queries closed without change
    for p in pick(people, 375):
        what = r.choice(["weight", "height", "hba1c", "smoking", "group", "creat", "ldl", "visit", "hypo"])
        log.append({"kind": "confirm", "pid": p.pid, "what": what})
    for e in log:
        k = e["kind"]
        if k in ("unit", "typo", "consent", "weight", "hypo", "creat_unit", "ldl_typo", "weight_fill", "vcode"):
            event = e["day"]
        elif k == "vdate":
            event = e["new"]
        else:
            pp = by_pid[e["pid"]]
            event = v0[e["pid"]].date if e["pid"] in v0 else pp.enroll
        e["logged"] = min(D(2023, 9, 1), event + dt.timedelta(days=r.randint(10, 150)))
        if k.startswith("late_"):
            e["logged"] = DB_LOCK + dt.timedelta(days=r.randint(5, 60))
        if "first" in e:
            e["logged_final"] = e["logged"] + dt.timedelta(days=r.randint(10, 40))
        if e.get("cancel"):
            e["cancel_logged"] = (DB_LOCK + dt.timedelta(days=r.randint(4, 45)) if e["cancel_late"]
                                  else e["logged"] + dt.timedelta(days=r.randint(8, 40)))
        if "late_supp" in e:
            e["late_supp_logged"] = DB_LOCK + dt.timedelta(days=r.randint(3, 50))
    log.sort(key=lambda e: (e["logged"], e["pid"]))
    for n, e in enumerate(log, 1):
        e["no"] = n
    return log


# ==========================================================================
# Reference analysis on the world model
# ==========================================================================

KEYS = [
    "F_dup_removed", "F_excl_consent", "F_excl_site5", "F_excl_no_v0", "F_excl_age",
    "F_excl_no_hba1c", "F_excl_creat", "N_A", "N_B",
    "T1_age_mean_A", "T1_age_mean_B", "T1_female_pct_A", "T1_female_pct_B", "T1_bmi_mean_A",
    "T1_bmi_mean_B", "T1_ldl_mean_A", "T1_ldl_mean_B", "T1_smoker_pct_A", "T1_smoker_pct_B",
    "T2_m6_assessed_A", "T2_m6_assessed_B", "T2_goal_n_A", "T2_goal_n_B", "T2_goal_pct_A",
    "T2_goal_pct_B", "T2_hypo_pct_A", "T2_hypo_pct_B",
    "T3_crude_rd", "T3_mh_rd", "T3_mh_or", "T3_std_pct_A", "T3_std_pct_B",
    "FIG_hba1c_m0_A", "FIG_hba1c_m0_B", "FIG_hba1c_m3_A", "FIG_hba1c_m3_B", "FIG_hba1c_m6_A",
    "FIG_hba1c_m6_B", "FIG_hba1c_m12_A", "FIG_hba1c_m12_B",
    # added by amendment 9 (clarified by committee meetings 7 and 8)
    "T1_egfr_mean_A", "T1_egfr_mean_B", "T2_hba1c_change_A", "T2_hba1c_change_B",
    "T2_composite_pct_A", "T2_composite_pct_B", "T2_ldl_m12_mean_A", "T2_ldl_m12_mean_B",
    "T3_crude_rr", "T3_mh_rr",
]
COUNT_KEYS = {k for k in KEYS if k.startswith(("F_", "N_")) or k.endswith(("assessed_A", "assessed_B"))
              or "_goal_n_" in k}
DECIMALS = {k: (1 if "pct" in k else 3 if k in ("T3_mh_or", "T3_crude_rr", "T3_mh_rr") else 2)
            for k in KEYS if k not in COUNT_KEYS}
CANON_ORDER = ("dup", "consent", "site5", "no_v0", "age", "no_hba1c", "creat")
DEFAULT = {"corrections": True, "units": True, "amend_threshold": True, "amend_site5": True,
           "amend_window12": True, "amend_creat": True, "dedupe": True, "order": CANON_ORDER,
           "age_at": "baseline", "replicates": "mean", "winsor": True,
           "late_supp": False, "cancel_ignored": False, "change_sign": 1, "composite_days": 196,
           "rr_inverse": False, "ldl_half12": 30, "egfr_creat": "mgdl", "letters": False}


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _winsor(xs: list[float]) -> list[float]:
    s = sorted(xs)
    n = len(s)
    lo = s[math.ceil(0.01 * n) - 1]
    hi = s[math.ceil(0.99 * n) - 1]
    return [min(hi, max(lo, x)) for x in xs]


def ckd_epi(scr_mgdl: float, age_years: int, female: bool) -> float:
    """eGFR by CKD-EPI 2021 (race-free), ml/min/1.73 m²."""
    kappa, alpha = (0.7, -0.241) if female else (0.9, -0.302)
    ratio = scr_mgdl / kappa
    value = 142 * min(ratio, 1.0) ** alpha * max(ratio, 1.0) ** -1.2 * 0.9938 ** age_years
    return value * 1.012 if female else value


def analyze(opt_over: dict | None = None) -> dict[str, float]:
    W = build_world()
    o = {**DEFAULT, **(opt_over or {})}
    C = o["corrections"]
    records = W["people"] + W["ghosts"]
    enroll = {p.pid: (p.enroll if C else p.enroll_exp) for p in records}
    birth = {p.pid: (p.birth if C else p.birth_exp) for p in records}
    sex = {p.pid: (p.sex if C else p.sex_exp) for p in records}
    withdrawn = {p.pid: (p.withdrawn if C else p.withdrawn_exp) for p in records}
    letter_weight: dict[int, float] = {}
    if C and o["late_supp"]:  # (wrong) apply supplements that arrived after the database lock
        for e in W["corrections"]:
            if "late_supp" in e:
                birth[e["pid"]] = e["late_supp"]
            if e.get("cancel_late"):
                withdrawn[e["pid"]] = False
            if e["kind"] == "late_sex":
                sex[e["pid"]] = e["new"]
            if e["kind"] == "late_birth":
                birth[e["pid"]] = e["new"]
    if o["letters"]:  # (wrong) apply claims from site correspondence that never reached the log
        v0vis = {v.pid: v for v in W["visits"] if v.code == "V0"}
        for e in site_letters():
            if e["kind"] == "birth":
                birth[e["pid"]] = e["new"]
            elif e["kind"] == "consent":
                withdrawn[e["pid"]] = True
            elif e["kind"] == "sex":
                sex[e["pid"]] = e["new"]
            else:
                letter_weight[v0vis[e["pid"]].vid] = e["new"]
    if C and o["cancel_ignored"]:  # (wrong) every logged withdrawal counts, cancellations ignored
        for e in W["corrections"]:
            if e["kind"] == "consent":
                withdrawn[e["pid"]] = True
    site = {p.pid: p.site for p in records}
    smoke = {p.pid: (p.smoking if C else p.smoking_exp) for p in records}
    group_of = {p.pid: (p.group if C else p.group_exp) for p in records}
    height_cm = {p.pid: (p.height_cm if C else p.height_exp) for p in records}
    v0date: dict[str, dt.date] = {}
    v0weight: dict[str, float] = {}
    vis_by: dict[str, list[tuple[dt.date, int]]] = {}
    for v in W["visits"]:
        day = v.date if C else v.date_exp
        if v.code == "V0":
            v0date[v.pid] = day
            v0weight[v.pid] = letter_weight.get(v.vid, v.weight if C else v.weight_exp)
        vis_by.setdefault(v.pid, []).append((day, v.hypo if C else v.hypo_exp))

    labs: dict[tuple[str, str], dict[dt.date, list[float]]] = {}
    for lab in W["labs"]:
        if C:
            native, unit = lab.native, lab.unit
        else:
            native = lab.value_exp
            unit = {"HBA1C": hba1c_unit, "LDL": lambda s, d: ldl_unit(s),
                    "CREAT": lambda s, d: creat_unit(s)}[lab.test](site[lab.pid], lab.date)
        if lab.test == "HBA1C":
            val = lab_to_pct(native, unit) if o["units"] else native
            if not 3.0 <= val <= 20.0:
                continue
        elif lab.test == "LDL":
            val = native / LDL_MGDL if (unit == "mgdl" and o["units"]) else native
        else:
            val = native * CREAT_MGDL if (unit == "mgdl" and o["units"]) else native
        labs.setdefault((lab.pid, lab.test), {}).setdefault(lab.date, []).append(val)

    def day_value(vals: list[float]) -> float:
        return _mean(vals) if o["replicates"] == "mean" else vals[0]

    def baseline_lab(pid: str, test: str) -> float | None:
        base = v0date[pid]
        days = [d for d in labs.get((pid, test), {}) if -30 <= (d - base).days <= 0]
        if not days:
            return None
        return day_value(labs[(pid, test)][max(days)])

    def window_lab(pid: str, test: str, target: int, half: int) -> float | None:
        base = v0date[pid]
        days = [d for d in labs.get((pid, test), {}) if abs((d - base).days - target) <= half]
        if not days:
            return None
        best = min(days, key=lambda d: (abs((d - base).days - target), d))
        return day_value(labs[(pid, test)][best])

    def age(pid: str) -> int:
        ref = v0date[pid] if o["age_at"] == "baseline" else enroll[pid]
        return full_years(birth[pid], ref)

    creat_lim = 200.0 if o["amend_creat"] else 177.0
    tests = {
        "dup": None,
        "consent": lambda pid: withdrawn[pid],
        "site5": (lambda pid: site[pid] == 5 and enroll[pid] < SITE5_CUTOFF) if o["amend_site5"] else (lambda pid: False),
        "no_v0": lambda pid: pid not in v0date,
        "age": lambda pid: not 18 <= age(pid) <= 79,
        "no_hba1c": lambda pid: baseline_lab(pid, "HBA1C") is None,
        "creat": lambda pid: (lambda c: c is not None and c > creat_lim)(baseline_lab(pid, "CREAT")),
    }
    alive = [p.pid for p in records]
    res: dict[str, float] = {}
    keyname = {"dup": "F_dup_removed", "consent": "F_excl_consent", "site5": "F_excl_site5",
               "no_v0": "F_excl_no_v0", "age": "F_excl_age", "no_hba1c": "F_excl_no_hba1c",
               "creat": "F_excl_creat"}
    for step in o["order"]:
        if step == "dup":
            if not o["dedupe"]:
                res["F_dup_removed"] = 0
                continue
            first: dict[str, str] = {}
            nid = {p.pid: p.nid for p in records}
            for pid in sorted(alive, key=lambda x: (enroll[x], x)):
                first.setdefault(nid[pid], pid)
            keep = set(first.values())
            res["F_dup_removed"] = sum(pid not in keep for pid in alive)
            alive = [pid for pid in alive if pid in keep]
            continue
        f = tests[step]
        # age/lab tests need a V0 date; a participant without V0 cannot be judged -> not excluded here
        bad = [pid for pid in alive if (step in ("consent", "site5", "no_v0") or pid in v0date) and f(pid)]
        res[keyname[step]] = len(bad)
        bs = set(bad)
        alive = [pid for pid in alive if pid not in bs]
    alive = [pid for pid in alive if pid in v0date]
    grp = {pid: ("A" if g == 1 else "B") for pid, g in group_of.items()}
    groups = {g: [pid for pid in alive if grp[pid] == g] for g in "AB"}
    res["N_A"], res["N_B"] = len(groups["A"]), len(groups["B"])

    # Table 1
    height = {pid: h / 100 for pid, h in height_cm.items()}
    if not o["units"]:
        height = {pid: (h / 100 if site[pid] != 4 else h / 10000) for pid, h in height_cm.items()}
    bmi = {pid: v0weight[pid] / height[pid] ** 2 for pid in alive if v0weight.get(pid, -99) != -99}
    ldl = {pid: v for pid in alive if (v := baseline_lab(pid, "LDL")) is not None}
    if o["winsor"]:
        for dct in (bmi, ldl):
            ks = list(dct)
            for k2, v in zip(ks, _winsor([dct[k] for k in ks]), strict=True):
                dct[k2] = v
    for g in "AB":
        ids = groups[g]
        res[f"T1_age_mean_{g}"] = _mean([age(pid) for pid in ids])
        res[f"T1_female_pct_{g}"] = 100 * sum(sex[pid] == 2 for pid in ids) / len(ids)
        res[f"T1_bmi_mean_{g}"] = _mean([bmi[pid] for pid in ids if pid in bmi])
        res[f"T1_ldl_mean_{g}"] = _mean([ldl[pid] for pid in ids if pid in ldl])
        known = [pid for pid in ids if smoke[pid] in (0, 1, 2)]
        res[f"T1_smoker_pct_{g}"] = 100 * sum(smoke[pid] == 2 for pid in known) / len(known)

    # Table 2 and figure
    thr = 7.0 if o["amend_threshold"] else 7.5
    w12 = 30 if o["amend_window12"] else 14
    m6 = {pid: v for pid in alive if (v := window_lab(pid, "HBA1C", 182, 14)) is not None}
    for g in "AB":
        ids = groups[g]
        ass = [pid for pid in ids if pid in m6]
        goal = [pid for pid in ass if m6[pid] < thr]
        res[f"T2_m6_assessed_{g}"] = len(ass)
        res[f"T2_goal_n_{g}"] = len(goal)
        res[f"T2_goal_pct_{g}"] = 100 * len(goal) / len(ass)
        hypo = 0
        for pid in ids:
            base = v0date[pid]
            if any(1 <= (d - base).days <= 196 and h != -99 and h >= 1 for d, h in vis_by.get(pid, [])):
                hypo += 1
        res[f"T2_hypo_pct_{g}"] = 100 * hypo / len(ids)
        res[f"FIG_hba1c_m0_{g}"] = _mean([baseline_lab(pid, "HBA1C") for pid in ids])
        for key, tgt, half in (("m3", 91, 14), ("m6", 182, 14), ("m12", 365, w12)):
            vals = [v for pid in ids if (v := window_lab(pid, "HBA1C", tgt, half)) is not None]
            res[f"FIG_hba1c_{key}_{g}"] = _mean(vals)

    # Table 3: strata age group x sex among assessed
    def stratum(pid: str) -> tuple[int, int]:
        a = age(pid)
        return (0 if a < 50 else 1 if a < 65 else 2, sex[pid])

    strata: dict[tuple[int, int], dict[str, list[int]]] = {}
    for g in "AB":
        for pid in groups[g]:
            if pid in m6:
                cell = strata.setdefault(stratum(pid), {"A": [0, 0], "B": [0, 0]})
                cell[g][0] += m6[pid] < thr
                cell[g][1] += 1
    num_rd = den_rd = num_or = den_or = 0.0
    total = sum(c["A"][1] + c["B"][1] for c in strata.values())
    std = {"A": 0.0, "B": 0.0}
    for c in strata.values():
        a, n1 = c["A"]
        cc, n0 = c["B"]
        b, d = n1 - a, n0 - cc
        nk = n1 + n0
        num_rd += (a * n0 - cc * n1) / nk
        den_rd += n1 * n0 / nk
        num_or += a * d / nk
        den_or += b * cc / nk
        w = nk / total
        std["A"] += w * a / n1
        std["B"] += w * cc / n0
    res["T3_crude_rd"] = res["T2_goal_pct_A"] - res["T2_goal_pct_B"]
    res["T3_mh_rd"] = 100 * num_rd / den_rd
    res["T3_mh_or"] = num_or / den_or
    res["T3_std_pct_A"] = 100 * std["A"]
    res["T3_std_pct_B"] = 100 * std["B"]

    # Amendment 9: risk ratios (A relative to B), crude and Mantel-Haenszel over the same strata
    num_rr = den_rr = 0.0
    for c in strata.values():
        a, n1 = c["A"]
        cc, n0 = c["B"]
        nk = n1 + n0
        num_rr += a * n0 / nk
        den_rr += cc * n1 / nk
    p_a = res["T2_goal_n_A"] / res["T2_m6_assessed_A"]
    p_b = res["T2_goal_n_B"] / res["T2_m6_assessed_B"]
    res["T3_crude_rr"] = p_b / p_a if o["rr_inverse"] else p_a / p_b
    res["T3_mh_rr"] = den_rr / num_rr if o["rr_inverse"] else num_rr / den_rr

    creat = {pid: v for pid in alive if (v := baseline_lab(pid, "CREAT")) is not None}
    egfr = {pid: ckd_epi(creat[pid] / CREAT_MGDL if o["egfr_creat"] == "mgdl" else creat[pid],
                         age(pid), sex[pid] == 2) for pid in creat}
    for g in "AB":
        ids = groups[g]
        ass = [pid for pid in ids if pid in m6]
        res[f"T1_egfr_mean_{g}"] = _mean([egfr[pid] for pid in ids if pid in egfr])
        res[f"T2_hba1c_change_{g}"] = _mean([o["change_sign"] * (m6[pid] - baseline_lab(pid, "HBA1C"))
                                             for pid in ass])
        comp = 0
        for pid in ass:
            base = v0date[pid]
            hypo = any(1 <= (d - base).days <= o["composite_days"] and h != -99 and h >= 1
                       for d, h in vis_by.get(pid, []))
            comp += m6[pid] < thr and not hypo
        res[f"T2_composite_pct_{g}"] = 100 * comp / len(ass)
        ldl12 = [v for pid in ids if (v := window_lab(pid, "LDL", 365, o["ldl_half12"])) is not None]
        res[f"T2_ldl_m12_mean_{g}"] = _mean(ldl12)
    return {k: res[k] for k in KEYS}


def rounded(res: dict[str, float]) -> dict[str, float | int]:
    out: dict[str, float | int] = {}
    for k in KEYS:
        if k in COUNT_KEYS:
            out[k] = int(res[k])
        else:
            q = Decimal(1).scaleb(-DECIMALS[k])
            out[k] = float(Decimal(repr(res[k])).quantize(q, rounding=ROUND_HALF_UP))
    return out


# ==========================================================================
# Export (per-site CSV files, site-specific formats)
# ==========================================================================

LAB_CODES = {4: {"HBA1C": "A1C", "LDL": "LDLC", "CREAT": "CREA"}}
DMY_SITES = (3, 5)


def _fmt_date(site: int, d: dt.date) -> str:
    return d.strftime("%d.%m.%Y") if site in DMY_SITES else d.isoformat()


def _fmt_num(x: float) -> str:
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.2f}".rstrip("0").rstrip(".")


@functools.cache
def export_files() -> dict[str, list]:
    """Rendered CSV tables: rel path -> (header, rows)."""
    W = build_world()
    r = random.Random(f"{TASK_ID}/export")
    records = sorted(W["people"] + W["ghosts"], key=lambda p: (p.site, int(p.pid[3:])))
    site_of = {p.pid: p.site for p in records}
    out: dict[str, list] = {}
    for site in SITES:
        rows = []
        for p in records:
            if p.site != site:
                continue
            height = f"{p.height_exp / 100:.2f}" if site == 4 else str(p.height_exp)
            rows.append([p.pid, site, _fmt_date(site, p.enroll_exp), _fmt_date(site, p.birth_exp), p.sex_exp,
                         height, p.smoking_exp, p.group_exp, int(p.withdrawn_exp), p.nid])
        out[f"data/site{site}/participants.csv"] = [
            ["participant_id", "site", "enrol_date", "birth_date", "sex", "height", "smoking", "therapy",
             "consent_withdrawn", "nid_hash"], rows]
    vis_rows: dict[str, list] = {}
    for v in sorted(W["visits"], key=lambda v: (v.date_exp, v.vid)):
        site = site_of[v.pid]
        half = "H1" if v.date_exp.month <= 6 else "H2"
        rel = f"data/site{site}/visits_{v.date_exp.year}{half}.csv"
        row = [f"V{v.vid:06d}", v.pid, v.code, _fmt_date(site, v.date_exp),
               _fmt_num(v.weight_exp) if v.weight_exp != -99 else "-99", v.sbp, v.hypo_exp]
        vis_rows.setdefault(rel, []).append(row)
        if r.random() < 0.02:
            vis_rows[rel].append(list(row))
    for rel, rows in vis_rows.items():
        out[rel] = [["visit_id", "participant_id", "visit_code", "visit_date", "weight_kg", "sbp_mmhg",
                     "hypo_events"], rows]
    lab_rows: dict[str, list] = {}
    for lab in sorted(W["labs"], key=lambda x: (x.date, x.lid)):
        site = site_of[lab.pid]
        q = (lab.date.month - 1) // 3 + 1
        rel = f"data/site{site}/lab_{lab.date.year}Q{q}.csv"
        code = LAB_CODES.get(site, {}).get(lab.test, lab.test)
        lab_rows.setdefault(rel, []).append([f"S{lab.lid:07d}", lab.pid, _fmt_date(site, lab.date), code,
                                             _fmt_num(lab.value_exp)])
    for rel, rows in lab_rows.items():
        out[rel] = [["sample_id", "participant_id", "sample_date", "test_code", "result"], rows]
    return out


# ==========================================================================
# Paper text
# ==========================================================================

ABSTRACT = """# Достижение целевого уровня HbA1c при терапии глиптазином и флозироном у взрослых с сахарным диабетом 2 типа: многоцентровое проспективное когортное исследование ГЛИКОНТ

Рабочая версия рукописи для воспроизведения результатов. Числовые значения в разделе
«Результаты» и в таблицах удалены и заменены ссылками на ключи файла `results_template.json`.

## Резюме

**Обоснование.** Выбор второго сахароснижающего препарата после метформина у взрослых с
сахарным диабетом 2 типа (СД2) в реальной клинической практике определяется не только
рекомендациями, но и доступностью препаратов, сопутствующими заболеваниями и предпочтениями
пациента. Прямые сравнения ингибитора дипептидилпептидазы-4 глиптазина и ингибитора
натрий-глюкозного котранспортёра 2 типа флозирона в российской популяции немногочисленны.

**Цель.** Сравнить долю пациентов, достигших целевого уровня гликированного гемоглобина
(HbA1c) через 6 месяцев после начала терапии глиптазином (группа А) или флозироном
(группа Б), с поправкой на возраст и пол.

**Методы.** Проспективное наблюдательное когортное исследование в шести центрах (Казань,
Екатеринбург, Томск, Самара, Ярославль, Новосибирск), включение с марта 2020 г. по июнь
2022 г., наблюдение до 12 месяцев. Визиты: базовый (V0), через 3 (V1), 6 (V2) и 12 (V3)
месяцев, а также внеплановые. Лабораторные показатели определялись в местных лабораториях и
гармонизировались по единым правилам. Первичная конечная точка — достижение целевого HbA1c на
визите 6 месяцев. Различия долей оценивались как нескорректированная разность рисков и
разность рисков по Мантелю–Хензелю со стратификацией по возрастной группе и полу; доли
стандартизованы прямым методом.

**Результаты.** В анализ включено [N_A] пациентов группы А и [N_B] пациентов группы Б.
Целевого уровня HbA1c через 6 месяцев достигли [T2_goal_pct_A] % и [T2_goal_pct_B] %
соответственно; скорректированная разность рисков составила [T3_mh_rd] процентного пункта,
отношение шансов по Мантелю–Хензелю — [T3_mh_or].

**Заключение.** Результаты будут сформулированы после воспроизведения расчётов.

**Ключевые слова:** сахарный диабет 2 типа; гликированный гемоглобин; ингибиторы ДПП-4;
ингибиторы НГЛТ-2; когортное исследование; стандартизация; метод Мантеля–Хензеля.

## Как читать эту рукопись

Рукопись разбита на файлы:

* `01_introduction.md` — введение;
* `02_methods_design.md` — дизайн, центры, участники, группы сравнения;
* `03_methods_measurements.md` — визиты, измерения, лабораторные показатели, исходы;
* `04_methods_statistics.md` — аналитическая выборка, порядок исключений, статистические
  методы, правила округления;
* `05_results.md` — структура результатов, таблицы и рисунки с ключами;
* `06_discussion.md` — обсуждение;
* `appendix_A_amendments.md` — поправки к протоколу;
* `appendix_B_laboratories.md` — лабораторные методы и единицы измерения по центрам;
* `appendix_C_data_queries.md` и продолжения `appendix_C_data_queries_part*.md` — журнал
  запросов на уточнение данных и исправления (несколько частей по кварталам);
* `appendix_D_committee.md` — выдержки из протоколов заседаний руководящего комитета;
* `appendix_E_site_reports.md` — итоговые отчёты мониторинга по центрам;
* `appendix_F_sap_v1.md` — план статистического анализа в исходной редакции 1.0;
* `appendix_G_worked_examples.md` — учебные примеры применения правил;
* `appendix_H_edit_checks.md` — проверки при вводе данных в ЭИРК;
* `appendix_I_monitoring_visits.md` — выписки из протоколов мониторинговых визитов;
* `appendix_J_derived_variables.md` — спецификация производных переменных;
* `appendix_K_analyst_questions.md` — вопросы второго аналитика и ответы статистика;
* `appendix_L_site_correspondence.md` — выдержки из переписки с центрами;
* `../codebook/*.md` — описание файлов данных, в том числе манифест выгрузки.

Разделы «Методы» написаны по исходной версии протокола (версия 1.0 от 14.01.2020).
Поправки к протоколу, принятые до закрытия базы данных, изложены в приложении А и **имеют
приоритет** над текстом разделов «Методы». Уточнения, принятые руководящим комитетом
(приложение D), и примечания к таблицам являются частью методики анализа.
"""

INTRO = """# 1. Введение

Сахарный диабет 2 типа (СД2) остаётся одной из ведущих причин инвалидизации и
преждевременной смертности взрослого населения. По данным федерального регистра, число
пациентов с СД2 в Российской Федерации за последние десять лет увеличилось более чем в
полтора раза, причём значительная доля пациентов не достигает индивидуальных целевых
показателей гликемического контроля. Недостаточный контроль гликемии ассоциирован с
увеличением риска микрососудистых осложнений — ретинопатии, нефропатии, нейропатии, — а
также с ростом сердечно-сосудистой заболеваемости.

Гликированный гемоглобин (HbA1c) отражает среднюю концентрацию глюкозы в крови за
предшествующие 2–3 месяца и служит основным показателем для оценки эффективности
сахароснижающей терапии. Клинические рекомендации предписывают устанавливать
индивидуальный целевой уровень HbA1c с учётом возраста, ожидаемой продолжительности жизни,
наличия тяжёлых осложнений и риска гипогликемий. Для большинства взрослых пациентов без
выраженных осложнений в качестве ориентира используется уровень менее 7,0 %; для пожилых и
пациентов с высоким риском гипогликемий допускаются более мягкие цели. В период
планирования настоящего исследования в ряде региональных протоколов в качестве
популяционного ориентира использовался порог 7,5 %, что нашло отражение в первой версии
протокола (см. приложение А).

Метформин остаётся препаратом первой линии, однако у значительной части пациентов со
временем требуется интенсификация терапии. В качестве второго препарата в реальной практике
наиболее часто назначаются ингибиторы дипептидилпептидазы-4 (иДПП-4) и ингибиторы
натрий-глюкозного котранспортёра 2 типа (иНГЛТ-2). Препараты этих классов различаются по
механизму действия, влиянию на массу тела, почечные и сердечно-сосудистые исходы, а также по
профилю нежелательных явлений. Рандомизированные исследования показали сопоставимое или
несколько более выраженное снижение HbA1c на фоне иНГЛТ-2, однако участники таких
исследований отбираются по строгим критериям и не всегда отражают популяцию реальной
клинической практики.

Наблюдательные исследования позволяют оценить эффективность терапии в широкой популяции, но
требуют аккуратного учёта различий между группами сравнения. В частности, иНГЛТ-2 чаще
назначают более молодым пациентам и женщинам, а иДПП-4 — пожилым пациентам со сниженной
функцией почек. Поэтому сравнение нескорректированных долей достижения цели может быть
смещено; в настоящем исследовании для учёта различий по возрасту и полу используются
стратифицированные методы — оценка общего эффекта по Мантелю–Хензелю и прямая
стандартизация. Эти методы прозрачны, легко воспроизводимы и не требуют допущений
регрессионных моделей о форме зависимости.

Отдельную методологическую задачу в многоцентровых наблюдательных исследованиях
представляет гармонизация лабораторных данных. Лаборатории центров используют разные
анализаторы и методы, а результаты HbA1c могут выражаться как в процентах (единицы NGSP/DCCT),
так и в ммоль/моль (единицы IFCC). Кроме того, центры могут менять лабораторные
информационные системы в ходе исследования. Мы подробно описываем правила гармонизации в
приложении B, поскольку от них напрямую зависит отнесение пациента к достигшим или не
достигшим целевого уровня.

Мы также уделили внимание прозрачности формирования аналитической выборки: порядок
применения критериев исключения влияет на числа, приводимые на блок-схеме (рис. 1), хотя
итоговая выборка от порядка не зависит. Поэтому порядок исключений зафиксирован в плане
статистического анализа.

## 1.1. Цель исследования

Основная цель исследования ГЛИКОНТ — сравнить долю взрослых пациентов с СД2, достигших
целевого уровня HbA1c через 6 месяцев после начала терапии глиптазином (иДПП-4, группа А)
или флозироном (иНГЛТ-2, группа Б) в дополнение к метформину, с поправкой на возраст и пол.

Дополнительные цели:

1. описать исходные характеристики пациентов, которым назначают каждый из препаратов;
2. описать динамику среднего HbA1c через 3, 6 и 12 месяцев;
3. оценить частоту гипогликемических эпизодов в первые полгода терапии (добавлено
   поправкой 1, см. приложение А);
4. оценить влияние стандартизации по возрасту и полу на результаты сравнения.

## 1.2. Гипотеза

Мы предполагали, что доля пациентов, достигших целевого уровня HbA1c через 6 месяцев, в
группе флозирона будет выше, чем в группе глиптазина, и что различие сохранится после
поправки на возраст и пол. Исследование не было рассчитано на проверку гипотезы о
не меньшей эффективности, поэтому основное внимание уделяется оценке величины различий, а не
проверке значимости.

## 1.3. Место исследования среди других работ

Ранее в регистровых исследованиях сравнивали изменения HbA1c на фоне иДПП-4 и иНГЛТ-2,
однако большинство из них использовало данные одной лаборатории или не описывало правил
приведения единиц. Многоцентровые когорты с централизованным мониторингом данных, журналом
запросов и формальной процедурой внесения исправлений в отечественной литературе
немногочисленны. Настоящая работа, помимо клинического вопроса, демонстрирует полный путь
от сырых выгрузок центров до итоговых таблиц, что позволяет независимым группам
воспроизвести результаты по опубликованным данным.
"""

METHODS_DESIGN = """# 2. Методы: дизайн исследования и участники

## 2.1. Дизайн

ГЛИКОНТ — проспективное наблюдательное многоцентровое когортное исследование. Решение о
назначении глиптазина или флозирона принимал лечащий врач в рамках обычной клинической
практики до включения пациента в исследование; протокол не предусматривал вмешательств,
кроме стандартизованного графика визитов и лабораторных исследований. Исследование
проведено в соответствии с Хельсинкской декларацией и одобрено локальными этическими
комитетами всех центров. Все участники подписали информированное согласие; участник мог
отозвать согласие в любой момент без объяснения причин.

## 2.2. Центры

В исследовании участвовали шесть центров (табл. М1). Каждому центру присвоен двузначный
номер, который является префиксом идентификатора участника: например, участник `03-0147`
включён в центре 3.

Таблица М1. Центры-участники

| № | Город | Учреждение |
|---|---|---|
""" + "".join(f"| {k} | {v[0]} | {v[1]} |\n" for k, v in SITES.items()) + """
Центры различаются по организации лабораторной службы: в каждом центре исследования
выполнялись местной лабораторией, а не центральной. Используемые единицы измерения и
особенности выгрузки описаны в приложении B и в кодовой книге; эти различия необходимо
учитывать при объединении данных.

## 2.3. Участники

Включение проводилось с 1 марта 2020 г. по 30 июня 2022 г. В исследование включали
последовательно обратившихся пациентов, которым лечащий врач назначил глиптазин или
флозирон.

**Критерии включения** (версия протокола 1.0):

1. возраст от 18 до 79 полных лет включительно;
2. установленный диагноз СД2;
3. приём метформина в стабильной дозе не менее 3 месяцев;
4. назначение глиптазина или флозирона в качестве второго препарата;
5. подписанное информированное согласие.

**Критерии невключения и исключения из анализа** (версия протокола 1.0):

1. тяжёлое нарушение функции почек, определяемое по концентрации креатинина сыворотки на
   базовом обследовании выше 177 мкмоль/л (2,0 мг/дл);
2. отсутствие результата HbA1c на базовом обследовании;
3. отсутствие базового визита V0;
4. отзыв информированного согласия;
5. повторная регистрация одного и того же пациента (дубликат записи).

Перечень выше приведён в порядке, принятом в протоколе, и **не является порядком применения
критериев при формировании аналитической выборки**. Порядок применения исключений и
количества, приводимые на блок-схеме, определены в плане статистического анализа
(раздел 4.2) с учётом поправок к протоколу (приложение А).

Возрастной критерий проверялся по возрасту **на дату базового визита V0**, а не на дату
включения (подписания согласия). Между включением и базовым визитом в большинстве центров
проходило от нескольких дней до трёх недель, поэтому возраст на эти две даты у части
пациентов различается.

## 2.4. Группы сравнения

Группа определяется по препарату, назначенному на базовом визите: код терапии `1` —
глиптазин (группа А), код `2` — флозирон (группа Б). Переключение между препаратами после
базового визита в анализе не учитывалось (анализ по исходно назначенной терапии). Пациенты,
у которых на базовом визите были назначены оба препарата, в исследование не включались.

## 2.5. Наблюдение

Для каждого пациента планировались визиты: базовый (V0), через 3 месяца (V1), через 6
месяцев (V2) и через 12 месяцев (V3). Помимо этого, по клиническим показаниям проводились
внеплановые визиты (код `U`). Даты визитов отсчитываются от даты базового визита V0.
Фактические даты визитов и забора крови могли отличаться от плановых; правила отнесения
измерений к временным точкам описаны в разделе 3.3.

Наблюдение завершалось через 12 месяцев после базового визита, при отзыве согласия или
при утрате связи с пациентом. Последний визит в исследовании состоялся в августе 2023 г.

## 2.6. Управление данными

Данные вносились в электронные индивидуальные регистрационные карты центров, после чего
выгружались в виде отдельных файлов по каждому центру: файл участников, файлы визитов по
полугодиям и файлы лабораторных результатов по кварталам (см. кодовую книгу). Форматы
выгрузок центров не были полностью унифицированы: в частности, различаются формат дат,
коды лабораторных тестов и единицы измерения. Эти различия описаны в кодовой книге и
приложении B.

**Дубликаты.** При повторном обращении пациента в центр в ряде случаев создавалась новая
запись с новым идентификатором вместо продолжения существующей. Для выявления таких
случаев каждому участнику присваивался обезличенный хеш национального идентификатора
(`nid_hash`). Записи с одинаковым `nid_hash` относятся к одному человеку; сохраняется запись с
**наиболее ранней датой включения**, остальные записи исключаются вместе со всеми
относящимися к ним визитами и лабораторными результатами.

**Мониторинг и исправления.** Центральная группа мониторинга направляла центрам запросы на
уточнение данных. Результаты рассмотрения запросов зафиксированы в журнале (приложение C).
Исправления, зарегистрированные в журнале **до закрытия базы данных 16.10.2023
включительно**, считаются частью данных и должны быть применены к выгрузкам (сами выгрузки
сформированы до внесения исправлений и их не содержат). Если по одному запросу в журнале
есть несколько записей, действует последняя по дате. Записи, поступившие после закрытия
базы, в анализ не включаются. Запросы, закрытые с формулировкой «данные подтверждены» или
аналогичной, изменений не влекут. Сведения, которые центры сообщали в переписке (приложение L)
или устно, но которые не оформлены записью журнала, данными не являются и не применяются.

**Отзыв согласия.** Отзыв согласия отмечается в поле `consent_withdrawn` файла участников.
Кроме того, часть форм отзыва согласия поступила в центральную группу после формирования
выгрузок; такие случаи отражены в журнале запросов и учитываются наравне с отметкой в
выгрузке (при регистрации до закрытия базы).

## 2.7. Этические аспекты

Протокол одобрен локальными этическими комитетами всех шести центров. Данные, переданные в
центральную группу, обезличены: вместо ФИО используются идентификаторы участников, вместо
паспортных данных — хеш национального идентификатора. Персональные данные, позволяющие
установить личность участника, в центральную базу не передавались.
"""

METHODS_MEASURE = """# 3. Методы: визиты, измерения и исходы

## 3.1. Базовый визит

Базовый визит V0 проводился в день начала терапии исследуемым препаратом или в ближайшие
дни после него. **Дата базового визита — дата визита с кодом `V0` в файле визитов** — служит
началом отсчёта времени для всех последующих измерений: «день наблюдения» измерения или
визита равен разности между его датой и датой V0 в сутках (день V0 — это день 0, следующий
день — день 1, предыдущий — день −1).

Дата включения (`enrol_date`, дата подписания согласия) используется только для выявления
дубликатов (раздел 2.6) и для критерия поправки 3 (приложение А). Для расчёта возраста,
отнесения измерений к временным точкам и определения исходов используется дата V0.

## 3.2. Демографические и антропометрические показатели

**Возраст** рассчитывается в полных годах на дату базового визита V0 по дате рождения
(`birth_date`): число полных лет, исполнившихся к этой дате. Если день рождения приходится
на дату V0, год считается исполнившимся.

**Пол** кодируется в файле участников: `1` — мужской, `2` — женский.

**Индекс массы тела (ИМТ)** рассчитывается как масса тела на визите V0 (кг), делённая на
квадрат роста (м). Масса тела берётся из файла визитов (поле `weight_kg` записи V0), рост —
из файла участников (поле `height`). Рост в выгрузках большинства центров указан в
сантиметрах; исключения описаны в кодовой книге. Если масса тела на визите V0 не измерена,
ИМТ считается отсутствующим, и пациент не учитывается при расчёте среднего ИМТ.

**Статус курения** на момент включения кодируется так: `0` — никогда не курил, `1` — бывший
курильщик, `2` — курит в настоящее время, `9` — нет данных.¹

¹ *Примечание к табл. 1.* Курящими считаются только участники с кодом `2` (курят в
настоящее время); бывшие курильщики (код `1`) относятся к некурящим. Участники с кодом `9`
исключаются из знаменателя доли курящих.

## 3.3. Лабораторные показатели и временные точки

Определялись гликированный гемоглобин (HbA1c), холестерин липопротеинов низкой плотности
(ХС-ЛНП) и креатинин сыворотки. Кровь забиралась натощак в дни визитов или в ближайшие дни;
базовые анализы, как правило, выполнялись в течение месяца до визита V0.

### 3.3.1. Гармонизация единиц

Результаты анализируются в единых единицах: HbA1c — в процентах (NGSP/DCCT), ХС-ЛНП — в
ммоль/л, креатинин — в мкмоль/л. В выгрузках центров единицы измерения **не указаны**:
результат записан в тех единицах, в которых его выдавала лаборатория центра в дату забора.
Единицы по центрам и периодам приведены в приложении B. Пересчёт выполняется так:

* HbA1c из ммоль/моль (IFCC) в % (NGSP): `HbA1c(%) = 0,09148 × HbA1c(ммоль/моль) + 2,152`.
  Полученное значение **округляется до одного знака после запятой** (половина округляется
  вверх), как в отчётах лабораторий, выдающих результат в процентах. Значения, изначально
  выданные в процентах, уже имеют один знак после запятой и не изменяются.
* ХС-ЛНП из мг/дл в ммоль/л: деление на 38,67; без округления.
* Креатинин из мг/дл в мкмоль/л: умножение на 88,4; без округления.

**Правдоподобие HbA1c.** Значения HbA1c вне диапазона 3,0–20,0 % (после пересчёта) считаются
ошибочными и исключаются из анализа (как будто измерения не было). Для ХС-ЛНП и креатинина
проверка правдоподобия не проводилась.

**Повторные результаты за один день.** Если для одного участника в один день есть несколько
результатов одного и того же теста (повторные определения, контрольные прогоны), используется
их **среднее арифметическое** — после пересчёта единиц, округления пересчитанных значений
HbA1c и исключения неправдоподобных значений.

### 3.3.2. Базовые значения

Базовым значением показателя считается результат с **наиболее поздней датой** забора в
интервале от дня −30 до дня 0 включительно относительно даты V0. Результаты, полученные после
визита V0 (день 1 и позже), базовыми не считаются, даже если других результатов нет.

### 3.3.3. Временные точки наблюдения

Измерения HbA1c относятся к временным точкам по дню наблюдения:

| Точка | Целевой день | Окно (версия протокола 1.0) |
|---|---|---|
| 3 месяца | 91 | 91 ± 14 дней (дни 77–105) |
| 6 месяцев | 182 | 182 ± 14 дней (дни 168–196) |
| 12 месяцев | 365 | 365 ± 14 дней (дни 351–379) |

Границы окон включаются. Для отнесения используется **дата забора крови**, а не дата визита
и не код визита: результат, полученный на внеплановом визите или в день, отличный от дня
визита, относится к точке, если попадает в её окно. Если в окно попадает несколько
результатов (с разными датами), используется результат, **ближайший к целевому дню**; при
одинаковом удалении двух дат от целевого дня (например, дни 180 и 184) выбирается **более
ранняя** дата (решение руководящего комитета, приложение D, заседание № 4). Если в окне нет ни
одного результата, значение в этой точке отсутствует.

Окно для точки 12 месяцев изменено поправкой 4 (приложение А).

## 3.4. Исходы

### 3.4.1. Первичный исход

Первичный исход — **достижение целевого уровня HbA1c** на точке 6 месяцев: значение HbA1c в
окне 6 месяцев (раздел 3.3.3) **менее 7,5 %** (строго меньше). Порог изменён поправкой 2
(приложение А).

Первичный исход оценивается у пациентов аналитической выборки, для которых есть значение
HbA1c на точке 6 месяцев («оценённые на 6 месяцах»). Пропущенные значения не
восстанавливаются; анализ проводится по доступным данным.

### 3.4.2. Вторичные исходы

* Среднее значение HbA1c на базовом обследовании и на точках 3, 6 и 12 месяцев (рис. 2) — по
  участникам аналитической выборки, у которых есть значение в соответствующей точке.
* Частота гипогликемических эпизодов (добавлено поправкой 1, определение — в приложении А).

### 3.4.3. Регистрация гипогликемий

На каждом визите, включая внеплановые, врач фиксировал число эпизодов гипогликемии, о которых
сообщил пациент с момента предыдущего визита (поле `hypo_events`). Значение `-99` означает,
что информация не собрана.

## 3.5. Ковариаты для стратификации

Для скорректированных оценок используются возрастная группа (менее 50 лет; 50–64 года;
65 лет и старше — по возрасту в полных годах на дату V0) и пол. Комбинации возрастной группы
и пола образуют шесть страт.
"""

METHODS_STATS = """# 4. Методы: статистический анализ

План статистического анализа (ПСА) утверждён руководящим комитетом до закрытия базы данных.
Ниже изложена версия ПСА 1.0; изменения, внесённые поправками к протоколу, приведены в
приложении А и имеют приоритет.

## 4.1. Исходные данные

Анализ выполняется по выгрузкам всех шести центров после:

1. приведения форматов дат и кодов тестов к единому виду (кодовая книга);
2. удаления полностью повторяющихся строк выгрузки визитов (артефакт выгрузки; такая строка
   описывает один и тот же визит и учитывается один раз);
3. применения исправлений из журнала запросов (приложение C), зарегистрированных до закрытия
   базы данных;
4. гармонизации лабораторных единиц (раздел 3.3.1, приложение B).

## 4.2. Формирование аналитической выборки

Аналитическая выборка формируется из всех записей файлов участников путём
**последовательного** применения исключений в следующем порядке:

1. дубликаты записей (раздел 2.6);
2. отзыв информированного согласия (отметка в выгрузке или запись в журнале запросов);
3. отсутствие базового визита V0;
4. возраст на дату V0 вне диапазона 18–79 полных лет;
5. отсутствие базового значения HbA1c (раздел 3.3.2) — в том числе если все результаты в
   базовом интервале оказались неправдоподобными;
6. креатинин сыворотки на базовом обследовании выше порога (раздел 2.3; порог изменён
   поправкой 7).

Каждая запись учитывается на блок-схеме (рис. 1) только **на первом шаге**, на котором она
исключается; на последующих шагах она уже не рассматривается. Поэтому числа на блок-схеме
зависят от порядка шагов, и порядок должен соблюдаться точно. Дополнительный шаг исключения,
введённый поправкой 3, встраивается в эту последовательность так, как указано в самой
поправке.

Пациенты, у которых базовое значение креатинина отсутствует, по критерию креатинина **не
исключаются**. Сравнение с порогом строгое: исключаются значения выше порога, значение,
равное порогу, не является основанием для исключения.

Все оставшиеся записи образуют аналитическую выборку; число пациентов в ней указывается
отдельно для групп А и Б.

## 4.3. Описательная статистика (таблица 1)

Непрерывные показатели описываются средним арифметическим; категориальные — долей в
процентах от числа участников группы с известным значением показателя. Показатели таблицы 1
рассчитываются по аналитической выборке:

* возраст — в полных годах на дату V0;
* доля женщин;
* ИМТ на визите V0;
* ХС-ЛНП — базовое значение (раздел 3.3.2), ммоль/л;
* доля курящих в настоящее время (примечание 1 к разделу 3.2).

**Винзоризация.** Для ограничения влияния экстремальных значений ИМТ и базовый ХС-ЛНП перед
расчётом средних винзоризуются на уровне 1-го и 99-го перцентилей. Перцентили вычисляются
**по всей аналитической выборке (обе группы вместе)** по участникам с известным значением
показателя, методом ближайшего ранга: для n упорядоченных по возрастанию значений
x(1) ≤ … ≤ x(n) p-й перцентиль равен x(k), где k = ⌈p·n/100⌉ (округление вверх до целого).
Значения меньше 1-го перцентиля заменяются значением 1-го перцентиля, значения больше 99-го
перцентиля — значением 99-го перцентиля. После этого рассчитываются средние по группам.
Другие показатели (возраст, HbA1c) не винзоризуются.

## 4.4. Исходы (таблица 2, рисунок 2)

Для первичного исхода в каждой группе приводятся: число оценённых на 6 месяцах, число
достигших целевого уровня и доля достигших (в процентах от оценённых).

Для гипогликемий приводится доля участников аналитической выборки группы, у которых
зарегистрирован хотя бы один эпизод (определение — поправка 1, приложение А), в процентах от
**всех** участников аналитической выборки группы.

На рисунке 2 показано среднее значение HbA1c в каждой группе на базовом обследовании и на
точках 3, 6 и 12 месяцев; среднее рассчитывается по участникам аналитической выборки, у
которых есть значение в соответствующей точке (значения не винзоризуются).

## 4.5. Сравнение групп (таблица 3)

Все сравнения выполняются для первичного исхода среди оценённых на 6 месяцах. Во всех
разностях группа А является сравниваемой, группа Б — референсной: разность рисков равна
«доля в группе А минус доля в группе Б» и выражается в **процентных пунктах**; отношение
шансов — «шансы достижения цели в группе А, делённые на шансы в группе Б».

**Нескорректированная разность рисков** — разность долей достигших цели в группах А и Б
(в процентных пунктах).

**Разность рисков по Мантелю–Хензелю.** Для каждой из шести страт k (раздел 3.5) обозначим:
a_k — число достигших цели в группе А, n1_k — число оценённых в группе А, c_k — число
достигших цели в группе Б, n0_k — число оценённых в группе Б, N_k = n1_k + n0_k. Тогда

    RD_MH = Σ_k (a_k · n0_k − c_k · n1_k) / N_k  ÷  Σ_k (n1_k · n0_k / N_k),

результат умножается на 100 (процентные пункты).

**Отношение шансов по Мантелю–Хензелю.** С обозначениями b_k = n1_k − a_k (не достигшие цели
в группе А) и d_k = n0_k − c_k (не достигшие цели в группе Б):

    OR_MH = Σ_k (a_k · d_k / N_k)  ÷  Σ_k (b_k · c_k / N_k).

**Прямая стандартизация.** Стандартной популяцией служат все оценённые на 6 месяцах участники
обеих групп вместе. Вес страты w_k = N_k / Σ_j N_j. Стандартизованная доля группы
равна Σ_k w_k · p_k, где p_k — доля достигших цели в группе в страте k (a_k / n1_k для группы А,
c_k / n0_k для группы Б); результат выражается в процентах.

Интервальные оценки в настоящей версии рукописи не приводятся.

## 4.6. Правила округления и представления результатов

Все промежуточные вычисления выполняются без округления (исключение — округление
пересчитанных значений HbA1c, раздел 3.3.1). Итоговые значения округляются по правилу
«половина вверх» (от нуля):

* численности (количество участников) — целые числа;
* доли в процентах и стандартизованные доли — до одного знака после запятой;
* средние значения — до двух знаков после запятой;
* разности рисков (процентные пункты) — до двух знаков после запятой;
* отношение шансов — до трёх знаков после запятой.

В файле `results_template.json` для каждого ключа указано, какой величине он соответствует;
в файл результатов записываются числа (не строки), с десятичной точкой.

## 4.7. Программное обеспечение

Расчёты выполнены на языке Python 3 с использованием только стандартной библиотеки;
независимая проверка — вторым аналитиком по тому же ПСА.
"""

AMENDMENTS = """# Приложение А. Поправки к протоколу

Ниже перечислены все поправки к протоколу исследования ГЛИКОНТ (версия 1.0 от 14.01.2020),
рассмотренные руководящим комитетом до закрытия базы данных 16.10.2023. Для каждой поправки
указаны дата решения, содержание, обоснование и статус. Поправки со статусом «действует»
**применяются в анализе и имеют приоритет над текстом разделов 2–4**. Поправки со статусом
«отклонена» или «не влияет на анализ» приведены для полноты.

---

## Поправка 1 (12.11.2020). Вторичный исход «гипогликемия»

**Статус:** действует.

**Содержание.** В перечень вторичных исходов добавляется частота гипогликемий в первые
полгода терапии. Участник считается перенёсшим гипогликемию, если хотя бы на одном визите
(плановом V1, V2, V3 или внеплановом `U`), день наблюдения которого находится в интервале **от 1
до 196 включительно**, зарегистрировано `hypo_events` ≥ 1. Базовый визит V0 (день 0) не
учитывается, поскольку на нём регистрируются эпизоды, произошедшие до начала терапии. Значение
`-99` означает отсутствие информации и не считается эпизодом. Знаменатель — все участники
аналитической выборки соответствующей группы, независимо от того, сколько визитов у них
состоялось.

**Обоснование.** Письмо регулятора о необходимости сбора данных по безопасности иДПП-4 и
иНГЛТ-2 в наблюдательных исследованиях.

## Поправка 2 (07.06.2021). Порог целевого уровня HbA1c

**Статус:** действует.

**Содержание.** Порог достижения целевого уровня HbA1c для первичного исхода изменён: вместо
«менее 7,5 %» (раздел 3.4.1 версии 1.0) используется **«менее 7,0 %»** (строго меньше 7,0 %).
Окно и правила отнесения измерения к точке 6 месяцев не меняются.

**Обоснование.** Приведение в соответствие с обновлёнными национальными клиническими
рекомендациями, в которых для большинства взрослых пациентов без тяжёлых осложнений
рекомендован целевой уровень менее 7,0 %. Сбор данных при этом не менялся, поэтому поправка
применяется ко всем участникам, включая включённых до её принятия.

## Поправка 3 (18.10.2021). Центр 5: повторная сертификация

**Статус:** действует.

**Содержание.** По результатам аудита в центре 5 (Ярославль) выявлены нарушения процедуры
получения информированного согласия и ведения первичной документации в период до повторной
сертификации центра. Все участники центра 5, **дата включения которых (`enrol_date`) раньше
01.09.2021**, исключаются из анализа. Участники центра 5, включённые 01.09.2021 и позже,
остаются в анализе.

Этот критерий применяется при формировании аналитической выборки **сразу после исключения
по отзыву согласия и перед исключением по отсутствию визита V0** (то есть становится
третьим шагом последовательности раздела 4.2, а прежние шаги 3–6 становятся шагами 4–7).
Дата включения берётся с учётом исправлений из журнала запросов.

**Обоснование.** Решение этического комитета центра 5 от 29.09.2021 и заключение аудита.

## Поправка 4 (22.02.2022). Окно для точки 12 месяцев

**Статус:** действует.

**Содержание.** В связи с ограничениями на плановые визиты в ряде центров окно для точки 12
месяцев расширено до **365 ± 30 дней (дни 335–395 включительно)**. Окна для точек 3 и 6
месяцев, а также базовый интервал (дни −30…0) не меняются.

**Обоснование.** Значительная доля визитов V3 проводилась с отклонением от плана более чем на
две недели.

## Поправка 5 (16.05.2022). Состав руководящего комитета

**Статус:** не влияет на анализ.

**Содержание.** В состав руководящего комитета включён представитель центра 6; председатель
комитета переизбран на второй срок. Порядок голосования не менялся.

## Поправка 6 (05.09.2022). Исключение пациентов со сниженной СКФ

**Статус:** отклонена.

**Содержание (предложение).** Предлагалось дополнительно исключать из анализа пациентов с
расчётной скоростью клубочковой фильтрации менее 45 мл/мин/1,73 м² на базовом обследовании.

**Решение.** Предложение отклонено центральным этическим комитетом (протокол от 27.10.2022):
критерий меняет популяцию исследования после начала включения. Поправка **не вступила в
силу**, дополнительного исключения по СКФ нет.

## Поправка 7 (19.01.2023). Порог креатинина

**Статус:** действует.

**Содержание.** Порог креатинина сыворотки для исключения из анализа (раздел 2.3, критерий 1
версии 1.0) изменён с 177 мкмоль/л на **200 мкмоль/л** (2,26 мг/дл). Исключаются участники,
у которых базовый креатинин (после пересчёта в мкмоль/л) **выше 200 мкмоль/л**. Порядок шагов
исключения не меняется.

**Обоснование.** Согласование с критериями, принятыми в международных регистрах, и
рекомендация комитета по безопасности.

## Поправка 8 (03.07.2023). Дистанционные визиты

**Статус:** не влияет на анализ.

**Содержание.** Визит V3 допускается проводить дистанционно с забором крови в ближайшем
пункте лаборатории центра. Такие визиты вносятся в выгрузку с кодом `V3`; правила отнесения
лабораторных результатов к точкам не меняются, так как отнесение выполняется по дате забора.

## Поправка 9 (11.04.2023). Дополнительные показатели для итоговой рукописи

**Статус:** действует (с изменением, принятым руководящим комитетом на заседании № 8,
приложение D).

**Содержание.** По запросу редакции и экспертов этического комитета в итоговые таблицы
добавляются показатели, не предусмотренные версией 1.0. Все они рассчитываются по
аналитической выборке раздела 4.2 (с учётом поправок 3 и 7) и по тем же правилам подготовки
данных (исправления по журналу, гармонизация единиц, усреднение повторных результатов за один
день).

1. **Расчётная скорость клубочковой фильтрации (рСКФ) на базовом обследовании** — таблица 1,
   среднее по группе. Для каждого участника с базовым значением креатинина (раздел 3.3.2)
   рСКФ вычисляется по формуле CKD-EPI 2021 года (без коэффициента расы):

       рСКФ = 142 × min(Scr/κ, 1)^α × max(Scr/κ, 1)^(−1,200) × 0,9938^Возраст × 1,012 [для женщин],

   где Scr — креатинин сыворотки **в мг/дл**, κ = 0,7 для женщин и 0,9 для мужчин, α = −0,241
   для женщин и −0,302 для мужчин, Возраст — в полных годах на дату V0; множитель 1,012
   применяется только для женщин. Поскольку в аналитической базе креатинин хранится в
   мкмоль/л (раздел 3.3.1), для подстановки в формулу его значение **делится на 88,4**.
   Участники без базового креатинина в среднем не учитываются; рСКФ не винзоризуется и не
   округляется до расчёта среднего.
2. **Изменение HbA1c к 6 месяцам** — таблица 2, среднее по группе среди оценённых на 6
   месяцах: значение на точке 6 месяцев **минус** базовое значение (оба — в процентах, по
   правилам разделов 3.3.1–3.3.3). Отрицательное значение означает снижение HbA1c.
3. **Комбинированный исход** — таблица 2: доля оценённых на 6 месяцах, у которых одновременно
   достигнут целевой уровень HbA1c (поправка 2) **и** не зарегистрировано ни одного эпизода
   гипогликемии на визитах с днём наблюдения от 1 до 182 включительно (определение эпизода —
   поправка 1). Знаменатель — оценённые на 6 месяцах участники группы.
4. **ХС-ЛНП на 12 месяцах** — таблица 2, среднее по группе. Отнесение результатов ХС-ЛНП к
   точке 12 месяцев выполняется по тем же правилам, что и для HbA1c: по дате забора, в окне
   точки 12 месяцев, ближайший к 365-му дню результат, при равном удалении — более ранний.
   Среднее рассчитывается по участникам аналитической выборки со значением в окне (без
   винзоризации; винзоризация раздела 4.3 относится только к базовому ХС-ЛНП).
5. **Отношение рисков** — таблица 3, для первичного исхода (А относительно Б):
   нескорректированное — отношение долей достигших цели, (a/n1) ÷ (c/n0), и по
   Мантелю–Хензелю с той же стратификацией, что в разделе 4.5:

       RR_MH = Σ_k (a_k · n0_k / N_k)  ÷  Σ_k (c_k · n1_k / N_k).

   Отношения рисков округляются до трёх знаков после запятой, рСКФ, изменение HbA1c и
   ХС-ЛНП — до двух, доля с комбинированным исходом — до одного (раздел 4.6).

**Обоснование.** Рекомендации рецензентов пилотной публикации; необходимость описать функцию
почек в группах сравнения после отклонения поправки 6.

## Поправка 10 (29.05.2023). Винзоризация ХС-ЛНП на 12 месяцах и перенос наблюдений

**Статус:** отклонена.

**Содержание (предложение).** Предлагалось винзоризовать значения ХС-ЛНП на точке 12 месяцев
по 1-му и 99-му перцентилям, а при отсутствии значения в окне 12 месяцев подставлять последнее
доступное значение ХС-ЛНП после визита V0.

**Решение.** Отклонена руководящим комитетом (заседание № 7): добавленные поправкой 9
показатели рассчитываются по доступным данным, без винзоризации и без подстановки значений.

---

## Сводка действующих изменений

| Раздел версии 1.0 | Было | Стало | Поправка |
|---|---|---|---|
| 3.4.2 | — | исход «гипогликемия» (дни 1–196) | 1 |
| 3.4.1 | HbA1c < 7,5 % | HbA1c < 7,0 % | 2 |
| 4.2 | — | исключение центра 5 до 01.09.2021 (шаг 3) | 3 |
| 3.3.3 | 12 мес.: 365 ± 14 | 12 мес.: 365 ± 30 | 4 |
| 2.3, 4.2 | креатинин > 177 мкмоль/л | креатинин > 200 мкмоль/л | 7 |
| 3.4.2, 4.3–4.5 | — | рСКФ, изменение HbA1c, комбинированный исход, ХС-ЛНП на 12 мес., отношения рисков | 9 (с изменением заседания № 8) |
"""

LABS_APPX = """# Приложение B. Лабораторные методы и единицы измерения по центрам

Лабораторные исследования выполнялись местными лабораториями центров. Все лаборатории
участвовали в федеральной системе внешней оценки качества. В выгрузках центров единицы
измерения не указываются — результат записан так, как его выдала лабораторная
информационная система (ЛИС) центра на дату забора. Ниже для каждого центра приведены
анализаторы, методы и **единицы, в которых записаны результаты**, а также особенности
выгрузки. Сводная таблица единиц — в конце приложения.

## Центр 1 (Казань)

Лаборатория республиканского диабетологического центра. HbA1c определялся методом
высокоэффективной жидкостной хроматографии (ВЭЖХ) на анализаторе класса D-10, результат
выдаётся в **процентах** (NGSP) с одним знаком после запятой. В ноябре 2021 г. анализатор
заменён на модель следующего поколения того же производителя; единицы и формат результата не
изменились, перекрёстная проверка на 40 образцах показала среднее расхождение 0,03 %.
ХС-ЛНП — прямой ферментативный метод, **ммоль/л**. Креатинин — ферментативный метод,
**мкмоль/л**. Даты в выгрузке — в формате ГГГГ-ММ-ДД.

## Центр 2 (Екатеринбург)

Лаборатория областной клинической больницы № 2. HbA1c — иммунотурбидиметрия. До
28.02.2022 включительно ЛИС выдавала HbA1c в **процентах**; с **01.03.2022** лаборатория
перешла на новую ЛИС, и с этой даты (по дате забора) результаты HbA1c выдаются в
**ммоль/моль** (IFCC), целыми числами. Перерасчёт архивных результатов не проводился:
результаты до 01.03.2022 в выгрузке остаются в процентах. ХС-ЛНП — **ммоль/л** (расчёт по
формуле Фридвальда до 2021 г., затем прямой метод; единицы не менялись). Креатинин — метод
Яффе, **мкмоль/л**. Даты — ГГГГ-ММ-ДД.

## Центр 3 (Томск)

Клиника эндокринологии медицинского университета. HbA1c — ВЭЖХ, **проценты**. ХС-ЛНП
выдаётся в **мг/дл** (лаборатория работает по стандартам, принятым в совместных
международных проектах университета); пересчёт в ммоль/л выполняется делением на 38,67.
Креатинин — **мкмоль/л**. Даты в выгрузке центра 3 записаны в формате **ДД.ММ.ГГГГ**.

## Центр 4 (Самара)

Эндокринологическое отделение городской поликлиники № 7; анализы выполняются в
централизованной лаборатории города. HbA1c — иммунохимический метод, **проценты**. ХС-ЛНП —
**ммоль/л**. Креатинин — **мкмоль/л**. Лаборатория рассматривала переход на выдачу HbA1c в
ммоль/моль в 2022 г., но **переход не состоялся**. Особенность выгрузки: коды тестов
отличаются от стандартных (`A1C` — HbA1c, `LDLC` — ХС-ЛНП, `CREA` — креатинин), а рост в файле
участников указан **в метрах** (например, `1.72`). Даты — ГГГГ-ММ-ДД.

## Центр 5 (Ярославль)

Областной эндокринологический диспансер. HbA1c — ВЭЖХ, **проценты**. ХС-ЛНП — **ммоль/л**.
Креатинин выдаётся в **мг/дл** с двумя знаками после запятой; пересчёт в мкмоль/л —
умножением на 88,4. Даты в выгрузке — **ДД.ММ.ГГГГ**. О повторной сертификации центра см.
поправку 3 (приложение А).

## Центр 6 (Новосибирск)

Центр профилактической медицины. HbA1c — ферментативный метод; результаты выдаются в
**ммоль/моль** (IFCC) целыми числами **за весь период исследования**. ХС-ЛНП — **ммоль/л**.
Креатинин — **мг/дл**. Даты — ГГГГ-ММ-ДД. Центр 6 участвует в международной программе
стандартизации IFCC; результаты в процентах в ЛИС центра не формируются.

## Сводная таблица единиц в выгрузках

| Центр | HbA1c | ХС-ЛНП | Креатинин | Формат даты | Коды тестов |
|---|---|---|---|---|---|
| 1 | % | ммоль/л | мкмоль/л | ГГГГ-ММ-ДД | стандартные |
| 2 | % до 28.02.2022; ммоль/моль с 01.03.2022 | ммоль/л | мкмоль/л | ГГГГ-ММ-ДД | стандартные |
| 3 | % | мг/дл | мкмоль/л | ДД.ММ.ГГГГ | стандартные |
| 4 | % | ммоль/л | мкмоль/л | ГГГГ-ММ-ДД | A1C, LDLC, CREA |
| 5 | % | ммоль/л | мг/дл | ДД.ММ.ГГГГ | стандартные |
| 6 | ммоль/моль | ммоль/л | мг/дл | ГГГГ-ММ-ДД | стандартные |

Стандартные коды тестов: `HBA1C`, `LDL`, `CREAT`.

## Формулы пересчёта

* HbA1c: `% = 0,09148 × ммоль/моль + 2,152`, результат округляется до 0,1 % (половина вверх).
  Пример: 53 ммоль/моль → 0,09148 × 53 + 2,152 = 7,00044 → 7,0 %; 64 ммоль/моль → 8,00672 →
  8,0 %; 58 ммоль/моль → 7,45784 → 7,5 %.
* ХС-ЛНП: `ммоль/л = мг/дл ÷ 38,67`.
* Креатинин: `мкмоль/л = мг/дл × 88,4`.

## Отдельные результаты, выданные в других единицах

В редких случаях результат вносился вручную в единицах, отличных от указанных для центра
(например, результат внешней лаборатории). Такие случаи выявлялись мониторингом и отражены в
журнале запросов (приложение C) с указанием фактических единиц; для них используются
единицы, указанные в журнале.
"""


def _d(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")


def _n(x: float) -> str:
    s = _fmt_num(x)
    return s.replace(".", ",")


MONITORS = ["О. В. Лисицына", "Р. А. Ганиев", "М. С. Чернова", "Д. И. Панов", "Е. Л. Федосеева"]
VIS_NAMES = {"V0": "базового визита V0", "V2": "визита V2 (6 месяцев)"}
WHAT = {
    "weight": ("масса тела на визите", "масса тела"), "height": ("рост", "рост"),
    "hba1c": ("результат HbA1c", "HbA1c"), "smoking": ("статус курения", "статус курения"),
    "group": ("код назначенной терапии", "код терапии"), "creat": ("результат креатинина", "креатинин"),
    "ldl": ("результат ХС-ЛНП", "ХС-ЛНП"), "visit": ("дата внепланового визита", "дата визита"),
    "hypo": ("число эпизодов гипогликемии", "гипогликемии"),
}
SMOKE_TXT = {0: "никогда не курил", 1: "бывший курильщик", 2: "курит в настоящее время", 9: "нет данных"}
THERAPY_TXT = {1: "глиптазин", 2: "флозирон"}
VCODE_TXT = {"V1": "визите V1", "V2": "визите V2", "U": "внеплановом визите"}


LOG_PART_CHARS = 30000   # appendix C is split into files of about this size (whole quarters)


def _entry_body(e: dict, r: random.Random) -> str:
    """Draft text of one query-log entry (after the head)."""
    k = e["kind"]
    if k == "birth":
        first = e.get("first", e["new"])
        body = r.choice([
            f"Дата рождения в выгрузке — {_d(e['old'])}, в копии медицинской карты — {_d(first)}. "
            f"Решение: исправить дату рождения на {_d(first)}.",
            f"При сверке с первичной документацией обнаружено расхождение даты рождения: указано "
            f"{_d(e['old'])}. Центр подтвердил ошибку ввода; верная дата рождения — {_d(first)}.",
            f"Дата рождения {_d(e['old'])} не соответствует паспортным данным в карте. Исправлено: "
            f"{_d(first)}.",
        ])
        if "first" in e:
            body += (f"\n  *Дополнение от {_d(e['logged_final'])}:* " + r.choice([
                f"исправление по этому запросу внесено ошибочно (перепутаны карты однофамильцев); "
                f"по повторной сверке верная дата рождения участника — {_d(e['new'])}.",
                f"центр прислал уточнение: предыдущая исправленная дата неверна, правильная дата "
                f"рождения — {_d(e['new'])}. Действует это дополнение.",
            ]))
        if "late_supp" in e:
            body += (f"\n  *Дополнение от {_d(e['late_supp_logged'])}:* " + r.choice([
                f"центр прислал ещё одну поправку: по данным паспортного стола дата рождения — "
                f"{_d(e['late_supp'])}.",
                f"после повторной сверки центр сообщает дату рождения {_d(e['late_supp'])}.",
            ]))
    elif k == "sex":
        new = "женский" if e["new"] == 2 else "мужской"
        body = r.choice([
            f"Пол в выгрузке указан неверно. Решение: исправить пол на {new} (код {e['new']}).",
            f"В карте участника указан {new} пол, в выгрузке — код {e['old']}. Ошибка ввода, исправлено "
            f"на код {e['new']}.",
            f"Центр подтвердил ошибку кодирования пола. Верное значение: {new}.",
        ])
    elif k == "consent":
        body = r.choice([
            f"Участник отозвал информированное согласие {_d(e['day'])}; письменная форма поступила в "
            "центральную группу после формирования выгрузки, отметка в выгрузке отсутствует. Решение: "
            "считать согласие отозванным.",
            f"Получено заявление участника об отзыве согласия от {_d(e['day'])}. В выгрузке поле "
            "consent_withdrawn не заполнено. Учитывать как отзыв согласия.",
            f"Центр сообщил, что {_d(e['day'])} участник отказался от дальнейшего участия и отозвал "
            "согласие на обработку данных. Решение: отзыв согласия.",
        ])
        if e.get("cancel"):
            body += (f"\n  *Дополнение от {_d(e['cancel_logged'])}:* " + r.choice([
                "заявление об отзыве согласия относилось к другому пациенту (однофамилец, ошибка при "
                "сканировании документов). Участник согласие не отзывал и продолжает участие; решение по "
                "запросу отменено.",
                "центр сообщил, что отзыв согласия оформлен ошибочно: участник подтвердил письменно, что "
                "продолжает участие. Отзыв согласия не учитывать.",
            ]))
    elif k == "enroll":
        body = r.choice([
            f"Дата включения в выгрузке — {_d(e['old'])}, по журналу скрининга и дате подписи "
            f"согласия — {_d(e['new'])}. Исправлено на {_d(e['new'])}.",
            f"Расхождение даты включения: {_d(e['old'])} в выгрузке против {_d(e['new'])} в "
            f"информированном согласии. Верная дата включения — {_d(e['new'])}.",
        ])
    elif k == "unit":
        body = r.choice([
            f"Результат HbA1c от {_d(e['day'])} (в выгрузке {_n(e['value'])}) внесён вручную по бланку "
            f"внешней лаборатории, выдающей результат в процентах, а не в ммоль/моль. Решение: считать "
            f"значение выраженным в процентах: {_n(e['value'])} %, пересчёт не выполнять.",
            f"HbA1c за {_d(e['day'])}: значение {_n(e['value'])} — это проценты (анализ выполнен в "
            "сторонней лаборатории), а не ммоль/моль, как обычно в этом центре. Единицы — %.",
        ])
    elif k == "typo":
        body = r.choice([
            f"Результат HbA1c от {_d(e['day'])} записан как {_n(e['old'])}; в бланке лаборатории — "
            f"{_n(e['new'])} % (при вводе потерян десятичный разделитель). Исправлено на {_n(e['new'])}.",
            f"HbA1c за {_d(e['day'])}: в выгрузке {_n(e['old'])}, что невозможно. По бланку — "
            f"{_n(e['new'])} %. Решение: исправить.",
        ])
    elif k == "vdate":
        body = r.choice([
            f"Дата {VIS_NAMES[e['code']]} в выгрузке — {_d(e['old'])}; по первичной документации визит "
            f"состоялся {_d(e['new'])} (при вводе перепутаны день и месяц). Исправлено.",
            f"Для {VIS_NAMES[e['code']]} указана дата {_d(e['old'])}, однако в карте — {_d(e['new'])}. "
            f"Верная дата визита — {_d(e['new'])}.",
        ])
    elif k == "weight":
        body = r.choice([
            f"Масса тела на базовом визите ({_d(e['day'])}) в выгрузке — {_n(e['old'])} кг; в карте — "
            f"{_n(e['new'])} кг (потерян десятичный разделитель). Исправлено на {_n(e['new'])}.",
            f"Неправдоподобная масса тела на визите V0: {_n(e['old'])}. По первичной документации — "
            f"{_n(e['new'])} кг. Решение: исправить.",
        ])
    elif k == "smoking":
        body = r.choice([
            f"Статус курения в выгрузке — код {e['old']}, в анкете при включении отмечено «"
            f"{SMOKE_TXT[e['new']]}». Исправлено на код {e['new']}.",
            f"Центр уточнил статус курения участника: {SMOKE_TXT[e['new']]} (код {e['new']}); в выгрузке "
            "указан другой код по ошибке. Исправить.",
        ])
    elif k == "therapy":
        body = r.choice([
            f"Код терапии в выгрузке — {e['old']}, однако по листу назначений на базовом визите пациенту "
            f"назначен {THERAPY_TXT[e['new']]}. Исправлено: код терапии {e['new']}.",
            f"Ошибка при вводе назначенного препарата: верно — {THERAPY_TXT[e['new']]} (код {e['new']}).",
        ])
    elif k == "height":
        body = r.choice([
            f"Рост в выгрузке — {e['old']}, что не соответствует карте ({e['new']} см). Исправлено на "
            f"{e['new']}.",
            f"Неправдоподобное значение роста ({e['old']}). По данным антропометрии при включении — "
            f"{e['new']} см. Решение: исправить.",
        ])
    elif k == "hypo":
        body = r.choice([
            f"Число эпизодов гипогликемии на {VCODE_TXT[e['code']]} {_d(e['day'])} в выгрузке — "
            f"{e['old']}, в карте — {e['new']} (значение внесено не в то поле). Исправлено на {e['new']}.",
            f"При сверке дневника пациента за визит {_d(e['day'])} выяснилось, что hypo_events должно быть "
            f"равно {e['new']}, а не {e['old']}. Решение: исправить.",
        ])
    elif k == "creat_unit":
        body = r.choice([
            f"Результат креатинина от {_d(e['day'])} (в выгрузке {_n(e['value'])}) внесён вручную по бланку "
            "сторонней лаборатории, которая выдаёт креатинин в мг/дл, а не в мкмоль/л, как обычно в этом "
            "центре. Решение: считать значение выраженным в мг/дл.",
            f"Креатинин за {_d(e['day'])}: значение {_n(e['value'])} — это мг/дл (анализ сдан в частной "
            "лаборатории по месту жительства). Единицы — мг/дл; значение не менять.",
            f"Неправдоподобно низкий креатинин ({_n(e['value'])}) от {_d(e['day'])}. Выяснено, что результат "
            "переписан с бланка в мг/дл. Решение: единицы — мг/дл.",
        ])
    elif k == "ldl_typo":
        body = r.choice([
            f"ХС-ЛНП от {_d(e['day'])}: в выгрузке {_n(e['old'])}, в бланке лаборатории — {_n(e['new'])} "
            f"ммоль/л (при вводе потерян десятичный разделитель). Исправлено на {_n(e['new'])}.",
            f"Значение ХС-ЛНП {_n(e['old'])} за {_d(e['day'])} невозможно. По первичной документации — "
            f"{_n(e['new'])}. Решение: исправить.",
        ])
    elif k == "weight_fill":
        body = r.choice([
            f"Масса тела на базовом визите ({_d(e['day'])}) в выгрузке не указана (−99), но в карте "
            f"записана: {_n(e['new'])} кг. Решение: внести значение {_n(e['new'])}.",
            f"Для визита V0 от {_d(e['day'])} поле массы тела заполнено как «не измерялась», однако "
            f"измерение есть в листе осмотра — {_n(e['new'])} кг. Исправить.",
        ])
    elif k == "vcode":
        body = r.choice([
            f"Визит {_d(e['day'])}, внесённый как внеплановый (`U`), по первичной документации был плановым "
            f"визитом {e['new']}. Код визита исправлен на {e['new']}; дата визита и результаты не меняются.",
            f"Код визита от {_d(e['day'])}: указан `U`, в карте — {e['new']} (визит проведён с отклонением от "
            f"плана). Исправлено на {e['new']}.",
        ])
    elif k == "late_sex":
        new = "женский" if e["new"] == 2 else "мужской"
        body = (f"Центр сообщил о возможной ошибке кодирования пола; предлагаемое значение — {new}. "
                "Ответ получен после закрытия базы данных.")
    elif k == "late_birth":
        body = (f"Центр прислал уточнённую дату рождения участника: {_d(e['new'])}. Ответ получен после "
                "закрытия базы данных.")
    else:  # confirm
        full, short = WHAT[e["what"]]
        body = r.choice([
            f"Запрошено подтверждение: {full} выглядит необычно. Ответ центра: данные подтверждены "
            "первичной документацией, изменений нет.",
            f"Проверка значения ({short}) по карте участника. Значение в выгрузке верное. Запрос закрыт "
            "без изменений.",
            f"Выброс по показателю «{short}» при автоматической проверке. Центр подтвердил значение; "
            "решение — оставить как есть.",
            f"Сомнение в корректности поля «{short}». После сверки ошибка не подтвердилась, изменений нет.",
        ])
    return body


@functools.cache
def _log_entries() -> tuple[list[dict], dict]:
    """Draft entries of appendix C (head, body, quarter) and the quarter introductions."""
    W = build_world()
    r = random.Random(f"{TASK_ID}/log")
    entries: list[dict] = []
    intros: dict[tuple[int, int], str] = {}
    for e in W["corrections"]:
        q = (e["logged"].year, (e["logged"].month - 1) // 3 + 1)
        if q not in intros:
            intros[q] = r.choice([
                f"Мониторинговых визитов в центры за квартал: {r.randint(3, 9)}; "
                f"проверено карт: {r.randint(120, 400)}. Ниже — запросы, по которым получены ответы.\n",
                f"Мониторинг в квартале вёлся удалённо и на местах (выездов в центры: {r.randint(2, 6)}). "
                "Существенных системных нарушений не выявлено, кроме отмеченных ниже.\n",
                "Сверка выгрузок с первичной документацией выполнена по выборке карт; результаты "
                "рассмотрения запросов приведены ниже.\n",
            ])
        mon = r.choice(MONITORS)
        head = f"**Запрос № {e['no']}** ({_d(e['logged'])}, монитор {mon}). Участник `{e['pid']}`."
        entries.append({"e": e, "q": q, "head": head, "body": _entry_body(e, r)})
    return entries, intros


LOG_RULES = """Правила применения (раздел 2.6):

* исправления, зарегистрированные **до закрытия базы данных 16.10.2023 включительно**,
  применяются к выгрузкам (выгрузки сформированы до исправлений и их не содержат);
* если по запросу есть дополнение с более поздней датой, действует дополнение;
* записи, зарегистрированные после закрытия базы, приводятся для полноты и **не применяются**;
  это относится и к **дополнениям**: дополнение с датой после 16.10.2023 не применяется, и по
  такому запросу действует последнее решение, зарегистрированное до закрытия базы;
* запросы, закрытые без изменений («данные подтверждены», «оставить как есть» и т. п.), не
  влекут изменений.
"""
_HEAD_RE = re.compile(r"(?m)^\*\*Запрос № (\d+)\*\* \((\d\d\.\d\d\.\d{4}), монитор [^)\n]*\)\. Участник `[^`\n]+`\.")
_SUPP_RE = re.compile(r"\*Дополнение от (\d\d\.\d\d\.\d{4}):\*")


def _log_file(k: int) -> str:
    return "paper/appendix_C_data_queries.md" if k == 1 else f"paper/appendix_C_data_queries_part{k}.md"


@functools.cache
def _log_groups() -> list[dict]:
    """Consecutive entries of one quarter, 2–4 per group: the unit of LLM rewriting."""
    entries, _ = _log_entries()
    r = random.Random(f"{TASK_ID}/log-groups")
    groups: list[list[dict]] = []
    size = 0
    for ent in entries:
        if not groups or ent["q"] != groups[-1][0]["q"] or len(groups[-1]) >= size:
            groups.append([])
            size = r.choice([2, 3, 3, 4])
        groups[-1].append(ent)
    return [{"key": f"log/{g[0]['e']['no']:04d}", "entries": g, "q": g[0]["q"],
             "draft": "\n\n".join(f"{x['head']} {x['body']}" for x in g)} for g in groups]


def _log_skeleton(text: str) -> tuple:
    """Entry heads (verbatim, in order) and the supplement dates of every entry."""
    heads = list(_HEAD_RE.finditer(text))
    segs = [text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)] for i, m in enumerate(heads)]
    return (tuple(m.group(0) for m in heads), tuple(tuple(_SUPP_RE.findall(sg)) for sg in segs),
            text.count("**Запрос №"), bool(heads) and heads[0].start() == 0)


def _log_skel_ok(draft: str, text: str) -> bool:
    return _log_skeleton(text) == _log_skeleton(draft)


def _group_text(g: dict) -> str:
    text = rewritten(TASK_ID, g["key"], g["draft"]).strip()
    return text if text == g["draft"] or _log_skel_ok(g["draft"], text) else g["draft"]


def _qname(q: tuple[int, int], gen: bool = False) -> str:
    return f"{['I', 'II', 'III', 'IV'][q[1] - 1]} квартал{'а' if gen else ''} {q[0]} года"


@functools.cache
def render_log() -> tuple[str, ...]:
    """Appendix C split into several files by whole quarters (sizes by the draft text)."""
    _, intros = _log_entries()
    blocks: list[list] = []
    for g in _log_groups():
        if not blocks or blocks[-1][0] != g["q"]:
            q = g["q"]
            blocks.append([q, f"\n## {q[0]} год, {q[1]}-й квартал\n\n{intros[q]}", 0, ""])
        blocks[-1][2] += len(g["draft"])
        blocks[-1][3] += "\n" + _group_text(g) + "\n"
    parts: list[list] = [[]]
    size = 0
    for b in blocks:
        if parts[-1] and size + b[2] > LOG_PART_CHARS:
            parts.append([])
            size = 0
        parts[-1].append(b)
        size += b[2]
    n = len(parts)
    others = ", ".join(f"`{_log_file(k).split('/')[1]}`" for k in range(2, n + 1))
    out = []
    for k, part in enumerate(parts, 1):
        span = f"с {_qname(part[0][0], gen=True)} по {_qname(part[-1][0])}"
        if k == 1:
            head = (
                "# Приложение C. Журнал запросов на уточнение данных (часть 1)\n\n"
                "Журнал ведёт центральная группа мониторинга. Каждая запись содержит номер запроса, дату\n"
                "регистрации результата рассмотрения, идентификатор участника, суть запроса и решение. Записи\n"
                f"приведены в хронологическом порядке. Журнал разбит на {n} файлов по кварталам регистрации\n"
                f"записи: в этом файле — записи {span}, продолжение — в файлах {others}.\n"
                "Нумерация запросов сквозная.\n\n" + LOG_RULES + "\n---\n")
        else:
            head = (
                f"# Приложение C. Журнал запросов на уточнение данных (часть {k} из {n})\n\n"
                f"Продолжение журнала: записи, зарегистрированные {span}"
                + (", в том числе ответы центров, поступившие после закрытия базы данных 16.10.2023" if k == n
                   else "")
                + ". Начало журнала и правила применения записей — в `appendix_C_data_queries.md`.\n\n---\n")
        out.append(head + "".join(b[1] + b[3] for b in part))
    return tuple(out)


COMMITTEE = """# Приложение D. Выдержки из протоколов заседаний руководящего комитета

Приводятся решения, касающиеся сбора и анализа данных. Организационные вопросы (бюджет,
публикационная политика, график мониторинга) опущены. Решения комитета, касающиеся анализа,
являются частью плана статистического анализа.

## Заседание № 1 (21.04.2020)

Присутствовали: председатель комитета, руководители центров 1–5, руководитель группы
мониторинга, статистик.

1. Утверждён план статистического анализа версии 1.0 без изменений.
2. Обсуждался вопрос о единой лабораторной службе. Централизовать анализы невозможно по
   логистическим причинам. **Решено:** центры выгружают результаты в том виде, в котором их
   выдаёт местная ЛИС, без пересчёта на своей стороне; гармонизация единиц выполняется
   центральной группой при анализе по правилам раздела 3.3.1 и приложения B.
3. Предложение центра 4 перевести рост в своих выгрузках в сантиметры отклонено по той же
   причине: пересчёт выполняется при анализе.

## Заседание № 2 (08.09.2020)

1. Руководитель группы мониторинга сообщил о случаях повторной регистрации одних и тех же
   пациентов под новыми номерами (пациент возвращался в центр после перерыва, регистратор
   создавал новую карту). **Решено:** ввести поле `nid_hash` (хеш национального
   идентификатора) и при анализе оставлять для каждого человека одну запись — с наиболее ранней
   датой включения. Визиты и анализы, внесённые под повторными номерами, в анализ не
   включаются, даже если они выглядят полноценными.
2. Обсуждался формат дат. Центры 3 и 5 используют в выгрузке формат ДД.ММ.ГГГГ, остальные —
   ГГГГ-ММ-ДД. **Решено:** форматы не менять, учитывать при анализе.

## Заседание № 3 (15.03.2021)

1. Обсуждался сбор данных о гипогликемиях (в связи с письмом регулятора, см. поправку 1).
   Вопрос: учитывать ли эпизоды, зарегистрированные на внеплановых визитах? **Решено:**
   учитывать все визиты — плановые и внеплановые — с днём наблюдения от 1 до 196; базовый визит
   не учитывать. Значение `-99` («не собрано») эпизодом не считается.
2. Предложено считать гипогликемией только эпизоды, потребовавшие помощи третьих лиц.
   **Решено:** не вводить такое ограничение, поскольку тяжесть эпизодов в картах не
   фиксируется.

## Заседание № 4 (10.06.2021)

1. Статистик представил результаты пробной обработки данных за первый год. Выявлены случаи,
   когда в окно временной точки попадают два результата HbA1c на одинаковом расстоянии от
   целевого дня (например, в дни 175 и 189 для точки 6 месяцев). **Решено:** в таких случаях
   использовать **более ранний** результат.
2. Выявлены повторные определения HbA1c в один день (контрольные прогоны). **Решено:**
   использовать среднее арифметическое всех результатов этого дня — после приведения к
   процентам и исключения неправдоподобных значений.
3. Подтверждено, что базовым считается последний результат в интервале от дня −30 до дня 0;
   результаты, полученные после визита V0, базовыми не считаются.
4. Принята к сведению поправка 2 (порог целевого HbA1c 7,0 %).

## Заседание № 5 (24.01.2022)

1. Обсуждалась доля визитов V3, проведённых с отклонением от плана. По данным центров, около
   четверти визитов V3 проведены позже чем через 379 дней. **Решено:** подготовить поправку о
   расширении окна для точки 12 месяцев (принята как поправка 4).
2. Предложено расширить также окно для точки 6 месяцев до ±21 дня. **Решено:** не расширять;
   окно для точки 6 месяцев остаётся 182 ± 14 дней.

## Заседание № 6 (14.11.2022)

1. Предложено винзоризовать также значения HbA1c. **Решено:** не винзоризовать HbA1c;
   винзоризации подлежат только ИМТ и базовый ХС-ЛНП (раздел 4.3).
2. Предложено восстанавливать пропущенные значения HbA1c на 6 месяцах методом переноса
   последнего наблюдения (LOCF). **Решено:** не восстанавливать; первичный исход оценивается
   только у участников со значением в окне 6 месяцев.
3. Информация о поправке 6 (исключение по СКФ): отклонена этическим комитетом, дополнительных
   исключений нет.

## Заседание № 7 (15.05.2023)

Присутствовали: председатель комитета, руководители центров 1–6, статистик, представитель
группы мониторинга.

1. Обсуждались показатели, добавленные поправкой 9. Статистик уточнил порядок расчёта.
   **Решено:**
   * ХС-ЛНП на 12 месяцах относится к точке по дате забора в окне **365 ± 30 дней (дни 335–395
     включительно)** — то же окно, что для HbA1c после поправки 4; из нескольких результатов в
     окне берётся ближайший к 365-му дню, при равном удалении — более ранний; повторные
     результаты за один день усредняются после пересчёта единиц (центр 3 — мг/дл).
   * Код визита для ХС-ЛНП на 12 месяцах значения не имеет (результат внепланового визита в окне
     учитывается).
   * Для рСКФ используется базовый креатинин в мкмоль/л после всех исправлений и пересчётов;
     перевод в мг/дл для формулы — делением на 88,4. Пол и возраст — после исправлений по журналу,
     возраст — на дату V0, как для остальных показателей.
2. Рассмотрена поправка 10 (винзоризация ХС-ЛНП на 12 месяцах и перенос последнего наблюдения).
   **Решено:** отклонить.
3. Статистик предложил приводить в таблице 1 медиану креатинина вместо рСКФ. **Решено:** не
   менять — в таблицу 1 входит средняя рСКФ по поправке 9.

## Заседание № 8 (04.09.2023)

1. Вопрос центра 2: в определении комбинированного исхода поправки 9 гипогликемия учитывается
   только до 182-го дня, тогда как вторичный исход «гипогликемия» (поправка 1) — до 196-го дня.
   **Решено:** для единообразия в комбинированном исходе учитывать гипогликемии на визитах с днём
   наблюдения **от 1 до 196 включительно** — так же, как в поправке 1. Это изменение вносится в
   поправку 9 и действует вместо интервала 1–182.
2. Уточнено: отношение рисков в таблице 3 — «А относительно Б», как и остальные сравнения
   раздела 4.5 (значение меньше 1 означает, что в группе А цель достигается реже). Отношение
   долей рассчитывается по неокруглённым долям.
3. Изменение HbA1c в таблице 2 приводится со знаком: «6 месяцев минус базовое». Предложение
   центра 4 приводить «снижение» (базовое минус 6 месяцев), как в анализе чувствительности
   F.9.3 исходного ПСА, отклонено.

## Заседание № 9 (16.10.2023)

1. База данных закрыта 16.10.2023. Журнал запросов (приложение C) закрыт этой же датой.
   **Решено:** ответы центров, поступившие после закрытия базы, не применять, но приводить в
   журнале для прозрачности.
2. Подтверждено: группа определяется по коду терапии в файле участников (`1` — глиптазин,
   группа А; `2` — флозирон, группа Б).
3. Подтверждено: отсутствие результата креатинина на базовом обследовании не является
   основанием для исключения.
4. Подтверждено: доля курящих рассчитывается среди участников с известным статусом курения;
   курящими считаются только курящие в настоящее время.
5. Статистику поручено подготовить таблицы и рисунки по ПСА с учётом поправок 1–4, 7 и 9 (с
   изменением, принятым на заседании № 8).
6. Подтверждено: дополнения к записям журнала запросов, поступившие после закрытия базы, не
   применяются так же, как и новые записи; в силе остаётся последнее решение по запросу,
   зарегистрированное до 16.10.2023 включительно.
"""

RESULTS = """# 5. Результаты (структура с ключами)

В этой версии рукописи вместо чисел указаны ключи файла `results_template.json`. Каждому
ключу соответствует одно число в файле `results.json`. Все величины рассчитываются по
методам разделов 2–4 с учётом поправок (приложение А), решений комитета (приложение D) и
исправлений из журнала запросов (приложение C); правила округления — раздел 4.6.

## 5.1. Формирование аналитической выборки (рис. 1)

Рисунок 1. Блок-схема формирования аналитической выборки. На каждом шаге указано число
**записей**, исключённых на этом шаге (записи, исключённые на более раннем шаге, повторно не
учитываются):

1. исключены как дубликаты — `F_dup_removed`;
2. отозвали информированное согласие — `F_excl_consent`;
3. центр 5, включены до 01.09.2021 (поправка 3) — `F_excl_site5`;
4. нет базового визита V0 — `F_excl_no_v0`;
5. возраст на дату V0 вне 18–79 лет — `F_excl_age`;
6. нет базового значения HbA1c — `F_excl_no_hba1c`;
7. базовый креатинин выше порога — `F_excl_creat`.

Аналитическая выборка: группа А (глиптазин) — `N_A`, группа Б (флозирон) — `N_B`.

## 5.2. Исходные характеристики (таблица 1)

Таблица 1. Характеристики участников аналитической выборки на базовом визите

| Показатель | Группа А | Группа Б |
|---|---|---|
| Возраст, лет, среднее | `T1_age_mean_A` | `T1_age_mean_B` |
| Женщины, % | `T1_female_pct_A` | `T1_female_pct_B` |
| ИМТ, кг/м², среднее² | `T1_bmi_mean_A` | `T1_bmi_mean_B` |
| ХС-ЛНП, ммоль/л, среднее² | `T1_ldl_mean_A` | `T1_ldl_mean_B` |
| Курят в настоящее время, %¹ | `T1_smoker_pct_A` | `T1_smoker_pct_B` |
| рСКФ (CKD-EPI 2021), мл/мин/1,73 м², среднее³ | `T1_egfr_mean_A` | `T1_egfr_mean_B` |

¹ См. примечание 1 в разделе 3.2.
² После винзоризации (раздел 4.3).
³ Показатель добавлен поправкой 9 (приложение А); уточнения — приложение D, заседание № 7.

Пациенты группы Б в среднем моложе, среди них больше женщин; эти различия учитываются при
скорректированном сравнении (раздел 5.4).

## 5.3. Исходы (таблица 2)

Таблица 2. Первичный и вторичный исходы

| Показатель | Группа А | Группа Б |
|---|---|---|
| Оценены на 6 месяцах, n | `T2_m6_assessed_A` | `T2_m6_assessed_B` |
| Достигли целевого HbA1c, n | `T2_goal_n_A` | `T2_goal_n_B` |
| Достигли целевого HbA1c, % от оценённых | `T2_goal_pct_A` | `T2_goal_pct_B` |
| Хотя бы одна гипогликемия (дни 1–196), % | `T2_hypo_pct_A` | `T2_hypo_pct_B` |
| Изменение HbA1c к 6 месяцам, п. п. HbA1c, среднее⁴ | `T2_hba1c_change_A` | `T2_hba1c_change_B` |
| Целевой HbA1c без гипогликемий, % от оценённых⁴ | `T2_composite_pct_A` | `T2_composite_pct_B` |
| ХС-ЛНП на 12 месяцах, ммоль/л, среднее⁴ | `T2_ldl_m12_mean_A` | `T2_ldl_m12_mean_B` |

⁴ Показатели поправки 9 (приложение А) с изменением, принятым на заседании № 8 (приложение D).

## 5.4. Сравнение групп (таблица 3)

Таблица 3. Сравнение доли достигших целевого HbA1c через 6 месяцев (А относительно Б)

| Показатель | Значение |
|---|---|
| Нескорректированная разность рисков, п. п. | `T3_crude_rd` |
| Разность рисков по Мантелю–Хензелю, п. п. | `T3_mh_rd` |
| Отношение шансов по Мантелю–Хензелю | `T3_mh_or` |
| Стандартизованная доля, группа А, % | `T3_std_pct_A` |
| Стандартизованная доля, группа Б, % | `T3_std_pct_B` |
| Нескорректированное отношение рисков⁴ | `T3_crude_rr` |
| Отношение рисков по Мантелю–Хензелю⁴ | `T3_mh_rr` |

## 5.5. Динамика HbA1c (рисунок 2)

Рисунок 2. Среднее значение HbA1c (%) по группам:

| Точка | Группа А | Группа Б |
|---|---|---|
| Базовое обследование | `FIG_hba1c_m0_A` | `FIG_hba1c_m0_B` |
| 3 месяца | `FIG_hba1c_m3_A` | `FIG_hba1c_m3_B` |
| 6 месяцев | `FIG_hba1c_m6_A` | `FIG_hba1c_m6_B` |
| 12 месяцев | `FIG_hba1c_m12_A` | `FIG_hba1c_m12_B` |

Средние рассчитываются по участникам аналитической выборки со значением в соответствующей
точке, поэтому число участников в точках различается.
"""

DISCUSSION = """# 6. Обсуждение

В многоцентровом проспективном когортном исследовании ГЛИКОНТ мы сравнили достижение
целевого уровня HbA1c через 6 месяцев после начала терапии глиптазином и флозироном у
взрослых пациентов с СД2, получающих метформин. Исходные характеристики групп различались:
флозирон чаще назначали более молодым пациентам и женщинам, что согласуется с данными других
наблюдательных исследований и отражает клинические представления о профиле пациентов,
которым показаны иНГЛТ-2. Для учёта этих различий мы использовали стратификацию по
возрастной группе и полу.

## 6.1. Интерпретация

Выбор стратифицированных методов вместо многофакторной регрессии обусловлен небольшим
числом ковариат, по которым планировалась поправка, и стремлением к прозрачности. Оценка
разности рисков по Мантелю–Хензелю объединяет стратоспецифические разности с весами,
зависящими от численности групп в страте, а прямая стандартизация отвечает на вопрос, какой
была бы доля достигших цели в каждой группе, если бы распределение по возрасту и полу в
группах совпадало с распределением в объединённой выборке. Совпадение направлений
нескорректированной и скорректированных оценок говорит о том, что различия между группами
по возрасту и полу не объясняют полностью различий в достижении цели, однако величина
поправки позволяет судить о степени смешивания.

Важно подчеркнуть, что поправка 2 изменила определение первичного исхода в ходе
исследования. Поскольку данные о HbA1c собирались в непрерывной шкале, изменение порога не
повлияло на сбор данных и было применено ко всем участникам. Тем не менее доли достигших
цели при пороге 7,0 % существенно ниже, чем были бы при исходном пороге 7,5 %, и при
сравнении с работами, использовавшими другой порог, это необходимо учитывать.

## 6.2. Гармонизация лабораторных данных

Наш опыт показывает, что гармонизация лабораторных показателей — один из наиболее
трудоёмких этапов многоцентровых наблюдательных исследований. Переход лаборатории центра 2
на новую информационную систему в середине исследования привёл к смене единиц выдачи HbA1c,
не отражённой в структуре выгрузки. Без учёта этого перехода значения в ммоль/моль были бы
восприняты как проценты и отброшены проверкой правдоподобия, что сократило бы выборку и
сместило бы оценки. Аналогично, отдельные результаты, внесённые вручную по бланкам внешних
лабораторий, требовали индивидуальной проверки; такие случаи отражены в журнале запросов.

Округление пересчитанных значений HbA1c до десятых долей процента, принятое в ПСА,
воспроизводит практику лабораторий, выдающих результаты в процентах, и обеспечивает
единообразие классификации пациентов относительно порога.

## 6.3. Сильные стороны и ограничения

К сильным сторонам исследования относятся проспективный сбор данных по единому протоколу,
участие шести центров из разных регионов, централизованный мониторинг с документированной
процедурой исправлений и заранее утверждённый план анализа.

Ограничения связаны прежде всего с наблюдательным дизайном: несмотря на стратификацию по
возрасту и полу, возможно остаточное смешивание по неучтённым факторам — длительности
диабета, исходной массе тела, сопутствующим заболеваниям, приверженности лечению. Анализ по
доступным данным предполагает, что пропуски значений HbA1c на 6 месяцах не связаны с
исходом; это допущение не может быть проверено. Исключение участников центра 5, включённых до
повторной сертификации (поправка 3), уменьшило размер выборки, но было необходимо по
этическим соображениям. Регистрация гипогликемий основывалась на сообщениях пациентов и,
вероятно, занижает истинную частоту эпизодов, особенно лёгких.

Мы не приводили интервальных оценок в настоящей версии рукописи; в окончательной версии
планируется привести доверительные интервалы для разностей рисков и отношения шансов.

## 6.4. Заключение

Воспроизводимость результатов наблюдательных исследований во многом определяется полнотой
описания методов, включая порядок формирования выборки, правила обработки лабораторных
данных и процедуру исправлений. Мы публикуем полный набор выгрузок центров, кодовую книгу и
журнал запросов, чтобы независимые исследователи могли повторить все этапы анализа.

## Благодарности

Авторы благодарят сотрудников лабораторий центров за помощь в описании методов и единиц
измерения, а также группу мониторинга за работу с журналом запросов.

## Конфликт интересов

Исследование выполнено без спонсорской поддержки производителей исследуемых препаратов.
"""

CODEBOOK_README = """# Кодовая книга: общие сведения

Данные исследования ГЛИКОНТ выгружены отдельно по каждому центру в каталоги
`data/site1` … `data/site6`. В каждом каталоге:

* `participants.csv` — участники центра (одна строка на запись регистрации);
* `visits_ГГГГH1.csv`, `visits_ГГГГH2.csv` — визиты за первое и второе полугодие года (по дате
  визита в выгрузке);
* `lab_ГГГГQn.csv` — лабораторные результаты за квартал n года (по дате забора).

Все файлы — CSV в кодировке UTF-8, разделитель — запятая, первая строка — заголовок,
десятичный разделитель — точка. Описание полей — в файлах `participants.md`, `visits.md`,
`lab.md`.

Особенности центров (подробнее — в приложении B к рукописи):

* центры 3 и 5 записывают даты в формате `ДД.ММ.ГГГГ`, остальные — `ГГГГ-ММ-ДД`;
* центр 4 использует собственные коды лабораторных тестов и указывает рост в метрах;
* единицы лабораторных результатов в файлах не указаны и зависят от центра и даты забора.

Выгрузки сформированы до внесения исправлений по журналу запросов (приложение C) и их не
содержат.
"""

CODEBOOK_PART = """# Кодовая книга: файл участников `participants.csv`

Одна строка — одна запись регистрации участника в центре. Один и тот же человек может иметь
несколько записей (повторная регистрация) — см. поле `nid_hash`.

| Поле | Тип | Описание |
|---|---|---|
| `participant_id` | строка | идентификатор записи `СС-NNNN`, где СС — номер центра |
| `site` | целое | номер центра, 1–6 |
| `enrol_date` | дата | дата включения (подписания информированного согласия) |
| `birth_date` | дата | дата рождения |
| `sex` | целое | пол: `1` — мужской, `2` — женский |
| `height` | число | рост; в сантиметрах (целое), в центре 4 — в метрах с двумя знаками |
| `smoking` | целое | статус курения при включении: `0` — никогда не курил, `1` — бывший курильщик, `2` — курит в настоящее время, `9` — нет данных |
| `therapy` | целое | препарат, назначенный на базовом визите: `1` — глиптазин (группа А), `2` — флозирон (группа Б) |
| `consent_withdrawn` | целое | `1` — участник отозвал согласие (по данным центра на момент выгрузки), `0` — нет |
| `nid_hash` | строка | хеш национального идентификатора; одинаковые значения — один человек |

Формат дат зависит от центра (см. общие сведения).

**Дубликаты.** Если несколько записей имеют одинаковый `nid_hash`, в анализе остаётся запись
с наиболее ранней `enrol_date`; остальные записи и все связанные с ними визиты и анализы
исключаются (раздел 2.6 рукописи).

**Отзыв согласия.** Поле `consent_withdrawn` отражает сведения центра на момент выгрузки.
Отзывы согласия, оформленные позже, приведены в журнале запросов (приложение C).
"""

CODEBOOK_VIS = """# Кодовая книга: файлы визитов `visits_*.csv`

Одна строка — один визит. Файлы разбиты по полугодиям по дате визита, указанной в выгрузке.

| Поле | Тип | Описание |
|---|---|---|
| `visit_id` | строка | идентификатор визита |
| `participant_id` | строка | идентификатор записи участника |
| `visit_code` | строка | `V0` — базовый визит; `V1` — 3 месяца; `V2` — 6 месяцев; `V3` — 12 месяцев; `U` — внеплановый |
| `visit_date` | дата | дата визита (формат зависит от центра) |
| `weight_kg` | число | масса тела, кг; `-99` — не измерялась |
| `sbp_mmhg` | целое | систолическое артериальное давление, мм рт. ст. |
| `hypo_events` | целое | число эпизодов гипогликемии, о которых сообщил пациент с предыдущего визита; `-99` — информация не собрана |

**Повторяющиеся строки.** Из-за ошибки процедуры выгрузки часть строк продублирована
полностью (включая `visit_id`). Такая строка описывает один и тот же визит и должна
учитываться один раз.

**Даты визитов** могли быть исправлены по журналу запросов (приложение C); при этом визит
остаётся в том файле, куда он попал по дате в выгрузке.

Плановые визиты V1–V3 могли проводиться с отклонением от плановой даты; отнесение
лабораторных результатов к временным точкам выполняется по дате забора (раздел 3.3.3), а
не по коду визита.
"""

CODEBOOK_LAB = """# Кодовая книга: лабораторные файлы `lab_*.csv`

Одна строка — один результат одного теста. Файлы разбиты по кварталам по дате забора.

| Поле | Тип | Описание |
|---|---|---|
| `sample_id` | строка | идентификатор результата |
| `participant_id` | строка | идентификатор записи участника |
| `sample_date` | дата | дата забора крови (формат зависит от центра) |
| `test_code` | строка | код теста (см. ниже) |
| `result` | число | результат в единицах лаборатории центра на дату забора (единицы в файле не указаны) |

**Коды тестов.** Стандартные коды: `HBA1C` — гликированный гемоглобин, `LDL` — холестерин
ЛНП, `CREAT` — креатинин сыворотки. Центр 4 использует коды `A1C`, `LDLC` и `CREA`
соответственно.

**Единицы.** Результаты записаны в единицах, в которых их выдала лаборатория центра на дату
забора: для HbA1c — проценты или ммоль/моль, для ХС-ЛНП — ммоль/л или мг/дл, для
креатинина — мкмоль/л или мг/дл. Соответствие центров, периодов и единиц — в приложении B;
отдельные исключения — в журнале запросов (приложение C).

**Повторные определения.** Для одного участника в один день может быть несколько результатов
одного теста с разными `sample_id` (контрольные прогоны). Правило обработки — раздел 3.3.1
рукописи.

Результаты участников, не вошедших в аналитическую выборку, в выгрузке сохранены.
"""

KEY_DESC = {
    "F_dup_removed": "Рис. 1, шаг 1: исключено записей-дубликатов (целое)",
    "F_excl_consent": "Рис. 1, шаг 2: исключено по отзыву согласия (целое)",
    "F_excl_site5": "Рис. 1, шаг 3: исключено участников центра 5, включённых до 01.09.2021 (целое)",
    "F_excl_no_v0": "Рис. 1, шаг 4: исключено из-за отсутствия визита V0 (целое)",
    "F_excl_age": "Рис. 1, шаг 5: исключено по возрасту на дату V0 (целое)",
    "F_excl_no_hba1c": "Рис. 1, шаг 6: исключено из-за отсутствия базового HbA1c (целое)",
    "F_excl_creat": "Рис. 1, шаг 7: исключено по базовому креатинину (целое)",
    "N_A": "Аналитическая выборка, группа А (целое)",
    "N_B": "Аналитическая выборка, группа Б (целое)",
}
for _g, _name in (("A", "группа А"), ("B", "группа Б")):
    KEY_DESC.update({
        f"T1_age_mean_{_g}": f"Табл. 1: средний возраст, лет, {_name} (2 знака)",
        f"T1_female_pct_{_g}": f"Табл. 1: доля женщин, %, {_name} (1 знак)",
        f"T1_bmi_mean_{_g}": f"Табл. 1: средний ИМТ после винзоризации, кг/м², {_name} (2 знака)",
        f"T1_ldl_mean_{_g}": f"Табл. 1: средний базовый ХС-ЛНП после винзоризации, ммоль/л, {_name} (2 знака)",
        f"T1_smoker_pct_{_g}": f"Табл. 1: доля курящих в настоящее время, %, {_name} (1 знак)",
        f"T2_m6_assessed_{_g}": f"Табл. 2: оценены на 6 месяцах, n, {_name} (целое)",
        f"T2_goal_n_{_g}": f"Табл. 2: достигли целевого HbA1c, n, {_name} (целое)",
        f"T2_goal_pct_{_g}": f"Табл. 2: достигли целевого HbA1c, % от оценённых, {_name} (1 знак)",
        f"T2_hypo_pct_{_g}": f"Табл. 2: хотя бы одна гипогликемия, %, {_name} (1 знак)",
        f"T3_std_pct_{_g}": f"Табл. 3: стандартизованная доля достигших цели, %, {_name} (1 знак)",
        f"FIG_hba1c_m0_{_g}": f"Рис. 2: средний базовый HbA1c, %, {_name} (2 знака)",
        f"FIG_hba1c_m3_{_g}": f"Рис. 2: средний HbA1c на 3 месяцах, %, {_name} (2 знака)",
        f"FIG_hba1c_m6_{_g}": f"Рис. 2: средний HbA1c на 6 месяцах, %, {_name} (2 знака)",
        f"FIG_hba1c_m12_{_g}": f"Рис. 2: средний HbA1c на 12 месяцах, %, {_name} (2 знака)",
    })
for _g, _name in (("A", "группа А"), ("B", "группа Б")):
    KEY_DESC.update({
        f"T1_egfr_mean_{_g}": f"Табл. 1: средняя рСКФ по CKD-EPI 2021, мл/мин/1,73 м², {_name} (2 знака)",
        f"T2_hba1c_change_{_g}": f"Табл. 2: среднее изменение HbA1c к 6 месяцам, {_name} (2 знака)",
        f"T2_composite_pct_{_g}": f"Табл. 2: целевой HbA1c без гипогликемий, % от оценённых, {_name} (1 знак)",
        f"T2_ldl_m12_mean_{_g}": f"Табл. 2: средний ХС-ЛНП на 12 месяцах, ммоль/л, {_name} (2 знака)",
    })
KEY_DESC.update({
    "T3_crude_rr": "Табл. 3: нескорректированное отношение рисков, А относительно Б (3 знака)",
    "T3_mh_rr": "Табл. 3: отношение рисков по Мантелю–Хензелю, А относительно Б (3 знака)",
    "T3_crude_rd": "Табл. 3: нескорректированная разность рисков А − Б, п. п. (2 знака)",
    "T3_mh_rd": "Табл. 3: разность рисков по Мантелю–Хензелю А − Б, п. п. (2 знака)",
    "T3_mh_or": "Табл. 3: отношение шансов по Мантелю–Хензелю, А относительно Б (3 знака)",
})


# ==========================================================================
# Build, task callables
# ==========================================================================

# ==========================================================================
# LLM rewriting (scripts/rewrite_long_texts.py)
# ==========================================================================
#
# Two kinds of items. (1) Groups of 2–4 consecutive query-log entries: heads stay
# verbatim, bodies are paraphrased; an independent model given the log rules must
# recover, for every entry, the corrected field, old/new values, the date the entry
# refers to and whether the change is applied (lock date, supplements, cancellations).
# (2) Rule-carrying sections of appendices A, B, D and J: the model must recover the
# rule parameters the section states. Ground truth comes from the world model and
# from the hand-written parameter table below, never from the rendered text.

_FIELD = {"birth": "birth_date", "late_birth": "birth_date", "sex": "sex", "late_sex": "sex",
          "consent": "consent", "enroll": "enroll_date", "unit": "hba1c_unit", "typo": "hba1c_value",
          "vdate": "visit_date", "weight": "weight", "weight_fill": "weight", "smoking": "smoking",
          "therapy": "therapy", "height": "height", "hypo": "hypo", "creat_unit": "creat_unit",
          "ldl_typo": "ldl_value", "vcode": "visit_code", "confirm": "none"}


def _entry_truth(e: dict) -> dict:
    k = e["kind"]
    t: dict = {"field": _FIELD[k], "applied": k not in ("confirm", "late_sex", "late_birth")}
    if k in ("birth", "enroll", "vdate", "late_birth"):
        t["new"] = _d(e["new"])
        if "old" in e:
            t["old"] = _d(e["old"])
    elif k in ("sex", "late_sex", "smoking", "therapy", "vcode"):
        t["new"] = e["new"]
    elif k in ("typo", "weight", "height", "hypo", "ldl_typo", "weight_fill"):
        t["new"] = e["new"]
        if "old" in e:
            t["old"] = e["old"]
    elif k in ("unit", "creat_unit"):
        t["new"] = "pct" if k == "unit" else "mgdl"
        t["old"] = e["value"]
    if k == "consent":
        t["applied"] = not (e.get("cancel") and not e.get("cancel_late"))
    if k in ("consent", "unit", "typo", "hypo", "creat_unit", "ldl_typo", "vcode"):
        t["date"] = _d(e["day"])
    if k == "vdate":
        t["visit"] = e["code"]
    return t


def _entry_brief(e: dict) -> str:
    t = _entry_truth(e)
    bits = [f"поле {t['field']}"] + [f"{k} = {t[k]}" for k in ("old", "new", "date", "visit") if k in t]
    why = "применяется" if t["applied"] else "НЕ применяется"
    if e["kind"] == "confirm":
        why = "закрыт без изменений (ничего не применяется; новых значений не называть)"
    elif e["kind"].startswith("late_"):
        why = "ответ после закрытия базы — НЕ применяется"
    elif e.get("cancel"):
        why = ("отзыв согласия отменён дополнением до закрытия базы — отзыв НЕ учитывается" if t["applied"] is False
               else "дополнение об отмене пришло после закрытия базы — отзыв согласия учитывается")
    elif "late_supp" in e:
        why = "применяется значение до закрытия базы; дополнение после закрытия базы не применяется"
    elif "first" in e:
        why = "первое исправление ошибочно, действует дополнение с верной датой"
    return f"Запрос № {e['no']}: " + ", ".join(bits) + f"; {why}."


_FIELD_GLOSSARY = """\
Коды полей (field): birth_date — дата рождения; sex — пол (1 — мужской, 2 — женский); consent — отзыв \
информированного согласия; enroll_date — дата включения; hba1c_unit — единицы результата HbA1c; hba1c_value — \
значение HbA1c; visit_date — дата визита (visit — V0 или V2); weight — масса тела на базовом визите; smoking — \
статус курения (0 — никогда не курил, 1 — бывший курильщик, 2 — курит сейчас, 9 — нет данных); therapy — код \
терапии (1 — глиптазин, 2 — флозирон); height — рост, см; hypo — число эпизодов гипогликемии на визите; \
creat_unit — единицы результата креатинина; ldl_value — значение ХС-ЛНП; visit_code — код визита; none — \
запрос закрыт без изменений."""

_LOG_JUDGE = """\
Ответь только JSON {"entries": {"<номер запроса>": {...}}} — по объекту на каждый запрос этого фрагмента:
- field — код поля (см. выше);
- old — значение в выгрузке, если запись его называет (для единиц — число из выгрузки), иначе не указывай;
- new — итоговое значение по этому запросу с учётом дополнений и правил (дата ДД.ММ.ГГГГ, число, код; для \
единиц — "pct" для процентов или "mgdl" для мг/дл); для отзыва согласия и для закрытых без изменений — не указывай;
- date — дата, к которой относится запись (дата анализа, визита или отзыва согласия), если она названа в \
теле записи (не дата регистрации запроса); для дат рождения, включения и визита — не указывай;
- visit — V0 или V2 только для исправления даты визита;
- applied — true, если по правилам журнала изменение применяется к данным (для отзыва согласия — если \
согласие в итоге считается отозванным), иначе false (закрыт без изменений; ответ после закрытия базы; \
отзыв согласия отменён дополнением до закрытия базы)."""


def _log_judge_system() -> str:
    return ("Ты проверяешь записи журнала запросов на уточнение данных когортного исследования. Дата закрытия "
            "базы данных — 16.10.2023.\n\n" + LOG_RULES + "\n" + _FIELD_GLOSSARY + "\n\n" + _LOG_JUDGE)


def _val_eq(want, got) -> bool:
    if isinstance(want, str):
        g = str(got).strip().casefold()
        if re.fullmatch(r"\d\d\.\d\d\.\d{4}", want):
            m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", g)
            return bool(m) and (int(m[1]), int(m[2]), int(m[3])) == tuple(map(int, want.split(".")))
        return g == want.casefold()
    if isinstance(want, bool):
        if isinstance(got, str):
            got = got.strip().casefold() in ("true", "да", "yes")
        return got is want
    try:
        return abs(float(str(got).replace(",", ".")) - float(want)) < 1e-6
    except ValueError:
        return False


def _log_accept(item: dict, got) -> list[str]:
    entries = got.get("entries") if isinstance(got, dict) else None
    if not isinstance(entries, dict):
        return ["не удалось разобрать записи"]
    bad = []
    for no, t in item["truth"].items():
        g = entries.get(str(no))
        if not isinstance(g, dict):
            bad.append(f"запрос № {no}: не найден")
            continue
        wrong = [k for k, v in t.items() if not _val_eq(v, g.get(k))]
        if wrong:
            bad.append(f"запрос № {no}: {', '.join(wrong)}")
    return bad


_PARAMS = {
    "status": "статус поправки: active (действует), rejected (отклонена), no_effect (не влияет на анализ)",
    "hba1c_goal_lt": "порог цели HbA1c, %: цель достигнута при значении строго меньше порога",
    "hypo_days": "[первый, последний] день наблюдения визитов, на которых ищут гипогликемию (вторичный исход)",
    "hypo_v0_counted": "учитывается ли базовый визит V0 при поиске гипогликемии",
    "hypo_unplanned_counted": "учитываются ли внеплановые визиты при поиске гипогликемии",
    "hypo_minus99_is_episode": "считается ли значение −99 эпизодом гипогликемии",
    "hypo_denominator": "знаменатель доли гипогликемий: all (все участники выборки группы) или with_visits",
    "hypo_severe_only": "учитываются ли только тяжёлые эпизоды (с помощью третьих лиц)",
    "site5_excl_enrol_before": "центр 5: исключаются участники, включённые раньше этой даты (ДД.ММ.ГГГГ)",
    "site5_excl_step": "номер шага исключения по центру 5 в последовательности исключений",
    "baseline_window": "[от, до] — дни базового интервала относительно V0",
    "m3_window": "[от, до] — дни окна точки 3 месяцев", "m6_window": "[от, до] — дни окна точки 6 месяцев",
    "m12_window": "[от, до] — дни окна точки 12 месяцев (HbA1c)",
    "creat_excl_gt_umol": "исключение при базовом креатинине строго выше порога, мкмоль/л",
    "egfr_exclusion": "есть ли исключение из анализа по рСКФ",
    "egfr_formula": "формула рСКФ, например \"CKD-EPI 2021\"",
    "egfr_creat_divisor": "делитель для перевода креатинина из мкмоль/л в мг/дл перед формулой рСКФ",
    "hba1c_change": "изменение HbA1c: m6_minus_base (6 месяцев минус базовое) или base_minus_m6",
    "composite_hypo_days": "[от, до] — дни визитов с гипогликемией в комбинированном исходе",
    "rr_direction": "отношение рисков: A_vs_B (А относительно Б) или B_vs_A",
    "ldl_m12_window": "[от, до] — дни окна ХС-ЛНП на 12 месяцах",
    "ldl_m12_winsorized": "винзоризуется ли ХС-ЛНП на 12 месяцах",
    "ldl_m12_locf": "подставляется ли последнее значение, если ХС-ЛНП в окне 12 месяцев нет",
    "tie_rule": "два результата на равном удалении от целевого дня: earlier (берётся более ранний) или later",
    "same_day": "несколько результатов одного дня: mean, first или last",
    "winsorized": "список винзоризуемых переменных из: bmi, ldl_base, hba1c, ldl_m12",
    "hba1c_m6_locf": "восстанавливаются ли пропущенные HbA1c на 6 месяцах переносом последнего наблюдения",
    "dedup_keep": "из записей с одним nid_hash остаётся запись с earliest_enrol (самой ранней датой включения) "
                  "или latest_enrol",
    "dup_records_data_used": "используются ли визиты и анализы, внесённые под повторными номерами",
    "harmonize_at": "где приводятся единицы и форматы: analysis (центральной группой при анализе) или site",
    "lock_date": "дата закрытия базы данных (ДД.ММ.ГГГГ)",
    "after_lock_applied": "применяются ли записи и дополнения журнала, поступившие после закрытия базы",
    "group_A_code": "код терапии группы А", "group_B_code": "код терапии группы Б",
    "creat_missing_excludes": "исключается ли участник без базового креатинина",
    "smoker_def": "доля курящих: current_among_known (курящие сейчас среди известного статуса), "
                  "current_or_former или current_among_all",
    "age_at": "на какую дату считается возраст: v0 или enrol",
    "age_groups": "границы возрастных групп [a, b]: группы <a, a…b−1, ≥b",
    "hba1c_valid_range": "[мин, макс] допустимых значений HbA1c, %",
    "hba1c_round": "шаг округления пересчитанного HbA1c, %",
    "consent_from_log": "учитывается ли отзыв согласия из журнала (до закрытия базы) наравне с отметкой выгрузки",
    "hba1c_unit": "единицы HbA1c в выгрузке центра: pct, mmol_mol или pct_then_mmol_mol (сначала %, потом "
                  "ммоль/моль)",
    "hba1c_switch_date": "первая дата забора, с которой HbA1c в ммоль/моль (ДД.ММ.ГГГГ)",
    "ldl_unit": "единицы ХС-ЛНП в выгрузке: mmol_l или mg_dl", "creat_unit": "единицы креатинина: umol_l или mg_dl",
    "date_format": "формат дат в выгрузке: YYYY-MM-DD или DD.MM.YYYY",
    "height_unit": "единицы роста в файле участников, если раздел их называет: m или cm",
    "test_codes": "нестандартные коды тестов центра (список), если раздел их называет",
    "ifcc_a": "коэффициент a в % = a × ммоль/моль + b", "ifcc_b": "слагаемое b в той же формуле",
    "ldl_mgdl_divisor": "ммоль/л = мг/дл ÷ это число", "creat_mgdl_factor": "мкмоль/л = мг/дл × это число",
    "manual_units_from_log": "для результатов, внесённых в других единицах, берутся единицы из журнала запросов",
}
_W12, _BASEW, _HYPO = [335, 395], [-30, 0], [1, 196]
_SECTION_TRUTH = {
    "A/1": {"status": "active", "hypo_days": _HYPO, "hypo_v0_counted": False, "hypo_unplanned_counted": True,
            "hypo_minus99_is_episode": False, "hypo_denominator": "all"},
    "A/2": {"status": "active", "hba1c_goal_lt": 7.0},
    "A/3": {"status": "active", "site5_excl_enrol_before": "01.09.2021", "site5_excl_step": 3},
    "A/4": {"status": "active", "m12_window": _W12, "baseline_window": _BASEW},
    "A/5": {"status": "no_effect"},
    "A/6": {"status": "rejected", "egfr_exclusion": False},
    "A/7": {"status": "active", "creat_excl_gt_umol": 200},
    "A/8": {"status": "no_effect"},
    "A/9": {"status": "active", "egfr_formula": "CKD-EPI 2021", "egfr_creat_divisor": 88.4,
            "hba1c_change": "m6_minus_base", "composite_hypo_days": [1, 182], "rr_direction": "A_vs_B",
            "ldl_m12_winsorized": False, "tie_rule": "earlier", "age_at": "v0"},
    "A/10": {"status": "rejected", "ldl_m12_winsorized": False, "ldl_m12_locf": False},
    "B/1": {"hba1c_unit": "pct", "ldl_unit": "mmol_l", "creat_unit": "umol_l", "date_format": "YYYY-MM-DD"},
    "B/2": {"hba1c_unit": "pct_then_mmol_mol", "hba1c_switch_date": "01.03.2022", "ldl_unit": "mmol_l",
            "creat_unit": "umol_l", "date_format": "YYYY-MM-DD"},
    "B/3": {"hba1c_unit": "pct", "ldl_unit": "mg_dl", "creat_unit": "umol_l", "date_format": "DD.MM.YYYY",
            "ldl_mgdl_divisor": 38.67},
    "B/4": {"hba1c_unit": "pct", "ldl_unit": "mmol_l", "creat_unit": "umol_l", "date_format": "YYYY-MM-DD",
            "height_unit": "m", "test_codes": ["A1C", "LDLC", "CREA"]},
    "B/5": {"hba1c_unit": "pct", "ldl_unit": "mmol_l", "creat_unit": "mg_dl", "date_format": "DD.MM.YYYY",
            "creat_mgdl_factor": 88.4},
    "B/6": {"hba1c_unit": "mmol_mol", "ldl_unit": "mmol_l", "creat_unit": "mg_dl", "date_format": "YYYY-MM-DD"},
    "B/formulas": {"ifcc_a": 0.09148, "ifcc_b": 2.152, "hba1c_round": 0.1, "ldl_mgdl_divisor": 38.67,
                   "creat_mgdl_factor": 88.4},
    "B/manual": {"manual_units_from_log": True},
    "D/1": {"harmonize_at": "analysis"},
    "D/2": {"dedup_keep": "earliest_enrol", "dup_records_data_used": False},
    "D/3": {"hypo_days": _HYPO, "hypo_unplanned_counted": True, "hypo_v0_counted": False,
            "hypo_minus99_is_episode": False, "hypo_severe_only": False},
    "D/4": {"tie_rule": "earlier", "same_day": "mean", "baseline_window": _BASEW, "hba1c_goal_lt": 7.0},
    "D/5": {"m6_window": [168, 196]},
    "D/6": {"winsorized": ["bmi", "ldl_base"], "hba1c_m6_locf": False, "egfr_exclusion": False},
    "D/7": {"ldl_m12_window": _W12, "tie_rule": "earlier", "same_day": "mean", "egfr_creat_divisor": 88.4,
            "ldl_m12_winsorized": False, "ldl_m12_locf": False, "age_at": "v0"},
    "D/8": {"composite_hypo_days": _HYPO, "rr_direction": "A_vs_B", "hba1c_change": "m6_minus_base"},
    "D/9": {"lock_date": "16.10.2023", "after_lock_applied": False, "group_A_code": 1, "group_B_code": 2,
            "creat_missing_excludes": False, "smoker_def": "current_among_known"},
    "J/1-3": {"dedup_keep": "earliest_enrol", "consent_from_log": True},
    "J/4-6": {"age_at": "v0", "age_groups": [50, 65]},
    "J/7-9": {"group_A_code": 1, "group_B_code": 2, "smoker_def": "current_among_known"},
    "J/10-12": {"hba1c_round": 0.1, "hba1c_valid_range": [3.0, 20.0], "same_day": "mean",
                "baseline_window": _BASEW},
    "J/13-15": {"m3_window": [77, 105], "m6_window": [168, 196], "m12_window": _W12, "tie_rule": "earlier",
                "hba1c_goal_lt": 7.0, "hypo_days": _HYPO, "hypo_minus99_is_episode": False,
                "hypo_v0_counted": False},
    "J/16-18": {"site5_excl_enrol_before": "01.09.2021", "site5_excl_step": 3, "creat_excl_gt_umol": 200,
                "egfr_formula": "CKD-EPI 2021", "egfr_creat_divisor": 88.4, "hba1c_change": "m6_minus_base"},
    "J/19-21": {"composite_hypo_days": _HYPO, "ldl_m12_window": _W12, "ldl_m12_winsorized": False,
                "tie_rule": "earlier", "rr_direction": "A_vs_B"},
}


def _section_key(doc: str, heading: str) -> str | None:
    pats = {"A": [(r"## Поправка (\d+)", "A/{}")], "D": [(r"## Заседание № (\d+)", "D/{}")],
            "B": [(r"## Центр (\d)", "B/{}"), (r"## (Формулы)", "B/formulas"), (r"## (Отдельные)", "B/manual")]}
    for pat, fmt in pats[doc]:
        m = re.match(pat, heading)
        if m:
            return fmt.format(m.group(1))
    return None


def _split_doc(doc: str, text: str) -> list[tuple]:
    """("fixed", text) and ("rw", key, prefix, body, suffix) segments; joined they give `text` back."""
    segs: list[tuple] = []
    if doc == "J":
        head, *paras = re.split(r"\n\n(?=\*\*J\.\d+\.)", text)
        segs.append(("fixed", head + "\n\n"))
        for i in range(0, len(paras), 3):
            body = "\n\n".join(paras[i:i + 3])
            core = body.rstrip()
            tail = body[len(core):] + ("\n\n" if i + 3 < len(paras) else "")
            segs.append(("rw", f"J/{i + 1}-{min(i + 3, len(paras))}", "", core, tail))
        return segs
    pieces = re.split(r"(?m)^(?=## )", text)
    segs.append(("fixed", pieces[0]))
    for pc in pieces[1:]:
        heading, _, body = pc.partition("\n")
        key = _section_key(doc, heading)
        if key not in _SECTION_TRUTH:
            segs.append(("fixed", pc))
            continue
        lead = body[: len(body) - len(body.lstrip("\n"))]
        m = re.search(r"(\n---)?\s*$", body)
        core = body[len(lead): m.start()].rstrip()
        segs.append(("rw", key, heading + "\n" + lead, core, body[len(lead) + len(core):]))
    return segs


def _sec_skeleton(text: str) -> tuple:
    return (tuple(re.findall(r"(?m)^\s*(\d+)\. ", text)), tuple(sorted(set(re.findall(r"`[^`\n]+`", text)))),
            tuple(re.findall(r"\*\*J\.\d+\.", text)), bool(re.search(r"(?m)^#", text)))


def _render_doc(doc: str, text: str) -> str:
    out = []
    for seg in _split_doc(doc, text):
        if seg[0] == "fixed":
            out.append(seg[1])
            continue
        _, key, prefix, body, suffix = seg
        new = rewritten(TASK_ID, f"sec/{key}", body).strip()
        if new != body and _sec_skeleton(new) != _sec_skeleton(body):
            new = body
        out.append(prefix + new + suffix)
    return "".join(out)


def _docs() -> dict[str, str]:
    return {"A": AMENDMENTS, "B": LABS_APPX, "D": COMMITTEE, "J": DERIVED}


def _param_text(k: str, v) -> str:
    return f"{k} = {json.dumps(v, ensure_ascii=False)} ({_PARAMS[k]})"


def _rw_items() -> list[dict]:
    items = []
    for g in _log_groups():
        truth = {x["e"]["no"]: _entry_truth(x["e"]) for x in g["entries"]}
        brief = ("Фрагмент журнала запросов (приложение C). Заголовки записей «**Запрос № …** (дата, монитор). "
                 "Участник `…`.» сохрани дословно в начале строки. Что должно однозначно следовать из каждой "
                 "записи:\n" + "\n".join(_entry_brief(x["e"]) for x in g["entries"]))
        items.append({"kind": "log", "key": g["key"], "draft": g["draft"], "brief": brief, "truth": truth})
    for doc, text in _docs().items():
        for seg in _split_doc(doc, text):
            if seg[0] != "rw":
                continue
            truth = _SECTION_TRUTH[seg[1]]
            brief = (f"Раздел приложения {doc} рукописи исследования ГЛИКОНТ"
                     + (f" («{seg[2].strip().lstrip('# ')}»)" if seg[2].strip() else "")
                     + ". Правила и значения, которые читатель должен однозначно извлечь из раздела (остальное "
                     "содержание тоже сохранить):\n" + "\n".join(_param_text(k, v) for k, v in truth.items()))
            items.append({"kind": "sec", "key": f"sec/{seg[1]}", "draft": seg[3], "brief": brief,
                          "heading": seg[2].strip(), "truth": truth})
    return items


def _sec_judge_system() -> str:
    return ("Ты читаешь один раздел приложения к рукописи когортного исследования и выписываешь правила анализа "
            "данных, которые этот раздел устанавливает или подтверждает. Если раздел называет прежнее значение и "
            "заменяет его новым, укажи новое. Для отклонённой поправки укажи status и то, что остаётся в силе "
            "(например, что исключения нет), но не предлагавшиеся значения. Параметры, о которых раздел ничего не "
            "говорит, не указывай.\n\nПараметры (код — смысл):\n"
            + "\n".join(f"- {k} — {v}" for k, v in _PARAMS.items())
            + "\n\nЧисла — числами, даты — строкой ДД.ММ.ГГГГ, интервалы — списком [от, до], да/нет — true/false. "
              'Ответ — только JSON {"params": {"код": значение, ...}}.')


def _judge_messages(item: dict, text: str) -> list[dict]:
    if item["kind"] == "log":
        item["_skel_ok"] = _log_skel_ok(item["draft"], text)
        return [{"role": "system", "content": _log_judge_system()},
                {"role": "user", "content": "Фрагмент журнала:\n<<<\n" + text + "\n>>>"}]
    item["_skel_ok"] = _sec_skeleton(text) == _sec_skeleton(item["draft"])
    return [{"role": "system", "content": _sec_judge_system()},
            {"role": "user", "content": (item["heading"] + "\n\n" if item["heading"] else "") + text}]


def _param_eq(want, got) -> bool:
    if isinstance(want, list):
        if not isinstance(got, list) or len(got) != len(want):
            return False
        if want and isinstance(want[0], str):
            return sorted(str(x).strip().casefold() for x in got) == sorted(x.casefold() for x in want)
        return all(_val_eq(w, g) for w, g in zip(want, got, strict=True))
    return _val_eq(want, got)


def _judge_accept(item: dict, reply: str) -> tuple[bool, str]:
    if not item.get("_skel_ok", True):
        return False, ("нарушена форма: заголовки записей, пометки «*Дополнение от ДД.ММ.ГГГГ:*», номера пунктов, "
                       "идентификаторы в `…` должны совпадать с черновиком; не добавляй строк с «#»")
    match = re.search(r"\{.*\}", reply, re.S)
    try:
        got = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        got = None
    if item["kind"] == "log":
        bad = _log_accept(item, got)
    else:
        params = got.get("params") if isinstance(got, dict) else None
        if not isinstance(params, dict):
            bad = ["не удалось выписать правила"]
        else:
            bad = [k for k, v in item["truth"].items() if not _param_eq(v, params.get(k))]
    if not bad:
        return True, ""
    return False, ("независимый читатель по правилам понял иначе: " + "; ".join(bad[:8])
                   + ". Сделай эти места однозначными, ничего не добавляя.")


_WRITER_SYSTEM = (
    "Ты редактируешь материалы многоцентрового когортного исследования ГЛИКОНТ (сахарный диабет 2 типа). Тебе "
    "дают либо фрагмент журнала запросов на уточнение данных (несколько записей группы мониторинга), либо один "
    "раздел приложения к рукописи (поправка к протоколу, описание лаборатории центра, решения заседания "
    "комитета, спецификация производных переменных). Черновик местами собран из шаблонов; перепиши его живым "
    "профессиональным языком: свои слова и порядок изложения, разная длина фраз, без канцелярских клише и "
    "опечаток; в журнале у разных мониторов и центров разная манера (кто-то сух, кто-то подробно пересказывает "
    "переписку с центром). Не повторяй обороты черновика дословно. Длина — от 1,0 до 1,5 длины черновика.\n\n"
    "Жёсткие требования:\n"
    "- в журнале каждая запись начинается с новой строки заголовком «**Запрос № N** (ДД.ММ.ГГГГ, монитор …). "
    "Участник `…`.» — его сохрани дословно, порядок записей не меняй, новых записей не добавляй; пометку "
    "дополнения пиши дословно как «*Дополнение от ДД.ММ.ГГГГ:*» с той же датой в той же записи;\n"
    "- сохрани все даты, числа, коды, единицы и решения: что исправлено, с какого значения на какое, к какой "
    "дате (анализа, визита, отзыва согласия) относится запись, что запрос закрыт без изменений, что ответ "
    "пришёл после закрытия базы, что дополнение отменяет или уточняет прежнее решение; не добавляй новых "
    "значений, исправлений и правил и не называй значений там, где их нет в черновике;\n"
    "- в разделах приложений сохрани каждое правило, число, дату, статус поправки и решение; пронумерованные "
    "пункты оставь пунктами с теми же номерами в начале строки; идентификаторы в `обратных кавычках` и "
    "метки вида **J.N.** сохрани дословно; формулы можно оформить иначе, но с теми же коэффициентами; "
    "строк, начинающихся с «#», не добавляй.\n\n"
    "Правила журнала запросов (для понимания):\n\n" + LOG_RULES
)

REWRITE = {
    "items": _rw_items,
    "writer_system": _WRITER_SYSTEM,
    "judge_messages": _judge_messages,
    "judge_accept": _judge_accept,
}


@functools.cache
def build() -> dict[str, str]:
    files = {
        "paper/00_abstract.md": ABSTRACT,
        "paper/01_introduction.md": INTRO,
        "paper/02_methods_design.md": METHODS_DESIGN,
        "paper/03_methods_measurements.md": METHODS_MEASURE,
        "paper/04_methods_statistics.md": METHODS_STATS,
        "paper/05_results.md": RESULTS,
        "paper/06_discussion.md": DISCUSSION,
        "paper/appendix_A_amendments.md": _render_doc("A", AMENDMENTS),
        "paper/appendix_B_laboratories.md": _render_doc("B", LABS_APPX),
        "paper/appendix_D_committee.md": _render_doc("D", COMMITTEE),
        "paper/appendix_E_site_reports.md": render_site_reports(),
        "paper/appendix_F_sap_v1.md": SAP_V1,
        "paper/appendix_G_worked_examples.md": EXAMPLES,
        "paper/appendix_H_edit_checks.md": EDIT_CHECKS,
        "paper/appendix_I_monitoring_visits.md": render_monitoring(),
        "paper/appendix_J_derived_variables.md": _render_doc("J", DERIVED),
        "paper/appendix_K_analyst_questions.md": ANALYST_QA,
        "paper/appendix_L_site_correspondence.md": render_letters(),
        "codebook/README.md": CODEBOOK_README,
        "codebook/manifest.md": render_manifest(),
        "codebook/participants.md": CODEBOOK_PART,
        "codebook/visits.md": CODEBOOK_VIS,
        "codebook/lab.md": CODEBOOK_LAB,
        **{_log_file(k): text for k, text in enumerate(render_log(), 1)},
        "results_template.json": json.dumps({k: KEY_DESC[k] for k in KEYS}, ensure_ascii=False, indent=2) + "\n",
    }
    return files


@functools.cache
def truth() -> dict[str, float | int]:
    return rounded(analyze())


def setup(ws: Path) -> None:
    for rel, text in build().items():
        write(ws, rel, text)
    for rel, (header, rows) in export_files().items():
        write_csv(ws, rel, header, rows)


def gold(ws: Path) -> None:
    write_json(ws, "results.json", truth())


def _ok(key: str, got: object, exp: float | int) -> bool:
    if isinstance(got, bool) or not isinstance(got, int | float):
        if isinstance(got, str):
            try:
                got = float(got.replace(",", "."))
            except ValueError:
                return False
        else:
            return False
    if not math.isfinite(float(got)):
        return False
    if key in COUNT_KEYS:
        return float(got) == float(exp)
    tol = max(0.5 * 10 ** -DECIMALS[key], 1e-3 * abs(float(exp))) + 1e-9
    return abs(float(got) - float(exp)) <= tol


def check(ws: Path) -> str:
    data = read_json(ws, "results.json")
    assert isinstance(data, dict), "results.json должен быть JSON-объектом"
    exp = truth()
    ok = 0
    errors = []
    for k in KEYS:
        if k not in data:
            errors.append(f"{k}: нет ключа")
        elif _ok(k, data[k], exp[k]):
            ok += 1
        else:
            errors.append(f"{k}: неверно")
    return require_share(len(KEYS), ok, min_share=0.95, what="значения results.json", errors=errors)


def _near(opts: dict, name: str):
    def damage(ws: Path) -> None:
        write_json(ws, "results.json", rounded(analyze(opts)))
    damage.__name__ = name
    return damage


NEAR_MISSES = [
    _near({"amend_threshold": False}, "skip_threshold_amendment"),
    _near({"units": False}, "no_lab_unit_conversion"),
    _near({"order": ("dup", "consent", "no_v0", "age", "no_hba1c", "creat", "site5")},
          "site5_exclusion_in_wrong_place"),
    _near({"corrections": False}, "ignore_data_query_log"),
    _near({"age_at": "enroll"}, "age_at_enrolment"),
    _near({"amend_window12": False, "amend_creat": False}, "skip_window_and_creatinine_amendments"),
    _near({"corrections": False, "units": False, "dedupe": False, "replicates": "first", "winsor": False,
           "amend_threshold": False, "amend_site5": False, "amend_window12": False, "amend_creat": False,
           "change_sign": -1, "composite_days": 182, "ldl_half12": 14, "rr_inverse": True},
          "naive_methods_text_only"),
    _near({"late_supp": True}, "log_supplements_after_lock_applied"),
    _near({"cancel_ignored": True}, "cancelled_consent_withdrawals_applied"),
    _near({"letters": True}, "site_letters_treated_as_corrections"),
    _near({"change_sign": -1, "composite_days": 182, "rr_inverse": True, "egfr_creat": "umol"},
          "amendment9_draft_and_sap_definitions"),
    _near({"ldl_half12": 14, "egfr_creat": "umol"}, "ldl_window_v1_and_egfr_in_umol"),
]

PROMPT = (
    "Нужно воспроизвести числовые результаты статьи по её методам и сырым данным. В рабочей папке:\n"
    "- `paper/` — рукопись многоцентрового когортного исследования ГЛИКОНТ по разделам (введение, "
    "методы, структура результатов, обсуждение) и приложения: поправки к протоколу, лабораторные "
    "методы центров, журнал запросов на уточнение данных, решения руководящего комитета;\n"
    "- `codebook/` — описание файлов данных;\n"
    "- `data/site1` … `data/site6` — выгрузки шести центров (участники, визиты, лабораторные "
    "результаты) в формате CSV;\n"
    "- `results_template.json` — 50 ключей с описанием величин (таблицы 1–3, блок-схема и "
    "рисунок 2).\n\n"
    "Создай файл `results.json` — JSON-объект с теми же 50 ключами, что в шаблоне, где каждому ключу "
    "сопоставлено число (не строка), рассчитанное строго по методике статьи с учётом всех поправок, "
    "исправлений данных и решений, описанных в материалах, и округлённое по правилам статьи. "
    "Документы написаны свободным текстом, правила распределены по разным файлам, и более поздние "
    "решения могут менять более ранние — важно учесть каждый документ.\n\n"
    "Проверка: численности должны совпасть точно, остальные величины — с точностью до округления "
    "(относительная погрешность до 0,1 %). Задача засчитывается, если верны не меньше 48 значений "
    "из 50."
)

TASK = long_task(
    id="task_405_paper_reproduce",  # registry id; TASK_ID stays the generator seed
    name="Воспроизведение результатов когортного исследования по статье и сырым данным",
    prompt=PROMPT,
    setup=setup,
    gold=gold,
    check=check,
    tags=("reproduce", "statistics", "reading"),
)

SAP_V1 = """# Приложение F. План статистического анализа, версия 1.0 (полный текст)

*Утверждён руководящим комитетом 21.04.2020 (заседание № 1). Текст приводится в исходной
редакции. Изменения, внесённые поправками к протоколу, в этот текст не внесены — они
перечислены в приложении А и имеют приоритет. Решения комитета, уточняющие отдельные
положения, приведены в приложении D.*

## F.1. Общие положения

F.1.1. Настоящий план определяет популяции анализа, конечные точки, методы описания данных и
сравнения групп для исследования ГЛИКОНТ. Анализ выполняется после закрытия базы данных.

F.1.2. Все отклонения от настоящего плана, принятые до закрытия базы данных, оформляются
поправками к протоколу или решениями руководящего комитета и документируются.

F.1.3. Статистическая значимость в настоящем исследовании не является основой выводов;
результаты представляются как точечные оценки величины эффекта.

## F.2. Источники данных и подготовка

F.2.1. Источниками являются выгрузки шести центров: файлы участников, визитов и лабораторных
результатов, описанные в кодовой книге.

F.2.2. Подготовка данных включает: приведение дат к единому формату; приведение кодов
лабораторных тестов к стандартным (`HBA1C`, `LDL`, `CREAT`); удаление полностью повторяющихся
строк визитов; применение исправлений по журналу запросов, зарегистрированных до закрытия базы;
пересчёт лабораторных результатов в единые единицы (HbA1c — %, ХС-ЛНП — ммоль/л, креатинин —
мкмоль/л) по таблице единиц центров.

F.2.3. Пересчёт HbA1c из ммоль/моль выполняется по формуле `0,09148 × x + 2,152` с округлением
до 0,1 %. Результаты вне диапазона 3,0–20,0 % исключаются как неправдоподобные.

F.2.4. Несколько результатов одного теста у одного участника в один день заменяются их средним
арифметическим.

## F.3. Популяции анализа

F.3.1. **Аналитическая выборка** формируется последовательным исключением записей в порядке:
(1) дубликаты по `nid_hash` — сохраняется запись с наиболее ранней датой включения; (2) отзыв
согласия; (3) отсутствие визита V0; (4) возраст на дату V0 вне 18–79 полных лет; (5)
отсутствие базового HbA1c; (6) базовый креатинин выше 177 мкмоль/л.

F.3.2. **Оценённые на 6 месяцах** — участники аналитической выборки, у которых есть значение
HbA1c в окне точки 6 месяцев.

F.3.3. Группы: А — код терапии 1 (глиптазин), Б — код терапии 2 (флозирон).

## F.4. Временные точки

F.4.1. День наблюдения — число суток от даты визита V0 до даты события (забора крови, визита).

F.4.2. Базовое значение лабораторного показателя — результат с наиболее поздней датой в
интервале дней −30…0.

F.4.3. Окна временных точек: 3 месяца — дни 77–105; 6 месяцев — дни 168–196; 12 месяцев —
дни 351–379. В окне выбирается результат, ближайший к целевому дню (91, 182, 365).

## F.5. Конечные точки

F.5.1. Первичная: HbA1c на точке 6 месяцев менее 7,5 %.

F.5.2. Вторичные: средний HbA1c на базовом обследовании и точках 3, 6, 12 месяцев.

## F.6. Описательная статистика

F.6.1. Непрерывные переменные — среднее; категориальные — доля в процентах.

F.6.2. ИМТ и базовый ХС-ЛНП винзоризуются по 1-му и 99-му перцентилям, рассчитанным методом
ближайшего ранга по всей аналитической выборке.

F.6.3. Возраст — в полных годах на дату V0; ИМТ — масса на визите V0, делённая на квадрат роста
в метрах.

## F.7. Сравнение групп

F.7.1. Разность долей достигших первичной конечной точки (А − Б) в процентных пунктах —
нескорректированная и по Мантелю–Хензелю со стратификацией по возрастной группе (<50, 50–64,
≥65 лет) и полу.

F.7.2. Отношение шансов по Мантелю–Хензелю (А относительно Б) с той же стратификацией.

F.7.3. Прямая стандартизация долей по тем же стратам; стандарт — все оценённые на 6 месяцах.

## F.8. Представление результатов

F.8.1. Численности — целыми числами; доли — с одним знаком после запятой; средние — с двумя;
разности рисков — с двумя; отношения шансов — с тремя. Округление — «половина вверх».

F.8.2. Промежуточные вычисления не округляются, за исключением пересчёта HbA1c (F.2.3).

## F.9. Анализы чувствительности (не входят в основные таблицы)

F.9.1. Первичный анализ повторяется без винзоризации.

F.9.2. Первичный анализ повторяется с исключением центра, давшего наибольшее число
участников.

F.9.3. Описывается снижение HbA1c к 6 месяцам, определяемое как базовое значение минус значение
на точке 6 месяцев (положительное значение — снижение), по оценённым на 6 месяцах участникам.

F.9.4. Рассчитывается отношение рисков недостижения цели, группа Б относительно группы А, без
стратификации.

Результаты анализов чувствительности приводятся в дополнительных материалах и в таблицы 1–3
не входят.
"""

SITE_NOTES = {
    1: ("В ноябре 2021 г. лаборатория заменила анализатор HbA1c на модель следующего поколения того же "
        "производителя. В декабре 2021 г. ЛИС центра две недели работала в тестовом режиме с выдачей HbA1c "
        "в ммоль/моль, однако эти результаты в рабочую базу и в выгрузку не попадали: все результаты "
        "HbA1c центра 1 в выгрузке — в процентах.",
        "Нарушений процедуры получения согласия не выявлено."),
    2: ("1 марта 2022 г. лаборатория перешла на новую лабораторную информационную систему. С этой даты "
        "(по дате забора) HbA1c выдаётся в ммоль/моль целыми числами; результаты, полученные до 1 марта "
        "2022 г., остались в процентах и не пересчитывались. В выгрузке оба вида результатов идут под одним "
        "кодом HBA1C без указания единиц.",
        "Монитор рекомендовал центру сверить несколько результатов конца февраля — начала марта 2022 г.; "
        "расхождений между датами забора в ЛИС и в карте не найдено."),
    3: ("Лаборатория выдаёт ХС-ЛНП в мг/дл; в 2023 г. обсуждался переход на ммоль/л, но в период "
        "исследования он не реализован. HbA1c и креатинин — в процентах и мкмоль/л соответственно. Даты в "
        "выгрузке центра 3 записаны как ДД.ММ.ГГГГ.",
        "Отмечено несколько случаев, когда базовые анализы выполнялись раньше чем за месяц до визита V0; "
        "такие результаты не считаются базовыми (раздел 3.3.2)."),
    4: ("Анализы выполняет централизованная городская лаборатория. Коды тестов в выгрузке — A1C, LDLC и "
        "CREA. Рост в файле участников записан в метрах. Переход на выдачу HbA1c в ммоль/моль "
        "планировался в 2022 г., но не состоялся.",
        "Центр предлагал перевести рост в сантиметры на своей стороне; комитет решил выполнять пересчёт "
        "при анализе (приложение D, заседание № 1)."),
    5: ("HbA1c — ВЭЖХ, проценты; ХС-ЛНП — ммоль/л; креатинин — мг/дл с двумя знаками после запятой. Даты — "
        "ДД.ММ.ГГГГ. По итогам аудита 2021 г. проведена повторная сертификация центра; участники, "
        "включённые до 1 сентября 2021 г., исключаются из анализа (поправка 3).",
        "После повторной сертификации нарушений процедуры согласия не выявлено. Даты включения нескольких "
        "участников уточнялись по журналу скрининга (приложение C)."),
    6: ("Лаборатория центра участвует в программе стандартизации IFCC и выдаёт HbA1c только в ммоль/моль "
        "(целыми числами) на протяжении всего исследования. Креатинин — в мг/дл, ХС-ЛНП — в ммоль/л.",
        "Отдельные результаты HbA1c, полученные в сторонних лабораториях и внесённые вручную в процентах, "
        "отмечены в журнале запросов."),
}


def render_site_reports() -> str:
    W = build_world()
    ex = export_files()
    r = random.Random(f"{TASK_ID}/sites")
    base = {v.pid: v.date for v in W["visits"] if v.code == "V0"}
    out = ["""# Приложение E. Итоговые отчёты мониторинга по центрам

Отчёты подготовлены группой мониторинга к закрытию базы данных. Приведённые численности
относятся к **строкам выгрузок** (до удаления дубликатов, исключений и исправлений) и служат для
контроля полноты передачи данных; они не являются числами блок-схемы.
"""]
    for site, (city, inst) in SITES.items():
        part_rows = ex[f"data/site{site}/participants.csv"][1]
        vis_rows = sum(len(rows) for rel, (_, rows) in ex.items() if rel.startswith(f"data/site{site}/visits_"))
        lab_rows = sum(len(rows) for rel, (_, rows) in ex.items() if rel.startswith(f"data/site{site}/lab_"))
        people = [p for p in W["people"] if p.site == site]
        enr = sorted(p.enroll for p in people)
        withdrawn_flag = sum(int(row[8]) for row in part_rows)
        v3 = [(v.date - base[v.pid]).days for v in W["visits"]
              if v.code == "V3" and v.pid in base and W["by_pid"].get(v.pid) and W["by_pid"][v.pid].site == site]
        late = sum(abs(d - 365) > 14 for d in v3)
        delays = sorted((base[p.pid] - p.enroll).days for p in people if p.pid in base)
        med = delays[len(delays) // 2]
        lab_note, extra = SITE_NOTES[site]
        site_labs = [lab for lab in W["labs"] if lab.pid[:2] == f"{site:02d}"]
        n_creat = sum(lab.test == "CREAT" for lab in site_labs)
        n_ldl = sum(lab.test == "LDL" for lab in site_labs)
        out.append(f"""
## Центр {site}: {city}

**Учреждение:** {inst}.

**Включение.** Первый участник включён {_d(enr[0])}, последний — {_d(enr[-1])}. Строк в файле
участников центра: {len(part_rows)}; из них с отметкой об отзыве согласия на момент выгрузки:
{withdrawn_flag}. Медиана интервала от даты включения до базового визита V0 — {med} дн.

**Визиты.** Строк в файлах визитов центра: {vis_rows} (включая полностью повторяющиеся строки,
возникшие при выгрузке). Визитов V3: {len(v3)}; из них с отклонением от 365-го дня более чем на
14 дней: {late}.

**Лаборатория.** Строк в лабораторных файлах центра: {lab_rows}. {lab_note} Результатов
креатинина в выгрузке центра: {n_creat}; ХС-ЛНП: {n_ldl}.

**Качество данных.** {extra} {r.choice([
            "Запросы по центру закрывались в среднем за три недели.",
            "Большинство запросов касалось опечаток при вводе дат и числовых значений.",
            "Существенных систематических ошибок ввода не выявлено.",
        ])}
""")
    return "".join(out)

EXAMPLES = """# Приложение G. Учебные примеры применения правил анализа

Примеры составлены статистиком для обучения второго аналитика. Участники в примерах
**условные** (их нет в выгрузках), числа подобраны для иллюстрации. Примеры учитывают
поправки к протоколу, действующие на момент закрытия базы.

## G.1. Базовое значение и временные точки

Условный участник: визит V0 состоялся 10.03.2021. Результаты HbA1c (все в процентах):

| Дата забора | День наблюдения | Значение |
|---|---|---|
| 01.02.2021 | −37 | 8,9 |
| 20.02.2021 | −18 | 8,6 |
| 08.03.2021 | −2 | 8,4 |
| 01.09.2021 | 175 | 7,1 |
| 15.09.2021 | 189 | 6,9 |

Базовое значение — результат 08.03.2021 (8,4 %): это самая поздняя дата в интервале от −30 до 0.
Результат 01.02.2021 в базовый интервал не попадает.

Для точки 6 месяцев (целевой день 182, окно 168–196) в окно попадают дни 175 и 189; оба удалены
от целевого дня на 7 дней, поэтому выбирается более ранний — 7,1 %. Участник **не достиг**
целевого уровня (7,1 не меньше 7,0). Если бы 06.09.2021 (день 180) был ещё один результат,
выбрали бы его как ближайший к дню 182.

## G.2. Пересчёт единиц и повторные результаты

Условный участник центра 2. Результат HbA1c от 25.02.2022 равен 7,7 — это проценты (до перехода
ЛИС центра 2 на ммоль/моль 01.03.2022). Результат от 05.03.2022 равен 61 — это ммоль/моль:
0,09148 × 61 + 2,152 = 7,73228, после округления 7,7 %.

Если 05.03.2022 есть два результата — 61 и 63 ммоль/моль, — каждый пересчитывается и
округляется (7,7 % и 7,9 %), после чего берётся среднее: 7,8 %.

Результат 74 в центре с выдачей в процентах (например, потерянная запятая) неправдоподобен
(вне 3,0–20,0 %) и исключается, если в журнале запросов нет исправления; если исправление
есть, используется исправленное значение.

## G.3. Возраст

Условный участник родился 15.06.1941, включён 01.06.2021 (на эту дату ему 79 полных лет), визит
V0 — 20.06.2021 (на эту дату — 80 полных лет). Участник исключается по возрасту, поскольку
возраст определяется на дату V0.

## G.4. Винзоризация

Если в аналитической выборке известен ИМТ у 250 участников, то 1-й перцентиль — это значение с
рангом ⌈0,01 × 250⌉ = ⌈2,5⌉ = 3 в упорядоченном по возрастанию ряду, 99-й перцентиль — значение
с рангом ⌈0,99 × 250⌉ = ⌈247,5⌉ = 248. Два наименьших значения заменяются третьим по величине,
два наибольших — 248-м. Перцентили считаются один раз по обеим группам вместе, затем
рассчитываются средние по группам.

## G.5. Метод Мантеля–Хензеля и стандартизация

Условные данные в двух стратах (обозначения раздела 4.5):

| Страта | a (А, цель) | n1 (А, всего) | c (Б, цель) | n0 (Б, всего) |
|---|---|---|---|---|
| 1 | 20 | 50 | 30 | 60 |
| 2 | 10 | 40 | 25 | 50 |

Разность рисков по Мантелю–Хензелю:

* числитель: (20·60 − 30·50)/110 + (10·50 − 25·40)/90 = −2,72727 − 5,55556 = −8,28283;
* знаменатель: 50·60/110 + 40·50/90 = 27,27273 + 22,22222 = 49,49495;
* RD_MH = −0,167347, то есть **−16,73** процентного пункта.

Отношение шансов: b = 30 и 30, d = 30 и 25;
числитель 20·30/110 + 10·25/90 = 8,23232; знаменатель 30·30/110 + 30·25/90 = 16,51515;
OR_MH = **0,498**.

Прямая стандартизация: всего оценённых 200, веса страт 110/200 = 0,55 и 90/200 = 0,45.
Группа А: 0,55 · 0,40 + 0,45 · 0,25 = 0,3325 → **33,3 %** (округление половины вверх);
группа Б: 0,55 · 0,50 + 0,45 · 0,50 = **50,0 %**.

## G.6. Гипогликемия

Условный участник: V0 — день 0 (`hypo_events` = 2, не учитывается); V1 — день 95
(`hypo_events` = 0); внеплановый визит — день 150 (`hypo_events` = −99, информации нет); V2 —
день 201 (`hypo_events` = 1, вне интервала 1–196). Участник **не считается** перенёсшим
гипогликемию. Если бы внеплановый визит на день 150 имел `hypo_events` = 1, участник считался бы
перенёсшим гипогликемию.

## G.7. Порядок исключений

Условная запись отозвала согласие и не имеет визита V0. На блок-схеме она учитывается только на
шаге «отзыв согласия», потому что этот шаг идёт раньше шага «нет визита V0». Условная запись
центра 5, включённая 20.08.2021, у которой нет базового HbA1c, учитывается на шаге «центр 5, до
01.09.2021» (шаг 3), а не на шаге «нет базового HbA1c».

## G.8. рСКФ по CKD-EPI 2021 (поправка 9)

Условная участница 67 лет (на дату V0), базовый креатинин 97 мкмоль/л. Для формулы креатинин
переводится в мг/дл: 97 ÷ 88,4 = 1,09729 мг/дл. Для женщин κ = 0,7, α = −0,241:
Scr/κ = 1,56755; min(1,56755; 1) = 1, поэтому множитель с α равен 1; max(1,56755; 1)^(−1,200) =
0,58309; 0,9938^67 = 0,65922. Итого 142 × 1 × 0,58309 × 0,65922 = 54,583, с женским множителем
1,012 — **55,24** мл/мин/1,73 м² (округлено здесь только для примера; в расчёте среднего
используются неокруглённые значения).

Условный участник 54 лет, базовый креатинин 62 мкмоль/л (0,70136 мг/дл). Для мужчин κ = 0,9,
α = −0,302: Scr/κ = 0,77929; min(…)^α = 0,77929^(−0,302) = 1,07822; max(…) = 1; 0,9938^54 =
0,71474. рСКФ = 142 × 1,07822 × 0,71474 = **109,43**.

Типичная ошибка — подставить в формулу креатинин в мкмоль/л (97 вместо 1,097): получится
значение около 0,25, лишённое смысла.

## G.9. Отношение рисков по Мантелю–Хензелю (поправка 9)

Для условных данных примера G.5: числитель Σ a·n0/N = 20·60/110 + 10·50/90 = 10,90909 + 5,55556 =
16,46465; знаменатель Σ c·n1/N = 30·50/110 + 25·40/90 = 13,63636 + 11,11111 = 24,74747;
RR_MH = **0,665**. Нескорректированное отношение рисков: доля в группе А 30/90 = 0,33333, в
группе Б 55/110 = 0,5; RR = 0,33333 ÷ 0,5 = **0,667** (А относительно Б).

## G.10. Комбинированный исход и изменение HbA1c (поправка 9 с изменением заседания № 8)

Условный участник группы А: базовый HbA1c 8,4 %, на точке 6 месяцев — 6,8 % (цель достигнута);
изменение HbA1c = 6,8 − 8,4 = **−1,6**. На внеплановом визите на 190-й день зарегистрирован
один эпизод гипогликемии. По интервалу 1–196 (заседание № 8) эпизод учитывается, поэтому
комбинированный исход **не достигнут**. По первоначальной редакции поправки 9 (дни 1–182)
этот эпизод не учитывался бы — эта редакция не действует.
"""

EDIT_CHECKS = """# Приложение H. Проверки при вводе данных в ЭИРК

Перечень автоматических проверок электронной индивидуальной регистрационной карты (ЭИРК).
Проверки срабатывают **при вводе** и выводят предупреждение для исследователя; значение,
вызвавшее предупреждение, может быть сохранено после подтверждения. Диапазоны проверок ввода
**не являются правилами анализа**: правила анализа приведены в разделах 2–4 и приложениях А–D.

| № | Поле | Условие предупреждения | Действие |
|---|---|---|---|
| 1 | Дата рождения | возраст на дату включения < 18 или > 85 лет | подтвердить |
| 2 | Дата включения | раньше 01.03.2020 или позже 30.06.2022 | запрет ввода |
| 3 | Пол | не 1 и не 2 | запрет ввода |
| 4 | Рост | < 130 или > 210 см | подтвердить |
| 5 | Масса тела | < 35 или > 220 кг | подтвердить |
| 6 | Масса тела | не заполнена | ввести −99 и причину |
| 7 | САД | < 80 или > 240 мм рт. ст. | подтвердить |
| 8 | Статус курения | не 0, 1, 2, 9 | запрет ввода |
| 9 | Код терапии | не 1 и не 2 | запрет ввода |
| 10 | Дата визита V0 | раньше даты включения | подтвердить |
| 11 | Дата визита V1 | вне дней 60–120 от V0 | подтвердить |
| 12 | Дата визита V2 | вне дней 150–215 от V0 | подтвердить |
| 13 | Дата визита V3 | вне дней 320–420 от V0 | подтвердить |
| 14 | Гипогликемии | > 10 эпизодов | подтвердить |
| 15 | Гипогликемии | не заполнено | ввести −99 |
| 16 | HbA1c | < 4,0 или > 15,0 (в единицах центра, %) | подтвердить |
| 17 | HbA1c | < 20 или > 140 (в единицах центра, ммоль/моль) | подтвердить |
| 18 | ХС-ЛНП | < 0,5 или > 10 ммоль/л (или 20–390 мг/дл) | подтвердить |
| 19 | Креатинин | < 30 или > 600 мкмоль/л (или 0,3–6,8 мг/дл) | подтвердить |
| 20 | Дата забора | позже даты ввода | запрет ввода |
| 21 | Дата забора | раньше V0 более чем на 60 дней | подтвердить |
| 22 | Повторный результат | тот же тест в тот же день | подтвердить (допускается) |
| 23 | Отзыв согласия | отмечен, но есть визиты после даты отзыва | подтвердить |
| 24 | Дубликат | совпадение ФИО и даты рождения с существующей картой | предупреждение регистратору |

Комментарии группы мониторинга:

* Проверка 24 срабатывала не всегда: при опечатке в ФИО карта-дубликат создавалась без
  предупреждения, поэтому для выявления дубликатов при анализе используется `nid_hash`.
* Проверки 16 и 17 выполнялись по настройке единиц центра в ЭИРК. После перехода ЛИС центра 2
  на ммоль/моль настройка ЭИРК центра 2 была изменена только в апреле 2022 г., поэтому в марте
  2022 г. часть результатов центра 2 вызывала ложные предупреждения. На содержимое выгрузки и на
  правила анализа это не влияет: единицы определяются по дате забора (приложение B).
* Проверка 21 допускает сохранение результатов, полученных за 31–60 дней до V0; в анализе такие
  результаты базовыми не считаются (раздел 3.3.2).
* Диапазон проверки 16 (4,0–15,0 %) шире реального разброса данных и не совпадает с диапазоном
  правдоподобия, используемым при анализе (3,0–20,0 %, раздел 3.3.1).
"""

METHODS_DESIGN += """
## 2.8. Размер выборки

Размер выборки рассчитывался исходя из ожидаемой доли достигших целевого уровня HbA1c через 6
месяцев около 35 % в группе глиптазина и 43 % в группе флозирона (по результатам пилотного
регистра двух центров). Для оценки разности рисков с полушириной 95 % доверительного интервала
не более 5 процентных пунктов при соотношении групп около 1:1 требовалось не менее 750
оценённых пациентов в каждой группе. С учётом ожидаемой доли выбывших и пропусков визита V2
(до 30 %) планировалось включить около 3000 пациентов. Расчёт выполнялся для исходного порога
7,5 % и не пересматривался после поправки 2.

## 2.9. Роли центров и центральной группы

Центры отвечали за включение пациентов, проведение визитов, выполнение анализов в местной
лаборатории и ввод данных в ЭИРК. Центральная группа (группа мониторинга и статистики) отвечала
за мониторинг, ведение журнала запросов, формирование аналитической базы и анализ. Центры не
имели доступа к объединённым данным до закрытия базы.
"""

METHODS_STATS += """
## 4.8. Анализы чувствительности

Анализы чувствительности (без винзоризации; с исключением одного центра; с исходным порогом
7,5 %) выполнены для оценки устойчивости выводов. Их результаты приведены в дополнительных
материалах и **не входят** в таблицы 1–3, блок-схему и рисунок 2 настоящей рукописи.

## 4.9. Обработка пропущенных данных

Пропущенные значения не восстанавливались. Участник без значения показателя не учитывается
при расчёте среднего или доли по этому показателю (за исключением доли гипогликемий, знаменатель
которой — все участники группы, см. поправку 1). Отсутствие базового креатинина не является
основанием для исключения; отсутствие базового HbA1c является (раздел 4.2).
"""

METHODS_STATS += """
## 4.10. Дополнительные показатели

Показатели, добавленные поправкой 9 (рСКФ в таблице 1; изменение HbA1c, комбинированный
исход и ХС-ЛНП на 12 месяцах в таблице 2; отношения рисков в таблице 3), определены в самой
поправке (приложение А) с уточнениями руководящего комитета (приложение D, заседания № 7 и
№ 8). В тексте версии 1.0 этих показателей нет; формулы и примеры расчёта приведены также в
приложениях G и J.
"""

INTRO += """
## 1.4. Обзор предшествующих наблюдательных данных

В европейских регистрах пациентов с СД2, начинающих терапию иНГЛТ-2 или иДПП-4, средний
исходный HbA1c составлял 8,0–8,6 %, а доля достигших уровня менее 7,0 % через полгода —
от четверти до двух пятых пациентов в зависимости от популяции и исходного уровня. В
большинстве работ пациенты, получавшие иНГЛТ-2, были моложе и имели более высокий индекс массы
тела. В отечественных исследованиях, выполненных преимущественно в одном центре, сообщалось о
сходной динамике, однако правила отбора измерений по временным точкам и обработки единиц
измерения, как правило, не описывались, что затрудняет сравнение.

Различия в определении временных окон заслуживают отдельного внимания. Если измерение,
выполненное через 5 месяцев, относят к точке 6 месяцев, а в другой работе такое измерение
исключается, доли достигших цели в двух работах несопоставимы даже при одинаковом пороге. В
настоящем исследовании окна зафиксированы в протоколе и изменены только поправкой для точки 12
месяцев; правило выбора одного измерения из нескольких в окне принято руководящим комитетом до
начала анализа.
"""

DISCUSSION += """
## 6.5. Сопоставление с другими исследованиями

Доли пациентов, достигших HbA1c менее 7,0 % через полгода, в нашем исследовании находятся в
пределах, описанных в крупных европейских регистрах. Направление различий между группами
совпадает с данными метаанализов рандомизированных исследований, хотя величина различий в
наблюдательных данных, как правило, больше из-за различий в популяциях. Стандартизация по
возрасту и полу в нашем исследовании изменила оценку разности рисков лишь на несколько
десятых процентного пункта, что говорит об умеренной роли этих факторов как искажающих.

Частота гипогликемий в группе глиптазина оказалась выше, чем в группе флозирона; следует
учитывать, что регистрация основана на сообщениях пациентов, а определение (любой эпизод в
первые 196 дней) шире, чем определение тяжёлой гипогликемии в регистрах.
"""

REMARKS = [
    "Температурный режим хранения образцов соблюдается, журналы температуры заполнены.",
    "В двух картах отсутствует подпись исследователя на странице визита V1; на данные не влияет, "
    "подписи получены.",
    "Версии информированного согласия соответствуют утверждённым этическим комитетом.",
    "Исследовательская команда центра прошла повторное обучение работе с ЭИРК.",
    "Выявлены задержки ввода данных визитов до трёх недель; центр обязался сократить срок ввода.",
    "Лабораторные бланки хранятся в картах в полном объёме.",
    "Замечаний к ведению журнала скрининга нет.",
    "Сверка листов назначений с ЭИРК выполнена выборочно, расхождений, кроме указанных, нет.",
    "Рекомендовано фиксировать в карте причину переноса визитов V3.",
    "Обсуждены сроки ответа на открытые запросы.",
    "Архив первичной документации организован по номерам участников, поиск карт не затруднён.",
    "Отмечена смена координатора исследования в центре; передача дел оформлена.",
    "Проверены отчёты о нежелательных явлениях: все сообщения переданы в установленные сроки.",
    "В центре обновлён список лиц, имеющих доступ к ЭИРК.",
]


def render_monitoring() -> str:
    W = build_world()
    r = random.Random(f"{TASK_ID}/monitoring")
    by_site_q: dict[tuple[int, int, int], list[int]] = {}
    for e in W["corrections"]:
        if e["kind"].startswith("late_"):
            continue
        site = int(e["pid"][:2])
        by_site_q.setdefault((site, e["logged"].year, (e["logged"].month - 1) // 3 + 1), []).append(e["no"])
    out = ["""# Приложение I. Протоколы мониторинговых визитов (краткие выписки)

Выписки содержат дату визита монитора в центр, объём проверки и ссылки на запросы, оформленные
по результатам визита. Содержание запросов и решения по ним — в журнале (приложение C); сами
протоколы визитов решений по данным не содержат.
"""]
    for site in SITES:
        out.append(f"\n## Центр {site} ({SITES[site][0]})\n")
        for year in (2020, 2021, 2022, 2023):
            for q in (1, 2, 3, 4):
                if (year, q) < (2020, 2) or (year, q) > (2023, 3):
                    continue
                nums = by_site_q.get((site, year, q), [])
                if not nums and r.random() < 0.5:
                    continue
                day = D(year, 3 * q - 2, 1) + dt.timedelta(days=r.randint(5, 80))
                checked = r.randint(25, 90)
                sdv = r.randint(8, min(checked, 30))
                refs = ", ".join(f"№ {n}" for n in nums)
                found = (f"Выявлено расхождений: {len(nums)}; оформлены запросы {refs}." if nums else
                         "Расхождений с первичной документацией не выявлено.")
                out.append(
                    f"**{_d(day)}**, монитор {r.choice(MONITORS)}. Проверено карт: {checked}, из них с полной "
                    f"сверкой первичных данных: {sdv}. {found} {r.choice(REMARKS)}\n\n")
    return "".join(out)


DERIVED = """# Приложение J. Спецификация производных переменных аналитической базы

Спецификация составлена для программиста аналитической базы и повторяет правила разделов 2–4
и приложений А–D в виде перечня переменных. При расхождении приоритет имеют поправки
(приложение А), затем решения комитета (приложение D), затем разделы рукописи.

**J.1. `person_record`** — запись участника после удаления дубликатов: из записей с одинаковым
`nid_hash` остаётся одна, с самой ранней датой включения (после исправлений по журналу).

**J.2. `withdrawn`** — 1, если в выгрузке `consent_withdrawn` = 1 или в журнале запросов до
закрытия базы зарегистрирован отзыв согласия; иначе 0.

**J.3. `v0_date`** — дата визита с кодом `V0` (после исправлений по журналу). Если визита V0 нет,
переменная не определена, и запись исключается на соответствующем шаге.

**J.4. `age_v0`** — число полных лет от даты рождения (после исправлений) до `v0_date`.

**J.5. `age_group`** — `<50`, `50–64`, `≥65` по `age_v0`.

**J.6. `sex`** — код пола после исправлений (`1` — мужской, `2` — женский).

**J.7. `group`** — `А` при коде терапии 1, `Б` при коде 2 (после исправлений).

**J.8. `bmi_v0`** — масса тела на визите V0 (после исправлений; `-99` — отсутствует), делённая на
квадрат роста в метрах (рост в сантиметрах делится на 100; в центре 4 рост уже в метрах).

**J.9. `smoker_current`** — 1 при коде курения 2, 0 при кодах 0 и 1, не определена при коде 9
(после исправлений).

**J.10. `hba1c_pct`** — каждый результат HbA1c в процентах: пересчёт из ммоль/моль по центру и
дате забора (приложение B) или по единицам, указанным в журнале; округление пересчитанного
значения до 0,1; исключение значений вне 3,0–20,0 %; усреднение результатов одного дня.

**J.11. `ldl_mmol`, `creat_umol`** — результаты ХС-ЛНП и креатинина в единых единицах, с
усреднением результатов одного дня.

**J.12. `*_base`** — базовые значения: результат с наиболее поздней датой в днях −30…0 от
`v0_date`.

**J.13. `hba1c_m3`, `hba1c_m6`, `hba1c_m12`** — значения в окнах 91 ± 14, 182 ± 14, 365 ± 30
дней (окно 12 месяцев — по поправке 4); ближайшее к целевому дню, при равенстве — более раннее.

**J.14. `goal_m6`** — 1, если `hba1c_m6` < 7,0 (поправка 2); 0, если `hba1c_m6` ≥ 7,0; не
определена, если `hba1c_m6` отсутствует.

**J.15. `hypo_any`** — 1, если есть визит (любого кода, кроме V0) с днём наблюдения 1–196 и
`hypo_events` ≥ 1 (после исправлений); иначе 0. Значение −99 эпизодом не считается.

**J.16. `excl_step`** — номер первого шага исключения по порядку раздела 4.2 с учётом поправки 3
(1 — дубликат; 2 — отзыв согласия; 3 — центр 5 до 01.09.2021; 4 — нет V0; 5 — возраст; 6 — нет
базового HbA1c; 7 — креатинин > 200 мкмоль/л) или пусто для аналитической выборки.

**J.17. `egfr_base`** — рСКФ по CKD-EPI 2021 (поправка 9): `creat_umol_base / 88.4` в мг/дл,
коэффициенты κ и α по полу (`sex` после исправлений), возраст — `age_v0`; множитель 1,012 для
женщин. Не определена, если нет `creat_umol_base`.

**J.18. `hba1c_change_m6`** — `hba1c_m6 − hba1c_base` (отрицательное — снижение); определена только
при наличии `hba1c_m6`.

**J.19. `composite_m6`** — 1, если `goal_m6` = 1 и `hypo_any` = 0 (дни 1–196, заседание № 8);
0, если `goal_m6` = 0 или `hypo_any` = 1; не определена, если не определена `goal_m6`.

**J.20. `ldl_m12`** — ХС-ЛНП (ммоль/л) в окне 365 ± 30 дней по дате забора; ближайшее к 365-му
дню, при равенстве — более раннее; повторные результаты дня усредняются. Не винзоризуется.

**J.21. Отношения рисков** — по `goal_m6` среди оценённых на 6 месяцах, А относительно Б;
стратификация — `age_group` × `sex` (шесть страт), формула RR_MH — поправка 9.
"""


def render_manifest() -> str:
    ex = export_files()
    lines = ["# Манифест выгрузки", "",
             "Перечень файлов, переданных центрами, с числом строк данных (без строки заголовка). Числа",
             "относятся к выгрузке как она есть, до обработки. Контрольная сверка числа строк выполнялась",
             "при приёме файлов от центров.", ""]
    for site in SITES:
        lines += [f"## Центр {site} ({SITES[site][0]})", "", "| Файл | Содержимое | Строк |", "|---|---|---|"]
        for rel in sorted(k for k in ex if k.startswith(f"data/site{site}/")):
            name = rel.split("/")[-1]
            if name == "participants.csv":
                what = "участники"
            elif name.startswith("visits_"):
                what = f"визиты, {name[7:11]} г., {'I' if name[11:13] == 'H1' else 'II'} полугодие"
            else:
                what = f"лабораторные результаты, {name[4:8]} г., {name[9]}-й квартал"
            lines.append(f"| `{rel}` | {what} | {len(ex[rel][1])} |")
        lines.append("")
    return "\n".join(lines) + "\n"


ANALYST_QA = """# Приложение K. Вопросы второго аналитика и ответы статистика

Переписка второго аналитика (независимая проверка расчётов) со статистиком исследования в
период подготовки таблиц. Ответы статистика опираются на разделы 2–4, поправки (приложение А)
и решения комитета (приложение D); самостоятельных правил в них нет. Вопросы приведены в том
порядке, в каком поступали; часть вопросов основана на неверном понимании — ответы это
поясняют.

---

**Вопрос 1.** В каком порядке применять исправления из журнала и удаление дубликатов? Если у
записи-дубликата исправлена дата включения, какая запись остаётся?

**Ответ.** Исправления журнала — часть подготовки данных (раздел 4.1, п. 3), они применяются
до формирования аналитической выборки. Дубликаты определяются по `nid_hash`, а остаётся запись
с наиболее ранней **исправленной** датой включения (приложение J, J.1).

**Вопрос 2.** В журнале есть отзыв согласия, а ниже у той же записи — дополнение, что отзыв
оформлен ошибочно. Исключать участника?

**Ответ.** Действует последнее решение по запросу, зарегистрированное до закрытия базы. Если
дополнение об отмене зарегистрировано до 16.10.2023 включительно — участник остаётся в
анализе (если его не исключают другие шаги). Если дополнение пришло после закрытия базы, оно не
применяется, и в силе остаётся отзыв согласия.

**Вопрос 3.** То же для даты рождения: исправление внесено в 2022 году, а в ноябре 2023 года
центр прислал «ещё одну поправку». Какую дату брать?

**Ответ.** Исправление 2022 года. Дополнение после закрытия базы не применяется (приложение C,
правила; приложение D, заседание № 9, п. 6).

**Вопрос 4.** Для рСКФ я взял возраст на дату включения — так делается в регистре, откуда
заимствована формула. Это допустимо?

**Ответ.** Нет. Все показатели исследования, включая рСКФ, используют возраст в полных годах
на дату визита V0 (раздел 3.2; приложение D, заседание № 7).

**Вопрос 5.** В формулу CKD-EPI я подставляю креатинин из аналитической базы, то есть в
мкмоль/л. Получаются значения около 0,2–0,4. Что не так?

**Ответ.** Формула требует креатинин в мг/дл. Значение в мкмоль/л нужно **разделить** на 88,4
(поправка 9, п. 1; пример G.8). Умножать на 88,4 нужно при обратном переходе — из мг/дл в
мкмоль/л при гармонизации выгрузок центров 5 и 6 (раздел 3.3.1).

**Вопрос 6.** У центров 5 и 6 креатинин и так в мг/дл. Можно подставлять его в формулу
напрямую, без перевода в мкмоль/л и обратно?

**Ответ.** Можно — результат тот же, поскольку пересчёт креатинина выполняется без округления.
Но базовое значение определяется по правилам раздела 3.3.2 (последний результат в днях −30…0,
среднее повторных результатов дня), и исключения из журнала запросов (например, результат в
мг/дл, внесённый в центре, где обычно мкмоль/л) нужно учесть до расчёта.

**Вопрос 7.** В журнале несколько записей о креатинине, внесённом «в мг/дл» в центрах 1–4.
Значения вроде 1,05 без исправления проходят как 1,05 мкмоль/л. Их пересчитывать?

**Ответ.** Да: по журналу единицы таких результатов — мг/дл, их нужно умножить на 88,4, как
результаты центров 5 и 6. После пересчёта значение сравнивается с порогом исключения по
креатинину (поправка 7), и оно же используется для рСКФ.

**Вопрос 8.** Для комбинированного исхода знаменатель — все участники группы, как у доли
гипогликемий?

**Ответ.** Нет. Знаменатель комбинированного исхода — оценённые на 6 месяцах (поправка 9,
п. 3). У доли гипогликемий (поправка 1) знаменатель другой — все участники аналитической
выборки группы.

**Вопрос 9.** В поправке 9 для комбинированного исхода написано «дни 1–182». Я так и считаю.

**Ответ.** Эта редакция изменена на заседании № 8: используются дни 1–196, как в поправке 1.
Эпизоды на базовом визите V0 не учитываются, значение −99 эпизодом не считается.

**Вопрос 10.** Изменение HbA1c считаю среди всех участников с базовым HbA1c, подставляя
пропущенное значение на 6 месяцах последним доступным. Верно?

**Ответ.** Нет. Изменение считается только среди оценённых на 6 месяцах, без подстановки
значений (раздел 4.9; приложение D, заседание № 6, п. 2). Знак — «6 месяцев минус базовое»
(заседание № 8, п. 3), а не «снижение», как в анализе чувствительности F.9.3.

**Вопрос 11.** ХС-ЛНП на 12 месяцах беру с визитов V3. У части пациентов V3 нет, но есть
внеплановый визит на 350-й день с анализом ХС-ЛНП. Его учитывать?

**Ответ.** Да. Отнесение — по дате забора, в окне 365 ± 30 дней (дни 335–395), код визита
значения не имеет (заседание № 7, п. 1). Из нескольких результатов в окне берётся ближайший к
365-му дню, при равном удалении — более ранний.

**Вопрос 12.** В центре 3 ХС-ЛНП в мг/дл. Перевожу в ммоль/л и округляю до двух знаков, как в
выгрузках других центров. Так можно?

**Ответ.** Нет. Пересчёт ХС-ЛНП выполняется делением на 38,67 **без округления** (раздел
3.3.1). Округляются только пересчитанные значения HbA1c.

**Вопрос 13.** Нужно ли винзоризовать ХС-ЛНП на 12 месяцах, как базовый ХС-ЛНП?

**Ответ.** Нет. Винзоризация раздела 4.3 касается только ИМТ и базового ХС-ЛНП; предложение
винзоризовать ХС-ЛНП на 12 месяцах (поправка 10) отклонено.

**Вопрос 14.** В журнале есть значения ХС-ЛНП вроде 285 или 312 с пометкой о потерянной
запятой. Если их не исправлять, среднее на 12 месяцах заметно завышено.

**Ответ.** Такие записи — исправления, зарегистрированные до закрытия базы; их нужно
применить (285 → 2,85 и т. д.). Проверки правдоподобия для ХС-ЛНП нет, поэтому без
исправления ошибочные значения попадают в среднее.

**Вопрос 15.** Отношение рисков я считаю по долям из таблицы 2, уже округлённым до одного знака.

**Ответ.** Нет, по неокруглённым долям (заседание № 8, п. 2; раздел 4.6: промежуточные
вычисления не округляются).

**Вопрос 16.** Отношение рисков — «Б относительно А», как в анализе чувствительности F.9.4?

**Ответ.** Нет. В таблице 3 все сравнения — «А относительно Б» (раздел 4.5; заседание № 8,
п. 2). F.9.4 — анализ чувствительности исходного ПСА, в таблицы он не входит.

**Вопрос 17.** Какие страты для RR по Мантелю–Хензелю?

**Ответ.** Те же шесть страт, что для разности рисков и отношения шансов: возрастная группа
(менее 50; 50–64; 65 и старше — по возрасту на V0) × пол после исправлений.

**Вопрос 18.** В журнале код внепланового визита исправлен на V2. Нужно ли теперь относить
анализ этого визита к точке 6 месяцев, даже если дата вне окна?

**Ответ.** Нет. Отнесение к точкам выполняется по дате забора (раздел 3.3.3), код визита на
него не влияет. Для гипогликемий учитываются все визиты, кроме V0, с днём наблюдения 1–196 —
код тоже не важен. Такие исправления на расчёты не влияют.

**Вопрос 19.** Масса тела на V0 в выгрузке −99, а в журнале указано значение. ИМТ для такого
участника считать?

**Ответ.** Да. Запись журнала до закрытия базы — исправление данных; после неё масса тела
известна, и участник входит в расчёт среднего ИМТ (и в винзоризацию).

**Вопрос 20.** ЛИС центра 2 выдаёт рСКФ по формуле CKD-EPI 2009 года. Может быть, взять её?

**Ответ.** Нет. В выгрузке рСКФ нет, и в исследовании используется формула 2021 года без
коэффициента расы (поправка 9). Значения из ЛИС в анализ не входят.

**Вопрос 21.** Округлять ли рСКФ каждого участника перед расчётом среднего?

**Ответ.** Нет. Округляется только итоговое среднее — до двух знаков.

**Вопрос 22.** Участник без базового креатинина: рСКФ считать нулевой?

**Ответ.** Нет, рСКФ у него не определена, в среднем он не учитывается (поправка 9, п. 1).
Из анализа такой участник по критерию креатинина не исключается (раздел 4.2).

**Вопрос 23.** Нужно ли в таблицах приводить анализы чувствительности F.9.1–F.9.4?

**Ответ.** Нет, они не входят в таблицы 1–3, блок-схему и рисунок 2 (раздел 4.8).

**Вопрос 24.** Повторные строки в файлах визитов — учитывать гипогликемию дважды?

**Ответ.** Нет: полностью повторяющаяся строка — артефакт выгрузки и описывает один визит. Для
доли гипогликемий и комбинированного исхода это и так не важно — важно, был ли хотя бы один
эпизод.
"""


# ==========================================================================
# Appendix L: correspondence with sites (context only; never changes the data)
# ==========================================================================

COORDINATORS = {1: "А. Р. Хайруллина", 2: "С. В. Мезенцев", 3: "Н. Г. Воронова", 4: "И. П. Кулагин",
                5: "Т. Ю. Смирнова", 6: "В. А. Штейн"}


@functools.cache
def site_letters() -> list[dict]:
    """Claims sent by site coordinators by e-mail that never reached the query log."""
    W = build_world()
    r = random.Random(f"{TASK_ID}/letters")
    touched = {e["pid"] for e in W["corrections"]}
    v0 = {v.pid: v for v in W["visits"] if v.code == "V0"}
    pool = [p for p in W["people"] if p.pid in v0 and not p.withdrawn and p.pid not in touched]
    r.shuffle(pool)
    out: list[dict] = []
    near = [p for p in pool if full_years(p.birth, v0[p.pid].date) in (18, 19, 78, 79)]
    for p in near[:3]:
        a = full_years(p.birth, v0[p.pid].date)
        out.append({"kind": "birth", "pid": p.pid, "new": _add_years(p.birth, 2 if a <= 19 else -2)})
    rest = [p for p in pool if p not in near[:3]]
    for p in rest[:5]:
        out.append({"kind": "consent", "pid": p.pid,
                    "day": min(D(2023, 6, 30), v0[p.pid].date + dt.timedelta(days=r.randint(40, 250)))})
    for p in rest[5:9]:
        out.append({"kind": "sex", "pid": p.pid, "new": 3 - p.sex})
    for p in rest[9:13]:
        v = v0[p.pid]
        if v.weight != -99:
            out.append({"kind": "weight", "pid": p.pid, "day": v.date, "new": round(v.weight + r.choice([-9, 8, 12]), 1)})
    for e in out:
        if e["kind"] == "consent":
            e["sent"] = e["day"] + dt.timedelta(days=r.randint(2, 30))
        else:
            e["sent"] = max(D(2022, 1, 10) + dt.timedelta(days=r.randint(0, 690)),
                            v0[e["pid"]].date + dt.timedelta(days=r.randint(10, 60)))
    out.sort(key=lambda e: (e["sent"], e["pid"]))
    return out


LETTER_INFO = [  # (site, text, reply, date)
    (2, "Сообщаем, что с 01.03.2022 лаборатория перешла на новую ЛИС; HbA1c теперь выдаётся в ммоль/моль. "
        "Архивные результаты не пересчитывались. Просим учесть при анализе.",
     "Спасибо, это отражено в приложении B; единицы определяются по дате забора.", D(2022, 3, 14)),
    (4, "Уточните, пожалуйста, нужно ли нам переводить рост в сантиметры перед выгрузкой.",
     "Нет, решение комитета (заседание № 1): выгрузка как есть, пересчёт при анализе.", D(2020, 9, 22)),
    (1, "В декабре 2021 года ЛИС две недели работала в тестовом режиме с HbA1c в ммоль/моль. Эти результаты "
        "в выгрузку не попали?",
     "Проверили: не попали, в выгрузке центра 1 все результаты HbA1c в процентах.", D(2022, 1, 19)),
    (6, "Можно ли прислать дополнительно рСКФ из нашей ЛИС (CKD-EPI 2009)? Это сэкономит вам время.",
     "Не нужно: рСКФ рассчитывается централизованно по формуле 2021 года (поправка 9).", D(2023, 5, 4)),
    (3, "Коллеги, в 2023 году мы переходим на ммоль/л для ХС-ЛНП. Нужно ли пересчитать архив?",
     "Нет. По данным центра переход в период исследования не состоялся; в выгрузке ХС-ЛНП в мг/дл.",
     D(2023, 2, 7)),
    (5, "Напоминаем, что даты в наших файлах в формате ДД.ММ.ГГГГ, креатинин — в мг/дл.",
     "Спасибо, это учтено (кодовая книга, приложение B).", D(2020, 11, 12)),
]


def render_letters() -> str:
    r = random.Random(f"{TASK_ID}/letters-text")
    out = ["""# Приложение L. Переписка с центрами по вопросам данных (выдержки)

Выдержки из электронной переписки координаторов центров с центральной группой. Переписка
приводится для контекста. **Изменения данных вносятся только через журнал запросов
(приложение C)**: сведения из писем, по которым в журнале нет записи, в анализе не
используются, даже если центр настаивает на исправлении (раздел 2.6). Если по письму был
оформлен запрос, он есть в журнале.

---
"""]
    items: list[tuple[dt.date, str]] = []
    for e in site_letters():
        site = int(e["pid"][:2])
        who = COORDINATORS[site]
        if e["kind"] == "birth":
            text = r.choice([
                f"По нашим данным, дата рождения участника `{e['pid']}` — {_d(e['new'])}, в выгрузке ошибка. "
                "Просим исправить в аналитической базе.",
                f"Участник `{e['pid']}` при повторном визите предъявил паспорт: дата рождения {_d(e['new'])}. "
                "Поправьте, пожалуйста, у себя.",
            ])
        elif e["kind"] == "consent":
            text = r.choice([
                f"Участник `{e['pid']}` по телефону сообщил {_d(e['day'])}, что больше не хочет участвовать. "
                "Письменного заявления пока нет. Прошу исключить его из анализа.",
                f"Сообщаем, что `{e['pid']}` ({_d(e['day'])}) устно отказался от дальнейших визитов. Считайте, "
                "пожалуйста, согласие отозванным.",
            ])
        elif e["kind"] == "sex":
            new = "женский" if e["new"] == 2 else "мужской"
            text = (f"Кажется, у участника `{e['pid']}` перепутан пол — должен быть {new}. Проверим карту, "
                    "но просим поправить уже сейчас.")
        else:
            text = (f"Масса тела `{e['pid']}` на визите V0 ({_d(e['day'])}) — {_n(e['new'])} кг, в ЭИРК "
                    "внесено другое значение. Исправьте, пожалуйста.")
        reply = r.choice([
            "Исправления вносятся только через запрос с приложением первичной документации. Пока запрос не "
            "зарегистрирован в журнале, данные не меняются.",
            "Спасибо. Направьте, пожалуйста, подтверждающий документ через форму запроса; до регистрации в "
            "журнале изменения в базу не вносятся.",
        ])
        items.append((e["sent"], f"**{_d(e['sent'])}. Центр {site}, {who}.** {text}\n\n*Ответ центральной "
                                 f"группы:* {reply}\n"))
    for site, text, reply, day in LETTER_INFO:
        items.append((day, f"**{_d(day)}. Центр {site}, {COORDINATORS[site]}.** {text}\n\n*Ответ центральной "
                           f"группы:* {reply}\n"))
    items.sort(key=lambda x: x[0])
    out += [t for _, t in items]
    return "\n".join(out)


CODEBOOK_README += """
## Порядок подготовки данных (краткая памятка)

Памятка повторяет разделы 2–4 рукописи и приложения к ней; при расхождении приоритет у
рукописи с поправками (приложение А) и решений комитета (приложение D).

1. Прочитать файлы всех шести центров; привести даты к единому виду (центры 3 и 5 —
   `ДД.ММ.ГГГГ`) и коды тестов к стандартным (центр 4: `A1C`, `LDLC`, `CREA`).
2. Удалить полностью повторяющиеся строки визитов (артефакт выгрузки).
3. Применить исправления из журнала запросов (приложение C, обе части) по правилам журнала:
   действует последнее решение по запросу, зарегистрированное до закрытия базы 16.10.2023
   включительно; записи и дополнения после этой даты не применяются. Письма центров
   (приложение L) исправлениями не являются.
4. Пересчитать лабораторные результаты в единые единицы по центру и дате забора (приложение B)
   с учётом единиц, указанных в журнале для отдельных результатов.
5. Сформировать аналитическую выборку (раздел 4.2 с поправкой 3), затем рассчитать показатели.

Файлы не содержат персональных данных; идентификатор `participant_id` уникален в пределах всей
выгрузки, а `nid_hash` связывает повторные регистрации одного человека.
"""

CODEBOOK_PART += """
## Примеры строк (условные участники)

```
participant_id,site,enrol_date,birth_date,sex,height,smoking,therapy,consent_withdrawn,nid_hash
01-0412,1,2021-04-19,1958-11-02,2,164,0,2,0,3f9c01aa7e
03-0077,3,02.09.2020,17.05.1949,1,178,1,1,0,a0b1c2d3e4
04-0231,4,2021-12-06,1966-03-30,1,1.81,2,1,0,77e1f0c2d9
```

Во второй строке даты записаны в формате центра 3, в третьей рост указан в метрах (центр 4).

## Замечания

* `sex`, `smoking`, `therapy` и `birth_date` могли быть исправлены по журналу запросов; в
  выгрузке остаются исходные значения.
* Отметка `consent_withdrawn` = 1 — единственный признак отзыва согласия в самой выгрузке;
  дополнительные отзывы (и отмены ошибочно оформленных отзывов) — только в журнале.
* Поле `height` в центрах 1–3, 5, 6 — целое число сантиметров; явные ошибки ввода (например,
  рост 17 или 205 у пациентки ростом 165 см) исправлены по журналу, остальные значения не
  проверяются.
"""

CODEBOOK_VIS += """
## Примеры строк (условные визиты)

```
visit_id,participant_id,visit_code,visit_date,weight_kg,sbp_mmhg,hypo_events
V004211,02-0198,V0,2021-06-14,88.4,138,0
V004212,02-0198,V0,2021-06-14,88.4,138,0
V005890,05-0310,U,11.01.2022,-99,126,-99
```

Первые две строки — полностью повторяющиеся (визит учитывается один раз). В третьей масса тела
не измерялась, а информация о гипогликемиях не собрана.

## Замечания

* Код визита (`V1`, `V2`, `V3`, `U`) для отнесения лабораторных результатов к временным точкам
  не используется; исправления кода визита в журнале на расчёты не влияют. Единственный код,
  который имеет значение, — `V0`: его дата задаёт день 0.
* Масса тела `-99` может быть заменена значением из журнала запросов, если там есть такая запись.
* Поле `sbp_mmhg` в анализ рукописи не входит.
"""

CODEBOOK_LAB += """
## Примеры строк (условные результаты)

```
sample_id,participant_id,sample_date,test_code,result
S0012034,02-0198,2022-02-25,HBA1C,7.7
S0012987,02-0198,2022-03-05,HBA1C,61
S0020412,04-0231,2021-11-30,A1C,8.2
S0020413,04-0231,2021-11-30,CREA,97
S0031007,03-0077,28.08.2020,LDL,131
S0031008,05-0310,03.12.2021,CREAT,1.12
```

Первые два результата — один участник центра 2 до и после перехода ЛИС (проценты и
ммоль/моль). Результат `LDL` центра 3 — в мг/дл (131 мг/дл = 3,39 ммоль/л), креатинин центра 5 —
в мг/дл (1,12 мг/дл = 99,0 мкмоль/л).

## Как не ошибиться с единицами

* Единицы определяются по центру и **дате забора**, а не по величине числа. Значение HbA1c 7,0 в
  центре 6 не может быть процентами: результаты центра 6 — всегда ммоль/моль, и такое значение
  после пересчёта неправдоподобно (исключается), если журнал не указывает иное.
* Отдельные результаты, внесённые вручную по бланкам сторонних лабораторий, имеют другие
  единицы; такие случаи перечислены в журнале запросов с указанием фактических единиц.
* Для креатинина граница между мг/дл и мкмоль/л видна по порядку величины, но решающим
  является центр и журнал: результат 1,05 в центре 2 без записи в журнале остаётся результатом
  в мкмоль/л (и, как правило, оказывается ошибкой, не влияющей на исключение).
"""

ANALYST_QA += """
**Вопрос 25.** Для рСКФ у участника с двумя результатами креатинина в один базовый день — какой
брать?

**Ответ.** Среднее результатов этого дня после приведения к мкмоль/л (раздел 3.3.1), затем
перевод среднего в мг/дл для формулы.

**Вопрос 26.** Участник центра 5 включён 25.08.2021, но в журнале дата включения исправлена на
03.09.2021. Он исключается по поправке 3?

**Ответ.** Нет: поправка 3 применяется к дате включения с учётом исправлений из журнала. С
исправленной датой участник включён после 01.09.2021 и по этому шагу не исключается.

**Вопрос 27.** Шаг «центр 5» — до или после шага «нет визита V0»?

**Ответ.** До: поправка 3 ставит его сразу после отзыва согласия (третьим шагом). Порядок шагов
влияет на числа блок-схемы, поэтому его нужно соблюдать точно.

**Вопрос 28.** В письме центра 3 (приложение L) сказано, что участник устно отказался от участия.
В журнале записи нет. Исключать по отзыву согласия?

**Ответ.** Нет. Письма центров данными не являются (раздел 2.6); без записи в журнале отзыв
согласия не учитывается.

**Вопрос 29.** Комбинированный исход: участник достиг цели, а гипогликемия отмечена только на
базовом визите V0. Исход достигнут?

**Ответ.** Да. Эпизоды на V0 (день 0) не учитываются — только визиты с днём наблюдения 1–196.

**Вопрос 30.** Изменение HbA1c: базовое значение — то, что входит в рисунок 2 (среднее базовых
значений), или индивидуальное?

**Ответ.** Индивидуальное: для каждого оценённого на 6 месяцах участника вычисляется разность
его значения на точке 6 месяцев и его базового значения, затем берётся среднее этих разностей по
группе. (Разность средних по рисунку 2 — другая величина: в ней знаменатели точек различаются.)

**Вопрос 31.** Нужно ли исключать из расчёта ХС-ЛНП на 12 месяцах значения, выходящие за
диапазон проверки ввода ЭИРК (приложение H, проверка 18)?

**Ответ.** Нет. Проверки ввода — не правила анализа (приложение H). Для ХС-ЛНП проверки
правдоподобия при анализе нет; ошибочные значения исправляются только по журналу.

**Вопрос 32.** В отношении шансов и отношении рисков по Мантелю–Хензелю страты без участников
одной из групп — как учитывать?

**Ответ.** Страта, в которой нет оценённых одной из групп, вклада в суммы не даёт (n1_k·n0_k = 0).
В наших данных все шесть страт содержат участников обеих групп.
"""

DISCUSSION += """
## 6.6. Дополнительные показатели

Показатели, добавленные поправкой 9, описывают функцию почек, динамику HbA1c и липидный
профиль групп. Средняя рСКФ в группах различалась мало, что согласуется с исключением
пациентов с выраженным снижением функции почек по критерию креатинина (поправка 7). Разность
средних изменений HbA1c к 6 месяцам следует интерпретировать с осторожностью: она оценивается
только по пациентам с измерением на точке 6 месяцев. Комбинированный исход (целевой HbA1c без
гипогликемий в первые 196 дней) объединяет эффективность и безопасность; его определение было
уточнено руководящим комитетом до закрытия базы, чтобы интервал учёта гипогликемий совпадал с
вторичным исходом поправки 1. Отношения рисков дополняют разности рисков и отношение шансов:
при долях достижения цели 25–45 % отношение шансов заметно дальше от единицы, чем отношение
рисков, и эти величины не следует смешивать.
"""

EXAMPLES += """
## G.11. Применение журнала запросов: дополнения и сроки

Условные записи журнала (номера и участники вымышлены):

* Запрос № 901 (10.03.2022): дата рождения исправлена с 02.05.1985 на 02.05.1958. Дополнение от
  20.11.2023: «центр сообщает дату рождения 02.05.1956». Дополнение зарегистрировано после
  закрытия базы и не применяется; действует 02.05.1958.
* Запрос № 902 (14.06.2021): отзыв согласия. Дополнение от 30.07.2021: «отзыв оформлен ошибочно,
  участник продолжает участие». Дополнение до закрытия базы — действует: участник по отзыву
  согласия **не исключается**.
* Запрос № 903 (05.09.2023): отзыв согласия. Дополнение от 02.11.2023 об ошибочном отзыве — после
  закрытия базы, не применяется: участник **исключается** на шаге «отзыв согласия».
* Запрос № 904 (18.01.2023): «код визита исправлен с `U` на `V2`» — на расчёты не влияет
  (отнесение по дате забора).
* Запрос № 905 (27.10.2023): «центр прислал уточнённый пол участника» — запись после закрытия
  базы, не применяется.
* Письмо координатора центра (приложение L) от 12.05.2023 о другой дате рождения участника,
  без записи в журнале, — не применяется.

## G.12. Креатинин в мг/дл в центре, выдающем мкмоль/л

Условный участник центра 2: базовый креатинин в выгрузке — 2,41. По журналу запросов результат
внесён по бланку сторонней лаборатории в мг/дл. После пересчёта 2,41 × 88,4 = 213,044 мкмоль/л —
выше порога 200 мкмоль/л (поправка 7), участник исключается на шаге «креатинин». Без учёта
журнала значение 2,41 было бы принято за мкмоль/л, и участник ошибочно остался бы в анализе
(а его рСКФ оказалась бы бессмысленно высокой).
"""
