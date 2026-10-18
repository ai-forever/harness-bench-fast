"""Independent geometry, rank and bounded-world checks for aggregate controls."""

from copy import deepcopy
from fractions import Fraction
from itertools import product
from random import Random

import pytest

from harness_bench.feasibility_tasks.nonnegative_aggregate import (
    check_certificate,
    check_solution,
    upgrade_pairs,
)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def number(value):
    return Fraction(value) if type(value) is int else Fraction(*value)


def modular_rank(rows, prime=1_000_000_007):
    """Rank from modular row elimination, independent of fixture Fraction rank."""
    a = [[x % prime for x in row] for row in rows]
    rank = 0
    for j in range(len(a[0])):
        pivot = next((i for i in range(rank, len(a)) if a[i][j]), None)
        if pivot is None:
            continue
        a[rank], a[pivot] = a[pivot], a[rank]
        inverse = pow(a[rank][j], -1, prime)
        a[rank] = [x * inverse % prime for x in a[rank]]
        for i in range(rank + 1, len(a)):
            multiplier = a[i][j]
            a[i] = [(x - multiplier * y) % prime for x, y in zip(a[i], a[rank], strict=True)]
        rank += 1
        if rank == len(a):
            break
    return rank


def negative(spec, worlds, status="impossible"):
    return check_certificate(spec, dict(status=status, certificate=dict(worlds=worlds)))


def forced_case():
    # x+y=0 and z=3: nonnegativity forces x=y=0. The target x-y+2z
    # is not in the row span of [[1,1,0],[0,0,1]], yet is uniquely six.
    return dict(
        kind="nonnegative_aggregate",
        variables=3,
        status="solved",
        observations=[
            dict(id="zero", coefficients=[1, 1, 0], total=0),
            dict(id="z", coefficients=[0, 0, 1], total=3),
        ],
        target_coefficients=[1, -1, 2],
    )


def forced_solution():
    return dict(
        world=[0, 0, 3],
        target=6,
        lower_weights={"zero": -1, "z": 2},
        upper_weights={"zero": 1, "z": 2},
    )


def test_identification_uses_nonnegativity_and_accepts_nonreference_bounds():
    s = forced_case()
    assert modular_rank([r["coefficients"] for r in s["observations"]]) == 2
    assert (
        modular_rank([r["coefficients"] for r in s["observations"]] + [s["target_coefficients"]])
        == 3
    )
    answer = forced_solution()
    assert check_solution(s, answer)
    other = deepcopy(answer)
    other["lower_weights"]["zero"] = [-15, 2]
    other["upper_weights"]["zero"] = [23, 3]
    assert check_solution(s, other)
    # Signed worlds can vary x-y while satisfying all observations, and must fail.
    assert not negative(s, [[0, 0, 3], [1, -1, 3]])
    assert not negative(s, [[0, 0, 3], [0, 0, 3]])
    assert not check_solution(s, dict(answer, world=[1, -1, 3]))
    assert not check_solution(s, dict(answer, target=7))


def test_direction_and_tightness_of_bounds_are_required():
    s = forced_case()
    answer = forced_solution()
    wrong = deepcopy(answer)
    wrong["lower_weights"], wrong["upper_weights"] = wrong["upper_weights"], wrong["lower_weights"]
    assert not check_solution(s, wrong)
    wrong = deepcopy(answer)
    wrong["upper_weights"]["z"] = 3
    assert not check_solution(s, wrong)
    wrong = deepcopy(answer)
    wrong["lower_weights"]["unknown"] = 0
    assert not check_solution(s, wrong)
    # Sparse dictionaries are allowed, rather than requiring a fixed output shape.
    s["target_coefficients"] = [1, -1, 0]
    assert check_solution(
        s, dict(world=[0, 0, 3], target=0, lower_weights={"zero": -1}, upper_weights={"zero": 1})
    )


def test_nonidentifiable_case_and_both_negative_labels():
    s = dict(
        kind="nonnegative_aggregate",
        variables=3,
        status="impossible",
        observations=[dict(id="sum", coefficients=[1, 1, 1], total=4)],
        target_coefficients=[2, 0, 0],
    )
    worlds = [[4, 0, 0], [0, 1, 3]]
    assert negative(s, worlds)
    assert negative(s, worlds, "budget_exceeded")
    assert not negative(s, worlds, "solved")
    assert not negative(s, worlds + [worlds[0]])
    assert not negative(s, [[4, 0, 0], [0, 1, 2]])
    assert not negative(s, [[4, 0, 0], [True, 0, 3]])
    assert not check_solution(
        s, dict(world=worlds[0], target=8, lower_weights={"sum": 2}, upper_weights={"sum": 2})
    )


