"""Agentic benchmark-inspired tasks (254..298).

This wave adapts patterns from Terminal-Bench, tau2-bench, and SWE-bench
into the compact harness-bench format: terminal/data workflows, policy-bound
tool decisions, and repository bug fixes with pytest.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness_bench.core import Task, VerifyResult
from harness_bench.verifiers import file_lines_equal, pytest_passes


def _read_json(ws: Path, rel: str) -> object:
    return json.loads((ws / rel).read_text())


def _json_equals(rel: str, expected: object):
    def _check(ws: Path) -> VerifyResult:
        try:
            data = _read_json(ws, rel)
        except FileNotFoundError:
            return VerifyResult(False, f"{rel} missing")
        except json.JSONDecodeError as exc:
            return VerifyResult(False, f"{rel} invalid JSON: {exc}")
        if data != expected:
            return VerifyResult(False, f"{rel} mismatch: {data!r}")
        return VerifyResult(True, f"{rel} matches expected JSON")

    return _check


def _json_equals_fuzzy(rel: str, expected: dict, *, free_text_fields: tuple[str, ...]):
    """Exact JSON match, except `free_text_fields` only need to be present
    and a non-empty string.

    Use for human-readable explanation fields (`reason`, `reason_code`) whose
    exact wording the prompt never dictates: the policy-load-bearing fields
    (amounts, ids, dates, booleans, enum action) are still matched exactly, so
    the task stays non-trivial, but a correctly-decided answer is not rejected
    just because it phrased its justification differently.
    """

    def _check(ws: Path) -> VerifyResult:
        try:
            data = _read_json(ws, rel)
        except FileNotFoundError:
            return VerifyResult(False, f"{rel} missing")
        except json.JSONDecodeError as exc:
            return VerifyResult(False, f"{rel} invalid JSON: {exc}")
        if not isinstance(data, dict):
            return VerifyResult(False, f"{rel} is not a JSON object: {data!r}")
        if set(data.keys()) != set(expected.keys()):
            return VerifyResult(False, f"{rel} key mismatch: {sorted(data.keys())!r}")
        for key, want in expected.items():
            if key in free_text_fields:
                got = data.get(key)
                if not isinstance(got, str) or not got.strip():
                    return VerifyResult(
                        False, f"{rel}.{key} must be a non-empty string, got {got!r}"
                    )
                continue
            if data.get(key) != want:
                return VerifyResult(
                    False, f"{rel}.{key} = {data.get(key)!r}, expected {want!r}"
                )
        return VerifyResult(True, f"{rel} matches expected JSON (free text: {free_text_fields})")

    return _check


# ---------------------------------------------------------------------------
# 254. terminal-style log forensics
# ---------------------------------------------------------------------------
_ACCESS_LOG_254 = """\
2026-05-24T10:00:01Z api01 GET /v1/orders 200 41
2026-05-24T10:00:02Z api02 POST /v1/payments 502 89
2026-05-24T10:00:03Z api01 GET /v1/orders 200 38
2026-05-24T10:00:04Z api03 GET /v1/search 504 120
2026-05-24T10:00:05Z api02 POST /v1/payments 502 91
2026-05-24T10:00:06Z api03 GET /v1/search 200 77
2026-05-24T10:00:07Z api02 POST /v1/payments 200 83
2026-05-24T10:00:08Z api01 GET /v1/orders 500 44
"""

_SUMMARY_254 = """\
endpoint,status,count
/v1/orders,200,2
/v1/orders,500,1
/v1/payments,200,1
/v1/payments,502,2
/v1/search,200,1
/v1/search,504,1
"""


TASK_254 = Task(
    id="task_254_terminal_log_status_matrix",
    name="Build an endpoint/status matrix from access.log",
    tags=("terminal-bench", "logs", "csv", "execute", "hard"),
    prompt=(
        "В файле access.log лежат строки формата: timestamp host method endpoint"
        " status latency_ms. Создай report/status_matrix.csv с заголовком"
        " endpoint,status,count. Посчитай количество строк для каждой пары"
        " endpoint/status. Отсортируй сначала по endpoint, затем по числовому"
        " status. Не добавляй лишних строк."
    ),
    setup_files={"access.log": _ACCESS_LOG_254},
    gold_files={"report/status_matrix.csv": _SUMMARY_254},
    verifier=file_lines_equal(
        "report/status_matrix.csv",
        [line for line in _SUMMARY_254.strip().splitlines()],
    ),
)


# ---------------------------------------------------------------------------
# 255. terminal-style process table triage
# ---------------------------------------------------------------------------
_PS_255 = """\
USER       PID %CPU %MEM COMMAND
root         1  0.0  0.1 /sbin/init
app       1042 87.5 12.0 python worker.py --queue emails
app       1043 12.1 42.5 python worker.py --queue images
db        1200 33.0 51.2 postgres: writer
app       1300  2.0  1.1 nginx: worker
"""

_PROCESS_REPORT_255 = {
    "cpu_hot": [{"pid": 1042, "command": "python worker.py --queue emails", "cpu": 87.5}],
    "memory_hot": [
        {"pid": 1043, "command": "python worker.py --queue images", "mem": 42.5},
        {"pid": 1200, "command": "postgres: writer", "mem": 51.2},
    ],
}


TASK_255 = Task(
    id="task_255_terminal_process_hotspots",
    name="Summarize CPU and memory hotspots from ps output",
    tags=("terminal-bench", "process", "json", "hard"),
    prompt=(
        "В ps.txt лежит таблица с колонками USER PID %CPU %MEM COMMAND."
        " Создай process_hotspots.json с двумя списками: cpu_hot для процессов"
        " с %CPU >= 50 и memory_hot для процессов с %MEM >= 40. В каждом"
        " объекте должны быть pid, command и соответственно cpu или mem."
        " Порядок сохраняй как в ps.txt."
    ),
    setup_files={"ps.txt": _PS_255},
    gold_files={"process_hotspots.json": json.dumps(_PROCESS_REPORT_255, indent=2) + "\n"},
    verifier=_json_equals("process_hotspots.json", _PROCESS_REPORT_255),
)


# ---------------------------------------------------------------------------
# 256. terminal-style process output normalization
# ---------------------------------------------------------------------------
_RAW_EVENTS_256 = """\
INFO job=ingest attempt=1 duration_ms=120
WARN job=ingest attempt=2 duration_ms=240
INFO job=export attempt=1 duration_ms=80
ERROR job=ingest attempt=3 duration_ms=510
INFO job=export attempt=2 duration_ms=90
ERROR job=sync attempt=1 duration_ms=310
INFO job=sync attempt=2 duration_ms=150
"""

_EVENTS_JSON_256 = [
    {"job": "export", "attempts": 2, "max_duration_ms": 90, "errors": 0},
    {"job": "ingest", "attempts": 3, "max_duration_ms": 510, "errors": 1},
    {"job": "sync", "attempts": 2, "max_duration_ms": 310, "errors": 1},
]


def _verify_task_256(ws: Path) -> VerifyResult:
    p = ws / "job_summary.json"
    if not p.exists():
        return VerifyResult(False, "job_summary.json missing")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"invalid JSON: {exc}")
    if data != _EVENTS_JSON_256:
        return VerifyResult(False, f"job_summary.json mismatch: {data!r}")
    return VerifyResult(True, "job_summary.json matches expected aggregate")


TASK_256 = Task(
    id="task_256_terminal_event_summary",
    name="Normalize shell-style event lines into JSON",
    tags=("terminal-bench", "parse", "json", "execute", "hard"),
    prompt=(
        "Прочитай raw_events.txt. Каждая строка начинается с уровня INFO/WARN/"
        "ERROR, дальше key=value поля job, attempt, duration_ms. Создай"
        " job_summary.json: JSON-массив объектов, отсортированный по job."
        " Для каждого job укажи attempts (количество строк), max_duration_ms"
        " (максимум duration_ms) и errors (количество строк уровня ERROR)."
    ),
    setup_files={"raw_events.txt": _RAW_EVENTS_256},
    gold_files={"job_summary.json": json.dumps(_EVENTS_JSON_256, indent=2) + "\n"},
    verifier=_verify_task_256,
)


# ---------------------------------------------------------------------------
# 257. tau2-style airline policy refusal
# ---------------------------------------------------------------------------
_AIRLINE_STATE_257 = {
    "reservation_id": "EHGLP3",
    "user_id": "emma_kim_9957",
    "traveler": "Emma Kim",
    "booked_at": "2026-05-20T09:30:00Z",
    "request_at": "2026-05-24T12:00:00Z",
    "fare": "basic",
    "insurance": False,
    "status": "confirmed",
}

_AIRLINE_ACTION_257 = {
    "action": "refuse_cancellation",
    "reservation_id": "EHGLP3",
    "refund_amount": 0,
    "reason": "basic fare without insurance is outside the 24 hour cancellation window",
}


def _verify_task_257(ws: Path) -> VerifyResult:
    # `reason` is free-form prose the prompt never dictates verbatim, so match
    # the decision fields (action / reservation_id / refund_amount) exactly and
    # only require `reason` to be a present, non-empty string.
    result = _json_equals_fuzzy(
        "agent_action.json", _AIRLINE_ACTION_257, free_text_fields=("reason",)
    )(ws)
    if not result.passed:
        return result
    state = json.loads((ws / "reservation.json").read_text())
    if state != _AIRLINE_STATE_257:
        return VerifyResult(False, "reservation.json must not be modified")
    return VerifyResult(True, "policy refusal action is correct")


TASK_257 = Task(
    id="task_257_tau2_airline_refusal",
    name="Choose a policy-compliant airline cancellation action",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "Смоделируй финальный tool-call для airline-сценария. Клиент Emma Kim"
        " хочет отменить бронь EHGLP3 и утверждает, что ей говорили про"
        " страховку на прошлой поездке. Используй reservation.json и"
        " airline_policy.md. Нельзя менять reservation.json. Создай"
        " agent_action.json ровно с полями action, reservation_id,"
        " refund_amount, reason. Если отмена запрещена политикой, action"
        " должен быть refuse_cancellation."
    ),
    setup_files={
        "reservation.json": json.dumps(_AIRLINE_STATE_257, indent=2) + "\n",
        "airline_policy.md": (
            "# Cancellation policy\n"
            "- Basic fare can be cancelled for a full refund only within 24 hours of booking.\n"
            "- After 24 hours, basic fare cancellation requires travel insurance on the reservation.\n"
            "- Previous trips and verbal claims do not override the reservation record.\n"
            "- When cancellation is not allowed, do not change the reservation state.\n"
        ),
    },
    gold_files={"agent_action.json": json.dumps(_AIRLINE_ACTION_257, indent=2) + "\n"},
    verifier=_verify_task_257,
)


# ---------------------------------------------------------------------------
# 258. tau2-style retail order action
# ---------------------------------------------------------------------------
_ORDER_258 = {
    "order_id": "R-1042",
    "customer_id": "c_778",
    "delivered_days_ago": 17,
    "items": [
        {"sku": "hoodie-navy-m", "category": "apparel", "price": 64.0, "opened": False},
        {"sku": "mug-ceramic", "category": "home", "price": 18.0, "opened": True},
    ],
}

_RETAIL_ACTION_258 = {
    "action": "create_return_label",
    "order_id": "R-1042",
    "accepted_skus": ["hoodie-navy-m"],
    "rejected_skus": ["mug-ceramic"],
    "refund_total": 64.0,
}


def _verify_task_258(ws: Path) -> VerifyResult:
    try:
        action = _read_json(ws, "retail_action.json")
    except FileNotFoundError:
        return VerifyResult(False, "retail_action.json missing")
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"invalid JSON: {exc}")
    if action != _RETAIL_ACTION_258:
        return VerifyResult(False, f"retail_action.json mismatch: {action!r}")
    return VerifyResult(True, "retail action matches policy")


TASK_258 = Task(
    id="task_258_tau2_retail_partial_return",
    name="Produce a partial retail return action",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "Покупатель просит вернуть оба товара из заказа R-1042. По policy.md"
        " реши, какие позиции можно принять. Создай retail_action.json с"
        " полями action, order_id, accepted_skus, rejected_skus, refund_total."
        " Если хотя бы один товар можно вернуть, action должен быть"
        " create_return_label. Списки sku отсортируй как в заказе."
    ),
    setup_files={
        "order.json": json.dumps(_ORDER_258, indent=2) + "\n",
        "policy.md": (
            "# Retail returns\n"
            "- Returns are allowed within 30 days after delivery.\n"
            "- Apparel may be returned if it is unopened.\n"
            "- Home goods may be returned only when unopened and within 14 days.\n"
            "- Refund total is the sum of accepted item prices.\n"
        ),
    },
    gold_files={"retail_action.json": json.dumps(_RETAIL_ACTION_258, indent=2) + "\n"},
    verifier=_verify_task_258,
)


# ---------------------------------------------------------------------------
# 259. tau2-style telecom plan migration
# ---------------------------------------------------------------------------
_TELECOM_STATE_259 = {
    "account_id": "acct-91",
    "current_plan": "starter_5gb",
    "contract_months_remaining": 0,
    "autopay": True,
    "lines": [
        {"line_id": "L1", "usage_gb": 8.4},
        {"line_id": "L2", "usage_gb": 1.2},
        {"line_id": "L3", "usage_gb": 14.9},
    ],
}

_TELECOM_ACTION_259 = {
    "action": "switch_plan",
    "account_id": "acct-91",
    "new_plan": "family_25gb",
    "monthly_delta_usd": 18,
    "requires_consent": True,
}


def _verify_task_259(ws: Path) -> VerifyResult:
    try:
        action = _read_json(ws, "telecom_action.json")
    except FileNotFoundError:
        return VerifyResult(False, "telecom_action.json missing")
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"invalid JSON: {exc}")
    if action != _TELECOM_ACTION_259:
        return VerifyResult(False, f"telecom_action.json mismatch: {action!r}")
    return VerifyResult(True, "telecom action matches upgrade rules")


TASK_259 = Task(
    id="task_259_tau2_telecom_plan_switch",
    name="Select a telecom plan switch action",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "Клиент хочет самый дешевый тариф, который покроет текущее суммарное"
        " потребление всех линий без овердрафта. Используй account.json и"
        " plans.csv. Создай telecom_action.json с полями action, account_id,"
        " new_plan, monthly_delta_usd, requires_consent. Значение action —"
        " switch_plan. Если смена повышает цену, requires_consent=true."
        " Не меняй исходные файлы."
    ),
    setup_files={
        "account.json": json.dumps(_TELECOM_STATE_259, indent=2) + "\n",
        "plans.csv": (
            "plan_id,included_gb,monthly_usd\n"
            "starter_5gb,5,30\n"
            "family_15gb,15,42\n"
            "family_25gb,25,48\n"
            "unlimited,999,80\n"
        ),
    },
    gold_files={"telecom_action.json": json.dumps(_TELECOM_ACTION_259, indent=2) + "\n"},
    verifier=_verify_task_259,
)


# ---------------------------------------------------------------------------
# 260. SWE-bench-style version parsing bug
# ---------------------------------------------------------------------------
_VERSIONS_PY_260 = """\
def normalize_version(raw: str) -> tuple[int, ...]:
    parts = raw.strip().split(".")
    return tuple(int(part) for part in parts)


