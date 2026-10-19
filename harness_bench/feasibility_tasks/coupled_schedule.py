"""Finite-worker scheduling: precedence windows and packing share one plan.

Energy certificates are necessary conditions derived from the public DAG. They
are deliberately not a complete decision procedure for nonpreemptive scheduling.
"""

from __future__ import annotations

from copy import deepcopy
from random import Random

KINDS = frozenset({"coupled_schedule"})


def _integer(value):
    if type(value) is not int:
        raise ValueError("integer required")
    return value


def _order(spec):
    jobs = {j["id"]: j for j in spec["jobs"]}
    if len(jobs) != len(spec["jobs"]):
        raise ValueError("duplicate job ID")
    done, ordered = set(), []
    while len(done) < len(jobs):
        ready = [j for j in jobs.values() if j["id"] not in done and set(j["parents"]) <= done]
        if not ready:
            raise ValueError("specification must be a DAG")
        for j in ready:
            done.add(j["id"])
            ordered.append(j)
    return ordered


def precedence_windows(spec):
    """Necessary earliest-start/latest-finish bounds, ignoring worker scarcity."""
    order = _order(spec)
    earliest, latest = {}, {}
    jobs = {j["id"]: j for j in order}
    children = {k: [] for k in jobs}
    for j in order:
        earliest[j["id"]] = max(
            [j["release"]] + [earliest[p] + jobs[p]["duration"] for p in j["parents"]]
        )
        for p in j["parents"]:
            children[p].append(j["id"])
    for j in reversed(order):
        latest[j["id"]] = min(
            [spec["budget_seconds"]] + [latest[c] - jobs[c]["duration"] for c in children[j["id"]]]
        )
    return earliest, latest


def mandatory_work(spec, start, finish):
    """Conservative interval demand; valid even with arbitrary preemption."""
    earliest, latest = precedence_windows(spec)
    return sum(
        max(
            0,
            j["duration"] - max(0, start - earliest[j["id"]]) - max(0, latest[j["id"]] - finish),
        )
        for j in spec["jobs"]
    )


def check_certificate(spec, report):
    try:
        if report["status"] != spec["status"] or report["status"] == "solved":
            return False
        certificate = report["certificate"]
        if "interval" in certificate:
            if not isinstance(certificate["interval"], list) or len(certificate["interval"]) != 2:
                return False
            start, finish = map(_integer, certificate["interval"])
            if ("start" in certificate or "finish" in certificate) and (
                _integer(certificate["start"]),
                _integer(certificate["finish"]),
            ) != (start, finish):
                return False
        else:
            start, finish = _integer(certificate["start"]), _integer(certificate["finish"])
        return 0 <= start < finish <= spec["budget_seconds"] and mandatory_work(
            spec, start, finish
        ) > spec["workers"] * (finish - start)
    except (KeyError, ValueError, TypeError, IndexError):
        return False


def check_solution(spec, solution):
    try:
        jobs = {j["id"]: j for j in spec["jobs"]}
        schedule = solution["schedule"]
        if not isinstance(schedule, dict) or set(schedule) != set(jobs):
            return False
        by_worker = [[] for _ in range(spec["workers"])]
        times = {}
        for key, slot in schedule.items():
            start, worker = _integer(slot["start"]), _integer(slot["worker"])
            finish = start + jobs[key]["duration"]
            if not jobs[key]["release"] <= start < finish <= spec["budget_seconds"]:
                return False
            if not 0 <= worker < spec["workers"]:
                return False
            times[key] = (start, finish)
            by_worker[worker].append((start, finish))
        if any(times[p][1] > times[k][0] for k, j in jobs.items() for p in j["parents"]):
            return False
        for slots in by_worker:
            slots.sort()
            if any(a[1] > b[0] for a, b in zip(slots, slots[1:], strict=False)):
                return False
        return True
    except (KeyError, ValueError, TypeError, IndexError):
        return False


