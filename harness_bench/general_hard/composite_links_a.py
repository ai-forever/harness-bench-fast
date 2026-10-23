"""Cross-domain obligations for composite tasks 512--518.

Hooks run only on the trusted host. Witnesses below use the same public clients
as a solver; they never inspect a trusted session or a private state file.
"""

from __future__ import annotations

import copy
import hashlib

from . import interactive_data as data
from . import interactive_systems as systems
from . import interactive_workflows as workflows


def _event(state, name, **values):
    coupling = state["coupling"]
    coupling["phase"] += 1
    coupling["events"].append(
        {"phase": coupling["phase"], "event": name, **copy.deepcopy(values)}
    )


def _complete(state):
    return (
        None
        if state["coupling"]["phase"] == 2 and len(state["coupling"]["events"]) == 2
        else "Both documented cross-domain corrections must be reconciled"
    )


def _phase(state):
    return state["coupling"]["phase"]


def _invoice_total(state, identity):
    return sum(
        payment["amount"]
        for payment in state["payments"].values()
        if payment["invoice"] == identity and not payment["reversed"]
    )


def _ask(ws, *args):
    return systems.send(ws, *args)


def _pair_status(ws):
    return _ask(ws, "pair", "status")


def _incident_repair(ws):
    """Fresh receipt names are essential after an incident source correction."""
    head = data._ask(ws, "head")
    live = data._latest(data._scan(ws, ("issues", "list"), revision=head["revision"]))
    reports = {}
    for team in head["teams"]:
        rows = [row for row in live.values() if row["team"] == team and row["state"] == "open"]
        reports[team] = {
            "revision": head["revision"],
            "open": sorted(row["id"] for row in rows),
            "urgent": sorted(row["id"] for row in rows if row["priority"] == "urgent"),
        }
    data._put(ws, ("reports", "stage"), reports)
    for team in reports:
        data._ask(
            ws, "reports", "publish", team=team, revision=head["revision"],
            key=f"composite/{head['revision']}/{team}",
        )
    alerts = data._ask(ws, "alerts", "status")
    data._put(
        ws, ("alerts", "replace"),
        {key: {"team": row["team"], "issue": key} for key, row in live.items()
         if row["state"] == "open" and row["priority"] == "urgent"},
        revision=head["revision"], generation=alerts["generation"],
    )


def _stream_repair(ws):
    head = data._ask(ws, "head")
    engine = data._ask(ws, "engine", "status")
    rows = data._scan(ws, ("feed", "list"), revision=head["revision"])
    data._put(ws, ("engine", "apply"),
              [row for row in rows if row["offset"] > engine["engine"]["offset"]],
              epoch=engine["epoch"])
    data._ask(ws, "engine", "checkpoint")
    current = data._ask(ws, "engine", "status")
    counts = {}
    for row in data._latest(current["engine"]["rows"]).values():
        start = row["ts"] // 10 * 10
        if start + 10 <= current["watermark"]:
            counts[str(start)] = counts.get(str(start), 0) + 1
    sink = data._ask(ws, "sink", "status")
    old = sink["task_counts"]
    data._put(
        ws, ("sink", "adjust"),
        {key: counts.get(key, 0) - old.get(key, 0) for key in counts.keys() | old.keys()},
        generation=sink["generation"], key=f"composite/{head['revision']}", scope="task",
    )


def _after_512(state, lane, words, opts, response, code):
    left, right = state["left"], state["right"]
    if _phase(state) == 0 and data.incident_v8_check(left) is None:
        urgent = sorted(key for key, row in data._latest(left["rows"]).items()
                        if row["state"] == "open" and row["priority"] == "urgent")
        row = right["items"]["event-2"]
        row.update(wanted=int(not urgent), units=1 + len(urgent) % 2,
                   revision=row["revision"] + 1)
        right["finished"] = False
        _event(state, "urgent-delivery-policy", source_revision=left["revision"],
               urgent=urgent, target="event-2", wanted=row["wanted"], units=row["units"],
               target_revision=row["revision"])
    elif _phase(state) == 1 and _check_512_right(right) is None:
        effects = {key: row["effect"] for key, row in right["items"].items()}
        team = left["teams"][sum(effects.values()) % len(left["teams"])]
        row = {"id": "delivery-reconciliation", "rev": 1, "team": team,
               "state": "open", "priority": "urgent"}
        left["rows"].append(row)
        left["revision"] = "incidents-delivery-512"
        left["staged"].clear()
        _event(state, "delivery-reconciliation-incident", effects=effects,
               row=row, target_revision=left["revision"],
               receipts=sorted(key for key, job in right["jobs"].items()
                               if job["phase"] == "committed" and key not in right["reversed"]))


