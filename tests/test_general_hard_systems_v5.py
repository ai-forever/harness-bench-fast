"""Post-calibration systems: independent observations, crash and lazy oracles."""
from __future__ import annotations

import copy
import inspect
import itertools
import textwrap
from fractions import Fraction

import pytest

from harness_bench.general_hard import systems_v5 as s


@pytest.mark.parametrize("task", s.TASKS, ids=lambda t: t.id)
def test_v5_gold_blank_and_source(task, tmp_path):
    task.setup(tmp_path)
    assert not task.verify(tmp_path).passed
    task.apply_gold(tmp_path)
    assert task.verify(tmp_path).passed
    protected = next(k for k in task.setup_files if k != "solution.py")
    with (tmp_path / protected).open("a") as handle:
        handle.write("\n")
    assert not task.verify(tmp_path).passed


@pytest.mark.parametrize("number", sorted(s.ANCHORS))
def test_v5_hand_anchors(number):
    for value, expected in s.ANCHORS[number]:
        assert s.SOLVERS[number](value) == expected


def test_chance_constraint_does_not_allow_randomized_convexification():
    answer = s.solve487(s.SAFE487)
    assert answer["reward"] == [3, 1] and answer["policy"]["action"] == "safe"
    # Mixing safe/risky would improve reward while meeting risk, but is forbidden.
    assert Fraction(3, 4) * 3 + Fraction(1, 4) * 5 > 3
    assert Fraction(3, 4) + Fraction(1, 4) * Fraction(1, 2) >= Fraction(3, 4)


def test_delayed_information_independent_finite_world_policy_enumeration():
    data = copy.deepcopy(s.control_case(0))
    data["prior"] = [[1, 3], [2, 3]]
    data["transition"] = [[[1, 1], [0, 1]], [[0, 1], [1, 1]]]
    data["energy"] = 2
    data["threshold"] = 7
    data["required_probability"] = [2, 3]
    for action in data["actions"]:
        for mode, row in enumerate(action["outcomes"]):
            selected = 0 if action["id"] == "safe" or (mode == (action["id"] in ("b",))) else 1
            for i, outcome in enumerate(row):
                outcome["p"] = [int(i == selected), 1]
    actions = {a["id"]: a for a in data["actions"]}
    names = ["wait", *sorted(actions)]

    def outcome(name, mode):
        if name == "wait":
            return 0, "_"
        value = next(o for o in actions[name]["outcomes"][mode] if o["p"][0])
        return value["reward"], name + ":" + value["label"]

    candidates = []
    for first, second in itertools.product(names, repeat=2):
        selected = [name for name in (first, second) if name != "wait"]
        if len(set(selected)) != len(selected):
            continue
        spent = sum(actions[name]["energy"] for name in selected)
        if spent > data["energy"]:
            continue
        available = [name for name in names if name not in selected and
                     (name == "wait" or actions[name]["energy"] <= data["energy"] - spent)]
        observations = sorted({outcome(first, mode)[1] for mode in (0, 1)})
        for choices in itertools.product(available, repeat=len(observations)):
            policy = dict(zip(observations, choices, strict=True))
            rewards = [sum(outcome(name, mode)[0] for name in
                           (first, second, policy[outcome(first, mode)[1]])) for mode in (0, 1)]
            expected = Fraction(rewards[0], 3) + Fraction(2*rewards[1], 3)
            success = Fraction(int(rewards[0] >= 7), 3) + Fraction(2*int(rewards[1] >= 7), 3)
            if success >= Fraction(2, 3):
                candidates.append((expected, success))
    answer = s.solve487(data)
    assert (Fraction(*answer["reward"]), Fraction(*answer["success"])) == max(candidates)
    # Independently traverse the returned tree: third action sees FIRST signal.
    for mode in (0, 1):
        policy, pending = answer["policy"], "_"
        for _ in range(3):
            _, new_signal = outcome(policy["action"], mode)
            branches = {b["observation"]: b["next"] for b in policy["branches"]}
            policy = branches[pending]
            pending = new_signal
        assert policy == {"done": True}


def test_crash_after_commit_ack_loss_requires_belief_and_retry():
    data = {**s.TINY489, "faults": 1, "max_rounds": 2}
    answer = s.solve489(data)
    assert answer["cost"] == 6
    branches = {b["observation"]: b["next"] for b in answer["policy"]["branches"]}
    assert branches["ack"] == {"done": True}
    assert branches["lost"]["action"] == "write"
    assert s.solve489({**data, "max_rounds": 1}) == {"feasible": False}
    guarded = copy.deepcopy(data)
    guarded["actions"][0]["guards"] = {"x": "a"}
    guarded["actions"].append({"id": "probe", "kind": "probe", "paths": ["x"], "guards": {}, "cost": 1})
    guarded["max_rounds"] = 3
    answer = s.solve489(guarded)
    assert answer["cost"] == 7
    lost = next(b["next"] for b in answer["policy"]["branches"] if b["observation"] == "lost")
    assert lost["action"] == "probe"


