"""Second semantic revision of the four calibrated workflow pilot tasks."""

from __future__ import annotations

import copy
import inspect
import itertools
import json
import math
import posixpath
import random
from fractions import Fraction
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness_bench.general_hard.workflow_revision import _revision_507
    from harness_bench.general_hard.workflow_tasks import _expand_507, _solve_500, _solve_511


def _recover_500(rows, dimension):
    basis = {}
    for row in rows:
        while row:
            pivot = row.bit_length() - 1
            if pivot in basis:
                row ^= basis[pivot]
            else:
                basis[pivot] = row
                break
    decoded = 0
    for index in range(dimension):
        row = 1 << index
        while row and row.bit_length() - 1 in basis:
            row ^= basis[row.bit_length() - 1]
        if not row:
            decoded |= 1 << index
    return decoded, len(basis)


def _solve_v5_500(data):
    labels = sorted(data["objects"])
    positions = {name: i for i, name in enumerate(labels)}
    hashes = {
        row["sha256"]: positions[label]
        for label in labels
        for row in _solve_500(
            {
                **data,
                "snapshots": [
                    {
                        "id": "x",
                        "parent": None,
                        "ops": [{"op": "put", "path": "x", "object": label}],
                    }
                ],
                "keep": ["x"],
                "existing": [],
            }
        )["snapshots"][0]["files"]
    }
    names = sorted(data["policy"]["candidates"])
    manifests = {name: _solve_500({**data, "keep": [name], "existing": []}) for name in names}
    required = {
        name: sum(1 << hashes[digest] for digest in manifests[name]["required"]) for name in names
    }
    choices = []
    for flags in itertools.product((False, True), repeat=len(names)):
        chosen = [name for name, flag in zip(names, flags, strict=True) if flag]
        picked = set(chosen)
        policy = data["policy"]
        if not set(policy["mandatory"]) <= picked:
            continue
        if any(
            name in picked and not set(needs) <= picked
            for name, needs in policy["requires"].items()
        ):
            continue
        if any(
            not group["min"] <= len(set(group["members"]) & picked) <= group["max"]
            for group in policy["groups"]
        ):
            continue
        bits = 0
        for name in chosen:
            bits |= required[name]
        choices.append((bits, -sum(policy["candidates"][name] for name in chosen), chosen))
    packets = sorted(data["packets"], key=lambda row: row["id"])
    zones = sorted({row["zone"] for row in packets})
    equations = [sum(1 << positions[label] for label in row["xor"]) for row in packets]
    best = None
    answer = None
    cache = {}
    retention = {}
    for mask in range(1 << len(packets)):
        selected = [i for i in range(len(packets)) if mask & (1 << i)]
        stored = sum(packets[i]["bytes"] for i in selected)
        upload = sum(packets[i]["upload"] for i in selected)
        if stored > data["max_bytes"] or upload > data["max_upload"]:
            continue
        robust = (1 << len(labels)) - 1
        ranks = []
        for zone in zones:
            rows = tuple(equations[i] for i in selected if packets[i]["zone"] != zone)
            if rows not in cache:
                cache[rows] = _recover_500(rows, len(labels))
            recovered, rank = cache[rows]
            robust &= recovered
            ranks.append(
                {
                    "lost_zone": zone,
                    "rank": rank,
                    "recoverable": [
                        label for j, label in enumerate(labels) if recovered & (1 << j)
                    ],
                }
            )
        if robust not in retention:
            feasible = [
                (-reward, chosen) for bits, reward, chosen in choices if bits & robust == bits
            ]
            retention[robust] = (
                min(feasible, key=lambda pair: (-pair[0], len(pair[1]), pair[1]))
                if feasible
                else None
            )
        winner = retention[robust]
        if winner is None:
            continue
        utility, chosen = winner
        packet_ids = [packets[i]["id"] for i in selected]
        score = -utility, upload, stored, len(chosen), chosen, len(packet_ids), packet_ids
        if best is None or score < best:
            best = score
            answer = {
                "feasible": True,
                "utility": utility,
                "retained": chosen,
                "packets": packet_ids,
                "upload_bytes": upload,
                "stored_bytes": stored,
                "recovery": ranks,
                **_solve_500({**data, "keep": chosen, "existing": []}),
            }
            for key in ("required", "upload", "gc", "physical_bytes"):
                answer.pop(key)
    return answer if answer is not None else {"feasible": False}


def _options_v5_503(data, invoice):
    lines = sorted(invoice["lines"], key=lambda row: row["id"])
    columns = sorted(data["column_totals"])
    bases = [
        Fraction(*row["unit"]) * row["qty"] / (1 + Fraction(*row["tax"]) if row["inclusive"] else 1)
        for row in lines
    ]
    weight = sum((max(value, 0) for value in bases), Fraction())
    discount = [
        Fraction(*invoice["discount"]) * max(value, 0) / weight if weight else Fraction()
        for value in bases
    ]
    net = [(value - share) * invoice["scale"] for value, share in zip(bases, discount, strict=True)]
    tax = [value * Fraction(*row["tax"]) for value, row in zip(net, lines, strict=True)]
    choices = [
        sorted({math.floor(value), math.ceil(value)})
        for pair in zip(net, tax, strict=True)
        for value in pair
    ]
    target_n, target_t = round(sum(net, Fraction())), round(sum(tax, Fraction()))
    options = []
    for native in itertools.product(*choices):
        if sum(native[::2]) != target_n or sum(native[1::2]) != target_t:
            continue
        exact = []
        for column in columns:
            gross = sum(
                native[2 * i] + native[2 * i + 1]
                for i, row in enumerate(lines)
                if row["jurisdiction"] == column
            )
            exact.append(
                Fraction(gross, invoice["scale"]) * Fraction(*invoice["fx"]) * data["base_scale"]
            )
        for settled in itertools.product(
            *(sorted({math.floor(value), math.ceil(value)}) for value in exact)
        ):
            if sum(settled) != data["row_totals"][invoice["id"]]:
                continue
            quota = tuple(
                sum(
                    settled[columns.index(column)]
                    for name, column in rule["cells"]
                    if name == invoice["id"]
                )
                for rule in data["quotas"]
            )
            errors = [abs(native[2 * i] - net[i]) for i in range(len(lines))]
            errors += [abs(native[2 * i + 1] - tax[i]) for i in range(len(lines))]
            errors += [abs(value - source) for value, source in zip(settled, exact, strict=True)]
            detail = {
                "id": invoice["id"],
                "lines": [
                    {
                        "id": row["id"],
                        "net_minor": native[2 * i],
                        "tax_minor": native[2 * i + 1],
                        "gross_minor": native[2 * i] + native[2 * i + 1],
                    }
                    for i, row in enumerate(lines)
                ],
                "allocations": [
                    {
                        "jurisdiction": column,
                        "exact_base": f"{value.numerator}/{value.denominator}",
                        "base_minor": amount,
                    }
                    for column, value, amount in zip(columns, exact, settled, strict=True)
                ],
            }
            options.append(
                (
                    settled,
                    quota,
                    max(errors, default=Fraction()),
                    sum((error * error for error in errors), Fraction()),
                    native + settled,
                    detail,
                )
            )
    return options


