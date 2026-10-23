"""Public-transport witnesses and negative controls for pairs 526--531."""

from __future__ import annotations

import copy
import json

import pytest

from harness_bench.feasibility_tasks.trusted_service import (
    evidence_context,
    get_session,
    trusted_service_available,
)
from harness_bench.general_hard import composite_links_c as links
from harness_bench.general_hard import composition
from harness_bench.general_hard import interactive_systems as systems
from harness_bench.general_hard import interactive_workflows as workflows

pytestmark = pytest.mark.skipif(
    not trusted_service_available(), reason="Unix socket transport unavailable"
)


def _parent_gold(number, workspace):
    if number < 500:
        systems.FAMILIES[number][2](workspace)
    else:
        family = workflows._case(number)[0]["family"]
        workflows.GOLD[family](workspace)


@pytest.mark.parametrize("number", range(526, 532))
@pytest.mark.parametrize("peer_first", (False, True))
def test_public_gold_and_trusted_replay(number, peer_first, tmp_path, record_property):
    task = composition.make_task(number)
    task.setup(tmp_path)
    try:
        assert not task.verify(tmp_path).passed
        if peer_first:
            _parent_gold(links.PAIRS[number]["right"], tmp_path / "right")
        task.apply_gold(tmp_path)
        result = task.verify(tmp_path)
        assert result.passed, result.message
        session = get_session(tmp_path)
        state = session.replay().state
        assert state["coupling"]["phase"] == 3
        events = state["coupling"]["events"]
        assert [event["direction"] for event in events] == [
            "left->right", "right->left", "left->right"
        ]
        assert all(event["data"] for event in events)
        assert len(session.records) <= composition.MAX_CALLS - 20
        record_property("service_calls", len(session.records))
        assert all(record["argv"][0] in ("left", "right", "pair") for record in session.records)
        # Once the bounded handoffs are complete, extra observations do not
        # create obligations or change either domain (except its command clock).
        prior = copy.deepcopy(state["coupling"])
        for _ in range(2):
            links._call(tmp_path, "pair", "status")
        assert get_session(tmp_path).replay().state["coupling"] == prior
        session.freeze()
        evidence = tmp_path.with_suffix(".trusted.json")
        session.export(evidence)
        with evidence_context(tmp_path, evidence):
            assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", range(526, 532))
def test_independent_parent_solutions_are_not_joint_completion(number, tmp_path):
    pair = links.PAIRS[number]
    task = composition.make_task(number)
    task.setup(tmp_path)
    try:
        _parent_gold(pair["left"], tmp_path / "left")
        if number == 530:
            # The old prod endpoint cannot satisfy the new prerequisite for
            # batch.  Its immutable-contract witness must not count as a pass.
            with pytest.raises(AssertionError, match="rollout failed to reach current runtime goals"):
                _parent_gold(pair["right"], tmp_path / "right")
        else:
            _parent_gold(pair["right"], tmp_path / "right")
        result = task.verify(tmp_path)
        assert not result.passed
        state = get_session(tmp_path).replay().state
        assert state["coupling"]["phase"] < 3
        assert not links._call(tmp_path, "pair", "status")["joint_done"]
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", (526, 527))
def test_migration_override_rejects_stale_replica_and_safety_violation(number, tmp_path):
    task = composition.make_task(number)
    task.setup(tmp_path)
    try:
        task.apply_gold(tmp_path)
        state = get_session(tmp_path).replay().state["left"]
        assert links._migration_check(state) is None
        damaged = copy.deepcopy(state)
        damaged["target"]["f5"]["rev"] -= 1
        assert links._migration_check(damaged) is not None
        damaged = copy.deepcopy(state)
        damaged["violations"].append("removed protected source")
        assert links._migration_check(damaged) is not None
        damaged = copy.deepcopy(state)
        damaged["sources"]["f5"]["removed"] = False
        assert links._migration_check(damaged) is not None
    finally:
        task.cleanup(tmp_path)


