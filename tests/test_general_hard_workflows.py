"""Independent small oracles plus the complete candidate lifecycle."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import random
from fractions import Fraction

import pytest

from harness_bench.general_hard.workflow_tasks import (
    _CASES,
    _INPUTS,
    _SOLVERS,
    TASKS,
    _allocate_503,
    _couple_503,
    _solve_500,
    _solve_501,
    _solve_502,
    _solve_503,
    _solve_504,
    _solve_505,
    _solve_506,
    _solve_507,
    _solve_508,
    _solve_509,
    _solve_510,
    _solve_511,
)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.id)
def test_workflow_gold_empty_broken_and_fixture_mutation(task, tmp_path):
    task.setup(tmp_path)
    assert not task.verify(tmp_path).passed, "empty workspace must not pass"
    task.apply_gold(tmp_path)
    result = task.verify(tmp_path)
    assert result.passed, result.message
    if "program" in task.tags:
        (tmp_path / "solution.py").write_text(
            "import sys\nfor line in sys.stdin:\n print('{}')\n", encoding="utf-8"
        )
    else:
        (tmp_path / "answer.json").write_text("{}", encoding="utf-8")
    assert not task.verify(tmp_path).passed, "constant empty output must not pass"
    task.apply_gold(tmp_path)
    source = next(path for path in task.setup_files if path != "solution.py")
    with (tmp_path / source).open("a", encoding="utf-8", newline="") as handle:
        handle.write("\nmodified fixture\n")
    assert not task.verify(tmp_path).passed, "a correct answer cannot excuse changed source"


def test_workflow_registry_and_hidden_case_counts():
    assert [int(task.id.split("_")[1]) for task in TASKS] == list(range(500, 512))
    assert sum("program" in task.tags for task in TASKS) == 8
    assert len(_CASES) == 8
    assert all(len(cases) >= 12 for cases in _CASES.values())
    for task in TASKS:
        number = int(task.id.split("_")[1])
        assert f"parent-{366 + (number - 500) // 2}" in task.tags
        if "program" in task.tags:
            examples = json.loads(task.setup_files["examples.json"])
            assert len(examples) == 2
            assert examples == [
                {"input": data, "output": expected} for data, expected in _CASES[number][:2]
            ]


@pytest.mark.parametrize("number", sorted(_INPUTS))
def test_reference_inputs_are_not_mutated(number):
    original = copy.deepcopy(_INPUTS[number])
    for data in _INPUTS[number]:
        _SOLVERS[number](data)
    assert _INPUTS[number] == original


def test_backup_retention_and_cas_manual_oracle():
    x, empty, y = digest("x"), digest(""), digest("y")
    answer = _solve_500({
        "objects": {"same": "x", "alias": "x", "empty": "", "unused": "y"},
        "snapshots": [
            {"id": "child", "parent": "root", "ops": [
                {"op": "drop", "path": "a"},
                {"op": "put", "path": "b", "object": "alias"},
                {"op": "put", "path": "e", "object": "empty"},
            ]},
            {"id": "root", "parent": None, "ops": [
                {"op": "put", "path": "a", "object": "same"},
            ]},
        ],
        "keep": ["child", "child"], "existing": [x, y, x],
    })
    tree = digest(f"b\0{1}\0{x}\ne\0{0}\0{empty}\n")
    assert answer == {
        "snapshots": [{"id": "child", "files": [
            {"path": "b", "size": 1, "sha256": x}, {"path": "e", "size": 0, "sha256": empty},
        ], "tree_sha256": tree}],
        "required": sorted([x, empty]), "upload": [empty], "gc": [y],
        "logical_bytes": 1, "physical_bytes": 1,
    }


def test_backup_copy_captures_before_destination_deletion():
    answer = _solve_500({
        "objects": {"a": "a", "b": "b"},
        "snapshots": [{"id": "s", "parent": None, "ops": [
            {"op": "put", "path": "d/a", "object": "a"},
            {"op": "put", "path": "d/b", "object": "b"},
            {"op": "copy", "source": "d", "path": "d/sub"},
            {"op": "copy", "source": "d/sub", "path": "d"},
        ]}], "keep": ["s"], "existing": [],
    })
    assert [(row["path"], row["sha256"]) for row in answer["snapshots"][0]["files"]] == [
        ("d/a", digest("a")), ("d/b", digest("b")),
    ]
    assert answer["logical_bytes"] == answer["physical_bytes"] == 2


def test_merkle_byte_framing_manual_oracle():
    leaf = hashlib.sha256(b"F1:A").digest()
    root = hashlib.sha256(b"D1:a" + leaf).hexdigest()
    assert _solve_501({"entries": [{"path": "A", "kind": "file", "text": "A"}]}) == {
        "root": root,
        "entries": [{"path": "a", "kind": "file", "sha256": leaf.hex()}],
        "hardlink_groups": [["a"]], "logical_bytes": 1, "physical_bytes": 1,
    }
    assert _solve_501({"entries": []})["root"] == hashlib.sha256(b"D").hexdigest()


def test_merkle_error_precedence_and_hardlink_identity():
    assert _solve_501({"entries": [
        {"path": "e\u0301", "kind": "file", "text": "one"},
        {"path": "É", "kind": "file", "text": "two"},
    ]}) == {"error": {"code": "collision", "index": 1}}
    assert _solve_501({"entries": [
        {"path": "a", "kind": "hardlink", "target": "b"},
        {"path": "b", "kind": "hardlink", "target": "a"},
    ]}) == {"error": {"code": "cycle", "path": "a"}}
    result = _solve_501({"entries": [
        {"path": "x", "kind": "file", "text": "é"},
        {"path": "y", "kind": "file", "text": "é"},
        {"path": "z", "kind": "hardlink", "target": "x"},
    ]})
    assert result["hardlink_groups"] == [["x", "z"], ["y"]]
    assert result["logical_bytes"] == 6
    assert result["physical_bytes"] == 4


def test_invoice_reversal_parity_and_two_phase_earmarks_manual_oracle():
    data = {
        "invoices": [
            {"id": "new", "customer": "c", "currency": "X", "due": 2, "amount": 100},
            {"id": "old", "customer": "c", "currency": "X", "due": 1, "amount": 100},
        ],
        "events": [
            {"id": "p1", "seq": 1, "kind": "payment", "customer": "c",
             "currency": "X", "amount": 80},
            {"id": "p2", "seq": 2, "kind": "payment", "customer": "c",
             "currency": "X", "amount": 100, "invoice": "new"},
            {"id": "p3", "seq": 3, "kind": "payment", "customer": "c",
             "currency": "X", "amount": 30},
            {"id": "f1", "seq": 4, "kind": "refund", "ref": "p1", "amount": 20},
            {"id": "r1", "seq": 5, "kind": "reverse", "ref": "f1"},
            {"id": "r2", "seq": 6, "kind": "reverse", "ref": "r1"},
            {"id": "r3", "seq": 7, "kind": "reverse", "ref": "p3"},
            {"id": "f2", "seq": 8, "kind": "refund", "ref": "p3", "amount": 25},
        ],
    }
    assert _solve_502(data) == {
        "invoices": [
            {"id": "new", "paid": 100, "balance": 0, "status": "paid"},
            {"id": "old", "paid": 60, "balance": 40, "status": "partial"},
        ],
        "payments": [
            {"id": "p1", "net": 60, "applied": 60, "credit": 0},
            {"id": "p2", "net": 100, "applied": 100, "credit": 0},
        ],
        "refunds": [
            {"id": "f1", "accepted": 20, "rejected": 0},
            {"id": "f2", "accepted": 0, "rejected": 25},
        ],
        "inactive": ["p3", "r1"],
    }


def test_signed_bankers_largest_remainder_manual_oracles():
    assert _allocate_503({"a": Fraction(-1, 2)}) == {"a": 0}
    assert _allocate_503({"a": Fraction(-3, 2)}) == {"a": -2}
    assert _allocate_503({"z": Fraction(1, 2), "a": Fraction(1, 2)}) == {"z": 0, "a": 1}
    assert _allocate_503({
        "a": Fraction(1, 2), "b": Fraction(1, 2), "c": Fraction(-1, 2),
    }) == {"a": 1, "b": 0, "c": -1}


def test_inclusive_discount_fx_manual_oracle():
    assert _solve_503({"base_scale": 100, "row_totals": {"i": 1740},
                       "column_totals": {"home": 1740}, "invoices": [{
        "id": "i", "currency": "X", "scale": 100, "fx": [3, 2], "discount": [1, 3],
        "lines": [{"id": "a", "qty": 1, "unit": [12, 1], "tax": [1, 5],
                   "inclusive": True, "jurisdiction": "home"}],
    }]}) == {
        "feasible": True,
        "invoices": [{
            "id": "i", "currency": "X", "due_minor": 1160, "base_minor": 1740,
            "lines": [{"id": "a", "discount": "1/3", "net_minor": 967,
                       "tax_minor": 193, "gross_minor": 1160}],
            "allocations": [{"jurisdiction": "home", "exact_base": "1740/1", "base_minor": 1740}],
        }],
        "base_total": 1740,
        "columns": [{"jurisdiction": "home", "base_minor": 1740}],
        "squared_error": "0/1",
    }


def test_tax_and_net_round_separately_and_fx_across_currencies():
    answer = _solve_503(_INPUTS[503][11])
    assert answer["invoices"][0]["due_minor"] == 0
    half = _solve_503(_INPUTS[503][10])
    assert half["invoices"][0]["due_minor"] == 1
    assert half["invoices"][0]["base_minor"] == 0
    assert half["squared_error"] == "1/4"


def _exhaustive_coupling_oracle(exact, rows, columns):
    """Independent enumeration: no graph, augmenting paths or encoded tie costs."""
    if sum(rows.values()) != sum(columns.values()):
        return {"feasible": False, "reason": "total"}
    keys = sorted(exact)
    for group, coordinate in ((rows, 0), (columns, 1)):
        for name, target in group.items():
            values = [exact[key] for key in keys if key[coordinate] == name]
            if not sum(math.floor(v) for v in values) <= target <= sum(math.ceil(v) for v in values):
                return {"feasible": False, "reason": "bounds"}
    choices = [sorted({math.floor(exact[key]), math.ceil(exact[key])}) for key in keys]
    assert sum(len(choice) > 1 for choice in choices) <= 12
    best = None
    answer = None
    for values in itertools.product(*choices):
        allocation = dict(zip(keys, values, strict=True))
        if any(sum(value for (r, _), value in allocation.items() if r == row) != target
               for row, target in rows.items()):
            continue
        if any(sum(value for (_, c), value in allocation.items() if c == col) != target
               for col, target in columns.items()):
            continue
        error = sum(((allocation[key] - exact[key]) ** 2 for key in keys), Fraction())
        score = error, values
        if best is None or score < best:
            best, answer = score, allocation
    if best is None:
        return {"feasible": False, "reason": "coupling"}
    return {"feasible": True, "allocation": answer, "squared_error": best[0]}


def test_coupled_rounding_hand_derived_global_and_lex_optima():
    equal = _solve_503(_INPUTS[503][0])
    assert [[item["base_minor"] for item in row["allocations"]] for row in equal["invoices"]] == [
        [0, 1], [1, 0],
    ]
    assert equal["squared_error"] == "1/1"
    greedy_trap = _solve_503(_INPUTS[503][1])
    assert [[item["base_minor"] for item in row["allocations"]]
            for row in greedy_trap["invoices"]] == [[0, 1], [1, 0]]
    assert greedy_trap["squared_error"] == "353/400"
    close = _solve_503(_INPUTS[503][8])
    assert [[item["base_minor"] for item in row["allocations"]]
            for row in close["invoices"]] == [[1, 0], [0, 1]]
    assert _solve_503(_INPUTS[503][3]) == {"feasible": False, "reason": "total"}
    assert _solve_503(_INPUTS[503][4]) == {"feasible": False, "reason": "bounds"}
    assert _solve_503(_INPUTS[503][5]) == {"feasible": False, "reason": "coupling"}
    signed = _solve_503(_INPUTS[503][6])
    assert [[item["base_minor"] for item in row["allocations"]]
            for row in signed["invoices"]] == [[-2, 1], [1, -2]]


def test_coupled_rounding_matches_independent_exhaustive_oracle():
    rng = random.Random(50320)
    for case in range(90):
        n, m = 2 + case % 2, 2 + (case // 2) % 2
        exact = {(f"r{i}", f"c{j}"): Fraction(rng.randrange(-9, 14), rng.randrange(2, 8))
                 for i in range(n) for j in range(m)}
        chosen = {key: rng.choice([math.floor(value), math.ceil(value)]) for key, value in exact.items()}
        rows = {f"r{i}": sum(value for (row, _), value in chosen.items() if row == f"r{i}")
                for i in range(n)}
        columns = {f"c{j}": sum(value for (_, col), value in chosen.items() if col == f"c{j}")
                   for j in range(m)}
        if case % 3 == 0:
            rows["r0"] += 1
            columns["c0"] += 1
        if case % 7 == 0:
            columns["c0"] += 1
        assert _couple_503(exact, rows, columns) == _exhaustive_coupling_oracle(exact, rows, columns)


def test_coupled_eight_by_eight_tie_has_deterministic_lex_solution():
    result = _solve_503(_INPUTS[503][14])
    matrix = [[item["base_minor"] for item in row["allocations"]] for row in result["invoices"]]
    assert matrix == [[0] * 4 + [1] * 4] * 4 + [[1] * 4 + [0] * 4] * 4
    assert result["squared_error"] == "16/1"


def test_retry_public_example_explicitly_demonstrates_sorted_tests():
    task = next(task for task in TASKS if task.id.startswith("task_510_"))
    example = json.loads(task.setup_files["examples.json"])[1]
    assert example["input"]["expected"] == ["z", "a", "z"]
    assert [row["test"] for row in example["output"]["tests"]] == ["a", "z"]
    assert "НЕЗАВИСИМО от исходного порядка expected" in task.prompt


def test_relational_union_ids_and_transactional_failure_manual_oracle():
    assert _solve_504(_INPUTS[504][1]) == {
        "ok": True,
        "state": {
            "version": 2,
            "customers": [{"id": 2, "tenant": "t", "name": "a", "aliases": ["a", "b", "c"]}],
            "orders": [
                {"id": 2, "customer_id": 2, "total": 100, "paid": 0, "status": "unpaid"},
                {"id": 7, "customer_id": 2, "total": 100, "paid": 0, "status": "unpaid"},
            ],
            "payments": [],
        },
    }
    bad = _INPUTS[504][-1]
    assert _solve_504(bad) == {"ok": False, "error": "duplicate-order", "state": bad}
    settled = _solve_504(_INPUTS[504][12])
    assert settled["state"]["orders"][0]["paid"] == 100
    assert settled["state"]["orders"][0]["status"] == "paid"
    assert _solve_504(settled["state"]) == settled


def test_temporal_null_masks_and_equal_value_coalescing_manual_oracle():
    common = {"recorded": 1, "revision": 1, "deleted": False, "entity": "x", "priority": 0}
    result = _solve_505({
        "version": 1, "cutoff": 1,
        "records": [
            {**common, "id": "a", "start": None, "end": 0, "value": "X"},
            {**common, "id": "b", "start": 0, "end": 5, "value": "X"},
            {**common, "id": "mask", "start": 2, "end": 3, "value": None, "priority": 1},
        ],
        "queries": [{"entity": "x", "at": i} for i in (-100, 0, 2, 3, 5)],
    })
    assert result == {
        "version": 2,
        "segments": [
            {"entity": "x", "start": None, "end": 2, "value": "X", "sources": ["a", "b"]},
            {"entity": "x", "start": 3, "end": 5, "value": "X", "sources": ["b"]},
        ],
        "answers": ["X", "X", None, "X", None],
    }
    assert _solve_505({**result, "queries": []}) == {
        "version": 2, "segments": result["segments"], "answers": [],
    }
    assert _solve_505(_INPUTS[505][7])["segments"] == []
    assert _solve_505(_INPUTS[505][8])["segments"] != []


def test_lossless_ini_empty_continuation_quotes_and_eol_manual_oracle():
    assert _solve_506({
        "text": '[S]\r\n K : "a # b" \t; c\r\nk=  # empty\r\npath=one \\\n[t] \\\r\n three # final',
        "edits": [
            {"section": "s", "key": "k", "which": 1, "value": "new\nline"},
            {"section": "s", "key": "k", "which": 2, "value": ""},
            {"section": "s", "key": "path", "which": "all", "value": "ONE"},
            {"section": "t", "key": "path", "which": "all", "value": "not present"},
        ],
    }) == {
        "text": '[S]\r\n K : "new\\nline" \t; c\r\nk=""  # empty\r\npath="ONE" # final',
        "applied": [1, 1, 1, 0],
    }


def test_scoped_include_capture_restore_and_literal_dollar_manual_oracle():
    data = {
        "entry": "main", "env": {},
        "files": {
            "main": "[s]\n@let V=outer\n@include child\nx=${VAR:V}\nz=${y}|$${s.x}\n",
            "child": "@let V=inner\ny=${VAR:V}\n[t]\nx=${s.x}\n",
        },
    }
    assert _solve_507(data) == {
        "values": {"s": {"x": "outer", "y": "inner", "z": "inner|${s.x}"}, "t": {"x": "outer"}},
        "origins": {"s": {"x": "main:4", "y": "child:2", "z": "main:5"}, "t": {"x": "child:4"}},
    }
    assert _solve_507({
        "entry": "main", "env": {}, "files": {"main": "x=${y:-safe}\ny=${missing}\n"},
    }) == {"error": "missing"}
    assert _solve_507({
        "entry": "main", "env": {"X": ""},
        "files": {"main": "x=${ENV:X:-safe}\n"},
    })["values"] == {"global": {"x": ""}}


def test_guarded_utf8_atomic_rollback_manual_oracle():
    assert _solve_508({
        "files": {"a": "éx"},
        "batches": [
            {"id": "ok", "guards": {"a": digest("éx")}, "post": {"a": digest("éY")},
             "ops": [{"op": "edit", "path": "a", "spans": [
                 {"start": 2, "end": 3, "old": "x", "new": "Y"},
             ]}]},
            {"id": "bad-byte", "guards": {}, "post": {},
             "ops": [{"op": "edit", "path": "a", "spans": [
                 {"start": 1, "end": 1, "old": "", "new": "z"},
             ]}]},
            {"id": "bad-post", "guards": {}, "post": {"a": digest("")},
             "ops": [{"op": "delete", "path": "a"}]},
        ],
    }) == {
        "files": {"a": "éY"}, "hashes": {"a": digest("éY")},
        "audit": [
            {"id": "ok", "committed": True, "failed_op": None, "error": None},
            {"id": "bad-byte", "committed": False, "failed_op": 1, "error": "boundary"},
            {"id": "bad-post", "committed": False, "failed_op": 2, "error": "post"},
        ],
    }


def test_snapshot_conflict_and_rollback_manual_oracles():
    assert _solve_509(_INPUTS[509][1]) == {
        "state": {"x": 9}, "versions": {"x": 1},
        "reads": [{"event": 5, "tx": "a", "key": "x", "exists": True, "value": 1}],
        "completions": [
            {"event": 4, "tx": "b", "status": "committed", "conflicts": []},
            {"event": 7, "tx": "a", "status": "conflict", "conflicts": ["x"]},
        ],
    }
    assert _solve_509(_INPUTS[509][6])["versions"] == {"x": 1}
    assert _solve_509(_INPUTS[509][6])["state"] == {}
    rolled_back = _solve_509(_INPUTS[509][8])
    assert rolled_back["state"] == {"x": 1}
    assert rolled_back["versions"] == {"x": 0}
    assert [row["exists"] for row in rolled_back["reads"]] == [False, True]
    assert _solve_509(_INPUTS[509][3])["state"] == {"x": 0, "y": 0}


def test_retry_report_full_manual_oracle():
    def row(seq, test, attempt, status, observed, duration, revision=0):
        return {"run": "r", "shard": "s", "seq": seq, "test": test, "attempt": attempt,
                "status": status, "observed": observed, "duration_ms": duration, "revision": revision}

    assert _solve_510({
        "run": "r", "cutoff": 10, "expected": ["a", "b", "c", "a"],
        "records": [
            row(1, "a", 1, "failed", 1, 9),
            row(2, "a", 2, "passed", 2, 5),
            row(3, "a", 2, "withdrawn", 3, 0, revision=1),
            row(3, "a", 2, "passed", 4, 6, revision=2),
            row(4, "b", 1, "infra", 3, 20),
            row(5, "b", 2, "cancelled", 4, 1),
            row(6, "c", 1, "passed", 1, 7),
            row(6, "d", 1, "passed", 2, 7),
            row(7, "a", 3, "running", 11, 100),
        ],
    }) == {
        "total": 3,
        "counts": {"passed": 1, "failed": 0, "skipped": 0, "infra": 1, "running": 0, "missing": 1},
        "tests": [
            {"test": "a", "status": "passed", "attempt": 2, "duration_ms": 6},
            {"test": "b", "status": "infra", "attempt": 1, "duration_ms": 20},
            {"test": "c", "status": "missing", "attempt": None, "duration_ms": 0},
        ],
        "attempts_total": 4, "duration_ms": 36, "flaky": ["a"], "retried": ["a", "b"],
        "infra_tests": ["b"], "unexpected": ["d"], "slowest": [{"test": "a", "duration_ms": 6}],
    }


def test_exact_diagnosis_and_distinguishing_plan_manual_oracle():
    data = {
        "tests": [
            {"id": "a", "fault_cost": 1, "rerun_cost": 3, "known_good": False, "can_rerun": True},
            {"id": "b", "fault_cost": 1, "rerun_cost": 2, "known_good": False, "can_rerun": True},
            {"id": "c", "fault_cost": 1, "rerun_cost": 1, "known_good": False, "can_rerun": True},
        ],
        "shards": [{"tests": ["a", "b", "c"], "status": "failed"}],
    }
    expected = {
        "consistent": True, "fault_cost": 1, "fault_count": 1,
        "hypotheses": [["a"], ["b"], ["c"]], "certain_bad": [], "possibly_bad": ["a", "b", "c"],
        "rerun": ["b", "c"], "rerun_cost": 3, "unresolved": [],
    }
    assert _solve_511(data) == expected
    data["tests"][0]["can_rerun"] = data["tests"][1]["can_rerun"] = False
    assert _solve_511(data) == {
        **expected, "rerun": None, "rerun_cost": None, "unresolved": [[0, 1]],
    }
    # Same fault cost: one diagnosis beats a two-fault explanation.
    assert _solve_511(_INPUTS[511][6])["hypotheses"] == [["a"]]
    assert _solve_511(_INPUTS[511][3]) == {"consistent": False}
