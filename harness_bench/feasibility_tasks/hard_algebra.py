"""Dense algebra and constrained-oracle controls for wave 19.

Validators evaluate the public constraints, never equality with gold answers.
The reference constructors are host-only; no executable answer code is accepted.
"""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from functools import lru_cache
from random import Random

KINDS = frozenset(
    {"hard_aggregate", "dfa_codec", "lattice_ledger", "affine_guid", "restricted_diagnosis"}
)


def _int(x):
    if type(x) is not int:
        raise ValueError("integer required")
    return x


def _rat(x):
    if type(x) is int:
        return Fraction(x)
    if isinstance(x, list) and len(x) == 2 and _int(x[1]) > 0:
        return Fraction(_int(x[0]), x[1])
    raise ValueError("rational required")


def _jsonrat(x):
    x = Fraction(x)
    return x.numerator if x.denominator == 1 else [x.numerator, x.denominator]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def _vector(x, n):
    return isinstance(x, list) and len(x) == n and all(type(v) is int for v in x)


def _world(s, x):
    return (
        _vector(x, s["variables"])
        and min(x) >= 0
        and all(_dot(r["coefficients"], x) == r["total"] for r in s["observations"])
    )


def _gf_rank(rows):
    basis = {}
    for raw in rows:
        v = _int(raw)
        if v < 0:
            raise ValueError("negative bit mask")
        while v:
            pivot = v.bit_length() - 1
            if pivot in basis:
                v ^= basis[pivot]
            else:
                basis[pivot] = v
                break
    return len(basis)


def _kernel_valid(s, vectors):
    return (
        isinstance(vectors, list)
        and all(type(v) is int and 0 < v < 1 << s["random_bits"] for v in vectors)
        and _gf_rank(vectors) == len(vectors)
        and all((v & r["mask"]).bit_count() % 2 == 0 for v in vectors for r in s["observations"])
    )


def _guid_capacity(s):
    # Each epoch discards its fractional unused check slot; no check crosses epochs.
    return sum(
        int((_rat(e["end"]) - _rat(e["start"])) * _rat(e["checks_per_second"])) for e in s["epochs"]
    )


def _dfa_counts(s):
    table = [[int(i in s["accepting"]) for i in range(len(s["transitions"]))]]
    for _ in range(s["input_length"]):
        table.append([sum(table[-1][j] for j in row) for row in s["transitions"]])
    return table


def _dfa_table_valid(s, table):
    return (
        isinstance(table, list)
        and len(table) == s["input_length"] + 1
        and all(_vector(row, len(s["transitions"])) for row in table)
        and table == _dfa_counts(s)
    )


def _output_capacity(s):
    return (s["output_alphabet"] ** (s["max_output_length"] + 1) - 1) // (s["output_alphabet"] - 1)


def _tree_depth(s, tree, mask, ancestors=0):
    if ancestors > len(s["queries"]) + s["outcomes"]:
        raise ValueError("tree too deep")
    if not isinstance(tree, dict):
        raise ValueError("tree node required")
    if set(tree) == {"outcome"}:
        outcome = _int(tree["outcome"])
        if not 0 <= outcome < s["outcomes"]:
            raise ValueError("outcome outside the component domain")
        return 0 if mask == 1 << outcome else 10**9
    if set(tree) != {"query", "yes", "no"}:
        raise ValueError("query node required")
    queries = {q["id"]: q["mask"] for q in s["queries"]}
    yes = mask & queries[tree["query"]]
    no = mask ^ yes
    if not yes or not no:
        return 10**9
    return 1 + max(
        _tree_depth(s, tree["yes"], yes, ancestors + 1),
        _tree_depth(s, tree["no"], no, ancestors + 1),
    )


def _diagnosis_bound(s, c):
    full = (1 << s["outcomes"]) - 1
    if "adversary_mask" in c:
        mask, cap = _int(c["adversary_mask"]), _int(c["max_eliminations"])
        if not 0 < mask <= full or cap <= 0:
            return False
        # Choose the larger ORIGINAL side for each query. Its complement has
        # at most cap elements, including after restricting to surviving states.
        if any(
            min((mask & q["mask"]).bit_count(), (mask & ~q["mask"]).bit_count()) > cap
            for q in s["queries"]
        ):
            return False
        lower = (mask.bit_count() - 1 + cap - 1) // cap
        return lower * _rat(s["seconds_per_query"]) > s["budget_seconds"]
    rows = c["bounds"]
    if not isinstance(rows, list):
        return False
    bounds = {}
    for row in rows:
        mask, depth = _int(row["mask"]), _int(row["depth_lower_bound"])
        if mask in bounds or not 0 < mask <= full or depth < 0:
            return False
        bounds[mask] = depth
    for mask, depth in bounds.items():
        if depth == 0:
            continue
        if mask.bit_count() <= 1:
            return False
        if depth == 1:
            continue
        for query in s["queries"]:
            yes, no = mask & query["mask"], mask & ~query["mask"]
            if yes and no and max(bounds.get(yes, 0), bounds.get(no, 0)) < depth - 1:
                return False
    return bounds.get(full, 0) * _rat(s["seconds_per_query"]) > s["budget_seconds"]


