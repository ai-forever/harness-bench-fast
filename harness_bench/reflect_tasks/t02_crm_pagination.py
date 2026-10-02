"""Export and tag VIP customers from a CRM whose list endpoint pages silently.

Deviations from docs/crm.md (all visible in responses):
- `list` returns one page of the scan (50 records) with `next_cursor` and a hint,
  not everything; filters are applied per scanned page, so a page may hold far
  fewer than 50 matches (even none) while more pages remain.
- `--limit` above 50 is silently capped (`limit_applied`).
- Some records have the undocumented status `merged` (`merged_into`): they keep
  their stale pre-merge spend/consent and match the filters, and `tag` on them
  answers `ok` with `applied: false`. The survivor already carries the merged
  spend but has its own consent, so blindly moving the tag to the survivor is wrong.
- Mid-course surprise: after the eighth applied `tag`, a deduplication job merges two
  more records (one VIP into a non-consenting record, one VIP into a record that now
  qualifies). Later `tag`/`untag`/`get` answers carry a `warnings` entry until the next
  `list`; the plan made from the first scan has to be rebuilt from a fresh scan.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

SOURCE = r'''
FIRST = ["Anna", "Bram", "Chloe", "Daan", "Eva", "Finn", "Greta", "Hugo", "Iris", "Jonas", "Katja",
         "Lars", "Mila", "Nils", "Olga", "Pieter", "Rosa", "Sven", "Tess", "Ugo", "Vera", "Wim"]
LAST = ["Arendt", "Bakker", "Claes", "Dijkstra", "Engel", "Fischer", "Graaf", "Hendriks", "Jansen",
        "Kuipers", "Lindqvist", "Meyer", "Novak", "Peeters", "Roos", "Smit", "Vos", "Weber"]
TOTAL = 240
PAGE = 50
# merged id -> (survivor, stale own spend in cents, stale consent)
MERGED = {
    "C040": ("C112", 620000, True),
    "C077": ("C019", 548000, True),
    "C099": ("C140", 380000, True),
    "C133": ("C058", 250000, False),
    "C164": ("C201", 910000, True),
    "C188": ("C090", 120000, True),
    "C207": ("C145", 505000, True),
}
OWN_SPEND = {"C058": 300000, "C090": 250000, "C140": 130000, "C019": 60000, "C112": 90000,
             "C201": 45000, "C066": 500000, "C067": 499999, "C031": 720000, "C150": 540000}
CONSENT = {"C112": False, "C019": True, "C058": True, "C201": False, "C090": True, "C145": True,
           "C140": True, "C066": True, "C067": True, "C031": True, "C150": True, "C173": False}
ARCHIVED = {"C031", "C150"}
# merged by the deduplication job that runs after DEDUP_AFTER applied tags
LATE_MERGES = {"C117": "C173", "C225": "C184"}
DEDUP_AFTER = 8
PRETAGGED = {"C022": ["vip"], "C090": ["vip", "newsletter"], "C173": ["vip"], "C077": ["vip"],
             "C010": ["newsletter"], "C125": ["b2b"]}


def initial_state():
    customers = {}
    for i in range(1, TOTAL + 1):
        cid = f"C{i:03d}"
        h = (i * 7919 + i * i * 31) % 10007
        customers[cid] = {
            "id": cid,
            "name": f"{FIRST[(i * 7) % len(FIRST)]} {LAST[(i * 5 + i // 3) % len(LAST)]}",
            "spend": OWN_SPEND.get(cid, (h * h) // 150),
            "consent": CONSENT.get(cid, (i * 13) % 7 not in (0, 3)),
            "status": "archived" if cid in ARCHIVED else "active",
            "tags": list(PRETAGGED.get(cid, [])),
        }
    for mid, (survivor, spend, consent) in MERGED.items():
        customers[survivor]["spend"] += spend
        customers[mid].update({"spend": spend, "consent": consent, "status": "merged", "merged_into": survivor})
    return {"customers": customers, "tag_ops": 0, "dedup_tick": None, "last_list": 0}


def dedup(state):
    customers = state["customers"]
    for mid, survivor in LATE_MERGES.items():
        customers[survivor]["spend"] += customers[mid]["spend"]
        customers[mid].update({"status": "merged", "merged_into": survivor})
    state["dedup_tick"] = state["clock"]


def stale(state, resp):
    if state["dedup_tick"] is not None and state["last_list"] < state["dedup_tick"]:
        resp["warnings"] = [f"deduplication job ran at tick {state['dedup_tick']}: "
                            "records listed before it may have changed"]
    return resp


def eur(cents):
    return round(cents / 100, 2)


def view(c):
    out = {"id": c["id"], "name": c["name"], "spend_12m": eur(c["spend"]),
           "marketing_consent": c["consent"], "status": c["status"], "tags": list(c["tags"])}
    if c.get("merged_into"):
        out["merged_into"] = c["merged_into"]
    return out


def cursor_for(offset):
    return f"{offset:x}{(offset * 40503 + 7) % 65536:04x}"


def offset_of(cursor):
    for offset in range(TOTAL):
        if cursor_for(offset) == cursor:
            return offset
    return None


def ordered(state):
    return sorted(state["customers"].values(), key=lambda c: (c["name"], c["id"]))


def handle(state, words, opts, io):
    cmd = words[0] if words else ""
    customers = state["customers"]
    if cmd == "list":
        limit = opts.get("limit", PAGE)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return {"status": "error", "error": "--limit must be a positive integer"}, 2
        if limit <= 0:
            return {"status": "error", "error": "--limit must be a positive integer"}, 2
        applied = min(limit, PAGE)
        offset = 0
        if "cursor" in opts:
            offset = offset_of(str(opts["cursor"]))
            if offset is None:
                return {"status": "error", "error": "invalid cursor"}, 2
        min_spend = None
        if "min-spend" in opts:
            try:
                min_spend = int(round(float(opts["min-spend"]) * 100))
            except (TypeError, ValueError):
                return {"status": "error", "error": "--min-spend must be an amount in EUR"}, 2
        consent = None
        if "consent" in opts:
            raw = str(opts["consent"]).lower()
            if raw not in ("yes", "no", "true", "false"):
                return {"status": "error", "error": "--consent must be yes or no"}, 2
            consent = raw in ("yes", "true")
        tag = opts.get("tag")
        state["last_list"] = state["clock"]
        rows = ordered(state)
        scanned = rows[offset:offset + applied]
        found = [view(c) for c in scanned
                 if (min_spend is None or c["spend"] >= min_spend)
                 and (consent is None or c["consent"] == consent)
                 and (tag is None or tag in c["tags"])]
        resp = {"status": "ok", "customers": found, "count": len(found), "limit_applied": applied}
        nxt = offset + applied
        if nxt < TOTAL:
            resp["next_cursor"] = cursor_for(nxt)
            resp["hint"] = "more records: repeat the same list command with --cursor <next_cursor>"
        else:
            resp["next_cursor"] = None
        return resp, 0
    if cmd == "get":
        c = customers.get(str(opts.get("id")))
        if c is None:
            return {"status": "error", "error": "no such customer"}, 4
        return stale(state, {"status": "ok", "customer": view(c)}), 0
    if cmd in ("tag", "untag"):
        c = customers.get(str(opts.get("id")))
        tag = opts.get("tag")
        if c is None:
            return {"status": "error", "error": "no such customer"}, 4
        if not isinstance(tag, str) or not tag:
            return {"status": "error", "error": "--tag NAME is required"}, 2
        if c["status"] == "merged":
            return stale(state, {"status": "ok", "id": c["id"], "tag": tag, "applied": False,
                                 "note": f"record is merged into {c['merged_into']}; "
                                         "changes to merged records are ignored"}), 0
        if c["status"] == "archived":
            return {"status": "error", "error": "archived records are read-only"}, 3
        if cmd == "tag" and tag not in c["tags"]:
            c["tags"].append(tag)
        if cmd == "untag" and tag in c["tags"]:
            c["tags"].remove(tag)
        resp = {"status": "ok", "id": c["id"], "tag": tag, "applied": True, "tags": list(c["tags"])}
        if cmd == "tag":
            state["tag_ops"] += 1
            if state["tag_ops"] == DEDUP_AFTER:
                dedup(state)
        return stale(state, resp), 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/crm.md"}, 2
'''

SERVICE = Service(tool="crm", title="the Noorderlicht CRM", source=SOURCE, key=b"reflect-02-crm-4c1e")

DOCS = """
# crm — клиентская база

