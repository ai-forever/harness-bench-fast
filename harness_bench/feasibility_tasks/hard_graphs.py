"""Host-only graph fixtures for wave 19, with algorithmically hidden obstructions.

A pair differs in one mathematical constraint, never in its instructions.  All
identifiers and row orders are deterministic but carry no ordering information.
Only the generated specification is published; author constructions stay here.
"""

from __future__ import annotations

import random
from collections import deque
from copy import deepcopy

KINDS = {"difference_cycle", "hall", "flow_cut", "xor", "clique"}
SLUGS = {
    "clock_contract",
    "deployment_matching",
    "replication_cut",
    "parity_repair",
    "maintenance_windows",
}


def _ids(rng, prefix, count):
    return [f"{prefix}-{x:06x}" for x in rng.sample(range(0x100000, 0xFFFFFF), count)]


def _install(pair, negative_spec, positive_spec, certificate, solution):
    for side, data, gold, status in (
        ("negative", negative_spec, certificate, "impossible"),
        ("positive", positive_spec, solution, "solved"),
    ):
        pair[side]["spec"] = dict(kind=pair[side]["spec"]["kind"], status=status, **data)
        pair[side]["gold"] = gold


def _clock(pair):
    rng = random.Random(190432)
    nodes = _ids(rng, "svc", 72)
    potential = {v: rng.randrange(-200, 201) for v in nodes}
    cycle = rng.sample(nodes, 29)
    used, edges = set(), []

    def edge(a, b, slack):
        used.add((a, b))
        edges.append({"from": a, "to": b, "max_delta": potential[b] - potential[a] + slack})

    for a, b in zip(cycle, cycle[1:] + cycle[:1], strict=True):
        edge(a, b, 0)
    while len(edges) < 580:
        a, b = rng.sample(nodes, 2)
        if (a, b) not in used:
            edge(a, b, rng.randrange(3, 61))
    for e, key in zip(edges, _ids(rng, "bound", len(edges)), strict=True):
        e["id"] = key
    certificate = {"edges": [e["id"] for e in edges[: len(cycle)]]}
    changed = edges[len(cycle) - 1]["id"]
    rng.shuffle(edges)
    positive = {"constraints": edges}
    negative = deepcopy(positive)
    next(e for e in negative["constraints"] if e["id"] == changed)["max_delta"] -= 1
    _install(pair, negative, positive, certificate, {"offsets": potential})


def _matching_solution(eligible):
    owner = {}

    def augment(job, seen):
        for worker in eligible[job]:
            if worker in seen:
                continue
            seen.add(worker)
            if worker not in owner or augment(owner[worker], seen):
                owner[worker] = job
                return True
        return False

    if not all(augment(job, set()) for job in eligible):
        raise ValueError("author matching construction failed")
    return {"assignment": {job: worker for worker, job in owner.items()}}


def _hall(pair):
    rng = random.Random(190433)
    jobs, workers = _ids(rng, "job", 64), _ids(rng, "worker", 64)
    core_jobs, core_workers = jobs[:23], workers[:22]
    eligible = {}
    for i, job in enumerate(core_jobs):
        allowed = set(rng.sample(core_workers, rng.randrange(8, 15)))
        allowed.add(core_workers[i % 22])
        eligible[job] = sorted(allowed)
    for i, job in enumerate(jobs[23:], 23):
        allowed = set(rng.sample(workers, rng.randrange(8, 15)))
        allowed.add(workers[i])
        eligible[job] = sorted(allowed)
    order = list(eligible)
    rng.shuffle(order)
    eligible = {j: rng.sample(eligible[j], len(eligible[j])) for j in order}
    positive = {"eligible": deepcopy(eligible)}
    positive["eligible"][core_jobs[-1]].append(workers[22])
    _install(
        pair,
        {"eligible": eligible},
        positive,
        {"jobs": core_jobs},
        _matching_solution(positive["eligible"]),
    )


def _max_flow(spec):
    """Edge-indexed residual BFS; handles anti-parallel physical links."""
    nodes, edges = spec["nodes"], spec["edges"]
    source, sink = spec["source"], spec["sink"]
    residual, adjacent = [], {v: [] for v in nodes}
    for e in edges:
        i = len(residual)
        residual.extend([[e["from"], e["to"], e["capacity"]], [e["to"], e["from"], 0]])
        adjacent[e["from"]].append(i)
        adjacent[e["to"]].append(i + 1)
    value = 0
    while True:
        previous, queue = {source: None}, deque([source])
        while queue and sink not in previous:
            for i in adjacent[queue.popleft()]:
                _, v, capacity = residual[i]
                if capacity > 0 and v not in previous:
                    previous[v] = i
                    queue.append(v)
        if sink not in previous:
            break
        path, v = [], sink
        while v != source:
            i = previous[v]
            path.append(i)
            v = residual[i][0]
        amount = min(residual[i][2] for i in path)
        value += amount
        for i in path:
            residual[i][2] -= amount
            residual[i ^ 1][2] += amount
    return (
        value,
        {"edge_flows": [e["capacity"] - residual[2 * i][2] for i, e in enumerate(edges)]},
        {"source_side": list(previous)},
    )