def check_certificate(spec, report):
    try:
        if report["status"] != spec["status"] or report["status"] == "solved":
            return False
        s, c = spec, report["certificate"]
        kind = s["kind"]
        if kind == "hard_aggregate":
            a, b = c["worlds"]
            return (
                _world(s, a)
                and _world(s, b)
                and _dot(s["target_coefficients"], a) != _dot(s["target_coefficients"], b)
            )
        if kind == "lattice_ledger":
            m, w = _int(c["modulus"]), c["weights"]
            return (
                m > 1
                and _vector(w, len(s["initial"]))
                and all(_dot(w, d) % m == 0 for d in s["operations"])
                and _dot(w, [b - a for a, b in zip(s["initial"], s["target"], strict=True)]) % m
                != 0
            )
        if kind == "dfa_codec":
            table = c["suffix_counts"]
            return (
                _dfa_table_valid(s, table)
                and _int(c["inputs"]) == table[s["input_length"]][s["start_state"]]
                and _int(c["outputs"]) == _output_capacity(s)
                and c["inputs"] > c["outputs"]
            )
        if kind == "affine_guid":
            vectors = c["kernel_vectors"]
            bound = min(Fraction(_guid_capacity(s), 2 ** len(vectors)), Fraction(1))
            return (
                _kernel_valid(s, vectors)
                and _int(c["max_checks"]) == _guid_capacity(s)
                and _rat(c["success_upper_bound"]) == bound
                and bound < _rat(s["required_success_probability"])
            )
        if kind == "restricted_diagnosis":
            return _diagnosis_bound(s, c)
        return False
    except (
        KeyError,
        ValueError,
        TypeError,
        IndexError,
        ZeroDivisionError,
        OverflowError,
        RecursionError,
    ):
        return False


def check_solution(s, c):
    try:
        kind = s["kind"]
        if kind == "hard_aggregate":
            weights = c["observation_weights"]
            ids = {r["id"] for r in s["observations"]}
            if not isinstance(weights, dict) or not set(weights) <= ids:
                return False
            return (
                _world(s, c["world"])
                and all(
                    sum(
                        _rat(weights.get(r["id"], 0)) * r["coefficients"][i]
                        for r in s["observations"]
                    )
                    == v
                    for i, v in enumerate(s["target_coefficients"])
                )
                and _int(c["target"])
                == sum(_rat(weights.get(r["id"], 0)) * r["total"] for r in s["observations"])
            )
        if kind == "lattice_ledger":
            counts = c["operation_counts"]
            return (
                _vector(counts, len(s["operations"]))
                and min(counts) >= 0
                and all(
                    a + sum(n * d[i] for n, d in zip(counts, s["operations"], strict=True)) == b
                    for i, (a, b) in enumerate(zip(s["initial"], s["target"], strict=True))
                )
            )
        if kind == "dfa_codec":
            return (
                c["operation"] == "dfa_lex_rank_shortlex"
                and _dfa_table_valid(s, c["suffix_counts"])
                and c["suffix_counts"][-1][s["start_state"]] <= _output_capacity(s)
            )
        if kind == "affine_guid":
            origin, vectors, count = _int(c["origin"]), c["kernel_vectors"], _int(c["checks"])
            nullity = s["random_bits"] - _gf_rank([r["mask"] for r in s["observations"]])
            return (
                c["operation"] == "enumerate_affine_basis"
                and 0 <= origin < 1 << s["random_bits"]
                and all((origin & r["mask"]).bit_count() % 2 == r["rhs"] for r in s["observations"])
                and _kernel_valid(s, vectors)
                and len(vectors) == nullity
                and 0 < count <= min(2**nullity, _guid_capacity(s))
                and Fraction(count, 2**nullity) >= _rat(s["required_success_probability"])
            )
        if kind == "restricted_diagnosis":
            depth = _tree_depth(s, c["tree"], (1 << s["outcomes"]) - 1)
            return depth * _rat(s["seconds_per_query"]) <= s["budget_seconds"]
        return False
    except (
        KeyError,
        ValueError,
        TypeError,
        IndexError,
        ZeroDivisionError,
        OverflowError,
        RecursionError,
    ):
        return False


