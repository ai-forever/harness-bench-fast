"""Export monthly sales reports through a reporting API with a deprecated flag and caps.

Deviations from docs/rep.md (all visible in responses):
- `--format csv` is deprecated and ignored: the export comes back as JSON rows with a
  `warnings` entry naming the new flag `--as csv`.
- An export holds at most 100 rows: `truncated: true`, `total_rows`, `next_page`
  and a `hint` naming `--all` (or `--page N`). The docs say an export is the whole month.
- Two months are served from a stale cache (`source: cache`, a `hint` naming
  `--fresh`); the docs say exports always come from the current ledger.
- Mid-course surprise: once four months have been exported, a late correction lands on
  the first of them (`notices` on that call, `revised_at_tick` in `months`); the file
  already written for it is outdated and has to be exported again.
Trap: January is small and fresh, so a pattern learned on it (just `--as csv`) is
wrong for the later months; paging and gluing pages repeats the CSV header; building
CSV from the JSON rows does not reproduce the service's formatting.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

MONTHS = [f"2026-{m:02d}" for m in range(1, 7)]

SOURCE = r'''
import csv as _csv
import io as _io

COUNTS = {1: 84, 2: 137, 3: 96, 4: 212, 5: 158, 6: 101}
DAYS = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30}
CACHED = (2, 5)
REGIONS = ["NL", "BE", "DE", "FR"]
PRODUCTS = [
    ("Cable, USB-C 2m", 1299), ("Desk lamp \"Nova\"", 4950), ("Monitor arm", 8900),
    ("Keyboard TKL", 7400), ("Mouse pad XL", 1850), ("Webcam 1080p", 5990),
    ("Headset, wireless", 11900), ("Laptop stand", 3450), ("Docking station", 18900),
    ("Chair mat", 6200), ("Notebook A5 (5 pcs)", 1575), ("Whiteboard 90x60", 7999),
]
PAGE = 100
HEADER = ["order_id", "date", "region", "product", "qty", "unit_price", "amount"]


def gen(month, revised=False):
    x = 1000 + month * 7919
    raw = []
    for i in range(COUNTS[month]):
        x = (x * 1103515245 + 12345) % 2147483648
        day = 1 + (x >> 8) % DAYS[month]
        x = (x * 1103515245 + 12345) % 2147483648
        prod = PRODUCTS[(x >> 8) % len(PRODUCTS)]
        x = (x * 1103515245 + 12345) % 2147483648
        qty = 1 + (x >> 8) % 9
        region = REGIONS[(x >> 12) % len(REGIONS)]
        raw.append((day, i, region, prod, qty))
    raw.sort()
    rows = []
    for k, (day, _i, region, (name, price), qty) in enumerate(raw, 1):
        rows.append({"order_id": f"S26{month:02d}-{k:04d}", "date": f"2026-{month:02d}-{day:02d}",
                     "region": region, "product": name, "qty": qty, "unit_price_cents": price,
                     "amount_cents": qty * price})
    if revised:
        for idx in (5, 20):
            r = rows[idx]
            r["qty"] = r["qty"] - 1 if r["qty"] > 1 else r["qty"] + 2
            r["amount_cents"] = r["qty"] * r["unit_price_cents"]
        name, price = PRODUCTS[3]
        rows.append({"order_id": f"S26{month:02d}-{len(rows) + 1:04d}", "date": f"2026-{month:02d}-{DAYS[month]:02d}",
                     "region": "NL", "product": name, "qty": 2, "unit_price_cents": price, "amount_cents": 2 * price})
    return rows


def cached(month):
    rows = [dict(r) for r in gen(month)]
    n = len(rows)
    for idx in (10, 40, 77):
        rows[idx]["qty"] += 1
        rows[idx]["amount_cents"] = rows[idx]["qty"] * rows[idx]["unit_price_cents"]
    return [r for j, r in enumerate(rows) if j not in (n - 1, n - 2, n - 5, n - 9)]


def money(c):
    return f"{c // 100}.{c % 100:02d}"


def to_csv(rows):
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    for r in rows:
        w.writerow([r["order_id"], r["date"], r["region"], r["product"], r["qty"],
                    money(r["unit_price_cents"]), money(r["amount_cents"])])
    return buf.getvalue()


def initial_state():
    return {"exports": 0, "exported": [], "revised": {}}


def correction(state):
    """Once four months have been exported, a late correction lands on the first of them."""
    if state["revised"] or len(state["exported"]) < 4:
        return None
    month = state["exported"][0]
    state["revised"][month] = state["clock"]
    return (f"late correction posted to the ledger for {month} at tick {state['clock']}; "
            f"exports of {month} taken before it are outdated")


def flag(opts, name):
    return opts.get(name) in (True, "true", "1", "yes")


def handle(state, words, opts, io):
    notice = correction(state)
    resp, code = serve(state, words, opts)
    if notice:
        resp["notices"] = [notice]
    return resp, code


def serve(state, words, opts):
    cmd = words[0] if words else ""
    if cmd == "months":
        out = []
        for m in COUNTS:
            item = {"month": f"2026-{m:02d}", "closed": True}
            if item["month"] in state["revised"]:
                item["revised_at_tick"] = state["revised"][item["month"]]
            out.append(item)
        return {"status": "ok", "months": out}, 0
    if cmd == "export":
        month = str(opts.get("month", ""))
        if len(month) != 7 or month[:5] != "2026-" or not month[5:].isdigit() or int(month[5:]) not in COUNTS:
            return {"status": "error", "error": "--month must be one of the months listed by `rep months`"}, 2
        m = int(month[5:])
        as_fmt = opts.get("as", "json")
        if as_fmt not in ("json", "csv"):
            return {"status": "error", "error": "--as must be json or csv"}, 2
        state["exports"] += 1
        resp = {"status": "ok", "month": month}
        warnings, hints = [], []
        if "format" in opts:
            warnings.append("--format is deprecated and ignored; use --as csv|json")
        fresh = flag(opts, "fresh")
        rows = cached(m) if m in CACHED and not fresh else gen(m, month in state["revised"])
        if m in CACHED and not fresh:
            resp["source"] = "cache"
            resp["cached_on"] = f"2026-{m + 1:02d}-02"
            hints.append("cached export may miss late corrections; add --fresh to rebuild from the ledger")
        else:
            resp["source"] = "ledger"
        total = len(rows)
        if flag(opts, "all"):
            part, page = rows, 1
        else:
            try:
                page = int(str(opts.get("page", 1)))
            except ValueError:
                return {"status": "error", "error": "--page must be a number"}, 2
            if page < 1:
                return {"status": "error", "error": "--page must be 1 or more"}, 2
            part = rows[(page - 1) * PAGE: page * PAGE]
        resp["total_rows"] = total
        resp["row_count"] = len(part)
        more = (page - 1) * PAGE + len(part) < total
        resp["truncated"] = more
        if more:
            resp["next_page"] = page + 1
            hints.append(f"export is limited to {PAGE} rows; add --all to get every row, or ask for --page N")
        resp["format"] = as_fmt
        if as_fmt == "csv":
            resp["content"] = to_csv(part)
        else:
            resp["rows"] = part
        if warnings:
            resp["warnings"] = warnings
        if hints:
            resp["hint"] = " ".join(hints)
        if month not in state["exported"]:
            state["exported"].append(month)
        return resp, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/rep.md"}, 2
'''

SERVICE = Service(tool="rep", title="the sales reporting API", source=SOURCE, key=b"reflect-16-rep-a7e3")

DOCS = """
# rep — отчёты о продажах

