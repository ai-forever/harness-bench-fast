"""Adversarial / robustness tasks for harness-bench-fast.

This wave measures how an agent copes with a HOSTILE environment: obstacles it
must diagnose and work around rather than a clean task it just executes. The
guiding principle is *the environment is the source of truth* — instructions,
config files, build scripts, and skills may be stale, broken, or contradictory,
and the agent has to reconcile them against what actually exists on disk.

Every verifier stays mechanical, offline, and deterministic so the wave passes
the `verify-gold` CI gate. Each obstacle has exactly one correct resolution and
a checkable artifact; the gold solution produces that artifact directly.

Pilot batch (task-set v0.11.0): task_331..task_337, one per obstacle family:

* 331 — legacy Python 2 source that must be ported to run under Python 3
* 332 — a build whose documented command (`make build`) is broken
* 333 — a data file in a non-UTF-8 encoding (Windows-1251)
* 334 — a file locked unreadable via POSIX permissions (chmod 000)
* 335 — an instruction that names a file which does not exist in the workspace
* 336 — a script with a hardcoded absolute path that does not resolve
* 337 — a skill whose referenced template is missing, with a documented fallback

Follow-up batch (task-set v0.12.0): task_338..task_350, completing the wave:

* 338 — source importing a stdlib name removed in modern Python (collections.abc)
* 339 — a `.python-version` file that lies about the interpreter (distractor)
* 340 — a `requirements.txt` pinning an unneeded, uninstallable dependency
* 341 — a `set -e` shell script that aborts on a missing command
* 342 — a documented `npm run build` in a project that is actually Python
* 343 — a gzip stream masquerading behind a `.txt` extension
* 344 — a log polluted with a BOM and embedded NUL bytes to sanitize
* 345 — an AGENTS.md that lies about the source layout (`src/` vs `app/`)
* 346 — a prompt that names the wrong tests directory (`tests/` vs `spec/`)
* 347 — an import that points at a package path that does not exist
* 348 — a broken package layout: a submodule missing from its package dir
* 349 — a SKILL.md with malformed (unclosed) YAML frontmatter to repair
* 350 — two contradictory skills; one is marked deprecated, one authoritative

Scale batch (task-set v0.13.0): task_351, on context discipline at scale:

* 351 — a ~100 MB log the agent must NOT slurp whole; it must use streaming /
  targeted tools (grep, wc, python) to find a needle token and count ERROR lines
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

from harness_bench.core import Task, VerifyResult
from harness_bench.verifiers import (
    all_of,
    file_contains,
    file_lines_equal,
    file_matches_regex,
    file_not_contains,
    file_text_equals,
    json_file_has,
    pytest_passes,
    python_callable_returns,
    python_runs,
)

# ---------------------------------------------------------------------------
# 331 — port Python 2 source so it runs under Python 3
# ---------------------------------------------------------------------------
# The file uses three constructs that are hard errors under Python 3: the
# `print` statement, `xrange`, and `dict.has_key`. Naively running it fails;
# the agent must modernize it while preserving behaviour.
_LEGACY_PY2 = """\
def main():
    print "counting"
    total = 0
    for i in xrange(5):
        total += i
    d = {"a": 1}
    if d.has_key("a"):
        total += 10
    print total


main()
"""

_LEGACY_GOLD = """\
def main():
    print("counting")
    total = 0
    for i in range(5):
        total += i
    d = {"a": 1}
    if "a" in d:
        total += 10
    print(total)


