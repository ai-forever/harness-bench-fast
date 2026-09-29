"""Schedules: rule + extra dates + exclusions + business-day roll (port of src/schedule.js)."""

from ._core import iso, need_dict, need_int, need_list, to_days
from .calendar import build_cal
from .errors import E_RANGE, E_TYPE, fail
from .roll import _need_convention, roll_days
from .rrule import iterate, to_rule

SPEC_KEYS = ("rule", "start", "exdates", "rdates", "roll", "calendar")


class Sched:
    __slots__ = ("rule", "start", "exdates", "rdates", "roll", "cal")


def build_schedule(spec):
    need_dict(spec, "schedule")
    for key in spec:
        if key not in SPEC_KEYS:
            fail(E_TYPE, "unknown schedule key")
    if spec.get("rule") is None or spec.get("start") is None:
        fail(E_TYPE, "schedule needs rule and start")
    s = Sched()
    s.rule = to_rule(spec["rule"])
    s.start = to_days(spec["start"], "start")
    ex = spec.get("exdates")
    ex = [] if ex is None else need_list(ex, "exdates")
    s.exdates = {to_days(x, "exdate") for x in ex}
    rd = spec.get("rdates")
    rd = [] if rd is None else need_list(rd, "rdates")
    s.rdates = sorted({to_days(x, "rdate") for x in rd if to_days(x, "rdate") >= s.start})
    conv = spec.get("roll")
    s.roll = "none" if conv is None else _need_convention(conv)
    s.cal = build_cal(spec.get("calendar"))
    return s


def _raw(s, stop=None):
    """Merge rule occurrences with RDATEs (ascending, no duplicates), minus EXDATEs."""
    gen = iterate(s.rule, s.start, stop)
    extra = [x for x in s.rdates if stop is None or x <= stop]
    i = 0
    last = None
    for c in gen:
        if stop is not None and c > stop:
            break
        while i < len(extra) and extra[i] <= c:
            x = extra[i]
            i += 1
            if x != last and x not in s.exdates:
                last = x
                yield x
        if c != last and c not in s.exdates:
            last = c
            yield c
    while i < len(extra):
        x = extra[i]
        i += 1
        if x != last and x not in s.exdates:
            last = x
            yield x


def _rolled(s, stop=None):
    seen = set()
    for x in _raw(s, stop):
        y = roll_days(s.cal, x, s.roll)
        if y in seen:
            continue
        seen.add(y)
        yield y


def schedule_between(spec, start, end):
    s = build_schedule(spec)
    lo, hi = to_days(start, "start"), to_days(end, "end")
    if hi < lo:
        return []
    return [iso(y) for y in _rolled(s, hi) if lo <= y <= hi]


def schedule_take(spec, n, *, from_=None):
    s = build_schedule(spec)
    need_int(n, "n")
    if n < 1:
        fail(E_RANGE, "n must be positive")
    lo = s.start if from_ is None else to_days(from_, "from")
    out = []
    for y in _rolled(s):
        if y < lo:
            continue
        out.append(iso(y))
        if len(out) >= n:
            break
    return out


def schedule_next(spec, after):
    s = build_schedule(spec)
    lo = to_days(after, "after")
    for y in _rolled(s):
        if y > lo:
            return iso(y)
    return None
