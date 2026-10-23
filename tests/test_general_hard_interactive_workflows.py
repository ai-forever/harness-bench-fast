"""Workflow witnesses use only the public transport, not reference state edits."""

from __future__ import annotations

import copy
import json

import pytest

from harness_bench.feasibility_tasks.trusted_service import (
    evidence_context,
    get_session,
    trusted_service_available,
)
from harness_bench.general_hard import interactive_workflows as w

pytestmark = pytest.mark.skipif(
    not trusted_service_available(), reason="Unix socket transport unavailable"
)


def call(ws, *args):
    return w.request(ws, *(str(arg) for arg in args))[0]


def send(ws, command, body):
    path = ws / "candidate.json"
    path.write_text(json.dumps(body))
    return call(ws, command, "--file", path.name)


def contract(ws):
    return json.loads((ws / "contract.json").read_text())


def pages(ws, command, *args):
    result, cursor = [], 0
    while cursor is not None:
        response = call(ws, command, *args, "--cursor", cursor, "--limit", 20)
        result.extend(response["items"])
        cursor = response["next"]
    return result


def poll(ws, job):
    for _ in range(5):
        response = call(ws, "poll", "--job", job)
        if response["status"] in ("complete", "superseded"):
            return response
    raise AssertionError(response)


def gold(ws, alternate=False):
    w.GOLD[contract(ws)["family"]](ws, alternate=alternate)


