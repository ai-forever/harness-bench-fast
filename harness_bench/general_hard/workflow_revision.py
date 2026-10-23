"""Semantic hardening of wave-20 workflows after the first Sonnet calibration.

This module contains evaluator-side reference algorithms, not task workspaces.
Legacy solvers are supplied explicitly by workflow_tasks to retain regression
coverage while emitting completely standalone gold programs.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import itertools
import json
import math
import posixpath
import random
import unicodedata
from fractions import Fraction
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness_bench.general_hard.workflow_tasks import (
        _allocate_503,
        _expand_507,
        _fraction_503,
        _norm_504,
        _path_501,
        _sha,
        _solve_500,
        _solve_501,
        _solve_502,
        _solve_504,
        _solve_506,
        _solve_511,
        _spans_506,
    )


def _revision_500(data):
    policy = data["policy"]
    names = sorted(policy["candidates"])
    materialized = {
        name: _solve_500({**data, "keep": [name]}) for name in names
    }
    blobs = {}
    masks = []
    blob_ids = {}
    for name in names:
        mask = 0
        for row in materialized[name]["snapshots"][0]["files"]:
            digest = row["sha256"]
            blob_ids.setdefault(digest, len(blob_ids))
            mask |= 1 << blob_ids[digest]
            blobs[digest] = row["size"]
        masks.append(mask)
    sizes = [blobs[digest] for digest in blob_ids]
    existing = set(data["existing"])
    upload_sizes = [0 if digest in existing else blobs[digest] for digest in blob_ids]
    positions = {name: i for i, name in enumerate(names)}

    def bits(items):
        return sum(1 << positions[name] for name in set(items))

    mandatory = bits(policy["mandatory"])
    implications = [(1 << positions[a], bits(bs)) for a, bs in policy["requires"].items()]
    groups = [(bits(row["members"]), row["min"], row["max"]) for row in policy["groups"]]
    best = None
    winner = None
    unions = [0] * (1 << len(names))
    rewards = [0] * len(unions)
    byte_cache = {0: (0, 0)}
    for chosen in range(len(unions)):
        if chosen:
            bit = chosen & -chosen
            index = bit.bit_length() - 1
            rest = chosen ^ bit
            unions[chosen] = unions[rest] | masks[index]
            rewards[chosen] = rewards[rest] + policy["candidates"][names[index]]
        if chosen & mandatory != mandatory:
            continue
        if any(chosen & a and chosen & bs != bs for a, bs in implications):
            continue
        if any(not low <= (chosen & group).bit_count() <= high for group, low, high in groups):
            continue
        union = unions[chosen]
        if union not in byte_cache:
            byte_cache[union] = (
                sum(size for i, size in enumerate(sizes) if union & (1 << i)),
                sum(size for i, size in enumerate(upload_sizes) if union & (1 << i)),
            )
        physical, upload = byte_cache[union]
        if physical > policy["max_physical"] or upload > policy["max_upload"]:
            continue
        retained = [name for i, name in enumerate(names) if chosen & (1 << i)]
        score = (-rewards[chosen], upload, physical, len(retained), retained)
        if best is None or score < best:
            best, winner = score, retained
    if winner is None:
        return {"feasible": False}
    return {
        "feasible": True, "retained": winner, "utility": -best[0], "upload_bytes": best[1],
        **_solve_500({**data, "keep": winner}),
    }


def _revision_501(data):
    initial = _solve_501({"entries": data["entries"]})
    if "error" in initial:
        return {"initial_error": initial["error"]}
    original = {_path_501(row["path"]): row for row in data["entries"]}
    nodes, contents = {"": ("dir", None)}, {}
    for row in initial["entries"]:
        if row["kind"] == "dir":
            nodes[row["path"]] = ("dir", None)
        elif row["kind"] == "symlink":
            nodes[row["path"]] = ("symlink", original[row["path"]]["target"])
    for identity, group in enumerate(initial["hardlink_groups"]):
        root = next(path for path in group if original[path]["kind"] == "file")
        contents[identity] = original[root]["text"]
        for path in group:
            nodes[path] = ("file", identity)
    next_identity = len(contents)

    def walk(path, state, follow=True):
        pending = path.split("/")
        parts = []
        hops = 0
        while pending:
            raw = pending.pop(0)
            if raw in ("", "."):
                continue
            if raw == "..":
                if not parts:
                    raise ValueError("escape")
                parts.pop()
                continue
            part = unicodedata.normalize("NFC", unicodedata.normalize("NFC", raw).casefold())
            key = "/".join([*parts, part])
            if key not in state:
                raise ValueError("missing")
            kind, value = state[key]
            if kind == "symlink" and (follow or pending):
                hops += 1
                if hops > 40:
                    raise ValueError("loop")
                if value.startswith("/"):
                    parts = []
                pending = value.split("/") + pending
                continue
            if pending and kind != "dir":
                raise ValueError("not-dir")
            parts.append(part)
        return "/".join(parts)

    def destination(path, state):
        normalized = _path_501(path)
        parent, _, base = normalized.rpartition("/")
        resolved = walk(parent, state) if parent else ""
        if state[resolved][0] != "dir":
            raise ValueError("not-dir")
        return f"{resolved}/{base}" if resolved else base

    audit = []
    for batch in data["transactions"]:
        staged, text = dict(nodes), dict(contents)
        counter = next_identity
        error, failed = None, None
        for index, op in enumerate(batch["ops"], 1):
            try:
                kind = op["op"]
                if kind in ("create", "mkdir", "symlink", "link", "rename"):
                    target = destination(op["path"], staged)
                    if target in staged:
                        raise ValueError("exists")
                    if kind == "create":
                        staged[target], text[counter] = ("file", counter), op["text"]
                        counter += 1
                    elif kind in ("mkdir", "symlink"):
                        staged[target] = (kind if kind == "symlink" else "dir", op.get("target"))
                    else:
                        source = walk(op["source"], staged, follow=kind == "link")
                        if kind == "link":
                            if staged[source][0] != "file":
                                raise ValueError("not-file")
                            staged[target] = staged[source]
                        else:
                            if not source or target.startswith(source + "/"):
                                raise ValueError("subtree")
                            moved = {
                                key: value for key, value in staged.items()
                                if key == source or key.startswith(source + "/")
                            }
                            for key in moved:
                                del staged[key]
                            staged.update({target + key[len(source):]: value
                                           for key, value in moved.items()})
                else:
                    source = walk(op["path"], staged, follow=kind == "write")
                    if kind == "write":
                        if staged[source][0] != "file":
                            raise ValueError("not-file")
                        text[staged[source][1]] = op["text"]
                    else:
                        if not source or any(key.startswith(source + "/") for key in staged):
                            raise ValueError("not-empty")
                        del staged[source]
            except ValueError as exc:
                error, failed = str(exc), index
                break
        if error is None:
            nodes, contents, next_identity = staged, text, counter
        audit.append({"id": batch["id"], "error": error, "failed_op": failed})
    physical = {}
    entries = []
    for path, (kind, value) in sorted(nodes.items()):
        if not path:
            continue
        row = {"path": path, "kind": kind}
        if kind == "file":
            if value in physical:
                row.update(kind="hardlink", target=physical[value])
            else:
                physical[value] = path
                row["text"] = contents[value]
        elif kind == "symlink":
            row["target"] = value
        entries.append(row)
    archive = _solve_501({"entries": entries})
    digests = {row["path"]: row["sha256"] for row in archive["entries"]}
    digests[""] = archive["root"]
    queries = []
    for query in data["queries"]:
        try:
            path = walk(query["path"], nodes, query["follow"])
            queries.append({"resolved": path, "kind": nodes[path][0], "sha256": digests[path]})
        except ValueError as exc:
            queries.append({"error": str(exc)})
    return {"archive": archive, "audit": audit, "queries": queries}


def _transport_502(payments, invoices, routes, minimums):
    pnames, inames = sorted(payments), sorted(invoices)
    n = len(pnames) + len(inames) + 2
    source, sink = n - 2, n - 1
    graph = [[] for _ in range(n)]
    zero = (0, 0, 0, 0, 0, 0)

    def edge(a, b, capacity, cost):
        forward = [b, len(graph[b]), capacity, cost, capacity]
        reverse = [a, len(graph[a]), 0, tuple(-v for v in cost), 0]
        graph[a].append(forward)
        graph[b].append(reverse)
        return forward

    total = sum(row["net"] for row in payments.values())
    base = total + 1
    decisions = {}
    for i, name in enumerate(pnames):
        edge(source, i, payments[name]["net"], zero)
        edge(i, sink, payments[name]["net"], zero)
    minima = {}
    for j, name in enumerate(inames, len(pnames)):
        low = minimums[name]
        minima[name] = edge(j, sink, low, (-1, 0, 0, 0, 0, 0))
        edge(j, sink, invoices[name]["amount"] - low, zero)
    ordered = sorted(routes, key=lambda row: (row["payment"], row["invoice"]))
    for k, route in enumerate(ordered):
        p, inv = route["payment"], route["invoice"]
        if p not in payments:
            continue
        payment, invoice = payments[p], invoices[inv]
        if (payment["customer"], payment["currency"]) != (
            invoice["customer"], invoice["currency"]
        ):
            continue
        cost = (
            0, -1, -invoice["priority"], -int(payment.get("invoice") == inv),
            route["fee"], base ** (len(ordered) - k - 1),
        )
        decisions[p, inv] = edge(pnames.index(p), len(pnames) + inames.index(inv),
                                 route["cap"], cost)
    remaining = total
    while remaining:
        distance, previous = [None] * n, [None] * n
        distance[source] = zero
        for _ in range(n - 1):
            changed = False
            for a, outgoing in enumerate(graph):
                if distance[a] is None:
                    continue
                for k, item in enumerate(outgoing):
                    if not item[2]:
                        continue
                    b = item[0]
                    candidate = tuple(x + y for x, y in zip(distance[a], item[3], strict=True))
                    if distance[b] is None or candidate < distance[b]:
                        distance[b], previous[b] = candidate, (a, k)
                        changed = True
            if not changed:
                break
        amount, at = remaining, sink
        while at != source:
            a, k = previous[at]
            amount = min(amount, graph[a][k][2])
            at = a
        at = sink
        while at != source:
            a, k = previous[at]
            item = graph[a][k]
            item[2] -= amount
            graph[at][item[1]][2] += amount
            at = a
        remaining -= amount
    if any(item[2] for item in minima.values()):
        return None
    return {key: item[4] - item[2] for key, item in decisions.items()}


def _revision_502(data):
    ledger = _solve_502(data)
    source = {row["id"]: row for row in data["events"] if row["kind"] == "payment"}
    payments = {row["id"]: {**source[row["id"]], "net": row["net"]}
                for row in ledger["payments"]}
    invoices = {row["id"]: row for row in data["invoices"]}
    allocation = _transport_502(payments, invoices, data["routes"], data["minimums"])
    common = {"inactive": ledger["inactive"], "refunds": ledger["refunds"]}
    if allocation is None:
        return {"feasible": False, **common}
    totals = {name: sum(value for (_, inv), value in allocation.items() if inv == name)
              for name in invoices}
    used = {name: sum(value for (p, _), value in allocation.items() if p == name)
            for name in payments}
    fees = {(row["payment"], row["invoice"]): row["fee"] for row in data["routes"]}
    return {
        "feasible": True, **common,
        "allocations": [{"payment": p, "invoice": i, "amount": allocation.get((p, i), 0)}
                        for p in sorted(payments) for i in sorted(invoices)],
        "invoices": [{"id": name, "paid": totals[name],
                      "balance": invoices[name]["amount"] - totals[name]} for name in sorted(invoices)],
        "payments": [{"id": name, "net": payments[name]["net"], "applied": used[name],
                      "credit": payments[name]["net"] - used[name]} for name in sorted(payments)],
        "paid_total": sum(totals.values()),
        "priority_total": sum(totals[name] * invoices[name]["priority"] for name in invoices),
        "earmark_total": sum(value for (p, i), value in allocation.items()
                            if payments[p].get("invoice") == i),
        "fee_total": sum(value * fees[key] for key, value in allocation.items()),
    }


def _native_503(data):
    invoices, exact = [], {}
    for invoice in sorted(data["invoices"], key=lambda row: row["id"]):
        lines = sorted(invoice["lines"], key=lambda row: row["id"])
        bases = {
            row["id"]: _fraction_503(row["unit"]) * row["qty"]
            / (1 + _fraction_503(row["tax"]) if row["inclusive"] else 1)
            for row in lines
        }
        weight = sum((max(value, 0) for value in bases.values()), Fraction())
        shares = {key: _fraction_503(invoice["discount"]) * max(value, 0) / weight
                  if weight else Fraction() for key, value in bases.items()}
        net = {key: (value - shares[key]) * invoice["scale"] for key, value in bases.items()}
        tax = {row["id"]: net[row["id"]] * _fraction_503(row["tax"]) for row in lines}
        net, tax = _allocate_503(net), _allocate_503(tax)
        for column in data["column_totals"]:
            minor = sum(net[row["id"]] + tax[row["id"]] for row in lines
                        if row["jurisdiction"] == column)
            exact[invoice["id"], column] = (
                Fraction(minor, invoice["scale"]) * _fraction_503(invoice["fx"]) * data["base_scale"]
            )
        invoices.append({
            "id": invoice["id"], "currency": invoice["currency"],
            "due_minor": sum(net.values()) + sum(tax.values()),
            "lines": [{"id": row["id"], "discount": str(shares[row["id"]].numerator)
                       + "/" + str(shares[row["id"]].denominator),
                       "net_minor": net[row["id"]], "tax_minor": tax[row["id"]],
                      "gross_minor": net[row["id"]] + tax[row["id"]]} for row in lines],
        })
    return invoices, exact


def _quota_round_503(exact, row_totals, column_totals, quotas):
    rows, cols = sorted(row_totals), sorted(column_totals)
    keys = [(row, col) for row in rows for col in cols]
    if sum(row_totals.values()) != sum(column_totals.values()):
        return {"feasible": False, "reason": "total"}
    if any(not sum(math.floor(exact[row, col]) for col in cols) <= row_totals[row]
               <= sum(math.ceil(exact[row, col]) for col in cols) for row in rows) or any(
        not sum(math.floor(exact[row, col]) for row in rows) <= column_totals[col]
        <= sum(math.ceil(exact[row, col]) for row in rows) for col in cols
    ):
        return {"feasible": False, "reason": "bounds"}
    quota_cells = [{tuple(cell) for cell in quota["cells"]} for quota in quotas]
    options = []
    for row in rows:
        choices = [sorted({math.floor(exact[row, col]), math.ceil(exact[row, col])}) for col in cols]
        local = []
        for values in itertools.product(*choices):
            if sum(values) != row_totals[row]:
                continue
            errors = [abs(value - exact[row, col]) for col, value in zip(cols, values, strict=True)]
            totals = tuple(sum(value for col, value in zip(cols, values, strict=True)
                               if (row, col) in cells) for cells in quota_cells)
            local.append((values, totals, max(errors, default=Fraction()),
                          sum((e * e for e in errors), Fraction())))
        options.append(local)
    thresholds = sorted({option[2] for group in options for option in group} | {Fraction()})
    bounds = []
    for position in range(len(rows) + 1):
        bounds.append((
            tuple(sum(min(option[0][j] for option in group) for group in options[position:])
                  for j in range(len(cols))),
            tuple(sum(max(option[0][j] for option in group) for group in options[position:])
                  for j in range(len(cols))),
            tuple(sum(min(option[1][q] for option in group) for group in options[position:])
                  for q in range(len(quotas))),
            tuple(sum(max(option[1][q] for option in group) for group in options[position:])
                  for q in range(len(quotas))),
        ))

    def optimize(threshold):
        @lru_cache(None)
        def visit(position, columns, accumulated):
            low, high, qlow, qhigh = bounds[position]
            if any(not a <= target <= b for a, target, b in zip(low, columns, high, strict=True)):
                return None
            if any(current + a > quota["max"] or current + b < quota["min"]
                   for current, a, b, quota in zip(accumulated, qlow, qhigh, quotas, strict=True)):
                return None
            if position == len(rows):
                return Fraction(), ()
            best = None
            for values, additions, worst, error in options[position]:
                if worst > threshold:
                    continue
                rest = visit(position + 1,
                             tuple(a - b for a, b in zip(columns, values, strict=True)),
                             tuple(a + b for a, b in zip(accumulated, additions, strict=True)))
                if rest is not None:
                    candidate = error + rest[0], values + rest[1]
                    if best is None or candidate < best:
                        best = candidate
            return best

        return visit(0, tuple(column_totals[col] for col in cols), (0,) * len(quotas))

    if not rows:
        if any(not quota["min"] <= 0 <= quota["max"] for quota in quotas):
            return {"feasible": False, "reason": "coupling"}
        return {"feasible": True, "allocation": {}, "maximum_error": Fraction(),
                "squared_error": Fraction()}
    left, right = 0, len(thresholds)
    while left < right:
        middle = (left + right) // 2
        if optimize(thresholds[middle]) is None:
            left = middle + 1
        else:
            right = middle
    if left == len(thresholds):
        return {"feasible": False, "reason": "coupling"}
    answer = optimize(thresholds[left])
    return {"feasible": True, "allocation": dict(zip(keys, answer[1], strict=True)),
            "maximum_error": thresholds[left], "squared_error": answer[0]}


def _revision_503(data):
    invoices, exact = _native_503(data)
    coupled = _quota_round_503(exact, data["row_totals"], data["column_totals"], data["quotas"])
    if not coupled["feasible"]:
        return coupled
    allocation = coupled["allocation"]

    def fraction(value):
        return f"{value.numerator}/{value.denominator}"

    for invoice in invoices:
        invoice["base_minor"] = data["row_totals"][invoice["id"]]
        invoice["allocations"] = [
            {"jurisdiction": col, "exact_base": fraction(exact[invoice["id"], col]),
             "base_minor": allocation[invoice["id"], col]} for col in sorted(data["column_totals"])
        ]
    return {
        "feasible": True, "invoices": invoices, "base_total": sum(data["row_totals"].values()),
        "columns": [{"jurisdiction": col, "base_minor": value}
                    for col, value in sorted(data["column_totals"].items())],
        "maximum_error": fraction(coupled["maximum_error"]),
        "squared_error": fraction(coupled["squared_error"]),
        "quotas": [{"id": row["id"], "total": sum(allocation[tuple(cell)]
                                                  for cell in row["cells"])}
                   for row in sorted(data["quotas"], key=lambda row: row["id"])],
    }


def _repair_aliases_504(aliases, forbidden):
    names = sorted({(row["tenant"], _norm_504(row[key]))
                    for row in [*aliases, *forbidden] for key in ("left", "right")})
    positions = {name: i for i, name in enumerate(names)}
    pairs = [(positions[row["tenant"], _norm_504(row["left"])],
              positions[row["tenant"], _norm_504(row["right"])]) for row in forbidden]
    if any(a == b for a, b in pairs):
        return None
    edges = sorted(aliases, key=lambda row: (-row["weight"], row["id"]))
    remaining = [0] * (len(edges) + 1)
    for i in range(len(edges) - 1, -1, -1):
        remaining[i] = remaining[i + 1] + edges[i]["weight"]
    best = None

    def visit(index, partition, weight, chosen):
        nonlocal best
        if best is not None and weight + remaining[index] < -best[0]:
            return
        if index == len(edges):
            removed = sorted(row["id"] for row in edges if row["id"] not in chosen)
            score = -weight, -len(chosen), removed
            if best is None or score < best:
                best = score
            return
        edge = edges[index]
        a = partition[positions[edge["tenant"], _norm_504(edge["left"])]]
        b = partition[positions[edge["tenant"], _norm_504(edge["right"])]]
        merged = tuple(a if value == b else value for value in partition)
        if all(merged[left] != merged[right] for left, right in pairs):
            visit(index + 1, merged, weight + edge["weight"], chosen | {edge["id"]})
        if not edge["required"] and a != b:
            visit(index + 1, partition, weight, chosen)

    visit(0, tuple(range(len(names))), 0, set())
    if best is None:
        return None
    return {"weight": -best[0], "removed": best[2],
            "kept": sorted(row["id"] for row in aliases if row["id"] not in best[2])}


def _revision_504(data):
    if data["version"] == 2:
        return {"ok": True, "state": copy.deepcopy(data), "repair": None}
    validation = _solve_504(data)
    if not validation["ok"]:
        return validation
    repair = _repair_aliases_504(data["aliases"], data["cannot_link"])
    if repair is None:
        return {"ok": False, "error": "identity-constraints", "state": copy.deepcopy(data)}
    staged = {**data, "aliases": [row for row in data["aliases"] if row["id"] in repair["kept"]]}
    migrated = _solve_504(staged)
    return {**migrated, "repair": repair}


def _timeline_505(records, cutoff):
    latest = {}
    for row in records:
        if row["recorded"] <= cutoff and (
            row["id"] not in latest or (row["recorded"], row["revision"])
            > (latest[row["id"]]["recorded"], latest[row["id"]]["revision"])
        ):
            latest[row["id"]] = row
    rows = [row for row in latest.values() if not row["deleted"]]
    entities = sorted({row["entity"] for row in records} |
                      {row["value"]["ref"] for row in records if isinstance(row["value"], dict)})
    solved = {}

    def at(segments, value):
        for row in segments:
            if (row["start"] is None or row["start"] <= value) and (
                row["end"] is None or value < row["end"]
            ):
                return row
        return None

    def resolve(entity):
        if entity in solved:
            return solved[entity]
        candidates = [row for row in rows if row["entity"] == entity]
        edges = {edge for row in candidates for edge in (row["start"], row["end"])
                 if edge is not None}
        for row in candidates:
            if isinstance(row["value"], dict):
                ref = row["value"]
                for segment in resolve(ref["ref"]):
                    edges.update(edge - ref["offset"] for edge in (segment["start"], segment["end"])
                                 if edge is not None)
        ordered = [None, *sorted(edges), None]
        output = []
        for left, right in zip(ordered, ordered[1:], strict=False):
            point = left if left is not None else (right - 1 if right is not None else 0)
            available = []
            for row in candidates:
                if row["start"] is not None and point < row["start"] or (
                    row["end"] is not None and point >= row["end"]
                ):
                    continue
                value, sources = row["value"], [row["id"]]
                if isinstance(value, dict):
                    inherited = at(resolve(value["ref"]), point + value["offset"])
                    if inherited is None:
                        continue
                    value, sources = inherited["value"], sorted({row["id"], *inherited["sources"]})
                available.append((row, value, sources))
            if not available:
                continue
            row, value, sources = max(available, key=lambda item: (
                item[0]["priority"], item[0]["recorded"], item[0]["revision"], item[0]["id"]
            ))
            # Masks remain in the internal timeline because a reference must
            # distinguish a missing parent from a parent's explicit tombstone.
            if output and output[-1]["end"] == left and output[-1]["value"] == value and (
                output[-1]["sources"] == sources
            ):
                output[-1]["end"] = right
            else:
                output.append({"entity": entity, "start": left, "end": right,
                               "value": value, "sources": sources})
        solved[entity] = output
        return output

    for entity in entities:
        resolve(entity)
    return solved


def _revision_505(data):
    views = {}
    for cutoff in sorted(set(data["cutoffs"]) | {row["asof"] for row in data["queries"]}):
        internal = _timeline_505(data["records"], cutoff)
        segments = []
        for entity in sorted(internal):
            for row in internal[entity]:
                if row["value"] is None:
                    continue
                if segments and segments[-1]["entity"] == entity and segments[-1]["end"] == row["start"] \
                        and segments[-1]["value"] == row["value"]:
                    segments[-1]["end"] = row["end"]
                    segments[-1]["sources"] = sorted(set(segments[-1]["sources"]) | set(row["sources"]))
                else:
                    segments.append(copy.deepcopy(row))
        views[cutoff] = segments
    answers = []
    for query in data["queries"]:
        found = next((row for row in views[query["asof"]] if row["entity"] == query["entity"]
                      and (row["start"] is None or row["start"] <= query["at"])
                      and (row["end"] is None or query["at"] < row["end"])), None)
        answers.append(found["value"] if found else None)
    return {"views": [{"asof": cutoff, "segments": views[cutoff]} for cutoff in sorted(views)],
            "answers": answers}


def _ini_selected_506(files, selector):
    matches = [(start, end) for section, key, start, end in _spans_506(files[selector["source"]])
               if section == selector["section"].casefold() and key == selector["key"].casefold()]
    return matches[selector["which"] - 1] if len(matches) >= selector["which"] else None


def _patch_plan_506(files, plan):
    for guard in plan["guards"]:
        span = _ini_selected_506(files, guard)
        observed = files[guard["source"]][span[0]:span[1]] if span else None
        if observed != guard["raw"]:
            return None
    edits = []
    for row in plan["edits"]:
        edit = dict(row)
        if "copy" in row:
            span = _ini_selected_506(files, row["copy"])
            if span is None:
                return None
            raw = files[row["copy"]["source"]][span[0]:span[1]]
            try:
                value = json.loads(raw)
            except ValueError:
                return None
            if not isinstance(value, str):
                return None
            edit["value"] = value
        edits.append(edit)
    changed, counts = dict(files), []
    for edit in edits:
        answer = _solve_506({"text": changed[edit["source"]], "edits": [edit]})
        changed[edit["source"]] = answer["text"]
        counts.extend(answer["applied"])
    return changed, counts


def _revision_506(data):
    plans = sorted(data["plans"], key=lambda row: row["id"])
    best, answer = None, None

    def visit(index, files, chosen, reward, cost, audit, changed_count):
        nonlocal best, answer
        if cost > data["budget"]:
            return
        if index == len(plans):
            selected = set(chosen)
            if any(row["mandatory"] and row["id"] not in selected for row in plans):
                return
            if any(row["id"] in selected and not set(row["requires"]) <= selected for row in plans):
                return
            if any(row["id"] in selected and set(row["excludes"]) & selected for row in plans):
                return
            if _patch_plan_506(files, {"guards": data["final"], "edits": []}) is None:
                return
            score = -reward, cost, changed_count, chosen
            if best is None or score < best:
                best = score
                answer = {"feasible": True, "chosen": list(chosen), "reward": reward,
                          "cost": cost, "replacements": changed_count, "files": dict(files),
                          "audit": copy.deepcopy(audit)}
            return
        plan = plans[index]
        if not plan["mandatory"]:
            visit(index + 1, files, chosen, reward, cost, audit, changed_count)
        if any(name in chosen for name in plan["excludes"]):
            return
        result = _patch_plan_506(files, plan)
        if result is not None:
            visit(index + 1, result[0], [*chosen, plan["id"]], reward + plan["reward"],
                  cost + plan["cost"], [*audit, {"id": plan["id"], "applied": result[1]}],
                  changed_count + sum(result[1]))

    visit(0, dict(data["files"]), [], 0, 0, [], 0)
    return answer if answer is not None else {"feasible": False}


def _revision_507(data):
    definitions, parents, profiles = {}, {}, {"global"}

    def visit(path, section, variables, active):
        if path in active:
            raise ValueError("include-cycle")
        if path not in data["files"]:
            raise ValueError("missing-file")
        variables = dict(variables)

        def early(name):
            namespace, key = name.split(":", 1)
            mapping = data["env"] if namespace == "ENV" else variables
            return key in mapping, mapping.get(key)

        for number, raw in enumerate(data["files"][path].splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("@let "):
                name, value = line[5:].split("=", 1)
                variables[name.strip()] = _expand_507(value.strip(), early)
            elif line.startswith("@include "):
                target = _expand_507(line[9:].strip(), early)
                if target.startswith("/"):
                    raise ValueError("path")
                target = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
                if target == ".." or target.startswith("../"):
                    raise ValueError("path")
                visit(target, section, variables, active | {path})
            elif line.startswith("["):
                section = line[1:-1]
                profiles.add(section)
            elif line.startswith("@extends"):
                parents[section] = line[len("@extends"):].split()
            else:
                key, value = line.split("=", 1)
                definitions[section, key.strip()] = (value.strip(), dict(variables), path, number)

    try:
        visit(data["entry"], "global", {}, set())
        for name in sorted(profiles):
            if any(parent not in profiles for parent in parents.get(name, [])):
                raise ValueError("unknown-parent")
        linear = {}

        def mro(name, active):
            if name in linear:
                return linear[name]
            if name in active:
                raise ValueError("inheritance-cycle")
            sequences = [list(mro(parent, active | {name})) for parent in parents.get(name, [])]
            sequences.append(list(parents.get(name, [])))
            result = [name]
            while any(sequences):
                candidate = next((seq[0] for seq in sequences if seq and all(
                    seq[0] not in other[1:] for other in sequences
                )), None)
                if candidate is None:
                    raise ValueError("mro")
                result.append(candidate)
                for seq in sequences:
                    if seq and seq[0] == candidate:
                        seq.pop(0)
            linear[name] = result
            return result

        for name in sorted(profiles):
            mro(name, set())
        resolved = {}

        def locate(receiver, key, start=0):
            return next((i for i in range(start, len(linear[receiver]))
                         if (linear[receiver][i], key) in definitions), None)

        def resolve(receiver, key, start, active):
            identity = receiver, key, start
            if identity in resolved:
                return resolved[identity]
            if identity in active:
                raise ValueError("cycle")
            position = locate(receiver, key, start)
            if position is None:
                raise ValueError("missing")
            owner = linear[receiver][position]
            template, variables, _, _ = definitions[owner, key]

            def lookup(name):
                if name.startswith(("ENV:", "VAR:")):
                    namespace, label = name.split(":", 1)
                    mapping = data["env"] if namespace == "ENV" else variables
                    return label in mapping, mapping.get(label)
                if name.startswith("super:"):
                    target, label, offset = receiver, name[6:], position + 1
                else:
                    target, label = name.split(".", 1) if "." in name else (receiver, name)
                    offset = 0
                if target not in linear or locate(target, label, offset) is None:
                    return False, None
                return True, resolve(target, label, offset, active | {identity})

            resolved[identity] = _expand_507(template, lookup)
            return resolved[identity]

        values, origins = {}, {}
        for profile in sorted(profiles):
            keys = sorted({key for owner, key in definitions if owner in linear[profile]})
            if not keys:
                continue
            values[profile], origins[profile] = {}, {}
            for key in keys:
                values[profile][key] = resolve(profile, key, 0, set())
                owner = linear[profile][locate(profile, key)]
                definition = definitions[owner, key]
                origins[profile][key] = f"{definition[2]}:{definition[3]}"
        return {"values": values, "origins": origins, "linearization": linear}
    except ValueError as exc:
        return {"error": str(exc)}


def _rebase_span_508(start, end, history):
    for left, right, size in history:
        if left == right:
            if start < left < end:
                raise ValueError("conflict")
            if left <= start:
                start += size
                end += size
        else:
            if start == end:
                if left < start < right:
                    raise ValueError("conflict")
                if start == left:
                    start = end = left + size
                elif start >= right:
                    start += size - (right - left)
                    end = start
            else:
                if max(start, left) < min(end, right):
                    raise ValueError("conflict")
                if right <= start:
                    start += size - (right - left)
                    end += size - (right - left)
    return start, end


def _revision_508(data):
    paths = {path: i for i, path in enumerate(sorted(data["files"]))}
    contents = {paths[path]: text for path, text in data["files"].items()}
    snapshots = [(dict(paths), dict(contents), 0)]
    history = []
    counter = len(paths)
    audit = []
    for batch in data["batches"]:
        base_paths, base_contents, base_history = snapshots[batch["base"]]
        staged_paths, staged_contents = dict(paths), dict(contents)
        staged_history = list(history)
        staged_counter = counter
        error, failed = None, None
        for path, expected in batch["guards"].items():
            found = base_contents[base_paths[path]] if path in base_paths else None
            if (found is not None if expected is None else found is None or _sha(found) != expected):
                error, failed = "guard", 0
                break
        if error is None:
            for index, op in enumerate(batch["ops"], 1):
                try:
                    if op["op"] == "create":
                        if op["path"] in staged_paths:
                            raise ValueError("exists")
                        staged_paths[op["path"]] = staged_counter
                        staged_contents[staged_counter] = op["text"]
                        staged_counter += 1
                        continue
                    if op["path"] not in base_paths:
                        raise ValueError("missing-base")
                    identity = base_paths[op["path"]]
                    current_path = next((path for path, item in staged_paths.items()
                                         if item == identity), None)
                    if current_path is None:
                        raise ValueError("deleted")
                    if op["op"] == "rename":
                        if op["target"] in staged_paths:
                            raise ValueError("exists")
                        del staged_paths[current_path]
                        staged_paths[op["target"]] = identity
                    elif op["op"] == "delete":
                        if any(item[0] == identity for item in staged_history[base_history:]):
                            raise ValueError("conflict")
                        del staged_paths[current_path]
                    else:
                        raw = base_contents[identity].encode()
                        spans = op["spans"]
                        for span in spans:
                            a, b = span["start"], span["end"]
                            if not 0 <= a <= b <= len(raw):
                                raise ValueError("range")
                            try:
                                raw[:a].decode()
                                raw[:b].decode()
                            except UnicodeDecodeError as exc:
                                raise ValueError("boundary") from exc
                        ordered = sorted(spans, key=lambda row: (row["start"], row["end"]))
                        if any(b["start"] < a["end"] or b["start"] == a["start"]
                               for a, b in zip(ordered, ordered[1:], strict=False)):
                            raise ValueError("overlap")
                        if any(raw[row["start"]:row["end"]] != row["old"].encode() for row in ordered):
                            raise ValueError("preimage")
                        prior = [item[1:] for item in staged_history[base_history:]
                                 if item[0] == identity]
                        mapped = [(*_rebase_span_508(row["start"], row["end"], prior), row["new"])
                                  for row in ordered]
                        current = staged_contents[identity].encode()
                        for a, b, replacement in reversed(mapped):
                            new = replacement.encode()
                            current = current[:a] + new + current[b:]
                            staged_history.append((identity, a, b, len(new)))
                        staged_contents[identity] = current.decode()
                except ValueError as exc:
                    error, failed = str(exc), index
                    break
        if error is None:
            for path, expected in batch["post"].items():
                found = staged_contents[staged_paths[path]] if path in staged_paths else None
                if (found is not None if expected is None else found is None or _sha(found) != expected):
                    error, failed = "post", len(batch["ops"]) + 1
                    break
        if error is None:
            paths, contents, history, counter = (
                staged_paths, staged_contents, staged_history, staged_counter
            )
            snapshots.append((dict(paths), dict(contents), len(history)))
        audit.append({"id": batch["id"], "committed": error is None,
                      "revision": len(snapshots) - 1, "error": error, "failed_op": failed})
    files = {path: contents[identity] for path, identity in sorted(paths.items())}
    return {"files": files, "hashes": {path: _sha(text) for path, text in files.items()},
            "revision": len(snapshots) - 1, "audit": audit}


def _serialization_edges_509(transactions):
    edges = set()
    for writer in transactions:
        for reader in transactions:
            if writer["name"] == reader["name"]:
                continue
            matching = any(
                key in reader["points"] or any(low <= key < high for low, high in reader["ranges"])
                for key in writer["writes"]
            )
            if matching and writer["commit"] > reader["begin"]:
                edges.add((reader["name"], writer["name"]))
            if writer["commit"] < reader["commit"] and (
                set(writer["writes"]) & set(reader["writes"])
            ):
                edges.add((writer["name"], reader["name"]))
        # A read-from dependency uses the most recent visible writer for each
        # observed point/predicate key, including a tombstone write.
    keys = {key for tx in transactions for key in tx["writes"]}
    for reader in transactions:
        for key in keys:
            if key not in reader["points"] and not any(
                low <= key < high for low, high in reader["ranges"]
            ):
                continue
            writers = [tx for tx in transactions if key in tx["writes"]
                       and tx["commit"] <= reader["begin"] and tx["name"] != reader["name"]]
            if writers:
                writer = max(writers, key=lambda tx: tx["commit"])
                edges.add((writer["name"], reader["name"]))
    return edges


def _revision_509(data):
    state, versions = dict(data["state"]), dict.fromkeys(data["state"], 0)
    active, committed = {}, []
    reads, completions = [], []
    clock = 0
    for index, event in enumerate(data["events"], 1):
        name, kind = event["tx"], event["op"]
        if kind == "begin":
            active[name] = {"name": name, "begin": clock, "snapshot": dict(state),
                            "versions": dict(versions), "writes": {}, "points": set(),
                            "ranges": set(), "savepoints": []}
            continue
        tx = active[name]
        if kind in ("read", "add"):
            key = event["key"]
            value = tx["writes"].get(key, tx["snapshot"].get(key))
            if key not in tx["writes"]:
                tx["points"].add(key)
            if kind == "add":
                tx["writes"][key] = (value if value is not None else 0) + event["value"]
            else:
                reads.append({"event": index, "tx": name, "kind": "point",
                              "key": key, "value": value})
        elif kind == "scan":
            low, high = event["low"], event["high"]
            tx["ranges"].add((low, high))
            visible = {**tx["snapshot"], **tx["writes"]}
            rows = [{"key": key, "value": value} for key, value in sorted(visible.items())
                    if low <= key < high and value is not None]
            reads.append({"event": index, "tx": name, "kind": "range", "rows": rows})
        elif kind == "set":
            tx["writes"][event["key"]] = event["value"]
        elif kind == "delete":
            tx["writes"][event["key"]] = None
        elif kind == "save":
            tx["savepoints"].append((event["name"], dict(tx["writes"]), set(tx["points"]),
                                     set(tx["ranges"])))
        elif kind in ("rollback", "release"):
            position = next(i for i, point in enumerate(tx["savepoints"]) if point[0] == event["name"])
            if kind == "rollback":
                _, writes, points, ranges = tx["savepoints"][position]
                tx["writes"], tx["points"], tx["ranges"] = dict(writes), set(points), set(ranges)
                tx["savepoints"] = tx["savepoints"][:position + 1]
            else:
                tx["savepoints"] = tx["savepoints"][:position]
        else:
            conflicts = sorted(key for key in tx["writes"]
                               if versions.get(key, 0) != tx["versions"].get(key, 0))
            cycle = []
            status = "aborted" if kind == "abort" else "write-conflict" if conflicts else "committed"
            if status == "committed":
                tx["commit"] = clock + 1
                edges = _serialization_edges_509([*committed, tx])

                def reachable(reverse, name=name, edges=edges):
                    seen, pending = {name}, [name]
                    while pending:
                        at = pending.pop()
                        for a, b in edges:
                            left, right = (b, a) if reverse else (a, b)
                            if left == at and right not in seen:
                                seen.add(right)
                                pending.append(right)
                    return seen

                component = reachable(False) & reachable(True)
                if len(component) > 1:
                    status, cycle = "serialization", sorted(component)
                else:
                    clock += 1
                    committed.append(tx)
                    for key, value in tx["writes"].items():
                        if value is None:
                            state.pop(key, None)
                        else:
                            state[key] = value
                        versions[key] = versions.get(key, 0) + 1
            completions.append({"event": index, "tx": name, "status": status,
                                "conflicts": conflicts if status == "write-conflict" else [],
                                "cycle": cycle})
            del active[name]
    return {"state": state, "versions": versions, "reads": reads, "completions": completions,
            "edges": [list(edge) for edge in sorted(_serialization_edges_509(committed))]}


def _schedule_510(jobs, dependencies, workers, capacity):
    jobs = sorted(jobs, key=lambda row: row["test"])
    names = [row["test"] for row in jobs]
    positions = {name: i for i, name in enumerate(names)}
    need = [sum(1 << positions[name] for name in dependencies.get(job["test"], [])
                if name in positions) for job in jobs]
    all_done = (1 << len(jobs)) - 1
    if any(row["resource"] > capacity or not set(row["workers"]) & set(workers) for row in jobs):
        return None

    @lru_cache(None)
    def visit(now, done, running):
        newly_done = [(i, end) for _, i, end in running if end == now]
        for i, _ in newly_done:
            done |= 1 << i
        running = tuple(item for item in running if item[2] > now)
        if done == all_done:
            return now, sum(end for _, end in newly_done), ()
        occupied = {worker for worker, _, _ in running}
        busy = sum(1 << i for _, i, _ in running)
        ready = [i for i, job in enumerate(jobs) if not (done | busy) & (1 << i)
                 and need[i] & done == need[i] and job["release"] <= now]
        free = [worker for worker in workers if worker not in occupied]
        used_resource = sum(jobs[i]["resource"] for _, i, _ in running)
        best = None

        def assign(position, selected, consumed):
            nonlocal best
            if position < len(free):
                assign(position + 1, selected, consumed)
                for i in ready:
                    if i in {item[1] for item in selected} or free[position] not in jobs[i]["workers"]:
                        continue
                    if consumed + jobs[i]["resource"] > capacity:
                        continue
                    if jobs[i]["deadline"] is not None and now + jobs[i]["duration"] > jobs[i]["deadline"]:
                        continue
                    assign(position + 1, [*selected, (free[position], i, now + jobs[i]["duration"])],
                           consumed + jobs[i]["resource"])
                return
            following = tuple(sorted((*running, *selected)))
            future = [end for _, _, end in following]
            future += [job["release"] for i, job in enumerate(jobs)
                       if not (done | busy) & (1 << i) and job["release"] > now]
            if not future:
                return
            suffix = visit(min(future), done, following)
            if suffix is None:
                return
            plan = tuple(sorted((*suffix[2],
                                 *((jobs[i]["test"], now, worker) for worker, i, _ in selected))))
            score = suffix[0], suffix[1] + sum(end for _, end in newly_done), plan
            if best is None or score < best:
                best = score

        assign(0, [], used_resource)
        return best

    return visit(0, 0, ())


def _revision_510(data):
    events = {}
    for index, row in enumerate(data["records"]):
        if row["run"] != data["run"] or row["observed"] > data["cutoff"]:
            continue
        key = row["shard"], row["seq"]
        if key not in events or (row["observed"], index) > events[key][:2]:
            events[key] = row["observed"], index, row
    attempts = {}
    for _, _, row in events.values():
        key = row["test"], row["attempt"]
        rank = row["revision"], row["observed"], row["shard"], row["seq"]
        if key not in attempts or rank > attempts[key][0]:
            attempts[key] = rank, row
    attempts = {key: pair[1] for key, pair in attempts.items() if pair[1]["status"] != "withdrawn"}
    usable = set()
    while True:
        additions = {
            key for key, row in attempts.items() if key not in usable and all(
                (dep["test"], dep["attempt"]) in usable
                and attempts[dep["test"], dep["attempt"]]["status"] == "passed"
                for dep in row["requires"]
            )
        }
        if not additions:
            break
        usable |= additions
    tests = []
    expected = sorted(set(data["expected"]))
    for name in expected:
        candidates = [row for key, row in attempts.items() if key in usable
                      and row["test"] == name and row["status"] != "cancelled"]
        final = max(candidates, key=lambda row: row["attempt"]) if candidates else None
        tests.append({"test": name, "status": final["status"] if final else "missing",
                      "attempt": final["attempt"] if final else None})
    passed = {row["test"] for row in tests if row["status"] == "passed"}
    jobs = [row for row in data["jobs"] if row["test"] not in passed]
    schedule = _schedule_510(jobs, data["dependencies"], sorted(data["workers"]), data["capacity"])
    return {
        "tests": tests,
        "usable": [{"test": name, "attempt": attempt} for name, attempt in sorted(usable)],
        "blocked": [{"test": name, "attempt": attempt} for name, attempt in sorted(set(attempts) - usable)],
        "unexpected": sorted({name for name, _ in attempts} - set(expected)),
        "schedule": None if schedule is None else {
            "makespan": schedule[0], "completion_sum": schedule[1],
            "jobs": [{"test": name, "start": start, "worker": worker}
                     for name, start, worker in schedule[2]],
        },
    }


def _adaptive_511(hypotheses, probes):
    probes = sorted((row for row in probes if row["available"]), key=lambda row: row["id"])
    outcomes = []
    for probe in probes:
        values = []
        for hypothesis in hypotheses:
            count = len(set(hypothesis) & set(probe["tests"]))
            values.append(int(count > 0) if probe["mode"] == "any"
                          else count % 2 if probe["mode"] == "parity" else count)
        outcomes.append(values)
    signatures = {}
    for i in range(len(hypotheses)):
        signatures.setdefault(tuple(values[i] for values in outcomes), []).append(i)
    unresolved = sorted(group for group in signatures.values() if len(group) > 1)
    if unresolved:
        return {"tree": None, "worst_cost": None, "path_cost_sum": None, "nodes": None,
                "unresolved": unresolved}

    def split(mask, index):
        result = {}
        for i in range(len(hypotheses)):
            if mask & (1 << i):
                result[outcomes[index][i]] = result.get(outcomes[index][i], 0) | (1 << i)
        return sorted(result.items())

    @lru_cache(None)
    def worst(mask):
        if mask.bit_count() <= 1:
            return 0
        return min(probe["cost"] + max(worst(part) for _, part in groups)
                   for i, probe in enumerate(probes) if len(groups := split(mask, i)) > 1)

    @lru_cache(None)
    def optimize(mask, budget):
        if mask.bit_count() <= 1:
            tree = {"hypothesis": mask.bit_length() - 1}
            return 0, 0, json.dumps(tree, sort_keys=True, separators=(",", ":")), tree
        best = None
        for i, probe in enumerate(probes):
            groups = split(mask, i)
            remaining = budget - probe["cost"]
            if len(groups) < 2 or any(worst(part) > remaining for _, part in groups):
                continue
            children = [(outcome, optimize(part, remaining)) for outcome, part in groups]
            tree = {"probe": probe["id"], "branches": [
                {"outcome": outcome, "tree": child[3]} for outcome, child in children
            ]}
            score = (
                mask.bit_count() * probe["cost"] + sum(child[0] for _, child in children),
                1 + sum(child[1] for _, child in children),
                json.dumps(tree, sort_keys=True, separators=(",", ":")),
                tree,
            )
            if best is None or score[:3] < best[:3]:
                best = score
        return best

    mask = (1 << len(hypotheses)) - 1
    budget = worst(mask)
    answer = optimize(mask, budget)
    return {"tree": answer[3], "worst_cost": budget, "path_cost_sum": answer[0],
            "nodes": answer[1], "unresolved": []}


def _revision_511(data):
    diagnosis = _solve_511({"tests": [{**row, "can_rerun": False} for row in data["tests"]],
                          "shards": data["shards"]})
    if not diagnosis["consistent"]:
        return diagnosis
    for key in ("rerun", "rerun_cost", "unresolved"):
        del diagnosis[key]
    return {**diagnosis, **_adaptive_511(diagnosis["hypotheses"], data["probes"])}


def _revision_prompts(legacy):
    prompts = {}
    prompts[500] = legacy["_PROMPT_500"].split("keep может повторяться")[0].replace(
        "Вход: objects={label: UTF-8 text}, snapshots=[{id,parent,ops}], keep=[id],\nexisting=[SHA-256].",
        "Прочитай backup/object-index.json и сами UTF-8 object files, оба\n"
        "backup/snapshots-*.json, storage/inventory.json и backup/policy.json.\n"
        "Логический вход: objects={label:text}, snapshots=[{id,parent,ops}],\n"
        "existing=inventory.existing и policy.",
    ) + """
