"""Wave 20 systems v4: bounded exact planning, causal repair and binding analysis.

Each gold solver is standalone; only its function source enters a program.
Fixtures and answer generation remain outside the evaluated workspace.
"""

from __future__ import annotations

import inspect
import json
import random
import textwrap

from .common import artifact_task, dumps, program_task

TASKS = []
CASES = {}
SOLVERS = {}
ANCHORS = {}
ARTIFACT_DATA = {}


def program(number, slug, name, parent, prompt, solver, anchors, cases):
    all_cases = list(anchors) + [(value, solver(value)) for value in cases]
    SOLVERS[number], ANCHORS[number], CASES[number] = solver, anchors, all_cases
    gold = textwrap.dedent(inspect.getsource(solver))
    gold += (
        "\nimport json, sys\nfor line in sys.stdin:\n"
        f"    print(json.dumps({solver.__name__}(json.loads(line)), ensure_ascii=False))\n"
    )
    TASKS.append(program_task(
        number=number, slug=slug, name=name, parent=parent, prompt=prompt,
        cases=all_cases, gold=gold, tags=("systems", "hardening-v4"),
    ))


def artifact(number, slug, name, parent, prompt, solver, anchors, instances):
    SOLVERS[number], ANCHORS[number], ARTIFACT_DATA[number] = solver, anchors, instances
    files = {"manifest.json": dumps([f"instances/{i:02}.json" for i in range(len(instances))])}
    files.update({f"instances/{i:02}.json": dumps(value) for i, value in enumerate(instances)})
    TASKS.append(artifact_task(
        number=number, slug=slug, name=name, parent=parent,
        prompt=prompt + "\nПрочти manifest.json. Для каждого файла в его порядке реши "
        "НЕЗАВИСИМЫЙ экземпляр. answer.json={results:[результат каждого экземпляра]}. "
        "Исходные файлы не меняй.\n",
        files=files, expected={"results": [solver(value) for value in instances]},
        tags=("systems", "hardening-v4", "optimization"),
    ))


def optimal486(data):
    from functools import lru_cache
    from itertools import combinations

    jobs = sorted(data["jobs"], key=lambda j: j["id"])
    horizon = data["horizon"]
    # State of each job: next response, ready time, running-until; -1 = closed.
    initial = tuple((0, j["release"], 0) for j in jobs)

    @lru_cache(None)
    def search(t, tokens, blocked, states):
        states = list(states)
        reward = completed = 0
        if t and t % data["period"] == 0:
            tokens = min(data["capacity"], tokens + 1)
        for i, (attempt, _ready, end) in enumerate(states):
            if end != t or not end:
                continue
            response = jobs[i]["responses"][attempt]
            if response["result"] == "ok":
                reward += jobs[i]["reward"]
                completed += 1
                states[i] = (-1, 0, 0)
            elif response["result"] == "fatal":
                states[i] = (-1, 0, 0)
            else:
                blocked = max(blocked, t + response.get("freeze", 0))
                if attempt + 1 == len(jobs[i]["responses"]):
                    states[i] = (-1, 0, 0)
                else:
                    states[i] = (attempt + 1, t + response["delay"], 0)
        if t == horizon:
            return reward, completed, 0, ()
        running = sum(end > t for _, _, end in states)
        ready = [
            i for i, (attempt, when, end) in enumerate(states)
            if attempt >= 0 and end == 0 and when <= t and t >= blocked
            and t + jobs[i]["responses"][attempt]["duration"] <= jobs[i]["deadline"]
        ]
        best = None
        for count in range(min(tokens, data["slots"] - running, len(ready)) + 1):
            for chosen in combinations(ready, count):
                next_state = list(states)
                for i in chosen:
                    attempt, when, _ = states[i]
                    next_state[i] = (
                        attempt, when, t + jobs[i]["responses"][attempt]["duration"],
                    )
                sub = search(t + 1, tokens - count, blocked, tuple(next_state))
                plan = tuple((t, jobs[i]["id"]) for i in chosen) + sub[3]
                value = (reward + sub[0], completed + sub[1], count + sub[2], plan)
                if best is None or (-value[0], -value[1], value[2], value[3]) < (
                    -best[0], -best[1], best[2], best[3]
                ):
                    best = value
        return best

    result = search(0, data["capacity"], 0, initial)
    return {"reward": result[0], "completed": result[1], "attempts": result[2],
            "starts": [{"time": t, "id": name} for t, name in result[3]]}


PROMPT486 = """
Вместо жадного retry-dispatcher найди ГЛОБАЛЬНО оптимальное расписание.
Вход {horizon,capacity,period,slots,jobs}. horizon 1..15, capacity/slots 1..3,
period 1..5, <=5 jobs. Job: уникальный ASCII id, release>=0, deadline<=horizon,
reward>=0, responses (1..3): {duration:1..4,result:ok|retry|fatal,
delay:0..5,freeze:0..5}. release<=deadline. Все результаты заранее известны
планировщику: это детерминированная offline optimization, не adaptive policy.
Время целое. В t=0 bucket=capacity; в каждый положительный t кратный period
добавь один token до capacity. Затем обработай ВСЕ завершения; затем выбирай
ЛЮБОЕ подмножество готовых jobs для старта, включая пустое. При старте расходуется
1 token, worker занят duration; одновременно <=slots, у job <=1 attempt.
Первый ready=release. Retry делает next ready=end+delay этой response и общий
запрет новых стартов до max(старый запрет,end+freeze), даже на последней попытке.
Успех даёт reward и закрывает job; fatal/последний retry закрывают без награды.
В момент окончания freeze старт разрешён. Уже работающие jobs не замораживаются.
Старт допустим только если end<=deadline; end=deadline=horizon допустим.
Пропускать job или намеренно ждать разрешено. В horizon только завершения.
Оптимизируй (-sum_reward,-число_success,число_стартов,список_стартов).
Список стартов — пары (time,id), отсортированные по time, затем id; сравнение
лексикографическое, shorter prefix first. Zero reward success влияет на count.
Вывод ровно {reward,completed,attempts,starts:[{time,id},...]}. Пустые jobs допустимы.
Нужно учитывать цену раннего retry/freeze для остальных jobs, а не выбирать
локально максимальную награду или выполнять все готовые запросы.
"""


def bayesian487(data):
    from fractions import Fraction
    from functools import lru_cache

    jobs = sorted(data["jobs"], key=lambda j: j["id"])
    probes = sorted(data["probes"], key=lambda p: p["id"])
    prior = tuple(Fraction(*x) for x in data["prior"])

    @lru_cache(None)
    def search(t, energy, states, used, belief):
        if t == data["horizon"]:
            return Fraction(0), "stop"
        choices = [(Fraction(0), "stop")]
        choices.append((search(t + 1, energy, states, used, belief)[0], "wait"))
        for i, job in enumerate(jobs):
            attempt, ready = states[i]
            if attempt < 0 or attempt == len(job["attempts"]) or ready > t:
                continue
            action = job["attempts"][attempt]
            end = t + action["duration"]
            if end > min(job["deadline"], data["horizon"]) or action["energy"] > energy:
                continue
            likelihood = tuple(Fraction(*p) for p in action["p"])
            value = Fraction(0)
            for success in (False, True):
                weights = tuple(b * (p if success else 1 - p)
                                for b, p in zip(belief, likelihood, strict=True))
                probability = sum(weights)
                if not probability:
                    continue
                updated = tuple(x / probability for x in weights)
                state = list(states)
                state[i] = (-1, 0) if success else (attempt + 1, end + action["delay"])
                continuation = search(end, energy - action["energy"], tuple(state), used, updated)[0]
                value += probability * (continuation + (job["reward"] if success else 0))
            choices.append((value, "job:" + job["id"]))
        for i, probe in enumerate(probes):
            end = t + probe["duration"]
            if used >> i & 1 or end > data["horizon"] or probe["energy"] > energy:
                continue
            value = Fraction(0)
            for signal in range(len(probe["likelihood"][0])):
                weights = tuple(
                    b * Fraction(*row[signal])
                    for b, row in zip(belief, probe["likelihood"], strict=True)
                )
                probability = sum(weights)
                if probability:
                    updated = tuple(x / probability for x in weights)
                    value += probability * search(
                        end, energy - probe["energy"], states, used | 1 << i, updated,
                    )[0]
            choices.append((value, "probe:" + probe["id"]))
        order = {f"job:{j['id']}": i for i, j in enumerate(jobs)}
        order.update({f"probe:{p['id']}": len(jobs) + i for i, p in enumerate(probes)})
        order["wait"], order["stop"] = len(order), len(order) + 1
        return min(choices, key=lambda item: (-item[0], order[item[1]]))

    value, first = search(0, data["energy"],
                          tuple((0, j["release"]) for j in jobs), 0, prior)
    return {"value": [value.numerator, value.denominator], "first": first}


PROMPT487 = """
Найди точную байесовскую adaptive retry-policy при ОБЩЕМ скрытом режиме.
Вход {horizon,energy,prior,jobs,probes}. horizon1..10, energy0..8,
2..3 скрытых режима Z; prior — положительные рациональные [num,den], сумма1.
Z выбирается ОДИН раз и никогда не меняется. Условно при Z результаты ВСЕХ
attempt/probe независимы. Безусловно они коррелируют. Наблюдение провала/успеха
другого job тоже меняет posterior Z. Режим непосредственно не наблюдается.
<=4 jobs: {id,reward:0..20,release,deadline,attempts:[{duration:1..3,
energy:0..3,delay:0..3,p:[по режимам рациональные вероятности успеха]}]}.
<=2 attempts/job, 0<=release<=deadline<=horizon. <=2 probes:
{id,duration:1..3,energy:0..3,likelihood:[по режимам [вероятности сигналов]]}.
У probe 2..3 нумерованных с0 сигналов; каждая строка likelihood суммируется в1.
Каждый probe можно использовать только один раз. Все дроби заданы точно.
Один worker, nonpreemptive. В t=0 ready=release. Выбор: stop, wait1,
следующий attempt готового job, либо неиспользованный probe. Нужно достаточно
energy; расход не возвращается. Завершение<=horizon и для job<=deadline.
Успех закрывает job с reward, провал расходует attempt и ready=end+delay
этой попытки; exhausted job недоступен. Probe даёт только информацию.
Результат известен при завершении, далее можно адаптировать действия ко ВСЕМ
наблюдениям. Сигналы нулевой вероятности не являются ветвями policy.
Максимизируй ожидаемую сумму reward. При ТОЧНОМ равенстве: job по id,
потом probe по id, потом wait, потом stop. В horizon допустим только stop.
Вывод {value:[несократимый числитель,положительный знаменатель],
first:"job:ID"|"probe:ID"|"wait"|"stop"}. Гарантирована указанная схема;
prior нельзя независимо переинициализировать перед каждым новым job.
"""


def archive488(data):
    import re
    import unicodedata
    from urllib.parse import unquote_to_bytes

    def normalize(raw, base=()):
        if re.search(r"%(?![0-9A-Fa-f]{2})", raw):
            return None
        try:
            raw = unquote_to_bytes(raw).decode("utf-8")
        except UnicodeError:
            return None
        raw = unicodedata.normalize("NFC", raw).replace("\\", "/")
        if raw.startswith("/") or "\x00" in raw or ":" in raw:
            return None
        parts = list(base)
        for segment in raw.split("/"):
            if segment in ("", "."):
                continue
            if segment == "..":
                if not parts:
                    return None
                parts.pop()
                continue
            segment = unicodedata.normalize("NFC", segment.casefold()).rstrip(" .")
            if not segment or re.fullmatch(r"(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", segment):
                return None
            parts.append(segment)
        return "/".join(parts) if parts else None

    entries = sorted(data["entries"], key=lambda e: e["id"])
    paths = [normalize(e["path"]) for e in entries]
    targets = [
        normalize(e["target"], tuple(paths[i].split("/")[:-1]) if e["type"] == "sym" else ())
        if e["type"] != "file" and paths[i] is not None else None
        for i, e in enumerate(entries)
    ]
    best = None
    for mask in range(1 << len(entries)):
        chosen = [i for i in range(len(entries)) if mask >> i & 1]
        ids = tuple(entries[i]["id"] for i in chosen)
        if not set(data["required"]) <= set(ids):
            continue
        if any(paths[i] is None or (entries[i]["type"] != "file" and targets[i] is None) for i in chosen):
            continue
        names = [paths[i] for i in chosen]
        if len(set(names)) != len(names):
            continue
        if any(a != b and b.startswith(a + "/") for a in names for b in names):
            continue
        by_path = {paths[i]: i for i in chosen}
        resolved = {}

        def resolve(i, trail, by_path=by_path):
            if i in trail:
                return None
            if entries[i]["type"] == "file":
                return i
            destination = targets[i]
            # Exact selected namespace only; symlinks cannot act as directories.
            if destination not in by_path:
                return None
            return resolve(by_path[destination], trail | {i})

        for i in chosen:
            result = resolve(i, set())
            if result is None:
                break
            resolved[i] = result
        else:
            actual = sum(entries[i]["size"] for i in chosen if entries[i]["type"] == "file")
            if actual > data["disk"]:
                continue
            score = sum(entries[i]["weight"] for i in chosen)
            key = (-score, -len(chosen), actual, ids)
            if best is None or key < best[0]:
                best = (key, {"feasible": True, "weight": score, "disk": actual,
                             "selected": list(ids),
                             "resolved": [{"id": entries[i]["id"], "path": paths[i],
                                           "file": entries[resolved[i]]["id"]} for i in chosen]})
    return best[1] if best else {"feasible": False}


