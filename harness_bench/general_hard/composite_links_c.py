"""Host-only, bounded cross-workflow changes for composite tasks 526--531.

All values exported across a boundary come from public domain results.  Gold
uses the same nested clients as an agent; it never reads the host service state.
"""

from __future__ import annotations

import copy
import json

from . import interactive_systems as systems
from . import interactive_workflows as workflows
from .interactive_common import request


def _record(state, direction, kind, **data):
    coupling = state["coupling"]
    coupling["phase"] += 1
    coupling["events"].append(
        {
            "number": coupling["phase"],
            "direction": direction,
            "kind": kind,
            "data": copy.deepcopy(data),
        }
    )


def _complete(state):
    coupling = state["coupling"]
    if coupling["phase"] != 3 or [e["number"] for e in coupling["events"]] != [1, 2, 3]:
        return "all three documented cross-workflow handoffs must be reconciled"
    return None


def _system_ready(number, child):
    return systems.check_goal(number, 0, child) is None


def _workflow_ready(child):
    return workflows._check(child) is None


def _migration_check(child):
    """The composite source includes the explicitly published return correction."""
    if child["violations"] or not child["finished"]:
        return "migration safety or successful finish missing"
    if set(child["sources"]) != {f"f{i}" for i in range(6)}:
        return "the six logical migration identities must be preserved"
    expected = {
        name: {key: row[key] for key in ("data", "rev")}
        for name, row in child["sources"].items()
    }
    if child["target"] != expected or child["verified"] != expected:
        return "replicas must match the current sources including the return correction"
    if (
        not all(row.get("removed") for row in child["sources"].values())
        or child["staged"]
        or any(job["phase"] == "pending" for job in child["jobs"].values())
        or not child["producer_done"]
        or child["protected"] != {"keep/control": "foreign-control"}
    ):
        return "migration source cleanup, producer, protected data or staging differs"
    if child["transaction"] is not None and (
        not child["cut"] or not child["transaction"]["applied"]
    ):
        return "the current WAL transaction and cutover must be complete"
    return None


def _database_value(child, identity, value):
    child["rows"][identity]["value"] = value
    child["revision"] += 1


def _reopen_source(child, name, suffix):
    row = child["sources"][name]
    row["data"] += suffix
    row["rev"] += 1
    row["removed"] = False
    child["finished"] = False


def after_526(state, lane, words, opts, response, code):
    left, right = state["left"], state["right"]
    phase = state["coupling"]["phase"]
    if phase == 0 and lane == "left" and _migration_check(left) is None:
        row = left["target"]["f5"]
        value = f"snapshot:{row['data']}@{row['rev']}"
        _database_value(right, "source-0", value)
        _record(state, "left->right", "snapshot_row", source="f5", **row,
                target="source-0", value=value, target_revision=right["revision"])
    elif phase == 1 and lane == "right" and _workflow_ready(right):
        source = right["rows"]["source-0"]
        value = right["target"][source["target_id"]]["value"]
        suffix = "|db:" + value
        _reopen_source(left, "f5", suffix)
        _record(state, "right->left", "database_return", source=source["target_id"],
                source_revision=right["committed_revision"], value=value,
                target="f5", suffix=suffix, revision=left["sources"]["f5"]["rev"])
    elif phase == 2 and lane == "left" and _migration_check(left) is None:
        row = left["target"]["f5"]
        _database_value(right, "source-5", row["data"])
        _record(state, "left->right", "reconciled_snapshot", source="f5", **row,
                target="source-5", target_revision=right["revision"])


def _refresh_copy_generation(child, name, increment):
    child["objects"][name]["revision"] += increment
    revision = child["objects"][name]["revision"]
    for path, row in child["places"].items():
        if row["id"] == name:
            row["revision"] = revision
            child["epochs"][path] += 1
    child["finished"] = False
    return revision


def _copy_quiet(child):
    return not child["scratch"] and {r["id"] for r in child["places"].values()} == set("ABCDEF")


