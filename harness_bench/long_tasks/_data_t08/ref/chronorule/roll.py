"""Business-day conventions (port of src/roll.js)."""

from ._core import iso, need_list, need_str, to_days, ymd
from .calendar import build_cal
from .errors import E_LIMIT, E_RANGE, fail

CONVENTIONS = (
    "none",
    "following",
    "preceding",
    "modified_following",
    "modified_preceding",
    "nearest",
)
MAX_SEARCH = 366


def _need_convention(convention):
    need_str(convention, "convention")
    if convention not in CONVENTIONS:
        fail(E_RANGE, "unknown convention")
    return convention


def _step(cal, n, direction):
    for _ in range(MAX_SEARCH):
        if cal.working(n):
            return n
        n += direction
    fail(E_LIMIT, "no working day nearby")


def roll_days(cal, n, convention):
    if convention == "none" or cal.working(n):
        return n
    if convention == "following":
        return _step(cal, n, 1)
    if convention == "preceding":
        return _step(cal, n, -1)
    month = ymd(n)[1]
    if convention == "modified_following":
        f = _step(cal, n, 1)
        return f if ymd(f)[1] == month else _step(cal, n, -1)
    if convention == "modified_preceding":
        p = _step(cal, n, -1)
        return p if ymd(p)[1] == month else _step(cal, n, 1)
    for k in range(1, MAX_SEARCH):
        if cal.working(n + k):
            return n + k
        if cal.working(n - k):
            return n - k
    fail(E_LIMIT, "no working day nearby")


def roll(date, convention, calendar=None):
    n = to_days(date)
    _need_convention(convention)
    cal = build_cal(calendar)
    return iso(roll_days(cal, n, convention))


def roll_many(dates, convention, calendar=None, *, dedupe=True):
    need_list(dates, "dates")
    nums = [to_days(x) for x in dates]
    _need_convention(convention)
    cal = build_cal(calendar)
    out = []
    seen = set()
    for n in nums:
        r = roll_days(cal, n, convention)
        if dedupe and r in seen:
            continue
        seen.add(r)
        out.append(iso(r))
    return out


def is_rolled(date, convention, calendar=None):
    return roll(date, convention, calendar) != date
