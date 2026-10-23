"""Independent exhaustive checks of the second workflow revision."""

from __future__ import annotations

import copy
import itertools
import json
import math
import random
from fractions import Fraction
from functools import lru_cache

from harness_bench.general_hard import workflow_revision_v5 as v5
from harness_bench.general_hard.workflow_tasks import (
    _V4_TASKS,
    _V5_ARTIFACTS,
    _V5_CASES,
    _V5_INPUTS,
    _V5_SOLVERS,
    TASKS,
)


def span(rows):
    values = {0}
    for row in rows:
        values |= {value ^ row for value in list(values)}
    return values


def test_gf2_recovery_matches_all_linear_combinations_not_rank():
    rng = random.Random(50051)
    for _ in range(80):
        rows = [rng.randrange(1, 16) for _ in range(rng.randrange(8))]
        combinations = span(rows)
        recovered, rank = v5._recover_500(rows, 4)
        assert recovered == sum(1 << i for i in range(4) if 1 << i in combinations)
        assert 1 << rank == len(combinations)
    assert v5._recover_500([0b11], 2) == (0, 1)  # rank1 does not recover either blob


def test_joint_retention_encoding_matches_bruteforce_with_span_oracle():
    rng = random.Random(50052)
    for _ in range(18):
        labels = ["a", "b", "c"]
        packets = [
            {
                "id": f"p{i}",
                "zone": str(i % 3),
                "xor": rng.sample(labels, rng.randrange(1, 4)),
                "bytes": rng.randrange(1, 4),
                "upload": rng.randrange(1, 4),
            }
            for i in range(7)
        ]
        data = {
            "objects": {name: name for name in labels},
            "snapshots": [
                {"id": name, "parent": None, "ops": [{"op": "put", "path": "x", "object": name}]}
                for name in labels
            ],
            "packets": packets,
            "max_bytes": 10,
            "max_upload": 9,
            "policy": {
                "candidates": {"a": 3, "b": 6, "c": 5},
                "mandatory": [],
                "requires": {"c": ["a"]},
                "groups": [{"members": labels, "min": 1, "max": 3}],
            },
        }
        best = None
        for flags in itertools.product((False, True), repeat=7):
            selected = [p for p, take in zip(packets, flags, strict=True) if take]
            stored, upload = sum(p["bytes"] for p in selected), sum(p["upload"] for p in selected)
            if stored > 10 or upload > 9:
                continue
            robust = set(labels)
            for zone in "012":
                equations = [
                    sum(1 << labels.index(label) for label in p["xor"])
                    for p in selected
                    if p["zone"] != zone
                ]
                possible = span(equations)
                robust &= {label for i, label in enumerate(labels) if 1 << i in possible}
            for count in range(1, 4):
                for retained in itertools.combinations(labels, count):
                    if not set(retained) <= robust or "c" in retained and "a" not in retained:
                        continue
                    score = (
                        -sum(data["policy"]["candidates"][x] for x in retained),
                        upload,
                        stored,
                        len(retained),
                        list(retained),
                        len(selected),
                        [p["id"] for p in selected],
                    )
                    if best is None or score < best:
                        best = score
        actual = v5._solve_v5_500(data)
        if best is None:
            assert actual == {"feasible": False}
        else:
            assert (
                -actual["utility"],
                actual["upload_bytes"],
                actual["stored_bytes"],
                len(actual["retained"]),
                actual["retained"],
                len(actual["packets"]),
                actual["packets"],
            ) == best


