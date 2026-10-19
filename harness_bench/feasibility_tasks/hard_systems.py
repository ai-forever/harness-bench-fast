"""Wave 19 systems contracts: exact, independently checkable witnesses.

Generated fixtures use only standard-library deterministic integer arithmetic.
The validators never execute code or compare an answer with the gold witness.
"""

from __future__ import annotations

import random
from copy import deepcopy
from fractions import Fraction

KINDS = frozenset({"normalized_function", "preemptive_window", "lagged_build", "quorum_release"})


def _int(x):
    if type(x) is not int:
        raise ValueError("integer required")
    return x


def _num(x):
    if type(x) is int:
        return Fraction(x)
    if isinstance(x, list) and len(x) == 2:
        a, b = map(_int, x)
        if b > 0:
            return Fraction(a, b)
    raise ValueError("integer or rational required")


def _key(s, r):
    left, right = r["left"], r["right"]
    for table in s["round_tables"]:
        left, right = right, left ^ table[right]
    packed = left * 32 + right
    return tuple((packed & m).bit_count() % 2 for m in s["projection_masks"])


def _topological(jobs, parent_key):
    by_id = {j["id"]: j for j in jobs}
    done, order = set(), []
    while len(done) < len(jobs):
        ready = [j for j in jobs if j["id"] not in done and all(p in done for p in parent_key(j))]
        if not ready:
            raise ValueError("cyclic specification")
        for j in ready:
            done.add(j["id"])
            order.append(j)
    if len(by_id) != len(jobs):
        raise ValueError("duplicate job")
    return order


def _build_schedule(s):
    schedule, chains = {}, {}
    for j in _topological(s["jobs"], lambda j: [e["job"] for e in j["parents"]]):
        start, chain = j["release"], [j["id"]]
        for e in j["parents"]:
            candidate = schedule[e["job"]]["finish"] + e["lag"]
            if candidate > start:
                start, chain = candidate, chains[e["job"]] + [j["id"]]
        schedule[j["id"]] = {"start": start, "finish": start + j["duration"]}
        chains[j["id"]] = chain
    last = max(schedule, key=lambda k: schedule[k]["finish"])
    return schedule, chains[last]


def _quorum_times(s):
    result = {}
    for j in _topological(s["gates"], lambda j: j["parents"]):
        ready = sorted(result[p] for p in j["parents"])
        unlock = ready[j["threshold"] - 1] if j["threshold"] else 0
        result[j["id"]] = max(j["release"], unlock) + j["duration"]
    return result


def _edf(s):
    """Exact unit-slot EDF; fixture releases, deadlines and work are integers."""
    remaining = {j["id"]: j["work"] for j in s["jobs"]}
    segments = []
    for t in range(s["budget_seconds"]):
        eligible = [j for j in s["jobs"] if j["release"] <= t and remaining[j["id"]]]
        if not eligible:
            continue
        job = min(eligible, key=lambda j: (j["deadline"], j["id"]))
        if t >= job["deadline"]:
            raise ValueError("infeasible fixture")
        remaining[job["id"]] -= 1
        if segments and segments[-1]["job"] == job["id"] and segments[-1]["finish"] == t:
            segments[-1]["finish"] = t + 1
        else:
            segments.append(dict(job=job["id"], start=t, finish=t + 1))
    if any(remaining.values()):
        raise ValueError("unfinished fixture")
    return {"segments": segments}