def after_527(state, lane, words, opts, response, code):
    left, right = state["left"], state["right"]
    phase = state["coupling"]["phase"]
    if phase == 0 and _migration_check(left) is None and _copy_quiet(right):
        increment = left["target"]["f0"]["rev"]
        revision = _refresh_copy_generation(right, "A", increment)
        _record(state, "left->right", "snapshot_encoding", source="f0",
                source_revision=increment, target="A", revision=revision)
    elif phase == 1 and lane == "right" and _system_ready(499, right):
        versions = {name: right["objects"][name]["revision"] for name in ("A", "F")}
        for name, source in (("f0", "A"), ("f5", "F")):
            _reopen_source(left, name, f"|store-{source}:{versions[source]}")
        left["transaction"] = {"id": "tx-copy-return", "members": ["f0", "f5"], "applied": False}
        left["cut"] = False
        _record(state, "right->left", "store_wal", source_versions=versions,
                transaction="tx-copy-return", revisions={n: left["sources"][n]["rev"] for n in ("f0", "f5")})
    elif phase == 2 and lane == "left" and _migration_check(left) is None:
        increment = left["target"]["f5"]["rev"]
        revision = _refresh_copy_generation(right, "F", increment)
        # The storage operator performs a lossless namespace relocation.  It
        # does not clear the old repair journal or any recorded safety history.
        right["places"] = {
            name.lower(): {"id": name, "revision": right["objects"][name]["revision"]}
            for name in "ABCDEF"
        }
        for path in right["epochs"]:
            right["epochs"][path] += 1
        _record(state, "left->right", "cutover_relocation", source="f5",
                source_revision=increment, target="F", revision=revision,
                placements={name: name.lower() for name in "ABCDEF"})


def _obsolete_binding(child, tenant, value, *, mask=False):
    child["layers"][tenant]["route"] = value
    child["revision"][tenant] += 1
    if mask:
        child["masks"][tenant]["audit"] = "off"
        child["mask_revision"][tenant] += 1
    child["finished"] = False


def after_528(state, lane, words, opts, response, code):
    left, right = state["left"], state["right"]
    phase = state["coupling"]["phase"]
    if phase == 0 and lane == "left" and _system_ready(491, left):
        ids = sorted(key for key, row in left["rows"].items() if row["archived"])
        value = "archive:" + ",".join(ids)
        _obsolete_binding(right, "blue", value)
        _record(state, "left->right", "archive_binding", archived=ids,
                generation=left["generation"], tenant="blue", route=value)
    elif phase == 1 and lane == "right" and _system_ready(495, right):
        policy = copy.deepcopy(right["published"]["blue"])
        left["rows"]["r1"]["urgent"] = False
        left["rows"]["r1"]["version"] += 1
        left["rows"]["policy-row"] = {
            "account": "policy:" + policy["route"], "version": 1, "labels": [],
            "urgent": policy["audit"] == "on", "archived": False,
        }
        left["deps"]["policy-row"] = ["r5"]
        left["generation"] += 1
        left["finished"] = False
        _record(state, "right->left", "policy_predicate", policy=policy,
                membership_revision=right["membership_revision"],
                generation=left["generation"], retired="r1", added="policy-row", dependency="r5")
    elif phase == 2 and lane == "left" and _system_ready(491, left):
        value = "predicate:" + str(left["generation"])
        _obsolete_binding(right, "green", value, mask=True)
        _record(state, "left->right", "predicate_mask", generation=left["generation"],
                tenant="green", route=value, audit="off")


