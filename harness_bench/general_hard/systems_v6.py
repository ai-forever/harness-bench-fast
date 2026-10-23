"""Distributionally robust delayed retry control, preserving shared policies."""
from __future__ import annotations

import copy
import inspect
import textwrap

from .common import program_task
from .systems_v5 import control_case


def solve487(data):
    import json
    from fractions import Fraction
    from functools import lru_cache
    from itertools import product

    models = sorted(data["models"], key=lambda row: row["id"])
    actions = sorted(data["actions"], key=lambda row: row["id"])
    transitions = [[[Fraction(*p) for p in row] for row in model["transition"]] for model in models]
    width = len(models)

    def code(tree):
        return json.dumps(tree, sort_keys=True, separators=(",", ":"))

    def pack(weights):
        total = sum(weights.values())
        if not total:
            return ()
        return tuple((*state, (value/total).numerator, (value/total).denominator)
                     for state, value in sorted(weights.items()) if value)

    def prune(rows):
        unique = {}
        for vector, tree in rows:
            if vector not in unique or code(tree) < code(unique[vector]):
                unique[vector] = tree
        return tuple((vector, tree) for vector, tree in unique.items()
                     if not any(other != vector and all(a >= b for a, b in zip(other, vector, strict=True))
                                for other in unique))

    @lru_cache(None)
    def frontier(time, used, energy, beliefs):
        if time == data["horizon"]:
            rewards, chances = [], []
            for model, belief in zip(models, beliefs, strict=True):
                rewards.append(sum((Fraction(n, d)*reward for _, reward, _, n, d in belief), Fraction()))
                chances.append(sum((Fraction(n, d) for _, reward, _, n, d in belief
                                    if reward >= model["threshold"]), Fraction()))
            return ((tuple(rewards + chances), {"done": True}),)
        candidates = []
        available = [(None, {"id": "wait", "energy": 0})] + [
            (i, a) for i, a in enumerate(actions) if not used >> i & 1 and a["energy"] <= energy]
        for index, action in available:
            groups = {}
            for m, (model, belief) in enumerate(zip(models, beliefs, strict=True)):
                for mode, reward, pending, n, d in belief:
                    outcomes = model["outcomes"][action["id"]][mode] if index is not None else [
                        {"label": "_", "reward": 0, "p": [1, 1]}]
                    for outcome in outcomes:
                        for next_mode, chance in enumerate(transitions[m][mode]):
                            mass = Fraction(n, d) * Fraction(*outcome["p"]) * chance
                            if not mass:
                                continue
                            signal = action["id"] + ":" + outcome["label"] if index is not None else "_"
                            state = next_mode, reward + outcome["reward"], signal
                            buckets = groups.setdefault(pending, [{} for _ in models])
                            buckets[m][state] = buckets[m].get(state, Fraction()) + mass
            branches = []
            for observation, buckets in sorted(groups.items()):
                probability = tuple(sum(bucket.values()) for bucket in buckets)
                child = frontier(time + 1, used if index is None else used | 1 << index,
                                 energy-action["energy"], tuple(pack(bucket) for bucket in buckets))
                branches.append((observation, probability, child))
            for chosen in product(*(branch[2] for branch in branches)):
                vector = tuple(sum((branch[1][j % width] * child[0][j]
                                    for branch, child in zip(branches, chosen, strict=True)), Fraction())
                               for j in range(2*width))
                tree = {"action": action["id"], "branches": [
                    {"observation": branch[0], "next": child[1]}
                    for branch, child in zip(branches, chosen, strict=True)]}
                candidates.append((vector, tree))
        return prune(candidates)

    beliefs = tuple(pack({(i, 0, "_"): Fraction(*p) for i, p in enumerate(model["prior"])}) for model in models)
    feasible = [(vector, tree) for vector, tree in frontier(0, 0, data["energy"], beliefs)
                if all(vector[width+i] >= Fraction(*model["required_probability"]) for i, model in enumerate(models))]
    if not feasible:
        return {"feasible": False}

    def objective(row):
        vector, tree = row
        return -min(vector[:width]), -sum(vector[:width]), -min(vector[width:]), -sum(vector[width:]), code(tree)

    vector, tree = min(feasible, key=objective)
    return {"feasible": True, "models": [
        {"id": model["id"], "reward": [vector[i].numerator, vector[i].denominator],
         "success": [vector[width+i].numerator, vector[width+i].denominator]}
        for i, model in enumerate(models)], "policy": tree}


