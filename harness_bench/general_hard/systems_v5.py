"""Four post-calibration systems revisions; standalone gold algorithms."""
from __future__ import annotations

import inspect
import textwrap

from .common import artifact_task, dumps, program_task

TASKS = []
CASES = {}
ANCHORS = {}
SOLVERS = {}
ARTIFACT_DATA = {}


def solve487(data):
    import json
    from fractions import Fraction
    from functools import lru_cache
    from itertools import product

    actions = sorted(data["actions"], key=lambda a: a["id"])
    transition = [[Fraction(*p) for p in row] for row in data["transition"]]

    def code(policy):
        return json.dumps(policy, sort_keys=True, separators=(",", ":"))

    def pack(weights):
        total = sum(weights.values())
        return tuple((*state, (weight / total).numerator, (weight / total).denominator)
                     for state, weight in sorted(weights.items()) if weight)

    def prune(rows):
        unique = {}
        for reward, success, policy in rows:
            key = reward, success
            if key not in unique or code(policy) < code(unique[key]):
                unique[key] = policy
        return tuple((r, p, policy) for (r, p), policy in unique.items()
                     if not any(rr >= r and pp >= p and (rr, pp) != (r, p) for rr, pp in unique))

    @lru_cache(None)
    def frontier(t, used, energy, belief):
        if t == data["horizon"]:
            reward = sum(Fraction(num, den) * value for _, value, _, num, den in belief)
            success = sum(Fraction(num, den) for _, value, _, num, den in belief if value >= data["threshold"])
            return ((reward, success, {"done": True}),)
        options = [(None, {"id": "wait", "energy": 0})] + [
            (i, a) for i, a in enumerate(actions) if not (used >> i & 1) and a["energy"] <= energy
        ]
        answers = []
        for index, action in options:
            groups = {}
            for mode, reward, pending, num, den in belief:
                outcomes = action["outcomes"][mode] if index is not None else [
                    {"label": "_", "reward": 0, "p": [1, 1]}
                ]
                for outcome in outcomes:
                    probability = Fraction(num, den) * Fraction(*outcome["p"])
                    for next_mode, chance in enumerate(transition[mode]):
                        mass = probability * chance
                        if not mass:
                            continue
                        signal = action["id"] + ":" + outcome["label"] if index is not None else "_"
                        state = next_mode, reward + outcome["reward"], signal
                        branch = groups.setdefault(pending, {})
                        branch[state] = branch.get(state, Fraction(0)) + mass
            branches = []
            for observation, weights in sorted(groups.items()):
                probability = sum(weights.values())
                child = frontier(t + 1, used if index is None else used | 1 << index,
                                 energy - action["energy"], pack(weights))
                branches.append((observation, probability, child))
            for selected in product(*(b[2] for b in branches)):
                reward = sum(b[1] * c[0] for b, c in zip(branches, selected, strict=True))
                success = sum(b[1] * c[1] for b, c in zip(branches, selected, strict=True))
                policy = {"action": action["id"], "branches": [
                    {"observation": b[0], "next": c[2]} for b, c in zip(branches, selected, strict=True)
                ]}
                answers.append((reward, success, policy))
        return prune(answers)

    initial = pack({(i, 0, "_"): Fraction(*p) for i, p in enumerate(data["prior"])})
    feasible = [row for row in frontier(0, 0, data["energy"], initial)
                if row[1] >= Fraction(*data["required_probability"])]
    if not feasible:
        return {"feasible": False}
    reward, success, policy = min(feasible, key=lambda row: (-row[0], -row[1], code(row[2])))
    return {"feasible": True, "reward": [reward.numerator, reward.denominator],
            "success": [success.numerator, success.denominator], "policy": policy}