def _solve_square(matrix, target):
    n = len(target)
    a = [[Fraction(matrix[j][i]) for j in range(n)] + [Fraction(target[i])] for i in range(n)]
    for i in range(n):
        pivot = next(j for j in range(i, n) if a[j][i])
        a[i], a[pivot] = a[pivot], a[i]
        scale = a[i][i]
        a[i] = [v / scale for v in a[i]]
        for j in range(n):
            if j != i:
                scale = a[j][i]
                a[j] = [x - scale * y for x, y in zip(a[j], a[i], strict=True)]
    return [r[-1] for r in a]


def _aggregate():
    rng, n = Random(190436), 16
    kernel = [rng.choice([-4, -3, -2, 2, 3, 4]) for _ in range(n - 1)] + [1]
    rows = [[int(i == j) for j in range(n - 1)] + [-kernel[i]] for i in range(n - 1)]
    for _ in range(100):
        i, j = rng.sample(range(n - 1), 2)
        factor = rng.choice([-1, 1])
        rows[i] = [x + factor * y for x, y in zip(rows[i], rows[j], strict=True)]
    world = [rng.randrange(30, 100) for _ in range(n)]
    target = [rng.randrange(-5, 6) for _ in range(n)]
    assert _dot(kernel, target)
    extra = [rng.randrange(-5, 6) for _ in range(n)]
    assert _dot(kernel, extra)
    common = dict(
        kind="hard_aggregate",
        variables=n,
        target_coefficients=target,
        observations=[
            dict(id=f"aggregate-{i:02}", coefficients=r, total=_dot(r, world))
            for i, r in enumerate(rows)
        ],
    )
    positive = deepcopy(common)
    positive["observations"].append(
        dict(id="aggregate-15", coefficients=extra, total=_dot(extra, world))
    )
    weights = _solve_square(rows + [extra], target)
    return (
        common,
        positive,
        {"worlds": [world, [a + b for a, b in zip(world, kernel, strict=True)]]},
        dict(
            world=world,
            target=_dot(target, world),
            observation_weights={f"aggregate-{i:02}": _jsonrat(v) for i, v in enumerate(weights)},
        ),
    )


def _ledger():
    rng, modulus = Random(190440), 97
    weights = [13, 29, 41, 53, 71, 1]
    operations = []
    for _ in range(16):
        d = [rng.randrange(-20, 21) for _ in range(5)]
        operations.append(d + [(-_dot(d, weights[:5])) % modulus + modulus * rng.randrange(-2, 3)])
    counts = [rng.randrange(1, 14) for _ in operations]
    initial = [rng.randrange(-100, 101) for _ in weights]
    target = [
        a + sum(n * d[i] for n, d in zip(counts, operations, strict=True))
        for i, a in enumerate(initial)
    ]
    positive = dict(kind="lattice_ledger", initial=initial, target=target, operations=operations)
    negative = deepcopy(positive)
    negative["target"][-1] += 1
    return negative, positive, dict(modulus=modulus, weights=weights), dict(operation_counts=counts)


def _codec():
    rng = Random(190439)
    common = dict(
        kind="dfa_codec",
        input_length=48,
        start_state=0,
        accepting=[1, 3, 6],
        transitions=[[rng.randrange(7) for _ in range(4)] for _ in range(7)],
        output_alphabet=2,
    )
    table = _dfa_counts(common)
    count = table[-1][0]
    positive = dict(common, max_output_length=count.bit_length() - 1)
    negative = dict(common, max_output_length=count.bit_length() - 2)
    return (
        negative,
        positive,
        dict(suffix_counts=table, inputs=count, outputs=_output_capacity(negative)),
        dict(operation="dfa_lex_rank_shortlex", suffix_counts=table),
    )


