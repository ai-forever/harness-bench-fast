"""Independent feasibility oracles for matching with conditional constraints."""

from copy import deepcopy
from fractions import Fraction
from itertools import permutations, product

import pytest

from harness_bench.feasibility_tasks.coupled_assignment import (
    check_certificate,
    check_solution,
    upgrade_pairs,
)


def edge(key, source, target, bound):
    return dict(id=key, **{"from": source, "to": target}, max_delta=bound)


def report(s, certificate):
    return {"status": s["status"], "certificate": certificate}


def selected_edges(s, assignment):
    return s["base_constraints"] + [
        e
        for activation in s["activations"]
        if assignment.get(activation["job"]) == activation["worker"]
        for e in activation["constraints"]
    ]


def feasible_clocks(s, assignment):
    """Floyd-Warshall, independent of cycle-witness acceptance."""
    nodes = s["nodes"]
    dist = {(a, b): (0 if a == b else float("inf")) for a in nodes for b in nodes}
    for e in selected_edges(s, assignment):
        a, b = e["from"], e["to"]
        dist[a, b] = min(dist[a, b], e["max_delta"])
    for k in nodes:
        for a in nodes:
            for b in nodes:
                dist[a, b] = min(dist[a, b], dist[a, k] + dist[k, b])
    return all(dist[a, a] >= 0 for a in nodes)


def tiny_case(first=-2, second=-3):
    return dict(
        kind="assignment_clock",
        status="impossible",
        nodes=["x", "y"],
        eligible={"a": ["u", "v"], "b": ["u", "v"]},
        base_constraints=[edge("base", "x", "y", 1)],
        activations=[
            dict(job="a", worker="u", constraints=[edge("au", "y", "x", first)]),
            dict(job="a", worker="v", constraints=[edge("av", "y", "x", second)]),
            dict(job="b", worker="u", constraints=[edge("bu", "x", "y", 5)]),
            dict(job="b", worker="v", constraints=[edge("bv", "x", "y", 6)]),
        ],
    )


def tiny_certificate():
    return dict(
        job="a",
        branches=[dict(worker="u", cycle=["base", "au"]), dict(worker="v", cycle=["base", "av"])],
    )


def test_small_cases_exhaustive_certificate_soundness_and_solution_completeness():
    for first, second in product(range(-4, 4), repeat=2):
        s = tiny_case(first, second)
        valid = check_certificate(s, report(s, tiny_certificate()))
        feasible_assignments = []
        for workers in permutations(["u", "v"]):
            assignment = dict(zip(["a", "b"], workers, strict=True))
            feasible = feasible_clocks(s, assignment)
            feasible_assignments.append(feasible)
            found = False
            for x, y in product(range(-5, 6), repeat=2):
                solution = dict(assignment=assignment, offsets=dict(x=x, y=y))
                if check_solution(s, solution):
                    found = True
                    assert feasible
            assert found == feasible
        assert valid == (not any(feasible_assignments))


def test_certificate_cannot_borrow_other_branch_or_skip_or_duplicate_workers():
    s = tiny_case()
    c = tiny_certificate()
    assert check_certificate(s, report(s, c))
    # The v edge exists in the spec but is not active in the u branch.
    wrong = deepcopy(c)
    wrong["branches"][0]["cycle"] = ["base", "av"]
    assert not check_certificate(s, report(s, wrong))
    assert not check_certificate(s, report(s, dict(c, branches=c["branches"][:1])))
    assert not check_certificate(s, report(s, dict(c, branches=[c["branches"][0]] * 2)))
    wrong = deepcopy(c)
    wrong["branches"][0]["cycle"] = ["base", "au", "base", "au"]
    assert not check_certificate(s, report(s, wrong))
    wrong = deepcopy(c)
    wrong["branches"][0]["cycle"] = ["au"]
    assert not check_certificate(s, report(s, wrong))
    assert not check_certificate(s, {"status": "solved", "certificate": c})


def test_repeated_activation_records_all_apply_and_foreign_jobs_cannot_help_proof():
    s = tiny_case(0, 0)
    c = tiny_certificate()
    assert not check_certificate(s, report(s, c))
    s["activations"].append(dict(job="a", worker="u", constraints=[edge("extra", "x", "x", -1)]))
    c["branches"][0]["cycle"] = ["extra"]
    assert not check_certificate(s, report(s, c))
    s["activations"].append(dict(job="a", worker="v", constraints=[edge("extra-v", "y", "y", -1)]))
    c["branches"][1]["cycle"] = ["extra-v"]
    assert check_certificate(s, report(s, c))
    assert not check_solution(s, dict(assignment=dict(a="u", b="v"), offsets=dict(x=0, y=0)))
    # An edge activated by another job is not justified by this proof branch.
    c["branches"][0]["cycle"] = ["bu"]
    assert not check_certificate(s, report(s, c))


