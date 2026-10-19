"""Independent construction checks for both sides of every formal contract pair."""

from copy import deepcopy
from fractions import Fraction

import pytest

from harness_bench.feasibility_tasks.checks import check
from harness_bench.feasibility_tasks.contract_pairs import CONTRACT_PAIRS, check_solution

PAIRS = {p["slug"]: p for p in CONTRACT_PAIRS}


@pytest.mark.parametrize("pair", CONTRACT_PAIRS, ids=lambda p: p["slug"])
def test_both_halves_and_cross_half_rejection(pair):
    negative, positive = pair["negative"], pair["positive"]
    report = dict(status=negative["spec"]["status"], certificate=negative["gold"])
    assert check(negative["spec"], report)
    assert check_solution(positive["spec"], positive["gold"])
    assert not check_solution(negative["spec"], positive["gold"])
    # Do not rely on status mismatch to reject the old impossibility witness.
    forged_positive = deepcopy(positive["spec"])
    forged_positive["status"] = negative["spec"]["status"]
    assert not check(forged_positive, report)
    assert negative["request"] == positive["request"]
    assert negative["instructions"] == positive["instructions"]
    assert negative["solution_schema"] == positive["solution_schema"]


def test_fourteen_distinct_contract_pairs():
    assert len(CONTRACT_PAIRS) == len(PAIRS) == 14
    assert len({p["positive"]["spec"]["kind"] for p in CONTRACT_PAIRS}) == 14


@pytest.mark.parametrize("pair", CONTRACT_PAIRS, ids=lambda p: p["slug"])
@pytest.mark.parametrize("bad", [None, [], {}, 42, {"status": "solved"}, {"solution": "trust me"}])
def test_malformed_and_claim_only_payloads(pair, bad):
    assert check_solution(pair["positive"]["spec"], bad) is False


def alternate(slug, good):
    """Construct different valid results without the production gold builders."""
    c = deepcopy(good)
    if slug == "clock_contract":
        c["offsets"] = {k: [2 * v + 1, 2] for k, v in c["offsets"].items()}
    elif slug == "deployment_matching":
        a = c["assignment"]
        a["job-04"], a["job-09"] = a["job-09"], a["job-04"]
    elif slug == "replication_cut":
        # Add a feasible circulation in the edge ring without changing throughput.
        for i in range(12):
            c["edge_flows"][5 * i + 3] = [1, 2]
    elif slug == "parity_repair":
        c["flags"] = {k: 1 - v for k, v in c["flags"].items()}
    elif slug == "aggregate_identifiability":
        c["observation_weights"] = {k: [v * 3, 3] for k, v in c["observation_weights"].items()}
    elif slug == "stateless_contract":
        c["table"].reverse()
    elif slug == "universal_compressor":
        c["symbol_map"].reverse()
    elif slug == "ledger_reachability":
        # Three +84 and two -126 operations form a zero-sum cycle.
        c["operation_counts"][0] += 3
        c["operation_counts"][1] += 2
    elif slug == "maintenance_windows":
        c["windows"] = {k: (v + 1) % 5 for k, v in c["windows"].items()}
    elif slug == "opaque_guid":
        c["start"] = 1  # Excluding one of 2**28 candidates still exceeds 99%.
    elif slug == "diagnostic_queries":
        c["midpoint"], c["query_half"] = "ceil", "upper"
    elif slug == "archive_transfer":
        # Reverse the transfer order, preserving each duration exactly.
        cursor = Fraction(0)
        c["transfers"].reverse()
        for slot in c["transfers"]:
            duration = Fraction(*slot["finish"]) - Fraction(*slot["start"])
            slot["start"] = [cursor.numerator, cursor.denominator]
            cursor += duration
            slot["finish"] = [cursor.numerator, cursor.denominator]
    elif slug == "build_critical_path":
        for times in c["schedule"].values():
            times["start"] += 1
            times["finish"] += 1
    elif slug == "sealed_partition":
        c["schedule"]["partition-00"] = dict(start=50, finish=53)
    return c


@pytest.mark.parametrize("pair", CONTRACT_PAIRS, ids=lambda p: p["slug"])
def test_alternate_constructions(pair):
    positive = pair["positive"]
    candidate = alternate(pair["slug"], positive["gold"])
    assert candidate != positive["gold"]
    assert check_solution(positive["spec"], candidate)
    assert not check_solution(pair["negative"]["spec"], candidate)


def broken(slug, good):
    c = deepcopy(good)
    if slug == "clock_contract":
        c["offsets"]["s1"] = 10**6
    elif slug == "deployment_matching":
        c["assignment"]["job-00"] = c["assignment"]["job-01"]
    elif slug == "replication_cut":
        c["edge_flows"][0] -= 1  # Violates conservation, not total arrival.
    elif slug == "parity_repair":
        c["flags"]["b0"] ^= 1
    elif slug == "aggregate_identifiability":
        c["observation_weights"]["aggregate-24"] = 0
    elif slug == "stateless_contract":
        c["table"][0]["output"] ^= 1
    elif slug == "universal_compressor":
        c["symbol_map"][0] = c["symbol_map"][1]
    elif slug == "ledger_reachability":
        c["operation_counts"][0] += 1
    elif slug == "maintenance_windows":
        c["windows"]["service-01"] = c["windows"]["service-00"]
    elif slug == "opaque_guid":
        c["stop"] = 1
    elif slug == "diagnostic_queries":
        c["on_no"] = "keep_queried_half"
    elif slug == "archive_transfer":
        c["transfers"][1]["start"] = 0  # Creates overlap while per-job durations remain valid.
    elif slug == "build_critical_path":
        c["schedule"]["build-01"] = dict(start=0, finish=10)
    elif slug == "sealed_partition":
        c["schedule"]["partition-37"] = dict(start=117, finish=119)
    return c