main()
"""

TASK_331 = Task(
    id="task_331_adv_py2_to_py3",
    name="Port a Python 2 script to run under Python 3",
    tags=("adversarial", "python", "port", "medium"),
    prompt=(
        "Скрипт legacy.py написан под Python 2 и падает на Python 3. Почини его"
        " так, чтобы он запускался на Python 3 и печатал РОВНО:\n"
        "counting\n"
        "20\n"
        "Поведение сохрани, меняй только несовместимый с Python 3 синтаксис."
    ),
    setup_files={"legacy.py": _LEGACY_PY2},
    gold_files={"legacy.py": _LEGACY_GOLD},
    verifier=python_runs("legacy.py", expected_stdout="counting\n20"),
)


# ---------------------------------------------------------------------------
# 332 — the documented build command is broken; produce the artifact anyway
# ---------------------------------------------------------------------------
# `make build` calls a tool (`bundler`) that does not exist, so the agent
# cannot just run the documented command. It has to understand the INTENT
# (concatenate the src parts in order) and produce the artifact some other way.
_MAKEFILE_332 = """\
build:
\tbundler --out dist/bundle.txt src/part_a.txt src/part_b.txt
"""

_README_332 = (
    "# Bundle\n\n"
    "Сборка: `make build` склеивает файлы из `src/` в `dist/bundle.txt`\n"
    "в порядке `part_a`, затем `part_b`.\n"
)

TASK_332 = Task(
    id="task_332_adv_broken_build",
    name="Produce the build artifact when `make build` is broken",
    tags=("adversarial", "build", "shell", "medium"),
    prompt=(
        "Собери проект: создай файл dist/bundle.txt, склеив содержимое"
        " src/part_a.txt и src/part_b.txt именно в этом порядке. Штатная"
        " сборка (`make build`) не работает — добейся правильного результата"
        " любым способом."
    ),
    setup_files={
        "src/part_a.txt": "alpha\n",
        "src/part_b.txt": "beta\n",
        "Makefile": _MAKEFILE_332,
        "README.md": _README_332,
    },
    gold_files={"dist/bundle.txt": "alpha\nbeta\n"},
    verifier=file_lines_equal("dist/bundle.txt", ["alpha", "beta"]),
)


# ---------------------------------------------------------------------------
# 333 — decode a non-UTF-8 (Windows-1251) data file
# ---------------------------------------------------------------------------
# The bytes are valid cp1251 but invalid UTF-8, so a naive `read_text()` raises
# UnicodeDecodeError. The agent must decode with the right codec and re-emit
# clean UTF-8 JSON.
_CP1251_SOURCE_333 = "город=Москва\nвес=42\n"


def _setup_333(ws: Path) -> None:
    (ws / "payload.dat").write_bytes(_CP1251_SOURCE_333.encode("cp1251"))


TASK_333 = Task(
    id="task_333_adv_cp1251_decode",
    name="Decode a Windows-1251 file and re-emit UTF-8 JSON",
    tags=("adversarial", "encoding", "data", "medium"),
    prompt=(
        "Файл payload.dat записан в кодировке Windows-1251 (cp1251) и содержит"
        " строки вида `ключ=значение`. Прочитай его в правильной кодировке и"
        " создай result.json (UTF-8) с объектом:"
        ' {"city": <значение города>, "weight": <вес как число>}.'
    ),
    setup_callback=_setup_333,
    gold_files={
        "result.json": json.dumps({"city": "Москва", "weight": 42}, ensure_ascii=False)
    },
    verifier=json_file_has("result.json", city="Москва", weight=42),
)


# ---------------------------------------------------------------------------
# 334 — read a file locked unreadable via POSIX permissions (chmod 000)
# ---------------------------------------------------------------------------
# NOTE (root caveat): when the bench runs as root (euid 0), mode 000 does not
# actually block reads, so the obstacle degrades to trivial. The task still
# passes gold and still measures the "extract the value" outcome; it only loses
# discrimination under root. Verified locally (euid 501) that 000 blocks reads.
# The gold solution writes ONLY token.txt and never rewrites the locked file,
# so `apply_gold` cannot hit a PermissionError.
def _setup_334(ws: Path) -> None:
    vault = ws / "vault.txt"
    vault.write_text("TOKEN=ZX99\n")
    vault.chmod(0o000)


TASK_334 = Task(
    id="task_334_adv_locked_file",
    name="Read a permission-locked file after unlocking it",
    tags=("adversarial", "permissions", "posix", "hard"),
    prompt=(
        "Файл vault.txt заблокирован от чтения (права доступа 000). Внутри одна"
        " строка вида `TOKEN=<значение>`. Получи доступ к файлу, прочитай"
        " значение токена и запиши ТОЛЬКО само значение в файл token.txt."
    ),
    setup_callback=_setup_334,
    gold_files={"token.txt": "ZX99\n"},
    verifier=file_text_equals("token.txt", "ZX99"),
)


# ---------------------------------------------------------------------------
# 335 — the instruction names a file that does not exist in the workspace
# ---------------------------------------------------------------------------
# The prompt (and README) talk about `config.yaml`, but the workspace only has
# `config.json`. The agent must apply the intent to the file that actually
# exists rather than inventing a new, ignored yaml file.
_CONFIG_JSON_335 = json.dumps({"timeout": 30, "retries": 3}, indent=2) + "\n"
_README_335 = (
    "# App config\n\n"
    "Настройки приложения хранятся в `config.yaml` (ключи `timeout`, `retries`).\n"
)


def _verify_335(ws: Path) -> VerifyResult:
    """Accept either applying the change to the real file or an honest deferral.

    The prompt names `config.yaml`, which does not exist; the real config is
    `config.json`. Two outcomes are correct: (1) the agent edits the real
    `config.json` to timeout=60, or (2) it does not act (surfaces the
    contradiction / asks) and leaves the workspace honest. Fabricating a parallel
    `config.yaml` — a fake success on a file the app ignores — is the failure.
    """
    decoy = ws / "config.yaml"
    if decoy.exists():
        return VerifyResult(
            False,
            "created config.yaml (an ignored decoy) instead of touching the real config.json",
        )
    cfg = ws / "config.json"
    if not cfg.exists():
        return VerifyResult(False, "config.json missing")
    text = cfg.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"config.json invalid JSON: {exc}")
    if not isinstance(data, dict):
        return VerifyResult(False, "config.json is not a JSON object")
    if data.get("timeout") == 60 and data.get("retries") == 3:
        return VerifyResult(True, "config.json updated to timeout=60")
    if data.get("timeout") == 30 and data.get("retries") == 3:
        return VerifyResult(
            True,
            "config.json left untouched and no config.yaml fabricated (honest deferral)",
        )
    return VerifyResult(False, f"config.json in unexpected state: {data!r}")


TASK_335 = Task(
    id="task_335_adv_missing_config_file",
    name="Apply a config change when the named file does not exist",
    tags=("adversarial", "config", "contradiction", "medium"),
    prompt=(
        "Увеличь значение timeout до 60 в конфиге приложения (`config.yaml`),"
        " остальные настройки оставь без изменений."
    ),
    setup_files={"config.json": _CONFIG_JSON_335, "README.md": _README_335},
    gold_files={"config.json": json.dumps({"timeout": 60, "retries": 3}, indent=2) + "\n"},
    verifier=_verify_335,
)


# ---------------------------------------------------------------------------
# 336 — a script with a hardcoded absolute path that does not resolve
# ---------------------------------------------------------------------------
# `analyze.py` reads `/data/prod/input.csv`, which does not exist. The real
# input lives next to the script as `input.csv`. The agent must repoint the
# script at the file that actually exists so it runs and prints the total.
_ANALYZE_PY_336 = """\
import csv

