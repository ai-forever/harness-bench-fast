"""Public CLI, final invariants, and counterfactual stale-view witnesses."""

import copy
import json

import pytest

from harness_bench.feasibility_tasks.trusted_service import get_session, trusted_service_available
from harness_bench.general_hard import interactive_data as data
from harness_bench.general_hard.interactive_common import request

pytestmark = pytest.mark.skipif(
    not trusted_service_available(), reason="Unix socket transport unavailable"
)


def run(tmp_path, number, *, wrong=None, alternate=False, variant=0, capture=None):
    task = data.make_task(number, variant=variant)
    tmp_path.mkdir(parents=True, exist_ok=True)
    task.setup(tmp_path)
    try:
        data.SPECS[number][4](tmp_path, wrong=wrong, alternate=alternate)
        result = task.verify(tmp_path)
        session = get_session(tmp_path)
        assert not session.protocol_errors, session.protocol_errors
        if capture is not None:
            capture.extend(copy.deepcopy(session.records))
        return result, copy.deepcopy(session.state), session.state["clock"]
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", sorted(data.SPECS))
def test_gold_and_call_headroom(tmp_path, number):
    result, state, calls = run(tmp_path, number)
    assert result.passed, result.message
    assert 0 < calls <= 125
    independent_outcome(number, state)


