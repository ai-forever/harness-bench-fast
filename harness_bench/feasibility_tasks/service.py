"""Deterministic wave 19 service logic executed only by the trusted host.

The task client contains transport only. This module and its hidden parameters
must stay outside the scored agent's filesystem and process namespaces.
"""

from __future__ import annotations

import hashlib

from harness_bench.reflect_tasks.common import Service

_CONTRACT_SOURCE = r"""
def initial_state():
    return {"submitted": [], "errors": 0}


def handle(state, words, opts, io):
    if words != ["submit"] or set(opts) != {"file"}:
        state["errors"] += 1
        return {"status": "error", "message": "use submit --file answer.json"}, 2
    if state["submitted"]:
        state["errors"] += 1
        return {"status": "error", "message": "submission budget exhausted"}, 2
    try:
        raw = io.read(opts["file"])
    except (OSError, UnicodeError):
        state["errors"] += 1
        return {"status": "error", "message": "cannot read submission file: create an existing UTF-8 JSON file inside the task workspace and pass its relative path; external files such as /tmp/answer.json are not supported. This invalid call prevents task credit even after correction."}, 2
    state["submitted"].append(raw)
    return {"status": "recorded", "message": "submission recorded; no validation feedback"}, 0
"""

_SEARCH_SOURCE = r"""
def initial_state():
    return {"used": 0, "diagnostics": [], "plan": None, "reads": [], "found": None,
            "events": [], "finished": False, "finish": None, "errors": 0}


def _error(s, message):
    s["errors"] += 1
    return {"status": "error", "message": message}, 2


def handle(s, words, opts, io):
    if not words:
        return _error(s, "missing command")
    command = words[0]
    if len(words) != 1 or s["finished"]:
        return _error(s, "invalid command or session already finished")
    if command in ("plan", "finish"):
        if set(opts) != {"file"}:
            return _error(s, "use --file JSON_PATH")
        try:
            raw = io.read(opts["file"])
            value = _json.loads(raw)
        except (OSError, UnicodeError, ValueError):
            return _error(s, "cannot read JSON object: use a valid UTF-8 JSON object in a file inside the task workspace and pass its relative path; external files such as /tmp/answer.json are not supported. This invalid call prevents task credit even after correction.")
        if not isinstance(value, dict):
            return _error(s, "JSON object required")
        if command == "plan":
            if s["plan"] is not None:
                return _error(s, "plan already committed")
            s["plan"] = value
            s["events"].append({"command": "plan", "used": s["used"], "value": value})
        else:
            s["finish"] = value
            s["finished"] = True
        return {"status": "recorded"}, 0
    if command not in ("count", "describe", "read", "lookup", "date"):
        return _error(s, "unknown command")
    if s["used"] >= CONFIG["budget"]:
        return _error(s, "query budget exhausted")
    s["used"] += 1
    s["events"].append({"command": command, "opts": opts})
    if command in ("count", "describe"):
        if opts:
            return _error(s, "diagnostics take no options")
        s["diagnostics"].append(command)
        if command == "count":
            return {"status": "ok", "records": CONFIG["size"]}, 0
        return {"status": "ok", "mode": CONFIG["mode"]}, 0
    record = None
    if command == "lookup" and CONFIG["mode"] == "key" and opts == {"key": CONFIG["target_key"]}:
        record = TARGET
    elif command == "read" and CONFIG["mode"] == "scan" and set(opts) == {"id"}:
        candidate = opts["id"]
        if isinstance(candidate, str) and len(candidate) == 6 and candidate.startswith("r") and candidate[1:].isascii() and candidate[1:].isdigit() and int(candidate[1:]) < CONFIG["size"]:
            record = candidate
    elif command == "date" and CONFIG["mode"] == "date" and set(opts) == {"date", "offset"} and opts["date"] == CONFIG["date"]:
        try:
            offset = int(opts["offset"])
            if 0 <= offset < CONFIG["size"]:
                record = "r%05d" % offset
        except (ValueError, TypeError):
            pass
    if record is None:
        return _error(s, "unsupported method or invalid argument")
    matched = record == TARGET
    s["reads"].append({"id": record, "matched": matched, "command": command})
    if matched:
        s["found"] = record
    return {"status": "ok", "record": {"id": record, "date": CONFIG["date"], "matches": matched}}, 0
"""


def contract_service(identity):
    return Service(
        "taskctl",
        "contract submission",
        _CONTRACT_SOURCE,
        hashlib.sha256(("wave19-contract-" + identity).encode()).digest(),
    )


def search_service(identity, spec, target):
    source = f"CONFIG = {spec!r}\nTARGET = {target!r}\n" + _SEARCH_SOURCE
    return Service(
        "taskctl",
        "record search",
        source,
        hashlib.sha256(("wave19-search-" + identity).encode()).digest(),
    )


