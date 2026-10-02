"""Apply counter increments through a key-value store that is only eventually consistent.

Deviations from docs/kv.md (all visible in responses):
- A plain `get` reads a replica that lags two ticks behind every write: right after a
  `put` it returns the old value and version with `stale: true` and a `hint` about
  the undocumented `--consistent` flag. The docs promise read-your-writes.
- `put --if-version N` with a wrong version is documented as a `conflict` (exit 5);
  the service answers `status: ok`, `applied: false`, `current_version` (exit 0).
- Mid-course surprise: after our second write to `logins` the counter is migrated to
  `auth.logins`; the old key answers reads with `read_only`/`moved_to` and writes with
  `ok`, `applied: false`. The docs say keys never change. The collector continues on the
  new key.
A background collector bumps two of the counters on its own (the prompt says so), so
even a consistent read can be outdated by the time of the write: only a conditional
write that is checked for `applied` and retried keeps both its and our increments.
Trap: re-running an event after an unnoticed `applied: false` is right, but re-running
one that was applied counts it twice; patching the final value by hand is a write
without an event, which the audit rejects.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

EVENTS = [
    ("ev-01", "orders.eu", 3), ("ev-02", "orders.eu", 2), ("ev-03", "orders.eu", -1),
    ("ev-04", "api.calls", 40), ("ev-05", "api.calls", 25), ("ev-06", "signups", 1),
    ("ev-07", "refunds.eu", 1), ("ev-08", "orders.us", 4), ("ev-09", "orders.us", 1),
    ("ev-10", "logins", 12), ("ev-11", "logins", 7), ("ev-12", "logins", -2),
    ("ev-13", "api.calls", 18), ("ev-14", "signups", 2), ("ev-15", "signups", 1),
    ("ev-16", "orders.eu", 5), ("ev-17", "refunds.eu", 2), ("ev-18", "api.calls", -6),
    ("ev-19", "orders.us", -2), ("ev-20", "logins", 9), ("ev-21", "api.calls", 31),
    ("ev-22", "orders.eu", 1),
]


def _events_csv() -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["event_id", "counter", "delta"])
    writer.writerows(EVENTS)
    return buf.getvalue()


SOURCE = r'''
INITIAL = {"orders.eu": 120, "orders.us": 87, "refunds.eu": 6, "signups": 41, "logins": 305, "api.calls": 9120}
LAG = 2
MIGRATE = ("logins", "auth.logins", 2)
HINT = "replica lag: this read may be stale; add --consistent to read from the leader"


def initial_state():
    return {"kv": {k: {"value": v, "version": 1, "hist": [[0, v, 1]]} for k, v in INITIAL.items()},
            "writes": [], "bg": {"api.calls": 0, "logins": 0}, "moved": {}}


def write(state, key, value):
    rec = state["kv"][key]
    rec["value"] = value
    rec["version"] += 1
    old = [n for n, h in enumerate(rec["hist"]) if h[0] < state["clock"] - 10]
    rec["hist"] = rec["hist"][old[-1]:] if old else rec["hist"]
    rec["hist"].append([state["clock"], value, rec["version"]])


def replica(state, key):
    rec = state["kv"][key]
    seen = [h for h in rec["hist"] if h[0] <= state["clock"] - LAG - 1]
    last = seen[-1] if seen else rec["hist"][0]
    return last[1], last[2], last[2] != rec["version"]


def background(state):
    if state["clock"] % 4 == 0:
        write(state, "api.calls", state["kv"]["api.calls"]["value"] + 1)
        state["bg"]["api.calls"] += 1
    if state["clock"] % 5 == 2:
        key = state["moved"].get("logins", "logins")
        write(state, key, state["kv"][key]["value"] + 1)
        state["bg"]["logins"] += 1


def migrate(state):
    old, new, after = MIGRATE
    if old in state["moved"] or sum(w["key"] == old for w in state["writes"]) < after:
        return None
    value = state["kv"][old]["value"]
    state["kv"][new] = {"value": value, "version": 1, "hist": [[state["clock"], value, 1]]}
    state["moved"][old] = new
    state["moved_at"] = state["clock"]
    return f"maintenance: counter {old} moved to key {new}; {old} is now read-only"


def read(state, key, consistent):
    if key in state["moved"]:
        rec = state["kv"][key]
        return {"key": key, "value": rec["value"], "version": rec["version"], "read_only": True,
                "moved_to": state["moved"][key],
                "note": f"frozen since tick {state['moved_at']}; the counter continues under {state['moved'][key]}"}
    if consistent:
        rec = state["kv"][key]
        return {"key": key, "value": rec["value"], "version": rec["version"]}
    value, version, stale = replica(state, key)
    out = {"key": key, "value": value, "version": version}
    if stale:
        out["stale"] = True
        out["hint"] = HINT
    return out


def handle(state, words, opts, io):
    notice = migrate(state)
    background(state)
    resp, code = serve(state, words, opts)
    if notice:
        resp["notices"] = [notice]
    return resp, code


def serve(state, words, opts):
    cmd = words[0] if words else ""
    consistent = opts.get("consistent") in (True, "true", "1", "yes")
    if cmd == "keys":
        return {"status": "ok", "keys": sorted(state["kv"])}, 0
    if cmd == "list":
        return {"status": "ok", "items": [read(state, k, consistent) for k in sorted(state["kv"])]}, 0
    if cmd == "ping":
        return {"status": "ok", "tick": state["clock"]}, 0
    key = opts.get("key")
    if cmd in ("get", "put") and key not in state["kv"]:
        return {"status": "error", "error": "unknown key; see `kv keys`"}, 2
    if cmd == "get":
        return {"status": "ok", **read(state, key, consistent)}, 0
    if cmd == "put":
        try:
            value = int(str(opts.get("value", "")))
        except ValueError:
            return {"status": "error", "error": "--value must be an integer"}, 2
        rec = state["kv"][key]
        if key in state["moved"]:
            return {"status": "ok", "key": key, "applied": False,
                    "reason": f"key is read-only: counter moved to {state['moved'][key]}"}, 0
        cond = opts.get("if-version")
        if cond is not None:
            try:
                cond = int(str(cond))
            except ValueError:
                return {"status": "error", "error": "--if-version must be an integer"}, 2
            if cond != rec["version"]:
                return {"status": "ok", "key": key, "applied": False, "reason": "version mismatch",
                        "current_version": rec["version"]}, 0
        event = opts.get("event")
        state["writes"].append({"key": key, "before": rec["value"], "value": value,
                                "event": event if isinstance(event, str) else None})
        write(state, key, value)
        return {"status": "ok", "key": key, "applied": True, "version": rec["version"]}, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/kv.md"}, 2
'''

SERVICE = Service(tool="kv", title="the metrics key-value store", source=SOURCE, key=b"reflect-15-kv-61d0")

DOCS = """
# kv — хранилище счётчиков