PROMPT488 = r"""
Выбери глобально оптимальный безопасный ПОДНАБОР архива, а не принимай первый
встретившийся путь. Вход {disk:0..100,required:[id],entries:[...]}, <=16 entries.
У каждого уникальный ASCII id, path, type=file|hard|sym, weight>=0;
file имеет size>=0, link имеет target. required — обязательные id.
Нормализация: проверить каждую % на ровно2 hex digits; percent-decode ОДИН раз
в bytes, строго UTF8; NFC, backslash->slash; запретить leading slash, NUL,
двоеточие в любой позиции. Разделить по "/"; пропустить пустые и "."; ".."
удаляет предыдущий segment или делает путь unsafe при выходе за root.
Обычный segment: NFC(casefold(segment)), затем rstrip(" ."); пустой запрещён.
Запрещены con/prn/aux/nul/com1..9/lpt1..9, также с суффиксом начинающимся ".".
Итоговый пустой путь запрещён. Начальная база для entry.path и hard.target
пуста; для sym.target — нормализованные segments РОДИТЕЛЯ entry.path.
Leading slash запрещён даже у sym.target. Переходы ".." применяются к базе.
Повторно %-decode результат не надо. Строки сравнивай Unicode codepoint order.

В выбранном поднаборе все пути/targets безопасны; нет одинаковых canonical
paths и file/ancestor collisions между ЛЮБЫМИ типами: ссылки не являются
директориями. Каждый link.target должен ТОЧНО совпасть с canonical path
выбранной entry; цепь hard/sym обязана закончиться выбранным file, без цикла.
Forward links разрешены. Отброшенный colliding file не удовлетворяет ссылку.
Disk — сумма size выбранных files, links места не занимают, даже hard.
Оптимизируй (-sum_weight,-число_selected,disk,sorted_selected_ids).
Если обязательные entries нельзя включить одновременно, {feasible:false}.
Иначе {feasible:true,weight,disk,selected:[id],resolved:[{id,path,file}]};
оба массива по id, file — id конечного file, в том числе у самого file.
Пустой поднабор допустим, если required пуст. Все schema-поля валидны, но
path/target могут быть unsafe. Обход по входному порядку не задаёт приоритета.
"""


def contingent489(data):
    import json
    from fractions import Fraction
    from functools import lru_cache

    actions = sorted(data["actions"], key=lambda a: a["id"])
    worlds = tuple(sorted(data["worlds"]))
    sizes = data["sizes"]

    def safe(tree):
        names = list(tree)
        return (
            not any(b.startswith(a + "/") for a in names for b in names if a != b)
            and all(tree.get(path) == data["initial"].get(path) for path in data["protected"])
            and all(any(tree.get(path) == blob for path, blob in clause)
                    for clause in data["invariants"])
            and sum(sizes[blob] for blob in tree.values()) <= data["disk"]
        )

    @lru_cache(None)
    def solve(state, possible, used, remaining):
        tree = dict(state)
        if tree == data["target"]:
            return 0, 0, {"done": True}
        if remaining == 0:
            return None
        best = None
        for i, action in enumerate(actions):
            if used >> i & 1 or any(tree.get(p) != b for p, b in action["guards"].items()):
                continue
            groups = {}
            valid = True
            for world in possible:
                outcome = action["outcomes"][world]
                writes = outcome["writes"]
                if any(p in data["protected"] for p in writes):
                    valid = False
                    break
                # Replacement retains old allocation until commit, including a no-op write.
                allocated = sum(sizes[b] for b in writes.values() if b is not None)
                if sum(sizes[b] for b in tree.values()) + allocated > data["disk"]:
                    valid = False
                    break
                changed = dict(tree)
                for path, blob in writes.items():
                    if blob is None:
                        changed.pop(path, None)
                    else:
                        changed[path] = blob
                if not safe(changed):
                    valid = False
                    break
                label = outcome["label"]
                canonical = tuple(sorted(changed.items()))
                if label in groups and groups[label][0] != canonical:
                    raise ValueError("a visible label must identify the resulting visible tree")
                groups.setdefault(label, (canonical, []))[1].append(world)
            if not valid:
                continue
            total_cost = len(possible) * action["cost"]
            total_steps = len(possible)
            branches = []
            for label, (changed, members) in sorted(groups.items()):
                child = solve(changed, tuple(members), used | 1 << i, remaining - 1)
                if child is None:
                    valid = False
                    break
                total_cost += child[0]
                total_steps += child[1]
                branches.append({"label": label, "next": child[2]})
            if valid:
                policy = {"action": action["id"], "branches": branches}
                code = json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                candidate = (total_cost, total_steps, code, policy)
                if best is None or candidate[:3] < best[:3]:
                    best = candidate
        return (best[0], best[1], best[3]) if best else None

    if not safe(data["initial"]):
        return {"feasible": False}
    answer = solve(tuple(sorted(data["initial"].items())), worlds, 0, data["max_rounds"])
    if answer is None:
        return {"feasible": False}
    cost, steps = Fraction(answer[0], len(worlds)), Fraction(answer[1], len(worlds))
    return {"feasible": True, "cost": [cost.numerator, cost.denominator],
            "steps": [steps.numerator, steps.denominator], "policy": answer[2]}


PROMPT489 = """
Построй КОНТИНГЕНТНУЮ policy атомарной миграции файлового namespace: действия
могут обнаружить скрытый режим инфраструктуры, план обязан завершиться во ВСЕХ
совместимых режимах. Вход {worlds,initial,target,sizes,protected,invariants,disk,
max_rounds,actions}. worlds —1..4 различных ASCII имен, один режим выбирается
равновероятно при старте и сохраняется. Его нельзя наблюдать напрямую.
initial/target — file path->blob, пути безопасны, case-sensitive, dirs неявны.
protected — ТОЧНЫЕ paths, всегда должны сохранить начальное наличие/значение,
и ни один action не может записать туда даже то же значение. invariants —
список clauses; clause — список [path,blob_or_null], хотя бы одно равенство
tree.get(path)==blob должно выполняться в КАЖДОМ видимом состоянии. null=нет файла.
Нельзя иметь file path, являющийся строгим предком другого file. sum sizes
физических файлов<=disk, одинаковые blobs в разных paths считаются отдельно.

<=12 actions: {id,guards:{path:blob_or_null},cost>=0,outcomes:{world:
{label,writes:{path:blob_or_null}}}}. Один round — ровно одно выбранное действие,
action не повторяется на одной ветви. Guards проверяются на текущем дереве.
Текущий world определяет outcome, но policy получает ТОЛЬКО label и новое
дерево. Одинаковый label данного action гарантированно имеет одинаковые writes.
Writes применяются атомарно, null удаляет (можно уже отсутствующий) файл.
Каждый nonnull write аллоцирует НОВУЮ копию даже при неизменившемся blob.
Дисковая вершина = start_size+sum(size всех nonnull writes), <=disk; старые
версии и удаления освобождаются только при commit. Invariants проверяются
до/после commit, а protected запрет — и на попытку записи.
Если action недопустим хотя бы в одном ещё возможном world, его выбирать нельзя.
Outcomes без оставшихся possible worlds не являются ветвями.

Нужно достичь ТОЧНОГО target за<=max_rounds (0..9) в каждом world. В target
немедленно остановиться. Ненужные actions можно пропустить; test/probe actions
могут иметь пустые writes и различать modes. При отсутствии policy feasible:false.
Среди policy минимизируй (ожидаемая sum cost, ожидаемое число rounds, policy_code).
Ожидания — по исходному uniform world, posterior только исключает несовместимые.
policy_code=json.dumps(policy,sort_keys=True,separators=(",",":"),ensure_ascii=False).
Это точное лексикографическое сравнение строки; ids/labels ASCII.
Policy: {done:true} в target либо {action:id,branches:[{label,next:policy}]},
branches по label. Вывод {feasible:true,cost:[num,den],steps:[num,den],policy};
дроби несократимы. Скрытый world не разрешает заранее выбрать разные первые actions.
"""


