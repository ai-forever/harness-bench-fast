"""Deterministic engineering contracts, with private reference certificates.

Fixtures are data only: no reference answers, seeds or verifier sources are
copied into task workspaces. No task asks a model to solve an open problem.
"""

from __future__ import annotations

import random

CASES: list[dict] = []


def add(slug, name, request, kind, data, instructions, schema, gold, *, budget=False):
    CASES.append(
        dict(
            slug=slug,
            name=name,
            request=request,
            spec=dict(kind=kind, status="budget_exceeded" if budget else "impossible", **data),
            instructions=instructions,
            schema=schema,
            gold=gold,
        )
    )


# 432. A negative cycle hidden among otherwise satisfiable clock constraints.
edges = [
    dict(id=f"clock-{i:02}", **{"from": f"s{i}", "to": f"s{i + 1}"}, max_delta=8 + i % 3)
    for i in range(40)
]
edges += [dict(id="clock-46", **{"from": "s17", "to": "s5"}, max_delta=-140)]
random.Random(432).shuffle(edges)
add(
    "clock_contract",
    "Clock reconciliation without relaxing constraints",
    "Восстанови вещественные смещения часов сервисов так, чтобы выполнялись все ограничения синхронизации.",
    "difference_cycle",
    {"constraints": edges},
    "Каждая запись означает offset[to] - offset[from] <= max_delta. Все записи обязательны; допускаются любые вещественные смещения.",
    '{"edges": [ID, ...]} — ориентированный замкнутый цикл ограничений в порядке обхода с отрицательной суммой max_delta',
    {"edges": [f"clock-{i:02}" for i in range(5, 17)] + ["clock-46"]},
)

# 433. Hall obstruction; every job individually has multiple available workers.
eligible = {f"job-{i:02}": [f"worker-{i:02}", f"worker-{(i + 1) % 32:02}"] for i in range(32)}
for i in (4, 9, 15, 23, 28):
    eligible[f"job-{i:02}"] = ["worker-03", "worker-11", "worker-19", "worker-27"]
add(
    "deployment_matching",
    "One-to-one deployment assignment",
    "Назначь каждому заданию отдельный совместимый worker на один одновременный запуск. Worker нельзя использовать дважды.",
    "hall",
    {"eligible": eligible},
    "eligible перечисляет все допустимые назначения; добавление workers и последовательное выполнение запрещены контрактом.",
    '{"jobs": [ID, ...]} — непустое множество заданий, у которого объединение допустимых workers меньше числа заданий',
    {"jobs": [f"job-{i:02}" for i in (4, 9, 15, 23, 28)]},
)

# 434. Multiple routes and a shared network cut.
nodes = ["source", "sink"] + [f"edge-{i}" for i in range(12)] + [f"core-{i}" for i in range(12)]
links = []
for i in range(12):
    links += [
        {"from": "source", "to": f"edge-{i}", "capacity": 100},
        {"from": f"edge-{i}", "to": f"core-{i}", "capacity": 4 + i % 3},
        {"from": f"core-{i}", "to": "sink", "capacity": 100},
        {"from": f"edge-{i}", "to": f"edge-{(i + 1) % 12}", "capacity": 50},
        {"from": f"core-{i}", "to": f"core-{(i + 1) % 12}", "capacity": 50},
    ]
add(
    "replication_cut",
    "Replication throughput contract",
    "Настрой постоянный поток репликации из source в sink не меньше required_flow без изменения сети.",
    "flow_cut",
    {"nodes": nodes, "source": "source", "sink": "sink", "required_flow": 61, "edges": links},
    "Рёбра направленные; capacity — жёсткий предел единиц потока в секунду. Поток можно дробить и маршрутизировать произвольно, сохраняется баланс в промежуточных узлах. Накопление во времени не меняет требование постоянной скорости.",
    '{"source_side": [узел, ...]} — сторона разреза с source без sink, суммарная ёмкость исходящих рёбер которой ниже required_flow',
    {"source_side": ["source"] + [f"edge-{i}" for i in range(12)]},
)

