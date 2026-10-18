"""Six controlled search pairs. One meaningful capability/budget change per pair."""

from __future__ import annotations

from copy import deepcopy

SEARCH_PAIRS = []


def _pair(slug, name, base, field, positive, negative):
    halves = []
    for value in (positive, negative):
        spec = deepcopy(base)
        spec[field] = value
        halves.append(spec)
    SEARCH_PAIRS.append({"slug": slug, "name": name, "positive": halves[0], "negative": halves[1]})


_BASE = {
    "size": 10000,
    "budget": 10,
    "mode": "scan",
    "hint": None,
    "unknown_size": False,
    "unknown_mode": False,
    "target_key": "order-K91",
    "threshold": [95, 100],
    "date": "2026-01-01",
}
_pair("search_pool", "Найти запись в доступном пуле", _BASE, "size", 8, 10000)
_pair("search_index", "Выбрать доступный способ поиска", _BASE, "mode", "key", "date")
_pair(
    "search_hint",
    "Использовать достоверный список кандидатов",
    _BASE,
    "hint",
    [f"r{i:05}" for i in range(6)],
    [f"r{i:05}" for i in range(1000)],
)
_pair(
    "search_count", "Сначала выяснить размер базы", dict(_BASE, unknown_size=True), "size", 8, 10000
)
_pair(
    "search_boundary",
    "Проверить достижимость порога 95 процентов",
    dict(_BASE, size=100),
    "budget",
    95,
    94,
)
_pair(
    "search_capabilities",
    "Сначала выяснить возможности инструмента",
    dict(_BASE, unknown_mode=True),
    "mode",
    "key",
    "scan",
)


def candidate_ids(spec):
    return spec["hint"] if spec["hint"] is not None else [f"r{i:05}" for i in range(spec["size"])]


def target_id(spec):
    """A reproducible draw kept inside the closed service, never a public hint.

    The boundary fixture intentionally exercises the legitimate unlucky tail.
    Other fixture targets use an unbiased deterministic PRNG draw. Probability
    scoring always evaluates the strategy, never infers quality from this draw.
    """
    import random

    candidates = candidate_ids(spec)
    if spec["size"] == 100 and spec["budget"] in (94, 95):
        return candidates[99]
    return random.Random(190043).choice(candidates)


def public_contract(spec):
    return {
        "records": None if spec["unknown_size"] else spec["size"],
        "query_budget": spec["budget"],
        "search_mode": None if spec["unknown_mode"] else spec["mode"],
        "trusted_candidate_ids": spec["hint"],
        "target_unique_key": spec["target_key"],
        "all_records_date": spec["date"],
        "required_success_probability": spec["threshold"],
    }
