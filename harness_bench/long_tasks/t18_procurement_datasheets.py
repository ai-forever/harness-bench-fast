"""Long task 18: procurement by equipment datasheets.

World model first: ~113 equipment models of 11 brands (pumps, motors, pressure sensors) with
attributes stored in the units their manufacturer prints; canonical SI values
are derived by the unit rules of ``rules.md``.  Datasheets (with errata and
superseded revisions), six supplier price lists (currencies, VAT, discounts,
delivery, lead times, corrections), a blacklist memo, dated exchange rates and
45 free-text engineering requests are rendered from the model.  Requests are
chosen from many random candidates so that the cheapest compliant offer is
unique with a margin and every naive reading (ignoring errata, older
revisions, blacklist, VAT, deadlines, request updates, dated rates, л.с. read as hp, …) changes
the answer for several requests.
"""

from __future__ import annotations

import datetime as _dt
import functools
import math
import random
import re
from pathlib import Path

from .common import long_task, norm, read_csv, require_share, rng, write, write_csv

TASK_ID = "long_18_procurement_datasheets"
N_REQ = 45
MIN_OK = 42
D = _dt.date

# --------------------------------------------------------------------------
# Units (the same factors are written in rules.md)
# --------------------------------------------------------------------------

GPM = 0.2271  # m3/h per US gpm
LPM = 0.06  # m3/h per l/min
FT = 0.3048  # m per ft
HP = 0.7457  # kW per hp
PSI = 0.068948  # bar per psi
LPS = 3.6  # m3/h per l/s
PS = 0.7355  # kW per metric horsepower (л.с.)
KGF = 0.980665  # bar per kgf/cm2
POWER_TO_KW = {"kW": 1.0, "hp": HP, "ps": PS}


def f2c(f: float) -> float:
    return (f - 32) * 5 / 9


def c2f(c: float) -> float:
    return c * 9 / 5 + 32


def fmt_num(x: float, comma: bool, nd: int = 2) -> str:
    text = f"{x:.{nd}f}".rstrip("0").rstrip(".") if nd else f"{x:.0f}"
    if text in ("-0", ""):
        text = "0"
    return text.replace(".", ",") if comma else text


def fmt_rub(x: float) -> str:
    whole, frac = f"{x:,.2f}".split(".")
    return whole.replace(",", " ") + "," + frac


# --------------------------------------------------------------------------
# Brands (layouts and units) and suppliers
# --------------------------------------------------------------------------

BRANDS = {
    "GM": {"name": "Гидромаш", "cat": "pump", "flow": "m3h", "head": "m", "temp": "C", "layout": "prose", "comma": True,
           "prefix": "ГМ ЦН", "country": "Россия"},
    "NPS": {"name": "НасосПромСервис", "cat": "pump", "flow": "lpm", "head": "m", "temp": "C", "layout": "table",
            "comma": True, "prefix": "НПС-К", "country": "Россия"},
    "AQV": {"name": "AquaVolt", "cat": "pump", "flow": "gpm", "head": "ft", "temp": "F", "layout": "kv", "comma": False,
            "prefix": "AquaVolt CX", "country": "США"},
    "EPU": {"name": "Электропривод-Урал", "cat": "motor", "power": "kW", "temp": "C", "layout": "table", "comma": True,
            "prefix": "АИР-У", "country": "Россия"},
    "STD": {"name": "Stellar Drives", "cat": "motor", "power": "hp", "temp": "F", "layout": "kv", "comma": False,
            "prefix": "Stellar SD", "country": "Италия"},
    "MTR": {"name": "Метрика", "cat": "sensor", "press": "MPa", "temp": "C", "layout": "table", "comma": True,
            "prefix": "Метрика ДД", "country": "Россия"},
    "DTK": {"name": "Датком", "cat": "sensor", "press": "bar", "temp": "C", "layout": "prose", "comma": True,
            "prefix": "Датком ПД", "country": "Россия"},
    "PTC": {"name": "PressTech", "cat": "sensor", "press": "psi", "temp": "F", "layout": "kv", "comma": False,
            "prefix": "PressTech PT", "country": "Германия"},
    "VKT": {"name": "Вектор-Гидро", "cat": "pump", "flow": "lps", "head": "m", "temp": "C", "layout": "bullets",
            "comma": True, "prefix": "Вектор ВЦ", "country": "Беларусь"},
    "MSA": {"name": "Моторостроитель", "cat": "motor", "power": "ps", "temp": "C", "layout": "bullets", "comma": True,
            "prefix": "МС-АД", "country": "Россия"},
    "MNT": {"name": "Манотех", "cat": "sensor", "press": "kgf", "temp": "C", "layout": "bullets", "comma": True,
            "prefix": "Манотех МТ", "country": "Россия"},
}
CAT_NAME = {"pump": "насосы", "motor": "электродвигатели", "sensor": "датчики давления"}

SUPPLIERS = {
    "S1": {"name": "ТехноСнаб", "legal": "ООО «ТехноСнаб»", "cur": "RUB", "vat": True, "cats": ("pump", "motor", "sensor"),
           "tiers": [(5, 3.0), (10, 6.0)], "delivery": ("fixed", 4500.0), "fmt": "csv"},
    "S2": {"name": "ГидроКомплект", "legal": "АО «ГидроКомплект»", "cur": "RUB", "vat": False, "cats": ("pump", "motor"),
           "tiers": [(3, 2.0), (8, 5.0)], "delivery": ("pct", 1.5), "fmt": "letter"},
    "S3": {"name": "ЕвроТехИмпорт", "legal": "ООО «ЕвроТехИмпорт»", "cur": "EUR", "vat": False, "cats": ("pump", "motor"),
           "tiers": [(4, 4.0)], "delivery": ("fixed", 180.0), "fmt": "table"},
    "S4": {"name": "Азия-Индастри", "legal": "ООО «Азия-Индастри»", "cur": "CNY", "vat": True,
           "cats": ("pump", "motor", "sensor"), "tiers": [(6, 5.0), (12, 8.0)], "delivery": ("unit", 450.0), "fmt": "kv"},
    "S5": {"name": "ПромДатчик", "legal": "ООО «ПромДатчик»", "cur": "RUB", "vat": True, "cats": ("sensor",),
           "tiers": [(10, 5.0)], "delivery": ("free_over", 2000.0, 100000.0), "fmt": "csv"},
    "S6": {"name": "Интерсервис", "legal": "ООО «Интерсервис»", "cur": "USD", "vat": False, "cats": ("motor", "sensor"),
           "tiers": [(5, 3.0), (10, 7.0)], "delivery": ("pct", 3.0), "fmt": "text"},
}

# weekly rates, RUB per unit; the rate for a request is the latest one dated not after the request
RATE_DATES = [D(2026, 9, 28) + _dt.timedelta(days=7 * i) for i in range(12)]


def _make_rates(r: random.Random) -> dict[str, list[tuple[_dt.date, float]]]:
    base = {"USD": 92.4, "EUR": 100.8, "CNY": 12.7}
    out = {}
    for cur, v in base.items():
        seq = []
        for d in RATE_DATES:
            v = round(v * (1 + r.uniform(-0.025, 0.028)), 4 if cur == "CNY" else 2)
            seq.append((d, v))
        out[cur] = seq
    return out


def rate_on(rates: dict, cur: str, day: _dt.date, mode: str = "") -> float:
    if cur == "RUB":
        return 1.0
    seq = rates[cur]
    if mode == "latest_rate":
        return seq[-1][1]
    best = None
    for d, v in seq:
        if d <= day:
            best = v
    assert best is not None
    return best


# blacklist: (supplier, category, from, until-exclusive or None)
BLACKLIST = [("S4", "pump", D(2026, 11, 2), None), ("S6", "sensor", D(2026, 10, 19), D(2026, 11, 23))]


def banned(sup: str, cat: str, day: _dt.date) -> bool:
    return any(s == sup and c == cat and day >= a and (b is None or day < b) for s, c, a, b in BLACKLIST)


VOLTS = {
    "3x380_10": (3, 342.0, 418.0), "3x400_10": (3, 360.0, 440.0), "3x415_5": (3, 394.25, 435.75),
    "3x380_415": (3, 380.0, 415.0), "3x660": (3, 627.0, 693.0), "1x230_10": (1, 207.0, 253.0),
    "1x220_240": (1, 220.0, 240.0),
}
VOLT_TEXT = {
    "3x380_10": ["3~ 380 В ±10 %", "трёхфазная сеть 380 В, допуск ±10 %", "3 phase 380 V ±10%"],
    "3x400_10": ["3~ 400 В ±10 %", "трёхфазная сеть 400 В, допуск ±10 %", "3 phase 400 V ±10%"],
    "3x415_5": ["3~ 415 В ±5 %", "трёхфазная сеть 415 В, допуск ±5 %", "3 phase 415 V ±5%"],
    "3x380_415": ["3~ 380–415 В", "трёхфазная сеть от 380 до 415 В", "3 phase 380-415 V"],
    "3x660": ["3~ 660 В ±5 %", "трёхфазная сеть 660 В, допуск ±5 %", "3 phase 660 V ±5%"],
    "1x230_10": ["1~ 230 В ±10 %", "однофазная сеть 230 В, допуск ±10 %", "1 phase 230 V ±10%"],
    "1x220_240": ["1~ 220–240 В", "однофазная сеть от 220 до 240 В", "1 phase 220-240 V"],
}

# --------------------------------------------------------------------------
# Equipment models: printed attributes (brand units) -> canonical values
# --------------------------------------------------------------------------

IP_PUMP = ["IP44", "IP54", "IP55", "IP65", "IP68"]
IP_MOTOR = ["IP54", "IP55", "IP65", "IP66"]
IP_SENSOR = ["IP54", "IP65", "IP66", "IP67", "IP68"]
TMAX_C = [60, 80, 90, 110, 120, 140]
TMAX_F = [140, 176, 194, 230, 248, 284]
KW_SERIES = [1.5, 2.2, 3.0, 4.0, 5.5, 7.5, 11.0, 15.0, 18.5, 22.0, 30.0, 37.0, 45.0]
HP_SERIES = [2.0, 3.0, 5.0, 7.5, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0]
PS_SERIES = [3.0, 4.0, 5.5, 7.5, 10.0, 12.5, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0]
POWER_SERIES = {"kW": KW_SERIES, "hp": HP_SERIES, "ps": PS_SERIES}
TAMB_C = [-20, -30, -40, -45, -50]
TAMB_F = [-4, -22, -40, -49, -58]
PRESS = {"MPa": [0.16, 0.25, 0.4, 0.6, 1.0, 1.6, 2.5, 4.0], "bar": [1.0, 1.6, 2.5, 4.0, 6.0, 10.0, 16.0, 25.0, 40.0],
         "psi": [15.0, 30.0, 60.0, 100.0, 150.0, 200.0, 300.0, 500.0],
         "kgf": [1.0, 1.6, 2.5, 4.0, 6.0, 10.0, 16.0, 25.0, 40.0]}
PRESS_TO_BAR = {"MPa": 10.0, "bar": 1.0, "psi": PSI, "kgf": KGF}
TPROC_C = [80, 100, 120, 150]
TPROC_F = [176, 212, 248, 302]
OUTPUTS = ["4-20", "4-20H", "0-10", "485"]
OUTPUT_TEXT = {"4-20": "4–20 мА", "4-20H": "4–20 мА + HART", "0-10": "0–10 В", "485": "RS-485 (Modbus RTU)"}
ACC = [0.1, 0.15, 0.25, 0.5, 1.0]
THREADS = ["G1/2", "M20×1,5", "1/2 NPT"]
MOUNTS = {"B3": "IM B3 (IM 1001), на лапах", "B5": "IM B5 (IM 3001), фланцевый", "B35": "IM B35 (IM 2001), лапы + фланец"}
COUNTS = {"GM": 11, "NPS": 10, "AQV": 9, "EPU": 13, "STD": 12, "MTR": 10, "DTK": 9, "PTC": 9, "VKT": 10, "MSA": 10,
          "MNT": 10}


def ip_tuple(text: str) -> tuple[int, int]:
    return int(text[2]), int(text[3])


def canon(brand: str, p: dict, ps_factor: float = PS) -> dict:
    """Canonical (SI) attribute values from printed ones (``ps_factor``: kW per л.с., differs only for a misreading)."""
    b = BRANDS[brand]
    c = {"eac": p["eac"], "ex": p["ex"], "ip": ip_tuple(p["ip"])}
    if b["cat"] == "pump":
        fq = {"m3h": 1.0, "lpm": LPM, "gpm": GPM, "lps": LPS}[b["flow"]]
        fh = FT if b["head"] == "ft" else 1.0
        c["points"] = [(q * fq, h * fh) for q, h in p["points"]]
        c["tmax"] = f2c(p["tmax"]) if b["temp"] == "F" else p["tmax"]
        c["volt"] = p["volt"]
        c["stainless"] = p["stainless"]
    elif b["cat"] == "motor":
        c["p2"] = p["power"] * (ps_factor if b["power"] == "ps" else POWER_TO_KW[b["power"]])
        c["poles"] = p["poles"]
        c["ie"] = p["ie"]
        c["mount"] = p["mount"]
        c["tamb"] = f2c(p["tamb"]) if b["temp"] == "F" else p["tamb"]
        c["volt"] = p["volt"]
    else:
        c["upper"] = p["upper"] * PRESS_TO_BAR[b["press"]]
        c["output"] = p["output"]
        c["acc"] = p["acc"]
        c["tproc"] = f2c(p["tproc"]) if b["temp"] == "F" else p["tproc"]
        c["thread"] = p["thread"]
        c["si"] = p["si"]
    return c