def _solve_v5_503(data):
    if sum(data["row_totals"].values()) != sum(data["column_totals"].values()):
        return {"feasible": False, "reason": "total"}
    invoices = sorted(data["invoices"], key=lambda row: row["id"])
    options = [_options_v5_503(data, invoice) for invoice in invoices]
    if any(not group for group in options):
        return {"feasible": False, "reason": "coupling"}
    thresholds = sorted({item[2] for group in options for item in group} | {Fraction()})
    columns = sorted(data["column_totals"])
    quotas = data["quotas"]
    bounds = []
    for at in range(len(options) + 1):
        bounds.append(
            (
                [
                    sum(min(option[0][j] for option in group) for group in options[at:])
                    for j in range(len(columns))
                ],
                [
                    sum(max(option[0][j] for option in group) for group in options[at:])
                    for j in range(len(columns))
                ],
                [
                    sum(min(option[1][q] for option in group) for group in options[at:])
                    for q in range(len(quotas))
                ],
                [
                    sum(max(option[1][q] for option in group) for group in options[at:])
                    for q in range(len(quotas))
                ],
            )
        )

    def optimize(threshold):
        @lru_cache(None)
        def visit(index, residual, accumulated):
            low, high, qlow, qhigh = bounds[index]
            if any(not a <= target <= b for a, target, b in zip(low, residual, high, strict=True)):
                return None
            if any(
                value + a > rule["max"] or value + b < rule["min"]
                for value, a, b, rule in zip(accumulated, qlow, qhigh, quotas, strict=True)
            ):
                return None
            if index == len(options):
                return Fraction(), (), ()
            best = None
            for number, option in enumerate(options[index]):
                settled, contributions, worst, squared, vector, _ = option
                if worst > threshold:
                    continue
                suffix = visit(
                    index + 1,
                    tuple(a - b for a, b in zip(residual, settled, strict=True)),
                    tuple(a + b for a, b in zip(accumulated, contributions, strict=True)),
                )
                if suffix is None:
                    continue
                score = squared + suffix[0], vector + suffix[1], (number,) + suffix[2]
                if best is None or score[:2] < best[:2]:
                    best = score
            return best

        return visit(
            0, tuple(data["column_totals"][column] for column in columns), (0,) * len(quotas)
        )

    left, right = 0, len(thresholds)
    while left < right:
        middle = (left + right) // 2
        if optimize(thresholds[middle]) is None:
            left = middle + 1
        else:
            right = middle
    if left == len(thresholds):
        return {"feasible": False, "reason": "coupling"}
    result = optimize(thresholds[left])
    return {
        "feasible": True,
        "invoices": [group[number][5] for group, number in zip(options, result[2], strict=True)],
        "maximum_error": f"{thresholds[left].numerator}/{thresholds[left].denominator}",
        "squared_error": f"{result[0].numerator}/{result[0].denominator}",
    }


def _relations_v5_507(facts, rules):
    predicates = sorted(
        {row["predicate"] for row in facts}
        | {rule["head"]["predicate"] for rule in rules}
        | {atom["predicate"] for rule in rules for atom in [*rule["body"], *rule["not"]]}
    )
    strata = dict.fromkeys(predicates, 0)
    for iteration in range(len(predicates) + 1):
        changed = False
        for rule in rules:
            head = rule["head"]["predicate"]
            level = max(
                [
                    strata[head],
                    *(strata[atom["predicate"]] for atom in rule["body"]),
                    *(strata[atom["predicate"]] + 1 for atom in rule["not"]),
                ]
            )
            if strata[head] != level:
                strata[head] = level
                changed = True
        if not changed:
            break
        if iteration == len(predicates):
            return {"error": "unstratified"}
    relations = {name: {} for name in predicates}

    def insert(predicate, values, proof):
        table = relations[predicate]
        previous = table.setdefault(tuple(values), set())
        if any(old <= proof for old in previous):
            return False
        previous.difference_update([old for old in previous if proof < old])
        previous.add(proof)
        return True

    for fact in facts:
        insert(fact["predicate"], fact["args"], frozenset([fact["id"]]))
    for level in sorted(set(strata.values())):
        changed = True
        while changed:
            changed = False
            for rule in rules:
                if strata[rule["head"]["predicate"]] != level:
                    continue
                matches = [({}, frozenset())]
                for atom in rule["body"]:
                    following = []
                    for binding, proof in matches:
                        for values, proofs in list(relations[atom["predicate"]].items()):
                            extended = dict(binding)
                            for term, value in zip(atom["args"], values, strict=True):
                                if term.startswith("?"):
                                    if term in extended and extended[term] != value:
                                        break
                                    extended[term] = value
                                elif term != value:
                                    break
                            else:
                                following.extend((extended, proof | support) for support in proofs)
                    matches = following
                for binding, proof in matches:

                    def terms(atom, binding=binding):
                        return tuple(
                            binding[term] if term.startswith("?") else term for term in atom["args"]
                        )

                    if any(terms(atom) in relations[atom["predicate"]] for atom in rule["not"]):
                        continue
                    changed |= insert(rule["head"]["predicate"], terms(rule["head"]), proof)
    return {
        "strata": strata,
        "relations": [
            {
                "predicate": name,
                "rows": [
                    {"values": list(values), "proofs": sorted([sorted(proof) for proof in proofs])}
                    for values, proofs in sorted(relations[name].items())
                ],
            }
            for name in predicates
        ],
    }