PROMPT = '''
Синтезируй ОДНУ distributionally robust delayed retry policy для неизвестной
постоянной модели среды. Вход {horizon:1..4,energy:0..6,actions,models}.
Actions <=4, {id,energy:0..3}; id уникальны, ASCII без двоеточия, не wait.
Models 1..2, уникальные ASCII id, каждая {id,prior,transition,outcomes,threshold,
required_probability}. Prior для ДВУХ hidden modes Z=0,1; transition2x2.
Outcomes отображает каждый action id в [row0,row1]; row 1..2 элементов
{label,reward:0..12,p:[num,den]}, label ASCII без двоеточия, уникальны в row,
union labels одного action по ВСЕМ modes/models <=2. Все probabilities точные
неотрицательные рациональные [num,den], den>0; prior/transition row/outcome row
суммируются в1. Threshold nonnegative integer, required_probability в[0,1].

Внешняя среда выбирает одну model M ДО начала, затем НЕ меняет её. У моделей
НЕТ заданных весов/априорного распределения: усреднять их как Bayesian mixture
НЕЛЬЗЯ. Policy не видит M или Z. Для каждого M начальный Z~M.prior.
В t=0 reward=0. Каждый action доступен максимум ОДИН раз за history; wait
всегда доступен, energy0,reward0,signal '_'. Один action в тик, общая energy
уменьшается на указанную стоимость, никогда не отрицательна.

Фазы тика t: policy видит только уже доставленные signals; выбирает action;
outcome сэмплируется по M.outcomes[action][Z_t], reward добавляется сейчас,
но reward НЕ наблюдается отдельно; Z_{t+1}~M.transition[Z_t] независимо от
outcome условно при (M,Z_t). Условно при полной mode trajectory outcomes
независимы. Затем в начале t+1 приходит signal действия t-1: задержка РОВНО2
от старта. Signal='ID:label'; для wait '_'. В начале t=0,t=1 signal '_'.
Mode эволюционирует и при wait. Поздние signals не дают дополнительных действий.
Политика обязана быть общей функцией OBSERVABLE history: одинаковые histories
в разных моделях требуют того же действия. Нельзя оптимизировать M отдельно
и склеить результаты. Не рандомизировать действия и не convexify frontier.

Для КАЖДОЙ M отдельно требуется P_M(terminal reward>=M.threshold)>=M.required_probability.
Ограничения глобальные от начала, НЕ conditional constraints в каждой ветви.
Среди feasible policies максимизируй лексикографически
(min_M E_M[reward], sum_M E_M[reward], min_M success_M, sum_M success_M),
затем минимизируй canonical JSON ВСЕЙ policy: json.dumps(sort_keys=True,
separators=(',',':'),ensure_ascii=True). Суммы здесь только tie objectives,
они НЕ заменяют отдельные risk constraints или worst-model objective.

Policy {done:true} ровно в horizon; иначе {action:ID|wait,branches:[{observation,next}]}.
Branches sorted по observation, включают union positive-probability observations
следующего начала тика по всем ещё возможным M, включая последнюю доставку в
horizon. History с probability0 в одной M всё ещё может быть возможен в другой;
branch policy должна быть корректна для всех оставшихся моделей. Ровно h actions
в каждой ветви, единую policy нельзя индексировать скрытым M.
Output {feasible:false} либо {feasible:true,models:[{id,reward:[num,den],
success:[num,den]}],policy}; models sorted по id, дроби несократимы.
Точный nondominated vector (E_1,...,E_m,P_1,...,P_m) frontier на каждом reachable
conditional belief имеет<=1024 элементов, output<=300KB. Один scalar value на
belief не сохраняет компромиссы разных моделей. Общий verifier budget25s.
'''


def scenario(seed):
    old = control_case(seed % 10)
    old["horizon"] = 3
    models = []
    for i in range(2):
        model = {k: copy.deepcopy(old[k]) for k in ("prior", "transition", "threshold", "required_probability")}
        model["id"] = f"m{i}"
        model["outcomes"] = {a["id"]: copy.deepcopy(a["outcomes"]) for a in old["actions"]}
        if i:
            model["prior"] = [[1, 4], [3, 4]]
            model["transition"] = [[[1, 2], [1, 2]], [[1, 4], [3, 4]]]
            model["outcomes"]["a"], model["outcomes"]["b"] = model["outcomes"]["b"], model["outcomes"]["a"]
        models.append(model)
    return {"horizon": old["horizon"], "energy": old["energy"],
            "actions": [{"id": a["id"], "energy": a["energy"]} for a in old["actions"]], "models": models}


def tiny():
    models = []
    for i in range(2):
        outcomes = {}
        for name, reward in (("a", 9 if i == 0 else 0), ("b", 0 if i == 0 else 9), ("safe", 3)):
            outcomes[name] = [[{"label": "ok", "reward": reward, "p": [1, 1]}]] * 2
        models.append({"id": f"m{i}", "prior": [[1, 1], [0, 1]],
                       "transition": [[[1, 1], [0, 1]], [[0, 1], [1, 1]]],
                       "outcomes": outcomes, "threshold": 3, "required_probability": [1, 1]})
    return {"horizon": 1, "energy": 1, "actions": [{"id": name, "energy": 1} for name in ("a", "b", "safe")], "models": models}


TINY = tiny()
EXPECTED = {"feasible": True, "models": [
    {"id": f"m{i}", "reward": [3, 1], "success": [1, 1]} for i in range(2)],
    "policy": {"action": "safe", "branches": [{"observation": "_", "next": {"done": True}}]}}
IMPOSSIBLE = {**TINY, "actions": TINY["actions"][:2]}
CASES = [(TINY, EXPECTED), (IMPOSSIBLE, {"feasible": False})]
CASES += [(value, solve487(value)) for value in (scenario(i) for i in range(10))]
GOLD = textwrap.dedent(inspect.getsource(solve487)) + '\nimport json,sys\nfor line in sys.stdin:\n    print(json.dumps(solve487(json.loads(line))))\n'
TASK = program_task(number=487, slug="adaptive_retry_policy", name="Share one delayed policy across adversarial environment models", parent=359,
                    prompt=PROMPT, cases=CASES, gold=GOLD, tags=("systems", "hardening-v4", "hardening-v5", "hardening-v6"))