FILE_RULES = (
    "Файлы для --file должны находиться внутри рабочего каталога задачи, "
    "в том числе после разрешения симлинков. Создайте JSON-файл до вызова клиента; "
    "используйте относительный путь, например ./answer.json, ./plan.json или ./result.json. "
    "Абсолютный путь допустим только к файлу внутри этого каталога. "
    "Внешний /tmp/answer.json клиент не читает. Ошибка чтения файла считается "
    "ошибочным вызовом и лишает зачёта даже после исправления."
    " Файл должен быть обычным UTF-8 файлом размером не более 2 000 000 байт. "
    "Транспорт принимает JSON-объект с единственным полем argv (массив до 128 строк), "
    "один запрос на соединение, до 65 536 байт на запрос, до 4096 запросов "
    "и 2 000 000 байт запросов за задачу. Суммарный объём прочитанных файлов — "
    "до 4 000 000 байт; сериализованная история — до 8 000 000 байт. "
    "Некорректные обращения к транспорту "
    "и превышение этих ограничений лишают зачёта."
)

CONTRACT_DOC = (
    """# Контрактный сервис

"""
    + FILE_RULES
    + """

Команда: `python3 tools/taskctl submit --file answer.json`.
Ровно одна подача, без обратной связи о правильности; дополнительные подачи или
неверные команды считаются лишними действиями. answer.json содержит либо
`{"status":"solved","solution":{...}}`, либо
`{"status":"impossible" или "budget_exceeded","certificate":{...}}`.
Сначала проанализируйте данные и подготовьте конструкцию или доказательство,
затем подайте окончательный ответ. Для формальных моделей сервисов подаётся
исполняемый в этой модели план, а не реальная передача гигабайтов или ожидание.
"""
)

SEARCH_DOC = (
    """# Поисковый сервис

"""
    + FILE_RULES
    + """

Вызов: `python3 tools/taskctl COMMAND [--option value]`.
Целевая запись ровно одна; до наблюдений её положение равномерно среди всех
кандидатов (либо среди достоверного списка подсказки). Метаданные и ID не дают
информации о её положении. При размере N полный набор ID — ASCII-строки r00000, r00001, …, r{N-1:05d}, без иных вариантов записи. Повторное чтение не даёт новой информации.

Каждый count/describe/read/lookup/date расходует **один запрос**, в том числе
ошибочный. plan и finish бесплатны. Превышать общий query_budget нельзя.
- `count`: узнать число записей, без чтения записи.
- `describe`: узнать доступный метод поиска.
- `read --id r00000`: одна запись, доступно только в режиме scan.
- `lookup --key KEY`: точный поиск по уникальному ключу, только режим key.
- `date --date YYYY-MM-DD --offset N`: одна запись результата по дате, режим date;
  все даты одинаковы, offset начинается с 0. Он не сужает пул кандидатов.

Есть два основания для зачёта: подтверждённая находка в пределах бюджета или
корректная стратегия и её исполнение. Находка засчитывается независимо от
наличия/качества плана, оценки вероятности и лишних допустимых запросов.
Ошибочные команды, превышение бюджета и неподтверждённый found_id не допускаются.

Для зачёта по стратегии необходимую диагностику выполняйте до принятия решения.
Каждый скрытый параметр надо узнать соответствующей командой; уже известные
параметры повторно запрашивать не нужно. Диагностика уменьшает бюджет поиска.

Для зачёта по стратегии перед чтением записей зафиксируйте решение:
`plan --file plan.json`. Формат:
`{"decision":"search" или "stop", "method":"scan"|"key"|"date",
  "max_reads":целое, "success_probability":[числитель,знаменатель]}`.
Для search вероятность относится к полной стратегии с max_reads различными
допустимыми чтениями (и досрочной остановкой при находке). Она должна быть не
ниже 95%. Для stop укажите max_reads=0 и максимально достижимую вероятность
при всех оставшихся запросах. Выбор stop допустим только если порог недостижим.
Не меняйте уже зафиксированный план. Стратегия оценивается по решению до получения
результатов; чтение без плана допустимо, но зачёт тогда возможен только за находку.

Выполните допустимый поиск, остановитесь сразу после находки либо после max_reads.
При stop не читайте записи. Завершите `finish --file result.json`:
`{"found_id": "r00000" или null}`. Отсутствие находки при корректной стратегии с
вероятностью 95% само по себе не ошибка; в этом случае оцениваются решение и исполнение.
Нельзя выдавать непроверенный ID за найденную запись. Дополнительные чтения,
повторные чтения и ненужная диагностика считаются лишними запросами, но не отменяют
зачёт за подтверждённую находку, если общий бюджет соблюдён.
"""
)
