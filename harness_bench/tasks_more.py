"""Tasks 61..100 — third wave of the benchmark.

Same conventions as `tasks.py` / `tasks_extra.py`: every task ships its own
setup_files, a Russian prompt, a mechanical verifier, and a gold_files dict
for the verify-gold sanity check.

The new tasks lean into things that have been brittle so far:
- "do X *and* don't do Y" — verify both halves
- whole-file rewrites where line order matters
- multi-key JSON / TOML edits
- arithmetic with bigger inputs (so the model has to think rather than
  pattern-match)
- env files, INI/TOML configs, dotfiles
- regex-style replacements that span multiple lines
"""

from __future__ import annotations

import json
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
    json_file_has,
    python_callable_returns,
)

# ---------------------------------------------------------------------------
# 61. add_env_var
# ---------------------------------------------------------------------------
TASK_61 = Task(
    id="task_61_add_env_var",
    name="Add LOG_LEVEL=INFO to .env",
    tags=("edit", "config", "easy"),
    prompt=(
        "В файле .env уже есть две переменные. Добавь в конец файла новую"
        " строку LOG_LEVEL=INFO (после существующих, на отдельной строке)."
        " Существующие строки оставь без изменений и в исходном порядке."
    ),
    setup_files={".env": "API_KEY=secret\nDEBUG=false\n"},
    gold_files={".env": "API_KEY=secret\nDEBUG=false\nLOG_LEVEL=INFO\n"},
    verifier=file_lines_equal(".env", ["API_KEY=secret", "DEBUG=false", "LOG_LEVEL=INFO"]),
)


# ---------------------------------------------------------------------------
# 62. remove_env_var
# ---------------------------------------------------------------------------
TASK_62 = Task(
    id="task_62_remove_env_var",
    name="Remove DEBUG line from .env",
    tags=("edit", "config", "easy"),
    prompt=(
        "В файле .env три переменные. Удали строку, начинающуюся с 'DEBUG=' —"
        " так, чтобы её совсем не было. Остальные две строки сохрани в исходном"
        " порядке."
    ),
    setup_files={".env": "API_KEY=secret\nDEBUG=false\nLOG_LEVEL=INFO\n"},
    gold_files={".env": "API_KEY=secret\nLOG_LEVEL=INFO\n"},
    verifier=all_of(
        file_lines_equal(".env", ["API_KEY=secret", "LOG_LEVEL=INFO"]),
        file_not_contains(".env", "DEBUG"),
    ),
)


# ---------------------------------------------------------------------------
# 63. nested_json_edit
# ---------------------------------------------------------------------------
_CONFIG_JSON_INITIAL = (
    "{\n"
    '  "name": "app",\n'
    '  "database": {\n'
    '    "host": "localhost",\n'
    '    "port": 5432\n'
    "  }\n"
    "}\n"
)


def _verify_task_63(ws: Path) -> VerifyResult:
    p = ws / "config.json"
    if not p.exists():
        return VerifyResult(False, "config.json missing")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"invalid JSON: {exc}")
    if data.get("name") != "app":
        return VerifyResult(False, f"name changed: {data.get('name')!r}")
    db = data.get("database")
    if not isinstance(db, dict):
        return VerifyResult(False, f"database is not an object: {db!r}")
    if db.get("host") != "db.internal":
        return VerifyResult(False, f"database.host is {db.get('host')!r}, expected 'db.internal'")
    if db.get("port") != 5432:
        return VerifyResult(False, f"database.port changed: {db.get('port')!r}")
    return VerifyResult(True, "database.host updated, other fields intact")


TASK_63 = Task(
    id="task_63_nested_json_edit",
    name="Update database.host in config.json",
    tags=("edit", "json", "medium"),
    prompt=(
        "В файле config.json внутри объекта database измени значение поля"
        " host с 'localhost' на 'db.internal'. Поле name, поле port и сама"
        " структура (вложенность database) должны остаться без изменений."
        " Файл должен остаться валидным JSON."
    ),
    setup_files={"config.json": _CONFIG_JSON_INITIAL},
    gold_files={
        "config.json": _CONFIG_JSON_INITIAL.replace("localhost", "db.internal"),
    },
    verifier=_verify_task_63,
)


# ---------------------------------------------------------------------------
# 64. add_logger_call
# ---------------------------------------------------------------------------
TASK_64 = Task(
    id="task_64_add_logger_call",
    name="Add logging to process()",
    tags=("edit", "python", "medium"),
    prompt=(
        "В файле app.py есть функция process(x). Добавь в самое начало её"
        " тела (первой строкой, до 'return x * 2') вызов"
        " 'print(f\"processing {x}\")'. Импорты и сигнатуру функции не меняй."
    ),
    setup_files={"app.py": "def process(x):\n    return x * 2\n"},
    gold_files={
        "app.py": (
            "def process(x):\n"
            '    print(f"processing {x}")\n'
            "    return x * 2\n"
        ),
    },
    # Behaviour: must print "processing 5" before returning 10. Using
    # `file_matches_regex` to assert the print sits ABOVE the return.
    verifier=all_of(
        file_contains("app.py", 'print(f"processing {x}")'),
        file_matches_regex(
            "app.py",
            r"def\s+process\s*\(\s*x\s*\)\s*:\s*\n\s*print\(f\"processing \{x\}\"\)\s*\n\s*return\s+x\s*\*\s*2",
        ),
    ),
)


# ---------------------------------------------------------------------------
# 65. sum_floats_3decimals
# ---------------------------------------------------------------------------
TASK_65 = Task(
    id="task_65_sum_floats",
    name="Sum floats with 3 decimal places",
    tags=("read", "compute", "medium"),
    prompt=(
        "В файле numbers.txt лежат вещественные числа по одному в строке."
        " Посчитай их сумму и запиши результат в файл sum.txt одной строкой,"
        " округлив до трёх знаков после запятой (используй точку как"
        " десятичный разделитель)."
    ),
    setup_files={"numbers.txt": "1.5\n2.25\n3.125\n0.625\n"},
    gold_files={"sum.txt": "7.500\n"},
    verifier=file_text_equals("sum.txt", "7.500"),
)


