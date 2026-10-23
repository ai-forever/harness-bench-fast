"""Seven public, data-bearing feedback links for composite task candidates.

The child state machines and their safety checks remain authoritative.  These
hooks model two documented external handoffs.  They preserve old service
history, receipts and actual writes when a handoff invalidates an earlier cut.
Gold clients access only the public transports and workspace fixtures.
"""

from __future__ import annotations

import copy
import hashlib
import json
from functools import partial

from . import interactive_data as data
from . import interactive_systems as systems
from . import interactive_workflows as workflows
from .interactive_common import request


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _event(state, kind, source_revision, source_value, **changes):
    link = state["coupling"]
    link["events"].append(
        {
            "kind": kind,
            "source_revision": copy.deepcopy(source_revision),
            "source_value": copy.deepcopy(source_value),
            **copy.deepcopy(changes),
        }
    )
    link["phase"] += 1


LEFT_CHECKS = {
    519: data.repair_v8_check,
    520: data.identity_v8_check,
    521: data.privacy_v8_check,
    522: data.latency_v8_check,
    523: data.macro_v8_check,
    524: data.document_v8_check,
    525: partial(systems.check_goal, 487, 0),
}


def _right_ready(number, state):
    right = state["right"]
    child = {520: 498, 523: 492, 524: 496}.get(number)
    failure = systems.check_goal(child, 0, right) if child else workflows._check(right)
    if failure:
        return False
    if number == 523:
        first = state["coupling"]["events"][0]
        return right["packages"]["worker"]["revision"] > first["worker_revision_before"]
    return True


