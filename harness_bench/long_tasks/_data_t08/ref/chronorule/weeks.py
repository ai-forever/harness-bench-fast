"""Week numbering (port of src/weeks.js)."""

from ._core import days_from_civil, dim, iso, need_int, need_month, to_days, wd, weekday_index, ymd
from .errors import E_RANGE, fail


def _iso_week(n):
    thursday = n - wd(n) + 3
    y, _, _ = ymd(thursday)
    return y, (thursday - days_from_civil(y, 1, 1)) // 7 + 1


def iso_week(date):
    y, w = _iso_week(to_days(date))
    return {"year": y, "week": w}


def iso_weeks_in_year(year):
    need_int(year, "year")
    return _iso_week(days_from_civil(year, 12, 28))[1]


def iso_week_start(year, week):
    need_int(year, "year")
    need_int(week, "week")
    if week < 1 or week > iso_weeks_in_year(year):
        fail(E_RANGE, "week out of range")
    jan4 = days_from_civil(year, 1, 4)
    return iso(jan4 - wd(jan4) + (week - 1) * 7)


def iso_week_label(date):
    y, w = _iso_week(to_days(date))
    return f"{y:04d}-W{w:02d}"


def week_of_month(date, *, week_start="MO"):
    n = to_days(date)
    ws = weekday_index(week_start, "week_start")
    y, m, d = ymd(n)
    offset = (wd(days_from_civil(y, m, 1)) - ws) % 7
    return (d - 1 + offset) // 7 + 1


def week_of_year(date, *, week_start="MO"):
    n = to_days(date)
    ws = weekday_index(week_start, "week_start")
    y, _, _ = ymd(n)
    jan1 = days_from_civil(y, 1, 1)
    offset = (wd(jan1) - ws) % 7
    return (n - jan1 + offset) // 7 + 1


def weeks_in_month(year, month, *, week_start="MO"):
    need_int(year, "year")
    need_month(month)
    ws = weekday_index(week_start, "week_start")
    offset = (wd(days_from_civil(year, month, 1)) - ws) % 7
    return (dim(year, month) - 1 + offset) // 7 + 1
