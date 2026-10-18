"""Current wave 19 fixtures; original small fixtures remain regression controls."""

from copy import deepcopy
from random import Random

from . import (
    coupled_assignment,
    coupled_flow,
    coupled_schedule,
    hard_algebra,
    hard_graphs,
    hard_systems,
    nonnegative_aggregate,
)
from .checks import check as base_certificate
from .contract_pairs import CONTRACT_PAIRS as BASE_PAIRS
from .contract_pairs import check_solution as base_solution

ATOMIC_PAIRS = deepcopy(BASE_PAIRS)
for module in (
    hard_graphs,
    hard_algebra,
    nonnegative_aggregate,
    hard_systems,
    coupled_assignment,
    coupled_flow,
    coupled_schedule,
):
    module.upgrade_pairs(ATOMIC_PAIRS)


def compose_pairs(pairs):
    """Release readiness is a conjunction, with an early stopping witness.

    Components have separate resources: this is a feasibility review of three
    mandatory deliverables, not an unstated shared-resource scheduling problem.
    Author answers, statuses and kind tags are absent from the public bundle.
    """
    composed = []
    for i, original in enumerate(pairs):
        rng = Random(190310 + i)
        indexes = [i, (i + 5) % len(pairs), (i + 9) % len(pairs)]
        rng.shuffle(indexes)
        identities = [f"requirement-{n:04x}" for n in rng.sample(range(65536), 3)]
        pair = deepcopy(original)
        pair["name"] = "Проверка готовности системы по трём обязательным контрактам"
        for side in ("positive", "negative"):
            components = [
                (identity, pairs[j][side if j == i else "positive"])
                for identity, j in zip(identities, indexes, strict=True)
            ]
            case = pair[side]
            case["name"] = pair["name"]
            case["request"] = (
                "Проверь готовность системы: должны быть выполнимы все три обязательства из requirements в contract.json."
            )
            case["instructions"] = (
                "У каждого обязательства собственные данные, правила и ресурсы; ресурсы между "
                "обязательствами не делятся. Вероятностная гарантия проверяется отдельно для "
                "каждого обязательства, совместная вероятность не вводится. Для разрешения "
                "запуска предъяви конструкции для ВСЕХ трёх обязательств. Для остановки достаточно "
                "одного проверяемого свидетельства невыполнимости любого обязательства. "
                "Не требуется доказывать остальные после обнаружения такого препятствия. "
                "Частичное решение не означает готовность системы. Общий лимит анализа — "
                "120 секунд на всю проверку, не на каждое обязательство. Формальные бюджеты "
                "операций указаны внутри соответствующих данных. Форматы внутренних конструкций "
                "и сертификатов даны в response_formats непосредственно у каждого обязательства; "
                "выбирай его собственный формат. CERTIFICATES.md содержит общий справочник "
                "и описание внешней обёртки.\n"
                'Полная структура отказа для этой составной задачи: '
                '{"status":"impossible","certificate":{"requirement":"<ID>",'
                '"certificate":{...}}}. Для ограничения бюджета можно использовать '
                'status="budget_exceeded" с той же структурой. Замени <ID> на id '
                "невыполнимого обязательства из requirements, а {...} — на его свидетельство "
                "по response_formats.certificate. Оба уровня certificate обязательны; "
                "requirement находится внутри внешнего certificate, не в корне ответа."
            )
            case["component_formats"] = list(
                dict.fromkeys(
                    c[key] for _, c in components for key in ("schema", "solution_schema")
                )
            )
            case["schema"] = (
                '{"requirement": ID, "certificate": {...}} — a valid impossibility/budget witness for that requirement, in its component format.'
            )
            case["solution_schema"] = (
                '{"requirements": {requirement ID: {...}, ...}} — constructive solutions for ALL requirements, each in its component format. No status wrapper inside these component objects.'
            )
            case["spec"] = dict(
                kind="conjunction",
                status=original[side]["spec"]["status"],
                requirements=[dict(id=k, spec=deepcopy(c["spec"])) for k, c in components],
            )
            case["public_spec"] = dict(
                requirements=[
                    dict(
                        id=k,
                        goal=c["request"],
                        rules=c["instructions"],
                        response_formats=dict(
                            solution=c["solution_schema"], certificate=c["schema"]
                        ),
                        contract={
                            key: deepcopy(value)
                            for key, value in c["spec"].items()
                            if key not in ("kind", "status")
                        },
                    )
                    for k, c in components
                ]
            )
            if side == "positive":
                case["gold"] = {"requirements": {k: deepcopy(c["gold"]) for k, c in components}}
            else:
                key, bad = next((k, c) for k, c in components if c["spec"]["status"] != "solved")
                case["gold"] = dict(requirement=key, certificate=deepcopy(bad["gold"]))
        composed.append(pair)
    return composed


CONTRACT_PAIRS = compose_pairs(ATOMIC_PAIRS)


def check_certificate(spec, report):
    # A local deadline obstruction can truthfully be called either impossible
    # under the contract or budget-exceeded. The witness determines validity;
    # a private author label must not reject an otherwise correct certificate.
    if (
        spec["status"] in ("impossible", "budget_exceeded")
        and isinstance(report, dict)
        and report.get("status") in ("impossible", "budget_exceeded")
    ):
        report = {**report, "status": spec["status"]}
    if spec["kind"] == "conjunction":
        try:
            if report["status"] not in ("impossible", "budget_exceeded"):
                return False
            c = report["certificate"]
            child = next(r["spec"] for r in spec["requirements"] if r["id"] == c["requirement"])
            return check_certificate(
                child, {"status": report["status"], "certificate": c["certificate"]}
            )
        except (KeyError, TypeError, ValueError, StopIteration):
            return False
    for module in (
        hard_algebra, hard_systems, coupled_assignment, coupled_flow, coupled_schedule,
        nonnegative_aggregate,
    ):
        if spec["kind"] in module.KINDS:
            return module.check_certificate(spec, report)
    return base_certificate(spec, report)


def check_solution(spec, solution):
    if spec["kind"] == "conjunction":
        try:
            children = solution["requirements"]
            return (
                isinstance(children, dict)
                and set(children) == {r["id"] for r in spec["requirements"]}
                and all(check_solution(r["spec"], children[r["id"]]) for r in spec["requirements"])
            )
        except (KeyError, TypeError, ValueError):
            return False
    for module in (
        hard_algebra, hard_systems, coupled_assignment, coupled_flow, coupled_schedule,
        nonnegative_aggregate,
    ):
        if spec["kind"] in module.KINDS:
            return module.check_solution(spec, solution)
    return base_solution(spec, solution)
