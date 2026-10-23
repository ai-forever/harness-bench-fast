"""Public-transport acceptance and independent workflow regression witnesses."""

from __future__ import annotations

import copy
import itertools
import json
from contextlib import contextmanager

import pytest

from harness_bench.feasibility_tasks.trusted_service import get_session, trusted_service_available
from harness_bench.general_hard import interactive_systems as s

pytestmark = pytest.mark.skipif(
    not trusted_service_available(), reason="Unix socket transport unavailable"
)


@contextmanager
def scenario(number, workspace, *, variant=0):
    task = s.build_task(number, variant=variant)
    task.setup(workspace)
    try:
        yield task
    finally:
        task.cleanup(workspace)


def command(ws, verb, **options):
    args = [verb]
    for key, value in options.items():
        args.extend(("--" + key.replace("_", "-"), str(value)))
    return s.send(ws, *args)


def payload(ws, verb, data):
    return command(ws, verb, file=s.put(ws, "request.json", data))


def stage_copy(ws, identity, slot):
    state = command(ws, "catalog")
    source = next(path for path, row in state["places"].items() if row["id"] == identity)
    for offset in range(state["objects"][identity]["size"]):
        response = command(
            ws,
            "copy",
            id=identity,
            slot=slot,
            source=source,
            revision=state["objects"][identity]["revision"],
            offset=offset,
        )
        if response["status"] != "copied":
            return response
    return command(ws, "verify", slot=slot)


def publish_copy(ws, identity, slot, **extra):
    state = command(ws, "catalog")
    target = state["targets"][identity]
    return payload(
        ws,
        "publish",
        {
            "moves": [{"id": identity, "slot": slot, "target": target, **extra}],
            "epochs": {target: state["epochs"][target]},
        },
    )


def copy_file(ws, identity):
    state = command(ws, "catalog")
    row = state["sources"][identity]
    accepted = command(ws, "copy", id=identity, path=row["path"], revision=row["rev"])
    if "job" not in accepted:
        return accepted
    while (response := command(ws, "job", id=accepted["job"]))["phase"] == "pending":
        pass
    return response


def stage_package(ws, name, version):
    state = command(ws, "status")
    return payload(
        ws,
        "stage",
        {
            "rows": [
                {
                    "package": name,
                    "version": version,
                    "revision": state["packages"][name]["revision"],
                    "digest": state["artifacts"][name][str(version)],
                }
            ]
        },
    )


def activate_packages(ws, names):
    state = command(ws, "status")
    return payload(
        ws, "activate", {"receipts": [state["staged"][name]["receipt"] for name in names]}
    )


def feed(ws):
    events = []
    offset = 0
    while offset is not None:
        page = command(ws, "feed", offset=offset)
        events.extend(page["events"])
        offset = page["next"]
    return events


def apply_event(ws, event):
    state = command(ws, "status")
    rows = {
        row["account"]: {"key": key, "version": row["version"]}
        for key, row in state["rows"].items()
        if row["account"] in event["accounts"]
    }
    return payload(ws, "apply", {"events": [{"id": event["id"], "rows": rows}]})


def edit(ws, scope, operations):
    state = command(ws, "catalog")
    return payload(
        ws, "edit", {"scope": scope, "revision": state["revision"][scope], "ops": operations}
    )


def pin(ws, identity):
    opened = command(ws, "open", id=identity)
    return command(ws, "pin", id=identity, revision=opened["revision"])


def transfer(ws, number, identity, pins, *, foreign=False):
    state = command(ws, "status")
    opened = command(ws, "open", id=identity)
    values = {
        field: pins[source]["values"][field]
        for field, source in state["transfers"][identity].items()
    }
    data = {
        "id": identity,
        "revision": opened["revision"],
        "pins": {name: row["receipt"] for name, row in pins.items()},
    }
    if number == 496:
        doc = opened["document"]
        doc["payload"] = values
        doc["heading"] = "release-" + identity
        doc["tags"].append("shipped")
        if foreign:
            doc["owner"] = "cached-owner"
        return payload(ws, "save", {**data, "document": doc})
    edits = [
        {
            "line": anchor,
            "old": next(row["text"] for row in opened["document"]["lines"] if row["id"] == anchor),
            "new": values[field],
        }
        for anchor, field in (("target", "left"), ("other", "right"))
    ]
    if foreign:
        edits.append({"line": "shadow", "old": "left-0", "new": "changed"})
    return payload(ws, "patch", {**data, "edits": edits})


