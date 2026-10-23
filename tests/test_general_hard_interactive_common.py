"""Trusted replay accepts recoverable failures, but not forged state or history."""

import json

import pytest

from harness_bench.core import Task, VerifyResult
from harness_bench.feasibility_tasks.trusted_service import (
    evidence_context,
    get_session,
    trusted_service_available,
)
from harness_bench.general_hard.interactive_common import interactive_task, request

pytestmark = pytest.mark.skipif(
    not trusted_service_available(), reason="Unix socket transport unavailable"
)

SOURCE = '''
def initial_state():
    return {"effects": 0, "keys": []}

def handle(state, words, opts, io):
    if words == ["aliased-status"]:
        return {"keys": state["keys"]}, 0
    if words == ["write-file"]:
        state["effects"] += 10
        io.read(opts["file"])
        return {"status": "ok"}, 0
    if words == ["status"]:
        return {"effects": state["effects"], "keys": list(state["keys"])}, 0
    if words == ["commit"] and isinstance(opts.get("key"), str):
        if opts["key"] in state["keys"]:
            return {"status": "already_committed"}, 0
        state["keys"].append(opts["key"])
        state["effects"] += 1
        return {"status": "ack_unknown", "key": opts["key"]}, 75
    return {"status": "bad_arguments", "commands": ["status", "commit --key KEY"]}, 2
'''


def _gold(workspace):
    response, code = request(workspace, "commit", "--key", "stable-key")
    assert code == 75 and response["status"] == "ack_unknown"
    response, code = request(workspace, "commit", "--key", "stable-key")
    assert code == 0 and response["status"] == "already_committed"


def _task(**kwargs):
    base = Task("task_472_test", "test", "", lambda _: VerifyResult(False), tags=("parent-352",))
    return interactive_task(
        base, prompt="Commit exactly one effect.",
        docs="status is read-only; commit --key KEY is idempotent and may lose its ack.",
        source=SOURCE, check=lambda state: None if state["effects"] == 1 else "wrong effects",
        gold=_gold, **kwargs,
    )


@pytest.fixture
def live_task(tmp_path):
    task = _task()
    task.setup(tmp_path)
    try:
        yield task, tmp_path
    finally:
        task.cleanup(tmp_path)


def test_expected_nonzero_reply_and_idempotent_recovery_pass(live_task):
    task, workspace = live_task
    task.apply_gold(workspace)
    assert task.verify(workspace).passed
    assert get_session(workspace).state["effects"] == 1


def test_status_recovery_and_extra_reads_are_alternative_valid_path(live_task):
    task, workspace = live_task
    request(workspace, "unknown-command")
    request(workspace, "status")
    request(workspace, "commit", "--key", "a-different-key")
    response, code = request(workspace, "status")
    assert code == 0 and response["effects"] == 1
    assert task.verify(workspace).passed


def test_new_key_after_unknown_ack_creates_rejected_duplicate(live_task):
    task, workspace = live_task
    request(workspace, "commit", "--key", "a")
    request(workspace, "commit", "--key", "b")
    assert not task.verify(workspace).passed


def test_transport_resource_errors_cannot_bypass_call_accounting(live_task):
    task, workspace = live_task
    task.apply_gold(workspace)
    get_session(workspace).protocol_error("transport resource budget exceeded")
    assert not task.verify(workspace).passed


def test_malformed_command_rolls_back_and_allows_recovery(live_task):
    task, workspace = live_task
    for args in [("write-file",), ("write-file", "--file", "missing.json")]:
        response, code = request(workspace, *args)
        assert code == 2 and response["status"] == "invalid_arguments"
        assert get_session(workspace).state["effects"] == 0
    task.apply_gold(workspace)
    assert task.verify(workspace).passed


def test_later_state_changes_cannot_rewrite_a_recorded_response(live_task):
    task, workspace = live_task
    request(workspace, "aliased-status")
    task.apply_gold(workspace)
    assert get_session(workspace).records[0]["response"] == {"keys": []}
    assert task.verify(workspace).passed


def test_fake_answer_and_fake_local_state_do_not_change_service(live_task):
    task, workspace = live_task
    (workspace / "answer.json").write_text('{"effects": 1}')
    (workspace / "state.json").write_text('{"effects": 1}')
    assert not task.verify(workspace).passed
    assert get_session(workspace).state["effects"] == 0
    task.apply_gold(workspace)
    assert task.verify(workspace).passed


@pytest.mark.parametrize("relative", ["tools/taskctl", "docs/taskctl.md", ".svc/connection.json"])
def test_fixed_input_mutation_rejected(live_task, relative):
    task, workspace = live_task
    task.apply_gold(workspace)
    with (workspace / relative).open("a") as handle:
        handle.write("\nmodified\n")
    assert not task.verify(workspace).passed


def test_budget_counts_reads_and_failed_calls(tmp_path):
    task = _task(max_calls=3)
    task.setup(tmp_path)
    try:
        task.apply_gold(tmp_path)
        request(tmp_path, "status")
        assert task.verify(tmp_path).passed
        _, code = request(tmp_path, "not-a-command")
        assert code == 2
        assert not task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


def test_frozen_export_replays_without_live_host_session(live_task, tmp_path_factory):
    task, workspace = live_task
    task.apply_gold(workspace)
    evidence = tmp_path_factory.mktemp("evidence") / "history.json"
    session = get_session(workspace)
    session.freeze()
    session.export(evidence)
    task.cleanup(workspace)
    assert not task.verify(workspace).passed
    with evidence_context(workspace, evidence):
        assert task.verify(workspace).passed
    payload = json.loads(evidence.read_text())
    payload["records"][0]["response"]["status"] = "forged_success"
    evidence.write_text(json.dumps(payload))
    with evidence_context(workspace, evidence):
        assert not task.verify(workspace).passed


@pytest.mark.parametrize("relative", ["../outside", "/absolute", ".svc", ".svc/state.json"])
def test_author_fixture_paths_cannot_escape_or_replace_transport(relative):
    with pytest.raises(ValueError, match="reserved fixture path"):
        _task(files={relative: "not a valid fixture"})


def test_public_workspace_contains_no_service_code_or_state(live_task):
    task, workspace = live_task
    assert task.requires_trusted_service
    assert "wave20-interactive-v8" in task.runtime_protocol
    assert task.min_timeout_seconds == task.max_timeout_seconds == 900
    assert "_workflow_handle" not in (workspace / "tools/taskctl").read_text()
    assert set(p.name for p in (workspace / ".svc").iterdir()) == {"connection.json"}