def _pump_points(r: random.Random, brand: str) -> tuple[list, float, float]:
    b = BRANDS[brand]
    qmax = r.uniform(8, 110)
    hmax = r.uniform(14, 90)
    pts = []
    for f in (0.0, 0.2, 0.4, 0.6, 0.8, 0.9):
        q = qmax * f
        h = hmax * (1 - 0.7 * f * f)
        if b["flow"] == "m3h":
            qp = round(q, 1)
        elif b["flow"] == "lpm":
            qp = round(q / LPM / 10) * 10
        elif b["flow"] == "lps":
            qp = round(q / LPS, 1)
        else:
            qp = round(q / GPM)
        hp = round(h / FT) if b["head"] == "ft" else round(h, 1)
        pts.append((float(qp), float(hp)))
    qmax_p = {"m3h": round(qmax, 1), "lpm": round(qmax / LPM / 10) * 10, "gpm": round(qmax / GPM),
              "lps": round(qmax / LPS, 1)}[b["flow"]]
    return pts, float(qmax_p), qmax


def _gen_printed(r: random.Random, brand: str) -> dict:
    b = BRANDS[brand]
    p: dict = {"ip": None, "eac": r.random() < 0.88, "ex": r.random() < 0.18}
    if b["cat"] == "pump":
        p["points"], p["qmax"], qsi = _pump_points(r, brand)
        p["ip"] = r.choice(IP_PUMP)
        p["tmax"] = r.choice(TMAX_F if b["temp"] == "F" else TMAX_C)
        small = qsi < 22
        p["volt"] = r.choice(["1x230_10", "1x220_240"] if small and r.random() < 0.6 else
                             ["3x380_10", "3x400_10", "3x415_5", "3x380_415"])
        p["stainless"] = r.random() < 0.35
        p["kw"] = round(max(0.37, qsi * p["points"][0][1] * (FT if b["head"] == "ft" else 1) / 367 / 0.55), 2)
    elif b["cat"] == "motor":
        p["power"] = r.choice(POWER_SERIES[b["power"]])
        p["poles"] = r.choice([2, 4, 4, 6])
        p["ie"] = r.choice([2, 3, 3, 4])
        p["ip"] = r.choice(IP_MOTOR)
        p["mount"] = r.choice(["B3", "B3", "B5", "B35"])
        p["tamb"] = r.choice(TAMB_F if b["temp"] == "F" else TAMB_C)
        p["volt"] = r.choice(["3x380_10", "3x400_10", "3x415_5", "3x380_415", "3x660"])
        p["eff"] = {2: 86.0, 3: 90.5, 4: 93.0}[p["ie"]] + r.uniform(-1.5, 1.5)
        p["rpm"] = int({2: 3000, 4: 1500, 6: 1000}[p["poles"]] * (1 - r.uniform(0.02, 0.045)))
    else:
        p["upper"] = r.choice(PRESS[b["press"]])
        p["output"] = r.choice(OUTPUTS)
        p["acc"] = r.choice(ACC)
        p["tproc"] = r.choice(TPROC_F if b["temp"] == "F" else TPROC_C)
        p["ip"] = r.choice(IP_SENSOR)
        p["thread"] = r.choice(THREADS)
        p["si"] = r.random() < 0.6
    return p


def _alt_value(r: random.Random, brand: str, p: dict, attr: str):
    """A plausible different printed value for `attr` (used for errata and old revisions)."""
    b = BRANDS[brand]
    cur = p[attr]
    if attr == "ip":
        pool = {"pump": IP_PUMP, "motor": IP_MOTOR, "sensor": IP_SENSOR}[b["cat"]]
        i = pool.index(cur)
        return pool[i + 1] if i + 1 < len(pool) and (i == 0 or r.random() < 0.7) else pool[i - 1]
    if attr in ("eac", "ex", "si", "stainless"):
        return not cur
    if attr == "tmax":
        pool = TMAX_F if b["temp"] == "F" else TMAX_C
    elif attr == "tamb":
        pool = TAMB_F if b["temp"] == "F" else TAMB_C
    elif attr == "tproc":
        pool = TPROC_F if b["temp"] == "F" else TPROC_C
    elif attr == "ie":
        pool = [2, 3, 4]
    elif attr == "acc":
        pool = ACC
    elif attr == "upper":
        pool = PRESS[b["press"]]
    elif attr == "output":
        pool = OUTPUTS
    elif attr == "mount":
        pool = ["B3", "B5", "B35"]
    elif attr == "volt":
        pool = ["3x380_10", "3x400_10", "3x415_5", "3x380_415"] if VOLTS[cur][0] == 3 else ["1x230_10", "1x220_240"]
    elif attr == "power":
        pool = POWER_SERIES[b["power"]]
        i = pool.index(cur)
        return pool[i + 1] if i + 1 < len(pool) else pool[i - 1]
    elif attr == "points":
        k = r.randint(2, 5)
        pts = list(cur)
        q, h = pts[k]
        nd = 1 if b["head"] == "m" else 0
        up = round(min(h * r.uniform(1.12, 1.25), pts[k - 1][1] * 0.97), nd)
        pts[k] = (q, up if up > h * 1.05 else round(h * r.uniform(0.8, 0.88), nd))
        return pts
    else:
        raise ValueError(attr)
    options = [v for v in pool if v != cur]
    return r.choice(options)


ERRATA_ATTRS = {"pump": ["ip", "tmax", "points", "eac", "stainless"], "motor": ["ip", "tamb", "ie", "eac", "mount", "power"],
                "sensor": ["acc", "ip", "tproc", "si", "output", "upper"]}
REV_ATTRS = {"pump": ["points", "volt", "tmax", "ip"], "motor": ["ie", "volt", "tamb", "power"],
             "sensor": ["upper", "acc", "output", "tproc"]}


def _model_name(r: random.Random, brand: str, p: dict, used: set) -> tuple[str, str]:
    b = BRANDS[brand]
    while True:
        if b["cat"] == "pump":
            core = f"{int(p['points'][3][0]) or 1}-{int(p['points'][0][1])}"
        elif b["cat"] == "motor":
            core = f"{fmt_num(p['power'], False)}-{p['poles']}{r.choice('ABKMT')}"
        else:
            core = f"{fmt_num(p['upper'], False)}{r.choice(['', 'S', 'M', 'X'])}-{r.randint(10, 99)}"
        name = f"{b['prefix']} {core}"
        if name not in used:
            used.add(name)
            break
        p = dict(p)
        if b["cat"] == "pump":
            p["points"] = [(q + 1, h) for q, h in p["points"]]
    article = f"{brand}-{r.randint(100000, 999999)}"
    return name, article


def make_models(r: random.Random) -> list[dict]:
    models: list[dict] = []
    used: set = set()
    for brand, n in COUNTS.items():
        for _ in range(n):
            p = _gen_printed(r, brand)
            name, art = _model_name(r, brand, p, used)
            models.append({"id": f"M{len(models) + 1:02d}", "brand": brand, "cat": BRANDS[brand]["cat"],
                           "name": name, "article": art, "p": p, "errata": {}, "revA": None})
    order = list(range(len(models)))
    r.shuffle(order)
    for i in order[:24]:
        m = models[i]
        attr = r.choice(ERRATA_ATTRS[m["cat"]])
        m["errata"][attr] = _alt_value(r, m["brand"], m["p"], attr)  # value printed in the main text
    for i in order[24:38]:
        m = models[i]
        old = dict(m["p"])
        for attr in r.sample(REV_ATTRS[m["cat"]], r.choice([1, 2])):
            old[attr] = _alt_value(r, m["brand"], m["p"], attr)
        m["revA"] = old
    return models


def perceive(m: dict, mode: str = "") -> dict:
    """Canonical attributes as seen by a careful reader ('') or by a naive reading."""
    p = m["p"]
    if mode in ("no_errata", "first_match") and m["errata"]:
        p = {**p, **m["errata"]}
    if mode in ("rev_a", "first_match") and m["revA"] is not None:
        p = m["revA"]
    return canon(m["brand"], p, HP if mode == "ps_as_hp" else PS)

# --------------------------------------------------------------------------
# Offers, compliance and cost
# --------------------------------------------------------------------------


def base_price(m: dict) -> float:
    c = perceive(m)
    if m["cat"] == "pump":
        qmax = max(q for q, _ in c["points"])
        hmax = max(h for _, h in c["points"])
        v = 30000 + 900 * qmax + 1100 * hmax
        v *= 1.3 if c["stainless"] else 1.0
    elif m["cat"] == "motor":
        v = 14000 * c["p2"] ** 0.82 * {2: 1.0, 3: 1.18, 4: 1.4}[c["ie"]]
    else:
        v = 9000 + 14000 / c["acc"] ** 0.5 * 0.35 + (6000 if c["output"] in ("4-20H", "485") else 0)
        v *= 1.25 if c["si"] else 1.0
    v *= 1.35 if c["ex"] else 1.0
    return v


def make_offers(r: random.Random, models: list[dict], rates: dict) -> list[dict]:
    offers = []
    for m in models:
        sups = [s for s, v in SUPPLIERS.items() if m["cat"] in v["cats"]]
        k = min(len(sups), r.choice([2, 3, 3, 4]))
        for s in r.sample(sups, k):
            sv = SUPPLIERS[s]
            rub = base_price(m) * r.uniform(0.8, 1.25) * (1.0 if sv["vat"] else 1 / 1.2)
            if sv["cur"] == "RUB":
                price = round(rub / 10) * 10.0
            else:
                price = round(rub / rate_on(rates, sv["cur"], RATE_DATES[0]), 2 if sv["cur"] != "CNY" else 0)
            if s == "S1":
                lead = r.randint(5, 25)
            elif s == "S2":
                lead = r.choice([3, 3, 21, 28, 35])
            elif s == "S3":
                lead = 7 * r.randint(4, 9)
            elif s == "S4":
                lead = r.randint(35, 70)
            elif s == "S5":
                lead = r.randint(3, 14)
            else:
                lead = r.randint(14, 45)
            offers.append({"model": m["id"], "sup": s, "price": float(price), "lead": lead, "old_price": None,
                           "ref": "article" if s in ("S4",) or (s == "S6" and r.random() < 0.5) else "name"})
    # price-list corrections (the printed table price is wrong, the correction at the end is right)
    for s in ("S2", "S6"):
        mine = [o for o in offers if o["sup"] == s]
        for o in r.sample(mine, min(4, len(mine))):
            o["old_price"] = round(o["price"] * r.choice([0.8, 0.85, 1.15, 1.2]), 2 if s == "S6" else -1)
    return offers


def complies(c: dict, q: dict) -> bool:
    if not c["eac"]:
        return False
    if "ip" in q and not (c["ip"][0] >= q["ip"][0] and c["ip"][1] >= q["ip"][1]):
        return False
    if q.get("ex") and not c["ex"]:
        return False
    if "volt" in q:
        ph, v = q["volt"]
        vp, lo, hi = VOLTS[c["volt"]]
        if vp != ph or not (lo <= v <= hi):
            return False
    if "q" in q and not any(qq >= q["q"] and hh >= q["h"] for qq, hh in c["points"]):
        return False
    if "tmed" in q and c["tmax"] < q["tmed"]:
        return False
    if q.get("stainless") and not c["stainless"]:
        return False
    if "pmin" in q and not (q["pmin"] <= c["p2"] <= q["pmax"]):
        return False
    if "poles" in q and c["poles"] != q["poles"]:
        return False
    if "ie" in q and c["ie"] < q["ie"]:
        return False
    if "mount" in q:
        ok = {"feet": ("B3", "B35"), "flange": ("B5", "B35"), "B35": ("B35",)}[q["mount"]]
        if c["mount"] not in ok:
            return False
    if "tamb" in q and c["tamb"] > q["tamb"]:
        return False
    if "umin" in q and not (q["umin"] <= c["upper"] <= q["umax"]):
        return False
    if "output" in q:
        ok = {"4-20": ("4-20", "4-20H"), "4-20H": ("4-20H",), "0-10": ("0-10",), "485": ("485",)}[q["output"]]
        if c["output"] not in ok:
            return False
    if "acc" in q and c["acc"] > q["acc"]:
        return False
    if "tproc" in q and c["tproc"] < q["tproc"]:
        return False
    if "thread" in q and c["thread"] != q["thread"]:
        return False
    return not (q.get("si") and not c["si"])


def tier_pct(s: str, qty: int) -> float:
    pct = 0.0
    for n, p in SUPPLIERS[s]["tiers"]:
        if qty >= n:
            pct = p
    return pct


