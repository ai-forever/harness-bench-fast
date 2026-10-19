"""Aggregate identifiability through nonnegative geometry, not row span alone.

A feasible integer world plus coinciding linear lower and upper bounds proves
identifiability. The validators evaluate public observations and inequalities;
the private reference construction is never used to recognize an answer.
"""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from random import Random

KINDS = frozenset({"nonnegative_aggregate"})


def _number(value):
    if type(value) is int:
        return Fraction(value)
    if isinstance(value, list) and len(value) == 2:
        n, d = value
        if type(n) is int and type(d) is int and d > 0:
            return Fraction(n, d)
    raise ValueError("expected integer or [integer, positive integer]")


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def _world_valid(spec, world):
    return (
        isinstance(world, list)
        and len(world) == spec["variables"]
        and all(type(value) is int and value >= 0 for value in world)
        and all(_dot(row["coefficients"], world) == row["total"] for row in spec["observations"])
    )


def _weights(spec, raw):
    ids = {row["id"] for row in spec["observations"]}
    if not isinstance(raw, dict) or not set(raw) <= ids:
        raise ValueError("weights must be keyed by observation ID")
    return [_number(raw.get(row["id"], 0)) for row in spec["observations"]]


def check_solution(spec, solution):
    """Any feasible integer world and tight valid dual bounds are accepted."""
    try:
        if spec["kind"] not in KINDS or not isinstance(solution, dict):
            return False
        if not _world_valid(spec, solution["world"]):
            return False
        target = solution["target"]
        if type(target) is not int:
            return False
        lower = _weights(spec, solution["lower_weights"])
        upper = _weights(spec, solution["upper_weights"])
        observations = spec["observations"]
        totals = [row["total"] for row in observations]
        if _dot(lower, totals) != target or _dot(upper, totals) != target:
            return False
        for i, coefficient in enumerate(spec["target_coefficients"]):
            column = [row["coefficients"][i] for row in observations]
            if not _dot(lower, column) <= coefficient <= _dot(upper, column):
                return False
        return target == _dot(spec["target_coefficients"], solution["world"])
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError, OverflowError):
        return False


def check_certificate(spec, report):
    """Two admissible worlds with different targets refute identification."""
    try:
        if (
            spec["kind"] not in KINDS
            or not isinstance(report, dict)
            or report["status"] not in ("impossible", "budget_exceeded")
        ):
            return False
        worlds = report["certificate"]["worlds"]
        if not isinstance(worlds, list) or len(worlds) != 2:
            return False
        return all(_world_valid(spec, world) for world in worlds) and _dot(
            spec["target_coefficients"], worlds[0]
        ) != _dot(spec["target_coefficients"], worlds[1])
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError, OverflowError):
        return False


def _rank(rows):
    matrix = [[Fraction(value) for value in row] for row in rows]
    pivot_row = 0
    for column in range(len(matrix[0])):
        pivot = next((i for i in range(pivot_row, len(matrix)) if matrix[i][column]), None)
        if pivot is None:
            continue
        matrix[pivot_row], matrix[pivot] = matrix[pivot], matrix[pivot_row]
        scale = matrix[pivot_row][column]
        matrix[pivot_row] = [v / scale for v in matrix[pivot_row]]
        for i in range(pivot_row + 1, len(matrix)):
            scale = matrix[i][column]
            if scale:
                matrix[i] = [
                    a - scale * b for a, b in zip(matrix[i], matrix[pivot_row], strict=True)
                ]
        pivot_row += 1
        if pivot_row == len(matrix):
            break
    return pivot_row