def _solve_v5_507(data):
    stripped = {
        name: "\n".join(
            "" if line.strip().startswith(("@fact ", "@rule ")) else line
            for line in text.split("\n")
        )
        for name, text in data["files"].items()
    }
    config = _revision_507({**data, "files": stripped})
    if "error" in config:
        return config
    facts, rules = [], []

    def visit(path, profile, variables):
        variables = dict(variables)

        def early(name):
            namespace, key = name.split(":", 1)
            source = data["env"] if namespace == "ENV" else variables
            return key in source, source.get(key)

        for raw in data["files"][path].splitlines():
            line = raw.strip()
            if line.startswith("@let "):
                name, template = line[5:].split("=", 1)
                variables[name.strip()] = _expand_507(template.strip(), early)
            elif line.startswith("@include "):
                target = _expand_507(line[9:].strip(), early)
                visit(
                    posixpath.normpath(posixpath.join(posixpath.dirname(path), target)),
                    profile,
                    variables,
                )
            elif line.startswith("["):
                profile = line[1:-1]
            elif line.startswith(("@fact ", "@rule ")):
                directive, encoded = line.split(" ", 1)
                row = json.loads(encoded)

                def lookup(name, profile=profile):
                    if name.startswith(("ENV:", "VAR:")):
                        return early(name)
                    section, key = name.split(".", 1) if "." in name else (profile, name)
                    source = config["values"].get(section, {})
                    return key in source, source.get(key)

                def expand(value):
                    if isinstance(value, str):
                        return _expand_507(value, lookup)
                    if isinstance(value, list):
                        return [expand(item) for item in value]
                    return {key: expand(item) for key, item in value.items()}

                (facts if directive == "@fact" else rules).append(expand(row))

    try:
        visit(data["entry"], "global", {})
    except ValueError as exc:
        return {"error": str(exc)}
    answer = _relations_v5_507(facts, rules)
    return answer if "error" in answer else {"config": config, **answer}


def _policy_v5_511(hypotheses, data):
    probes = sorted((p for p in data["probes"] if p["available"]), key=lambda p: p["id"])
    states = sorted(data["states"])
    modes = sorted(data["fault_modes"], key=lambda mode: mode["id"])
    worlds = [(h, mode["id"]) for h in range(len(hypotheses)) for mode in modes]
    flips = {mode["id"]: {tuple(pair) for pair in mode["flips"]} for mode in modes}
    probe_by_id = {p["id"]: p for p in probes}

    def outcome(world, probe, state):
        h, mode = world
        count = len(set(hypotheses[h]) & set(probe["tests"]))
        truth = int(count > 0) if probe["mode"] == "any" else count % 2
        return truth ^ int((state, probe["id"]) in flips[mode])

    state = data["initial_state"]
    for observation in data["observations"]:
        probe = probe_by_id[observation["probe"]]
        worlds = [
            world for world in worlds if outcome(world, probe, state) == observation["outcome"]
        ]
        state = probe["next"][observation["outcome"]]
    if not worlds:
        return {"identifiable": False, "worlds": [], "state": state}
    outcomes = {
        (s, i): tuple(outcome(world, probe, s) for world in worlds)
        for s in states
        for i, probe in enumerate(probes)
        if s in probe["cost"]
    }

    def split(mask, s, i):
        groups = [0, 0]
        for w in range(len(worlds)):
            if mask & (1 << w):
                groups[outcomes[s, i][w]] |= 1 << w
        return [(value, part) for value, part in enumerate(groups) if part]

    def terminal(mask):
        labels = {worlds[w][0] for w in range(len(worlds)) if mask & (1 << w)}
        return next(iter(labels)) if len(labels) == 1 else None

    infinity = 10**9

    @lru_cache(None)
    def worst(mask):
        if terminal(mask) is not None:
            return tuple(0 for _ in states)
        values = [infinity] * len(states)
        constant = []
        for s_index, s in enumerate(states):
            for i, probe in enumerate(probes):
                if s not in probe["cost"]:
                    continue
                groups = split(mask, s, i)
                cost = probe["cost"][s]
                if len(groups) == 1:
                    constant.append((s_index, states.index(probe["next"][groups[0][0]]), cost))
                else:
                    children = [
                        worst(part)[states.index(probe["next"][value])] for value, part in groups
                    ]
                    if max(children) < infinity:
                        values[s_index] = min(values[s_index], cost + max(children))
        for _ in states:
            for source, target, cost in constant:
                values[source] = min(values[source], cost + values[target])
        return tuple(values)

    @lru_cache(None)
    def optimize(mask, s, budget):
        label = terminal(mask)
        if label is not None:
            tree = {"hypothesis": label}
            return 0, 0, json.dumps(tree, sort_keys=True, separators=(",", ":")), tree
        best = None
        for i, probe in enumerate(probes):
            if s not in probe["cost"]:
                continue
            cost = probe["cost"][s]
            groups = split(mask, s, i)
            remaining = budget - cost
            if any(
                worst(part)[states.index(probe["next"][value])] > remaining
                for value, part in groups
            ):
                continue
            if len(groups) == 1 and probe["next"][groups[0][0]] == s:
                continue
            children = [
                (value, optimize(part, probe["next"][value], remaining)) for value, part in groups
            ]
            tree = {
                "probe": probe["id"],
                "branches": [{"outcome": value, "tree": child[3]} for value, child in children],
            }
            candidate = (
                mask.bit_count() * cost + sum(child[0] for _, child in children),
                1 + sum(child[1] for _, child in children),
                json.dumps(tree, sort_keys=True, separators=(",", ":")),
                tree,
            )
            if best is None or candidate[:3] < best[:3]:
                best = candidate
        return best

    mask = (1 << len(worlds)) - 1
    budget = worst(mask)[states.index(state)]
    common = {
        "worlds": [{"hypothesis": h, "fault_mode": mode} for h, mode in worlds],
        "state": state,
    }
    if budget >= infinity:
        return {"identifiable": False, **common}
    answer = optimize(mask, state, budget)
    return {
        "identifiable": True,
        **common,
        "worst_cost": budget,
        "path_cost_sum": answer[0],
        "nodes": answer[1],
        "tree": answer[3],
    }


