"""Joint matching and conditional clock constraints for wave 19.

An assignment activates public difference constraints. The impossibility
certificate exhausts all choices of one job, with a checked negative cycle for
each choice; it does not assume that a separately feasible matching suffices.
"""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from random import Random

KINDS = frozenset({"assignment_clock"})


def _number(value):
    if type(value) is int:
        return Fraction(value)
    if isinstance(value, list) and len(value) == 2:
        numerator, denominator = value
        if type(numerator) is int and type(denominator) is int and denominator > 0:
            return Fraction(numerator, denominator)
    raise ValueError("expected integer or [integer, positive integer]")


def _active_constraints(spec, assignment):
    result = list(spec["base_constraints"])
    for activation in spec["activations"]:
        if assignment.get(activation["job"]) == activation["worker"]:
            result.extend(activation["constraints"])
    return result


def _negative_cycle(edges, ids):
    if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
        return False
    by_id = {edge["id"]: edge for edge in edges}
    chosen = [by_id[key] for key in ids]
    return (
        all(
            first["to"] == second["from"]
            for first, second in zip(chosen, chosen[1:] + chosen[:1], strict=True)
        )
        and sum(_number(edge["max_delta"]) for edge in chosen) < 0
    )


def check_certificate(spec, report):
    """Accept any covered job and any valid branch-local negative cycles."""
    try:
        if report["status"] != spec["status"] or report["status"] == "solved":
            return False
        certificate = report["certificate"]
        job = certificate["job"]
        eligible = spec["eligible"][job]
        branches = certificate["branches"]
        if not isinstance(branches, list) or len(branches) != len(eligible):
            return False
        covered = [branch["worker"] for branch in branches]
        if len(set(covered)) != len(covered) or set(covered) != set(eligible):
            return False
        return all(
            _negative_cycle(_active_constraints(spec, {job: branch["worker"]}), branch["cycle"])
            for branch in branches
        )
    except (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError, OverflowError):
        return False


def check_solution(spec, solution):
    """Check the full matching and every constraint it actually activates."""
    try:
        assignment, raw_offsets = solution["assignment"], solution["offsets"]
        if (
            not isinstance(assignment, dict)
            or set(assignment) != set(spec["eligible"])
            or len(set(assignment.values())) != len(assignment)
            or any(worker not in spec["eligible"][job] for job, worker in assignment.items())
            or not isinstance(raw_offsets, dict)
            or set(raw_offsets) != set(spec["nodes"])
        ):
            return False
        offsets = {node: _number(value) for node, value in raw_offsets.items()}
        return all(
            offsets[edge["to"]] - offsets[edge["from"]] <= _number(edge["max_delta"])
            for edge in _active_constraints(spec, assignment)
        )
    except (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError, OverflowError):
        return False


