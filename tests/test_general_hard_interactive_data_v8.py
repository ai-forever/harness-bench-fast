"""Finite witnesses for concurrent data workflows, using the public client."""

import copy
import itertools
from contextlib import contextmanager

import pytest

from harness_bench.feasibility_tasks.trusted_service import get_session, trusted_service_available
from harness_bench.general_hard import interactive_data as data

pytestmark = pytest.mark.skipif(
    not trusted_service_available(), reason="Unix socket transport unavailable"
)


@contextmanager
def live(path, number, *, variant=0):
    task = data.make_task(number, variant=variant)
    task.setup(path)
    try:
        yield task
        assert not get_session(path).protocol_errors
    finally:
        task.cleanup(path)


@pytest.mark.parametrize("number", sorted(data.SPECS))
def test_observation_does_not_trigger_domain_writers(tmp_path, number):
    with live(tmp_path, number):
        before = copy.deepcopy(get_session(tmp_path).state)
        for _ in range(3):
            data._ask(tmp_path, "head")
            data._ask(tmp_path, "help")
            data._ask(tmp_path, "status")
        after = copy.deepcopy(get_session(tmp_path).state)
        before.pop("clock")
        after.pop("clock")
        assert before == after


@pytest.mark.parametrize("number", sorted(data.SPECS))
def test_alternative_strategy_in_second_live_world(tmp_path, number):
    with live(tmp_path, number, variant=1) as task:
        data.SPECS[number][4](tmp_path, alternate=True)
        result = task.verify(tmp_path)
        assert result.passed, result.message
        assert get_session(tmp_path).state["clock"] <= 125


def test_publication_key_replay_after_source_change_is_not_a_new_publish(tmp_path):
    with live(tmp_path, 472) as task:
        h = data._ask(tmp_path, "head")
        cut = data._ask(tmp_path, "seal", revision=h["revision"])
        data._put(
            tmp_path,
            ("reports", "stage"),
            {"amber": {"revision": cut["revision"], "open": [], "urgent": []}},
        )
        first = data._ask(
            tmp_path, "reports", "publish", team="amber", revision=cut["revision"], key="probe"
        )
        assert first["status"] == "ok"
        changed = data._ask(tmp_path, "head")
        assert changed["revision"] != cut["revision"]
        assert (
            data._ask(
                tmp_path, "reports", "publish", team="amber", revision=cut["revision"], key="probe"
            )
            == first
        )
        assert data._ask(tmp_path, "head") == changed
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed


def test_sink_commit_is_not_an_unconditional_producer_advance(tmp_path):
    with live(tmp_path, 473) as task:
        head = data._ask(tmp_path, "head")
        sink = data._ask(tmp_path, "sink", "status")
        data._put(
            tmp_path,
            ("sink", "adjust"),
            {},
            scope="task",
            generation=sink["generation"],
            key="empty-before-consumption",
        )
        assert data._ask(tmp_path, "head") == head
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed
        # The new partition temporarily retreats the watermark; subsequent
        # publication reconciles it instead of rewinding the other writer.
        states = [
            r["response"]
            for r in get_session(tmp_path).records
            if r["argv"][:2] == ["engine", "status"]
        ]
        assert any(r.get("watermark") == 4 for r in states)


def test_capacity_tiny_independent_global_schedule_oracle(tmp_path):
    with live(tmp_path, 475) as task:
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed
        h = data._ask(tmp_path, "head")
        bundles = data._scan(tmp_path, ("bundles", "list"))
        feasible = []
        for choices in itertools.product(*(b["allowed"] for b in bundles)):
            by_id = {b["id"]: slot for b, slot in zip(bundles, choices, strict=True)}
            if any(by_id[d] >= by_id[b["id"]] for b in bundles for d in b["after"]):
                continue
            if any(
                sum(
                    slot == at and resource in b["resources"]
                    for b, slot in zip(bundles, choices, strict=True)
                )
                + h["maintenance"].get(resource + ":" + str(at), 0)
                > capacity
                for resource, capacity in h["capacity"].items()
                for at in h["slots"]
            ):
                continue
            feasible.append(choices)
        assert set(feasible) == {(2, 0, 4, 3), (2, 0, 5, 3)}
        # C at3 is individually feasible after A2/B0, but steals D's ONLY slot.
        assert 3 in bundles[2]["allowed"] and bundles[3]["allowed"] == [3]
        assert all(choices[2] != 3 for choices in feasible)


def test_stale_macro_nested_in_concat_is_a_recoverable_compile_error(tmp_path):
    with live(tmp_path, 484, variant=1) as task:
        data._ask(tmp_path, "registry", "seal")
        data._put(
            tmp_path,
            ("module", "stage"),
            {
                "defs": {},
                "entry": {
                    "concat": [{"call": {"name": "badge", "version": "v2", "arg": {"input": True}}}]
                },
            },
            module="welcome",
        )
        assert (
            data._ask(tmp_path, "module", "compile", module="welcome")["status"] == "compile_failed"
        )
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed


def test_dashboard_boolean_pending_is_not_integer_zero(tmp_path):
    with live(tmp_path, 482) as task:
        task.apply_gold(tmp_path)
        before = data._ask(tmp_path, "status")
        body = copy.deepcopy(before["dashboard"])
        body.pop("revision")
        body["metrics"]["pending"] = False
        assert data._put(tmp_path, ("dashboard", "publish"), body)["status"] == "invalid_metrics"
        assert data._ask(tmp_path, "status") == before
        assert task.verify(tmp_path).passed


