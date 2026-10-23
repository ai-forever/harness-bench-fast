"""Persistent donor faults require an observable adaptive recovery policy."""

from __future__ import annotations

import inspect
import random

from .common import program_task
from .data_v5 import PROMPT as OLD_PROMPT
from .data_v5 import piece, resource


def adaptive_solve(x):
    import json
    from fractions import Fraction
    from functools import lru_cache

    rr = sorted(x["resources"], key=lambda r: r["id"])
    ids = [r["id"] for r in rr]
    pp = sorted(x["pieces"], key=lambda p: p["id"])
    index = {key: i for i, key in enumerate(ids)}
    required = [tuple(index[k] for k in p["requires"]) for p in pp]
    writes = [tuple(index[k] for k in p["writes"]) for p in pp]
    worlds = x["scenarios"]
    all_worlds = tuple(range(len(worlds)))

    def canonical(p):
        return json.dumps(p, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def moves(position, running, remaining, cache, t):
        pinned = set(required[running]) if running >= 0 else set()
        droppable = [
            i for i, mode in enumerate(cache) if mode != 0 and mode <= 1 and i not in pinned
        ]
        for mask in range(1 << len(droppable)):
            dropped = [droppable[j] for j in range(len(droppable)) if mask >> j & 1]
            base = list(cache)
            for i in dropped:
                base[i] = 0
            starts = [-1]
            if running < 0:
                starts += [
                    j
                    for j, p in enumerate(pp)
                    if p["a"] == position
                    and t >= p["release"]
                    and t + p["ticks"] <= p["deadline"]
                    and all(base[i] > 0 for i in required[j])
                ]
            for start in starts:
                active = running if start < 0 else start
                ticks = remaining if start < 0 else pp[start]["ticks"]
                held = set(required[active]) if active >= 0 else set()
                occupied = sum(r["bytes"] for r, c in zip(rr, base, strict=True) if c)
                operations = [("", -1)]
                for i, r in enumerate(rr):
                    if i in held:
                        continue
                    if (
                        base[i] <= 0
                        and (base[i] < 0 or occupied + r["bytes"] <= x["cache_bytes"])
                        and any(a <= t < b for a, b in r["available"])
                    ):
                        operations.append(("fetch", i))
                    if base[i] >= 2:
                        operations.append(("writeback", i))
                for op, i in operations:
                    action = {
                        "start": pp[start]["id"] if start >= 0 else None,
                        "drop": [ids[j] for j in dropped],
                        "io": None if not op else {"op": op, "resource": ids[i]},
                    }
                    yield action, base, active, ticks, op, i

    for limit in range(x["horizon"] + 1):

        @lru_cache(None)
        def visit(t, position, running, remaining, cache, belief, limit=limit):
            if position == x["n"] and running < 0 and all(c <= 1 for c in cache):
                return Fraction(0), {"done": True}
            if t == limit:
                return None
            best = None
            total = sum(worlds[w]["weight"] for w in belief)
            for action, base, active, ticks, op, i in moves(position, running, remaining, cache, t):
                groups = {}
                for w in belief:
                    observation = (
                        ("fault" if ids[i] in worlds[w]["unavailable"] else "ok")
                        if op == "fetch"
                        else "none"
                    )
                    groups.setdefault(observation, []).append(w)
                branches = []
                cost = Fraction(
                    rr[i]["fetch_cost"] if op == "fetch" else rr[i]["writeback_cost"] if op else 0
                )
                for observation, group in sorted(groups.items()):
                    updated = base[:]
                    if op == "fetch" and observation == "ok":
                        progress = 1 if updated[i] == 0 else 1 - updated[i]
                        updated[i] = 1 if progress == rr[i]["fetch_ticks"] else -progress
                    elif op == "writeback":
                        progress = updated[i] - 1
                        updated[i] = 1 if progress == rr[i]["writeback_ticks"] else progress + 2
                    at = position
                    next_running = active
                    next_remaining = ticks
                    if active >= 0:
                        next_remaining -= 1
                        if next_remaining == 0:
                            at = pp[active]["b"]
                            next_running = -1
                            for j in writes[active]:
                                updated[j] = 2
                    child = visit(
                        t + 1, at, next_running, next_remaining, tuple(updated), tuple(group)
                    )
                    if child is None:
                        break
                    cost += Fraction(sum(worlds[w]["weight"] for w in group), total) * child[0]
                    branches.append({"observation": observation, "next": child[1]})
                else:
                    policy = {"action": action, "branches": branches}
                    candidate = (cost, canonical(policy), policy)
                    if best is None or candidate[:2] < best[:2]:
                        best = candidate
            return None if best is None else (best[0], best[2])

        answer = visit(0, 0, -1, 0, (0,) * len(rr), all_worlds)
        if answer is not None:
            cost, policy = answer
            return {
                "feasible": True,
                "worst_finish": limit,
                "expected_io": [cost.numerator, cost.denominator],
                "policy": policy,
            }
    return {"feasible": False}


PROMPT = (
    OLD_PROMPT.split("Оптимизируй:")[0].replace(
        "Вход {n,cache_bytes,horizon,resources,pieces}.",
        "Вход {n,cache_bytes,horizon,resources,pieces,scenarios}.",
    )
    + """
НОВЫЙ режим: перед t=0 один скрытый ПОСТОЯННЫЙ scenario выбирается с вероятностью
weight/SUM weights. scenarios=[{id,weight:positive integer,unavailable:[resource_id]}].
Контроллер НЕ видит выбранный id; знает список scenarios и свои прошлые наблюдения.
Только fetch меняется: попытка fetch ресурса из unavailable ВСЕГДА даёт observation
"fault", платит обычный fetch_cost за tick, но НЕ меняет cache/progress (в частности,
не резервирует bytes отсутствующего ресурса). Успешная попытка даёт "ok" и обычный
fetch progress. Проверки available, capacity, pinning выполняются ДО попытки одинаково
для обеих исходов: даже будущий fault должен пройти проверку staging capacity.
CPU в таком tick продолжает работать по обычным правилам; writeback никогда не fails.
Наблюдение доставляется после конца tick, включая CPU completion. Для io=null или
writeback наблюдение ровно "none". Других сигналов о scenario нет. Постоянство faults
может сообщать информацию о ДРУГИХ ресурсах через корреляции списка scenarios.
Cached bytes остаются пригодны; unavailable запрещает только успешные fetch ticks.

Нужна ДЕТЕРМИНИРОВАННАЯ adaptive policy, гарантирующая finish во ВСЕХ scenarios
к horizon. Одинаковая история наблюдений обязана иметь одинаковое действие.
Policy={done:true} немедленно в goal, иначе {action:{start,drop,io},branches:[
{observation,next}]}; branches содержит ровно positive-probability observations,
по observation lex. Действие должно быть допустимо во всех совместимых scenarios.
Оптимизируй worst_finish, затем expected суммарную оплаченную I/O cost по заданным
weights, затем canonical JSON ВСЕЙ policy: json.dumps(sort_keys=True,
separators=(",",":"),ensure_ascii=True). Это заменяет критерий фиксированного расписания;
число pieces отдельно не оптимизируется. Branches могут завершаться в разные ticks.
Выход ровно {feasible:false} либо {feasible:true,worst_finish:int,
expected_io:[numerator,denominator],policy}; дробь сокращённая, denominator>0.
Размеры:1<=n<=10^12,1..3 resources,1..5 pieces,1<=horizon<=7,1..3 scenarios;
1<=bytes<=3,1<=cache_bytes<=5,1<=fetch_ticks/writeback_ticks/piece.ticks<=3,
costs0..5,0<=release<=deadline<=7,available endpoints0..30,weights1..100.
Scenario ids уникальны; unavailable содержит уникальные известные resource ids.
Порядок входных массивов не влияет. Все остальные правила cache/CPU/durability выше
сохраняются. 900 секунд агента и 25 секунд полного проверочного потока не изменяются.
"""
)


def inputs():
    cases = []
    for seed in range(12):
        rng = random.Random(4796000 + seed)
        resources = [
            resource("a", fetch=1 + seed % 2, writeback=1 + seed % 2),
            resource("b", fetch=1 + seed % 2),
            resource("c"),
        ]
        pieces = [
            piece("A", 0, 1, ["a"], ["a"] if seed % 3 == 0 else [], deadline=7),
            piece("B", 0, 1, ["b"], deadline=7),
        ]
        if seed >= 4:
            pieces = [
                piece("A", 0, 1, ["a"], ["a"] if seed % 3 == 0 else [], ticks=2, deadline=7),
                piece("B", 0, 1, ["b"], deadline=7),
                piece("tail", 1, 2, ["c"], deadline=7),
            ]
        worlds = [
            {"id": "left", "weight": 1 + seed, "unavailable": ["b"]},
            {"id": "right", "weight": 1, "unavailable": ["a"]},
        ]
        if seed >= 6:
            worlds.append({"id": "both", "weight": 2, "unavailable": []})
        if seed == 11:
            worlds[2]["unavailable"] = ["a", "b"]
        cases.append(
            {
                "n": 2 if seed >= 4 else 1,
                "cache_bytes": 2,
                "horizon": 7,
                "resources": resources,
                "pieces": pieces,
                "scenarios": worlds,
            }
        )
        if seed in (7, 9):
            resources[2]["available"] = [[rng.randrange(2), 4], [5, 7]]
    return cases


GOLD = (
    inspect.getsource(adaptive_solve)
    + "\nimport json,sys\nfor line in sys.stdin:\n print(json.dumps(adaptive_solve(json.loads(line))))\n"
)
CASES = [(x, adaptive_solve(x)) for x in inputs()]


def harden_479_v6(tasks):
    old = next(t for t in tasks if t.id.startswith("task_479_"))
    new = program_task(
        number=479,
        slug=old.id.split("_", 2)[2],
        name="Recover adaptively under persistent hidden donor faults",
        parent=355,
        prompt=PROMPT,
        cases=CASES,
        gold=GOLD,
        tags=("hardening-v4", "hardening-v5", "hardening-v6"),
    )
    return [new if t.id == old.id else t for t in tasks]
