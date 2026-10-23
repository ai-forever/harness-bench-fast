"""Independent small-instance oracles and mechanism mutants for data v4."""

from __future__ import annotations

import copy
import itertools
import json
from fractions import Fraction
from functools import lru_cache

import pytest

from harness_bench.general_hard import data_tasks as d
from harness_bench.general_hard import data_v4 as v


@lru_cache(None)
def gold(number):
    namespace = {}
    exec(v.PROGRAM_GOLDS[number].split(v.CLI)[0], namespace)
    return namespace["solve"]


def test_all_fourteen_active_tasks_changed_and_keep_contracts():
    assert len(d.TASKS) == 14
    for current, old in zip(d.TASKS, d.LEGACY_TASKS, strict=True):
        assert current.id == old.id
        assert "hardening-v4" in current.tags
        assert current.max_timeout_seconds == 900
        assert current.runtime_protocol == old.runtime_protocol
        assert current.gold_files != old.gold_files
    assert set(v.PROGRAM_CASES) == {473, 475, 477, 479, 481, 483, 484, 485}
    assert all(len(cases) >= 18 for cases in v.PROGRAM_CASES.values())


def remediation_oracle(reports, policy):
    actions = sorted(policy["actions"], key=lambda a: a["id"])
    candidates = []
    for count in range(len(actions) + 1):
        for group in itertools.combinations(actions, count):
            names = {a["id"] for a in group}
            cost = sum(a["cost"] for a in group)
            if cost > policy["budget"] or any(
                sum(a["load"].get(r, 0) for a in group) > cap
                for r, cap in policy["capacity"].items()
            ):
                continue
            if any(not set(a["requires"]) <= names for a in group) or any(
                len(names & set(g)) > 1 for g in policy["exclusive"]
            ):
                continue
            residual = []
            for failure in policy["failures"]:
                live = [a for a in group if a["id"] not in failure["disabled"]]
                row = []
                for report in reports:
                    total = 0
                    for incident in report["incidents"]:
                        target = [incident["service"], incident["fingerprint"]]
                        kinds = {a["kind"] for a in live if target in a["targets"]}
                        if kinds != {"repair", "guard"}:
                            total += (incident["end"] - incident["start"] + policy["linger"]) * len(
                                incident["ids"]
                            )
                    row.append(total)
                residual.append(row)
            key = (
                max(itertools.chain.from_iterable(residual)),
                sum(itertools.chain.from_iterable(residual)),
                cost,
                count,
                tuple(sorted(names)),
            )
            candidates.append((key, residual))

    def pack(key):
        return {"worst": key[0], "total": key[1], "cost": key[2], "actions": list(key[4])}

    best, residual = min(candidates)
    sensitivity = []
    for action in actions:
        row = {"action": action["id"]}
        for name, required in [("required", True), ("forbidden", False)]:
            subset = [key for key, _ in candidates if (action["id"] in key[4]) == required]
            row[name] = pack(min(subset)) if subset else None
        sensitivity.append(row)
    return {
        **pack(best),
        "residuals": [
            {"scenario": f["id"], "snapshots": r}
            for f, r in zip(policy["failures"], residual, strict=True)
        ],
        "sensitivity": sensitivity,
    }


def test_472_independent_frontier_oracle_and_single_type_mutant():
    reports = [
        {
            "incidents": [
                {"service": "s", "fingerprint": "f", "start": 0, "end": 5, "ids": ["a", "b"]}
            ]
        }
    ]
    actions = [
        {
            "id": k,
            "kind": kind,
            "cost": cost,
            "load": {"r": 1},
            "targets": [["s", "f"]],
            "requires": [],
        }
        for k, kind, cost in [
            ("a", "repair", 1),
            ("b", "guard", 1),
            ("c", "repair", 2),
            ("d", "guard", 2),
        ]
    ]
    policy = {
        "actions": actions,
        "linger": 2,
        "budget": 4,
        "capacity": {"r": 3},
        "exclusive": [["a", "c"]],
        "failures": [{"id": "healthy", "disabled": []}, {"id": "broken", "disabled": ["a"]}],
    }
    result = v.incident_remediation(reports, policy)
    assert result == remediation_oracle(reports, policy)
    assert result["actions"] == ["b", "c"] and result["worst"] == 0
    no_failure = copy.deepcopy(policy)
    no_failure["failures"] = no_failure["failures"][:1]
    assert v.incident_remediation(reports, no_failure)["actions"] == ["a", "b"]
    one_type = copy.deepcopy(policy)
    for action in one_type["actions"]:
        action["kind"] = "repair"
    assert v.incident_remediation(reports, one_type)["worst"] == 14
    dependency = copy.deepcopy(policy)
    dependency["actions"][2]["requires"] = ["d"]
    assert v.incident_remediation(reports, dependency) == remediation_oracle(reports, dependency)
    assert v.incident_remediation(reports, dependency)["actions"] != result["actions"]


