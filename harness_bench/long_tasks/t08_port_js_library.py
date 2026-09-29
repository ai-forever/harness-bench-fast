"""long_08_port_js_library — port a JS calendar-rules library to Python.

The workspace holds the JavaScript library ``chronorule`` (≈17 modules with
JSDoc, README, JS tests) and 120 sample vectors. The agent writes the Python
package ``chronorule/`` with the same behaviour (names converted by the rules
in the README). The hidden reference is a Python port kept in
``_data_t08/ref/chronorule`` that was cross-checked against the JS library on
random vectors with node; ``check`` runs ~1000 hidden vectors generated from
it against the agent's package.
"""

from __future__ import annotations

import copy
import functools
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

from .common import long_task, read_text, require_share, rng, run_python, write, write_json

TASK_ID = "long_08_port_js_library"
DATA = Path(__file__).parent / "_data_t08"
REF_DIR = DATA / "ref" / "chronorule"
JS_DIR = DATA / "js"
N_HIDDEN = 1000
MIN_SHARE = 0.98

# --------------------------------------------------------------------------
# Reference package (loaded under a private name so it never clashes with a
# `chronorule` on sys.path)
# --------------------------------------------------------------------------

_REF_NAME = "_hb_t08_ref_chronorule"


@functools.cache
def ref():
    spec = importlib.util.spec_from_file_location(
        _REF_NAME, REF_DIR / "__init__.py", submodule_search_locations=[str(REF_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[_REF_NAME] = module
    spec.loader.exec_module(module)
    return module


def expected(vec: dict) -> dict:
    """Run one vector against the reference: {"result": value} or {"error": code}."""
    mod = ref()
    fn = getattr(mod, vec["fn"])
    try:
        value = fn(*copy.deepcopy(vec["args"]), **copy.deepcopy(vec.get("kwargs", {})))
    except mod.ChronoError as exc:
        return {"error": exc.code}
    return {"result": value}


# --------------------------------------------------------------------------
# Vector generation
# --------------------------------------------------------------------------

WD = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
BAD_DATES = (
    "2023-02-29", "2024-13-01", "2024-00-10", "2024-04-31", "2024-5-1", "2024/05/01",
    "0999-12-31", "20240501", " 2024-05-01", "", 20240501, None, "2024-05-01T00:00",
)
CONVENTIONS = ("none", "following", "preceding", "modified_following", "modified_preceding", "nearest")


def _dim(y, m):
    if m == 2:
        return 29 if (y % 4 == 0 and y % 100 != 0) or y % 400 == 0 else 28
    return 30 if m in (4, 6, 9, 11) else 31


def _iso(y, m, d):
    return f"{y:04d}-{m:02d}-{d:02d}"


def _ordinal(y, m, d):
    import datetime

    return datetime.date(y, m, d).toordinal()


def _from_ordinal(n):
    import datetime

    x = datetime.date.fromordinal(n)
    return _iso(x.year, x.month, x.day)


def _shift(date, days):
    y, m, d = map(int, date.split("-"))
    return _from_ordinal(_ordinal(y, m, d) + days)


class Gen:
    def __init__(self, R):
        self.R = R

    # ---- scalars ---------------------------------------------------------
    def year(self):
        R = self.R
        return R.choice([R.randint(1995, 2035), R.randint(1995, 2035), R.choice([2000, 2020, 2024, 2100, 1900])])

    def ymd(self):
        R = self.R
        y = R.randint(1992, 2034)
        m = R.randint(1, 12)
        x = R.random()
        if x < 0.35:
            d = _dim(y, m) - R.randint(0, 3)
        elif x < 0.45:
            y = R.choice([1996, 2000, 2004, 2016, 2020, 2024, 2028])
            m, d = 2, 29
        elif x < 0.55:
            m = R.choice([1, 12])
            d = R.choice([1, 2, 3, 28, 29, 30, 31])
        else:
            d = R.randint(1, _dim(y, m))
        return y, m, d

    def date(self, bad=0.03):
        R = self.R
        if R.random() < bad:
            return R.choice(BAD_DATES)
        return _iso(*self.ymd())

    def near(self, date, span=40):
        if not _is_valid(date):
            return self.date(0)
        return _shift(date, self.R.randint(-span, span))

    def weekday(self, bad=0.03):
        if self.R.random() < bad:
            return self.R.choice(["mo", "XX", "", 0])
        return self.R.choice(WD)

    def small_int(self, lo=-40, hi=40):
        R = self.R
        return R.choice([R.randint(lo, hi), R.randint(-3, 3), R.choice([0, 1, -1, 12, -12, 13, 24, 60])])

    # ---- calendars -------------------------------------------------------
    def calendar(self, around=None, allow_none=True):
        R = self.R
        if allow_none and R.random() < 0.15:
            return None
        base = around if isinstance(around, str) and len(around) == 10 and around[4] == "-" else self.date(0)
        try:
            _shift(base, 0)
        except ValueError:
            base = self.date(0)
        spec = {}
        if R.random() < 0.85:
            spec["holidays"] = sorted({_shift(base, R.randint(-25, 25)) for _ in range(R.randint(1, 7))})
        if R.random() < 0.5:
            spec["workdays"] = sorted({_shift(base, R.randint(-25, 25)) for _ in range(R.randint(1, 3))})
        if R.random() < 0.25:
            spec["weekend"] = R.choice([["SA", "SU"], ["SU"], ["FR", "SA"], ["SU", "SA", "SU"], [], ["TH", "FR", "SA"]])
        if R.random() < 0.2:
            spec["day_hours"] = R.choice([8, 7, 6, 12, 4])
        if R.random() < 0.2:
            spec["preholiday_cut"] = R.choice([0, 1, 2, 3])
        if R.random() < 0.04:
            spec[R.choice(["holiday", "day_hour", "weekends"])] = R.choice([[], 8, ["SA"]])
        if R.random() < 0.02:
            spec["weekend"] = list(WD)
        if R.random() < 0.02:
            spec["day_hours"] = R.choice([0, 25, "8"])
        return spec

    # ---- durations -------------------------------------------------------
    def duration(self):
        R = self.R
        x = R.random()
        if x < 0.55:
            parts = ""
            for letter, top in (("Y", 3), ("M", 25), ("W", 5), ("D", 40)):
                if R.random() < 0.4:
                    parts += f"{R.randint(0, top)}{letter}"
            if not parts:
                parts = f"{R.randint(1, 14)}M"
            sign = R.choice(["", "", "-", "+"])
            text = f"{sign}P{parts}"
            if R.random() < 0.1:
                text = f" {text} "
            return text
        if x < 0.85:
            obj = {}
            for key, top in (("years", 3), ("months", 25), ("weeks", 5), ("days", 40)):
                if R.random() < 0.5:
                    obj[key] = R.randint(0, top)
            if R.random() < 0.4:
                obj["sign"] = R.choice([1, -1])
            return obj
        return R.choice([
            "PT1H", "P", "1M", "P-1M", "p1m", "P1D2M", "P1.5M", "-P", "P123456D",
            {"sign": 0, "months": 1}, {"months": -1}, {"month": 1}, {"days": "3"}, 5,
        ])

    # ---- period labels ---------------------------------------------------
    def label(self):
        R = self.R
        y = R.randint(1998, 2032)
        kind = R.choice(["year", "half", "quarter", "month", "week", "week", "fiscal", "fiscal", "bad"])
        if kind == "year":
            return str(y)
        if kind == "half":
            return f"{y}-H{R.choice([1, 2, 2, 3])}"
        if kind == "quarter":
            return f"{y}-Q{R.choice([1, 2, 3, 4, 4, 5, 0])}"
        if kind == "month":
            return f"{y}-{R.choice([1, 2, 6, 12, 12, 13, 0]):02d}"
        if kind == "week":
            return f"{y}-W{R.choice([1, 1, 2, 26, 52, 52, 53, 53, 54, 0]):02d}"
        if kind == "fiscal":
            return f"FY{y}"
        return R.choice(["2024-q3", "2024Q3", "FY24", "2024-W5", "0999", "2024-M05", " 2024", "fy2024"])

    def fiscal(self):
        R = self.R
        return R.choice([1, 1, 4, 7, 7, 10, 12, 2, 0, 13])

    # ---- recurrence rules ------------------------------------------------
    def rule(self, bounded=None):
        """Rule text (sometimes an object); `bounded`: force COUNT/UNTIL presence."""
        R = self.R
        if R.random() < 0.04:
            return R.choice([
                "FREQ=HOURLY", "INTERVAL=2", "FREQ=DAILY;COUNT=0", "FREQ=WEEKLY;BYMONTHDAY=1",
                "FREQ=DAILY;BYDAY=1MO", "FREQ=MONTHLY;BYDAY=6FR", "FREQ=MONTHLY;BYMONTHDAY=0",
                "FREQ=DAILY;COUNT=3;UNTIL=20250101", "FREQ=MONTHLY;BYSETPOS=1", "FREQ = DAILY",
                "FREQ=MONTHLY;BYDAY=-1FR;BYMONTHDAY=13", "FREQ=YEARLY;UNTIL=20230230",
                "FREQ=DAILY;FREQ=WEEKLY", "FREQ=WEEKLY;WKST=XX", "FREQ=DAILY;INTERVAL=+2", "",
            ])
        freq = R.choice(["DAILY", "WEEKLY", "WEEKLY", "MONTHLY", "MONTHLY", "MONTHLY", "YEARLY", "YEARLY"])
        parts = [f"FREQ={freq}"]
        interval = R.choice([1, 1, 1, 2, 2, 3, 5, 21, 12, 22])
        if interval != 1 or R.random() < 0.15:
            parts.append(f"INTERVAL={interval}")
        bymonth = []
        if R.random() < (0.35 if freq == "YEARLY" else 0.12):
            bymonth = R.sample(range(1, 13), R.randint(1, 3))
            parts.append("BYMONTH=" + ",".join(map(str, bymonth)))
        has_monthday = False
        if freq != "WEEKLY" and R.random() < 0.35:
            has_monthday = True
            vals = R.sample([1, 2, 13, 15, 28, 29, 30, 31, -1, -2, -3, -31], R.randint(1, 2))
            parts.append("BYMONTHDAY=" + ",".join(map(str, vals)))
        has_byday = False
        if freq == "WEEKLY":
            if R.random() < 0.75:
                has_byday = True
                parts.append("BYDAY=" + ",".join(R.sample(WD, R.randint(1, 3))))
        elif R.random() < (0.3 if has_monthday else 0.55):
            has_byday = True
            items = []
            for _ in range(R.randint(1, 2)):
                w = R.choice(WD)
                if freq in ("MONTHLY", "YEARLY") and not has_monthday and R.random() < 0.7:
                    if freq == "YEARLY" and not bymonth and R.random() < 0.4:
                        n = R.choice([1, 2, 10, 20, 52, 53, -1, -2, -3, -10])
                    else:
                        n = R.choice([1, 2, 3, 4, 5, -1, -1, -2, -3])
                    items.append(f"{R.choice(['', '', '+']) if n > 0 else ''}{n}{w}")
                else:
                    items.append(w)
            parts.append("BYDAY=" + ",".join(items))
        if (has_byday or has_monthday or bymonth) and R.random() < 0.15:
            parts.append("BYSETPOS=" + ",".join(map(str, R.sample([1, 2, -1, -2, 3], R.randint(1, 2)))))
        if freq == "WEEKLY" and R.random() < 0.2:
            parts.append("WKST=" + R.choice(["SU", "SA", "WE"]))
        bound = bounded if bounded is not None else R.random() < 0.7
        if bound:
            if R.random() < 0.6:
                parts.append(f"COUNT={R.choice([1, 2, 3, 4, 5, 6, 8, 10, 12, 21])}")
            else:
                y, m, d = self.ymd()
                y = R.randint(2024, 2027)
                d = min(d, _dim(y, m))
                parts.append(f"UNTIL={y:04d}{m:02d}{d:02d}" if R.random() < 0.7 else f"UNTIL={_iso(y, m, d)}")
        R.shuffle(parts)
        text = ";".join(parts)
        x = R.random()
        if x < 0.1:
            text = text.lower()
        elif x < 0.15:
            text = "RRULE:" + text
        elif x < 0.2:
            text = text + ";"
        if R.random() < 0.08:
            return self._rule_obj(text)
        return text

    def _rule_obj(self, text):
        try:
            obj = ref().parse_rule(text)
        except ref().ChronoError:
            return text
        if self.R.random() < 0.5:
            obj = {k: v for k, v in obj.items() if v not in (None, [], 1) or k == "freq"}
        return obj

    def start(self):
        R = self.R
        y = R.randint(2022, 2025)
        m = R.randint(1, 12)
        d = R.choice([1, 1, 15, 28, 29, 30, 31, R.randint(1, 28)])
        return _iso(y, m, min(d, _dim(y, m)))


# --------------------------------------------------------------------------
# Per-function argument generators: name -> (weight, fn(g) -> (args, kwargs))
# --------------------------------------------------------------------------


def _week_kw(g):
    return {"week_start": g.R.choice(["MO", "SU", "SA", "WE"])} if g.R.random() < 0.5 else {}


def _fs_kw(g):
    return {"fiscal_start": g.fiscal()} if g.R.random() < 0.6 else {}


def _rng_pair(g, span=45):
    a = g.date()
    b = g.near(a, span) if isinstance(a, str) and len(a) == 10 and g.is_valid(a) else g.date()
    return a, b


def _is_valid(date):
    try:
        y, m, d = map(int, date.split("-"))
        return len(date) == 10 and y >= 1000 and 1 <= m <= 12 and 1 <= d <= _dim(y, m)
    except (ValueError, AttributeError):
        return False


Gen.is_valid = staticmethod(_is_valid)


def _parse_text(g):
    R = g.R
    y, m, d = g.ymd()
    x = R.random()
    if x < 0.12:
        s = _iso(y, m, d)
    elif x < 0.2:
        s = f"{y:04d}{m:02d}{d:02d}"
    elif x < 0.42:
        yy = R.choice([str(y), f"{y % 100:02d}", f"{R.choice([0, 12, 49, 50, 51, 99]):02d}"])
        s = f"{R.choice([str(d), f'{d:02d}'])}.{R.choice([str(m), f'{m:02d}'])}.{yy}"
    elif x < 0.5:
        s = f"{y}/{m}/{d}"
    elif x < 0.62:
        s = f"{d}/{m}/{y}" if R.random() < 0.5 else f"{m}/{d}/{y}"
    elif x < 0.88:
        from_table = R.choice([
            ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
             "октября", "ноября", "декабря"],
            ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
             "октябрь", "ноябрь", "декабрь"],
            ["янв", "фев", "мар.", "апр", "мая", "июн", "июл", "авг.", "сент.", "окт", "нояб", "дек"],
        ])
        word = from_table[m - 1]
        if R.random() < 0.2:
            word = word.upper()
        s = f"{d} {word} {y}" + R.choice(["", "", " г.", "г", " г", "  г."])
        if R.random() < 0.1:
            s = s.replace(" ", "  ", 1)
    else:
        s = R.choice([
            "31.02.2024", "2024-3-5", "5 мартобря 2024", "5 march 2024", "1.1.124", "2024-02-30",
            "29.02.23", "29.02.24", "30/02/2024", "13/13/2024", "5 марта 24", "5\tмарта 2024",
            "00.01.2024", "5 Марта 2024 Г.", "05.03.2024 г.", "2024.03.05",
        ])
    if R.random() < 0.15:
        s = R.choice([" ", "\t", "  "]) + s + R.choice(["", " ", "\n"])
    kw = {"day_first": R.random() < 0.5} if "/" in s and R.random() < 0.7 else {}
    return [s], kw


def _d(g):
    return g.date()


def _cal_args(g, date):
    return g.calendar(date)


def _sched_spec(g):
    R = g.R
    start = g.start()
    spec = {"rule": g.rule(bounded=R.random() < 0.5), "start": start}
    if R.random() < 0.5:
        spec["exdates"] = [_shift(start, R.randint(0, 120)) for _ in range(R.randint(1, 4))]
    if R.random() < 0.4:
        spec["rdates"] = [_shift(start, R.randint(-20, 150)) for _ in range(R.randint(1, 3))]
    if R.random() < 0.8:
        spec["roll"] = R.choice(CONVENTIONS + ("modified_following", "following", "preceding"))
    if R.random() < 0.7:
        spec["calendar"] = g.calendar(_shift(start, R.randint(0, 90)), allow_none=False)
    if R.random() < 0.03:
        spec["exdate"] = []
    return spec, start


def _expand(g):
    R = g.R
    start = g.start()
    rule = g.rule()
    kw = {}
    if R.random() < 0.3:
        kw["after"] = _shift(start, R.randint(-10, 200))
    if R.random() < 0.6:
        kw["before"] = _shift(start, R.randint(20, 900))
    if R.random() < 0.5 or not kw:
        kw["limit"] = R.choice([1, 3, 5, 8, 12, 0])
    if R.random() < 0.15:
        kw["inclusive"] = False
    return [rule, start], kw


def _between(g):
    spec, start = _sched_spec(g)
    a = _shift(start, g.R.randint(-10, 60))
    return [spec, a, _shift(a, g.R.randint(-5, 400))], {}


def _take(g):
    spec, start = _sched_spec(g)
    kw = {"from_": _shift(start, g.R.randint(-10, 200))} if g.R.random() < 0.5 else {}
    return [spec, g.R.choice([1, 3, 5, 8, 12, 0])], kw


def _next(g):
    spec, start = _sched_spec(g)
    return [spec, _shift(start, g.R.randint(-10, 300))], {}


def _roll_many(g):
    R = g.R
    base = g.date(0)
    dates = [g.near(base, 12) for _ in range(R.randint(1, 6))]
    kw = {"dedupe": R.random() < 0.6} if R.random() < 0.5 else {}
    return [dates, R.choice(CONVENTIONS), g.calendar(base, allow_none=False)], kw


def _conv(g):
    return g.R.choice(CONVENTIONS + ("modified_following", "modified_preceding", "nearest", "Following", "mod_following"))


def _bd_between(g):
    a = g.date()
    b = g.near(a, 30) if _is_valid(a) else g.date()
    return [a, b, g.calendar(a)], {}


def _dates_list(g):
    base = g.date(0)
    out = [g.near(base, 400) for _ in range(g.R.randint(0, 6))]
    if g.R.random() < 0.05:
        out.append("2024-02-30")
    return out


FUNCS = {
    # civil
    "is_leap_year": (3, lambda g: ([g.year()], {})),
    "days_in_month": (3, lambda g: ([g.year(), g.R.choice([1, 2, 2, 4, 12, 13, 0])], {})),
    "days_in_year": (2, lambda g: ([g.year()], {})),
    "is_valid_date": (4, lambda g: ([g.date(bad=0.5)], {})),
    "make_date": (4, lambda g: ([g.year(), g.R.randint(0, 13), g.R.randint(0, 32)], {})),
    "date_parts": (2, lambda g: ([g.date()], {})),
    "weekday": (5, lambda g: ([g.date()], {})),
    "weekday_code": (4, lambda g: ([g.date()], {})),
    "day_of_year": (3, lambda g: ([g.date()], {})),
    "add_days": (4, lambda g: ([g.date(), g.small_int(-400, 400)], {})),
    "add_months": (22, lambda g: ([g.date(), g.small_int(-30, 30)], {})),
    "add_months_clamped": (8, lambda g: ([g.date(), g.small_int(-30, 30)], {})),
    "add_years": (10, lambda g: ([g.date(), g.small_int(-8, 8)], {})),
    "diff_days": (3, lambda g: (list(_rng_pair(g, 400)), {})),
    "compare_dates": (2, lambda g: (list(_rng_pair(g, 3)), {})),
    "start_of_month": (2, lambda g: ([g.date()], {})),
    "end_of_month": (3, lambda g: ([g.date()], {})),
    "start_of_quarter": (2, lambda g: ([g.date()], {})),
    "end_of_quarter": (2, lambda g: ([g.date()], {})),
    "start_of_year": (1, lambda g: ([g.date()], {})),
    "end_of_year": (1, lambda g: ([g.date()], {})),
    "start_of_week": (6, lambda g: ([g.date()], _week_kw(g))),
    "end_of_week": (5, lambda g: ([g.date()], _week_kw(g))),
    "clamp_date": (3, lambda g: ([g.date(), g.date(), g.date()], {})),
    "min_date": (2, lambda g: ([_dates_list(g)], {})),
    "max_date": (2, lambda g: ([_dates_list(g)], {})),
    "sort_dates": (4, lambda g: ([_dates_list(g) + ["2024-01-01"] * g.R.randint(0, 2)],
                                 {k: g.R.random() < 0.5 for k in g.R.sample(["descending", "unique"], g.R.randint(0, 2))})),
    "each_day": (4, lambda g: (list(_rng_pair(g, 12)), {"step": g.R.choice([1, 2, 3, 0])} if g.R.random() < 0.5 else {})),
    "is_same_month": (2, lambda g: (list(_rng_pair(g, 5)), {})),
    "is_weekend": (4, lambda g: ([g.date()], {"weekend": g.R.choice([["SU"], ["FR", "SA"], [], ["su"]])} if g.R.random() < 0.5 else {})),
    "to_compact": (2, lambda g: ([g.date()], {})),
    # parse
    "parse_date": (30, _parse_text),
    "try_parse_date": (6, _parse_text),
    # weeks
    "iso_week": (10, lambda g: ([g.date()], {})),
    "iso_weeks_in_year": (3, lambda g: ([g.year()], {})),
    "iso_week_start": (5, lambda g: ([g.year(), g.R.choice([1, 1, 2, 26, 52, 53, 53, 0, 54])], {})),
    "iso_week_label": (6, lambda g: ([g.date()], {})),
    "week_of_month": (9, lambda g: ([g.date()], _week_kw(g))),
    "week_of_year": (5, lambda g: ([g.date()], _week_kw(g))),
    "weeks_in_month": (4, lambda g: ([g.year(), g.R.randint(1, 12)], _week_kw(g))),
    # nth
    "nth_weekday_of_month": (12, lambda g: ([g.year(), g.R.randint(1, 12), g.weekday(),
                                             g.R.choice([1, 2, 3, 4, 5, 5, -1, -2, -5, 0, 6])], {})),
    "last_weekday_of_month": (4, lambda g: ([g.year(), g.R.randint(1, 12), g.weekday()], {})),
    "weekday_occurrence": (6, lambda g: ([g.date()], {})),
    "next_weekday": (5, lambda g: ([g.date(), g.weekday()], {"inclusive": g.R.random() < 0.5} if g.R.random() < 0.6 else {})),
    "prev_weekday": (5, lambda g: ([g.date(), g.weekday()], {"inclusive": g.R.random() < 0.5} if g.R.random() < 0.6 else {})),
    "weekdays_in_month": (3, lambda g: ([g.year(), g.R.randint(1, 12), g.weekday()], {})),
    "is_last_weekday_of_month": (3, lambda g: ([g.date()], {})),
    # calendar
    "make_calendar": (8, lambda g: ([g.calendar()], {})),
    "day_kind": (12, lambda g: (lambda d: ([d, g.calendar(d)], {}))(g.date())),
    "is_workday": (6, lambda g: (lambda d: ([d, g.calendar(d)], {}))(g.date())),
    "is_holiday": (3, lambda g: (lambda d: ([d, g.calendar(d)], {}))(g.date())),
    "work_hours": (6, lambda g: (lambda d: ([d, g.calendar(d)], {}))(g.date())),
    "day_info": (8, lambda g: (lambda d: ([d, g.calendar(d)], {}))(g.date())),
    "holidays_between": (3, lambda g: (lambda d: ([d, g.near(d, 30), g.calendar(d)], {}))(g.date(0))),
    "merge_calendars": (4, lambda g: (lambda d: ([g.calendar(d), g.calendar(d, allow_none=g.R.random() < 0.1)], {}))(g.date(0))),
    # business
    "add_business_days": (22, lambda g: (lambda d: ([d, g.R.choice([0, 0, 1, 1, 2, 3, 5, 10, -1, -2, -5, 22]), g.calendar(d)], {}))(g.date())),
    "business_days_between": (22, _bd_between),
    "next_business_day": (4, lambda g: (lambda d: ([d, g.calendar(d)], {}))(g.date())),
    "prev_business_day": (4, lambda g: (lambda d: ([d, g.calendar(d)], {}))(g.date())),
    "nth_business_day": (6, lambda g: (lambda d: ([int(d[:4]), int(d[5:7]), g.R.choice([1, 2, 3, 5, 20, 23, -1, -2, 0]), g.calendar(d)], {}))(g.date(0))),
    "last_business_day": (3, lambda g: (lambda d: ([int(d[:4]), int(d[5:7]), g.calendar(d)], {}))(g.date(0))),
    "business_days_in_month": (4, lambda g: (lambda d: ([int(d[:4]), int(d[5:7]), g.calendar(d)], {}))(g.date(0))),
    "work_hours_in_month": (5, lambda g: (lambda d: ([int(d[:4]), int(d[5:7]), g.calendar(d)], {}))(g.date(0))),
    "work_hours_between": (6, _bd_between),
    "business_days_list": (4, lambda g: (lambda d: ([d, g.near(d, 15), g.calendar(d)], {}))(g.date(0))),
    # roll
    "roll": (22, lambda g: (lambda d: ([d, _conv(g), g.calendar(d)], {}))(g.date())),
    "roll_many": (6, _roll_many),
    "is_rolled": (3, lambda g: (lambda d: ([d, _conv(g), g.calendar(d)], {}))(g.date())),
    # duration
    "parse_duration": (8, lambda g: ([g.duration() if g.R.random() < 0.8 else g.R.choice(["P1Y2M3W4D", "-P0D", "+P7W"])], {})),
    "format_duration": (8, lambda g: ([g.duration()], {})),
    "add_duration": (20, lambda g: ([g.date(), g.duration()], {})),
    "subtract_duration": (8, lambda g: ([g.date(), g.duration()], {})),
    "negate_duration": (3, lambda g: ([g.duration()], {})),
    "duration_days": (5, lambda g: ([g.duration(), g.date()], {})),
    "diff_dates": (12, lambda g: (list(_rng_pair(g, 800)), {})),
    "months_between": (5, lambda g: (list(_rng_pair(g, 800)), {})),
    "age_on": (5, lambda g: ([g.date(), g.date()], {})),
    "end_of_month_after": (3, lambda g: ([g.date(), g.small_int(-14, 14)], {})),
    # period
    "quarter_of": (2, lambda g: ([g.date()], {})),
    "half_of": (2, lambda g: ([g.date()], {})),
    "fiscal_year": (6, lambda g: ([g.date(), g.fiscal()], {})),
    "period_of": (12, lambda g: ([g.date(), g.R.choice(["year", "half", "quarter", "month", "week", "week", "fiscal", "fiscal", "day"])], _fs_kw(g))),
    "period_range": (12, lambda g: ([g.label()], _fs_kw(g))),
    "period_kind": (3, lambda g: ([g.label()], {})),
    "shift_period": (10, lambda g: ([g.label(), g.small_int(-30, 30)], _fs_kw(g))),
    "periods_between": (6, lambda g: (list(_rng_pair(g, 120)) + [g.R.choice(["month", "week", "quarter", "fiscal", "half"])], _fs_kw(g))),
    "period_contains": (5, lambda g: ([g.label(), g.date()], _fs_kw(g))),
    "days_in_period": (4, lambda g: ([g.label()], _fs_kw(g))),
    # rrule
    "parse_rule": (12, lambda g: ([g.rule() if g.R.random() < 0.95 else g.R.choice([5, None])], {})),
    "rule_to_string": (10, lambda g: ([g.rule()], {})),
    "expand_rule": (45, _expand),
    "next_occurrence": (10, lambda g: (lambda s: ([g.rule(), s, _shift(s, g.R.randint(-20, 400))], {}))(g.start())),
    "occurs_on": (8, lambda g: (lambda s: ([g.rule(), s, _shift(s, g.R.randint(0, 200))], {}))(g.start())),
    "count_occurrences": (6, lambda g: (lambda s: ([g.rule(), s], {"before": _shift(s, g.R.randint(0, 500))} if g.R.random() < 0.6 else {}))(g.start())),
    # schedule
    "schedule_between": (22, _between),
    "schedule_take": (16, _take),
    "schedule_next": (8, _next),
    # format
    "format_date": (20, lambda g: ([g.date(), g.R.choice([
        "D MMMM YYYY [г.]", "DD.MM.YYYY", "dd, D MMM", "dddd", "LLLL YYYY", "YY-MM-DD", "GGGG-[W]WW-E",
        "Q кв. YYYY", "DDDD", "DDD", "D.M.YY", "[Сегодня] dddd", "W/WW", "MMMMM", "YYYYY", "[не закрыто",
        "ddd D", "Неделя W, E-й день", "M/D", "LLLL, [квартал] Q", "dd MMM YYYY", "Y m d",
    ])], {})),
    "format_range": (8, lambda g: (lambda a: ([a, _shift(a, g.R.randint(-3, 400)) if _is_valid(a) else g.date()], {}))(g.date())),
    "plural_ru": (5, lambda g: ([g.R.choice([0, 1, 2, 5, 11, 12, 14, 21, 22, 25, 101, 111, 112, 1001, -1, -22]), "день", "дня", "дней"], {})),
    "month_name": (4, lambda g: ([g.R.randint(0, 13)], {"form": g.R.choice(["nominative", "genitive", "prepositional", "short", "dative"])} if g.R.random() < 0.8 else {})),
    "weekday_name": (4, lambda g: ([g.weekday()], {"form": g.R.choice(["full", "short", "accusative", "dative_plural", "dativePlural"])} if g.R.random() < 0.8 else {})),
    # humanize
    "describe_rule": (40, lambda g: ([g.rule(), g.start()], {})),
    "describe_duration": (8, lambda g: ([g.duration()], {})),
    "describe_period": (8, lambda g: ([g.label()], _fs_kw(g))),
    "describe_date": (3, lambda g: ([g.date()], {})),
    "describe_relative": (8, lambda g: (lambda d: ([g.near(d, 90), d], {}))(g.date(0))),
    "describe_day_range": (3, lambda g: (lambda d: ([d, _shift(d, g.R.randint(-2, 40))], {}))(g.date(0))),
}


# --------------------------------------------------------------------------
# Edge families: each one concentrates on a documented quirk, so that a port
# that misses any single quirk loses well over 2 % of the hidden vectors.
# Each returns (fn, args, kwargs).
# --------------------------------------------------------------------------


def _month_end(g, days=(29, 30, 31)):
    R = g.R
    y = R.randint(1995, 2033)
    m = R.choice([1, 3, 5, 7, 8, 10, 12, 1, 3, 12])
    return _iso(y, m, R.choice(days))


def e_overflow(g):
    R = g.R
    x = R.random()
    if x < 0.45:
        return "add_months", [_month_end(g), R.choice([1, 1, 3, 13, -1, -3, 5, 8, -10, 25])], {}
    if x < 0.6:
        return "add_years", [_iso(R.choice([1996, 2000, 2012, 2020, 2024]), 2, 29), R.choice([1, 2, 3, -1, 5])], {}
    months = R.choice([1, 3, 13, 1, 1])
    dur = R.choice([f"P{months}M", f"P{months}M{R.randint(1, 9)}D", f"-P{months}M", {"months": months, "days": R.randint(0, 3)}])
    fn = R.choice(["add_duration", "add_duration", "subtract_duration", "duration_days"])
    date = _month_end(g)
    return fn, ([dur, date] if fn == "duration_days" else [date, dur]), {}


_DTSTART_RULES = (
    ("FREQ=MONTHLY;BYDAY=-1FR;COUNT={c}", 1),
    ("FREQ=MONTHLY;BYDAY=2TU;COUNT={c}", 20),
    ("FREQ=WEEKLY;BYDAY=MO,TH;COUNT={c}", None),
    ("FREQ=MONTHLY;BYMONTHDAY=15;COUNT={c}", 3),
    ("FREQ=YEARLY;BYMONTH=6;BYMONTHDAY=1;COUNT={c}", 10),
    ("FREQ=WEEKLY;INTERVAL=2;BYDAY=FR;COUNT={c}", None),
    ("FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT={c}", 2),
)


def e_dtstart(g):
    R = g.R
    tpl, day = R.choice(_DTSTART_RULES)
    rule = tpl.format(c=R.choice([2, 3, 4, 5]))
    y, m = R.randint(2023, 2025), R.randint(1, 12)
    start = _iso(y, m, day if day else R.choice([3, 10, 17, 24]))
    x = R.random()
    if x < 0.45:
        return "expand_rule", [rule, start], {}
    if x < 0.6:
        return "count_occurrences", [rule, start], {}
    if x < 0.75:
        return "next_occurrence", [rule, start, _shift(start, -1)], {}
    return "schedule_take", [{"rule": rule, "start": start}, 3], {}


def e_bdays(g):
    R = g.R
    y, m = R.randint(2020, 2030), R.randint(1, 12)
    a = _iso(y, m, R.randint(1, 28))
    b = _shift(a, R.choice([1, 2, 3, 4, 5, 6, 8, 11, -1, -2, -4, -9]))
    return "business_days_between", [a, b, g.calendar(a) if R.random() < 0.4 else None], {}


def e_plural(g):
    R = g.R
    n = R.choice([21, 22, 23, 24, 31, 32, 33, 34, 41, 42, 101, 102, 121, 122, 1001, 1002])
    x = R.random()
    if x < 0.3:
        unit = R.choice(["years", "months", "weeks", "days"])
        return "describe_duration", [{unit: n, R.choice(["days", "months"]): R.choice([1, 2, 5, 22])}], {}
    if x < 0.5:
        base = g.date(0)
        return "describe_relative", [_shift(base, R.choice([n, -n]) if n < 200 else 22), base], {}
    if x < 0.8:
        freq = R.choice(["DAILY", "WEEKLY", "MONTHLY", "YEARLY"])
        rule = f"FREQ={freq};INTERVAL={min(n, 1000)}" if R.random() < 0.6 else f"FREQ={freq};COUNT={min(n, 10000)}"
        return "describe_rule", [rule, g.start()], {}
    return "plural_ru", [R.choice([n, -n]), R.choice(["год", "неделя", "раз"]), R.choice(["года", "недели", "раза"]), R.choice(["лет", "недель", "раз"])], {}


def e_modified(g):
    R = g.R
    conv = R.choice(["modified_following", "modified_preceding"])
    plain = "following" if conv == "modified_following" else "preceding"
    for _ in range(40):
        y, m = R.randint(1995, 2033), R.randint(1, 12)
        days = range(_dim(y, m) - 2, _dim(y, m) + 1) if plain == "following" else range(1, 4)
        cands = [_iso(y, m, d) for d in days if ref().weekday(_iso(y, m, d)) >= 5]
        cands = [c for c in cands if ref().roll(c, plain)[5:7] != c[5:7]]
        if cands:
            break
    date = R.choice(cands) if cands else _iso(y, m, 1)
    if R.random() < 0.25:
        return "roll_many", [[date, _shift(date, R.choice([-7, 7]))], conv, None], {}
    return R.choice(["roll", "roll", "is_rolled"]), [date, conv, None], {}


def e_exdates(g):
    R = g.R
    y, m = R.randint(2023, 2025), R.randint(1, 12)
    start = _iso(y, m, 1)
    rule = R.choice(["FREQ=WEEKLY;BYDAY=SA", "FREQ=WEEKLY;BYDAY=SU", "FREQ=WEEKLY;BYDAY=SA,SU", "FREQ=MONTHLY;BYDAY=1SA,-1SU"])
    occ = ref().expand_rule(rule, start, limit=8)
    conv = R.choice(["following", "preceding", "modified_following"])
    rolled = [ref().roll(d, conv, None) for d in occ]
    pick = R.sample(range(len(occ)), 2)
    ex = [rolled[pick[0]], occ[pick[1]]]
    spec = {"rule": rule + (f";COUNT={R.choice([4, 5, 6])}" if R.random() < 0.5 else ""), "start": start, "roll": conv, "exdates": ex}
    if R.random() < 0.5:
        return "schedule_between", [spec, start, _shift(start, R.choice([40, 70, 250]))], {}
    return "schedule_take", [spec, R.choice([3, 4, 6])], {}


def e_isoweek(g):
    R = g.R
    y = R.randint(1995, 2033)
    date = _shift(_iso(y, 12, 28), R.randint(0, 8))
    fn = R.choice(["iso_week", "iso_week_label", "period_of", "format_date", "iso_week"])
    if fn == "period_of":
        return fn, [date, "week"], {}
    if fn == "format_date":
        return fn, [date, R.choice(["GGGG-[W]WW", "YYYY/GGGG W", "GGGG-WW-E"])], {}
    return fn, [date], {}


def e_weekday(g):
    R = g.R
    date = g.date(0)
    x = R.random()
    if x < 0.35:
        return "weekday", [date], {}
    if x < 0.7:
        return "week_of_month", [date], ({"week_start": R.choice(["SU", "SA"])} if R.random() < 0.5 else {})
    return "start_of_week", [date], {}


def e_pivot(g):
    R = g.R
    yy = R.choice(list(range(40, 60)) + [0, 99])
    return R.choice(["parse_date", "parse_date", "try_parse_date"]), [f"{R.randint(1, 28)}.{R.randint(1, 12)}.{yy:02d}"], {}


def e_monthly_skip(g):
    R = g.R
    if R.random() < 0.75:
        start = _month_end(g, (29, 30, 31))
        rule = f"FREQ=MONTHLY;COUNT={R.choice([3, 4, 6])}" + (";INTERVAL=1" if R.random() < 0.3 else "")
    else:
        start = _iso(R.choice([2000, 2016, 2020, 2024]), 2, 29)
        rule = f"FREQ=YEARLY;COUNT={R.choice([2, 3])}"
    if R.random() < 0.8:
        return "expand_rule", [rule, start], {}
    return "next_occurrence", [rule, start, _shift(start, 1)], {}


def e_fiscal(g):
    R = g.R
    fs = R.choice([2, 4, 7, 10, 12])
    x = R.random()
    date = g.date(0)
    if x < 0.3:
        return "fiscal_year", [date, fs], {}
    if x < 0.55:
        return "period_of", [date, "fiscal"], {"fiscal_start": fs}
    if x < 0.8:
        return "period_range", [f"FY{R.randint(2000, 2030)}"], {"fiscal_start": fs}
    return "describe_period", [f"FY{R.randint(2000, 2030)}"], {"fiscal_start": fs}


def e_hours(g):
    R = g.R
    a = g.date(0)
    cal = {"holidays": [_shift(a, R.randint(0, 6)), _shift(a, R.randint(0, 6))]}
    if R.random() < 0.4:
        cal["preholiday_cut"] = R.choice([0, 2])
    x = R.random()
    if x < 0.5:
        return "work_hours_between", [a, _shift(a, R.randint(0, 9)), cal], {}
    if x < 0.8:
        return "work_hours", [_shift(a, R.randint(-1, 6)), cal], {}
    return "day_kind", [_shift(a, R.randint(-1, 6)), cal], {}


def e_nearest(g):
    R = g.R
    a = g.date(0)
    cal = {"holidays": sorted({_shift(a, R.randint(-3, 3)) for _ in range(R.randint(1, 3))})}
    return "roll", [a, "nearest", cal], {}


def e_zero_bdays(g):
    R = g.R
    y, m = R.randint(2000, 2030), R.randint(1, 12)
    d = _iso(y, m, R.randint(1, 28))
    sat = _shift(d, (5 - ref().weekday(d)) % 7 + R.choice([0, 1]))
    return "add_business_days", [sat, R.choice([0, 0, 1, -1, 2, -2]), None], {}


def e_diff(g):
    R = g.R
    a = _month_end(g, (28, 29, 30, 31))
    b = _shift(a, R.randint(20, 800))
    if R.random() < 0.3:
        a, b = b, a
    return R.choice(["diff_dates", "diff_dates", "months_between"]), [a, b], {}


EDGE = (
    (12, e_overflow),
    (7, e_dtstart),
    (8, e_bdays),
    (6, e_plural),
    (6, e_modified),
    (7, e_exdates),
    (4, e_isoweek),
    (3, e_weekday),
    (3, e_pivot),
    (4, e_monthly_skip),
    (4, e_fiscal),
    (4, e_hours),
    (3, e_nearest),
    (3, e_zero_bdays),
    (4, e_diff),
)
EDGE_SHARE = 0.55


def gen_vectors(seed: str, n: int, *, cover_all: bool = False) -> list[dict]:
    R = rng(seed)
    g = Gen(R)
    names = sorted(FUNCS)
    weights = [FUNCS[k][0] for k in names]
    picks = list(names) if cover_all else []
    while len(picks) < n:
        if R.random() < EDGE_SHARE:
            picks.append(R.choices(EDGE, weights=[w for w, _ in EDGE])[0][1])
        else:
            picks.append(R.choices(names, weights=weights)[0])
    out = []
    for i, pick in enumerate(picks, 1):
        if callable(pick):
            name, args, kwargs = pick(g)
        else:
            name = pick
            args, kwargs = FUNCS[name][1](g)
        vec = {"id": f"v{i:04d}", "fn": name, "args": args, "kwargs": kwargs}
        vec["expect"] = expected(vec)
        out.append(vec)
    return out


@functools.cache
def sample_vectors() -> tuple:
    return tuple(gen_vectors(TASK_ID + ":sample", 120, cover_all=True))


@functools.cache
def hidden_vectors() -> tuple:
    return tuple(gen_vectors(TASK_ID + ":hidden", N_HIDDEN, cover_all=True))


# --------------------------------------------------------------------------
# Vector runner (the visible tests/run_vectors.py and the hidden check share it)
# --------------------------------------------------------------------------

RUNNER = r'''"""Прогон векторов против Python-порта chronorule (только стандартная библиотека).

    python3 tests/run_vectors.py                  # tests/vectors_sample.json, печать расхождений
    python3 tests/run_vectors.py other.json       # другой файл векторов того же формата
    python3 tests/run_vectors.py V.json --json OUT --root DIR   # машинный вывод результатов

Каждый вектор: {"id", "fn", "args", "kwargs", "expect"}; expect — {"result": ...}
или {"error": "<код ChronoError>"}. Функция берётся как chronorule.<fn>.
"""

import json
import signal
import sys
from pathlib import Path

TIMEOUT = 2.0


class VectorTimeout(Exception):
    pass


def _on_alarm(signum, frame):
    raise VectorTimeout("vector timed out")


def norm(value):
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        return [norm(x) for x in value]
    if isinstance(value, dict):
        return {str(k): norm(v) for k, v in value.items()}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return {"__unserializable__": repr(value)}


def run_one(mod, vec):
    fn = getattr(mod, vec["fn"], None)
    if not callable(fn):
        return {"missing": vec["fn"]}
    use_alarm = hasattr(signal, "setitimer")
    if use_alarm:
        signal.setitimer(signal.ITIMER_REAL, TIMEOUT)
    try:
        value = fn(*vec["args"], **vec.get("kwargs", {}))
    except Exception as exc:  # noqa: BLE001 — any failure is a result
        code = getattr(exc, "code", None)
        if isinstance(code, str):
            return {"error": code}
        return {"exception": type(exc).__name__ + ": " + str(exc)[:200]}
    finally:
        if use_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
    return {"result": norm(value)}


def same(a, b):
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def main(argv):
    args = list(argv)
    out_path = root = None
    if "--json" in args:
        i = args.index("--json")
        out_path = args[i + 1]
        del args[i:i + 2]
    if "--root" in args:
        i = args.index("--root")
        root = args[i + 1]
        del args[i:i + 2]
    here = Path(__file__).resolve().parent
    vectors_path = Path(args[0]) if args else here / "vectors_sample.json"
    sys.path.insert(0, root or str(here.parent))
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _on_alarm)
    vectors = json.loads(vectors_path.read_text(encoding="utf-8"))
    import chronorule

    results = {}
    for vec in vectors:
        try:
            results[vec["id"]] = run_one(chronorule, vec)
        except VectorTimeout:
            results[vec["id"]] = {"exception": "VectorTimeout"}
    if out_path:
        Path(out_path).write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        return 0
    bad = 0
    for vec in vectors:
        got = results[vec["id"]]
        if "expect" in vec and not same(got, vec["expect"]):
            bad += 1
            call = json.dumps(vec["args"], ensure_ascii=False)
            print(f"{vec['id']} {vec['fn']}{call} {vec.get('kwargs') or ''}")
            print(f"    ожидалось: {json.dumps(vec['expect'], ensure_ascii=False)}")
            print(f"    получено:  {json.dumps(got, ensure_ascii=False)}")
    print(f"совпало {len(vectors) - bad} из {len(vectors)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


# --------------------------------------------------------------------------
# Task
# --------------------------------------------------------------------------

PROMPT = (
    "В корне рабочей папки лежит JavaScript-библиотека chronorule: исходники в src/ (17 модулей"
    " с подробными JSDoc-комментариями), README.md, JS-тесты в test/. Это календарные правила:"
    " арифметика дат, разбор дат, ISO-недели, производственный календарь, рабочие дни и нормы"
    " часов, конвенции переноса, длительности, отчётные периоды, повторения в духе RRULE,"
    " расписания, форматирование и русские подписи.\n\n"
    "Нужно перенести библиотеку на Python: создать в корне пакет chronorule/ (импортируется как"
    " `import chronorule`), который ведёт себя точно так же, как JS-версия. Правила соответствия"
    " имён функций, параметров, ключей объектов, типов и ошибок описаны в разделе «Порт на"
    " Python» файла README.md. Поведение задают код и JSDoc-комментарии модулей, включая все"
    " задокументированные особенности, граничные случаи, порядок проверок и коды ошибок — их"
    " нужно воспроизвести, а не «исправить». Нужны все функции, экспортируемые из src/index.js."
    " Используй только стандартную библиотеку Python (3.9+); node.js в окружении может не быть,"
    " и готовое решение не должно его требовать.\n\n"
    "tests/vectors_sample.json — образец векторов, снятых с JS-версии (вызов → ожидаемый результат"
    " или код ошибки); прогнать их можно командой `python3 tests/run_vectors.py`. Итог проверяется"
    " на большом скрытом наборе векторов того же формата по всем публичным функциям, нужно"
    " совпадение не менее чем в 98 % векторов. Кода много, и важен каждый модуль."
)


def _copy_tree(src: Path, ws: Path, rel: str) -> None:
    for path in sorted(src.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            write(ws, f"{rel}/{path.relative_to(src).as_posix()}" if rel else path.relative_to(src).as_posix(),
                  path.read_text(encoding="utf-8"))


def setup(ws: Path) -> None:
    _copy_tree(JS_DIR, ws, "")
    sample = [
        {"id": v["id"], "fn": v["fn"], "args": v["args"], "kwargs": v["kwargs"], "expect": v["expect"]}
        for v in sample_vectors()
    ]
    write_json(ws, "tests/vectors_sample.json", sample)
    write(ws, "tests/run_vectors.py", RUNNER)


def gold(ws: Path) -> None:
    for path in sorted(REF_DIR.glob("*.py")):
        write(ws, f"chronorule/{path.name}", path.read_text(encoding="utf-8"))


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def check(ws: Path) -> str:
    assert (ws / "chronorule" / "__init__.py").is_file(), "нет пакета chronorule/ (chronorule/__init__.py)"
    vectors = hidden_vectors()
    tmp = Path(tempfile.mkdtemp(prefix="hb_t08_"))
    try:
        runner = write(tmp, "runner.py", RUNNER)
        vec_path = tmp / "vectors.json"
        vec_path.write_text(
            json.dumps([{k: v[k] for k in ("id", "fn", "args", "kwargs")} for v in vectors], ensure_ascii=False),
            encoding="utf-8",
        )
        out = tmp / "out.json"
        proc = run_python(ws, [str(runner), str(vec_path), "--json", str(out), "--root", str(ws)], timeout=115)
        if not out.is_file():
            tail = " | ".join((proc.stdout + proc.stderr).strip().splitlines()[-4:])
            raise AssertionError(f"прогон векторов упал: {tail[:300]}")
        results = json.loads(read_text(tmp, "out.json"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    correct = 0
    errors = []
    for vec in vectors:
        got = results.get(vec["id"])
        if got is not None and _same(got, vec["expect"]):
            correct += 1
        else:
            errors.append(f"{vec['fn']}")
    return require_share(len(vectors), correct, min_share=MIN_SHARE, what="скрытые векторы", errors=errors)


TASK = long_task(
    id="task_399_port_js_library",  # registry id; TASK_ID stays the generator seed
    name="Перенос JS-библиотеки календарных правил на Python",
    prompt=PROMPT,
    setup=setup,
    gold=gold,
    check=check,
    tags=("code", "port", "reading"),
)


# --------------------------------------------------------------------------
# Near misses: plausible ports that miss one documented quirk
# --------------------------------------------------------------------------


def _patch(ws: Path, rel: str, old: str, new: str) -> None:
    path = ws / "chronorule" / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"near miss patch target not found in {rel}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def clamp_instead_of_overflow(ws: Path) -> None:
    """addMonths ported with the 'intuitive' end-of-month clamp."""
    _patch(ws, "civil.py", "return iso(days_from_civil(ny, nm, 1) + d - 1)", "return iso(days_from_civil(ny, nm, last))")


def rfc_dtstart(ws: Path) -> None:
    """Recurrences follow RFC 5545: DTSTART is always the first occurrence."""
    _patch(
        ws,
        "rrule.py",
        "    count = 0\n    for k in range(MAX_PERIODS):",
        "    count = 1\n    yield start\n    if r.count == 1:\n        return\n    for k in range(MAX_PERIODS):",
    )
    _patch(ws, "rrule.py", "            if c < start:\n", "            if c <= start:\n")


def closed_open_business_days(ws: Path) -> None:
    """businessDaysBetween ported as the usual [start, end) count."""
    _patch(ws, "business.py", "range(lo + 1, hi + 1) if cal.working(x)", "range(lo, hi) if cal.working(x)")


def naive_plural(ws: Path) -> None:
    """Russian plural rules reduced to 1 / 2-4 / other (ignores 11-14 and 21, 22...)."""
    _patch(
        ws,
        "locale_ru.py",
        "    n = abs(n)\n    if n % 10 == 1 and n % 100 != 11:",
        "    n = abs(n)\n    if n == 1:\n        return 0\n    if 2 <= n <= 4:\n        return 1\n    return 2\n    if n % 10 == 1 and n % 100 != 11:",
    )


def modified_as_plain(ws: Path) -> None:
    """Modified conventions ported without the month check."""
    _patch(ws, "roll.py", "return f if ymd(f)[1] == month else _step(cal, n, -1)", "return f")
    _patch(ws, "roll.py", "return p if ymd(p)[1] == month else _step(cal, n, 1)", "return p")


def no_humanize(ws: Path) -> None:
    """Port that skipped humanize.js (the Russian descriptions)."""
    (ws / "chronorule" / "humanize.py").unlink()
    _patch(
        ws,
        "__init__.py",
        "from .humanize import (\n    describe_date,\n    describe_day_range,\n    describe_duration,\n"
        "    describe_period,\n    describe_relative,\n    describe_rule,\n)\n",
        "",
    )


def exdates_after_roll(ws: Path) -> None:
    """Schedule EXDATEs compared with rolled dates instead of raw dates."""
    _patch(ws, "schedule.py", "            if x != last and x not in s.exdates:\n                last = x\n                yield x\n        if c != last and c not in s.exdates:",
           "            if x != last:\n                last = x\n                yield x\n        if c != last:")
    _patch(ws, "schedule.py", "        y = roll_days(s.cal, x, s.roll)\n        if y in seen:",
           "        y = roll_days(s.cal, x, s.roll)\n        if y in seen or y in s.exdates:")


NEAR_MISSES = [
    clamp_instead_of_overflow,
    rfc_dtstart,
    closed_open_business_days,
    naive_plural,
    modified_as_plain,
    no_humanize,
    exdates_after_roll,
]