def exhaustive_joint(data):
    if sum(data["row_totals"].values()) != sum(data["column_totals"].values()):
        return None
    options = []
    columns = sorted(data["column_totals"])
    for inv in sorted(data["invoices"], key=lambda row: row["id"]):
        lines = sorted(inv["lines"], key=lambda row: row["id"])
        assert inv["discount"] == [0, 1]
        exact = []
        for row in lines:
            base = Fraction(*row["unit"]) * row["qty"]
            if row["inclusive"]:
                base /= 1 + Fraction(*row["tax"])
            exact.extend([base * inv["scale"], base * inv["scale"] * Fraction(*row["tax"])])
        variants = []
        for native in itertools.product(*(sorted({math.floor(x), math.ceil(x)}) for x in exact)):
            if sum(native[::2]) != round(sum(exact[::2])) or sum(native[1::2]) != round(
                sum(exact[1::2])
            ):
                continue
            fx = [
                sum(
                    native[2 * i] + native[2 * i + 1]
                    for i, row in enumerate(lines)
                    if row["jurisdiction"] == col
                )
                * Fraction(*inv["fx"])
                * data["base_scale"]
                / inv["scale"]
                for col in columns
            ]
            for settlement in itertools.product(
                *(sorted({math.floor(x), math.ceil(x)}) for x in fx)
            ):
                if sum(settlement) != data["row_totals"][inv["id"]]:
                    continue
                errors = [abs(a - b) for a, b in zip(native, exact, strict=True)] + [
                    abs(a - b) for a, b in zip(settlement, fx, strict=True)
                ]
                variants.append((native + settlement, settlement, errors, inv["id"]))
        options.append(variants)
    best = None
    for chosen in itertools.product(*options):
        if any(
            sum(row[1][j] for row in chosen) != data["column_totals"][col]
            for j, col in enumerate(columns)
        ):
            continue
        allocated = {(row[3], col): row[1][j] for row in chosen for j, col in enumerate(columns)}
        if any(
            not q["min"] <= sum(allocated[tuple(cell)] for cell in q["cells"]) <= q["max"]
            for q in data["quotas"]
        ):
            continue
        errors = [error for row in chosen for error in row[2]]
        candidate = (
            max(errors, default=Fraction()),
            sum((error * error for error in errors), Fraction()),
            tuple(x for row in chosen for x in row[0]),
        )
        if best is None or candidate < best:
            best = candidate
    return best


def test_native_and_fx_joint_optimum_matches_full_enumeration():
    rng = random.Random(50351)
    for _ in range(28):
        data = copy.deepcopy(_V5_INPUTS[503][1])
        for inv in data["invoices"]:
            for row in inv["lines"]:
                row["unit"] = [rng.randrange(1, 6), 3]
                row["tax"] = [rng.randrange(2), 2]
            inv["fx"] = [1, 2]
            bases = [Fraction(*row["unit"]) for row in inv["lines"]]
            gross = round(sum(bases)) + round(
                sum(
                    (b * Fraction(*row["tax"]) for b, row in zip(bases, inv["lines"], strict=True)),
                    Fraction(),
                )
            )
            data["row_totals"][inv["id"]] = round(Fraction(gross, 2))
        total = sum(data["row_totals"].values())
        first = rng.randrange(total + 1)
        data["column_totals"] = {"j0": first, "j1": total - first}
        expected = exhaustive_joint(data)
        actual = v5._solve_v5_503(data)
        if expected is None:
            assert actual == {"feasible": False, "reason": "coupling"}
        else:
            vector = tuple(
                x
                for inv in actual["invoices"]
                for x in [
                    *(
                        value
                        for row in inv["lines"]
                        for value in (row["net_minor"], row["tax_minor"])
                    ),
                    *(row["base_minor"] for row in inv["allocations"]),
                ]
            )
            assert (
                Fraction(actual["maximum_error"]),
                Fraction(actual["squared_error"]),
                vector,
            ) == expected


def test_freezing_native_rounding_mutant_loses_feasible_solution():
    result = v5._solve_v5_503(_V5_INPUTS[503][0])
    assert [r["net_minor"] for r in result["invoices"][0]["lines"]] == [0, 1]
    frozen_largest_remainder = [1, 0]
    assert frozen_largest_remainder[0] != _V5_INPUTS[503][0]["column_totals"]["j0"]