# 435. XOR core is a chain plus one inconsistent checksum.
rows = [dict(id=f"check-{i:02}", variables=[f"b{i}", f"b{i + 1}"], rhs=i % 2) for i in range(35)]
rows.append(dict(id="check-41", variables=["b7", "b24"], rhs=0))  # 7..23 XOR is 1
random.Random(435).shuffle(rows)
add(
    "parity_repair",
    "Recover a parity-constrained configuration",
    "Восстанови булевы флаги конфигурации, одновременно удовлетворяющие всем контрольным суммам.",
    "xor",
    {"rows": rows},
    "Каждая строка требует XOR перечисленных variables равным rhs. Все строки доверенные; удалять или ослаблять их нельзя.",
    '{"rows": [ID, ...]} — набор строк, XOR которых даёт 0 = 1 (каждая переменная встречается чётное число раз)',
    {"rows": [f"check-{i:02}" for i in range(7, 24)] + ["check-41"]},
)

# 436. Two possible original datasets have identical published measurements.
observations = []
base = [3, 7, 11, 5, 8, 4, 9, 6]
for i in range(24):
    coeff = [(i * 7 + j * 3) % 11 + 1 for j in range(8)]
    coeff[1] = coeff[0]
    observations.append(
        dict(
            id=f"aggregate-{i:02}",
            coefficients=coeff,
            total=sum(a * b for a, b in zip(coeff, base, strict=True)),
        )
    )
add(
    "aggregate_identifiability",
    "Recover an exact count from released aggregates",
    "Восстанови точное число объектов первого сегмента по опубликованным агрегатам; ответ должен гарантированно совпасть с исходными данными.",
    "two_worlds",
    {"variables": 8, "observations": observations, "target_coefficients": [1, 0, 0, 0, 0, 0, 0, 0]},
    "Вектор сегментов состоит из неотрицательных целых. Наблюдения означают скалярное произведение coefficients на вектор равное total. Это все доступные данные; априорных ограничений нет.",
    '{"worlds": [[целое, ...], [целое, ...]]} — два допустимых исходных вектора, согласующихся со всеми агрегатами и дающих разные значения целевой величины',
    {"worlds": [base, [4, 6, 11, 5, 8, 4, 9, 6]]},
)

# 437. Deterministic API cannot use metadata discarded before invocation.
records = [
    dict(
        id=f"sample-{i:02}",
        route=f"/api/{i % 7}",
        tier=i % 4,
        region=f"r{i % 3}",
        required_output=i % 2,
    )
    for i in range(64)
]
records[47].update({k: records[11][k] for k in ("route", "tier", "region")})
records[47]["required_output"] = 1 - records[11]["required_output"]
add(
    "stateless_contract",
    "Implement a deterministic stateless router",
    "Реализуй детерминированную stateless-функцию маршрутизации, воспроизводящую required_output для каждого примера.",
    "function_collision",
    {"available_fields": ["route", "tier", "region"], "records": records},
    "Функция получает только available_fields. ID примера, время, история вызовов, случайность и внешнее состояние недоступны. Повтор одинакового входа обязан давать одинаковый результат.",
    '{"records": [ID, ID]} — два примера с одинаковыми доступными полями и разными обязательными выходами',
    {"records": ["sample-11", "sample-47"]},
)

# 438. Count *all* shorter outputs, including the empty one.
add(
    "universal_compressor",
    "Universal strict lossless compression",
    "Спроектируй кодек, который без потерь сжимает каждую допустимую входную строку до указанной максимальной длины.",
    "pigeonhole",
    {"alphabet_size": 256, "input_length": 64, "output_alphabet": 256, "max_output_length": 63},
    "Вход — любая строка из input_length символов алфавита alphabet_size. Выход имеет длину от 0 до max_output_length включительно. Декодеру доступна только выходная строка; внешние словари, метаданные и состояние запрещены. Кодек должен работать для всех входов, не только типичных.",
    '{"inputs": целое, "outputs": целое} — число возможных входов и число всех допустимых выходов, доказывающие нарушение инъективности',
    {"inputs": 256**64, "outputs": sum(256**i for i in range(64))},
)

