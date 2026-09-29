"""Internal helpers: day numbers, strict ISO parsing, argument checks (src/internal.js)."""

import re

from .errors import E_PARSE, E_RANGE, E_TYPE, fail

WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
MIN_YEAR = 1000
MAX_YEAR = 9999

_ISO_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")


def is_leap(y):
    return (y % 4 == 0 and y % 100 != 0) or y % 400 == 0


def dim(y, m):
    if m == 2:
        return 29 if is_leap(y) else 28
    return 30 if m in (4, 6, 9, 11) else 31


def days_from_civil(y, m, d):
    y -= 1 if m <= 2 else 0
    era = y // 400
    yoe = y - era * 400
    mp = m - 3 if m > 2 else m + 9
    doy = (153 * mp + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_from_days(z):
    z += 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    return (y + 1 if m <= 2 else y, m, d)


def wd(n):
    """Weekday of a day number, Monday = 0 ... Sunday = 6."""
    return (n + 3) % 7


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def need_int(v, what="value"):
    if not is_int(v):
        fail(E_TYPE, f"{what} must be an integer")
    return v


def need_str(v, what="value"):
    if not isinstance(v, str):
        fail(E_TYPE, f"{what} must be a string")
    return v


def need_month(m):
    need_int(m, "month")
    if m < 1 or m > 12:
        fail(E_RANGE, "month out of range")
    return m


def need_year(y):
    need_int(y, "year")
    if y < MIN_YEAR or y > MAX_YEAR:
        fail(E_RANGE, "year out of range")
    return y


def make_days(y, m, d):
    """Day number of a validated civil date."""
    if y < MIN_YEAR or y > MAX_YEAR:
        fail(E_RANGE, "year out of range")
    if m < 1 or m > 12:
        fail(E_RANGE, "month out of range")
    if d < 1 or d > dim(y, m):
        fail(E_RANGE, "day out of range")
    return days_from_civil(y, m, d)


def to_days(value, what="date"):
    need_str(value, what)
    mt = _ISO_RE.fullmatch(value)
    if not mt:
        fail(E_PARSE, f"{what} must be YYYY-MM-DD")
    return make_days(int(mt.group(1)), int(mt.group(2)), int(mt.group(3)))


def opt_days(value, what="date"):
    return None if value is None else to_days(value, what)


def ymd(n):
    return civil_from_days(n)


def iso(n):
    y, m, d = civil_from_days(n)
    if y < MIN_YEAR or y > MAX_YEAR:
        fail(E_RANGE, "result out of supported range")
    return f"{y:04d}-{m:02d}-{d:02d}"


def weekday_index(code, what="weekday"):
    need_str(code, what)
    if code not in WEEKDAYS:
        fail(E_RANGE, f"unknown {what} code")
    return WEEKDAYS.index(code)


def need_list(v, what="value"):
    if not isinstance(v, list):
        fail(E_TYPE, f"{what} must be an array")
    return v


def need_dict(v, what="value"):
    if not isinstance(v, dict):
        fail(E_TYPE, f"{what} must be an object")
    return v
