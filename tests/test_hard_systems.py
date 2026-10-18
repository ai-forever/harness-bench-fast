"""Independent tiny-instance checks and generated pair contract tests."""

import random
from copy import deepcopy
from itertools import product

import pytest

from harness_bench.feasibility_tasks.contract_pairs import CONTRACT_PAIRS
from harness_bench.feasibility_tasks.hard_systems import (
    KINDS,
    _build_schedule,
    _key,
    _quorum_times,
    check_certificate,
    check_solution,
    upgrade_pairs,
)


def pairs():
    return [
        p for p in upgrade_pairs(deepcopy(CONTRACT_PAIRS)) if p["negative"]["spec"]["kind"] in KINDS
    ]


@pytest.mark.parametrize("pair", pairs(), ids=lambda p: p["slug"])
def test_generated_gold_and_cross_rejection(pair):
    n, p = pair["negative"], pair["positive"]
    assert check_certificate(n["spec"], dict(status=n["spec"]["status"], certificate=n["gold"]))
    assert check_solution(p["spec"], p["gold"])
    assert not check_solution(n["spec"], p["gold"])
    positive_as_negative = {**p["spec"], "status": n["spec"]["status"]}
    assert not check_certificate(
        positive_as_negative, dict(status=n["spec"]["status"], certificate=n["gold"])
    )
    for field in ("instructions", "schema", "solution_schema", "request", "name"):
        assert n[field] == p[field]
    for bad in (None, {}, [], {"schedule": {}}, {"segments": []}):
        assert not check_solution(p["spec"], bad)
        assert not check_certificate(n["spec"], bad)


def test_normalizer_independent_bit_parity():
    p = next(p for p in pairs() if p["slug"] == "stateless_contract")["positive"]
    s = p["spec"]
    for r in s["records"]:
        left, right = r["left"], r["right"]
        for table in s["round_tables"]:
            previous_left = left
            left = right
            right = previous_left ^ table[left]
        bits = [int(x) for x in f"{left:05b}{right:05b}"]
        expected = tuple(
            sum(a * int(b) for a, b in zip(bits, f"{m:010b}", strict=True)) % 2
            for m in s["projection_masks"]
        )
        assert _key(s, r) == expected
    # Order is immaterial; an alternative valid table is accepted.
    alternate = deepcopy(p["gold"])
    alternate["table"].reverse()
    assert check_solution(s, alternate)
    alternate["table"][0]["output"] += 1
    assert not check_solution(s, alternate)


def _brute_slots(jobs, horizon):
    ids = [j["id"] for j in jobs]
    for assignment in product([None] + ids, repeat=horizon):
        if all(
            sum(assignment[t] == j["id"] for t in range(horizon)) == j["work"]
            and all(
                assignment[t] != j["id"] or j["release"] <= t < j["deadline"]
                for t in range(horizon)
            )
            for j in jobs
        ):
            return dict(
                segments=[
                    dict(job=j, start=t, finish=t + 1)
                    for t, j in enumerate(assignment)
                    if j is not None
                ]
            )
    return None


def test_interval_overload_matches_exhaustive_small_schedules():
    windows = [(0, 1), (0, 2), (1, 3), (0, 3), (2, 3)]
    for a, b in product(windows, repeat=2):
        jobs = [
            dict(id="a", release=a[0], deadline=a[1], work=2),
            dict(id="b", release=b[0], deadline=b[1], work=1),
        ]
        s = dict(kind="preemptive_window", status="budget_exceeded", budget_seconds=3, jobs=jobs)
        witness = _brute_slots(jobs, 3)
        overloads = [
            dict(start=x, finish=y)
            for x in range(3)
            for y in range(x + 1, 4)
            if check_certificate(s, dict(status=s["status"], certificate=dict(start=x, finish=y)))
        ]
        assert bool(overloads) == (witness is None)
        if witness:
            assert check_solution(s, witness)


