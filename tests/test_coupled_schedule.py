"""Scheduling soundness checked against exhaustive tiny start/worker assignments."""

from copy import deepcopy
from itertools import product
from random import Random

from harness_bench.feasibility_tasks.coupled_schedule import (
    _fixture,
    check_certificate,
    check_solution,
    mandatory_work,
    precedence_windows,
)


def _independent_valid(spec, starts, workers):
    jobs = spec["jobs"]
    slots = {
        j["id"]: (starts[i], starts[i] + j["duration"], workers[i]) for i, j in enumerate(jobs)
    }
    for i, job in enumerate(jobs):
        a, b, lane = slots[job["id"]]
        if a < job["release"] or b > spec["budget_seconds"]:
            return False
        if any(slots[p][1] > a for p in job["parents"]):
            return False
        for other in jobs[i + 1 :]:
            x, y, other_lane = slots[other["id"]]
            if lane == other_lane and max(a, x) < min(b, y):
                return False
    return True


def test_planted_pair_and_alternative_worker_labels():
    negative, positive, certificate, solution = _fixture()
    assert len(positive["jobs"]) == 26
    assert check_solution(positive, solution)
    assert not check_solution(negative, solution)
    assert check_certificate(negative, dict(status=negative["status"], certificate=certificate))
    other_status = {**positive, "status": "budget_exceeded"}
    assert not check_certificate(
        other_status, dict(status="budget_exceeded", certificate=certificate)
    )
    alternate = deepcopy(solution)
    for slot in alternate["schedule"].values():
        slot["worker"] = (slot["worker"] + 1) % positive["workers"]
    assert check_solution(positive, alternate)
    # Global workload alone is deliberately too weak: precedence is essential.
    assert (
        sum(j["duration"] for j in negative["jobs"])
        <= negative["workers"] * negative["budget_seconds"]
    )
    earliest, latest = precedence_windows(negative)
    assert all(earliest[j["id"]] + j["duration"] <= latest[j["id"]] for j in negative["jobs"])


def test_tiny_exhaustive_soundness_and_solution_checker():
    rng = Random(1971)
    checked_schedules = 0
    for _ in range(24):
        horizon = 4
        jobs = [
            dict(
                id=str(i),
                duration=rng.randrange(1, 3),
                release=rng.randrange(2),
                parents=[str(p) for p in range(i) if rng.random() < 0.3],
            )
            for i in range(3)
        ]
        rng.shuffle(jobs)
        spec = dict(
            kind="coupled_schedule",
            status="budget_exceeded",
            jobs=jobs,
            workers=2,
            budget_seconds=horizon,
        )
        feasible = False
        earliest, latest = precedence_windows(spec)
        for starts in product(range(horizon), repeat=3):
            for workers in product(range(2), repeat=3):
                valid = _independent_valid(spec, starts, workers)
                answer = dict(
                    schedule={
                        j["id"]: dict(start=starts[i], worker=workers[i])
                        for i, j in enumerate(jobs)
                    }
                )
                assert check_solution(spec, answer) == valid
                checked_schedules += 1
                if not valid:
                    continue
                feasible = True
                for i, j in enumerate(jobs):
                    assert starts[i] >= earliest[j["id"]]
                    assert starts[i] + j["duration"] <= latest[j["id"]]
                for a in range(horizon):
                    for b in range(a + 1, horizon + 1):
                        actual = sum(
                            max(0, min(starts[i] + j["duration"], b) - max(starts[i], a))
                            for i, j in enumerate(jobs)
                        )
                        assert mandatory_work(spec, a, b) <= actual
        for a in range(horizon):
            for b in range(a + 1, horizon + 1):
                accepted = check_certificate(
                    spec, dict(status="budget_exceeded", certificate=dict(interval=[a, b]))
                )
                assert not (accepted and feasible)
    assert checked_schedules == 12288


def test_certificate_uses_capacity_and_precedence_together():
    negative, _, certificate, _ = _fixture()
    start, finish = certificate["interval"]
    assert mandatory_work(negative, start, finish) > negative["workers"] * (finish - start)
    relaxed = deepcopy(negative)
    for j in relaxed["jobs"]:
        j["parents"] = []
    assert not check_certificate(relaxed, dict(status="budget_exceeded", certificate=certificate))
    relaxed = deepcopy(negative)
    relaxed["workers"] += 1
    assert not check_certificate(relaxed, dict(status="budget_exceeded", certificate=certificate))


def test_invalid_inputs_and_overlap_fail_closed():
    n, p, _, solution = _fixture()
    for value in (None, [], {}, {"schedule": {}}):
        assert not check_solution(p, value)
        assert not check_certificate(n, value)
    for interval in ([True, 1], [0, 1.5], [-1, 2], [2, 1], [0, 10000], [0, 1, 2]):
        assert not check_certificate(
            n, dict(status="budget_exceeded", certificate=dict(interval=interval))
        )
    for replacement in (True, 1.5, -1, 3):
        bad = deepcopy(solution)
        first = next(iter(bad["schedule"]))
        bad["schedule"][first]["worker"] = replacement
        assert not check_solution(p, bad)
    bad = deepcopy(solution)
    for slot in bad["schedule"].values():
        slot["worker"] = 0
    assert not check_solution(p, bad)
