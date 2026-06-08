"""Version-control tasks: merge-conflict resolution, patch apply, semantic merge.

These 15 tasks (299-313) probe a skill the rest of the suite does not cover:
working with the *artifacts* of version control, and they escalate from
single-file edits to genuinely multi-file / multi-step workflows.

The 309-313 block (revision 0.9.0) is tuned to find the ceiling of mid-tier
agents: each task spreads work across 8-12 files or a 5-step pipeline, so an
agent that cannot keep a long edit plan coherent runs out of its step budget
or leaves a marker behind, while a frontier agent completes them.

Difficulty gradient (revision 0.8.0 is deliberately discriminating):

- Anchors (299-302): pick the right side (ours / theirs / both), apply a
  multi-hunk unified diff. Strong models and GigaChat clear these.
- Hard tier (303-307): multi-file and multi-step workflows where a single
  local heuristic is *wrong* and an error in any step fails the whole task:
    * 303 — cascading cross-file resolution: resolving schema.py decides which
      side is correct in three dependent files, with the correct side
      scattered across branches (so "always HEAD" fails).
    * 304 — apply an ordered chain of three unified diffs (p1 -> p2 -> p3),
      each of which only applies after the previous one.
    * 305 — resolve a name conflict, then propagate the rename across three
      files (one with its own conflict) so a hidden pytest suite passes.
    * 306 — compute a semantic deep-merge of two JSON configs (sum overlapping
      numbers, union+dedup+sort lists, merge nested dicts) — picking one side
      is wrong.
    * 307 — twelve module files each carry a conflict; keep the larger weight
      in every one so an aggregate pytest over all twelve passes.
- 308 — detect genuinely-conflicted files across nested dirs while ignoring
  false-positive traps (lone separators, half markers, shell redirects).

Verifiers are strict: behaviour (`python_callable_returns` / `pytest_passes`)
plus exact non-empty lines plus a ban on leftover markers and Markdown code
fences (a known GigaChat stray-character failure mode). Every task remains
fully correct — the gold solution is the unique deterministic result and
`verify-gold` passes 10/10.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness_bench.core import Task, VerifyResult
from harness_bench.verifiers import (
    all_of,
    file_contains,
    file_does_not_exist,
    file_lines_equal,
    file_not_contains,
    json_file_matches,
    pytest_passes,
    python_callable_returns,
)

# Every kind of Git conflict marker, including the diff3 common-ancestor line.
_MARKERS = ("<<<<<<<", "=======", ">>>>>>>", "|||||||")
# Markdown code fence — a common stray-character artefact when models wrap
# edited file bodies. No legitimate solution here contains one.
_FENCE = "```"


# ---------------------------------------------------------------------------
# 299. resolve_conflict_take_both  (anchor)
# ---------------------------------------------------------------------------
TASK_299 = Task(
    id="task_299_resolve_conflict_take_both",
    name="Resolve merge conflict keeping both functions",
    tags=("merge", "conflict", "python", "medium"),
    prompt=(
        "В файле utils.py остался незавершённый конфликт слияния. Обе ветки"
        " добавили нужные функции — add и sub. Разреши конфликт так, чтобы в"
        " файле остались ОБЕ функции (сначала add, затем sub), а все маркеры"
        " конфликта (<<<<<<<, =======, >>>>>>>) были удалены. Функцию greet"
        " не трогай. Не оборачивай содержимое файла в дополнительные символы."
    ),
    setup_files={
        "utils.py": (
            "def greet(name):\n"
            '    return f"Hello, {name}!"\n'
            "\n"
            "\n"
            "<<<<<<< HEAD\n"
            "def add(a, b):\n"
            "    return a + b\n"
            "=======\n"
            "def sub(a, b):\n"
            "    return a - b\n"
            ">>>>>>> feature\n"
        ),
    },
    gold_files={
        "utils.py": (
            "def greet(name):\n"
            '    return f"Hello, {name}!"\n'
            "\n"
            "\n"
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "\n"
            "def sub(a, b):\n"
            "    return a - b\n"
        ),
    },
    verifier=all_of(
        file_not_contains("utils.py", *_MARKERS, _FENCE),
        python_callable_returns("utils.py", "mod.add(2, 3)", 5),
        python_callable_returns("utils.py", "mod.sub(5, 2)", 3),
        file_contains("utils.py", "def greet"),
    ),
)


# ---------------------------------------------------------------------------
# 300. resolve_conflict_take_ours  (anchor: strict exact content)
# ---------------------------------------------------------------------------
TASK_300 = Task(
    id="task_300_resolve_conflict_take_ours",
    name="Resolve conflict taking our (HEAD) version, exact content",
    tags=("merge", "conflict", "config", "medium"),
    prompt=(
        "В файле config.py конфликт по переменной VERSION. Прими значение из"
        " нашей ветки (HEAD): \"2.4.0\". Удали маркеры конфликта и чужой"
        " вариант. После правки файл должен содержать РОВНО три строки, без"
        " лишних символов:\n"
        "DEBUG = False\n"
        'VERSION = "2.4.0"\n'
        "TIMEOUT = 30"
    ),
    setup_files={
        "config.py": (
            "DEBUG = False\n"
            "<<<<<<< HEAD\n"
            'VERSION = "2.4.0"\n'
            "=======\n"
            'VERSION = "2.3.1"\n'
            ">>>>>>> hotfix\n"
            "TIMEOUT = 30\n"
        ),
    },
    gold_files={
        "config.py": ('DEBUG = False\nVERSION = "2.4.0"\nTIMEOUT = 30\n'),
    },
    verifier=all_of(
        file_not_contains("config.py", *_MARKERS, _FENCE, "2.3.1"),
        file_lines_equal(
            "config.py", ["DEBUG = False", 'VERSION = "2.4.0"', "TIMEOUT = 30"]
        ),
    ),
)


# ---------------------------------------------------------------------------
# 301. resolve_conflict_take_theirs  (anchor: exact JSON)
# ---------------------------------------------------------------------------
TASK_301 = Task(
    id="task_301_resolve_conflict_take_theirs",
    name="Resolve conflict taking incoming version, valid JSON",
    tags=("merge", "conflict", "json", "medium"),
    prompt=(
        "В файле settings.json конфликт слияния. Прими версию из входящей"
        " ветки security-update: lib_version = \"1.4.7\". Удали все маркеры"
        " конфликта. Файл обязан остаться валидным JSON-объектом с полями"
        " name=\"service\", lib_version=\"1.4.7\", debug=false (и только ими)."
        " Не добавляй markdown-обрамление и лишние символы."
    ),
    setup_files={
        "settings.json": (
            "{\n"
            '  "name": "service",\n'
            "<<<<<<< HEAD\n"
            '  "lib_version": "1.2.0",\n'
            "=======\n"
            '  "lib_version": "1.4.7",\n'
            ">>>>>>> security-update\n"
            '  "debug": false\n'
            "}\n"
        ),
    },
    gold_files={
        "settings.json": (
            "{\n"
            '  "name": "service",\n'
            '  "lib_version": "1.4.7",\n'
            '  "debug": false\n'
            "}\n"
        ),
    },
    verifier=all_of(
        file_not_contains("settings.json", *_MARKERS, _FENCE),
        json_file_matches(
            "settings.json",
            {"name": "service", "lib_version": "1.4.7", "debug": False},
        ),
    ),
)


# ---------------------------------------------------------------------------
# 302. apply_unified_diff  (anchor: multi-hunk patch, behaviour-verified)
# ---------------------------------------------------------------------------
TASK_302 = Task(
    id="task_302_apply_unified_diff",
    name="Apply a multi-hunk unified diff to a source file",
    tags=("patch", "apply", "python", "medium"),
    prompt=(
        "В файле change.patch лежит unified diff для calc.py (два изменения)."
        " Применить его к calc.py (git apply / patch -p1 или вручную). После"
        " применения sub должна вычитать, а mul — умножать; add остаётся"
        " сложением. Файл change.patch можно оставить."
    ),
    setup_files={
        "calc.py": (
            "def add(a, b):\n    return a + b\n\n\n"
            "def sub(a, b):\n    return a + b\n\n\n"
            "def mul(a, b):\n    return a + b\n"
        ),
        "change.patch": (
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -4,7 +4,7 @@\n"
            " \n"
            " def sub(a, b):\n"
            "-    return a + b\n"
            "+    return a - b\n"
            " \n"
            " \n"
            " def mul(a, b):\n"
            "-    return a + b\n"
            "+    return a * b\n"
        ),
    },
    gold_files={
        "calc.py": (
            "def add(a, b):\n    return a + b\n\n\n"
            "def sub(a, b):\n    return a - b\n\n\n"
            "def mul(a, b):\n    return a * b\n"
        ),
    },
    verifier=all_of(
        file_not_contains("calc.py", _FENCE, "<<<<<<<"),
        python_callable_returns("calc.py", "mod.add(2, 3)", 5),
        python_callable_returns("calc.py", "mod.sub(9, 4)", 5),
        python_callable_returns("calc.py", "mod.mul(4, 5)", 20),
    ),
)


# ---------------------------------------------------------------------------
# 303. cascading_cross_file  (HARD: 4 files, resolution of one drives others)
# ---------------------------------------------------------------------------
TASK_303 = Task(
    id="task_303_cascading_cross_file",
    name="Cascading cross-file resolution: schema decides the rest",
    tags=("merge", "conflict", "multifile", "python", "hard"),
    prompt=(
        "Четыре файла с конфликтами связаны между собой. Сначала разреши"
        " конфликт в schema.py, приняв вариант нашей ветки (HEAD): тогда"
        " API_VERSION станет равен 2. Затем в файлах client.py, server.py и"
        " worker.py оставь в КАЖДОМ тот вариант, который соответствует выбранной"
        " версии API (то есть со строкой про v2), независимо от того, в какой"
        " ветке (HEAD или legacy) он находится. Внимание: правильная сторона"
        " разбросана по разным веткам — слепо брать HEAD везде нельзя. Удали"
        " все маркеры конфликта во всех четырёх файлах, лишних символов не"
        " добавляй."
    ),
    setup_files={
        "schema.py": (
            "<<<<<<< HEAD\n"
            "API_VERSION = 2\n"
            "=======\n"
            "API_VERSION = 1\n"
            ">>>>>>> legacy\n"
        ),
        # v2 side is on HEAD here.
        "client.py": (
            "def endpoint():\n"
            "<<<<<<< HEAD\n"
            '    return "/v2/items"\n'
            "=======\n"
            '    return "/v1/items"\n'
            ">>>>>>> legacy\n"
        ),
        # v2 side is on the legacy (theirs) side here — "always HEAD" fails.
        "server.py": (
            "def route():\n"
            "<<<<<<< HEAD\n"
            '    return "/v1/route"\n'
            "=======\n"
            '    return "/v2/route"\n'
            ">>>>>>> legacy\n"
        ),
        # v2 side is on HEAD here.
        "worker.py": (
            "def queue():\n"
            "<<<<<<< HEAD\n"
            '    return "v2-queue"\n'
            "=======\n"
            '    return "v1-queue"\n'
            ">>>>>>> legacy\n"
        ),
    },
    gold_files={
        "schema.py": "API_VERSION = 2\n",
        "client.py": 'def endpoint():\n    return "/v2/items"\n',
        "server.py": 'def route():\n    return "/v2/route"\n',
        "worker.py": 'def queue():\n    return "v2-queue"\n',
    },
    verifier=all_of(
        file_not_contains("schema.py", *_MARKERS, _FENCE),
        file_not_contains("client.py", *_MARKERS, _FENCE),
        file_not_contains("server.py", *_MARKERS, _FENCE),
        file_not_contains("worker.py", *_MARKERS, _FENCE),
        python_callable_returns("schema.py", "mod.API_VERSION", 2),
        python_callable_returns("client.py", "mod.endpoint()", "/v2/items"),
        python_callable_returns("server.py", "mod.route()", "/v2/route"),
        python_callable_returns("worker.py", "mod.queue()", "v2-queue"),
        file_not_contains("client.py", "/v1/items"),
        file_not_contains("server.py", "/v1/route"),
        file_not_contains("worker.py", "v1-queue"),
    ),
)


# ---------------------------------------------------------------------------
# 304. apply_patch_chain  (HARD: three ordered, dependent unified diffs)
# ---------------------------------------------------------------------------
TASK_304 = Task(
    id="task_304_apply_patch_chain",
    name="Apply an ordered chain of three dependent unified diffs",
    tags=("patch", "apply", "multistep", "python", "hard"),
    prompt=(
        "В файлы p1.patch, p2.patch, p3.patch записаны три unified diff для"
        " seq.py, которые нужно применить ПО ПОРЯДКУ: сначала p1, затем p2,"
        " затем p3. Каждый следующий патч рассчитан на состояние файла ПОСЛЕ"
        " предыдущего, поэтому порядок важен. Применить все три (git apply по"
        " очереди или вручную). После этого f(x) должна возвращать (x + 1) * 2,"
        " а g(x) — f(x) - 3. Файлы патчей можно оставить."
    ),
    setup_files={
        "seq.py": "def f(x):\n    return x\n",
        "p1.patch": (
            "--- a/seq.py\n+++ b/seq.py\n@@ -1,2 +1,2 @@\n"
            " def f(x):\n-    return x\n+    return x + 1\n"
        ),
        "p2.patch": (
            "--- a/seq.py\n+++ b/seq.py\n@@ -1,2 +1,2 @@\n"
            " def f(x):\n-    return x + 1\n+    return (x + 1) * 2\n"
        ),
        "p3.patch": (
            "--- a/seq.py\n+++ b/seq.py\n@@ -1,2 +1,6 @@\n"
            " def f(x):\n     return (x + 1) * 2\n"
            "+\n+\n+def g(x):\n+    return f(x) - 3\n"
        ),
    },
    gold_files={
        "seq.py": (
            "def f(x):\n    return (x + 1) * 2\n\n\ndef g(x):\n    return f(x) - 3\n"
        ),
    },
    verifier=all_of(
        file_not_contains("seq.py", *_MARKERS, _FENCE),
        python_callable_returns("seq.py", "mod.f(3)", 8),
        python_callable_returns("seq.py", "mod.g(3)", 5),
        python_callable_returns("seq.py", "mod.f(0)", 2),
    ),
)


# ---------------------------------------------------------------------------
# 305. resolve_and_propagate_rename  (HARD: resolve + rename across 3 files +
# pytest gate)
# ---------------------------------------------------------------------------
TASK_305 = Task(
    id="task_305_resolve_and_propagate_rename",
    name="Resolve a rename conflict and propagate it across the codebase",
    tags=("merge", "conflict", "rename", "multifile", "pytest", "hard"),
    prompt=(
        "В core.py конфликт слияния по ИМЕНИ функции: наша ветка (HEAD)"
        " называет её compute, ветка rename — calculate (тело одинаковое)."
        " Прими вариант ветки rename, то есть функция должна называться"
        " calculate. Затем во ВСЁМ проекте обнови ссылки на старое имя:\n"
        "- в api.py разреши его собственный конфликт (FACTOR: возьми вариант"
        " ветки rename — 20) и замени compute на calculate (и в импорте, и в"
        " вызове);\n"
        "- в cli.py замени compute на calculate (в импорте и вызове).\n"
        "Имя compute не должно остаться нигде. В каталоге tests/ лежат тесты —"
        " после правок команда pytest должна проходить. Удали все маркеры"
        " конфликта; лишних символов не добавляй."
    ),
    setup_files={
        "core.py": (
            "<<<<<<< HEAD\n"
            "def compute(x):\n"
            "    return x * x\n"
            "=======\n"
            "def calculate(x):\n"
            "    return x * x\n"
            ">>>>>>> rename\n"
        ),
        "api.py": (
            "from core import compute\n"
            "\n"
            "<<<<<<< HEAD\n"
            "FACTOR = 10\n"
            "=======\n"
            "FACTOR = 20\n"
            ">>>>>>> rename\n"
            "\n"
            "\n"
            "def run(n):\n"
            "    return compute(n) + FACTOR\n"
        ),
        "cli.py": (
            "from core import compute\n"
            "\n"
            "\n"
            "def main(n):\n"
            "    return compute(n) * 2\n"
        ),
        "tests/test_app.py": (
            "from api import run\n"
            "from cli import main\n"
            "\n"
            "\n"
            "def test_run():\n"
            "    assert run(3) == 29\n"
            "\n"
            "\n"
            "def test_main():\n"
            "    assert main(4) == 32\n"
        ),
    },
    gold_files={
        "core.py": "def calculate(x):\n    return x * x\n",
        "api.py": (
            "from core import calculate\n"
            "\n"
            "FACTOR = 20\n"
            "\n"
            "\n"
            "def run(n):\n"
            "    return calculate(n) + FACTOR\n"
        ),
        "cli.py": (
            "from core import calculate\n"
            "\n"
            "\n"
            "def main(n):\n"
            "    return calculate(n) * 2\n"
        ),
    },
    verifier=all_of(
        file_not_contains("core.py", *_MARKERS, _FENCE, "compute"),
        file_not_contains("api.py", *_MARKERS, _FENCE, "compute"),
        file_not_contains("cli.py", *_MARKERS, _FENCE, "compute"),
        pytest_passes("tests"),
    ),
)


# ---------------------------------------------------------------------------
# 306. compute_deep_merge  (HARD: semantic merge requiring computation)
# ---------------------------------------------------------------------------
TASK_306 = Task(
    id="task_306_compute_deep_merge",
    name="Compute a semantic deep-merge of two JSON configs",
    tags=("merge", "json", "compute", "hard"),
    prompt=(
        "Есть два файла-конфига ours.json и theirs.json, которые надо слить в"
        " merged.json по следующим правилам (выбрать одну сторону НЕЛЬЗЯ —"
        " нужно вычислять):\n"
        "- для каждого ключа, который есть в обоих и значение число —"
        " сложить значения;\n"
        "- ключи, которые есть только в одной стороне, перенести как есть;\n"
        "- для значений-списков строк: объединить оба списка, убрать дубли и"
        " отсортировать по алфавиту;\n"
        "- вложенные объекты сливать по тем же правилам рекурсивно.\n"
        "Запиши результат в merged.json (валидный JSON, без лишних символов"
        " и markdown-обрамления)."
    ),
    setup_files={
        "ours.json": (
            "{\n"
            '  "limits": {"cpu": 2, "mem": 512},\n'
            '  "tags": ["a", "c"],\n'
            '  "meta": {"owner": "x"}\n'
            "}\n"
        ),
        "theirs.json": (
            "{\n"
            '  "limits": {"cpu": 3, "gpu": 1},\n'
            '  "tags": ["b", "a"],\n'
            '  "meta": {"team": "y"}\n'
            "}\n"
        ),
    },
    gold_files={
        "merged.json": (
            "{\n"
            '  "limits": {"cpu": 5, "mem": 512, "gpu": 1},\n'
            '  "tags": ["a", "b", "c"],\n'
            '  "meta": {"owner": "x", "team": "y"}\n'
            "}\n"
        ),
    },
    verifier=all_of(
        file_not_contains("merged.json", *_MARKERS, _FENCE),
        json_file_matches(
            "merged.json",
            {
                "limits": {"cpu": 5, "mem": 512, "gpu": 1},
                "tags": ["a", "b", "c"],
                "meta": {"owner": "x", "team": "y"},
            },
        ),
    ),
)


# ---------------------------------------------------------------------------
# 307. resolve_many_files  (HARD: 12 files, keep-larger, pytest aggregate)
# ---------------------------------------------------------------------------
_WEIGHT_PAIRS = [
    (7, 13), (22, 9), (4, 18), (30, 11), (6, 25), (17, 8),
    (2, 19), (28, 14), (15, 33), (10, 5), (21, 12), (3, 27),
]
_WEIGHT_NAMES = [f"mod{i:02d}" for i in range(1, len(_WEIGHT_PAIRS) + 1)]
_WEIGHT_GOLD = [max(a, b) for a, b in _WEIGHT_PAIRS]
_WEIGHT_TOTAL = sum(_WEIGHT_GOLD)  # 263


def _weight_setup() -> dict[str, str]:
    files: dict[str, str] = {}
    for name, (head, theirs) in zip(_WEIGHT_NAMES, _WEIGHT_PAIRS, strict=True):
        files[f"{name}.py"] = (
            "<<<<<<< HEAD\n"
            f"WEIGHT = {head}\n"
            "=======\n"
            f"WEIGHT = {theirs}\n"
            ">>>>>>> tuning\n"
        )
    imports = "".join(f"from {n} import WEIGHT as {n}\n" for n in _WEIGHT_NAMES)
    total_expr = " + ".join(_WEIGHT_NAMES)
    files["tests/test_total.py"] = (
        f"{imports}"
        "\n"
        "\n"
        "def test_total():\n"
        f"    assert ({total_expr}) == {_WEIGHT_TOTAL}\n"
    )
    return files


def _weight_gold() -> dict[str, str | None]:
    return {
        f"{name}.py": f"WEIGHT = {larger}\n"
        for name, larger in zip(_WEIGHT_NAMES, _WEIGHT_GOLD, strict=True)
    }


def _weight_verifier_parts():
    parts = []
    for name, (head, theirs), larger in zip(
        _WEIGHT_NAMES, _WEIGHT_PAIRS, _WEIGHT_GOLD, strict=True
    ):
        smaller = min(head, theirs)
        parts.append(
            file_not_contains(f"{name}.py", *_MARKERS, _FENCE, f"WEIGHT = {smaller}")
        )
        parts.append(file_lines_equal(f"{name}.py", [f"WEIGHT = {larger}"]))
    return parts


TASK_307 = Task(
    id="task_307_resolve_many_files",
    name="Resolve a conflict in twelve files, keeping the larger weight",
    tags=("merge", "conflict", "multifile", "pytest", "hard"),
    prompt=(
        "В каталоге лежат двенадцать модулей mod01.py..mod12.py. В КАЖДОМ из"
        " них конфликт слияния по переменной WEIGHT (вариант нашей ветки HEAD"
        " и вариант ветки tuning). В каждом файле оставь тот вариант, где"
        " WEIGHT БОЛЬШЕ, и удали маркеры конфликта. В каждом файле должна"
        " остаться ровно одна строка WEIGHT = <число>, без лишних символов."
        " В каталоге tests/ лежит тест суммы — после правок pytest должен"
        " проходить (он проверяет, что сумма всех WEIGHT равна ожидаемой)."
    ),
    setup_files=_weight_setup(),
    gold_files=_weight_gold(),
    verifier=all_of(*_weight_verifier_parts(), pytest_passes("tests")),
)


# ---------------------------------------------------------------------------
# 308. detect_unresolved_conflicts  (HARD: nested dirs + false-positive traps)
# ---------------------------------------------------------------------------
def _verify_unresolved_list(ws: Path) -> VerifyResult:
    """Pass when report.txt names exactly the two genuinely-conflicted files."""
    p = ws / "report.txt"
    if not p.exists():
        return VerifyResult(False, "report.txt missing")
    raw = [
        line.strip().replace("\\", "/")
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    got = sorted({r[2:] if r.startswith("./") else r for r in raw})
    expected = ["src/core/engine.py", "src/util/parse.py"]
    if got == expected:
        return VerifyResult(True, "report.txt lists exactly the conflicted files")
    return VerifyResult(False, f"report.txt {got!r} differs from {expected!r}")


TASK_308 = Task(
    id="task_308_detect_unresolved_conflicts",
    name="Detect genuinely-unresolved conflicts, avoiding traps",
    tags=("merge", "conflict", "analyze", "hard"),
    prompt=(
        "В каталоге src/ (включая вложенные подкаталоги) лежат файлы. Найди"
        " только те, где есть НЕРАЗРЕШЁННЫЙ конфликт слияния. Файл считается"
        " конфликтным, ТОЛЬКО если в нём есть И открывающий маркер <<<<<<<, И"
        " закрывающий >>>>>>>. Файлы, где встречается лишь строка-разделитель"
        " из знаков = (например ======= как подчёркивание заголовка), только"
        " открывающий маркер без закрывающего, или просто стрелки >>> в тексте"
        " — конфликтными НЕ считаются. Запиши пути найденных файлов"
        " относительно корня рабочей директории (например src/core/engine.py)"
        " в файл report.txt — по одному пути на строку, по алфавиту."
    ),
    setup_files={
        "src/core/engine.py": (
            "def run():\n"
            "<<<<<<< HEAD\n"
            "    return 1\n"
            "=======\n"
            "    return 2\n"
            ">>>>>>> branch\n"
        ),
        "src/util/parse.py": (
            "VALUE = 0\n"
            "<<<<<<< HEAD\n"
            "VALUE = 1\n"
            "=======\n"
            "VALUE = 2\n"
            ">>>>>>> feature\n"
        ),
        # Trap 1: markdown separator of '=' only — NOT a conflict.
        "src/docs/readme.md": ("Title\n=======\nSome description text.\n"),
        # Trap 2: opening marker only, no closing — NOT a full conflict.
        "src/util/half.py": ("x = 1\n<<<<<<< HEAD\nx = 2\n"),
        # Trap 3: a shell redirect with '>>>' — NOT a conflict.
        "src/util/arrows.txt": ("echo done >>> log.txt\nnext line\n"),
        # Clean file.
        "src/core/ok.py": "def ok():\n    return True\n",
    },
    gold_files={"report.txt": "src/core/engine.py\nsrc/util/parse.py\n"},
    verifier=_verify_unresolved_list,
)


# ---------------------------------------------------------------------------
# 309. rename_refactor_scale  (HARD: resolve def + propagate rename across 12
# files that each carry their own conflict)
# ---------------------------------------------------------------------------
_RENAME_USERS = [f"c{i}" for i in range(1, 13)]

TASK_309 = Task(
    id="task_309_rename_refactor_scale",
    name="Resolve a rename conflict and propagate it to twelve conflicted call sites",
    tags=("merge", "conflict", "rename", "multifile", "pytest", "hard"),
    prompt=(
        "В lib.py конфликт слияния по имени функции: наша ветка (HEAD) называет"
        " её parse, ветка rename — decode (тело одинаковое). Прими вариант"
        " ветки rename: функция должна называться decode. Затем в КАЖДОМ из"
        " двенадцати файлов c1.py..c12.py есть СВОЙ конфликт слияния внутри"
        " функции run — прими в нём сторону ветки rename (между ======= и"
        " >>>>>>>), и после этого обнови ссылку на функцию: и в импорте"
        " (from lib import parse), и в вызове parse(...) заменить на decode."
        " Имя parse не должно остаться нигде, маркеров конфликта быть не"
        " должно. В каталоге tests/ лежат тесты — после правок pytest должен"
        " проходить. Лишних символов не добавляй."
    ),
    setup_files={
        "lib.py": (
            "<<<<<<< HEAD\n"
            "def parse(s):\n"
            "    return s.strip()\n"
            "=======\n"
            "def decode(s):\n"
            "    return s.strip()\n"
            ">>>>>>> rename\n"
        ),
        **{
            f"{name}.py": (
                "from lib import parse\n"
                "\n"
                "\n"
                "def run():\n"
                "<<<<<<< HEAD\n"
                f'    return parse("  {name}!  ")\n'
                "=======\n"
                f'    return parse("{name}")\n'
                ">>>>>>> rename\n"
            )
            for name in _RENAME_USERS
        },
        "tests/test_users.py": (
            "".join(f"from {n} import run as {n}\n" for n in _RENAME_USERS)
            + "\n\n"
            + "def test_all():\n"
            + "".join(f'    assert {n}() == "{n}"\n' for n in _RENAME_USERS)
        ),
    },
    gold_files={
        "lib.py": "def decode(s):\n    return s.strip()\n",
        **{
            f"{name}.py": (
                "from lib import decode\n"
                "\n"
                "\n"
                "def run():\n"
                f'    return decode("{name}")\n'
            )
            for name in _RENAME_USERS
        },
    },
    verifier=all_of(
        file_not_contains("lib.py", *_MARKERS, _FENCE, "parse"),
        *[
            file_not_contains(f"{n}.py", *_MARKERS, _FENCE, "parse", f"{n}!")
            for n in _RENAME_USERS
        ],
        pytest_passes("tests"),
    ),
)


# ---------------------------------------------------------------------------
# 310. module_split  (HARD: resolve + split file into a package + fix imports)
# ---------------------------------------------------------------------------
TASK_310 = Task(
    id="task_310_module_split",
    name="Resolve a conflict, split a module into a package, repoint imports",
    tags=("merge", "conflict", "refactor", "multifile", "pytest", "hard"),
    prompt=(
        "В monolith.py конфликт слияния по константе SCALE — прими вариант"
        " нашей ветки (HEAD): SCALE = 2. Затем выполни рефакторинг:\n"
        "- создай пакет pkg/ с файлом pkg/geometry.py, перенеси туда константу"
        " SCALE и функции area и perimeter из monolith.py;\n"
        "- сделай pkg/__init__.py, который реэкспортирует area и perimeter"
        " (from pkg.geometry import area, perimeter);\n"
        "- в app.py поменяй импорт с 'from monolith import area, perimeter' на"
        " 'from pkg import area, perimeter' (остальной код не трогай);\n"
        "- удали файл monolith.py.\n"
        "В каталоге tests/ лежит тест — после рефакторинга pytest должен"
        " проходить. Маркеров конфликта и лишних символов быть не должно."
    ),
    setup_files={
        "monolith.py": (
            "<<<<<<< HEAD\n"
            "SCALE = 2\n"
            "=======\n"
            "SCALE = 3\n"
            ">>>>>>> tuning\n"
            "\n"
            "\n"
            "def area(r):\n"
            "    return SCALE * r * r\n"
            "\n"
            "\n"
            "def perimeter(r):\n"
            "    return SCALE * r\n"
        ),
        "app.py": (
            "from monolith import area, perimeter\n"
            "\n"
            "\n"
            "def report(r):\n"
            "    return (area(r), perimeter(r))\n"
        ),
        "tests/test_app.py": (
            "from app import report\n"
            "\n"
            "\n"
            "def test_report():\n"
            "    assert report(5) == (50, 10)\n"
        ),
    },
    gold_files={
        "monolith.py": None,
        "pkg/__init__.py": "from pkg.geometry import area, perimeter\n",
        "pkg/geometry.py": (
            "SCALE = 2\n"
            "\n"
            "\n"
            "def area(r):\n"
            "    return SCALE * r * r\n"
            "\n"
            "\n"
            "def perimeter(r):\n"
            "    return SCALE * r\n"
        ),
        "app.py": (
            "from pkg import area, perimeter\n"
            "\n"
            "\n"
            "def report(r):\n"
            "    return (area(r), perimeter(r))\n"
        ),
    },
    verifier=all_of(
        file_does_not_exist("monolith.py"),
        file_not_contains("app.py", *_MARKERS, _FENCE, "monolith"),
        file_not_contains("pkg/geometry.py", *_MARKERS, _FENCE),
        pytest_passes("tests"),
    ),
)


# ---------------------------------------------------------------------------
# 311. apply_patch_stack_multifile  (HARD: 5 ordered patches across 3 files)
# ---------------------------------------------------------------------------
def _hunk(name: str, fn: str, before: int, after: int) -> str:
    return (
        f"--- a/{name}\n+++ b/{name}\n@@ -1,2 +1,2 @@\n"
        f" def {fn}():\n-    return {before}\n+    return {after}\n"
    )


TASK_311 = Task(
    id="task_311_apply_patch_stack_multifile",
    name="Apply nine ordered patches spanning four files",
    tags=("patch", "apply", "multistep", "multifile", "python", "hard"),
    prompt=(
        "В каталоге лежат девять патчей p1.patch..p9.patch, затрагивающих"
        " файлы a.py, b.py, c.py, d.py. Применить их СТРОГО ПО ПОРЯДКУ от p1 к"
        " p9. Несколько патчей изменяют один и тот же файл повторно и"
        " рассчитаны на результат предыдущего, поэтому порядок критичен:"
        " применение не по порядку приведёт к ошибке. Применять можно git"
        " apply по очереди или вручную. После всех девяти патчей должно быть:"
        " a()==4, b()==30, c()==300, d()==3000. Затем создай файл"
        " summary.json с актуальными значениями всех четырёх функций в виде"
        ' объекта {"a": ..., "b": ..., "c": ..., "d": ...} (числа — это то, что'
        " возвращают функции после применения всех патчей). Файлы патчей можно"
        " оставить."
    ),
    setup_files={
        "a.py": "def a():\n    return 1\n",
        "b.py": "def b():\n    return 10\n",
        "c.py": "def c():\n    return 100\n",
        "d.py": "def d():\n    return 1000\n",
        "p1.patch": _hunk("a.py", "a", 1, 2),
        "p2.patch": _hunk("b.py", "b", 10, 20),
        "p3.patch": _hunk("a.py", "a", 2, 3),
        "p4.patch": _hunk("c.py", "c", 100, 200),
        "p5.patch": _hunk("b.py", "b", 20, 30),
        "p6.patch": _hunk("d.py", "d", 1000, 2000),
        "p7.patch": _hunk("a.py", "a", 3, 4),
        "p8.patch": _hunk("c.py", "c", 200, 300),
        "p9.patch": _hunk("d.py", "d", 2000, 3000),
    },
    gold_files={
        "a.py": "def a():\n    return 4\n",
        "b.py": "def b():\n    return 30\n",
        "c.py": "def c():\n    return 300\n",
        "d.py": "def d():\n    return 3000\n",
        "summary.json": '{"a": 4, "b": 30, "c": 300, "d": 3000}\n',
    },
    verifier=all_of(
        file_not_contains("a.py", *_MARKERS, _FENCE),
        file_not_contains("b.py", *_MARKERS, _FENCE),
        file_not_contains("c.py", *_MARKERS, _FENCE),
        file_not_contains("d.py", *_MARKERS, _FENCE),
        python_callable_returns("a.py", "mod.a()", 4),
        python_callable_returns("b.py", "mod.b()", 30),
        python_callable_returns("c.py", "mod.c()", 300),
        python_callable_returns("d.py", "mod.d()", 3000),
        json_file_matches("summary.json", {"a": 4, "b": 30, "c": 300, "d": 3000}),
    ),
)


# ---------------------------------------------------------------------------
# 312. policy_driven_merge  (HARD: a manifest decides each of twelve files)
# ---------------------------------------------------------------------------
_POLICY_CASES = [
    ("m01", 5, 9, "ours"), ("m02", 12, 7, "theirs"), ("m03", 3, 8, "ours"),
    ("m04", 20, 15, "theirs"), ("m05", 6, 11, "ours"), ("m06", 14, 9, "theirs"),
    ("m07", 2, 17, "ours"), ("m08", 19, 4, "theirs"), ("m09", 15, 33, "ours"),
    ("m10", 10, 5, "theirs"), ("m11", 21, 12, "ours"), ("m12", 27, 3, "theirs"),
    ("m13", 8, 16, "theirs"), ("m14", 31, 18, "ours"), ("m15", 13, 6, "theirs"),
    ("m16", 24, 29, "ours"),
]
_POLICY_GOLD = {n: (h if pol == "ours" else t) for n, h, t, pol in _POLICY_CASES}
_POLICY_TOTAL = sum(_POLICY_GOLD.values())  # 95


def _policy_setup() -> dict[str, str]:
    files: dict[str, str] = {}
    for name, head, theirs, _pol in _POLICY_CASES:
        files[f"{name}.py"] = (
            "<<<<<<< HEAD\n"
            f"VALUE = {head}\n"
            "=======\n"
            f"VALUE = {theirs}\n"
            ">>>>>>> incoming\n"
        )
    files["policy.json"] = json.dumps(
        {n: pol for n, _h, _t, pol in _POLICY_CASES}, indent=2
    ) + "\n"
    imports = "".join(f"from {n} import VALUE as {n}\n" for n, *_ in _POLICY_CASES)
    total_expr = " + ".join(n for n, *_ in _POLICY_CASES)
    files["tests/test_total.py"] = (
        f"{imports}\n\ndef test_total():\n    assert ({total_expr}) == {_POLICY_TOTAL}\n"
    )
    return files


TASK_312 = Task(
    id="task_312_policy_driven_merge",
    name="Resolve sixteen conflicts according to a policy manifest",
    tags=("merge", "conflict", "multifile", "policy", "pytest", "hard"),
    prompt=(
        "В каталоге шестнадцать модулей m01.py..m16.py, в каждом конфликт"
        " слияния по переменной VALUE: вариант нашей ветки (HEAD) и вариант"
        " ветки incoming. Какой вариант оставить — задано в файле policy.json:"
        " для каждого модуля значение \"ours\" означает взять сторону HEAD, а"
        " \"theirs\" — сторону incoming. Разреши конфликт в КАЖДОМ модуле строго"
        " по policy.json. В каждом файле должна остаться ровно одна строка"
        " VALUE = <число> без маркеров и лишних символов. После этого собери"
        " файл resolved.json — JSON-объект, который для каждого модуля"
        ' сопоставляет его имени выбранное значение, например {"m01": 5,'
        ' "m02": 7, ...} по всем шестнадцати модулям. В каталоге tests/ лежит'
        " тест суммы — после правок pytest должен проходить."
    ),
    setup_files=_policy_setup(),
    gold_files={
        **{
            f"{name}.py": f"VALUE = {_POLICY_GOLD[name]}\n" for name, *_ in _POLICY_CASES
        },
        "resolved.json": json.dumps(_POLICY_GOLD, indent=2) + "\n",
    },
    verifier=all_of(
        *[
            part
            for name, head, theirs, _pol in _POLICY_CASES
            for part in (
                file_not_contains(
                    f"{name}.py",
                    *_MARKERS,
                    _FENCE,
                    f"VALUE = {head if _POLICY_GOLD[name] != head else theirs}",
                ),
                file_lines_equal(f"{name}.py", [f"VALUE = {_POLICY_GOLD[name]}"]),
            )
        ],
        json_file_matches("resolved.json", _POLICY_GOLD),
        pytest_passes("tests"),
    ),
)


# ---------------------------------------------------------------------------
# 313. aggregate_config_fragments  (HARD: resolve 5 fragments + precedence merge)
# ---------------------------------------------------------------------------
def _fragment(head: str, feature: str) -> str:
    return (
        "{\n"
        "<<<<<<< HEAD\n"
        f"{head}\n"
        "=======\n"
        f"{feature}\n"
        ">>>>>>> feature\n"
        "}\n"
    )


TASK_313 = Task(
    id="task_313_aggregate_config_fragments",
    name="Resolve five config fragments and merge them with precedence",
    tags=("merge", "conflict", "json", "compute", "multifile", "hard"),
    prompt=(
        "В каталоге conf.d/ лежат пять JSON-фрагментов 01.json..05.json, в"
        " каждом конфликт слияния. Сначала в КАЖДОМ фрагменте прими сторону"
        " ветки feature (между ======= и >>>>>>>). Затем объедини все пять"
        " разрешённых фрагментов в один файл final.json по правилам"
        " (порядок применения — по возрастанию имени файла, 01 → 05):\n"
        "- скалярные значения (строки, числа, булевы): более поздний фрагмент"
        " перезаписывает более ранний;\n"
        "- значения-списки строк: объединить из всех фрагментов, убрать дубли,"
        " отсортировать по алфавиту.\n"
        "Запиши результат в final.json (валидный JSON, без маркеров и лишних"
        " символов)."
    ),
    setup_files={
        "conf.d/01.json": _fragment('"name": "old", "tags": ["x"]', '"name": "app", "tags": ["x"]'),
        "conf.d/02.json": _fragment('"retries": 1, "tags": ["q"]', '"retries": 3, "tags": ["y"]'),
        "conf.d/03.json": _fragment('"debug": false, "tags": ["x"]', '"debug": true, "tags": ["x", "z"]'),
        "conf.d/04.json": _fragment('"workers": 2', '"workers": 4'),
        "conf.d/05.json": _fragment('"workers": 5, "tags": ["k"]', '"workers": 8, "tags": ["w"]'),
    },
    gold_files={
        "final.json": (
            "{\n"
            '  "name": "app",\n'
            '  "retries": 3,\n'
            '  "debug": true,\n'
            '  "workers": 8,\n'
            '  "tags": ["w", "x", "y", "z"]\n'
            "}\n"
        ),
    },
    verifier=all_of(
        file_not_contains("final.json", *_MARKERS, _FENCE),
        json_file_matches(
            "final.json",
            {
                "name": "app",
                "retries": 3,
                "debug": True,
                "workers": 8,
                "tags": ["w", "x", "y", "z"],
            },
        ),
    ),
)


VCS_TASKS: list[Task] = [
    TASK_299,
    TASK_300,
    TASK_301,
    TASK_302,
    TASK_303,
    TASK_304,
    TASK_305,
    TASK_306,
    TASK_307,
    TASK_308,
    TASK_309,
    TASK_310,
    TASK_311,
    TASK_312,
    TASK_313,
]