# ---------------------------------------------------------------------------
# 66. join_with_comma
# ---------------------------------------------------------------------------
TASK_66 = Task(
    id="task_66_join_with_comma",
    name="Join words from list.txt into csv.txt",
    tags=("read", "compute", "easy"),
    prompt=(
        "В файле list.txt лежат слова по одному на строку. Соедини их через"
        " запятую без пробелов в одну строку и запиши результат в файл"
        " csv.txt — одна строка, без перевода строки в конце не обязательно,"
        " но и наличие финального перевода строки допустимо."
    ),
    setup_files={"list.txt": "apple\nbanana\ncherry\ndate\n"},
    gold_files={"csv.txt": "apple,banana,cherry,date\n"},
    verifier=file_text_equals("csv.txt", "apple,banana,cherry,date"),
)


# ---------------------------------------------------------------------------
# 67. replace_in_logs
# ---------------------------------------------------------------------------
TASK_67 = Task(
    id="task_67_replace_in_logs",
    name='Replace "WARN" with "WARNING" in app.log',
    tags=("edit", "text", "medium"),
    prompt=(
        "В файле app.log слово 'WARN' встречается несколько раз. Замени все"
        " его вхождения на 'WARNING'. Остальной текст и порядок строк не"
        " меняй."
    ),
    setup_files={
        "app.log": "INFO start\nWARN slow query\nERROR fail\nWARN retry\nINFO done\n",
    },
    gold_files={
        "app.log": "INFO start\nWARNING slow query\nERROR fail\nWARNING retry\nINFO done\n",
    },
    verifier=all_of(
        file_not_contains("app.log", "WARN "),
        file_contains("app.log", "WARNING slow query", "WARNING retry", "ERROR fail"),
    ),
)


# ---------------------------------------------------------------------------
# 68. add_keys_to_dict
# ---------------------------------------------------------------------------
TASK_68 = Task(
    id="task_68_add_two_keys",
    name="Add two keys (port, host) to config.json",
    tags=("edit", "json", "medium"),
    prompt=(
        "В файле config.json (валидный JSON-объект) добавь два новых поля:"
        " host со значением 'localhost' и port со значением 8080 (число)."
        " Существующее поле name со значением 'demo' сохрани. Файл должен"
        " остаться валидным JSON."
    ),
    setup_files={"config.json": '{"name": "demo"}\n'},
    gold_files={
        "config.json": '{"name": "demo", "host": "localhost", "port": 8080}\n',
    },
    verifier=json_file_has(
        "config.json", name="demo", host="localhost", port=8080
    ),
)


# ---------------------------------------------------------------------------
# 69. remove_comments
# ---------------------------------------------------------------------------
TASK_69 = Task(
    id="task_69_remove_comments",
    name="Strip single-line comments from script.py",
    tags=("edit", "python", "medium"),
    prompt=(
        "В файле script.py удали все строки, которые начинаются с символа '#'"
        " (с учётом возможных ведущих пробелов — например, '   # comment' тоже"
        " удалить). Остальные строки и их порядок сохрани без изменений."
    ),
    setup_files={
        "script.py": (
            "# top comment\n"
            "x = 1\n"
            "    # indented comment\n"
            "y = 2\n"
            "# another\n"
            "print(x + y)\n"
        ),
    },
    gold_files={"script.py": "x = 1\ny = 2\nprint(x + y)\n"},
    verifier=file_lines_equal("script.py", ["x = 1", "y = 2", "print(x + y)"]),
)


# ---------------------------------------------------------------------------
# 70. uppercase_first_column
# ---------------------------------------------------------------------------
TASK_70 = Task(
    id="task_70_uppercase_first_column",
    name="Uppercase first column of cities.csv",
    tags=("edit", "csv", "medium"),
    prompt=(
        "В файле cities.csv первый столбец — название города. Переведи в"
        " верхний регистр значения первого столбца во всех строках с данными"
        " (но не в строке-заголовке). Второй столбец и заголовок оставь без"
        " изменений. Файл должен остаться валидным CSV."
    ),
    setup_files={
        "cities.csv": "city,country\nmoscow,russia\nberlin,germany\nparis,france\n",
    },
    gold_files={
        "cities.csv": "city,country\nMOSCOW,russia\nBERLIN,germany\nPARIS,france\n",
    },
    verifier=file_lines_equal(
        "cities.csv",
        [
            "city,country",
            "MOSCOW,russia",
            "BERLIN,germany",
            "PARIS,france",
        ],
    ),
)


# ---------------------------------------------------------------------------
# 71. count_matches_regex
# ---------------------------------------------------------------------------
TASK_71 = Task(
    id="task_71_count_assert",
    name="Count `assert` occurrences across tests/*.py",
    tags=("read", "search", "compute", "medium"),
    prompt=(
        "Посчитай, сколько раз встречается слово 'assert' (как отдельное слово"
        " или как часть строки, без учёта регистра не нужно — точное совпадение"
        " подстроки) во всех .py-файлах внутри каталога tests. Запиши число"
        " одной строкой в файл assert_count.txt."
    ),
    setup_files={
        "tests/test_a.py": "def test_one():\n    assert True\n    assert 1 + 1 == 2\n",
        "tests/test_b.py": "def test_two():\n    x = 1\n    assert x > 0\n",
    },
    gold_files={"assert_count.txt": "3\n"},
    verifier=file_text_equals("assert_count.txt", "3"),
)