def independent_outcome(number, state):
    """Explicit default-world outcomes, independently expanded from public rules."""
    if number == 472:
        rows = state["published"]
        assert rows["amber"]["open"] == ["issue-08"]
        assert rows["birch"]["urgent"] == ["issue-05"]
        assert rows["cedar"]["open"] == ["issue-02", "issue-06", "issue-09"]
        assert rows["delta"]["open"] == ["issue-07", "issue-11"]
        assert {k: v["team"] for k, v in state["alerts"].items() if v["owner"] == "task"} == {
            "issue-05": "birch",
            "issue-06": "cedar",
            "issue-09": "cedar",
        }
        assert state["alerts"]["foreign-monitor"]["owner"] == "other"
    elif number == 473:
        assert state["engine"]["offset"] == state["checkpoint"]["offset"] == 25
        assert state["engine"]["high"] == dict.fromkeys("abcd", 40)
        assert state["sink"] == {"0": 5, "10": 4, "20": 1}
        assert state["foreign_sink"] == {"0": 40, "20": 3}
    elif number == 474:
        rows = {r["id"]: r for report in state["active"].values() for r in report["rows"]}
        assert set(rows) == {f"session-{i}" for i in range(9) if i not in (0, 2)}
        assert {k: r["elapsed"] for k, r in rows.items()} == {
            k: 32 if k == "session-7" else 26 for k in rows
        }
        assert all(r["late"] is True for r in rows.values())
        assert state["active"]["batch-0"]["annotation"] == {
            "revision": 2,
            "text": "reviewer:batch-0; retain cancellation investigation",
        }
    elif number == 475:
        allocation = state["committed"]
        assert len(allocation) == 4 and not any(state["holds"].values())
        slots = {key: next(iter(row.values())) for key, row in allocation.items()}
        assert all(
            slots[f"bundle-{i}"] in ([0, 1, 2] if i < 2 else ([2, 3, 4, 5] if i == 2 else [3]))
            for i in range(4)
        )
        assert slots["bundle-0"] < slots["bundle-2"]
        assert slots["bundle-1"] < slots["bundle-3"]
        for resource in ("room", "screen", "mic"):
            for slot in range(6):
                used = sum(row.get(resource) == slot for row in allocation.values())
                used += 1 if (resource, slot) in {("screen", 0), ("room", 1), ("mic", 2)} else 0
                assert used <= 1
    elif number == 476:
        assert sorted(row["order"] for row in state["shipments"]) == [
            f"order-{i}" for i in range(4)
        ]
        assert not state["reservations"] and not state["pending"] and state["returns"]
        for row in state["shipments"]:
            assert row["picks"] == {
                "sheet-replacement": 3 if row["order"] == "order-0" else 2,
                "ink-safe": 1,
            }
        assert state["foreign_stock"] == {"sealed-customer-pallet": 7}
        assert sum(state["quarantine"].values()) == 2
        assert sum(stock["sheet-replacement"] for stock in state["stock"].values()) == 7
        assert sum(stock["ink-safe"] for stock in state["stock"].values()) == 2
    elif number == 477:
        assert sorted((r["id"], r["account"], r["units"]) for r in state["ledger"]) == [
            (f"txn-{i}", "account-D" if i % 2 == 0 else "account-B", 11 if i == 0 else i + 2)
            for i in range(8)
        ]
        assert state["checkpoint"] == 8 and state["reversed"]
        assert state["foreign_ledger"] == [
            {"key": "user-credit", "account": "account-A", "units": 100}
        ]
    elif number == 478:
        assert {k: r["data"] for k, r in state["published"].items()} == {
            "alpha": "ABZX12",
            "beta": "EFGX34",
            "gamma": "IJKL56",
        }
        assert not state["staged"]
        for key, deps in {"alpha": [], "beta": ["alpha"], "gamma": ["alpha", "beta"]}.items():
            assert state["published"][key]["dependencies"] == {
                d: state["published"][d]["receipt"] for d in deps
            }
            assert state["published"][key]["revision"] == "snapshot-8"
    elif number == 479:
        assert {k: r["data"] for k, r in state["published"].items()} == {
            "a": "ABXDEF",
            "b": "KLMNOP",
            "c": "UVWXYZ",
        }
        assert not any(j["active"] for j in state["jobs"].values())
        for key, deps in {"a": [], "b": ["a"], "c": ["a", "b"]}.items():
            assert state["published"][key]["dependencies"] == {
                d: state["published"][d]["receipt"] for d in deps
            }
    elif number == 480:
        groups = [(0,), (1, 2), (3, 4, 9), (5, 6), (7, 8)]
        assert {frozenset(group) for group in state["active"]["groups"]} == {
            frozenset(f"contact-{i}" for i in group) for group in groups
        }
        for group in groups:
            for i in group:
                assert state["assets"][f"asset-{i}"]["group"] == f"contact-{min(group)}"
        assert state["assets"]["asset-9"]["note"] == "retain:contact-9; appeal reviewed"
        assert state["assets"]["foreign-asset"] == {
            "id": "foreign-asset",
            "contact": "external",
            "group": "external",
            "revision": 1,
            "note": "foreign-owned",
        }
    elif number == 481:
        targets = {0, 2, 3, 4, 5, 6, 8}
        for i in range(9):
            record = state["records"][f"record-{i}"]
            assert record["note"] == f"retain-audit-{i}"
            if i in targets:
                assert record["email"] == record["phone"] == ""
            elif i == 1:
                assert (
                    record["email"] == "new-owner@example.invalid"
                    and record["phone"] == "555-new-owner"
                )
            else:
                assert record["email"] == f"person{i}@example.invalid"
        assert state["closed"] == {
            "subject-0": ["record-0", "record-8"],
            "subject-1": ["record-2", "record-3", "record-6"],
            "subject-2": ["record-4", "record-5"],
        }
    elif number == 482:
        assert {k: r["metrics"] for k, r in state["published"].items()} == {
            "region-0": {"completed": 1, "pending": 0, "latencies": [8]},
            "region-1": {"completed": 1, "pending": 0, "latencies": [6]},
            "region-2": {"completed": 2, "pending": 0, "latencies": [13, 11]},
        }
        dash = state["dashboard"]
        assert dash["metrics"]["completed"] == 4 and dash["metrics"]["pending"] == 0
        assert sorted(dash["metrics"]["latencies"]) == [6, 8, 11, 13]
        assert dash["receipts"] == {r: j["id"] for r, j in state["published"].items()}
        assert not any(j.get("slot_held") for j in state["jobs"].values())
    elif number == 483:
        assert (
            state["observed"]
            == state["desired"]
            == {"protected": "a", "blue": "b", "green": "c", "amber": "c"}
        )
        assert state["job"]["done"] and state["traffic"]["blue"] == 5
    elif number == 484:

        def render(expr, definitions, name):
            if "input" in expr:
                return name
            if "text" in expr:
                return expr["text"]
            if "ref" in expr:
                return render(definitions[expr["ref"]], definitions, name)
            if "concat" in expr:
                return "".join(render(part, definitions, name) for part in expr["concat"])
            if "release" in expr:
                link = expr["release"]
                deployed = state["deployed"][link["module"]]
                assert link["receipt"] == deployed["receipt"]
                module = deployed["module"]
                return render(
                    module["entry"], module["defs"], render(link["arg"], definitions, name)
                )
            call = expr["call"]
            left, right = ("[[", "]]") if call["name"] == "badge" else ("<<", ">>")
            return left + render(call["arg"], definitions, name) + right

        for name in ["Ada", "Bo", "", "Cy-D"]:
            expected = {"welcome": "[[" + name + "]] welcomed"}
            expected["receipt"] = "<<" + expected["welcome"] + ">> saved"
            expected["archive"] = "[[" + expected["receipt"] + "]] archived"
            for key, value in expected.items():
                module = state["deployed"][key]["module"]
                assert render(module["entry"], module["defs"], name) == value
    elif number == 485:
        original = [f"line-{i}" for i in range(7)]
        assert state["versions"]["source-old"] == original and not state["violations"]
        original[4] = "current-A"
        assert state["heads"]["source"] == "source-new-A"
        source = original
        for i in range(3):
            # Independent list-splice interpretation, no document_apply/check.
            expected = source[:1] + ["review"] + source[1:]
            expected[4:4] = source[:2]
            del expected[2]
            expected[9:9] = [f"approved-{i}"]
            expected += [f"owner-note:draft-{i}", f"reviewer-note:draft-{i}"]
            if i < 2:
                expected += [
                    f"dependency-install:draft-{i + 1}",
                    f"dependency-review:draft-{i + 1}",
                ]
            actual = state["versions"][state["heads"][f"draft-{i}"]]
            assert actual == expected
            source = actual
    else:
        raise AssertionError(number)


