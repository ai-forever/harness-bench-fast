"""Collect order payments through a gateway with undocumented per-card limits.

Deviations from docs/charge.md (all visible in responses):
- Every card has a per-transaction limit: bigger charges are declined with a bare
  code R17; `charge explain --code R17` decodes it and points to `charge limits`.
- Every card has a daily limit. A charge larger than what is left today is not
  declined but *partially approved*: `status: ok`, `approved` < `requested`. Once
  nothing is left the gateway declines with R51 (explained: wait for `advance-day`).
- Mid-course surprise: at the start of day 2 the issuer replaces one card. Charging the
  old token is declined with R54 (explained: see `status`, whose `card` now shows the
  new token), and the new card has its own, lower limits that must be read again.
Trap: treating the partial `ok` as paid leaves orders short; retrying the requested
amount after it overcharges the order (and a refund does not give the daily limit
back, so the budget for day 3 runs out).
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

ORDERS = [
    ("SO-1041", "tok_visa_4411", "1240.00"),
    ("SO-1042", "tok_mc_2290", "2450.00"),
    ("SO-1043", "tok_amex_7003", "845.90"),
    ("SO-1044", "tok_visa_4411", "980.50"),
    ("SO-1045", "tok_visa_9180", "1999.99"),
    ("SO-1046", "tok_amex_7003", "312.35"),
    ("SO-1047", "tok_mc_2290", "760.40"),
    ("SO-1048", "tok_visa_4411", "1890.25"),
    ("SO-1049", "tok_visa_9180", "455.00"),
    ("SO-1050", "tok_amex_7003", "129.99"),
]


def _orders_csv() -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["order_id", "card", "amount_eur"])
    writer.writerows(ORDERS)
    return buf.getvalue()


SOURCE = r'''
ORDERS = {
    "SO-1041": ("tok_visa_4411", 124000), "SO-1042": ("tok_mc_2290", 245000),
    "SO-1043": ("tok_amex_7003", 84590), "SO-1044": ("tok_visa_4411", 98050),
    "SO-1045": ("tok_visa_9180", 199999), "SO-1046": ("tok_amex_7003", 31235),
    "SO-1047": ("tok_mc_2290", 76040), "SO-1048": ("tok_visa_4411", 189025),
    "SO-1049": ("tok_visa_9180", 45500), "SO-1050": ("tok_amex_7003", 12999),
}
LIMITS = {
    "tok_visa_4411": (50000, 150000),
    "tok_mc_2290": (100000, 120000),
    "tok_amex_7003": (30000, 200000),
    "tok_visa_9180": (80000, 90000),
    "tok_mc_5518": (40000, 110000),
}
REPLACED = {"tok_mc_2290": ("tok_mc_5518", 2)}
EXPLAIN = {
    "R17": "Amount exceeds the per-transaction limit of the card. Split the payment into several "
           "charges; the limits of a card are shown by `charge limits --card TOKEN`.",
    "R51": "The daily limit of the card is used up. It is restored at the start of the next business "
           "day (`charge advance-day`); `charge limits --card TOKEN` shows what is left today.",
    "R54": "The card was replaced by the issuer and can no longer be charged. The order's current "
           "card is shown by `charge status --order ID` (field `card`).",
    "R05": "Generic decline by the issuer.",
}


def eur(cents):
    return f"{cents // 100}.{cents % 100:02d}"


def cents(text):
    text = str(text).strip()
    whole, _, frac = text.partition(".")
    if not whole.isdigit() or (frac and (not frac.isdigit() or len(frac) > 2)):
        return None
    return int(whole) * 100 + int((frac + "00")[:2])


def initial_state():
    return {"day": 1, "txs": [], "used": {c: {} for c in LIMITS}, "card": {o: v[0] for o, v in ORDERS.items()},
            "net": {o: 0 for o in ORDERS}, "max_net": {o: 0 for o in ORDERS}}


def used_today(state, card):
    return state["used"][card].get(str(state["day"]), 0)


def order_view(state, oid):
    card, amount = state["card"][oid], ORDERS[oid][1]
    paid = state["net"][oid]
    status = "paid" if paid == amount else ("overpaid" if paid > amount else ("partially_paid" if paid else "unpaid"))
    return {"order": oid, "card": card, "amount": eur(amount), "paid": eur(paid),
            "due": eur(max(amount - paid, 0)), "status": status}


def handle(state, words, opts, io):
    cmd = words[0] if words else ""
    if cmd == "day":
        return {"status": "ok", "day": state["day"]}, 0
    if cmd == "orders":
        return {"status": "ok", "day": state["day"], "orders": [order_view(state, o) for o in ORDERS]}, 0
    if cmd == "status":
        oid = opts.get("order")
        if oid not in ORDERS:
            return {"status": "error", "error": "unknown order"}, 2
        view = order_view(state, oid)
        view["transactions"] = [{"tx": t["id"], "amount": eur(t["amount"]), "day": t["day"],
                                 "refunded": t["refunded"]} for t in state["txs"] if t["order"] == oid]
        return {"status": "ok", **view}, 0
    if cmd == "pay":
        oid, card = opts.get("order"), opts.get("card")
        if oid not in ORDERS:
            return {"status": "error", "error": "unknown order"}, 2
        if card == ORDERS[oid][0] and card != state["card"][oid]:
            return {"status": "declined", "code": "R54", "error": "card declined (R54)"}, 4
        if card != state["card"][oid]:
            return {"status": "error", "error": "card does not match the card on file for this order"}, 2
        amount = cents(opts.get("amount", ""))
        if not amount:
            return {"status": "error", "error": "--amount must be a positive amount in EUR, e.g. 120.50"}, 2
        per_tx, daily = LIMITS[card]
        if amount > per_tx:
            return {"status": "declined", "code": "R17", "error": "card declined (R17)"}, 4
        left = daily - used_today(state, card)
        if left <= 0:
            return {"status": "declined", "code": "R51", "error": "card declined (R51)"}, 4
        approved = min(amount, left)
        state["used"][card][str(state["day"])] = used_today(state, card) + approved
        tx = {"id": f"TX-{len(state['txs']) + 1:04d}", "order": oid, "card": card, "amount": approved,
              "day": state["day"], "refunded": False}
        state["txs"].append(tx)
        state["net"][oid] += approved
        state["max_net"][oid] = max(state["max_net"][oid], state["net"][oid])
        return {"status": "ok", "tx": tx["id"], "order": oid, "requested": eur(amount),
                "approved": eur(approved), "day": state["day"]}, 0
    if cmd == "refund":
        tid = opts.get("tx")
        for tx in state["txs"]:
            if tx["id"] == tid:
                if tx["refunded"]:
                    return {"status": "error", "error": "transaction already refunded"}, 2
                tx["refunded"] = True
                state["net"][tx["order"]] -= tx["amount"]
                return {"status": "ok", "tx": tid, "refunded": eur(tx["amount"])}, 0
        return {"status": "error", "error": "unknown transaction"}, 2
    if cmd == "advance-day":
        state["day"] += 1
        for old, (new, day) in REPLACED.items():
            if state["day"] >= day:
                for o in state["card"]:
                    if state["card"][o] == old:
                        state["card"][o] = new
        return {"status": "ok", "day": state["day"], "message": "Business day closed; settlement done."}, 0
    if cmd == "explain":
        code = str(opts.get("code", "")).upper()
        if code not in EXPLAIN:
            return {"status": "error", "error": f"unknown code {code!r}"}, 2
        return {"status": "ok", "code": code, "meaning": EXPLAIN[code]}, 0
    if cmd == "limits":
        card = opts.get("card")
        if card not in LIMITS:
            return {"status": "error", "error": "unknown card; pass --card TOKEN"}, 2
        per_tx, daily = LIMITS[card]
        return {"status": "ok", "card": card, "day": state["day"], "per_transaction": eur(per_tx),
                "daily_limit": eur(daily), "used_today": eur(used_today(state, card)),
                "remaining_today": eur(daily - used_today(state, card))}, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/charge.md"}, 2
'''

SERVICE = Service(tool="charge", title="the card payment gateway", source=SOURCE, key=b"reflect-13-charge-9b52")

DOCS = """
# charge — платёжный шлюз

