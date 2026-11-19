"""False-negative scoreboard override (variant B) — issue #462.

Infra-crash OC tasks that a K=3 rerun proved solvable are counted as passed and
labelled ``решаемо (K=3: N/3)``; raw artifacts are untouched.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fn = _load("fn_override", ROOT / "scripts" / "fork_false_negative_override.py")

FEAS_OFF = "oc_medium_feasibility_minimaxm3_off_20261109"
FEAS_MED = "oc_medium_feasibility_minimaxm3_medium_20261109"
REFLECT_OFF = "oc_medium_reflect_minimaxm3_off_20261109"
LONG_MED = "oc_medium_long_minimaxm3_medium_20261109"


def test_rescored_tasks_and_labels():
    assert fn.rescored_label(fn.task_status(FEAS_OFF, "task_449_search_capabilities")) \
        == "решаемо (K=3: 2/3)"
    assert fn.rescored_label(fn.task_status(FEAS_MED, "task_470_search_hint")) \
        == "решаемо (K=3: 1/3)"
    assert fn.rescored_label(fn.task_status(REFLECT_OFF, "task_422_calendar_timezone")) \
        == "решаемо (K=3: 2/3)"
    assert fn.rescored_label(fn.task_status(REFLECT_OFF, "task_429_mail_bounce")) \
        == "решаемо (K=3: 2/3)"


def test_real_failure_stays_failed():
    # task_394 passed 0/3 -> not listed -> no override.
    assert fn.task_status(LONG_MED, "task_394_review_annotation") is None


def test_apply_number_only_bumps_listed_failures():
    assert fn.apply_number(FEAS_OFF, "task_449_search_capabilities", False) is True
    assert fn.apply_number(FEAS_OFF, "task_436_search_capabilities", False) is False
    assert fn.apply_number(FEAS_OFF, "task_432_clock_contract", True) is True


def test_apply_summary_variant_b():
    # variant B: +1 passed per rescored task, total unchanged
    assert fn.apply_summary(FEAS_OFF, {"passed": 33, "total": 40, "pass_rate": 0.825}) == {
        "passed": 34, "total": 40, "pass_rate": 0.85}
    assert fn.apply_summary(FEAS_MED, {"passed": 26, "total": 40, "pass_rate": 0.65})["passed"] == 27
    assert fn.apply_summary(REFLECT_OFF, {"passed": 13, "total": 20, "pass_rate": 0.65})["passed"] == 15
    # untouched run / non-dict
    assert fn.apply_summary("oc_shotlist_glm52_full_391", {"passed": 1, "total": 2}) == {"passed": 1, "total": 2}
    assert fn.apply_summary(FEAS_OFF, None) is None


def test_pages_task_entry_rescored():
    pages = _load("fp_test", ROOT / "scripts" / "build_fork_pages.py")
    entry = {"task_id": "task_449_search_capabilities", "passed": False, "message": "x"}
    norm = pages._task_entry(entry, stem=FEAS_OFF)
    assert norm["passed"] is True
    assert norm["rescored"] == "решаемо (K=3: 2/3)"
    # a plain failing task is unchanged
    plain = pages._task_entry({"task_id": "task_436_search_capabilities", "passed": False},
                              stem=FEAS_OFF)
    assert plain["passed"] is False and plain["rescored"] is None


def test_pages_artifact_numbers_rescored():
    pages = _load("fp_test2", ROOT / "scripts" / "build_fork_pages.py")
    data = {"tasks": [{"task_id": "task_449_search_capabilities", "passed": False}]}
    assert pages._artifact_task_numbers(data, FEAS_OFF) == {449: True}
    assert pages._artifact_task_numbers(data, None) == {449: False}
