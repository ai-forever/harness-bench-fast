"""Business-day arithmetic (port of src/business.js)."""

from ._core import days_from_civil, dim, iso, need_int, need_month, need_year, to_days
from .calendar import build_cal
from .errors import E_LIMIT, E_RANGE, fail

MAX_STEPS = 100000


def _forward(cal, n):
    steps = 0
    while not cal.working(n):
        n += 1
        steps += 1
        if steps > MAX_STEPS:
            fail(E_LIMIT, "no working day found")
    return n


def _backward(cal, n):
    steps = 0
    while not cal.working(n):
        n -= 1
        steps += 1
        if steps > MAX_STEPS:
            fail(E_LIMIT, "no working day found")
    return n


def add_business_days(date, n, calendar=None):
    d = to_days(date)
    need_int(n, "n")
    cal = build_cal(calendar)
    if n == 0:
        return iso(_forward(cal, d))
    step = 1 if n > 0 else -1
    left = abs(n)
    steps = 0
    while left:
        d += step
        steps += 1
        if steps > MAX_STEPS:
            fail(E_LIMIT, "too many steps")
        if cal.working(d):
            left -= 1
    return iso(d)


def business_days_between(start, end, calendar=None):
    a, b = to_days(start), to_days(end)
    cal = build_cal(calendar)
    if a == b:
        return 0
    lo, hi, sign = (a, b, 1) if a < b else (b, a, -1)
    if hi - lo > MAX_STEPS:
        fail(E_LIMIT, "range too long")
    return sign * sum(1 for x in range(lo + 1, hi + 1) if cal.working(x))


def next_business_day(date, calendar=None):
    d = to_days(date)
    return iso(_forward(build_cal(calendar), d + 1))


def prev_business_day(date, calendar=None):
    d = to_days(date)
    return iso(_backward(build_cal(calendar), d - 1))


def _month_working(year, month, cal):
    first = days_from_civil(year, month, 1)
    return [x for x in range(first, first + dim(year, month)) if cal.working(x)]


def nth_business_day(year, month, n, calendar=None):
    need_year(year)
    need_month(month)
    need_int(n, "n")
    if n == 0:
        fail(E_RANGE, "n must not be zero")
    days = _month_working(year, month, build_cal(calendar))
    idx = n - 1 if n > 0 else len(days) + n
    if idx < 0 or idx >= len(days):
        return None
    return iso(days[idx])


def last_business_day(year, month, calendar=None):
    return nth_business_day(year, month, -1, calendar)


def business_days_in_month(year, month, calendar=None):
    need_year(year)
    need_month(month)
    return len(_month_working(year, month, build_cal(calendar)))


def work_hours_in_month(year, month, calendar=None):
    need_year(year)
    need_month(month)
    cal = build_cal(calendar)
    first = days_from_civil(year, month, 1)
    return sum(cal.hours(x) for x in range(first, first + dim(year, month)))


def work_hours_between(start, end, calendar=None):
    a, b = to_days(start), to_days(end)
    cal = build_cal(calendar)
    if b < a:
        return 0
    if b - a > MAX_STEPS:
        fail(E_LIMIT, "range too long")
    return sum(cal.hours(x) for x in range(a, b + 1))


def business_days_list(start, end, calendar=None):
    a, b = to_days(start), to_days(end)
    cal = build_cal(calendar)
    if b < a:
        return []
    if b - a > 3660:
        fail(E_LIMIT, "range too long")
    return [iso(x) for x in range(a, b + 1) if cal.working(x)]