def causal490(data):
    import json

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    events, invalid = [], []
    for line, raw in enumerate(data["lines"], 1):
        try:
            event = json.loads(raw, object_pairs_hook=pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            events.append(event)
        except (ValueError, TypeError):
            invalid.append(line)
    events.sort(key=lambda e: e["id"])
    groups = sorted({e["group"] for e in events})
    best = None
    for mask in range(1 << len(groups)):
        enabled = {g for i, g in enumerate(groups) if mask >> i & 1}
        chosen = [e for e in events if e["group"] in enabled]
        by_dot = {}
        valid = True
        for event in chosen:
            dot = tuple(event["dot"])
            if dot in by_dot:
                valid = False
                break
            by_dot[dot] = event
        if not valid:
            continue
        edges = {e["id"]: set() for e in chosen}
        for event in chosen:
            replica, counter = event["dot"]
            ctx = event["context"]
            if ctx.get(replica, 0) != counter - 1:
                valid = False
                break
            for actor, end in ctx.items():
                for count in range(1, end + 1):
                    dependency = by_dot.get((actor, count))
                    if dependency is None or dependency["id"] == event["id"]:
                        valid = False
                        break
                    if any(ctx.get(key, 0) < value for key, value in dependency["context"].items()):
                        valid = False
                        break
                    edges[event["id"]].add(dependency["id"])
                if not valid:
                    break
            if not valid:
                break
        if not valid:
            continue
        order, remaining = [], set(edges)
        while remaining:
            ready = sorted(name for name in remaining if not edges[name] & remaining)
            if not ready:
                break
            order.append(ready[0])
            remaining.remove(ready[0])
        if remaining:
            continue
        ids = tuple(e["id"] for e in chosen)
        score = sum(e["weight"] for e in chosen)
        key = (-score, -len(ids), ids)
        if best is not None and best[0] <= key:
            continue
        documents = []
        for name in sorted({e["key"] for e in chosen}):
            rows = [e for e in chosen if e["key"] == name]
            frontier = [
                e for e in rows if not any(
                    other["context"].get(e["dot"][0], 0) >= e["dot"][1]
                    for other in rows if other is not e
                )
            ]
            tombstones = [e for e in frontier if e["op"] == "delete"]
            winner = min(tombstones or frontier, key=lambda e: e["id"])
            documents.append({"key": name, "frontier": sorted(e["id"] for e in frontier),
                              "winner": winner["id"], "deleted": bool(tombstones),
                              "value": None if tombstones else winner["value"]})
        best = (key, {"selected": list(ids), "rejected": [e["id"] for e in events if e not in chosen],
                      "weight": score, "order": order, "documents": documents,
                      "invalid_lines": invalid})
    return best[1]


PROMPT490 = """
Восстанови максимальную согласованную историю из JSONL с forked replica dots.
Вход {lines:[строки]}. Парсинг строгий: повтор любого ключа на любой глубине,
NaN/Infinity или синтаксическая ошибка => строка invalid. Остальные строки
гарантированно валидные события {id,group,weight,dot:[replica,counter],
context:{replica:counter},key,op:put|delete,value}. id/key/group/replica ASCII,
ids уникальны; counter у dot>=1, context>=0, отсутствующий context counter=0.
<=18 parsed events, <=12 groups, <=4 replicas, counters<=6, weight>=0.
Порядок lines не задаёт причинность. Несколько разных событий могут иметь один dot.
Включение АТОМАРНО по group: включены все parsed события group либо ни одно.
Invalid строки не принадлежат никакой группе и просто исключаются.

Для выбранного множества: каждый dot уникален; event.context собственного
replica == event.dot.counter-1. Для КАЖДОГО (r,n) context должны существовать
выбранные события для ВСЕХ dots(r,1..n), не само событие. Context event должен
покомпонентно покрывать context КАЖДОГО такого predecessor (транзитивная
замкнутость). Граф predecessor->event обязан быть DAG. Причинные зависимости
между разными keys тоже обязательны; пропуск counters запрещён.
Максимизируй (sum_weight,число_events), затем минимизируй sorted selected ids.
Пустой выбор допустим. Не выбирай независимо победителя каждого duplicated dot:
его context может сделать более дорогую зависимую группу недопустимой.

order — лексикографически минимальная топологическая последовательность ids.
Для каждого key в выбранных events frontier — те события этого key, чей dot
НЕ включён в context ни одного другого выбранного события этого же key.
Если frontier содержит delete, deleted=true и winner=min id среди deletes.
Иначе winner=min id frontier и value его payload. Delete победителя даёт null.
Вывод {selected:[id],rejected:[parsed id],weight,order:[id],
documents:[{key,frontier:[id],winner,deleted,value}],invalid_lines:[1-based line]}.
selected/rejected/frontier по id, documents по key, invalid_lines по номеру.
Пустые documents и null payload допустимы. Все значения JSON сравниваются строго.
"""


def serial491(data):
    import json

    transactions = sorted(data["transactions"], key=lambda t: t["id"])

    def encode(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    def same(a, b):
        if type(a) is not type(b):
            return False
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
        if isinstance(a, list):
            return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
        return a == b

    def execute(tx, start):
        work = dict(start)
        bad = []
        saves = []
        for op in tx["ops"]:
            kind = op["op"]
            if kind == "put":
                work[op["key"]] = op["value"]
            elif kind == "delete":
                work.pop(op["key"], None)
            elif kind == "get":
                seen = {"exists": op["key"] in work, "value": work.get(op["key"])}
                bad.append(not same(seen, op["seen"]))
            elif kind == "scan":
                seen = {k: v for k, v in work.items() if op["low"] <= k < op["high"]}
                bad.append(not same(seen, op["seen"]))
            elif kind == "save":
                saves = [s for s in saves if s[0] != op["name"]]
                saves.append((op["name"], dict(work), list(bad)))
            else:
                index = next(i for i, item in enumerate(saves) if item[0] == op["name"])
                work, bad = dict(saves[index][1]), list(saves[index][2])
                saves = saves[:index + 1]
        return None if any(bad) else work

    initial = encode(data["initial"])
    reachable = {(0, initial): ()}
    best = None
    for count in range(len(transactions) + 1):
        current = [(key, order) for key, order in reachable.items() if key[0].bit_count() == count]
        for (mask, state), order in current:
            selected = tuple(t["id"] for i, t in enumerate(transactions) if mask >> i & 1)
            ids = set(selected)
            if set(data["required"]) <= ids and all(
                set(t["requires"]) <= ids for i, t in enumerate(transactions) if mask >> i & 1
            ):
                reward = sum(t["reward"] for i, t in enumerate(transactions) if mask >> i & 1)
                candidate = (-reward, -count, selected, order)
                if best is None or candidate < best[0]:
                    best = (candidate, {"feasible": True, "reward": reward,
                                        "selected": list(selected), "order": list(order),
                                        "data": json.loads(state)})
            for i, tx in enumerate(transactions):
                if mask >> i & 1:
                    continue
                # A transaction appended after an already serialized one cannot precede it in real time.
                if any(tx["end"] <= earlier["start"] for j, earlier in enumerate(transactions) if mask >> j & 1):
                    continue
                changed = execute(tx, json.loads(state))
                if changed is None:
                    continue
                key = (mask | 1 << i, encode(changed))
                new_order = order + (tx["id"],)
                if key not in reachable or new_order < reachable[key]:
                    reachable[key] = new_order
    return best[1] if best else {"feasible": False}


PROMPT491 = """
Найди максимальный strict-serializable поднабор транзакций по записанным
наблюдениям, включая predicate scans и savepoint rollback. Вход
{initial:{key:JSON},required:[id],transactions:[{id,start,end,reward,requires,ops}]}.
<=9 transactions, <=12 ops/tx, <=8 ASCII keys; start<end, reward>=0.
Выбирается поднабор и последовательный порядок его атомарных выполнений.
Если A.end<=B.start и обе выбраны, A должна быть раньше B. requires — ids,
которые тоже должны быть ВЫБРАНЫ (не обязательно раньше; это не order edge).
Виртуальное последовательное исполнение должно воспроизвести все оставшиеся
наблюдения: get{key,seen:{exists:bool,value:JSON_or_null}},
scan{low,high,seen:{key:value}} читает ВСЕ keys low<=key<high, включая phantom.
Absent get => exists=false,value=null; настоящее null => exists=true.
put{key,value}, delete{key} меняют локальную копию текущей committed карты.
Чтения видят свои writes. После ops локальная карта коммитится атомарно.
save{name} сохраняет локальную карту И список read obligations; повторное
имя удаляет старый одноимённый savepoint, новый добавляется в конец.
rollback{name} восстанавливает карту И obligations до этого savepoint,
удаляет более поздние savepoints, сохраняет сам named savepoint.
Rollback targets гарантированно существуют согласно последовательности ops.
Наблюдения откаченного участка НЕ обязаны совпасть. Поэтому mismatch нельзя
немедленно признать необратимым abort, пока не обработан последующий rollback.

Все значения сравниваются со строгими JSON-типами; bool,int,float различны,
dict order не важен. Нет незаписанных reads и других типов ops.
Оптимизируй (-sum_reward,-число_selected,sorted_selected_ids,serial_order).
Это GLOBAL subset: greedy commit-order и один фиксированный conflict graph
не учитывают изменение predicate membership и read-your-writes.
Если required невозможны, {feasible:false}; иначе {feasible:true,reward,
selected:[id],order:[id],data:итоговая_карта}. selected отсортирован.
Пустой поднабор разрешён, initial не изменяется при пустом выборе.
"""


def dependency492(data):
    nodes = sorted(data["nodes"])
    index = {name: i for i, name in enumerate(nodes)}
    bundles = sorted(data["bundles"], key=lambda b: b["id"])
    best = None
    for mask in range(1 << len(bundles)):
        kept = {b["id"] for i, b in enumerate(bundles) if mask >> i & 1}
        if any(b["id"] in kept and (
            not set(b["requires"]) <= kept or set(b["excludes"]) & kept
        ) for b in bundles):
            continue
        if any(not (clause["min"] <= len(kept & set(clause["ids"])) <= clause["max"])
               for clause in data["quotas"]):
            continue
        edges = list(data["required"])
        for bundle in bundles:
            if bundle["id"] in kept:
                edges.extend(bundle["edges"])
        reach = [0] * len(nodes)
        for source, target in edges:
            reach[index[source]] |= 1 << index[target]
        for middle in range(len(nodes)):
            for source in range(len(nodes)):
                if reach[source] >> middle & 1:
                    reach[source] |= reach[middle]
        if any(reach[i] >> i & 1 for i in range(len(nodes))):
            continue
        if any(not (reach[index[a]] >> index[b] & 1) for a, b in data["must_reach"]):
            continue
        if any(reach[index[a]] >> index[b] & 1 for a, b in data["must_not_reach"]):
            continue
        drop = tuple(b["id"] for b in bundles if b["id"] not in kept)
        cost = sum(b["cost"] for b in bundles if b["id"] not in kept)
        candidate = (cost, len(drop), drop)
        if best is not None and best[0] <= candidate:
            continue
        remaining, order = set(nodes), []
        while remaining:
            ready = [v for v in remaining if not any(a in remaining and b == v for a, b in edges)]
            node = min(ready, key=lambda x: (data["priority"][x], x))
            remaining.remove(node)
            order.append(node)
        best = (candidate, {"feasible": True, "cost": cost, "drop": list(drop), "order": order,
                            "reach": [[a, b] for a in nodes for b in nodes
                                      if reach[index[a]] >> index[b] & 1]})
    return best[1] if best else {"feasible": False}


PROMPT492 = """
Отремонтируй dependency graph, удаляя АТОМАРНЫЕ feature bundles, сохраняя
глобальные reachability constraints. Вход {nodes,priority,required,bundles,
quotas,must_reach,must_not_reach}. <=12 nodes ASCII; edges [u,v] означают u
РАНЬШЕ v. required edges нельзя удалить. <=16 bundles:
{id,cost>=0,edges:[[u,v]],requires:[bundle_id],excludes:[bundle_id]}.
Keep bundle => все его edges включены; requires должны быть kept, ни один
excludes не kept. Excludes действует в записанном направлении, но достаточно
любого нарушения; не надо искусственно дополнять requires симметрично.
Edges от нескольких bundles объединяются, drop одного не удаляет edge другого.
quota {ids,min,max} ограничивает число kept ids этого списка включительно.
Все id ссылаются на существующие объекты, self requires допустим.
Итоговый граф DAG; каждый [a,b] must_reach требует НЕПУСТОЙ directed path,
must_not_reach запрещает любой такой path. Проверяй транзитивное замыкание
после всего выбора, не только непосредственные edges.
Оптимизируй (sum_cost_dropped,число_dropped,sorted_dropped_ids).
Если нет решения, {feasible:false}; иначе {feasible:true,cost,drop:[id],
order:[node],reach:[[source,target]]}. drop по id, reach по source,target.
order строится Kahn: среди всех сейчас ready выбирай minimum(priority,node).
Reachability — для исходного итогового DAG, не добавляй edges из order.
Пустые nodes/bundles допустимы. Required cycle означает невозможность.
"""


def schedule493(data):
    from itertools import product

    jobs = sorted(data["jobs"], key=lambda j: j["id"])
    by_id = {j["id"]: j for j in jobs}
    horizon = len(data["capacity"])
    order = []
    remaining = set(by_id)
    while remaining:
        ready = sorted(x for x in remaining if all(d["id"] not in remaining for d in by_id[x]["deps"]))
        if not ready:
            return {"feasible": False}
        order.append(ready[0])
        remaining.remove(ready[0])
    best = None
    assigned = {}
    usage = [[0] * len(data["capacity"][0]) for _ in range(horizon)] if horizon else []

    def visit(position):
        nonlocal best
        if position == len(order):
            storage = [0] * horizon
            for job in jobs:
                end = assigned[job["id"]][1]
                consumers = [assigned[j["id"]][0] for j in jobs
                             if any(d["id"] == job["id"] for d in j["deps"])]
                last = max(consumers, default=end)
                for t in range(end, last):
                    storage[t] += job["bytes"]
            if any(value > cap for value, cap in zip(storage, data["storage"], strict=True)):
                return
            makespan = max((value[1] for value in assigned.values()), default=0)
            energy = sum(
                sum(data["tariff"][t] * mode["power"] for t in range(start, end))
                for start, end, _, mode in assigned.values()
            )
            signature = tuple((assigned[j["id"]][0], assigned[j["id"]][2]) for j in jobs)
            candidate = (makespan, energy, max(storage, default=0), signature)
            if best is None or candidate < best[0]:
                best = (candidate, {"feasible": True, "makespan": makespan, "energy": energy,
                                    "storage_peak": candidate[2], "schedule": [
                                        {"id": j["id"], "start": assigned[j["id"]][0],
                                         "end": assigned[j["id"]][1], "mode": assigned[j["id"]][2]}
                                        for j in jobs]})
            return
        job = by_id[order[position]]
        for mode, start in product(sorted(job["modes"], key=lambda m: m["id"]), range(job["release"], job["deadline"])):
            end = start + mode["duration"]
            if end > min(job["deadline"], horizon):
                continue
            if best is not None and end > best[0][0]:
                continue
            if any(not (d["min"] <= start - assigned[d["id"]][1] <= d["max"]) for d in job["deps"]):
                continue
            if any(usage[t][r] + amount > data["capacity"][t][r]
                   for t in range(start, end) for r, amount in enumerate(mode["demand"])):
                continue
            if any(
                set(job["locks"]) & set(by_id[other]["locks"]) and start < oe and os < end
                for other, (os, oe, _, _) in assigned.items()
            ):
                continue
            for t in range(start, end):
                for r, amount in enumerate(mode["demand"]):
                    usage[t][r] += amount
            assigned[job["id"]] = (start, end, mode["id"], mode)
            visit(position + 1)
            del assigned[job["id"]]
            for t in range(start, end):
                for r, amount in enumerate(mode["demand"]):
                    usage[t][r] -= amount

    visit(0)
    return best[1] if best else {"feasible": False}


PROMPT493 = """
Найди точное multi-mode расписание build/migration jobs с renewable resources
И буфером промежуточных артефактов. Вход {capacity,storage,tariff,jobs}.
H=len(capacity)=1..12, capacity[t] —2 ресурса, storage[t] — shared bytes limit,
tariff[t]>=0. Все массивы длины H. <=6 jobs:
{id,release,deadline,bytes,locks:[name],deps:[{id,min,max}],
modes:[{id,duration,demand:[r0,r1],power}]}.
Необходим один mode и целый start для каждого job. duration>=1, mode ids
уникальны внутри job; 1..3 modes/job. end=start+duration<=deadline<=H,
start>=release>=0. Occupancy [start,end); sum demands<=capacity каждого тика,
общие locks запрещают пересечения. Precedence DAG: start(job)-end(dep)
должен быть в [min,max], 0<=min<=max. Exact lag задаётся min=max.
Результат job bytes>=0 появляется в end и хранится до START ПОСЛЕДНЕГО
прямого consumer (любой job, имеющий этот dep). Буфер занимает
[end,max_consumer_start); если consumers отсутствуют, отдаётся сразу.
Одна физическая копия на job, даже при нескольких consumers. Никакое
потребление в начале первого consumer не освобождает данные для последнего.
Capacity, locks и storage независимы; storage calendar может меняться.
Energy=sum по jobs power(mode)*sum tariff[start:end].
Оптимизируй (makespan,energy,max_t storage_usage[t],signature),
signature=[(start,mode_id)] для jobs по id, сравнение обычное lex.
Вывод {feasible:false}, если невозможно; иначе {feasible:true,makespan,
energy,storage_peak,schedule:[{id,start,end,mode}]} по job id.
Выбор самого быстрого mode может сделать буфер/календарь недопустимым.
"""


def configuration494(data):
    from itertools import product

    def equal(a, b):
        if type(a) is not type(b):
            return False
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
        if isinstance(a, list):
            return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b, strict=True))
        return a == b

    variables = sorted(data["variables"])
    nodes, writers = {}, {}
    for layer in data["layers"]:
        for key, expression in layer["nodes"].items():
            if expression == {"delete": True}:
                nodes.pop(key, None)
                writers.pop(key, None)
            else:
                nodes[key], writers[key] = expression, layer["id"]
    best = None
    for choices in product(*(range(len(data["variables"][v])) for v in variables)):
        assignment = {v: data["variables"][v][i] for v, i in zip(variables, choices, strict=True)}
        cache, visiting = {}, set()

        def node(key, cache=cache, visiting=visiting):
            if not isinstance(key, str):
                raise ValueError("reference key")
            if key in cache:
                return cache[key]
            if key not in nodes:
                raise KeyError(key)
            if key in visiting:
                raise ValueError("cycle")
            visiting.add(key)
            try:
                value, provenance = evaluate(nodes[key])
                cache[key] = value, provenance | {writers[key]}
                return cache[key]
            finally:
                visiting.remove(key)

        def evaluate(expr, assignment=assignment):
            kind = expr["op"]
            if kind == "lit":
                return expr["value"], set()
            if kind == "var":
                return assignment[expr["name"]]["value"], {"var:" + expr["name"]}
            if kind == "ref":
                key, origins = evaluate(expr["key"])
                value, refs = node(key)
                return value, origins | refs
            if kind == "if":
                condition, origins = evaluate(expr["cond"])
                if type(condition) is not bool:
                    raise ValueError("condition")
                value, sources = evaluate(expr["then"] if condition else expr["else"])
                return value, origins | sources
            if kind == "coalesce":
                for arg in expr["args"]:
                    try:
                        return evaluate(arg)
                    except KeyError:
                        pass
                raise KeyError("empty coalesce")
            values, origins = [], set()
            for arg in expr["args"]:
                value, sources = evaluate(arg)
                origins |= sources
                values.append(value)
                if kind in ("and", "or"):
                    if type(value) is not bool:
                        raise ValueError("boolean")
                    if (kind == "and" and not value) or (kind == "or" and value):
                        return value, origins
            if kind == "eq":
                return equal(values[0], values[1]), origins
            if kind == "not":
                if type(values[0]) is not bool:
                    raise ValueError("boolean")
                return not values[0], origins
            if kind in ("and", "or"):
                return kind == "and", origins
            if kind == "concat":
                if any(type(v) is not str for v in values):
                    raise ValueError("string")
                return "".join(values), origins
            if kind in ("add", "mul", "le"):
                if any(type(v) is not int for v in values):
                    raise ValueError("integer")
                if kind == "add":
                    return sum(values), origins
                if kind == "le":
                    return values[0] <= values[1], origins
                value = 1
                for item in values:
                    value *= item
                return value, origins
            raise ValueError(kind)

        try:
            outputs = {key: node(key) for key in sorted(data["outputs"])}
            if any(evaluate(expr)[0] is not True for expr in data["assertions"]):
                continue
        except (KeyError, ValueError):
            continue
        cost = sum(v["cost"] for v in assignment.values())
        changed = sum(i != data["baseline"][v] for v, i in zip(variables, choices, strict=True))
        candidate = (cost, changed, choices)
        if best is None or candidate < best[0]:
            best = (candidate, {"feasible": True, "cost": cost, "changed": changed,
                                "choices": dict(zip(variables, choices, strict=True)),
                                "resolved": {k: v[0] for k, v in outputs.items()},
                                "provenance": {k: sorted(v[1]) for k, v in outputs.items()}})
    return best[1] if best else {"feasible": False}


