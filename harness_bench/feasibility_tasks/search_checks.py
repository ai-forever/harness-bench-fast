"""Credit verified finds or a correct strategy, retaining separate telemetry."""

from __future__ import annotations

from fractions import Fraction


def rational(value):
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(v) is not int for v in value)
        or value[1] <= 0
    ):
        raise ValueError("expected rational [integer, positive integer]")
    return Fraction(*value)


def probability(spec, remaining, reads=None):
    pool = len(spec["hint"]) if spec["hint"] is not None else spec["size"]
    if spec["mode"] == "key":
        return Fraction(int((remaining if reads is None else reads) >= 1))
    return min(Fraction(max(0, remaining if reads is None else reads), pool), Fraction(1))


def assess(spec, state):
    """Return (passed, telemetry); legal verified finds override strategy errors."""
    required_diagnostics = (["count"] if spec["unknown_size"] else []) + (
        ["describe"] if spec["unknown_mode"] else []
    )
    diagnostics = state["diagnostics"]
    reads = state["reads"]
    unique = {r["id"] for r in reads}
    metrics = {
        "query_count": state["used"],
        "diagnostic_requests": len(diagnostics),
        "search_requests": len(reads),
        "unique_reads": len(unique),
        "extra_requests": state["errors"],
        "target_found": state["found"] is not None,
        "decision_correct": False,
        "probability_error": None,
        "success_basis": "none",
    }
    errors = []
    if sorted(diagnostics) != sorted(required_diagnostics):
        errors.append("missing or unnecessary diagnostics")
        metrics["extra_requests"] += sum(
            max(0, diagnostics.count(d) - required_diagnostics.count(d)) for d in set(diagnostics)
        )
    plan = state["plan"]
    plan_seen = False
    for event in state["events"]:
        if event["command"] == "plan":
            plan_seen = True
        elif event["command"] in ("read", "lookup", "date") and not plan_seen:
            errors.append("search before plan")
        elif event["command"] in ("count", "describe") and plan_seen:
            errors.append("diagnostics after plan")
    remaining = spec["budget"] - len(diagnostics)
    upper = probability(spec, remaining)
    threshold = rational(spec["threshold"])
    metrics["max_success_probability"] = [upper.numerator, upper.denominator]
    try:
        if not isinstance(plan, dict):
            raise ValueError("missing plan")
        declared = rational(plan["success_probability"])
        k = plan["max_reads"]
        if type(k) is not int or k < 0 or k > remaining or plan["method"] != spec["mode"]:
            raise ValueError("invalid plan")
        expected = upper if plan["decision"] == "stop" else probability(spec, remaining, k)
        error = abs(declared - expected)
        metrics["probability_error"] = [error.numerator, error.denominator]
        metrics["declared_probability"] = [declared.numerator, declared.denominator]
        metrics["decision"] = plan["decision"]
        if error:
            errors.append("incorrect probability")
        if plan["decision"] == "stop":
            metrics["decision_correct"] = upper < threshold and k == 0
            if reads:
                errors.append("search despite stop decision")
        elif plan["decision"] == "search":
            metrics["decision_correct"] = upper >= threshold and expected >= threshold and k > 0
            if len(reads) > k or (state["found"] is None and len(unique) != k):
                errors.append("strategy not executed to completion")
            if spec["hint"] is not None and any(r["id"] not in spec["hint"] for r in reads):
                errors.append("read outside known candidate set")
            if spec["mode"] == "key" and (k != 1 or len(reads) != 1):
                errors.append("exact lookup needs one request")
        else:
            raise ValueError("unknown decision")
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        errors.append("malformed plan")
    found_at = next((i for i, r in enumerate(reads) if r["matched"]), None)
    if found_at is not None and found_at != len(reads) - 1:
        errors.append("continued reading after success")
    # Count each unnecessary successful read at most once; invalid calls and
    # unnecessary diagnostics are disjoint categories counted above.
    seen = set()
    for i, record in enumerate(reads):
        outside = spec["hint"] is not None and record["id"] not in spec["hint"]
        cap = plan.get("max_reads", 0) if isinstance(plan, dict) else 0
        excess = (
            upper < threshold
            or record["id"] in seen
            or outside
            or (found_at is not None and i > found_at)
            or (type(cap) is int and i >= cap)
        )
        metrics["extra_requests"] += int(excess)
        seen.add(record["id"])
    if not metrics["decision_correct"]:
        errors.append("wrong decision for available budget and tools")
    if len(unique) != len(reads):
        errors.append("repeated reads")
    metrics["strategy_correct"] = not errors
    metrics["strategy_reason"] = "; ".join(dict.fromkeys(errors)) or "valid decision and execution"
    hard_errors = []
    if state["used"] > spec["budget"] or state["errors"]:
        hard_errors.append("invalid commands or query budget violation")
    if not state["finished"] or state["finish"] != {"found_id": state["found"]}:
        hard_errors.append("missing finish or unsupported found_id")
    verified_find = state["found"] is not None and any(
        r["matched"] and r["id"] == state["found"] for r in reads
    )
    if not hard_errors and verified_find:
        metrics["success_basis"] = "verified_find"
        metrics["reason"] = "verified target found within budget"
    elif not hard_errors and not errors:
        metrics["success_basis"] = "strategy"
        metrics["reason"] = "valid decision and execution"
    else:
        metrics["reason"] = "; ".join(dict.fromkeys(hard_errors + errors))
    return metrics["success_basis"] != "none", metrics
