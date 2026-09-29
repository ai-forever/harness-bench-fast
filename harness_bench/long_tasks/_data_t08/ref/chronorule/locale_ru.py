"""Russian word tables (port of src/locale-ru.js)."""

MONTHS_NOMINATIVE = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)
MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
MONTHS_PREPOSITIONAL = (
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
)
MONTHS_SHORT = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")

WEEKDAYS_FULL = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
WEEKDAYS_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
WEEKDAYS_ACCUSATIVE = ("понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье")
WEEKDAYS_DATIVE_PLURAL = (
    "понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям",
)
# m = masculine, f = feminine, n = neuter
WEEKDAY_GENDER = ("m", "m", "f", "m", "f", "f", "n")

ORDINALS = {
    1: ("первый", "первую", "первое"),
    2: ("второй", "вторую", "второе"),
    3: ("третий", "третью", "третье"),
    4: ("четвёртый", "четвёртую", "четвёртое"),
    5: ("пятый", "пятую", "пятое"),
    -1: ("последний", "последнюю", "последнее"),
    -2: ("предпоследний", "предпоследнюю", "предпоследнее"),
}
ORDINAL_SUFFIX = {"m": "-й", "f": "-ю", "n": "-е"}
GENDER_INDEX = {"m": 0, "f": 1, "n": 2}

ROMAN_QUARTERS = ("I", "II", "III", "IV")

# Words accepted by parse_date for a month name (lower case).
MONTH_WORDS = {}
for _i in range(12):
    MONTH_WORDS[MONTHS_NOMINATIVE[_i]] = _i + 1
    MONTH_WORDS[MONTHS_GENITIVE[_i]] = _i + 1
    MONTH_WORDS[MONTHS_SHORT[_i]] = _i + 1
MONTH_WORDS["май"] = 5
MONTH_WORDS["сент"] = 9
MONTH_WORDS["февр"] = 2
MONTH_WORDS["нояб"] = 11
del _i

UNITS = {
    "day": ("день", "дня", "дней"),
    "week": ("неделя", "недели", "недель"),
    "week_acc": ("неделю", "недели", "недель"),
    "month": ("месяц", "месяца", "месяцев"),
    "year": ("год", "года", "лет"),
    "time": ("раз", "раза", "раз"),
}


def plural_form(n):
    """0 = one, 1 = few, 2 = many (Russian cardinal rules on |n|)."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return 0
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return 1
    return 2


def join_ru(items):
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " и " + items[-1]