def _solve_v5_511(data):
    diagnosis = _solve_511(
        {"tests": [{**row, "can_rerun": False} for row in data["tests"]], "shards": data["shards"]}
    )
    if not diagnosis["consistent"]:
        return diagnosis
    for key in ("rerun", "rerun_cost", "unresolved"):
        del diagnosis[key]
    return {**diagnosis, **_policy_v5_511(diagnosis["hypotheses"], data)}


def _prompts_v5(legacy, v4):
    p500 = (
        legacy["_PROMPT_500"]
        .split("keep может повторяться")[0]
        .replace(
            "Вход: objects={label: UTF-8 text}, snapshots=[{id,parent,ops}], keep=[id],\nexisting=[SHA-256].",
            "Прочитай objects/*.txt, backup/object-index.json, backup/snapshots.json и\n"
            "backup/recovery-policy.json. objects labels имеют различное содержимое.\n"
            "Логический вход {objects,snapshots,policy,packets,max_bytes,max_upload}.",
        )
    )
    p500 += """
В storage вместо исходных blobs лежат coded packets. Packet={id,zone,xor,
bytes,upload}; xor — непустой список различных object labels. Это одна
линейная equation над GF(2): XOR указанных неизвестных blobs; blobs padded
нулями до общей ширины, исходные длины известны. Для РЕКОНСТРУКЦИИ конкретного
blob его UNIT VECTOR обязан лежать в ROW SPAN выбранных equations. Большой
rank или участие blob в equation само по себе восстановления не даёт.
Выбирай subset packets, sum bytes<=max_bytes и sum upload<=max_upload.
Стоимость bytes/upload задана (включает заголовки и транспорт), не вычисляй её
по исходному тексту. Packet immutable, чтение XOR bytes не расходуется отдельно.
Требуется устойчивость: после удаления ВСЕХ выбранных packets любой ОДНОЙ
zone каждый нужный blob всё ещё независимо восстанавливается. Список zones
включает все zones исходного каталога, даже если из них ничего не выбрано.

СОВМЕСТНО выбери retained snapshots из policy.candidates={id:positive utility}.
policy.mandatory обязательны; requires={id:[IDs]} — импликации (циклы допустимы);
groups=[{members,min,max}] — overlapping inclusive quotas на число retained.
Предки не удерживаются автоматически; materialization использует их состояние.
Каждый blob retained snapshots должен быть recoverable при каждой zone loss.
Цель MIN (-utility,upload_bytes,stored_bytes,число retained,sorted retained,
число packets,sorted packet IDs). Это совместная оптимизация, не независимые
выборы snapshot и encoding. До10 candidates,16 packets,8 object labels,
4 zones,20 snapshots. IDs/paths ASCII, уникальны; bytes/upload positive integers.

Если infeasible, только {feasible:false}. Иначе {feasible:true,utility,retained,
packets,upload_bytes,stored_bytes,recovery,snapshots,logical_bytes}.
retained/packets sorted ascending; recovery sorted lost_zone, каждый
{lost_zone,rank,recoverable:[sorted object labels]} после потери zone; recoverable
включает ВСЕ восстанавливаемые blobs, не только required. snapshots строго
по id, каждый {id,files,tree_sha256}; files sorted path, {path,size,sha256}, size
UTF8 bytes. tree_sha256=SHA256 UTF8 concatenation path+NUL+decimal size+NUL+
content SHA256 hex+LF по files; lowercase hex. logical_bytes считает все file
instances всех retained snapshots. Исходные files только материализуют desired
content; нельзя считать их бесплатно доступными recovery packets.
"""
    native = legacy["_PROMPT_503"].split("Определение ALLOCATE")[0]
    native = native.replace(
        "Вход {base_scale,invoices,row_totals,column_totals}.",
        "Вход {base_scale,invoices,row_totals,column_totals,quotas}.",
    )
    p503 = (
        native
        + """
Здесь native rounding и FX settlement выбираются СОВМЕСТНО, ALLOCATE или
largest-remainder до FX применять НЕЛЬЗЯ. Для каждой line выбери integer
N из {floor(net_exact),ceil(net_exact)} и T из {floor(tax_exact),ceil(tax_exact)}.
В каждом invoice SUM N = round-half-even(SUM net_exact), SUM T аналогично;
это единственные native constraints, равные remainders НЕ фиксируют выбор.
Gross=N+T. Для каждой jurisdiction j вычисли X[i,j]=SUM gross относящихся
lines / invoice.scale * invoice.fx * base_scale, точно rational. Каждая line
jurisdiction есть в column_totals, keys row_totals ровно invoice IDs.
Выбери integer Y[i,j] из {floor(X),ceil(X)} с exact row_totals/column_totals.
quotas=[{id,cells:[[invoice,jurisdiction]],min,max}] одновременно ограничивают
сумму Y в заданных уникальных cells; quotas могут перекрываться.

Ошибки E — абсолютные отклонения КАЖДОГО native N от net_exact, T от tax_exact
и КАЖДОГО Y от ЕГО X (X зависит от native decisions). Все эти ошибки измерены
в соответствующих minor units и имеют равный вес. Глобальная цель MIN
(MAX E,SUM E^2,decision vector). Decision vector по invoices sorted id:
сначала [N_line0,T_line0,N_line1,T_line1,...] по line.id, затем Y по sorted
jurisdiction; потом следующий invoice. Пустой MAX=0. Не замораживай native
решение по его локальному error: оно меняет FX cells и feasibility.
Если общие row/column totals различаются: {feasible:false,reason:"total"}.
Любая другая infeasibility: {feasible:false,reason:"coupling"}.
Успех {feasible:true,maximum_error,squared_error,invoices}; ошибки сокращённые
"n/d", включая0/1. invoices по id, каждый {id,lines,allocations}; lines по id
[{id,net_minor:N,tax_minor:T,gross_minor:N+T}]; allocations все columns sorted
[{jurisdiction,exact_base:X as n/d,base_minor:Y}]. Других полей нет.
До4 invoices,4 lines/invoice,3 jurisdictions,3 quotas; <=8 fractional native
N/T entries в invoice; <=256 допустимых вариантов native+FX для одной invoice.
Все числители/знаменатели по модулю<=10^6, scale<=1000. Signed lines допустимы.
"""
    )
    p507 = (
        v4[507].replace(
            "На любую ошибку только {error:code}. Успех {values,origins,linearization}:",
            "На любую ошибку только {error:code}. Поле config успешного ответа имеет "
            "формат {values,origins,linearization}:",
        )
        + """

Итоговый ответ успешного job: {config,relations,strata},
где config — описанный выше C3 result. Ошибка config по-прежнему
ТОЛЬКО {error:code} и имеет приоритет над policy processing.
В INI разрешены дополнительные строки @fact JSON и @rule JSON. Они НЕ
создают settings и не меняют физические номера строк. Fact={id,predicate,args};
rule={id,head:{predicate,args},body:[{predicate,args}],not:[{predicate,args}]}.
Все leaf values JSON строки. Сначала вычисли обычный config, игнорируя эти
строки; затем снова прочитай includes с теми же lexical scopes. В каждой
строке fact/rule интерполируй ВСЕ string fields: ENV/VAR из текущего captured
scope, ${key}/${profile.key} из FINAL config; $$ и fallback как выше. Super
references внутри fact/rule не используются. Include повторения разрешены;
ID facts после interpolation уникальны в job. IDs rules тоже уникальны.

Это конечная relational policy, а не однопроходный список правил. Predicate
ASCII ID, фиксированная arity1..3. Fact args — constants (не начинаются '?').
Rule terms — constants или variables '?name'. Все head/not variables встречаются
в positive body. Пустое body допустимо только для ground rules. Никаких функций
и создания новых constants; домен — строки из facts/rule constants (<=32).
Repeated variable в одном atom означает equality; positive body — natural join.
Negative atom означает ОТСУТСТВИЕ exact tuple в окончательной lower-stratum relation.
Найди минимальные nonnegative integer strata: head>=каждый positive predicate,
head>=каждый negative predicate+1. Если невозможно, только {error:"unstratified"}.
Вычисляй strata по возрастанию, внутри LEAST FIXED POINT положительных правил.
Циклы без seed не создают tuples; нельзя считать рекурсивную ссылку доказательством.

Provenance tuple — antichain ВСЕХ inclusion-minimal sets исходных fact IDs,
которые доказывают tuple. Каждый fact даёт singleton {id}. Join объединяет
sets positive premises; rule ID не добавляется. Negative premise не добавляет
ID (это closed-world guard после полного lower stratum). Для одинакового tuple
сохраняй все incomparable supports; отбрасывай строгие supersets и duplicates.
Пустой support ground rule допустим и поглощает все непустые. Provenance тоже
должна достичь fixed point, даже если множество tuples уже не меняется.
Output strata={predicate:integer}; relations включает ВСЕ использованные
predicates sorted, каждый {predicate,rows}; rows sorted lexicographic по
values array; row={values:[strings],proofs:[[sorted fact IDs]]}, proofs sorted
lexicographic. До18 facts,24 rules,300 derived tuples,256 supports/tuple;
всего<=4000 supports в output. IDs/constants<=64 UTF8 bytes. Output<2MB.
"""
    )
    p511 = legacy["_PROMPT_511"].split("Один rerun test")[0]
    p511 = p511.replace(
        "Гарантируется <=128 оптимальных гипотез.", "Гарантируется <=8 оптимальных гипотез."
    )
    p511 += """
Input дополнен states:[IDs],initial_state,probes,fault_modes,observations.
План должен определить underlying optimal diagnosis H, но НЕ обязан определить
fault mode. Мир=(hypothesis index,fault_mode). Все комбинации первоначально
возможны и имеют равный вес. fault_modes=[{id,flips:[[state,probeID]]}],
неизвестный mode постоянен в течение всей сессии, включая прошлые observations.
Probe={id,tests,mode:any/parity,available,cost:{state:positive integer},next:[s0,s1]}.
Probe разрешён только при available=true и наличии текущего state в cost.
Truth=any(int пересечения H с tests>0) либо parity(размер пересечения mod2).
Observed outcome=truth XOR int((current state,probe.id) в fault_mode.flips).
После outcome0/1 state=probe.next[outcome]. Повторное измерение той же пары
(state,probe) в том же мире даёт тот же outcome; в ДРУГОМ state результат
может измениться. Fault mode не переобирается после наблюдения.

observations=[{probe,outcome}] — уже выполненный префикс, каждое действие
разрешено по состоянию. Отфильтруй миры и продвинь state; затраты префикса
не входят в оптимизацию. Если миров не осталось, identifiable=false.
Теперь выбери adaptive decision tree с nodes {probe,branches:[{outcome,tree}]},
branches numeric sorted и только реально возможные. Leaf {hypothesis:index}
допустим, когда ВСЕ оставшиеся миры имеют этот diagnosis, даже если fault modes
не различены. Неинформативный probe допустим: смена state может разблокировать
измерения; конечное дерево обязано завершаться во ВСЕХ мирах. Бесконечный цикл
не решение. Не отбрасывай заранее пробы с constant outcome.
Цель MIN (worst remaining path cost,SUM remaining path cost по всем admissible
worlds,число внутренних nodes,canonical tree JSON). Canonical json.dumps
sort_keys=True,separators=(',',':'). Все IDs ASCII. Сумма учитывает каждый
оставшийся fault-mode world отдельно; не усреднять только по hypotheses.
У одной H без действий стоимость0, независимо от неизвестного mode.

Если diagnosis constraints inconsistent, только {consistent:false}.
Иначе output включает consistent:true,fault_cost,fault_count,hypotheses,
certain_bad,possibly_bad из diagnosis и identifiable:bool,worlds,state.
worlds=[{hypothesis:index,fault_mode:id}], sorted(index,mode ID), после prefix;
state после prefix. Если identifiable=false, других полей нет. Если true,
добавь worst_cost,path_cost_sum,nodes,tree. Если никакое конечное дерево из
этого state не различает diagnoses, identifiable=false (даже если можно
различить часть миров). Точное optimum; greedy entropy не подходит.
До16 tests,80 shards,3 fault modes,24 worlds,4 states,12 probes,20 observations.
Cost1..20; если solution существует, optimal worst_cost<=200. Это гарантированные
границы данных. Tests.rerun_cost/can_rerun игнорируются; только probes управляют
доступом/стоимостью. Bounds/лимит80 graph steps не сокращались.
"""
    return {500: p500, 503: p503, 507: p507, 511: p511}