def _advance(number, state, lane, words, opts, response, code):
    """Called after every child command, including durable unknown outcomes."""
    left, right, link = state["left"], state["right"], state["coupling"]
    if link["phase"] == 0 and LEFT_CHECKS[number](left) is None:
        if number == 519:
            source = left["published"]["a"]
            row = right["catalog"]["entry-5"]
            row["digest"] = _hash(source["data"])
            row["generation"] += 1
            right["revision"] += 1
            workflows._backup_catalog_closure(right)
            _event(
                state,
                "repair-to-archive",
                source["generation"],
                source["data"],
                entry="entry-5",
                digest=row["digest"],
                archive_revision=right["revision"],
            )
        elif number == 520:
            groups = sorted(sorted(group) for group in left["active"]["groups"])
            # B..F avoids an accidental unchanged original A binding.
            identity = "BCDEF"[sum(len(group) ** 2 for group in groups) % 5]
            previous = right["clients"]["client-A"]
            right["clients"]["client-A"] = identity
            right["reference_revision"] += 1
            right["finished"] = False
            # The old concrete reference intentionally remains until activation.
            _event(
                state,
                "groups-to-client",
                left["revision"],
                groups,
                client="client-A",
                previous_identity=previous,
                identity=identity,
                reference_revision=right["reference_revision"],
            )
        elif number == 521:
            source = copy.deepcopy(left["closed"])
            right["rows"]["source-0"]["value"] = _canonical(source)
            right["revision"] += 1
            _event(
                state,
                "privacy-to-migration",
                left["revision"],
                source,
                source_id="source-0",
                value=_canonical(source),
                database_revision=right["revision"],
            )
        elif number == 522:
            metrics = copy.deepcopy(left["dashboard"]["metrics"])
            metrics["latencies"].sort()
            goals = copy.deepcopy(right["goals"])
            goals["prod"]["timeout"] = str(30 + metrics["completed"])
            goals["canary"]["timeout"] = str(
                40 + metrics["pending"] + max(metrics["latencies"], default=0)
            )
            right["goals"] = goals
            _event(state, "latency-to-runtime", left["revision"], metrics, goals=goals)
        elif number == 523:
            release = left["deployed"]["archive"]
            source = data.macro_v8_eval(
                left, release["module"], release["module"]["entry"], left["names"][0]
            )
            increment = 1 + len(source) % 5
            before = right["packages"]["worker"]["revision"]
            right["epoch"]["worker"] += increment
            # This still fits the existing three-byte staging pool; versions
            # 1/2 keep their original two-byte cost and compatibility contracts.
            right["costs"]["worker"]["3"] = 1 + len(source) % 2
            right["finished"] = False
            _event(
                state,
                "template-to-artifact",
                release["receipt"],
                source,
                epoch_increment=increment,
                worker_bridge_bytes=right["costs"]["worker"]["3"],
                worker_revision_before=before,
            )
        elif number == 524:
            source = left["versions"][left["heads"]["draft-0"]][:]
            value = "\n".join(source)
            right["original_values"]["d0"]["left"] = value
            # A superseding source cut makes completion/proofs from the prior
            # transfer epoch insufficient.  Actual documents, pins, receipts,
            # published history, ownership and violations survive the handoff.
            retired = right["done"][:]
            right["done"] = []
            right["revision"]["d0"] += 1
            right["finished"] = False
            _event(
                state,
                "draft-to-source-cut",
                left["heads"]["draft-0"],
                source,
                source_document="d0",
                source_field="left",
                value=value,
                retired_transfer_completion=retired,
            )
        elif number == 525:
            source = {
                name: {field: row[field] for field in ("effect", "route", "revision", "units")}
                for name, row in sorted(left["items"].items())
            }
            increment = sum(row["effect"] * row["revision"] for row in source.values())
            right["environment_revision"] += increment
            right["input_revisions"]["release"] += sum(
                row["effect"] * row["units"] for row in source.values()
            )
            _event(
                state,
                "delivery-to-test-input",
                {name: row["revision"] for name, row in source.items()},
                source,
                environment_increment=increment,
                fingerprint=workflows._fingerprint(right),
                release_input_revision=right["input_revisions"]["release"],
            )
    elif link["phase"] == 1 and _right_ready(number, state):
        if number == 519:
            publication = right["published"]
            source = {key: row["digest"] for key, row in sorted(publication["items"].items())}
            value = _hash(source)[:6].upper()
            left["objects"]["c"] = value
            left["generation"]["c"] = "c-archive-1"
            left["revision"] = "repairs-archive-1"
            _event(
                state,
                "archive-to-donor",
                publication["revision"],
                source,
                object="c",
                generation="c-archive-1",
                data=value,
            )
        elif number == 520:
            groups = link["events"][0]["source_value"]
            name = right["references"]["client-A"]
            index = (ord(name) - ord("a")) % (len(groups) - 1) + 1
            edge = {
                "id": "namespace-client-link",
                "rev": 1,
                "left": groups[0][0],
                "right": groups[index][0],
            }
            left["edges"].append(edge)
            left["revision"] = "identity-namespace-1"
            left["candidate"] = left["receipt"] = None
            _event(state, "namespace-to-identity", right["reference_revision"], name, edge=edge)
        elif number == 521:
            targets = sorted(key for key, row in right["target"].items() if row["owner"] == "task")
            subject = left["subjects"][len(targets) % len(left["subjects"])]
            alias = {"subject": subject, "record": "record-7", "archived": True}
            left["aliases"].append(alias)
            left["revision"] = "aliases-migration-1"
            # Existing closures remain as historical state, but receipts bind
            # the old cut and the new subject's coverage is now incomplete.
            _event(state, "migration-to-privacy", right["committed_revision"], targets, alias=alias)
        elif number == 522:
            runtime = {
                profile: right["runtime_values"][profile]["timeout"]
                for profile in sorted(right["profiles"])
            }
            req = data._latest(left["requests"])["request-5"]
            revision = max(row["rev"] for row in left["attempts"] if row["id"] == "attempt-5") + 1
            attempt = {
                "id": "attempt-5",
                "rev": revision,
                "request": "request-5",
                "finish": req["start"] + int(runtime["prod"]),
                "outcome": "success",
            }
            left["attempts"].append(attempt)
            left["revision"] = "latency-runtime-1"
            _event(state, "runtime-to-latency", right["live_generation"], runtime, attempt=attempt)
        elif number == 523:
            versions = {name: row["version"] for name, row in sorted(right["packages"].items())}
            tail = " packages=" + ",".join(
                f"{name}:{version}" for name, version in versions.items()
            )
            left["requirements"]["archive"]["tail"] = tail
            left["revision"] = "macros-packages-1"
            # Old drafts/compiled/tested/deployed receipts remain inspectable;
            # the new revision requires recompiling and deploying the full DAG.
            _event(
                state,
                "packages-to-template",
                {name: row["revision"] for name, row in right["packages"].items()},
                versions,
                module="archive",
                tail=tail,
            )
        elif number == 524:
            source = {
                name: copy.deepcopy(doc["payload"]) for name, doc in sorted(right["docs"].items())
            }
            note = "published-merge:" + _canonical(source)
            data.document_v8_foreign_append(left, "draft-0", note)
            _event(
                state,
                "merge-to-draft",
                right["published"],
                source,
                document="draft-0",
                protected_note=note,
            )
        elif number == 525:
            coverage = workflows._retry_passed(right)
            live = [
                key
                for key, job in left["jobs"].items()
                if job["id"] == "event-5"
                and job["phase"] == "committed"
                and key not in left["reversed"]
            ]
            assert len(live) == 1
            receipt = live[0]
            left["reversed"].append(receipt)
            left["items"]["event-5"]["effect"] -= 1
            left["items"]["event-5"]["route"] = "relay" if len(coverage) % 2 else "direct"
            left["items"]["event-5"]["revision"] += 1
            left["finished"] = False
            _event(
                state,
                "test-to-redelivery",
                right["accepted"],
                coverage,
                compensated_receipt=receipt,
                event="event-5",
                route=left["items"]["event-5"]["route"],
                delivery_revision=left["items"]["event-5"]["revision"],
            )


