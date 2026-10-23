"""Independent action-language interpreter and tiny exhaustive scheduler for 479 v5."""

from __future__ import annotations

import copy
import itertools

import pytest

from harness_bench.general_hard import data_v5 as v


class Invalid(Exception):
    pass


def simulate(x, actions, mutant=None, require_goal=True):
    """Direct contract interpreter, deliberately no packed-cache/DP representation."""
    definitions = {r["id"]: r for r in x["resources"]}
    pieces = {p["id"]: p for p in x["pieces"]}
    cache = {}
    active = None
    remaining = 0
    position = 0
    cost = 0
    path = []
    for time, action in enumerate(actions):
        if position == x["n"] and active is None and not any(c["dirty"] for c in cache.values()):
            raise Invalid("actions after finish")
        held = set(pieces[active]["requires"]) if active else set()
        if action["drop"] != sorted(set(action["drop"])):
            raise Invalid("drop order")
        for key in action["drop"]:
            if key not in cache or key in held or (cache[key]["dirty"] and mutant != "dirty_drop"):
                raise Invalid("illegal eviction")
            del cache[key]
        start = action["start"]
        if start is not None:
            p = pieces[start]
            if (
                active is not None
                or p["a"] != position
                or time < p["release"]
                or time + p["ticks"] > p["deadline"]
            ):
                raise Invalid("illegal start")
            if any(
                key not in cache or cache[key]["fetch"] != definitions[key]["fetch_ticks"]
                for key in p["requires"]
            ):
                raise Invalid("resource not ready at start")
            active, remaining = start, p["ticks"]
            path.append(start)
            held = set(p["requires"])
        operation = action["io"]
        if mutant == "serial_io" and active and operation:
            raise Invalid("mutant serializes CPU and IO")
        if mutant == "forget_fetch_pause":
            for key, c in cache.items():
                if c["fetch"] < definitions[key]["fetch_ticks"] and (
                    not operation or operation["resource"] != key or operation["op"] != "fetch"
                ):
                    c["fetch"] = 0
        if operation:
            key, op = operation["resource"], operation["op"]
            r = definitions[key]
            if key in held and not (mutant == "pinned_writeback" and op == "writeback"):
                raise Invalid("IO of pinned resource")
            if op == "fetch":
                if not any(a <= time < b for a, b in r["available"]):
                    raise Invalid("fetch outside availability")
                if key in cache and cache[key]["fetch"] == r["fetch_ticks"]:
                    raise Invalid("duplicate fetch")
                cache.setdefault(key, {"fetch": 0, "dirty": False, "writeback": 0})
                cache[key]["fetch"] += 1
                cost += r["fetch_cost"]
            else:
                if key not in cache or not cache[key]["dirty"]:
                    raise Invalid("writeback of clean resource")
                cache[key]["writeback"] += 1
                cost += r["writeback_cost"]
                if cache[key]["writeback"] == r["writeback_ticks"]:
                    cache[key]["dirty"] = False
                    cache[key]["writeback"] = 0
        occupied = sum(
            definitions[key]["bytes"]
            for key, c in cache.items()
            if mutant != "unreserved_fetch" or c["fetch"] == definitions[key]["fetch_ticks"]
        )
        if occupied > x["cache_bytes"]:
            raise Invalid("cache overflow")
        if active:
            remaining -= 1
            if remaining == 0:
                p = pieces[active]
                position = p["b"]
                for key in p["writes"]:
                    cache[key]["dirty"] = True
                    if mutant != "keep_writeback_progress":
                        cache[key]["writeback"] = 0
                active = None
    goal = position == x["n"] and active is None and not any(c["dirty"] for c in cache.values())
    if require_goal and not goal:
        raise Invalid("not durable at finish")
    trace = tuple(
        (
            a["start"] or "",
            tuple(a["drop"]),
            (a["io"] or {}).get("op", ""),
            (a["io"] or {}).get("resource", ""),
        )
        for a in actions
    )
    return (len(actions), cost, len(path), tuple(path), trace), goal