@pytest.mark.parametrize("number", range(486, 500))
def test_gold_blank_and_bounded_public_history(number, tmp_path):
    task = s.build_task(number)
    task.setup(tmp_path)
    try:
        assert not task.verify(tmp_path).passed
        task.apply_gold(tmp_path)
        result = task.verify(tmp_path)
        assert result.passed, result.message
        records = get_session(tmp_path).records
        assert 0 < len(records) <= 125, (number, len(records))
        assert all(
            set(record) == {"argv", "files", "response", "code", "terminal", "elapsed_seconds"}
            for record in records
        )
        assert all(record["elapsed_seconds"] is None for record in records)
        assert all(type(record["terminal"]) is bool for record in records)
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", range(486, 500))
def test_other_current_state_alternative_order_and_safe_reads(number, tmp_path):
    task = s.build_task(number, variant=1)
    task.setup(tmp_path)
    try:
        gold = s.FAMILIES[number][2]
        gold(tmp_path, reverse_order=True, extra_reads=True)
        result = task.verify(tmp_path)
        assert result.passed, result.message
        assert len(get_session(tmp_path).records) <= 125
    finally:
        task.cleanup(tmp_path)


def wrong_policy(number, mistake, ws):
    """Public observations identify the decisive failed policy assumption."""
    if number == 486:
        command(ws, "settle", key="foreign-receipt")
        state = command(ws, "status")
        if mistake == 0:
            row = state["items"]["event-0"]
            command(
                ws,
                "dispatch",
                id="event-0",
                key="greedy-0",
                route=row["route"],
                revision=row["revision"],
            )
            assert command(ws, "receipt", key="greedy-0")["status"] == "dependency_blocked"
            row = state["items"]["event-4"]
            assert (
                command(
                    ws,
                    "dispatch",
                    id="event-4",
                    key="greedy-4",
                    route=row["route"],
                    revision=row["revision"],
                )["status"]
                == "escrow_or_lane_busy"
            )
        else:
            row = state["items"]["event-3"]
            command(
                ws,
                "dispatch",
                id="event-3",
                key="route-barrier",
                route=row["route"],
                revision=row["revision"],
            )
            if command(ws, "receipt", key="route-barrier")["status"] == "absent":
                command(ws, "retry", id="event-3", key="route-barrier")
            while command(ws, "receipt", key="route-barrier")["job"]["phase"] != "committed":
                pass
            command(ws, "settle", key="route-barrier")
            old = state["items"]["event-4"]
            assert (
                command(
                    ws,
                    "dispatch",
                    id="event-4",
                    key="stale-route",
                    route=old["route"],
                    revision=old["revision"],
                )["status"]
                == "route_revision_conflict"
            )
    elif number == 487:
        if mistake == 0:
            outcomes = [
                command(ws, "cancel", id=f"event-{i}", key=f"old-event-{i}") for i in (0, 2, 4)
            ]
            assert any(row["status"] == "too_late" for row in outcomes)
            assert command(ws, "status")["items"]["event-2"]["effect"] == 1
        else:
            assert command(ws, "cancel", id="event-5", key="old-event-5")["status"] == "too_late"
            result = command(ws, "reverse", id="event-4", receipt="old-event-4")
            assert result["status"] == "live_consumers" and "event-5" in result["consumers"]
    elif number in (488, 489):
        if mistake == 0:
            copy_file(ws, "f0")
            assert payload(ws, "verify", {"ids": ["f0"]})["status"] == "dependency_not_current"
            copy_file(ws, "f2")
            assert copy_file(ws, "f1")["status"] == "staging_full"
        elif number == 488:
            copy_file(ws, "f5")
            copy_file(ws, "f2")
            state = command(ws, "catalog")
            assert state["sources"]["f5"]["path"] == "moved/f5"
            assert state["staged"]["f5"]["rev"] != state["sources"]["f5"]["rev"]
            assert payload(ws, "verify", {"ids": ["f5"]})["status"] == "source_or_replica_changed"
        else:
            copy_file(ws, "f2")
            state = command(ws, "catalog")
            result = payload(
                ws,
                "replay",
                {
                    "transaction": "tx-AF",
                    "revisions": {name: state["sources"][name]["rev"] for name in ("f0", "f5")},
                },
            )
            assert result["status"] == "participants_not_resident"
            assert command(ws, "cutover")["status"] == "not_current"
    elif number == 490:
        events = feed(ws)
        if mistake == 0:
            assert apply_event(ws, events[0])["status"] == "applied"
            assert apply_event(ws, events[1])["status"] == "resource_conflict"
            assert apply_event(ws, events[2])["status"] == "applied"
            assert apply_event(ws, events[3])["status"] == "causal_gap"
            assert command(ws, "status")["resources"] == {"X": 2, "Z": 1}
        else:
            payloads = []
            state = command(ws, "status")
            for event in (events[1], events[5], events[4]):
                rows = {
                    row["account"]: {"key": key, "version": row["version"]}
                    for key, row in state["rows"].items()
                    if row["account"] in event["accounts"]
                }
                payloads.append({"id": event["id"], "rows": rows})
            result = payload(ws, "apply", {"events": payloads})
            assert result["status"] == "row_conflict" and result["applied"] == ["e1"]
            assert command(ws, "ack", through=8)["status"] == "unsafe_checkpoint"
            command(ws, "undo", id="e1")
            s.predicate_gold_v8(ws)
    elif number == 491:
        state = command(ws, "snapshot")
        cohort = [
            {"key": key, "version": state["rows"][key]["version"]} for key in ("r0", "r1", "r2")
        ]
        if mistake == 0:
            assert (
                payload(
                    ws, "archive", {"generation": state["generation"], "cohorts": [cohort[:2]]}
                )["status"]
                == "reference_gap"
            )
        else:
            assert (
                payload(ws, "archive", {"generation": state["generation"], "cohorts": [cohort]})[
                    "status"
                ]
                == "ack_unknown"
            )
            current = command(ws, "snapshot")
            assert current["rows"]["r0"]["archived"] and not current["rows"]["r0"]["urgent"]
            assert (
                payload(
                    ws,
                    "archive",
                    {"generation": state["generation"], "cohorts": [[{"key": "r3", "version": 1}]]},
                )["status"]
                == "generation_conflict"
            )
    elif number in (492, 493):
        if mistake == 0:
            target = 2 if number == 492 else 1
            stage_package(ws, "base", target)
            assert activate_packages(ws, ["base"])["status"] == "incompatible_running_set"
            assert stage_package(ws, "codec", target)["status"] == "capacity"
        else:
            stage_package(ws, "worker", 3)
            stage_package(ws, "base", 3)
            assert activate_packages(ws, ["base"])["status"] == "outcome_unknown"
            assert activate_packages(ws, ["worker"])["status"] == "stale_cohort"
    elif number in (494, 495):
        if mistake == 0:
            for tenant in ("red", "canary"):
                result = edit(
                    ws,
                    tenant,
                    [
                        {"op": "set", "key": key, "value": value}
                        for key, value in {"route": "old", "audit": "off"}.items()
                    ],
                )
                assert result["status"] == ("ok" if tenant == "red" else "shadow_capacity")
            assert (
                edit(ws, "prod", [{"op": "set", "key": "audit", "value": "on"}])["status"]
                == "would_change_protected_effective"
            )
        else:
            before = command(ws, "catalog")
            edit(ws, "blue", [{"op": "delete", "key": "route"}])
            after = command(ws, "catalog")
            assert after["must_stay"] != before["must_stay"]
            result = payload(
                ws,
                "move-group",
                {
                    "tenant": after["must_stay"],
                    "group": "ops",
                    "membership_revision": after["membership_revision"],
                },
            )
            assert result["status"] == "membership_policy"
            result = payload(
                ws,
                "clear-mask",
                {
                    "tenant": "blue",
                    "revision": after["mask_revision"]["blue"],
                    "keys": list(after["masks"]["blue"]),
                },
            )
            assert result["status"] == "forbidden"
            s.config_gold_v8(ws)
    elif number in (496, 497):
        if mistake == 0:
            pins = {"d1": pin(ws, "d1")}
            assert transfer(ws, number, "d0", pins)["status"] == "would_destroy_unconsumed_source"
            pins["d0"] = pin(ws, "d0")
            assert transfer(ws, number, "d0", pins)["status"] == "saved"
            assert command(ws, "unpin", id="d0")["status"] == "original_still_needed"
            assert pin(ws, "d2")["status"] == "pin_capacity"
        else:
            pins = {name: pin(ws, name) for name in ("d0", "d1")}
            assert transfer(ws, number, "d0", pins, foreign=True)["status"] == "forbidden"
            command(ws, "unpin", id="d0")
            command(ws, "unpin", id="d1")
            s.merge_gold_v8(ws)
    elif number == 498:
        state = command(ws, "catalog")
        if mistake == 0:
            for name in state["targets"]:
                result = s.send(
                    ws,
                    "reserve",
                    "--id",
                    name,
                    "--from",
                    state["names"][name],
                    "--to",
                    state["targets"][name],
                    "--revision",
                    1,
                )
                assert (
                    payload(ws, "prepare-references", {"tickets": [result["ticket"]]})["status"]
                    == "namespace_collision"
                )
                command(ws, "discard", ticket=result["ticket"])
        else:
            ticket = s.send(
                ws, "reserve", "--id", "A", "--from", "a", "--to", "tmp-blue", "--revision", 1
            )["ticket"]
            proof = payload(ws, "prepare-references", {"tickets": [ticket]})
            assert proof["notice"]["identity"] == "A"
            assert (
                payload(ws, "activate", {"tickets": [ticket], "receipt": proof["receipt"]})[
                    "status"
                ]
                == "stale_receipt"
            )
            assert (
                payload(ws, "prepare-references", {"tickets": [ticket]})["status"]
                == "subscriber_requires_permanent_name"
            )
    elif number == 499:
        if mistake == 0:
            for name in "ABCDEF":
                assert stage_copy(ws, name, "s0")["status"] == "verified"
                assert publish_copy(ws, name, "s0")["status"] == "would_lose_object"
                assert command(ws, "drop", slot="s0")["status"] == "dropped"
        else:
            assert stage_copy(ws, "B", "s0")["status"] == "verified"
            assert stage_copy(ws, "A", "s1")["status"] == "verified"
            assert publish_copy(ws, "A", "s1")["status"] == "ack_unknown"
            state = command(ws, "catalog")
            journal = state["journal"]["B"]
            command(
                ws, "refresh", slot="s0", revision=journal["revision"], receipt=journal["receipt"]
            )
            command(ws, "verify", slot="s0")
            assert command(ws, "drop", slot="s0")["status"] == "only_recoverable_copy"
            assert stage_copy(ws, "F", "s1")["status"] == "capacity"
    command(ws, "finish")