# 439. Strict precedence cannot be repaired by running concurrently.
precedence = [dict(id=f"rule-{i:02}", before=f"tx-{i}", after=f"tx-{i + 1}") for i in range(30)]
precedence += [dict(id="rule-34", before="tx-22", after="tx-8")]
random.Random(439).shuffle(precedence)
add(
    "migration_order",
    "Find a compliant migration order",
    "Построй порядок миграций, удовлетворяющий всем обязательным требованиям before/after.",
    "order_cycle",
    {"precedence": precedence},
    "before должен строго завершиться до начала after. Все миграции обязательны. Одновременность, пропуск миграции и изменение требований не разрешены.",
    '{"edges": [ID, ...]} — ориентированный замкнутый цикл требований в порядке обхода',
    {"edges": [f"rule-{i:02}" for i in range(8, 22)] + ["rule-34"]},
)

# 440. An algebraic invariant rules out arbitrarily long search.
add(
    "ledger_reachability",
    "Reconcile a ledger using approved adjustments",
    "Доведи баланс initial до target, используя только разрешённые операции сколько угодно раз.",
    "residue",
    {"initial": 1_000_003, "target": 9_000_004, "allowed_deltas": [84, -126, 210, -294, 462, -630]},
    "Баланс — целое, промежуточные отрицательные значения допустимы; каждая операция прибавляет одну из allowed_deltas. Других операций нет.",
    '{"modulus": целое} — модуль больше 1, по которому все разрешённые приращения равны нулю, а target-initial не равно нулю',
    {"modulus": 42},
)

# 441. Clique certificate embedded in a sparse conflict graph.
vertices = [f"service-{i:02}" for i in range(48)]
clique = [vertices[i] for i in (7, 18, 29, 35, 42)]
conflicts = [[vertices[i], vertices[(i + 1) % 48]] for i in range(48)]
conflicts += [[a, b] for i, a in enumerate(clique) for b in clique[i + 1 :]]
random.Random(441).shuffle(conflicts)
add(
    "maintenance_windows",
    "Schedule services into four maintenance windows",
    "Размести все сервисы в colors окон обслуживания так, чтобы конфликтующие сервисы не попадали в одно окно.",
    "clique",
    {"vertices": vertices, "conflicts": conflicts, "colors": 4},
    "Каждый сервис назначается ровно в одно окно. conflicts — неориентированные пары несовместимости. Дополнительные окна и дробление сервиса запрещены.",
    '{"vertices": [ID, ...]} — попарно конфликтующее множество сервисов размером больше colors',
    {"vertices": clique},
)

# 442. Equality-oracle probability, not an assertion that luck is impossible.
add(
    "opaque_guid",
    "Recover an opaque random identifier with a guarantee",
    "Восстанови секретный случайный 128-битный идентификатор с вероятностью успеха не ниже required_success_probability за бюджет сервиса.",
    "guess_probability",
    {
        "random_bits": 128,
        "revealed_bit_positions": list(range(0, 128, 5)),
        "budget_seconds": 60,
        "max_checks_per_second": 10**9,
        "required_success_probability": [99, 100],
    },
    "Это абстрактный равномерный 128-битный GUID без служебных битов UUID. Значения битов в revealed_bit_positions уже известны; остальные независимы и равномерны. Единственный доступ к секрету — проверка равенства полного кандидата, максимум max_checks_per_second во всём сервисе независимо от параллелизма. Отрицательный ответ исключает только один кандидат; побочных каналов нет. Успех — кандидат с положительной проверкой до дедлайна; непроверенный последний ответ не считается. Требуется оценить достижимость гарантии, а не реальное обращение к сервису.",
    '{"candidate_count": целое, "max_attempts": целое, "success_upper_bound": [числитель, знаменатель]} — точная верхняя граница успеха лучшей стратегии',
    {
        "candidate_count": 2**102,
        "max_attempts": 60 * 10**9,
        "success_upper_bound": [60 * 10**9, 2**102],
    },
    budget=True,
)

