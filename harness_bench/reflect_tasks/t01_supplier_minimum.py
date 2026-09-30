"""Restock through a wholesale portal whose orders are in packs and have minimums.

Deviations from docs/shop.md (all visible in responses):
- `offers` prices are per pack (`"per": "pack"`, `pack_size`), and `--item sku=N`
  orders N packs, not N units; the order response echoes the units.
- Each supplier has a minimum order total (`min_order` in `offers`); below it, or
  over the balance, the order is refused with one ambiguous message.
- One supplier needs two days (`lead_days` in `offers`), not the documented one.
- Mid-course surprise: the cheap energy supplier has one pack in stock. The order
  comes back `status: ok`, but half the energy line is backordered to day 6
  (`backordered_units`, `backorder_eta_day`). The budget cannot carry that order
  plus the other drinks supplier's minimum, so the order has to be cancelled and
  the drinks re-planned. Padding the wrong supplier to its minimum also runs out
  of money.
"""

from __future__ import annotations

import math
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

TARGETS = {"cola": 60, "water": 40, "stroopwafel": 30, "energy": 20, "chips": 30, "choco": 24}

SOURCE = r'''
SUPPLIERS = {
    "zuidas": {"name": "Zuidas Groothandel", "min_order": 15000, "lead_days": 1,
               "items": {"cola": (24, 1152), "water": (24, 720), "stroopwafel": (12, 768), "energy": (12, 1680)}},
    "noordzee": {"name": "Noordzee Dranken", "min_order": 12000, "lead_days": 2,
                 "items": {"cola": (24, 1200), "energy": (12, 1548), "water": (24, 768)},
                 "in_stock_packs": {"energy": 1}, "backorder_eta_day": 6},
    "bakker": {"name": "Bakker & Zoon", "min_order": 9000, "lead_days": 1,
               "items": {"chips": (20, 1800), "choco": (24, 1728), "stroopwafel": (12, 816)}},
}


def initial_state():
    return {
        "day": 1,
        "balance": 25000,
        "stock": {"cola": 10, "water": 4, "stroopwafel": 6, "energy": 0, "chips": 2, "choco": 3},
        "orders": [],
        "stock_at_day": {"1": {"cola": 10, "water": 4, "stroopwafel": 6, "energy": 0, "chips": 2, "choco": 3}},
    }


def eur(cents):
    return round(cents / 100, 2)


def handle(state, words, opts, io):
    cmd = words[0] if words else ""
    if cmd == "balance":
        return {"status": "ok", "day": state["day"], "balance": eur(state["balance"])}, 0
    if cmd == "stock":
        return {"status": "ok", "day": state["day"], "stock": dict(state["stock"])}, 0
    if cmd == "suppliers":
        return {"status": "ok", "suppliers": [{"id": k, "name": v["name"]} for k, v in SUPPLIERS.items()]}, 0
    if cmd == "offers":
        sid = opts.get("supplier")
        if sid not in SUPPLIERS:
            return {"status": "error", "error": "unknown supplier"}, 2
        sup = SUPPLIERS[sid]
        return {"status": "ok", "supplier": sid, "min_order": eur(sup["min_order"]), "lead_days": sup["lead_days"],
                "offers": [{"sku": sku, "price": eur(price), "per": "pack", "pack_size": size}
                           for sku, (size, price) in sup["items"].items()]}, 0
    if cmd == "order":
        sid = opts.get("supplier")
        if sid not in SUPPLIERS:
            return {"status": "error", "error": "unknown supplier"}, 2
        sup = SUPPLIERS[sid]
        items = opts.get("item")
        if items is None:
            return {"status": "error", "error": "no --item given"}, 2
        items = items if isinstance(items, list) else [items]
        lines, total = [], 0
        for raw in items:
            if not isinstance(raw, str) or "=" not in raw:
                return {"status": "error", "error": f"bad --item {raw!r}, expected sku=qty"}, 2
            sku, qty = raw.split("=", 1)
            if sku not in sup["items"]:
                return {"status": "error", "error": f"{sid} does not sell {sku}"}, 2
            try:
                packs = int(qty)
            except ValueError:
                return {"status": "error", "error": f"bad quantity {qty!r}"}, 2
            if packs <= 0:
                return {"status": "error", "error": "quantity must be positive"}, 2
            size, price = sup["items"][sku]
            line = {"sku": sku, "qty": packs, "units": packs * size, "price": eur(price)}
            limit = sup.get("in_stock_packs", {}).get(sku)
            if limit is not None and packs > limit - state.setdefault("taken", {}).get(f"{sid}:{sku}", 0):
                ship = max(0, limit - state["taken"].get(f"{sid}:{sku}", 0))
                line.update(shipped_units=ship * size, backordered_units=(packs - ship) * size,
                            backorder_eta_day=sup["backorder_eta_day"])
            lines.append(line)
            total += packs * price
        if total > state["balance"] or total < sup["min_order"]:
            return {"status": "refused",
                    "error": "Order refused: insufficient funds or below supplier minimum. Use offers to budget."}, 3
        state["balance"] -= total
        for line in lines:
            if "shipped_units" in line:
                size = sup["items"][line["sku"]][0]
                key = f"{sid}:{line['sku']}"
                state["taken"][key] = state["taken"].get(key, 0) + line["shipped_units"] // size
        oid = f"PO-{len(state['orders']) + 1:03d}"
        arrival = state["day"] + sup["lead_days"]
        state["orders"].append({"id": oid, "supplier": sid, "lines": lines, "total": total,
                                "arrival_day": arrival, "status": "placed"})
        return {"status": "ok", "order": oid, "total": eur(total), "lines": lines,
                "balance": eur(state["balance"])}, 0
    if cmd == "cancel":
        oid = opts.get("order")
        for order in state["orders"]:
            if order["id"] == oid and order["status"] == "placed":
                order["status"] = "cancelled"
                state["balance"] += order["total"]
                for line in order["lines"]:
                    if "shipped_units" in line:
                        size = SUPPLIERS[order["supplier"]]["items"][line["sku"]][0]
                        state["taken"][f"{order['supplier']}:{line['sku']}"] -= line["shipped_units"] // size
                return {"status": "ok", "order": oid, "refunded": eur(order["total"])}, 0
        return {"status": "error", "error": "no open order with that id"}, 2
    if cmd == "orders":
        return {"status": "ok", "orders": [{"id": o["id"], "supplier": o["supplier"], "total": eur(o["total"]),
                                           "status": o["status"]} for o in state["orders"]]}, 0
    if cmd == "advance":
        state["day"] += 1
        for order in state["orders"]:
            if order["status"] == "placed" and order["arrival_day"] <= state["day"]:
                order["status"] = "delivered"
                for line in order["lines"]:
                    state["stock"][line["sku"]] += line.get("shipped_units", line["units"])
        state["stock_at_day"][str(state["day"])] = dict(state["stock"])
        return {"status": "ok", "day": state["day"], "message": "Deliveries for today have arrived."}, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/shop.md"}, 2
'''

