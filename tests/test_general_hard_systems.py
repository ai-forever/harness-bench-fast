"""Independent oracle anchors and acceptance checks for systems wave 20."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from harness_bench.general_hard import systems_tasks as s


@pytest.mark.parametrize("task", s.TASKS, ids=lambda task: task.id)
def test_systems_gold(task, tmp_path):
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    result = task.verify(tmp_path)
    assert result.passed, result.message


@pytest.mark.parametrize("task", s.TASKS, ids=lambda task: task.id)
def test_systems_blank_rejected(task, tmp_path):
    task.setup(tmp_path)
    assert not task.verify(tmp_path).passed


@pytest.mark.parametrize("task", s.TASKS, ids=lambda task: task.id)
def test_systems_wrong_answer_rejected(task, tmp_path):
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    if "program" in task.tags:
        (tmp_path / "solution.py").write_text(
            "import sys\nfor line in sys.stdin:\n    print('null')\n", encoding="utf-8"
        )
    else:
        (tmp_path / "answer.json").write_text('{"incorrect":true}\n', encoding="utf-8")
    assert not task.verify(tmp_path).passed


@pytest.mark.parametrize("task", s.TASKS, ids=lambda task: task.id)
def test_systems_fixture_mutation_rejected(task, tmp_path):
    task.setup(tmp_path)
    task.apply_gold(tmp_path)
    source = next(name for name in task.setup_files if name != "solution.py")
    with (tmp_path / source).open("a", encoding="utf-8") as file:
        file.write("\n")
    result = task.verify(tmp_path)
    assert not result.passed
    assert "source fixture changed" in result.message


@pytest.mark.parametrize("number", sorted(s.ANCHORS))
def test_systems_manual_anchors(number):
    for value, expected in s.ANCHORS[number]:
        assert s.SOLVERS[number](value) == expected


def test_systems_inventory_and_hidden_coverage():
    assert [int(task.id.split("_")[1]) for task in s.TASKS] == list(range(486, 500))
    assert len(s.TASKS) == 14
    assert sum("program" not in task.tags for task in s.TASKS) == 4
    for number, cases in s.CASES.items():
        assert len(cases) >= 12, number
        task = next(task for task in s.TASKS if task.id.startswith(f"task_{number}_"))
        public = json.loads(task.setup_files["examples.json"])
        assert len(public) == 2
        assert "harness_bench" not in task.gold_files["solution.py"]
        assert task.max_timeout_seconds == 900
        assert len({json.dumps(value, sort_keys=True) for value, _ in cases}) == len(cases)


def test_retry_completion_deadline_and_shared_freeze_anchor():
    # Both completions happen before expiration/dispatch at t=1.
    value = s._retry([
        s._job("a", [{"duration": 1, "result": "retry", "after": 3, "global": True}]),
        s._job("b", [{"duration": 1, "result": "ok"}], deadline=1),
        s._job("c", [{"duration": 1, "result": "ok"}], release=1, deadline=4),
    ], period=1)
    assert s.solve486(value) == {
        "log": [
            {"id": "a", "attempt": 1, "start": 0},
            {"id": "b", "attempt": 1, "start": 0},
            {"id": "a", "attempt": 1, "end": 1, "result": "retry"},
            {"id": "b", "attempt": 1, "end": 1, "result": "ok"},
        ],
        "jobs": [
            {"id": "a", "state": "exhausted", "attempts": 1, "end": 1},
            {"id": "b", "state": "ok", "attempts": 1, "end": 1},
            {"id": "c", "state": "deadline", "attempts": 0, "end": 4},
        ],
    }


def test_policy_adaptation_changes_expected_reward():
    # A first: success gets 8 then certain B gets 3; failure gets B only.
    # B first leaves A's deadline unreachable. E = .5*11+.5*3 = 7.
    data = {"horizon": 3, "jobs": [
        s._pj("a", reward=8, deadline=2,
              attempts=[{"duration": 2, "delay": 0, "p": [1, 2]}]),
        s._pj("b", reward=3, deadline=3,
              attempts=[{"duration": 1, "delay": 0, "p": [1, 1]}]),
    ]}
    assert s.solve487(data) == {"value": [7, 1], "first": "a"}


def test_archive_late_link_and_unsafe_priority():
    data = {"entries": [
        {"path": "a", "type": "file", "size": 1},
        {"path": "A", "type": "sym", "target": "../outside"},
        {"path": "x", "type": "hard", "target": "y"},
        {"path": "y", "type": "sym", "target": "a"},
    ]}
    assert s.solve488(data) == {
        "entries": [
            {"path": "a", "type": "file", "size": 1},
            {"path": "x", "type": "hard", "target": "y", "resolved": "a", "error": None},
            {"path": "y", "type": "sym", "target": "a", "resolved": "a", "error": None},
        ],
        "errors": [{"index": 2, "error": "unsafe"}],
    }


@pytest.mark.parametrize("tree,protected,op", [
    ({"a/secret": "x"}, ["a/secret"], {"op": "move", "path": "a/secret", "to": "b"}),
    ({"a": "x"}, ["safe"], {"op": "move", "path": "a", "to": "safe/x"}),
    ({"incoming/reserved": "x"}, ["target/reserved"],
     {"op": "move", "path": "incoming/reserved", "to": "target/reserved"}),
])
def test_move_protection_checks_descendants_and_destinations(tree, protected, op):
    action = s._action489("move", {}, [op])
    assert s._apply489(tree, action, protected, {"x": 1}) is None


def _planner489(initial, target, sizes, actions, *, workers=1, network=20, disk=20, rounds=4):
    return {
        "initial": initial, "target": target, "sizes": sizes, "protected": [], "actions": actions,
        "scenarios": [{"id": "test", "workers": workers, "network": network,
                       "disk": disk, "max_rounds": rounds}],
    }


def test_migration_parallel_read_write_conflicts_force_reader_first():
    value = _planner489(
        {"x": "old"}, {"x": "new", "y": "seen"}, {"old": 1, "new": 1, "seen": 1},
        [
            s._action489("a-writer", {"x": "old"},
                         [s._delete489("x"), s._put489("x", "new")], transfer=1),
            s._action489("b-reader", {"x": "old", "y": None},
                         [s._put489("y", "seen")], transfer=1),
        ], workers=2,
    )
    assert s.solve489(value) == {"scenarios": [{
        "id": "test", "rounds": 2, "transfer": 2, "peak": 3,
        "plan": [["b-reader"], ["a-writer"]],
    }]}


def test_migration_cannot_credit_deletions_before_round_commit(monkeypatch):
    value = _planner489(
        {"garbage": "old6", "live": "old4"}, {"live": "new6"},
        {"old6": 6, "old4": 4, "new6": 6},
        [
            s._action489("cleanup", {"garbage": "old6"}, [s._delete489("garbage")]),
            s._action489("replace", {"live": "old4"},
                         [s._delete489("live"), s._put489("live", "new6")], transfer=6),
        ], workers=2, disk=10,
    )
    expected = {"scenarios": [{
        "id": "test", "rounds": 2, "transfer": 6, "peak": 10,
        "plan": [["cleanup"], ["replace"]],
    }]}
    assert s.solve489(value) == expected
    original = s._apply489

    def no_staging(*args):
        prepared = original(*args)
        return (*prepared[:3], 0) if prepared else None

    monkeypatch.setattr(s, "_apply489", no_staging)
    # Mutation: ignoring copy-on-write reservations wrongly permits one round.
    assert s.solve489(value)["scenarios"][0]["rounds"] == 1


def test_migration_peak_pareto_frontier_preserves_future_lex_tie():
    value = _planner489(
        {"garbage": "g"}, {"small": "s", "large": "l"}, {"g": 5, "s": 1, "l": 8},
        [
            s._action489("a-create", {"small": None}, [s._put489("small", "s")]),
            s._action489("b-delete", {"garbage": "g"}, [s._delete489("garbage")]),
            s._action489("c-final", {"small": "s", "garbage": None},
                         [s._put489("large", "l")]),
        ], disk=10,
    )
    # b,a has a lower past peak (5 versus 6), but future peak9 erases the
    # advantage; the lexicographically earlier a,b,c is the required optimum.
    assert s.solve489(value) == {"scenarios": [{
        "id": "test", "rounds": 3, "transfer": 0, "peak": 9,
        "plan": [["a-create"], ["b-delete"], ["c-final"]],
    }]}


def test_migration_copy_allocations_survive_internal_delete_and_rollback():
    tree = {"source": "x", "vault/key": "x"}
    action = s._action489("temporary-copy", {}, [
        {"op": "copy", "path": "source", "to": "temporary"},
        s._delete489("temporary"),
    ])
    patch, reads, writes, staging = s._apply489(tree, action, ["vault"], {"x": 7})
    assert patch == {"temporary": None}
    assert reads == {"source", "temporary"} and writes == {"temporary"}
    assert staging == 7
    bad = s._action489("rollback", {}, [
        s._delete489("source"), s._delete489("vault/key"),
    ])
    assert s._apply489(tree, bad, ["vault"], {"x": 7}) is None
    assert tree == {"source": "x", "vault/key": "x"}


def test_migration_round_count_precedes_transfer_cost():
    value = _planner489(
        {}, {"x": "x", "y": "y"}, {"x": 1, "y": 1},
        [
            s._action489("bundle", {}, [s._put489("x", "x"), s._put489("y", "y")], transfer=9),
            s._action489("free-x", {}, [s._put489("x", "x")]),
            s._action489("free-y", {"x": "x"}, [s._put489("y", "y")]),
        ],
    )
    assert s.solve489(value) == {"scenarios": [{
        "id": "test", "rounds": 1, "transfer": 9, "peak": 2, "plan": [["bundle"]],
    }]}


def test_migration_four_scenarios_hand_computed_metrics():
    answers = s.solve489(s._FS_DATA)["scenarios"]
    assert [(a["id"], a["rounds"], a["transfer"], a["peak"]) for a in answers] == [
        ("standard", 4, 19, 25), ("tight-disk", 5, 19, 23),
        ("narrow-link", 6, 19, 31), ("wide-link", 2, 18, 33),
    ]


def test_migration_plans_independent_replay():
    """Replay declared plans without calling the planner's transaction helper."""
    data = s._FS_DATA
    sizes = data["sizes"]
    actions = {a["id"]: a for a in data["actions"]}
    for limits, result in zip(data["scenarios"], s.solve489(data)["scenarios"], strict=True):
        state = dict(data["initial"])
        used_ids = set()
        total_transfer = 0
        peak = sum(sizes[blob] for blob in state.values())
        for group in result["plan"]:
            assert group and group == sorted(group)
            assert not used_ids.intersection(group)
            used_ids.update(group)
            prepared = []
            staging = 0
            for name in group:
                action = actions[name]
                assert all(state.get(path) == blob for path, blob in action["guards"].items())
                trial = dict(state)
                reads, writes = set(action["guards"]), set()
                for op in action["ops"]:
                    path = op["path"]
                    reads.add(path)
                    kind = op["op"]
                    if kind == "put":
                        assert path not in trial
                        trial[path] = op["blob"]
                        staging += sizes[op["blob"]]
                        changed = {path}
                    elif kind == "delete":
                        assert path in trial
                        del trial[path]
                        changed = {path}
                    else:
                        destination = op["to"]
                        reads.add(destination)
                        assert path in trial and destination not in trial
                        trial[destination] = trial[path]
                        if kind == "move":
                            del trial[path]
                            changed = {path, destination}
                        else:
                            staging += sizes[trial[path]]
                            changed = {destination}
                    assert not any(
                        p == protected or p.startswith(protected + "/")
                        for p in changed for protected in data["protected"]
                    )
                    assert not any(q.startswith(p + "/") for p in trial for q in trial)
                    writes.update(changed)
                for _, earlier_reads, earlier_writes in prepared:
                    assert not writes & (earlier_reads | earlier_writes)
                    assert not earlier_writes & reads
                prepared.append((trial, reads, writes))
            assert sum(actions[name]["workers"] for name in group) <= limits["workers"]
            transfer = sum(actions[name]["transfer"] for name in group)
            assert transfer <= limits["network"]
            total_transfer += transfer
            round_peak = sum(sizes[blob] for blob in state.values()) + staging
            for trial, _, writes in prepared:
                for path in writes:
                    if path in trial:
                        state[path] = trial[path]
                    else:
                        state.pop(path, None)
            assert not any(q.startswith(p + "/") for p in state for q in state)
            round_peak = max(round_peak, sum(sizes[blob] for blob in state.values()))
            assert round_peak <= limits["disk"]
            peak = max(peak, round_peak)
        assert state == data["target"]
        assert len(result["plan"]) == result["rounds"] <= limits["max_rounds"]
        assert total_transfer == result["transfer"]
        assert peak == result["peak"]


