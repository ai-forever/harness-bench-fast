"""Readiness requires all components; a single genuine obstruction suffices."""

import json
from copy import deepcopy

import pytest

from harness_bench.feasibility_tasks.hard_contracts import (
    CONTRACT_PAIRS,
    check_certificate,
    check_solution,
)


@pytest.mark.parametrize("pair", CONTRACT_PAIRS, ids=lambda p: p["slug"])
def test_all_requirements_and_any_single_obstruction(pair):
    pos, neg = pair["positive"], pair["negative"]
    assert check_solution(pos["spec"], pos["gold"])
    assert not check_solution(neg["spec"], pos["gold"])
    for status in ("impossible", "budget_exceeded"):
        report = dict(status=status, certificate=neg["gold"])
        assert check_certificate(neg["spec"], report)
        assert not check_certificate(pos["spec"], report)
    for identity in pos["gold"]["requirements"]:
        partial = deepcopy(pos["gold"])
        del partial["requirements"][identity]
        assert not check_solution(pos["spec"], partial)
    assert pos["public_spec"].keys() == {"requirements"}
    assert len(pos["public_spec"]["requirements"]) == 3
    for case in (pos, neg):
        for r in case["public_spec"]["requirements"]:
            assert set(r) == {"id", "goal", "rules", "response_formats", "contract"}
            assert set(r["response_formats"]) == {"solution", "certificate"}
            assert all(isinstance(v, str) and v for v in r["response_formats"].values())
            assert not {"kind", "status", "gold", "target_seed"} & set(r["contract"])
        assert len(json.dumps(case["gold"])) < 20000
    for key in ("request", "instructions", "component_formats", "schema", "solution_schema"):
        assert pos[key] == neg[key]


def test_unknown_requirement_and_unsupported_negative_claim_fail():
    case = CONTRACT_PAIRS[0]["negative"]
    claim = deepcopy(case["gold"])
    claim["requirement"] = "invented"
    assert not check_certificate(case["spec"], {"status": "impossible", "certificate": claim})
    assert not check_certificate(case["spec"], {"status": "impossible", "certificate": {}})
    assert not check_solution(case["spec"], {"requirements": {}})
