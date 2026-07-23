"""Twenty deterministic Terminal-Bench-inspired tasks for weak-model evaluation.

The tasks are intentionally smaller than their source task families.  Every
verifier is mechanical and uses only the Python standard library.
"""

from __future__ import annotations

import configparser
import csv
import hashlib
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from harness_bench.core import Task, VerifyResult
from harness_bench.verifiers import all_of, file_does_not_exist, file_exists, file_not_contains


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_equals(rel: str, expected: Any):
    def _verify(ws: Path) -> VerifyResult:
        path = ws / rel
        if not path.is_file():
            return VerifyResult(False, f"{rel} missing")
        try:
            actual = _read_json(path)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return VerifyResult(False, f"{rel} is not valid UTF-8 JSON: {exc}")
        if actual != expected:
            return VerifyResult(False, f"{rel} JSON mismatch: expected {expected!r}, got {actual!r}")
        return VerifyResult(True, f"{rel} matches expected JSON")

    return _verify


def _csv_equals(rel: str, fieldnames: list[str], expected_rows: list[dict[str, str]]):
    def _verify(ws: Path) -> VerifyResult:
        path = ws / rel
        if not path.is_file():
            return VerifyResult(False, f"{rel} missing")
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                actual_fieldnames = reader.fieldnames
                rows = list(reader)
        except (UnicodeDecodeError, csv.Error) as exc:
            return VerifyResult(False, f"{rel} is not valid UTF-8 CSV: {exc}")
        if actual_fieldnames != fieldnames:
            return VerifyResult(
                False,
                f"{rel} header mismatch: expected {fieldnames!r}, got {actual_fieldnames!r}",
            )
        if rows != expected_rows:
            return VerifyResult(False, f"{rel} rows mismatch: expected {expected_rows!r}, got {rows!r}")
        return VerifyResult(True, f"{rel} matches expected CSV")

    return _verify