def stream_replay_oracle(x):
    # Rebuild the branch from its event tape after every step. No saved mutable engine states.
    tape = []
    checkpoints = {}
    previous = {}
    audit = []
    published = []

    def replay(commands):
        active = set(x["partitions"])
        high = {p: None for p in active}
        watermark = None
        rows = {}
        status = None
        for e in commands:
            op = e["op"]
            status = op
            if op == "record":
                parts = e["line"].split(" ")
                pairs = [p.split("=", 1) for p in parts]
                if any(len(p) != 2 for p in pairs) or len(dict(pairs)) != len(pairs):
                    status = "invalid"
                    continue
                row = dict(pairs)
                row["ts"] = int(row["ts"])
                if e["partition"] not in active:
                    status = "idle"
                    continue
                prior = watermark
                p = e["partition"]
                high[p] = max(high[p], row["ts"]) if high[p] is not None else row["ts"]
                if active and all(high[p] is not None for p in active):
                    current = min(high[p] for p in active) - x["lateness"]
                    watermark = current if watermark is None else max(watermark, current)
                key = (row["svc"], row["id"])
                if prior is not None and row["ts"] < prior:
                    status = "late"
                elif key in rows and e["rev"] <= rows[key][0]:
                    status = "stale"
                else:
                    rows[key] = (e["rev"], None if e["delete"] else row)
                    status = "deleted" if e["delete"] else "accepted"
            else:
                if op == "idle":
                    active.discard(e["partition"])
                else:
                    active.add(e["partition"])
                if active and all(high[p] is not None for p in active):
                    current = min(high[p] for p in active) - x["lateness"]
                    watermark = current if watermark is None else max(watermark, current)
        return watermark, rows, status

    for i, e in enumerate(x["events"], 1):
        op = e["op"]
        if op == "rollback":
            tape = checkpoints[e["name"]][:]
            status = "rollback"
        elif op == "checkpoint":
            checkpoints[e["name"]] = tape[:]
            status = "checkpoint"
            watermark, rows, _ = replay(tape)
            now = {}
            for _, row in rows.values():
                if row is not None:
                    start = (row["ts"] // x["width"]) * x["width"]
                    key = (row["svc"], start)
                    if watermark is not None and start + x["width"] <= watermark:
                        count, errors = now.get(key, (0, 0))
                        now[key] = (count + 1, errors + (row["level"] == "ERROR"))
            delta = []
            for key in sorted(set(now) | set(previous)):
                a = previous.get(key, (0, 0))
                b = now.get(key, (0, 0))
                if a != b:
                    delta.append(
                        {
                            "svc": key[0],
                            "start": key[1],
                            "count": b[0] - a[0],
                            "errors": b[1] - a[1],
                        }
                    )
            published.append({"name": e["name"], "watermark": watermark, "delta": delta})
            previous = now
        else:
            tape.append(e)
            _, _, status = replay(tape)
        audit.append({"event": i, "status": status})
    watermark, rows, _ = replay(tape)
    return {
        "audit": audit,
        "checkpoints": published,
        "rows": [
            {"svc": a, "id": b, "rev": rev, "ts": None if row is None else row["ts"]}
            for (a, b), (rev, row) in sorted(rows.items())
        ],
        "watermark": watermark,
    }


@pytest.mark.parametrize("case", range(20))
def test_473_independent_tape_replay_oracle(case):
    x, expected = v.PROGRAM_CASES[473][case]
    assert stream_replay_oracle(x) == expected


def test_473_rollback_does_not_rollback_published_totals_mutant():
    def record(key, at):
        return {
            "op": "record",
            "partition": "p",
            "line": f"ts={at} svc=s level=ERROR id={key}",
            "rev": 1,
            "delete": False,
        }

    x = {
        "partitions": ["p"],
        "lateness": 0,
        "width": 2,
        "events": [
            {"op": "checkpoint", "name": "empty"},
            record("a", 0),
            record("b", 4),
            {"op": "checkpoint", "name": "filled"},
            {"op": "rollback", "name": "empty"},
            {"op": "checkpoint", "name": "retracted"},
        ],
    }
    result = gold(473)(x)
    assert result == stream_replay_oracle(x)
    assert result["checkpoints"][-1]["delta"] == [
        {"svc": "s", "start": 0, "count": -1, "errors": -1}
    ]


def matching_assignment_oracle(x):
    starts = sorted(x["starts"], key=lambda a: a["id"])
    ends = {e["id"]: e for e in x["ends"]}
    best = None
    answer = None
    for vector in itertools.product([None, *sorted(ends)], repeat=len(starts)):
        selected = [(a, ends[b]) for a, b in zip(starts, vector, strict=True) if b is not None]
        if len({b["id"] for a, b in selected}) != len(selected):
            continue
        if any(
            a["kind"] not in b["accept"] or not a["min"] <= b["at"] - a["at"] <= a["max"]
            for a, b in selected
        ):
            continue
        ids = {a["id"] for a, b in selected}
        if any(ids & set(group) and not set(group) <= ids for group in x["groups"]):
            continue
        if any(
            not lo <= sum(a["kind"] == kind for a, b in selected) <= hi
            for kind, (lo, hi) in x["quotas"].items()
        ):
            continue
        feasible = True
        for t in range(-100, 101):
            running = [a for a, b in selected if a["at"] <= t < b["at"]]
            if any(
                sum(a["channel"] == channel for a in running) > cap
                for channel, cap in x["capacity"].items()
            ) or any(
                sum(a["demands"].get(resource, 0) for a in running) > cap
                for resource, cap in x["resources"].items()
            ):
                feasible = False
                break
        if not feasible:
            continue
        cost = sum((b["at"] - a["at"] - a["target"]) ** 2 for a, b in selected)
        pairs = [[a["id"], b["id"]] for a, b in selected]
        key = (-len(pairs), cost, pairs)
        if best is None or key < best:
            best = key
            answer = {
                "pairs": pairs,
                "cost": cost,
                "unmatched_starts": [a["id"] for a in starts if a["id"] not in ids],
                "unmatched_ends": sorted(set(ends) - {b["id"] for a, b in selected}),
            }
    return answer if answer is not None else {"pairs": None}


@pytest.mark.parametrize("case", [0, 1, 2, 3, 6, 7, 10, 15, 19])
def test_475_independent_assignment_oracle(case):
    x, expected = v.PROGRAM_CASES[475][case]
    assert matching_assignment_oracle(x) == expected


def test_475_groups_quotas_and_cross_resource_capacity_are_binding():
    x = copy.deepcopy(v.PROGRAM_CASES[475][1][0])
    x["resources"]["cpu"] = 1
    x["quotas"] = {"k": [1, 2]}
    assert gold(475)(x) == {"pairs": None}  # group requires both, capacity permits one
    without = copy.deepcopy(x)
    without["groups"] = []
    assert gold(475)(without)["pairs"] is not None
    x["resources"]["cpu"] = 2
    assert len(gold(475)(x)["pairs"]) == 2
    x["quotas"]["k"] = [1, 1]
    assert gold(475)(x) == {"pairs": None}


def transaction_unit_oracle(x):
    lots = {r["id"]: r for r in x["lots"]}
    units = []
    holds = {}
    audit = []
    for lot in x["lots"]:
        units.extend(
            [
                {"lot": lot["id"], "wh": lot["warehouse"], "state": "free", "hold": None}
                for _ in range(lot["qty"])
            ]
        )

    def advance(t):
        for name, hold in holds.items():
            if hold["state"] == "held" and hold["until"] <= t:
                hold["state"] = "expired"
                for unit in units:
                    if unit["state"] == "held" and unit["hold"] == name:
                        unit["state"] = "free"
                        unit["hold"] = None
        for unit in units:
            if unit["state"] == "free" and lots[unit["lot"]]["expires"] <= t:
                unit["state"] = "expired"

    for tx in sorted(x["transactions"], key=lambda t: (t["at"], t["id"])):
        if tx["at"] > x["horizon"]:
            continue
        advance(tx["at"])
        saved = copy.deepcopy((units, holds))
        failure = None
        for j, event in enumerate(tx["events"]):
            op = event["op"]
            reason = None
            if op in ("reserve", "transfer"):
                warehouses = event["warehouses"] if op == "reserve" else [event["source"]]
                until = event["until"] if op == "reserve" else tx["at"]
                candidates = sorted(
                    [
                        i
                        for i, u in enumerate(units)
                        if u["state"] == "free"
                        and u["wh"] in warehouses
                        and lots[u["lot"]]["sku"] == event["sku"]
                        and lots[u["lot"]]["expires"] > until
                    ],
                    key=lambda i: (
                        lots[units[i]["lot"]]["expires"],
                        warehouses.index(units[i]["wh"]),
                        units[i]["lot"],
                        i,
                    ),
                )
                if len(candidates) < event["qty"]:
                    reason = "insufficient"
                else:
                    selected = candidates[: event["qty"]]
                    allocations = []
                    for i in selected:
                        unit = units[i]
                        if op == "transfer":
                            unit["wh"] = event["destination"]
                        else:
                            key = (unit["lot"], unit["wh"])
                            if (
                                allocations
                                and (allocations[-1]["lot"], allocations[-1]["warehouse"]) == key
                            ):
                                allocations[-1]["qty"] += 1
                            else:
                                allocations.append({"lot": key[0], "warehouse": key[1], "qty": 1})
                            unit["state"] = "held"
                            unit["hold"] = event["name"]
                    if op == "reserve":
                        holds[event["name"]] = {
                            "id": event["name"],
                            "state": "held",
                            "until": until,
                            "allocations": allocations,
                        }
            else:
                name = event["hold"]
                if name not in holds or holds[name]["state"] != "held":
                    reason = "inactive_hold"
                else:
                    holds[name]["state"] = "cancelled" if op == "cancel" else "shipped"
                    for unit in units:
                        if unit["state"] == "held" and unit["hold"] == name:
                            unit["state"] = "free" if op == "cancel" else "shipped"
                            unit["hold"] = None
            if reason is not None:
                failure = (j, reason)
                break
        if failure:
            units, holds = saved
        audit.append(
            {
                "id": tx["id"],
                "status": "aborted" if failure else "committed",
                "failure": failure[0] if failure else None,
                "reason": failure[1] if failure else None,
            }
        )
    advance(x["horizon"])
    inventory = {}
    shipped = dict.fromkeys({r["sku"] for r in lots.values()}, 0)
    expired = dict(shipped)
    for unit in units:
        sku = lots[unit["lot"]]["sku"]
        if unit["state"] == "expired":
            expired[sku] += 1
        elif unit["state"] == "shipped":
            shipped[sku] += 1
        else:
            key = (unit["lot"], unit["wh"])
            inventory.setdefault(key, {"lot": key[0], "warehouse": key[1], "free": 0, "held": 0})[
                unit["state"]
            ] += 1
    return {
        "transactions": audit,
        "ledger": {
            "inventory": [inventory[k] for k in sorted(inventory)],
            "holds": [holds[k] for k in sorted(holds)],
            "shipped": shipped,
            "expired_units": expired,
        },
    }


@pytest.mark.parametrize("case", range(20))
def test_477_independent_unit_ledger_rollback_oracle(case):
    x, expected = v.PROGRAM_CASES[477][case]
    assert transaction_unit_oracle(x) == expected


def test_477_partial_transfer_is_rolled_back_and_expiry_is_not():
    x = {
        "lots": [
            {"id": "a", "sku": "A", "warehouse": "x", "qty": 2, "expires": 5},
            {"id": "b", "sku": "A", "warehouse": "x", "qty": 1, "expires": 1},
        ],
        "horizon": 2,
        "transactions": [
            {
                "id": "t",
                "at": 1,
                "events": [
                    {"op": "transfer", "sku": "A", "qty": 2, "source": "x", "destination": "y"},
                    {
                        "op": "reserve",
                        "name": "h",
                        "sku": "A",
                        "qty": 3,
                        "warehouses": ["y"],
                        "until": 3,
                    },
                ],
            }
        ],
    }
    result = gold(477)(x)
    assert result == transaction_unit_oracle(x)
    assert result["ledger"]["inventory"] == [{"lot": "a", "warehouse": "x", "free": 2, "held": 0}]
    assert result["ledger"]["expired_units"] == {"A": 1} and result["ledger"]["holds"] == []


def cache_path_oracle(x):
    resources = {r["id"]: r for r in x["resources"]}
    best = None
    caches = [
        set(s)
        for n in range(len(resources) + 1)
        for s in itertools.combinations(sorted(resources), n)
        if sum(resources[k]["bytes"] for k in s) <= x["cache_bytes"]
    ]

    def paths(at, pieces):
        nonlocal best
        if at == x["n"]:
            for history in itertools.product(caches, repeat=len(pieces)):
                previous = set()
                cost = sum(p["cost"] for p in pieces)
                size = 0
                valid = True
                for piece, resident in zip(pieces, history, strict=True):
                    loads = resident - previous
                    if not set(piece["requires"]) <= resident or any(
                        not any(a <= piece["a"] < b for a, b in resources[r]["available"])
                        for r in loads
                    ):
                        valid = False
                        break
                    cost += sum(resources[r]["cost"] for r in loads)
                    size += sum(resources[r]["bytes"] for r in loads)
                    previous = resident
                if valid:
                    key = (
                        cost,
                        size,
                        len(pieces),
                        [p["id"] for p in pieces],
                        [sorted(c) for c in history],
                    )
                    if best is None or key < best:
                        best = key
            return
        for piece in x["pieces"]:
            if piece["a"] == at:
                paths(piece["b"], pieces + [piece])

    paths(0, [])
    return (
        {"plan": None}
        if best is None
        else {"plan": best[3], "cost": best[0], "bytes": best[1], "cache": best[4]}
    )


@pytest.mark.parametrize("case", range(9))
def test_479_independent_path_cache_cartesian_oracle(case):
    x, expected = v.PROGRAM_CASES[479][case]
    assert cache_path_oracle(x) == expected


def test_479_prefetch_and_reload_mutants_are_exposed():
    prefetch = v.PROGRAM_CASES[479][0][1]
    assert prefetch["cost"] == 5 and prefetch["bytes"] == 2 and prefetch["cache"][0] == ["a", "b"]
    reload = v.PROGRAM_CASES[479][1][1]
    assert reload["cost"] == 7 and reload["bytes"] == 3 and reload["cache"] == [["a"], ["b"], ["a"]]


def all_partitions(ids):
    # Restricted-growth strings, independent of the reference group-list recursion.
    out = []
    for labels in itertools.product(range(len(ids)), repeat=len(ids)):
        if labels[0] != 0 or any(labels[i] > max(labels[:i]) + 1 for i in range(1, len(ids))):
            continue
        out.append(
            tuple(
                tuple(v for v, left in zip(ids, labels, strict=True) if left == group)
                for group in range(max(labels) + 1)
            )
        )
    return out


def partition_history_oracle(x):
    ids = sorted(x["ids"])
    choices = []
    for layer in x["layers"]:
        accepted = []
        for groups in all_partitions(ids):
            owner = {v: i for i, g in enumerate(groups) for v in g}
            if (
                not layer["groups"][0] <= len(groups) <= layer["groups"][1]
                or max(map(len, groups)) > layer["max_size"]
            ):
                continue
            if any(owner[a] != owner[b] for a, b in layer["must"]) or any(
                owner[a] == owner[b] for a, b in layer["cannot"]
            ):
                continue
            score = sum(e["weight"] for e in layer["evidence"] if owner[e["a"]] == owner[e["b"]])
            accepted.append((groups, owner, score))
        choices.append(accepted)
    costs = {tuple(sorted((e["a"], e["b"]))): e["cost"] for e in x["change_costs"]}
    best = None
    for history in itertools.product(*choices):
        changes = []
        for before, after in zip(history, history[1:], strict=False):
            changes.extend(
                (a, b)
                for a, b in itertools.combinations(ids, 2)
                if (before[1][a] == before[1][b]) != (after[1][a] == after[1][b])
            )
        if len(changes) > x["change_budget"]:
            continue
        score = sum(layer[2] for layer in history) - sum(costs.get(pair, 0) for pair in changes)
        key = (
            -score,
            len(changes),
            sum(len(layer[0]) for layer in history),
            tuple(layer[0] for layer in history),
        )
        if best is None or key < best:
            best = key
    return (
        {"history": None}
        if best is None
        else {
            "history": [[list(g) for g in s] for s in best[3]],
            "score": -best[0],
            "changes": best[1],
            "groups": best[2],
        }
    )


@pytest.mark.parametrize("case", [0, 1, 4, 8, 12])
def test_481_independent_history_cartesian_oracle(case):
    x, expected = v.PROGRAM_CASES[481][case]
    assert partition_history_oracle(x) == expected


def test_480_evidence_is_derived_from_corrected_components_and_history_is_coupled():
    task = next(t for t in d.TASKS if t.id.startswith("task_480_"))
    answer = json.loads(task.gold_files["answer.json"])
    policy, x = v.ARTIFACT_POLICIES[480]
    for snapshot, layer in zip(answer["baseline"], x["layers"], strict=True):
        groups = [set(g["source_ids"]) for g in snapshot["groups"]]
        for edge in layer["evidence"]:
            assert edge["weight"] == (
                policy["same_weight"]
                if any({edge["a"], edge["b"]} <= g for g in groups)
                else policy["different_weight"]
            )
    assert answer["privacy_history"] == v.partition_history_solve(x)
    independent = copy.deepcopy(x)
    independent["change_budget"] = 100
    independent["change_costs"] = []
    assert v.partition_history_solve(independent)["history"] != answer["privacy_history"]["history"]
    # The same privacy mechanism has an independent tiny exhaustive check.
    tiny = copy.deepcopy(x)
    tiny["ids"] = tiny["ids"][:3]
    for layer in tiny["layers"]:
        layer["groups"] = [1, 3]
        layer["cannot"] = [p for p in layer["cannot"] if set(p) <= set(tiny["ids"])]
        layer["evidence"] = [e for e in layer["evidence"] if {e["a"], e["b"]} <= set(tiny["ids"])]
    tiny["change_costs"] = [e for e in tiny["change_costs"] if {e["a"], e["b"]} <= set(tiny["ids"])]
    assert v.partition_history_solve(tiny) == partition_history_oracle(tiny)


def test_481_global_change_budget_changes_the_optimum():
    x = {
        "ids": ["a", "b", "c"],
        "layers": [
            {
                "must": [],
                "cannot": [["a", "c"]],
                "max_size": 2,
                "groups": [2, 3],
                "evidence": [{"a": "a", "b": "b", "weight": 8}],
            },
            {
                "must": [],
                "cannot": [["a", "c"]],
                "max_size": 2,
                "groups": [2, 3],
                "evidence": [{"a": "b", "b": "c", "weight": 9}],
            },
        ],
        "change_budget": 0,
        "change_costs": [],
    }
    assert gold(481)(x) == partition_history_oracle(x)
    assert gold(481)(x)["score"] == 9
    x["change_budget"] = 2
    assert gold(481)(x)["score"] == 17


def mitigation_cells_oracle(x):
    policy = x["mitigation"]
    h = x["horizon"]
    w = policy["width"]
    curves = {}
    for resource, capacity in x["capacity"].items():
        curves[resource] = [
            max(
                0,
                sum(
                    r["resource"] == resource
                    and any(a <= t < b for a, b in r["intervals"])
                    and not any(a <= t < b for a, b in x["maintenance"].get(resource, []))
                    for r in x["requests"]
                )
                - capacity,
            )
            for t in range(h)
        ]
    best = None
    for count in range(policy["limit"] + 1):
        for starts in itertools.combinations(range(h - w + 1), count):
            if any(a + w > b for a, b in zip(starts, starts[1:], strict=False)):
                continue
            for modes in itertools.product(policy["modes"], repeat=count):
                cost = sum(mode["cost"] for mode in modes)
                if cost > policy["budget"]:
                    continue
                residual = 0
                for resource, values in curves.items():
                    for t, value in enumerate(values):
                        relief = sum(
                            mode["relief"].get(resource, 0)
                            for start, mode in zip(starts, modes, strict=True)
                            if start <= t < start + w
                        )
                        residual += max(0, value - relief) * policy["weights"][resource]
                plan = tuple((start, mode["id"]) for start, mode in zip(starts, modes, strict=True))
                key = (residual, cost, count, plan)
                if best is None or key < best:
                    best = key
    return {
        "residual": best[0],
        "cost": best[1],
        "windows": [[start, start + w, mode] for start, mode in best[3]],
    }


@pytest.mark.parametrize("case", [0, 1, 2, 4, 7, 10, 14])
def test_483_independent_integer_cell_scheduling_oracle(case):
    x, expected = v.PROGRAM_CASES[483][case]
    assert mitigation_cells_oracle(x) == expected["mitigation"]


def test_483_budget_and_nonoverlap_matter_and_scale_is_exact():
    x, expected = v.PROGRAM_CASES[483][0]
    no_budget = copy.deepcopy(x)
    no_budget["mitigation"]["budget"] = 0
    assert gold(483)(no_budget)["mitigation"]["residual"] > expected["mitigation"]["residual"]
    scaled = copy.deepcopy(x)
    scale = 10**9
    scaled["horizon"] *= scale
    scaled["window"] *= scale
    scaled["mitigation"]["width"] *= scale
    for intervals in scaled["maintenance"].values():
        for pair in intervals:
            pair[:] = [t * scale for t in pair]
    for request in scaled["requests"]:
        for pair in request["intervals"]:
            pair[:] = [t * scale for t in pair]
    result = gold(483)(scaled)["mitigation"]
    assert result["residual"] == expected["mitigation"]["residual"] * scale
    assert result["windows"] == [
        [a * scale, b * scale, m] for a, b, m in expected["mitigation"]["windows"]
    ]


def test_484_independent_generated_macro_values_and_lazy_static_boundary():
    for n in range(1, 16):
        x = {
            "definitions": [
                {"name": "f", "params": ["x"], "expr": "let(x,$x~, $x[0:3] | 9)"},
                {"name": "g", "params": ["x"], "expr": "@f($x) \\ 2"},
            ],
            "expr": f"@g(1..{n})",
        }
        values = list(range(1, n + 1))[::-1][:3]
        if 9 not in values:
            values.append(9)
        assert gold(484)(x) == {"values": [v for v in values if v != 2]}
    assert gold(484)({"definitions": [], "expr": "ifempty(1,1..3/0,7)"}) == {"values": [7]}
    assert gold(484)({"definitions": [], "expr": "ifempty(1,7,1..3/0)"}) == {"error": "invalid"}
    assert gold(484)({"definitions": [], "expr": "ifempty(1,$missing,7)"}) == {"error": "invalid"}
    assert gold(484)(
        {
            "definitions": [{"name": "f", "params": [], "expr": "$outer"}],
            "expr": "let(outer,7,@f())",
        }
    ) == {"error": "invalid"}


def test_484_diamond_call_graph_is_memoized():
    x, expected = v.PROGRAM_CASES[484][-4]
    assert len(x["definitions"]) == 29
    assert expected == {"values": list(range(42, 32, -1))}


def list_rope_oracle(x):
    versions = [[]]
    sizes = []
    for op in x["ops"]:
        seq = versions[op["base"]][:]
        kind = op["op"]
        if kind in ("add", "move", "remove"):
            a, b = op["a"], op["b"]
            lo, hi = sorted((a, b))
            if kind == "add":
                old = set(seq)
                seq.extend(
                    v
                    for v in range(a, b + (1 if a <= b else -1), 1 if a <= b else -1)
                    if v not in old
                )
            else:
                seq = [v for v in seq if not lo <= v <= hi] + (
                    [v for v in seq if lo <= v <= hi] if kind == "move" else []
                )
        elif kind == "reverse":
            seq.reverse()
        elif kind == "splice":
            part = versions[op["source"]][op["start"] : op["stop"]]
            if op["reverse"]:
                part.reverse()
            seq = seq[: op["at"]] + part + seq[op["at"] + op["delete"] :]
        else:
            a, b = op["start"], op["stop"]
            part = seq[a:b]
            shift = op["by"] % len(part) if part else 0
            seq = seq[:a] + (part[-shift:] + part[:-shift] if shift else part) + seq[b:]
        versions.append(seq)
        sizes.append(len(seq))
    answers = []
    for query in x["queries"]:
        seq = versions[query["version"]]
        kind = query["op"]
        if kind == "rank":
            answers.append(seq.index(query["value"]) if query["value"] in seq else None)
        elif kind == "select":
            answers.append(seq[query["index"]] if 0 <= query["index"] < len(seq) else None)
        else:
            part = seq[max(0, query["start"]) : max(0, query["stop"])]
            answers.append(
                sum(part)
                if kind == "sum"
                else sum(
                    value * pow(query["base"], i, query["mod"]) for i, value in enumerate(part)
                )
                % query["mod"]
            )
    return {"sizes": sizes, "answers": answers}


@pytest.mark.parametrize("case", range(20))
def test_485_independent_materialized_version_oracle(case):
    x, expected = v.PROGRAM_CASES[485][case]
    assert list_rope_oracle(x) == expected


def test_485_exponential_repeats_composite_hash_and_old_versions():
    ops = [{"base": 0, "op": "add", "a": 1, "b": 1}]
    for i in range(1, 61):
        ops.append(
            {
                "base": i,
                "op": "splice",
                "at": 1 << (i - 1),
                "delete": 0,
                "source": i,
                "start": 0,
                "stop": 1 << (i - 1),
                "reverse": bool(i % 2),
            }
        )
    n = 1 << 60
    x = {
        "ops": ops,
        "queries": [
            {"version": 61, "op": "sum", "start": 0, "stop": n},
            {"version": 61, "op": "hash", "start": 0, "stop": n, "base": 4, "mod": 12},
            {"version": 61, "op": "hash", "start": 0, "stop": n, "base": 2, "mod": 16},
            {"version": 1, "op": "sum", "start": 0, "stop": n},
        ],
    }
    assert gold(485)(x)["answers"] == [n, (1 + 4 * (n - 1)) % 12, 15, 1]
    assert gold(485)(x)["sizes"] == [1 << i for i in range(61)]


def test_485_duplicate_preservation_and_branch_isolation_mutants():
    x = {
        "ops": [
            {"base": 0, "op": "add", "a": 1, "b": 3},
            {
                "base": 1,
                "op": "splice",
                "at": 1,
                "delete": 0,
                "source": 1,
                "start": 0,
                "stop": 3,
                "reverse": True,
            },
            {"base": 1, "op": "remove", "a": 2, "b": 2},
        ],
        "queries": [
            {"version": 2, "op": "sum", "start": 0, "stop": 99},
            {"version": 2, "op": "rank", "value": 3},
            {"version": 1, "op": "select", "index": 1},
            {"version": 3, "op": "select", "index": 1},
        ],
    }
    assert gold(485)(x) == list_rope_oracle(x)
    assert gold(485)(x)["answers"] == [12, 1, 2, 3]


def session_cartesian_oracle(rows, policy, queries, restriction):
    hosts = sorted(policy["clocks"])
    best = None
    winner = None
    for clocks in itertools.product(*(policy["clocks"][h] for h in hosts)):
        offsets = {h: c["offset"] for h, c in zip(hosts, clocks, strict=True)}
        if any(offsets[h] != value for h, value in restriction["clock_pin"].items()):
            continue
        if any(
            not r["min"] <= offsets[r["left"]] - offsets[r["right"]] <= r["max"]
            for r in policy["relations"]
        ):
            continue
        choices = []
        views = []
        for query in queries:
            starts, ends, _, _ = d._reconstruction_view(rows, policy, query, offsets)
            views.append(starts)
            possible = []
            for vector in itertools.product([None, *sorted(ends)], repeat=len(starts)):
                pairs = dict(
                    (s, f) for s, f in zip(sorted(starts), vector, strict=True) if f is not None
                )
                if len(set(pairs.values())) != len(pairs):
                    continue
                if any(
                    policy["sessions"][s]["label"] not in ends[f]["accept"]
                    or not policy["sessions"][s]["min"]
                    <= ends[f]["at"] - starts[s]["at"]
                    <= policy["sessions"][s]["max"]
                    for s, f in pairs.items()
                ):
                    continue
                if any(
                    parent not in pairs or ends[pairs[parent]]["at"] > starts[s]["at"]
                    for s in pairs
                    for parent in policy["sessions"][s]["requires"]
                ):
                    continue
                spans = [
                    (starts[s]["at"], ends[f]["at"], policy["sessions"][s]["resource"])
                    for s, f in pairs.items()
                ]
                if any(
                    sum(a <= t < b and r == resource for a, b, r in spans) > cap
                    for resource, cap in policy["capacity"].items()
                    for t in range(-10, 20)
                ):
                    continue
                if any(
                    p["snapshot"] == query["name"] and pairs.get(p["start"]) != p["finish"]
                    for p in restriction["require"]
                ):
                    continue
                if any(
                    p["snapshot"] == query["name"] and pairs.get(p["start"]) == p["finish"]
                    for p in restriction["forbid"]
                ):
                    continue
                cost = sum(
                    policy["sessions"][s]["weight"]
                    * abs(ends[f]["at"] - starts[s]["at"] - policy["sessions"][s]["target"])
                    for s, f in pairs.items()
                )
                possible.append((pairs, cost))
            choices.append(possible)
        for history in itertools.product(*choices):
            pairs = sum(len(p) for p, c in history)
            cost = sum(c for p, c in history) + sum(c["cost"] for c in clocks)
            changes = 0
            for i in range(1, len(history)):
                for key in set(views[i - 1]) & set(views[i]):
                    if history[i - 1][0].get(key) != history[i][0].get(key):
                        changes += 1
                        cost += policy["sessions"][key]["change_cost"]
            signature = tuple(tuple(sorted(p.items())) for p, c in history)
            key = (-pairs, cost, changes, tuple(offsets[h] for h in hosts), signature)
            if best is None or key < best:
                best = key
                winner = offsets
    return (
        None
        if best is None
        else {
            "pairs": -best[0],
            "cost": best[1],
            "changes": best[2],
            "clock_offsets": winner,
            "history": [[list(p) for p in s] for s in best[4]],
        }
    )


def test_474_counterfactual_oracle_reoptimizes_cardinality_and_detects_infeasibility():
    rows = [
        {
            "id": key,
            "kind": kind,
            "host": host,
            "local": at,
            "rev": 1,
            "seen": 1,
            "op": "put",
            **({"accept": ["k"]} if kind == "finish" else {}),
        }
        for key, kind, host, at in [
            ("s", "start", "c", 0),
            ("t", "start", "c", 3),
            ("x", "finish", "w", 2),
            ("y", "finish", "w", 5),
        ]
    ]
    rule = {
        "label": "k",
        "resource": "r",
        "min": 0,
        "max": 10,
        "target": 2,
        "weight": 1,
        "change_cost": 3,
        "requires": [],
    }
    policy = {
        "clocks": {
            "c": [{"offset": 0, "cost": 0}],
            "w": [{"offset": 0, "cost": 0}, {"offset": 1, "cost": 1}],
        },
        "relations": [],
        "capacity": {"r": 1},
        "sessions": {"s": rule, "t": {**rule, "requires": ["s"]}},
    }
    queries = [
        {"name": "first", "seen": 1, "horizon": 9},
        {"name": "second", "seen": 2, "horizon": 9},
    ]
    restrictions = [
        {"id": "none", "require": [], "forbid": [], "clock_pin": {}},
        {
            "id": "ban",
            "require": [],
            "forbid": [{"snapshot": "first", "start": "t", "finish": "y"}],
            "clock_pin": {},
        },
        {
            "id": "impossible",
            "require": [
                {"snapshot": "first", "start": "s", "finish": "x"},
                {"snapshot": "first", "start": "t", "finish": "x"},
            ],
            "forbid": [],
            "clock_pin": {},
        },
        {"id": "clock", "require": [], "forbid": [], "clock_pin": {"w": 1}},
    ]
    actual = v.session_frontier(d, rows, policy, queries, restrictions)
    assert actual == [
        {"id": r["id"], "result": session_cartesian_oracle(rows, policy, queries, r)}
        for r in restrictions
    ]
    assert actual[0]["result"]["pairs"] == 4 and actual[1]["result"]["pairs"] == 3
    assert actual[2]["result"] is None


def order_product_oracle(report, policy):
    catalog = {r["sku"]: r for r in report["catalog"]}
    orders = sorted(policy["orders"], key=lambda o: o["id"])
    choices = []
    for order in orders:
        choices.append(
            [None]
            + [
                (wh, variant)
                for wh in order["warehouses"]
                for variant in policy["alternatives"].get(
                    order["sku"],
                    [
                        {
                            "id": "original",
                            "requirements": catalog[order["sku"]]["requirements"],
                            "fee": 0,
                        }
                    ],
                )
            ]
            if order["sku"] in catalog
            else [None]
        )
    best = None
    remaining = None
    for vector in itertools.product(*choices):
        stock = {(b["warehouse"], b["sku"]): b["available"] for b in report["bins"]}
        selected = {o["id"] for o, pick in zip(orders, vector, strict=True) if pick}
        used = {}
        net = 0
        priority = 0
        assign = []
        if any(o["id"] in selected and not set(o["requires"]) <= selected for o in orders) or any(
            len(selected & set(group)) > 1 for group in policy["exclusive"]
        ):
            continue
        for order, pick in zip(orders, vector, strict=True):
            if pick is None:
                continue
            wh, variant = pick
            priority += order["priority"]
            net += (catalog[order["sku"]]["price_cents"] - variant["fee"]) * order["qty"]
            assign.append((order["id"], wh, variant["id"]))
            for leaf, qty in variant["requirements"].items():
                stock[(wh, leaf)] = stock.get((wh, leaf), 0) - qty * order["qty"]
                used[wh] = used.get(wh, 0) + qty * order["qty"]
        if any(qty < 0 for qty in stock.values()) or any(
            qty > policy["capacity"][wh] for wh, qty in used.items()
        ):
            continue
        net -= sum(policy["activation"][wh] for wh in used)
        key = (-priority, -net, len(used), tuple(assign))
        if best is None or key < best:
            best = key
            remaining = stock
    return {
        "priority": -best[0],
        "net_cents": -best[1],
        "warehouses": best[2],
        "assignments": [list(a) for a in best[3]],
        "remaining": [
            {"warehouse": wh, "sku": sku, "qty": qty}
            for (wh, sku), qty in sorted(remaining.items())
        ],
    }


def test_476_atomic_substitutions_activation_capacity_oracle():
    report = {
        "bins": [
            {"warehouse": wh, "sku": sku, "available": qty}
            for wh, sku, qty in [
                ("east", "A", 2),
                ("east", "B", 2),
                ("west", "A", 1),
                ("west", "B", 2),
            ]
        ],
        "catalog": [
            {"sku": sku, "price_cents": price, "requirements": req}
            for sku, price, req in [("A", 10, {"A": 1}), ("B", 20, {"B": 1}), ("K", 40, {"A": 2})]
        ],
    }
    policy = {
        "activation": {"east": 4, "west": 7},
        "capacity": {"east": 3, "west": 4},
        "orders": [
            {
                "id": key,
                "sku": sku,
                "qty": qty,
                "priority": priority,
                "warehouses": ["east", "west"],
                "requires": ["o2"] if key == "o0" else [],
            }
            for key, sku, qty, priority in [("o0", "K", 1, 7), ("o1", "A", 1, 3), ("o2", "B", 2, 5)]
        ],
        "exclusive": [["o0", "o1"]],
        "alternatives": {
            "K": [
                {"id": "original", "requirements": {"A": 2}, "fee": 0},
                {"id": "sub", "requirements": {"B": 1}, "fee": 3},
            ]
        },
    }
    result = v.warehouse_orders(report, policy)
    assert result == order_product_oracle(report, policy)
    relaxed = copy.deepcopy(policy)
    relaxed["capacity"] = {"east": 99, "west": 99}
    relaxed["activation"] = {"east": 0, "west": 99}
    assert v.warehouse_orders(report, relaxed) == order_product_oracle(report, relaxed)
    assert v.warehouse_orders(report, relaxed)["assignments"] != result["assignments"]
    no_substitutes = copy.deepcopy(policy)
    no_substitutes["alternatives"]["K"] = no_substitutes["alternatives"]["K"][:1]
    assert v.warehouse_orders(report, no_substitutes) == order_product_oracle(
        report, no_substitutes
    )


def restore_flat_oracle(shards, policy):
    best = None
    selected = None
    for picks in itertools.product([None, "high", "low"], repeat=len(shards)):
        rows = []
        loads = {}
        donors = set()
        valid = True
        for shard, pick in zip(shards, picks, strict=True):
            value = json.loads(shard["files"][pick]) if pick else None
            rows.append(value)
            if pick:
                donor = policy["segments"][shard["id"] + "/" + pick]["donor"]
                donors.add(donor)
                loads[donor] = loads.get(donor, 0) + 1
        if any(loads.get(d, 0) > record["capacity"] for d, record in policy["donors"].items()):
            continue
        cost = sum(policy["donors"][donor]["cost"] for donor in donors)
        if cost > policy["budget"]:
            continue
        for check in policy["checks"]:
            a, b = rows[check["left"]], rows[check["right"]]
            if a and b and a["records"][0]["value"] != b["records"][0]["value"]:
                valid = False
        if not valid:
            continue
        coverage = [1 if r else 0 for r in rows]
        generation = sum(r["generation"] for r in rows if r)
        files = tuple((pick,) if pick else () for pick in picks)
        key = (-min(coverage), -sum(coverage), -generation, cost, sum(coverage), files)
        if best is None or key < best:
            best = key
            selected = picks
    return {
        "minimum_covered": -best[0],
        "total_covered": -best[1],
        "generation_sum": -best[2],
        "cost": best[3],
        "selected": [
            {"shard": s["id"], "files": [p] if p else [], "covered": int(p is not None)}
            for s, p in zip(shards, selected, strict=True)
        ],
    }


def test_478_shared_donors_and_payload_checkpoints_prevent_independent_winners():
    files = {
        name: json.dumps(
            {
                "start": 0,
                "end": 0,
                "parent": "ROOT",
                "digest": name,
                "generation": generation,
                "records": [{"seq": 0, "value": name}],
            }
        )
        for name, generation in [("high", 10), ("low", 1)]
    }
    shards = [
        {"id": f"s{i}", "files": files, "audit": {"invalid": [], "duplicates": []}}
        for i in range(3)
    ]
    policy = {
        "donors": {"x": {"capacity": 1, "cost": 3}, "y": {"capacity": 3, "cost": 1}},
        "budget": 4,
        "segments": {
            f"s{i}/{name}": {"donor": "x" if name == "high" else "y"}
            for i in range(3)
            for name in files
        },
        "checks": [{"left": 0, "right": 1, "seq": 0}],
    }
    result = v.restore_shards(shards, policy)
    assert result == restore_flat_oracle(shards, policy)
    assert [r["files"] for r in result["selected"]] == [["low"], ["low"], ["high"]]
    no_checks = copy.deepcopy(policy)
    no_checks["checks"] = []
    assert [r["files"] for r in v.restore_shards(shards, no_checks)["selected"]] == [
        ["high"],
        ["low"],
        ["low"],
    ]
    assert v.restore_shards(shards, no_checks) == restore_flat_oracle(shards, no_checks)


def survival_unit_time_oracle(reports, policy):
    def q(value):
        return [value.numerator, value.denominator]

    result = []
    for snapshot in reports:
        cohorts = []
        for endpoint in ["*"] + sorted({r["endpoint"] for r in snapshot["requests"]}):
            rows = [r for r in snapshot["requests"] if endpoint == "*" or r["endpoint"] == endpoint]
            excluded = sorted(
                r["request"]
                for r in rows
                if policy["subjects"][r["request"]]["entry"] > r["latency"]
            )
            rows = [r for r in rows if r["request"] not in excluded]
            survival = Fraction(1)
            cif = dict.fromkeys(["complete", "failed", "cancelled"], Fraction(0))
            area = Fraction(0)
            points = []
            median = None
            for t in range(policy["tau"] + 1):
                if t:
                    area += survival
                risk = sum(
                    (
                        Fraction(*policy["subjects"][r["request"]]["weight"])
                        for r in rows
                        if policy["subjects"][r["request"]]["entry"] <= t <= r["latency"]
                    ),
                    Fraction(0),
                )
                events = {
                    cause: sum(
                        (
                            Fraction(*policy["subjects"][r["request"]]["weight"])
                            for r in rows
                            if r["latency"] == t and r["status"] == cause
                        ),
                        Fraction(0),
                    )
                    for cause in cif
                }
                censor = sum(
                    (
                        Fraction(*policy["subjects"][r["request"]]["weight"])
                        for r in rows
                        if r["latency"] == t and r["status"] in ("open", "timed_out")
                    ),
                    Fraction(0),
                )
                before = survival
                if risk:
                    for cause, weight in events.items():
                        cif[cause] += before * weight / risk
                    survival = before * (risk - sum(events.values())) / risk
                if median is None and survival <= Fraction(1, 2):
                    median = t
                if t == policy["tau"] or any(r["latency"] == t for r in rows):
                    points.append(
                        {
                            "time": t,
                            "risk": q(risk),
                            "events": {c: q(w) for c, w in events.items()},
                            "censored": q(censor),
                            "survival": q(survival),
                            "incidence": {c: q(w) for c, w in cif.items()},
                        }
                    )
            cohorts.append(
                {
                    "endpoint": endpoint,
                    "excluded": excluded,
                    "points": points,
                    "restricted_mean": q(area),
                    "median": median,
                }
            )
        result.append({"snapshot": snapshot["snapshot"], "cohorts": cohorts})
    return result


def test_482_exact_competing_risks_and_delayed_entry_unit_time_oracle():
    reports = [
        {
            "snapshot": "q",
            "requests": [
                {"request": key, "endpoint": "/x", "latency": latency, "status": status}
                for key, latency, status in [
                    ("a", 2, "complete"),
                    ("b", 2, "timed_out"),
                    ("c", 4, "failed"),
                    ("d", 1, "open"),
                    ("e", 3, "cancelled"),
                ]
            ],
        }
    ]
    policy = {
        "tau": 5,
        "subjects": {
            "a": {"entry": 0, "weight": [1, 2]},
            "b": {"entry": 0, "weight": [3, 2]},
            "c": {"entry": 2, "weight": [2, 1]},
            "d": {"entry": 2, "weight": [1, 1]},
            "e": {"entry": 3, "weight": [1, 1]},
        },
    }
    result = v.survival_report(reports, policy)
    assert result == survival_unit_time_oracle(reports, policy)
    cohort = result[0]["cohorts"][0]
    assert cohort["excluded"] == ["d"]
    first = cohort["points"][0]
    assert first["risk"] == [4, 1] and first["survival"] == [7, 8] and first["censored"] == [3, 2]
    censor_as_event = copy.deepcopy(reports)
    censor_as_event[0]["requests"][1]["status"] = "failed"
    assert v.survival_report(censor_as_event, policy) != result
    without_entry = copy.deepcopy(policy)
    without_entry["subjects"]["c"]["entry"] = 3
    assert v.survival_report(reports, without_entry) != result


@pytest.mark.parametrize("case", range(22))
def test_485_every_edit_respects_declared_version_and_index_bounds(case):
    """Independent length algebra; do not trust Python slicing's silent clipping."""
    x, expected = v.PROGRAM_CASES[485][case]
    assert len(x["ops"]) <= 100 and len(x["queries"]) <= 100
    if case < 20:
        # Materialization is bounded here and independent of the rope reference.
        sizes = [0] + list_rope_oracle(x)["sizes"]
    else:
        sizes = [0]
        initial = x["ops"][0]
        unit = abs(initial["b"] - initial["a"]) + 1
        for i, op in enumerate(x["ops"]):
            assert 0 <= op["base"] <= i
            n = sizes[op["base"]]
            if op["op"] == "add":
                assert i == 0 and n == 0
                n = unit
            elif op["op"] == "splice":
                n += op["stop"] - op["start"] - op["delete"]
            elif op["op"] == "remove":
                # The huge fixture has only whole-version doubling followed by
                # permutations before this remove: each value has same count.
                assert case == 20 and i == 22
                assert all(
                    e["source"] == e["base"]
                    and e["start"] == 0
                    and e["stop"] == sizes[e["base"]]
                    and e["delete"] == 0
                    for e in x["ops"][1:20]
                )
                lo, hi = sorted((op["a"], op["b"]))
                overlap = max(0, min(hi, initial["b"]) - max(lo, initial["a"]) + 1)
                assert n % unit == 0
                n -= overlap * (n // unit)
            else:
                assert op["op"] in ("rotate", "move", "reverse")
            sizes.append(n)
    assert sizes[1:] == expected["sizes"]
    for i, op in enumerate(x["ops"]):
        assert 0 <= op["base"] <= i
        n = sizes[op["base"]]
        assert 0 <= sizes[i + 1] <= 2**60
        if op["op"] == "splice":
            assert 0 <= op["source"] <= i
            assert 0 <= op["start"] <= op["stop"] <= sizes[op["source"]]
            assert 0 <= op["at"] <= n and 0 <= op["delete"] <= n - op["at"]
        if op["op"] == "rotate":
            assert 0 <= op["start"] <= op["stop"] <= n
        if op["op"] in ("add", "move", "remove"):
            assert abs(op["a"]) <= 10**12 and abs(op["b"]) <= 10**12
    for query in x["queries"]:
        assert 0 <= query["version"] <= len(x["ops"])
        if query["op"] == "hash":
            assert 0 <= query["base"] <= 10**9 and 2 <= query["mod"] <= 2**31
    if case == 21:
        assert sizes[30:] == [536870912000000] * 71
        assert x["ops"][30]["source"] == 30


def test_485_v5_validity_revision_keeps_public_contract():
    task = next(task for task in d.TASKS if task.id.startswith("task_485_"))
    assert {"hardening-v4", "hardening-v5"} <= set(task.tags)
    assert "0<=start<=stop<=соответствующая длина" in task.prompt
