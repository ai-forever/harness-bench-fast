"""RRULE-lite recurrence rules (port of src/rrule.js)."""

import re

from ._core import (
    WEEKDAYS,
    days_from_civil,
    dim,
    is_int,
    iso,
    make_days,
    need_int,
    to_days,
    wd,
    ymd,
)
from .errors import E_LIMIT, E_RANGE, E_RULE, E_TYPE, ChronoError, fail

FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
KEYS = ("FREQ", "INTERVAL", "COUNT", "UNTIL", "BYMONTH", "BYMONTHDAY", "BYDAY", "BYSETPOS", "WKST")
RULE_FIELDS = ("freq", "interval", "count", "until", "bymonth", "bymonthday", "byday", "bysetpos", "wkst")
MAX_PERIODS = 50000
MAX_INTERVAL = 1000
MAX_COUNT = 10000

_RE_UINT = re.compile(r"[0-9]+")
_RE_SINT = re.compile(r"[+-]?[0-9]+")
_RE_DAY = re.compile(r"([+-]?[0-9]{1,2})?(MO|TU|WE|TH|FR|SA|SU)")
_RE_UNTIL = re.compile(r"([0-9]{4})-?([0-9]{2})-?([0-9]{2})")


class Rule:
    __slots__ = RULE_FIELDS + ("days",)

    def as_dict(self):
        return {
            "freq": self.freq,
            "interval": self.interval,
            "count": self.count,
            "until": None if self.until is None else iso(self.until),
            "bymonth": list(self.bymonth),
            "bymonthday": list(self.bymonthday),
            "byday": [f"{n}{WEEKDAYS[w]}" if n else WEEKDAYS[w] for n, w in self.days],
            "bysetpos": list(self.bysetpos),
            "wkst": self.wkst,
        }


def _uint(text, lo, hi):
    if not _RE_UINT.fullmatch(text):
        fail(E_RULE, "expected a positive integer")
    v = int(text)
    if v < lo or v > hi:
        fail(E_RULE, "number out of range")
    return v


def _int_list(text, lo, hi, *, nonzero):
    out = []
    for item in text.split(","):
        if not _RE_SINT.fullmatch(item):
            fail(E_RULE, "bad number in list")
        v = int(item)
        if (nonzero and v == 0) or v < lo or v > hi:
            fail(E_RULE, "number out of range")
        if v not in out:
            out.append(v)
    return out