PROMPT494 = """
Разреши layered config И выбери глобально минимальные правки окружения.
Вход {variables,baseline,layers,outputs,assertions}. <=8 variables; variable
имеет1..3 options [{value:JSON,cost:неотрицательное целое}]; baseline — index
исходной option каждого variable (0-based). Один выбор действует во ВСЁМ config.
Layers [{id,nodes:{key:expression}}] применяются по порядку; последний writer
заменяет expression полностью, {delete:true} удаляет node. Node keys/layer ids
ASCII, уникальны внутри слоя; всего<=25 nodes после merge. Отсутствие !=null.

Expression tree: {op:"lit",value}; {op:"var",name};
{op:"ref",key:expression} — key должен дать string, затем разреши node;
{op:"if",cond,then,else} — cond СТРОГО bool, вычисляется лишь выбранная ветвь;
{op:"coalesce",args:[expr]} — первый expr БЕЗ missing-node error, null считается
успехом. Type errors и cycles НЕ поглощаются coalesce. Provenance неудачных
coalesce branches отбрасывается. Остальные ops имеют args:
eq(2) — строгое JSON equality (bool/int/float различны; dict order не важен);
not(1), and/or(любое число) — только bool, lazy слева направо; пустое and=true,
or=false. concat — только strings. add/mul — только INTEGERS, bool запрещён;
пустые add=0,mul=1. le(2) — integer <=. Неявных приведений типов нет.
Цикл — повторное разрешение node на текущем активном стеке; НЕИСПОЛНЕННАЯ
ветвь с cycle допустима. Ошибка любого output или evaluated assertion делает
assignment недопустимым. Каждый assertion обязан дать именно boolean true.

Provenance — множество layer ids ВСЕХ вычисленных nodes и var:NAME всех
вычисленных vars транзитивно, включая condition и dynamic reference key.
lit не имеет источника. Неисполненные ветви источников не добавляют.
Output provenance НЕ включает независимые assertions. Unused nodes разрешать
не требуется. Integer magnitude результата<=10^12, суммарный output<=100KB.
Оптимизируй (sum option_cost,число индексов отличных от baseline,
список выбранных option indices для variable names по алфавиту).
Вывод {feasible:false} либо {feasible:true,cost,changed,choices:{var:index},
resolved:{output:value},provenance:{output:[source]}}; sources отсортированы.
Есть связанные ограничения, поэтому минимизировать каждую variable отдельно
или заранее объявлять все циклы фатальными неправильно.
"""


def profiles495(data):
    profiles = data["profiles"]
    optional = sorted(
        ((edge["id"], edge["cost"]) for p in profiles.values() for edge in p["parents"] if edge["optional"]),
    )

    def same(a, b):
        if type(a) is not type(b):
            return False
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
        if isinstance(a, list):
            return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
        return a == b

    best = None
    for mask in range(1 << len(optional)):
        drop = tuple(name for i, (name, _) in enumerate(optional) if mask >> i & 1)
        removed = set(drop)
        cost = sum(cost for i, (_, cost) in enumerate(optional) if mask >> i & 1)
        key = (cost, len(drop), drop)
        if best is not None and best[0] <= key:
            continue
        cache, visiting = {}, set()

        def linearize(name, cache=cache, visiting=visiting, removed=removed):
            if name in cache:
                return list(cache[name])
            if name in visiting:
                raise ValueError("cycle")
            visiting.add(name)
            parents = [e["parent"] for e in profiles[name]["parents"] if e["id"] not in removed]
            seqs = [linearize(p) for p in parents] + [list(parents)]
            output = [name]
            while any(seqs):
                seqs = [row for row in seqs if row]
                candidate = next(
                    (row[0] for row in seqs if all(row[0] not in other[1:] for other in seqs)), None,
                )
                if candidate is None:
                    raise ValueError("inconsistent")
                output.append(candidate)
                for row in seqs:
                    if row and row[0] == candidate:
                        row.pop(0)
            visiting.remove(name)
            cache[name] = output
            return list(output)

        try:
            for name in sorted(profiles):
                linearize(name)
            results = []
            for query in data["queries"]:
                mro = linearize(query["profile"])
                if not set(query["ancestors"]) <= set(mro):
                    raise ValueError("ancestor")
                config, writers = {}, {}
                for name in reversed(mro):
                    for op in profiles[name]["ops"]:
                        field = op["key"]
                        old = config.get(field)
                        if op["op"] == "delete":
                            config.pop(field, None)
                            writers.pop(field, None)
                        elif op["op"] == "set":
                            config[field], writers[field] = op["value"], name
                        else:
                            if field not in config or type(old) is not list:
                                raise ValueError("list")
                            if op["op"] == "append":
                                value = list(old)
                                for item in op["values"]:
                                    if not any(same(item, previous) for previous in value):
                                        value.append(item)
                            else:
                                value = [item for item in old if not any(same(item, x) for x in op["values"])]
                            if not same(value, old):
                                config[field], writers[field] = value, name
                if any(field not in config or not same(config[field], value)
                       for field, value in query["expect"].items()):
                    raise ValueError("expect")
                if any(field in config for field in query["absent"]):
                    raise ValueError("absent")
                results.append({"id": query["id"], "mro": mro, "config": config, "writers": writers})
        except (KeyError, ValueError):
            continue
        best = (key, {"feasible": True, "cost": cost, "drop": list(drop), "results": results})
    return best[1] if best else {"feasible": False}


PROMPT495 = """
Исправь набор C3 profiles ОДНИМ глобальным удалением optional parent edges.
Вход {profiles,queries}. <=10 profiles; parents — ORDERED list of
{id,parent,optional,cost}; edge ids глобально уникальны, parents существуют,
одинаковый parent дважды в списке не встречается. <=12 optional edges.
Неoptional edges нельзя удалить, остальные удаления имеют cost>=0.
Нужна корректная C3 linearization КАЖДОГО profile, включая неиспользованные:
L(P)=[P]+merge(L(parent1),...,L(parentN),[parents в исходном порядке]).
Merge выбирает первый head слева направо, не входящий ни в один другой tail,
и удаляет этот head из голов всех последовательностей. Нет head=>inconsistent;
цикл=>invalid. Удаление edge не меняет относительный порядок оставшихся parents.

Queries в заданном порядке: {id,profile,ancestors:[profile],expect:{key:JSON},
absent:[key]}. Все ancestors должны входить в MRO (сам profile тоже разрешён).
Построй config, выполняя ops профилей В ОБРАТНОМ MRO, ops внутри profile по
порядку. set{key,value} пишет значение и writer, даже если значение прежнее.
delete{key} удаляет key/writer, отсутствие допустимо. append/remove{key,values}
требует существующий list. append добавляет только отсутствующие typed values,
remove удаляет все typed-equal values. Строго bool/int/float различаются.
Если list edit не изменил list, прежний writer сохраняется.
Expect требует наличия и typed equality; absent — отсутствия. Любая ошибка
ops любого query делает ОБЩИЙ выбранный ремонт недопустимым.
Необходимо одновременно удовлетворить все queries. Оптимизируй
(sum costs dropped,число dropped,sorted dropped edge ids).
Вывод {feasible:false} или {feasible:true,cost,drop:[id],results:
[{id,mro:[profile],config:{...},writers:{key:profile}}]} в порядке queries.
Локально самый дешёвый ремонт одного diamond может сломать другой query.
"""


def merge496(data):
    import json
    from functools import lru_cache
    from itertools import product

    missing = object()

    def same(a, b):
        if a is missing or b is missing:
            return a is b
        if type(a) is not type(b):
            return False
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
        if isinstance(a, list):
            return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
        return a == b

    def alternatives(base, ours, theirs, path):
        if same(ours, theirs):
            return [(ours, 0, ())]
        if same(ours, base):
            return [(theirs, 0, ())]
        if same(theirs, base):
            return [(ours, 0, ())]
        if all(type(value) is dict for value in (base, ours, theirs)):
            keys = sorted(set(base) | set(ours) | set(theirs))
            child = [alternatives(base.get(k, missing), ours.get(k, missing), theirs.get(k, missing),
                                  path + "/" + k.replace("~", "~0").replace("/", "~1")) for k in keys]
            result = []
            for values in product(*child):
                merged = {k: item[0] for k, item in zip(keys, values, strict=True) if item[0] is not missing}
                result.append((merged, sum(item[1] for item in values),
                               tuple(r for item in values for r in item[2])))
            return result
        return [(ours, data["weights"]["theirs"], ((path, "ours"),)),
                (theirs, data["weights"]["ours"], ((path, "theirs"),))]

    names = sorted(set(data["base"]) | set(data["ours"]) | set(data["theirs"]))
    variants = [alternatives(data["base"].get(n, missing), data["ours"].get(n, missing),
                             data["theirs"].get(n, missing), "/" + n) for n in names]
    best = None
    order_cache = {}

    def order_for(ids):
        if ids in order_cache:
            return order_cache[ids]

        @lru_cache(None)
        def dp(mask):
            if mask == (1 << len(ids)) - 1:
                return 0, ()
            candidates = []
            placed = {ids[i] for i in range(len(ids)) if mask >> i & 1}
            for i, name in enumerate(ids):
                if mask >> i & 1:
                    continue
                if any(e["hard"] and e["after"] == name and e["before"] in ids and e["before"] not in placed
                       for e in data["order"]):
                    continue
                sub = dp(mask | 1 << i)
                if sub is None:
                    continue
                loss = sum(e["weight"] for e in data["order"]
                           if not e["hard"] and e["before"] == name
                           and (e["after"] in placed or e["after"] == name))
                candidates.append((loss + sub[0], (name,) + sub[1]))
            return min(candidates) if candidates else None

        order_cache[ids] = dp(0)
        return order_cache[ids]

    for selected in product(*variants):
        records = {name: choice[0] for name, choice in zip(names, selected, strict=True) if choice[0] is not missing}
        if not set(data["required"]) <= set(records):
            continue
        if any(type(r.get("name")) is not str or type(r.get("refs")) is not list for r in records.values()):
            continue
        if len({r["name"] for r in records.values()}) != len(records):
            continue
        if any(ref not in records for r in records.values() for ref in r["refs"]):
            continue
        order = order_for(tuple(sorted(records)))
        if order is None:
            continue
        loss = sum(v[1] for v in selected) + order[0]
        resolutions = tuple(sorted(r for item in selected for r in item[2]))
        signature = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        candidate = (loss, order[1], signature, resolutions)
        if best is None or candidate < best[0]:
            best = (candidate, {"feasible": True, "loss": loss,
                                "records": [{"id": name, "value": records[name]} for name in order[1]],
                                "resolutions": [{"path": p, "side": side} for p, side in resolutions]})
    return best[1] if best else {"feasible": False}