def is_compatible(installed: str, required: str) -> bool:
    return normalize_version(installed) >= normalize_version(required)
"""

_TEST_VERSION_260 = """\
from versions import is_compatible, normalize_version


def test_plain_versions_still_work():
    assert normalize_version("1.2.3") == (1, 2, 3)
    assert is_compatible("1.10.0", "1.2.9")


def test_prefix_and_suffix_are_ignored():
    assert normalize_version("v2.4.0") == (2, 4, 0)
    assert normalize_version("3.1.0-rc1") == (3, 1, 0)
    assert is_compatible("v3.1.0-rc1", "3.0.9")


def test_missing_patch_is_padded():
    assert normalize_version("2.4") == (2, 4, 0)
    assert is_compatible("2.4", "2.4.0")
"""

_VERSIONS_GOLD_260 = """\
import re


def normalize_version(raw: str) -> tuple[int, ...]:
    numbers = [int(part) for part in re.findall(r"\\d+", raw)]
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers[:3])


def is_compatible(installed: str, required: str) -> bool:
    return normalize_version(installed) >= normalize_version(required)
"""

TASK_260 = Task(
    id="task_260_swe_version_parser",
    name="Fix normalize_version for prefixed and prerelease versions",
    tags=("swe-bench", "python", "pytest", "bugfix", "hard"),
    prompt=(
        "В мини-репозитории падают тесты для парсинга версий. Почини"
        " versions.py так, чтобы normalize_version принимала обычные версии,"
        " префикс v, суффиксы вроде -rc1, а также версии без patch-компонента."
        " Запусти тесты и не меняй tests/test_versions.py."
    ),
    setup_files={"versions.py": _VERSIONS_PY_260, "tests/test_versions.py": _TEST_VERSION_260},
    gold_files={"versions.py": _VERSIONS_GOLD_260},
    verifier=pytest_passes("tests"),
)


# ---------------------------------------------------------------------------
# 261. SWE-bench-style config precedence bug
# ---------------------------------------------------------------------------
_SETTINGS_PY_261 = """\
import os


def load_settings(defaults: dict, file_settings: dict, cli_settings: dict) -> dict:
    settings = {}
    settings.update(cli_settings)
    settings.update(file_settings)
    settings.update(defaults)
    if "APP_PORT" in os.environ:
        settings["port"] = int(os.environ["APP_PORT"])
    return settings