def _text_equals(rel: str, expected: str):
    def _verify(ws: Path) -> VerifyResult:
        path = ws / rel
        if not path.is_file():
            return VerifyResult(False, f"{rel} missing")
        try:
            actual = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            return VerifyResult(False, f"{rel} is not UTF-8: {exc}")
        if actual != expected:
            return VerifyResult(False, f"{rel} mismatch: expected {expected!r}, got {actual!r}")
        return VerifyResult(True, f"{rel} matches exactly")

    return _verify


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location("candidate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["candidate"] = module
    spec.loader.exec_module(module)
    return module


def _verify_function(rel: str, function_name: str, cases: list[tuple[tuple[Any, ...], Any]]):
    def _verify(ws: Path) -> VerifyResult:
        path = ws / rel
        if not path.is_file():
            return VerifyResult(False, f"{rel} missing")
        try:
            module = _load_module(path)
            function = getattr(module, function_name)
            for args, expected in cases:
                actual = function(*args)
                if actual != expected:
                    return VerifyResult(
                        False,
                        f"{function_name}{args!r} returned {actual!r}, expected {expected!r}",
                    )
        except Exception as exc:  # noqa: BLE001 - task verifier reports candidate errors
            return VerifyResult(False, f"cannot test {rel}:{function_name}: {type(exc).__name__}: {exc}")
        return VerifyResult(True, f"{rel}:{function_name} passed all cases")

    return _verify


# 352. Count only the structured level field; message text contains decoys.
_TASK_352_LOG = """\
2026-07-01T10:00:00Z level=INFO component=api message="started"
2026-07-01T10:00:01Z level=ERROR component=db message="timeout"
2026-07-01T10:00:02Z level=WARNING component=api message="previous level=ERROR recovered"
2026-07-01T10:00:03Z level=INFO component=worker message="ERROR is only text"
2026-07-01T10:00:04Z level=ERROR component=api message="bad request"
2026-07-01T10:00:05Z level=DEBUG component=worker message="trace"
2026-07-01T10:00:06Z component=api message="missing level=ERROR only in message"
"""
_TASK_352_EXPECTED = {
    "overall": {"DEBUG": 1, "INFO": 2, "WARNING": 1, "ERROR": 2},
    "by_component": {
        "api": {"DEBUG": 0, "INFO": 1, "WARNING": 1, "ERROR": 1},
        "db": {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 1},
        "worker": {"DEBUG": 1, "INFO": 1, "WARNING": 0, "ERROR": 0},
    },
    "first_error_at": "2026-07-01T10:00:01Z",
    "malformed_lines": 1,
}

TASK_352 = Task(
    id="task_352_structured_log_summary",
    name="Count structured log levels",
    tags=("tbench-lite", "logs", "json", "easy"),
    prompt=(
        "Прочитай logs/app.log и создай summary.json. Валидная строка обязана иметь структурированные "
        "поля level и component до message; слова level=ERROR/ERROR внутри message игнорируй. Формат: "
        "overall с количествами DEBUG,INFO,WARNING,ERROR; by_component с компонентами по алфавиту, "
        "для каждой все четыре уровня включая нули; first_error_at — timestamp самой ранней валидной "
        "ERROR или null; malformed_lines — число строк без структурированного level или component. "
        "В overall и by_component malformed не учитывай."
    ),
    setup_files={"logs/app.log": _TASK_352_LOG},
    gold_files={"summary.json": json.dumps(_TASK_352_EXPECTED, indent=2) + "\n"},
    verifier=_json_equals("summary.json", _TASK_352_EXPECTED),
)


# 353. Reconstruct repeated sessions from unordered events using FIFO pairing.
_TASK_353_EVENTS = """\
{"ts":"2026-07-02T10:03:00Z","session":"s1","type":"finish"}
{"ts":"2026-07-02T10:01:00Z","session":"s1","type":"start"}
{"ts":"2026-07-02T10:00:00Z","session":"s2","type":"finish"}
{"ts":"2026-07-02T10:04:30Z","session":"s3","type":"finish"}
{"ts":"2026-07-02T10:00:00Z","session":"s1","type":"start"}
{"ts":"2026-07-02T10:01:00Z","session":"s2","type":"start"}
{"ts":"2026-07-02T10:02:00Z","session":"s1","type":"finish"}
{"ts":"2026-07-02T10:04:00Z","session":"s3","type":"start"}
"""
_TASK_353_ROWS = [
    {"session": "s1", "cycle": "1", "start": "2026-07-02T10:00:00Z", "end": "2026-07-02T10:02:00Z", "duration_seconds": "120", "status": "complete"},
    {"session": "s1", "cycle": "2", "start": "2026-07-02T10:01:00Z", "end": "2026-07-02T10:03:00Z", "duration_seconds": "120", "status": "complete"},
    {"session": "s2", "cycle": "1", "start": "", "end": "2026-07-02T10:00:00Z", "duration_seconds": "", "status": "missing_start"},
    {"session": "s2", "cycle": "2", "start": "2026-07-02T10:01:00Z", "end": "", "duration_seconds": "", "status": "missing_end"},
    {"session": "s3", "cycle": "1", "start": "2026-07-02T10:04:00Z", "end": "2026-07-02T10:04:30Z", "duration_seconds": "30", "status": "complete"},
]

TASK_353 = Task(
    id="task_353_reconstruct_sessions",
    name="Reconstruct sessions from unordered events",
    tags=("tbench-lite", "jsonl", "csv", "medium"),
    prompt=(
        "В events.jsonl события расположены не по времени, а один session может иметь несколько "
        "перекрывающихся циклов. Создай sessions.csv с заголовком "
        "session,cycle,start,end,duration_seconds,status. Сначала сортируй события каждого session "
        "по ts. Каждый finish сопоставляй с самым ранним ещё не сопоставленным start (FIFO). Finish "
        "без доступного start образует missing_start; оставшиеся start образуют missing_end. Для "
        "полной пары вычисли длительность в секундах и status=complete. Пустые значения оставляй "
        "пустыми. Строки сортируй по session, затем по времени имеющегося start или end; cycle — "
        "номер строки внутри session начиная с 1."
    ),
    setup_files={"events.jsonl": _TASK_353_EVENTS},
    gold_files={
        "sessions.csv": (
            "session,cycle,start,end,duration_seconds,status\n"
            "s1,1,2026-07-02T10:00:00Z,2026-07-02T10:02:00Z,120,complete\n"
            "s1,2,2026-07-02T10:01:00Z,2026-07-02T10:03:00Z,120,complete\n"
            "s2,1,,2026-07-02T10:00:00Z,,missing_start\n"
            "s2,2,2026-07-02T10:01:00Z,,,missing_end\n"
            "s3,1,2026-07-02T10:04:00Z,2026-07-02T10:04:30Z,30,complete\n"
        )
    },
    verifier=_csv_equals(
        "sessions.csv",
        ["session", "cycle", "start", "end", "duration_seconds", "status"],
        _TASK_353_ROWS,
    ),
)


# 354. Join five sources with effective dating, aggregation, reservations, and discounts.
_TASK_354_ROWS = [
    {"sku": "A-10", "name": "Adapter", "price_cents": "1125", "available": "5", "value_cents": "5625"},
    {"sku": "C-30", "name": "Cable", "price_cents": "700", "available": "3", "value_cents": "2100"},
]
_TASK_354_AUDIT = {
    "snapshot": "2026-07-10",
    "active_products": 3,
    "sellable_products": 2,
    "total_value_cents": 7725,
    "excluded": {"inactive": ["B-20"], "not_available": ["D-40"]},
}

TASK_354 = Task(
    id="task_354_catalog_join",
    name="Join catalog, prices, and stock",
    tags=("tbench-lite", "data-join", "csv", "json", "medium"),
    prompt=(
        "Объедини products.csv, prices.json, stock.csv, reservations.csv и discounts.json и создай "
        "sellable.csv с заголовком sku,name,price_cents,available,value_cents. SKU во всех источниках "
        "сопоставляются без учёта регистра. Для snapshot 2026-07-10 выбери последнюю цену с "
        "effective_at<=snapshot. Stock суммируй по складам, reservations суммируй, available = "
        "stock-reserved. Для скидки выбери запись с максимальным priority среди тех, где "
        "starts_at<=snapshot<=ends_at; при равном priority бери больший discount_bps. Итоговая цена "
        "= floor(base_price*(10000-discount_bps)/10000), без скидки discount_bps=0. Включай только "
        "active=true и available>0; value_cents=price_cents*available. Сортируй по нормализованному "
        "SKU и выводи SKU в верхнем регистре. Также создай inventory_audit.json: snapshot, число "
        "active_products, sellable_products, сумму value_cents всех строк и excluded с двумя "
        "лексикографически отсортированными массивами SKU в верхнем регистре: inactive и "
        "not_available (active, но available<=0)."
    ),
    setup_files={
        "products.csv": "sku,name,active\na-10,Adapter,true\nB-20,Battery,false\nc-30,Cable,true\nD-40,Dock,true\n",
        "prices.json": (
            '[{"sku":"A-10","price_cents":1100,"effective_at":"2026-07-01"},'
            '{"sku":"a-10","price_cents":1250,"effective_at":"2026-07-08"},'
            '{"sku":"C-30","price_cents":700,"effective_at":"2026-07-09"},'
            '{"sku":"C-30","price_cents":750,"effective_at":"2026-07-11"},'
            '{"sku":"D-40","price_cents":3000,"effective_at":"2026-07-02"}]\n'
        ),
        "stock.csv": "warehouse,sku,stock\nw1,A-10,4\nw2,a-10,3\nw1,b-20,8\nw1,C-30,5\nw1,d-40,0\n",
        "reservations.csv": "reservation,sku,quantity\nr1,a-10,2\nr2,C-30,1\nr3,c-30,1\nr4,unknown,9\n",
        "discounts.json": (
            '[{"sku":"A-10","discount_bps":500,"priority":1,"starts_at":"2026-07-01","ends_at":"2026-07-31"},'
            '{"sku":"a-10","discount_bps":1000,"priority":2,"starts_at":"2026-07-09","ends_at":"2026-07-10"},'
            '{"sku":"A-10","discount_bps":1500,"priority":2,"starts_at":"2026-07-11","ends_at":"2026-07-31"},'
            '{"sku":"C-30","discount_bps":2000,"priority":5,"starts_at":"2026-06-01","ends_at":"2026-06-30"}]\n'
        ),
    },
    gold_files={
        "sellable.csv": (
            "sku,name,price_cents,available,value_cents\n"
            "A-10,Adapter,1125,5,5625\n"
            "C-30,Cable,700,3,2100\n"
        ),
        "inventory_audit.json": json.dumps(_TASK_354_AUDIT, indent=2) + "\n",
    },
    verifier=all_of(
        _csv_equals(
            "sellable.csv",
            ["sku", "name", "price_cents", "available", "value_cents"],
            _TASK_354_ROWS,
        ),
        _json_equals("inventory_audit.json", _TASK_354_AUDIT),
    ),
)


# 355. Audit segment file names.
_TASK_355_EXPECTED = {"missing": [3, 6], "duplicates": [4], "invalid": ["segment_0007.txt", "segment_bad.json"]}

TASK_355 = Task(
    id="task_355_segment_audit",
    name="Audit numbered segment files",
    tags=("tbench-lite", "filesystem", "json", "easy"),
    prompt=(
        "Проверь имена файлов в каталоге segments и создай audit.json. Допустимые имена: "
        "segment_NNNN.json и segment_NNNN.retry.json; retry является второй копией того же номера. "
        "Ожидаемый диапазон номеров 1..7. JSON должен содержать missing — отсутствующие номера, "
        "duplicates — номера с несколькими допустимыми файлами, invalid — недопустимые имена. "
        "Числа и имена отсортируй по возрастанию."
    ),
    setup_files={
        "segments/segment_0001.json": "{}\n",
        "segments/segment_0002.json": "{}\n",
        "segments/segment_0004.json": "{}\n",
        "segments/segment_0004.retry.json": "{}\n",
        "segments/segment_0005.json": "{}\n",
        "segments/segment_0007.json": "{}\n",
        "segments/segment_bad.json": "{}\n",
        "segments/segment_0007.txt": "{}\n",
    },
    gold_files={"audit.json": json.dumps(_TASK_355_EXPECTED, ensure_ascii=False) + "\n"},
    verifier=_json_equals("audit.json", _TASK_355_EXPECTED),
)


# 356. Deduplicate contacts using explicit precedence rules.
_TASK_356_EXPECTED = [
    {"canonical_id": "u1", "email": "anna.alt@example.com", "phone": "+3725550199", "name": "A. K.", "source_ids": ["u1", "u10", "u11", "u9"]},
    {"canonical_id": "u2", "email": "", "phone": "+3725550102", "name": "Boris", "source_ids": ["u2", "u7"]},
]

TASK_356 = Task(
    id="task_356_deduplicate_contacts",
    name="Deduplicate contact records",
    tags=("tbench-lite", "normalization", "json", "medium"),
    prompt=(
        "Объедини записи contacts.json в contacts_clean.json. Email нормализуй strip+lower. Телефон "
        "нормализуй, оставив ведущий + и только цифры. Две записи связаны, если у них совпадает "
        "непустой нормализованный email ИЛИ непустой нормализованный phone; группы — транзитивные "
        "компоненты этой связи (мост через третью запись тоже объединяет группы). canonical_id — "
        "лексикографически минимальный id группы. Для name, email и phone независимо бери самое новое "
        "по updated_at непустое нормализованное значение; при равном updated_at побеждает запись с "
        "лексикографически меньшим id. source_ids отсортируй. Итоговый массив по canonical_id."
    ),
    setup_files={
        "contacts.json": (
            '[{"id":"u1","email":" Anna@Example.COM ","phone":"+372 555 0101","name":"Anna","updated_at":"2026-07-01"},'
            '{"id":"u9","email":"anna@example.com","phone":"","name":"Anna K.","updated_at":"2026-07-05"},'
            '{"id":"u10","email":"anna.alt@example.com","phone":"+372 555 0101","name":"","updated_at":"2026-07-07"},'
            '{"id":"u11","email":"ANNA.ALT@example.com","phone":"+372 555 0199","name":"A. K.","updated_at":"2026-07-08"},'
            '{"id":"u2","email":"","phone":"+372-555-0102","name":"Boris","updated_at":"2026-07-03"},'
            '{"id":"u7","email":"","phone":"+372 555 0102","name":"","updated_at":"2026-07-06"}]\n'
        )
    },
    gold_files={"contacts_clean.json": json.dumps(_TASK_356_EXPECTED, ensure_ascii=False, indent=2) + "\n"},
    verifier=_json_equals("contacts_clean.json", _TASK_356_EXPECTED),
)


# 357. Reconstruct retried requests and compute terminal latency.
_TASK_357_ROWS = [
    {"request_id": "r1", "endpoint": "/search", "attempts": "2", "duration_ms": "250", "status": "complete"},
    {"request_id": "r2", "endpoint": "/items", "attempts": "1", "duration_ms": "1500", "status": "complete"},
    {"request_id": "r3", "endpoint": "/search", "attempts": "2", "duration_ms": "400", "status": "failed"},
    {"request_id": "r4", "endpoint": "/export", "attempts": "0", "duration_ms": "", "status": "missing_end"},
]

TASK_357 = Task(
    id="task_357_request_latency",
    name="Join request logs and compute latency",
    tags=("tbench-lite", "logs", "csv", "medium"),
    prompt=(
        "Соедини gateway.csv и worker.csv по request_id и создай latency.csv с заголовком "
        "request_id,endpoint,attempts,duration_ms,status. В worker может быть несколько попыток; "
        "сначала сортируй их по finished_at. attempts — их общее число. Если есть result=ok, терминал "
        "— самая ранняя такая попытка, duration_ms считается от started_at до неё, status=complete "
        "(более поздние записи всё равно входят в attempts). Если ok нет, но попытки есть, терминал — "
        "последняя попытка, duration_ms считается до неё, status=failed. Если попыток нет, "
        "duration_ms пустой и status=missing_end. Длительность — целые миллисекунды; строки по request_id."
    ),
    setup_files={
        "gateway.csv": (
            "request_id,endpoint,started_at\n"
            "r2,/items,2026-07-03T10:00:01.000Z\n"
            "r1,/search,2026-07-03T10:00:00.000Z\n"
            "r3,/search,2026-07-03T10:00:03.000Z\n"
            "r4,/export,2026-07-03T10:00:04.000Z\n"
        ),
        "worker.csv": (
            "request_id,attempt,finished_at,result\n"
            "r1,2,2026-07-03T10:00:00.250Z,ok\n"
            "r3,2,2026-07-03T10:00:03.400Z,error\n"
            "r1,1,2026-07-03T10:00:00.100Z,error\n"
            "r2,1,2026-07-03T10:00:02.500Z,ok\n"
            "r3,1,2026-07-03T10:00:03.200Z,error\n"
        ),
    },
    gold_files={
        "latency.csv": (
            "request_id,endpoint,attempts,duration_ms,status\n"
            "r1,/search,2,250,complete\n"
            "r2,/items,1,1500,complete\n"
            "r3,/search,2,400,failed\n"
            "r4,/export,0,,missing_end\n"
        )
    },
    verifier=_csv_equals(
        "latency.csv",
        ["request_id", "endpoint", "attempts", "duration_ms", "status"],
        _TASK_357_ROWS,
    ),
)


# 358. Implement ordered range-set expressions with stride and exclusions.
_TASK_358_GOLD = '''from __future__ import annotations

import re

_TOKEN = re.compile(r"^(!)?(-?\\d+)(?:-(-?\\d+)(?:/(\\d+))?)?$")


def parse_ranges(spec: str) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for raw in spec.split(","):
        token = raw.strip()
        match = _TOKEN.fullmatch(token)
        if match is None:
            raise ValueError(f"invalid token: {token}")
        exclude = match.group(1) is not None
        start = int(match.group(2))
        end_text = match.group(3)
        stride_text = match.group(4)
        values = [start]
        if end_text is not None:
            end = int(end_text)
            stride = int(stride_text or "1")
            if stride <= 0:
                raise ValueError("stride must be positive")
            step = stride if end >= start else -stride
            values = list(range(start, end + step, step))
            if values and ((step > 0 and values[-1] > end) or (step < 0 and values[-1] < end)):
                values.pop()
        elif stride_text is not None:
            raise ValueError("stride requires a range")
        for value in values:
            if exclude:
                if value in seen:
                    seen.remove(value)
                    result.remove(value)
            elif value not in seen:
                seen.add(value)
                result.append(value)
    return result
'''

TASK_358 = Task(
    id="task_358_range_parser",
    name="Implement a robust integer range parser",
    tags=("tbench-lite", "python", "parsing", "medium"),
    prompt=(
        "В ranges.py реализуй parse_ranges(spec: str) -> list[int]. Токены разделены запятыми: целое, "
        "диапазон a-b, диапазон с положительным шагом a-b/step либо любой из них с префиксом !. "
        "Поддерживай убывание и отрицательные числа (-3--1/2). Диапазон включает достижимые значения "
        "от a в направлении b, не перешагивая b. Обычный токен добавляет ещё отсутствующие значения "
        "в конец результата, ! удаляет совпавшие значения из текущего результата; удалённое значение "
        "можно добавить снова более поздним токеном. Пробелы допустимы только вокруг целого токена. "
        "Порядок результата — порядок текущего первого появления, повторов нет. Пустой токен, нулевой/"
        "отрицательный step, step у одиночного числа и любой некорректный токен вызывают ValueError."
    ),
    setup_files={"ranges.py": "def parse_ranges(spec: str) -> list[int]:\n    raise NotImplementedError\n"},
    gold_files={"ranges.py": _TASK_358_GOLD},
    verifier=_verify_function(
        "ranges.py",
        "parse_ranges",
        [
            (("1,3-5,8-6,3",), [1, 3, 4, 5, 8, 7, 6]),
            (("-3--1,2",), [-3, -2, -1, 2]),
            ((" 2 , 2-4 , 3 ",), [2, 3, 4]),
            (("1-10/3,!4-8/3,4",), [1, 10, 4]),
            (("5-1/2,!3,3",), [5, 1, 3]),
        ],
    ),
)


# 359. Deterministic retry delays.
_TASK_359_GOLD = '''def retry_delays(attempts: int, base: int = 2, cap: int = 30) -> list[int]:
    if attempts < 0 or base <= 0 or cap <= 0:
        raise ValueError("attempts must be non-negative; base and cap must be positive")
    return [min(base * (2 ** index), cap) for index in range(attempts)]
'''

TASK_359 = Task(
    id="task_359_retry_schedule",
    name="Fix deterministic exponential retry delays",
    tags=("tbench-lite", "python", "debugging", "easy"),
    prompt=(
        "Исправь retry.py. Функция retry_delays(attempts, base=2, cap=30) должна вернуть список "
        "задержек для attempts попыток: base, base*2, base*4 и так далее, каждое значение ограничено "
        "cap. attempts=0 возвращает []. При attempts<0, base<=0 или cap<=0 вызывай ValueError. "
        "Никакого jitter и sleep."
    ),
    setup_files={
        "retry.py": (
            "def retry_delays(attempts: int, base: int = 2, cap: int = 30) -> list[int]:\n"
            "    return [base * attempt for attempt in range(attempts)]\n"
        )
    },
    gold_files={"retry.py": _TASK_359_GOLD},
    verifier=_verify_function(
        "retry.py",
        "retry_delays",
        [
            ((0,), []),
            ((5,), [2, 4, 8, 16, 30]),
            ((4, 3, 10), [3, 6, 10, 10]),
        ],
    ),
)


# 360. Reject absolute and traversal paths in both slash conventions.
_TASK_360_GOLD = '''from __future__ import annotations

import re
from pathlib import PurePosixPath

_DRIVE = re.compile(r"^[A-Za-z]:")


def safe_relative_path(name: str) -> str:
    normalized = name.replace("\\\\", "/")
    if not normalized or normalized.startswith("/") or _DRIVE.match(normalized):
        raise ValueError("unsafe path")
    raw_parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in raw_parts):
        raise ValueError("unsafe path")
    parts = PurePosixPath(normalized).parts
    return "/".join(parts)
'''


def _verify_safe_path(ws: Path) -> VerifyResult:
    path = ws / "safe_path.py"
    if not path.is_file():
        return VerifyResult(False, "safe_path.py missing")
    try:
        module = _load_module(path)
        function = module.safe_relative_path
        valid = {
            "data/file.txt": "data/file.txt",
            "nested\\file.txt": "nested/file.txt",
            "one.txt": "one.txt",
        }
        for value, expected in valid.items():
            if function(value) != expected:
                return VerifyResult(False, f"safe_relative_path({value!r}) returned wrong value")
        invalid = ["../secret", "a/../secret", "/etc/passwd", "C:\\temp\\x", "", "./x"]
        for value in invalid:
            try:
                function(value)
            except ValueError:
                continue
            return VerifyResult(False, f"unsafe path {value!r} was accepted")
    except Exception as exc:  # noqa: BLE001
        return VerifyResult(False, f"safe_path.py failed: {type(exc).__name__}: {exc}")
    return VerifyResult(True, "safe_relative_path passed all cases")


TASK_360 = Task(
    id="task_360_safe_relative_path",
    name="Implement safe relative path normalization",
    tags=("tbench-lite", "python", "security", "medium"),
    prompt=(
        "В safe_path.py реализуй safe_relative_path(name: str) -> str. Принимай только непустой "
        "относительный путь без компонентов . и ... Обрабатывай / и Windows-разделитель обратный "
        "слеш одинаково и возвращай нормализованный путь с /. Отклоняй ValueError абсолютные POSIX-"
        "пути, Windows drive paths вроде C:\\temp\\x и любые traversal-пути."
    ),
    setup_files={"safe_path.py": "def safe_relative_path(name: str) -> str:\n    return name\n"},
    gold_files={"safe_path.py": _TASK_360_GOLD},
    verifier=_verify_safe_path,
)


# 361. A small executable JSONL cleaner.
_TASK_361_SCRIPT = '''from __future__ import annotations

import csv
import json
import sys


def main() -> None:
    source, clean_path, errors_path = sys.argv[1:4]
    latest = {}
    with open(source, "r", encoding="utf-8") as src, open(errors_path, "w", encoding="utf-8", newline="") as errors:
        writer = csv.writer(errors)
        writer.writerow(["line", "error"])
        for number, raw in enumerate(src, 1):
            try:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("not_object")
                if "id" not in value:
                    raise ValueError("missing_id")
                if not isinstance(value["id"], int) or isinstance(value["id"], bool) or value["id"] <= 0:
                    raise ValueError("invalid_id")
                if not isinstance(value.get("name"), str) or not value["name"].strip():
                    raise ValueError("invalid_name")
            except json.JSONDecodeError:
                writer.writerow([number, "invalid_json"])
                continue
            except ValueError as exc:
                writer.writerow([number, str(exc)])
                continue
            latest[value["id"]] = value
    with open(clean_path, "w", encoding="utf-8") as clean:
        for record_id in sorted(latest):
            clean.write(json.dumps(latest[record_id], ensure_ascii=False, separators=(",", ":")) + "\\n")


if __name__ == "__main__":
    main()
'''


def _verify_jsonl_cleaner(ws: Path) -> VerifyResult:
    script = ws / "clean_jsonl.py"
    if not script.is_file():
        return VerifyResult(False, "clean_jsonl.py missing")
    clean = ws / "_verified_clean.jsonl"
    errors = ws / "_verified_errors.csv"
    result = subprocess.run(
        [sys.executable, str(script), "records.jsonl", clean.name, errors.name],
        cwd=ws,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        return VerifyResult(False, f"clean_jsonl.py failed: {result.stderr.strip()[:400]}")
    expected_clean = '{"id":1,"name":"A2"}\n{"id":2,"name":"Б"}\n'
    if clean.read_text(encoding="utf-8") != expected_clean:
        return VerifyResult(False, "clean output is wrong")
    expected_errors = [
        {"line": "2", "error": "invalid_json"},
        {"line": "4", "error": "missing_id"},
        {"line": "5", "error": "not_object"},
        {"line": "7", "error": "invalid_id"},
        {"line": "8", "error": "invalid_name"},
    ]
    with errors.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if reader.fieldnames != ["line", "error"] or rows != expected_errors:
            return VerifyResult(False, f"errors CSV is wrong: {rows!r}")
    return VerifyResult(True, "JSONL cleaner produced the expected outputs")


TASK_361 = Task(
    id="task_361_jsonl_cleaner",
    name="Implement a streaming JSONL cleaner",
    tags=("tbench-lite", "python", "jsonl", "csv", "medium"),
    prompt=(
        "Реализуй clean_jsonl.py как CLI: python clean_jsonl.py INPUT CLEAN_OUTPUT ERRORS_CSV. Читай "
        "INPUT построчно. Валидна JSON-строка с объектом, где id — положительный int (bool нельзя), "
        "а name — непустая после strip строка. Среди повторов id оставляй последнюю валидную запись, "
        "затем записывай объекты в CLEAN_OUTPUT по id по возрастанию, компактный JSON по одному на строку. "
        "В ERRORS_CSV создай заголовок line,error и для ошибок пиши номер строки и один из кодов "
        "invalid_json, not_object, missing_id, invalid_id, invalid_name. Невалидный повтор не удаляет "
        "предыдущую валидную запись. Используй UTF-8."
    ),
    setup_files={
        "clean_jsonl.py": "raise NotImplementedError\n",
        "records.jsonl": (
            '{"id":1,"name":"A"}\n'
            '{bad json}\n'
            '{"id":2,"name":"Б"}\n'
            '{"name":"without id"}\n'
            '[1,2,3]\n'
            '{"id":1,"name":"A2"}\n'
            '{"id":0,"name":"zero"}\n'
            '{"id":3,"name":"  "}\n'
        ),
    },
    gold_files={"clean_jsonl.py": _TASK_361_SCRIPT},
    verifier=_verify_jsonl_cleaner,
)


# 362. Stable topological ordering.
_TASK_362_GOLD = '''from __future__ import annotations

import heapq


def dependency_order(graph: dict[str, list[str]], priorities: dict[str, int] | None = None) -> list[str]:
    priorities = priorities or {}
    nodes = set(graph)
    normalized = {}
    for node, dependencies in graph.items():
        required = {dep for dep in dependencies if not dep.startswith("?")}
        optional = {dep[1:] for dep in dependencies if dep.startswith("?") and dep[1:] in nodes}
        unknown = required - nodes
        if unknown:
            raise KeyError(sorted(unknown)[0])
        normalized[node] = required | optional
    dependents = {node: [] for node in nodes}
    indegree = {node: len(normalized[node]) for node in nodes}
    for node, dependencies in normalized.items():
        for dependency in dependencies:
            dependents[dependency].append(node)
    ready = [(priorities.get(node, 0), node) for node, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        _, node = heapq.heappop(ready)
        result.append(node)
        for dependent in sorted(dependents[node]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, (priorities.get(dependent, 0), dependent))
    if len(result) != len(nodes):
        raise ValueError("cycle")
    return result
'''


def _verify_toposort(ws: Path) -> VerifyResult:
    path = ws / "deps.py"
    if not path.is_file():
        return VerifyResult(False, "deps.py missing")
    try:
        function = _load_module(path).dependency_order
        cases = [
            ({"app": ["core", "ui"], "ui": ["core"], "core": []}, None, ["core", "ui", "app"]),
            ({"b": [], "a": [], "c": ["a"]}, {"b": -1, "a": 5}, ["b", "a", "c"]),
            ({"deploy": ["build", "?docs", "?missing"], "build": [], "docs": []}, {"docs": 10}, ["build", "docs", "deploy"]),
        ]
        for graph, priorities, expected in cases:
            actual = function(graph, priorities)
            if actual != expected:
                return VerifyResult(False, f"wrong order for {graph!r}: {actual!r}")
        try:
            function({"a": ["missing"]})
        except KeyError:
            pass
        else:
            return VerifyResult(False, "unknown dependency did not raise KeyError")
        try:
            function({"a": ["b"], "b": ["a"]})
        except ValueError:
            pass
        else:
            return VerifyResult(False, "cycle did not raise ValueError")
    except Exception as exc:  # noqa: BLE001
        return VerifyResult(False, f"deps.py failed: {type(exc).__name__}: {exc}")
    return VerifyResult(True, "dependency_order passed all cases")


TASK_362 = Task(
    id="task_362_dependency_order",
    name="Implement stable dependency ordering",
    tags=("tbench-lite", "python", "graph", "medium"),
    prompt=(
        "В deps.py реализуй dependency_order(graph, priorities=None) -> list[str]. Каждый ключ graph — "
        "узел, значения — зависимости. Обычная неизвестная зависимость вызывает KeyError. Зависимость "
        "с префиксом ? является optional: если такой узел есть, зависимость соблюдается, иначе "
        "игнорируется. Зависимости идут раньше зависимого. Среди готовых узлов выбирай минимальную "
        "целую priorities[node] (по умолчанию 0), при равенстве — лексикографически минимальный узел. "
        "Цикл вызывает ValueError. Ни graph, ни priorities не изменяй."
    ),
    setup_files={"deps.py": "def dependency_order(graph):\n    return list(graph)\n"},
    gold_files={"deps.py": _TASK_362_GOLD},
    verifier=_verify_toposort,
)


# 363. Merge four config layers and record leaf provenance.
_TASK_363_RESOLVED = {
    "host": "api.internal",
    "port": 9090,
    "features": {"audit": True, "search": False},
    "labels": [],
    "database": {"host": "", "pool": {"min": 2, "max": 20}},
}
_TASK_363_PROVENANCE = {
    "host": "environment",
    "port": "cli",
    "features.audit": "user",
    "features.search": "cli",
    "labels": "cli",
    "database.host": "cli",
    "database.pool.min": "user",
    "database.pool.max": "environment",
}

TASK_363 = Task(
    id="task_363_config_precedence",
    name="Resolve layered configuration with provenance",
    tags=("tbench-lite", "config", "json", "medium"),
    prompt=(
        "Создай resolved.json и provenance.json из defaults.json, user.json, environment.json и "
        "cli.json. Приоритет CLI > environment > user > defaults. Словари объединяются рекурсивно; "
        "списки заменяются целиком; null удаляет ключ; пустая строка и пустой список являются обычными "
        "значениями. provenance.json должен быть плоским объектом: ключ — полный dotted path каждого "
        "leaf из итогового resolved.json, значение — ровно имя слоя без .json: defaults, user, "
        "environment или cli. Ключи SECRET_* не должны попасть ни в один результат."
    ),
    setup_files={
        "defaults.json": (
            '{"host":"localhost","port":8000,"features":{"audit":false,"search":true},'
            '"labels":["base"],"database":{"host":"db.local","pool":{"min":1,"max":5}},'
            '"obsolete":{"enabled":true},"SECRET_TOKEN":"x"}\n'
        ),
        "user.json": (
            '{"features":{"audit":true},"labels":["user"],"unused":"remove-me",'
            '"database":{"pool":{"min":2}}}\n'
        ),
        "environment.json": (
            '{"host":"api.internal","unused":null,"obsolete":null,'
            '"features":{"search":null},"database":{"pool":{"max":20}}}\n'
        ),
        "cli.json": (
            '{"port":9090,"features":{"search":false},"labels":[],"database":{"host":""}}\n'
        ),
    },
    gold_files={
        "resolved.json": json.dumps(_TASK_363_RESOLVED, ensure_ascii=False, indent=2) + "\n",
        "provenance.json": json.dumps(_TASK_363_PROVENANCE, ensure_ascii=False, indent=2) + "\n",
    },
    verifier=all_of(
        _json_equals("resolved.json", _TASK_363_RESOLVED),
        _json_equals("provenance.json", _TASK_363_PROVENANCE),
    ),
)


# 364. Resolve multiple conflict hunks using per-hunk manifest policies.
TASK_364 = Task(
    id="task_364_manifest_conflicts",
    name="Resolve conflict markers by policy manifest",
    tags=("tbench-lite", "vcs", "filesystem", "easy"),
    prompt=(
        "В conflict-policy.json для каждого конфликтного файла дан массив правил по порядку hunks. "
        "Число правил равно числу конфликтных блоков. Разреши каждый блок своим правилом: "
        "ours — оставить блок между <<<<<<< ours и =======; theirs — блок между ======= и >>>>>>> theirs; "
        "combine — оставить сначала ours, затем theirs, удаляя повторные одинаковые строки внутри "
        "этого блока и сохраняя первое появление. Удали все маркеры. Остальной текст и порядок строк сохрани."
    ),
    setup_files={
        "conflict-policy.json": (
            '{"src/settings.py":["ours","theirs"],'
            '"docs/api.md":["theirs","combine"],'
            '"data/roles.txt":["combine","ours"]}\n'
        ),
        "src/settings.py": (
            "MODE = 'prod'\n<<<<<<< ours\nTIMEOUT = 30\n=======\nTIMEOUT = 90\n>>>>>>> theirs\n"
            "RETRIES = 3\n<<<<<<< ours\nCACHE = False\n=======\nCACHE = True\n>>>>>>> theirs\n"
        ),
        "docs/api.md": (
            "# API\n<<<<<<< ours\nOld endpoint: /v1\n=======\nCurrent endpoint: /v2\n>>>>>>> theirs\n"
            "Auth methods:\n<<<<<<< ours\nbearer\noauth\n=======\noauth\nmtls\n>>>>>>> theirs\n"
        ),
        "data/roles.txt": (
            "roles:\n<<<<<<< ours\nreader\nadmin\n=======\nwriter\nadmin\n>>>>>>> theirs\n"
            "scopes:\n<<<<<<< ours\nread\nwrite\n=======\nwrite\nadmin\n>>>>>>> theirs\n"
        ),
    },
    gold_files={
        "src/settings.py": "MODE = 'prod'\nTIMEOUT = 30\nRETRIES = 3\nCACHE = True\n",
        "docs/api.md": "# API\nCurrent endpoint: /v2\nAuth methods:\nbearer\noauth\nmtls\n",
        "data/roles.txt": "roles:\nreader\nadmin\nwriter\nscopes:\nread\nwrite\n",
    },
    verifier=all_of(
        _text_equals("src/settings.py", "MODE = 'prod'\nTIMEOUT = 30\nRETRIES = 3\nCACHE = True\n"),
        _text_equals("docs/api.md", "# API\nCurrent endpoint: /v2\nAuth methods:\nbearer\noauth\nmtls\n"),
        _text_equals("data/roles.txt", "roles:\nreader\nadmin\nwriter\nscopes:\nread\nwrite\n"),
        file_not_contains("src/settings.py", "<<<<<<<", "=======", ">>>>>>>"),
        file_not_contains("docs/api.md", "<<<<<<<", "=======", ">>>>>>>"),
        file_not_contains("data/roles.txt", "<<<<<<<", "=======", ">>>>>>>"),
    ),
)


# 365. Rename a package in code/config/docs while preserving explicit legacy fixtures.
TASK_365 = Task(
    id="task_365_package_rename",
    name="Rename a Python package with exclusions",
    tags=("tbench-lite", "refactor", "python", "medium"),
    prompt=(
        "Переименуй Python-пакет blueledger в greenledger. Переименуй каталог, обнови imports в app.py "
        "и tests, абсолютные imports внутри пакета и plugin, оба entry points в pyproject.toml, "
        "текущие команды в README.md, docs/current.md, Dockerfile и .github/workflows/test.yml. "
        "Не изменяй CHANGELOG.md, tests/data/legacy_blueledger.json и migrations/legacy_blueledger.sql: "
        "там старое имя является историческими данными. После работы каталога blueledger быть не должно."
    ),
    setup_files={
        "blueledger/__init__.py": "from .api import total\n",
        "blueledger/api.py": "def total(values):\n    return sum(values)\n",
        "blueledger/client.py": "from blueledger.api import total\n\ndef compute(values):\n    return total(values)\n",
        "blueledger/plugins/__init__.py": "",
        "blueledger/plugins/report.py": "from blueledger.client import compute\n",
        "app.py": "from blueledger.api import total\nprint(total([1, 2, 3]))\n",
        "tests/test_api.py": "from blueledger.api import total\n\ndef test_total():\n    assert total([2, 3]) == 5\n",
        "tests/test_client.py": "from blueledger.client import compute\n\ndef test_compute():\n    assert compute([4]) == 4\n",
        "pyproject.toml": (
            '[project]\nname = "demo"\n[project.scripts]\nledger = "blueledger.api:total"\n'
            '[project.entry-points."demo.plugins"]\nreport = "blueledger.plugins.report:compute"\n'
        ),
        "README.md": "Use blueledger for totals.\nRun: python -m blueledger.client\n",
        "docs/current.md": "API module: `blueledger.api`; plugin: `blueledger.plugins.report`.\n",
        "Dockerfile": 'CMD ["python", "-m", "blueledger.client"]\n',
        ".github/workflows/test.yml": "steps:\n  - run: python -m pytest tests && python -m blueledger.client\n",
        "CHANGELOG.md": "1.0: blueledger initial release.\n",
        "tests/data/legacy_blueledger.json": '{"package":"blueledger"}\n',
        "migrations/legacy_blueledger.sql": "-- archive table blueledger_events\n",
    },
    gold_files={
        "blueledger/__init__.py": None,
        "blueledger/api.py": None,
        "blueledger/client.py": None,
        "blueledger/plugins/__init__.py": None,
        "blueledger/plugins/report.py": None,
        "greenledger/__init__.py": "from .api import total\n",
        "greenledger/api.py": "def total(values):\n    return sum(values)\n",
        "greenledger/client.py": "from greenledger.api import total\n\ndef compute(values):\n    return total(values)\n",
        "greenledger/plugins/__init__.py": "",
        "greenledger/plugins/report.py": "from greenledger.client import compute\n",
        "app.py": "from greenledger.api import total\nprint(total([1, 2, 3]))\n",
        "tests/test_api.py": "from greenledger.api import total\n\ndef test_total():\n    assert total([2, 3]) == 5\n",
        "tests/test_client.py": "from greenledger.client import compute\n\ndef test_compute():\n    assert compute([4]) == 4\n",
        "pyproject.toml": (
            '[project]\nname = "demo"\n[project.scripts]\nledger = "greenledger.api:total"\n'
            '[project.entry-points."demo.plugins"]\nreport = "greenledger.plugins.report:compute"\n'
        ),
        "README.md": "Use greenledger for totals.\nRun: python -m greenledger.client\n",
        "docs/current.md": "API module: `greenledger.api`; plugin: `greenledger.plugins.report`.\n",
        "Dockerfile": 'CMD ["python", "-m", "greenledger.client"]\n',
        ".github/workflows/test.yml": "steps:\n  - run: python -m pytest tests && python -m greenledger.client\n",
    },
    verifier=all_of(
        file_does_not_exist("blueledger"),
        file_exists("greenledger/__init__.py"),
        _text_equals("greenledger/api.py", "def total(values):\n    return sum(values)\n"),
        _text_equals("greenledger/client.py", "from greenledger.api import total\n\ndef compute(values):\n    return total(values)\n"),
        _text_equals("greenledger/plugins/__init__.py", ""),
        _text_equals("greenledger/plugins/report.py", "from greenledger.client import compute\n"),
        _text_equals("app.py", "from greenledger.api import total\nprint(total([1, 2, 3]))\n"),
        _text_equals("tests/test_api.py", "from greenledger.api import total\n\ndef test_total():\n    assert total([2, 3]) == 5\n"),
        _text_equals("tests/test_client.py", "from greenledger.client import compute\n\ndef test_compute():\n    assert compute([4]) == 4\n"),
        _text_equals("pyproject.toml", '[project]\nname = "demo"\n[project.scripts]\nledger = "greenledger.api:total"\n[project.entry-points."demo.plugins"]\nreport = "greenledger.plugins.report:compute"\n'),
        _text_equals("README.md", "Use greenledger for totals.\nRun: python -m greenledger.client\n"),
        _text_equals("docs/current.md", "API module: `greenledger.api`; plugin: `greenledger.plugins.report`.\n"),
        _text_equals("Dockerfile", 'CMD ["python", "-m", "greenledger.client"]\n'),
        _text_equals(".github/workflows/test.yml", "steps:\n  - run: python -m pytest tests && python -m greenledger.client\n"),
        _text_equals("CHANGELOG.md", "1.0: blueledger initial release.\n"),
        _text_equals("tests/data/legacy_blueledger.json", '{"package":"blueledger"}\n'),
        _text_equals("migrations/legacy_blueledger.sql", "-- archive table blueledger_events\n"),
    ),
)


# 366. Create a deterministic checksum manifest.
_TASK_366_FILES = {
    "data/a.txt": "alpha\n",
    "data/b.txt": "beta\n",
    "data/nested/c.json": '{"value":3}\n',
    "data/nested/empty.bin": "",
    "data/.cache/temp.bin": "ignore me\n",
}
_TASK_366_INCLUDED = [
    {
        "path": rel.removeprefix("data/"),
        "size": len(content.encode("utf-8")),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
    for rel, content in _TASK_366_FILES.items()
    if "/.cache/" not in f"/{rel}"
]
_TASK_366_INCLUDED.sort(key=lambda item: item["path"])
_TASK_366_TREE_INPUT = "".join(
    f"{item['path']}\0{item['size']}\0{item['sha256']}\n" for item in _TASK_366_INCLUDED
)
_TASK_366_EXPECTED = {
    "algorithm": "sha256",
    "files": _TASK_366_INCLUDED,
    "total_bytes": sum(item["size"] for item in _TASK_366_INCLUDED),
    "tree_sha256": hashlib.sha256(_TASK_366_TREE_INPUT.encode("utf-8")).hexdigest(),
}

TASK_366 = Task(
    id="task_366_checksum_manifest",
    name="Build a deterministic SHA-256 manifest",
    tags=("tbench-lite", "filesystem", "hashing", "json", "easy"),
    prompt=(
        "Создай data/manifest.json. Рекурсивно включи все обычные файлы внутри data, включая пустые, "
        "кроме самого manifest.json и всего data/.cache. Формат: algorithm='sha256'; files — массив "
        "по POSIX path относительно data лексикографически, каждый объект содержит path, size в байтах "
        "и нижний hex sha256; total_bytes — сумма size. tree_sha256 — SHA-256 UTF-8 строки, полученной "
        "конкатенацией для каждого элемента files строки path + NUL + decimal size + NUL + sha256 + LF. "
        "NUL означает один байт 0x00, LF — 0x0a."
    ),
    setup_files=_TASK_366_FILES,
    gold_files={"data/manifest.json": json.dumps(_TASK_366_EXPECTED, indent=2, sort_keys=True) + "\n"},
    verifier=_json_equals("data/manifest.json", _TASK_366_EXPECTED),
)


# 367. Reconcile invoices against potentially multiple payments.
_TASK_367_ROWS = [
    {"invoice_id": "i1", "amount_cents": "1000", "paid_cents": "1000", "balance_cents": "0", "status": "paid"},
    {"invoice_id": "i2", "amount_cents": "1500", "paid_cents": "500", "balance_cents": "1000", "status": "partial"},
    {"invoice_id": "i3", "amount_cents": "700", "paid_cents": "900", "balance_cents": "-200", "status": "overpaid"},
    {"invoice_id": "i4", "amount_cents": "400", "paid_cents": "0", "balance_cents": "400", "status": "unpaid"},
]

TASK_367 = Task(
    id="task_367_invoice_reconciliation",
    name="Reconcile invoices and payments",
    tags=("tbench-lite", "data-join", "csv", "medium"),
    prompt=(
        "Объедини invoices.csv и payments.csv и создай reconciliation.csv с заголовком "
        "invoice_id,amount_cents,paid_cents,balance_cents,status. Суммируй все payments для invoice_id. "
        "balance=amount-paid. status: paid при balance=0, partial при 0<paid<amount, overpaid при "
        "paid>amount, unpaid при paid=0. Порядок строк по invoice_id. Платёж для неизвестного invoice "
        "игнорируй."
    ),
    setup_files={
        "invoices.csv": "invoice_id,amount_cents\ni1,1000\ni2,1500\ni3,700\ni4,400\n",
        "payments.csv": "payment_id,invoice_id,amount_cents\np1,i1,600\np2,i1,400\np3,i2,500\np4,i3,900\np5,unknown,999\n",
    },
    gold_files={
        "reconciliation.csv": (
            "invoice_id,amount_cents,paid_cents,balance_cents,status\n"
            "i1,1000,1000,0,paid\n"
            "i2,1500,500,1000,partial\n"
            "i3,700,900,-200,overpaid\n"
            "i4,400,0,400,unpaid\n"
        )
    },
    verifier=_csv_equals(
        "reconciliation.csv",
        ["invoice_id", "amount_cents", "paid_cents", "balance_cents", "status"],
        _TASK_367_ROWS,
    ),
)


# 368. Migrate a small SQLite database using a script accepted by path argument.
def _setup_task_368(ws: Path) -> None:
    db = sqlite3.connect(ws / "orders.db")
    try:
        db.executescript(
            """
            PRAGMA user_version = 1;
            CREATE TABLE orders(
                id INTEGER PRIMARY KEY,
                customer TEXT NOT NULL,
                total_cents INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO orders VALUES
                (1, ' Alice ', 1200, '2026-07-01'),
                (2, 'alice', 0, '2026-07-02'),
                (3, 'BOB', 500, '2026-07-03');
            """
        )
        db.commit()
    finally:
        db.close()


_TASK_368_GOLD = '''from __future__ import annotations

import sqlite3
import sys


def migrate(path: str) -> None:
    db = sqlite3.connect(path)
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version >= 2:
            return
        db.execute("ALTER TABLE orders RENAME TO orders_v1")
        db.execute("CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE)")
        db.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), total_cents INTEGER NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL)")
        rows = db.execute("SELECT id, customer, total_cents, created_at FROM orders_v1 ORDER BY id").fetchall()
        customer_ids = {}
        for order_id, customer, total_cents, created_at in rows:
            name = customer.strip().lower()
            if name not in customer_ids:
                cursor = db.execute("INSERT INTO customers(name) VALUES (?)", (name,))
                customer_ids[name] = cursor.lastrowid
            status = "paid" if total_cents > 0 else "void"
            db.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", (order_id, customer_ids[name], total_cents, created_at, status))
        db.execute("DROP TABLE orders_v1")
        db.execute("PRAGMA user_version = 2")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    migrate(sys.argv[1])
'''


def _verify_sqlite_migration(ws: Path) -> VerifyResult:
    script = ws / "migrate.py"
    source = ws / "orders.db"
    if not script.is_file() or not source.is_file():
        return VerifyResult(False, "migrate.py or orders.db missing")
    with tempfile.TemporaryDirectory(prefix="hb_sqlite_verify_") as tmp:
        copied = Path(tmp) / "orders.db"
        shutil.copy2(source, copied)
        for _ in range(2):
            result = subprocess.run(
                [sys.executable, str(script), str(copied)],
                cwd=ws,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
            if result.returncode != 0:
                return VerifyResult(False, f"migrate.py failed: {result.stderr.strip()[:400]}")
        db = sqlite3.connect(copied)
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            customers = db.execute("SELECT id, name FROM customers ORDER BY id").fetchall()
            orders = db.execute(
                "SELECT o.id, c.name, o.total_cents, o.created_at, o.status "
                "FROM orders o JOIN customers c ON c.id=o.customer_id ORDER BY o.id"
            ).fetchall()
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.Error as exc:
            return VerifyResult(False, f"migrated database schema/query failed: {exc}")
        finally:
            db.close()
    if version != 2:
        return VerifyResult(False, f"user_version is {version}, expected 2")
    if customers != [(1, "alice"), (2, "bob")]:
        return VerifyResult(False, f"customers mismatch: {customers!r}")
    expected_orders = [
        (1, "alice", 1200, "2026-07-01", "paid"),
        (2, "alice", 0, "2026-07-02", "void"),
        (3, "bob", 500, "2026-07-03", "paid"),
    ]
    if orders != expected_orders:
        return VerifyResult(False, f"orders mismatch: {orders!r}")
    if integrity != "ok":
        return VerifyResult(False, f"integrity_check returned {integrity!r}")
    return VerifyResult(True, "SQLite migration is correct and idempotent")


TASK_368 = Task(
    id="task_368_sqlite_migration",
    name="Implement an idempotent SQLite schema migration",
    tags=("tbench-lite", "sqlite", "python", "medium"),
    prompt=(
        "Создай migrate.py. Запуск python migrate.py PATH должен мигрировать SQLite-базу версии 1 в "
        "версию 2 и быть идемпотентным. В v1 есть orders(id, customer, total_cents, created_at). В v2 "
        "нужны customers(id INTEGER PRIMARY KEY, name TEXT UNIQUE без учёта регистра) и orders(id, "
        "customer_id, total_cents, created_at, status). Клиентов объединяй по customer.strip().lower(). "
        "ID заказов сохрани. status='paid' при total_cents>0, иначе 'void'. Установи PRAGMA user_version=2."
    ),
    setup_files={"migrate.py": "raise NotImplementedError\n"},
    setup_callback=_setup_task_368,
    gold_files={"migrate.py": _TASK_368_GOLD},
    verifier=_verify_sqlite_migration,
)


# 369. Update only specific INI fields and preserve the rest semantically.
def _verify_ini(ws: Path) -> VerifyResult:
    path = ws / "service.ini"
    if not path.is_file():
        return VerifyResult(False, "service.ini missing")
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        return VerifyResult(False, f"service.ini invalid: {exc}")
    expected = {
        "server": {"host": "127.0.0.1", "port": "9090", "debug": "false"},
        "logging": {"level": "WARNING", "format": "json"},
        "database": {"url": "sqlite:///app.db"},
    }
    actual = {section: dict(parser[section]) for section in parser.sections()}
    if actual != expected:
        return VerifyResult(False, f"service.ini semantic mismatch: {actual!r}")
    text = path.read_text(encoding="utf-8")
    if "; managed manually" not in text:
        return VerifyResult(False, "the existing comment was removed")
    return VerifyResult(True, "service.ini updated correctly")


TASK_369 = Task(
    id="task_369_ini_update",
    name="Update selected INI settings",
    tags=("tbench-lite", "config", "edit", "easy"),
    prompt=(
        "Измени service.ini: в [server] port поставь 9090 и debug=false; в [logging] level поставь "
        "WARNING. Остальные значения, секции и комментарий '; managed manually' сохрани. Не добавляй "
        "новых секций или ключей."
    ),
    setup_files={
        "service.ini": (
            "; managed manually\n"
            "[server]\n"
            "host = 127.0.0.1\n"
            "port = 8080\n"
            "debug = true\n\n"
            "[logging]\n"
            "level = INFO\n"
            "format = json\n\n"
            "[database]\n"
            "url = sqlite:///app.db\n"
        )
    },
    gold_files={
        "service.ini": (
            "; managed manually\n"
            "[server]\n"
            "host = 127.0.0.1\n"
            "port = 9090\n"
            "debug = false\n\n"
            "[logging]\n"
            "level = WARNING\n"
            "format = json\n\n"
            "[database]\n"
            "url = sqlite:///app.db\n"
        )
    },
    verifier=_verify_ini,
)


# 370. Apply cascading ordered changes and report every operation with a post-state hash.
_TASK_370_APP_V2 = "ENDPOINT = 'API_V2'\nFALLBACK = 'API_V2'\n"
_TASK_370_APP_FINAL = "ENDPOINT = 'API_CURRENT'\nFALLBACK = 'API_CURRENT'\n"
_TASK_370_CONFIG_PORT = "HOST=localhost\nPORT=9000\n"
_TASK_370_CONFIG_FINAL = "HOST=127.0.0.1\nPORT=9000\n"
_TASK_370_README_FINAL = "The client uses API_V2.\n"
_TASK_370_REPORT = [
    {"operation": 1, "file": "app.py", "replacements": 2, "sha256_after": hashlib.sha256(_TASK_370_APP_V2.encode()).hexdigest()},
    {"operation": 2, "file": "app.py", "replacements": 2, "sha256_after": hashlib.sha256(_TASK_370_APP_FINAL.encode()).hexdigest()},
    {"operation": 3, "file": "config.env", "replacements": 1, "sha256_after": hashlib.sha256(_TASK_370_CONFIG_PORT.encode()).hexdigest()},
    {"operation": 4, "file": "config.env", "replacements": 1, "sha256_after": hashlib.sha256(_TASK_370_CONFIG_FINAL.encode()).hexdigest()},
    {"operation": 5, "file": "README.md", "replacements": 1, "sha256_after": hashlib.sha256(_TASK_370_README_FINAL.encode()).hexdigest()},
    {"operation": 6, "file": "README.md", "replacements": 0, "sha256_after": hashlib.sha256(_TASK_370_README_FINAL.encode()).hexdigest()},
]

TASK_370 = Task(
    id="task_370_change_plan",
    name="Apply an ordered multi-file change plan",
    tags=("tbench-lite", "multi-file", "json", "medium"),
    prompt=(
        "В changes.json перечислены упорядоченные точные замены old->new. Примени каждую к текущему "
        "состоянию файла строго по порядку; результат ранней операции может стать входом следующей. "
        "Для каждой операции замени все точные вхождения old, даже если их ноль. Создай applied.json "
        "с одной записью на каждую операцию: operation (номер с 1), file, replacements и sha256_after "
        "(нижний hex SHA-256 байтов UTF-8 всего файла сразу после этой операции). Порядок и ключи "
        "записей именно такие, как перечислено. Другие файлы не меняй."
    ),
    setup_files={
        "changes.json": (
            '[{"file":"app.py","old":"API_V1","new":"API_V2"},'
            '{"file":"app.py","old":"API_V2","new":"API_CURRENT"},'
            '{"file":"config.env","old":"PORT=8000","new":"PORT=9000"},'
            '{"file":"config.env","old":"HOST=localhost","new":"HOST=127.0.0.1"},'
            '{"file":"README.md","old":"API_V1","new":"API_V2"},'
            '{"file":"README.md","old":"API_V3","new":"API_CURRENT"}]\n'
        ),
        "app.py": "ENDPOINT = 'API_V1'\nFALLBACK = 'API_V1'\n",
        "config.env": "HOST=localhost\nPORT=8000\n",
        "README.md": "The client uses API_V1.\n",
        "notes.txt": "Keep API_V1 here as historical text.\n",
    },
    gold_files={
        "app.py": _TASK_370_APP_FINAL,
        "config.env": _TASK_370_CONFIG_FINAL,
        "README.md": _TASK_370_README_FINAL,
        "applied.json": json.dumps(_TASK_370_REPORT, indent=2) + "\n",
    },
    verifier=all_of(
        _text_equals("app.py", _TASK_370_APP_FINAL),
        _text_equals("config.env", _TASK_370_CONFIG_FINAL),
        _text_equals("README.md", _TASK_370_README_FINAL),
        _text_equals("notes.txt", "Keep API_V1 here as historical text.\n"),
        _json_equals("applied.json", _TASK_370_REPORT),
    ),
)


# 371. Aggregate test-result shards with deterministic tie breaking.
_TASK_371_EXPECTED = {
    "total": 6,
    "attempts_total": 9,
    "passed": 3,
    "failed": 2,
    "skipped": 1,
    "failed_tests": ["test_auth", "test_export"],
    "retried_tests": ["test_auth", "test_login", "test_search"],
    "flaky_tests": ["test_search"],
    "slowest": [
        {"name": "test_export", "duration_ms": 900},
        {"name": "test_search", "duration_ms": 420},
        {"name": "test_auth", "duration_ms": 250},
    ],
}

TASK_371 = Task(
    id="task_371_test_result_aggregation",
    name="Aggregate test-result shards",
    tags=("tbench-lite", "json", "aggregation", "easy"),
    prompt=(
        "Объедини JSON-файлы из results в report.json. Запись содержит name,attempt,status,duration_ms. "
        "Для каждого name финальной считается запись с максимальным attempt. total и passed/failed/"
        "skipped считай по финальным записям, attempts_total — по всем записям. failed_tests — финальные "
        "failed по алфавиту. retried_tests — имена с более чем одной записью. flaky_tests — имена, где "
        "до финальной passed была хотя бы одна failed. slowest — три финальные записи с максимальным "
        "duration_ms, при равенстве name по алфавиту; элементы содержат только name,duration_ms."
    ),
    setup_files={
        "results/shard_b.json": (
            '[{"name":"test_search","attempt":2,"status":"passed","duration_ms":420},'
            '{"name":"test_export","attempt":1,"status":"failed","duration_ms":900},'
            '{"name":"test_cache","attempt":1,"status":"skipped","duration_ms":10},'
            '{"name":"test_auth","attempt":2,"status":"failed","duration_ms":250}]\n'
        ),
        "results/shard_a.json": (
            '[{"name":"test_login","attempt":1,"status":"passed","duration_ms":120},'
            '{"name":"test_auth","attempt":1,"status":"failed","duration_ms":300},'
            '{"name":"test_logout","attempt":1,"status":"passed","duration_ms":80},'
            '{"name":"test_search","attempt":1,"status":"failed","duration_ms":500}]\n'
        ),
        "results/shard_c.json": (
            '[{"name":"test_login","attempt":2,"status":"passed","duration_ms":100}]\n'
        ),
    },
    gold_files={"report.json": json.dumps(_TASK_371_EXPECTED, indent=2) + "\n"},
    verifier=_json_equals("report.json", _TASK_371_EXPECTED),
)


TBENCH_LITE_TASKS: list[Task] = [
    TASK_352,
    TASK_353,
    TASK_354,
    TASK_355,
    TASK_356,
    TASK_357,
    TASK_358,
    TASK_359,
    TASK_360,
    TASK_361,
    TASK_362,
    TASK_363,
    TASK_364,
    TASK_365,
    TASK_366,
    TASK_367,
    TASK_368,
    TASK_369,
    TASK_370,
    TASK_371,
]
