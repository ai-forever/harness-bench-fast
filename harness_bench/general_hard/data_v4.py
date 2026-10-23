"""Substantive fourth-revision hardening of the data descendants.

The v1/v2 factories remain as regression fixtures in data_tasks. Only the tasks
returned here are registered. No reference implementation is placed in inputs.
"""

from __future__ import annotations

import copy
import inspect
import itertools
import json
import random
import re
from functools import lru_cache

from harness_bench.general_hard.common import artifact_task, dumps, program_task


def _standalone_dependency(*args, **kwargs):
    raise RuntimeError("Invoke these references through their compiled standalone source")


# legacy_source supplies the concrete dependencies inside each gold namespace.
parse = legacy_solve = union = subtract = _standalone_dependency

CLI = '\n\nimport json, sys\nfor line in sys.stdin:\n    print(json.dumps(solve(json.loads(line)),ensure_ascii=False,separators=(",",":")))\n'


def source(fn, *helpers):
    return (
        "from functools import lru_cache\nimport itertools, math, re, copy, sys\nsys.setrecursionlimit(20000)\n"
        + "\n\n".join(inspect.getsource(f) for f in (*helpers, fn))
        + "\nsolve = "
        + fn.__name__
        + CLI
    )


def legacy_source(task):
    return (
        task.gold_files["solution.py"]
        .split("\n\nimport json, sys\n")[0]
        .replace("def solve(x):", "def legacy_solve(x):")
    )


def original_prompt(task):
    return task.prompt.split("\n\nРеализуй solution.py:")[0].split("\n\nЗапиши результат")[0]


def upgraded_program(task, prompt, cases, gold):
    number = int(task.id.split("_")[1])
    return program_task(
        number=number,
        slug=task.id.split("_", 2)[2],
        name=task.name,
        parent=352 + (number - 472) // 2,
        prompt=prompt,
        cases=cases,
        gold=gold,
        tags=("hardening-v4", "hardening-v5") if number == 485 else ("hardening-v4",),
    )


def upgraded_artifact(task, prompt, files, expected):
    number = int(task.id.split("_")[1])
    return artifact_task(
        number=number,
        slug=task.id.split("_", 2)[2],
        name=task.name,
        parent=352 + (number - 472) // 2,
        prompt=prompt,
        files=files,
        expected=expected,
        tags=("hardening-v4",),
    )


