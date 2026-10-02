"""Ship orders through a carrier API with a hidden weight limit and soft address checks.

Deviations from docs/ship.md (all visible in responses):
- Parcels heavier than 20 kg *gross* (items plus 0.5 kg packaging) are rejected with
  a bare `carrier rejected (C4)`; the docs promise 30 kg. `ship explain --code C4`
  gives the real rule.
- `address-check` never fails: it answers `ok` with `deliverable: false` and a
  `suggestion` for bad addresses (the docs say it returns `status: invalid`).
- `create` does not refuse an undeliverable address (the docs say it does): the
  parcel is created with `deliverable: false` and keeps that address even after
  `address-fix`.
- Mid-course surprise: a standard parcel holding an oversize item is created with
  `ok`, but on the very next call it is refused at the sorting hub (`notices`, and
  `parcels` shows `refused_at_hub`): the whole parcel is void. The oversize item has to
  go alone as `--service bulky`, the rest of that parcel has to be shipped again.
  The docs say an `ok` parcel is accepted for delivery.
- The prompt asks for the minimum number of parcels, so one item per parcel fails.
Trap: an agent that ships first and fixes addresses afterwards has to cancel the
parcels already created; re-creating them without cancelling ships items twice,
and leaving them ships to an address the carrier cannot deliver to. Packing to
20 kg of item weight still trips C4 because of the packaging.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

# order id -> (customer, address as in the shop, [(item id, sku, weight grams)])
ORDERS = {
    "ORD-501": ("Janssen", ("Keizersgracht 104", "1015 CJ", "Amsterdam"),
                [("501-1", "kettlebell-12", 12000), ("501-2", "yoga-mat", 1400), ("501-3", "kettlebell-8", 8000)]),
    "ORD-502": ("De Vries", ("Coolsingel 4O", "3011 AD", "Rotterdam"),
                [("502-1", "desk-lamp", 1800), ("502-2", "monitor-27", 6900)]),
    "ORD-503": ("Bakker", ("Oudegracht 211", "3511 NH", "Utrecht"),
                [("503-1", "dumbbell-10", 10000), ("503-2", "dumbbell-10", 10000), ("503-3", "bench-pad", 3200),
                 ("503-4", "resistance-bands", 600)]),
    "ORD-504": ("Visser", ("Grote Markt 7", "9711 LV", "Groningen"),
                [("504-1", "office-chair", 14500), ("504-2", "footrest", 4100), ("504-3", "cable-kit", 900)]),
    "ORD-505": ("Smit", ("Stationsplein 12", "5611 AC", "Eindhoven"),
                [("505-1", "coffee-grinder", 2300), ("505-2", "espresso-machine", 11800), ("505-3", "beans-1kg", 1000),
                 ("505-4", "beans-1kg", 1000)]),
    "ORD-506": ("Meijer", ("Vrijthof 18", "6211 LE", "Maastrict"),
                [("506-1", "bookshelf-kit", 17200), ("506-2", "shelf-brackets", 2100), ("506-3", "screws", 400)]),
    "ORD-507": ("De Boer", ("Lange Voorhout 2", "2514 EE", "Den Haag"),
                [("507-1", "plant-pot-xl", 9600), ("507-2", "potting-soil-20l", 9900), ("507-3", "watering-can", 800)]),
    "ORD-508": ("Mulder", ("Hoogstraat 33", "5615 PL", "Eindhoven"),
                [("508-1", "rowing-machine-rail", 11000), ("508-2", "rowing-machine-seat", 8800)]),
}
OVERSIZE = ("504-1", "508-1")
GOOD = {
    "ORD-502": ("Coolsingel 40", "3011 AD", "Rotterdam"),
    "ORD-504": ("Grote Markt 7", "9711 LM", "Groningen"),
    "ORD-506": ("Vrijthof 18", "6211 LE", "Maastricht"),
}


def _orders_json() -> str:
    data = [{"order_id": oid, "customer": cust,
             "address": {"street": addr[0], "postcode": addr[1], "city": addr[2], "country": "NL"},
             "items": [{"item_id": iid, "sku": sku, "weight_kg": grams / 1000} for iid, sku, grams in items]}
            for oid, (cust, addr, items) in ORDERS.items()]
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


SOURCE = r'''
ORDERS = __ORDERS__
GOOD = __GOOD__
OVERSIZE = __OVERSIZE__
LIMIT, BULKY_LIMIT, PACKAGING = 20000, 30000, 500
EXPLAIN = {
    "C4": "Carrier rejected the parcel: gross weight (items plus 0.5 kg packaging per parcel) is over the "
          "20.0 kg carrier limit. Split the order into lighter parcels.",
    "C7": "Carrier rejected the parcel: unknown item.",
    "C9": "Carrier rejected the parcel: a bulky parcel carries exactly one item.",
}


def kg(grams):
    return round(grams / 1000, 2)


def initial_state():
    return {"addr": {o: list(v[1]) for o, v in ORDERS.items()},
            "good": {o: list(GOOD.get(o, v[1])) for o, v in ORDERS.items()},
            "parcels": []}


def fmt(addr):
    return {"street": addr[0], "postcode": addr[1], "city": addr[2], "country": "NL"}


def ok_addr(state, oid):
    return state["addr"][oid] == state["good"][oid]


def hub(state):
    """Standard parcels with an oversize item are refused at the sorting hub on the next call."""
    notes = []
    for p in state["parcels"]:
        if p["active"] and p["service"] == "standard" and p["created"] < state["clock"]:
            big = [i for i in p["items"] if i in OVERSIZE]
            if big:
                p["active"] = False
                p["state"] = "refused_at_hub"
                notes.append(f"parcel {p['id']} was refused at the sorting hub: item {big[0]} is oversize "
                             "(longest side over 120 cm) and has to travel alone as --service bulky. "
                             "The parcel is void; none of its items are shipped.")
    return notes


def handle(state, words, opts, io):
    notes = hub(state)
    resp, code = serve(state, words, opts)
    if notes:
        resp["notices"] = notes
    return resp, code


def serve(state, words, opts):
    cmd = words[0] if words else ""
    oid = opts.get("order")
    if cmd in ("address-check", "address-fix", "create") and oid not in ORDERS:
        return {"status": "error", "error": "unknown order; pass --order ID"}, 2
    if cmd == "orders":
        return {"status": "ok", "orders": [{"order": o, "customer": v[0], "items": len(v[2])}
                                          for o, v in ORDERS.items()]}, 0
    if cmd == "address-check":
        resp = {"status": "ok", "order": oid, "address": fmt(state["addr"][oid]), "deliverable": ok_addr(state, oid)}
        if not resp["deliverable"]:
            resp["suggestion"] = fmt(state["good"][oid])
        return resp, 0
    if cmd == "address-fix":
        keys = {"street": 0, "postcode": 1, "city": 2}
        if not any(k in opts for k in keys):
            return {"status": "error", "error": "give at least one of --street, --postcode, --city"}, 2
        for key, pos in keys.items():
            if key in opts:
                state["addr"][oid][pos] = str(opts[key])
        return {"status": "ok", "order": oid, "address": fmt(state["addr"][oid]),
                "deliverable": ok_addr(state, oid)}, 0
    if cmd == "create":
        known = {iid: grams for iid, _sku, grams in ORDERS[oid][2]}
        items = opts.get("item")
        items = list(known) if items is None else (items if isinstance(items, list) else [items])
        if not items or any(i not in known for i in items):
            return {"status": "rejected", "error": "carrier rejected (C7)", "code": "C7"}, 3
        if len(set(items)) != len(items):
            return {"status": "error", "error": "an item is listed twice"}, 2
        service = opts.get("service", "standard")
        if service not in ("standard", "bulky"):
            return {"status": "error", "error": "--service must be standard or bulky"}, 2
        if service == "bulky" and len(items) != 1:
            return {"status": "rejected", "error": "carrier rejected (C9)", "code": "C9"}, 3
        gross = sum(known[i] for i in items) + PACKAGING
        if gross > (BULKY_LIMIT if service == "bulky" else LIMIT):
            return {"status": "rejected", "error": "carrier rejected (C4)", "code": "C4"}, 3
        pid = f"P-{len(state['parcels']) + 1:04d}"
        parcel = {"id": pid, "order": oid, "items": items, "weight": gross, "to": list(state["addr"][oid]),
                  "deliverable": ok_addr(state, oid), "active": True, "state": "active", "service": service,
                  "created": state["clock"]}
        state["parcels"].append(parcel)
        return {"status": "ok", "parcel": pid, "order": oid, "service": service, "items": items, "weight_kg": kg(gross),
                "to": fmt(parcel["to"]), "deliverable": parcel["deliverable"]}, 0
    if cmd == "parcels":
        rows = [{"parcel": p["id"], "order": p["order"], "items": p["items"], "weight_kg": kg(p["weight"]),
                 "service": p["service"], "to": fmt(p["to"]), "deliverable": p["deliverable"], "state": p["state"]}
                for p in state["parcels"] if oid is None or p["order"] == oid]
        return {"status": "ok", "parcels": rows}, 0
    if cmd == "cancel":
        pid = opts.get("parcel")
        for p in state["parcels"]:
            if p["id"] == pid and p["active"]:
                p["active"] = False
                p["state"] = "cancelled"
                return {"status": "ok", "cancelled": pid}, 0
        return {"status": "error", "error": "no active parcel with that id"}, 2
    if cmd == "explain":
        code = str(opts.get("code", "")).upper()
        if code not in EXPLAIN:
            return {"status": "error", "error": f"unknown code {code!r}"}, 2
        return {"status": "ok", "code": code, "meaning": EXPLAIN[code]}, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/ship.md"}, 2
'''.replace("__ORDERS__", repr(ORDERS)).replace("__GOOD__", repr(GOOD)).replace(
    "__OVERSIZE__", repr(OVERSIZE))

SERVICE = Service(tool="ship", title="the parcel carrier API", source=SOURCE, key=b"reflect-14-ship-2d8f")

DOCS = """
# ship — API перевозчика

