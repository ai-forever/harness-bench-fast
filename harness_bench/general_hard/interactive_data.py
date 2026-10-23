"""Fourteen honest stateful data workflows for Wave 20 v8.

Service implementations and authoritative state stay on the host. Gold clients
use only documented taskctl requests and public workspace fixtures.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json

from .data_tasks import TASKS as LEGACY_TASKS
from .interactive_common import interactive_task, request


# These small helpers are included in each host service, never in the workspace.
def _reply(status="ok", **values):
    return copy.deepcopy({"status": status, **values}), 0 if status in {
        "ok",
        "accepted",
        "partial",
        "outcome_unknown",
    } else 2


def _body(opts, io):
    def reject_constant(value):
        raise ValueError("Nonfinite JSON constant: " + value)

    value = io.read(opts["file"]) if "file" in opts else opts.get("json", "null")
    return json.loads(value, parse_constant=reject_constant)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _pages(rows, cursor, scope):
    chunks = [rows[i : i + 2] for i in range(0, len(rows), 2)] or [[]]
    if len(chunks) > 2:
        chunks.insert(1, [])
    page = 0 if not cursor else int(cursor.rsplit("/", 1)[1])
    if cursor and cursor != f"{scope}/{page}":
        return _reply("cursor_mismatch", message="Cursor belongs to another collection/snapshot")
    if not 0 <= page < len(chunks):
        return _reply("invalid_cursor")
    return _reply(
        items=chunks[page],
        next_cursor=f"{scope}/{page + 1}" if page + 1 < len(chunks) else None,
        snapshot=scope,
    )


def _latest(rows, key="id"):
    current = {}
    for row in rows:
        if row[key] not in current or row["rev"] > current[row[key]]["rev"]:
            current[row[key]] = row
    return {k: v for k, v in current.items() if not v.get("deleted", False)}


def _remember(state, key, body, result):
    state["receipts"][key] = {"hash": _digest(body), "result": result}


def _prior(state, key, body):
    old = state["receipts"].get(key)
    if old is None:
        return None
    return old["result"] if old["hash"] == _digest(body) else {"status": "key_conflict"}


def _service_initial():
    return copy.deepcopy(CONFIG)  # noqa: F821 - supplied in the isolated service


def _service_handle(state, words, opts, io):
    if words == ["help"]:
        return _reply(documentation=state["documentation"])
    before = copy.deepcopy(state)
    try:
        return FAMILY_HANDLE(state, words, opts, io)  # noqa: F821
    except (ValueError, TypeError, KeyError, IndexError, StopIteration, OverflowError):
        state.clear()
        state.update(before)
        return _reply(
            "invalid_request", message="Check the documented command/schema; no implicit success"
        )


# 472: live revisions, complete pagination, staged versus published reports.
def incident_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"], teams=s["teams"], collection="issues", sealed=s["sealed"]
        )
    if words == ["seal"]:
        if o.get("revision") != s["revision"]:
            return _reply("revision_conflict", current=s["revision"])
        if not s["sealed"]:
            s["rows"].extend(s.pop("pending"))
            s["revision"] = "live-r7"
            s["staged"].clear()
            s["sealed"] = True
        return _reply(revision=s["revision"], sealed=True)
    if words == ["issues", "list"]:
        rev = o.get("revision")
        if rev not in {s["revision"], "cache-r1"}:
            return _reply("revision_conflict", current=s["revision"])
        rows = s["cache"] if rev == "cache-r1" else s["rows"]
        return _pages(rows, o.get("cursor"), "issues:" + rev)
    if words == ["reports", "stage"]:
        body = _body(o, io)
        for team, row in body.items():
            if team not in s["teams"] or set(row) != {"revision", "open", "urgent"}:
                return _reply("invalid_report")
            if not isinstance(row["open"], list) or not isinstance(row["urgent"], list):
                return _reply("invalid_report")
        s["staged"].update(copy.deepcopy(body))
        return _reply("accepted", staged=sorted(body), published=False)
    if words == ["reports", "publish"]:
        if not s["sealed"]:
            incident_handle(s, ["seal"], {"revision": s["revision"]}, io)
            return _reply(
                "revision_conflict",
                current=s["revision"],
                sealed=True,
                message="The publication barrier flushed corrections and invalidated staged reports",
            )
        team, key = o["team"], o["key"]
        body = {"team": team, "revision": o["revision"], "report": s["staged"].get(team)}
        old = _prior(s, key, body)
        if old:
            return old, 0 if old["status"] == "ok" else 2
        if o["revision"] != s["revision"] or team not in s["staged"]:
            return _reply("revision_conflict", current=s["revision"])
        s["published"][team] = copy.deepcopy(s["staged"][team])
        result = {"status": "ok", "team": team, "revision": s["revision"], "published": True}
        _remember(s, key, body, result)
        if team == s["teams"][1]:
            return _reply(
                "outcome_unknown",
                key=key,
                message="Publication receipt was lost; status or same-key replay is safe",
            )
        return result, 0
    if words == ["reports", "status"]:
        return _reply(published=copy.deepcopy(s["published"]), staged=sorted(s["staged"]))
    return _reply("unknown_command")


def incident_check(s):
    if not s["sealed"]:
        return "The publication cut must be sealed"
    live = _latest(s["rows"])
    expected = {}
    for team in s["teams"]:
        rows = [r for r in live.values() if r["team"] == team and r["state"] == "open"]
        expected[team] = {
            "revision": s["revision"],
            "open": sorted(r["id"] for r in rows),
            "urgent": sorted(r["id"] for r in rows if r["priority"] == "urgent"),
        }
    actual = copy.deepcopy(s["published"])
    for row in actual.values():
        row["open"] = sorted(row["open"])
        row["urgent"] = sorted(row["urgent"])
    return (
        None
        if actual == expected
        else "Current reports for every team must be published from all latest revisions"
    )


# 473: engine checkpoints and sink generations have different persistence scope.
def stream_desired(s):
    high = s["engine"]["high"]
    watermark = min(high.values()) - s["lateness"]
    counts = {}
    for row in _latest(s["engine"]["rows"]).values():
        start = row["ts"] // 10 * 10
        if start + 10 <= watermark:
            key = str(start)
            counts[key] = counts.get(key, 0) + 1
    return watermark, counts


def stream_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            end_offset=len(s["feed"]),
            lateness=s["lateness"],
            checkpoint=s["checkpoint"]["offset"],
        )
    if words == ["feed", "list"]:
        if o.get("revision") != s["revision"]:
            return _reply("revision_conflict", current=s["revision"])
        return _pages(s["feed"], o.get("cursor"), "feed:" + s["revision"])
    if words == ["engine", "status"]:
        watermark, _ = stream_desired(s)
        return _reply(
            epoch=s["epoch"],
            engine=copy.deepcopy(s["engine"]),
            watermark=watermark,
            checkpoint_offset=s["checkpoint"]["offset"],
        )
    if words == ["engine", "apply"]:
        rows = _body(o, io)
        if o.get("epoch") != str(s["epoch"]):
            return _reply("epoch_conflict", epoch=s["epoch"])
        for row in rows:
            offset = row["offset"]
            if offset <= s["engine"]["offset"]:
                if s["feed"][offset - 1] != row:
                    return _reply("event_conflict", committed_offset=s["engine"]["offset"])
                continue
            if offset != s["engine"]["offset"] + 1 or s["feed"][offset - 1] != row:
                return _reply(
                    "offset_gap",
                    next_offset=s["engine"]["offset"] + 1,
                    committed_offset=s["engine"]["offset"],
                )
            if row["kind"] == "row":
                s["engine"]["rows"].append(copy.deepcopy(row))
            s["engine"]["high"][row["partition"]] = max(
                s["engine"]["high"][row["partition"]], row["ts"]
            )
            s["engine"]["offset"] = offset
        return _reply(offset=s["engine"]["offset"], checkpointed=False)
    if words == ["engine", "checkpoint"]:
        s["checkpoint"] = copy.deepcopy(s["engine"])
        return _reply(offset=s["checkpoint"]["offset"], sink_unchanged=True)
    if words == ["engine", "rollback"]:
        s["engine"] = copy.deepcopy(s["checkpoint"])
        s["epoch"] += 1
        return _reply(epoch=s["epoch"], sink_unchanged=True)
    if words == ["sink", "status"]:
        return _reply(generation=s["sink_generation"], counts=s["sink"].copy())
    if words == ["sink", "adjust"]:
        delta = _body(o, io)
        key = o["key"]
        body = {"generation": o["generation"], "delta": delta}
        old = _prior(s, key, body)
        if old:
            return old, 0 if old["status"] == "ok" else 2
        if str(s["sink_generation"]) != o["generation"]:
            return _reply("generation_conflict", generation=s["sink_generation"])
        if not isinstance(delta, dict) or any(type(v) is not int for v in delta.values()):
            return _reply("invalid_delta")
        for key2, value in delta.items():
            s["sink"][key2] = s["sink"].get(key2, 0) + value
            if s["sink"][key2] == 0:
                del s["sink"][key2]
        s["sink_generation"] += 1
        result = {"status": "ok", "generation": s["sink_generation"], "counts": s["sink"].copy()}
        _remember(s, key, body, result)
        return _reply(
            "outcome_unknown",
            key=key,
            message="Sink commit receipt was lost; query sink or replay this key",
        )
    return _reply("unknown_command")


def stream_check(s):
    watermark, desired = stream_desired(s)
    if s["engine"]["offset"] != len(s["feed"]) or s["checkpoint"]["offset"] != len(s["feed"]):
        return "All source offsets must be applied and checkpointed"
    if watermark < 20 or s["sink"] != desired:
        return "External sink must equal closed windows at the current global watermark"
    return None


COMMON_DOC = """
Transport: python3 tools/taskctl COMMAND [--option VALUE]. `help` returns this
same documentation. --file PATH reads one JSON value from your own workspace
file; --json JSON is equivalent. JSON must be finite (no NaN or Infinity).
Malformed arguments return a recoverable error and roll back that call; explicit
partial replies keep the durable effects described by the command. Status and
exit code are separate: accepted/partial/outcome_unknown use exit0 and are not
proof that every requested effect completed. Errors are normal recoverable
responses unless a specific forbidden effect below is explicitly documented.
All lists use opaque next_cursor tokens bound to collection and revision.
The only end marker is next_cursor=null. An empty items array can have a next
cursor (an empty filtered storage page). Repeating any read is safe and does
not change domain time. No real sleeps are useful. The service request counter
is only a resource limit, not a hidden trigger. `cache.json` is an explicitly
outdated convenience export, never an authority for current facts.
Idempotency keys are scoped to this task run; the same key and exact payload
returns the same committed receipt, even after outcome_unknown, once that
command's explicitly documented scope preconditions are satisfied. Reusing a
key with a different payload returns key_conflict and does not apply that payload.
"""

INCIDENT_DOC = (
    COMMON_DOC
    + """
Goal: publish current open and urgent-open issue ids for every team listed by
head. head -> revision,teams,sealed. issues list --revision R [--cursor TOKEN] returns
append-only revisions {id,rev,team,state,priority,deleted?}; choose greatest rev
per id, not the last arrival. A tombstone deletes that id. Equal rev is an exact
replay. state=open and priority=urgent have their literal meanings.
Revision cache-r1 exposes the stale export. The first seal --revision R flushes
pending corrections, returns the new current revision and clears staged reports.
After this barrier the source is immutable. Publishing before sealing performs
the same barrier and returns revision_conflict: refetch the new cut and restage.
Calling seal again with the current revision is safe. Old live cursors expire.
reports stage --file F takes {team:{revision,open:[id],urgent:[id]}}; subsets of
teams and batch staging are allowed. Staging does not publish. reports publish
--team T --revision R --key K publishes that staged team if R is current.
One team's publication receipt can be lost AFTER commit; outcome_unknown is
resolved by reports status or identical-key replay. reports status returns all
published rows. Publish any team order; repeated safe reads are irrelevant.
No particular cursor sequence or number of reads is required by the grader.
"""
)

STREAM_DOC = (
    COMMON_DOC
    + """
Goal: consume every feed offset, checkpoint the final engine state, and reconcile
the separately persisted external sink to exactly the current closed windows.
head -> revision,end_offset,lateness,checkpoint. feed list --revision R
[--cursor TOKEN] returns authoritative events with contiguous offset from1,
partition,ts,kind=row|heartbeat. Rows additionally contain id,rev,deleted?.
Use greatest row rev per id; a tombstone wins over older replayed revisions.
Heartbeats only advance that partition's high timestamp. Window=[10*k,10*k+10).
Global watermark=min(high of ALL partitions)-lateness. Only windows ending at
or before watermark are published; count nondeleted latest rows in each window.
engine status -> epoch,engine:{offset,rows,high},watermark,checkpoint_offset.
engine apply --epoch E --file [events] applies events in contiguous offset order;
exact duplicates at already-applied offsets are safe. It is NOT a checkpoint.
A batch stops at the first conflicting/gapped event; earlier events in that
batch remain applied and committed_offset tells you how far. Reconcile status
and continue from the next offset; an error does not roll back the prefix.
engine checkpoint stores engine rows/high/offset only. engine rollback restores
that checkpoint and increments epoch. It DOES NOT rewind sink or its generation.
The initial engine has already been restored, while a newer sink survived.
sink status -> generation,counts (decimal window-start strings to integer counts).
sink adjust --generation G --key K --file {window:integer_delta} commits deltas
with compare-and-swap. Zero totals are removed. Receipt may be lost AFTER commit;
query the sink or replay the same key, never infer that timeout rolled it back.
A stale generation/epoch changes nothing. Any equivalent batching, replay,
checkpoint strategy or order of safe observations is accepted.
"""
)


# Public client utilities. They never read host state or implementation/config.
def _ask(ws, *words, **opts):
    argv = list(words)
    for key, value in opts.items():
        if value is not None:
            argv += ["--" + key.replace("_", "-"), str(value)]
    response, _ = request(ws, *argv)
    return response


def _put(ws, words, value, **opts):
    path = ws / "candidate.json"
    path.write_text(json.dumps(value) + "\n")
    return _ask(ws, *words, file="candidate.json", **opts)


def _scan(ws, words, *, short=False, **opts):
    rows = []
    cursor = None
    while True:
        result = _ask(ws, *words, cursor=cursor, **opts)
        assert result["status"] == "ok", result
        rows.extend(result["items"])
        cursor = result["next_cursor"]
        if short or cursor is None:
            return rows


def gold_incident(ws, *, wrong=None, alternate=False):
    meta = _ask(ws, "head")
    if not alternate:
        sealed = _ask(ws, "seal", revision=meta["revision"])
        meta["revision"] = sealed["revision"]
    rows = _scan(
        ws,
        ("issues", "list"),
        revision="cache-r1" if wrong == "stale" else meta["revision"],
        short=wrong == "first_page",
    )
    live = {}
    for row in rows:
        if row["id"] not in live or row["rev"] > live[row["id"]]["rev"]:
            live[row["id"]] = row
    teams = list(reversed(meta["teams"])) if alternate else meta["teams"]
    reports = {}
    for team in teams:
        active = [
            row
            for row in live.values()
            if row["team"] == team and row["state"] == "open" and not row.get("deleted")
        ]
        reports[team] = {
            "revision": meta["revision"],
            "open": sorted(r["id"] for r in active),
            "urgent": sorted(r["id"] for r in active if r["priority"] == "urgent"),
        }
        if not alternate:
            _put(ws, ("reports", "stage"), {team: reports[team]})
    if alternate:
        _put(ws, ("reports", "stage"), reports)
    for team in teams:
        result = _ask(
            ws, "reports", "publish", team=team, revision=meta["revision"], key="report-" + team
        )
        if result["status"] == "revision_conflict" and alternate:
            return gold_incident(ws, wrong=wrong)
        _ask(ws, "reports", "status")


def gold_stream(ws, *, wrong=None, alternate=False):
    meta = _ask(ws, "head")
    engine = _ask(ws, "engine", "status")
    sink = _ask(ws, "sink", "status")
    cursor = None
    all_rows = []
    while True:
        page = _ask(ws, "feed", "list", revision=meta["revision"], cursor=cursor)
        rows = [r for r in page["items"] if r["offset"] > engine["engine"]["offset"]]
        if alternate:
            all_rows += rows
        else:
            _put(ws, ("engine", "apply"), rows, epoch=engine["epoch"])
        cursor = page["next_cursor"]
        if cursor is None or wrong == "first_page":
            break
    if alternate:
        _put(ws, ("engine", "apply"), all_rows, epoch=engine["epoch"])
        _put(ws, ("engine", "apply"), all_rows, epoch=engine["epoch"])
    _ask(ws, "engine", "checkpoint")
    final = _ask(ws, "engine", "status")
    latest = {}
    for row in final["engine"]["rows"]:
        if row["id"] not in latest or row["rev"] > latest[row["id"]]["rev"]:
            latest[row["id"]] = row
    counts = {}
    for row in latest.values():
        start = row["ts"] // 10 * 10
        if not row.get("deleted") and start + 10 <= final["watermark"]:
            counts[str(start)] = counts.get(str(start), 0) + 1
    old = {} if wrong == "rewind_sink" else sink["counts"]
    delta = {k: counts.get(k, 0) - old.get(k, 0) for k in counts.keys() | old.keys()}
    _put(ws, ("sink", "adjust"), delta, generation=sink["generation"], key="reconcile")
    if alternate:
        _put(ws, ("sink", "adjust"), delta, generation=sink["generation"], key="reconcile")
    _ask(ws, "sink", "status")


def _incident_config(variant=0):
    teams = ["amber", "birch", "cedar", "delta"]
    rows = [
        {
            "id": f"issue-{i:02}",
            "rev": 1,
            "team": teams[i % 4],
            "state": "closed" if i % 5 == 0 else "open",
            "priority": "urgent" if i % 3 == 0 else "normal",
        }
        for i in range(1, 13)
    ]
    cache = copy.deepcopy(rows)
    rows += [
        {**rows[2], "rev": 2, "state": "closed"},
        {**rows[3], "rev": 2, "priority": "urgent"},
        {**rows[11], "rev": 2, "deleted": True},
        copy.deepcopy(rows[2]),
    ]
    pending = [
        {**rows[4], "rev": 3, "state": "open", "priority": "urgent"},
        {**rows[7], "rev": 3, "team": "amber" if variant == 0 else "cedar"},
    ]
    return {
        "revision": "live-r6",
        "sealed": False,
        "teams": teams,
        "rows": rows,
        "pending": pending,
        "cache": cache,
        "staged": {},
        "published": {},
        "receipts": {},
    }


def _stream_config(variant=0):
    rows = [
        {"kind": "row", "id": f"event-{i}", "rev": 1, "partition": "abc"[i % 3], "ts": i + 1}
        for i in range(12)
    ]
    rows += [
        {**rows[1], "rev": 2, "deleted": True},
        {**rows[5], "rev": 2, "ts": 14 if variant == 0 else 4},
        copy.deepcopy(rows[1]),
    ]
    rows += [{"kind": "heartbeat", "partition": p, "ts": 30} for p in "abc"]
    feed = [{"offset": i + 1, **r} for i, r in enumerate(rows)]
    engine = {"offset": 2, "rows": copy.deepcopy(feed[:2]), "high": {"a": 1, "b": 2, "c": 0}}
    return {
        "revision": "feed-r4",
        "feed": feed,
        "lateness": 3,
        "engine": engine,
        "checkpoint": copy.deepcopy(engine),
        "epoch": 2,
        "sink_generation": 7,
        "sink": {"0": 4},
        "cache": {"checkpoint_offset": 8, "sink_counts": {}},
        "receipts": {},
    }


# 474: a clock-catalog lease is invalidated by an explicit source barrier.
def session_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(revision=s["revision"], batches=s["batches"], sealed=s["sealed"])
    if words == ["clocks"]:
        return _reply(revision=s["clock_revision"], offsets=copy.deepcopy(s["offsets"]))
    if words == ["seal"]:
        if o.get("revision") != s["revision"]:
            return _reply("revision_conflict", current=s["revision"])
        if not s["sealed"]:
            s["offsets"].update(s.pop("clock_correction"))
            s["clock_revision"] = "clocks-9"
            s["revision"] = "sessions-5"
            s["sealed"] = True
            s["drafts"].clear()
        return _reply(revision=s["revision"], clock_revision=s["clock_revision"], sealed=True)
    if words == ["events", "list"]:
        if o.get("revision") != s["revision"]:
            return _reply("revision_conflict", current=s["revision"])
        return _pages(s["events"], o.get("cursor"), "sessions:" + s["revision"])
    if words == ["closeout", "stage"]:
        body = _body(o, io)
        if o.get("clocks") != s["clock_revision"] or o.get("revision") != s["revision"]:
            return _reply(
                "dependency_changed", revision=s["revision"], clock_revision=s["clock_revision"]
            )
        if not isinstance(body, dict) or any(k not in s["batches"] for k in body):
            return _reply("invalid_batch")
        s["drafts"].update(
            {
                k: {"source": s["revision"], "clocks": s["clock_revision"], "rows": v}
                for k, v in body.items()
            }
        )
        return _reply("accepted", staged=sorted(body), active=False)
    if words == ["closeout", "activate"]:
        if not s["sealed"]:
            session_handle(s, ["seal"], {"revision": s["revision"]}, io)
            return _reply(
                "dependency_changed", revision=s["revision"], clock_revision=s["clock_revision"]
            )
        batch = o["batch"]
        draft = s["drafts"].get(batch)
        if not draft or draft["clocks"] != s["clock_revision"] or draft["source"] != s["revision"]:
            return _reply("missing_current_draft")
        s["active"][batch] = copy.deepcopy(draft)
        return _reply(batch=batch, active=True)
    if words == ["closeout", "status"]:
        return _reply(active=copy.deepcopy(s["active"]), drafts=sorted(s["drafts"]))
    return _reply("unknown_command")


def session_check(s):
    if not s["sealed"]:
        return "Seal the input and the corrected clock catalog"
    expected = {batch: [] for batch in s["batches"]}
    for row in _latest(s["events"]).values():
        if row["cancelled"]:
            continue
        elapsed = (
            row["end"]
            - s["offsets"][row["end_zone"]]
            - row["start"]
            + s["offsets"][row["start_zone"]]
        )
        expected[row["batch"]].append(
            {"id": row["id"], "elapsed": elapsed, "late": elapsed > row["limit"]}
        )
    actual = s["active"]
    if set(actual) != set(expected):
        return "Every batch needs an active current closeout"
    for batch, rows in expected.items():
        report = actual[batch]
        if report["source"] != s["revision"] or report["clocks"] != s["clock_revision"]:
            return "Closeout uses an invalidated clock/source dependency"
        if sorted(report["rows"], key=lambda r: r["id"]) != sorted(rows, key=lambda r: r["id"]):
            return "Closeout must use latest noncancelled sessions and corrected UTC durations"
        if any(
            type(r.get("elapsed")) is not int or type(r.get("late")) is not bool
            for r in report["rows"]
        ):
            return "Elapsed is an integer and late is boolean"
    return None


SESSION_DOC = (
    COMMON_DOC
    + """