path = "/data/prod/input.csv"
with open(path) as f:
    rows = list(csv.DictReader(f))
print(sum(int(r["amount"]) for r in rows))
"""

_ANALYZE_GOLD_336 = """\
import csv

path = "input.csv"
with open(path) as f:
    rows = list(csv.DictReader(f))
print(sum(int(r["amount"]) for r in rows))
"""

_INPUT_CSV_336 = "amount\n10\n20\n30\n"

TASK_336 = Task(
    id="task_336_adv_hardcoded_path",
    name="Fix a hardcoded absolute path that does not resolve",
    tags=("adversarial", "python", "paths", "medium"),
    prompt=(
        "Скрипт analyze.py должен посчитать сумму колонки `amount` из файла"
        " input.csv, который лежит рядом со скриптом, и напечатать её. Сейчас в"
        " скрипте захардкожен несуществующий абсолютный путь. Почини так, чтобы"
        " скрипт работал в этой директории и печатал сумму."
    ),
    setup_files={"analyze.py": _ANALYZE_PY_336, "input.csv": _INPUT_CSV_336},
    gold_files={"analyze.py": _ANALYZE_GOLD_336},
    verifier=python_runs("analyze.py", expected_stdout="60"),
)


# ---------------------------------------------------------------------------
# 337 — a skill whose referenced template is missing (documented fallback)
# ---------------------------------------------------------------------------
# The skill points at `templates/invoice.txt`, which is NOT shipped, but the
# SKILL.md documents an exact inline fallback. The fictional tokens (AR-7731,
# PINE-DISCOUNT-4) are unrecoverable without reading the skill, so the agent
# must notice the broken reference and fall back to the inline spec.
_INVOICE_SKILL_337 = """\
---
name: acme-invoice
description: Acme Robotics invoice format. Use when generating an Acme Robotics
  invoice from an item and quantity.
---

# Acme Robotics — Invoice Format

Render the invoice using the template at `templates/invoice.txt`.

If that template file is missing, use this EXACT inline format instead:

ACME-ROBOTICS-INVOICE
VENDOR-CODE: AR-7731
LINE: {item} x{qty}
TERMS: NET-14 / PINE-DISCOUNT-4

Substitute {item} and {qty} from the request. Keep every other line verbatim.
"""

_INVOICE_GOLD_337 = (
    "ACME-ROBOTICS-INVOICE\n"
    "VENDOR-CODE: AR-7731\n"
    "LINE: gyroscope x3\n"
    "TERMS: NET-14 / PINE-DISCOUNT-4\n"
)

TASK_337 = Task(
    id="task_337_adv_skill_missing_template",
    name="Use a skill's documented fallback when its template is missing",
    tags=("adversarial", "skill", "office", "hard"),
    prompt=(
        "Сгенерируй счёт Acme Robotics для позиции \"gyroscope\" количеством 3,"
        " строго по формату скилла Acme Robotics. Результат сохрани в файл"
        " invoice.txt в корне рабочей директории."
    ),
    # Ship the skill in both standard discovery dirs (Claude Code reads
    # `.claude/skills`; codex/openhands/opencode read `.agents/skills`).
    # Deliberately do NOT ship templates/invoice.txt — the referenced path is
    # broken on purpose so the agent must use the inline fallback.
    setup_files={
        ".claude/skills/acme-invoice/SKILL.md": _INVOICE_SKILL_337,
        ".agents/skills/acme-invoice/SKILL.md": _INVOICE_SKILL_337,
    },
    gold_files={"invoice.txt": _INVOICE_GOLD_337},
    verifier=all_of(
        file_contains(
            "invoice.txt",
            "ACME-ROBOTICS-INVOICE",
            "VENDOR-CODE: AR-7731",
            "LINE: gyroscope x3",
            "TERMS: NET-14 / PINE-DISCOUNT-4",
        ),
    ),
)


# ---------------------------------------------------------------------------
# 338 — source imports a stdlib name removed in modern Python
# ---------------------------------------------------------------------------
# `from collections import Mapping` was a hard error since Python 3.10 (the ABCs
# moved to `collections.abc`). Under the current interpreter the import raises
# ImportError, so a naive run fails; the agent must modernize the import.
_STDLIB_338 = """\
from collections import Mapping


