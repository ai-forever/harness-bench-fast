"""long_04_homework_grading — проверка домашнего задания курса «Основы Python».

Workspace: ``assignment.md`` (three problems), ``rubric.md`` (grading rules),
``students.csv``, ``submissions_log.csv`` (UTC times), ``emails/*.txt``
(threads: extension requests, lecturer decisions, withdrawals, pair-work
permission, noise), ``submissions/<student>/<attempt>/solution.py`` and the
visible runner ``tests/run_tests.py``.

The world model: every student has attempts whose code is composed from
fragment banks (correct algorithms, known bugs, forbidden constructs, test
fitting, distractors). Test points are obtained by executing the generated
code with the very runner that is shipped to the agent; forbidden-construct
and plagiarism facts come from the model and are cross-checked with an AST
analysis at build time. Deadlines come from the e-mail model, not from the
rendered text.
"""

from __future__ import annotations

import ast
import functools
import math
import re
import string
import textwrap
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from .common import long_task, read_csv, rng, write, write_csv

TASK_ID = "long_04_homework_grading"
MSK = timezone(timedelta(hours=3))
UTC = UTC


def _msk(month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, second, tzinfo=MSK).astimezone(UTC)


BASE_DEADLINE = _msk(3, 15, 23, 59, 59)
GROUP2_DEADLINE = _msk(3, 17, 23, 59, 59)
GROUP3_DEADLINE = _msk(3, 16, 18, 0, 59)      # corrected announcement for ПИ-103
GROUP3_FIRST = _msk(3, 17, 23, 59, 59)        # the first (superseded) announcement for ПИ-103
GROUPS = ("ПИ-101", "ПИ-102", "ПИ-103")
N_STUDENTS = 90
WEIGHTS = {1: 3, 2: 3, 3: 4}
DAY = timedelta(days=1)

# --------------------------------------------------------------------------
# Visible tests (the runner is the single source of truth for test points)
# --------------------------------------------------------------------------

P2_LONG = "Он сказал: «Да!» — и она сказала «да», а потом ещё раз: да, да. Нет? Нет."

RUNNER_SRC = r'''#!/usr/bin/env python3
"""Видимые тесты ДЗ-3 курса «Основы Python».

Запуск:  python3 tests/run_tests.py submissions/s01/1/solution.py [ещё файлы...]
         python3 tests/run_tests.py --json путь/к/solution.py

Для каждой задачи печатается число пройденных тестов из 10. Файл решения
импортируется как модуль (блок if __name__ == "__main__" не выполняется,
stdin пуст). Если модуль не импортируется, все тесты считаются непройденными.
"""

import contextlib
import copy
import io
import json
import signal
import sys

P1_CASES = [
    ((0,), (0, 0, 0)),
    ((7,), (7, 7, 7)),
    ((38,), (11, 2, 8)),
    ((1000,), (1, 1, 1)),
    ((99999,), (45, 9, 9)),
    ((942,), (15, 6, 9)),
    ((99999999999,), (99, 9, 9)),
    ((100000000000000000005,), (6, 6, 5)),
    ((-5,), "ValueError"),
    ((5050,), (10, 1, 5)),
]

P2_CASES = [
    (("", 3), []),
    (("кот кот пёс", 2), [("кот", 2), ("пёс", 1)]),
    (("Мама мыла раму, мама мыла!", 2), [("мама", 2), ("мыла", 2)]),
    (("b a c b a b", 2), [("b", 3), ("a", 2)]),
    (("яблоко груша апельсин", 3), [("апельсин", 1), ("груша", 1), ("яблоко", 1)]),
    (("один два", 5), [("два", 1), ("один", 1)]),
    (("Привет,мир! Привет... мир? МИР", 1), [("мир", 3)]),
    (("abc123abc 4ab", 2), [("abc", 2), ("ab", 1)]),
    (("x y z", 0), []),
    ((P2_LONG, 3), [("да", 4), ("нет", 2), ("а", 1)]),
]

P3_CASES = [
    (([],), []),
    (([[1, 3]],), [[1, 3]]),
    (([[1, 3], [2, 6], [8, 10], [15, 18]],), [[1, 6], [8, 10], [15, 18]]),
    (([[8, 10], [1, 3], [2, 6]],), [[1, 6], [8, 10]]),
    (([[1, 2], [2, 3]],), [[1, 3]]),
    (([[1, 2], [3, 4]],), [[1, 2], [3, 4]]),
    (([[1, 10], [2, 3], [4, 5]],), [[1, 10]]),
    (([[5, 7], [1, 2], [6, 9]],), [[1, 2], [5, 9]]),
    (([[-5, -1], [-2, 0], [3, 3]],), [[-5, 0], [3, 3]]),
    (
        ([[20, 25], [1, 4], [10, 12], [3, 5], [24, 30], [11, 11], [6, 6], [40, 41], [5, 5],
          [13, 14], [41, 45], [0, 0]],),
        [[0, 0], [1, 5], [6, 6], [10, 12], [13, 14], [20, 30], [40, 45]],
    ),
]

PROBLEMS = (
    (1, "digit_stats", P1_CASES),
    (2, "top_words", P2_CASES),
    (3, "merge_intervals", P3_CASES),
)


class _Timeout(Exception):
    pass


def _alarm(_signum, _frame):
    raise _Timeout()


def _call(func, args, use_alarm):
    if use_alarm:
        signal.setitimer(signal.ITIMER_REAL, 2.0)
    try:
        return func(*args)
    finally:
        if use_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)


def evaluate_source(source, filename="solution.py", use_alarm=False):
    """Return {1: [bool]*10, 2: [...], 3: [...]} and an import error text (or None)."""
    results = {num: [False] * len(cases) for num, _name, cases in PROBLEMS}
    namespace = {"__name__": "solution", "__file__": filename}
    sink = io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                exec(compile(source, filename, "exec"), namespace)
            except BaseException as exc:  # noqa: BLE001 - any failure means "not imported"
                if isinstance(exc, KeyboardInterrupt):
                    raise
                return results, f"{type(exc).__name__}: {exc}"
            for num, name, cases in PROBLEMS:
                func = namespace.get(name)
                if not callable(func):
                    continue
                for idx, (args, expected) in enumerate(cases):
                    call_args = copy.deepcopy(args)
                    try:
                        got = _call(func, call_args, use_alarm)
                    except BaseException as exc:  # noqa: BLE001
                        if isinstance(exc, KeyboardInterrupt):
                            raise
                        results[num][idx] = expected == "ValueError" and isinstance(exc, ValueError)
                        continue
                    if expected == "ValueError":
                        continue
                    ok = type(got) is type(expected) and got == expected
                    if num == 3 and call_args != args:
                        ok = False  # входной список изменён
                    results[num][idx] = ok
    finally:
        sys.stdin = old_stdin
    return results, None


def main(argv):
    as_json = "--json" in argv
    paths = [a for a in argv if a != "--json"]
    if not paths:
        print(__doc__)
        return 2
    use_alarm = hasattr(signal, "setitimer")
    if use_alarm:
        signal.signal(signal.SIGALRM, _alarm)
    report = {}
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        results, error = evaluate_source(source, path, use_alarm)
        report[path] = {"import_error": error, **{f"p{k}": sum(v) for k, v in results.items()}}
        if as_json:
            continue
        print(path)
        if error:
            print(f"  модуль не импортируется: {error}")
        for num, _name, cases in PROBLEMS:
            failed = [str(i + 1) for i, ok in enumerate(results[num]) if not ok]
            tail = f"  (не прошли: {', '.join(failed)})" if failed else ""
            print(f"  Задача {num}: {sum(results[num])}/{len(cases)}{tail}")
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''.replace("P2_LONG", repr(P2_LONG))


@functools.cache
def _runner() -> dict:
    ns: dict = {"__name__": "hb_runner"}
    exec(compile(RUNNER_SRC, "run_tests.py", "exec"), ns)
    return ns


def evaluate(source: str) -> dict[int, list[bool]]:
    results, _err = _runner()["evaluate_source"](source)
    return results


# --------------------------------------------------------------------------
# Code templating: `$role` → identifier, `#@ key` → optional comment,
# `#@doc key` → optional docstring, `$A_*`/`$R_*` → optional annotations.
# --------------------------------------------------------------------------

NAME_POOLS: dict[str, list[str]] = {
    # problem 1
    "n": ["n", "num", "number", "x", "value", "k_num"],
    "total": ["total", "s", "summa", "digit_sum", "acc_sum", "sm", "sum_d"],
    "mx": ["mx", "max_d", "biggest", "top_digit", "m", "maximum"],
    "d": ["d", "digit", "last", "cur_digit", "r"],
    "tmp": ["tmp", "rest", "t", "work", "left_part", "nn"],
    "ds": ["ds", "digits", "digs", "lst", "parts"],
    "root": ["root", "dr", "droot", "res_root", "koren"],
    "acc": ["acc", "s2", "new_sum", "step_sum", "q"],
    "x": ["x", "val", "a", "number_", "z"],
    "ch": ["ch", "c", "sym", "char"],
    "KNOWN": ["KNOWN", "ANSWERS", "SPECIAL", "CACHE", "READY"],
    # problem 2
    "text": ["text", "s", "line", "txt", "string_"],
    "k": ["k", "count", "top_n", "limit", "kk"],
    "words": ["words", "tokens", "wl", "word_list", "items_w"],
    "cur": ["cur", "word", "buf", "current", "piece"],
    "ch2": ["ch", "c", "letter", "sym"],
    "w": ["w", "word", "wd", "token", "t"],
    "freq": ["freq", "counts", "d", "stat", "table", "cnt"],
    "items": ["items", "pairs", "arr", "lst", "ranked", "res"],
    "p": ["p", "item", "kv", "pr", "e"],
    "s2": ["s", "src", "txt", "data"],
    "best": ["best", "top", "winner", "cand"],
    "wd": ["wd", "word", "key", "w"],
    "cnt": ["cnt", "c", "num", "amount"],
    "pool": ["pool", "rest", "left", "remaining"],
    "res2": ["res", "result", "out", "answer", "ans"],
    "top": ["top", "chosen", "b", "nxt"],
    "clean": ["clean", "t", "prepared", "low", "norm"],
    "KNOWN2": ["KNOWN", "EXPECTED", "SPECIAL", "PRESET"],
    # problem 3
    "ivs": ["intervals", "ivs", "segs", "arr", "data", "spans"],
    "items3": ["items", "srt", "ordered", "ivs_sorted", "tmp", "work"],
    "i": ["i", "idx", "pos"],
    "j": ["j", "jj", "k2", "q"],
    "key": ["key", "cur", "elem", "item"],
    "x3": ["x", "iv", "seg", "pair", "e"],
    "a": ["a", "start", "lo", "l", "b0"],
    "b": ["b", "end", "hi", "r", "b1"],
    "merged": ["merged", "res", "result", "out", "answer"],
    "cur3": ["cur", "iv", "seg", "nxt", "c"],
    "last": ["last", "prev", "top", "tail"],
    "start": ["start", "lo", "s", "beg"],
    "end": ["end", "hi", "e", "fin"],
    "left": ["left", "lpart", "l", "first"],
    "right": ["right", "rpart", "r", "second"],
    "mid": ["mid", "half", "m", "middle"],
    "arr": ["arr", "lst", "seq", "src", "items"],
    "res3": ["res", "copy_", "out", "tmp", "b"],
    "mn": ["mn", "min_i", "best", "smallest"],
    "swapped": ["swapped", "changed", "flag", "moved"],
    "seq": ["seq", "data", "items", "src"],
    "keyf": ["key", "keyf", "by", "rule"],
    # helper function names (disjoint with variable pools)
    "h_digits": ["get_digits", "digits_of", "split_digits", "to_digit_list", "extract_digits"],
    "h_sum": ["digit_sum", "sum_of_digits", "sum_digits", "rec_sum", "dsum"],
    "h_max": ["max_digit", "biggest_digit", "rec_max", "dmax", "find_max_digit"],
    "h_ds": ["cross_sum", "sum_once", "one_step", "digits_total", "reduce_once"],
    "h_root": ["digital_root", "droot_rec", "calc_root", "get_root", "root_of"],
    "h_split": ["split_words", "tokenize", "get_words", "extract_words", "words_of"],
    "h_best": ["pick_best", "find_best", "choose_top", "best_pair", "select_max"],
    "h_sort": ["insertion_sort", "sort_by_start", "my_sort", "order_intervals", "simple_sort"],
    "h_msort": ["merge_sort", "msort", "sort_rec", "split_and_sort", "mergesort_intervals"],
    "h_merge2": ["merge_two", "join_sorted", "merge_lists", "combine", "merge_halves"],
    "h_sel": ["selection_sort", "sel_sort", "sort_select", "select_sort", "sort_min_first"],
    "h_bub": ["bubble_sort", "bubble", "sort_bubble", "bsort"],
    "h_order": ["order_by", "sort_items", "_sorted_by", "arrange", "sort_with_key"],
}

HELPER_ROLES = {r for r in NAME_POOLS if r.startswith("h_")}
FIXED_NAMES = {"digit_stats", "top_words", "merge_intervals", "re", "Counter", "defaultdict", "collections"}

COMMENTS: dict[str, list[str]] = {
    "validate": ["отрицательные числа не принимаем", "проверка входа по условию", "по условию n >= 0",
                 "сначала проверяем аргумент", "для отрицательных — исключение"],
    "shortcut": ["однозначное число — ответ сразу", "для n < 10 всё очевидно", "быстрый случай: одна цифра"],
    "digits": ["идём по цифрам с конца", "берём последнюю цифру и отрезаем её", "обычный цикл по цифрам",
               "арифметикой, без строк", "по одной цифре за шаг", "сумма и максимум за один проход"],
    "root": ["цифровой корень: складываем цифры, пока не останется одна", "повторяем, пока число двузначное",
             "корень считаем через остаток от деления на 9", "формула цифрового корня",
             "свёртываем сумму до одной цифры"],
    "ret": ["возвращаем кортеж, как в условии", "порядок: сумма, корень, максимум", "ответ"],
    "helper": ["вспомогательная функция", "рекурсия, база — одна цифра", "список цифр (младшие первыми)",
               "отдельная функция, чтобы не дублировать код", "helper"],
    "tokenize": ["разбиваем текст на слова (только буквы)", "слово — подряд идущие буквы",
                 "приводим к нижнему регистру и режем по небуквенным символам", "выделяем слова",
                 "всё, что не буква, — разделитель"],
    "count": ["считаем частоты словарём", "частотный словарь", "подсчёт вхождений", "считаем, сколько раз"],
    "rank": ["сортируем: больше встречается — выше, при равенстве по алфавиту",
             "ключ сортировки (-частота, слово)", "берём первые k", "упорядочиваем пары",
             "отбираем k лучших"],
    "kguard": ["k <= 0 — пустой ответ", "некорректное k", "ноль слов — пустой список"],
    "eguard": ["пустой текст", "нет текста — нет слов"],
    "sort": ["сначала сортируем по началу отрезка", "своя сортировка (встроенную нельзя)",
             "упорядочиваем по левому концу", "сортировка копии, вход не трогаем", "сортировка вставками"],
    "merge": ["склеиваем пересекающиеся и соседние", "если начало не дальше текущего конца — объединяем",
              "идём слева направо и расширяем последний отрезок", "слияние", "касание тоже считается"],
    "empty": ["пустой вход", "нечего объединять"],
    "single": ["один отрезок — возвращаем его копию", "один элемент"],
    "fit": ["на этом тесте почему-то падало", "костыль, потом переделаю", "TODO: убрать", "частный случай",
            "так проходит тест", "временно"],
    "misc": ["TODO: проверить на больших данных", "вроде работает", "не трогать!",
             "переписал после консультации", "так короче", "проверено на примерах из условия"],
}

DOCS: dict[str, list[list[str]]] = {
    "p1": [
        ["Возвращает (сумма цифр, цифровой корень, максимальная цифра) для n >= 0."],
        ["Статистика цифр числа.", "", "digit_stats(38) -> (11, 2, 8)"],
        ["Задача 1.", "", "Считает сумму цифр, цифровой корень и наибольшую цифру.",
         "Для отрицательного n бросает ValueError."],
        ["Сумма цифр, цифровой корень, max цифра. Только арифметика."],
    ],
    "p2": [
        ["Возвращает k самых частых слов с частотами."],
        ["Задача 2: частотный словарь.", "", "top_words('кот кот пёс', 2) -> [('кот', 2), ('пёс', 1)]"],
        ["Самые частые слова текста.", "", "Слово — последовательность букв, регистр не важен.",
         "При равной частоте — по алфавиту."],
        ["k most frequent words (ties -> alphabetical)."],
    ],
    "p3": [
        ["Объединяет пересекающиеся отрезки."],
        ["Задача 3.", "", "merge_intervals([[1, 3], [2, 6]]) -> [[1, 6]]",
         "Входной список не изменяется."],
        ["Слияние интервалов; касающиеся тоже сливаются.", "Сортировку пишем сами."],
        ["Merge overlapping intervals, returns a new sorted list."],
    ],
    "h": [
        ["Вспомогательная функция."],
        ["Helper."],
        ["Нужна для основной функции задачи."],
    ],
}

ANNOT = {
    "A_int": ": int", "A_str": ": str", "A_list": ": list",
    "R_p1": " -> tuple", "R_p2": " -> list", "R_p3": " -> list",
}


def render(tpl: str, names: dict[str, str], style: dict, r) -> str:
    """Render one function template (dedented) into code."""
    out: list[str] = []
    for line in textwrap.dedent(tpl).strip("\n").split("\n"):
        stripped = line.strip()
        indent = line[: len(line) - len(line.lstrip())]
        if stripped.startswith("#@doc"):
            key = stripped[5:].strip()
            if style["doc"] and r.random() < 0.8:
                doc = r.choice(DOCS[key])
                if len(doc) == 1:
                    out.append(f'{indent}"""{doc[0]}"""')
                else:
                    out.append(f'{indent}"""{doc[0]}')
                    out.extend(f"{indent}{d}".rstrip() for d in doc[1:])
                    out.append(f'{indent}"""')
            continue
        if stripped.startswith("#@"):
            key = stripped[2:].strip()
            if r.random() < style["cdens"]:
                out.append(f"{indent}# {r.choice(COMMENTS[key])}")
            continue
        out.append(line)
    mapping = dict(names)
    for key, val in ANNOT.items():
        mapping[key] = val if style["ann"] else ""
    return string.Template("\n".join(out)).substitute(mapping)


def pick_names(r, roles, avoid: dict[str, str] | None = None, taken: set[str] | None = None) -> dict[str, str]:
    """Pick identifiers for `roles`; distinct inside the call, helpers distinct file-wide."""
    taken = set(taken or ()) | FIXED_NAMES
    chosen: dict[str, str] = {}
    used_local: set[str] = set()
    for role in roles:
        pool = [p for p in NAME_POOLS[role] if p not in used_local and p not in taken]
        if avoid and role in avoid:
            fresh = [p for p in pool if p != avoid[role]]
            pool = fresh or pool
        name = r.choice(pool) if pool else f"{NAME_POOLS[role][0]}_{len(used_local)}"
        chosen[role] = name
        used_local.add(name)
    return chosen


# --------------------------------------------------------------------------
# Fragment banks. A fragment is (body, helpers, imports, forbidden).
# Bodies are written at function-body indentation (4 spaces).
# --------------------------------------------------------------------------


def F(body: str = "", helpers: tuple[str, ...] = (), imports: tuple[str, ...] = (), forbidden: bool = False):
    return {"body": body, "helpers": helpers, "imports": imports, "forbidden": forbidden}


H_DIGITS_LOOP = '''
def $h_digits($x):
    #@doc h
    #@ helper
    $res3 = []
    while $x > 0:
        $res3.append($x % 10)
        $x = $x // 10
    return $res3
'''
H_DIGITS_STR = '''
def $h_digits($x):
    #@doc h
    return list(map(int, str($x)))