PROMPT487 = '''
Реши finite-horizon partially observed retry/inference control с отложенной
информацией И chance constraint. Вход {horizon:1..4,energy:0..6,prior,
transition,actions,threshold,required_probability}. Два hidden modes Z=0,1.
prior и каждая строка transition — точные рациональные вероятности [num,den],
den>0, сумма1. В t=0 Z~prior, accumulated reward=0. Hidden state=(Z,accumulated reward,pending signal); текущий belief хранит все три, не только Z. <=4 actions с уникальными
ASCII id (не wait, без двоеточия): {id,energy:0..3,outcomes:[row0,row1]}.
Row —1..2 outcomes {label:ASCII без двоеточия,reward:0..12,p:[num,den]},
labels внутри row уникальны, их union по двум row<=2, сумма вероятностей1; один и тот же label разных
режимов может иметь разные rewards. Каждый action доступен максимум ОДИН раз,
расходует energy; wait доступен всегда, reward0 и signal "_". Один action/тик.

Фаза тика t: (1) политика имеет только наблюдения, доставленные к началу t;
(2) выбирает действие; (3) outcome независимо сэмплируется по текущему Z_t,
reward добавляется СЕЙЧАС, но сама награда НЕ наблюдается отдельно;
(4) Z_{t+1}~transition[Z_t], независимо от outcome условно при Z_t;
(5) в начале t+1 доставляется signal действия t-1 (задержка РОВНО2 от старта).
Signal action — "ID:label", wait — "_". В начале t=0 и t=1 signal "_".
Политика НЕ видит Z, accumulated reward или ещё не доставленный signal.
Никакого сброса режима между actions: mode эволюционирует даже при wait.
Условно при всей mode trajectory outcomes независимы. Поздние signals после
horizon не влияют на решения. В horizon прекращение обязательно, без action.

Нужна ДЕТЕРМИНИРОВАННАЯ history-dependent policy, без рандомизации действий.
P(terminal accumulated reward>=threshold) >= required_probability. Среди таких
policy максимизируй (expected terminal reward, указанная success probability),
затем минимизируй canonical JSON policy string: json.dumps(sort_keys=True,
separators=(",",":"),ensure_ascii=True). Все keys/labels ASCII.
Chance constraint применяется к начальному распределению, НЕ отдельно к
каждой ветви. Нельзя выбрать лишь локальную максимальную expectation policy:
разные доступные suffix policies дают frontier риск/награда, который нужно
комбинировать с вероятностями наблюдений. Нельзя convexify frontier: randomized
mixture НЕ является разрешённой policy.
Policy {done:true} в horizon, иначе {action:ID|wait,branches:[{observation,next}]},
ветви по observation, только positive-probability observations НАЧАЛА следующего
тика, включая последнюю доставку в horizon. Полная policy сохраняет h действий
на каждой ветви. Вывод {feasible:false} либо {feasible:true,reward:[num,den],
success:[num,den],policy}. Дроби несократимы. Policy output<=200KB; точное число
недоминируемых (reward,success) в каждом reachable belief<=512.
'''


def solve489(data):
    import json
    from functools import lru_cache

    actions = sorted(data["actions"], key=lambda a: a["id"])
    sizes = data["sizes"]
    infinity = 10 ** 12

    def safe(tree):
        return (sum(sizes[b] for b in tree.values()) <= data["disk"]
                and not any(q.startswith(p + "/") for p in tree for q in tree if p != q)
                and all(any(tree.get(p) == b for p, b in clause) for clause in data["invariants"]))

    def goal(belief):
        return all(dict(state) == data["target"] for state, _ in belief)

    @lru_cache(None)
    def outcomes(belief, index):
        action = actions[index]
        groups = {}
        for encoded, faults in belief:
            stable = dict(encoded)
            if any(stable.get(p) != b for p, b in action["guards"].items()):
                return None
            if action["kind"] == "probe":
                label = json.dumps([[p, stable.get(p)] for p in action["paths"]], separators=(",", ":"))
                groups.setdefault(label, set()).add((encoded, faults))
                continue
            volatile, allocated = dict(stable), 0
            for op in action["ops"] + [{"op": "flush"}]:
                if op["op"] == "write":
                    path, blob = op["path"], op["blob"]
                    if any(path == p or path.startswith(p + "/") for p in data["protected"]):
                        return None
                    if blob is None:
                        volatile.pop(path, None)
                    else:
                        volatile[path] = blob
                        allocated += sizes[blob]
                    if any(q.startswith(p + "/") for p in volatile for q in volatile if p != q):
                        return None
                    if sum(sizes[b] for b in stable.values()) + allocated > data["disk"]:
                        return None
                else:
                    stable, allocated = dict(volatile), 0
                    if not safe(stable):
                        return None
                if faults:
                    groups.setdefault("lost", set()).add((tuple(sorted(stable.items())), faults - 1))
            groups.setdefault("ack", set()).add((tuple(sorted(stable.items())), faults))
        return tuple((label, tuple(sorted(members))) for label, members in sorted(groups.items()))

    @lru_cache(None)
    def minimum(belief, remaining):
        if goal(belief):
            return 0
        if not remaining:
            return infinity
        best = infinity
        for i, action in enumerate(actions):
            groups = outcomes(belief, i)
            if groups is None:
                continue
            worst = max(minimum(child, remaining - 1) for _, child in groups)
            best = min(best, action["cost"] + worst)
        return best

    @lru_cache(None)
    def reconstruct(belief, remaining, budget):
        if goal(belief):
            return {"done": True}
        choices = []
        for i, action in enumerate(actions):
            groups = outcomes(belief, i)
            if groups is None or action["cost"] > budget:
                continue
            allowance = budget - action["cost"]
            if any(minimum(child, remaining - 1) > allowance for _, child in groups):
                continue
            policy = {"action": action["id"], "branches": [
                {"observation": label, "next": reconstruct(child, remaining - 1, allowance)}
                for label, child in groups
            ]}
            choices.append((json.dumps(policy, sort_keys=True, separators=(",", ":")), policy))
        return min(choices, key=lambda row: row[0])[1]

    initial = ((tuple(sorted(data["initial"].items())), data["faults"]),)
    if not safe(data["initial"]):
        return {"feasible": False}
    cost = minimum(initial, data["max_rounds"])
    if cost >= infinity:
        return {"feasible": False}
    return {"feasible": True, "cost": cost,
            "policy": reconstruct(initial, data["max_rounds"], cost)}