def after_529(state, lane, words, opts, response, code):
    left, right = state["left"], state["right"]
    phase = state["coupling"]["phase"]
    if phase == 0 and lane == "left" and _system_ready(493, left):
        versions = {name: row["version"] for name, row in left["packages"].items()}
        right["environment_revision"] += sum(versions.values())
        right["accepted"] = None
        _record(state, "left->right", "rollback_environment", versions=versions,
                environment_revision=right["environment_revision"])
    elif phase == 1 and lane == "right" and _workflow_ready(right):
        fingerprint = right["accepted"]
        left["packages"]["base"]["version"] = 3
        left["packages"]["base"]["revision"] += 1
        left["epoch"]["base"] += right["environment_revision"]
        left["finished"] = False
        _record(state, "right->left", "compatibility_bridge", fingerprint=fingerprint,
                package="base", version=3, revision=left["packages"]["base"]["revision"],
                artifact_epoch=left["epoch"]["base"])
    elif phase == 2 and lane == "left" and _system_ready(493, left):
        revision = left["packages"]["base"]["revision"]
        right["input_revisions"]["release"] += revision
        right["accepted"] = None
        _record(state, "left->right", "rollback_acceptance_inputs", source="base",
                source_revision=revision, group="release",
                input_revision=right["input_revisions"]["release"])


def _runtime_goal(child, profile, key, value):
    child["goals"][profile][key] = value
    child["goal_revision"] = child.get("goal_revision", 0) + 1
    child["layers"]["overrides"]["values"].setdefault(profile, {})[key] = value
    child["layers"]["overrides"]["revision"] += 1
    # Publishing the new desired configuration does not deploy it.  Pending
    # jobs retain their captured drafts and must observe the new generation.
    child["live_generation"] += 1
    child["probes"] = {}


def after_530(state, lane, words, opts, response, code):
    left, right = state["left"], state["right"]
    phase = state["coupling"]["phase"]
    if phase == 0 and lane == "left" and _system_ready(494, left):
        route = left["published"]["blue"]["route"]
        value = "handoff-" + route + ".internal"
        _runtime_goal(right, "prod", "endpoint", value)
        _record(state, "left->right", "route_runtime", route=route, profile="prod",
                endpoint=value, live_generation=right["live_generation"],
                goals=right["goals"], goal_revision=right["goal_revision"])
    elif phase == 1 and lane == "right" and _workflow_ready(right):
        values = copy.deepcopy(right["runtime_values"]["prod"])
        route = "runtime:" + values["timeout"] + ":" + values["endpoint"]
        _obsolete_binding(left, "green", route, mask=True)
        _record(state, "right->left", "runtime_binding", values=values,
                live_generation=right["live_generation"], tenant="green", route=route)
    elif phase == 2 and lane == "left" and _system_ready(494, left):
        revision = left["revision"]["green"]
        value = "0" + str(revision)
        _runtime_goal(right, "batch", "timeout", value)
        _record(state, "left->right", "binding_retirement_runtime", source="green",
                source_revision=revision, profile="batch", timeout=value,
                live_generation=right["live_generation"], goals=right["goals"],
                goal_revision=right["goal_revision"])
    if lane == "right" and words == ["status"] and code == 0:
        response["goals"] = copy.deepcopy(right["goals"])
        response["goal_revision"] = right.get("goal_revision", 0)


def _check_530(state):
    if failure := _complete(state):
        return failure
    first, _, third = state["coupling"]["events"]
    goals = state["right"]["goals"]
    if (goals["prod"]["endpoint"] != first["data"]["endpoint"]
            or goals["batch"]["timeout"] != third["data"]["timeout"]):
        return "current deployment goals must retain both cross-workflow values"
    return None


def _new_file_obligation(child, path, label, value):
    before, after = f"{label}=pending\n", f"{label}={value}\n"
    child["files"][path]["text"] += before
    child["files"][path]["revision"] += 1
    child["expected"][path] += after
    child["verified"].pop(path, None)
    return before, after


