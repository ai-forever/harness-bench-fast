"""Formatting (port of src/format.js)."""

from ._core import days_from_civil, need_int, need_month, need_str, to_days, wd, weekday_index, ymd
from .errors import E_RANGE, fail
from .locale_ru import (
    MONTHS_GENITIVE,
    MONTHS_NOMINATIVE,
    MONTHS_PREPOSITIONAL,
    MONTHS_SHORT,
    WEEKDAYS_ACCUSATIVE,
    WEEKDAYS_DATIVE_PLURAL,
    WEEKDAYS_FULL,
    WEEKDAYS_SHORT,
    plural_form,
)
from .weeks import _iso_week

TOKENS = (
    "YYYY", "GGGG", "MMMM", "LLLL", "dddd", "DDDD", "MMM", "YY", "MM", "DD", "dd", "WW",
    "M", "D", "E", "Q", "W",
)


def _token(tok, n):
    y, m, d = ymd(n)
    if tok == "YYYY":
        return f"{y:04d}"
    if tok == "GGGG":
        return f"{_iso_week(n)[0]:04d}"
    if tok == "MMMM":
        return MONTHS_GENITIVE[m - 1]
    if tok == "LLLL":
        return MONTHS_NOMINATIVE[m - 1]
    if tok == "dddd":
        return WEEKDAYS_FULL[wd(n)]
    if tok == "DDDD":
        return f"{n - days_from_civil(y, 1, 1) + 1:03d}"
    if tok == "MMM":
        return MONTHS_SHORT[m - 1]
    if tok == "YY":
        return f"{y % 100:02d}"
    if tok == "MM":
        return f"{m:02d}"
    if tok == "DD":
        return f"{d:02d}"
    if tok == "dd":
        return WEEKDAYS_SHORT[wd(n)]
    if tok == "WW":
        return f"{_iso_week(n)[1]:02d}"
    if tok == "M":
        return str(m)
    if tok == "D":
        return str(d)
    if tok == "E":
        return str(wd(n) + 1)
    if tok == "Q":
        return str((m - 1) // 3 + 1)
    return str(_iso_week(n)[1])


def format_date(date, pattern):
    n = to_days(date)
    need_str(pattern, "pattern")
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "[":
            j = pattern.find("]", i + 1)
            if j < 0:
                out.append(ch)
                i += 1
                continue
            out.append(pattern[i + 1:j])
            i = j + 1
            continue
        for tok in TOKENS:
            if pattern.startswith(tok, i):
                out.append(_token(tok, n))
                i += len(tok)
                break
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def format_range(start, end):
    a, b = to_days(start), to_days(end)
    if b < a:
        fail(E_RANGE, "end before start")
    ya, ma, da = ymd(a)
    yb, mb, db = ymd(b)
    if a == b:
        return f"{da} {MONTHS_GENITIVE[ma - 1]} {ya}"
    if ya == yb and ma == mb:
        return f"{da}–{db} {MONTHS_GENITIVE[mb - 1]} {yb}"
    if ya == yb:
        return f"{da} {MONTHS_GENITIVE[ma - 1]} – {db} {MONTHS_GENITIVE[mb - 1]} {yb}"
    return f"{da} {MONTHS_GENITIVE[ma - 1]} {ya} – {db} {MONTHS_GENITIVE[mb - 1]} {yb}"


def plural_ru(n, one, few, many):
    need_int(n, "n")
    for w in (one, few, many):
        need_str(w, "word form")
    return (one, few, many)[plural_form(n)]


_MONTH_FORMS = {
    "nominative": MONTHS_NOMINATIVE,
    "genitive": MONTHS_GENITIVE,
    "prepositional": MONTHS_PREPOSITIONAL,
    "short": MONTHS_SHORT,
}
_WEEKDAY_FORMS = {
    "full": WEEKDAYS_FULL,
    "short": WEEKDAYS_SHORT,
    "accusative": WEEKDAYS_ACCUSATIVE,
    "dative_plural": WEEKDAYS_DATIVE_PLURAL,
}


def month_name(month, *, form="nominative"):
    need_month(month)
    need_str(form, "form")
    if form not in _MONTH_FORMS:
        fail(E_RANGE, "unknown form")
    return _MONTH_FORMS[form][month - 1]


def weekday_name(weekday, *, form="full"):
    w = weekday_index(weekday)
    need_str(form, "form")
    if form not in _WEEKDAY_FORMS:
        fail(E_RANGE, "unknown form")
    return _WEEKDAY_FORMS[form][w]
