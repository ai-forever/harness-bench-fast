"""Independent small-instance and mutation checks for dense wave-19 controls."""

import tracemalloc
from copy import deepcopy
from functools import lru_cache
from itertools import product

import pytest

from harness_bench.feasibility_tasks.hard_algebra import (
    KINDS,
    check_certificate,
    check_solution,
    upgrade_pairs,
)


def certificate(s, c):
    return check_certificate(s, {"status": s["status"], "certificate": c})


def test_aggregate_accepts_sparse_alternative_proof_and_distinct_worlds():
    s = dict(
        kind="hard_aggregate",
        status="impossible",
        variables=3,
        observations=[
            dict(id="a", coefficients=[1, 2, 3], total=14),
            dict(id="b", coefficients=[2, 1, 3], total=13),
        ],
        target_coefficients=[1, 0, 0],
    )
    assert certificate(s, {"worlds": [[1, 2, 3], [2, 3, 2]]})
    assert not certificate(s, {"worlds": [[1, 2, 3], [1, 2, 3]]})
    assert not certificate(s, {"worlds": [[1, 2, 3], [-2, -1, 6]]})
    s["target_coefficients"] = [3, 3, 6]
    assert not certificate(s, {"worlds": [[1, 2, 3], [2, 3, 2]]})
    assert check_solution(s, dict(world=[2, 3, 2], target=27, observation_weights={"a": 1, "b": 1}))
    s["target_coefficients"] = [1, 2, 3]
    assert check_solution(s, dict(world=[1, 2, 3], target=14, observation_weights={"a": 1}))
    assert not check_solution(
        s, dict(world=[1, 2, 3], target=14, observation_weights={"a": 1, "unknown": 0})
    )


def test_ledger_accepts_any_modular_functional_and_constructive_counts():
    s = dict(
        kind="lattice_ledger",
        status="impossible",
        initial=[3, -4],
        target=[14, 8],
        operations=[[2, 4], [4, 2]],
    )
    assert certificate(s, dict(modulus=2, weights=[1, 0]))
    assert certificate(s, dict(modulus=2, weights=[3, 2]))
    assert not certificate(s, dict(modulus=2, weights=[0, 1]))
    s["target"] = [13, 4]
    assert check_solution(s, dict(operation_counts=[1, 2]))
    for a, b in product(range(7), repeat=2):
        constructed = [3 + 2 * a + 4 * b, -4 + 4 * a + 2 * b]
        ss = dict(s, target=constructed)
        assert check_solution(ss, dict(operation_counts=[a, b]))
        for modulus in range(2, 6):
            for weights in product(range(modulus), repeat=2):
                assert not certificate(ss, dict(modulus=modulus, weights=list(weights)))


def test_dfa_counts_agree_with_enumeration_and_codec_roundtrip():
    # Accepted words have an even number of ones, no reference table import.
    transitions = [[0, 1], [1, 0]]
    s = dict(
        kind="dfa_codec",
        status="impossible",
        transitions=transitions,
        start_state=0,
        accepting=[0],
        input_length=5,
        output_alphabet=2,
        max_output_length=3,
    )
    table = []
    for length in range(6):
        counts = []
        for start in range(2):
            count = 0
            for word in product(range(2), repeat=length):
                state = start
                for symbol in word:
                    state = transitions[state][symbol]
                count += state == 0
            counts.append(count)
        table.append(counts)
    assert certificate(s, dict(suffix_counts=table, inputs=16, outputs=15))
    assert not check_solution(s, dict(operation="dfa_lex_rank_shortlex", suffix_counts=table))
    s["max_output_length"] = 4
    assert check_solution(s, dict(operation="dfa_lex_rank_shortlex", suffix_counts=table))
    accepted = [w for w in product(range(2), repeat=5) if sum(w) % 2 == 0]
    outputs = [w for length in range(5) for w in product(range(2), repeat=length)]
    encoding = {word: outputs[rank] for rank, word in enumerate(accepted)}
    decoding = {v: k for k, v in encoding.items()}
    assert len(decoding) == 16
    assert all(decoding[encoding[w]] == w for w in accepted)
    bad = deepcopy(table)
    bad[3][1] += 1
    assert not check_solution(s, dict(operation="dfa_lex_rank_shortlex", suffix_counts=bad))


def test_guid_rank_probability_matches_exhaustive_small_space():
    s = dict(
        kind="affine_guid",
        status="budget_insufficient",
        random_bits=4,
        observations=[dict(mask=3, rhs=1), dict(mask=12, rhs=0), dict(mask=15, rhs=1)],
        budget_seconds=2,
        epochs=[
            dict(start=0, end=1, checks_per_second=[3, 2]),
            dict(start=1, end=2, checks_per_second=[3, 2]),
        ],
        required_success_probability=[3, 4],
    )
    candidates = [
        v
        for v in range(16)
        if all((v & r["mask"]).bit_count() % 2 == r["rhs"] for r in s["observations"])
    ]
    assert candidates == [1, 2, 13, 14]
    assert certificate(s, dict(kernel_vectors=[3, 12], max_checks=2, success_upper_bound=[1, 2]))
    assert not certificate(s, dict(kernel_vectors=[3, 3], max_checks=2, success_upper_bound=[1, 2]))
    assert not certificate(s, dict(kernel_vectors=[3, 4], max_checks=2, success_upper_bound=[1, 2]))
    # The epoch slots cannot be pooled: floor(1.5)+floor(1.5)=2, not 3.
    assert not certificate(
        s, dict(kernel_vectors=[3, 12], max_checks=3, success_upper_bound=[3, 4])
    )
    solution = dict(operation="enumerate_affine_basis", origin=1, kernel_vectors=[15, 12], checks=3)
    assert not check_solution(s, solution)
    s["epochs"][1]["checks_per_second"] = 2
    assert check_solution(s, solution)
    assert len({1 ^ (15 if k & 1 else 0) ^ (12 if k & 2 else 0) for k in range(3)}) == 3
    assert not check_solution(s, dict(solution, origin=0))
    assert not check_solution(s, dict(solution, kernel_vectors=[3]))