def after_531(state, lane, words, opts, response, code):
    left, right = state["left"], state["right"]
    phase = state["coupling"]["phase"]
    if phase == 0 and lane == "left" and _system_ready(497, left):
        value = left["docs"]["d0"]["payload"]["left"]
        before, after = _new_file_obligation(right, "config/app.txt", "linked-anchor", value)
        _record(state, "left->right", "anchor_file", document="d0",
                source_revision=left["revision"]["d0"], value=value,
                path="config/app.txt", before=before, after=after)
    elif phase == 1 and lane == "right" and _workflow_ready(right):
        row = right["files"]["config/app.txt"]
        value = "edit-bytes-" + str(len(row["text"].encode("utf-8")))
        left["original_values"]["d1"]["left"] = value
        left["revision"]["d0"] += 1
        left["finished"] = False
        _record(state, "right->left", "edited_source_cut", path="config/app.txt",
                source_revision=row["revision"], utf8_bytes=len(row["text"].encode("utf-8")),
                source_document="d1", field="left", value=value, destination="d0",
                destination_revision=left["revision"]["d0"])
    elif phase == 2 and lane == "left" and _system_ready(497, left):
        revision = left["revision"]["d0"]
        before, after = _new_file_obligation(right, "config/worker.txt", "linked-revision", str(revision))
        _record(state, "left->right", "anchor_revision_file", document="d0",
                source_revision=revision, path="config/worker.txt", before=before, after=after)


def _call(workspace, *args):
    return request(workspace, *(str(arg) for arg in args))[0]


def _put(workspace, name, data):
    (workspace / name).write_text(json.dumps(data), encoding="utf-8")
    return name


def _events(workspace):
    return _call(workspace, "pair", "status")["coupling"]["events"]


def _migration_gold(workspace, *, live=False):
    # Existing targets can be stale after a return correction.  The original
    # gold handles initial copies and WAL, but needs this legitimate recopy.
    state = _call(workspace, "catalog")
    if not live:
        for name, source in state["sources"].items():
            current = {key: source[key] for key in ("data", "rev")}
            if name in state["target"] and state["target"][name] != current:
                result = _call(workspace, "copy", "--id", name, "--path", source["path"],
                               "--revision", source["rev"])
                assert result["status"] == "accepted", result
                while _call(workspace, "job", "--id", result["job"])["phase"] == "pending":
                    pass
    systems.migration_gold_v8(workspace)


def gold_526(workspace):
    _migration_gold(workspace / "left")
    workflows._gold_database(workspace / "right")
    _migration_gold(workspace / "left")
    workflows._gold_database(workspace / "right", alternate=True)


def gold_527(workspace):
    _migration_gold(workspace / "left", live=True)
    systems.copy_gold_v8(workspace / "right")
    _migration_gold(workspace / "left", live=True)
    systems.copy_gold_v8(workspace / "right", reverse_order=True)


def gold_528(workspace):
    systems.predicate_gold_v8(workspace / "left")
    systems.config_gold_v8(workspace / "right")
    systems.predicate_gold_v8(workspace / "left", reverse_order=True)
    systems.config_gold_v8(workspace / "right", reverse_order=True)


def gold_529(workspace):
    systems.release_gold_v8(workspace / "left")
    workflows._gold_retry(workspace / "right")
    systems.release_gold_v8(workspace / "left", reverse_order=True)
    workflows._gold_retry(workspace / "right")