def _parse_text(text):
    s = text.strip().upper()
    if s.startswith("RRULE:"):
        s = s[6:]
    parts = [p for p in s.split(";") if p != ""]
    if not parts:
        fail(E_RULE, "empty rule")
    seen = {}
    for part in parts:
        if "=" not in part:
            fail(E_RULE, "expected KEY=VALUE")
        key, value = part.split("=", 1)
        if key not in KEYS:
            fail(E_RULE, "unknown key")
        if key in seen:
            fail(E_RULE, "duplicate key")
        seen[key] = value
    r = Rule()
    if "FREQ" not in seen or seen["FREQ"] not in FREQS:
        fail(E_RULE, "FREQ is required")
    r.freq = seen["FREQ"]
    r.interval = _uint(seen["INTERVAL"], 1, MAX_INTERVAL) if "INTERVAL" in seen else 1
    r.count = _uint(seen["COUNT"], 1, MAX_COUNT) if "COUNT" in seen else None
    r.until = None
    if "UNTIL" in seen:
        mt = _RE_UNTIL.fullmatch(seen["UNTIL"])
        if not mt:
            fail(E_RULE, "bad UNTIL")
        try:
            r.until = make_days(int(mt.group(1)), int(mt.group(2)), int(mt.group(3)))
        except ChronoError:
            fail(E_RULE, "bad UNTIL date")
    if r.count is not None and r.until is not None:
        fail(E_RULE, "COUNT and UNTIL are exclusive")
    r.bymonth = sorted(_int_list(seen["BYMONTH"], 1, 12, nonzero=True)) if "BYMONTH" in seen else []
    r.bymonthday = (
        sorted(_int_list(seen["BYMONTHDAY"], -31, 31, nonzero=True)) if "BYMONTHDAY" in seen else []
    )
    if r.bymonthday and r.freq == "WEEKLY":
        fail(E_RULE, "BYMONTHDAY is not allowed with WEEKLY")
    r.days = []
    if "BYDAY" in seen:
        for item in seen["BYDAY"].split(","):
            mt = _RE_DAY.fullmatch(item)
            if not mt:
                fail(E_RULE, "bad BYDAY item")
            n = int(mt.group(1)) if mt.group(1) else 0
            if mt.group(1) and n == 0:
                fail(E_RULE, "ordinal must not be zero")
            if n:
                if r.freq not in ("MONTHLY", "YEARLY"):
                    fail(E_RULE, "ordinal BYDAY needs MONTHLY or YEARLY")
                top = 5 if (r.freq == "MONTHLY" or r.bymonth) else 53
                if abs(n) > top:
                    fail(E_RULE, "ordinal out of range")
            entry = (n, WEEKDAYS.index(mt.group(2)))
            if entry not in r.days:
                r.days.append(entry)
    if r.bymonthday and any(n for n, _ in r.days):
        fail(E_RULE, "ordinal BYDAY cannot be combined with BYMONTHDAY")
    r.bysetpos = _int_list(seen["BYSETPOS"], -366, 366, nonzero=True) if "BYSETPOS" in seen else []
    if r.bysetpos and not (r.bymonth or r.bymonthday or r.days):
        fail(E_RULE, "BYSETPOS needs another BYxxx part")
    if "WKST" in seen:
        if seen["WKST"] not in WEEKDAYS:
            fail(E_RULE, "bad WKST")
        r.wkst = seen["WKST"]
    else:
        r.wkst = "MO"
    return r


def _dict_to_text(obj):
    for key in obj:
        if key not in RULE_FIELDS:
            fail(E_TYPE, "unknown rule field")
    freq = obj.get("freq")
    if not isinstance(freq, str):
        fail(E_TYPE, "freq must be a string")
    parts = ["FREQ=" + freq]
    interval = obj.get("interval")
    if interval is not None:
        need_int(interval, "interval")
        if interval != 1:
            parts.append(f"INTERVAL={interval}")
    count = obj.get("count")
    if count is not None:
        need_int(count, "count")
        parts.append(f"COUNT={count}")
    until = obj.get("until")
    if until is not None:
        if not isinstance(until, str):
            fail(E_TYPE, "until must be a string")
        parts.append("UNTIL=" + until)
    for field, key in (("bymonth", "BYMONTH"), ("bymonthday", "BYMONTHDAY"), ("bysetpos", "BYSETPOS")):
        items = obj.get(field)
        if items is None:
            continue
        if not isinstance(items, list) or not all(is_int(v) for v in items):
            fail(E_TYPE, f"{field} must be an array of integers")
        if items:
            parts.append(key + "=" + ",".join(str(v) for v in items))
    byday = obj.get("byday")
    if byday is not None:
        if not isinstance(byday, list) or not all(isinstance(v, str) for v in byday):
            fail(E_TYPE, "byday must be an array of strings")
        if byday:
            parts.append("BYDAY=" + ",".join(byday))
    wkst = obj.get("wkst")
    if wkst is not None:
        if not isinstance(wkst, str):
            fail(E_TYPE, "wkst must be a string")
        parts.append("WKST=" + wkst)
    return ";".join(parts)


def to_rule(rule):
    if isinstance(rule, str):
        return _parse_text(rule)
    if isinstance(rule, dict):
        return _parse_text(_dict_to_text(rule))
    fail(E_TYPE, "rule must be a string or an object")


