"""Pay out exact net amounts through a bank API with hidden fees and a transfer cap.

Deviations from docs/bank.md (all visible in responses):
- Transfers are documented as free; each one actually costs 1.5% of the amount
  (half-up to the cent) plus 0.30 EUR, deducted from what the recipient gets.
  `send` and `quote` show `fee_eur`, `delivered_eur` and the fee schedule.
- A transfer debiting more than 1000.00 EUR is refused with the bare code TX-409;
  the documented `explain --code` says it is a per-transfer cap, so large payouts
  must be split, and every part pays the fixed 0.30 again.
Obvious recoveries are wrong: grossing up the total and splitting it loses 0.30 per
extra part, topping up by the shortfall is itself charged, and rounding the gross
up "to be safe" overpays by a cent that cannot be taken back.
- Mid-course surprise: once 5000.00 EUR has been debited in total, the fee drops to
  1.2% + 0.30 EUR. The crossing `send` carries a `notice`, and `fee_schedule`/`quote`
  change from then on, so gross amounts planned up front over-deliver irreversibly.
"""

from __future__ import annotations

import csv
import io
import math
import re
from decimal import Decimal
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

RECIPIENTS = {
    "R01": "Anouk Visser", "R02": "Bas Mulder", "R03": "Cor Timmer", "R04": "Dewi Hartman",
    "R05": "Eline Bos", "R06": "Fedde Kok", "R07": "Gijs Prins", "R08": "Hanna de Wit",
    "R09": "Ids Brouwer", "R10": "Joost Smit",
}
# net amounts in cents that each recipient must receive
PAYOUTS = {"R01": 24500, "R02": 183050, "R03": 61237, "R04": 295000, "R05": 8810,
           "R06": 98471, "R07": 47000, "R08": 120399, "R09": 12345}
LIMIT = 100000


def _fee(gross: int, permille: int = 15) -> int:
    return (gross * permille + 500) // 1000 + 30


def _gross_for(net: int, permille: int = 15) -> int:
    gross = max(net, 1)
    while gross - _fee(gross, permille) < net:
        gross += 1
    return gross


def _parts(net: int, permille: int = 15) -> list[int]:
    top = LIMIT - _fee(LIMIT, permille)
    parts = []
    while net > 0:
        parts.append(min(net, top))
        net -= parts[-1]
    return parts


BALANCE = sum(_gross_for(p) for net in PAYOUTS.values() for p in _parts(net)) + 4000


def _csv() -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["recipient", "name", "net_eur"])
    for rid, net in PAYOUTS.items():
        writer.writerow([rid, RECIPIENTS[rid], f"{net / 100:.2f}"])
    return buf.getvalue()