PROMPT496 = """
Сделай structural three-way merge с ГЛОБАЛЬНЫМИ namespace/reference constraints
и оптимальным порядком записей. Вход {base,ours,theirs,weights:{ours,theirs},
required:[id],order:[{before,after,weight,hard}]}.
Три карты id->record; <=7 ids, ASCII без "/" и "~". Record — JSON object с
обязательными name:string и refs:[id], плюс произвольные JSON fields.
После слияния name должны быть УНИКАЛЬНЫ, каждый ref указывать на сохранённый
id, все required ids сохранены. Self reference допустим. Эти ограничения
могут заставить выбрать другую сторону конфликта или сделать merge impossible.

Recursive merge с ABSENT, отличным от null: если O==T, бери O; иначе если O==B,
бери T; иначе если T==B, бери O. Equality строго JSON typed; object key order
не важен. Иначе, если B,O,T ВСЕ objects, рекурсивно объединяй union keys
(ABSENT children разрешены). Во всех остальных случаях конфликт: можно взять
ТОЛЬКО O либо T, включая ABSENT (удаление). Никакого fallback B.
Выбор ours теряет weights.theirs, выбор theirs теряет weights.ours; значения
weights положительные integers. Независимые автоматически merged fields дают0.
Resolutions содержат именно достигнутые конфликтные узлы; не надо добавлять
дочерние конфликты внутри атомарно выбранного whole-record delete/modify.
Path — JSON pointer от корня maps, включая id; escape "~"->"~0","/"->"~1".

Order edges действуют только если обе записи сохранены. Hard требует before
раньше after. Soft добавляет weight>=0 к loss, если нарушен. Все edges разные
по паре и hard; циклы soft разрешены, hard могут сделать выбор недопустимым.
Финальный total loss=sum conflict losses+violated soft edge weights.
Оптимизируй (loss,список ids в порядке records,canonical_records_map,
список(path,side) по path). canonical_records_map=json.dumps(map,sort_keys=True,
separators=(",",":"),ensure_ascii=False). Полный набор вариантов recursive
merge до constraints гарантированно<=4096; depth<=4.
Вывод {feasible:false} либо {feasible:true,loss,records:[{id,value:record}],
resolutions:[{path,side:"ours"|"theirs"}]} по path.
Выбор победителя каждого конфликта и отдельная сортировка без constraints
не решают задачу: namespace uniqueness, dangling refs и ordering связаны.
"""


def patch497(data):
    import itertools

    hunks = sorted(data["hunks"], key=lambda h: h["id"])
    candidates = []
    for hunk in hunks:
        text = data["files"][hunk["file"]]
        rows = []
        for position in range(len(text) - len(hunk["old"]) + 1):
            captured, good = {}, True
            for token, actual in zip(hunk["old"], text[position:position + len(hunk["old"])], strict=True):
                if isinstance(token, str):
                    good &= token == actual
                else:
                    key = token["capture"]
                    if key in captured and captured[key] != actual:
                        good = False
                    captured[key] = actual
            if good:
                rows.append((position, captured))
        candidates.append(rows)
    best = None
    for choices in itertools.product(*([None] + row for row in candidates)):
        selected = {h["id"] for h, choice in zip(hunks, choices, strict=True) if choice is not None}
        if any(h["id"] in selected and (not set(h["requires"]) <= selected or set(h["excludes"]) & selected)
               for h in hunks):
            continue
        captures, chosen, valid = {}, [], True
        for hunk, choice in zip(hunks, choices, strict=True):
            if choice is None:
                continue
            position, values = choice
            if any(k in captures and captures[k] != v for k, v in values.items()):
                valid = False
                break
            captures.update(values)
            chosen.append((hunk, position))
        if not valid:
            continue
        for (a, x), (b, y) in itertools.combinations(chosen, 2):
            if a["file"] != b["file"]:
                continue
            ae, be = x + len(a["old"]), y + len(b["old"])
            conflict = (
                (x < be and y < ae) if ae > x and be > y
                else ((y <= x <= be) if ae == x else (x <= y <= ae))
            )
            if conflict:
                valid = False
                break
        if not valid:
            continue
        positions = {h["id"]: p for h, p in chosen}
        if any(a in selected and b in selected and not (low <= positions[b] - positions[a] <= high)
               for a, b, low, high in data["distances"]):
            continue
        if any(isinstance(token, dict) and token["use"] not in captures for h, _ in chosen for token in h["new"]):
            continue
        weight = sum(h["weight"] for h, _ in chosen)
        displacement = sum(abs(p - h["expected"]) for h, p in chosen)
        signature = tuple((h["id"], p) for h, p in chosen)
        key = (-weight, -len(chosen), displacement, signature, tuple(sorted(captures.items())))
        if best is not None and best[0] <= key:
            continue
        outputs = {}
        for name, lines in data["files"].items():
            output, cursor = [], 0
            for hunk, pos in sorted(((h, p) for h, p in chosen if h["file"] == name), key=lambda x: x[1]):
                output.extend(lines[cursor:pos])
                output.extend(token if isinstance(token, str) else captures[token["use"]] for token in hunk["new"])
                cursor = pos + len(hunk["old"])
            output.extend(lines[cursor:])
            outputs[name] = output
        best = (key, {"weight": weight, "displacement": displacement, "files": outputs,
                      "applied": [{"id": h["id"], "position": p} for h, p in chosen],
                      "rejected": [h["id"] for h in hunks if h["id"] not in selected],
                      "bindings": captures})
    return best[1]


PROMPT497 = """
Подбери глобально оптимальный пакет context patches с ОБЩИМИ captures.
Вход {files:{filename:[строки]},hunks,distances}. <=4 files, <=16 lines/file,
<=9 hunks. Hunk: {id,file,old:[token],new:[token],expected:integer,
weight>=0,requires:[hunk_id],excludes:[hunk_id]}.
Old token — literal string либо {capture:NAME}; new token — literal string
либо {use:NAME}. Capture связывает ОДНУ целую исходную строку. Одинаковое имя
capture во ВСЕХ выбранных hunks должно иметь одно значение. New use может
использовать capture другого выбранного hunk. Если capture не связан,
данный выбор недопустим. Распознавание идёт в неизменном исходном файле.
Каждый hunk можно выбрать0 или1 раз, в одной из matching positions 0-based.
Для каждого hunk гарантированно<=5 matching positions. Old может быть пустым;
тогда совпадение в любом boundary0..len(file), ограничение<=5 всё ещё действует.
Selected requires должны быть selected; selected excludes запрещены.
Distances — [idA,idB,min,max], действует ЕСЛИ ОБА выбраны: min<=posB-posA<=max.
Даже для разных files позиции сравниваются как числа. requires задаётся отдельно.

В одном file непустые old intervals [p,p+len(old)) не пересекаются.
Insertion [p,p] конфликтует с replacement [a,b), если a<=p<=b (ОБЕ границы);
две insertion конфликтуют при равных positions. Для разных files conflicts нет.
Все выбранные patches применяются одновременно в original coordinates,
по positions слева направо; новые строки не могут дать match другому hunk.
Оптимизируй (-sum_weight,-число_selected,sum abs(pos-expected),
список(id,pos) по id,список(capture,value) по capture).
Вывод {weight,displacement,files:полная_новая_карта,
applied:[{id,position}],rejected:[id],bindings:{capture:строка}}.
applied/rejected по id. В bindings только captures выбранных old patterns.
Пустой пакет допустим. Локальное nearest-match может потерять совместимый
capture или нужную пару dependency hunks; не фиксируй bindings жадно.
"""


def binding498(data):
    from itertools import product

    bindings = sorted(data["bindings"], key=lambda b: b["id"])
    refs = sorted(data["references"], key=lambda r: r["id"])
    parents = data["scopes"]

    def lookup(scope, name, names):
        while scope is not None:
            candidates = [b["symbol"] for b in bindings if b["scope"] == scope and names[b["id"]] == name]
            if candidates:
                return candidates[0]
            scope = parents[scope]
        return None

    original = {b["id"]: b["old"] for b in bindings}
    wanted = {ref["id"]: lookup(ref["scope"], ref["old"], original) for ref in refs}
    best = None
    for choices in product(*(b["options"] for b in bindings)):
        names = {b["id"]: name for b, name in zip(bindings, choices, strict=True)}
        occupied = [(b["scope"], names[b["id"]]) for b in bindings if names[b["id"]] is not None]
        if len(set(occupied)) != len(occupied):
            continue
        if any(lookup(contract["scope"], contract["name"], names) != contract["symbol"]
               for contract in data["public"]):
            continue
        # Export destinations are a simultaneous namespace mapping; exact full names only.
        exports = {}
        valid = True
        for export in data["exports"]:
            local = names[export["binding"]]
            if local is None:
                valid = False
                break
            path = data["module_map"].get(export["module"], export["module"]) + "." + local
            if path in exports and exports[path] != export["symbol"]:
                valid = False
                break
            exports[path] = export["symbol"]
        if not valid:
            continue
        ref_names = {}
        ref_cost = 0
        for ref in refs:
            allowed = [name for name in ref["options"]
                       if lookup(ref["scope"], name, names) == wanted[ref["id"]]]
            if not allowed:
                valid = False
                break
            name = min(allowed, key=lambda x: (0 if x == ref["old"] else ref["cost"], x != ref["old"], x))
            ref_names[ref["id"]] = name
            ref_cost += 0 if name == ref["old"] else ref["cost"]
        if not valid:
            continue
        declaration_cost = sum(
            b["cost"] if names[b["id"]] != b["old"] else 0 for b in bindings
        )
        enabled_alias_cost = sum(
            b["alias_cost"] for b in bindings if b["old"] is None and names[b["id"]] is not None
        )
        cost = declaration_cost + enabled_alias_cost + ref_cost
        changed = sum(names[b["id"]] != b["old"] for b in bindings) + sum(ref_names[r["id"]] != r["old"] for r in refs)
        signature = tuple("" if name is None else name for name in choices)
        key = (cost, changed, signature, tuple(ref_names[r["id"]] for r in refs))
        if best is None or key < best[0]:
            best = (key, {"feasible": True, "cost": cost, "changed": changed,
                          "bindings": [{"id": b["id"], "name": names[b["id"]]} for b in bindings],
                          "references": [{"id": r["id"], "name": ref_names[r["id"]]} for r in refs],
                          "exports": exports})
    return best[1] if best else {"feasible": False}


PROMPT498 = """
Спланируй simultaneous namespace migration по lexical binding graph.
Нужно сохранить SYMBOL ID каждого использования, выбрать совместимые local
names/alias shims и избежать захвата имён во вложенных scopes. Файлы содержат
формальный frontend IR, а не неполный синтаксический анализ Python.
Вход {scopes,bindings,references,public,exports,module_map}.
scopes — scope_id->parent_or_null, лес без циклов. <=12 bindings:
{id,scope,symbol,old:name_or_null,options:[name_or_null],cost,alias_cost}.
Identifiers ASCII непустые, null означает отключённое объявление; options1..3.
Все обычные bindings old!=null имеют alias_cost=0; optional shim имеет old=null.
Разные bindings могут обозначать ОДИН symbol. Исходно нет двух одинаковых
ненулевых имён в одном scope. Имя разрешается к ближайшему binding с этим
name, начиная с текущего scope и поднимаясь по parents. Shadowing допускается.

References {id,scope,old:name,options:[name],cost}; исходный old гарантированно
разрешается к существующему symbol. После правки каждый ref ОБЯЗАН разрешаться
к тому же symbol, хотя другой alias-binding этого symbol подходит. Options
ref непустые, <=4. Не переписывай refs независимо от changed declarations:
локальный rename может захватить неизменённое использование из child scope.
Public contracts {scope,name,symbol} требуют lookup этого ТОЧНОГО name к symbol
после изменения. Public contract может нуждаться во включении alias shim.
Bindings с null вообще отсутствуют. В одном scope не может быть двух одинаковых
включённых names, ДАЖЕ если symbols одинаковы.

Exports {module,binding,symbol}: binding обязан остаться включён, его symbol
гарантированно совпадает с заданным. Новый export path =
module_map.get(old_module,old_module)+"."+новое имя binding. Module mapping
ОДНОШАГОВЫЙ и одновременный: A->B,B->C не превращает A в C. Exact names,
никаких prefix substitutions. Два exports одного path допускаются только
при одинаковом symbol. Explicit public names не меняются через module_map.

Cost: за каждое binding name!=old прибавить binding.cost; дополнительно за
включённый shim(old=null,new!=null) alias_cost. За ref name!=old прибавить
ref.cost. Все costs>=0. Changed — число изменённых bindings + references,
shim активация одно изменение. Оптимизируй (cost,changed,список новых binding
names по binding id,список новых ref names по ref id), null в сравнении names
считать пустой строкой (настоящие names непустые).
Вывод {feasible:false} либо {feasible:true,cost,changed,bindings:[{id,name}],
references:[{id,name}],exports:{full_path:symbol}}; массивы по id.
"""