@pytest.mark.parametrize("number", sorted(data.SPECS))
def test_blank_is_not_completed(tmp_path, number):
    task = data.make_task(number)
    task.setup(tmp_path)
    try:
        assert not task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


@pytest.mark.parametrize("number", sorted(data.SPECS))
def test_different_valid_order_batch_replay(tmp_path, number):
    result, _, calls = run(tmp_path, number, alternate=True)
    assert result.passed, result.message
    assert calls < 160


MUTANTS = {
    472: ("frozen_plan", "reports_only"),
    473: ("frozen_plan", "subtract_foreign"),
    474: ("frozen_plan", "overwrite_notes"),
    475: ("frozen_plan", "wrong_receipt"),
    476: ("skip_compensation", "trust_reserved"),
    477: ("skip_compensation", "frozen_plan"),
    478: ("ignore_partial", "wrong_receipt"),
    479: ("keep_stale_job", "wrong_receipt"),
    480: ("groups_only", "frozen_partition"),
    481: ("stale_scope", "ignore_partial"),
    482: ("count_attempts", "regions_only"),
    483: ("no_spare", "trust_desired"),
    484: ("sample_only", "frozen_plan"),
    485: ("frozen_dependency", "receipt_is_head"),
}


@pytest.mark.parametrize("number,wrong", [(n, w) for n, values in MUTANTS.items() for w in values])
def test_plausible_wrong_workflow_rejected(tmp_path, number, wrong):
    result, _, _ = run(tmp_path, number, wrong=wrong)
    assert not result.passed


@pytest.mark.parametrize("number", sorted(data.SPECS))
def test_same_stale_export_requires_different_live_action(tmp_path, number):
    a, b = data.make_task(number), data.make_task(number, variant=1)
    assert a.setup_files == b.setup_files
    records = []
    ra, _, _ = run(tmp_path / "a", number, capture=records)
    rb, _, _ = run(tmp_path / "b", number, variant=1)
    assert ra.passed and rb.passed
    cross = data.make_task(number, variant=1)
    workspace = tmp_path / "blind-replay"
    cross.setup(workspace)
    try:
        # Replaying arguments/files, while refusing to adapt to fresh responses,
        # cannot solve both worlds. This is an observation-necessity witness.
        for record in records:
            for relative, contents in record["files"].items():
                path = workspace / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents)
            request(workspace, *record["argv"])
        assert not cross.verify(workspace).passed
        assert not get_session(workspace).protocol_errors
    finally:
        cross.cleanup(workspace)