Нужно СОВМЕСТНО выбрать retained snapshots. policy.candidates={id:utility};
utility — positive integer. mandatory — обязательные IDs; requires={id:[IDs]}
задаёт импликации выбора, в том числе циклические. groups=[{members,min,max}]
ограничивает число выбранных уникальных members, группы перекрываются.
max_physical/max_upload — бюджеты в UTF-8 bytes. Обязательны ВСЕ ограничения.
Выбор snapshot НЕ означает выбор его предков; предки только восстанавливают
состояние. Хранимое пространство равно сумме sizes УНИКАЛЬНЫХ content blobs
во всех выбранных snapshots; общий blob оплачивается один раз. Upload — та
же сумма только для blobs, отсутствующих в inventory.existing.
Оптимизируй лексикографически (-сумма utility,upload_bytes,physical_bytes,
число retained,retained). retained — список выбранных IDs по возрастанию.
Нельзя независимо оптимизировать ветви/группы: у них общие blobs и зависимости.
Если допустимого выбора нет, только {feasible:false}. Иначе
{feasible:true,retained,utility,upload_bytes,snapshots,required,upload,gc,
logical_bytes,physical_bytes}. snapshots СТРОГО по id по возрастанию,
независимо от исходного порядка manifests/policy. Каждый {id,files,tree_sha256};
files по path по возрастанию, каждый {path,size,sha256}, size UTF-8 bytes.
Хеши — нижний SHA256 hex. tree_sha256=SHA256 UTF8 конкатенации
path+NUL+decimal size+NUL+sha256+LF по files. required=уникальные content
hashes выбранных files; upload=required-existing; gc=existing-required.
Все три массива уникальны и по возрастанию. logical_bytes считает повторения
files во всех retained; physical_bytes — только уникальные blobs.
Пустой blob включается, хотя его size=0. До20 candidates,100 snapshots,
1000 materialized files,30 groups. Изменённые источники не принимаются.
"""
    prompts[501] = legacy["_PROMPT_501"].split("Выход {root,entries")[0].replace(
        "Вход entries:", "Вход {entries,transactions,queries}. Начальное состояние entries:"
    ).replace('возвращай ТОЛЬКО {"error":...}', 'возвращай ТОЛЬКО {"initial_error":...}') + """