def check_certificate(s, report):
    try:
        if report["status"] != s["status"] or report["status"] == "solved":
            return False
        c, kind = report["certificate"], s["kind"]
        if kind == "normalized_function":
            records = {r["id"]: r for r in s["records"]}
            a, b = c["records"]
            return (
                a != b
                and _key(s, records[a]) == _key(s, records[b])
                and (records[a]["required_output"] != records[b]["required_output"])
            )
        if kind == "preemptive_window":
            if "interval" in c:
                if not isinstance(c["interval"], list) or len(c["interval"]) != 2:
                    return False
                a, b = map(_num, c["interval"])
                if ("start" in c or "finish" in c) and (_num(c["start"]), _num(c["finish"])) != (
                    a,
                    b,
                ):
                    return False
            else:
                a, b = _num(c["start"]), _num(c["finish"])
            if not 0 <= a < b <= s["budget_seconds"]:
                return False
            # Every job whose complete eligibility window lies in [a,b] must fit there.
            demand = sum(j["work"] for j in s["jobs"] if j["release"] >= a and j["deadline"] <= b)
            return demand > b - a
        if kind == "lagged_build":
            jobs = {j["id"]: j for j in s["jobs"]}
            chain = c["jobs"]
            if not isinstance(chain, list) or not chain or len(set(chain)) != len(chain):
                return False
            lower = jobs[chain[0]]["release"] + jobs[chain[0]]["duration"]
            for a, b in zip(chain, chain[1:], strict=False):
                edge = next((e for e in jobs[b]["parents"] if e["job"] == a), None)
                if edge is None:
                    return False
                lower += edge["lag"] + jobs[b]["duration"]
            return lower > s["budget_seconds"]
        if kind == "quorum_release":
            lower = c["finish_lower_bounds"]
            if not isinstance(lower, dict) or set(lower) != {j["id"] for j in s["gates"]}:
                return False
            lower = {k: _num(v) for k, v in lower.items()}
            for j in _topological(s["gates"], lambda j: j["parents"]):
                ready = sorted(lower[p] for p in j["parents"])
                unlocked = ready[j["threshold"] - 1] if j["threshold"] else 0
                implied = max(j["release"], unlocked) + j["duration"]
                if not 0 <= lower[j["id"]] <= implied:
                    return False
            return lower[s["target"]] > s["budget_seconds"]
        return False
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError):
        return False


def check_solution(s, c):
    try:
        kind = s["kind"]
        if kind == "normalized_function":
            table = {}
            for row in c["table"]:
                k = tuple(_int(v) for v in row["input"])
                if len(k) != len(s["projection_masks"]) or any(v not in (0, 1) for v in k):
                    return False
                if k in table:
                    return False
                table[k] = _int(row["output"])
            keys = {_key(s, r) for r in s["records"]}
            return set(table) == keys and all(
                table[_key(s, r)] == r["required_output"] for r in s["records"]
            )
        if kind == "preemptive_window":
            jobs = {j["id"]: j for j in s["jobs"]}
            done = dict.fromkeys(jobs, Fraction(0))
            intervals = []
            for seg in c["segments"]:
                j = jobs[seg["job"]]
                start, finish = _num(seg["start"]), _num(seg["finish"])
                if not j["release"] <= start < finish <= min(j["deadline"], s["budget_seconds"]):
                    return False
                done[j["id"]] += finish - start
                intervals.append((start, finish))
            intervals.sort()
            return all(
                b <= c for (_, b), (c, _) in zip(intervals, intervals[1:], strict=False)
            ) and all(done[k] == j["work"] for k, j in jobs.items())
        if kind == "lagged_build":
            jobs, schedule = {j["id"]: j for j in s["jobs"]}, c["schedule"]
            if set(schedule) != set(jobs):
                return False
            times = {k: (_num(v["start"]), _num(v["finish"])) for k, v in schedule.items()}
            return all(
                j["release"] <= times[k][0]
                and times[k][1] - times[k][0] == j["duration"]
                and times[k][1] <= s["budget_seconds"]
                and all(times[e["job"]][1] + e["lag"] <= times[k][0] for e in j["parents"])
                for k, j in jobs.items()
            )
        if kind == "quorum_release":
            gates, schedule = {j["id"]: j for j in s["gates"]}, c["schedule"]
            # Nonessential gates may be omitted; all included dependencies are checked.
            if s["target"] not in schedule or not set(schedule) <= set(gates):
                return False
            times = {k: (_num(v["start"]), _num(v["finish"])) for k, v in schedule.items()}
            for k, (start, finish) in times.items():
                j = gates[k]
                if start < j["release"] or finish - start != j["duration"]:
                    return False
                if sum(p in times and times[p][1] <= start for p in j["parents"]) < j["threshold"]:
                    return False
            return times[s["target"]][1] <= s["budget_seconds"]
        return False
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError):
        return False


def _replace(
    pairs,
    slug,
    negative,
    positive,
    neg_gold,
    pos_gold,
    *,
    name,
    request,
    instructions,
    schema,
    solution_schema,
):
    pair = next(p for p in pairs if p["slug"] == slug)
    for side, spec, gold in (("negative", negative, neg_gold), ("positive", positive, pos_gold)):
        case = pair[side]
        case.update(
            spec=spec,
            gold=gold,
            name=name,
            request=request,
            instructions=instructions,
            schema=schema,
            solution_schema=solution_schema,
        )
    pair["name"] = name