def _flow(pair):
    rng = random.Random(190434)
    nodes = _ids(rng, "relay", 50)
    left, right = nodes[:25], nodes[25:]
    source, sink = left[0], right[-1]
    links = {}
    # Internal cycles and chords hide the bottleneck behind many alternative paths.
    for group in (left, right):
        for i, a in enumerate(group):
            for step in (1, 4, 9):
                links[a, group[(i + step) % len(group)]] = rng.randrange(80, 131)
    for a, b in zip(rng.sample(left[1:], 14), rng.sample(right[:-1], 14), strict=True):
        links[a, b] = rng.randrange(3, 10)
    for _ in range(50):
        a, b = rng.choice(right[:-1]), rng.choice(left[1:])
        links[a, b] = rng.randrange(15, 51)
    edges = [{"from": a, "to": b, "capacity": capacity} for (a, b), capacity in links.items()]
    rng.shuffle(nodes)
    rng.shuffle(edges)
    positive = dict(nodes=nodes, source=source, sink=sink, edges=edges)
    optimum, solution, certificate = _max_flow(positive)
    positive["required_flow"] = optimum
    negative = deepcopy(positive)
    negative["required_flow"] += 1
    _install(pair, negative, positive, certificate, solution)


def _xor(pair):
    rng = random.Random(190435)
    count = 56
    variables = _ids(rng, "flag", count)
    # Elementary row operations keep a full-rank basis but erase chain structure.
    basis = [1 << i for i in range(count)]
    for _ in range(12 * count):
        a, b = rng.sample(range(count), 2)
        basis[a] ^= basis[b]
    flags = {v: rng.randrange(2) for v in variables}
    rows = []
    for mask in basis:
        selected = [v for i, v in enumerate(variables) if mask >> i & 1]
        rows.append(dict(variables=selected, rhs=sum(flags[v] for v in selected) % 2))
    selected_indices = rng.sample(range(count), 39)
    dependent = 0
    for i in selected_indices:
        dependent ^= basis[i]
    chosen = [v for i, v in enumerate(variables) if dependent >> i & 1]
    rows.append(dict(variables=chosen, rhs=sum(flags[v] for v in chosen) % 2))
    for row, key in zip(rows, _ids(rng, "check", len(rows)), strict=True):
        row["id"] = key
        rng.shuffle(row["variables"])
    bad_id = rows[-1]["id"]
    certificate = {"rows": [rows[i]["id"] for i in selected_indices] + [bad_id]}
    rng.shuffle(rows)
    positive = {"rows": rows}
    negative = deepcopy(positive)
    next(row for row in negative["rows"] if row["id"] == bad_id)["rhs"] ^= 1
    _install(pair, negative, positive, certificate, {"flags": flags})


def _color(pair):
    rng = random.Random(190441)
    vertices = _ids(rng, "service", 65)
    colors = {v: i % 5 for i, v in enumerate(vertices)}
    clique = vertices[:6]  # Endpoints 0 and 5 share a color in the positive case.
    conflicts = set()
    for i, a in enumerate(vertices):
        for b in vertices[i + 1 :]:
            if colors[a] != colors[b] and rng.random() < 0.43:
                conflicts.add(tuple(sorted((a, b))))
    for i, a in enumerate(clique):
        for b in clique[i + 1 :]:
            if colors[a] != colors[b]:
                conflicts.add(tuple(sorted((a, b))))
    edges = [list(e) for e in sorted(conflicts)]
    rng.shuffle(vertices)
    rng.shuffle(edges)
    positive = dict(vertices=vertices, conflicts=edges, colors=5)
    negative = deepcopy(positive)
    negative["conflicts"].insert(rng.randrange(len(edges)), [clique[0], clique[5]])
    _install(pair, negative, positive, {"vertices": clique}, {"windows": colors})


def upgrade_pairs(pairs):
    """Upgrade the supplied copy in place; preserve pair order and public schemas."""
    upgrades = dict(
        clock_contract=_clock,
        deployment_matching=_hall,
        replication_cut=_flow,
        parity_repair=_xor,
        maintenance_windows=_color,
    )
    found = set()
    for pair in pairs:
        if pair["slug"] in upgrades:
            upgrades[pair["slug"]](pair)
            found.add(pair["slug"])
    if found != set(upgrades):
        raise ValueError(f"missing graph pairs: {set(upgrades) - found}")
    return pairs