@pytest.mark.parametrize("number", range(486, 500))
@pytest.mark.parametrize("mistake", (0, 1))
def test_two_domain_specific_wrong_policies_rejected(number, mistake, tmp_path):
    with scenario(number, tmp_path) as task:
        wrong_policy(number, mistake, tmp_path)
        result = task.verify(tmp_path)
        assert not result.passed, (number, mistake)
        # Expected command errors remain valid, replayable domain evidence.
        assert "invalid trusted history" not in result.message
        assert get_session(tmp_path).protocol_errors == []


@pytest.mark.parametrize("number", range(486, 500))
def test_same_stale_preview_requires_different_current_actions(number, tmp_path):
    preview = "list" if number in (486, 487) else "catalog" if number in (492, 493) else "cached"
    original, changed, blind = (tmp_path / name for name in ("original", "changed", "blind"))
    with scenario(number, original) as task:
        old_preview = command(original, preview)
        task.apply_gold(original)
        assert task.verify(original).passed
        trace = json.loads(json.dumps(get_session(original).records))
    with scenario(number, changed, variant=1) as task:
        assert command(changed, preview) == old_preview
        task.apply_gold(changed)
        assert task.verify(changed).passed
        changed_trace = get_session(changed).records
        assert [(r["argv"], r["files"]) for r in changed_trace] != [
            (r["argv"], r["files"]) for r in trace
        ]
    # A fixed transcript inferred from the old preview fails on the paired state.
    # All calls still use public transport and unchanged public input documents.
    with scenario(number, blind, variant=1) as task:
        for record in trace:
            for name, contents in record["files"].items():
                path = blind / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents, encoding="utf-8")
            s.send(blind, *record["argv"])
        result = task.verify(blind)
        assert not result.passed, number
        assert "invalid trusted history" not in result.message