def _inputs_v5(legacy):
    invoice = legacy["_invoice_503"]
    item = legacy["_item_503"]
    first = {
        "base_scale": 1,
        "invoices": [
            invoice(
                "i0",
                [item("a", (1, 2), jurisdiction="j0"), item("b", (1, 2), jurisdiction="j1")],
                scale=1,
            )
        ],
        "row_totals": {"i0": 1},
        "column_totals": {"j0": 0, "j1": 1},
        "quotas": [],
    }
    inputs503 = [first]
    second = copy.deepcopy(first)
    second["invoices"].append({**copy.deepcopy(second["invoices"][0]), "id": "i1"})
    second["row_totals"]["i1"] = 1
    second["column_totals"]["j0"] = 1
    inputs503.append(second)
    for seed in range(12):
        rng = random.Random(503500 + seed)
        n, m = 2 + seed % 3, 2 + seed % 2
        data = {
            "base_scale": 1,
            "invoices": [],
            "row_totals": {},
            "column_totals": {f"j{j}": 0 for j in range(m)},
            "quotas": [],
        }
        for i in range(n):
            lines = [
                item(
                    f"l{j}",
                    (rng.randrange(1, 8), 3),
                    qty=-1 if j == 2 and seed % 3 == 0 else 1,
                    tax=(1, 3) if (j + seed) % 2 else (0, 1),
                    inclusive=(j + i) % 3 == 0,
                    jurisdiction=f"j{j % m}",
                )
                for j in range(3)
            ]
            inv = invoice(f"i{i}", lines, scale=1, fx=[1 + seed % 2, 2])
            data["invoices"].append(inv)
            bases = [
                Fraction(*r["unit"]) * r["qty"] / (1 + Fraction(*r["tax"]) if r["inclusive"] else 1)
                for r in lines
            ]
            gross = round(sum(bases)) + round(
                sum(
                    (x * Fraction(*r["tax"]) for x, r in zip(bases, lines, strict=True)), Fraction()
                )
            )
            data["row_totals"][inv["id"]] = round(Fraction(gross) * Fraction(*inv["fx"]))
        options = [_options_v5_503(data, inv) for inv in data["invoices"]]
        planted = [group[rng.randrange(len(group))] for group in options]
        for j, column in enumerate(sorted(data["column_totals"])):
            data["column_totals"][column] = sum(option[0][j] for option in planted)
        for q in range(1 + seed % 3):
            cells = [[f"i{i}", f"j{j}"] for i in range(n) for j in range(m) if (i + j + q) % 3 != 0]
            total = sum(planted[int(i[1:])][0][int(j[1:])] for i, j in cells)
            data["quotas"].append({"id": f"q{q}", "cells": cells, "min": total, "max": total})
        inputs503.append(data)
    # Full native branching with jointly constrained FX cells: pre-rounding
    # or enumerating products of complete invoice plans is insufficient here.
    for seed in range(4):
        rng = random.Random(503590 + seed)
        data = {
            "base_scale": 1,
            "invoices": [],
            "row_totals": {},
            "column_totals": {"j0": 0, "j1": 0, "j2": 0},
            "quotas": [],
        }
        for i in range(4):
            units = [(1, 2)] * 4 if seed % 2 == 0 else [(2, 5), (3, 5)] * 2
            lines = [
                item(f"l{j}", units[j], tax=(1, 1), jurisdiction=f"j{(j + i + seed) % 3}")
                for j in range(4)
            ]
            data["invoices"].append(invoice(f"i{i}", lines, scale=1, fx=[1, 2]))
            data["row_totals"][f"i{i}"] = 2
        groups = [_options_v5_503(data, inv) for inv in data["invoices"]]
        planted = [group[rng.randrange(len(group))] for group in groups]
        for j, column in enumerate(sorted(data["column_totals"])):
            data["column_totals"][column] = sum(option[0][j] for option in planted)
        for q in range(3):
            cells = [
                [f"i{i}", f"j{j}"] for i in range(4) for j in range(3) if (2 * i + j + q) % 3 != 0
            ]
            total = sum(planted[int(i[1:])][0][int(j[1:])] for i, j in cells)
            data["quotas"].append({"id": f"cross{q}", "cells": cells, "min": total, "max": total})
        inputs503.append(data)
    invalid = copy.deepcopy(first)
    invalid["quotas"] = [{"id": "impossible", "cells": [["i0", "j0"]], "min": 1, "max": 1}]
    inputs503.append(invalid)
    invalid = copy.deepcopy(first)
    invalid["column_totals"]["j0"] = 1
    inputs503.append(invalid)
    inputs503.append(
        {"base_scale": 1, "invoices": [], "row_totals": {}, "column_totals": {}, "quotas": []}
    )

    inputs511 = []
    for seed in range(14):
        names = list("abcdefgh")[: 2 + seed % 7]
        states = ["cold", "hot", "safe"]
        probes = [
            {
                "id": "warm",
                "tests": [],
                "mode": "any",
                "available": True,
                "cost": {"cold": 1},
                "next": ["hot", "hot"],
            },
            {
                "id": "reset",
                "tests": [],
                "mode": "any",
                "available": True,
                "cost": {"hot": 2, "safe": 2},
                "next": ["cold", "cold"],
            },
        ]
        for i, name in enumerate(names):
            probes.append(
                {
                    "id": f"p{i}",
                    "tests": [name],
                    "mode": "any",
                    "available": True,
                    "cost": {"hot": 1 + i % 3, "safe": 2},
                    "next": ["safe", "hot"],
                }
            )
        probes.append(
            {
                "id": "group",
                "tests": names[::2],
                "mode": "parity",
                "available": True,
                "cost": {"hot": 2, "safe": 1},
                "next": ["hot", "safe"],
            }
        )
        modes = [
            {"id": "clean", "flips": []},
            {
                "id": "warm-fault",
                "flips": [["hot", f"p{i}"] for i in range(len(names)) if i % 2 == seed % 2],
            },
        ]
        if seed % 3 == 0:
            modes.append({"id": "other-fault", "flips": [["safe", "group"], ["hot", "group"]]})
        data = {
            "tests": [legacy["_test_511"](name) for name in names],
            "shards": [legacy["_shard_511"](names)],
            "states": states,
            "initial_state": "cold",
            "probes": probes,
            "fault_modes": modes,
            "observations": [],
        }
        if seed % 4 == 1:
            data["observations"] = [{"probe": "warm", "outcome": 0}, {"probe": "p0", "outcome": 1}]
        if seed % 4 == 2:
            for probe in probes:
                if probe["id"].startswith("p"):
                    probe["cost"].pop("safe", None)
        if seed % 5 == 4:
            data["fault_modes"] = [
                {"id": "m0", "flips": []},
                {
                    "id": "m1",
                    "flips": [
                        [state, probe["id"]]
                        for state in states
                        for probe in probes
                        if probe["tests"]
                    ],
                },
            ]
        inputs511.append(data)
    return {503: inputs503, 511: inputs511}


