"""Independent small oracles and semantic counterfactuals for all fourteen tasks."""

from __future__ import annotations

import copy
import inspect
import itertools
import json
import textwrap
from fractions import Fraction

import pytest

from harness_bench.general_hard import systems_v4 as s


def test_v4_inventory_preserves_contracts():
    assert [int(task.id.split("_")[1]) for task in s.TASKS] == list(range(486, 500))
    assert sum("program" not in task.tags for task in s.TASKS) == 4
    assert all("hardening-v4" in task.tags for task in s.TASKS)
    assert all(task.max_timeout_seconds == 900 for task in s.TASKS)
    for number, rows in s.CASES.items():
        assert len(rows) >= 14, number
        assert len({json.dumps(value, sort_keys=True) for value, _ in rows}) == len(rows), number


def test_retry_tiny_complete_schedule_enumeration():
    """Independent enumeration of all per-tick actions, without the memoized DP."""
    data = s.retry_case([
        s.retry_job("a", 4, deadline=3, responses=[
            {"duration": 1, "result": "retry", "delay": 0, "freeze": 2},
            {"duration": 1, "result": "ok", "delay": 0, "freeze": 0}]),
        s.retry_job("b", 7, release=1, deadline=3),
    ], horizon=3, period=2, capacity=2, slots=1)
    candidates = []
    # A symbol at each tick proposes a start; invalid proposals are discarded.
    for proposal in itertools.product((None, "a", "b"), repeat=3):
        tokens, freeze, running = 2, 0, None
        attempts = {"a": 0, "b": 0}
        ready = {"a": 0, "b": 1}
        closed = set()
        reward = completed = 0
        starts = []
        valid = True
        for time in range(4):
            if time and time % 2 == 0:
                tokens = min(2, tokens + 1)
            if running is not None and running[1] == time:
                name, _, response = running
                running = None
                if response["result"] == "ok":
                    closed.add(name)
                    completed += 1
                    reward += 4 if name == "a" else 7
                else:
                    freeze = time + 2
                    ready[name] = time
            if time == 3 or proposal[time] is None:
                continue
            name = proposal[time]
            if running or name in closed or time < ready[name] or time < freeze or not tokens:
                valid = False
                break
            job = next(j for j in data["jobs"] if j["id"] == name)
            if attempts[name] >= len(job["responses"]):
                valid = False
                break
            response = job["responses"][attempts[name]]
            if time + response["duration"] > job["deadline"]:
                valid = False
                break
            attempts[name] += 1
            tokens -= 1
            starts.append((time, name))
            running = name, time + response["duration"], response
        if valid:
            candidates.append((-reward, -completed, len(starts), tuple(starts)))
    reward, count, attempts, starts = min(candidates)
    assert s.optimal486(data) == {
        "reward": -reward, "completed": -count, "attempts": attempts,
        "starts": [{"time": time, "id": name} for time, name in starts],
    }
    assert starts == ((1, "b"),)


def test_bayesian_common_regime_and_probe_information():
    # Two mutually exclusive regimes; a perfect probe buys the only remaining
    # attempt for the profitable regime. Independent-prior policies get only5.
    jobs = [s.stochastic_job("a", 10, p=[[1, 1], [0, 1]], deadline=2),
            s.stochastic_job("b", 10, p=[[0, 1], [1, 1]], deadline=2)]
    probe = {"id": "p", "duration": 1, "energy": 0,
             "likelihood": [[[1, 1], [0, 1]], [[0, 1], [1, 1]]]}
    data = s.stochastic_case(jobs, horizon=2, energy=1, probes=[probe])
    assert s.bayesian487(data) == {"value": [10, 1], "first": "probe:p"}
    without = s.bayesian487({**data, "probes": []})
    assert without == {"value": [5, 1], "first": "job:a"}
    # Learning from a real failed attempt is just as informative as a probe.
    data = s.stochastic_case(jobs, horizon=2, energy=2)
    assert s.bayesian487(data) == {"value": [10, 1], "first": "job:a"}


