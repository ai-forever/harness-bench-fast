"""Order event catering by a deadline from a supplier that silently backorders.

Deviations from docs/orders.md (all visible in responses):
- `place` is not all-or-nothing: what is not in stock comes back as a separate
  line with `"status": "backorder"` and `eta_day` 9 (a line is split when stock
  covers only part of it), while the order itself is `ok`.
- Lines served from the remote warehouse are `confirmed` but arrive two days
  later than documented (`eta_day` 5 instead of 3), past the deadline.
Substitutes come from `alternatives --sku`, whose first entries are traps: nuts
in `allergens` or `traces`, another category, a product that is itself out of
stock or remote. Only reading every line of every `place` answer works.
- Mid-course: the stock recount on the morning of day 2 finds only 28 cheese
  sandwiches. `advance` reports it in `notices`, and 12 units of the already
  confirmed line are moved to a new backorder line. Ordering on day 2 still
  arrives on day 4, on day 3 it is too late; the original is gone, so the
  remainder must come from a nut-free alternative (the first one has pesto).
"""

from __future__ import annotations

import csv
import io as _io
import json
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

DEADLINE = 4
# sku: (name, category, allergens, traces, stock, warehouse)
PRODUCTS = {
    "sw-cheese": ("Сэндвич с сыром", "sandwiches", ["gluten", "milk"], [], 100, "main"),
    "cookie-choc": ("Печенье шоколадное", "cookies", ["gluten", "milk"], [], 35, "main"),
    "juice-orange": ("Сок апельсиновый 0,33 л", "juice", [], [], 0, "main"),
    "bar-muesli": ("Батончик мюсли", "bars", ["gluten"], [], 0, "main"),
    "fruit-cup": ("Фруктовый стаканчик", "fruit", [], [], 50, "main"),
    "water-still": ("Вода негазированная 0,5 л", "water", [], [], 200, "remote"),
    "cookie-hazelnut": ("Печенье с фундуком", "cookies", ["gluten", "nuts"], [], 80, "main"),
    "cookie-oat": ("Печенье овсяное", "cookies", ["gluten"], [], 90, "remote"),
    "brownie": ("Брауни", "cakes", ["gluten", "egg", "milk"], [], 50, "main"),
    "cookie-butter": ("Печенье сливочное", "cookies", ["gluten", "milk"], [], 30, "main"),
    "juice-apple": ("Сок яблочный 0,33 л", "juice", [], [], 0, "main"),
    "smoothie-mango": ("Смузи манго 0,25 л", "smoothies", ["milk"], [], 60, "main"),
    "juice-multi": ("Сок мультифрукт 0,33 л", "juice", [], [], 100, "main"),
    "bar-almond": ("Батончик миндальный", "bars", ["nuts"], [], 70, "main"),
    "bar-honey-oat": ("Батончик овсяный с мёдом", "bars", ["gluten"], ["nuts"], 60, "main"),
    "bar-fruit": ("Батончик фруктовый", "bars", [], [], 18, "main"),
    "bar-cereal": ("Батончик злаковый", "bars", ["gluten"], [], 40, "main"),
    "water-spring": ("Вода родниковая 0,5 л", "water", [], [], 100, "remote"),
    "water-sparkling": ("Вода газированная 0,5 л", "water", [], [], 120, "main"),
    "sw-pesto": ("Сэндвич с песто", "sandwiches", ["gluten", "milk", "nuts"], [], 40, "main"),
    "sw-veggie": ("Сэндвич овощной", "sandwiches", ["gluten"], [], 30, "main"),
}
ALTERNATIVES = {
    "cookie-choc": ["cookie-hazelnut", "cookie-oat", "brownie", "cookie-butter"],
    "juice-orange": ["juice-apple", "smoothie-mango", "juice-multi"],
    "bar-muesli": ["bar-almond", "bar-honey-oat", "bar-fruit", "bar-cereal"],
    "water-still": ["water-spring", "water-sparkling"],
    "sw-cheese": ["sw-pesto", "sw-veggie"],
    "fruit-cup": [],
}
NEEDS = {"sw-cheese": 40, "cookie-choc": 60, "juice-orange": 48, "bar-muesli": 30, "fruit-cup": 20, "water-still": 60}
NEEDS_CSV = "sku,name,qty\n" + "".join(f'{sku},"{PRODUCTS[sku][0]}",{qty}\n' for sku, qty in NEEDS.items())