"""

_TEST_SETTINGS_261 = """\
from settings import load_settings


def test_precedence_defaults_file_cli_env(monkeypatch):
    monkeypatch.setenv("APP_PORT", "9000")
    got = load_settings(
        {"host": "127.0.0.1", "port": 8000, "debug": False},
        {"port": 8100, "debug": True},
        {"debug": False},
    )
    assert got == {"host": "127.0.0.1", "port": 9000, "debug": False}


def test_none_cli_value_does_not_delete_file_value(monkeypatch):
    monkeypatch.delenv("APP_PORT", raising=False)
    got = load_settings({"timeout": 10}, {"timeout": 30}, {"timeout": None})
    assert got["timeout"] == 30
"""

_SETTINGS_GOLD_261 = """\
import os


def load_settings(defaults: dict, file_settings: dict, cli_settings: dict) -> dict:
    settings = {}
    settings.update(defaults)
    settings.update(file_settings)
    settings.update({key: value for key, value in cli_settings.items() if value is not None})
    if "APP_PORT" in os.environ:
        settings["port"] = int(os.environ["APP_PORT"])
    return settings
"""

TASK_261 = Task(
    id="task_261_swe_config_precedence",
    name="Fix configuration precedence and None handling",
    tags=("swe-bench", "python", "pytest", "bugfix", "hard"),
    prompt=(
        "Почини баг в settings.py. Ожидаемый приоритет настроек:"
        " defaults < file_settings < cli_settings < переменная окружения"
        " APP_PORT. Значения None в cli_settings означают 'не задано' и не"
        " должны перетирать значение из файла. Тесты менять нельзя."
    ),
    setup_files={"settings.py": _SETTINGS_PY_261, "tests/test_settings.py": _TEST_SETTINGS_261},
    gold_files={"settings.py": _SETTINGS_GOLD_261},
    verifier=pytest_passes("tests"),
)


# ---------------------------------------------------------------------------
# 262. SWE-bench-style CSV edge case
# ---------------------------------------------------------------------------
_REPORTS_PY_262 = """\
import csv


def top_customers(path: str, limit: int = 3) -> list[str]:
    totals = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            totals[row["customer"]] = int(row["amount"])
    return [name for name, total in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]]
"""

_TEST_REPORTS_262 = """\
from pathlib import Path

from reports import top_customers


def test_sums_repeated_customers_and_ignores_refunds(tmp_path: Path):
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text(
        "customer,amount,status\\n"
        "Ada,40,paid\\n"
        "Bob,100,refunded\\n"
        "Ada,35,paid\\n"
        "Cara,60,paid\\n"
        "Bob,25,paid\\n"
    )
    assert top_customers(str(csv_path), limit=3) == ["Ada", "Cara", "Bob"]


def test_tie_breaks_by_name(tmp_path: Path):
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text(
        "customer,amount,status\\n"
        "Zoe,50,paid\\n"
        "Ann,50,paid\\n"
        "Mike,10,paid\\n"
    )
    assert top_customers(str(csv_path), limit=2) == ["Ann", "Zoe"]
"""

_REPORTS_GOLD_262 = """\
import csv


