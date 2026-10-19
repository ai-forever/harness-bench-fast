"""Independent optimization and exhaustive checks for costed transshipment."""

from copy import deepcopy
from itertools import product
from random import Random

import pytest

from harness_bench.feasibility_tasks.contract_pairs import CONTRACT_PAIRS
from harness_bench.feasibility_tasks.coupled_flow import (
    check_certificate,
    check_solution,
    upgrade_pairs,
)

PAIR = next(p for p in upgrade_pairs(deepcopy(CONTRACT_PAIRS)) if p["slug"] == "replication_cut")


def _report(potentials, status="impossible"):
    return dict(status=status, certificate=dict(potentials=potentials))


def _balances(spec, flows):
    result = dict.fromkeys(spec["nodes"], 0)
    for e, f in zip(spec["edges"], flows, strict=True):
        result[e["from"]] -= f
        result[e["to"]] += f
    return result


def _cost(spec, flows):
    return sum(e["cost"] * f for e, f in zip(spec["edges"], flows, strict=True))


def _residual_potential(spec, flows):
    """Find dual potentials from an optimum, without the author's potentials."""
    distances = dict.fromkeys(spec["nodes"], 0)
    arcs = []
    for e, f in zip(spec["edges"], flows, strict=True):
        if f < e["upper"]:
            arcs.append((e["from"], e["to"], e["cost"]))
        if f > e["lower"]:
            arcs.append((e["to"], e["from"], -e["cost"]))
    for _ in spec["nodes"]:
        changed = False
        for a, b, cost in arcs:
            if distances[b] > distances[a] + cost:
                distances[b] = distances[a] + cost
                changed = True
        if not changed:
            return distances
    raise AssertionError("negative cost residual cycle: candidate is not optimal")


def _dual_value(spec, p):
    # Independent equivalent derivation: choose each capacity endpoint by sign.
    bound = sum(p[v] * b for v, b in spec["balances"].items())
    for e in spec["edges"]:
        reduced = e["cost"] + p[e["from"]] - p[e["to"]]
        bound += reduced * (e["upper"] if reduced < 0 else e["lower"])
    return bound


def _solve_by_shortest_augmentations(spec):
    """Independent min-cost-flow solver, starting only from public lower bounds.

    Unit costs in these fixtures are nonnegative, so zero residual flow has no
    negative cycles. Successive Bellman-Ford shortest augmentations find the
    optimum of the lower-bound-reduced super-source/sink flow problem.
    """
    source, sink = "super-source", "super-sink"
    nodes = spec["nodes"] + [source, sink]
    residual = []

    def add(a, b, capacity, cost):
        residual.extend([[a, b, capacity, cost], [b, a, 0, -cost]])

    for e in spec["edges"]:
        add(e["from"], e["to"], e["upper"] - e["lower"], e["cost"])
    lower_balance = _balances(spec, [e["lower"] for e in spec["edges"]])
    required = 0
    for v in spec["nodes"]:
        demand = spec["balances"][v] - lower_balance[v]
        if demand > 0:
            add(v, sink, demand, 0)
            required += demand
        elif demand < 0:
            add(source, v, -demand, 0)
    value = 0
    while value < required:
        distance, previous = {source: 0}, {}
        for _ in nodes[:-1]:
            changed = False
            for i, (a, b, capacity, cost) in enumerate(residual):
                if (
                    capacity > 0
                    and a in distance
                    and (b not in distance or distance[b] > distance[a] + cost)
                ):
                    distance[b] = distance[a] + cost
                    previous[b] = i
                    changed = True
            if not changed:
                break
        assert sink in distance, "unexpected infeasible balances"
        path, v = [], sink
        while v != source:
            i = previous[v]
            path.append(i)
            v = residual[i][0]
        amount = min(residual[i][2] for i in path)
        for i in path:
            residual[i][2] -= amount
            residual[i ^ 1][2] += amount
        value += amount
    return [e["upper"] - residual[2 * i][2] for i, e in enumerate(spec["edges"])]


def test_full_fixture_independent_optimizer_and_dual_equality():
    pos, neg = PAIR["positive"], PAIR["negative"]
    s = pos["spec"]
    flows = _solve_by_shortest_augmentations(s)
    potentials = _residual_potential(s, flows)
    assert _balances(s, flows) == s["balances"]
    assert _cost(s, flows) == _dual_value(s, potentials) == s["cost_budget"]
    assert check_solution(s, {"edge_flows": flows})
    assert not check_solution(neg["spec"], {"edge_flows": flows})
    assert check_certificate(neg["spec"], _report(potentials))
    assert not check_certificate(s, _report(potentials))
    assert len(s["nodes"]) == 45 and len(s["edges"]) == 150
    reduced = [e["cost"] + potentials[e["from"]] - potentials[e["to"]] for e in s["edges"]]
    assert min(reduced) < 0 < max(reduced) and 0 in reduced
    assert sum(e["lower"] > 0 for e in s["edges"]) > 50
    assert sum(e["lower"] < f < e["upper"] for e, f in zip(s["edges"], flows, strict=True)) > 5
    assert all(e["cost"] >= 0 for e in s["edges"])