def rename499(data):
    from itertools import combinations

    locations = sorted(data["locations"])
    objects = sorted(data["objects"])
    loc = data["locations"]
    sizes = {name: data["objects"][name]["size"] for name in objects}
    initial = tuple(data["objects"][name]["start"] for name in objects)
    target = tuple(data["objects"][name]["target"] for name in objects)
    horizon = len(data["network"])

    def scratch(state):
        return sum(sizes[obj] for obj, place in zip(objects, state, strict=True) if loc[place]["scratch"])

    layer = {initial: [(0, scratch(initial), ())]}
    for time in range(horizon + 1):
        if target in layer:
            transfer, peak, plan = min(layer[target])
            return {"feasible": True, "rounds": time, "transfer": transfer,
                    "scratch_peak": peak, "plan": [
                        [{"from": source, "to": destination} for source, destination in group] for group in plan
                    ], "final": {place: obj for obj, place in zip(objects, target, strict=True)}}
        if time == horizon:
            break
        following = {}
        for state, labels in layer.items():
            occupied = set(state)
            options = []
            for i, (obj, source) in enumerate(zip(objects, state, strict=True)):
                destinations = {data["objects"][obj]["target"]} | {p for p in locations if loc[p]["scratch"]}
                for destination in sorted(destinations - occupied):
                    if sizes[obj] > loc[destination]["max_size"]:
                        continue
                    if [source, destination] not in data["edges"]:
                        continue
                    if source in data["closed"][time] or destination in data["closed"][time]:
                        continue
                    options.append((i, source, destination))
            for count in range(min(data["slots"], len(options)) + 1):
                for group in combinations(options, count):
                    if len({i for i, _, _ in group}) != count or len({p for _, _, p in group}) != count:
                        continue
                    transfer = sum(sizes[objects[i]] for i, a, b in group if loc[a]["device"] != loc[b]["device"])
                    if transfer > data["network"][time]:
                        continue
                    new = list(state)
                    for i, _, destination in group:
                        new[i] = destination
                    staging = {}
                    for i, source, destination in group:
                        if loc[source]["device"] != loc[destination]["device"]:
                            device = loc[destination]["device"]
                            staging[device] = staging.get(device, 0) + sizes[objects[i]]
                    used = {device: 0 for device in data["capacity"]}
                    for obj, place in zip(objects, state, strict=True):
                        used[loc[place]["device"]] += sizes[obj]
                    if any(used[d] + staging.get(d, 0) > cap for d, cap in data["capacity"].items()):
                        continue
                    scratch_peak = scratch(state) + sum(sizes[objects[i]] for i, _, p in group if loc[p]["scratch"])
                    if scratch_peak > data["scratch_limit"]:
                        continue
                    new = tuple(new)
                    plan_group = tuple(sorted((a, b) for _, a, b in group))
                    bucket = following.setdefault(new, [])
                    for cost, peak, plan in labels:
                        candidate = cost + transfer, max(peak, scratch_peak), plan + (plan_group,)
                        if bucket and candidate[0] > bucket[0][0]:
                            continue
                        if bucket and candidate[0] < bucket[0][0]:
                            bucket.clear()
                        if any(old[1] <= candidate[1] and old[2] <= candidate[2] for old in bucket):
                            continue
                        bucket[:] = [old for old in bucket if not (candidate[1] <= old[1] and candidate[2] <= old[2])]
                        bucket.append(candidate)
        layer = following
    return {"feasible": False}


PROMPT499 = """
Спланируй rename/move с несколькими scratch slots, devices, COW staging и
календарём доступности. Вход {locations,objects,edges,capacity,network,closed,
slots,scratch_limit}. <=8 locations: {name:{device,scratch:bool,max_size}}.
<=5 objects {object_id:{start,target,size>=0}}, initial starts уникальны,
targets уникальны; starts/targets могут быть scratch. Initial occupancy
допустимо: size<=max_size, device usage<=capacity, scratch sum<=scratch_limit.
H=len(network)=1..9, closed[t] — unavailable locations этого round, slots1..3.
Edges — допустимые directed [source,destination], exact names, без implicit edges.
В round можно выбрать0..slots moves (пустой round разрешён для ожидания).
Каждый source занят в НАЧАЛЕ round, каждый destination СВОБОДЕН в НАЧАЛЕ
round; sources/destinations уникальны. Нельзя засунуть cycle в один atomic round.
Object можно переместить только в его target либо любой scratch location,
включая промежуточный scratch->scratch. Target другого object не является
доступным временным местом, если сам не scratch. Source/destination не closed[t],
size<=destination.max_size. Закрытая location может продолжать хранить object.

Same-device move — metadata, transfer0, новой физической allocation нет.
Cross-device move копирует size bytes, затем удаляет source при commit.
sum cross-device bytes<=network[t]. На КАЖДОМ device требуются
start_bytes+sum входящих cross-device allocations<=capacity[device]:
outgoing bytes нельзя освободить заранее. Commit одновременно переносит
identities; source исчезает, copies между rounds не остаются.
Scratch reservation отдельно: start_scratch_bytes + sizes ВСЕХ incoming
scratch moves<=scratch_limit, даже same-device; outgoing scratch освобождаются
только на commit. Это reservation quota, не физический disk usage.
Peak включает initial scratch и эти round reservation значения.

Остановиться при первом точном размещении всех objects в targets.
Оптимизируй (число rounds,total transferred bytes,maximum scratch reservation,
plan). Plan — список rounds, round — пары(source,destination), отсортированные
lex; пустой round — []; lex comparison shorter-prefix-first.
При невозможности за H rounds {feasible:false}. Иначе {feasible:true,rounds,
transfer,scratch_peak,plan:[[{from,to},...],...],final:{target:object_id}}.
Промежуточно больший scratch peak не всегда хуже при будущем общем high peak:
для последнего lex tie нельзя терять нужный prefix.
"""


# Fixed-seed suites mix independently checked tiny anchors with interacting
# instances. "Hard" is an empirical claim deferred to calibration.
def retry_job(name, reward=1, result="ok", **kwargs):
    return {"id": name, "release": 0, "deadline": 4, "reward": reward,
            "responses": [{"duration": 1, "result": result, "delay": 0, "freeze": 0}], **kwargs}


def retry_case(jobs, **kwargs):
    return {"horizon": 4, "capacity": 1, "period": 2, "slots": 1, "jobs": jobs, **kwargs}


R486 = random.Random(48604)
EXTRA486 = [
    retry_case([
        retry_job("a", 2, responses=[{"duration": 1, "result": "retry", "delay": 0, "freeze": 4},
                                   {"duration": 1, "result": "ok", "delay": 0, "freeze": 0}]),
        retry_job("b", 9, release=1, deadline=3),
    ], capacity=2, slots=2),
    retry_case([retry_job("a", 0), retry_job("b", 0), retry_job("c", 0)], capacity=2, slots=2),
]
for seed in range(12):
    jobs = []
    for i in range(4 + (seed == 11)):
        responses = [{"duration": R486.randint(1, 3),
                      "result": R486.choice(["retry", "retry", "fatal", "ok"]),
                      "delay": R486.randint(0, 2), "freeze": R486.randint(0, 3)}
                     for _ in range(2)]
        responses[-1]["result"] = "ok"
        jobs.append(retry_job(chr(97 + i), R486.randint(0, 15), release=i % 2,
                              deadline=8 + i % 2, responses=responses))
    EXTRA486.append(retry_case(jobs, horizon=9, capacity=1 + seed % 3,
                              period=1 + seed % 3, slots=1 + seed % 2))
program(486, "retry_dispatcher", "Optimize shared retries under global freezes", 359,
        PROMPT486, optimal486,
        [(retry_case([]), {"reward": 0, "completed": 0, "attempts": 0, "starts": []}),
         (retry_case([retry_job("a", 3)]), {"reward": 3, "completed": 1, "attempts": 1,
                                          "starts": [{"time": 0, "id": "a"}]})], EXTRA486)


def stochastic_job(name, reward=4, p=None, **kwargs):
    return {"id": name, "reward": reward, "release": 0, "deadline": 3,
            "attempts": [{"duration": 1, "energy": 1, "delay": 0,
                          "p": p if p is not None else [[1, 1], [0, 1]]}], **kwargs}


def stochastic_case(jobs, **kwargs):
    return {"horizon": 3, "energy": 3, "prior": [[1, 2], [1, 2]],
            "jobs": jobs, "probes": [], **kwargs}


EXTRA487 = []
R487 = random.Random(48704)
for seed in range(12):
    jobs = []
    for i in range(3):
        attempts = [{"duration": 1 + ((seed + i + k) % 3 == 0), "energy": 1,
                     "delay": (seed + i + k) % 2,
                     "p": [[R487.randint(0, 4), 4], [R487.randint(0, 4), 4]]}
                    for k in range(2)]
        jobs.append(stochastic_job(chr(97 + i), 3 + R487.randrange(12),
                                   deadline=5 + i % 2, attempts=attempts))
    EXTRA487.append(stochastic_case(jobs, horizon=6, energy=4 + seed % 2,
                                    prior=[[1, 3], [2, 3]],
                                    probes=[{"id": "diagnose", "duration": 1, "energy": seed % 2,
                                             "likelihood": [[[3, 4], [1, 4]], [[1, 4], [3, 4]]]}]))
program(487, "adaptive_retry_policy", "Infer a hidden shared retry regime", 359,
        PROMPT487, bayesian487,
        [(stochastic_case([], horizon=1), {"value": [0, 1], "first": "wait"}),
         (stochastic_case([stochastic_job("a", 6)], horizon=1),
          {"value": [3, 1], "first": "job:a"})], EXTRA487)


def archive_file(name, path, weight=1, size=1):
    return {"id": name, "path": path, "type": "file", "weight": weight, "size": size}


def archive_link(name, path, target, weight=1, kind="sym"):
    return {"id": name, "path": path, "type": kind, "weight": weight, "target": target}


EXTRA488 = []
R488 = random.Random(48804)
for seed in range(12):
    entries = [
        archive_file("a", "pkg/data", 2 + R488.randrange(8), 2),
        archive_file("b", "PKG/DATA. ", 2 + R488.randrange(8), 3),
        archive_file("c", "pkg", 4 + R488.randrange(8), 1),
        archive_file("d", "café", 2 + R488.randrange(6), 3),
        archive_file("e", "cafe\u0301", 2 + R488.randrange(6), 1),
        archive_link("f", "links/a", "../pkg/data", 4),
        archive_link("g", "links/b", "links/a", 3, "hard"),
        archive_link("h", "loop/a", "b", 20),
        archive_link("i", "loop/b", "a", 20),
        archive_link("j", "bad%2f..%2fescape", "../outside", 100),
        archive_file("k", "CON.txt" if seed % 2 else "100%2525", 9, 1),
    ]
    R488.shuffle(entries)
    EXTRA488.append({"entries": entries, "disk": 3 + seed % 5,
                     "required": ["g"] if seed % 4 == 0 else []})
program(488, "archive_namespace_audit", "Select an optimal safe archive namespace", 360,
        PROMPT488, archive488,
        [({"entries": [], "disk": 0, "required": []},
          {"feasible": True, "weight": 0, "disk": 0, "selected": [], "resolved": []}),
         ({"entries": [archive_file("a", "X", 3, 2)], "disk": 2, "required": ["a"]},
          {"feasible": True, "weight": 3, "disk": 2, "selected": ["a"],
           "resolved": [{"id": "a", "path": "x", "file": "a"}]})], EXTRA488)