@pytest.mark.parametrize("number", range(486, 500))
def test_recoverable_command_typos_do_not_poison_correct_workflow(number, tmp_path):
    with scenario(number, tmp_path) as task:
        assert command(tmp_path, "unknown-spelling")["status"] in ("unknown_command", "unknown_id")
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed


@pytest.mark.parametrize("variant", (0, 1))
def test_causal_order_requires_resource_lookahead_and_undo_recovers(variant, tmp_path):
    with scenario(490, tmp_path, variant=variant) as task:
        events = feed(tmp_path)
        by_id = {event["id"]: event for event in events}
        feasible = []
        # Independent exhaustive order oracle; does not call the reference BFS.
        for order in itertools.permutations(by_id):
            done = set()
            resources = {}
            valid = True
            for name in order:
                event = by_id[name]
                if not set(event["parents"]) <= done:
                    valid = False
                    break
                for resource, delta in event["resources"].items():
                    resources[resource] = resources.get(resource, 0) + delta
                if min(resources.values(), default=0) < 0 or sum(resources.values()) > 3:
                    valid = False
                    break
                done.add(name)
            if valid:
                feasible.append(order)
        assert feasible and all(order[0] == ("e0" if variant else "e1") for order in feasible)
        trap = by_id["e1" if variant else "e0"]
        assert apply_event(tmp_path, trap)["status"] == "applied"
        assert command(tmp_path, "undo", id=trap["id"])["status"] == "undone"
        for name in feasible[-1]:
            assert name in apply_event(tmp_path, by_id[name])["applied"]
        command(tmp_path, "ack", through=8)
        command(tmp_path, "finish")
        assert task.verify(tmp_path).passed