Goal: activate a closeout for each batch. head -> revision,batches,sealed;
clocks -> revision,offsets, where UTC=local-offset. events list --revision R
[--cursor C] returns session revisions {id,rev,batch,start,start_zone,end,
end_zone,limit,cancelled}. Greatest rev per id wins, not arrival order.
Omit cancelled sessions. Report each remaining id with elapsed=UTC(end)-UTC(start)
and late=(elapsed>limit), not >=. Integers and booleans have distinct types.
seal --revision R flushes pending clock corrections, changes the source and
clock revisions, clears old drafts, and freezes both. clocks must then be read
again. Activating before seal also performs this barrier and returns
dependency_changed without activating anything. No wall-clock waits are needed.
closeout stage --revision R --clocks CR --file {batch:[{id,elapsed,late},...]}
accepts subsets/batches but does not activate them. Wrong dependencies return
dependency_changed and current revision identifiers. closeout activate --batch B
activates only a current draft. closeout status returns active reports/drafts.
All batches and all latest sessions must be covered; any row/batch order and
any staging batch sizes are allowed. cache.json has obsolete clock offsets.
"""
)


def _session_config(variant=0):
    events = [
        {
            "id": f"session-{i}",
            "rev": 1,
            "batch": f"batch-{i % 3}",
            "start": 10 + i,
            "start_zone": "east",
            "end": 27 + i,
            "end_zone": "west",
            "limit": 20,
            "cancelled": False,
        }
        for i in range(9)
    ]
    events += [
        {**events[2], "rev": 2, "cancelled": True},
        {**events[7], "rev": 3, "end": 40},
        copy.deepcopy(events[7]),
    ]
    return {
        "revision": "sessions-4",
        "clock_revision": "clocks-8",
        "sealed": False,
        "batches": ["batch-0", "batch-1", "batch-2"],
        "events": events,
        "offsets": {"east": 2, "west": 0},
        "clock_correction": {"west": -4 if variant == 0 else 3},
        "drafts": {},
        "active": {},
        "cache": {"offsets": {"east": 2, "west": 0}},
    }


def gold_session(ws, *, wrong=None, alternate=False):
    meta = _ask(ws, "head")
    old_clocks = _ask(ws, "clocks")
    sealed = _ask(ws, "seal", revision=meta["revision"])
    clocks = _ask(ws, "clocks")
    rows = _scan(ws, ("events", "list"), revision=sealed["revision"], short=wrong == "first_page")
    current = {}
    for row in rows:
        if row["id"] not in current or row["rev"] > current[row["id"]]["rev"]:
            current[row["id"]] = row
    offset = old_clocks["offsets"] if wrong == "stale" else clocks["offsets"]
    reports = {b: [] for b in meta["batches"]}
    for row in current.values():
        if not row["cancelled"]:
            elapsed = (
                row["end"] - offset[row["end_zone"]] - row["start"] + offset[row["start_zone"]]
            )
            reports[row["batch"]].append(
                {"id": row["id"], "elapsed": elapsed, "late": elapsed > row["limit"]}
            )
    batches = list(reversed(meta["batches"])) if alternate else meta["batches"]
    if alternate:
        _put(
            ws,
            ("closeout", "stage"),
            reports,
            revision=sealed["revision"],
            clocks=clocks["revision"],
        )
    for batch in batches:
        if not alternate:
            _put(
                ws,
                ("closeout", "stage"),
                {batch: reports[batch]},
                revision=sealed["revision"],
                clocks=clocks["revision"],
            )
        _ask(ws, "closeout", "activate", batch=batch)
        _ask(ws, "closeout", "status")


# 475: resource-level holds are durable even when a bundle is only partial.
def capacity_free(s, resource, slot):
    used = s["maintenance"].get(resource + ":" + str(slot), 0)
    for holds in s["holds"].values():
        used += int(holds.get(resource) == slot)
    for allocation in s["committed"].values():
        used += int(allocation.get(resource) == slot)
    return s["capacity"][resource] - used


def capacity_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            slots=s["slots"],
            capacity=s["capacity"],
            generation=s["generation"],
        )
    if words == ["bundles", "list"]:
        return _pages(s["bundles"], o.get("cursor"), "bundles:" + s["revision"])
    if words == ["availability"]:
        return _reply(
            generation=s["generation"],
            free={r: [capacity_free(s, r, slot) for slot in s["slots"]] for r in s["capacity"]},
        )
    if words == ["reserve"]:
        bundle = next(b for b in s["bundles"] if b["id"] == o["bundle"])
        slot = int(o["slot"])
        body = {"bundle": bundle["id"], "slot": slot, "generation": o["generation"]}
        prior = _prior(s, o["key"], body)
        if prior:
            return prior, 0
        if o["generation"] != str(s["generation"]):
            return _reply("generation_conflict", generation=s["generation"])
        if slot not in s["slots"] or bundle["id"] in s["committed"]:
            return _reply("invalid_reservation")
        if s["holds"].get(bundle["id"]):
            return _reply("release_existing_holds", holds=s["holds"][bundle["id"]])
        holds = {r: slot for r in bundle["resources"] if capacity_free(s, r, slot) > 0}
        s["holds"][bundle["id"]] = holds
        s["generation"] += 1
        result = {
            "status": "ok" if len(holds) == len(bundle["resources"]) else "partial",
            "bundle": bundle["id"],
            "held": holds,
            "generation": s["generation"],
            "key": o["key"],
            "committed": False,
        }
        _remember(s, o["key"], body, result)
        return result, 0
    if words == ["release"]:
        s["holds"].pop(o["bundle"], None)
        s["generation"] += 1
        return _reply(generation=s["generation"])
    if words == ["commit"]:
        bundle = next(b for b in s["bundles"] if b["id"] == o["bundle"])
        receipt = s["receipts"].get(o["receipt"], {}).get("result", {})
        holds = s["holds"].get(bundle["id"], {})
        if (
            receipt.get("bundle") != bundle["id"]
            or receipt.get("held") != holds
            or set(holds) != set(bundle["resources"])
            or len(set(holds.values())) != 1
        ):
            return _reply("receipt_or_coverage_mismatch", bundle=bundle["id"], held=holds)
        s["committed"][bundle["id"]] = s["holds"].pop(bundle["id"])
        s["generation"] += 1
        return _reply(
            "outcome_unknown",
            bundle=bundle["id"],
            message="Commit acknowledgement lost; inspect status",
        )
    if words == ["status"]:
        return _reply(
            holds=copy.deepcopy(s["holds"]),
            committed=copy.deepcopy(s["committed"]),
            generation=s["generation"],
        )
    return _reply("unknown_command")


def capacity_check(s):
    if any(s["holds"].values()):
        return "Release all uncommitted partial holds"
    if set(s["committed"]) != {b["id"] for b in s["bundles"]}:
        return "Every bundle must be committed, not merely reserved"
    for bundle in s["bundles"]:
        allocation = s["committed"][bundle["id"]]
        if set(allocation) != set(bundle["resources"]) or len(set(allocation.values())) != 1:
            return "A bundle needs every resource at one common slot"
    if any(capacity_free(s, r, slot) < 0 for r in s["capacity"] for slot in s["slots"]):
        return "Capacity includes maintenance and all committed bundles"
    return None


CAPACITY_DOC = (
    COMMON_DOC
    + """
Goal: commit every requested resource bundle at any feasible common slot, leave
no partial holds, and preserve maintenance allocations. head returns slots,
per-resource capacity,revision. bundles list [--cursor C] returns id,resources.
availability returns generation and free[resource][slot_index]; maintenance,
existing holds and committed bundles all consume capacity. cache.json is stale.
reserve --bundle B --slot S --generation G --key K independently holds each
available required resource at S. It may return partial with a durable held map:
neither exit0 nor status=partial means the entire bundle is held. No resource is
held if generation is stale. Every successful reserve changes generation, so
another precomputed availability token is stale. One bundle cannot hold two
slots: release --bundle B clears its holds (not committed allocations), then
query availability and reserve another slot with a fresh key. A repeated exact
reserve key returns its original receipt, even after release; it does not
reacquire resources. Thus a receipt must match the current bundle AND held map.
commit --bundle B --receipt K requires the reservation key belonging to B and
all its live holds at a single slot. The acknowledgement is outcome_unknown
AFTER commit; status returns holds,committed,generation. Replaying commit after
the holds have been consumed may return receipt_or_coverage_mismatch; status is
authoritative. Any feasible slot choices, bundle order and extra reads pass.
"""
)


def _capacity_config(variant=0):
    resources = [["room", "screen"], ["room", "mic"], ["screen", "mic"], ["room", "screen", "mic"]]
    return {
        "revision": "requests-3",
        "slots": list(range(4)),
        "capacity": {"room": 2, "screen": 2, "mic": 2},
        "bundles": [{"id": f"bundle-{i}", "resources": r} for i, r in enumerate(resources)],
        "maintenance": {("screen:0" if variant == 0 else "room:0"): 2, "mic:1": 2},
        "generation": 6,
        "holds": {},
        "committed": {},
        "receipts": {},
        "cache": {"free": {r: [2] * 4 for r in ("room", "screen", "mic")}},
    }


def gold_capacity(ws, *, wrong=None, alternate=False):
    head = _ask(ws, "head")
    bundles = _scan(ws, ("bundles", "list"))
    if alternate:
        bundles.reverse()
    first_receipt = None
    for index, bundle in enumerate(bundles):
        available = _ask(ws, "availability")
        slot = (
            0
            if alternate or wrong == "ignore_partial"
            else next(
                slot
                for slot in head["slots"]
                if all(
                    available["free"][r][head["slots"].index(slot)] > 0 for r in bundle["resources"]
                )
            )
        )
        key = f"try-{bundle['id']}"
        receipt = _ask(
            ws,
            "reserve",
            bundle=bundle["id"],
            slot=slot,
            generation=available["generation"],
            key=key,
        )
        if len(receipt.get("held", {})) < len(bundle["resources"]) and wrong != "ignore_partial":
            _ask(ws, "release", bundle=bundle["id"])
            available = _ask(ws, "availability")
            slots = list(reversed(head["slots"])) if alternate else head["slots"]
            slot = next(
                slot
                for slot in slots
                if all(
                    available["free"][r][head["slots"].index(slot)] > 0 for r in bundle["resources"]
                )
            )
            key = f"complete-{bundle['id']}"
            _ask(
                ws,
                "reserve",
                bundle=bundle["id"],
                slot=slot,
                generation=available["generation"],
                key=key,
            )
        first_receipt = first_receipt or key
        used = first_receipt if wrong == "wrong_receipt" and index else key
        _ask(ws, "commit", bundle=bundle["id"], receipt=used)
        _ask(ws, "status")


# 476: warehouse quotes are leases, not reservations or shipment receipts.
def warehouse_options(s, order):
    options = []
    for warehouse, stock in s["stock"].items():
        picks = {}
        for label, count in order["need"].items():
            sku = next((sku for sku in s["catalog"][label] if stock.get(sku, 0) >= count), None)
            if sku is None:
                break
            picks[sku] = count
        else:
            options.append({"warehouse": warehouse, "picks": picks, "fee": s["fees"][warehouse]})
    return sorted(options, key=lambda r: (r["fee"], r["warehouse"]))


def warehouse_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(revision=s["revision"], warehouses=sorted(s["stock"]), catalog=s["catalog"])
    if words == ["orders", "list"]:
        return _pages(s["orders"], o.get("cursor"), "orders:" + s["revision"])
    if words == ["inventory", "reconcile"]:
        warehouse = o["warehouse"]
        for sku, count in s["pending"].get(warehouse, {}).items():
            s["stock"][warehouse][sku] = s["stock"][warehouse].get(sku, 0) + count
        if s["pending"].pop(warehouse, None):
            s["generation"] += 1
        return _reply(generation=s["generation"], warehouse=warehouse, stock=s["stock"][warehouse])
    if words == ["quote"]:
        order = next(row for row in s["orders"] if row["id"] == o["order"])
        return _reply(
            order=order["id"],
            generation=s["generation"],
            options=warehouse_options(s, order),
            reserved=False,
        )
    if words == ["reserve"]:
        body = _body(o, io)
        if (
            not isinstance(body, dict)
            or not isinstance(body.get("picks"), dict)
            or type(body.get("fee")) is not int
            or any(type(count) is not int or count <= 0 for count in body["picks"].values())
        ):
            return _reply("invalid_quantities")
        prior = _prior(s, o["key"], body)
        if prior:
            return prior, 0
        order = next(row for row in s["orders"] if row["id"] == body["order"])
        if body["generation"] != s["generation"] or type(body["generation"]) is not int:
            return _reply("quote_expired", generation=s["generation"], order=order["id"])
        option = {k: body[k] for k in ("warehouse", "picks", "fee")}
        if option not in warehouse_options(s, order):
            return _reply("unavailable", order=order["id"])
        if any(r["order"] == order["id"] for r in s["reservations"].values()):
            return _reply("already_reserved")
        for sku, count in body["picks"].items():
            s["stock"][body["warehouse"]][sku] -= count
        s["generation"] += 1
        reservation = "reservation-" + str(s["generation"])
        value = {
            "order": order["id"],
            "warehouse": body["warehouse"],
            "picks": body["picks"],
            "fee": body["fee"],
        }
        s["reservations"][reservation] = value
        result = {"status": "ok", "reservation": reservation, **value, "shipped": False}
        _remember(s, o["key"], body, result)
        return result, 0
    if words == ["ship"]:
        body = {"order": o["order"], "reservation": o["reservation"]}
        prior = _prior(s, o["key"], body)
        if prior:
            return prior, 0
        held = s["reservations"].get(o["reservation"])
        if not held or held["order"] != o["order"]:
            return _reply("receipt_owner_mismatch")
        s["shipments"].append(s["reservations"].pop(o["reservation"]))
        result = {"status": "ok", "order": o["order"], "shipped": True}
        _remember(s, o["key"], body, result)
        return _reply(
            "outcome_unknown",
            key=o["key"],
            message="Shipment persisted; consult status or replay the identical shipping key",
        )
    if words == ["status"]:
        return _reply(
            reservations=s["reservations"], shipments=s["shipments"], generation=s["generation"]
        )
    return _reply("unknown_command")


def warehouse_check(s):
    if s["reservations"] or s["pending"]:
        return "Reconcile inbound stock and leave no unshipped reservations"
    if sorted(r["order"] for r in s["shipments"]) != sorted(r["id"] for r in s["orders"]):
        return "Each order needs exactly one actual shipment"
    for shipment in s["shipments"]:
        order = next(row for row in s["orders"] if row["id"] == shipment["order"])
        if len(shipment["picks"]) != len(order["need"]):
            return "All order components must ship together from one warehouse"
        for label, count in order["need"].items():
            if sum(shipment["picks"].get(sku, 0) for sku in s["catalog"][label]) != count:
                return "Use only current approved substitutions and exact quantities"
    if any(count < 0 for stock in s["stock"].values() for count in stock.values()):
        return "Stock must not be oversold"
    return None


WAREHOUSE_DOC = (
    COMMON_DOC
    + """
Goal: reconcile every warehouse's inbound inventory and ship every order exactly
once with approved current substitutions, from one warehouse per order. Leave
no unshipped reservations. head returns warehouses,current catalog mapping
component label to ordered approved SKU alternatives. orders list is paginated.
inventory reconcile --warehouse W releases that warehouse's staged arrivals
once and changes the global generation if stock changed. Repeating is safe.
quote --order O returns the current generation and feasible options, each with
warehouse,picks:{sku:quantity},fee. It neither reserves nor ships. Options use
the first sufficiently stocked approved SKU per component; any returned option
is acceptable, not necessarily the cheapest. Stock changes expire ALL quotes.
reserve --key K --file {order,generation,warehouse,picks,fee} consumes stock and
returns reservation,order,warehouse,picks,shipped:false. A stale quote changes
nothing and returns quote_expired: obtain a fresh quote and choose again.
ship --order O --reservation R --key K consumes exactly that live reservation;
R must belong to O. The shipment persists even if its reply is outcome_unknown.
status lists actual shipments and remaining reservations. Query it or replay
the identical shipping key to resolve uncertainty. Replacing the key and making
a NEW reservation for a possibly shipped order can cause duplicate shipment;
duplicate shipment fails the exact-once goal. Any warehouse/order choices and
safe read/replay pattern satisfying the outcome are accepted. Historical cached
quotes, receipt labels and fee ordering do not establish current feasibility.
"""
)


def _warehouse_config(variant=0):
    return {
        "revision": "fulfillment-6",
        "catalog": {"sheet": ["sheet-new", "sheet-alt"], "ink": ["ink-safe"]},
        "orders": [{"id": f"order-{i}", "need": {"sheet": 2, "ink": 1}} for i in range(4)],
        "stock": {
            "north": {"sheet-new": 4, "sheet-alt": 0, "ink-safe": 2},
            "south": {"sheet-new": 0, "sheet-alt": 4, "ink-safe": 2},
        },
        "pending": {
            "north": {"sheet-new": 2 if variant == 0 else 0, "ink-safe": 1},
            "south": {"sheet-alt": 0 if variant == 0 else 2, "ink-safe": 1},
        },
        "fees": {"north": 1, "south": 2},
        "generation": 3,
        "reservations": {},
        "shipments": [],
        "receipts": {},
        "cache": {"catalog": {"sheet": ["sheet-old"], "ink": ["ink-safe"]}, "quote_generation": 2},
    }


def gold_warehouse(ws, *, wrong=None, alternate=False):
    head = _ask(ws, "head")
    orders = _scan(ws, ("orders", "list"))
    for warehouse in head["warehouses"]:
        _ask(ws, "inventory", "reconcile", warehouse=warehouse)
    if alternate:
        orders.reverse()
    first_receipt = None
    for order in orders:
        quote = _ask(ws, "quote", order=order["id"])
        option = quote["options"][-1 if alternate else 0]
        receipt = _put(
            ws,
            ("reserve",),
            {"order": order["id"], "generation": quote["generation"], **option},
            key="hold-" + order["id"],
        )
        first_receipt = first_receipt or receipt["reservation"]
        _ask(ws, "status")
        if wrong != "trust_reserved":
            response = _ask(
                ws,
                "ship",
                order=order["id"],
                reservation=first_receipt if wrong == "wrong_receipt" else receipt["reservation"],
                key="ship-" + order["id"],
            )
            if alternate and response["status"] == "outcome_unknown":
                _ask(
                    ws,
                    "ship",
                    order=order["id"],
                    reservation=receipt["reservation"],
                    key="ship-" + order["id"],
                )
        _ask(ws, "status")


# 477: per-item idempotency survives a partially committed import batch.
def transaction_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            end_offset=len(s["intents"]),
            import_id="interrupted-import",
            routes_sealed=s["routes_sealed"],
        )
    if words == ["intents", "list"]:
        return _pages(s["intents"], o.get("cursor"), "intents:" + s["revision"])
    if words == ["routes"]:
        return _reply(revision=s["route_revision"], routes=s["routes"], sealed=s["routes_sealed"])
    if words == ["routes", "seal"]:
        if not s["routes_sealed"]:
            s["routes"].update(s.pop("pending_routes"))
            s["route_revision"] = "routes-8"
            s["routes_sealed"] = True
        return _reply(revision=s["route_revision"], routes=s["routes"], sealed=True)
    if words == ["import", "status"]:
        return _reply(
            summary="accepted",
            complete=len(s["ledger"]) == len(s["intents"]),
            items=copy.deepcopy(s["ledger"]),
            checkpoint=s["checkpoint"],
        )
    if words == ["import", "apply"]:
        rows = _body(o, io)
        if not s["routes_sealed"]:
            transaction_handle(s, ["routes", "seal"], {}, io)
            return _reply("route_changed", revision=s["route_revision"], committed=[])
        committed = []
        for row in rows:
            intent = next(i for i in s["intents"] if i["id"] == row["id"])
            old = _prior(s, row["key"], row)
            if old:
                if old["status"] != "ok":
                    return _reply(
                        "partial", committed=committed, blocked_id=row["id"], reason="key_conflict"
                    )
                committed.append(old)
                continue
            if (
                row["account"] != s["routes"][intent["channel"]]
                or row["units"] != intent["units"]
                or type(row["units"]) is not int
            ):
                return _reply(
                    "partial",
                    committed=committed,
                    blocked_id=row["id"],
                    reason="route_or_amount_mismatch",
                    route_revision=s["route_revision"],
                )
            s["ledger"].append(
                {
                    "id": row["id"],
                    "account": row["account"],
                    "units": row["units"],
                    "key": row["key"],
                }
            )
            result = {"status": "ok", **s["ledger"][-1]}
            _remember(s, row["key"], row, result)
            committed.append(result)
        return _reply(
            "outcome_unknown",
            submitted=len(rows),
            message="Import receipts lost after the listed payload was processed; inspect item ledger or replay keys",
        )
    if words == ["checkpoint"]:
        s["checkpoint"] = int(o["offset"])
        return _reply(
            "accepted",
            checkpoint=s["checkpoint"],
            note="A checkpoint is only a claimed offset; it does not import records",
        )
    return _reply("unknown_command")


def transaction_check(s):
    if not s["routes_sealed"] or s["checkpoint"] != len(s["intents"]):
        return "Seal routes and checkpoint the complete source offset"
    expected = sorted((i["id"], s["routes"][i["channel"]], i["units"]) for i in s["intents"])
    actual = sorted((i["id"], i["account"], i["units"]) for i in s["ledger"])
    return (
        None
        if actual == expected
        else "Ledger must contain each intended transaction exactly once on its current route"
    )


TRANSACTION_DOC = (
    COMMON_DOC
    + """