def test_tiny_random_bounded_worlds_dual_soundness():
    rng = Random(76843)
    accepted = 0
    for trial in range(100):
        matrix = [[rng.randrange(-2, 3) for _ in range(3)] for _ in range(2)]
        anchor = [rng.randrange(3) for _ in range(3)]
        totals = [dot(row, anchor) for row in matrix]
        c = [rng.randrange(-3, 4) for _ in range(3)]
        if trial % 2 == 0:
            w = [rng.randrange(-2, 3) for _ in range(2)]
            c = [sum(w[j] * matrix[j][i] for j in range(2)) for i in range(3)]
        s = dict(
            kind="nonnegative_aggregate",
            variables=3,
            observations=[
                dict(id=str(j), coefficients=row, total=totals[j]) for j, row in enumerate(matrix)
            ],
            target_coefficients=c,
        )
        worlds = [
            list(x)
            for x in product(range(5), repeat=3)
            if all(dot(row, x) == total for row, total in zip(matrix, totals, strict=True))
        ]
        for low, high in product(product(range(-2, 3), repeat=2), repeat=2):
            answer = dict(
                world=anchor,
                target=dot(c, anchor),
                lower_weights={str(i): v for i, v in enumerate(low)},
                upper_weights={str(i): v for i, v in enumerate(high)},
            )
            if check_solution(s, answer):
                accepted += 1
                assert all(dot(c, x) == answer["target"] for x in worlds)
        if len(worlds) > 1:
            a, b = rng.sample(worlds, 2)
            assert negative(s, [a, b]) == (dot(c, a) != dot(c, b))
    assert accepted > 100


def test_fixture_rank_geometry_minimal_pair_and_gold_cross_rejection():
    from harness_bench.feasibility_tasks.contract_pairs import CONTRACT_PAIRS

    original = deepcopy(CONTRACT_PAIRS)
    upgraded = upgrade_pairs(deepcopy(CONTRACT_PAIRS))
    changed = [p for old, p in zip(original, upgraded, strict=True) if old != p]
    assert len(changed) == 1 and changed[0]["slug"] == "aggregate_identifiability"
    pair = changed[0]
    n, p = pair["negative"], pair["positive"]
    assert check_solution(p["spec"], p["gold"])
    assert not check_solution(n["spec"], p["gold"])
    for label in ("impossible", "budget_exceeded"):
        assert negative(n["spec"], n["gold"]["worlds"], label)
        assert not negative(p["spec"], n["gold"]["worlds"], label)
    matrix = [r["coefficients"] for r in p["spec"]["observations"]]
    c = p["spec"]["target_coefficients"]
    assert len(matrix) == 26 and len(matrix[0]) == 36
    assert modular_rank(matrix) == 26
    assert modular_rank(matrix + [c]) == 27
    active = [i for i, v in enumerate(p["gold"]["world"]) if v > 0]
    zero = [i for i, v in enumerate(p["gold"]["world"]) if v == 0]
    assert len(active) == 20 and len(zero) == 16
    assert modular_rank([[row[i] for i in active] for row in matrix]) == 19
    delta = [b - a for a, b in zip(*n["gold"]["worlds"], strict=True)]
    assert all(delta[i] in (-1, 1) for i in active)
    assert all(delta[i] == 0 for i in zero)
    assert all(dot(row, delta) == 0 for row in matrix)
    rows = p["spec"]["observations"]
    lower = [number(p["gold"]["lower_weights"][r["id"]]) for r in rows]
    upper = [number(p["gold"]["upper_weights"][r["id"]]) for r in rows]
    annihilator = [b - a for a, b in zip(lower, upper, strict=True)]
    assert dot(annihilator, [r["total"] for r in rows]) == 0
    assert all(dot(annihilator, [row[i] for row in matrix]) == 0 for i in active)
    assert all(dot(annihilator, [row[i] for row in matrix]) > 0 for i in zero)
    assert sum(a != b for a, b in zip(c, n["spec"]["target_coefficients"], strict=True)) == 1
    normalized = deepcopy(n["spec"])
    normalized.update(status="solved", target_coefficients=c)
    assert normalized == p["spec"]
    assert set(p["spec"]) == {"kind", "status", "variables", "observations", "target_coefficients"}
    assert original == CONTRACT_PAIRS


@pytest.mark.parametrize("bad", [None, True, False, 1, 0.5, "bad", [], {}, [1, 0], [True, 2]])
def test_malformed_inputs_and_numbers_fail_closed(bad):
    s = forced_case()
    assert not check_solution(s, bad)
    assert not check_certificate(s, bad)
    assert not check_certificate(s, dict(status="impossible", certificate=bad))
    solution = forced_solution()
    solution["lower_weights"]["z"] = bad
    assert not check_solution(s, solution)