# 443. Optimal binary search still cannot beat a decision-tree depth bound.
add(
    "diagnostic_queries",
    "Identify a fault through a constrained oracle",
    "Гарантированно определи один неисправный компонент среди outcomes равновозможных вариантов до дедлайна.",
    "decision_tree",
    {"outcomes": 2**47 + 1, "branches": 2, "seconds_per_query": 3, "budget_seconds": 120},
    "Единственный источник информации — последовательные запросы «входит ли неисправный компонент в выбранное подмножество?». Разрешено любое подмножество, два ответа, каждый запрос занимает не менее seconds_per_query, следующий начинается после ответа. Никаких начальных сведений о виновнике нет. Нужна гарантия для всех вариантов, не среднее время.",
    '{"min_queries": целое, "available_queries": целое} — минимальная глубина дерева решений и число последовательных запросов в бюджете',
    {"min_queries": 48, "available_queries": 40},
    budget=True,
)


def resource_case(
    slug, name, request, resource, units_per_second, parallel, amounts, budget_seconds, rules
):
    jobs = [dict(id=f"item-{i:03}", usage={resource: amount}) for i, amount in enumerate(amounts)]
    add(
        slug,
        name,
        request,
        "resource",
        {
            "budget_seconds": budget_seconds,
            "resources": {
                resource: {"units_per_second": units_per_second, "parallel_units": parallel}
            },
            "jobs": jobs,
        },
        rules
        + " Все jobs обязательны; usage — доказанный контрактом минимальный расход указанного ресурса. "
        "Пропуск работы, кэш, объединение операций, сжатие и изменение качества недопустимы. "
        "units_per_second — жёсткий максимальный темп на одну из parallel_units единиц ресурса. "
        "Ресурсы стартуют в t=0 без накопленного запаса; иной мощности нет.",
        '{"resource": имя, "jobs": [ID, ...], "required_units": целое, "capacity_units": целое} — подмножество обязательных работ, чей суммарный минимальный расход превышает всю ёмкость ресурса за бюджет',
        {
            "resource": resource,
            "jobs": [j["id"] for j in jobs],
            "required_units": sum(amounts),
            "capacity_units": units_per_second * parallel * budget_seconds,
        },
        budget=True,
    )


# 444. Network throughput lower bound, independent of local optimization.
resource_case(
    "archive_transfer",
    "Transfer a complete archive before expiry",
    "Доставь все перечисленные архивные объекты до закрытия канала.",
    "wire_bytes",
    12_500_000,
    1,
    [100_000_000 + i * 100_003 for i in range(64)],
    120,
    "Данные расположены за одним общим каналом; wire_bytes уже учитывает транспортное кодирование. Размеры в байтах, не битах.",
)

# 445. Service-wide rate limit is not multiplied by local threads.
resource_case(
    "api_inventory",
    "Read all pages under a global API quota",
    "Получить полный точный снимок всех разделов каталога через API до дедлайна.",
    "api_requests",
    7,
    1,
    [17 + i % 9 for i in range(64)],
    120,
    "Для каждого раздела дано минимальное число запросов страниц; API возвращает ровно одну страницу за запрос. Квота общая для всех клиентов, без burst и без пакетного endpoint.",
)

# 446. Critical path rather than total duration: unrelated jobs may run in parallel.
jobs = [
    dict(id=f"build-{i:02}", min_seconds=9 + i % 4, depends_on=[f"build-{i - 1:02}"] if i else [])
    for i in range(18)
]
jobs += [dict(id=f"audit-{i:02}", min_seconds=1, depends_on=[]) for i in range(40)]
random.Random(446).shuffle(jobs)
add(
    "build_critical_path",
    "Complete an uncached release pipeline",
    "Заверши все этапы сборки и аттестации до дедлайна, используя любое число параллельных workers.",
    "critical_path",
    {"jobs": jobs, "budget_seconds": 120},
    "Все этапы обязательны, старт не раньше t=0. depends_on должен завершиться до старта этапа. min_seconds — сертифицированная нижняя граница длительности этапа даже на самом быстром допустимом оборудовании. Кэш и обход этапов исключены; независимые этапы можно выполнять параллельно.",
    '{"jobs": [ID, ...], "min_seconds": целое} — цепочка непосредственных зависимостей и сумма нижних границ её длительности',
    {
        "jobs": [f"build-{i:02}" for i in range(18)],
        "min_seconds": sum(9 + i % 4 for i in range(18)),
    },
    budget=True,
)