def _artifact_v5_500():
    objects = {f"b{i}": f"blob-{i}-" + "x" * (i + 1) for i in range(6)}
    snapshots = []
    for i in range(8):
        snapshots.append(
            {
                "id": f"s{i}",
                "parent": None,
                "ops": [
                    {"op": "put", "path": f"d/{j}", "object": f"b{j}"}
                    for j in {i % 6, (i * 3 + 1) % 6}
                ],
            }
        )
    snapshots += [
        {
            "id": "branch",
            "parent": "s0",
            "ops": [
                {"op": "copy", "source": "d", "path": "mirror"},
                {"op": "drop", "path": "d/0"},
                {"op": "put", "path": "new", "object": "b3"},
            ],
        }
    ]
    packets = [
        {
            "id": f"{zone}-{i}",
            "zone": zone,
            "xor": [f"b{i}"],
            "bytes": 8 + i % 3,
            "upload": 1 + (i * 5 + (zone == "B")) % 8,
        }
        for zone in ("A", "B")
        for i in range(6)
    ]
    for i, labels in enumerate(
        (["b0", "b1"], ["b1", "b2", "b3"], ["b2", "b4"], ["b3", "b4", "b5"])
    ):
        packets.append({"id": f"C-{i}", "zone": "C", "xor": labels, "bytes": 7, "upload": 2 + i})
    policy = {
        "candidates": {row["id"]: 3 + (i * 7) % 17 for i, row in enumerate(snapshots)},
        "mandatory": [],
        "requires": {"s7": ["s1"], "branch": ["s0"], "s5": ["s4"]},
        "groups": [
            {"members": ["s0", "s1", "s2", "s3"], "min": 1, "max": 3},
            {"members": ["s2", "s4", "s6", "branch"], "min": 1, "max": 3},
        ],
    }
    data = {
        "objects": objects,
        "snapshots": list(reversed(snapshots)),
        "policy": policy,
        "packets": packets,
        "max_bytes": 94,
        "max_upload": 43,
    }
    files = {f"objects/{label}.txt": text for label, text in objects.items()}
    files["backup/object-index.json"] = json.dumps(
        {label: f"objects/{label}.txt" for label in objects}
    )
    files["backup/snapshots.json"] = json.dumps(data["snapshots"])
    files["backup/recovery-policy.json"] = json.dumps(
        {key: data[key] for key in ("policy", "packets", "max_bytes", "max_upload")}, indent=2
    )
    return files, data