Затем исполни transactions=[{id,ops}] по порядку, каждый атомарно.
Идентичность физического file сохраняется при hardlink/rename; write через
любой hardlink меняет содержимое ВСЕХ его имён. Одинаковые bytes разных
исходных files не объединяют inode. Unlink имени не удаляет другие hardlinks.
Создание нового файла после unlink даёт НОВЫЙ inode даже с тем же текстом.
Неявные dirs начального состояния становятся обычными сохраняемыми dirs.

WALK(path,follow): корень ""; начальный / тоже корень. Сегменты "" и "."
пропускаются; ".." снимает один уже разрешённый компонент, выше корня escape.
Остальные сегменты NFC→casefold→NFC. Нет узла → missing; промежуточный
не-dir → not-dir. Symlink разыменуется, если остались компоненты или
follow=true; его target подставляется перед остатком. Относительный target
от родителя symlink, начальный / сбрасывает путь к корню; ".." обрабатывается
ПОСЛЕ подстановки. Счётчик разыменований на один WALK: 41-е даёт loop.
Не выполнять реальные OS symlinks. Пустой path в WALK допустим.
DEST(path): сначала нормализуй path как начальный entries.path; WALK его
родителя (follow=true), он обязан быть dir; присоедини нормализованный basename.

ops проверяются по порядку, первая ошибка откатывает ВЕСЬ transaction.
create {path,text}, mkdir {path}, symlink {path,target}, link {source,path},
rename {source,path}: сначала DEST(path) и отсутствие destination (иначе exists).
create — новый inode; mkdir — пустая dir; symlink хранит literal target.
link: WALK(source,true) обязан быть file, иначе not-file.
rename: WALK(source,false), переносит имя и всё поддерево; корень или target
строго внутри source → subtree. Link target bytes не переписываются.
write {path,text}: WALK(path,true), требует file, иначе not-file.
unlink {path}: WALK(path,false), корень/непустая dir → not-empty.
Все остальные операции допустимы для разрешённых типов как описано.
Неявно создавать dirs после начального построения НЕЛЬЗЯ.
Audit {id,error,failed_op}: null/null при успехе, иначе code и 1-based op index.
Внутри транзакции последующие ops видят предыдущие, откат включает inodes.

