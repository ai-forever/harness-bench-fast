"""Production calendars (port of src/calendar.js)."""

from ._core import WEEKDAYS, iso, need_dict, need_int, need_list, to_days, wd, weekday_index
from .errors import E_RANGE, E_TYPE, fail

CAL_KEYS = ("weekend", "holidays", "workdays", "day_hours", "preholiday_cut")
DEFAULT_WEEKEND = ("SA", "SU")


class Cal:
    __slots__ = ("weekend", "holidays", "workdays", "day_hours", "cut")

    def working(self, n):
        if n in self.holidays:
            return False
        return n in self.workdays or wd(n) not in self.weekend

    def kind(self, n):
        if n in self.holidays:
            return "holiday"
        if not (n in self.workdays or wd(n) not in self.weekend):
            return "weekend"
        if n + 1 in self.holidays:
            return "preholiday"
        if n in self.workdays:
            return "transfer"
        return "workday"

    def hours(self, n):
        k = self.kind(n)
        if k == "preholiday":
            return self.day_hours - self.cut
        if k in ("workday", "transfer"):
            return self.day_hours
        return 0


def build_cal(spec):
    cal = Cal()
    if spec is None:
        spec = {}
    need_dict(spec, "calendar")
    for key in spec:
        if key not in CAL_KEYS:
            fail(E_TYPE, "unknown calendar key")
    weekend = spec.get("weekend")
    if weekend is None:
        weekend = list(DEFAULT_WEEKEND)
    need_list(weekend, "weekend")
    cal.weekend = {weekday_index(c, "weekend day") for c in weekend}
    if len(cal.weekend) > 6:
        fail(E_RANGE, "a calendar needs at least one working weekday")
    holidays = spec.get("holidays")
    holidays = [] if holidays is None else need_list(holidays, "holidays")
    cal.holidays = {to_days(x, "holiday") for x in holidays}
    workdays = spec.get("workdays")
    workdays = [] if workdays is None else need_list(workdays, "workdays")
    cal.workdays = {to_days(x, "workday") for x in workdays} - cal.holidays
    hours = spec.get("day_hours")
    hours = 8 if hours is None else need_int(hours, "day_hours")
    if hours < 1 or hours > 24:
        fail(E_RANGE, "day_hours out of range")
    cut = spec.get("preholiday_cut")
    cut = 1 if cut is None else need_int(cut, "preholiday_cut")
    if cut < 0 or cut >= hours:
        fail(E_RANGE, "preholiday_cut out of range")
    cal.day_hours = hours
    cal.cut = cut
    return cal


def make_calendar(spec=None):
    cal = build_cal(spec)
    return {
        "weekend": [WEEKDAYS[i] for i in sorted(cal.weekend)],
        "holidays": [iso(n) for n in sorted(cal.holidays)],
        "workdays": [iso(n) for n in sorted(cal.workdays)],
        "day_hours": cal.day_hours,
        "preholiday_cut": cal.cut,
    }


def day_kind(date, calendar=None):
    n = to_days(date)
    return build_cal(calendar).kind(n)


def is_workday(date, calendar=None):
    n = to_days(date)
    return build_cal(calendar).working(n)


def is_holiday(date, calendar=None):
    n = to_days(date)
    return n in build_cal(calendar).holidays


def work_hours(date, calendar=None):
    n = to_days(date)
    return build_cal(calendar).hours(n)


def day_info(date, calendar=None):
    n = to_days(date)
    cal = build_cal(calendar)
    kind = cal.kind(n)
    return {
        "date": iso(n),
        "kind": kind,
        "is_workday": kind in ("workday", "transfer", "preholiday"),
        "hours": cal.hours(n),
        "weekday": WEEKDAYS[wd(n)],
    }


def holidays_between(start, end, calendar=None):
    a, b = to_days(start), to_days(end)
    cal = build_cal(calendar)
    return [iso(n) for n in sorted(cal.holidays) if a <= n <= b]


def merge_calendars(base, extra):
    """Union of holidays and workdays; weekend/hours of `extra` win when given."""
    a = make_calendar(base)
    need_dict(extra, "calendar")
    b = make_calendar(extra)
    spec = {
        "weekend": b["weekend"] if extra.get("weekend") is not None else a["weekend"],
        "holidays": a["holidays"] + b["holidays"],
        "workdays": a["workdays"] + b["workdays"],
        "day_hours": b["day_hours"] if extra.get("day_hours") is not None else a["day_hours"],
        "preholiday_cut": (
            b["preholiday_cut"] if extra.get("preholiday_cut") is not None else a["preholiday_cut"]
        ),
    }
    return make_calendar(spec)
