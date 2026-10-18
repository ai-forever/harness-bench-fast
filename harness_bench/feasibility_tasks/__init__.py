"""Wave 19 / v0.19.0: twenty matched pairs, forty independently run tasks."""

from __future__ import annotations

import random

from harness_bench.feasibility_tasks.hard_contracts import (
    CONTRACT_PAIRS,
    check_certificate,
    check_solution,
)
from harness_bench.feasibility_tasks.hard_search import build_pairs, make_task
from harness_bench.feasibility_tasks.task_builders import make_contract_task, make_search_task

SEARCH_PAIRS = build_pairs()


def _reference(pairs=CONTRACT_PAIRS):
    formats = list(
        dict.fromkeys(
            schema
            for pair in pairs
            for case in (pair["positive"], pair["negative"])
            for schema in [
                case["schema"],
                case["solution_schema"],
                *case.get("component_formats", []),
            ]
        )
    )
    return (
        "# Форматы решений и доказательств\n\n"
        "Выберите подходящий формат по условиям. Этот общий справочник одинаков для "
        "всех контрактных задач и не указывает, решаема ли конкретная задача. "
        "В составном contract.json точные форматы каждого обязательства указаны "
        "в его response_formats; используйте их, а не формат соседнего обязательства. "
        "Для status=solved поместите конструкцию в solution; для impossible или "
        "budget_exceeded — свидетельство в certificate.\n\n"
        + "\n\n".join("- " + schema for schema in formats)
        + "\n"
    )


_entries = [
    (kind, pair, side)
    for kind, pairs in (("contract", CONTRACT_PAIRS), ("search", SEARCH_PAIRS))
    for pair in pairs
    for side in ("positive", "negative")
]
# Neither numeric parity nor alphabetical half labels reveal the correct action.
random.Random(1900471).shuffle(_entries)
FEASIBILITY_TASKS = []
FEASIBILITY_PAIRS: dict[str, tuple[str, str]] = {}
FEASIBILITY_CASES = {}
_pair_members: dict[str, list[str]] = {}
_reference_text = _reference()
for number, (kind, pair, side) in enumerate(_entries, 432):
    if kind == "contract":
        task = make_contract_task(
            number, pair[side], _reference_text, check_solution, check_certificate
        )
    else:
        task = make_task(number, pair, pair[side])
    FEASIBILITY_TASKS.append(task)
    FEASIBILITY_CASES[task.id] = {"kind": kind, "pair": pair, "side": side}
    _pair_members.setdefault(pair["slug"], []).append(task.id)
FEASIBILITY_PAIRS = {pair_id: tuple(ids) for pair_id, ids in _pair_members.items()}


def legacy_registry():
    """Original simple cases for protocol regression only, never registered tasks."""
    from .contract_pairs import CONTRACT_PAIRS as old_contracts
    from .contract_pairs import check_solution as old_solution
    from .search_cases import SEARCH_PAIRS as old_searches

    entries = [
        (kind, pair, side)
        for kind, pairs in (("contract", old_contracts), ("search", old_searches))
        for pair in pairs
        for side in ("positive", "negative")
    ]
    random.Random(1900471).shuffle(entries)
    tasks, cases = [], {}
    reference = _reference(old_contracts)
    for number, (kind, pair, side) in enumerate(entries, 432):
        task = (
            make_contract_task(number, pair[side], reference, old_solution)
            if kind == "contract"
            else make_search_task(number, pair, pair[side])
        )
        tasks.append(task)
        cases[task.id] = {"kind": kind, "pair": pair, "side": side}
    return tasks, cases