def _guid():
    rng, n, rank = Random(190442), 128, 89
    tails = [rng.getrandbits(n - rank) for _ in range(rank)]
    masks = [(1 << i) | (tails[i] << rank) for i in range(rank)]
    kernel = [
        (1 << (rank + j)) | sum(((tails[i] >> j) & 1) << i for i in range(rank))
        for j in range(n - rank)
    ]
    origin = rng.getrandbits(n)
    for _ in range(700):
        i, j = rng.sample(range(rank), 2)
        masks[i] ^= masks[j]
    masks += [masks[i] ^ masks[(i + 23) % rank] for i in range(19)]
    common = dict(
        kind="affine_guid",
        random_bits=n,
        budget_seconds=120,
        observations=[dict(mask=v, rhs=(v & origin).bit_count() % 2) for v in masks],
        required_success_probability=[99, 100],
        epochs=[
            dict(start=0, end=17, checks_per_second=3_000_000_001),
            dict(start=17, end=61, checks_per_second=4_000_000_003),
            dict(start=61, end=120, checks_per_second=5_300_000_007),
        ],
    )
    positive = deepcopy(common)
    positive["epochs"][-1]["checks_per_second"] = 5_600_000_007
    bound = min(Fraction(_guid_capacity(common), 2 ** len(kernel)), Fraction(1))
    needed = (99 * 2 ** len(kernel) + 99) // 100
    assert bound < Fraction(99, 100) and _guid_capacity(positive) >= needed
    return (
        common,
        positive,
        dict(
            kernel_vectors=kernel,
            max_checks=_guid_capacity(common),
            success_upper_bound=_jsonrat(bound),
        ),
        dict(
            operation="enumerate_affine_basis", origin=origin, kernel_vectors=kernel, checks=needed
        ),
    )


def _diagnosis():
    rng, n = Random(190443), 14
    masks = []
    for _ in range(15):
        masks.append(sum(1 << i for i in rng.sample(range(n), 3)))
    masks += [1 << i for i in range(n)]
    queries = [dict(id=f"q{i:02}", mask=v) for i, v in enumerate(dict.fromkeys(masks))]

    @lru_cache(None)
    def solve(mask):
        if mask.bit_count() == 1:
            return 0, dict(outcome=mask.bit_length() - 1)
        best = n + 1, None
        for q in queries:
            yes, no = mask & q["mask"], mask & ~q["mask"]
            if not yes or not no:
                continue
            dy, ty = solve(yes)
            dn, tn = solve(no)
            depth = 1 + max(dy, dn)
            if depth < best[0]:
                best = depth, dict(query=q["id"], yes=ty, no=tn)
        return best

    full = (1 << n) - 1
    depth, tree = solve(full)
    common = dict(kind="restricted_diagnosis", outcomes=n, queries=queries, budget_seconds=120)
    # All queries have a side of at most three elements. After four replies
    # an adversary can retain at least two of the fourteen hypotheses.
    negative = dict(common, seconds_per_query=30)
    positive = dict(common, seconds_per_query=_jsonrat(Fraction(120, depth)))
    return negative, positive, dict(adversary_mask=full, max_eliminations=3), dict(tree=tree)


