"""Asynchronous cache/durability hardening of task479 only."""

from __future__ import annotations

import copy
import inspect
import random

from harness_bench.general_hard.common import program_task


def async_repair_solve(x):
    resources = sorted(x["resources"], key=lambda r: r["id"])
    ids = [r["id"] for r in resources]
    index = {key: i for i, key in enumerate(ids)}
    pieces = sorted(x["pieces"], key=lambda p: p["id"])
    required = [tuple(index[key] for key in p["requires"]) for p in pieces]
    writes = [tuple(index[key] for key in p["writes"]) for p in pieces]
    # Cache:0 absent,1 clean,2+j dirty with j completed writeback ticks,
    # -j fetching with j completed ticks and its complete slot already reserved.
    states = {(0, -1, 0, (0,) * len(resources)): (0, (), ())}

    def priority(value):
        return value[0], len(value[1]), value[1], value[2]

    for time in range(x["horizon"] + 1):
        goals = [
            value
            for (position, running, remaining, cache), value in states.items()
            if position == x["n"] and running < 0 and all(mode <= 1 for mode in cache)
        ]
        if goals:
            cost, path, trace = min(goals, key=priority)
            return {
                "finish": time,
                "io_cost": cost,
                "plan": list(path),
                "schedule": [
                    {
                        "start": start or None,
                        "drop": list(drop),
                        "io": None if not op else {"op": op, "resource": resource},
                    }
                    for start, drop, op, resource in trace
                ],
            }
        if time == x["horizon"]:
            break
        following = {}
        for (position, running, remaining, cache), value in states.items():
            pinned = set(required[running]) if running >= 0 else set()
            droppable = [
                i for i, mode in enumerate(cache) if mode != 0 and mode <= 1 and i not in pinned
            ]
            for mask in range(1 << len(droppable)):
                dropped = tuple(droppable[j] for j in range(len(droppable)) if mask >> j & 1)
                after_drop = list(cache)
                for i in dropped:
                    after_drop[i] = 0
                starts = [-1]
                if running < 0:
                    starts += [
                        j
                        for j, piece in enumerate(pieces)
                        if piece["a"] == position
                        and time >= piece["release"]
                        and time + piece["ticks"] <= piece["deadline"]
                        and all(after_drop[i] > 0 for i in required[j])
                    ]
                for start in starts:
                    active = running if start < 0 else start
                    ticks = remaining if start < 0 else pieces[start]["ticks"]
                    held = set(required[active]) if active >= 0 else set()
                    occupied = sum(
                        r["bytes"] for r, mode in zip(resources, after_drop, strict=True) if mode
                    )
                    operations = [("", -1)]
                    for i, resource in enumerate(resources):
                        mode = after_drop[i]
                        if i in held:
                            continue
                        if (
                            mode <= 0
                            and (mode < 0 or occupied + resource["bytes"] <= x["cache_bytes"])
                            and any(a <= time < b for a, b in resource["available"])
                        ):
                            operations.append(("fetch", i))
                        if mode >= 2:
                            operations.append(("writeback", i))
                    for op, i in operations:
                        updated = after_drop[:]
                        cost = value[0]
                        if op == "fetch":
                            progress = 1 if updated[i] == 0 else 1 - updated[i]
                            updated[i] = 1 if progress == resources[i]["fetch_ticks"] else -progress
                            cost += resources[i]["fetch_cost"]
                        elif op == "writeback":
                            progress = updated[i] - 1
                            updated[i] = (
                                1 if progress == resources[i]["writeback_ticks"] else progress + 2
                            )
                            cost += resources[i]["writeback_cost"]
                        at = position
                        next_running = active
                        next_remaining = ticks
                        if active >= 0:
                            next_remaining -= 1
                            if next_remaining == 0:
                                at = pieces[active]["b"]
                                next_running = -1
                                for j in writes[active]:
                                    updated[j] = 2
                        path = value[1] + ((pieces[start]["id"],) if start >= 0 else ())
                        action = (
                            pieces[start]["id"] if start >= 0 else "",
                            tuple(ids[j] for j in dropped),
                            op,
                            ids[i] if i >= 0 else "",
                        )
                        candidate = (cost, path, value[2] + (action,))
                        state = (at, next_running, next_remaining, tuple(updated))
                        previous = following.get(state)
                        if previous is None or priority(candidate) < priority(previous):
                            following[state] = candidate
        states = following
    return {"plan": None}