Goal: complete the interrupted import exactly once, with the current account
route and exact units for each intent, then checkpoint end_offset. head returns
revision,end_offset,import_id. intents list returns id,offset,channel,units,key;
the supplied key is the original operation idempotency key, not a batch key.
routes returns revision,routes,sealed; routes seal flushes pending routing
corrections and freezes them. Calling import apply before seal performs that
barrier and returns route_changed without importing: read fresh routes.
import status always says summary=accepted for the submitted job, even if it
stopped partway. Its items array is the actual committed ledger, with id,key,
account,units. complete and checkpoint are separate. An initial committed
prefix survived the interrupted attempt; it must not be imported again under
new keys. Receipt ownership is the full id/account/units/key tuple.
import apply --file [{id,key,account,units},...] processes items in array order.
It stops on the first wrong route/amount or reused key with different payload,
returning partial, committed (this call's durable prefix), blocked_id,reason.
Earlier items remain committed. Correct duplicate payloads under original keys
are safe. Fresh keys cause fresh ledger effects, including duplicate ids.
An outcome_unknown reply can occur after the whole batch commits. Resolve it
using the item ledger or exact-key replay, not a fresh batch of fresh keys.
checkpoint --offset N records a claim only; it neither imports nor verifies.
Any item order, batches, safe duplicate replay and status reads are accepted.
"""
)


def _transaction_config(variant=0):
    intents = [
        {
            "id": f"txn-{i}",
            "offset": i + 1,
            "channel": "a" if i % 2 == 0 else "b",
            "units": i + 2,
            "key": f"original-{i}",
        }
        for i in range(8)
    ]
    first = {"id": "txn-0", "key": "original-0", "account": "account-A", "units": 2}
    s = {
        "revision": "intents-5",
        "intents": intents,
        "route_revision": "routes-7",
        "routes": {"a": "account-A", "b": "old-B"},
        "pending_routes": {"b": "account-B" if variant == 0 else "account-C"},
        "routes_sealed": False,
        "ledger": [copy.deepcopy(first)],
        "receipts": {},
        "checkpoint": 1,
        "cache": {
            "summary": "accepted",
            "complete": True,
            "routes": {"a": "account-A", "b": "old-B"},
        },
    }
    _remember(s, first["key"], first, {"status": "ok", **first})
    return s


def gold_transaction(ws, *, wrong=None, alternate=False):
    head = _ask(ws, "head")
    intents = _scan(ws, ("intents", "list"))
    before = _ask(ws, "routes")
    routes = _ask(ws, "routes", "seal")
    current = _ask(ws, "import", "status")
    if wrong == "green_summary":
        _ask(ws, "checkpoint", offset=head["end_offset"])
        return
    if alternate:
        intents.reverse()
    ledger = {row["id"] for row in current["items"]}
    for intent in intents:
        if intent["id"] in ledger and wrong != "fresh_keys":
            continue
        payload = {
            "id": intent["id"],
            "units": intent["units"],
            "key": "new-" + intent["id"] if wrong == "fresh_keys" else intent["key"],
            "account": routes["routes"][intent["channel"]],
        }
        if alternate:
            # An optimistic stale route can commit a prefix or reject this item.
            stale = {**payload, "account": before["routes"][intent["channel"]]}
            _put(ws, ("import", "apply"), [stale])
        _put(ws, ("import", "apply"), [payload])
        _ask(ws, "import", "status")
    _ask(ws, "checkpoint", offset=head["end_offset"])


# 478: received bytes are not authenticated; receipts bind object and manifest.
def restore_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(revision=s["revision"], sealed=s["sealed"])
    if words == ["pin"]:
        if o.get("revision") != s["revision"]:
            return _reply("revision_conflict", current=s["revision"])
        if not s["sealed"]:
            s["objects"].update(s.pop("pending"))
            s["revision"] = "snapshot-7"
            s["staged"].clear()
            s["verified"].clear()
            s["sealed"] = True
        return _reply(revision=s["revision"], sealed=True)
    if words == ["manifest", "list"]:
        if o.get("revision") != s["revision"]:
            return _reply("revision_conflict", current=s["revision"])
        rows = [
            {"object": key, "length": len(value), "digest": _digest(value)}
            for key, value in sorted(s["objects"].items())
        ]
        return _pages(rows, o.get("cursor"), "manifest:" + s["revision"])
    if words == ["fetch"]:
        if o.get("revision") != s["revision"]:
            return _reply("revision_conflict", current=s["revision"])
        value = s["objects"][o["object"]]
        offset = int(o["offset"])
        if not 0 <= offset <= len(value):
            return _reply("invalid_offset")
        stop = min(len(value), offset + 4)
        return _reply(
            object=o["object"],
            revision=s["revision"],
            offset=offset,
            data=value[offset:stop],
            next_offset=stop,
            complete=stop == len(value),
        )
    if words == ["stage"]:
        value = _body(o, io)
        if not isinstance(value, str):
            return _reply("invalid_content")
        key = o["object"]
        if key not in s["objects"] or o.get("revision") != s["revision"]:
            return _reply("revision_conflict", current=s["revision"])
        old = s["staged"].get(key, "")
        if int(o["offset"]) != len(old):
            return _reply("offset_conflict", next_offset=len(old))
        accepted = value[:3]
        s["staged"][key] = old + accepted
        s["verified"].pop(key, None)
        return _reply(
            "partial" if len(accepted) < len(value) else "accepted",
            received=len(accepted),
            next_offset=len(old) + len(accepted),
            verified=False,
        )
    if words == ["verify"]:
        key = o["object"]
        if s["staged"].get(key) != s["objects"].get(key) or key not in s["objects"]:
            return _reply(
                "digest_mismatch", object=key, received_length=len(s["staged"].get(key, ""))
            )
        receipt = _digest([s["revision"], key, s["staged"][key]])
        s["verified"][key] = receipt
        return _reply(object=key, revision=s["revision"], receipt=receipt, verified=True)
    if words == ["publish"]:
        key = o["object"]
        if not s["sealed"]:
            return _reply("pin_required")
        if key not in s["verified"] or o.get("receipt") != s["verified"][key]:
            return _reply("receipt_mismatch", object=key)
        s["published"][key] = {
            "data": s["staged"][key],
            "revision": s["revision"],
            "receipt": o["receipt"],
        }
        return _reply(
            "outcome_unknown", object=key, message="Publish persisted; status is authoritative"
        )
    if words == ["status"]:
        return _reply(
            revision=s["revision"],
            received={k: len(v) for k, v in s["staged"].items()},
            verified=s["verified"],
            published=s["published"],
        )
    return _reply("unknown_command")


def restore_check(s):
    if not s["sealed"] or set(s["published"]) != set(s["objects"]):
        return "Pin and publish every object in the current manifest"
    for key, value in s["objects"].items():
        p = s["published"][key]
        if p != {
            "data": value,
            "revision": s["revision"],
            "receipt": _digest([s["revision"], key, value]),
        }:
            return "Every published object needs its own current authenticated receipt"
    return None


RESTORE_DOC = (
    COMMON_DOC
    + """
Goal: publish every authenticated object from one current pinned snapshot.
head -> revision,sealed. pin --revision R flushes pending source corrections,
returns a new immutable revision, and clears old staged bytes/verification
receipts. Pin again with the current revision is safe. manifest list --revision
R [--cursor C] returns object,length,digest. The digest is SHA256 of canonical
JSON encoding of the STRING (UTF8, ensure_ascii=True, separators=(',',':')).
fetch --object O --revision R --offset N returns at most4 characters, data,
next_offset,complete. Complete refers to the fetch suffix, not the restore.
stage --object O --revision R --offset N --file JSON_STRING durably accepts at
most3 characters. Its next_offset is authoritative; partial is not rollback.
N must equal the object's currently received length. Changing staged content
invalidates that object's prior verification receipt. status exposes received
lengths, verified receipts and actually published records.
verify --object O compares all staged bytes against the pinned source and, on
success, returns a receipt bound to revision,object,content. publish --object O
--receipt T requires that object's current receipt; another object's verified
receipt is insufficient even for identical bytes. outcome_unknown is after
publication. Query status or repeat publication. Any chunk sizes/order and
equivalent complete authenticated restore pass; merely received bytes do not.
"""
)


def _restore_config(variant=0):
    return {
        "revision": "snapshot-6",
        "sealed": False,
        "objects": {"alpha": "ABCD12", "beta": "EFGH34", "gamma": "IJKL56"},
        "pending": {"beta": "EFGX34" if variant == 0 else "EFGY34"},
        "staged": {},
        "verified": {},
        "published": {},
        "cache": {"revision": "snapshot-5", "manifest": ["alpha", "beta"], "summary": "received"},
    }


def gold_restore(ws, *, wrong=None, alternate=False):
    head = _ask(ws, "head")
    pinned = _ask(ws, "pin", revision=head["revision"])
    rows = _scan(ws, ("manifest", "list"), revision=pinned["revision"])
    if alternate:
        rows.reverse()
    first_receipt = None
    for row in rows:
        offset = 0
        while offset < row["length"]:
            fetched = _ask(
                ws, "fetch", object=row["object"], revision=pinned["revision"], offset=offset
            )
            value = fetched["data"][:2] if alternate else fetched["data"]
            staged = _put(
                ws,
                ("stage",),
                value,
                object=row["object"],
                revision=pinned["revision"],
                offset=offset,
            )
            offset = fetched["next_offset"] if wrong == "ignore_partial" else staged["next_offset"]
        verified = _ask(ws, "verify", object=row["object"])
        receipt = verified.get("receipt", "not-verified")
        first_receipt = first_receipt or receipt
        _ask(
            ws,
            "publish",
            object=row["object"],
            receipt=first_receipt if wrong == "wrong_receipt" else receipt,
        )
        _ask(ws, "status")


# 479: job identity, received/verified offsets and quota survive lost replies.
def repair_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            quota=s["quota"],
            objects=[
                {"id": k, "length": len(v), "generation": s["generation"][k]}
                for k, v in s["objects"].items()
            ],
        )
    if words == ["jobs", "list"]:
        return _pages(
            [{k: v for k, v in j.items() if k != "data"} for j in s["jobs"].values()],
            o.get("cursor"),
            "jobs:" + s["revision"],
        )
    if words == ["job", "status"]:
        job = s["jobs"][o["job"]]
        return _reply(**copy.deepcopy(job), received=len(job["data"]))
    if words == ["donor", "read"]:
        key = o["object"]
        if o.get("generation") != s["generation"][key]:
            return _reply("generation_conflict", generation=s["generation"][key])
        start = int(o["offset"])
        if not 0 <= start <= len(s["objects"][key]):
            return _reply("invalid_offset")
        return _reply(
            object=key,
            generation=s["generation"][key],
            offset=start,
            data=s["objects"][key][start : start + 5],
        )
    if words == ["job", "start"]:
        key = o["object"]
        if any(j["active"] and j["object"] == key for j in s["jobs"].values()):
            return _reply(
                "already_running",
                jobs=[j["id"] for j in s["jobs"].values() if j["active"] and j["object"] == key],
            )
        if (
            sum(len(s["objects"][j["object"]]) for j in s["jobs"].values() if j["active"])
            + len(s["objects"][key])
            > s["quota"]
        ):
            return _reply("quota_exhausted")
        jobid = "job-" + str(s["serial"])
        s["serial"] += 1
        s["jobs"][jobid] = {
            "id": jobid,
            "object": key,
            "generation": s["generation"][key],
            "data": "",
            "verified_prefix": 0,
            "active": True,
        }
        return _reply("accepted", job=jobid, object=key, published=False)
    if words == ["job", "append"]:
        job = s["jobs"][o["job"]]
        value = _body(o, io)
        if not job["active"] or not isinstance(value, str):
            return _reply("inactive_or_invalid")
        if int(o["offset"]) != len(job["data"]):
            return _reply(
                "offset_conflict", received=len(job["data"]), verified_prefix=job["verified_prefix"]
            )
        job["data"] += value[:3]
        return _reply(
            "outcome_unknown",
            job=job["id"],
            message="Up to3 characters durably received; query this job before resuming",
        )
    if words == ["job", "verify"]:
        job = s["jobs"][o["job"]]
        if (
            not job["active"]
            or job["generation"] != s["generation"][job["object"]]
            or not s["objects"][job["object"]].startswith(job["data"])
        ):
            return _reply("verification_failed")
        job["verified_prefix"] = len(job["data"])
        return _reply(
            job=job["id"],
            object=job["object"],
            verified_prefix=job["verified_prefix"],
            complete=job["data"] == s["objects"][job["object"]],
        )
    if words == ["job", "cancel"]:
        job = s["jobs"][o["job"]]
        job["active"] = False
        return _reply(job=job["id"], quota_released=True, published_unchanged=True)
    if words == ["publish"]:
        job = s["jobs"][o["job"]]
        key = o["object"]
        if (
            job["object"] != key
            or not job["active"]
            or job["generation"] != s["generation"][key]
            or job["data"] != s["objects"][key]
            or job["verified_prefix"] != len(job["data"])
        ):
            return _reply("job_owner_or_verification_mismatch")
        s["published"][key] = {"data": job["data"], "generation": job["generation"]}
        job["active"] = False
        return _reply(
            "outcome_unknown",
            job=job["id"],
            message="Publication persisted and quota released; inspect published status",
        )
    if words == ["status"]:
        return _reply(
            published=s["published"],
            active_jobs=[j["id"] for j in s["jobs"].values() if j["active"]],
        )
    return _reply("unknown_command")


def repair_check(s):
    expected = {
        key: {"data": value, "generation": s["generation"][key]}
        for key, value in s["objects"].items()
    }
    if s["published"] != expected:
        return "Every object must be published at its current generation from a fully verified job"
    if any(j["active"] for j in s["jobs"].values()):
        return "No repair job may retain a quota reservation"
    return None


REPAIR_DOC = (
    COMMON_DOC
    + """
Goal: publish all current objects and leave no active repair jobs/quota holds.
head returns objects:{id,length,generation},revision,quota (total lengths of
active jobs, not bytes already received). jobs list is paginated; old inactive
jobs are historical and can refer to a different object/generation. job status
--job J returns id,object,generation,data,received,verified_prefix,active.
There is already a partially received active repair. Resume it or cancel it;
never assume accepted/green historical receipts mean the target is published.
job start --object O reserves its full length and returns a job id. Only one
active job per object; already_running identifies its actual ids. donor read
--object O --generation G --offset N returns at most5 authoritative characters.
job append --job J --offset N --file JSON_STRING durably receives at most3
characters and returns outcome_unknown without the exact count. Query that
same job's status for the next offset. N must equal received, not verified_prefix.
Bytes received after the last verify are not yet verified. job verify --job J
checks the entire received prefix against the matching generation and updates
verified_prefix. publish --object O --job J requires ownership, current
generation, exact complete content AND verified_prefix==length. It commits,
deactivates the job, releases quota, and may lose its acknowledgement.
status lists actual published objects and active job ids. job cancel --job J
releases quota without changing published objects; a cancelled job cannot be
used as a publication receipt. Jobs do not progress just by being polled.
Any chunk sizes, object order, safe restart/resume or extra reads are accepted.
"""
)


def _repair_config(variant=0):
    objects = {"a": "ABCDEF", "b": "KLMNOP" if variant == 0 else "KLQRST", "c": "UVWXYZ"}
    return {
        "revision": "repairs-4",
        "objects": objects,
        "generation": {"a": "a-3", "b": "b-7", "c": "c-2"},
        "quota": 12,
        "serial": 23,
        "jobs": {
            "job-19": {
                "id": "job-19",
                "object": "b",
                "generation": "b-6",
                "data": "OLDOLD",
                "verified_prefix": 6,
                "active": False,
            },
            "job-21": {
                "id": "job-21",
                "object": "a",
                "generation": "a-3",
                "data": "ABC",
                "verified_prefix": 1,
                "active": True,
            },
            "job-22": {
                "id": "job-22",
                "object": "c",
                "generation": "c-1",
                "data": "OLDOLD",
                "verified_prefix": 6,
                "active": False,
            },
        },
        "published": {},
        "cache": {"summary": "accepted", "job": "job-19", "verified_prefix": 6},
    }


def gold_repair(ws, *, wrong=None, alternate=False):
    head = _ask(ws, "head")
    jobs = _scan(ws, ("jobs", "list"))
    objects = list(reversed(head["objects"])) if alternate else head["objects"]
    for obj in objects:
        active = next(
            (
                j
                for j in jobs
                if j["active"] and j["object"] == obj["id"] and j["generation"] == obj["generation"]
            ),
            None,
        )
        if active and alternate:
            _ask(ws, "job", "cancel", job=active["id"])
            active = None
        if active:
            job = active["id"]
        else:
            started = _ask(ws, "job", "start", object=obj["id"])
            if "job" not in started:
                assert wrong, started
                return
            job = started["job"]
        status = _ask(ws, "job", "status", job=job)
        offset = status["received"]
        while offset < obj["length"]:
            source = _ask(
                ws, "donor", "read", object=obj["id"], generation=obj["generation"], offset=offset
            )
            value = source["data"][:2] if alternate else source["data"]
            response = _put(ws, ("job", "append"), value, job=job, offset=offset)
            status = _ask(ws, "job", "status", job=job)
            offset = offset + len(value) if wrong == "ignore_response" else status["received"]
            if response["status"] == "offset_conflict" and wrong:
                break
        _ask(ws, "job", "verify", job=job)
        _ask(ws, "publish", object=obj["id"], job="job-19" if wrong == "wrong_receipt" else job)
        _ask(ws, "status")


# 480: restating identity groups must undo a tombstoned bridge.
def identity_components(s):
    groups = [{c} for c in s["contacts"]]
    for edge in _latest(s["edges"]).values():
        left = next(g for g in groups if edge["left"] in g)
        right = next(g for g in groups if edge["right"] in g)
        if left is not right:
            left.update(right)
            groups.remove(right)
    return sorted(sorted(g) for g in groups)


def identity_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(revision=s["revision"], sealed=s["sealed"], contacts=s["contacts"])
    if words == ["seal"]:
        if not s["sealed"]:
            s["edges"].extend(s.pop("pending"))
            s["revision"] = "identity-9"
            s["sealed"] = True
            s["candidate"] = None
            s["receipt"] = None
        return _reply(revision=s["revision"], sealed=True)
    if words == ["edges", "list"]:
        if o.get("revision") != s["revision"]:
            return _reply("revision_conflict", current=s["revision"])
        return _pages(s["edges"], o.get("cursor"), "edges:" + s["revision"])
    if words == ["groups", "stage"]:
        groups = _body(o, io)
        if not isinstance(groups, list) or any(
            not isinstance(g, list) or any(not isinstance(v, str) for v in g) for g in groups
        ):
            return _reply("invalid_groups")
        if o.get("revision") != s["revision"]:
            return _reply("revision_conflict", current=s["revision"])
        s["candidate"] = {"revision": s["revision"], "groups": copy.deepcopy(groups)}
        s["receipt"] = None
        return _reply("accepted", candidate=_digest(s["candidate"]), active=False)
    if words == ["groups", "validate"]:
        candidate = s["candidate"]
        if not s["sealed"] or candidate is None:
            return _reply("sealed_candidate_required")
        members = [c for g in candidate["groups"] for c in g]
        if sorted(members) != sorted(s["contacts"]) or any(not g for g in candidate["groups"]):
            return _reply("coverage_mismatch")
        s["receipt"] = _digest(["coverage", candidate])
        return _reply(
            receipt=s["receipt"],
            coverage_valid=True,
            note="Coverage validates each contact once; edge semantics remain your responsibility",
        )
    if words == ["groups", "publish"]:
        if o.get("receipt") != s["receipt"] or s["receipt"] is None:
            return _reply("receipt_mismatch")
        s["active"] = copy.deepcopy(s["candidate"])
        return _reply(active=True)
    if words == ["groups", "status"]:
        return _reply(active=s["active"], candidate=s["candidate"], receipt=s["receipt"])
    return _reply("unknown_command")


def identity_check(s):
    if not s["sealed"] or s["active"] is None or s["active"]["revision"] != s["revision"]:
        return "Publish groups from the sealed current identity cut"
    groups = s["active"]["groups"]
    return (
        None
        if sorted(sorted(g) for g in groups) == identity_components(s)
        else "Groups must be exactly connected components of current nondeleted edges, including singleton contacts"
    )


IDENTITY_DOC = (
    COMMON_DOC
    + """
Goal: publish the exact current identity groups, including singleton contacts.
head returns contacts,revision,sealed. seal flushes pending edge corrections,
changes revision, clears candidates and verification receipts, and freezes the
source. edges list --revision R [--cursor C] is append-only {id,rev,left,right,
deleted?}. Select greatest rev per EDGE id; tombstones remove the edge even if
an older copy arrived later. Undirected connected components of surviving edges
are the groups. Deleting a bridge can split an old group; previous group merges
are not evidence for current edges. cache.json contains obsolete merged groups.
groups stage --revision R --file [[contact,...],...] replaces the candidate and
invalidates its old receipt. It does not activate anything. groups validate
checks only that each current contact appears exactly once and groups are
nonempty; coverage_valid=true says NOTHING about whether the grouping follows
the edges. It returns a receipt bound to the full candidate and source revision.
groups publish --receipt T activates that current candidate; changing groups
after validation requires revalidation. groups status is authoritative.
Any group/member order, grouping algorithm and safe observation pattern pass
if the published partition is correct. Do not treat green coverage as semantic
validation or reuse a receipt from an earlier grouping.
"""
)


def _identity_config(variant=0):
    contacts = [f"contact-{i}" for i in range(10)]
    pairs = [(0, 1), (1, 2), (2, 3), (3, 4), (5, 6), (6, 7), (7, 8)]
    edges = [
        {"id": f"edge-{i}", "rev": 1, "left": contacts[a], "right": contacts[b]}
        for i, (a, b) in enumerate(pairs)
    ]
    pending = [
        {**edges[2], "rev": 2, "deleted": True},
        {**edges[5], "rev": 2, "deleted": variant == 0},
        copy.deepcopy(edges[2]),
    ]
    return {
        "revision": "identity-8",
        "sealed": False,
        "contacts": contacts,
        "edges": edges,
        "pending": pending,
        "candidate": None,
        "receipt": None,
        "active": None,
        "cache": {"groups": [contacts[:5], contacts[5:9], contacts[9:]]},
    }


def gold_identity(ws, *, wrong=None, alternate=False):
    meta = _ask(ws, "head")
    cut = _ask(ws, "seal")
    rows = _scan(ws, ("edges", "list"), revision=cut["revision"])
    latest = {}
    for row in rows:
        if (
            wrong == "last_arrival"
            or row["id"] not in latest
            or row["rev"] > latest[row["id"]]["rev"]
        ):
            latest[row["id"]] = row
    groups = [{c} for c in meta["contacts"]]
    for edge in latest.values():
        if edge.get("deleted"):
            continue
        a = next(g for g in groups if edge["left"] in g)
        b = next(g for g in groups if edge["right"] in g)
        if a is not b:
            a.update(b)
            groups.remove(b)
    result = [sorted(g, reverse=alternate) for g in groups]
    if alternate:
        result.reverse()
    previous = None
    if wrong in {"green_summary", "stale_receipt"}:
        _put(ws, ("groups", "stage"), [meta["contacts"]], revision=cut["revision"])
        previous = _ask(ws, "groups", "validate")
    if wrong != "green_summary":
        _put(ws, ("groups", "stage"), result, revision=cut["revision"])
        receipt = _ask(ws, "groups", "validate")
    else:
        receipt = previous
    _ask(
        ws,
        "groups",
        "publish",
        receipt=previous["receipt"] if wrong == "stale_receipt" else receipt["receipt"],
    )
    _ask(ws, "groups", "status")


# 481: deletion fanout crosses current aliases; CAS batches can partly apply.
def privacy_flush(s):
    if not s["sealed"]:
        s["aliases"].extend(s.pop("pending_aliases"))
        s["revision"] = "aliases-8"
        s["sealed"] = True
        s["closed"].clear()


def privacy_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            sealed=s["sealed"],
            subjects=s["subjects"],
            fields=["email", "phone"],
        )
    if words == ["aliases", "seal"]:
        privacy_flush(s)
        return _reply(revision=s["revision"], sealed=True)
    if words == ["aliases", "list"]:
        if o.get("revision") != s["revision"]:
            return _reply("revision_conflict", current=s["revision"])
        return _pages(s["aliases"], o.get("cursor"), "aliases:" + s["revision"])
    if words == ["record", "get"]:
        return _reply(record=copy.deepcopy(s["records"][o["id"]]))
    if words == ["redact"]:
        rows = _body(o, io)
        applied = []
        rejected = []
        for row in rows:
            record = s["records"][row["id"]]
            # A queued application edit is flushed by the first write attempt.
            pending = s["pending_edits"].pop(row["id"], None)
            if pending:
                record.update(pending)
                record["rev"] += 1
            if row["if_rev"] != record["rev"] or type(row["if_rev"]) is not int:
                rejected.append(
                    {"id": row["id"], "reason": "revision_conflict", "current_rev": record["rev"]}
                )
                continue
            if set(row["fields"]) != {"email", "phone"}:
                rejected.append({"id": row["id"], "reason": "exact_fields_required"})
                continue
            if record["email"] or record["phone"]:
                record["email"] = record["phone"] = ""
                record["rev"] += 1
            receipt = _digest([s["revision"], row["id"], record["rev"], "redacted"])
            s["redaction_receipts"][receipt] = {
                "id": row["id"],
                "revision": s["revision"],
                "rev": record["rev"],
            }
            applied.append({"id": row["id"], "rev": record["rev"], "receipt": receipt})
        return _reply(
            "partial" if rejected else "ok",
            applied=applied,
            rejected=rejected,
            note="Applied items are durable; rejected items must be reconciled individually",
        )
    if words == ["request", "close"]:
        if not s["sealed"]:
            privacy_flush(s)
            return _reply("aliases_changed", revision=s["revision"])
        subject = o["subject"]
        receipts = _body(o, io)
        target = {a["record"] for a in s["aliases"] if a["subject"] == subject}
        actual = {}
        for token in receipts:
            value = s["redaction_receipts"].get(token)
            if not value or value["revision"] != s["revision"]:
                return _reply("receipt_mismatch")
            record = s["records"][value["id"]]
            if record["rev"] != value["rev"] or record["email"] or record["phone"]:
                return _reply("receipt_stale")
            actual[value["id"]] = value
        if set(actual) != target or subject not in s["subjects"]:
            return _reply("receipt_owner_or_coverage_mismatch")
        s["closed"][subject] = sorted(target)
        return _reply(closed=True, subject=subject)
    if words == ["status"]:
        return _reply(closed=s["closed"], revision=s["revision"])
    return _reply("unknown_command")


def privacy_check(s):
    if not s["sealed"] or set(s["closed"]) != set(s["subjects"]):
        return "Close all requested subjects after covering current aliases"
    targets = {a["record"] for a in s["aliases"] if a["subject"] in s["subjects"]}
    for key, record in s["records"].items():
        if key in targets:
            if record["email"] or record["phone"]:
                return "PII remains on a current or archived alias"
            if record["note"] != s["original"][key]["note"]:
                return "Keep non-PII record fields intact"
        elif record != s["original"][key]:
            return "Unrequested records must remain byte-for-byte unchanged"
    return None


PRIVACY_DOC = (
    COMMON_DOC
    + """
Goal: erase email and phone from every current AND archived alias of the subjects
returned by head, close their requests, and preserve all unrequested records and
every note. head returns subjects,revision,sealed,fields. aliases seal flushes
pending alias additions and freezes the alias cut; aliases list --revision R
[--cursor C] returns subject,record,archived (archived is NOT an exclusion).
record get --id I returns the entire current record with rev,email,phone,note.
redact --file [{id,if_rev,fields:["email","phone"]},...] processes each item
independently. A queued ordinary application edit can flush immediately before
that item's first write: its rev increases and the stale CAS is rejected.
The reply contains applied and rejected item arrays. status=partial/exit0 means
only applied items changed; those effects are durable. Read each rejected
record again and retry its new revision. No reads, sleeps or unrelated writes
flush pending edits. Redacting an already empty current record is safe.
Every applied item returns a receipt bound to alias revision,record id,record
revision. request close --subject S --file [receipt,...] requires exactly all
current alias records of S, no other subject's receipts. Closing before the
alias barrier performs it and returns aliases_changed; previously issued
receipts then refer to the old alias revision. Closing changes no PII.
status returns closed requests, not a global assertion that all PII is gone.
Any batching, safe retry order and extra reads with the same final outcome pass.
"""
)


def _privacy_config(variant=0):
    records = {
        f"record-{i}": {
            "id": f"record-{i}",
            "rev": 1,
            "email": f"person{i}@example.invalid",
            "phone": f"55500{i}",
            "note": "retain-audit-" + str(i),
        }
        for i in range(9)
    }
    aliases = [
        {"subject": f"subject-{i // 2}", "record": f"record-{i}", "archived": i % 2 == 1}
        for i in range(6)
    ]
    return {
        "revision": "aliases-7",
        "sealed": False,
        "subjects": [f"subject-{i}" for i in range(3)],
        "records": records,
        "original": copy.deepcopy(records),
        "aliases": aliases,
        "pending_aliases": [
            {
                "subject": "subject-1",
                "record": "record-6" if variant == 0 else "record-7",
                "archived": True,
            }
        ],
        "pending_edits": {"record-3": {"phone": "555-corrected"}},
        "redaction_receipts": {},
        "closed": {},
        "cache": {"aliases": copy.deepcopy(aliases), "summary": "three current primary records"},
    }


def gold_privacy(ws, *, wrong=None, alternate=False):
    meta = _ask(ws, "head")
    cut = _ask(ws, "aliases", "seal")
    aliases = _scan(ws, ("aliases", "list"), revision=cut["revision"])
    if alternate:
        aliases.reverse()
    if wrong == "ignore_archived":
        aliases = [a for a in aliases if not a["archived"]]
    receipts = {subject: [] for subject in meta["subjects"]}
    for alias in aliases:
        row = _ask(ws, "record", "get", id=alias["record"])["record"]
        response = _put(
            ws, ("redact",), [{"id": row["id"], "if_rev": row["rev"], "fields": meta["fields"]}]
        )
        if response["rejected"] and wrong != "ignore_partial":
            row = _ask(ws, "record", "get", id=alias["record"])["record"]
            response = _put(
                ws, ("redact",), [{"id": row["id"], "if_rev": row["rev"], "fields": meta["fields"]}]
            )
        receipts[alias["subject"]].extend(r["receipt"] for r in response["applied"])
    for subject in meta["subjects"]:
        _put(
            ws,
            ("request", "close"),
            receipts[meta["subjects"][0]] if wrong == "wrong_receipt" else receipts[subject],
            subject=subject,
        )
        _ask(ws, "status")


# 482: asynchronous reports bind a source cut, region, and completed job.
def latency_expected(s):
    attempts = list(_latest(s["attempts"]).values())
    result = {}
    for region in s["regions"]:
        durations = []
        pending = 0
        for req in _latest(s["requests"]).values():
            if req["region"] != region or req["cancelled"]:
                continue
            finishes = [
                a["finish"]
                for a in attempts
                if a["request"] == req["id"]
                and a["outcome"] == "success"
                and a["finish"] >= req["start"]
            ]
            if finishes:
                durations.append(min(finishes) - req["start"])
            else:
                pending += 1
        result[region] = {
            "completed": len(durations),
            "pending": pending,
            "latencies": sorted(durations),
        }
    return result


def latency_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(revision=s["revision"], regions=s["regions"], sealed=s["sealed"])
    if words == ["seal"]:
        if not s["sealed"]:
            s["requests"].extend(s.pop("pending_requests"))
            s["attempts"].extend(s.pop("pending_attempts"))
            s["revision"] = "latency-6"
            s["sealed"] = True
        return _reply(revision=s["revision"], sealed=True)
    if words in (["requests", "list"], ["attempts", "list"]):
        if o.get("revision") != s["revision"]:
            return _reply("revision_conflict", current=s["revision"])
        return _pages(s[words[0]], o.get("cursor"), words[0] + ":" + s["revision"])
    if words == ["report", "start"]:
        if (
            not s["sealed"]
            or o.get("revision") != s["revision"]
            or o.get("region") not in s["regions"]
        ):
            return _reply("sealed_current_source_required")
        payload = _body(o, io)
        if (
            set(payload) != {"completed", "pending", "latencies"}
            or any(type(payload[k]) is not int for k in ("completed", "pending"))
            or not isinstance(payload["latencies"], list)
            or any(type(v) is not int for v in payload["latencies"])
        ):
            return _reply("invalid_metrics")
        body = {"region": o["region"], "revision": o["revision"], "metrics": payload}
        prior = _prior(s, o["key"], body)
        if prior:
            return prior, 0
        job = "report-" + str(s["serial"])
        s["serial"] += 1
        s["jobs"][job] = {"id": job, **body, "phase": "queued"}
        result = {
            "status": "accepted",
            "job": job,
            "region": o["region"],
            "phase": "queued",
            "published": False,
        }
        _remember(s, o["key"], body, result)
        return result, 0
    if words == ["report", "advance"]:
        job = s["jobs"][o["job"]]
        job["phase"] = {"queued": "built", "built": "ready", "ready": "ready"}[job["phase"]]
        return _reply(
            "partial" if job["phase"] == "built" else "ok",
            job=job["id"],
            phase=job["phase"],
            note="Readiness is packaging completeness, not metric correctness",
        )
    if words == ["report", "status"]:
        return _reply(job=s["jobs"][o["job"]])
    if words == ["publish"]:
        job = s["jobs"][o["job"]]
        if (
            job["region"] != o["region"]
            or job["revision"] != s["revision"]
            or job["phase"] != "ready"
        ):
            return _reply("job_owner_or_readiness_mismatch")
        s["published"][o["region"]] = copy.deepcopy(job)
        return _reply(published=True, region=o["region"])
    if words == ["status"]:
        return _reply(published=s["published"])
    return _reply("unknown_command")


def latency_check(s):
    expected = latency_expected(s)
    if not s["sealed"] or set(s["published"]) != set(s["regions"]):
        return "Publish a ready current job for every region"
    for region, metrics in expected.items():
        actual = copy.deepcopy(s["published"][region]["metrics"])
        actual["latencies"].sort()
        if actual != metrics:
            return "Request latency uses the first successful completion and latest uncancelled request/attempt revisions"
    return None


LATENCY_DOC = (
    COMMON_DOC
    + """
Goal: publish per-region request latency metrics from the current sealed cut.
head -> revision,regions,sealed. seal flushes late corrections to BOTH requests
and attempts and returns the final revision. requests list --revision R and
attempts list --revision R are separately paginated append-only revision logs;
greatest rev per id wins and deleted tombstones remove that record, regardless
of arrival order. Request rows have id,rev,region,start,cancelled. Attempt rows
have id,rev,request,finish,outcome=success|failure. Omit cancelled requests.
For each remaining request choose minimum finish among its CURRENT successful
attempts with finish>=request.start. One request contributes one latency,
finish-start, even if multiple attempts succeeded. Without one, count pending.
Each region's metrics={completed:integer,pending:integer,latencies:[integer,...]}.
report start --region R --revision V --key K --file METRICS queues a packaging
job, returning job and phase=queued. This does not validate your arithmetic or
publish anything. report advance --job J deterministically changes queued→built
→ready; a built/partial response still needs advance. Status reads and sleeps
do not advance jobs. report status --job J returns its source,region,metrics,
phase. publish --region R --job J requires a READY job for THAT region and the
current revision. status returns actually published jobs. Readiness only checks
packaging; the grader checks the metrics. Any region order, safe replay and
latency-array ordering are accepted. Cached dashboards are not current facts.
"""
)


def _latency_config(variant=0):
    requests = [
        {
            "id": f"request-{i}",
            "rev": 1,
            "region": f"region-{i % 3}",
            "start": 10 * i,
            "cancelled": False,
        }
        for i in range(6)
    ]
    attempts = [
        {
            "id": f"attempt-{i}",
            "rev": 1,
            "request": r["id"],
            "finish": r["start"] + 5 + i,
            "outcome": "success",
        }
        for i, r in enumerate(requests)
    ]
    attempts += [
        {
            "id": "duplicate-success",
            "rev": 1,
            "request": "request-1",
            "finish": 30,
            "outcome": "success",
        },
        {"id": "failed-fast", "rev": 1, "request": "request-2", "finish": 21, "outcome": "failure"},
    ]
    return {
        "revision": "latency-5",
        "sealed": False,
        "regions": [f"region-{i}" for i in range(3)],
        "requests": requests,
        "attempts": attempts,
        "pending_requests": [
            {**requests[4], "rev": 2, "cancelled": True},
            copy.deepcopy(requests[4]),
        ],
        "pending_attempts": [
            {**attempts[2], "rev": 2, "finish": 33 if variant == 0 else 37},
            {**attempts[5], "rev": 2, "deleted": True},
            copy.deepcopy(attempts[2]),
        ],
        "jobs": {},
        "published": {},
        "receipts": {},
        "serial": 8,
        "cache": {"summary": "all requests completed", "request_count": 6},
    }


def gold_latency(ws, *, wrong=None, alternate=False):
    meta = _ask(ws, "head")
    cut = _ask(ws, "seal")
    requests = _scan(ws, ("requests", "list"), revision=cut["revision"])
    attempts = _scan(ws, ("attempts", "list"), revision=cut["revision"])
    latest_requests = {}
    latest_attempts = {}
    for rows, target in ((requests, latest_requests), (attempts, latest_attempts)):
        for row in rows:
            if row["id"] not in target or row["rev"] > target[row["id"]]["rev"]:
                target[row["id"]] = row
    regions = list(reversed(meta["regions"])) if alternate else meta["regions"]
    for region in regions:
        metrics = {"completed": 0, "pending": 0, "latencies": []}
        for req in latest_requests.values():
            if req["region"] != region or req["cancelled"] or req.get("deleted"):
                continue
            finishes = [
                a["finish"]
                for a in latest_attempts.values()
                if a["request"] == req["id"]
                and a["outcome"] == "success"
                and not a.get("deleted")
                and a["finish"] >= req["start"]
            ]
            if finishes:
                values = finishes if wrong == "count_attempts" else [min(finishes)]
                metrics["completed"] += len(values)
                metrics["latencies"] += [t - req["start"] for t in values]
            else:
                metrics["pending"] += 1
        job = _put(
            ws,
            ("report", "start"),
            metrics,
            region=region,
            revision=cut["revision"],
            key="region-" + region,
        )["job"]
        if wrong != "trust_accepted":
            phase = "queued"
            while phase != "ready":
                _ask(ws, "report", "advance", job=job)
                phase = _ask(ws, "report", "status", job=job)["job"]["phase"]
        _ask(ws, "publish", region=region, job=job)
    _ask(ws, "status")


# 483: desired routing and observed routing diverge until a safe plan completes.
def relief_loads(s, routes):
    result = {node: 0 for node in s["capacity"]}
    for key, node in routes.items():
        result[node] += s["traffic"][key]
    return result


def relief_simulate(s, moves):
    routes = s["observed"].copy()
    for move in moves:
        key, node = move["route"], move["to"]
        if (
            key not in routes
            or node not in s["capacity"]
            or key in s["protected"]
            and node != s["protected"][key]
        ):
            return None
        if routes[key] != node:
            load = relief_loads(s, routes)
            if load[node] + s["traffic"][key] > s["capacity"][node]:
                return None
        routes[key] = node
    return routes


def relief_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(revision=s["revision"], sealed=s["sealed"], protected=s["protected"])
    if words == ["health", "seal"]:
        if not s["sealed"]:
            s["capacity"].update(s.pop("pending_capacity"))
            s["revision"] = "health-9"
            s["sealed"] = True
            s["candidate"] = s["receipt"] = None
        return _reply(revision=s["revision"], capacity=s["capacity"], sealed=True)
    if words == ["routes", "list"]:
        return _pages(
            [
                {"route": k, "load": v, "observed": s["observed"][k], "desired": s["desired"][k]}
                for k, v in s["traffic"].items()
            ],
            o.get("cursor"),
            "routes:" + s["revision"],
        )
    if words == ["status"]:
        return _reply(
            observed=s["observed"],
            desired=s["desired"],
            loads=relief_loads(s, s["observed"]),
            capacity=s["capacity"],
            job=s["job"],
        )
    if words == ["plan", "stage"]:
        moves = _body(o, io)
        if not isinstance(moves, list) or any(
            not isinstance(m, dict) or set(m) != {"route", "to"} for m in moves
        ):
            return _reply("invalid_plan")
        s["candidate"] = {
            "revision": o["revision"],
            "moves": copy.deepcopy(moves),
            "base": s["observed"].copy(),
        }
        s["receipt"] = None
        return _reply("accepted", active=False)
    if words == ["plan", "canary"]:
        candidate = s["candidate"]
        if (
            not s["sealed"]
            or not candidate
            or candidate["revision"] != s["revision"]
            or candidate["base"] != s["observed"]
        ):
            return _reply("dependency_changed", revision=s["revision"])
        target = relief_simulate(s, candidate["moves"])
        if target is None:
            return _reply("unsafe_intermediate_move")
        s["receipt"] = _digest(candidate)
        return _reply(
            receipt=s["receipt"],
            safe_steps=True,
            target=target,
            note="Safe steps do not assert that all overloads are resolved",
        )
    if words == ["plan", "apply"]:
        if (
            not s["receipt"]
            or o.get("receipt") != s["receipt"]
            or s["candidate"]["base"] != s["observed"]
        ):
            return _reply("receipt_mismatch")
        target = relief_simulate(s, s["candidate"]["moves"])
        if target is None:
            return _reply("unsafe_intermediate_move")
        s["desired"] = target
        s["job"] = {
            "moves": copy.deepcopy(s["candidate"]["moves"]),
            "next": 0,
            "done": not s["candidate"]["moves"],
        }
        s["receipt"] = None
        return _reply("accepted", desired=s["desired"], observed_unchanged=True)
    if words == ["job", "advance"]:
        job = s["job"]
        if not job:
            return _reply("no_job")
        if not job["done"]:
            move = job["moves"][job["next"]]
            target = relief_simulate(s, [move])
            if target is None:
                return _reply("unsafe_intermediate_move")
            s["observed"] = target
            job["next"] += 1
            job["done"] = job["next"] == len(job["moves"])
            return _reply(
                "outcome_unknown",
                message="One movement may have committed; inspect job.next and observed routes",
            )
        return _reply(done=True)
    return _reply("unknown_command")


def relief_check(s):
    if not s["sealed"] or s["desired"] != s["observed"] or not s["job"] or not s["job"]["done"]:
        return "The complete plan must reach observed routing, not just desired routing"
    if any(s["observed"][key] != node for key, node in s["protected"].items()):
        return "Protected routes must remain on their designated nodes"
    return (
        None
        if all(load <= s["capacity"][node] for node, load in relief_loads(s, s["observed"]).items())
        else "All observed overloads must be relieved"
    )


RELIEF_DOC = (
    COMMON_DOC
    + """
Goal: finish a safe routing migration that removes every observed overload,
keeps protected routes fixed, conserves each route's entire load, and leaves
desired==observed. head returns revision,sealed,protected route→fixed node.
health seal flushes pending capacity corrections, returns current capacity and
revision, and invalidates staged plans/receipts. routes list [--cursor C] gives
route,load,observed,desired. status includes observed,desired,loads,capacity,job.
plan stage --revision R --file [{route,to},...] sets a candidate sequence and
invalidates its previous canary receipt. plan canary checks the pinned source,
observed base, protected routes and EVERY intermediate move. A move to a new
node requires its current load plus the full moved route to fit capacity.
Initially overloaded source nodes are allowed; the plan must ultimately fix
all overloads. Safe steps alone do not certify that this goal was achieved.
canary returns a receipt bound to the exact candidate, source and observed base.
plan apply --receipt T sets desired and starts a job; observed is unchanged.
job advance performs at most one next movement and returns outcome_unknown
after its durable effect. status exposes job.next,job.done and observed routes;
read it before deciding whether another advancement is needed. Calling advance
after done is safe. Reads/sleeps never move traffic. A staged edit invalidates
the receipt, and an applied step invalidates a receipt tied to the old base.
Any safe sequence and final feasible assignment pass, including extra safe
no-op moves; a particular route order or exact trace is not required.
"""
)


def _relief_config(variant=0):
    observed = {"protected": "a", "blue": "a", "green": "a", "amber": "b"}
    return {
        "revision": "health-8",
        "sealed": False,
        "capacity": {"a": 12, "b": 7, "c": 6},
        "pending_capacity": {"a": 4, "b": 6 if variant == 0 else 7, "c": 6 if variant == 0 else 3},
        "protected": {"protected": "a"},
        "traffic": {"protected": 4, "blue": 4, "green": 3, "amber": 3},
        "observed": observed,
        "desired": observed.copy(),
        "candidate": None,
        "receipt": None,
        "job": None,
        "cache": {"capacity": {"a": 12, "b": 7, "c": 6}, "summary": "healthy"},
    }


def gold_relief(ws, *, wrong=None, alternate=False):
    meta = _ask(ws, "head")
    health = _ask(ws, "health", "seal")
    rows = _scan(ws, ("routes", "list"))
    current = {r["route"]: r["observed"] for r in rows}
    sizes = {r["route"]: r["load"] for r in rows}
    nodes = sorted(health["capacity"], reverse=alternate)
    # Tiny public-domain search over safe movements, no hidden target assignment.
    queue = [(current, [])]
    seen = {tuple(sorted(current.items()))}
    moves = None
    while queue:
        routes, path = queue.pop(0)
        load = {node: sum(sizes[r] for r, dest in routes.items() if dest == node) for node in nodes}
        if all(load[node] <= health["capacity"][node] for node in nodes):
            moves = path
            break
        for route in sorted(sizes, reverse=alternate):
            if route in meta["protected"]:
                continue
            for node in nodes:
                if node == routes[route] or load[node] + sizes[route] > health["capacity"][node]:
                    continue
                next_routes = {**routes, route: node}
                key = tuple(sorted(next_routes.items()))
                if key not in seen:
                    seen.add(key)
                    queue.append((next_routes, path + [{"route": route, "to": node}]))
    assert moves is not None
    _put(ws, ("plan", "stage"), moves, revision=health["revision"])
    canary = _ask(ws, "plan", "canary")
    if wrong == "stale_receipt":
        _put(
            ws,
            ("plan", "stage"),
            moves + [{"route": "protected", "to": meta["protected"]["protected"]}],
            revision=health["revision"],
        )
    _ask(ws, "plan", "apply", receipt=canary["receipt"])
    status = _ask(ws, "status")
    if wrong != "trust_desired":
        while status["job"] and not status["job"]["done"]:
            _ask(ws, "job", "advance")
            status = _ask(ws, "status")


# 484: compilation/testing receipts bind a complete immutable source digest.
def macro_eval(s, module, expr, value, stack=()):
    if len(stack) > 20 or not isinstance(expr, dict) or len(expr) != 1:
        raise ValueError("invalid/deep expression")
    if "text" in expr and isinstance(expr["text"], str):
        return expr["text"]
    if "input" in expr and expr["input"] is True:
        return value
    if "concat" in expr and isinstance(expr["concat"], list):
        return "".join(
            macro_eval(s, module, child, value, stack + ("concat",)) for child in expr["concat"]
        )
    if "ref" in expr:
        name = expr["ref"]
        marker = ("ref", name)
        if marker in stack:
            raise ValueError("reference cycle")
        return macro_eval(s, module, module["defs"][name], value, stack + (marker,))
    if "call" in expr:
        call = expr["call"]
        macro = next(
            (
                m
                for m in s["registry"]
                if m["name"] == call["name"] and m["version"] == call["version"] and m["current"]
            ),
            None,
        )
        if macro is None:
            raise ValueError("missing current macro version")
        return (
            macro["prefix"]
            + macro_eval(s, module, call["arg"], value, stack + ("call",))
            + macro["suffix"]
        )
    raise ValueError("invalid expression")


def macro_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(revision=s["revision"], sealed=s["sealed"], modules=s["requirements"])
    if words == ["registry", "seal"]:
        if not s["sealed"]:
            for row in s["registry"]:
                if row["name"] in s["pending"] and row["current"]:
                    row.update(s["pending"][row["name"]])
            s["pending"].clear()
            s["revision"] = "macros-7"
            s["sealed"] = True
            s["compiled"].clear()
            s["tested"].clear()
        return _reply(revision=s["revision"], sealed=True)
    if words == ["registry", "list"]:
        return _pages(s["registry"], o.get("cursor"), "registry:" + s["revision"])
    if words == ["names", "list"]:
        return _pages(s["names"], o.get("cursor"), "names-3")
    if words == ["module", "stage"]:
        module = _body(o, io)
        if (
            o["module"] not in s["requirements"]
            or set(module) != {"defs", "entry"}
            or not isinstance(module["defs"], dict)
        ):
            return _reply("invalid_module")
        s["drafts"][o["module"]] = copy.deepcopy(module)
        s["compiled"].pop(o["module"], None)
        s["tested"].pop(o["module"], None)
        return _reply("accepted", hash=_digest(module), deployed=False)
    if words == ["module", "compile"]:
        key = o["module"]
        module = s["drafts"][key]
        if not s["sealed"]:
            return _reply("registry_not_sealed")
        try:
            for name in module["defs"]:
                macro_eval(s, module, {"ref": name}, "probe")
            macro_eval(s, module, module["entry"], "probe")
        except (ValueError, KeyError, StopIteration, TypeError):
            return _reply(
                "compile_failed",
                message="Every definition, including unused definitions, must be valid and acyclic with current macro references",
            )
        receipt = _digest([key, s["revision"], module])
        s["compiled"][key] = receipt
        return _reply(receipt=receipt, compiled=True, deployed=False)
    if words == ["module", "test"]:
        key = o["module"]
        if s["compiled"].get(key) != o.get("receipt") or not o.get("receipt"):
            return _reply("receipt_mismatch")
        requirement = s["requirements"][key]
        macro = next(m for m in s["registry"] if m["name"] == requirement["macro"] and m["current"])
        value = s["names"][0]
        expected = macro["prefix"] + value + macro["suffix"] + requirement["tail"]
        if macro_eval(s, s["drafts"][key], s["drafts"][key]["entry"], value) != expected:
            return _reply("sample_failed")
        s["tested"][key] = o["receipt"]
        return _reply(
            sample_passed=True,
            tested_name=value,
            note="One sample is not complete behavioral coverage",
        )
    if words == ["module", "deploy"]:
        key = o["module"]
        if (
            not o.get("receipt")
            or s["compiled"].get(key) != o["receipt"]
            or s["tested"].get(key) != o["receipt"]
        ):
            return _reply("receipt_mismatch")
        s["deployed"][key] = {
            "revision": s["revision"],
            "module": copy.deepcopy(s["drafts"][key]),
            "receipt": o["receipt"],
        }
        return _reply("outcome_unknown", module=key, message="Deployment committed; inspect status")
    if words == ["status"]:
        return _reply(deployed=s["deployed"], compiled=s["compiled"], tested=s["tested"])
    return _reply("unknown_command")


def macro_check(s):
    if not s["sealed"] or set(s["deployed"]) != set(s["requirements"]):
        return "Deploy every required module against the sealed registry"
    for key, requirement in s["requirements"].items():
        deployed = s["deployed"][key]
        if deployed["revision"] != s["revision"]:
            return "Deployed modules must use the current compiler/registry cut"
        macro = next(m for m in s["registry"] if m["name"] == requirement["macro"] and m["current"])
        module = deployed["module"]
        for value in s["names"]:
            expected = macro["prefix"] + value + macro["suffix"] + requirement["tail"]
            if macro_eval(s, module, module["entry"], value) != expected:
                return "Every listed input name must produce the required current formatting"
    return None


MACRO_DOC = (
    COMMON_DOC
    + """
Goal: deploy every module named by head.modules. For each listed input name its
output must be current_macro.prefix + name + current_macro.suffix + module.tail.
head returns each module's macro name and tail. registry seal flushes pending
current-version/prefix/suffix corrections, freezes the registry and invalidates old compiled/
tested receipts. registry list is paginated and includes retired versions;
only rows with current=true may be called. names list is separately paginated.
module stage --module M --file {defs:{name:EXPR,...},entry:EXPR} stages, never
deploys. EXPR is exactly one of {text:STRING}, {input:true}, {concat:[EXPR,...]},
{ref:NAME}, {call:{name:MACRO,version:VERSION,arg:EXPR}}. concat concatenates;
input is the current input name; ref evaluates a definition; call wraps its
argument with that exact current macro's prefix/suffix. No implicit casts.
Maximum expression/reference expansion depth is20. All definitions, even unused
ones, must be valid, acyclic, and reference existing current macro versions.
module compile --module M checks that whole source and returns a receipt bound
to module id,full source,registry revision. ANY stage invalidates old compile
and test receipts. module test --module M --receipt T checks only the first
listed name; a green sample is not coverage of all public names. module deploy
--module M --receipt T requires the same current compiled AND tested receipt;
it may return outcome_unknown after commit. status lists actual deployments.
Equivalent programs, including inlining literal prefix/suffix and any valid
definition names, pass. Code shape, request order and exact traces are not graded.
"""
)


def _macro_config(variant=0):
    return {
        "revision": "macros-6",
        "sealed": False,
        "registry": [
            {"name": n, "version": v, "current": v == "v2", "prefix": prefix, "suffix": suffix}
            for n, prefix, suffix in (("badge", "[", "]"), ("quote", "<", ">"))
            for v in ("v1", "v2")
        ],
        "pending": {
            "badge": {
                "version": "v2" if variant == 0 else "v3",
                "prefix": "[[" if variant == 0 else "{{",
                "suffix": "]]" if variant == 0 else "}}",
            }
        },
        "requirements": {
            "welcome": {"macro": "badge", "tail": " hello"},
            "receipt": {"macro": "quote", "tail": " saved"},
            "archive": {"macro": "badge", "tail": " archived"},
        },
        "names": ["Ada", "Bo", "", "Cy-D"],
        "drafts": {},
        "compiled": {},
        "tested": {},
        "deployed": {},
        "cache": {"badge": {"version": "v1", "prefix": "[", "suffix": "]"}, "summary": "compiled"},
    }


def gold_macro(ws, *, wrong=None, alternate=False):
    meta = _ask(ws, "head")
    _ask(ws, "registry", "seal")
    rows = _scan(ws, ("registry", "list"))
    names = _scan(ws, ("names", "list"))
    macros = {r["name"]: r for r in rows if r["current"]}
    for key, req in (
        reversed(list(meta["modules"].items())) if alternate else meta["modules"].items()
    ):
        macro = macros[req["macro"]]
        if alternate:
            expression = {
                "concat": [
                    {"text": macro["prefix"]},
                    {"input": True},
                    {"text": macro["suffix"] + req["tail"]},
                ]
            }
        else:
            expression = {
                "concat": [
                    {
                        "call": {
                            "name": macro["name"],
                            "version": macro["version"],
                            "arg": {"input": True},
                        }
                    },
                    {"text": req["tail"]},
                ]
            }
        if wrong == "sample_only":
            expression = {"text": macro["prefix"] + names[0] + macro["suffix"] + req["tail"]}
        module = {"defs": {"format": expression}, "entry": {"ref": "format"}}
        _put(ws, ("module", "stage"), module, module=key)
        compiled = _ask(ws, "module", "compile", module=key)
        _ask(ws, "module", "test", module=key, receipt=compiled["receipt"])
        if wrong == "stale_receipt":
            _put(ws, ("module", "stage"), {"defs": {}, "entry": expression}, module=key)
        _ask(ws, "module", "deploy", module=key, receipt=compiled["receipt"])
        _ask(ws, "status")


# 485: immutable versions and per-operation receipts, not mutable HEAD aliases.
def document_apply(data, op, versions):
    if not isinstance(op, dict) or type(op.get("at")) is not int or not 0 <= op["at"] <= len(data):
        raise ValueError("invalid destination")
    at = op["at"]
    if (
        op["kind"] == "insert"
        and isinstance(op["lines"], list)
        and all(isinstance(x, str) for x in op["lines"])
    ):
        return data[:at] + op["lines"] + data[at:]
    if op["kind"] == "delete" and type(op["count"]) is int and 0 <= op["count"] <= len(data) - at:
        return data[:at] + data[at + op["count"] :]
    if op["kind"] == "copy":
        source = versions[op["source"]]
        if (
            type(op["start"]) is int
            and type(op["stop"]) is int
            and 0 <= op["start"] <= op["stop"] <= len(source)
        ):
            return data[:at] + source[op["start"] : op["stop"]] + data[at:]
    raise ValueError("invalid operation")


def document_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            sealed=s["sealed"],
            heads=s["heads"],
            protected=s["protected"],
            targets=s["targets"],
        )
    if words == ["pin"]:
        if not s["sealed"]:
            s["heads"].update(s.pop("pending_heads"))
            s["revision"] = "documents-6"
            s["sealed"] = True
            s["pinned"] = {key: s["heads"][key] for key in s["protected"]}
        return _reply(revision=s["revision"], pinned=s["pinned"], sealed=True)
    if words == ["read"]:
        key = o["version"]
        rows = [{"index": i, "text": text} for i, text in enumerate(s["versions"][key])]
        return _pages(rows, o.get("cursor"), "version:" + key)
    if words == ["branch"]:
        body = {"doc": o["doc"], "source": o["source"], "revision": o["revision"]}
        prior = _prior(s, o["key"], body)
        if prior:
            return prior, 0
        if not s["sealed"] or o["revision"] != s["revision"] or o["source"] not in s["versions"]:
            return _reply("pin_or_version_mismatch")
        if o["doc"] in s["protected"]:
            s["violations"].append("protected_head_changed")
        s["heads"][o["doc"]] = o["source"]
        result = {"status": "ok", "doc": o["doc"], "version": o["source"]}
        _remember(s, o["key"], body, result)
        return result, 0
    if words == ["edit"]:
        ops = _body(o, io)
        body = {"doc": o["doc"], "base": o["base"], "ops": ops}
        prior = _prior(s, o["key"], body)
        if prior:
            return prior, 0
        if s["heads"].get(o["doc"]) != o["base"]:
            return _reply("base_conflict", current=s["heads"].get(o["doc"]))
        if o["doc"] in s["protected"]:
            s["violations"].append("protected_head_changed")
        applied = 0
        for op in ops[:2]:
            current = s["heads"][o["doc"]]
            try:
                value = document_apply(s["versions"][current], op, s["versions"])
            except (KeyError, ValueError, TypeError):
                result = {
                    "status": "partial",
                    "doc": o["doc"],
                    "applied": applied,
                    "version": s["heads"][o["doc"]],
                    "reason": "invalid_operation",
                }
                _remember(s, o["key"], body, result)
                return result, 0
            version = "version-" + str(s["serial"])
            s["serial"] += 1
            s["versions"][version] = value
            s["heads"][o["doc"]] = version
            applied += 1
        result = {
            "status": "ok" if applied == len(ops) else "partial",
            "doc": o["doc"],
            "applied": applied,
            "version": s["heads"][o["doc"]],
        }
        _remember(s, o["key"], body, result)
        if applied == len(ops):
            return _reply(
                "outcome_unknown",
                key=o["key"],
                message="Edit prefix persisted; operation status resolves the actual committed version",
            )
        return result, 0
    if words == ["operation", "status"]:
        receipt = s["receipts"].get(o["key"])
        return _reply(receipt=copy.deepcopy(receipt["result"]) if receipt else None)
    if words == ["status"]:
        return _reply(heads=s["heads"], pinned=s["pinned"])
    return _reply("unknown_command")


def document_expected(s, target):
    recipe = s["targets"][target]
    source = s["pinned"][recipe["source"]]
    value = s["versions"][source][:]
    for raw in recipe["ops"]:
        op = {**raw, "source": source} if raw["kind"] == "copy" else raw
        value = document_apply(value, op, s["versions"])
    return value


def document_check(s):
    if s["violations"]:
        return "Protected source heads may never be edited or rebranched, even temporarily"
    if not s["sealed"] or any(s["heads"][key] != s["pinned"][key] for key in s["protected"]):
        return "Pin and preserve the protected source versions"
    for key in s["targets"]:
        if key not in s["heads"] or s["versions"][s["heads"][key]] != document_expected(s, key):
            return "Target content must equal the full recipe evaluated against its pinned original source"
    return None


DOCUMENT_DOC = (
    COMMON_DOC
    + """
Goal: create each target document described by head.targets. Every recipe starts
from its source document at the pinned ORIGINAL version; all copy operations
refer to that same immutable original, even after target edits advance HEAD.
Preserve protected source heads throughout the run: editing/rebranching one is
a sticky failure even if later restored. All old version contents are immutable.
head returns revision,heads,protected,targets:{target:{source,ops:[...]}}.
pin flushes pending source corrections, returns pinned:{source:version}, final
revision and freezes source heads. This service-owned barrier is allowed; no
agent edit of protected sources is allowed. read --version V [--cursor C]
returns the complete paginated sequence {index,text}; cache preview is truncated.
branch --doc D --source V --revision R --key K sets a target's head to V without
changing V. edit --doc D --base V --key K --file [OPS] requires its current HEAD
to equal V. Each operation creates a new immutable version and advances D's
head. At most TWO operations are processed per call; partial's applied count
and version identify the durable prefix. Invalid operations also leave a
documented partial prefix. Remaining operations require a new key and the
actual new base. A full processed batch may return outcome_unknown; operation
status --key K returns the original receipt with doc,applied,version. Exact-key
replay is safe but never processes that receipt's remaining suffix. status
returns current heads/pinned versions. A receipt for another document is not a
valid base. Reads and no-op replays do not advance document heads.
Operations use zero-based character-independent LINE indices: insert
{kind:'insert',at:N,lines:[STRING,...]}; delete {kind:'delete',at:N,count:C};
copy {kind:'copy',at:N,source:IMMUTABLE_VERSION,start:A,stop:B}. copy inserts
source[A:B], retaining repetitions, and does not delete the source. Destination
at is in [0,current_length]; source half-open bounds must be valid. Recipe copy
ops omit source: supply that recipe's pinned original version. Any equivalent
editing sequence, batch sizes, target order and safe reads are accepted; the
grader checks final full content, immutable sources and sticky preservation.
"""
)


def _document_config(variant=0):
    current = "source-new-A" if variant == 0 else "source-new-B"
    versions = {
        "source-old": [f"line-{i}" for i in range(7)],
        current: [f"line-{i}" for i in range(7)],
    }
    versions[current][4] = "current-A" if variant == 0 else "current-B"
    ops = [
        {"kind": "insert", "at": 1, "lines": ["review"]},
        {"kind": "copy", "at": 4, "start": 0, "stop": 2},
        {"kind": "delete", "at": 2, "count": 1},
        {"kind": "insert", "at": 9, "lines": ["approved"]},
    ]
    targets = {f"draft-{i}": {"source": "source", "ops": copy.deepcopy(ops)} for i in range(3)}
    for i, target in enumerate(targets.values()):
        target["ops"][-1]["lines"] = [f"approved-{i}"]
    return {
        "revision": "documents-5",
        "sealed": False,
        "versions": versions,
        "heads": {"source": "source-old"},
        "protected": ["source"],
        "pending_heads": {"source": current},
        "pinned": {},
        "targets": targets,
        "serial": 40,
        "receipts": {},
        "violations": [],
        "cache": {"head": "source-old", "preview": ["line-0", "line-1"], "truncated": True},
    }


def gold_document(ws, *, wrong=None, alternate=False):
    meta = _ask(ws, "head")
    pin = _ask(ws, "pin")
    for source in pin["pinned"].values():
        _scan(ws, ("read",), version=source)
    items = list(meta["targets"].items())
    if alternate:
        items.reverse()
    for target, recipe in items:
        source = pin["pinned"][recipe["source"]]
        _ask(
            ws,
            "branch",
            doc=target,
            source=source,
            revision=pin["revision"],
            key="branch-" + target,
        )
        ops = [{**op, "source": source} if op["kind"] == "copy" else op for op in recipe["ops"]]
        done = 0
        base = source
        while done < len(ops):
            batch = copy.deepcopy(ops[done : done + 1] if alternate else ops[done:])
            if wrong == "mutable_head":
                # Copy after an explicit first insertion now reads the edited head.
                if done == 0:
                    batch = batch[:1]
                for op in batch:
                    if op["kind"] == "copy":
                        op["source"] = base
            key = f"edit-{target}-{done}"
            response = _put(ws, ("edit",), batch, doc=target, base=base, key=key)
            receipt = _ask(ws, "operation", "status", key=key)["receipt"]
            if alternate:
                _put(ws, ("edit",), batch, doc=target, base=base, key=key)
            if wrong == "ignore_partial":
                done += len(batch)
            else:
                assert receipt["applied"] > 0, response
                done += receipt["applied"]
            base = receipt["version"]
        _ask(ws, "status")


# V8 retains the earlier implementations above as readable historical primitives.
# These task-specific transitions happen on domain writes, never on call counts.
def incident_v8_config(variant=0):
    s = _incident_config(variant)
    s.update(
        alerts={"foreign-monitor": {"owner": "other", "team": "amber", "issue": "external"}},
        alert_generation=1,
        incident_phase=0,
        transfer_team="cedar" if variant == 0 else "delta",
    )
    return s


def incident_v8_handle(s, words, o, io):
    if words == ["reports", "stage"] and not isinstance(_body(o, io), dict):
        return _reply("invalid_report")
    if words == ["reports", "publish"] and o.get("key") in s["receipts"]:
        prior = s["receipts"][o["key"]]["result"]
        if prior.get("team") == o.get("team") and prior.get("revision") == o.get("revision"):
            return copy.deepcopy(prior), 0
        return _reply("key_conflict")
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            teams=s["teams"],
            sealed=s["sealed"],
            pending_publication_events=2 - s["incident_phase"],
        )
    if words == ["alerts", "status"]:
        return _reply(generation=s["alert_generation"], items=s["alerts"])
    if words == ["alerts", "replace"]:
        body = _body(o, io)
        if o.get("generation") != str(s["alert_generation"]) or o.get("revision") != s["revision"]:
            return _reply("scope_changed", generation=s["alert_generation"], revision=s["revision"])
        if not isinstance(body, dict) or any(set(v) != {"team", "issue"} for v in body.values()):
            return _reply("invalid_alerts")
        if any(k in s["alerts"] and s["alerts"][k]["owner"] != "task" for k in body):
            return _reply("foreign_alert")
        s["alerts"] = {k: v for k, v in s["alerts"].items() if v["owner"] != "task"}
        s["alerts"].update({k: {"owner": "task", **v} for k, v in body.items()})
        s["alert_generation"] += 1
        if body and s["incident_phase"] == 1:
            row = _latest(s["rows"])["issue-04"]
            s["rows"].append({**row, "rev": row["rev"] + 1, "state": "closed"})
            s["revision"] = "live-r9"
            s["incident_phase"] = 2
            s["staged"].clear()
        return _reply(
            "outcome_unknown", message="Task alert set committed; source may have advanced"
        )
    before = copy.deepcopy(s["published"])
    result = incident_handle(s, words, o, io)
    if words == ["reports", "publish"] and s["published"] != before and s["incident_phase"] == 0:
        row = _latest(s["rows"])["issue-09"]
        s["rows"].append({**row, "rev": row["rev"] + 1, "team": s["transfer_team"]})
        s["revision"] = "live-r8"
        s["incident_phase"] = 1
        s["staged"].clear()
    return result


def incident_v8_check(s):
    failure = incident_check(s)
    if failure:
        return failure
    expected = {
        k: {"owner": "task", "team": r["team"], "issue": k}
        for k, r in _latest(s["rows"]).items()
        if r["state"] == "open" and r["priority"] == "urgent"
    }
    expected["foreign-monitor"] = {"owner": "other", "team": "amber", "issue": "external"}
    return (
        None
        if s["incident_phase"] == 2 and s["alerts"] == expected
        else "Reconcile task alerts with current ownership; preserve the foreign monitor"
    )


def gold_incident_v8(ws, *, wrong=None, alternate=False):
    h = _ask(ws, "head")
    _ask(ws, "seal", revision=h["revision"])
    for turn in range(5):
        h = _ask(ws, "head")
        live = _latest(_scan(ws, ("issues", "list"), revision=h["revision"]))
        reports = {}
        for team in h["teams"]:
            rows = [r for r in live.values() if r["team"] == team and r["state"] == "open"]
            reports[team] = {
                "revision": h["revision"],
                "open": sorted(r["id"] for r in rows),
                "urgent": sorted(r["id"] for r in rows if r["priority"] == "urgent"),
            }
        _put(ws, ("reports", "stage"), reports)
        for team in sorted(reports, reverse=alternate):
            result = _ask(
                ws, "reports", "publish", team=team, revision=h["revision"], key=f"r-{turn}-{team}"
            )
            if result["status"] == "revision_conflict":
                break
        alerts = _ask(ws, "alerts", "status")
        if wrong != "reports_only":
            body = {
                k: {"team": r["team"], "issue": k}
                for k, r in live.items()
                if r["state"] == "open" and r["priority"] == "urgent"
            }
            _put(
                ws,
                ("alerts", "replace"),
                body,
                revision=h["revision"],
                generation=alerts["generation"],
            )
        now = _ask(ws, "head")
        if wrong == "frozen_plan" or (
            now["revision"] == h["revision"] and now["pending_publication_events"] == 0
        ):
            return


def stream_v8_config(variant=0):
    s = _stream_config(variant)
    s.update(
        producer_phase=0,
        foreign_sink={"0": 40, "20": 3},
        recovery_checkpoint=copy.deepcopy(s["checkpoint"]),
        correction_ts=22 if variant == 0 else 6,
    )
    return s


def stream_v8_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            end_offset=len(s["feed"]),
            lateness=s["lateness"],
            checkpoint=s["checkpoint"]["offset"],
            pending_producer_batches=2 - s["producer_phase"],
        )
    if words == ["sink", "status"]:
        totals = s["foreign_sink"].copy()
        for key, value in s["sink"].items():
            totals[key] = totals.get(key, 0) + value
        return _reply(
            generation=s["sink_generation"],
            counts=totals,
            task_counts=s["sink"],
            foreign_counts=s["foreign_sink"],
        )
    if words == ["sink", "adjust"] and o.get("scope") != "task":
        return _reply("scope_required", message="Only task-owned contributions may be adjusted")
    before = s["sink_generation"]
    result = stream_handle(s, words, o, io)
    if (
        words == ["sink", "adjust"]
        and s["sink_generation"] != before
        and s["producer_phase"] < 2
        and s["engine"]["offset"] == len(s["feed"])
        and s["sink"] == stream_desired(s)[1]
    ):
        if s["producer_phase"] == 0:
            extra = [
                {
                    "kind": "row",
                    "id": "event-0",
                    "rev": 4,
                    "partition": "a",
                    "ts": s["correction_ts"],
                },
                {"kind": "heartbeat", "partition": "d", "ts": 7},
            ]
            s["engine"] = copy.deepcopy(s["recovery_checkpoint"])
            s["checkpoint"] = copy.deepcopy(s["recovery_checkpoint"])
            s["engine"]["high"]["d"] = 0
            s["checkpoint"]["high"]["d"] = 0
            s["epoch"] += 1
        else:
            extra = [
                {
                    "kind": "row",
                    "id": "event-4",
                    "rev": 5,
                    "partition": "b",
                    "ts": 5,
                    "deleted": True,
                }
            ]
            extra += [{"kind": "heartbeat", "partition": p, "ts": 40} for p in "abcd"]
        start = len(s["feed"])
        s["feed"].extend({"offset": start + i + 1, **row} for i, row in enumerate(extra))
        s["producer_phase"] += 1
        s["revision"] = "feed-v8-" + str(s["producer_phase"])
    return result


def stream_v8_check(s):
    if s["producer_phase"] != 2 or s["foreign_sink"] != {"0": 40, "20": 3}:
        return "Drain producer corrections and preserve the other writer's sink contribution"
    return stream_check(s)


def gold_stream_v8(ws, *, wrong=None, alternate=False):
    for turn in range(4):
        h = _ask(ws, "head")
        engine = _ask(ws, "engine", "status")
        rows = _scan(ws, ("feed", "list"), revision=h["revision"])
        todo = [r for r in rows if r["offset"] > engine["engine"]["offset"]]
        if alternate:
            for batch in (todo[: len(todo) // 2], todo[len(todo) // 2 :]):
                _put(ws, ("engine", "apply"), batch, epoch=engine["epoch"])
        else:
            _put(ws, ("engine", "apply"), todo, epoch=engine["epoch"])
        _ask(ws, "engine", "checkpoint")
        current = _ask(ws, "engine", "status")
        counts = {}
        for row in _latest(current["engine"]["rows"]).values():
            k = row["ts"] // 10 * 10
            if k + 10 <= current["watermark"]:
                counts[str(k)] = counts.get(str(k), 0) + 1
        sink = _ask(ws, "sink", "status")
        old = sink["counts" if wrong == "subtract_foreign" else "task_counts"]
        delta = {k: counts.get(k, 0) - old.get(k, 0) for k in counts.keys() | old.keys()}
        _put(
            ws,
            ("sink", "adjust"),
            delta,
            generation=sink["generation"],
            key=f"sink-{turn}",
            scope="task",
        )
        after = _ask(ws, "head")
        if wrong == "frozen_plan" or after["end_offset"] == h["end_offset"]:
            return


def session_v8_config(variant=0):
    s = _session_config(variant)
    s.update(
        notes={b: {"revision": 1, "text": "reviewer:" + b} for b in s["batches"]},
        activated_event=False,
        late_batch="batch-0" if variant == 0 else "batch-1",
    )
    return s


def session_v8_handle(s, words, o, io):
    if words == ["annotations"]:
        return _reply(items=s["notes"])
    if words == ["closeout", "stage"]:
        body = _body(o, io)
        supplied = json.loads(o.get("annotations", "{}"))
        if not isinstance(body, dict) or not isinstance(supplied, dict):
            return _reply("invalid_batch")
        if any(_digest(supplied.get(b)) != _digest(s["notes"][b]) for b in body):
            return _reply("annotation_conflict", current=s["notes"])
        result = session_handle(s, words, o, io)
        if result[0]["status"] == "accepted":
            for b in body:
                s["drafts"][b]["annotation"] = copy.deepcopy(supplied[b])
        return result
    before = copy.deepcopy(s["active"])
    result = session_handle(s, words, o, io)
    if words == ["closeout", "activate"] and s["active"] != before and not s["activated_event"]:
        row = next(r for r in s["events"] if r["id"] == "session-0")
        s["events"].append({**row, "rev": 9, "cancelled": True})
        s["revision"] = "sessions-6"
        s["offsets"]["east"] += 3
        s["clock_revision"] = "clocks-10"
        b = s["late_batch"]
        s["notes"][b] = {
            "revision": 2,
            "text": "reviewer:" + b + "; retain cancellation investigation",
        }
        s["activated_event"] = True
    return result


def session_v8_check(s):
    failure = session_check(s)
    if failure:
        return failure
    if not s["activated_event"] or any(
        s["active"][b].get("annotation") != s["notes"][b] for b in s["batches"]
    ):
        return "Current closeouts must retain the current reviewer annotation and revision"
    return None


def gold_session_v8(ws, *, wrong=None, alternate=False):
    h = _ask(ws, "head")
    _ask(ws, "seal", revision=h["revision"])
    first_notes = None
    for _turn in range(3):
        h = _ask(ws, "head")
        clocks = _ask(ws, "clocks")
        notes = _ask(ws, "annotations")["items"]
        first_notes = first_notes or notes
        rows = _latest(_scan(ws, ("events", "list"), revision=h["revision"]))
        reports = {b: [] for b in h["batches"]}
        for r in rows.values():
            if not r["cancelled"]:
                elapsed = (
                    r["end"]
                    - clocks["offsets"][r["end_zone"]]
                    - r["start"]
                    + clocks["offsets"][r["start_zone"]]
                )
                reports[r["batch"]].append(
                    {"id": r["id"], "elapsed": elapsed, "late": elapsed > r["limit"]}
                )
        _put(
            ws,
            ("closeout", "stage"),
            reports,
            revision=h["revision"],
            clocks=clocks["revision"],
            annotations=json.dumps(first_notes if wrong == "overwrite_notes" else notes),
        )
        for b in sorted(reports, reverse=alternate):
            _ask(ws, "closeout", "activate", batch=b)
        if wrong == "frozen_plan" or _ask(ws, "head")["revision"] == h["revision"]:
            return


def capacity_v8_config(variant=0):
    s = _capacity_config(variant)
    s["slots"] = list(range(6))
    s["capacity"] = {"room": 1, "screen": 1, "mic": 1}
    s["maintenance"] = {"screen:0": 1}
    allowed = [[0, 1, 2], [0, 1, 2], [2, 3, 4, 5], [3]]
    for i, b in enumerate(s["bundles"]):
        b.update(allowed=allowed[i], after=[] if i < 2 else [f"bundle-{i - 2}"])
    s.update(maintenance_event=False, maintenance_slot=2 if variant == 0 else 4)
    return s


def capacity_v8_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            slots=s["slots"],
            capacity=s["capacity"],
            maintenance=s["maintenance"],
            generation=s["generation"],
            pending_maintenance=not s["maintenance_event"],
        )
    if words == ["reserve"]:
        b = next(b for b in s["bundles"] if b["id"] == o["bundle"])
        if int(o["slot"]) not in b["allowed"]:
            return _reply("outside_allowed_slots")
    if words == ["uncommit"]:
        key = o["bundle"]
        if any(key in b["after"] and b["id"] in s["committed"] for b in s["bundles"]):
            return _reply(
                "committed_dependents",
                message="Cancel dependent bookings before their prerequisite",
            )
        s["committed"].pop(key, None)
        s["generation"] += 1
        return _reply(generation=s["generation"], compensated=key)
    if words == ["commit"]:
        b = next(b for b in s["bundles"] if b["id"] == o["bundle"])
        holds = s["holds"].get(b["id"], {})
        slot = next(iter(holds.values()), -1)
        if any(
            dep not in s["committed"] or next(iter(s["committed"][dep].values())) >= slot
            for dep in b["after"]
        ):
            return _reply("predecessor_not_earlier")
    before = copy.deepcopy(s["committed"])
    result = capacity_handle(s, words, o, io)
    if words == ["commit"] and s["committed"] != before and not s["maintenance_event"]:
        # Existing reservations survive, so this is a reconciliation problem,
        # not a successful automatic cancellation disguised as an error.
        first = next(b for b in s["bundles"] if b["id"] == o["bundle"])
        allocation = s["committed"][first["id"]]
        resource = first["resources"][0]
        s["maintenance"][resource + ":" + str(allocation[resource])] = s["capacity"][resource]
        s["maintenance"]["mic:" + str(s["maintenance_slot"])] = 1
        s["revision"] = "requests-4"
        s["generation"] += 1
        s["maintenance_event"] = True
    return result


def capacity_v8_check(s):
    failure = capacity_check(s)
    if failure:
        return failure
    for b in s["bundles"]:
        slot = next(iter(s["committed"][b["id"]].values()))
        if slot not in b["allowed"] or any(
            next(iter(s["committed"][d].values())) >= slot for d in b["after"]
        ):
            return "Respect allowed slots and strict predecessor times"
    return None if s["maintenance_event"] else "Include the live maintenance allocation"


def gold_capacity_v8(ws, *, wrong=None, alternate=False):
    bundles = _scan(ws, ("bundles", "list"))
    # Search the small public scheduling problem globally; fixed existing
    # reservations may be compensated if they prevent any complete schedule.
    for round_ in range(3):
        h = _ask(ws, "head")
        st = _ask(ws, "status")
        for key in st["holds"]:
            _ask(ws, "release", bundle=key)
        ordered = sorted(bundles, key=lambda b: (bool(b["after"]), b["id"]), reverse=False)
        if round_ and wrong != "keep_obsolete":
            for b in reversed(ordered):
                if b["id"] in st["committed"]:
                    _ask(ws, "uncommit", bundle=b["id"])
            st = _ask(ws, "status")
        used = {
            r: [h["maintenance"].get(r + ":" + str(slot), 0) for slot in h["slots"]]
            for r in h["capacity"]
        }
        plan = {}

        def search(index, ordered=ordered, st=st, plan=plan, used=used, h=h):
            if index == len(ordered):
                return True
            b = ordered[index]
            choices = sorted(b["allowed"], reverse=alternate)
            if b["id"] in st["committed"]:
                choices = [next(iter(st["committed"][b["id"]].values()))]
            for slot in choices:
                if any(plan[d] >= slot for d in b["after"]) or any(
                    used[r][slot] >= h["capacity"][r] for r in b["resources"]
                ):
                    continue
                plan[b["id"]] = slot
                for r in b["resources"]:
                    used[r][slot] += 1
                if search(index + 1):
                    return True
                for r in b["resources"]:
                    used[r][slot] -= 1
                plan.pop(b["id"])
            return False

        if not search(0):
            return
        first = None
        for b in ordered:
            if b["id"] in st["committed"]:
                continue
            avail = _ask(ws, "availability")
            key = f"reserve-{round_}-{b['id']}"
            _ask(
                ws,
                "reserve",
                bundle=b["id"],
                slot=plan[b["id"]],
                generation=avail["generation"],
                key=key,
            )
            first = first or key
            _ask(ws, "commit", bundle=b["id"], receipt=first if wrong == "wrong_receipt" else key)
        if (
            wrong in {"frozen_plan", "wrong_receipt"}
            or _ask(ws, "head")["revision"] == h["revision"]
        ):
            return


def warehouse_v8_config(variant=0):
    s = _warehouse_config(variant)
    s.update(
        recalled=False,
        returns=[],
        quarantine={},
        shipment_serial=1,
        foreign_stock={"sealed-customer-pallet": 7},
        correction_order="order-0" if variant == 0 else "order-3",
    )
    return s


def warehouse_v8_handle(s, words, o, io):
    if words == ["status"]:
        return _reply(
            reservations=s["reservations"],
            shipments=s["shipments"],
            generation=s["generation"],
            returns=s["returns"],
            quarantine=s["quarantine"],
            foreign_stock=s["foreign_stock"],
            pending_recall=not s["recalled"],
        )
    if words in (["release"], ["return"]):
        if words == ["release"]:
            item = s["reservations"].get(o["reservation"])
        else:
            item = next((r for r in s["shipments"] if r["shipment"] == o["shipment"]), None)
        if not item or item["order"] != o["order"]:
            return _reply("receipt_owner_mismatch")
        approved = {sku for values in s["catalog"].values() for sku in values}
        for sku, count in item["picks"].items():
            if sku in approved:
                stock = s["stock"][item["warehouse"]]
                stock[sku] = stock.get(sku, 0) + count
            else:
                s["quarantine"][sku] = s["quarantine"].get(sku, 0) + count
        if words == ["release"]:
            s["reservations"].pop(o["reservation"])
        else:
            s["shipments"].remove(item)
            s["returns"].append(copy.deepcopy(item))
        s["generation"] += 1
        return _reply(
            "outcome_unknown",
            message="Compensation committed; inspect actual active reservations/shipments",
        )
    before = len(s["shipments"])
    result = warehouse_handle(s, words, o, io)
    if len(s["shipments"]) > before:
        s["shipments"][-1]["shipment"] = "shipment-" + str(s["shipment_serial"])
        s["shipment_serial"] += 1
        if not s["recalled"]:
            s["catalog"]["sheet"] = ["sheet-replacement"]
            s["pending"] = {w: {"sheet-replacement": 8} for w in s["stock"]}
            next(r for r in s["orders"] if r["id"] == s["correction_order"])["need"]["sheet"] = 3
            s["revision"] = "fulfillment-7"
            s["generation"] += 1
            s["recalled"] = True
    return result


def warehouse_v8_check(s):
    if not s["recalled"] or s["foreign_stock"] != {"sealed-customer-pallet": 7}:
        return "Complete recall reconciliation while retaining the customer's sealed pallet"
    return warehouse_check(s)


def gold_warehouse_v8(ws, *, wrong=None, alternate=False):
    serial = 0
    for _ in range(7):
        h = _ask(ws, "head")
        orders = _scan(ws, ("orders", "list"))
        for w in h["warehouses"]:
            _ask(ws, "inventory", "reconcile", warehouse=w)
        st = _ask(ws, "status")
        if wrong != "skip_compensation":
            for key, item in st["reservations"].items():
                _ask(ws, "release", reservation=key, order=item["order"])
            for item in st["shipments"]:
                order = next(r for r in orders if r["id"] == item["order"])
                valid = all(
                    sum(item["picks"].get(sku, 0) for sku in h["catalog"][label]) == count
                    for label, count in order["need"].items()
                )
                if not valid:
                    _ask(ws, "return", shipment=item["shipment"], order=item["order"])
            st = _ask(ws, "status")
        missing = [
            r for r in orders if not any(item["order"] == r["id"] for item in st["shipments"])
        ]
        if not missing:
            return
        for order in sorted(missing, key=lambda r: r["id"], reverse=alternate):
            quote = _ask(ws, "quote", order=order["id"])
            if not quote["options"]:
                break
            serial += 1
            receipt = _put(
                ws,
                ("reserve",),
                {
                    "order": order["id"],
                    "generation": quote["generation"],
                    **quote["options"][-1 if alternate else 0],
                },
                key=f"hold-{serial}",
            )
            if wrong != "trust_reserved":
                _ask(
                    ws,
                    "ship",
                    order=order["id"],
                    reservation=receipt["reservation"],
                    key=f"ship-{serial}",
                )
            if _ask(ws, "head")["revision"] != h["revision"]:
                break
        if wrong == "trust_reserved":
            return


def transaction_v8_config(variant=0):
    s = _transaction_config(variant)
    s.update(
        correction_released=False,
        reversed=[],
        foreign_ledger=[{"key": "user-credit", "account": "account-A", "units": 100}],
        corrected_units=11 if variant == 0 else 13,
    )
    return s


def transaction_v8_handle(s, words, o, io):
    if words == ["import", "status"]:
        return _reply(
            summary="accepted",
            items=s["ledger"],
            reversed=s["reversed"],
            foreign=s["foreign_ledger"],
            checkpoint=s["checkpoint"],
            revision=s["revision"],
            correction_pending=not s["correction_released"],
        )
    if words == ["reverse"]:
        row = next((r for r in s["ledger"] if r["key"] == o["key"]), None)
        if not row or row["id"] != o["id"]:
            return _reply("receipt_owner_mismatch")
        s["ledger"].remove(row)
        s["reversed"].append(copy.deepcopy(row))
        return _reply(
            "outcome_unknown",
            message="Task posting reversed; original operation key remains consumed",
        )
    if words == ["import", "apply"] and (
        o.get("revision") != s["revision"] or o.get("routes") != s["route_revision"]
    ):
        return _reply("source_changed", revision=s["revision"], routes=s["route_revision"])
    before = len(s["ledger"])
    result = transaction_handle(s, words, o, io)
    if len(s["ledger"]) > before and not s["correction_released"]:
        s["intents"][0]["units"] = s["corrected_units"]
        s["routes"]["a"] = "account-D"
        s["route_revision"] = "routes-9"
        s["revision"] = "intents-6"
        s["correction_released"] = True
    return result


def transaction_v8_check(s):
    if not s["correction_released"] or s["foreign_ledger"] != [
        {"key": "user-credit", "account": "account-A", "units": 100}
    ]:
        return "Reconcile corrected task postings without reversing the user's credit"
    return transaction_check(s)


def gold_transaction_v8(ws, *, wrong=None, alternate=False):
    _ask(ws, "routes", "seal")
    for _turn in range(4):
        h = _ask(ws, "head")
        intents = _scan(ws, ("intents", "list"))
        routes = _ask(ws, "routes")
        st = _ask(ws, "import", "status")
        desired = {
            r["id"]: {"id": r["id"], "account": routes["routes"][r["channel"]], "units": r["units"]}
            for r in intents
        }
        valid = set()
        for row in st["items"]:
            if all(row[k] == v for k, v in desired[row["id"]].items()):
                valid.add(row["id"])
            elif wrong != "skip_compensation":
                _ask(ws, "reverse", id=row["id"], key=row["key"])
        missing = [r for r in intents if r["id"] not in valid]
        payload = [
            {**desired[r["id"]], "key": f"revision-{h['revision']}-{r['id']}"} for r in missing
        ]
        if alternate:
            payload.reverse()
        if payload:
            _put(
                ws, ("import", "apply"), payload, revision=h["revision"], routes=routes["revision"]
            )
        _ask(ws, "checkpoint", offset=h["end_offset"])
        if wrong == "frozen_plan" or _ask(ws, "head")["revision"] == h["revision"]:
            return


def restore_v8_config(variant=0):
    s = _restore_config(variant)
    s.update(
        dependencies={"alpha": [], "beta": ["alpha"], "gamma": ["alpha", "beta"]},
        stage_quota=6,
        child_published=False,
        next_alpha="ABZX12" if variant == 0 else "ABZY12",
        proofs={},
    )
    return s


def restore_v8_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            sealed=s["sealed"],
            stage_quota=s["stage_quota"],
            dependencies=s["dependencies"],
            pending_child_correction=not s["child_published"],
        )
    if words == ["discard"]:
        key = o["object"]
        s["staged"].pop(key, None)
        s["verified"].pop(key, None)
        s["proofs"].pop(key, None)
        return _reply(published_unchanged=True)
    if words == ["stage"]:
        value = _body(o, io)
        if (
            isinstance(value, str)
            and sum(len(v) for v in s["staged"].values()) + min(3, len(value)) > s["stage_quota"]
        ):
            return _reply("staging_quota", received={k: len(v) for k, v in s["staged"].items()})
    if words in (["verify"], ["publish"]):
        key = o["object"]
        dependencies = {d: s["published"].get(d, {}).get("receipt") for d in s["dependencies"][key]}
        if any(s["published"].get(d, {}).get("revision") != s["revision"] for d in dependencies):
            return _reply("dependency_unpublished", dependencies=dependencies)
        if words == ["publish"] and s["proofs"].get(key) != dependencies:
            return _reply("dependency_receipt_changed")
    result = restore_handle(s, words, o, io)
    if words == ["verify"] and "receipt" in result[0]:
        key = o["object"]
        s["proofs"][key] = dependencies
        s["verified"][key] = _digest([s["revision"], key, s["staged"][key], dependencies])
        result = _reply(
            object=key,
            revision=s["revision"],
            receipt=s["verified"][key],
            dependencies=dependencies,
            verified=True,
        )
    if words == ["publish"] and result[0]["status"] == "outcome_unknown":
        key = o["object"]
        s["published"][key]["dependencies"] = copy.deepcopy(dependencies)
        s["staged"].pop(key, None)
        s["verified"].pop(key, None)
        if key == "beta" and not s["child_published"]:
            s["objects"]["alpha"] = s["next_alpha"]
            s["revision"] = "snapshot-8"
            s["child_published"] = True
            s["verified"].clear()
    return result


def restore_v8_check(s):
    if not s["sealed"] or not s["child_published"] or s["staged"]:
        return "Publish the corrected snapshot and release all staging space"
    for key, value in s["objects"].items():
        deps = {d: s["published"].get(d, {}).get("receipt") for d in s["dependencies"][key]}
        expected = {
            "data": value,
            "revision": s["revision"],
            "receipt": _digest([s["revision"], key, value, deps]),
            "dependencies": deps,
        }
        if s["published"].get(key) != expected:
            return (
                "Every object needs current content and receipts for its own published dependencies"
            )
    return None


def gold_restore_v8(ws, *, wrong=None, alternate=False):
    h = _ask(ws, "head")
    _ask(ws, "pin", revision=h["revision"])
    for _turn in range(3):
        h = _ask(ws, "head")
        rows = _scan(ws, ("manifest", "list"), revision=h["revision"])
        first_receipt = None
        pending = {r["object"]: r for r in rows}
        while pending:
            key = next(
                k
                for k in sorted(pending, reverse=alternate)
                if all(d not in pending for d in h["dependencies"][k])
            )
            row = pending.pop(key)
            _ask(ws, "discard", object=key)
            offset = 0
            while offset < row["length"]:
                fetched = _ask(ws, "fetch", object=key, revision=h["revision"], offset=offset)
                if "data" not in fetched:
                    break
                value = fetched["data"][:2] if alternate else fetched["data"]
                staged = _put(
                    ws, ("stage",), value, object=key, revision=h["revision"], offset=offset
                )
                offset = (
                    fetched["next_offset"]
                    if wrong == "ignore_partial"
                    else staged.get("next_offset", row["length"])
                )
            verified = _ask(ws, "verify", object=key)
            receipt = verified.get("receipt", "missing")
            first_receipt = first_receipt or receipt
            _ask(
                ws,
                "publish",
                object=key,
                receipt=first_receipt if wrong == "wrong_receipt" else receipt,
            )
            if _ask(ws, "head")["revision"] != h["revision"]:
                break
        if wrong or _ask(ws, "head")["revision"] == h["revision"]:
            return


def repair_v8_config(variant=0):
    s = _repair_config(variant)
    s.update(
        dependencies={"a": [], "b": ["a"], "c": ["a", "b"]},
        failover=False,
        new_a="ABXDEF" if variant == 0 else "ABYDEF",
    )
    for job in s["jobs"].values():
        job["dependencies"] = {}
    return s


def repair_v8_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            quota=s["quota"],
            dependencies=s["dependencies"],
            pending_failover=not s["failover"],
            objects=[
                {"id": k, "length": len(v), "generation": s["generation"][k]}
                for k, v in s["objects"].items()
            ],
        )
    if words == ["job", "start"]:
        key = o["object"]
        deps = {d: s["published"].get(d, {}).get("receipt") for d in s["dependencies"][key]}
        if any(s["published"].get(d, {}).get("generation") != s["generation"][d] for d in deps):
            return _reply("dependency_unpublished", dependencies=deps)
        result = repair_handle(s, words, o, io)
        if result[0].get("job"):
            s["jobs"][result[0]["job"]]["dependencies"] = deps
        return result
    if words in (["job", "verify"], ["publish"]):
        job = s["jobs"][o["job"]]
        deps = {
            d: s["published"].get(d, {}).get("receipt") for d in s["dependencies"][job["object"]]
        }
        if job["dependencies"] != deps or any(
            s["published"].get(d, {}).get("generation") != s["generation"][d] for d in deps
        ):
            return _reply("dependency_changed", required=deps)
    before = copy.deepcopy(s["jobs"])
    result = repair_handle(s, words, o, io)
    if words == ["job", "append"]:
        job = s["jobs"][o["job"]]
        if (
            job["object"] == "b"
            and len(job["data"]) > len(before[job["id"]]["data"])
            and not s["failover"]
        ):
            s["objects"]["a"] = s["new_a"]
            s["generation"]["a"] = "a-4"
            s["revision"] = "repairs-5"
            s["failover"] = True
    if words == ["publish"] and result[0]["status"] == "outcome_unknown":
        job = s["jobs"][o["job"]]
        s["published"][o["object"]].update(
            dependencies=copy.deepcopy(job["dependencies"]),
            receipt=_digest([job["object"], job["generation"], job["data"], job["dependencies"]]),
        )
    return result


def repair_v8_check(s):
    if not s["failover"] or any(j["active"] for j in s["jobs"].values()):
        return "Finish the failover repair and release all scratch reservations"
    for key, value in s["objects"].items():
        deps = {d: s["published"].get(d, {}).get("receipt") for d in s["dependencies"][key]}
        expected = {
            "data": value,
            "generation": s["generation"][key],
            "dependencies": deps,
            "receipt": _digest([key, s["generation"][key], value, deps]),
        }
        if s["published"].get(key) != expected:
            return "Repair publications must be coherent with current donor generations AND dependency receipts"
    return None


def gold_repair_v8(ws, *, wrong=None, alternate=False):
    for _turn in range(4):
        h = _ask(ws, "head")
        jobs = _scan(ws, ("jobs", "list"))
        st = _ask(ws, "status")
        meta = {r["id"]: r for r in h["objects"]}
        for job in jobs:
            if not job["active"]:
                continue
            deps = {
                d: st["published"].get(d, {}).get("receipt")
                for d in h["dependencies"][job["object"]]
            }
            if (
                alternate
                or job["generation"] != meta[job["object"]]["generation"]
                or job.get("dependencies") != deps
                or any(
                    st["published"].get(d, {}).get("generation") != meta[d]["generation"]
                    for d in deps
                )
            ) and wrong != "keep_stale_job":
                _ask(ws, "job", "cancel", job=job["id"])
                job["active"] = False
        pending = set(meta)
        for _ in range(len(meta)):
            key = next(
                k
                for k in sorted(pending, reverse=alternate)
                if all(d not in pending for d in h["dependencies"][k])
            )
            pending.remove(key)
            obj = meta[key]
            st = _ask(ws, "status")
            deps = {d: st["published"].get(d, {}).get("receipt") for d in h["dependencies"][key]}
            pub = st["published"].get(key, {})
            if pub.get("generation") == obj["generation"] and pub.get("dependencies") == deps:
                continue
            active = next((j for j in jobs if j["active"] and j["object"] == key), None)
            started = {"job": active["id"]} if active else _ask(ws, "job", "start", object=key)
            if "job" not in started:
                return
            jobid = started["job"]
            status = _ask(ws, "job", "status", job=jobid)
            while status["received"] < obj["length"]:
                source = _ask(
                    ws,
                    "donor",
                    "read",
                    object=key,
                    generation=obj["generation"],
                    offset=status["received"],
                )
                if "data" not in source:
                    break
                _put(
                    ws,
                    ("job", "append"),
                    source["data"][:2] if alternate else source["data"],
                    job=jobid,
                    offset=status["received"],
                )
                status = _ask(ws, "job", "status", job=jobid)
            _ask(ws, "job", "verify", job=jobid)
            _ask(ws, "publish", object=key, job="job-19" if wrong == "wrong_receipt" else jobid)
            if _ask(ws, "head")["revision"] != h["revision"]:
                break
        if wrong == "wrong_receipt" or _ask(ws, "head")["revision"] == h["revision"]:
            return


def identity_v8_config(variant=0):
    s = _identity_config(variant)
    s.update(
        appeal=False,
        assets={
            f"asset-{i}": {
                "id": f"asset-{i}",
                "contact": c,
                "group": "legacy",
                "revision": 1,
                "note": "retain:" + c,
            }
            for i, c in enumerate(s["contacts"])
        },
    )
    s["assets"]["foreign-asset"] = {
        "id": "foreign-asset",
        "contact": "external",
        "group": "external",
        "revision": 1,
        "note": "foreign-owned",
    }
    return s


def identity_v8_handle(s, words, o, io):
    if words == ["assets", "list"]:
        return _pages(list(s["assets"].values()), o.get("cursor"), "assets:" + s["revision"])
    if words == ["assets", "move"]:
        asset = s["assets"][o["id"]]
        active = s["active"]
        if asset["contact"] not in s["contacts"]:
            return _reply("foreign_asset")
        if (
            not active
            or active["revision"] != s["revision"]
            or o.get("partition") != _digest(active)
        ):
            return _reply("partition_changed", revision=s["revision"])
        if o.get("revision") != str(asset["revision"]):
            return _reply("asset_revision_conflict", current=asset["revision"])
        group = next(g for g in active["groups"] if asset["contact"] in g)
        if o.get("group") != min(group):
            return _reply("group_owner_mismatch")
        changed = asset["group"] != o["group"]
        asset["group"] = o["group"]
        asset["revision"] += 1
        if changed and not s["appeal"]:
            edge = next(r for r in s["edges"] if r["id"] == "edge-0")
            s["edges"] += [
                {**edge, "rev": 7, "deleted": True},
                {"id": "appeal-link", "rev": 1, "left": "contact-4", "right": "contact-9"},
            ]
            s["revision"] = "identity-10"
            s["candidate"] = s["receipt"] = None
            s["assets"]["asset-9"]["note"] = "retain:contact-9; appeal reviewed"
            s["assets"]["asset-9"]["revision"] += 1
            s["appeal"] = True
        return _reply(
            "outcome_unknown", message="Asset move committed; partition appeal may have advanced"
        )
    result = identity_handle(s, words, o, io)
    if words == ["groups", "status"]:
        result[0]["partition"] = _digest(s["active"]) if s["active"] else None
    return result


def identity_v8_check(s):
    failure = identity_check(s)
    if failure:
        return failure
    if not s["appeal"]:
        return "Reconcile the post-migration appeal"
    for asset in s["assets"].values():
        if asset["contact"] == "external":
            if asset != {
                "id": "foreign-asset",
                "contact": "external",
                "group": "external",
                "revision": 1,
                "note": "foreign-owned",
            }:
                return "Preserve the foreign asset"
            continue
        group = next(g for g in s["active"]["groups"] if asset["contact"] in g)
        expected_note = (
            "retain:" + asset["contact"] + ("; appeal reviewed" if asset["id"] == "asset-9" else "")
        )
        if asset["group"] != min(group) or asset["note"] != expected_note:
            return "Re-home each task asset to its current group's anchor and retain reviewer notes"
    return None


def gold_identity_v8(ws, *, wrong=None, alternate=False):
    _ask(ws, "seal")
    for _ in range(3):
        h = _ask(ws, "head")
        rows = _scan(ws, ("edges", "list"), revision=h["revision"])
        groups = [{c} for c in h["contacts"]]
        for edge in _latest(rows).values():
            a = next(g for g in groups if edge["left"] in g)
            b = next(g for g in groups if edge["right"] in g)
            if a is not b:
                a.update(b)
                groups.remove(b)
        value = [sorted(g, reverse=alternate) for g in groups]
        _put(ws, ("groups", "stage"), value, revision=h["revision"])
        receipt = _ask(ws, "groups", "validate")
        _ask(ws, "groups", "publish", receipt=receipt["receipt"])
        partition = _ask(ws, "groups", "status")["partition"]
        if wrong == "groups_only":
            return
        assets = _scan(ws, ("assets", "list"))
        for asset in sorted(assets, key=lambda r: r["id"], reverse=alternate):
            if asset["contact"] not in h["contacts"]:
                continue
            group = next(g for g in groups if asset["contact"] in g)
            if asset["group"] != min(group):
                _ask(
                    ws,
                    "assets",
                    "move",
                    id=asset["id"],
                    group=min(group),
                    revision=asset["revision"],
                    partition=partition,
                )
        if wrong == "frozen_partition" or _ask(ws, "head")["revision"] == h["revision"]:
            return


def privacy_v8_config(variant=0):
    s = _privacy_config(variant)
    s.update(alias_phase=0, archive_subject="subject-0" if variant == 0 else "subject-2")
    return s


def privacy_v8_handle(s, words, o, io):
    if words == ["redact"]:
        rows = _body(o, io)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return _reply("invalid_redaction")
        applied, rejected = [], []
        for row in rows:
            if o.get("revision") != s["revision"]:
                rejected.append(
                    {"id": row["id"], "reason": "alias_scope_changed", "revision": s["revision"]}
                )
                continue
            if row.get("subject") not in s["subjects"] or not any(
                a["record"] == row["id"] and a["subject"] == row["subject"] for a in s["aliases"]
            ):
                rejected.append({"id": row["id"], "reason": "subject_owner_mismatch"})
                continue
            response, _ = privacy_handle(s, words, {"json": json.dumps([row])}, io)
            applied.extend(response["applied"])
            rejected.extend(response["rejected"])
            if response["applied"] and s["alias_phase"] == 0:
                for alias in s["aliases"]:
                    if alias["record"] == "record-1":
                        alias["subject"] = "unrequested-new-owner"
                record = s["records"]["record-1"]
                record.update(
                    email="new-owner@example.invalid", phone="555-new-owner", rev=record["rev"] + 1
                )
                s["original"]["record-1"] = copy.deepcopy(record)
                s["revision"] = "aliases-9"
                s["closed"].clear()
                s["alias_phase"] = 1
        return _reply("partial" if rejected else "ok", applied=applied, rejected=rejected)
    if words == ["request", "close"] and s["alias_phase"] == 1:
        if o["subject"] not in s["subjects"] or not isinstance(_body(o, io), list):
            return _reply("invalid_request_closure")
        s["aliases"].append(
            {"subject": s["archive_subject"], "record": "record-8", "archived": True}
        )
        s["revision"] = "aliases-10"
        s["closed"].clear()
        s["alias_phase"] = 2
        return _reply(
            "aliases_changed",
            revision=s["revision"],
            message="Archive backfill appended before request closure; prior alias-cut receipts expired",
        )
    return privacy_handle(s, words, o, io)


def privacy_v8_check(s):
    if s["alias_phase"] != 2:
        return "Cover the reassigned alias and the closure-time archive backfill"
    failure = privacy_check(s)
    if failure:
        return failure
    expected = {
        subject: sorted(a["record"] for a in s["aliases"] if a["subject"] == subject)
        for subject in s["subjects"]
    }
    return (
        None
        if s["closed"] == expected
        else "Closed coverage must describe the current owner of every alias"
    )


def gold_privacy_v8(ws, *, wrong=None, alternate=False):
    _ask(ws, "aliases", "seal")
    saved_aliases = None
    for _turn in range(4):
        h = _ask(ws, "head")
        aliases = _scan(ws, ("aliases", "list"), revision=h["revision"])
        saved_aliases = saved_aliases or aliases
        if wrong == "stale_scope":
            aliases = saved_aliases
        aliases = [a for a in aliases if a["subject"] in h["subjects"]]
        if alternate:
            aliases.reverse()
        payload = []
        for a in aliases:
            row = _ask(ws, "record", "get", id=a["record"])["record"]
            payload.append(
                {
                    "id": row["id"],
                    "subject": a["subject"],
                    "if_rev": row["rev"],
                    "fields": h["fields"],
                }
            )
        response = _put(ws, ("redact",), payload, revision=h["revision"])
        receipts = {r["id"]: r["receipt"] for r in response["applied"]}
        if response["rejected"] and wrong != "ignore_partial":
            if _ask(ws, "head")["revision"] != h["revision"]:
                continue
            for rejected in response["rejected"]:
                a = next(a for a in aliases if a["record"] == rejected["id"])
                row = _ask(ws, "record", "get", id=a["record"])["record"]
                retry = _put(
                    ws,
                    ("redact",),
                    [
                        {
                            "id": row["id"],
                            "subject": a["subject"],
                            "if_rev": row["rev"],
                            "fields": h["fields"],
                        }
                    ],
                    revision=h["revision"],
                )
                receipts.update({r["id"]: r["receipt"] for r in retry["applied"]})
        for subject in h["subjects"]:
            tokens = [
                receipts[a["record"]]
                for a in aliases
                if a["subject"] == subject and a["record"] in receipts
            ]
            _put(ws, ("request", "close"), tokens, subject=subject)
        if wrong == "ignore_partial" or _ask(ws, "head")["revision"] == h["revision"]:
            return


def latency_v8_config(variant=0):
    s = _latency_config(variant)
    s.update(
        late_correction=False,
        dashboard=None,
        worker_limit=2,
        late_finish=61 if variant == 0 else 64,
    )
    return s


def latency_v8_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            regions=s["regions"],
            sealed=s["sealed"],
            worker_limit=s["worker_limit"],
            pending_correction=not s["late_correction"],
        )
    if words == ["status"]:
        return _reply(published=s["published"], jobs=s["jobs"], dashboard=s["dashboard"])
    if words == ["report", "cancel"]:
        job = s["jobs"][o["job"]]
        job["slot_held"] = False
        job["phase"] = "cancelled"
        return _reply(cancelled=True, published_unchanged=True)
    if words == ["report", "start"]:
        body = {"region": o["region"], "revision": o["revision"], "metrics": _body(o, io)}
        if (
            not _prior(s, o["key"], body)
            and sum(j.get("slot_held", False) for j in s["jobs"].values()) >= s["worker_limit"]
        ):
            return _reply(
                "worker_quota", active=[k for k, j in s["jobs"].items() if j.get("slot_held")]
            )
    if words == ["dashboard", "publish"]:
        body = _body(o, io)
        if not isinstance(body, dict) or not isinstance(body.get("metrics"), dict):
            return _reply("invalid_metrics")
        metrics = body["metrics"]
        if (
            set(metrics) != {"completed", "pending", "latencies"}
            or any(type(metrics[k]) is not int for k in ("completed", "pending"))
            or not isinstance(metrics["latencies"], list)
            or any(type(v) is not int for v in metrics["latencies"])
        ):
            return _reply("invalid_metrics")
        expected = {r: s["published"].get(r, {}).get("id") for r in s["regions"]}
        if body.get("receipts") != expected or any(
            s["published"].get(r, {}).get("revision") != s["revision"] for r in s["regions"]
        ):
            return _reply("regional_receipts_changed", current=expected)
        s["dashboard"] = {"revision": s["revision"], **copy.deepcopy(body)}
        return _reply(published=True)
    prior_phase = s["jobs"].get(o.get("job"), {}).get("phase")
    existing = set(s["jobs"])
    result = latency_handle(s, words, o, io)
    if words == ["report", "start"]:
        for key in set(s["jobs"]) - existing:
            s["jobs"][key]["slot_held"] = True
    if (
        words == ["report", "advance"]
        and result[0].get("phase") == "ready"
        and prior_phase != "ready"
        and not s["late_correction"]
    ):
        req = next(r for r in s["requests"] if r["id"] == "request-0")
        s["requests"].append({**req, "rev": 6, "cancelled": True})
        s["attempts"].append(
            {
                "id": "attempt-5",
                "rev": 7,
                "request": "request-5",
                "finish": s["late_finish"],
                "outcome": "success",
            }
        )
        s["revision"] = "latency-7"
        s["late_correction"] = True
    if words == ["publish"] and result[0].get("published"):
        s["jobs"][o["job"]]["slot_held"] = False
    return result


def latency_v8_check(s):
    failure = latency_check(s)
    if failure:
        return failure
    if not s["late_correction"] or any(j.get("slot_held") for j in s["jobs"].values()):
        return "Drain obsolete worker reservations after the late correction"
    metrics = {"completed": 0, "pending": 0, "latencies": []}
    for r in latency_expected(s).values():
        metrics["completed"] += r["completed"]
        metrics["pending"] += r["pending"]
        metrics["latencies"] += r["latencies"]
    metrics["latencies"].sort()
    dash = copy.deepcopy(s["dashboard"])
    if dash and isinstance(dash.get("metrics", {}).get("latencies"), list):
        dash["metrics"]["latencies"].sort()
    expected = {
        "revision": s["revision"],
        "receipts": {r: s["published"][r]["id"] for r in s["regions"]},
        "metrics": metrics,
    }
    if (
        any(s["published"][r]["revision"] != s["revision"] for r in s["regions"])
        or dash != expected
    ):
        return "The dashboard must combine current regional metrics and bind those exact publication receipts"
    return None


def gold_latency_v8(ws, *, wrong=None, alternate=False):
    _ask(ws, "seal")
    for turn in range(3):
        h = _ask(ws, "head")
        requests = _latest(_scan(ws, ("requests", "list"), revision=h["revision"]))
        attempts = _latest(_scan(ws, ("attempts", "list"), revision=h["revision"]))
        st = _ask(ws, "status")
        for key, job in st["jobs"].items():
            if job.get("slot_held") and job["revision"] != h["revision"]:
                _ask(ws, "report", "cancel", job=key)
        aggregate = {"completed": 0, "pending": 0, "latencies": []}
        for region in sorted(h["regions"], reverse=alternate):
            metrics = {"completed": 0, "pending": 0, "latencies": []}
            for req in requests.values():
                if req["region"] != region or req["cancelled"]:
                    continue
                finishes = [
                    a["finish"]
                    for a in attempts.values()
                    if a["request"] == req["id"]
                    and a["outcome"] == "success"
                    and a["finish"] >= req["start"]
                ]
                if finishes:
                    values = finishes if wrong == "count_attempts" else [min(finishes)]
                    metrics["completed"] += len(values)
                    metrics["latencies"] += [f - req["start"] for f in values]
                else:
                    metrics["pending"] += 1
            job = _put(
                ws,
                ("report", "start"),
                metrics,
                region=region,
                revision=h["revision"],
                key=f"job-{turn}-{region}",
            )["job"]
            _ask(ws, "report", "advance", job=job)
            _ask(ws, "report", "advance", job=job)
            _ask(ws, "publish", region=region, job=job)
            if _ask(ws, "head")["revision"] != h["revision"]:
                break
            aggregate["completed"] += metrics["completed"]
            aggregate["pending"] += metrics["pending"]
            aggregate["latencies"] += metrics["latencies"]
        else:
            if wrong != "regions_only":
                st = _ask(ws, "status")
                _put(
                    ws,
                    ("dashboard", "publish"),
                    {
                        "receipts": {r: st["published"][r]["id"] for r in h["regions"]},
                        "metrics": aggregate,
                    },
                )
            return


def relief_v8_config(variant=0):
    s = _relief_config(variant)
    s["capacity"]["d"] = 3
    s["pending_capacity"] = {"a": 4, "b": 6, "c": 7, "d": 3}
    s.update(
        moved_event=False,
        spare="d",
        next_capacity={"b": 5 if variant == 0 else 6, "c": 6 if variant == 0 else 5},
    )
    return s


def relief_v8_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            sealed=s["sealed"],
            protected=s["protected"],
            spare=s["spare"],
            pending_traffic_change=not s["moved_event"],
        )
    if words == ["job", "cancel"]:
        s["job"] = {"moves": [], "next": 0, "done": True, "revision": s["revision"]}
        s["desired"] = s["observed"].copy()
        s["receipt"] = None
        return _reply(cancelled_remainder=True, observed_unchanged=True)
    if words == ["plan", "apply"] and s["job"] and not s["job"]["done"]:
        return _reply("worker_busy", message="Advance or cancel the existing plan remainder")
    if words == ["job", "advance"] and s["job"] and s["job"].get("revision") != s["revision"]:
        return _reply("plan_source_changed", revision=s["revision"], observed=s["observed"])
    before = s["observed"].copy()
    result = relief_handle(s, words, o, io)
    if words == ["plan", "apply"] and result[0]["status"] == "accepted":
        s["job"]["revision"] = s["revision"]
    if words == ["job", "advance"] and s["observed"] != before and not s["moved_event"]:
        s["capacity"].update(s["next_capacity"])
        s["traffic"]["blue"] = 5
        s["revision"] = "health-10"
        s["moved_event"] = True
        s["receipt"] = None
    return result


def relief_v8_check(s):
    failure = relief_check(s)
    if failure:
        return failure
    if not s["moved_event"] or any(node == s["spare"] for node in s["observed"].values()):
        return "Reconcile the live traffic change and evacuate the temporary spare node"
    return None


def gold_relief_v8(ws, *, wrong=None, alternate=False):
    _ask(ws, "health", "seal")
    for _ in range(3):
        h = _ask(ws, "head")
        st = _ask(ws, "status")
        rows = _scan(ws, ("routes", "list"))
        sizes = {r["route"]: r["load"] for r in rows}
        nodes = sorted(st["capacity"], reverse=alternate)
        queue, seen = [(st["observed"], [])], {tuple(sorted(st["observed"].items()))}
        plan = None
        while queue:
            routes, path = queue.pop(0)
            load = {n: sum(sizes[r] for r, dest in routes.items() if dest == n) for n in nodes}
            if all(load[n] <= st["capacity"][n] for n in nodes) and load[h["spare"]] == 0:
                plan = path
                break
            for r in sorted(sizes, reverse=alternate):
                if r in h["protected"]:
                    continue
                for n in nodes:
                    if (
                        n == routes[r]
                        or load[n] + sizes[r] > st["capacity"][n]
                        or (wrong == "no_spare" and n == h["spare"])
                    ):
                        continue
                    new = {**routes, r: n}
                    key = tuple(sorted(new.items()))
                    if key not in seen:
                        seen.add(key)
                        queue.append((new, path + [{"route": r, "to": n}]))
        if plan is None:
            return
        if st["job"] and not st["job"]["done"]:
            _ask(ws, "job", "cancel")
        _put(ws, ("plan", "stage"), plan, revision=h["revision"])
        canary = _ask(ws, "plan", "canary")
        _ask(ws, "plan", "apply", receipt=canary["receipt"])
        if wrong == "trust_desired":
            return
        for _ in plan:
            _ask(ws, "job", "advance")
            current = _ask(ws, "head")
            if current["revision"] != h["revision"]:
                _ask(ws, "job", "cancel")
                break
        else:
            return


def macro_v8_config(variant=0):
    s = _macro_config(variant)
    s.update(
        dependencies={"welcome": [], "receipt": ["welcome"], "archive": ["receipt"]},
        link_proofs={},
        hotpatch_phase=0,
        hotpatch_prefix="<<" if variant == 0 else "((",
    )
    return s


def macro_v8_eval(s, module, expr, value, stack=()):
    if len(stack) > 20 or not isinstance(expr, dict) or len(expr) != 1:
        raise ValueError("invalid/deep expression")
    if "text" in expr and isinstance(expr["text"], str):
        return expr["text"]
    if "input" in expr and expr["input"] is True:
        return value
    if "concat" in expr and isinstance(expr["concat"], list):
        return "".join(
            macro_v8_eval(s, module, e, value, stack + ("concat",)) for e in expr["concat"]
        )
    if "ref" in expr:
        marker = ("ref", expr["ref"], _digest(module))
        if marker in stack:
            raise ValueError("reference cycle")
        return macro_v8_eval(s, module, module["defs"][expr["ref"]], value, stack + (marker,))
    if "call" in expr:
        call = expr["call"]
        macro = next(
            (
                m
                for m in s["registry"]
                if m["name"] == call["name"] and m["version"] == call["version"] and m["current"]
            ),
            None,
        )
        if macro is None:
            raise ValueError("missing current macro version")
        return (
            macro["prefix"]
            + macro_v8_eval(s, module, call["arg"], value, stack + ("call",))
            + macro["suffix"]
        )
    if "release" in expr:
        call = expr["release"]
        release = s["deployed"][call["module"]]
        if release["receipt"] != call["receipt"] or ("release", call["module"]) in stack:
            raise ValueError("release changed/cyclic")
        arg = macro_v8_eval(s, module, call["arg"], value, stack + ("argument",))
        return macro_v8_eval(
            s,
            release["module"],
            release["module"]["entry"],
            arg,
            stack + (("release", call["module"]),),
        )
    raise ValueError("invalid expression")


def macro_v8_expected(s, key, value):
    req = s["requirements"][key]
    if s["dependencies"][key]:
        value = macro_v8_expected(s, s["dependencies"][key][0], value)
    macro = next(m for m in s["registry"] if m["current"] and m["name"] == req["macro"])
    return macro["prefix"] + value + macro["suffix"] + req["tail"]


def macro_v8_handle(s, words, o, io):
    if words == ["head"]:
        return _reply(
            revision=s["revision"],
            sealed=s["sealed"],
            modules=s["requirements"],
            dependencies=s["dependencies"],
            pending_hotpatches=2 - s["hotpatch_phase"],
        )
    if words in (["module", "compile"], ["module", "test"], ["module", "deploy"]):
        key = o["module"]
        deps = {d: s["deployed"].get(d, {}).get("receipt") for d in s["dependencies"][key]}
        if any(s["deployed"].get(d, {}).get("revision") != s["revision"] for d in deps):
            return _reply("dependency_not_deployed", dependencies=deps)
        if words == ["module", "compile"]:
            if not s["sealed"]:
                return _reply("registry_not_sealed")
            module = s["drafts"][key]
            try:
                for name in module["defs"]:
                    macro_v8_eval(s, module, {"ref": name}, "probe")
                macro_v8_eval(s, module, module["entry"], "probe")
            except (ValueError, KeyError, StopIteration, TypeError):
                return _reply("compile_failed")
            receipt = _digest([key, s["revision"], module, deps])
            s["compiled"][key] = receipt
            s["link_proofs"][key] = deps
            return _reply(receipt=receipt, dependencies=deps, compiled=True)
        if (
            not o.get("receipt")
            or s["compiled"].get(key) != o["receipt"]
            or s["link_proofs"].get(key) != deps
        ):
            return _reply("receipt_or_dependency_changed")
        if words == ["module", "test"]:
            try:
                actual = macro_v8_eval(
                    s, s["drafts"][key], s["drafts"][key]["entry"], s["names"][0]
                )
            except (ValueError, KeyError, StopIteration, TypeError):
                return _reply("sample_failed")
            if actual != macro_v8_expected(s, key, s["names"][0]):
                return _reply("sample_failed")
            s["tested"][key] = o["receipt"]
            return _reply(sample_passed=True, tested_name=s["names"][0])
        if s["tested"].get(key) != o["receipt"]:
            return _reply("receipt_mismatch")
        before = s["deployed"].get(key)
        s["deployed"][key] = {
            "revision": s["revision"],
            "module": copy.deepcopy(s["drafts"][key]),
            "receipt": o["receipt"],
            "dependencies": deps,
        }
        if before != s["deployed"][key] and s["hotpatch_phase"] == 0:
            for macro in s["registry"]:
                if macro["name"] == "quote" and macro["current"]:
                    macro.update(
                        version="v8",
                        prefix=s["hotpatch_prefix"],
                        suffix=">>" if s["hotpatch_prefix"] == "<<" else "))",
                    )
            s["hotpatch_phase"] = 1
            s["revision"] = "macros-8"
            s["compiled"].clear()
            s["tested"].clear()
        elif key == "receipt" and before != s["deployed"][key] and s["hotpatch_phase"] == 1:
            s["requirements"]["welcome"]["tail"] = " welcomed"
            s["hotpatch_phase"] = 2
            s["revision"] = "macros-9"
            s["compiled"].clear()
            s["tested"].clear()
        return _reply(
            "outcome_unknown",
            message="Deployment committed; registry or provider requirements may have changed",
        )
    return macro_handle(s, words, o, io)


def macro_v8_check(s):
    if not s["sealed"] or s["hotpatch_phase"] != 2 or set(s["deployed"]) != set(s["requirements"]):
        return "Deploy the complete module graph after registry and provider corrections"
    for key, deployed in s["deployed"].items():
        deps = {d: s["deployed"][d]["receipt"] for d in s["dependencies"][key]}
        if deployed["revision"] != s["revision"] or deployed["dependencies"] != deps:
            return "Consumer link receipts must name the current deployed providers"
        try:
            for value in s["names"]:
                if macro_v8_eval(
                    s, deployed["module"], deployed["module"]["entry"], value
                ) != macro_v8_expected(s, key, value):
                    return "Every name must render the entire composed provider chain"
        except (ValueError, KeyError, StopIteration, TypeError):
            return "A deployed release reference is stale or invalid"
    return None


def gold_macro_v8(ws, *, wrong=None, alternate=False):
    _ask(ws, "registry", "seal")
    names = _scan(ws, ("names", "list"))
    for _turn in range(4):
        h = _ask(ws, "head")
        macros = {r["name"]: r for r in _scan(ws, ("registry", "list")) if r["current"]}
        pending = set(h["modules"])
        expressions = {}
        while pending:
            key = next(
                k for k in sorted(pending) if all(d not in pending for d in h["dependencies"][k])
            )
            pending.remove(key)
            req = h["modules"][key]
            m = macros[req["macro"]]
            arg = {"input": True}
            if h["dependencies"][key]:
                dep = h["dependencies"][key][0]
                if alternate:
                    arg = copy.deepcopy(expressions[dep])
                else:
                    release = _ask(ws, "status")["deployed"][dep]
                    arg = {
                        "release": {
                            "module": dep,
                            "receipt": release["receipt"],
                            "arg": {"input": True},
                        }
                    }
            expr = (
                {"concat": [{"text": m["prefix"]}, arg, {"text": m["suffix"] + req["tail"]}]}
                if alternate
                else {
                    "concat": [
                        {"call": {"name": m["name"], "version": m["version"], "arg": arg}},
                        {"text": req["tail"]},
                    ]
                }
            )
            expressions[key] = copy.deepcopy(expr)
            if wrong == "sample_only":
                # Build the correct sample constant from public requirements.
                value = names[0]
                chain = [key]
                while h["dependencies"][chain[-1]]:
                    chain.append(h["dependencies"][chain[-1]][0])
                for part in reversed(chain):
                    r = h["modules"][part]
                    mm = macros[r["macro"]]
                    value = mm["prefix"] + value + mm["suffix"] + r["tail"]
                expr = {"text": value}
            _put(ws, ("module", "stage"), {"defs": {}, "entry": expr}, module=key)
            compiled = _ask(ws, "module", "compile", module=key)
            if "receipt" not in compiled:
                return
            _ask(ws, "module", "test", module=key, receipt=compiled["receipt"])
            _ask(ws, "module", "deploy", module=key, receipt=compiled["receipt"])
            if _ask(ws, "head")["revision"] != h["revision"]:
                break
        if wrong == "frozen_plan" or _ask(ws, "head")["revision"] == h["revision"]:
            return


def document_v8_config(variant=0):
    s = _document_config(variant)
    s["targets"]["draft-1"]["source"] = "draft-0"
    s["targets"]["draft-2"]["source"] = "draft-1"
    s.update(notes={key: [] for key in s["targets"]}, collaborator_events=[], foreign_serial=1)
    return s


def document_v8_expected(s, key):
    recipe = s["targets"][key]
    source = s["pinned"].get(recipe["source"], s["heads"].get(recipe["source"]))
    if source is None:
        return None
    value = s["versions"][source][:]
    for raw in recipe["ops"]:
        op = {**raw, "source": source} if raw["kind"] == "copy" else raw
        value = document_apply(value, op, s["versions"])
    return value + s["notes"][key]


def document_v8_foreign_append(s, doc, note):
    value = s["versions"][s["heads"][doc]] + [note]
    version = "collaborator-" + str(s["foreign_serial"])
    s["foreign_serial"] += 1
    s["versions"][version] = value
    s["heads"][doc] = version
    s["notes"][doc].append(note)


def document_v8_handle(s, words, o, io):
    if words == ["document", "get"]:
        doc = o["doc"]
        version = s["heads"].get(doc)
        return _reply(
            doc=doc,
            version=version,
            lines=s["versions"].get(version),
            protected_suffix=s["notes"].get(doc, []),
        )
    if words == ["branch"] and o["doc"] in s["targets"]:
        body = {"doc": o["doc"], "source": o["source"], "revision": o["revision"]}
        prior = _prior(s, o["key"], body)
        if prior:
            return prior, 0
        target = s["versions"].get(o["source"], [])
        notes = s["notes"][o["doc"]]
        if notes and target[-len(notes) :] != notes:
            return _reply("foreign_suffix_conflict")
        result = document_handle(s, words, o, io)
        if result[0]["status"] == "ok" and not notes:
            document_v8_foreign_append(s, o["doc"], "owner-note:" + o["doc"])
            upstream = s["targets"][o["doc"]]["source"]
            if upstream in s["targets"]:
                document_v8_foreign_append(s, upstream, "dependency-install:" + o["doc"])
        return result
    if words == ["edit"] and o["doc"] in s["targets"]:
        ops = _body(o, io)
        body = {"doc": o["doc"], "base": o["base"], "ops": ops}
        old = _prior(s, o["key"], body)
        if old:
            return old, 0
        doc = o["doc"]
        if s["heads"].get(doc) != o["base"]:
            return _reply("base_conflict", current=s["heads"].get(doc))
        applied = 0
        receipt_version = s["heads"][doc]
        reason = None
        for op in ops[:2]:
            previous = s["versions"][s["heads"][doc]]
            try:
                value = document_apply(previous, op, s["versions"])
            except (KeyError, ValueError, TypeError):
                reason = "invalid_operation"
                break
            notes = s["notes"][doc]
            if notes and (len(value) < len(notes) or value[-len(notes) :] != notes):
                reason = "foreign_suffix_conflict"
                break
            receipt_version = "version-" + str(s["serial"])
            s["serial"] += 1
            s["versions"][receipt_version] = value
            s["heads"][doc] = receipt_version
            applied += 1
            if value != previous and doc not in s["collaborator_events"]:
                s["collaborator_events"].append(doc)
                document_v8_foreign_append(s, doc, "reviewer-note:" + doc)
                source_doc = s["targets"][doc]["source"]
                if source_doc in s["targets"]:
                    document_v8_foreign_append(s, source_doc, "dependency-review:" + doc)
                reason = "concurrent_write"
                break
        result = {
            "status": "ok" if applied == len(ops) and reason is None else "partial",
            "doc": doc,
            "applied": applied,
            "version": receipt_version,
            "reason": reason,
        }
        _remember(s, o["key"], body, result)
        return _reply(
            "outcome_unknown",
            key=o["key"],
            message="Own durable prefix recorded; receipt.version is the last own write, not necessarily current HEAD",
        )
    return document_handle(s, words, o, io)


def document_v8_check(s):
    if s["violations"]:
        return "Protected source heads may never be edited or rebranched"
    if not s["sealed"] or any(s["heads"][k] != s["pinned"][k] for k in s["protected"]):
        return "Pin and preserve the original sources"
    for key in s["targets"]:
        if s["versions"].get(s["heads"].get(key)) != document_v8_expected(s, key):
            return "Target recipes use current upstream document content and must retain their own collaborator suffix"
    return None


def gold_document_v8(ws, *, wrong=None, alternate=False):
    h = _ask(ws, "head")
    pin = _ask(ws, "pin")
    pending = set(h["targets"])
    serial = 0
    while pending:
        doc = next(
            k
            for k in sorted(pending, reverse=alternate)
            if h["targets"][k]["source"] not in pending
        )
        pending.remove(doc)
        recipe = h["targets"][doc]
        frozen_source = None
        for _ in range(6):
            source = _ask(ws, "document", "get", doc=recipe["source"])
            frozen_source = frozen_source or source
            if wrong == "frozen_dependency":
                source = frozen_source
            current = _ask(ws, "document", "get", doc=doc)
            if current["version"] is None:
                _ask(
                    ws,
                    "branch",
                    doc=doc,
                    source=source["version"],
                    revision=pin["revision"],
                    key="branch-" + doc,
                )
                current = _ask(ws, "document", "get", doc=doc)
            body = source["lines"][:]
            for raw in recipe["ops"]:
                op = {**raw, "source": source["version"]} if raw["kind"] == "copy" else raw
                body = document_apply(body, op, {source["version"]: source["lines"]})
            notes = current["protected_suffix"]
            if current["lines"] == body + notes:
                break
            prefix_length = len(current["lines"]) - len(notes)
            ops = [{"kind": "delete", "at": 0, "count": prefix_length}] if prefix_length else []
            ops.append({"kind": "insert", "at": 0, "lines": body})
            if alternate:
                ops = ops[:1]
            serial += 1
            result = _put(
                ws, ("edit",), ops, doc=doc, base=current["version"], key=f"edit-v8-{serial}"
            )
            if wrong == "receipt_is_head":
                receipt = _ask(ws, "operation", "status", key=f"edit-v8-{serial}")["receipt"]
                if result["status"] == "outcome_unknown":
                    _put(
                        ws,
                        ("edit",),
                        ops[receipt["applied"] :],
                        doc=doc,
                        base=receipt["version"],
                        key=f"suffix-{serial}",
                    )
                    return


SPECS = {
    472: (
        _incident_config,
        incident_handle,
        (),
        incident_check,
        gold_incident,
        INCIDENT_DOC,
        "Опубликуй актуальные списки открытых и срочных открытых инцидентов для всех команд.",
    ),
    473: (
        _stream_config,
        stream_handle,
        (stream_desired,),
        stream_check,
        gold_stream,
        STREAM_DOC,
        "Восстанови поток после rollback: доведи engine до конца источника, сохрани checkpoint и согласуй внешний sink.",
    ),
    474: (
        _session_config,
        session_handle,
        (),
        session_check,
        gold_session,
        SESSION_DOC,
        "Закрой все пакеты сессий по актуальным событиям и исправленным часам; активируй полные отчёты.",
    ),
    475: (
        _capacity_config,
        capacity_handle,
        (capacity_free,),
        capacity_check,
        gold_capacity,
        CAPACITY_DOC,
        "Забронируй и подтверди все составные заявки; каждая требует одновременного доступа ко всем ресурсам.",
    ),
    476: (
        _warehouse_config,
        warehouse_handle,
        (warehouse_options,),
        warehouse_check,
        gold_warehouse,
        WAREHOUSE_DOC,
        "Доведи складские заказы до фактической отправки по актуальному каталогу замен, без дублей и оставшихся резервов.",
    ),
    477: (
        _transaction_config,
        transaction_handle,
        (),
        transaction_check,
        gold_transaction,
        TRANSACTION_DOC,
        "Восстанови прерванный импорт проводок: текущие маршруты, ровно один эффект на намерение, полный checkpoint.",
    ),
    478: (
        _restore_config,
        restore_handle,
        (),
        restore_check,
        gold_restore,
        RESTORE_DOC,
        "Восстанови и опубликуй весь текущий снимок; каждый объект должен быть проверен по собственному manifest receipt.",
    ),
    479: (
        _repair_config,
        repair_handle,
        (),
        repair_check,
        gold_repair,
        REPAIR_DOC,
        "Заверши возобновляемый ремонт объектов после потери подтверждений; опубликуй проверенные данные и освободи квоту.",
    ),
    480: (
        _identity_config,
        identity_handle,
        (),
        identity_check,
        gold_identity,
        IDENTITY_DOC,
        "Пересобери и опубликуй текущие группы контактов по исправленному журналу связей, включая распавшиеся группы.",
    ),
    481: (
        _privacy_config,
        privacy_handle,
        (privacy_flush,),
        privacy_check,
        gold_privacy,
        PRIVACY_DOC,
        "Выполни запросы удаления контактных данных по всем актуальным и архивным алиасам, сохрани остальные записи.",
    ),
    482: (
        _latency_config,
        latency_handle,
        (),
        latency_check,
        gold_latency,
        LATENCY_DOC,
        "Опубликуй актуальные региональные отчёты о задержках запросов; заверши реальные задания публикации.",
    ),
    483: (
        _relief_config,
        relief_handle,
        (relief_loads, relief_simulate),
        relief_check,
        gold_relief,
        RELIEF_DOC,
        "Сними перегрузку текущей маршрутизации безопасными перемещениями и дождись фактического применения плана.",
    ),
    484: (
        _macro_config,
        macro_handle,
        (macro_eval,),
        macro_check,
        gold_macro,
        MACRO_DOC,
        "Собери, проверь и разверни шаблонные модули по актуальному реестру макросов для всех входных имён.",
    ),
    485: (
        _document_config,
        document_handle,
        (document_apply,),
        document_check,
        gold_document,
        DOCUMENT_DOC,
        "Создай все редакции документов по рецептам от закреплённого оригинала; сохрани исходники и согласуй частичные коммиты.",
    ),
}


V8_DOCS = {
    472: """
Goal: publish every team's current open/urgent-open issue lists AND make the task
alert set match urgent-open issue ownership. Retain the foreign monitor alert.
head returns revision,teams,sealed,pending_publication_events. seal --revision R
flushes the initial pending corrections and clears staged reports; repeat with
current R is safe. This initial barrier does not disable the following writers:
the first changed team publication transfers issue-09 to a new team; the first
nonempty task-alert replacement after that transfer closes issue-04. Each event
advances the live revision and clears drafts; already published reports/alerts
remain durable and may be obsolete. No reads or identical publication-key
replays trigger these events. Both events must be reflected in the final sinks.
issues list --revision R [--cursor C] returns append-only {id,rev,team,state,
priority,deleted?}; greatest rev per id wins, tombstones remove ids, old arrivals
do not undo corrections. cache-r1 is obsolete. Open means state=open; urgent
means additionally priority=urgent. reports stage --file {team:{revision,open:
[id],urgent:[id]}} accepts any subset. reports publish --team T --revision R
--key K publishes that current staged team; it can lose its ACK after commit.
Publishing before seal performs the initial barrier and returns revision_conflict.
reports status returns published/staged. alerts status returns generation and
items:{id:{owner,team,issue}}. alerts replace --revision R --generation G --file
{issue_id:{team,issue}} replaces ONLY task-owned alerts with CAS; keys belonging
to another owner are rejected. Omit obsolete task alerts to cancel them. Report
publication and alert replacement are separate durable operations, not atomic.
Equivalent ordering/batching and safe reads are accepted; no exact trace is graded.
""",
    473: """
Goal: apply and checkpoint every current offset and reconcile only the task's
external sink contribution to all closed windows. Preserve the other writer.
head returns revision,end_offset,lateness,checkpoint,pending_producer_batches.
feed list --revision R [--cursor C] returns offset,partition,ts,kind=row|heartbeat;
rows also have id,rev,deleted?. Greatest row rev wins; ignore deleted rows.
engine status gives epoch,engine:{offset,rows,high},watermark,checkpoint_offset.
Global watermark=min(high of EVERY registered partition)-lateness. Window
[10*k,10*k+10) is closed only when its end<=watermark; count latest nondeleted
rows. engine apply --epoch E --file [events] accepts contiguous exact feed rows;
old exact offsets are safe; a gap/conflict leaves its earlier batch prefix
durable. Rows and heartbeats advance their partition high by max(old,ts).
engine checkpoint persists engine only. engine rollback restores it and bumps
epoch, never rewinding sink. sink status exposes generation,counts (all writers),
task_counts and foreign_counts. sink adjust --scope task --generation G --key K
--file {window:integer_delta} changes task_counts only with CAS; zero totals
are removed. A lost ACK follows commit; inspect status or replay the exact key.
Producer handoff occurs only on a NEW sink commit after engine has consumed the
current end_offset and task_counts equals its closed windows. The first such
commit registers partition d at high=0, appends a row
correction and d heartbeat, and crashes engine/checkpoint back to the documented
initial offset2 recovery checkpoint. Sink survives. The second such sink commit
appends a tombstone plus heartbeats for a,b,c,d. These change source revision;
head/feed expose exact new facts. Old cursors expire. Key replay releases no
producer batch. Sink must reflect the temporarily retreating global watermark
as well as the final source. Any valid checkpoint/batch/replay strategy passes.
""",
    474: """
Goal: activate current closeouts for every batch, retaining its current reviewer
annotation exactly. head -> revision,batches,sealed; clocks -> revision,offsets,
where UTC=local-offset. events list --revision R [--cursor C] returns revision
rows {id,rev,batch,start,start_zone,end,end_zone,limit,cancelled}. Greatest rev
wins; omit cancelled sessions; elapsed=UTC(end)-UTC(start), late=(elapsed>limit).
Integers and booleans are distinct. annotations returns items:{batch:{revision,
text}} owned by the reviewer. seal --revision R applies the initial correction
to west's clock, changes both revisions and clears drafts; it does not prevent
later closeout-time corrections. The first changed activation cancels session-0,
adds3 to east's offset, changes source/clock revisions and changes one reviewer's
annotation. Old active reports remain and must be reconciled. No reads trigger it.
closeout stage --revision R --clocks CR --annotations JSON --file {batch:[{id,
elapsed,late},...]} requires current clock/source and an exact annotation object
for each supplied batch in JSON. Staging stores those reviewer annotations with
the report; annotation_conflict has no effect. closeout activate --batch B
requires a current staged source/clock pair. Activation before initial seal
flushes that barrier and returns dependency_changed. closeout status returns
active reports/draft names; acceptance alone is not a full closeout validation.
Any batch/row order and staging sizes preserving the current notes are accepted.
""",
    475: """
Goal: commit every resource bundle within its allowed slots and strictly AFTER
each named predecessor, leave no partial holds, preserve maintenance, and meet final resource capacity. head returns slots,capacity,maintenance,revision,
generation,pending_maintenance. bundles list returns id,resources,allowed,after.
availability returns generation and free[resource][slot_index], subtracting
maintenance, all holds and all committed bundles. Reserve/commit are separate.
reserve --bundle B --slot S --generation G --key K holds each available required
resource independently. Partial holds are durable and consume capacity; S must
be allowed. One bundle cannot hold two slots. Stale generation changes nothing.
release --bundle B clears only uncommitted holds. commit --bundle B --receipt K
requires that reservation key to match B's complete live resource map and each
predecessor already committed at an earlier slot. Commit can lose its ACK.
status returns holds,committed,generation. uncommit --bundle B compensates ONLY
that task booking, but rejects while any committed dependent still names B.
The first changed commit reserves its first listed resource for maintenance at
the committed slot, and adds a separate mic maintenance allocation; head exposes
both slots/amounts and advances revision/generation. Existing own allocations
and holds remain durable, even if now incompatible. Resolving conflicts may
require compensating committed dependents before their prerequisite. Maintenance
has no cancellation command. The original reservation key stays consumed after
release/uncommit; replay returns its old receipt, not a new reservation. Any
globally feasible schedule and safe compensation order are accepted, no optimum
or exact number/order of calls is required. Readiness of one bundle is not proof
that the remaining requests can fit.
""",
    476: """
Goal: reconcile all inbound inventory and fulfill each current order exactly
once with currently approved substitutions; leave no reservations and preserve
the foreign sealed pallet. Returned shipments do not count as active fulfillment.
head returns revision,warehouses,catalog (component→approved SKU alternatives).
orders list is paginated, each {id,need:{component:quantity}}. inventory reconcile
--warehouse W releases its pending arrivals once. quote --order O gives current
generation and feasible {warehouse,picks:{sku:quantity},fee} options, using the
first sufficiently stocked approved SKU per component. Any returned option is
allowed. reserve --key K --file {order,generation,warehouse,picks,fee} consumes
stock and returns reservation. Stock changes expire ALL quotes. Exact replay
does not reserve again. ship --order O --reservation R --key K consumes only
that order's live reservation and may return outcome_unknown after shipment.
status gives actual shipments with shipment ids, reservations,returns,quarantine,
foreign_stock and pending_recall. First NEW shipment releases a supplier recall:
only sheet-replacement remains approved, new replacement arrivals become pending,
one order's sheet quantity changes to3, revision/generation advance. Already
shipped/reserved goods remain real effects. release --order O --reservation R
returns an unshipped reservation. return --order O --shipment S compensates an
active own shipment and records it in returns. Both require receipt ownership;
approved SKUs re-enter stock, recalled SKUs enter quarantine (never sellable).
Both can lose ACK after compensation. Exact shipping-key replay never ships a
replacement after a return; replacement reservation/shipment uses new operation
keys. No foreign pallet mutation exists. Status, not a green reservation or old
quote, determines current physical effects. Any feasible warehouse/order choice
and equivalent safe reconciliation are accepted.
""",
    477: """
Goal: each CURRENT import intent has exactly one active task ledger posting on
the current account route with exact units; checkpoint end_offset and preserve
the unrelated user credit. head -> revision,end_offset. intents list returns
id,offset,channel,units,key; routes -> revision,routes,sealed. routes seal flushes
initial routes; it is not a permanent lock against later corrections.
import status -> items (active task ledger), reversed (compensated history),
foreign (other writer),checkpoint,revision,correction_pending. summary=accepted
is submission state, not completion. Initial intent0 already survived a crash.
import apply --revision R --routes CR --file [{id,key,account,units},...] requires
current source/route scopes, applies in order, and stops with durable prefix on
wrong route/amount or conflicting key. A whole committed batch may lose ACK.
The source/route preconditions run BEFORE item-key lookup, including retries:
source_changed leaves previous effects intact and processes no items. To replay
old item keys/payloads, use current outer --revision/--routes scopes; alternatively
inspect import status. An old item receipt remains historical, not proof that
its amount/account still matches the current intent.
After the first NEW posting, intent0's amount is corrected and channel a moves
to a new account. Current intents/routes expose exact values, both revisions
advance. Already posted rows remain unchanged, including this batch's prefix.
reverse --id I --key K compensates that active task posting only, preserving its
history and consumed idempotency key; ACK may be lost. It cannot reverse foreign
credits. A reversed operation's original key still replays its old receipt;
it does NOT create its replacement. New operations require new keys. Fresh keys
also create duplicate effects if the old posting was never compensated, so use
the actual item ledger to establish ownership and persistence. checkpoint
--offset N records a claim and imports nothing. Any batching/order and safe
compensation satisfying the current exactly-once outcome pass.
""",
    478: """
Goal: publish every object of the current snapshot with its OWN authenticated
receipt and receipts of current published prerequisites, then release staging.
head -> revision,sealed,stage_quota,dependencies,pending_child_correction. pin
--revision R flushes initial source corrections, clears staged/verified content,
changes revision, and is idempotent on the current cut. manifest list --revision
R [--cursor C] returns object,length,digest. Digest=SHA256(canonical JSON STRING,
UTF8, ensure_ascii=True,separators=(',',':')). fetch --object O --revision R
--offset N returns at most4 characters,data,next_offset,complete. stage --object
O --revision R --offset N --file JSON_STRING accepts at most3 characters, returns
the durable next_offset, invalidates O's verification. Total staged characters
cannot exceed stage_quota; a rejected quota call has no effect. discard --object
O frees only O's staged bytes/verification; published content is retained.
verify --object O requires exact complete source bytes AND all prerequisites
published at current revision. Its receipt binds object/content/source and those
prerequisite receipts. publish --object O --receipt T checks this scope, commits,
automatically releases O's staging, and may lose ACK. status gives received,
verified,published. The first publication of beta releases a correction to alpha
and advances snapshot revision; already published objects stay durable but stale,
verification expires. This is separate from initial pin. Dependency receipts
can therefore expire even when the child's bytes did not change. Restore in any
order/chunks consistent with resource capacity and dependency availability.
""",
    479: """
Goal: publish every current repair object with complete verified bytes and
current prerequisite publications; leave no active jobs holding scratch quota.
head returns objects:{id,length,generation},revision,quota,dependencies and
pending_failover. quota is the total scratch CAPACITY limit, not current usage.
Each active job reserves its FULL object LENGTH, not just received bytes; the
sum of those reservations must fit quota. The capacity stays unchanged when
jobs finish or are cancelled. jobs list is paginated; history can contain
unrelated inactive receipts.
job status --job J exposes id,object,generation,data,received,verified_prefix,
active,dependencies. Resume or cancel the existing partial repair safely.
job start --object O reserves its full length, rejects duplicate active jobs,
and requires each prerequisite published at its current donor generation; it
captures their receipt ids. donor read --object O --generation G --offset N
returns at most5 authoritative characters. job append --job J --offset N --file
JSON_STRING accepts at most3 durably; outcome_unknown omits the new count.
Only that job's received status establishes the next offset, not verified_prefix.
The first NEW append to b changes donor a's content/generation and the overall
revision AFTER receiving the prefix. Existing jobs, dependencies and published
data survive; head/read expose new facts. Reads/replayed offset conflicts do not
trigger failover. job verify --job J verifies the full received prefix only if
donor generation AND captured prerequisite receipts are still current. publish
--object O --job J requires ownership, exact full data and full verification
under current prerequisites; commits a receipt, releases quota, may lose ACK.
status exposes published and active jobs. job cancel --job J releases quota and
keeps published data; cancelled receipts cannot publish. Parent republication
changes the prerequisite receipt even for a child with unchanged donor bytes.
Any safe resume/restart, chunks or topologically feasible work order passes;
there is no prescribed history or requirement to retain speculative work.
""",
    480: """
Goal: publish current connected components of the contact graph AND re-home
every task asset to the lexicographically smallest contact in its component.
Preserve reviewer notes and the foreign asset. head returns contacts,revision,
sealed. seal flushes initial corrections; edges list --revision R [--cursor C]
returns append-only {id,rev,left,right,deleted?}. Greatest rev per EDGE id wins;
tombstones remove edges; all contacts including singletons need coverage.
groups stage --revision R --file [[contact,...],...] stores a candidate and
invalidates its validation receipt. groups validate checks each contact exactly
once, not graph correctness. groups publish --receipt T activates that candidate.
groups status returns active and partition (digest of active source+groups).
assets list is paginated: {id,contact,group,revision,note}. assets move --id I
--group ANCHOR --revision ASSET_REV --partition P requires a current active
partition, membership in that group and the asset's CAS revision. It preserves
notes. Foreign-contact assets are rejected. The first changed task asset move
durably succeeds then releases an appeal: edge-0 is tombstoned, contact4 links
contact9, graph revision advances, candidate/receipt expire, and asset9 receives
a reviewer note/revision. Existing group publication and moved assets remain.
Later reconciliation must respect both the new partition and independently
changed asset row. Lost ACK may follow a move. Source correction does not undo
any asset move. Any correct partition order and safe asset migration sequence
are accepted; a green coverage report alone cannot establish completion.
""",
    481: """
Goal: erase email and phone for every CURRENT or archived alias of requested
subjects, close their current coverage, retain every note and every unrequested
record (including a reassigned alias's new owner). head -> subjects,revision,
fields,sealed. aliases seal flushes initial aliases; aliases list --revision R
[--cursor C] returns subject,record,archived. Archive status is not an exclusion.
record get --id I gives rev,email,phone,note. redact --revision ALIAS_REV --file
[{id,subject,if_rev,fields:["email","phone"]},...] is independently applied per
item only while alias cut, subject ownership and record CAS match. A queued
application edit may raise a record rev before its first attempted write. Read
rejected rows again; applied rows in a partial result remain durable. Each
applied item returns a receipt bound to current alias cut, record id and rev.
After the first applied redaction a concurrent ownership reassignment moves
record1 to an unrequested owner, installs that owner's NEW PII, changes alias
revision and invalidates previous closeouts. This new owner's record must be
preserved; a stale subject scope cannot authorize its erasure. The first close
attempt after reassignment appends an archived alias for record8, advances cut,
clears closed requests and returns aliases_changed. Exact new owners are visible
through current aliases; neither event is driven by reads or call count.
request close --subject S --file [receipt,...] requires exactly S's current
records with current-cut/current-record receipts, no other subject's tokens.
status gives closed coverage and revision. Empty current records may be safely
redacted again for a fresh receipt. Any safe batch/retry/subject order passes.
""",
    482: """
Goal: publish current regional latency reports AND a coherent aggregate dashboard
bound to those publication ids; release all worker reservations. head returns
revision,regions,sealed,worker_limit,pending_correction. seal flushes initial
request/attempt corrections. requests list --revision R and attempts list
--revision R are independently paginated revision logs. Greatest rev wins,
tombstones delete. Request={id,rev,region,start,cancelled}; attempt={id,rev,request,
finish,outcome}. For each latest uncancelled request use its earliest CURRENT
successful finish>=start, one latency=finish-start; otherwise count pending.
Metrics={completed:integer,pending:integer,latencies:[integer,...]} per region.
report start --region R --revision V --key K --file METRICS reserves one worker
and queues a packaging job. report advance --job J changes queued→built→ready;
reads never progress jobs. report status --job J returns the job. On the first
transition to ready, a request cancellation and a recovered attempt arrive,
advancing source revision. The ready job's old source/metrics remain immutable.
report cancel --job J releases an obsolete worker reservation; it does not erase
published output. publish --region R --job J requires matching region, ready
phase and current source, publishes and releases that worker. Exact start-key
replay returns the same historical job, never allocates a replacement. status
returns jobs,published,dashboard. dashboard publish --file {receipts:{region:job
id},metrics:METRICS} requires every referenced current regional publication;
aggregate completed/pending are sums and latencies are the concatenated regional
latencies (any order). Packaging/accepted/ready do not validate arithmetic. Any
region order, safe cancellation/retry and equivalent correct metrics pass.
""",
    483: """
Goal: remove every observed overload, preserve protected routes, keep desired
equal observed, finish the plan worker AND evacuate the temporary spare node.
head -> revision,sealed,protected,spare,pending_traffic_change. health seal applies
initial capacity changes and invalidates drafts. routes list gives route,load,
observed,desired; status gives observed,desired,loads,capacity,job. Whole routes
cannot be split. plan stage --revision R --file [{route,to},...] records current
observed base. plan canary checks every intermediate move: the destination must
fit its current load+whole moved load, and protected routes cannot leave their
fixed nodes. Initially overloaded sources are allowed; safe steps do not certify
that the complete target meets all goals. Canary receipt binds source/base/full
plan. plan apply --receipt T starts one worker and sets desired, not observed.
job advance performs at most one move; lost ACK follows its durable effect.
The first actual movement then increases blue's load to5, changes b/c capacities
as exposed by status, advances health revision and makes the old worker plan
stale. Already moved traffic stays observed. Further old-plan advances reject.
job cancel discards only the remaining plan, sets desired=observed and frees the
worker. New apply rejects while a prior nonfinished worker still exists. A spare
node is ordinary limited capacity for intermediate moves but must be empty at
completion. Any safe feasible sequence, including temporary extra moves, passes;
no globally optimal path or exact canary/read sequence is required.
""",
    484: """
Goal: deploy the whole module DAG for every name, using current registry and
provider releases. head -> revision,modules:{id:{macro,tail}},dependencies,
pending_hotpatches. A providerless module outputs macro.prefix+name+macro.suffix
+tail. A consumer substitutes its sole provider's FULL required output for name
inside that formula, recursively. registry seal flushes initial macro changes;
registry list includes retired versions (only current=true usable); names list
is paginated. The first changed deployment hotpatches quote's current version
and wrappers. The first changed receipt-module deployment after that changes
welcome's required tail. Each changes global revision, invalidates compile/test
receipts, and retains already deployed artifacts. Read head/registry for facts.
module stage --module M --file {defs:{name:EXPR,...},entry:EXPR} invalidates M's
proofs. EXPR has exactly one of text:STRING, input:true, concat:[EXPR,...],
ref:NAME, call:{name:MACRO,version:VERSION,arg:EXPR}, release:{module:M,receipt:T,
arg:EXPR}. ref evaluates a definition; call wraps its evaluated argument;
release evaluates that argument through the named deployed module, requiring
its exact current deployment receipt. Max expansion depth20; every definition,
including unused ones, must be valid and acyclic. Literal inlining of equivalent
current behavior is also allowed. module compile --module M requires direct
providers deployed at current revision and binds source+registry+provider
receipt ids. module test --module M --receipt T tests only the first name.
module deploy --module M --receipt T requires the same current compiled/tested
receipt AND unchanged direct provider receipts. A changed upstream deployment
invalidates consumers even if their own draft was untouched. Deployment can lose
ACK. status lists actual deployed modules,compiled,tested. Any equivalent valid
program and dependency-respecting deployment order is accepted; sample success
alone is not the all-name goal.
""",
    485: """
Goal: create every target recipe from its CURRENT upstream document content,
then append its own protected collaborator suffix. Target dependencies form a
DAG in head.targets:{target:{source,ops}}; the first target uses the pinned
protected original, subsequent targets use the final upstream target version.
All recipe copy operations read the SAME immutable source version used to start
that evaluation, not the edited destination HEAD. Own protected suffix is not
part of the recipe body. Copied upstream notes are ordinary source content.
Pin and preserve protected source heads throughout; editing/rebranching one is
a sticky failure even if restored. Old versions are immutable. head lists
revision,heads,protected,targets. pin applies the initial service-owned source
correction once and returns pinned versions/revision. read --version V is
paginated {index,text}. document get --doc D gives full current version,lines,
protected_suffix; missing targets return null version/lines. These are reads.
branch --doc D --source V --revision R --key K sets a target head to immutable V.
On initial target creation its owner appends owner-note:D, moving HEAD beyond
the branch receipt. For a target upstream, the owner also appends
dependency-install:D to that upstream's protected suffix, changing its HEAD.
Exact branch-key replay does not create another note. It
rejects removal of an existing collaborator suffix. edit --doc D --base V
--key K --file [OPS] needs current HEAD V and processes at most TWO operations,
each creating an immutable version. An invalid operation or foreign-suffix
conflict leaves earlier prefix effects durable. The first content-changing edit
of EACH target is interrupted after its first changed operation by a collaborator
appending reviewer-note:D to D. If D's source is another target, the collaborator
also appends dependency-review:D to that upstream target. Both real writes move
HEAD and extend the relevant protected suffix. This is one semantic event per
target, never a call-count trigger. Even when the interrupted batch fit within
two operations, its remaining suffix was not executed. operation status --key K
returns doc,applied,version,reason for the ORIGINAL own prefix. version identifies
the last OWN write, so can already differ from live HEAD. Exact-key replay never
applies remaining ops; status/document get establish actual current heads.
An edit reply can be outcome_unknown; reconcile the operation receipt AND live
document. Subsequent edits must preserve its entire protected suffix, in order,
at the end of the document. Recipes use zero-based LINE indices: insert
{kind:'insert',at:N,lines:[STRING,...]}, delete {kind:'delete',at:N,count:C},
copy {kind:'copy',at:N,source:VERSION,start:A,stop:B}. Copy inserts source[A:B].
Bounds are half-open valid source bounds and at in[0,current_length]. Recipe
copy ops omit source; supply the immutable upstream evaluation version. Any
equivalent edit sequence/batching and safe dependency order passes; no exact
author history is required, and no automatic merge/reconcile command exists.
Additional unprotected scratch documents are allowed; they have no collaborator
events and are not part of the final target set. Installing a completed scratch
version still performs the target's initial owner/upstream write described above.
""",
}

_V8_IMPLEMENTATIONS = {
    472: (
        incident_v8_config,
        incident_v8_handle,
        (incident_handle,),
        incident_v8_check,
        gold_incident_v8,
    ),
    473: (
        stream_v8_config,
        stream_v8_handle,
        (stream_desired, stream_handle),
        stream_v8_check,
        gold_stream_v8,
    ),
    474: (
        session_v8_config,
        session_v8_handle,
        (session_handle,),
        session_v8_check,
        gold_session_v8,
    ),
    475: (
        capacity_v8_config,
        capacity_v8_handle,
        (capacity_free, capacity_handle),
        capacity_v8_check,
        gold_capacity_v8,
    ),
    476: (
        warehouse_v8_config,
        warehouse_v8_handle,
        (warehouse_options, warehouse_handle),
        warehouse_v8_check,
        gold_warehouse_v8,
    ),
    477: (
        transaction_v8_config,
        transaction_v8_handle,
        (transaction_handle,),
        transaction_v8_check,
        gold_transaction_v8,
    ),
    478: (
        restore_v8_config,
        restore_v8_handle,
        (restore_handle,),
        restore_v8_check,
        gold_restore_v8,
    ),
    479: (repair_v8_config, repair_v8_handle, (repair_handle,), repair_v8_check, gold_repair_v8),
    480: (
        identity_v8_config,
        identity_v8_handle,
        (identity_handle,),
        identity_v8_check,
        gold_identity_v8,
    ),
    481: (
        privacy_v8_config,
        privacy_v8_handle,
        (privacy_flush, privacy_handle),
        privacy_v8_check,
        gold_privacy_v8,
    ),
    482: (
        latency_v8_config,
        latency_v8_handle,
        (latency_handle,),
        latency_v8_check,
        gold_latency_v8,
    ),
    483: (
        relief_v8_config,
        relief_v8_handle,
        (relief_loads, relief_simulate, relief_handle),
        relief_v8_check,
        gold_relief_v8,
    ),
    484: (
        macro_v8_config,
        macro_v8_handle,
        (macro_eval, macro_handle, macro_v8_eval, macro_v8_expected),
        macro_v8_check,
        gold_macro_v8,
    ),
    485: (
        document_v8_config,
        document_v8_handle,
        (document_apply, document_handle, document_v8_foreign_append),
        document_v8_check,
        gold_document_v8,
    ),
}
V8_PROMPTS = {
    472: "Согласуй текущие отчёты об инцидентах и адресованные командам срочные уведомления, сохранив чужой монитор.",
    473: "Восстанови поток, обработай коррекции и зарегистрированные partitions, сохрани checkpoint и согласуй task-вклад sink, не затронув другого автора.",
    474: "Активируй текущие closeout отчёты по исправленным часам и событиям, сохранив актуальные reviewer annotations.",
    475: "Подтверди все составные заявки в допустимые слоты с учётом предшественников и живого maintenance; устрани конфликты собственных броней.",
    476: "Доведи текущие складские заказы до корректных отправок после отзыва товара; согласуй возвраты, карантин, приходы и резервы.",
    477: "Согласуй ledger с текущими исправленными намерениями импорта: каждый эффект ровно один раз, нужные компенсации, сохранность чужого credit и полный checkpoint.",
    478: "Восстанови согласованный текущий снимок с проверенными объектами и актуальными dependency receipts в пределах staging quota.",
    479: "Заверши ремонт зависимых объектов после donor failover, опубликуй согласованные проверенные поколения и освободи scratch quota.",
    480: "Пересобери текущие группы контактов и перенеси их assets с учётом appeal, сохранив чужие assets и reviewer notes.",
    481: "Закрой запросы удаления PII по текущим и архивным алиасам; учти смену владельца и сохрани новые данные незапрошенного владельца.",
    482: "Опубликуй текущие региональные latency reports и согласованный dashboard, обработав поздние коррекции и освободив worker slots.",
    483: "Сними наблюдаемую перегрузку при изменении traffic/capacity, сохрани protected routes и освободи временный spare node.",
    484: "Разверни весь граф шаблонных модулей с актуальными макросами и provider releases; проверь составное поведение на всех именах.",
    485: "Создай зависимые редакции документов по рецептам от текущих upstream версий; сохрани закреплённые исходники и чужие suffix, согласуй durable partial edits и изменяющийся HEAD.",
}
SPECS = {
    number: (*implementation, COMMON_DOC + V8_DOCS[number], V8_PROMPTS[number])
    for number, implementation in _V8_IMPLEMENTATIONS.items()
}


def make_task(number: int, *, variant: int = 0):
    factory, handler, dependencies, check, gold, docs, prompt = SPECS[number]
    config = factory(variant)
    config["documentation"] = docs
    functions = (
        _reply,
        _body,
        _digest,
        _pages,
        _latest,
        _remember,
        _prior,
        *dependencies,
        handler,
        _service_initial,
        _service_handle,
    )
    source = "import copy\nimport hashlib\nimport json\n\n" + "\n\n".join(
        inspect.getsource(f) for f in functions
    )
    source += f"\nCONFIG = {config!r}\nFAMILY_HANDLE = {handler.__name__}\ninitial_state = _service_initial\nhandle = _service_handle\n"
    base = next(t for t in LEGACY_TASKS if t.id.startswith(f"task_{number}_"))
    return interactive_task(
        base,
        prompt=prompt,
        docs=docs,
        source=source,
        check=check,
        gold=gold,
        files={"cache.json": json.dumps(config.get("cache", {}), indent=2) + "\n"},
        tags=("data-workflow",),
    )


TASKS = [make_task(number) for number in sorted(SPECS)]