def test_event_payloads_are_frozen_values():
    state = {"coupling": {"phase": 0, "events": []}}
    transferred = {"rows": [{"id": "a", "revision": 1}]}
    links._record(state, "left->right", "fixture", transferred=transferred)
    transferred["rows"][0]["revision"] = 2
    assert state["coupling"]["events"][0]["data"]["transferred"]["rows"][0]["revision"] == 1


def test_terminal_phase_alone_is_insufficient():
    state = {"coupling": {"phase": 3, "events": [{"number": 1}, {"number": 3}]}}
    assert links._complete(state) is not None


def test_530_public_goal_handoff_does_not_auto_deploy_or_rewrite_fixture(tmp_path):
    task = composition.make_task(530)
    task.setup(tmp_path)
    try:
        child = tmp_path / "right"
        initial_contract = (child / "contract.json").read_bytes()
        workflows._gold_config(child)
        systems.config_gold_v8(tmp_path / "left")
        first = links._events(tmp_path)[0]["data"]
        status = links._call(child, "status")
        assert status["goal_revision"] == first["goal_revision"] == 1
        assert status["goals"] == first["goals"]
        assert first["goals"]["prod"]["endpoint"] == "handoff-" + first["route"] + ".internal"
        actual = links._call(child, "runtime", "--profile", "prod", "--fresh")["values"]
        old_goals = json.loads(initial_contract)["goals"]
        assert actual["endpoint"] == old_goals["prod"]["endpoint"]
        assert actual["endpoint"] != first["goals"]["prod"]["endpoint"]
        # The old witness sees its own fixed goals satisfied and returns, but
        # the current cross-workflow obligation remains unsatisfied.
        workflows._gold_config(child)
        assert not links._call(tmp_path, "pair", "status")["right_done"]
        assert not task.verify(tmp_path).passed
        assert (child / "contract.json").read_bytes() == initial_contract
        assert get_session(tmp_path).replay().state["right"]["violations"] == []
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("profile,key", (("prod", "endpoint"), ("batch", "timeout")))
def test_530_deploying_stale_fixed_goal_is_rejected(profile, key, tmp_path):
    task = composition.make_task(530)
    task.setup(tmp_path)
    try:
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed
        child = tmp_path / "right"
        original = json.loads((child / "contract.json").read_text())
        public = links._call(child, "status")
        stale = original["goals"][profile][key]
        assert public["goals"][profile][key] != stale
        assert public["goal_revision"] == 2
        assert public["goals"] == links._events(tmp_path)[2]["data"]["goals"]
        workflows._call(child, "refresh")
        layer = workflows._call(child, "overlay", "--id", "task-stale")["layer"]
        current = workflows._call(child, "read", "--layer", layer, "--view", "draft")
        workflows._send(child, "stage", {
            "layer": layer, "expected_revision": current["revision"],
            "changes": {profile: {key: stale}},
        })
        receipt = workflows._call(child, "validate")
        layers = workflows._call(child, "layers")
        job = workflows._call(
            child, "deploy", "--validation", receipt["validation"],
            "--if-live-generation", layers["live_generation"], "--profiles", profile,
        )["job"]
        outcome = workflows._poll(child, job)
        assert outcome["items"] == [{"profile": profile, "status": "applied"}]
        for name in original["profiles"]:
            values = workflows._call(child, "runtime", "--profile", name, "--fresh")["values"]
            if name == profile:
                assert values[key] == stale
        status = links._call(tmp_path, "pair", "status")
        assert status["coupling"]["phase"] == 3
        assert status["left_done"] and not status["right_done"]
        assert not task.verify(tmp_path).passed
        state = get_session(tmp_path).replay().state
        assert state["right"]["goals"] == public["goals"]
        assert not state["right"]["violations"]
        assert len(get_session(tmp_path).records) < composition.MAX_CALLS
    finally:
        task.cleanup(tmp_path)