def test_archive_global_alias_and_link_choice_bruteforce():
    entries = [s.archive_file("a", "x", 1, 4), s.archive_file("b", "X", 3, 2),
               s.archive_link("c", "y", "x", 8, "hard"), s.archive_file("d", "x/z", 7, 1)]
    data = {"entries": entries, "disk": 3, "required": []}
    feasible = []
    # Explicitly enumerate actual canonical namespaces; no normalizer reuse.
    for bits in itertools.product((False, True), repeat=4):
        chosen = {entry["id"] for entry, keep in zip(entries, bits, strict=True) if keep}
        if {"a", "b"} <= chosen or ("d" in chosen and chosen & {"a", "b"}):
            continue
        if "c" in chosen and not chosen & {"a", "b"}:
            continue
        disk = 4 * ("a" in chosen) + 2 * ("b" in chosen) + ("d" in chosen)
        if disk <= 3:
            weight = sum(e["weight"] for e in entries if e["id"] in chosen)
            feasible.append((-weight, -len(chosen), disk, sorted(chosen)))
    winner = min(feasible)
    answer = s.archive488(data)
    assert (-answer["weight"], -len(answer["selected"]), answer["disk"], answer["selected"]) == winner
    assert answer["selected"] == ["b", "c"]
    assert answer["resolved"][-1]["file"] == "b"


def test_migration_policy_cannot_see_world_and_cow_is_real():
    data = copy.deepcopy(s._tiny489)
    data.update(worlds=["a", "b"], max_rounds=2)
    data["actions"] = [
        {"id": "probe", "guards": {}, "cost": 1,
         "outcomes": {w: {"label": w, "writes": {}} for w in ("a", "b")}},
        *({"id": w, "guards": {"x": "a"}, "cost": 2,
           "outcomes": {v: {"label": "ok" if v == w else "fail",
                            "writes": {"x": "b"} if v == w else {}} for v in ("a", "b")}}
          for w in ("a", "b")),
    ]
    # Direct a then b has cost3 (half execute the second); probe then matching
    # action also has cost3 but expected1.5 steps beats2, so direct a wins.
    answer = s.contingent489(data)
    assert answer["cost"] == [3, 1] and answer["steps"] == [3, 2]
    assert answer["policy"]["action"] == "a"
    assert {b["label"] for b in answer["policy"]["branches"]} == {"ok", "fail"}
    # One-round oracle would wrongly spend2 by choosing world-specific a/b.
    assert s.contingent489({**data, "max_rounds": 1}) == {"feasible": False}
    assert s.contingent489({**data, "disk": 1}) == {"feasible": False}


def test_causal_closed_context_and_atomic_group_hand_oracle():
    rows = [
        s.causal_event("a", "a", 1, {}, weight=1),
        s.causal_event("b", "b", 1, {"a": 1}, weight=2),
        s.causal_event("bad", "c", 1, {"b": 1}, weight=100),
        s.causal_event("fork", "a", 1, {}, weight=4, group="pair"),
        s.causal_event("missing", "e", 2, {"e": 1}, weight=0, group="pair"),
        s.causal_event("delete", "d", 1, {}, weight=1, op="delete", value=None),
    ]
    result = s.causal490(s.causal_case(rows))
    assert result["selected"] == ["a", "b", "delete"]
    assert result["weight"] == 4
    assert result["documents"] == [
        {"key": "doc", "frontier": ["b", "delete"], "winner": "delete", "deleted": True, "value": None},
    ]
    assert s.causal490(s.causal_case(list(reversed(rows)))) == result


def test_serializable_subset_exhaustive_orders_and_predicate_phantom():
    txs = [
        s.transaction("a", [s.get("x", 0), {"op": "put", "key": "y", "value": 1}], 4),
        s.transaction("b", [s.get("y", 0), {"op": "put", "key": "x", "value": 1}], 5),
        s.transaction("c", [{"op": "scan", "low": "p", "high": "r", "seen": {}},
                             {"op": "put", "key": "q", "value": 1}], 3),
        s.transaction("d", [s.get("q", 1), {"op": "put", "key": "z", "value": 2}], 2),
    ]
    initial = {"x": 0, "y": 0}
    candidates = []
    for length in range(5):
        for order in itertools.permutations(txs, length):
            state = dict(initial)
            valid = True
            for tx in order:
                for op in tx["ops"]:
                    if op["op"] == "get":
                        valid &= (op["key"] in state) == op["seen"]["exists"] and state.get(op["key"]) == op["seen"]["value"]
                    elif op["op"] == "scan":
                        valid &= {k: v for k, v in state.items() if op["low"] <= k < op["high"]} == op["seen"]
                    else:
                        state[op["key"]] = op["value"]
            if valid:
                candidates.append((-sum(t["reward"] for t in order), -length,
                                   tuple(sorted(t["id"] for t in order)), tuple(t["id"] for t in order), state))
    wanted = min(candidates, key=lambda c: c[:4])
    answer = s.serial491({"initial": initial, "transactions": txs, "required": []})
    assert answer == {"feasible": True, "reward": -wanted[0], "selected": list(wanted[2]),
                      "order": list(wanted[3]), "data": wanted[4]}
    assert answer["selected"] == ["b", "c", "d"]