SOURCE = r'''
import re as _re
RECIPIENTS = __RECIPIENTS__
BALANCE = __BALANCE__
LIMIT = 100000
TIER = 500000
CODES = {
    "TX-409": "Single-transfer limit: one transfer may debit at most 1000.00 EUR. Split larger payments into several transfers.",
    "TX-212": "The amount does not cover the transfer charges.",
    "TX-402": "Insufficient funds on the account.",
}


def initial_state():
    return {"balance": BALANCE, "transfers": [], "permille": 15, "volume": 0}


def eur(cents):
    return round(cents / 100, 2)


def fee_note(state):
    pct = f"{state['permille'] // 10}.{state['permille'] % 10}"
    return (f"fee = {pct}% of the amount, rounded half-up to the cent, plus 0.30 EUR per transfer; "
            "deducted from the amount")


def fee(state, gross):
    return (gross * state["permille"] + 500) // 1000 + 30


def parse_amount(raw):
    raw = str(raw).strip()
    if not _re.fullmatch(r"\d+(\.\d{1,2})?", raw):
        return None
    whole, _, frac = raw.partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


def rejected(code):
    return {"status": "rejected", "code": code, "error": f"transfer rejected ({code})"}, 3


def preview(state, opts):
    cents = parse_amount(opts.get("amount"))
    if cents is None or cents <= 0:
        return None, ({"status": "error", "error": "--amount must be a positive amount in EUR, e.g. 125.50"}, 2)
    if cents > LIMIT:
        return None, rejected("TX-409")
    if cents - fee(state, cents) <= 0:
        return None, rejected("TX-212")
    return cents, None


def handle(state, words, opts, io):
    cmd = words[0] if words else ""
    if cmd == "balance":
        return {"status": "ok", "balance_eur": eur(state["balance"])}, 0
    if cmd == "recipients":
        got = {}
        for t in state["transfers"]:
            got[t["to"]] = got.get(t["to"], 0) + t["delivered"]
        return {"status": "ok", "recipients": [{"id": r, "name": n, "received_eur": eur(got.get(r, 0))}
                                               for r, n in RECIPIENTS.items()]}, 0
    if cmd == "transfers":
        return {"status": "ok", "transfers": [{"tx": t["tx"], "to": t["to"], "debited_eur": eur(t["amount"]),
                                               "fee_eur": eur(t["fee"]), "delivered_eur": eur(t["delivered"])}
                                              for t in state["transfers"]]}, 0
    if cmd == "explain":
        code = str(opts.get("code"))
        if code not in CODES:
            return {"status": "error", "error": f"unknown code {code}"}, 4
        return {"status": "ok", "code": code, "meaning": CODES[code]}, 0
    if cmd == "quote":
        cents, err = preview(state, opts)
        if err:
            return err
        return {"status": "ok", "amount_eur": eur(cents), "debited_eur": eur(cents),
                "fee_eur": eur(fee(state, cents)), "delivered_eur": eur(cents - fee(state, cents)),
                "fee_schedule": fee_note(state)}, 0
    if cmd == "send":
        to = opts.get("to")
        if to not in RECIPIENTS:
            return {"status": "error", "error": "unknown recipient"}, 4
        cents, err = preview(state, opts)
        if err:
            return err
        if cents > state["balance"]:
            return rejected("TX-402")
        state["balance"] -= cents
        tx = f"T{len(state['transfers']) + 1:04d}"
        t = {"tx": tx, "to": to, "amount": cents, "fee": fee(state, cents), "delivered": cents - fee(state, cents)}
        state["transfers"].append(t)
        resp = {"status": "ok", "tx": tx, "to": to, "debited_eur": eur(cents), "fee_eur": eur(t["fee"]),
                "delivered_eur": eur(t["delivered"]), "fee_schedule": fee_note(state),
                "balance_eur": eur(state["balance"])}
        state["volume"] += cents
        if state["volume"] >= TIER and state["permille"] == 15:
            state["permille"] = 12
            resp["notice"] = ("volume tier reached (5000.00 EUR sent): from the next transfer on the fee is "
                              + fee_note(state))
        return resp, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/bank.md"}, 2
'''.replace("__RECIPIENTS__", repr(RECIPIENTS)).replace("__BALANCE__", repr(BALANCE))

SERVICE = Service(tool="bank", title="the Havenbank payouts API", source=SOURCE, key=b"reflect-06-bank-5ad7")

DOCS = """
# bank — выплаты получателям

Клиент: `python3 tools/bank <команда> [--опция значение ...]`. Каждая команда печатает JSON.
Суммы — в евро, не более двух знаков после точки (`125.50`).

| Команда | Что делает |
|---|---|
| `balance` | Остаток на счёте. |
| `recipients` | Получатели и сколько каждый уже получил (`received_eur`). |
| `quote --amount EUR` | Предварительный расчёт перевода: сколько будет списано со счёта. |
| `send --to ID --amount EUR` | Перевод получателю. Возвращает номер транзакции `tx`. |
| `transfers` | Все проведённые переводы. |
| `explain --code CODE` | Расшифровка кода ошибки. |

Переводы бесплатны: получатель получает ровно `--amount`, столько же списывается со
счёта. Проведённый перевод отменить нельзя. Если перевод не проходит, ответ содержит
`"status": "rejected"` и код ошибки `code`; его смысл выдаёт `explain`.
"""

PROMPT = """
Через API `bank` выплатите получателям из файла `payouts.csv` (колонки `recipient`,
`name`, `net_eur`). Каждый получатель должен в итоге получить на руки ровно `net_eur` —
не меньше и не больше, с точностью до цента (сколько получено, видно в `recipients`);
переводить можно несколькими частями. Другим получателям ничего не переводите.
Проведённые переводы отменить нельзя, так что лишнее вернуть не получится.
""" + PROMPT_RULES.format(tool="bank")


