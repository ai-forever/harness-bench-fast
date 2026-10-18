"""Coupled capacity, conservation and cost contracts for wave 19.

The host constructs an optimal primal/dual pair. Public data contain neither
construction: solvers must find a feasible transshipment or a valid cost bound.
"""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from random import Random

KINDS = frozenset({"costed_transshipment"})


def _rat(value):
    if type(value) is int:
        return Fraction(value)
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(type(x) is int for x in value)
        and value[1] > 0
    ):
        return Fraction(*value)
    raise ValueError("use an integer or [integer, positive integer]")


def check_solution(spec, solution):
    """Check every public capacity, node balance and the single cost budget."""
    try:
        if spec["kind"] not in KINDS or not isinstance(solution, dict):
            return False
        raw = solution["edge_flows"]
        if not isinstance(raw, list) or len(raw) != len(spec["edges"]):
            return False
        balance = dict.fromkeys(spec["nodes"], Fraction(0))
        cost = Fraction(0)
        for edge, value in zip(spec["edges"], raw, strict=True):
            flow = _rat(value)
            if not edge["lower"] <= flow <= edge["upper"]:
                return False
            balance[edge["from"]] -= flow
            balance[edge["to"]] += flow
            cost += edge["cost"] * flow
        return balance == spec["balances"] and cost <= spec["cost_budget"]
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError, OverflowError):
        return False


def check_certificate(spec, report):
    """A dual bound proves impossibility, independently of the author's label.

    For b = incoming - outgoing and r_uv = c_uv - p_v + p_u,
    c*f = p*b + sum(r*f) >= p*b + sum(min(r*lower, r*upper)).
    No optimality, normalization or integrality requirement is imposed on p.
    """
    try:
        if (
            spec["kind"] not in KINDS
            or not isinstance(report, dict)
            or report["status"] not in ("impossible", "budget_exceeded")
        ):
            return False
        raw = report["certificate"]["potentials"]
        if not isinstance(raw, dict) or set(raw) != set(spec["nodes"]):
            return False
        p = {node: _rat(value) for node, value in raw.items()}
        bound = sum(p[node] * spec["balances"][node] for node in spec["nodes"])
        for edge in spec["edges"]:
            reduced = edge["cost"] - p[edge["to"]] + p[edge["from"]]
            bound += min(reduced * edge["lower"], reduced * edge["upper"])
        return bound > spec["cost_budget"]
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError, OverflowError):
        return False


def _fixture():
    rng = Random(1903434)
    nodes = [f"hub-{v:06x}" for v in rng.sample(range(0x100000, 0xFFFFFF), 45)]
    p = {node: rng.randrange(-90, 91) for node in nodes}
    arcs = set()
    for a, b in zip(nodes, nodes[1:] + nodes[:1], strict=True):
        arcs.update(((a, b), (b, a)))
    while len(arcs) < 150:
        arcs.add(tuple(rng.sample(nodes, 2)))
    edges, flows = [], []
    balance = dict.fromkeys(nodes, 0)
    # Complementary slackness gives a private optimum without public hints.
    # Nonnegative unit prices coexist with positive, negative and zero reduced
    # costs. Lower bounds are real obligations, not optional capacity hints.
    for a, b in sorted(arcs):
        delta = p[b] - p[a]
        cost = delta if delta > 0 and rng.random() < 0.42 else rng.randrange(1, 71)
        lower = rng.randrange(0, 5)
        upper = lower + rng.randrange(5, 20)
        reduced = cost - delta
        flow = lower if reduced > 0 else upper if reduced < 0 else rng.randrange(lower, upper + 1)
        edges.append(dict(**{"from": a, "to": b}, lower=lower, upper=upper, cost=cost))
        flows.append(flow)
        balance[a] -= flow
        balance[b] += flow
    order = rng.sample(range(len(edges)), len(edges))
    edges = [edges[i] for i in order]
    flows = [flows[i] for i in order]
    rng.shuffle(nodes)
    optimum = sum(e["cost"] * f for e, f in zip(edges, flows, strict=True))
    positive = dict(
        kind="costed_transshipment",
        status="solved",
        nodes=nodes,
        edges=edges,
        balances={node: balance[node] for node in nodes},
        cost_budget=optimum,
    )
    negative = deepcopy(positive)
    negative.update(status="impossible", cost_budget=optimum - 1)
    return negative, positive, {"potentials": p}, {"edge_flows": flows}


def upgrade_pairs(pairs):
    """Replace only replication_cut in the supplied mutable copy of pairs."""
    pair = next(p for p in pairs if p["slug"] == "replication_cut")
    negative, positive, certificate, solution = _fixture()
    name = "Перевозки с обязательными объёмами, балансами и бюджетом стоимости"
    request = (
        "Настрой единый поток перевозок, одновременно соблюдающий обязательные объёмы, "
        "ёмкости, балансы всех узлов и общий бюджет стоимости."
    )
    instructions = (
        "edges — направленные рёбра в фиксированном порядке. Поток может быть любым "
        "неотрицательным рациональным числом; на каждом ребре lower <= flow <= upper. "
        "Для каждого узла суммарный входящий поток минус исходящий должен равняться "
        "balances[node]; положительный баланс означает потребление, отрицательный — "
        "поставку. Стоимость равна сумме cost*flow по всем рёбрам и не должна превышать "
        "cost_budget. Все эти ограничения действуют одновременно; изменение сети, "
        "отказ от обязательных нижних объёмов и перенос за пределы бюджета запрещены. "
        "Циклы и встречные рёбра допустимы. Числа ответа — целые или дроби [числитель, "
        "положительный знаменатель]. Для отрицательного свидетельства можно предъявить "
        "любые потенциалы p для ВСЕХ узлов. Определив для ребра u->v значение "
        "r=cost-p[v]+p[u], вычисли нижнюю оценку L=sum(p[node]*balances[node]) + "
        "sum(min(r*lower,r*upper)). Если L>cost_budget, допустимого потока нет. "
        "Потенциалы не обязаны быть оптимальными или нормированными."
    )
    for side, spec, gold in (
        ("negative", negative, certificate),
        ("positive", positive, solution),
    ):
        pair[side].update(
            spec=spec,
            gold=gold,
            name=name,
            request=request,
            instructions=instructions,
            schema=(
                '{"potentials": {node ID: integer or [numerator,positive_denominator], ...}}; '
                "include every node; the stated dual lower bound must exceed cost_budget."
            ),
            solution_schema=(
                '{"edge_flows": [integer or [numerator,positive_denominator], ...]}; '
                "one flow per edge, in edges order, satisfying all bounds, balances and cost_budget."
            ),
        )
    pair["name"] = name
    return pairs