Клиент: `python3 tools/kv <команда> [--опция значение ...]`. Каждая команда печатает JSON.
Значения — целые числа. У каждого ключа есть `version`, она растёт на 1 при каждой записи.

| Команда | Что делает |
|---|---|
| `keys` | Список ключей. |
| `list` | Все ключи со значениями и версиями. |
| `get --key K` | Значение и версия ключа. |
| `put --key K --value V [--if-version N] [--event ID]` | Записать значение. `--event` — идентификатор события для аудита. |
| `ping` | Проверка связи. |

Гарантии:

- Хранилище строго согласовано: любое чтение после записи возвращает записанное значение
  (read-your-writes), реплики не отстают.
- `--if-version N` делает запись условной: если текущая версия ключа не `N`, запись не
  выполняется, ответ `status: conflict` с кодом выхода 5.
- Ответ `status: ok` означает, что значение записано.
- Набор ключей постоянен: счётчик всегда хранится под своим именем.
- Атомарного инкремента нет: увеличение делается как чтение, вычисление и запись.
"""

PROMPT = """
Примените к счётчикам в хранилище `kv` события из `events.csv` (идентификатор события,
счётчик, приращение; приращение может быть отрицательным).

Правила аудита: каждое событие применяется ровно одной выполненной записью
`put --key <счётчик> --value <новое значение> --event <event_id>`, где новое значение — это
значение счётчика непосредственно перед этой записью плюс приращение события. Других
выполненных записей (без `--event`, повторных по тому же событию, «ручных исправлений»)
быть не должно. Запись по событию должна попасть в тот ключ, где этот счётчик хранится в
момент записи. Параллельно с вами фоновый сборщик метрик сам увеличивает некоторые
счётчики; его изменения тоже нельзя терять.
""" + PROMPT_RULES.format(tool="kv")


MIGRATED = ("logins", "auth.logins")


def check(result: Replay, ws: Path) -> str | None:
    writes = result.state["writes"]
    delta = {eid: (key, d) for eid, key, d in EVENTS}
    seen: dict[str, int] = {}
    for w in writes:
        eid = w["event"]
        if eid not in delta:
            return "a write was made without a valid --event id"
        seen[eid] = seen.get(eid, 0) + 1
        key, d = delta[eid]
        if w["key"] != key and w["key"] != dict([MIGRATED]).get(key):
            return f"event {eid} was written to the wrong counter"
        if w["value"] != w["before"] + d:
            return f"the write for event {eid} did not add its delta to the current value"
    missing = [eid for eid in delta if eid not in seen]
    if missing:
        return f"{len(missing)} event(s) were never applied"
    twice = sorted(eid for eid, n in seen.items() if n > 1)
    if twice:
        return f"event {twice[0]} was applied more than once"
    return None


def _events(ws: Path) -> list[dict[str, str]]:
    with (ws / "events.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def gold(ws: Path) -> None:
    events = _events(ws)
    where: dict[str, str] = {}
    for ev in events:
        while True:
            key = where.get(ev["counter"], ev["counter"])
            # A plain get after our own put is stale; its hint names --consistent.
            cur = call(ws, SERVICE, "get", "--key", key, "--consistent")[0]
            if "moved_to" in cur:
                where[ev["counter"]] = cur["moved_to"]
                continue
            resp, code = call(ws, SERVICE, "put", "--key", key, "--value",
                              str(cur["value"] + int(ev["delta"])), "--if-version", str(cur["version"]),
                              "--event", ev["event_id"])
            assert code == 0, resp
            if resp["applied"]:
                break


def _naive(ws: Path, *, consistent: bool, cas: bool, wait_fresh: bool = False) -> None:
    for ev in _events(ws):
        args = ["get", "--key", ev["counter"], *(["--consistent"] if consistent else [])]
        cur = call(ws, SERVICE, *args)[0]
        while wait_fresh and cur.get("stale"):
            cur = call(ws, SERVICE, *args)[0]
        extra = ["--if-version", str(cur["version"])] if cas else []
        call(ws, SERVICE, "put", "--key", ev["counter"], "--value", str(cur["value"] + int(ev["delta"])),
             *extra, "--event", ev["event_id"])


def near_miss_docs_read_modify_write(ws: Path) -> None:
    """Docs literally: get, add, put."""
    _naive(ws, consistent=False, cas=False)


def near_miss_consistent_without_cas(ws: Path) -> None:
    """Reads with --consistent but writes unconditionally, losing collector increments."""
    _naive(ws, consistent=True, cas=False)


def near_miss_waits_for_fresh_read(ws: Path) -> None:
    """Re-reads until the replica is no longer stale, then writes unconditionally."""
    _naive(ws, consistent=False, cas=False, wait_fresh=True)


def near_miss_cas_trusts_ok(ws: Path) -> None:
    """Uses a conditional write, but treats `status: ok` as written and never retries."""
    _naive(ws, consistent=False, cas=True)


def near_miss_no_replan_after_move(ws: Path) -> None:
    """Consistent reads and checked conditional writes, but keeps retrying the original key."""
    for ev in _events(ws):
        for _attempt in range(6):
            cur = call(ws, SERVICE, "get", "--key", ev["counter"], "--consistent")[0]
            resp, _ = call(ws, SERVICE, "put", "--key", ev["counter"], "--value", str(cur["value"] + int(ev["delta"])),
                           "--if-version", str(cur["version"]), "--event", ev["event_id"])
            if resp.get("applied"):
                break


def near_miss_moves_without_cas(ws: Path) -> None:
    """Follows the move, reads consistently, but writes the new key unconditionally."""
    where: dict[str, str] = {}
    for ev in _events(ws):
        cur = call(ws, SERVICE, "get", "--key", where.get(ev["counter"], ev["counter"]), "--consistent")[0]
        if "moved_to" in cur:
            where[ev["counter"]] = cur["moved_to"]
            cur = call(ws, SERVICE, "get", "--key", cur["moved_to"], "--consistent")[0]
        call(ws, SERVICE, "put", "--key", cur["key"], "--value", str(cur["value"] + int(ev["delta"])),
             "--event", ev["event_id"])


def near_miss_patches_totals(ws: Path) -> None:
    """Applies events with consistent unconditional writes, then patches totals by hand."""
    _naive(ws, consistent=True, cas=False)
    start = {"orders.eu": 120, "orders.us": 87, "refunds.eu": 6, "signups": 41, "logins": 305, "api.calls": 9120}
    for key, value in start.items():
        target = value + sum(int(e["delta"]) for e in _events(ws) if e["counter"] == key)
        call(ws, SERVICE, "put", "--key", key, "--value", str(target))


NEAR_MISSES = [
    near_miss_no_replan_after_move,
    near_miss_moves_without_cas,
    near_miss_docs_read_modify_write,
    near_miss_consistent_without_cas,
    near_miss_waits_for_fresh_read,
    near_miss_cas_trusts_ok,
    near_miss_patches_totals,
]

TASK = reflect_task(
    id="task_426_kv_eventual",
    name="Read-modify-write counters on a lagging replica with a concurrent writer",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"events.csv": _events_csv()},
    tags=("consistency", "concurrency"),
)
