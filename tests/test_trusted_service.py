"""Acceptance tests for the wave-19 boundary, using real client/socket calls.

These regressions reproduce the old target-disclosure/history-rollback route,
then challenge the new transport without depending on its private internals.
"""

from __future__ import annotations

import ast
import base64
import json
import os
import shutil
import socket
import time
import zlib
from contextlib import suppress

import pytest

from harness_bench.feasibility_tasks import FEASIBILITY_CASES, FEASIBILITY_TASKS, hard_search
from harness_bench.feasibility_tasks.service import contract_service
from harness_bench.feasibility_tasks.trusted_service import (
    MAX_EVIDENCE_BYTES,
    MAX_FILE_BYTES,
    MAX_REQUEST_BYTES,
    MAX_REQUESTS,
    close_session,
    evidence_context,
    get_session,
    trusted_service_available,
)
from harness_bench.reflect_tasks.common import (
    JOURNAL_FILE,
    STATE_FILE,
    call,
)
from harness_bench.reflect_tasks.common import (
    replay as legacy_replay,
)

pytestmark = pytest.mark.skipif(
    not trusted_service_available(), reason="Unix-socket host service unavailable"
)


def task_number(number):
    return next(task for task in FEASIBILITY_TASKS if task.id.split("_")[1] == str(number))


def client_service(task):
    entry = FEASIBILITY_CASES[task.id]
    identity = task.id.split("_")[1]
    if entry["kind"] == "contract":
        return contract_service(identity)
    return hard_search.service(identity, entry["pair"][entry["side"]])


def send_payload(ws, task, command, payload):
    (ws / "answer.json").write_text(json.dumps(payload), encoding="utf-8")
    return call(ws, client_service(task), command, "--file", "answer.json")