def _artifact_v5_507(v4data):
    files = dict(v4data["files"])
    facts = [
        {"id": f"e{i}", "predicate": "edge", "args": list(pair)}
        for i, pair in enumerate(("ab", "bc", "ac", "cd", "de", "ef", "ce", "cb"))
    ]
    facts += [
        {"id": "deny", "predicate": "denied", "args": ["d"]},
        {
            "id": "region-${VAR:TAG}",
            "predicate": "region",
            "args": ["${VAR:TAG}", "${Canary.endpoint}"],
        },
    ]

    def atom(predicate, *args):
        return {"predicate": predicate, "args": list(args)}

    rules = [
        {
            "id": "seed",
            "head": atom("reach", "?x", "?y"),
            "body": [atom("edge", "?x", "?y")],
            "not": [],
        },
        {
            "id": "closure",
            "head": atom("reach", "?x", "?z"),
            "body": [atom("reach", "?x", "?y"), atom("edge", "?y", "?z")],
            "not": [],
        },
        {
            "id": "usable",
            "head": atom("usable", "?x", "?y"),
            "body": [atom("reach", "?x", "?y")],
            "not": [atom("denied", "?y")],
        },
        {
            "id": "blocked-origin",
            "head": atom("blocked", "?x"),
            "body": [atom("reach", "?x", "?y"), atom("denied", "?y")],
            "not": [],
        },
        {
            "id": "safe-origin",
            "head": atom("safe", "?x"),
            "body": [atom("edge", "?x", "?y")],
            "not": [atom("blocked", "?x")],
        },
        {
            "id": "empty-cycle",
            "head": atom("empty", "?x"),
            "body": [atom("empty", "?x")],
            "not": [],
        },
    ]
    files["config/policy.ini"] = "\n".join("@fact " + json.dumps(row) for row in facts) + "\n"
    files["config/policy.ini"] += "\n".join("@rule " + json.dumps(row) for row in rules) + "\n"
    files["config/main.ini"] += "\n@let TAG=primary\n@include policy.ini\n"
    files["config/unstratified.ini"] = (
        "[s]\nx=ok\n@fact " + json.dumps({"id": "x", "predicate": "a", "args": ["x"]}) + "\n"
    )
    files["config/unstratified.ini"] += (
        "@rule "
        + json.dumps(
            {
                "id": "negative-cycle",
                "head": atom("a", "?x"),
                "body": [atom("a", "?x")],
                "not": [atom("a", "?x")],
            }
        )
        + "\n"
    )
    jobs = copy.deepcopy(v4data["jobs"])
    jobs.append({"id": "negative-cycle", "entry": "config/unstratified.ini", "env": {}})
    # Add a second include with a different lexical parameter but distinct facts;
    # this uses final C3 values while preserving each invocation's local capture.
    files["config/second-policy.ini"] = (
        "@fact "
        + json.dumps(
            {
                "id": "site-${VAR:TAG}",
                "predicate": "region",
                "args": ["${VAR:TAG}", "${Canary.chain}"],
            }
        )
        + "\n"
    )
    files["config/main.ini"] += "@let TAG=secondary\n@include second-policy.ini\n"
    source = dict(files)
    files["jobs.json"] = json.dumps({"jobs": jobs}, indent=2)
    return files, {"files": source, "jobs": jobs}