PROMPT489 = '''
Синтезируй robust crash-consistent filesystem migration policy. Вход
{initial,target,sizes,disk,protected,invariants,faults:0..2,max_rounds:0..7,actions}.
File maps path->blob, безопасные ASCII POSIX paths; dirs неявны, blobs immutable.
Protected prefixes запрещают ЛЮБУЮ write под ними, даже прежнего значения.
Invariants — CNF, clause список [path,blob_or_null], хотя бы одно совпадение;
null=absence. Каждое durable state должно соблюдать все clauses, disk и запрет
file/ancestor collisions. Размер каждой физической копии считается отдельно.

<=8 reusable actions {id,kind:transaction|probe,guards:{path:blob_or_null},cost>=1}.
В начале round guards должны совпасть В КАЖДОМ ещё возможном durable state.
Probe имеет paths (отсортированные unique), не может crash и возвращает ТОЧНО
json.dumps([[path,actual_blob_or_null],...],separators=(",",":"),ensure_ascii=True).
Transaction имеет1..5 ops: write{path,blob_or_null} либо flush. В начале
volatile=durable, allocation=0. Write меняет volatile, null удаляет; nonnull
резервирует size(blob) НОВЫХ bytes даже при прежнем значении. Старые версии и
промежуточные allocations не освобождаются до flush; durable_size+allocation
обязательно<=disk после каждого write. Volatile namespace тоже без ancestor
collision после каждого write. Flush атомарно заменяет durable=volatile и
сбрасывает allocation; проверяет durable invariants. В конце transaction есть
НЕЯВНЫЙ flush, даже если последний op уже flush.

После КАЖДОГО op, включая flush и неявный final flush, adversary может crash,
если оставшийся faults>0: volatile изменения после последнего flush теряются,
durable сохраняется, faults уменьшается на1, action немедленно заканчивается
с observation "lost". Crash после final flush моделирует потерянный ack после
успешного коммита. Без crash action выдаёт "ack", faults не уменьшается.
В каждом round adversary может выбрать отсутствие crash; policy должна
закончить во ВСЕХ допустимых histories. Политика НЕ видит точку crash, число
оставшихся faults или фактическое дерево; знает только ack/lost/probe output.
Один lost объединяет несколько возможных durable states. Не допускается
читать эту скрытую карту напрямую. Каждый action стоит cost при любом исходе,
занимает1 round; action можно повторять. Termination: все возможные trees
ТОЧНО target; тогда остановка немедленная. Max_rounds ограничивает все ветви,
поэтому reusable actions и probes не делают search бесконечным.

Минимизируй worst-case SUM action costs, затем canonical JSON всей policy
(sort_keys=True,separators=(",",":"),ensure_ascii=True). Для lex tie нельзя
требовать локально минимальную cost каждого child: дорогая ветвь-сосед может
оставить другой ветви slack без изменения общего worst cost.
Policy {done:true} либо {action:id,branches:[{observation,next}]}; branches
по observation, только возможные. Вывод {feasible:false} либо
{feasible:true,cost:целое,policy}. Невыполнимый промежуточный prefix делает
transaction недопустимой, даже если normal final state корректно. Guards
проверяют durable; volatile промежуточные состояния policy не наблюдает.
'''