def _check(number, state):
    left, right, link = state["left"], state["right"], state["coupling"]
    if link["phase"] != 2 or len(link["events"]) != 2:
        return "Complete both documented cross-service handoffs and reconcile their consequences"
    first, second = link["events"]
    if number == 519:
        valid = (
            right["catalog"]["entry-5"]["digest"] == _hash(first["source_value"])
            and left["objects"]["c"] == _hash(second["source_value"])[:6].upper()
        )
    elif number == 520:
        valid = (
            right["clients"]["client-A"] == first["identity"]
            and second["edge"] in left["edges"]
            and right["references"]["client-A"] == second["source_value"]
        )
    elif number == 521:
        valid = (
            right["rows"]["source-0"]["value"] == _canonical(first["source_value"])
            and second["alias"] in left["aliases"]
        )
    elif number == 522:
        valid = right["goals"] == first["goals"] and second["attempt"] in left["attempts"]
    elif number == 523:
        valid = (
            right["packages"]["worker"]["revision"] > first["worker_revision_before"]
            and left["requirements"]["archive"]["tail"] == second["tail"]
        )
    elif number == 524:
        valid = (
            right["original_values"]["d0"]["left"] == "\n".join(first["source_value"])
            and second["protected_note"] in left["notes"]["draft-0"]
        )
    else:
        valid = (
            second["compensated_receipt"] in left["reversed"]
            and left["items"]["event-5"]["revision"] == second["delivery_revision"]
            and right["input_revisions"]["release"] == first["release_input_revision"]
        )
    return (
        None if valid else "Final cross-service data bindings differ from their documented handoffs"
    )


def _root_status(workspace):
    response, code = request(workspace, "status")
    assert code == 0, response
    return response


def _latency_repair(workspace):
    """Use revision-derived operation keys, safe across repeated source cuts."""
    for _ in range(4):
        head = data._ask(workspace, "head")
        requests = data._latest(
            data._scan(workspace, ("requests", "list"), revision=head["revision"])
        )
        attempts = data._latest(
            data._scan(workspace, ("attempts", "list"), revision=head["revision"])
        )
        status = data._ask(workspace, "status")
        for key, job in status["jobs"].items():
            if job.get("slot_held") and job["revision"] != head["revision"]:
                data._ask(workspace, "report", "cancel", job=key)
        aggregate = {"completed": 0, "pending": 0, "latencies": []}
        for region in head["regions"]:
            metrics = {"completed": 0, "pending": 0, "latencies": []}
            for req in requests.values():
                if req["region"] != region or req["cancelled"]:
                    continue
                finishes = [
                    row["finish"]
                    for row in attempts.values()
                    if row["request"] == req["id"]
                    and row["outcome"] == "success"
                    and row["finish"] >= req["start"]
                ]
                if finishes:
                    metrics["completed"] += 1
                    metrics["latencies"].append(min(finishes) - req["start"])
                else:
                    metrics["pending"] += 1
            job = data._put(
                workspace,
                ("report", "start"),
                metrics,
                region=region,
                revision=head["revision"],
                key=f"linked-report-{head['revision']}-{region}",
            )["job"]
            data._ask(workspace, "report", "advance", job=job)
            data._ask(workspace, "report", "advance", job=job)
            data._ask(workspace, "publish", region=region, job=job)
            if data._ask(workspace, "head")["revision"] != head["revision"]:
                break
            aggregate["completed"] += metrics["completed"]
            aggregate["pending"] += metrics["pending"]
            aggregate["latencies"].extend(metrics["latencies"])
        else:
            status = data._ask(workspace, "status")
            data._put(
                workspace,
                ("dashboard", "publish"),
                {
                    "receipts": {r: status["published"][r]["id"] for r in head["regions"]},
                    "metrics": aggregate,
                },
            )
            return
    raise AssertionError("latency feedback failed to stabilize")