def _linked_runtime_gold(workspace, *, alternate=False):
    child = workspace / "right"
    contract = json.loads((child / "contract.json").read_text())
    # The immutable fixture describes the initial goals.  Public handoffs
    # publish complete replacement goal maps, with no host-state access.
    for event in _events(workspace):
        if "goals" in event["data"]:
            contract["goals"] = copy.deepcopy(event["data"]["goals"])
    if alternate:
        workflows._call(child, "sync")
        workflows._call(child, "refresh")
        if "task-release" not in workflows._call(child, "layers")["order"]:
            workflows._call(child, "overlay", "--id", "task-release")
    for _ in range(10):
        live = {
            profile: workflows._call(child, "runtime", "--profile", profile, "--fresh")
            for profile in contract["profiles"]
        }
        done = {
            profile for profile in contract["profiles"]
            if all(live[profile]["values"][key] == value
                   for key, value in contract["goals"][profile].items())
        }
        if len(done) == len(contract["profiles"]):
            return
        workflows._call(child, "refresh")
        changes = {}
        for profile, goals in contract["goals"].items():
            for key, value in goals.items():
                explanation = workflows._call(
                    child, "explain", "--profile", profile, "--key", key, "--view", "draft"
                )
                owner = "task-release" if alternate else explanation["owner"]
                if explanation["value"] != value:
                    changes.setdefault(owner, {}).setdefault(profile, {})[key] = value
        for layer, patch in changes.items():
            current = workflows._call(child, "read", "--layer", layer, "--view", "draft")
            workflows._send(child, "stage", {
                "layer": layer, "expected_revision": current["revision"], "changes": patch,
            })
        receipt = workflows._call(child, "validate")
        layers = workflows._call(child, "layers")
        selected = []
        for profile in ("canary", "prod", "batch"):
            if profile in contract["profiles"] and profile not in done:
                dependency = layers["dependencies"].get(profile)
                if dependency is None or dependency in done | set(selected):
                    selected.append(profile)
                if len(selected) == (2 if alternate else 1):
                    break
        deployment = workflows._call(
            child, "deploy", "--validation", receipt["validation"],
            "--if-live-generation", layers["live_generation"],
            "--profiles", ",".join(selected),
        )
        if deployment["status"] == "accepted":
            workflows._poll(child, deployment["job"])
            continue
        assert deployment["status"] in (
            "protected_setting_conflict", "live_conflict", "validation_stale",
        ), deployment
    raise AssertionError("rollout failed to reach public cross-workflow goals")


def gold_530(workspace):
    systems.config_gold_v8(workspace / "left")
    _linked_runtime_gold(workspace)
    systems.config_gold_v8(workspace / "left", reverse_order=True)
    _linked_runtime_gold(workspace, alternate=True)


def _linked_file_edits(workspace):
    child = workspace / "right"
    for event in _events(workspace):
        if event["kind"] not in ("anchor_file", "anchor_revision_file"):
            continue
        change = event["data"]
        row = _call(child, "read", "--path", change["path"])
        if change["after"] not in row["text"]:
            assert row["text"].count(change["before"]) == 1
            response = _call(
                child, "apply", "--file", _put(child, "linked-edit.json", {
                    "operation_id": f"link-{event['number']}-{row['revision']}",
                    "edits": [{"path": change["path"], "expected_revision": row["revision"],
                               "before": change["before"], "after": change["after"]}],
                }),
            )
            assert response["status"] in ("complete", "ack_unknown"), response
        _call(child, "verify", "--path", change["path"])


def gold_531(workspace):
    systems.merge_gold_v8(workspace / "left")
    workflows._gold_edits(workspace / "right", alternate=True)
    _linked_file_edits(workspace)
    event = next(e for e in _events(workspace) if e["kind"] == "edited_source_cut")
    child = workspace / "left"
    opened = _call(child, "open", "--id", "d0")
    target = next(row for row in opened["document"]["lines"] if row["id"] == "target")
    response = _call(child, "patch", "--file", _put(child, "linked-anchor.json", {
        "id": "d0", "revision": opened["revision"],
        "edits": [{"line": "target", "old": target["text"], "new": event["data"]["value"]}],
    }))
    assert response["status"] == "saved", response
    receipts = {f"d{i}": _call(child, "validate", "--id", f"d{i}")["receipt"] for i in range(6)}
    assert _call(child, "publish", "--file", _put(child, "linked-publish.json", {"receipts": receipts}))["applied"]
    assert _call(child, "finish")["status"] == "finished"
    _linked_file_edits(workspace)


_COMMON = """
There are exactly THREE cross-workflow events, in the documented order below.
They are deterministic domain changes, not time-based events. Current source
data remains authoritative; a previously finished half may be reopened by a
return event. Do not stop when only one half is complete. Read pair status
for phase and the complete event payloads. Old receipts, target copies, drafts
and proofs are not silently repaired by the handoff. No history, protected
data, foreign ownership or sticky safety violations is cleared. Both current
domain goals AND all three handoffs must be complete together.
"""


