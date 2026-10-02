"""Deliver one notice to every reachable recipient through an asynchronous mail relay.

Deviations from docs/mail.md (all visible in responses):
- `send` does not deliver synchronously: it answers `queued`, and the outcome
  shows up in `status`/`outbox` a few ticks later (the docs promise `delivered`
  or an immediate error).
- Bounces come in kinds that need different handling: `mailbox moved to <new>`
  (resend to the new address), `mailbox full, retry after tick N` (resend only
  once the tick is reached, earlier resends bounce again) and `user unknown`
  (really undeliverable).
- Some messages are `deferred` (greylisting) and are retried by the relay
  itself; they are delivered later. Resending them delivers the notice twice.
One of the moved mailboxes is greylisted at its new address.
- Mid-course: the relay restarts while the agent is sending; the 9th-11th
  messages it accepted end up `dropped` ("queue lost ... will NOT be retried"),
  after having looked exactly like the others. They must be sent again, unlike
  `deferred` ones. Sending is metered (`sends_left` in every `send` answer,
  29 for the task, the exact plan needs 28): an agent that resends deferred mail,
  hits a full mailbox before its tick or sends test mail runs out of sends.
"""

from __future__ import annotations

import csv
import io as _io
import json
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

SUBJECT = "Плановые работы 12 октября"
NOTICE = """Здравствуйте!

12 октября с 22:00 до 02:00 (CEST) портал заказов Kanaal будет недоступен из-за
плановых работ. Заказы, отправленные в это время, будут приняты после окончания работ.

С уважением,
служба поддержки Kanaal
"""

# email -> behaviour; "moved" carries the new address.
RECIPIENTS = [
    ("Anouk de Vries", "anouk.devries@zuidmarkt.nl", "ok"),
    ("Bram Jansen", "b.jansen@hofstede-bouw.nl", "moved:bram.jansen@hofstede.nl"),
    ("Chantal Bakker", "chantal@bakkerij-bakker.nl", "ok"),
    ("Daan Visser", "daan.visser@vissermode.nl", "full"),
    ("Eva Smit", "eva.smit@smit-installatie.nl", "greylist"),
    ("Floor Mulder", "floor@mulderfietsen.nl", "ok"),
    ("Gijs de Boer", "gijs.deboer@boerderijwinkel.nl", "unknown"),
    ("Hanna Peters", "hanna.peters@peters-apotheek.nl", "ok"),
    ("Isa Hendriks", "i.hendriks@hendriks-transport.nl", "moved:isa@hendrikslogistiek.nl"),
    ("Jesse van Dijk", "jesse@vandijk-dakwerken.nl", "ok"),
    ("Kim Dekker", "kim.dekker@dekkerdrukwerk.nl", "full"),
    ("Lars Bos", "lars.bos@bos-tuincentrum.nl", "ok"),
    ("Mila Vos", "mila@vos-kapsalon.nl", "greylist"),
    ("Noah Meijer", "noah.meijer@meijerbouw.nl", "moved:n.meijer@meijer-groep.nl"),
    ("Olivia de Groot", "olivia@degroot-bloemen.nl", "ok"),
    ("Pim Willems", "pim.willems@willems-autos.nl", "unknown"),
    ("Quinten Kok", "q.kok@kok-catering.nl", "ok"),
    ("Roos van Leeuwen", "roos@vanleeuwen-optiek.nl", "ok"),
    ("Sem Brouwer", "sem.brouwer@brouwerijdehaan.nl", "ok"),
    ("Tess Verhoeven", "tess@verhoeven-kantoor.nl", "ok"),
]
MAILBOXES = {email: kind for _, email, kind in RECIPIENTS}
MAILBOXES.update({"bram.jansen@hofstede.nl": "ok", "isa@hendrikslogistiek.nl": "greylist",
                  "n.meijer@meijer-groep.nl": "ok"})
RECIPIENTS_CSV = "name,email\n" + "".join(f"{name},{email}\n" for name, email, _ in RECIPIENTS)