Выход {archive,audit,queries}. archive={root,entries,hardlink_groups,
logical_bytes,physical_bytes} по указанному выше Merkle framing, entries
по path без корня, {path,kind,sha256}; hardlinks представлены kind=file.
Все dirs включены; symlinks хешируют исходный target. hardlink_groups —
отсортированные списки путей на каждый оставшийся inode, включая singleton;
внешний массив лексикографически. logical_bytes считает все имена files,
physical_bytes каждый оставшийся inode один раз. queries в исходном порядке
для input {path,follow:bool}: WALK по окончательному состоянию; успех
{resolved:canonical path,kind,sha256}, ошибка только {error:code}.
До400 entries,60 transactions по20 ops,100 queries,200KB содержимого.
Каждый path/target до240 UTF-8 bytes, ID до64 UTF-8 bytes.
Все op names/fields корректны; NUL отсутствует в путях и targets.
"""
    prompts[502] = legacy["_PROMPT_502"].split("По окончательным net")[0].replace(
        "Вход invoices=[{id,customer,currency,due,amount}], events с уникальными id",
        "Прочитай accounting/invoices.csv (CSV quoting обязателен, due/amount целые),\n"
        "объедини accounting/events-*.jsonl, прочитай accounting/allocation.json.\n"
        "allocation.priority задаёт integer priority каждого invoice; routes/minimums\n"
        "описаны ниже. invoices=[{id,customer,currency,due,amount}], events с уникальными id",
    ) + """
Здесь нет greedy двухфазного settlement. Распредели окончательные net
ГЛОБАЛЬНО. routes=[{payment,invoice,cap,fee}] задают единственные разрешённые
пары; нет route → allocation0. Повторов пары нет. Неактивные payment и пары
с несовпадающими customer/currency недопустимы, даже если route присутствует.
Все allocation — integers>=0, <=route.cap; сумма payment <=net; сумма
invoice между minimums[invoice] и amount. minimums содержит каждый invoice,
0<=minimum<=amount. cap/fee>=0, invoice.priority>=0.
Лексикографическая цель: MAX total paid; затем MAX Σ paid_i*priority_i;
затем MAX earmark_total (деньги по совпадающему payment.invoice);
затем MIN Σ allocation*fee; затем MIN вектор allocation в порядке
(active payment.id,invoice.id), включая все нулевые клетки.
Minimums — жёсткие ограничения, сильнее любой цели. Earmark не разрешает
запрещённый route. Перераспределения ранее выбранных routes могут быть нужны.

При infeasible: {feasible:false,inactive,refunds}. Иначе
{feasible:true,inactive,refunds,allocations,invoices,payments,paid_total,
priority_total,earmark_total,fee_total}. inactive все неактивные event IDs
по алфавиту; refunds все активные refunds по id [{id,accepted,rejected}].
allocations — ВСЕ пары active payments×invoices по (payment,invoice),
{payment,invoice,amount}. invoices по id [{id,paid,balance}].
payments активные по id [{id,net,applied,credit}], credit=net-applied.
До40 payments,40 invoices,400 routes,500 events; integer quantities<=10^6.
"""
    native = legacy["_PROMPT_503"].split("Найди целую матрицу")[0]
    native = native.replace(
        "Вход {base_scale,invoices,row_totals,column_totals}.",
        "Вход {base_scale,invoices,row_totals,column_totals,quotas}.",
    )
    prompts[503] = native + """