def parse_rule(text):
    if not isinstance(text, str):
        fail(E_TYPE, "rule text must be a string")
    return _parse_text(text).as_dict()


def rule_to_string(rule):
    r = to_rule(rule)
    parts = ["FREQ=" + r.freq]
    if r.interval != 1:
        parts.append(f"INTERVAL={r.interval}")
    if r.count is not None:
        parts.append(f"COUNT={r.count}")
    if r.until is not None:
        parts.append("UNTIL=" + iso(r.until).replace("-", ""))
    if r.bymonth:
        parts.append("BYMONTH=" + ",".join(map(str, r.bymonth)))
    if r.bymonthday:
        parts.append("BYMONTHDAY=" + ",".join(map(str, r.bymonthday)))
    if r.days:
        parts.append("BYDAY=" + ",".join(r.as_dict()["byday"]))
    if r.bysetpos:
        parts.append("BYSETPOS=" + ",".join(map(str, r.bysetpos)))
    if r.wkst != "MO":
        parts.append("WKST=" + r.wkst)
    return ";".join(parts)


# --------------------------------------------------------------------------
# Expansion
# --------------------------------------------------------------------------


def _month_days(r, y, m, d0):
    last = dim(y, m)
    if not r.bymonthday and not r.days:
        return [d0] if d0 <= last else []
    days = set()
    if r.bymonthday:
        for v in r.bymonthday:
            dd = v if v > 0 else last + v + 1
            if 1 <= dd <= last:
                days.add(dd)
        if r.days:
            wanted = {w for _, w in r.days}
            first = days_from_civil(y, m, 1)
            days = {dd for dd in days if wd(first + dd - 1) in wanted}
    else:
        first = days_from_civil(y, m, 1)
        for n, w in r.days:
            if n == 0:
                dd = 1 + (w - wd(first)) % 7
                while dd <= last:
                    days.add(dd)
                    dd += 7
            elif n > 0:
                dd = 1 + (w - wd(first)) % 7 + (n - 1) * 7
                if dd <= last:
                    days.add(dd)
            else:
                end = first + last - 1
                dd = last - (wd(end) - w) % 7 - (-n - 1) * 7
                if dd >= 1:
                    days.add(dd)
    return sorted(days)


def _year_candidates(r, y, m0, d0):
    out = []
    if not r.bymonthday and not r.days:
        for m in r.bymonth or [m0]:
            if d0 <= dim(y, m):
                out.append(days_from_civil(y, m, d0))
        return out
    if r.bymonthday or r.bymonth:
        for m in r.bymonth or range(1, 13):
            first = days_from_civil(y, m, 1)
            out.extend(first + dd - 1 for dd in _month_days(r, y, m, d0))
        return out
    jan1 = days_from_civil(y, 1, 1)
    dec31 = days_from_civil(y, 12, 31)
    found = set()
    for n, w in r.days:
        if n == 0:
            x = jan1 + (w - wd(jan1)) % 7
            while x <= dec31:
                found.add(x)
                x += 7
        elif n > 0:
            x = jan1 + (w - wd(jan1)) % 7 + (n - 1) * 7
            if x <= dec31:
                found.add(x)
        else:
            x = dec31 - (wd(dec31) - w) % 7 - (-n - 1) * 7
            if x >= jan1:
                found.add(x)
    return sorted(found)


def _day_matches(r, n):
    y, m, d = ymd(n)
    if r.bymonth and m not in r.bymonth:
        return False
    if r.bymonthday:
        last = dim(y, m)
        if not any((v if v > 0 else last + v + 1) == d for v in r.bymonthday):
            return False
    return not (r.days and wd(n) not in {w for _, w in r.days})