Клиент: `python3 tools/ship <команда> [--опция значение ...]`. Каждая команда печатает JSON.
Вес — в килограммах.

| Команда | Что делает |
|---|---|
| `orders` | Заказы, переданные перевозчику. |
| `address-check --order ID` | Проверка адреса заказа. Доставляемый адрес — `status: ok`; недоставляемый — `status: invalid` и код выхода 3. |
| `address-fix --order ID [--street S] [--postcode P] [--city C]` | Исправить поля адреса заказа. |
| `create --order ID [--item ITEM_ID ...] [--service standard\\|bulky]` | Создать посылку из перечисленных товаров заказа (без `--item` — из всех товаров заказа). `standard` — по умолчанию; `bulky` — для крупногабаритного товара, ровно один товар в посылке. |
| `parcels [--order ID]` | Посылки: состав, вес, адрес, состояние. |
| `cancel --parcel ID` | Отменить посылку (товары из неё снова можно отправить). |
| `explain --code CODE` | Расшифровка кода отказа перевозчика. |

Правила перевозчика:

- Максимальный вес посылки — 30 кг (и `standard`, и `bulky`); упаковка в вес не входит.
- Посылка, созданная с `status: ok`, принята перевозчиком и будет доставлена.
- `create` проверяет адрес заказа и не создаёт посылку на недоставляемый адрес.
- Адрес посылки — это адрес заказа: после `address-fix` существующие посылки поедут на
  исправленный адрес.