def test_causal_frontier_ignores_arrival_order_and_resurrects():
    rows = [
        s._event("delete", clock={"a": 1}, op="delete"),
        s._event("concurrent", clock={"b": 1}, source="b", value=4),
        s._event("new", clock={"a": 2, "b": 1}, value=5),
        s._event("old", clock={"a": 1}, value=0),
    ]
    expected = {"documents": [{"id": 1, "frontier": ["new"], "conflict": False,
                              "winner": "new", "deleted": False, "value": 5}], "errors": []}
    assert s.solve490(s._cdc(rows)) == expected
    assert s.solve490(s._cdc(list(reversed(rows)))) == expected


def test_journal_rollback_restores_read_set_and_keeps_savepoint():
    value = s._wal([
        s._op("begin"), s._op("save", name="s"), s._op("get", key="x"),
        s._op("begin", "b"), s._op("delete", "b", key="x"), s._op("commit", "b"),
        s._op("rollback", name="s"), s._op("rollback", name="s"), s._op("commit"),
    ])
    assert s.solve491(value) == {
        "data": {}, "revision": 1, "active": [], "log": [
            {"line": 1, "status": "ok"}, {"line": 2, "status": "ok"},
            {"line": 3, "status": "ok", "exists": False, "value": None},
            {"line": 4, "status": "ok"}, {"line": 5, "status": "ok"},
            {"line": 6, "status": "ok", "revision": 1},
            {"line": 7, "status": "ok"}, {"line": 8, "status": "ok"},
            {"line": 9, "status": "ok", "revision": 1},
        ],
    }