def test_document_receipt_lags_live_head_and_replay_preserves_foreign_suffix(tmp_path):
    with live(tmp_path, 485) as task:
        pin = data._ask(tmp_path, "pin")
        args = {
            "doc": "draft-0",
            "source": pin["pinned"]["source"],
            "revision": pin["revision"],
            "key": "probe-branch",
        }
        branch = data._ask(tmp_path, "branch", **args)
        actual = data._ask(tmp_path, "document", "get", doc="draft-0")
        assert branch["version"] != actual["version"]
        assert actual["protected_suffix"] == ["owner-note:draft-0"]
        assert data._ask(tmp_path, "branch", **args) == branch
        assert data._ask(tmp_path, "document", "get", doc="draft-0") == actual
        data._put(
            tmp_path,
            ("edit",),
            [
                {"kind": "insert", "at": 0, "lines": ["temporary"]},
                {"kind": "delete", "at": 0, "count": 1},
            ],
            doc="draft-0",
            base=actual["version"],
            key="interrupted",
        )
        receipt = data._ask(tmp_path, "operation", "status", key="interrupted")["receipt"]
        after = data._ask(tmp_path, "document", "get", doc="draft-0")
        assert receipt["applied"] == 1 and receipt["reason"] == "concurrent_write"
        assert receipt["version"] != after["version"] and after["lines"][0] == "temporary"
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed


def test_document_staging_then_branch_is_an_independent_correct_strategy(tmp_path):
    with live(tmp_path, 485) as task:
        head = data._ask(tmp_path, "head")
        pin = data._ask(tmp_path, "pin")
        # A transaction-style client builds each complete body in a scratch
        # document before installing it. It need not reproduce live-edit gold.
        serial = 0
        for doc, recipe in head["targets"].items():
            for _ in range(3):
                original = data._ask(tmp_path, "document", "get", doc=recipe["source"])
                current = data._ask(tmp_path, "document", "get", doc=doc)
                desired = original["lines"][:]
                for op in recipe["ops"]:
                    at = op["at"]
                    if op["kind"] == "insert":
                        desired[at:at] = op["lines"]
                    elif op["kind"] == "copy":
                        desired[at:at] = original["lines"][op["start"] : op["stop"]]
                    else:
                        del desired[at : at + op["count"]]
                desired += current["protected_suffix"]
                if current["lines"] == desired:
                    break
                serial += 1
                scratch = "scratch-" + str(serial)
                branch = data._ask(
                    tmp_path,
                    "branch",
                    doc=scratch,
                    source=original["version"],
                    revision=pin["revision"],
                    key="scratch-branch-" + str(serial),
                )
                base = branch["version"]
                key = "scratch-edit-" + str(serial)
                data._put(
                    tmp_path,
                    ("edit",),
                    [
                        {"kind": "delete", "at": 0, "count": len(original["lines"])},
                        {"kind": "insert", "at": 0, "lines": desired},
                    ],
                    doc=scratch,
                    base=base,
                    key=key,
                )
                receipt = data._ask(tmp_path, "operation", "status", key=key)["receipt"]
                assert receipt["applied"] == 2
                data._ask(
                    tmp_path,
                    "branch",
                    doc=doc,
                    source=receipt["version"],
                    revision=pin["revision"],
                    key="install-" + str(serial),
                )
        result = task.verify(tmp_path)
        assert result.passed, result.message
        assert get_session(tmp_path).state["collaborator_events"] == []


@pytest.mark.parametrize(
    "number,words,opts,payload",
    [
        (472, ("reports", "stage"), {}, []),
        (474, ("closeout", "stage"), {"annotations": "[]"}, {}),
        (481, ("redact",), {"revision": "aliases-7"}, ["not-an-object"]),
    ],
)
def test_invalid_json_shape_is_a_recoverable_command_error(tmp_path, number, words, opts, payload):
    with live(tmp_path, number):
        before = copy.deepcopy(get_session(tmp_path).state)
        result = data._put(tmp_path, words, payload, **opts)
        assert result["status"] in {"invalid_report", "invalid_batch", "invalid_redaction"}
        after = copy.deepcopy(get_session(tmp_path).state)
        before.pop("clock")
        after.pop("clock")
        assert before == after


def test_import_retry_refreshes_outer_scopes_without_reposting_item_keys(tmp_path):
    with live(tmp_path, 477) as task:
        routes = data._ask(tmp_path, "routes", "seal")
        head = data._ask(tmp_path, "head")
        payload = [{"id": "txn-1", "key": "scope-probe", "account": "account-B", "units": 3}]
        first = data._put(
            tmp_path,
            ("import", "apply"),
            payload,
            revision=head["revision"],
            routes=routes["revision"],
        )
        assert first["status"] == "outcome_unknown"
        after_commit = data._ask(tmp_path, "import", "status")
        stale = data._put(
            tmp_path,
            ("import", "apply"),
            payload,
            revision=head["revision"],
            routes=routes["revision"],
        )
        assert stale["status"] == "source_changed"
        assert data._ask(tmp_path, "import", "status")["items"] == after_commit["items"]
        replay = data._put(
            tmp_path,
            ("import", "apply"),
            payload,
            revision=stale["revision"],
            routes=stale["routes"],
        )
        assert replay["status"] == "outcome_unknown"
        assert data._ask(tmp_path, "import", "status")["items"] == after_commit["items"]
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed
