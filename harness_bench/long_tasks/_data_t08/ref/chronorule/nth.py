"""N-th weekday helpers (port of src/nth.js)."""

from ._core import (
    WEEKDAYS,
    days_from_civil,
    dim,
    iso,
    need_int,
    need_month,
    need_year,
    to_days,
    wd,
    weekday_index,
    ymd,
)
from .errors import E_RANGE, fail


def _nth_day(year, month, w, n):
    last = dim(year, month)
    if n > 0:
        first = days_from_civil(year, month, 1)
        day = 1 + (w - wd(first)) % 7 + (n - 1) * 7
        return day if day <= last else None
    end = days_from_civil(year, month, last)
    day = last - (wd(end) - w) % 7 - (-n - 1) * 7
    return day if day >= 1 else None


def nth_weekday_of_month(year, month, weekday, n):
    need_year(year)
    need_month(month)
    w = weekday_index(weekday)
    need_int(n, "n")
    if n == 0 or n > 5 or n < -5:
        fail(E_RANGE, "n must be 1..5 or -1..-5")
    day = _nth_day(year, month, w, n)
    return None if day is None else iso(days_from_civil(year, month, day))


def last_weekday_of_month(year, month, weekday):
    return nth_weekday_of_month(year, month, weekday, -1)


def weekday_occurrence(date):
    n = to_days(date)
    y, m, d = ymd(n)
    return {"n": (d - 1) // 7 + 1, "from_end": -((dim(y, m) - d) // 7 + 1), "weekday": WEEKDAYS[wd(n)]}


def next_weekday(date, weekday, *, inclusive=False):
    n = to_days(date)
    w = weekday_index(weekday)
    delta = (w - wd(n)) % 7
    if delta == 0 and not inclusive:
        delta = 7
    return iso(n + delta)


def prev_weekday(date, weekday, *, inclusive=False):
    n = to_days(date)
    w = weekday_index(weekday)
    delta = (wd(n) - w) % 7
    if delta == 0 and not inclusive:
        delta = 7
    return iso(n - delta)


def weekdays_in_month(year, month, weekday):
    need_year(year)
    need_month(month)
    w = weekday_index(weekday)
    first = days_from_civil(year, month, 1)
    day = 1 + (w - wd(first)) % 7
    out = []
    while day <= dim(year, month):
        out.append(iso(first + day - 1))
        day += 7
    return out


def is_last_weekday_of_month(date):
    y, m, d = ymd(to_days(date))
    return d + 7 > dim(y, m)
