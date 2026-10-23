"""Independent anchors, reference execution, and source-protection regressions."""

from __future__ import annotations

import hashlib
import json

import pytest

from harness_bench.general_hard import data_tasks as d


@pytest.mark.parametrize("task", d.TASKS, ids=lambda task: task.id)
def test_data_tasks_reject_blank_accept_gold_and_reject_corruption(task, tmp_path):
    task.setup(tmp_path)
    assert not task.verify(tmp_path).passed
    task.apply_gold(tmp_path)
    assert task.verify(tmp_path).passed
    if "solution.py" in task.gold_files:
        (tmp_path / "solution.py").write_text('import sys\nfor line in sys.stdin:\n print("{}")\n')
    else:
        (tmp_path / "answer.json").write_text("{}\n")
    assert not task.verify(tmp_path).passed


@pytest.mark.parametrize("task", d.TASKS, ids=lambda task: task.id)
def test_every_fixture_rejects_modification_and_removal(task, tmp_path):
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    for rel in task.setup_files:
        if rel == "solution.py":
            continue
        path = tmp_path / rel
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        assert not task.verify(tmp_path).passed, rel
        path.unlink()
        assert not task.verify(tmp_path).passed, rel
        path.write_bytes(original)


def test_exact_ids_parents_and_case_coverage():
    assert [int(t.id.split("_")[1]) for t in d.TASKS] == list(range(472, 486))
    assert len([t for t in d.TASKS if "answer.json" in t.gold_files]) == 6
    for task in d.TASKS:
        parent = 352 + (int(task.id.split("_")[1]) - 472) // 2
        assert f"parent-{parent}" in task.tags
    for factory in (
        d._logfmt_cases,
        d._matching_cases,
        d._warehouse_cases,
        d._repair_cases,
        d._contact_partition_cases,
        d._saturation_cases,
        d._range_cases,
        d._compressed_cases,
    ):
        assert len(factory()) >= 12


def test_472_manual_clock_mute_gap_and_union_anchor():
    rows = []
    for key, local, level in [
        ("a", 5, "ERROR"),
        ("b", 9, "ERROR"),
        ("c", 10, "INFO"),
        ("d", 12, "ERROR"),
        ("invalid", 1, "error"),
        ("outside", 20, "INFO"),
    ]:
        rows.append(
            {
                "id": key,
                "rev": 1,
                "seen": 1,
                "op": "put",
                "host": "h",
                "service": "api",
                "fingerprint": "x",
                "local": local,
                "level": level,
            }
        )
    rows.append({"id": "gone", "rev": 2, "seen": 1, "op": "delete"})
    calibration = [{"id": "c", "host": "h", "start": -100, "end": 100, "priority": 0, "offset": 2}]
    config = {
        "services": ["api"],
        "horizon": 14,
        "gap": 3,
        "linger": 5,
        "buckets": [0, 7, 14],
        "snapshots": [{"name": "q", "seen": 2}],
    }
    report = d.log_report(rows, calibration, [{"service": "api", "start": 8, "end": 10}], config)[0]
    assert report == {
        "snapshot": "q",
        "overall": {"DEBUG": 0, "INFO": 0, "WARN": 0, "ERROR": 3},
        "by_service": {"api": {"DEBUG": 0, "INFO": 0, "WARN": 0, "ERROR": 3}},
        "incidents": [
            {"service": "api", "fingerprint": "x", "start": 3, "end": 3, "ids": ["a"]},
            {"service": "api", "fingerprint": "x", "start": 7, "end": 10, "ids": ["b", "d"]},
        ],
        "exposure": [{"service": "api", "seconds": 11, "buckets": [4, 7]}],
        "invalid": ["invalid"],
        "outside": ["outside"],
        "muted": ["c"],
        "deleted": ["gone"],
    }


