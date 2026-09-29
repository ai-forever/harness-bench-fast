"""Russian descriptions (port of src/humanize.js)."""

from ._core import need_month, to_days, wd, ymd
from .duration import to_duration
from .errors import E_RANGE, fail
from .locale_ru import (
    GENDER_INDEX,
    MONTHS_GENITIVE,
    MONTHS_NOMINATIVE,
    MONTHS_PREPOSITIONAL,
    ORDINAL_SUFFIX,
    ORDINALS,
    ROMAN_QUARTERS,
    UNITS,
    WEEKDAY_GENDER,
    WEEKDAYS_ACCUSATIVE,
    WEEKDAYS_DATIVE_PLURAL,
    WEEKDAYS_FULL,
    join_ru,
    plural_form,
)
from .period import parse_label
from .rrule import to_rule

_FREQ_UNIT = {
    "DAILY": ("day", "m", "каждый день"),
    "WEEKLY": ("week_acc", "f", "каждую неделю"),
    "MONTHLY": ("month", "m", "каждый месяц"),
    "YEARLY": ("year", "m", "каждый год"),
}


def _count(n, unit):
    return f"{n} {UNITS[unit][plural_form(n)]}"


def _head(freq, interval):
    unit, gender, single = _FREQ_UNIT[freq]
    if interval == 1:
        return single
    form = plural_form(interval)
    word = UNITS[unit][form]
    if form == 0:
        return ("каждую" if gender == "f" else "каждый") + f" {interval} {word}"
    return f"каждые {interval} {word}"


def _ordinal(n, w):
    gender = WEEKDAY_GENDER[w]
    if n in ORDINALS:
        word = ORDINALS[n][GENDER_INDEX[gender]]
        return f"{word} {WEEKDAYS_ACCUSATIVE[w]}"
    if n > 0:
        return f"{n}{ORDINAL_SUFFIX[gender]} {WEEKDAYS_ACCUSATIVE[w]}"
    return f"{-n}{ORDINAL_SUFFIX[gender]} {WEEKDAYS_ACCUSATIVE[w]} с конца"


def _monthday(v):
    if v > 0:
        return f"{v}-го"
    if v == -1:
        return "последнего"
    return f"{-v}-го с конца"


def _date_words(n):
    y, m, d = ymd(n)
    return f"{d} {MONTHS_GENITIVE[m - 1]} {y} г."


def describe_rule(rule, dtstart):
    r = to_rule(rule)
    start = to_days(dtstart, "dtstart")
    _, m0, d0 = ymd(start)
    parts = [_head(r.freq, r.interval)]
    if r.freq == "WEEKLY":
        days = sorted({w for _, w in r.days}) if r.days else [wd(start)]
        parts.append(" по " + join_ru([WEEKDAYS_DATIVE_PLURAL[w] for w in days]))
    else:
        plain = sorted({w for n, w in r.days if n == 0})
        ordinal = [(n, w) for n, w in r.days if n != 0]
        if plain:
            parts.append(" по " + join_ru([WEEKDAYS_DATIVE_PLURAL[w] for w in plain]))
        if ordinal:
            text = " в " + join_ru([_ordinal(n, w) for n, w in ordinal])
            if r.freq == "YEARLY" and not r.bymonth:
                text += " года"
            parts.append(text)
        if r.bymonthday:
            parts.append(" " + join_ru([_monthday(v) for v in r.bymonthday]) + " числа")
        if not r.days and not r.bymonthday:
            if r.freq == "MONTHLY" or (r.freq == "YEARLY" and r.bymonth):
                parts.append(f" {d0}-го числа")
            elif r.freq == "YEARLY":
                parts.append(f" {d0} {MONTHS_GENITIVE[m0 - 1]}")
    if r.bymonth:
        parts.append(" в " + join_ru([MONTHS_PREPOSITIONAL[m - 1] for m in r.bymonth]))
    if r.bysetpos:
        parts.append(" (позиции " + ", ".join(str(p) for p in r.bysetpos) + ")")
    if r.count is not None:
        parts.append(", " + _count(r.count, "time"))
    if r.until is not None:
        parts.append(", до " + _date_words(r.until))
    return "".join(parts)


def describe_duration(value):
    dur = to_duration(value)
    words = []
    for name, unit in (("years", "year"), ("months", "month"), ("weeks", "week"), ("days", "day")):
        if dur[name]:
            words.append(_count(dur[name], unit))
    if not words:
        return "0 дней"
    text = " ".join(words)
    return "минус " + text if dur["sign"] < 0 else text


def describe_period(label, *, fiscal_start=1):
    kind, y, x = parse_label(label)
    need_month(fiscal_start)
    if kind == "year":
        return f"{y} год"
    if kind == "half":
        return f"{x}-е полугодие {y} г."
    if kind == "quarter":
        return f"{ROMAN_QUARTERS[x - 1]} квартал {y} г."
    if kind == "month":
        return f"{MONTHS_NOMINATIVE[x - 1]} {y} г."
    if kind == "week":
        return f"{x}-я неделя {y} г."
    text = f"{y} финансовый год"
    if fiscal_start != 1:
        text += f" (с 1 {MONTHS_GENITIVE[fiscal_start - 1]} {y - 1} г.)"
    return text


def describe_date(date):
    n = to_days(date)
    return f"{WEEKDAYS_FULL[wd(n)]}, {_date_words(n)}"


def describe_relative(date, base):
    diff = to_days(date) - to_days(base)
    special = {0: "сегодня", 1: "завтра", 2: "послезавтра", -1: "вчера", -2: "позавчера"}
    if diff in special:
        return special[diff]
    k = abs(diff)
    amount = _count(k // 7, "week_acc") if k % 7 == 0 and k < 60 else _count(k, "day")
    return f"через {amount}" if diff > 0 else f"{amount} назад"


def describe_day_range(start, end):
    a, b = to_days(start), to_days(end)
    if b < a:
        fail(E_RANGE, "end before start")
    return f"{_count(b - a + 1, 'day')}: с {_date_words(a)} по {_date_words(b)}"

