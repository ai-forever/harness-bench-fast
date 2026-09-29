"""Reporting periods and labels (port of src/period.js)."""

import re

from ._core import days_from_civil, dim, iso, need_int, need_month, need_str, to_days, ymd
from .errors import E_LIMIT, E_PARSE, E_RANGE, fail
from .weeks import _iso_week, iso_week_start, iso_weeks_in_year

KINDS = ("year", "half", "quarter", "month", "week", "fiscal")
MAX_PERIODS = 1000

_PATTERNS = (
    ("year", re.compile(r"([0-9]{4})")),
    ("half", re.compile(r"([0-9]{4})-H([0-9])")),
    ("quarter", re.compile(r"([0-9]{4})-Q([0-9])")),
    ("month", re.compile(r"([0-9]{4})-([0-9]{2})")),
    ("week", re.compile(r"([0-9]{4})-W([0-9]{2})")),
    ("fiscal", re.compile(r"FY([0-9]{4})")),
)


def _year_ok(y):
    if y < 1000 or y > 9999:
        fail(E_RANGE, "year out of range")
    return y


def parse_label(label):
    """Return (kind, year, index); index is 0 for year and fiscal labels."""
    need_str(label, "label")
    for kind, rx in _PATTERNS:
        mt = rx.fullmatch(label)
        if not mt:
            continue
        y = _year_ok(int(mt.group(1)))
        if kind in ("year", "fiscal"):
            return kind, y, 0
        x = int(mt.group(2))
        top = {"half": 2, "quarter": 4, "month": 12}.get(kind)
        if kind == "week":
            top = iso_weeks_in_year(y)
        if x < 1 or x > top:
            fail(E_RANGE, f"{kind} number out of range")
        return kind, y, x
    fail(E_PARSE, "unrecognised period label")


def _label(kind, y, x):
    _year_ok(y)
    if kind == "year":
        return f"{y:04d}"
    if kind == "half":
        return f"{y:04d}-H{x}"
    if kind == "quarter":
        return f"{y:04d}-Q{x}"
    if kind == "month":
        return f"{y:04d}-{x:02d}"
    if kind == "week":
        return f"{y:04d}-W{x:02d}"
    return f"FY{y:04d}"


def _need_kind(kind):
    need_str(kind, "kind")
    if kind not in KINDS:
        fail(E_RANGE, "unknown period kind")
    return kind


def quarter_of(date):
    return (ymd(to_days(date))[1] - 1) // 3 + 1


def half_of(date):
    return 1 if ymd(to_days(date))[1] <= 6 else 2


def fiscal_year(date, start_month):
    n = to_days(date)
    need_month(start_month)
    y, m, _ = ymd(n)
    if start_month == 1:
        return y
    return y + 1 if m >= start_month else y


def _period_parts(n, kind, fs):
    y, m, _ = ymd(n)
    if kind == "year":
        return y, 0
    if kind == "half":
        return y, 1 if m <= 6 else 2
    if kind == "quarter":
        return y, (m - 1) // 3 + 1
    if kind == "month":
        return y, m
    if kind == "week":
        return _iso_week(n)
    return (y if fs == 1 else (y + 1 if m >= fs else y)), 0


def period_of(date, kind, *, fiscal_start=1):
    n = to_days(date)
    _need_kind(kind)
    need_month(fiscal_start)
    y, x = _period_parts(n, kind, fiscal_start)
    return _label(kind, y, x)


def _range_days(kind, y, x, fs):
    if kind == "year":
        return days_from_civil(y, 1, 1), days_from_civil(y, 12, 31)
    if kind == "half":
        m0 = 1 if x == 1 else 7
        return days_from_civil(y, m0, 1), days_from_civil(y, m0 + 5, dim(y, m0 + 5))
    if kind == "quarter":
        m0 = (x - 1) * 3 + 1
        return days_from_civil(y, m0, 1), days_from_civil(y, m0 + 2, dim(y, m0 + 2))
    if kind == "month":
        return days_from_civil(y, x, 1), days_from_civil(y, x, dim(y, x))
    if kind == "week":
        start = to_days(iso_week_start(y, x))
        return start, start + 6
    if fs == 1:
        return days_from_civil(y, 1, 1), days_from_civil(y, 12, 31)
    return days_from_civil(y - 1, fs, 1), days_from_civil(y, fs, 1) - 1


def period_range(label, *, fiscal_start=1):
    kind, y, x = parse_label(label)
    need_month(fiscal_start)
    a, b = _range_days(kind, y, x, fiscal_start)
    return [iso(a), iso(b)]


def period_kind(label):
    return parse_label(label)[0]


def shift_period(label, n, *, fiscal_start=1):
    kind, y, x = parse_label(label)
    need_int(n, "n")
    need_month(fiscal_start)
    if kind in ("year", "fiscal"):
        return _label(kind, y + n, 0)
    if kind == "week":
        start = to_days(iso_week_start(y, x)) + 7 * n
        wy, wn = _iso_week(start)
        return _label("week", wy, wn)
    per = {"half": 2, "quarter": 4, "month": 12}[kind]
    total = y * per + (x - 1) + n
    ny = total // per
    return _label(kind, ny, total - ny * per + 1)


def periods_between(start, end, kind, *, fiscal_start=1):
    a, b = to_days(start), to_days(end)
    _need_kind(kind)
    need_month(fiscal_start)
    if b < a:
        return []
    first = _label(kind, *_period_parts(a, kind, fiscal_start))
    last = _label(kind, *_period_parts(b, kind, fiscal_start))
    out = [first]
    cur = first
    while cur != last:
        cur = shift_period(cur, 1, fiscal_start=fiscal_start)
        out.append(cur)
        if len(out) > MAX_PERIODS:
            fail(E_LIMIT, "too many periods")
    return out


def period_contains(label, date, *, fiscal_start=1):
    kind, y, x = parse_label(label)
    n = to_days(date)
    need_month(fiscal_start)
    a, b = _range_days(kind, y, x, fiscal_start)
    return a <= n <= b


def days_in_period(label, *, fiscal_start=1):
    kind, y, x = parse_label(label)
    need_month(fiscal_start)
    a, b = _range_days(kind, y, x, fiscal_start)
    return b - a + 1