def _fixture():
    rng = Random(1904503)
    workers, width, prefix, suffix = 3, 997, 37, 43
    keys = [f"job-{v}" for v in rng.sample(range(100_000, 1_000_000), 26)]
    initial, terminal = keys[0], keys[-1]
    middle, planted = [], {initial: dict(start=0, worker=0)}
    for worker in range(workers):
        # Each planted track fills the same window exactly. Independent random
        # durations obscure the partition; no track labels enter the contract.
        while True:
            durations = [rng.randrange(80, 171) for _ in range(7)]
            last = width - sum(durations)
            if 80 <= last <= 170:
                durations.append(last)
                break
        cursor = prefix
        for offset, duration in enumerate(durations):
            key = keys[1 + worker * 8 + offset]
            planted[key] = dict(start=cursor, worker=worker)
            middle.append(
                dict(
                    id=key,
                    duration=duration,
                    release=rng.randrange(max(1, cursor - 180)),
                    parents=[initial],
                )
            )
            cursor += duration
    # Additional edges join jobs from different planted tracks. They constrain
    # one shared packing problem rather than independent per-track schedules.
    for job in middle:
        start = planted[job["id"]]["start"]
        candidates = [
            j["id"]
            for j in middle
            if planted[j["id"]]["worker"] != planted[job["id"]]["worker"]
            and planted[j["id"]]["start"] + j["duration"] <= start
        ]
        job["parents"] += rng.sample(candidates, min(len(candidates), rng.randrange(3)))
    jobs = (
        [dict(id=initial, duration=prefix, release=0, parents=[])]
        + middle
        + [dict(id=terminal, duration=suffix, release=0, parents=[j["id"] for j in middle])]
    )
    planted[terminal] = dict(start=prefix + width, worker=0)
    rng.shuffle(jobs)
    positive = dict(
        kind="coupled_schedule",
        status="solved",
        workers=workers,
        budget_seconds=prefix + width + suffix,
        jobs=jobs,
    )
    negative = deepcopy(positive)
    negative.update(status="budget_exceeded", budget_seconds=positive["budget_seconds"] - 1)
    certificate = dict(interval=[prefix, prefix + width - 1])
    assert check_solution(positive, dict(schedule=planted))
    assert check_certificate(negative, dict(status="budget_exceeded", certificate=certificate))
    return negative, positive, certificate, dict(schedule=planted)


def upgrade_pairs(pairs):
    """Replace the atomic build pair in the supplied copied registry."""
    negative, positive, certificate, solution = _fixture()
    pair = next(p for p in pairs if p["slug"] == "build_critical_path")
    name = "Deadline build packing on finite workers"
    request = "Заверши все сборки на workers исполнителях за budget_seconds формального сервиса."
    instructions = (
        "Время дискретное: start каждого job — неотрицательное целое число секунд. "
        "Job выполняется непрерывно ровно duration секунд на одном выбранном worker "
        "из 0..workers-1. На одном worker интервалы [start,start+duration) не пересекаются; "
        "смена worker и прерывание внутри job запрещены. Начало не раньше release и завершения "
        "ВСЕХ parents. Граф ацикличен, список jobs перемешан. Все jobs должны завершиться "
        "не позже budget_seconds. Иных ресурсов и задержек нет. Представь одно полное "
        "расписание, одновременно удовлетворяющее всем ограничениям; реальная сборка не нужна. "
        "Для отказа допустим энергетический сертификат целого интервала [a,b]: из DAG "
        "вычисли E(j)=max(release(j), max(E(p)+duration(p) для parents)), максимум пустого "
        "набора равен 0. Обратно вычисли L(j)=min(budget_seconds, min(L(c)-duration(c) для "
        "непосредственных children)); минимум пустого набора здесь budget_seconds. "
        "В любом допустимом расписании start(j)>=E(j), finish(j)<=L(j). Поэтому внутри "
        "[a,b] каждый job обязательно требует не менее "
        "max(0,duration(j)-max(0,a-E(j))-max(0,L(j)-b)) секунд работы. Если сумма этих "
        "нижних границ строго больше workers*(b-a), расписание невозможно. Сертификат "
        "содержит только интервал: проверяющий сам вычисляет E, L и сумму. Необнаружение "
        "перегрузки само по себе не доказывает выполнимость; положительный ответ требует расписания."
    )
    for side, spec, gold in (("negative", negative, certificate), ("positive", positive, solution)):
        pair[side].update(
            spec=spec,
            gold=gold,
            name=name,
            request=request,
            instructions=instructions,
            schema='{"interval": [integer a, integer b]} or {"start": integer a, "finish": integer b}, with 0<=a<b<=budget_seconds and strict energetic overload.',
            solution_schema='{"schedule": {job ID: {"start": nonnegative integer, "worker": integer in [0,workers)}, ...}}. Every job exactly once; finish is start+duration.',
        )
    pair["name"] = name
    return pairs