def _period(r, k, start):
    """Return (period_start_day, sorted candidate days) or None past year 9999."""
    y0, m0, d0 = ymd(start)
    if r.freq == "DAILY":
        day = start + k * r.interval
        if ymd(day)[0] > 9999:
            return None
        return day, ([day] if _day_matches(r, day) else [])
    if r.freq == "WEEKLY":
        ws = WEEKDAYS.index(r.wkst)
        week0 = start - (wd(start) - ws) % 7
        pstart = week0 + 7 * r.interval * k
        if ymd(pstart)[0] > 9999:
            return None
        wanted = {w for _, w in r.days} if r.days else {wd(start)}
        cands = [x for x in range(pstart, pstart + 7) if wd(x) in wanted]
        if r.bymonth:
            cands = [x for x in cands if ymd(x)[1] in r.bymonth]
        return pstart, cands
    if r.freq == "MONTHLY":
        total = y0 * 12 + (m0 - 1) + k * r.interval
        y = total // 12
        m = total - y * 12 + 1
        if y > 9999:
            return None
        first = days_from_civil(y, m, 1)
        if r.bymonth and m not in r.bymonth:
            return first, []
        return first, [first + dd - 1 for dd in _month_days(r, y, m, d0)]
    y = y0 + k * r.interval
    if y > 9999:
        return None
    return days_from_civil(y, 1, 1), _year_candidates(r, y, m0, d0)


def _apply_setpos(r, cands):
    if not r.bysetpos or not cands:
        return cands
    picked = set()
    for p in r.bysetpos:
        idx = p - 1 if p > 0 else len(cands) + p
        if 0 <= idx < len(cands):
            picked.add(cands[idx])
    return sorted(picked)


def iterate(r, start, stop=None):
    """Yield occurrence day numbers in order (COUNT and UNTIL applied)."""
    count = 0
    for k in range(MAX_PERIODS):
        period = _period(r, k, start)
        if period is None:
            return
        pstart, cands = period
        if r.until is not None and pstart > r.until:
            return
        if stop is not None and pstart > stop:
            return
        for c in _apply_setpos(r, cands):
            if c < start:
                continue
            if r.until is not None and c > r.until:
                return
            if ymd(c)[0] > 9999:
                return
            count += 1
            yield c
            if r.count is not None and count >= r.count:
                return


def _opt_limit(limit):
    if limit is None:
        return None
    need_int(limit, "limit")
    if limit < 1:
        fail(E_RANGE, "limit must be positive")
    return limit


def expand_rule(rule, dtstart, *, after=None, before=None, limit=None, inclusive=True):
    r = to_rule(rule)
    start = to_days(dtstart, "dtstart")
    lo = None if after is None else to_days(after, "after")
    hi = None if before is None else to_days(before, "before")
    limit = _opt_limit(limit)
    if r.count is None and r.until is None and hi is None and limit is None:
        fail(E_LIMIT, "unbounded rule needs COUNT, UNTIL, before or limit")
    out = []
    for c in iterate(r, start, hi):
        if hi is not None and (c > hi or (not inclusive and c == hi)):
            break
        if lo is not None and (c < lo or (not inclusive and c == lo)):
            continue
        out.append(iso(c))
        if limit is not None and len(out) >= limit:
            break
    return out


def next_occurrence(rule, dtstart, after):
    r = to_rule(rule)
    start = to_days(dtstart, "dtstart")
    lo = to_days(after, "after")
    for c in iterate(r, start):
        if c > lo:
            return iso(c)
    return None


def occurs_on(rule, dtstart, date):
    r = to_rule(rule)
    start = to_days(dtstart, "dtstart")
    target = to_days(date)
    for c in iterate(r, start, target):
        if c == target:
            return True
        if c > target:
            return False
    return False


def count_occurrences(rule, dtstart, *, before=None):
    r = to_rule(rule)
    start = to_days(dtstart, "dtstart")
    hi = None if before is None else to_days(before, "before")
    if r.count is None and r.until is None and hi is None:
        fail(E_LIMIT, "unbounded rule needs COUNT, UNTIL or before")
    total = 0
    for c in iterate(r, start, hi):
        if hi is not None and c > hi:
            break
        total += 1
    return total