def build_v5(legacy, tasks, v4artifacts):
    from harness_bench.general_hard.common import artifact_task, program_task
    from harness_bench.general_hard.workflow_revision import _revision_507, _revision_prompts

    globals().update({name: legacy[name] for name in ("_solve_500", "_solve_511", "_expand_507")})
    globals()["_revision_507"] = _revision_507
    solvers = {500: _solve_v5_500, 503: _solve_v5_503, 507: _solve_v5_507, 511: _solve_v5_511}
    inputs = _inputs_v5(legacy)
    cases = {
        number: [(data, solvers[number](data)) for data in rows] for number, rows in inputs.items()
    }
    prompts = _prompts_v5(legacy, _revision_prompts(legacy))
    original = {int(task.id.split("_")[1]): task for task in tasks}
    result = [task for task in tasks if int(task.id.split("_")[1]) not in solvers]
    helpers = {
        503: [_options_v5_503, _solve_v5_503],
        511: [legacy["_solve_511"], _policy_v5_511, _solve_v5_511],
    }
    imports = (
        "import itertools\nimport json\nimport math\nimport sys\n"
        "from fractions import Fraction\nfrom functools import lru_cache\n"
        "sys.set_int_max_str_digits(0)\n\n"
    )
    for number in (503, 511):
        task = original[number]
        gold = imports + "\n\n".join(inspect.getsource(function) for function in helpers[number])
        gold += f"\nfor line in sys.stdin:\n    print(json.dumps(_solve_v5_{number}(json.loads(line)),ensure_ascii=False))\n"
        result.append(
            program_task(
                number=number,
                slug=task.id.split("_", 2)[2],
                name=task.name,
                parent=366 + (number - 500) // 2,
                prompt=prompts[number],
                cases=cases[number],
                gold=gold,
                tags=("workflow", "exact", "hardening-v4", "hardening-v5"),
            )
        )
    artifacts = {}
    for number, (files, data) in (
        (500, _artifact_v5_500()),
        (507, _artifact_v5_507(v4artifacts[507])),
    ):
        expected = (
            solvers[number](data)
            if number == 500
            else {job["id"]: _solve_v5_507({"files": data["files"], **job}) for job in data["jobs"]}
        )
        artifacts[number] = data
        task = original[number]
        result.append(
            artifact_task(
                number=number,
                slug=task.id.split("_", 2)[2],
                name=task.name,
                parent=366 + (number - 500) // 2,
                prompt=prompts[number],
                files=files,
                expected=expected,
                tags=("workflow", "multi-file", "hardening-v4", "hardening-v5"),
            )
        )
    return sorted(result, key=lambda task: task.id), cases, inputs, artifacts, solvers