def check(result: Replay, ws: Path) -> str | None:
    got: dict[str, int] = {}
    for t in result.state["transfers"]:
        got[t["to"]] = got.get(t["to"], 0) + t["delivered"]
    for rid in RECIPIENTS:
        want = PAYOUTS.get(rid, 0)
        if got.get(rid, 0) < want:
            return f"{rid} received less than the payout"
        if got.get(rid, 0) > want:
            return f"{rid} received more than the payout" if want else f"{rid} is not in payouts.csv but was paid"
    return None


def _rows(ws: Path) -> list[tuple[str, int]]:
    reader = csv.DictReader(io.StringIO((ws / "payouts.csv").read_text(encoding="utf-8")))
    return [(r["recipient"], int(Decimal(r["net_eur"]) * 100)) for r in reader]


def _eur(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _cents(value: float) -> int:
    return int(Decimal(str(value)) * 100)


def gold(ws: Path) -> None:
    call(ws, SERVICE, "balance")
    probe, _ = call(ws, SERVICE, "quote", "--amount", "100.00")
    assert "1.5%" in probe["fee_schedule"] and _cents(probe["delivered_eur"]) == 10000 - _fee(10000), probe
    refused, code = call(ws, SERVICE, "quote", "--amount", "1000.01")
    assert code == 3, refused
    meaning, _ = call(ws, SERVICE, "explain", "--code", refused["code"])
    assert "1000.00" in meaning["meaning"], meaning
    permille = _permille(probe["fee_schedule"])
    for rid, net in _rows(ws):
        while net > 0:
            part = _parts(net, permille)[0]
            gross = _gross_for(part, permille)
            quote, _ = call(ws, SERVICE, "quote", "--amount", _eur(gross))
            assert _cents(quote["delivered_eur"]) == part, quote
            resp, code = call(ws, SERVICE, "send", "--to", rid, "--amount", _eur(gross))
            assert code == 0 and _cents(resp["delivered_eur"]) == part, resp
            net -= part
            if "notice" in resp:
                permille = _permille(resp["notice"])
    call(ws, SERVICE, "recipients")


def _permille(schedule: str) -> int:
    found = re.search(r"(\d+)\.(\d)% of the amount", schedule)
    assert found, schedule
    return int(found.group(1)) * 10 + int(found.group(2))


def near_miss_no_replan_after_tier(ws: Path) -> None:
    for rid, net in _rows(ws):
        for part in _parts(net):
            call(ws, SERVICE, "send", "--to", rid, "--amount", _eur(_gross_for(part)))


def near_miss_free_as_documented(ws: Path) -> None:
    for rid, net in _rows(ws):
        call(ws, SERVICE, "send", "--to", rid, "--amount", _eur(net))


def near_miss_splits_net_ignoring_fee(ws: Path) -> None:
    for rid, net in _rows(ws):
        while net > 0:
            part = min(net, LIMIT)
            call(ws, SERVICE, "send", "--to", rid, "--amount", _eur(part))
            net -= part


def near_miss_grosses_total_then_splits(ws: Path) -> None:
    for rid, net in _rows(ws):
        gross = _gross_for(net)
        while gross > 0:
            part = min(gross, LIMIT)
            call(ws, SERVICE, "send", "--to", rid, "--amount", _eur(part))
            gross -= part


def near_miss_rounds_gross_up(ws: Path) -> None:
    for rid, net in _rows(ws):
        for part in _parts(net):
            call(ws, SERVICE, "send", "--to", rid, "--amount", _eur(math.ceil((part + 30) / 0.985)))


def near_miss_tops_up_shortfall(ws: Path) -> None:
    for rid, net in _rows(ws):
        for part in _parts(net):
            short = part
            for _ in range(3):
                resp, code = call(ws, SERVICE, "send", "--to", rid, "--amount", _eur(short))
                if code != 0:
                    break
                short -= _cents(resp["delivered_eur"])


NEAR_MISSES = [
    near_miss_free_as_documented,
    near_miss_splits_net_ignoring_fee,
    near_miss_grosses_total_then_splits,
    near_miss_rounds_gross_up,
    near_miss_tops_up_shortfall,
    near_miss_no_replan_after_tier,
]

TASK = reflect_task(
    id="reflect_06_transfer_fees",
    name="Pay out exact net amounts through a bank with hidden fees and a per-transfer cap",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"payouts.csv": _csv()},
    tags=("payments", "fees"),
)