Клиент: `python3 tools/charge <команда> [--опция значение ...]`. Каждая команда печатает JSON.
Суммы — в евро с точкой, до двух знаков после неё (`120.50`).

| Команда | Что делает |
|---|---|
| `day` | Текущий операционный день. |
| `orders` | Заказы: сумма, оплачено (`paid`), остаток (`due`), статус. |
| `status --order ID` | Заказ и его транзакции. |
| `pay --order ID --card TOKEN --amount EUR` | Списание с карты в счёт заказа. Ответ: `status: ok` и `tx` — списана вся сумма. |
| `refund --tx TX` | Полный возврат транзакции. |
| `advance-day` | Закрыть операционный день и перейти к следующему. |
| `explain --code CODE` | Расшифровка кода отказа. |

Правила:

- Шлюз не ограничивает сумму списания: заказ на любую сумму оплачивается одним `pay`.
- `pay` либо списывает всю сумму (`status: ok`), либо отклоняется целиком
  (`status: declined`, код в `code`, код выхода 4). Подробности отказа — `explain`.
- Карта в `pay` должна совпадать с картой заказа.
- По одному заказу можно провести несколько списаний; шлюз не проверяет, что их сумма не
  превышает сумму заказа.
"""

PROMPT = """
Спишите оплату за заказы из `orders.csv` (номер заказа, токен карты, сумма в евро) через
платёжный шлюз `charge`. Сейчас 1-й операционный день.