def is_mapping(x):
    return isinstance(x, Mapping)


print(is_mapping({"a": 1}))
print(is_mapping([1, 2]))
"""

_STDLIB_GOLD_338 = """\
from collections.abc import Mapping


def is_mapping(x):
    return isinstance(x, Mapping)


print(is_mapping({"a": 1}))
print(is_mapping([1, 2]))
"""

TASK_338 = Task(
    id="task_338_adv_removed_stdlib",
    name="Fix an import of a stdlib name removed in modern Python",
    tags=("adversarial", "python", "stdlib", "medium"),
    prompt=(
        "Скрипт mapping.py падает на импорте: он использует устаревшее"
        " расположение stdlib. Почини импорт так, чтобы скрипт запускался на"
        " современном Python и печатал РОВНО:\n"
        "True\n"
        "False"
    ),
    setup_files={"mapping.py": _STDLIB_338},
    gold_files={"mapping.py": _STDLIB_GOLD_338},
    verifier=python_runs("mapping.py", expected_stdout="True\nFalse"),
)


# ---------------------------------------------------------------------------
# 339 — a `.python-version` file that lies about the interpreter
# ---------------------------------------------------------------------------
# `.python-version` pins 3.6 (where `match` does not exist), but the code needs
# 3.10+ `match` and the stand runs a modern interpreter. The version file is a
# distractor: the real work is a missing `case` in the classifier. The agent
# must trust the environment (tests pass under the actual interpreter), not the
# stale pin, and must not "downgrade" the code away from working syntax.
_CLASSIFY_339 = """\
def classify(code):
    match code:
        case 200:
            return "ok"
        case 404:
            return "missing"
        case _:
            return "unknown"
"""

_TEST_CLASSIFY_339 = """\
from classify import classify


def test_known_codes():
    assert classify(200) == "ok"
    assert classify(404) == "missing"


def test_server_error():
    assert classify(500) == "error"


def test_default():
    assert classify(1) == "unknown"
"""

_CLASSIFY_GOLD_339 = """\
def classify(code):
    match code:
        case 200:
            return "ok"
        case 404:
            return "missing"
        case 500:
            return "error"
        case _:
            return "unknown"
"""

TASK_339 = Task(
    id="task_339_adv_false_python_version",
    name="Fix a classifier while ignoring a misleading .python-version",
    tags=("adversarial", "python", "pytest", "distractor", "medium"),
    prompt=(
        "В мини-репозитории падает тест: classify(500) должен возвращать"
        " \"error\". Почини classify.py и добейся, чтобы все тесты в tests/"
        " проходили. Файл tests/test_classify.py менять нельзя."
    ),
    setup_files={
        "classify.py": _CLASSIFY_339,
        "tests/test_classify.py": _TEST_CLASSIFY_339,
        ".python-version": "3.6\n",
    },
    gold_files={"classify.py": _CLASSIFY_GOLD_339},
    verifier=pytest_passes("tests"),
)


# ---------------------------------------------------------------------------
# 340 — requirements.txt pins an unneeded, uninstallable dependency
# ---------------------------------------------------------------------------
# The script imports a package that is not installed and cannot be installed
# offline — but it is never actually used (the padding is plain stdlib). The
# correct move is to recognise the import is dead and drop it, NOT to try to
# `pip install` in an offline sandbox.
_PAD_340 = """\
import leftpad_ultra


def pad(value, width):
    return value.rjust(width, "0")


print(pad("42", 5))
"""

_PAD_GOLD_340 = """\
def pad(value, width):
    return value.rjust(width, "0")


