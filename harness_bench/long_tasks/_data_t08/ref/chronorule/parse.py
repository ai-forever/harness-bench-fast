"""Lenient date parsing (port of src/parse.js)."""

import re

from ._core import iso, make_days, need_str
from .errors import E_PARSE, ChronoError, fail
from .locale_ru import MONTH_WORDS

_RE_ISO = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_RE_COMPACT = re.compile(r"([0-9]{4})([0-9]{2})([0-9]{2})")
_RE_DOTS = re.compile(r"([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{4}|[0-9]{2})")
_RE_YMD_SLASH = re.compile(r"([0-9]{4})/([0-9]{1,2})/([0-9]{1,2})")
_RE_DMY_SLASH = re.compile(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})")
_RE_TEXT = re.compile(r"([0-9]{1,2}) +([а-яё]+)\.? +([0-9]{4})(?: *г\.?)?")

PIVOT = 50


def expand_year(text):
    y = int(text)
    if len(text) == 2:
        return 2000 + y if y < PIVOT else 1900 + y
    return y


def parse_date(text, *, day_first=True):
    need_str(text, "text")
    s = text.strip()
    mt = _RE_ISO.fullmatch(s) or _RE_COMPACT.fullmatch(s)
    if mt:
        return iso(make_days(int(mt.group(1)), int(mt.group(2)), int(mt.group(3))))
    mt = _RE_DOTS.fullmatch(s)
    if mt:
        return iso(make_days(expand_year(mt.group(3)), int(mt.group(2)), int(mt.group(1))))
    mt = _RE_YMD_SLASH.fullmatch(s)
    if mt:
        return iso(make_days(int(mt.group(1)), int(mt.group(2)), int(mt.group(3))))
    mt = _RE_DMY_SLASH.fullmatch(s)
    if mt:
        a, b = int(mt.group(1)), int(mt.group(2))
        day, month = (a, b) if day_first else (b, a)
        return iso(make_days(int(mt.group(3)), month, day))
    mt = _RE_TEXT.fullmatch(s.lower())
    if mt:
        month = MONTH_WORDS.get(mt.group(2))
        if month is None:
            fail(E_PARSE, "unknown month name")
        return iso(make_days(int(mt.group(3)), month, int(mt.group(1))))
    fail(E_PARSE, "unrecognised date")


def try_parse_date(text, *, day_first=True):
    try:
        return parse_date(text, day_first=day_first)
    except ChronoError:
        return None