def test_author_controls_minimal_pair_and_metadata():
    positive, negative = PAIR["positive"], PAIR["negative"]
    assert check_solution(positive["spec"], positive["gold"])
    assert not check_solution(negative["spec"], positive["gold"])
    for label in ("impossible", "budget_exceeded"):
        report = dict(status=label, certificate=negative["gold"])
        assert check_certificate(negative["spec"], report)
        assert not check_certificate(positive["spec"], report)
    spec = deepcopy(positive["spec"])
    spec.update(status="impossible", cost_budget=spec["cost_budget"] - 1)
    assert spec == negative["spec"]
    for key in ("request", "instructions", "schema", "solution_schema", "name"):
        assert positive[key] == negative[key]
    assert set(positive["spec"]) == {"kind", "status", "nodes", "edges", "balances", "cost_budget"}
    original = deepcopy(CONTRACT_PAIRS)
    upgraded = upgrade_pairs(deepcopy(original))
    assert original == CONTRACT_PAIRS
    assert upgraded == upgrade_pairs(deepcopy(original))
    for before, after in zip(original, upgraded, strict=True):
        if before["slug"] != "replication_cut":
            assert before == after


def test_small_exhaustive_flow_optima_and_dual_certificates():
    rng = Random(1903444)
    for _ in range(100):
        nodes = ["a", "b", "c"]
        possible = [(a, b) for a in nodes for b in nodes if a != b]
        edges = []
        for a, b in rng.sample(possible, rng.randrange(3, 7)):
            lower = rng.randrange(2)
            edges.append(
                dict(
                    **{"from": a, "to": b},
                    lower=lower,
                    upper=lower + rng.randrange(1, 3),
                    cost=rng.randrange(8),
                )
            )
        spec = dict(kind="costed_transshipment", status="solved", nodes=nodes, edges=edges)
        anchor = [rng.randrange(e["lower"], e["upper"] + 1) for e in edges]
        spec["balances"] = _balances(spec, anchor)
        candidates = [
            f
            for f in product(*(range(e["lower"], e["upper"] + 1) for e in edges))
            if _balances(spec, f) == spec["balances"]
        ]
        best = min(candidates, key=lambda f: _cost(spec, f))
        optimum = _cost(spec, best)
        p = _residual_potential(spec, best)
        assert _dual_value(spec, p) == optimum
        found = _solve_by_shortest_augmentations(spec)
        assert _cost(spec, found) == optimum
        for budget in (optimum - 1, optimum, optimum + 1):
            spec["cost_budget"] = budget
            assert check_solution(spec, {"edge_flows": list(best)}) == (budget >= optimum)
            assert check_certificate(spec, _report(p)) == (budget < optimum)
        # Arbitrary valid potentials must never overestimate the primal optimum.
        for _ in range(5):
            arbitrary = {v: rng.randrange(-10, 11) for v in nodes}
            assert _dual_value(spec, arbitrary) <= optimum
            spec["cost_budget"] = optimum
            assert not check_certificate(spec, _report(arbitrary))


def test_alternative_rational_potentials_and_flows_accepted():
    pos, neg = PAIR["positive"], PAIR["negative"]
    # Uniform shifts preserve a potential bound because total balance is zero.
    shifted = {k: [v * 3 + 1, 3] for k, v in neg["gold"]["potentials"].items()}
    assert check_certificate(neg["spec"], _report(shifted))
    encoded = [[f * 2, 2] for f in pos["gold"]["edge_flows"]]
    assert check_solution(pos["spec"], {"edge_flows": encoded})
    # Fractional feasible flows are allowed, including genuinely nonintegral ones.
    spec = dict(
        kind="costed_transshipment",
        nodes=["a", "b"],
        balances={"a": 0, "b": 0},
        edges=[
            dict(**{"from": "a", "to": "b"}, lower=0, upper=1, cost=1),
            dict(**{"from": "b", "to": "a"}, lower=0, upper=1, cost=1),
        ],
        cost_budget=1,
    )
    assert check_solution(spec, {"edge_flows": [[1, 2], [1, 2]]})
    assert not check_solution(spec, {"edge_flows": [[2, 3], [2, 3]]})


@pytest.mark.parametrize("bad", [None, True, 0, "x", [], {}, [1], [1, 0], [1, -1], [True, 2], 1.5])
def test_malformed_numeric_payloads_fail_closed(bad):
    pos, neg = PAIR["positive"], PAIR["negative"]
    solution = deepcopy(pos["gold"])
    solution["edge_flows"][0] = bad
    assert not check_solution(pos["spec"], solution)
    certificate = deepcopy(neg["gold"])
    certificate["potentials"][neg["spec"]["nodes"][0]] = bad
    assert not check_certificate(neg["spec"], dict(status="impossible", certificate=certificate))


@pytest.mark.parametrize("bad", [None, [], True, "x", {}, {"edge_flows": []}, {"edge_flows": {}}])
def test_malformed_solution_objects_fail_closed(bad):
    assert not check_solution(PAIR["positive"]["spec"], bad)


def test_missing_extra_nodes_wrong_labels_and_capacity_balance_errors():
    pos, neg = PAIR["positive"], PAIR["negative"]
    p = deepcopy(neg["gold"]["potentials"])
    p["unknown"] = 0
    assert not check_certificate(neg["spec"], _report(p))
    del p["unknown"]
    del p[next(iter(p))]
    assert not check_certificate(neg["spec"], _report(p))
    assert not check_certificate(neg["spec"], _report(neg["gold"]["potentials"], "solved"))
    flows = deepcopy(pos["gold"]["edge_flows"])
    flows[0] = pos["spec"]["edges"][0]["upper"] + 1
    assert not check_solution(pos["spec"], dict(edge_flows=flows))
    flows = deepcopy(pos["gold"]["edge_flows"])
    e = pos["spec"]["edges"][0]
    flows[0] += 1 if flows[0] < e["upper"] else -1
    assert not check_solution(pos["spec"], dict(edge_flows=flows))
    wrong = deepcopy(pos["spec"])
    wrong["balances"][wrong["nodes"][0]] += 1
    assert not check_solution(wrong, pos["gold"])