def _config_gold(workspace, goals):
    """The linked public goals supersede only the initial timeout targets."""
    for _ in range(10):
        live = {
            profile: workflows._call(workspace, "runtime", "--profile", profile, "--fresh")
            for profile in goals
        }
        done = {
            profile
            for profile, wanted in goals.items()
            if all(live[profile]["values"][key] == value for key, value in wanted.items())
        }
        if len(done) == len(goals):
            return
        workflows._call(workspace, "refresh")
        changes = {}
        for profile, wanted in goals.items():
            for key, value in wanted.items():
                explanation = workflows._call(
                    workspace, "explain", "--profile", profile, "--key", key, "--view", "draft"
                )
                if explanation["value"] != value:
                    changes.setdefault(explanation["owner"], {}).setdefault(profile, {})[key] = (
                        value
                    )
        for layer, patch in changes.items():
            row = workflows._call(workspace, "read", "--layer", layer, "--view", "draft")
            workflows._send(
                workspace,
                "stage",
                {"layer": layer, "expected_revision": row["revision"], "changes": patch},
            )
        validation = workflows._call(workspace, "validate")
        layers = workflows._call(workspace, "layers")
        profile = next(
            profile
            for profile in ("canary", "prod")
            if profile not in done
            and (
                layers["dependencies"].get(profile) is None
                or layers["dependencies"][profile] in done
            )
        )
        result = workflows._call(
            workspace,
            "deploy",
            "--validation",
            validation["validation"],
            "--if-live-generation",
            layers["live_generation"],
            "--profiles",
            profile,
        )
        if result["status"] == "accepted":
            workflows._poll(workspace, result["job"])
    raise AssertionError("linked runtime goals did not converge")


def _namespace_gold(workspace):
    status = systems.send(workspace, "catalog")
    if status["names"] == status["targets"] and any(
        status["references"][client] != status["targets"][identity]
        for client, identity in status["clients"].items()
    ):
        name = next(name for name in sorted(status["names"]) if name not in status["pinned"])
        ticket = systems.send(
            workspace,
            "reserve",
            "--id",
            name,
            "--from",
            status["names"][name],
            "--to",
            status["temporary"][name],
            "--revision",
            status["revision"][name],
        )["ticket"]
        body = {"tickets": [ticket]}
        proof = systems.send(
            workspace,
            "prepare-references",
            "--file",
            systems.put(workspace, "linked-reference.json", body),
        )
        assert not proof.get("notice"), (
            proof
        )  # a completed child has already admitted its subscriber
        systems.send(
            workspace,
            "activate",
            "--file",
            systems.put(workspace, "linked-activation.json", {**body, "receipt": proof["receipt"]}),
        )
    systems.namespace_gold_v8(workspace)


def _release_gold(workspace, baseline):
    state = systems.send(workspace, "status")
    target = json.loads((workspace / "manifest.json").read_text())["version"]
    if (
        all(row["version"] == target for row in state["packages"].values())
        and state["packages"]["worker"]["revision"] <= baseline
    ):
        for name in state["staged"]:
            systems.send(workspace, "discard", "--package", name)
        row = {
            "package": "worker",
            "version": target,
            "revision": state["packages"]["worker"]["revision"],
            "digest": state["artifacts"]["worker"][str(target)],
        }
        systems.send(
            workspace,
            "stage",
            "--file",
            systems.put(workspace, "linked-worker.json", {"rows": [row]}),
        )
        state = systems.send(workspace, "status")
        systems.send(
            workspace,
            "activate",
            "--file",
            systems.put(
                workspace,
                "linked-worker.json",
                {"receipts": [state["staged"]["worker"]["receipt"]]},
            ),
        )
    systems.release_gold_v8(workspace)