Условие успеха: к концу 3-го операционного дня каждый заказ оплачен ровно на свою сумму —
всеми списаниями, проведёнными в 1–3-й день. Ни один заказ ни в какой момент не должен быть
списан сверх своей суммы (переплату нельзя «исправить» возвратом). Списания в 4-й день и
позже не засчитываются.
""" + PROMPT_RULES.format(tool="charge")


def _cents(text: str) -> int:
    return int((Decimal(text) * 100).to_integral_value())


def check(result: Replay, ws: Path) -> str | None:
    state = result.state
    for oid, _card, amount in ORDERS:
        need = _cents(amount)
        if state["max_net"][oid] > need:
            return f"order {oid} was charged more than its amount"
        paid = sum(t["amount"] for t in state["txs"] if t["order"] == oid and not t["refunded"] and t["day"] <= 3)
        if paid != need:
            return f"order {oid} is not paid exactly by the end of day 3"
    return None


def _read_orders(ws: Path) -> list[dict[str, str]]:
    with (ws / "orders.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _eur(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def gold(ws: Path) -> None:
    orders = _read_orders(ws)
    probe, code = call(ws, SERVICE, "pay", "--order", orders[1]["order_id"], "--card", orders[1]["card"],
                       "--amount", orders[1]["amount_eur"])
    assert code == 4, probe
    call(ws, SERVICE, "explain", "--code", probe["code"])
    for day in (1, 2, 3):
        listed = call(ws, SERVICE, "orders")[0]["orders"]
        dues = {o["order"]: _cents(o["due"]) for o in listed}
        cards = {o["order"]: o["card"] for o in listed}  # the issuer may replace a card overnight
        for card in sorted({cards[o["order_id"]] for o in orders if dues[o["order_id"]]}):
            lim = call(ws, SERVICE, "limits", "--card", card)[0]
            per_tx, left = _cents(lim["per_transaction"]), _cents(lim["remaining_today"])
            for o in orders:
                if cards[o["order_id"]] != card:
                    continue
                due = dues[o["order_id"]]
                while due and left:
                    amount = min(due, per_tx, left)
                    resp, code = call(ws, SERVICE, "pay", "--order", o["order_id"], "--card", card,
                                      "--amount", _eur(amount))
                    assert code == 0, resp
                    got = _cents(resp["approved"])
                    due, left = due - got, left - got
        if day < 3:
            call(ws, SERVICE, "advance-day")
    final = call(ws, SERVICE, "orders")[0]["orders"]
    assert all(o["status"] == "paid" for o in final), final


def near_miss_one_charge_each(ws: Path) -> None:
    """Docs literally: one pay per order for the full amount."""
    for o in _read_orders(ws):
        call(ws, SERVICE, "pay", "--order", o["order_id"], "--card", o["card"], "--amount", o["amount_eur"])


def _split(ws: Path, *, trust_ok: bool, advance: bool) -> None:
    """Splits by the per-transaction limit and waits on R51; bookkeeping differs."""
    orders = _read_orders(ws)
    todo = {o["order_id"]: _cents(o["amount_eur"]) for o in orders}
    for _day in (1, 2, 3):
        card_of = {o["order"]: o["card"] for o in call(ws, SERVICE, "orders")[0]["orders"]}
        per_tx = {c: _cents(call(ws, SERVICE, "limits", "--card", c)[0]["per_transaction"]) for c in set(card_of.values())}
        for oid, due in list(todo.items()):
            while due:
                amount = min(due, per_tx[card_of[oid]])
                resp, code = call(ws, SERVICE, "pay", "--order", oid, "--card", card_of[oid], "--amount", _eur(amount))
                if code != 0:
                    break
                if trust_ok or resp["approved"] == resp["requested"]:
                    due -= amount
            todo[oid] = due
        if not advance:
            break
        call(ws, SERVICE, "advance-day")


def near_miss_trusts_partial_ok(ws: Path) -> None:
    """Handles R17 and R51, but counts a partially approved `ok` as the requested amount."""
    _split(ws, trust_ok=True, advance=True)


def near_miss_retries_partial(ws: Path) -> None:
    """Notices a partial approval but retries the requested amount, overcharging the order."""
    _split(ws, trust_ok=False, advance=True)


def near_miss_keeps_csv_card(ws: Path) -> None:
    """Exact plan from the csv cards and live limits, but never re-plans when a card is replaced."""
    orders = _read_orders(ws)
    for day in (1, 2, 3):
        dues = {o["order"]: _cents(o["due"]) for o in call(ws, SERVICE, "orders")[0]["orders"]}
        for card in sorted({o["card"] for o in orders}):
            lim = call(ws, SERVICE, "limits", "--card", card)[0]
            per_tx, left = _cents(lim["per_transaction"]), _cents(lim["remaining_today"])
            for o in (o for o in orders if o["card"] == card):
                due = dues[o["order_id"]]
                while due and left:
                    resp, code = call(ws, SERVICE, "pay", "--order", o["order_id"], "--card", card,
                                      "--amount", _eur(min(due, per_tx, left)))
                    if code != 0:
                        break
                    due, left = due - _cents(resp["approved"]), left - _cents(resp["approved"])
        if day < 3:
            call(ws, SERVICE, "advance-day")


def near_miss_new_card_old_limits(ws: Path) -> None:
    """Switches to the replacement card, but keeps planning with the old card's limits."""
    orders = _read_orders(ws)
    limits = {c: call(ws, SERVICE, "limits", "--card", c)[0] for c in sorted({o["card"] for o in orders})}
    for day in (1, 2, 3):
        listed = {o["order"]: o for o in call(ws, SERVICE, "orders")[0]["orders"]}
        left = {c: _cents(v["daily_limit"]) for c, v in limits.items()}
        for o in orders:
            card, due = listed[o["order_id"]]["card"], _cents(listed[o["order_id"]]["due"])
            per_tx = _cents(limits[o["card"]]["per_transaction"])
            while due and left[o["card"]]:
                resp, code = call(ws, SERVICE, "pay", "--order", o["order_id"], "--card", card,
                                  "--amount", _eur(min(due, per_tx, left[o["card"]])))
                if code != 0:
                    break
                due -= _cents(resp["approved"])
                left[o["card"]] -= _cents(resp["approved"])
        if day < 3:
            call(ws, SERVICE, "advance-day")