def test_flush_exposes_invalid_durable_prefix_even_when_final_valid():
    data = s.crash_case(0)
    data["faults"] = 0
    data["max_rounds"] = 1
    data["disk"] = 20
    data["actions"] = [next(a for a in data["actions"] if a["id"] == "unsafe")]
    assert s.solve489(data) == {"feasible": False}


def test_call_by_need_lexical_capture_and_fuel_hand_counts():
    lazy = {**s.TINY494, "queries": [{"id": "q", "expr": s.call(
        s.closure(["x"], s.operation("add", s.var("x"), s.var("x"))),
        {"op": "mark", "label": "M", "value": s.lit(2)})}]}
    result = s.solve494(lazy)
    assert result["steps"] == 8 and result["trace"] == ["M"] and result["results"][0]["value"] == 4
    lexical = {**s.TINY494, "fuel": 30, "queries": [{"id": "q", "expr": {
        "op": "letrec", "bindings": {"x": s.lit(1), "f": s.closure(["y"], s.operation("add", s.var("x"), s.var("y")))},
        "body": {"op": "letrec", "bindings": {"x": s.lit(100)}, "body": s.call(s.var("f"), s.lit(2))}}}]}
    result = s.solve494(lexical)
    assert result["steps"] == 13 and result["results"][0]["value"] == 3
    assert s.solve494({**s.MEMO494, "fuel": 4}) == {"feasible": False}
    blackhole = {**s.TINY494, "definitions": {"bad": s.var("bad")}, "queries": [{"id": "q", "expr": s.var("bad")}]}
    assert s.solve494(blackhole) == {"feasible": False}
    ignored = {**blackhole, "queries": [{"id": "q", "expr": s.call(s.closure(["x"], s.lit(7)), s.var("bad"))}]}
    assert s.solve494(ignored)["results"][0]["value"] == 7
    wrong_arity = {**s.TINY494, "queries": [{"id": "q", "expr": s.operation("sub", s.lit(1))}]}
    assert s.solve494(wrong_arity) == {"feasible": False}


def test_replicas_require_three_independent_phases_and_true_cow():
    answer = s.solve499(s.TINY499)
    # Two bytes must be copied, verified only after delivery, then published.
    assert answer["rounds"] == 3 and answer["io"] == 2 and answer["peak"] == 4
    assert answer["plan"][1] == ["verify:s0", "verify:s1"]
    assert s.solve499({**s.TINY499, "capacity": {"d": 3}}) == {"feasible": False}
    # Publishing a lone identity would erase the only verified copy of its peer.
    single = copy.deepcopy(s.TINY499)
    single["groups"] = [{"id": "A", "objects": ["A"]}]
    assert s.solve499(single) == {"feasible": False}


MUTANTS = [
    (487, 'if row[1] >= Fraction(*data["required_probability"])', 'if True'),
    (487, 'groups.setdefault(pending, {})', 'groups.setdefault(signal, {})'),
    (489, 'for op in action["ops"] + [{"op": "flush"}]:', 'for op in action["ops"]:'),
    (489, 'if any(stable.get(p) != b for p, b in action["guards"].items()):', 'if False:'),
    (494, 'local = dict(function[3])', 'local = dict(scope)'),
    (494, 'if thunk[0] == 2:\n                return thunk[3]',
     'if thunk[0] == 2 and thunk[1] is None:\n                return thunk[3]'),
    (499, '{destination: (obj, old[1] + amount, False)}',
     '{destination: (obj, old[1] + amount, old[1] + amount == sizes[obj])}'),
]


@pytest.mark.parametrize("number,old,new", MUTANTS)
def test_semantic_mutants(number, old, new):
    source = textwrap.dedent(inspect.getsource(s.SOLVERS[number]))
    assert old in source
    namespace = {}
    exec(compile(source.replace(old, new), f"mutant{number}", "exec"), namespace)
    mutant = namespace[s.SOLVERS[number].__name__]
    rows = s.CASES.get(number, [(v, s.SOLVERS[number](v)) for v in s.ARTIFACT_DATA.get(number, [])])
    assert any(mutant(copy.deepcopy(value)) != expected for value, expected in rows)
