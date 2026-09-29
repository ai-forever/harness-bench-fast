"""long_10_review_comments — внести правки по ветке ревью в проект биллинга ЖКХ.

Workspace: the ``kvartplata`` package (≈45 modules of a housing/utilities billing
system, stdlib only), its unittest suite ``tests/``, ``README.md``, ``CHANGELOG.md``
and ``REVIEW*.md`` — a two-round review thread of 123 items by four reviewers plus
the lead: change requests, requests retracted later (in the same or in a later
thread), requests postponed and reopened later, decisions of the lead in
conflicts, requests made inside replies, questions without a request, nits,
out-of-scope ideas, stale line numbers. The second round reviews four new
modules (quality, refunds, owners, reading_window).

The sources below carry change markers::

    # <<C07          # <<R02
    old code         old code
    # ==             # ==
    new code         code of a retracted request (never in gold)
    # >>             # >>

``setup`` renders the original project (all ``old`` parts), ``gold`` renders
every ``C`` change (the real patched project, visible tests updated). Hidden
tests: one pytest group per requested change (``test_cNN_*``) plus regression
tests of behaviour that must stay (including every retracted request). The
check also runs the agent's visible unittest suite.
"""

from __future__ import annotations

import functools
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .common import git, long_task, run_python, write

TASK_ID = "long_10_review_comments"
MIN_VISIBLE_TESTS = 209
ALLOWED_MISSES = 3
REVIEW_CUTS = ("### [43]", "## Второй круг", "### [118]")  # where REVIEW-2.md, REVIEW-3.md, ... start

_MARK = re.compile(r"^\s*# <<([CR]\d+)\s*$")
_LOC = re.compile(r"\{\{L:([^:}]+):(.+?)(?::([+-]\d+))?\}\}")


def render_marked(text: str, new_ids: set[str]) -> str:
    """Keep the ``old`` part of every marked block unless its id is in ``new_ids``."""
    out: list[str] = []
    state: str | None = None
    cid = ""
    for line in text.split("\n"):
        m = _MARK.match(line)
        if m:
            state, cid = "old", m.group(1)
            continue
        if state and line.strip() == "# ==":
            state = "new"
            continue
        if state and line.strip() == "# >>":
            state = None
            continue
        if state == "old" and cid in new_ids:
            continue
        if state == "new" and cid not in new_ids:
            continue
        out.append(line)
    return "\n".join(out)


@functools.cache
def change_ids() -> tuple[str, ...]:
    ids = set()
    for text in _FILES.values():
        ids.update(m.group(1) for m in map(_MARK.match, text.split("\n")) if m and m.group(1).startswith("C"))
    return tuple(sorted(ids))


@functools.cache
def retracted_ids() -> tuple[str, ...]:
    ids = set()
    for text in _FILES.values():
        ids.update(m.group(1) for m in map(_MARK.match, text.split("\n")) if m and m.group(1).startswith("R"))
    return tuple(sorted(ids))


def project(new_ids) -> dict[str, str]:
    new = set(new_ids)
    return {rel: render_marked(text, new) for rel, text in _FILES.items()}


def _resolve_review(files: dict[str, str]) -> str:
    """Replace ``{{L:path:regex[:+N]}}`` by line numbers; add the code hunk under each thread header."""

    def anchor(m: re.Match) -> tuple[list[str], int, int]:
        rel, pattern, shift = m.group(1), m.group(2), m.group(3)
        lines = files[rel].split("\n")
        rx = re.compile(pattern)
        for n, line in enumerate(lines, 1):
            if rx.search(line):
                return lines, n, n + int(shift or 0)
        raise AssertionError(f"review anchor not found: {rel}: {pattern}")

    out: list[str] = []
    for line in _REVIEW.split("\n"):
        m = _LOC.search(line)
        if not m:
            out.append(line)
            continue
        lines, true_n, shown_n = anchor(m)
        out.append(line[: m.start()] + str(shown_n) + line[m.end():])
        if line.startswith("### ["):
            lo, hi = max(1, true_n - 2), min(len(lines), true_n + 10)
            lang = "python" if m.group(1).endswith(".py") else ""
            out += ["", f"```{lang}", *lines[lo - 1: hi], "```"]
    return "\n".join(out)


@functools.cache
def original() -> dict[str, str]:
    files = project(())
    review = _resolve_review(files)
    cuts = [0] + [review.index(marker) for marker in REVIEW_CUTS] + [len(review)]
    parts = [review[a:b] for a, b in zip(cuts, cuts[1:], strict=False)]
    for i, part in enumerate(parts):
        name = "REVIEW.md" if i == 0 else f"REVIEW-{i + 1}.md"
        head = "" if i == 0 else f"# Ревью PR #412 (продолжение, часть {i + 1})\n\n"
        tail = f"*Продолжение ревью — в файле `REVIEW-{i + 2}.md`.*\n" if i + 1 < len(parts) else ""
        files[name] = head + part + tail
    return files


@functools.cache
def patched() -> dict[str, str]:
    return project(change_ids())


def setup(ws: Path) -> None:
    for rel, text in original().items():
        write(ws, rel, text)
    write(ws, ".gitignore", "__pycache__/\n*.pyc\n")
    git(ws, "init", "-q")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "PR #412: состояние ветки на момент ревью", date="2026-03-20T12:00:00+03:00",
        author="Игорь Тарасов <itarasov@sever-kvartal.example>")


def gold(ws: Path) -> None:
    for rel, text in patched().items():
        write(ws, rel, text)


# --------------------------------------------------------------------------
# Checking
# --------------------------------------------------------------------------


def _run_hidden(ws: Path) -> dict[str, bool]:
    """Run hidden pytest files; return {test_name: passed}."""
    tmp = Path(tempfile.mkdtemp(prefix="hb_t10_"))
    try:
        write(tmp, "test_hidden_changes.py", _HIDDEN_CHANGES)
        write(tmp, "test_hidden_regress.py", _HIDDEN_REGRESS)
        report = tmp / "report.xml"
        run_python(ws, ["-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp),
                        f"--junitxml={report}", str(tmp / "test_hidden_changes.py"),
                        str(tmp / "test_hidden_regress.py")], timeout=300)
        assert report.is_file(), "скрытые тесты не запустились"
        results: dict[str, bool] = {}
        for case in ET.parse(report).getroot().iter("testcase"):
            failed = any(child.tag in ("failure", "error") for child in case)
            skipped = any(child.tag == "skipped" for child in case)
            results[case.get("name", "")] = not failed and not skipped
        return results
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _visible_tests(ws: Path) -> tuple[bool, int, str]:
    proc = run_python(ws, ["-m", "unittest", "discover", "-s", "tests", "-t", "."], timeout=300)
    out = proc.stdout + proc.stderr
    m = re.search(r"Ran (\d+) tests?", out)
    ran = int(m.group(1)) if m else 0
    lines = out.strip().splitlines()
    ok = proc.returncode == 0 and bool(lines) and lines[-1].startswith("OK")
    return ok, ran, out[-600:]


def check(ws: Path) -> str:
    assert (ws / "kvartplata").is_dir(), "нет пакета kvartplata"
    ok, ran, _tail = _visible_tests(ws)
    assert ran >= MIN_VISIBLE_TESTS, f"видимых тестов запущено {ran}, ожидалось не меньше {MIN_VISIBLE_TESTS}"
    assert ok, "видимые тесты (python3 -m unittest) не проходят"
    results = _run_hidden(ws)
    regress = {k: v for k, v in results.items() if not re.match(r"test_c\d+_", k)}
    assert regress, "скрытые регрессионные тесты не найдены"
    broken = sorted(k for k, v in regress.items() if not v)
    assert not broken, f"сломано поведение, которое не должно было меняться: {len(broken)} тест(ов)"
    groups: dict[str, bool] = {}
    for name, passed in results.items():
        m = re.match(r"test_(c\d+)_", name)
        if m:
            groups[m.group(1)] = groups.get(m.group(1), True) and passed
    total = len(change_ids())
    done = sum(groups.values())
    assert len(groups) == total, "не все группы скрытых тестов выполнились"
    note = f"правок внесено {done}/{total}, регрессия {len(regress)}/{len(regress)}, видимых тестов {ran}"
    assert done >= total - ALLOWED_MISSES, f"правок внесено {done}/{total}, нужно не меньше {total - ALLOWED_MISSES}"
    return note


# --------------------------------------------------------------------------
# Near misses
# --------------------------------------------------------------------------


def _write_project(ws: Path, new_ids, *, keep_tests_from=None) -> None:
    files = project(new_ids)
    for rel, text in files.items():
        if keep_tests_from is not None and rel.startswith("tests/"):
            text = keep_tests_from[rel]
        write(ws, rel, text)


def apply_retracted_too(ws: Path) -> None:
    """Applies every request literally, including the retracted ones."""
    _write_project(ws, (*change_ids(), *retracted_ids()))


def skip_conflicting_threads(ws: Path) -> None:
    """Leaves out the changes where reviewers disagreed and the lead decided."""
    _write_project(ws, [c for c in change_ids() if c not in ("C03", "C24", "C31", "C42", "C44")])


def miss_requests_in_replies(ws: Path) -> None:
    """Misses requests that appear only in replies / as a second request in a thread."""
    _write_project(ws, [c for c in change_ids() if c not in ("C13", "C21", "C27", "C45", "C49", "C53")])


def forget_visible_tests(ws: Path) -> None:
    """Patches the code but leaves the visible tests pinned to the old behaviour."""
    _write_project(ws, change_ids(), keep_tests_from=project(()))


def also_change_gis_format(ws: Path) -> None:
    """Also switches the fixed GIS export to ';' (thread [65] says not to)."""
    gold(ws)
    path = ws / "kvartplata" / "gis_export.py"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'(f"(?:#GIS|D|S)\|[^"]*")', lambda m: m.group(1).replace("|", ";"), text)
    path.write_text(text, encoding="utf-8")


def trust_first_answer(ws: Path) -> None:
    """Reads each second-round thread alone: keeps [89]/[104]/[126] requests, skips [113] (reopened in [116])."""
    _write_project(ws, [*(c for c in change_ids() if c != "C49"), "R06", "R08", "R09"])


def only_first_round(ws: Path) -> None:
    """Stops after the first round (REVIEW.md and REVIEW-2.md)."""
    _write_project(ws, [c for c in change_ids() if int(c[1:]) <= 35])


NEAR_MISSES = [apply_retracted_too, skip_conflicting_threads, miss_requests_in_replies, forget_visible_tests,
               also_change_gis_format, trust_first_answer, only_first_round]

PROMPT = """В рабочей папке — git-репозиторий проекта `kvartplata` (Python, только стандартная библиотека):
расчёт квартплаты — начисления по услугам, льготы, перерасчёты, пени, квитанции, выгрузки, отчёты.
В файлах `REVIEW.md`, `REVIEW-2.md`, `REVIEW-3.md` и `REVIEW-4.md` (продолжение) — ветка код-ревью этого PR
в два круга: комментарии четырёх ревьюеров и ведущего разработчика с ответами. Твоя задача — довести PR по итогам ревью: внести в код все изменения, которые по итогам
обсуждения нужно сделать, и не вносить то, что делать не нужно.

Как читать ревью (эти правила продублированы в начале `REVIEW.md`):
- замечания с пометкой «nit» или «по желанию» — необязательны; вопросы без просьбы что-то изменить —
  просто вопросы;
- если автор замечания сам его снял или ведущий разработчик (Анна Ковалёва) написала, что не делаем,
  — не делаем; если ревьюеры спорят, делаем так, как решила ведущий разработчик;
- всё остальное — обязательные изменения поведения;
- номера строк в части комментариев устарели — ориентируйся на имена функций и описание.

Требования:
- публичные имена, сигнатуры и форматы, о которых ревью ничего не говорит, не меняй — проект
  используют другие команды;
- видимые тесты (`python3 -m unittest` из корня проекта) должны проходить; тесты, которые фиксируют
  поведение, изменённое по ревью, обнови под новое поведение (не удаляй их);
- файлы `REVIEW*.md` не редактируй.

Ревью длинное, а правки разбросаны по всему проекту: учти каждый тред.
"""

TASK = long_task(
    id="task_401_review_comments",  # registry id; TASK_ID stays the generator seed
    name="Внести правки по ветке ревью в проект биллинга ЖКХ",
    prompt=PROMPT,
    setup=setup,
    gold=gold,
    check=check,
    tags=("code-review", "python", "refactoring", "reading"),
)

# --------------------------------------------------------------------------
# Data (generated from the reference project; see the module docstring)
# --------------------------------------------------------------------------
_FILES: dict[str, str] = {}

_FILES['CHANGELOG.md'] = r'''# Changelog

## 2.4.0 (в работе, PR #412)
- Весенние исправления расчётов по итогам сверки с расчётным отделом (см. ревью PR #412).
- Добавлены модули `court.py`, `debt_collection.py`, `anomalies.py`, `gis_export.py`,
  `privileges_report.py`, `receipt_html.py`, `settings_io.py`, `indexation.py`.
- KV-231…KV-235: модули `quality.py`, `refunds.py`, `owners.py`, `reading_window.py`, `closing.py`.

## 2.3.2 (2026-01-20)
- Тарифы на 2026 год, ключевая ставка 16 % с 22.12.2025 и 15,5 % с 16.02.2026.
- Праздники 2026 года в производственном календаре.

## 2.3.0 (2025-10-01)
- Рассрочка задолженности (`installments.py`), уведомления должникам (`notices.py`).
- Импорт реестров платежей банков.

## 2.2.0 (2025-07-01)
- Двухтарифный учёт электроэнергии.
- Двухкомпонентный тариф на ГВС (теплоноситель + тепловая энергия).

## 2.1.0 (2025-03-15)
- Льготы и отчёт о должниках.
'''

_FILES['README.md'] = r'''# kvartplata

Расчёт платы за жилищно-коммунальные услуги для управляющей компании
«УК Северный квартал»: начисления по приборам учёта и нормативам, льготы, перерасчёты,
пени, платёжные документы (текст, HTML, QR по ГОСТ Р 56042-2014), выгрузки для бухгалтерии
и ГИС ЖКХ, реестры банков, отчёты для контролёров, юристов и органов соцзащиты.

Требования: Python 3.9+, только стандартная библиотека.

## Быстрый старт

```bash
python3 -m unittest                      # тесты
python3 -m kvartplata bill --data demo.json --account 12345678905 --period 2026-03
python3 -m kvartplata export --data demo.json --period 2026-03 > charges.csv
```

## Устройство

| Модуль | Назначение |
|---|---|
| `money.py` | денежная арифметика на `Decimal`, округление, деление сумм, форматирование |
| `periods.py`, `calendar_ru.py` | расчётные периоды, производственный календарь |
| `models.py`, `config.py` | модели данных и настройки расчёта |
| `tariffs.py`, `normatives.py`, `meters.py` | тарифы, нормативы, определение объёма потребления |
| `services/*.py` | калькуляторы услуг (ХВС, ГВС, водоотведение, электроэнергия, отопление, газ, содержание, капремонт, ТКО) |
| `privileges.py`, `recalc.py` | льготы и перерасчёт за временное отсутствие |
| `penalties.py`, `payments.py`, `ledger.py` | пени, разнесение платежей, сальдо лицевого счёта |
| `billing.py` | сборка платёжного документа за период |
| `receipt.py`, `receipt_html.py`, `words.py` | квитанции и сумма прописью |
| `export.py`, `gis_export.py`, `importer.py`, `bank_registry.py` | выгрузки и загрузки |
| `reports.py`, `anomalies.py`, `privileges_report.py`, `reconciliation.py` | отчёты |
| `notices.py`, `debt_collection.py`, `court.py`, `installments.py` | работа с задолженностью |
| `subsidies.py`, `house.py`, `verification.py`, `indexation.py` | субсидии, ОДН, поверка, индексация |
| `validators.py`, `people.py`, `addresses.py` | реквизиты, ФИО, адреса |
| `storage.py`, `settings_io.py`, `audit.py`, `cli.py` | хранение, настройки, журнал, командная строка |
| `quality.py`, `refunds.py`, `owners.py`, `reading_window.py`, `closing.py` | снижение платы за перерывы, возврат переплаты, раздел платы между долевыми собственниками, окно приёма показаний, закрытие счёта |

## Порядок расчёта документа

1. Для каждой услуги калькулятор из `services/` определяет объём (прибор учёта → среднее →
   норматив) и сумму по тарифу на первое число периода.
2. `privileges.apply_privileges` проставляет скидки по льготам.
3. `recalc.recalculate` делает перерасчёт за временное отсутствие жильцов.
4. `billing.compute_bill` добавляет входящее сальдо и пени по долгам прошлых периодов.

## Соглашения

* Все суммы — `Decimal`, округление до копеек — только функциями из `money.py`.
* Публичные функции модулей используются другими командами (личный кабинет, отчёты) —
  сигнатуры не меняем без согласования.
* Тесты — `unittest`, запуск из корня проекта: `python3 -m unittest`.
'''

_FILES['kvartplata/__init__.py'] = r'''"""kvartplata — расчёт платы за жилищно-коммунальные услуги.

Основные точки входа:

* :func:`kvartplata.billing.compute_bill` — платёжный документ за период;
* :func:`kvartplata.receipt.render` — текстовая квитанция;
* :mod:`kvartplata.cli` — командная строка (``python -m kvartplata``).
"""

__version__ = "2.4.0"
'''

_FILES['kvartplata/__main__.py'] = r'''from .cli import main

raise SystemExit(main())
'''

_FILES['kvartplata/addresses.py'] = r'''"""Нормализация адресов для поиска и сверки.

Приводит сокращения к единому виду («улица» → «ул.», «дом» → «д.», «квартира» → «кв.»),
убирает лишние пробелы и запятые, выделяет номер квартиры.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REPLACEMENTS = [
    (r"\bулица\b", "ул."), (r"\bул\b\.?", "ул."), (r"\bпроспект\b", "пр-т"), (r"\bпр\.", "пр-т"),
    (r"\bпереулок\b", "пер."), (r"\bшоссе\b", "ш."), (r"\bнабережная\b", "наб."),
    (r"\bдом\b", "д."), (r"\bд\b\.?", "д."), (r"\bкорпус\b", "корп."), (r"\bкорп\b\.?", "корп."),
    (r"\bстроение\b", "стр."), (r"\bквартира\b", "кв."), (r"\bкв\b\.?", "кв."),
]


@dataclass
class Address:
    street: str
    house: str
    flat: str = ""

    def __str__(self) -> str:
        text = f"{self.street}, д. {self.house}"
        return text + (f", кв. {self.flat}" if self.flat else "")


def normalize(text: str) -> str:
    value = " " + text.strip().lower() + " "
    for pattern, repl in REPLACEMENTS:
        value = re.sub(pattern, repl, value)
    value = re.sub(r"\.\.+", ".", value)
    value = re.sub(r"\s*,\s*", ", ", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"(ул\.|д\.|кв\.|пер\.|ш\.|наб\.|корп\.|стр\.)(?=\S)", r"\1 ", value)
    return value.strip(" ,")


def parse(text: str) -> Address:
    norm = normalize(text)
    m = re.match(r"^(?P<street>.+?),? д\. (?P<house>[\w/-]+(?: корп\. \w+)?)(?:, кв\. (?P<flat>\w+))?$", norm)
    if not m:
        raise ValueError(f"не удалось разобрать адрес: {text!r}")
    return Address(m.group("street").strip(", "), m.group("house"), m.group("flat") or "")


def same_address(a: str, b: str) -> bool:
    return normalize(a) == normalize(b)
'''

_FILES['kvartplata/anomalies.py'] = r'''"""Поиск аномалий в показаниях.

Правила (каждое даёт отдельный флаг):

* ``spike`` — потребление за период больше среднемесячного в ``spike_factor`` раз
  (среднее — по предыдущим показаниям, см. ``ReadingBook.average_monthly``);
* ``zero`` — нулевое потребление при зарегистрированных жильцах три периода подряд;
* ``same_value`` — показание в точности равно показанию предыдущего периода при наличии жильцов
  (частный случай ``zero`` для одного периода, выводится отдельно для обзвона);
* ``hot_gt_cold`` — горячей воды израсходовано больше, чем холодной, более чем в 3 раза.

Флаги используются только в отчётах для контролёров и на начисления не влияют.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .config import Settings
from .meters import ReadingBook
from .models import Account
from .periods import Period


@dataclass
class Flag:
    account: str
    meter_id: str
    period: Period
    kind: str
    details: str


def meter_flags(account: Account, period: Period, book: ReadingBook, settings: Settings,
                spike_factor: Decimal = Decimal(3)) -> list[Flag]:
    flags = []
    for meter in account.meters:
        cur = book.monthly_consumption(meter, period)
        if cur is None:
            continue
        avg = book.average_monthly(meter, period, settings)
        if avg and avg > 0 and cur > avg * spike_factor:
            flags.append(Flag(account.number, meter.meter_id, period, "spike",
                              f"{cur} при среднем {avg}"))
        if account.registered_count and cur == 0:
            flags.append(Flag(account.number, meter.meter_id, period, "same_value", "показание не изменилось"))
            zeros = 1
            p = period.prev()
            while zeros < 3:
                prev = book.monthly_consumption(meter, p)
                if prev is None or prev != 0:
                    break
                zeros += 1
                p = p.prev()
            if zeros >= 3:
                flags.append(Flag(account.number, meter.meter_id, period, "zero", "3 периода без потребления"))
    return flags


def water_balance_flag(account: Account, period: Period, book: ReadingBook) -> Flag | None:
    cold = [book.monthly_consumption(m, period) for m in account.meters_for("cold_water")]
    hot = [book.monthly_consumption(m, period) for m in account.meters_for("hot_water")]
    if not cold or not hot or any(v is None for v in cold + hot):
        return None
    c, h = sum(cold, Decimal(0)), sum(hot, Decimal(0))
    if c > 0 and h > c * 3:
        return Flag(account.number, "*", period, "hot_gt_cold", f"ГВС {h} м³ при ХВС {c} м³")
    return None


def scan(accounts: list[Account], period: Period, book: ReadingBook, settings: Settings) -> list[Flag]:
    out = []
    for acc in accounts:
        out.extend(meter_flags(acc, period, book, settings))
        flag = water_balance_flag(acc, period, book)
        if flag:
            out.append(flag)
    out.sort(key=lambda f: (f.account, f.meter_id, f.kind))
    return out


def render(flags: list[Flag]) -> str:
    titles = {"spike": "Резкий рост", "zero": "Нет потребления", "same_value": "Показание не менялось",
              "hot_gt_cold": "ГВС ≫ ХВС"}
    lines = [f"{'Л/с':<13}{'Прибор':<10}{'Период':<9}{'Признак':<24}Подробности"]
    for f in flags:
        lines.append(f"{f.account:<13}{f.meter_id:<10}{str(f.period):<9}{titles.get(f.kind, f.kind):<24}{f.details}")
    return "\n".join(lines) + "\n"
'''

_FILES['kvartplata/audit.py'] = r'''"""Журнал событий расчёта (JSON Lines)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Event:
    kind: str  # bill | payment | import | recalc | notice
    account: str
    details: dict = field(default_factory=dict)
    at: str = ""


class AuditLog:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.events: list[Event] = []

    def record(self, kind: str, account: str, clock=datetime.now, **details) -> Event:
        event = Event(kind, account, {k: str(v) for k, v in details.items()}, clock().isoformat(timespec="seconds"))
        self.events.append(event)
        if self.path:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        return event

    def for_account(self, account: str) -> list[Event]:
        return [e for e in self.events if e.account == account]

    @staticmethod
    def read(path: str | Path) -> list[Event]:
        out = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(Event(**json.loads(line)))
        return out
'''

_FILES['kvartplata/bank_registry.py'] = r'''"""Разбор реестров платежей от банков.

Реестр — текстовый файл в кодировке UTF-8, по одной операции в строке::

    #REESTR;<банк>;<дата реестра YYYY-MM-DD>
    <дата платежа DD.MM.YYYY>;<лицевой счёт>;<сумма>;<плательщик>;<номер операции>
    ...
    =;<число операций>;<общая сумма>

Сумма — с точкой или запятой. Контрольная строка ``=`` обязательна: число
операций и общая сумма должны совпадать с данными строк, иначе реестр
отклоняется целиком. Отдельные ошибочные строки (не тот формат, неизвестный
лицевой счёт) пропускаются и попадают в список ошибок.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from .models import Payment
from .money import round_money
from .validators import is_valid_account


class RegistryError(ValueError):
    pass


@dataclass
class Registry:
    bank: str
    registry_date: date
    payments: list[Payment] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return round_money(sum((p.amount for p in self.payments), Decimal(0)))


def _amount(raw: str) -> Decimal:
    try:
        return round_money(Decimal(raw.strip().replace(",", ".")))
    except InvalidOperation as exc:
        raise ValueError(f"некорректная сумма {raw!r}") from exc


def _date(raw: str) -> date:
    d, m, y = raw.strip().split(".")
    return date(int(y), int(m), int(d))


def parse_registry(text: str, known_accounts: set[str] | None = None,
                   check_digits: bool = True) -> Registry:
    lines = [ln.rstrip("\r") for ln in text.split("\n") if ln.strip()]
    if not lines or not lines[0].startswith("#REESTR;"):
        raise RegistryError("нет заголовка реестра")
    _tag, bank, reg_date = lines[0].split(";")[:3]
    registry = Registry(bank.strip(), date.fromisoformat(reg_date.strip()))
    control = None
    count_rows = 0
    sum_rows = Decimal(0)
    for n, line in enumerate(lines[1:], start=2):
        parts = line.split(";")
        if parts[0] == "=":
            if len(parts) < 3:
                raise RegistryError("некорректная контрольная строка")
            control = (int(parts[1]), _amount(parts[2]))
            continue
        if len(parts) < 5:
            registry.errors.append(f"строка {n}: ожидается 5 полей")
            continue
        try:
            paid_on = _date(parts[0])
            amount = _amount(parts[2])
        except ValueError as exc:
            registry.errors.append(f"строка {n}: {exc}")
            continue
        count_rows += 1
        sum_rows += amount
        account = parts[1].strip()
        if check_digits and not is_valid_account(account):
            registry.errors.append(f"строка {n}: некорректный номер лицевого счёта {account}")
            continue
        if known_accounts is not None and account not in known_accounts:
            registry.errors.append(f"строка {n}: неизвестный лицевой счёт {account}")
            continue
        registry.payments.append(Payment(account, paid_on, amount, parts[4].strip()))
    if control is None:
        raise RegistryError("нет контрольной строки")
    if control != (count_rows, round_money(sum_rows)):
        raise RegistryError(f"контрольные суммы не сходятся: {control} != {(count_rows, round_money(sum_rows))}")
    return registry
'''

_FILES['kvartplata/billing.py'] = r'''"""Расчёт платёжного документа за период.

``compute_bill`` собирает начисления по всем услугам, применяет льготы
и перерасчёты, добавляет входящее сальдо и пени и возвращает :class:`Bill`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .calendar_ru import next_workday
from .config import DEFAULT, Settings
from .ledger import Ledger
from .meters import ReadingBook
from .models import Absence, Account, Charge
from .money import round_money
from .penalties import PenaltyLine, penalty_lines, total_penalty
from .periods import Period
from .privileges import apply_privileges
from .recalc import recalculate
from .services import CALCULATORS
from .services.base import Context
from .tariffs import TariffTable


@dataclass
class Bill:
    account: Account
    period: Period
    charges: list[Charge]
    incoming: Decimal = Decimal("0.00")
    penalties: list[PenaltyLine] = field(default_factory=list)
    due: date | None = None

    @property
    def charged(self) -> Decimal:
        return round_money(sum((c.total for c in self.charges), Decimal(0)))

    @property
    def penalty(self) -> Decimal:
        return total_penalty(self.penalties)

    @property
    def to_pay(self) -> Decimal:
        return round_money(self.incoming + self.charged + self.penalty)


def due_date(period: Period, settings: Settings = DEFAULT) -> date:
    """Срок оплаты за ``period`` — ``due_day``-е число следующего месяца."""
    nxt = period.next()
    day = date(nxt.year, nxt.month, settings.due_day)
    # <<C07
    return day
    # ==
    # если срок выпадает на выходной или праздник — переносится на рабочий день
    return next_workday(day)
    # >>


def compute_charges(account: Account, period: Period, book: ReadingBook, tariffs: TariffTable,
                    settings: Settings = DEFAULT, absences: list[Absence] | None = None) -> list[Charge]:
    ctx = Context(account, period, book, tariffs, settings)
    charges: list[Charge] = []
    for _service, calc in CALCULATORS:
        charges.extend(calc(ctx))
    apply_privileges(charges, account, settings)
    recalculate(charges, account, period, absences or [], settings)
    return charges


def compute_bill(account: Account, period: Period, book: ReadingBook, tariffs: TariffTable,
                 ledger: Ledger | None = None, settings: Settings = DEFAULT,
                 absences: list[Absence] | None = None, on: date | None = None) -> Bill:
    charges = compute_charges(account, period, book, tariffs, settings, absences)
    bill = Bill(account, period, charges, due=due_date(period, settings))
    if ledger is not None:
        prev = period.prev()
        on = on or period.first_day
        bill.incoming = ledger.balance(prev, on)
        debts = [(str(p), amount, due_date(p, settings)) for p, amount in ledger.debts(prev, on)]
        bill.penalties = [line for line in penalty_lines(debts, on, settings) if line.amount > 0]
    return bill
'''

_FILES['kvartplata/calendar_ru.py'] = r'''"""Производственный календарь (упрощённый).

Праздничные и перенесённые выходные дни на 2025–2026 годы перечислены явно.
Рабочие субботы (переносы) не поддерживаются — в биллинге они не нужны:
календарь используется только для сдвига сроков оплаты и выгрузок.
"""

from __future__ import annotations

from datetime import date, timedelta

HOLIDAYS: frozenset[date] = frozenset(
    [date(2025, 1, d) for d in range(1, 9)]
    + [date(2025, 5, 1), date(2025, 5, 2), date(2025, 5, 8), date(2025, 5, 9), date(2025, 6, 12),
       date(2025, 6, 13), date(2025, 11, 3), date(2025, 11, 4), date(2025, 12, 31)]
    + [date(2026, 1, d) for d in range(1, 10)]
    + [date(2026, 2, 23), date(2026, 3, 9), date(2026, 5, 1), date(2026, 5, 11), date(2026, 6, 12),
       date(2026, 11, 4), date(2026, 12, 31)]
)


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def is_holiday(day: date) -> bool:
    return day in HOLIDAYS


def is_workday(day: date) -> bool:
    return not is_weekend(day) and not is_holiday(day)


def next_workday(day: date) -> date:
    """Ближайший рабочий день, начиная с ``day`` (сам ``day`` подходит)."""
    # <<C06
    while is_weekend(day):
        day += timedelta(days=1)
    # ==
    while not is_workday(day):
        day += timedelta(days=1)
    # >>
    return day


def add_workdays(day: date, count: int) -> date:
    """Прибавить ``count`` рабочих дней (``count`` >= 0)."""
    if count < 0:
        raise ValueError("count должен быть неотрицательным")
    while count:
        day += timedelta(days=1)
        if is_workday(day):
            count -= 1
    return day


def workdays_between(start: date, end: date) -> int:
    """Число рабочих дней в полуинтервале (start, end]."""
    n = 0
    day = start
    while day < end:
        day += timedelta(days=1)
        if is_workday(day):
            n += 1
    return n
'''

_FILES['kvartplata/cli.py'] = r'''"""Командная строка.

Примеры::

    python -m kvartplata bill --data demo.json --account 12345678905 --period 2026-03
    python -m kvartplata export --data demo.json --period 2026-03 > charges.csv
    python -m kvartplata import-readings --data demo.json readings.csv
    python -m kvartplata debtors --data demo.json --period 2026-03 --on 2026-04-15
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from . import export, receipt, reports
from .billing import compute_bill
from .config import DEFAULT
from .importer import import_readings
from .ledger import Ledger
from .meters import ReadingBook
from .periods import Period
from .storage import Store
from .tariffs import default_tariffs


def _ledgers(store: Store, bills_until: Period) -> dict[str, Ledger]:
    ledgers = {n: Ledger(n) for n in store.accounts}
    for p in store.payments:
        if p.account in ledgers:
            ledgers[p.account].add_payment(p)
    return ledgers


def cmd_bill(args) -> int:
    store = Store.load(args.data)
    acc = store.accounts.get(args.account)
    if acc is None:
        print(f"нет лицевого счёта {args.account}", file=sys.stderr)
        return 2
    period = Period.parse(args.period)
    bill = compute_bill(acc, period, ReadingBook(store.readings), default_tariffs(), settings=DEFAULT,
                        absences=store.absences.get(acc.number, []))
    sys.stdout.write(receipt.render(bill))
    return 0


def cmd_export(args) -> int:
    store = Store.load(args.data)
    period = Period.parse(args.period)
    book = ReadingBook(store.readings)
    bills = [compute_bill(a, period, book, default_tariffs(), absences=store.absences.get(a.number, []))
             for a in store.accounts.values()]
    sys.stdout.write(export.export_bills(bills))
    return 0


def cmd_import(args) -> int:
    store = Store.load(args.data)
    with open(args.csv, encoding="utf-8") as fh:
        result = import_readings(fh.read())
    store.readings.extend(result.readings)
    store.save(args.data)
    print(f"импортировано: {len(result.readings)}, ошибок: {len(result.errors)}, дублей: {result.duplicates}")
    for err in result.errors:
        print("  " + err)
    return 0 if not result.errors else 1


def cmd_debtors(args) -> int:
    store = Store.load(args.data)
    period = Period.parse(args.period)
    ledgers = _ledgers(store, period)
    rows = reports.debtors(list(ledgers.values()), period, date.fromisoformat(args.on), args.min_months)
    sys.stdout.write(reports.render_debtors(rows))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kvartplata", description="Расчёт квартплаты")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("bill", help="квитанция по лицевому счёту")
    p.add_argument("--data", required=True)
    p.add_argument("--account", required=True)
    p.add_argument("--period", required=True)
    p.set_defaults(func=cmd_bill)
    p = sub.add_parser("export", help="выгрузка начислений в CSV")
    p.add_argument("--data", required=True)
    p.add_argument("--period", required=True)
    p.set_defaults(func=cmd_export)
    p = sub.add_parser("import-readings", help="импорт показаний из CSV")
    p.add_argument("--data", required=True)
    p.add_argument("csv")
    p.set_defaults(func=cmd_import)
    p = sub.add_parser("debtors", help="список должников")
    p.add_argument("--data", required=True)
    p.add_argument("--period", required=True)
    p.add_argument("--on", required=True)
    p.add_argument("--min-months", type=int, default=3)
    p.set_defaults(func=cmd_debtors)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
'''

_FILES['kvartplata/config.py'] = r'''"""Настройки расчёта.

Значения по умолчанию соответствуют регламенту управляющей компании
«УК Северный квартал» на 2026 год. Все денежные ставки — в рублях.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


def _normatives() -> dict[str, Decimal]:
    # Нормативы потребления на одного зарегистрированного человека в месяц.
    return {
        "cold_water": Decimal("4.745"),  # м³
        "hot_water": Decimal("3.500"),  # м³
        "electricity": Decimal("87"),  # кВт·ч (газовая плита)
        "electricity_stove": Decimal("121"),  # кВт·ч (электроплита)
        "gas": Decimal("10.2"),  # м³
    }


def _key_rates() -> list[tuple[date, Decimal]]:
    # Ключевая ставка ЦБ (дата начала действия, процент годовых).
    return [
        (date(2025, 6, 9), Decimal("20.0")),
        (date(2025, 7, 28), Decimal("18.0")),
        (date(2025, 9, 15), Decimal("17.0")),
        (date(2025, 10, 27), Decimal("16.5")),
        (date(2025, 12, 22), Decimal("16.0")),
        (date(2026, 2, 16), Decimal("15.5")),
    ]


@dataclass
class Settings:
    # Нормативы и коэффициенты
    normatives: dict[str, Decimal] = field(default_factory=_normatives)
    no_meter_coefficient: Decimal = Decimal("1.5")
    heating_norm_per_m2: Decimal = Decimal("0.0187")  # Гкал на м² в месяц (равномерно)
    hot_water_heat_norm: Decimal = Decimal("0.0612")  # Гкал на подогрев 1 м³
    # Ставки, не зависящие от тарифной таблицы
    maintenance_rate: Decimal = Decimal("32.40")  # руб./м²
    capital_repair_rate: Decimal = Decimal("14.84")  # руб./м²
    waste_rate_per_person: Decimal = Decimal("148.62")  # руб./чел.
    # Льготы
    social_norm_area_per_person: Decimal = Decimal("18")  # м²
    social_norm_area_single: Decimal = Decimal("33")  # м² для одиноко проживающего
    # Показания
    average_window_months: int = 6
    average_max_periods: int = 3
    # Пени
    key_rates: list[tuple[date, Decimal]] = field(default_factory=_key_rates)
    penalty_grace_days: int = 30
    penalty_high_rate_from_day: int = 91
    due_day: int = 10
    # Перерасчёт
    absence_min_days: int = 5
    # Прочее
    company_name: str = "ООО «УК Северный квартал»"
    company_inn: str = "7801234565"
    bank_account: str = "40702810900000012345"
    bik: str = "044030653"

    def normative(self, service: str, *, electric_stove: bool = False) -> Decimal:
        if service == "electricity" and electric_stove:
            return self.normatives["electricity_stove"]
        return self.normatives[service]

    def key_rate_on(self, day: date) -> Decimal:
        rate = self.key_rates[0][1]
        for start, value in self.key_rates:
            if start <= day:
                rate = value
        return rate


DEFAULT = Settings()
'''

_FILES['kvartplata/corrections.py'] = r'''"""Разовые корректировки начислений.

Корректировка — ручное начисление или сторно по услуге за период с обязательным
основанием (номер обращения, акта, решения суда). Корректировки на сумму больше
``APPROVAL_LIMIT`` требуют подтверждения вторым сотрудником (принцип «четырёх глаз»);
неподтверждённые корректировки в расчёт не попадают.

Корректировка закрытого периода (см. :mod:`periods_lock`) запрещена — её нужно
провести в текущем периоде с указанием исходного.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .models import SERVICES, Charge
from .money import round_money, to_money
from .periods import Period

APPROVAL_LIMIT = Decimal("5000")
REASON_PREFIXES = ("ОБР-", "АКТ-", "СУД-", "ПРЕТ-")


class CorrectionError(ValueError):
    pass


@dataclass
class Correction:
    account: str
    period: Period
    service: str
    amount: Decimal  # положительная — доначисление, отрицательная — сторно
    reason: str
    author: str
    created: date
    approved_by: str = ""
    source_period: Period | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def needs_approval(self) -> bool:
        return abs(self.amount) > APPROVAL_LIMIT

    @property
    def effective(self) -> bool:
        return not self.needs_approval or bool(self.approved_by)


def create(account: str, period: Period, service: str, amount, reason: str, author: str, created: date,
           source_period: Period | None = None) -> Correction:
    if service not in SERVICES:
        raise CorrectionError(f"неизвестная услуга {service!r}")
    value = round_money(to_money(amount))
    if value == 0:
        raise CorrectionError("нулевая корректировка")
    reason = reason.strip()
    if not reason.startswith(REASON_PREFIXES):
        raise CorrectionError("основание должно начинаться с ОБР-, АКТ-, СУД- или ПРЕТ-")
    if source_period is not None and source_period >= period:
        raise CorrectionError("исходный период должен быть раньше периода проведения")
    return Correction(account, period, service, value, reason, author, created, source_period=source_period)


def approve(correction: Correction, approver: str) -> None:
    if not correction.needs_approval:
        raise CorrectionError("корректировка не требует подтверждения")
    if approver == correction.author:
        raise CorrectionError("подтверждать должен другой сотрудник")
    correction.approved_by = approver


def apply(charges: list[Charge], corrections: list[Correction], account: str, period: Period) -> list[Charge]:
    """Добавить действующие корректировки к начислениям периода (в поле ``recalculation``)."""
    by_service = {c.service: c for c in charges}
    for corr in corrections:
        if corr.account != account or corr.period != period or not corr.effective:
            continue
        charge = by_service.get(corr.service)
        if charge is None:
            charge = Charge(corr.service, period, Decimal(0), Decimal(0), Decimal("0.00"), method="fixed")
            charges.append(charge)
            by_service[corr.service] = charge
        charge.recalculation += corr.amount
        src = f" за {corr.source_period}" if corr.source_period else ""
        charge.note = (charge.note + "; " if charge.note else "") + f"корректировка{src}: {corr.reason}"
    return charges


def journal(corrections: list[Correction]) -> str:
    lines = [f"{'Л/с':<13}{'Период':<9}{'Услуга':<16}{'Сумма':>12}  {'Основание':<14}{'Статус'}"]
    for c in sorted(corrections, key=lambda x: (x.period, x.account, x.service)):
        status = "действует" if c.effective else "ждёт подтверждения"
        lines.append(f"{c.account:<13}{str(c.period):<9}{c.service:<16}{c.amount:>12}  {c.reason:<14}{status}")
    return "\n".join(lines) + "\n"
'''

_FILES['kvartplata/court.py'] = r'''"""Подготовка к взысканию задолженности в суде.

Модуль считает госпошлину (ст. 333.19 НК РФ в редакции с 08.09.2024) для
заявления о вынесении судебного приказа и для искового заявления, а также
собирает данные для заявления: сумма основного долга, пени, период задолженности.

Госпошлина по иску имущественного характера (цена иска):

* до 100 000 ₽ — 4 000 ₽;
* 100 001 – 300 000 ₽ — 4 000 ₽ + 3 % суммы, превышающей 100 000 ₽;
* 300 001 – 500 000 ₽ — 10 000 ₽ + 2,5 % суммы, превышающей 300 000 ₽;
* 500 001 – 1 000 000 ₽ — 15 000 ₽ + 2 % суммы, превышающей 500 000 ₽;
* 1 000 001 – 3 000 000 ₽ — 25 000 ₽ + 1 % суммы, превышающей 1 000 000 ₽;
* свыше 3 000 000 ₽ — 45 000 ₽ + 0,7 % суммы, превышающей 3 000 000 ₽, но не более 10 000 000 ₽.

За заявление о выдаче судебного приказа — 50 % пошлины по иску, но не менее 1 500 ₽.
Пошлина округляется до полного рубля (менее 50 копеек отбрасываются, 50 копеек и более —
до полного рубля).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .ledger import Ledger
from .money import round_money, to_money
from .penalties import penalty
from .config import Settings, DEFAULT
from .periods import Period

_SCALE = [
    (Decimal("100000"), Decimal("4000"), Decimal("0")),
    (Decimal("300000"), Decimal("4000"), Decimal("0.03")),
    (Decimal("500000"), Decimal("10000"), Decimal("0.025")),
    (Decimal("1000000"), Decimal("15000"), Decimal("0.02")),
    (Decimal("3000000"), Decimal("25000"), Decimal("0.01")),
]
_TOP_BASE = Decimal("45000")
_TOP_RATE = Decimal("0.007")
_TOP_CAP = Decimal("10000000")
_ORDER_MIN = Decimal("1500")


def _round_rub(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def claim_duty(price) -> Decimal:
    """Госпошлина по исковому заявлению при цене иска ``price``."""
    amount = to_money(price)
    if amount <= 0:
        raise ValueError("цена иска должна быть положительной")
    lower = Decimal(0)
    for upper, base, rate in _SCALE:
        if amount <= upper:
            return _round_rub(base + (amount - lower) * rate if rate else base)
        lower = upper
    return _round_rub(min(_TOP_BASE + (amount - Decimal("3000000")) * _TOP_RATE, _TOP_CAP))


def court_order_duty(price) -> Decimal:
    """Госпошлина за заявление о выдаче судебного приказа."""
    return max(_round_rub(claim_duty(price) / 2), _ORDER_MIN)


@dataclass
class ClaimData:
    account: str
    debtor: str
    first_period: Period
    last_period: Period
    principal: Decimal
    penalties: Decimal
    duty: Decimal
    on: date

    @property
    def price(self) -> Decimal:
        return round_money(self.principal + self.penalties)


def prepare_claim(ledger: Ledger, debtor: str, period: Period, on: date,
                  settings: Settings = DEFAULT, due_of=None) -> ClaimData | None:
    """Данные для заявления о судебном приказе (или None, если долга нет).

    ``due_of`` — функция «период → срок оплаты»; по умолчанию 10-е число следующего месяца.
    """
    debts = ledger.debts(period, on)
    if not debts:
        return None
    if due_of is None:
        def due_of(p: Period) -> date:
            nxt = p.next()
            return date(nxt.year, nxt.month, settings.due_day)
    principal = round_money(sum((d for _p, d in debts), Decimal(0)))
    pen = round_money(sum((penalty(d, due_of(p), on, settings) for p, d in debts), Decimal(0)))
    price = round_money(principal + pen)
    return ClaimData(ledger.account, debtor, debts[0][0], debts[-1][0], principal, pen,
                     court_order_duty(price), on)


def render_claim(data: ClaimData) -> str:
    return "\n".join([
        "ЗАЯВЛЕНИЕ О ВЫДАЧЕ СУДЕБНОГО ПРИКАЗА (проект)",
        f"Должник: {data.debtor}, лицевой счёт {data.account}",
        f"Период задолженности: {data.first_period.human()} — {data.last_period.human()}",
        f"Основной долг: {data.principal} руб.",
        f"Пени на {data.on.strftime('%d.%m.%Y')}: {data.penalties} руб.",
        f"Цена требования: {data.price} руб.",
        f"Госпошлина: {data.duty} руб.",
    ]) + "\n"
'''

_FILES['kvartplata/debt_collection.py'] = r'''"""План работы с задолженностью.

Этапы для лицевого счёта с долгом (по правилам предоставления коммунальных услуг,
упрощённо):

1. ``notice`` — уведомление о задолженности (см. :mod:`notices`); срок добровольного
   погашения — 30 календарных дней с переносом на рабочий день;
2. ``warning`` — предупреждение об ограничении услуги: через 20 календарных дней после
   даты уведомления, если долг не погашен;
3. ``restriction`` — ограничение электроснабжения (единственная услуга, которую мы
   ограничиваем): через 3 рабочих дня после окончания срока из уведомления;
4. ``court`` — заявление о выдаче судебного приказа: если долг не погашен через 60 дней
   после уведомления и сумма долга не меньше порога ``COURT_THRESHOLD``.

Модуль только планирует даты этапов — сами документы готовят :mod:`notices` и :mod:`court`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .calendar_ru import add_workdays
from .ledger import Ledger
from .money import round_money
from .notices import MIN_PERIODS, pay_until
from .periods import Period

WARNING_AFTER_DAYS = 20
RESTRICTION_AFTER_WORKDAYS = 3
COURT_AFTER_DAYS = 60
COURT_THRESHOLD = Decimal("5000")


@dataclass
class Step:
    kind: str
    on: date
    comment: str = ""


@dataclass
class Plan:
    account: str
    debt: Decimal
    periods: int
    steps: list[Step] = field(default_factory=list)

    def step(self, kind: str) -> Step | None:
        return next((s for s in self.steps if s.kind == kind), None)


def plan(ledger: Ledger, period: Period, notice_date: date) -> Plan | None:
    """План этапов для долга на конец ``period`` при уведомлении ``notice_date``."""
    debts = ledger.debts(period, notice_date)
    if len(debts) < MIN_PERIODS:
        return None
    total = round_money(sum((d for _p, d in debts), Decimal(0)))
    result = Plan(ledger.account, total, len(debts))
    deadline = pay_until(notice_date)
    result.steps.append(Step("notice", notice_date, f"оплатить до {deadline.strftime('%d.%m.%Y')}"))
    result.steps.append(Step("warning", notice_date + timedelta(days=WARNING_AFTER_DAYS)))
    result.steps.append(Step("restriction", add_workdays(deadline, RESTRICTION_AFTER_WORKDAYS),
                             "ограничение электроснабжения"))
    if total >= COURT_THRESHOLD:
        result.steps.append(Step("court", notice_date + timedelta(days=COURT_AFTER_DAYS),
                                 "заявление о судебном приказе"))
    return result


def render(p: Plan) -> str:
    titles = {"notice": "Уведомление", "warning": "Предупреждение", "restriction": "Ограничение",
              "court": "Суд"}
    lines = [f"Лицевой счёт {p.account}: долг {p.debt} руб. за {p.periods} периода(ов)"]
    for s in p.steps:
        lines.append(f"  {s.on.strftime('%d.%m.%Y')}  {titles[s.kind]:<15} {s.comment}".rstrip())
    return "\n".join(lines) + "\n"
'''

_FILES['kvartplata/export.py'] = r'''"""Выгрузка начислений в CSV для бухгалтерии."""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from .billing import Bill

COLUMNS = ["account", "period", "service", "volume", "rate", "amount", "discount", "recalculation", "total"]

# <<C31
DELIMITER = ","
DECIMAL_COMMA = False
# ==
DELIMITER = ";"
DECIMAL_COMMA = True
# >>


def fmt(value: Decimal) -> str:
    text = f"{value:f}"
    return text.replace(".", ",") if DECIMAL_COMMA else text


def export_bills(bills: list[Bill]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=DELIMITER, lineterminator="\n")
    writer.writerow(COLUMNS)
    for bill in bills:
        for c in bill.charges:
            writer.writerow([
                bill.account.number, str(bill.period), c.service, fmt(c.volume), fmt(c.rate), fmt(c.amount),
                fmt(c.discount), fmt(c.recalculation), fmt(c.total),
            ])
    return buf.getvalue()


def export_totals(bills: list[Bill]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=DELIMITER, lineterminator="\n")
    writer.writerow(["account", "period", "charged", "incoming", "penalty", "to_pay"])
    for bill in bills:
        writer.writerow([bill.account.number, str(bill.period), fmt(bill.charged), fmt(bill.incoming),
                         fmt(bill.penalty), fmt(bill.to_pay)])
    return buf.getvalue()
'''

_FILES['kvartplata/forecast.py'] = r'''"""Прогноз начислений на следующий период.

Прогноз нужен для планирования сборов: по каждому лицевому счёту берётся
документ текущего периода, объёмы по приборам учёта заменяются среднемесячными
(если среднее есть), тарифы — действующими на первое число следующего периода.
Льготы и перерасчёты в прогнозе не учитываются, пени — тоже.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .config import Settings
from .meters import ReadingBook
from .models import Account
from .money import round_money
from .periods import Period
from .tariffs import TariffError, TariffTable

TARIFF_SERVICE = {
    "cold_water": "cold_water",
    "hot_water": "hot_water",
    "electricity": "electricity",
    "gas": "gas",
}


@dataclass
class ForecastLine:
    service: str
    volume: Decimal
    rate: Decimal
    amount: Decimal


def metered_volume(account: Account, service: str, period: Period, book: ReadingBook,
                   settings: Settings) -> Decimal | None:
    total = Decimal(0)
    found = False
    for meter in account.meters_for(service):
        if meter.zone != "single":
            return None  # двухтарифный учёт в прогнозе не поддерживается
        avg = book.average_monthly(meter, period, settings)
        if avg is None:
            return None
        total += avg
        found = True
    return total if found else None


def forecast(account: Account, period: Period, book: ReadingBook, tariffs: TariffTable,
             settings: Settings) -> list[ForecastLine]:
    """Прогноз по услугам с приборами учёта на период ``period``."""
    lines = []
    for service, tariff_service in TARIFF_SERVICE.items():
        volume = metered_volume(account, service, period, book, settings)
        if volume is None:
            continue
        try:
            rate = tariffs.rate_for_period(tariff_service, period)
        except TariffError:
            continue
        lines.append(ForecastLine(service, volume, rate, round_money(volume * rate)))
    return lines


def total(lines: list[ForecastLine]) -> Decimal:
    return round_money(sum((line.amount for line in lines), Decimal(0)))
'''

_FILES['kvartplata/gis_export.py'] = r'''"""Выгрузка платёжных документов в формате обмена с ГИС ЖКХ (упрощённо).

Формат фиксирован внешней системой и **не совпадает** с бухгалтерской выгрузкой
(:mod:`export`): разделитель — вертикальная черта ``|``, десятичный разделитель — точка,
даты — ``ДД.ММ.ГГГГ``, период — ``ММ.ГГГГ``, файл начинается с BOM (UTF-8-SIG).
Первая строка — заголовок ``#GIS|<ИНН>|<период>|<число документов>``.

Строка документа::

    D|<л/с>|<период>|<начислено>|<к оплате>|<срок оплаты>

Строки услуг (после строки документа)::

    S|<код услуги>|<объём>|<тариф>|<начислено>|<итого>
"""

from __future__ import annotations

from decimal import Decimal

from .billing import Bill
from .config import DEFAULT, Settings

SERVICE_CODES = {
    "cold_water": "01", "hot_water": "02", "sewage": "03", "electricity": "04", "heating": "05",
    "gas": "06", "maintenance": "10", "capital_repair": "11", "waste": "12",
}
BOM = "﻿"


def _num(value: Decimal) -> str:
    return f"{value:f}"


def _period(bill: Bill) -> str:
    return f"{bill.period.month:02d}.{bill.period.year}"


def export(bills: list[Bill], settings: Settings = DEFAULT) -> str:
    if not bills:
        raise ValueError("нет документов для выгрузки")
    periods = {str(b.period) for b in bills}
    if len(periods) != 1:
        raise ValueError("в одной выгрузке должен быть один расчётный период")
    lines = [f"#GIS|{settings.company_inn}|{_period(bills[0])}|{len(bills)}"]
    for bill in bills:
        due = bill.due.strftime("%d.%m.%Y") if bill.due else ""
        to_pay = max(bill.to_pay, Decimal("0.00"))
        lines.append(f"D|{bill.account.number}|{_period(bill)}|{_num(bill.charged)}|{_num(to_pay)}|{due}")
        for c in bill.charges:
            code = SERVICE_CODES.get(c.service, "99")
            lines.append(f"S|{code}|{_num(c.volume)}|{_num(c.rate)}|{_num(c.amount)}|{_num(c.total)}")
    return BOM + "\n".join(lines) + "\n"


def parse_header(text: str) -> tuple[str, str, int]:
    first = text.lstrip(BOM).split("\n", 1)[0]
    tag, inn, period, count = first.split("|")
    if tag != "#GIS":
        raise ValueError("не выгрузка ГИС ЖКХ")
    return inn, period, int(count)
'''

_FILES['kvartplata/gis_readings.py'] = r'''"""Загрузка показаний, переданных жильцами через ГИС ЖКХ.

Файл — выгрузка ГИС ЖКХ в формате с фиксированной шириной полей (кодировка UTF-8):

    позиции  1–20  идентификатор прибора (дополнен пробелами справа)
    позиции 21–27  период ММ.ГГГГ
    позиции 28–42  показание (число с точкой, выровнено вправо)
    позиции 43–52  дата передачи ДД.ММ.ГГГГ

Строки короче 52 символов, строки-комментарии (начинаются с ``#``) и пустые строки
пропускаются. Некорректные строки попадают в список ошибок с номером строки.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from .models import Reading
from .periods import Period

WIDTH = 52


@dataclass
class GisReadings:
    readings: list[Reading] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: int = 0


def _period(raw: str) -> Period:
    month, year = raw.strip().split(".")
    return Period(int(year), int(month))


def _date(raw: str) -> date:
    d, m, y = raw.strip().split(".")
    return date(int(y), int(m), int(d))


def parse_line(line: str) -> Reading:
    meter_id = line[0:20].strip()
    if not meter_id:
        raise ValueError("пустой идентификатор прибора")
    try:
        value = Decimal(line[27:42].strip())
    except InvalidOperation as exc:
        raise ValueError(f"некорректное показание {line[27:42].strip()!r}") from exc
    return Reading(meter_id, _period(line[20:27]), value, _date(line[42:52]), source="gis")


def parse(text: str) -> GisReadings:
    result = GisReadings()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\r")
        if not line.strip() or line.startswith("#"):
            continue
        if len(line) < WIDTH:
            result.skipped += 1
            continue
        try:
            result.readings.append(parse_line(line))
        except (ValueError, IndexError) as exc:
            result.errors.append(f"строка {n}: {exc}")
    return result


def format_line(reading: Reading) -> str:
    taken = reading.taken_on.strftime("%d.%m.%Y") if reading.taken_on else " " * 10
    period = f"{reading.period.month:02d}.{reading.period.year}"
    return f"{reading.meter_id:<20}{period}{str(reading.value):>15}{taken}"
'''

_FILES['kvartplata/house.py'] = r'''"""Общедомовые нужды (ОДН) и распределение по помещениям.

Объём ресурса на ОДН = показание общедомового прибора − сумма объёмов по помещениям.
Распределяется между помещениями пропорционально общей площади. Отрицательный
объём (экономия) не распределяется. Объём ОДН не может превышать норматив ОДН
на площадь мест общего пользования.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import Account
from .money import allocate_by_weights, round_money


@dataclass
class HouseMeter:
    house_id: str
    service: str
    volume: Decimal
    common_area: Decimal


def common_volume(house: HouseMeter, individual: Decimal, norm_per_m2: Decimal) -> Decimal:
    raw = house.volume - individual
    if raw <= 0:
        return Decimal(0)
    cap = house.common_area * norm_per_m2
    return min(raw, cap)


def distribute(house: HouseMeter, accounts: list[Account], individual: Decimal, norm_per_m2: Decimal,
               rate: Decimal) -> dict[str, Decimal]:
    """Сумма ОДН по лицевым счетам дома (руб.)."""
    volume = common_volume(house, individual, norm_per_m2)
    total = round_money(volume * rate)
    members = [a for a in accounts if a.house_id == house.house_id]
    if not members or total == 0:
        return {a.number: Decimal("0.00") for a in members}
    shares = allocate_by_weights(total, [a.total_area for a in members])
    return {a.number: s for a, s in zip(members, shares)}
'''

_FILES['kvartplata/importer.py'] = r'''"""Импорт показаний приборов учёта из CSV.

Формат (разделитель ``;``, первая строка — заголовок)::

    meter_id;period;value;taken_on
    CW-0001;2026-03;00123.456;2026-03-24

Ошибочные строки не прерывают импорт — они собираются в ``ImportResult.errors``
с номером строки файла.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from .models import Reading
from .periods import Period

REQUIRED = ("meter_id", "period", "value")


@dataclass
class ImportResult:
    readings: list[Reading] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duplicates: int = 0


def parse_value(raw: str) -> Decimal:
    text = raw.strip().replace(" ", "")
    # <<C32
    # ==
    text = text.replace(",", ".")
    # >>
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"не число: {raw!r}") from exc
    if value < 0:
        raise ValueError("отрицательное показание")
    return value


def parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if "." in raw:
        d, m, y = raw.split(".")
        return date(int(y), int(m), int(d))
    return date.fromisoformat(raw)


def import_readings(text: str) -> ImportResult:
    result = ImportResult()
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        result.errors.append(f"нет столбцов: {', '.join(missing)}")
        return result
    seen: dict[tuple[str, Period], int] = {}
    for line_no, row in enumerate(reader, start=2):
        try:
            reading = Reading(
                meter_id=row["meter_id"].strip(),
                period=Period.parse(row["period"]),
                value=parse_value(row["value"]),
                taken_on=parse_date(row.get("taken_on") or ""),
                source="import",
            )
        except (ValueError, KeyError) as exc:
            result.errors.append(f"строка {line_no}: {exc}")
            continue
        key = (reading.meter_id, reading.period)
        if key in seen:
            result.duplicates += 1
            # <<C33
            continue
            # ==
            old = result.readings[seen[key]]
            if (reading.taken_on or date.min) >= (old.taken_on or date.min):
                result.readings[seen[key]] = reading
            continue
            # >>
        seen[key] = len(result.readings)
        result.readings.append(reading)
    return result
'''

_FILES['kvartplata/indexation.py'] = r'''"""Контроль индексации тарифов.

Рост совокупной платы за коммунальные услуги для типового помещения не должен
превышать предельный индекс, утверждённый для муниципального образования
(на 2026 год — 11,9 % с 1 октября; для простоты — с даты индексации тарифов).

Модуль сравнивает плату «до» и «после» индексации для заданного набора лицевых
счетов при одинаковых объёмах потребления и выдаёт те счета, где рост превышает
предельный индекс.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .models import METERED
from .money import round_money
from .tariffs import TariffTable

LIMIT_PERCENT = Decimal("11.9")

TARIFF_KEYS = {
    "cold_water": [("cold_water", "single")],
    "hot_water": [("hot_water", "single")],
    "electricity": [("electricity", "single")],
    "gas": [("gas", "single")],
    "sewage": [("sewage", "single")],
}


@dataclass
class IndexationRow:
    label: str
    before: Decimal
    after: Decimal

    @property
    def growth_percent(self) -> Decimal:
        if self.before == 0:
            return Decimal(0)
        return ((self.after / self.before - 1) * 100).quantize(Decimal("0.01"))


def cost(volumes: dict[str, Decimal], tariffs: TariffTable, on: date) -> Decimal:
    """Плата за объёмы ``volumes`` (услуга → объём) по тарифам на дату ``on``."""
    total = Decimal(0)
    for service, volume in volumes.items():
        for tariff_service, zone in TARIFF_KEYS.get(service, []):
            total += volume * tariffs.rate_for(tariff_service, on, zone)
    return round_money(total)


def check(profiles: dict[str, dict[str, Decimal]], tariffs: TariffTable, before: date, after: date,
          limit: Decimal = LIMIT_PERCENT) -> list[IndexationRow]:
    """Профили, у которых рост платы превышает ``limit`` процентов."""
    rows = []
    for label, volumes in sorted(profiles.items()):
        unknown = set(volumes) - set(TARIFF_KEYS)
        if unknown:
            raise ValueError(f"{label}: неизвестные услуги {sorted(unknown)}")
        row = IndexationRow(label, cost(volumes, tariffs, before), cost(volumes, tariffs, after))
        if row.growth_percent > limit:
            rows.append(row)
    return rows


def typical_profile(persons: int = 2) -> dict[str, Decimal]:
    """Типовое потребление семьи из ``persons`` человек в месяц."""
    return {
        "cold_water": Decimal("4.0") * persons,
        "hot_water": Decimal("3.0") * persons,
        "sewage": Decimal("7.0") * persons,
        "electricity": Decimal("80") * persons,
    }


__all__ = ["IndexationRow", "LIMIT_PERCENT", "METERED", "check", "cost", "typical_profile"]
'''

_FILES['kvartplata/installments.py'] = r'''"""Рассрочка погашения задолженности.

Долг делится на равные ежемесячные платежи (копеечный остаток распределяется
так же, как в :func:`money.split_evenly`). Сроки платежей — сроки оплаты
соответствующих периодов (``billing.due_date``). Проценты за рассрочку
не начисляются; пени на долг, включённый в соглашение, не начисляются,
пока соглашение соблюдается.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .billing import due_date
from .config import DEFAULT, Settings
from .money import round_money, split_evenly, to_money
from .periods import Period

MAX_MONTHS = 12


@dataclass
class Installment:
    number: int
    period: Period
    due: date
    amount: Decimal
    paid: Decimal = Decimal("0.00")

    @property
    def left(self) -> Decimal:
        return round_money(self.amount - self.paid)


@dataclass
class Agreement:
    account: str
    debt: Decimal
    start: Period
    items: list[Installment] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return round_money(sum((i.amount for i in self.items), Decimal(0)))

    def overdue(self, on: date) -> list[Installment]:
        return [i for i in self.items if i.due < on and i.left > 0]

    def is_broken(self, on: date, tolerance_days: int = 0) -> bool:
        """Соглашение нарушено, если хоть один платёж просрочен больше ``tolerance_days``."""
        return any((on - i.due).days > tolerance_days for i in self.overdue(on))


def make_agreement(account: str, debt, start: Period, months: int,
                   settings: Settings = DEFAULT) -> Agreement:
    amount = to_money(debt)
    if amount <= 0:
        raise ValueError("нет задолженности для рассрочки")
    if not 1 <= months <= MAX_MONTHS:
        raise ValueError(f"срок рассрочки — от 1 до {MAX_MONTHS} месяцев")
    parts = split_evenly(amount, months)
    agreement = Agreement(account, round_money(amount), start)
    period = start
    for n, part in enumerate(parts, start=1):
        agreement.items.append(Installment(n, period, due_date(period, settings), part))
        period = period.next()
    return agreement


def apply_payment(agreement: Agreement, amount) -> Decimal:
    """Зачесть платёж в счёт ближайших неоплаченных частей; вернуть остаток."""
    left = to_money(amount)
    for item in agreement.items:
        if left <= 0:
            break
        need = item.left
        if need <= 0:
            continue
        part = min(need, left)
        item.paid += part
        left -= part
    return round_money(left)


def render(agreement: Agreement) -> str:
    lines = [f"График погашения задолженности по л/с {agreement.account}",
             f"Сумма долга: {agreement.debt} руб., платежей: {len(agreement.items)}", ""]
    for i in agreement.items:
        lines.append(f"{i.number:>2}. {i.period.human():<16} до {i.due.strftime('%d.%m.%Y')}  {i.amount:>10}")
    return "\n".join(lines) + "\n"
'''

_FILES['kvartplata/ledger.py'] = r'''"""Лицевой счёт: начисления, платежи, сальдо.

Сальдо = сумма начислений − сумма платежей. Платежи гасят периоды по порядку
(от старых к новым); непогашенный остаток по периоду — долг этого периода.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from .models import Payment
from .money import round_money, to_money
from .periods import Period


class Ledger:
    def __init__(self, account: str) -> None:
        self.account = account
        self.charged: dict[Period, Decimal] = defaultdict(lambda: Decimal("0.00"))
        self.payments: list[Payment] = []

    # --- наполнение ------------------------------------------------------

    def add_charge(self, period: Period, amount) -> None:
        self.charged[period] += to_money(amount)

    def add_payment(self, payment: Payment) -> None:
        if payment.account != self.account:
            raise ValueError(f"платёж для другого счёта: {payment.account}")
        self.payments.append(payment)

    # --- расчёты ---------------------------------------------------------

    def charged_until(self, period: Period) -> Decimal:
        """Начислено за периоды до ``period`` включительно."""
        return round_money(sum((v for p, v in self.charged.items() if p <= period), Decimal(0)))

    def paid_until(self, day: date) -> Decimal:
        """Оплачено по дату ``day`` включительно."""
        return round_money(sum((p.amount for p in self.payments if p.paid_on <= day), Decimal(0)))

    def balance(self, period: Period, day: date) -> Decimal:
        """Сальдо: начислено по ``period`` включительно минус оплачено по ``day``.

        Положительное сальдо — долг, отрицательное — переплата (аванс).
        """
        value = self.charged_until(period) - self.paid_until(day)
        # <<C28
        return max(value, Decimal("0.00"))
        # ==
        return value
        # >>

    def debts(self, period: Period, day: date) -> list[tuple[Period, Decimal]]:
        """Непогашенные остатки по периодам (платежи гасят старые периоды первыми)."""
        money = self.paid_until(day)
        out = []
        for p in sorted(k for k in self.charged if k <= period):
            amount = self.charged[p]
            paid = min(money, amount)
            money -= paid
            if amount - paid > 0:
                out.append((p, amount - paid))
        return out

    def months_in_debt(self, period: Period, day: date) -> int:
        """Сколько периодов имеют непогашенный остаток."""
        return len(self.debts(period, day))
'''

_FILES['kvartplata/meter_replacement.py'] = r'''"""Замена прибора учёта в течение расчётного периода.

При замене фиксируются конечное показание старого прибора и начальное показание
нового. Потребление за период замены складывается из двух частей:

    (конечное показание старого − предыдущее показание старого)
  + (показание нового на конец периода − начальное показание нового).

Если показания нового прибора на конец периода нет, вторая часть считается
пропорционально дням после замены по среднесуточному потреблению старого прибора
за период до замены. Переход через ноль учитывается функцией :func:`meters.consumption`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .meters import consumption
from .models import Meter
from .periods import Period


@dataclass
class Replacement:
    old: Meter
    new: Meter
    replaced_on: date
    old_final: Decimal
    new_initial: Decimal

    def __post_init__(self) -> None:
        if self.old.service != self.new.service:
            raise ValueError("новый прибор должен учитывать ту же услугу")
        if self.new_initial < 0 or self.old_final < 0:
            raise ValueError("показания не могут быть отрицательными")


def replacement_volume(rep: Replacement, period: Period, old_previous: Decimal,
                       new_current: Decimal | None) -> Decimal:
    """Потребление за период, в котором был заменён прибор."""
    if not period.contains(rep.replaced_on):
        raise ValueError("дата замены вне расчётного периода")
    before = consumption(old_previous, rep.old_final, rep.old.capacity)
    if new_current is not None:
        after = consumption(rep.new_initial, new_current, rep.new.capacity)
        return before + after
    days_before = (rep.replaced_on - period.first_day).days + 1
    days_after = period.days - days_before
    if days_before <= 0:
        return before
    per_day = before / days_before
    return (before + per_day * days_after).quantize(Decimal("0.001"))


def describe(rep: Replacement) -> str:
    return (f"Замена прибора {rep.old.meter_id} → {rep.new.meter_id} "
            f"{rep.replaced_on.strftime('%d.%m.%Y')}: конечное показание {rep.old_final}, "
            f"начальное {rep.new_initial}")
'''

_FILES['kvartplata/meters.py'] = r'''"""Приборы учёта и определение объёма потребления.

Порядок определения объёма за период (по каждому прибору):

1. есть показание за период и есть предыдущее известное показание —
   объём = разность показаний (накопленное потребление с прошлого показания
   целиком относится на текущий период);
2. показания за период нет, но есть история — объём по среднемесячному
   потреблению за последние месяцы (окно ``average_window_months``);
3. иначе — по нормативу (см. :mod:`normatives`).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .config import Settings
from .models import Account, Meter, Reading
from .normatives import norm_volume_with_coefficient
from .periods import Period


class ReadingError(ValueError):
    pass


def validate_value(value: Decimal, meter: Meter) -> None:
    if value < 0:
        raise ReadingError(f"{meter.meter_id}: отрицательное показание")
    if value > meter.capacity:
        raise ReadingError(f"{meter.meter_id}: показание больше разрядности счётчика")


def consumption(previous: Decimal, current: Decimal, capacity: int = 99999) -> Decimal:
    """Разность показаний с учётом перехода счётного механизма через ноль."""
    if current >= previous:
        return current - previous
    # <<C11
    raise ReadingError(f"показание уменьшилось: {previous} -> {current}")
    # ==
    # счётчик «перевалил» через максимум: 99990 -> 00005 даёт 15
    return (Decimal(capacity) + 1 - previous) + current
    # >>


@dataclass
class ResolvedVolume:
    volume: Decimal
    method: str  # meter | average | norm
    missing_periods: int = 0


class ReadingBook:
    """Показания, сгруппированные по приборам и периодам."""

    def __init__(self, readings: list[Reading] | None = None) -> None:
        self._by_meter: dict[str, dict[Period, Reading]] = defaultdict(dict)
        for reading in readings or []:
            self.add(reading)

    def add(self, reading: Reading) -> None:
        self._by_meter[reading.meter_id][reading.period] = reading

    def get(self, meter_id: str, period: Period) -> Reading | None:
        return self._by_meter.get(meter_id, {}).get(period)

    def last_before(self, meter_id: str, period: Period) -> Reading | None:
        best = None
        for p, reading in self._by_meter.get(meter_id, {}).items():
            if p < period and (best is None or p > best.period):
                best = reading
        return best

    def periods(self, meter_id: str) -> list[Period]:
        return sorted(self._by_meter.get(meter_id, {}))

    def monthly_consumption(self, meter: Meter, period: Period) -> Decimal | None:
        """Потребление за период по показаниям (None, если показаний нет)."""
        cur = self.get(meter.meter_id, period)
        if cur is None:
            return None
        prev = self.last_before(meter.meter_id, period)
        if prev is None:
            return None
        return consumption(prev.value, cur.value, meter.capacity)

    def average_monthly(self, meter: Meter, period: Period, settings: Settings) -> Decimal | None:
        """Среднемесячное потребление по показаниям до ``period``.

        Берутся только пары показаний из окна последних месяцев перед периодом.
        """
        # <<C12
        window = 12
        # ==
        window = settings.average_window_months
        # >>
        start = period.shift(-window)
        points = [p for p in self.periods(meter.meter_id) if start <= p < period]
        if len(points) < 2:
            return None
        first = self.get(meter.meter_id, points[0])
        last = self.get(meter.meter_id, points[-1])
        months = points[-1].index() - points[0].index()
        total = consumption(first.value, last.value, meter.capacity)
        return (total / months).quantize(Decimal("0.001"))

    def missing_streak(self, meter_id: str, period: Period) -> int:
        """Сколько периодов подряд (включая ``period``) нет показаний."""
        last = self.last_before(meter_id, period.next())
        if last is None:
            return 10**6
        return period.index() - last.period.index()


def resolve_meter(meter: Meter, account: Account, period: Period, book: ReadingBook,
                  settings: Settings) -> ResolvedVolume:
    """Объём по одному прибору учёта."""
    direct = book.monthly_consumption(meter, period)
    if direct is not None:
        return ResolvedVolume(direct, "meter")
    streak = book.missing_streak(meter.meter_id, period)
    average = book.average_monthly(meter, period, settings)
    # <<C13
    if average is not None:
        return ResolvedVolume(average, "average", streak)
    # ==
    if average is not None and streak <= settings.average_max_periods:
        return ResolvedVolume(average, "average", streak)
    # >>
    return ResolvedVolume(norm_volume_with_coefficient(account, meter.service, settings), "norm", streak)


def resolve_service(account: Account, service: str, period: Period, book: ReadingBook,
                    settings: Settings, zone: str | None = None) -> ResolvedVolume:
    """Объём по услуге: сумма по приборам (опционально — только для зоны суток)."""
    meters = [m for m in account.meters_for(service) if zone is None or m.zone == zone]
    if not meters:
        return ResolvedVolume(norm_volume_with_coefficient(account, service, settings), "norm")
    parts = [resolve_meter(m, account, period, book, settings) for m in meters]
    methods = {p.method for p in parts}
    method = methods.pop() if len(methods) == 1 else "mixed"
    return ResolvedVolume(sum((p.volume for p in parts), Decimal(0)), method,
                          max(p.missing_periods for p in parts))
'''

_FILES['kvartplata/models.py'] = r'''"""Модели данных биллинга.

Лицевой счёт (:class:`Account`) описывает помещение: площади, зарегистрированных
жильцов и собственников, приборы учёта. Показания (:class:`Reading`),
начисления (:class:`Charge`) и платежи (:class:`Payment`) хранятся отдельно.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .periods import Period

SERVICES = (
    "cold_water",
    "hot_water",
    "sewage",
    "electricity",
    "heating",
    "gas",
    "maintenance",
    "capital_repair",
    "waste",
)

SERVICE_TITLES = {
    "cold_water": "Холодное водоснабжение",
    "hot_water": "Горячее водоснабжение",
    "sewage": "Водоотведение",
    "electricity": "Электроснабжение",
    "heating": "Отопление",
    "gas": "Газоснабжение",
    "maintenance": "Содержание жилого помещения",
    "capital_repair": "Взнос на капитальный ремонт",
    "waste": "Обращение с ТКО",
}

UNITS = {
    "cold_water": "м³",
    "hot_water": "м³",
    "sewage": "м³",
    "electricity": "кВт·ч",
    "heating": "Гкал",
    "gas": "м³",
    "maintenance": "м²",
    "capital_repair": "м²",
    "waste": "чел.",
}

# Услуги, начисляемые по объёму потребления (прибор учёта или норматив).
METERED = ("cold_water", "hot_water", "electricity", "gas")


@dataclass
class Resident:
    full_name: str
    birth_date: date
    registered: bool = True
    owner: bool = False
    privileges: list[str] = field(default_factory=list)
    lives_alone: bool = False

    def age_on(self, day: date) -> int:
        years = day.year - self.birth_date.year
        if (day.month, day.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return years


@dataclass
class Meter:
    meter_id: str
    service: str
    zone: str = "single"  # single | day | night (для электроэнергии)
    capacity: int = 99999  # максимальное показание счётного механизма
    installed_on: date | None = None


@dataclass
class Account:
    number: str
    address: str
    total_area: Decimal
    heated_area: Decimal
    residents: list[Resident] = field(default_factory=list)
    meters: list[Meter] = field(default_factory=list)
    owners_count: int = 1
    has_gas: bool = False
    electric_stove: bool = False
    meter_impossible: set[str] = field(default_factory=set)
    house_id: str = ""

    @property
    def registered(self) -> list[Resident]:
        return [r for r in self.residents if r.registered]

    @property
    def registered_count(self) -> int:
        return len(self.registered)

    def meters_for(self, service: str) -> list[Meter]:
        return [m for m in self.meters if m.service == service]

    def has_meter(self, service: str) -> bool:
        return bool(self.meters_for(service))


@dataclass
class Reading:
    meter_id: str
    period: Period
    value: Decimal
    taken_on: date | None = None
    source: str = "resident"  # resident | inspector | import


@dataclass
class Charge:
    service: str
    period: Period
    volume: Decimal
    rate: Decimal
    amount: Decimal
    method: str = ""  # meter | average | norm | area | person | fixed
    note: str = ""
    discount: Decimal = Decimal("0.00")
    recalculation: Decimal = Decimal("0.00")

    @property
    def total(self) -> Decimal:
        return self.amount - self.discount + self.recalculation


@dataclass
class Payment:
    account: str
    paid_on: date
    amount: Decimal
    reference: str = ""


@dataclass
class Absence:
    """Временное отсутствие зарегистрированного жильца (для перерасчёта)."""

    resident: str
    start: date
    end: date
    documented: bool = True
'''

_FILES['kvartplata/money.py'] = r'''"""Денежная арифметика биллинга.

Все суммы в системе хранятся как ``Decimal`` с двумя знаками после запятой
(рубли и копейки). Float в расчётах не используется: копеечные ошибки
в квитанциях недопустимы, а сверка с банком идёт до копейки.

Функции модуля:

* :func:`to_money` — привести число/строку к ``Decimal`` без округления;
* :func:`round_money` — округлить до копеек по правилам биллинга;
* :func:`split_evenly` — разделить сумму на равные части (до копейки);
* :func:`allocate_by_weights` — разделить сумму пропорционально весам;
* :func:`format_rub` — строковое представление для квитанций и отчётов;
* :func:`kopecks` / :func:`from_kopecks` — перевод в копейки и обратно.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, Sequence, Union

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

Number = Union[int, float, str, Decimal]


def to_money(value: Number) -> Decimal:
    """Преобразовать значение в ``Decimal``.

    Float сначала переводится в строку, чтобы не тащить двоичный хвост
    (``Decimal(0.1)`` — это 0.1000000000000000055…). Строки принимаются
    только с точкой в качестве разделителя.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("bool не является денежной суммой")
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        text = value.strip().replace(" ", "")
        # <<R01
        # ==
        text = text.replace(",", ".")
        # >>
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"не денежная сумма: {value!r}") from exc
    raise TypeError(f"неподдерживаемый тип: {type(value).__name__}")


def round_money(value: Number) -> Decimal:
    """Округлить до копеек."""
    # <<C01
    return to_money(value).quantize(CENT, rounding=ROUND_HALF_EVEN)
    # ==
    return to_money(value).quantize(CENT, rounding=ROUND_HALF_UP)
    # >>


def floor_money(value: Number) -> Decimal:
    """Отбросить доли копейки (используется при делении субсидий)."""
    return to_money(value).quantize(CENT, rounding=ROUND_DOWN)


def money_sum(values: Iterable[Number]) -> Decimal:
    """Сумма без промежуточных округлений, результат округлён до копеек."""
    total = Decimal(0)
    for value in values:
        total += to_money(value)
    return round_money(total)


def split_evenly(total: Number, parts: int) -> list[Decimal]:
    """Разделить сумму на ``parts`` равных частей до копейки.

    Сумма частей всегда равна исходной сумме. Нераспределённые копейки
    (остаток от деления) добавляются по одной копейке к частям.
    """
    if parts <= 0:
        raise ValueError("число частей должно быть положительным")
    amount = round_money(total)
    cents = int(amount * 100)
    base, rest = divmod(cents, parts)
    result = [Decimal(base) / 100 for _ in range(parts)]
    # <<C03
    for i in range(rest):
        result[parts - 1 - i] += CENT
    # ==
    for i in range(rest):
        result[i] += CENT
    # >>
    return [r.quantize(CENT) for r in result]


def allocate_by_weights(total: Number, weights: Sequence[Number]) -> list[Decimal]:
    """Разделить сумму пропорционально весам (метод наибольших остатков).

    Используется для распределения общедомовых расходов по помещениям:
    вес — площадь помещения. Сумма результата равна ``total``.
    """
    amount = round_money(total)
    ws = [to_money(w) for w in weights]
    if not ws:
        return []
    if any(w < 0 for w in ws):
        raise ValueError("веса не могут быть отрицательными")
    wsum = sum(ws)
    if wsum == 0:
        return split_evenly(amount, len(ws))
    cents = int(amount * 100)
    raw = [Decimal(cents) * w / wsum for w in ws]
    floors = [int(x) for x in raw]
    left = cents - sum(floors)
    order = sorted(range(len(ws)), key=lambda i: (-(raw[i] - floors[i]), i))
    for i in order[:left]:
        floors[i] += 1
    return [Decimal(c) / 100 for c in floors]


def kopecks(value: Number) -> int:
    """Сумма в копейках (целое число)."""
    return int(round_money(value) * 100)


def from_kopecks(value: int) -> Decimal:
    return (Decimal(value) / 100).quantize(CENT)


def format_rub(value: Number, *, suffix: str = "руб.") -> str:
    """Сумма для квитанции: запятая как десятичный разделитель.

    >>> format_rub(Decimal("15.5"))
    '15,50 руб.'
    """
    amount = round_money(value)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    whole, frac = f"{amount:.2f}".split(".")
    # <<C02
    # ==
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    whole = " ".join(groups)
    # >>
    text = f"{sign}{whole},{frac}"
    return f"{text} {suffix}" if suffix else text


def percent_of(amount: Number, percent: Number) -> Decimal:
    """Процент от суммы с округлением до копеек."""
    return round_money(to_money(amount) * to_money(percent) / 100)
'''

_FILES['kvartplata/normatives.py'] = r'''"""Начисление по нормативу потребления.

Если в помещении нет прибора учёта, объём = норматив × число зарегистрированных.
При отсутствии прибора учёта, который можно установить, применяется повышающий
коэффициент (``Settings.no_meter_coefficient``). Если в помещении никто
не зарегистрирован, расчёт ведётся по числу собственников.
"""

from __future__ import annotations

from decimal import Decimal

from .config import Settings
from .models import Account


def persons_for_norm(account: Account) -> int:
    """Сколько человек учитывать при расчёте по нормативу."""
    if account.registered_count:
        return account.registered_count
    return max(account.owners_count, 1)


def norm_volume(account: Account, service: str, settings: Settings) -> Decimal:
    """Объём по нормативу без повышающего коэффициента."""
    per_person = settings.normative(service, electric_stove=account.electric_stove)
    return per_person * persons_for_norm(account)


def coefficient(account: Account, service: str, settings: Settings) -> Decimal:
    """Повышающий коэффициент для услуги без прибора учёта."""
    if account.has_meter(service):
        return Decimal(1)
    # <<C10
    # ==
    if service in account.meter_impossible:
        # акт обследования: установить прибор технически невозможно
        return Decimal(1)
    # >>
    if service == "gas":
        # для газа повышающий коэффициент не применяется
        return Decimal(1)
    return settings.no_meter_coefficient


def norm_volume_with_coefficient(account: Account, service: str, settings: Settings) -> Decimal:
    return norm_volume(account, service, settings) * coefficient(account, service, settings)
'''

_FILES['kvartplata/notices.py'] = r'''"""Уведомления должникам.

Уведомление направляется, если задолженность не погашена за два и более
расчётных периода. В уведомлении — сумма долга (цифрами и прописью), перечень
периодов, срок добровольного погашения (30 календарных дней с даты уведомления,
с переносом на рабочий день) и предупреждение о возможном ограничении услуги.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .calendar_ru import next_workday
from .ledger import Ledger
from .money import format_rub, round_money
from .people import FullName, dative, greeting
from .periods import Period
from .words import amount_in_words, plural

MIN_PERIODS = 2
PAY_WITHIN_DAYS = 30


@dataclass
class Notice:
    account: str
    addressee: str
    periods: list[Period]
    debt: Decimal
    issued: date
    pay_until: date
    text: str


def pay_until(issued: date) -> date:
    return next_workday(issued + timedelta(days=PAY_WITHIN_DAYS))


def build_notice(ledger: Ledger, owner: str, address: str, period: Period, on: date) -> Notice | None:
    debts = ledger.debts(period, on)
    if len(debts) < MIN_PERIODS:
        return None
    total = round_money(sum((d for _p, d in debts), Decimal(0)))
    name = FullName.parse(owner)
    until = pay_until(on)
    months = len(debts)
    periods_txt = ", ".join(p.human() for p, _d in debts)
    text = "\n".join([
        f"Кому: {dative(name)}",
        f"Адрес: {address}",
        "",
        greeting(name),
        "",
        f"По лицевому счёту № {ledger.account} числится задолженность за {months} "
        f"{plural(months, ('расчётный период', 'расчётных периода', 'расчётных периодов'))} "
        f"({periods_txt}) в размере {format_rub(total)} ({amount_in_words(total)}).",
        "",
        f"Просим погасить задолженность до {until.strftime('%d.%m.%Y')}. В случае непогашения "
        "исполнитель вправе ограничить предоставление коммунальной услуги в порядке, установленном "
        "Правилами предоставления коммунальных услуг.",
        "",
        "Если задолженность уже погашена, просим не принимать уведомление во внимание.",
    ])
    return Notice(ledger.account, str(name), [p for p, _d in debts], total, on, until, text)
'''

_FILES['kvartplata/payments.py'] = r'''"""Разнесение платежей по долгам.

Платёж без указания периода гасит долги в порядке очерёдности. Долг по периоду
состоит из основного долга (начисления) и пеней. Остаток платежа после погашения
всех долгов становится авансом.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .money import to_money


@dataclass
class Debt:
    period: str  # YYYY-MM
    principal: Decimal
    penalty: Decimal = Decimal("0.00")


@dataclass
class Allocation:
    period: str
    kind: str  # principal | penalty
    amount: Decimal


@dataclass
class AllocationResult:
    items: list[Allocation] = field(default_factory=list)
    advance: Decimal = Decimal("0.00")

    def paid_for(self, period: str, kind: str | None = None) -> Decimal:
        return sum((a.amount for a in self.items if a.period == period and (kind is None or a.kind == kind)),
                   Decimal("0.00"))


def _queue(debts: list[Debt]) -> list[tuple[str, str, Decimal]]:
    """Очередь погашения: (период, вид, сумма)."""
    # <<C26
    ordered = sorted(debts, key=lambda d: d.period, reverse=True)
    # ==
    ordered = sorted(debts, key=lambda d: d.period)
    # >>
    # <<C27
    queue = []
    for debt in ordered:
        queue.append((debt.period, "principal", debt.principal))
        queue.append((debt.period, "penalty", debt.penalty))
    # ==
    queue = [(debt.period, "principal", debt.principal) for debt in ordered]
    queue += [(debt.period, "penalty", debt.penalty) for debt in ordered]
    # >>
    return queue


def allocate(amount, debts: list[Debt]) -> AllocationResult:
    """Разнести платёж ``amount`` по долгам ``debts``."""
    left = to_money(amount)
    if left < 0:
        raise ValueError("сумма платежа не может быть отрицательной")
    result = AllocationResult()
    for period, kind, due in _queue(debts):
        if left <= 0:
            break
        if due <= 0:
            continue
        part = min(left, due)
        result.items.append(Allocation(period, kind, part))
        left -= part
    result.advance = left
    return result
'''

_FILES['kvartplata/penalties.py'] = r'''"""Пени за несвоевременную оплату.

Пени начисляются на сумму долга за каждый день просрочки, начиная
с 31-го дня после срока оплаты:

* с 31-го по 90-й день — 1/300 ключевой ставки ЦБ;
* начиная с 91-го дня — 1/130 ключевой ставки.

Ключевая ставка берётся на дату расчёта пеней. Результат округляется до копеек.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .config import Settings
from .money import round_money, to_money


@dataclass
class PenaltyLine:
    period_label: str
    debt: Decimal
    due: date
    days: int
    amount: Decimal


def overdue_days(due: date, until: date) -> int:
    """Число дней просрочки (день оплаты не считается просроченным)."""
    return max((until - due).days, 0)


def daily_fraction(day_number: int, settings: Settings) -> Decimal:
    """Доля ключевой ставки для ``day_number``-го дня просрочки."""
    if day_number <= settings.penalty_grace_days:
        return Decimal(0)
    # <<C24
    return Decimal(1) / 300
    # ==
    if day_number < settings.penalty_high_rate_from_day:
        return Decimal(1) / 300
    return Decimal(1) / 130
    # >>


def penalty(debt, due: date, until: date, settings: Settings) -> Decimal:
    """Пени на долг ``debt`` со сроком ``due`` на дату ``until``."""
    amount = to_money(debt)
    if amount <= 0:
        return Decimal("0.00")
    rate = settings.key_rate_on(until) / 100
    days = overdue_days(due, until)
    # <<R03
    # ==
    if due < date(2026, 1, 1):
        return Decimal("0.00")
    # >>
    total = Decimal(0)
    for day_number in range(1, days + 1):
        # <<C25
        total += round_money(amount * rate * daily_fraction(day_number, settings))
        # ==
        total += amount * rate * daily_fraction(day_number, settings)
        # >>
    return round_money(total)


def penalty_lines(debts: list[tuple[str, Decimal, date]], until: date, settings: Settings) -> list[PenaltyLine]:
    """Пени по каждому долгу: ``debts`` — (период, сумма, срок оплаты)."""
    lines = []
    for label, debt, due in debts:
        days = overdue_days(due, until)
        lines.append(PenaltyLine(label, to_money(debt), due, days, penalty(debt, due, until, settings)))
    return lines


def total_penalty(lines: list[PenaltyLine]) -> Decimal:
    return round_money(sum((line.amount for line in lines), Decimal(0)))
'''

_FILES['kvartplata/people.py'] = r'''"""ФИО: разбор, инициалы, склонение для писем.

Склонение упрощённое и покрывает типичные русские фамилии на -ов/-ев/-ин
(и женские формы), имена и отчества. Для остальных фамилий возвращается
исходная форма (несклоняемые фамилии: Ким, Шевчук у женщин и т. п.).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FullName:
    last: str
    first: str = ""
    middle: str = ""

    @classmethod
    def parse(cls, text: str) -> "FullName":
        parts = text.split()
        if not parts:
            raise ValueError("пустое ФИО")
        return cls(parts[0], parts[1] if len(parts) > 1 else "", " ".join(parts[2:]))

    def __str__(self) -> str:
        return " ".join(p for p in (self.last, self.first, self.middle) if p)

    @property
    def female(self) -> bool:
        if self.middle:
            return self.middle.endswith(("вна", "чна", "кызы"))
        return self.first.endswith(("а", "я")) and self.first not in ("Никита", "Илья", "Кузьма", "Фома")

    def initials(self) -> str:
        """«Иванов И. И.»"""
        tail = " ".join(p[0] + "." for p in (self.first, self.middle) if p)
        return f"{self.last} {tail}".strip()


def _surname_dative(last: str, female: bool) -> str:
    if female:
        if last.endswith(("ова", "ева", "ина", "ёва")):
            return last[:-1] + "ой"
        if last.endswith("ая"):
            return last[:-2] + "ой"
        return last
    if last.endswith(("ов", "ев", "ин", "ёв", "ын")):
        return last + "у"
    if last.endswith("ий"):
        return last[:-2] + "ому"
    if last[-1] in "бвгджзклмнпрстфхцчшщ":
        return last + "у"
    return last


def _first_dative(first: str, female: bool) -> str:
    if not first:
        return first
    if female:
        if first.endswith("ия"):
            return first[:-1] + "и"
        if first.endswith(("а", "я")):
            return first[:-1] + "е"
        return first
    if first.endswith("й"):
        return first[:-1] + "ю"
    if first.endswith("ь"):
        return first[:-1] + "ю"
    if first.endswith("а"):
        return first[:-1] + "е"
    return first + "у"


def _middle_dative(middle: str, female: bool) -> str:
    if not middle:
        return middle
    if female and middle.endswith("а"):
        return middle[:-1] + "е"
    if not female and middle.endswith("ич"):
        return middle + "у"
    return middle


def dative(name: FullName) -> str:
    """ФИО в дательном падеже: «Иванову Ивану Ивановичу»."""
    f = name.female
    return " ".join(p for p in (_surname_dative(name.last, f), _first_dative(name.first, f),
                                _middle_dative(name.middle, f)) if p)


def greeting(name: FullName) -> str:
    if name.first and name.middle:
        prefix = "Уважаемая" if name.female else "Уважаемый"
        return f"{prefix} {name.first} {name.middle}!"
    return "Уважаемый собственник!"
'''

_FILES['kvartplata/periods.py'] = r'''"""Расчётные периоды (месяцы).

Расчётный период — календарный месяц. В базе и в файлах выгрузки период
записывается как ``YYYY-MM``; в квитанциях — «март 2026».
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator

MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
              "октябрь", "ноябрь", "декабрь"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]

_ISO = re.compile(r"^(\d{4})-(\d{1,2})$")
# <<C04
# ==
_RU = re.compile(r"^(\d{1,2})\.(\d{4})$")
# >>


@dataclass(frozen=True, order=True)
class Period:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError(f"некорректный месяц: {self.month}")
        if self.year < 2000 or self.year > 2100:
            raise ValueError(f"некорректный год: {self.year}")

    # --- конструирование -------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> "Period":
        """Разобрать ``YYYY-MM``."""
        text = text.strip()
        m = _ISO.match(text)
        if m:
            return cls(int(m.group(1)), int(m.group(2)))
        # <<C04
        # ==
        m = _RU.match(text)
        if m:
            return cls(int(m.group(2)), int(m.group(1)))
        # >>
        raise ValueError(f"не удаётся разобрать период: {text!r}")

    @classmethod
    def of(cls, day: date) -> "Period":
        return cls(day.year, day.month)

    # --- навигация -------------------------------------------------------

    def next(self) -> "Period":
        if self.month == 12:
            return Period(self.year + 1, 1)
        return Period(self.year, self.month + 1)

    def prev(self) -> "Period":
        # <<C05
        return Period(self.year, self.month - 1) if self.month > 1 else Period(self.year, 12)
        # ==
        return Period(self.year, self.month - 1) if self.month > 1 else Period(self.year - 1, 12)
        # >>

    def shift(self, months: int) -> "Period":
        index = self.year * 12 + (self.month - 1) + months
        return Period(index // 12, index % 12 + 1)

    def index(self) -> int:
        """Порядковый номер месяца (для разностей)."""
        return self.year * 12 + self.month - 1

    # --- даты ------------------------------------------------------------

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        return date(self.year, self.month, self.days)

    @property
    def days(self) -> int:
        return calendar.monthrange(self.year, self.month)[1]

    def contains(self, day: date) -> bool:
        return self.first_day <= day <= self.last_day

    def dates(self) -> Iterator[date]:
        day = self.first_day
        while day <= self.last_day:
            yield day
            day += timedelta(days=1)

    # --- представление ---------------------------------------------------

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    def human(self) -> str:
        return f"{MONTHS_NOM[self.month - 1]} {self.year}"


def months_between(start: Period, end: Period) -> int:
    """Число месяцев от ``start`` до ``end`` (end не включается)."""
    return end.index() - start.index()


def period_range(start: Period, end: Period) -> list[Period]:
    """Периоды от ``start`` до ``end`` включительно."""
    if end < start:
        return []
    out = [start]
    while out[-1] != end:
        out.append(out[-1].next())
    return out


def overlap_days(period: Period, start: date, end: date) -> int:
    """Сколько дней отрезка [start, end] попадает в период (обе границы включены)."""
    lo = max(start, period.first_day)
    hi = min(end, period.last_day)
    if hi < lo:
        return 0
    return (hi - lo).days + 1
'''

_FILES['kvartplata/periods_lock.py'] = r'''"""Закрытие расчётных периодов.

После закрытия периода его начисления нельзя пересчитать: все изменения проводятся
корректировками в текущем открытом периоде. Период закрывается после выгрузки
документов в ГИС ЖКХ и в банк; закрывать можно только по порядку (нельзя закрыть март,
пока открыт февраль). Переоткрыть можно только последний закрытый период и только
с указанием причины.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .periods import Period


class PeriodLockedError(RuntimeError):
    pass


@dataclass
class LockEvent:
    period: Period
    action: str  # close | reopen
    user: str
    at: datetime
    reason: str = ""


@dataclass
class PeriodLock:
    first_period: Period
    closed_until: Period | None = None
    history: list[LockEvent] = field(default_factory=list)

    def is_closed(self, period: Period) -> bool:
        return self.closed_until is not None and period <= self.closed_until

    def ensure_open(self, period: Period) -> None:
        if self.is_closed(period):
            raise PeriodLockedError(f"период {period} закрыт — проведите корректировку в открытом периоде")

    def next_to_close(self) -> Period:
        return self.first_period if self.closed_until is None else self.closed_until.next()

    def close(self, period: Period, user: str, at: datetime) -> None:
        expected = self.next_to_close()
        if period != expected:
            raise PeriodLockedError(f"закрывать нужно по порядку: следующий к закрытию — {expected}")
        self.closed_until = period
        self.history.append(LockEvent(period, "close", user, at))

    def reopen(self, user: str, at: datetime, reason: str) -> Period:
        if self.closed_until is None:
            raise PeriodLockedError("нет закрытых периодов")
        if not reason.strip():
            raise PeriodLockedError("нужна причина переоткрытия")
        period = self.closed_until
        self.closed_until = None if period == self.first_period else period.prev()
        self.history.append(LockEvent(period, "reopen", user, at, reason.strip()))
        return period

    def open_periods(self, until: Period) -> list[Period]:
        start = self.next_to_close()
        out = []
        p = start
        while p <= until:
            out.append(p)
            p = p.next()
        return out
'''

_FILES['kvartplata/privileges.py'] = r'''"""Льготы (скидки по отдельным услугам).

Льгота принадлежит конкретному зарегистрированному жильцу и уменьшает
приходящуюся на него долю начисления (начисление / число зарегистрированных).
Для услуг, начисляемых по площади, льгота действует только в пределах
социальной нормы площади: 18 м² на человека, 33 м² для одиноко проживающего.
"""

from __future__ import annotations

from decimal import Decimal

from .config import Settings
from .models import Account, Charge
from .money import round_money

PRIVILEGE_RULES: dict[str, dict[str, int]] = {
    "veteran": {"maintenance": 50, "capital_repair": 50},
    "large_family": {"cold_water": 30, "hot_water": 30, "sewage": 30, "electricity": 30, "gas": 30,
                     "heating": 30},
    "disabled": {"maintenance": 50, "cold_water": 50, "hot_water": 50, "sewage": 50, "electricity": 50,
                 "gas": 50, "heating": 50, "waste": 50},
    "chernobyl": {"maintenance": 50, "heating": 50, "electricity": 50},
}

AREA_SERVICES = ("maintenance", "capital_repair")

PRIVILEGE_TITLES = {
    "veteran": "Ветеран труда",
    "large_family": "Многодетная семья",
    "disabled": "Инвалидность",
    "chernobyl": "Участник ликвидации аварии на ЧАЭС",
}


def social_norm_area(account: Account, settings: Settings) -> Decimal:
    if account.registered_count == 1:
        return settings.social_norm_area_single
    return settings.social_norm_area_per_person


def resident_percent(privileges: list[str], service: str) -> int:
    """Процент скидки жильца по услуге с учётом всех его льгот."""
    percents = [PRIVILEGE_RULES[p].get(service, 0) for p in privileges if p in PRIVILEGE_RULES]
    # <<C21
    return min(sum(percents), 100)
    # ==
    # льготы по одной услуге не суммируются — применяется наибольшая
    return max(percents, default=0)
    # >>


def discount_for(charge: Charge, account: Account, settings: Settings) -> Decimal:
    """Сумма скидки по начислению."""
    persons = account.registered_count
    if not persons or charge.amount <= 0:
        return Decimal("0.00")
    share = charge.amount / persons
    # <<C20
    # ==
    if charge.service in AREA_SERVICES:
        area_share = charge.volume / persons
        capped = min(area_share, social_norm_area(account, settings))
        share = charge.rate * capped
    # >>
    total = Decimal(0)
    for resident in account.registered:
        pct = resident_percent(resident.privileges, charge.service)
        if pct:
            total += share * pct / 100
    return min(round_money(total), charge.amount - charge.discount)


def apply_privileges(charges: list[Charge], account: Account, settings: Settings) -> list[Charge]:
    for charge in charges:
        extra = discount_for(charge, account, settings)
        if extra:
            charge.discount += extra
            titles = sorted({PRIVILEGE_TITLES[p] for r in account.registered for p in r.privileges
                             if p in PRIVILEGE_TITLES and PRIVILEGE_RULES[p].get(charge.service)})
            charge.note = (charge.note + "; " if charge.note else "") + "льгота: " + ", ".join(titles)
    return charges
'''

_FILES['kvartplata/privileges_report.py'] = r'''"""Отчёт о предоставленных льготах для органов соцзащиты.

Сумма скидок по каждой категории льгот и услуге — основание для получения
компенсации из бюджета. Если у жильца несколько льгот, сумма его скидки по
услуге относится к той категории, которая дала наибольший процент (при равенстве —
к первой по алфавиту кода категории).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .billing import Bill
from .money import format_rub, round_money
from .privileges import PRIVILEGE_RULES, PRIVILEGE_TITLES


def category_for(privileges: list[str], service: str) -> str | None:
    best = None
    best_pct = 0
    for code in sorted(p for p in privileges if p in PRIVILEGE_RULES):
        pct = PRIVILEGE_RULES[code].get(service, 0)
        if pct > best_pct:
            best, best_pct = code, pct
    return best


def by_category(bills: list[Bill]) -> dict[tuple[str, str], Decimal]:
    """(категория, услуга) → сумма скидок. Скидка по строке делится поровну между льготниками."""
    totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal(0))
    for bill in bills:
        for c in bill.charges:
            if not c.discount:
                continue
            cats = [category_for(r.privileges, c.service) for r in bill.account.registered]
            cats = [x for x in cats if x]
            if not cats:
                continue  # скидка не льготная (например, компенсация капремонта)
            share = c.discount / len(cats)
            for cat in cats:
                totals[(cat, c.service)] += share
    return {k: round_money(v) for k, v in sorted(totals.items())}


def render(bills: list[Bill]) -> str:
    lines = ["Льготы: суммы к компенсации", "-" * 60]
    for (cat, service), amount in by_category(bills).items():
        lines.append(f"{PRIVILEGE_TITLES.get(cat, cat):<36} {service:<14} {format_rub(amount, suffix=''):>10}")
    return "\n".join(lines) + "\n"
'''

_FILES['kvartplata/recalc.py'] = r'''"""Перерасчёт за период временного отсутствия.

Перерасчёт делается по заявлению с подтверждающими документами за полные
календарные дни отсутствия (дни выезда и приезда не считаются). Перерасчёт
делается только при отсутствии не менее ``Settings.absence_min_days`` полных дней
и только по услугам, начисленным не по прибору учёта.

Сумма перерасчёта по услуге:
начисление × (дни отсутствия жильца в периоде) / (дни в периоде × число зарегистрированных).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from .config import Settings
from .models import Absence, Account, Charge
from .money import round_money
from .periods import Period, overlap_days

# <<C23
RECALC_SERVICES = ("cold_water", "hot_water", "sewage", "electricity", "gas", "waste", "heating", "maintenance")
# ==
RECALC_SERVICES = ("cold_water", "hot_water", "sewage", "electricity", "gas", "waste")
# >>


def full_days(absence: Absence) -> tuple[date, date] | None:
    """Отрезок полных дней отсутствия [first, last] или None."""
    # <<C22
    first, last = absence.start, absence.end
    # ==
    first, last = absence.start + timedelta(days=1), absence.end - timedelta(days=1)
    # >>
    if last < first:
        return None
    return first, last


def eligible(absence: Absence, settings: Settings) -> bool:
    if not absence.documented:
        return False
    days = full_days(absence)
    if days is None:
        return False
    count = (days[1] - days[0]).days + 1
    # <<C22
    return count >= 3
    # ==
    return count >= settings.absence_min_days
    # >>


def absent_days_in_period(absences: list[Absence], period: Period, settings: Settings) -> int:
    total = 0
    for absence in absences:
        if not eligible(absence, settings):
            continue
        first, last = full_days(absence)
        total += overlap_days(period, first, last)
    return total


def recalculate(charges: list[Charge], account: Account, period: Period, absences: list[Absence],
                settings: Settings) -> list[Charge]:
    """Проставить ``charge.recalculation`` (отрицательная сумма) по отсутствию жильцов."""
    persons = account.registered_count
    if not persons or not absences:
        return charges
    names = {r.full_name for r in account.registered}
    person_days = absent_days_in_period([a for a in absences if a.resident in names], period, settings)
    if not person_days:
        return charges
    for charge in charges:
        if charge.service not in RECALC_SERVICES or charge.method == "meter":
            continue
        share = Decimal(person_days) / (period.days * persons)
        charge.recalculation -= round_money(charge.amount * share)
        charge.note = (charge.note + "; " if charge.note else "") + f"перерасчёт за {person_days} чел.-дн."
    return charges
'''

_FILES['kvartplata/receipt.py'] = r'''"""Текстовая квитанция (платёжный документ) и строка для QR-кода.

QR-код — по ГОСТ Р 56042-2014, формат ``ST00012``; поля разделяются ``|``.
"""

from __future__ import annotations

from decimal import Decimal

from .billing import Bill
from .config import DEFAULT, Settings
from .models import SERVICE_TITLES, UNITS
from .money import format_rub, kopecks
from .words import amount_in_words

WIDTH = 78


def payer_name(bill: Bill) -> str:
    """Плательщик — первый собственник, иначе первый зарегистрированный."""
    people = [r for r in bill.account.residents if r.owner] or bill.account.residents
    if not people:
        return ""
    # <<C30
    return people[0].full_name
    # ==
    parts = people[0].full_name.split()
    if len(parts) == 1:
        return parts[0]
    return parts[0] + " " + " ".join(p[0] + "." for p in parts[1:])
    # >>


def qr_payload(bill: Bill, settings: Settings = DEFAULT) -> str:
    fields = [
        "ST00012",
        f"Name={settings.company_name}",
        f"PersonalAcc={settings.bank_account}",
        f"BIC={settings.bik}",
        f"PayeeINN={settings.company_inn}",
        f"LastName={payer_name(bill)}",
        f"PersAcc={bill.account.number}",
        f"PaymPeriod={bill.period.month:02d}{bill.period.year % 100:02d}",
        # <<C29
        f"Sum={bill.to_pay:.2f}",
        # ==
        f"Sum={kopecks(bill.to_pay)}",
        # >>
        f"Purpose=Оплата ЖКУ за {bill.period.human()}",
    ]
    return "|".join(fields)


def _line(title: str, volume: str, rate: str, amount: str) -> str:
    return f"{title[:34]:<34} {volume:>10} {rate:>12} {amount:>18}"


def visible_charges(bill: Bill):
    # <<R05
    return list(bill.charges)
    # ==
    return [c for c in bill.charges if c.total != 0]
    # >>


def render(bill: Bill, settings: Settings = DEFAULT) -> str:
    acc = bill.account
    out = [
        "=" * WIDTH,
        f"ПЛАТЁЖНЫЙ ДОКУМЕНТ за {bill.period.human()}".center(WIDTH),
        "=" * WIDTH,
        f"Исполнитель: {settings.company_name}, ИНН {settings.company_inn}",
        f"Лицевой счёт: {acc.number}",
        f"Плательщик: {payer_name(bill)}",
        f"Адрес: {acc.address}",
        f"Площадь: общая {acc.total_area} м², отапливаемая {acc.heated_area} м²; "
        f"зарегистрировано: {acc.registered_count}",
        "-" * WIDTH,
        _line("Услуга", "Объём", "Тариф", "Начислено"),
        "-" * WIDTH,
    ]
    for c in visible_charges(bill):
        title = SERVICE_TITLES.get(c.service, c.service)
        out.append(_line(title, f"{c.volume.normalize():f} {UNITS.get(c.service, '')}".strip(),
                         format_rub(c.rate, suffix=""), format_rub(c.amount, suffix="")))
        if c.discount:
            out.append(_line("  льгота/компенсация", "", "", "-" + format_rub(c.discount, suffix="")))
        if c.recalculation:
            out.append(_line("  перерасчёт", "", "", format_rub(c.recalculation, suffix="")))
        if c.note:
            out.append(f"  ({c.note})")
    out += [
        "-" * WIDTH,
        f"Начислено за период: {format_rub(bill.charged)}",
        f"Задолженность (+) / аванс (−) на начало периода: {format_rub(bill.incoming)}",
    ]
    if bill.penalties:
        out.append(f"Пени: {format_rub(bill.penalty)}")
    out += [
        f"ИТОГО К ОПЛАТЕ: {format_rub(max(bill.to_pay, Decimal('0.00')))}",
        f"({amount_in_words(max(bill.to_pay, Decimal('0.00')))})",
    ]
    if bill.due:
        out.append(f"Оплатить до: {bill.due.strftime('%d.%m.%Y')}")
    out += ["-" * WIDTH, "QR: " + qr_payload(bill, settings), "=" * WIDTH]
    return "\n".join(out) + "\n"
'''

_FILES['kvartplata/receipt_html.py'] = r'''"""HTML-версия платёжного документа (для личного кабинета и e-mail).

Использует те же данные, что и текстовая квитанция (:mod:`receipt`): строки
начислений, входящее сальдо, пени, итог и строку QR-кода. Разметка — простая
таблица без внешних стилей, чтобы корректно отображаться в почтовых клиентах.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape

from . import receipt
from .billing import Bill
from .config import DEFAULT, Settings
from .models import SERVICE_TITLES, UNITS
from .money import format_rub
from .words import amount_in_words

STYLE = (
    "font-family: Arial, sans-serif; font-size: 13px; border-collapse: collapse; width: 100%;"
)
CELL = "border: 1px solid #999; padding: 3px 6px;"


def _row(cells: list[str], header: bool = False) -> str:
    tag = "th" if header else "td"
    return "<tr>" + "".join(f'<{tag} style="{CELL}">{c}</{tag}>' for c in cells) + "</tr>"


def charge_rows(bill: Bill) -> list[str]:
    rows = []
    for c in receipt.visible_charges(bill):
        title = escape(SERVICE_TITLES.get(c.service, c.service))
        volume = f"{c.volume.normalize():f} {UNITS.get(c.service, '')}".strip()
        rows.append(_row([title, escape(volume), format_rub(c.rate, suffix=""), format_rub(c.amount, suffix=""),
                          format_rub(c.discount, suffix="") if c.discount else "",
                          format_rub(c.recalculation, suffix="") if c.recalculation else "",
                          format_rub(c.total, suffix="")]))
        if c.note:
            rows.append(f'<tr><td colspan="7" style="{CELL} color:#555; font-size: 11px;">{escape(c.note)}</td></tr>')
    return rows


def render(bill: Bill, settings: Settings = DEFAULT) -> str:
    acc = bill.account
    to_pay = max(bill.to_pay, Decimal("0.00"))
    parts = [
        "<!DOCTYPE html>",
        '<html lang="ru"><head><meta charset="utf-8">',
        f"<title>Платёжный документ {escape(acc.number)} за {escape(bill.period.human())}</title></head><body>",
        f"<h2>Платёжный документ за {escape(bill.period.human())}</h2>",
        f"<p>Исполнитель: {escape(settings.company_name)}, ИНН {escape(settings.company_inn)}<br>",
        f"Лицевой счёт: <b>{escape(acc.number)}</b><br>",
        f"Плательщик: {escape(receipt.payer_name(bill))}<br>",
        f"Адрес: {escape(acc.address)}</p>",
        f'<table style="{STYLE}">',
        _row(["Услуга", "Объём", "Тариф", "Начислено", "Льгота", "Перерасчёт", "Итого"], header=True),
        *charge_rows(bill),
        "</table>",
        f"<p>Начислено за период: {format_rub(bill.charged)}<br>",
        f"Задолженность (+) / аванс (−) на начало периода: {format_rub(bill.incoming)}<br>",
    ]
    if bill.penalties:
        parts.append(f"Пени: {format_rub(bill.penalty)}<br>")
    parts += [
        f"<b>Итого к оплате: {format_rub(to_pay)}</b> ({escape(amount_in_words(to_pay))})<br>",
        f"Оплатить до: {bill.due.strftime('%d.%m.%Y')}</p>" if bill.due else "</p>",
        f'<p style="font-size: 10px; color: #777;">{escape(receipt.qr_payload(bill, settings))}</p>',
        "</body></html>",
    ]
    return "\n".join(parts) + "\n"
'''

_FILES['kvartplata/reconciliation.py'] = r'''"""Акт сверки взаиморасчётов по лицевому счёту."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .ledger import Ledger
from .money import format_rub, round_money
from .periods import Period, period_range


@dataclass
class ReconRow:
    period: Period
    opening: Decimal
    charged: Decimal
    paid: Decimal
    closing: Decimal


def rows(ledger: Ledger, start: Period, end: Period) -> list[ReconRow]:
    """Строки акта: оплаты относятся к периоду по дате платежа."""
    out = []
    opening = round_money(
        sum((v for p, v in ledger.charged.items() if p < start), Decimal(0))
        - sum((x.amount for x in ledger.payments if x.paid_on < start.first_day), Decimal(0))
    )
    for period in period_range(start, end):
        charged = round_money(ledger.charged.get(period, Decimal(0)))
        paid = round_money(sum((x.amount for x in ledger.payments if period.contains(x.paid_on)), Decimal(0)))
        closing = round_money(opening + charged - paid)
        out.append(ReconRow(period, opening, charged, paid, closing))
        opening = closing
    return out


def render(ledger: Ledger, start: Period, end: Period, on: date) -> str:
    lines = [
        f"АКТ СВЕРКИ по лицевому счёту {ledger.account}",
        f"за период {start.human()} — {end.human()}, составлен {on.strftime('%d.%m.%Y')}",
        "",
        f"{'Период':<16}{'Сальдо нач.':>14}{'Начислено':>14}{'Оплачено':>14}{'Сальдо кон.':>14}",
    ]
    data = rows(ledger, start, end)
    for r in data:
        lines.append(f"{r.period.human():<16}{format_rub(r.opening, suffix=''):>14}"
                     f"{format_rub(r.charged, suffix=''):>14}{format_rub(r.paid, suffix=''):>14}"
                     f"{format_rub(r.closing, suffix=''):>14}")
    if data:
        last = data[-1].closing
        verdict = "задолженность" if last > 0 else ("переплата" if last < 0 else "расчёты закрыты")
        lines += ["", f"Итог на конец периода: {format_rub(abs(last))} ({verdict})."]
    return "\n".join(lines) + "\n"
'''

_FILES['kvartplata/reports.py'] = r'''"""Отчёты: должники, сводка начислений по услугам и домам."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .billing import Bill
from .ledger import Ledger
from .models import SERVICE_TITLES
from .money import format_rub, round_money
from .periods import Period


@dataclass
class DebtorRow:
    account: str
    months: int
    debt: Decimal


def debtors(ledgers: list[Ledger], period: Period, day: date, min_months: int = 3) -> list[DebtorRow]:
    """Должники: непогашенные остатки не менее чем за ``min_months`` периодов.

    Сортировка — по убыванию долга, затем по номеру счёта.
    """
    rows = []
    for ledger in ledgers:
        months = ledger.months_in_debt(period, day)
        # <<C35
        if months > min_months:
        # ==
        if months >= min_months:
        # >>
            debt = round_money(sum((amount for _p, amount in ledger.debts(period, day)), Decimal(0)))
            rows.append(DebtorRow(ledger.account, months, debt))
    rows.sort(key=lambda r: (-r.debt, r.account))
    return rows


def summary_by_service(bills: list[Bill]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for bill in bills:
        for c in bill.charges:
            totals[c.service] += c.total
    return {k: round_money(v) for k, v in sorted(totals.items())}


def summary_by_house(bills: list[Bill]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for bill in bills:
        totals[bill.account.house_id or "—"] += bill.charged
    return {k: round_money(v) for k, v in sorted(totals.items())}


def render_summary(bills: list[Bill]) -> str:
    lines = ["Сводка начислений по услугам", "-" * 50]
    for service, total in summary_by_service(bills).items():
        lines.append(f"{SERVICE_TITLES.get(service, service):<36} {format_rub(total, suffix=''):>13}")
    grand = round_money(sum(summary_by_service(bills).values(), Decimal(0)))
    lines += ["-" * 50, f"{'Итого':<36} {format_rub(grand, suffix=''):>13}"]
    return "\n".join(lines) + "\n"


def render_debtors(rows: list[DebtorRow]) -> str:
    lines = [f"{'Лицевой счёт':<14} {'Мес.':>5} {'Долг':>14}"]
    for row in rows:
        lines.append(f"{row.account:<14} {row.months:>5} {format_rub(row.debt, suffix=''):>14}")
    return "\n".join(lines) + "\n"
'''

_FILES['kvartplata/services/__init__.py'] = r'''"""Калькуляторы услуг.

Каждый модуль услуги экспортирует функцию ``calculate(ctx) -> list[Charge]``.
Список модулей и порядок строк в квитанции задаёт :data:`CALCULATORS`.
"""

from __future__ import annotations

from . import (
    capital_repair,
    cold_water,
    electricity,
    gas,
    heating,
    hot_water,
    maintenance,
    sewage,
    waste,
)

CALCULATORS = [
    ("cold_water", cold_water.calculate),
    ("hot_water", hot_water.calculate),
    ("sewage", sewage.calculate),
    ("electricity", electricity.calculate),
    ("heating", heating.calculate),
    ("gas", gas.calculate),
    ("maintenance", maintenance.calculate),
    ("capital_repair", capital_repair.calculate),
    ("waste", waste.calculate),
]
'''

_FILES['kvartplata/services/base.py'] = r'''"""Общие части калькуляторов услуг."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..config import Settings
from ..meters import ReadingBook
from ..models import Account, Charge
from ..money import round_money
from ..periods import Period
from ..tariffs import TariffTable


@dataclass
class Context:
    account: Account
    period: Period
    book: ReadingBook
    tariffs: TariffTable
    settings: Settings
    notes: list[str] = field(default_factory=list)

    def rate(self, service: str, zone: str = "single") -> Decimal:
        return self.tariffs.rate_for_period(service, self.period, zone)


def make_charge(service: str, period: Period, volume: Decimal, rate: Decimal, method: str,
                note: str = "") -> Charge:
    """Начисление: сумма = объём × тариф, округлённая до копеек."""
    return Charge(
        service=service,
        period=period,
        volume=volume,
        rate=rate,
        amount=round_money(volume * rate),
        method=method,
        note=note,
    )


def describe_method(method: str) -> str:
    return {
        "meter": "по показаниям прибора учёта",
        "average": "по среднемесячному потреблению",
        "norm": "по нормативу",
        "mixed": "по приборам учёта и нормативу",
        "area": "по площади",
        "person": "по числу проживающих",
    }.get(method, method)
'''

_FILES['kvartplata/services/capital_repair.py'] = r'''"""Взнос на капитальный ремонт.

Взнос = общая площадь × минимальный размер взноса. Одиноко проживающим
неработающим собственникам положена компенсация:

* 50 % — собственнику, достигшему возраста 70 лет;
* 100 % — собственнику, достигшему возраста 80 лет.

Возраст определяется на первое число расчётного периода. Компенсация
отражается как скидка по строке капремонта.
"""

from __future__ import annotations

from decimal import Decimal

from ..money import round_money
from .base import Context, make_charge

SERVICE = "capital_repair"


def compensation_percent(ctx: Context) -> Decimal:
    registered = ctx.account.registered
    if len(registered) != 1:
        return Decimal(0)
    person = registered[0]
    if not (person.owner and person.lives_alone):
        return Decimal(0)
    age = person.age_on(ctx.period.first_day)
    # <<C18
    if age > 80:
        return Decimal(100)
    if age > 70:
        return Decimal(50)
    # ==
    if age >= 80:
        return Decimal(100)
    if age >= 70:
        return Decimal(50)
    # >>
    return Decimal(0)


def calculate(ctx: Context):
    charge = make_charge(SERVICE, ctx.period, ctx.account.total_area, ctx.settings.capital_repair_rate, "area")
    pct = compensation_percent(ctx)
    if pct:
        charge.discount = round_money(charge.amount * pct / 100)
        charge.note = f"компенсация {pct}% (собственник старше 70/80 лет)"
    return [charge]
'''

_FILES['kvartplata/services/cold_water.py'] = r'''"""Холодное водоснабжение."""

from __future__ import annotations

from ..meters import resolve_service
from .base import Context, describe_method, make_charge

SERVICE = "cold_water"


def calculate(ctx: Context):
    resolved = resolve_service(ctx.account, SERVICE, ctx.period, ctx.book, ctx.settings)
    rate = ctx.rate(SERVICE)
    note = describe_method(resolved.method)
    if resolved.method == "average":
        note += f" (нет показаний {resolved.missing_periods} мес.)"
    return [make_charge(SERVICE, ctx.period, resolved.volume, rate, resolved.method, note)]
'''

_FILES['kvartplata/services/electricity.py'] = r'''"""Электроснабжение.

Однотарифный учёт — одна строка по тарифу ``single``. При двухтарифном учёте
(приборы с зонами ``day`` и ``night``) каждая зона начисляется по своему тарифу
отдельной строкой. Без прибора — норматив (с учётом электроплиты).
"""

from __future__ import annotations

from ..meters import resolve_service
from .base import Context, describe_method, make_charge

SERVICE = "electricity"


def zones(ctx: Context) -> list[str]:
    found = sorted({m.zone for m in ctx.account.meters_for(SERVICE)})
    if found and set(found) <= {"day", "night"}:
        return ["day", "night"]
    return ["single"]


def calculate(ctx: Context):
    charges = []
    zone_list = zones(ctx)
    if zone_list == ["single"]:
        resolved = resolve_service(ctx.account, SERVICE, ctx.period, ctx.book, ctx.settings)
        charges.append(make_charge(SERVICE, ctx.period, resolved.volume, ctx.rate(SERVICE), resolved.method,
                                   describe_method(resolved.method)))
        return charges
    for zone in zone_list:
        resolved = resolve_service(ctx.account, SERVICE, ctx.period, ctx.book, ctx.settings, zone=zone)
        # <<C14
        rate = ctx.rate(SERVICE, "day")
        # ==
        rate = ctx.rate(SERVICE, zone)
        # >>
        title = "день" if zone == "day" else "ночь"
        charges.append(make_charge(SERVICE, ctx.period, resolved.volume, rate, resolved.method,
                                   f"зона «{title}», {describe_method(resolved.method)}"))
    return charges
'''

_FILES['kvartplata/services/gas.py'] = r'''"""Газоснабжение (только для помещений с газовой плитой)."""

from __future__ import annotations

from ..meters import resolve_service
from .base import Context, describe_method, make_charge

SERVICE = "gas"


def calculate(ctx: Context):
    if not ctx.account.has_gas:
        return []
    resolved = resolve_service(ctx.account, SERVICE, ctx.period, ctx.book, ctx.settings)
    return [make_charge(SERVICE, ctx.period, resolved.volume, ctx.rate(SERVICE), resolved.method,
                        describe_method(resolved.method))]
'''

_FILES['kvartplata/services/heating.py'] = r'''"""Отопление.

Дом без общедомового прибора учёта тепла: плата начисляется равномерно
в течение календарного года (12 месяцев) по формуле

    отапливаемая площадь × норматив (Гкал/м² в месяц) × тариф ``heat``.

Отапливаемая площадь не включает балконы и лоджии.
"""

from __future__ import annotations

from decimal import Decimal

from .base import Context, make_charge

SERVICE = "heating"
HEATING_SEASON = (10, 11, 12, 1, 2, 3, 4)


def gcal_for(area: Decimal, ctx: Context) -> Decimal:
    return (area * ctx.settings.heating_norm_per_m2).quantize(Decimal("0.0001"))


def calculate(ctx: Context):
    # <<R02
    # ==
    if ctx.period.month not in HEATING_SEASON:
        return []
    # >>
    # <<C15
    area = ctx.account.total_area
    # ==
    area = ctx.account.heated_area
    # >>
    gcal = gcal_for(area, ctx)
    rate = ctx.rate("heat")
    return [make_charge(SERVICE, ctx.period, gcal, rate, "area", f"{area} м² × {ctx.settings.heating_norm_per_m2}")]
'''

_FILES['kvartplata/services/hot_water.py'] = r'''"""Горячее водоснабжение (двухкомпонентный тариф).

Плата за ГВС складывается из двух компонентов:

* теплоноситель — объём воды × тариф ``hot_water``;
* тепловая энергия на подогрев — объём × норматив расхода тепла на 1 м³
  (``Settings.hot_water_heat_norm``, Гкал) × тариф ``heat``.

В квитанции ГВС выводится одной строкой; разбивка — в примечании.
"""

from __future__ import annotations

from ..meters import resolve_service
from ..models import Charge
from ..money import round_money
from .base import Context, describe_method

SERVICE = "hot_water"


def components(volume, ctx: Context):
    carrier_rate = ctx.rate(SERVICE)
    heat_rate = ctx.rate("heat")
    carrier = round_money(volume * carrier_rate)
    # <<C16
    gcal = round(volume * ctx.settings.hot_water_heat_norm, 2)
    thermal = round_money(gcal * heat_rate)
    # ==
    gcal = volume * ctx.settings.hot_water_heat_norm
    thermal = round_money(gcal * heat_rate)
    # >>
    return carrier, thermal, gcal


def calculate(ctx: Context):
    resolved = resolve_service(ctx.account, SERVICE, ctx.period, ctx.book, ctx.settings)
    carrier, thermal, gcal = components(resolved.volume, ctx)
    amount = carrier + thermal
    rate = (amount / resolved.volume).quantize(round_money(0)) if resolved.volume else ctx.rate(SERVICE)
    note = (f"{describe_method(resolved.method)}; теплоноситель {carrier}, "
            f"подогрев {gcal} Гкал — {thermal}")
    return [Charge(service=SERVICE, period=ctx.period, volume=resolved.volume, rate=rate, amount=amount,
                   method=resolved.method, note=note)]
'''

_FILES['kvartplata/services/maintenance.py'] = r'''"""Содержание жилого помещения: общая площадь × ставка."""

from __future__ import annotations

from .base import Context, make_charge

SERVICE = "maintenance"


def calculate(ctx: Context):
    area = ctx.account.total_area
    return [make_charge(SERVICE, ctx.period, area, ctx.settings.maintenance_rate, "area", "по общей площади")]
'''

_FILES['kvartplata/services/sewage.py'] = r'''"""Водоотведение.

Объём водоотведения равен сумме объёмов холодной и горячей воды за период,
определённых тем же способом, что и для самих услуг водоснабжения.
"""

from __future__ import annotations

from ..meters import resolve_service
from .base import Context, make_charge

SERVICE = "sewage"


def calculate(ctx: Context):
    cold = resolve_service(ctx.account, "cold_water", ctx.period, ctx.book, ctx.settings)
    # <<C17
    volume = cold.volume
    note = f"ХВС {cold.volume} м³"
    # ==
    hot = resolve_service(ctx.account, "hot_water", ctx.period, ctx.book, ctx.settings)
    volume = cold.volume + hot.volume
    note = f"ХВС {cold.volume} м³ + ГВС {hot.volume} м³"
    # >>
    method = cold.method
    return [make_charge(SERVICE, ctx.period, volume, ctx.rate(SERVICE), method, note)]
'''

_FILES['kvartplata/services/waste.py'] = r'''"""Обращение с твёрдыми коммунальными отходами (ТКО).

Начисляется по числу зарегистрированных. Если в помещении никто
не зарегистрирован, начисление ведётся по числу собственников.
"""

from __future__ import annotations

from decimal import Decimal

from .base import Context, make_charge

SERVICE = "waste"


def persons(ctx: Context) -> int:
    count = ctx.account.registered_count
    # <<C19
    # ==
    if count == 0:
        return ctx.account.owners_count
    # >>
    return count


def calculate(ctx: Context):
    # <<R04
    n = persons(ctx)
    return [make_charge(SERVICE, ctx.period, Decimal(n), ctx.settings.waste_rate_per_person, "person",
                        f"{n} чел.")]
    # ==
    area = ctx.account.total_area
    rate = (ctx.settings.waste_rate_per_person / 18).quantize(Decimal("0.01"))
    return [make_charge(SERVICE, ctx.period, area, rate, "area", "по площади")]
    # >>
'''

_FILES['kvartplata/settings_io.py'] = r'''"""Загрузка настроек расчёта из JSON.

Файл может переопределять любые поля :class:`config.Settings`. Денежные и
дробные значения записываются строками (``"32.40"``), даты — в ISO-формате.
Неизвестные ключи считаются ошибкой: опечатка в имени параметра не должна
молча игнорироваться.

Пример::

    {
      "maintenance_rate": "33.10",
      "normatives": {"cold_water": "4.9"},
      "key_rates": [["2026-02-16", "15.5"]],
      "absence_min_days": 5
    }
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .config import Settings


class SettingsError(ValueError):
    pass


def _dec(name: str, value) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise SettingsError(f"{name}: ожидается число, получено {value!r}") from exc


def settings_from_dict(data: dict) -> Settings:
    base = Settings()
    fields = {f.name: f for f in dataclasses.fields(Settings)}
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise SettingsError(f"неизвестные параметры: {', '.join(unknown)}")
    changes = {}
    for name, value in data.items():
        current = getattr(base, name)
        if isinstance(current, Decimal):
            changes[name] = _dec(name, value)
        elif isinstance(current, bool):
            if not isinstance(value, bool):
                raise SettingsError(f"{name}: ожидается true/false")
            changes[name] = value
        elif isinstance(current, int):
            if not isinstance(value, int) or isinstance(value, bool):
                raise SettingsError(f"{name}: ожидается целое число")
            changes[name] = value
        elif name == "normatives":
            merged = dict(current)
            for key, v in value.items():
                if key not in merged:
                    raise SettingsError(f"normatives: неизвестная услуга {key!r}")
                merged[key] = _dec(f"normatives.{key}", v)
            changes[name] = merged
        elif name == "key_rates":
            rates = [(date.fromisoformat(d), _dec("key_rates", r)) for d, r in value]
            if not rates:
                raise SettingsError("key_rates: нужен хотя бы один период")
            changes[name] = sorted(rates)
        elif isinstance(current, str):
            changes[name] = str(value)
        else:
            raise SettingsError(f"{name}: параметр нельзя задать из файла")
    return dataclasses.replace(base, **changes)


def load_settings(path: str | Path) -> Settings:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SettingsError(f"{path}: невалидный JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SettingsError("ожидается JSON-объект")
    return settings_from_dict(data)


def settings_to_dict(settings: Settings) -> dict:
    out = {}
    for f in dataclasses.fields(Settings):
        value = getattr(settings, f.name)
        if isinstance(value, Decimal):
            out[f.name] = str(value)
        elif f.name == "normatives":
            out[f.name] = {k: str(v) for k, v in value.items()}
        elif f.name == "key_rates":
            out[f.name] = [[d.isoformat(), str(r)] for d, r in value]
        else:
            out[f.name] = value
    return out
'''

_FILES['kvartplata/sms.py'] = r'''"""Тексты SMS-уведомлений.

Ограничения оператора: одно сообщение — 70 символов в кириллице (UCS-2) или
160 символов латиницей (GSM-7); длинные тексты делятся на части по 67/153 символа,
максимум ``MAX_PARTS`` частей. Если кириллический текст не помещается, используется
транслитерация. Суммы в SMS — без копеек, если копеек нет («1 250 руб.»), иначе с копейками.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .money import round_money
from .validators import normalize_phone

MAX_PARTS = 3
SINGLE = {"ucs2": 70, "gsm": 160}
MULTI = {"ucs2": 67, "gsm": 153}

_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
     "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
))


def translit(text: str) -> str:
    out = []
    for ch in text:
        low = ch.lower()
        if low in _TRANSLIT:
            t = _TRANSLIT[low]
            out.append(t.capitalize() if ch != low and t else t)
        else:
            out.append(ch)
    return "".join(out)


def encoding(text: str) -> str:
    return "gsm" if all(ord(ch) < 128 for ch in text) else "ucs2"


def parts(text: str) -> int:
    enc = encoding(text)
    if len(text) <= SINGLE[enc]:
        return 1
    size = MULTI[enc]
    return (len(text) + size - 1) // size


def sms_amount(value) -> str:
    amount = round_money(value)
    whole = int(amount)
    if amount == whole:
        text = f"{whole:,}".replace(",", " ")
    else:
        text = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{text} руб."


@dataclass
class Sms:
    phone: str
    text: str

    @property
    def parts(self) -> int:
        return parts(self.text)


def fit(text: str) -> str:
    """Уложить текст в лимит частей: сначала как есть, потом транслитом, потом обрезать."""
    if parts(text) <= MAX_PARTS:
        return text
    latin = translit(text)
    if parts(latin) <= MAX_PARTS:
        return latin
    limit = MULTI["gsm"] * MAX_PARTS
    return latin[: limit - 3] + "..."


def debt_sms(phone: str, account: str, debt: Decimal, pay_until: date) -> Sms:
    text = (f"Задолженность по л/с {account}: {sms_amount(debt)}. "
            f"Оплатите до {pay_until.strftime('%d.%m.%Y')}. УК Северный квартал")
    return Sms(normalize_phone(phone), fit(text))


def reading_reminder(phone: str, account: str, day_from: int = 20, day_to: int = 25) -> Sms:
    text = f"Передайте показания счётчиков по л/с {account} с {day_from} по {day_to} число. УК Северный квартал"
    return Sms(normalize_phone(phone), fit(text))
'''

_FILES['kvartplata/statement.py'] = r'''"""Выписка из лицевого счёта за год.

Выписка показывает помесячно: начислено (всего и по услугам), оплачено (по дате
платежа), сальдо на конец месяца. Используется при обращениях жильцов и для
приложений к исковым заявлениям.

Начисления берутся из сохранённых платёжных документов (:class:`billing.Bill`),
платежи — из :class:`ledger.Ledger`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from .billing import Bill
from .ledger import Ledger
from .models import SERVICE_TITLES, SERVICES
from .money import format_rub, round_money
from .periods import Period, period_range


@dataclass
class StatementRow:
    period: Period
    by_service: dict[str, Decimal] = field(default_factory=dict)
    charged: Decimal = Decimal("0.00")
    paid: Decimal = Decimal("0.00")
    closing: Decimal = Decimal("0.00")


@dataclass
class Statement:
    account: str
    start: Period
    end: Period
    opening: Decimal
    rows: list[StatementRow]

    @property
    def total_charged(self) -> Decimal:
        return round_money(sum((r.charged for r in self.rows), Decimal(0)))

    @property
    def total_paid(self) -> Decimal:
        return round_money(sum((r.paid for r in self.rows), Decimal(0)))

    def service_totals(self) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        for row in self.rows:
            for service, amount in row.by_service.items():
                totals[service] += amount
        return {s: round_money(totals[s]) for s in SERVICES if s in totals}


def build(ledger: Ledger, bills: list[Bill], start: Period, end: Period) -> Statement:
    """Собрать выписку по документам ``bills`` (по одному на период) и платежам из ``ledger``."""
    by_period = {}
    for bill in bills:
        if bill.account.number != ledger.account:
            raise ValueError(f"документ другого лицевого счёта: {bill.account.number}")
        if bill.period in by_period:
            raise ValueError(f"два документа за период {bill.period}")
        by_period[bill.period] = bill
    opening_charged = sum((b.charged for p, b in by_period.items() if p < start), Decimal(0))
    opening_paid = sum((x.amount for x in ledger.payments if x.paid_on < start.first_day), Decimal(0))
    balance = round_money(opening_charged - opening_paid)
    statement = Statement(ledger.account, start, end, balance, [])
    for period in period_range(start, end):
        row = StatementRow(period)
        bill = by_period.get(period)
        if bill is not None:
            for c in bill.charges:
                row.by_service[c.service] = round_money(row.by_service.get(c.service, Decimal(0)) + c.total)
            row.charged = bill.charged
        row.paid = round_money(sum((x.amount for x in ledger.payments if period.contains(x.paid_on)), Decimal(0)))
        balance = round_money(balance + row.charged - row.paid)
        row.closing = balance
        statement.rows.append(row)
    return statement


def render(statement: Statement) -> str:
    lines = [
        f"ВЫПИСКА ИЗ ЛИЦЕВОГО СЧЁТА {statement.account}",
        f"за период {statement.start.human()} — {statement.end.human()}",
        f"Сальдо на начало: {format_rub(statement.opening)}",
        "",
        f"{'Период':<16}{'Начислено':>14}{'Оплачено':>14}{'Сальдо':>14}",
    ]
    for row in statement.rows:
        lines.append(f"{row.period.human():<16}{format_rub(row.charged, suffix=''):>14}"
                     f"{format_rub(row.paid, suffix=''):>14}{format_rub(row.closing, suffix=''):>14}")
    lines += ["", "Начислено по услугам:"]
    for service, amount in statement.service_totals().items():
        lines.append(f"  {SERVICE_TITLES[service]:<36}{format_rub(amount, suffix=''):>14}")
    lines += [
        "",
        f"Всего начислено: {format_rub(statement.total_charged)}",
        f"Всего оплачено: {format_rub(statement.total_paid)}",
        f"Сальдо на конец: {format_rub(statement.rows[-1].closing if statement.rows else statement.opening)}",
    ]
    return "\n".join(lines) + "\n"
'''

_FILES['kvartplata/storage.py'] = r'''"""Хранение данных в JSON-файле (демо-режим и тесты).

Структура файла::

    {"accounts": [...], "readings": [...], "payments": [...], "absences": [...]}

Денежные суммы и показания хранятся строками, даты — в ISO-формате.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from .models import Absence, Account, Meter, Payment, Reading, Resident
from .periods import Period


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def account_from_dict(data: dict) -> Account:
    return Account(
        number=data["number"],
        address=data.get("address", ""),
        total_area=Decimal(data["total_area"]),
        heated_area=Decimal(data.get("heated_area", data["total_area"])),
        residents=[
            Resident(
                full_name=r["full_name"],
                birth_date=date.fromisoformat(r["birth_date"]),
                registered=r.get("registered", True),
                owner=r.get("owner", False),
                privileges=list(r.get("privileges", [])),
                lives_alone=r.get("lives_alone", False),
            )
            for r in data.get("residents", [])
        ],
        meters=[
            Meter(m["meter_id"], m["service"], m.get("zone", "single"), m.get("capacity", 99999),
                  _d(m.get("installed_on")))
            for m in data.get("meters", [])
        ],
        owners_count=data.get("owners_count", 1),
        has_gas=data.get("has_gas", False),
        electric_stove=data.get("electric_stove", False),
        meter_impossible=set(data.get("meter_impossible", [])),
        house_id=data.get("house_id", ""),
    )


def account_to_dict(acc: Account) -> dict:
    return {
        "number": acc.number,
        "address": acc.address,
        "total_area": str(acc.total_area),
        "heated_area": str(acc.heated_area),
        "residents": [
            {"full_name": r.full_name, "birth_date": r.birth_date.isoformat(), "registered": r.registered,
             "owner": r.owner, "privileges": r.privileges, "lives_alone": r.lives_alone}
            for r in acc.residents
        ],
        "meters": [
            {"meter_id": m.meter_id, "service": m.service, "zone": m.zone, "capacity": m.capacity,
             "installed_on": m.installed_on.isoformat() if m.installed_on else None}
            for m in acc.meters
        ],
        "owners_count": acc.owners_count,
        "has_gas": acc.has_gas,
        "electric_stove": acc.electric_stove,
        "meter_impossible": sorted(acc.meter_impossible),
        "house_id": acc.house_id,
    }


class Store:
    def __init__(self) -> None:
        self.accounts: dict[str, Account] = {}
        self.readings: list[Reading] = []
        self.payments: list[Payment] = []
        self.absences: dict[str, list[Absence]] = {}

    @classmethod
    def load(cls, path: str | Path) -> "Store":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        store = cls()
        for item in data.get("accounts", []):
            acc = account_from_dict(item)
            store.accounts[acc.number] = acc
        for item in data.get("readings", []):
            store.readings.append(Reading(item["meter_id"], Period.parse(item["period"]), Decimal(item["value"]),
                                          _d(item.get("taken_on")), item.get("source", "resident")))
        for item in data.get("payments", []):
            store.payments.append(Payment(item["account"], date.fromisoformat(item["paid_on"]),
                                          Decimal(item["amount"]), item.get("reference", "")))
        for item in data.get("absences", []):
            store.absences.setdefault(item["account"], []).append(
                Absence(item["resident"], date.fromisoformat(item["start"]), date.fromisoformat(item["end"]),
                        item.get("documented", True)))
        return store

    def save(self, path: str | Path) -> None:
        data = {
            "accounts": [account_to_dict(a) for a in self.accounts.values()],
            "readings": [{"meter_id": r.meter_id, "period": str(r.period), "value": str(r.value),
                          "taken_on": r.taken_on.isoformat() if r.taken_on else None, "source": r.source}
                         for r in self.readings],
            "payments": [{"account": p.account, "paid_on": p.paid_on.isoformat(), "amount": str(p.amount),
                          "reference": p.reference} for p in self.payments],
            "absences": [{"account": acc, "resident": a.resident, "start": a.start.isoformat(),
                          "end": a.end.isoformat(), "documented": a.documented}
                         for acc, items in self.absences.items() for a in items],
        }
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
'''

_FILES['kvartplata/subsidies.py'] = r'''"""Субсидии на оплату жилого помещения и коммунальных услуг.

Субсидия предоставляется, если расходы семьи на оплату ЖКУ в пределах
регионального стандарта превышают максимально допустимую долю совокупного
дохода семьи. Упрощённая формула (без учёта льгот):

    СС = ССЖКУ − МДД × СД × ПМ_коэфф,

где ССЖКУ — региональный стандарт стоимости ЖКУ на семью (стандарт на 1 м²
× социальная норма площади × число членов семьи), МДД — максимально
допустимая доля расходов (22 %), СД — совокупный доход семьи в месяц,
ПМ_коэфф — поправочный коэффициент: если среднедушевой доход ниже
прожиточного минимума, МДД уменьшается пропорционально отношению
среднедушевого дохода к прожиточному минимуму.

Субсидия не может превышать фактические расходы семьи на оплату ЖКУ.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .money import floor_money, round_money, to_money

MAX_SHARE = Decimal("0.22")


@dataclass
class Family:
    members: int
    monthly_income: Decimal
    single_pensioner: bool = False


@dataclass
class SubsidyParams:
    standard_per_m2: Decimal = Decimal("128.40")  # руб. за м² в месяц
    norm_area_per_person: Decimal = Decimal("18")
    norm_area_single: Decimal = Decimal("33")
    norm_area_couple: Decimal = Decimal("42")
    subsistence_level: Decimal = Decimal("17733")  # прожиточный минимум на душу, руб.
    max_share: Decimal = MAX_SHARE


def standard_area(family: Family, params: SubsidyParams) -> Decimal:
    if family.members == 1:
        return params.norm_area_single
    if family.members == 2:
        return params.norm_area_couple
    return params.norm_area_per_person * family.members


def regional_standard_cost(family: Family, params: SubsidyParams) -> Decimal:
    return round_money(params.standard_per_m2 * standard_area(family, params))


def effective_share(family: Family, params: SubsidyParams) -> Decimal:
    """МДД с поправкой на низкий среднедушевой доход."""
    if family.members <= 0:
        raise ValueError("в семье должен быть хотя бы один человек")
    per_capita = to_money(family.monthly_income) / family.members
    if per_capita >= params.subsistence_level:
        return params.max_share
    return (params.max_share * per_capita / params.subsistence_level).quantize(Decimal("0.0001"))


def subsidy(family: Family, actual_costs, params: SubsidyParams | None = None) -> Decimal:
    """Размер субсидии в месяц (руб.)."""
    params = params or SubsidyParams()
    costs = to_money(actual_costs)
    if costs <= 0:
        return Decimal("0.00")
    standard = regional_standard_cost(family, params)
    base = min(standard, costs)
    own = to_money(family.monthly_income) * effective_share(family, params)
    value = base - own
    if value <= 0:
        return Decimal("0.00")
    return min(floor_money(value), costs)


def explain(family: Family, actual_costs, params: SubsidyParams | None = None) -> str:
    params = params or SubsidyParams()
    lines = [
        f"Членов семьи: {family.members}",
        f"Совокупный доход: {round_money(family.monthly_income)} руб.",
        f"Стандарт площади: {standard_area(family, params)} м²",
        f"Региональный стандарт стоимости: {regional_standard_cost(family, params)} руб.",
        f"Максимальная доля расходов: {effective_share(family, params) * 100:.2f} %",
        f"Фактические расходы: {round_money(actual_costs)} руб.",
        f"Субсидия: {subsidy(family, actual_costs, params)} руб.",
    ]
    return "\n".join(lines)
'''

_FILES['kvartplata/tariffs.py'] = r'''"""Тарифы на коммунальные ресурсы.

Тариф задаётся для услуги (и, для электроэнергии, для зоны суток) с даты
начала действия. Новый тариф вступает в силу с указанной даты включительно
и действует до начала следующего. Тарифы индексируются, как правило, 1 июля.

Формат CSV-файла тарифов::

    service;zone;start;rate
    cold_water;single;2026-01-01;52,31
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .money import to_money
from .periods import Period


@dataclass(frozen=True)
class Tariff:
    service: str
    zone: str
    start: date
    rate: Decimal


class TariffError(LookupError):
    pass


class TariffTable:
    def __init__(self, tariffs: list[Tariff] | None = None) -> None:
        self._items: list[Tariff] = sorted(tariffs or [], key=lambda t: (t.service, t.zone, t.start))

    def add(self, service: str, start: date, rate, zone: str = "single") -> None:
        self._items.append(Tariff(service, zone, start, to_money(rate)))
        self._items.sort(key=lambda t: (t.service, t.zone, t.start))

    def __len__(self) -> int:
        return len(self._items)

    def history(self, service: str, zone: str = "single") -> list[Tariff]:
        return [t for t in self._items if t.service == service and t.zone == zone]

    def rate_for(self, service: str, on: date, zone: str = "single") -> Decimal:
        """Тариф, действующий на дату ``on``."""
        current: Tariff | None = None
        for tariff in self.history(service, zone):
            # <<C08
            if on > tariff.start:
                current = tariff
            # ==
            if on >= tariff.start:
                current = tariff
            # >>
        if current is None:
            raise TariffError(f"нет тарифа {service}/{zone} на {on.isoformat()}")
        return current.rate

    def rate_for_period(self, service: str, period: Period, zone: str = "single") -> Decimal:
        """Тариф для расчётного периода — действующий на первое число месяца."""
        return self.rate_for(service, period.first_day, zone)

    @classmethod
    def from_csv(cls, text: str) -> "TariffTable":
        reader = csv.DictReader(io.StringIO(text), delimiter=";")
        items = []
        for row in reader:
            items.append(
                Tariff(
                    service=row["service"].strip(),
                    zone=(row.get("zone") or "single").strip(),
                    start=date.fromisoformat(row["start"].strip()),
                    rate=to_money(row["rate"].strip().replace(",", ".")),
                )
            )
        return cls(items)


def default_tariffs() -> TariffTable:
    """Тарифы 2025–2026 для тестов и демонстрации."""
    table = TariffTable()
    for service, zone, rows in (
        ("cold_water", "single", [(date(2025, 7, 1), "48.20"), (date(2026, 1, 1), "52.31")]),
        ("hot_water", "single", [(date(2025, 7, 1), "41.10"), (date(2026, 1, 1), "44.64")]),
        ("heat", "single", [(date(2025, 7, 1), "2810.55"), (date(2026, 1, 1), "3044.18")]),
        ("sewage", "single", [(date(2025, 7, 1), "39.90"), (date(2026, 1, 1), "43.27")]),
        ("electricity", "single", [(date(2025, 7, 1), "6.43"), (date(2026, 1, 1), "6.98")]),
        ("electricity", "day", [(date(2025, 7, 1), "7.33"), (date(2026, 1, 1), "7.95")]),
        ("electricity", "night", [(date(2025, 7, 1), "3.22"), (date(2026, 1, 1), "3.49")]),
        ("gas", "single", [(date(2025, 7, 1), "9.12"), (date(2026, 1, 1), "9.88")]),
    ):
        for start, rate in rows:
            table.add(service, start, rate, zone)
    table.add("cold_water", date(2026, 7, 1), "56.02")
    table.add("hot_water", date(2026, 7, 1), "47.80")
    table.add("heat", date(2026, 7, 1), "3259.90")
    return table
'''

_FILES['kvartplata/tenants.py'] = r'''"""Изменения в составе зарегистрированных в течение периода.

Если жилец зарегистрирован или снят с регистрационного учёта в середине месяца,
начисления «по числу проживающих» (нормативы, ТКО) считаются пропорционально дням:
число человек за период = сумма по жильцам (дни регистрации в периоде / дни в периоде).
Результат округляется до четырёх знаков и используется калькуляторами как дробное
число человек.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .periods import Period, overlap_days


@dataclass
class Registration:
    resident: str
    start: date
    end: date | None = None  # None — зарегистрирован по настоящее время

    def active_on(self, day: date) -> bool:
        return self.start <= day and (self.end is None or day <= self.end)


def person_days(regs: list[Registration], period: Period) -> int:
    total = 0
    for reg in regs:
        end = reg.end or period.last_day
        total += overlap_days(period, reg.start, end)
    return total


def persons_in_period(regs: list[Registration], period: Period) -> Decimal:
    """Среднее число зарегистрированных за период (дробное)."""
    return (Decimal(person_days(regs, period)) / period.days).quantize(Decimal("0.0001"))


def registered_on(regs: list[Registration], day: date) -> list[str]:
    return sorted({r.resident for r in regs if r.active_on(day)})


def changes_in_period(regs: list[Registration], period: Period) -> list[str]:
    """Человекочитаемый список изменений состава за период (для примечания в квитанции)."""
    out = []
    for reg in sorted(regs, key=lambda r: (r.start, r.resident)):
        if period.contains(reg.start):
            out.append(f"{reg.resident}: регистрация с {reg.start.strftime('%d.%m.%Y')}")
        if reg.end and period.contains(reg.end):
            out.append(f"{reg.resident}: снят(а) с учёта {reg.end.strftime('%d.%m.%Y')}")
    return out
'''

_FILES['kvartplata/validators.py'] = r'''"""Проверки реквизитов: номер лицевого счёта, ИНН, телефон, e-mail."""

from __future__ import annotations

import re


class ValidationError(ValueError):
    pass


# Номер лицевого счёта: 11 цифр — 10 значащих и контрольная.
ACCOUNT_RE = re.compile(r"^\d{11}$")
ACCOUNT_WEIGHTS = (1, 3)


def account_control_digit(body: str) -> int:
    """Контрольная цифра для 10 значащих цифр номера лицевого счёта.

    Цифры умножаются на веса 1, 3, 1, 3, … начиная с первой (левой) цифры,
    контрольная цифра дополняет сумму до кратной 10.
    """
    if len(body) != 10 or not body.isdigit():
        raise ValidationError("ожидается 10 цифр")
    # <<C34
    digits = [int(ch) for ch in reversed(body)]
    # ==
    digits = [int(ch) for ch in body]
    # >>
    total = sum(d * ACCOUNT_WEIGHTS[i % 2] for i, d in enumerate(digits))
    return (10 - total % 10) % 10


def is_valid_account(number: str) -> bool:
    number = number.strip()
    if not ACCOUNT_RE.match(number):
        return False
    return account_control_digit(number[:10]) == int(number[10])


def make_account_number(body: str) -> str:
    return body + str(account_control_digit(body))


_INN10_WEIGHTS = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_WEIGHTS_1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_WEIGHTS_2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


def _inn_check(digits: list[int], weights: tuple[int, ...]) -> int:
    return sum(d * w for d, w in zip(digits, weights)) % 11 % 10


def is_valid_inn(inn: str) -> bool:
    """ИНН юрлица (10 цифр) или физлица (12 цифр)."""
    inn = inn.strip()
    if not inn.isdigit():
        return False
    digits = [int(ch) for ch in inn]
    if len(digits) == 10:
        return _inn_check(digits, _INN10_WEIGHTS) == digits[9]
    if len(digits) == 12:
        return (_inn_check(digits, _INN12_WEIGHTS_1) == digits[10]
                and _inn_check(digits, _INN12_WEIGHTS_2) == digits[11])
    return False


def normalize_phone(raw: str) -> str:
    """Телефон в формат +7XXXXXXXXXX; 8XXXXXXXXXX и 7XXXXXXXXXX тоже принимаются."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10:
        raise ValidationError(f"некорректный телефон: {raw!r}")
    return "+7" + digits


EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value.strip()))
'''

_FILES['kvartplata/verification.py'] = r'''"""Межповерочные интервалы приборов учёта.

Показания прибора с истёкшим сроком поверки считаются недостоверными.
Модуль используется для отчёта «приборы, требующие поверки» и для
предупреждений в квитанции; сам расчёт объёма этот модуль не меняет.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import Account, Meter

# межповерочный интервал, лет
INTERVALS = {
    "cold_water": 6,
    "hot_water": 4,
    "electricity": 16,
    "gas": 10,
}
WARN_DAYS = 60


def add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 февраля
        return day.replace(year=day.year + years, day=28)


def next_verification(meter: Meter) -> date | None:
    if meter.installed_on is None:
        return None
    years = INTERVALS.get(meter.service)
    if years is None:
        return None
    return add_years(meter.installed_on, years)


def is_expired(meter: Meter, on: date) -> bool:
    nxt = next_verification(meter)
    return nxt is not None and on >= nxt


@dataclass
class VerificationRow:
    account: str
    meter_id: str
    service: str
    due: date
    status: str  # expired | soon


def verification_report(accounts: list[Account], on: date) -> list[VerificationRow]:
    rows = []
    for acc in accounts:
        for meter in acc.meters:
            nxt = next_verification(meter)
            if nxt is None:
                continue
            if on >= nxt:
                rows.append(VerificationRow(acc.number, meter.meter_id, meter.service, nxt, "expired"))
            elif (nxt - on).days <= WARN_DAYS:
                rows.append(VerificationRow(acc.number, meter.meter_id, meter.service, nxt, "soon"))
    rows.sort(key=lambda r: (r.due, r.account, r.meter_id))
    return rows
'''

_FILES['kvartplata/words.py'] = r'''"""Сумма прописью (для квитанций и актов сверки)."""

from __future__ import annotations

from decimal import Decimal

from .money import round_money

_UNITS_M = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_UNITS_F = ["", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
          "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят",
         "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот",
             "девятьсот"]
_SCALES = [
    (("рубль", "рубля", "рублей"), False),
    (("тысяча", "тысячи", "тысяч"), True),
    (("миллион", "миллиона", "миллионов"), False),
    (("миллиард", "миллиарда", "миллиардов"), False),
]


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """Форма слова для числа: 1 рубль, 2 рубля, 5 рублей."""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def _triad(n: int, feminine: bool) -> list[str]:
    words = []
    h, rest = divmod(n, 100)
    if h:
        words.append(_HUNDREDS[h])
    if 10 <= rest <= 19:
        words.append(_TEENS[rest - 10])
    else:
        t, u = divmod(rest, 10)
        if t:
            words.append(_TENS[t])
        if u:
            words.append((_UNITS_F if feminine else _UNITS_M)[u])
    return words


def number_in_words(n: int, feminine: bool = False) -> str:
    if n == 0:
        return "ноль"
    if n < 0:
        return "минус " + number_in_words(-n, feminine)
    parts = []
    scale = 0
    while n:
        n, triad = divmod(n, 1000)
        if triad:
            forms, fem = _SCALES[scale]
            words = _triad(triad, fem if scale else feminine)
            if scale:
                words.append(plural(triad, forms))
            parts.insert(0, " ".join(words))
        scale += 1
    return " ".join(parts)


def amount_in_words(value) -> str:
    """«1 234,05» → «Одна тысяча двести тридцать четыре рубля 05 копеек»."""
    amount = round_money(value)
    rub = int(amount)
    kop = int((amount - rub) * 100)
    text = f"{number_in_words(rub)} {plural(rub, _SCALES[0][0])} {kop:02d} {plural(kop, ('копейка', 'копейки', 'копеек'))}"
    return text[0].upper() + text[1:]


def is_integer_amount(value) -> bool:
    return round_money(value) == round_money(value).to_integral_value()


def decimal_words(value: Decimal) -> str:
    return amount_in_words(value)
'''

_FILES['kvartplata/closing.py'] = r'''"""Закрытие лицевого счёта при смене собственника.

При продаже помещения лицевой счёт прежнего собственника закрывается датой
перехода права собственности (``closed_on``). За месяц закрытия начисления
по площади и по числу жильцов (:data:`PRORATED`) пересчитываются
пропорционально дням владения; начисления по приборам учёта считаются по
контрольным показаниям на дату передачи и не пересчитываются. Итоговый
документ содержит начисления за неполный месяц, сальдо и переплату
к возврату (возврат оформляется через :mod:`kvartplata.refunds`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .ledger import Ledger
from .models import Charge
from .money import ZERO, format_rub, round_money
from .periods import Period

PRORATED = ("maintenance", "capital_repair", "heating", "waste")
# <<R09
# ==
PRORATED = ("maintenance", "capital_repair", "waste")
# >>


def owned_days(period: Period, closed_on: date) -> int:
    """Сколько дней периода помещение принадлежало прежнему собственнику."""
    if not period.contains(closed_on):
        raise ValueError(f"дата закрытия {closed_on} вне периода {period}")
    # <<C51
    return closed_on.day - 1
    # ==
    return closed_on.day
    # >>


def month_days(period: Period) -> int:
    """Число дней, на которое делится месячное начисление."""
    # <<C50
    return 30
    # ==
    return period.days
    # >>


def owned_share(period: Period, closed_on: date) -> Decimal:
    """Доля месяца, за которую платит прежний собственник."""
    return Decimal(owned_days(period, closed_on)) / Decimal(month_days(period))


def prorate(charges: list[Charge], period: Period, closed_on: date) -> list[Charge]:
    """Пересчитать начисления месяца закрытия пропорционально дням владения."""
    share = owned_share(period, closed_on)
    for charge in charges:
        if charge.service not in PRORATED:
            continue
        charge.amount = round_money(charge.amount * share)
        # <<C52
        # ==
        charge.discount = round_money(charge.discount * share)
        # >>
        note = f"за {owned_days(period, closed_on)} дн. владения"
        charge.note = f"{charge.note}; {note}" if charge.note else note
    return charges


@dataclass
class FinalStatement:
    account: str
    period: Period
    closed_on: date
    charges: list[Charge] = field(default_factory=list)
    balance: Decimal = ZERO

    @property
    def charged(self) -> Decimal:
        return round_money(sum((c.total for c in self.charges), Decimal(0)))

    @property
    def to_pay(self) -> Decimal:
        return round_money(max(self.balance + self.charged, ZERO))

    @property
    def to_refund(self) -> Decimal:
        return round_money(max(-(self.balance + self.charged), ZERO))


def final_statement(ledger: Ledger, charges: list[Charge], period: Period, closed_on: date) -> FinalStatement:
    """Итоговый расчёт при закрытии счёта: начисления месяца закрытия плюс сальдо на дату закрытия."""
    prorate(charges, period, closed_on)
    charged = ledger.charged_until(period.shift(-1))
    paid = ledger.paid_until(closed_on)
    return FinalStatement(ledger.account, period, closed_on, charges, round_money(charged - paid))


def render(st: FinalStatement) -> str:
    """Текст итогового документа."""
    # <<C53
    closed = st.closed_on.isoformat()
    # ==
    closed = st.closed_on.strftime("%d.%m.%Y")
    # >>
    lines = [f"Итоговый расчёт по л/с {st.account}", f"Счёт закрыт {closed} (смена собственника)", ""]
    for c in st.charges:
        lines.append(f"{c.service:<16} {format_rub(c.total, suffix=''):>12}  {c.note}")
    lines += ["", f"Сальдо на дату закрытия: {format_rub(st.balance)}"]
    if st.to_refund > 0:
        lines.append(f"Переплата к возврату: {format_rub(st.to_refund)}")
    else:
        lines.append(f"Итого к оплате: {format_rub(st.to_pay)}")
    return "\n".join(lines) + "\n"
'''

_FILES['kvartplata/owners.py'] = r'''"""Раздельные платёжные документы для долевых собственников.

Если помещение находится в долевой собственности и собственники подали
соглашение о порядке оплаты, сумма платёжного документа делится между ними
пропорционально долям. Доли записываются дробью («1/3») или целым числом
(«1» — вся квартира у одного собственника). Строка соглашения в карточке
лицевого счёта: ``Иванов И. И. — 1/2; Иванова А. П. — 1/2``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

# <<C44
from .money import format_rub, round_money
# ==
from .money import allocate_by_weights, format_rub, round_money
# >>

SEPARATORS = ("—", "–", " - ")


@dataclass
class Share:
    owner: str
    part: Fraction

    @property
    def weight(self) -> Decimal:
        return Decimal(self.part.numerator) / Decimal(self.part.denominator)


def parse_share(text: str) -> Fraction:
    """Разобрать долю: «1/3», «1»."""
    raw = text.strip().replace(" ", "")
    try:
        # <<C43
        if "/" in raw:
        # ==
        if raw.endswith("%"):
            value = Fraction(raw[:-1].replace(",", ".")) / 100
        elif "/" in raw:
        # >>
            num, den = raw.split("/", 1)
            value = Fraction(int(num), int(den))
        else:
            value = Fraction(int(raw))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"не удаётся разобрать долю: {text!r}") from exc
    if not 0 < value <= 1:
        raise ValueError(f"доля должна быть больше 0 и не больше 1: {text!r}")
    return value


def _split_owner(chunk: str) -> tuple[str, str]:
    for sep in SEPARATORS:
        if sep in chunk:
            owner, _, part = chunk.rpartition(sep)
            return owner.strip(), part.strip()
    raise ValueError(f"не указана доля: {chunk.strip()!r}")


def parse_shares(text: str) -> list[Share]:
    """Разобрать строку соглашения о порядке оплаты."""
    shares = []
    for chunk in text.split(";"):
        if not chunk.strip():
            continue
        owner, part = _split_owner(chunk)
        if not owner:
            raise ValueError(f"не указан собственник: {chunk.strip()!r}")
        shares.append(Share(owner, parse_share(part)))
    # <<C45
    # ==
    merged: dict[str, Fraction] = {}
    for share in shares:
        merged[share.owner] = merged.get(share.owner, Fraction(0)) + share.part
    shares = [Share(owner, part) for owner, part in merged.items()]
    # >>
    return shares


def validate(shares: list[Share]) -> None:
    """Проверить, что доли в сумме составляют всю квартиру."""
    if not shares:
        raise ValueError("нет ни одного собственника")
    total = sum((s.part for s in shares), Fraction(0))
    # <<R08
    if total != 1:
    # ==
    if total > 1:
    # >>
        raise ValueError(f"сумма долей равна {total}, а должна быть 1")


def split_amount(total, shares: list[Share]) -> list[tuple[str, Decimal]]:
    """Разделить сумму документа между собственниками; сумма частей равна ``total``."""
    validate(shares)
    amount = round_money(total)
    # <<C44
    result = []
    left = amount
    for share in shares[:-1]:
        part = round_money(amount * share.weight)
        result.append((share.owner, part))
        left -= part
    result.append((shares[-1].owner, left))
    return result
    # ==
    parts = allocate_by_weights(amount, [s.weight for s in shares])
    return [(s.owner, part) for s, part in zip(shares, parts)]
    # >>


def render(account: str, total, shares: list[Share]) -> str:
    """Расшифровка раздела суммы для приложения к платёжным документам."""
    lines = [f"Раздел платы по л/с {account} между собственниками", f"Всего к оплате: {format_rub(total)}", ""]
    by_owner = dict((s.owner, s) for s in shares)
    for owner, amount in split_amount(total, shares):
        part = by_owner[owner].part
        lines.append(f"{owner:<32} {part.numerator}/{part.denominator:<4} {format_rub(amount):>16}")
    return "\n".join(lines) + "\n"


def describe(shares: list[Share]) -> str:
    """Строка соглашения в каноническом виде (обратная к :func:`parse_shares`)."""
    return "; ".join(f"{s.owner} — {s.part.numerator}/{s.part.denominator}" for s in shares)


def largest_owner(shares: list[Share]) -> str:
    """Собственник наибольшей доли (при равенстве — первый по соглашению); ему уходят уведомления."""
    if not shares:
        raise ValueError("нет ни одного собственника")
    best = shares[0]
    for share in shares[1:]:
        if share.part > best.part:
            best = share
    return best.owner
'''

_FILES['kvartplata/quality.py'] = r'''"""Снижение платы за перерывы в предоставлении коммунальных услуг.

Перерыв (:class:`Outage`) фиксирует аварийно-диспетчерская служба либо сам
жилец (заявка через личный кабинет). Для каждой услуги установлена допустимая
продолжительность перерыва — суммарно за расчётный период и однократно
(:data:`ALLOWED_HOURS`). За каждый час превышения допустимой продолжительности
плата за услугу за расчётный период снижается на :data:`REDUCTION_PER_HOUR`
процента (от суммы начисления по тарифу, до льгот и перерасчётов).

Снижение отражается в строке начисления как отрицательный перерасчёт
(``Charge.recalculation``), основание — в примечании строки.
"""

from __future__ import annotations

# <<C36
# ==
import math
# >>
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .models import Charge
from .money import ZERO, percent_of, round_money
from .periods import Period

REDUCTION_PER_HOUR = Decimal("0.15")  # процента платы за период за каждый час превышения

# Допустимая продолжительность перерыва, часов: (суммарно за период, однократно).
ALLOWED_HOURS: dict[str, tuple[int, int]] = {
    "cold_water": (8, 4),
    "hot_water": (8, 4),
    "sewage": (8, 8),
    "electricity": (72, 24),
    "heating": (24, 16),
    "gas": (4, 4),
}


@dataclass
class Outage:
    """Перерыв в предоставлении услуги."""

    service: str
    start: datetime
    end: datetime
    source: str = "dispatcher"  # dispatcher | resident
    act: str = ""  # номер акта о непредоставлении услуги

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("окончание перерыва раньше начала")

    @property
    def confirmed(self) -> bool:
        """Перерыв подтверждён: зафиксирован диспетчерской или по нему составлен акт."""
        return self.source == "dispatcher" or bool(self.act)


def period_bounds(period: Period) -> tuple[datetime, datetime]:
    """Начало периода и начало следующего периода."""
    nxt = period.next()
    return datetime(period.year, period.month, 1), datetime(nxt.year, nxt.month, 1)


def outage_hours(outage: Outage, period: Period) -> int:
    """Продолжительность перерыва в расчётном периоде, часов."""
    # <<C38
    seconds = (outage.end - outage.start).total_seconds()
    # ==
    lo, hi = period_bounds(period)
    seconds = max((min(outage.end, hi) - max(outage.start, lo)).total_seconds(), 0)
    # >>
    # <<C36
    return int(seconds // 3600)
    # ==
    return math.ceil(seconds / 3600)
    # >>


def relevant(outages: list[Outage], service: str) -> list[Outage]:
    """Перерывы по услуге, которые учитываются при снижении платы."""
    found = [o for o in outages if o.service == service]
    # <<R06
    found = [o for o in found if o.confirmed]
    # ==
    # >>
    return found


def excess_hours(outages: list[Outage], service: str, period: Period) -> int:
    """Часы превышения допустимой продолжительности перерывов по услуге за период."""
    if service not in ALLOWED_HOURS:
        return 0
    month_limit, single_limit = ALLOWED_HOURS[service]
    hours = [outage_hours(o, period) for o in relevant(outages, service)]
    total = max(sum(hours) - month_limit, 0)
    # <<C37
    return total
    # ==
    single = sum(max(h - single_limit, 0) for h in hours)
    return max(total, single)
    # >>


def reduction(charge: Charge, outages: list[Outage], period: Period) -> Decimal:
    """Сумма снижения платы по строке начисления."""
    hours = excess_hours(outages, charge.service, period)
    if not hours:
        return ZERO
    value = percent_of(charge.amount, REDUCTION_PER_HOUR * hours)
    # <<C39
    return value
    # ==
    return min(value, round_money(charge.amount))
    # >>


def apply_reductions(charges: list[Charge], outages: list[Outage], period: Period) -> list[Charge]:
    """Отразить снижение платы в строках начислений (отрицательный перерасчёт)."""
    for charge in charges:
        value = reduction(charge, outages, period)
        if value <= 0:
            continue
        hours = excess_hours(outages, charge.service, period)
        charge.recalculation = round_money(charge.recalculation - value)
        note = f"снижение за перерывы сверх допустимых: {hours} ч"
        charge.note = f"{charge.note}; {note}" if charge.note else note
    return charges


def journal(outages: list[Outage], period: Period) -> str:
    """Журнал перерывов за период (для контролёров)."""
    lines = [f"Перерывы в предоставлении услуг за {period.human()}", ""]
    for o in sorted(outages, key=lambda x: (x.service, x.start)):
        mark = "" if o.confirmed else "  (не подтверждён)"
        lines.append(f"{o.service:<12} {o.start:%d.%m %H:%M} — {o.end:%d.%m %H:%M}  "
                     f"{outage_hours(o, period):>4} ч{mark}")
    return "\n".join(lines) + "\n"


def summary(outages: list[Outage], period: Period) -> dict[str, tuple[int, int]]:
    """Сводка по услугам: (часы перерывов, часы превышения) за период."""
    out: dict[str, tuple[int, int]] = {}
    for service in sorted({o.service for o in outages}):
        hours = sum(outage_hours(o, period) for o in relevant(outages, service))
        out[service] = (hours, excess_hours(outages, service, period))
    return out


LOG_SERVICES = {
    "ХВС": "cold_water",
    "ГВС": "hot_water",
    "КАН": "sewage",
    "ЭЛ": "electricity",
    "ОТОП": "heating",
    "ГАЗ": "gas",
}


def _log_time(raw: str) -> datetime:
    raw = raw.strip()
    # <<C54
    return datetime.fromisoformat(raw)
    # ==
    try:
        return datetime.strptime(raw, "%d.%m.%Y %H:%M")
    except ValueError:
        return datetime.fromisoformat(raw)
    # >>


def parse_dispatch_log(text: str) -> list[Outage]:
    """Разобрать выгрузку журнала аварийно-диспетчерской службы.

    Формат: строки ``услуга;начало;окончание;номер акта``; строки, начинающиеся
    с ``#``, и пустые строки пропускаются. Услуга — код из :data:`LOG_SERVICES`.
    Все перерывы из журнала считаются зафиксированными диспетчерской.
    """
    outages = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 3:
            raise ValueError(f"строка {n}: ожидается не меньше трёх полей")
        code = parts[0].upper()
        if code not in LOG_SERVICES:
            raise ValueError(f"строка {n}: неизвестная услуга {parts[0]!r}")
        act = parts[3] if len(parts) > 3 else ""
        outages.append(Outage(LOG_SERVICES[code], _log_time(parts[1]), _log_time(parts[2]), "dispatcher", act))
    return outages
'''

_FILES['kvartplata/reading_window.py'] = r'''"""Приём показаний приборов учёта: окно передачи и отнесение к периоду.

Показания принимаются с :data:`WINDOW_START` по :data:`WINDOW_END` число
месяца и относятся к текущему расчётному периоду. Показания, переданные после
окончания окна, относятся к следующему периоду; переданные до начала окна —
к текущему. За :data:`REMINDER_DAYS_BEFORE` дня до конца окна жильцам
отправляется напоминание (SMS или push в личном кабинете).
"""

from __future__ import annotations

from datetime import date, timedelta

# <<C48
# ==
from .calendar_ru import next_workday
# >>
from .meters import ReadingBook
from .models import Account, Reading
from .periods import Period

WINDOW_START = 15
WINDOW_END = 25
REMINDER_DAYS_BEFORE = 3


def window(period: Period) -> tuple[date, date]:
    """Первый и последний день приёма показаний за период (включительно)."""
    start = date(period.year, period.month, WINDOW_START)
    end = date(period.year, period.month, WINDOW_END)
    # <<C48
    # ==
    end = next_workday(end)
    # >>
    return start, end


def period_for(day: date) -> Period:
    """Расчётный период, к которому относится показание, переданное в день ``day``."""
    period = Period.of(day)
    _start, end = window(period)
    # <<C47
    if day >= end:
    # ==
    if day > end:
    # >>
        return period.next()
    return period


def accept(reading: Reading, received: date, book: ReadingBook) -> str | None:
    """Причина отказа в приёме показания или ``None``, если показание принимается."""
    if reading.taken_on is not None and reading.taken_on > received:
        return "дата снятия показания позже даты передачи"
    if reading.value < 0:
        return "отрицательное показание"
    previous = book.last_before(reading.meter_id, reading.period)
    if previous is not None and reading.value < previous.value:
        # <<C46
        return "показание меньше предыдущего"
        # ==
        if reading.source != "inspector":
            return "показание меньше предыдущего"
        # >>
    return None


def receive(readings: list[Reading], received: date, book: ReadingBook) -> list[tuple[Reading, str]]:
    """Принять пачку показаний; вернуть отклонённые с причинами.

    Принятые показания записываются в ``book`` в период по дате передачи.
    """
    rejected = []
    period = period_for(received)
    for reading in readings:
        reading = Reading(reading.meter_id, period, reading.value, reading.taken_on, reading.source)
        reason = accept(reading, received, book)
        if reason:
            rejected.append((reading, reason))
        else:
            book.add(reading)
    return rejected


def reminder_date(period: Period) -> date:
    """День отправки напоминания о передаче показаний."""
    _start, end = window(period)
    return end - timedelta(days=REMINDER_DAYS_BEFORE)


def needs_reminder(account: Account, period: Period, book: ReadingBook) -> bool:
    """Нужно ли напоминать жильцу о передаче показаний за период."""
    if not account.meters:
        return False
    # <<C49
    return True
    # ==
    return any(book.get(m.meter_id, period) is None for m in account.meters)
    # >>


def schedule(year: int) -> list[tuple[Period, date, date, date]]:
    """График приёма показаний на год: (период, начало окна, конец окна, день напоминания)."""
    out = []
    for month in range(1, 13):
        period = Period(year, month)
        start, end = window(period)
        out.append((period, start, end, reminder_date(period)))
    return out


def render_schedule(year: int) -> str:
    """График приёма показаний для сайта УК."""
    lines = [f"График приёма показаний приборов учёта на {year} год", ""]
    for period, start, end, _reminder in schedule(year):
        lines.append(f"{period.human():<16} с {start:%d.%m} по {end:%d.%m}")
    return "\n".join(lines) + "\n"
'''

_FILES['kvartplata/refunds.py'] = r'''"""Возврат переплаты по заявлению собственника.

Переплата — превышение оплаченного на дату заявления над начисленным за
периоды, по которым уже выставлены платёжные документы (все периоды до
месяца заявления). Заявление рассматривается в течение
:data:`DECISION_DAYS` дней со дня подачи; возврат выполняется на карту
или банковский счёт заявителя.
"""

from __future__ import annotations

from dataclasses import dataclass
# <<C40
from datetime import date, timedelta
# ==
from datetime import date
# >>
from decimal import Decimal

from .calendar_ru import add_workdays
from .ledger import Ledger
# <<C55
from .money import ZERO, format_rub, round_money, to_money
# ==
from .money import ZERO, format_rub, kopecks, round_money, to_money
# >>
from .periods import Period

MIN_REFUND = Decimal("100.00")
DECISION_DAYS = 10
METHODS = ("card", "bank_account")
# <<R07
# ==
METHODS = ("card", "bank_account", "cash")
# >>


@dataclass
class RefundRequest:
    account: str
    filed_on: date
    amount: Decimal | None = None  # None — вернуть всю переплату
    method: str = "card"
    applicant_is_owner: bool = True
    # <<C42
    # ==
    closing: bool = False  # лицевой счёт закрывается (помещение продано)
    # >>


@dataclass
class RefundDecision:
    approved: bool
    amount: Decimal
    decide_by: date
    reason: str = ""


def overpayment(ledger: Ledger, on: date) -> Decimal:
    """Переплата на дату ``on`` (ноль, если переплаты нет)."""
    billed = ledger.charged_until(Period.of(on).shift(-1))
    value = ledger.paid_until(on) - billed
    return round_money(max(value, ZERO))


def decision_deadline(filed_on: date) -> date:
    """Крайний срок решения по заявлению."""
    # <<C40
    return filed_on + timedelta(days=DECISION_DAYS)
    # ==
    return add_workdays(filed_on, DECISION_DAYS)
    # >>


def decide(request: RefundRequest, ledger: Ledger) -> RefundDecision:
    """Решение по заявлению о возврате переплаты."""
    decide_by = decision_deadline(request.filed_on)

    def reject(reason: str) -> RefundDecision:
        return RefundDecision(False, ZERO, decide_by, reason)

    if request.account != ledger.account:
        return reject("заявление по другому лицевому счёту")
    if not request.applicant_is_owner:
        return reject("заявитель не является собственником")
    if request.method not in METHODS:
        return reject("способ возврата не поддерживается")
    available = overpayment(ledger, request.filed_on)
    if available <= 0:
        return reject("переплаты нет")
    amount = available if request.amount is None else to_money(request.amount)
    if amount <= 0:
        return reject("некорректная сумма")
    if amount > available:
        # <<C41
        return reject("запрошенная сумма больше переплаты")
        # ==
        amount = available
        # >>
    # <<C42
    if amount < MIN_REFUND:
    # ==
    if amount < MIN_REFUND and not request.closing:
    # >>
        return reject(f"сумма возврата меньше {format_rub(MIN_REFUND)}")
    return RefundDecision(True, round_money(amount), decide_by)


def render(request: RefundRequest, decision: RefundDecision) -> str:
    """Текст уведомления заявителю."""
    head = f"Заявление о возврате переплаты по л/с {request.account} от {request.filed_on:%d.%m.%Y}"
    if decision.approved:
        body = (f"Решение: возврат {format_rub(decision.amount)} "
                f"({'на карту' if request.method == 'card' else 'на банковский счёт'}).")
    else:
        body = f"Решение: отказ. Причина: {decision.reason}."
    return f"{head}\n{body}\nСрок рассмотрения — до {decision.decide_by:%d.%m.%Y}.\n"


REGISTRY_HEADER = "#REFUND|{date}|{count}|{total}"


def registry_line(request: RefundRequest, decision: RefundDecision, payee: str, bank_account: str) -> str:
    """Строка реестра возвратов для банка: ``R|лицевой счёт|получатель|счёт|сумма``."""
    if not decision.approved:
        raise ValueError("в реестр попадают только одобренные возвраты")
    if request.method != "bank_account":
        raise ValueError("в банковский реестр попадают только возвраты на счёт")
    if len(bank_account) != 20 or not bank_account.isdigit():
        raise ValueError(f"некорректный номер счёта: {bank_account!r}")
    # <<C55
    amount = f"{decision.amount:.2f}"
    # ==
    amount = str(kopecks(decision.amount))
    # >>
    return f"R|{request.account}|{payee.strip()}|{bank_account}|{amount}"


def registry(items: list[tuple[RefundRequest, RefundDecision, str, str]], on: date) -> str:
    """Реестр возвратов на счета за день ``on`` (заголовок и строки)."""
    lines = [registry_line(*item) for item in items]
    total = sum((item[1].amount for item in items), Decimal(0))
    head = REGISTRY_HEADER.format(date=on.strftime("%d.%m.%Y"), count=len(lines), total=f"{total:.2f}")
    return "\n".join([head, *lines]) + "\n"
'''

_FILES['tests/__init__.py'] = r''''''

_FILES['tests/fixtures.py'] = r'''"""Общие данные для тестов."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from kvartplata.config import Settings
from kvartplata.meters import ReadingBook
from kvartplata.models import Account, Meter, Reading, Resident
from kvartplata.periods import Period
from kvartplata.services.base import Context
from kvartplata.tariffs import default_tariffs

MARCH = Period(2026, 3)
FEB = Period(2026, 2)


def resident(name="Петров Пётр Петрович", born=date(1980, 1, 15), **kw) -> Resident:
    return Resident(name, born, **kw)


def flat(**kw) -> Account:
    base = dict(
        number="12345678905",
        address="ул. Садовая, д. 5, кв. 12",
        total_area=D("54.3"),
        heated_area=D("50.1"),
        residents=[resident(owner=True), resident("Петрова Анна Сергеевна", date(1982, 7, 2))],
        meters=[Meter("CW1", "cold_water"), Meter("HW1", "hot_water"), Meter("EL1", "electricity")],
        has_gas=False,
    )
    base.update(kw)
    return Account(**base)


def readings(*items) -> ReadingBook:
    """items: (meter_id, period, value)"""
    return ReadingBook([Reading(m, p, D(str(v))) for m, p, v in items])


def standard_book() -> ReadingBook:
    return readings(
        ("CW1", FEB, "100"), ("CW1", MARCH, "108.5"),
        ("HW1", FEB, "50"), ("HW1", MARCH, "54"),
        ("EL1", FEB, "1000"), ("EL1", MARCH, "1210"),
    )


def ctx(account=None, period=MARCH, book=None, settings=None) -> Context:
    return Context(account or flat(), period, book or standard_book(), default_tariffs(), settings or Settings())
'''

_FILES['tests/test_accounting.py'] = r'''import unittest
from datetime import date, datetime
from decimal import Decimal as D

from kvartplata import corrections, forecast, gis_readings, sms, statement
from kvartplata.billing import compute_bill, compute_charges
from kvartplata.config import Settings
from kvartplata.ledger import Ledger
from kvartplata.models import Payment, Reading
from kvartplata.periods import Period
from kvartplata.periods_lock import PeriodLock, PeriodLockedError
from kvartplata.tariffs import default_tariffs

from tests.fixtures import FEB, MARCH, flat, readings, standard_book


class StatementTest(unittest.TestCase):
    def test_build(self):
        book = standard_book()
        bills = [compute_bill(flat(), p, book, default_tariffs()) for p in (FEB, MARCH)]
        led = Ledger("12345678905")
        led.add_payment(Payment("12345678905", date(2026, 3, 5), D("1000")))
        st = statement.build(led, bills, FEB, MARCH)
        self.assertEqual(st.opening, D("0.00"))
        self.assertEqual(st.rows[1].paid, D("1000.00"))
        self.assertEqual(st.rows[-1].closing, st.total_charged - st.total_paid)
        self.assertIn("ВЫПИСКА ИЗ ЛИЦЕВОГО СЧЁТА 12345678905", statement.render(st))

    def test_duplicate_period(self):
        bill = compute_bill(flat(), MARCH, standard_book(), default_tariffs())
        with self.assertRaises(ValueError):
            statement.build(Ledger("12345678905"), [bill, bill], FEB, MARCH)


class CorrectionsTest(unittest.TestCase):
    def test_small_correction_applies(self):
        corr = corrections.create("12345678905", MARCH, "cold_water", "-120.50", "ОБР-1043", "ivanova",
                                  date(2026, 3, 20))
        charges = compute_charges(flat(), MARCH, standard_book(), default_tariffs())
        corrections.apply(charges, [corr], "12345678905", MARCH)
        cold = next(c for c in charges if c.service == "cold_water")
        self.assertEqual(cold.recalculation, D("-120.50"))

    def test_big_needs_approval(self):
        corr = corrections.create("12345678905", MARCH, "heating", "7000", "СУД-2-1123/2026", "ivanova",
                                  date(2026, 3, 20))
        self.assertFalse(corr.effective)
        with self.assertRaises(corrections.CorrectionError):
            corrections.approve(corr, "ivanova")
        corrections.approve(corr, "petrov")
        self.assertTrue(corr.effective)

    def test_reason_required(self):
        with self.assertRaises(corrections.CorrectionError):
            corrections.create("12345678905", MARCH, "cold_water", "10", "по звонку", "ivanova", date(2026, 3, 20))


class LockTest(unittest.TestCase):
    def test_close_in_order(self):
        lock = PeriodLock(Period(2025, 10))
        lock.close(Period(2025, 10), "admin", datetime(2025, 11, 12, 10, 0))
        with self.assertRaises(PeriodLockedError):
            lock.close(Period(2025, 12), "admin", datetime(2026, 1, 12, 10, 0))
        lock.close(Period(2025, 11), "admin", datetime(2025, 12, 12, 10, 0))
        self.assertTrue(lock.is_closed(Period(2025, 11)))
        with self.assertRaises(PeriodLockedError):
            lock.ensure_open(Period(2025, 10))

    def test_reopen(self):
        lock = PeriodLock(Period(2025, 10))
        lock.close(Period(2025, 10), "admin", datetime(2025, 11, 12, 10, 0))
        lock.close(Period(2025, 11), "admin", datetime(2025, 12, 12, 10, 0))
        self.assertEqual(lock.reopen("admin", datetime(2025, 12, 13, 9, 0), "ошибка в тарифе"), Period(2025, 11))
        self.assertEqual(lock.next_to_close(), Period(2025, 11))


class SmsTest(unittest.TestCase):
    def test_amount(self):
        self.assertEqual(sms.sms_amount(D("1250")), "1 250 руб.")
        self.assertEqual(sms.sms_amount(D("1250.5")), "1 250,50 руб.")

    def test_debt_sms(self):
        msg = sms.debt_sms("8 921 123-45-67", "12345678905", D("4312.2"), date(2026, 4, 10))
        self.assertEqual(msg.phone, "+79211234567")
        self.assertLessEqual(msg.parts, sms.MAX_PARTS)
        self.assertIn("4 312,20 руб.", msg.text)

    def test_parts(self):
        self.assertEqual(sms.parts("a" * 160), 1)
        self.assertEqual(sms.parts("я" * 71), 2)
        self.assertEqual(sms.translit("Щука"), "Schuka")


class ForecastTest(unittest.TestCase):
    def test_forecast_uses_average(self):
        book = readings(("CW1", Period(2025, 12), "100"), ("CW1", Period(2026, 1), "106"), ("CW1", FEB, "112"))
        acc = flat(meters=[flat().meters[0]])
        lines = forecast.forecast(acc, MARCH, book, default_tariffs(), Settings())
        self.assertEqual([(x.service, x.volume) for x in lines], [("cold_water", D("6.000"))])
        self.assertEqual(forecast.total(lines), D("313.86"))


class GisReadingsTest(unittest.TestCase):
    def test_roundtrip(self):
        r = Reading("CW-0001", MARCH, D("123.456"), date(2026, 3, 24))
        line = gis_readings.format_line(r)
        self.assertEqual(len(line), gis_readings.WIDTH)
        parsed = gis_readings.parse("# выгрузка\n" + line + "\n\nкороткая\n")
        self.assertEqual(parsed.readings[0].value, D("123.456"))
        self.assertEqual(parsed.readings[0].period, MARCH)
        self.assertEqual(parsed.skipped, 1)


if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_billing.py'] = r'''import unittest
from datetime import date
from decimal import Decimal as D

from kvartplata.billing import compute_bill, compute_charges, due_date
from kvartplata.config import Settings
from kvartplata.ledger import Ledger
from kvartplata.models import Absence, Payment
from kvartplata.payments import Debt, allocate
from kvartplata.penalties import daily_fraction, overdue_days, penalty
from kvartplata.periods import Period
from kvartplata.recalc import eligible, full_days
from kvartplata.tariffs import default_tariffs

from tests.fixtures import MARCH, flat, standard_book


class BillTest(unittest.TestCase):
    def test_services_present(self):
        charges = compute_charges(flat(), MARCH, standard_book(), default_tariffs())
        self.assertEqual([c.service for c in charges],
                         ["cold_water", "hot_water", "sewage", "electricity", "heating", "maintenance",
                          "capital_repair", "waste"])

    def test_due_date_regular(self):
        self.assertEqual(due_date(Period(2026, 2)), date(2026, 3, 10))

    def test_bill_total_consistent(self):
        bill = compute_bill(flat(), MARCH, standard_book(), default_tariffs())
        self.assertEqual(bill.to_pay, sum(c.total for c in bill.charges))

    def test_incoming_balance(self):
        ledger = Ledger("12345678905")
        ledger.add_charge(Period(2026, 2), D("5000"))
        ledger.add_payment(Payment("12345678905", date(2026, 2, 20), D("3000")))
        bill = compute_bill(flat(), MARCH, standard_book(), default_tariffs(), ledger=ledger, on=date(2026, 3, 1))
        self.assertEqual(bill.incoming, D("2000.00"))
        self.assertEqual(bill.penalties, [])


class RecalcTest(unittest.TestCase):
    def test_full_days_undocumented(self):
        a = Absence("Петров Пётр Петрович", date(2026, 3, 1), date(2026, 3, 20), documented=False)
        self.assertFalse(eligible(a, Settings()))

    def test_long_absence_reduces_norm_services(self):
        acc = flat(meters=[])
        a = Absence("Петров Пётр Петрович", date(2026, 3, 1), date(2026, 3, 20))
        charges = compute_charges(acc, MARCH, standard_book(), default_tariffs(), absences=[a])
        cold = next(c for c in charges if c.service == "cold_water")
        self.assertLess(cold.recalculation, 0)

    def test_metered_service_not_recalculated(self):
        a = Absence("Петров Пётр Петрович", date(2026, 3, 1), date(2026, 3, 20))
        charges = compute_charges(flat(), MARCH, standard_book(), default_tariffs(), absences=[a])
        cold = next(c for c in charges if c.service == "cold_water")
        self.assertEqual(cold.recalculation, D("0.00"))

    def test_full_days_order(self):
        a = Absence("X", date(2026, 3, 10), date(2026, 3, 9))
        self.assertIsNone(full_days(a))


class PenaltyTest(unittest.TestCase):
    def test_no_penalty_within_30_days(self):
        self.assertEqual(penalty(D("1000"), date(2026, 3, 10), date(2026, 4, 9), Settings()), D("0.00"))

    def test_overdue_days(self):
        self.assertEqual(overdue_days(date(2026, 3, 10), date(2026, 3, 9)), 0)
        self.assertEqual(overdue_days(date(2026, 3, 10), date(2026, 4, 10)), 31)

    def test_first_fraction(self):
        self.assertEqual(daily_fraction(31, Settings()), D(1) / 300)
        self.assertEqual(daily_fraction(30, Settings()), 0)

    def test_penalty_after_40_days(self):
        # 10 дней по 1/300 от 15.5 % на 3000 руб.
        self.assertEqual(penalty(D("3000"), date(2026, 3, 10), date(2026, 4, 19), Settings()), D("15.50"))


class PaymentTest(unittest.TestCase):
    def test_single_debt(self):
        res = allocate(D("500"), [Debt("2026-02", D("400"))])
        self.assertEqual(res.paid_for("2026-02"), D("400"))
        self.assertEqual(res.advance, D("100"))

    def test_order(self):
        res = allocate(D("500"), [Debt("2026-01", D("400")), Debt("2026-02", D("400"))])
        # <<C26
        self.assertEqual(res.paid_for("2026-02"), D("400"))
        # ==
        self.assertEqual(res.paid_for("2026-01"), D("400"))
        # >>

    def test_negative_payment(self):
        with self.assertRaises(ValueError):
            allocate(D("-1"), [])


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger("12345678905")
        self.ledger.add_charge(Period(2026, 1), D("1000"))
        self.ledger.add_charge(Period(2026, 2), D("1200"))

    def test_debts_fifo(self):
        self.ledger.add_payment(Payment("12345678905", date(2026, 2, 5), D("1500")))
        self.assertEqual(self.ledger.debts(Period(2026, 2), date(2026, 3, 1)), [(Period(2026, 2), D("700"))])

    def test_overpayment(self):
        self.ledger.add_payment(Payment("12345678905", date(2026, 2, 5), D("2500")))
        # <<C28
        self.assertEqual(self.ledger.balance(Period(2026, 2), date(2026, 3, 1)), D("0.00"))
        # ==
        self.assertEqual(self.ledger.balance(Period(2026, 2), date(2026, 3, 1)), D("-300.00"))
        # >>

    def test_foreign_payment(self):
        with self.assertRaises(ValueError):
            self.ledger.add_payment(Payment("99999999999", date(2026, 2, 5), D("1")))


if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_collection.py'] = r'''import unittest
from datetime import date
from decimal import Decimal as D

from kvartplata import debt_collection, gis_export, privileges_report
from kvartplata.billing import compute_bill
from kvartplata.ledger import Ledger
from kvartplata.periods import Period
from kvartplata.tariffs import default_tariffs

from tests.fixtures import MARCH, flat, resident, standard_book


def ledger_with(months, amount="3000"):
    led = Ledger("12345678905")
    p = Period(2025, 10)
    for _ in range(months):
        led.add_charge(p, D(amount))
        p = p.next()
    return led


class PlanTest(unittest.TestCase):
    def test_no_plan_for_one_period(self):
        self.assertIsNone(debt_collection.plan(ledger_with(1), Period(2026, 2), date(2026, 3, 2)))

    def test_steps(self):
        plan = debt_collection.plan(ledger_with(3), Period(2026, 2), date(2026, 3, 2))
        self.assertEqual([s.kind for s in plan.steps], ["notice", "warning", "restriction", "court"])
        self.assertEqual(plan.step("warning").on, date(2026, 3, 22))
        self.assertEqual(plan.step("restriction").on, date(2026, 4, 6))
        self.assertEqual(plan.debt, D("9000.00"))

    def test_small_debt_no_court(self):
        plan = debt_collection.plan(ledger_with(2, "1000"), Period(2026, 2), date(2026, 3, 2))
        self.assertIsNone(plan.step("court"))


class GisExportTest(unittest.TestCase):
    def bill(self):
        return compute_bill(flat(), MARCH, standard_book(), default_tariffs())

    def test_header(self):
        text = gis_export.export([self.bill()])
        self.assertTrue(text.startswith("﻿#GIS|7801234565|03.2026|1\n"))
        self.assertEqual(gis_export.parse_header(text), ("7801234565", "03.2026", 1))

    def test_service_line(self):
        text = gis_export.export([self.bill()])
        self.assertIn("\nS|01|8.5|52.31|444.64|444.64\n", text)

    def test_mixed_periods(self):
        other = compute_bill(flat(), Period(2026, 2), standard_book(), default_tariffs())
        with self.assertRaises(ValueError):
            gis_export.export([self.bill(), other])


class PrivilegeReportTest(unittest.TestCase):
    def test_category_choice(self):
        self.assertEqual(privileges_report.category_for(["veteran", "disabled"], "cold_water"), "disabled")
        self.assertEqual(privileges_report.category_for(["veteran", "disabled"], "maintenance"), "disabled")
        self.assertIsNone(privileges_report.category_for(["veteran"], "gas"))

    def test_totals(self):
        acc = flat(residents=[resident(privileges=["large_family"]), resident("Петрова Анна Сергеевна")])
        bill = compute_bill(acc, MARCH, standard_book(), default_tariffs())
        totals = privileges_report.by_category([bill])
        self.assertEqual(totals[("large_family", "cold_water")], D("66.70"))
        self.assertNotIn(("large_family", "maintenance"), totals)


if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_extra.py'] = r'''import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from kvartplata import receipt_html
from kvartplata.anomalies import meter_flags, scan, water_balance_flag
from kvartplata.billing import compute_bill
from kvartplata.config import Settings
from kvartplata.court import claim_duty, court_order_duty, prepare_claim
from kvartplata.indexation import check, cost, typical_profile
from kvartplata.ledger import Ledger
from kvartplata.models import Meter
from kvartplata.periods import Period
from kvartplata.settings_io import SettingsError, load_settings, settings_from_dict, settings_to_dict
from kvartplata.tariffs import default_tariffs

from tests.fixtures import FEB, MARCH, flat, readings, standard_book


class CourtTest(unittest.TestCase):
    def test_duty_scale(self):
        self.assertEqual(claim_duty(D("50000")), D("4000"))
        self.assertEqual(claim_duty(D("150000")), D("5500"))
        self.assertEqual(claim_duty(D("400000")), D("12500"))

    def test_order_duty_minimum(self):
        self.assertEqual(court_order_duty(D("20000")), D("2000"))
        self.assertEqual(court_order_duty(D("150000")), D("2750"))

    def test_bad_price(self):
        with self.assertRaises(ValueError):
            claim_duty(0)

    def test_prepare_claim(self):
        led = Ledger("12345678905")
        led.add_charge(Period(2025, 10), D("3000"))
        led.add_charge(Period(2025, 11), D("3000"))
        data = prepare_claim(led, "Петров П. П.", Period(2025, 11), date(2026, 3, 1))
        self.assertEqual(data.principal, D("6000.00"))
        self.assertGreater(data.penalties, 0)
        self.assertEqual(data.duty, D("2000"))
        self.assertIsNone(prepare_claim(Ledger("1"), "X", Period(2025, 11), date(2026, 3, 1)))


class AnomalyTest(unittest.TestCase):
    def test_spike(self):
        book = readings(("CW1", Period(2025, 12), "100"), ("CW1", Period(2026, 1), "105"), ("CW1", FEB, "110"),
                        ("CW1", MARCH, "140"))
        flags = meter_flags(flat(), MARCH, book, Settings())
        self.assertEqual([f.kind for f in flags], ["spike"])

    def test_zero_three_periods(self):
        book = readings(("CW1", Period(2025, 8), "100"), ("CW1", Period(2025, 9), "100"),
                        ("CW1", Period(2025, 10), "100"), ("CW1", Period(2025, 11), "100"))
        kinds = [f.kind for f in meter_flags(flat(), Period(2025, 11), book, Settings())]
        self.assertEqual(kinds, ["same_value", "zero"])

    def test_hot_gt_cold(self):
        book = readings(("CW1", FEB, "10"), ("CW1", MARCH, "11"), ("HW1", FEB, "10"), ("HW1", MARCH, "15"))
        self.assertEqual(water_balance_flag(flat(), MARCH, book).kind, "hot_gt_cold")

    def test_scan_clean(self):
        self.assertEqual(scan([flat()], MARCH, standard_book(), Settings()), [])


class HtmlReceiptTest(unittest.TestCase):
    def test_render(self):
        bill = compute_bill(flat(), MARCH, standard_book(), default_tariffs())
        html = receipt_html.render(bill)
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("Холодное водоснабжение", html)
        self.assertIn("ST00012", html)


class SettingsIoTest(unittest.TestCase):
    def test_override(self):
        s = settings_from_dict({"maintenance_rate": "33.10", "normatives": {"cold_water": "4.9"},
                                "absence_min_days": 7})
        self.assertEqual(s.maintenance_rate, D("33.10"))
        self.assertEqual(s.normatives["cold_water"], D("4.9"))
        self.assertEqual(s.normatives["hot_water"], D("3.500"))
        self.assertEqual(s.absence_min_days, 7)

    def test_unknown_key(self):
        with self.assertRaises(SettingsError):
            settings_from_dict({"maintanance_rate": "1"})

    def test_roundtrip_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            path.write_text(json.dumps(settings_to_dict(Settings())), encoding="utf-8")
            self.assertEqual(load_settings(path), Settings())


class IndexationTest(unittest.TestCase):
    def test_cost(self):
        self.assertEqual(cost({"cold_water": D("8")}, default_tariffs(), date(2026, 3, 1)), D("418.48"))

    def test_no_excess_at_new_year(self):
        rows = check({"2 чел.": typical_profile(2)}, default_tariffs(), date(2025, 12, 1), date(2026, 1, 15))
        self.assertEqual(rows, [])

    def test_unknown_service(self):
        with self.assertRaises(ValueError):
            check({"x": {"heating": D("1")}}, default_tariffs(), date(2025, 12, 1), date(2026, 1, 15))


if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_io.py'] = r'''import unittest
from datetime import date
from decimal import Decimal as D

from kvartplata.bank_registry import RegistryError, parse_registry
from kvartplata.billing import compute_bill
from kvartplata.export import export_bills
from kvartplata.importer import import_readings
from kvartplata.periods import Period
from kvartplata.tariffs import TariffTable, default_tariffs

from tests.fixtures import MARCH, flat, standard_book


class ImportTest(unittest.TestCase):
    def test_ok(self):
        res = import_readings("meter_id;period;value;taken_on\nCW1;2026-03;108.5;2026-03-24\n")
        self.assertEqual(len(res.readings), 1)
        self.assertEqual(res.readings[0].value, D("108.5"))
        self.assertEqual(res.readings[0].taken_on, date(2026, 3, 24))

    def test_missing_column(self):
        res = import_readings("meter;period;value\nCW1;2026-03;1\n")
        self.assertTrue(res.errors)

    def test_bad_line_does_not_stop(self):
        res = import_readings("meter_id;period;value\nCW1;2026-13;1\nCW2;2026-03;5\n")
        self.assertEqual(len(res.readings), 1)
        self.assertEqual(len(res.errors), 1)

    def test_duplicate_counted(self):
        res = import_readings("meter_id;period;value\nCW1;2026-03;1\nCW1;2026-03;2\n")
        self.assertEqual(res.duplicates, 1)
        self.assertEqual(len(res.readings), 1)


class ExportTest(unittest.TestCase):
    def test_header_and_rows(self):
        text = export_bills([compute_bill(flat(), MARCH, standard_book(), default_tariffs())])
        lines = text.splitlines()
        # <<C31
        self.assertEqual(lines[0], "account,period,service,volume,rate,amount,discount,recalculation,total")
        self.assertIn("12345678905,2026-03,cold_water,8.5,52.31,444.64", text)
        # ==
        self.assertEqual(lines[0], "account;period;service;volume;rate;amount;discount;recalculation;total")
        self.assertIn("12345678905;2026-03;cold_water;8,5;52,31;444,64", text)
        # >>
        self.assertEqual(len(lines), 9)


class TariffTest(unittest.TestCase):
    def test_rate_in_middle(self):
        self.assertEqual(default_tariffs().rate_for("cold_water", date(2026, 3, 15)), D("52.31"))

    def test_csv(self):
        table = TariffTable.from_csv("service;zone;start;rate\ncold_water;single;2026-01-01;52,31\n")
        self.assertEqual(table.rate_for_period("cold_water", Period(2026, 4)), D("52.31"))


class RegistryTest(unittest.TestCase):
    TEXT = ("#REESTR;Банк Северный;2026-03-20\n"
            "19.03.2026;12345678905;1500,00;Петров П. П.;A-1\n"
            "19.03.2026;00000000001;100.00;Кто-то;A-2\n"
            "=;2;1600.00\n")

    def test_parse(self):
        reg = parse_registry(self.TEXT)
        self.assertEqual(len(reg.payments), 1)
        self.assertEqual(reg.payments[0].amount, D("1500.00"))

    def test_control_mismatch(self):
        with self.assertRaises(RegistryError):
            parse_registry(self.TEXT.replace("=;2;1600.00", "=;2;1700.00"))


if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_meters.py'] = r'''import unittest
from decimal import Decimal as D

from kvartplata.config import Settings
from kvartplata.meters import ReadingBook, ReadingError, consumption, resolve_meter, resolve_service, validate_value
from kvartplata.models import Meter, Reading
from kvartplata.normatives import coefficient, norm_volume, persons_for_norm
from kvartplata.periods import Period

from tests.fixtures import FEB, MARCH, flat, readings


class ConsumptionTest(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(consumption(D("100"), D("108.5")), D("8.5"))

    def test_decrease(self):
        # <<C11
        with self.assertRaises(ReadingError):
            consumption(D("99990"), D("5"))
        # ==
        self.assertEqual(consumption(D("99990"), D("5")), D("15"))
        # >>

    def test_validate(self):
        with self.assertRaises(ReadingError):
            validate_value(D("-1"), Meter("X", "cold_water"))
        with self.assertRaises(ReadingError):
            validate_value(D("100000"), Meter("X", "cold_water"))


class ResolveTest(unittest.TestCase):
    def test_direct(self):
        book = readings(("CW1", FEB, "100"), ("CW1", MARCH, "108.5"))
        res = resolve_meter(Meter("CW1", "cold_water"), flat(), MARCH, book, Settings())
        self.assertEqual((res.volume, res.method), (D("8.5"), "meter"))

    def test_gap_between_readings(self):
        book = readings(("CW1", Period(2025, 12), "100"), ("CW1", MARCH, "112"))
        res = resolve_meter(Meter("CW1", "cold_water"), flat(), MARCH, book, Settings())
        self.assertEqual(res.volume, D("12"))

    def test_average_when_missing(self):
        book = readings(("CW1", Period(2025, 12), "100"), ("CW1", Period(2026, 1), "106"),
                        ("CW1", FEB, "112"))
        res = resolve_meter(Meter("CW1", "cold_water"), flat(), MARCH, book, Settings())
        self.assertEqual((res.volume, res.method), (D("6.000"), "average"))

    def test_norm_without_history(self):
        res = resolve_meter(Meter("CW1", "cold_water"), flat(), MARCH, ReadingBook(), Settings())
        self.assertEqual(res.method, "norm")

    def test_service_without_meter_uses_norm_with_coefficient(self):
        acc = flat(meters=[])
        res = resolve_service(acc, "cold_water", MARCH, ReadingBook(), Settings())
        self.assertEqual(res.volume, D("4.745") * 2 * D("1.5"))

    def test_two_meters_are_summed(self):
        acc = flat(meters=[Meter("CW1", "cold_water"), Meter("CW2", "cold_water")])
        book = readings(("CW1", FEB, "10"), ("CW1", MARCH, "13"), ("CW2", FEB, "20"), ("CW2", MARCH, "22"))
        self.assertEqual(resolve_service(acc, "cold_water", MARCH, book, Settings()).volume, D("5"))


class NormTest(unittest.TestCase):
    def test_persons_registered(self):
        self.assertEqual(persons_for_norm(flat()), 2)

    def test_persons_owners_when_nobody_registered(self):
        self.assertEqual(persons_for_norm(flat(residents=[], owners_count=3)), 3)

    def test_electric_stove_norm(self):
        self.assertEqual(norm_volume(flat(electric_stove=True), "electricity", Settings()), D("242"))

    def test_coefficient_gas(self):
        self.assertEqual(coefficient(flat(meters=[]), "gas", Settings()), D(1))

    def test_coefficient_no_meter(self):
        self.assertEqual(coefficient(flat(meters=[]), "cold_water", Settings()), D("1.5"))


if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_misc.py'] = r'''import unittest
from datetime import date
from decimal import Decimal as D

from kvartplata.addresses import normalize, parse, same_address
from kvartplata.installments import apply_payment, make_agreement
from kvartplata.ledger import Ledger
from kvartplata.models import Meter, Payment
from kvartplata.notices import build_notice, pay_until
from kvartplata.people import FullName, dative, greeting
from kvartplata.periods import Period
from kvartplata.reports import debtors, summary_by_service
from kvartplata.subsidies import Family, subsidy
from kvartplata.validators import (
    ValidationError,
    is_valid_account,
    is_valid_email,
    is_valid_inn,
    make_account_number,
    normalize_phone,
)
from kvartplata.verification import is_expired, next_verification


class ValidatorsTest(unittest.TestCase):
    def test_fixture_account_is_valid(self):
        self.assertTrue(is_valid_account("12345678905"))

    def test_account_roundtrip(self):
        self.assertTrue(is_valid_account(make_account_number("4000000001")))

    def test_account_format(self):
        self.assertFalse(is_valid_account("1234"))

    def test_inn(self):
        self.assertTrue(is_valid_inn("7707083893"))
        self.assertFalse(is_valid_inn("7707083894"))

    def test_phone(self):
        self.assertEqual(normalize_phone("8 (921) 123-45-67"), "+79211234567")
        with self.assertRaises(ValidationError):
            normalize_phone("123")

    def test_email(self):
        self.assertTrue(is_valid_email("a.b@example.ru"))
        self.assertFalse(is_valid_email("a.b@"))


class PeopleTest(unittest.TestCase):
    def test_initials(self):
        self.assertEqual(FullName.parse("Иванов Иван Иванович").initials(), "Иванов И. И.")

    def test_dative(self):
        self.assertEqual(dative(FullName.parse("Петрова Анна Сергеевна")), "Петровой Анне Сергеевне")
        self.assertEqual(dative(FullName.parse("Смирнов Андрей Ильич")), "Смирнову Андрею Ильичу")

    def test_greeting(self):
        self.assertEqual(greeting(FullName.parse("Петрова Анна Сергеевна")), "Уважаемая Анна Сергеевна!")


class AddressTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("Улица Садовая,  дом 5 , квартира 12"), "ул. садовая, д. 5, кв. 12")

    def test_parse(self):
        a = parse("ул. Садовая, д. 5, кв. 12")
        self.assertEqual((a.street, a.house, a.flat), ("ул. садовая", "5", "12"))

    def test_same(self):
        self.assertTrue(same_address("ул Садовая, д 5, кв 12", "улица Садовая, дом 5, квартира 12"))


class SubsidyTest(unittest.TestCase):
    def test_rich_family_gets_nothing(self):
        self.assertEqual(subsidy(Family(2, D("200000")), D("9000")), D("0.00"))

    def test_low_income_single(self):
        self.assertGreater(subsidy(Family(1, D("15000"), single_pensioner=True), D("6000")), 0)


class InstallmentsTest(unittest.TestCase):
    def test_schedule(self):
        ag = make_agreement("12345678905", D("1000"), Period(2026, 4), 3)
        # <<C03
        self.assertEqual([i.amount for i in ag.items], [D("333.33"), D("333.33"), D("333.34")])
        # ==
        self.assertEqual([i.amount for i in ag.items], [D("333.34"), D("333.33"), D("333.33")])
        # >>
        self.assertEqual(ag.total, D("1000.00"))

    def test_due_dates(self):
        ag = make_agreement("12345678905", D("900"), Period(2026, 1), 2)
        self.assertEqual([i.due for i in ag.items], [date(2026, 2, 10), date(2026, 3, 10)])

    def test_payment(self):
        ag = make_agreement("12345678905", D("900"), Period(2026, 1), 3)
        self.assertEqual(apply_payment(ag, D("1000")), D("100.00"))

    def test_limits(self):
        with self.assertRaises(ValueError):
            make_agreement("12345678905", D("900"), Period(2026, 1), 13)


class ReportsTest(unittest.TestCase):
    def ledger(self, months):
        led = Ledger("12345678905")
        p = Period(2025, 12)
        for _ in range(months):
            led.add_charge(p, D("1000"))
            p = p.next()
        return led

    def test_debtors_four_months(self):
        rows = debtors([self.ledger(4)], Period(2026, 3), date(2026, 4, 1))
        self.assertEqual([(r.months, r.debt) for r in rows], [(4, D("4000.00"))])

    def test_not_debtor(self):
        self.assertEqual(debtors([self.ledger(2)], Period(2026, 3), date(2026, 4, 1)), [])

    def test_notice(self):
        notice = build_notice(self.ledger(2), "Петрова Анна Сергеевна", "ул. Садовая, д. 5", Period(2026, 3),
                              date(2026, 4, 1))
        self.assertIsNotNone(notice)
        self.assertIn("Петровой Анне Сергеевне", notice.text)
        self.assertIn("две тысячи", notice.text.lower())

    def test_pay_until(self):
        self.assertEqual(pay_until(date(2026, 4, 3)), date(2026, 5, 4))


class VerificationTest(unittest.TestCase):
    def test_interval(self):
        m = Meter("HW1", "hot_water", installed_on=date(2022, 3, 1))
        self.assertEqual(next_verification(m), date(2026, 3, 1))
        self.assertTrue(is_expired(m, date(2026, 3, 1)))
        self.assertFalse(is_expired(m, date(2026, 2, 28)))

    def test_unknown_install_date(self):
        self.assertFalse(is_expired(Meter("X", "cold_water"), date(2030, 1, 1)))


if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_money.py'] = r'''import unittest
from decimal import Decimal as D

from kvartplata.money import (
    allocate_by_weights,
    floor_money,
    format_rub,
    from_kopecks,
    kopecks,
    money_sum,
    percent_of,
    round_money,
    split_evenly,
    to_money,
)


class ToMoneyTest(unittest.TestCase):
    def test_float_is_converted_via_repr(self):
        self.assertEqual(to_money(0.1), D("0.1"))

    def test_string_with_spaces(self):
        self.assertEqual(to_money(" 1 250.5 "), D("1250.5"))

    def test_bool_rejected(self):
        with self.assertRaises(TypeError):
            to_money(True)

    def test_garbage_string(self):
        with self.assertRaises(ValueError):
            to_money("двенадцать")


class RoundTest(unittest.TestCase):
    def test_regular_values(self):
        self.assertEqual(round_money(D("10.004")), D("10.00"))
        self.assertEqual(round_money(D("10.006")), D("10.01"))

    def test_half_cases(self):
        # <<C01
        self.assertEqual(round_money(D("0.125")), D("0.12"))
        self.assertEqual(round_money(D("0.135")), D("0.14"))
        # ==
        self.assertEqual(round_money(D("0.125")), D("0.13"))
        self.assertEqual(round_money(D("0.135")), D("0.14"))
        # >>

    def test_floor(self):
        self.assertEqual(floor_money(D("3.999")), D("3.99"))

    def test_money_sum(self):
        self.assertEqual(money_sum(["0.1", "0.2", 0.3]), D("0.60"))


class SplitTest(unittest.TestCase):
    def test_even(self):
        self.assertEqual(split_evenly(D("90"), 3), [D("30.00")] * 3)

    def test_remainder(self):
        parts = split_evenly(D("100"), 3)
        self.assertEqual(sum(parts), D("100.00"))
        # <<C03
        self.assertEqual(parts, [D("33.33"), D("33.33"), D("33.34")])
        # ==
        self.assertEqual(parts, [D("33.34"), D("33.33"), D("33.33")])
        # >>

    def test_bad_parts(self):
        with self.assertRaises(ValueError):
            split_evenly(D("10"), 0)

    def test_weights(self):
        self.assertEqual(allocate_by_weights(D("100"), [D("1"), D("1"), D("2")]),
                         [D("25.00"), D("25.00"), D("50.00")])

    def test_weights_sum_is_preserved(self):
        parts = allocate_by_weights(D("10"), [D("33.3"), D("41.7"), D("54")])
        self.assertEqual(sum(parts), D("10.00"))


class FormatTest(unittest.TestCase):
    def test_small(self):
        self.assertEqual(format_rub(D("15.5")), "15,50 руб.")

    def test_negative(self):
        self.assertEqual(format_rub(D("-7.1"), suffix=""), "-7,10")

    def test_kopecks(self):
        self.assertEqual(kopecks(D("12.34")), 1234)
        self.assertEqual(from_kopecks(1234), D("12.34"))

    def test_percent(self):
        self.assertEqual(percent_of(D("200"), 15), D("30.00"))


if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_people_meters.py'] = r'''import unittest
from datetime import date
from decimal import Decimal as D

from kvartplata.meter_replacement import Replacement, replacement_volume
from kvartplata.models import Meter
from kvartplata.periods import Period
from kvartplata.tenants import Registration, changes_in_period, persons_in_period, registered_on

MARCH = Period(2026, 3)


class ReplacementTest(unittest.TestCase):
    def rep(self):
        return Replacement(Meter("CW-OLD", "cold_water"), Meter("CW-NEW", "cold_water"), date(2026, 3, 10),
                           D("250.5"), D("0.2"))

    def test_with_new_reading(self):
        self.assertEqual(replacement_volume(self.rep(), MARCH, D("245"), D("4.2")), D("9.5"))

    def test_without_new_reading(self):
        self.assertEqual(replacement_volume(self.rep(), MARCH, D("245"), None), D("17.050"))

    def test_other_service(self):
        with self.assertRaises(ValueError):
            Replacement(Meter("A", "cold_water"), Meter("B", "hot_water"), date(2026, 3, 1), D("1"), D("0"))

    def test_outside_period(self):
        with self.assertRaises(ValueError):
            replacement_volume(self.rep(), Period(2026, 4), D("245"), D("4.2"))


class TenantsTest(unittest.TestCase):
    def regs(self):
        return [Registration("Петров П. П.", date(2020, 1, 1)),
                Registration("Петрова А. С.", date(2026, 3, 17)),
                Registration("Сидоров И. И.", date(2019, 5, 5), date(2026, 3, 10))]

    def test_persons(self):
        self.assertEqual(persons_in_period(self.regs(), MARCH), D("1.8065"))

    def test_registered_on(self):
        self.assertEqual(registered_on(self.regs(), date(2026, 3, 12)), ["Петров П. П."])

    def test_changes(self):
        self.assertEqual(changes_in_period(self.regs(), MARCH),
                         ["Сидоров И. И.: снят(а) с учёта 10.03.2026", "Петрова А. С.: регистрация с 17.03.2026"])


if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_periods.py'] = r'''import unittest
from datetime import date

from kvartplata.calendar_ru import add_workdays, is_workday, next_workday, workdays_between
from kvartplata.periods import Period, months_between, overlap_days, period_range


class PeriodTest(unittest.TestCase):
    def test_parse_iso(self):
        self.assertEqual(Period.parse("2026-03"), Period(2026, 3))

    def test_parse_bad(self):
        with self.assertRaises(ValueError):
            Period.parse("март")

    def test_next_december(self):
        self.assertEqual(Period(2025, 12).next(), Period(2026, 1))

    def test_prev_regular(self):
        self.assertEqual(Period(2026, 3).prev(), Period(2026, 2))

    def test_shift(self):
        self.assertEqual(Period(2026, 3).shift(-6), Period(2025, 9))
        self.assertEqual(Period(2026, 3).shift(10), Period(2027, 1))

    def test_days(self):
        self.assertEqual(Period(2028, 2).days, 29)
        self.assertEqual(Period(2026, 2).days, 28)

    def test_str_and_human(self):
        self.assertEqual(str(Period(2026, 3)), "2026-03")
        self.assertEqual(Period(2026, 3).human(), "март 2026")

    def test_range_and_between(self):
        self.assertEqual(len(period_range(Period(2025, 11), Period(2026, 2))), 4)
        self.assertEqual(months_between(Period(2025, 11), Period(2026, 2)), 3)

    def test_overlap(self):
        self.assertEqual(overlap_days(Period(2026, 3), date(2026, 2, 25), date(2026, 3, 4)), 4)
        self.assertEqual(overlap_days(Period(2026, 3), date(2026, 4, 1), date(2026, 4, 4)), 0)


class CalendarTest(unittest.TestCase):
    def test_weekend(self):
        self.assertEqual(next_workday(date(2026, 3, 14)), date(2026, 3, 16))

    def test_workday_itself(self):
        self.assertEqual(next_workday(date(2026, 3, 11)), date(2026, 3, 11))

    def test_holiday_flag(self):
        self.assertFalse(is_workday(date(2026, 1, 5)))

    def test_add_workdays(self):
        self.assertEqual(add_workdays(date(2026, 3, 12), 2), date(2026, 3, 16))

    def test_between(self):
        self.assertEqual(workdays_between(date(2026, 3, 13), date(2026, 3, 17)), 2)


if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_receipt.py'] = r'''import unittest
from decimal import Decimal as D

from kvartplata import receipt
from kvartplata.billing import compute_bill
from kvartplata.tariffs import default_tariffs
from kvartplata.words import amount_in_words, number_in_words, plural

from tests.fixtures import MARCH, flat, standard_book


def bill():
    return compute_bill(flat(), MARCH, standard_book(), default_tariffs())


class ReceiptTest(unittest.TestCase):
    def test_header(self):
        text = receipt.render(bill())
        self.assertIn("ПЛАТЁЖНЫЙ ДОКУМЕНТ за март 2026", text)
        self.assertIn("Лицевой счёт: 12345678905", text)

    def test_all_lines_present(self):
        text = receipt.render(bill())
        for title in ("Холодное водоснабжение", "Водоотведение", "Обращение с ТКО"):
            self.assertIn(title, text)

    def test_qr_prefix_and_period(self):
        payload = receipt.qr_payload(bill())
        self.assertTrue(payload.startswith("ST00012|"))
        self.assertIn("|PaymPeriod=0326|", payload)

    def test_qr_sum(self):
        b = bill()
        payload = receipt.qr_payload(b)
        # <<C29
        self.assertIn(f"|Sum={b.to_pay:.2f}|", payload)
        # ==
        self.assertIn(f"|Sum={int(b.to_pay * 100)}|", payload)
        # >>

    def test_payer_is_owner(self):
        self.assertIn("Петров", receipt.payer_name(bill()))


class WordsTest(unittest.TestCase):
    def test_plural(self):
        self.assertEqual([plural(n, ("рубль", "рубля", "рублей")) for n in (1, 2, 5, 11, 21, 112)],
                         ["рубль", "рубля", "рублей", "рублей", "рубль", "рублей"])

    def test_numbers(self):
        self.assertEqual(number_in_words(2021), "две тысячи двадцать один")
        self.assertEqual(number_in_words(1000000), "один миллион")

    def test_amount(self):
        self.assertEqual(amount_in_words(D("1234.05")), "Одна тысяча двести тридцать четыре рубля 05 копеек")


if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_services.py'] = r'''import unittest
from datetime import date
from decimal import Decimal as D

from kvartplata.models import Meter
from kvartplata.privileges import discount_for, resident_percent
from kvartplata.services import capital_repair, cold_water, electricity, gas, heating, hot_water, maintenance
from kvartplata.services import sewage, waste
from kvartplata.config import Settings

from tests.fixtures import FEB, MARCH, ctx, flat, readings, resident


class WaterTest(unittest.TestCase):
    def test_cold_water(self):
        (c,) = cold_water.calculate(ctx())
        self.assertEqual((c.volume, c.rate, c.amount), (D("8.5"), D("52.31"), D("444.64")))

    def test_hot_water_components(self):
        (c,) = hot_water.calculate(ctx())
        self.assertEqual(c.volume, D("4"))
        self.assertIn("теплоноситель 178.56", c.note)

    def test_sewage_cold_part(self):
        (c,) = sewage.calculate(ctx())
        self.assertIn("ХВС 8.5", c.note)
        self.assertEqual(c.rate, D("43.27"))


class ElectricityTest(unittest.TestCase):
    def test_single_rate(self):
        (c,) = electricity.calculate(ctx())
        self.assertEqual((c.volume, c.amount), (D("210"), D("1465.80")))

    def test_two_zones_give_two_lines(self):
        acc = flat(meters=[Meter("ED", "electricity", "day"), Meter("EN", "electricity", "night")])
        book = readings(("ED", FEB, "1000"), ("ED", MARCH, "1150"), ("EN", FEB, "500"), ("EN", MARCH, "560"))
        lines = electricity.calculate(ctx(acc, book=book))
        self.assertEqual([c.volume for c in lines], [D("150"), D("60")])
        self.assertEqual(lines[0].rate, D("7.95"))


class AreaServicesTest(unittest.TestCase):
    def test_maintenance(self):
        (c,) = maintenance.calculate(ctx())
        self.assertEqual(c.amount, D("1759.32"))

    def test_heating_rate(self):
        (c,) = heating.calculate(ctx())
        self.assertEqual(c.rate, D("3044.18"))
        self.assertEqual(c.method, "area")

    def test_heating_in_summer_uses_new_tariff(self):
        (c,) = heating.calculate(ctx(period=Period_july()))
        # <<C08
        self.assertEqual(c.rate, D("3044.18"))
        # ==
        self.assertEqual(c.rate, D("3259.90"))
        # >>

    def test_capital_repair_regular(self):
        (c,) = capital_repair.calculate(ctx())
        self.assertEqual((c.amount, c.discount), (D("805.81"), D("0.00")))

    def test_capital_repair_85_alone(self):
        acc = flat(residents=[resident(born=date(1940, 2, 1), owner=True, lives_alone=True)])
        (c,) = capital_repair.calculate(ctx(acc))
        self.assertEqual(c.discount, c.amount)

    def test_gas_only_with_stove(self):
        self.assertEqual(gas.calculate(ctx()), [])
        (c,) = gas.calculate(ctx(flat(has_gas=True)))
        self.assertEqual(c.method, "norm")


class WasteTest(unittest.TestCase):
    def test_per_person(self):
        (c,) = waste.calculate(ctx())
        self.assertEqual(c.amount, D("297.24"))

    def test_nobody_registered(self):
        acc = flat(residents=[resident(registered=False, owner=True)], owners_count=2)
        (c,) = waste.calculate(ctx(acc))
        # <<C19
        self.assertEqual(c.amount, D("0.00"))
        # ==
        self.assertEqual(c.amount, D("297.24"))
        # >>


class PrivilegeTest(unittest.TestCase):
    def test_percent_single(self):
        self.assertEqual(resident_percent(["large_family"], "cold_water"), 30)

    def test_percent_unknown(self):
        self.assertEqual(resident_percent(["astronaut"], "cold_water"), 0)

    def test_large_family_water(self):
        acc = flat(residents=[resident(privileges=["large_family"]), resident("Петрова Анна Сергеевна")])
        (c,) = cold_water.calculate(ctx(acc))
        self.assertEqual(discount_for(c, acc, Settings()), D("66.70"))


def Period_july():
    from kvartplata.periods import Period
    return Period(2026, 7)


if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_closing.py'] = r'''import unittest
from datetime import date
from decimal import Decimal as D

from kvartplata.closing import final_statement, month_days, owned_days, owned_share, prorate, render
from kvartplata.ledger import Ledger
from kvartplata.models import Charge, Payment

from tests.fixtures import FEB, MARCH

CLOSED = date(2026, 3, 16)


def _charges():
    return [Charge("maintenance", MARCH, D("54.3"), D("30"), D("1629.00"), "area"),
            Charge("heating", MARCH, D("1.2"), D("2500"), D("3000.00"), "norm"),
            Charge("cold_water", MARCH, D("8.5"), D("52.31"), D("444.64"), "meter")]


class ClosingTest(unittest.TestCase):
    def test_owned_days(self):
        # <<C51
        self.assertEqual(owned_days(MARCH, CLOSED), 15)
        # ==
        self.assertEqual(owned_days(MARCH, CLOSED), 16)
        # >>

    def test_month_days(self):
        # <<C50
        self.assertEqual(month_days(MARCH), 30)
        # ==
        self.assertEqual(month_days(MARCH), 31)
        # >>

    def test_outside_period(self):
        with self.assertRaises(ValueError):
            owned_days(MARCH, date(2026, 4, 2))

    def test_prorate(self):
        charges = prorate(_charges(), MARCH, CLOSED)
        share = owned_share(MARCH, CLOSED)
        self.assertEqual(charges[0].amount, (D("1629.00") * share).quantize(D("0.01")))
        self.assertEqual(charges[2].amount, D("444.64"))
        self.assertIn("дн. владения", charges[0].note)
        self.assertEqual(charges[2].note, "")

    def test_final_statement(self):
        led = Ledger("12345678905")
        led.add_charge(FEB, D("3000"))
        led.add_payment(Payment("12345678905", date(2026, 3, 5), D("9000")))
        st = final_statement(led, _charges(), MARCH, CLOSED)
        self.assertEqual(st.balance, D("-6000.00"))
        self.assertEqual(st.to_pay, D("0.00"))
        self.assertEqual(st.to_refund, -(st.balance + st.charged))

    def test_render(self):
        led = Ledger("12345678905")
        led.add_charge(FEB, D("3000"))
        text = render(final_statement(led, _charges(), MARCH, CLOSED))
        self.assertIn("Итоговый расчёт по л/с 12345678905", text)
        self.assertIn("Итого к оплате", text)


if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_owners_window.py'] = r'''import unittest
from datetime import date
from decimal import Decimal as D
from fractions import Fraction

from kvartplata.models import Reading
from kvartplata.owners import (Share, describe, largest_owner, parse_share, parse_shares, render,
                               split_amount, validate)
from kvartplata.periods import Period
from kvartplata.reading_window import (accept, needs_reminder, period_for, receive, reminder_date,
                                       render_schedule, schedule, window)

from tests.fixtures import FEB, MARCH, flat, readings


class ShareTest(unittest.TestCase):
    def test_parse_fraction(self):
        self.assertEqual(parse_share("1/3"), Fraction(1, 3))
        self.assertEqual(parse_share(" 2 / 5 "), Fraction(2, 5))
        self.assertEqual(parse_share("1"), Fraction(1))

    def test_parse_bad(self):
        for text in ("0", "3/2", "1/0", "треть", ""):
            with self.assertRaises(ValueError):
                parse_share(text)

    def test_parse_shares(self):
        shares = parse_shares("Иванов И. И. — 1/2; Иванова А. П. – 1/2")
        self.assertEqual([s.owner for s in shares], ["Иванов И. И.", "Иванова А. П."])
        self.assertEqual([s.part for s in shares], [Fraction(1, 2), Fraction(1, 2)])

    def test_parse_shares_without_part(self):
        with self.assertRaises(ValueError):
            parse_shares("Иванов И. И.; Иванова А. П. — 1/2")

    def test_validate(self):
        validate([Share("A", Fraction(1, 2)), Share("B", Fraction(1, 2))])
        with self.assertRaises(ValueError):
            validate([Share("A", Fraction(2, 3)), Share("B", Fraction(2, 3))])
        with self.assertRaises(ValueError):
            validate([])

    def test_split_halves(self):
        shares = [Share("A", Fraction(1, 2)), Share("B", Fraction(1, 2))]
        self.assertEqual(split_amount(D("1000.10"), shares), [("A", D("500.05")), ("B", D("500.05"))])

    def test_split_thirds(self):
        shares = [Share(x, Fraction(1, 3)) for x in "ABC"]
        parts = split_amount(D("100"), shares)
        self.assertEqual(sum(p for _o, p in parts), D("100"))
        # <<C44
        self.assertEqual([p for _o, p in parts], [D("33.33"), D("33.33"), D("33.34")])
        # ==
        self.assertEqual([p for _o, p in parts], [D("33.34"), D("33.33"), D("33.33")])
        # >>

    def test_render(self):
        shares = [Share("Иванов И. И.", Fraction(1, 4)), Share("Иванова А. П.", Fraction(3, 4))]
        text = render("12345678905", D("2000"), shares)
        self.assertIn("Иванов И. И.", text)
        self.assertIn("1/4", text)
        self.assertIn("500,00 руб.", text)


class WindowTest(unittest.TestCase):
    def test_window(self):
        self.assertEqual(window(MARCH), (date(2026, 3, 15), date(2026, 3, 25)))

    def test_period_for(self):
        self.assertEqual(period_for(date(2026, 3, 3)), MARCH)
        self.assertEqual(period_for(date(2026, 3, 20)), MARCH)
        self.assertEqual(period_for(date(2026, 3, 27)), Period(2026, 4))

    def test_last_day(self):
        # <<C47
        self.assertEqual(period_for(date(2026, 3, 25)), Period(2026, 4))
        # ==
        self.assertEqual(period_for(date(2026, 3, 25)), MARCH)
        # >>

    def test_accept(self):
        book = readings(("CW1", FEB, "100"))
        self.assertIsNone(accept(Reading("CW1", MARCH, D("104")), date(2026, 3, 20), book))
        self.assertIsNotNone(accept(Reading("CW1", MARCH, D("98")), date(2026, 3, 20), book))
        self.assertIsNotNone(accept(Reading("CW1", MARCH, D("104"), date(2026, 3, 22)), date(2026, 3, 20), book))
        self.assertIsNotNone(accept(Reading("CW1", MARCH, D("-1")), date(2026, 3, 20), book))

    def test_receive(self):
        book = readings(("CW1", FEB, "100"), ("HW1", FEB, "50"))
        bad = receive([Reading("CW1", FEB, D("104")), Reading("HW1", FEB, D("40"))], date(2026, 3, 18), book)
        self.assertEqual([r.meter_id for r, _why in bad], ["HW1"])
        self.assertEqual(book.get("CW1", MARCH).value, D("104"))

    def test_reminder_date(self):
        self.assertEqual(reminder_date(MARCH), date(2026, 3, 22))

    def test_needs_reminder(self):
        self.assertTrue(needs_reminder(flat(), MARCH, readings(("CW1", FEB, "100"))))
        self.assertFalse(needs_reminder(flat(meters=[]), MARCH, readings()))



class HelpersTest(unittest.TestCase):
    def test_describe_roundtrip(self):
        text = "Иванов И. И. — 1/4; Петрова А. С. — 3/4"
        self.assertEqual(describe(parse_shares(text)), text)

    def test_largest_owner(self):
        self.assertEqual(largest_owner(parse_shares("Иванов И. И. — 1/4; Петрова А. С. — 3/4")), "Петрова А. С.")
        with self.assertRaises(ValueError):
            largest_owner([])

    def test_schedule(self):
        rows = schedule(2026)
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[2][1:3], (date(2026, 3, 15), date(2026, 3, 25)))
        self.assertIn("с 15.03 по 25.03", render_schedule(2026))

if __name__ == "__main__":
    unittest.main()
'''

_FILES['tests/test_quality_refunds.py'] = r'''import unittest
from datetime import date, datetime
from decimal import Decimal as D

from kvartplata.ledger import Ledger
from kvartplata.models import Charge, Payment
from kvartplata.quality import (Outage, apply_reductions, excess_hours, journal, outage_hours,
                                parse_dispatch_log, reduction, summary)
from kvartplata.refunds import RefundRequest, decide, overpayment, registry, registry_line, render

from tests.fixtures import FEB, MARCH


def _cw(start, end, **kw):
    return Outage("cold_water", start, end, **kw)


class OutageTest(unittest.TestCase):
    def test_hours_inside_period(self):
        o = _cw(datetime(2026, 3, 10, 8, 0), datetime(2026, 3, 10, 14, 0))
        self.assertEqual(outage_hours(o, MARCH), 6)

    def test_end_before_start(self):
        with self.assertRaises(ValueError):
            _cw(datetime(2026, 3, 10, 8, 0), datetime(2026, 3, 9, 8, 0))

    def test_confirmed(self):
        self.assertTrue(_cw(datetime(2026, 3, 1), datetime(2026, 3, 2)).confirmed)
        self.assertFalse(_cw(datetime(2026, 3, 1), datetime(2026, 3, 2), source="resident").confirmed)
        self.assertTrue(_cw(datetime(2026, 3, 1), datetime(2026, 3, 2), source="resident", act="А-17").confirmed)

    def test_monthly_limit(self):
        outages = [_cw(datetime(2026, 3, 3, 9, 0), datetime(2026, 3, 3, 13, 0)),
                   _cw(datetime(2026, 3, 17, 9, 0), datetime(2026, 3, 17, 15, 0))]
        self.assertEqual(excess_hours(outages, "cold_water", MARCH), 2)

    def test_single_outage(self):
        outages = [_cw(datetime(2026, 3, 3, 9, 0), datetime(2026, 3, 3, 15, 0))]
        # <<C37
        self.assertEqual(excess_hours(outages, "cold_water", MARCH), 0)
        # ==
        self.assertEqual(excess_hours(outages, "cold_water", MARCH), 2)
        # >>

    def test_other_service_ignored(self):
        outages = [Outage("hot_water", datetime(2026, 3, 3, 9, 0), datetime(2026, 3, 3, 21, 0))]
        self.assertEqual(excess_hours(outages, "cold_water", MARCH), 0)

    def test_unknown_service(self):
        self.assertEqual(excess_hours([], "maintenance", MARCH), 0)

    def test_reduction(self):
        outages = [_cw(datetime(2026, 3, 3, 9, 0), datetime(2026, 3, 3, 13, 0)),
                   _cw(datetime(2026, 3, 17, 9, 0), datetime(2026, 3, 17, 15, 0))]
        charge = Charge("cold_water", MARCH, D("8.5"), D("52.31"), D("444.64"), "meter")
        self.assertEqual(reduction(charge, outages, MARCH), D("1.33"))
        apply_reductions([charge], outages, MARCH)
        self.assertEqual(charge.recalculation, D("-1.33"))
        self.assertIn("2 ч", charge.note)

    def test_journal(self):
        text = journal([_cw(datetime(2026, 3, 3, 9, 0), datetime(2026, 3, 3, 13, 0), source="resident")], MARCH)
        self.assertIn("не подтверждён", text)


def _ledger(paid="1000", charged="300"):
    led = Ledger("12345678905")
    led.add_charge(FEB, D(charged))
    led.add_payment(Payment("12345678905", date(2026, 3, 5), D(paid)))
    return led


class RefundTest(unittest.TestCase):
    def test_overpayment(self):
        self.assertEqual(overpayment(_ledger(), date(2026, 4, 6)), D("700.00"))
        self.assertEqual(overpayment(_ledger(paid="200"), date(2026, 4, 6)), D("0.00"))

    def test_full_refund(self):
        d = decide(RefundRequest("12345678905", date(2026, 4, 6)), _ledger())
        self.assertTrue(d.approved)
        self.assertEqual(d.amount, D("700.00"))

    def test_partial_refund(self):
        d = decide(RefundRequest("12345678905", date(2026, 4, 6), D("250")), _ledger())
        self.assertEqual((d.approved, d.amount), (True, D("250.00")))

    def test_amount_above_overpayment(self):
        d = decide(RefundRequest("12345678905", date(2026, 4, 6), D("900")), _ledger())
        # <<C41
        self.assertFalse(d.approved)
        # ==
        self.assertEqual((d.approved, d.amount), (True, D("700.00")))
        # >>

    def test_not_owner(self):
        d = decide(RefundRequest("12345678905", date(2026, 4, 6), applicant_is_owner=False), _ledger())
        self.assertFalse(d.approved)

    def test_small_amount(self):
        d = decide(RefundRequest("12345678905", date(2026, 4, 6)), _ledger(paid="350"))
        self.assertFalse(d.approved)

    def test_cash(self):
        d = decide(RefundRequest("12345678905", date(2026, 4, 6), method="cash"), _ledger())
        self.assertFalse(d.approved)

    def test_render(self):
        req = RefundRequest("12345678905", date(2026, 4, 6))
        text = render(req, decide(req, _ledger()))
        self.assertIn("700,00 руб.", text)
        self.assertIn("на карту", text)



class DispatchLogTest(unittest.TestCase):
    LOG = "# АДС, март\nХВС;2026-03-10T08:00;2026-03-10T14:00;А-1\n\nгвс;2026-03-11T09:00;2026-03-11T12:00\n"

    def test_parse(self):
        outages = parse_dispatch_log(self.LOG)
        self.assertEqual([o.service for o in outages], ["cold_water", "hot_water"])
        self.assertEqual(outages[0].act, "А-1")
        self.assertTrue(all(o.confirmed for o in outages))

    def test_bad_lines(self):
        with self.assertRaises(ValueError):
            parse_dispatch_log("ХВС;2026-03-10T08:00\n")
        with self.assertRaises(ValueError):
            parse_dispatch_log("ПАР;2026-03-10T08:00;2026-03-10T14:00\n")

    def test_summary(self):
        self.assertEqual(summary(parse_dispatch_log(self.LOG), MARCH)["hot_water"], (3, 0))


class RegistryTest(unittest.TestCase):
    def _req(self, method="bank_account"):
        return RefundRequest("12345678905", date(2026, 4, 6), method=method)

    def test_line(self):
        dec = decide(self._req(), _ledger())
        line = registry_line(self._req(), dec, "Петров Пётр Петрович", "40817810000000000001")
        # <<C55
        self.assertEqual(line, "R|12345678905|Петров Пётр Петрович|40817810000000000001|700.00")
        # ==
        self.assertEqual(line, "R|12345678905|Петров Пётр Петрович|40817810000000000001|70000")
        # >>

    def test_header(self):
        dec = decide(self._req(), _ledger())
        text = registry([(self._req(), dec, "Петров Пётр Петрович", "40817810000000000001")], date(2026, 4, 21))
        self.assertEqual(text.splitlines()[0], "#REFUND|21.04.2026|1|700.00")

    def test_card_not_in_registry(self):
        dec = decide(self._req("card"), _ledger())
        with self.assertRaises(ValueError):
            registry_line(self._req("card"), dec, "Петров Пётр Петрович", "40817810000000000001")

if __name__ == "__main__":
    unittest.main()
'''

_REVIEW = r'''# Ревью PR #412 «kvartplata 2.4: весенние исправления расчётов»

**Автор PR:** Игорь Тарасов (@itarasov)
**Ревьюеры:** Дмитрий Орлов (@dorlov, методолог расчётного отдела), Сергей Липин (@slipin, разработчик),
Марина Жукова (@mzhukova, QA), Анна Ковалёва (@akovaleva, ведущий разработчик — финальное слово в спорных вопросах).

> **Описание PR (Игорь Тарасов):** Собрал в одну ветку накопившиеся правки по расчётам перед весенней
> индексацией. Прошу посмотреть всё, что считаете нужным, — после ревью доделаю по комментариям.

> **Анна Ковалёва:** Коллеги, ревью шло по коммиту `7c1e9a2`. После этого Игорь сделал rebase на `main`
> (там были правки докстрингов и импортов), поэтому номера строк в части комментариев могли уехать —
> ориентируйтесь на имена функций. Напоминаю правила: «nit» и «по желанию» — необязательно; вопросы без
> просьбы что-то поменять — это просто вопросы; если ревьюер сам снял замечание или я написала, что не
> делаем, — не делаем; если ревьюеры спорят, делаем так, как я решила в треде. Всё остальное — к исправлению.
> Тесты должны оставаться зелёными: если правка меняет поведение, которое зафиксировано в `tests/`,
> тест нужно поправить под новое поведение.

---

### [1] `kvartplata/money.py:{{L:kvartplata/money.py:^"""Денежная}}` — докстринг модуля

**Марина Жукова:** nit: в докстринге модуля перечислены не все функции (нет `floor_money`,
`money_sum`, `percent_of`). По желанию.

> **Игорь Тарасов:** Поправлю, если руки дойдут.

---

### [2] `kvartplata/money.py:{{L:kvartplata/money.py:def to_money}}` — `to_money`

**Сергей Липин:** `to_money("1,5")` падает с `ValueError`. Давайте принимать и запятую как десятичный
разделитель — пользователи вводят суммы по-русски.

> **Игорь Тарасов:** Можно, это одна строчка.
>
> **Анна Ковалёва:** Сергей, а где именно пользователи вводят суммы? Импорт показаний и реестры банков
> разбирают запятую у себя.
>
> **Сергей Липин:** Посмотрел — действительно, всё пользовательское идёт через `importer` и
> `bank_registry`, там запятая уже обрабатывается (или будет, см. тред про импорт). `to_money` пусть
> остаётся строгой: лучше упасть, чем молча превратить «1,234» (тысяча двести тридцать четыре) в 1,23 ₽. Снимаю своё замечание.

---

### [3] `kvartplata/money.py:{{L:kvartplata/money.py:return Decimal\(repr\(value\)\):+4}}` — float в `to_money`

**Марина Жукова:** Вопрос: почему float переводится через `repr`, а не просто `Decimal(value)`?

> **Игорь Тарасов:** `Decimal(0.1)` даёт `0.1000000000000000055511151231257827…`, а через `repr` —
> ровно `0.1`. Для денег нам нужно второе.
>
> **Марина Жукова:** Понятно, спасибо.

---

### [4] `kvartplata/money.py:{{L:kvartplata/money.py:def round_money}}` — `round_money`

**Дмитрий Орлов:** Здесь банковское округление (`ROUND_HALF_EVEN`): 0,125 → 0,12, 2,665 → 2,66.
По нашей методике и по всем договорам суммы округляются до копеек по обычным математическим
правилам — половина копейки округляется вверх (по модулю): 0,125 → 0,13, −0,125 → −0,13.
Из-за этого у нас в сверке с банком «плавают» копейки. Прошу исправить.

> **Игорь Тарасов:** Да, это моя ошибка, в старой версии было `ROUND_HALF_UP`. Верну.

---

### [5] `kvartplata/money.py:{{L:kvartplata/money.py:def split_evenly}}` — `split_evenly`, остаток копеек

**Сергей Липин:** Сейчас нераспределённые копейки уходят в последние части: 100 ₽ на 3 = 33,33 +
33,33 + 33,34. В графиках рассрочки и во всех наших договорах наоборот: лишние копейки идут
в первые части — 33,34 + 33,33 + 33,33 (по одной копейке, начиная с первой части).

> **Марина Жукова:** А я бы оставила как есть — привычнее, когда «хвост» в последнем платеже. Только
> докстринг поправить, чтобы было понятно, куда идут копейки.
>
> **Сергей Липин:** Тогда график в личном кабинете расходится с тем, что печатаем в соглашении.
>
> **Анна Ковалёва:** Делаем как предложил Сергей: остаток — по копейке в первые части. Марина,
> учти в тестах рассрочки.

---

### [6] `kvartplata/money.py:{{L:kvartplata/money.py:def allocate_by_weights}}` — `allocate_by_weights`

**Сергей Липин:** nit: переменная `ws` читается плохо, я бы назвал `weights_dec`. Не блокирует.

---

### [7] `kvartplata/money.py:{{L:kvartplata/money.py:def format_rub:+3}}` — `format_rub`

**Марина Жукова:** В квитанции сумма 12345,67 печатается без разделителя разрядов — жильцы жалуются,
что трудно читать. Нужно разбивать целую часть на группы по три цифры пробелом: `12 345,67 руб.`,
`1 234 567,80 руб.`, для отрицательных — `-1 234,50`. Суммы меньше тысячи — как сейчас (`999,99 руб.`).
Формат дробной части и суффикса не меняем.

> **Игорь Тарасов:** Обычный пробел или неразрывный?
>
> **Марина Жукова:** Обычный пробел — у нас моноширинная печать, неразрывный принтер печатает криво.
>
> **Анна Ковалёва:** Согласна, обычный пробел.

---

### [8] `kvartplata/money.py:{{L:kvartplata/money.py:def kopecks}}` — `kopecks`

**Марина Жукова:** Вопрос: `kopecks(-0.5)` что вернёт? Для отрицательных сумм это где-то используется?

> **Игорь Тарасов:** Вернёт −50. Сейчас используется только в QR, там суммы неотрицательные.
>
> **Марина Жукова:** Ок, вопрос снят.

---

### [9] `kvartplata/periods.py:{{L:kvartplata/periods.py:def parse}}` — `Period.parse`

**Дмитрий Орлов:** Из реестров банка и выгрузок Госуслуг период приходит в виде «03.2026» (месяц
точка год, месяц может быть и без ведущего нуля: «3.2026»). Сейчас `Period.parse` понимает только
`2026-03`. Нужно принимать оба формата; ISO, конечно, оставить.

> **Игорь Тарасов:** Сделаю.

---

### [10] `kvartplata/periods.py:{{L:kvartplata/periods.py:def prev\(self\):-6}}` — `Period.prev`

**Марина Жукова:** Баг: `Period(2026, 1).prev()` возвращает `Period(2026, 12)` вместо декабря
предыдущего года. Из-за этого для январских квитанций входящее сальдо берётся из будущего периода.

> **Игорь Тарасов:** Ох. Да, исправлю.

---

### [11] `kvartplata/periods.py:{{L:kvartplata/periods.py:def months_between}}` — `months_between`

**Сергей Липин:** Вопрос: `months_between` не включает конечный период — это специально?

> **Игорь Тарасов:** Специально: это разность индексов месяцев, её используют отчёты. «Включительно»
> считает `period_range`.
>
> **Сергей Липин:** Понял.

---

### [12] `kvartplata/calendar_ru.py:{{L:kvartplata/calendar_ru.py:def next_workday}}` — `next_workday`

**Сергей Липин:** `next_workday` пропускает только субботу и воскресенье, а праздники из `HOLIDAYS`
игнорирует: `next_workday(date(2026, 1, 3))` сейчас даёт 5 января, хотя это праздник. Функция должна
возвращать ближайший рабочий день с учётом праздников (для 3 января 2026 — 12 января).

> **Игорь Тарасов:** Да, там должно быть `is_workday`. Поправлю.

---

### [13] `kvartplata/calendar_ru.py:{{L:kvartplata/calendar_ru.py:^HOLIDAYS}}` — список праздников

**Дмитрий Орлов:** Праздники на 2027 год придётся снова вписывать руками. Может, грузить
производственный календарь из файла?

> **Анна Ковалёва:** Хорошая идея, но это отдельная задача — заведу KV-231. В этом PR не делаем.

---

### [14] `kvartplata/billing.py:{{L:kvartplata/billing.py:def due_date}}` — `due_date`

**Дмитрий Орлов:** Срок оплаты — 10-е число следующего месяца. Если 10-е выпадает на выходной
или праздничный день, срок переносится на ближайший следующий рабочий день (например, за декабрь
2025 — не 10.01.2026, суббота, а 12.01.2026). Сейчас всегда 10-е. От срока считаются пени, так что
это важно.

> **Игорь Тарасов:** Использую `next_workday` из `calendar_ru` (после исправления из треда [12]).

---

### [15] `kvartplata/tariffs.py:{{L:kvartplata/tariffs.py:def rate_for\(:+5}}` — `TariffTable.rate_for`

**Марина Жукова:** Тариф, который вводится с 1 июля, 1 июля не применяется — применяется только
со 2-го. В `rate_for` строгое сравнение даты с началом действия. По докстрингу модуля тариф действует
с даты начала включительно. Из-за этого июльские квитанции (тариф берётся на 1-е число) считаются
по старому тарифу.

> **Игорь Тарасов:** Точно. Поправлю на нестрогое.

---

### [16] `kvartplata/tariffs.py:{{L:kvartplata/tariffs.py:def from_csv}}` — `from_csv`

**Сергей Липин:** nit: вместо ручного `replace(",", ".")` можно было бы сделать общий хелпер.
Необязательно.

---

### [17] `kvartplata/normatives.py:{{L:kvartplata/normatives.py:def coefficient}}` — `coefficient`

**Дмитрий Орлов:** Повышающий коэффициент 1,5 не применяется, если установка прибора учёта
технически невозможна (есть акт обследования). У нас это поле `Account.meter_impossible` — множество
услуг, по которым такой акт есть. Сейчас оно нигде не учитывается, и таким жильцам начисляется
норматив × 1,5. Для услуг из `meter_impossible` коэффициент должен быть 1.

> **Игорь Тарасов:** Добавлю проверку.

---

### [18] `kvartplata/normatives.py:{{L:kvartplata/normatives.py:def persons_for_norm}}` — `persons_for_norm`

**Марина Жукова:** А если `owners_count == 0` и никто не зарегистрирован?

> **Игорь Тарасов:** Тогда считаем на одного — там `max(..., 1)`.
>
> **Марина Жукова:** Вижу, ок.

---

### [19] `kvartplata/meters.py:{{L:kvartplata/meters.py:def consumption}}` — `consumption`

**Сергей Липин:** Если счётчик «перевалил» через максимум (показание было 99 990, стало 00 005),
сейчас летит `ReadingError`. Надо считать переход через ноль с учётом разрядности прибора
(`capacity`): 99 990 → 5 при `capacity=99999` — это 15 м³; для четырёхразрядного счётчика
(`capacity=9999`) 9 990 → 10 — это 20.

> **Дмитрий Орлов:** Поддерживаю, у нас в феврале было три таких случая, все ушли в ручную обработку.

---

### [20] `kvartplata/meters.py:{{L:kvartplata/meters.py:def average_monthly}}` — `average_monthly`

**Дмитрий Орлов:** Среднемесячное потребление берётся по показаниям за последние 12 месяцев
(`window = 12` захардкожено), а по правилам — за последние 6. В настройках для этого есть
`Settings.average_window_months` (по умолчанию 6) — используйте её вместо константы.

> **Игорь Тарасов:** Ок.

---

### [21] `kvartplata/meters.py:{{L:kvartplata/meters.py:def resolve_meter}}` — `resolve_meter`, начисление по среднему

**Марина Жукова:** Вопрос к методологам: сколько месяцев подряд можно начислять по среднему, если
жилец не передаёт показания? Сейчас, насколько я вижу, по среднему начисляется бесконечно.

> **Дмитрий Орлов:** По правилам — не более 3 расчётных периодов подряд. Начиная с 4-го периода
> без показаний начисляем по нормативу (как для помещения без прибора, но коэффициент при наличии
> прибора не применяется — это уже учтено в `coefficient`). В настройках это
> `Settings.average_max_periods`. Игорь, поправь, пожалуйста: сейчас это ошибка, а не вопрос.
>
> **Игорь Тарасов:** Понял. `missing_streak` уже считает число периодов без показаний, добавлю условие.

---

### [22] `kvartplata/meters.py:{{L:kvartplata/meters.py:def validate_value}}` — отрицательные показания

**Марина Жукова:** Нужна проверка, что показание не отрицательное и не больше разрядности.

> **Игорь Тарасов:** Она есть — `validate_value` чуть выше.
>
> **Марина Жукова:** Вижу, снимаю.

---

### [23] `kvartplata/services/electricity.py:{{L:kvartplata/services/electricity.py:def calculate}}` — двухтарифный учёт

**Дмитрий Орлов:** При двухтарифном учёте ночная зона начисляется по дневному тарифу: для строки
«ночь» берётся тариф зоны `day`. Каждая зона должна считаться по своему тарифу (`night` — 3,49 ₽
с января 2026, а не 7,95).

> **Игорь Тарасов:** Копипаста, исправлю.

---

### [24] `kvartplata/services/hot_water.py:{{L:kvartplata/services/hot_water.py:def components}}` — компонент на тепловую энергию

**Дмитрий Орлов:** Объём тепловой энергии на подогрев (Гкал) округляется до сотых **до** умножения
на тариф: 4 м³ × 0,0612 = 0,2448 Гкал превращается в 0,24 Гкал, и жилец недоплачивает 14,62 ₽.
Промежуточные величины не округляем — округляется только итоговая сумма компонента в рублях.

> **Игорь Тарасов:** Уберу `round(..., 2)`. В примечании тогда будет 0.2448 Гкал — это нормально?
>
> **Дмитрий Орлов:** Нормально.

---

### [25] `kvartplata/services/sewage.py:{{L:kvartplata/services/sewage.py:def calculate}}` — объём водоотведения

**Сергей Липин:** В докстринге модуля написано, что водоотведение = ХВС + ГВС, а в коде берётся
только холодная вода. Горячая тоже уходит в канализацию. Объём водоотведения должен быть суммой
объёмов ХВС и ГВС (определённых так же, как для самих услуг).

> **Дмитрий Орлов:** Подтверждаю, это ошибка.

---

### [26] `kvartplata/services/heating.py:{{L:kvartplata/services/heating.py:def calculate:-3}}` — площадь для отопления

**Дмитрий Орлов:** Отопление считается по общей площади (`total_area`), а должно — по отапливаемой
(`heated_area`, без балконов и лоджий). В докстринге модуля это написано, код не соответствует.

> **Игорь Тарасов:** Исправлю.

---

### [27] `kvartplata/services/heating.py:{{L:kvartplata/services/heating.py:^HEATING_SEASON}}` — отопление летом

**Марина Жукова:** Отопление начисляется и в июле. Давайте начислять только в отопительный сезон
(октябрь — апрель), константа `HEATING_SEASON` уже объявлена, но не используется.

> **Анна Ковалёва:** Нет. Наш регион выбрал равномерный способ оплаты — отопление начисляется
> равными долями все 12 месяцев, так и описано в модуле. `HEATING_SEASON` нужна для будущего режима
> «по фактическому потреблению». Не меняем.
>
> **Марина Жукова:** Поняла, тогда вопрос снят.

---

### [28] `kvartplata/services/capital_repair.py:{{L:kvartplata/services/capital_repair.py:def compensation_percent}}` — возраст для компенсации

**Дмитрий Орлов:** Компенсация положена собственнику, **достигшему** возраста 70 (80) лет — то есть
уже в тот месяц, когда на 1-е число ему исполнилось ровно 70 (80). Сейчас сравнение строгое, и
70-летние получают компенсацию только через год. Нужно: с 70 лет включительно — 50 %, с 80 лет
включительно — 100 %.

> **Игорь Тарасов:** Понял, исправлю обе границы.

---

### [29] `kvartplata/services/waste.py:{{L:kvartplata/services/waste.py:def persons}}` — ТКО без зарегистрированных

**Марина Жукова:** Если в квартире никто не зарегистрирован, ТКО начисляется 0 ₽. В докстринге
модуля (и в договоре с регоператором) — по числу собственников. Поправьте код.

> **Сергей Липин:** Может, оставить ноль? Жильцы будут спорить, что там никто не живёт.
>
> **Анна Ковалёва:** Нет, делаем по собственникам (`owners_count`), как в договоре. Спорные случаи
> решаются перерасчётом по заявлению.

---

### [30] `kvartplata/services/waste.py:{{L:kvartplata/services/waste.py:def calculate}}` — ТКО по площади?

**Сергей Липин:** Кстати, разве наш регион не перешёл на начисление ТКО по площади помещения?
Тогда нужно считать `total_area × ставка/м²`.

> **Дмитрий Орлов:** Сергей, это было в 2024-м как эксперимент. С 2026 года снова по числу
> проживающих (и по собственникам, если никто не зарегистрирован — см. тред выше).
>
> **Сергей Липин:** Точно, отзываю предложение.

---

### [31] `kvartplata/privileges.py:{{L:kvartplata/privileges.py:def discount_for}}` — социальная норма площади

**Дмитрий Орлов:** Льгота по услугам, начисляемым по площади (содержание, капремонт), должна
предоставляться только в пределах социальной нормы площади: 18 м² на человека, 33 м² для одиноко
проживающего (см. `social_norm_area`). Сейчас скидка считается от всей доли начисления: у ветерана
в квартире 54,3 м² на двоих скидка по содержанию получается 439,83 ₽ вместо 291,60 ₽. Для площадных
услуг долю жильца надо брать как тариф × min(площадь/число зарегистрированных, соцнорма).
Константа `AREA_SERVICES` для этого уже заведена.

> **Игорь Тарасов:** Понял. Для остальных услуг всё как было — доля начисления на человека?
>
> **Дмитрий Орлов:** Да.

---

### [32] `kvartplata/privileges.py:{{L:kvartplata/privileges.py:def resident_percent}}` — несколько льгот у одного жильца

**Марина Жукова:** У жильца две льготы — ветеран труда и инвалидность, обе дают 50 % по содержанию.
Итоговая скидка по содержанию — 100 %. Это правильно?

> **Дмитрий Орлов:** Нет. Льготы по одной и той же услуге не суммируются — применяется наибольшая
> из положенных (здесь 50 %). Игорь, поправь `resident_percent`.
>
> **Игорь Тарасов:** Сделаю.

---

### [33] `kvartplata/privileges.py:{{L:kvartplata/privileges.py:def apply_privileges}}` — примечание в строке

**Сергей Липин:** nit: в примечании список льгот формируется через множество и сортировку —
можно проще. Не принципиально.

---

### [34] `kvartplata/recalc.py:{{L:kvartplata/recalc.py:def full_days:+1}}` — полные дни отсутствия

**Дмитрий Орлов:** Перерасчёт делается за полные календарные дни отсутствия — день выезда и день
приезда не считаются. Сейчас они считаются. И порог: перерасчёт положен при отсутствии не менее
5 полных дней подряд — у нас в `Settings.absence_min_days` так и записано, а в коде захардкожено 3.
Пример: выехал 2 марта, вернулся 7 марта — полных дней 4 (3–6 марта), перерасчёта нет; выехал
1 марта, вернулся 8-го — 6 полных дней (2–7 марта), перерасчёт за 6 дней.

> **Игорь Тарасов:** Понял: и отрезок полных дней, и порог из настроек.

---

### [35] `kvartplata/recalc.py:{{L:kvartplata/recalc.py:^RECALC_SERVICES}}` — какие услуги пересчитываются

**Дмитрий Орлов:** При временном отсутствии не пересчитываются отопление и содержание жилого
помещения (они не зависят от числа проживающих). Сейчас они есть в `RECALC_SERVICES`. Остальной
список правильный.

---

### [36] `kvartplata/penalties.py:{{L:kvartplata/penalties.py:def daily_fraction}}` — ставка пеней после 90 дней

**Дмитрий Орлов:** Сейчас пени всё время считаются по 1/300 ключевой ставки. По ЖК РФ (ст. 155):
с 31-го по 90-й день просрочки — 1/300, начиная с 91-го дня — 1/130. Граница есть в настройках:
`penalty_high_rate_from_day = 91`.

> **Сергей Липин:** По-моему, 1/130 применяется уже с 90-го дня: «по истечении девяноста дней»…
>
> **Дмитрий Орлов:** «По истечении 90 дней» — это как раз с 91-го. 90-й день ещё по 1/300.
>
> **Анна Ковалёва:** Делаем как предложил Дмитрий: 1/130 с 91-го дня (порог из настроек).

---

### [37] `kvartplata/penalties.py:{{L:kvartplata/penalties.py:def penalty\(}}` — округление пеней

**Марина Жукова:** Пени округляются до копеек **каждый день**, а потом суммируются. На долге 1000 ₽
за 10 дней получается 5,20 ₽ вместо 5,17 ₽ — сверка с банком не сходится. Нужно суммировать дневные
начисления без округления и округлять один раз — итоговую сумму.

> **Игорь Тарасов:** Согласен.

---

### [38] `kvartplata/penalties.py:{{L:kvartplata/penalties.py:def penalty\(:+6}}` — мораторий

**Сергей Липин:** Предлагаю добавить мораторий: по долгам со сроком оплаты до 1 января 2026 года
пени не начислять.

> **Анна Ковалёва:** Мораторий на пени закончился ещё в 2025 году, сейчас он не действует.
> Добавлять не нужно.
>
> **Сергей Липин:** Ок, убираю из предложений.

---

### [39] `kvartplata/penalties.py:{{L:kvartplata/penalties.py:rate = settings.key_rate_on}}` — ключевая ставка

**Марина Жукова:** Вопрос: ставку берём на дату расчёта пеней, а не на дату возникновения долга?

> **Дмитрий Орлов:** Да, для нас так (на дату расчёта). Менять не нужно.

---

### [40] `kvartplata/payments.py:{{L:kvartplata/payments.py:def _queue}}` — очерёдность погашения

**Дмитрий Орлов:** Платёж без указания периода сейчас гасит сначала **самые новые** периоды. Должно
быть наоборот: сначала самые старые (по возрастанию периода), затем более поздние.

> **Игорь Тарасов:** Поправлю сортировку.

---

### [41] `kvartplata/payments.py:{{L:kvartplata/payments.py:def _queue:+9}}` — пени в очереди погашения

**Дмитрий Орлов:** И второе в той же функции: пени гасятся в самую последнюю очередь — только после
основного долга **по всем** периодам. Сейчас после основного долга периода сразу гасятся пени этого
периода. Порядок пеней между собой — тоже от старых периодов к новым.

> **Игорь Тарасов:** Понял: сначала весь основной долг (по возрастанию периодов), потом все пени.

---

### [42] `kvartplata/payments.py:{{L:kvartplata/payments.py:def allocate}}` — имя `_queue`

**Сергей Липин:** nit: `_queue` → `_repayment_order`? По желанию.

---

### [43] `kvartplata/ledger.py:{{L:kvartplata/ledger.py:def balance}}` — переплата

**Марина Жукова:** Если жилец переплатил, `balance` возвращает 0 — переплата «теряется», в квитанции
не видно аванса, и итог к оплате завышен. Сальдо должно быть отрицательным (аванс), как и написано
в докстринге `balance`.

> **Игорь Тарасов:** Уберу `max(..., 0)`. В квитанции «итого к оплате» и так ограничено нулём снизу.

---

### [44] `kvartplata/ledger.py:{{L:kvartplata/ledger.py:def months_in_debt}}` — `months_in_debt`

**Сергей Липин:** Вопрос: `months_in_debt` считает периоды с любым непогашенным остатком, даже
с копейкой?

> **Дмитрий Орлов:** Да, так и нужно для отчёта о должниках.

---

### [45] `kvartplata/receipt.py:{{L:kvartplata/receipt.py:def qr_payload}}` — сумма в QR-коде

**Сергей Липин:** По ГОСТ Р 56042-2014 поле `Sum` — сумма **в копейках**, целым числом, без
разделителя: 8 802,05 ₽ → `Sum=880205`. Сейчас пишем `Sum=8802.05`, и часть банковских приложений
читает это как 88 ₽. Прошу исправить.

> **Игорь Тарасов:** Там есть `kopecks()`, использую его.

---

### [46] `kvartplata/receipt.py:{{L:kvartplata/receipt.py:def payer_name}}` — ФИО плательщика

**Дмитрий Орлов:** В квитанции и в QR-коде (`LastName=`) печатается полное ФИО. Юристы просят
ограничиться фамилией и инициалами: «Петров Пётр Петрович» → «Петров П. П.» (инициалы через пробел,
с точками). Если в ФИО одно слово — печатаем как есть.

> **Марина Жукова:** А полное ФИО не нужно для идентификации платежа?
>
> **Анна Ковалёва:** Нет, платёж идентифицируется по лицевому счёту. Делаем как сказал Дмитрий.

---

### [47] `kvartplata/receipt.py:{{L:kvartplata/receipt.py:def visible_charges}}` — нулевые строки

**Марина Жукова:** Предлагаю не печатать строки услуг, по которым к оплате 0 (например, капремонт
при компенсации 100 %) — квитанция станет короче.

> **Дмитрий Орлов:** Так нельзя: в платёжном документе должны быть все начисленные услуги, включая
> строки с нулём после компенсации, — это требование к форме документа.
>
> **Марина Жукова:** Поняла, отзываю.

---

### [48] `kvartplata/receipt.py:{{L:kvartplata/receipt.py:^WIDTH}}` — ширина квитанции

**Сергей Липин:** nit: 78 символов — магическое число, можно вынести в настройки. Не обязательно.

---

### [49] `kvartplata/export.py:{{L:kvartplata/export.py:^COLUMNS}}` — формат CSV для бухгалтерии

**Марина Жукова:** Бухгалтерия открывает выгрузку в Excel, и она разваливается: разделитель —
запятая, а дробная часть — через точку. Нужно: разделитель столбцов `;`, десятичный разделитель —
запятая (`8,5`, `444,64`). Относится к обеим выгрузкам модуля.

> **Сергей Липин:** Не согласен: CSV — машинный формат, пусть будет `,` и точка, а бухгалтерия
> пусть импортирует через мастер.
>
> **Анна Ковалёва:** Эту выгрузку читает только бухгалтерия, машинного потребителя у неё нет.
> Делаем как предложила Марина.

---

### [50] `kvartplata/importer.py:{{L:kvartplata/importer.py:def parse_value}}` — десятичная запятая в показаниях

**Марина Жукова:** Управляющие компании присылают показания с десятичной запятой: `123,5`. Сейчас
такая строка попадает в ошибки «не число». Нужно принимать и запятую, и точку.

> **Игорь Тарасов:** Да, добавлю.

---

### [51] `kvartplata/importer.py:{{L:kvartplata/importer.py:def import_readings}}` — дубликаты показаний

**Дмитрий Орлов:** Если в файле несколько показаний одного прибора за один период, сейчас
остаётся первое по порядку в файле, остальные отбрасываются. Нужно оставлять показание с самой
поздней датой снятия (`taken_on`) — независимо от порядка строк в файле. Счётчик `duplicates`
пусть считает, как сейчас, число отброшенных строк.

> **Сергей Липин:** А если даты одинаковые?
>
> **Дмитрий Орлов:** Тогда более позднее в файле. Но это редкость.

---

### [52] `kvartplata/validators.py:{{L:kvartplata/validators.py:def account_control_digit}}` — контрольная цифра лицевого счёта

**Сергей Липин:** Контрольная цифра считается по цифрам в обратном порядке (`reversed`), а по
описанию в докстринге и по алгоритму банка веса 1, 3, 1, 3, … применяются начиная с первой (левой)
цифры. Из-за этого часть реальных лицевых счетов в реестрах банка отбрасывается как «некорректные».
Пример: для `5000012345` контрольная цифра должна быть 2.

> **Игорь Тарасов:** Уберу `reversed`.

---

### [53] `kvartplata/reports.py:{{L:kvartplata/reports.py:def debtors}}` — критерий должника

**Дмитрий Орлов:** Должник — тот, у кого долг за 3 **и более** периода (`min_months`). Сейчас
в отчёт попадают только те, у кого больше трёх.

---

### [54] `kvartplata/cli.py:{{L:kvartplata/cli.py:def build_parser}}` — рассылка уведомлений

**Марина Жукова:** Было бы удобно добавить команду `notices`, которая печатает уведомления должникам.

> **Анна Ковалёва:** Полезно, но не в этом PR — завела KV-240.

---

### [55] `kvartplata/storage.py:{{L:kvartplata/storage.py:^class Store}}` — хранение

**Сергей Липин:** Вопрос: почему JSON, а не SQLite? На больших домах будет медленно.

> **Анна Ковалёва:** JSON только для демо и тестов, в проде — база через отдельный слой. Не трогаем.

---

### [56] `kvartplata/subsidies.py:{{L:kvartplata/subsidies.py:^MAX_SHARE}}` — доля расходов

**Марина Жукова:** `MAX_SHARE = 0.22` — может, вынести в `Settings`?

> **Игорь Тарасов:** Она уже параметр в `SubsidyParams`, константа — только значение по умолчанию.
>
> **Марина Жукова:** А, точно.

---

### [57] `kvartplata/installments.py:{{L:kvartplata/installments.py:def make_agreement}}` — проценты по рассрочке

**Сергей Липин:** Вопрос: проценты за рассрочку не начисляем совсем?

> **Дмитрий Орлов:** Не начисляем, так решило правление УК. Всё верно.

---

### [58] `kvartplata/bank_registry.py:{{L:kvartplata/bank_registry.py:def parse_registry}}` — реестры банков

**Марина Жукова:** nit: в сообщении «контрольные суммы не сходятся» стоит писать, сколько ожидалось
и сколько получилось, человеческими словами. По желанию.

---

### [59] `kvartplata/people.py:{{L:kvartplata/people.py:def _surname_dative}}` — несклоняемые фамилии

**Сергей Липин:** Вопрос: «Ким» в дательном падеже для мужчины будет «Киму» — это правильно?

> **Игорь Тарасов:** Да, мужские фамилии на согласный склоняются, женские — нет. Так и работает.

---

### [60] `kvartplata/verification.py:{{L:kvartplata/verification.py:def is_expired}}` — поверка

**Дмитрий Орлов:** Показания прибора с истёкшим сроком поверки не должны приниматься — объём надо
считать по среднему/нормативу.

> **Анна Ковалёва:** Согласна по сути, но методика ещё не утверждена (ждём письмо от ГЖИ).
> В этом PR не делаем — KV-236.

---

### [61] `kvartplata/house.py:{{L:kvartplata/house.py:def distribute}}` — ОДН

**Марина Жукова:** Вопрос: если экономия (объём ОДН отрицательный), то ничего не распределяем?

> **Дмитрий Орлов:** Да, отрицательный ОДН не распределяется. Всё правильно.

---

### [62] `kvartplata/services/hot_water.py:{{L:kvartplata/services/hot_water.py:def calculate}}` — «тариф» в строке ГВС

**Сергей Липин:** nit: в строке ГВС в колонке «Тариф» печатается эффективная цена за м³ — может,
подписать это в примечании? Не обязательно.

---

### [63] `kvartplata/models.py:{{L:kvartplata/models.py:def total}}` — `Charge.total`

**Марина Жукова:** Вопрос: перерасчёт хранится со знаком минус и прибавляется, а скидка — с плюсом
и вычитается?

> **Игорь Тарасов:** Да: `discount` всегда неотрицательная, `recalculation` — со знаком.
>
> **Марина Жукова:** Понятно.

---

### [64] `kvartplata/addresses.py:{{L:kvartplata/addresses.py:def normalize}}` — адреса

**Сергей Липин:** nit: регулярки в `REPLACEMENTS` можно скомпилировать заранее. По желанию.

---

### [65] `kvartplata/gis_export.py:{{L:kvartplata/gis_export.py:def export}}` — формат выгрузки в ГИС ЖКХ

**Марина Жукова:** Раз в `export.py` меняем разделитель на `;` и десятичную запятую (тред [49]), может,
и здесь сделать так же — для единообразия?

> **Анна Ковалёва:** Нет! Формат обмена с ГИС ЖКХ задан внешней системой: `|` и точка. Тред [49]
> касается только бухгалтерской выгрузки `export.py`. Здесь ничего не меняем.
>
> **Марина Жукова:** Поняла.

---

### [66] `kvartplata/court.py:{{L:kvartplata/court.py:def claim_duty}}` — госпошлина

**Сергей Липин:** Вопрос: шкала госпошлины — из новой редакции 333.19 (после сентября 2024)?

> **Дмитрий Орлов:** Да, из действующей. Проверял на трёх делах в январе, совпало.

---

### [67] `kvartplata/court.py:{{L:kvartplata/court.py:def prepare_claim:+12}}` — срок оплаты в расчёте пеней для суда

**Марина Жукова:** В `prepare_claim` срок оплаты считается своей функцией (10-е число без переноса),
а не через `billing.due_date`. После правки из треда [14] суммы пеней в квитанции и в заявлении могут
разойтись на день.

> **Анна Ковалёва:** Знаю. Для суда юристы пока считают пени от 10-го числа без переноса — так
> в их шаблонах. Отдельно обсудим с юристами (KV-244), в этом PR `court.py` не трогаем.

---

### [68] `kvartplata/anomalies.py:{{L:kvartplata/anomalies.py:def meter_flags}}` — порог «резкого роста»

**Дмитрий Орлов:** Порог `spike_factor = 3` — не слишком грубо? Контролёры просили 2,5.

> **Анна Ковалёва:** Это параметр функции, контролёры могут передать своё значение. Значение
> по умолчанию оставляем.
>
> **Дмитрий Орлов:** Хорошо.

---

### [69] `kvartplata/anomalies.py:{{L:kvartplata/anomalies.py:def meter_flags:+11}}` — цикл по нулевым периодам

**Сергей Липин:** nit: цикл подсчёта нулевых периодов можно переписать через `itertools`. По желанию.

---

### [70] `kvartplata/debt_collection.py:{{L:kvartplata/debt_collection.py:def plan}}` — ограничение электроснабжения

**Марина Жукова:** Вопрос: ограничение — через 3 рабочих дня после срока из уведомления, а не
календарных?

> **Дмитрий Орлов:** Рабочих. Так в регламенте. Всё верно.

---

### [71] `kvartplata/settings_io.py:{{L:kvartplata/settings_io.py:def settings_from_dict}}` — неизвестные ключи

**Сергей Липин:** Может, неизвестные ключи не ронять, а просто писать предупреждение?

> **Анна Ковалёва:** Нет, пусть падает — у нас уже был случай, когда опечатка в имени ставки
> месяц молча игнорировалась. Оставляем как есть.

---

### [72] `kvartplata/indexation.py:{{L:kvartplata/indexation.py:^LIMIT_PERCENT}}` — предельный индекс

**Дмитрий Орлов:** Предельный индекс на 2026 год для нас — 11,9 %. В коде так и есть, спасибо.

---

### [73] `kvartplata/privileges_report.py:{{L:kvartplata/privileges_report.py:def category_for}}` — категория льготы в отчёте

**Марина Жукова:** Если исправляем суммирование льгот (тред [32]), отчёт для соцзащиты тоже нужно
менять?

> **Дмитрий Орлов:** Нет, отчёт уже относит скидку к одной категории с наибольшим процентом —
> он согласуется с новым правилом. Трогать не надо.

---

### [74] `kvartplata/receipt_html.py:{{L:kvartplata/receipt_html.py:def render}}` — HTML-квитанция

**Сергей Липин:** Вопрос: HTML-версия берёт ФИО плательщика и строку QR из `receipt`?

> **Игорь Тарасов:** Да, через `receipt.payer_name` и `receipt.qr_payload`, так что правки из тредов
> [45] и [46] автоматически попадут и сюда.
>
> **Сергей Липин:** Отлично.

---

### [75] `tests/test_money.py:{{L:tests/test_money.py:class SplitTest}}` — тесты

**Марина Жукова:** Напоминаю: в `tests/` есть тесты, которые фиксируют старое поведение (округление,
остаток копеек, формат выгрузки, QR и т. д.). После правок их нужно обновить, а не удалить.

> **Игорь Тарасов:** Да, прогоню и поправлю всё, что упадёт по делу.

---

### [76] `kvartplata/statement.py:{{L:kvartplata/statement.py:def build}}` — пени в выписке

**Дмитрий Орлов:** В выписку из лицевого счёта хорошо бы добавить столбец с пенями.

> **Анна Ковалёва:** Согласна, но это доработка формата для юристов, отдельная задача (KV-247).
> Не в этом PR.

---

### [77] `kvartplata/corrections.py:{{L:kvartplata/corrections.py:^APPROVAL_LIMIT}}` — порог подтверждения

**Сергей Липин:** Порог «четырёх глаз» 5 000 ₽ маловат, давайте 10 000?

> **Дмитрий Орлов:** Порог утверждён приказом по УК, менять его в коде без приказа нельзя.
>
> **Сергей Липин:** Тогда снимаю.

---

### [78] `kvartplata/corrections.py:{{L:kvartplata/corrections.py:def apply}}` — корректировки в строке начисления

**Марина Жукова:** Вопрос: корректировка попадает в `recalculation` — значит, в квитанции она
печатается в строке «перерасчёт»?

> **Игорь Тарасов:** Да, и основание добавляется в примечание строки.

---

### [79] `kvartplata/periods_lock.py:{{L:kvartplata/periods_lock.py:def reopen}}` — переоткрытие периода

**Сергей Липин:** Вопрос: переоткрыть можно только последний закрытый период? А если ошибка
обнаружилась два месяца назад?

> **Дмитрий Орлов:** Тогда корректировкой в текущем периоде, переоткрывать глубоко нельзя.
> Всё правильно.

---

### [80] `kvartplata/sms.py:{{L:kvartplata/sms.py:def sms_amount}}` — формат суммы в SMS

**Марина Жукова:** После правки `format_rub` (тред [7]) может, и в SMS использовать `format_rub`?

> **Анна Ковалёва:** Нет, в SMS формат другой: без «,00», когда копеек нет, — ради длины сообщения.
> `sms_amount` не трогаем.

---

### [81] `kvartplata/forecast.py:{{L:kvartplata/forecast.py:def metered_volume}}` — двухтарифный учёт в прогнозе

**Сергей Липин:** nit: для двухтарифного учёта прогноз просто пропускает услугу — стоит хотя бы
написать это в примечании к отчёту. По желанию.

---

### [82] `kvartplata/gis_readings.py:{{L:kvartplata/gis_readings.py:def parse_line}}` — ширина полей

**Марина Жукова:** Вопрос: позиции полей — точно по спецификации ГИС?

> **Игорь Тарасов:** Да, по формату 13.1, проверено на выгрузке за февраль.

---

### [83] `kvartplata/meter_replacement.py:{{L:kvartplata/meter_replacement.py:def replacement_volume}}` — замена прибора без показания нового

**Марина Жукова:** Если после замены нет показания нового прибора, досчитываем по среднесуточному
старого. А не по нормативу?

> **Дмитрий Орлов:** По среднесуточному — так в нашем регламенте для месяца замены. Всё верно.

---

### [84] `kvartplata/tenants.py:{{L:kvartplata/tenants.py:def persons_in_period}}` — дробное число жильцов

**Сергей Липин:** Вопрос: калькуляторы услуг пока берут `registered_count` (целое), а не дробное
число из `tenants`. Подключать сейчас?

> **Анна Ковалёва:** Нет, подключение `tenants` к калькуляторам — отдельная задача (KV-250),
> там нужна миграция данных о регистрации. В этом PR не трогаем.

---

> **Анна Ковалёва:** Всем спасибо. Игорь, доделывай по тредам; вопросы, nit'ы и снятые замечания —
> на твоё усмотрение (снятые — не делаем). Перед мержем — зелёные тесты.

---

## Второй круг

> **Игорь Тарасов:** По просьбе расчётного отдела докатил в эту же ветку пять небольших модулей из задач
> KV-231…KV-235: `quality.py` (снижение платы за перерывы в предоставлении услуг), `refunds.py` (возврат
> переплаты), `owners.py` (раздел платы между долевыми собственниками), `reading_window.py` (окно приёма
> показаний) и `closing.py` (закрытие лицевого счёта при смене собственника). Тесты к ним —
> `tests/test_quality_refunds.py`, `tests/test_owners_window.py` и `tests/test_closing.py`. Посмотрите,
> пожалуйста; всё, о чём договорились в первом круге, остаётся в силе.
>
> **Анна Ковалёва:** Правила второго круга те же, что и первого. Отдельно: треды второго круга иногда
> ссылаются друг на друга и на первый круг — решение по треду может поменяться в более позднем треде,
> читайте до конца. Номера строк здесь указаны по коммиту `b4d07e1`, после него Игорь ещё правил докстринги.

---

### [85] `kvartplata/quality.py:{{L:kvartplata/quality.py:^"""Снижение платы}}` — от какой суммы считается снижение

**Марина Жукова:** Вопрос: 0,15 % за час считаются от суммы с учётом льготы или без? Если у жильца
скидка 50 %, снижение тоже вдвое меньше?

> **Дмитрий Орлов:** От начисления по тарифу, до льгот и перерасчётов, — так в Правилах. В модуле так и
> сделано (`charge.amount`), в докстринге это написано.
>
> **Марина Жукова:** Точно, вижу. Спасибо.

---

### [86] `kvartplata/quality.py:{{L:kvartplata/quality.py:def outage_hours:+3}}` — неполные часы

**Дмитрий Орлов:** Сейчас продолжительность перерыва округляется вниз до целых часов: перерыв с 10:00 до
15:20 даёт 5 часов, а перерыв на 40 минут — ноль. По Правилам (приложение 1) продолжительность считается
в часах, и неполный час считается за полный: 5 ч 20 мин — это 6 часов, 40 минут — 1 час, даже одна
минута — 1 час. Ровно 5 часов — 5 часов. Прошу исправить `outage_hours`.

> **Игорь Тарасов:** Понял, округление вверх. Сделаю.

---

### [87] `kvartplata/quality.py:{{L:kvartplata/quality.py:def outage_hours:+1}}` — перерыв на стыке месяцев

**Марина Жукова:** Нашла на тестовых данных: авария началась 28 февраля в 20:00 и закончилась 1 марта
в 6:00. `outage_hours` возвращает 10 часов и для февраля, и для марта — то есть одни и те же часы
снижают плату дважды. Для каждого периода нужно считать только ту часть перерыва, которая попала в
этот период: здесь в феврале 4 часа, в марте 6. Перерыв, целиком лежащий вне периода, даёт 0.

> **Игорь Тарасов:** Да, я про стык месяцев не подумал. Обрежу по границам периода — там уже есть
> `period_bounds`.
>
> **Дмитрий Орлов:** Подтверждаю, так и должно быть.

---

### [88] `kvartplata/quality.py:{{L:kvartplata/quality.py:^ALLOWED_HOURS}}` — летнее отключение горячей воды

**Сергей Липин:** Летом горячую воду отключают на две недели на профилактику. С лимитом 8 часов в месяц
это же сразу 300+ часов превышения? Может, для `hot_water` поднять лимит?

> **Дмитрий Орлов:** Плановая профилактика — это не перерыв в смысле этого модуля: диспетчерская её не
> заводит как `Outage`, график согласован с жилищной инспекцией. Лимиты в таблице верные, трогать не надо.
>
> **Сергей Липин:** Понял, спасибо.

---

### [89] `kvartplata/quality.py:{{L:kvartplata/quality.py:def relevant}}` — перерывы по заявкам жильцов

**Марина Жукова:** Сейчас перерывы, о которых сообщил сам жилец (`source="resident"`) и по которым нет
акта, не учитываются вовсе. Жильцы на это жалуются в каждом втором обращении. Давайте учитывать все
перерывы, независимо от подтверждения, — уберите фильтр по `confirmed`.

> **Игорь Тарасов:** Хорошо, уберу фильтр.

---

### [90] `kvartplata/quality.py:{{L:kvartplata/quality.py:def excess_hours}}` — однократная норма

**Дмитрий Орлов:** `excess_hours` учитывает только суммарную норму за месяц. Но у каждой услуги есть и
однократная норма (второе число в `ALLOWED_HOURS`): если один перерыв длился дольше неё, часы сверх
однократной нормы — тоже превышение, даже если за месяц в сумме уложились. Пример: один перерыв ХВС на
6 часов — суммарно норма 8 не превышена, но однократная (4) превышена на 2 часа, снижение за 2 часа.

Нужно считать превышение двумя способами — сверх месячной нормы (как сейчас) и сверх однократной
(сумма превышений по каждому перерыву отдельно) — и брать **большее** из двух.

> **Сергей Липин:** А не сумму двух превышений? Вроде бы это разные нарушения.
>
> **Дмитрий Орлов:** Нет, одни и те же часы нельзя оплатить дважды. Большее из двух.
>
> **Игорь Тарасов:** Сделаю через `max`.

---

### [91] `kvartplata/quality.py:{{L:kvartplata/quality.py:def reduction:+4}}` — имя переменной

**Сергей Липин:** nit: `value` в `reduction` — слишком общее имя, я бы назвал `amount_off`. Не блокирует.

---

### [92] `kvartplata/quality.py:{{L:kvartplata/quality.py:def reduction}}` — снижение больше платы

**Марина Жукова:** Проверила длинную аварию: холодной воды не было весь март (744 часа). Превышение —
больше 700 часов, 0,15 % × 700+ — это больше 100 %, и `reduction` возвращает сумму больше самого
начисления. Получается, что мы жильцу ещё и должны. Снижение не может превышать плату за услугу:
ограничьте результат суммой начисления строки (`charge.amount`).

> **Игорь Тарасов:** Логично. Добавлю ограничение сверху.
>
> **Анна Ковалёва:** Да, делаем.

---

### [93] `kvartplata/quality.py:{{L:kvartplata/quality.py:def apply_reductions}}` — перерасчёт или скидка

**Сергей Липин:** Вопрос: почему снижение пишется в `recalculation`, а не в `discount`? По смыслу это
скорее скидка.

> **Анна Ковалёва:** `discount` у нас — только льготы: `privileges_report` суммирует его для соцзащиты,
> и снижение туда попасть не должно. Перерасчёт — правильное место. Оставляем.

---

### [94] `kvartplata/quality.py:{{L:kvartplata/quality.py:def journal}}` — итоги в журнале

**Марина Жукова:** nit: в журнале перерывов не хватает итога по каждой услуге. По желанию, можно
следующим PR.

---

### [95] `kvartplata/refunds.py:{{L:kvartplata/refunds.py:def overpayment}}` — `shift(-1)` вместо `prev()`

**Сергей Липин:** Вопрос: почему в `overpayment` период берётся через `shift(-1)`, а не `prev()`?

> **Игорь Тарасов:** Писал модуль до того, как нашли баг в `prev` для января (тред [10]); `shift(-1)` и
> так работает правильно. После исправления `prev` разницы не будет, можно оставить как есть.
>
> **Сергей Липин:** Ок, принято.

---

### [96] `kvartplata/refunds.py:{{L:kvartplata/refunds.py:def decision_deadline}}` — срок рассмотрения

**Дмитрий Орлов:** Срок рассмотрения заявления — 10 **рабочих** дней со дня подачи (регламент возврата,
п. 4.2), а сейчас прибавляются календарные. Считайте по производственному календарю — в
`calendar_ru` для этого есть `add_workdays`. Например, заявление от 6 апреля 2026 — решение до
20 апреля, а не до 16-го.

> **Игорь Тарасов:** Да, импорт `add_workdays` у меня даже остался, забыл применить. Исправлю.

---

### [97] `kvartplata/refunds.py:{{L:kvartplata/refunds.py:^MIN_REFUND}}` — минимальная сумма возврата

**Сергей Липин:** Откуда минимум в 100 ₽? Банк берёт с нас комиссию за перевод, но это наши проблемы,
а не жильца. Предлагаю минимум убрать совсем.

> **Дмитрий Орлов:** Минимум установлен регламентом возврата, убирать его нельзя. Но есть случай, когда
> он действительно мешает: собственник продал квартиру и закрывает лицевой счёт. Тогда переплату нужно
> вернуть любую, хоть 3 рубля, иначе она зависнет на закрытом счёте навсегда. Предлагаю добавить в
> `RefundRequest` поле `closing: bool = False`: при `closing=True` минимальная сумма не проверяется,
> остальные проверки — как обычно.
>
> **Сергей Липин:** По-моему, проще убрать минимум совсем, чем плодить флаги в заявлении.
>
> **Марина Жукова:** Я за вариант Дмитрия — минимум нужен, иначе нас завалят заявлениями на 5 рублей.
>
> **Анна Ковалёва:** Делаем как предложил Дмитрий: поле `closing` (по умолчанию `False`), минимальная
> сумма не применяется только при закрытии счёта. Сергей, минимум остаётся, `MIN_REFUND` не трогаем.

---

### [98] `kvartplata/refunds.py:{{L:kvartplata/refunds.py:if amount > available}}` — сумма больше переплаты

**Марина Жукова:** Если жилец просит вернуть больше, чем у него переплата (например, переплата 700 ₽, а в
заявлении написал 900 ₽ — посмотрел не ту квитанцию), сейчас мы отказываем целиком. По регламенту
так нельзя: возвращаем в пределах переплаты, то есть уменьшаем сумму до размера переплаты и одобряем.

> **Игорь Тарасов:** Понял. Тест `test_amount_above_overpayment` тогда тоже поправлю.

---

### [99] `kvartplata/refunds.py:{{L:kvartplata/refunds.py:^METHODS}}` — возврат наличными

**Марина Жукова:** Добавьте возврат наличными (`"cash"`) — пенсионеры просят, у многих нет карт.

> **Игорь Тарасов:** Добавлю в `METHODS`.
>
> **Анна Ковалёва:** Подождите. Касса в офисе УК закрыта с прошлого года, наличных у нас физически нет.
> Выдать деньги некому.
>
> **Марина Жукова:** Точно, я забыла про кассу. Снимаю — пенсионерам предложим перевод на счёт.

---

### [100] `kvartplata/refunds.py:{{L:kvartplata/refunds.py:def render}}` — текст уведомления

**Сергей Липин:** Вопрос: для `bank_account` в уведомлении пишется «на банковский счёт», а номер счёта
не пишем?

> **Игорь Тарасов:** Не пишем специально — уведомление уходит и по e-mail, реквизиты туда не кладём.
>
> **Сергей Липин:** Разумно.

---

### [101] `kvartplata/refunds.py:{{L:kvartplata/refunds.py:def decide:+10}}` — заявление от представителя

**Сергей Липин:** Сейчас заявление от не-собственника отклоняется. А если подаёт представитель по
нотариальной доверенности? Такое бывает часто.

> **Анна Ковалёва:** Это отдельная задача KV-270 (там нужна проверка доверенностей). В этом PR не
> трогаем.

---

### [102] `kvartplata/owners.py:{{L:kvartplata/owners.py:def parse_share}}` — доли в процентах

**Дмитрий Орлов:** В части соглашений доли записаны процентами: «50%», «25 %», «12,5%». Сейчас
`parse_share` их не понимает. Нужно принимать и такую запись — с пробелом перед знаком процента или
без, с запятой или точкой в дробной части; доля = процент / 100 (то есть «12,5%» — это 1/8). Проверки
диапазона те же, что для дробей: «150%» — ошибка. Дроби и целые числа — как сейчас.

> **Игорь Тарасов:** Сделаю.

---

### [103] `kvartplata/owners.py:{{L:kvartplata/owners.py:def parse_shares}}` — разделители

**Марина Жукова:** Вопрос: между собственником и долей принимаются длинное тире, короткое тире и дефис с
пробелами. А дефис без пробелов?

> **Игорь Тарасов:** Без пробелов нельзя — у нас полно двойных фамилий вроде «Иванова-Петрова».
>
> **Марина Жукова:** Логично, вопрос снят. Тогда ещё одно, раз уж смотрю этот код: в соглашении один и тот
> же собственник может встретиться дважды — например, Иванов выкупил долю соседа, и строка выглядит так:
> «Иванов И. И. — 1/4; Петрова А. С. — 1/2; Иванов И. И. — 1/4». Сейчас получатся две строки на Иванова и
> два документа. Доли одного собственника нужно складывать в одну (здесь у Иванова 1/2), порядок —
> по первому упоминанию собственника.
>
> **Игорь Тарасов:** Да, сделаю в `parse_shares`.

---

### [104] `kvartplata/owners.py:{{L:kvartplata/owners.py:def validate:+3}}` — сумма долей меньше единицы

**Сергей Липин:** Сумма долей может быть меньше единицы, если часть квартиры муниципальная (собственник —
город). Сейчас `validate` падает. Давайте ругаться только если сумма больше 1.

> **Игорь Тарасов:** Хорошо, поменяю проверку на `total > 1`.

---

### [105] `kvartplata/owners.py:{{L:kvartplata/owners.py:def split_amount}}` — куда уходят копейки

**Дмитрий Орлов:** Сейчас все копейки округления уходят последнему собственнику: 100 ₽ на три равные
доли — 33,33 + 33,33 + 33,34. Предлагаю делить методом наибольших остатков, как ОДН, — через
`money.allocate_by_weights`, веса — доли собственников в порядке соглашения.

> **Марина Жукова:** А может, проще — весь остаток отдавать владельцу наибольшей доли? Он обычно и
> платит за всех.
>
> **Дмитрий Орлов:** При равных долях «наибольшей» нет, опять придётся выбирать. А у `allocate_by_weights`
> правило уже есть и протестировано.
>
> **Анна Ковалёва:** Делаем как предложил Дмитрий — через `allocate_by_weights`, веса — доли в порядке
> соглашения. Для трёх равных долей получится 33,34 + 33,33 + 33,33. Марина, поправь тест
> `test_split_thirds`.

---

### [106] `kvartplata/owners.py:{{L:kvartplata/owners.py:def render}}` — выравнивание

**Сергей Липин:** nit: в `render` ширина колонки ФИО 32 символа — длинные ФИО с двойной фамилией не
влезают. По желанию.

---

### [107] `kvartplata/owners.py:{{L:kvartplata/owners.py:^"""Раздельные}}` — отдельные лицевые счета

**Марина Жукова:** Вопрос: раздельные документы — это отдельные лицевые счета для каждого собственника?

> **Анна Ковалёва:** Нет, лицевой счёт один, документы разные. Хранение раздельных документов —
> KV-271, не в этом PR.

---

### [108] `kvartplata/reading_window.py:{{L:kvartplata/reading_window.py:^WINDOW_END}}` — продлить окно до 26-го

**Марина Жукова:** Давайте принимать показания до 26-го — жильцы не успевают, особенно в длинные месяцы.

> **Дмитрий Орлов:** Окно 15–25 записано в договоре управления, поменять его можно только решением общего
> собрания. Оставляем 25-е.
>
> **Марина Жукова:** Поняла.

---

### [109] `kvartplata/reading_window.py:{{L:kvartplata/reading_window.py:def period_for:+5}}` — последний день окна

**Марина Жукова:** Баг: показание, переданное 25 марта, уходит в апрель, хотя 25-е — последний день окна
**включительно**. В следующий период должны уходить только показания, переданные после последнего дня
окна.

> **Игорь Тарасов:** Да, там `>=` вместо `>`. Исправлю, и тест `test_last_day` заодно — он зафиксировал
> ошибку.

---

### [110] `kvartplata/reading_window.py:{{L:kvartplata/reading_window.py:def window}}` — конец окна в выходной

**Дмитрий Орлов:** Если последний день окна выпадает на выходной или праздник, окно продлевается до
ближайшего рабочего дня включительно — так же, как срок оплаты в треде про `due_date`. Например,
25 апреля 2026 года — суббота, значит, показания принимаются по понедельник 27 апреля, и переданные
26-го относятся к апрелю. Начало окна не переносится.

> **Игорь Тарасов:** Сделаю через `next_workday` из `calendar_ru`.

---

### [111] `kvartplata/reading_window.py:{{L:kvartplata/reading_window.py:def accept}}` — показание контролёра

**Дмитрий Орлов:** `accept` отклоняет показание, если оно меньше предыдущего. Для показаний жильцов и
импорта это правильно. Но показание контролёра (`source == "inspector"`) принимаем, даже если оно меньше
предыдущего: это значит, что жилец раньше передавал завышенные показания, а прав контролёр. Остальные
проверки (дата, отрицательное значение) действуют и для контролёра.

> **Игорь Тарасов:** Понял, добавлю исключение для `inspector`.

---

### [112] `kvartplata/reading_window.py:{{L:kvartplata/reading_window.py:def reminder_date}}` — напоминание в воскресенье

**Марина Жукова:** Вопрос: для марта 2026 напоминание уходит 22-го, а это воскресенье. Так и задумано?

> **Сергей Липин:** SMS отправляет шлюз по расписанию, ему выходные не важны. Да, задумано.
>
> **Марина Жукова:** Ок.

---

### [113] `kvartplata/reading_window.py:{{L:kvartplata/reading_window.py:def needs_reminder}}` — напоминание тем, кто уже передал

**Марина Жукова:** Сейчас напоминание уходит всем, у кого есть приборы учёта, даже если показания за
период по всем приборам уже переданы. Жильцы ругаются: «я же передал вчера». Давайте не напоминать, если
показания за период есть по всем приборам помещения; если хотя бы по одному нет — напоминать.

> **Анна Ковалёва:** Это задача KV-260 (там ещё push-уведомления из личного кабинета), в этом PR не делаем.
>
> **Марина Жукова:** Хорошо, подожду KV-260.

---

### [114] `kvartplata/reading_window.py:{{L:kvartplata/reading_window.py:def receive}}` — переменная цикла

**Сергей Липин:** nit: в `receive` переменная цикла `reading` перезаписывается внутри цикла. Читается
плохо, по желанию переименовать.

---

### [115] `kvartplata/quality.py:{{L:kvartplata/quality.py:def relevant:+2}}` — снова про неподтверждённые перерывы

**Дмитрий Орлов:** К треду [89]. Игорь, фильтр по `confirmed` не убирай. По Правилам снижение платы
делается только за подтверждённые перерывы — зафиксированные аварийно-диспетчерской службой или актом
о непредоставлении услуги. Заявка жильца без акта — это повод составить акт, а не основание для снижения.
Марина, согласна?

> **Марина Жукова:** Да, я была неправа. Снимаю своё замечание из [89].
>
> **Анна Ковалёва:** Ок, фильтр остаётся как был.

---

### [116] `kvartplata/sms.py:{{L:kvartplata/sms.py:^"""Тексты}}` — текст напоминания о показаниях

**Сергей Липин:** Вопрос: текст напоминания о передаче показаний тоже будет в `sms.py`?

> **Игорь Тарасов:** Нет, шаблон напоминания хранится в самом SMS-шлюзе, мы передаём только список
> лицевых счетов.
>
> **Анна Ковалёва:** Кстати, раз уж про напоминания. По треду [113]: KV-260 закрыли как дубль — push будет
> делать команда личного кабинета у себя. Поэтому то, что просила Марина (не напоминать, если показания
> за период переданы по всем приборам помещения), делаем в этом PR, в `needs_reminder`.
>
> **Марина Жукова:** Отлично, спасибо!

---

### [117] `kvartplata/owners.py:{{L:kvartplata/owners.py:def validate}}` — муниципальная доля

**Дмитрий Орлов:** К треду [104]. Муниципальную долю город оплачивает по отдельному договору с
администрацией, в соглашение собственников о порядке оплаты она не входит. Поэтому в соглашении сумма долей
всегда ровно 1, и `validate` должна по-прежнему падать и при сумме меньше единицы — иначе незамеченная
опечатка в доле оставит часть квартиры без плательщика. Сергей, снимаешь?

> **Сергей Липин:** Да, про договор с администрацией не знал. Снимаю.
>
> **Игорь Тарасов:** Ок, проверку не трогаю.

---

### [118] `kvartplata/money.py:{{L:kvartplata/money.py:def split_evenly}}` — копейки у долевых собственников

**Марина Жукова:** Вопрос: для раздела между собственниками используем `split_evenly` с остатком в первые
части, как договорились в треде [5]?

> **Дмитрий Орлов:** Нет, там будет `allocate_by_weights` (тред [105]). `split_evenly` меняется только так,
> как решили в треде [5], больше ничего.

---

### [119] `kvartplata/calendar_ru.py:{{L:kvartplata/calendar_ru.py:def add_workdays}}` — отсчёт рабочих дней

**Сергей Липин:** Вопрос: `add_workdays` начинает считать со следующего дня. Для срока рассмотрения
заявления это правильно — день подачи не считается?

> **Дмитрий Орлов:** Правильно, день подачи в срок не входит.

---

### [120] `kvartplata/refunds.py:{{L:kvartplata/refunds.py:def overpayment:+3}}` — пени в переплате

**Марина Жукова:** Вопрос: переплата считается без учёта пеней?

> **Игорь Тарасов:** Пени в `Ledger` не попадают, у них свой учёт. Да, без пеней.
>
> **Дмитрий Орлов:** И это верно: пени с переплаты не удерживаем.

---

### [121] `kvartplata/quality.py:{{L:kvartplata/quality.py:^REDUCTION_PER_HOUR}}` — отопление

**Сергей Липин:** 0,15 % за час — и для отопления? Там же снижение считается по температуре в квартире.

> **Дмитрий Орлов:** Снижение за температуру воздуха ниже нормы — отдельная история (KV-272). За перерывы
> в отоплении — те же 0,15 % за час сверх нормы. Всё верно, не трогаем.

---

### [122] `tests/test_owners_window.py:{{L:tests/test_owners_window.py:class ShareTest}}` — тесты второго круга

**Марина Жукова:** Напоминаю, как и в первом круге: часть тестов в `tests/test_quality_refunds.py`,
`tests/test_owners_window.py` и `tests/test_closing.py` зафиксировала поведение, которое мы меняем по тредам второго круга. Их
нужно поправить под новое поведение, а не удалять.

> **Игорь Тарасов:** Да, конечно.

---

### [123] `README.md:{{L:README.md:^\| .quality\.py}}` — новые модули в README

**Сергей Липин:** nit: в таблицу модулей README стоит добавить краткое описание порядка снижения платы.
По желанию, можно после мержа.

---

### [124] `kvartplata/closing.py:{{L:kvartplata/closing.py:def month_days}}` — на сколько дней делится месяц

**Дмитрий Орлов:** В `month_days` всегда 30 — «банковский месяц». У нас так не считают: начисление за месяц
делится на фактическое число дней в этом месяце — 28 (29) в феврале, 30 или 31 в остальных. В `Period` для
этого уже есть свойство `days`.

> **Игорь Тарасов:** Хм, 30 я взял из старого модуля бухгалтерии. Исправлю на `period.days`.

---

### [125] `kvartplata/closing.py:{{L:kvartplata/closing.py:def owned_days:+4}}` — день перехода права

**Марина Жукова:** Вопрос: при закрытии 16 марта прежний собственник платит за 15 дней. А кто платит за
сам день 16-го?

> **Игорь Тарасов:** По-моему, новый — с этого дня он собственник.
>
> **Дмитрий Орлов:** Нет. По нашей методике день перехода права оплачивает прежний собственник, новый
> платит со следующего дня. То есть при закрытии 16 марта — 16 дней владения, при закрытии 1-го числа —
> 1 день. Игорь, поправь `owned_days`.
>
> **Игорь Тарасов:** Понял, поправлю.

---

### [126] `kvartplata/closing.py:{{L:kvartplata/closing.py:^PRORATED}}` — отопление в пересчёте

**Марина Жукова:** Отопление, по-моему, не надо пересчитывать по дням: оно начисляется по нормативу за
отопительный сезон, и дни владения тут ни при чём. Давайте уберём `heating` из `PRORATED`.

> **Игорь Тарасов:** Могу убрать.
>
> **Дмитрий Орлов:** Не нужно. Отопление начисляется помесячно по площади, и за месяц закрытия прежний
> собственник платит только за свои дни — ровно как за содержание. Норматив тут ни при чём.
>
> **Марина Жукова:** Да, согласна, снимаю. Отопление остаётся в списке.

---

### [127] `kvartplata/closing.py:{{L:kvartplata/closing.py:def prorate}}` — льготная скидка при пересчёте

**Марина Жукова:** В `prorate` сумма начисления пересчитывается по дням, а скидка по льготе остаётся за
весь месяц. У льготника с 50 % скидкой, закрывающего счёт 5-го числа, скидка получается больше
начисления, и итог по строке уходит в минус. Скидку нужно пересчитывать той же долей, что и сумму.

> **Игорь Тарасов:** Точно. Сделаю.
>
> **Анна Ковалёва:** Да, обязательно.

---

### [128] `kvartplata/closing.py:{{L:kvartplata/closing.py:def final_statement}}` — сальдо на дату закрытия

**Сергей Липин:** Вопрос: сальдо берётся по периодам до месяца закрытия, а платежи — по дату закрытия.
Платёж, пришедший после закрытия (например, от банка с задержкой), не попадёт?

> **Дмитрий Орлов:** Не попадёт, и это правильно: такие платежи разбирает бухгалтерия вручную — их
> возвращают плательщику или засчитывают новому собственнику по заявлению. Всё верно.

---

### [129] `kvartplata/closing.py:{{L:kvartplata/closing.py:def render}}` — формат даты в итоговом документе

**Сергей Липин:** nit: в `render` строка с сальдо и строка «Итого» — разного вида, можно выровнять.
По желанию.

> **Марина Жукова:** Раз уж тут: дата закрытия печатается в ISO (`2026-03-16`). Итоговый документ идёт
> на бумаге новому и прежнему собственнику, там нужна обычная дата — `16.03.2026`, как во всех наших
> документах. Это не по желанию, это исправить.
>
> **Игорь Тарасов:** Хорошо, дату поправлю, выравнивание — если успею.

---

### [130] `kvartplata/closing.py:{{L:kvartplata/closing.py:^"""Закрытие}}` — возврат переплаты при закрытии

**Сергей Липин:** Может, `final_statement` сразу создаёт заявление о возврате (`RefundRequest` с
`closing=True`)?

> **Анна Ковалёва:** Нет, заявление на возврат пишет сам собственник, автоматически не создаём. Модули
> остаются независимыми.

---

### [131] `kvartplata/receipt.py:{{L:kvartplata/receipt.py:def render}}` — снижение за перерывы в квитанции

**Марина Жукова:** Вопрос: снижение платы за перерывы (тред [93]) попадёт в квитанцию в строке
«перерасчёт»? Отдельную строку делать не надо?

> **Игорь Тарасов:** Попадёт в «перерасчёт», а основание — в примечание строки. Отдельную строку не
> делаем: формат квитанции утверждён.
>
> **Анна Ковалёва:** Подтверждаю, квитанцию не трогаем.

---

### [132] `kvartplata/penalties.py:{{L:kvartplata/penalties.py:def penalty_lines}}` — пени на снижение

**Сергей Липин:** Если плата снижена за перерывы, пени считаются от уже сниженной суммы?

> **Дмитрий Орлов:** Пени считаются от долга по лицевому счёту, а долг — от итоговой суммы документа, то
> есть уже после снижения. Ничего менять не надо.

---

### [133] `kvartplata/installments.py:{{L:kvartplata/installments.py:def make_agreement}}` — рассрочка и закрытие счёта

**Марина Жукова:** Если у собственника действует рассрочка и он продаёт квартиру — что с графиком?

> **Анна Ковалёва:** Досрочное погашение при закрытии — это KV-275, пока юристы не согласовали. В этом
> PR ничего не делаем.

---

### [134] `kvartplata/gis_export.py:{{L:kvartplata/gis_export.py:def export}}` — закрытые счета в выгрузке ГИС

**Сергей Липин:** Закрытые при смене собственника счета в выгрузку ГИС ЖКХ за месяц закрытия попадают?

> **Игорь Тарасов:** Попадают, с неполным начислением. Формат выгрузки не меняется (тред [65]).
>
> **Сергей Липин:** Ок.

---

### [135] `kvartplata/privileges.py:{{L:kvartplata/privileges.py:def apply_privileges}}` — льготы и снижение

**Дмитрий Орлов:** Для справки к тредам [85] и [127]: порядок такой — сначала льготы
(`apply_privileges`), потом снижение за перерывы и пересчёт при закрытии. Снижение считается от
`amount`, не от итога строки, так что порядок на сумму снижения не влияет. Менять ничего не нужно,
пишу, чтобы не было вопросов.

---

### [136] `kvartplata/refunds.py:{{L:kvartplata/refunds.py:^DECISION_DAYS}}` — срок рассмотрения при закрытии

**Марина Жукова:** При закрытии счёта (`closing=True`) срок рассмотрения тоже 10 рабочих дней?

> **Дмитрий Орлов:** Да, срок один для всех заявлений (тред [96]). `DECISION_DAYS` не трогаем.

---

### [137] `kvartplata/quality.py:{{L:kvartplata/quality.py:def parse_dispatch_log}}` — даты в журнале диспетчерской

**Дмитрий Орлов:** Посмотрел реальную выгрузку журнала аварийно-диспетчерской службы за февраль: время
там записано как `10.03.2026 08:15` (день.месяц.год, пробел, часы:минуты), а `parse_dispatch_log`
понимает только ISO (`2026-03-10T08:15`) и на первой же строке падает. Нужно принимать оба формата.

> **Игорь Тарасов:** Я делал по старому образцу выгрузки, где был ISO. Добавлю разбор `ДД.ММ.ГГГГ ЧЧ:ММ`,
> ISO тоже оставлю — старые выгрузки ещё лежат в архиве.
>
> **Дмитрий Орлов:** Да, так.

---

### [138] `kvartplata/quality.py:{{L:kvartplata/quality.py:^LOG_SERVICES}}` — коды услуг в журнале

**Марина Жукова:** Вопрос: электроснабжение в журнале обозначается «ЭЛ» или «ЭЭ»? В квитанциях у нас «ЭЭ».

> **Игорь Тарасов:** В журнале диспетчерской — «ЭЛ», сверил с выгрузкой. Квитанции тут ни при чём.
>
> **Марина Жукова:** Ок.

---

### [139] `kvartplata/quality.py:{{L:kvartplata/quality.py:def summary}}` — сортировка сводки

**Сергей Липин:** nit: `summary` сортирует услуги по коду, а в отчётах мы обычно идём в порядке
`models.SERVICES`. По желанию.

---

### [140] `kvartplata/refunds.py:{{L:kvartplata/refunds.py:def registry_line}}` — сумма в реестре для банка

**Сергей Липин:** Банк прислал спецификацию реестра возвратов (версия 3): в строках `R` сумма передаётся
целым числом копеек, без разделителя — 700,00 ₽ пишется как `70000`, 1 234,50 ₽ — как `123450`. Сейчас мы
пишем `700.00`, и банк отбивает весь реестр.

> **Анна Ковалёва:** Да, исправляем. Заголовок `#REFUND` по той же спецификации — в рублях с точкой, его
> не трогаем, меняются только строки `R`.
>
> **Игорь Тарасов:** Понял. Для копеек есть `money.kopecks`.

---

### [141] `kvartplata/refunds.py:{{L:kvartplata/refunds.py:def registry\(:+3}}` — проверка номера счёта

**Марина Жукова:** Вопрос: номер счёта получателя проверяется только на 20 цифр. Контрольный ключ по БИК
не считаем?

> **Сергей Липин:** Без БИК ключ не посчитать, а БИК в заявлении пока не собираем. Банк сам проверит
> и вернёт ошибочные строки.
>
> **Анна Ковалёва:** Оставляем как есть.

---

### [142] `kvartplata/reading_window.py:{{L:kvartplata/reading_window.py:def schedule}}` — график на год

**Сергей Липин:** Вопрос: в графике на год конец окна тоже будет переноситься с выходных?

> **Игорь Тарасов:** Да, `schedule` берёт границы из `window`, так что перенос из треда [110] попадёт и
> сюда автоматически. Отдельно ничего делать не нужно.

---

### [143] `kvartplata/owners.py:{{L:kvartplata/owners.py:def largest_owner}}` — кому уходят уведомления

**Марина Жукова:** Вопрос: уведомления по счёту уходят владельцу наибольшей доли, а при равных долях —
первому по соглашению?

> **Дмитрий Орлов:** Да, так в регламенте. Функция так и работает.

---

### [144] `kvartplata/money.py:{{L:kvartplata/money.py:def format_rub}}` — `format_rub` в реестре

**Сергей Липин:** Раз уж говорим про реестр возвратов (тред [140]) — может, и там использовать `format_rub`,
раз мы его исправили в треде [7]?

> **Анна Ковалёва:** Нет, у банка свой формат, человекочитаемое форматирование туда не годится.
> `format_rub` меняется только по треду [7].

---

### [145] `kvartplata/periods.py:{{L:kvartplata/periods.py:def overlap_days}}` — `overlap_days` для перерывов

**Сергей Липин:** Для обрезки перерывов по периоду (тред [87]) можно было бы взять `overlap_days`?

> **Игорь Тарасов:** Нет, `overlap_days` считает целые дни с включёнными границами, а нам нужны часы.
> Для перерывов будет отдельная обрезка по `period_bounds`. `overlap_days` не трогаю — им пользуется
> перерасчёт за отсутствие.
>
> **Сергей Липин:** Логично.

---

### [146] `kvartplata/ledger.py:{{L:kvartplata/ledger.py:def paid_until}}` — платёж в день заявления

**Марина Жукова:** Вопрос для `refunds`: платёж, пришедший в сам день подачи заявления, учитывается в
переплате?

> **Игорь Тарасов:** Да, `paid_until` включает этот день.
>
> **Дмитрий Орлов:** И это правильно.

---

### [147] `kvartplata/validators.py:{{L:kvartplata/validators.py:def is_valid_account}}` — проверка счёта в заявлении

**Сергей Липин:** Может, в `refunds.decide` проверять контрольную цифру номера лицевого счёта?

> **Анна Ковалёва:** Не нужно: заявление связывается с лицевым счётом из базы, номер там уже проверен при
> заведении. Лишняя проверка не нужна.

---

### [148] `kvartplata/sms.py:{{L:kvartplata/sms.py:def debt_sms}}` — SMS о возврате

**Марина Жукова:** Давайте отправлять SMS, когда возврат одобрен?

> **Анна Ковалёва:** Хорошая идея, но это KV-278 (нужен новый шаблон в шлюзе). Не в этом PR.

---

### [149] `kvartplata/audit.py:{{L:kvartplata/audit.py:^class AuditLog}}` — события возвратов в журнале

**Сергей Липин:** Решения по возвратам стоит писать в журнал событий `AuditLog`.

> **Анна Ковалёва:** Согласна, но журнал сейчас пишет только события расчёта, для возвратов нужен новый тип
> событий и согласование с безопасностью — KV-276. В этом PR не делаем.

---

### [150] `kvartplata/storage.py:{{L:kvartplata/storage.py:^class Store}}` — где хранить перерывы

**Сергей Липин:** Вопрос: перерывы (`Outage`) и заявления на возврат нигде не хранятся — их будут
передавать извне?

> **Игорь Тарасов:** Да, пока их передаёт диспетчерская выгрузкой и личный кабинет. Хранение — KV-277.
>
> **Анна Ковалёва:** Всё так, `storage.py` в этом PR не трогаем.

---

### [151] `kvartplata/recalc.py:{{L:kvartplata/recalc.py:def recalculate}}` — перерасчёт за отсутствие и снижение за перерывы

**Марина Жукова:** Если жилец отсутствовал весь март и получил перерасчёт за отсутствие, а в марте ещё и
была авария — снижение за перерывы ему тоже положено? Получается двойная выгода.

> **Дмитрий Орлов:** Положено: снижение считается от начисления по тарифу (`amount`), а перерасчёт за
> отсутствие — отдельная строка в `recalculation`. Методика такая, двойной выгоды тут нет — это разные
> основания. В `recalc.py` ничего не меняем.
>
> **Марина Жукова:** Поняла.

---

### [152] `kvartplata/house.py:{{L:kvartplata/house.py:def distribute}}` — ОДН при закрытии счёта

**Сергей Липин:** Вопрос: ОДН за месяц закрытия счёта тоже делится по дням владения?

> **Дмитрий Орлов:** ОДН распределяется по площади помещений на весь дом и начисляется в составе строк
> услуг, а строки по площади за месяц закрытия пересчитываются в `prorate`. Отдельной логики в `house.py`
> не нужно.
>
> **Сергей Липин:** Понял, спасибо.

---

### [153] `kvartplata/receipt_html.py:{{L:kvartplata/receipt_html.py:def render:+6}}` — HTML для долевых собственников

**Сергей Липин:** Для раздельных документов (тред [105]) HTML-квитанцию нужно доработать — печатать
долю собственника и его часть суммы?

> **Анна Ковалёва:** Это делает команда личного кабинета у себя (KV-279), у них свой шаблон. Наш
> `receipt_html.py` в этом PR не трогаем.

---

### [154] `kvartplata/statement.py:{{L:kvartplata/statement.py:def build:+4}}` — возвраты в выписке

**Марина Жукова:** Возврат переплаты в выписке из лицевого счёта будет виден?

> **Игорь Тарасов:** Возврат проводится бухгалтерией как отрицательный платёж, в выписку он попадёт
> автоматически, как и сейчас. Отдельно ничего делать не нужно.
>
> **Дмитрий Орлов:** Подтверждаю.

---

### [155] `kvartplata/reports.py:{{L:kvartplata/reports.py:def debtors}}` — должники и закрытые счета

**Сергей Липин:** Закрытый при продаже счёт с долгом попадёт в отчёт о должниках?

> **Дмитрий Орлов:** Попадёт, и так и должно быть: долг прежнего собственника никуда не исчезает, с ним
> работают юристы. Правило трёх месяцев из треда про `debtors` действует и для закрытых счетов.
>
> **Сергей Липин:** Ок.

---

### [156] `kvartplata/cli.py:{{L:kvartplata/cli.py:def build_parser}}` — команды для новых модулей

**Сергей Липин:** Добавим в CLI команды `refund` и `close-account`?

> **Анна Ковалёва:** Нет, новые модули вызывает личный кабинет через Python API. CLI — для бухгалтерии,
> им эти команды не нужны. Не добавляем.

---

### [157] `kvartplata/quality.py:{{L:kvartplata/quality.py:class Outage:+12}}` — перерыв без окончания

**Марина Жукова:** Вопрос: а если авария ещё не устранена и времени окончания нет?

> **Игорь Тарасов:** Такие перерывы диспетчерская в выгрузку не включает, пока их не закроет. В модуль
> попадают только закрытые перерывы, поэтому `end` обязателен.
>
> **Марина Жукова:** Понятно, спасибо.

---

### [158] `kvartplata/owners.py:{{L:kvartplata/owners.py:^SEPARATORS}}` — дефис как разделитель

**Сергей Липин:** nit: список разделителей стоит вынести в `config.py`, раз он может меняться. По желанию,
не блокирует.

---

> **Анна Ковалёва:** Второй круг тоже закрываем. Игорь, доделывай по тредам обоих кругов; снятые и
> отложенные замечания не делаем. Перед мержем — зелёные тесты.
'''

_HIDDEN_CHANGES = r'''"""Hidden tests: one group per requested change (test name prefix = group id)."""

from datetime import date
from decimal import Decimal as D

from kvartplata.config import Settings
from kvartplata.meters import ReadingBook, consumption, resolve_meter
from kvartplata.models import Absence, Account, Meter, Payment, Reading, Resident
from kvartplata.periods import Period
from kvartplata.tariffs import default_tariffs

MARCH = Period(2026, 3)
FEB = Period(2026, 2)


def _res(name="Петров Пётр Петрович", born=date(1980, 1, 15), **kw):
    return Resident(name, born, **kw)


def _flat(**kw):
    base = dict(number="12345678905", address="ул. Садовая, д. 5, кв. 12", total_area=D("54.3"),
                heated_area=D("50.1"),
                residents=[_res(owner=True), _res("Петрова Анна Сергеевна", date(1982, 7, 2))],
                meters=[Meter("CW1", "cold_water"), Meter("HW1", "hot_water"), Meter("EL1", "electricity")])
    base.update(kw)
    return Account(**base)


def _book(*items):
    return ReadingBook([Reading(m, p, D(str(v))) for m, p, v in items])


def _std_book():
    return _book(("CW1", FEB, "100"), ("CW1", MARCH, "108.5"), ("HW1", FEB, "50"), ("HW1", MARCH, "54"),
                 ("EL1", FEB, "1000"), ("EL1", MARCH, "1210"))


def _ctx(account=None, period=MARCH, book=None):
    from kvartplata.services.base import Context
    return Context(account or _flat(), period, book or _std_book(), default_tariffs(), Settings())


def test_c01_half_up():
    from kvartplata.money import round_money
    assert round_money(D("0.125")) == D("0.13")
    assert round_money(D("2.665")) == D("2.67")
    assert round_money(D("-0.125")) == D("-0.13")


def test_c02_thousands():
    from kvartplata.money import format_rub
    assert format_rub(D("1234567.8")) == "1 234 567,80 руб."
    assert format_rub(D("-1234.5"), suffix="") == "-1 234,50"
    assert format_rub(D("999.99")) == "999,99 руб."
    assert format_rub(D("100000")) == "100 000,00 руб."


def test_c03_remainder_first():
    from kvartplata.money import split_evenly
    assert split_evenly(D("100"), 3) == [D("33.34"), D("33.33"), D("33.33")]
    assert split_evenly(D("0.05"), 3) == [D("0.02"), D("0.02"), D("0.01")]


def test_c04_ru_period():
    assert Period.parse("03.2026") == Period(2026, 3)
    assert Period.parse("11.2025") == Period(2025, 11)
    assert Period.parse("2026-03") == Period(2026, 3)


def test_c05_prev_january():
    assert Period(2026, 1).prev() == Period(2025, 12)


def test_c06_holidays():
    from kvartplata.calendar_ru import next_workday
    assert next_workday(date(2026, 1, 3)) == date(2026, 1, 12)
    assert next_workday(date(2026, 3, 9)) == date(2026, 3, 10)


def test_c07_due_date_shift():
    from kvartplata.billing import due_date
    assert due_date(Period(2026, 9)) == date(2026, 10, 12)
    assert due_date(Period(2025, 12)) == date(2026, 1, 12)
    assert due_date(Period(2026, 2)) == date(2026, 3, 10)


def test_c08_tariff_from_start_date():
    t = default_tariffs()
    assert t.rate_for("cold_water", date(2026, 7, 1)) == D("56.02")
    assert t.rate_for("cold_water", date(2026, 1, 1)) == D("52.31")
    assert t.rate_for_period("heat", Period(2026, 7)) == D("3259.90")


def test_c10_meter_impossible():
    from kvartplata.normatives import coefficient
    from kvartplata.services import cold_water
    acc = _flat(meters=[], meter_impossible={"cold_water"})
    assert coefficient(acc, "cold_water", Settings()) == 1
    (c,) = cold_water.calculate(_ctx(acc))
    assert c.volume == D("4.745") * 2
    assert coefficient(_flat(meters=[]), "cold_water", Settings()) == D("1.5")


def test_c11_rollover():
    assert consumption(D("99990"), D("5"), 99999) == D("15")
    assert consumption(D("9990"), D("10"), 9999) == D("20")


def test_c12_average_window():
    book = _book(("CW1", Period(2025, 6), "0"), ("CW1", Period(2025, 8), "100"), ("CW1", Period(2025, 10), "110"),
                 ("CW1", FEB, "130"))
    res = resolve_meter(Meter("CW1", "cold_water"), _flat(), MARCH, book, Settings())
    assert res.method == "average"
    assert res.volume == D("5")


def test_c13_average_limit():
    book = _book(("CW1", Period(2025, 9), "100"), ("CW1", Period(2025, 10), "105"), ("CW1", Period(2025, 11), "110"))
    meter = Meter("CW1", "cold_water")
    res = resolve_meter(meter, _flat(), MARCH, book, Settings())
    assert res.method == "norm"
    assert res.volume == D("9.490")
    res2 = resolve_meter(meter, _flat(), FEB, book, Settings())
    assert res2.method == "average"


def test_c14_night_rate():
    from kvartplata.services import electricity
    acc = _flat(meters=[Meter("ED", "electricity", "day"), Meter("EN", "electricity", "night")])
    book = _book(("ED", FEB, "1000"), ("ED", MARCH, "1150"), ("EN", FEB, "500"), ("EN", MARCH, "560"))
    day, night = electricity.calculate(_ctx(acc, book=book))
    assert day.rate == D("7.95") and night.rate == D("3.49")
    assert night.amount == D("209.40")


def test_c15_heated_area():
    from kvartplata.services import heating
    (c,) = heating.calculate(_ctx())
    assert c.volume == D("0.9369")
    assert c.amount == D("2852.09")


def test_c16_hot_water_rounding():
    from kvartplata.services import hot_water
    (c,) = hot_water.calculate(_ctx())
    assert c.amount == D("923.78")


def test_c17_sewage_includes_hot():
    from kvartplata.services import sewage
    (c,) = sewage.calculate(_ctx())
    assert c.volume == D("12.5")
    assert c.amount == D("540.88")


def test_c18_exact_age():
    from kvartplata.money import round_money
    from kvartplata.services import capital_repair
    acc70 = _flat(residents=[_res(born=date(1956, 3, 1), owner=True, lives_alone=True)])
    (c,) = capital_repair.calculate(_ctx(acc70))
    assert c.discount == round_money(c.amount * 50 / 100)
    acc80 = _flat(residents=[_res(born=date(1946, 3, 1), owner=True, lives_alone=True)])
    (c,) = capital_repair.calculate(_ctx(acc80))
    assert c.discount == c.amount


def test_c19_waste_by_owners():
    from kvartplata.services import waste
    acc = _flat(residents=[_res(registered=False, owner=True)], owners_count=3)
    (c,) = waste.calculate(_ctx(acc))
    assert c.volume == 3
    assert c.amount == D("445.86")


def test_c20_social_norm_cap():
    from kvartplata.privileges import discount_for
    from kvartplata.services import maintenance
    acc = _flat(residents=[_res(owner=True, privileges=["veteran"]), _res("Петрова Анна Сергеевна")])
    (c,) = maintenance.calculate(_ctx(acc))
    assert discount_for(c, acc, Settings()) == D("291.60")


def test_c21_max_privilege():
    from kvartplata.privileges import discount_for, resident_percent
    from kvartplata.services import maintenance
    assert resident_percent(["veteran", "disabled"], "maintenance") == 50
    acc = _flat(total_area=D("30"), heated_area=D("30"),
                residents=[_res(owner=True, privileges=["veteran", "disabled"])])
    (c,) = maintenance.calculate(_ctx(acc))
    assert discount_for(c, acc, Settings()) == D("486.00")


def test_c22_full_days():
    from kvartplata.recalc import absent_days_in_period, eligible
    short = Absence("Петров Пётр Петрович", date(2026, 3, 2), date(2026, 3, 7))
    assert not eligible(short, Settings())
    ok = Absence("Петров Пётр Петрович", date(2026, 3, 1), date(2026, 3, 8))
    assert eligible(ok, Settings())
    assert absent_days_in_period([ok], MARCH, Settings()) == 6


def test_c23_no_recalc_heating_maintenance():
    from kvartplata.billing import compute_charges
    a = Absence("Петров Пётр Петрович", date(2026, 3, 1), date(2026, 3, 20))
    charges = compute_charges(_flat(meters=[]), MARCH, _std_book(), default_tariffs(), absences=[a])
    by = {c.service: c for c in charges}
    assert by["heating"].recalculation == 0
    assert by["maintenance"].recalculation == 0
    assert by["cold_water"].recalculation < 0


def test_c24_high_rate_from_day_91():
    from kvartplata.penalties import daily_fraction, penalty
    s = Settings()
    assert daily_fraction(90, s) == D(1) / 300
    assert daily_fraction(91, s) == D(1) / 130
    value = penalty(D("3000"), date(2026, 1, 10), date(2026, 4, 20), s)
    assert abs(value - D("128.77")) <= D("0.05")


def test_c25_round_once():
    from kvartplata.penalties import penalty
    assert penalty(D("1000"), date(2026, 3, 10), date(2026, 4, 19), Settings()) == D("5.17")


def test_c26_oldest_first():
    from kvartplata.payments import Debt, allocate
    res = allocate(D("500"), [Debt("2026-02", D("400")), Debt("2026-01", D("400"))])
    assert res.paid_for("2026-01") == D("400")
    assert res.paid_for("2026-02") == D("100")


def test_c27_penalties_last():
    from kvartplata.payments import Debt, allocate
    res = allocate(D("820"), [Debt("2026-01", D("400"), D("50")), Debt("2026-02", D("400"), D("50"))])
    assert res.paid_for("2026-01", "principal") == D("400")
    assert res.paid_for("2026-02", "principal") == D("400")
    assert res.paid_for("2026-01", "penalty") + res.paid_for("2026-02", "penalty") == D("20")


def test_c28_negative_balance():
    from kvartplata.ledger import Ledger
    led = Ledger("12345678905")
    led.add_charge(Period(2026, 1), D("1000"))
    led.add_payment(Payment("12345678905", date(2026, 1, 20), D("1500")))
    assert led.balance(Period(2026, 1), date(2026, 2, 1)) == D("-500.00")


def test_c29_qr_kopecks():
    from kvartplata import receipt
    from kvartplata.billing import compute_bill
    bill = compute_bill(_flat(), MARCH, _std_book(), default_tariffs())
    payload = receipt.qr_payload(bill)
    field = [f for f in payload.split("|") if f.startswith("Sum=")][0]
    assert field == f"Sum={int(bill.to_pay * 100)}"


def test_c30_payer_initials():
    from kvartplata import receipt
    from kvartplata.billing import compute_bill
    acc = _flat(residents=[_res("Петров Пётр Петрович", owner=True)])
    bill = compute_bill(acc, MARCH, _std_book(), default_tariffs())
    assert receipt.payer_name(bill) == "Петров П. П."
    assert "LastName=Петров П. П.|" in receipt.qr_payload(bill)


def test_c31_export_format():
    from kvartplata.billing import compute_bill
    from kvartplata.export import export_bills
    text = export_bills([compute_bill(_flat(), MARCH, _std_book(), default_tariffs())])
    assert text.splitlines()[0] == "account;period;service;volume;rate;amount;discount;recalculation;total"
    assert "12345678905;2026-03;cold_water;8,5;52,31;444,64;0,00;0,00;444,64" in text


def test_c32_import_comma():
    from kvartplata.importer import import_readings
    res = import_readings("meter_id;period;value;taken_on\nCW1;2026-03;123,5;2026-03-20\n")
    assert not res.errors
    assert res.readings[0].value == D("123.5")


def test_c33_duplicates_latest():
    from kvartplata.importer import import_readings
    res = import_readings("meter_id;period;value;taken_on\nCW1;2026-03;5;2026-03-20\nCW1;2026-03;7;2026-03-25\n")
    assert [r.value for r in res.readings] == [D("7")]
    res = import_readings("meter_id;period;value;taken_on\nCW1;2026-03;7;2026-03-25\nCW1;2026-03;5;2026-03-20\n")
    assert [r.value for r in res.readings] == [D("7")]
    assert res.duplicates == 1


def test_c34_checksum_left_to_right():
    from kvartplata.validators import account_control_digit, is_valid_account
    assert account_control_digit("5000012345") == 2
    assert is_valid_account("40000000013")
    assert not is_valid_account("40000000017")


def test_c35_debtors_three_months():
    from kvartplata.ledger import Ledger
    from kvartplata.reports import debtors
    led = Ledger("12345678905")
    for p in (Period(2026, 1), Period(2026, 2), Period(2026, 3)):
        led.add_charge(p, D("1000"))
    rows = debtors([led], Period(2026, 3), date(2026, 4, 1))
    assert [(r.account, r.months) for r in rows] == [("12345678905", 3)]

# --- второй круг -------------------------------------------------------------

def _refund_ledger(paid, charged="300"):
    from kvartplata.ledger import Ledger
    led = Ledger("12345678905")
    led.add_charge(FEB, D(charged))
    led.add_payment(Payment("12345678905", date(2026, 3, 5), D(paid)))
    return led


def test_c36_partial_hour_counts():
    from datetime import datetime
    from kvartplata.quality import Outage, outage_hours
    assert outage_hours(Outage("cold_water", datetime(2026, 3, 10, 10, 0), datetime(2026, 3, 10, 15, 20)), MARCH) == 6
    assert outage_hours(Outage("hot_water", datetime(2026, 3, 10, 10, 0), datetime(2026, 3, 10, 10, 1)), MARCH) == 1
    assert outage_hours(Outage("gas", datetime(2026, 3, 10, 10, 0), datetime(2026, 3, 10, 15, 0)), MARCH) == 5


def test_c37_single_limit():
    from datetime import datetime
    from kvartplata.quality import Outage, excess_hours
    one = [Outage("cold_water", datetime(2026, 3, 3, 9, 0), datetime(2026, 3, 3, 15, 0))]
    assert excess_hours(one, "cold_water", MARCH) == 2
    heat = [Outage("heating", datetime(2026, 3, 2, 0, 0), datetime(2026, 3, 2, 20, 0)),
            Outage("heating", datetime(2026, 3, 9, 0, 0), datetime(2026, 3, 9, 7, 0))]
    assert excess_hours(heat, "heating", MARCH) == 4
    many = [Outage("cold_water", datetime(2026, 3, d, 9, 0), datetime(2026, 3, d, 12, 0)) for d in (2, 3, 4, 5)]
    assert excess_hours(many, "cold_water", MARCH) == 4


def test_c38_clip_to_period():
    from datetime import datetime
    from kvartplata.quality import Outage, outage_hours
    o = Outage("cold_water", datetime(2026, 2, 28, 20, 0), datetime(2026, 3, 1, 6, 0))
    assert outage_hours(o, MARCH) == 6
    assert outage_hours(o, FEB) == 4
    assert outage_hours(Outage("gas", datetime(2026, 4, 1, 0, 0), datetime(2026, 4, 1, 5, 0)), MARCH) == 0


def test_c39_reduction_capped():
    from datetime import datetime
    from kvartplata.models import Charge
    from kvartplata.quality import Outage, apply_reductions, reduction
    charge = Charge("cold_water", MARCH, D("5"), D("20"), D("100.00"), "meter")
    whole = [Outage("cold_water", datetime(2026, 3, 1), datetime(2026, 4, 1))]
    assert reduction(charge, whole, MARCH) == D("100.00")
    apply_reductions([charge], whole, MARCH)
    assert charge.total == D("0.00")


def test_c40_decision_in_workdays():
    from kvartplata.refunds import RefundRequest, decide, decision_deadline
    assert decision_deadline(date(2026, 4, 6)) == date(2026, 4, 20)
    assert decision_deadline(date(2026, 7, 1)) == date(2026, 7, 15)
    d = decide(RefundRequest("12345678905", date(2026, 4, 6)), _refund_ledger("1000"))
    assert d.decide_by == date(2026, 4, 20)


def test_c41_refund_capped_to_overpayment():
    from kvartplata.refunds import RefundRequest, decide
    d = decide(RefundRequest("12345678905", date(2026, 4, 6), D("900")), _refund_ledger("1000"))
    assert d.approved and d.amount == D("700.00")


def test_c42_closing_account_any_amount():
    from kvartplata.refunds import RefundRequest, decide
    d = decide(RefundRequest("12345678905", date(2026, 4, 6), closing=True), _refund_ledger("350"))
    assert d.approved and d.amount == D("50.00")
    d = decide(RefundRequest("12345678905", date(2026, 4, 6), method="cash", closing=True), _refund_ledger("350"))
    assert not d.approved


def test_c43_percent_shares():
    from fractions import Fraction

    import pytest
    from kvartplata.owners import parse_share, parse_shares
    assert parse_share("50%") == Fraction(1, 2)
    assert parse_share("25 %") == Fraction(1, 4)
    assert parse_share("12,5%") == Fraction(1, 8)
    assert parse_share("12.5%") == Fraction(1, 8)
    with pytest.raises(ValueError):
        parse_share("150%")
    assert [s.part for s in parse_shares("Иванов И. И. — 75%; Иванова А. П. — 25 %")] == [Fraction(3, 4),
                                                                                         Fraction(1, 4)]


def test_c44_owner_split_largest_remainder():
    from fractions import Fraction
    from kvartplata.owners import Share, split_amount
    thirds = [Share(x, Fraction(1, 3)) for x in "ABC"]
    assert split_amount(D("100"), thirds) == [("A", D("33.34")), ("B", D("33.33")), ("C", D("33.33"))]
    assert split_amount(D("1000"), thirds) == [("A", D("333.34")), ("B", D("333.33")), ("C", D("333.33"))]


def test_c45_same_owner_merged():
    from fractions import Fraction
    from kvartplata.owners import parse_shares
    shares = parse_shares("Иванов И. И. — 1/4; Петрова А. С. — 1/2; Иванов И. И. — 1/4")
    assert [(s.owner, s.part) for s in shares] == [("Иванов И. И.", Fraction(1, 2)), ("Петрова А. С.", Fraction(1, 2))]


def test_c46_inspector_lower_reading():
    from kvartplata.reading_window import accept
    book = _book(("CW1", FEB, "120"))
    assert accept(Reading("CW1", MARCH, D("110"), source="inspector"), date(2026, 3, 20), book) is None
    assert accept(Reading("CW1", MARCH, D("110")), date(2026, 3, 20), book) is not None
    assert accept(Reading("CW1", MARCH, D("110"), date(2026, 3, 22), source="inspector"),
                  date(2026, 3, 20), book) is not None


def test_c47_last_window_day_included():
    from kvartplata.reading_window import period_for
    assert period_for(date(2026, 3, 25)) == MARCH
    assert period_for(date(2026, 6, 25)) == Period(2026, 6)


def test_c48_window_end_on_weekend():
    from kvartplata.reading_window import period_for, window
    assert window(Period(2026, 4)) == (date(2026, 4, 15), date(2026, 4, 27))
    assert period_for(date(2026, 4, 26)) == Period(2026, 4)
    assert period_for(date(2026, 4, 28)) == Period(2026, 5)


def test_c49_no_reminder_when_all_readings_present():
    from kvartplata.reading_window import needs_reminder
    book = _book(("CW1", MARCH, "108"), ("HW1", MARCH, "54"), ("EL1", MARCH, "1210"))
    assert needs_reminder(_flat(), MARCH, book) is False
    book = _book(("CW1", MARCH, "108"), ("HW1", MARCH, "54"))
    assert needs_reminder(_flat(), MARCH, book) is True


def _closing_charges():
    from kvartplata.models import Charge
    return [Charge("maintenance", MARCH, D("54.3"), D("30"), D("1629.00"), "area", discount=D("814.50")),
            Charge("heating", MARCH, D("1.2"), D("2500"), D("3000.00"), "norm"),
            Charge("cold_water", MARCH, D("8.5"), D("52.31"), D("444.64"), "meter")]


def test_c50_actual_month_length():
    from kvartplata.closing import month_days
    assert month_days(MARCH) == 31
    assert month_days(FEB) == 28
    assert month_days(Period(2026, 4)) == 30


def test_c51_transfer_day_counts():
    from kvartplata.closing import owned_days
    assert owned_days(MARCH, date(2026, 3, 16)) == 16
    assert owned_days(MARCH, date(2026, 3, 1)) == 1
    assert owned_days(FEB, date(2026, 2, 28)) == 28


def test_c52_discount_prorated():
    from kvartplata.closing import owned_share, prorate
    from kvartplata.money import round_money
    closed = date(2026, 3, 5)
    charges = prorate(_closing_charges(), MARCH, closed)
    share = owned_share(MARCH, closed)
    assert charges[0].discount == round_money(D("814.50") * share)
    assert charges[0].total >= 0
    assert charges[2].discount == D("0.00")


def test_c53_closing_date_format():
    from kvartplata.closing import final_statement, render
    from kvartplata.ledger import Ledger
    text = render(final_statement(Ledger("12345678905"), _closing_charges(), MARCH, date(2026, 3, 16)))
    assert "Счёт закрыт 16.03.2026" in text
    assert "2026-03-16" not in text


def test_c54_dispatch_log_ru_dates():
    from datetime import datetime
    from kvartplata.quality import parse_dispatch_log
    log = "# журнал АДС\nХВС;10.03.2026 08:15;10.03.2026 13:40;А-12\nотоп;2026-03-11T00:00;2026-03-11T05:00\n"
    outages = parse_dispatch_log(log)
    assert [(o.service, o.start, o.end, o.act) for o in outages] == [
        ("cold_water", datetime(2026, 3, 10, 8, 15), datetime(2026, 3, 10, 13, 40), "А-12"),
        ("heating", datetime(2026, 3, 11, 0, 0), datetime(2026, 3, 11, 5, 0), "")]


def test_c55_registry_amount_in_kopecks():
    from kvartplata.refunds import RefundDecision, RefundRequest, registry, registry_line
    req = RefundRequest("12345678905", date(2026, 4, 6), method="bank_account")
    dec = RefundDecision(True, D("1234.50"), date(2026, 4, 20))
    line = registry_line(req, dec, "Петров Пётр Петрович", "40817810000000000001")
    assert line == "R|12345678905|Петров Пётр Петрович|40817810000000000001|123450"
    text = registry([(req, dec, "Петров Пётр Петрович", "40817810000000000001")], date(2026, 4, 21))
    assert text.splitlines() == ["#REFUND|21.04.2026|1|1234.50",
                                 "R|12345678905|Петров Пётр Петрович|40817810000000000001|123450"]
'''

_HIDDEN_REGRESS = r'''"""Hidden regression tests: behaviour that must NOT change (incl. retracted requests)."""

import io
import json
from contextlib import redirect_stdout
from datetime import date
from decimal import Decimal as D

import pytest

from kvartplata.config import Settings
from kvartplata.meters import ReadingBook
from kvartplata.models import Absence, Account, Meter, Payment, Reading, Resident
from kvartplata.periods import Period
from kvartplata.tariffs import default_tariffs

MARCH = Period(2026, 3)
FEB = Period(2026, 2)


def _res(name="Петров Пётр Петрович", born=date(1980, 1, 15), **kw):
    return Resident(name, born, **kw)


def _flat(**kw):
    base = dict(number="12345678905", address="ул. Садовая, д. 5, кв. 12", total_area=D("54.3"),
                heated_area=D("50.1"),
                residents=[_res(owner=True), _res("Петрова Анна Сергеевна", date(1982, 7, 2))],
                meters=[Meter("CW1", "cold_water"), Meter("HW1", "hot_water"), Meter("EL1", "electricity")])
    base.update(kw)
    return Account(**base)


def _book(*items):
    return ReadingBook([Reading(m, p, D(str(v))) for m, p, v in items])


def _std_book():
    return _book(("CW1", FEB, "100"), ("CW1", MARCH, "108.5"), ("HW1", FEB, "50"), ("HW1", MARCH, "54"),
                 ("EL1", FEB, "1000"), ("EL1", MARCH, "1210"))


def _ctx(account=None, period=MARCH, book=None):
    from kvartplata.services.base import Context
    return Context(account or _flat(), period, book or _std_book(), default_tariffs(), Settings())


# --- money -----------------------------------------------------------------

def test_r_to_money_rejects_comma():
    from kvartplata.money import to_money
    with pytest.raises(ValueError):
        to_money("1,5")


def test_money_basics():
    from kvartplata.money import allocate_by_weights, floor_money, kopecks, percent_of, round_money, to_money
    assert to_money(0.1) == D("0.1")
    assert round_money(D("10.006")) == D("10.01")
    assert round_money(D("10.004")) == D("10.00")
    assert floor_money(D("3.999")) == D("3.99")
    assert kopecks(D("12.34")) == 1234
    assert percent_of(D("200"), 15) == D("30.00")
    assert sum(allocate_by_weights(D("10"), [D("33.3"), D("41.7"), D("54")])) == D("10.00")


def test_format_small_and_split_even():
    from kvartplata.money import format_rub, split_evenly
    assert format_rub(D("15.5")) == "15,50 руб."
    assert format_rub(D("-7.1"), suffix="") == "-7,10"
    assert split_evenly(D("90"), 3) == [D("30.00")] * 3
    assert sum(split_evenly(D("100"), 7)) == D("100.00")


# --- periods / calendar / tariffs ------------------------------------------

def test_periods():
    from kvartplata.periods import overlap_days, period_range
    assert Period.parse("2026-03") == MARCH
    assert Period(2026, 5).prev() == Period(2026, 4)
    assert Period(2025, 12).next() == Period(2026, 1)
    assert MARCH.shift(-6) == Period(2025, 9)
    assert Period(2028, 2).days == 29
    assert MARCH.human() == "март 2026"
    assert len(period_range(Period(2025, 11), FEB)) == 4
    assert overlap_days(MARCH, date(2026, 2, 25), date(2026, 3, 4)) == 4
    with pytest.raises(ValueError):
        Period.parse("март")


def test_calendar():
    from kvartplata.calendar_ru import add_workdays, next_workday
    assert next_workday(date(2026, 3, 14)) == date(2026, 3, 16)
    assert next_workday(date(2026, 3, 11)) == date(2026, 3, 11)
    assert add_workdays(date(2026, 3, 12), 2) == date(2026, 3, 16)


def test_tariffs():
    from kvartplata.tariffs import TariffError, TariffTable
    t = default_tariffs()
    assert t.rate_for("cold_water", date(2026, 3, 15)) == D("52.31")
    assert t.rate_for("cold_water", date(2026, 6, 30)) == D("52.31")
    with pytest.raises(TariffError):
        t.rate_for("cold_water", date(2024, 1, 1))
    table = TariffTable.from_csv("service;zone;start;rate\ncold_water;single;2026-01-01;52,31\n")
    assert table.rate_for_period("cold_water", Period(2026, 4)) == D("52.31")


def test_due_date_workday_unchanged():
    from kvartplata.billing import due_date
    assert due_date(FEB) == date(2026, 3, 10)
    assert due_date(Period(2026, 5)) == date(2026, 6, 10)


# --- normatives / meters ------------------------------------------------------

def test_normatives():
    from kvartplata.normatives import coefficient, norm_volume, persons_for_norm
    assert persons_for_norm(_flat(residents=[], owners_count=3)) == 3
    assert norm_volume(_flat(electric_stove=True), "electricity", Settings()) == D("242")
    assert coefficient(_flat(meters=[]), "gas", Settings()) == 1
    assert coefficient(_flat(meters=[]), "hot_water", Settings()) == D("1.5")
    assert coefficient(_flat(), "cold_water", Settings()) == 1


def test_meters_direct_and_average():
    from kvartplata.meters import consumption, resolve_meter
    m = Meter("CW1", "cold_water")
    assert consumption(D("100"), D("108.5")) == D("8.5")
    assert resolve_meter(m, _flat(), MARCH, _std_book(), Settings()).volume == D("8.5")
    gap = _book(("CW1", Period(2025, 12), "100"), ("CW1", MARCH, "112"))
    assert resolve_meter(m, _flat(), MARCH, gap, Settings()).volume == D("12")
    avg = _book(("CW1", Period(2025, 12), "100"), ("CW1", Period(2026, 1), "106"), ("CW1", FEB, "112"))
    res = resolve_meter(m, _flat(), MARCH, avg, Settings())
    assert (res.volume, res.method) == (D("6"), "average")


# --- services ------------------------------------------------------------------

def test_cold_hot_electricity_single():
    from kvartplata.services import cold_water, electricity, hot_water
    (c,) = cold_water.calculate(_ctx())
    assert c.amount == D("444.64")
    (h,) = hot_water.calculate(_ctx())
    assert "теплоноситель 178.56" in h.note
    (e,) = electricity.calculate(_ctx())
    assert (e.volume, e.rate, e.amount) == (D("210"), D("6.98"), D("1465.80"))


def test_two_zone_day_line():
    from kvartplata.services import electricity
    acc = _flat(meters=[Meter("ED", "electricity", "day"), Meter("EN", "electricity", "night")])
    book = _book(("ED", FEB, "1000"), ("ED", MARCH, "1150"), ("EN", FEB, "500"), ("EN", MARCH, "560"))
    day, _night = electricity.calculate(_ctx(acc, book=book))
    assert (day.volume, day.rate, day.amount) == (D("150"), D("7.95"), D("1192.50"))


def test_r_heating_all_year():
    from kvartplata.services import heating
    charges = heating.calculate(_ctx(period=Period(2026, 7)))
    assert len(charges) == 1 and charges[0].amount > 0
    (c,) = heating.calculate(_ctx())
    assert c.method == "area" and c.rate == D("3044.18")


def test_r_waste_per_person():
    from kvartplata.services import waste
    (c,) = waste.calculate(_ctx())
    assert (c.volume, c.rate, c.amount, c.method) == (D("2"), D("148.62"), D("297.24"), "person")


def test_maintenance_gas_capital():
    from kvartplata.services import capital_repair, gas, maintenance
    (m,) = maintenance.calculate(_ctx())
    assert m.amount == D("1759.32")
    assert gas.calculate(_ctx()) == []
    (g,) = gas.calculate(_ctx(_flat(has_gas=True)))
    assert g.volume == D("20.4")
    young = _flat(residents=[_res(born=date(1957, 6, 1), owner=True, lives_alone=True)])
    (c,) = capital_repair.calculate(_ctx(young))
    assert c.discount == 0
    pair = _flat(residents=[_res(born=date(1940, 1, 1), owner=True), _res("Петрова Анна Сергеевна")])
    (c,) = capital_repair.calculate(_ctx(pair))
    assert c.discount == 0


def test_privileges_regular():
    from kvartplata.privileges import discount_for, resident_percent
    from kvartplata.services import cold_water
    assert resident_percent(["large_family"], "cold_water") == 30
    assert resident_percent([], "cold_water") == 0
    acc = _flat(residents=[_res(privileges=["large_family"]), _res("Петрова Анна Сергеевна")])
    (c,) = cold_water.calculate(_ctx(acc))
    assert discount_for(c, acc, Settings()) == D("66.70")


# --- recalc / penalties / payments / ledger ------------------------------------------

def test_recalc_regular():
    from kvartplata.billing import compute_charges
    from kvartplata.recalc import eligible
    a = Absence("Петров Пётр Петрович", date(2026, 3, 1), date(2026, 3, 20))
    assert eligible(a, Settings())
    assert not eligible(Absence("Петров Пётр Петрович", date(2026, 3, 1), date(2026, 3, 20), False), Settings())
    charges = compute_charges(_flat(), MARCH, _std_book(), default_tariffs(), absences=[a])
    assert next(c for c in charges if c.service == "cold_water").recalculation == 0
    charges = compute_charges(_flat(meters=[]), MARCH, _std_book(), default_tariffs(), absences=[a])
    assert next(c for c in charges if c.service == "waste").recalculation < 0


def test_penalties_regular():
    from kvartplata.penalties import penalty
    s = Settings()
    assert penalty(D("1000"), date(2026, 3, 10), date(2026, 4, 9), s) == 0
    assert penalty(D("3000"), date(2026, 3, 10), date(2026, 4, 19), s) == D("15.50")
    assert penalty(D("0"), date(2026, 1, 10), date(2026, 4, 19), s) == 0


def test_r_penalty_no_moratorium():
    from kvartplata.penalties import penalty
    assert penalty(D("3000"), date(2025, 12, 10), date(2026, 1, 19), Settings()) > 0


def test_payments_regular():
    from kvartplata.payments import Debt, allocate
    res = allocate(D("500"), [Debt("2026-02", D("400"))])
    assert res.paid_for("2026-02") == D("400") and res.advance == D("100")
    with pytest.raises(ValueError):
        allocate(D("-1"), [])


def test_ledger_regular():
    from kvartplata.ledger import Ledger
    led = Ledger("12345678905")
    led.add_charge(Period(2026, 1), D("1000"))
    led.add_charge(FEB, D("1200"))
    led.add_payment(Payment("12345678905", date(2026, 2, 5), D("1500")))
    assert led.debts(FEB, date(2026, 3, 1)) == [(FEB, D("700"))]
    assert led.balance(FEB, date(2026, 3, 1)) == D("700.00")


def test_bill_regular():
    from kvartplata.billing import compute_bill, compute_charges
    charges = compute_charges(_flat(), MARCH, _std_book(), default_tariffs())
    assert [c.service for c in charges] == ["cold_water", "hot_water", "sewage", "electricity", "heating",
                                            "maintenance", "capital_repair", "waste"]
    bill = compute_bill(_flat(), MARCH, _std_book(), default_tariffs())
    assert bill.to_pay == sum(c.total for c in bill.charges)


# --- receipt / words / export / import ---------------------------------------

def test_r_receipt_keeps_zero_lines():
    from kvartplata import receipt
    from kvartplata.billing import compute_bill
    acc = _flat(residents=[_res(born=date(1940, 2, 1), owner=True, lives_alone=True)])
    bill = compute_bill(acc, MARCH, _std_book(), default_tariffs())
    cap = next(c for c in bill.charges if c.service == "capital_repair")
    assert cap.total == 0
    text = receipt.render(bill)
    assert "Взнос на капитальный ремонт" in text
    assert "ПЛАТЁЖНЫЙ ДОКУМЕНТ за март 2026" in text
    assert "|PaymPeriod=0326|" in receipt.qr_payload(bill)


def test_words():
    from kvartplata.words import amount_in_words, number_in_words
    assert number_in_words(2021) == "две тысячи двадцать один"
    assert amount_in_words(D("1234.05")) == "Одна тысяча двести тридцать четыре рубля 05 копеек"


def test_export_totals_and_lines():
    from kvartplata.billing import compute_bill
    from kvartplata.export import export_bills, export_totals
    bill = compute_bill(_flat(), MARCH, _std_book(), default_tariffs())
    assert len(export_bills([bill]).splitlines()) == 9
    assert export_totals([bill]).splitlines()[0].replace(";", ",") == "account,period,charged,incoming,penalty,to_pay"


def test_importer_regular():
    from kvartplata.importer import import_readings
    res = import_readings("meter_id;period;value;taken_on\nCW1;2026-13;1;\nCW2;2026-03;5;24.03.2026\n")
    assert len(res.errors) == 1 and len(res.readings) == 1
    assert res.readings[0].taken_on == date(2026, 3, 24)
    res = import_readings("meter;period;value\nCW1;2026-03;1\n")
    assert res.errors and not res.readings


# --- misc modules ----------------------------------------------------------------

def test_validators():
    from kvartplata.validators import is_valid_account, is_valid_email, is_valid_inn, make_account_number, normalize_phone
    assert is_valid_account("12345678905")
    assert is_valid_account(make_account_number("7801234560"))
    assert not is_valid_account("1234")
    assert is_valid_inn("7707083893") and not is_valid_inn("7707083894")
    assert normalize_phone("8 (921) 123-45-67") == "+79211234567"
    assert is_valid_email("a.b@example.ru")


def test_people_addresses():
    from kvartplata.addresses import normalize
    from kvartplata.people import FullName, dative
    assert FullName.parse("Иванов Иван Иванович").initials() == "Иванов И. И."
    assert dative(FullName.parse("Петрова Анна Сергеевна")) == "Петровой Анне Сергеевне"
    assert normalize("Улица Садовая,  дом 5 , квартира 12") == "ул. садовая, д. 5, кв. 12"


def test_subsidy_installments():
    from kvartplata.installments import apply_payment, make_agreement
    from kvartplata.subsidies import Family, subsidy
    assert subsidy(Family(2, D("200000")), D("9000")) == 0
    assert subsidy(Family(1, D("15000")), D("6000")) > 0
    ag = make_agreement("12345678905", D("1000"), Period(2026, 4), 3)
    assert ag.total == D("1000.00") and len(ag.items) == 3
    assert apply_payment(make_agreement("12345678905", D("900"), Period(2026, 1), 3), D("1000")) == D("100.00")


def test_reconciliation_and_registry():
    from kvartplata.bank_registry import parse_registry
    from kvartplata.ledger import Ledger
    from kvartplata.reconciliation import rows
    led = Ledger("12345678905")
    led.add_charge(Period(2026, 1), D("1000"))
    led.add_payment(Payment("12345678905", date(2026, 1, 20), D("400")))
    r = rows(led, Period(2026, 1), FEB)
    assert [x.closing for x in r] == [D("600.00"), D("600.00")]
    reg = parse_registry("#REESTR;Банк;2026-03-20\n19.03.2026;12345678905;1500,00;П;A-1\n=;1;1500.00\n")
    assert reg.total == D("1500.00")


def test_verification_notice():
    from kvartplata.ledger import Ledger
    from kvartplata.notices import build_notice
    from kvartplata.verification import is_expired
    assert is_expired(Meter("HW1", "hot_water", installed_on=date(2022, 3, 1)), date(2026, 3, 1))
    led = Ledger("12345678905")
    led.add_charge(Period(2026, 1), D("1000"))
    assert build_notice(led, "Петрова Анна Сергеевна", "ул. Садовая", MARCH, date(2026, 4, 1)) is None


def test_storage_and_cli(tmp_path):
    from kvartplata.cli import main
    from kvartplata.storage import Store, account_to_dict
    store = Store()
    acc = _flat()
    store.accounts[acc.number] = acc
    store.readings = [Reading("CW1", FEB, D("100")), Reading("CW1", MARCH, D("108.5"))]
    path = tmp_path / "data.json"
    store.save(path)
    again = Store.load(path)
    assert account_to_dict(again.accounts[acc.number]) == account_to_dict(acc)
    assert json.loads(path.read_text(encoding="utf-8"))["readings"][1]["value"] == "108.5"
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["bill", "--data", str(path), "--account", acc.number, "--period", "2026-03"])
    assert code == 0
    assert "ИТОГО К ОПЛАТЕ" in buf.getvalue()


# --- modules outside the requested changes ------------------------------------

def test_gis_export_format_is_fixed():
    from kvartplata import gis_export
    from kvartplata.billing import compute_bill
    text = gis_export.export([compute_bill(_flat(), MARCH, _std_book(), default_tariffs())])
    assert text.startswith("﻿#GIS|7801234565|03.2026|1\n")
    assert "\nS|01|8.5|52.31|444.64|444.64\n" in text
    assert ";" not in text


def test_court_duty():
    from kvartplata.court import claim_duty, court_order_duty
    assert claim_duty(D("50000")) == D("4000")
    assert claim_duty(D("150000")) == D("5500")
    assert claim_duty(D("400000")) == D("12500")
    assert court_order_duty(D("20000")) == D("2000")


def test_anomalies_and_html():
    from kvartplata import receipt_html
    from kvartplata.anomalies import meter_flags
    from kvartplata.billing import compute_bill
    book = _book(("CW1", Period(2025, 12), "100"), ("CW1", Period(2026, 1), "105"), ("CW1", FEB, "110"),
                 ("CW1", MARCH, "140"))
    assert [f.kind for f in meter_flags(_flat(), MARCH, book, Settings())] == ["spike"]
    html = receipt_html.render(compute_bill(_flat(), MARCH, _std_book(), default_tariffs()))
    assert html.startswith("<!DOCTYPE html>") and "Холодное водоснабжение" in html


def test_settings_io_and_indexation():
    from datetime import date as _date
    from kvartplata.indexation import cost
    from kvartplata.settings_io import SettingsError, settings_from_dict
    s = settings_from_dict({"maintenance_rate": "33.10", "absence_min_days": 7})
    assert s.maintenance_rate == D("33.10") and s.absence_min_days == 7
    with pytest.raises(SettingsError):
        settings_from_dict({"maintanance_rate": "1"})
    assert cost({"cold_water": D("8")}, default_tariffs(), _date(2026, 3, 1)) == D("418.48")


def test_debt_plan_and_privilege_report():
    from kvartplata import debt_collection, privileges_report
    from kvartplata.billing import compute_bill
    from kvartplata.ledger import Ledger
    led = Ledger("12345678905")
    p = Period(2025, 10)
    for _ in range(3):
        led.add_charge(p, D("3000"))
        p = p.next()
    plan = debt_collection.plan(led, FEB, date(2026, 3, 2))
    assert [s.kind for s in plan.steps] == ["notice", "warning", "restriction", "court"]
    assert plan.step("restriction").on == date(2026, 4, 6)
    acc = _flat(residents=[_res(privileges=["large_family"]), _res("Петрова Анна Сергеевна")])
    totals = privileges_report.by_category([compute_bill(acc, MARCH, _std_book(), default_tariffs())])
    assert totals[("large_family", "cold_water")] == D("66.70")


def test_statement_corrections_lock():
    from datetime import datetime
    from kvartplata import corrections, statement
    from kvartplata.billing import compute_bill, compute_charges
    from kvartplata.ledger import Ledger
    from kvartplata.periods_lock import PeriodLock, PeriodLockedError
    bills = [compute_bill(_flat(), p, _std_book(), default_tariffs()) for p in (FEB, MARCH)]
    led = Ledger("12345678905")
    led.add_payment(Payment("12345678905", date(2026, 3, 5), D("1000")))
    st = statement.build(led, bills, FEB, MARCH)
    assert st.rows[-1].closing == st.total_charged - st.total_paid
    corr = corrections.create("12345678905", MARCH, "cold_water", "-120.50", "ОБР-1043", "ivanova", date(2026, 3, 20))
    charges = compute_charges(_flat(), MARCH, _std_book(), default_tariffs())
    corrections.apply(charges, [corr], "12345678905", MARCH)
    assert next(c for c in charges if c.service == "cold_water").recalculation == D("-120.50")
    assert not corrections.create("1", MARCH, "heating", "7000", "СУД-1", "a", date(2026, 3, 1)).effective
    lock = PeriodLock(Period(2025, 10))
    lock.close(Period(2025, 10), "admin", datetime(2025, 11, 12, 10, 0))
    with pytest.raises(PeriodLockedError):
        lock.close(Period(2025, 12), "admin", datetime(2026, 1, 12, 10, 0))


def test_sms_forecast_gis_readings():
    from kvartplata import forecast, gis_readings, sms
    assert sms.sms_amount(D("1250")) == "1 250 руб."
    assert sms.sms_amount(D("1250.5")) == "1 250,50 руб."
    msg = sms.debt_sms("8 921 123-45-67", "12345678905", D("4312.2"), date(2026, 4, 10))
    assert msg.phone == "+79211234567" and "4 312,20 руб." in msg.text
    book = _book(("CW1", Period(2025, 12), "100"), ("CW1", Period(2026, 1), "106"), ("CW1", FEB, "112"))
    acc = _flat(meters=[Meter("CW1", "cold_water")])
    assert forecast.total(forecast.forecast(acc, MARCH, book, default_tariffs(), Settings())) == D("313.86")
    line = gis_readings.format_line(Reading("CW-0001", MARCH, D("123.456"), date(2026, 3, 24)))
    assert gis_readings.parse(line).readings[0].value == D("123.456")


def test_replacement_and_tenants():
    from kvartplata.meter_replacement import Replacement, replacement_volume
    from kvartplata.tenants import Registration, persons_in_period
    rep = Replacement(Meter("CW-OLD", "cold_water"), Meter("CW-NEW", "cold_water"), date(2026, 3, 10),
                      D("250.5"), D("0.2"))
    assert replacement_volume(rep, MARCH, D("245"), D("4.2")) == D("9.5")
    assert replacement_volume(rep, MARCH, D("245"), None) == D("17.050")
    regs = [Registration("A", date(2020, 1, 1)), Registration("B", date(2026, 3, 17)),
            Registration("C", date(2019, 5, 5), date(2026, 3, 10))]
    assert persons_in_period(regs, MARCH) == D("1.8065")

# --- второй круг --------------------------------------------------------------

def _refund_ledger(paid, charged="300"):
    from kvartplata.ledger import Ledger
    led = Ledger("12345678905")
    led.add_charge(FEB, D(charged))
    led.add_payment(Payment("12345678905", date(2026, 3, 5), D(paid)))
    return led


def test_r_quality_unconfirmed_ignored():
    from datetime import datetime
    from kvartplata.quality import Outage, excess_hours
    unconfirmed = [Outage("cold_water", datetime(2026, 3, 3, 0, 0), datetime(2026, 3, 3, 20, 0), source="resident")]
    assert excess_hours(unconfirmed, "cold_water", MARCH) == 0
    with_act = [Outage("cold_water", datetime(2026, 3, 3, 9, 0), datetime(2026, 3, 3, 13, 0), source="resident",
                       act="А-5"),
                Outage("cold_water", datetime(2026, 3, 17, 9, 0), datetime(2026, 3, 17, 15, 0))]
    assert excess_hours(with_act, "cold_water", MARCH) == 2


def test_quality_reduction_unchanged():
    from datetime import datetime
    from kvartplata.models import Charge
    from kvartplata.quality import ALLOWED_HOURS, REDUCTION_PER_HOUR, Outage, apply_reductions, reduction
    assert REDUCTION_PER_HOUR == D("0.15")
    assert ALLOWED_HOURS["hot_water"] == (8, 4) and ALLOWED_HOURS["heating"] == (24, 16)
    outages = [Outage("cold_water", datetime(2026, 3, 3, 9, 0), datetime(2026, 3, 3, 13, 0)),
               Outage("cold_water", datetime(2026, 3, 17, 9, 0), datetime(2026, 3, 17, 15, 0))]
    charge = Charge("cold_water", MARCH, D("8.5"), D("52.31"), D("444.64"), "meter", discount=D("100.00"))
    assert reduction(charge, outages, MARCH) == D("1.33")
    apply_reductions([charge], outages, MARCH)
    assert charge.recalculation == D("-1.33") and charge.discount == D("100.00")
    other = Charge("hot_water", MARCH, D("4"), D("200"), D("800.00"), "meter")
    assert reduction(other, outages, MARCH) == D("0.00")


def test_r_refund_no_cash():
    from kvartplata.refunds import METHODS, RefundRequest, decide
    assert "cash" not in METHODS
    assert not decide(RefundRequest("12345678905", date(2026, 4, 6), method="cash"), _refund_ledger("1000")).approved


def test_refunds_unchanged():
    from kvartplata.refunds import MIN_REFUND, RefundRequest, decide, overpayment
    assert MIN_REFUND == D("100.00")
    led = _refund_ledger("1000")
    assert overpayment(led, date(2026, 4, 6)) == D("700.00")
    d = decide(RefundRequest("12345678905", date(2026, 4, 6)), led)
    assert d.approved and d.amount == D("700.00")
    assert decide(RefundRequest("12345678905", date(2026, 4, 6), D("250")), led).amount == D("250.00")
    assert not decide(RefundRequest("12345678905", date(2026, 4, 6)), _refund_ledger("350")).approved
    assert not decide(RefundRequest("12345678905", date(2026, 4, 6), applicant_is_owner=False), led).approved
    assert not decide(RefundRequest("12345678905", date(2026, 4, 6)), _refund_ledger("200")).approved
    assert not decide(RefundRequest("40000000013", date(2026, 4, 6)), led).approved


def test_r_owner_shares_must_sum_to_one():
    from fractions import Fraction
    from kvartplata.owners import Share, validate
    with pytest.raises(ValueError):
        validate([Share("A", Fraction(1, 2)), Share("B", Fraction(1, 3))])
    with pytest.raises(ValueError):
        validate([Share("A", Fraction(2, 3)), Share("B", Fraction(2, 3))])


def test_owners_unchanged():
    from fractions import Fraction
    from kvartplata.owners import Share, parse_share, parse_shares, split_amount
    assert parse_share("2/5") == Fraction(2, 5) and parse_share("1") == Fraction(1)
    for bad in ("0", "3/2", "1/0", "треть"):
        with pytest.raises(ValueError):
            parse_share(bad)
    shares = parse_shares("Иванова-Петрова А. С. — 1/2; Иванов И. И. - 1/2")
    assert [s.owner for s in shares] == ["Иванова-Петрова А. С.", "Иванов И. И."]
    halves = [Share("A", Fraction(1, 2)), Share("B", Fraction(1, 2))]
    assert split_amount(D("1000.10"), halves) == [("A", D("500.05")), ("B", D("500.05"))]


def test_reading_window_unchanged():
    from kvartplata.reading_window import (WINDOW_END, WINDOW_START, accept, needs_reminder, period_for,
                                           reminder_date, window)
    assert (WINDOW_START, WINDOW_END) == (15, 25)
    assert window(MARCH) == (date(2026, 3, 15), date(2026, 3, 25))
    assert period_for(date(2026, 3, 10)) == MARCH
    assert period_for(date(2026, 3, 26)) == Period(2026, 4)
    assert reminder_date(MARCH) == date(2026, 3, 22)
    book = _book(("CW1", FEB, "120"))
    assert accept(Reading("CW1", MARCH, D("110"), source="import"), date(2026, 3, 20), book) is not None
    assert accept(Reading("CW1", MARCH, D("125")), date(2026, 3, 20), book) is None
    assert needs_reminder(_flat(), MARCH, _book(("CW1", MARCH, "108"))) is True
    assert needs_reminder(_flat(meters=[]), MARCH, _book()) is False


def test_r_closing_heating_prorated():
    from kvartplata.closing import PRORATED, owned_share, prorate
    from kvartplata.models import Charge
    from kvartplata.money import round_money
    assert "heating" in PRORATED
    closed = date(2026, 3, 16)
    charges = prorate([Charge("heating", MARCH, D("1.2"), D("2500"), D("3000.00"), "norm")], MARCH, closed)
    assert charges[0].amount == round_money(D("3000.00") * owned_share(MARCH, closed))


def test_closing_unchanged():
    from kvartplata.closing import final_statement, owned_share, prorate, render
    from kvartplata.ledger import Ledger
    from kvartplata.models import Charge
    from kvartplata.money import round_money
    closed = date(2026, 3, 16)
    charges = [Charge("maintenance", MARCH, D("54.3"), D("30"), D("1629.00"), "area"),
               Charge("cold_water", MARCH, D("8.5"), D("52.31"), D("444.64"), "meter"),
               Charge("waste", MARCH, D("2"), D("150"), D("300.00"), "person")]
    prorate(charges, MARCH, closed)
    share = owned_share(MARCH, closed)
    assert charges[0].amount == round_money(D("1629.00") * share)
    assert charges[1].amount == D("444.64") and charges[1].note == ""
    assert charges[2].amount == round_money(D("300.00") * share)
    led = Ledger("12345678905")
    led.add_charge(FEB, D("3000"))
    led.add_payment(Payment("12345678905", date(2026, 3, 5), D("9000")))
    st = final_statement(led, [Charge("cold_water", MARCH, D("8.5"), D("52.31"), D("444.64"), "meter")], MARCH, closed)
    assert st.balance == D("-6000.00") and st.to_refund == D("5555.36") and st.to_pay == D("0.00")
    assert "Переплата к возврату" in render(st)
    with pytest.raises(ValueError):
        prorate([], MARCH, date(2026, 4, 1))


def test_second_round_helpers_unchanged():
    from datetime import datetime
    from fractions import Fraction
    from kvartplata.owners import Share, describe, largest_owner
    from kvartplata.quality import parse_dispatch_log, summary
    from kvartplata.reading_window import render_schedule, schedule
    from kvartplata.refunds import RefundDecision, RefundRequest, registry_line
    outages = parse_dispatch_log("ГВС;2026-03-10T08:00;2026-03-10T11:00\n\nГАЗ;2026-03-12T10:00;2026-03-12T12:00;А-3\n")
    assert [(o.service, o.confirmed) for o in outages] == [("hot_water", True), ("gas", True)]
    assert outages[0].start == datetime(2026, 3, 10, 8, 0)
    with pytest.raises(ValueError):
        parse_dispatch_log("ПАР;2026-03-10T08:00;2026-03-10T14:00\n")
    assert summary(outages, MARCH) == {"gas": (2, 0), "hot_water": (3, 0)}
    shares = [Share("Иванов И. И.", Fraction(1, 4)), Share("Петрова А. С.", Fraction(3, 4))]
    assert describe(shares) == "Иванов И. И. — 1/4; Петрова А. С. — 3/4"
    assert largest_owner(shares) == "Петрова А. С."
    assert largest_owner([Share("A", Fraction(1, 2)), Share("B", Fraction(1, 2))]) == "A"
    rows = schedule(2026)
    assert len(rows) == 12 and rows[2] == (MARCH, date(2026, 3, 15), date(2026, 3, 25), date(2026, 3, 22))
    assert "март 2026" in render_schedule(2026)
    req = RefundRequest("12345678905", date(2026, 4, 6), method="card")
    with pytest.raises(ValueError):
        registry_line(req, RefundDecision(True, D("700"), date(2026, 4, 20)), "Петров", "40817810000000000001")
    req = RefundRequest("12345678905", date(2026, 4, 6), method="bank_account")
    with pytest.raises(ValueError):
        registry_line(req, RefundDecision(False, D("0"), date(2026, 4, 20)), "Петров", "40817810000000000001")
    with pytest.raises(ValueError):
        registry_line(req, RefundDecision(True, D("700"), date(2026, 4, 20)), "Петров", "4081781000")
'''