def test_dependency_nonlocal_reachability_and_atomic_bundles():
    value = s.dependency_case([
        s.bundle("a", [["a", "b"], ["d", "e"]], cost=5),
        s.bundle("b", [["b", "c"]], cost=2),
        s.bundle("c", [["c", "a"]], cost=1),
        s.bundle("d", [["a", "c"]], cost=3, excludes=["a"]),
    ], must_reach=[["a", "c"]], must_not_reach=[["e", "a"]],
       quotas=[{"ids": ["a", "d"], "min": 1, "max": 1}])
    answer = s.dependency492(value)
    assert answer["cost"] == 4 and answer["drop"] == ["c", "d"]
    assert ["a", "c"] in answer["reach"] and ["d", "e"] in answer["reach"]
    assert answer["order"] == list("abcde")


def test_schedule_buffer_last_consumer_exhaustive_tiny():
    data = {"capacity": [[2, 2]] * 4, "storage": [1, 1, 1, 1], "tariff": [3, 2, 1, 1],
            "jobs": [
                s.schedule_job("a", deadline=4, bytes=1, modes=[
                    {"id": "fast", "duration": 1, "demand": [1, 1], "power": 2},
                    {"id": "slow", "duration": 2, "demand": [1, 1], "power": 1}]),
                s.schedule_job("b", ["a"], deadline=4, bytes=0, modes=[
                    {"id": "one", "duration": 1, "demand": [1, 1], "power": 1}]),
                s.schedule_job("c", ["a"], release=2, deadline=4, bytes=0, modes=[
                    {"id": "one", "duration": 1, "demand": [1, 1], "power": 1}]),
            ]}
    candidates = []
    for mode in data["jobs"][0]["modes"]:
        for starts in itertools.product(range(4), repeat=3):
            ends = [starts[0] + mode["duration"], starts[1] + 1, starts[2] + 1]
            if max(ends) > 4 or starts[2] < 2 or min(starts[1:]) < ends[0]:
                continue
            occupancy = [sum(start <= t < end for start, end in zip(starts, ends, strict=True)) for t in range(4)]
            if max(occupancy) > 2:
                continue
            store = [int(ends[0] <= t < max(starts[1:])) for t in range(4)]
            energy = sum(
                sum(data["tariff"][t] for t in range(start, end)) * (mode["power"] if i == 0 else 1)
                for i, (start, end) in enumerate(zip(starts, ends, strict=True))
            )
            signature = tuple((start, mode["id"] if i == 0 else "one") for i, start in enumerate(starts))
            candidates.append((max(ends), energy, max(store), signature))
    answer = s.schedule493(data)
    assert (answer["makespan"], answer["energy"], answer["storage_peak"],
            tuple((row["start"], row["mode"]) for row in answer["schedule"])) == min(candidates)


def test_configuration_global_assignment_closed_form_and_lazy_provenance():
    data = {
        "variables": {"a": [{"value": 0, "cost": 0}, {"value": 1, "cost": 3}],
                      "b": [{"value": 0, "cost": 0}, {"value": 1, "cost": 1}]},
        "baseline": {"a": 0, "b": 0},
        "layers": [{"id": "L", "nodes": {
            "bad": s.reference("bad"),
            "x": {"op": "if", "cond": s.expression("eq", s.variable("a"), s.literal(1)),
                  "then": s.variable("b"), "else": s.reference("bad")},
        }}],
        "outputs": ["x"],
        "assertions": [s.expression("eq", s.reference("x"), s.literal(1))],
    }
    assert s.configuration494(data) == {
        "feasible": True, "cost": 4, "changed": 2, "choices": {"a": 1, "b": 1},
        "resolved": {"x": 1}, "provenance": {"x": ["L", "var:a", "var:b"]},
    }
    zero = s.configuration494(s.EXTRA494[-1])
    assert zero["resolved"] == {"x": True, "y": False}