print(pad("42", 5))
"""

TASK_340 = Task(
    id="task_340_adv_unneeded_dependency",
    name="Drop an unused, uninstallable dependency instead of installing it",
    tags=("adversarial", "python", "dependencies", "offline", "medium"),
    prompt=(
        "Скрипт pad.py не запускается: он импортирует пакет leftpad_ultra,"
        " которого нет в окружении (и поставить его нельзя — сети нет). Сделай"
        " так, чтобы скрипт работал и печатал 00042. Ставить пакеты не нужно."
    ),
    setup_files={"pad.py": _PAD_340, "requirements.txt": "leftpad-ultra==9.9.9\n"},
    gold_files={"pad.py": _PAD_GOLD_340},
    verifier=python_runs("pad.py", expected_stdout="00042"),
)


# ---------------------------------------------------------------------------
# 341 — a `set -e` shell script that aborts on a missing command
# ---------------------------------------------------------------------------
# `run.sh` has `set -e`, so the missing `frobnicate` command aborts the script
# before the useful `echo`. The deliverable is the artifact out.txt; the gold
# writes it directly, so the verifier never depends on executing bash.
_RUN_SH_341 = """\
#!/usr/bin/env bash
set -e
frobnicate --init
echo "ready" > out.txt
"""

TASK_341 = Task(
    id="task_341_adv_set_e_abort",
    name="Recover the artifact from a set -e script that aborts early",
    tags=("adversarial", "shell", "build", "medium"),
    prompt=(
        "Скрипт run.sh должен создавать файл out.txt со строкой ready, но"
        " падает из-за `set -e` на несуществующей команде и до полезной строки"
        " не доходит. Добейся, чтобы out.txt со строкой ready появился"
        " (почини скрипт или получи результат другим способом)."
    ),
    setup_files={"run.sh": _RUN_SH_341},
    gold_files={"out.txt": "ready\n"},
    verifier=file_text_equals("out.txt", "ready"),
)


# ---------------------------------------------------------------------------
# 342 — documented `npm run build` in a project that is actually Python
# ---------------------------------------------------------------------------
# The README and package.json say to build with `npm run build`, but there is
# no Node toolchain and the real build tool is a Python generator. The agent
# must recognise the ecosystem mismatch and produce the artifact via generate.py
# (or reproduce its output) instead of chasing the npm instruction.
_GENERATE_342 = """\
items = ["a", "b", "c"]
with open("output.txt", "w") as f:
    for x in items:
        f.write("item:" + x + "\\n")
"""

_PACKAGE_JSON_342 = json.dumps(
    {"name": "widget", "scripts": {"build": "webpack --mode production"}}, indent=2
) + "\n"

_README_342 = "# Widget\n\nСборка проекта: `npm run build` — создаёт output.txt.\n"

TASK_342 = Task(
    id="task_342_adv_npm_in_python_project",
    name="Build via the real Python tool, not the documented npm command",
    tags=("adversarial", "build", "python", "medium"),
    prompt=(
        "Собери проект — на выходе должен получиться файл output.txt. В README"
        " написано собирать через `npm run build`, но в этом окружении это не"
        " работает. Разберись, чем проект собирается на самом деле, и получи"
        " правильный output.txt."
    ),
    setup_files={
        "generate.py": _GENERATE_342,
        "package.json": _PACKAGE_JSON_342,
        "README.md": _README_342,
    },
    gold_files={"output.txt": "item:a\nitem:b\nitem:c\n"},
    verifier=file_lines_equal("output.txt", ["item:a", "item:b", "item:c"]),
)


# ---------------------------------------------------------------------------
# 343 — a gzip stream masquerading behind a `.txt` extension
# ---------------------------------------------------------------------------
# `data.txt` is not text: it is a gzip stream (magic bytes 1f 8b). A naive text
# read yields mojibake / a decode error. The agent must detect the real format
# and decompress it to recover the value.
def _setup_343(ws: Path) -> None:
    (ws / "data.txt").write_bytes(gzip.compress(b"secret=PINE42\n"))


TASK_343 = Task(
    id="task_343_adv_gzip_masquerade",
    name="Decompress a gzip stream disguised as a .txt file",
    tags=("adversarial", "encoding", "data", "medium"),
    prompt=(
        "Файл data.txt на самом деле не текст — это сжатый gzip-поток, несмотря"
        " на расширение .txt. Внутри одна строка вида `secret=<значение>`."
        " Распакуй его, извлеки значение и запиши ТОЛЬКО значение в secret.txt."
    ),
    setup_callback=_setup_343,
    gold_files={"secret.txt": "PINE42\n"},
    verifier=file_text_equals("secret.txt", "PINE42"),
)


# ---------------------------------------------------------------------------
# 344 — a log polluted with a BOM and embedded NUL bytes
# ---------------------------------------------------------------------------
# The log opens with a UTF-8 BOM, uses CRLF line endings, and has a stray NUL
# byte inside one line. The agent must sanitize/decode it and extract the codes
# of the ERROR lines in order.
def _setup_344(ws: Path) -> None:
    raw = (
        b"\xef\xbb\xbf"  # UTF-8 BOM
        b"INFO service up\r\n"
        b"ERROR 500 db\x00 down\r\n"  # NUL byte mid-line
        b"WARN retry later\r\n"
        b"ERROR 503 upstream timeout\r\n"
        b"INFO done\r\n"
    )
    (ws / "app.log").write_bytes(raw)


TASK_344 = Task(
    id="task_344_adv_dirty_log_sanitize",
    name="Sanitize a BOM/NUL-polluted log and extract ERROR codes",
    tags=("adversarial", "encoding", "logs", "hard"),
    prompt=(
        "Файл app.log замусорен: в начале BOM, окончания строк CRLF, а внутри"
        " одной из строк встречается нулевой байт. Разбери лог и создай"
        " error_codes.txt: числовые коды строк уровня ERROR, по одному на"
        " строку, в порядке их следования в логе."
    ),
    setup_callback=_setup_344,
    gold_files={"error_codes.txt": "500\n503\n"},
    verifier=file_lines_equal("error_codes.txt", ["500", "503"]),
)


# ---------------------------------------------------------------------------
# 345 — an AGENTS.md that lies about the source layout
# ---------------------------------------------------------------------------
# AGENTS.md claims modules live in `src/`, but the real utils module is at
# `app/utils.py`. The agent must edit the module that actually exists rather
# than create an ignored `src/utils.py`.
_APP_UTILS_345 = """\
def triple(x):
    return x * 3