def offer_total(o: dict, qty: int, day: _dt.date, rates: dict, mode: str = "") -> float:
    s = o["sup"]
    sv = SUPPLIERS[s]
    price = o["old_price"] if (mode == "no_plcorr" and o["old_price"] is not None) else o["price"]
    disc = 0.0 if mode == "no_discount" else tier_pct(s, qty)
    goods = price * (1 - disc / 100) * qty
    kind = sv["delivery"]
    if mode == "no_delivery":
        dl = 0.0
    elif kind[0] == "fixed":
        dl = kind[1]
    elif kind[0] == "pct":
        dl = goods * kind[1] / 100
    elif kind[0] == "unit":
        dl = kind[1] * qty
    else:
        dl = 0.0 if goods >= kind[2] else kind[1]
    sub = goods + dl
    if not sv["vat"] and mode != "no_vat":
        sub *= 1.2
    return round(sub * rate_on(rates, sv["cur"], day, mode), 2)


PERCEPTION_MODES = ("no_errata", "rev_a", "first_match", "ps_as_hp")
MODES = ("", "no_errata", "rev_a", "first_match", "no_blacklist", "no_vat", "no_delivery", "no_discount",
         "no_deadline", "no_upd", "obey_comment", "no_plcorr", "latest_rate", "ps_as_hp")


def ranked(world: dict, req: dict, mode: str = "") -> list[tuple[float, int, str, str]]:
    """All admissible offers sorted by (total, lead): (total, lead, model_id, supplier)."""
    q = req["q"]
    qty = req["qty"]
    if mode == "no_upd" and req.get("orig") is not None:
        q, qty = req["orig"]
    if mode == "first_match" and req.get("orig") is not None:
        q, qty = req["orig"]
    if mode == "obey_comment" and req.get("comment") is not None:
        q, qty = req["comment"]
    pmode = mode if mode in PERCEPTION_MODES else ""
    out = []
    for o in world["offers_by_cat"][req["cat"]]:
        m = world["mid"][o["model"]]
        if not complies(world["perc"][(m["id"], pmode)], q):
            continue
        if mode not in ("no_blacklist", "first_match") and banned(o["sup"], req["cat"], req["date"]):
            continue
        if mode != "no_deadline" and req["date"] + _dt.timedelta(days=o["lead"]) > req["need_by"]:
            continue
        out.append((offer_total(o, qty, req["date"], world["rates"], mode), o["lead"], m["id"], o["sup"]))
    out.sort()
    return out


def answer(world: dict, req: dict, mode: str = "") -> tuple[str, str, float] | None:
    rk = ranked(world, req, mode)
    return (rk[0][2], rk[0][3], rk[0][0]) if rk else None

# --------------------------------------------------------------------------
# Requests: candidates with safe margins, chosen to cover every trap
# --------------------------------------------------------------------------

TMED = [50, 70, 85, 100, 115, 130]
TAMB_REQ = [-15, -25, -35, -40, -45]
TPROC_REQ = [70, 90, 110, 130]


def _far(values: list[float], x: float, tol: float = 0.012) -> bool:
    return all(abs(v / x - 1) >= tol for v in values if v)


def _ip_below(r: random.Random, pool: list[str], ip: tuple) -> tuple | None:
    ok = [ip_tuple(x) for x in pool if ip_tuple(x)[0] <= ip[0] and ip_tuple(x)[1] <= ip[1]]
    return r.choice(ok) if ok else None


def _gen_q(r: random.Random, world: dict, cat: str, c: dict, brand: str) -> dict | None:
    q: dict = {}
    allc = world["cat_canon"][cat]
    if cat in ("pump", "motor"):
        ph, lo, hi = VOLTS[c["volt"]]
        vs = [v for v in ((220, 230) if ph == 1 else (380, 400)) if lo <= v <= hi]
        if not vs:
            return None
        q["volt"] = (ph, r.choice(vs))
    if cat == "pump":
        qq, hh = c["points"][r.randint(1, 5)]
        q["q"] = max(1.0, int(qq * r.uniform(0.72, 0.96) * 2) / 2)
        q["h"] = float(max(3, int(hh * r.uniform(0.72, 0.96))))
        tol = 0.012
        for x in allc:  # a curve point near a threshold matters only if its other coordinate qualifies
            for pq, ph in x["points"]:
                near_q, near_h = abs(pq / q["q"] - 1) < tol, abs(ph / q["h"] - 1) < tol
                if (near_q and ph >= q["h"] * (1 - tol)) or (near_h and pq >= q["q"] * (1 - tol)):
                    return None
        if r.random() < 0.6:
            ok = [t for t in TMED if t <= c["tmax"]]
            if ok:
                q["tmed"] = r.choice(ok)
        if r.random() < 0.5 and (ip := _ip_below(r, IP_PUMP, c["ip"])):
            q["ip"] = ip
        if c["stainless"] and r.random() < 0.5:
            q["stainless"] = True
    elif cat == "motor":
        vals = [x["p2"] for x in allc]
        q["pmin"] = int(c["p2"] * r.uniform(0.7, 0.97) * 2) / 2
        if BRANDS[brand]["power"] == "ps" and r.random() < 0.45:
            # the upper bound lies between the л.с. value and its misreading as hp
            q["pmax"] = math.ceil(c["p2"] * 1.004 * 100) / 100
            if q["pmax"] >= c["p2"] * HP / PS * 0.996 or not _far([v for v in vals if v != c["p2"]], q["pmax"], 0.004):
                return None
        else:
            q["pmax"] = int(c["p2"] * r.uniform(1.05, 1.6) * 2 + 1) / 2
            if not _far(vals, q["pmax"]):
                return None
        if q["pmin"] <= 0 or not _far(vals, q["pmin"]):
            return None
        q["poles"] = c["poles"]
        if r.random() < 0.6:
            q["ie"] = r.choice([v for v in (2, 3, 4) if v <= c["ie"]])
        if r.random() < 0.5 and (ip := _ip_below(r, IP_MOTOR, c["ip"])):
            q["ip"] = ip
        if r.random() < 0.6:
            q["mount"] = {"B3": "feet", "B5": "flange"}.get(c["mount"]) or r.choice(["feet", "flange", "B35"])
        if r.random() < 0.5:
            ok = [t for t in TAMB_REQ if t >= c["tamb"]]
            if ok:
                q["tamb"] = r.choice(ok)
    else:
        u = c["upper"]
        step = 0.1 if u < 2 else 0.5
        q["umin"] = round(int(u * r.uniform(0.5, 0.94) / step) * step, 2)
        q["umax"] = round((int(u * r.uniform(1.12, 2.2) / step) + 1) * step, 2)
        vals = [x["upper"] for x in allc]
        if q["umin"] <= 0 or not (_far(vals, q["umin"]) and _far(vals, q["umax"])):
            return None
        if r.random() < 0.8:
            q["output"] = r.choice(["4-20", "4-20H"]) if c["output"] == "4-20H" else c["output"]
        if r.random() < 0.6:
            q["acc"] = r.choice([a for a in ACC if a >= c["acc"]])
        if r.random() < 0.5:
            ok = [t for t in TPROC_REQ if t <= c["tproc"]]
            if ok:
                q["tproc"] = r.choice(ok)
        if r.random() < 0.5 and (ip := _ip_below(r, IP_SENSOR, c["ip"])):
            q["ip"] = ip
        if r.random() < 0.35:
            q["thread"] = c["thread"]
        if c["si"] and r.random() < 0.6:
            q["si"] = True
    if c["ex"] and r.random() < 0.5:
        q["ex"] = True
    return q


def _mutate(r: random.Random, cat: str, q: dict, qty: int) -> tuple[dict, int]:
    q2 = dict(q)
    choice = r.random()
    if choice < 0.45:
        return q2, r.choice([n for n in (1, 2, 3, 4, 5, 6, 8, 10, 12) if n != qty])
    if cat == "pump":
        key = r.choice(["tmed", "ip", "h"])
        if key == "tmed":
            q2["tmed"] = r.choice([t for t in TMED if t != q.get("tmed")])
        elif key == "ip":
            q2["ip"] = r.choice([ip_tuple(x) for x in IP_PUMP if ip_tuple(x) != q.get("ip")])
        else:
            q2["h"] = float(int(q["h"] * r.choice([0.8, 1.2, 1.35])))
    elif cat == "motor":
        key = r.choice(["ie", "ip", "tamb"])
        if key == "ie":
            q2["ie"] = r.choice([v for v in (2, 3, 4) if v != q.get("ie")])
        elif key == "ip":
            q2["ip"] = r.choice([ip_tuple(x) for x in IP_MOTOR if ip_tuple(x) != q.get("ip")])
        else:
            q2["tamb"] = r.choice([t for t in TAMB_REQ if t != q.get("tamb")])
    else:
        key = r.choice(["acc", "ip", "tproc"])
        if key == "acc":
            q2["acc"] = r.choice([a for a in ACC if a != q.get("acc")])
        elif key == "ip":
            q2["ip"] = r.choice([ip_tuple(x) for x in IP_SENSOR if ip_tuple(x) != q.get("ip")])
        else:
            q2["tproc"] = r.choice([t for t in TPROC_REQ if t != q.get("tproc")])
    return q2, qty


def _differs(a, b) -> bool:
    if a is None or b is None:
        return (a is None) != (b is None)
    return a[0] != b[0] or a[1] != b[1] or abs(a[2] - b[2]) > 1.0


def _candidates(r: random.Random, world: dict, n: int) -> list[dict]:
    out = []
    models = world["models"]
    tries = 0
    while len(out) < n and tries < n * 6:
        tries += 1
        m = r.choice(models)
        basis = r.choice(["", "", "", "no_errata", "rev_a"])
        if basis == "no_errata" and not m["errata"]:
            basis = ""
        if basis == "rev_a" and m["revA"] is None:
            basis = ""
        q = _gen_q(r, world, m["cat"], world["perc"][(m["id"], basis)], m["brand"])
        if q is None:
            continue
        day = D(2026, 10, 5) + _dt.timedelta(days=r.randint(0, 65))
        if day.weekday() >= 5:
            day -= _dt.timedelta(days=day.weekday() - 4)
        need_by = day + _dt.timedelta(days=r.randint(12, 80))
        req = {"cat": m["cat"], "q": q, "qty": r.choice([1, 1, 2, 2, 3, 4, 5, 6, 8, 10, 12]), "date": day,
               "need_by": need_by, "orig": None, "comment": None, "target": m["id"]}
        if any(day + _dt.timedelta(days=o["lead"]) == need_by for o in world["offers_by_cat"][m["cat"]]):
            continue
        if r.random() < 0.3:
            req["orig"] = _mutate(r, m["cat"], q, req["qty"])
        elif r.random() < 0.2:
            req["comment"] = _mutate(r, m["cat"], q, req["qty"])
        rk = ranked(world, req)
        if len(rk) >= 2 and not (rk[1][0] >= rk[0][0] * 1.004 and rk[1][0] - rk[0][0] >= 100):
            continue
        truth = (rk[0][2], rk[0][3], rk[0][0]) if rk else None
        req["truth"] = truth
        req["naive"] = {mode: answer(world, req, mode) for mode in MODES[1:]}
        req["diffs"] = {mode for mode, a in req["naive"].items() if _differs(a, truth)}
        out.append(req)
    return out


