"""Apply a file of stock adjustments through a batch endpoint that is not atomic.

Deviations from docs/inv.md (all visible in responses):
- `apply --file` is documented as atomic (all rows or nothing, `status: error` on any
  failure). It actually applies rows in order until the first failing row, answers
  `status: ok` with `applied: k` and one entry in `errors`, and ignores the rest.
- The error entry reports `line` (the physical line of the file, header = line 1),
  not the documented data-row number `row`; `applied` and the `sku` in the error
  pin down which row failed.
- A successor SKU can itself be retired, so the fix may fail again on the same row.
Re-running the whole corrected file double-applies the rows that already went in.
- Mid-course surprise: after eight batch uploads the endpoint keeps answering
  `status: ok` but with `applied: 0` and an error saying the daily batch quota is
  used up; the remaining rows have to be switched to single `adjust` calls (which
  refuse retired SKUs with the same message), without repeating anything.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

ACTIVE = {
    "BLT-M4-20Z": ("Bolt M4x20, zinc", 640), "BLT-M5-30": ("Bolt M5x30", 420), "BLT-M8-50": ("Bolt M8x50", 310),
    "NUT-M4": ("Nut M4", 900), "NUT-M5": ("Nut M5", 780), "NUT-M6-C": ("Nut M6, class 8", 520),
    "NUT-M8": ("Nut M8", 450), "WSH-8S": ("Washer 8 mm, steel", 1100), "WSH-10": ("Washer 10 mm", 860),
    "SCR-3x16T": ("Screw 3x16 torx", 1500), "SCR-4x30": ("Screw 4x30", 1320), "SCR-5x50": ("Screw 5x50", 740),
    "SCR-6x80": ("Screw 6x80", 380), "HNG-75N": ("Hinge 75 mm, nickel", 160), "HNG-100": ("Hinge 100 mm", 140),
    "GLU-PVA2": ("Wood glue PVA, 750 ml", 95), "GLU-EPX": ("Epoxy glue", 60), "TAP-50P": ("Tape 50 mm, paper", 210),
    "TAP-25": ("Tape 25 mm", 260), "DWL-8B": ("Dowel 8 mm, beech", 2400), "DWL-10": ("Dowel 10 mm", 1800),
    "ANC-6": ("Wall anchor 6 mm", 950), "ANC-8": ("Wall anchor 8 mm", 870), "BRK-L40": ("Bracket L 40", 330),
    "BRK-L60": ("Bracket L 60", 290), "CHN-5": ("Chain 5 mm per m", 120), "SLD-400": ("Drawer slide 400", 88),
    "SLD-500": ("Drawer slide 500", 76), "HKS-S": ("Hook S", 540), "RVT-4": ("Rivet 4 mm", 3000),
}
RETIRED = {
    "BLT-M4-20": ("Bolt M4x20", "BLT-M4-20Z"), "NUT-M6": ("Nut M6", "NUT-M6-B"),
    "NUT-M6-B": ("Nut M6, class 6", "NUT-M6-C"), "WSH-8": ("Washer 8 mm", "WSH-8S"),
    "SCR-3x16": ("Screw 3x16", "SCR-3x16T"), "HNG-75": ("Hinge 75 mm", "HNG-75N"),
    "GLU-PVA": ("Wood glue PVA, 500 ml", "GLU-PVA2"), "TAP-50": ("Tape 50 mm", "TAP-50P"),
    "DWL-8": ("Dowel 8 mm", "DWL-8B"),
}
# data row (1-based) -> retired SKU placed there
RETIRED_ROWS = {9: "BLT-M4-20", 17: "NUT-M6", 26: "WSH-8", 31: "SCR-3x16", 38: "BLT-M4-20", 44: "HNG-75",
                52: "NUT-M6-B", 57: "GLU-PVA", 63: "NUT-M6", 69: "TAP-50", 74: "DWL-8", 80: "WSH-8"}
REASONS = ["cycle count", "damaged in storage", "customer return", "supplier short delivery",
           "found in wrong bin", "damaged, water", "sample for QA", "recount after audit"]


def _rows() -> list[tuple[str, int, str]]:
    skus = sorted(ACTIVE)
    rows = []
    for i in range(1, 81):
        sku = RETIRED_ROWS.get(i) or skus[(i * 11) % len(skus)]
        delta = ((i * 37) % 23) - 11 or 7
        if delta < 0 and i % 5 == 0:
            delta *= 3
        rows.append((sku, delta, REASONS[(i * 3) % len(REASONS)]))
    return rows


def _csv(rows: list[tuple[str, int, str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["sku", "delta", "reason"])
    writer.writerows(rows)
    return buf.getvalue()


ROWS = _rows()

SOURCE = r'''
import csv as _csv
ACTIVE = __ACTIVE__
RETIRED = __RETIRED__
MAX_BATCHES = 8


def initial_state():
    stock = {sku: qty for sku, (_, qty) in ACTIVE.items()}
    stock.update({sku: 0 for sku in RETIRED})
    return {"stock": stock, "history": [], "batches": 0}


def retired_error(sku):
    return f"SKU {sku} retired; superseded by {RETIRED[sku][1]}"


def sku_view(state, sku):
    if sku in ACTIVE:
        return {"sku": sku, "name": ACTIVE[sku][0], "stock": state["stock"][sku], "status": "active"}
    name, succ = RETIRED[sku]
    return {"sku": sku, "name": name, "stock": 0, "status": "retired", "superseded_by": succ}


def handle(state, words, opts, io):
    cmd = words[0] if words else ""
    if cmd == "stock":
        return {"status": "ok", "stock": {sku: state["stock"][sku] for sku in sorted(ACTIVE)}}, 0
    if cmd == "sku":
        sku = str(opts.get("id"))
        if sku not in ACTIVE and sku not in RETIRED:
            return {"status": "error", "error": f"unknown SKU {sku}"}, 4
        return {"status": "ok", **sku_view(state, sku)}, 0
    if cmd == "adjust":
        sku = str(opts.get("sku"))
        try:
            delta = int(str(opts.get("delta")))
        except ValueError:
            return {"status": "error", "error": "--delta must be an integer"}, 2
        if sku in RETIRED:
            return {"status": "error", "error": retired_error(sku)}, 3
        if sku not in ACTIVE:
            return {"status": "error", "error": f"unknown SKU {sku}"}, 4
        if delta == 0 or state["stock"][sku] + delta < 0:
            return {"status": "error", "error": "delta must be non-zero and keep stock non-negative"}, 3
        state["stock"][sku] += delta
        reason = opts.get("reason") if isinstance(opts.get("reason"), str) else ""
        state["history"].append({"kind": "adjust", "sku": sku, "delta": delta, "reason": reason, "tick": state["clock"]})
        return {"status": "ok", "sku": sku, "delta": delta, "stock": state["stock"][sku]}, 0
    if cmd == "apply":
        path = opts.get("file")
        if not isinstance(path, str):
            return {"status": "error", "error": "--file PATH is required"}, 2
        text = io.read(path)
        reader = _csv.reader(text.splitlines())
        header = next(reader, None)
        if header is None or [h.strip() for h in header] != ["sku", "delta", "reason"]:
            return {"status": "error", "error": "the file must start with the header sku,delta,reason"}, 2
        if state["batches"] >= MAX_BATCHES:
            data = [r for r in reader if r and any(c.strip() for c in r)]
            return {"status": "ok", "batch": None, "rows": len(data), "applied": 0,
                    "errors": [{"line": 2, "sku": data[0][0].strip() if data else "",
                                "reason": f"batch upload quota exhausted ({MAX_BATCHES} per day); "
                                          "submit the remaining rows individually with adjust"}]}, 0
        state["batches"] += 1
        batch = f"B-{state['batches']:03d}"
        rows, applied, errors = 0, 0, []
        for rec in reader:
            if not rec or all(not c.strip() for c in rec):
                continue
            rows += 1
            if errors:
                continue
            line = reader.line_num
            sku = rec[0].strip() if rec else ""
            reason = None
            delta = None
            if len(rec) < 2:
                reason = "expected sku,delta,reason"
            else:
                try:
                    delta = int(rec[1].strip())
                except ValueError:
                    reason = "delta is not an integer"
            if reason is None:
                if sku in RETIRED:
                    reason = retired_error(sku)
                elif sku not in ACTIVE:
                    reason = f"unknown SKU {sku}"
                elif delta == 0 or state["stock"][sku] + delta < 0:
                    reason = "delta must be non-zero and keep stock non-negative"
            if reason is not None:
                errors.append({"line": line, "sku": sku, "reason": reason})
                continue
            state["stock"][sku] += delta
            applied += 1
            state["history"].append({"kind": "batch", "batch": batch, "sku": sku, "delta": delta,
                                     "reason": rec[2] if len(rec) > 2 else "", "tick": state["clock"]})
        return {"status": "ok", "batch": batch, "rows": rows, "applied": applied, "errors": errors}, 0
    if cmd == "history":
        return {"status": "ok", "entries": state["history"][-200:]}, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/inv.md"}, 2
'''.replace("__ACTIVE__", repr(ACTIVE)).replace("__RETIRED__", repr(RETIRED))

SERVICE = Service(tool="inv", title="the Oosthaven warehouse inventory", source=SOURCE, key=b"reflect-03-inv-b83d")

DOCS = """
# inv — складской учёт