def _fixture():
    rng = Random(19043649)
    active, forced, observations = 20, 16, 26
    kernel = [rng.choice([-1, 1]) for _ in range(active - 1)] + [1]
    matrix = []
    for _ in range(observations - 1):
        left = [rng.randrange(-5, 6) for _ in range(active - 1)]
        left.append(-_dot(left, kernel[:-1]))
        matrix.append(left + [rng.randrange(-8, 9) for _ in range(forced)])
    combination = [rng.choice([-3, -2, -1, 1, 2, 3]) for _ in range(observations - 1)]
    positive_slack = [rng.randrange(2, 8) for _ in range(forced)]
    last = [
        sum(w * row[i] for w, row in zip(combination, matrix, strict=True))
        for i in range(active + forced)
    ]
    last[active:] = [
        value - slack for value, slack in zip(last[active:], positive_slack, strict=True)
    ]
    matrix.append(last)
    annihilator = combination + [-1]
    world = [rng.randrange(15, 61) for _ in range(active)] + [0] * forced
    totals = [_dot(row, world) for row in matrix]
    weights = [rng.randrange(-4, 5) for _ in range(observations)]
    correction = [0] * active + [rng.choice([-9, -7, -4, -1, 1, 3, 6, 8]) for _ in range(forced)]
    target = [
        sum(w * row[i] for w, row in zip(weights, matrix, strict=True)) + correction[i]
        for i in range(active + forced)
    ]
    assert _rank([row[:active] for row in matrix]) == active - 1
    assert _rank(matrix) == observations
    assert _rank(matrix + [target]) == observations + 1
    assert _dot(annihilator, totals) == 0
    # Integer M is deliberately nonminimal; any valid tight bounds are legal.
    multiplier = max(abs(c) for c in correction)
    lower = [w - multiplier * y for w, y in zip(weights, annihilator, strict=True)]
    upper = [w + multiplier * y for w, y in zip(weights, annihilator, strict=True)]
    other = [a + v for a, v in zip(world[:active], kernel, strict=True)] + [0] * forced
    negative_target = list(target)
    negative_target[7] += 1
    columns = rng.sample(range(active + forced), active + forced)
    rows = rng.sample(range(observations), observations)
    ids = [f"aggregate-{i:02}" for i in rng.sample(range(observations), observations)]
    public_observations = [
        dict(id=ids[k], coefficients=[matrix[i][j] for j in columns], total=totals[i])
        for k, i in enumerate(rows)
    ]
    positive = dict(
        kind="nonnegative_aggregate",
        status="solved",
        variables=active + forced,
        observations=public_observations,
        target_coefficients=[target[j] for j in columns],
    )
    negative = deepcopy(positive)
    negative.update(status="impossible", target_coefficients=[negative_target[j] for j in columns])
    solution = dict(
        world=[world[j] for j in columns],
        target=_dot(target, world),
        lower_weights={ids[k]: lower[i] for k, i in enumerate(rows)},
        upper_weights={ids[k]: upper[i] for k, i in enumerate(rows)},
    )
    certificate = dict(worlds=[[world[j] for j in columns], [other[j] for j in columns]])
    return negative, positive, certificate, solution


def upgrade_pairs(pairs):
    """Replace aggregate_identifiability in a caller-owned copied collection."""
    pair = next(pair for pair in pairs if pair["slug"] == "aggregate_identifiability")
    negative, positive, certificate, solution = _fixture()
    name = "Identify an aggregate using nonnegative data constraints"
    request = "Восстанови точное значение target_coefficients·world, которое гарантированно одинаково во всех допустимых исходных данных."
    instructions = (
        "world — вектор из variables неотрицательных ЦЕЛЫХ чисел. Каждая строка observations "
        "задаёт точное равенство coefficients·world=total. Коэффициенты агрегатов и целевого "
        "функционала могут быть отрицательными. Это все сведения о данных: верхних границ "
        "координат и скрытых ограничений нет. Нужен однозначный target, а не произвольный "
        "мир, согласующийся с наблюдениями. Неотрицательность координат может дать информацию, "
        "которой нет в одном лишь линейном пространстве строк. Для решения предъяви допустимый "
        "world, целый target и два набора рациональных весов наблюдений lower_weights, "
        "upper_weights. Для каждой координаты i должно выполняться "
        "sum(lower_weights[r]*coefficients[r][i]) <= target_coefficients[i] <= "
        "sum(upper_weights[r]*coefficients[r][i]). Обе суммы весов на правых частях должны "
        "равняться target: sum(lower_weights[r]*total[r])=sum(upper_weights[r]*total[r])=target. "
        "Это нижняя и верхняя границы на всех неотрицательных мирах; их совпадение доказывает "
        "однозначность. Разрешены любые веса, удовлетворяющие этим условиям; они не обязаны "
        "быть нормированными. Пропущенные веса считаются нулевыми, неизвестные ID запрещены. "
        "Для невозможности однозначного восстановления достаточно любых двух допустимых "
        "неотрицательных целочисленных миров с разными значениями целевого функционала."
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
            schema='{"worlds": [[nonnegative integer,...], [nonnegative integer,...]]}; '
            "оба вектора удовлетворяют всем наблюдениям, целевые значения различаются.",
            solution_schema='{"world": [nonnegative integer,...], "target": integer, '
            '"lower_weights": {observation_id: integer or [integer,positive_integer], ...}, '
            '"upper_weights": {observation_id: integer or [integer,positive_integer], ...}}; '
            "оба набора весов должны доказывать указанные покоординатные границы с одинаковым target; "
            "пропущенные веса равны нулю, неизвестные observation_id запрещены.",
        )
    pair["name"] = name
    return pairs