_SOURCE = r'''
PRODUCTS = __PRODUCTS__
ALTERNATIVES = __ALTERNATIVES__
LEAD = {"main": 2, "remote": 4}
BACKORDER_ETA = 9
RECOUNT = {"sw-cheese": 28}  # physical stock found by the recount on the morning of day 2


def initial_state():
    return {"day": 1, "stock": {sku: p[4] for sku, p in PRODUCTS.items()}, "orders": [],
            "inventory": {}, "inventory_at_day": {"1": {}}}


def _recount(state):
    notices = []
    for sku, physical in RECOUNT.items():
        lines = [(o, line) for o in state["orders"] for line in o["lines"]
                 if line["sku"] == sku and line["status"] == "confirmed"]
        allocated = sum(line["qty"] for _, line in lines)
        cut = max(0, allocated + state["stock"][sku] - physical)
        from_free = min(cut, state["stock"][sku])
        state["stock"][sku] -= from_free
        cut -= from_free
        for order, line in reversed(lines):
            if cut <= 0:
                break
            moved = min(cut, line["qty"])
            cut -= moved
            line["qty"] -= moved
            if line["qty"] == 0:
                line["status"] = "cancelled"
            new = {"line": len(order["lines"]) + 1, "sku": sku, "qty": moved, "status": "backorder",
                   "eta_day": BACKORDER_ETA}
            order["lines"].append(new)
            notices.append("stock recount: %s is short; order %s line %d reduced to %d, %d units moved to "
                           "backorder as line %d (eta_day %d)" % (sku, order["id"], line["line"], line["qty"],
                                                                  moved, new["line"], BACKORDER_ETA))
    return notices


def _card(sku):
    name, cat, allergens, traces = PRODUCTS[sku][:4]
    return {"sku": sku, "name": name, "category": cat, "allergens": list(allergens), "traces": list(traces)}


def _find(state, oid):
    for order in state["orders"]:
        if order["id"] == oid:
            return order
    return None


def _order_view(order):
    return {"order": order["id"], "placed_day": order["day"],
            "lines": [dict(line) for line in order["lines"]]}


def handle(state, words, opts, io):
    cmd = words[0] if words else ""
    day = state["day"]
    if cmd == "product":
        sku = opts.get("sku")
        if sku not in PRODUCTS:
            return {"status": "error", "error": "unknown sku"}, 2
        return dict(_card(sku), status="ok"), 0
    if cmd == "alternatives":
        sku = opts.get("sku")
        if sku not in PRODUCTS:
            return {"status": "error", "error": "unknown sku"}, 2
        return {"status": "ok", "sku": sku, "alternatives": [_card(s) for s in ALTERNATIVES.get(sku, [])]}, 0
    if cmd == "place":
        items = opts.get("item")
        if items is None:
            return {"status": "error", "error": "no --item given"}, 2
        items = items if isinstance(items, list) else [items]
        wanted = []
        for raw in items:
            if not isinstance(raw, str) or "=" not in raw:
                return {"status": "error", "error": f"bad --item {raw!r}, expected SKU=QTY"}, 2
            sku, qty = raw.split("=", 1)
            if sku not in PRODUCTS:
                return {"status": "error", "error": f"unknown sku {sku!r}"}, 2
            try:
                qty = int(qty)
            except ValueError:
                return {"status": "error", "error": f"bad quantity {qty!r}"}, 2
            if qty <= 0:
                return {"status": "error", "error": "quantity must be positive"}, 2
            wanted.append((sku, qty))
        oid = "O-%d" % (len(state["orders"]) + 1)
        order = {"id": oid, "day": day, "lines": []}
        for sku, qty in wanted:
            take = min(qty, state["stock"][sku])
            state["stock"][sku] -= take
            if take:
                order["lines"].append({"line": len(order["lines"]) + 1, "sku": sku, "qty": take,
                                       "status": "confirmed", "eta_day": day + LEAD[PRODUCTS[sku][5]]})
            if qty - take:
                order["lines"].append({"line": len(order["lines"]) + 1, "sku": sku, "qty": qty - take,
                                       "status": "backorder", "eta_day": BACKORDER_ETA})
        state["orders"].append(order)
        return dict(_order_view(order), status="ok"), 0
    if cmd == "order":
        order = _find(state, opts.get("id"))
        if order is None:
            return {"status": "error", "error": "no order with that id"}, 2
        return dict(_order_view(order), status="ok"), 0
    if cmd == "orders":
        return {"status": "ok", "orders": [_order_view(o) for o in state["orders"]]}, 0
    if cmd == "cancel-line":
        order = _find(state, opts.get("order"))
        if order is None:
            return {"status": "error", "error": "no order with that id"}, 2
        for line in order["lines"]:
            if str(line["line"]) == str(opts.get("line")):
                if line["status"] in ("delivered", "cancelled"):
                    return {"status": "error", "error": f"line is already {line['status']}"}, 2
                if line["status"] == "confirmed":
                    state["stock"][line["sku"]] += line["qty"]
                line["status"] = "cancelled"
                return {"status": "ok", "order": order["id"], "line": dict(line)}, 0
        return {"status": "error", "error": "no such line in that order"}, 2
    if cmd == "cancel":
        order = _find(state, opts.get("order"))
        if order is None:
            return {"status": "error", "error": "no order with that id"}, 2
        cancelled = []
        for line in order["lines"]:
            if line["status"] in ("confirmed", "backorder"):
                if line["status"] == "confirmed":
                    state["stock"][line["sku"]] += line["qty"]
                line["status"] = "cancelled"
                cancelled.append(line["line"])
        return {"status": "ok", "order": order["id"], "cancelled_lines": cancelled}, 0
    if cmd == "advance":
        state["day"] += 1
        notices = _recount(state) if state["day"] == 2 else []
        arrived = []
        for order in state["orders"]:
            for line in order["lines"]:
                if line["status"] == "confirmed" and line["eta_day"] <= state["day"]:
                    line["status"] = "delivered"
                    state["inventory"][line["sku"]] = state["inventory"].get(line["sku"], 0) + line["qty"]
                    arrived.append({"sku": line["sku"], "qty": line["qty"]})
        state["inventory_at_day"][str(state["day"])] = dict(state["inventory"])
        resp = {"status": "ok", "day": state["day"], "arrived": arrived}
        if notices:
            resp["notices"] = notices
        return resp, 0
    if cmd == "inventory":
        return {"status": "ok", "day": day, "inventory": dict(state["inventory"])}, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/orders.md"}, 2
'''