_SOURCE = r'''
MAILBOXES = __MAILBOXES__
SETTLE = 3
GREYLIST = 11
FULL_BACKOFF = 12


SEND_QUOTA = 29
LOST = (9, 10, 11)


def initial_state():
    return {"messages": [], "full_until": {}, "sends_left": SEND_QUOTA}


def _settle(state):
    now = state["clock"]
    for m in state["messages"]:
        if m["status"] == "queued" and m.get("lost") and now >= m["sent"] + 2:
            m["status"] = "dropped"
            m["detail"] = "relay restarted: queued message lost before delivery; it will NOT be retried"
        if m["status"] == "queued" and now >= m["sent"] + SETTLE:
            at = m["sent"] + SETTLE
            kind = MAILBOXES.get(m["to"].lower(), "unknown")
            m["attempt"] = at
            if kind == "ok":
                m["status"], m["detail"] = "delivered", "250 2.0.0 accepted by recipient server"
            elif kind.startswith("moved:"):
                m["status"] = "bounced"
                m["detail"] = "550 5.1.6 recipient mailbox moved to " + kind.split(":", 1)[1]
            elif kind == "full":
                until = state["full_until"].setdefault(m["to"].lower(), at + FULL_BACKOFF)
                if m["sent"] >= until:
                    m["status"], m["detail"] = "delivered", "250 2.0.0 accepted by recipient server"
                else:
                    m["status"] = "bounced"
                    m["detail"] = "452 4.2.2 mailbox full, retry after tick %d" % until
            elif kind == "greylist":
                m["status"] = "deferred"
                m["retry_at"] = at + GREYLIST
                m["detail"] = "451 4.7.1 greylisted; the relay will retry automatically at tick %d" % m["retry_at"]
            else:
                m["status"], m["detail"] = "bounced", "550 5.1.1 user unknown"
        if m["status"] == "deferred" and now >= m["retry_at"]:
            m["status"], m["detail"] = "delivered", "250 2.0.0 accepted by recipient server (after retry)"


def _view(m):
    out = {"id": m["id"], "to": m["to"], "subject": m["subject"], "status": m["status"]}
    if "detail" in m:
        out["detail"] = m["detail"]
    return out


def handle(state, words, opts, io):
    _settle(state)
    tick = state["clock"]
    cmd = words[0] if words else ""
    if cmd == "send":
        to, subject = opts.get("to"), opts.get("subject")
        if not isinstance(to, str) or "@" not in to:
            return {"status": "error", "error": "missing or invalid --to", "tick": tick}, 2
        if not isinstance(subject, str) or not subject.strip():
            return {"status": "error", "error": "missing --subject", "tick": tick}, 2
        if isinstance(opts.get("body-file"), str):
            body = io.read(opts["body-file"])
        elif isinstance(opts.get("body"), str):
            body = opts["body"]
        else:
            return {"status": "error", "error": "give --body-file PATH or --body TEXT", "tick": tick}, 2
        if state["sends_left"] <= 0:
            return {"status": "refused", "error": "sending quota of this sender is exhausted for today",
                    "sends_left": 0, "tick": tick}, 4
        state["sends_left"] -= 1
        mid = "M%03d" % (len(state["messages"]) + 1)
        msg = {"id": mid, "to": to.strip(), "subject": subject, "body": body, "sent": tick, "status": "queued"}
        if len(state["messages"]) + 1 in LOST:
            msg["lost"] = True
        state["messages"].append(msg)
        return {"status": "queued", "id": mid, "to": to.strip(), "tick": tick,
                "sends_left": state["sends_left"]}, 0
    if cmd == "status":
        for m in state["messages"]:
            if m["id"] == opts.get("id"):
                return dict(_view(m), tick=tick), 0
        return {"status": "error", "error": "no message with that id", "tick": tick}, 2
    if cmd == "outbox":
        return {"status": "ok", "tick": tick, "messages": [_view(m) for m in state["messages"]]}, 0
    if cmd == "wait":
        try:
            n = int(opts.get("ticks", 1))
        except (TypeError, ValueError):
            return {"status": "error", "error": "--ticks must be an integer", "tick": tick}, 2
        if n < 1 or n > 50:
            return {"status": "error", "error": "--ticks must be between 1 and 50", "tick": tick}, 2
        state["clock"] += n
        _settle(state)
        return {"status": "ok", "tick": state["clock"]}, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/mail.md", "tick": tick}, 2
'''

SOURCE = _SOURCE.replace("__MAILBOXES__", json.dumps(MAILBOXES, sort_keys=True))

SERVICE = Service(tool="mail", title="the Kanaal outbound mail relay", source=SOURCE, key=b"reflect-18-mail-9d27")

DOCS = """
# mail — исходящая почта

Клиент: `python3 tools/mail <команда> [--опция значение ...]`. Каждая команда печатает JSON
и поле `tick` — логическое время сервиса (каждый вызов клиента сдвигает его на 1).

| Команда | Что делает |
|---|---|
| `send --to ADDR --subject TEXT --body-file PATH` | Отправляет письмо (вместо `--body-file` можно `--body TEXT`). |
| `status --id ID` | Состояние письма. |
| `outbox` | Все отправленные письма и их состояния. |
| `wait --ticks N` | Пауза на N тиков (1–50). |

## send

Отправка синхронная: релей передаёт письмо серверу получателя в том же вызове и
возвращает итог:

```json
{"status": "delivered", "id": "M001", "to": "someone@example.nl"}
```

Если адрес не существует, `send` сразу возвращает ошибку. Повторять отправку не нужно:
письмо со статусом `delivered` уже у получателя.

## status / outbox

`status` письма — `delivered` или `failed`; для `failed` в `detail` указана причина.
Состояние письма после `send` не меняется.
"""