# ---------------------------------------------------------------------------
# 72. wrap_in_function
# ---------------------------------------------------------------------------
TASK_72 = Task(
    id="task_72_wrap_in_function",
    name="Wrap top-level code into main()",
    tags=("edit", "python", "refactor", "medium"),
    prompt=(
        "В файле script.py две строки на верхнем уровне: 'x = 5' и 'print(x)'."
        " Оберни их в функцию main() (с отступом в 4 пробела) и в самом конце"
        " файла, после пустой строки, добавь блок\n"
        "    if __name__ == '__main__':\n"
        "        main()\n"
        "Содержимое строк не меняй."
    ),
    setup_files={"script.py": "x = 5\nprint(x)\n"},
    gold_files={
        "script.py": (
            "def main():\n"
            "    x = 5\n"
            "    print(x)\n"
            "\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
    },
    verifier=all_of(
        file_contains("script.py", "def main():", "    x = 5", "    print(x)"),
        file_matches_regex(
            "script.py",
            r"if __name__\s*==\s*['\"]__main__['\"]\s*:\s*\n\s+main\(\)",
        ),
    ),
)


# ---------------------------------------------------------------------------
# 73. extract_emails
# ---------------------------------------------------------------------------
def _verify_task_73(ws: Path) -> VerifyResult:
    p = ws / "emails.txt"
    if not p.exists():
        return VerifyResult(False, "emails.txt missing")
    lines = [line.strip() for line in p.read_text().splitlines() if line.strip()]
    expected = {"alice@example.com", "bob@test.org", "carol@demo.net"}
    if set(lines) != expected:
        return VerifyResult(
            False,
            f"emails.txt contains {sorted(lines)}, expected (any order) {sorted(expected)}",
        )
    return VerifyResult(True, "all three emails captured exactly once")


TASK_73 = Task(
    id="task_73_extract_emails",
    name="Extract email addresses from contacts.txt",
    tags=("read", "search", "medium"),
    prompt=(
        "В файле contacts.txt есть несколько строк со смешанным текстом и"
        " email-адресами. Найди все email-адреса (вида name@domain) и запиши"
        " их в файл emails.txt по одному на строку. Порядок не важен;"
        " дубликаты включать не нужно — каждый адрес ровно один раз."
    ),
    setup_files={
        "contacts.txt": (
            "Alice — alice@example.com, прислала отчёт.\n"
            "Bob (bob@test.org) ответит позже.\n"
            "Контакт повторный: alice@example.com (дублирующая запись).\n"
            "Carol: carol@demo.net.\n"
        ),
    },
    gold_files={
        "emails.txt": "alice@example.com\nbob@test.org\ncarol@demo.net\n",
    },
    verifier=_verify_task_73,
)


# ---------------------------------------------------------------------------
# 74. rename_class
# ---------------------------------------------------------------------------
TASK_74 = Task(
    id="task_74_rename_class",
    name="Rename OldName class to NewName",
    tags=("edit", "python", "refactor", "medium"),
    prompt=(
        "В файле model.py определён класс OldName и есть его использование"
        " (OldName())\n. Переименуй класс в NewName — и в определении ('class"
        " OldName:'), и во всех его упоминаниях. Содержимое тел методов не"
        " меняй."
    ),
    setup_files={
        "model.py": (
            "class OldName:\n"
            "    def hello(self):\n"
            "        return 'hi'\n"
            "\n"
            "\n"
            "x = OldName()\n"
            "print(x.hello())\n"
        ),
    },
    gold_files={
        "model.py": (
            "class NewName:\n"
            "    def hello(self):\n"
            "        return 'hi'\n"
            "\n"
            "\n"
            "x = NewName()\n"
            "print(x.hello())\n"
        ),
    },
    verifier=all_of(
        file_not_contains("model.py", "OldName"),
        file_contains("model.py", "class NewName", "NewName()"),
    ),
)


# ---------------------------------------------------------------------------
# 75. squash_blank_lines
# ---------------------------------------------------------------------------
def _verify_task_75(ws: Path) -> VerifyResult:
    p = ws / "squashed.txt"
    if not p.exists():
        return VerifyResult(False, "squashed.txt missing")
    text = p.read_text()
    if "\n\n\n" in text:
        return VerifyResult(False, "squashed.txt still has 2+ consecutive blank lines")
    expected_content_lines = ["one", "two", "three"]
    content_lines = [line for line in text.splitlines() if line.strip()]
    if content_lines != expected_content_lines:
        return VerifyResult(
            False,
            f"content lines are {content_lines!r}, expected {expected_content_lines!r}",
        )
    return VerifyResult(True, "squashed.txt has at most one blank line between content lines")


TASK_75 = Task(
    id="task_75_squash_blank",
    name="Squash multiple blank lines into one",
    tags=("edit", "text", "medium"),
    prompt=(
        "В файле messy.txt есть подряд идущие пустые строки. Замени каждую"
        " последовательность из двух и более пустых строк на ровно одну пустую"
        " строку. Содержимое непустых строк и их порядок сохрани. Запиши"
        " результат в файл squashed.txt."
    ),
    setup_files={"messy.txt": "one\n\n\n\ntwo\n\n\nthree\n"},
    gold_files={"squashed.txt": "one\n\ntwo\n\nthree\n"},
    verifier=_verify_task_75,
)


# ---------------------------------------------------------------------------
# 76. create_dataclass
# ---------------------------------------------------------------------------
TASK_76 = Task(
    id="task_76_create_dataclass",
    name="Create a Point dataclass",
    tags=("create", "python", "medium"),
    prompt=(
        "Создай файл point.py с импортом 'from dataclasses import dataclass'"
        " и определением dataclass-класса Point с двумя аннотированными полями:"
        " x: float и y: float. Никаких дополнительных методов или импортов не"
        " добавляй."
    ),
    setup_files={},
    gold_files={
        "point.py": (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Point:\n"
            "    x: float\n"
            "    y: float\n"
        ),
    },
    verifier=all_of(
        file_contains(
            "point.py",
            "from dataclasses import dataclass",
            "@dataclass",
            "class Point",
            "x: float",
            "y: float",
        ),
        python_callable_returns("point.py", "mod.Point(1.0, 2.0).x", 1.0),
        python_callable_returns("point.py", "mod.Point(1.0, 2.0).y", 2.0),
    ),
)


# ---------------------------------------------------------------------------
# 77. tally_grades
# ---------------------------------------------------------------------------
def _verify_task_77(ws: Path) -> VerifyResult:
    p = ws / "tally.txt"
    if not p.exists():
        return VerifyResult(False, "tally.txt missing")
    lines = [line for line in p.read_text().splitlines() if line.strip()]
    expected = {"A: 2", "B: 1", "C: 3"}
    if set(lines) != expected:
        return VerifyResult(
            False, f"tally.txt lines {lines!r} differ from expected {sorted(expected)}"
        )
    return VerifyResult(True, "tally.txt has correct counts (order-agnostic)")


TASK_77 = Task(
    id="task_77_tally_grades",
    name="Tally grades from grades.txt",
    tags=("read", "compute", "medium"),
    prompt=(
        "В файле grades.txt лежат оценки — по одной букве на строку, возможны"
        " A, B и C. Посчитай, сколько раз встречается каждая буква, и запиши"
        " результаты в файл tally.txt — по строке на букву, в формате 'A: 2',"
        " 'B: 1', 'C: 3' (число — количество вхождений). Порядок строк может"
        " быть любым."
    ),
    setup_files={"grades.txt": "A\nC\nB\nC\nA\nC\n"},
    gold_files={"tally.txt": "A: 2\nB: 1\nC: 3\n"},
    verifier=_verify_task_77,
)


# ---------------------------------------------------------------------------
# 78. add_property_decorator
# ---------------------------------------------------------------------------
TASK_78 = Task(
    id="task_78_add_property",
    name="Make name a @property in user.py",
    tags=("edit", "python", "medium"),
    prompt=(
        "В файле user.py есть метод get_name(self) у класса User. Преврати"
        " этот метод в property: добавь декоратор @property над методом и"
        " переименуй метод в name (без префикса get_). Тело метода (return"
        " self._name) сохрани."
    ),
    setup_files={
        "user.py": (
            "class User:\n"
            "    def __init__(self, name):\n"
            "        self._name = name\n"
            "\n"
            "    def get_name(self):\n"
            "        return self._name\n"
        ),
    },
    gold_files={
        "user.py": (
            "class User:\n"
            "    def __init__(self, name):\n"
            "        self._name = name\n"
            "\n"
            "    @property\n"
            "    def name(self):\n"
            "        return self._name\n"
        ),
    },
    verifier=all_of(
        file_contains("user.py", "@property", "def name(self):", "return self._name"),
        file_not_contains("user.py", "def get_name"),
        python_callable_returns("user.py", "mod.User('Alice').name", "Alice"),
    ),
)


# ---------------------------------------------------------------------------
# 79. replace_double_quotes
# ---------------------------------------------------------------------------
TASK_79 = Task(
    id="task_79_double_to_single_quotes",
    name="Replace double quotes with single quotes in greetings.py",
    tags=("edit", "python", "medium"),
    prompt=(
        "В файле greetings.py есть две строки-литералы в двойных кавычках:"
        " 'Hello' и 'Hi'. Замени все двойные кавычки в этом файле на одинарные"
        " (и для 'Hello', и для 'Hi'). Сами значения внутри кавычек не трогай."
    ),
    setup_files={
        "greetings.py": (
            'def greet():\n'
            '    return "Hello"\n'
            '\n'
            '\n'
            'def short():\n'
            '    return "Hi"\n'
        ),
    },
    gold_files={
        "greetings.py": (
            "def greet():\n"
            "    return 'Hello'\n"
            "\n"
            "\n"
            "def short():\n"
            "    return 'Hi'\n"
        ),
    },
    verifier=all_of(
        file_not_contains("greetings.py", '"Hello"', '"Hi"'),
        file_contains("greetings.py", "'Hello'", "'Hi'"),
        python_callable_returns("greetings.py", "mod.greet()", "Hello"),
        python_callable_returns("greetings.py", "mod.short()", "Hi"),
    ),
)


# ---------------------------------------------------------------------------
# 80. delete_specific_lines
# ---------------------------------------------------------------------------
TASK_80 = Task(
    id="task_80_delete_lines_with",
    name='Delete lines containing "DEPRECATED"',
    tags=("edit", "text", "search", "medium"),
    prompt=(
        "В файле api.py удали все строки, содержащие подстроку 'DEPRECATED'."
        " Остальные строки сохрани в исходном порядке."
    ),
    setup_files={
        "api.py": (
            "def new_api():\n"
            "    return 1\n"
            "\n"
            "\n"
            "# DEPRECATED: do not use\n"
            "def old_api():\n"
            "    return 0  # DEPRECATED\n"
            "\n"
            "\n"
            "def fresh():\n"
            "    return 2\n"
        ),
    },
    gold_files={
        "api.py": (
            "def new_api():\n"
            "    return 1\n"
            "\n"
            "\n"
            "def old_api():\n"
            "\n"
            "\n"
            "def fresh():\n"
            "    return 2\n"
        ),
    },
    verifier=all_of(
        file_not_contains("api.py", "DEPRECATED"),
        file_contains(
            "api.py", "def new_api", "def fresh", "return 1", "return 2"
        ),
    ),
)


# ---------------------------------------------------------------------------
# 81. swap_two_lines
# ---------------------------------------------------------------------------
TASK_81 = Task(
    id="task_81_swap_lines",
    name="Swap second and third lines of order.txt",
    tags=("edit", "text", "easy"),
    prompt=(
        "В файле order.txt четыре непустые строки: first, second, third,"
        " fourth. Поменяй местами вторую и третью строки — должно стать:"
        " first, third, second, fourth."
    ),
    setup_files={"order.txt": "first\nsecond\nthird\nfourth\n"},
    gold_files={"order.txt": "first\nthird\nsecond\nfourth\n"},
    verifier=file_lines_equal("order.txt", ["first", "third", "second", "fourth"]),
)


# ---------------------------------------------------------------------------
# 82. add_field_to_csv
# ---------------------------------------------------------------------------
TASK_82 = Task(
    id="task_82_add_csv_column",
    name="Add status=active column to users.csv",
    tags=("edit", "csv", "medium"),
    prompt=(
        "В файле users.csv два столбца — name, age. Добавь третий столбец"
        " status: в строке-заголовке — slово 'status', в каждой строке"
        " данных — значение 'active'. Существующие данные не меняй."
    ),
    setup_files={"users.csv": "name,age\nAlice,30\nBob,25\n"},
    gold_files={"users.csv": "name,age,status\nAlice,30,active\nBob,25,active\n"},
    verifier=file_lines_equal(
        "users.csv",
        [
            "name,age,status",
            "Alice,30,active",
            "Bob,25,active",
        ],
    ),
)


# ---------------------------------------------------------------------------
# 83. constant_to_uppercase
# ---------------------------------------------------------------------------
TASK_83 = Task(
    id="task_83_const_uppercase",
    name="Rename lowercase constants to UPPER_CASE",
    tags=("edit", "python", "refactor", "medium"),
    prompt=(
        "В файле constants.py две строки: 'max_retries = 5' и"
        " 'default_timeout = 30'. Переименуй переменные в верхний регистр:"
        " 'MAX_RETRIES = 5' и 'DEFAULT_TIMEOUT = 30' соответственно. Значения"
        " (5 и 30) и порядок строк сохрани."
    ),
    setup_files={"constants.py": "max_retries = 5\ndefault_timeout = 30\n"},
    gold_files={"constants.py": "MAX_RETRIES = 5\nDEFAULT_TIMEOUT = 30\n"},
    verifier=all_of(
        file_not_contains("constants.py", "max_retries", "default_timeout"),
        file_lines_equal(
            "constants.py", ["MAX_RETRIES = 5", "DEFAULT_TIMEOUT = 30"]
        ),
    ),
)


# ---------------------------------------------------------------------------
# 84. concat_two_csvs
# ---------------------------------------------------------------------------
TASK_84 = Task(
    id="task_84_concat_csvs",
    name="Concat users_a.csv and users_b.csv",
    tags=("read", "compute", "csv", "medium"),
    prompt=(
        "В корне рабочей директории есть два CSV-файла с одинаковыми"
        " заголовками: users_a.csv и users_b.csv. Создай файл merged.csv,"
        " в котором: первая строка — заголовок (тот же 'name,age');"
        " дальше идут все строки данных из users_a.csv в их порядке,"
        " потом все строки данных из users_b.csv в их порядке. Дубликаты"
        " не убирай."
    ),
    setup_files={
        "users_a.csv": "name,age\nAlice,30\nBob,25\n",
        "users_b.csv": "name,age\nCarol,40\nDave,22\n",
    },
    gold_files={
        "merged.csv": "name,age\nAlice,30\nBob,25\nCarol,40\nDave,22\n",
    },
    verifier=file_lines_equal(
        "merged.csv",
        [
            "name,age",
            "Alice,30",
            "Bob,25",
            "Carol,40",
            "Dave,22",
        ],
    ),
)


# ---------------------------------------------------------------------------
# 85. add_logging_import
# ---------------------------------------------------------------------------
TASK_85 = Task(
    id="task_85_add_logging_import",
    name="Add logging import at the top of app.py",
    tags=("edit", "python", "easy"),
    prompt=(
        "В файл app.py добавь в самом начале строку 'import logging' (как"
        " первую строку файла). Существующее содержимое (import os и"
        " print('app')) оставь без изменений и в исходном порядке."
    ),
    setup_files={"app.py": "import os\n\nprint('app')\n"},
    gold_files={"app.py": "import logging\nimport os\n\nprint('app')\n"},
    verifier=all_of(
        file_matches_regex("app.py", r"\Aimport logging\nimport os\n"),
        file_contains("app.py", "print('app')"),
    ),
)


# ---------------------------------------------------------------------------
# 86. extract_numbers
# ---------------------------------------------------------------------------
def _verify_task_86(ws: Path) -> VerifyResult:
    p = ws / "numbers.txt"
    if not p.exists():
        return VerifyResult(False, "numbers.txt missing")
    lines = [line for line in p.read_text().splitlines() if line.strip()]
    expected = ["42", "7", "13", "99"]
    if lines != expected:
        return VerifyResult(
            False, f"numbers.txt lines {lines!r} differ from expected {expected!r}"
        )
    return VerifyResult(True, "numbers.txt contains the four numbers in order")


TASK_86 = Task(
    id="task_86_extract_numbers",
    name="Extract numbers from text.txt",
    tags=("read", "search", "medium"),
    prompt=(
        "В файле text.txt есть текст со встроенными числами. Извлеки из него"
        " все целые числа (последовательности цифр) в том порядке, в котором"
        " они встречаются, и запиши их в файл numbers.txt — по одному числу на"
        " строку."
    ),
    setup_files={
        "text.txt": "Заказ 42 уже отправлен. Через 7 дней доставка.\nКод 13 не использовать. Цена: 99 рублей.\n",
    },
    gold_files={"numbers.txt": "42\n7\n13\n99\n"},
    verifier=_verify_task_86,
)


# ---------------------------------------------------------------------------
# 87. flip_boolean
# ---------------------------------------------------------------------------
TASK_87 = Task(
    id="task_87_flip_booleans",
    name="Flip True ↔ False in flags.py",
    tags=("edit", "python", "medium"),
    prompt=(
        "В файле flags.py четыре строки с булевыми значениями:\n"
        "  A = True\n  B = False\n  C = True\n  D = False\n"
        " Поменяй все True на False и все False на True (то есть инвертируй"
        " каждое значение). Имена переменных и их порядок сохрани."
    ),
    setup_files={
        "flags.py": "A = True\nB = False\nC = True\nD = False\n",
    },
    gold_files={
        "flags.py": "A = False\nB = True\nC = False\nD = True\n",
    },
    verifier=file_lines_equal(
        "flags.py", ["A = False", "B = True", "C = False", "D = True"]
    ),
)


# ---------------------------------------------------------------------------
# 88. sort_imports_alpha
# ---------------------------------------------------------------------------
TASK_88 = Task(
    id="task_88_sort_imports",
    name="Sort top-of-file imports alphabetically",
    tags=("edit", "python", "refactor", "medium"),
    prompt=(
        "В файле module.py первые четыре строки — это импорты:\n"
        "  import sys\n  import os\n  import json\n  import re\n"
        " Переставь эти четыре импорта в алфавитном порядке по возрастанию"
        " (json, os, re, sys), оставив их на первых четырёх строках. Остальное"
        " содержимое файла (пустую строку и 'print(json.dumps({}))') сохрани"
        " без изменений и сразу после блока импортов."
    ),
    setup_files={
        "module.py": (
            "import sys\n"
            "import os\n"
            "import json\n"
            "import re\n"
            "\n"
            "print(json.dumps({}))\n"
        ),
    },
    gold_files={
        "module.py": (
            "import json\n"
            "import os\n"
            "import re\n"
            "import sys\n"
            "\n"
            "print(json.dumps({}))\n"
        ),
    },
    verifier=all_of(
        file_matches_regex(
            "module.py",
            r"\Aimport json\nimport os\nimport re\nimport sys\n",
        ),
        file_contains("module.py", "print(json.dumps({}))"),
    ),
)


# ---------------------------------------------------------------------------
# 89. add_pre_commit_hook
# ---------------------------------------------------------------------------
TASK_89 = Task(
    id="task_89_create_pre_commit",
    name="Create .pre-commit-config.yaml",
    tags=("create", "config", "easy"),
    prompt=(
        "Создай в корне рабочей директории файл .pre-commit-config.yaml со"
        " следующими ровно четырьмя непустыми строками (в указанном порядке,"
        " отступы сохраняй):\n"
        "  repos:\n"
        "    - repo: https://github.com/astral-sh/ruff-pre-commit\n"
        "      rev: v0.6.0\n"
        "      hooks:\n"
        " Никаких других строк добавлять не нужно."
    ),
    setup_files={},
    gold_files={
        ".pre-commit-config.yaml": (
            "repos:\n"
            "  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
            "    rev: v0.6.0\n"
            "    hooks:\n"
        ),
    },
    verifier=file_lines_equal(
        ".pre-commit-config.yaml",
        [
            "repos:",
            "  - repo: https://github.com/astral-sh/ruff-pre-commit",
            "    rev: v0.6.0",
            "    hooks:",
        ],
    ),
)


# ---------------------------------------------------------------------------
# 90. duplicate_function
# ---------------------------------------------------------------------------
TASK_90 = Task(
    id="task_90_duplicate_function",
    name="Add a second function `mul` next to `add`",
    tags=("edit", "python", "medium"),
    prompt=(
        "В файле math_ops.py есть функция add(a, b). После неё добавь вторую"
        " функцию mul(a, b) с телом 'return a * b'. Между двумя функциями"
        " должна быть пустая строка-разделитель (две пустые строки между"
        " определениями, как принято в PEP 8 — но достаточно хотя бы одной"
        " пустой строки)."
    ),
    setup_files={"math_ops.py": "def add(a, b):\n    return a + b\n"},
    gold_files={
        "math_ops.py": (
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "\n"
            "def mul(a, b):\n"
            "    return a * b\n"
        ),
    },
    verifier=all_of(
        file_contains("math_ops.py", "def add", "def mul", "return a * b"),
        python_callable_returns("math_ops.py", "mod.add(2, 3)", 5),
        python_callable_returns("math_ops.py", "mod.mul(2, 3)", 6),
    ),
)


# ---------------------------------------------------------------------------
# 91. parse_kv_to_json
# ---------------------------------------------------------------------------
def _verify_task_91(ws: Path) -> VerifyResult:
    p = ws / "config.json"
    if not p.exists():
        return VerifyResult(False, "config.json missing")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"invalid JSON: {exc}")
    if not isinstance(data, dict):
        return VerifyResult(False, "config.json is not a JSON object")
    if data.get("host") != "localhost":
        return VerifyResult(False, f"host: {data.get('host')!r}, expected 'localhost'")
    if data.get("port") not in (8080, "8080"):
        return VerifyResult(False, f"port: {data.get('port')!r}, expected 8080 or '8080'")
    if data.get("debug") not in (True, "true", "True"):
        return VerifyResult(
            False, f"debug: {data.get('debug')!r}, expected True or 'true'"
        )
    return VerifyResult(True, "config.json has the three expected keys")