def solve494(data):
    from itertools import product

    variables = sorted(data["variables"])

    def equal(a, b):
        if type(a) is not type(b):
            return False
        if isinstance(a, list):
            return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b, strict=True))
        return a == b

    best = None
    for indices in product(*(range(len(data["variables"][n])) for n in variables)):
        chosen = {n: data["variables"][n][i] for n, i in zip(variables, indices, strict=True)}
        steps, trace = [0], []
        # Thunk: [status, expression, lexical environment, cached(value,origins)].
        env = {n: [2, None, None, (option["value"], frozenset({"var:" + n}))] for n, option in chosen.items()}
        for name, expr in data["definitions"].items():
            env[name] = [0, expr, env, None]

        def charge(steps=steps):
            steps[0] += 1
            if steps[0] > data["fuel"]:
                raise ValueError("fuel")

        def force(thunk):
            if thunk[0] == 2:
                return thunk[3]
            if thunk[0] == 1:
                raise ValueError("blackhole")
            charge()
            thunk[0] = 1
            thunk[3] = evaluate(thunk[1], thunk[2])
            thunk[0] = 2
            return thunk[3]

        def evaluate(expr, scope, trace=trace):
            charge()
            op = expr["op"]
            if op == "lit":
                return expr["value"], frozenset()
            if op == "var":
                return force(scope[expr["name"]])
            if op == "lambda":
                return ("closure", expr["params"], expr["body"], dict(scope)), frozenset()
            if op == "letrec":
                local = dict(scope)
                for name, child in expr["bindings"].items():
                    local[name] = [0, child, local, None]
                return evaluate(expr["body"], local)
            if op == "call":
                function, origins = evaluate(expr["fn"], scope)
                if type(function) is not tuple or function[0] != "closure" or len(function[1]) != len(expr["args"]):
                    raise ValueError("call")
                local = dict(function[3])
                for name, child in zip(function[1], expr["args"], strict=True):
                    local[name] = [0, child, scope, None]
                value, sources = evaluate(function[2], local)
                return value, origins | sources
            if op == "if":
                condition, origins = evaluate(expr["cond"], scope)
                if type(condition) is not bool:
                    raise ValueError("condition")
                value, sources = evaluate(expr["then"] if condition else expr["else"], scope)
                return value, origins | sources
            if op == "mark":
                trace.append(expr["label"])
                return evaluate(expr["value"], scope)
            if op == "count":
                return trace.count(expr["label"]), frozenset()
            evaluated = [evaluate(arg, scope) for arg in expr["args"]]
            values = [item[0] for item in evaluated]
            sources = frozenset().union(*(item[1] for item in evaluated))
            arity = {"sub": 2, "le": 2, "eq": 2, "head": 1, "tail": 1, "length": 1}
            if op in arity and len(values) != arity[op]:
                raise ValueError("arity")
            if op == "list":
                if any(type(v) is tuple for v in values):
                    raise ValueError("closure in list")
                return values, sources
            if op == "eq":
                if any(type(v) is tuple for v in values):
                    raise ValueError("closure equality")
                return equal(values[0], values[1]), sources
            if op in ("add", "mul", "sub", "le"):
                if any(type(v) is not int for v in values):
                    raise ValueError("integer")
                if op == "le":
                    return values[0] <= values[1], sources
                if op == "sub":
                    return values[0] - values[1], sources
                result = 0 if op == "add" else 1
                for value in values:
                    result = result + value if op == "add" else result * value
                return result, sources
            if op in ("head", "tail", "length"):
                if type(values[0]) is not list or (op != "length" and not values[0]):
                    raise ValueError("list")
                value = len(values[0]) if op == "length" else (values[0][0] if op == "head" else values[0][1:])
                return value, sources
            raise ValueError("operator")

        try:
            answers = []
            for query in data["queries"]:
                value, sources = evaluate(query["expr"], env)
                if type(value) is tuple:
                    raise ValueError("function output")
                answers.append({"id": query["id"], "value": value, "provenance": sorted(sources)})
                env = {**env, "result:" + query["id"]: [2, None, None, (value, sources)]}
            if any(evaluate(check, env)[0] is not True for check in data["checks"]):
                continue
        except (KeyError, ValueError, RecursionError):
            continue
        cost = sum(x["cost"] for x in chosen.values())
        changed = sum(i != data["baseline"][name] for name, i in zip(variables, indices, strict=True))
        key = cost, changed, steps[0], indices
        if best is None or key < best[0]:
            best = (key, {"feasible": True, "cost": cost, "changed": changed,
                          "choices": dict(zip(variables, indices, strict=True)), "steps": steps[0],
                          "results": answers, "trace": trace})
    return best[1] if best else {"feasible": False}


PROMPT494 = '''
Реши глобальную конфигурацию, исполнив scoped call-by-need язык с recursive
closures и наблюдаемым demand trace. Вход {variables,baseline,definitions,
queries,checks,fuel:1..1500}. <=6 variables,1..3 options {value,cost>=0};
baseline index каждой. Values — int,bool,string,null или вложенные lists этих
типов (без floats/objects). Definitions — name->expression, <=16 simultaneous
recursive lazy bindings. Их имена не пересекаются с variables и не начинаются
result:. Queries [{id,expr}] выполняются ПО ПОРЯДКУ в одном общем memo session;
после query его результат доступен как binding result:ID. IDs уникальны,
новые result bindings видны только последующим top-level evaluations; первоначальные definitions не имеют их в lexical environment. Lexical
closure сохраняет свой environment на момент создания. В конце checks также
по порядку, каждый обязан дать именно true. Все expressions используют язык:

lit{value}; var{name}; lambda{params:[unique name],body};
letrec{bindings:{name:expr},body}; call{fn,args:[expr]}; if{cond,then,else};
mark{label,value:expr}; count{label}; остальные {op,args:[expr]}:
add/mul(any arity; empty0/1), sub/le/eq(2), list(any), head/tail/length(1).
Все strict args вычисляются слева направо. add/mul/sub/le требуют integer,
bool не integer. eq — строго typed equality включая рекурсивные lists;
closures сравнивать нельзя. head/tail требуют непустой list; length — любой
list. list нельзя содержать closure. if cond строго bool, вычисляет только
выбранную ветвь. Lambda не вычисляет body, захватывает lexical environment.
Call сначала вычисляет fn; требует closure и точное число args. Каждый arg
становится lazy thunk в CALLER environment. Body исполняется в новом lexical
frame поверх CAPTURED environment closure с params, затеняющими имена.
Letrec создаёт одновременно все thunks в новом рекурсивном environment; bindings
ленивы, могут взаимно ссылаться, shadowing lexical. Каждый thunk вычисляется
не более одного раза и хранит value И provenance; повторное force активного
thunk = blackhole error. Рекурсивные функции допустимы: closure thunk уже готов
до call. Unused erroneous arg/binding допустим. Top-level variables сразу готовы.

Fuel: +1 за КАЖДОЕ evaluation AST node, включая var и count; ещё +1 при ПЕРВОМ
force неготового thunk, перед вычислением его expression. Memo hit не тратит
дополнительный force fuel (сам var node всё равно стоит1). Budget общий на все
queries/checks; step>fuel => invalid assignment. mark сначала добавляет label
к trace, потом вычисляет value. count возвращает текущее число такого label,
не добавляет trace. Memo hit повторно mark не исполняет. Undefined name,
blackhole, type/arity error, nontrue check и closure как query result делают
assignment invalid. Runtime stackdepth корректных assignment<=150.

Provenance: selected variable value имеет {var:NAME}; literal/lambda/count
пустое множество. Strict ops объединяют sources вычисленных args; if добавляет
cond+выбранную ветвь; call добавляет sources fn+body, НЕ неиспользованных args;
mark просто сохраняет sources своего value. letrec передаёт sources body.
Closure не добавляет sources всех захваченных, но реально прочитанные значения
добавятся при body evaluation. Query result сохраняет provenance в result:ID.

Оптимизируй (sum option costs,число отклонений от baseline,полный spent fuel,
индексы options по variable names). Вывод {feasible:false} либо {feasible:true,
cost,changed,choices:{name:index},steps,results:[{id,value,provenance:[source]}],
trace:[label]}. Results в query order, provenance sorted, trace execution order.
Checks влияют на steps/trace, но не добавляют sources к предыдущим results.
Нельзя переинициализировать memo между queries или применять dynamic scoping.
'''