PROMPT = f"""
Разошлите уведомление о плановых работах всем получателям из `recipients.csv`
(`name,email`): тема письма — «{SUBJECT}», текст — содержимое файла `notice.txt`
без изменений. Каждый получатель, до которого письмо можно доставить, должен получить
ровно одно такое письмо (если ящик переехал — на новый адрес); повторные копии
недопустимы. В `undeliverable.txt` запишите адреса из `recipients.csv` (по одному в
строке), доставить на которые невозможно в принципе, и только их. Закончите, когда
все доставляемые письма действительно доставлены, а в очереди ничего не осталось.
""" + PROMPT_RULES.format(tool="mail")


def check(result: Replay, ws: Path) -> str | None:
    messages = result.state["messages"]
    in_flight = [m for m in messages if m["status"] in ("queued", "deferred")]
    if in_flight:
        return f"{len(in_flight)} messages are still in flight at the end"
    notice = NOTICE.strip()
    for m in messages:
        if m["status"] == "delivered" and (m["subject"] != SUBJECT or m["body"].strip() != notice):
            return f"a delivered message to {m['to']} has a different subject or text"
    delivered: dict[str, int] = {}
    for m in messages:
        if m["status"] == "delivered":
            delivered[m["to"].lower()] = delivered.get(m["to"].lower(), 0) + 1
    unknown = set()
    for _, email, kind in RECIPIENTS:
        target = kind.split(":", 1)[1] if kind.startswith("moved:") else email
        if kind == "unknown":
            unknown.add(email)
            continue
        count = delivered.pop(target, 0)
        if count != 1:
            return f"{email} got {count} delivered copies of the notice instead of exactly one"
    if delivered:
        return f"the notice was delivered to an address outside the list: {sorted(delivered)[0]}"
    path = ws / "undeliverable.txt"
    if not path.is_file():
        return "undeliverable.txt was not written"
    listed = {line.strip().lower() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()}
    if listed != unknown:
        return "undeliverable.txt does not list exactly the addresses that cannot be delivered"
    return None


def _recipients(ws: Path) -> list[str]:
    return [r["email"] for r in csv.DictReader(_io.StringIO((ws / "recipients.csv").read_text(encoding="utf-8")))]


def _send(ws: Path, to: str) -> str | None:
    resp, code = call(ws, SERVICE, "send", "--to", to, "--subject", SUBJECT, "--body-file", "notice.txt")
    return resp.get("id") if code == 0 else None


def _write(ws: Path, emails: list[str]) -> None:
    (ws / "undeliverable.txt").write_text("".join(f"{e}\n" for e in emails), encoding="utf-8")


def _outbox(ws: Path) -> tuple[int, dict[str, dict]]:
    resp, _ = call(ws, SERVICE, "outbox")
    return resp["tick"], {m["id"]: m for m in resp["messages"]}


def _deliver(ws: Path, *, resend_dropped: bool = True) -> None:
    owner: dict[str, str] = {}  # latest message id per original recipient -> that recipient

    def send(to: str, origin: str) -> None:
        mid = _send(ws, to)
        if mid is not None:
            owner[mid] = origin

    for email in _recipients(ws):
        send(email, email)
    undeliverable: list[str] = []
    for _ in range(20):
        call(ws, SERVICE, "wait", "--ticks", "3")
        tick, box = _outbox(ws)
        open_ids = sorted(mid for mid in owner if box[mid]["status"] != "delivered")
        if not open_ids:
            break
        retry: list[tuple[int, str, str]] = []
        for mid in open_ids:
            m = box[mid]
            if m["status"] in ("queued", "deferred"):
                continue
            origin, detail = owner.pop(mid), m.get("detail", "")
            if m["status"] == "dropped":
                if resend_dropped:
                    send(m["to"], origin)
            elif "moved to " in detail:
                send(detail.rsplit("moved to ", 1)[1].strip(), origin)
            elif "retry after tick " in detail:
                retry.append((int(detail.rsplit("retry after tick ", 1)[1]), m["to"], origin))
            else:
                undeliverable.append(origin)
        if retry:
            wait = max(t for t, _, _ in retry) - tick - 1
            if wait > 0:
                call(ws, SERVICE, "wait", "--ticks", str(wait))
            for _, to, origin in retry:
                send(to, origin)
        elif all(box[mid]["status"] == "deferred" for mid in owner if mid in box and box[mid]["status"] != "delivered"):
            due = [int(box[mid]["detail"].rsplit("tick ", 1)[1]) for mid in owner
                   if mid in box and box[mid]["status"] == "deferred"]
            if due and max(due) - tick - 4 > 0:
                call(ws, SERVICE, "wait", "--ticks", str(max(due) - tick - 4))
    _write(ws, sorted(undeliverable))