Найди integer Y[i,j] из {floor(X[i,j]),ceil(X[i,j])}, одновременно с
заданными row/column totals и ВСЕМИ quotas=[{id,cells:[[invoice,jurisdiction]],
min,max}]. В каждой quota нет повторов клеток, но quotas произвольно
перекрываются; min/max — inclusive целые, в том числе отрицательные.
Не заменяй quotas независимыми row/column constraints.
Лексикографически минимизируй:
1) maximum_error = MAX abs(Y-X) по всем клеткам (0 для пустой матрицы);
2) squared_error = SUM (Y-X)^2;
3) вектор Y row-major (invoice.id, внутри jurisdiction), оба по алфавиту.
Все сравнения точные рациональные. Меньшая squared_error не компенсирует
большую maximum_error. Оптимизация только squared_error теперь неверна.
При infeasible ТОЛЬКО {feasible:false,reason}: total если общие totals
не равны; bounds если любой row/column total вне суммы floor/ceil;
coupling во всех остальных случаях, включая невыполнимые quotas.
При успехе {feasible:true,invoices,columns,base_total,maximum_error,
squared_error,quotas}. invoices по id, каждый {id,currency,due_minor,
base_minor,lines,allocations}; base_minor=row_totals[id]. lines по id,
{id,discount,net_minor,tax_minor,gross_minor}. allocations ВСЕ columns по
jurisdiction, {jurisdiction,exact_base:X[i,j],base_minor:Y[i,j]}.
columns по jurisdiction [{jurisdiction,base_minor:column_totals[j]}].
base_total=сумма row_totals. discount/exact_base/maximum_error/squared_error
— сокращённые "n/d", включая n/1 и0/1. quotas по id [{id,total:сумма Y в cells}].
До7 rows,7 columns,30 lines/invoice,5 quotas; не более4 дробных клеток
в каждой строке, остальные exact integers. |n|,d,|qty|<=10^12.
Пустые inputs допустимы. Можно sys.set_int_max_str_digits(0).
"""
    prompts[504] = legacy["_PROMPT_504"].split("Для успеха v2")[0].replace(
        "v1={version:1,orders,payments,aliases}.", "v1={version:1,orders,payments,aliases,cannot_link}."
    ).replace("alias={tenant,left,right}.", "alias={id,tenant,left,right,weight,required}.") + """
После перечисленных базовых validation проверок необходимо REPAIR identities.
cannot_link=[{tenant,left,right}] запрещает попадание нормализованных имён в
один equivalence class. Имена здесь непустые; порядок пар не важен.
Можно удалить только aliases с required=false. Их weight positive integer.
Нужно выбрать subset kept aliases, содержащий ВСЕ required=true, чьё
транзитивное замыкание не нарушает ни одной cannot_link. Выбор глобальный:
локально безопасные edges совместно могут создать запрещённую связь.
Минимизируй (-сумма weights kept,-число kept,отсортированные removed IDs).
При невозможности error identity-constraints с НЕИЗМЕНЁННОЙ исходной state,
по той же форме {ok:false,error,state}; базовые ошибки приоритетнее repair.

Затем миграция использует ТОЛЬКО kept aliases; удалённые рёбра и имена,
присутствовавшие лишь в них/cannot_link, не создают aliases в v2.
Один customer на class с хотя бы одним order; id=min order.id группы,
tenant исходный; aliases все имена группы из orders и kept aliases, sorted,
name=min aliases. Неиспользованные classes удаляются. customers по id.
v2={version:2,customers,orders,payments}; orders по id, каждый
{id,customer_id,total,paid,status}, paid=net payments; status paid при
paid==total (включая 0), иначе unpaid при paid==0, иначе partial.
payments сохраняются точно и по id. Успех {ok:true,state:v2,repair:
{weight,kept,removed}}, оба ID массива по алфавиту.
Если input.version=2, вернуть {ok:true,state:исходный объект,repair:null}
без проверок/перестановок. До22 aliases,16 разных (tenant,normalized name)
в aliases/cannot_link,30 cannot_link,500 orders/payments. Alias IDs уникальны.
"""
    prompts[505] = """
Материализуй bitemporal configuration history с переносом интервалов и provenance.
Input {records,cutoffs,queries}; record={id,recorded,revision,deleted,entity,
start,end,priority,value}. recorded/revision/priority целые; [start,end),
null start=-∞,null end=+∞; интервалы непустые. value строка, null MASK или
{ref:entity,offset:int}. Пары(id,recorded,revision) уникальны.
Для каждого cutoff в union(input.cutoffs,queries[*].asof) независимо:
отбрось recorded>cutoff, выбери max(recorded,revision) каждого logical id,
затем отбрось deleted. Редакция может менять entity/interval/value.

В каждой entity и точке t кандидаты — покрывающие записи. Literal string
даёт своё значение и provenance=[id]; null — явную MASK и [id].
Reference обращается к окончательной semantic timeline ref в точке t+offset.
Если там MISSING (нет победителя), этот reference не является кандидатом:
может победить более слабая локальная запись. Если там MASK, reference
становится MASK; если string — этим string. Provenance reference —
union собственного id и ВСЕХ реально использованных id по цепочке.
Среди доступных кандидатов побеждает max(priority,recorded,revision,id).
MASK подавляет нижние значения; её нельзя считать MISSING и fallback.
Граф всех references во всех редакциях ацикличен; отсутствующая entity
имеет везде MISSING. Это наследование интервалов, не textual interpolation.

Материализуй все максимальные интервалы с постоянным string, исключая MASK
и MISSING. Соседние интервалы равного string объединяются; sources — union
provenance всех непустых частей объединения. Не включай проигравшие records.
Важно вычислять точечную provenance цепочки ДО coalescing: источники
соседнего равного parent segment не должны протекать за его границу.
Output {views,answers}; views по asof возрастающе, уникально:
{asof,segments:[{entity,start,end,value,sources}]}. segments по(entity,start),
-∞ первым; sources sorted unique IDs. answers в порядке queries:
query={entity,at:int,asof:int}, ответ string либо null.
До160 records,20 entities,12 distinct requested cutoffs,100 queries,
координаты/offsets до10^12. После переноса parent boundaries на каждую entity
не более2000 breakpoints. Во ВСЕХ requested views суммарно не более2000
выходных segments и20000 элементов sources. ID/entity до64 UTF-8 bytes,
value string до128 UTF-8 bytes. Нельзя проходить каждую integer точку времени.
"""
    prompts[506] = legacy["_PROMPT_506"].split("Выход answer.json")[0].replace(
        "Выполни lossless редактирование набора INI по edit-plan.json.\nКаждый job задаёт source (путь файла) и edits в порядке исполнения.",
        "Выбери совместимый conditional release plan для lossless INI конфигов.\n"
        "Прочитай configs/* и release-plan.json={plans,budget,final}.",
    ) + """
plans=[{id,reward,cost,mandatory,requires:[IDs],excludes:[IDs],guards,edits}].
Выбери subset, содержащий mandatory, замкнутый по requires, без любой пары
selected plan/excluded plan; sum cost<=budget. IDs/rewards/costs уникальны/
positive/nonnegative соответственно. Выполняй выбранные plans по id ASCENDING.
guards/final — массив selectors {source,section,key,which:positive int,raw}.
Selector выбирает value span по правилам выше. Его raw сравнивается точно,
raw=null требует отсутствия. Сравнение с raw НЕ разбирает JSON.
Перед plan все guards проверяются по текущему состоянию; mismatch делает
весь выбранный subset недопустимым. Затем подготовь значения ВСЕХ edits:
edit имеет source,section,key,which и ЛИБО value:string, ЛИБО copy:selector.
copy читает raw span из состояния ДО plan; он обязан быть существующей JSON
string, которую нужно decode, иначе subset недопустим. Copy не использует
raw поле. Затем edits исполняются последовательно с повторным parsing;
отсутствие target — допустимые0 replacements, как выше. Скопированная строка
снова кодируется JSON при вставке. Все copy внутри plan видят pre-plan,
а не предыдущие edits. После последнего plan обязательны все final guards.

Глобальная цель минимизировать (-sum reward,sum cost,total replacements,
sorted chosen IDs). Не применять сначала самые ценные/дешёвые plans:
совместимость зависит от результатов предыдущих и finale. Output при
infeasible только {feasible:false}; иначе {feasible:true,chosen,reward,cost,
replacements,files,audit}. files содержит полный text КАЖДОГО исходного
конфига; audit выбранных plans по id [{id,applied:[counts на каждый edit]}].
До18 plans,8 files,20 edits/plan,30KB текста. Источники read-only.
"""
    prompts[507] = """