def wire(ws, data):
    """A model can connect directly, so framing errors must be host-observable."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(str(get_session(ws).socket_path))
        # Oversized input may be rejected before all its bytes are received.
        with suppress(BrokenPipeError, ConnectionResetError):
            connection.sendall(data)
        response = bytearray()
        while not response.endswith(b"\n"):
            try:
                part = connection.recv(65536)
            except ConnectionResetError:
                break
            if not part:
                break
            response.extend(part)
            assert len(response) <= 1_000_000, "unbounded error response"
        assert response, "transport silently dropped a malformed request"
        return json.loads(response)


def rpc(ws, argv):
    return wire(ws, json.dumps({"argv": argv}).encode() + b"\n")


def test_original_437_decoder_has_no_target_or_service_state_to_recover(tmp_path):
    first, second = task_number(437), task_number(451)
    workspaces = [tmp_path / "first", tmp_path / "second"]
    for task, ws in zip((first, second), workspaces, strict=True):
        task.setup(ws)
        client = (ws / "tools/taskctl").read_text()
        ast.parse(client)  # A readable, ordinary transport rather than packed code.
        assert "b85decode" not in client and "zlib.decompress" not in client
        assert "TARGET" not in client and "CONFIG" not in client and "_KEY" not in client
        assert not (ws / JOURNAL_FILE).exists()
        assert not (ws / STATE_FILE).exists()
        entry = FEASIBILITY_CASES[task.id]
        spec = entry["pair"][entry["side"]]
        found_id = hard_search.target(spec)[2]
        for path in ws.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                assert found_id.encode() not in data
                assert b"target_seed" not in data
        assert {p.name for p in (ws / ".svc").iterdir()} == {"connection.json"}
    assert (workspaces[0] / "tools/taskctl").read_bytes() == (
        workspaces[1] / "tools/taskctl"
    ).read_bytes()
    assert get_session(workspaces[0]).socket_path != get_session(workspaces[1]).socket_path


def test_valid_legacy_hmac_history_cannot_forge_a_new_session_success(tmp_path):
    task = task_number(437)
    ws, forged = tmp_path / "actual", tmp_path / "old-client"
    task.setup(ws)
    entry = FEASIBILITY_CASES[task.id]
    spec = entry["pair"][entry["side"]]
    service = client_service(task)
    # Construct a fully valid old-style history, not merely arbitrary bad JSON.
    (forged / "tools").mkdir(parents=True)
    (forged / ".svc").mkdir()
    (forged / "tools/taskctl").write_text(service.client_script())
    (forged / STATE_FILE).write_text(service.state_blob())
    shard, rank, found_id = hard_search.target(spec)
    route = next(
        r
        for r in spec["routes"]
        if r["shard"] == shard
        and rank < r["prefix"]
        and r["id"] in spec["enabled"]
        and r["query_cost"] <= spec["query_budget"]
        and r["io_cost"] <= spec["io_budget"]
    )
    response, code = call(forged, service, "probe", "--route", route["id"])
    assert code == 0 and response["found_id"] == found_id
    (forged / "answer.json").write_text(json.dumps({"found_id": found_id}))
    _, code = call(forged, service, "finish", "--file", "answer.json")
    assert code == 0
    old = legacy_replay(forged, service)
    assert not isinstance(old, str) and hard_search.assess(spec, old.state)[0]
    for filename in (STATE_FILE, JOURNAL_FILE):
        shutil.copyfile(forged / filename, ws / filename)
    (ws / "answer.json").write_text(json.dumps({"found_id": found_id}))
    assert get_session(ws).replay().calls == []
    assert not task.verify(ws).passed


def test_restoring_every_workspace_file_cannot_erase_a_bogus_call(tmp_path):
    task = task_number(443)
    task.setup(tmp_path)
    snapshot = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    _, code = call(tmp_path, client_service(task), "bogus")
    assert code != 0
    for path, content in snapshot.items():
        (tmp_path / path).write_bytes(content)
    task.apply_gold(tmp_path)
    result = task.verify(tmp_path)
    assert not result.passed
    assert json.loads(result.message)["wave19"]["invalid_calls"] >= 1


def test_real_lucky_find_still_passes_without_a_plan_or_diagnosis(tmp_path):
    task = task_number(451)
    task.setup(tmp_path)
    entry = FEASIBILITY_CASES[task.id]
    spec = entry["pair"][entry["side"]]
    shard, rank, found_id = hard_search.target(spec)
    route = next(
        r
        for r in spec["routes"]
        if r["shard"] == shard
        and rank < r["prefix"]
        and r["id"] in spec["enabled"]
        and r["query_cost"] <= spec["query_budget"]
        and r["io_cost"] <= spec["io_budget"]
    )
    response, code = call(tmp_path, client_service(task), "probe", "--route", route["id"])
    assert code == 0 and response["found_id"] == found_id
    response, code = send_payload(tmp_path, task, "finish", {"found_id": found_id})
    assert code == 0, response
    result = task.verify(tmp_path)
    assert result.passed, result.message
    metrics = json.loads(result.message)["wave19"]
    assert metrics["success_basis"] == "verified_find" and not metrics["strategy_correct"]


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json\n",
        b"",
        b"\xff\n",
        b"null\n",
        b"[]\n",
        b'{"argv":null}\n',
        b'{"argv":[1]}\n',
        b'{"argv":["count"],"files":{"fake":"answer"}}\n',
        b'{"argv":["count"],"argv":["describe"]}\n',
    ],
)
def test_malformed_direct_requests_remain_errors_after_valid_solution(tmp_path, payload):
    task = task_number(443)
    task.setup(tmp_path)
    response = wire(tmp_path, payload)
    assert response["code"] != 0
    task.apply_gold(tmp_path)
    assert not task.verify(tmp_path).passed


def test_oversized_direct_request_is_bounded_and_permanently_rejected(tmp_path):
    task = task_number(443)
    task.setup(tmp_path)
    response = wire(tmp_path, b'{"argv":["' + b"a" * MAX_REQUEST_BYTES + b'"]}\n')
    assert response["code"] != 0
    task.apply_gold(tmp_path)
    assert not task.verify(tmp_path).passed


def test_one_connection_cannot_execute_a_hidden_second_command(tmp_path):
    task = task_number(443)
    task.setup(tmp_path)
    response = wire(tmp_path, b'{"argv":["count"]}\n{"argv":["bogus"]}\n')
    assert response["code"] == 0
    assert [argv for argv, _, _ in get_session(tmp_path).replay().calls] == [["count"]]


def test_direct_request_flood_cannot_grow_an_unbounded_trusted_history(tmp_path):
    task = task_number(443)
    task.setup(tmp_path)
    for _ in range(MAX_REQUESTS + 2):
        response = rpc(tmp_path, ["bogus"])
        assert response["code"] != 0
    evidence = get_session(tmp_path).replay()
    assert len(evidence.calls) <= MAX_REQUESTS + 1
    assert not task.verify(tmp_path).passed


@pytest.mark.parametrize("byte", [b"x", b"\x00"])
def test_repeated_bad_json_files_cannot_exhaust_or_invalidate_host_evidence(tmp_path, byte):
    task = task_number(443)
    ws = tmp_path / "workspace"
    task.setup(ws)
    # NULs expand sixfold in a JSON history. The raw-input cap alone would not
    # bound the exported artifact or ensure that the offline reader accepts it.
    (ws / "bad.json").write_bytes(byte * (MAX_FILE_BYTES * 3 // 4))
    for _ in range(4):
        response = rpc(ws, ["plan", "--file", "bad.json"])
        assert response["code"] != 0
    evidence = tmp_path / "host-evidence.json"
    get_session(ws).export(evidence)
    assert evidence.stat().st_size <= MAX_EVIDENCE_BYTES
    close_session(ws)
    with evidence_context(ws, evidence):
        result = task.verify(ws)
        assert not result.passed
        # The bounded history remains replayable; rejected calls still count.
        assert json.loads(result.message)["wave19"]["invalid_calls"] >= 1


@pytest.mark.parametrize("kind", ["fifo", "oversized", "outside", "symlink_outside"])
def test_host_file_reads_reject_unbounded_or_external_objects(tmp_path, kind):
    task = task_number(432)
    ws = tmp_path / "workspace"
    task.setup(ws)
    source = ws / "answer.json"
    if kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO unavailable")
        os.mkfifo(source)
    elif kind == "oversized":
        with source.open("wb") as output:
            output.truncate(MAX_FILE_BYTES + 1)
    else:
        outside = tmp_path / "outside.json"
        outside.write_text('{"status":"solved","solution":{}}')
        if kind == "symlink_outside":
            source.symlink_to(outside)
        else:
            source = outside
    started = time.monotonic()
    response = rpc(ws, ["submit", "--file", str(source)])
    assert time.monotonic() - started < 3
    assert response["code"] != 0
    assert not get_session(ws).replay().state["submitted"]
    if source == ws / "answer.json":
        source.unlink()
    task.apply_gold(ws)
    assert not task.verify(ws).passed


def test_replay_is_a_copy_and_freeze_preserves_timely_evidence(tmp_path):
    task = task_number(443)
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    session = get_session(tmp_path)
    first = session.replay()
    expected_calls = len(first.calls)
    first.calls.clear()
    first.state.clear()
    assert len(session.replay().calls) == expected_calls
    assert task.verify(tmp_path).passed
    session.freeze()
    before = session.replay()
    _, code = call(tmp_path, client_service(task), "bogus")
    assert code != 0
    after = session.replay()
    assert before.state == after.state and before.calls == after.calls
    assert task.verify(tmp_path).passed


def test_pending_incomplete_request_cannot_change_frozen_success(tmp_path):
    task = task_number(443)
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    session = get_session(tmp_path)
    before = session.replay()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as pending:
        pending.settimeout(3)
        pending.connect(str(session.socket_path))
        pending.sendall(b'{"argv":')  # No newline: the host reader is still waiting.
        session.freeze()
    after = session.replay()
    assert before.state == after.state and before.calls == after.calls
    assert task.verify(tmp_path).passed


def test_missing_host_session_cannot_be_replaced_with_workspace_files(tmp_path):
    task = task_number(443)
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    assert task.verify(tmp_path).passed
    close_session(tmp_path)
    (tmp_path / JOURNAL_FILE).write_text("[]\n")
    (tmp_path / STATE_FILE).write_text(base64.b85encode(zlib.compress(b"{}")).decode())
    assert not task.verify(tmp_path).passed


def test_offline_replay_requires_matching_host_supplied_evidence(tmp_path):
    task = task_number(443)
    ws = tmp_path / "workspace"
    task.setup(ws)
    task.apply_gold(ws)
    path = tmp_path / "host-evidence.json"
    get_session(ws).export(path)
    assert path.is_file()
    close_session(ws)
    assert not task.verify(ws).passed
    with evidence_context(ws, path):
        result = task.verify(ws)
        assert result.passed, result.message
    other = task_number(465)
    with evidence_context(ws, path):
        assert not other.verify(ws).passed
    inside = ws / "pretend-host.json"
    shutil.copyfile(path, inside)
    with pytest.raises(ValueError, match="outside"), evidence_context(ws, inside):
        pass


def test_export_refuses_to_place_authoritative_history_in_the_workspace(tmp_path):
    task = task_number(443)
    task.setup(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        get_session(tmp_path).export(tmp_path / "history.json")
    assert not (tmp_path / "history.json").exists()