SERVICE = Service(tool="shop", title="the Kanaal wholesale portal", source=SOURCE, key=b"reflect-01-shop-7f3a")

DOCS = """
# shop — оптовый портал

Клиент: `python3 tools/shop <команда> [--опция значение ...]`. Каждая команда печатает JSON.

| Команда | Что делает |
|---|---|
| `balance` | Баланс счёта в евро и текущий день. |
| `stock` | Остатки в автомате, в штуках. |
| `suppliers` | Список поставщиков. |
| `offers --supplier ID` | Цены поставщика: `price` — цена **за штуку** в евро. |
| `order --supplier ID --item SKU=QTY [--item SKU=QTY ...]` | Заказ. `QTY` — количество **штук**. Оплата списывается сразу. |
| `orders` | Ваши заказы. |
| `cancel --order ID` | Отмена ещё не доставленного заказа, деньги возвращаются. |
| `advance` | Переход к утру следующего дня. |

Правила портала:

- Минимальной суммы заказа нет.
- Все поставщики доставляют заказ на следующий день: он появится в `stock` после одного `advance`.
- Если на счёте не хватает денег, заказ отклоняется.
"""

PROMPT = """
Вы снабжаете торговый автомат через оптовый портал `shop`. Сейчас утро 1-го дня.
К утру 3-го дня в автомате должно быть не меньше (в штуках): cola 60, water 40,
stroopwafel 30, energy 20, chips 30, choco 24. Платите с текущего счёта; в минус уйти
нельзя. Закончив с заказами, переведите время до утра 3-го дня (`advance`) и
убедитесь по `stock`, что цель достигнута.
""" + PROMPT_RULES.format(tool="shop")


def check(result: Replay, ws: Path) -> str | None:
    snapshot = result.state["stock_at_day"].get("3")
    if snapshot is None:
        return "the service never reached the morning of day 3"
    short = [sku for sku, need in TARGETS.items() if snapshot.get(sku, 0) < need]
    if short:
        return f"on the morning of day 3 the machine is short of: {', '.join(sorted(short))}"
    return None


def _offers(ws: Path) -> dict[str, dict]:
    listed, _ = call(ws, SERVICE, "suppliers")
    return {s["id"]: call(ws, SERVICE, "offers", "--supplier", s["id"])[0] for s in listed["suppliers"]}


def _pad(prices: dict[str, float], base: float, minimum: float) -> dict[str, int]:
    """Fewest-euro set of extra packs that lifts `base` to at least `minimum` (unbounded knapsack)."""
    cents = {k: round(v * 100) for k, v in prices.items()}
    gap = round(minimum * 100) - round(base * 100)
    if gap <= 0:
        return {}
    limit = gap + max(cents.values())
    best: list[tuple[int, dict[str, int]] | None] = [None] * (limit + 1)
    best[0] = (0, {})
    for amount in range(1, limit + 1):
        for sku, price in cents.items():
            if price <= amount and best[amount - price] is not None:
                prev = best[amount - price]
                if best[amount] is None:
                    best[amount] = (amount, {**prev[1], sku: prev[1].get(sku, 0) + 1})
    return min((b for a, b in enumerate(best) if a >= gap and b is not None), key=lambda b: b[0])[1]


