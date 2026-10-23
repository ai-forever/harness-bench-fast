"""Independent observable-history enumeration and deterministic-world replay."""

import copy
import itertools
import json
from fractions import Fraction

import pytest
from test_general_hard_data_v5 import Invalid, action, simulate

from harness_bench.general_hard import data_v6 as v


def step(x, world, history, a):
    # Validate even a faulting attempt as if it succeeded: capacity, window and
    # pinning preconditions are checked before the hidden outcome.
    simulate(x, history + [a], require_goal=False)
    io = a["io"]
    observation = "none"
    effective = copy.deepcopy(a)
    paid = 0
    if io:
        r = next(r for r in x["resources"] if r["id"] == io["resource"])
        paid = r["fetch_cost"] if io["op"] == "fetch" else r["writeback_cost"]
        if io["op"] == "fetch":
            observation = "fault" if io["resource"] in world["unavailable"] else "ok"
            if observation == "fault":
                effective["io"] = None
    updated = history + [effective]
    _, goal = simulate(x, updated, require_goal=False)
    return observation, updated, paid, goal


@pytest.mark.parametrize("case", range(12))
def test_all_feasible_policies_replay_in_each_world(case):
    x, result = v.CASES[case]
    if not result["feasible"]:
        return
    observed = {}
    declared = {}
    cost = Fraction(0)
    finishes = []
    for world in x["scenarios"]:
        node = result["policy"]
        history = []
        spent = 0
        while node != {"done": True}:
            observation, history, paid, goal = step(x, world, history, node["action"])
            spent += paid
            observed.setdefault(id(node), set()).add(observation)
            declared[id(node)] = {b["observation"] for b in node["branches"]}
            assert [b["observation"] for b in node["branches"]] == sorted(declared[id(node)])
            node = next(b["next"] for b in node["branches"] if b["observation"] == observation)
            if goal:
                assert node == {"done": True}
        assert simulate(x, history)[1]
        finishes.append(len(history))
        cost += world["weight"] * spent
    cost /= sum(w["weight"] for w in x["scenarios"])
    assert observed == declared
    assert max(finishes) == result["worst_finish"]
    assert [cost.numerator, cost.denominator] == result["expected_io"]


def tiny():
    x = copy.deepcopy(v.CASES[0][0])
    x["resources"] = x["resources"][:2]
    x["pieces"][0]["writes"] = []
    x["horizon"] = 3
    return x


def brute(x):
    names = [r["id"] for r in x["resources"]]
    drops = [list(c) for n in range(len(names) + 1) for c in itertools.combinations(names, n)]
    alphabet = [
        action(start, drop, op, key)
        for start, drop, (op, key) in itertools.product(
            [None] + [p["id"] for p in x["pieces"]],
            drops,
            [(None, None)] + [(op, key) for op in ("fetch", "writeback") for key in names],
        )
    ]

    def code(p):
        return json.dumps(p, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    for limit in range(x["horizon"] + 1):

        def search(belief, histories, limit=limit):
            goals = [simulate(x, h, require_goal=False)[1] for h in histories]
            if all(goals):
                return Fraction(0), {"done": True}
            if len(histories[0]) == limit:
                return None
            best = None
            for a in alphabet:
                groups = {}
                paid = None
                try:
                    for w, h in zip(belief, histories, strict=True):
                        observation, updated, paid, _ = step(x, x["scenarios"][w], h, a)
                        groups.setdefault(observation, []).append((w, updated))
                except Invalid:
                    continue
                total = sum(x["scenarios"][w]["weight"] for w in belief)
                cost = Fraction(paid)
                branches = []
                for observation, rows in sorted(groups.items()):
                    child = search([w for w, h in rows], [h for w, h in rows])
                    if child is None:
                        break
                    cost += (
                        Fraction(sum(x["scenarios"][w]["weight"] for w, h in rows), total)
                        * child[0]
                    )
                    branches.append({"observation": observation, "next": child[1]})
                else:
                    policy = {"action": a, "branches": branches}
                    candidate = (cost, code(policy), policy)
                    if best is None or candidate[:2] < best[:2]:
                        best = candidate
            return None if best is None else (best[0], best[2])

        answer = search(list(range(len(x["scenarios"]))), [[] for _ in x["scenarios"]])
        if answer:
            c, p = answer
            return {
                "feasible": True,
                "worst_finish": limit,
                "expected_io": [c.numerator, c.denominator],
                "policy": p,
            }
    return {"feasible": False}


def test_independent_full_policy_enumeration_and_clairvoyance_mutant():
    x = tiny()
    expected = brute(x)
    assert v.adaptive_solve(x) == expected
    assert expected["worst_finish"] == 3 and expected["expected_io"] == [3, 2]
    # A forbidden clairvoyant controller chooses the correct donor at t=0.
    clairvoyant = [v.adaptive_solve({**x, "scenarios": [world]}) for world in x["scenarios"]]
    assert max(y["worst_finish"] for y in clairvoyant) == 2


def test_weights_change_initial_action_and_no_static_plan_can_cover_both():
    x = tiny()
    x["scenarios"][0]["weight"] = 9
    left = v.adaptive_solve(x)
    assert left["policy"]["action"]["io"]["resource"] == "a"
    x["scenarios"][0]["weight"] = 1
    x["scenarios"][1]["weight"] = 9
    right = v.adaptive_solve(x)
    assert right["policy"]["action"]["io"]["resource"] == "b"
    assert right["expected_io"] == [11, 10]
    assert len(right["policy"]["branches"]) == 2
    assert v.CASES[-1][1] == {"feasible": False}
