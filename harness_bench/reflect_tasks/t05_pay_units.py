"""Settle invoices exactly through a payments API that counts in cents.

Deviations from docs/pay.md (all visible in responses):
- `send --amount` is documented in euros but is read as cents (half-up to a whole
  cent); the response echoes `amount_eur` and the invoice's `remaining_eur`.
- An overpaid invoice is reported as `paid`; only the undocumented `credit_eur`
  (in `send` and `invoice`) shows the excess.
- `refund --tx` is documented with a partial `--amount`; the amount is ignored and
  the whole transaction is refunded (`refunded_eur`).
Several invoices were already partly paid, so after a misread first payment the
obvious fix (pay the file amount again, in cents) overpays, and trimming the excess
with a partial refund wipes out the whole payment.
- Mid-course surprise: a payment above 2500.00 EUR answers `status: ok` but only
  2500.00 goes through (`amount_eur`, `requested_eur`, a `warnings` entry, the invoice
  stays `partially_paid`); the rest has to be sent as a further payment.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

# id -> (vendor, amount in cents, already paid in cents)
INVOICES = {
    "INV-2601": ("Hanze Logistiek", 127360, 0),
    "INV-2602": ("Brouwer Print", 31840, 0),
    "INV-2603": ("Delta Schoonmaak", 98765, 0),
    "INV-2604": ("Kade IT", 441035, 100000),
    "INV-2605": ("Molen Catering", 63680, 0),
    "INV-2606": ("Pier Energie", 231280, 0),
    "INV-2607": ("Vaart Advocaten", 74219, 0),
    "INV-2608": ("Duin Media", 15920, 0),
    "INV-2609": ("Rijn Transport", 312045, 12045),
    "INV-2610": ("Zeil Kantoor", 21999, 0),
    "INV-2611": ("Polder Beveiliging", 150000, 0),
    "INV-2612": ("Gracht Bouw", 5810, 0),
    "INV-2613": ("Sluis Software", 115640, 53760),
    "INV-2614": ("Eiland Drukwerk", 40960, 40960),
    "INV-2615": ("Haven Verzekering", 8620, 0),
}
TO_PAY = ["INV-2601", "INV-2602", "INV-2603", "INV-2604", "INV-2605", "INV-2606", "INV-2608", "INV-2609",
          "INV-2610", "INV-2612", "INV-2613", "INV-2615"]
BALANCE = sum(INVOICES[i][1] - INVOICES[i][2] for i in TO_PAY) + 30000


def _csv() -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["invoice", "vendor", "amount_eur"])
    for inv in TO_PAY:
        vendor, amount, _ = INVOICES[inv]
        writer.writerow([inv, vendor, f"{amount / 100:.2f}"])
    return buf.getvalue()


SOURCE = r'''
from decimal import Decimal as _D, InvalidOperation as _Bad, ROUND_HALF_UP as _UP
INVOICES = __INVOICES__
BALANCE = __BALANCE__
MAX_SINGLE = 250000


def initial_state():
    return {"balance": BALANCE, "txs": [],
            "invoices": {i: {"vendor": v, "amount": a, "prepaid": p} for i, (v, a, p) in INVOICES.items()}}


def eur(cents):
    return round(cents / 100, 2)


def paid(state, inv):
    return state["invoices"][inv]["prepaid"] + sum(
        t["amount"] for t in state["txs"] if t["invoice"] == inv and not t["refunded"])


def summary(state, inv):
    rec = state["invoices"][inv]
    total = paid(state, inv)
    status = "open" if total == 0 else ("partially_paid" if total < rec["amount"] else "paid")
    out = {"invoice": inv, "vendor": rec["vendor"], "amount_eur": eur(rec["amount"]), "paid_eur": eur(total),
           "status": status, "remaining_eur": eur(max(rec["amount"] - total, 0))}
    if total > rec["amount"]:
        out["credit_eur"] = eur(total - rec["amount"])
    return out


def handle(state, words, opts, io):
    cmd = words[0] if words else ""
    if cmd == "balance":
        return {"status": "ok", "balance_eur": eur(state["balance"])}, 0
    if cmd == "invoices":
        return {"status": "ok", "invoices": [{k: v for k, v in summary(state, i).items() if k != "credit_eur"}
                                             for i in sorted(state["invoices"])]}, 0
    if cmd == "invoice":
        inv = opts.get("id")
        if inv not in state["invoices"]:
            return {"status": "error", "error": "no such invoice"}, 4
        out = summary(state, inv)
        out["payments"] = [{"tx": t["tx"], "amount_eur": eur(t["amount"]), "refunded": t["refunded"]}
                           for t in state["txs"] if t["invoice"] == inv]
        return {"status": "ok", **out}, 0
    if cmd == "send":
        inv = opts.get("invoice")
        if inv not in state["invoices"]:
            return {"status": "error", "error": "no such invoice"}, 4
        try:
            cents = int(_D(str(opts.get("amount"))).quantize(_D("1"), rounding=_UP))
        except (_Bad, ValueError):
            return {"status": "error", "error": "--amount must be a number"}, 2
        if cents <= 0:
            return {"status": "error", "error": "amount must be positive"}, 2
        if cents > state["balance"]:
            return {"status": "declined", "error": "payment declined"}, 3
        requested = cents
        cents = min(cents, MAX_SINGLE)
        state["balance"] -= cents
        tx = f"TX-{len(state['txs']) + 1:04d}"
        state["txs"].append({"tx": tx, "invoice": inv, "amount": cents, "refunded": False})
        out = summary(state, inv)
        resp = {"status": "ok", "tx": tx, "invoice": inv, "amount_eur": eur(cents), "invoice_status": out["status"],
                "remaining_eur": out["remaining_eur"]}
        if "credit_eur" in out:
            resp["credit_eur"] = out["credit_eur"]
        if requested > cents:
            resp["requested_eur"] = eur(requested)
            resp["warnings"] = [f"amount above the single-payment limit of {eur(MAX_SINGLE):.2f} was capped"]
        return resp, 0
    if cmd == "refund":
        tx = opts.get("tx")
        for t in state["txs"]:
            if t["tx"] == tx:
                if t["refunded"]:
                    return {"status": "error", "error": "transaction already refunded"}, 3
                t["refunded"] = True
                state["balance"] += t["amount"]
                out = summary(state, t["invoice"])
                return {"status": "ok", "tx": tx, "refunded_eur": eur(t["amount"]), "invoice": t["invoice"],
                        "invoice_status": out["status"], "remaining_eur": out["remaining_eur"]}, 0
        return {"status": "error", "error": "no such transaction"}, 4
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/pay.md"}, 2
'''.replace("__INVOICES__", repr(INVOICES)).replace("__BALANCE__", repr(BALANCE))

SERVICE = Service(tool="pay", title="the Grachtbank supplier payments API", source=SOURCE, key=b"reflect-05-pay-91d4")

DOCS = """
# pay — оплата счетов поставщиков