def _select(r: random.Random, cands: list[dict], total: int = N_REQ) -> list[dict]:
    need = {mode: 6 for mode in MODES[1:]}
    quota = {"pump": N_REQ // 3, "motor": N_REQ // 3, "sensor": N_REQ // 3}
    none_left = 4
    chosen: list[dict] = []
    used_targets: set = set()

    def allowed(c):
        if quota[c["cat"]] <= 0 or c in chosen:
            return False
        return not (c["truth"] is None and none_left <= 0)

    nones = sorted((c for c in cands if c["truth"] is None), key=lambda c: -len(c["diffs"]))
    for c in nones:
        if none_left and quota[c["cat"]] > N_REQ // 3 - 2 and c["target"] not in used_targets:
            chosen.append(c)
            used_targets.add(c["target"])
            quota[c["cat"]] -= 1
            none_left -= 1
            for mode in c["diffs"]:
                need[mode] -= 1
    while len(chosen) < total:
        best, best_score = None, -1.0
        for c in cands:
            if not allowed(c):
                continue
            score = sum(1 for mode in c["diffs"] if need[mode] > 0) + 0.3 * len(c["diffs"]) / len(MODES)
            score -= 0.5 if c["target"] in used_targets else 0.0
            if score > best_score:
                best, best_score = c, score
        assert best is not None, "not enough request candidates"
        chosen.append(best)
        used_targets.add(best["target"])
        quota[best["cat"]] -= 1
        if best["truth"] is None:
            none_left -= 1
        for mode in best["diffs"]:
            need[mode] -= 1
    return chosen


def make_world(r: random.Random) -> dict:
    models = make_models(r)
    rates = _make_rates(r)
    offers = make_offers(r, models, rates)
    world = {"models": models, "mid": {m["id"]: m for m in models}, "offers": offers, "rates": rates,
             "offers_by_cat": {c: [o for o in offers if o["model"] in {m["id"] for m in models if m["cat"] == c}]
                               for c in CAT_NAME}}
    world["perc"] = {(m["id"], mode): perceive(m, mode) for m in models for mode in ("",) + PERCEPTION_MODES}
    world["cat_canon"] = {c: [world["perc"][(m["id"], "")] for m in models if m["cat"] == c] for c in CAT_NAME}
    return world

# --------------------------------------------------------------------------
# Datasheet text banks
# --------------------------------------------------------------------------

DESC = {
    "pump": [
        "Насос {name} — одноступенчатый центробежный консольно-моноблочный агрегат, предназначенный для перекачивания чистых неагрессивных жидкостей без абразивных включений. Корпус спиральный, рабочее колесо закрытого типа, уплотнение вала — торцевое.",
        "Агрегат {name} разработан для систем водоснабжения, отопления и кондиционирования зданий, а также для технологических контуров промышленных предприятий, где требуется стабильная подача при умеренном напоре.",
        "Конструкция насоса позволяет обслуживать проточную часть без демонтажа корпуса из трубопровода: достаточно снять электродвигатель вместе с фонарём и рабочим колесом.",
        "Рабочее колесо динамически отбалансировано на заводе, что снижает вибрацию и продлевает срок службы подшипников двигателя и торцевого уплотнения.",
        "Насосы серии выпускаются в нескольких исполнениях по материалам проточной части и по типу уплотнения; конкретное исполнение указано в разделе технических характеристик данного паспорта.",
        "Благодаря компактной моноблочной конструкции насос занимает мало места в тепловом пункте и может устанавливаться как на фундамент, так и непосредственно на трубопровод при условии надёжных опор.",
    ],
    "motor": [
        "Электродвигатель {name} — асинхронный трёхфазный с короткозамкнутым ротором, общепромышленного назначения. Предназначен для привода насосов, вентиляторов, компрессоров, конвейеров и другого оборудования, не требующего регулирования частоты вращения.",
        "Станина и подшипниковые щиты двигателя выполнены из алюминиевого сплава или чугуна (в зависимости от габарита), что обеспечивает хороший теплоотвод и достаточную механическую прочность.",
        "Обмотка статора выполнена эмалированным проводом с изоляцией класса нагревостойкости F; превышение температуры при номинальной нагрузке соответствует классу B, что даёт запас по ресурсу изоляции.",
        "Подшипники закрытого типа заполнены смазкой на весь срок службы и не требуют обслуживания в течение первых 20 000 часов работы при нормальных условиях эксплуатации.",
        "Двигатель допускает работу от преобразователя частоты при условии применения фильтров, ограничивающих скорость нарастания напряжения; подробности — в руководстве по применению с частотными приводами.",
        "Коробка выводов может быть развёрнута с шагом 90° для удобства подключения кабеля; в ней предусмотрены зажим заземления и кабельные вводы.",
    ],
    "sensor": [
        "Датчик {name} предназначен для непрерывного преобразования избыточного давления жидкостей и газов в унифицированный выходной сигнал. Чувствительный элемент — тензорезистивный на керамической или металлической мембране.",
        "Датчик применяется в системах автоматического контроля и регулирования технологических процессов в энергетике, водоснабжении, химической и пищевой промышленности.",
        "Электронный блок датчика выполняет температурную компенсацию и линеаризацию характеристики, что обеспечивает заявленную погрешность во всём рабочем диапазоне температур.",
        "Корпус датчика выполнен из нержавеющей стали; мембрана приварена лазерной сваркой, что исключает применение уплотнительных колец в контакте с измеряемой средой.",
        "Датчик имеет защиту от переполюсовки питания и от импульсных перенапряжений в линии связи, что важно при длинных кабельных трассах на промышленных объектах.",
        "Конструкция допускает монтаж в любом положении; для измерения давления пара рекомендуется использовать импульсную трубку или охладитель.",
    ],
}
GENERIC = {
    "install": [
        "Монтаж должен выполняться квалифицированным персоналом, имеющим допуск к работам с электрооборудованием до 1000 В. Перед монтажом проверьте целостность упаковки и соответствие маркировки изделия заказу.",
        "Место установки должно обеспечивать свободный доступ для обслуживания и достаточное охлаждение. Не допускается установка вблизи источников открытого огня и в местах возможного затопления, если это не предусмотрено исполнением изделия.",
        "Кабель питания подбирается по току и условиям прокладки в соответствии с действующими правилами устройства электроустановок. Сечение и тип кабеля в комплект поставки не входят.",
        "Трубопроводы и конструкции, к которым крепится изделие, не должны передавать на него механические нагрузки. При необходимости используйте компенсаторы и дополнительные опоры.",
        "После монтажа проверьте надёжность всех соединений и заземления. Пробный пуск выполняйте в присутствии ответственного лица, фиксируя результаты в журнале пусконаладочных работ.",
        "Перед первым пуском убедитесь, что направление вращения (для вращающихся машин) или полярность подключения (для датчиков) соответствуют схеме, приведённой в руководстве по монтажу.",
    ],
    "service": [
        "Периодичность технического обслуживания зависит от условий эксплуатации. Рекомендуется не реже одного раза в шесть месяцев проводить внешний осмотр, проверку креплений и очистку от загрязнений.",
        "Во время обслуживания изделие должно быть отключено от сети и защищено от случайного включения. Работы на находящемся под давлением оборудовании запрещены.",
        "Результаты обслуживания заносятся в эксплуатационный журнал. Отсутствие записей о регламентных работах может служить основанием для отказа в гарантийном ремонте.",
        "Запасные части рекомендуется заказывать у официальных дистрибьюторов производителя; применение неоригинальных комплектующих снимает изделие с гарантии.",
        "При появлении посторонних шумов, вибрации или отклонений в показаниях эксплуатацию следует прекратить до выяснения и устранения причины.",
        "Ресурс изделия до капитального ремонта при соблюдении требований настоящего паспорта составляет не менее 40 000 часов.",
    ],
    "safety": [
        "Изделие соответствует требованиям безопасности, установленным для электрооборудования общепромышленного назначения. Эксплуатация с повреждённой изоляцией, без заземления или со снятыми защитными кожухами запрещена.",
        "При работе с изделием соблюдайте требования охраны труда, действующие на предприятии. Не допускайте к эксплуатации лиц, не прошедших инструктаж.",
        "Изделие не предназначено для работы с легковоспламеняющимися средами, если иное прямо не указано в разделе о взрывозащите.",
        "Поверхности изделия могут нагреваться в процессе работы; прикосновение к ним без средств защиты может привести к ожогам.",
    ],
    "storage": [
        "Изделие хранится в упаковке изготовителя в закрытых помещениях при температуре от −30 до +40 °C и относительной влажности не более 80 %. Срок хранения без переконсервации — 2 года.",
        "Транспортирование допускается любым видом крытого транспорта в соответствии с правилами перевозки грузов, действующими на данном виде транспорта. Изделие должно быть надёжно закреплено.",
        "При длительном хранении рекомендуется раз в три месяца проворачивать вал (для вращающихся машин) и проверять состояние консервационного покрытия.",
    ],
    "warranty": [
        "Гарантийный срок — 24 месяца со дня ввода в эксплуатацию, но не более 30 месяцев со дня отгрузки с завода. Гарантия действует при соблюдении условий транспортирования, хранения, монтажа и эксплуатации.",
        "Гарантия не распространяется на изделия с механическими повреждениями, следами самостоятельного ремонта, а также на повреждения, вызванные нарушением параметров питающей сети.",
        "Претензии по гарантии направляются изготовителю или официальному дистрибьютору с приложением акта рекламации и копии эксплуатационного журнала.",
    ],
    "disposal": [
        "По окончании срока службы изделие подлежит утилизации в соответствии с требованиями законодательства; металлические части сдаются во вторичную переработку, электронные компоненты — специализированным организациям.",
        "Изделие не содержит драгоценных металлов в количествах, подлежащих учёту, и не содержит веществ, запрещённых к применению в электрооборудовании.",
    ],
    "package": [
        "Комплект поставки: изделие, паспорт, руководство по монтажу и эксплуатации, упаковка. Ответные фланцы, крепёж и кабельные вводы поставляются по отдельному заказу, если не указано иное.",
        "Упаковка — картонная коробка или деревянный ящик (для изделий массой свыше 50 кг) с влагозащитной плёнкой. На упаковке нанесены манипуляционные знаки.",
    ],
}

APPLICATIONS = {
    "pump": ["системы отопления и горячего водоснабжения зданий", "контуры охлаждения технологического оборудования",
             "повышение давления в системах холодного водоснабжения", "циркуляция в системах кондиционирования",
             "перекачивание конденсата и питательной воды", "промывка фильтров водоподготовки",
             "оросительные и поливочные системы", "подача воды в моечные машины и линии"],
    "motor": ["приводы центробежных насосов и вентиляторов", "ленточные и цепные конвейеры", "компрессорные установки",
              "дробилки и мельницы малой и средней мощности", "перемешивающие устройства и мешалки",
              "станки и деревообрабатывающее оборудование", "подъёмно-транспортные механизмы с лёгким режимом работы"],
    "sensor": ["системы водоснабжения и водоотведения", "тепловые пункты и котельные", "гидравлические и пневматические системы",
               "компрессорные станции", "системы пожаротушения", "технологические линии пищевых производств",
               "узлы учёта энергоносителей"],
}
TROUBLE = {
    "pump": [("Насос не создаёт напор", "Воздух в корпусе насоса", "Удалить воздух через пробку, проверить герметичность всасывающей линии"),
             ("Повышенный шум и вибрация", "Кавитация или износ подшипников", "Проверить подпор на всасывании, заменить подшипники"),
             ("Течь по валу", "Износ торцевого уплотнения", "Заменить торцевое уплотнение"),
             ("Срабатывает защита двигателя", "Перегрузка по току, заклинивание колеса", "Проверить рабочую точку и свободное вращение вала"),
             ("Подача ниже расчётной", "Засорение рабочего колеса или сетки", "Очистить проточную часть"),
             ("Насос не запускается", "Нет питания или обрыв фазы", "Проверить питающую сеть и автоматический выключатель")],
    "motor": [("Двигатель не запускается", "Обрыв фазы, неверная схема соединения", "Проверить питание и схему звезда/треугольник"),
              ("Повышенный нагрев", "Перегрузка, загрязнение рёбер охлаждения", "Снизить нагрузку, очистить корпус"),
              ("Повышенная вибрация", "Несоосность валов, дисбаланс муфты", "Выполнить центровку, отбалансировать муфту"),
              ("Шум подшипников", "Износ или недостаток смазки", "Заменить подшипники"),
              ("Срабатывает защита", "Межвитковое замыкание", "Проверить сопротивление изоляции, отправить в ремонт"),
              ("Пониженная частота вращения", "Пониженное напряжение сети", "Проверить напряжение на клеммах")],
    "sensor": [("Нет выходного сигнала", "Обрыв линии, неверная полярность", "Проверить подключение и напряжение питания"),
               ("Сигнал нестабилен", "Наводки на линии связи", "Использовать экранированный кабель, заземлить экран с одной стороны"),
               ("Смещение нуля", "Механическое напряжение при монтаже", "Выполнить подстройку нуля при нулевом давлении"),
               ("Показания завышены", "Гидроудары в системе", "Установить демпфер или гаситель пульсаций"),
               ("Сигнал выше 20 мА", "Перегрузка по давлению", "Проверить давление в системе, при повреждении заменить датчик"),
               ("Выход из строя после монтажа", "Превышение момента затяжки", "Соблюдать момент затяжки из руководства")],
}
COMMISSION = [
    "Пусконаладочные работы выполняются после окончания монтажа и проверки сопротивления изоляции. Результаты измерений заносятся в протокол, который хранится вместе с паспортом.",
    "При первом включении контролируйте ток, температуру корпуса и отсутствие посторонних шумов в течение не менее 30 минут работы.",
    "После пуска проверьте герметичность всех соединений и отсутствие вибраций трубопроводов и опор.",
    "Перед сдачей в эксплуатацию убедитесь, что паспорт заполнен: указаны дата ввода, место установки и ответственное лицо.",
]

SECTION_TITLES = {
    "desc": ["Назначение и описание", "Общие сведения", "Описание изделия"],
    "install": ["Монтаж", "Указания по монтажу", "Установка и подключение"],
    "service": ["Техническое обслуживание", "Обслуживание и ремонт"],
    "safety": ["Меры безопасности", "Требования безопасности"],
    "storage": ["Хранение и транспортирование", "Транспортирование и хранение"],
    "warranty": ["Гарантийные обязательства", "Гарантия изготовителя"],
    "disposal": ["Утилизация"],
    "package": ["Комплектность", "Комплект поставки и упаковка"],
}

# --------------------------------------------------------------------------
# Datasheet rendering
# --------------------------------------------------------------------------

FLOW_UNIT = {"m3h": "м³/ч", "lpm": "л/мин", "gpm": "gpm (US)", "lps": "л/с"}
HEAD_UNIT = {"m": "м", "ft": "ft"}
PRESS_UNIT = {"MPa": "МПа", "bar": "бар", "psi": "psi", "kgf": "кгс/см²"}


def _t(brand: str, v: float) -> str:
    return f"{v:+.0f} °F" if BRANDS[brand]["temp"] == "F" else f"{v:+.0f} °C"


def show(m: dict, attr: str, v, r: random.Random | None = None) -> str:
    """Printed form of one attribute value."""
    b = BRANDS[m["brand"]]
    cm = b["comma"]
    if attr == "ip":
        return v
    if attr in ("tmax", "tproc"):
        return f"до {_t(m['brand'], v)}"
    if attr == "tamb":
        top = 104 if b["temp"] == "F" else 40
        return f"от {_t(m['brand'], v)} до {_t(m['brand'], top)}"
    if attr == "volt":
        return VOLT_TEXT[v][2 if b["layout"] == "kv" else (1 if b["layout"] == "prose" else 0)]
    if attr == "stainless":
        return ("нержавеющая сталь AISI 316" if m["id"][-1] in "13579" else "нержавеющая сталь AISI 304") if v else \
            ("чугун EN-GJL-250" if m["id"][-1] in "02468" else "серый чугун СЧ20, рабочее колесо — латунь")
    if attr == "eac":
        if v:
            return f"декларация о соответствии ЕАЭС N RU Д-{m['brand'][:2]}.РА01.В.{int(m['article'][-6:]) % 90000 + 10000}/26"
        return "декларация ЕАЭС в процессе оформления (ожидается во II квартале 2027 г.)"
    if attr == "ex":
        return "взрывозащищённое исполнение, маркировка 1Ex db IIB T4 Gb" if v else "общепромышленное исполнение (без взрывозащиты)"
    if attr == "power":
        return f"{fmt_num(v, cm)} {({'hp': 'hp', 'ps': 'л.с.'}).get(b['power'], 'кВт')}"
    if attr == "ie":
        return f"IE{v}"
    if attr == "mount":
        return MOUNTS[v]
    if attr == "upper":
        return f"0…{fmt_num(v, cm)} {PRESS_UNIT[b['press']]}"
    if attr == "output":
        return OUTPUT_TEXT[v]
    if attr == "acc":
        return f"±{fmt_num(v, cm)} %"
    if attr == "thread":
        return v
    if attr == "si":
        num = int(m["article"][-5:]) % 90000 + 10000
        return (f"внесён в Государственный реестр средств измерений, регистрационный № {num}-26, межповерочный интервал 5 лет"
                if v else "в Государственный реестр средств измерений не внесён")
    raise ValueError(attr)


LABELS = {
    "pump": [("ip", "Степень защиты", "Protection class / Степень защиты"),
             ("tmax", "Температура перекачиваемой жидкости", "Liquid temperature / Температура жидкости"),
             ("volt", "Электропитание", "Power supply / Питание"),
             ("stainless", "Материал проточной части", "Wetted parts / Проточная часть")],
    "motor": [("power", "Номинальная мощность на валу P2", "Rated output P2 / Мощность на валу"),
              ("ie", "Класс энергоэффективности", "Efficiency class / Класс энергоэффективности"),
              ("ip", "Степень защиты двигателя", "Motor protection / Степень защиты двигателя"),
              ("mount", "Монтажное исполнение", "Mounting / Монтажное исполнение"),
              ("tamb", "Температура окружающей среды", "Ambient temperature / Температура окружающей среды"),
              ("volt", "Напряжение питания", "Voltage / Напряжение")],
    "sensor": [("upper", "Диапазон измерений", "Measuring range / Диапазон измерений"),
               ("output", "Выходной сигнал", "Output / Выходной сигнал"),
               ("acc", "Основная приведённая погрешность", "Accuracy / Погрешность"),
               ("tproc", "Температура измеряемой среды", "Process temperature / Температура среды"),
               ("ip", "Степень защиты", "Protection class / Степень защиты"),
               ("thread", "Присоединение к процессу", "Process connection / Присоединение")],
}


def _spec_items(m: dict, p: dict, r: random.Random) -> list[tuple[str, str]]:
    b = BRANDS[m["brand"]]
    cm = b["comma"]
    kv = b["layout"] == "kv"
    items = [(en if kv else ru, show(m, attr, p[attr])) for attr, ru, en in LABELS[m["cat"]]]
    if m["cat"] == "pump":
        items.insert(0, ("Max. head / Максимальный напор" if kv else "Максимальный напор",
                         f"{fmt_num(p['points'][0][1], cm, 1)} {HEAD_UNIT[b['head']]}"))
        items.insert(1, ("Max. flow / Максимальная подача" if kv else "Максимальная подача",
                         f"{fmt_num(p['qmax'], cm, 1)} {FLOW_UNIT[b['flow']]}"))
        items.append(("Motor power / Мощность двигателя" if kv else "Мощность электродвигателя",
                      f"{fmt_num(p['kw'], cm)} кВт"))
        items.append(("Max. system pressure / Давление в системе" if kv else "Максимальное рабочее давление",
                      "145 psi" if kv else f"{r.choice([10, 16])} бар"))
        items.append(("Noise / Уровень шума" if kv else "Уровень шума", f"{r.randint(52, 70)} дБ(А)"))
    elif m["cat"] == "motor":
        p2kw = p["power"] * POWER_TO_KW[b["power"]]
        items.insert(1, ("Input power P1 / Потребляемая мощность" if kv else "Потребляемая мощность P1",
                         f"{fmt_num(p2kw / p['eff'] * 100, cm)} кВт"))
        sync = {2: 3000, 4: 1500, 6: 1000}[p["poles"]]
        items.insert(2, ("Poles / Число полюсов" if kv else "Число полюсов",
                         f"{p['poles']} (синхронная частота {sync} об/мин)"))
        items.insert(3, ("Rated speed / Номинальная частота вращения" if kv else "Номинальная частота вращения",
                         f"{p['rpm']} об/мин"))
        items.insert(4, ("Efficiency / КПД" if kv else "КПД при номинальной нагрузке", f"{fmt_num(p['eff'], cm, 1)} %"))
        items.append(("Terminal box / Клеммная коробка" if kv else "Степень защиты клеммной коробки", "IP66"))
        items.append(("Weight / Масса" if kv else "Масса", f"{fmt_num(8 + p2kw * 6.5, cm, 0)} кг"))
    else:
        items.insert(1, ("Overload / Перегрузочная способность" if kv else "Допустимая перегрузка",
                         f"до {fmt_num(p['upper'] * 2, cm)} {PRESS_UNIT[b['press']]}"))
        items.append(("Supply / Питание" if kv else "Напряжение питания", "12…36 В постоянного тока"))
        items.append(("Ambient / Окружающая среда" if kv else "Температура окружающей среды",
                      "от −40 до +176 °F" if kv else "от −40 до +80 °C"))
    return items


def _render_specs(m: dict, p: dict, r: random.Random) -> str:
    b = BRANDS[m["brand"]]
    items = _spec_items(m, p, r)
    if b["layout"] == "table":
        return "| Параметр | Значение |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in items)
    if b["layout"] == "kv":
        return "\n".join(f"{k}: {v}" for k, v in items)
    if b["layout"] == "bullets":
        return "\n".join(f"- **{k}:** {v}" for k, v in items)
    sents = [f"{k} — {v}." for k, v in items]
    out, cur = [], []
    for s in sents:
        cur.append(s)
        if len(cur) == 3:
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return "\n\n".join(out)


def _curve(m: dict, p: dict) -> str:
    b = BRANDS[m["brand"]]
    cm = b["comma"]
    rows = "\n".join(f"| {fmt_num(q, cm, 1)} | {fmt_num(h, cm, 1)} |" for q, h in p["points"])
    return (f"| Подача, {FLOW_UNIT[b['flow']]} | Напор, {HEAD_UNIT[b['head']]} |\n|---|---|\n{rows}\n\n"
            "Характеристика снята на воде при температуре +20 °C с номинальной частотой вращения; допуск по напору — "
            "по ISO 9906, класс 3B. Работа насоса за пределами таблицы не допускается.")


ERRATA_WHERE = {"points": "таблице рабочей характеристики", "eac": "разделе «Сертификация»",
                "si": "разделе «Метрологические характеристики»", "stainless": "технических характеристиках"}


def _errata(m: dict) -> str:
    lines = ["## Errata — исправления к данному паспорту", "",
             "Изготовитель уведомляет об ошибках, допущенных при вёрстке настоящего паспорта. Исправления имеют "
             "приоритет над текстом выше.", ""]
    for attr, old in m["errata"].items():
        new = m["p"][attr]
        if attr == "points":
            for (q, h0), (_, h1) in zip(old, new, strict=True):
                if h0 != h1:
                    cm = BRANDS[m["brand"]]["comma"]
                    lines.append(f"- В таблице рабочей характеристики для подачи {fmt_num(q, cm, 1)} "
                                 f"{FLOW_UNIT[BRANDS[m['brand']]['flow']]} напор ошибочно указан "
                                 f"{fmt_num(h0, cm, 1)}; правильное значение — {fmt_num(h1, cm, 1)} "
                                 f"{HEAD_UNIT[BRANDS[m['brand']]['head']]}.")
            continue
        where = ERRATA_WHERE.get(attr, "технических характеристиках")
        lines.append(f"- В {where} ошибочно указано: «{show(m, attr, old)}». Следует читать: «{show(m, attr, new)}».")
    return "\n".join(lines)


def render_datasheet(r: random.Random, m: dict, p: dict, rev: str | None, with_errata: bool) -> str:
    b = BRANDS[m["brand"]]
    name = m["name"]
    main = {**p, **m["errata"]} if with_errata else p
    rm = random.Random(m["article"])  # incidental numbers stay the same across revisions
    head = {"prose": f"# {name}\n\nТехнический паспорт изделия",
            "table": f"# Паспорт изделия {name}",
            "kv": f"# DATASHEET {name}\n\nОфициальный перевод технического описания изготовителя",
            "bullets": f"# {name}\n\nПаспорт и руководство по эксплуатации"}[b["layout"]]
    if rev == "A":
        red = "Редакция документа: Rev A от 18.11.2024."
    elif rev == "B":
        red = "Редакция документа: Rev B от 20.05.2026. Заменяет редакцию Rev A; при расхождениях действует Rev B."
    else:
        red = f"Редакция документа: {r.choice(['1.0', '1.1', '2.0'])}."
    parts = [head, f"Изготовитель: {b['name']} ({b['country']}). Модель: {name}. Артикул: {m['article']}.\n{red}"]

    def sec(key, paras):
        parts.append(f"## {r.choice(SECTION_TITLES[key])}\n\n" + "\n\n".join(paras))

    sec("desc", [x.format(name=name) for x in r.sample(DESC[m["cat"]], 4)])
    apps = r.sample(APPLICATIONS[m["cat"]], 5)
    parts.append("## Области применения\n\n" + "\n".join(f"- {a};" for a in apps[:-1]) + f"\n- {apps[-1]}.")
    parts.append("## Технические характеристики\n\n" + _render_specs(m, main, rm))
    if m["cat"] == "pump":
        parts.append("## Рабочая характеристика\n\n" + _curve(m, main))
    dims = [rm.randint(180, 900) for _ in range(3)]
    parts.append("## Габаритные размеры и масса\n\n| L, мм | B, мм | H, мм | Масса нетто, кг | Масса брутто, кг |\n"
                 f"|---|---|---|---|---|\n| {dims[0]} | {dims[1]} | {dims[2]} | {rm.randint(3, 180)} | {rm.randint(185, 260)} |")
    cert = [f"Подтверждение соответствия: {show(m, 'eac', main['eac'])}.",
            f"Исполнение по взрывозащите: {show(m, 'ex', main['ex'])}."]
    if m["cat"] == "sensor":
        cert.append(f"Метрология: {show(m, 'si', main['si'])}.")
    parts.append("## Сертификация\n\n" + "\n\n".join(cert))
    sec("install", r.sample(GENERIC["install"], 3))
    parts.append("## Пусконаладка\n\n" + "\n\n".join(r.sample(COMMISSION, 3)))
    sec("service", r.sample(GENERIC["service"], 3))
    rows = r.sample(TROUBLE[m["cat"]], 5)
    parts.append("## Возможные неисправности\n\n| Неисправность | Вероятная причина | Способ устранения |\n|---|---|---|\n"
                 + "\n".join(f"| {a} | {b} | {c} |" for a, b, c in rows))
    for key, k in (("safety", 2), ("storage", 2), ("package", 1), ("warranty", 2), ("disposal", 1)):
        sec(key, r.sample(GENERIC[key], k))
    if with_errata and m["errata"]:
        parts.append(_errata(m))
    return "\n\n".join(parts) + "\n"


def render_datasheets(r: random.Random, models: list[dict]) -> dict[str, str]:
    docs = []
    for m in models:
        if m["revA"] is not None:
            docs.append(render_datasheet(r, m, m["revA"], "A", False))
            docs.append(render_datasheet(r, m, m["p"], "B", False))
        else:
            docs.append(render_datasheet(r, m, m["p"], None, True))
    r.shuffle(docs)
    return {f"datasheets/ds_{i + 1:03d}.md": d for i, d in enumerate(docs)}

# --------------------------------------------------------------------------
# Price lists, rates, blacklist
# --------------------------------------------------------------------------

ACCESSORIES = [
    "Комплект ответных фланцев DN50", "Кабель силовой ВВГнг 4×2,5, бухта 50 м", "Виброопоры резиновые, комплект 4 шт.",
    "Кабельный ввод M25×1,5 латунный", "Клапан обратный муфтовый 1¼\"", "Манометр показывающий 0–16 бар",
    "Кран трёхходовой для манометра", "Охладитель импульсной линии", "Комплект уплотнений торцевых",
    "Частотный преобразователь 7,5 кВт (под заказ, цена по запросу)",
]
SUP_INTRO = {
    "S1": "ООО «ТехноСнаб» — многопрофильный поставщик промышленного оборудования. Работаем с 2009 года, собственный склад в Подольске.",
    "S2": "АО «ГидроКомплект» благодарит за интерес к нашей продукции и направляет коммерческое предложение на насосное оборудование и электродвигатели.",
    "S3": "ООО «ЕвроТехИмпорт» — официальный импортёр европейского насосного и электротехнического оборудования. Поставки напрямую со складов производителей.",
    "S4": "ООО «Азия-Индастри» поставляет оборудование производства Китая и дистрибутирует ряд европейских марок через склады в Шанхае и Новосибирске.",
    "S5": "ООО «ПромДатчик» специализируется на средствах измерения давления, температуры и расхода. Проводим поверку и калибровку перед отгрузкой.",
    "S6": "ООО «Интерсервис» — поставки электродвигателей и контрольно-измерительных приборов под проекты промышленных предприятий.",
}


def _ref(world: dict, o: dict) -> str:
    m = world["mid"][o["model"]]
    return m["article"] if o["ref"] == "article" else m["name"]


def _lead_text(o: dict) -> str:
    s, lead = o["sup"], o["lead"]
    if s == "S3":
        return f"{lead // 7} недель"
    if s == "S2":
        return "в наличии, срок поставки 3 дня" if lead == 3 else f"под заказ, срок поставки {lead} {_days(lead)}"
    return f"{lead} дн."


def _days(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "дня"
    return "дней"


def _price_text(o: dict, printed: float) -> str:
    cur = SUPPLIERS[o["sup"]]["cur"]
    if cur == "RUB":
        return fmt_rub(printed)
    return f"{printed:.2f}" if cur != "CNY" else f"{printed:.0f}"


def _terms(s: str) -> list[str]:
    sv = SUPPLIERS[s]
    cur_name = {"RUB": "российских рублях", "EUR": "евро", "USD": "долларах США", "CNY": "китайских юанях"}[sv["cur"]]
    vat = ("Все цены указаны с учётом НДС 20 %." if sv["vat"] else
           "Цены указаны без НДС. НДС 20 % начисляется дополнительно — на стоимость товара и на доставку.")
    tiers = "; ".join(f"от {n} шт. одной позиции — скидка {fmt_num(p, True)} %" for n, p in sv["tiers"])
    kind = sv["delivery"]
    if kind[0] == "fixed":
        dl = f"Доставка до склада заказчика — {fmt_num(kind[1], True)} {'руб.' if sv['cur'] == 'RUB' else sv['cur']} за заказ."
    elif kind[0] == "pct":
        dl = f"Доставка — {fmt_num(kind[1], True)} % от стоимости товара после скидки."
    elif kind[0] == "unit":
        dl = f"Доставка — {fmt_num(kind[1], True)} {sv['cur']} за каждую единицу товара."
    else:
        dl = (f"Доставка — {fmt_num(kind[1], True)} руб. за заказ; бесплатно, если стоимость товара после скидки "
              f"составляет не менее {fmt_rub(kind[2])} руб.")
    return [f"Цены указаны в {cur_name}.", vat, f"Скидки за объём: {tiers}. Скидка применяется к цене единицы.", dl,
            "Сроки поставки указаны в календарных днях (или неделях) от даты заявки до поступления на склад заказчика.",
            "Оплата — 100 % предоплата по счёту; счёт действителен 5 банковских дней.",
            "Гарантия — по условиям изготовителя; все поставляемые изделия новые, в заводской упаковке.",
            "Возврат товара надлежащего качества не производится; претензии по комплектности принимаются в течение "
            "10 дней с даты поставки.",
            "Сопроводительная документация (паспорта, декларации, сертификаты) передаётся вместе с товаром.",
            "Цены действительны до 31.12.2026 при условии оплаты счёта в установленный срок."]


def render_pricelists(r: random.Random, world: dict) -> dict[str, str]:
    files = {}
    by_sup: dict[str, list] = {}
    for o in world["offers"]:
        by_sup.setdefault(o["sup"], []).append(o)
    for s, offers in sorted(by_sup.items()):
        sv = SUPPLIERS[s]
        offers = list(offers)
        r.shuffle(offers)
        acc = r.sample(ACCESSORIES, 4)
        terms = _terms(s)
        head = f"{sv['legal']}\nПрайс-лист на {r.choice(['октябрь–декабрь', 'IV квартал'])} 2026 г."
        corr = [o for o in offers if o["old_price"] is not None]
        if sv["fmt"] == "csv":
            rows = [[_ref(world, o), world["mid"][o["model"]]["article"] if o["ref"] == "name" else "", _price_text(o, o["price"]),
                     o["lead"]] for o in offers]
            rows += [[a, "", fmt_rub(r.randint(8, 90) * 100.0), r.randint(3, 20)] for a in acc]
            slug = {"S1": "tehnosnab", "S5": "promdatchik"}[s]
            buf = ["позиция;артикул;цена;срок_дней"] + [";".join(str(x) for x in row) for row in rows]
            files[f"pricelists/{slug}_price.csv"] = "\n".join(buf) + "\n"
            files[f"pricelists/{slug}_terms.md"] = (f"# Условия поставки к прайс-листу {slug}_price.csv\n\n{head}\n\n"
                                                    + SUP_INTRO[s] + "\n\n" + "\n".join(f"- {t}" for t in terms)
                                                    + "\n\nСтолбец «цена» — цена за единицу, столбец «срок_дней» — срок поставки.\n")
            continue
        lines = []
        for o in offers:
            printed = o["old_price"] if o["old_price"] is not None else o["price"]
            lines.append((_ref(world, o), _price_text(o, printed), _lead_text(o)))
        lines += [(a, "по запросу", "—") for a in acc]
        r.shuffle(lines)
        if sv["fmt"] == "letter":
            body = [f"# Коммерческое предложение\n\n{head}", SUP_INTRO[s], "Предлагаем следующие позиции:"]
            body += [f"— {a}: {p} руб. за шт. ({t})" if p != "по запросу" else f"— {a}: цена по запросу" for a, p, t in lines]
            body += ["Условия:"] + [f"• {t}" for t in terms]
            body += ["Будем рады сотрудничеству. Менеджер проекта — Ю. В. Корнеева, тел. +7 495 000-17-40."]
            if corr:
                body.append("P.S. При подготовке предложения допущены ошибки в ценах; правильные цены:")
                body += [f"— {_ref(world, o)}: {_price_text(o, o['price'])} руб. за шт." for o in corr]
            files["pricelists/gidrokomplekt_kp.md"] = "\n\n".join(body) + "\n"
        elif sv["fmt"] == "table":
            body = [f"# Прайс-лист\n\n{head}", SUP_INTRO[s], "| Наименование | Цена за шт., EUR | Срок поставки |", "|---|---|---|"]
            body += [f"| {a} | {p} | {t} |" for a, p, t in lines]
            files["pricelists/eurotechimport.md"] = "\n".join(body[:2]) + "\n\n" + "\n".join(body[2:]) + \
                "\n\n## Условия\n\n" + "\n".join(f"- {t}" for t in terms) + "\n"
        elif sv["fmt"] == "kv":
            body = [f"{head}\n\n{SUP_INTRO[s]}"]
            for a, p, t in lines:
                body.append(f"Позиция: {a}\nЦена за ед., CNY: {p}\nСрок поставки: {t}")
            body.append("УСЛОВИЯ\n" + "\n".join(terms))
            files["pricelists/asia_industry.txt"] = "\n\n".join(body) + "\n"
        else:
            body = [f"{head}\n{SUP_INTRO[s]}\n", "Наименование / артикул .......... цена за шт., USD .......... срок"]
            body += [f"{a} .......... {p} .......... {t}" for a, p, t in lines]
            body.append("\nУсловия поставки:\n" + "\n".join(f"* {t}" for t in terms))
            if corr:
                body.append("\nИСПРАВЛЕНИЕ К ПРАЙС-ЛИСТУ от 12.10.2026. В ценах следующих позиций допущены опечатки, "
                            "верные цены за шт. (USD, без НДС):")
                body += [f"{_ref(world, o)} — {_price_text(o, o['price'])}" for o in corr]
            files["pricelists/interservis.txt"] = "\n".join(body) + "\n"
    return files


def rates_md(rates: dict) -> str:
    lines = ["# Курсы валют для расчётов", "",
             "Курсы публикуются казначейством по понедельникам. Для заявки используется курс, опубликованный "
             "последним на дату заявки (дата публикации не позже даты заявки). Рублей за единицу валюты.", "",
             "| Дата публикации | USD | EUR | CNY |", "|---|---|---|---|"]
    for i, d in enumerate(RATE_DATES):
        lines.append(f"| {d:%d.%m.%Y} | {fmt_num(rates['USD'][i][1], True)} | {fmt_num(rates['EUR'][i][1], True)} | "
                     f"{fmt_num(rates['CNY'][i][1], True, 4)} |")
    return "\n".join(lines) + "\n"


BLACKLIST_MD = """# Служебная записка отдела экономической безопасности

Кому: всем инициаторам закупок и отделу снабжения
Тема: ограничения на закупки у отдельных поставщиков

По итогам проверки исполнения договоров за III квартал 2026 года сообщаем следующее.

1. По ООО «Азия-Индастри» выявлены систематические нарушения при поставке насосного оборудования
   (несоответствие маркировки, отсутствие части документации). С 2 ноября 2026 года закупка насосов
   у ООО «Азия-Индастри» запрещена. Закупки электродвигателей и датчиков у этого поставщика
   по-прежнему разрешены.

2. ООО «Интерсервис» не представило подтверждения поверки части поставленных датчиков давления.
   С 19 октября 2026 года закупка датчиков давления у ООО «Интерсервис» запрещена до отдельного
   распоряжения. Закупки электродвигателей у ООО «Интерсервис» разрешены.

3. АО «ГидроКомплект» находится на плановой проверке. Ограничений на закупки у АО «ГидроКомплект» нет.

Запрет применяется к заявкам, дата которых приходится на период его действия.

---

# Дополнение к служебной записке

ООО «Интерсервис» представило документы о поверке. Запрет на закупку датчиков давления
у ООО «Интерсервис» снимается с 23 ноября 2026 года (для заявок, датированных 23.11.2026 и позже).
Остальные положения записки сохраняют силу.
"""

# --------------------------------------------------------------------------
# Request rendering
# --------------------------------------------------------------------------

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября",
          "декабря"]
ENGINEERS = [
    ("Сидоров Павел Андреевич", "инженер-механик", "цех № 3"), ("Лебедева Ирина Олеговна", "главный энергетик", "энергослужба"),
    ("Гончаров Артём Ильич", "инженер КИПиА", "служба КИПиА"), ("Мельникова Ольга Сергеевна", "технолог", "участок водоподготовки"),
    ("Рахимов Тимур Рашидович", "начальник участка", "котельная № 2"), ("Потапов Денис Игоревич", "инженер-энергетик", "цех № 1"),
    ("Широкова Анна Павловна", "ведущий инженер", "отдел главного механика"), ("Васин Олег Николаевич", "мастер", "насосная станция"),
]
REVIEWERS = [("Корнилов Е. В.", "отдел снабжения"), ("Федорова Л. М.", "планово-экономический отдел"),
             ("Юдин С. А.", "служба заказчика"), ("Абрамова Н. К.", "отдел снабжения")]
CONTEXT = {
    "pump": [
        "Меняем насос на подпитке контура отопления: старый агрегат выработал ресурс, торцевое уплотнение течёт уже третий раз за год.",
        "Требуется насос для циркуляции в технологическом контуре охлаждения пресса. Существующий насос оставим как резервный.",
        "Для нового теплового пункта на складе готовой продукции нужен сетевой насос; проект согласован, монтажники готовы.",
        "На участке водоподготовки планируется замена насосов промывки фильтров, работают в повторно-кратковременном режиме.",
        "Насосы нужны для подачи воды на мойку тары; линия работает в две смены, остановка допускается только в выходные.",
        "Нужен насос для откачки воды из приямка теплового узла; сейчас откачиваем переносным дренажным насосом.",
        "Для узла смешения системы отопления административного корпуса требуется новый циркуляционный насос.",
        "Меняем насосы подачи охлаждающей жидкости на станки участка мехобработки, старые насосы подтекают.",
    ],
    "motor": [
        "Нужен двигатель для привода вентилятора градирни; старый двигатель сгорел, сейчас вентилятор работает от временной схемы.",
        "Требуются двигатели для замены на ленточных конвейерах склада сырья — текущие двигатели перегреваются летом.",
        "Двигатель нужен для привода насоса оборотного водоснабжения, который перекладываем на новую раму.",
        "Для модернизации дробилки нужен новый приводной двигатель; редуктор остаётся прежним.",
        "Заменяем двигатели вытяжных вентиляторов в покрасочной камере в рамках планового ремонта.",
        "Нужен двигатель для мешалки в реакторе участка смол; мешалка работает круглосуточно, остановки редкие.",
        "Двигатели нужны для привода рольгангов на участке раскроя, старые двигатели сняты с производства.",
        "Для компрессора пневмосети требуется новый двигатель — текущий после перемотки греется и шумит.",
    ],
    "sensor": [
        "Нужны датчики давления для новой системы диспетчеризации водозаборного узла; сигналы заводим в существующий контроллер.",
        "Заменяем датчики давления на линиях подачи пара к автоклавам: старые приборы дают дрейф показаний.",
        "Для узла коммерческого учёта тепла на вводе требуется датчик давления; проект согласован с теплоснабжающей организацией.",
        "Устанавливаем датчики на напорных линиях насосной станции второго подъёма, чтобы автоматизировать переключение насосов.",
        "Датчики нужны для контроля давления масла в гидросистемах прессов, сигнал уходит на панель оператора.",
        "Датчики ставим на коллекторы котельной вместо электроконтактных манометров, которые постоянно залипают.",
        "Для испытательного стенда гидроцилиндров нужны датчики давления; стенд аттестуется, поэтому к документам "
        "будут вопросы.",
        "Модернизируем узел подпитки тепловой сети: датчики нужны на входе и выходе подпиточных насосов.",
    ],
}
CONTEXT_GENERIC = [
    "Бюджет на эту позицию предусмотрен в плане ремонтов на текущий год.",
    "Монтаж выполнит собственная служба, пусконаладка поставщика не нужна.",
    "Если по каким-то позициям будут вопросы, я на связи, лучше писать в корпоративный мессенджер.",
    "Старое оборудование после замены передадим на склад для разборки на запчасти.",
    "Прошу обратить внимание на сроки: работы привязаны к графику остановки линии.",
    "Документацию (паспорта, декларации) прошу передать вместе с оборудованием.",
    "Согласование с главным инженером получено устно, служебную записку донесу.",
    "Если по срокам будет впритык — сообщите заранее, попробуем сдвинуть монтаж внутри смены.",
    "Приёмку проведём комиссионно, с участием службы качества.",
    "Прошу не дробить поставку: всё количество должно прийти одной партией.",
]

HISTORY = {
    "pump": [
        "Для справки: сейчас на этом месте стоит насос 2009 года выпуска, его паспорт давно утерян, табличка нечитаема. По опыту эксплуатации он справлялся, но последние полгода явно не додаёт напора, поэтому ориентироваться на него не стоит — параметры выше посчитаны проектировщиком заново.",
        "История вопроса: в прошлом году мы уже пытались заменить этот насос, но заявку отклонили из-за бюджета. Сейчас деньги выделены, и затягивать нельзя — отопительный сезон начался.",
        "Раньше здесь работали два насоса попеременно; после реконструкции контура остаётся один рабочий и один резервный, поэтому требования к каждому одинаковые.",
    ],
    "motor": [
        "Для справки: предыдущий двигатель был отечественного производства, отработал около двенадцати лет. Его параметры проектировщик пересчитал под новую нагрузку, поэтому брать «такой же» не нужно — только по требованиям выше.",
        "История вопроса: в прошлом году на этом механизме уже меняли двигатель, но подобрали его без учёта условий на улице, и зимой он не запустился. Повторять ошибку не хотим.",
        "Раньше привод работал через ременную передачу, сейчас переходим на прямое соединение через муфту, поэтому частота вращения двигателя важна.",
    ],
    "sensor": [
        "Для справки: сейчас стоят стрелочные манометры и старые датчики без документов; их данные в расчёт не берём, требования выше согласованы с проектировщиком АСУ ТП.",
        "История вопроса: прошлые датчики закупали без проверки по реестру, и при приёмке узла учёта возникли замечания. В этот раз прошу проверять документы внимательно.",
        "Раньше сигналы шли на самописцы, теперь всё заводится в контроллер, поэтому тип выходного сигнала важен.",
    ],
}
HISTORY_MODEL = [
    "Для справки: сейчас на этом месте стоит {old} ({year} г. выпуска). Брать такой же не обязательно и даже не "
    "нужно — он не справляется; подбирайте строго по требованиям выше.",
    "В прошлый раз по похожей заявке брали {old} у {sup}. Это не требование и не рекомендация: служба эксплуатации "
    "к нему претензий не имела, но параметры тогда были другие.",
    "Коллеги с соседнего участка советуют {old} — говорят, надёжный. Решайте сами, мне важны только параметры, "
    "перечисленные выше.",
    "У нас на складе лежит паспорт на {old}, но самого изделия нет — его передали на другой объект ещё в {year} году. "
    "К этой заявке он отношения не имеет.",
    "Проектировщик в спецификации сначала вписал {old}, но потом заменил на параметрическое описание — оно и приведено "
    "выше. Модель из спецификации не обязательна.",
]
JUSTIFY = [
    "Обоснование закупки: без замены оборудования участок работает с повышенным риском аварийной остановки. Экономический эффект оценён в служебной записке, которую могу приложить по запросу.",
    "Обоснование: оборудование включено в план технического перевооружения; замена снизит расходы на внеплановые ремонты и простои.",
    "Обоснование: требование предписания надзорного органа по итогам последней проверки, срок устранения замечаний ограничен.",
    "Обоснование: текущее оборудование не обеспечивает требуемых параметров процесса, что подтверждено замерами службы главного инженера.",
]


def date_ru(d: _dt.date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year} г."


def _num(x: float) -> str:
    return fmt_num(x, True, 2)


def req_phrase(r: random.Random, cat: str, key: str, q: dict) -> str:
    v = q.get(key)
    if key == "q":
        lpm, lps = q["q"] / LPM, q["q"] / LPS
        flow = f"{round(lpm)} л/мин" if abs(lpm - round(lpm)) < 1e-9 and r.random() < 0.5 else f"{_num(q['q'])} м³/ч"
        if abs(lps * 10 - round(lps * 10)) < 1e-9 and r.random() < 0.5:
            flow = f"{_num(round(lps, 1))} л/с"
        return r.choice([f"подача не менее {flow} при напоре не менее {_num(q['h'])} м",
                         f"рабочая точка: {flow} и не ниже {_num(q['h'])} метров напора",
                         f"насос должен выдавать не меньше {flow}, развивая при этом напор от {_num(q['h'])} м"])
    if key == "tmed":
        return r.choice([f"температура перекачиваемой воды — до {v} °C", f"среда — горячая вода до {v} градусов",
                         f"насос должен выдерживать жидкость температурой {v} °C"])
    if key == "ip":
        return r.choice([f"степень защиты не ниже IP{v[0]}{v[1]}", f"IP не хуже {v[0]}{v[1]}",
                         f"защита оболочки — минимум IP{v[0]}{v[1]}"])
    if key == "volt":
        ph, volts = v
        return (r.choice([f"питание от трёхфазной сети {volts} В", f"сеть у нас 3×{volts} В", f"напряжение 3~{volts} В"])
                if ph == 3 else r.choice([f"питание от однофазной сети {volts} В", f"розетка 1~{volts} В, трёх фаз там нет"]))
    if key == "stainless":
        return r.choice(["проточная часть — только нержавеющая сталь (среда агрессивная)",
                         "чугун не подходит, нужна нержавейка в проточной части"])
    if key == "ex":
        return r.choice(["место установки — взрывоопасная зона, нужно взрывозащищённое исполнение (Ex)",
                         "обязательно исполнение с маркировкой взрывозащиты Ex"])
    if key == "pmin":
        return r.choice([f"мощность на валу от {_num(q['pmin'])} до {_num(q['pmax'])} кВт",
                         f"номинальная мощность не меньше {_num(q['pmin'])} кВт, но и не больше {_num(q['pmax'])} кВт"])
    if key == "poles":
        sync = {2: 3000, 4: 1500, 6: 1000}[v]
        return r.choice([f"синхронная частота вращения {sync} об/мин ({v} полюса)" if v < 5 else
                         f"синхронная частота вращения {sync} об/мин ({v} полюсов)",
                         f"двигатель на {sync} оборотов (синхронных)", f"{v}-полюсный двигатель"])
    if key == "ie":
        return r.choice([f"класс энергоэффективности не ниже IE{v}", f"по энергоэффективности — IE{v} или выше"])
    if key == "mount":
        return {"feet": r.choice(["крепление на лапах", "двигатель ставится на лапы"]),
                "flange": r.choice(["фланцевое крепление", "крепление фланцем, лапы не нужны"]),
                "B35": "нужны одновременно и лапы, и фланец (исполнение IM B35)"}[v]
    if key == "tamb":
        return r.choice([f"работа на улице, зимой до {v} °C", f"должен работать при температуре воздуха до {v} °C"])
    if key == "umin":
        return r.choice([f"верхний предел измерений — не меньше {_num(q['umin'])} бар и не больше {_num(q['umax'])} бар",
                         f"диапазон датчика: верхний предел от {_num(q['umin'])} до {_num(q['umax'])} бар"])
    if key == "output":
        return {"4-20": r.choice(["выходной сигнал 4–20 мА", "токовый выход 4–20 мА"]),
                "4-20H": "выход 4–20 мА обязательно с HART", "0-10": "выходной сигнал 0–10 В",
                "485": "цифровой выход RS-485 (Modbus RTU)"}[v]
    if key == "acc":
        return r.choice([f"погрешность не хуже ±{_num(v)} %", f"точность ±{_num(v)} % или лучше"])
    if key == "tproc":
        return r.choice([f"температура измеряемой среды до {v} °C", f"среда нагрета до {v} °C"])
    if key == "thread":
        return f"присоединение к процессу {v}"
    if key == "si":
        return r.choice(["прибор идёт на коммерческий учёт — должен быть внесён в Госреестр СИ",
                         "нужен датчик, внесённый в Государственный реестр средств измерений"])
    raise ValueError(key)


KEY_ORDER = ["q", "pmin", "poles", "umin", "output", "acc", "tmed", "tproc", "tamb", "ip", "volt", "ie", "mount",
             "thread", "stainless", "si", "ex"]
KEY_LABEL = {"tmed": "температуре среды", "ip": "степени защиты", "h": "напору", "ie": "энергоэффективности",
             "tamb": "температуре окружающего воздуха", "acc": "погрешности", "tproc": "температуре среды"}
CAT_WORD = {"pump": "насос", "motor": "электродвигатель", "sensor": "датчик давления"}


def _update_text(r: random.Random, cat: str, old: tuple, new: tuple) -> list[str]:
    (q0, n0), (q1, n1) = old, new
    out = []
    if n0 != n1:
        out.append(f"количество меняется: нужно {n1} шт. вместо {n0}")
    for key in KEY_ORDER + ["h"]:
        if key == "h":
            if q0.get("h") != q1.get("h"):
                out.append("уточнение по рабочей точке: " + req_phrase(r, cat, "q", q1))
            continue
        if q0.get(key) == q1.get(key):
            continue
        if key not in q1:
            out.append(f"требование к {KEY_LABEL.get(key, key)} снимается")
        else:
            out.append(("вместо указанного выше: " if key in q0 else "дополнительно: ") + req_phrase(r, cat, key, q1))
    return out


NUM_GEN = {2: "двух", 3: "трёх", 4: "четырёх"}


def _qty_text(r: random.Random, n: int) -> str:
    """Quantity phrased in one of several ways; the total is always ``n``."""
    opts = [f"Количество — {n} шт.", f"Нужно {n} шт.", f"Всего требуется {n} шт."]
    for a in (2, 3, 4):
        if n % a == 0 and n > a:
            obj = r.choice(["линий", "установок", "секций"])
            opts.append(f"Нужно по {n // a} шт. на каждую из {NUM_GEN[a]} {obj} (резерв не нужен).")
    if n >= 3:
        k = r.randint(1, n // 2)
        opts.append(f"Количество: {n - k} шт. в работу и ещё {k} шт. в резерв на склад.")
    return r.choice(opts)


def render_request(r: random.Random, world: dict, rid: str, req: dict) -> str:
    cat = req["cat"]
    who, pos, dept = req["author"]
    shown_q, shown_n = req["orig"] if req["orig"] is not None else (req["q"], req["qty"])
    reqs = [req_phrase(r, cat, k, shown_q) for k in KEY_ORDER if k in shown_q]
    r.shuffle(reqs)
    parts = [f"# Заявка на закупку {rid}", f"Дата заявки: {req['date']:%d.%m.%Y}\nИнициатор: {who}, {pos}, {dept}"]
    parts.append(r.choice(CONTEXT[cat]))
    lead = r.choice([f"Нужен {CAT_WORD[cat]}. Требования: ", f"Прошу закупить {CAT_WORD[cat]} со следующими параметрами: ",
                     "Что важно: "])
    parts.append(lead + "; ".join(reqs) + ".")
    parts.append(_qty_text(r, shown_n) + " "
                 + r.choice([f"Оборудование должно поступить на склад не позднее {date_ru(req['need_by'])}",
                             f"Крайний срок поступления на склад — {date_ru(req['need_by'])}",
                             f"Срок: на складе не позже {date_ru(req['need_by'])}"]))
    parts.append(" ".join(r.sample(CONTEXT_GENERIC, 3)))
    if r.random() < 0.45:
        others = [m for m in world["models"] if m["cat"] == cat and m["id"] != req["target"]]
        old = r.choice(others)
        sup = r.choice([v["legal"] for v in SUPPLIERS.values() if cat in v["cats"]])
        parts.append(r.choice(HISTORY_MODEL).format(old=old["name"], sup=sup, year=r.randint(2012, 2021)))
    else:
        parts.append(r.choice(HISTORY[cat]))
    parts.append(r.choice(JUSTIFY))
    first = who.split()[0]
    if req["orig"] is not None:
        d = req["date"] + _dt.timedelta(days=r.randint(1, 4))
        for line in _update_text(r, cat, req["orig"], (req["q"], req["qty"])):
            parts.append(f"UPD {d:%d.%m.%Y} ({first}, автор заявки): {line}.")
    if req["comment"] is not None:
        rv, dept2 = r.choice(REVIEWERS)
        d = req["date"] + _dt.timedelta(days=r.randint(1, 5))
        for line in _update_text(r, cat, (req["q"], req["qty"]), req["comment"]):
            parts.append(f"Комментарий {d:%d.%m.%Y} ({rv}, {dept2}): предлагаю так — {line}.")
    return "\n\n".join(parts) + "\n"

# --------------------------------------------------------------------------
# Rules, build, setup / gold / check
# --------------------------------------------------------------------------

RULES_MD = """# Правила подбора оборудования и поставщика

Для каждой заявки из `requests/` нужно выбрать одну модель оборудования и одного поставщика.

## 1. Паспорта оборудования (`datasheets/`)

- Одна модель может быть описана несколькими паспортами разных редакций (ревизий). Действует паспорт
  **самой поздней редакции** целиком; более ранняя редакция не используется ни в какой части.
- Раздел «Errata» в конце паспорта исправляет ошибки этого паспорта и имеет приоритет над его текстом.
- Модель определяется наименованием (заголовок паспорта, поле «Модель») и артикулом изготовителя.

## 2. Соответствие заявке

Модель подходит, только если выполняются все требования заявки. Требования, которые в заявке не
названы, не проверяются. Упоминания конкретных моделей в тексте заявки (что стоит сейчас, что брали
раньше, что советуют коллеги или вписал проектировщик) требованиями не являются. Кроме того, для любой
заявки обязательно:

- **ЕАЭС**: у изделия есть оформленная декларация (сертификат) ЕАЭС. «В процессе оформления» — не подходит.

Как проверять отдельные требования:

- **Насос, подача и напор**: насос подходит, если в таблице его рабочей характеристики есть строка,
  в которой подача не меньше требуемой **и** напор не меньше требуемого. Значения «максимальный напор»
  и «максимальная подача» вне таблицы для этого не используются.
- **Температура перекачиваемой жидкости / измеряемой среды**: верхняя граница, указанная в паспорте,
  не ниже температуры из заявки.
- **Материал проточной части**: требование «нержавеющая сталь» выполняет любая нержавеющая сталь; чугун,
  латунь и т. п. — нет.
- **Степень защиты IP**: обе цифры кода не меньше требуемых (IP55 удовлетворяет «не ниже IP54», IP65 не
  удовлетворяет «не ниже IP56»). У двигателей учитывается степень защиты двигателя, а не клеммной коробки.
- **Питание**: число фаз совпадает, и напряжение сети из заявки лежит в допустимом диапазоне изделия
  (номинал с допуском, границы включительно, или явно указанный диапазон).
- **Взрывозащита**: если заявка её требует, нужно исполнение с маркировкой взрывозащиты Ex.
- **Двигатель, мощность**: номинальная мощность на валу P2 (не потребляемая мощность P1) в указанных
  границах включительно.
- **Двигатель, частота вращения**: сравнивается синхронная частота (по числу полюсов: 2 полюса — 3000,
  4 — 1500, 6 — 1000 об/мин); номинальная частота вращения под нагрузкой немного ниже и роли не играет.
- **Класс энергоэффективности**: не ниже требуемого (IE2 < IE3 < IE4).
- **Монтажное исполнение**: IM B3 — на лапах, IM B5 — фланцевое, IM B35 — лапы и фланец одновременно
  (подходит и под требование «на лапах», и под «фланцевое»).
- **Температура окружающего воздуха**: нижняя граница рабочего диапазона двигателя не выше температуры
  из заявки (двигатель «от −40 °C» подходит для работы «до −35 °C», а «от −30 °C» — нет).
- **Датчик, диапазон**: верхний предел измерений (нижний у всех датчиков 0) лежит в границах из заявки
  включительно. Перегрузочная способность значения не имеет.
- **Выходной сигнал**: «4–20 мА + HART» подходит и под требование «4–20 мА»; если заявка требует HART,
  подходит только выход с HART.
- **Погрешность**: не больше указанной в заявке.
- **Присоединение к процессу**: совпадает с заявкой.
- **Госреестр СИ**: если заявка этого требует, датчик должен быть внесён в Государственный реестр средств
  измерений.

## 3. Единицы

1 US gpm = 0,2271 м³/ч; 1 л/мин = 0,06 м³/ч; 1 л/с = 3,6 м³/ч; 1 ft = 0,3048 м;
1 hp = 0,7457 кВт; 1 л.с. = 0,7355 кВт (метрическая лошадиная сила — это не hp);
°C = (°F − 32) × 5/9; 1 psi = 0,068948 бар; 1 МПа = 10 бар; 1 кгс/см² = 0,980665 бар.

## 4. Заявки (`requests/`)

- Дата заявки — дата в её заголовке. По ней определяются курс валюты и действие запретов.
- Количество — общее число единиц по заявке: рабочие и резервные вместе; «по 2 шт. на каждую из трёх
  линий» — это 6 шт. Скидка за объём считается от этого общего количества.
- Строки «UPD» от автора заявки изменяют заявку (количество или требования); изменения действуют
  вместо исходного текста, дата заявки при этом не меняется.
- Комментарии других сотрудников (снабжение, экономисты и т. п.) заявку **не меняют**.

## 5. Поставщики и цены (`pricelists/`)

- Поставщик может поставить модель, только если она есть в его прайс-листе (по наименованию или по
  артикулу изготовителя). Позиции «по запросу» не рассматриваются.
- Исправления цен в конце прайс-листа (P.S., «исправление») заменяют цены, указанные выше.
- Условия каждого поставщика (валюта, НДС, скидки, доставка) — в его прайс-листе или файле условий.

## 6. Полная стоимость

1. Цена единицы по прайс-листу (с учётом исправлений) × (1 − скидка за объём) — скидка по количеству
   этой позиции в заявке, по наибольшему достигнутому порогу.
2. Стоимость товара = цена со скидкой × количество.
3. Плюс доставка по условиям поставщика (процент считается от стоимости товара после скидки).
4. Если цены поставщика без НДС — (товар + доставка) × 1,2.
5. Перевод в рубли по курсу из `rates.md`: последний курс, опубликованный не позже даты заявки.
6. Итог округляется до копеек только в конце.

## 7. Срок поставки

Предложение успевает, если дата заявки + срок поставки (календарные дни; неделя = 7 дней) не позже
требуемой даты поступления на склад.

## 8. Запреты

Служебная записка `blacklist_memo.md` запрещает отдельных поставщиков для отдельных категорий на
определённые периоды (по дате заявки). Запрещённые предложения не рассматриваются.

## 9. Выбор

Из всех подходящих, успевающих и незапрещённых пар «модель + поставщик» выбирается пара с наименьшей
полной стоимостью; при равной стоимости — с меньшим сроком поставки. Если подходящих пар нет — `NONE`.

## 10. Результат

`selection.csv` с заголовком `request_id,model,supplier,total_rub`, по строке на каждую заявку:
`model` — наименование модели, как в заголовке паспорта; `supplier` — название поставщика без
организационно-правовой формы (например, `ТехноСнаб`); `total_rub` — полная стоимость в рублях
с двумя знаками после точки. Если подходящих вариантов нет: `model` и `supplier` — `NONE`,
`total_rub` — пусто.
"""


@functools.cache
def build() -> dict:
    r = rng(TASK_ID)
    world = make_world(r)
    cands = _candidates(r, world, 2500)
    sel = sorted(_select(r, cands), key=lambda q: (q["date"], q["target"]))
    files: dict[str, str] = {}
    for i, req in enumerate(sel):
        req["rid"] = f"R{i + 1:02d}"
        req["author"] = ENGINEERS[(i * 3 + r.randint(0, 7)) % len(ENGINEERS)]
        files[f"requests/{req['rid']}.md"] = render_request(r, world, req["rid"], req)
    files.update(render_datasheets(r, world["models"]))
    files.update(render_pricelists(r, world))
    files["rates.md"] = rates_md(world["rates"])
    files["blacklist_memo.md"] = BLACKLIST_MD
    files["rules.md"] = RULES_MD
    return {"world": world, "requests": sel, "files": files}


PROMPT = (
    "Ты помогаешь отделу снабжения. В рабочей папке: `requests/` — 45 заявок инженеров на закупку "
    "оборудования (насосы, электродвигатели, датчики давления), написанных свободным текстом; `datasheets/` — "
    "технические паспорта моделей в разной вёрстке и в разных единицах измерения (в том числе с исправлениями "
    "и несколькими редакциями); `pricelists/` — прайс-листы и условия шести поставщиков; `rates.md` — курсы "
    "валют; `blacklist_memo.md` — служебная записка о запретах; `rules.md` — правила подбора и расчёта "
    "стоимости. Материала много, и учитывать нужно каждый документ.\n\n"
    "Для каждой заявки выбери по правилам из `rules.md` самую дешёвую по полной стоимости подходящую модель "
    "и поставщика и запиши результат в `selection.csv` с заголовком `request_id,model,supplier,total_rub` "
    "(по строке на заявку R01–R45; `model` — наименование модели как в заголовке паспорта, `supplier` — "
    "название поставщика без ООО/АО, `total_rub` — полная стоимость в рублях с двумя знаками после точки; "
    "если подходящего варианта нет — `NONE,NONE,` ). Входные файлы не меняй."
)


def setup(ws: Path) -> None:
    for rel, text in build()["files"].items():
        write(ws, rel, text)


def _rows(ans_by_req: dict) -> list[list]:
    b = build()
    rows = []
    for req in b["requests"]:
        a = ans_by_req[req["rid"]]
        if a is None:
            rows.append([req["rid"], "NONE", "NONE", ""])
        else:
            rows.append([req["rid"], b["world"]["mid"][a[0]]["name"], SUPPLIERS[a[1]]["name"], f"{a[2]:.2f}"])
    return rows


def _write_selection(ws: Path, ans_by_req: dict) -> None:
    write_csv(ws, "selection.csv", ["request_id", "model", "supplier", "total_rub"], _rows(ans_by_req))


def gold(ws: Path) -> None:
    _write_selection(ws, {q["rid"]: q["truth"] for q in build()["requests"]})


def _key(text: str) -> str:
    text = re.sub(r"^(ооо|ао|зао|пао)\s+", "", norm(text))
    return re.sub(r"[\s\-–«»\"'.,/()]", "", text)


def check(ws: Path) -> str:
    b = build()
    rows = read_csv(ws, "selection.csv", required=["request_id", "model", "supplier", "total_rub"])
    got = {}
    for row in rows:
        rid = row["request_id"].strip().upper()
        assert rid not in got, f"заявка {rid} встречается повторно"
        got[rid] = row
    correct, errors = 0, []
    for req in b["requests"]:
        rid, truth = req["rid"], req["truth"]
        row = got.get(rid)
        if row is None:
            errors.append(f"{rid}: нет строки")
            continue
        if truth is None:
            ok = _key(row["model"]) in ("none", "") and _key(row["supplier"]) in ("none", "")
        else:
            m = b["world"]["mid"][truth[0]]
            sv = SUPPLIERS[truth[1]]
            ok_model = _key(row["model"]) in (_key(m["name"]), _key(m["article"]))
            ok_sup = _key(row["supplier"]) in (_key(sv["name"]), _key(sv["legal"]))
            total = row["total_rub"].replace(" ", "").replace(" ", "").replace(",", ".")
            try:
                ok_total = abs(float(total) - truth[2]) <= 1.0
            except ValueError:
                ok_total = False
            ok = ok_model and ok_sup and ok_total
        if ok:
            correct += 1
        else:
            errors.append(f"{rid}: неверный выбор или сумма")
    return require_share(len(b["requests"]), correct, min_share=MIN_OK / N_REQ, what="заявки", errors=errors)


def _naive_writer(mode: str):
    def miss(ws: Path) -> None:
        _write_selection(ws, {q["rid"]: q["naive"][mode] for q in build()["requests"]})

    miss.__name__ = f"near_miss_{mode}"
    miss.__doc__ = f"Naive reading: {mode}"
    return miss


NEAR_MISSES = [_naive_writer(mode) for mode in (
    "first_match", "no_errata", "rev_a", "no_blacklist", "no_vat", "no_delivery", "no_discount", "no_deadline",
    "no_upd", "obey_comment", "no_plcorr", "latest_rate", "ps_as_hp")]

TASK = long_task(
    id="task_409_procurement_datasheets",  # registry id; TASK_ID stays the generator seed
    name="Закупка оборудования по техническим паспортам и прайс-листам",
    prompt=PROMPT,
    setup=setup,
    gold=gold,
    check=check,
    tags=("reading", "units", "procurement", "extraction"),
)
