"""Civil date arithmetic (port of src/civil.js)."""

from ._core import (
    WEEKDAYS,
    days_from_civil,
    dim,
    is_leap,
    iso,
    make_days,
    need_int,
    need_list,
    need_month,
    need_str,
    to_days,
    wd,
    weekday_index,
    ymd,
)
from .errors import E_LIMIT, E_RANGE, ChronoError, fail

MAX_RANGE_ITEMS = 3660


def is_leap_year(year):
    need_int(year, "year")
    return is_leap(year)


def days_in_month(year, month):
    need_int(year, "year")
    need_month(month)
    return dim(year, month)


def days_in_year(year):
    need_int(year, "year")
    return 366 if is_leap(year) else 365


def is_valid_date(value):
    try:
        to_days(value)
    except ChronoError:
        return False
    return True


def make_date(year, month, day):
    need_int(year, "year")
    need_int(month, "month")
    need_int(day, "day")
    return iso(make_days(year, month, day))


def date_parts(date):
    y, m, d = ymd(to_days(date))
    return {"year": y, "month": m, "day": d}


def weekday(date):
    return wd(to_days(date))


def weekday_code(date):
    return WEEKDAYS[wd(to_days(date))]


def day_of_year(date):
    n = to_days(date)
    y, _, _ = ymd(n)
    return n - days_from_civil(y, 1, 1) + 1


def add_days(date, n):
    d = to_days(date)
    need_int(n, "n")
    return iso(d + n)


def _shift_month(y, m, n):
    total = y * 12 + (m - 1) + n
    ny = total // 12
    return ny, total - ny * 12 + 1


def add_months(date, n):
    y, m, d = ymd(to_days(date))
    need_int(n, "n")
    ny, nm = _shift_month(y, m, n)
    if ny < 1000 or ny > 9999:
        fail(E_RANGE, "result out of supported range")
    last = dim(ny, nm)
    if d <= last:
        return iso(days_from_civil(ny, nm, d))
    # Overflow like Date.prototype.setMonth: surplus days spill into the next month.
    return iso(days_from_civil(ny, nm, 1) + d - 1)


def add_months_clamped(date, n):
    y, m, d = ymd(to_days(date))
    need_int(n, "n")
    ny, nm = _shift_month(y, m, n)
    if ny < 1000 or ny > 9999:
        fail(E_RANGE, "result out of supported range")
    return iso(days_from_civil(ny, nm, min(d, dim(ny, nm))))


def add_years(date, n):
    need_int(n, "n")
    return add_months(date, 12 * n)


def diff_days(a, b):
    return to_days(b) - to_days(a)


def compare_dates(a, b):
    x, y = to_days(a), to_days(b)
    return (x > y) - (x < y)


def start_of_month(date):
    y, m, _ = ymd(to_days(date))
    return iso(days_from_civil(y, m, 1))


def end_of_month(date):
    y, m, _ = ymd(to_days(date))
    return iso(days_from_civil(y, m, dim(y, m)))


def start_of_quarter(date):
    y, m, _ = ymd(to_days(date))
    return iso(days_from_civil(y, (m - 1) // 3 * 3 + 1, 1))


def end_of_quarter(date):
    y, m, _ = ymd(to_days(date))
    qm = (m - 1) // 3 * 3 + 3
    return iso(days_from_civil(y, qm, dim(y, qm)))


def start_of_year(date):
    y, _, _ = ymd(to_days(date))
    return iso(days_from_civil(y, 1, 1))


def end_of_year(date):
    y, _, _ = ymd(to_days(date))
    return iso(days_from_civil(y, 12, 31))


def start_of_week(date, *, week_start="MO"):
    n = to_days(date)
    ws = weekday_index(week_start, "week_start")
    return iso(n - (wd(n) - ws) % 7)


def end_of_week(date, *, week_start="MO"):
    n = to_days(date)
    ws = weekday_index(week_start, "week_start")
    return iso(n - (wd(n) - ws) % 7 + 6)


def clamp_date(date, lo, hi):
    n, a, b = to_days(date), to_days(lo), to_days(hi)
    if a > b:
        fail(E_RANGE, "lower bound after upper bound")
    return iso(min(max(n, a), b))


def min_date(dates):
    need_list(dates, "dates")
    if not dates:
        fail(E_RANGE, "empty list")
    return iso(min(to_days(x) for x in dates))


def max_date(dates):
    need_list(dates, "dates")
    if not dates:
        fail(E_RANGE, "empty list")
    return iso(max(to_days(x) for x in dates))


def sort_dates(dates, *, descending=False, unique=False):
    need_list(dates, "dates")
    nums = [to_days(x) for x in dates]
    if unique:
        nums = list(set(nums))
    nums.sort(reverse=bool(descending))
    return [iso(x) for x in nums]


def each_day(start, end, *, step=1):
    a, b = to_days(start), to_days(end)
    need_int(step, "step")
    if step < 1:
        fail(E_RANGE, "step must be positive")
    if b < a:
        return []
    if (b - a) // step + 1 > MAX_RANGE_ITEMS:
        fail(E_LIMIT, "too many days")
    return [iso(x) for x in range(a, b + 1, step)]


def is_same_month(a, b):
    ya, ma, _ = ymd(to_days(a))
    yb, mb, _ = ymd(to_days(b))
    return ya == yb and ma == mb


def is_weekend(date, *, weekend=None):
    n = to_days(date)
    if weekend is None:
        weekend = ["SA", "SU"]
    need_list(weekend, "weekend")
    return wd(n) in [weekday_index(c) for c in weekend]


def to_compact(date):
    need_str(date, "date")
    return iso(to_days(date)).replace("-", "")