def brute_depth(n, masks):
    @lru_cache(None)
    def search(state):
        if state.bit_count() <= 1:
            return 0
        depths = []
        for mask in masks:
            a, b = state & mask, state & ~mask
            if a and b:
                depths.append(1 + max(search(a), search(b)))
        return min(depths, default=1000)

    return search((1 << n) - 1)


def test_diagnosis_adversary_bounds_sound_against_exhaustive_query_sets():
    n = 4
    for chosen in product([False, True], repeat=7):
        masks = [i + 1 for i, yes in enumerate(chosen) if yes]
        optimum = brute_depth(n, masks)
        for available in range(1, 4):
            s = dict(
                kind="restricted_diagnosis",
                status="budget_insufficient",
                outcomes=n,
                queries=[dict(id=str(i), mask=m) for i, m in enumerate(masks)],
                budget_seconds=available,
                seconds_per_query=1,
            )
            for subset in range(1, 1 << n):
                for cap in range(1, n):
                    if certificate(s, dict(adversary_mask=subset, max_eliminations=cap)):
                        assert optimum > available


def test_diagnosis_tree_and_recursive_certificate():
    s = dict(
        kind="restricted_diagnosis",
        status="budget_insufficient",
        outcomes=4,
        queries=[dict(id="a", mask=1), dict(id="b", mask=2), dict(id="c", mask=4)],
        budget_seconds=2,
        seconds_per_query=1,
    )
    # Every first query leaves a three-element no branch requiring two tests.
    proof = dict(
        bounds=[
            dict(mask=15, depth_lower_bound=3),
            dict(mask=14, depth_lower_bound=2),
            dict(mask=13, depth_lower_bound=2),
            dict(mask=11, depth_lower_bound=2),
            *[dict(mask=m, depth_lower_bound=1) for m in (12, 10, 9)],
        ]
    )
    assert certificate(s, proof)
    tree = dict(
        query="a",
        yes=dict(outcome=0),
        no=dict(
            query="b",
            yes=dict(outcome=1),
            no=dict(query="c", yes=dict(outcome=2), no=dict(outcome=3)),
        ),
    )
    assert not check_solution(s, dict(tree=tree))
    s["budget_seconds"] = 3
    assert check_solution(s, dict(tree=tree))
    assert not certificate(s, proof)
    bad = deepcopy(proof)
    bad["bounds"].append(dict(mask=15, depth_lower_bound=10))
    assert not certificate(s, bad)


@pytest.mark.parametrize("outcome", [-1, 1, True, 0.0, "0"])
def test_diagnosis_leaf_rejects_outside_domain_and_noninteger_values(outcome):
    spec = dict(
        kind="restricted_diagnosis",
        outcomes=1,
        queries=[],
        budget_seconds=0,
        seconds_per_query=1,
    )
    assert check_solution(spec, {"tree": {"outcome": 0}})
    assert not check_solution(spec, {"tree": {"outcome": outcome}})


def test_diagnosis_leaf_index_cannot_allocate_an_unbounded_bitmask():
    spec = dict(
        kind="restricted_diagnosis",
        outcomes=1,
        queries=[],
        budget_seconds=0,
        seconds_per_query=1,
    )
    # The vulnerable shift allocated about 2 MB for this bounded test value;
    # a similarly short JSON integer could otherwise demand hundreds of GB.
    # Keep the regression safe even if the range check is accidentally removed.
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    tracemalloc.reset_peak()
    baseline = tracemalloc.get_traced_memory()[0]
    try:
        assert not check_solution(spec, {"tree": {"outcome": 16_000_000}})
        peak = tracemalloc.get_traced_memory()[1] - baseline
        assert peak < 256_000, "invalid leaf index was shifted before domain validation"
    finally:
        if not already_tracing:
            tracemalloc.stop()


@pytest.mark.parametrize(
    "value", [None, [], {}, {"worlds": []}, {"modulus": True}, {"bounds": None}]
)
def test_malformed_inputs_fail_closed(value):
    for kind in KINDS:
        s = dict(kind=kind, status="impossible")
        assert not check_solution(s, value)
        assert not check_certificate(s, value)
        assert not certificate(s, value)


def test_upgraded_fixtures_are_independent_and_gold_is_valid():
    from harness_bench.feasibility_tasks.contract_pairs import CONTRACT_PAIRS

    before = deepcopy(CONTRACT_PAIRS)
    pairs = upgrade_pairs(deepcopy(CONTRACT_PAIRS))
    changed = 0
    for old, pair in zip(before, pairs, strict=True):
        if pair["negative"]["spec"]["kind"] not in KINDS:
            assert old == pair
            continue
        changed += 1
        n, p = pair["negative"], pair["positive"]
        assert certificate(n["spec"], n["gold"])
        assert check_solution(p["spec"], p["gold"])
        assert not check_solution(n["spec"], p["gold"])
        assert len(str(n["gold"])) < 20_000
    assert changed == 5
    assert before == CONTRACT_PAIRS
