"""Public finite witnesses and feedback-obligation checks for pairs 519--525."""

from contextlib import contextmanager

import pytest

from harness_bench.feasibility_tasks.trusted_service import get_session, trusted_service_available
from harness_bench.general_hard import composite_links_b as links
from harness_bench.general_hard.composition import PairRuntime, get_pair, make_task, original_tasks

pytestmark = pytest.mark.skipif(
    not trusted_service_available(), reason="Unix socket transport unavailable"
)


@contextmanager
def live(path, number):
    task = make_task(number)
    task.setup(path)
    try:
        yield task
        assert not get_session(path).protocol_errors
    finally:
        task.cleanup(path)


@pytest.mark.parametrize("number", range(519, 526))
def test_public_gold_reconciles_two_data_handoffs(tmp_path, number):
    with live(tmp_path, number) as task:
        task.apply_gold(tmp_path)
        result = task.verify(tmp_path)
        assert result.passed, result.message
        session = get_session(tmp_path)
        link = session.state["coupling"]
        assert link["phase"] == 2
        assert len(link["events"]) == 2
        assert all(
            "source_value" in event and "source_revision" in event for event in link["events"]
        )
        assert session.state["clock"] <= 285, (number, session.state["clock"])
        assert links._root_status(tmp_path)["joint_done"]


@pytest.mark.parametrize("number", range(519, 526))
def test_first_completed_lane_is_not_joint_success(tmp_path, number):
    with live(tmp_path, number) as task:
        links.LEFT_GOLDS[number](tmp_path / "left")
        state = get_session(tmp_path).state
        assert state["coupling"]["phase"] == 1
        assert PairRuntime(number).lane_check(state, "left") is None
        assert not task.verify(tmp_path).passed


@pytest.mark.parametrize("number", range(519, 526))
def test_peer_completed_before_handoff_remains_recoverable(tmp_path, number):
    with live(tmp_path, number) as task:
        # Independent completion is legal, but the subsequent source handoff
        # must reopen its real obligations without destroying recoverability.
        original_tasks()[get_pair(number)["right"]].apply_gold(tmp_path / "right")
        assert get_session(tmp_path).state["coupling"]["phase"] == 0
        task.apply_gold(tmp_path)
        result = task.verify(tmp_path)
        assert result.passed, result.message
        assert get_session(tmp_path).state["clock"] <= 320


@pytest.mark.parametrize("number", range(519, 526))
def test_independent_parent_golds_once_do_not_solve_feedback(tmp_path, number):
    with live(tmp_path, number) as task:
        pair = get_pair(number)
        parents = original_tasks()
        parents[pair["left"]].apply_gold(tmp_path / "left")
        if number == 522:
            # The old immutable timeout targets no longer meet the canary
            # prerequisite of prod.  Accept only this specific policy mismatch,
            # never transport, key/schema errors or an arbitrary assertion.
            with pytest.raises(
                AssertionError, match="^rollout failed to reach current runtime goals$"
            ):
                parents[pair["right"]].apply_gold(tmp_path / "right")
        else:
            parents[pair["right"]].apply_gold(tmp_path / "right")
        observed = links._root_status(tmp_path)
        assert not observed["joint_done"]
        assert observed["coupling"]["phase"] == (1 if number == 522 else 2)
        stale_lane = "right_done" if number == 522 else "left_done"
        assert not observed[stale_lane]
        result = task.verify(tmp_path)
        assert not result.passed, result.message
