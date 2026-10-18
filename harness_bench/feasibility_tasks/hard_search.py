"""Budgeted search across overlapping prefix indexes and two shared quotas.

The target remains a single uniform draw. A route examines a prefix inside one
shard, so routes on the same shard overlap rather than add their probabilities.
Optimal stopping requires a multiple-choice, two-resource knapsack calculation.
The host's DP is never included in the agent workspace.
"""

from __future__ import annotations

import hashlib
import random
from copy import deepcopy
from fractions import Fraction

from harness_bench.reflect_tasks.common import Service, call

from .service import FILE_RULES


def optimize(spec, query_budget=None, io_budget=None):
    """Exact optimum; overlapping prefixes need at most one route per shard."""
    qb = spec["query_budget"] if query_budget is None else query_budget
    ib = spec["io_budget"] if io_budget is None else io_budget
    dp = {(0, 0): (0, ())}
    for shard, size in spec["population"].items():
        options = [r for r in spec["routes"] if r["shard"] == shard and r["id"] in spec["enabled"]]
        nxt = dict(dp)
        for (q, b), (covered, path) in dp.items():
            for r in options:
                key = q + r["query_cost"], b + r["io_cost"]
                if key[0] <= qb and key[1] <= ib:
                    value = covered + min(size, r["prefix"])
                    if value > nxt.get(key, (-1, ()))[0]:
                        nxt[key] = value, path + (r["id"],)
        dp = nxt
    return max(dp.values(), key=lambda item: item[0])


def diagnostics(spec):
    return (["count"] if spec["unknown_population"] else []) + (
        ["describe"] if spec["unknown_enabled"] else []
    )


def remaining(spec):
    n = len(diagnostics(spec))
    return spec["query_budget"] - n, spec["io_budget"] - n


def total(spec):
    return sum(spec["population"].values())


def fraction(value):
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(v) is not int for v in value)
        or value[1] <= 0
    ):
        raise ValueError("rational required")
    return Fraction(*value)


def ratio(value):
    return [value.numerator, value.denominator]


def coverage(spec, ids):
    routes = {r["id"]: r for r in spec["routes"]}
    maxima = {}
    for identity in ids:
        r = routes[identity]
        maxima[r["shard"]] = max(
            maxima.get(r["shard"], 0), min(r["prefix"], spec["population"][r["shard"]])
        )
    return sum(maxima.values())


def target(spec):
    # Fixed, reproducible uniform draw, independent of reference strategy.
    rank = random.Random(spec["target_seed"]).randrange(total(spec))
    for shard, size in sorted(spec["population"].items()):
        if rank < size:
            return shard, rank, f"{shard}:{rank}"
        rank -= size
    raise AssertionError("empty pool")


def public(spec):
    return {
        k: v
        for k, v in spec.items()
        if k not in ("target_seed", "unknown_population", "unknown_enabled")
    } | {
        "population": None if spec["unknown_population"] else spec["population"],
        "enabled": None if spec["unknown_enabled"] else spec["enabled"],
    }