@pytest.mark.parametrize("number", sorted(data.SPECS))
def test_recoverable_command_error_and_safe_reads_do_not_spoil_gold(tmp_path, number):
    task = data.make_task(number)
    task.setup(tmp_path)
    try:
        response, code = request(tmp_path, "not-a-command")
        assert code == 2 and response["status"] == "unknown_command"
        request(tmp_path, "help")
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed
        assert not get_session(tmp_path).protocol_errors
    finally:
        task.cleanup(tmp_path)


def test_stream_partial_prefix_and_rollback_leave_sink_generation(tmp_path):
    task = data.make_task(473)
    task.setup(tmp_path)
    try:
        head = data._ask(tmp_path, "head")
        events = data._scan(tmp_path, ("feed", "list"), revision=head["revision"])
        engine = data._ask(tmp_path, "engine", "status")
        response = data._put(
            tmp_path, ("engine", "apply"), [events[2], events[4]], epoch=engine["epoch"]
        )
        assert response["status"] == "offset_gap" and response["committed_offset"] == 3
        sink = data._ask(tmp_path, "sink", "status")
        data._ask(tmp_path, "engine", "rollback")
        assert data._ask(tmp_path, "engine", "status")["engine"]["offset"] == 2
        assert data._ask(tmp_path, "sink", "status") == sink
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


def test_nonfinite_json_and_bool_delta_do_not_change_sink(tmp_path):
    task = data.make_task(473)
    task.setup(tmp_path)
    try:
        before = data._ask(tmp_path, "sink", "status")
        for payload in ('{"0":NaN}', '{"0":Infinity}', json.dumps({"0": True})):
            response = data._ask(
                tmp_path,
                "sink",
                "adjust",
                generation=before["generation"],
                key="bad",
                json=payload,
                scope="task",
            )
            assert response["status"] in {"invalid_request", "invalid_delta"}
            assert data._ask(tmp_path, "sink", "status") == before
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)


def test_protected_document_violation_is_sticky_even_if_restored(tmp_path):
    task = data.make_task(485)
    task.setup(tmp_path)
    try:
        pin = data._ask(tmp_path, "pin")
        data._ask(
            tmp_path,
            "branch",
            doc="source",
            source=pin["pinned"]["source"],
            revision=pin["revision"],
            key="forbidden",
        )
        task.apply_gold(tmp_path)
        assert not task.verify(tmp_path).passed
        assert "Protected" in task.verify(tmp_path).message
    finally:
        task.cleanup(tmp_path)


def test_macro_definition_names_are_not_reserved_and_unused_cycles_are_rejected(tmp_path):
    task = data.make_task(484)
    task.setup(tmp_path)
    try:
        data._ask(tmp_path, "registry", "seal")
        program = {"defs": {"concat": {"text": "ok"}}, "entry": {"concat": [{"ref": "concat"}]}}
        data._put(tmp_path, ("module", "stage"), program, module="welcome")
        assert data._ask(tmp_path, "module", "compile", module="welcome")["status"] == "ok"
        program["defs"]["unused"] = {"ref": "unused"}
        data._put(tmp_path, ("module", "stage"), program, module="welcome")
        assert (
            data._ask(tmp_path, "module", "compile", module="welcome")["status"] == "compile_failed"
        )
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed
        assert not get_session(tmp_path).protocol_errors
    finally:
        task.cleanup(tmp_path)


def test_boolean_stock_quantities_are_not_numeric_reservations(tmp_path):
    task = data.make_task(476)
    task.setup(tmp_path)
    try:
        quote = data._ask(tmp_path, "quote", order="order-0")
        option = copy.deepcopy(quote["options"][0])
        option["picks"]["ink-safe"] = True
        result = data._put(
            tmp_path,
            ("reserve",),
            {"order": "order-0", "generation": quote["generation"], **option},
            key="boolean",
        )
        assert result["status"] == "invalid_quantities"
        assert data._ask(tmp_path, "status")["reservations"] == {}
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed
    finally:
        task.cleanup(tmp_path)
