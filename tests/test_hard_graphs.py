"""Independent solvers validate upgraded graph pairs and fresh certificates."""

from collections import deque
from copy import deepcopy
from itertools import combinations

import pytest

from harness_bench.feasibility_tasks.checks import check
from harness_bench.feasibility_tasks.contract_pairs import CONTRACT_PAIRS, check_solution
from harness_bench.feasibility_tasks.hard_graphs import SLUGS, upgrade_pairs

PAIRS = {p["slug"]: p for p in upgrade_pairs(deepcopy(CONTRACT_PAIRS)) if p["slug"] in SLUGS}


def certificate_ok(case, certificate):
    return check(case["spec"], {"status": case["spec"]["status"], "certificate": certificate})


@pytest.mark.parametrize("slug", sorted(SLUGS))
def test_author_witnesses_and_contract_symmetry(slug):
    p = PAIRS[slug]
    assert check_solution(p["positive"]["spec"], p["positive"]["gold"])
    assert not check_solution(p["negative"]["spec"], p["positive"]["gold"])
    assert certificate_ok(p["negative"], p["negative"]["gold"])
    # Compare certificate validity at the positive constraints, without relying on
    # the metadata status check to reject a mathematically false certificate.
    positive_as_negative = deepcopy(p["positive"])
    positive_as_negative["spec"]["status"] = "impossible"
    assert not certificate_ok(positive_as_negative, p["negative"]["gold"])
    for key in ("request", "instructions", "schema", "solution_schema", "name"):
        assert p["positive"][key] == p["negative"][key]


def test_pure_deterministic_upgrade_and_unchanged_other_pairs():
    original = deepcopy(CONTRACT_PAIRS)
    assert upgrade_pairs(deepcopy(original)) == upgrade_pairs(deepcopy(original))
    assert original == CONTRACT_PAIRS
    for old, new in zip(original, upgrade_pairs(deepcopy(original)), strict=True):
        if old["slug"] not in SLUGS:
            assert old == new


def _bellman_ford(spec):
    edges = spec["constraints"]
    nodes = {e[k] for e in edges for k in ("from", "to")}
    distances, previous = dict.fromkeys(nodes, 0), {}
    for _ in nodes:
        changed = None
        for e in edges:
            if distances[e["to"]] > distances[e["from"]] + e["max_delta"]:
                distances[e["to"]] = distances[e["from"]] + e["max_delta"]
                previous[e["to"]] = e
                changed = e["to"]
        if changed is None:
            return {"offsets": distances}, None
    for _ in nodes:
        changed = previous[changed]["from"]
    start, cycle = changed, []
    while True:
        e = previous[changed]
        cycle.append(e["id"])
        changed = e["from"]
        if changed == start:
            return None, {"edges": cycle[::-1]}


def test_clock_solver_extracts_hidden_cycle_and_new_solution():
    pair = PAIRS["clock_contract"]
    solution, cycle = _bellman_ford(pair["positive"]["spec"])
    assert cycle is None
    assert check_solution(pair["positive"]["spec"], solution)
    solution, cycle = _bellman_ford(pair["negative"]["spec"])
    assert solution is None
    assert len(cycle["edges"]) == 29
    assert certificate_ok(pair["negative"], cycle)


def _matching(spec):
    eligible = spec["eligible"]
    match = {}
    # Breadth-first alternating paths, independent of the author's recursive DFS.
    for root in eligible:
        previous, queue, end = {root: None}, deque([root]), None
        while queue and end is None:
            job = queue.popleft()
            for worker in eligible[job]:
                if worker not in match:
                    end = job, worker
                    break
                other = match[worker]
                if other not in previous:
                    previous[other] = job, worker
                    queue.append(other)
        if end is None:
            continue
        while end is not None:
            job, worker = end
            match[worker] = job
            end = previous[job]
    assignment = {j: w for w, j in match.items()}
    jobs = set(eligible) - set(assignment)
    queue = deque(jobs)
    while queue:
        for w in eligible[queue.popleft()]:
            if w in match and match[w] not in jobs:
                jobs.add(match[w])
                queue.append(match[w])
    return assignment, jobs


def test_hall_requires_alternating_component_not_duplicate_lists():
    pair = PAIRS["deployment_matching"]
    for case in pair.values():
        if not isinstance(case, dict) or "spec" not in case:
            continue
        lists = list(case["spec"]["eligible"].values())
        assert len({frozenset(v) for v in lists}) == len(lists)
    assignment, unmatched_component = _matching(pair["positive"]["spec"])
    assert not unmatched_component
    assert check_solution(pair["positive"]["spec"], {"assignment": assignment})
    assignment, unmatched_component = _matching(pair["negative"]["spec"])
    assert len(assignment) == 63
    assert len(unmatched_component) == 23
    assert certificate_ok(pair["negative"], {"jobs": sorted(unmatched_component)})