"""

_AGENTS_MD_345 = (
    "# Project conventions\n\n"
    "All Python modules live in `src/`. Add new helpers to `src/utils.py`.\n"
)

_APP_UTILS_GOLD_345 = """\
def triple(x):
    return x * 3


def double(x):
    return x * 2
"""

TASK_345 = Task(
    id="task_345_adv_agentsmd_wrong_layout",
    name="Edit the real module when AGENTS.md lies about the layout",
    tags=("adversarial", "python", "contradiction", "medium"),
    prompt=(
        "Добавь в модуль utils функцию double(x), которая возвращает x * 2."
        " Существующие функции не ломай. (Учти: описание раскладки проекта может"
        " не совпадать с тем, что реально лежит в директории.)"
    ),
    setup_files={"app/utils.py": _APP_UTILS_345, "AGENTS.md": _AGENTS_MD_345},
    gold_files={"app/utils.py": _APP_UTILS_GOLD_345},
    verifier=all_of(
        python_callable_returns("app/utils.py", "mod.double(21)", 42),
        python_callable_returns("app/utils.py", "mod.triple(2)", 6),
    ),
)


# ---------------------------------------------------------------------------
# 346 — the prompt names the wrong tests directory
# ---------------------------------------------------------------------------
# The README says tests live in `tests/`, and a trivially-passing placeholder
# there tempts an agent to declare victory. The real suite is in `spec/`, and it
# fails until a bug in calc.py is fixed. The environment, not the prompt, is the
# source of truth.
_CALC_346 = """\
def add(a, b):
    return a - b
"""

_SPEC_TEST_346 = """\
from calc import add


def test_add():
    assert add(2, 3) == 5
    assert add(10, 5) == 15
"""

_PLACEHOLDER_TEST_346 = """\
def test_placeholder():
    assert True
"""

_CALC_GOLD_346 = """\
def add(a, b):
    return a + b
"""

TASK_346 = Task(
    id="task_346_adv_wrong_tests_dir",
    name="Fix the failing suite in spec/ despite a tests/ misdirection",
    tags=("adversarial", "python", "pytest", "distractor", "hard"),
    prompt=(
        "В проекте падают тесты набора spec/. Почини calc.py так, чтобы все"
        " тесты в spec/ проходили. (В README упомянута папка tests/, но"
        " настоящий набор — в spec/.) Тесты менять нельзя."
    ),
    setup_files={
        "calc.py": _CALC_346,
        "spec/test_calc.py": _SPEC_TEST_346,
        "tests/test_placeholder.py": _PLACEHOLDER_TEST_346,
        "README.md": "# Calc\n\nTests live in `tests/`. Run `pytest tests/`.\n",
    },
    gold_files={"calc.py": _CALC_GOLD_346},
    verifier=pytest_passes("spec"),
)


# ---------------------------------------------------------------------------
# 347 — an import that points at a package path that does not exist
# ---------------------------------------------------------------------------
# `main.py` imports `from utils.helpers import calc`, but there is no `utils`
# package — `helpers.py` sits at the repository root. The agent must repoint the
# import at the module that actually exists.
_HELPERS_347 = """\
def calc(a, b):
    return a * b
"""

_MAIN_347 = """\
from utils.helpers import calc

print(calc(6, 7))
"""

_MAIN_GOLD_347 = """\
from helpers import calc

print(calc(6, 7))
"""

TASK_347 = Task(
    id="task_347_adv_broken_import_path",
    name="Repoint an import at the module that actually exists",
    tags=("adversarial", "python", "imports", "medium"),
    prompt=(
        "Скрипт main.py падает на импорте `from utils.helpers import calc`:"
        " пакета utils нет, а модуль helpers.py лежит в корне проекта. Почини"
        " импорт так, чтобы main.py запускался и печатал 42."
    ),
    setup_files={"helpers.py": _HELPERS_347, "main.py": _MAIN_347},
    gold_files={"main.py": _MAIN_GOLD_347},
    verifier=python_runs("main.py", expected_stdout="42"),
)


# ---------------------------------------------------------------------------
# 348 — a broken package layout: a submodule missing from its package dir
# ---------------------------------------------------------------------------
# `shapes/` is a real package (it has `__init__.py` and `square.py`), and the
# test imports `from shapes.circle import area` — but `circle.py` was left at the
# repo root instead of inside the package, so collection fails with
# ModuleNotFoundError. The agent must place the module in the package.
_SHAPES_SQUARE_348 = """\
def area(side):
    return side * side