def test_dependency_repair_cost_count_and_cycle_anchor():
    data = s._dep({"a": [], "b": [], "c": []},
                  [("a", "b", 1), ("b", "a", 1), ("b", "c", 0)])
    assert s.solve492(data) == {"error": None, "drop": [["a", "b"]], "cost": 1,
                               "order": ["a", "c", "b"]}
    assert s.solve492(s._dep({"a": ["b"], "b": ["a"], "c": ["c"]})) == {
        "error": "required_cycle", "cycle": ["c", "c"],
    }


def test_calendar_exact_lag_and_energy_secondary_anchor():
    # a must start at0 to allow its exact successor b at1, before capacity0.
    data = {"capacity": [2, 2, 0, 2], "tariff": [7, 3, 1, 1], "jobs": [
        s._sched_job("a", 1, 1, deadline=4),
        s._sched_job("b", 1, 1, [("a", 0, True)], deadline=4),
        s._sched_job("c", 1, 1, deadline=4),
    ]}
    assert s.solve493(data) == {
        "feasible": True, "makespan": 2, "cost": 13, "peak": 2,
        "schedule": [{"id": "a", "start": 0, "end": 1},
                     {"id": "b", "start": 1, "end": 2}, {"id": "c", "start": 1, "end": 2}],
    }


