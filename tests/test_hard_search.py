"""Independent small-instance oracle and actual service execution."""

import itertools
import json
import random
from copy import deepcopy
from fractions import Fraction

import pytest

from harness_bench.feasibility_tasks.hard_search import (
    assess,
    build_pairs,
    coverage,
    make_task,
    optimize,
    ratio,
    remaining,
    service,
    target,
    total,
)
from harness_bench.feasibility_tasks.trusted_service import get_session, trusted_service_available
from harness_bench.reflect_tasks.common import call

PAIRS = build_pairs()
require_unix_service = pytest.mark.skipif(
    not trusted_service_available(), reason="Unix-socket host service unavailable"
)


def brute(spec, qb, ib):
    # All subsets, including overlapping routes: independently validates the
    # dominance reduction to one route per shard used by production DP.
    best = 0
    routes = spec["routes"]
    for bits in itertools.product((False, True), repeat=len(routes)):
        selected = [r for r, bit in zip(routes, bits, strict=True) if bit]
        if any(r["id"] not in spec["enabled"] for r in selected):
            continue
        if sum(r["query_cost"] for r in selected) > qb or sum(r["io_cost"] for r in selected) > ib:
            continue
        members = {
            (r["shard"], i)
            for r in selected
            for i in range(min(r["prefix"], spec["population"][r["shard"]]))
        }
        best = max(best, len(members))
    return best


def test_two_quota_dp_against_exhaustive_actual_records():
    rng = random.Random(9177)
    for _ in range(40):
        spec = dict(population={"a": 5, "b": 7, "c": 4}, routes=[], enabled=[])
        for i in range(8):
            spec["routes"].append(
                dict(
                    id=str(i),
                    shard=rng.choice("abc"),
                    prefix=rng.randrange(1, 10),
                    query_cost=rng.randrange(1, 4),
                    io_cost=rng.randrange(1, 5),
                )
            )
        spec["enabled"] = [str(i) for i in range(8) if rng.randrange(4)]
        q, b = rng.randrange(2, 10), rng.randrange(2, 12)
        value, ids = optimize(spec, q, b)
        assert value == brute(spec, q, b)
        assert coverage(spec, ids) == value


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p["slug"])
def test_controls_and_exact_threshold(pair):
    pos, neg = pair["positive"], pair["negative"]
    assert Fraction(optimize(pos, *remaining(pos))[0], total(pos)) >= Fraction(95, 100)
    assert Fraction(optimize(neg, *remaining(neg))[0], total(neg)) < Fraction(95, 100)
    assert {k for k in pos if pos[k] != neg[k]} == {"query_budget"}


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p["slug"])
@pytest.mark.parametrize("side", ["positive", "negative"])
@require_unix_service
def test_gold_actual_closed_client_and_replay(pair, side, tmp_path):
    task = make_task(999, pair, pair[side])
    task.setup(tmp_path)
    assert not task.verify(tmp_path).passed
    task.apply_gold(tmp_path)
    result = task.verify(tmp_path)
    assert result.passed, result.message
    metrics = json.loads(result.message)["wave19"]
    assert metrics["decision_correct"] and metrics["extra_requests"] == 0


def send(ws, client, command, payload):
    (ws / "answer.json").write_text(json.dumps(payload))
    response, code = call(ws, client, command, "--file", "answer.json")
    assert code == 0, response


@require_unix_service
def test_verified_lucky_find_without_plan_is_accepted(tmp_path):
    spec = deepcopy(PAIRS[0]["negative"])
    shard, offset, found = target(spec)
    r = next(
        r
        for r in spec["routes"]
        if r["shard"] == shard
        and r["prefix"] > offset
        and r["query_cost"] <= spec["query_budget"]
        and r["io_cost"] <= spec["io_budget"]
    )
    task = make_task(999, PAIRS[0], spec)
    client = service("999", spec)
    task.setup(tmp_path)
    response, code = call(tmp_path, client, "probe", "--route", r["id"])
    assert code == 0 and response["found_id"] == found
    send(tmp_path, client, "finish", {"found_id": found})
    assert task.verify(tmp_path).passed
    state = get_session(tmp_path).replay().state
    ok, metrics = assess(spec, state)
    assert ok and metrics["success_basis"] == "verified_find"
    assert not metrics["strategy_correct"]


@require_unix_service
def test_missing_diagnostics_cannot_be_replaced_by_guessed_numbers(tmp_path):
    pair = next(p for p in PAIRS if p["slug"] == "search_count")
    spec = pair["negative"]
    task = make_task(999, pair, spec)
    client = service("999", spec)
    task.setup(tmp_path)
    p = Fraction(optimize(spec)[0], total(spec))
    send(tmp_path, client, "plan", dict(decision="stop", routes=[], success_probability=ratio(p)))
    send(tmp_path, client, "finish", {"found_id": None})
    result = task.verify(tmp_path)
    assert not result.passed and "diagnostics" in result.message


@require_unix_service
def test_unlucky_tail_passes_and_forged_probabilities_do_not(tmp_path):
    spec = deepcopy(PAIRS[4]["positive"])
    best, ids = optimize(spec, *remaining(spec))
    assert Fraction(95, 100) <= Fraction(best, total(spec)) < 1
    selected = {r["shard"]: r["prefix"] for r in spec["routes"] if r["id"] in ids}
    for seed in range(1000):
        spec["target_seed"] = seed
        s, rank, _ = target(spec)
        if rank >= selected.get(s, 0):
            break
    else:
        raise AssertionError("no unlucky draw")
    task = make_task(999, PAIRS[4], spec)
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    state = get_session(tmp_path).replay().state
    assert state["found"] is None and task.verify(tmp_path).passed
    state["plan"]["success_probability"] = [1, 1]
    assert not assess(spec, state)[0]
    state["plan"]["success_probability"] = ratio(Fraction(best, total(spec)))
    state["finish"] = {"found_id": "invented"}
    assert not assess(spec, state)[0]


@pytest.mark.parametrize("quota", ["query_budget", "io_budget"])
@require_unix_service
def test_lucky_find_does_not_override_quota_errors(tmp_path, quota):
    spec = deepcopy(PAIRS[0]["positive"])
    task = make_task(999, PAIRS[0], spec)
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    state = get_session(tmp_path).replay().state
    state["query_used" if quota == "query_budget" else "io_used"] = spec[quota] + 1
    assert not assess(spec, state)[0]