TASK_91 = Task(
    id="task_91_kv_to_json",
    name="Convert config.ini-style key=value file to JSON",
    tags=("read", "compute", "json", "medium"),
    prompt=(
        "В файле config.kv лежат три строки в формате key=value:\n"
        "  host=localhost\n  port=8080\n  debug=true\n"
        " Сохрани их как объект JSON в файле config.json. Значения 8080 и"
        " true можно сохранить как строки или как соответствующие типы"
        " (число и булево) — оба варианта допустимы."
    ),
    setup_files={"config.kv": "host=localhost\nport=8080\ndebug=true\n"},
    gold_files={
        "config.json": '{"host": "localhost", "port": 8080, "debug": true}\n',
    },
    verifier=_verify_task_91,
)


# ---------------------------------------------------------------------------
# 92. count_chars
# ---------------------------------------------------------------------------
TASK_92 = Task(
    id="task_92_count_chars",
    name="Count letter 'a' in text.txt",
    tags=("read", "compute", "easy"),
    prompt=(
        "Посчитай, сколько раз встречается строчная буква 'a' (английская,"
        " нижний регистр) в файле text.txt. Запиши число одной строкой в файл"
        " count.txt."
    ),
    setup_files={"text.txt": "abracadabra and a banana\n"},
    gold_files={"count.txt": "10\n"},
    verifier=file_text_equals("count.txt", "10"),
)