@pytest.mark.parametrize("number", range(500, 512))
def test_blank_gold_and_current_state_via_public_cli(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        assert not task.verify(tmp_path).passed
        task.apply_gold(tmp_path)
        result = task.verify(tmp_path)
        assert result.passed, result.message
        records = get_session(tmp_path).records
        assert 1 <= len(records) <= 125
        assert all(
            set(record) == {"argv", "files", "response", "code", "terminal", "elapsed_seconds"}
            for record in records
        )
        assert all(record["elapsed_seconds"] is None for record in records)
        assert all(type(record["terminal"]) is bool for record in records)
        # The frozen verifier must reconstruct the same state from public evidence.
        assert w._check(get_session(tmp_path).replay().state) is None
        session = get_session(tmp_path)
        session.freeze()
        evidence = tmp_path.with_suffix(".trusted.json")
        session.export(evidence)
        with evidence_context(tmp_path, evidence):
            assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", range(500, 512))
@pytest.mark.parametrize("variant", (0, 1))
def test_alternative_safe_path_and_other_live_world(number, variant, tmp_path):
    task = w.make_task(number, variant=variant)
    task.setup(tmp_path)
    try:
        gold(tmp_path, alternate=True)
        result = task.verify(tmp_path)
        assert result.passed, result.message
        assert len(get_session(tmp_path).records) <= 125
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", range(500, 512))
def test_identical_stale_files_require_observation_dependent_actions(number, tmp_path):
    first, second = w.make_task(number, variant=0), w.make_task(number, variant=1)
    assert first.setup_files == second.setup_files
    left, right = tmp_path / "left", tmp_path / "right"
    first.setup(left)
    try:
        first.apply_gold(left)
        assert first.verify(left).passed
        transcript = copy.deepcopy(get_session(left).records)
    finally:
        first.cleanup(left)
    second.setup(right)
    try:
        # Blindly transplant a successful complete procedure and its payloads.
        # This must fail: new live responses require a different repair decision.
        for record in transcript:
            for path, text in record["files"].items():
                target = right / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            call(right, *record["argv"])
        assert not second.verify(right).passed
    finally:
        second.cleanup(right)


def stale_backup(ws):
    call(ws, "source", "sync")
    head = call(ws, "head")
    snapshot = call(ws, "pin", "--revision", head["revision"])["snapshot"]
    for row in pages(ws, "catalog", "--snapshot", snapshot):
        job = call(ws, "copy", "--snapshot", snapshot, "--id", row["id"], "--key", row["id"])["job"]
        poll(ws, job)
        call(ws, "verify", "--id", row["id"])
    assert call(ws, "publish", "--snapshot", snapshot)["status"] == "stale_snapshot"
    # Complete old-snapshot copying is not proof that source can now be dropped.
    call(ws, "prune", "--revision", contract(ws)["obsolete_revision"])
    call(ws, "status")


def premature_prune(ws):
    call(ws, "prune", "--revision", contract(ws)["obsolete_revision"])
    gold(ws, alternate=True)


def settlement_ignores_per_item_status(ws):
    items = [
        {"invoice": row["id"], "quote": call(ws, "quote", "--id", row["id"])["quote"]}
        for row in pages(ws, "invoices")
    ]
    job = send(ws, "submit", {"key": "batch", "items": items})["job"]
    assert poll(ws, job)["status"] == "complete"
    pages(ws, "ledger")
    assert call(ws, "close")["status"] == "unreconciled"
    call(ws, "status")


def retry_payment_with_new_key(ws):
    items = [
        {"invoice": row["id"], "quote": call(ws, "quote", "--id", row["id"])["quote"]}
        for row in pages(ws, "invoices")
    ]
    first = send(ws, "submit", {"key": "original", "items": items})["job"]
    result = poll(ws, first)
    assert any(row["status"] == "retryable" for row in result["items"])
    # Typical batch retry: duplicate all previously successful payments too.
    second = send(ws, "submit", {"key": "retry-new-key", "items": items})["job"]
    poll(ws, second)
    for row in pages(ws, "invoices"):
        if row["remaining"] > 0:
            quote = call(ws, "quote", "--id", row["id"])["quote"]
            job = send(
                ws, "submit", {"key": row["id"], "items": [{"invoice": row["id"], "quote": quote}]}
            )["job"]
            poll(ws, job)
    assert call(ws, "close")["status"] == "unreconciled"
    assert any(row["remaining"] < 0 for row in pages(ws, "invoices"))


def migrate_complete_old_snapshot(ws):
    snapshot = call(ws, "snapshot")["snapshot"]
    rows = pages(ws, "scan", "--snapshot", snapshot)
    send(ws, "stage", {"receipts": [row["receipt"] for row in rows]})
    call(ws, "remove", "--id", "obsolete-alias")
    call(ws, "refresh-target")
    current = call(ws, "head")
    outcome = call(
        ws,
        "commit",
        "--source-revision",
        current["revision"],
        "--target-epoch",
        current["target_epoch"],
    )
    assert outcome["status"] in ("source_closure_pending", "coverage_diff")
    call(ws, "status")


def destructive_database_repair(ws):
    call(ws, "reset", "--scope", "all")
    gold(ws, alternate=True)


def edit_the_config_base_only(ws):
    call(ws, "sync")
    call(ws, "refresh")
    base = call(ws, "read", "--layer", "base", "--view", "draft")
    send(
        ws,
        "stage",
        {"layer": "base", "expected_revision": base["revision"], "changes": contract(ws)["goals"]},
    )
    validation = call(ws, "validate")["validation"]
    generation = call(ws, "layers")["live_generation"]
    job = call(
        ws,
        "deploy",
        "--validation",
        validation,
        "--if-live-generation",
        generation,
        "--profiles",
        "canary,prod",
    )["job"]
    poll(ws, job)
    for profile in contract(ws)["profiles"]:
        call(ws, "runtime", "--profile", profile, "--fresh")
    call(ws, "status")


def trust_cached_config_readback(ws):
    gold(ws, alternate=True)
    # Deploy again; old fresh probes no longer prove the new live generation.
    validation = call(ws, "validate")["validation"]
    generation = call(ws, "layers")["live_generation"]
    job = call(
        ws,
        "deploy",
        "--validation",
        validation,
        "--if-live-generation",
        generation,
        "--profiles",
        "canary",
    )["job"]
    poll(ws, job)
    for profile in contract(ws)["profiles"]:
        assert call(ws, "runtime", "--profile", profile)["cached"]
    call(ws, "status")


def overwrite_from_stale_file_copy(ws):
    gold(ws, alternate=True)
    cache = json.loads((ws / "cached/preview.json").read_text())
    files = {
        row["path"]: cache["files"][row["path"]]["text"].replace(row["before"], row["after"], 1)
        for row in contract(ws)["changes"]
    }
    for path, text in files.items():
        revision = call(ws, "read", "--path", path)["revision"]
        send(ws, "restore", {"files": {path: text}, "revisions": {path: revision}})
    for path in files:
        call(ws, "verify", "--path", path)
    call(ws, "status")


def replay_unknown_edit_with_new_id(ws):
    gold(ws)
    record = next(
        row for row in get_session(ws).records if row["response"]["status"] == "ack_unknown"
    )
    body = json.loads(next(iter(record["files"].values())))
    edits = body["edits"]
    # Re-read guards but assume the missing acknowledgement means no effect.
    for row in edits:
        row["expected_revision"] = call(ws, "read", "--path", row["path"])["revision"]
    assert (
        send(ws, "apply", {"operation_id": "retry-new-id", "edits": edits})["status"] == "complete"
    )
    for row in edits:
        assert call(ws, "verify", "--path", row["path"])["text"].count("feature=enabled\n") == 2
    call(ws, "status")


def trust_green_previous_run(ws):
    manifest = call(ws, "manifest", "--refresh")
    assert call(ws, "summary")["status"] == "green"
    response = send(
        ws,
        "schedule",
        {
            "key": "cache",
            "run": manifest["run"],
            "fingerprint": manifest["fingerprint"],
            "tests": manifest["required"],
            "force": False,
        },
    )
    assert response["status"] == "cached_preview"
    cached = json.loads((ws / "cached/preview.json").read_text())
    result = send(
        ws,
        "accept",
        {
            "run": manifest["run"],
            "fingerprint": manifest["fingerprint"],
            "receipts": [row["receipt"] for row in cached["tests"]],
        },
    )
    assert result["status"] == "incomplete"
    call(ws, "summary")


def complete_job_is_not_complete_coverage(ws):
    manifest = call(ws, "manifest", "--refresh")
    batches, current, units = [], [], 0
    for test in manifest["required"]:
        weight = manifest["worker_units"][test]
        if units + weight > manifest["worker_capacity"]:
            batches.append(current)
            current, units = [], 0
        current.append(test)
        units += weight
    batches.append(current)
    items = []
    for index, tests in enumerate(batches):
        job = send(
            ws,
            "schedule",
            {
                "key": f"flat-{index}",
                "run": manifest["run"],
                "fingerprint": manifest["fingerprint"],
                "tests": tests,
                "force": True,
            },
        )["job"]
        items.extend(poll(ws, job)["items"])
    assert {row["status"] for row in items} >= {"pass", "infra_retryable", "blocked"}
    result = send(
        ws,
        "accept",
        {
            "run": manifest["run"],
            "fingerprint": manifest["fingerprint"],
            "receipts": [row["receipt"] for row in items],
        },
    )
    assert result["status"] == "incomplete"
    call(ws, "summary")


WRONG = {
    "backup": (stale_backup, premature_prune),
    "ledger": (settlement_ignores_per_item_status, retry_payment_with_new_key),
    "database": (migrate_complete_old_snapshot, destructive_database_repair),
    "config": (edit_the_config_base_only, trust_cached_config_readback),
    "edits": (overwrite_from_stale_file_copy, replay_unknown_edit_with_new_id),
    "retry": (trust_green_previous_run, complete_job_is_not_complete_coverage),
}


@pytest.mark.parametrize("number", range(500, 512))
@pytest.mark.parametrize("strategy", (0, 1))
def test_plausible_complete_wrong_strategy_rejected(number, strategy, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        WRONG[contract(tmp_path)["family"]][strategy](tmp_path)
        result = task.verify(tmp_path)
        assert not result.passed
        assert "invalid trusted history" not in result.message, result.message
        assert len(get_session(tmp_path).records) <= 150
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (508, 509))
def test_unknown_ack_replay_same_id_and_body_is_safe(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        gold(tmp_path)
        record = next(
            row
            for row in get_session(tmp_path).records
            if row["response"]["status"] == "ack_unknown"
        )
        body = json.loads(next(iter(record["files"].values())))
        before = copy.deepcopy(get_session(tmp_path).replay().state["files"])
        assert send(tmp_path, "apply", body)["status"] in ("complete", "partial")
        assert get_session(tmp_path).replay().state["files"] == before
        for row in body["edits"]:
            assert (
                call(tmp_path, "verify", "--path", row["path"])["text"].count("feature=enabled\n")
                == 1
            )
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (506, 507))
def test_validate_receipt_invalid_after_a_later_patch(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        call(tmp_path, "sync")
        call(tmp_path, "refresh")
        receipt = call(tmp_path, "validate")["validation"]
        base = call(tmp_path, "read", "--layer", "base", "--view", "draft")
        send(
            tmp_path,
            "stage",
            {
                "layer": "base",
                "expected_revision": base["revision"],
                "changes": {"prod": {"timeout": "30"}},
            },
        )
        generation = call(tmp_path, "layers")["live_generation"]
        assert (
            call(tmp_path, "deploy", "--validation", receipt, "--if-live-generation", generation)[
                "status"
            ]
            == "validation_stale"
        )
        gold(tmp_path, alternate=True)
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (506, 507))
def test_rejected_stage_is_atomic_for_whole_change_map(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        before = call(tmp_path, "layers")
        base = call(tmp_path, "read", "--layer", "base", "--view", "draft")
        bad = {
            "layer": "base",
            "expected_revision": base["revision"],
            "changes": {"prod": {"timeout": "999", "retention": "keep-1"}},
        }
        assert send(tmp_path, "stage", bad)["status"] == "protected_setting"
        assert call(tmp_path, "layers") == before
        assert call(tmp_path, "read", "--layer", "base", "--view", "draft") == base
        assert call(tmp_path, "read", "--layer", "base", "--view", "live") == base
        call(tmp_path, "sync")
        call(tmp_path, "refresh")
        receipt = call(tmp_path, "validate")["validation"]
        before = call(tmp_path, "layers")
        base = call(tmp_path, "read", "--layer", "base", "--view", "draft")
        for changes in (
            {"prod": {"timeout": "999", "retention": "keep-1"}},
            {"prod": {"timeout": "999"}, "canary": {"retention": "keep-1"}},
            {"prod": {"timeout": "999", "endpoint": 17}},
            {"prod": {"timeout": "999"}, "canary": []},
        ):
            bad.update(expected_revision=base["revision"], changes=changes)
            assert send(tmp_path, "stage", bad)["status"] in ("protected_setting", "invalid")
            assert call(tmp_path, "layers") == before
            assert call(tmp_path, "read", "--layer", "base", "--view", "draft") == base
        # The old validation still authorizes precisely the unchanged draft.
        deployed = call(
            tmp_path,
            "deploy",
            "--validation",
            receipt,
            "--if-live-generation",
            before["live_generation"],
            "--profiles",
            "canary",
        )
        assert deployed["status"] == "accepted"
        poll(tmp_path, deployed["job"])
        gold(tmp_path, alternate=True)
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize(
    "raw",
    (
        '{"layer":"base","expected_revision":true,"changes":{}}',
        '{"layer":"base","expected_revision":NaN,"changes":{}}',
        '{"layer":"base","expected_revision":Infinity,"changes":{}}',
        '{"layer":"base","layer":"shared","expected_revision":2,"changes":{}}',
    ),
)
def test_malformed_json_is_recoverable_and_cannot_mutate_domain(raw, tmp_path):
    task = w.make_task(506)
    task.setup(tmp_path)
    try:
        before = call(tmp_path, "layers")
        (tmp_path / "bad.json").write_text(raw)
        response = call(tmp_path, "stage", "--file", "bad.json")
        assert response["status"] in ("invalid", "invalid_arguments")
        assert call(tmp_path, "layers") == before
        gold(tmp_path, alternate=True)
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (502, 503))
def test_duplicate_task_payment_can_be_compensated_without_touching_history(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        invoice = pages(tmp_path, "invoices")[0]["id"]
        gold(tmp_path)
        payment = next(
            row
            for row in pages(tmp_path, "ledger")
            if row["invoice"] == invoice and row["owner"] == "task" and not row["reversed"]
        )
        quote = payment["quote"]
        job = send(
            tmp_path,
            "submit",
            {"key": "accidental-repeat", "items": [{"invoice": invoice, "quote": quote}]},
        )["job"]
        receipt = poll(tmp_path, job)["items"][0]
        assert receipt["status"] == "settled"
        assert not task.verify(tmp_path).passed
        call(tmp_path, "reverse", "--payment", receipt["payment"])
        assert call(tmp_path, "close")["status"] == "closed"
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (502, 503))
def test_overpayment_requires_owned_compensation_not_a_negative_debit(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        gold(tmp_path)
        invoice = pages(tmp_path, "invoices")[0]["id"]
        payment = next(
            row
            for row in pages(tmp_path, "ledger")
            if row["invoice"] == invoice and row["owner"] == "task" and not row["reversed"]
        )
        for attempt, expected_amount in (("zero", 0), ("negative", -payment["amount"])):
            quote = call(tmp_path, "quote", "--id", invoice)
            assert quote["amount"] == expected_amount
            before = pages(tmp_path, "ledger")
            job = send(
                tmp_path,
                "submit",
                {"key": attempt, "items": [{"invoice": invoice, "quote": quote["quote"]}]},
            )["job"]
            assert poll(tmp_path, job)["items"][0]["status"] == "nonpositive_debit"
            assert pages(tmp_path, "ledger") == before
            if attempt == "zero":
                job = send(
                    tmp_path,
                    "submit",
                    {
                        "key": "repeated-debit",
                        "items": [{"invoice": invoice, "quote": payment["quote"]}],
                    },
                )["job"]
                duplicate = poll(tmp_path, job)["items"][0]["payment"]
        assert not task.verify(tmp_path).passed
        call(tmp_path, "reverse", "--payment", duplicate)
        assert call(tmp_path, "close")["status"] == "closed"
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


def test_component_repair_invalidates_every_old_pass_receipt(tmp_path):
    task = w.make_task(511)
    task.setup(tmp_path)
    try:
        manifest = call(tmp_path, "manifest", "--refresh")
        job = send(
            tmp_path,
            "schedule",
            {
                "key": "pre-repair",
                "run": manifest["run"],
                "fingerprint": manifest["fingerprint"],
                "tests": ["test-0"],
                "force": True,
            },
        )["job"]
        result = poll(tmp_path, job)
        assert any(row["status"] == "pass" for row in result["items"])
        component = call(tmp_path, "component")
        call(
            tmp_path,
            "repair",
            "--name",
            "parser",
            "--mode",
            "modern",
            "--expected-revision",
            component["revision"],
        )
        coverage = call(tmp_path, "coverage")
        assert coverage["passed"] == {}
        assert set(coverage["missing"]) == set(manifest["required"])
        assert (
            send(
                tmp_path,
                "accept",
                {
                    "run": manifest["run"],
                    "fingerprint": coverage["fingerprint"],
                    "receipts": [row["receipt"] for row in result["items"]],
                },
            )["status"]
            == "incomplete"
        )
        gold(tmp_path, alternate=True)
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("variant", (0, 1))
def test_component_repair_noop_preserves_current_acceptance(variant, tmp_path):
    task = w.make_task(511, variant=variant)
    task.setup(tmp_path)
    try:
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed
        before = call(tmp_path, "component")
        coverage = call(tmp_path, "coverage")
        assert before["mode"] == "modern" and coverage["missing"] == []
        assert call(tmp_path, "summary")["current_acceptance"]
        repaired = call(
            tmp_path,
            "repair",
            "--name",
            "parser",
            "--mode",
            "modern",
            "--expected-revision",
            before["revision"],
        )
        assert repaired["fingerprint"] == coverage["fingerprint"]
        assert call(tmp_path, "component") == before
        assert call(tmp_path, "coverage") == coverage
        assert call(tmp_path, "summary")["current_acceptance"]
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (500, 501))
def test_copy_capacity_and_late_old_job_invalidate_new_storage(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        call(tmp_path, "source", "sync")
        original = call(tmp_path, "pin", "--revision", call(tmp_path, "head")["revision"])[
            "snapshot"
        ]
        identities = [row["id"] for row in pages(tmp_path, "catalog", "--snapshot", original)]
        jobs = [
            call(
                tmp_path,
                "copy",
                "--snapshot",
                original,
                "--id",
                identities[i],
                "--key",
                f"seed-{i}",
            )["job"]
            for i in (0, 3)
        ]
        assert (
            call(
                tmp_path, "copy", "--snapshot", original, "--id", identities[2], "--key", "overflow"
            )["status"]
            == "capacity"
        )
        for job in jobs:
            assert poll(tmp_path, job)["producer_change"] is not None
        current = call(tmp_path, "pin", "--revision", call(tmp_path, "head")["revision"])[
            "snapshot"
        ]
        late = call(
            tmp_path, "copy", "--snapshot", original, "--id", identities[1], "--key", "delayed-old"
        )["job"]
        fresh = call(
            tmp_path, "copy", "--snapshot", current, "--id", identities[1], "--key", "fresh"
        )["job"]
        poll(tmp_path, fresh)
        assert call(tmp_path, "verify", "--id", identities[1])["status"] == "verified"
        assert call(tmp_path, "publish", "--snapshot", current)["status"] == "pending_copy_jobs"
        poll(tmp_path, late)
        assert not call(tmp_path, "inspect", "--id", identities[1])["verified"]
        assert not task.verify(tmp_path).passed
        gold(tmp_path, alternate=True)
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (500, 501))
def test_same_bytes_new_storage_invalidates_manifest_dependency_proof(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        gold(tmp_path)
        snapshot = call(tmp_path, "pin", "--revision", call(tmp_path, "head")["revision"])[
            "snapshot"
        ]
        rows = pages(tmp_path, "catalog", "--snapshot", snapshot)
        leaf = next(row for row in rows if row["id"].endswith("-1"))
        parent = next(row for row in rows if leaf["id"] in row["dependencies"])
        assert call(tmp_path, "inspect", "--id", parent["id"])["verified"]
        job = call(
            tmp_path, "copy", "--snapshot", snapshot, "--id", leaf["id"], "--key", "same-bytes"
        )["job"]
        poll(tmp_path, job)
        call(tmp_path, "verify", "--id", leaf["id"])
        assert not call(tmp_path, "inspect", "--id", parent["id"])["verified"]
        assert call(tmp_path, "publish", "--snapshot", snapshot)["status"] == "incomplete"
        assert not task.verify(tmp_path).passed
        gold(tmp_path, alternate=True)
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (502, 503))
def test_settlement_compensates_own_overpayment_and_preserves_new_external_credits(
    number, tmp_path
):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        gold(tmp_path)
        payments = pages(tmp_path, "ledger")
        assert len([row for row in payments if row["owner"] != "task"]) == 3
        assert not any(row["reversed"] for row in payments if row["owner"] != "task")
        assert any(row["reversed"] for row in payments if row["owner"] == "task")
        assert all(row["remaining"] == 0 for row in pages(tmp_path, "invoices"))
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (504, 505))
def test_staging_one_identity_invalidates_later_receipts_in_same_batch(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        call(tmp_path, "source", "sync")
        snapshot = call(tmp_path, "snapshot")["snapshot"]
        rows = pages(tmp_path, "scan", "--snapshot", snapshot)
        response = send(tmp_path, "stage", {"receipts": [row["receipt"] for row in rows]})
        assert response["items"][0]["status"] == "staged"
        assert all(row["status"] == "stale_receipt" for row in response["items"][1:])
        target = call(tmp_path, "target")
        assert target["staged"]["entity-0"]["source_id"] == "source-0"
        assert target["live"]["user-note"] != target["staged"]["user-note"]
        assert not task.verify(tmp_path).passed
        gold(tmp_path)
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (506, 507))
def test_rollout_keeps_partial_effect_when_operator_supersedes_remaining_profiles(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        call(tmp_path, "sync")
        call(tmp_path, "refresh")
        call(tmp_path, "overlay", "--id", "task-first")
        send(
            tmp_path,
            "stage",
            {"layer": "task-first", "expected_revision": 1, "changes": contract(tmp_path)["goals"]},
        )
        validation = call(tmp_path, "validate")["validation"]
        generation = call(tmp_path, "layers")["live_generation"]
        job = call(
            tmp_path,
            "deploy",
            "--validation",
            validation,
            "--if-live-generation",
            generation,
            "--profiles",
            "canary,prod",
        )["job"]
        assert call(tmp_path, "poll", "--job", job)["status"] == "running"
        partial = call(tmp_path, "poll", "--job", job)
        assert partial["items"] == [{"profile": "canary", "status": "applied"}]
        assert partial["operator_change"]
        assert call(tmp_path, "poll", "--job", job)["status"] == "superseded"
        runtime = call(tmp_path, "runtime", "--profile", "canary", "--fresh")
        assert runtime["values"]["endpoint"] == contract(tmp_path)["goals"]["canary"]["endpoint"]
        assert runtime["values"]["timeout"] == "9"
        assert not task.verify(tmp_path).passed
        gold(tmp_path, alternate=True)
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (508, 509))
def test_presync_cannot_remove_prepare_and_cross_file_commit_conflicts(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        call(tmp_path, "sync")
        info = call(tmp_path, "list")
        assert info["deferred_edits"] == 4
        edits = [
            {**row, "expected_revision": call(tmp_path, "read", "--path", row["path"])["revision"]}
            for row in contract(tmp_path)["changes"]
        ]
        body = {"operation_id": "v7-once", "edits": edits[: info["transaction_capacity"]]}
        outcome = send(tmp_path, "apply", body)
        if outcome["status"] == "ack_unknown":
            outcome = call(tmp_path, "operation", "--id", body["operation_id"])
        assert outcome["status"] == ("rejected" if number == 509 else "partial")
        if number == 509:
            assert "rolled_back" in {row["status"] for row in outcome["items"]}
        else:
            assert "applied" in {row["status"] for row in outcome["items"]}
        assert not task.verify(tmp_path).passed
        gold(tmp_path)
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


def schedule(ws, tests, key):
    manifest = call(ws, "manifest", "--refresh")
    response = send(
        ws,
        "schedule",
        {
            "key": key,
            "run": manifest["run"],
            "fingerprint": manifest["fingerprint"],
            "tests": tests,
            "force": True,
        },
    )
    return response if "job" not in response else poll(ws, response["job"])


@pytest.mark.parametrize("number", (510, 511))
def test_ci_resource_and_cross_group_scope_cannot_be_bypassed_by_preflight(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        if number == 511:
            component = call(tmp_path, "component")
            call(
                tmp_path,
                "repair",
                "--name",
                "parser",
                "--mode",
                "modern",
                "--expected-revision",
                component["revision"],
            )
        assert schedule(tmp_path, ["test-0", "test-3"], "overbook")["status"] == "worker_capacity"
        first = schedule(tmp_path, ["test-0"], "root-a")["items"][0]
        dependent = schedule(tmp_path, ["test-1"], "child-a")["items"][0]
        assert first["status"] == dependent["status"] == "pass"
        assert "test-1" in call(tmp_path, "coverage")["passed"]
        other = schedule(tmp_path, ["test-3"], "root-b")
        assert other["input_updates"] == [{"group": "schema", "input_revision": 3}]
        current = call(tmp_path, "coverage")["passed"]
        assert "test-0" not in current and "test-1" not in current and "test-3" in current
        gold(tmp_path, alternate=True)
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (510, 511))
def test_repeating_successful_prerequisite_invalidates_only_its_descendants(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        gold(tmp_path)
        before = call(tmp_path, "coverage")
        assert before["missing"] == []
        result = schedule(tmp_path, ["test-0"], "unnecessary-root-repeat")
        assert result["items"][0]["status"] == "pass"
        after = call(tmp_path, "coverage")
        assert after["passed"]["test-0"] != before["passed"]["test-0"]
        assert after["passed"]["test-3"] == before["passed"]["test-3"]
        assert after["passed"]["test-4"] == before["passed"]["test-4"]
        assert {"test-1", "test-2", "test-5", "test-6", "test-7"} <= set(after["missing"])
        assert not task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (510, 511))
def test_new_test_job_revokes_acceptance_until_current_artifacts_are_accepted(number, tmp_path):
    task = w.make_task(number)
    task.setup(tmp_path)
    try:
        gold(tmp_path)
        manifest = call(tmp_path, "manifest")
        previous = call(tmp_path, "coverage")
        leaf = next(test for test in manifest["required"] if "discovered" in test)
        body = {
            "key": "new-attempt-after-acceptance",
            "run": manifest["run"],
            "fingerprint": manifest["fingerprint"],
            "tests": [leaf],
            "force": True,
        }
        job = send(tmp_path, "schedule", body)["job"]
        assert not call(tmp_path, "summary")["current_acceptance"]
        assert not task.verify(tmp_path).passed
        assert call(tmp_path, "poll", "--job", job)["status"] == "running"
        assert not task.verify(tmp_path).passed
        assert poll(tmp_path, job)["items"][0]["status"] == "pass"
        current = call(tmp_path, "coverage")
        assert current["missing"] == [] and current["passed"][leaf] != previous["passed"][leaf]
        assert not task.verify(tmp_path).passed
        acceptance = {
            "run": manifest["run"],
            "fingerprint": manifest["fingerprint"],
            "receipts": list(previous["passed"].values()),
        }
        assert send(tmp_path, "accept", acceptance)["status"] == "incomplete"
        acceptance["receipts"] = list(current["passed"].values())
        assert send(tmp_path, "accept", acceptance)["status"] == "accepted"
        assert task.verify(tmp_path).passed
        assert send(tmp_path, "schedule", body)["status"] == "complete"
        assert call(tmp_path, "summary")["current_acceptance"]
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)