def test_config_transitive_object_leaf_provenance_anchor():
    value = s._cfg({"base": {"x": 1, "secret": {"SECRET_X": "bad"}}, "copy": "${/base}"},
                   {"base": {"x": "${ENV:X}", "y": True}}, env={"X": "v"})
    assert s.solve494(value) == {
        "error": None,
        "resolved": {"base": {"x": "v", "y": True, "secret": {}},
                     "copy": {"x": "v", "y": True, "secret": {}}},
        "provenance": {
            "/base/secret": ["L0"], "/base/x": ["L1", "env:X"], "/base/y": ["L1"],
            "/copy/secret": ["L0"], "/copy/x": ["L0", "L1", "env:X"], "/copy/y": ["L0", "L1"],
        },
    }
    assert s.solve494(s._cfg({"x": 1e-7, "y": "${/x}ms"}))["resolved"]["y"] == "1e-07ms"


def test_c3_list_noop_does_not_steal_writer():
    data = {"profiles": {
        "base": s._profile((), s._set("x", [1, True, 1.0])),
        "child": s._profile(("base",), {"op": "append", "key": "x", "values": [True, 1]})
    }, "queries": [{"id": "q", "profile": "child"}]}
    assert s.solve495(data) == {"results": [{
        "id": "q", "status": "ok", "mro": ["child", "base"],
        "config": {"x": [1, True, 1.0]}, "writers": {"x": "base"},
    }]}