def resource(key, fetch=1, writeback=1, available=None, bytes_=1, fetch_cost=1, writeback_cost=1):
    return {
        "id": key,
        "bytes": bytes_,
        "fetch_ticks": fetch,
        "writeback_ticks": writeback,
        "fetch_cost": fetch_cost,
        "writeback_cost": writeback_cost,
        "available": [[0, 30]] if available is None else available,
    }


def piece(key, a, b, requires, writes=(), ticks=1, release=0, deadline=18):
    return {
        "id": key,
        "a": a,
        "b": b,
        "requires": list(requires),
        "writes": list(writes),
        "ticks": ticks,
        "release": release,
        "deadline": deadline,
    }


def async_inputs():
    # Hand-derived anchor mechanisms occur in the scored stream, then seeded
    # branching/availability cases combine them. No oracle is copied to inputs.
    cases = [
        {
            "n": 1,
            "cache_bytes": 1,
            "horizon": 6,
            "resources": [resource("a", writeback=2)],
            "pieces": [piece("p", 0, 1, ["a"], ["a"])],
        },
        {
            "n": 2,
            "cache_bytes": 2,
            "horizon": 8,
            "resources": [resource("a"), resource("b", fetch=3)],
            "pieces": [piece("p", 0, 1, ["a"], ["a"], ticks=3), piece("q", 1, 2, ["b"])],
        },
        {
            "n": 2,
            "cache_bytes": 2,
            "horizon": 9,
            "resources": [resource("a"), resource("b", fetch=3, available=[[1, 2], [3, 5]])],
            "pieces": [piece("p", 0, 1, ["a"], ticks=4), piece("q", 1, 2, ["b"])],
        },
        {
            "n": 2,
            "cache_bytes": 1,
            "horizon": 10,
            "resources": [resource("a"), resource("b", fetch=3)],
            "pieces": [piece("p", 0, 1, ["a"], ticks=2), piece("q", 1, 2, ["b"])],
        },
        {
            "n": 2,
            "cache_bytes": 1,
            "horizon": 12,
            "resources": [resource("a", writeback=3)],
            "pieces": [piece("p", 0, 1, ["a"], ["a"]), piece("q", 1, 2, ["a"], ["a"], release=4)],
        },
        {
            "n": 2,
            "cache_bytes": 1,
            "horizon": 10,
            "resources": [resource("a", writeback=2), resource("b")],
            "pieces": [piece("p", 0, 1, ["a"], ["a"]), piece("q", 1, 2, ["b"])],
        },
        {
            "n": 1,
            "cache_bytes": 1,
            "horizon": 4,
            "resources": [resource("a", fetch=3, available=[[0, 2]])],
            "pieces": [piece("p", 0, 1, ["a"])],
        },
        {
            "n": 2,
            "cache_bytes": 2,
            "horizon": 9,
            "resources": [resource("a", fetch=2), resource("b", writeback=2)],
            "pieces": [piece("p", 0, 1, ["b"], ["b"]), piece("q", 1, 2, ["b"], ticks=3)],
        },
    ]
    for seed in range(10):
        rng = random.Random(4795000 + seed)
        count = 2 + seed % 2
        n = 2 + seed % 2
        rr = [
            resource(
                chr(97 + i),
                fetch=1 + rng.randrange(3),
                writeback=1 + rng.randrange(3),
                available=[[0, 3 + i], [4 + i, 14]],
                fetch_cost=1 + rng.randrange(3),
                writeback_cost=1 + rng.randrange(3),
            )
            for i in range(count)
        ]
        pp = []
        for a in range(n):
            for step in (1, 2):
                if a + step > n:
                    continue
                selected = rng.sample([r["id"] for r in rr], 1 + (seed + a + step) % 2)
                pp.append(
                    piece(
                        f"p{a}{step}",
                        a,
                        a + step,
                        selected,
                        selected[: (seed + step) % 2],
                        ticks=1 + rng.randrange(3),
                        release=a,
                        deadline=10 + seed % 4,
                    )
                )
        cases.append(
            {"n": n, "cache_bytes": count, "horizon": 12 + seed % 3, "resources": rr, "pieces": pp}
        )
    # A second path can overtake an earlier-starting path after durability/eviction.
    cases.append(
        {
            "n": 3,
            "cache_bytes": 2,
            "horizon": 16,
            "resources": [
                resource("a", writeback=3),
                resource("b", fetch=2),
                resource("c", fetch=2),
            ],
            "pieces": [
                piece("cheap", 0, 1, ["a"], ["a"]),
                piece("fast", 0, 2, ["b"], ticks=3),
                piece("middle", 1, 2, ["a", "c"], ["c"]),
                piece("tail", 2, 3, ["b", "c"], ticks=2),
            ],
        }
    )
    # Four resources, distinct sizes, split fetch windows and branching recovery.
    cases.append(
        {
            "n": 3,
            "cache_bytes": 3,
            "horizon": 16,
            "resources": [
                resource("a", writeback=2, bytes_=2),
                resource("b", fetch=2),
                resource("c", fetch=3, available=[[1, 3], [5, 9]]),
                resource("d", writeback=3),
            ],
            "pieces": [
                piece("p", 0, 1, ["a"], ["a"], ticks=2),
                piece("q", 0, 2, ["b", "d"], ["d"], ticks=3),
                piece("r", 1, 3, ["b", "c"], ticks=2),
                piece("s", 2, 3, ["b", "c"], ["b"]),
            ],
        }
    )
    # Input-order permutation is a scored metamorphic case.
    permuted = copy.deepcopy(cases[-2])
    permuted["resources"].reverse()
    permuted["pieces"].reverse()
    cases.append(permuted)
    # Maximum resource/piece counts: availability, dirty versions and alternative
    # chains force simultaneous prefetch/writeback scheduling, not path scoring.
    cases.append(
        {
            "n": 6,
            "cache_bytes": 3,
            "horizon": 18,
            "resources": [
                resource("a", fetch=2, writeback=3),
                resource("b", fetch=3, writeback=2, available=[[0, 4], [6, 15]], fetch_cost=2),
                resource("c", fetch=2, writeback=4, available=[[1, 5], [8, 18]]),
                resource("d", fetch=4, writeback=2, writeback_cost=3),
            ],
            "pieces": [
                piece("a01", 0, 1, ["a"], ["a"], ticks=2),
                piece("b02", 0, 2, ["b"], ["b"], ticks=3),
                piece("c13", 1, 3, ["a", "c"], ["c"]),
                piece("d23", 2, 3, ["b", "c"], ticks=2),
                piece("e34", 3, 4, ["d"], ["d"]),
                piece("f35", 3, 5, ["a", "c"], ["a"], ticks=2),
                piece("g46", 4, 6, ["c", "d"], ticks=2),
                piece("h56", 5, 6, ["a", "b"], ticks=2),
            ],
        }
    )
    return cases