def _document_repair(workspace):
    head = data._ask(workspace, "head")
    pending = set(head["targets"])
    while pending:
        doc = next(
            name for name in sorted(pending) if head["targets"][name]["source"] not in pending
        )
        pending.remove(doc)
        recipe = head["targets"][doc]
        for _ in range(4):
            source = data._ask(workspace, "document", "get", doc=recipe["source"])
            current = data._ask(workspace, "document", "get", doc=doc)
            body = source["lines"][:]
            for raw in recipe["ops"]:
                operation = {**raw, "source": source["version"]} if raw["kind"] == "copy" else raw
                body = data.document_apply(body, operation, {source["version"]: source["lines"]})
            notes = current["protected_suffix"]
            if current["lines"] == body + notes:
                break
            prefix = len(current["lines"]) - len(notes)
            operations = [{"kind": "delete", "at": 0, "count": prefix}] if prefix else []
            operations.append({"kind": "insert", "at": 0, "lines": body})
            data._put(
                workspace,
                ("edit",),
                operations,
                doc=doc,
                base=current["version"],
                key=f"linked-edit-{doc}-{current['version']}",
            )
        else:
            raise AssertionError("document feedback did not converge")


def _redeliver(workspace):
    status = systems.send(workspace, "status")
    row = status["items"]["event-5"]
    key = "linked-redelivery-event-5-" + str(row["revision"])
    result = systems.send(
        workspace,
        "dispatch",
        "--id",
        "event-5",
        "--key",
        key,
        "--route",
        row["route"],
        "--revision",
        row["revision"],
    )
    for _ in range(4):
        receipt = systems.send(workspace, "receipt", "--key", key)
        if receipt["status"] == "absent":
            systems.send(workspace, "retry", "--id", "event-5", "--key", key)
        elif receipt["job"]["phase"] == "committed":
            systems.send(workspace, "settle", "--key", key)
            assert systems.send(workspace, "finish")["status"] == "finished"
            return
    raise AssertionError(("redelivery did not complete", result))


LEFT_GOLDS = {
    519: data.gold_repair_v8,
    520: data.gold_identity_v8,
    521: data.gold_privacy_v8,
    522: data.gold_latency_v8,
    523: data.gold_macro_v8,
    524: data.gold_document_v8,
    525: systems.retry_gold_v8,
}


def _gold(number, workspace):
    left, right = workspace / "left", workspace / "right"
    LEFT_GOLDS[number](left)
    first = _root_status(workspace)["coupling"]["events"][0]
    if number == 519:
        workflows._gold_backup(right)
    elif number == 520:
        _namespace_gold(right)
    elif number == 521:
        workflows._gold_database(right)
    elif number == 522:
        _config_gold(right, first["goals"])
    elif number == 523:
        _release_gold(right, first["worker_revision_before"])
    elif number == 524:
        for name in list(systems.send(right, "status")["pins"]):
            systems.send(right, "unpin", "--id", name)
        systems.merge_gold_v8(right)
    else:
        workflows._gold_retry(right)
    assert _root_status(workspace)["coupling"]["phase"] == 2
    if number == 522:
        _latency_repair(left)
    elif number == 524:
        _document_repair(left)
    elif number == 525:
        _redeliver(left)
    else:
        LEFT_GOLDS[number](left)


COMMON_DOC = """
This pair has exactly TWO one-time data handoffs, in the order described below.
A handoff occurs after a child command as soon as its stated source goal is
complete, including a durable commit whose acknowledgement was lost. Reads can
therefore observe a handoff caused by earlier durable work. Root `status` (also
`pair status`) exposes coupling.phase and an append-only events array containing
the source_revision, complete source_value, and applied changes. Phase 0 means
the first handoff is pending; phase 1 means the second is pending; phase 2 means
no further cross-service change will occur. This does not suppress either
child's own documented producer/operator events. Complete BOTH current child
goals after phase 2. Historical successful finish/publication is insufficient.
No hidden vocabulary, external knowledge, sleeping, or brute-force search is
required. All handoff formulas and affected identities are specified here.
JSON canonicalization below means json.dumps(value, sort_keys=True,
separators=(',', ':'), ensure_ascii=True); SHA256 hashes its UTF-8 encoding.
Existing receipts and history are preserved; fresh cuts may invalidate their
use. Do not recycle an operation key for a different request body or source cut.
"""