Клиент: `python3 tools/inv <команда> [--опция значение ...]`. Каждая команда печатает JSON.

| Команда | Что делает |
|---|---|
| `stock` | Остатки по всем действующим позициям. |
| `sku --id SKU` | Карточка позиции: название, остаток, `status` (`active`/`retired`), для выведенных — `superseded_by`. |
| `adjust --sku SKU --delta N [--reason TEXT]` | Одна корректировка остатка на `N` штук (`N` может быть отрицательным). |
| `apply --file PATH` | Пакетная корректировка из CSV-файла в рабочей папке. |
| `history` | Журнал проведённых корректировок (последние 200). |

## Пакетная корректировка

Файл — CSV с заголовком `sku,delta,reason`, по строке на корректировку.

`apply` **атомарен**: пакет проводится либо целиком, либо не проводится вовсе. Успех:

```json
{"status": "ok", "batch": "B-001", "rows": 80, "applied": 80}
```

Если хотя бы одна строка некорректна, склад не меняется, а ответ перечисляет все
ошибочные строки (`row` — номер строки данных, без заголовка):

```json
{"status": "error", "errors": [{"row": 12, "error": "..."}]}
```

Поэтому после исправления файла его можно просто отправить заново.

## Выведенные позиции

Позиция со статусом `retired` снята с учёта и корректировок не принимает. Её движения
ведутся на позиции-преемнике (`superseded_by` в `sku --id`): корректировку для выведенного
SKU нужно провести по преемнику с тем же `delta`.
"""

PROMPT = """
На складе прошла инвентаризация. Все корректировки из файла `adjustments.csv` (заголовок
`sku,delta,reason`, 80 строк) нужно провести в учётной системе `inv` — каждую ровно один
раз, по правилам из документации. Файл `adjustments.csv` не меняйте; при необходимости
создавайте свои файлы рядом. В конце остатки по `stock` должны совпадать с исходными
плюс все 80 корректировок.
""" + PROMPT_RULES.format(tool="inv")


def _final_sku(sku: str) -> str:
    while sku in RETIRED:
        sku = RETIRED[sku][1]
    return sku


def check(result: Replay, ws: Path) -> str | None:
    expected = {sku: qty for sku, (_, qty) in ACTIVE.items()}
    for sku, delta, _ in ROWS:
        expected[_final_sku(sku)] += delta
    stock = result.state["stock"]
    wrong = sorted(sku for sku in ACTIVE if stock[sku] != expected[sku])
    if wrong:
        return f"stock differs from the adjusted inventory for {len(wrong)} SKUs, e.g. {wrong[0]}"
    if (ws / "adjustments.csv").read_text(encoding="utf-8").replace("\r\n", "\n") != _csv(ROWS):
        return "adjustments.csv was modified"
    return None


def _write(ws: Path, name: str, rows: list[tuple[str, int, str]]) -> str:
    (ws / name).write_text(_csv(rows), encoding="utf-8", newline="")
    return name


def _read(ws: Path) -> list[tuple[str, int, str]]:
    reader = csv.reader(io.StringIO((ws / "adjustments.csv").read_text(encoding="utf-8")))
    next(reader)
    return [(r[0], int(r[1]), r[2]) for r in reader if r]


def _successor(message: str) -> str | None:
    found = re.search(r"superseded by (\S+)", message)
    return found.group(1) if found else None


def _batches(ws: Path, rows: list[tuple[str, int, str]], mapping: dict[str, str]) -> int:
    """Upload the rest of the file batch by batch; returns how many rows went in."""
    start = 0
    for attempt in range(30):
        rest = [(mapping.get(s, s), d, r) for s, d, r in rows[start:]]
        resp, code = call(ws, SERVICE, "apply", "--file", _write(ws, f"adjustments_rest_{attempt}.csv", rest))
        assert code == 0, resp
        start += resp["applied"]
        if not resp["errors"]:
            break
        err = resp["errors"][0]
        if "superseded by" not in err["reason"]:
            break
        assert err["line"] == resp["applied"] + 2 and err["sku"] == rest[resp["applied"]][0], resp
        info, _ = call(ws, SERVICE, "sku", "--id", err["sku"])
        assert info["status"] == "retired", info
        original = rows[start][0]
        mapping[original] = info["superseded_by"]
        for s, succ in list(mapping.items()):
            if succ == err["sku"]:
                mapping[s] = info["superseded_by"]
    return start


def gold(ws: Path) -> None:
    rows = _read(ws)
    call(ws, SERVICE, "stock")
    mapping: dict[str, str] = {}
    start = _batches(ws, rows, mapping)
    for sku, delta, reason in rows[start:]:
        target = mapping.get(sku, sku)
        while True:
            resp, code = call(ws, SERVICE, "adjust", "--sku", target, "--delta", str(delta), "--reason", reason)
            if code == 0:
                break
            target = _successor(resp["error"])
            assert target, resp
            mapping[sku] = target
    call(ws, SERVICE, "stock")


def near_miss_no_replan_after_quota(ws: Path) -> None:
    _batches(ws, _read(ws), {})


def near_miss_trusts_atomic(ws: Path) -> None:
    call(ws, SERVICE, "apply", "--file", "adjustments.csv")


def near_miss_reruns_whole_file(ws: Path) -> None:
    rows = _read(ws)
    mapping: dict[str, str] = {}
    for _ in range(30):
        fixed = [(mapping.get(s, s), d, r) for s, d, r in rows]
        resp, _ = call(ws, SERVICE, "apply", "--file", _write(ws, "adjustments_fixed.csv", fixed))
        if not resp["errors"]:
            break
        bad = resp["errors"][0]["sku"]
        succ = _successor(resp["errors"][0]["reason"])
        for s in list(mapping) + [bad]:
            if mapping.get(s, s) == bad:
                mapping[s] = succ


def _resume(ws: Path, index_of_failure, drop_failing: bool, follow_chain: bool = True) -> None:
    """Batch phase with one specific misreading, then (after the quota) row-by-row adjusts."""
    rows = _read(ws)
    mapping: dict[str, str] = {}
    start = 0
    for attempt in range(40):
        rest = [(mapping.get(s, s), d, r) for s, d, r in rows[start:]]
        if not rest:
            return
        resp, _ = call(ws, SERVICE, "apply", "--file", _write(ws, f"rest_{attempt}.csv", rest))
        if not resp["errors"]:
            return
        err = resp["errors"][0]
        succ = _successor(err["reason"])
        if succ is None:
            start += resp["applied"]
            break
        failed = start + index_of_failure(resp, err)
        original = rows[min(failed, len(rows) - 1)][0]
        if not follow_chain and original in mapping:
            start = start + resp["applied"] + 1
            continue
        mapping[original] = succ
        for s, target in list(mapping.items()):
            if target == err["sku"]:
                mapping[s] = succ
        start = failed + 1 if drop_failing else failed
    for sku, delta, _ in rows[start:]:
        target = mapping.get(sku, sku)
        for _ in range(5):
            resp, code = call(ws, SERVICE, "adjust", "--sku", target, "--delta", str(delta))
            if code == 0 or drop_failing or (not follow_chain and sku in mapping):
                break
            target = mapping[sku] = _successor(resp["error"])


def near_miss_line_as_data_row(ws: Path) -> None:
    _resume(ws, lambda resp, err: err["line"] - 1, drop_failing=False)


def near_miss_skips_failing_rows(ws: Path) -> None:
    _resume(ws, lambda resp, err: resp["applied"], drop_failing=True)


def near_miss_single_hop_successor(ws: Path) -> None:
    _resume(ws, lambda resp, err: resp["applied"], drop_failing=False, follow_chain=False)


NEAR_MISSES = [
    near_miss_trusts_atomic,
    near_miss_reruns_whole_file,
    near_miss_line_as_data_row,
    near_miss_skips_failing_rows,
    near_miss_single_hop_successor,
    near_miss_no_replan_after_quota,
]

TASK = reflect_task(
    id="task_414_batch_partial",
    name="Apply stock adjustments through a batch endpoint that stops at the first bad row",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"adjustments.csv": _csv(ROWS)},
    tags=("batch", "idempotency"),
)