- Отказ перевозчика — `status: rejected` с кодом в `error`, код выхода 3.
"""

PROMPT = """
Отправьте через перевозчика `ship` все товары из `orders.json` (заказы, адреса, товары с весом).

Условие успеха: каждый товар (`item_id`) лежит ровно в одной действующей посылке своего
заказа, ни одна посылка не нарушает правил перевозчика, и каждая действующая посылка
адресована на доставляемый адрес. Если адрес заказа недоставляемый, исправьте его по
подсказке перевозчика. Посылки оплачиваются поштучно, поэтому действующих посылок должно
быть минимально возможное число при правилах перевозчика. Лишние и ошибочные посылки
отмените.
""" + PROMPT_RULES.format(tool="ship")


def _min_parcels(items: list[tuple[str, str, int]]) -> int:
    """Fewest parcels for one order: oversize items alone, the rest within 19.5 kg of items."""
    loose = [g for iid, _sku, g in items if iid not in OVERSIZE]
    best = len(loose)

    def place(k: int, bins: list[int]) -> None:
        nonlocal best
        if len(bins) >= best:
            return
        if k == len(loose):
            best = len(bins)
            return
        for n in range(len(bins)):
            if bins[n] + loose[k] <= 19500:
                bins[n] += loose[k]
                place(k + 1, bins)
                bins[n] -= loose[k]
        place(k + 1, [*bins, loose[k]])

    place(0, [])
    return best + sum(iid in OVERSIZE for iid, _sku, _g in items)


def check(result: Replay, ws: Path) -> str | None:
    state = result.state
    active = [p for p in state["parcels"] if p["active"]]
    for p in active:
        if p["service"] == "standard" and any(i in OVERSIZE for i in p["items"]):
            return f"a parcel of {p['order']} breaks the carrier's rules and will be refused"
    for oid, (_cust, _addr, items) in ORDERS.items():
        good = list(GOOD.get(oid, _addr))
        for iid, _sku, _grams in items:
            holders = [p for p in active if iid in p["items"]]
            if not holders:
                return f"item {iid} of {oid} is not in any active parcel"
            if len(holders) > 1:
                return f"item {iid} of {oid} is in {len(holders)} active parcels"
        for p in active:
            if p["order"] == oid and (p["to"] != good or not p["deliverable"]):
                return f"a parcel of {oid} is addressed to an undeliverable address"
    need = sum(_min_parcels(items) for _c, _a, items in ORDERS.values())
    if len(active) != need:
        return "the items travel in more parcels than the carrier's rules require"
    return None


def _orders(ws: Path) -> list[dict]:
    return json.loads((ws / "orders.json").read_text(encoding="utf-8"))


def _pack(items: list[dict], limit_g: int) -> list[list[str]]:
    """First-fit decreasing by weight in grams."""
    bins: list[tuple[int, list[str]]] = []
    for it in sorted(items, key=lambda i: -i["weight_kg"]):
        g = round(it["weight_kg"] * 1000)
        for n, (load, ids) in enumerate(bins):
            if load + g <= limit_g:
                bins[n] = (load + g, [*ids, it["item_id"]])
                break
        else:
            bins.append((g, [it["item_id"]]))
    return [ids for _load, ids in bins]


def _create(ws: Path, oid: str, ids: list[str]) -> tuple[dict, int]:
    return call(ws, SERVICE, "create", "--order", oid, *[a for i in ids for a in ("--item", i)])


def gold(ws: Path) -> None:
    notices: list[str] = []

    def ship(*argv: str) -> tuple[dict, int]:
        resp, code = call(ws, SERVICE, *argv)
        notices.extend(resp.get("notices", []))
        return resp, code

    def create(oid: str, ids: list[str], *extra: str) -> tuple[dict, int]:
        return ship("create", "--order", oid, *[a for i in ids for a in ("--item", i)], *extra)

    orders = _orders(ws)
    by_id = {o["order_id"]: o for o in orders}
    probe, code = create("ORD-501", [i["item_id"] for i in orders[0]["items"]])
    assert code == 3, probe
    meaning = ship("explain", "--code", probe["code"])[0]["meaning"]
    assert "0.5 kg packaging" in meaning and "20.0 kg" in meaning, meaning
    limit_g = 20000 - 500
    oversize: set[str] = set()
    for o in orders:
        chk = ship("address-check", "--order", o["order_id"])[0]
        if not chk["deliverable"]:
            sug = chk["suggestion"]
            fixed = ship("address-fix", "--order", o["order_id"], "--street", sug["street"],
                         "--postcode", sug["postcode"], "--city", sug["city"])[0]
            assert fixed["deliverable"], fixed
        for ids in _pack(o["items"], limit_g):
            resp, code = create(o["order_id"], ids)
            assert code == 0 and resp["deliverable"], resp
    handled: set[str] = set()
    while True:
        # The hub refuses some parcels after the fact: re-pack them with the oversize item alone.
        listing = ship("parcels")[0]["parcels"]
        oversize |= {m.group(1) for n in notices for m in [re.search(r"item (\S+) is oversize", n)] if m}
        refused = [p for p in listing if p["state"] == "refused_at_hub" and p["parcel"] not in handled]
        if not refused:
            break
        for p in refused:
            handled.add(p["parcel"])
            big = [i for i in p["items"] if i in oversize]
            assert len(big) == 1, (p, oversize)
            resp, code = create(p["order"], big, "--service", "bulky")
            assert code == 0, resp
            rest = [i for i in by_id[p["order"]]["items"] if i["item_id"] in p["items"] and i["item_id"] not in big]
            for ids in _pack(rest, limit_g):
                create(p["order"], ids)
    assert all(p["deliverable"] for p in listing if p["state"] == "active"), listing


def near_miss_one_parcel_per_order(ws: Path) -> None:
    """Docs literally: `create --order` with all items, relying on create to police addresses."""
    for o in _orders(ws):
        call(ws, SERVICE, "create", "--order", o["order_id"])


def _ship(ws: Path, *, limit_g: int, fix_first: bool) -> None:
    for o in _orders(ws):
        if fix_first:
            chk = call(ws, SERVICE, "address-check", "--order", o["order_id"])[0]
            if not chk["deliverable"]:
                sug = chk["suggestion"]
                call(ws, SERVICE, "address-fix", "--order", o["order_id"], "--street", sug["street"],
                     "--postcode", sug["postcode"], "--city", sug["city"])
        for ids in _pack(o["items"], limit_g):
            _create(ws, o["order_id"], ids)


def near_miss_docs_weight_limit(ws: Path) -> None:
    """Fixes addresses, but packs to the documented 30 kg."""
    _ship(ws, limit_g=30000, fix_first=True)


def near_miss_ignores_packaging(ws: Path) -> None:
    """Fixes addresses and packs to 20 kg of item weight, forgetting the packaging."""
    _ship(ws, limit_g=20000, fix_first=True)


def near_miss_skips_address_check(ws: Path) -> None:
    """Packs correctly but never checks addresses."""
    _ship(ws, limit_g=19500, fix_first=False)


def _ship_then_fix(ws: Path, *, recreate: bool) -> None:
    _ship(ws, limit_g=19500, fix_first=False)
    for o in _orders(ws):
        chk = call(ws, SERVICE, "address-check", "--order", o["order_id"])[0]
        if chk["deliverable"]:
            continue
        sug = chk["suggestion"]
        call(ws, SERVICE, "address-fix", "--order", o["order_id"], "--street", sug["street"],
             "--postcode", sug["postcode"], "--city", sug["city"])
        if recreate:
            for ids in _pack(o["items"], 19500):
                _create(ws, o["order_id"], ids)


def near_miss_fix_after_shipping(ws: Path) -> None:
    """Ships, then fixes addresses believing existing parcels follow the order address."""
    _ship_then_fix(ws, recreate=False)


def near_miss_recreate_without_cancel(ws: Path) -> None:
    """Ships, fixes addresses, re-creates the parcels but never cancels the old ones."""
    _ship_then_fix(ws, recreate=True)


def _gold_until_hub(ws: Path) -> list[dict]:
    """Everything right up to the hub refusal; returns the refused parcels."""
    for o in _orders(ws):
        chk = call(ws, SERVICE, "address-check", "--order", o["order_id"])[0]
        if not chk["deliverable"]:
            sug = chk["suggestion"]
            call(ws, SERVICE, "address-fix", "--order", o["order_id"], "--street", sug["street"],
                 "--postcode", sug["postcode"], "--city", sug["city"])
        for ids in _pack(o["items"], 19500):
            _create(ws, o["order_id"], ids)
    return [p for p in call(ws, SERVICE, "parcels")[0]["parcels"] if p["state"] == "refused_at_hub"]


def near_miss_no_replan_after_hub(ws: Path) -> None:
    """Ships correctly but ignores the parcels the hub refused afterwards."""
    _gold_until_hub(ws)


def near_miss_rebulk_only_oversize(ws: Path) -> None:
    """Re-sends only the oversize item as bulky and forgets the rest of the refused parcel."""
    for p in _gold_until_hub(ws):
        big = [i for i in p["items"] if i in ("504-1", "508-1")]
        call(ws, SERVICE, "create", "--order", p["order"], "--item", big[0], "--service", "bulky")
    call(ws, SERVICE, "parcels")


def near_miss_resend_refused_parcel(ws: Path) -> None:
    """Re-creates the refused parcel unchanged."""
    for p in _gold_until_hub(ws):
        _create(ws, p["order"], p["items"])
    call(ws, SERVICE, "parcels")


def near_miss_one_item_per_parcel(ws: Path) -> None:
    """Fixes addresses and ships every item alone (oversize ones as bulky): valid but wasteful."""
    for o in _orders(ws):
        chk = call(ws, SERVICE, "address-check", "--order", o["order_id"])[0]
        if not chk["deliverable"]:
            sug = chk["suggestion"]
            call(ws, SERVICE, "address-fix", "--order", o["order_id"], "--street", sug["street"],
                 "--postcode", sug["postcode"], "--city", sug["city"])
        for it in o["items"]:
            extra = ["--service", "bulky"] if it["item_id"] in ("504-1", "508-1") else []
            call(ws, SERVICE, "create", "--order", o["order_id"], "--item", it["item_id"], *extra)
    call(ws, SERVICE, "parcels")


NEAR_MISSES = [
    near_miss_no_replan_after_hub,
    near_miss_rebulk_only_oversize,
    near_miss_resend_refused_parcel,
    near_miss_one_item_per_parcel,
    near_miss_one_parcel_per_order,
    near_miss_docs_weight_limit,
    near_miss_ignores_packaging,
    near_miss_skips_address_check,
    near_miss_fix_after_shipping,
    near_miss_recreate_without_cancel,
]

TASK = reflect_task(
    id="task_425_shipping_weight_codes",
    name="Ship orders under a hidden gross weight limit and soft address validation",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"orders.json": _orders_json()},
    tags=("shipping", "error-codes"),
)