Клиент: `python3 tools/pay <команда> [--опция значение ...]`. Каждая команда печатает JSON.
Все суммы — в евро.

| Команда | Что делает |
|---|---|
| `balance` | Остаток на счёте компании. |
| `invoices` | Все счета: сумма, оплачено, статус (`open`, `partially_paid`, `paid`), остаток к оплате. |
| `invoice --id ID` | Один счёт и список платежей по нему. |
| `send --invoice ID --amount EUR` | Платёж по счёту на сумму в евро, например `--amount 1234.50`. Возвращает номер транзакции `tx`. |
| `refund --tx TX [--amount EUR]` | Возврат по транзакции: без `--amount` — полный, с `--amount` — частичный на указанную сумму. |

Если денег на счёте не хватает, платёж отклоняется. Статус счёта `paid` означает, что
счёт оплачен ровно на свою сумму.
"""

PROMPT = """
Оплатите через API `pay` счета поставщиков из файла `invoices.csv` (колонки `invoice`,
`vendor`, `amount_eur` — полная сумма счёта в евро). После вашей работы каждый счёт из
файла должен быть оплачен ровно на свою сумму — не меньше и не больше, с точностью до
цента, с учётом того, что по некоторым счетам уже были платежи. По счетам, которых
нет в файле, итоговых платежей быть не должно.
""" + PROMPT_RULES.format(tool="pay")


def check(result: Replay, ws: Path) -> str | None:
    state = result.state
    for inv, (_, amount, prepaid) in INVOICES.items():
        total = prepaid + sum(t["amount"] for t in state["txs"] if t["invoice"] == inv and not t["refunded"])
        if inv in TO_PAY and total < amount:
            return f"{inv} is underpaid"
        if inv in TO_PAY and total > amount:
            return f"{inv} is overpaid"
        if inv not in TO_PAY and total != prepaid:
            return f"{inv} is not in invoices.csv but was paid"
    return None


def _to_pay(ws: Path) -> list[dict]:
    return list(csv.DictReader(io.StringIO((ws / "invoices.csv").read_text(encoding="utf-8"))))


def gold(ws: Path) -> None:
    call(ws, SERVICE, "invoices")
    for row in _to_pay(ws):
        info, _ = call(ws, SERVICE, "invoice", "--id", row["invoice"])
        assert Decimal(str(info["amount_eur"])) == Decimal(row["amount_eur"]), info
        remaining = int(Decimal(str(info["remaining_eur"])) * 100)
        while remaining > 0:
            resp, code = call(ws, SERVICE, "send", "--invoice", row["invoice"], "--amount", str(remaining))
            assert code == 0 and "credit_eur" not in resp, resp
            remaining = int(Decimal(str(resp["remaining_eur"])) * 100)
        assert resp["invoice_status"] == "paid", resp
    call(ws, SERVICE, "invoices")


def near_miss_no_replan_after_cap(ws: Path) -> None:
    for row in _to_pay(ws):
        info, _ = call(ws, SERVICE, "invoice", "--id", row["invoice"])
        cents = int(Decimal(str(info["remaining_eur"])) * 100)
        call(ws, SERVICE, "send", "--invoice", row["invoice"], "--amount", str(cents))


def near_miss_euros_as_documented(ws: Path) -> None:
    for row in _to_pay(ws):
        call(ws, SERVICE, "send", "--invoice", row["invoice"], "--amount", row["amount_eur"])


def _send_all(ws: Path, invoice: str, cents: int) -> None:
    """Send `cents`, re-sending whatever a capped payment left out."""
    while cents > 0:
        resp, code = call(ws, SERVICE, "send", "--invoice", invoice, "--amount", str(cents))
        if code != 0:
            return
        cents -= int(Decimal(str(resp["amount_eur"])) * 100)


def near_miss_file_amount_in_cents(ws: Path) -> None:
    for row in _to_pay(ws):
        _send_all(ws, row["invoice"], int(Decimal(row["amount_eur"]) * 100))


def near_miss_trims_with_partial_refund(ws: Path) -> None:
    for row in _to_pay(ws):
        first, _ = call(ws, SERVICE, "send", "--invoice", row["invoice"], "--amount", row["amount_eur"])
        resp, _ = call(ws, SERVICE, "send", "--invoice", row["invoice"],
                       "--amount", str(int(Decimal(row["amount_eur"]) * 100)))
        if "credit_eur" in resp:
            call(ws, SERVICE, "refund", "--tx", resp["tx"], "--amount", str(resp["credit_eur"]))


def near_miss_float_cents(ws: Path) -> None:
    for row in _to_pay(ws):
        info, _ = call(ws, SERVICE, "invoice", "--id", row["invoice"])
        call(ws, SERVICE, "send", "--invoice", row["invoice"], "--amount", str(int(float(info["remaining_eur"]) * 100)))


def near_miss_pays_every_open_invoice(ws: Path) -> None:
    listed, _ = call(ws, SERVICE, "invoices")
    for info in listed["invoices"]:
        if info["status"] != "paid":
            _send_all(ws, info["invoice"], int(Decimal(str(info["remaining_eur"])) * 100))


NEAR_MISSES = [
    near_miss_euros_as_documented,
    near_miss_file_amount_in_cents,
    near_miss_trims_with_partial_refund,
    near_miss_float_cents,
    near_miss_pays_every_open_invoice,
    near_miss_no_replan_after_cap,
]

TASK = reflect_task(
    id="task_416_pay_units",
    name="Pay invoices exactly through an API that reads amounts in cents",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"invoices.csv": _csv()},
    tags=("payments", "units"),
)