# ---------------------------------------------------------------------------
# 93. split_csv_lines_into_files
# ---------------------------------------------------------------------------
def _verify_task_93(ws: Path) -> VerifyResult:
    rows_dir = ws / "rows"
    if not rows_dir.is_dir():
        return VerifyResult(False, "rows/ directory missing")
    files = sorted(p.name for p in rows_dir.glob("*.csv"))
    expected = ["row_1.csv", "row_2.csv", "row_3.csv"]
    if files != expected:
        return VerifyResult(False, f"rows/ contains {files!r}, expected {expected!r}")
    expected_contents = {
        "row_1.csv": "Alice,30",
        "row_2.csv": "Bob,25",
        "row_3.csv": "Carol,40",
    }
    for name, content in expected_contents.items():
        actual = (rows_dir / name).read_text().strip()
        if actual != content:
            return VerifyResult(
                False, f"rows/{name} content {actual!r} differs from {content!r}"
            )
    return VerifyResult(True, "each data row of users.csv saved to rows/row_N.csv")


TASK_93 = Task(
    id="task_93_split_rows",
    name="Split users.csv data rows into rows/row_N.csv",
    tags=("read", "edit", "filesystem", "hard"),
    prompt=(
        "В файле users.csv первая строка — заголовок 'name,age', дальше идут"
        " три строки данных. Для каждой строки данных создай отдельный файл в"
        " каталоге rows: первая строка данных — в rows/row_1.csv, вторая —"
        " в rows/row_2.csv, третья — в rows/row_3.csv. Содержимое каждого файла"
        " — это одна строка соответствующих данных (без заголовка), например"
        " 'Alice,30'."
    ),
    setup_files={"users.csv": "name,age\nAlice,30\nBob,25\nCarol,40\n"},
    gold_files={
        "rows/row_1.csv": "Alice,30\n",
        "rows/row_2.csv": "Bob,25\n",
        "rows/row_3.csv": "Carol,40\n",
    },
    verifier=_verify_task_93,
)