def top_customers(path: str, limit: int = 3) -> list[str]:
    totals: dict[str, int] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row["status"] != "paid":
                continue
            customer = row["customer"]
            totals[customer] = totals.get(customer, 0) + int(row["amount"])
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return [name for name, _total in ranked[:limit]]
"""

TASK_262 = Task(
    id="task_262_swe_csv_top_customers",
    name="Fix top_customers aggregation and sorting",
    tags=("swe-bench", "python", "pytest", "bugfix", "hard"),
    prompt=(
        "В reports.py есть баг в функции top_customers. Нужно суммировать"
        " несколько paid-заказов одного клиента, игнорировать refunded-заказы,"
        " сортировать по сумме по убыванию, а при равной сумме по имени"
        " клиента по возрастанию. Тесты менять нельзя."
    ),
    setup_files={"reports.py": _REPORTS_PY_262, "tests/test_reports.py": _TEST_REPORTS_262},
    gold_files={"reports.py": _REPORTS_GOLD_262},
    verifier=pytest_passes("tests"),
)


# ---------------------------------------------------------------------------
# 263. terminal-style disk usage ranking
# ---------------------------------------------------------------------------
_DU_263 = """\
16K ./src/api
44K ./src/web
8K ./docs
120K ./data/raw
64K ./data/processed
12K ./tests
"""

_TOP_DIRS_263 = """\
data/raw	120
data/processed	64
src/web	44
"""

TASK_263 = Task(
    id="task_263_terminal_du_top_dirs",
    name="Extract top directories from du-style output",
    tags=("terminal-bench", "parse", "text", "execute", "hard"),
    prompt=(
        "В файле du.txt лежит вывод, похожий на `du -h`, но все размеры уже"
        " в KiB и записаны как '<N>K <path>'. Создай reports/top_dirs.tsv:"
        " три самые большие директории, формат '<path без ./>\\t<size_kib>',"
        " сортировка по размеру по убыванию. Заголовок не нужен."
    ),
    setup_files={"du.txt": _DU_263},
    gold_files={"reports/top_dirs.tsv": _TOP_DIRS_263},
    verifier=file_lines_equal("reports/top_dirs.tsv", _TOP_DIRS_263.strip().splitlines()),
)


# ---------------------------------------------------------------------------
# 264. terminal-style env precedence
# ---------------------------------------------------------------------------
_EFFECTIVE_ENV_264 = """\
API_URL=https://api.prod.example
DEBUG=false
LOG_LEVEL=debug
TIMEOUT=45
"""

TASK_264 = Task(
    id="task_264_terminal_env_precedence",
    name="Merge env files with CLI precedence",
    tags=("terminal-bench", "config", "text", "hard"),
    prompt=(
        "Собери effective.env из трех файлов: env/defaults.env,"
        " env/local.env, env/cli.env. Приоритет значений:"
        " defaults < local < cli. Игнорируй пустые строки и комментарии,"
        " итоговые KEY=VALUE строки отсортируй по имени ключа."
    ),
    setup_files={
        "env/defaults.env": "API_URL=https://api.dev.example\nDEBUG=true\nTIMEOUT=30\n",
        "env/local.env": "# local overrides\nAPI_URL=https://api.prod.example\nTIMEOUT=45\n",
        "env/cli.env": "DEBUG=false\nLOG_LEVEL=debug\n",
    },
    gold_files={"effective.env": _EFFECTIVE_ENV_264},
    verifier=file_lines_equal("effective.env", _EFFECTIVE_ENV_264.strip().splitlines()),
)


# ---------------------------------------------------------------------------
# 265. terminal-style Makefile dependency plan
# ---------------------------------------------------------------------------
_BUILD_ORDER_265 = """\
clean
assets
compile
package
deploy
"""

TASK_265 = Task(
    id="task_265_terminal_makefile_plan",
    name="Resolve a Makefile target dependency order",
    tags=("terminal-bench", "makefile", "dependency", "hard"),
    prompt=(
        "В Makefile.simple есть targets в формате 'target: dep1 dep2'."
        " Построй build_order.txt для цели deploy: каждый target должен"
        " появиться после своих зависимостей, по одному target в строке."
        " Команды под target отсутствуют; строки комментариев игнорируй."
        " Если на каком-то шаге к сборке готовы сразу несколько targets"
        " (все их зависимости уже идут выше), бери из них лексикографически"
        " наименьший — чтобы порядок был однозначным."
    ),
    setup_files={
        "Makefile.simple": (
            "# simplified make dependencies\n"
            "deploy: package\n"
            "package: compile assets\n"
            "compile: clean\n"
            "assets: clean\n"
            "clean:\n"
        )
    },
    gold_files={"build_order.txt": _BUILD_ORDER_265},
    verifier=file_lines_equal("build_order.txt", _BUILD_ORDER_265.strip().splitlines()),
)


# ---------------------------------------------------------------------------
# 266. terminal-style checksum manifest
# ---------------------------------------------------------------------------
_FILES_266 = {
    "payload/a.txt": "alpha\n",
    "payload/b.txt": "bravo\n",
    "payload/nested/c.txt": "charlie\n",
}


def _manifest_266() -> str:
    import hashlib

    rows = []
    for rel, content in sorted(_FILES_266.items()):
        rows.append(f"{hashlib.sha256(content.encode()).hexdigest()}  {rel}")
    return "\n".join(rows) + "\n"


def _verify_task_266(ws: Path) -> VerifyResult:
    expected = _manifest_266().strip().splitlines()
    p = ws / "SHA256SUMS"
    if not p.exists():
        return VerifyResult(False, "SHA256SUMS missing")
    actual = [line for line in p.read_text().splitlines() if line.strip()]
    if actual != expected:
        return VerifyResult(False, f"SHA256SUMS mismatch: {actual!r}")
    return VerifyResult(True, "SHA256SUMS matches payload files")


TASK_266 = Task(
    id="task_266_terminal_sha256_manifest",
    name="Create a deterministic SHA256 manifest",
    tags=("terminal-bench", "hash", "manifest", "execute", "hard"),
    prompt=(
        "Для всех обычных файлов внутри каталога payload создай SHA256SUMS."
        " Формат каждой строки как у sha256sum: '<hex>  <relative-path>'."
        " Пути должны быть относительно корня рабочей директории и"
        " отсортированы лексикографически."
    ),
    setup_files=_FILES_266,
    gold_files={"SHA256SUMS": _manifest_266()},
    verifier=_verify_task_266,
)


# ---------------------------------------------------------------------------
# 267. terminal-style failed job report
# ---------------------------------------------------------------------------
_JOB_REPORT_267 = {
    "failed_jobs": ["backup-users", "sync-ledger"],
    "slowest_job": "backup-users",
    "slowest_duration_s": 540,
}


TASK_267 = Task(
    id="task_267_terminal_job_log_report",
    name="Summarize job failures and slowest duration",
    tags=("terminal-bench", "logs", "json", "hard"),
    prompt=(
        "Прочитай jobs.log. Каждая строка: '<timestamp> job=<name>"
        " status=<ok|failed> duration_s=<N>'. Создай job_report.json с"
        " failed_jobs (список имен failed в порядке появления), slowest_job"
        " и slowest_duration_s по всем строкам."
    ),
    setup_files={
        "jobs.log": (
            "2026-05-24T01:00:00Z job=backup-users status=failed duration_s=540\n"
            "2026-05-24T01:05:00Z job=sync-ledger status=failed duration_s=180\n"
            "2026-05-24T01:10:00Z job=refresh-cache status=ok duration_s=45\n"
            "2026-05-24T01:12:00Z job=send-digest status=ok duration_s=12\n"
        )
    },
    gold_files={"job_report.json": json.dumps(_JOB_REPORT_267, indent=2) + "\n"},
    verifier=_json_equals("job_report.json", _JOB_REPORT_267),
)


# ---------------------------------------------------------------------------
# 268. terminal-style permission audit
# ---------------------------------------------------------------------------
_PERMISSION_REPORT_268 = """\
scripts/deploy.sh	world-writable
secrets/token.txt	world-readable
"""

TASK_268 = Task(
    id="task_268_terminal_permission_audit",
    name="Audit risky file permissions from ls output",
    tags=("terminal-bench", "permissions", "security", "hard"),
    prompt=(
        "В ls_permissions.txt лежит вывод `ls -l` с путями в последней колонке."
        " Создай permission_findings.tsv без заголовка. Добавь строку"
        " '<path>\\tworld-writable' для файлов, у которых установлен бит записи"
        " для others, и '<path>\\tworld-readable' для файлов внутри secrets/,"
        " у которых установлен бит чтения для others. Порядок как в исходном"
        " файле."
    ),
    setup_files={
        "ls_permissions.txt": (
            "-rw-r--r-- 1 app app 120 May 25 10:00 README.md\n"
            "-rwxrwxrwx 1 app app 400 May 25 10:01 scripts/deploy.sh\n"
            "-rw-r----- 1 app app  99 May 25 10:02 secrets/db.txt\n"
            "-rw-r--r-- 1 app app  40 May 25 10:03 secrets/token.txt\n"
        )
    },
    gold_files={"permission_findings.tsv": _PERMISSION_REPORT_268},
    verifier=file_lines_equal(
        "permission_findings.tsv", _PERMISSION_REPORT_268.strip().splitlines()
    ),
)


# ---------------------------------------------------------------------------
# 269. terminal-style markdown index
# ---------------------------------------------------------------------------
_INDEX_269 = """\
docs/api/auth.md	Auth API
docs/api/orders.md	Orders API
docs/guide/install.md	Install Guide
"""

TASK_269 = Task(
    id="task_269_terminal_markdown_index",
    name="Build an index from markdown H1 headings",
    tags=("terminal-bench", "markdown", "search", "hard"),
    prompt=(
        "Найди все .md-файлы внутри docs. Для каждого возьми первый заголовок"
        " первого уровня (# Title) и создай docs_index.tsv в корне. Формат"
        " '<path>\\t<title>', пути сортируй лексикографически."
    ),
    setup_files={
        "docs/api/auth.md": "# Auth API\n\nDetails\n",
        "docs/api/orders.md": "# Orders API\n\nDetails\n",
        "docs/guide/install.md": "# Install Guide\n\nDetails\n",
        "docs/README.txt": "# Not markdown\n",
    },
    gold_files={"docs_index.tsv": _INDEX_269},
    verifier=file_lines_equal("docs_index.tsv", _INDEX_269.strip().splitlines()),
)


# ---------------------------------------------------------------------------
# 270..254. tau2-style policy tasks
# ---------------------------------------------------------------------------
_BANK_ACTION_270 = {
    "action": "open_dispute",
    "transaction_id": "txn-774",
    "provisional_credit": 48.75,
    "reason_code": "duplicate_card_present",
}

TASK_270 = Task(
    id="task_270_tau2_bank_duplicate_charge",
    name="Choose a bank dispute action for duplicate charge",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "Клиент оспаривает повторное списание. Используй transaction.json и"
        " policy.md. Создай bank_action.json с action, transaction_id,"
        " provisional_credit, reason_code. Если policy разрешает dispute,"
        " action=open_dispute."
    ),
    setup_files={
        "transaction.json": json.dumps(
            {
                "transaction_id": "txn-774",
                "merchant": "Metro Cafe",
                "amount": 48.75,
                "card_present": True,
                "duplicate_of": "txn-773",
                "days_since_posted": 3,
            },
            indent=2,
        )
        + "\n",
        "policy.md": (
            "- Duplicate card-present charges may be disputed within 10 days.\n"
            "- Provisional credit equals the duplicate transaction amount.\n"
        ),
    },
    gold_files={"bank_action.json": json.dumps(_BANK_ACTION_270, indent=2) + "\n"},
    verifier=_json_equals_fuzzy(
        "bank_action.json", _BANK_ACTION_270, free_text_fields=("reason_code",)
    ),
)


_HOTEL_ACTION_271 = {
    "action": "offer_paid_late_checkout",
    "reservation_id": "H-919",
    "latest_checkout": "14:00",
    "fee_usd": 35,
}

TASK_271 = Task(
    id="task_271_tau2_hotel_late_checkout",
    name="Select hotel late-checkout action",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "Гость просит late checkout до 16:00. По reservation.json и"
        " hotel_policy.md создай hotel_action.json с action, reservation_id,"
        " latest_checkout, fee_usd. Значение action — offer_paid_late_checkout."
        " Выбери максимально поздний вариант, разрешенный политикой."
    ),
    setup_files={
        "reservation.json": json.dumps(
            {"reservation_id": "H-919", "tier": "standard", "occupancy_next_day": 0.92},
            indent=2,
        )
        + "\n",
        "hotel_policy.md": (
            "- Standard guests get free late checkout until 12:00 when occupancy is below 80%.\n"
            "- Paid late checkout until 14:00 costs 35 USD when occupancy is below 95%.\n"
            "- Checkout after 14:00 is only for platinum guests.\n"
        ),
    },
    gold_files={"hotel_action.json": json.dumps(_HOTEL_ACTION_271, indent=2) + "\n"},
    verifier=_json_equals("hotel_action.json", _HOTEL_ACTION_271),
)


_CLINIC_ACTION_272 = {
    "action": "reschedule",
    "appointment_id": "A-502",
    "new_slot": "2026-05-28T09:30:00",
    "notify_patient": True,
}

TASK_272 = Task(
    id="task_272_tau2_clinic_reschedule",
    name="Pick a clinic reschedule slot",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "Пациент просит ближайший утренний слот у того же врача. Используй"
        " appointment.json и slots.json. Утренние слоты начинаются до 12:00."
        " Создай clinic_action.json с action, appointment_id, new_slot,"
        " notify_patient. Значение action — reschedule, notify_patient — true."
    ),
    setup_files={
        "appointment.json": json.dumps(
            {"appointment_id": "A-502", "doctor_id": "dr-7", "current_slot": "2026-05-27T16:00:00"},
            indent=2,
        )
        + "\n",
        "slots.json": json.dumps(
            [
                {"doctor_id": "dr-8", "slot": "2026-05-27T09:00:00"},
                {"doctor_id": "dr-7", "slot": "2026-05-28T13:00:00"},
                {"doctor_id": "dr-7", "slot": "2026-05-28T09:30:00"},
            ],
            indent=2,
        )
        + "\n",
    },
    gold_files={"clinic_action.json": json.dumps(_CLINIC_ACTION_272, indent=2) + "\n"},
    verifier=_json_equals("clinic_action.json", _CLINIC_ACTION_272),
)


_INSURANCE_ACTION_273 = {
    "action": "request_documents",
    "claim_id": "C-331",
    "missing_documents": ["police_report"],
    "can_approve_now": False,
}

TASK_273 = Task(
    id="task_273_tau2_insurance_docs",
    name="Request missing insurance claim documents",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "По claim.json и policy.md реши, можно ли одобрить claim. Создай"
        " insurance_action.json с action, claim_id, missing_documents,"
        " can_approve_now. Значение action — request_documents."
        " Для кражи дороже 500 USD нужен police_report."
    ),
    setup_files={
        "claim.json": json.dumps(
            {"claim_id": "C-331", "type": "theft", "amount_usd": 880, "documents": ["receipt"]},
            indent=2,
        )
        + "\n",
        "policy.md": "- Theft claims over 500 USD require receipt and police_report.\n",
    },
    gold_files={"insurance_action.json": json.dumps(_INSURANCE_ACTION_273, indent=2) + "\n"},
    verifier=_json_equals("insurance_action.json", _INSURANCE_ACTION_273),
)


_DELIVERY_ACTION_274 = {
    "action": "partial_refund",
    "order_id": "D-88",
    "refund_usd": 12.5,
    "coupon_usd": 5,
}

TASK_274 = Task(
    id="task_274_tau2_delivery_late_food",
    name="Apply food delivery late-order policy",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "Заказ еды доставлен с опозданием. Используй delivery.json и"
        " policy.md. Создай delivery_action.json с action, order_id,"
        " refund_usd, coupon_usd. Значение action — partial_refund."
        " Холодные товары компенсируются полностью,"
        " при задержке больше 45 минут добавляется купон 5 USD."
    ),
    setup_files={
        "delivery.json": json.dumps(
            {
                "order_id": "D-88",
                "minutes_late": 52,
                "items": [
                    {"name": "ramen", "price": 12.5, "arrived_cold": True},
                    {"name": "tea", "price": 4.0, "arrived_cold": False},
                ],
            },
            indent=2,
        )
        + "\n",
        "policy.md": "- Refund cold items at item price. Add 5 USD coupon if late by more than 45 minutes.\n",
    },
    gold_files={"delivery_action.json": json.dumps(_DELIVERY_ACTION_274, indent=2) + "\n"},
    verifier=_json_equals("delivery_action.json", _DELIVERY_ACTION_274),
)


_SUBSCRIPTION_ACTION_275 = {
    "action": "schedule_downgrade",
    "account_id": "sub-19",
    "new_plan": "basic",
    "effective_date": "2026-06-01",
    "refund_now": 0,
}

TASK_275 = Task(
    id="task_275_tau2_subscription_downgrade",
    name="Schedule a subscription downgrade",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "Пользователь хочет перейти с pro на basic. По subscription.json и"
        " policy.md создай subscription_action.json с action, account_id,"
        " new_plan, effective_date, refund_now. Значение action —"
        " schedule_downgrade. Downgrade вступает в силу"
        " в следующий billing_anchor и не дает мгновенный refund."
    ),
    setup_files={
        "subscription.json": json.dumps(
            {"account_id": "sub-19", "current_plan": "pro", "billing_anchor": "2026-06-01"},
            indent=2,
        )
        + "\n",
        "policy.md": "- Downgrades take effect on the next billing_anchor. Immediate refund is 0.\n",
    },
    gold_files={
        "subscription_action.json": json.dumps(_SUBSCRIPTION_ACTION_275, indent=2) + "\n"
    },
    verifier=_json_equals("subscription_action.json", _SUBSCRIPTION_ACTION_275),
)


_BAGGAGE_ACTION_276 = {
    "action": "compensate_baggage_delay",
    "case_id": "B-707",
    "compensation_usd": 100,
    "escalate": False,
}

TASK_276 = Task(
    id="task_276_tau2_baggage_delay",
    name="Calculate baggage delay compensation",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "По baggage_case.json и policy.md создай baggage_action.json с"
        " action, case_id, compensation_usd, escalate. Значение action —"
        " compensate_baggage_delay. Для задержки багажа"
        " больше 24 часов компенсация 100 USD; escalate только если больше"
        " 72 часов."
    ),
    setup_files={
        "baggage_case.json": json.dumps({"case_id": "B-707", "delay_hours": 31}, indent=2)
        + "\n",
        "policy.md": "- Delay >24h: 100 USD. Delay >72h: escalate.\n",
    },
    gold_files={"baggage_action.json": json.dumps(_BAGGAGE_ACTION_276, indent=2) + "\n"},
    verifier=_json_equals("baggage_action.json", _BAGGAGE_ACTION_276),
)


# ---------------------------------------------------------------------------
# 277..261. SWE-bench-style pytest bug fixes
# ---------------------------------------------------------------------------
_DATES_PY_277 = """\
from datetime import datetime