# 447. This is a mandatory execution contract, not a conjectured algorithmic cost.
resource_case(
    "certified_evaluations",
    "Execute every required certification evaluation",
    "Получи сертификат полного исполнения всех обязательных проверок до дедлайна.",
    "signed_evaluations",
    25,
    4,
    [400 + i * 7 for i in range(50)],
    120,
    "Зачёт требует подписанный аппаратным модулем результат каждого исполнения, не предсказанный ответ. Имеется ровно четыре модуля, каждый выдаёт не более 25 подписей в секунду; они не клонируются.",
)

# 448. Output cardinality, irrespective of how fast a formula is discovered.
resource_case(
    "explicit_export",
    "Materialize a full uncompressed export",
    "Запиши весь обязательный экспорт на приёмник в явном формате до дедлайна; генератор или формула вместо данных не принимается.",
    "output_bytes",
    8_000_000,
    1,
    [40_000_000 + i * 131 for i in range(80)],
    120,
    "Приёмник принимает только перечисленные несжатые записи по единственному каналу. output_bytes — минимальное число байтов каждого обязательного блока уже в целевом формате.",
)

# 449. Sequential latency: parallel hardware cannot supply future tokens.
chain = [
    dict(id=f"challenge-{i:02}", min_seconds=4, depends_on=[f"challenge-{i - 1:02}"] if i else [])
    for i in range(35)
]
add(
    "sequential_attestation",
    "Finish a sequential remote attestation chain",
    "Получи финальную аттестацию, пройдя всю последовательность обязательных challenge-response шагов до дедлайна.",
    "critical_path",
    {"jobs": chain, "budget_seconds": 120},
    "Каждый следующий запрос требует непредсказуемый токен из предыдущего ответа. Сервер не принимает предварительные и пакетные запросы. min_seconds — минимальное время ответа, даже при нулевой сетевой задержке. Локально сгенерированные токены не принимаются. Все шаги обязательны, первый возможен только с t=0.",
    '{"jobs": [ID, ...], "min_seconds": целое} — цепочка непосредственных зависимостей и сумма её минимальных задержек',
    {"jobs": [j["id"] for j in chain], "min_seconds": 140},
    budget=True,
)

# 450. Earliest possible release is later than the requested deadline.
releases = [
    dict(id=f"partition-{i:02}", release_seconds=15 + i % 13, min_processing_seconds=3)
    for i in range(55)
]
releases[37].update(release_seconds=119, min_processing_seconds=2)
add(
    "sealed_partition",
    "Restore all sealed partitions before the deadline",
    "Восстанови и проверь все разделы резервной копии до дедлайна.",
    "release",
    {"jobs": releases, "budget_seconds": 120},
    "release_seconds — самое раннее время выдачи единственного ключа раздела относительно t=0; до него доступ и обработка физически недоступны. min_processing_seconds — нижняя граница обработки после выдачи ключа. Все разделы обязательны. Независимые разделы можно обрабатывать параллельно без лимита workers.",
    '{"job": ID, "earliest_finish_seconds": целое} — обязательный раздел, у которого даже самое раннее завершение позже дедлайна',
    {"job": "partition-37", "earliest_finish_seconds": 121},
    budget=True,
)

# 451. Total worker-time lower bound remains valid even with preemption.
resource_case(
    "worker_capacity",
    "Schedule mandatory jobs on a fixed worker pool",
    "Составь расписание, завершающее все обязательные работы на доступных workers до дедлайна.",
    "worker_seconds",
    1,
    8,
    [18 + i % 11 for i in range(48)],
    120,
    "Каждая работа требует не меньше указанного worker_seconds суммарного времени исполнения. Прерывания, миграции и любое распределение работы разрешены бесплатно; добавлять workers нельзя. Даже такое максимально благоприятное расписание обязано уложиться в общий бюджет.",
)