def test_fractional_preemption_and_overlap():
    s = dict(
        kind="preemptive_window",
        budget_seconds=2,
        jobs=[
            dict(id="a", release=0, deadline=2, work=1),
            dict(id="b", release=0, deadline=2, work=1),
        ],
    )
    segments = [
        dict(job="a", start=0, finish=[1, 2]),
        dict(job="b", start=[1, 2], finish=[3, 2]),
        dict(job="a", start=[3, 2], finish=2),
    ]
    assert check_solution(s, dict(segments=segments))
    segments[1]["start"] = [1, 3]
    assert not check_solution(s, dict(segments=segments))


def test_build_path_enumeration_independent():
    rng = random.Random(44)
    for _ in range(20):
        jobs = [
            dict(
                id=str(i),
                release=rng.randrange(7),
                duration=rng.randrange(1, 5),
                parents=[
                    dict(job=str(j), lag=rng.randrange(4)) for j in range(i) if rng.random() < 0.5
                ],
            )
            for i in range(6)
        ]
        all_paths = []
        for j in jobs:
            all_paths.append(([j["id"]], j["release"] + j["duration"]))
            for path, length in list(all_paths):
                for edge in j["parents"]:
                    if path[-1] == edge["job"]:
                        all_paths.append((path + [j["id"]], length + edge["lag"] + j["duration"]))
        true_end = max(length for _, length in all_paths)
        rng.shuffle(jobs)
        s = dict(kind="lagged_build", jobs=jobs, budget_seconds=true_end)
        schedule, chain = _build_schedule(s)
        assert max(v["finish"] for v in schedule.values()) == true_end
        assert check_solution(s, dict(schedule=schedule))
        s.update(status="budget_exceeded", budget_seconds=true_end - 1)
        assert not check_solution(s, dict(schedule=schedule))
        assert check_certificate(s, dict(status=s["status"], certificate=dict(jobs=chain)))


def test_quorum_earliest_matches_discrete_event_simulation():
    rng = random.Random(61)
    for _ in range(30):
        gates = []
        for i in range(9):
            parents = [str(j) for j in range(i) if rng.random() < 0.5]
            gates.append(
                dict(
                    id=str(i),
                    parents=parents,
                    release=rng.randrange(5),
                    duration=rng.randrange(1, 5),
                    threshold=rng.randrange(len(parents) + 1),
                )
            )
        started, finished = {}, {}
        for t in range(100):
            for j in gates:
                if j["id"] in started and started[j["id"]] + j["duration"] == t:
                    finished[j["id"]] = t
            for j in gates:
                if (
                    j["id"] not in started
                    and j["release"] <= t
                    and sum(p in finished for p in j["parents"]) >= j["threshold"]
                ):
                    started[j["id"]] = t
        rng.shuffle(gates)
        s = dict(kind="quorum_release", gates=gates, target="8", budget_seconds=finished["8"])
        assert _quorum_times(s) == finished
        assert check_solution(
            s, dict(schedule={k: dict(start=started[k], finish=v) for k, v in finished.items()})
        )
        s.update(status="budget_exceeded", budget_seconds=finished["8"] - 1)
        assert check_certificate(
            s, dict(status=s["status"], certificate=dict(finish_lower_bounds=finished))
        )
        fabricated = deepcopy(finished)
        fabricated["8"] += 1
        assert not check_certificate(
            s, dict(status=s["status"], certificate=dict(finish_lower_bounds=fabricated))
        )


def test_quorum_accepts_nonexact_lower_bounds_and_partial_schedule():
    gates = [
        dict(id="a", parents=[], threshold=0, release=0, duration=10),
        dict(id="unused", parents=[], threshold=0, release=100, duration=10),
        dict(id="target", parents=["a", "unused"], threshold=1, release=0, duration=2),
    ]
    s = dict(kind="quorum_release", gates=gates, target="target", budget_seconds=12)
    assert check_solution(
        s, dict(schedule={"a": dict(start=0, finish=10), "target": dict(start=10, finish=12)})
    )
    s.update(status="budget_exceeded", budget_seconds=10)
    assert check_certificate(
        s,
        dict(
            status=s["status"],
            certificate=dict(finish_lower_bounds={"a": 9, "unused": 100, "target": 11}),
        ),
    )