def migration_case(seed=0):
    worlds = ["fast", "bad-app", "bad-index"]
    initial = {"live/app": "old", "live/index": "i0", "backup": "old"}
    target = {"live/app": "new", "live/index": "i1"}

    def action(name, guards, cost, writes, fail=None):
        outcomes = {w: {"label": "ok", "writes": writes} for w in worlds}
        if fail is not None:
            outcomes[fail] = {"label": "failed", "writes": {}}
        return {"id": name, "guards": guards, "cost": cost, "outcomes": outcomes}

    actions = [
        action("app-cheap", {"stage/app": None}, 1, {"stage/app": "new"}, "bad-app"),
        action("app-safe", {"stage/app": None}, 5 + seed % 2, {"stage/app": "new"}),
        action("index-cheap", {"stage/index": None}, 1, {"stage/index": "i1"}, "bad-index"),
        action("index-safe", {"stage/index": None}, 4 + seed % 3, {"stage/index": "i1"}),
        action("commit", {"stage/app": "new", "stage/index": "i1"}, 1,
               {"live/app": "new", "live/index": "i1", "backup": None,
                "stage/app": None, "stage/index": None}),
        action("purge", {"backup": "old"}, 0, {"backup": None}),
        action("unsafe-delete", {"live/app": "old"}, 0, {"live/app": None}),
        {"id": "probe", "guards": {}, "cost": seed % 3,
         "outcomes": {w: {"label": w, "writes": {}} for w in worlds}},
        action("bundle", {"stage/app": None, "stage/index": None}, 7 + seed % 2,
               {"stage/app": "new", "stage/index": "i1"}),
    ]
    return {"worlds": worlds, "initial": initial, "target": target,
            "sizes": {"old": 3, "new": 4, "i0": 2, "i1": 3}, "protected": [],
            "invariants": [[["live/app", "old"], ["live/app", "new"]],
                           [["live/index", "i0"], ["live/index", "i1"]]],
            "disk": 19 + seed % 4, "max_rounds": 3 + seed % 4, "actions": actions}


_tiny489 = {"worlds": ["one"], "initial": {"x": "a"}, "target": {"x": "b"},
            "sizes": {"a": 1, "b": 1}, "protected": [], "invariants": [], "disk": 2,
            "max_rounds": 1, "actions": [{"id": "write", "guards": {"x": "a"}, "cost": 3,
                                         "outcomes": {"one": {"label": "ok", "writes": {"x": "b"}}}}]}
artifact(489, "atomic_tree_migration", "Synthesize a contingent safe migration policy", 360,
         PROMPT489, contingent489,
         [(_tiny489, {"feasible": True, "cost": [3, 1], "steps": [1, 1],
                     "policy": {"action": "write", "branches": [{"label": "ok", "next": {"done": True}}]}}),
          ({**_tiny489, "disk": 1}, {"feasible": False})],
         [migration_case(i) for i in range(8)])


def causal_event(name, actor, count, context, *, group=None, weight=1, key="doc", op="put", value=1):
    return {"id": name, "group": group or name, "weight": weight, "dot": [actor, count],
            "context": context, "key": key, "op": op, "value": value}


def causal_case(events, extra=()):
    return {"lines": [json.dumps(e) for e in events] + list(extra)}


EXTRA490 = []
R490 = random.Random(49004)
for seed in range(12):
    events = [
        causal_event("a0", "a", 1, {}, weight=R490.randrange(1, 7)),
        causal_event("a1", "a", 1, {"b": 1}, weight=R490.randrange(1, 7)),
        causal_event("b0", "b", 1, {}, weight=R490.randrange(1, 7)),
        causal_event("b1", "b", 1, {"a": 1}, weight=R490.randrange(1, 7)),
        causal_event("c0", "c", 1, {}, key="other"),
        causal_event("a2", "a", 2, {"a": 1, "b": 1}, group="joint", weight=7, value=3),
        causal_event("b2", "b", 2, {"b": 1, "a": 1}, group="joint", weight=3,
                     op="delete", value=None),
        causal_event("c1", "c", 2, {"c": 1, "a": 2, "b": 2}, weight=4, value=9),
        causal_event("fork", "c", 2, {"c": 1}, weight=8 + seed % 3, value=4),
        causal_event("missing", "d", 2, {"d": 1}, weight=100),
        causal_event("closure", "d", 1, {"a": 2}, weight=30),
    ]
    R490.shuffle(events)
    EXTRA490.append(causal_case(events, ('{"nested":{"x":1,"x":2}}', "NaN") if seed % 2 else ()))
program(490, "causal_jsonl_reducer", "Repair atomically grouped causal JSONL histories", 361,
        PROMPT490, causal490,
        [(causal_case([]), {"selected": [], "rejected": [], "weight": 0, "order": [],
                           "documents": [], "invalid_lines": []}),
         (causal_case([causal_event("a", "r", 1, {}, weight=2)]),
          {"selected": ["a"], "rejected": [], "weight": 2, "order": ["a"],
           "documents": [{"key": "doc", "frontier": ["a"], "winner": "a",
                          "deleted": False, "value": 1}], "invalid_lines": []})], EXTRA490)


def transaction(name, ops, reward=1, **kwargs):
    return {"id": name, "start": 0, "end": 10, "reward": reward, "requires": [], "ops": ops, **kwargs}


def get(key, value, exists=True):
    return {"op": "get", "key": key, "seen": {"exists": exists, "value": value}}


EXTRA491 = []
R491 = random.Random(49104)
for seed in range(12):
    transactions = [
        transaction("a", [get("x", 0), {"op": "put", "key": "y", "value": 1}], R491.randrange(1, 8)),
        transaction("b", [get("y", 0), {"op": "put", "key": "x", "value": 1}], R491.randrange(1, 8)),
        transaction("c", [{"op": "scan", "low": "p", "high": "z", "seen": {"x": 1, "y": 0}},
                          {"op": "put", "key": "q", "value": 2}], 4),
        transaction("d", [{"op": "save", "name": "s"}, get("q", 99),
                          {"op": "put", "key": "x", "value": 99}, {"op": "rollback", "name": "s"},
                          get("x", 1), {"op": "delete", "key": "y"}], 3 + seed % 3),
        transaction("e", [get("y", None, False), {"op": "put", "key": "y", "value": None}], 2),
        transaction("f", [get("y", None), {"op": "put", "key": "z", "value": True}], 5,
                    requires=["d"] if seed % 3 == 0 else []),
        transaction("g", [get("z", 1), {"op": "put", "key": "x", "value": 5}], 100),
    ]
    if seed % 2:
        transactions[0]["end"] = 2
        transactions[1]["start"] = 3
    EXTRA491.append({"initial": {"x": 0, "y": 0}, "required": ["a"] if seed % 4 == 0 else [],
                     "transactions": transactions})
program(491, "transaction_jsonl_replay", "Recover a maximal serializable transaction history", 361,
        PROMPT491, serial491,
        [({"initial": {}, "required": [], "transactions": []},
          {"feasible": True, "reward": 0, "selected": [], "order": [], "data": {}}),
         ({"initial": {}, "required": ["a"], "transactions": [
             transaction("a", [{"op": "put", "key": "x", "value": 1}], 2)]},
          {"feasible": True, "reward": 2, "selected": ["a"], "order": ["a"], "data": {"x": 1}})],
        EXTRA491)


def bundle(name, edges, cost=1, requires=(), excludes=()):
    return {"id": name, "edges": edges, "cost": cost, "requires": list(requires), "excludes": list(excludes)}


def dependency_case(bundles, **kwargs):
    return {"nodes": ["a", "b", "c", "d", "e"], "priority": dict.fromkeys("abcde", 0),
            "required": [], "bundles": bundles, "quotas": [], "must_reach": [],
            "must_not_reach": [], **kwargs}


EXTRA492 = []
R492 = random.Random(49204)
for seed in range(12):
    bundles = [
        bundle("ab", [["a", "b"]], 2),
        bundle("bc", [["b", "c"]], 3, requires=["ab"]),
        bundle("ca", [["c", "a"]], 4),
        bundle("cd", [["c", "d"]], 2, excludes=["de"]),
        bundle("de", [["d", "e"]], 3),
        bundle("ad", [["a", "d"], ["b", "e"]], 3),
        bundle("ea", [["e", "a"]], 2),
    ]
    for i in range(4 + (seed == 11)):
        a, b = R492.sample(list("abcde"), 2)
        bundles.append(bundle(f"r{i}", [[a, b]], R492.randrange(5)))
    EXTRA492.append(dependency_case(
        bundles, quotas=[{"ids": ["cd", "de", "ad"], "min": 1, "max": 2}],
        must_reach=[["a", "e"]] if seed % 2 else [["a", "c"]],
        must_not_reach=[["d", "b"]] if seed % 3 else [["e", "c"]],
        priority={n: (i + seed) % 3 for i, n in enumerate("abcde")},
    ))
program(492, "weighted_dependency_repair", "Repair coupled dependency bundles and reachability", 362,
        PROMPT492, dependency492,
        [(dependency_case([]), {"feasible": True, "cost": 0, "drop": [],
                               "order": list("abcde"), "reach": []}),
         (dependency_case([bundle("ab", [["a", "b"]]), bundle("ba", [["b", "a"]])]),
          {"feasible": True, "cost": 1, "drop": ["ab"], "order": list("bacde"),
           "reach": [["b", "a"]]})], EXTRA492)


def schedule_job(name, deps=(), **kwargs):
    return {"id": name, "release": 0, "deadline": 8, "bytes": 2, "locks": [],
            "deps": [{"id": dep, "min": 0, "max": 4} for dep in deps],
            "modes": [{"id": "fast", "duration": 1, "demand": [2, 1], "power": 4},
                      {"id": "slow", "duration": 2, "demand": [1, 1], "power": 1}], **kwargs}


SCHEDULES493 = []
for seed in range(8):
    jobs = [schedule_job("a", bytes=2 + seed % 2),
            schedule_job("b", ["a"], locks=["build"]),
            schedule_job("c", ["a"], locks=["build"] if seed % 2 else []),
            schedule_job("d", ["b", "c"], bytes=0),
            schedule_job("e", ["b"], bytes=0, release=seed % 3)]
    SCHEDULES493.append({"capacity": [[3, 2] if t != 2 + seed % 3 else [1, 1] for t in range(8)],
                         "storage": [3 + seed % 3] * 8, "tariff": [5, 4, 3, 8, 1, 2, 1, 1],
                         "jobs": jobs})
_tiny493 = {"capacity": [[1, 1], [1, 1]], "storage": [0, 0], "tariff": [2, 1],
            "jobs": [schedule_job("a", deadline=2, bytes=0,
                                  modes=[{"id": "one", "duration": 1, "demand": [1, 1], "power": 3}])]}
artifact(493, "calendar_dependency_schedule", "Schedule multimode jobs with retained artifacts", 362,
         PROMPT493, schedule493,
         [(_tiny493, {"feasible": True, "makespan": 1, "energy": 6, "storage_peak": 0,
                     "schedule": [{"id": "a", "start": 0, "end": 1, "mode": "one"}]}),
          ({**_tiny493, "capacity": [[0, 0], [0, 0]]}, {"feasible": False})], SCHEDULES493)


def literal(value):
    return {"op": "lit", "value": value}


def variable(name):
    return {"op": "var", "name": name}


def reference(name):
    return {"op": "ref", "key": literal(name)}


def expression(op, *args):
    return {"op": op, "args": list(args)}


def configuration_case(seed=0):
    variables = {
        name: [{"value": value, "cost": (value * (i + 2) + seed) % 7} for value in range(3)]
        for i, name in enumerate("abcdef")
    }
    nodes = {
        "total": expression("add", *(variable(v) for v in variables)),
        "route": {"op": "if", "cond": expression("eq", variable("a"), literal(0)),
                  "then": reference("loop"), "else": {"op": "ref", "key": expression(
                      "concat", literal("backend"), {"op": "if", "cond": expression("eq", variable("b"), literal(1)),
                                                    "then": literal("1"), "else": literal("2")})}},
        "loop": reference("route"),
        "backend1": expression("add", variable("c"), literal(2)),
        "backend2": expression("mul", variable("d"), literal(2)),
        "safe": expression("coalesce", reference("removed"), literal(None)),
        "typed": expression("eq", literal(True), literal(1)),
    }
    return {
        "variables": variables, "baseline": {name: (i + seed) % 3 for i, name in enumerate(variables)},
        "layers": [{"id": "base", "nodes": {**nodes, "removed": literal("obsolete")}},
                   {"id": "override", "nodes": {"removed": {"delete": True},
                                                "backend1": expression("add", variable("c"), literal(3))}}],
        "outputs": ["route", "total", "safe", "typed"],
        "assertions": [
            expression("le", literal(5 + seed % 3), reference("total")),
            expression("le", reference("total"), literal(7 + seed % 2)),
            expression("eq", expression("add", variable("c"), variable("e")), literal(2 + seed % 2)),
            expression("le", variable("f"), reference("route")),
            expression("not", expression("eq", variable("b"), variable("d"))),
        ],
    }


_empty494 = {"variables": {}, "baseline": {}, "layers": [], "outputs": [], "assertions": []}
_tiny494 = {**_empty494, "layers": [{"id": "base", "nodes": {"x": literal(1)}}], "outputs": ["x"]}
EXTRA494 = [configuration_case(i) for i in range(12)]
EXTRA494.append({**_empty494, "layers": [{"id": "zero", "nodes": {
    "x": expression("eq", literal([-0.0]), literal([0.0])),
    "y": expression("eq", literal([True]), literal([1])),
}}], "outputs": ["x", "y"]})
program(494, "config_provenance_interpolation", "Optimize lazy configuration resolution and provenance", 363,
        PROMPT494, configuration494,
        [(_empty494, {"feasible": True, "cost": 0, "changed": 0, "choices": {},
                     "resolved": {}, "provenance": {}}),
         (_tiny494, {"feasible": True, "cost": 0, "changed": 0, "choices": {},
                    "resolved": {"x": 1}, "provenance": {"x": ["base"]}})], EXTRA494)


def parent_edge(name, parent, cost=1, optional=True):
    return {"id": name, "parent": parent, "cost": cost, "optional": optional}