def _plan(offers: dict[str, dict], need: dict[str, int], assign: dict[str, str]) -> dict[str, dict[str, int]]:
    """Packs per supplier for `need` (sku -> supplier in `assign`), padded to each minimum."""
    plan: dict[str, dict[str, int]] = {}
    for sku, units in need.items():
        if units <= 0:
            continue
        sid = assign[sku]
        offer = next(o for o in offers[sid]["offers"] if o["sku"] == sku)
        plan.setdefault(sid, {})[sku] = math.ceil(units / offer["pack_size"])
    for sid, lines in plan.items():
        prices = {o["sku"]: o["price"] for o in offers[sid]["offers"]}
        base = sum(prices[k] * q for k, q in lines.items())
        for sku, extra in _pad(prices, base, offers[sid]["min_order"]).items():
            lines[sku] = lines.get(sku, 0) + extra
    return plan


def _need(ws: Path) -> dict[str, int]:
    stock = call(ws, SERVICE, "stock")[0]["stock"]
    return {sku: TARGETS[sku] - stock.get(sku, 0) for sku in TARGETS}


def _order(ws: Path, sid: str, lines: dict[str, int]) -> tuple[dict, int]:
    return call(ws, SERVICE, "order", "--supplier", sid, *[a for k, q in lines.items() for a in ("--item", f"{k}={q}")])


CHEAP = {"cola": "noordzee", "water": "noordzee", "energy": "noordzee", "stroopwafel": "bakker", "chips": "bakker", "choco": "bakker"}
DRINKS_AT_ZUIDAS = {**CHEAP, "cola": "zuidas", "water": "zuidas", "energy": "zuidas"}


def gold(ws: Path) -> None:
    offers = _offers(ws)
    need = _need(ws)
    # The plan an attentive agent makes first: cheapest drinks at noordzee, snacks at bakker.
    resp, code = _order(ws, "noordzee", _plan(offers, need, CHEAP)["noordzee"])
    assert code == 0, resp
    if any(line.get("backordered_units") for line in resp["lines"]):
        # Energy is half backordered past the deadline: move all drinks to zuidas instead.
        call(ws, SERVICE, "cancel", "--order", resp["order"])
        plan = _plan(offers, need, DRINKS_AT_ZUIDAS)
    else:
        plan = _plan(offers, need, CHEAP)
        plan.pop("noordzee")
    for sid, lines in plan.items():
        resp, code = _order(ws, sid, lines)
        assert code == 0, resp
    call(ws, SERVICE, "advance")
    call(ws, SERVICE, "advance")


def near_miss_units_as_documented(ws: Path) -> None:
    offers = _offers(ws)
    need = _need(ws)
    for sku, units in need.items():
        sid = min((o["price"], s) for s in offers for o in offers[s]["offers"] if o["sku"] == sku)[1]
        call(ws, SERVICE, "order", "--supplier", sid, "--item", f"{sku}={units}")
    call(ws, SERVICE, "advance")
    call(ws, SERVICE, "advance")


def near_miss_packs_without_minimum(ws: Path) -> None:
    offers = _offers(ws)
    for sku, units in _need(ws).items():
        offer = next(o for o in offers[CHEAP[sku]]["offers"] if o["sku"] == sku)
        call(ws, SERVICE, "order", "--supplier", CHEAP[sku], "--item", f"{sku}={math.ceil(units / offer['pack_size'])}")
    call(ws, SERVICE, "advance")
    call(ws, SERVICE, "advance")


def near_miss_ignores_backorder(ws: Path) -> None:
    offers = _offers(ws)
    for sid, lines in _plan(offers, _need(ws), CHEAP).items():
        _order(ws, sid, lines)
    call(ws, SERVICE, "advance")
    call(ws, SERVICE, "advance")


def near_miss_keeps_partial_order(ws: Path) -> None:
    offers = _offers(ws)
    need = _need(ws)
    for sid, lines in _plan(offers, need, CHEAP).items():
        _order(ws, sid, lines)
    # Tops up the missing energy at zuidas without cancelling the half-backordered order.
    _order(ws, "zuidas", _plan(offers, {"energy": 12}, {"energy": "zuidas"})["zuidas"])
    call(ws, SERVICE, "advance")
    call(ws, SERVICE, "advance")


def near_miss_pads_every_supplier(ws: Path) -> None:
    offers = _offers(ws)
    need = _need(ws)
    assign = {**CHEAP, "cola": "zuidas"}
    for sid, lines in _plan(offers, need, assign).items():
        _order(ws, sid, lines)
    call(ws, SERVICE, "advance")
    call(ws, SERVICE, "advance")


NEAR_MISSES = [
    near_miss_units_as_documented,
    near_miss_packs_without_minimum,
    near_miss_ignores_backorder,
    near_miss_keeps_partial_order,
    near_miss_pads_every_supplier,
]

TASK = reflect_task(
    id="reflect_01_supplier_minimum",
    name="Restock through a portal with pack pricing, hidden minimums and lead times",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    tags=("orders", "budget"),
)