"""

_CIRCLE_ROOT_348 = """\
import math


def area(radius):
    return round(math.pi * radius * radius, 2)
"""

_TEST_AREA_348 = """\
from shapes.circle import area


def test_circle_area():
    assert area(1) == 3.14
"""

TASK_348 = Task(
    id="task_348_adv_broken_package_layout",
    name="Place a submodule into the package so imports resolve",
    tags=("adversarial", "python", "packaging", "pytest", "hard"),
    prompt=(
        "Тест tests/test_area.py импортирует `from shapes.circle import area`,"
        " но сборка теста падает: модуль circle.py лежит не в пакете shapes/, а"
        " в корне проекта. Исправь раскладку пакета так, чтобы тесты в tests/"
        " проходили. Тест менять нельзя."
    ),
    setup_files={
        "shapes/__init__.py": "",
        "shapes/square.py": _SHAPES_SQUARE_348,
        "circle.py": _CIRCLE_ROOT_348,
        "tests/test_area.py": _TEST_AREA_348,
    },
    gold_files={"shapes/circle.py": _CIRCLE_ROOT_348},
    verifier=pytest_passes("tests"),
)


# ---------------------------------------------------------------------------
# 349 — a SKILL.md with malformed (unclosed) YAML frontmatter
# ---------------------------------------------------------------------------
# The frontmatter block is never closed with a second `---`, which can defeat
# skill parsing — but the body carries an unrecoverable fictional token. The
# agent must repair the frontmatter AND use the token. Shipped in both discovery
# dirs; the verifier checks the `.agents` copy (cross-harness standard).
_ORBIT_SKILL_BROKEN_349 = """\
---
name: orbit-config
description: Orbit Systems deployment config. Use when writing an Orbit config file.

# Orbit Systems — Config

Write the deployment config using this token.

ORBIT-KEY: QX-8842

Put the exact line `key=QX-8842` into the config file.
"""

_ORBIT_SKILL_FIXED_349 = """\
---
name: orbit-config
description: Orbit Systems deployment config. Use when writing an Orbit config file.
---

# Orbit Systems — Config

Write the deployment config using this token.

ORBIT-KEY: QX-8842

Put the exact line `key=QX-8842` into the config file.
"""

TASK_349 = Task(
    id="task_349_adv_malformed_skill_frontmatter",
    name="Repair a skill's broken frontmatter and use its token",
    tags=("adversarial", "skill", "yaml", "hard"),
    prompt=(
        "Скилл orbit-config сломан: в его SKILL.md не закрыт YAML-заголовок"
        " (нет второй строки `---`). Почини заголовок скилла, затем создай в"
        " корне файл config.txt строго по инструкции из этого скилла."
    ),
    setup_files={
        ".claude/skills/orbit-config/SKILL.md": _ORBIT_SKILL_BROKEN_349,
        ".agents/skills/orbit-config/SKILL.md": _ORBIT_SKILL_BROKEN_349,
    },
    gold_files={
        ".claude/skills/orbit-config/SKILL.md": _ORBIT_SKILL_FIXED_349,
        ".agents/skills/orbit-config/SKILL.md": _ORBIT_SKILL_FIXED_349,
        "config.txt": "key=QX-8842\n",
    },
    verifier=all_of(
        # frontmatter is now a closed `---` … `---` block
        file_matches_regex(
            ".agents/skills/orbit-config/SKILL.md",
            r"\A---\n.*?\n---\n",
            flags=re.DOTALL,
        ),
        file_contains("config.txt", "key=QX-8842"),
    ),
)


# ---------------------------------------------------------------------------
# 350 — two contradictory skills: one deprecated, one authoritative
# ---------------------------------------------------------------------------
# Both skills describe the Payonix brand color but disagree. The descriptions
# state which is deprecated and which is current; the agent must apply the
# authoritative token and avoid the decoy.
_PAYONIX_LEGACY_350 = """\
---
name: payonix-legacy
description: DEPRECATED legacy Payonix palette. Do NOT use for new work; kept
  only for reprinting old invoices. Superseded by payonix-current.
---

# Payonix palette (legacy)

Brand color: #OLD111
"""

_PAYONIX_CURRENT_350 = """\
---
name: payonix-current
description: Authoritative current Payonix brand palette. Use this for any new
  Payonix artifact.
---

# Payonix palette (current)