def boolean_closure(facts, rules):
    values = {(row["predicate"], tuple(row["args"])) for row in facts}
    domain = sorted({term for _, args in values for term in args})
    while True:
        following = set(values)
        for rule in rules:
            variables = sorted(
                {
                    term
                    for atom in [rule["head"], *rule["body"]]
                    for term in atom["args"]
                    if term.startswith("?")
                }
            )
            for assigned in itertools.product(domain, repeat=len(variables)):
                mapping = dict(zip(variables, assigned, strict=True))

                def ground(atom, mapping=mapping):
                    return atom["predicate"], tuple(
                        mapping.get(term, term) for term in atom["args"]
                    )

                if all(ground(atom) in values for atom in rule["body"]):
                    following.add(ground(rule["head"]))
        if following == values:
            return values
        values = following


def test_recursive_minimal_provenance_matches_all_fact_subsets():
    facts = [
        {"id": str(i), "predicate": "edge", "args": list(pair)}
        for i, pair in enumerate(("ab", "bc", "ac", "cb"))
    ]
    rules = [
        {
            "id": "seed",
            "head": {"predicate": "reach", "args": ["?x", "?y"]},
            "body": [{"predicate": "edge", "args": ["?x", "?y"]}],
            "not": [],
        },
        {
            "id": "step",
            "head": {"predicate": "reach", "args": ["?x", "?z"]},
            "body": [
                {"predicate": "reach", "args": ["?x", "?y"]},
                {"predicate": "edge", "args": ["?y", "?z"]},
            ],
            "not": [],
        },
    ]
    oracle = {}
    for flags in itertools.product((False, True), repeat=4):
        selected = [row for row, flag in zip(facts, flags, strict=True) if flag]
        proof = frozenset(row["id"] for row in selected)
        for key in boolean_closure(selected, rules):
            oracle.setdefault(key, set()).add(proof)
    actual = v5._relations_v5_507(facts, rules)
    for relation in actual["relations"]:
        for row in relation["rows"]:
            candidates = oracle[relation["predicate"], tuple(row["values"])]
            expected = sorted(
                [
                    sorted(support)
                    for support in candidates
                    if not any(other < support for other in candidates)
                ]
            )
            assert row["proofs"] == expected
    # A tuple-only fixed point would miss alternative proofs added later.
    row = next(
        r
        for t in actual["relations"]
        if t["predicate"] == "reach"
        for r in t["rows"]
        if r["values"] == ["a", "c"]
    )
    assert row["proofs"] == [["0", "1"], ["2"]]


def test_stratified_negation_and_scoped_rule_capture():
    data = _V5_ARTIFACTS[507]
    answers = {job["id"]: v5._solve_v5_507({"files": data["files"], **job}) for job in data["jobs"]}
    assert answers["negative-cycle"] == {"error": "unstratified"}
    prod = answers["prod"]
    region = next(row for row in prod["relations"] if row["predicate"] == "region")
    assert [row["values"][0] for row in region["rows"]] == ["primary", "secondary"]
    assert region["rows"][0]["values"][1] == "eu:443"
    assert prod["strata"]["usable"] == 1
    assert next(row for row in prod["relations"] if row["predicate"] == "empty")["rows"] == []


def all_policy_trees(hypotheses, data, budget):
    worlds = [(i, mode) for i in range(len(hypotheses)) for mode in data["fault_modes"]]

    @lru_cache(None)
    def visit(indices, state, remaining):
        labels = {worlds[i][0] for i in indices}
        if len(labels) == 1:
            return [({i: 0 for i in indices}, 0, {"hypothesis": next(iter(labels))})]
        answers = []
        for probe in data["probes"]:
            if (
                not probe["available"]
                or state not in probe["cost"]
                or probe["cost"][state] > remaining
            ):
                continue
            groups = {}
            for i in indices:
                hypothesis, mode = worlds[i]
                truth = int(bool(set(hypotheses[hypothesis]) & set(probe["tests"])))
                observed = truth ^ int([state, probe["id"]] in mode["flips"])
                groups.setdefault(observed, []).append(i)
            outcomes = sorted(groups)
            choices = [
                visit(tuple(groups[value]), probe["next"][value], remaining - probe["cost"][state])
                for value in outcomes
            ]
            for children in itertools.product(*choices):
                costs = {
                    i: value + probe["cost"][state]
                    for child in children
                    for i, value in child[0].items()
                }
                tree = {
                    "probe": probe["id"],
                    "branches": [
                        {"outcome": value, "tree": child[2]}
                        for value, child in zip(outcomes, children, strict=True)
                    ],
                }
                answers.append((costs, 1 + sum(child[1] for child in children), tree))
        return answers

    return visit(tuple(range(len(worlds))), data["initial_state"], budget)