Клиент: `python3 tools/rep <команда> [--опция значение ...]`. Каждая команда печатает JSON.

| Команда | Что делает |
|---|---|
| `months` | Закрытые месяцы, доступные для выгрузки. |
| `export --month YYYY-MM --format csv\\|json` | Выгрузка продаж за месяц. |

Выгрузка:

- `--format csv` возвращает CSV-файл месяца: в JSON-ответе поле `content` содержит текст
  CSV с заголовком (`order_id,date,region,product,qty,unit_price,amount`), суммы — в евро.
  `--format json` (по умолчанию) возвращает строки в поле `rows`, суммы — в центах.
- Выгрузка всегда содержит все продажи месяца, без ограничений на число строк.
- Выгрузка всегда строится по текущему состоянию учёта, со всеми поздними исправлениями.
- Месяцы в `months` закрыты: данные закрытого месяца больше не меняются.
"""

PROMPT = """
Выгрузите из сервиса отчётов `rep` продажи за январь–июнь 2026 года в CSV: по файлу на месяц,
`reports/2026-01.csv` … `reports/2026-06.csv`.

Условие успеха: каждый файл по содержимому совпадает с CSV-выгрузкой сервиса за этот месяц —
та же шапка, те же строки в том же порядке и форматировании, все продажи месяца, по текущему
состоянию учёта на момент, когда вы закончите (со всеми поздними исправлениями). Шапка —
одна, в первой строке.
""" + PROMPT_RULES.format(tool="rep")


def _expected(month: str, revised: bool) -> str:
    ns = SERVICE.namespace()
    return ns["to_csv"](ns["gen"](int(month[5:]), revised))


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip("\n")


def check(result: Replay, ws: Path) -> str | None:
    for month in MONTHS:
        path = ws / "reports" / f"{month}.csv"
        if not path.is_file():
            return f"reports/{month}.csv is missing"
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
        if _norm(text) != _norm(_expected(month, month in result.state["revised"])):
            return f"reports/{month}.csv does not match the current CSV export of that month"
    return None


def _write(ws: Path, month: str, text: str) -> None:
    out = ws / "reports" / f"{month}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="")


def _export(ws: Path, month: str) -> None:
    args = ["export", "--month", month, "--format", "csv"]
    while True:
        resp, code = call(ws, SERVICE, *args)
        assert code == 0, resp
        if "--as csv" in " ".join(resp.get("warnings", [])) and "--as" not in args:
            args = [a for a in args if a not in ("--format", "csv")] + ["--as", "csv"]
        elif resp.get("truncated") and "--all" not in args:
            args.append("--all")
        elif resp.get("source") == "cache" and "--fresh" not in args:
            args.append("--fresh")
        else:
            break
    _write(ws, month, resp["content"])


def gold(ws: Path) -> None:
    months = [m["month"] for m in call(ws, SERVICE, "months")[0]["months"] if m["month"] in MONTHS]
    for month in months:
        _export(ws, month)
    # A late correction can invalidate a month exported earlier: export it again.
    for m in call(ws, SERVICE, "months")[0]["months"]:
        if m["month"] in MONTHS and "revised_at_tick" in m:
            _export(ws, m["month"])


def _each_month(ws: Path, export: Callable[[Path, str], None], *, replan: bool = True) -> None:
    """Run one export strategy over all months, then (optionally) over the revised ones again."""
    for month in MONTHS:
        export(ws, month)
    if replan:
        for m in call(ws, SERVICE, "months")[0]["months"]:
            if m["month"] in MONTHS and "revised_at_tick" in m:
                export(ws, m["month"])


def near_miss_no_replan_after_correction(ws: Path) -> None:
    """Handles every flag, but never re-exports the month the late correction hit."""
    _each_month(ws, _export, replan=False)


def _docs_format(ws: Path, month: str) -> None:
    resp, _ = call(ws, SERVICE, "export", "--month", month, "--format", "csv")
    _write(ws, month, resp.get("content") or json.dumps(resp.get("rows", []), ensure_ascii=False))


def near_miss_docs_format(ws: Path) -> None:
    """Docs literally: `--format csv`, saving whatever came back."""
    _each_month(ws, _docs_format)


def _as_csv(*extra: str) -> Callable[[Path, str], None]:
    def export(ws: Path, month: str) -> None:
        resp, _ = call(ws, SERVICE, "export", "--month", month, "--as", "csv", *extra)
        _write(ws, month, resp["content"])

    return export


def near_miss_ignores_truncation(ws: Path) -> None:
    """Switches to `--as csv` and refreshes the cache, but keeps the first 100 rows."""
    _each_month(ws, _as_csv("--fresh"))


def near_miss_ignores_cache(ws: Path) -> None:
    """Uses `--as csv --all`, but accepts the cached months."""
    _each_month(ws, _as_csv("--all"))


def _own_csv(ws: Path, month: str) -> None:
    resp, _ = call(ws, SERVICE, "export", "--month", month, "--all", "--fresh")
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["order_id", "date", "region", "product", "qty", "unit_price", "amount"])
    for r in resp["rows"]:
        writer.writerow([r["order_id"], r["date"], r["region"], r["product"], r["qty"],
                         r["unit_price_cents"] / 100, r["amount_cents"] / 100])
    _write(ws, month, buf.getvalue())


def near_miss_own_csv(ws: Path) -> None:
    """Gets every fresh row as JSON and writes the CSV itself."""
    _each_month(ws, _own_csv)


def _glued_pages(ws: Path, month: str) -> None:
    parts, page = [], 1
    while True:
        resp, _ = call(ws, SERVICE, "export", "--month", month, "--as", "csv", "--fresh", "--page", str(page))
        parts.append(resp["content"])
        if not resp.get("truncated"):
            break
        page = resp["next_page"]
    _write(ws, month, "".join(parts))


def near_miss_glues_pages(ws: Path) -> None:
    """Pages through fresh CSV exports and concatenates the pages as they are."""
    _each_month(ws, _glued_pages)


NEAR_MISSES = [
    near_miss_no_replan_after_correction,
    near_miss_docs_format,
    near_miss_ignores_truncation,
    near_miss_ignores_cache,
    near_miss_own_csv,
    near_miss_glues_pages,
]

TASK = reflect_task(
    id="task_427_report_deprecated_flag",
    name="Export monthly CSV reports past a deprecated flag, a row cap and a stale cache",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    tags=("export", "files"),
)