def _fixtures():
    rng = Random(19043327)
    job_ids = [f"job-{i:02}" for i in range(40)]
    worker_ids = [f"worker-{i:02}" for i in range(40)]
    node_ids = [f"clock-{i:02}" for i in range(50)]
    rng.shuffle(job_ids)
    rng.shuffle(worker_ids)
    rng.shuffle(node_ids)
    potential = {node: rng.randrange(-700, 701) for node in node_ids}
    shifts = [0, 3, 11, 17, 29]
    eligible = {
        job: rng.sample([worker_ids[(i + shift) % 40] for shift in shifts], len(shifts))
        for i, job in enumerate(job_ids)
    }
    # Shuffling list order and IDs makes all activation records look alike.
    constraint_ids = [f"constraint-{i:03}" for i in range(290)]
    rng.shuffle(constraint_ids)

    def edge(start, finish, slack):
        return dict(
            id=constraint_ids.pop(),
            **{"from": start, "to": finish},
            max_delta=potential[finish] - potential[start] + slack,
        )

    ring = [edge(node_ids[i], node_ids[(i + 1) % 50], 1) for i in range(50)]
    base = list(ring)
    for _ in range(40):
        start, finish = rng.sample(node_ids, 2)
        base.append(edge(start, finish, rng.randrange(70, 111)))
    bottleneck_index = 23
    bottleneck = job_ids[bottleneck_index]
    branch_cycles = {}
    activations = []
    fix_id = None
    winning_shift = 17
    winning_worker = worker_ids[(bottleneck_index + winning_shift) % 40]
    for job in job_ids:
        for j, worker in enumerate(eligible[job]):
            if job == bottleneck:
                start_index = (7 + 9 * j) % 50
                path_length = 18 + 2 * j
                end_index = (start_index + path_length) % 50
                # The ring path start->end plus this reverse edge totals -1.
                conditional = edge(node_ids[end_index], node_ids[start_index], -path_length - 1)
                branch_cycles[worker] = [
                    ring[(start_index + k) % 50]["id"] for k in range(path_length)
                ] + [conditional["id"]]
                if worker == winning_worker:
                    fix_id = conditional["id"]
            else:
                start, finish = rng.sample(node_ids, 2)
                conditional = edge(start, finish, rng.randrange(0, 19))
            activations.append(dict(job=job, worker=worker, constraints=[conditional]))
    rng.shuffle(base)
    rng.shuffle(activations)
    jobs_shuffled = rng.sample(job_ids, len(job_ids))
    negative = dict(
        kind="assignment_clock",
        nodes=rng.sample(node_ids, len(node_ids)),
        eligible={job: eligible[job] for job in jobs_shuffled},
        base_constraints=base,
        activations=activations,
    )
    positive = deepcopy(negative)
    assert fix_id is not None
    for activation in positive["activations"]:
        for conditional in activation["constraints"]:
            if conditional["id"] == fix_id:
                conditional["max_delta"] = (
                    potential[conditional["to"]] - potential[conditional["from"]] + 2
                )
    certificate = dict(
        job=bottleneck,
        branches=[
            dict(worker=worker, cycle=branch_cycles[worker]) for worker in eligible[bottleneck]
        ],
    )
    solution = dict(
        assignment={job: worker_ids[(i + winning_shift) % 40] for i, job in enumerate(job_ids)},
        offsets=potential,
    )
    return negative, positive, certificate, solution


def upgrade_pairs(pairs):
    """Replace deployment_matching in a caller-owned copied pair collection."""
    for pair in pairs:
        if pair["slug"] != "deployment_matching":
            continue
        negative, positive, certificate, solution = _fixtures()
        for side, spec, gold in (
            ("negative", negative, certificate),
            ("positive", positive, solution),
        ):
            case = pair[side]
            spec["status"] = case["spec"]["status"]
            case.update(
                spec=spec,
                gold=gold,
                name="Joint worker assignment and activated clock constraints",
                request="Назначь каждому заданию отдельного допустимого работника и одновременно "
                "выставь смещения часов, удовлетворяющие всем активированным ограничениям.",
                instructions="eligible задаёт допустимых работников каждого задания. Каждому "
                "заданию нужен ровно один работник, никакой работник не может выполнять два "
                "задания. nodes — все часы, их offsets могут быть любыми рациональными числами. "
                "Каждая запись ограничения означает offsets[to]-offsets[from] <= max_delta. "
                "Все base_constraints действуют всегда. Запись activations действует тогда "
                "и только тогда, когда указанному job назначен указанный worker; тогда все "
                "её constraints добавляются к базовым. Выбранные активации действуют "
                "одновременно, другие не действуют. Это полные условия задачи; порядок "
                "записей не имеет значения, ограничения на величину смещений отсутствуют. "
                "Проверить отдельно существование паросочетания и совместность базовых часов "
                "недостаточно: нужно совместное решение. Для сертификата невозможности "
                "выбери любое задание и покрой ВСЕ его допустимые назначения. Для каждой "
                "ветви предъяви направленный замкнутый цикл с отрицательной суммой max_delta; "
                "использовать можно только base_constraints и constraints активаций именно "
                "этого job с именно этим worker. Циклы перечисляются в порядке обхода, "
                "ID внутри одного цикла не повторяются. Поскольку каждое назначение этого "
                "задания противоречиво само по себе, совместное решение невозможно.",
                schema='{"job": job_id, "branches": [{"worker": worker_id, '
                '"cycle": [constraint_id, ...]}, ...]}; ровно одна ветвь на каждого '
                "допустимого работника выбранного задания; цикл непустой.",
                solution_schema='{"assignment": {job_id: worker_id, ...}, '
                '"offsets": {clock_id: integer or [integer, positive_integer], ...}}; '
                "заданы все задания и все часы, без лишних ID.",
            )
        pair["name"] = pair["negative"]["name"]
    return pairs