def toy_policy():
    return {
        "states": ["cold", "hot", "safe"],
        "initial_state": "cold",
        "observations": [],
        "fault_modes": [{"id": "clean", "flips": []}, {"id": "bad", "flips": [["hot", "p"]]}],
        "probes": [
            {
                "id": "warm",
                "tests": [],
                "mode": "any",
                "available": True,
                "cost": {"cold": 1},
                "next": ["hot", "hot"],
            },
            {
                "id": "configure",
                "tests": [],
                "mode": "any",
                "available": True,
                "cost": {"hot": 1},
                "next": ["safe", "safe"],
            },
            {
                "id": "p",
                "tests": ["a"],
                "mode": "any",
                "available": True,
                "cost": {"hot": 1, "safe": 1},
                "next": ["safe", "safe"],
            },
        ],
    }


def test_stateful_faulty_probe_policy_matches_all_finite_trees():
    data = toy_policy()
    hypotheses = [["a"], ["b"]]
    actual = v5._policy_v5_511(hypotheses, data)
    trees = all_policy_trees(hypotheses, data, 4)

    def score(row):
        return (
            max(row[0].values()),
            sum(row[0].values()),
            row[1],
            json.dumps(row[2], sort_keys=True, separators=(",", ":")),
        )

    expected = min(trees, key=score)
    assert (
        actual["worst_cost"],
        actual["path_cost_sum"],
        actual["nodes"],
        json.dumps(actual["tree"], sort_keys=True, separators=(",", ":")),
    ) == score(expected)
    assert actual["worst_cost"] == 3
    assert actual["tree"]["probe"] == "warm"  # constant outcome action is essential


def test_persistent_mode_prefix_and_diagnosis_leaf_not_mode_leaf():
    data = toy_policy()
    data["observations"] = [{"probe": "warm", "outcome": 0}, {"probe": "p", "outcome": 1}]
    actual = v5._policy_v5_511([["a"], ["b"]], data)
    assert actual["state"] == "safe" and len(actual["worlds"]) == 2
    assert actual["worst_cost"] == 1 and actual["tree"]["probe"] == "p"
    one = v5._policy_v5_511([["a"]], toy_policy())
    assert len(one["worlds"]) == 2 and one["tree"] == {"hypothesis": 0} and one["worst_cost"] == 0


def test_v5_contracts_lifecycle_and_eight_unchanged(tmp_path):
    previous = {task.id: task for task in _V4_TASKS}
    for task in TASKS:
        number = int(task.id.split("_")[1])
        if number not in (500, 503, 507, 511):
            old = previous[task.id]
            assert (task.prompt, task.setup_files, task.gold_files, task.tags) == (
                old.prompt,
                old.setup_files,
                old.gold_files,
                old.tags,
            )
        else:
            assert "hardening-v4" in task.tags and "hardening-v5" in task.tags
            root = tmp_path / str(number)
            root.mkdir()
            task.setup(root)
            assert not task.verify(root).passed
            task.apply_gold(root)
            assert task.verify(root).passed
    for number, rows in _V5_CASES.items():
        for data, expected in rows:
            before = copy.deepcopy(data)
            assert _V5_SOLVERS[number](data) == expected
            assert data == before