def test_profiles_global_repair_includes_unqueried_cycle_and_typed_noop():
    data = s.profile_case(1)
    answer = s.profiles495(data)
    # All six diamonds choices can be checked by C3 manually: one cross edge
    # must disappear, and the disconnected two-cycle needs a separate deletion.
    assert len(answer["drop"]) == 2
    assert len(set(answer["drop"]) & {"AY", "BX"}) == 1
    assert len(set(answer["drop"]) & {"U1", "U2"}) == 1
    for row in answer["results"]:
        assert len(row["mro"]) == len(set(row["mro"]))
    typed = copy.deepcopy(s._tiny495)
    typed["profiles"]["A"]["ops"] = [
        {"op": "set", "key": "x", "value": [-0.0, 1, True]},
        {"op": "append", "key": "x", "values": [0.0, 1.0]},
    ]
    typed["queries"][0]["expect"] = {"x": [0.0, 1, True, 1.0]}
    assert s.profiles495(typed)["feasible"]


def test_merge_names_references_and_order_joint_oracle():
    base = {"a": {"name": "A", "refs": [], "n": 0},
            "b": {"name": "B", "refs": [], "n": 0}}
    ours = {"a": {"name": "X", "refs": ["b"], "n": 1},
            "b": {"name": "Y", "refs": [], "n": 1}}
    theirs = {"a": {"name": "Y", "refs": [], "n": 2},
              "b": {"name": "X", "refs": [], "n": 2}}
    data = s.merge_case(base, ours, theirs, weights={"ours": 3, "theirs": 1},
                        required=["a", "b"],
                        order=[{"before": "a", "after": "b", "hard": False, "weight": 2},
                               {"before": "b", "after": "a", "hard": False, "weight": 5}])
    answer = s.merge496(data)
    # Four scalar conflicts (two names, two n), ours costs1 each; unchanged
    # theirs.refs permits independent ours.refs. Ordering b,a loses2 not5.
    assert answer["loss"] == 6
    assert [row["id"] for row in answer["records"]] == ["b", "a"]
    assert {row["value"]["name"] for row in answer["records"]} == {"X", "Y"}
    assert next(row["value"] for row in answer["records"] if row["id"] == "a")["refs"] == ["b"]


def test_patch_shared_capture_forces_non_nearest_placement():
    data = {"files": {"a": ["red", "blue"], "b": ["blue"]}, "distances": [],
            "hunks": [
                s.hunk("a", [{"capture": "color"}], ["A"], expected=0, weight=5, requires=["b"]),
                s.hunk("b", [{"capture": "color"}], [{"use": "color"}, "B"], file="b", weight=5),
            ]}
    assert s.patch497(data) == {
        "weight": 10, "displacement": 1, "files": {"a": ["red", "A"], "b": ["blue", "B"]},
        "applied": [{"id": "a", "position": 1}, {"id": "b", "position": 0}],
        "rejected": [], "bindings": {"color": "blue"},
    }


def test_binding_capture_cost_beats_greedy_declaration_rename():
    data = {
        "scopes": {"root": None, "child": "root"},
        "bindings": [
            s.binding("global", "root", "G", "old", ["new", "other"], 1),
            s.binding("local", "child", "L", "new", ["new", "local"], 7),
        ],
        "references": [s.reference_site("r", "child", "old", ["new", "other"], 1)],
        "public": [], "exports": [], "module_map": {},
    }
    assert s.binding498(data) == {
        "feasible": True, "cost": 2, "changed": 2,
        "bindings": [{"id": "global", "name": "other"}, {"id": "local", "name": "new"}],
        "references": [{"id": "r", "name": "other"}], "exports": {},
    }
    unchanged = s.binding498(s.ZERO498)
    assert unchanged["cost"] == 0 and unchanged["changed"] == 0
    assert unchanged["references"] == [{"id": "r", "name": "z"}]


def test_rename_cross_device_cow_and_waiting_independent_path_search():
    data = copy.deepcopy(s._tiny499)
    data["locations"]["b"]["device"] = "other"
    data["capacity"] = {"d": 1, "other": 1}
    data["network"], data["closed"] = [0, 1], [[], []]
    answer = s.rename499(data)
    assert answer == {"feasible": True, "rounds": 2, "transfer": 1, "scratch_peak": 0,
                      "plan": [[], [{"from": "a", "to": "b"}]], "final": {"b": "x"}}
    data["capacity"]["other"] = 0
    assert s.rename499(data) == {"feasible": False}
    # Occupied destinations at round start forbid an atomic no-temp swap.
    data = s.rename_case(0, count=2)
    data["locations"] = {k: v for k, v in data["locations"].items() if not v["scratch"]}
    data["edges"] = [[a, b] for a in data["locations"] for b in data["locations"] if a != b]
    assert s.rename499(data) == {"feasible": False}