def test_473_manual_watermark_precedence_and_error_bursts():
    x, expected = d._logfmt_cases()[0]
    assert expected == {
        "invalid": [],
        "late": [3],
        "duplicates": [4],
        "accepted": ["api/a", "api/b", "api/d", "api/e"],
        "services": [
            {
                "svc": "api",
                "counts": {"DEBUG": 0, "INFO": 1, "WARN": 0, "ERROR": 3},
                "bursts": [["b"], ["a"], ["e"]],
            }
        ],
    }
    y = {
        "lateness": 0,
        "gap": 2,
        "lines": ["ts=9 svc=a level=ERROR id=x", "ts=2 svc=a level=ERROR id=x"],
    }
    assert d.logfmt_oracle(y)["late"] == [2]
    assert d.logfmt_oracle(y)["duplicates"] == []


def _small_joint_fixture():
    rows = [
        {"id": "s", "kind": "start", "host": "c", "local": 0, "rev": 1, "seen": 1, "op": "put"},
        {
            "id": "a",
            "kind": "finish",
            "host": "c",
            "local": 4,
            "accept": ["x"],
            "rev": 1,
            "seen": 1,
            "op": "put",
        },
        {
            "id": "b",
            "kind": "finish",
            "host": "c",
            "local": 6,
            "accept": ["x"],
            "rev": 1,
            "seen": 1,
            "op": "put",
        },
        {
            "id": "a",
            "kind": "finish",
            "host": "c",
            "local": 10,
            "accept": ["x"],
            "rev": 2,
            "seen": 2,
            "op": "put",
        },
    ]
    policy = {
        "clocks": {"c": [{"offset": 0, "cost": 0}]},
        "relations": [],
        "capacity": {"r": 1},
        "sessions": {
            "s": {
                "label": "x",
                "resource": "r",
                "min": 0,
                "max": 12,
                "target": 4,
                "weight": 1,
                "change_cost": 5,
                "requires": [],
            }
        },
    }
    queries = [
        {"name": "before", "seen": 1, "horizon": 20},
        {"name": "after", "seen": 2, "horizon": 20},
    ]
    return rows, policy, queries


def _joint_signature(report):
    return (
        -report["objective"]["pairs"],
        report["objective"]["cost"],
        report["objective"]["changes"],
        tuple(report["clock_offsets"][h] for h in sorted(report["clock_offsets"])),
        tuple(
            tuple((p["start"], p["finish"]) for p in snapshot["pairs"])
            for snapshot in report["snapshots"]
        ),
    )


def test_474_manual_joint_choice_beats_independent_snapshot_optima():
    rows, policy, queries = _small_joint_fixture()
    report = d.session_report(rows, policy, queries)
    assert report["objective"] == {"pairs": 2, "cost": 4, "changes": 0}
    assert report["cost_breakdown"] == {"clock": 0, "duration": 4, "changes": 0}
    assert [[(p["start"], p["finish"]) for p in snap["pairs"]] for snap in report["snapshots"]] == [
        [("s", "b")],
        [("s", "b")],
    ]
    assert _joint_signature(report) == d.reconstruction_small_oracle(rows, policy, queries)
    first_only = d.session_report(rows, policy, queries[:1])
    assert first_only["snapshots"][0]["pairs"][0]["finish"] == "a"
    assert first_only["objective"]["cost"] == 0


def test_474_manual_clock_inference_and_dependency_requirement():
    rows, policy, queries = _small_joint_fixture()
    policy["clocks"]["w"] = [{"offset": 0, "cost": 0}, {"offset": 2, "cost": 1}]
    rows = [rows[0], dict(rows[2], host="w")]
    report = d.session_report(rows, policy, queries[:1])
    assert report["clock_offsets"] == {"c": 0, "w": 2}
    assert report["objective"] == {"pairs": 1, "cost": 1, "changes": 0}
    assert _joint_signature(report) == d.reconstruction_small_oracle(rows, policy, queries[:1])
    policy["sessions"]["s"]["requires"] = ["missing"]
    report = d.session_report(rows, policy, queries[:1])
    assert report["objective"]["pairs"] == 0
    assert report["snapshots"][0]["unmatched_starts"] == ["s"]