def parse_date(value: str) -> str:
    return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
"""

_TEST_DATES_277 = """\
from dates import parse_date


def test_accepts_iso_datetime_and_date():
    assert parse_date("2026-05-25") == "2026-05-25"
    assert parse_date("2026-05-25T10:30:00Z") == "2026-05-25"


def test_accepts_slashes():
    assert parse_date("2026/05/25") == "2026-05-25"
"""

_DATES_GOLD_277 = """\
from datetime import datetime


def parse_date(value: str) -> str:
    value = value.strip().removesuffix("Z")
    if "T" in value:
        value = value.split("T", 1)[0]
    value = value.replace("/", "-")
    return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
"""

TASK_277 = Task(
    id="task_277_swe_date_parser",
    name="Fix date parser accepted formats",
    tags=("swe-bench", "python", "pytest", "bugfix", "hard"),
    prompt=(
        "Почини dates.py: parse_date должна принимать YYYY-MM-DD,"
        " YYYY/MM/DD и ISO datetime с суффиксом Z, возвращая дату в"
        " формате YYYY-MM-DD. Тесты менять нельзя."
    ),
    setup_files={"dates.py": _DATES_PY_277, "tests/test_dates.py": _TEST_DATES_277},
    gold_files={"dates.py": _DATES_GOLD_277},
    verifier=pytest_passes("tests"),
)


_QUERY_PY_278 = """\
def parse_query(query: str) -> dict[str, str]:
    result = {}
    for pair in query.split("&"):
        key, value = pair.split("=")
        result[key] = value
    return result
"""

_TEST_QUERY_278 = """\
from query import parse_query


def test_decodes_percent_encoding_and_plus():
    assert parse_query("q=hello+world&city=Sankt%20Petersburg") == {
        "q": "hello world",
        "city": "Sankt Petersburg",
    }


def test_repeated_keys_become_lists_and_blank_values_stay():
    assert parse_query("tag=ai&tag=bench&empty=") == {
        "tag": ["ai", "bench"],
        "empty": "",
    }
"""

_QUERY_GOLD_278 = """\
from urllib.parse import parse_qs


def parse_query(query: str) -> dict[str, str | list[str]]:
    parsed = parse_qs(query, keep_blank_values=True)
    return {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}
"""

TASK_278 = Task(
    id="task_278_swe_query_parser",
    name="Fix query-string parsing edge cases",
    tags=("swe-bench", "python", "pytest", "bugfix", "hard"),
    prompt=(
        "Почини query.py: parse_query должен декодировать percent-encoding,"
        " заменять + на пробел, сохранять blank values и повторяющиеся ключи"
        " возвращать списком значений. Тесты менять нельзя."
    ),
    setup_files={"query.py": _QUERY_PY_278, "tests/test_query.py": _TEST_QUERY_278},
    gold_files={"query.py": _QUERY_GOLD_278},
    verifier=pytest_passes("tests"),
)


_SLUGS_PY_279 = """\
import re