PROMPT = """Восстанови файл[0,n) с асинхронным cache и обязательной durability записей.
Вход {n,cache_bytes,horizon,resources,pieces}. Время — целые ticks, t=0..horizon.
Resource={id,bytes,fetch_ticks,writeback_ticks,fetch_cost,writeback_cost,available:[[a,b],...]};
все id уникальные непустые ASCII строки. bytes/fetch_ticks/writeback_ticks>0, costs>=0.
Fetch/writeback costs платятся ЗА КАЖДЫЙ занятый I/O tick, включая впоследствии потерянный
прогресс. Available разрешает КАЖДЫЙ fetch tick при a<=t<b; writeback всегда доступен.
Изначально cache пуст, восстановлено0, CPU и единственный I/O канал свободны.
Piece={id,a,b,requires,writes,ticks,release,deadline}; 0<=a<b<=n, ticks>0, release>=0,
requires — уникальные известные resource id, writes — подмножество requires. Piece можно
начать, только когда предыдущий завершился и его a равно восстановленной координате;
t>=release, t+ticks<=deadline. Первая a=0, последний b=n. Стартованный piece выполняется
CPU непрерывно ticks тиков, прерывать нельзя. Весь requires должен быть полностью fetched
(clean ИЛИ dirty) НА НАЧАЛО start tick. Частично fetched ресурс читать нельзя.
В начале КАЖДОГО t выбери действие {start,drop,io}, строго в таком порядке:
1. drop — отсортированный список resident CLEAN или PARTIALLY FETCHED ресурсов; удалить
их бесплатно. Не включай отсутствующие, dirty или pinned ресурсы. Fetch прогресс теряется,
оплаченные ticks не возвращаются. Dirty можно удалить только после полного writeback.
2. start=null или id следующего piece. Если CPU уже работает, start обязательно null.
3. io=null либо {op:"fetch"|"writeback",resource:id}; выполняется ровно ОДИН tick выбранной
операции, параллельно одному tick CPU. Pinned ресурс — любой requires работающего ИЛИ
только что начатого piece; до конца текущего tick его нельзя drop/fetch/writeback.
Fetch разрешён для отсутствующего или частично fetched ресурса. При ПЕРВОМ fetch tick
он резервирует ПОЛНЫЕ resource.bytes в cache, включая незавершённый fetch. Сумма bytes
всех resident/partially fetched<=cache_bytes всегда. После fetch_ticks накопленных ticks
становится clean. Fetch можно паузить, прогресс сохраняется; каждое возобновление снова
проверяет available. Повторно fetch clean/dirty ресурс нельзя, сначала допустимый drop.
Writeback разрешён только dirty и не pinned. Накопи writeback_ticks; паузы сохраняют прогресс;
полное завершение делает ресурс clean. Частичный writeback не делает ресурс clean и НЕ
освобождает его bytes. Такой ресурс всё ещё можно читать следующим piece, но I/O на нём
приостанавливается на время pinning.
В КОНЦЕ tick сначала завершается I/O и обновляется его прогресс/состояние, затем CPU
уменьшает remaining. Завершившийся piece переводит координату в b, а ВСЕ writes делает
DIRTY с writeback progress0, даже если они уже dirty/частично written back. Это новая версия
данных; старый частичный writeback прогресс теряется без возврата оплаты. Pinned снимается
только на СЛЕДУЮЩЕЙ границе t+1. Fetch, завершившийся в конце tick, нельзя использовать
для CPU start того же tick. Свободный CPU может ждать; пустой I/O tick стоит0.
Финиш — первая граница t<=horizon, когда координатаn, CPU свободен, нет dirty ресурсов.
Все посторонние partially fetched ресурсы автоматически отменяются на финише бесплатно,
без отдельного действия; ранее потраченная стоимость остаётся. Clean cache может оставаться.
Оптимизируй: минимальный finish, затем минимальный io_cost, затем число pieces, затем lex
массив piece.id в порядке восстановления, затем lex ПОЛНОЕ расписание. Для последнего
каждый tick сравнивается как tuple(start_or_empty_string, tuple(drop), op_or_empty_string,
resource_or_empty_string); строки сравниваются как Python/ASCII, расписания по tick order.
Выход РОВНО {finish:int,io_cost:int,plan:[piece_id,...],schedule:[{start:id|null,drop:[id],
io:{op,resource}|null},...]} с len(schedule)=finish, tick индексируется с0. Если невозможно
завершить к horizon — ровно {plan:null}. Локальная жадность, суммирование fetch times или
единовременная плата за donor недостаточны: прогресс, pinning и durability меняют план.
Размеры:1<=n<=10^12,1..4resources,1..8pieces,1<=horizon<=18,1<=bytes<=3,
1<=cache_bytes<=5,1<=fetch_ticks/writeback_ticks/piece.ticks<=4,costs<=5;
0<=release<=deadline<=18, available endpoints0..30. Порядок входных массивов не влияет.
Все внешние JSON поля/типы корректны. Нельзя менять временные бюджеты харнесса."""

GOLD = (
    "import itertools\n"
    + inspect.getsource(async_repair_solve)
    + '\nsolve=async_repair_solve\n\nimport json,sys\nfor line in sys.stdin:\n print(json.dumps(solve(json.loads(line)),separators=(",",":")))\n'
)
CASES = [(x, async_repair_solve(x)) for x in async_inputs()]


def harden_479_v5(tasks):
    old = next(t for t in tasks if t.id.startswith("task_479_"))
    task = program_task(
        number=479,
        slug=old.id.split("_", 2)[2],
        name="Schedule recovery with asynchronous dirty-cache writeback",
        parent=355,
        prompt=PROMPT,
        cases=CASES,
        gold=GOLD,
        tags=("hardening-v4", "hardening-v5", "async-cache", "durability"),
    )
    return [task if t.id == old.id else t for t in tasks]