@pytest.mark.parametrize("seed", range(12))
def test_474_independent_small_assignment_sequence_oracle(seed):
    import random

    rng = random.Random(4742000 + seed)
    rows, policy, queries = _small_joint_fixture()
    policy["clocks"]["w"] = [{"offset": -1, "cost": 1}, {"offset": 1, "cost": 0}]
    policy["capacity"]["r"] = 1 + seed % 2
    policy["sessions"]["t"] = dict(
        policy["sessions"]["s"], target=3, change_cost=2, requires=["s"] if seed % 3 == 0 else []
    )
    rows.append(
        {"id": "t", "kind": "start", "host": "c", "local": 3, "rev": 1, "seen": 1, "op": "put"}
    )
    for row in rows:
        if row["kind"] == "finish":
            row["host"] = "w"
            row["local"] = rng.randrange(2, 11)
    if seed % 4 == 0:
        rows.append({"id": "b", "rev": 2, "seen": 2, "op": "delete"})
    reference = d.session_report(rows, policy, queries)
    assert _joint_signature(reference) == d.reconstruction_small_oracle(rows, policy, queries)


def test_474_main_fixture_requires_global_coupling_and_boundary_revisions():
    actual = json.loads(d.TASK_474.gold_files["answer.json"])
    assert actual["objective"] == {"pairs": 26, "cost": 154, "changes": 3}
    assert actual["clock_offsets"] == {"collector": 0, "desktop": 1, "mobile": 0}
    assert actual["cost_breakdown"] == {"clock": 2, "duration": 107, "changes": 45}
    assert "moving" in actual["snapshots"][0]["outside"]
    assert "moving" not in actual["snapshots"][1]["outside"]
    assert "moving" in actual["snapshots"][2]["outside"]
    assert all("boundary" in snapshot["outside"] for snapshot in actual["snapshots"])
    policy = json.loads(json.dumps(d._SESSION_POLICY))
    for rule in policy["sessions"].values():
        rule["change_cost"] = 0
    independent = d.session_report(
        d._SESSION_ROWS + d._SESSION_CORRECTIONS, policy, d._SESSION_QUERIES
    )
    assert [s["pairs"] for s in independent["snapshots"]] != [
        s["pairs"] for s in actual["snapshots"]
    ]


@pytest.mark.parametrize("feature", ["capacity", "dependencies"])
def test_474_main_fixture_capacity_and_dependencies_are_binding(feature):
    policy = json.loads(json.dumps(d._SESSION_POLICY))
    if feature == "capacity":
        policy["capacity"] = {resource: 99 for resource in policy["capacity"]}
    else:
        for rule in policy["sessions"].values():
            rule["requires"] = []
    relaxed = d.session_report(d._SESSION_ROWS + d._SESSION_CORRECTIONS, policy, d._SESSION_QUERIES)
    assert relaxed["objective"]["pairs"] == 27
    assert json.loads(d.TASK_474.gold_files["answer.json"])["objective"]["pairs"] == 26


def test_475_manual_capacity_changes_global_assignment():
    cases = d._matching_cases()
    assert cases[0][1] == {
        "pairs": [["a", "x"]],
        "cost": 0,
        "unmatched_starts": ["b"],
        "unmatched_ends": ["y"],
    }
    assert cases[1][1] == {
        "pairs": [["a", "x"], ["b", "y"]],
        "cost": 4,
        "unmatched_starts": [],
        "unmatched_ends": [],
    }


