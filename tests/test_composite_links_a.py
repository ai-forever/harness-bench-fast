"""Trusted public-client witnesses and rejection checks for seven feedback pairs."""

import copy

import pytest

from harness_bench.feasibility_tasks.trusted_service import get_session, trusted_service_available
from harness_bench.general_hard import composite_links_a as links
from harness_bench.general_hard import interactive_data as data
from harness_bench.general_hard import interactive_systems as systems
from harness_bench.general_hard import interactive_workflows as workflows
from harness_bench.general_hard.composition import PairRuntime, make_task

pytestmark = pytest.mark.skipif(
    not trusted_service_available(), reason="Unix socket transport unavailable"
)


def _parent_gold(number, workspace, *, alternate=False):
    if number < 486:
        data.SPECS[number][4](workspace, alternate=alternate)
    elif number < 500:
        systems.FAMILIES[number][2](workspace, reverse_order=alternate)
    else:
        family = workflows._case(number)[0]["family"]
        workflows.GOLD[family](workspace, alternate=alternate)


def _break_left(number, state):
    if number in (512, 518):
        state["published"].clear()
    elif number == 513:
        state["sink"].clear()
    elif number == 514:
        state["active"].clear()
    elif number == 515:
        state["committed"].clear()
    elif number == 516:
        state["shipments"].clear()
    else:
        state["ledger"].clear()


def _break_right(number, state):
    if number == 512:
        state["items"]["event-2"]["effect"] += 1
    elif number == 513:
        state["applied"].remove("e7")
    elif number == 514:
        state["verified"].clear()
    elif number == 515:
        state["desired"]["blue"] = "d"
    elif number in (516, 517):
        state["closed"] = False
    else:
        state["published"] = None


@pytest.mark.parametrize("number", sorted(links.PAIRS))
def test_public_witness_and_both_halves_required(tmp_path, number):
    task = make_task(number)
    task.setup(tmp_path)
    try:
        task.apply_gold(tmp_path)
        outcome = task.verify(tmp_path)
        assert outcome.passed, outcome.message
        session = get_session(tmp_path)
        assert not session.protocol_errors
        assert session.state["clock"] <= 280, "A witness needs headroom under the shared320 limit"
        assert len(session.state["coupling"]["events"]) == 2
        runtime = PairRuntime(number)
        for lane, breaker in (("left", _break_left), ("right", _break_right)):
            broken = copy.deepcopy(session.state)
            breaker(number, broken[lane])
            assert runtime.check(broken) is not None
        missing_feedback = copy.deepcopy(session.state)
        missing_feedback["coupling"]["phase"] = 1
        missing_feedback["coupling"]["events"].pop()
        assert runtime.check(missing_feedback) is not None
        # Reads cannot repeat a cross-transaction or create a third correction.
        before = copy.deepcopy(session.state["coupling"])
        links._pair_status(tmp_path)
        systems.send(tmp_path / "left", "head")
        systems.send(tmp_path / "right", "status")
        assert session.state["coupling"] == before
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


def test_513_premature_irreversible_ack_is_not_secretly_reset(tmp_path):
    task = make_task(513)
    task.setup(tmp_path)
    try:
        systems.predicate_gold_v8(tmp_path / "right")
        before = copy.deepcopy(get_session(tmp_path).state["right"])
        data.gold_stream_v8(tmp_path / "left")
        after = get_session(tmp_path).state
        assert after["coupling"]["phase"] == 0
        assert after["right"] == before
        assert not task.verify(tmp_path).passed
        assert "undo" in links.PAIRS[513]["docs"]
        assert "str(10*k)" in links.PAIRS[513]["docs"]
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", sorted(links.PAIRS))
def test_independent_parent_solutions_do_not_solve_feedback(tmp_path, number):
    """Real public execution, with neither state corruption nor suppressed errors."""
    pair = links.PAIRS[number]
    task = make_task(number)
    task.setup(tmp_path)
    try:
        _parent_gold(pair["left"], tmp_path / "left")
        _parent_gold(pair["right"], tmp_path / "right")
        status = links._pair_status(tmp_path)
        assert not status["joint_done"]
        # 514's original editor does not perform the new summary replacement.
        # The other original right clients finish, releasing new LEFT work.
        if number == 514:
            assert status["coupling"]["phase"] == 1
            assert not status["right_done"]
        else:
            assert status["coupling"]["phase"] == 2
            assert status["right_done"]
            assert not status["left_done"]
        result = task.verify(tmp_path)
        assert not result.passed
        assert not get_session(tmp_path).protocol_errors
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize(("number", "alternate"), [(512, False), (514, True)])
def test_right_first_is_recoverable_with_public_compensation(tmp_path, number, alternate):
    """Delivery compensation and an alternate atomic-edit order remain valid."""
    task = make_task(number)
    task.setup(tmp_path)
    try:
        _parent_gold(links.PAIRS[number]["right"], tmp_path / "right", alternate=alternate)
        before = links._pair_status(tmp_path)
        assert before["right_done"]
        assert before["coupling"]["phase"] == 0
        task.apply_gold(tmp_path)
        result = task.verify(tmp_path)
        assert result.passed, result.message
        session = get_session(tmp_path)
        assert not session.protocol_errors
        assert session.state["clock"] <= 300
        assert links._pair_status(tmp_path)["joint_done"]
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", [512, 513, 514, 515, 516, 517, 518])
def test_empty_pair_cannot_claim_cross_completion(number):
    runtime = PairRuntime(number)
    state = runtime.initial_state()
    before = copy.deepcopy(state)
    links.PAIRS[number]["after"](state, "left", ["head"], {}, {}, 0)
    assert state == before
    assert links.PAIRS[number]["check"](state) is not None