def streaming_solve(x):
    # parse() is the independent character lexer from the original gold.
    state = {
        "high": {p: None for p in x["partitions"]},
        "active": set(x["partitions"]),
        "rows": {},
        "watermark": None,
    }
    checkpoints = {}
    emitted = {}
    audit = []
    snapshots = []
    for index, e in enumerate(x["events"], 1):
        op = e["op"]
        status = op
        if op == "record":
            row = parse(e["line"])
            if row is None:
                status = "invalid"
            elif e["partition"] not in state["active"]:
                status = "idle"
            else:
                old_watermark = state["watermark"]
                p = e["partition"]
                high = state["high"][p]
                state["high"][p] = row["ts"] if high is None else max(high, row["ts"])
                known = [state["high"][p] for p in state["active"]]
                if known and all(v is not None for v in known):
                    w = min(known) - x["lateness"]
                    state["watermark"] = w if old_watermark is None else max(old_watermark, w)
                key = (row["svc"], row["id"])
                if old_watermark is not None and row["ts"] < old_watermark:
                    status = "late"
                elif key in state["rows"] and e["rev"] <= state["rows"][key][0]:
                    status = "stale"
                else:
                    state["rows"][key] = (e["rev"], None if e["delete"] else row)
                    status = "deleted" if e["delete"] else "accepted"
        elif op in ("idle", "resume"):
            if op == "idle":
                state["active"].discard(e["partition"])
            else:
                state["active"].add(e["partition"])
            known = [state["high"][p] for p in state["active"]]
            if known and all(v is not None for v in known):
                w = min(known) - x["lateness"]
                state["watermark"] = w if state["watermark"] is None else max(w, state["watermark"])
        elif op == "rollback":
            state = copy.deepcopy(checkpoints[e["name"]])
        else:
            checkpoints[e["name"]] = copy.deepcopy(state)
            current = {}
            for _, row in state["rows"].values():
                if row is None:
                    continue
                start = (row["ts"] // x["width"]) * x["width"]
                if state["watermark"] is None or start + x["width"] > state["watermark"]:
                    continue
                key = (row["svc"], start)
                if key not in current:
                    current[key] = [0, 0]
                current[key][0] += 1
                current[key][1] += int(row["level"] == "ERROR")
            delta = []
            for key in sorted(set(emitted) | set(current)):
                before = emitted.get(key, [0, 0])
                after = current.get(key, [0, 0])
                if before != after:
                    delta.append(
                        {
                            "svc": key[0],
                            "start": key[1],
                            "count": after[0] - before[0],
                            "errors": after[1] - before[1],
                        }
                    )
            snapshots.append({"name": e["name"], "watermark": state["watermark"], "delta": delta})
            emitted = current
        audit.append({"event": index, "status": status})
    rows = sorted(
        [
            {"svc": k[0], "id": k[1], "rev": v[0], "ts": None if v[1] is None else v[1]["ts"]}
            for k, v in state["rows"].items()
        ],
        key=lambda r: (r["svc"], r["id"]),
    )
    return {"audit": audit, "checkpoints": snapshots, "rows": rows, "watermark": state["watermark"]}


def matching_solve(x):
    starts = sorted(x["starts"], key=lambda a: a["id"])
    ends = sorted(x["ends"], key=lambda b: b["id"])
    options = [
        [b for b in ends if a["kind"] in b["accept"] and a["min"] <= b["at"] - a["at"] <= a["max"]]
        for a in starts
    ]
    best = None
    winner = []

    def visit(i, used, pairs, cost):
        nonlocal best, winner
        if best is not None and len(pairs) + len(starts) - i < -best[0]:
            return
        selected = {a["id"] for a, b in pairs}
        for group in x["groups"]:
            done = {a["id"] for a in starts[:i] if a["id"] in group}
            if selected.intersection(group) and not done <= selected:
                return
        for kind, bounds in x["quotas"].items():
            count = sum(a["kind"] == kind for a, b in pairs)
            remain = sum(a["kind"] == kind for a in starts[i:])
            if count > bounds[1] or count + remain < bounds[0]:
                return
        if i == len(starts):
            if any(bool(selected.intersection(g)) and not set(g) <= selected for g in x["groups"]):
                return
            key = (-len(pairs), cost, tuple((a["id"], b["id"]) for a, b in pairs))
            if best is None or key < best:
                best = key
                winner = pairs[:]
            return
        a = starts[i]
        for b in options[i]:
            if b["id"] in used:
                continue
            selected_pairs = pairs + [(a, b)]
            times = sorted({t for s, e in selected_pairs for t in (s["at"], e["at"])})
            if any(
                sum(s["demands"].get(r, 0) for s, e in selected_pairs if s["at"] <= t < e["at"])
                > cap
                for r, cap in x["resources"].items()
                for t in times
            ):
                continue
            if any(
                sum(s["channel"] == ch and s["at"] <= t < e["at"] for s, e in selected_pairs) > cap
                for ch, cap in x["capacity"].items()
                for t in times
            ):
                continue
            visit(
                i + 1,
                used | {b["id"]},
                selected_pairs,
                cost + (b["at"] - a["at"] - a["target"]) ** 2,
            )
        visit(i + 1, used, pairs, cost)

    visit(0, set(), [], 0)
    if best is None:
        return {"pairs": None}
    return {
        "pairs": [[a["id"], b["id"]] for a, b in winner],
        "cost": best[1],
        "unmatched_starts": [
            a["id"] for a in starts if a["id"] not in {s["id"] for s, e in winner}
        ],
        "unmatched_ends": [b["id"] for b in ends if b["id"] not in {e["id"] for s, e in winner}],
    }


def transaction_solve(x):
    accepted = []
    transactions = []
    for tx in sorted(x["transactions"], key=lambda t: (t["at"], t["id"])):
        if tx["at"] > x["horizon"]:
            continue
        trial = accepted + [
            {**event, "at": tx["at"], "id": f"e{len(accepted) + i:06d}"}
            for i, event in enumerate(tx["events"])
        ]
        # Every event references a stable logical hold id. Translate it to replay id.
        names = {}
        materialized = []
        for event in trial:
            row = dict(event)
            if row["op"] == "reserve":
                names[row.pop("name")] = row["id"]
            if row["op"] in ("cancel", "ship"):
                row["hold"] = names.get(row["hold"], "_unknown_")
            materialized.append(row)
        report = legacy_solve({"lots": x["lots"], "events": materialized, "horizon": tx["at"]})
        failures = [
            (i, a["status"])
            for i, a in enumerate(report["audit"][-len(tx["events"]) :])
            if a["status"] != "accepted"
        ]
        if failures:
            transactions.append(
                {
                    "id": tx["id"],
                    "status": "aborted",
                    "failure": failures[0][0],
                    "reason": failures[0][1],
                }
            )
        else:
            accepted = trial
            transactions.append(
                {"id": tx["id"], "status": "committed", "failure": None, "reason": None}
            )
    names = {}
    materialized = []
    for event in accepted:
        row = dict(event)
        if row["op"] == "reserve":
            names[row.pop("name")] = row["id"]
        if row["op"] in ("cancel", "ship"):
            row["hold"] = names.get(row["hold"], "_unknown_")
        materialized.append(row)
    result = legacy_solve({"lots": x["lots"], "events": materialized, "horizon": x["horizon"]})
    logical = {v: k for k, v in names.items()}
    for hold in result["holds"]:
        hold["id"] = logical[hold["id"]]
    result["holds"].sort(key=lambda h: h["id"])
    del result["audit"]
    return {"ledger": result, "transactions": transactions}


def cache_repair_solve(x):
    rr = sorted(x["resources"], key=lambda r: r["id"])
    n = len(rr)
    bits = {r["id"]: 1 << i for i, r in enumerate(rr)}
    masks = [
        m
        for m in range(1 << n)
        if sum(r["bytes"] for i, r in enumerate(rr) if m >> i & 1) <= x["cache_bytes"]
    ]
    ids = {m: tuple(r["id"] for i, r in enumerate(rr) if m >> i & 1) for m in masks}
    size = {m: sum(r["bytes"] for i, r in enumerate(rr) if m >> i & 1) for m in range(1 << n)}
    fee = {m: sum(r["cost"] for i, r in enumerate(rr) if m >> i & 1) for m in range(1 << n)}
    states = {0: {0: (0, 0, 0, (), ())}}
    outgoing = {}
    for p in x["pieces"]:
        outgoing.setdefault(p["a"], []).append(p)
    for at in sorted({0, x["n"]} | set(outgoing)):
        available = sum(
            1 << i for i, r in enumerate(rr) if any(a <= at < b for a, b in r["available"])
        )
        for old, prior in list(states.get(at, {}).items()):
            for p in outgoing.get(at, []):
                required = sum(bits[k] for k in p["requires"])
                for new in masks:
                    if new & required != required:
                        continue
                    loaded = new & ~old
                    if loaded & available != loaded:
                        continue
                    score = (
                        prior[0] + p["cost"] + fee[loaded],
                        prior[1] + size[loaded],
                        prior[2] + 1,
                        prior[3] + (p["id"],),
                        prior[4] + (ids[new],),
                    )
                    target = states.setdefault(p["b"], {})
                    if new not in target or score < target[new]:
                        target[new] = score
    if not states.get(x["n"]):
        return {"plan": None}
    best = min(states[x["n"]].values())
    return {
        "plan": list(best[3]),
        "cost": best[0],
        "bytes": best[1],
        "cache": [list(v) for v in best[4]],
    }


def partition_options(x, layer):
    ids = sorted(x["ids"])
    out = []
    groups = []

    def visit(i):
        if i == len(ids):
            if not layer["groups"][0] <= len(groups) <= layer["groups"][1]:
                return
            owner = {v: k for k, g in enumerate(groups) for v in g}
            if any(owner[a] != owner[b] for a, b in layer["must"]) or any(
                owner[a] == owner[b] for a, b in layer["cannot"]
            ):
                return
            signature = sum(
                1 << k
                for k, (a, b) in enumerate(itertools.combinations(ids, 2))
                if owner[a] == owner[b]
            )
            score = sum(e["weight"] for e in layer["evidence"] if owner[e["a"]] == owner[e["b"]])
            out.append((tuple(tuple(g) for g in groups), signature, score))
            return
        for g in groups:
            if len(g) < layer["max_size"]:
                g.append(ids[i])
                visit(i + 1)
                g.pop()
        if len(groups) < layer["groups"][1]:
            groups.append([ids[i]])
            visit(i + 1)
            groups.pop()

    visit(0)
    return out


def partition_history_solve(x):
    layers = [partition_options(x, layer) for layer in x["layers"]]
    if any(not choices for choices in layers):
        return {"history": None}
    states = {}
    for groups, signature, score in layers[0]:
        states[(signature, 0)] = (-score, len(groups), (groups,))
    ids = sorted(x["ids"])
    pairs = list(itertools.combinations(ids, 2))
    weights = {tuple(sorted((e["a"], e["b"]))): e["cost"] for e in x["change_costs"]}

    @lru_cache(None)
    def cost(mask):
        return sum(weights.get(p, 0) for i, p in enumerate(pairs) if mask >> i & 1)

    for choices in layers[1:]:
        nxt = {}
        for groups, sig, score in choices:
            for (old, used), value in states.items():
                changed = (old ^ sig).bit_count()
                count = used + changed
                if count > x["change_budget"]:
                    continue
                candidate = (
                    value[0] - score + cost(old ^ sig),
                    value[1] + len(groups),
                    value[2] + (groups,),
                )
                key = (sig, count)
                if key not in nxt or candidate < nxt[key]:
                    nxt[key] = candidate
        states = nxt
    if not states:
        return {"history": None}
    (_, changes), best = min(
        states.items(), key=lambda kv: (kv[1][0], kv[0][1], kv[1][1], kv[1][2])
    )
    return {
        "history": [[list(g) for g in layer] for layer in best[2]],
        "score": -best[0],
        "changes": changes,
        "groups": best[1],
    }


class MacroParser:
    def __init__(self, text):
        self.s = text
        self.p = 0

    def ws(self):
        while self.p < len(self.s) and self.s[self.p] in " \t\r\n":
            self.p += 1

    def take(self, t):
        self.ws()
        if self.s.startswith(t, self.p):
            self.p += len(t)
            return True
        return False

    def need(self, t):
        if not self.take(t):
            raise ValueError("syntax")

    def number(self):
        self.ws()
        m = re.match(r"-?(?:0|[1-9][0-9]*)", self.s[self.p :])
        if not m:
            raise ValueError("number")
        self.p += len(m.group())
        return int(m.group())

    def name(self):
        self.ws()
        m = re.match(r"[A-Za-z_][A-Za-z_0-9]*", self.s[self.p :])
        if not m:
            raise ValueError("name")
        self.p += len(m.group())
        return m.group()

    def atom(self):
        if self.take("("):
            node = self.expr()
            self.need(")")
        elif self.take("$"):
            node = ("var", self.name())
        elif self.take("@"):
            name = self.name()
            self.need("(")
            args = []
            if not self.take(")"):
                args.append(self.expr())
                while self.take(","):
                    args.append(self.expr())
                self.need(")")
            node = ("call", name, tuple(args))
        elif self.take("let"):
            self.need("(")
            name = self.name()
            self.need(",")
            value = self.expr()
            self.need(",")
            body = self.expr()
            self.need(")")
            node = ("let", name, value, body)
        elif self.take("ifempty"):
            self.need("(")
            test = self.expr()
            self.need(",")
            a = self.expr()
            self.need(",")
            b = self.expr()
            self.need(")")
            node = ("ifempty", test, a, b)
        else:
            a = self.number()
            b = a
            step = 1
            if self.take(".."):
                b = self.number()
                if self.take("/"):
                    step = self.number()
            node = ("range", a, b, step)
        while True:
            if self.take("~"):
                node = ("reverse", node)
            elif self.take("["):
                a = self.number()
                self.need(":")
                b = self.number()
                s = self.number() if self.take(":") else 1
                self.need("]")
                node = ("slice", node, a, b, s)
            else:
                return node

    def inter(self):
        node = self.atom()
        while self.take("&"):
            node = ("&", node, self.atom())
        return node

    def expr(self):
        node = self.inter()
        while True:
            op = next((t for t in ("|", "\\", "^") if self.take(t)), None)
            if op is None:
                return node
            node = (op, node, self.inter())

    def all(self):
        node = self.expr()
        self.ws()
        if self.p != len(self.s):
            raise ValueError("trailing")
        return node


def macro_solve(x):
    try:
        functions = {}

        def check(node, scope, known):
            op = node[0]
            if op == "range":
                return
            if op == "var":
                if node[1] not in scope:
                    raise ValueError("free")
            elif op == "call":
                if node[1] not in known or len(node[2]) != len(functions[node[1]][0]):
                    raise ValueError("call")
                for arg in node[2]:
                    check(arg, scope, known)
            elif op == "let":
                check(node[2], scope, known)
                check(node[3], scope | {node[1]}, known)
            elif op == "slice":
                check(node[1], scope, known)
            else:
                for child in node[1:]:
                    check(child, scope, known)

        for definition in x["definitions"]:
            name = definition["name"]
            params = definition["params"]
            if (
                not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name)
                or name in functions
                or len(set(params)) != len(params)
                or any(not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", p) for p in params)
            ):
                raise ValueError("definition")
            ast = MacroParser(definition["expr"]).all()
            check(ast, set(params), set(functions))
            functions[name] = (tuple(params), ast)
        ast = MacroParser(x["expr"]).all()
        check(ast, set(), set(functions))

        @lru_cache(None)
        def call(name, args):
            params, body = functions[name]
            return evaluate(body, dict(zip(params, args, strict=True)))

        def evaluate(node, env):
            op = node[0]
            if op == "range":
                _, a, b, s = node
                if s <= 0:
                    raise ValueError("step")
                return tuple(range(a, b + (1 if b >= a else -1), (1 if b >= a else -1) * s))
            if op == "var":
                return env[node[1]]
            if op == "call":
                return call(node[1], tuple(evaluate(a, env) for a in node[2]))
            if op == "let":
                value = evaluate(node[2], env)
                return evaluate(node[3], {**env, node[1]: value})
            if op == "ifempty":
                return evaluate(node[2] if not evaluate(node[1], env) else node[3], env)
            a = evaluate(node[1], env)
            if op == "reverse":
                return a[::-1]
            if op == "slice":
                if node[4] == 0:
                    raise ValueError("slice")
                return a[node[2] : node[3] : node[4]]
            b = evaluate(node[2], env)
            sa = set(a)
            sb = set(b)
            if op == "|":
                return a + tuple(v for v in b if v not in sa)
            if op == "&":
                return tuple(v for v in a if v in sb)
            if op == "\\":
                return tuple(v for v in a if v not in sb)
            return tuple(v for v in a if v not in sb) + tuple(v for v in b if v not in sa)

        return {"values": list(evaluate(ast, {}))}
    except ValueError:
        return {"error": "invalid"}


class Rope:
    def __init__(self):
        self.nodes = [None]
        self.memo = {}

    def leaf(self, a, d, n):
        if not n:
            return 0
        key = ("leaf", a, d, n)
        if key not in self.memo:
            self.memo[key] = len(self.nodes)
            self.nodes.append((0, a, d, n, n * (2 * a + (n - 1) * d) // 2))
        return self.memo[key]

    def length(self, p):
        return self.nodes[p][3] if p else 0

    def total(self, p):
        return self.nodes[p][4] if p else 0

    def cat(self, a, b):
        if not a or not b:
            return a or b
        key = ("cat", a, b)
        if key not in self.memo:
            self.memo[key] = len(self.nodes)
            self.nodes.append(
                (1, a, b, self.length(a) + self.length(b), self.total(a) + self.total(b))
            )
        return self.memo[key]

    def reverse(self, p):
        if not p:
            return 0
        node = self.nodes[p]
        if node[0] == 0:
            return self.leaf(node[1] + node[2] * (node[3] - 1), -node[2], node[3])
        if node[0] == 2:
            return node[1]
        key = ("reverse", p)
        if key not in self.memo:
            self.memo[key] = len(self.nodes)
            self.nodes.append((2, p, 0, node[3], node[4]))
        return self.memo[key]

    def split(self, p, k):
        if k <= 0:
            return 0, p
        if k >= self.length(p):
            return p, 0
        node = self.nodes[p]
        if node[0] == 0:
            return self.leaf(node[1], node[2], k), self.leaf(
                node[1] + node[2] * k, node[2], node[3] - k
            )
        if node[0] == 2:
            a, b = self.split(node[1], node[3] - k)
            return self.reverse(b), self.reverse(a)
        a, b = node[1:3]
        size = self.length(a)
        if k < size:
            left, r = self.split(a, k)
            return left, self.cat(r, b)
        left, r = self.split(b, k - size)
        return self.cat(a, left), r

    def slice(self, p, a, b):
        a = max(0, a)
        b = min(self.length(p), b)
        if a >= b:
            return 0
        _, r = self.split(p, a)
        return self.split(r, b - a)[0]

    def select(self, p, k):
        if not 0 <= k < self.length(p):
            return None
        node = self.nodes[p]
        if node[0] == 0:
            return node[1] + node[2] * k
        if node[0] == 2:
            return self.select(node[1], node[3] - 1 - k)
        a, b = node[1:3]
        return self.select(a, k) if k < self.length(a) else self.select(b, k - self.length(a))

    def filter(self, p, lo, hi, inside):
        @lru_cache(None)
        def go(q):
            if not q:
                return 0
            node = self.nodes[q]
            if node[0] == 0:
                a, d, n = node[1:4]
                low = min(a, a + d * (n - 1))
                high = max(a, a + d * (n - 1))
                left = max(low, lo)
                h = min(high, hi)
                if left > h:
                    return 0 if inside else q
                i, j = (left - a, h - a + 1) if d == 1 else (a - h, a - left + 1)
                if inside:
                    return self.leaf(a + d * i, d, j - i)
                return self.cat(self.leaf(a, d, i), self.leaf(a + d * j, d, n - j))
            if node[0] == 2:
                return self.reverse(go(node[1]))
            return self.cat(go(node[1]), go(node[2]))

        return go(p)

    def covered(self, p):
        stack = [p]
        seen = set()
        spans = []
        while stack:
            q = stack.pop()
            if not q or q in seen:
                continue
            seen.add(q)
            node = self.nodes[q]
            if node[0] == 0:
                spans.append(
                    (
                        min(node[1], node[1] + node[2] * (node[3] - 1)),
                        max(node[1], node[1] + node[2] * (node[3] - 1)),
                    )
                )
            elif node[0] == 2:
                stack.append(node[1])
            else:
                stack.extend(node[1:3])
        result = []
        for a, b in sorted(spans):
            if result and a <= result[-1][1] + 1:
                result[-1][1] = max(b, result[-1][1])
            else:
                result.append([a, b])
        return result

    def rank(self, p, value):
        @lru_cache(None)
        def go(q, rev):
            if not q:
                return None
            node = self.nodes[q]
            if node[0] == 0:
                a, d, n = node[1:4]
                i = (value - a) * d
                return (n - 1 - i if rev else i) if 0 <= i < n else None
            if node[0] == 2:
                return go(node[1], not rev)
            a, b = node[1:3]
            if rev:
                a, b = b, a
            found = go(a, rev)
            if found is not None:
                return found
            found = go(b, rev)
            return None if found is None else self.length(a) + found

        return go(p, False)

    def hash(self, p, base, mod):
        @lru_cache(None)
        def progression(n):
            # Return base**n, sum base**i, sum i*base**i; division-free for composite moduli.
            if not n:
                return 1, 0, 0
            k = n // 2
            p, s, t = progression(k)
            power = p * p % mod
            ss = (s + p * s) % mod
            tt = (t + p * (t + k * s)) % mod
            if n % 2:
                return power * base % mod, (ss + power) % mod, (tt + 2 * k * power) % mod
            return power, ss, tt

        @lru_cache(None)
        def go(q, rev):
            if not q:
                return 0
            node = self.nodes[q]
            if node[0] == 0:
                a, d, n = node[1:4]
                if rev:
                    a, d = a + d * (n - 1), -d
                _, s, t = progression(n)
                return (a * s + d * t) % mod
            if node[0] == 2:
                return go(node[1], not rev)
            a, b = node[1:3]
            if rev:
                a, b = b, a
            return (go(a, rev) + pow(base, self.length(a), mod) * go(b, rev)) % mod

        return go(p, False)


def rope_solve(x):
    rope = Rope()
    versions = [0]
    sizes = []
    for event in x["ops"]:
        p = versions[event["base"]]
        op = event["op"]
        if op in ("add", "remove", "move"):
            a, b = event["a"], event["b"]
            lo, hi = sorted((a, b))
            if op == "add":
                spans = [(lo, hi)]
                for left, h in rope.covered(p):
                    spans = [
                        piece
                        for u, v in spans
                        for piece in ((u, min(v, left - 1)), (max(u, h + 1), v))
                        if piece[0] <= piece[1]
                    ]
                if a > b:
                    spans.reverse()
                for u, v in spans:
                    p = rope.cat(p, rope.leaf(u if a <= b else v, 1 if a <= b else -1, v - u + 1))
            else:
                outside = rope.filter(p, lo, hi, False)
                p = outside if op == "remove" else rope.cat(outside, rope.filter(p, lo, hi, True))
        elif op == "reverse":
            p = rope.reverse(p)
        elif op == "splice":
            left, rest = rope.split(p, event["at"])
            _, right = rope.split(rest, event["delete"])
            part = rope.slice(versions[event["source"]], event["start"], event["stop"])
            if event["reverse"]:
                part = rope.reverse(part)
            p = rope.cat(rope.cat(left, part), right)
        else:
            left, rest = rope.split(p, event["start"])
            mid, right = rope.split(rest, event["stop"] - event["start"])
            n = rope.length(mid)
            k = event["by"] % n if n else 0
            a, b = rope.split(mid, n - k)
            p = rope.cat(rope.cat(left, rope.cat(b, a)), right)
        versions.append(p)
        sizes.append(rope.length(p))
    answers = []
    for query in x["queries"]:
        p = versions[query["version"]]
        op = query["op"]
        if op == "rank":
            answers.append(rope.rank(p, query["value"]))
        elif op == "select":
            answers.append(rope.select(p, query["index"]))
        else:
            sub = rope.slice(p, query["start"], query["stop"])
            answers.append(
                rope.total(sub) if op == "sum" else rope.hash(sub, query["base"], query["mod"])
            )
    return {"sizes": sizes, "answers": answers}


def mitigation_solve(x):
    report = legacy_solve(x)
    h = x["horizon"]
    w = x["mitigation"]["width"]
    k = x["mitigation"]["limit"]
    budget = x["mitigation"]["budget"]
    curves = {}
    points = {0, h}
    for resource, cap in x["capacity"].items():
        holes = union([(max(0, a), min(h, b)) for a, b in x["maintenance"].get(resource, [])])
        intervals = []
        for request in x["requests"]:
            if request["resource"] == resource:
                active = union([(max(0, a), min(h, b)) for a, b in request["intervals"]])
                intervals.extend(subtract(active, holes))
        ends = sorted({0, h} | {t for a, b in intervals for t in (a, b)})
        curves[resource] = [
            (a, b, max(0, sum(left <= a < r for left, r in intervals) - cap))
            for a, b in zip(ends, ends[1:], strict=False)
        ]
        points.update(ends)
    total = sum(
        (b - a) * v * x["mitigation"]["weights"][r]
        for r, curve in curves.items()
        for a, b, v in curve
    )
    starts = sorted(
        {t + j * w for t in points for j in range(-k, k + 1) if 0 <= t + j * w <= h - w}
    )
    # Uniform length windows: any optimum can be shifted to a curve breakpoint
    # or a touching chain rooted there; endpoints +/- k*width suffice.
    modes = sorted(x["mitigation"]["modes"], key=lambda m: m["id"])
    tables = []
    import bisect

    for start in starts:
        previous = bisect.bisect_right(starts, start - w) - 1
        table = dict(tables[-1]) if tables else {(0, 0): (0, ())}
        base = tables[previous] if previous >= 0 else {(0, 0): (0, ())}
        for mode in modes:
            benefit = sum(
                max(0, min(b, start + w) - max(a, start))
                * min(v, mode["relief"].get(r, 0))
                * x["mitigation"]["weights"][r]
                for r, curve in curves.items()
                for a, b, v in curve
            )
            for (count, cost), (negative, plan) in base.items():
                target = (count + 1, cost + mode["cost"])
                if target[0] > k or target[1] > budget:
                    continue
                value = (negative - benefit, plan + ((start, mode["id"]),))
                if target not in table or value < table[target]:
                    table[target] = value
        tables.append(table)
    table = tables[-1] if tables else {(0, 0): (0, ())}
    (count, cost), best = min(
        table.items(), key=lambda item: (item[1][0], item[0][1], item[0][0], item[1][1])
    )
    report["mitigation"] = {
        "residual": total + best[0],
        "cost": cost,
        "windows": [[a, a + w, mode] for a, mode in best[1]],
    }
    return report


def incident_remediation(reports, policy):
    # A target is a distinct (service,fingerprint), weighted separately in each snapshot.
    keys = sorted(
        {(r["service"], r["fingerprint"]) for report in reports for r in report["incidents"]}
    )
    index = {key: i for i, key in enumerate(keys)}
    weights = []
    for report in reports:
        row = [0] * len(keys)
        for r in report["incidents"]:
            row[index[(r["service"], r["fingerprint"])]] += (
                r["end"] - r["start"] + policy["linger"]
            ) * len(r["ids"])
        weights.append(row)
    actions = sorted(policy["actions"], key=lambda a: a["id"])
    byid = {a["id"]: i for i, a in enumerate(actions)}
    groups = {
        kind: sum(1 << i for i, a in enumerate(actions) if a["kind"] == kind)
        for kind in ("repair", "guard")
    }
    cover = [
        sum(1 << i for i, a in enumerate(actions) if list(key) in a["targets"]) for key in keys
    ]
    best = None
    winner = None
    forced = {a["id"]: None for a in actions}
    banned = {a["id"]: None for a in actions}

    def visit(i, mask, cost, load):
        nonlocal best, winner
        if cost > policy["budget"] or any(
            load.get(r, 0) > v for r, v in policy["capacity"].items()
        ):
            return
        if i < len(actions):
            visit(i + 1, mask, cost, load)
            a = actions[i]
            visit(
                i + 1,
                mask | (1 << i),
                cost + a["cost"],
                {r: load.get(r, 0) + a["load"].get(r, 0) for r in policy["capacity"]},
            )
            return
        if any(
            mask >> j & 1 and any(not (mask >> byid[k] & 1) for k in a["requires"])
            for j, a in enumerate(actions)
        ):
            return
        if any(sum(bool(mask >> byid[k] & 1) for k in group) > 1 for group in policy["exclusive"]):
            return
        selected = tuple(a["id"] for j, a in enumerate(actions) if mask >> j & 1)
        scenario_rows = []
        for scenario in policy["failures"]:
            live = mask & ~sum(1 << byid[k] for k in scenario["disabled"])
            residual = [
                sum(
                    value
                    for value, c in zip(row, cover, strict=True)
                    if not (live & c & groups["repair"] and live & c & groups["guard"])
                )
                for row in weights
            ]
            scenario_rows.append(residual)
        objective = (
            max(v for row in scenario_rows for v in row),
            sum(v for row in scenario_rows for v in row),
            cost,
            len(selected),
            selected,
        )
        if best is None or objective < best:
            best = objective
            winner = scenario_rows
        for j, a in enumerate(actions):
            table = forced if mask >> j & 1 else banned
            if table[a["id"]] is None or objective < table[a["id"]]:
                table[a["id"]] = objective

    visit(0, 0, 0, {})

    def value(key):
        return (
            None
            if key is None
            else {"worst": key[0], "total": key[1], "cost": key[2], "actions": list(key[4])}
        )

    return {
        **value(best),
        "residuals": [
            {"scenario": scenario["id"], "snapshots": row}
            for scenario, row in zip(policy["failures"], winner, strict=True)
        ],
        "sensitivity": [
            {
                "action": a["id"],
                "required": value(forced[a["id"]]),
                "forbidden": value(banned[a["id"]]),
            }
            for a in actions
        ],
    }


def constrained_session_options(starts, finishes, policy, required, forbidden):
    keys = sorted(starts)
    best = -1
    out = []

    def visit(i, pairs, used, cost):
        nonlocal best, out
        if len(pairs) + len(keys) - i < best:
            return
        if i == len(keys):
            if not set(required) <= set(pairs.items()) or set(forbidden) & set(pairs.items()):
                return
            for key in pairs:
                if any(
                    parent not in pairs or finishes[pairs[parent]]["at"] > starts[key]["at"]
                    for parent in policy["sessions"][key]["requires"]
                ):
                    return
            if len(pairs) > best:
                best = len(pairs)
                out = []
            if len(pairs) == best:
                out.append((tuple(sorted(pairs.items())), cost))
            return
        key = keys[i]
        a = starts[key]
        rule = policy["sessions"][key]
        for finish, b in sorted(finishes.items()):
            if (
                finish in used
                or (key, finish) in forbidden
                or rule["label"] not in b["accept"]
                or not rule["min"] <= b["at"] - a["at"] <= rule["max"]
            ):
                continue
            spans = [
                (starts[s]["at"], finishes[f]["at"])
                for s, f in pairs.items()
                if policy["sessions"][s]["resource"] == rule["resource"]
            ] + [(a["at"], b["at"])]
            if any(
                sum(left <= t < r for left, r in spans) > policy["capacity"][rule["resource"]]
                for t in {left for left, r in spans}
            ):
                continue
            pairs[key] = finish
            visit(
                i + 1,
                pairs,
                used | {finish},
                cost + rule["weight"] * abs(b["at"] - a["at"] - rule["target"]),
            )
            del pairs[key]
        visit(i + 1, pairs, used, cost)

    visit(0, {}, set(), 0)
    return best, out


def session_frontier(d, rows, policy, queries, restrictions):
    hosts = sorted(policy["clocks"])
    answers = []
    for restriction in restrictions:
        best = None
        best_offsets = None
        best_history = None
        for choices in itertools.product(*(policy["clocks"][h] for h in hosts)):
            offsets = {h: c["offset"] for h, c in zip(hosts, choices, strict=True)}
            if any(offsets[h] != v for h, v in restriction["clock_pin"].items()):
                continue
            if any(
                not r["min"] <= offsets[r["left"]] - offsets[r["right"]] <= r["max"]
                for r in policy["relations"]
            ):
                continue
            views = [d._reconstruction_view(rows, policy, q, offsets) for q in queries]
            alternatives = []
            for query, view in zip(queries, views, strict=True):
                required = [
                    (r["start"], r["finish"])
                    for r in restriction["require"]
                    if r["snapshot"] == query["name"]
                ]
                forbidden = [
                    (r["start"], r["finish"])
                    for r in restriction["forbid"]
                    if r["snapshot"] == query["name"]
                ]
                alternatives.append(
                    constrained_session_options(view[0], view[1], policy, required, forbidden)
                )
            if any(not choices for _, choices in alternatives):
                continue
            cardinality = sum(count for count, _ in alternatives)
            if best is not None and cardinality < -best[0]:
                continue
            states = {pairs: (cost, 0, (pairs,)) for pairs, cost in alternatives[0][1]}
            for i, (_, options) in enumerate(alternatives[1:], 1):
                common = set(views[i - 1][0]) & set(views[i][0])
                nxt = {}
                for pairs, cost in options:
                    for old, value in states.items():
                        transition, changes = d._reconstruction_transition(
                            old, pairs, common, policy
                        )
                        candidate = (
                            value[0] + cost + transition,
                            value[1] + changes,
                            value[2] + (pairs,),
                        )
                        if pairs not in nxt or candidate < nxt[pairs]:
                            nxt[pairs] = candidate
                states = nxt
            value = min(states.values())
            key = (
                -cardinality,
                value[0] + sum(c["cost"] for c in choices),
                value[1],
                tuple(offsets[h] for h in hosts),
                value[2],
            )
            if best is None or key < best:
                best = key
                best_offsets = offsets
                best_history = value[2]
        answers.append(
            {
                "id": restriction["id"],
                "result": None
                if best is None
                else {
                    "pairs": -best[0],
                    "cost": best[1],
                    "changes": best[2],
                    "clock_offsets": best_offsets,
                    "history": [[list(p) for p in layer] for layer in best_history],
                },
            }
        )
    return answers


def warehouse_orders(report, policy):
    available = {(b["warehouse"], b["sku"]): b["available"] for b in report["bins"]}
    catalog = {r["sku"]: r for r in report["catalog"]}
    orders = sorted(policy["orders"], key=lambda o: o["id"])
    best = None
    best_remaining = None

    def visit(i, stock, load, opened, selected, priority, revenue):
        nonlocal best, best_remaining
        if i == len(orders):
            chosen = {v[0] for v in selected}
            if any(o["id"] in chosen and not set(o["requires"]) <= chosen for o in orders):
                return
            if any(sum(o in chosen for o in group) > 1 for group in policy["exclusive"]):
                return
            key = (-priority, -revenue, len(opened), tuple(selected))
            if best is None or key < best:
                best = key
                best_remaining = stock
            return
        order = orders[i]
        visit(i + 1, stock, load, opened, selected, priority, revenue)
        if order["sku"] not in catalog:
            return
        for wh in order["warehouses"]:
            for alternative in policy["alternatives"].get(
                order["sku"],
                [
                    {
                        "id": "original",
                        "requirements": catalog[order["sku"]]["requirements"],
                        "fee": 0,
                    }
                ],
            ):
                used = {
                    leaf: qty * order["qty"] for leaf, qty in alternative["requirements"].items()
                }
                if (
                    any(stock.get((wh, leaf), 0) < qty for leaf, qty in used.items())
                    or load.get(wh, 0) + sum(used.values()) > policy["capacity"][wh]
                ):
                    continue
                cost = alternative["fee"] * order["qty"] + (
                    0 if wh in opened else policy["activation"][wh]
                )
                updated = dict(stock)
                for leaf, qty in used.items():
                    updated[(wh, leaf)] -= qty
                visit(
                    i + 1,
                    updated,
                    {**load, wh: load.get(wh, 0) + sum(used.values())},
                    opened | {wh},
                    selected + [(order["id"], wh, alternative["id"])],
                    priority + order["priority"],
                    revenue + catalog[order["sku"]]["price_cents"] * order["qty"] - cost,
                )

    visit(0, available, {}, set(), [], 0, 0)
    return {
        "priority": -best[0],
        "net_cents": -best[1],
        "warehouses": best[2],
        "assignments": [list(v) for v in best[3]],
        "remaining": [
            {"warehouse": wh, "sku": sku, "qty": qty}
            for (wh, sku), qty in sorted(best_remaining.items())
        ],
    }


def restore_shards(shards, policy):
    options = []
    for shard in shards:
        files = shard["files"]
        bad = {r["file"] for r in shard["audit"]["invalid"]}
        duplicate = {name for group in shard["audit"]["duplicates"] for name in group[1:]}
        valid = {
            name: json.loads(text) for name, text in files.items() if name not in bad | duplicate
        }
        chains = []

        def extend(at, parent, chain, chains=chains, valid=valid):
            chains.append(tuple(chain))
            for name, value in sorted(valid.items()):
                if value["start"] == at and value["parent"] == parent:
                    extend(value["end"] + 1, value["digest"], chain + [name])

        extend(0, "ROOT", [])
        rows = []
        for chain in chains:
            values = [valid[name] for name in chain]
            records = {r["seq"]: r["value"] for v in values for r in v["records"]}
            donor = [policy["segments"][shard["id"] + "/" + name]["donor"] for name in chain]
            rows.append(
                (
                    chain,
                    values[-1]["end"] + 1 if values else 0,
                    sum(v["generation"] for v in values),
                    donor,
                    records,
                )
            )
        options.append(rows)
    best = None
    winner = None

    def visit(i, chosen, loads, donors):
        nonlocal best, winner
        if any(loads.get(d, 0) > r["capacity"] for d, r in policy["donors"].items()):
            return
        cost = sum(policy["donors"][d]["cost"] for d in donors)
        if cost > policy["budget"]:
            return
        if i == len(shards):
            for link in policy["checks"]:
                a, b = chosen[link["left"]], chosen[link["right"]]
                seq = link["seq"]
                if seq in a[4] and seq in b[4] and a[4][seq] != b[4][seq]:
                    return
            key = (
                -min(r[1] for r in chosen),
                -sum(r[1] for r in chosen),
                -sum(r[2] for r in chosen),
                cost,
                sum(len(r[0]) for r in chosen),
                tuple(r[0] for r in chosen),
            )
            if best is None or key < best:
                best = key
                winner = chosen[:]
            return
        for row in options[i]:
            new = dict(loads)
            for donor in row[3]:
                new[donor] = new.get(donor, 0) + 1
            visit(i + 1, chosen + [row], new, donors | set(row[3]))

    visit(0, [], {}, set())
    return {
        "minimum_covered": -best[0],
        "total_covered": -best[1],
        "generation_sum": -best[2],
        "cost": best[3],
        "selected": [
            {"shard": shard["id"], "files": list(row[0]), "covered": row[1]}
            for shard, row in zip(shards, winner, strict=True)
        ],
    }


def survival_report(reports, policy):
    from fractions import Fraction

    def rational(v):
        return [v.numerator, v.denominator]

    output = []
    for snapshot in reports:
        groups = []
        for endpoint in ["*"] + sorted({r["endpoint"] for r in snapshot["requests"]}):
            observations = []
            excluded = []
            for r in snapshot["requests"]:
                if endpoint != "*" and r["endpoint"] != endpoint:
                    continue
                entry = policy["subjects"][r["request"]]["entry"]
                weight = Fraction(*policy["subjects"][r["request"]]["weight"])
                if entry > r["latency"]:
                    excluded.append(r["request"])
                    continue
                observations.append((entry, r["latency"], r["status"], weight))
            survival = Fraction(1)
            incidence = {cause: Fraction(0) for cause in ("complete", "failed", "cancelled")}
            points = []
            area = Fraction(0)
            last = 0
            median = None
            for time in sorted(
                {o[1] for o in observations if o[1] <= policy["tau"]} | {policy["tau"]}
            ):
                area += survival * (time - last)
                last = time
                risk = sum((w for a, b, s, w in observations if a <= time <= b), Fraction(0))
                events = {
                    cause: sum(
                        (w for a, b, s, w in observations if b == time and s == cause), Fraction(0)
                    )
                    for cause in incidence
                }
                censor = sum(
                    (w for a, b, s, w in observations if b == time and s in ("open", "timed_out")),
                    Fraction(0),
                )
                if risk:
                    for cause in incidence:
                        incidence[cause] += survival * events[cause] / risk
                    survival *= 1 - sum(events.values()) / risk
                if median is None and survival <= Fraction(1, 2):
                    median = time
                points.append(
                    {
                        "time": time,
                        "risk": rational(risk),
                        "events": {c: rational(v) for c, v in events.items()},
                        "censored": rational(censor),
                        "survival": rational(survival),
                        "incidence": {c: rational(v) for c, v in incidence.items()},
                    }
                )
            groups.append(
                {
                    "endpoint": endpoint,
                    "excluded": sorted(excluded),
                    "points": points,
                    "restricted_mean": rational(area),
                    "median": median,
                }
            )
        output.append({"snapshot": snapshot["snapshot"], "cohorts": groups})
    return output


def program_inputs(d):
    inputs = {}
    stream = []
    for seed in range(20):
        rng = random.Random(4734000 + seed)
        events = []
        checkpoints = []
        for i in range(45 if seed < 18 else 500):
            if i % 13 == 5:
                name = f"c{i}"
                events.append({"op": "checkpoint", "name": name})
                checkpoints.append(name)
            elif i % 17 == 9 and checkpoints:
                events.append({"op": "rollback", "name": rng.choice(checkpoints)})
            elif i % 19 == 8:
                events.append({"op": "idle", "partition": "b"})
            elif i % 19 == 12:
                events.append({"op": "resume", "partition": "b"})
            else:
                at = rng.randrange(-5, 25) if seed < 18 else rng.randrange(-20, 1000)
                line = f"ts={at} svc={rng.choice(['a', 'b'])} level={rng.choice(['ERROR', 'WARN', 'INFO'])} id=r{rng.randrange(8)}"
                if i % 23 == 7:
                    line += " ts=9"
                events.append(
                    {
                        "op": "record",
                        "partition": rng.choice(["a", "b"]),
                        "line": line,
                        "rev": rng.randrange(1, 9),
                        "delete": i % 7 == 0,
                    }
                )
        events.append({"op": "checkpoint", "name": "final"})
        stream.append(
            {
                "partitions": ["a", "b"],
                "lateness": seed % 7,
                "width": 1 + seed % 6,
                "events": events,
            }
        )
    inputs[473] = stream
    matching = []
    for seed, (value, _) in enumerate(d._matching_cases()):
        x = copy.deepcopy(value)
        rng = random.Random(4754000 + seed)
        for a in x["starts"]:
            a["demands"] = {"cpu": 1 + (a["kind"] == "b"), "io": rng.randrange(2)}
        x["resources"] = {"cpu": 2 + seed % 3, "io": 1}
        x["groups"] = [[a["id"] for a in x["starts"][:2]]] if x["starts"] else []
        x["quotas"] = {kind: [seed % 2, 10] for kind in {a["kind"] for a in x["starts"]}}
        matching.append(x)
    for seed in range(3):
        n = 12 + seed % 2
        matching.append(
            {
                "starts": [
                    {
                        "id": f"s{i:02d}",
                        "at": 2 * i,
                        "kind": "a",
                        "channel": "x",
                        "min": 1,
                        "max": 5,
                        "target": 2 + (i + seed) % 3,
                        "demands": {"cpu": 1, "io": i % 2},
                    }
                    for i in range(n)
                ],
                "ends": [{"id": f"e{i:02d}", "at": 2 * i + 3, "accept": ["a"]} for i in range(n)],
                "capacity": {"x": 3},
                "resources": {"cpu": 3, "io": 1},
                "groups": [[f"s{i:02d}", f"s{i + 1:02d}"] for i in range(0, n - 1, 3)],
                "quotas": {"a": [5, 11]},
            }
        )
    inputs[475] = matching
    warehouse = []
    for seed in range(20):
        rng = random.Random(4774000 + seed)
        lots = [
            {
                "id": f"l{i}",
                "sku": "AB"[i % 2],
                "warehouse": "xy"[i % 2],
                "qty": 3 + i % 3,
                "expires": 8 + i * 3,
            }
            for i in range(6 if seed < 18 else 28)
        ]
        tx = []
        names = []
        for i in range(12 if seed < 18 else 60):
            at = i // 2
            events = []
            for j in range(2 + i % 3):
                op = rng.choice(["reserve", "transfer", "ship", "cancel"])
                sku = rng.choice(["A", "B"])
                if op == "reserve":
                    name = f"h{i}_{j}"
                    names.append(name)
                    events.append(
                        {
                            "op": op,
                            "name": name,
                            "sku": sku,
                            "qty": 1 + rng.randrange(5),
                            "warehouses": rng.sample(["x", "y"], 2),
                            "until": at + 1 + rng.randrange(8),
                        }
                    )
                elif op == "transfer":
                    events.append(
                        {
                            "op": op,
                            "sku": sku,
                            "qty": 1 + rng.randrange(4),
                            "source": "x",
                            "destination": "y",
                        }
                    )
                else:
                    events.append({"op": op, "hold": rng.choice(names or ["missing"])})
            tx.append({"id": f"t{i:03d}", "at": at, "events": events})
        rng.shuffle(tx)
        warehouse.append({"lots": lots, "transactions": tx, "horizon": 7 + seed})
    inputs[477] = warehouse
    repair = []
    for seed, (value, _) in enumerate(d._repair_cases()):
        x = copy.deepcopy(value)
        for r in x["resources"]:
            r["available"] = [[0, x["n"]]]
        x["cache_bytes"] = max([r["bytes"] for r in x["resources"]] or [0]) + seed % 9
        if seed % 3 == 0 and x["resources"]:
            x["resources"][-1]["available"] = [[0, 1]]
        repair.append(x)
    repair.insert(
        0,
        {
            "n": 2,
            "cache_bytes": 2,
            "resources": [
                {"id": "a", "cost": 2, "bytes": 1, "available": [[0, 1]]},
                {"id": "b", "cost": 3, "bytes": 1, "available": [[0, 1]]},
            ],
            "pieces": [
                {"id": "p", "a": 0, "b": 1, "cost": 0, "requires": ["a"]},
                {"id": "q", "a": 1, "b": 2, "cost": 0, "requires": ["b"]},
            ],
        },
    )
    repair.insert(
        1,
        {
            "n": 3,
            "cache_bytes": 1,
            "resources": [
                {"id": "a", "cost": 2, "bytes": 1, "available": [[0, 3]]},
                {"id": "b", "cost": 3, "bytes": 1, "available": [[0, 3]]},
            ],
            "pieces": [
                {"id": f"p{i}", "a": i, "b": i + 1, "cost": 0, "requires": ["aba"[i]]}
                for i in range(3)
            ],
        },
    )
    for seed in range(3):
        rng = random.Random(4794400 + seed)
        n = 15
        repair.append(
            {
                "n": n,
                "cache_bytes": 8,
                "resources": [
                    {"id": f"r{i}", "cost": 1 + i, "bytes": 1 + i % 3, "available": [[0, 7 + i]]}
                    for i in range(7)
                ],
                "pieces": [
                    {
                        "id": f"p{i:02d}_{step}",
                        "a": i,
                        "b": i + step,
                        "cost": rng.randrange(4),
                        "requires": rng.sample([f"r{j}" for j in range(7)], 2),
                    }
                    for i in range(n)
                    for step in (1, 2)
                    if i + step <= n
                ],
            }
        )
    inputs[479] = repair
    partitions = []
    for seed in range(16):
        rng = random.Random(4814000 + seed)
        n = 3 + seed % 4
        ids = [chr(97 + i) for i in range(n)]
        layers = []
        for _ in range(2 + seed % 3):
            layers.append(
                {
                    "must": [],
                    "cannot": [
                        [a, b] for a, b in itertools.combinations(ids, 2) if rng.random() < 0.12
                    ],
                    "evidence": [
                        {"a": a, "b": b, "weight": rng.randrange(-5, 9)}
                        for a, b in itertools.combinations(ids, 2)
                    ],
                    "max_size": 2 + seed % 2,
                    "groups": [max(1, n // 3), n],
                }
            )
        partitions.append(
            {
                "ids": ids,
                "layers": layers,
                "change_budget": seed % 8,
                "change_costs": [
                    {"a": a, "b": b, "cost": rng.randrange(4)}
                    for a, b in itertools.combinations(ids, 2)
                ],
            }
        )
    for seed in range(2):
        rng = random.Random(4814900 + seed)
        ids = [f"u{i}" for i in range(8)]
        layers = []
        for _ in range(4):
            layers.append(
                {
                    "must": [],
                    "cannot": [],
                    "evidence": [
                        {"a": a, "b": b, "weight": rng.randrange(-5, 10)}
                        for a, b in itertools.combinations(ids, 2)
                    ],
                    "max_size": 3,
                    "groups": [6, 8],
                }
            )
        partitions.append(
            {
                "ids": ids,
                "layers": layers,
                "change_budget": 4,
                "change_costs": [
                    {"a": a, "b": b, "cost": 1} for a, b in itertools.combinations(ids, 2)
                ],
            }
        )
    inputs[481] = partitions
    mitigation = []
    for seed, (value, _) in enumerate(d._saturation_cases()):
        x = copy.deepcopy(value)
        resources = sorted(x["capacity"])
        x["mitigation"] = {
            "width": max(1, x["horizon"] // (2 + seed % 3)),
            "limit": 1 + seed % 3,
            "budget": 2 + seed % 4,
            "weights": {r: i + 1 for i, r in enumerate(resources)},
            "modes": [{"id": "all", "cost": 2, "relief": dict.fromkeys(resources, 1)}]
            + [{"id": "solo_" + r, "cost": 1, "relief": {r: 2}} for r in resources],
        }
        mitigation.append(x)
    inputs[483] = mitigation
    macros = []
    for x, _ in d._range_cases():
        if x["definitions"]:
            continue
        macros.append({"definitions": [], "expr": x["expr"]})
    macros.extend(
        [
            {
                "definitions": [
                    {"name": "f", "params": ["x"], "expr": "let(x,$x~,ifempty($x,99,$x[0:2]))"}
                ],
                "expr": "@f(1..4)",
            },
            {
                "definitions": [
                    {"name": "f", "params": ["x"], "expr": "$x | 8"},
                    {"name": "g", "params": ["x"], "expr": "let(x,$x~, @f($x))"},
                ],
                "expr": "@g(1..3)",
            },
            {"definitions": [], "expr": "ifempty(1\\1,7,1..3/0)"},
            {"definitions": [], "expr": "ifempty(1,7,1..3/0)"},
            {"definitions": [], "expr": "let(x,1..3,let(x,$x~, $x | 8))"},
            {"definitions": [{"name": "bad", "params": ["x"], "expr": "$free"}], "expr": "7"},
            {"definitions": [{"name": "bad", "params": ["x", "x"], "expr": "$x"}], "expr": "7"},
            {
                "definitions": [
                    {"name": "f", "params": ["x"], "expr": "@g($x)"},
                    {"name": "g", "params": ["x"], "expr": "$x"},
                ],
                "expr": "0",
            },
            {"definitions": [], "expr": "ifempty(1,7,$unknown)"},
            {"definitions": [], "expr": "ifempty(1,1..3/0,7)"},
            {"definitions": [{"name": "f", "params": [], "expr": "1..2"}], "expr": "@f()"},
        ]
    )
    for seed in range(3):
        definitions = [{"name": "f0", "params": ["x"], "expr": "$x"}]
        for i in range(1, 29):
            definitions.append(
                {"name": f"f{i}", "params": ["x"], "expr": f"@f{i - 1}($x) | @f{i - 1}($x)"}
            )
        macros.append({"definitions": definitions, "expr": f"@f28({seed}..{seed + 40})~[0:10]"})
    inputs[484] = macros
    ropes = []
    for seed in range(20):
        rng = random.Random(4854000 + seed)
        ops = []
        versions = [[]]
        for i in range(20):
            base = rng.randrange(len(versions))
            seq = versions[base][:]
            op = rng.choice(["add", "move", "remove", "reverse", "splice", "rotate"])
            event = {"base": base, "op": op}
            if op in ("add", "move", "remove"):
                a, b = rng.randrange(-8, 9), rng.randrange(-8, 9)
                event.update(a=a, b=b)
                lo, hi = sorted((a, b))
                if op == "add":
                    seq += [
                        v
                        for v in range(a, b + (1 if a <= b else -1), 1 if a <= b else -1)
                        if v not in seq
                    ]
                else:
                    seq = [v for v in seq if not lo <= v <= hi] + (
                        [v for v in seq if lo <= v <= hi] if op == "move" else []
                    )
            elif op == "reverse":
                seq.reverse()
            elif op == "splice":
                origin = rng.randrange(len(versions))
                a, b = sorted([rng.randrange(len(versions[origin]) + 1) for _ in range(2)])
                at = rng.randrange(len(seq) + 1)
                delete = rng.randrange(len(seq) - at + 1)
                rev = bool(i % 2)
                event.update(at=at, delete=delete, source=origin, start=a, stop=b, reverse=rev)
                part = versions[origin][a:b]
                seq = seq[:at] + (part[::-1] if rev else part) + seq[at + delete :]
            else:
                a, b = sorted([rng.randrange(len(seq) + 1) for _ in range(2)])
                shift = rng.randrange(-10, 11)
                event.update(start=a, stop=b, by=shift)
                part = seq[a:b]
                k = shift % len(part) if part else 0
                seq = seq[:a] + (part[-k:] + part[:-k] if k else part) + seq[b:]
            ops.append(event)
            versions.append(seq)
        queries = []
        for v in range(len(versions)):
            queries.extend(
                [
                    {"op": "rank", "version": v, "value": -3},
                    {"op": "select", "version": v, "index": 2},
                    {"op": "sum", "version": v, "start": -5, "stop": 10},
                    {"op": "hash", "version": v, "start": 1, "stop": 20, "base": 4, "mod": 12},
                ]
            )
        ropes.append({"ops": ops, "queries": queries})
    ops = [{"base": 0, "op": "add", "a": 1, "b": 10**12}]
    length = 10**12
    for i in range(1, 20):
        ops.append(
            {
                "base": i,
                "op": "splice",
                "at": length,
                "delete": 0,
                "source": i,
                "start": 0,
                "stop": length,
                "reverse": i % 2 == 0,
            }
        )
        length *= 2
    ops.append({"base": 20, "op": "rotate", "start": 7, "stop": length - 5, "by": 10**14 + 3})
    ops.append({"base": 21, "op": "move", "a": 1, "b": 2})
    ops.append({"base": 22, "op": "remove", "a": 10**12 - 2, "b": 10**12})
    queries = [
        {"version": v, "op": "hash", "start": 3, "stop": length, "base": b, "mod": m}
        for v in (1, 19, 20, 21, 22, 23)
        for b, m in [(2, 16), (7, 1000), (911382323, 1000000007)]
    ]
    queries += [
        {"version": v, "op": "sum", "start": 0, "stop": length} for v in (20, 21, 22, 23)
    ] + [
        {"version": 22, "op": "rank", "value": 1},
        {"version": 23, "op": "rank", "value": 10**12},
        {"version": 21, "op": "select", "index": length - 2},
    ]
    ropes.append({"ops": ops, "queries": queries})
    inputs[485] = ropes
    # Lexer, infeasibility, lazy-evaluation, and maximum-depth cases are part of
    # the scored hidden stream, not merely unit tests around the references.
    lines = [line for x, _ in d._logfmt_cases()[:3] for line in x["lines"]]
    inputs[473].append(
        {
            "partitions": ["p"],
            "lateness": 1000000000,
            "width": 3,
            "events": [
                {"op": "record", "partition": "p", "line": line, "rev": 1, "delete": False}
                for line in lines
            ]
            + [{"op": "checkpoint", "name": "quoted-lexer"}],
        }
    )
    inputs[477].append(
        {
            "lots": [{"id": "l", "sku": "a", "warehouse": "x", "qty": 1, "expires": 1}],
            "transactions": [],
            "horizon": 1,
        }
    )
    impossible = {
        "ids": ["a", "b", "c"],
        "layers": [
            {
                "must": [["a", "b"]],
                "cannot": [["a", "c"]],
                "evidence": [],
                "max_size": 2,
                "groups": [2, 3],
            },
            {
                "must": [["a", "c"]],
                "cannot": [["a", "b"]],
                "evidence": [],
                "max_size": 2,
                "groups": [2, 3],
            },
        ],
        "change_budget": 0,
        "change_costs": [],
    }
    inputs[481].append(impossible)
    feasible = copy.deepcopy(impossible)
    feasible["change_budget"] = 2
    inputs[481].append(feasible)
    conflict = copy.deepcopy(feasible)
    conflict["layers"][0]["cannot"].append(["a", "b"])
    inputs[481].append(conflict)
    inputs[484].extend(
        [
            {"definitions": [{"name": "bad", "params": [], "expr": "1..3/0"}], "expr": "7"},
            {"definitions": [{"name": "f", "params": ["x"], "expr": "7"}], "expr": "@f(1..3/0)"},
            {
                "definitions": [{"name": "f", "params": ["x"], "expr": "$x"}],
                "expr": "ifempty(1,@f(),7)",
            },
        ]
    )
    stress = [{"base": 0, "op": "add", "a": 1, "b": 10**6}]
    length = 10**6
    for i in range(1, 30):
        stress.append(
            {
                "base": i,
                "op": "splice",
                "at": length,
                "delete": 0,
                "source": i,
                "start": 0,
                "stop": length,
                "reverse": i % 2 == 0,
            }
        )
        length *= 2
    for i in range(30, 100):
        if i % 2:
            stress.append(
                {"base": i, "op": "rotate", "start": i, "stop": length - i, "by": i * 1001}
            )
        else:
            stress.append(
                {
                    "base": i,
                    "op": "splice",
                    "at": i,
                    "delete": length // 2,
                    "source": max(30, i - 3),
                    "start": i,
                    "stop": i + length // 2,
                    "reverse": True,
                }
            )
    inputs[485].append(
        {
            "ops": stress,
            "queries": [
                {"version": 100, "op": "hash", "start": 0, "stop": length, "base": 4, "mod": 12},
                {"version": 100, "op": "rank", "value": 10**6},
                {"version": 100, "op": "sum", "start": 13, "stop": length - 17},
            ],
        }
    )
    return inputs


PROGRAM_PROMPTS = {
    473: r"""Реализуй partitioned event-time stream с коррекциями, rollback и retractable окнами.
Вход {partitions,lateness,width,events}; partitions уникальные ASCII id, width>0,lateness>=0.
record={op:"record",partition,line,rev,delete}: line использует следующий строгий logfmt.
Пары key=value разделены одним или более ASCII spaces; внешние spaces разрешены.
key=[a-z_]+ без повторов. Unquoted value непустое, без space, quotes, backslash и control<33.
Quoted value в двойных кавычках; обычные символы кроме quotes и control<32; escape только
\", \\, \n, \t. После quotes только space/end. Ключи ровно ts,svc,level,id и необязательный msg.
ts=-?(0|[1-9][0-9]*); svc=[a-z][a-z0-9_-]*; id=[A-Za-z0-9_.-]+; level DEBUG/INFO/WARN/ERROR.
Значения проверяются ПОСЛЕ декодирования. msg может быть пустым, декои не поля.
Все partitions изначально active, high=null; глобальный watermark=null. Невалидный record ->
invalid; иначе partition idle -> idle; оба случая ничего не меняют. Иначе сравнение ts использует
watermark ДО события, но СНАЧАЛА обнови high этой partition=max(high,ts), включая late/stale.
Если у всех active high известен и active непуст, watermark=max(старый watermark,min(high)-lateness),
null заменяется вычисленным числом. Если ts<старый watermark -> late. Иначе ключ=(svc,id),
rev<=текущей сохранённой ревизии -> stale; иначе замени ключ целиком (accepted либо deleted).
Tombstone хранит rev, но не строку. Delete также имеет валидную line и проходит watermark.
idle/resume={op,partition}: идемпотентно убери/добавь active, high сохраняется; после этого
обнови watermark тем же правилом. Resume никогда не уменьшает watermark.
checkpoint={op:"checkpoint",name}: сохрани полное состояние движка. Также опубликуй оконную
дельту: активные не-tombstone строки группируются по (svc, floor(ts/width)*width), и только
окна start+width<=watermark закрыты. У каждого окна count всех строк и errors числа ERROR.
Delta = новые totals минус totals ПОСЛЕДНЕГО опубликованного checkpoint, включая отрицательные
retractions и исчезнувшие окна. Выводи только изменённые окна, по (svc,start).
rollback={op:"rollback",name}: восстанови active/high/watermark/rows на checkpoint; сохранённые
checkpoints, audit и ПОСЛЕДНИЕ ОПУБЛИКОВАННЫЕ totals НЕ откатываются. Имена checkpoints уникальны;
rollback ссылается на уже существующий. Иные события публикации не делают.
Выход {audit:[{event,status}],checkpoints:[{name,watermark,delta:[{svc,start,count,errors}]}],
rows:[{svc,id,rev,ts}],watermark}. event с1; audit/checkpoints в порядке событий; status у
нерекордов равен op; rows по (svc,id), все победители включая tombstone с ts=null.
До600 events,8 partitions, |ts|<=10^12, rev>0. Синтаксически корректен весь JSON envelope.""",
    475: """Дополни глобальное session matching зависимыми пакетами и несколькими ресурсами.
Исходная часть контракта ниже остаётся в силе, кроме размеров и нового infeasible выхода.
""",
    477: """Вместо независимого replay выполни атомарные складские транзакции. Вход {lots,transactions,horizon}.
Правила партий, FEFO, expiry, reserve/transfer/cancel/ship и формат ledger описаны ниже.
transactions={id,at,events:[...]}, по (at,id), только at<=horizon; events непустой массив операций
в ПОРЯДКЕ МАССИВА, без id/at. Все операции транзакции имеют её at. Reserve имеет дополнительный
name — логическое имя hold; cancel/ship ссылаются на него. Имена reserve глобально уникальны
включая откатившиеся; references могут указывать на будущие/откатившиеся/неизвестные имена.
Перед транзакцией выполни expiry независимо от её исхода. Применяй операции по порядку.
При первом insufficient/inactive_hold вся транзакция откатывается, включая созданные holds,
отгрузки, переводы и отмены; expiry ПЕРЕД транзакцией не откатывается. Остальные операции
после отказа не выполняются. Повторное освобождение отменённого hold является ошибкой.
Выход {ledger,transactions:[{id,status,failure,reason}]}. status committed/aborted;
failure — индекс первой неуспешной операции с0, reason её status; при committed оба null.
ledger имеет inventory,holds,shipped,expired_units по исходным правилам, БЕЗ audit; holds.id=name.
Только committed holds существуют. До30 lots/200 units,60 transactions,4 operations каждая.
""",
    479: """Восстанови [0,n) цепочкой pieces, но доноры доступны временно и не помещаются вместе в cache.
Вход {n,resources,pieces,cache_bytes}. Resource={id,cost,bytes,available:[[a,b],...]}, bytes>0,
cost>=0, a<b; доступность означает возможность ЗАГРУЗКИ в координате a<=position<b.
Piece={id,a,b,cost,requires}, 0<=a<b<=n, requires уникальные известные resource id.
Первый piece начинается0, далее без дыр/перекрытий, последний b=n. Cache изначально пуст.
ПЕРЕД каждым piece можно бесплатно удалить любые resident resources и загрузить любые
ресурсы, включая prefetch ещё не требуемых, если они available в piece.a. Итоговый cache
содержит requires и sum(bytes)<=cache_bytes. Загрузка каждого отсутствовавшего до этого
piece ресурса стоит его cost и bytes; повторная загрузка после eviction оплачивается снова.
Resident ресурс можно использовать после окончания available. Evict+reload того же ресурса
в одном переходе не нужен и не является отдельным разрешённым действием. Действий между
pieces нет. Выбирается ровно cache ПОСЛЕ загрузок для каждого piece, даже последнего.
Оптимум: минимальные total cost (pieces+loads), затем total downloaded bytes, затем число pieces,
затем lex массива piece id в порядке восстановления, затем lex массива cache lists; каждый
cache list отсортирован ASCII id. Выход {plan:[id],cost,bytes,cache:[[id]]} или {plan:null}.
Размеры: n<=10^12,pieces<=35,resources<=9. Целые суммы точные. Порядок входов не важен.""",
    481: """Восстанови СОГЛАСОВАННУЮ историю partition контактов. Вход {ids,layers,change_budget,change_costs}.
В каждом layer={must,cannot,evidence,max_size,groups:[min,max]} разбей ВСЕ ids на группы:
must пары вместе, cannot врозь, размер<=max_size, число групп в inclusive интервале groups.
Evidence={a,b,weight} суммируется для ВСЕХ записей с концами в одной группе, включая обратные
и повторные и отрицательные. Нет evidence не является запретом. Каждая неупорядоченная пара
ids считается changed между соседними layers, если изменилась истинность «в одной группе».
Суммарно changed по всей истории<=change_budget. change_costs={a,b,cost}, уникальные unordered
pairs, cost>=0; отсутствующая пара cost0. score=сумма evidence по layers минус costs ВСЕХ
изменений (одна пара может платить несколько раз). Цель: maximum score, minimum total changes,
minimum total number groups по layers, затем lex history. Канонизация: каждая группа по id,
в каждом layer группы lex, history в порядке layers. Выход {history,score,changes,groups};
если история невозможна — ровно {history:null}. 1..8 ids,2..4 layers,change_budget<=12,
не более500 допустимых partitions в каждом layer (с учётом всех ограничений).""",
    483: """Сначала вычисли исходный overload report по правилам ниже. Дополнительно вход содержит
mitigation={width,limit,budget,weights,modes}; width в1..horizon,0<=limit<=4,0<=budget<=12.
Каждый mode={id,cost,relief:{resource:nonnegative int}}, cost>=1; weights положительные целые
для всех resources. Выбери до limit попарно НЕПЕРЕСЕКАЮЩИХСЯ полуоткрытых окон [t,t+width),
целый0<=t<=horizon-width, каждому один mode. Touching разрешены. Общая cost<=budget.
Baseline excess(resource,time)=max(0,active_requests-capacity), как в исходном отчёте.
Во время окна residual excess=max(0,baseline excess-mode.relief[resource]); вне окон baseline.
Нет relief для resource ->0. Цель: min точный интеграл суммы weights*residual за[0,horizon),
затем minimum cost, затем minimum число окон, затем lex массив [start,mode.id], отсортированный
по start. Добавь в исходный output ключ mitigation:{residual,cost,windows:[[start,end,mode_id]]}.
Нельзя перечислять все моменты: horizon до10^12. Modes<=5,requests<=40,intervals<=8 на request.
""",
    484: r"""Интерпретируй язык упорядоченных множеств с параметризованными макросами и lexical scopes.
Вход {definitions:[{name,params:[name],expr}],expr}. Грамматика:
expr := intersection (("|" | "\" | "^") intersection)*
intersection := postfix ("&" postfix)*
postfix := atom ("~" | "[" int ":" int (":" int)? "]")*
atom := int (".." int ("/" int)?)? | "$" name | "(" expr ")"
      | "@" name "(" (expr ("," expr)*)? ")"
      | "let(" name "," expr "," expr ")" | "ifempty(" expr "," expr "," expr ")"
int=-?(0|[1-9][0-9]*); name=[A-Za-z_][A-Za-z_0-9]*. ASCII space/tab/CR/LF между лексемами.
let и ifempty — отдельные ключевые лексемы; пробел перед '(' допустим. Диапазон inclusive,
направление к b, положительный step по умолчанию1. | — A плюс отсутствующие элементы B;
& и \ фильтруют A по наличию/отсутствию в B; ^ — A вне B, затем B вне A. Postfix выше &,
& выше |,\,^; бинарные одного уровня слева направо. ~ reverse; slice точный Python[a:b:s],
a,b обязательны, s!=0. Каждое промежуточное значение уникально.
Definitions по порядку: уникальные имена, уникальные params; функция может вызывать только
РАНЕЕ объявленные функции, не себя. $name только параметр или lexically enclosing let.
let(n,value,body) вычисляет value во внешнем scope, затем body в scope с новым n; shadowing
разрешён. Function body видит только собственные params и let; scope вызывающего не наследует.
@f(args) вычисляет args слева направо, число аргументов строго равно params. ifempty(test,a,b)
вычисляет test, затем ТОЛЬКО a при пустом результате, иначе ТОЛЬКО b. Невыбранная ветвь
НЕ вычисляет шаги диапазонов/срезов. Но весь AST, все references, scopes и arities валидируются
во ВСЕХ definitions/ветвях до вычисления, даже неиспользуемых. Поэтому неизвестная ссылка в
невыполненной ветке invalid; step0 в ней допустим. Любая синтаксическая/статическая/вычисленная
ошибка -> ровно {error:"invalid"}; иначе {values:[...]}. Не выполняй вход как код.
До30definitions,2000символов выражение,глубина30, |числа|<=1000,списки<=2001.
Макросы могут образовать экспоненциальное дерево повторных вызовов; число РАЗЛИЧНЫХ пар
(function,ordered argument tuples) <=20000. Требуется повторное использование вычислений.""",
    485: """Редактируй persistent последовательности огромной длины, версия0 пустая. Каждый op имеет
base — индекс УЖЕ созданной версии; результат операции i создаёт version i+1, старые неизменны.
add{a,b}: перечисли целые a..b inclusive в направлении b, добавь только значения, которых вообще
нет в base, по одному в конец. remove{a,b}: удали ВСЕ вхождения значений в[min(a,b),max(a,b)].
move{a,b}: все такие вхождения перенеси в конец, порядок внутри обеих частей сохраняется.
reverse: разверни всё. splice{at,delete,source,start,stop,reverse}: из версии base удали delete
элементов начиная с at, вставь на at копию source[start:stop], предварительно развернув её
если reverse=true. Source — любая уже созданная версия, включая base; операции не «живые».
Копирование СОХРАНЯЕТ повторы, поэтому после splice это последовательность, не множество.
rotate{start,stop,by}: циклически сдвинь вправо base[start:stop] на by (negative влево),
нормализуй modulo длина, пустой блок не меняет ничего. Все edit indices валидны и неотрицательны;
source и base от0 до i, 0<=start<=stop<=соответствующая длина, at+delete<=base length.
Queries={version,op,...} относятся к любой версии0..len(ops): rank{value} первый индекс или null;
select{index} значение или null при index<0/index>=length; sum{start,stop} сумма с max(0,start)
до min(length,stop) exclusive, пусто0. hash{start,stop,base,mod} тот же срез, точная сумма
value[j]*base**j modulo mod, j начинается0 ВНУТРИ СРЕЗА; 0<=base<=10^9,2<=mod<=2^31,
mod может быть составным, деление по модулю не обещано. Отрицательные query indices не с конца.
Output {sizes:[длины version1,...],answers:[в порядке queries]}; массивы и все суммы точные integers.
До100ops/100queries, значенияa,b,value до±10^12, длина любой версии<=2^60. Копирование может
экспоненциально увеличивать число RUNS с одинаковыми поддеревьями: materialize values или
все повторённые runs недопустимо. Нужна структура с разделяемыми неизменными частями.""",
}


PROGRAM_CASES = {}
PROGRAM_GOLDS = {}
REFERENCE_RUNTIME = {}
ARTIFACT_POLICIES = {}


def build_data_v4(tasks, legacy):
    """Build current tasks from frozen pre-v4 domain fixtures, preserving IDs."""
    import time
    from types import SimpleNamespace

    d = SimpleNamespace(**legacy)
    old = {int(t.id.split("_")[1]): t for t in tasks}
    current = {}
    inputs = program_inputs(d)
    sources = {
        473: legacy_source(old[473]) + "\n" + source(streaming_solve),
        475: source(matching_solve),
        477: legacy_source(old[477]) + "\n" + source(transaction_solve),
        479: source(cache_repair_solve),
        481: source(partition_history_solve, partition_options),
        483: legacy_source(old[483]) + "\n" + source(mitigation_solve),
        484: source(macro_solve, MacroParser),
        485: source(rope_solve, Rope),
    }
    prompts = dict(PROGRAM_PROMPTS)
    prompts[475] = original_prompt(old[475]).replace(
        "Размеры: до 8 starts и 8 ends, at от -100 до100, остальные числовые поля<=200.", ""
    )
    prompts[475] = prompts[475].replace(
        "Выход ровно {pairs, cost, unmatched_starts, unmatched_ends};",
        "При существующем допустимом сопоставлении выход {pairs, cost, unmatched_starts, unmatched_ends};",
    )
    prompts[
        475
    ] += """\nДополнительно вход содержит groups,quotas,resources; каждый start содержит demands.
Groups — массив массивов start id: из каждой группы выбираются ЛИБО ВСЕ starts, ЛИБО НИ ОДНОГО;
группы могут пересекаться. Quotas={kind:[min,max]} задаёт число выбранных starts данного kind
inclusive; неуказанные kind без квоты. Resources={name:capacity}, demands={name:units>=0}.
Для каждого resource в каждый момент сумма demands выбранных интервалов[start.at,end.at)
не выше capacity. Это ограничение ДОПОЛНЯЕТ прежнюю capacity по channel. У нулевых интервалов
расход0. Требования групп/квот проверяются даже для нулевых интервалов. Если ни одного
допустимого сопоставления нет, output ровно {pairs:null}. Оптимизация та же: count,cost,lex.
До14starts/14ends,4resources, целые at от-100до100, min/max/target<=200, demands<=4;
IDs внутри groups известные без повторов, все channels/resources объявлены."""
    warehouse_rules = original_prompt(old[477])
    warehouse_rules = warehouse_rules[
        warehouse_rules.index("Перед каждым событием") : warehouse_rules.index("Выход {inventory")
    ]
    warehouse_rules = warehouse_rules.replace(
        "Перед каждым событием и в horizon", "Перед транзакцией и в horizon"
    ).replace("hold id=event.id", "hold id=name")
    warehouse_rules = warehouse_rules.replace(
        "status=insufficient и hold state=rejected, allocations=[]",
        "status=insufficient (транзакция откатывается, отвергнутый hold НЕ сохраняется)",
    )
    warehouse_rules = warehouse_rules.replace("{id,at,op:", "{op:").replace(
        "reserve:", "reserve(name, кроме указанных полей):"
    )
    prompts[477] += (
        "\nПравила ledger:\n"
        + warehouse_rules
        + """\nLots={id,sku,warehouse,qty,expires}, qty>0,expires>0, clocks неотрицательные.
Начальные units свободны в0. Inventory по(lot,warehouse),только free+held>0:
{lot,warehouse,free,held}. Holds по name:{id,state,until,allocations}. Исторические allocations
в committed holds сохраняются при отмене/expiry/shipping. shipped/expired_units содержат
все SKU lots, включая0. В горизонте также выполняется expiry."""
    )
    # Define the baseline result inside a named field, never publish two competing root schemas.
    base483 = original_prompt(old[483])
    base483 = base483.replace("Выход ", "Поля исходного report: ").replace(
        "До80 requests и400 интервалов.", ""
    )
    prompts[483] += "\n" + base483
    for number, code in sources.items():
        namespace = {}
        exec(code.split(CLI)[0], namespace)
        started = time.perf_counter()
        cases = [(x, namespace["solve"](copy.deepcopy(x))) for x in inputs[number]]
        REFERENCE_RUNTIME[number] = time.perf_counter() - started
        PROGRAM_CASES[number] = cases
        PROGRAM_GOLDS[number] = code
        current[number] = upgraded_program(old[number], prompts[number], cases, code)

    def baseline_prompt(number, addition):
        prompt = (
            original_prompt(old[number])
            .replace("answer.json", "baseline")
            .replace("Не добавляй другие поля.", "")
        )
        if number == 478:
            prompt = prompt.replace("target лежит в manifest.json", "target лежит в restore.json")
        return addition + "\n\nПравила вычисления поля baseline:\n" + prompt

    # 472: robust remediation and the full force/ban sensitivity frontier.
    baseline = json.loads(old[472].gold_files["answer.json"])
    keys = sorted({(r["service"], r["fingerprint"]) for s in baseline for r in s["incidents"]})
    rng = random.Random(4724000)
    actions = []
    for i in range(18):
        actions.append(
            {
                "id": f"a{i:02d}",
                "kind": "repair" if i % 2 == 0 else "guard",
                "cost": 1 + i % 3,
                "load": {"engineers": 1, "risk": i % 3},
                "targets": [list(k) for k in keys if rng.random() < 0.45],
                "requires": [f"a{i - 2:02d}"] if i >= 2 and i % 7 == 0 else [],
            }
        )
    policy = {
        "linger": d._LOG_CONFIG["linger"],
        "budget": 12,
        "capacity": {"engineers": 7, "risk": 7},
        "actions": actions,
        "exclusive": [["a00", "a03"], ["a06", "a11"], ["a10", "a15"]],
        "failures": [
            {"id": "none", "disabled": []},
            {"id": "rollout", "disabled": ["a00", "a05", "a12"]},
            {"id": "network", "disabled": ["a02", "a09", "a16"]},
        ],
    }
    ARTIFACT_POLICIES[472] = policy
    files = {**old[472].setup_files, "remediation.json": dumps(policy)}
    answer = {"baseline": baseline, "remediation": incident_remediation(baseline, policy)}
    prompt = """Построй точный corrected-log baseline и робастный план устранения причин.
Итоговый answer.json РОВНО {baseline,remediation}. remediation.json задаёт actions,capacity,
budget,exclusive,failures,linger. Target=(service,fingerprint). Его burden в каждом snapshot —
SUM((incident.end-incident.start+linger)*len(incident.ids)) по его baseline incidents.
Выбери подмножество actions: sum(cost)<=budget; sum(load[r])<=capacity[r]; если action выбран,
все requires также выбраны; из каждой exclusive-группы максимум один. Стоимость/нагрузки
платятся даже за action, отключившийся в failure scenario. Сценарий disabled выключает эффект.
Target устранён в сценарии только если хотя бы один НЕОТКЛЮЧЁННЫЙ выбранный repair И хотя бы
один guard имеют его в targets. Иначе ВСЯ burden остаётся. Risk каждой пары scenario/snapshot —
сумма оставшихся burden. Цель по порядку: min maximum risk, min sum всех risks, min cost,
min число actions, lex sorted action ids. Это одно общее решение для всех снимков/сценариев.
Remediation={worst,total,cost,actions,residuals:[{scenario,snapshots:[risks]}],sensitivity:[...]}.
Residuals в порядке failures, snapshots в порядке config.snapshots. Sensitivity по action id:
{action,required,forbidden}; для каждой заново оптимизируй ТОТ ЖЕ полный objective при
обязательном включении/исключении action. Result={worst,total,cost,actions}, либо null если
невозможно. Это условные глобальные оптимумы, не изменение уже выбранного плана.
Все ограничения явны; не допускаются локальные greedy или независимый выбор по snapshot."""
    current[472] = upgraded_artifact(old[472], baseline_prompt(472, prompt), files, answer)
    # 474: forced/forbidden hypotheses require re-solving the cardinality tier too.
    baseline = json.loads(old[474].gold_files["answer.json"])
    restrictions = [{"id": "unrestricted", "require": [], "forbid": [], "clock_pin": {}}]
    for snapshot in baseline["snapshots"]:
        for pair in snapshot["pairs"][::2]:
            item = {"snapshot": snapshot["name"], "start": pair["start"], "finish": pair["finish"]}
            restrictions.append(
                {
                    "id": f"ban-{snapshot['name']}-{pair['start']}",
                    "require": [],
                    "forbid": [item],
                    "clock_pin": {},
                }
            )
    for host, choices in sorted(d._SESSION_POLICY["clocks"].items()):
        for choice in choices:
            restrictions.append(
                {
                    "id": f"clock-{host}-{choice['offset']}",
                    "require": [],
                    "forbid": [],
                    "clock_pin": {host: choice["offset"]},
                }
            )
    first = baseline["snapshots"][0]
    pairs = first["pairs"]
    restrictions += [
        {
            "id": "impossible-shared-finish",
            "require": [
                {
                    "snapshot": first["name"],
                    "start": pairs[0]["start"],
                    "finish": pairs[0]["finish"],
                },
                {
                    "snapshot": first["name"],
                    "start": pairs[1]["start"],
                    "finish": pairs[0]["finish"],
                },
            ],
            "forbid": [],
            "clock_pin": {},
        },
        {
            "id": "coupled-force",
            "require": [
                {
                    "snapshot": s["name"],
                    "start": s["pairs"][-1]["start"],
                    "finish": s["pairs"][-1]["finish"],
                }
                for s in baseline["snapshots"]
            ],
            "forbid": [
                {
                    "snapshot": first["name"],
                    "start": pairs[0]["start"],
                    "finish": pairs[0]["finish"],
                }
            ],
            "clock_pin": {},
        },
    ]
    ARTIFACT_POLICIES[474] = restrictions
    answer = {
        "baseline": baseline,
        "counterfactuals": session_frontier(
            d,
            d._SESSION_ROWS + d._SESSION_CORRECTIONS,
            d._SESSION_POLICY,
            d._SESSION_QUERIES,
            restrictions,
        ),
    }
    prompt = """Вычисли joint clock/session baseline и полный counterfactual frontier.
Итоговый answer.json РОВНО {baseline,counterfactuals}. restrictions.json — массив независимых
условий {id,require,forbid,clock_pin}; require/forbid записи {snapshot,start,finish} означают
обязательную/запрещённую пару именно в указанном snapshot; clock_pin фиксирует offset host
на ВСЮ историю. Каждое условие решается отдельно, поверх исходных правил, не поверх baseline.
Нужно повторно оптимизировать ВСЕ уровни исходного objective, включая максимальное число пар:
запрет может заставить снизить кардинальность в одной или нескольких эпохах. Не оставляй только
исходные максимальные matchings; часы могут поменяться. Обязательная пара с отсутствующим
или недопустимым endpoint делает данную clock hypothesis невозможной, а не игнорируется.
Counterfactuals в порядке restrictions: {id,result}; result=null если невозможно, иначе
{pairs,cost,changes,clock_offsets,history}. pairs/cost/changes как в baseline.objective;
history — массив snapshots, каждый — отсортированный по start массив [start,finish].
Tie-breaks те же, что у исходного совместного оптимума. Аудиты для условных решений не нужны."""
    current[474] = upgraded_artifact(
        old[474],
        baseline_prompt(474, prompt),
        {**old[474].setup_files, "restrictions.json": dumps(restrictions)},
        answer,
    )
    # 476: indivisible, warehouse-local orders with substitutes and activation charges.
    baseline = json.loads(old[476].gold_files["answer.json"])
    rng = random.Random(4764000)
    order_skus = ["K1", "K2", "K3", "K4", "A", "B", "C", "D", "E", "K1", "K4", "K2"]
    policy = {
        "activation": {"east": 650, "west": 800, "quarantine": 0},
        "capacity": {"east": 12, "west": 17, "quarantine": 0},
        "orders": [
            {
                "id": f"o{i:02d}",
                "sku": sku,
                "qty": 1 + i % 2,
                "priority": 2 + rng.randrange(9),
                "warehouses": ["east", "west"] if i % 4 else ["west"],
                "requires": [f"o{i - 3:02d}"] if i in (5, 9) else [],
            }
            for i, sku in enumerate(order_skus)
        ],
        "exclusive": [["o00", "o02"], ["o03", "o10"]],
        "alternatives": {
            "K1": [
                {"id": "original", "requirements": {"A": 1, "C": 2}, "fee": 0},
                {"id": "spare", "requirements": {"B": 1, "E": 1}, "fee": 200},
            ],
            "K2": [
                {"id": "original", "requirements": {"A": 1, "C": 2, "B": 1}, "fee": 0},
                {"id": "dock", "requirements": {"D": 1, "E": 2}, "fee": 150},
            ],
        },
    }
    ARTIFACT_POLICIES[476] = policy
    answer = {
        "baseline": baseline,
        "fulfillment": [
            {"snapshot": s["snapshot"], **warehouse_orders(s, policy)} for s in baseline
        ],
    }
    prompt = """Сначала восстанови bitemporal catalog baseline, затем выполни глобальную комплектацию.
Итоговый answer.json РОВНО {baseline,fulfillment}. fulfillment.json описывает новые atomic
orders независимо от старого orders.json (старый нужен только baseline). Для КАЖДОГО snapshot
оптимизируй все новые orders вместе. Заказ отклоняется целиком или целиком собирается в ОДНОМ
разрешённом warehouse по ОДНОМУ варианту requirements из alternatives[sku]. Если alternatives
нет, единственный вариант id="original",requirements=baseline.catalog.requirements,fee=0.
SKU отсутствующий в catalog нельзя продать; replacements не обходят эту проверку.
Потребление leaf=order.qty*requirements[leaf], только bins.available своего warehouse,
переводов/смешивания складов нет. Sum всех потреблённых leaf units в warehouse<=capacity[wh].
При первом заказе со склада заплати activation[wh] один раз; variant.fee платится за каждую
единицу order.qty. Выбранный order требует все order.requires; exclusive-группа максимум1.
Цель: max SUM(order.priority) (не умножать на qty), затем max net_cents (catalog price*qty минус
fees/activation), затем minimum число использованных warehouses, затем lex sorted assignments
[order.id,warehouse,variant.id]. Пустой план допустим. Fulfillment по snapshots:
{snapshot,priority,net_cents,warehouses,assignments,remaining}; remaining ВСЕ исходные bins,
по(warehouse,sku), {warehouse,sku,qty} с оставшейся available включая0. Между snapshots
stock не переносится: это независимые restatements одного плана спроса."""
    current[476] = upgraded_artifact(
        old[476],
        baseline_prompt(476, prompt),
        {**old[476].setup_files, "fulfillment.json": dumps(policy)},
        answer,
    )
    # 478: four authenticated replicas compete for shared donor capacities.
    base_files = {
        k.removeprefix("segments/"): v
        for k, v in old[478].setup_files.items()
        if k.startswith("segments/")
    }
    shards = []
    files = {}
    rng = random.Random(4784000)
    metadata = {}
    for i in range(4):
        shard_id = f"s{i}"
        audit = d.segment_report({"segments/" + k: v for k, v in base_files.items()}, 24)
        shards.append({"id": shard_id, "files": base_files, "audit": audit})
        for name, text in base_files.items():
            files[f"replicas/{shard_id}/{name}"] = text
            metadata[shard_id + "/" + name] = {"donor": f"d{rng.randrange(7)}"}
    policy = {
        "target": 24,
        "donors": {f"d{i}": {"capacity": 2 + i % 2, "cost": 3 + i} for i in range(7)},
        "budget": 25,
        "segments": metadata,
        "checks": [
            {"left": 0, "right": 1, "seq": 5},
            {"left": 1, "right": 2, "seq": 13},
            {"left": 2, "right": 3, "seq": 20},
        ],
    }
    ARTIFACT_POLICIES[478] = (shards, policy)
    files["restore.json"] = dumps(policy)
    answer = {
        "baseline": [{"shard": s["id"], **s["audit"]} for s in shards],
        "restore": restore_shards(shards, policy),
    }
    prompt = """Восстанови ЧЕТЫРЕ аутентифицированные реплики совместно, с общими донорами.
Итоговый answer.json РОВНО {baseline,restore}. Файлы лежат replicas/s0..s3/; внутри каждой
папки применяются нижеописанные одиночные правила валидации, дубликатов, reachable chains
с target=restore.json.target. Baseline — массив s0..s3, элементы {shard,...поля одиночного отчёта}.
Для restore выбирается по одной допустимой цепочке в каждой реплике, включая пустую и любой
достижимый prefix. Дубликат используется ТОЛЬКО через lex-min representative. Каждый выбранный
файл расходует1 capacity своего donor по restore.segments[shard+"/"+basename]. Загрузка donor
стоит donor.cost ОДИН РАЗ для всех файлов всех реплик; total cost<=budget. Donor capacity общая.
Каждый check={left,right,seq} ссылается на индексы s0..s3. Если ОБЕ цепочки содержат seq,
их record.value должны совпадать буквально; если хотя бы одна не дошла до seq, check не мешает.
Оптимум: max минимум covered среди4 реплик; max SUM covered; max SUM generation; min donor cost;
min общее число файлов; lex массив4 списков selected basenames в порядке цепочек/реплик.
Restore={minimum_covered,total_covered,generation_sum,cost,selected:[{shard,files,covered}]},
selected в порядке s0..s3. Независимо лучшие цепочки baseline могут нарушать общие ограничения.
В правилах ниже paths segments/ обозначают содержимое КАЖДОЙ replicas/sN/, а одиночный output
описывает один элемент baseline (плюс shard). Других исходных файлов нет."""
    current[478] = upgraded_artifact(old[478], baseline_prompt(478, prompt), files, answer)
    # 480: privacy-constrained temporal partition evidence is derived from the actual bitemporal groups.
    baseline = json.loads(old[480].gold_files["answer.json"])
    ids = ["u01", "u02", "u03", "u05", "u06", "u07", "u08"]
    layers = []
    for i, snapshot in enumerate(baseline):
        owner = {v: j for j, g in enumerate(snapshot["groups"]) for v in g["source_ids"]}
        layers.append(
            {
                "must": [],
                "cannot": [["u01", "u03"], ["u05", "u06"]] + ([["u02", "u07"]] if i == 1 else []),
                "evidence": [
                    {
                        "a": a,
                        "b": b,
                        "weight": 8 if a in owner and b in owner and owner[a] == owner[b] else -3,
                    }
                    for a, b in itertools.combinations(ids, 2)
                ],
                "max_size": 3,
                "groups": [4, 7],
            }
        )
    policy = {
        "ids": ids,
        "constraints": [
            {
                "must": layer["must"],
                "cannot": layer["cannot"],
                "max_size": layer["max_size"],
                "groups": layer["groups"],
            }
            for layer in layers
        ],
        "same_weight": 8,
        "different_weight": -3,
        "change_budget": 6,
        "change_costs": [
            {"a": a, "b": b, "cost": (j % 3) + 1}
            for j, (a, b) in enumerate(itertools.combinations(ids, 2))
        ],
    }
    partition_input = {
        "ids": ids,
        "layers": layers,
        "change_budget": policy["change_budget"],
        "change_costs": policy["change_costs"],
    }
    ARTIFACT_POLICIES[480] = (policy, partition_input)
    answer = {"baseline": baseline, "privacy_history": partition_history_solve(partition_input)}
    prompt = """Восстанови исходные contact snapshots и согласованную историю privacy-safe identities.
Итоговый answer.json РОВНО {baseline,privacy_history}. privacy.json задаёт ids (подмножество
контактов), constraints по snapshots, same_weight,different_weight,change_budget,change_costs.
В каждом snapshot partition ВСЕХ policy.ids обязана выполнить must/cannot,size<=max_size,
min<=число groups<=max. Для каждой unordered пары policy.ids ровно одно evidence:
same_weight если оба id присутствуют в ОДНОЙ baseline group, иначе different_weight.
score=sum evidence для пар внутри выбранных групп во всех snapshots минус change costs.
Changed = пара изменила истинность «в одной выбранной группе» между соседними snapshots.
Каждое изменение оплачивается отдельно, общий счётчик<=change_budget; отсутствующий cost0.
Оптимум: maximum score; minimum число changed; minimum SUM числа groups; lex history.
Каждая группа sorted id, группы каждого snapshot lex, history по snapshots. Privacy_history
ровно {history,score,changes,groups}; если невозможно — {history:null}. Этот второй результат
не меняет baseline fields/provenance и включает policy.ids даже при отсутствии контакта
в отдельном baseline (его evidence со всеми different_weight). Требуется единая оптимизация
всей истории, нельзя чинить снимки независимо или минимизировать только количество разрезов."""
    current[480] = upgraded_artifact(
        old[480],
        baseline_prompt(480, prompt),
        {**old[480].setup_files, "privacy.json": dumps(policy)},
        answer,
    )
    # 482: exact competing-risks estimates with censoring, delayed entry and rational weights.
    baseline = json.loads(old[482].gold_files["answer.json"])
    ids = sorted({r["request"] for s in baseline for r in s["requests"]})
    policy = {
        "tau": 35,
        "subjects": {
            key: {"entry": i % 7, "weight": [1 + i % 4, 1 + i % 3]} for i, key in enumerate(ids)
        },
    }
    ARTIFACT_POLICIES[482] = policy
    answer = {"baseline": baseline, "survival": survival_report(baseline, policy)}
    prompt = """Восстанови corrected retries baseline и exact competing-risk survival cohorts.
Итоговый answer.json РОВНО {baseline,survival}. survival.json содержит tau и subjects:
request->{entry,weight:[numerator,denominator]}, weight>0. Каждая строка baseline.requests
даёт observation: delayed entry=entry, exit=latency,status из baseline. Если entry>exit,
исключи observation целиком. Complete/failed/cancelled — ТРИ competing event causes;
open/timed_out — right censoring, НЕ event. Exit/entry в шкале elapsed latency, не wall clock.
Для каждого snapshot и cohort ('*' для всех, затем endpoint lex) в моменты t из distinct exit<=tau
плюс tau по возрастанию посчитай risk=sum weight для entry<=t<=exit. Entrants в t включаются
ДО events; censored в t всё ещё в risk, удаляются ПОСЛЕ events. Все причины events в t
обрабатываются одновременно с survival S ДО t, S изначально1: CIF[c]+=S*events[c]/risk,
S*=1-SUM(events)/risk. При risk0 S/CIF не меняются. Censoring не уменьшает S.
CIF изначально0. Десятичные приближения запрещены: все рациональные выводи сокращённой парой
[num,den] с den>0 (включая0=[0,1]). Restricted_mean=integral S(t) от0доtau, S постоянна между
exit-моментами и ПОСЛЕ обновления в t; risk gaps не обнуляют S. median=первый t с S<=1/2,
либо null. Survival по snapshots: {snapshot,cohorts:[{endpoint,excluded,points,restricted_mean,median}]}.
Excluded sorted request id. Point={time,risk,events:{complete,failed,cancelled},censored,survival,
incidence:{complete,failed,cancelled}}, все веса/S/CIF рациональные, time integer. Даже tau
без exit даёт point, все cohort включая пустые после exclusion сохраняются."""
    current[482] = upgraded_artifact(
        old[482],
        baseline_prompt(482, prompt),
        {**old[482].setup_files, "survival.json": dumps(policy)},
        answer,
    )
    return [current[number] for number in sorted(current)]