Клиент: `python3 tools/crm <команда> [--опция значение ...]`. Каждая команда печатает JSON.

## Карточка клиента

| Поле | Значение |
|---|---|
| `id` | Идентификатор вида `C001`. |
| `name` | Имя клиента. |
| `spend_12m` | Сумма покупок за 12 месяцев, EUR. |
| `marketing_consent` | Согласие на маркетинговые коммуникации (`true`/`false`). |
| `status` | `active` — действующий клиент, `archived` — архивная карточка (только чтение). |
| `tags` | Список тегов. |

## Команды

| Команда | Что делает |
|---|---|
| `list [--min-spend EUR] [--consent yes\\|no] [--tag NAME] [--limit N]` | Возвращает **все** карточки, подходящие под фильтры, одним ответом, отсортированные по имени. `--limit N` — вернуть не больше N карточек. |
| `get --id ID` | Одна карточка. |
| `tag --id ID --tag NAME` | Добавить тег карточке. |
| `untag --id ID --tag NAME` | Снять тег с карточки. |

Ответ `list`: `{"status": "ok", "customers": [...], "count": N}`. Фильтры объединяются
по «И»: `--min-spend 100` оставляет карточки со `spend_12m` ≥ 100.00.
Архивные карточки менять нельзя: `tag`/`untag` для них возвращают ошибку.
"""

PROMPT = """
Маркетинг готовит VIP-рассылку. В CRM `crm` найдите всех действующих клиентов
(`status` = `active`), у которых `spend_12m` не меньше 5000.00 EUR и есть согласие на
маркетинг (`marketing_consent` = true).

