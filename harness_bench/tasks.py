"""The 30 file-operation tasks that make up the benchmark.

Each task is a `Task` instance defining:

- `setup_files`: the workspace state before the agent runs
- `prompt`: the instruction handed to the agent (in Russian, since the
  harness profile targets GigaChat)
- `verifier`: a callable that inspects the workspace and decides pass/fail
- `gold_files`: a "perfect" final workspace state used by `verify-gold`
  to sanity-check that the verifier itself is correct

Tasks intentionally stay small and verifiable: every check is mechanical
(file presence, exact content, regex, JSON parse, or running a small
Python subprocess), no LLM-as-judge.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness_bench.core import Task, VerifyResult
from harness_bench.tasks_adversarial import ADVERSARIAL_TASKS
from harness_bench.tasks_agentic import AGENTIC_TASKS
from harness_bench.tasks_diagnostic import DIAGNOSTIC_TASKS
from harness_bench.tasks_extra import EXTRA_TASKS
from harness_bench.tasks_extreme import EXTREME_TASKS
from harness_bench.tasks_hard import HARD_TASKS
from harness_bench.tasks_memory import MEMORY_TASKS
from harness_bench.tasks_more import MORE_TASKS
from harness_bench.tasks_skills import SKILL_TASKS
from harness_bench.tasks_tbench_lite import TBENCH_LITE_TASKS
from harness_bench.tasks_vcs import VCS_TASKS
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
    python_runs,
)

# ---------------------------------------------------------------------------
# 1. create_hello_py
# ---------------------------------------------------------------------------
TASK_01 = Task(
    id="task_01_create_hello",
    name="Create hello.py that prints Hello, world!",
    tags=("create", "python", "easy"),
    prompt=(
        "Создай в рабочей директории файл hello.py. При запуске он должен"
        " напечатать в стандартный вывод ровно одну строку: Hello, world!"
        " (через функцию print)."
    ),
    setup_files={},
    gold_files={"hello.py": 'print("Hello, world!")\n'},
    verifier=python_runs("hello.py", expected_stdout="Hello, world!"),
)


# ---------------------------------------------------------------------------
# 2. write_data_json
# ---------------------------------------------------------------------------
TASK_02 = Task(
    id="task_02_write_data_json",
    name="Write data.json with given fields",
    tags=("create", "json", "easy"),
    prompt=(
        "Создай файл data.json в текущей директории. В нём должен лежать"
        " валидный JSON-объект с двумя полями: name со строковым значением"
        ' "GigaChat" и version с целочисленным значением 3.'
    ),
    setup_files={},
    gold_files={"data.json": '{"name": "GigaChat", "version": 3}\n'},
    verifier=json_file_has("data.json", name="GigaChat", version=3),
)


# ---------------------------------------------------------------------------
# 3. create_slugify
# ---------------------------------------------------------------------------
_SLUGIFY_GOLD = (
    "import re\n"
    "\n"
    "\n"
    "def slugify(text: str) -> str:\n"
    "    text = text.lower()\n"
    '    text = re.sub(r"\\s+", "-", text)\n'
    '    text = re.sub(r"[^a-z0-9-]", "", text)\n'
    '    return text.strip("-")\n'
)

TASK_03 = Task(
    id="task_03_slugify",
    name="Implement src/utils.py:slugify",
    tags=("create", "python", "medium"),
    prompt=(
        "В каталоге src создай файл utils.py с функцией slugify(text: str) -> str."
        " Поведение функции:\n"
        "- сначала привести строку к нижнему регистру;\n"
        "- любые подряд идущие пробельные символы заменить на одиночный дефис;\n"
        "- удалить все символы, кроме латинских букв a-z, цифр 0-9 и дефиса.\n"
        "Пример: slugify('Hello, World!') должна возвращать 'hello-world'."
    ),
    setup_files={},
    gold_files={"src/utils.py": _SLUGIFY_GOLD},
    verifier=all_of(
        python_callable_returns("src/utils.py", "mod.slugify('Hello, World!')", "hello-world"),
        python_callable_returns("src/utils.py", "mod.slugify('Foo Bar Baz')", "foo-bar-baz"),
    ),
)


# ---------------------------------------------------------------------------
# 4. write_numbers
# ---------------------------------------------------------------------------
TASK_04 = Task(
    id="task_04_write_numbers",
    name="Write numbers.txt with 1..10",
    tags=("create", "text", "easy"),
    prompt=(
        "Создай файл numbers.txt. В нём должно быть десять непустых строк —"
        " целые числа от 1 до 10 по возрастанию, по одному числу в строке."
    ),
    setup_files={},
    gold_files={"numbers.txt": "1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n"},
    verifier=file_lines_equal("numbers.txt", ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]),
)


# ---------------------------------------------------------------------------
# 5. write_greet
# ---------------------------------------------------------------------------
TASK_05 = Task(
    id="task_05_greet",
    name="Implement greeting.py:greet",
    tags=("create", "python", "easy"),
    prompt=(
        "Создай файл greeting.py с функцией greet(name: str) -> str."
        " Она должна возвращать строку формата 'Привет, <name>!'."
        " Например, greet('Аня') возвращает 'Привет, Аня!'."
    ),
    setup_files={},
    gold_files={
        "greeting.py": ('def greet(name: str) -> str:\n    return f"Привет, {name}!"\n'),
    },
    verifier=python_callable_returns("greeting.py", "mod.greet('Аня')", "Привет, Аня!"),
)


# ---------------------------------------------------------------------------
# 6. toggle_debug
# ---------------------------------------------------------------------------
TASK_06 = Task(
    id="task_06_toggle_debug",
    name="Toggle DEBUG in config.py",
    tags=("edit", "python", "easy"),
    prompt=(
        "В файле config.py измени значение переменной DEBUG c True на False."
        " Остальные строки оставь без изменений."
    ),
    setup_files={"config.py": "DEBUG = True\nHOST = 'localhost'\nPORT = 8000\n"},
    gold_files={"config.py": "DEBUG = False\nHOST = 'localhost'\nPORT = 8000\n"},
    verifier=all_of(
        file_contains("config.py", "DEBUG = False", "HOST = 'localhost'", "PORT = 8000"),
        file_not_contains("config.py", "DEBUG = True"),
    ),
)


# ---------------------------------------------------------------------------
# 7. rename_function
# ---------------------------------------------------------------------------
TASK_07 = Task(
    id="task_07_rename_function",
    name="Rename process_data to transform_data",
    tags=("edit", "python", "refactor", "medium"),
    prompt=(
        "В файле app.py переименуй функцию process_data в transform_data —"
        " и в определении функции, и во всех её вызовах. Логику и форматирование"
        " не меняй."
    ),
    setup_files={
        "app.py": "def process_data(x):\n    return x * 2\n\n\nprint(process_data(21))\n",
    },
    gold_files={
        "app.py": "def transform_data(x):\n    return x * 2\n\n\nprint(transform_data(21))\n",
    },
    verifier=all_of(
        file_contains("app.py", "def transform_data", "transform_data(21)"),
        file_not_contains("app.py", "process_data"),
        python_runs("app.py", expected_stdout="42"),
    ),
)


# ---------------------------------------------------------------------------
# 8. bump_version
# ---------------------------------------------------------------------------
TASK_08 = Task(
    id="task_08_bump_version",
    name="Bump VERSION from 1.0.0 to 1.0.1",
    tags=("edit", "python", "easy"),
    prompt=(
        "В файле version.py обнови значение переменной VERSION с '1.0.0' на '1.0.1'."
        " Кавычки и остальное содержимое сохраняй."
    ),
    setup_files={"version.py": 'VERSION = "1.0.0"\n'},
    gold_files={"version.py": 'VERSION = "1.0.1"\n'},
    verifier=file_matches_regex("version.py", r'^VERSION\s*=\s*"1\.0\.1"\s*$'),
)


# ---------------------------------------------------------------------------
# 9. replace_string
# ---------------------------------------------------------------------------
TASK_09 = Task(
    id="task_09_replace_hello",
    name="Replace Hello with Hi in greeting.py",
    tags=("edit", "python", "easy"),
    prompt=(
        "В файле greeting.py поменяй слово 'Hello' на 'Hi' в строке-результате"
        " функции greet. Сигнатуру функции и весь остальной код не трогай."
    ),
    setup_files={"greeting.py": "def greet(name):\n    return f'Hello, {name}!'\n"},
    gold_files={"greeting.py": "def greet(name):\n    return f'Hi, {name}!'\n"},
    verifier=python_callable_returns("greeting.py", "mod.greet('Bob')", "Hi, Bob!"),
)


# ---------------------------------------------------------------------------
# 10. bump_pyproject_version
# ---------------------------------------------------------------------------
_PYPROJECT_INITIAL = '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
_PYPROJECT_GOLD = _PYPROJECT_INITIAL.replace("0.1.0", "0.2.0")

TASK_10 = Task(
    id="task_10_bump_pyproject",
    name="Bump pyproject.toml version",
    tags=("edit", "config", "easy"),
    prompt=(
        "В файле pyproject.toml в секции [project] обнови поле version"
        " с 0.1.0 на 0.2.0. Остальные поля и заголовок секции сохрани без изменений."
    ),
    setup_files={"pyproject.toml": _PYPROJECT_INITIAL},
    gold_files={"pyproject.toml": _PYPROJECT_GOLD},
    verifier=all_of(
        file_matches_regex("pyproject.toml", r'^version\s*=\s*"0\.2\.0"\s*$'),
        file_contains("pyproject.toml", 'name = "demo"', 'requires-python = ">=3.12"'),
        file_not_contains("pyproject.toml", '"0.1.0"'),
    ),
)


# ---------------------------------------------------------------------------
# 11. count_py_files
# ---------------------------------------------------------------------------
TASK_11 = Task(
    id="task_11_count_py",
    name="Count .py files into count.txt",
    tags=("read", "search", "medium"),
    prompt=(
        "Посчитай, сколько в текущей рабочей директории файлов с расширением"
        " .py (включая вложенные подкаталоги). Запиши получившееся число одной"
        " строкой в файл count.txt — без лишних пробелов, текста или префиксов."
    ),
    setup_files={
        "main.py": "",
        "a/foo.py": "",
        "b/bar.py": "",
        "c/baz.txt": "",
        "nested/inner/deep.py": "",
    },
    gold_files={"count.txt": "4\n"},
    verifier=file_text_equals("count.txt", "4"),
)


# ---------------------------------------------------------------------------
# 12. extract_todos
# ---------------------------------------------------------------------------
TASK_12 = Task(
    id="task_12_extract_todos",
    name="Collect TODO lines into todos.txt",
    tags=("read", "search", "medium"),
    prompt=(
        "Найди во всех .py файлах текущей директории (и подкаталогов) строки,"
        " в которых встречается слово TODO. Сохрани эти строки (содержимое самих"
        " строк, без указания имён файлов) в файл todos.txt — каждую с новой"
        " строки. Порядок строк не имеет значения."
    ),
    setup_files={
        "a.py": "x = 1\n# TODO: do stuff\nprint('hi')\n",
        "b.py": "# TODO: refactor this\ny = 2\n",
        "c.py": "no todos here\n",
    },
    gold_files={"todos.txt": "# TODO: do stuff\n# TODO: refactor this\n"},
    verifier=all_of(
        file_contains("todos.txt", "# TODO: do stuff", "# TODO: refactor this"),
        file_not_contains("todos.txt", "no todos", "x = 1", "y = 2"),
    ),
)


# ---------------------------------------------------------------------------
# 13. count_csv_lines
# ---------------------------------------------------------------------------
TASK_13 = Task(
    id="task_13_count_csv_lines",
    name="Count lines in data.csv",
    tags=("read", "easy"),
    prompt=(
        "В файле data.csv несколько строк (включая строку-заголовок). Посчитай"
        " общее количество строк в файле и запиши получившееся число одной"
        " строкой в файл lines.txt — только число, без лишнего текста."
    ),
    setup_files={
        "data.csv": "name,age\nAlice,30\nBob,25\nCharlie,40\nDana,22\n",
    },
    gold_files={"lines.txt": "5\n"},
    verifier=file_text_equals("lines.txt", "5"),
)


# ---------------------------------------------------------------------------
# 14. sum_numbers
# ---------------------------------------------------------------------------
TASK_14 = Task(
    id="task_14_sum_numbers",
    name="Sum numbers from numbers.txt",
    tags=("read", "compute", "medium"),
    prompt=(
        "В файле numbers.txt лежат целые числа — по одному на строку. Посчитай"
        " их сумму и запиши результат одной строкой в файл sum.txt (только"
        " число)."
    ),
    setup_files={"numbers.txt": "10\n20\n30\n40\n"},
    gold_files={"sum.txt": "100\n"},
    verifier=file_text_equals("sum.txt", "100"),
)


# ---------------------------------------------------------------------------
# 15. extract_first_word
# ---------------------------------------------------------------------------
TASK_15 = Task(
    id="task_15_first_word",
    name="Extract first word of text.txt",
    tags=("read", "easy"),
    prompt=(
        "В файле text.txt одна строка с текстом. Запиши его первое слово"
        " (всё, что идёт до первого пробельного символа) одной строкой в файл"
        " first_word.txt — без точек, запятых или других знаков препинания"
        " в конце."
    ),
    setup_files={"text.txt": "Привет всем, как дела?\n"},
    gold_files={"first_word.txt": "Привет\n"},
    verifier=file_text_equals("first_word.txt", "Привет"),
)


# ---------------------------------------------------------------------------
# 16. add_type_hints
# ---------------------------------------------------------------------------
TASK_16 = Task(
    id="task_16_add_type_hints",
    name="Add int type hints to add()",
    tags=("edit", "python", "easy"),
    prompt=(
        "В файле math_utils.py добавь аннотации типов к функции add: оба"
        " аргумента — int, возвращаемое значение — int. Тело функции (return a + b)"
        " не меняй."
    ),
    setup_files={"math_utils.py": "def add(a, b):\n    return a + b\n"},
    gold_files={"math_utils.py": "def add(a: int, b: int) -> int:\n    return a + b\n"},
    verifier=all_of(
        file_matches_regex(
            "math_utils.py",
            r"def\s+add\s*\(\s*a\s*:\s*int\s*,\s*b\s*:\s*int\s*\)\s*->\s*int\s*:",
        ),
        file_contains("math_utils.py", "return a + b"),
    ),
)


# ---------------------------------------------------------------------------
# 17. add_future_imports
# ---------------------------------------------------------------------------
TASK_17 = Task(
    id="task_17_add_future_imports",
    name="Add `from __future__ import annotations`",
    tags=("edit", "python", "easy"),
    prompt=(
        "Добавь в самое начало файла module.py строку 'from __future__ import"
        " annotations', а сразу под ней — одну пустую строку. Существующее"
        " содержимое файла (x = 1 и print(x)) сохрани без изменений."
    ),
    setup_files={"module.py": "x = 1\nprint(x)\n"},
    gold_files={"module.py": "from __future__ import annotations\n\nx = 1\nprint(x)\n"},
    verifier=all_of(
        file_matches_regex("module.py", r"\Afrom __future__ import annotations\b"),
        file_contains("module.py", "x = 1", "print(x)"),
    ),
)


# ---------------------------------------------------------------------------
# 18. add_docstring
# ---------------------------------------------------------------------------
TASK_18 = Task(
    id="task_18_add_docstring",
    name="Add docstring to calculate()",
    tags=("edit", "python", "easy"),
    prompt=(
        "В функцию calculate из файла calc.py добавь docstring первой строкой"
        " её тела. Текст docstring — 'Возвращает удвоенное значение x.', в тройных"
        " двойных кавычках. Остальной код не меняй."
    ),
    setup_files={"calc.py": "def calculate(x):\n    return x * 2\n"},
    gold_files={
        "calc.py": (
            'def calculate(x):\n    """Возвращает удвоенное значение x."""\n    return x * 2\n'
        ),
    },
    verifier=all_of(
        file_contains(
            "calc.py",
            '"""Возвращает удвоенное значение x."""',
            "return x * 2",
        ),
        python_callable_returns("calc.py", "mod.calculate(21)", 42),
    ),
)


# ---------------------------------------------------------------------------
# 19. remove_old_func
# ---------------------------------------------------------------------------
_MOD_INITIAL = (
    "def old_func():\n"
    "    return 'old'\n"
    "\n"
    "\n"
    "def new_func():\n"
    "    return 'new'\n"
    "\n"
    "\n"
    "def helper():\n"
    "    return 42\n"
)
_MOD_GOLD = "def new_func():\n    return 'new'\n\n\ndef helper():\n    return 42\n"

TASK_19 = Task(
    id="task_19_remove_old_func",
    name="Remove old_func from mod.py",
    tags=("edit", "python", "refactor", "medium"),
    prompt=(
        "Удали из файла mod.py определение функции old_func целиком (вместе с"
        " телом). Функции new_func и helper трогать не нужно — они должны"
        " остаться без изменений."
    ),
    setup_files={"mod.py": _MOD_INITIAL},
    gold_files={"mod.py": _MOD_GOLD},
    verifier=all_of(
        file_not_contains("mod.py", "old_func", "'old'"),
        file_contains("mod.py", "def new_func", "def helper", "return 42"),
        python_callable_returns("mod.py", "mod.new_func()", "new"),
    ),
)


# ---------------------------------------------------------------------------
# 20. move_function
# ---------------------------------------------------------------------------
TASK_20 = Task(
    id="task_20_move_function",
    name="Move helper() from a.py to b.py",
    tags=("edit", "python", "refactor", "hard"),
    prompt=(
        "Перенеси функцию helper() из файла a.py в файл b.py. После переноса:\n"
        "- в a.py должна остаться только строка TOKEN = 'X';\n"
        "- в b.py должна остаться существующая строка VALUE = 1, а ниже неё —"
        " определение функции helper().\n"
        "Тело функции (return 'help') менять не нужно."
    ),
    setup_files={
        "a.py": "TOKEN = 'X'\n\n\ndef helper():\n    return 'help'\n",
        "b.py": "VALUE = 1\n",
    },
    gold_files={
        "a.py": "TOKEN = 'X'\n",
        "b.py": "VALUE = 1\n\n\ndef helper():\n    return 'help'\n",
    },
    verifier=all_of(
        file_not_contains("a.py", "def helper", "'help'"),
        file_contains("a.py", "TOKEN = 'X'"),
        file_contains("b.py", "VALUE = 1", "def helper"),
        python_callable_returns("b.py", "mod.helper()", "help"),
    ),
)


# ---------------------------------------------------------------------------
# 21. rename_file
# ---------------------------------------------------------------------------
TASK_21 = Task(
    id="task_21_rename_file",
    name="Rename oldname.txt to newname.txt",
    tags=("edit", "filesystem", "easy"),
    prompt=(
        "Переименуй файл oldname.txt в newname.txt, сохранив его содержимое"
        " байт-в-байт. После переименования файла oldname.txt существовать не"
        " должно."
    ),
    setup_files={"oldname.txt": "important content\n"},
    gold_files={"oldname.txt": None, "newname.txt": "important content\n"},
    verifier=all_of(
        file_does_not_exist("oldname.txt"),
        file_contains("newname.txt", "important content"),
    ),
)


# ---------------------------------------------------------------------------
# 22. delete_file
# ---------------------------------------------------------------------------
TASK_22 = Task(
    id="task_22_delete_file",
    name="Delete obsolete.py",
    tags=("edit", "filesystem", "easy"),
    prompt=("Удали файл obsolete.py из текущей директории. Файл keep.py трогать не нужно."),
    setup_files={
        "obsolete.py": "# this file is obsolete\n",
        "keep.py": "# keep me\n",
    },
    gold_files={"obsolete.py": None, "keep.py": "# keep me\n"},
    verifier=all_of(
        file_does_not_exist("obsolete.py"),
        file_exists("keep.py"),
        file_contains("keep.py", "keep me"),
    ),
)


# ---------------------------------------------------------------------------
# 23. append_log
# ---------------------------------------------------------------------------
TASK_23 = Task(
    id="task_23_append_log",
    name="Append a line to log.txt",
    tags=("edit", "text", "easy"),
    prompt=(
        "В конец файла log.txt добавь одну новую строку: '2026-05-12: deployed'."
        " Все существующие строки сохрани в исходном порядке."
    ),
    setup_files={"log.txt": "2026-05-10: started\n2026-05-11: tested\n"},
    gold_files={"log.txt": "2026-05-10: started\n2026-05-11: tested\n2026-05-12: deployed\n"},
    verifier=file_lines_equal(
        "log.txt",
        [
            "2026-05-10: started",
            "2026-05-11: tested",
            "2026-05-12: deployed",
        ],
    ),
)


# ---------------------------------------------------------------------------
# 24. add_header_comment
# ---------------------------------------------------------------------------
TASK_24 = Task(
    id="task_24_add_header_comment",
    name="Prepend copyright header to src/*.py",
    tags=("edit", "python", "medium"),
    prompt=(
        "В каждый .py файл в каталоге src добавь первой строкой комментарий"
        " '# (c) 2026 Acme Inc.' и сразу после него — одну пустую строку. Существующее"
        " содержимое файлов сохрани без изменений."
    ),
    setup_files={
        "src/a.py": "import os\nprint(1)\n",
        "src/b.py": "x = 2\n",
    },
    gold_files={
        "src/a.py": "# (c) 2026 Acme Inc.\n\nimport os\nprint(1)\n",
        "src/b.py": "# (c) 2026 Acme Inc.\n\nx = 2\n",
    },
    verifier=all_of(
        file_matches_regex("src/a.py", r"\A# \(c\) 2026 Acme Inc\."),
        file_matches_regex("src/b.py", r"\A# \(c\) 2026 Acme Inc\."),
        file_contains("src/a.py", "import os", "print(1)"),
        file_contains("src/b.py", "x = 2"),
    ),
)


# ---------------------------------------------------------------------------
# 25. sort_lines
# ---------------------------------------------------------------------------
TASK_25 = Task(
    id="task_25_sort_lines",
    name="Sort lines from unsorted.txt to sorted.txt",
    tags=("read", "compute", "easy"),
    prompt=(
        "Прочитай файл unsorted.txt, отсортируй его строки по возрастанию"
        " (лексикографически) и запиши результат в файл sorted.txt — одна"
        " строка на каждое значение, без пустых строк между ними."
    ),
    setup_files={"unsorted.txt": "banana\napple\ncherry\ndate\n"},
    gold_files={"sorted.txt": "apple\nbanana\ncherry\ndate\n"},
    verifier=file_lines_equal("sorted.txt", ["apple", "banana", "cherry", "date"]),
)


# ---------------------------------------------------------------------------
# 26. add_json_key
# ---------------------------------------------------------------------------
TASK_26 = Task(
    id="task_26_add_json_key",
    name="Add port=8080 to config.json",
    tags=("edit", "json", "easy"),
    prompt=(
        "В файле config.json добавь ключ port со значением 8080 (число)."
        " Существующие ключи host и debug сохраняй с их прежними значениями."
        " Файл должен остаться валидным JSON."
    ),
    setup_files={"config.json": '{"host": "localhost", "debug": false}\n'},
    gold_files={"config.json": '{"host": "localhost", "debug": false, "port": 8080}\n'},
    verifier=json_file_has("config.json", host="localhost", debug=False, port=8080),
)


# ---------------------------------------------------------------------------
# 27. update_dep_version
# ---------------------------------------------------------------------------
_PACKAGE_INITIAL = (
    "{\n"
    '  "name": "demo",\n'
    '  "dependencies": {\n'
    '    "requests": "2.20.0",\n'
    '    "flask": "2.0.0"\n'
    "  }\n"
    "}\n"
)
_PACKAGE_GOLD = _PACKAGE_INITIAL.replace("2.20.0", "2.31.0")


def _verify_task_27(ws: Path) -> VerifyResult:
    p = ws / "package.json"
    if not p.exists():
        return VerifyResult(False, "package.json missing")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"package.json invalid JSON: {exc}")
    deps = data.get("dependencies", {})
    if not isinstance(deps, dict):
        return VerifyResult(False, f"dependencies is not an object: {deps!r}")
    if deps.get("requests") != "2.31.0":
        return VerifyResult(
            False, f"requests version is {deps.get('requests')!r}, expected '2.31.0'"
        )
    if deps.get("flask") != "2.0.0":
        return VerifyResult(False, f"flask version changed unexpectedly: {deps.get('flask')!r}")
    if data.get("name") != "demo":
        return VerifyResult(False, f"name field changed: {data.get('name')!r}")
    return VerifyResult(True, "package.json updated as expected")


TASK_27 = Task(
    id="task_27_update_dep_version",
    name="Bump requests version in package.json",
    tags=("edit", "json", "medium"),
    prompt=(
        "В файле package.json в секции dependencies обнови версию пакета"
        " requests с '2.20.0' на '2.31.0'. Поле name и версия flask должны остаться"
        " прежними. Файл должен остаться валидным JSON."
    ),
    setup_files={"package.json": _PACKAGE_INITIAL},
    gold_files={"package.json": _PACKAGE_GOLD},
    verifier=_verify_task_27,
)


# ---------------------------------------------------------------------------
# 28. csv_to_json
# ---------------------------------------------------------------------------
def _verify_task_28(ws: Path) -> VerifyResult:
    p = ws / "users.json"
    if not p.exists():
        return VerifyResult(False, "users.json missing")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"users.json invalid JSON: {exc}")
    if not isinstance(data, list) or len(data) != 2:
        return VerifyResult(False, f"expected a list of 2 objects, got {data!r}")
    expected = [("Alice", 30), ("Bob", 25)]
    for row, (name, age) in zip(data, expected, strict=False):
        if not isinstance(row, dict):
            return VerifyResult(False, f"entry is not an object: {row!r}")
        if row.get("name") != name:
            return VerifyResult(False, f"name mismatch: got {row.get('name')!r}, expected {name!r}")
        actual_age = row.get("age")
        # Accept either string or int representation of age.
        if actual_age not in (age, str(age)):
            return VerifyResult(
                False, f"age for {name} is {actual_age!r}, expected {age} or '{age}'"
            )
    return VerifyResult(True, "users.json contains the expected records")


TASK_28 = Task(
    id="task_28_csv_to_json",
    name="Convert users.csv to users.json",
    tags=("read", "compute", "json", "medium"),
    prompt=(
        "Прочитай файл users.csv (первая строка — заголовки name,age; дальше"
        " две строки данных) и сохрани его содержимое в файл users.json как"
        " JSON-массив объектов. Каждый объект должен содержать ключи name и age."
        " Значения age можно записать как строки или как числа — допустимы оба"
        " варианта."
    ),
    setup_files={"users.csv": "name,age\nAlice,30\nBob,25\n"},
    gold_files={"users.json": '[{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}]\n'},
    verifier=_verify_task_28,
)


# ---------------------------------------------------------------------------
# 29. create_gitignore
# ---------------------------------------------------------------------------
TASK_29 = Task(
    id="task_29_create_gitignore",
    name="Create .gitignore with given entries",
    tags=("create", "config", "easy"),
    prompt=(
        "Создай файл .gitignore в текущей директории. В нём должны быть"
        " ровно четыре непустые строки, в указанном порядке:\n"
        "1) __pycache__/\n"
        "2) *.pyc\n"
        "3) .venv/\n"
        "4) .env"
    ),
    setup_files={},
    gold_files={".gitignore": "__pycache__/\n*.pyc\n.venv/\n.env\n"},
    verifier=file_lines_equal(".gitignore", ["__pycache__/", "*.pyc", ".venv/", ".env"]),
)


# ---------------------------------------------------------------------------
# 30. add_todo_entry
# ---------------------------------------------------------------------------
TASK_30 = Task(
    id="task_30_add_todo",
    name="Append a new entry to tasks.txt",
    tags=("edit", "text", "easy"),
    prompt=(
        "В конец файла tasks.txt добавь одну новую строку: '4. Купить молоко'."
        " Существующие три пункта сохрани без изменений."
    ),
    setup_files={
        "tasks.txt": "1. Сделать зарядку\n2. Позвонить маме\n3. Сходить в магазин\n",
    },
    gold_files={
        "tasks.txt": (
            "1. Сделать зарядку\n2. Позвонить маме\n3. Сходить в магазин\n4. Купить молоко\n"
        ),
    },
    verifier=file_lines_equal(
        "tasks.txt",
        [
            "1. Сделать зарядку",
            "2. Позвонить маме",
            "3. Сходить в магазин",
            "4. Купить молоко",
        ],
    ),
)


ALL_TASKS: list[Task] = [
    TASK_01,
    TASK_02,
    TASK_03,
    TASK_04,
    TASK_05,
    TASK_06,
    TASK_07,
    TASK_08,
    TASK_09,
    TASK_10,
    TASK_11,
    TASK_12,
    TASK_13,
    TASK_14,
    TASK_15,
    TASK_16,
    TASK_17,
    TASK_18,
    TASK_19,
    TASK_20,
    TASK_21,
    TASK_22,
    TASK_23,
    TASK_24,
    TASK_25,
    TASK_26,
    TASK_27,
    TASK_28,
    TASK_29,
    TASK_30,
    *EXTRA_TASKS,
    *MORE_TASKS,
    *HARD_TASKS,
    *EXTREME_TASKS,
    *DIAGNOSTIC_TASKS,
    *MEMORY_TASKS,
    *AGENTIC_TASKS,
    *VCS_TASKS,
    *SKILL_TASKS,
    *ADVERSARIAL_TASKS,
    *TBENCH_LITE_TASKS,
]

_TASK_INDEX: dict[str, Task] = {t.id: t for t in ALL_TASKS}


def get_task(task_id: str) -> Task:
    """Look up a task by id. Raises `KeyError` when the id is unknown."""
    try:
        return _TASK_INDEX[task_id]
    except KeyError as exc:
        raise KeyError(
            f"Unknown task id: {task_id!r}. Run `python -m harness_bench list`."
        ) from exc