def test_rename_future_peak_erases_early_peak_and_keeps_lex_prefix():
    answer = s.rename499(s.PARETO499)
    assert answer["rounds"] == 5 and answer["scratch_peak"] == 9
    assert answer["plan"] == [
        [{"from": "a", "to": "s"}], [{"from": "b", "to": "y"}],
        [{"from": "c", "to": "b"}], [{"from": "b", "to": "a"}], [{"from": "s", "to": "c"}],
    ]
    assert s.rename499(s.SCRATCH499)["rounds"] == 3
    assert s.rename499(s.COW499) == {"feasible": False}


MUTANTS = [
    (486, 'blocked = max(blocked, t + response.get("freeze", 0))', "blocked = blocked"),
    (487, "updated = tuple(x / probability for x in weights)", "updated = belief"),
    (488, "if len(set(names)) != len(names):", "if False:"),
    (489, 'if sum(sizes[b] for b in tree.values()) + allocated > data["disk"]:', "if False:"),
    (490, 'if any(ctx.get(key, 0) < value for key, value in dependency["context"].items()):', "if False:"),
    (491, "work, bad = dict(saves[index][1]), list(saves[index][2])", "work = dict(saves[index][1])"),
    (492, 'if any(not (reach[index[a]] >> index[b] & 1) for a, b in data["must_reach"]):',
     'if any([a, b] not in edges for a, b in data["must_reach"]):'),
    (493, "last = max(consumers, default=end)", "last = min(consumers, default=end)"),
    (494, 'value, sources = evaluate(expr["then"] if condition else expr["else"])',
     'evaluate(expr["then"])\n                evaluate(expr["else"])\n'
     '                value, sources = evaluate(expr["then"] if condition else expr["else"])'),
    (495, "for name in sorted(profiles):", "for name in []:"),
    (496, 'if len({r["name"] for r in records.values()}) != len(records):', "if False:"),
    (497, "if any(k in captures and captures[k] != v for k, v in values.items()):", "if False:"),
    (498, 'data["module_map"].get(export["module"], export["module"]) + "." + local',
     'data["module_map"].get(data["module_map"].get(export["module"], export["module"]), '
     'data["module_map"].get(export["module"], export["module"])) + "." + local'),
    (499, 'scratch_peak = scratch(state) + sum(sizes[objects[i]] for i, _, p in group if loc[p]["scratch"])',
     "scratch_peak = scratch(new)"),
]


@pytest.mark.parametrize("number,old,new", MUTANTS, ids=[str(row[0]) for row in MUTANTS])
def test_each_new_mechanism_kills_wrong_algorithm(number, old, new):
    source = textwrap.dedent(inspect.getsource(s.SOLVERS[number]))
    assert old in source
    namespace = {}
    exec(compile(source.replace(old, new), f"mutant_{number}", "exec"), namespace)
    mutant = namespace[s.SOLVERS[number].__name__]
    rows = s.CASES[number] if number in s.CASES else [
        (data, s.SOLVERS[number](data)) for data in s.ARTIFACT_DATA[number]
    ]
    assert any(mutant(copy.deepcopy(data)) != expected for data, expected in rows), number


def test_all_program_suites_finish_and_advertise_exact_bounds():
    # Bound validity, not a brittle wall-clock assertion: source copies are
    # exercised in their actual isolated 25-second verifier by the gold tests.
    assert all(len(data["jobs"]) <= 5 for data, _ in s.CASES[486])
    assert all(len(data["entries"]) <= 16 for data, _ in s.CASES[488])
    assert all(len(data["transactions"]) <= 9 for data, _ in s.CASES[491])
    assert all(len(data["bundles"]) <= 16 for data, _ in s.CASES[492])
    assert all(len(data["variables"]) <= 8 for data, _ in s.CASES[494])
    for data, _ in s.CASES[487]:
        assert sum(Fraction(*p) for p in data["prior"]) == 1
        for probe in data["probes"]:
            assert all(sum(Fraction(*p) for p in row) == 1 for row in probe["likelihood"])