# ---------------------------------------------------------------------------
# 94. remove_specific_function
# ---------------------------------------------------------------------------
TASK_94 = Task(
    id="task_94_remove_unused",
    name="Remove `unused_helper` from utils.py",
    tags=("edit", "python", "refactor", "medium"),
    prompt=(
        "В файле utils.py три функции: keep_one, unused_helper, keep_two."
        " Удали определение функции unused_helper целиком (вместе с её телом)."
        " Функции keep_one и keep_two трогать не нужно — оставь их без"
        " изменений."
    ),
    setup_files={
        "utils.py": (
            "def keep_one():\n"
            "    return 1\n"
            "\n"
            "\n"
            "def unused_helper():\n"
            "    return 'dead code'\n"
            "\n"
            "\n"
            "def keep_two():\n"
            "    return 2\n"
        ),
    },
    gold_files={
        "utils.py": (
            "def keep_one():\n"
            "    return 1\n"
            "\n"
            "\n"
            "def keep_two():\n"
            "    return 2\n"
        ),
    },
    verifier=all_of(
        file_not_contains("utils.py", "unused_helper", "dead code"),
        file_contains("utils.py", "def keep_one", "def keep_two", "return 1", "return 2"),
        python_callable_returns("utils.py", "mod.keep_one()", 1),
        python_callable_returns("utils.py", "mod.keep_two()", 2),
    ),
)


