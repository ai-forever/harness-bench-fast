"""Tasks 31..60 — second wave of the benchmark.

These are deliberately a bit harder than the first 30: multi-file refactors,
deduplication, log filtering, CSV/markdown conversion, regex-style text
edits. They give the harness profile more opportunities to either help or
hurt; many of them require "do X, then also do Y" — a class of failure
we already saw with `task_20_move_function` and `task_21_rename_file`.

Same conventions as `tasks.py`: each task has setup_files, a Russian-language
prompt, a mechanical verifier, and a gold_files dict for the verify-gold
sanity check.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from harness_bench.core import Task, VerifyResult
from harness_bench.verifiers import (
    all_of,
    file_contains,
    file_does_not_exist,
    file_exists,
    file_lines_equal,
    file_matches_regex,
    file_not_contains,
    file_text_equals,
    python_callable_returns,
)

# ---------------------------------------------------------------------------
# 31. rename_in_multiple_files
# ---------------------------------------------------------------------------
TASK_31 = Task(
    id="task_31_rename_in_multi",
    name="Rename helper() to assist() across two files",
    tags=("edit", "python", "refactor", "multifile", "medium"),
    prompt=(
        "В файлах a.py и b.py есть функция helper() и её вызовы. Переименуй её"
        " в assist() — и определение, и все вызовы — в обоих файлах. Помимо"
        " переименования ничего не меняй."
    ),
    setup_files={
        "a.py": "def helper():\n    return 1\n\n\nprint(helper())\n",
        "b.py": "from a import helper\n\n\ndef wrapper():\n    return helper() + 1\n\n\nprint(wrapper())\n",
    },
    gold_files={
        "a.py": "def assist():\n    return 1\n\n\nprint(assist())\n",
        "b.py": "from a import assist\n\n\ndef wrapper():\n    return assist() + 1\n\n\nprint(wrapper())\n",
    },
    verifier=all_of(
        file_not_contains("a.py", "helper"),
        file_not_contains("b.py", "helper"),
        file_contains("a.py", "def assist", "print(assist())"),
        file_contains("b.py", "from a import assist", "assist() + 1"),
    ),
)


# ---------------------------------------------------------------------------
# 32. count_words
# ---------------------------------------------------------------------------
TASK_32 = Task(
    id="task_32_count_words",
    name="Count words in text.txt",
    tags=("read", "compute", "easy"),
    prompt=(
        "Посчитай количество слов в файле text.txt (разделители — любые"
        " пробельные символы). Запиши получившееся число одной строкой в файл"
        " words.txt (без лишнего текста)."
    ),
    setup_files={"text.txt": "Привет мир как дела сегодня всё хорошо спасибо большое тебе друг\n"},
    gold_files={"words.txt": "11\n"},
    verifier=file_text_equals("words.txt", "11"),
)


# ---------------------------------------------------------------------------
# 33. find_max
# ---------------------------------------------------------------------------
TASK_33 = Task(
    id="task_33_find_max",
    name="Find max number in numbers.txt",
    tags=("read", "compute", "easy"),
    prompt=(
        "В файле numbers.txt лежат целые числа — по одному на строку. Найди"
        " максимум и запиши его одной строкой в файл max.txt (только число)."
    ),
    setup_files={"numbers.txt": "3\n17\n5\n42\n8\n23\n11\n"},
    gold_files={"max.txt": "42\n"},
    verifier=file_text_equals("max.txt", "42"),
)


# ---------------------------------------------------------------------------
# 34. filter_errors
# ---------------------------------------------------------------------------
_APP_LOG = (
    "INFO: server started\n"
    "INFO: client connected\n"
    "ERROR: db timeout after 30s\n"
    "WARN: retry attempt 1\n"
    "ERROR: db unreachable\n"
    "INFO: shutdown\n"
)
_ERRORS_GOLD = "ERROR: db timeout after 30s\nERROR: db unreachable\n"

TASK_34 = Task(
    id="task_34_filter_errors",
    name="Extract ERROR lines from app.log",
    tags=("read", "search", "medium"),
    prompt=(
        "Из файла app.log выбери только строки, которые начинаются с 'ERROR:',"
        " и запиши их (в исходном порядке) в файл errors.log. Остальные"
        " строки в errors.log включать не нужно."
    ),
    setup_files={"app.log": _APP_LOG},
    gold_files={"errors.log": _ERRORS_GOLD},
    verifier=file_lines_equal(
        "errors.log",
        ["ERROR: db timeout after 30s", "ERROR: db unreachable"],
    ),
)


# ---------------------------------------------------------------------------
# 35. remove_blank_lines
# ---------------------------------------------------------------------------
TASK_35 = Task(
    id="task_35_remove_blank_lines",
    name="Remove blank lines from notes.txt",
    tags=("read", "edit", "easy"),
    prompt=(
        "В файле notes.txt есть пустые строки. Удали их (то есть отфильтруй"
        " строки, состоящие только из пробельных символов или вообще пустые)"
        " и запиши результат в файл cleaned.txt — в исходном порядке оставшихся"
        " строк."
    ),
    setup_files={
        "notes.txt": "первая\n\nвторая\n  \nтретья\n\n\nчетвёртая\n",
    },
    gold_files={"cleaned.txt": "первая\nвторая\nтретья\nчетвёртая\n"},
    verifier=file_lines_equal("cleaned.txt", ["первая", "вторая", "третья", "четвёртая"]),
)


# ---------------------------------------------------------------------------
# 36. add_imports
# ---------------------------------------------------------------------------
TASK_36 = Task(
    id="task_36_add_imports",
    name="Add `import os` and `import sys` to script.py",
    tags=("edit", "python", "easy"),
    prompt=(
        "В файл script.py добавь в самое начало две строки импортов: 'import os'"
        " и 'import sys' (именно в этом порядке, каждая на отдельной строке)."
        " Остальное содержимое файла не меняй."
    ),
    setup_files={"script.py": "print('hello')\n"},
    gold_files={"script.py": "import os\nimport sys\n\nprint('hello')\n"},
    verifier=all_of(
        file_matches_regex(
            "script.py",
            r"\Aimport os\nimport sys\n",
        ),
        file_contains("script.py", "print('hello')"),
    ),
)


# ---------------------------------------------------------------------------
# 37. make_pytest_test
# ---------------------------------------------------------------------------
TASK_37 = Task(
    id="task_37_make_pytest_test",
    name="Create a pytest test for add()",
    tags=("create", "python", "tests", "medium"),
    prompt=(
        "В каталоге tests создай файл test_math.py с функцией"
        " test_add(), которая импортирует функцию add из модуля math_utils"
        " (math_utils.py лежит в корне рабочей директории) и проверяет, что"
        " add(2, 3) == 5 через assert. Никаких дополнительных тестов не нужно."
    ),
    setup_files={"math_utils.py": "def add(a, b):\n    return a + b\n"},
    gold_files={
        "tests/test_math.py": (
            "from math_utils import add\n"
            "\n"
            "\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        ),
    },
    verifier=all_of(
        file_exists("tests/test_math.py"),
        file_contains(
            "tests/test_math.py",
            "from math_utils import add",
            "def test_add",
            "add(2, 3) == 5",
        ),
    ),
)


# ---------------------------------------------------------------------------
# 38. trim_trailing_ws
# ---------------------------------------------------------------------------
TASK_38 = Task(
    id="task_38_trim_trailing_ws",
    name="Strip trailing whitespace from messy.txt",
    tags=("edit", "text", "medium"),
    prompt=(
        "В каждой строке файла messy.txt удали пробельные символы (пробелы и"
        " табы) в конце строки, не меняя самих строк и их порядка. Запиши"
        " результат в файл trimmed.txt. Пустые строки оставляй пустыми."
    ),
    setup_files={
        "messy.txt": "alpha   \nbeta\t\ngamma  \t \ndelta\n",
    },
    gold_files={"trimmed.txt": "alpha\nbeta\ngamma\ndelta\n"},
    verifier=file_lines_equal("trimmed.txt", ["alpha", "beta", "gamma", "delta"]),
)


# ---------------------------------------------------------------------------
# 39. reverse_lines
# ---------------------------------------------------------------------------
TASK_39 = Task(
    id="task_39_reverse_lines",
    name="Reverse lines from source.txt",
    tags=("read", "compute", "easy"),
    prompt=(
        "Возьми строки из файла source.txt и запиши их в файл reversed.txt"
        " в обратном порядке (последняя строка становится первой). Сами"
        " строки не меняй."
    ),
    setup_files={"source.txt": "one\ntwo\nthree\nfour\nfive\n"},
    gold_files={"reversed.txt": "five\nfour\nthree\ntwo\none\n"},
    verifier=file_lines_equal("reversed.txt", ["five", "four", "three", "two", "one"]),
)


# ---------------------------------------------------------------------------
# 40. merge_files
# ---------------------------------------------------------------------------
TASK_40 = Task(
    id="task_40_merge_files",
    name="Merge parts/a.txt, parts/b.txt, parts/c.txt",
    tags=("read", "filesystem", "medium"),
    prompt=(
        "В каталоге parts лежат три файла: a.txt, b.txt, c.txt. Объедини их"
        " содержимое (в этом же порядке) в один файл merged.txt в корне рабочей"
        " директории. Между содержимым файлов лишних разделителей добавлять не"
        " нужно — просто склей по очереди."
    ),
    setup_files={
        "parts/a.txt": "alpha line 1\nalpha line 2\n",
        "parts/b.txt": "beta line 1\nbeta line 2\n",
        "parts/c.txt": "gamma line 1\n",
    },
    gold_files={
        "merged.txt": "alpha line 1\nalpha line 2\nbeta line 1\nbeta line 2\ngamma line 1\n",
    },
    verifier=file_lines_equal(
        "merged.txt",
        [
            "alpha line 1",
            "alpha line 2",
            "beta line 1",
            "beta line 2",
            "gamma line 1",
        ],
    ),
)


# ---------------------------------------------------------------------------
# 41. count_word_occurrences
# ---------------------------------------------------------------------------
TASK_41 = Task(
    id="task_41_count_todos",
    name="Count occurrences of TODO across files",
    tags=("read", "search", "medium"),
    prompt=(
        "Посчитай общее число вхождений слова 'TODO' (с учётом регистра, точное"
        " совпадение подстроки) во всех файлах в текущей директории и подкаталогах."
        " Запиши получившееся число одной строкой в файл count.txt."
    ),
    setup_files={
        "a.py": "# TODO: fix\nx = 1\n",
        "b.py": "y = 2  # TODO\n# nothing here\n# TODO and TODO\n",
        "c.txt": "TODO\n",
    },
    gold_files={"count.txt": "5\n"},
    verifier=file_text_equals("count.txt", "5"),
)


# ---------------------------------------------------------------------------
# 42. rename_var_in_files
# ---------------------------------------------------------------------------
TASK_42 = Task(
    id="task_42_snake_case",
    name="Rename `userName` to `user_name` in two files",
    tags=("edit", "python", "refactor", "multifile", "medium"),
    prompt=(
        "В файлах one.py и two.py переименуй переменную userName в user_name"
        " во всех её появлениях. Никаких других изменений делать не надо."
    ),
    setup_files={
        "one.py": "userName = 'Alice'\nprint(userName)\n",
        "two.py": "from one import userName\n\n\nprint(f'hi {userName}')\n",
    },
    gold_files={
        "one.py": "user_name = 'Alice'\nprint(user_name)\n",
        "two.py": "from one import user_name\n\n\nprint(f'hi {user_name}')\n",
    },
    verifier=all_of(
        file_not_contains("one.py", "userName"),
        file_not_contains("two.py", "userName"),
        file_contains("one.py", "user_name = 'Alice'", "print(user_name)"),
        file_contains("two.py", "from one import user_name", "{user_name}"),
    ),
)


# ---------------------------------------------------------------------------
# 43. longest_line
# ---------------------------------------------------------------------------
TASK_43 = Task(
    id="task_43_longest_line",
    name="Write longest line of text.txt into longest.txt",
    tags=("read", "compute", "easy"),
    prompt=(
        "В файле text.txt несколько строк разной длины. Найди самую длинную"
        " (по количеству символов; если такие есть несколько — возьми первую"
        " по порядку) и запиши её одной строкой в файл longest.txt."
    ),
    setup_files={
        "text.txt": (
            "short\n"
            "this line is the longest of all\n"
            "medium length here\n"
            "tiny\n"
        ),
    },
    gold_files={"longest.txt": "this line is the longest of all\n"},
    verifier=file_text_equals("longest.txt", "this line is the longest of all"),
)


# ---------------------------------------------------------------------------
# 44. create_requirements
# ---------------------------------------------------------------------------
TASK_44 = Task(
    id="task_44_create_requirements",
    name="Create requirements.txt with three deps",
    tags=("create", "config", "easy"),
    prompt=(
        "Создай файл requirements.txt со следующими тремя строками (каждая"
        " на отдельной строке, в указанном порядке):\n"
        "1) requests==2.31.0\n"
        "2) pydantic>=2.0\n"
        "3) httpx"
    ),
    setup_files={},
    gold_files={"requirements.txt": "requests==2.31.0\npydantic>=2.0\nhttpx\n"},
    verifier=file_lines_equal(
        "requirements.txt", ["requests==2.31.0", "pydantic>=2.0", "httpx"]
    ),
)


# ---------------------------------------------------------------------------
# 45. inline_constant
# ---------------------------------------------------------------------------
TASK_45 = Task(
    id="task_45_inline_constant",
    name="Inline MAX_RETRIES constant in retry.py",
    tags=("edit", "python", "refactor", "medium"),
    prompt=(
        "В файле retry.py определена константа MAX_RETRIES = 3 и она используется"
        " в функции attempt() как `for _ in range(MAX_RETRIES):`. Выпиши значение"
        " константы вручную: убери строку 'MAX_RETRIES = 3' целиком и в теле"
        " функции замени 'range(MAX_RETRIES)' на 'range(3)'. Сама функция и её"
        " return должны остаться рабочими."
    ),
    setup_files={
        "retry.py": (
            "MAX_RETRIES = 3\n"
            "\n"
            "\n"
            "def attempt():\n"
            "    result = []\n"
            "    for _ in range(MAX_RETRIES):\n"
            "        result.append('try')\n"
            "    return result\n"
        ),
    },
    gold_files={
        "retry.py": (
            "def attempt():\n"
            "    result = []\n"
            "    for _ in range(3):\n"
            "        result.append('try')\n"
            "    return result\n"
        ),
    },
    verifier=all_of(
        file_not_contains("retry.py", "MAX_RETRIES"),
        file_contains("retry.py", "for _ in range(3):", "return result"),
        python_callable_returns("retry.py", "mod.attempt()", ["try", "try", "try"]),
    ),
)


# ---------------------------------------------------------------------------
# 46. add_init_export
# ---------------------------------------------------------------------------
TASK_46 = Task(
    id="task_46_add_init_export",
    name="Create src/__init__.py with __all__ = ['foo', 'bar']",
    tags=("create", "python", "easy"),
    prompt=(
        "В каталоге src создай файл __init__.py. В нём должно быть ровно одно"
        " присваивание: переменной __all__ списка ['foo', 'bar'] (именно такой"
        " список из двух строк, в указанном порядке). Иных строк добавлять не"
        " нужно."
    ),
    setup_files={"src/foo.py": "def f():\n    return 1\n"},
    gold_files={"src/__init__.py": '__all__ = ["foo", "bar"]\n'},
    verifier=all_of(
        file_exists("src/__init__.py"),
        file_matches_regex(
            "src/__init__.py",
            r"__all__\s*=\s*\[\s*['\"]foo['\"]\s*,\s*['\"]bar['\"]\s*\]",
        ),
    ),
)


# ---------------------------------------------------------------------------
# 47. dedupe_lines
# ---------------------------------------------------------------------------
TASK_47 = Task(
    id="task_47_dedupe_lines",
    name="Deduplicate lines preserving first-seen order",
    tags=("read", "compute", "medium"),
    prompt=(
        "В файле data.txt есть повторяющиеся строки. Удали дубликаты, оставляя"
        " только первое появление каждой строки, и сохрани результат в файл"
        " deduped.txt — порядок первых появлений нужно сохранить, перестановки"
        " не допускаются."
    ),
    setup_files={"data.txt": "apple\nbanana\napple\ncherry\nbanana\napple\ndate\n"},
    gold_files={"deduped.txt": "apple\nbanana\ncherry\ndate\n"},
    verifier=file_lines_equal("deduped.txt", ["apple", "banana", "cherry", "date"]),
)


# ---------------------------------------------------------------------------
# 48. append_eof_to_each
# ---------------------------------------------------------------------------
def _verify_task_48(ws: Path) -> VerifyResult:
    log_dir = ws / "logs"
    if not log_dir.is_dir():
        return VerifyResult(False, "logs/ directory missing")
    expected_names = {"first.log", "second.log", "third.log"}
    actual_names = {p.name for p in log_dir.glob("*.log")}
    if actual_names != expected_names:
        return VerifyResult(
            False,
            f"logs/ contents differ: got {sorted(actual_names)}, expected {sorted(expected_names)}",
        )
    for name, original_first_line in [
        ("first.log", "one alpha"),
        ("second.log", "two beta"),
        ("third.log", "three gamma"),
    ]:
        lines = (log_dir / name).read_text().splitlines()
        if lines[-1] != "EOF":
            return VerifyResult(False, f"logs/{name}: last line is {lines[-1]!r}, expected 'EOF'")
        if original_first_line not in lines:
            return VerifyResult(
                False, f"logs/{name}: lost original line {original_first_line!r}"
            )
    return VerifyResult(True, "all logs/*.log end with EOF and keep original content")


TASK_48 = Task(
    id="task_48_append_eof_each",
    name="Append `EOF` to every log in logs/",
    tags=("edit", "filesystem", "multifile", "medium"),
    prompt=(
        "В каждый файл в каталоге logs (там лежат first.log, second.log,"
        " third.log) добавь в самый конец одну строку: EOF. Существующее"
        " содержимое каждого файла оставь без изменений."
    ),
    setup_files={
        "logs/first.log": "one alpha\n",
        "logs/second.log": "two beta\ntwo beta extra\n",
        "logs/third.log": "three gamma\n",
    },
    gold_files={
        "logs/first.log": "one alpha\nEOF\n",
        "logs/second.log": "two beta\ntwo beta extra\nEOF\n",
        "logs/third.log": "three gamma\nEOF\n",
    },
    verifier=_verify_task_48,
)


# ---------------------------------------------------------------------------
# 49. csv_to_markdown
# ---------------------------------------------------------------------------
def _verify_task_49(ws: Path) -> VerifyResult:
    p = ws / "table.md"
    if not p.exists():
        return VerifyResult(False, "table.md missing")
    text = p.read_text()
    required = [
        "| name | age |",
        "Alice",
        "30",
        "Bob",
        "25",
    ]
    missing = [s for s in required if s not in text]
    if missing:
        return VerifyResult(False, f"table.md missing pieces: {missing!r}")
    if "---" not in text and ":--" not in text and "--|" not in text:
        return VerifyResult(False, "table.md has no markdown separator row (`|---|---|`)")
    return VerifyResult(True, "table.md looks like a valid markdown table")


TASK_49 = Task(
    id="task_49_csv_to_markdown",
    name="Render data.csv as a markdown table",
    tags=("read", "compute", "medium"),
    prompt=(
        "Прочитай файл data.csv (первая строка — заголовки name,age; дальше две"
        " строки данных) и сохрани его в виде markdown-таблицы в файле table.md."
        " Шапка должна выглядеть как `| name | age |`, под ней — строка-разделитель"
        " с дефисами (например `| --- | --- |`), а ниже — две строки данных:"
        " `| Alice | 30 |` и `| Bob | 25 |`. Каждый ряд — одна строка файла."
    ),
    setup_files={"data.csv": "name,age\nAlice,30\nBob,25\n"},
    gold_files={
        "table.md": (
            "| name | age |\n"
            "| --- | --- |\n"
            "| Alice | 30 |\n"
            "| Bob | 25 |\n"
        ),
    },
    verifier=_verify_task_49,
)


# ---------------------------------------------------------------------------
# 50. fix_typo_everywhere
# ---------------------------------------------------------------------------
TASK_50 = Task(
    id="task_50_fix_typo",
    name='Fix "recieve" -> "receive" across docs',
    tags=("edit", "text", "multifile", "medium"),
    prompt=(
        "В файлах docs/intro.md и docs/usage.md встречается слово 'recieve' с"
        " опечаткой. Замени все его вхождения на правильное 'receive'."
        " Остального текста не трогай."
    ),
    setup_files={
        "docs/intro.md": "# Intro\n\nYou will recieve a token.\nThen recieve a response.\n",
        "docs/usage.md": "## Usage\n\nrecieve the message and reply.\n",
    },
    gold_files={
        "docs/intro.md": "# Intro\n\nYou will receive a token.\nThen receive a response.\n",
        "docs/usage.md": "## Usage\n\nreceive the message and reply.\n",
    },
    verifier=all_of(
        file_not_contains("docs/intro.md", "recieve"),
        file_not_contains("docs/usage.md", "recieve"),
        file_contains("docs/intro.md", "receive a token", "receive a response"),
        file_contains("docs/usage.md", "receive the message"),
    ),
)


# ---------------------------------------------------------------------------
# 51. count_total_py_lines
# ---------------------------------------------------------------------------
TASK_51 = Task(
    id="task_51_count_total_lines",
    name="Sum lines across all .py files",
    tags=("read", "search", "compute", "medium"),
    prompt=(
        "Посчитай суммарное количество строк во всех .py-файлах в рабочей"
        " директории (включая подкаталоги). Считай каждую строку файла,"
        " включая пустые. Запиши получившееся число одной строкой в файл"
        " total.txt."
    ),
    setup_files={
        "a.py": "x = 1\nprint(x)\n",  # 2 lines
        "b.py": "y = 2\n\nprint(y)\n",  # 3 lines
        "pkg/c.py": "z = 3\n",  # 1 line
        "pkg/sub/d.py": "w = 4\nprint(w)\n\n# trailing\n",  # 4 lines
        "notes.txt": "ignored",  # not .py
    },
    gold_files={"total.txt": "10\n"},
    verifier=file_text_equals("total.txt", "10"),
)


# ---------------------------------------------------------------------------
# 52. find_files_with_marker
# ---------------------------------------------------------------------------
def _verify_task_52(ws: Path) -> VerifyResult:
    p = ws / "files.txt"
    if not p.exists():
        return VerifyResult(False, "files.txt missing")
    lines = [line.strip() for line in p.read_text().splitlines() if line.strip()]
    # Accept either basename or relative path with or without leading "./".
    normalized = {line.removeprefix("./").removesuffix("/") for line in lines}
    expected_sets = [
        {"alpha.py", "gamma.py"},
        {"./alpha.py", "./gamma.py"},
    ]
    for exp in expected_sets:
        if normalized == {e.removeprefix("./") for e in exp}:
            return VerifyResult(True, "files.txt has expected file names")
    return VerifyResult(
        False,
        f"files.txt entries {sorted(normalized)} do not match expected {{'alpha.py', 'gamma.py'}}",
    )


TASK_52 = Task(
    id="task_52_find_files_with",
    name="List files containing MARKER",
    tags=("read", "search", "medium"),
    prompt=(
        "Найди все файлы в текущей рабочей директории (без подкаталогов), в"
        " которых встречается подстрока MARKER. Запиши их имена (без префиксов"
        " вроде путей) в файл files.txt — каждое имя на отдельной строке. Порядок"
        " не важен."
    ),
    setup_files={
        "alpha.py": "# MARKER\nx = 1\n",
        "beta.py": "y = 2\n",
        "gamma.py": "z = 3  # has MARKER inline\n",
        "delta.py": "w = 4\n",
    },
    gold_files={"files.txt": "alpha.py\ngamma.py\n"},
    verifier=_verify_task_52,
)


# ---------------------------------------------------------------------------
# 53. add_shebang
# ---------------------------------------------------------------------------
TASK_53 = Task(
    id="task_53_add_shebang",
    name="Add shebang to script.py",
    tags=("edit", "python", "easy"),
    prompt=(
        "Добавь в самое начало файла script.py строку '#!/usr/bin/env python3'"
        " (как первую строку файла). Остальное содержимое сохрани без изменений."
    ),
    setup_files={"script.py": "print('hi')\n"},
    gold_files={"script.py": "#!/usr/bin/env python3\nprint('hi')\n"},
    verifier=all_of(
        file_matches_regex("script.py", r"\A#!/usr/bin/env python3\n"),
        file_contains("script.py", "print('hi')"),
    ),
)


# ---------------------------------------------------------------------------
# 54. swap_csv_cols
# ---------------------------------------------------------------------------
def _verify_task_54(ws: Path) -> VerifyResult:
    p = ws / "users.csv"
    if not p.exists():
        return VerifyResult(False, "users.csv missing")
    rows = list(csv.reader(io.StringIO(p.read_text())))
    if not rows:
        return VerifyResult(False, "users.csv empty")
    header = rows[0]
    if header[:2] != ["age", "name"]:
        return VerifyResult(False, f"header is {header!r}, expected ['age', 'name', ...]")
    data = rows[1:]
    # We expect rows (age, name) — same data, swapped columns
    expected_pairs = {("30", "Alice"), ("25", "Bob")}
    actual_pairs = {(row[0], row[1]) for row in data if len(row) >= 2}
    if expected_pairs != actual_pairs:
        return VerifyResult(
            False,
            f"data rows {sorted(actual_pairs)} do not match expected {sorted(expected_pairs)}",
        )
    return VerifyResult(True, "users.csv columns swapped correctly")


TASK_54 = Task(
    id="task_54_swap_csv_cols",
    name="Swap name and age columns in users.csv",
    tags=("edit", "csv", "medium"),
    prompt=(
        "В файле users.csv два столбца — name и age. Поменяй их местами:"
        " теперь первым столбцом должен быть age, вторым — name. Это касается"
        " и строки-заголовка, и строк с данными. Файл должен остаться валидным"
        " CSV (запятая как разделитель, без лишних кавычек)."
    ),
    setup_files={"users.csv": "name,age\nAlice,30\nBob,25\n"},
    gold_files={"users.csv": "age,name\n30,Alice\n25,Bob\n"},
    verifier=_verify_task_54,
)


# ---------------------------------------------------------------------------
# 55. add_conftest
# ---------------------------------------------------------------------------
TASK_55 = Task(
    id="task_55_add_conftest",
    name="Create tests/conftest.py with a fixture",
    tags=("create", "python", "tests", "medium"),
    prompt=(
        "В каталоге tests создай файл conftest.py со следующим содержимым: импорт"
        " pytest, после него — функция sample_data, помеченная декоратором"
        " @pytest.fixture, возвращающая словарь {'name': 'Alice', 'age': 30}."
        " Других определений добавлять не нужно."
    ),
    setup_files={"tests/.keep": ""},
    gold_files={
        "tests/conftest.py": (
            "import pytest\n"
            "\n"
            "\n"
            "@pytest.fixture\n"
            "def sample_data():\n"
            "    return {'name': 'Alice', 'age': 30}\n"
        ),
    },
    verifier=all_of(
        file_exists("tests/conftest.py"),
        file_contains(
            "tests/conftest.py",
            "import pytest",
            "@pytest.fixture",
            "def sample_data",
            "name",
            "Alice",
            "30",
        ),
    ),
)


# ---------------------------------------------------------------------------
# 56. move_to_subdir
# ---------------------------------------------------------------------------
TASK_56 = Task(
    id="task_56_move_to_subdir",
    name="Convert utils.py to a package",
    tags=("edit", "filesystem", "refactor", "hard"),
    prompt=(
        "Сейчас в корне рабочей директории лежит файл utils.py. Преврати его в"
        " пакет: создай каталог utils и положи в него файл __init__.py с тем же"
        " содержимым, что было в utils.py. Старый файл utils.py из корня нужно"
        " удалить."
    ),
    setup_files={
        "utils.py": "def slug(s):\n    return s.lower()\n",
    },
    gold_files={
        "utils.py": None,
        "utils/__init__.py": "def slug(s):\n    return s.lower()\n",
    },
    verifier=all_of(
        file_does_not_exist("utils.py"),
        file_exists("utils/__init__.py"),
        file_contains("utils/__init__.py", "def slug", "return s.lower()"),
        python_callable_returns(
            "utils/__init__.py", "mod.slug('Hello')", "hello"
        ),
    ),
)


# ---------------------------------------------------------------------------
# 57. add_main_guard
# ---------------------------------------------------------------------------
TASK_57 = Task(
    id="task_57_add_main_guard",
    name="Add `if __name__ == '__main__': main()` to runme.py",
    tags=("edit", "python", "easy"),
    prompt=(
        "В файле runme.py в самый конец добавь блок\n"
        "    if __name__ == '__main__':\n"
        "        main()\n"
        "Перед этим блоком оставь одну пустую строку. Существующий код выше"
        " не меняй."
    ),
    setup_files={
        "runme.py": "def main():\n    print('hi')\n",
    },
    gold_files={
        "runme.py": (
            "def main():\n"
            "    print('hi')\n"
            "\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
    },
    verifier=all_of(
        file_contains(
            "runme.py",
            "def main():",
            "if __name__ ==",
            "main()",
        ),
        file_matches_regex(
            "runme.py",
            r"if __name__\s*==\s*['\"]__main__['\"]\s*:\s*\n\s+main\(\)",
        ),
    ),
)


# ---------------------------------------------------------------------------
# 58. find_replace_phone
# ---------------------------------------------------------------------------
def _verify_task_58(ws: Path) -> VerifyResult:
    p = ws / "phones.txt"
    if not p.exists():
        return VerifyResult(False, "phones.txt missing")
    expected = ["+7 (999) 123-45-67", "+7 (495) 555-12-34"]
    actual = [line for line in p.read_text().splitlines() if line.strip()]
    if actual != expected:
        return VerifyResult(
            False, f"phones.txt content {actual!r} differs from expected {expected!r}"
        )
    return VerifyResult(True, "phones.txt formatted correctly")


TASK_58 = Task(
    id="task_58_format_phones",
    name="Reformat phones.txt to +7 (xxx) xxx-xx-xx",
    tags=("edit", "text", "compute", "medium"),
    prompt=(
        "В файле phones.txt две строки с телефонами в формате "
        "'8 XXX XXXXXXX' (три части через пробел; первая часть — '8',"
        " вторая — три цифры кода, третья — семь цифр номера)."
        " Перепиши их в формат '+7 (XXX) XXX-XX-XX' (с пробелом после '+7' и"
        " после закрывающей скобки, и с дефисами в номере). Каждый телефон"
        " по-прежнему на своей строке, порядок сохраняй."
    ),
    setup_files={"phones.txt": "8 999 1234567\n8 495 5551234\n"},
    gold_files={"phones.txt": "+7 (999) 123-45-67\n+7 (495) 555-12-34\n"},
    verifier=_verify_task_58,
)


# ---------------------------------------------------------------------------
# 59. unique_words_sorted
# ---------------------------------------------------------------------------
def _verify_task_59(ws: Path) -> VerifyResult:
    p = ws / "unique_words.txt"
    if not p.exists():
        return VerifyResult(False, "unique_words.txt missing")
    actual = [line for line in p.read_text().splitlines() if line.strip()]
    expected = sorted({"foo", "bar", "baz"})
    if actual == expected:
        return VerifyResult(True, "unique_words.txt sorted unique words match")
    return VerifyResult(
        False, f"unique_words.txt content {actual!r} differs from expected {expected!r}"
    )


TASK_59 = Task(
    id="task_59_unique_words",
    name="Write unique words from text.txt sorted alphabetically",
    tags=("read", "compute", "medium"),
    prompt=(
        "В файле text.txt есть слова, разделённые пробелами (могут повторяться)."
        " Извлеки все уникальные слова и запиши их в файл unique_words.txt — каждое"
        " слово с новой строки, в алфавитном порядке по возрастанию."
    ),
    setup_files={"text.txt": "foo bar foo baz bar foo baz\n"},
    gold_files={"unique_words.txt": "bar\nbaz\nfoo\n"},
    verifier=_verify_task_59,
)


# ---------------------------------------------------------------------------
# 60. rename_directory
# ---------------------------------------------------------------------------
def _verify_task_60(ws: Path) -> VerifyResult:
    old = ws / "src" / "old"
    new = ws / "src" / "new"
    if old.exists():
        return VerifyResult(False, "src/old/ still exists")
    if not new.is_dir():
        return VerifyResult(False, "src/new/ directory missing")
    expected = {
        "alpha.py": "ALPHA = 1\n",
        "beta.py": "BETA = 2\n",
    }
    for name, content in expected.items():
        f = new / name
        if not f.exists():
            return VerifyResult(False, f"src/new/{name} missing")
        actual = f.read_text()
        if actual != content:
            return VerifyResult(
                False, f"src/new/{name} content differs: {actual!r} vs {content!r}"
            )
    return VerifyResult(True, "src/old/ renamed to src/new/ with content preserved")


TASK_60 = Task(
    id="task_60_rename_dir",
    name="Move src/old/* to src/new/",
    tags=("edit", "filesystem", "refactor", "hard"),
    prompt=(
        "Переименуй каталог src/old в src/new — все файлы alpha.py и beta.py из"
        " него должны оказаться в src/new с прежним содержимым; пустого или"
        " непустого каталога src/old после этого быть не должно."
    ),
    setup_files={
        "src/old/alpha.py": "ALPHA = 1\n",
        "src/old/beta.py": "BETA = 2\n",
    },
    gold_files={
        "src/old/alpha.py": None,
        "src/old/beta.py": None,
        "src/new/alpha.py": "ALPHA = 1\n",
        "src/new/beta.py": "BETA = 2\n",
    },
    verifier=_verify_task_60,
)


EXTRA_TASKS: list[Task] = [
    TASK_31,
    TASK_32,
    TASK_33,
    TASK_34,
    TASK_35,
    TASK_36,
    TASK_37,
    TASK_38,
    TASK_39,
    TASK_40,
    TASK_41,
    TASK_42,
    TASK_43,
    TASK_44,
    TASK_45,
    TASK_46,
    TASK_47,
    TASK_48,
    TASK_49,
    TASK_50,
    TASK_51,
    TASK_52,
    TASK_53,
    TASK_54,
    TASK_55,
    TASK_56,
    TASK_57,
    TASK_58,
    TASK_59,
    TASK_60,
]


# json/re are imported above to keep this module self-contained even though
# the helpers used here happen not to call them; keep them around in case
# follow-up tasks need them.
_ = (json, re)