def _check_512_right(state):
    failure = systems._check(state)
    if failure:
        return failure
    if any(row["effect"] != row["wanted"] for row in state["items"].values()):
        return "Delivery effects must match the current incident-derived policy"
    if state["holds"] or any(job["phase"] in ("queued", "draining")
                             for job in state["jobs"].values()):
        return "Delivery jobs or escrow remain outstanding"
    if any(row["wanted"] and state["parents"][key]
           and state["items"][state["parents"][key]]["effect"] != 1
           for key, row in state["items"].items()):
        return "Current delivery dependency is not live"
    return None


def _check_512(state):
    if failure := _complete(state):
        return failure
    first, second = state["coupling"]["events"]
    row = state["right"]["items"]["event-2"]
    if row["wanted"] != first["wanted"] or row["units"] != first["units"]:
        return "Delivery policy binding differs"
    if data._latest(state["left"]["rows"]).get(second["row"]["id"]) != second["row"]:
        return "Delivery incident was not retained"
    return None


def _gold_512(ws):
    data.gold_incident_v8(ws / "left")
    systems.retry_gold_v8(ws / "right")
    _incident_repair(ws / "left")


def _after_513(state, lane, words, opts, response, code):
    left, right = state["left"], state["right"]
    if (_phase(state) == 0 and data.stream_v8_check(left) is None
            and "e7" not in right["applied"] and not right["acked"]):
        counts = dict(left["sink"])
        count = sum(counts.values())
        event = next(row for row in right["events"] if row["id"] == "e7")
        event["accounts"] = [f"a{count % 6}", f"a{(count + 2) % 6}"]
        _event(state, "window-dependent-final-event", source_revision=left["revision"],
               counts=counts, count=count, target="e7", accounts=event["accounts"])
    elif _phase(state) == 1 and _check_513_right(right) is None:
        accounts = next(row["accounts"] for row in right["events"] if row["id"] == "e7")
        timestamp = 10 + sum(int(account[1:]) for account in accounts)
        row = {"kind": "row", "id": "causal-ack-513", "rev": 1,
               "partition": "a", "ts": timestamp, "offset": len(left["feed"]) + 1}
        left["feed"].append(row)
        left["revision"] = "feed-causal-513"
        _event(state, "causal-checkpoint-stream-row", accounts=accounts,
               acknowledged=right["acked"], row=row, target_revision=left["revision"])


def _check_513_right(state):
    if failure := systems._check(state):
        return failure
    if (set(state["applied"]) != {f"e{i}" for i in range(8)}
            or len(state["applied"]) != 8 or state["acked"] != 8
            or any(state["resources"].values()) or not state["producer_done"]):
        return "The current causal feed must be completely applied and acknowledged"
    expected = {f"a{i}": [] for i in range(8)}
    for event in state["events"]:
        for account in event["accounts"]:
            expected[account].append(event["id"])
    # Task 490's original fixture is variant zero; the foreign writer owns a1.
    expected["a1"].append("foreign-review")
    if any(sorted(row["labels"]) != sorted(expected[row["account"]]) or row["archived"]
           for row in state["rows"].values()):
        return "Causal labels must match the current feed, including the foreign review"
    return None


def _check_513(state):
    if failure := _complete(state):
        return failure
    first, second = state["coupling"]["events"]
    event = next(row for row in state["right"]["events"] if row["id"] == "e7")
    if event["accounts"] != first["accounts"] or second["row"] not in state["left"]["feed"]:
        return "Window/feed bindings differ"
    return None