def upgrade_pairs(pairs):
    """Mutate the supplied deep copy of CONTRACT_PAIRS; preserve task IDs/slugs."""
    rng = random.Random(19106)
    tables = []
    for _ in range(7):
        table = list(range(32))
        rng.shuffle(table)
        tables.append(table)
    masks = [557, 365, 718, 839, 149, 902, 233, 474]
    spec = dict(
        kind="normalized_function",
        status="solved",
        round_tables=tables,
        projection_masks=masks,
        records=[],
    )
    for i, raw in enumerate(rng.sample(range(1024), 180)):
        r = dict(id=f"sample-{rng.randrange(10**7, 10**8)}", left=raw // 32, right=raw % 32)
        r["required_output"] = sum((i + 3) * b for i, b in enumerate(_key(spec, r))) % 19
        spec["records"].append(r)
    table = {}
    collision = None
    for r in spec["records"]:
        k = _key(spec, r)
        if k in table:
            collision = [table[k]["id"], r["id"]]
        table[k] = r
    assert collision is not None
    negative = deepcopy(spec)
    negative["status"] = "impossible"
    next(r for r in negative["records"] if r["id"] == collision[1])["required_output"] += 1
    _replace(
        pairs,
        "stateless_contract",
        negative,
        spec,
        dict(records=collision),
        dict(table=[dict(input=list(k), output=r["required_output"]) for k, r in table.items()]),
        name="Stateless function after lossy normalization",
        request="Построй детерминированную функцию на доступных нормализованных входах.",
        instructions="Для каждой записи положи L=left, R=right. Последовательно для каждой таблицы T из round_tables одновременно обнови (L,R)=(R,L XOR T[R]); XOR — побитовое исключающее ИЛИ. Затем z=32*L+R. Вход функции — список битов ((z & m).bit_count() mod 2) по порядку projection_masks, где & — побитовое И. Никакие raw поля, ID, время или состояние функции недоступны. Для каждого перечисленного примера она должна вернуть required_output. Контракт ограничен этими нормализованными входами.",
        schema='{"records": [ID, ID]}: two distinct records with equal normalized inputs and different required_output.',
        solution_schema='{"table": [{"input": [0 or 1, ...], "output": integer}, ...]}: exactly one row for each distinct normalized input.',
    )

    # Start with a feasible contiguous schedule, then hide a tight interval inside
    # heterogeneous overlapping eligibility windows. No individual job overloads.
    work = [rng.randrange(3, 9) for _ in range(64)]
    horizon = sum(work)
    bottleneck_start, bottleneck_finish = sum(work[:20]), sum(work[:38])
    jobs, cursor = [], 0
    for i, amount in enumerate(work):
        release = max(0, cursor - rng.randrange(0, 81))
        deadline = cursor + amount + rng.randrange(0, 81)
        if 20 <= i < 38:
            release = max(release, bottleneck_start)
            deadline = min(deadline, bottleneck_finish)
        jobs.append(
            dict(
                id=f"transfer-{rng.randrange(10**7, 10**8)}",
                release=release,
                deadline=deadline,
                work=amount,
            )
        )
        cursor += amount
    overloaded_id = jobs[27]["id"]
    rng.shuffle(jobs)
    spec = dict(kind="preemptive_window", status="solved", budget_seconds=horizon + 80, jobs=jobs)
    negative = deepcopy(spec)
    negative["status"] = "budget_exceeded"
    next(j for j in negative["jobs"] if j["id"] == overloaded_id)["work"] += 1
    _replace(
        pairs,
        "archive_transfer",
        negative,
        spec,
        dict(start=bottleneck_start, finish=bottleneck_finish),
        _edf(spec),
        name="Preemptive archive delivery with local deadlines",
        request="Доставь все задания в пределах их release/deadline и общего бюджета сервиса.",
        instructions="Один общий канал выполняет ровно одну единицу work за секунду; параллельное обслуживание запрещено. Каждый job можно прерывать и возобновлять без затрат. Его обработка разрешена только в [release,deadline], время непрерывно. Суммарная длительность сегментов job должна равняться work. Ресурсы и данные не передаются локально: представь символическое расписание. Сертификат интервала суммирует work всех job, чьи полные окна лежат в выбранном интервале.",
        schema='{"start": number, "finish": number} or {"interval": [start, finish]}: interval demand strictly exceeds its length. number is integer or [numerator, positive denominator].',
        solution_schema='{"segments": [{"job": ID, "start": number, "finish": number}, ...]}: nonoverlapping segments of the shared channel; each job receives exactly its work.',
    )

    ids = [f"compile-{x}" for x in rng.sample(range(10**6, 10**7), 96)]
    jobs = []
    for i, key in enumerate(ids):
        parents = rng.sample(range(i), min(i, rng.randrange(2, 6)))
        jobs.append(
            dict(
                id=key,
                release=rng.randrange(0, 70),
                duration=rng.randrange(2, 13),
                parents=[dict(job=ids[p], lag=rng.randrange(0, 9)) for p in parents],
            )
        )
    rng.shuffle(jobs)
    spec = dict(kind="lagged_build", status="solved", jobs=jobs)
    schedule, chain = _build_schedule(spec)
    makespan = max(v["finish"] for v in schedule.values())
    spec["budget_seconds"] = makespan
    negative = deepcopy(spec)
    negative.update(status="budget_exceeded", budget_seconds=makespan - 1)
    _replace(
        pairs,
        "build_critical_path",
        negative,
        spec,
        dict(jobs=chain),
        dict(schedule=schedule),
        name="Build DAG with releases and communication lags",
        request="Заверши все сборки не позже бюджета формального сервиса.",
        instructions="Число исполнителей не ограничено. Для job начало не раньше release, длительность ровно duration. Для каждого parents entry начало не раньше finish указанного job плюс lag. Запускать реальные сборки не нужно. Порядок списка jobs не является топологическим. Сертификат — любая цепь зависимостей: release первого job + длительности всех job цепи + lag её рёбер дают нижнюю границу завершения последнего job.",
        schema='{"jobs": [ID, ...]}: a dependency chain whose release+durations+lags exceeds budget_seconds.',
        solution_schema='{"schedule": {ID: {"start": number, "finish": number}, ...}}: every job exactly once; number is integer or [numerator, positive denominator].',
    )

    ids = [f"key-{x}" for x in rng.sample(range(10**6, 10**7), 90)]
    gates = []
    for i, key in enumerate(ids):
        parents = rng.sample(range(i), min(i, rng.randrange(3, 10))) if i >= 8 else []
        gates.append(
            dict(
                id=key,
                release=rng.randrange(0, 28),
                duration=rng.randrange(1, 9),
                parents=[ids[p] for p in parents],
                threshold=rng.randrange(1, len(parents) + 1) if parents else 0,
            )
        )
    rng.shuffle(gates)
    spec = dict(kind="quorum_release", status="solved", gates=gates, target=ids[-1])
    times = _quorum_times(spec)
    spec["budget_seconds"] = times[spec["target"]]
    negative = deepcopy(spec)
    negative.update(status="budget_exceeded", budget_seconds=spec["budget_seconds"] - 1)
    schedule = {
        j["id"]: dict(start=times[j["id"]] - j["duration"], finish=times[j["id"]]) for j in gates
    }
    _replace(
        pairs,
        "sealed_partition",
        negative,
        spec,
        dict(finish_lower_bounds=times),
        dict(schedule=schedule),
        name="Threshold key reconstruction network",
        request="Получи target до истечения бюджета формального сервиса.",
        instructions="Граф parents ацикличен, список gates перемешан. Gate можно запустить не раньше release и завершения любых threshold различных parents. Gate выполняется ровно duration секунд, число исполнителей не ограничено, готовые ключи сохраняются. threshold=0 не требует родителей. Дедлайн относится только к target; ненужные gates можно не запускать. Для сертификата предъяви неотрицательные нижние границы F каждого gate. Проверяется F(g) <= max(release(g), k-я наименьшая F родителей)+duration(g), где k=threshold; при k=0 вместо k-й границы берётся 0. Ацикличность делает эти локальные неравенства доказательством нижних границ; F(target) должна строго превышать budget_seconds.",
        schema='{"finish_lower_bounds": {gate ID: number, ...}}: all gates, valid local lower bounds, target lower bound strictly greater than budget_seconds.',
        solution_schema='{"schedule": {gate ID: {"start": number, "finish": number}, ...}}: include target and sufficient prerequisite gates. number is integer or [numerator, positive denominator].',
    )
    return pairs