def solve499(data):
    from itertools import combinations, product

    places = sorted(data["locations"])
    index = {p: i for i, p in enumerate(places)}
    objects = sorted(data["objects"])
    sizes = {obj: value["size"] for obj, value in data["objects"].items()}
    devices = [data["locations"][p]["device"] for p in places]
    scratch = [i for i, p in enumerate(places) if data["locations"][p]["scratch"]]
    empty = ("", 0, False)
    initial = tuple((data["initial"][p], sizes[data["initial"][p]], True) if p in data["initial"] else empty for p in places)
    wanted = {value["target"]: obj for obj, value in data["objects"].items()}
    target = tuple((wanted[p], sizes[wanted[p]], True) if p in wanted else empty for p in places)
    layer = {initial: [(0, 0, sum(c[1] for c in initial), ())]}
    for time in range(len(data["network"]) + 1):
        if target in layer:
            net, io, peak, plan = min(layer[target])
            return {"feasible": True, "rounds": time, "network": net, "io": io,
                    "peak": peak, "plan": [list(group) for group in plan]}
        if time == len(data["network"]):
            break
        following = {}
        unavailable = set(data["closed"][time])
        for state, labels in layer.items():
            actions = []
            # An action is (code, reads, writes, patch, network, io, cpu, allocations).
            for source, (obj, _, verified) in enumerate(state):
                if not verified or places[source] in unavailable:
                    continue
                for destination in scratch:
                    old = state[destination]
                    if places[destination] in unavailable or old[2] or (old[0] and old[0] != obj):
                        continue
                    for amount in range(1, sizes[obj] - old[1] + 1):
                        actions.append((f"copy:{places[source]}:{places[destination]}:{amount}",
                                        {source, destination}, {destination},
                                        {destination: (obj, old[1] + amount, False)},
                                        amount if devices[source] != devices[destination] else 0,
                                        amount, 0, {devices[destination]: amount}))
            for place in scratch:
                obj, amount, verified = state[place]
                if not obj or places[place] in unavailable:
                    continue
                actions.append(("drop:" + places[place], {place}, {place}, {place: empty}, 0, 0, 0, {}))
                if not verified and amount == sizes[obj]:
                    actions.append(("verify:" + places[place], {place}, {place},
                                    {place: (obj, amount, True)}, 0, 0, 1, {}))
            for group in sorted(data["groups"], key=lambda g: g["id"]):
                members = sorted(group["objects"])
                sources = [[i for i in scratch if state[i][0] == obj and state[i][2]
                            and devices[i] == data["locations"][data["objects"][obj]["target"]]["device"]]
                           for obj in members]
                for selected in product(*sources):
                    targets = [index[data["objects"][obj]["target"]] for obj in members]
                    touched = set(selected) | set(targets)
                    if any(places[i] in unavailable for i in touched):
                        continue
                    patch = dict.fromkeys(selected, empty)
                    patch.update({i: (obj, sizes[obj], True) for obj, i in zip(members, targets, strict=True)})
                    code = "publish:" + group["id"] + ":" + ",".join(places[i] for i in selected)
                    actions.append((code, touched, touched, patch, 0, 0, 0, {}))
            used = {dev: 0 for dev in data["capacity"]}
            for i, cell in enumerate(state):
                used[devices[i]] += cell[1]
            for count in range(min(data["slots"], len(actions)) + 1):
                for selected in combinations(actions, count):
                    if any(a[2] & (b[1] | b[2]) or b[2] & a[1] for a, b in combinations(selected, 2)):
                        continue
                    network = sum(a[4] for a in selected)
                    io = sum(a[5] for a in selected)
                    if network > data["network"][time] or io > data["io"][time] or sum(a[6] for a in selected) > data["cpu"][time]:
                        continue
                    allocated = {dev: sum(a[7].get(dev, 0) for a in selected) for dev in used}
                    if any(used[dev] + allocated[dev] > data["capacity"][dev] for dev in used):
                        continue
                    changed = list(state)
                    for action in selected:
                        for i, cell in action[3].items():
                            changed[i] = cell
                    if any(not any(c[0] == obj and c[2] for c in changed) for obj in objects):
                        continue
                    changed = tuple(changed)
                    peak = sum(used.values()) + sum(allocated.values())
                    code = tuple(sorted(a[0] for a in selected))
                    bucket = following.setdefault(changed, [])
                    for old_net, old_io, old_peak, plan in labels:
                        candidate = old_net + network, old_io + io, max(old_peak, peak), plan + (code,)
                        if bucket and candidate[:2] > bucket[0][:2]:
                            continue
                        if bucket and candidate[:2] < bucket[0][:2]:
                            bucket.clear()
                        if any(old[2] <= candidate[2] and old[3] <= candidate[3] for old in bucket):
                            continue
                        bucket[:] = [old for old in bucket if not (candidate[2] <= old[2] and candidate[3] <= old[3])]
                        bucket.append(candidate)
        layer = following
    return {"feasible": False}


