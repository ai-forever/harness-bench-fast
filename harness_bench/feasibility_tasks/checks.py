"""Small, deterministic certificate checkers; never execute submitted code.

The contract, not a reference answer, determines acceptance. All arithmetic
uses integers/Fraction. Certificates establish impossibility or a lower bound,
not the empirical inability of a particular model to find an algorithm.
"""

from __future__ import annotations

from fractions import Fraction
from functools import reduce
from operator import xor


def _int(value):
    if type(value) is not int:
        raise ValueError("expected an integer (not bool/float)")
    return value


def _ids(values, universe):
    if not isinstance(values, list) or not values or len(set(values)) != len(values):
        raise ValueError("expected nonempty unique IDs")
    if any(v not in universe for v in values):
        raise ValueError("unknown ID")
    return values


def _fraction(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("rational must be [numerator, denominator]")
    n, d = map(_int, value)
    if d <= 0:
        raise ValueError("denominator must be positive")
    return Fraction(n, d)


def check(spec: dict, report: dict) -> bool:
    """Check a mathematical witness against the authoritative, host-side spec.

    Malformed inputs raise ordinary exceptions, caught by the task verifier.
    Accepted witnesses need not be minimal or equal to the author's gold.
    """
    c = report["certificate"]
    kind = spec["kind"]
    if report["status"] != spec["status"]:
        return False
    if not isinstance(c, dict):
        return False
    if kind == "difference_cycle":
        edges = {e["id"]: e for e in spec["constraints"]}
        chosen = [edges[i] for i in _ids(c["edges"], edges)]
        return (
            all(a["to"] == b["from"] for a, b in zip(chosen, chosen[1:] + chosen[:1], strict=True))
            and sum(e["max_delta"] for e in chosen) < 0
        )
    if kind == "hall":
        eligible = spec["eligible"]
        jobs = _ids(c["jobs"], eligible)
        neighbors = set().union(*(set(eligible[j]) for j in jobs))
        return len(neighbors) < len(jobs)
    if kind == "flow_cut":
        side = set(_ids(c["source_side"], spec["nodes"]))
        capacity = sum(
            e["capacity"] for e in spec["edges"] if e["from"] in side and e["to"] not in side
        )
        return (
            spec["source"] in side and spec["sink"] not in side and capacity < spec["required_flow"]
        )
    if kind == "xor":
        rows = {r["id"]: r for r in spec["rows"]}
        chosen = [rows[i] for i in _ids(c["rows"], rows)]
        odd = set()
        for row in chosen:
            odd.symmetric_difference_update(row["variables"])
        return not odd and reduce(xor, (r["rhs"] for r in chosen), 0) == 1
    if kind == "two_worlds":
        worlds = c["worlds"]
        if not isinstance(worlds, list) or len(worlds) != 2:
            return False
        for w in worlds:
            if len(w) != spec["variables"] or any(_int(v) < 0 for v in w):
                return False
            if any(
                sum(a * b for a, b in zip(row["coefficients"], w, strict=True)) != row["total"]
                for row in spec["observations"]
            ):
                return False
        t = spec["target_coefficients"]
        return sum(a * b for a, b in zip(t, worlds[0], strict=True)) != sum(
            a * b for a, b in zip(t, worlds[1], strict=True)
        )
    if kind == "function_collision":
        records = {r["id"]: r for r in spec["records"]}
        selected = _ids(c["records"], records)
        if len(selected) != 2:
            return False
        a, b = (records[i] for i in selected)
        key = spec["available_fields"]
        return all(a[k] == b[k] for k in key) and a["required_output"] != b["required_output"]
    if kind == "pigeonhole":
        input_count = spec["alphabet_size"] ** spec["input_length"]
        # Variable-length output includes the empty word, with no side channel.
        output_count = sum(
            spec["output_alphabet"] ** i for i in range(spec["max_output_length"] + 1)
        )
        return (
            _int(c["inputs"]) == input_count
            and _int(c["outputs"]) == output_count
            and input_count > output_count
        )
    if kind == "order_cycle":
        edges = {e["id"]: e for e in spec["precedence"]}
        chosen = [edges[i] for i in _ids(c["edges"], edges)]
        return all(
            a["after"] == b["before"] for a, b in zip(chosen, chosen[1:] + chosen[:1], strict=True)
        )
    if kind == "residue":
        m = _int(c["modulus"])
        return (
            m > 1
            and all(d % m == 0 for d in spec["allowed_deltas"])
            and (spec["target"] - spec["initial"]) % m != 0
        )
    if kind == "clique":
        vertices = _ids(c["vertices"], spec["vertices"])
        edges = {frozenset(e) for e in spec["conflicts"]}
        return len(vertices) > spec["colors"] and all(
            frozenset((a, b)) in edges for i, a in enumerate(vertices) for b in vertices[i + 1 :]
        )
    if kind == "guess_probability":
        remaining = spec["random_bits"] - len(set(spec["revealed_bit_positions"]))
        candidates = 2**remaining
        attempts = spec["budget_seconds"] * spec["max_checks_per_second"]
        bound = min(Fraction(attempts, candidates), Fraction(1))
        return (
            _int(c["candidate_count"]) == candidates
            and _int(c["max_attempts"]) == attempts
            and _fraction(c["success_upper_bound"]) == bound
            and bound < _fraction(spec["required_success_probability"])
        )
    if kind == "decision_tree":
        outcomes = spec["outcomes"]
        depth = 0
        while spec["branches"] ** depth < outcomes:
            depth += 1
        available = spec["budget_seconds"] // spec["seconds_per_query"]
        return (
            _int(c["min_queries"]) == depth
            and _int(c["available_queries"]) == available
            and depth > available
        )
    if kind == "resource":
        resource = spec["resources"][c["resource"]]
        jobs = {j["id"]: j for j in spec["jobs"]}
        chosen = _ids(c["jobs"], jobs)
        required = sum(jobs[j]["usage"].get(c["resource"], 0) for j in chosen)
        capacity = (
            resource["units_per_second"] * resource["parallel_units"] * spec["budget_seconds"]
        )
        return (
            _int(c["required_units"]) == required
            and _int(c["capacity_units"]) == capacity
            and required > capacity
        )
    if kind == "critical_path":
        jobs = {j["id"]: j for j in spec["jobs"]}
        chain = _ids(c["jobs"], jobs)
        duration = sum(jobs[j]["min_seconds"] for j in chain)
        return (
            all(a in jobs[b]["depends_on"] for a, b in zip(chain, chain[1:], strict=False))
            and _int(c["min_seconds"]) == duration
            and duration > spec["budget_seconds"]
        )
    if kind == "release":
        jobs = {j["id"]: j for j in spec["jobs"]}
        j = jobs[c["job"]]
        earliest = j["release_seconds"] + j["min_processing_seconds"]
        return _int(c["earliest_finish_seconds"]) == earliest and earliest > spec["budget_seconds"]
    raise ValueError(f"unknown certificate kind: {kind}")