def exhaustive(x, mutant=None):
    """Enumerate full action histories; validity only comes from independent interpreter."""
    ids = sorted(r["id"] for r in x["resources"])
    drops = [list(c) for n in range(len(ids) + 1) for c in itertools.combinations(ids, n)]
    starts = [None] + sorted(p["id"] for p in x["pieces"])
    io = [None] + [{"op": op, "resource": key} for op in ("fetch", "writeback") for key in ids]
    alphabet = [
        {"start": start, "drop": drop, "io": operation}
        for start, drop, operation in itertools.product(starts, drops, io)
    ]
    histories = [[]]
    for _ in range(x["horizon"]):
        following, goals = [], []
        for history in histories:
            for action in alphabet:
                candidate = history + [action]
                try:
                    objective, goal = simulate(x, candidate, mutant, require_goal=False)
                except Invalid:
                    continue
                if goal:
                    goals.append((objective, candidate))
                else:
                    following.append(candidate)
        if goals:
            obj, actions = min(goals, key=lambda item: item[0])
            return {"finish": obj[0], "io_cost": obj[1], "plan": list(obj[3]), "schedule": actions}
        histories = following
    return {"plan": None}


@pytest.mark.parametrize("case", range(len(v.CASES)))
def test_every_gold_schedule_has_independent_semantic_replay(case):
    x, expected = v.CASES[case]
    if expected["plan"] is not None:
        objective, goal = simulate(x, expected["schedule"])
        assert goal
        assert objective[:2] == (expected["finish"], expected["io_cost"])
        assert list(objective[3]) == expected["plan"]


@pytest.mark.parametrize(
    "fetch,writeback,writes", [(1, 1, False), (2, 1, False), (1, 2, True), (2, 2, True)]
)
def test_independent_full_history_oracle(fetch, writeback, writes):
    x = {
        "n": 1,
        "cache_bytes": 1,
        "horizon": 5,
        "resources": [v.resource("r", fetch=fetch, writeback=writeback)],
        "pieces": [v.piece("p", 0, 1, ["r"], ["r"] if writes else [])],
    }
    assert v.async_repair_solve(x) == exhaustive(x)


def test_two_resource_ties_and_branching_against_exhaustive_oracle():
    x = {
        "n": 2,
        "cache_bytes": 2,
        "horizon": 4,
        "resources": [v.resource("a"), v.resource("b")],
        "pieces": [
            v.piece("z", 0, 2, ["a"], ticks=2),
            v.piece("x", 0, 1, ["a"]),
            v.piece("y", 1, 2, ["b"]),
        ],
    }
    assert v.async_repair_solve(x) == exhaustive(x)
    y = copy.deepcopy(x)
    y["pieces"] = [v.piece("z", 0, 2, ["a"]), v.piece("a", 0, 2, ["b"])]
    assert v.async_repair_solve(y) == exhaustive(y)
    assert v.async_repair_solve(y)["plan"] == ["a"]


@pytest.mark.parametrize(
    "case,finish,cost",
    [(0, 4, 3), (1, 5, 5), (2, 6, 4), (3, 7, 4), (4, 8, 4), (5, 6, 4), (7, 7, 3)],
)
def test_hand_derived_interaction_optima(case, finish, cost):
    assert (v.CASES[case][1]["finish"], v.CASES[case][1]["io_cost"]) == (finish, cost)


def action(start=None, drop=(), op=None, resource=None):
    return {
        "start": start,
        "drop": list(drop),
        "io": None if op is None else {"op": op, "resource": resource},
    }


@pytest.mark.parametrize(
    "case,mutant,actions",
    [
        (
            1,
            "serial_io",
            [
                action(op="fetch", resource="a"),
                action("p", op="fetch", resource="b"),
                action(op="fetch", resource="b"),
                action(op="fetch", resource="b"),
                action("q", op="writeback", resource="a"),
            ],
        ),
        (
            2,
            "forget_fetch_pause",
            [
                action(op="fetch", resource="a"),
                action("p", op="fetch", resource="b"),
                action(),
                action(op="fetch", resource="b"),
                action(op="fetch", resource="b"),
                action("q"),
            ],
        ),
    ],
)
def test_mutants_reject_feasible_parallel_or_resumed_schedule(case, mutant, actions):
    assert simulate(v.CASES[case][0], actions)[1]
    with pytest.raises(Invalid):
        simulate(v.CASES[case][0], actions, mutant)