PROMPT499 = '''
Построй кратчайший recoverable publish plan: это chunked replication,
verification и atomic cohort cutover, а не перестановка имён.
Вход {locations,objects,initial,groups,capacity,network,io,cpu,closed,slots}.
2..3 objects {id:{size:1..3,target:location}}, targets уникальны и не scratch.
Locations {name:{device,scratch:bool}}, <=3 scratch, остальные targets;
initial даёт ровно одну FULL VERIFIED копию каждого object в nonscratch
location, перестановка target locations. Scratch initially empty. Names/ids
ASCII alphanumeric/underscore (без colon/comma), sizes integer bytes.
Physical state каждой location: empty либо (object,received_bytes,verified).
Initial device occupancy<=capacity. H=len(network)=1..7; io,cpu,closed длиныH.

Round выбирает0..slots (1..2) actions, всё проверяется на общем START snapshot.
copy:SRC:DST:N: SRC full verified; DST scratch, empty либо partial/unverified
копия ТОГО ЖЕ object; 1<=N<=remaining bytes. Добавляет N bytes, verified=false
ДАЖЕ при завершении. Источник остаётся. IO cost=N всегда; network=N только
между разными devices, иначе0. Нельзя копировать из partial/unverified.
verify:SLOT: scratch full unverified -> verified, cpu1; не даёт network/IO.
drop:SLOT: непустой scratch -> empty; нельзя явно удалять nonscratch.
publish:GROUP:S1,S2,...: group задаёт непустой unique список object ids.
Для каждого object в АЛФАВИТНОМ порядке выбери full verified SCRATCH replica
на ТОМ ЖЕ device, что его target; перечисли её location в action code.
Атомарно перенеси выбранные replicas в их targets, scratch освободи, прежние
файлы targets уничтожь. Группа может затронуть несколько identities, поэтому
циклический cutover возможен. IO/network/cpu0, один action slot на всю группу.
Groups можно использовать неоднократно. В каждом POST-COMMIT state у КАЖДОГО
object должна оставаться хотя бы ОДНА FULL VERIFIED replica: это точное
условие восстановления после crash на границе rounds. Partial/unverified
копия не считается. Публикация атомарна, промежуточного удаления всех copies нет.

Read/write sets: copy reads SRC,DST, writes DST; verify/drop read+write SLOT;
publish read+write все выбранные scratch и targets. Совместимость пары:
W1∩(R2∪W2)=W2∩(R1∪W1)=empty. Shared read source разрешён. Нельзя использовать
результат copy/verify из того же round. Нельзя трогать closed[t] location ни
read, ни write; закрытые копии продолжают существовать и считаются recoverable.
Sum costs<=network[t],io[t],cpu[t]; по каждому device
start_received_bytes+incoming_copy_bytes<=capacity. Drop/publish освобождают
bytes только на commit, не субсидируют copy staging в том же round.
Global physical peak=max по rounds(sum start bytes+sum copy bytes), включая
initial. Verified metadata не аллоцирует bytes.

Goal: каждый target содержит нужный verified object, все scratch пусты.
Остановиться сразу. Оптимизируй (rounds,total network,total io,physical peak,
plan), plan список rounds, round список action CODE strings lex sorted;
lex comparison shorter-prefix-first. Пустой round разрешён для calendar wait.
Вывод {feasible:false} либо {feasible:true,rounds,network,io,peak,plan}.
Не замени verified availability на наличие любых bytes, не публикуй свежую
копию в том же round и не освобождай COW storage до commit.
'''


def register(number, slug, name, parent, prompt, solver, anchors, values, artifact=False):
    SOLVERS[number], ANCHORS[number] = solver, anchors
    if artifact:
        ARTIFACT_DATA[number] = values
        files = {"manifest.json": dumps([f"instances/{i:02}.json" for i in range(len(values))])}
        files.update({f"instances/{i:02}.json": dumps(v) for i, v in enumerate(values)})
        task = artifact_task(number=number, slug=slug, name=name, parent=parent,
                             prompt=prompt + '\nРеши каждый независимый экземпляр из manifest.json. '
                             'answer.json={results:[результаты в порядке manifest]}. Исходники не меняй.\n',
                             files=files, expected={"results": [solver(v) for v in values]},
                             tags=("systems", "hardening-v4", "hardening-v5"))
    else:
        CASES[number] = list(anchors) + [(v, solver(v)) for v in values]
        gold = textwrap.dedent(inspect.getsource(solver)) + '\nimport json,sys\nfor line in sys.stdin:\n'
        gold += f'    print(json.dumps({solver.__name__}(json.loads(line))))\n'
        task = program_task(number=number, slug=slug, name=name, parent=parent, prompt=prompt,
                            cases=CASES[number], gold=gold, tags=("systems", "hardening-v4", "hardening-v5"))
    TASKS.append(task)