Разреши scoped INI deployments с C3 multiple inheritance и late super.
Прочитай jobs.json={jobs:[{id,entry,env}]} и config/*; output answer.json
mapping job.id→result, jobs независимы. Читай файл построчно strip().
Пустые/#/; строки пропусти; [profile] меняет текущий profile (начальный global);
key=template сохраняет определение (profile,key), последнее побеждает.
@let NAME=template задаёт lexical variable; @include path-template включает
файл с КОПИЕЙ variables и текущим profile. После возврата scope/profile
вызывающего восстановлены; записи definitions/profiles/parents общие.
@extends A B задаёт упорядоченных родителей текущего profile; последнее
объявление побеждает, пустой @extends очищает родителей. Имена ASCII identifiers
без точек/двоеточий, parents в одном объявлении уникальны. Заголовки объявляют
profile даже без keys; global существует всегда. Parent позже может объявляться.

let/include вычисляются сразу, только ENV:/VAR: references; let видит старую
переменную. Assignment захватывает variables на момент записи. Include path
от родительской директории; normpath; абсолютный/выход выше workspace → path;
нет файла → missing-file; повтор в АКТИВНОМ стеке → include-cycle.
Parsing ошибки по порядку чтения. Потом проверь unknown-parent по profiles
по алфавиту; затем вычисли C3 MRO profiles по алфавиту, рекурсивно parents
слева направо. Цикл даёт inheritance-cycle; невозможный C3 merge даёт mro.
C3(P)=[P]+merge(C3(parent1),...,C3(parentN),parents); на каждом шаге берётся
первый head, не встречающийся ни в одном tail; он убирается из всех heads.

Effective key receiver — первое definition key по его MRO. Resolve templates
ПОСЛЕ всех includes/MRO: ${key} виртуально обращается к тому же receiver с
начала MRO; ${other.key} — к другому receiver с начала MRO.
${super:key} ищет key СТРОГО ПОСЛЕ defining owner ТЕКУЩЕГО template в MRO
того же receiver. Поэтому наследованный template сохраняет virtual receiver,
а super зависит от позиции owner, а не от прямого parent list.
${VAR:X} использует captured variables; ${ENV:X} — job.env.
${ref:-fallback}: literal fallback только если непосредственно ref отсутствует,
ошибка внутри существующего ref не подавляется. Пустая строка существует.
$$ даёт literal $, вставки/fallback повторно не интерполируются; синтаксис
${...} корректен, без вложений. Остальной $ literal.
Цикл определяется по(receiver,key,start-position); errors cycle/missing.
Разрешай все effective keys по(profile,key), первая ошибка завершает job.
На любую ошибку только {error:code}. Успех {values,origins,linearization}:
values/profile/key strings, origins победившего definition "path:1-based-line";
profiles без effective keys не включать в values/origins. linearization
содержит ВСЕ объявленные profiles (включая global), каждый MRO array.
До35 profiles,8 parents/profile,300 definitions,60 include invocations/job.
"""
    prompts[508] = """
Реализуй atomic rebase UTF-8 patches поверх уже изменённого multi-file store.
Input {files:{path:text},batches:[{id,base,guards,ops,post}]}. Путь — opaque
строка, текст Unicode, SHA256 UTF8 lowercase. Начальная revision0; только
успешный batch создаёт revision+1, включая no-op. base — существующая
committed revision на начало batch. Каждый исходный/create file имеет
уникальную физическую identity, rename её сохраняет; delete+create — ABA,
новый file НЕ совпадает с прежним даже при том же path/hash.

guards mapping path→hash/null проверяются в BASE snapshot: hash требует
точный file hash; null отсутствие. Failure guard,failed_op0.
ops по порядку на private current snapshot. create {path,text}: текущий path
должен отсутствовать, иначе exists. Остальные ops сначала ищут op.path в BASE
(missing-base), затем его identity в текущем staged state (deleted).
rename {path,target}: target текущий literal path обязан отсутствовать
(exists), переносит исходную identity, даже после предыдущего rename.
delete {path}: если identity редактировалась после base, включая same-value
edit и staged edits этого batch, conflict; иначе удаляет текущее имя.

edit {path,spans:[{start,end,old,new}]}: все offsets в UTF8 BYTES BASE content
данной identity, НЕ текущего текста. Первые проверки до rebase:
spans в input order range(0<=start<=end<=len), затем boundary для каждого
span (start/end UTF8 boundaries); далее sorted(start,end) соседние overlap
если next.start<previous.end ИЛИ равенство start; далее old==base byte slice,
иначе preimage. Проверки range/boundary чередуются по spans, как сказано.
Затем преобразуй spans через ВСЕ успешно принятые edits после base и ранее
исполненные staged edits текущего batch. История одного edit применяется
в убывающем start; старые hunks [a,b)→newbytes, L=len(newbytes).
Для incoming nonempty [s,e): old insertion a==b внутри s<a<e → conflict;
при a<=s insertion сдвигает обе границы на L, при a>=e не сдвигает.
Old nonempty hunk с max(s,a)<min(e,b) → conflict; если b<=s, сдвинь обе
границы на L-(b-a); иначе без сдвига. Incoming insertion s==e:
old insertion a<=s сдвигает на L; old nonempty a<s<b → conflict;
s==a переноси в a+L; s>=b сдвигай на L-(b-a); s<a оставляй.
Это right-affinity, а не поиск old bytes в текущем тексте. Conflicting
same-value writes не игнорируются. После mapping примени новые spans
одновременно, справа налево В ПОРЯДКЕ ИСХОДНЫХ (start,end). Если разные
BASE anchors после rebase совпали, больший BASE start применяется первым:
текст меньшего BASE start окажется слева. Новые offsets в history — уже
current coordinates, запись history идёт в этом же порядке.
Пустой spans не создаёт history. После ops проверь post в staged CURRENT
как hash/null guards; ошибка post,failed_op=len(ops)+1.
Любая ошибка откатывает files/identity/history/revision целиком; первая ошибка
по порядку, следующие batches продолжают с последнего commit.

Output {files,hashes,revision,audit}; audit в порядке batches
[{id,committed,revision,error,failed_op}], revision ПОСЛЕ попытки, при успехе
error/failed_op null; иначе 1-based op index кроме guards/post. До80 batches,
30 ops/batch,1000 files; path до128 UTF-8 bytes. Суммарный размер JSON-encoded
file texts в любой committed revision до1MB. Paths op.path всегда обозначают
BASE имя, не staged имя.
"""
    prompts[509] = """
Исполни optimistic serializable MVCC schedule с predicate reads и savepoints.
Input {state:{key:int},events}. Events {tx,op,...} по порядку. begin сохраняет
committed snapshot values/versions и begin clock; clock увеличивается на1
только при successful commit. Tx IDs уникальны за всё расписание; команды
только активной tx; в конце все закрыты. Versions начальных/отсутствующих
ключей0. Write buffer: set{key,value},delete{key} (tombstone intent),add{key,value}
(visible value или0 +delta). read{key}: visible read-your-writes, отсутствует
→null. Если key ещё НЕ в write buffer, read/add добавляет point read footprint.
scan{low,high}: возвращает все visible nonnull keys в [low,high), sorted;
всегда добавляет predicate interval footprint, даже пустой result.
Чужие commits после begin чтениям невидимы.

save{name} сохраняет writes И point/range footprints; rollback{name}
восстанавливает их, удаляет поздние points и сохраняет выбранную;
release{name} удаляет выбранную и поздние savepoints без смены buffers.
Активные имена уникальны, указанные существуют. Read AUDIT не откатывается.
abort закрывает tx без эффекта. commit:
1) Сравни begin/current versions всех ключей НЫНЕШНЕГО write buffer.
Различия → write-conflict, conflicts=sorted keys, tx закрывается.
2) Иначе tentative commit clock=current+1. Построй serialization graph для
всех ранее committed tx плюс candidate (active/aborted исключены):
WW: writer→later writer для любого общего written key, порядок commit clocks.
WR: для каждого key, попавшего в point/predicate footprint reader, найди
последний writer этого key с commit<=reader.begin; edge writer→reader.
Неинициализированные/initial keys не имеют writer. Tombstone — тоже write.
RW: reader→writer, если writer.commit>reader.begin и хотя бы один written
key writer попадает в point/predicate footprint reader. Self edges исключены.
Read footprint после savepoint rollback — единственный действующий.
Если candidate в directed cycle, reject со status serialization и cycle=
sorted tx IDs ВСЕГО strongly connected component candidate, включая его.
3) Иначе atomic commit, каждый written key.version+=1, даже same-value set
или delete отсутствующего; tombstone version сохраняется. Commit clock+=1.
Read-only tx тоже проверяет graph и может быть отвергнута.

Output {state,versions,reads,completions,edges}. Point audit:
{event:1-based global index,tx,kind:"point",key,value}; range audit:
{event,tx,kind:"range",rows:[{key,value}]}. reads chronological.
completions chronological {event,tx,status,conflicts,cycle};
status committed/aborted/write-conflict/serialization; conflicts/cycle=[]
кроме соответствующей ошибки. edges итогового committed graph, sorted
массивы [from,to]. До120 tx,1500 events,1000 keys; low<high string ordering.
Во всех scan audit суммарно<=15000 rows; key/tx до64 UTF-8 bytes,
integer values/arguments по модулю<=10^12.
"""
    prompts[510] = """
Согласуй provenance retry shards, затем найди ТОЧНЫЙ resource rerun schedule.
Input {run,cutoff,expected,records,jobs,dependencies,workers,capacity}.
Record={run,shard,seq,observed,test,attempt,revision,status,duration_ms,requires}.
Все числа integer>=0, attempt>=1; status passed/failed/skipped/infra/running/
cancelled/withdrawn. requires=[{test,attempt}] может ссылаться на ещё не
доставленные/withdrawn attempts или создавать циклы. References точные.
Сначала discard чужой run/observed>cutoff; затем dedup(shard,seq) по
max(observed,input index), исправляться могут ВСЕ поля; затем winner каждой
(test,attempt) по max(revision,observed,shard,seq); затем удаляй withdrawn
winner БЕЗ восстановления старой revision.
Usable определяется НАИМЕНЬШЕЙ fixed point: attempt без requires usable;
затем usable, если ВСЕ required attempts usable И status passed.
Failed dependency/cycle не удовлетворяются. cancelled может быть usable,
но не является passed и не участвует в выборе final attempt.
Для каждого unique expected выбери наибольший usable НЕcancelled attempt;
его status либо missing/attempt:null. Массив tests sorted по test,
НЕЗАВИСИМО от исходного порядка expected. usable/blocked содержат ВСЕ
оставшиеся attempts, включая unexpected; sorted по(test,attempt).

jobs содержит ровно по одному {test,duration,release,deadline,resource,workers}
для каждого unique expected. Удали jobs с final status passed; они считаются
завершёнными в момент0. Остальные должны быть выполнены заново nonpreemptively.
duration positive integer,release>=0, deadline integer/null (inclusive finish),
resource positive integer. workers перечисляет допустимых workers для job.
Input workers — distinct IDs, один job на worker; одновременно sum resource
running jobs<=capacity. dependencies={test:[test IDs]} DAG, predecessor должен
завершиться до старта job; старый failed/infra etc не считается завершением.
Разрешено намеренное ожидание и выбор worker; greedy earliest-ready не оптимален.
Все start times — nonnegative integers. Минимизируй (makespan,
sum completion times всех rerun jobs,вектор [test,start,worker] по test).
Для пустого расписания makespan/completion_sum0, jobs[]. Deadline и release
обязательны. Если расписания нет, schedule=null.
Output {tests:[{test,status,attempt}],usable:[{test,attempt}],
blocked:[{test,attempt}],unexpected:sorted IDs,schedule}.
Schedule успех {makespan,completion_sum,jobs:[{test,start,worker}]} по test.
До9 expected,2000 records,3 workers,capacity<=6,duration/release/deadline<=100.
Все IDs до64 UTF-8 bytes.
Unexpected IDs могут присутствовать в requires; dependencies только expected,
без повторов. Программа должна решать глобальный scheduling optimum.
"""
    prompts[511] = legacy["_PROMPT_511"].split("Один rerun test")[0].replace(
        "Вход tests=", "Вход {tests,shards,probes}; tests="
    ).replace("Гарантируется <=128 оптимальных гипотез.", "Гарантируется <=16 оптимальных гипотез.") + """
Теперь нужен АДАПТИВНЫЙ decision tree. probes=[{id,tests:[IDs],mode,cost,
available}], id unique; mode any/count/parity. Outcome в гипотезе H:
any=int(H пересекается с probe.tests), count=число уникальных faulty IDs
в probe.tests, parity=count%2. Все observations идеальны; cost integer>=0.
Выбирать можно только available=true; следующий probe зависит от outcome.
Leaf обязан однозначно назвать 0-based index гипотезы. Внутри node probe
обязан давать хотя бы2 возможных outcomes для оставшихся гипотез (не тратить
время на constant query). Повторять probe на разных branches допустимо.
Минимизируй лексикографически: максимальную стоимость root→leaf;
сумму стоимостей root→leaf по ВСЕМ hypotheses (равный вес каждой);
общее число внутренних nodes; canonical tree string.
Canonical string = json.dumps(tree,sort_keys=True,separators=(',',':')),
ASCII IDs гарантированы. Node={probe:id,branches:[{outcome:int,tree:child}]},
branches по numeric outcome; только непустые branches. Leaf={hypothesis:index}.
Оптимизируя вторую цель, нельзя требовать локально minimax-optimal subtree:
допустимы более дорогие subtree в пределах ОБЩЕГО worst-case budget.
Если полные vectors outcomes всех доступных probes не различают H,
tree/worst_cost/path_cost_sum/nodes=null; unresolved — группы неразличимых
0-based hypothesis indices размера>1, внутри ascending, группы lexicographic.
Иначе unresolved=[]. Одна H даёт leaf, все три стоимости0.

Успех diagnosis output {consistent:true,fault_cost,fault_count,hypotheses,
certain_bad,possibly_bad,tree,worst_cost,path_cost_sum,nodes,unresolved}.
certain_bad/possibly_bad — intersection/union H, sorted. Inconsistent
по-прежнему ТОЛЬКО {consistent:false}. rerun_cost/can_rerun в tests здесь
игнорируются: разрешения и стоимость определяются probes. До20 probes,
16 tests,80 shards; cost/fault_cost<=10^6, IDs до64 ASCII bytes.
Требуется точный adaptive optimum, не entropy heuristic.
"""
    return prompts


def _revision_inputs(legacy):
    old = legacy["_INPUTS"]
    inputs = {}
    overlay = {
        "entries": [
            {"path": "d/a", "kind": "file", "text": "café"},
            {"path": "d/b", "kind": "hardlink", "target": "d/a"},
            {"path": "same", "kind": "file", "text": "café"},
            {"path": "alias", "kind": "symlink", "target": "d"},
            {"path": "d/up", "kind": "symlink", "target": "../alias/a"},
        ],
        "transactions": [
            {"id": "change", "ops": [{"op": "write", "path": "alias/a", "text": "文\n"},
                                    {"op": "rename", "source": "d", "path": "moved"}]},
            {"id": "rollback", "ops": [{"op": "write", "path": "moved/b", "text": "wrong"},
                                      {"op": "unlink", "path": "moved"}]},
        ],
        "queries": [{"path": name, "follow": True} for name in ("moved/a", "same", "alias/a", "moved/up")],
    }
    inputs[501] = [overlay, {
        "entries": [{"path": "a", "kind": "file", "text": "x"}],
        "transactions": [{"id": "aba", "ops": [
            {"op": "link", "source": "a", "path": "b"}, {"op": "unlink", "path": "a"},
            {"op": "create", "path": "a", "text": "x"}, {"op": "write", "path": "a", "text": "y"},
        ]}],
        "queries": [{"path": name, "follow": True} for name in ("a", "b", "")],
    }]
    for case in old[501][1:10]:
        inputs[501].append({**copy.deepcopy(case), "transactions": [], "queries": []})
    for target in ("loop", "loop/x", "../gone", "/d/a"):
        case = copy.deepcopy(overlay)
        case["entries"].append({"path": "loop", "kind": "symlink", "target": target})
        case["transactions"] = []
        case["queries"] = [{"path": "loop", "follow": value} for value in (False, True)]
        inputs[501].append(case)
    large = {"entries": [], "transactions": [], "queries": []}
    for i in range(45):
        large["entries"].extend([
            {"path": f"d{i}/f", "kind": "file", "text": f"shared-{i % 4}"},
            {"path": f"d{i}/h", "kind": "hardlink", "target": f"d{i}/f"},
            {"path": f"l{i}", "kind": "symlink", "target": f"d{i}"},
        ])
        large["transactions"].append({"id": f"t{i}", "ops": [
            {"op": "write", "path": f"l{i}/h", "text": f"文{i}"},
            {"op": "rename", "source": f"d{i}", "path": f"m{i}"},
            {"op": "symlink", "path": f"d{i}", "target": f"m{i}"},
        ]})
        large["queries"].append({"path": f"l{i}/f", "follow": True})
    inputs[501].append(large)

    matrix = legacy["_matrix_case_503"]
    inputs[503] = []
    for data in old[503][:14]:
        data = copy.deepcopy(data)
        data["quotas"] = []
        inputs[503].append(data)
    inputs[503][0]["quotas"] = [{"id": "diagonal", "cells": [["i0", "j0"], ["i1", "j1"]],
                               "min": 2, "max": 2}]
    inputs[503][1]["quotas"] = [{"id": "off-diagonal", "cells": [["i0", "j1"], ["i1", "j0"]],
                               "min": 0, "max": 0}]
    rng = random.Random(20503)
    for k in range(8):
        n = 5 + k % 3
        values = [[rng.choice([1, 3, 5, 7, 9]) if (j - i) % n < 4 else 10 * rng.randrange(-1, 2)
                   for j in range(n)] for i in range(n)]
        chosen = [[value // 10 + int(value % 10 != 0 and rng.randrange(2))
                   for value in row] for row in values]
        data = matrix(values, [sum(row) for row in chosen],
                      [sum(row[j] for row in chosen) for j in range(n)])
        quotas = []
        for q in range(3 + k % 3):
            cells = [[f"i{i}", f"j{j}"] for i in range(n) for j in range(n)
                     if (2 * i + j + q) % (3 + q % 2) < 2]
            total = sum(chosen[int(i[1:])][int(j[1:])] for i, j in cells)
            quotas.append({"id": f"q{q}", "cells": cells, "min": total, "max": total})
        data["quotas"] = quotas
        inputs[503].append(data)
    impossible = copy.deepcopy(inputs[503][0])
    impossible["quotas"].append({"id": "incompatible", "cells": [["i0", "j0"]], "min": 0, "max": 0})
    inputs[503].append(impossible)
    bottleneck = matrix([[4, 8, 9], [5, 2, 6], [4, 4, 8]], [1, 1, 1], [1, 1, 1])
    bottleneck["quotas"] = []
    inputs[503].append(bottleneck)

    inputs[504] = []
    for data in old[504]:
        data = copy.deepcopy(data)
        if data["version"] == 1:
            data["cannot_link"] = []
            for i, row in enumerate(data["aliases"]):
                row.update(id=f"e{i:02}", weight=i % 5 + 1, required=False)
        inputs[504].append(data)
    for k in range(6):
        names = list("abcdefghij")[:7 + k % 4]
        aliases = []
        rng = random.Random(50400 + k)
        for i, (a, b) in enumerate(itertools.combinations(names, 2)):
            if rng.random() < 0.57 and len(aliases) < 21:
                aliases.append({"id": f"e{i:02}", "tenant": "t", "left": a, "right": b,
                                "weight": rng.randrange(1, 12), "required": i % 11 == 0})
        inputs[504].append({
            "version": 1, "orders": [legacy["_order_504"](i + 1, name) for i, name in enumerate(names)],
            "payments": [], "aliases": aliases,
            "cannot_link": [{"tenant": "t", "left": a, "right": b}
                            for a, b in zip(names[::2], reversed(names[1::2]), strict=False)],
        })
    inputs[504][0] = {
        "version": 1, "orders": [legacy["_order_504"](i + 1, c) for i, c in enumerate("abc")],
        "payments": [], "aliases": [
            {"id": "ab", "tenant": "t", "left": "a", "right": "b", "weight": 9, "required": False},
            {"id": "ac", "tenant": "t", "left": "a", "right": "c", "weight": 8, "required": False},
            {"id": "bc", "tenant": "t", "left": "b", "right": "c", "weight": 7, "required": False},
        ], "cannot_link": [{"tenant": "t", "left": "b", "right": "c"}],
    }
    impossible = copy.deepcopy(inputs[504][0])
    for row in impossible["aliases"]:
        row["required"] = True
    inputs[504].append(impossible)

    inputs[505] = []
    row505 = legacy["_row_505"]
    for i in range(12):
        records = [
            row505("a", "base", None, 0, "A", recorded=1),
            row505("b", "base", 0, 10, "A", recorded=1),
            row505("mask", "base", 3, 5, None, priority=5, recorded=2),
            row505("local", "middle", None, None, "fallback", recorded=1),
            row505("inherit", "middle", -4, 12, {"ref": "base", "offset": 2},
                   priority=2, recorded=2),
            row505("tip", "tip", None, None, {"ref": "middle", "offset": -3},
                   priority=1, recorded=1),
            row505("override", "tip", 2, 7, "override", priority=3, recorded=3),
        ]
        if i % 2:
            records.append(row505("mask", "base", 3, 5, None, priority=5,
                                 recorded=4, revision=2, deleted=True))
        if i % 3 == 0:
            records.append(row505("inherit", "moved", -4, 12, {"ref": "base", "offset": 5},
                                 priority=2, recorded=5, revision=2))
        if i >= 4:
            for n in range(i):
                records.append(row505(f"chain{n}", f"layer{n}", -n - 10, n + 30,
                                      {"ref": f"layer{n - 1}" if n else "tip", "offset": n - 4},
                                      priority=n % 3, recorded=2 + n % 3))
        rng = random.Random(50500 + i)
        rng.shuffle(records)
        inputs[505].append({
            "records": records, "cutoffs": [5, 1, 3, 3],
            "queries": [{"entity": name, "at": at, "asof": cutoff}
                        for name in ("middle", "tip") for at in (-5, 1, 4, 8)
                        for cutoff in (2, 4)],
        })
    inputs[505].extend([{"records": [], "cutoffs": [0], "queries": []},
                       {"records": [row505("a", "x", None, None, {"ref": "absent", "offset": 10})],
                        "cutoffs": [10], "queries": [{"entity": "x", "at": 0, "asof": 10}]}])

    inputs[508] = []
    for data in old[508]:
        data = copy.deepcopy(data)
        # Every legacy batch here uses original byte coordinates; choosing base0
        # deliberately makes multi-batch histories exercise rebasing.
        for batch in data["batches"]:
            batch["base"] = 0
        inputs[508].append(data)
    base = {"files": {"a": "éabcdef", "b": "copy"}, "batches": [
        {"id": "first", "base": 0, "guards": {}, "post": {}, "ops": [
            {"op": "edit", "path": "a", "spans": [{"start": 3, "end": 3, "old": "", "new": "插"}]},
            {"op": "rename", "path": "a", "target": "renamed"},
        ]},
        {"id": "stale", "base": 0, "guards": {"a": hashlib.sha256("éabcdef".encode()).hexdigest()},
         "post": {}, "ops": [
            {"op": "edit", "path": "a", "spans": [{"start": 5, "end": 7, "old": "de", "new": "D"}]},
        ]},
        {"id": "collision", "base": 0, "guards": {}, "post": {}, "ops": [
            {"op": "edit", "path": "a", "spans": [{"start": 2, "end": 5, "old": "abc", "new": "bad"}]},
        ]},
    ]}
    inputs[508][0] = base
    inputs[508][1] = {"files": {"a": "x"}, "batches": [
        {"id": "aba", "base": 0, "guards": {}, "post": {}, "ops": [
            {"op": "delete", "path": "a"}, {"op": "create", "path": "a", "text": "x"}]},
        {"id": "stale", "base": 0, "guards": {}, "post": {}, "ops": [
            {"op": "edit", "path": "a", "spans": [{"start": 0, "end": 1, "old": "x", "new": "y"}]}]},
    ]}
    inputs[508].append({"files": {"a": "ab"}, "batches": [
        {"id": "delete-bytes", "base": 0, "guards": {}, "post": {}, "ops": [
            {"op": "edit", "path": "a", "spans": [
                {"start": 0, "end": 2, "old": "ab", "new": ""},
            ]},
        ]},
        {"id": "collapsed-anchors", "base": 0, "guards": {}, "post": {}, "ops": [
            {"op": "edit", "path": "a", "spans": [
                {"start": 2, "end": 2, "old": "", "new": "Y"},
                {"start": 0, "end": 0, "old": "", "new": "X"},
            ]},
        ]},
    ]})
    for k in range(4):
        text = "α" + "".join(chr(97 + i % 26) for i in range(100))
        batches = []
        for i in range(40):
            position = 2 + i * 2
            batches.append({"id": f"b{i}", "base": 0, "guards": {}, "post": {}, "ops": [
                {"op": "edit", "path": "a", "spans": [
                    {"start": position, "end": position + (i % 3 == 0),
                     "old": text.encode()[position:position + (i % 3 == 0)].decode(),
                     "new": f"文{k}{i}"},
                ]},
            ]})
        inputs[508].append({"files": {"a": text}, "batches": batches})

    inputs[509] = copy.deepcopy(old[509])
    inputs[509][0] = legacy["_schedule_509"]({}, [
        ("a", "begin"), ("b", "begin"), ("a", "scan", {"low": "x", "high": "z"}),
        ("b", "scan", {"low": "a", "high": "c"}), ("a", "set", {"key": "b", "value": 1}),
        ("b", "set", {"key": "y", "value": 1}), ("a", "commit"), ("b", "commit"),
    ])
    inputs[509][1] = legacy["_schedule_509"]({}, [
        ("a", "begin"), ("a", "save", {"name": "s"}), ("b", "begin"),
        ("a", "scan", {"low": "x", "high": "z"}), ("b", "scan", {"low": "a", "high": "c"}),
        ("a", "rollback", {"name": "s"}), ("a", "set", {"key": "b", "value": 1}),
        ("b", "set", {"key": "y", "value": 1}), ("a", "commit"), ("b", "commit"),
    ])
    for size in (4, 9, 24, 60):
        events = [{"tx": f"t{i:02}", "op": "begin"} for i in range(size)]
        for i in range(size):
            events += [{"tx": f"t{i:02}", "op": "read", "key": f"k{(i + 1) % size:02}"},
                       {"tx": f"t{i:02}", "op": "set", "key": f"k{i:02}", "value": i}]
        events += [{"tx": f"t{i:02}", "op": "commit"} for i in range(size)]
        inputs[509].append({"state": {}, "events": events})

    inputs[510] = []
    for k, original in enumerate(old[510][:-1]):
        data = copy.deepcopy(original)
        for row in data["records"]:
            row["requires"] = []
        names = sorted(set(data["expected"]))
        data.update(
            jobs=[{"test": name, "duration": i % 3 + 2, "release": 0, "deadline": None,
                   "resource": 1, "workers": ["w0", "w1"]} for i, name in enumerate(names)],
            dependencies={}, workers=["w0", "w1"], capacity=2,
        )
        if k % 3 == 0 and data["records"]:
            data["records"][0]["requires"] = [{"test": "never-delivered", "attempt": 1}]
        inputs[510].append(data)
    for k, size in enumerate((5, 6, 7, 8, 9)):
        names = [f"t{i}" for i in range(size)]
        rng = random.Random(51000 + k)
        data = legacy["_report_510"]([], names)
        records = [legacy["_record_510"](name, status="passed", seq=i, observed=i)
                   for i, name in enumerate(names)]
        for i, row in enumerate(records):
            row["requires"] = [{"test": names[(i + 1) % size], "attempt": 1}]
        records += [{**records[-1], "seq": size + 1, "revision": 2, "status": "withdrawn"}]
        data.update(
            records=records,
            jobs=[{"test": name, "duration": rng.randrange(2, 8), "release": i % 3,
                   "deadline": None if k != 3 else 8, "resource": 1 + i % 2,
                   "workers": ["w0", "w1"] if i % 3 else ["w1"]}
                  for i, name in enumerate(names)],
            dependencies={name: [names[i - 2]] if i >= 2 and i % 2 else []
                          for i, name in enumerate(names)},
            workers=["w0", "w1"], capacity=3,
        )
        inputs[510].append(data)

    inputs[511] = []
    for k, data in enumerate(old[511]):
        data = copy.deepcopy(data)
        names = [row["id"] for row in data["tests"]]
        probes = [{"id": f"single-{name}", "tests": [name], "mode": "any",
                   "cost": 1 + i % 3, "available": row["can_rerun"]}
                  for i, (name, row) in enumerate(zip(names, data["tests"], strict=True))]
        for i in range(min(4, len(names))):
            probes.append({"id": f"group-{i}", "tests": [name for j, name in enumerate(names)
                                                        if (j >> i) & 1],
                           "mode": ("any", "count", "parity")[i % 3],
                           "cost": 1 + i % 2, "available": k % 5 != 4})
        data["probes"] = probes
        inputs[511].append(data)
    for k in range(4):
        names = list("abcdef")
        data = {"tests": [legacy["_test_511"](name) for name in names],
                "shards": [legacy["_shard_511"](names)], "probes": []}
        rng = random.Random(51100 + k)
        for i in range(12):
            data["probes"].append({"id": f"p{i:02}", "tests": rng.sample(names, rng.randrange(1, 5)),
                                   "mode": rng.choice(["any", "count", "parity"]),
                                   "cost": rng.randrange(0, 5), "available": True})
        inputs[511].append(data)
    inputs[511].append({
        "tests": [legacy["_test_511"](name) for name in "abcdef"],
        "shards": [legacy["_shard_511"]("abcdef")],
        "probes": [{"id": f"p{i}", "tests": list(names), "mode": "count",
                    "cost": cost, "available": True}
                   for i, (names, cost) in enumerate([
                       ("db", 5), ("efcb", 3), ("adeb", 2), ("af", 6),
                       ("d", 2), ("b", 1), ("cb", 7), ("b", 1),
                   ])],
    })
    return inputs


def _revision_artifacts(legacy, originals):
    old = {int(task.id.split("_")[1]): task for task in originals}
    result = {}
    files = dict(old[500].setup_files)
    objects = {name: files[path] for name, path in json.loads(files["backup/object-index.json"]).items()}
    snapshots = json.loads(files["backup/snapshots-a.json"]) + json.loads(files["backup/snapshots-b.json"])
    for i in range(10):
        name = f"region-{i:02}"
        objects[name] = ("shared-release" if i % 3 == 0 else f"region:{i % 4}\n") * 3
        snapshots.append({"id": name, "parent": ("release", "archive", "hotfix")[i % 3], "ops": [
            {"op": "drop", "path": "data"}, {"op": "drop", "path": "mirror"},
            {"op": "put", "path": "regional/payload", "object": name},
            {"op": "copy", "source": "config", "path": "regional/config"},
        ]})
    names = sorted(row["id"] for row in snapshots)
    policy = {
        "candidates": {name: 3 + (i * 7) % 23 for i, name in enumerate(names)},
        "mandatory": ["empty"],
        "requires": {"region-01": ["release"], "region-03": ["region-06"],
                     "region-06": ["region-03"], "hotfix": ["archive"],
                     "region-08": ["hotfix"], "region-09": ["release"]},
        "groups": [
            {"members": ["archive", "hotfix", "release", "root"], "min": 1, "max": 2},
            {"members": [name for name in names if name.startswith("region-")],
             "min": 3, "max": 7},
            {"members": ["draft", "experiment", "unretained-tip", "region-02"],
             "min": 0, "max": 1},
            {"members": ["region-00", "region-01", "region-05", "region-09"],
             "min": 1, "max": 2},
            {"members": ["region-03", "region-04", "region-06", "region-07", "region-08"],
             "min": 1, "max": 4},
        ],
        "max_physical": 195, "max_upload": 130,
    }
    files.pop("backup/retention.json")
    files["backup/policy.json"] = json.dumps(policy, ensure_ascii=False, indent=2) + "\n"
    files["backup/object-index.json"] = json.dumps({name: f"objects/{name}.txt" for name in objects})
    files.update({f"objects/{name}.txt": text for name, text in objects.items()})
    files["backup/snapshots-a.json"] = json.dumps(snapshots[::2], ensure_ascii=False)
    files["backup/snapshots-b.json"] = json.dumps(list(reversed(snapshots[1::2])), ensure_ascii=False)
    result[500] = (files, {"objects": objects, "snapshots": snapshots, "policy": policy,
                           "existing": json.loads(files["storage/inventory.json"])["existing"]})

    import csv
    import io

    files = dict(old[502].setup_files)
    invoices = list(csv.DictReader(io.StringIO(files["accounting/invoices.csv"])))
    for row in invoices:
        row["due"], row["amount"] = int(row["due"]), int(row["amount"])
    events = [json.loads(line) for name, content in files.items() if name.endswith(".jsonl")
              for line in content.splitlines()]
    seq = max(row["seq"] for row in events) + 1
    for i in range(8):
        invoices.append({"id": f"i-project-{i}", "customer": "Acme, Inc", "currency": "USD",
                         "due": i % 3, "amount": 50 + i * 3})
        events.append({"id": f"project-{i}", "seq": seq, "kind": "payment", "customer": "Acme, Inc",
                       "currency": "USD", "amount": 70 + i * 2, "invoice": f"i-project-{(i + 3) % 8}"})
        seq += 1
        if i % 2 == 0:
            events.append({"id": f"refund-{i}", "seq": seq, "kind": "refund",
                           "ref": f"project-{i}", "amount": 13 + i})
            seq += 1
    events.extend([
        {"id": "repair-r0", "seq": seq, "kind": "reverse", "ref": "refund-0"},
        {"id": "repair-r1", "seq": seq + 1, "kind": "reverse", "ref": "repair-r0"},
        {"id": "repair-r2", "seq": seq + 2, "kind": "reverse", "ref": "repair-r1"},
    ])
    payments = [row for row in events if row["kind"] == "payment"]
    routes = []
    for p, payment in enumerate(payments):
        for i, invoice in enumerate(invoices):
            if (p * 3 + i * 5) % 7 < 4 or payment["id"].removeprefix("project-") == (
                invoice["id"].removeprefix("i-project-")
            ):
                routes.append({"payment": payment["id"], "invoice": invoice["id"],
                               "cap": 25 + (p * 11 + i * 3) % 49, "fee": (p * 7 + i * 3) % 11})
    priority = {row["id"]: 1 + (i * 5) % 8 for i, row in enumerate(invoices)}
    minimums = {row["id"]: 15 if row["id"].startswith("i-project-") else 0 for row in invoices}
    for row in invoices:
        row["priority"] = priority[row["id"]]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=["id", "customer", "currency", "due", "amount"],
                            extrasaction="ignore")
    writer.writeheader()
    writer.writerows(reversed(invoices))
    files["accounting/invoices.csv"] = stream.getvalue()
    for i in range(3):
        files[f"accounting/events-{i}.jsonl"] = "".join(json.dumps(row) + "\n"
                                                       for row in reversed(events[i::3]))
    files["accounting/allocation.json"] = json.dumps(
        {"priority": priority, "routes": routes, "minimums": minimums}, indent=2
    ) + "\n"
    result[502] = (files, {"invoices": invoices, "events": events,
                         "routes": routes, "minimums": minimums})

    files = {path: text for path, text in old[506].setup_files.items() if path.startswith("configs/")}
    files["configs/release.ini"] = (
        '[gate]\r\nstate = "old" ; preserve\r\n'
        '[eu]\r\ncolor="none"\r\nendpoint="unset"\r\n'
        '[us]\ncolor = "none"\nendpoint="unset"\n'
        '[cache]\nmode="off"\n[final]\nmanifest="pending"'
    )
    files["configs/templates.ini"] = (
        '[blue]\nendpoint="https://blue/#safe" ; B\n'
        '[green]\r\nendpoint="https://green/文"\r\n'
        '[premium]\nendpoint="https://premium"\n'
        '[label]\nname = old \\\n second ; retain suffix\n'
    )
    plans = []

    def selector(section, key, raw=None, source="configs/release.ini"):
        return {"source": source, "section": section, "key": key, "which": 1, "raw": raw}

    def edit(section, key, value, source="configs/release.ini", which=1):
        return {"source": source, "section": section, "key": key, "which": which, "value": value}

    def plan(name, reward, cost, edits, *, guards=None, requires=None, excludes=None, mandatory=False):
        plans.append({"id": name, "reward": reward, "cost": cost, "mandatory": mandatory,
                      "requires": requires or [], "excludes": excludes or [],
                      "guards": guards or [], "edits": edits})

    plan("00-prepare", 1, 1, [edit("gate", "state", "ready"),
                             edit("label", "name", "release\n2026", "configs/templates.ini")],
         guards=[selector("gate", "state", '"old"')], mandatory=True)
    for region, offset in (("eu", 10), ("us", 20)):
        ids = [f"{offset + i:02}-{region}-{color}" for i, color in enumerate(("blue", "green", "premium"))]
        for i, color in enumerate(("blue", "green", "premium")):
            copy_selector = selector(color, "endpoint", source="configs/templates.ini")
            copy_selector.pop("raw")
            plan(ids[i], (12, 16, 27)[i] + (region == "us"), (3, 5, 9)[i],
                 [edit(region, "color", color),
                  {"source": "configs/release.ini", "section": region, "key": "endpoint",
                   "which": 1, "copy": copy_selector}],
                 guards=[selector("gate", "state", '"ready"'), selector(region, "color", '"none"')],
                 requires=["00-prepare"], excludes=[name for name in ids if name != ids[i]])
    plan("30-cache", 12, 4, [edit("cache", "mode", "on")],
         requires=["11-eu-green"], excludes=["12-eu-premium"])
    plan("31-warm", 8, 2, [edit("cache", "mode", "warm")],
         guards=[selector("cache", "mode", '"on"')], requires=["30-cache"])
    plan("40-workers", 7, 3, [edit("pool", "count", "24", "configs/workers.ini", "all")])
    plan("41-window", 8, 3, [edit("server", "port", "9443", "configs/windows.ini", "all")])
    plan("42-service", 10, 4, [edit("server", "message", "ready", "configs/service.ini"),
                              edit("server", "port", "9443", "configs/service.ini", "all")],
         requires=["41-window"])
    plan("43-rollback-cache", 19, 1, [edit("cache", "mode", "off")],
         guards=[selector("cache", "mode", '"warm"')], requires=["31-warm"])
    plan("44-port-copy", 11, 2, [{
        "source": "configs/mixed.ini", "section": "s", "key": "key", "which": "all",
        "copy": {"source": "configs/windows.ini", "section": "server", "key": "port", "which": 2},
    }], requires=["41-window"])
    plan("90-final", 1, 1, [edit("final", "manifest", "ready")],
         guards=[selector("eu", "color", '"green"'), selector("us", "color", '"blue"')],
         mandatory=True)
    release = {"plans": plans, "budget": 25, "final": [
        selector("final", "manifest", '"ready"'), selector("gate", "state", '"ready"'),
        selector("cache", "mode", '"warm"'),
    ]}
    source_files = dict(files)
    files["release-plan.json"] = json.dumps(release, ensure_ascii=False, indent=2) + "\n"
    result[506] = (files, {"files": source_files, **release})

    files = {
        "config/base.ini": (
            "@let LABEL=${ENV:LABEL:-root}\n[Base]\nhost=${ENV:HOST:-base}\nport=8000\n"
            "chain=B\nendpoint=${host}:${port}\ncaptured=${VAR:LABEL}\n"
            "[Region]\n@extends Base\nhost=eu\nchain=R>${super:chain}\n"
            "[Secure]\n@extends Base\nport=443\nchain=S>${super:chain}\n"
        ),
        "config/main.ini": (
            "@include base.ini\n@let LABEL=outer\n[Prod]\n@extends Region Secure\n"
            "chain=P>${super:chain}\n@include pieces/extra.ini\n"
            "after=${VAR:LABEL}\n[Audit]\n@extends Secure\nchain=A>${super:chain}\n"
            "[Canary]\n@extends Prod Audit\nchain=C>${super:chain}\n"
            "full=${endpoint}/${chain}/${captured}/${after}/${included}\n"
            "[Flip]\n@extends Secure Region\nchain=F>${super:chain}\n"
        ),
        "config/pieces/extra.ini": (
            "@let LABEL=inner-${VAR:LABEL}\nincluded=${VAR:LABEL}\n"
            "literal=$${super:chain}\n[Aux]\nx=${Canary.endpoint}|${Canary.chain}\n"
        ),
        "config/capture.ini": (
            "@include base.ini\n@let LABEL=one\n[First]\n@extends Region\n"
            "@include pieces/shared.ini\n@let LABEL=two\n[Second]\n@extends Secure\n"
            "@include pieces/shared.ini\n[Both]\n@extends First Second\n"
            "summary=${chain}|${captured}|${endpoint}\n"
        ),
        "config/pieces/shared.ini": (
            "captured=${VAR:LABEL}\nchain=${VAR:LABEL}>${super:chain}\n"
            "@let LABEL=local-only\n"
        ),
        "config/mro.ini": (
            "[O]\nx=O\n[A]\n@extends O\n[B]\n@extends O\n"
            "[X]\n@extends A B\n[Y]\n@extends B A\n[Z]\n@extends X Y\n"
        ),
        "config/virtual-cycle.ini": (
            "[Base]\nx=${y}\ny=ok\n[Child]\n@extends Base\ny=${x}\n"
        ),
        "config/inheritance-cycle.ini": "[A]\n@extends B\n[B]\n@extends A\n",
        "config/unknown.ini": "[A]\n@extends NoSuchProfile\nx=${also_missing}\n",
        "config/super-fallback.ini": (
            "[Base]\nx=\n[Left]\n@extends Base\nx=L${super:x:-no}\n"
            "[Right]\n@extends Base\nx=R${super:absent:-literal$cash}\n"
            "[Child]\n@extends Left Right\nx=C${super:x}\n"
        ),
        "config/late-override.ini": (
            "@include main.ini\n[Base]\nport=9000\n[Region]\nchain=newR>${super:chain}\n"
        ),
        "config/deep.ini": "@include main.ini\n" + "".join(
            f"[Layer{i}]\n@extends {'Layer' + str(i - 1) if i else 'Canary'}\n"
            f"chain=L{i}>${{super:chain}}\n" for i in range(18)
        ),
        "config/missing-super.ini": "[A]\nx=${super:absent}\n",
        "config/nested-missing.ini": "[A]\nx=${y:-safe}\ny=${missing}\n",
    }
    jobs = [{"id": name, "entry": f"config/{entry}.ini", "env": env}
            for name, entry, env in [
                ("prod", "main", {}), ("prod-env", "main", {"LABEL": "ship", "HOST": "remote"}),
                ("capture", "capture", {}), ("inconsistent-order", "mro", {}),
                ("virtual-cycle", "virtual-cycle", {}), ("inheritance-cycle", "inheritance-cycle", {}),
                ("unknown", "unknown", {}), ("super-fallback", "super-fallback", {}),
                ("late-override", "late-override", {}), ("deep", "deep", {}),
                ("missing-super", "missing-super", {}), ("nested-missing", "nested-missing", {}),
            ]]
    source_files = dict(files)
    files["jobs.json"] = json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2) + "\n"
    result[507] = (files, {"files": source_files, "jobs": jobs})
    return result


def build_revision(legacy, originals):
    from harness_bench.general_hard.common import artifact_task, program_task

    helpers = {
        501: ["_path_501", "_frame_501", "_solve_501"],
        503: ["_fraction_503", "_allocate_503"],
        504: ["_norm_504", "_solve_504"],
        505: [],
        508: ["_sha"],
        509: [],
        510: [],
        511: ["_solve_511"],
    }
    # Explicit evaluator binding; none of these names/files enters a workspace.
    for name in ("_solve_500", "_solve_501", "_solve_502", "_solve_504", "_solve_506",
                 "_solve_511", "_path_501", "_sha", "_fraction_503", "_allocate_503",
                 "_norm_504", "_spans_506", "_expand_507"):
        globals()[name] = legacy[name]
    algorithms = {
        501: [_revision_501],
        503: [_native_503, _quota_round_503, _revision_503],
        504: [_repair_aliases_504, _revision_504],
        505: [_timeline_505, _revision_505],
        508: [_rebase_span_508, _revision_508],
        509: [_serialization_edges_509, _revision_509],
        510: [_schedule_510, _revision_510],
        511: [_adaptive_511, _revision_511],
    }
    solvers = {number: globals()[f"_revision_{number}"] for number in range(500, 512)}
    inputs = _revision_inputs(legacy)
    cases = {number: [(data, solvers[number](data)) for data in rows]
             for number, rows in inputs.items()}
    prompts = _revision_prompts(legacy)
    original = {int(task.id.split("_")[1]): task for task in originals}
    tasks = []
    imports = (
        "import copy\nimport hashlib\nimport itertools\nimport json\nimport math\n"
        "import posixpath\nimport re\nimport sys\nimport unicodedata\n"
        "from fractions import Fraction\nfrom functools import lru_cache\n"
        "sys.set_int_max_str_digits(0)\n\n"
    )
    for number, rows in cases.items():
        source = imports + "\n\n".join(
            inspect.getsource(function)
            for function in [*(legacy[name] for name in helpers[number]), *algorithms[number]]
        )
        source += (f"\n\nfor _line in sys.stdin:\n"
                   f"    print(json.dumps(_revision_{number}(json.loads(_line)), ensure_ascii=False))\n")
        task = original[number]
        tasks.append(program_task(
            number=number, slug=task.id.split("_", 2)[2], name=task.name,
            parent=366 + (number - 500) // 2, prompt=prompts[number], cases=rows, gold=source,
            tags=("workflow", "exact", "hardening-v4"),
        ))
    artifact_inputs = {}
    for number, (files, data) in _revision_artifacts(legacy, originals).items():
        task = original[number]
        if number == 507:
            expected = {job["id"]: _revision_507({"files": data["files"], **job}) for job in data["jobs"]}
        else:
            expected = solvers[number](data)
        artifact_inputs[number] = data
        tasks.append(artifact_task(
            number=number, slug=task.id.split("_", 2)[2], name=task.name,
            parent=366 + (number - 500) // 2, prompt=prompts[number], files=files, expected=expected,
            tags=("workflow", "multi-file", "hardening-v4"),
        ))
    tasks.sort(key=lambda task: task.id)
    return tasks, cases, inputs, artifact_inputs, solvers