@pytest.mark.parametrize("number", (488, 489))
def test_migration_wrong_staging_can_be_discarded_and_replanned(number, tmp_path):
    with scenario(number, tmp_path) as task:
        copy_file(tmp_path, "f0")
        copy_file(tmp_path, "f2")
        assert copy_file(tmp_path, "f1")["status"] == "staging_full"
        command(tmp_path, "drop", id="f2")
        s.migration_gold_v8(tmp_path)
        assert task.verify(tmp_path).passed


def test_copy_first_displacement_requires_repair_and_reverification(tmp_path):
    with scenario(499, tmp_path):
        stage_copy(tmp_path, "A", "s0")
        stage_copy(tmp_path, "F", "s1")
        assert publish_copy(tmp_path, "F", "s1")["status"] == "ack_unknown"
        stage_copy(tmp_path, "E", "s1")
        assert publish_copy(tmp_path, "E", "s1")["status"] == "would_lose_object"
        state = command(tmp_path, "catalog")
        repair = state["journal"]["A"]
        assert (
            command(
                tmp_path,
                "refresh",
                slot="s0",
                revision=repair["revision"],
                receipt=repair["receipt"],
            )["status"]
            == "repaired_unverified"
        )
        assert publish_copy(tmp_path, "E", "s1")["status"] == "would_lose_object"
        assert command(tmp_path, "verify", slot="s0")["status"] == "verified"
        assert publish_copy(tmp_path, "E", "s1")["status"] == "ack_unknown"