PAIRS = {
    526: {
        "left": 488, "right": 505, "slug": "migration_database_roundtrip",
        "title": "Reconcile a migrated snapshot with a changing database view",
        "docs": _COMMON + """
1. Left successful current finish exports target f5. Right source-0.value becomes
   'snapshot:' + f5.data + '@' + decimal(f5.rev); the DB source revision increments.
2. Once right has committed its full current view, append '|db:' + the committed
   value of source-0's target to left source f5.data, increment f5.rev, reopen
   its original path (removed=false), and reopen left finish. Existing target
   bytes remain old: recopy f5, verify/activate, remove, finish again. Its current
   path is still found by logical identity in catalog, including moved/f5.
3. That new left finish sets right source-5.value to the current f5.data and
   increments the DB source revision. Reconcile and commit the DB again.
The left goal is the six CURRENT source data/rev values including step2, not
the historical base strings. Preserve keep/control; every source must be
removed, every current replica verified, and all copy jobs/staging drained.
""", "after": after_526, "check": _complete,
        "check_left": _migration_check, "gold": gold_526,
    },
    527: {
        "left": 489, "right": 499, "slug": "live_restore_copy_cutover",
        "title": "Close a live restore and copy-store cutover without stale replicas",
        "docs": _COMMON + """
1. After a command to EITHER lane, if left's current migration is finished and
   right has empty scratch and all six identities in published places, increase
   object A.revision by
   left target f0.rev. Its published copies receive that revision and their
   path epochs increment; right finish reopens. Sizes/identities stay fixed.
   This first handoff can occur in the response to left finish itself; it does
   not require an additional right command.
2. Right current finish emits a NEW WAL transaction tx-copy-return over f0,f5.
   Append '|store-A:' + decimal(A.revision) to source f0.data and '|store-F:' +
   decimal(F.revision) to f5.data; increment both source revisions, reopen their
   removal flags, left finish and cutover. Old replicas remain resident. Replay
   BOTH revisions atomically, reverify current dependency cohorts, cut over and
   remove again. Historical tx-AF was already applied; its history remains.
3. Left current finish increases object F.revision by current target f5.rev.
   The storage operator atomically relocates each identity I to lowercase(I),
   retaining all CURRENT content revisions, then increments every path epoch.
   Right finish reopens; perform the manifest placement cycle again. The original
   one-time displacement journal is preserved and is not emitted a second time.
Left's final goal uses its current source data/rev including the new WAL.
""", "after": after_527, "check": _complete,
        "check_left": _migration_check, "gold": gold_527,
    },
    528: {
        "left": 491, "right": 495, "slug": "predicate_policy_retirement",
        "title": "Retire obsolete configuration while its policy changes archive coverage",
        "docs": _COMMON + """
1. Left finish materializes a right blue tenant route equal to 'archive:' plus
   comma-joined sorted archived row keys, increments that layer revision and
   reopens finish. It is obsolete: the manifest still requires inherited group/on.
2. Right current finish makes left r1 nonurgent and increments its version. It
   adds policy-row with account='policy:'+published blue route, version1, no
   labels, urgent=(published blue audit=='on'), archived=false, dependency r5.
   Increment predicate generation and reopen left finish. Previously valid r1
   archival is not a violation; explicitly restore r1 and archive current policy-row.
3. Left finish materializes green.route='predicate:'+decimal(generation), plus
   green audit mask='off'; both relevant revisions increment and right reopens.
   Retire the new bindings and publish the unchanged inherited goals again.
All old foreign labels/tokens and protected tenant effective values survive.
""", "after": after_528, "check": _complete, "gold": gold_528,
    },
    529: {
        "left": 493, "right": 511, "slug": "rollback_acceptance_roundtrip",
        "title": "Reconcile rollback, compatibility bridges and fresh acceptance evidence",
        "docs": _COMMON + """
1. Left finish increments right environment_revision by the SUM of published
   package versions, invalidating old acceptance/fingerprint. Right's authorized
   parser repair and all current dependency/input rules still apply.
2. Right current accepted test coverage authorizes an operator bridge: set left
   base.version=3, increment base.revision, and add right environment_revision
   to base artifact epoch. Reopen left finish. With every other package at1,
   (base3,codec1) is explicitly compatible. The final manifest goal remains1:
   stage the current version1 artifact and activate a compatible rollback.
3. The new left finish increments right input_revisions.release by current
   base.revision and invalidates acceptance. Rerun affected tests and downstream
   dependencies, then accept current evidence. No historical receipt is erased.
""", "after": after_529, "check": _complete, "gold": gold_529,
    },
    530: {
        "left": 494, "right": 507, "slug": "inherited_runtime_roundtrip",
        "title": "Reconcile inherited tenant configuration with live profile deployment",
        "docs": _COMMON + """
1. Left finish makes 'handoff-'+published blue route+'.internal' the NEW right
   prod.endpoint GOAL and writes it to the live overrides layer. The layer
   revision, goal_revision and live_generation increment; probes are invalidated.
   Actual runtime and drafts stay unchanged: deploy the new endpoint, respecting
   rollout dependencies and current retention. The event includes the complete
   current goals map and goal_revision; right status exposes both as well.
2. Right current runtime goal with fresh probes for EVERY profile materializes
   left green.route='runtime:'+prod.timeout+':'+prod.endpoint and audit mask off.
   Relevant revisions increment and left finish reopens. Remove the obsolete
   route/mask, preserve protected tenants and republish inherited new/on.
3. Left current finish makes '0'+decimal(left green layer revision) the NEW
   right batch.timeout GOAL (an exact string, including its leading zero).
   Update the live overrides layer; increment its revision, goal_revision and
   live_generation, invalidate probes, but leave runtime/drafts unchanged.
   Deploy this new batch timeout, retain the step1 prod endpoint and refresh all
   current probes. Preserve current retention throughout all three events.
The immutable right contract.json supplies INITIAL goals only. Step1 replaces
its goals.prod.endpoint; step3 replaces goals.batch.timeout. All other goals
remain unchanged. Follow the latest public event/status goals, not the old
constants for those two keys. Publishing a goal or changing a live layer does
not activate runtime. A job captured before a handoff sees a changed live
generation and becomes superseded when polled; drain it before taking its slots.
""", "after": after_530, "check": _check_530, "gold": gold_530,
    },
    531: {
        "left": 497, "right": 508, "slug": "anchor_file_feedback",
        "title": "Apply cross-document source corrections without losing concurrent edits",
        "docs": _COMMON + """
1. Left finish appends 'linked-anchor=pending\\n' to right config/app.txt and
   increments its revision. The NEW required edit is to replace that exact line
   with 'linked-anchor='+left d0.payload.left+'\\n'. These two spans and the
   source revision are included in the event. All other bytes are preserved.
2. Once all right original AND new edits have current read-back, the source cut
   receives one explicit correction: d1.original.left becomes 'edit-bytes-N',
   where N is the UTF-8 byte length of current config/app.txt. This is published
   in the event. d0's target anchor/payload must now use that new source value;
   increment d0 revision and reopen left finish, without changing its text for
   the agent. Already completed transfers may patch their owned anchors without
   new pins; obtain current revision, patch ONLY target, validate/publish all.
   Shadow, foreign metadata, other anchor and source-transfer history stay intact.
3. Left finish appends 'linked-revision=pending\\n' to right config/worker.txt.
   Replace that exact line with 'linked-revision='+decimal(current d0 revision)
   +'\\n', preserving all other bytes, then read back that current file.
The event's before/after values are authoritative additional edit requests.
""", "after": after_531, "check": _complete, "gold": gold_531,
    },
}