SOURCE = (_SOURCE.replace("__PRODUCTS__", json.dumps(PRODUCTS, ensure_ascii=False, sort_keys=True))
          .replace("__ALTERNATIVES__", json.dumps(ALTERNATIVES, sort_keys=True)))

SERVICE = Service(tool="orders", title="the Hapje catering supplier", source=SOURCE, key=b"reflect-19-orders-b8e5")

DOCS = """
# orders — заказы у поставщика кейтеринга

Клиент: `python3 tools/orders <команда> [--опция значение ...]`. Каждая команда печатает JSON.

| Команда | Что делает |
|---|---|
| `product --sku SKU` | Карточка товара: `name`, `category`, `allergens`, `traces` (возможные следы). |
| `alternatives --sku SKU` | Список возможных замен товара (карточки в том же формате). |
| `place --item SKU=QTY [--item SKU=QTY ...]` | Новый заказ. |
| `order --id ID` / `orders` | Один заказ / все заказы со строками. |
| `cancel-line --order ID --line N` | Отменяет строку заказа, пока она не доставлена. |
| `cancel --order ID` | Отменяет все недоставленные строки заказа. |
| `advance` | Переход к утру следующего дня; доставленное попадает в `inventory`. |
| `inventory` | Что уже получено (в штуках). |

## place

Заказ собирается со склада **целиком или не принимается вовсе**: если какого-то товара
не хватает, `place` возвращает ошибку `out_of_stock`, и ничего не резервируется.
Принятый заказ получает `"status": "ok"`, каждая его строка — `"status": "confirmed"`.

Доставка — через два дня: заказ, размещённый утром дня N, приходит к утру дня N+2
(поле `eta_day` в строках).

```json
{"status": "ok", "order": "O-1", "placed_day": 1,
 "lines": [{"line": 1, "sku": "sw-cheese", "qty": 40, "status": "confirmed", "eta_day": 3}]}
```

Строки заказа: `confirmed` → `delivered` (после `advance` в день `eta_day`), либо `cancelled`.
"""