# ---------------------------------------------------------------------------
# 95. add_default_argument
# ---------------------------------------------------------------------------
TASK_95 = Task(
    id="task_95_add_default_arg",
    name="Add default value `=10` to parameter `count`",
    tags=("edit", "python", "medium"),
    prompt=(
        "В файле repeater.py есть функция repeat(text, count). Добавь к"
        " параметру count значение по умолчанию: 10. Тело функции"
        " (return text * count) и параметр text не меняй."
    ),
    setup_files={"repeater.py": "def repeat(text, count):\n    return text * count\n"},
    gold_files={
        "repeater.py": "def repeat(text, count=10):\n    return text * count\n",
    },
    verifier=all_of(
        file_matches_regex(
            "repeater.py",
            r"def\s+repeat\s*\(\s*text\s*,\s*count\s*=\s*10\s*\)\s*:",
        ),
        python_callable_returns("repeater.py", "mod.repeat('ab')", "ababababababababababab"[:20]),
        python_callable_returns("repeater.py", "mod.repeat('x', 3)", "xxx"),
    ),
)


# ---------------------------------------------------------------------------
# 96. group_by_first_letter
# ---------------------------------------------------------------------------
def _verify_task_96(ws: Path) -> VerifyResult:
    p = ws / "groups.txt"
    if not p.exists():
        return VerifyResult(False, "groups.txt missing")
    expected = {
        "a: apple,avocado",
        "b: banana,blueberry",
        "c: cherry",
    }
    actual = {line.strip() for line in p.read_text().splitlines() if line.strip()}
    if actual != expected:
        return VerifyResult(
            False,
            f"groups.txt lines {sorted(actual)} differ from expected {sorted(expected)}",
        )
    return VerifyResult(True, "groups.txt groups words by first letter as expected")