def _gold_513(ws):
    data.gold_stream_v8(ws / "left")
    systems.predicate_gold_v8(ws / "right")
    _stream_repair(ws / "left")


def _after_514(state, lane, words, opts, response, code):
    left, right = state["left"], state["right"]
    path = "config/app.txt"
    if _phase(state) == 0 and data.session_v8_check(left) is None:
        reports = copy.deepcopy(left["active"])
        elapsed = sum(row["elapsed"] for report in reports.values() for row in report["rows"])
        late = sum(row["late"] for report in reports.values() for row in report["rows"])
        desired = f"closeout-seconds={elapsed};late={late}\n"
        right["files"][path]["text"] += "closeout-seconds=pending;late=pending\n"
        right["files"][path]["revision"] += 1
        right["expected"][path] += desired
        right["verified"].pop(path, None)
        _event(state, "closeout-configuration-summary", source_revision=left["revision"],
               clock_revision=left["clock_revision"], elapsed=elapsed, late=late,
               path=path, desired=desired)
    elif _phase(state) == 1 and workflows._check(right) is None:
        revision = right["files"][path]["revision"]
        old = data._latest(left["events"])["session-1"]
        updated = {**old, "rev": old["rev"] + 1, "end": old["end"] + 1 + revision % 3}
        left["events"].append(updated)
        left["revision"] = "sessions-configuration-514"
        note = left["notes"][updated["batch"]]
        note["revision"] += 1
        note["text"] += f"; config/app.txt read-back revision {revision}"
        _event(state, "configuration-closeout-adjustment", path=path,
               verified_revision=revision, row=updated, annotation=note,
               target_revision=left["revision"])


def _check_514(state):
    if failure := _complete(state):
        return failure
    first, second = state["coupling"]["events"]
    if first["desired"] not in state["right"]["files"][first["path"]]["text"]:
        return "Closeout summary must be retained in the configuration"
    if data._latest(state["left"]["events"])["session-1"] != second["row"]:
        return "Configuration-dependent session correction differs"
    return None


def _gold_514(ws):
    data.gold_session_v8(ws / "left")
    workflows._gold_edits(ws / "right")
    event = _pair_status(ws)["coupling"]["events"][0]
    path = event["path"]
    row = workflows._call(ws / "right", "read", "--path", path)
    result = workflows._send(ws / "right", "restore", {
        "files": {path: row["text"].replace("closeout-seconds=pending;late=pending\n",
                                             event["desired"], 1)},
        "revisions": {path: row["revision"]},
    })
    assert result["status"] == "restored", result
    workflows._call(ws / "right", "verify", "--path", path)
    data.gold_session_v8(ws / "left")


def _after_515(state, lane, words, opts, response, code):
    left, right = state["left"], state["right"]
    if (_phase(state) == 0 and data.capacity_v8_check(left) is None
            and right["moved_event"]):
        slot = next(iter(left["committed"]["bundle-2"].values()))
        target = "b" if slot % 2 == 0 else "c"
        traffic = 3 + slot
        right["traffic"]["blue"] = traffic
        right["capacity"].update(b=6, c=6)
        right["capacity"][target] = traffic
        right["revision"] = "health-bookings-515"
        right["candidate"] = right["receipt"] = None
        _event(state, "bookings-live-routing", slots=copy.deepcopy(left["committed"]),
               source_revision=left["revision"], blue_load=traffic, blue_target=target,
               target_revision=right["revision"])
    elif (_phase(state) == 1 and data.relief_v8_check(right) is None
          and right["job"].get("revision") == "health-bookings-515"):
        target = right["observed"]["blue"]
        slot = 5 if target == "b" else 4
        bundle = next(row for row in left["bundles"] if row["id"] == "bundle-2")
        bundle["allowed"] = [slot]
        left["generation"] += 1
        left["revision"] = "requests-routing-515"
        _event(state, "routing-booking-window", source_revision=right["revision"],
               observed=right["observed"], target="bundle-2", allowed=[slot],
               target_revision=left["revision"])