PROMPT = f"""
Мы кейтерим мероприятие. Сейчас утро 1-го дня; всё из `needs.csv` (`sku,name,qty`)
должно быть у нас (в `inventory`) к утру {DEADLINE}-го дня — закажите у поставщика
`orders`. По каждой строке `needs.csv` нужно получить ровно указанное количество: либо
сам товар, либо замену из `alternatives` для этого товара, либо их сочетание.

Ограничения:
- у гостей аллергия на орехи: ни в одном полученном товаре не должно быть `nuts` — ни в
  `allergens`, ни в `traces`;
- замена допустима только в той же категории (`category`), что и исходный товар;
- к концу работы у поставщика не должно остаться незакрытых строк: всё заказанное либо
  доставлено к утру {DEADLINE}-го дня, либо отменено.

Закончив с заказами, переведите время до утра {DEADLINE}-го дня (`advance`) и
проверьте `inventory`.
""" + PROMPT_RULES.format(tool="orders")


def _nut_free(sku: str) -> bool:
    return "nuts" not in PRODUCTS[sku][2] and "nuts" not in PRODUCTS[sku][3]


def check(result: Replay, ws: Path) -> str | None:
    state = result.state
    snapshot = state["inventory_at_day"].get(str(DEADLINE))
    if snapshot is None:
        return f"the service never reached the morning of day {DEADLINE}"
    open_lines = [line for o in state["orders"] for line in o["lines"]
                  if line["status"] == "backorder" or (line["status"] == "confirmed" and line["eta_day"] > DEADLINE)]
    if open_lines:
        return f"{len(open_lines)} order lines are still open (backordered or arriving after day {DEADLINE})"
    late = [s for s, q in state["inventory"].items() if q != snapshot.get(s, 0)]
    if late:
        return f"some goods arrived after the morning of day {DEADLINE}"
    categories = {PRODUCTS[sku][1]: (sku, qty) for sku, qty in NEEDS.items()}
    got: dict[str, int] = {}
    for sku, qty in snapshot.items():
        if not _nut_free(sku):
            return f"received {sku}, which contains nuts or traces of nuts"
        cat = PRODUCTS[sku][1]
        if cat not in categories or (sku not in NEEDS and sku not in ALTERNATIVES[categories[cat][0]]):
            return f"received {sku}, which is not an allowed substitute for any need"
        got[cat] = got.get(cat, 0) + qty
    for cat, (sku, qty) in categories.items():
        if got.get(cat, 0) != qty:
            return f"for {sku} received {got.get(cat, 0)} units instead of {qty}"
    return None


def _needs(ws: Path) -> dict[str, int]:
    rows = csv.DictReader(_io.StringIO((ws / "needs.csv").read_text(encoding="utf-8")))
    return {r["sku"]: int(r["qty"]) for r in rows}


