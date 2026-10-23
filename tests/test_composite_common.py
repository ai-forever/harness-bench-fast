"""Composition must preserve clock, IO, replay and domain-effect boundaries."""

from __future__ import annotations

import copy
from collections import Counter

import pytest

from harness_bench.core import Task, VerifyResult
from harness_bench.feasibility_tasks.trusted_service import get_session, trusted_service_available
from harness_bench.general_hard import composition
from harness_bench.general_hard.composite_tasks import (
    COMPOSITE_CALIBRATION_TASKS,
    COMPOSITE_HARD_TASKS,
    SELECTED_COMPOSITE_NUMBERS,
)
from harness_bench.general_hard.interactive_common import (
    interactive_task,
    request,
    workflow_definition,
)
from harness_bench.tasks import ALL_TASKS, SUITES

pytestmark = pytest.mark.skipif(
    not trusted_service_available(), reason="Unix socket transport unavailable",
)

SOURCE = '''
def initial_state():
    return {"effects": 0, "target": 1, "seen": []}

def handle(state, words, opts, io):
    if words == ["read"]:
        return {"text": io.read(opts["file"])}, 0
    if words == ["set"]:
        state["effects"] = int(opts["value"])
        state["seen"].append(state["effects"])
        return {"status": "outcome_unknown", "seen": state["seen"]}, 75
    if words == ["bad"]:
        state["effects"] = 999
        raise ValueError("malformed")
    return {"status": "ok", "effects": state["effects"], "target": state["target"]}, 0
'''


def _goal(state):
    return None if state["effects"] == state["target"] else "current target unmet"


def _after(state, lane, words, opts, response, code):
    phase = state["coupling"]["phase"]
    if phase == 0 and _goal(state["left"]) is None:
        state["right"]["target"] = state["left"]["effects"] + 1
    elif phase == 1 and _goal(state["right"]) is None:
        state["left"]["target"] = state["right"]["effects"] + 1
    else:
        return
    state["coupling"]["phase"] += 1
    state["coupling"]["events"].append({"phase": phase + 1, "seen": state[lane]["seen"][:]})


@pytest.fixture
def live_pair(tmp_path, monkeypatch):
    originals = {}
    for number in (1, 2):
        originals[number] = interactive_task(
            Task(f"task_{number}_fixture", "fixture", "", lambda _: VerifyResult(False)),
            source=SOURCE, prompt="Reach current target", docs="set --value N; read --file PATH",
            check=_goal, gold=lambda _: None, files={"input.txt": str(number)},
        )
    pair = {
        "left": 1, "right": 2, "slug": "test_pair", "title": "test pair",
        "docs": "left effect changes right target; right effect changes left target.",
        "after": _after, "check": lambda s: None if s["coupling"]["phase"] == 2 else "handoff missing",
        "gold": lambda _: None,
    }
    monkeypatch.setattr(composition, "get_pair", lambda _: pair)
    monkeypatch.setattr(composition, "original_tasks", lambda: originals)
    task = composition.make_task(999)
    task.setup(tmp_path)
    try:
        yield task, tmp_path
    finally:
        task.cleanup(tmp_path)


def _finish(workspace):
    request(workspace, "left", "set", "--value", "1")
    request(workspace, "right", "set", "--value", "2")
    request(workspace, "left", "set", "--value", "3")


def test_unknown_ack_triggers_once_and_history_stays_frozen(live_pair):
    task, ws = live_pair
    response, code = request(ws, "left", "set", "--value", "1")
    assert code == 75 and response["coupling_events"][0]["phase"] == 1
    before = copy.deepcopy(get_session(ws).records[0])
    request(ws, "left", "status")
    request(ws, "right", "set", "--value", "2")
    assert not task.verify(ws).passed
    request(ws, "left", "set", "--value", "3")
    assert task.verify(ws).passed
    assert get_session(ws).records[0] == before
    assert get_session(ws).replay().state == get_session(ws).state


def test_failed_child_command_preserves_peer_and_clock_is_lane_scoped(live_pair):
    _, ws = live_pair
    request(ws, "left", "set", "--value", "1")
    session = get_session(ws)
    left = copy.deepcopy(session.state["left"])
    for _ in range(3):
        request(ws, "pair", "status")
        request(ws, "right", "status")
    assert session.state["left"] == left
    _, code = request(ws, "right", "bad")
    assert code == 2
    assert session.state["right"]["effects"] == 0
    assert session.state["right"]["clock"] == 4
    assert session.state["left"] == left
    assert session.state["clock"] == 8


def test_scoped_client_and_io_cannot_read_other_lane(live_pair):
    _, ws = live_pair
    response, code = request(ws / "left", "read", "--file", "input.txt")
    assert code == 0 and response["text"] == "1"
    response, code = request(ws, "right", "read", "--file", "input.txt")
    assert code == 0 and response["text"] == "2"
    for path in ("../right/input.txt", str(ws / "right/input.txt")):
        _, code = request(ws, "left", "read", "--file", path)
        assert code == 2
    records = get_session(ws).records
    assert records[0]["files"] == {"left/input.txt": "1"}
    assert records[1]["files"] == {"right/input.txt": "2"}


@pytest.mark.parametrize("relative", ("left/input.txt", "right/docs/taskctl.md", "left/tools/taskctl"))
def test_composite_verifier_pins_every_lane_input(live_pair, relative):
    task, ws = live_pair
    _finish(ws)
    assert task.verify(ws).passed
    with (ws / relative).open("a") as stream:
        stream.write("changed")
    assert not task.verify(ws).passed


def test_definition_keys_do_not_collide_when_variants_are_built():
    from harness_bench.general_hard.interactive_data import make_task

    first = make_task(477, variant=0)
    definition = workflow_definition(first)
    other = make_task(477, variant=1)
    assert first.runtime_protocol != other.runtime_protocol
    assert workflow_definition(first) is definition
    assert workflow_definition(other).source != definition.source


def test_twenty_pairs_cover_each_original_once_without_replacing_control():
    assert SUITES["composite-hard-calibration"] == COMPOSITE_CALIBRATION_TASKS == ALL_TASKS[511:531]
    assert len({task.prompt for task in COMPOSITE_CALIBRATION_TASKS}) == 20
    source_counts = Counter()
    for task in COMPOSITE_CALIBRATION_TASKS:
        source_counts.update(int(tag.removeprefix("source-")) for tag in task.tags if tag.startswith("source-"))
        assert task.requires_trusted_service and task.max_timeout_seconds == 900
        assert "160 шагов графа" in task.prompt and "320" in task.prompt
        assert ":wave21-composite-v1:" in task.runtime_protocol
        assert task.setup_files["left/tools/taskctl"] != task.setup_files["right/tools/taskctl"]
    assert source_counts == Counter({number: 1 for number in range(472, 512)})
    assert len(SUITES["general-hard"]) == 40


def test_selected_wave_contains_exactly_the_ten_approved_original_tasks():
    expected = (512, 518, 520, 522, 523, 524, 525, 526, 527, 530)
    assert expected == SELECTED_COMPOSITE_NUMBERS
    assert SUITES["composite-hard"] == COMPOSITE_HARD_TASKS
    assert tuple(int(task.id.split("_")[1]) for task in COMPOSITE_HARD_TASKS) == expected
    assert all(task is ALL_TASKS[int(task.id.split("_")[1]) - 1] for task in COMPOSITE_HARD_TASKS)