def _matrix_flow(spec):
    nodes = spec["nodes"]
    residual = {u: dict.fromkeys(nodes, 0) for u in nodes}
    for e in spec["edges"]:
        residual[e["from"]][e["to"]] += e["capacity"]
    source, sink = spec["source"], spec["sink"]
    value = 0
    while True:
        # DFS augmenting paths exercise a different order and residual layout.
        parent, stack = {source: None}, [source]
        while stack and sink not in parent:
            u = stack.pop()
            for v in nodes:
                if residual[u][v] > 0 and v not in parent:
                    parent[v] = u
                    stack.append(v)
        if sink not in parent:
            return value, set(parent)
        path, v = [], sink
        while v != source:
            path.append((parent[v], v))
            v = parent[v]
        amount = min(residual[u][v] for u, v in path)
        for u, v in path:
            residual[u][v] -= amount
            residual[v][u] += amount
        value += amount


def test_flow_optimum_and_residual_cut_are_independently_computed():
    p = PAIRS["replication_cut"]
    optimum, side = _matrix_flow(p["negative"]["spec"])
    assert p["negative"]["spec"]["required_flow"] == optimum + 1
    assert p["positive"]["spec"]["required_flow"] == optimum
    assert 10 < len(side) < len(p["negative"]["spec"]["nodes"]) - 10
    assert certificate_ok(p["negative"], {"source_side": sorted(side)})
    edges = {(e["from"], e["to"]) for e in p["positive"]["spec"]["edges"]}
    # Multiple paths, cycles, and non-layered links; no capacity sum at endpoints
    # is the decisive obstruction.
    assert len(edges) > 190
    spec = p["positive"]["spec"]
    assert sum(e["capacity"] for e in spec["edges"] if e["from"] == spec["source"]) > optimum
    assert sum(e["capacity"] for e in spec["edges"] if e["to"] == spec["sink"]) > optimum


def _eliminate(spec):
    variables = sorted({v for r in spec["rows"] for v in r["variables"]})
    basis = {}
    for row in spec["rows"]:
        active, rhs, proof = set(row["variables"]), row["rhs"], {row["id"]}
        while active:
            pivot = min(active)
            if pivot not in basis:
                basis[pivot] = active, rhs, proof
                break
            old, value, evidence = basis[pivot]
            active ^= old
            rhs ^= value
            proof ^= evidence
        if not active and rhs:
            return None, {"rows": sorted(proof)}
    flags = dict.fromkeys(variables, 0)
    for pivot in sorted(basis, reverse=True):
        active, rhs, _ = basis[pivot]
        flags[pivot] = rhs ^ (sum(flags[v] for v in active - {pivot}) % 2)
    return {"flags": flags}, None


def test_xor_dense_full_rank_and_long_dependency():
    p = PAIRS["parity_repair"]
    assert min(len(r["variables"]) for r in p["positive"]["spec"]["rows"]) >= 12
    solution, certificate = _eliminate(p["positive"]["spec"])
    assert certificate is None
    assert check_solution(p["positive"]["spec"], solution)
    solution, certificate = _eliminate(p["negative"]["spec"])
    assert solution is None
    assert len(certificate["rows"]) == 40
    assert certificate_ok(p["negative"], certificate)


def _find_clique(spec, size):
    neighbors = {v: set() for v in spec["vertices"]}
    for a, b in spec["conflicts"]:
        neighbors[a].add(b)
        neighbors[b].add(a)

    def visit(chosen, candidates):
        if len(chosen) == size:
            return chosen
        while len(chosen) + len(candidates) >= size:
            v = candidates.pop()
            result = visit(chosen + [v], candidates & neighbors[v])
            if result:
                return result
        return None

    return visit([], set(neighbors))


def test_negative_clique_is_discoverable_without_author_ids():
    p = PAIRS["maintenance_windows"]
    found = _find_clique(p["negative"]["spec"], 6)
    assert found and certificate_ok(p["negative"], {"vertices": found})
    assert _find_clique(p["positive"]["spec"], 6) is None
    # Validate the construction independently of the production checker too.
    colors = p["positive"]["gold"]["windows"]
    edges = {frozenset(e) for e in p["positive"]["spec"]["conflicts"]}
    assert all(colors[a] != colors[b] for a, b in (tuple(e) for e in edges))
    assert all(
        frozenset(e) in {frozenset(x) for x in p["negative"]["spec"]["conflicts"]}
        for e in combinations(found, 2)
    )