def _check_515(state):
    if failure := _complete(state):
        return failure
    first, second = state["coupling"]["events"]
    if (state["right"]["traffic"]["blue"] != first["blue_load"]
            or state["right"]["observed"]["blue"] != first["blue_target"]):
        return "Routing no longer matches its booking-derived capacity"
    bundle = next(row for row in state["left"]["bundles"] if row["id"] == second["target"])
    return None if bundle["allowed"] == second["allowed"] else "Routing-derived window differs"


def _gold_515(ws):
    data.gold_capacity_v8(ws / "left")
    data.gold_relief_v8(ws / "right")
    head = data._ask(ws / "left", "head")
    bundles = data._scan(ws / "left", ("bundles", "list"))
    bundle = next(row for row in bundles if row["id"] == "bundle-2")
    data._ask(ws / "left", "uncommit", bundle=bundle["id"])
    data._ask(ws / "left", "release", bundle=bundle["id"])
    availability = data._ask(ws / "left", "availability")
    key = f"composite/{head['revision']}/{availability['generation']}"
    result = data._ask(ws / "left", "reserve", bundle=bundle["id"], slot=bundle["allowed"][0],
                       generation=availability["generation"], key=key)
    assert result["status"] == "ok", result
    data._ask(ws / "left", "commit", bundle=bundle["id"], receipt=key)


def _after_516(state, lane, words, opts, response, code):
    left, right = state["left"], state["right"]
    identity = "invoice-503-1"
    if (_phase(state) == 0 and data.warehouse_v8_check(left) is None
            and not right["quote_change_pending"]):
        shipped = copy.deepcopy(left["shipments"])
        units = sum(sum(row["picks"].values()) for row in shipped)
        invoice = right["invoices"][identity]
        invoice["amount"] += units
        invoice["revision"] += 1
        right["closed"] = False
        _event(state, "shipment-invoice-surcharge", source_revision=left["revision"],
               shipments=shipped, units=units, invoice=identity,
               amount=invoice["amount"], invoice_revision=invoice["revision"])
    elif _phase(state) == 1 and workflows._check(right) is None:
        amount = _invoice_total(right, identity)
        order = next(row for row in left["orders"] if row["id"] == "order-1")
        order["need"]["sheet"] = 3 + amount % 2
        left["revision"] = "fulfillment-settlement-516"
        left["generation"] += 1
        _event(state, "settled-shipment-demand", invoice=identity, settled=amount,
               invoice_revision=right["invoices"][identity]["revision"],
               order=order, target_revision=left["revision"])


def _check_516(state):
    if failure := _complete(state):
        return failure
    first, second = state["coupling"]["events"]
    if state["right"]["invoices"][first["invoice"]]["amount"] != first["amount"]:
        return "Shipment-derived invoice amount differs"
    order = next(row for row in state["left"]["orders"] if row["id"] == "order-1")
    return None if order == second["order"] else "Settlement-derived shipment demand differs"


def _gold_516(ws):
    data.gold_warehouse_v8(ws / "left")
    workflows._gold_ledger(ws / "right")
    left = ws / "left"
    status = data._ask(left, "status")
    for row in status["shipments"]:
        if row["order"] == "order-1":
            data._ask(left, "return", shipment=row["shipment"], order=row["order"])
    quote = data._ask(left, "quote", order="order-1")
    assert quote["options"], quote
    key = f"composite/order-1/{quote['generation']}"
    reservation = data._put(left, ("reserve",),
                            {"order": "order-1", "generation": quote["generation"],
                             **quote["options"][0]}, key=key)
    data._ask(left, "ship", order="order-1", reservation=reservation["reservation"],
              key=key + "/shipment")