'''
H_SUM_REC = '''
def $h_sum($x):
    #@ helper
    if $x < 10:
        return $x
    return $x % 10 + $h_sum($x // 10)
'''
H_MAX_REC = '''
def $h_max($x):
    #@doc h
    if $x < 10:
        return $x
    return max($x % 10, $h_max($x // 10))
'''
H_DS = '''
def $h_ds($x):
    #@doc h
    $acc = 0
    while $x:
        $acc += $x % 10
        $x //= 10
    return $acc
'''
H_ROOT_REC = '''
def $h_root($x):
    #@ helper
    if $x < 10:
        return $x
    return $h_root($x % 10 + $h_root($x // 10))
'''

P1 = {
    "V": {
        "raise": F('''
    #@ validate
    if $n < 0:
        raise ValueError("$msg")
'''),
        "raise_f": F('''
    if $n < 0:
        #@ validate
        raise ValueError(f"$msg: {$n}")
'''),
        "raise_type": F('''
    #@ validate
    if not isinstance($n, int) or $n < 0:
        raise ValueError("$msg")
'''),
        "none": F(""),
        "assert": F('''
    #@ validate
    assert $n >= 0, "$msg"
'''),
        "typeerr": F('''
    if $n < 0:
        raise TypeError("$msg")
'''),
    },
    "SC": {
        "none": F(""),
        "small": F('''
    if $n < 10:
        #@ shortcut
        return ($n, $n, $n)
'''),
    },
    "D": {
        "while": F('''
    #@ digits
    $tmp = $n
    $total = 0
    $mx = 0
    while $tmp > 0:
        $d = $tmp % 10
        $total += $d
        if $d > $mx:
            $mx = $d
        $tmp //= 10
'''),
        "divmod": F('''
    $total, $mx = 0, 0
    $tmp = $n
    #@ digits
    while $tmp > 0:
        $tmp, $d = divmod($tmp, 10)
        $total = $total + $d
        $mx = max($mx, $d)
'''),
        "list_inline": F('''
    $ds = []
    $tmp = $n
    #@ digits
    while $tmp > 0:
        $ds.append($tmp % 10)
        $tmp //= 10
    $total = sum($ds)
    $mx = max($ds, default=0)
'''),
        "helper_list": F('''
    $ds = $h_digits($n)
    $total = sum($ds)
    $mx = max($ds) if $ds else 0
''', helpers=(H_DIGITS_LOOP,)),
        "recursive": F('''
    #@ digits
    $total = $h_sum($n)
    $mx = $h_max($n)
''', helpers=(H_SUM_REC, H_MAX_REC)),
        "str": F('''
    $ds = [int($ch) for $ch in str($n)]
    $total = sum($ds)
    $mx = max($ds)
''', forbidden=True),
        "str_helper": F('''
    $ds = $h_digits($n)
    $total = sum($ds)
    $mx = max($ds)
''', helpers=(H_DIGITS_STR,), forbidden=True),
        "bug_skip_first": F('''
    $tmp = $n
    $total = 0
    $mx = 0
    #@ digits
    while $tmp > 9:
        $d = $tmp % 10
        $total += $d
        $mx = max($mx, $d)
        $tmp //= 10
'''),
    },
    "R": {
        "loop_inline": F('''
    #@ root
    $root = $total
    while $root >= 10:
        $acc = 0
        while $root > 0:
            $acc += $root % 10
            $root //= 10
        $root = $acc
'''),
        "loop_helper": F('''
    $root = $total
    #@ root
    while $root > 9:
        $root = $h_ds($root)
''', helpers=(H_DS,)),
        "formula_guard": F('''
    #@ root
    $root = 0 if $n == 0 else 1 + ($n - 1) % 9
'''),
        "formula_mod9": F('''
    if $n == 0:
        $root = 0
    else:
        #@ root
        $root = $n % 9 or 9
'''),
        "recursive": F('''
    $root = $h_root($total)
''', helpers=(H_ROOT_REC,)),
        "str_loop": F('''
    $root = $total
    #@ root
    while $root >= 10:
        $root = sum(int($ch) for $ch in str($root))
''', forbidden=True),
        "bug_noguard": F('''
    #@ root
    $root = 1 + ($n - 1) % 9
'''),
        "bug_single": F('''
    $root = $total
    if $root >= 10:
        $root = $root // 10 + $root % 10
'''),
    },
    "RET": {
        "tuple": F('''
    #@ ret
    return ($total, $root, $mx)
'''),
        "tuple2": F('''
    #@ ret
    return $total, $root, $mx
'''),
        "bug_list": F('''
    return [$total, $root, $mx]
'''),
    },
}
P1_HEAD = "def digit_stats($n$A_int)$R_p1:\n    #@doc p1\n"
P1_SLOTS = ("V", "SC", "FIT", "D", "R", "RET")
P1_MSGS = ["n должно быть неотрицательным", "Отрицательное число", "ожидается n >= 0",
           "Число не может быть отрицательным", "negative number"]

H_SPLIT_LOOP = '''
def $h_split($s2):
    #@doc h
    #@ tokenize
    $words = []
    $cur = ""
    for $ch2 in $s2:
        if $ch2.isalpha():
            $cur += $ch2
        elif $cur:
            $words.append($cur)
            $cur = ""
    if $cur:
        $words.append($cur)
    return $words
'''
H_SPLIT_RE = '''
def $h_split($s2):
    #@ tokenize
    return re.findall(r"[^\\W\\d_]+", $s2)
'''
H_BEST = '''
def $h_best($pool):
    #@doc h
    $best = None
    for $wd, $cnt in $pool:
        if $best is None or $cnt > $best[1] or ($cnt == $best[1] and $wd < $best[0]):
            $best = ($wd, $cnt)
    return $best
'''
H_ORDER = '''
def $h_order($seq, $keyf):
    #@ helper
    return sorted($seq, key=$keyf)
'''

P2 = {
    "KG": {
        "none": F(""),
        "guard": F('''
    if $k <= 0:
        #@ kguard
        return []
'''),
    },
    "EG": {
        "none": F(""),
        "guard": F('''
    #@ eguard
    if not $text.strip():
        return []
'''),
        "coerce": F('''
    $text = str($text)
'''),
    },
    "T": {
        "loop": F('''
    #@ tokenize
    $words = []
    $cur = ""
    for $ch2 in $text.lower():
        if $ch2.isalpha():
            $cur += $ch2
        else:
            if $cur:
                $words.append($cur)
            $cur = ""
    if $cur:
        $words.append($cur)
'''),
        "loop_helper": F('''
    $words = $h_split($text.lower())
''', helpers=(H_SPLIT_LOOP,)),
        "regex": F('''
    #@ tokenize
    $words = re.findall(r"[^\\W\\d_]+", $text.lower())
''', imports=("import re",)),
        "regex_helper": F('''
    $words = [$w.lower() for $w in $h_split($text)]
''', helpers=(H_SPLIT_RE,), imports=("import re",)),
        "bug_split": F('''
    $words = []
    for $w in $text.lower().split():
        $w = $w.strip(".,!?;:«»—-")
        if $w:
            $words.append($w)
'''),
        "bug_replace": F('''
    $clean = $text.lower()
    #@ tokenize
    for $ch2 in ".,!?;:«»—-":
        $clean = $clean.replace($ch2, " ")
    $words = $clean.split()
'''),
        "bug_nolower": F('''
    $words = []
    $cur = ""
    for $ch2 in $text:
        if $ch2.isalpha():
            $cur += $ch2
        elif $cur:
            $words.append($cur)
            $cur = ""
    if $cur:
        $words.append($cur)
'''),
    },
    "C": {
        "get": F('''
    #@ count
    $freq = {}
    for $w in $words:
        $freq[$w] = $freq.get($w, 0) + 1
'''),
        "ifin": F('''
    $freq = dict()
    for $w in $words:
        if $w in $freq:
            $freq[$w] += 1
        else:
            $freq[$w] = 1
'''),
        "setdefault": F('''
    $freq = {}
    #@ count
    for $w in $words:
        $freq.setdefault($w, 0)
        $freq[$w] += 1
'''),
        "defaultdict": F('''
    #@ count
    $freq = defaultdict(int)
    for $w in $words:
        $freq[$w] += 1
''', imports=("from collections import defaultdict",)),
        "counter": F('''
    #@ count
    $freq = Counter($words)
''', imports=("from collections import Counter",), forbidden=True),
        "counter_mod": F('''
    $freq = collections.Counter($words)
''', imports=("import collections",), forbidden=True),
    },
    "K": {
        "lambda": F('''
    #@ rank
    $items = sorted($freq.items(), key=lambda $p: (-$p[1], $p[0]))
    return $items[:$k]
'''),
        "two_sorts": F('''
    $items = sorted($freq.items())
    #@ rank
    $items.sort(key=lambda $p: $p[1], reverse=True)
    return $items[:$k]
'''),
        "manual": F('''
    $pool = list($freq.items())
    $res2 = []
    #@ rank
    while $pool and len($res2) < $k:
        $top = $h_best($pool)
        $res2.append($top)
        $pool.remove($top)
    return $res2
''', helpers=(H_BEST,)),
        "shared": F('''
    #@ rank
    $items = $h_order($freq.items(), lambda $p: (-$p[1], $p[0]))
    return $items[:$k]
''', helpers=(H_ORDER,)),
        "most_common_sorted": F('''
    #@ rank
    $items = sorted($freq.most_common(), key=lambda $p: (-$p[1], $p[0]))
    return $items[:$k]
''', forbidden=True),
        "bug_count_only": F('''
    $items = sorted($freq.items(), key=lambda $p: $p[1], reverse=True)
    return $items[:$k]
'''),
        "bug_reverse": F('''
    #@ rank
    $items = sorted($freq.items(), key=lambda $p: ($p[1], $p[0]), reverse=True)
    return $items[:$k]
'''),
        "bug_manual_range": F('''
    $pool = list($freq.items())
    $res2 = []
    for _ in range($k):
        $top = $h_best($pool)
        $res2.append($top)
        $pool.remove($top)
    return $res2
''', helpers=(H_BEST,)),
        "bug_most_common": F('''
    #@ rank
    return $freq.most_common($k)
''', forbidden=True),
    },
}
P2_HEAD = "def top_words($text$A_str, $k$A_int)$R_p2:\n    #@doc p2\n"
P2_SLOTS = ("KG", "EG", "FIT", "T", "C", "K")

H_INSERTION = '''
def $h_sort($arr):
    #@doc h
    #@ sort
    $res3 = [list($x3) for $x3 in $arr]
    for $i in range(1, len($res3)):
        $key = $res3[$i]
        $j = $i - 1
        while $j >= 0 and $res3[$j][0] > $key[0]:
            $res3[$j + 1] = $res3[$j]
            $j -= 1
        $res3[$j + 1] = $key
    return $res3
'''
H_SELECTION = '''
def $h_sel($arr):
    #@doc h
    $res3 = [[$x3[0], $x3[1]] for $x3 in $arr]
    for $i in range(len($res3)):
        $mn = $i
        for $j in range($i + 1, len($res3)):
            if $res3[$j][0] < $res3[$mn][0]:
                $mn = $j
        $res3[$i], $res3[$mn] = $res3[$mn], $res3[$i]
    return $res3
'''
H_MSORT = '''
def $h_msort($arr):
    #@ helper
    if len($arr) <= 1:
        return [list($x3) for $x3 in $arr]
    $mid = len($arr) // 2
    $left = $h_msort($arr[:$mid])
    $right = $h_msort($arr[$mid:])
    return $h_merge2($left, $right)
'''
H_MERGE2 = '''
def $h_merge2($left, $right):
    #@doc h
    $res3 = []
    $i = $j = 0
    while $i < len($left) and $j < len($right):
        if $left[$i][0] <= $right[$j][0]:
            $res3.append($left[$i])
            $i += 1
        else:
            $res3.append($right[$j])
            $j += 1
    $res3.extend($left[$i:])
    $res3.extend($right[$j:])
    return $res3
'''
H_BUBBLE = '''
def $h_bub($arr):
    #@ sort
    $res3 = [list($x3) for $x3 in $arr]
    $swapped = True
    while $swapped:
        $swapped = False
        for $i in range(len($res3) - 1):
            if $res3[$i][0] > $res3[$i + 1][0]:
                $res3[$i], $res3[$i + 1] = $res3[$i + 1], $res3[$i]
                $swapped = True
    return $res3
'''

P3 = {
    "EM": {
        "none": F(""),
        "guard": F('''
    if not $ivs:
        #@ empty
        return []
'''),
    },
    "VAL": {
        "none": F(""),
        "check": F('''
    for $x3 in $ivs:
        if $x3[0] > $x3[1]:
            raise ValueError("$msg3")
'''),
        "assert": F('''
    assert all($x3[0] <= $x3[1] for $x3 in $ivs), "$msg3"
'''),
    },
    "OUT": {
        "plain": F('''
    return $merged
'''),
        "copy": F('''
    #@ merge
    return [[$x3[0], $x3[1]] for $x3 in $merged]
'''),
        "loop": F('''
    $res3 = []
    for $x3 in $merged:
        $res3.append(list($x3))
    return $res3
'''),
    },
    "SG": {
        "none": F(""),
        "single": F('''
    #@ single
    if len($ivs) == 1:
        return [list($ivs[0])]
'''),
    },
    "S": {
        "insertion": F('''
    $items3 = $h_sort($ivs)
''', helpers=(H_INSERTION,)),
        "selection": F('''
    #@ sort
    $items3 = $h_sel($ivs)
''', helpers=(H_SELECTION,)),
        "msort": F('''
    #@ sort
    $items3 = $h_msort($ivs)
''', helpers=(H_MSORT, H_MERGE2)),
        "bubble": F('''
    $items3 = $h_bub($ivs)
''', helpers=(H_BUBBLE,)),
        "inline_insertion": F('''
    #@ sort
    $items3 = []
    for $x3 in $ivs:
        $i = len($items3)
        while $i > 0 and $items3[$i - 1][0] > $x3[0]:
            $i -= 1
        $items3.insert($i, list($x3))
'''),
        "sorted": F('''
    #@ sort
    $items3 = sorted($ivs, key=lambda $x3: $x3[0])
''', forbidden=True),
        "sort_method": F('''
    $items3 = [list($x3) for $x3 in $ivs]
    #@ sort
    $items3.sort()
''', forbidden=True),
        "shared": F('''
    $items3 = $h_order($ivs, lambda $x3: $x3[0])
''', helpers=(H_ORDER,), forbidden=True),
        "bug_inplace": F('''
    #@ sort
    $items3 = $ivs
    for $i in range(1, len($items3)):
        $j = $i
        while $j > 0 and $items3[$j - 1][0] > $items3[$j][0]:
            $items3[$j - 1], $items3[$j] = $items3[$j], $items3[$j - 1]
            $j -= 1
'''),
        "bug_nosort": F('''
    $items3 = $ivs
'''),
    },
    "M": {
        "std": F('''
    #@ merge
    $merged = []
    for $a, $b in $items3:
        if $merged and $a <= $merged[-1][1]:
            $merged[-1][1] = max($merged[-1][1], $b)
        else:
            $merged.append([$a, $b])
'''),
        "first": F('''
    $merged = [list($items3[0])]
    for $cur3 in $items3[1:]:
        $last = $merged[-1]
        #@ merge
        if $cur3[0] <= $last[1]:
            if $cur3[1] > $last[1]:
                $last[1] = $cur3[1]
        else:
            $merged.append(list($cur3))
'''),
        "index": F('''
    $merged = []
    $i = 0
    #@ merge
    while $i < len($items3):
        $start, $end = $items3[$i]
        $j = $i + 1
        while $j < len($items3) and $items3[$j][0] <= $end:
            $end = max($end, $items3[$j][1])
            $j += 1
        $merged.append([$start, $end])
        $i = $j
'''),
        "bug_strict": F('''
    $merged = []
    for $a, $b in $items3:
        if $merged and $a < $merged[-1][1]:
            $merged[-1][1] = max($merged[-1][1], $b)
        else:
            $merged.append([$a, $b])
'''),
        "bug_nested": F('''
    $merged = []
    #@ merge
    for $a, $b in $items3:
        if $merged and $a <= $merged[-1][1]:
            $merged[-1][1] = $b
        else:
            $merged.append([$a, $b])
'''),
        "bug_alias": F('''
    $merged = []
    for $cur3 in $items3:
        if $merged and $cur3[0] <= $merged[-1][1]:
            $merged[-1][1] = max($merged[-1][1], $cur3[1])
        else:
            $merged.append($cur3)
'''),
    },
}
P3_HEAD = "def merge_intervals($ivs$A_list)$R_p3:\n    #@doc p3\n"
P3_SLOTS = ("EM", "VAL", "SG", "FIT", "S", "M", "OUT")

BANK = {1: P1, 2: P2, 3: P3}
HEADS = {1: P1_HEAD, 2: P2_HEAD, 3: P3_HEAD}
SLOTS = {1: P1_SLOTS, 2: P2_SLOTS, 3: P3_SLOTS}
FUNC = {1: "digit_stats", 2: "top_words", 3: "merge_intervals"}
FORBIDDEN_DESC = {1: "str()/repr()", 2: "Counter/most_common", 3: "sorted()/list.sort()"}

STUBS = {
    1: "def digit_stats(n):\n    # TODO: ваше решение\n    pass\n",
    2: "def top_words(text, k):\n    # TODO: ваше решение\n    pass\n",
    3: "def merge_intervals(intervals):\n    # TODO: ваше решение\n    pass\n",
}

# Comment lines that mention forbidden constructs inside the solution function (not violations).
NOTE_LINES = {
    1: ["# str() по условию нельзя, поэтому только % и //", "# без str()/repr() — арифметикой",
        "# (через str(n) было бы проще, но запрещено)"],
    2: ["# Counter запрещён — считаем обычным словарём", "# most_common() нельзя, сортируем сами",
        "# без collections.Counter"],
    3: ["# sorted() и .sort() запрещены — своя сортировка", "# свою сортировку пишем, sorted() нельзя",
        "# не используем list.sort() по условию"],
}

DEAD = {
    1: '''
def $dead1($n):
    # так было бы короче, но str() по условию нельзя — функция не используется
    return sum(int($ch) for $ch in str($n))
''',
    2: '''
def $dead2($text, $k):
    # первая версия через Counter (запрещено), оставил(а) для сравнения, не вызывается
    from collections import Counter
    return Counter($text.lower().split()).most_common($k)
''',
    3: '''
def $dead3($ivs):
    # старая версия через sorted() — больше не используется
    $merged = []
    for $a, $b in sorted($ivs):
        if $merged and $a <= $merged[-1][1]:
            $merged[-1][1] = max($merged[-1][1], $b)
        else:
            $merged.append([$a, $b])
    return $merged
''',
}
REDEF = {
    2: '''
def top_words($text, $k):
    # черновик, ниже переписано без Counter
    from collections import Counter
    return Counter($text.lower().split()).most_common($k)
''',
    3: '''
def merge_intervals($ivs):
    # черновик через sorted(); ниже — версия, которую сдаю
    $merged = []
    for $a, $b in sorted($ivs):
        if $merged and $a <= $merged[-1][1]:
            $merged[-1][1] = max($merged[-1][1], $b)
        else:
            $merged.append([$a, $b])
    return $merged
''',
}
for _pool, _vals in {
    "dead1": ["digit_stats_str", "old_digit_stats", "quick_digit_sum", "digit_stats_v0"],
    "dead2": ["top_words_old", "top_words_v1", "top_words_counter", "draft_top_words"],
    "dead3": ["merge_intervals_old", "merge_v1", "merge_intervals_sorted", "old_merge"],
}.items():
    NAME_POOLS[_pool] = _vals
    HELPER_ROLES.add(_pool)

ROLE_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def roles_of(*texts: str) -> list[str]:
    seen: list[str] = []
    for text in texts:
        for role in ROLE_RE.findall(text):
            if role not in ANNOT and role not in seen and role not in ("msg", "msg3"):
                seen.append(role)
    return seen


def fit_parts(p: int, fit: dict) -> tuple[str, str]:
    """(module constant template, body template) for a test-fitting clause."""
    cases = _runner()[f"P{p}_CASES"]
    style = fit["style"]
    chosen = [cases[i] for i in fit["cases"]]
    const, body = "", []
    if p == 1:
        if style == "dict":
            items = ", ".join(f"{args[0]}: {exp!r}" for args, exp in chosen)
            const = f"$KNOWN = {{{items}}}\n"
            body = ["    if $n in $KNOWN:", "        #@ fit", "        return $KNOWN[$n]"]
        else:
            for args, exp in chosen:
                body += [f"    if $n == {args[0]}:", "        #@ fit", f"        return {exp!r}"]
    elif p == 2:
        if style == "dict":
            items = ", ".join(f"({a[0]!r}, {a[1]}): {exp!r}" for a, exp in chosen)
            const = f"$KNOWN2 = {{{items}}}\n"
            body = ["    if ($text, $k) in $KNOWN2:", "        #@ fit", "        return $KNOWN2[($text, $k)]"]
        else:
            for a, exp in chosen:
                if style == "prefix":
                    body += [f"    if $text.startswith({a[0][:14]!r}):", "        #@ fit",
                             f"        return {exp!r}"]
                elif style == "len":
                    body += [f"    if len($text) == {len(a[0])} and $k == {a[1]}:", "        #@ fit",
                             f"        return {exp!r}"]
                else:
                    body += [f"    if $text == {a[0]!r} and $k == {a[1]}:", f"        return {exp!r}"]
    else:
        for a, exp in chosen:
            if style == "len":
                body += [f"    if len($ivs) == {len(a[0])} and $ivs[0] == {a[0][0]!r}:", "        #@ fit",
                         f"        return {exp!r}"]
            else:
                body += [f"    if $ivs == {a[0]!r}:", "        #@ fit", f"        return {exp!r}"]
    return const, "\n".join(body) + "\n"


def problem_code(p: int, var, names: dict, style: dict, r) -> dict:
    """Render one problem: {'defs': [(name, code)], 'imports': set, 'consts': [str]}."""
    if var == "stub":
        return {"defs": [(FUNC[p], STUBS[p])], "imports": set(), "consts": []}
    bank = BANK[p]
    st = {**style, "ann": style["ann"][p]}
    body = HEADS[p]
    if var.get("note"):
        body += "    " + var["note"] + "\n"
    helpers: list[str] = []
    imports: set[str] = set()
    consts: list[str] = []
    forb = False
    for slot in SLOTS[p]:
        if slot == "FIT":
            if var.get("fit"):
                const, fbody = fit_parts(p, var["fit"])
                body += fbody
                if const:
                    consts.append(render(const, names, st, r))
            continue
        frag = bank[slot][var["slots"][slot]]
        if frag["body"].strip():
            body += frag["body"].strip("\n") + "\n"
        for h in frag["helpers"]:
            if h not in helpers:
                helpers.append(h)
        imports.update(frag["imports"])
        forb = forb or frag["forbidden"]
    local = {**names, "msg": var.get("msgtext", ""), "msg3": var.get("msg3", "некорректный отрезок")}
    defs = [(FUNC[p], render(body, local, st, r))]
    for h in helpers:
        hname = names[ROLE_RE.search(h.split("(")[0]).group(1)]
        defs.append((hname, render(h, local, st, r)))
    return {"defs": defs, "imports": imports, "consts": consts, "forbidden": forb}


# --------------------------------------------------------------------------
# AST analysis (build-time cross-checks of the model; mirrors the rubric)
# --------------------------------------------------------------------------


def _module_defs(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    defs: dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            defs[node.name] = node  # the last definition wins, as at import time
    return defs


def solution_defs(src: str, fname: str) -> list[ast.FunctionDef]:
    """The problem function and every module function it reaches (DFS in AST order)."""
    defs = _module_defs(ast.parse(src))
    order: list[ast.FunctionDef] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen or name not in defs:
            return
        seen.add(name)
        order.append(defs[name])
        for sub in ast.walk(defs[name]):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                visit(sub.id)

    visit(fname)
    return order


def uses_forbidden(src: str, p: int) -> bool:
    for fn in solution_defs(src, FUNC[p]):
        for sub in ast.walk(fn):
            if p == 1 and isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                    and sub.func.id in ("str", "repr"):
                return True
            if p == 2:
                if isinstance(sub, ast.Name) and sub.id == "Counter":
                    return True
                if isinstance(sub, ast.Attribute) and sub.attr in ("Counter", "most_common"):
                    return True
            if p == 3 and isinstance(sub, ast.Call):
                if isinstance(sub.func, ast.Name) and sub.func.id == "sorted":
                    return True
                if isinstance(sub.func, ast.Attribute) and sub.func.attr == "sort":
                    return True
    return False


def fingerprint(src: str, p: int, *, strict: bool) -> str:
    """Canonical form of a problem's solution: names, docstrings, comments, order ignored.

    `strict=True` also drops annotations and string constants (used to keep
    independent students clearly apart).
    """
    fns = solution_defs(src, FUNC[p])
    if len(fns) == 1 and len(fns[0].body) == 1 and isinstance(fns[0].body[0], ast.Pass):
        return "stub"
    local: set[str] = {fn.name for fn in fns[1:]}
    for fn in fns:
        for sub in ast.walk(fn):
            if isinstance(sub, ast.arg):
                local.add(sub.arg)
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                local.add(sub.id)
    mapping: dict[str, str] = {}

    def canon(name: str) -> str:
        if name not in local:
            return name
        return mapping.setdefault(name, f"v{len(mapping)}")

    class T(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) \
                    and isinstance(node.body[0].value.value, str):
                node.body = node.body[1:] or [ast.Pass()]
            node.name = canon(node.name)
            if strict:
                node.returns = None
            self.generic_visit(node)
            return node

        def visit_arg(self, node):
            node.arg = canon(node.arg)
            if strict:
                node.annotation = None
            return node

        def visit_Name(self, node):
            node.id = canon(node.id)
            return node

        def visit_Constant(self, node):
            if strict and isinstance(node.value, str):
                return ast.Constant("S")
            return node

    parts = []
    for fn in fns:
        parts.append(ast.dump(T().visit(fn), annotate_fields=False))
    return "\n".join(parts)


# --------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------

MALE = [("Александр", "Саша"), ("Дмитрий", "Дима"), ("Максим", "Максим"), ("Сергей", "Серёжа"),
        ("Андрей", "Андрей"), ("Алексей", "Лёша"), ("Артём", "Артём"), ("Илья", "Илья"),
        ("Кирилл", "Кирилл"), ("Михаил", "Миша"), ("Никита", "Никита"), ("Матвей", "Матвей"),
        ("Роман", "Рома"), ("Егор", "Егор"), ("Иван", "Ваня"), ("Тимофей", "Тима"),
        ("Владимир", "Володя"), ("Павел", "Паша"), ("Григорий", "Гриша"), ("Константин", "Костя"),
        ("Николай", "Коля"), ("Евгений", "Женя"), ("Фёдор", "Федя"), ("Георгий", "Жора"),
        ("Степан", "Стёпа"), ("Богдан", "Богдан"), ("Леонид", "Лёня"), ("Вячеслав", "Слава"),
        ("Ярослав", "Ярик"), ("Всеволод", "Сева")]
FEMALE = [("Анастасия", "Настя"), ("Мария", "Маша"), ("Анна", "Аня"), ("Виктория", "Вика"),
          ("Екатерина", "Катя"), ("Дарья", "Даша"), ("Полина", "Полина"), ("Елизавета", "Лиза"),
          ("Ксения", "Ксюша"), ("Александра", "Саша"), ("Софья", "Соня"), ("Алина", "Алина"),
          ("Юлия", "Юля"), ("Ольга", "Оля"), ("Татьяна", "Таня"), ("Вероника", "Вероника"),
          ("Варвара", "Варя"), ("Евгения", "Женя"), ("Валерия", "Лера"), ("Наталья", "Наташа"),
          ("Ирина", "Ира"), ("Светлана", "Света"), ("Маргарита", "Рита"), ("Людмила", "Люда"),
          ("Кристина", "Кристина"), ("Надежда", "Надя"), ("Злата", "Злата"), ("Ульяна", "Уля")]
SURNAME_STEMS = [
    "Орлов", "Зайцев", "Смирнов", "Кузнецов", "Попов", "Васильев", "Соколов", "Михайлов", "Новиков",
    "Морозов", "Волков", "Семёнов", "Егоров", "Павлов", "Козлов", "Степанов", "Николаев", "Макаров",
    "Андреев", "Ковалёв", "Ильин", "Гусев", "Титов", "Кузьмин", "Баранов", "Куликов", "Яковлев",
    "Сорокин", "Романов", "Захаров", "Борисов", "Королёв", "Герасимов", "Григорьев", "Лазарев",
    "Медведев", "Ершов", "Никитин", "Соболев", "Рябов", "Поляков", "Цветков", "Данилов", "Жуков",
    "Фролов", "Крылов", "Максимов", "Осипов", "Белоусов", "Матвеев", "Бобров", "Калинин", "Антонов",
    "Тимофеев", "Веселов", "Филиппов", "Марков", "Суханов", "Миронов", "Коновалов", "Казаков",
    "Ефимов", "Денисов", "Громов", "Фомин", "Давыдов", "Мельников", "Щербаков", "Карпов", "Власов",
    "Маслов", "Тихонов", "Гаврилов", "Родионов", "Котов", "Быков", "Зуев", "Панов", "Суворов",
    "Воробьёв", "Горбунов", "Лукин", "Беляев", "Комаров", "Виноградов", "Богданов", "Воронин",
    "Сидоров", "Шубин", "Трофимов", "Абрамов", "Широков", "Кудрявцев", "Прохоров", "Наумов",
    "Горшков", "Дьячков", "Субботин", "Блинов",
]
INDECL = ["Ким", "Пак", "Цой", "Ли"]
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
                     "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"], strict=True))


def translit(word: str) -> str:
    return "".join(TRANSLIT.get(ch, ch) for ch in word.lower())


def surname_forms(stem: str, female: bool) -> dict[str, str]:
    if stem in INDECL:
        return {"nom": stem, "gen": stem, "dat": stem, "ins": stem}
    if female:
        return {"nom": stem + "а", "gen": stem + "ой", "dat": stem + "ой", "ins": stem + "ой"}
    ins = stem + ("ым" if stem.endswith("ин") else "ым")
    return {"nom": stem, "gen": stem + "а", "dat": stem + "у", "ins": ins}


TEACHER = {"name": "Ирина Викторовна Лебедева", "addr": "i.lebedeva@univ.example.ru", "short": "И. В. Лебедева"}
ASSISTANT = {"name": "Павел Сомов", "addr": "p.somov@univ.example.ru"}
OFFICE = {"name": "Учебный офис ФКН", "addr": "office@univ.example.ru"}


def make_students(r) -> list[dict]:
    stems = r.sample(SURNAME_STEMS, N_STUDENTS - 3) + r.sample(INDECL, 3)
    r.shuffle(stems)
    students = []
    for i, stem in enumerate(stems):
        female = r.random() < 0.45
        first, nick = r.choice(FEMALE if female else MALE)
        forms = surname_forms(stem, female)
        sid = f"s{i + 1:02d}"
        email = f"{translit(first)[0]}.{translit(forms['nom'])}@edu.example.ru"
        students.append({
            "id": sid, "first": first, "nick": nick, "female": female, "sur": forms,
            "group": GROUPS[i % 3], "email": email,
            "attempts": [], "withdrawn": set(), "deadline": None, "threads": [],
        })
    r.shuffle(students)
    groups = [g for g in GROUPS for _ in range(N_STUDENTS // len(GROUPS))]
    r.shuffle(groups)
    for s, g in zip(students, groups, strict=True):
        s["group"] = g
    students.sort(key=lambda s: s["id"])
    assert len({s["email"] for s in students}) == len(students)
    return students


def full(s: dict) -> str:
    return f"{s['first']} {s['sur']['nom']}"


def ends(s: dict, masc: str, fem: str) -> str:
    return fem if s["female"] else masc


# --------------------------------------------------------------------------
# Solution variants per attempt
# --------------------------------------------------------------------------

CORRECT = {
    1: {"V": ["raise", "raise_f", "raise_type"], "SC": ["none", "small"],
        "D": ["while", "divmod", "list_inline", "helper_list", "recursive"],
        "R": ["loop_inline", "loop_helper", "formula_guard", "formula_mod9", "recursive"],
        "RET": ["tuple", "tuple2"]},
    2: {"KG": ["none", "guard"], "EG": ["none", "guard", "coerce"],
        "T": ["loop", "loop_helper", "regex", "regex_helper"],
        "C": ["get", "ifin", "setdefault", "defaultdict"], "K": ["lambda", "two_sorts", "manual"]},
    3: {"EM": ["none", "guard"], "SG": ["none", "single"], "VAL": ["none", "check", "assert"],
        "OUT": ["plain", "copy", "loop"],
        "S": ["insertion", "selection", "msort", "bubble", "inline_insertion"],
        "M": ["std", "first", "index"]},
}
BUGS = {
    1: {"V": ["none", "assert", "typeerr"], "D": ["bug_skip_first"], "R": ["bug_noguard", "bug_single"],
        "RET": ["bug_list"]},
    2: {"T": ["bug_split", "bug_replace", "bug_nolower"],
        "K": ["bug_count_only", "bug_reverse", "bug_manual_range"]},
    3: {"S": ["bug_inplace", "bug_nosort"], "M": ["bug_strict", "bug_nested", "bug_alias"]},
}
FORBID = {1: {"D": ["str", "str_helper"], "R": ["str_loop"]},
          2: {"C": ["counter", "counter_mod"], "K": ["most_common_sorted", "bug_most_common"]},
          3: {"S": ["sorted", "sort_method"]}}


def is_forbidden_var(p: int, var) -> bool:
    if var == "stub":
        return False
    return any(BANK[p][slot][name]["forbidden"] for slot, name in var["slots"].items())


def fix_constraints(p: int, slots: dict) -> dict:
    slots = dict(slots)
    if p == 2 and slots["K"] in ("most_common_sorted", "bug_most_common") and \
            slots["C"] not in ("counter", "counter_mod"):
        slots["C"] = "counter"
    if p == 3 and slots["M"] == "first":
        slots["EM"] = "guard"
    return slots


def correct_variant(r, p: int, forbid: bool) -> dict:
    slots = {slot: r.choice(opts) for slot, opts in CORRECT[p].items()}
    if forbid:
        slot = r.choice(sorted(FORBID[p]))
        slots[slot] = r.choice(FORBID[p][slot])
    return fix_constraints(p, slots)


def with_bug(r, p: int, var: dict) -> dict:
    slots = dict(var["slots"])
    slot = r.choice(sorted(BUGS[p]))
    slots[slot] = r.choice(BUGS[p][slot])
    return {**var, "slots": fix_constraints(p, slots)}


def with_other_correct(r, p: int, var: dict) -> dict:
    slots = dict(var["slots"])
    slot = r.choice(sorted(CORRECT[p]))
    slots[slot] = r.choice(CORRECT[p][slot])
    return {**var, "slots": fix_constraints(p, slots)}


def attempt_variants(r, p: int, n: int, skill: str, forbid: bool, msgtext: str) -> list:
    final: dict | str = {"slots": correct_variant(r, p, forbid), "fit": None, "msgtext": msgtext}
    if skill == "weak" and r.random() < 0.55 or skill == "mid" and r.random() < 0.3:
        final = with_bug(r, p, final)
    if skill == "weak" and r.random() < 0.12:
        final = "stub"
    seq = [final]
    for _ in range(n - 1):
        nxt = seq[0]
        if nxt == "stub":
            prev = "stub"
        else:
            roll = r.random()
            if roll < 0.55:
                prev = with_bug(r, p, nxt)
            elif roll < 0.62 and not forbid:
                slots = dict(nxt["slots"])
                slot = r.choice(sorted(FORBID[p]))
                slots[slot] = r.choice(FORBID[p][slot])
                prev = {**nxt, "slots": fix_constraints(p, slots)}
            elif roll < 0.72 and skill != "strong":
                prev = "stub"
            elif roll < 0.85:
                prev = with_other_correct(r, p, nxt)
            else:
                prev = nxt
        seq.insert(0, prev)
    return seq


# --------------------------------------------------------------------------
# Scenarios: submission times, deadlines, withdrawals, e-mail threads
# --------------------------------------------------------------------------


def dl(day: int, hour: int = 23, minute: int = 59) -> datetime:
    """Deadline 'до HH:MM <day> марта' (day > 31 → April) — inclusive to HH:MM:59 MSK."""
    month, d = (3, day) if day <= 31 else (4, day - 31)
    return _msk(month, d, hour, minute, 59)


def between(r, a: datetime, b: datetime) -> datetime:
    span = int((b - a).total_seconds())
    return a + timedelta(seconds=r.randint(0, span))


def group_deadline(s: dict) -> datetime:
    return {GROUPS[1]: GROUP2_DEADLINE, GROUPS[2]: GROUP3_DEADLINE}.get(s["group"], BASE_DEADLINE)


def gday(s: dict) -> int:
    return {GROUPS[1]: 17, GROUPS[2]: 16}.get(s["group"], 15)


def spread(r, n: int, a: datetime, b: datetime, gap_h: float = 3) -> list[datetime]:
    """n sorted times in [a, b] at least gap_h hours apart."""
    for _ in range(200):
        ts = sorted(between(r, a, b) for _ in range(n))
        if all((y - x) >= timedelta(hours=gap_h) for x, y in zip(ts, ts[1:], strict=False)):
            return ts
    step = (b - a) / (n + 1)
    return [a + step * (i + 1) for i in range(n)]


EARLY = _msk(3, 5, 9, 0)

SCENARIO_PLAN = (
    [("normal", None)] * 21
    + [("late_after_ontime", None)] * 6
    + [("late_only", None)] * 4
    + [("too_late", None)] * 2
    + [("utc_prev", GROUPS[0])] * 3
    + [("utc_only", GROUPS[0])] * 2
    + [("g102_window", GROUPS[1])] * 4
    + [("g102_late", GROUPS[1])]
    + [("g103_window", GROUPS[2])] * 4
    + [("g103_before", GROUPS[2])] * 2
    + [("ext_ok", None)] * 3
    + [("ext_modified", None)] * 3
    + [("ext_reject", None)] * 2
    + [("ext_claim_noreply", None)] * 2 + [("ext_claim_denied", None)]
    + [("ext_assistant", None)] * 2
    + [("ext_corr_up", None)] + [("ext_corr_down", None)] * 2
    + [("ext_group", None)] * 3
    + [("ext_cond_ok", None)] * 2 + [("ext_cond_no", None)]
    + [("ext_relative", GROUPS[1]), ("ext_relative2", GROUPS[2])]
    + [("ext_weekday", None), ("ext_weekday_wed", None)]
    + [("ext_other_hw", None)] * 2
    + [("ext_revoked", None)] * 2
    + [("withdraw_ok", None)] * 6
    + [("withdraw_other", None)] * 2
    + [("withdraw_cancel", None)] * 2
    + [("withdraw_wrong_addr", None)]
)


def assign_scenarios(r, students: list[dict]) -> None:
    assert len(SCENARIO_PLAN) == len(students), len(SCENARIO_PLAN)
    free = list(students)
    r.shuffle(free)
    plan = sorted(SCENARIO_PLAN, key=lambda x: x[1] is None)  # group-bound first
    for scen, grp in plan:
        s = next(x for x in free if grp is None or x["group"] == grp)
        free.remove(s)
        s["scen"] = scen


def make_times(r, s: dict) -> None:
    """Fill attempts' times, withdrawn set and the personal deadline (truth)."""
    scen = s["scen"]
    g = group_deadline(s)
    gd = gday(s)
    s["deadline"] = g
    ext = {}
    before = g - timedelta(hours=5)
    if scen in ("normal", "withdraw_ok", "withdraw_other", "withdraw_cancel", "withdraw_wrong_addr"):
        n = r.choice([2, 3, 3, 4, 4]) if scen == "normal" else r.choice([2, 3, 4])
        end = min(before, BASE_DEADLINE - timedelta(hours=4))
        ts = spread(r, n, EARLY, end)
    elif scen == "late_after_ontime":
        ts = spread(r, r.choice([1, 2, 3]), EARLY, before) + [g + timedelta(hours=r.uniform(3, 60))]
    elif scen == "late_only":
        last = g + timedelta(hours=r.choice([r.uniform(4, 20), r.uniform(28, 44), r.uniform(52, 70)]))
        ts = [last] if r.random() < 0.3 else [g + timedelta(hours=r.uniform(1, 3)), last]
        ts.sort()
    elif scen == "too_late":
        ts = spread(r, 2, g + timedelta(hours=75), g + timedelta(hours=110))
    elif scen == "utc_prev":
        ts = spread(r, r.choice([1, 2, 3]), EARLY, before) + [g + timedelta(minutes=r.randint(20, 170))]
    elif scen == "utc_only":
        ts = [g + timedelta(minutes=r.randint(15, 175))]
    elif scen == "g102_window":
        k = r.choice([0, 1, 2, 2])
        ts = spread(r, k, EARLY, BASE_DEADLINE - timedelta(hours=6)) + [
            between(r, BASE_DEADLINE + timedelta(hours=8), GROUP2_DEADLINE - timedelta(hours=2))]
    elif scen == "g102_late":
        ts = [g + timedelta(hours=r.uniform(6, 20))]
    elif scen == "g103_window":
        # after the corrected ПИ-103 deadline, before the superseded one
        ts = spread(r, r.choice([0, 1, 1, 2]), EARLY, BASE_DEADLINE - timedelta(hours=6)) + [
            between(r, g + timedelta(minutes=20), GROUP3_FIRST - timedelta(hours=1))]
    elif scen == "g103_before":
        ts = spread(r, r.choice([1, 2]), EARLY, BASE_DEADLINE - timedelta(hours=6)) + [
            between(r, BASE_DEADLINE + timedelta(hours=2), g - timedelta(minutes=30))]
    else:
        ts, ext = ext_times(r, s, scen, g, gd)
    s["ext"] = ext
    s["attempts"] = [{"n": i + 1, "t": t.replace(microsecond=0)} for i, t in enumerate(ts)]
    if scen == "withdraw_ok":
        s["withdrawn"] = {len(ts)}
    s["claims"] = {len(ts)} if scen in ("withdraw_other", "withdraw_cancel", "withdraw_wrong_addr") else set()
    if "deadline" in ext:
        s["deadline"] = max(g, ext["deadline"])


def ext_times(r, s: dict, scen: str, g: datetime, gd: int):
    """Times and extension facts for e-mail scenarios (all attempts after the group deadline)."""
    ext: dict = {"scen": scen}
    after = g + timedelta(hours=1)
    if scen == "ext_ok":
        day = gd + r.choice([2, 3])
        ext.update(asked=(day, 23, 59), deadline=dl(day))
        ts = [between(r, after, dl(day) - timedelta(hours=3))]
    elif scen == "ext_modified":
        asked = gd + r.choice([4, 5])
        given = (gd + 2, 12, 0) if r.random() < 0.5 else (gd + 1, 23, 59)
        ext.update(asked=(asked, 23, 59), given=given, deadline=dl(*given))
        ts = [dl(*given) + timedelta(hours=r.uniform(3, 30))]
    elif scen in ("ext_reject", "ext_claim_noreply", "ext_claim_denied", "ext_assistant", "ext_cond_no",
                  "ext_other_hw"):
        day = gd + r.choice([2, 3])
        ext.update(asked=(day, 23, 59))
        ts = [between(r, after, g + timedelta(hours=r.choice([20, 44, 68])))]
    elif scen == "ext_revoked":
        day = gd + 3
        ext.update(asked=(day, 23, 59), revoked=(day, 23, 59))
        ts = [between(r, g + timedelta(hours=26), dl(day) - timedelta(hours=4))]
    elif scen == "ext_corr_up":
        ext.update(first=(gd + 1, 23, 59), final=(gd + 3, 23, 59), deadline=dl(gd + 3))
        ts = [between(r, dl(gd + 1) + timedelta(hours=2), dl(gd + 3) - timedelta(hours=4))]
    elif scen == "ext_corr_down":
        ext.update(first=(gd + 4, 23, 59), final=(gd + 2, 23, 59), deadline=dl(gd + 2))
        ts = [between(r, dl(gd + 2) + timedelta(hours=2), dl(gd + 2) + timedelta(hours=40))]
    elif scen == "ext_group":
        ext.update(asked=(20, 23, 59))
        ts = [between(r, max(after, _msk(3, 18, 1, 0)), dl(19) - timedelta(hours=3))]
    elif scen == "ext_cond_ok":
        day = gd + 3
        ext.update(asked=(day, 23, 59), deadline=dl(day))
        ts = [between(r, after, dl(day) - timedelta(hours=5))]
    elif scen == "ext_relative":
        days = 3
        ext.update(days=days, deadline=g + timedelta(days=days))
        ts = [g + timedelta(hours=r.uniform(30, 60))]
    elif scen == "ext_relative2":
        days = 2
        ext.update(days=days, deadline=g + timedelta(days=days))
        ts = [g + timedelta(hours=r.uniform(30.5, 46))]  # after 17.03 23:59 (a count from 15.03 fails)
    elif scen == "ext_weekday":
        ext.update(deadline=dl(20))
        ts = [between(r, max(after, _msk(3, 19, 8, 0)), dl(20) - timedelta(hours=3))]
    elif scen == "ext_weekday_wed":
        ext.update(deadline=dl(18))
        ts = [between(r, max(after, _msk(3, 18, 1, 0)), dl(18) - timedelta(hours=2))]
    else:
        raise AssertionError(scen)
    if r.random() < 0.5 and scen in ("ext_reject", "ext_claim_noreply", "ext_claim_denied", "ext_assistant",
                                     "ext_cond_no", "ext_other_hw", "ext_corr_up", "ext_weekday",
                                     "ext_revoked", "ext_weekday_wed"):
        ts = [ts[0] - timedelta(hours=r.uniform(0.5, 0.9) * (ts[0] - g).total_seconds() / 3600)] + ts
    if r.random() < 0.35 and scen not in ("ext_group",):
        ts = [between(r, EARLY, g - timedelta(days=2))] + ts if scen in ("ext_ok", "ext_cond_ok") else ts
    return sorted(ts), ext


# --------------------------------------------------------------------------
# Rendering a whole solution file
# --------------------------------------------------------------------------

ATTEMPT_NOTES = ["первая версия", "исправления после тестов", "переписал(а) задачу {p}", "добавил(а) проверки",
                 "финальная версия", "мелкие правки", "починил(а) задачу {p}", "поправил(а) граничные случаи",
                 "после консультации", "почистил(а) код"]
HEADER_STYLES = 4


def file_header(s: dict, att: dict, r) -> str:
    kind = s["style"]["header"]
    note = r.choice(ATTEMPT_NOTES).format(p=r.randint(1, 3))
    who = f"{s['sur']['nom']} {s['first']}"
    if kind == 0:
        return f"# ДЗ-3, «Основы Python»\n# {who}, {s['group']}\n# попытка {att['n']}: {note}\n\n"
    if kind == 1:
        return f'"""Домашнее задание 3.\n\nАвтор: {who} ({s["group"]}).\n{note.capitalize()}.\n"""\n\n'
    if kind == 2:
        return f"# {who}\n# {note}\n\n"
    return ""


def main_block(s: dict, r) -> tuple[str, set[str]]:
    kinds = s["extras"]["main"]
    if not kinds:
        return "", set()
    imports: set[str] = set()
    lines = ['if __name__ == "__main__":', f"    # {r.choice(COMMENTS['misc'])}"]
    if s["extras"].get("selftest"):
        lines.append(f"    {s['names']['selftest']}()")
    if "str" in kinds:
        lines += ["    for v in (0, 38, 942, 5050):", '        print("digit_stats(" + str(v) + ") =", digit_stats(v))']
    else:
        lines += ["    print(digit_stats(38))", "    print(digit_stats(99999))"]
    if "counter" in kinds:
        imports.add("from collections import Counter")
        lines += ['    sample = "Мама мыла раму, мама мыла!"', "    print(top_words(sample, 2))",
                  "    # сверка с Counter (только для проверки, в решении его нет)",
                  "    print(Counter(sample.lower().replace(',', ' ').replace('!', ' ').split()).most_common(2))"]
    else:
        lines += ['    print(top_words("кот кот пёс", 2))']
    if "sorted" in kinds:
        lines += ["    data = [[8, 10], [1, 3], [2, 6]]", "    print(merge_intervals(data))",
                  "    print(sorted(data))  # для сравнения", "    assert data == [[8, 10], [1, 3], [2, 6]]"]
    else:
        lines += ["    print(merge_intervals([[1, 3], [2, 6], [8, 10]]))"]
    return "\n".join(lines) + "\n", imports


DESCR = {
    ("D", "while"): "Задача 1: цифры достаю через % 10 и // 10 в цикле while, заодно считаю максимум.",
    ("D", "divmod"): "Задача 1: цифры получаю через divmod(n, 10), сумму и максимум считаю в одном цикле.",
    ("D", "list_inline"): "Задача 1: сначала собираю список цифр арифметикой, потом sum и max.",
    ("D", "helper_list"): "Задача 1: отдельная функция возвращает список цифр числа.",
    ("D", "recursive"): "Задача 1: сумма и максимум цифр — рекурсивными функциями.",
    ("D", "str"): "Задача 1: цифры беру из строкового представления числа — так проще всего.",
    ("D", "str_helper"): "Задача 1: цифры получаю вспомогательной функцией.",
    ("R", "formula_guard"): "Цифровой корень — по формуле 1 + (n - 1) % 9 (для нуля отдельно).",
    ("R", "formula_mod9"): "Цифровой корень через остаток от деления на 9.",
    ("R", "loop_inline"): "Цифровой корень — повторяю суммирование цифр, пока не останется одна.",
    ("R", "loop_helper"): "Цифровой корень — повторно применяю функцию суммы цифр.",
    ("R", "recursive"): "Цифровой корень — рекурсией.",
    ("T", "loop"): "Задача 2: слова выделяю вручную, проходя по символам и проверяя isalpha().",
    ("T", "regex"): "Задача 2: слова выделяю регулярным выражением (только буквы).",
    ("T", "regex_helper"): "Задача 2: разбиение на слова вынес в отдельную функцию с re.",
    ("T", "loop_helper"): "Задача 2: разбиение на слова вынес в отдельную функцию.",
    ("C", "defaultdict"): "Частоты считаю через defaultdict(int).",
    ("C", "get"): "Частоты считаю обычным словарём через get.",
    ("C", "counter"): "Частоты — через Counter.",
    ("K", "manual"): "Первые k слов выбираю сам, без сортировки всего списка.",
    ("K", "lambda"): "Сортирую пары по ключу (-частота, слово).",
    ("K", "two_sorts"): "Сортирую дважды: по слову, потом устойчиво по частоте.",
    ("S", "insertion"): "Задача 3: сортировка вставками по левому концу.",
    ("S", "msort"): "Задача 3: сортировка слиянием (рекурсивная).",
    ("S", "selection"): "Задача 3: сортировка выбором.",
    ("S", "bubble"): "Задача 3: пузырьковая сортировка — медленно, но для тестов хватает.",
    ("S", "inline_insertion"): "Задача 3: вставляю каждый отрезок на своё место в новый список.",
    ("S", "shared"): "Задача 3: для сортировки использую общую функцию из задачи 2.",
    ("M", "index"): "Слияние — двумя индексами.",
    ("M", "std"): "Слияние — один проход, расширяю последний отрезок.",
    ("M", "first"): "Слияние — начинаю с первого отрезка и иду дальше.",
}
REFLECT = [
    "Больше всего времени ушло на граничные случаи.", "Тесты запускал(а) командой из README.",
    "С третьей задачей помогла лекция про сортировки.", "Во второй задаче долго не понимал(а) про ничьи.",
    "Проверял(а) вручную на примерах из условия.", "Код старался(ась) писать без лишних библиотек.",
    "Не уверен(а) насчёт производительности на больших данных.", "Спасибо за интересные задачи!",
]
SELFTEST = [
    "    assert digit_stats(38) == (11, 2, 8)", "    assert digit_stats(0) == (0, 0, 0)",
    "    assert digit_stats(99999999999) == (99, 9, 9)", "    assert digit_stats(942)[1] == 6",
    '    assert top_words("кот кот пёс", 2) == [("кот", 2), ("пёс", 1)]',
    '    assert top_words("", 3) == []', '    assert top_words("b a c b a b", 2) == [("b", 3), ("a", 2)]',
    '    assert top_words("abc123abc 4ab", 2)[0] == ("abc", 2)',
    "    assert merge_intervals([[1, 2], [2, 3]]) == [[1, 3]]", "    assert merge_intervals([]) == []",
    "    assert merge_intervals([[1, 10], [2, 3], [4, 5]]) == [[1, 10]]",
    "    data = [[5, 7], [1, 2], [6, 9]]", "    merge_intervals(data)",
    "    assert data == [[5, 7], [1, 2], [6, 9]], 'вход изменился!'",
    '    print("digit_stats:", [digit_stats(v) for v in (7, 1000, 5050)])',
    '    print("sorted для сравнения:", sorted([[3, 4], [1, 2]]))',
    '    print("проверки: " + str(len("ok")))',
]
NAME_POOLS["selftest"] = ["self_test", "run_checks", "my_tests", "check_all", "smoke_test"]
HELPER_ROLES.add("selftest")


def explanation(s: dict, att: dict, r) -> str:
    lines = []
    for p in (1, 2, 3):
        var = att["var"][p]
        if var == "stub":
            lines.append(f"Задача {p}: не успел(а).")
            continue
        for slot, name in var["slots"].items():
            if (slot, name) in DESCR and r.random() < 0.8:
                lines.append(DESCR[(slot, name)])
    lines += r.sample(REFLECT, 2)
    wrapped = []
    for ln in lines:
        wrapped += textwrap.wrap(ln, 96)
    return "".join(f"# {ln}\n" for ln in wrapped) + "\n"


def selftest_block(s: dict, r) -> str:
    body = r.sample(SELFTEST, r.randint(6, 12))
    if "    merge_intervals(data)" in body and "    data = [[5, 7], [1, 2], [6, 9]]" not in body:
        body.remove("    merge_intervals(data)")
    if any("data ==" in ln for ln in body) and "    data = [[5, 7], [1, 2], [6, 9]]" not in body:
        body = [ln for ln in body if "data ==" not in ln]
    if "    data = [[5, 7], [1, 2], [6, 9]]" in body:
        body = [ln for ln in body if "data" not in ln] + [
            "    data = [[5, 7], [1, 2], [6, 9]]", "    merge_intervals(data)",
            "    assert data == [[5, 7], [1, 2], [6, 9]], 'вход изменился!'"]
    name = s["names"]["selftest"]
    return f"def {name}():\n    # мои проверки (запускаются только вручную)\n" + "\n".join(body) + \
        '\n    print("все мои проверки прошли")\n'


def render_file(s: dict, att: dict) -> str:
    r = rng(f"{TASK_ID}/{s['id']}/{att['n']}")
    style, names, extras = s["style"], s["names"], s["extras"]
    imports: set[str] = set()
    consts: list[str] = []
    per_problem: dict[int, list[tuple[str, str]]] = {}
    for p in (1, 2, 3):
        var = att["var"][p]
        pnames = {**names, **(var.get("names") or {})} if var != "stub" else names
        pstyle = {**style, "ann": {**style["ann"], p: var.get("ann", style["ann"][p])}} if var != "stub" else style
        code = problem_code(p, var, pnames, pstyle, r)
        imports |= code["imports"]
        consts += code["consts"]
        per_problem[p] = code["defs"]
    blocks: list[str] = []
    seen_defs: set[str] = set()
    for p in style["order"]:
        defs = per_problem[p]
        main, helpers = defs[0], [d for d in defs[1:] if d[0] not in seen_defs]
        seen_defs |= {d[0] for d in helpers}
        if style["helpers_first"]:
            blocks += [h[1] for h in helpers] + [main[1]]
        else:
            blocks += [main[1]] + [h[1] for h in helpers]
        if p in extras["redef"]:
            blocks.insert(len(blocks) - 1 - (0 if style["helpers_first"] else len(helpers)),
                          render(REDEF[p], {**names}, {**style, "ann": False}, r))
    for p in extras["dead"]:
        blocks.append(render(DEAD[p], names, {**style, "ann": False}, r))
    mb, mimports = main_block(s, r)
    imports |= mimports
    if extras.get("top_import"):
        imports.add(extras["top_import"])
    if s["extras"].get("selftest"):
        blocks.append(selftest_block(s, r))
    head = file_header(s, att, r)
    if s["extras"].get("explain"):
        head += explanation(s, att, r)
    imp = "\n".join(sorted(imports, key=lambda x: (x.startswith("from"), x)))
    out = head + (imp + "\n\n" if imp else "")
    if consts:
        out += "\n".join(c.rstrip("\n") for c in consts) + "\n\n\n"
    out += "\n\n".join(b.strip("\n") + "\n" for b in blocks)
    if extras.get("top_debug"):
        out += "\n\n# быстрая проверка\nprint(digit_stats(38))\n"
    if att.get("crash") == "input":
        out += '\n\n# ручной ввод для проверки\nn = int(input("n = "))\nprint(digit_stats(n))\n'
    elif att.get("crash") == "assert":
        out += ('\n\n# быстрая самопроверка при запуске\nassert digit_stats(38) == (11, 2, 8)\n'
                'assert top_words("кот кот пёс", 2) == [("кот", 2), ("пёс", 2)], "частоты не сошлись"\n')
    if mb:
        out += "\n\n" + mb
    return out


# --------------------------------------------------------------------------
# Grading (truth and plausible wrong graders for near misses)
# --------------------------------------------------------------------------

ALL_ROLES = sorted(NAME_POOLS)


def ext_deadline(s: dict, flags: frozenset) -> datetime:
    g = BASE_DEADLINE if "no_group" in flags else group_deadline(s)
    if "g103_first" in flags and s["group"] == GROUPS[2]:
        g = GROUP3_FIRST
    ext = s.get("ext") or {}
    if "no_ext" in flags:
        return g
    if "all_ext" in flags:
        cand = ext.get("asked") or ext.get("first") or ext.get("final")
        cand = dl(*cand) if cand else ext.get("deadline")
    elif "days" in ext:
        cand = g + timedelta(days=ext["days"])
    else:
        cand = ext.get("deadline")
    return max(g, cand) if cand else g


def counted(s: dict, flags: frozenset = frozenset()) -> tuple[dict | None, int]:
    atts = list(s["attempts"])
    if "last" in flags:
        return (atts[-1] if atts else None), 0
    if "no_withdraw" not in flags:
        gone = s["withdrawn"] | (s["claims"] if "over_withdraw" in flags else set())
        atts = [a for a in atts if a["n"] not in gone]
    special = {"no_ext", "all_ext", "no_group", "g103_first"}
    deadline = s["deadline"] if not flags & special else ext_deadline(s, flags)
    shift = timedelta(hours=-3) if "tz" in flags else timedelta(0)
    ontime = [a for a in atts if a["t"] + shift <= deadline]
    if ontime:
        return ontime[-1], 0
    late = [a for a in atts if a["t"] + shift <= deadline + 3 * DAY]
    if not late:
        return None, 0
    a = late[-1]
    days = math.ceil((a["t"] + shift - deadline).total_seconds() / 86400)
    return a, 10 * days


def grade_rows(model: dict, flags: frozenset = frozenset()) -> dict[str, tuple[int, int, int, int, int]]:
    rows = {}
    plag = model["plag"]
    for s in model["students"]:
        att, penalty = counted(s, flags)
        if att is None:
            rows[s["id"]] = (0, 0, 0, 0, 0)
            continue
        ps = []
        for p in (1, 2, 3):
            pts = WEIGHTS[p] * sum(att["passes"][p])
            if "tests_only" not in flags:
                fit = att["var"][p] != "stub" and bool(att["var"][p].get("fit"))
                key = (s["id"], p)
                is_plag = key in plag["real"] and att["n"] == plag["real"][key]
                if "over_plag" in flags:
                    is_plag = is_plag or key in plag["lookalike"]
                if "no_plag" in flags:
                    is_plag = False
                if "grep_file" in flags:
                    forb = att["grep_file"][p]
                elif "grep_func" in flags:
                    forb = att["grep_func"][p]
                else:
                    forb = att["forbidden"][p]
                if (fit and "no_fit" not in flags) or is_plag:
                    pts = 0
                elif forb:
                    pts //= 2
            ps.append(pts)
        rows[s["id"]] = (*ps, penalty, max(0, sum(ps) - penalty))
    return rows


# --------------------------------------------------------------------------
# Build the world
# --------------------------------------------------------------------------

GREP = {1: re.compile(r"\b(?:str|repr)\("), 2: re.compile(r"Counter|most_common"),
        3: re.compile(r"\bsorted\(|\.sort\(")}
TOGGLES = {1: ("SC", "V"), 2: ("KG", "EG"), 3: ("SG", "EM")}


def _main_func_text(src: str, p: int) -> str:
    tree = ast.parse(src)
    node = _module_defs(tree).get(FUNC[p])
    return (ast.get_source_segment(src, node) or "") if node else ""


def _copy_var(var: dict) -> dict:
    return {**var, "slots": dict(var["slots"])}


def _style(r) -> dict:
    ann = r.random() < 0.25
    order = [1, 2, 3] if r.random() < 0.7 else r.sample([1, 2, 3], 3)
    return {"doc": r.random() < 0.8, "cdens": r.uniform(0.35, 0.95), "ann": {1: ann, 2: ann, 3: ann},
            "header": r.randrange(HEADER_STYLES), "order": order, "helpers_first": r.random() < 0.5}


def _extras(r) -> dict:
    main = set()
    if r.random() < 0.8:
        main = {k for k in ("str", "counter", "sorted") if r.random() < 0.4} | {"plain"}
    return {"dead": [p for p in (1, 2, 3) if r.random() < 0.12],
            "redef": [p for p in (2, 3) if r.random() < 0.05],
            "main": main,
            "top_import": "from collections import Counter" if r.random() < 0.08 else None,
            "top_debug": r.random() < 0.1,
            "selftest": r.random() < 0.85,
            "explain": r.random() < 0.85}


def _render_all(students: list[dict]) -> None:
    for s in students:
        for att in s["attempts"]:
            att["text"] = render_file(s, att)


def _fit_candidates(p: int, passes: list[bool]) -> list[int]:
    excluded = {1: {0, 8}, 2: {0, 8}, 3: {0, 1}}[p]
    return [i for i, ok in enumerate(passes) if not ok and i not in excluded]


@functools.cache
def build() -> dict:
    r = rng(TASK_ID)
    students = make_students(r)
    assign_scenarios(r, students)
    for s in students:
        make_times(r, s)
    threads = build_threads(rng(f"{TASK_ID}/mail"), students)
    by_id = {s["id"]: s for s in students}
    late_scen = {"late_after_ontime", "late_only", "utc_prev", "utc_only", "g102_window", "g102_late",
                 "g103_window", "g103_before"}
    # --- who uses forbidden constructs (in their final code)
    order = list(students)
    r.shuffle(order)
    forbid_plan = [1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 3]
    for s in students:
        s["forbid"] = set()
    for s, p in zip(order[:len(forbid_plan)], forbid_plan, strict=True):
        s["forbid"].add(p)
    for s in students:
        late = s["scen"] in late_scen or s["scen"].startswith("ext")
        s["skill"] = r.choice(["strong", "mid"]) if late else r.choice(["strong", "strong", "mid", "mid", "weak"])
        s["style"] = _style(r)
        s["extras"] = _extras(r)
        s["names"] = pick_names(r, ALL_ROLES)
        msg = r.choice(P1_MSGS)
        n = len(s["attempts"])
        seqs = {p: attempt_variants(r, p, n, s["skill"], p in s["forbid"], msg) for p in (1, 2, 3)}
        notes = {p: r.choice(NOTE_LINES[p]) for p in (1, 2, 3) if r.random() < 0.15}
        for i, att in enumerate(s["attempts"]):
            att["var"] = {}
            for p in (1, 2, 3):
                var = seqs[p][i]
                if var != "stub" and p in notes:
                    var = {**var, "note": notes[p]}
                att["var"][p] = var
        if s["scen"] in ("withdraw_ok", "withdraw_wrong_addr"):
            # the (claimed) withdrawn upload is a draft: one problem is still the starter stub
            prev, last = s["attempts"][-2], s["attempts"][-1]
            ps = [p for p in (1, 2, 3) if prev["var"][p] != "stub" and not any(
                name.startswith("bug") for name in prev["var"][p]["slots"].values())]
            ps = ps or [p for p in (1, 2, 3) if prev["var"][p] != "stub"] or [1]
            last["var"][r.choice(ps)] = "stub"
        elif s["scen"] in ("withdraw_cancel", "withdraw_other"):
            # the last upload is the good one; the previous one still has a stub
            prev, last = s["attempts"][-2], s["attempts"][-1]
            ps = [p for p in (1, 2, 3) if last["var"][p] != "stub" and not any(
                name.startswith("bug") for name in last["var"][p]["slots"].values())]
            ps = ps or [p for p in (1, 2, 3) if last["var"][p] != "stub"] or [1]
            prev["var"][r.choice(ps)] = "stub"
        s["counted"], s["penalty"] = counted(s)
    with_code = [s for s in students if s["counted"] is not None]
    cands = [s for s in with_code if all(s["counted"]["var"][p] != "stub" for p in (1, 2, 3))]
    r.shuffle(cands)
    used: set[str] = set()

    def take(pred=lambda s: True) -> dict:
        s = next(x for x in cands if x["id"] not in used and pred(x))
        used.add(s["id"])
        return s

    # --- forced helper-level violations (str via helper in P1, shared sorted helper P2/P3)
    for _ in range(2):
        s = take(lambda x: 1 not in x["forbid"])
        s["counted"]["var"][1]["slots"]["D"] = "str_helper"
        s["forbid"].add(1)
    for _ in range(3):
        s = take(lambda x: 3 not in x["forbid"] and 2 not in x["forbid"])
        s["counted"]["var"][3]["slots"]["S"] = "shared"
        s["counted"]["var"][2]["slots"]["K"] = "shared"
        s["forbid"].add(3)
    # the counted attempt must hold the planned forbidden construct
    for s in with_code:
        for p in s["forbid"]:
            var = s["counted"]["var"][p]
            if var != "stub" and not is_forbidden_var(p, var):
                slot = r.choice(sorted(FORBID[p]))
                var["slots"][slot] = r.choice(FORBID[p][slot])
                var["slots"] = fix_constraints(p, var["slots"])
    # --- crash at import (leftover input(); a module-level self-check with a wrong expectation)
    for kind in ("input", "assert"):
        s = take(lambda x: x["scen"] == "normal")
        s["counted"]["crash"] = kind
    # --- comment / docstring notes that name forbidden constructs inside clean solutions
    for p in (1, 2, 3, 1, 3, 2, 1):
        s = take(lambda x, p=p: p not in x["forbid"])
        s["counted"]["var"][p]["note"] = r.choice(NOTE_LINES[p])
    for p in (2, 3, 2):
        s = take(lambda x, p=p: p not in x["forbid"])
        s["extras"]["redef"] = [p]
    for dead in ([1, 3], [2, 3]):
        s = take()
        s["extras"]["dead"] = dead
    # --- plagiarism
    plag = {"real": {}, "lookalike": set(), "pairs": [], "permitted": None, "claim": None, "similar": []}

    def ok_source(x, p):
        v = x["counted"]["var"][p]
        return p not in x["forbid"] and v != "stub" and v["slots"].get("K") != "shared" \
            and v["slots"].get("S") != "shared" and not v.get("fit")

    def make_copy(src: dict, dst: dict, p: int, att: dict | None = None) -> None:
        var = _copy_var(src["counted"]["var"][p])
        roles = roles_of(HEADS[p], *(BANK[p][sl][nm]["body"] for sl, nm in var["slots"].items()),
                         *(h for sl, nm in var["slots"].items() for h in BANK[p][sl][nm]["helpers"]))
        taken = {dst["names"][x] for x in HELPER_ROLES if x not in roles}
        var["names"] = pick_names(r, roles, avoid=src["names"], taken=taken)
        var["ann"] = src["style"]["ann"][p]
        var.pop("note", None)
        (att or dst["counted"])["var"][p] = var

    # (problem, group size): five pairs and a triple
    groups = [(2, 2), (1, 2), (3, 2), (1, 3), (3, 2), (2, 2)]
    for p, size in groups:
        src = take(lambda x, p=p: ok_source(x, p))
        members = [src]
        for _ in range(size - 1):
            dst = take(lambda x, p=p: ok_source(x, p))
            make_copy(src, dst, p)
            members.append(dst)
        for m in members:
            plag["real"][(m["id"], p)] = m["counted"]["n"]
        plag["pairs"].append((p, [m["id"] for m in members]))
    plag["claim"] = plag["pairs"][1]
    # permitted pairs (lecturer allowed pair work on this problem); the second pair also copied
    # problem 1, which the permission does not cover
    plag["permitted"] = []
    for p, extra in ((2, None), (3, 1)):
        a = take(lambda x, p=p, extra=extra: ok_source(x, p) and (extra is None or ok_source(x, extra)))
        b = take(lambda x, p=p, extra=extra: ok_source(x, p) and (extra is None or ok_source(x, extra)))
        make_copy(a, b, p)
        plag["lookalike"] |= {(a["id"], p), (b["id"], p)}
        plag["permitted"].append((p, a["id"], b["id"]))
        if extra is not None:
            make_copy(a, b, extra)
            for m in (a, b):
                plag["real"][(m["id"], extra)] = m["counted"]["n"]
            plag["pairs"].append((extra, [a["id"], b["id"]]))
    # look-alikes with one real structural difference
    for p in (3, 1, 2):
        a = take(lambda x, p=p: ok_source(x, p))
        b = take(lambda x, p=p: ok_source(x, p))
        make_copy(a, b, p)
        var = b["counted"]["var"][p]
        slot = TOGGLES[p][0]
        var["slots"][slot] = next(o for o in CORRECT[p][slot] if o != var["slots"][slot])
        var["slots"] = fix_constraints(p, var["slots"])
        plag["lookalike"] |= {(a["id"], p), (b["id"], p)}
        plag["similar"].append((p, a["id"], b["id"]))
    # copies inside attempts that do not count
    for p in (3, 2):
        b = take(lambda x, p=p: x["scen"] in ("late_after_ontime", "withdraw_ok") and ok_source(x, p))
        a = next(x for x in cands if x["id"] != b["id"] and ok_source(x, p) and (x["id"], p) not in plag["real"])
        make_copy(a, b, p, att=b["attempts"][-1])
    # --- test fitting (base code first, fitting clauses for its failing tests)
    fit_plan = [(1, "if"), (1, "dict"), (2, "if"), (2, "prefix"), (3, "if"), (3, "len"), (2, "dict"),
                (1, "if"), (3, "if"), (2, "len")]
    fitted = []
    for p, style in fit_plan:
        s = take(lambda x, p=p: ok_source(x, p))
        var = s["counted"]["var"][p]
        for _ in range(30):
            passes = evaluate(problem_only(s, var, p))[p]
            if _fit_candidates(p, passes):
                break
            var["slots"] = with_bug(r, p, var)["slots"]
        cases = _fit_candidates(p, passes) or [r.choice([2, 3, 5])]
        if style == "prefix":
            cases = [i for i in cases if _prefix_unique(i)] or [6]
        var["fit"] = {"style": style, "cases": cases}
        fitted.append((s["id"], p))
    _ensure_unique(r, students, plag)
    _render_all(students)
    for s in students:
        for att in s["attempts"]:
            text = att["text"]
            att["passes"] = evaluate(text)
            att["forbidden"] = {p: is_forbidden_var(p, att["var"][p]) for p in (1, 2, 3)}
            for p in (1, 2, 3):
                assert att["forbidden"][p] == uses_forbidden(text, p), (s["id"], att["n"], p)
            att["grep_file"] = {p: bool(GREP[p].search(text)) for p in (1, 2, 3)}
            att["grep_func"] = {p: bool(GREP[p].search(_main_func_text(text, p))) for p in (1, 2, 3)}
    _check_plagiarism(students, plag, by_id)
    model = {"students": students, "threads": threads, "plag": plag, "fitted": fitted,
             "pair_threads": pair_threads(rng(f"{TASK_ID}/pairs"), plag, by_id)}
    model["truth"] = grade_rows(model)
    return model


def _prefix_unique(i: int) -> bool:
    cases = _runner()["P2_CASES"]
    pre = cases[i][0][0][:14]
    return bool(pre) and sum(c[0][0].startswith(pre) for c in cases) == 1


def problem_only(s: dict, var, p: int) -> str:
    code = problem_code(p, var, s["names"], s["style"], rng("probe"))
    head = "\n".join(sorted(code["imports"])) + "\n" + "".join(code["consts"])
    return head + "\n\n".join(d[1] for d in code["defs"]) + "\n"


def _designated(plag: dict) -> dict[tuple[str, int], int]:
    """(sid, p) → group number for solutions that are copies of each other on purpose."""
    out: dict[tuple[str, int], int] = {}
    for gi, (p, ids) in enumerate(plag["pairs"]):
        for sid in ids:
            out[(sid, p)] = gi
    for k, (p, a, b) in enumerate(plag["permitted"]):
        out[(a, p)] = out[(b, p)] = 100 + k
    return out


def _ensure_unique(r, students: list[dict], plag: dict) -> None:
    groups = _designated(plag)
    protected = set(groups) | {(a, p) for p, a, b in plag["similar"]} | {(b, p) for p, a, b in plag["similar"]}
    for _round in range(40):
        clash = None
        for p in (1, 2, 3):
            seen: dict[str, tuple[str, int]] = {}
            for s in students:
                att = s["counted"]
                if att is None or att["var"][p] == "stub":
                    continue
                fp = fingerprint(render_file(s, att), p, strict=True)
                key = (s["id"], p)
                if fp in seen:
                    other = seen[fp]
                    if groups.get(other, -1) == groups.get(key, -2):
                        continue
                    clash = key if key not in protected else other
                    break
                seen[fp] = key
            if clash:
                break
        if not clash:
            return
        sid, p = clash
        s = next(x for x in students if x["id"] == sid)
        assert clash not in protected, clash
        var = s["counted"]["var"][p]
        forb = is_forbidden_var(p, var)
        keep = {k: v for k, v in var["slots"].items() if k in ("K", "S") and "shared" in v}
        new = correct_variant(r, p, forb)
        new.update(keep)
        var["slots"] = fix_constraints(p, new)
    raise AssertionError("не удалось добиться уникальности решений")


def _check_plagiarism(students: list[dict], plag: dict, by_id: dict) -> None:
    def fp(sid: str, p: int, strict: bool = False) -> str:
        s = by_id[sid]
        return fingerprint(s["counted"]["text"], p, strict=strict)

    for p, ids in plag["pairs"]:
        assert len({fp(x, p) for x in ids}) == 1, ("plag group differs", p, ids)
    for p, a, b in plag["permitted"]:
        assert fp(a, p) == fp(b, p)
    for p, a, b in plag["similar"]:
        assert fp(a, p, True) != fp(b, p, True)


# --------------------------------------------------------------------------
# E-mail threads
# --------------------------------------------------------------------------

MONTHS_GEN = {3: "марта", 4: "апреля"}
WEEKDAYS_GEN = ["понедельника", "вторника", "среды", "четверга", "пятницы", "субботы", "воскресенья"]
T = TEACHER
SEP = "\n" + "=" * 60 + "\n"


def _date_obj(day: int) -> datetime:
    month, d = (3, day) if day <= 31 else (4, day - 31)
    return datetime(2026, month, d)


def say_date(r, day: int, hour: int = 23, minute: int = 59) -> str:
    """A deadline in words; only (day, hour, minute) matter."""
    dt = _date_obj(day)
    dm = f"{dt.day} {MONTHS_GEN[dt.month]}"
    wd = WEEKDAYS_GEN[dt.weekday()]
    if (hour, minute) != (23, 59):
        return r.choice([f"до {hour:02d}:{minute:02d} {dm}", f"до {dm}, {hour:02d}:{minute:02d} (МСК)"])
    return r.choice([f"до {dm} включительно", f"до конца {dm}", f"до {dt.day:02d}.{dt.month:02d} 23:59",
                     f"до {wd}, {dm}, включительно", f"до {dm} (23:59 по Москве)"])


def stu(s: dict) -> tuple[str, str]:
    return full(s), s["email"]


def M(frm, to, date: datetime, body: str, cc=()) -> dict:
    return {"from": frm, "to": list(to), "cc": list(cc), "date": date, "body": textwrap.dedent(body).strip()}


def teacher():
    return (T["name"], T["addr"])


def assistant():
    return (ASSISTANT["name"], ASSISTANT["addr"])


def render_thread(th: dict) -> str:
    out = [f"Тема: {th['subject']}"]
    prev = None
    for m in th["msgs"]:
        lines = [f"От: {m['from'][0]} <{m['from'][1]}>",
                 "Кому: " + ", ".join(f"{n} <{a}>" for n, a in m["to"])]
        if m["cc"]:
            lines.append("Копия: " + ", ".join(f"{n} <{a}>" for n, a in m["cc"]))
        lines.append(f"Дата: {m['date'].strftime('%d.%m.%Y %H:%M')} (МСК)")
        body = m["body"]
        if prev is not None and th.get("quote", True):
            q = [ln for ln in prev["body"].split("\n") if ln.strip()][:4]
            body += "\n\n" + f"{prev['date'].strftime('%d.%m.%Y %H:%M')}, {prev['from'][0]} пишет:\n" + \
                "\n".join("> " + ln for ln in q)
        out.append("\n".join(lines) + "\n\n" + body)
        prev = m
    return SEP.join(out) + "\n"


REASONS = [
    "я {ill} — с четверга температура, справку из поликлиники донесу",
    "на этой неделе у меня были сборы сборной университета по волейболу, почти не было времени",
    "у меня сломался ноутбук, он в ремонте до среды, пишу с телефона",
    "у меня на работе (я работаю на полставки) внезапный аврал",
    "семейные обстоятельства — пришлось срочно уехать домой, в другой город",
    "я неправильно записал{a} срок и думал{a}, что сдавать в следующее воскресенье",
    "я лежал{a} в больнице три дня, выписка есть",
    "у нас дома отключили интернет почти на неделю, а в общежитии я не живу",
    "я ездил{a} на конференцию с докладом от кафедры, приказ о командировании есть в учебном офисе",
    "я перепутал{a} ДЗ-3 и ДЗ-4 и всё это время делал{a} не то задание",
    "у меня была пересдача по матанализу, и я все дни готовил{a}ся к ней",
]
GREET_S = ["Здравствуйте, Ирина Викторовна!", "Добрый день, Ирина Викторовна.", "Ирина Викторовна, здравствуйте!",
           "Здравствуйте!"]
SIGN_S = ["С уважением,\n{full}, {group}", "{full} ({group})", "Спасибо!\n{first}", "--\n{full}, группа {group}"]
FILLER_S = [
    "Задачи 1 и 2 у меня почти готовы, осталась третья — не успеваю разобраться с сортировкой без sorted().",
    "Большая часть уже сделана, но хочется довести до ума тесты.",
    "Я понимаю, что пишу поздно, извините.",
    "Если нужно, могу прислать подтверждающие документы.",
    "На консультацию в пятницу я тоже не попал{a}, поэтому пишу сюда.",
    "Тесты из tests/run_tests.py я запускал{a}, первая задача проходит полностью, со второй ещё вожусь.",
    "Заранее спасибо за ответ и извините за беспокойство.",
    "В прошлом ДЗ я сдал{a} всё вовремя, так что надеюсь на понимание.",
    "Если это важно, могу подойти после лекции и объяснить подробнее.",
    "Со второй задачей, кажется, разобрал{a}сь: проблема была в словах с цифрами внутри.",
    "Третью задачу переписываю уже второй раз — моя сортировка вставками портила входной список.",
    "Понимаю, что это не самая уважительная причина, но решил{a} всё-таки спросить.",
    "Если продление невозможно, сдам то, что есть, — лишь бы не ноль.",
    "Одногруппники говорят, что вы обычно идёте навстречу, если написать заранее, поэтому пишу.",
]
THANKS_S = [
    "Спасибо большое! Всё понял{a}.\n\n{first}",
    "Огромное спасибо, Ирина Викторовна!\n\n{first}",
    "Спасибо, постараюсь сдать раньше.\n\n{full}",
    "Понял{a}, спасибо! Больше не подведу.\n\n{first}",
    "Благодарю! Если что-то пойдёт не так с загрузкой, напишу.\n\n{full}, {group}",
]


def _fmt(text: str, s: dict) -> str:
    return text.format(a=ends(s, "", "а"), ill=ends(s, "заболел", "заболела"), full=full(s), first=s["first"],
                       group=s["group"], nick=s["nick"])


NEUTRAL_S = ["Заранее спасибо!", "Извините, если вопрос глупый.", "В условии я этого не нашёл(ла).",
             "Спросил(а) у одногруппников, но все отвечают по-разному.", "Буду благодарен(на) за ответ."]


def student_body(r, s: dict, core: str, reason: bool = True, neutral: bool = False) -> str:
    parts = [r.choice(GREET_S), ""]
    if reason:
        parts.append(_fmt("Пишу по поводу ДЗ-3: " + r.choice(REASONS) + ".", s))
    parts.append(core)
    for filler in r.sample(NEUTRAL_S if neutral else FILLER_S, r.randint(1, 2)):
        parts.append(_fmt(filler, s))
    parts += ["", _fmt(r.choice(SIGN_S), s)]
    return "\n".join(parts)


def ask_dt(s: dict, r, days_before: float = 2.0) -> datetime:
    base = group_deadline(s).astimezone(MSK) - timedelta(days=days_before)
    return (base + timedelta(minutes=r.randint(-600, 300))).replace(second=0, tzinfo=None)


def after(dt: datetime, r, lo: int = 40, hi: int = 900) -> datetime:
    return dt + timedelta(minutes=r.randint(lo, hi))


APPROVE = ["{nick}, хорошо, продлеваю срок сдачи ДЗ-3 {date}.",
           "Продление даю: ДЗ-3 можно сдать {date}. Выздоравливайте и не затягивайте.",
           "Договорились, срок для вас по ДЗ-3 — {date}.",
           "Не возражаю. Сдавайте ДЗ-3 {date}, штрафа за опоздание не будет.",
           "{nick}, да, ДЗ-3 {date} — можно."]
REJECT = ["К сожалению, продлить не могу: о таких обстоятельствах надо предупреждать заранее. "
          "Сдавайте, что успеете; опоздание — по общим правилам.",
          "{nick}, продления по ДЗ-3 не будет. Правила опозданий есть в регламенте курса.",
          "Нет, срок общий для всех. Если сдадите позже — действуют обычные штрафы.",
          "Увы, оснований для продления не вижу. Штраф за опоздание — по регламенту."]
T_SIGN = ["И. В. Лебедева", "ИВ", "--\nИрина Викторовна", "С уважением, И. В."]


def teacher_body(r, core: str) -> str:
    return core + "\n\n" + r.choice(T_SIGN)


def ext_thread(r, s: dict) -> list[dict]:
    """Thread(s) for a student's extension scenario (the truth is already in s['ext'])."""
    e, scen = s["ext"], s["scen"]
    me, t0 = stu(s), ask_dt(s, r)
    subj = r.choice(["ДЗ-3: продление", "Продление срока ДЗ-3", "Просьба о продлении", "ДЗ-3 — можно ли позже?"])
    ask = e.get("asked")
    ask_txt = say_date(r, *ask) if ask else ""
    msgs = []
    if scen == "ext_ok":
        msgs.append(M(me, [teacher()], t0, student_body(r, s, f"Можно ли сдать ДЗ-3 {ask_txt}?")))
        msgs.append(M(teacher(), [me], after(t0, r),
                      teacher_body(r, r.choice(APPROVE).format(nick=s["nick"], date=say_date(r, *ask)))))
    elif scen == "ext_modified":
        g = e["given"]
        msgs.append(M(me, [teacher()], t0, student_body(r, s, f"Можно ли сдать ДЗ-3 {ask_txt}?")))
        core = r.choice([
            "{nick}, {asked_short} — это слишком долго, группа уже будет разбирать решения. "
            "Продлеваю {date}.",
            "Столько дать не могу. Срок по ДЗ-3 для вас — {date}, дальше — штрафы по регламенту.",
            "Хорошо, но не {asked_short}: сдайте ДЗ-3 {date}.",
        ]).format(nick=s["nick"], asked_short=f"до {_date_obj(ask[0]).day}-го", date=say_date(r, *g))
        msgs.append(M(teacher(), [me], after(t0, r), teacher_body(r, core)))
        if r.random() < 0.5:
            msgs.append(M(me, [teacher()], after(msgs[-1]["date"], r),
                          f"Спасибо большое! А {ask_txt} точно никак?\n\n{s['first']}"))
    elif scen == "ext_reject":
        msgs.append(M(me, [teacher()], t0, student_body(r, s, f"Можно ли сдать ДЗ-3 {ask_txt}?")))
        msgs.append(M(teacher(), [me], after(t0, r), teacher_body(r, r.choice(REJECT).format(nick=s["nick"]))))
        if r.random() < 0.6:
            msgs.append(M(me, [teacher()], after(msgs[-1]["date"], r),
                          f"Поняла вас. Тогда сдам {ask_txt}, пусть и со штрафом." if s["female"] else
                          f"Понял вас. Тогда сдам {ask_txt}, пусть и со штрафом."))
    elif scen == "ext_claim_noreply":
        body = student_body(r, s, _fmt(
            f"Как мы с вами договорились после лекции во вторник, сдаю ДЗ-3 {ask_txt}. "
            "Спасибо, что разрешили! Просто напоминаю, чтобы не было недоразумений.", s), reason=False)
        msgs.append(M(me, [teacher()], t0, body))
    elif scen == "ext_claim_denied":
        body = student_body(r, s, f"Вы же разрешили мне на семинаре сдать ДЗ-3 {ask_txt}, правильно я понимаю?",
                            reason=False)
        msgs.append(M(me, [teacher()], t0, body))
        msgs.append(M(teacher(), [me], after(t0, r), teacher_body(r, r.choice([
            "{nick}, я такого не помню и продления не давала. Срок общий.",
            "Нет, никаких договорённостей о продлении у нас не было. Срок сдачи прежний.",
        ]).format(nick=s["nick"]))))
    elif scen == "ext_assistant":
        msgs.append(M(me, [assistant()], t0, student_body(r, s, f"Павел, можно ли сдать ДЗ-3 {ask_txt}?")
                      .replace("Ирина Викторовна", "Павел")))
        msgs.append(M(assistant(), [me], after(t0, r), r.choice([
            f"Привет! Думаю, Ирина Викторовна не будет против — сдавай {ask_txt}. Я ей передам.\n\nП. С.",
            f"Да, конечно, сдавай {ask_txt}, я в курсе твоей ситуации.\n\nПавел",
        ])))
        if r.random() < 0.5:
            msgs.append(M(teacher(), [me, assistant()], after(msgs[-1]["date"], r), teacher_body(r, (
                "Павел, напоминаю, что продления по ДЗ даю только я. "
                f"{s['nick']}, оснований для продления я не вижу, срок прежний."))))
    elif scen in ("ext_corr_up", "ext_corr_down"):
        f, fin = e["first"], e["final"]
        msgs.append(M(me, [teacher()], t0, student_body(r, s, "Можно ли немного продлить срок по ДЗ-3?")))
        msgs.append(M(teacher(), [me], after(t0, r), teacher_body(r, r.choice(APPROVE).format(
            nick=s["nick"], date=say_date(r, *f)))))
        if scen == "ext_corr_up":
            core = (f"Поправка к моему письму: я посмотрела даты олимпиады — вам можно сдать ДЗ-3 "
                    f"{say_date(r, *fin)}.")
        else:
            core = (f"Прошу прощения, ошиблась в дате. Продление по ДЗ-3 — {say_date(r, *fin)}, "
                    f"а не до {_date_obj(f[0]).day}-го, как я написала выше.")
        msgs.append(M(teacher(), [me], after(msgs[-1]["date"], r, 60, 600), teacher_body(r, core)))
    elif scen == "ext_cond_ok":
        msgs.append(M(me, [teacher()], t0, student_body(r, s, f"Можно ли сдать ДЗ-3 {ask_txt}?")))
        msgs.append(M(teacher(), [me], after(t0, r), teacher_body(
            r, f"Продлю {say_date(r, *ask)}, если пришлёте справку.")))
        msgs.append(M(me, [teacher()], after(msgs[-1]["date"], r), f"Справку прикладываю (скан во вложении).\n\n"
                                                                   f"{s['first']}"))
        msgs.append(M(teacher(), [me], after(msgs[-1]["date"], r), teacher_body(r, r.choice([
            f"Справку получила, продление {say_date(r, *ask)} в силе.",
            "Получила, спасибо. Продление подтверждаю.",
        ]))))
    elif scen == "ext_cond_no":
        msgs.append(M(me, [teacher()], t0, student_body(r, s, f"Можно ли сдать ДЗ-3 {ask_txt}?")))
        msgs.append(M(teacher(), [me], after(t0, r), teacher_body(
            r, f"Продлю {say_date(r, *ask)} при условии, что пришлёте справку.")))
        msgs.append(M(me, [teacher()], after(msgs[-1]["date"], r),
                      _fmt("Справки, к сожалению, нет — я лечил{a}сь дома, к врачу не ходил{a}. "
                           "Можно ли всё-таки продлить?\n\n{first}", s)))
    elif scen == "ext_relative":
        msgs.append(M(me, [teacher()], t0, student_body(r, s, "Можно ли мне немного продлить срок по ДЗ-3?")))
        msgs.append(M(teacher(), [me], after(t0, r), teacher_body(r, r.choice([
            "{nick}, хорошо: продлеваю вам ДЗ-3 на трое суток.",
            "Даю ещё трое суток к вашему сроку по ДЗ-3.",
        ]).format(nick=s["nick"]))))
    elif scen == "ext_relative2":
        msgs.append(M(me, [teacher()], t0, student_body(r, s, "Нельзя ли сдвинуть для меня срок по ДЗ-3?")))
        msgs.append(M(teacher(), [me], after(t0, r), teacher_body(r, r.choice([
            "{nick}, сдвигаю ваш срок по ДЗ-3 на двое суток.",
            "Хорошо, к вашему сроку по ДЗ-3 добавляю двое суток (48 часов).",
        ]).format(nick=s["nick"]))))
        msgs.append(M(me, [teacher()], after(msgs[-1]["date"], r),
                      _fmt("Спасибо! То есть отсчитывать от 15 марта, правильно?\n\n{first}", s)))
        msgs.append(M(teacher(), [me], after(msgs[-1]["date"], r), teacher_body(r, (
            "Нет, от срока вашей группы — я его переносила отдельным объявлением."))))
    elif scen == "ext_revoked":
        msgs.append(M(me, [teacher()], t0, student_body(r, s, f"Можно ли сдать ДЗ-3 {ask_txt}?")))
        msgs.append(M(teacher(), [me], after(t0, r), teacher_body(
            r, r.choice(APPROVE).format(nick=s["nick"], date=say_date(r, *ask)))))
        msgs.append(M(me, [teacher()], after(msgs[-1]["date"], r), _fmt(r.choice([
            "Спасибо огромное! Справку донесу на следующей неделе.\n\n{first}",
            "Большое спасибо, постараюсь не затягивать.\n\n{first}"]), s)))
        msgs.append(M(teacher(), [me], after(msgs[-1]["date"], r, 300, 1500), teacher_body(r, r.choice([
            "{nick}, вынуждена отозвать своё продление: учебный офис сообщил, что справка, о которой вы "
            "писали, закрыта ещё до начала ДЗ-3. Срок сдачи для вас — общий, как у группы.",
            "Прошу прощения, но продление по ДЗ-3 отменяю — я выяснила, что в эти дни вы были на занятиях. "
            "Действует срок вашей группы; опоздание — по регламенту.",
        ]).format(nick=s["nick"]))))
    elif scen == "ext_weekday_wed":
        t0 = datetime(2026, 3, 15, r.randint(9, 20), r.randint(0, 59))
        msgs.append(M(me, [teacher()], t0, student_body(r, s, "Можно ли досдать ДЗ-3 в начале недели?")))
        msgs.append(M(teacher(), [me], t0 + timedelta(minutes=r.randint(20, 200)), teacher_body(r, r.choice([
            "{nick}, до среды включительно — можно.",
            "Хорошо, ДЗ-3 приму до среды (включительно). Дальше — никак.",
        ]).format(nick=s["nick"]))))
    elif scen == "ext_weekday":
        t0 = datetime(2026, 3, 14, r.randint(10, 20), r.randint(0, 59))
        msgs.append(M(me, [teacher()], t0, student_body(r, s, "Можно ли сдать ДЗ-3 на следующей неделе?")))
        msgs.append(M(teacher(), [me], t0 + timedelta(minutes=r.randint(20, 200)), teacher_body(r, r.choice([
            "{nick}, можно до пятницы включительно.",
            "Хорошо, ДЗ-3 примите до пятницы включительно — сдавайте до пятницы.",
        ]).format(nick=s["nick"]))))
    elif scen == "ext_other_hw":
        msgs.append(M(me, [teacher()], t0, student_body(
            r, s, f"Можно ли продлить ДЗ-3 ({ask_txt}) и ДЗ-4 (до 5 апреля)?")))
        msgs.append(M(teacher(), [me], after(t0, r), teacher_body(r, r.choice([
            "По ДЗ-4 — да, до 5 апреля включительно. А ДЗ-3 сдавайте в срок, его продлить не могу.",
            "ДЗ-4 продлеваю до 5 апреля. По ДЗ-3 — нет, срок прежний.",
        ]))))
    else:
        raise AssertionError(scen)
    thankful = {"ext_ok", "ext_modified", "ext_corr_up", "ext_corr_down", "ext_cond_ok", "ext_relative",
                "ext_relative2", "ext_weekday", "ext_weekday_wed", "ext_other_hw"}
    if msgs[-1]["from"] == teacher() and scen in thankful and r.random() < 0.6:
        msgs.append(M(me, [teacher()], after(msgs[-1]["date"], r, 10, 300),
                      _fmt(r.choice(THANKS_S), s)))
    elif scen == "ext_revoked":
        msgs.append(M(me, [teacher()], after(msgs[-1]["date"], r, 30, 400), _fmt(
            "Ирина Викторовна, но я ведь уже рассчитывал{a} на этот срок! Может быть, всё-таки можно "
            "оставить продление хотя бы на день?\n\n{first}", s)))
    return [{"subject": subj, "msgs": msgs}]


def group_ext_thread(r, trio: list[dict]) -> dict:
    """One student writes for three; the lecturer approves two of them by surname (dative)."""
    writer = r.choice(trio)
    denied = r.choice(trio)
    ok = [s for s in trio if s is not denied]
    for s in trio:
        s["ext"] = {"scen": "ext_group", "asked": (20, 23, 59)}
        if s is not denied:
            s["ext"]["deadline"] = dl(19)
            s["deadline"] = max(group_deadline(s), dl(19))
    names = ", ".join(f"{x['sur']['nom']} {x['first']}" for x in trio)
    t0 = datetime(2026, 3, 13, r.randint(9, 21), r.randint(0, 59))
    body = student_body(r, writer, (
        f"Пишу от имени нескольких ребят с курса ({names}): мы на этой неделе были на отборочном туре "
        "олимпиады в другом городе. Можно ли всем нам сдать ДЗ-3 до 20 марта включительно?"), reason=False)
    cc = [stu(x) for x in trio if x is not writer]
    t1 = after(t0, r, 120, 1200)
    core = (f"Продлеваю срок по ДЗ-3 до 19 марта включительно {ok[0]['sur']['dat']} и {ok[1]['sur']['dat']} — "
            f"их фамилии есть в приказе о командировании на олимпиаду. {denied['sur']['gen']} в приказе нет, "
            f"поэтому для {ends(denied, 'него', 'неё')} срок прежний. До 20-го не получится: "
            "в пятницу я публикую разбор.")
    return {"subject": "ДЗ-3, олимпиада", "msgs": [
        M(stu(writer), [teacher()], t0, body, cc=cc),
        M(teacher(), [stu(writer)], t1, teacher_body(r, core), cc=cc)]}


def withdraw_thread(r, s: dict, by_time: bool) -> dict:
    att = s["attempts"][-1]
    t_msk = att["t"].astimezone(MSK)
    if by_time:
        what = (f"попытку, которую я загрузил{ends(s, '', 'а')} {t_msk.day} {MONTHS_GEN[t_msk.month]} "
                f"около {t_msk.strftime('%H:%M')} по Москве")
    else:
        what = f"мою попытку номер {att['n']}"
    body = student_body(r, s, _fmt(
        f"Прошу не учитывать {what} — я по ошибке загрузил{{a}} черновик, а не финальную версию. "
        "Проверьте, пожалуйста, предыдущую попытку.", s), reason=False)
    t0 = (t_msk + timedelta(minutes=r.randint(20, 300))).replace(tzinfo=None)
    msgs = [M(stu(s), [teacher()], t0, body)]
    if r.random() < 0.5:
        msgs.append(M(teacher(), [stu(s)], after(t0, r), teacher_body(r, "Хорошо, учту.")))
    return {"subject": "Отзыв попытки ДЗ-3", "msgs": msgs}


def withdraw_cancel_thread(r, s: dict) -> dict:
    """Withdrawal of the last attempt, later cancelled by the same student (the attempt counts)."""
    att = s["attempts"][-1]
    t_msk = att["t"].astimezone(MSK)
    body = student_body(r, s, _fmt(
        f"Прошу не учитывать мою попытку номер {att['n']} — кажется, я загрузил{{a}} не тот файл.", s),
        reason=False)
    t0 = (t_msk + timedelta(minutes=r.randint(20, 240))).replace(tzinfo=None)
    msgs = [M(stu(s), [teacher()], t0, body)]
    t1 = after(t0, r, 90, 600)
    msgs.append(M(stu(s), [teacher()], t1, _fmt(r.choice([
        f"Ирина Викторовна, извините за путаницу! Я проверил{{a}} ещё раз: в попытке {att['n']} как раз "
        "правильный файл. Пожалуйста, не обращайте внимания на моё предыдущее письмо и оценивайте её.\n\n{first}",
        f"Отбой, пожалуйста: просьбу про попытку {att['n']} отменяю, файл там верный. Оценивайте её, как "
        "обычно.\n\n{full}, {group}",
    ]), s)))
    if r.random() < 0.6:
        msgs.append(M(teacher(), [stu(s)], after(t1, r), teacher_body(r, "Хорошо, поняла.")))
    return {"subject": r.choice(["Попытка ДЗ-3", "Отзыв попытки ДЗ-3"]), "msgs": msgs}


def personal_addr(s: dict) -> str:
    return f"{translit(s['first'])}.{translit(s['sur']['nom'])}{len(s['first']) * 7 + 1990}@mail.example.com"


def withdraw_wrong_addr_thread(r, s: dict) -> dict:
    """A withdrawal sent from a personal mailbox — does not count (rubric 1.5)."""
    att = s["attempts"][-1]
    t_msk = att["t"].astimezone(MSK)
    me = (full(s), personal_addr(s))
    body = student_body(r, s, _fmt(
        f"Пишу с личной почты, университетская что-то не открывается. Прошу не учитывать мою попытку "
        f"номер {att['n']}: это черновик, загрузил{{a}} по ошибке. Проверьте, пожалуйста, предыдущую.", s),
        reason=False)
    t0 = (t_msk + timedelta(minutes=r.randint(30, 300))).replace(tzinfo=None)
    msgs = [M(me, [teacher()], t0, body)]
    msgs.append(M(teacher(), [me], after(t0, r), teacher_body(r, (
        "Здравствуйте. Отзыв попытки принимаю только с адреса, который указан в списке курса. "
        "С этого адреса учесть не могу."))))
    return {"subject": "Черновик вместо решения", "msgs": msgs}


def assistant_summary_thread(r, students: list[dict]) -> dict:
    """The assistant's (unofficial and partly wrong) summary of extensions."""
    pick = [s for s in students if s["scen"] in ("ext_assistant", "ext_claim_noreply", "ext_ok", "ext_reject")]
    lines = []
    for s in pick:
        ask = s["ext"].get("asked")
        lines.append(f"- {s['sur']['nom']} {s['first']} ({s['group']}) — {say_date(r, *ask)}"
                     + (" (со слов студента)" if s["scen"] == "ext_claim_noreply" else ""))
    t0 = datetime(2026, 3, 16, r.randint(8, 11), r.randint(0, 59))
    body = ("Ирина Викторовна, добрый день!\n\nСобрал черновой список тех, кто, насколько я знаю, сдаёт ДЗ-3 "
            "позже общего срока, — чтобы было удобнее сверять журнал:\n\n" + "\n".join(lines) +
            "\n\nСписок неполный, я вёл его по памяти и по письмам, которые приходили мне.\n\nП. Сомов")
    return {"subject": "ДЗ-3: кто сдаёт позже (черновик)", "msgs": [
        M(assistant(), [teacher()], t0, body),
        M(teacher(), [assistant()], after(t0, r, 200, 900), teacher_body(
            r, "Павел, спасибо, но ведомость буду сверять только по своей переписке."))]}


def withdraw_other_thread(r, writer: dict, target: dict) -> dict:
    t = target["attempts"][-1]["t"].astimezone(MSK)
    t0 = (t + timedelta(minutes=r.randint(30, 240))).replace(tzinfo=None)
    body = student_body(r, writer, (
        f"Мой сосед по общежитию {target['sur']['nom']} {target['first']} {ends(target, 'просил', 'просила')} передать: не учитывайте, "
        f"пожалуйста, его последнюю попытку по ДЗ-3 (номер {target['attempts'][-1]['n']}), "
        "у него сейчас не работает почта.").replace(
        "его последнюю", ends(target, "его последнюю", "её последнюю")).replace(
        "у него", ends(target, "у него", "у неё")).replace("Мой сосед", ends(target, "Мой сосед", "Моя соседка")),
        reason=False)
    if r.random() < 0.5:
        body = body.replace(ends(target, "Мой сосед по общежитию", "Моя соседка по общежитию"),
                            ends(target, "Мой одногруппник", "Моя одногруппница"))
    msgs = [M(stu(writer), [teacher()], t0, body)]
    msgs.append(M(teacher(), [stu(writer)], after(t0, r), teacher_body(
        r, r.choice([f"Пусть {target['first']} напишет {ends(target, 'сам', 'сама')} со своего адреса — "
                     "отзывать попытки за других нельзя.",
                     "Спасибо, но такие просьбы я принимаю только от самого студента."]))))
    return {"subject": "Попытка соседа", "msgs": msgs}


def announcement_threads(r) -> list[dict]:
    t = teacher()
    return [
        {"subject": "ДЗ-3 опубликовано", "quote": False, "msgs": [M(t, [("Поток ПИ-101/102/103", "pi-all@edu.example.ru")],
            datetime(2026, 3, 2, 10, 15), teacher_body(r, (
                "Коллеги, добрый день!\n\nДЗ-3 (три задачи: digit_stats, top_words, merge_intervals) "
                "опубликовано в системе. Срок сдачи — воскресенье, 15 марта, 23:59 по Москве. "
                "Правила оценивания — в rubric.md, видимые тесты — tests/run_tests.py. "
                "Обратите внимание на запрещённые конструкции в каждой задаче.")))]},
        {"subject": "ПИ-102: перенос семинара и срока ДЗ-3", "quote": False, "msgs": [
            M(t, [("Группа ПИ-102", "pi-102@edu.example.ru")], datetime(2026, 3, 11, 18, 40), teacher_body(r, (
                "Коллеги из ПИ-102!\n\nИз-за переноса вашего семинара с 12 на 16 марта срок сдачи ДЗ-3 "
                "для вашей группы сдвигается: сдавайте до вторника, 17 марта, 23:59 по Москве. "
                "Для ПИ-101 срок прежний.")))]},
        {"subject": "ПИ-103: срок ДЗ-3", "quote": False, "msgs": [
            M(t, [("Группа ПИ-103", "pi-103@edu.example.ru")], datetime(2026, 3, 11, 19, 5), teacher_body(r, (
                "Коллеги из ПИ-103!\n\nВаш семинар тоже переносится (с 13 на 16 марта), поэтому срок сдачи "
                "ДЗ-3 для ПИ-103 такой же, как у ПИ-102: до вторника, 17 марта, 23:59 по Москве."))),
            M(t, [("Группа ПИ-103", "pi-103@edu.example.ru")], datetime(2026, 3, 12, 8, 50), teacher_body(r, (
                "Коллеги, поправка ко вчерашнему письму. Семинар ПИ-103 16 марта начинается в 18:30, и на нём "
                "мы разбираем решения ДЗ-3, поэтому сдать нужно до его начала: срок для ПИ-103 — понедельник, "
                "16 марта, до 18:00 (МСК). Вторник, 17 марта, во вчерашнем письме — моя ошибка, он относится "
                "только к ПИ-102.")))]},
        {"subject": "ДЗ-4", "quote": False, "msgs": [M(t, [("Поток ПИ-101/102/103", "pi-all@edu.example.ru")],
            datetime(2026, 3, 16, 9, 5), teacher_body(r, (
                "Опубликовано ДЗ-4 (работа с файлами). Срок — 29 марта, 23:59. "
                "Напоминаю, что приём ДЗ-3 закрыт по регламенту; опоздавшие — по правилам штрафов.")))]},
    ]


def pair_threads(r, plag: dict, by_id: dict) -> list[dict]:
    out = []
    for k, (p, a_id, b_id) in enumerate(plag["permitted"]):
        a, b = by_id[a_id], by_id[b_id]
        t0 = datetime(2026, 3, 6 + 2 * k, r.randint(10, 20), r.randint(0, 59))
        if k == 0:
            ask = (f"Можно ли нам с {b['sur']['ins']} ({b['first']}) сделать задачу {p} ДЗ-3 вместе? "
                   "Мы хотим попробовать парное программирование.")
            answer = (f"Да, задачу {p} вы с {b['sur']['ins']} можете сдать как парную работу — одинаковый код в "
                      "этой задаче не будет считаться списыванием. Остальные задачи — строго самостоятельно.")
        else:
            ask = (f"Мы с {b['sur']['ins']} ({b['first']}) на семинаре начали вместе разбирать слияние отрезков. "
                   f"Можно ли сдать эту задачу (№ {p}) как общую, а остальное — каждый своё?")
            answer = (f"{a['nick']}, разрешаю: задачу {p} (merge_intervals) вы с {b['sur']['ins']} сдаёте "
                      "как парную, совпадение кода в ней я списыванием не считаю. Это касается только "
                      f"задачи {p}; digit_stats и top_words каждый пишет сам.")
        out.append({"subject": f"Парная работа, задача {p}", "msgs": [
            M(stu(a), [teacher()], t0, student_body(r, a, ask, reason=False), cc=[stu(b)]),
            M(teacher(), [stu(a)], after(t0, r), teacher_body(r, answer), cc=[stu(b)])]})
        if k == 1:
            out[-1]["msgs"].append(M(stu(b), [teacher()], after(out[-1]["msgs"][-1]["date"], r), _fmt(
                "Спасибо! Тогда мы и первую задачу немного обсуждали вместе, надеюсь, это не страшно.\n\n{first}",
                b), cc=[stu(a)]))
    cp, ids = plag["claim"]
    x, y = by_id[ids[0]], by_id[ids[1]]
    t1 = datetime(2026, 3, 16, r.randint(10, 20), r.randint(0, 59))
    out.append({"subject": f"ДЗ-3, задача {cp}", "msgs": [
        M(stu(y), [assistant()], t1, student_body(r, y, (
            f"Павел, добрый день. Хочу предупредить: задачу {cp} мы решали вместе с {x['sur']['ins']}, "
            "Ирина Викторовна на консультации сказала, что так можно. Чтобы не было вопросов "
            "про одинаковый код."), reason=False).replace("Ирина Викторовна!", "Павел!")
            .replace("Ирина Викторовна.", "Павел.").replace("Ирина Викторовна,", "Павел,")),
        M(assistant(), [stu(y)], after(t1, r), "Принял, передам Ирине Викторовне.\n\nП. Сомов")]})
    return out


NOISE = [
    ("Задача 2, тест 7", "У меня в задаче 2 не проходит тест 7 — там «Привет,мир!» без пробела. "
     "Это же одно слово?",
     "Нет, слово — это непрерывная последовательность букв. Запятая разделяет слова, так что там "
     "«привет» и «мир»."),
    ("defaultdict в задаче 2", "Можно ли в задаче 2 использовать collections.defaultdict? Counter ведь нельзя.",
     "Можно. Запрещены только Counter (в любом виде) и метод most_common. defaultdict и остальное "
     "из collections — пожалуйста."),
    ("Регулярные выражения", "А модуль re в задаче 2 разрешён?", "Да, re разрешён."),
    ("f-строки в задаче 1", "В первой задаче запрещён str(). А если я в сообщении об ошибке пишу "
     "f\"получено {n}\" — это нарушение?",
     "Нет. Запрещены именно вызовы str() и repr() в коде решения задачи 1. f-строки и сообщения "
     "об ошибках нарушением не считаются."),
    ("Касающиеся отрезки", "В задаче 3 отрезки [1, 2] и [2, 3] надо склеивать?",
     "Да, касающиеся отрезки тоже объединяются: [1, 3]. Это есть в условии и в тесте 5."),
    ("Запуск тестов на Windows", "У меня на Windows run_tests.py пишет что-то про signal. Что делать?",
     "Попробуйте запустить в WSL или на сервере курса. Сами тесты от этого не меняются."),
    ("Сортировка в задаче 2", "Если в задаче 3 sorted() нельзя, то в задаче 2 тоже нельзя?",
     "В задаче 2 sorted() и sort() можно. Запрет на встроенную сортировку действует только для кода "
     "решения задачи 3 (функции merge_intervals и того, что она вызывает)."),
    ("Помощник для двух задач", "Можно ли сделать одну вспомогательную функцию и вызывать её и из задачи 2, "
     "и из задачи 3?", "Можно, но помните: всё, что вызывает merge_intervals (в том числе общая функция), "
     "считается кодом задачи 3, и запреты задачи 3 на это распространяются."),
    ("Консультация", "Будет ли консультация перед сдачей ДЗ-3?",
     "Да, в пятницу 13 марта в 18:30, ауд. 402. Приходите с вопросами."),
    ("Оценка за ДЗ-2", "Когда будут оценки за ДЗ-2?", "На следующей неделе, после проверки плагиата."),
    ("Буква ё", "В тесте 2 слово «пёс» с ё. Надо ли заменять ё на е?",
     "Нет, ничего заменять не нужно, только приводить к нижнему регистру."),
    ("Вывод в консоль", "Можно ли оставить print в файле решения?",
     "Лучше убрать в блок if __name__ == \"__main__\". Тесты импортируют модуль: код вне этого блока "
     "выполняется при импорте, и если он падает, не пройдёт ни один тест."),
    ("Проверка на совпадения", "Как вы проверяете списывание? Если у нас похожая идея, это плохо?",
     "Похожая идея — нормально. Формальный критерий описан в rubric.md: совпадение кода задачи с "
     "точностью до имён, комментариев, форматирования и порядка функций."),
    ("Большие числа в задаче 1", "В тесте 8 число 100000000000000000005. Python точно справится с таким "
     "числом? В C++ оно бы не влезло.", "Справится: целые числа в Python произвольной длины. Арифметика % и // "
     "работает для любых целых."),
    ("Порядок слов при равной частоте", "Если у двух слов одинаковая частота, как их упорядочить — как в тексте?",
     "Нет, по алфавиту (обычное сравнение строк Python). Это написано в условии; тесты 5 и 10 это проверяют."),
    ("Кортеж или список", "В задаче 1 можно вернуть список [s, r, m] вместо кортежа?",
     "Нет, по условию нужен кортеж; тест сравнивает результат с кортежем, список тест не пройдёт."),
    ("Мутация входа в задаче 3", "Почему у меня падают почти все тесты в задаче 3, хотя ответы верные?",
     "Скорее всего, вы меняете входной список или вложенные списки (сортируете на месте или расширяете "
     "отрезок, который лежит во входе). Тесты проверяют, что вход не изменился."),
    ("Где смотреть результаты", "Где можно увидеть, сколько баллов я получил за ДЗ-3?",
     "Ведомость будет в LMS после проверки. Тесты можно запускать самостоятельно, но итог зависит и от "
     "проверки кода, и от сроков — см. регламент."),
    ("Несколько попыток", "Можно ли загрузить решение ещё раз до срока, если нашёл ошибку?",
     "Да, попыток может быть сколько угодно; до срока оценивается последняя."),
    ("Номер попытки", "Если я загружу ещё одну попытку после срока, пропадёт ли та, что была до срока?",
     "Нет. Засчитывается последняя попытка, сданная не позже вашего срока; поздние попытки в этом "
     "случае просто не рассматриваются."),
    ("Время в системе", "В личном кабинете у моей попытки стоит время 21:15, а я отправлял(а) её в полночь. "
     "Система сломалась?", "Нет, система показывает и хранит время в UTC. К московскому времени надо "
     "прибавить три часа. Сроки курса — всегда по Москве."),
    ("Рекурсия в задаче 1", "Можно ли в задаче 1 считать сумму цифр рекурсией?",
     "Можно. Ограничение одно — не пользоваться str() и repr(); рекурсия, divmod, циклы — пожалуйста."),
    ("Проверка типа в задаче 1", "Нужно ли в digit_stats проверять, что пришло именно целое число?",
     "Не обязательно. Тесты передают только целые; проверка типа не мешает и не считается ни подгонкой, "
     "ни нарушением."),
    ("assert вместо исключения", "Можно ли вместо raise ValueError написать assert n >= 0?",
     "Нет: assert бросает AssertionError, а условие требует ValueError. Тест 9 это проверяет."),
    ("Пустые строки в тексте", "Что вернуть в задаче 2, если в тексте одни знаки препинания, например "
     "\"!!! ...\"?", "Пустой список: слов нет. Отдельная проверка на такой случай не нужна, если "
     "разбиение на слова сделано правильно."),
    ("Словарь готовых ответов", "В чате курса кто-то советовал «закэшировать» ответы для тестов в словаре. "
     "Это разрешено?", "Нет. Словарь «вход → готовый ответ» для входов из тестов — это подгонка "
     "(rubric.md, раздел 5), за задачу будет 0. Кэш, который заполняется результатами вычислений, — другое дело."),
    ("Мутация вложенных списков", "Если я копирую входной список через list(intervals), этого достаточно?",
     "Нет: list() копирует только внешний список, вложенные отрезки остаются общими. Если потом "
     "расширять отрезок на месте, изменится вход — тест это заметит."),
    ("Вспомогательный модуль", "Можно ли сдать решение из двух файлов: solution.py и helpers.py?",
     "Нет, сдаётся один файл solution.py. Всё, что нужно, определяйте в нём."),
    ("Оценка после разбора", "Если я досдам ДЗ-3 после разбора на семинаре, оценка будет?",
     "Попытки принимаются в пределах 72 часов после вашего срока со штрафом; позже — не принимаются. "
     "Разбор на семинаре на это не влияет."),
    ("Перевод в другую группу", "Меня переводят в другую группу, приказ ещё не подписан. Какой у меня срок по ДЗ-3?",
     "Действует группа, указанная в списке курса (students.csv). Если там ещё старая группа, напишите в "
     "учебный офис, но для ДЗ-3 я ориентируюсь на этот список."),
    ("Попытки после отзыва", "Если я отзову последнюю попытку, могу ли я потом загрузить ещё одну?",
     "Да, можно, если успеваете в срок. Отозванная попытка просто не рассматривается."),
    ("Консультация 2", "Будет ли вторая консультация перед сроком ПИ-102?",
     "Нет, только пятничная. Вопросы можно задавать в переписке."),
    ("Штраф и ноль", "Если у меня за задачи 15 баллов, а штраф 20, получится минус?",
     "Нет, итог не бывает меньше нуля — будет 0."),
    ("Лишние функции", "У меня в файле осталась старая версия top_words под другим именем. Надо удалять?",
     "Лучше удалить, но функции, которые решение не вызывает, на оценку не влияют — ни на запреты, ни "
     "на проверку совпадений."),
]


# Longer discussions (student <-> staff); nothing in them changes a grade.
LONG_NOISE = [
    ("Задача 3: копия отрезков", "t", [
        "Я делаю так: result = [iv for iv in intervals], потом сортирую result своей сортировкой и "
        "склеиваю. Тесты 3–10 падают, хотя ответы на вид правильные. Почему?",
        "Вы копируете только внешний список: элементы result — те же объекты, что во входе. Сортировка "
        "перестановками внешнего списка вход не портит, а вот склейка, если вы пишете result[-1][1] = ..., "
        "меняет вложенный список входа. Копируйте отрезки: [[a, b] for a, b in intervals].",
        "Понял(а), переделал(а) — теперь всё проходит. А если я при склейке создаю новый список [a, max(b, d)], "
        "то можно и без копии?",
        "Да, если вы ни разу не меняете вложенные списки на месте, отдельная копия не нужна. Главное — "
        "результат должен быть новым списком, а вход — остаться прежним.",
    ]),
    ("Задача 2: апостроф и дефис", "a", [
        "Павел, а слова вроде «из-за» и «don't» — это одно слово или два?",
        "По условию слово — непрерывная последовательность букв. Дефис и апостроф — не буквы, поэтому "
        "«из-за» — это «из» и «за», а «don't» — «don» и «t».",
        "Странно, но ладно. А цифры внутри, как в тесте 8 («abc123abc»)?",
        "Так же: цифры — разделители, там два слова «abc».",
    ]),
    ("Штраф за опоздание: как считать", "t", [
        "Ирина Викторовна, я сдал(а) через 24 часа и 30 секунд после срока. Это 10 или 20 баллов?",
        "20: каждые начатые сутки. Опоздание ровно до 24 часов включительно — 10, всё, что больше, — уже "
        "вторые сутки.",
        "А если опоздание 5 минут — тоже целых 10?",
        "Да, тоже 10. Регламент один для всех, см. раздел 6.",
    ]),
    ("Подгонка или нет?", "t", [
        "У меня в digit_stats есть строчка if n < 10: return (n, n, n). Это же не подгонка?",
        "Нет, это общее условие для всех однозначных чисел, и ответ вычисляется из n. Подгонка — это "
        "когда ответ заранее записан для конкретного входа из тестов.",
        "А если бы я написал(а) if n == 0: return (0, 0, 0)?",
        "Это уже ветка вида «if n == …: return …» с заранее записанным ответом для конкретного числа — по "
        "регламенту такая ветка считается подгонкой, даже если ответ в ней верный. Пусть функция считает.",
    ]),
    ("Сортировка вставками и ключ", "a", [
        "Павел, в задаче 3 я сортирую вставками по левому концу, а при равных левых концах порядок "
        "какой-то случайный. Это важно?",
        "Для результата — нет: при склейке отрезки с одинаковым началом всё равно объединятся. Важно, "
        "чтобы вы не меняли вход и вернули новый список.",
    ]),
    ("Большие k", "t", [
        "В задаче 2 k может быть больше числа слов. Тогда надо дополнять ответ чем-то?",
        "Нет, возвращайте все слова, какие есть (тест 6). Ничего дополнять не нужно.",
        "Спасибо! И последний вопрос: k может быть отрицательным?",
        "В тестах такого нет, но по условию при k <= 0 ответ — пустой список.",
    ]),
    ("Совпадение с одногруппником", "t", [
        "Ирина Викторовна, мы с соседом по парте случайно написали очень похожие решения третьей задачи: "
        "одинаковая идея, но переменные и проверки разные. Нас накажут?",
        "Похожая идея — не списывание. Формальный критерий — в разделе 7 регламента: совпадение с "
        "точностью до имён, комментариев, форматирования и порядка функций. Если у вас разные проверки, "
        "совпадения по этому критерию нет.",
    ]),
    ("Кодировка файла", "a", [
        "Павел, у меня Windows, и run_tests.py ругается на кодировку solution.py. Что делать?",
        "Сохраните файл в UTF-8 (в большинстве редакторов это выбирается при сохранении). Все файлы решений "
        "проверяются в UTF-8.",
        "Сохранил(а), заработало. А на оценку это не повлияет — я же загружал(а) старую версию?",
        "Загруженные файлы в системе уже в UTF-8, с ними всё в порядке.",
    ]),
    ("Цифровой корень через % 9", "t", [
        "Ирина Викторовна, в задаче 1 я считаю цифровой корень как n % 9. Для 38 выходит 2, для 942 — 6, "
        "а для 99999 почему-то 0. Где ошибка?",
        "Для чисел, кратных 9 (кроме нуля), остаток равен 0, а цифровой корень — 9. Нужна поправка: "
        "например, n % 9 or 9, и отдельно случай n == 0. Это общее правило, а не подгонка под тест.",
        "Спасибо, заработало. А формула 1 + (n - 1) % 9 — то же самое?",
        "Да, для n >= 1. Для нуля она даёт 9, так что ноль всё равно придётся обработать отдельно.",
    ]),
    ("Функции внутри функций", "a", [
        "Павел, можно ли определить вспомогательную функцию внутри merge_intervals, а не на уровне модуля?",
        "Можно. Вложенная функция — часть кода решения задачи, запреты задачи 3 на неё распространяются.",
        "А если я вызову внутри неё sorted, но только для печати отладки?",
        "Любой вызов sorted() в коде решения задачи 3 — нарушение, для чего бы он ни был. Отладочную печать "
        "лучше убрать совсем.",
    ]),
    ("Сдача с телефона", "t", [
        "Ирина Викторовна, если я загружу файл с телефона, время в журнале будет по часовому поясу "
        "телефона? Я сейчас в Екатеринбурге.",
        "Нет, система записывает время сервера в UTC, часовой пояс вашего устройства ни на что не влияет. "
        "Сроки — по Москве.",
    ]),
    ("Тест 10 во второй задаче", "a", [
        "Павел, в тесте 10 у меня получается [('да', 4), ('нет', 2), ('он', 1)] вместо ('а', 1). Почему «а»?",
        "При равной частоте слова идут по алфавиту: «а» меньше «он», «она», «потом» и т. д. Проверьте, что "
        "у вас сортировка по слову идёт по возрастанию, а по частоте — по убыванию.",
        "Нашёл(ла): сортировал(а) по слову в обратном порядке. Спасибо!",
    ]),
    ("Можно ли использовать heapq", "t", [
        "Можно ли в задаче 2 брать k лучших через heapq.nlargest?",
        "Можно: запрещены только Counter и most_common. Но аккуратно с порядком при равной частоте — "
        "nlargest с ключом (частота, слово) даст обратный алфавит.",
    ]),
    ("Вопрос про отзыв попытки", "t", [
        "Ирина Викторовна, если я отзову попытку, а потом передумаю, это можно отменить?",
        "Да, напишите мне со своего университетского адреса, что отменяете просьбу, — тогда я снова "
        "буду рассматривать эту попытку. Действует ваше последнее письмо.",
    ]),
]


def long_noise_threads(r, students: list[dict]) -> list[dict]:
    pool = list(students)
    r.shuffle(pool)
    out = []
    for i, (subj, who_key, texts) in enumerate(LONG_NOISE):
        s = pool[i]
        who = teacher() if who_key == "t" else assistant()
        t = datetime(2026, 3, r.randint(4, 14), r.randint(9, 21), r.randint(0, 59))
        msgs = []
        for k, text in enumerate(texts):
            if k % 2 == 0:
                greet = "Павел, " if who_key == "a" and k == 0 and not text.startswith("Павел") else ""
                msgs.append(M(stu(s), [who], t, greet + text + f"\n\n{s['first']}"))
            else:
                msgs.append(M(who, [stu(s)], t, teacher_body(r, text) if who_key == "t" else text + "\n\nП. С."))
            t = after(t, r, 25, 700)
        out.append({"subject": subj, "msgs": msgs})
    return out


def noise_threads(r, students: list[dict]) -> list[dict]:
    out = []
    pool = list(students)
    r.shuffle(pool)
    for i, (subj, q, a) in enumerate(NOISE):
        s = pool[i]
        t0 = datetime(2026, 3, r.randint(3, 14), r.randint(9, 22), r.randint(0, 59))
        who = teacher() if i % 3 else assistant()
        answer = teacher_body(r, a) if who == teacher() else a + "\n\nП. Сомов"
        out.append({"subject": subj, "msgs": [
            M(stu(s), [who], t0, student_body(r, s, q, reason=False, neutral=True).replace(
                "Ирина Викторовна", "Павел" if who == assistant() else "Ирина Викторовна")),
            M(who, [stu(s)], after(t0, r), answer)]})
    # a letter from a student of another course
    out.append({"subject": "Продление ДЗ-3", "msgs": [
        M(("Олег Воронцов", "o.vorontsov@edu.example.ru"), [teacher()], datetime(2026, 3, 13, 23, 10),
          "Ирина Викторовна, здравствуйте! Можно ли сдать ДЗ-3 по курсу «Алгоритмы» до 22 марта?\n\nОлег, ПМ-201"),
        M(teacher(), [("Олег Воронцов", "o.vorontsov@edu.example.ru")], datetime(2026, 3, 14, 9, 30),
          teacher_body(r, "Олег, курс «Алгоритмы» ведёт другой преподаватель, но с моей стороны не возражаю: "
                          "до 22 марта включительно."))]})
    return out


def build_threads(r, students: list[dict]) -> list[dict]:
    threads = announcement_threads(r)
    trio = [s for s in students if s["scen"] == "ext_group"]
    threads.append(group_ext_thread(r, trio))
    for s in students:
        if s["scen"].startswith("ext_") and s["scen"] != "ext_group":
            threads += ext_thread(r, s)
    wd = [s for s in students if s["scen"] == "withdraw_ok"]
    for i, s in enumerate(wd):
        threads.append(withdraw_thread(r, s, by_time=(i % 3 == 0)))
    writers = [s for s in students if s["scen"] == "normal"]
    for i, target in enumerate(s for s in students if s["scen"] == "withdraw_other"):
        threads.append(withdraw_other_thread(r, writers[i], target))
    threads += [withdraw_cancel_thread(r, s) for s in students if s["scen"] == "withdraw_cancel"]
    threads += [withdraw_wrong_addr_thread(r, s) for s in students if s["scen"] == "withdraw_wrong_addr"]
    threads.append(assistant_summary_thread(r, students))
    threads += noise_threads(r, students)
    threads += long_noise_threads(r, students)
    return threads


def thread_files(model: dict) -> dict[str, str]:
    threads = list(model["threads"]) + model["pair_threads"]
    threads.sort(key=lambda th: (th["msgs"][0]["date"], th["subject"]))
    out = {}
    for i, th in enumerate(threads, 1):
        out[f"emails/{i:03d}.txt"] = render_thread(th)
    return out


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------

ASSIGNMENT_MD = """# ДЗ-3. Курс «Основы Python» (поток ПИ-101/ПИ-102/ПИ-103, весна 2026)

Преподаватель: Ирина Викторовна Лебедева. Ассистент: Павел Сомов.

Решение сдаётся одним файлом `solution.py`, в котором определены три функции:
`digit_stats`, `top_words`, `merge_intervals`. Можно определять любые вспомогательные функции.
Сторонние библиотеки не нужны; стандартная библиотека разрешена, кроме того, что запрещено
в конкретной задаче (см. ниже и `rubric.md`).

Срок сдачи: **воскресенье, 15 марта 2026, 23:59 по московскому времени** (для отдельных групп
и студентов срок мог быть изменён — см. `rubric.md`). Можно сдавать несколько попыток.

Видимые тесты: `python3 tests/run_tests.py путь/к/solution.py` — по 10 тестов на задачу.

---

## Задача 1. Статистика цифр (`digit_stats(n)`)

Дано целое `n >= 0`. Вернуть **кортеж** `(s, r, m)`:

* `s` — сумма цифр числа `n`;
* `r` — цифровой корень `n`: складываем цифры, затем цифры получившейся суммы и т. д.,
  пока не останется одна цифра (у нуля цифровой корень 0);
* `m` — наибольшая цифра числа `n`.

Если `n < 0`, функция должна бросить `ValueError`.

Примеры: `digit_stats(38) == (11, 2, 8)`, `digit_stats(0) == (0, 0, 0)`,
`digit_stats(1000) == (1, 1, 1)`.

**Ограничение:** задачу нужно решить арифметикой. В коде решения задачи 1 **запрещено
вызывать `str()` и `repr()`** (что такое «код решения задачи» — см. `rubric.md`).

## Задача 2. Частые слова (`top_words(text, k)`)

Слово — максимальная непрерывная последовательность букв (`str.isalpha()`); всё остальное
(пробелы, знаки препинания, цифры) — разделители. Регистр не важен: слова приводятся к нижнему
регистру, других преобразований нет (ё остаётся ё).

Вернуть **список кортежей** `(слово, частота)` для `k` самых частых слов: по убыванию частоты,
при равной частоте — по алфавиту (обычное сравнение строк Python). Если различных слов меньше
`k`, вернуть все. При `k <= 0` или пустом тексте — пустой список.

Пример: `top_words("Мама мыла раму, мама мыла!", 2) == [("мама", 2), ("мыла", 2)]`.

**Ограничение:** в коде решения задачи 2 запрещено использовать `collections.Counter`
(в любом виде) и метод `most_common`. Остальное (`dict`, `defaultdict`, `re`, `sorted`) можно.

## Задача 3. Слияние отрезков (`merge_intervals(intervals)`)

Дан список отрезков `[a, b]` (`a <= b`, целые), в любом порядке. Объединить пересекающиеся
**и касающиеся** отрезки (`[1, 2]` и `[2, 3]` → `[1, 3]`) и вернуть **новый список списков**,
упорядоченный по левому концу. Входной список и вложенные списки изменять нельзя
(тесты это проверяют).

Пример: `merge_intervals([[8, 10], [1, 3], [2, 6]]) == [[1, 6], [8, 10]]`.

**Ограничение:** сортировку нужно написать самостоятельно — в коде решения задачи 3
**запрещены `sorted()` и метод `list.sort()`**.

---

## Заготовка

```python
def digit_stats(n):
    # TODO: ваше решение
    pass


def top_words(text, k):
    # TODO: ваше решение
    pass


def merge_intervals(intervals):
    # TODO: ваше решение
    pass
```
"""

RUBRIC_MD = """# Регламент оценивания ДЗ-3

Документ определяет, как по материалам этой папки получить ведомость `grades.csv`.
Если что-то в переписке противоречит регламенту, действует регламент, кроме решений
лектора, которые регламент прямо разрешает (продления, парная работа, перенос срока группы).

## 1. Какая попытка оценивается

1.1. Все попытки перечислены в `submissions_log.csv` (`student, attempt, submitted_at`).
Время в журнале — **UTC** (суффикс `Z`). Все сроки в курсе, в объявлениях и письмах —
**московское время (UTC+3)**. Перед сравнением приведите время к одной шкале.

1.2. Общий срок сдачи — 15 марта 2026, 23:59 МСК. Лектор может объявить другой срок
для целой группы; такое объявление действует на всех студентов этой группы
(группа студента — в `students.csv`, другие сведения о группе значения не имеют). Если лектор
позже поправил своё объявление для группы, действует последняя поправка — даже если новый
срок раньше прежнего.

1.3. Срок, указанный с точностью до минуты («до 23:59», «до 12:00»), включает всю эту минуту
(до ЧЧ:ММ:59). Срок без времени («до 18 марта», «до 18 марта включительно», «до конца
18 марта») означает 23:59:59 этой даты по Москве. Если назван только день недели
(«до пятницы»), имеется в виду ближайший такой день после даты письма.

1.4. **Индивидуальное продление.** Продление действует, только если его письменно дал
**лектор** (И. В. Лебедева) в переписке из папки `emails/`, и только для ДЗ-3.
Не являются продлением: просьба студента; ссылка студента на устную договорённость;
согласие или мнение ассистента или кого-либо ещё; продление по другому заданию.
Если лектор назвал срок, отличный от запрошенного, действует срок лектора. Если лектор
позже исправил своё решение, действует последнее решение. Условное продление («продлю,
если пришлёте справку») действует, только если лектор затем письменно его подтвердил.
Продление «на N суток» отсчитывается от срока, который действовал для студента без
продления (общего или группового). Продление в письме нескольким студентам действует
только для тех, кого лектор назвал. Если у студента есть и групповой, и индивидуальный
срок, действует более поздний. Дата письма с продлением значения не имеет.

1.5. **Отзыв попытки.** Студент может письмом со своего адреса (адрес из `students.csv`)
попросить не учитывать конкретную свою попытку (по номеру или по времени отправки). Такая
попытка исключается из рассмотрения, даже если она последняя. Ответ лектора для этого
не нужен. Не действуют: просьба об отзыве чужой попытки (в том числе «по просьбе» самого
студента); письмо с другого адреса, даже подписанное именем студента. Студент может так же,
письмом со своего адреса, отменить свою просьбу об отзыве — тогда попытка снова
рассматривается (действует последнее из его писем об этой попытке).

1.6. Из оставшихся попыток оценивается **последняя попытка, отправленная не позже срока
студента**. Если таких нет, оценивается последняя попытка, отправленная в течение 72 часов
после срока, со штрафом за опоздание (п. 6). Попытки позже 72 часов после срока
не принимаются. Если нет ни одной подходящей попытки, студент получает 0 по всем задачам
и штраф 0.

## 2. Баллы за тесты

2.1. Для оцениваемой попытки запускаются видимые тесты `tests/run_tests.py` — ровно они,
других (скрытых) тестов нет. Результат запуска и есть база оценки.

2.2. Балл за задачу до вычетов = число пройденных тестов этой задачи × вес:
задача 1 — 3 балла за тест (максимум 30), задача 2 — 3 (максимум 30), задача 3 — 4
(максимум 40).

2.3. Если модуль не импортируется (например, код вне `if __name__ == "__main__":` падает или
ждёт ввода), все тесты считаются непройденными — так и делает `run_tests.py`.

## 3. Код решения задачи

3.1. **Код решения задачи** — это функция задачи (`digit_stats`, `top_words`,
`merge_intervals`) и все функции модуля, которые она вызывает прямо или через другие
функции (в том числе общие для нескольких задач). Если функция в файле определена
несколько раз, учитывается то определение, которое действует после импорта модуля
(последнее).

3.2. Не относятся к коду решения задачи: комментарии и строки документации; блок
`if __name__ == "__main__":`; функции, которые функция задачи не вызывает (старые версии,
черновики, собственные проверки студента); код других задач, если функция задачи его
не вызывает; импорты сами по себе (важно, используется ли импортированное в коде решения).

## 4. Запрещённые конструкции

4.1. Запреты задач: задача 1 — вызовы `str()` и `repr()`; задача 2 — `collections.Counter`
в любом виде (`Counter(...)`, `collections.Counter(...)`) и метод `most_common`; задача 3 —
`sorted()` и метод `.sort()`. Запрет действует только в коде решения своей задачи.
f-строки, `format`, `defaultdict`, `re` и прочее не запрещены.

4.2. Если в коде решения задачи есть запрещённая конструкция, балл за эту задачу делится
на 2 с округлением вниз (один раз, сколько бы нарушений ни было).

## 5. Подгонка под тесты

5.1. Подгонка — это код в решении задачи, который для конкретных входных данных
(конкретного числа, конкретного текста, конкретного списка отрезков) возвращает заранее
известный ответ вместо того, чтобы его вычислить: ветки `if n == ...: return ...`,
сравнения со строкой или списком из теста, словари «вход → готовый ответ», проверки вида
«длина такая-то и первый элемент такой-то».

5.2. Не являются подгонкой общие условия, верные для целого класса входов: `n < 10`,
пустой текст, `k <= 0`, пустой список, список из одного отрезка, проверки корректности
аргументов. Собственные проверки студента (`assert` в отдельной функции или в блоке
`__main__`) — не код решения и подгонкой не являются.

5.3. За задачу с подгонкой ставится 0 баллов — независимо от результатов тестов.

## 6. Штраф за опоздание

6.1. Если оценивается попытка, отправленная после срока студента (п. 1.6), штраф —
10 баллов за каждые начатые сутки опоздания: опоздание до 24 часов включительно — 10,
больше 24 и до 48 часов — 20, больше 48 и до 72 часов — 30. Сутки отсчитываются от момента
срока студента (с учётом группового срока и продления).

6.2. Штраф вычитается из суммы баллов за задачи; итог не может быть меньше 0.

## 7. Плагиат

7.1. Плагиат проверяется только между **оцениваемыми** попытками разных студентов и
по каждой задаче отдельно. Код решения задачи (п. 3.1) у двух студентов считается
совпадающим, если он одинаков с точностью до: комментариев и строк документации;
форматирования (пробелы, пустые строки, переносы); последовательного переименования
функций-помощников, параметров и переменных; порядка, в котором функции записаны в файле.

7.2. Любое другое отличие — другая конструкция, лишняя или недостающая проверка, другое
условие, другой порядок операторов, другое значение константы или строки — означает,
что формального совпадения нет. Подозрительно похожие, но не совпадающие работы
разбираются очно и на эту ведомость не влияют.

7.3. При совпадении каждый участник (двое, трое и т. д.) получает 0 за эту задачу.

7.4. Исключение: парная работа над конкретной задачей, которую лектор письменно
разрешил конкретным студентам. Разрешение, на которое ссылается студент, но которого нет
в переписке от лектора, не действует.

7.5. Нерешённая задача (заготовка без изменений) на плагиат не проверяется — за неё и так 0.

## 8. Итог и формат ведомости

8.1. Для каждой задачи: баллы за тесты (п. 2) → если подгонка или плагиат, 0 → иначе
при запрещённой конструкции делим на 2 с округлением вниз. `total = max(0, p1 + p2 + p3 −
penalty)`.

8.2. Файл `grades.csv` — UTF-8, разделитель запятая, заголовок
`student,p1,p2,p3,penalty,total`, по одной строке на каждого студента из `students.csv`
(включая тех, у кого нет засчитанных попыток), все значения — целые числа.

## 9. Примеры применения правил

* Студент ПИ-101 отправил попытку 1 в `2026-03-15T19:40:00Z` и попытку 2 в
  `2026-03-15T21:10:00Z`. По Москве это 22:40 и 00:10 16 марта: оценивается попытка 1,
  штрафа нет.
* Продление «до 18 марта» и попытка в 05:30 МСК 19 марта: опоздание 5 ч 30 мин → штраф 10.
* Студент написал ассистенту, ассистент ответил «сдавай до среды»: продления нет. Списки и
  сводки ассистента тоже не являются решениями лектора.
* Лектор дала продление, а через день написала, что отменяет его: действует срок без продления.
* Продление «на двое суток» студенту группы с перенесённым сроком: двое суток прибавляются
  к сроку группы (с учётом последней поправки к объявлению), а не к 15 марта.
* В файле есть `def merge_intervals_old(...)` с `sorted()`, но `merge_intervals` её не вызывает —
  нарушения нет. Если же `merge_intervals` вызывает общую функцию, внутри которой `sorted()`, —
  нарушение в задаче 3 есть, даже если та же функция используется и в задаче 2.
* В `top_words` есть ветка `if text == "b a c b a b": return [...]` — подгонка, 0 за задачу 2.
"""


# --------------------------------------------------------------------------
# Task plumbing
# --------------------------------------------------------------------------

HEADER = ["student", "p1", "p2", "p3", "penalty", "total"]
MIN_ROWS = 86


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def setup(ws: Path) -> None:
    model = build()
    students = model["students"]
    write(ws, "assignment.md", ASSIGNMENT_MD)
    write(ws, "rubric.md", RUBRIC_MD)
    write(ws, "tests/run_tests.py", RUNNER_SRC, executable=True)
    write_csv(ws, "students.csv", ["student", "name", "email", "group"],
              [(s["id"], f"{s['sur']['nom']} {s['first']}", s["email"], s["group"]) for s in students])
    log = sorted((a["t"], s["id"], a["n"]) for s in students for a in s["attempts"])
    write_csv(ws, "submissions_log.csv", ["student", "attempt", "submitted_at"],
              [(sid, n, _iso(t)) for t, sid, n in log])
    for s in students:
        for a in s["attempts"]:
            write(ws, f"submissions/{s['id']}/{a['n']}/solution.py", a["text"])
    for rel, text in thread_files(model).items():
        write(ws, rel, text)


def _write_rows(ws: Path, rows: dict) -> None:
    write_csv(ws, "grades.csv", HEADER, [(sid, *vals) for sid, vals in sorted(rows.items())])


def gold(ws: Path) -> None:
    _write_rows(ws, build()["truth"])


def check(ws: Path) -> str:
    truth = build()["truth"]
    rows = read_csv(ws, "grades.csv", HEADER)
    got: dict[str, tuple] = {}
    for row in rows:
        sid = row.get("student", "").strip()
        vals = []
        for col in HEADER[1:]:
            raw = row.get(col, "").strip()
            try:
                num_val = float(raw.replace(",", "."))
            except ValueError:
                num_val = None
            vals.append(int(num_val) if num_val is not None and num_val == int(num_val) else None)
        got[sid] = tuple(vals)
    extra = set(got) - set(truth)
    assert not extra, f"grades.csv: неизвестные студенты ({len(extra)})"
    correct = sum(1 for sid, exp in truth.items() if got.get(sid) == exp)
    errors = [sid for sid, exp in sorted(truth.items()) if got.get(sid) != exp]
    note = f"строк верно {correct}/{len(truth)}"
    assert correct >= MIN_ROWS, f"{note}, нужно не меньше {MIN_ROWS}; неверные: {', '.join(errors[:8])}"
    return note


def _miss(flags: set[str], name: str):
    def damage(ws: Path) -> None:
        _write_rows(ws, grade_rows(build(), frozenset(flags)))

    damage.__name__ = name
    return damage


NEAR_MISSES = [
    _miss({"last", "tests_only"}, "naive_last_attempt_visible_tests_only"),
    _miss({"no_ext"}, "ignore_email_extensions"),
    _miss({"all_ext"}, "every_request_or_assistant_counts_as_extension"),
    _miss({"tz"}, "log_times_read_as_moscow"),
    _miss({"no_group"}, "ignore_group_deadline_announcement"),
    _miss({"grep_file"}, "forbidden_by_grep_over_whole_file"),
    _miss({"grep_func"}, "forbidden_by_grep_in_function_text_only"),
    _miss({"no_fit"}, "no_test_fitting_check"),
    _miss({"no_plag"}, "no_plagiarism_check"),
    _miss({"over_plag"}, "plagiarism_ignores_permission_and_real_differences"),
    _miss({"no_withdraw"}, "ignore_withdrawal_letters"),
    _miss({"over_withdraw"}, "withdrawals_by_others_cancelled_or_foreign_address_applied"),
    _miss({"g103_first"}, "first_pi103_announcement_not_its_correction"),
]

PROMPT = """Ты — ассистент преподавателя курса «Основы Python». Нужно выставить оценки за ДЗ-3.

В рабочей папке:
- `assignment.md` — условие ДЗ-3 (три задачи);
- `rubric.md` — регламент оценивания: какая попытка оценивается, сроки и продления,
  баллы за тесты, запрещённые конструкции, подгонка под тесты, плагиат, штрафы, формат ведомости;
- `students.csv` — список студентов (90 человек) с группами и адресами;
- `submissions_log.csv` — журнал отправок (время в UTC);
- `submissions/<студент>/<попытка>/solution.py` — все присланные файлы;
- `emails/*.txt` — переписка по курсу (письма свободным текстом, в цепочках с цитатами);
- `tests/run_tests.py` — видимые тесты (`python3 tests/run_tests.py <файл> [...]`).

Материала много, и каждое письмо и каждый файл решения могут влиять на оценку:
регламент требует смотреть на код, а не только на результаты тестов, и учитывать переписку.

Результат — файл `grades.csv` в корне рабочей папки: заголовок
`student,p1,p2,p3,penalty,total` и по одной строке на каждого студента из `students.csv`
(все значения — целые числа, как описано в `rubric.md`). Исходные файлы не изменяй.
"""

TASK = long_task(
    id="task_395_homework_grading",  # registry id; TASK_ID stays the generator seed
    name="Проверка ДЗ по Python: тесты, код, сроки, переписка, плагиат",
    prompt=PROMPT,
    setup=setup,
    gold=gold,
    check=check,
    tags=("grading", "code-reading", "email", "rules"),
)