PAIR_DOCS = {
    519: """
Repair and hardlink archive form a recovery loop.
1. Once left has the correct complete current repair publications and no active
scratch jobs, the data string of published object a is archived in right entry-5:
its digest becomes SHA256(canonical JSON of that string), its generation and the
archive revision each increase by one. The ordinary backup dependency-generation
closure then applies. This replaces entry-5's prior catalog bytes; all snapshots,
copy jobs, staging and verification history remain. Publish the new current cut.
2. Once right meets its complete current archive goal, including verification,
completed jobs and pruning, take {entry_id:digest} from that actual publication,
sorted by entry_id. Left donor c becomes the first six uppercase hex digits of
SHA256(canonical JSON of that mapping), generation c-archive-1 and repair revision
repairs-archive-1. Its old published data is not rewritten for you. Repair and
publish c again with the current dependency receipts; final scratch must be free.
""",
    520: """
Contact consolidation feeds a rename service whose client routes feed identity.
1. Once left's current groups and every asset are correct, sort each group and
the list of groups lexicographically. Compute q=sum(len(group)**2) % 5 and take
identity='BCDEF'[q]. An authorized client migration changes right client-A's
stable identity to that identity. Every OTHER client identity is preserved.
Its old concrete reference remains until an atomic rename activation rewrites
all current references; reference_revision increases by one and finish reopens.
Old tickets remain reserved and old proofs can be stale. This client-A identity
change supersedes the child's initial identity-preservation rule for that client
only. The namespace target names and temporary-name/pin constraints do not change.
If names already equal their targets, repair stale references using a permitted
temporary rename and return to targets; a no-op reserve is still forbidden.
2. Once right finishes at current targets with all references correct, let n be
its actual reference string for client-A (one lowercase letter a..f), and G be
the sorted groups captured in handoff 1. Let j=(ord(n)-ord('a'))%(len(G)-1)+1.
Append edge {id:'namespace-client-link',rev:1,left:G[0][0],right:G[j][0]} on left,
set revision identity-namespace-1 and invalidate candidate/validation receipts.
Recompute components and move affected assets while preserving all owner notes.
""",
    521: """
Privacy closures and entity migration exchange a changing coverage manifest.
1. Once left has correctly closed every requested subject, including its own
archive backfill, capture its {subject:[record_ids]} closed mapping. Right source-0
gets the canonical JSON of this mapping as its value; source revision increases
by one. The other source rows and all existing snapshots/staging/targets remain.
Migrate that new source value with the ordinary alias closure and ownership rules.
2. Once right's exact current live migration is committed, sort the target IDs
whose owner is task. Let k be their count. Add left archived alias record-7 for
subject head.subjects[k % len(head.subjects)], and set aliases-migration-1 as the
alias revision. Record-7 is an existing record with PII, not a new permission to
change its note. Old closures and receipts stay visible but refer to the earlier
cut. Re-read aliases, redact the new target and close every subject using fresh
current-cut receipts. Preserve the reassigned record-1 and all other foreign data.
""",
    522: """
Measured latency controls deployment; the accepted runtime controls a correction.
1. Once left's regional reports and dashboard are complete/current and workers
free, capture dashboard metrics M. Right's timeout goals become prod=str(30+
M.completed), canary=str(40+M.pending+max(M.latencies, default=0)). Endpoint goals
remain as in contract.json. These timeout goals supersede the initial contract
timeouts; root status publishes the exact goals. Retention remains protected.
The new request changes goals, not live runtime; deploy it under ordinary layer,
CAS, profile dependency and asynchronous read-back rules.
2. Once right actually satisfies those goals, including all current probes and
finished operator/rollout jobs, read its actual prod timeout T. On left append a
new revision of attempt-5 (max previous rev for that ID plus one), success for
request-5 with finish=current request-5.start+int(T). Set latency-runtime-1 as the
source revision. Old report jobs/receipts remain historical. Rebuild reports and
dashboard at the new cut; release all worker slots. This correction does NOT
trigger a third timeout update: the two recorded handoffs are the entire loop.
""",
    523: """
Template compilation drives a package build, then records deployed package data.
1. Once left's entire module DAG is correctly deployed, render its actual archive
release on the first name in names list; call this string R. Increment right's
worker artifact epoch by 1+len(R)%5 and set its bridge-version-3 staging bytes to
1+len(R)%2. Version 1/2 costs stay two bytes, total capacity stays three, all running
compatibility contracts and final target version stay unchanged. Record worker's
current package revision V. This handoff reopens finish but preserves running
packages and occupied staging. Old worker artifact proofs can now be stale.
The worker MUST subsequently be activated with its current artifact (its package
revision must become >V), even if already at target version. Stage/activate of
the same running version is permitted and increments its revision. Then finish
all packages at manifest targets with staging empty.
2. Once that real reactivation and the full right goal are complete, form the
sorted map P={package:running_version}. Set left archive requirement tail to
' packages='+','.join(name+':'+str(version) for sorted P), and source revision to
macros-packages-1. Other macro requirements and source names are preserved.
Deploy current releases throughout the dependency DAG; old provider links and
receipts are still inspectable but do not satisfy the new source cut.
""",
    524: """
Versioned document recipes exchange payload data with a bounded-pin merge.
1. Once left's current document DAG is correct, capture draft-0's complete current
lines. Right's new original source cut replaces ONLY d0.payload.left with those
lines joined by LF (no terminal LF). Other original source values stay unchanged.
The source is the migration's retained original-value archive, returned by pin;
it is not the potentially overwritten live destination. Existing transfer
completion is retired into the root event and right done becomes empty for this
new cut; finish reopens and d0's revision increases by one. Actual documents,
foreign fields, old published revisions, receipts and occupied pins are retained.
An old occupied d0 pin still contains the old cut: release it and acquire a fresh
one before using new data. Unpin is permitted after the completion epoch resets.
Redo the transfer graph from its current original-value archive with the two-pin
limit, preserve all foreign fields, republish current revisions and release pins.
2. Once right has finished that current migration, capture every actual published
document's payload as a sorted {document:{left,right}} map P. Append protected
suffix line 'published-merge:'+canonical_JSON(P) to left draft-0, creating a new
collaborator version exactly as an ordinary protected append. All other source
heads and notes are preserved. Reconcile downstream draft-1/draft-2 recipes from
their now-current upstream heads; the new protected line must survive everywhere
the recipes carry it. Old edit keys remain bound to their original bodies.
""",
    525: """
Delivery recovery supplies test inputs, whose accepted certificate requires redelivery.
1. After left successfully finishes the wanted net effects with dependencies and
escrow satisfied, capture each item's effect,route,revision,units. Right environment
revision increases by sum(effect*revision) over that capture; its release input
revision increases by sum(effect*units). Suite/run/test dependency rules and worker
capacity do not change. Historical receipts/jobs are retained but the new current
fingerprint and release input invalidate old coverage. Run and accept the actual
current complete test graph, handling normal dependency invalidations and retries.
2. Once right has accepted full current PASS coverage with no unfinished jobs,
let C be its accepted {test:receipt} coverage map. The integration automatically
compensates the one unreversed owned committed receipt for left event-5 exactly
once: that receipt is appended to reversed and its net effect decreases by one.
The historical committed job and its immutable key remain. This compensation is
authorized, preserves foreign receipt/history, and requires a replacement event.
Set event-5 route to relay if len(C) is odd, otherwise direct; increase its routing
revision by one and reopen finish. Use a NEW key for this new delivery intention,
but retain/retry that same new key if its acknowledgement is lost. Old-key replay
cannot create the replacement effect. Settle its escrow and finish with all
original wanted net effects and live dependencies. No third test run is required.
""",
}


_IDENTITIES = {
    519: (479, 501, "repair_archive_feedback", "Repair donors and archive dependency closure"),
    520: (480, 498, "identity_namespace_feedback", "Contact groups and client namespace handoff"),
    521: (481, 504, "privacy_migration_feedback", "Privacy coverage and entity migration handoff"),
    522: (482, 506, "latency_runtime_feedback", "Latency reports and layered runtime feedback"),
    523: (
        484,
        492,
        "template_release_feedback",
        "Template providers and package artifact feedback",
    ),
    524: (485, 496, "document_merge_feedback", "Versioned document recipes and source-pin merge"),
    525: (487, 510, "delivery_test_feedback", "Delivery compensation and dependency test coverage"),
}

PAIRS = {
    number: {
        "left": left,
        "right": right,
        "slug": slug,
        "title": title,
        "docs": COMMON_DOC + PAIR_DOCS[number],
        "after": partial(_advance, number),
        "check": partial(_check, number),
        "gold": partial(_gold, number),
    }
    for number, (left, right, slug, title) in _IDENTITIES.items()
}