_DETAILS = {
    "aggregate_identifiability": (
        _aggregate,
        "Восстанови точное значение target_coefficients·world по опубликованным агрегатам.",
        "world — неотрицательный целочисленный вектор. Каждый агрегат задаёт точное равенство coefficients·world=total; коэффициенты могут быть отрицательными. Иных данных нет. Для положительного ответа предъяви мир и линейную комбинацию наблюдений, выводящую целевой функционал.",
        '{"worlds": [[integer,...],[integer,...]]}: two nonnegative worlds satisfying every observation and having distinct target values.',
        '{"world": [nonnegative integer,...], "target": integer, "observation_weights": {observation_id: integer or [numerator,positive_denominator],...}}; omitted weights are zero.',
    ),
    "ledger_reachability": (
        _ledger,
        "Достигни target из initial целым неотрицательным числом разрешённых операций.",
        "Состояние — целочисленный вектор. Операция прибавляет соответствующий вектор operations; все координаты могут быть отрицательными, ограничений промежуточных состояний и порядка нет. Для невозможности подходит любой модульный линейный инвариант.",
        '{"modulus": integer > 1, "weights": [integer,...]}: every operation has zero weighted residue but target-initial does not.',
        '{"operation_counts": [nonnegative integer,...]} in operations order.',
    ),
    "universal_compressor": (
        _codec,
        "Построй обратимый кодек для всех принимаемых автоматом строк длины input_length в строки длиной не более max_output_length.",
        "transitions[state][symbol] задаёт полный DFA; символы входа 0..len(transitions[0])-1. Принимаются только строки, заканчивающиеся в accepting. Выходные символы 0..output_alphabet-1; допускаются все длины от 0 до max_output_length, включая пустую строку; побочных каналов нет. suffix_counts[r][s] — число принимаемых продолжений длины r из s. Исполняемый план dfa_lex_rank_shortlex ранжирует допустимый вход лексикографически с нуля через suffix_counts, затем выводит строку того же ранга среди выходов в порядке сначала длины, затем лексикографически. Декодер обращает оба ранжирования. Для каждого шага ранг вычисляется суммой чисел продолжений по меньшим символам. Реально перечислять строки не требуется.",
        '{"suffix_counts": [[integer,...],...], "inputs": integer, "outputs": integer}: exact recurrence table and pigeonhole counts.',
        '{"operation":"dfa_lex_rank_shortlex", "suffix_counts": [[integer,...],...]} with input_length+1 rows.',
    ),
    "opaque_guid": (
        _guid,
        "Спроектируй поиск равномерного секретного GUID с вероятностью не ниже required_success_probability в бюджете сервиса.",
        "Это равномерный random_bits-битный вектор, условно на все наблюдения parity(mask & secret)=rhs. bit 0 — младший. Наблюдения совместны, иной информации нет. Только проверка полного кандидата на равенство; отрицание исключает один кандидат, непроверенная догадка не успех. epochs — непересекающиеся временные окна [start,end); проверка в окне занимает ровно 1/checks_per_second секунд, не может пересекать границу окна; параллелизм запрещён, иных затрат нет. Неиспользованный дробный слот окна пропадает. План enumerate_affine_basis перечисляет k=0..checks-1, кандидат origin XOR всех kernel_vectors[j] с j-м битом k=1; прекращается при совпадении. Нужны полный независимый базис ядра и допустимый origin; локально перечислять кандидатов не требуется. Для отрицательного сертификата достаточно любого независимого набора векторов ядра: он даёт нижнюю границу размера пространства кандидатов и верхнюю границу успеха.",
        '{"kernel_vectors":[positive integer bitmask,...], "max_checks": integer, "success_upper_bound": integer or [numerator,positive_denominator]}. Bound min(max_checks / 2**len(kernel_vectors),1) must be below requirement.',
        '{"operation":"enumerate_affine_basis", "origin": integer bitmask, "kernel_vectors":[positive integer bitmask,...], "checks":positive integer}.',
    ),
    "diagnostic_queries": (
        _diagnosis,
        "Определи неисправный компонент с гарантией для всех outcomes вариантов за бюджет сервиса.",
        "Компоненты 0..outcomes-1. Доступны только перечисленные queries: ответ да тогда и только тогда, когда бит компонента в mask равен 1. Запросы последовательны, каждый занимает ровно seconds_per_query; других затрат нет. Положительный план — адаптивное двоичное дерево. Отрицательный сертификат bounds задаёт нижние границы глубины на подмножествах, закодированных битовыми масками. Граница 0 допустима всегда, 1 — для любого множества размера >=2. Для границы d>=2 на S каждый запрос, разбивающий S на два непустых множества, должен иметь хотя бы одну ветвь с предъявленной границей >=d-1. Неразбивающие запросы бесполезны и игнорируются. Все дочерние множества строго меньше, поэтому это ациклическое доказательство. У полного множества граница должна превышать доступное число запросов. Пропущенные границы считаются нулём. Альтернатива: adversary_mask задаёт подмножество S, max_eliminations=c>0. Для каждого запроса меньшая из двух частей S должна иметь размер <=c. Тогда противник может сохранять как минимум |S|-k*c вариантов после k запросов, и ceil((|S|-1)/c) — нижняя граница числа запросов; она должна превышать бюджет.",
        '{"bounds":[{"mask":positive integer,"depth_lower_bound":nonnegative integer},...]} with unique masks; OR {"adversary_mask":positive integer,"max_eliminations":positive integer}.',
        '{"tree":node}; node is {"outcome":integer} or {"query":query_id,"yes":node,"no":node}.',
    ),
}


def upgrade_pairs(pairs):
    """Mutate supplied copied pairs; caller owns the original source fixtures."""
    for pair in pairs:
        if pair["slug"] not in _DETAILS:
            continue
        factory, request, instructions, schema, solution_schema = _DETAILS[pair["slug"]]
        negative, positive, certificate, solution = factory()
        for key, spec, gold in (
            ("negative", negative, certificate),
            ("positive", positive, solution),
        ):
            case = pair[key]
            spec["status"] = case["spec"]["status"]
            case.update(
                spec=spec,
                gold=gold,
                request=request,
                instructions=instructions,
                schema=schema,
                solution_schema=solution_schema,
            )
    return pairs