def test_476_manual_per_warehouse_clamp_and_nested_allocation():
    products = [
        {"id": sku, "sku": sku, "name": sku, "active": True, "seen": 1, "rev": 1}
        for sku in ("A", "B", "K")
    ]
    prices = [
        {
            "id": sku,
            "sku": sku,
            "cents": price,
            "discount_bps": 1000 if sku == "K" else 0,
            "start": 0,
            "end": 20,
            "priority": 0,
            "seen": 1,
            "rev": 1,
        }
        for sku, price in [("A", 101), ("B", 200), ("K", 500)]
    ]
    moves = [
        {"id": str(i), "sku": sku, "warehouse": wh, "delta": qty, "at": 0, "seen": 1, "rev": 1}
        for i, (sku, wh, qty) in enumerate([("A", "east", 3), ("A", "west", -1), ("B", "west", 2)])
    ]
    holds = [
        {
            "id": "hold",
            "sku": "A",
            "warehouse": "east",
            "qty": 1,
            "start": 0,
            "until": 20,
            "state": "held",
            "seen": 1,
            "rev": 1,
        }
    ]
    report = d.catalog_report(
        products,
        prices,
        moves,
        holds,
        {"K": {"A": 1, "B": 1}},
        {"east": True, "west": True},
        {"A": 1, "K": 2},
        [{"name": "q", "at": 5, "seen": 1}],
    )[0]
    assert report["allocation"] == [{"sku": "K", "quantity": 2}]
    assert report["units"] == 2 and report["revenue_cents"] == 900
    assert report["remaining"] == {"A": 0, "B": 0}
    assert report["catalog"][-1] == {
        "sku": "K",
        "name": "K",
        "price_cents": 450,
        "standalone_available": 2,
        "requirements": {"A": 1, "B": 1},
    }
    assert report["bins"][1] == {
        "sku": "A",
        "warehouse": "west",
        "stock": -1,
        "reserved": 0,
        "available": 0,
    }


def test_477_manual_expiration_precedes_ship():
    _, expected = d._warehouse_cases()[0]
    assert expected == {
        "inventory": [{"lot": "l", "warehouse": "x", "free": 3, "held": 0}],
        "holds": [
            {
                "id": "h",
                "state": "expired",
                "until": 5,
                "allocations": [{"lot": "l", "warehouse": "x", "qty": 2}],
            }
        ],
        "audit": [{"id": "h", "status": "accepted"}, {"id": "s", "status": "inactive_hold"}],
        "shipped": {"A": 0},
        "expired_units": {"A": 0},
    }