def control_case(seed=0):
    def action(name, rewards, probabilities, energy=1):
        return {"id": name, "energy": energy, "outcomes": [
            [{"label": "yes", "reward": rewards[i], "p": [probabilities[i], 4]},
             {"label": "no", "reward": 0, "p": [4-probabilities[i], 4]}] for i in range(2)]}
    return {"horizon": 3 + (seed == 9), "energy": 2 + (seed % 4 == 0),
            "prior": [[1, 2], [1, 2]], "transition": [[[3, 4], [1, 4]], [[1, 4], [3, 4]]],
            "actions": [action("a", [8, 5], [3, 1]), action("b", [5, 9], [1, 3]),
                        action("safe", [3, 3], [4, 4]), action("probe", [0, 0], [4, 0], 0)],
            "threshold": 5 + seed % 4, "required_probability": [[1, 2], [2, 3], [3, 4]][seed % 3]}


EMPTY487 = {"horizon": 1, "energy": 0, "prior": [[1, 2], [1, 2]],
            "transition": [[[1, 1], [0, 1]], [[0, 1], [1, 1]]], "actions": [],
            "threshold": 0, "required_probability": [1, 1]}
SAFE487 = {**EMPTY487, "energy": 1, "threshold": 3, "required_probability": [3, 4], "actions": [
    {"id": "safe", "energy": 1, "outcomes": [[{"label": "ok", "reward": 3, "p": [1, 1]}]] * 2},
    {"id": "risky", "energy": 1, "outcomes": [[{"label": "ok", "reward": 10, "p": [1, 2]},
                                                {"label": "fail", "reward": 0, "p": [1, 2]}]] * 2},
]}
register(487, "adaptive_retry_policy", "Control delayed hidden-regime risk without randomized policies", 359,
         PROMPT487, solve487,
         [(EMPTY487, {"feasible": True, "reward": [0, 1], "success": [1, 1],
                      "policy": {"action": "wait", "branches": [{"observation": "_", "next": {"done": True}}]}}),
          (SAFE487, {"feasible": True, "reward": [3, 1], "success": [1, 1],
                     "policy": {"action": "safe", "branches": [{"observation": "_", "next": {"done": True}}]}})],
         [control_case(i) for i in range(10)])


def write(path, blob):
    return {"op": "write", "path": path, "blob": blob}


def transaction(name, ops, cost=1, guards=None):
    return {"id": name, "kind": "transaction", "guards": guards or {}, "cost": cost, "ops": ops}


def crash_case(seed=0):
    return {"initial": {"app": "old", "index": "i0"}, "target": {"app": "new", "index": "i1"},
            "sizes": {"old": 2, "new": 3, "i0": 1, "i1": 2}, "disk": 6 + seed % 4,
            "protected": ["vault"], "invariants": [
                [["app", "old"], ["app", "new"]], [["index", "i0"], ["index", "i1"]],
                [["app", "new"], ["index", "i0"]]],
            "faults": 1 + (seed >= 4), "max_rounds": 5 + seed % 3,
            "actions": [
                transaction("upgrade", [write("app", "new"), {"op": "flush"}, write("index", "i1")], 3),
                transaction("app", [write("app", "new")], 2, {"app": "old"}),
                transaction("index", [write("index", "i1")], 2, {"app": "new"}),
                transaction("bundle", [write("app", "new"), write("index", "i1")], 4),
                transaction("unsafe", [write("index", "i1"), {"op": "flush"}, write("app", "new")], 1),
                {"id": "probe", "kind": "probe", "cost": 1, "guards": {}, "paths": ["app", "index"]},
            ]}


TINY489 = {"initial": {"x": "a"}, "target": {"x": "b"}, "sizes": {"a": 1, "b": 1},
           "disk": 2, "protected": [], "invariants": [], "faults": 0, "max_rounds": 1,
           "actions": [transaction("write", [write("x", "b")], 3)]}
register(489, "atomic_tree_migration", "Recover partially observed durable transactions after crashes", 360,
         PROMPT489, solve489,
         [(TINY489, {"feasible": True, "cost": 3,
                     "policy": {"action": "write", "branches": [{"observation": "ack", "next": {"done": True}}]}}),
          ({**TINY489, "disk": 1}, {"feasible": False})], [crash_case(i) for i in range(6)], artifact=True)


def lit(value):
    return {"op": "lit", "value": value}


def var(name):
    return {"op": "var", "name": name}


def operation(name, *args):
    return {"op": name, "args": list(args)}


def call(fn, *args):
    return {"op": "call", "fn": fn, "args": list(args)}