@pytest.mark.parametrize("pair", CONTRACT_PAIRS, ids=lambda p: p["slug"])
def test_forged_plans_and_inconsistent_constructions(pair):
    positive = pair["positive"]
    assert not check_solution(positive["spec"], broken(pair["slug"], positive["gold"]))


def test_known_feasible_world_does_not_prove_identifiability():
    p = PAIRS["aggregate_identifiability"]
    claim = dict(
        world=[3, 7, 11, 5, 8, 4, 9, 6],
        target=3,
        observation_weights={r["id"]: 0 for r in p["negative"]["spec"]["observations"]},
    )
    assert not check_solution(p["negative"]["spec"], claim)


@pytest.mark.parametrize("weight", [1, [3, 3]])
def test_sparse_observation_weights_from_actual_model_submission(weight):
    solution = dict(
        world=[3, 7, 11, 5, 8, 4, 9, 6],
        target=3,
        observation_weights={"aggregate-24": weight},
    )
    pair = PAIRS["aggregate_identifiability"]
    assert check_solution(pair["positive"]["spec"], solution)
    assert not check_solution(pair["negative"]["spec"], solution)


@pytest.mark.parametrize(
    "weights",
    [
        None,
        [],
        {},
        {"unknown": 0, "aggregate-24": 1},
        {"aggregate-24": 0},
        {"aggregate-24": 2},
        {"aggregate-24": True},
        {"aggregate-24": 1.0},
        {"aggregate-24": [1, 0]},
    ],
)
def test_sparse_observation_weights_reject_invalid_proofs(weights):
    solution = dict(world=[3, 7, 11, 5, 8, 4, 9, 6], target=3, observation_weights=weights)
    assert not check_solution(PAIRS["aggregate_identifiability"]["positive"]["spec"], solution)


@pytest.mark.parametrize("field,value", [("target", 4), ("world", [4, 6, 11, 5, 8, 4, 9, 6])])
def test_sparse_observation_weights_still_require_consistent_world_and_target(field, value):
    solution = dict(
        world=[3, 7, 11, 5, 8, 4, 9, 6], target=3, observation_weights={"aggregate-24": 1}
    )
    solution[field] = value
    assert not check_solution(PAIRS["aggregate_identifiability"]["positive"]["spec"], solution)


@pytest.mark.parametrize(
    "start,stop,expected",
    [(0, 99, True), (0, 98, False), (1, 100, True), (0, 101, False), (-1, 98, False)],
)
def test_probability_exact_threshold_and_bounds(start, stop, expected):
    spec = deepcopy(PAIRS["opaque_guid"]["positive"]["spec"])
    spec.update(
        random_bits=7,
        revealed_bit_positions=[],
        budget_seconds=1,
        max_checks_per_second=99,
        required_success_probability=[99, 128],
    )
    c = deepcopy(PAIRS["opaque_guid"]["positive"]["gold"])
    c.update(start=start, stop=stop)
    assert check_solution(spec, c) is expected


@pytest.mark.parametrize("count,expected", [(1, True), (2**40, True), (2**40 + 1, False)])
def test_decision_tree_exact_worst_case_depth(count, expected):
    spec = deepcopy(PAIRS["diagnostic_queries"]["positive"]["spec"])
    spec["outcomes"] = count
    c = deepcopy(PAIRS["diagnostic_queries"]["positive"]["gold"])
    c["high"] = count
    assert check_solution(spec, c) is expected


def test_transfer_rejects_duplicates_missing_jobs_and_bad_lane():
    p = PAIRS["archive_transfer"]["positive"]
    c = deepcopy(p["gold"])
    c["transfers"][0]["job"] = c["transfers"][1]["job"]
    assert not check_solution(p["spec"], c)
    c = deepcopy(p["gold"])
    c["transfers"][0]["lane"] = 1
    assert not check_solution(p["spec"], c)
    c = deepcopy(p["gold"])
    c["transfers"].pop()
    assert not check_solution(p["spec"], c)


def test_schedule_exact_deadline_is_valid_but_late_finish_is_not():
    p = PAIRS["sealed_partition"]["positive"]
    c = deepcopy(p["gold"])
    assert c["schedule"]["partition-37"]["finish"] == 120
    assert check_solution(p["spec"], c)
    c["schedule"]["partition-37"]["finish"] = [120001, 1000]
    assert not check_solution(p["spec"], c)


def test_boolean_is_not_numeric_solution():
    p = PAIRS["parity_repair"]["positive"]
    c = deepcopy(p["gold"])
    c["flags"]["b0"] = False
    assert not check_solution(p["spec"], c)


def test_negative_status_alone_does_not_control_solution_checker():
    p = PAIRS["clock_contract"]["positive"]
    s = deepcopy(p["spec"])
    s["status"] = "impossible"
    assert check_solution(s, p["gold"])