def test_three_way_order_conflict_does_not_lose_value_merge():
    base = {"x": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    ours = {"x": [{"id": "b", "v": 2}, {"id": "a", "v": 1}]}
    theirs = {"x": [{"id": "a", "v": 3}, {"id": "b", "v": 2}]}
    assert s.solve496(s._merge(base, ours, theirs)) == {
        "merged": {"x": [{"id": "a", "v": 3}, {"id": "b", "v": 2}]},
        "conflicts": [{"path": "/x/$order", "kind": "order", "resolution": "unresolved"}],
    }


def test_patch_global_count_outranks_displacement():
    data = {"lines": ["a", "b"], "hunks": [
        s._hunk("a", ["a", "b"], ["both"], weight=2),
        s._hunk("b", ["a"], ["A"], expected=10),
        s._hunk("c", ["b"], ["B"], expected=10),
    ]}
    assert s.solve497(data) == {
        "lines": ["A", "B"], "applied": [{"id": "b", "position": 0}, {"id": "c", "position": 1}],
        "rejected": ["a"], "weight": 2, "displacement": 19,
    }


def test_migrated_project_runs_and_shadowed_names_stay(tmp_path):
    for name, content in s._MIGRATION_FINAL.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if name.endswith(".py"):
            compile(content, name, "exec")
    script = (
        "import sys,types\n"
        f"sys.path[:0]=[{str(tmp_path)!r},{str(tmp_path / 'src')!r}]\n"
        "sys.modules['blueledger_extra']=types.ModuleType('blueledger_extra')\n"
        "import app,plugin\n"
        "assert app.global_use()==7 and app.value==7\n"
        "assert plugin.A.total([2,3])==5 and plugin.B.run()==7\n"
        "assert plugin.use(lambda x:x)=='blueledger.api'\n"
        "assert app.shadow(types.SimpleNamespace(api='unchanged'))=='unchanged'\n"
        "assert app.label=='blueledger.api'\n"
    )
    run = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr
    for name in s._MIGRATION_RULES["exclude"]:
        assert s._MIGRATION_FINAL[name] == s._MIGRATION_FILES[name]


def test_rename_zero_bytes_still_minimizes_operations():
    data = s._rename({"a": 0, "b": 0}, {"a": "b", "b": "c"})
    assert s.solve499(data) == {
        "error": None, "temp_bytes": 0,
        "steps": [{"from": "b", "to": "c"}, {"from": "a", "to": "b"}],
        "final": {"b": "a", "c": "b"},
    }


def test_every_rename_plan_preserves_identity_and_accounts_spills():
    for data, answer in s.LEGACY_CASES[499]:
        if answer["error"] is not None:
            continue
        state = {name: name for name in data["files"]}
        cost = 0
        for step in answer["steps"]:
            source, target = step["from"], step["to"]
            assert source in state and target not in state
            identity = state.pop(source)
            state[target] = identity
            if target == data["temp"]:
                cost += data["files"][identity]
        assert state == answer["final"]
        assert cost == answer["temp_bytes"]