def closure(params, body):
    return {"op": "lambda", "params": params, "body": body}


def interpreter_case(seed=0):
    recurse = closure(["n"], {"op": "mark", "label": "step", "value": {
        "op": "if", "cond": operation("le", var("n"), lit(0)), "then": lit(0),
        "else": operation("add", var("n"), call(var("sum"), operation("sub", var("n"), lit(1))))}})
    return {
        "variables": {"depth": [{"value": n, "cost": (2*n+seed) % 5} for n in (1, 2, 3)],
                      "offset": [{"value": n, "cost": (n+seed) % 3} for n in (0, 1, 2)],
                      "tweak": [{"value": n, "cost": n % 2} for n in (1, 2)]},
        "baseline": {"depth": seed % 3, "offset": 0, "tweak": 0},
        "definitions": {"sum": recurse, "memo": {"op": "mark", "label": "shared", "value": call(var("sum"), var("depth"))},
                        "factory": closure(["x"], closure(["y"], operation("add", var("x"), var("y")))),
                        "fn": call(var("factory"), var("offset"))},
        "queries": [
            {"id": "a", "expr": operation("add", var("memo"), var("memo"))},
            {"id": "b", "expr": {"op": "letrec", "bindings": {"offset": lit(100)},
                                    "body": call(var("fn"), var("tweak"))}},
            {"id": "c", "expr": call(closure(["unused"], var("result:a")),
                                        {"op": "letrec", "bindings": {"bad": var("bad")}, "body": var("bad")})},
            {"id": "d", "expr": operation("list", var("memo"), {"op": "count", "label": "shared"})},
        ],
        "checks": [operation("le", lit(8 + seed % 5), operation("add", var("result:a"), var("result:b"))),
                   operation("eq", {"op": "count", "label": "shared"}, lit(1)),
                   operation("eq", {"op": "count", "label": "step"}, operation("add", var("depth"), lit(1)))],
        "fuel": 180 + seed,
    }


TINY494 = {"variables": {}, "baseline": {}, "definitions": {},
           "queries": [{"id": "q", "expr": lit(7)}], "checks": [], "fuel": 10}
MEMO494 = {**TINY494, "definitions": {"x": {"op": "mark", "label": "once", "value": lit(2)}},
           "queries": [{"id": "a", "expr": var("x")}, {"id": "b", "expr": var("x")}], "fuel": 10}
register(494, "config_provenance_interpolation", "Resolve shared lazy recursive configuration closures", 363,
         PROMPT494, solve494,
         [(TINY494, {"feasible": True, "cost": 0, "changed": 0, "choices": {}, "steps": 1,
                     "results": [{"id": "q", "value": 7, "provenance": []}], "trace": []}),
          (MEMO494, {"feasible": True, "cost": 0, "changed": 0, "choices": {}, "steps": 5,
                     "results": [{"id": "a", "value": 2, "provenance": []},
                                 {"id": "b", "value": 2, "provenance": []}], "trace": ["once"]})],
         [interpreter_case(i) for i in range(10)])


def replication_case(seed=0):
    distinct = seed % 2
    size = 1 + (seed % 3 == 0)
    return {"locations": {"a": {"device": "d0", "scratch": False},
                           "b": {"device": "d1" if distinct else "d0", "scratch": False},
                           "s0": {"device": "d0", "scratch": True},
                           "s1": {"device": "d1" if distinct else "d0", "scratch": True}},
            "objects": {"A": {"size": size, "target": "b"}, "B": {"size": 1, "target": "a"}},
            "initial": {"a": "A", "b": "B"},
            "groups": [{"id": "both", "objects": ["A", "B"]},
                       {"id": "a", "objects": ["A"]}, {"id": "b", "objects": ["B"]}],
            "capacity": {"d0": 2*(size+1), "d1": 2*(size+1)},
            "network": [1 + seed % 2] * 5, "io": [1 + seed % 3] * 5,
            "cpu": [1 + seed % 2] * 5, "closed": [["s0"] if t == seed % 4 and seed > 3 else [] for t in range(5)],
            "slots": 2}


TINY499 = {"locations": {p: {"device": "d", "scratch": p.startswith("s")} for p in ["a", "b", "s0", "s1"]},
           "objects": {"A": {"size": 1, "target": "b"}, "B": {"size": 1, "target": "a"}},
           "initial": {"a": "A", "b": "B"}, "groups": [{"id": "g", "objects": ["A", "B"]}],
           "capacity": {"d": 4}, "network": [0]*3, "io": [2]*3, "cpu": [2]*3,
           "closed": [[], [], []], "slots": 2}
register(499, "minimal_spill_rename", "Schedule chunk verification and recoverable cohort publication", 365,
         PROMPT499, solve499,
         [(TINY499, {"feasible": True, "rounds": 3, "network": 0, "io": 2, "peak": 4,
                     "plan": [["copy:a:s0:1", "copy:b:s1:1"], ["verify:s0", "verify:s1"], ["publish:g:s0,s1"]]}),
          ({**TINY499, "network": [0]*2, "io": [2]*2, "cpu": [2]*2, "closed": [[], []]}, {"feasible": False})],
         [replication_case(i) for i in range(10)])
