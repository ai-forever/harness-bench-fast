"""Calendar durations (port of src/duration.js)."""

import re

from ._core import days_from_civil, dim, is_int, iso, need_str, to_days, ymd
from .civil import add_months, add_months_clamped
from .errors import E_PARSE, E_RANGE, E_TYPE, fail

_RE = re.compile(r"([+-])?P(?:([0-9]+)Y)?(?:([0-9]+)M)?(?:([0-9]+)W)?(?:([0-9]+)D)?")
FIELDS = ("years", "months", "weeks", "days")
MAX_COMPONENT = 99999


def parse_duration(text):
    need_str(text, "duration")
    s = text.strip()
    mt = _RE.fullmatch(s)
    if not mt or all(g is None for g in mt.groups()[1:]):
        fail(E_PARSE, "bad duration")
    out = {"sign": -1 if mt.group(1) == "-" else 1}
    for i, name in enumerate(FIELDS):
        g = mt.group(i + 2)
        v = int(g) if g is not None else 0
        if v > MAX_COMPONENT:
            fail(E_RANGE, "duration component too large")
        out[name] = v
    return out


def to_duration(value):
    """Accept a duration string or object and return the normalised object."""
    if isinstance(value, str):
        return parse_duration(value)
    if not isinstance(value, dict):
        fail(E_TYPE, "duration must be a string or an object")
    for key in value:
        if key != "sign" and key not in FIELDS:
            fail(E_TYPE, "unknown duration key")
    sign = value.get("sign")
    sign = 1 if sign is None else sign
    if not is_int(sign):
        fail(E_TYPE, "sign must be an integer")
    if sign not in (1, -1):
        fail(E_RANGE, "sign must be 1 or -1")
    out = {"sign": sign}
    for name in FIELDS:
        v = value.get(name)
        v = 0 if v is None else v
        if not is_int(v):
            fail(E_TYPE, "duration components must be integers")
        if v < 0 or v > MAX_COMPONENT:
            fail(E_RANGE, "duration component out of range")
        out[name] = v
    return out


def format_duration(value):
    dur = to_duration(value)
    body = ""
    for name, letter in zip(FIELDS, "YMWD"):  # noqa: B905 — equal lengths, py3.9
        if dur[name]:
            body += f"{dur[name]}{letter}"
    if not body:
        return "P0D"
    return ("-" if dur["sign"] < 0 else "") + "P" + body


def add_duration(date, value):
    to_days(date)
    dur = to_duration(value)
    months = dur["sign"] * (dur["years"] * 12 + dur["months"])
    days = dur["sign"] * (dur["weeks"] * 7 + dur["days"])
    shifted = add_months(date, months)
    return iso(to_days(shifted) + days)


def subtract_duration(date, value):
    dur = to_duration(value)
    dur["sign"] = -dur["sign"]
    return add_duration(date, dur)


def negate_duration(value):
    dur = to_duration(value)
    dur["sign"] = -dur["sign"]
    return dur


def duration_days(value, start):
    return to_days(add_duration(start, value)) - to_days(start)


def diff_dates(start, end):
    a, b = to_days(start), to_days(end)
    sign = 1
    if b < a:
        a, b, sign = b, a, -1
    ya, ma, da = ymd(a)
    yb, mb, db = ymd(b)
    months = (yb - ya) * 12 + (mb - ma)
    if db < da:
        months -= 1
    anchor = to_days(add_months_clamped(iso(a), months))
    return {"sign": sign, "years": months // 12, "months": months % 12, "days": b - anchor}


def months_between(start, end):
    """Whole months from start to end (negative when end < start), clamped like diff_dates."""
    d = diff_dates(start, end)
    return d["sign"] * (d["years"] * 12 + d["months"])


def age_on(birth, date):
    """Full years; someone born on 29 Feb gets a year older on 1 Mar in common years."""
    a, b = to_days(birth), to_days(date)
    if b < a:
        fail(E_RANGE, "date before birth")
    ya, ma, da = ymd(a)
    yb, mb, db = ymd(b)
    years = yb - ya
    if (mb, db) < (ma, da):
        years -= 1
    return years


def end_of_month_after(date, n):
    y, m, _ = ymd(to_days(add_months_clamped(date, n)))
    return iso(days_from_civil(y, m, dim(y, m)))