def test_478_manual_non_greedy_chain_and_independent_payload_hash():
    expected = json.loads(d.TASK_478.gold_files["answer.json"])
    assert expected["selected"] == [
        "segment_0000_0005.g001.copy1.json",
        "segment_0006_0011.g004.copy1.json",
        "segment_0012_0023.g003.copy1.json",
    ]
    assert (
        expected["covered"] == 24 and expected["generation_sum"] == 8 and expected["missing"] == []
    )
    content = "".join(
        f"{i}:{label}:{i % 7}\n"
        for label, indices in [("b1", range(6)), ("b2", range(6, 12)), ("b3", range(12, 24))]
        for i in indices
    )
    assert expected["records_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert len(expected["invalid"]) == 6 and len(expected["duplicates"]) == 2
    assert expected["unreachable"] == ["segment_0014_0017.g008.copy1.json"]


def test_479_manual_shared_donor_paid_once():
    assert d._repair_cases()[0][1] == {
        "plan": ["a", "b"],
        "cost": 7,
        "bytes": 9,
        "resources": ["r"],
    }
    assert d._repair_cases()[1][1] == {"plan": None}


def test_480_manual_hub_suppression_and_explicit_field_erase():
    rows = []
    for key, name, phone, _priority in [
        ("a", "Alice", "+1", 3),
        ("b", "Bob", "+2", 1),
        ("c", "", "+2", 2),
    ]:
        rows.append(
            {
                "id": key,
                "source": key,
                "effective": 0,
                "seen": 1,
                "rev": 1,
                "fields": {
                    "name": {"value": name, "at": 5 if key == "c" else 1, "erase": key == "c"},
                    "email": {"value": " SAME@example.test ", "at": 2, "erase": False},
                    "phone": {"value": phone, "at": 2, "erase": False},
                },
            }
        )
    config = {
        "source_priority": {"a": 3, "b": 1, "c": 2},
        "hub_limit": 2,
        "minimum_confidence": 80,
        "snapshots": [{"name": "q", "seen": 1, "at": 10}],
    }
    report = d.temporal_contacts_report(rows, [], config)[0]
    assert report["suppressed"] == [
        {"kind": "email", "value": "same@example.test", "ids": ["a", "b", "c"]}
    ]
    assert report["groups"][0]["source_ids"] == ["a"]
    assert report["groups"][1] == {
        "canonical_id": "b",
        "source_ids": ["b", "c"],
        "name": "",
        "email": "same@example.test",
        "phone": "+2",
        "provenance": {"name": "c", "email": "c", "phone": "c"},
    }


def test_481_manual_conflicts_require_global_optimum():
    assert d._contact_partition_cases()[0][1] == {"groups": [["a"], ["b", "c"]], "score": 6}
    assert d._contact_partition_cases()[1][1] == {"groups": None}
    assert d.contact_partition_oracle(
        {
            "ids": ["a", "b"],
            "must": [],
            "cannot": [],
            "evidence": [{"a": "a", "b": "b", "weight": 3}, {"a": "b", "b": "a", "weight": -5}],
        }
    ) == {"groups": [["a"], ["b"]], "score": 0}


def test_482_manual_success_wins_deadline_and_cancel_tie():
    gateway = [{"id": "r", "start": 0, "deadline": 10, "endpoint": "/x", "seen": 1, "rev": 1}]
    attempts = [
        {"id": key, "request": "r", "start": a, "finish": b, "result": result, "seen": 1, "rev": 1}
        for key, a, b, result in [
            ("a", 0, 10, "ok"),
            ("b", 2, 12, "ok"),
            ("c", 10, 11, "error"),
            ("d", 11, 12, "error"),
        ]
    ]
    cancel = [{"id": "cancel", "request": "r", "at": 10, "seen": 1, "rev": 1}]
    report = d.latency_report(gateway, attempts, cancel, [{"name": "q", "seen": 1, "horizon": 15}])[
        0
    ]
    assert report["requests"] == [
        {
            "request": "r",
            "endpoint": "/x",
            "status": "complete",
            "terminal_id": "a",
            "latency": 10,
            "attempts": 3,
            "work": 18,
            "busy": 10,
            "wasted": 8,
            "unfinished": ["b", "c"],
            "post_terminal": ["d"],
        }
    ]
    assert report["endpoints"][0]["p50"] == report["endpoints"][0]["p95"] == 10


def test_483_manual_overlapping_retries_and_disconnected_overload():
    _, expected = d._saturation_cases()[0]
    assert expected == {
        "resources": [
            {
                "resource": "a",
                "busy": 8,
                "excess": 4,
                "longest_overload": 2,
                "best_window": [0, 4],
                "window_excess": 2,
                "p95": 6,
            }
        ],
        "durations": [{"id": "x", "duration": 6}, {"id": "y", "duration": 6}],
    }


def test_484_hand_derived_precedence_and_invalid_cases():
    cases = d._range_cases()
    assert cases[2][1] == {"values": [1, 2, 5]}
    assert cases[3][1] == {"values": [5]}
    assert cases[8][1] == {"values": [2, 1, 5, 7]}
    assert sum(expected == {"error": "invalid"} for _, expected in cases) >= 12


def test_485_manual_huge_exact_sum_and_reordering():
    cases = d._compressed_cases()
    m = 10**12
    assert cases[-2][1] == {
        "sizes": [m, m],
        "length": m,
        "answers": [m // 2 + 1, 1, m // 2 - 1, m * (m + 1) // 2],
    }
    assert cases[-3][1]["answers"] == [m, 2, 1, 0, -m, None, 4, 3]


@pytest.mark.parametrize(
    "function,args",
    [
        (
            d.session_report,
            (d._SESSION_ROWS + d._SESSION_CORRECTIONS, d._SESSION_POLICY, d._SESSION_QUERIES),
        ),
        (
            d.log_report,
            (d._LOG_ROWS + d._LOG_CORRECTIONS, d._LOG_CALIBRATIONS, d._LOG_MUTES, d._LOG_CONFIG),
        ),
        (
            d.temporal_contacts_report,
            (d._CONTACT_ROWS + d._CONTACT_CORRECTIONS, d._CONTACT_LINKS, d._CONTACT_CONFIG),
        ),
    ],
)
def test_artifact_input_permutation_invariance(function, args):
    assert function(*args) == function(list(reversed(args[0])), *args[1:])
