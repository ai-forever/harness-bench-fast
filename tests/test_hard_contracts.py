"""No hidden author label may reject a valid impossibility witness."""

from copy import deepcopy

import pytest

from harness_bench.feasibility_tasks.hard_contracts import ATOMIC_PAIRS as CONTRACT_PAIRS
from harness_bench.feasibility_tasks.hard_contracts import check_certificate


@pytest.mark.parametrize("pair", CONTRACT_PAIRS, ids=lambda p: p["slug"])
@pytest.mark.parametrize("status", ["impossible", "budget_exceeded"])
def test_negative_labels_are_equivalent_only_with_valid_proof(pair, status):
    case = pair["negative"]
    report = {"status": status, "certificate": deepcopy(case["gold"])}
    assert check_certificate(case["spec"], report)
    assert report["status"] == status
    assert not check_certificate(pair["positive"]["spec"], report)
    # Label equivalence must not make empty unsupported refusal acceptable.
    try:
        accepted = check_certificate(case["spec"], {"status": status, "certificate": {}})
    except (KeyError, ValueError, TypeError):
        accepted = False
    assert not accepted


def test_actual_archive_interval_rejected_only_by_old_hidden_label():
    pair = next(p for p in CONTRACT_PAIRS if p["slug"] == "archive_transfer")
    cert = {"start": 0, "finish": 185}
    jobs = pair["negative"]["spec"]["jobs"]
    assert sum(j["work"] for j in jobs if j["release"] >= 0 and j["deadline"] <= 185) == 186
    assert check_certificate(
        pair["negative"]["spec"], {"status": "impossible", "certificate": cert}
    )


@pytest.mark.parametrize("slug", ["archive_transfer", "build_critical_path"])
def test_equivalent_interval_shapes_keep_mathematical_validation(slug):
    pair = next(p for p in CONTRACT_PAIRS if p["slug"] == slug)
    original = pair["negative"]["gold"]
    a, b = original.get("interval", [original.get("start"), original.get("finish")])
    for certificate in ({"interval": [a, b]}, {"start": a, "finish": b}):
        report = {"status": "impossible", "certificate": certificate}
        assert check_certificate(pair["negative"]["spec"], report)
        assert not check_certificate(pair["positive"]["spec"], report)
    for bad in (
        {"interval": [a, b], "start": a, "finish": b + 1},
        {"interval": [a, b, b]},
        {"interval": [a, b], "start": a},
        {"interval": [b, a]},
        {"interval": [False, b]},
    ):
        assert not check_certificate(
            pair["negative"]["spec"], {"status": "impossible", "certificate": bad}
        )