def _place(ws: Path, items: dict[str, int]) -> dict:
    resp, code = call(ws, SERVICE, "place", *[a for sku, q in items.items() for a in ("--item", f"{sku}={q}")])
    assert code == 0, resp
    return resp


def _reconcile(ws: Path, needs: dict[str, int], source: dict[str, str], *, check_eta: bool = True,
               cancel_bad: bool = True, check_traces: bool = True, check_category: bool = True) -> None:
    """Cancel lines that will not arrive by the deadline and cover each shortfall from alternatives."""
    have = dict.fromkeys(needs, 0)
    for order in call(ws, SERVICE, "orders")[0]["orders"]:
        for line in order["lines"]:
            on_time = line["status"] == "delivered" or (
                line["status"] == "confirmed" and (not check_eta or line["eta_day"] <= DEADLINE))
            if on_time:
                have[source[line["sku"]]] += line["qty"]
            elif line["status"] in ("confirmed", "backorder") and cancel_bad:
                call(ws, SERVICE, "cancel-line", "--order", order["order"], "--line", str(line["line"]))
    for sku, qty in needs.items():
        missing = qty - have[sku]
        if missing <= 0:
            continue
        card = call(ws, SERVICE, "product", "--sku", sku)[0] if check_category else {}
        for alt in call(ws, SERVICE, "alternatives", "--sku", sku)[0]["alternatives"]:
            if missing <= 0:
                break
            if "nuts" in alt["allergens"] or (check_traces and "nuts" in alt["traces"]):
                continue
            if check_category and alt["category"] != card["category"]:
                continue
            source[alt["sku"]] = sku
            order = _place(ws, {alt["sku"]: missing})
            for line in order["lines"]:
                if line["status"] == "confirmed" and (not check_eta or line["eta_day"] <= DEADLINE):
                    missing -= line["qty"]
                elif cancel_bad:
                    call(ws, SERVICE, "cancel-line", "--order", order["order"], "--line", str(line["line"]))


def _solve(ws: Path, *, replan: bool = True, **rules: bool) -> None:
    needs = _needs(ws)
    source = {sku: sku for sku in needs}
    _place(ws, needs)
    _reconcile(ws, needs, source, **rules)
    notices = call(ws, SERVICE, "advance")[0].get("notices")
    if notices and replan:
        _reconcile(ws, needs, source, **rules)
    for _ in range(DEADLINE - 2):
        call(ws, SERVICE, "advance")
    call(ws, SERVICE, "inventory")


def gold(ws: Path) -> None:
    _solve(ws)


def near_miss_no_replan(ws: Path) -> None:
    """Handles everything on day 1, then advances without reacting to the recount notice."""
    _solve(ws, replan=False)


def near_miss_trusts_docs(ws: Path) -> None:
    _place(ws, _needs(ws))
    for _ in range(DEADLINE - 1):
        call(ws, SERVICE, "advance")


def near_miss_ignores_eta(ws: Path) -> None:
    """Handles backorders but takes every confirmed line as on time."""
    _solve(ws, check_eta=False)


def near_miss_leaves_backorders(ws: Path) -> None:
    """Substitutes the missing quantity but never cancels the backordered lines."""
    _solve(ws, cancel_bad=False)


def near_miss_allergens_only(ws: Path) -> None:
    """Checks `allergens` for nuts but not `traces`."""
    _solve(ws, check_traces=False)


def near_miss_any_category(ws: Path) -> None:
    """Takes the first nut-free alternative regardless of its category."""
    _solve(ws, check_category=False)


NEAR_MISSES = [
    near_miss_no_replan,
    near_miss_trusts_docs,
    near_miss_ignores_eta,
    near_miss_leaves_backorders,
    near_miss_allergens_only,
    near_miss_any_category,
]

TASK = reflect_task(
    id="reflect_19_orders_backorder",
    name="Cater by a deadline from a supplier that backorders silently and ships late",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"needs.csv": NEEDS_CSV},
    tags=("orders", "substitution"),
)