def _after_517(state, lane, words, opts, response, code):
    left, right = state["left"], state["right"]
    identity = "invoice-502-1"
    if (_phase(state) == 0 and data.transaction_v8_check(left) is None
            and not right["quote_change_pending"]):
        postings = copy.deepcopy(left["ledger"])
        units = sum(row["units"] for row in postings)
        invoice = right["invoices"][identity]
        invoice["amount"] += units
        invoice["revision"] += 1
        right["closed"] = False
        _event(state, "posting-invoice-adjustment", source_revision=left["revision"],
               postings=postings, units=units, invoice=identity,
               amount=invoice["amount"], invoice_revision=invoice["revision"])
    elif _phase(state) == 1 and workflows._check(right) is None:
        amount = _invoice_total(right, identity)
        intent = next(row for row in left["intents"] if row["id"] == "txn-1")
        intent["units"] = 2 + amount % 17
        left["revision"] = "intents-settlement-517"
        _event(state, "settled-posting-correction", invoice=identity, settled=amount,
               invoice_revision=right["invoices"][identity]["revision"],
               intent=intent, target_revision=left["revision"])


def _check_517(state):
    if failure := _complete(state):
        return failure
    first, second = state["coupling"]["events"]
    if state["right"]["invoices"][first["invoice"]]["amount"] != first["amount"]:
        return "Posting-derived invoice amount differs"
    intent = next(row for row in state["left"]["intents"] if row["id"] == "txn-1")
    return None if intent == second["intent"] else "Settlement-derived posting differs"


def _gold_517(ws):
    data.gold_transaction_v8(ws / "left")
    workflows._gold_ledger(ws / "right")
    data.gold_transaction_v8(ws / "left")


def _after_518(state, lane, words, opts, response, code):
    left, right = state["left"], state["right"]
    identity = "blob-5"
    if (_phase(state) == 0 and data.restore_v8_check(left) is None
            and not right["pending_change"]):
        publication = copy.deepcopy(left["published"]["alpha"])
        digest = hashlib.sha256(publication["data"].encode("utf-8")).hexdigest()
        row = right["catalog"][identity]
        row["digest"] = digest
        row["generation"] += 1
        right["revision"] += 1
        workflows._backup_catalog_closure(right)
        _event(state, "restored-backup-content", source_revision=left["revision"],
               publication=publication, item=identity, digest=digest,
               generation=row["generation"], target_revision=right["revision"])
    elif _phase(state) == 1 and workflows._check(right) is None:
        published = copy.deepcopy(right["published"]["items"][identity])
        left["objects"]["alpha"] = published["digest"][:6].upper()
        left["revision"] = "snapshot-backup-518"
        left["verified"].clear()
        _event(state, "backup-restoration-metadata", publication_revision=right["published"]["revision"],
               published=published, alpha=left["objects"]["alpha"], target_revision=left["revision"])


def _check_518(state):
    if failure := _complete(state):
        return failure
    first, second = state["coupling"]["events"]
    if state["right"]["catalog"]["blob-5"]["digest"] != first["digest"]:
        return "Backup does not retain the verified restoration content digest"
    return None if state["left"]["objects"]["alpha"] == second["alpha"] else "Backup-derived alpha differs"


def _gold_518(ws):
    data.gold_restore_v8(ws / "left")
    workflows._gold_backup(ws / "right")
    data.gold_restore_v8(ws / "left")


_COMMON = """
There are exactly TWO additional producer transactions in this composite,
in addition to the bounded corrections documented by each child. They are
one-shot transactions, triggered by domain completion barriers, never by read
counts or elapsed time. pair status exposes phase and the immutable event log,
including the input values and revisions used at each barrier. A response can
precede the cross-transaction; read current state after its coupling notice.
Each transaction binds the source value AT ITS barrier. The feedback correction
does not recursively re-export to the other side: two events are the maximum.
Both child goals and both cross-domain bindings must hold at the end. A previous
finish/close/publication can become stale; reconcile current authoritative data.
Preserve foreign history and all existing safety rules. An idempotency receipt
does not become fresh after compensation: use a new key for a genuinely new
operation. You can implement the finite reconciliation loops in a local script.
"""