def profile(parents=(), ops=()):
    return {"parents": list(parents), "ops": list(ops)}


def profile_case(seed=0):
    profiles = {
        "X": profile(ops=[{"op": "set", "key": "flag", "value": 1},
                          {"op": "set", "key": "tags", "value": [1, True, 0.0]}]),
        "Y": profile(ops=[{"op": "set", "key": "flag", "value": 2},
                          {"op": "set", "key": "tags", "value": [2, False, -0.0]}]),
        "A": profile([parent_edge("AX", "X", 1 + seed % 3), parent_edge("AY", "Y", 2 + seed % 2)],
                     [{"op": "append", "key": "tags", "values": [True, 1.0]}]),
        "B": profile([parent_edge("BY", "Y", 2), parent_edge("BX", "X", 1 + seed % 4)],
                     [{"op": "remove", "key": "tags", "values": [False]}]),
        "Root": profile([parent_edge("RA", "A", 4), parent_edge("RB", "B", 3)]),
        "Unused1": profile([parent_edge("U1", "Unused2", seed % 3)]),
        "Unused2": profile([parent_edge("U2", "Unused1", 1)]),
    }
    return {"profiles": profiles, "queries": [
        {"id": "qa", "profile": "A", "ancestors": ["X"], "expect": {"flag": 1}, "absent": []},
        {"id": "qb", "profile": "B", "ancestors": ["Y"], "expect": {"flag": 2}, "absent": []},
        {"id": "qr", "profile": "Root", "ancestors": ["A", "B", "X", "Y"],
         "expect": {}, "absent": ["secret"]},
    ]}


_tiny495 = {"profiles": {"A": profile(ops=[{"op": "set", "key": "x", "value": 1}])},
            "queries": [{"id": "q", "profile": "A", "ancestors": [], "expect": {"x": 1}, "absent": []}]}
artifact(495, "c3_profile_resolution", "Repair shared C3 profiles under configuration contracts", 363,
         PROMPT495, profiles495,
         [(_tiny495, {"feasible": True, "cost": 0, "drop": [],
                     "results": [{"id": "q", "mro": ["A"], "config": {"x": 1}, "writers": {"x": "A"}}]}),
          ({**_tiny495, "queries": [{**_tiny495["queries"][0], "expect": {"x": True}}]},
           {"feasible": False})], [profile_case(i) for i in range(8)])


def merge_case(base, ours, theirs, **kwargs):
    return {"base": base, "ours": ours, "theirs": theirs, "weights": {"ours": 2, "theirs": 1},
            "required": [], "order": [], **kwargs}


EXTRA496 = []
R496 = random.Random(49604)
for seed in range(12):
    count = 4 + (seed == 11)
    names = [chr(97 + i) for i in range(count)]
    base = {name: {"name": f"old-{name}", "refs": [], "flags": {"n": 0}} for name in names}
    ours = {name: {"name": f"n-{i}", "refs": [names[(i + 1) % count]],
                   "flags": {"n": 1, "ours": True}} for i, name in enumerate(names)}
    theirs = {name: {"name": f"n-{(i + 1) % count}", "refs": [names[(i - 1) % count]],
                     "flags": {"n": 1 if seed == 11 else 2, "theirs": True}} for i, name in enumerate(names)}
    if seed % 3 == 0:
        theirs.pop(names[-1])
    edges = [
        {"before": names[i], "after": names[(i + 1) % count],
         "weight": R496.randrange(1, 5), "hard": seed % 4 == 1 and i == 0}
        for i in range(count)
    ]
    EXTRA496.append(merge_case(base, ours, theirs, weights={"ours": 1 + seed % 3, "theirs": 2},
                              required=names[:2], order=edges))
program(496, "structural_three_way_merge", "Resolve coupled structural, namespace and order conflicts", 364,
        PROMPT496, merge496,
        [(merge_case({}, {}, {}), {"feasible": True, "loss": 0, "records": [], "resolutions": []}),
         (merge_case({}, {"a": {"name": "A", "refs": []}}, {}),
          {"feasible": True, "loss": 0, "records": [{"id": "a", "value": {"name": "A", "refs": []}}],
           "resolutions": []})], EXTRA496)


def hunk(name, old, new, *, file="a", weight=1, expected=0, requires=(), excludes=()):
    return {"id": name, "file": file, "old": old, "new": new, "weight": weight,
            "expected": expected, "requires": list(requires), "excludes": list(excludes)}


EXTRA497 = []
for seed in range(12):
    files = {"a": ["BEGIN", "red", "END", "BEGIN", "blue", "END", "BEGIN", "red", "END"],
             "b": ["key", "blue", "value", "key", "red", "value"]}
    hunks = [
        hunk("capture-a", ["BEGIN", {"capture": "COLOR"}, "END"], ["A", {"use": "COLOR"}],
             weight=4 + seed % 3, expected=seed % 8, requires=["capture-b"]),
        hunk("capture-b", ["key", {"capture": "COLOR"}, "value"], [{"use": "COLOR"}],
             file="b", weight=3, expected=3 if seed % 2 else 0),
        hunk("broad", ["END", "BEGIN"], ["JOIN"], weight=4, expected=2 + seed % 3,
             excludes=["tail"]),
        hunk("tail", ["BEGIN", {"capture": "OTHER"}, "END"], [{"use": "COLOR"}, {"use": "OTHER"}],
             weight=3, expected=6, requires=["capture-a"]),
        hunk("small", ["red"], ["R"], weight=2, expected=1),
    ]
    EXTRA497.append({"files": files, "hunks": hunks,
                     "distances": [["capture-a", "capture-b", -4 + seed % 2, 4],
                                   ["capture-a", "tail", 3, 6]]})
program(497, "optimal_context_patch", "Unify captures while selecting global context patches", 364,
        PROMPT497, patch497,
        [({"files": {"a": []}, "hunks": [], "distances": []},
          {"weight": 0, "displacement": 0, "files": {"a": []}, "applied": [],
           "rejected": [], "bindings": {}}),
         ({"files": {"a": ["x"]}, "hunks": [hunk("one", ["x"], ["y"], weight=3)], "distances": []},
          {"weight": 3, "displacement": 0, "files": {"a": ["y"]},
           "applied": [{"id": "one", "position": 0}], "rejected": [], "bindings": {}})], EXTRA497)


def binding(name, scope, symbol, old, options, cost=1, alias_cost=0):
    return {"id": name, "scope": scope, "symbol": symbol, "old": old,
            "options": options, "cost": cost, "alias_cost": alias_cost}


def reference_site(name, scope, old, options, cost=1):
    return {"id": name, "scope": scope, "old": old, "options": options, "cost": cost}


def bindings_case(seed=0):
    bindings = [
        binding("A", "root", "SA", "a", ["b", "c"], 2),
        binding("B", "root", "SB", "b", ["a", "d"], 2),
        binding("ShimA", "root", "SA", None, [None, "a"], 1, 2 + seed % 3),
        binding("ShimB", "root", "SB", None, [None, "b"], 1, 1 + seed % 3),
        binding("Local", "child", "SL", "a", ["a", "d", "x"], 1 + seed % 4),
        binding("Nested", "deep", "SN", "d", ["d", "x"], 2),
        binding("Plugin", "plugin", "SP", "c", ["b", "c", "d"], 1),
        binding("ShimL", "child", "SL", None, [None, "z"], 1, 1),
    ]
    refs = [
        reference_site("r0", "root", "a", ["a", "b", "c"], 2),
        reference_site("r1", "child", "a", ["a", "d", "x", "z"], 2),
        reference_site("r2", "child", "b", ["a", "b", "d"], 4 + seed % 2),
        reference_site("r3", "deep", "b", ["a", "b", "d"], 3),
        reference_site("r4", "deep", "d", ["d", "x"], 1),
        reference_site("r5", "plugin", "c", ["b", "c", "d"], 1),
    ]
    return {"scopes": {"root": None, "child": "root", "deep": "child", "plugin": None},
            "bindings": bindings, "references": refs, "public": [
                {"scope": "root", "name": "a", "symbol": "SA"},
                *([{"scope": "child", "name": "a", "symbol": "SL"}] if seed % 2 else []),
            ], "exports": [{"module": "old", "binding": "A", "symbol": "SA"},
                           {"module": "plugin", "binding": "Plugin", "symbol": "SP"}],
            "module_map": {"old": "shared", "shared": "next", "plugin": "shared"}}


_tiny498 = {"scopes": {"root": None},
            "bindings": [binding("a", "root", "symbol", "old", ["new"])],
            "references": [reference_site("r", "root", "old", ["new"])],
            "public": [], "exports": [{"module": "a", "binding": "a", "symbol": "symbol"}],
            "module_map": {"a": "b", "b": "c"}}
ZERO498 = {
    "scopes": {"root": None},
    "bindings": [binding("a", "root", "S", "a", ["a"], 0),
                 binding("z", "root", "S", "z", ["z"], 0)],
    "references": [reference_site("r", "root", "z", ["a", "z"], 0)],
    "public": [], "exports": [], "module_map": {},
}
artifact(498, "simultaneous_namespace_migration", "Migrate lexical bindings without capture or export collision", 365,
         PROMPT498, binding498,
         [(_tiny498, {"feasible": True, "cost": 2, "changed": 2,
                     "bindings": [{"id": "a", "name": "new"}],
                     "references": [{"id": "r", "name": "new"}], "exports": {"b.new": "symbol"}}),
          ({**_tiny498, "public": [{"scope": "root", "name": "old", "symbol": "symbol"}]},
           {"feasible": False})], [bindings_case(i) for i in range(8)] + [ZERO498])


def rename_case(seed=0, count=4):
    names = [chr(97 + i) for i in range(count)]
    locations = {name: {"device": "disk" if i % 2 == 0 else "other",
                        "scratch": False, "max_size": 20} for i, name in enumerate(names)}
    locations.update({"t0": {"device": "disk", "scratch": True, "max_size": 3},
                      "t1": {"device": "other", "scratch": True, "max_size": 5}})
    objects = {name: {"start": name, "target": names[(i + 1) % count], "size": 1 + (seed + i) % 3}
               for i, name in enumerate(names)}
    return {"locations": locations, "objects": objects,
            "edges": [[a, b] for a in locations for b in locations if a != b],
            "capacity": {"disk": 7 + seed % 3, "other": 7 + seed % 2},
            "network": [3 + (seed + t) % 4 for t in range(7)],
            "closed": [["t0"] if t == seed % 4 else [] for t in range(7)],
            "slots": 2, "scratch_limit": 4 + seed % 3}


_tiny499 = {"locations": {"a": {"device": "d", "scratch": False, "max_size": 2},
                           "b": {"device": "d", "scratch": False, "max_size": 2}},
            "objects": {"x": {"start": "a", "target": "b", "size": 1}},
            "edges": [["a", "b"]], "capacity": {"d": 2}, "network": [0],
            "closed": [[]], "slots": 1, "scratch_limit": 0}
SCRATCH499 = {
    "locations": {name: {"device": "d", "scratch": name.startswith("t"), "max_size": 3}
                  for name in ["a", "b", "c", "t0", "t1"]},
    "objects": {"x": {"start": "t0", "target": "a", "size": 2},
                "y": {"start": "b", "target": "c", "size": 3}},
    "edges": [["t0", "a"], ["b", "t1"], ["t1", "c"]],
    "capacity": {"d": 5}, "network": [0, 0, 0], "closed": [[], [], []],
    "slots": 2, "scratch_limit": 3,
}
PARETO499 = {
    "locations": {name: {"device": "d", "scratch": name in ("b", "s"), "max_size": cap}
                  for name, cap in [("a", 8), ("b", 8), ("c", 8), ("s", 1), ("y", 5)]},
    "objects": {"x": {"start": "a", "target": "c", "size": 1},
                "y": {"start": "b", "target": "y", "size": 5},
                "z": {"start": "c", "target": "a", "size": 8}},
    "edges": [["a", "s"], ["s", "c"], ["b", "y"], ["c", "b"], ["b", "a"]],
    "capacity": {"d": 14}, "network": [0] * 5, "closed": [[] for _ in range(5)],
    "slots": 1, "scratch_limit": 9,
}
COW499 = {
    "locations": {name: {"device": device, "scratch": False, "max_size": 3}
                  for name, device in [("a", "d1"), ("b", "d2"), ("c", "d2"), ("d", "d1")]},
    "objects": {"x": {"start": "a", "target": "b", "size": 3},
                "y": {"start": "c", "target": "d", "size": 3}},
    "edges": [["a", "b"], ["c", "d"]], "capacity": {"d1": 3, "d2": 3},
    "network": [6], "closed": [[]], "slots": 2, "scratch_limit": 0,
}
program(499, "minimal_spill_rename", "Plan identity-preserving moves across constrained devices", 365,
        PROMPT499, rename499,
        [(_tiny499, {"feasible": True, "rounds": 1, "transfer": 0, "scratch_peak": 0,
                    "plan": [[{"from": "a", "to": "b"}]], "final": {"b": "x"}}),
         ({**_tiny499, "edges": []}, {"feasible": False})],
        [rename_case(i) for i in range(12)] + [SCRATCH499, PARETO499, COW499])