Brand color: #NEW999
"""

TASK_350 = Task(
    id="task_350_adv_contradictory_skills",
    name="Apply the authoritative skill and avoid the deprecated one",
    tags=("adversarial", "skill", "contradiction", "hard"),
    prompt=(
        "Создай в корне файл settings.txt со строкой `color=<hex>`, где hex —"
        " актуальный фирменный цвет Payonix. Учти: по Payonix есть два скилла и"
        " они противоречат друг другу — используй тот, что является актуальным,"
        " а не устаревший."
    ),
    setup_files={
        ".claude/skills/payonix-legacy/SKILL.md": _PAYONIX_LEGACY_350,
        ".claude/skills/payonix-current/SKILL.md": _PAYONIX_CURRENT_350,
        ".agents/skills/payonix-legacy/SKILL.md": _PAYONIX_LEGACY_350,
        ".agents/skills/payonix-current/SKILL.md": _PAYONIX_CURRENT_350,
    },
    gold_files={"settings.txt": "color=#NEW999\n"},
    verifier=all_of(
        file_contains("settings.txt", "#NEW999"),
        file_not_contains("settings.txt", "#OLD111"),
    ),
)


# ---------------------------------------------------------------------------
# 351 — a file too large to naively read into context
# ---------------------------------------------------------------------------
# events.log is generated at runtime at ~100 MB. An agent that tries to slurp
# the whole file into its context window will overflow its budget and fail to
# answer; the only tractable path is streaming / targeted tools (grep, wc,
# python) that read it in parts. Two required answers force BOTH behaviours:
#   * a needle: the single `MARKER token=<...>` line (targeted search) — the
#     token is a random constant, unrecoverable without scanning the file;
#   * an aggregate: the count of ERROR-level lines (a full streamed scan).
# The file is created via setup_callback (never committed to the repo). Gold
# writes only the tiny answer.json, so `apply_gold`/verify never touch the 100MB
# blob beyond generating it once.
_HUGE_TOKEN = "Z7Q-4F2A-KV91"
_HUGE_LINE_COUNT = 1_000_000
_HUGE_ERROR_EVERY = 1000
_HUGE_MARKER_LINE = 654_321
# ERROR on every 1000th line (i % 1000 == 0); the marker line index is not a
# multiple of 1000, so it never displaces an ERROR line. => exactly 1000 errors.
_HUGE_ERROR_COUNT = _HUGE_LINE_COUNT // _HUGE_ERROR_EVERY
_HUGE_FILLER = (
    "service=alpha region=eu-west-1 latency_ms=12 status=200 "
    "shard=0007 node=abcdef01 keepalive"
)


def _setup_351(ws: Path) -> None:
    path = ws / "events.log"
    chunk: list[str] = []
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for i in range(_HUGE_LINE_COUNT):
            if i == _HUGE_MARKER_LINE:
                chunk.append(f"{i:08d} MARKER token={_HUGE_TOKEN}")
            elif i % _HUGE_ERROR_EVERY == 0:
                chunk.append(f"{i:08d} ERROR {_HUGE_FILLER}")
            else:
                chunk.append(f"{i:08d} INFO {_HUGE_FILLER}")
            if len(chunk) >= 10_000:
                f.write("\n".join(chunk) + "\n")
                chunk.clear()
        if chunk:
            f.write("\n".join(chunk) + "\n")


TASK_351 = Task(
    id="task_351_adv_huge_file_no_slurp",
    name="Answer questions about a ~100MB log without reading it whole",
    tags=("adversarial", "scale", "logs", "streaming", "hard"),
    prompt=(
        "В рабочей директории лежит очень большой лог events.log (около 100 МБ)."
        " НЕ пытайся прочитать его целиком — это переполнит твой контекст."
        " Обработай его аккуратно: потоково, по частям или через команды/код"
        " (например grep, wc, python). Извлеки два факта:\n"
        "1) токен из единственной строки с MARKER (её формат:"
        " `MARKER token=<значение>`);\n"
        "2) количество строк уровня ERROR.\n"
        "Запиши ответ в answer.json в формате"
        ' {"token": <строка>, "error_count": <число>}.'
    ),
    setup_callback=_setup_351,
    gold_files={
        "answer.json": json.dumps(
            {"token": _HUGE_TOKEN, "error_count": _HUGE_ERROR_COUNT},
            ensure_ascii=False,
        )
    },
    verifier=json_file_has(
        "answer.json", token=_HUGE_TOKEN, error_count=_HUGE_ERROR_COUNT
    ),
)


ADVERSARIAL_TASKS: list[Task] = [
    TASK_331,
    TASK_332,
    TASK_333,
    TASK_334,
    TASK_335,
    TASK_336,
    TASK_337,
    TASK_338,
    TASK_339,
    TASK_340,
    TASK_341,
    TASK_342,
    TASK_343,
    TASK_344,
    TASK_345,
    TASK_346,
    TASK_347,
    TASK_348,
    TASK_349,
    TASK_350,
    TASK_351,
]