def test_rational_offsets_translation_and_matching_restrictions():
    s = tiny_case(0, -3)
    good = dict(assignment=dict(a="u", b="v"), offsets=dict(x=[1, 3], y=[5, 6]))
    assert check_solution(s, good)
    shifted = dict(assignment=good["assignment"], offsets=dict(x=[16, 3], y=[35, 6]))
    assert check_solution(s, shifted)
    for bad in (
        dict(assignment=dict(a="u", b="u"), offsets=good["offsets"]),
        dict(assignment=dict(a="u"), offsets=good["offsets"]),
        dict(assignment=dict(a="u", b="missing"), offsets=good["offsets"]),
        dict(assignment=good["assignment"], offsets=dict(x=0)),
        dict(assignment=good["assignment"], offsets=dict(x=0, y=0, z=0)),
        dict(assignment=good["assignment"], offsets=dict(x=True, y=0)),
        dict(assignment=good["assignment"], offsets=dict(x=[0, 0], y=0)),
    ):
        assert not check_solution(s, bad)


def matching_with_fixed_worker(eligible, fixed_job, fixed_worker):
    owner = {fixed_worker: fixed_job}

    def augment(job, seen):
        for worker in eligible[job]:
            if worker in seen or worker == fixed_worker:
                continue
            seen.add(worker)
            if worker not in owner or augment(owner[worker], seen):
                owner[worker] = job
                return True
        return False

    for job in eligible:
        if job != fixed_job and not augment(job, set()):
            return None
    return {job: worker for worker, job in owner.items()}


def test_current_fixtures_have_genuine_alternatives_and_one_changed_edge():
    from harness_bench.feasibility_tasks.contract_pairs import CONTRACT_PAIRS

    original = deepcopy(CONTRACT_PAIRS)
    upgraded = upgrade_pairs(deepcopy(CONTRACT_PAIRS))
    changed = [p for old, p in zip(original, upgraded, strict=True) if old != p]
    assert len(changed) == 1
    pair = changed[0]
    assert pair["slug"] == "deployment_matching"
    n, p = pair["negative"], pair["positive"]
    assert check_certificate(n["spec"], report(n["spec"], n["gold"]))
    assert check_solution(p["spec"], p["gold"])
    assert not check_solution(n["spec"], p["gold"])
    positive_report = dict(p["spec"], status=n["spec"]["status"])
    assert not check_certificate(positive_report, report(positive_report, n["gold"]))
    assert feasible_clocks(n["spec"], {})
    assert len(n["spec"]["eligible"]) == 40
    assert len(n["spec"]["nodes"]) == 50
    fixed_job = n["gold"]["job"]
    for branch in n["gold"]["branches"]:
        worker = branch["worker"]
        assignment = matching_with_fixed_worker(n["spec"]["eligible"], fixed_job, worker)
        assert assignment is not None and len(assignment) == 40
        assert not feasible_clocks(n["spec"], {fixed_job: worker})
        assert len(branch["cycle"]) >= 19
    differences = 0
    for left, right in zip(n["spec"]["activations"], p["spec"]["activations"], strict=True):
        if left != right:
            differences += 1
            changed_left, changed_right = deepcopy(left), deepcopy(right)
            del changed_left["constraints"][0]["max_delta"]
            del changed_right["constraints"][0]["max_delta"]
            assert changed_left == changed_right
    assert differences == 1
    # Gold is not a template match: a rational translation remains a solution.
    translated = deepcopy(p["gold"])
    for key, value in translated["offsets"].items():
        shifted = Fraction(value) + Fraction(13, 7)
        translated["offsets"][key] = [shifted.numerator, shifted.denominator]
    assert check_solution(p["spec"], translated)
    assert original == CONTRACT_PAIRS


@pytest.mark.parametrize("value", [None, [], {}, 1, "bad", {"branches": None}])
def test_malformed_inputs_fail_closed(value):
    s = tiny_case()
    assert not check_solution(s, value)
    assert not check_certificate(s, value)
    assert not check_certificate(s, report(s, value))