def heading_slug(title: str, existing: set[str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    existing.add(slug)
    return slug
"""

_TEST_SLUGS_279 = """\
from slugs import heading_slug


def test_duplicate_slugs_get_numeric_suffix():
    existing = set()
    assert heading_slug("Hello, World!", existing) == "hello-world"
    assert heading_slug("Hello World", existing) == "hello-world-2"
    assert heading_slug("Hello   World", existing) == "hello-world-3"


def test_empty_heading_uses_section():
    existing = set()
    assert heading_slug("!!!", existing) == "section"
"""

_SLUGS_GOLD_279 = """\
import re


def heading_slug(title: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "section"
    slug = base
    counter = 2
    while slug in existing:
        slug = f"{base}-{counter}"
        counter += 1
    existing.add(slug)
    return slug
"""

TASK_279 = Task(
    id="task_279_swe_markdown_slug_duplicates",
    name="Fix duplicate markdown heading slugs",
    tags=("swe-bench", "python", "pytest", "bugfix", "hard"),
    prompt=(
        "Почини slugs.py: duplicate slugs должны получать суффиксы -2, -3,"
        " а пустой после нормализации заголовок должен стать section."
        " Тесты менять нельзя."
    ),
    setup_files={"slugs.py": _SLUGS_PY_279, "tests/test_slugs.py": _TEST_SLUGS_279},
    gold_files={"slugs.py": _SLUGS_GOLD_279},
    verifier=pytest_passes("tests"),
)


_PAGING_PY_280 = """\
def paginate(items: list, page: int, per_page: int) -> list:
    start = page * per_page
    end = start + per_page
    return items[start:end]
"""

_TEST_PAGING_280 = """\
import pytest

from paging import paginate


def test_pages_are_one_based():
    assert paginate([1, 2, 3, 4, 5], page=1, per_page=2) == [1, 2]
    assert paginate([1, 2, 3, 4, 5], page=3, per_page=2) == [5]


def test_invalid_arguments():
    with pytest.raises(ValueError):
        paginate([1], page=0, per_page=10)
    with pytest.raises(ValueError):
        paginate([1], page=1, per_page=0)
"""

_PAGING_GOLD_280 = """\
def paginate(items: list, page: int, per_page: int) -> list:
    if page < 1:
        raise ValueError("page must be >= 1")
    if per_page < 1:
        raise ValueError("per_page must be >= 1")
    start = (page - 1) * per_page
    end = start + per_page
    return items[start:end]
"""

TASK_280 = Task(
    id="task_280_swe_pagination_one_based",
    name="Fix one-based pagination",
    tags=("swe-bench", "python", "pytest", "bugfix", "medium"),
    prompt=(
        "Почини paging.py: page должен быть one-based, page/per_page меньше"
        " 1 должны вызывать ValueError. Тесты менять нельзя."
    ),
    setup_files={"paging.py": _PAGING_PY_280, "tests/test_paging.py": _TEST_PAGING_280},
    gold_files={"paging.py": _PAGING_GOLD_280},
    verifier=pytest_passes("tests"),
)


_RETRY_PY_281 = """\
def retry_delays(base: int, attempts: int, cap: int) -> list[int]:
    delays = []
    delay = base
    for _ in range(attempts):
        delays.append(delay)
        delay *= 2
    return delays
"""

_TEST_RETRY_281 = """\
import pytest

from retry import retry_delays


def test_exponential_delays_are_capped():
    assert retry_delays(base=2, attempts=5, cap=10) == [2, 4, 8, 10, 10]


def test_zero_attempts_and_invalid_values():
    assert retry_delays(base=2, attempts=0, cap=10) == []
    with pytest.raises(ValueError):
        retry_delays(base=0, attempts=1, cap=10)
"""

_RETRY_GOLD_281 = """\
def retry_delays(base: int, attempts: int, cap: int) -> list[int]:
    if base <= 0 or attempts < 0 or cap <= 0:
        raise ValueError("base, attempts, and cap must be positive")
    delays = []
    delay = base
    for _ in range(attempts):
        delays.append(min(delay, cap))
        delay *= 2
    return delays
"""

TASK_281 = Task(
    id="task_281_swe_retry_backoff_cap",
    name="Fix capped retry backoff",
    tags=("swe-bench", "python", "pytest", "bugfix", "medium"),
    prompt=(
        "Почини retry.py: retry_delays должен применять cap к каждому"
        " delay, поддерживать attempts=0 и валидировать положительные base/cap."
        " Тесты менять нельзя."
    ),
    setup_files={"retry.py": _RETRY_PY_281, "tests/test_retry.py": _TEST_RETRY_281},
    gold_files={"retry.py": _RETRY_GOLD_281},
    verifier=pytest_passes("tests"),
)


_PATHS_PY_282 = """\
def safe_join(root: str, user_path: str) -> str:
    return root + "/" + user_path
"""

_TEST_PATHS_282 = """\
import os
import pytest

from paths import safe_join


def test_normalizes_inside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert safe_join(str(root), "a/../b.txt") == os.path.join(str(root), "b.txt")


def test_blocks_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError):
        safe_join(str(root), "../secret.txt")
"""

_PATHS_GOLD_282 = """\
import os


def safe_join(root: str, user_path: str) -> str:
    root_abs = os.path.abspath(root)
    target = os.path.abspath(os.path.join(root_abs, user_path))
    if os.path.commonpath([root_abs, target]) != root_abs:
        raise ValueError("path escapes root")
    return target
"""

TASK_282 = Task(
    id="task_282_swe_safe_join",
    name="Fix safe path join",
    tags=("swe-bench", "python", "pytest", "bugfix", "hard"),
    prompt=(
        "Почини paths.py: safe_join должен нормализовать путь внутри root и"
        " выбрасывать ValueError, если user_path выходит за пределы root."
        " Тесты менять нельзя."
    ),
    setup_files={"paths.py": _PATHS_PY_282, "tests/test_paths.py": _TEST_PATHS_282},
    gold_files={"paths.py": _PATHS_GOLD_282},
    verifier=pytest_passes("tests"),
)


_INVENTORY_PY_283 = """\
def allocate(stock: dict[str, int], order: dict[str, int]) -> dict[str, int]:
    allocated = {}
    for sku, qty in order.items():
        allocated[sku] = qty
        stock[sku] -= qty
    return allocated
"""

_TEST_INVENTORY_283 = """\
import pytest

from inventory import allocate


def test_allocates_without_mutating_on_success():
    stock = {"a": 5, "b": 1}
    result = allocate(stock, {"a": 3})
    assert result == {"a": 3}
    assert stock == {"a": 5, "b": 1}


def test_rejects_missing_or_insufficient_stock():
    with pytest.raises(ValueError):
        allocate({"a": 1}, {"a": 2})
    with pytest.raises(ValueError):
        allocate({"a": 1}, {"b": 1})
"""

_INVENTORY_GOLD_283 = """\
def allocate(stock: dict[str, int], order: dict[str, int]) -> dict[str, int]:
    for sku, qty in order.items():
        if stock.get(sku, 0) < qty:
            raise ValueError(f"insufficient stock for {sku}")
    return dict(order)
"""

TASK_283 = Task(
    id="task_283_swe_inventory_allocate",
    name="Fix inventory allocation validation",
    tags=("swe-bench", "python", "pytest", "bugfix", "medium"),
    prompt=(
        "Почини inventory.py: allocate должен проверить наличие всех SKU и"
        " достаточный остаток, вернуть allocation, но не мутировать stock."
        " При нехватке выбросить ValueError. Тесты менять нельзя."
    ),
    setup_files={
        "inventory.py": _INVENTORY_PY_283,
        "tests/test_inventory.py": _TEST_INVENTORY_283,
    },
    gold_files={"inventory.py": _INVENTORY_GOLD_283},
    verifier=pytest_passes("tests"),
)


# ---------------------------------------------------------------------------
# 284..266. more Terminal-Bench-style tasks
# ---------------------------------------------------------------------------
_PORTS_284 = {
    "services/api/config.json": '{\n  "service": "api",\n  "port": 8080,\n  "enabled": true\n}\n',
    "services/worker/config.json": (
        '{\n  "service": "worker",\n  "port": 9090,\n  "enabled": false\n}\n'
    ),
    "services/web/config.json": '{\n  "service": "web",\n  "port": 3000,\n  "enabled": true\n}\n',
}

_PORTS_REPORT_284 = """\
api	8080
web	3000
"""

TASK_284 = Task(
    id="task_284_terminal_json_config_inventory",
    name="Inventory enabled service ports from JSON configs",
    tags=("terminal-bench", "json", "config", "hard"),
    prompt=(
        "В services/*/config.json лежат конфиги сервисов с полями service,"
        " port, enabled. Создай enabled_ports.tsv: только enabled=true,"
        " формат '<service>\\t<port>', сортировка по service."
    ),
    setup_files=_PORTS_284,
    gold_files={"enabled_ports.tsv": _PORTS_REPORT_284},
    verifier=file_lines_equal("enabled_ports.tsv", _PORTS_REPORT_284.strip().splitlines()),
)


_PATCH_SUMMARY_285 = """\
files_changed	3
insertions	7
deletions	3
"""

TASK_285 = Task(
    id="task_285_terminal_patch_stat_summary",
    name="Summarize git patch statistics",
    tags=("terminal-bench", "patch", "text", "hard"),
    prompt=(
        "В patch.diff лежит unified diff. Посчитай files_changed,"
        " insertions и deletions. Строки заголовков diff (+++ и ---) не"
        " считать как insertions/deletions. Создай patch_summary.tsv ровно"
        " с тремя строками: files_changed, insertions, deletions."
    ),
    setup_files={
        "patch.diff": (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,5 @@\n"
            " line\n-old\n+new\n+extra\n"
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1,2 +1,4 @@\n"
            "-x\n+y\n+z\n+q\n"
            "diff --git a/c.py b/c.py\n--- a/c.py\n+++ b/c.py\n@@ -1,3 +1,4 @@\n"
            "-drop\n keep\n+add\n+more\n"
        )
    },
    gold_files={"patch_summary.tsv": _PATCH_SUMMARY_285},
    verifier=file_lines_equal("patch_summary.tsv", _PATCH_SUMMARY_285.strip().splitlines()),
)


_NGINX_286 = """\
10.0.0.1 - - [25/May/2026:10:00:00 +0000] "GET /api/items HTTP/1.1" 200 12
10.0.0.2 - - [25/May/2026:10:00:01 +0000] "GET /api/items HTTP/1.1" 499 0
10.0.0.3 - - [25/May/2026:10:00:02 +0000] "POST /api/cart HTTP/1.1" 201 44
10.0.0.4 - - [25/May/2026:10:00:03 +0000] "GET /api/items HTTP/1.1" 502 20
10.0.0.5 - - [25/May/2026:10:00:04 +0000] "POST /api/cart HTTP/1.1" 500 30
"""

_NGINX_REPORT_286 = """\
/api/cart	1	1
/api/items	1	2
"""

TASK_286 = Task(
    id="task_286_terminal_nginx_endpoint_classes",
    name="Classify nginx endpoint successes and failures",
    tags=("terminal-bench", "logs", "text", "hard"),
    prompt=(
        "Прочитай nginx.log. Для каждого endpoint посчитай success"
        " (status 200-399) и failure (все остальные). Создай"
        " endpoint_classes.tsv с колонками endpoint, success, failure без"
        " заголовка, сортировка по endpoint."
    ),
    setup_files={"nginx.log": _NGINX_286},
    gold_files={"endpoint_classes.tsv": _NGINX_REPORT_286},
    verifier=file_lines_equal("endpoint_classes.tsv", _NGINX_REPORT_286.strip().splitlines()),
)


_RELEASE_NOTES_287 = """\
## Added
- Add invoice export
- Add retry metrics
## Fixed
- Fix cache invalidation
- Fix CSV quoting
"""

TASK_287 = Task(
    id="task_287_terminal_changelog_extract",
    name="Extract release note sections from changelog",
    tags=("terminal-bench", "markdown", "text", "medium"),
    prompt=(
        "В CHANGELOG.md найди секцию версии 1.2.0 и создай"
        " release_notes.md, содержащий только подразделы Added и Fixed"
        " этой версии вместе с bullet-строками. Заголовки подразделов в"
        " выводе оформи уровнем ## (в исходнике они уровня ###)."
        " Не включай текст версии 1.1.0."
    ),
    setup_files={
        "CHANGELOG.md": (
            "# Changelog\n\n"
            "## 1.2.0\n\n"
            "### Added\n- Add invoice export\n- Add retry metrics\n\n"
            "### Fixed\n- Fix cache invalidation\n- Fix CSV quoting\n\n"
            "## 1.1.0\n\n### Added\n- Old feature\n"
        )
    },
    gold_files={"release_notes.md": _RELEASE_NOTES_287},
    verifier=file_lines_equal("release_notes.md", _RELEASE_NOTES_287.strip().splitlines()),
)


_ARTIFACTS_288 = """\
artifacts/app-linux.tar.gz
artifacts/app-macos.tar.gz
artifacts/app-windows.zip
"""

TASK_288 = Task(
    id="task_288_terminal_artifact_filter",
    name="Filter release artifacts by manifest flags",
    tags=("terminal-bench", "json", "text", "hard"),
    prompt=(
        "В artifacts.json список артефактов с fields path, type, draft."
        " Создай publish_artifacts.txt: path для type='binary' и draft=false,"
        " сортировка лексикографически, по одному пути в строке."
    ),
    setup_files={
        "artifacts.json": json.dumps(
            [
                {"path": "artifacts/app-linux.tar.gz", "type": "binary", "draft": False},
                {"path": "artifacts/app-macos.tar.gz", "type": "binary", "draft": False},
                {"path": "artifacts/debug-symbols.zip", "type": "symbols", "draft": False},
                {"path": "artifacts/app-windows.zip", "type": "binary", "draft": False},
                {"path": "artifacts/app-beta.zip", "type": "binary", "draft": True},
            ],
            indent=2,
        )
        + "\n"
    },
    gold_files={"publish_artifacts.txt": _ARTIFACTS_288},
    verifier=file_lines_equal("publish_artifacts.txt", _ARTIFACTS_288.strip().splitlines()),
)


# ---------------------------------------------------------------------------
# 289..271. more tau2-style policy tasks
# ---------------------------------------------------------------------------
_UTILITY_ACTION_289 = {
    "action": "create_payment_plan",
    "account_id": "util-41",
    "installments": 3,
    "down_payment_usd": 60,
    "avoid_disconnect": True,
}

TASK_289 = Task(
    id="task_289_tau2_utility_payment_plan",
    name="Choose utility payment-plan action",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "Клиент просит избежать отключения. По utility_account.json и"
        " policy.md создай utility_action.json с action, account_id,"
        " installments, down_payment_usd, avoid_disconnect. Значение action —"
        " create_payment_plan. Если долг меньше"
        " 500 и есть 20% down payment, разрешен план на 3 платежа."
    ),
    setup_files={
        "utility_account.json": json.dumps(
            {"account_id": "util-41", "past_due_usd": 300, "offered_down_payment_usd": 60},
            indent=2,
        )
        + "\n",
        "policy.md": "- Debt <500 with 20% down payment qualifies for a 3 installment plan.\n",
    },
    gold_files={"utility_action.json": json.dumps(_UTILITY_ACTION_289, indent=2) + "\n"},
    verifier=_json_equals("utility_action.json", _UTILITY_ACTION_289),
)


_CAR_ACTION_290 = {
    "action": "charge_cleaning_fee",
    "rental_id": "car-58",
    "fee_usd": 75,
    "reason": "pet hair reported without pet add-on",
}

TASK_290 = Task(
    id="task_290_tau2_car_rental_fee",
    name="Apply car rental cleaning-fee policy",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "По rental.json и policy.md создай car_action.json с action,"
        " rental_id, fee_usd, reason. Значение action — charge_cleaning_fee."
        " Если найден pet_hair и pet_addon=false,"
        " нужно начислить cleaning fee 75 USD."
    ),
    setup_files={
        "rental.json": json.dumps(
            {"rental_id": "car-58", "inspection": ["pet_hair"], "pet_addon": False},
            indent=2,
        )
        + "\n",
        "policy.md": "- Pet hair without pet add-on incurs a 75 USD cleaning fee.\n",
    },
    gold_files={"car_action.json": json.dumps(_CAR_ACTION_290, indent=2) + "\n"},
    verifier=_json_equals_fuzzy("car_action.json", _CAR_ACTION_290, free_text_fields=("reason",)),
)


_EDU_ACTION_291 = {
    "action": "grant_extension",
    "student_id": "stu-204",
    "assignment_id": "essay-7",
    "new_due_date": "2026-05-30",
    "penalty_percent": 0,
}

TASK_291 = Task(
    id="task_291_tau2_student_extension",
    name="Grant a student assignment extension",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "Студент просит extension из-за подтвержденной болезни. По"
        " request.json и policy.md создай edu_action.json с action,"
        " student_id, assignment_id, new_due_date, penalty_percent."
        " Значение action — grant_extension."
        " Подтвержденная болезнь дает 5 дней без штрафа."
    ),
    setup_files={
        "request.json": json.dumps(
            {
                "student_id": "stu-204",
                "assignment_id": "essay-7",
                "due_date": "2026-05-25",
                "reason": "illness",
                "documented": True,
            },
            indent=2,
        )
        + "\n",
        "policy.md": "- Documented illness grants a 5 day extension with 0 penalty.\n",
    },
    gold_files={"edu_action.json": json.dumps(_EDU_ACTION_291, indent=2) + "\n"},
    verifier=_json_equals("edu_action.json", _EDU_ACTION_291),
)


_PHARMACY_ACTION_292 = {
    "action": "refuse_refill",
    "prescription_id": "rx-900",
    "days_until_eligible": 4,
    "reason": "controlled medication refill requested too early",
}

TASK_292 = Task(
    id="task_292_tau2_pharmacy_refill",
    name="Refuse an early controlled refill",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "Пациент просит refill. По prescription.json и policy.md создай"
        " pharmacy_action.json с action, prescription_id, days_until_eligible,"
        " reason. Controlled medication можно refill не раньше чем за 2 дня"
        " до окончания supply; если рефилл запрошен слишком рано, значение"
        " action — refuse_refill."
    ),
    setup_files={
        "prescription.json": json.dumps(
            {"prescription_id": "rx-900", "controlled": True, "days_supply_left": 6},
            indent=2,
        )
        + "\n",
        "policy.md": "- Controlled refills are eligible when days_supply_left <= 2.\n",
    },
    gold_files={"pharmacy_action.json": json.dumps(_PHARMACY_ACTION_292, indent=2) + "\n"},
    verifier=_json_equals_fuzzy(
        "pharmacy_action.json", _PHARMACY_ACTION_292, free_text_fields=("reason",)
    ),
)


_EVENT_ACTION_293 = {
    "action": "exchange_ticket",
    "ticket_id": "T-55",
    "new_event_id": "show-b",
    "price_delta_usd": 15,
}

TASK_293 = Task(
    id="task_293_tau2_event_ticket_exchange",
    name="Exchange an event ticket under policy",
    tags=("tau2", "policy", "json", "hard"),
    prompt=(
        "Клиент хочет обменять билет на show-b. По ticket.json,"
        " events.json и policy.md создай event_action.json с action,"
        " ticket_id, new_event_id, price_delta_usd. Значение action —"
        " exchange_ticket. Обмен разрешен если"
        " до события больше 24 часов; price_delta = new_price - old_price."
    ),
    setup_files={
        "ticket.json": json.dumps(
            {"ticket_id": "T-55", "event_id": "show-a", "hours_until_event": 30, "price_usd": 45},
            indent=2,
        )
        + "\n",
        "events.json": json.dumps({"show-b": {"price_usd": 60}}, indent=2) + "\n",
        "policy.md": "- Exchanges are allowed more than 24 hours before the event.\n",
    },
    gold_files={"event_action.json": json.dumps(_EVENT_ACTION_293, indent=2) + "\n"},
    verifier=_json_equals("event_action.json", _EVENT_ACTION_293),
)


# ---------------------------------------------------------------------------
# 294..276. more SWE-bench-style pytest bug fixes
# ---------------------------------------------------------------------------
_MONEY_PY_294 = """\
def cents(value: str) -> int:
    return int(float(value) * 100)
"""

_TEST_MONEY_294 = """\
from money import cents


def test_decimal_money_is_exact():
    assert cents("10.25") == 1025
    assert cents("0.29") == 29


def test_currency_symbols_and_commas():
    assert cents("$1,234.50") == 123450
"""

_MONEY_GOLD_294 = """\
from decimal import Decimal


def cents(value: str) -> int:
    cleaned = value.strip().replace("$", "").replace(",", "")
    return int(Decimal(cleaned) * 100)
"""

TASK_294 = Task(
    id="task_294_swe_money_cents",
    name="Fix exact money-to-cents parsing",
    tags=("swe-bench", "python", "pytest", "bugfix", "hard"),
    prompt=(
        "Почини money.py: cents должен точно парсить decimal money,"
        " поддерживать $ и запятые-разделители тысяч. Не используй float для"
        " финального расчета. Тесты менять нельзя."
    ),
    setup_files={"money.py": _MONEY_PY_294, "tests/test_money.py": _TEST_MONEY_294},
    gold_files={"money.py": _MONEY_GOLD_294},
    verifier=pytest_passes("tests"),
)


_TAGS_PY_295 = """\
def parse_tags(raw: str) -> list[str]:
    return raw.split(",")
"""

_TEST_TAGS_295 = """\
from tags import parse_tags


def test_trims_dedupes_and_sorts():
    assert parse_tags(" beta,alpha,, beta ,Gamma ") == ["alpha", "beta", "gamma"]


def test_empty_returns_empty_list():
    assert parse_tags(" , ") == []
"""

_TAGS_GOLD_295 = """\
def parse_tags(raw: str) -> list[str]:
    tags = {part.strip().lower() for part in raw.split(",") if part.strip()}
    return sorted(tags)
"""

TASK_295 = Task(
    id="task_295_swe_parse_tags",
    name="Fix tag parsing normalization",
    tags=("swe-bench", "python", "pytest", "bugfix", "medium"),
    prompt=(
        "Почини tags.py: parse_tags должен trim, lower-case, удалить пустые"
        " элементы, дедуплицировать и вернуть отсортированный список. Тесты"
        " менять нельзя."
    ),
    setup_files={"tags.py": _TAGS_PY_295, "tests/test_tags.py": _TEST_TAGS_295},
    gold_files={"tags.py": _TAGS_GOLD_295},
    verifier=pytest_passes("tests"),
)


_ROMAN_PY_296 = """\
def roman_to_int(value: str) -> int:
    table = {"I": 1, "V": 5, "X": 10, "L": 50}
    total = 0
    for ch in value:
        total += table[ch]
    return total
"""

_TEST_ROMAN_296 = """\
import pytest

from roman import roman_to_int


def test_subtractive_notation():
    assert roman_to_int("IV") == 4
    assert roman_to_int("IX") == 9
    assert roman_to_int("XLII") == 42


def test_rejects_invalid_symbols():
    with pytest.raises(ValueError):
        roman_to_int("A")
"""

_ROMAN_GOLD_296 = """\
def roman_to_int(value: str) -> int:
    table = {"I": 1, "V": 5, "X": 10, "L": 50}
    total = 0
    previous = 0
    for ch in reversed(value):
        if ch not in table:
            raise ValueError(f"invalid Roman symbol: {ch}")
        current = table[ch]
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total
"""

TASK_296 = Task(
    id="task_296_swe_roman_parser",
    name="Fix Roman numeral parsing",
    tags=("swe-bench", "python", "pytest", "bugfix", "medium"),
    prompt=(
        "Почини roman.py: roman_to_int должен поддерживать subtractive"
        " notation (IV, IX, XL) и выбрасывать ValueError для неизвестных"
        " символов. Тесты менять нельзя."
    ),
    setup_files={"roman.py": _ROMAN_PY_296, "tests/test_roman.py": _TEST_ROMAN_296},
    gold_files={"roman.py": _ROMAN_GOLD_296},
    verifier=pytest_passes("tests"),
)


_AUTH_PY_297 = """\
def has_role(user: dict, role: str) -> bool:
    return role in user["roles"]


def can_access(user: dict, resource: dict) -> bool:
    return has_role(user, resource["required_role"])
"""

_TEST_AUTH_297 = """\
from authz import can_access


def test_admin_bypasses_required_role():
    assert can_access({"roles": ["admin"]}, {"required_role": "billing"})


def test_missing_roles_is_safe_false():
    assert not can_access({}, {"required_role": "billing"})
"""

_AUTH_GOLD_297 = """\
def has_role(user: dict, role: str) -> bool:
    return role in user.get("roles", [])


def can_access(user: dict, resource: dict) -> bool:
    return has_role(user, "admin") or has_role(user, resource["required_role"])
"""

TASK_297 = Task(
    id="task_297_swe_authz_admin",
    name="Fix authorization role checks",
    tags=("swe-bench", "python", "pytest", "bugfix", "hard"),
    prompt=(
        "Почини authz.py: admin должен иметь доступ к любому ресурсу, а"
        " отсутствие roles у user должно безопасно возвращать False, не"
        " KeyError. Тесты менять нельзя."
    ),
    setup_files={"authz.py": _AUTH_PY_297, "tests/test_authz.py": _TEST_AUTH_297},
    gold_files={"authz.py": _AUTH_GOLD_297},
    verifier=pytest_passes("tests"),
)


_STATS_PY_298 = """\
def median(values: list[float]) -> float:
    values = sorted(values)
    return values[len(values) // 2]
"""

_TEST_STATS_298 = """\
import pytest

from stats import median


def test_odd_and_even_lengths():
    assert median([3, 1, 2]) == 2
    assert median([10, 2, 4, 8]) == 6


def test_empty_raises_value_error():
    with pytest.raises(ValueError):
        median([])
"""

_STATS_GOLD_298 = """\
def median(values: list[float]) -> float:
    if not values:
        raise ValueError("median of empty list")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2
"""

TASK_298 = Task(
    id="task_298_swe_median_even_empty",
    name="Fix median for even and empty inputs",
    tags=("swe-bench", "python", "pytest", "bugfix", "medium"),
    prompt=(
        "Почини stats.py: median должен работать для нечетной и четной длины,"
        " а для пустого списка выбрасывать ValueError. Тесты менять нельзя."
    ),
    setup_files={"stats.py": _STATS_PY_298, "tests/test_stats.py": _TEST_STATS_298},
    gold_files={"stats.py": _STATS_GOLD_298},
    verifier=pytest_passes("tests"),
)


AGENTIC_TASKS: list[Task] = [
    TASK_254,
    TASK_255,
    TASK_256,
    TASK_257,
    TASK_258,
    TASK_259,
    TASK_260,
    TASK_261,
    TASK_262,
    TASK_263,
    TASK_264,
    TASK_265,
    TASK_266,
    TASK_267,
    TASK_268,
    TASK_269,
    TASK_270,
    TASK_271,
    TASK_272,
    TASK_273,
    TASK_274,
    TASK_275,
    TASK_276,
    TASK_277,
    TASK_278,
    TASK_279,
    TASK_280,
    TASK_281,
    TASK_282,
    TASK_283,
    TASK_284,
    TASK_285,
    TASK_286,
    TASK_287,
    TASK_288,
    TASK_289,
    TASK_290,
    TASK_291,
    TASK_292,
    TASK_293,
    TASK_294,
    TASK_295,
    TASK_296,
    TASK_297,
    TASK_298,
]