def near_miss_never_advances(ws: Path) -> None:
    """Splits and reads approvals, but never moves to the next business day."""
    orders = _read_orders(ws)
    for o in orders:
        per_tx = _cents(call(ws, SERVICE, "limits", "--card", o["card"])[0]["per_transaction"])
        due = _cents(o["amount_eur"])
        while due:
            resp, code = call(ws, SERVICE, "pay", "--order", o["order_id"], "--card", o["card"],
                              "--amount", _eur(min(due, per_tx)))
            if code != 0:
                break
            due -= _cents(resp["approved"])


def near_miss_refunds_overcharge(ws: Path) -> None:
    """Retries the requested amount after a partial approval, then refunds the extra charge."""
    _split(ws, trust_ok=False, advance=True)
    for o in _read_orders(ws):
        st = call(ws, SERVICE, "status", "--order", o["order_id"])[0]
        if st["status"] == "overpaid":
            for tx in reversed(st["transactions"]):
                if not tx["refunded"]:
                    call(ws, SERVICE, "refund", "--tx", tx["tx"])
                    break


NEAR_MISSES = [
    near_miss_one_charge_each,
    near_miss_trusts_partial_ok,
    near_miss_retries_partial,
    near_miss_never_advances,
    near_miss_keeps_csv_card,
    near_miss_new_card_old_limits,
    near_miss_refunds_overcharge,
]

TASK = reflect_task(
    id="reflect_13_payments_split_limits",
    name="Collect payments under hidden per-transaction and daily card limits",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"orders.csv": _orders_csv()},
    tags=("payments", "limits"),
)