PAIRS = {
    512: {
        "left": 472, "right": 486, "slug": "incident_delivery_feedback",
        "title": "Reconcile incident ownership with a changing delivery policy",
        "docs": _COMMON + """
1. Once left reports and alerts fully reflect its two native corrections,
collect sorted urgent-open issue IDs U. Right event-2 is a leaf. Its current
wanted effect becomes 0 if U is nonempty, otherwise 1; its escrow units become
1+(len(U) mod 2), and its revision increases. This CURRENT status policy
supersedes the initial manifest for event-2 only. Existing deliveries/receipts
remain real: cancel or reverse as needed, then settle all escrow and finish.
The other five wanted effects and the foreign receipt are unchanged.
2. Once right successfully finishes the current policy, let D be the sum of its
six net effects. Left receives a new issue delivery-reconciliation, revision1,
open/urgent, owned by head.teams[D mod len(head.teams)]. Left revision becomes
incidents-delivery-512; its drafts expire. Publish all current reports and
reconcile alerts, preserving foreign-monitor. The event log records actual
effects and committed non-reversed receipts, not a solver-supplied token.
""",
        "after": _after_512, "check": _check_512, "check_right": _check_512_right,
        "gold": _gold_512,
    },
    513: {
        "left": 473, "right": 490, "slug": "stream_causal_feedback",
        "title": "Join a corrected event stream to a causal account feed",
        "docs": _COMMON + """
Window keys in ALL left sink maps/deltas are str(10*k), the decimal START of
[10*k,10*k+10), never str(k). Task and foreign contributions stay separate.
Right e7 has a provisional account list until the first cross event. Do not
irreversibly ack the right feed before that event. You may process other events
first. If e7 was provisionally applied, undo that leaf before the cross event;
undo is impossible after ack, so a premature ack cannot be repaired.
1. When left has drained its native producers and exactly checkpointed/published
current closed windows, AND right e7 is not applied and right acked is0, let
N=sum(left task_counts.values()). Set right e7.accounts to
[a{N mod6}, a{(N+2) mod6}]. Its other fields and all seven other events are
unchanged. Existing row labels/resources are NEVER reset. Refetch feed and
current physical row keys/versions, apply the current events, ack through8,
and finish. The current feed replaces the initial e7 account specification.
2. A correct finished right feed releases one new left row causal-ack-513,
revision1, partition a, timestamp 10+the sum of the two numeric suffixes in
current e7.accounts. Its offset is the next contiguous feed offset. Source
revision becomes feed-causal-513. Consume/checkpoint/reconcile this real row;
the already-current watermark closes its window. Foreign sink is unchanged.
""",
        "after": _after_513, "check": _check_513, "check_right": _check_513_right,
        "gold": _gold_513,
    },
    514: {
        "left": 474, "right": 509, "slug": "closeout_configuration_feedback",
        "title": "Bind session closeouts to atomic configuration edits",
        "docs": _COMMON + """
1. After all current left closeouts/annotations are correct, sum elapsed over
their rows as E and count late=true as L. Right config/app.txt gains the
task-owned line closeout-seconds=pending;late=pending and a new revision.
In addition to its original changes, replace that line once with
closeout-seconds=E;late=L (decimal integers, trailing newline). Its expected
value is in pair status and independently derivable from closeout status.
Keep every other byte and concurrent user line; CAS and verify current content.
2. When all right edits/read-backs are current, let V be config/app.txt's
verified revision. Left session-1 receives a higher revision and end increases
by 1+(V mod3); source becomes sessions-configuration-514. Its batch's reviewer
annotation gains '; config/app.txt read-back revision V' and a new revision.
Refresh events and annotations, recompute elapsed/late, then activate all
current closeouts. Do not re-export this second closeout into config: the
configuration summary intentionally records the first barrier's closeouts.
""",
        "after": _after_514, "check": _check_514, "gold": _gold_514,
    },
    515: {
        "left": 475, "right": 483, "slug": "booking_routing_feedback",
        "title": "Replan reservations against live traffic placement",
        "docs": _COMMON + """
1. Once all left bookings satisfy current maintenance AND right's native first
movement correction has happened, take bundle-2's committed slot S. Right blue
load becomes 3+S. Its target is b for even S, c for odd S. Both b and c capacity
become6, then the target capacity becomes exactly3+S. Other loads/capacities and
protected routes survive. Source becomes health-bookings-515; existing plans
and canary receipts are stale. Safely replan, apply and complete a job for this
CURRENT revision; a zero-move job is valid if the actual assignment is already
safe. The large blue load forces the matching node; evacuate spare d as before.
2. Once that current job is complete and routing is valid, bundle-2's allowed
slots become [5] if observed blue is on b, otherwise [4]. Its source becomes
requests-routing-515 and reservation generation advances. Existing bookings
remain committed and consume capacity; compensate the obsolete bundle-2
booking, reserve its new allowed slot with a fresh receipt and commit it.
All other allowed slots, dependencies and maintenance allocations stay valid.
""",
        "after": _after_515, "check": _check_515, "gold": _gold_515,
    },
    516: {
        "left": 476, "right": 503, "slug": "shipment_invoice_feedback",
        "title": "Reconcile recalled shipments with invoice adjustments",
        "docs": _COMMON + """
1. After left's recall is fully reconciled and right's first quote correction
has been released, U is the sum of all SKU quantities in active left shipments.
Increase right invoice-503-1.amount by U, increment its revision and reopen the
ledger. That invoice is not a target of later native settlement adjustments;
old quotes can be stale. Keep user credits, settle current balances and close.
2. A correct closed right ledger releases a change to left order-1: sheet need
becomes 3+(the actually paid total for invoice-503-1 mod2), with ink unchanged.
Source becomes fulfillment-settlement-516 and stock generation advances.
Existing shipments are not magically changed or erased. Return a now-incorrect
order-1 shipment using its owner-bound receipt, re-quote and ship its complete
new need with fresh reservation/shipment keys. Recall quarantine and the
customer's sealed pallet remain protected. Other orders stay complete.
""",
        "after": _after_516, "check": _check_516, "gold": _gold_516,
    },
    517: {
        "left": 477, "right": 502, "slug": "posting_settlement_feedback",
        "title": "Reconcile an import ledger with a corrected settlement ledger",
        "docs": _COMMON + """
1. After left import is current, compensated and checkpointed AND right's first
quote correction is released, U is the sum of units in active task postings.
Increase invoice-502-1.amount by U, increment revision and reopen the ledger.
Foreign credit is not in U. This invoice receives no native later adjustment.
Re-quote, reconcile balances while preserving user credits, and close right.
2. On correct closure let P be the actually paid total for invoice-502-1.
Left txn-1.units becomes 2+(P mod17); revision becomes intents-settlement-517.
Its old posting remains committed: reverse that exact task receipt and apply
the corrected intent once under a fresh key, then checkpoint. The original
operation key stays consumed, and the foreign user-credit must survive.
""",
        "after": _after_517, "check": _check_517, "gold": _gold_517,
    },
    518: {
        "left": 478, "right": 500, "slug": "restore_backup_feedback",
        "title": "Publish mutually dependent restoration and backup snapshots",
        "docs": _COMMON + """
1. Once the current corrected left snapshot has verified dependency receipts
and no staging, AND right's initial source correction is released, set right
blob-5.digest to SHA-256 of the UTF-8 bytes of left's published alpha data.
Increment blob-5 generation and right source revision, propagating ordinary
dependency-generation closure. Existing copied bytes, jobs and verification
records stay real and may now be stale. Drain native corrections, repin/copy/
verify current generations, publish the current backup and prune only its
documented obsolete source revision. All digest inputs are publicly readable.
2. A fully published and verified right backup changes left alpha content to
the FIRST SIX hexadecimal characters of the published blob-5 digest, UPPERCASE.
Left source becomes snapshot-backup-518; outstanding verification receipts
expire. Previously published objects remain unchanged and now need refresh.
Re-fetch current objects, verify and publish them in dependency order within
staging quota. The backup intentionally binds pre-feedback alpha, so this
second metadata correction does not create an endless digest recursion.
""",
        "after": _after_518, "check": _check_518, "gold": _gold_518,
    },
}