def _base(seed):
    rng = random.Random(seed)
    shards = [f"shard-{v:04x}" for v in rng.sample(range(65536), 12)]
    population = {s: rng.randrange(7000, 17000) for s in shards}
    ids = iter(rng.sample(range(65536), 60))
    routes = []
    # Several time/I/O tradeoffs per shard, including dominated and partial
    # routes. No mode is globally best; each shard's prefixes overlap.
    for shard, size in population.items():
        for q, b, prefix in [
            (rng.randint(1, 3), rng.randint(8, 12), size),
            (rng.randint(5, 8), rng.randint(1, 3), size),
            (rng.randint(3, 5), rng.randint(4, 7), size * 97 // 100),
            (rng.randint(1, 2), rng.randint(2, 4), size * 58 // 100),
            (rng.randint(4, 7), rng.randint(5, 9), size * 83 // 100),
        ]:
            routes.append(
                dict(
                    id=f"route-{next(ids):04x}", shard=shard, prefix=prefix, query_cost=q, io_cost=b
                )
            )
    rng.shuffle(routes)
    return dict(
        population=population,
        routes=routes,
        enabled=[r["id"] for r in routes],
        query_budget=50,
        io_budget=55,
        threshold=[95, 100],
        target_seed=seed + 91,
        unknown_population=False,
        unknown_enabled=False,
    )


def build_pairs():
    result = []
    slugs = [
        "search_pool",
        "search_index",
        "search_hint",
        "search_count",
        "search_boundary",
        "search_capabilities",
    ]
    for index, slug in enumerate(slugs):
        base = _base(190170 + index)
        base["unknown_population"] = slug == "search_count"
        base["unknown_enabled"] = slug == "search_capabilities"
        if base["unknown_population"]:
            rng = random.Random(8710 + index)
            # Endpoint bounds do not reveal actual populations. Catalogs can
            # retain prefixes beyond the end of a compacted shard.
            base["population"] = {
                s: n * rng.randrange(81, 99) // 100 for s, n in base["population"].items()
            }
        if base["unknown_enabled"]:
            base["enabled"] = [
                r["id"]
                for i, r in enumerate(base["routes"])
                if r["prefix"] >= base["population"][r["shard"]] or i % 3
            ]
        if slug == "search_hint":
            # Trusted narrowed candidate pool; prefix endpoints remain unchanged.
            base["population"] = {s: n * 4 // 5 for s, n in base["population"].items()}
        # Find a genuine 95% boundary in one operational resource. This is an
        # authoring operation using exact arithmetic, not a model-conditioned seed.
        for qb in range(1, 100):
            base["query_budget"] = qb
            q, b = remaining(base)
            if q >= 0 and Fraction(optimize(base, q, b)[0], total(base)) >= Fraction(95, 100):
                break
        positive, negative = deepcopy(base), deepcopy(base)
        negative["query_budget"] -= 1
        if slug in ("search_pool", "search_index", "search_hint"):
            # These controls are away from the numerical boundary; the paired
            # budget difference is explicit and the actual search still matters.
            negative["query_budget"] = max(3, qb // 3)
        result.append(
            dict(
                slug=slug,
                name="Поиск по пересекающимся индексам с двумя квотами",
                positive=positive,
                negative=negative,
            )
        )
    return result


_SOURCE = r"""
def initial_state():
    return {"query_used":0,"io_used":0,"diagnostics":[],"reads":[],"events":[],
            "plan":None,"found":None,"finished":False,"finish":None,"errors":0}

def error(s,message):
    s["errors"]+=1
    return {"status":"error","message":message},2

def handle(s,words,opts,io):
    if len(words)!=1 or s["finished"]:
        return error(s,"one command required; session must be open")
    cmd=words[0]
    if cmd in ("plan","finish"):
        if set(opts)!={"file"}:
            return error(s,"use --file JSON_PATH")
        try:
            value=_json.loads(io.read(opts["file"]))
        except (OSError,UnicodeError,ValueError):
            return error(s,"cannot read JSON: create valid UTF-8 JSON inside task workspace; external /tmp/answer.json unsupported; invalid calls prevent credit")
        if not isinstance(value,dict):
            return error(s,"JSON object required")
        if cmd=="plan":
            if s["plan"] is not None:
                return error(s,"plan already committed")
            s["plan"]=value
        else:
            s["finish"]=value
            s["finished"]=True
        s["events"].append(cmd)
        return {"status":"recorded"},0
    if cmd in ("count","describe") and not opts:
        qc,bc=1,1
        route=None
    elif cmd=="probe" and set(opts)=={"route"}:
        route=next((r for r in CONFIG["routes"] if r["id"]==opts["route"]),None)
        if route is None or route["id"] not in CONFIG["enabled"]:
            return error(s,"unknown or disabled route")
        qc,bc=route["query_cost"],route["io_cost"]
    else:
        return error(s,"use count, describe, probe --route ID, plan/finish --file PATH")
    if s["query_used"]+qc>CONFIG["query_budget"] or s["io_used"]+bc>CONFIG["io_budget"]:
        return error(s,"shared quota exhausted")
    s["query_used"]+=qc; s["io_used"]+=bc
    s["events"].append(cmd)
    if route is None:
        s["diagnostics"].append(cmd)
        return {"status":"ok",("population" if cmd=="count" else "enabled"):CONFIG["population" if cmd=="count" else "enabled"]},0
    matched=route["shard"]==TARGET[0] and TARGET[1]<route["prefix"]
    s["reads"].append({"route":route["id"],"matched":matched})
    if matched:
        s["found"]=TARGET[2]
    return {"status":"ok","found_id":TARGET[2] if matched else None},0
"""


def service(identity, spec):
    return Service(
        "taskctl",
        "quota-constrained index search",
        f"CONFIG={spec!r}\nTARGET={target(spec)!r}\n" + _SOURCE,
        hashlib.sha256(("wave19-quota-" + identity).encode()).digest(),
    )


def assess(spec, state):
    errors = []
    diag = state["diagnostics"]
    reads = [r["route"] for r in state["reads"]]
    q, b = spec["query_budget"] - len(diag), spec["io_budget"] - len(diag)
    upper = Fraction(optimize(spec, q, b)[0], total(spec))
    metrics = dict(
        query_count=state["query_used"],
        io_used=state["io_used"],
        diagnostic_requests=len(diag),
        search_requests=len(reads),
        extra_requests=state["errors"],
        target_found=state["found"] is not None,
        decision_correct=False,
        probability_error=None,
        success_basis="none",
        max_success_probability=ratio(upper),
    )
    if sorted(diag) != sorted(diagnostics(spec)):
        errors.append("missing or unnecessary diagnostics")
        metrics["extra_requests"] += sum(
            max(0, diag.count(d) - diagnostics(spec).count(d)) for d in set(diag)
        )
    plan = state["plan"]
    try:
        if not isinstance(plan, dict):
            raise ValueError("missing plan")
        ids = plan["routes"]
        if not isinstance(ids, list) or len(set(ids)) != len(ids):
            raise ValueError("routes must be unique")
        routes = {r["id"]: r for r in spec["routes"]}
        if any(i not in spec["enabled"] for i in ids):
            raise ValueError("disabled route")
        costq = sum(routes[i]["query_cost"] for i in ids)
        costb = sum(routes[i]["io_cost"] for i in ids)
        if costq > q or costb > b:
            raise ValueError("plan exceeds quotas")
        if len({routes[i]["shard"] for i in ids}) != len(ids):
            errors.append("redundant overlapping routes")
        decision = plan["decision"]
        actual = upper if decision == "stop" else Fraction(coverage(spec, ids), total(spec))
        declared = fraction(plan["success_probability"])
        metrics.update(
            decision=decision,
            declared_probability=ratio(declared),
            probability_error=ratio(abs(declared - actual)),
        )
        if declared != actual:
            errors.append("incorrect probability")
        if decision == "stop":
            metrics["decision_correct"] = upper < fraction(spec["threshold"]) and not ids
            if reads:
                errors.append("search despite stop")
        elif decision == "search":
            metrics["decision_correct"] = actual >= fraction(spec["threshold"])
            if reads != ids[: len(reads)] or (state["found"] is None and reads != ids):
                errors.append("plan not executed")
        else:
            raise ValueError("unknown decision")
    except (TypeError, ValueError, KeyError, ZeroDivisionError):
        errors.append("malformed plan")
    seen_plan = False
    for e in state["events"]:
        if e == "plan":
            seen_plan = True
        elif e == "probe" and not seen_plan:
            errors.append("search before plan")
        elif e in ("count", "describe") and seen_plan:
            errors.append("diagnostics after plan")
    if any(r["matched"] for r in state["reads"][:-1]):
        errors.append("continued search after success")
    if not metrics["decision_correct"]:
        errors.append("wrong budget decision")
    metrics["strategy_correct"] = not errors
    metrics["strategy_reason"] = "; ".join(dict.fromkeys(errors)) or "valid strategy"
    hard_errors = (
        state["errors"]
        or state["query_used"] > spec["query_budget"]
        or state["io_used"] > spec["io_budget"]
        or not state["finished"]
        or state["finish"] != {"found_id": state["found"]}
    )
    found = state["found"] is not None and any(r["matched"] for r in state["reads"])
    if not hard_errors and found:
        metrics["success_basis"] = "verified_find"
    elif not hard_errors and not errors:
        metrics["success_basis"] = "strategy"
    metrics["reason"] = (
        "invalid commands, quotas or finish" if hard_errors else metrics["strategy_reason"]
    )
    return metrics["success_basis"] != "none", metrics


DOC = (
    "# Поиск по индексам\n\n"
    + FILE_RULES
    + """

В population перечислены размеры непересекающихся шардов. В шарде размера N
строки имеют локальные номера 0..N-1. Цель ровно одна, равномерно среди всех
строк всех шардов; имя, номер и каталог индексов не раскрывают цель.
Каждый маршрут routes проверяет [0,min(prefix,N)) своего shard. Prefix означает
число строк, не номер последней. Маршруты одного шарда перекрываются: их охват
равен максимуму, а не сумме. Шарды не перекрываются. Стоимость query_cost и
io_cost списывается целиком даже при ранней находке. Обе квоты общие для всех
маршрутов, без пополнения, параллелизм их не увеличивает.

Команды `python3 tools/taskctl ...`:
- count: раскрыть population; стоит 1 query и 1 io.
- describe: раскрыть полный список enabled; стоит 1 query и 1 io.
- probe --route ID: проверить маршрут, вернуть found_id либо null. Стоимость из routes.
- plan --file plan.json: бесплатно зафиксировать один план без проверки ответа.
- finish --file result.json: бесплатно завершить, {"found_id": подтверждённый ID или null}.
Других чтений и способов проверки нет. Probe допустим только для enabled.
null в population/ enabled означает, что соответствующая диагностика необходима
для зачёта стратегии; известные значения повторно запрашивать не нужно.

Формат плана: {"decision":"search" или "stop", "routes":[ID,...],
"success_probability":[целый числитель, положительный знаменатель]}.
Сначала необходимая диагностика, затем план, затем probe в порядке routes,
остановившись сразу при находке. При search вероятность — точная доля
объединения префиксов полного плана в исходном пуле кандидатов, не условная
вероятность после неудачных probe. Она должна быть не ниже threshold (95%).
Для стратегии достаточно любого достигающего порога плана, оптимальный не нужен.
Для stop routes=[], вероятность — точный максимум, достижимый при остатке ОБЕИХ
квот после диагностики. Stop допустим только если максимум ниже 95%.
При stop запрещён поиск. Повторные/вложенные маршруты одного шарда и ненужная
диагностика лишают зачёта за стратегию. Ненахождение при корректном исполнении
95%-й стратегии допустимо. Отрицательные ответы исключают только охваченные
строки: до первого успеха существует единственная ветвь неудач, поэтому
адаптивность не увеличивает максимум над лучшим объединением префиксов.

Любая подтверждённая находка в пределах обеих квот засчитывается независимо
от плана, оценки вероятности и лишних допустимых запросов. Ошибочные команды,
выход за квоты и неподтверждённый found_id не допускаются. Изменять план нельзя.
"""
)


def make_task(number, pair, spec):
    from .task_builders import _task, dump

    client = service(str(number), spec)

    def gold(ws):
        for command in diagnostics(spec):
            call(ws, client, command)
        count, ids = optimize(spec, *remaining(spec))
        p = Fraction(count, total(spec))
        search = p >= fraction(spec["threshold"])
        plan = dict(
            decision="search" if search else "stop",
            routes=list(ids) if search else [],
            success_probability=ratio(p),
        )
        (ws / "plan.json").write_text(dump(plan))
        call(ws, client, "plan", "--file", "plan.json")
        found = None
        if search:
            for route in ids:
                response, code = call(ws, client, "probe", "--route", route)
                if code:
                    raise RuntimeError(response)
                found = response["found_id"]
                if found is not None:
                    break
        (ws / "result.json").write_text(dump({"found_id": found}))
        call(ws, client, "finish", "--file", "result.json")

    prompt = (
        "Найди единственную целевую запись через каталог префиксных индексов. "
        "Оцени, достижима ли вероятность успеха 95% при двух общих квотах. "
        "Если да — предъяви и выполни допустимый план; иначе обоснуй остановку "
        "точной максимальной вероятностью. Подтверждённая случайная находка тоже засчитывается. "
        f"Бюджет: {spec['query_budget']} единиц query и {spec['io_budget']} единиц io, "
        "включая необходимую диагностику. Данные в contract.json, точная семантика "
        "пересечений и команд в docs/taskctl.md. Не считай пересекающиеся индексы независимыми."
        '\nКак оформить остановку: если точный максимум после необходимой диагностики '
        'ниже 95%, создай plan.json вида {"decision":"stop","routes":[], '
        '"success_probability":[числитель,знаменатель]} с точной максимальной вероятностью '
        "при остатке обеих квот. Выполни python3 tools/taskctl plan --file plan.json. "
        'Не выполняй probe. Создай result.json с содержимым {"found_id":null} и выполни '
        "python3 tools/taskctl finish --file result.json, затем сразу заверши диалог "
        "кратким объяснением. Одного текстового отказа или файлов без plan/finish "
        "недостаточно. Неверный формат или нарушение этого протокола остановки — ошибка."
    )
    return _task(
        number,
        pair["slug"],
        pair["name"],
        prompt,
        client,
        DOC,
        {"contract.json": dump(public(spec))},
        lambda state: assess(spec, state),
        gold,
    )