TASK_96 = Task(
    id="task_96_group_by_letter",
    name="Group words by first letter",
    tags=("read", "compute", "hard"),
    prompt=(
        "В файле words.txt лежат слова, по одному на строку: apple, avocado,"
        " banana, blueberry, cherry. Сгруппируй слова по первой букве и сохрани"
        " результат в файл groups.txt — по одной строке на букву, в формате"
        " '<буква>: word1,word2' (слова через запятую без пробелов, в исходном"
        " порядке появления). Порядок групп может быть любым."
    ),
    setup_files={"words.txt": "apple\navocado\nbanana\nblueberry\ncherry\n"},
    gold_files={
        "groups.txt": "a: apple,avocado\nb: banana,blueberry\nc: cherry\n",
    },
    verifier=_verify_task_96,
)


# ---------------------------------------------------------------------------
# 97. add_negation_to_filter
# ---------------------------------------------------------------------------
TASK_97 = Task(
    id="task_97_negate_filter",
    name="Negate the `is_even` check in filters.py",
    tags=("edit", "python", "medium"),
    prompt=(
        "В файле filters.py есть функция is_even(x) с телом 'return x % 2 == 0'."
        " Переименуй её в is_odd и обнови тело так, чтобы оно возвращало"
        " True для нечётных чисел: 'return x % 2 != 0'. Никаких других"
        " изменений."
    ),
    setup_files={"filters.py": "def is_even(x):\n    return x % 2 == 0\n"},
    gold_files={"filters.py": "def is_odd(x):\n    return x % 2 != 0\n"},
    verifier=all_of(
        file_not_contains("filters.py", "is_even", "== 0"),
        file_contains("filters.py", "def is_odd", "x % 2 != 0"),
        python_callable_returns("filters.py", "mod.is_odd(3)", True),
        python_callable_returns("filters.py", "mod.is_odd(4)", False),
    ),
)


# ---------------------------------------------------------------------------
# 98. count_unique_words
# ---------------------------------------------------------------------------
TASK_98 = Task(
    id="task_98_count_unique",
    name="Count unique words in text.txt",
    tags=("read", "compute", "medium"),
    prompt=(
        "Посчитай количество уникальных слов (разделители — пробельные"
        " символы) в файле text.txt. Регистр учитывай ('Apple' и 'apple' —"
        " разные слова). Запиши число одной строкой в файл unique_count.txt."
    ),
    setup_files={
        "text.txt": "foo bar foo baz bar quux foo\n",
    },
    gold_files={"unique_count.txt": "4\n"},
    verifier=file_text_equals("unique_count.txt", "4"),
)


# ---------------------------------------------------------------------------
# 99. add_readme
# ---------------------------------------------------------------------------
TASK_99 = Task(
    id="task_99_add_readme",
    name="Create a short README.md",
    tags=("create", "docs", "easy"),
    prompt=(
        "Создай в корне рабочей директории файл README.md. В нём должно быть"
        " ровно две непустые строки в указанном порядке:\n"
        "  # demo\n"
        "  Описание проекта."
    ),
    setup_files={},
    gold_files={"README.md": "# demo\nОписание проекта.\n"},
    verifier=file_lines_equal("README.md", ["# demo", "Описание проекта."]),
)


# ---------------------------------------------------------------------------
# 100. zero_out_balance
# ---------------------------------------------------------------------------
TASK_100 = Task(
    id="task_100_zero_balance",
    name="Set every balance field to 0 in accounts.json",
    tags=("edit", "json", "hard"),
    prompt=(
        "В файле accounts.json лежит массив объектов, у каждого из которых"
        " есть поля 'name' и 'balance' (число). Обнули поле balance во"
        " всех объектах (поставь 0). Поле name и порядок объектов сохрани."
        " Файл должен остаться валидным JSON."
    ),
    setup_files={
        "accounts.json": (
            "[\n"
            '  {"name": "Alice", "balance": 100},\n'
            '  {"name": "Bob", "balance": 250},\n'
            '  {"name": "Carol", "balance": 0}\n'
            "]\n"
        ),
    },
    gold_files={
        "accounts.json": (
            "[\n"
            '  {"name": "Alice", "balance": 0},\n'
            '  {"name": "Bob", "balance": 0},\n'
            '  {"name": "Carol", "balance": 0}\n'
            "]\n"
        ),
    },
    verifier=lambda ws: _verify_task_100(ws),
)


def _verify_task_100(ws: Path) -> VerifyResult:
    p = ws / "accounts.json"
    if not p.exists():
        return VerifyResult(False, "accounts.json missing")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"invalid JSON: {exc}")
    if not isinstance(data, list) or len(data) != 3:
        return VerifyResult(False, f"expected a 3-element array, got {data!r}")
    expected_names = ["Alice", "Bob", "Carol"]
    for entry, expected_name in zip(data, expected_names, strict=False):
        if not isinstance(entry, dict):
            return VerifyResult(False, f"entry is not an object: {entry!r}")
        if entry.get("name") != expected_name:
            return VerifyResult(
                False, f"entry name {entry.get('name')!r} != expected {expected_name!r}"
            )
        if entry.get("balance") != 0:
            return VerifyResult(
                False, f"entry for {expected_name} balance is {entry.get('balance')!r}, expected 0"
            )
    return VerifyResult(True, "accounts.json has all balances zeroed out, names preserved")


MORE_TASKS: list[Task] = [
    TASK_61,
    TASK_62,
    TASK_63,
    TASK_64,
    TASK_65,
    TASK_66,
    TASK_67,
    TASK_68,
    TASK_69,
    TASK_70,
    TASK_71,
    TASK_72,
    TASK_73,
    TASK_74,
    TASK_75,
    TASK_76,
    TASK_77,
    TASK_78,
    TASK_79,
    TASK_80,
    TASK_81,
    TASK_82,
    TASK_83,
    TASK_84,
    TASK_85,
    TASK_86,
    TASK_87,
    TASK_88,
    TASK_89,
    TASK_90,
    TASK_91,
    TASK_92,
    TASK_93,
    TASK_94,
    TASK_95,
    TASK_96,
    TASK_97,
    TASK_98,
    TASK_99,
    TASK_100,
]

# Re-export for completeness.
_ = (file_exists, file_does_not_exist)