@pytest.mark.parametrize("number", (494, 495))
def test_bogus_publication_cannot_pretrigger_controller(number, tmp_path):
    with scenario(number, tmp_path) as task:
        before = command(tmp_path, "catalog")
        result = payload(
            tmp_path,
            "commit",
            {"receipts": {tenant: "invented" for tenant in ("blue", "green", "red", "canary")}},
        )
        assert result["applied"] is False
        assert command(tmp_path, "catalog") == before
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed


@pytest.mark.parametrize("number", (494, 495))
def test_local_audit_plan_distinguishes_rollout_from_full_inheritance(number, tmp_path):
    with scenario(number, tmp_path) as task:
        # All tenants stay in prod. Two one-key protective bindings fit, while
        # two complete two-key bindings would exceed the shared capacity.
        for _ in range(2):
            for tenant in ("blue", "green"):
                state = command(tmp_path, "catalog")
                if "route" in state["layers"][tenant]:
                    edit(tmp_path, tenant, [{"op": "delete", "key": "route"}])
        for tenant in ("red", "canary"):
            assert (
                edit(tmp_path, tenant, [{"op": "set", "key": "route", "value": "old"}])["status"]
                == "ok"
            )
        route = s.manifest_for(number)["expected"]["blue"]["route"]
        assert (
            edit(tmp_path, "prod", [{"op": "set", "key": "route", "value": route}])["status"]
            == "ok"
        )
        for attempt in range(2):
            for tenant in ("blue", "green"):
                state = command(tmp_path, "catalog")
                ops = [{"op": "set", "key": "audit", "value": "on"}]
                if "route" in state["layers"][tenant]:
                    ops.append({"op": "delete", "key": "route"})
                assert edit(tmp_path, tenant, ops)["status"] == "ok"
                state = command(tmp_path, "catalog")
                keys = [key for key in ("route", "audit") if key in state["masks"][tenant]]
                if keys:
                    payload(
                        tmp_path,
                        "clear-mask",
                        {
                            "tenant": tenant,
                            "revision": state["mask_revision"][tenant],
                            "keys": keys,
                        },
                    )
            if number == 495:
                assert command(tmp_path, "validate", tenant="blue")["status"] == "invalid"
                assert not task.verify(tmp_path).passed
                return
            receipts = {
                tenant: command(tmp_path, "validate", tenant=tenant)["receipt"]
                for tenant in ("blue", "green", "red", "canary")
            }
            result = payload(tmp_path, "commit", {"receipts": receipts})
            assert result["applied"] is (attempt == 1)
        assert command(tmp_path, "finish")["status"] == "finished"
        state = command(tmp_path, "catalog")
        assert set(state["groups"].values()) == {"prod"}
        assert state["shadow_used"] == 2
        assert task.verify(tmp_path).passed


@pytest.mark.parametrize("number", (486, 487, 488, 489, 490, 491, 494, 495, 496, 497, 498, 499))
def test_post_finish_writes_leave_completed_state_unchanged(number, tmp_path):
    with scenario(number, tmp_path) as task:
        task.apply_gold(tmp_path)
        before = copy.deepcopy(get_session(tmp_path).state)
        verb = {486: "bind", 487: "settle", 496: "pin", 497: "patch"}.get(number, "edit")
        assert command(tmp_path, verb)["status"] == "closed"
        after = copy.deepcopy(get_session(tmp_path).state)
        # The trusted wrapper accounts for every request, including refusals.
        before.pop("clock", None)
        after.pop("clock", None)
        assert after == before
        assert task.verify(tmp_path).passed
