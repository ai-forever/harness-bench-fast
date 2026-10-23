"""Independent tiny oracles and executable misconceptions for all 12 workflows."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import random
import time
from fractions import Fraction
from functools import lru_cache

import pytest

from harness_bench.general_hard import workflow_revision as revised
from harness_bench.general_hard.workflow_tasks import (
    _REVISION_ARTIFACTS,
    _REVISION_INPUTS,
    _REVISION_SOLVERS,
    TASKS,
    _couple_503,
    _solve_500,
    _solve_509,
)
from harness_bench.general_hard.workflow_tasks import (
    _V4_CASES as _CASES,
)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _retention_oracle(data):
    names = sorted(data["policy"]["candidates"])
    policy = data["policy"]
    best = None
    for count in range(len(names) + 1):
        for items in itertools.combinations(names, count):
            selected = set(items)
            if not set(policy["mandatory"]) <= selected:
                continue
            if any(name in selected and not set(needs) <= selected
                   for name, needs in policy["requires"].items()):
                continue
            if any(not row["min"] <= len(selected & set(row["members"])) <= row["max"]
                   for row in policy["groups"]):
                continue
            # Tiny inputs have independent put-only snapshots; no reference
            # materialization or bitmask union is used by this oracle.
            texts = {data["objects"][op["object"]] for snapshot in data["snapshots"]
                     if snapshot["id"] in selected for op in snapshot["ops"]}
            physical = sum(len(text.encode()) for text in texts)
            upload = sum(len(text.encode()) for text in texts if sha(text) not in data["existing"])
            if physical > policy["max_physical"] or upload > policy["max_upload"]:
                continue
            score = (-sum(policy["candidates"][name] for name in selected), upload,
                     physical, count, list(items))
            if best is None or score < best:
                best = score
    return best


def test_500_shared_storage_matches_independent_subset_oracle():
    rng = random.Random(50041)
    for _ in range(50):
        names = list("abcdef")
        objects = {str(i): chr(97 + i) * (i + 1) for i in range(5)}
        data = {
            "objects": objects,
            "snapshots": [{"id": name, "parent": None, "ops": [
                {"op": "put", "path": f"x{i}", "object": str(i)}
                for i in rng.sample(range(5), rng.randrange(1, 4))
            ]} for name in names],
            "existing": [sha(objects["0"])],
            "policy": {
                "candidates": {name: rng.randrange(1, 10) for name in names},
                "mandatory": ["a"], "requires": {"e": ["c"], "d": ["b"]},
                "groups": [{"members": ["b", "c", "d"], "min": 1, "max": 2}],
                "max_physical": rng.randrange(3, 16), "max_upload": rng.randrange(3, 16),
            },
        }
        expected = _retention_oracle(data)
        actual = revised._revision_500(data)
        if expected is None:
            assert actual == {"feasible": False}
        else:
            assert (-actual["utility"], actual["upload_bytes"], actual["physical_bytes"],
                    len(actual["retained"]), actual["retained"]) == expected
            assert [row["id"] for row in actual["snapshots"]] == actual["retained"]


def test_500_independent_snapshot_size_mutant_fails_shared_blob_choice():
    data = {"objects": {"x": "xxxx", "y": "yyyyy"},
            "snapshots": [
                {"id": name, "parent": None, "ops": [{"op": "put", "path": "x", "object": obj}]}
                for name, obj in (("a", "x"), ("b", "x"), ("c", "y"))
            ], "existing": [], "policy": {
                "candidates": {"a": 6, "b": 6, "c": 10}, "mandatory": [], "requires": {},
                "groups": [], "max_physical": 5, "max_upload": 5,
            }}
    actual = revised._revision_500(data)
    assert actual["retained"] == ["a", "b"]
    greedy = []
    remaining = 5
    for name in sorted(data["policy"]["candidates"], key=data["policy"]["candidates"].get, reverse=True):
        size = _solve_500({**data, "keep": [name]})["physical_bytes"]
        if size <= remaining:
            greedy.append(name)
            remaining -= size
    assert greedy == ["c"] != actual["retained"]


def test_501_inode_overlay_rollback_and_copy_on_write_mutant():
    data = copy.deepcopy(_REVISION_INPUTS[501][0])
    actual = revised._revision_501(data)
    assert actual["archive"]["hardlink_groups"] == [["moved/a", "moved/b"], ["same"]]
    assert actual["archive"]["logical_bytes"] == 13
    assert actual["archive"]["physical_bytes"] == 9
    assert actual["audit"][1] == {"id": "rollback", "error": "not-empty", "failed_op": 2}
    leaf = hashlib.sha256(b"F4:" + "文\n".encode()).hexdigest()
    assert actual["queries"][0] == {"resolved": "moved/a", "kind": "file", "sha256": leaf}
    # Wrong interpreter copies file bytes on link, losing shared identity.
    mutant = {"d/a": "café", "d/b": "café", "same": "café"}
    mutant["d/a"] = "文\n"
    assert sum(len(text.encode()) for text in mutant.values()) != actual["archive"]["logical_bytes"]


def test_501_repeated_symlink_is_not_necessarily_a_cycle_and_rename_preserves_literal():
    data = {"entries": [
        {"path": "d/f", "kind": "file", "text": "ok"},
        {"path": "s", "kind": "symlink", "target": "."},
        {"path": "d/up", "kind": "symlink", "target": "../d/f"},
    ], "transactions": [], "queries": [
        {"path": "s/s/s/d/f", "follow": True},
        {"path": "d/up", "follow": False},
    ]}
    actual = revised._revision_501(data)
    assert actual["queries"][0]["resolved"] == "d/f"
    assert actual["queries"][1]["kind"] == "symlink"
    data["transactions"] = [{"id": "move", "ops": [{"op": "rename", "source": "d", "path": "new"}]}]
    data["queries"] = [{"path": "new/up", "follow": True}, {"path": "new/f/../f", "follow": True}]
    assert revised._revision_501(data)["queries"] == [{"error": "missing"}, {"error": "not-dir"}]


def _transport_oracle(payments, invoices, routes, minimums):
    eligible = [row for row in routes if row["payment"] in payments
                and (payments[row["payment"]]["customer"], payments[row["payment"]]["currency"])
                == (invoices[row["invoice"]]["customer"], invoices[row["invoice"]]["currency"])]
    best, answer = None, None
    for values in itertools.product(*(range(row["cap"] + 1) for row in eligible)):
        flow = {(row["payment"], row["invoice"]): amount
                for row, amount in zip(eligible, values, strict=True)}
        by_p = {p: sum(value for (name, _), value in flow.items() if name == p) for p in payments}
        by_i = {i: sum(value for (_, name), value in flow.items() if name == i) for i in invoices}
        if any(by_p[p] > payments[p]["net"] for p in payments):
            continue
        if any(not minimums[i] <= by_i[i] <= invoices[i]["amount"] for i in invoices):
            continue
        score = (-sum(values), -sum(by_i[i] * invoices[i]["priority"] for i in invoices),
                 -sum(value for (p, i), value in flow.items() if payments[p].get("invoice") == i),
                 sum(value * row["fee"] for row, value in zip(eligible, values, strict=True)),
                 tuple(flow.get((p, i), 0) for p in sorted(payments) for i in sorted(invoices)))
        if best is None or score < best:
            best, answer = score, flow
    return answer


def test_502_constrained_transport_matches_exhaustive_integer_flows():
    rng = random.Random(50241)
    for _ in range(70):
        invoices = {name: {"amount": rng.randrange(1, 4), "priority": rng.randrange(4),
                           "customer": "c", "currency": "X"} for name in "ab"}
        payments = {name: {"net": rng.randrange(4), "invoice": rng.choice(["a", "b", None]),
                           "customer": "c", "currency": "X"} for name in "xyz"}
        routes = [{"payment": p, "invoice": i, "cap": rng.randrange(3), "fee": rng.randrange(4)}
                  for p in payments for i in invoices]
        minimums = {name: rng.randrange(row["amount"] + 1) for name, row in invoices.items()}
        assert revised._transport_502(payments, invoices, routes, minimums) == (
            _transport_oracle(payments, invoices, routes, minimums)
        )


def test_502_greedy_earmark_mutant_cannot_repair_reserved_capacity():
    payments = {p: {"net": 1, "customer": "c", "currency": "X", "invoice": "a"} for p in "xy"}
    invoices = {i: {"amount": 1, "priority": 1, "customer": "c", "currency": "X"} for i in "ab"}
    routes = [{"payment": p, "invoice": i, "cap": 1, "fee": 0}
              for p, i in (("x", "a"), ("x", "b"), ("y", "a"))]
    optimum = revised._transport_502(payments, invoices, routes, {"a": 0, "b": 0})
    greedy = {}
    available = {"a": 1, "b": 1}
    for p in payments:
        for route in routes:
            if route["payment"] == p and available[route["invoice"]]:
                greedy[p, route["invoice"]] = 1
                available[route["invoice"]] -= 1
                break
    assert sum(optimum.values()) == 2
    assert sum(greedy.values()) == 1


def _quota_oracle(exact, rows, columns, quotas):
    if sum(rows.values()) != sum(columns.values()):
        return {"feasible": False, "reason": "total"}
    for names, coordinate in ((rows, 0), (columns, 1)):
        for name, total in names.items():
            values = [value for key, value in exact.items() if key[coordinate] == name]
            if not sum(map(math.floor, values)) <= total <= sum(map(math.ceil, values)):
                return {"feasible": False, "reason": "bounds"}
    keys = sorted(exact)
    best = None
    for values in itertools.product(*(sorted({math.floor(exact[key]), math.ceil(exact[key])})
                                      for key in keys)):
        chosen = dict(zip(keys, values, strict=True))
        if any(sum(value for (r, _), value in chosen.items() if r == row) != total
               for row, total in rows.items()):
            continue
        if any(sum(value for (_, c), value in chosen.items() if c == col) != total
               for col, total in columns.items()):
            continue
        if any(not q["min"] <= sum(chosen[tuple(cell)] for cell in q["cells"]) <= q["max"]
               for q in quotas):
            continue
        errors = [abs(chosen[key] - exact[key]) for key in keys]
        score = max(errors, default=Fraction()), sum((e * e for e in errors), Fraction()), values
        if best is None or score < best:
            best = score
    if best is None:
        return {"feasible": False, "reason": "coupling"}
    return {"feasible": True, "allocation": dict(zip(keys, best[2], strict=True)),
            "maximum_error": best[0], "squared_error": best[1]}


def test_503_overlapping_signed_quotas_match_full_matrix_enumeration():
    rng = random.Random(50341)
    for case in range(90):
        exact = {(f"r{i}", f"c{j}"): Fraction(rng.randrange(-9, 11), rng.randrange(2, 9))
                 for i in range(3) for j in range(3)}
        chosen = {key: rng.choice([math.floor(value), math.ceil(value)]) for key, value in exact.items()}
        rows = {f"r{i}": sum(value for (r, _), value in chosen.items() if r == f"r{i}") for i in range(3)}
        cols = {f"c{j}": sum(value for (_, c), value in chosen.items() if c == f"c{j}") for j in range(3)}
        quotas = []
        for q in range(3):
            cells = rng.sample(list(exact), 4)
            total = sum(chosen[cell] for cell in cells) + (case % 4 == 0 and q == 0)
            quotas.append({"id": str(q), "cells": cells, "min": total, "max": total})
        assert revised._quota_round_503(exact, rows, cols, quotas) == _quota_oracle(exact, rows, cols, quotas)


def test_503_row_column_only_mutant_ignores_nonlocal_quota():
    data = _REVISION_INPUTS[503][0]
    actual = revised._revision_503(data)
    mutant = revised._revision_503({**data, "quotas": []})
    def flatten(result):
        return [cell["base_minor"] for row in result["invoices"] for cell in row["allocations"]]
    assert flatten(actual) == [1, 0, 0, 1]
    assert flatten(mutant) == [0, 1, 1, 0]


def test_503_squared_error_first_mutant_violates_bottleneck_priority():
    data = _REVISION_INPUTS[503][-1]
    _, exact = revised._native_503(data)
    correct = revised._quota_round_503(exact, data["row_totals"], data["column_totals"], [])
    wrong = _couple_503(exact, data["row_totals"], data["column_totals"])
    assert correct["maximum_error"] == Fraction(4, 5)
    assert correct["squared_error"] == Fraction(131, 50)
    assert wrong["squared_error"] == Fraction(101, 50)
    assert max(abs(wrong["allocation"][key] - value) for key, value in exact.items()) == Fraction(9, 10)


def _repair_oracle(edges, forbidden):
    best, answer = None, None
    for count in range(len(edges) + 1):
        for chosen in itertools.combinations(edges, count):
            kept = {row["id"] for row in chosen}
            if any(row["required"] and row["id"] not in kept for row in edges):
                continue
            graph = {}
            for row in chosen:
                a, b = row["left"], row["right"]
                graph.setdefault(a, set()).add(b)
                graph.setdefault(b, set()).add(a)
            invalid = False
            for row in forbidden:
                seen, pending = {row["left"]}, [row["left"]]
                while pending:
                    at = pending.pop()
                    for neighbor in graph.get(at, set()) - seen:
                        seen.add(neighbor)
                        pending.append(neighbor)
                invalid |= row["right"] in seen
            if invalid:
                continue
            removed = sorted(row["id"] for row in edges if row["id"] not in kept)
            score = -sum(row["weight"] for row in chosen), -count, removed
            if best is None or score < best:
                best, answer = score, {"weight": -score[0], "kept": sorted(kept), "removed": removed}
    return answer


def test_504_alias_repair_matches_independent_subgraph_enumeration():
    rng = random.Random(50441)
    for _ in range(75):
        edges = [{"id": f"e{i}", "tenant": "t", "left": a, "right": b,
                  "weight": rng.randrange(1, 7), "required": rng.randrange(6) == 0}
                 for i, (a, b) in enumerate(rng.sample(list(itertools.combinations("abcde", 2)), 8))]
        forbidden = [{"tenant": "t", "left": a, "right": b}
                     for a, b in rng.sample(list(itertools.combinations("abcde", 2)), 3)]
        assert revised._repair_aliases_504(edges, forbidden) == _repair_oracle(edges, forbidden)


def test_504_greedy_alias_mutant_loses_globally():
    edges = [{"id": name, "tenant": "t", "left": "a", "right": name,
              "weight": weight, "required": False} for name, weight in (("b", 9), ("c", 8), ("d", 8))]
    forbidden = [{"tenant": "t", "left": "b", "right": name} for name in "cd"]
    optimum = revised._repair_aliases_504(edges, forbidden)
    accepted = []
    for edge in sorted(edges, key=lambda row: -row["weight"]):
        candidate = [*accepted, {**edge, "required": True}]
        if _repair_oracle([{**row, "required": True} for row in candidate], forbidden):
            accepted.append(edge)
    assert optimum["kept"] == ["c", "d"]
    assert sum(row["weight"] for row in accepted) == 9 < optimum["weight"] == 16


def _point_505(records, cutoff, entity, at):
    winners = {}
    for row in records:
        if row["recorded"] <= cutoff:
            old = winners.get(row["id"])
            if old is None or (row["recorded"], row["revision"]) > (old["recorded"], old["revision"]):
                winners[row["id"]] = row
    rows = [row for row in winners.values() if not row["deleted"] and row["entity"] == entity
            and (row["start"] is None or row["start"] <= at)
            and (row["end"] is None or at < row["end"])]
    for row in sorted(rows, key=lambda r: (r["priority"], r["recorded"], r["revision"], r["id"]), reverse=True):
        if isinstance(row["value"], dict):
            target = row["value"]
            found = _point_505(records, cutoff, target["ref"], at + target["offset"])
            if found is not False:
                return found
        else:
            return row["value"]
    return False


def test_505_shifted_temporal_segments_match_independent_point_oracle():
    for data in _REVISION_INPUTS[505]:
        answer = revised._revision_505(data)
        for view in answer["views"]:
            for entity in {row["entity"] for row in data["records"]}:
                for at in range(-16, 25):
                    actual = next((row["value"] for row in view["segments"]
                                   if row["entity"] == entity
                                   and (row["start"] is None or row["start"] <= at)
                                   and (row["end"] is None or at < row["end"])), None)
                    expected = _point_505(data["records"], view["asof"], entity, at)
                    assert actual == (None if expected is False else expected)


def test_505_coalesce_parent_first_mutant_leaks_provenance():
    common = {"recorded": 1, "revision": 0, "priority": 0, "deleted": False}
    data = {"records": [
        {**common, "id": "a", "entity": "parent", "start": None, "end": 0, "value": "same"},
        {**common, "id": "b", "entity": "parent", "start": 0, "end": None, "value": "same"},
        {**common, "id": "ref", "entity": "child", "start": -2, "end": 0,
         "value": {"ref": "parent", "offset": 0}},
    ], "cutoffs": [1], "queries": []}
    output = revised._revision_505(data)["views"][0]["segments"]
    assert output[0]["sources"] == ["a", "ref"]
    parent_sources = output[1]["sources"]
    wrong_child_sources = sorted(set(parent_sources) | {"ref"})
    assert wrong_child_sources == ["a", "b", "ref"] != output[0]["sources"]


def _plan_506(name, *, reward=1, cost=0, guards=(), edits=(), requires=(), excludes=(), mandatory=False):
    return {"id": name, "reward": reward, "cost": cost, "guards": list(guards),
            "edits": list(edits), "requires": list(requires), "excludes": list(excludes),
            "mandatory": mandatory}


def test_506_global_patch_choice_matches_independent_semantic_subsets():
    rng = random.Random(50641)
    for _ in range(30):
        plans = []
        for i in range(6):
            key = rng.choice("xy")
            plans.append(_plan_506(str(i), reward=rng.randrange(1, 9), cost=rng.randrange(1, 4),
                                  guards=[{"source": "a", "section": "s", "key": key,
                                           "which": 1, "raw": json.dumps(str(i % 2))}],
                                  edits=[{"source": "a", "section": "s", "key": key,
                                          "which": 1, "value": str(1 - i % 2)}]))
        data = {"files": {"a": '[s]\nx="0"\ny="0"\n'}, "plans": plans, "budget": 7, "final": []}
        best = None
        for flags in itertools.product((False, True), repeat=6):
            state, reward, cost, chosen = {"x": "0", "y": "0"}, 0, 0, []
            for include, plan in zip(flags, plans, strict=True):
                if not include:
                    continue
                guard = plan["guards"][0]
                if state[guard["key"]] != json.loads(guard["raw"]):
                    break
                state[plan["edits"][0]["key"]] = plan["edits"][0]["value"]
                reward += plan["reward"]
                cost += plan["cost"]
                chosen.append(plan["id"])
            else:
                if cost <= 7:
                    score = -reward, cost, len(chosen), chosen
                    if best is None or score < best:
                        best = score
        actual = revised._revision_506(data)
        assert (-actual["reward"], actual["cost"], actual["replacements"], actual["chosen"]) == best


def test_506_copy_reads_preplan_not_prior_edit_and_greedy_mutant_fails():
    files = {"a": '[s]\nx="old"\ny="before"\n'}
    copy_selector = {"source": "a", "section": "s", "key": "x", "which": 1}
    plan = _plan_506("a", edits=[
        {"source": "a", "section": "s", "key": "x", "which": 1, "value": "new"},
        {"source": "a", "section": "s", "key": "y", "which": 1, "copy": copy_selector},
    ])
    actual = revised._patch_plan_506(files, plan)[0]["a"]
    assert actual == '[s]\nx="new"\ny="old"\n'
    wrong_sequential_copy = '[s]\nx="new"\ny="new"\n'
    assert actual != wrong_sequential_copy
    data = {"files": files, "budget": 2, "final": [], "plans": [
        _plan_506("a", reward=9, cost=2, excludes=["b", "c"]),
        _plan_506("b", reward=6, cost=1), _plan_506("c", reward=6, cost=1),
    ]}
    assert revised._revision_506(data)["chosen"] == ["b", "c"]


def test_507_c3_diamonds_all_parent_permutations_and_virtual_receiver():
    for order in itertools.permutations("ABC"):
        text = "[Base]\nchain=Z\nhost=base\nurl=${host}/\n"
        for name in "ABC":
            text += f"[{name}]\n@extends Base\nchain={name}${{super:chain}}\n"
        text += f"[Child]\n@extends {' '.join(order)}\nchain=X${{super:chain}}\nhost=child\n"
        answer = revised._revision_507({"files": {"main": text}, "entry": "main", "env": {}})
        assert answer["linearization"]["Child"] == ["Child", *order, "Base"]
        assert answer["values"]["Child"]["chain"] == "X" + "".join(order) + "Z"
        assert answer["values"]["Child"]["url"] == "child/"
        # Depth-first concatenation emits Base before the second sibling;
        # lexical-owner evaluation gives base/ instead of child/.
        dfs_mutant = list(dict.fromkeys(["Child", order[0], "Base", *order[1:]]))
        assert dfs_mutant != answer["linearization"]["Child"]


def test_507_inconsistent_mro_and_fallback_error_order():
    data = _REVISION_ARTIFACTS[507]
    by_id = {job["id"]: revised._revision_507({"files": data["files"], **job}) for job in data["jobs"]}
    assert by_id["inconsistent-order"] == {"error": "mro"}
    assert by_id["virtual-cycle"] == {"error": "cycle"}
    assert by_id["inheritance-cycle"] == {"error": "inheritance-cycle"}
    assert by_id["unknown"] == {"error": "unknown-parent"}
    assert by_id["prod"]["values"]["Canary"]["chain"] == "C>P>R>A>S>B"
    assert by_id["prod"]["values"]["Canary"]["endpoint"] == "eu:443"
    assert by_id["capture"]["values"]["Both"]["chain"] == "one>R>two>S>B"


def test_508_stale_rebase_matches_independent_tagged_character_editor():
    rng = random.Random(50841)
    for _ in range(35):
        original = "abcdefghijklmnop"
        tagged = [(i, char) for i, char in enumerate(original)]
        indices = rng.sample(range(len(original)), 8)
        batches = []
        for number, index in enumerate(indices):
            replacement = f"文{number}"
            batches.append({"id": str(number), "base": 0, "guards": {}, "post": {}, "ops": [
                {"op": "edit", "path": "a", "spans": [
                    {"start": index, "end": index + 1, "old": original[index], "new": replacement},
                ]},
            ]})
            position = next(k for k, (tag, _) in enumerate(tagged) if tag == index)
            tagged[position:position + 1] = [(None, char) for char in replacement]
        actual = revised._revision_508({"files": {"a": original}, "batches": batches})
        assert actual["files"]["a"] == "".join(char for _, char in tagged)
        assert all(row["committed"] for row in actual["audit"])


def test_508_aba_and_unrebased_offset_mutants_are_rejected():
    result = revised._revision_508(_REVISION_INPUTS[508][0])
    assert result["files"]["renamed"] == "éa插bcDf"
    assert result["audit"][-1]["error"] == "conflict"
    # Applying BASE offset directly to current bytes would split inserted 文/插.
    current = "éa插bcdef".encode()
    with pytest.raises(UnicodeDecodeError):
        (current[:5] + b"D" + current[7:]).decode()
    aba = revised._revision_508(_REVISION_INPUTS[508][1])
    assert aba["files"] == {"a": "x"}
    assert aba["audit"][1]["error"] == "deleted"
    collapsed = next(data for data in _REVISION_INPUTS[508]
                     if any(batch["id"] == "collapsed-anchors" for batch in data["batches"]))
    assert revised._revision_508(collapsed)["files"] == {"a": "XY"}


def test_509_serialization_cycle_matches_independent_permutation_oracle():
    rng = random.Random(50941)
    for _ in range(45):
        names = list("abcd")
        read_sets = {name: set(rng.sample(names, rng.randrange(4))) - {name} for name in names}
        events = [{"tx": name, "op": "begin"} for name in names]
        for name in names:
            events += [{"tx": name, "op": "read", "key": key} for key in sorted(read_sets[name])]
            events += [{"tx": name, "op": "set", "key": name, "value": 1}]
        events += [{"tx": name, "op": "commit"} for name in names]
        accepted, expected = [], []
        for name in names:
            candidates = [*accepted, name]
            edges = {(reader, writer) for reader in candidates for writer in candidates
                     if writer in read_sets[reader]}
            possible = any(all(order.index(a) < order.index(b) for a, b in edges)
                           for order in itertools.permutations(candidates))
            if possible:
                accepted.append(name)
            expected.append("committed" if possible else "serialization")
        actual = revised._revision_509({"state": {}, "events": events})
        assert [row["status"] for row in actual["completions"]] == expected


def test_509_snapshot_only_mutant_allows_write_skew_but_rollback_removes_predicate():
    data = _REVISION_INPUTS[509][0]
    new = revised._revision_509(data)
    assert new["completions"][-1]["status"] == "serialization"
    assert new["completions"][-1]["cycle"] == ["a", "b"]
    old_data = copy.deepcopy(data)
    # Replace predicate reads by point reads of the eventual phantom keys.
    for event in old_data["events"]:
        if event["op"] == "scan":
            event["op"], event["key"] = "read", "y" if event["tx"] == "a" else "b"
    assert all(row["status"] == "committed" for row in _solve_509(old_data)["completions"])
    assert all(row["status"] == "committed"
               for row in revised._revision_509(_REVISION_INPUTS[509][1])["completions"])


def _schedule_oracle(jobs, dependencies, workers, capacity):
    names = sorted(row["test"] for row in jobs)
    by_name = {row["test"]: row for row in jobs}
    horizon = sum(row["duration"] for row in jobs) + max((row["release"] for row in jobs), default=0)
    best = None
    for starts in itertools.product(*(range(by_name[name]["release"], horizon + 1) for name in names)):
        beginning = dict(zip(names, starts, strict=True))
        ending = {name: beginning[name] + by_name[name]["duration"] for name in names}
        if any(ending[predecessor] > beginning[name] for name in names
               for predecessor in dependencies.get(name, []) if predecessor in by_name):
            continue
        if any(by_name[name]["deadline"] is not None and ending[name] > by_name[name]["deadline"]
               for name in names):
            continue
        for assignment in itertools.product(*(by_name[name]["workers"] for name in names)):
            valid = True
            for t in range(horizon + max((row["duration"] for row in jobs), default=0) + 1):
                running = [i for i, name in enumerate(names) if beginning[name] <= t < ending[name]]
                if len({assignment[i] for i in running}) != len(running) or (
                    sum(by_name[names[i]]["resource"] for i in running) > capacity
                ):
                    valid = False
                    break
            if valid:
                score = (max(ending.values(), default=0), sum(ending.values()),
                         tuple((name, beginning[name], worker)
                               for name, worker in zip(names, assignment, strict=True)))
                if best is None or score < best:
                    best = score
    return best


def test_510_event_scheduler_matches_exhaustive_integer_start_times():
    rng = random.Random(51041)
    for case in range(18):
        jobs = [{"test": name, "duration": rng.randrange(1, 4), "release": rng.randrange(2),
                 "deadline": 4 if case % 4 == 0 else None, "resource": rng.randrange(1, 3),
                 "workers": ["w0", "w1"] if i % 2 else ["w0"]}
                for i, name in enumerate("abc")]
        dependencies = {"c": ["a"]} if case % 2 else {}
        assert revised._schedule_510(jobs, dependencies, ["w0", "w1"], 3) == (
            _schedule_oracle(jobs, dependencies, ["w0", "w1"], 3)
        )


def test_510_intentional_idle_beats_eager_start_and_self_support_is_blocked():
    jobs = [{"test": name, "duration": duration, "release": 0, "deadline": None,
             "resource": 1, "workers": workers}
            for name, duration, workers in (
                ("a", 4, ["w0", "w1"]), ("b", 1, ["w0"]), ("c", 8, ["w1"]),
            )]
    exact = revised._schedule_510(jobs, {"c": ["b"]}, ["w0", "w1"], 2)
    assert exact == (9, 15, (("a", 1, "w0"), ("b", 0, "w0"), ("c", 1, "w1")))
    eager = {"a": (0, 4, "w0"), "b": (4, 5, "w0"), "c": (5, 13, "w1")}
    assert max(value[1] for value in eager.values()) > exact[0]
    data = copy.deepcopy(_REVISION_INPUTS[510][-1])
    result = revised._revision_510(data)
    assert result["usable"] == []
    assert all(row["status"] == "missing" for row in result["tests"])


def _all_trees_511(hypotheses, probes, subset):
    if len(subset) == 1:
        return [({subset[0]: 0}, 0, {"hypothesis": subset[0]})]
    answers = []
    for probe in probes:
        if not probe["available"]:
            continue
        groups = {}
        for index in subset:
            count = len(set(hypotheses[index]) & set(probe["tests"]))
            outcome = count if probe["mode"] == "count" else count % 2 if probe["mode"] == "parity" else int(count > 0)
            groups.setdefault(outcome, []).append(index)
        if len(groups) == 1:
            continue
        options = [_all_trees_511(hypotheses, probes, groups[outcome]) for outcome in sorted(groups)]
        for children in itertools.product(*options):
            costs = {index: value + probe["cost"] for child in children for index, value in child[0].items()}
            tree = {"probe": probe["id"], "branches": [
                {"outcome": outcome, "tree": child[2]} for outcome, child in zip(sorted(groups), children, strict=True)
            ]}
            answers.append((costs, 1 + sum(child[1] for child in children), tree))
    return answers


def test_511_adaptive_tree_matches_independent_all_tree_enumeration():
    rng = random.Random(51141)
    for _ in range(16):
        hypotheses = [["a"], ["b"], ["c"], ["d"]]
        probes = [{"id": f"p{i}", "tests": rng.sample(list("abcd"), rng.randrange(1, 4)),
                   "mode": rng.choice(["any", "count", "parity"]),
                   "cost": rng.randrange(4), "available": True} for i in range(4)]
        trees = _all_trees_511(hypotheses, probes, list(range(4)))
        actual = revised._adaptive_511(hypotheses, probes)
        if not trees:
            assert actual["tree"] is None
            continue
        def score(row):
            return (max(row[0].values()), sum(row[0].values()), row[1],
                    json.dumps(row[2], sort_keys=True, separators=(",", ":")))
        expected = min(trees, key=score)
        assert (actual["worst_cost"], actual["path_cost_sum"], actual["nodes"],
                json.dumps(actual["tree"], sort_keys=True, separators=(",", ":"))) == score(expected)


def test_511_nonadaptive_rerun_mutant_pays_for_unneeded_branch():
    hypotheses = [["a"], ["b"], ["c"], ["d"]]
    probes = [
        {"id": "split", "tests": ["a", "b"], "mode": "any", "cost": 1, "available": True},
        {"id": "a", "tests": ["a"], "mode": "any", "cost": 1, "available": True},
        {"id": "c", "tests": ["c"], "mode": "any", "cost": 1, "available": True},
    ]
    result = revised._adaptive_511(hypotheses, probes)
    assert result["worst_cost"] == 2
    best_nonadaptive = None
    for count in range(4):
        for subset in itertools.combinations(probes, count):
            signatures = {tuple(int(bool(set(h) & set(p["tests"]))) for p in subset) for h in hypotheses}
            if len(signatures) == 4:
                cost = sum(probe["cost"] for probe in subset)
                best_nonadaptive = cost if best_nonadaptive is None else min(cost, best_nonadaptive)
    assert best_nonadaptive == 3 > result["worst_cost"]


def test_511_local_minimax_subtree_mutant_wastes_available_budget_slack():
    data = _REVISION_INPUTS[511][-1]
    hypotheses = [[name] for name in "abcdef"]
    probes = data["probes"]

    @lru_cache(None)
    def wrong(subset):
        if len(subset) == 1:
            return 0, 0, 0
        options = []
        for probe in probes:
            groups = {}
            for index in subset:
                outcome = len(set(hypotheses[index]) & set(probe["tests"]))
                groups.setdefault(outcome, []).append(index)
            if len(groups) == 1:
                continue
            children = [wrong(tuple(group)) for group in groups.values()]
            options.append((probe["cost"] + max(child[0] for child in children),
                            probe["cost"] * len(subset) + sum(child[1] for child in children),
                            1 + sum(child[2] for child in children)))
        return min(options)

    actual = revised._adaptive_511(hypotheses, probes)
    assert (actual["worst_cost"], actual["path_cost_sum"], actual["nodes"]) == (8, 40, 5)
    assert wrong(tuple(range(6))) == (8, 42, 5)


def test_revision_registration_nonmutation_and_reference_headroom():
    assert len(TASKS) == 12
    assert all("hardening-v4" in task.tags and task.max_timeout_seconds == 900 for task in TASKS)
    assert sum("program" in task.tags for task in TASKS) == 8
    for number, cases in _CASES.items():
        started = time.perf_counter()
        for data, expected in cases:
            before = copy.deepcopy(data)
            assert _REVISION_SOLVERS[number](data) == expected
            assert data == before
        assert time.perf_counter() - started < 12, f"reference {number} needs verifier headroom"
    for number in (500, 502, 506):
        before = copy.deepcopy(_REVISION_ARTIFACTS[number])
        assert _REVISION_SOLVERS[number](before)["feasible"]
        assert before == _REVISION_ARTIFACTS[number]
