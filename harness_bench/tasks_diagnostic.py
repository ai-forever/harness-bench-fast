"""Diagnostic hard tasks 206..221.

These tasks are intentionally "signal-rich": when they fail, verifier messages
should clearly tell *what kind* of mistake happened (schema mismatch, sorting,
rounding, missing files, logic bugs, etc.).
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tarfile
import tomllib
import zipfile
from pathlib import Path

import yaml

from harness_bench.core import Task, VerifyResult
from harness_bench.verifiers import (
    all_of,
    file_contains,
    file_exists,
    file_not_contains,
    file_text_equals,
    pytest_passes,
)


def _json_file_matches_loose(rel: str, expected, *, ordered: bool = False):
    """Compare JSON file to expected with optional order-insensitive list-of-dicts."""

    def _canon(v):
        if isinstance(v, list):
            return [_canon(x) for x in v]
        if isinstance(v, dict):
            return {k: _canon(x) for k, x in v.items()}
        return v

    def _norm(v):
        if not ordered and isinstance(v, list) and all(isinstance(x, dict) for x in v):
            return sorted([_canon(x) for x in v], key=lambda d: json.dumps(d, sort_keys=True))
        return _canon(v)

    def _check(ws: Path) -> VerifyResult:
        p = ws / rel
        if not p.exists():
            return VerifyResult(False, f"{rel} missing")
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as exc:
            return VerifyResult(False, f"{rel} invalid JSON: {exc}")
        if _norm(data) == _norm(expected):
            return VerifyResult(True, f"{rel} matches expected JSON")
        return VerifyResult(False, f"{rel} JSON mismatch\nGot: {data!r}\nExp: {expected!r}")

    return _check


def _verify_csv_rows(path: Path, expected_header: list[str], expected_rows: list[list[str]]) -> VerifyResult:
    if not path.exists():
        return VerifyResult(False, f"{path.name} missing")
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if not lines:
        return VerifyResult(False, f"{path.name} is empty")
    header = [x.strip() for x in lines[0].split(",")]
    if header != expected_header:
        return VerifyResult(False, f"{path.name} header {header!r} != {expected_header!r}")
    rows = [[x.strip() for x in line.split(",")] for line in lines[1:]]
    if rows != expected_rows:
        return VerifyResult(False, f"{path.name} rows differ\nGot: {rows!r}\nExp: {expected_rows!r}")
    return VerifyResult(True, f"{path.name} matches expected rows")


# 206. Multi-source reconciliation
_CUSTOMERS_206 = (
    "user_id,email,country,tier\n"
    "1,alice@example.com,US,gold\n"
    "2,bob@example.com,DE,silver\n"
    "3,carol@example.com,RU,gold\n"
    "4,dave@example.com,US,bronze\n"
    "5,erin@example.com,CN,gold\n"
    "6,fraud@example.com,NK,bronze\n"
)
_ORDERS_206 = [
    {"user_id": 1, "amount": 120, "currency": "USD", "status": "paid"},
    {"user_id": 1, "amount": 100, "currency": "EUR", "status": "paid"},
    {"user_id": 1, "amount": 50, "currency": "USD", "status": "refund"},
    {"user_id": 2, "amount": 200, "currency": "USD", "status": "paid"},
    {"user_id": 2, "amount": 100, "currency": "EUR", "status": "paid"},
    {"user_id": 3, "amount": 30000, "currency": "RUB", "status": "paid"},
    {"user_id": 4, "amount": 150, "currency": "USD", "status": "paid"},
    {"user_id": 4, "amount": 160, "currency": "USD", "status": "paid"},
    {"user_id": 5, "amount": 400, "currency": "USD", "status": "failed"},
    {"user_id": 6, "amount": 1000, "currency": "USD", "status": "paid"},
]
_ORDERS_206_JSONL = "".join(json.dumps(x) + "\n" for x in _ORDERS_206)
_FX_206 = {"USD": 1.0, "EUR": 1.2, "RUB": 0.01}
_BLACKLIST_COUNTRIES_206 = "NK\n"
_REPORT_206_ROWS = [
    ["2", "silver", "320.00", "2"],
    ["1", "gold", "240.00", "2"],
    ["4", "bronze", "310.00", "2"],
    ["3", "gold", "300.00", "1"],
]
_REPORT_206_ROWS = sorted(_REPORT_206_ROWS, key=lambda r: (-float(r[2]), r[0]))


def _verify_task_206(ws: Path) -> VerifyResult:
    return _verify_csv_rows(
        ws / "vip_revenue.csv",
        ["user_id", "tier", "paid_usd", "paid_orders"],
        _REPORT_206_ROWS,
    )


TASK_206 = Task(
    id="task_206_reconcile_paid_revenue",
    name="Reconcile CSV+JSONL revenue into vip_revenue.csv",
    tags=("csv", "jsonl", "pipeline", "execute", "hard"),
    prompt=(
        "Есть файлы customers.csv, orders.jsonl, fx_rates.json и blocked_countries.txt.\n"
        "Построй vip_revenue.csv с колонками user_id,tier,paid_usd,paid_orders:\n"
        "  - учитывать только заказы со status='paid';\n"
        "  - amount конвертировать в USD через fx_rates.json;\n"
        "  - пользователей из стран из blocked_countries.txt исключить;\n"
        "  - агрегировать сумму paid_usd и количество paid_orders по user_id;\n"
        "  - включать только пользователей с paid_usd >= 200;\n"
        "  - paid_usd форматировать как строку с ровно двумя знаками после\n"
        "    точки (например '320.00', а не '320.0'); сортировать по paid_usd\n"
        "    desc, при равенстве по user_id asc."
    ),
    setup_files={
        "customers.csv": _CUSTOMERS_206,
        "orders.jsonl": _ORDERS_206_JSONL,
        "fx_rates.json": json.dumps(_FX_206) + "\n",
        "blocked_countries.txt": _BLACKLIST_COUNTRIES_206,
    },
    gold_files={
        "vip_revenue.csv": (
            "user_id,tier,paid_usd,paid_orders\n"
            + "".join(",".join(row) + "\n" for row in _REPORT_206_ROWS)
        ),
    },
    verifier=_verify_task_206,
)


# 207. SQLite reconciliation with adjustments
def _sqlite_207_setup(ws: Path) -> None:
    conn = sqlite3.connect(ws / "inventory.db")
    conn.execute("CREATE TABLE stock (sku TEXT PRIMARY KEY, qty INTEGER)")
    conn.executemany(
        "INSERT INTO stock VALUES (?, ?)",
        [("A", 10), ("B", 5), ("C", 0), ("D", 3)],
    )
    conn.commit()
    conn.close()


_ADJUST_207 = (
    "sku,delta\n"
    "A,-4\n"
    "B,-10\n"
    "C,3\n"
    "X,8\n"
    "D,-1\n"
)
_ANOMALY_207 = {
    "negative_stock": [{"sku": "B", "final_qty": -5}],
    "unknown_sku": ["X"],
}


def _verify_task_207(ws: Path) -> VerifyResult:
    p = ws / "anomalies.json"
    if not p.exists():
        return VerifyResult(False, "anomalies.json missing")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"anomalies.json invalid JSON: {exc}")
    got_neg = sorted(data.get("negative_stock", []), key=lambda x: x.get("sku", ""))
    exp_neg = sorted(_ANOMALY_207["negative_stock"], key=lambda x: x["sku"])
    got_unknown = sorted(data.get("unknown_sku", []))
    exp_unknown = sorted(_ANOMALY_207["unknown_sku"])
    if got_neg != exp_neg or got_unknown != exp_unknown:
        return VerifyResult(
            False,
            f"anomalies mismatch: negative={got_neg!r} unknown={got_unknown!r}; expected {exp_neg!r} and {exp_unknown!r}",
        )
    return VerifyResult(True, "anomalies.json contains expected negative and unknown SKU sections")


TASK_207 = Task(
    id="task_207_inventory_anomaly_report",
    name="Apply CSV deltas to sqlite stock and report anomalies",
    tags=("sqlite", "csv", "pipeline", "execute", "hard"),
    prompt=(
        "В inventory.db есть таблица stock(sku,qty). В adjustments.csv лежат sku,delta.\n"
        "Нужно применить дельты и сформировать anomalies.json:\n"
        "  - negative_stock: список объектов {sku, final_qty} для SKU, у которых"
        "    итоговый qty < 0;\n"
        "  - unknown_sku: список SKU из adjustments.csv, которых нет в stock.\n"
        "Итоговый JSON должен содержать оба ключа."
    ),
    setup_files={"adjustments.csv": _ADJUST_207},
    setup_callback=_sqlite_207_setup,
    gold_files={"anomalies.json": json.dumps(_ANOMALY_207) + "\n"},
    verifier=_verify_task_207,
)


# 208. Multi-file API refactor with tests
_SETUP_208 = {
    "pricing/legacy.py": (
        "def calc_total(subtotal, tax_rate):\n"
        "    return round(subtotal * (1 + tax_rate), 2)\n"
    ),
    "app/service.py": (
        "from pricing.legacy import calc_total\n"
        "\n"
        "\n"
        "def quote(lines):\n"
        "    subtotal = sum(item['qty'] * item['price'] for item in lines)\n"
        "    return calc_total(subtotal, 0.2)\n"
    ),
    "cli.py": (
        "from pricing.legacy import calc_total\n"
        "\n"
        "\n"
        "def render_quote(lines):\n"
        "    subtotal = sum(item['qty'] * item['price'] for item in lines)\n"
        "    return f'Total={calc_total(subtotal, 0.2)}'\n"
    ),
    "tests/test_pricing_refactor.py": (
        "from app.service import quote\n"
        "from cli import render_quote\n"
        "from core.pricing import compute_total\n"
        "\n"
        "\n"
        "def _rows():\n"
        "    return [\n"
        "        {'qty': 2, 'price': 10},\n"
        "        {'qty': 1, 'price': 5},\n"
        "    ]\n"
        "\n"
        "\n"
        "def test_compute_total():\n"
        "    assert compute_total(_rows(), tax=0.2) == 30.0\n"
        "\n"
        "\n"
        "def test_service_and_cli_use_new_api():\n"
        "    assert quote(_rows()) == 30.0\n"
        "    assert render_quote(_rows()) == 'Total=30.0'\n"
    ),
}
_GOLD_208 = {
    "core/pricing.py": (
        "def compute_total(lines, tax=0.0):\n"
        "    subtotal = sum(item['qty'] * item['price'] for item in lines)\n"
        "    return round(subtotal * (1 + tax), 2)\n"
    ),
    "app/service.py": (
        "from core.pricing import compute_total\n"
        "\n"
        "\n"
        "def quote(lines):\n"
        "    return compute_total(lines, tax=0.2)\n"
    ),
    "cli.py": (
        "from core.pricing import compute_total\n"
        "\n"
        "\n"
        "def render_quote(lines):\n"
        "    return f'Total={compute_total(lines, tax=0.2)}'\n"
    ),
}

TASK_208 = Task(
    id="task_208_pricing_api_migration",
    name="Migrate legacy pricing API to core.pricing with tests",
    tags=("refactor", "python", "multifile", "pytest", "execute", "hard"),
    prompt=(
        "Нужно сделать миграцию API:\n"
        "  1) создать core/pricing.py с функцией compute_total(lines, tax=0.0),\n"
        "  2) перевести app/service.py и cli.py с pricing.legacy.calc_total на новую API,\n"
        "  3) чтобы pytest из tests/ проходил.\n"
        "Поведение расчета не менять."
    ),
    setup_files=_SETUP_208,
    gold_files=_GOLD_208,
    verifier=all_of(
        file_exists("core/pricing.py"),
        file_not_contains("app/service.py", "pricing.legacy", "calc_total("),
        file_not_contains("cli.py", "pricing.legacy", "calc_total("),
        file_contains("app/service.py", "compute_total("),
        file_contains("cli.py", "compute_total("),
        pytest_passes("tests"),
    ),
)


# 209. Request latency reconstruction from JSONL events
_EVENTS_209 = [
    {"req": "r1", "event": "start", "ts_ms": 1000},
    {"req": "r1", "event": "end", "ts_ms": 1450},
    {"req": "r2", "event": "start", "ts_ms": 2000},
    {"req": "r2", "event": "end", "ts_ms": 2400},
    {"req": "r3", "event": "start", "ts_ms": 3000},
    {"req": "r4", "event": "start", "ts_ms": 4000},
    {"req": "r4", "event": "end", "ts_ms": 4300},
    {"req": "r5", "event": "start", "ts_ms": 5000},
    {"req": "r5", "event": "end", "ts_ms": 5600},
]
_EVENTS_209_JSONL = "".join(json.dumps(row) + "\n" for row in _EVENTS_209)
_REPORT_209 = {
    "durations_ms": {"r1": 450, "r2": 400, "r4": 300, "r5": 600},
    "slow_requests": ["r5", "r1"],
    "incomplete_count": 1,
}


def _verify_task_209(ws: Path) -> VerifyResult:
    p = ws / "latency_report.json"
    if not p.exists():
        return VerifyResult(False, "latency_report.json missing")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"latency_report.json invalid JSON: {exc}")
    if data.get("durations_ms") != _REPORT_209["durations_ms"]:
        return VerifyResult(False, f"durations_ms mismatch: {data.get('durations_ms')!r}")
    if data.get("slow_requests") != _REPORT_209["slow_requests"]:
        return VerifyResult(False, f"slow_requests mismatch: {data.get('slow_requests')!r}")
    if data.get("incomplete_count") != _REPORT_209["incomplete_count"]:
        return VerifyResult(False, f"incomplete_count mismatch: {data.get('incomplete_count')!r}")
    return VerifyResult(True, "latency_report.json has expected durations, top slow requests, and incomplete count")


TASK_209 = Task(
    id="task_209_request_latency_reconstruction",
    name="Reconstruct request latencies from events.jsonl",
    tags=("jsonl", "analytics", "pipeline", "execute", "hard"),
    prompt=(
        "В events.jsonl каждая строка — JSON с req,event,ts_ms. event: start/end.\n"
        "Собери latency_report.json со структурой:\n"
        "  - durations_ms: объект req->duration_ms для запросов, у которых есть и start, и end;\n"
        "  - slow_requests: список из 2 req с максимальной duration_ms (по убыванию);\n"
        "  - incomplete_count: сколько req не имеют пары start+end.\n"
        "Если у req несколько событий одного типа, бери первую корректную пару start->end."
    ),
    setup_files={"events.jsonl": _EVENTS_209_JSONL},
    gold_files={"latency_report.json": json.dumps(_REPORT_209) + "\n"},
    verifier=_verify_task_209,
)


# 210. Tar archive manifest
_FILES_210 = {
    "docs/a.txt": "alpha\nbeta\n",
    "docs/b.txt": "one\ntwo\nthree\n",
    "notes/c.txt": "xyz\n",
}


def _tar_210_setup(ws: Path) -> None:
    with tarfile.open(ws / "bundle.tar.gz", "w:gz") as tf:
        for rel, content in _FILES_210.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=rel)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _manifest_210_rows() -> list[list[str]]:
    rows: list[list[str]] = []
    for rel, content in sorted(_FILES_210.items()):
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        rows.append([rel, str(len(content.encode("utf-8"))), digest])
    return rows


_MANIFEST_210_ROWS = _manifest_210_rows()


def _verify_task_210(ws: Path) -> VerifyResult:
    return _verify_csv_rows(
        ws / "manifest.csv",
        ["path", "size_bytes", "sha256"],
        _MANIFEST_210_ROWS,
    )


TASK_210 = Task(
    id="task_210_tar_manifest_with_hashes",
    name="Extract tar.gz and build deterministic file manifest",
    tags=("archive", "hashing", "pipeline", "execute", "hard"),
    prompt=(
        "В bundle.tar.gz лежат текстовые файлы в разных подкаталогах.\n"
        "Распакуй архив и построй manifest.csv с колонками path,size_bytes,sha256:\n"
        "  - path — относительный путь файла внутри архива,\n"
        "  - size_bytes — размер файла в байтах,\n"
        "  - sha256 — SHA-256 хэш hex-строкой.\n"
        "Строки отсортируй по path по возрастанию."
    ),
    setup_files={},
    setup_callback=_tar_210_setup,
    gold_files={
        "manifest.csv": "path,size_bytes,sha256\n"
        + "".join(",".join(row) + "\n" for row in _MANIFEST_210_ROWS),
    },
    verifier=_verify_task_210,
)


# 211. Pytest algorithmic task: merge intervals with validation
TASK_211 = Task(
    id="task_211_impl_merge_intervals",
    name="Implement merge_intervals with validation so pytest passes",
    tags=("python", "impl", "pytest", "execute", "hard"),
    prompt=(
        "Создай intervals.py с функцией merge_intervals(intervals).\n"
        "Вход: список пар [start, end]. Нужно:\n"
        "  - валидировать, что start <= end, иначе ValueError;\n"
        "  - объединять пересекающиеся и соприкасающиеся интервалы;\n"
        "  - вернуть список объединенных интервалов, отсортированный по start."
    ),
    setup_files={
        "tests/test_intervals.py": (
            "import pytest\n"
            "from intervals import merge_intervals\n"
            "\n"
            "def test_merge_basic():\n"
            "    assert merge_intervals([[1,3],[2,6],[8,10],[15,18]]) == [[1,6],[8,10],[15,18]]\n"
            "\n"
            "def test_merge_touching():\n"
            "    assert merge_intervals([[1,2],[2,3],[4,4]]) == [[1,3],[4,4]]\n"
            "\n"
            "def test_unsorted_input():\n"
            "    assert merge_intervals([[5,7],[1,2],[3,6]]) == [[1,2],[3,7]]\n"
            "\n"
            "def test_invalid_interval():\n"
            "    with pytest.raises(ValueError):\n"
            "        merge_intervals([[5,4]])\n"
        ),
    },
    gold_files={
        "intervals.py": (
            "def merge_intervals(intervals):\n"
            "    norm = []\n"
            "    for start, end in intervals:\n"
            "        if start > end:\n"
            "            raise ValueError('start > end')\n"
            "        norm.append([start, end])\n"
            "    norm.sort(key=lambda x: x[0])\n"
            "    out = []\n"
            "    for cur in norm:\n"
            "        if not out or cur[0] > out[-1][1]:\n"
            "            out.append(cur[:])\n"
            "        else:\n"
            "            out[-1][1] = max(out[-1][1], cur[1])\n"
            "    return out\n"
        ),
    },
    verifier=pytest_passes("tests"),
)


# 212. Config precedence merge across YAML + TOML + JSON
_BASE_212 = (
    "service:\n"
    "  host: localhost\n"
    "  port: 8080\n"
    "features:\n"
    "  cache: false\n"
    "  retries: 2\n"
)
_ENV_212 = (
    "service:\n"
    "  port: 9090\n"
    "features:\n"
    "  cache: true\n"
)
_OVERRIDES_212 = {
    "service": {"host": "api.internal"},
    "features": {"retries": 5},
}
_EFFECTIVE_212 = {
    "service": {"host": "api.internal", "port": 9090},
    "features": {"cache": True, "retries": 5},
}


def _verify_task_212(ws: Path) -> VerifyResult:
    p = ws / "effective_config.json"
    if not p.exists():
        return VerifyResult(False, "effective_config.json missing")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"effective_config.json invalid JSON: {exc}")
    if data != _EFFECTIVE_212:
        return VerifyResult(False, f"effective config mismatch: {data!r} != {_EFFECTIVE_212!r}")
    return VerifyResult(True, "effective_config.json matches expected merged precedence")


TASK_212 = Task(
    id="task_212_merge_config_precedence",
    name="Merge base/env/override configs with precedence",
    tags=("yaml", "toml", "json", "pipeline", "execute", "hard"),
    prompt=(
        "Есть три файла конфигурации:\n"
        "  - base.yaml,\n"
        "  - env.yaml,\n"
        "  - overrides.toml.\n"
        "Сделай effective_config.json, выполнив глубокое слияние словарей по правилам:\n"
        "  1) base — базовый слой,\n"
        "  2) env перекрывает base,\n"
        "  3) overrides перекрывает env.\n"
        "Сливать рекурсивно по ключам. Итог сохранить как JSON-объект."
    ),
    setup_files={
        "base.yaml": _BASE_212,
        "env.yaml": _ENV_212,
        "overrides.toml": (
            "[service]\n"
            "host = 'api.internal'\n"
            "\n"
            "[features]\n"
            "retries = 5\n"
        ),
    },
    gold_files={"effective_config.json": json.dumps(_EFFECTIVE_212) + "\n"},
    verifier=_verify_task_212,
)


# 213. Markdown link audit
_DOCS_213 = {
    "docs/a.md": "See https://example.com/a and http://legacy.local/page.\n",
    "docs/b.md": "Ref: https://example.com/b and https://docs.site/help.\n",
    "docs/c.md": "Internal http://localhost:3000/dev and https://example.com/c\n",
}
_DOMAINS_213 = {"example.com": 3, "docs.site": 1, "legacy.local": 1, "localhost:3000": 1}
_BROKEN_213 = sorted(["http://legacy.local/page", "http://localhost:3000/dev"])


def _verify_task_213(ws: Path) -> VerifyResult:
    djson = ws / "domains.json"
    bl = ws / "broken_links.txt"
    if not djson.exists():
        return VerifyResult(False, "domains.json missing")
    if not bl.exists():
        return VerifyResult(False, "broken_links.txt missing")
    try:
        domains = json.loads(djson.read_text())
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"domains.json invalid JSON: {exc}")
    if domains != _DOMAINS_213:
        return VerifyResult(False, f"domains.json mismatch: {domains!r} != {_DOMAINS_213!r}")
    broken = sorted([line.strip() for line in bl.read_text().splitlines() if line.strip()])
    if broken != _BROKEN_213:
        return VerifyResult(False, f"broken_links mismatch: {broken!r} != {_BROKEN_213!r}")
    return VerifyResult(True, "domains.json and broken_links.txt match expected link audit")


TASK_213 = Task(
    id="task_213_markdown_link_audit",
    name="Audit markdown links into domains.json and broken_links.txt",
    tags=("markdown", "regex", "analytics", "execute", "hard"),
    prompt=(
        "Просканируй все .md файлы в каталоге docs и найди URL (http/https).\n"
        "Сформируй два артефакта:\n"
        "  1) domains.json — объект domain->count (сколько раз домен встретился).\n"
        "     domain = netloc из urllib.parse.urlparse, то есть host вместе с\n"
        "     портом если он указан: для 'http://localhost:3000/dev' domain ==\n"
        "     'localhost:3000', а не 'localhost';\n"
        "  2) broken_links.txt — по одной ссылке в строке для URL, которые\n"
        "     считаются проблемными: scheme == http ИЛИ домен содержит localhost.\n"
        "Для broken_links.txt порядок строк не важен."
    ),
    setup_files=_DOCS_213,
    gold_files={
        "domains.json": json.dumps(_DOMAINS_213) + "\n",
        "broken_links.txt": "\n".join(_BROKEN_213) + "\n",
    },
    verifier=_verify_task_213,
)


# 214. Data quality report
_CUSTOMERS_214 = (
    "id,email,age,country\n"
    "1,alice@example.com,30,US\n"
    "2,bob_at_example.com,25,DE\n"
    "2,bob_at_example.com,25,DE\n"
    "3,carol@example.com,-1,RU\n"
    "4,dave@example.com,not_a_number,US\n"
    "5,erin@example.com,40,\n"
)
_DQ_214 = {
    "row_count": 6,
    "duplicate_ids": ["2"],
    "invalid_emails": ["2"],
    "invalid_ages": ["3", "4"],
    "missing_country_ids": ["5"],
}


def _verify_task_214(ws: Path) -> VerifyResult:
    p = ws / "dq_report.json"
    if not p.exists():
        return VerifyResult(False, "dq_report.json missing")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"dq_report.json invalid JSON: {exc}")
    if data != _DQ_214:
        return VerifyResult(False, f"dq_report mismatch: {data!r} != {_DQ_214!r}")
    return VerifyResult(True, "dq_report.json matches expected quality checks")


TASK_214 = Task(
    id="task_214_customer_data_quality_report",
    name="Build data quality report for customers.csv",
    tags=("csv", "quality", "analytics", "execute", "hard"),
    prompt=(
        "Проанализируй customers.csv и собери dq_report.json со структурой:\n"
        "  - row_count: общее число data-строк (без заголовка),\n"
        "  - duplicate_ids: список id, которые встречаются >1 раза,\n"
        "  - invalid_emails: список id, у которых email не содержит символ '@',\n"
        "  - invalid_ages: список id, у которых age не целое неотрицательное число,\n"
        "  - missing_country_ids: список id, у которых пустое country.\n"
        "Все списки — строки id, отсортированные по возрастанию."
    ),
    setup_files={"customers.csv": _CUSTOMERS_214},
    gold_files={"dq_report.json": json.dumps(_DQ_214) + "\n"},
    verifier=_verify_task_214,
)


# 215. TODO/FIXME triage from source tree
_SRC_215 = {
    "src/a.py": "def f():\n    pass  # TODO: refactor\n",
    "src/b.py": "# FIXME: handle None\n\ndef g():\n    return 1\n",
    "src/nested/c.py": "x = 1  # TODO: remove magic number\n# FIXME: add typing\n",
}
_SUMMARY_215 = {"todo_count": 2, "fixme_count": 2}
_TRIAGE_215 = (
    "## TODO\n"
    "- src/a.py:2 TODO: refactor\n"
    "- src/nested/c.py:1 TODO: remove magic number\n"
    "\n"
    "## FIXME\n"
    "- src/b.py:1 FIXME: handle None\n"
    "- src/nested/c.py:2 FIXME: add typing\n"
)


def _verify_task_215(ws: Path) -> VerifyResult:
    s = ws / "summary.json"
    t = ws / "triage.md"
    if not s.exists():
        return VerifyResult(False, "summary.json missing")
    if not t.exists():
        return VerifyResult(False, "triage.md missing")
    try:
        summary = json.loads(s.read_text())
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"summary.json invalid JSON: {exc}")
    if summary != _SUMMARY_215:
        return VerifyResult(False, f"summary mismatch: {summary!r} != {_SUMMARY_215!r}")
    triage = t.read_text().strip()
    if triage != _TRIAGE_215.strip():
        return VerifyResult(False, "triage.md content differs from expected structured report")
    return VerifyResult(True, "summary.json and triage.md match expected TODO/FIXME inventory")


TASK_215 = Task(
    id="task_215_source_todo_fixme_triage",
    name="Generate structured TODO/FIXME triage report",
    tags=("search", "multifile", "reporting", "execute", "hard"),
    prompt=(
        "Просканируй все .py файлы под src/ и найди строки с TODO и FIXME.\n"
        "Создай:\n"
        "  - summary.json с ключами todo_count и fixme_count,\n"
        "  - triage.md в формате:\n"
        "      ## TODO\n"
        "      - <path>:<line> <text>\n"
        "      ...\n"
        "      ## FIXME\n"
        "      - <path>:<line> <text>\n"
        "    где <text> — содержимое комментария начиная с маркера TODO:/FIXME:\n"
        "    (то есть префикс сохраняется). Пример строки:\n"
        "      - src/a.py:2 TODO: refactor\n"
        "Секции и порядок строк внутри каждой секции — по path asc, затем line asc.\n"
        "Между секциями ровно одна пустая строка."
    ),
    setup_files=_SRC_215,
    gold_files={
        "summary.json": json.dumps(_SUMMARY_215) + "\n",
        "triage.md": _TRIAGE_215,
    },
    verifier=_verify_task_215,
)


# 216. Null/NA handling in joins and aggregation
_PRODUCTS_216 = (
    "sku,category\n"
    "A,books\n"
    "B,books\n"
    "C,tech\n"
    "D,tech\n"
    "E,food\n"
)
_SALES_216 = (
    "sku,qty,price\n"
    "A,2,100\n"
    "A,1,100\n"
    "B,1,80\n"
    "C,3,50\n"
    "D,1,\n"
    "E,4,20\n"
    "X,10,1\n"
)
_ROLLUP_216 = "category,revenue\nbooks,380\ntech,150\nfood,80\n"
TASK_216 = Task(
    id="task_216_category_revenue_rollup",
    name="Join products/sales and roll up revenue by category",
    tags=("csv", "join", "analytics", "execute", "hard"),
    prompt=(
        "Есть products.csv (sku,category) и sales.csv (sku,qty,price). Построй"
        " category_revenue.csv с колонками category,revenue:\n"
        "  - revenue = sum(qty*price) по sku категории,\n"
        "  - строки с пустым price игнорировать,\n"
        "  - sku, которых нет в products.csv, игнорировать,\n"
        "  - сортировка по revenue по убыванию."
    ),
    setup_files={"products.csv": _PRODUCTS_216, "sales.csv": _SALES_216},
    gold_files={"category_revenue.csv": _ROLLUP_216},
    verifier=file_contains("category_revenue.csv", "books,380", "tech,150", "food,80"),
)


# 217. Nested JSON normalization
_NESTED_217 = {
    "users": [
        {"id": 1, "contacts": [{"type": "email", "value": "a@example.com"}, {"type": "phone", "value": "+111"}]},
        {"id": 2, "contacts": [{"type": "email", "value": "b@example.com"}]},
        {"id": 3, "contacts": []},
    ]
}
_FLAT_217 = [
    {"id": 1, "email": "a@example.com"},
    {"id": 2, "email": "b@example.com"},
]
TASK_217 = Task(
    id="task_217_extract_user_emails",
    name="Extract first email contact per user from nested JSON",
    tags=("json", "normalization", "execute", "hard"),
    prompt=(
        "В input.json есть users[].contacts[]. Построй emails.json как массив"
        " объектов {id,email} для пользователей, у которых есть контакт type='email'."
        " Бери первое такое значение. Пользователей без email не включать."
    ),
    setup_files={"input.json": json.dumps(_NESTED_217, ensure_ascii=False) + "\n"},
    gold_files={"emails.json": json.dumps(_FLAT_217, ensure_ascii=False) + "\n"},
    verifier=_json_file_matches_loose("emails.json", _FLAT_217, ordered=True),
)


# 218. TOML + ENV synthesis
_PYPROJECT_218 = (
    "[project]\n"
    "name = 'diag'\n"
    "version = '1.2.3'\n"
    "\n"
    "[tool.service]\n"
    "host = 'localhost'\n"
    "port = 8080\n"
)
_ENV_218 = "SERVICE_HOST=api.local\nSERVICE_TIMEOUT=30\n"
_RUNTIME_218 = {
    "service_url": "http://api.local:8080",
    "timeout": 30,
    "app": "diag",
    "version": "1.2.3",
}


def _verify_task_218(ws: Path) -> VerifyResult:
    p = ws / "runtime_config.json"
    if not p.exists():
        return VerifyResult(False, "runtime_config.json missing")
    try:
        got = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        return VerifyResult(False, f"runtime_config.json invalid JSON: {exc}")
    if got != _RUNTIME_218:
        return VerifyResult(False, f"runtime_config mismatch: {got!r} != {_RUNTIME_218!r}")
    return VerifyResult(True, "runtime_config.json has expected merged values")


TASK_218 = Task(
    id="task_218_build_runtime_config",
    name="Build runtime_config.json from pyproject.toml and .env",
    tags=("toml", "env", "json", "pipeline", "execute", "hard"),
    prompt=(
        "Есть pyproject.toml и .env. Построй runtime_config.json со структурой:\n"
        "  - service_url = 'http://<SERVICE_HOST>:<port из tool.service.port>'\n"
        "  - timeout = int(SERVICE_TIMEOUT)\n"
        "  - app = project.name\n"
        "  - version = project.version\n"
        "Не добавляй других ключей."
    ),
    setup_files={"pyproject.toml": _PYPROJECT_218, ".env": _ENV_218},
    gold_files={"runtime_config.json": json.dumps(_RUNTIME_218) + "\n"},
    verifier=_verify_task_218,
)


# 219. SQL query shape + deterministic ordering
def _sqlite_219_setup(ws: Path) -> None:
    conn = sqlite3.connect(ws / "orders.db")
    conn.executescript(
        """
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer TEXT, amount INTEGER, status TEXT);
        INSERT INTO orders (customer, amount, status) VALUES
            ('alice', 100, 'paid'),
            ('alice', 50, 'paid'),
            ('bob', 70, 'refund'),
            ('bob', 130, 'paid'),
            ('carol', 200, 'paid'),
            ('dave', 200, 'paid');
        """
    )
    conn.commit()
    conn.close()


_LEADERBOARD_219 = (
    "customer,paid_total\n"
    "carol,200\n"
    "dave,200\n"
    "alice,150\n"
    "bob,130\n"
)
TASK_219 = Task(
    id="task_219_sql_paid_leaderboard",
    name="Export deterministic paid leaderboard from sqlite",
    tags=("sqlite", "analytics", "execute", "hard"),
    prompt=(
        "По orders.db построй paid_leaderboard.csv:\n"
        "  - только status='paid',\n"
        "  - сумма amount по customer,\n"
        "  - заголовок customer,paid_total,\n"
        "  - сортировка paid_total desc, при равенстве customer asc."
    ),
    setup_files={},
    setup_callback=_sqlite_219_setup,
    gold_files={"paid_leaderboard.csv": _LEADERBOARD_219},
    verifier=file_text_equals("paid_leaderboard.csv", _LEADERBOARD_219),
)


# 220. Python package refactor safety
_PKG_220 = {
    "src/app/main.py": "from utils.math import add\n\n\nprint(add(2, 3))\n",
    "src/utils/math.py": "def add(a, b):\n    return a + b\n",
    "tests/test_main.py": "from src.utils.math import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
}
_PKG_220_GOLD = {
    "src/app/main.py": "from src.core.math_ops import add\n\n\nprint(add(2, 3))\n",
    "src/core/math_ops.py": "def add(a, b):\n    return a + b\n",
    "tests/test_main.py": "from src.core.math_ops import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
}
TASK_220 = Task(
    id="task_220_python_import_migration",
    name="Move math module and rewrite imports project-wide",
    tags=("refactor", "python", "multifile", "pytest", "execute", "hard"),
    prompt=(
        "Сделай миграцию модуля:\n"
        "  - перенеси src/utils/math.py -> src/core/math_ops.py,\n"
        "  - перепиши все импорты на src.core.math_ops,\n"
        "  - старый путь src/utils/math.py удалить,\n"
        "  - pytest tests должен проходить."
    ),
    setup_files=_PKG_220,
    gold_files={
        **_PKG_220_GOLD,
        "src/utils/math.py": None,
    },
    verifier=all_of(
        file_exists("src/core/math_ops.py"),
        file_not_contains("src/app/main.py", "utils.math"),
        file_not_contains("tests/test_main.py", "utils.math"),
        pytest_passes("tests"),
    ),
)


# 221. Mixed log parsing with strict buckets
_LOG_221 = (
    "2026-05-01T10:00:00Z INFO auth user=1\n"
    "2026-05-01T10:00:01Z ERROR db timeout\n"
    "2026-05-01T10:00:02Z WARN api retry\n"
    "2026-05-01T10:00:03Z ERROR api 500\n"
    "2026-05-01T10:00:04Z INFO auth user=2\n"
    "2026-05-01T10:00:05Z WARN db slow\n"
)
_SUMMARY_221 = {"INFO": 2, "WARN": 2, "ERROR": 2}
TASK_221 = Task(
    id="task_221_log_level_summary",
    name="Summarize INFO/WARN/ERROR counts from app.log",
    tags=("logs", "analytics", "json", "execute", "hard"),
    prompt=(
        "В app.log каждая строка начинается с timestamp и далее уровнем INFO/WARN/ERROR."
        " Построй level_summary.json как объект с ключами INFO,WARN,ERROR и"
        " соответствующим количеством строк каждого уровня."
    ),
    setup_files={"app.log": _LOG_221},
    gold_files={"level_summary.json": json.dumps(_SUMMARY_221) + "\n"},
    verifier=_json_file_matches_loose("level_summary.json", _SUMMARY_221),
)


DIAGNOSTIC_TASKS: list[Task] = [
    TASK_206,
    TASK_207,
    TASK_208,
    TASK_209,
    TASK_210,
    TASK_211,
    TASK_212,
    TASK_213,
    TASK_214,
    TASK_215,
    TASK_216,
    TASK_217,
    TASK_218,
    TASK_219,
    TASK_220,
    TASK_221,
]


# keep imports considered "used" for static analyzers
_ = (yaml, tomllib, zipfile)