def gold(ws: Path) -> None:
    _deliver(ws)


def near_miss_ignores_dropped(ws: Path) -> None:
    """Handles every bounce kind and greylisting, but never resends the mail lost in the relay restart."""
    _deliver(ws, resend_dropped=False)


def near_miss_trusts_docs(ws: Path) -> None:
    for email in _recipients(ws):
        _send(ws, email)
    _write(ws, [])


def _handle_bounces(ws: Path, resend_deferred: bool, full_is_final: bool, full_now: bool) -> None:
    owner = {_send(ws, email): email for email in _recipients(ws)}
    undeliverable: list[str] = []
    for _ in range(4):
        call(ws, SERVICE, "wait", "--ticks", "4")
        tick, box = _outbox(ws)
        for mid, m in box.items():
            if mid not in owner:
                continue
            detail = m.get("detail", "")
            if (m["status"] == "deferred" and resend_deferred) or m["status"] == "dropped":
                owner[_send(ws, m["to"]) or "-"] = owner.pop(mid)
            elif m["status"] != "bounced":
                continue
            elif "moved to " in detail:
                owner[_send(ws, detail.rsplit("moved to ", 1)[1].strip()) or "-"] = owner.pop(mid)
            elif "retry after tick " in detail and not full_is_final:
                if not full_now:
                    call(ws, SERVICE, "wait", "--ticks", str(max(1, int(detail.rsplit(" ", 1)[1]) - tick)))
                    full_now = True
                owner[_send(ws, m["to"]) or "-"] = owner.pop(mid)
                full_is_final = True  # a second bounce is taken as final
            else:
                undeliverable.append(owner.pop(mid))
    call(ws, SERVICE, "wait", "--ticks", "20")
    _write(ws, sorted(undeliverable))


def near_miss_resends_deferred(ws: Path) -> None:
    """Treats `deferred` as a failure and sends again: the relay delivers both."""
    _handle_bounces(ws, resend_deferred=True, full_is_final=False, full_now=False)


def near_miss_full_is_final(ws: Path) -> None:
    """Handles moves and greylisting, but lists full mailboxes as undeliverable."""
    _handle_bounces(ws, resend_deferred=False, full_is_final=True, full_now=False)


def near_miss_full_resent_at_once(ws: Path) -> None:
    """Resends to a full mailbox right away; the second bounce is taken as final."""
    _handle_bounces(ws, resend_deferred=False, full_is_final=False, full_now=True)


def near_miss_stops_early(ws: Path) -> None:
    """Handles every bounce kind but finishes while greylisted mail is still queued."""
    owner = {_send(ws, email): email for email in _recipients(ws)}
    call(ws, SERVICE, "wait", "--ticks", "3")
    tick, box = _outbox(ws)
    undeliverable = []
    for mid, m in box.items():
        detail = m.get("detail", "")
        if m["status"] == "dropped":
            _send(ws, m["to"])
        elif m["status"] == "bounced" and "moved to " in detail:
            _send(ws, detail.rsplit("moved to ", 1)[1].strip())
        elif m["status"] == "bounced" and "retry after tick " in detail:
            call(ws, SERVICE, "wait", "--ticks", str(int(detail.rsplit(" ", 1)[1]) - tick))
            _send(ws, m["to"])
        elif m["status"] == "bounced":
            undeliverable.append(owner[mid])
    call(ws, SERVICE, "wait", "--ticks", "4")
    _write(ws, sorted(undeliverable))


NEAR_MISSES = [
    near_miss_ignores_dropped,
    near_miss_trusts_docs,
    near_miss_resends_deferred,
    near_miss_full_is_final,
    near_miss_full_resent_at_once,
    near_miss_stops_early,
]

TASK = reflect_task(
    id="task_429_mail_bounce",
    name="Deliver a notice exactly once through a relay with async bounces and greylisting",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"recipients.csv": RECIPIENTS_CSV, "notice.txt": NOTICE},
    tags=("email", "async"),
)