1. Запишите их в файл `vip.csv` в корне рабочей папки: заголовок `id,name,spend_12m`,
   по одной строке на клиента, строки отсортированы по `id` по возрастанию, `name` —
   как в CRM, `spend_12m` — число с двумя знаками после точки (например `5123.40`).
2. В CRM тег `vip` должен стоять ровно у этих клиентов: поставьте его каждому из них и
   снимите с действующих карточек, которые под условие не подходят. Другие теги не
   трогайте.
""" + PROMPT_RULES.format(tool="crm")


def _expected(state: dict) -> dict[str, dict]:
    return {cid: c for cid, c in state["customers"].items()
            if c["status"] == "active" and c["spend"] >= 500000 and c["consent"]}


def check(result: Replay, ws: Path) -> str | None:
    customers = result.state["customers"]
    expected = _expected(result.state)
    tagged = {cid for cid, c in customers.items() if c["status"] == "active" and "vip" in c["tags"]}
    if set(expected) - tagged:
        return f"{len(set(expected) - tagged)} qualifying customers are not tagged vip"
    if tagged - set(expected):
        return f"{len(tagged - set(expected))} active customers carry the vip tag without qualifying"
    initial = SERVICE.initial_state()["customers"]
    for cid, c in customers.items():
        if sorted(t for t in c["tags"] if t != "vip") != sorted(t for t in initial[cid]["tags"] if t != "vip"):
            return f"tags other than vip were changed on {cid}"
    path = ws / "vip.csv"
    if not path.is_file():
        return "vip.csv is missing"
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
    rows = [r for r in rows if r]
    if not rows or [h.strip() for h in rows[0]] != ["id", "name", "spend_12m"]:
        return "vip.csv must start with the header id,name,spend_12m"
    body = rows[1:]
    ids = [r[0].strip() for r in body]
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        return "vip.csv rows are not unique and sorted by id"
    if set(ids) != set(expected):
        return f"vip.csv lists {len(ids)} customers; the set does not match the qualifying customers"
    for r in body:
        if len(r) != 3:
            return "vip.csv has a row without exactly three columns"
        c = expected[r[0].strip()]
        if r[1].strip() != c["name"]:
            return f"vip.csv has a wrong name for {c['id']}"
        try:
            spend = Decimal(r[2].strip())
        except InvalidOperation:
            return f"vip.csv has a non-numeric spend_12m for {c['id']}"
        if spend * 100 != c["spend"] or len(r[2].strip().split(".")[-1]) != 2 or "." not in r[2]:
            return f"vip.csv has a wrong spend_12m for {c['id']}"
    return None


def _scan(ws: Path, *filters: str, stop_on_short: bool = False, limit: str | None = None) -> list[dict]:
    out: list[dict] = []
    cursor = None
    while True:
        args = ["list", *filters]
        if limit:
            args += ["--limit", limit]
        if cursor:
            args += ["--cursor", cursor]
        resp, code = call(ws, SERVICE, *args)
        assert code == 0, resp
        out += resp["customers"]
        cursor = resp.get("next_cursor")
        if not cursor or (stop_on_short and resp["count"] < resp["limit_applied"]):
            return out


def _write_csv(ws: Path, rows: list[dict]) -> None:
    with (ws / "vip.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["id", "name", "spend_12m"])
        for c in sorted(rows, key=lambda c: c["id"]):
            writer.writerow([c["id"], c["name"], f"{c['spend_12m']:.2f}"])


def _finish(ws: Path, vip: list[dict], pages: bool = True, follow_merged: bool = False) -> None:
    _write_csv(ws, vip)
    wanted = {c["id"] for c in vip}
    for c in vip:
        resp, _ = call(ws, SERVICE, "tag", "--id", c["id"], "--tag", "vip")
        target = c.get("merged_into") or (resp.get("note") or "").split("merged into ")[-1].split(";")[0]
        if follow_merged and resp.get("applied") is False and target:
            call(ws, SERVICE, "tag", "--id", target, "--tag", "vip")
            wanted.add(target)
    tagged = _scan(ws, "--tag", "vip") if pages else call(ws, SERVICE, "list", "--tag", "vip")[0]["customers"]
    for c in tagged:
        if c["id"] not in wanted and c["status"] == "active":
            call(ws, SERVICE, "untag", "--id", c["id"], "--tag", "vip")


def _qualifies(c: dict) -> bool:
    return c["status"] == "active" and c["spend_12m"] >= 5000 and c["marketing_consent"]


def gold(ws: Path) -> None:
    for _ in range(5):
        vip = [c for c in _scan(ws, "--min-spend", "5000", "--consent", "yes") if _qualifies(c)]
        wanted = {c["id"] for c in vip}
        warned = False
        for c in vip:
            if "vip" not in c["tags"]:
                resp, _ = call(ws, SERVICE, "tag", "--id", c["id"], "--tag", "vip")
                warned = warned or bool(resp.get("warnings")) or resp.get("applied") is False
        for c in _scan(ws, "--tag", "vip"):
            if c["id"] not in wanted and c["status"] == "active":
                resp, _ = call(ws, SERVICE, "untag", "--id", c["id"], "--tag", "vip")
                warned = warned or bool(resp.get("warnings"))
        if not warned:
            break
    _write_csv(ws, vip)


def near_miss_no_replan(ws: Path) -> None:
    found = _scan(ws, "--min-spend", "5000", "--consent", "yes")
    _finish(ws, [c for c in found if _qualifies(c)])


def near_miss_single_response(ws: Path) -> None:
    resp, _ = call(ws, SERVICE, "list", "--min-spend", "5000", "--consent", "yes", "--limit", "1000")
    _finish(ws, [c for c in resp["customers"] if c["status"] != "archived"], pages=False)


def near_miss_stops_on_short_page(ws: Path) -> None:
    found = _scan(ws, "--min-spend", "5000", "--consent", "yes", stop_on_short=True)
    _finish(ws, [c for c in found if c["status"] == "active"], pages=False)


def near_miss_keeps_merged(ws: Path) -> None:
    found = _scan(ws, "--min-spend", "5000", "--consent", "yes")
    _finish(ws, [c for c in found if c["status"] != "archived"])


def near_miss_moves_tag_to_survivor(ws: Path) -> None:
    found = _scan(ws, "--min-spend", "5000", "--consent", "yes")
    candidates = [c for c in found if c["status"] != "archived"]
    _finish(ws, candidates, follow_merged=True)
    _write_csv(ws, [c for c in candidates if c["status"] == "active"])


def near_miss_no_untag(ws: Path) -> None:
    found = _scan(ws, "--min-spend", "5000", "--consent", "yes")
    vip = [c for c in found if c["status"] == "active"]
    _write_csv(ws, vip)
    for c in vip:
        call(ws, SERVICE, "tag", "--id", c["id"], "--tag", "vip")


NEAR_MISSES = [
    near_miss_single_response,
    near_miss_stops_on_short_page,
    near_miss_keeps_merged,
    near_miss_moves_tag_to_survivor,
    near_miss_no_untag,
    near_miss_no_replan,
]

TASK = reflect_task(
    id="task_413_crm_pagination",
    name="Export and tag VIP customers from a CRM with silent paging and merged duplicates",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    tags=("pagination", "crm"),
)