@pytest.mark.parametrize(
    "case,mutant,actions",
    [
        (
            3,
            "unreserved_fetch",
            [
                action(op="fetch", resource="a"),
                action("p", op="fetch", resource="b"),
                action(op="fetch", resource="b"),
                action(drop=["a"], op="fetch", resource="b"),
                action("q"),
            ],
        ),
        (
            4,
            "keep_writeback_progress",
            [
                action(op="fetch", resource="a"),
                action("p"),
                action(op="writeback", resource="a"),
                action(op="writeback", resource="a"),
                action("q"),
                action(op="writeback", resource="a"),
            ],
        ),
        (
            5,
            "dirty_drop",
            [
                action(op="fetch", resource="a"),
                action("p"),
                action(drop=["a"], op="fetch", resource="b"),
                action("q"),
            ],
        ),
        (
            7,
            "pinned_writeback",
            [
                action(op="fetch", resource="b"),
                action("p"),
                action("q", op="writeback", resource="b"),
                action(op="writeback", resource="b"),
                action(),
            ],
        ),
    ],
)
def test_mutants_admit_impossible_faster_schedules(case, mutant, actions):
    x, gold = v.CASES[case]
    assert simulate(x, actions, mutant)[1]
    assert len(actions) < gold["finish"]
    with pytest.raises(Invalid):
        simulate(x, actions)


def test_fetch_completion_is_not_visible_to_same_tick_cpu_start():
    x = v.CASES[0][0]
    with pytest.raises(Invalid, match="not ready"):
        simulate(x, [action("p", op="fetch", resource="a")], require_goal=False)


def test_partial_fetch_auto_cancel_is_sunk_cost_and_needs_no_drop():
    x = {
        "n": 1,
        "cache_bytes": 2,
        "horizon": 2,
        "resources": [v.resource("a"), v.resource("b", fetch=3, fetch_cost=5)],
        "pieces": [v.piece("p", 0, 1, ["a"])],
    }
    result, goal = simulate(
        x, [action(op="fetch", resource="a"), action("p", op="fetch", resource="b")]
    )
    assert goal and result[:2] == (2, 6)
    assert v.async_repair_solve(x)["io_cost"] == 1


def test_impossible_availability_and_order_invariance():
    assert v.CASES[6][1] == {"plan": None}
    assert v.CASES[18][1] == v.CASES[20][1]


def test_active_task_identity_budget_and_both_revision_tags():
    from harness_bench.general_hard import data_tasks

    task = next(t for t in data_tasks.TASKS if t.id.startswith("task_479_"))
    assert {"hardening-v4", "hardening-v5"} <= set(task.tags)
    assert task.max_timeout_seconds == 900
    assert task.runtime_protocol == "general-hard-program-v1"
    assert "В КОНЦЕ tick сначала завершается I/O" in task.prompt


def test_piece_count_precedes_lex_path_and_cost_precedes_piece_count():
    x = {
        "n": 2,
        "cache_bytes": 2,
        "horizon": 4,
        "resources": [v.resource("a", fetch_cost=0), v.resource("b", fetch_cost=0)],
        "pieces": [
            v.piece("z", 0, 2, ["a"], ticks=2),
            v.piece("a", 0, 1, ["a"]),
            v.piece("b", 1, 2, ["b"]),
        ],
    }
    assert v.async_repair_solve(x) == exhaustive(x)
    assert v.async_repair_solve(x)["plan"] == ["z"]
    # Equal finish but the single-piece route needs an extra paid donor.
    x["resources"][1]["fetch_cost"] = 2
    x["pieces"][0]["requires"] = ["a", "b"]
    x["pieces"][0]["ticks"] = 1
    x["pieces"][2]["requires"] = ["a"]
    assert v.async_repair_solve(x) == exhaustive(x)
    assert v.async_repair_solve(x)["plan"] == ["a", "b"]


def test_release_deadline_and_fetch_tick_boundary_oracle():
    x = {
        "n": 1,
        "cache_bytes": 1,
        "horizon": 4,
        "resources": [v.resource("a", fetch=2, available=[[0, 1], [2, 3]])],
        "pieces": [v.piece("p", 0, 1, ["a"], release=1, deadline=4)],
    }
    assert v.async_repair_solve(x) == exhaustive(x)
    assert v.async_repair_solve(x)["finish"] == 4
    x["pieces"][0]["deadline"] = 3
    assert v.async_repair_solve(x) == exhaustive(x) == {"plan": None}
