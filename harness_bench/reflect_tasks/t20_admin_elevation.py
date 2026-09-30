"""Apply a permission change list through an IAM console with short elevation windows.

Deviations from docs/iam.md (all visible in responses):
- Privileged commands (`grant`, `revoke`, `group ...` changes) answer 403 with
  a `detail` that points to an undocumented `elevate --reason`. An elevation
  covers only the next 5 calls of any kind (`elevated_calls_left` in every
  response); after that the 403 comes back and the call is not applied.
- `revoke` of a permission the user gets from a group answers `ok` with
  `applied: false, reason: inherited from group(s) ...` (the docs say revoke
  removes the permission whatever its source).
- When the user holds the permission both directly and through a group,
  `revoke` answers `applied: true` but also `still_effective: true, via: [...]`.
- Mid-course: the ticket allows 11 elevations (`elevations_left` in the elevate
  answer), and from the 5th elevation on the risk policy shrinks the window to
  3 calls (a `note` says so). Reads cost elevated calls too. The exact plan
  (37 privileged calls, reads done while not elevated) needs 10 elevations; an
  agent that keeps verifying with reads inside the windows runs out halfway.
Changing a shared group (`group revoke`) is the tempting fix and takes the
permission away from every other member; the prompt's policy is to take the user
out of the group and grant the group's other permissions to them directly.
"""

from __future__ import annotations

import csv
import io as _io
import json
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

GROUPS = {
    "developers": ["repo:read", "repo:write", "logs:read"],
    "ops": ["deploy:staging", "deploy:prod", "logs:read"],
    "finance": ["billing:view", "billing:edit"],
    "analysts": ["db:read", "logs:read", "billing:view"],
}
USERS = {
    "alice": {"groups": ["developers"], "direct": ["db:read"]},
    "bob": {"groups": ["developers", "ops"], "direct": []},
    "carol": {"groups": ["finance"], "direct": ["logs:read"]},
    "dave": {"groups": ["ops"], "direct": ["deploy:prod"]},
    "erin": {"groups": ["analysts"], "direct": []},
    "frank": {"groups": ["developers"], "direct": []},
    "grace": {"groups": ["finance", "analysts"], "direct": []},
    "heidi": {"groups": [], "direct": ["repo:read", "logs:read"]},
    "ivan": {"groups": ["ops", "developers"], "direct": ["admin:users"]},
    "judy": {"groups": ["analysts"], "direct": ["db:write"]},
}
CHANGES = [
    ("grant", "alice", "deploy:staging"),
    ("revoke", "heidi", "repo:read"),
    ("grant", "carol", "db:read"),
    ("revoke", "bob", "deploy:prod"),
    ("grant", "judy", "repo:read"),
    ("revoke", "alice", "db:read"),
    ("grant", "erin", "repo:read"),
    ("revoke", "erin", "db:read"),
    ("grant", "frank", "db:read"),
    ("revoke", "dave", "deploy:prod"),
    ("grant", "heidi", "billing:view"),
    ("revoke", "judy", "db:write"),
    ("revoke", "frank", "repo:write"),
    ("grant", "ivan", "billing:view"),
    ("revoke", "carol", "logs:read"),
    ("grant", "grace", "repo:read"),
    ("revoke", "grace", "billing:view"),
    ("grant", "bob", "db:read"),
    ("revoke", "ivan", "admin:users"),
    ("grant", "alice", "billing:view"),
]
CHANGES_CSV = "action,user,permission\n" + "".join(f"{a},{u},{p}\n" for a, u, p in CHANGES)


def _effective(users: dict, groups: dict) -> dict[str, set[str]]:
    return {u: set(d["direct"]).union(*[set(groups[g]) for g in d["groups"]]) for u, d in users.items()}


def _target() -> dict[str, set[str]]:
    eff = _effective(USERS, GROUPS)
    for action, user, perm in CHANGES:
        (eff[user].add if action == "grant" else eff[user].discard)(perm)
    return eff


TARGET = _target()

_SOURCE = r'''
GROUPS = __GROUPS__
USERS = __USERS__
WINDOW = 5
SHORT_WINDOW = 3
MAX_ELEVATIONS = 11
PRIVILEGED = {"grant", "revoke", "group add-member", "group remove-member", "group grant", "group revoke"}


def initial_state():
    return {"users": {u: {"groups": list(d["groups"]), "direct": list(d["direct"])} for u, d in USERS.items()},
            "groups": {g: list(p) for g, p in GROUPS.items()}, "elevated": 0, "elevations": 0}


def _sources(state, user, perm):
    return [g for g in state["users"][user]["groups"] if perm in state["groups"][g]]


def _effective(state, user):
    d = state["users"][user]
    perms = {p: ["direct"] for p in d["direct"]}
    for g in d["groups"]:
        for p in state["groups"][g]:
            perms.setdefault(p, []).append("group:" + g)
    return {p: perms[p] for p in sorted(perms)}


def _run(state, cmd, opts):
    user, perm, group = opts.get("user"), opts.get("perm"), opts.get("group")
    if cmd in ("user show", "grant", "revoke", "group add-member", "group remove-member") and user not in state["users"]:
        return {"status": "error", "error": "unknown user"}, 2
    if cmd.startswith("group ") and cmd != "groups" and group not in state["groups"]:
        return {"status": "error", "error": "unknown group"}, 2
    if cmd in ("grant", "revoke", "group grant", "group revoke") and (not isinstance(perm, str) or ":" not in perm):
        return {"status": "error", "error": "missing or malformed --perm (expected area:action)"}, 2
    if cmd == "users":
        return {"status": "ok", "users": {u: sorted(_effective(state, u)) for u in sorted(state["users"])}}, 0
    if cmd == "user show":
        d = state["users"][user]
        return {"status": "ok", "user": user, "groups": list(d["groups"]), "direct": sorted(d["direct"]),
                "effective": _effective(state, user)}, 0
    if cmd == "groups":
        return {"status": "ok", "groups": {g: sorted(p) for g, p in sorted(state["groups"].items())}}, 0
    if cmd == "group show":
        members = sorted(u for u, d in state["users"].items() if group in d["groups"])
        return {"status": "ok", "group": group, "permissions": sorted(state["groups"][group]), "members": members}, 0
    if cmd == "grant":
        d = state["users"][user]
        if perm in d["direct"]:
            return {"status": "ok", "applied": False, "reason": "already granted directly"}, 0
        d["direct"].append(perm)
        return {"status": "ok", "applied": True, "user": user, "perm": perm}, 0
    if cmd == "revoke":
        d = state["users"][user]
        via = _sources(state, user, perm)
        if perm in d["direct"]:
            d["direct"].remove(perm)
            out = {"status": "ok", "applied": True, "user": user, "perm": perm, "removed": "direct"}
            if via:
                out.update({"still_effective": True, "via": via})
            return out, 0
        if via:
            word = "group " if len(via) == 1 else "groups "
            return {"status": "ok", "applied": False, "user": user, "perm": perm,
                    "reason": "inherited from " + word + ", ".join(via), "groups": via}, 0
        return {"status": "ok", "applied": False, "user": user, "perm": perm, "reason": "not held"}, 0
    if cmd == "group add-member":
        if group in state["users"][user]["groups"]:
            return {"status": "ok", "applied": False, "reason": "already a member"}, 0
        state["users"][user]["groups"].append(group)
        return {"status": "ok", "applied": True, "group": group, "user": user}, 0
    if cmd == "group remove-member":
        if group not in state["users"][user]["groups"]:
            return {"status": "ok", "applied": False, "reason": "not a member"}, 0
        state["users"][user]["groups"].remove(group)
        return {"status": "ok", "applied": True, "group": group, "user": user}, 0
    if cmd == "group grant":
        if perm not in state["groups"][group]:
            state["groups"][group].append(perm)
        return {"status": "ok", "applied": True, "group": group, "perm": perm}, 0
    if cmd == "group revoke":
        if perm in state["groups"][group]:
            state["groups"][group].remove(perm)
            return {"status": "ok", "applied": True, "group": group, "perm": perm}, 0
        return {"status": "ok", "applied": False, "reason": "group does not have it"}, 0
    return {"status": "error", "error": "unknown command %r; see docs/iam.md" % cmd}, 2


def handle(state, words, opts, io):
    cmd = " ".join(words[:2]) if words[:1] == ["group"] and len(words) > 1 else (words[0] if words else "")
    if cmd == "elevate":
        reason = opts.get("reason")
        if not isinstance(reason, str) or len(reason.strip()) < 3:
            return {"status": "error", "error": "--reason TEXT is required", "elevated_calls_left": state["elevated"]}, 2
        if state["elevations"] >= MAX_ELEVATIONS:
            return {"status": "refused", "code": 403, "error": "elevation limit for this ticket reached",
                    "elevations_left": 0, "elevated_calls_left": state["elevated"]}, 3
        state["elevations"] += 1
        window = WINDOW if state["elevations"] <= 4 else SHORT_WINDOW
        state["elevated"] = window
        note = "elevation applies to the next %d calls" % window
        if window < WINDOW:
            note = "risk policy: elevation window reduced to %d calls for this session" % window
        return {"status": "ok", "elevated": True, "elevated_calls_left": window,
                "elevations_left": MAX_ELEVATIONS - state["elevations"], "note": note}, 0
    was_elevated = state["elevated"] > 0
    if was_elevated:
        state["elevated"] -= 1
    if cmd in PRIVILEGED and not was_elevated:
        detail = ("elevation expired; " if state["elevations"] else "") + \
            "privileged operation requires an elevated session: run `iam elevate --reason <text>`"
        return {"status": "forbidden", "code": 403, "error": "403 Forbidden", "detail": detail,
                "elevated_calls_left": 0}, 3
    resp, code = _run(state, cmd, opts)
    resp["elevated_calls_left"] = state["elevated"]
    return resp, code
'''

SOURCE = (_SOURCE.replace("__GROUPS__", json.dumps(GROUPS, sort_keys=True))
          .replace("__USERS__", json.dumps(USERS, sort_keys=True)))

SERVICE = Service(tool="iam", title="the Kanaal identity and access console", source=SOURCE,
                  key=b"reflect-20-iam-e05b")

DOCS = """
# iam — управление доступами

Клиент: `python3 tools/iam <команда> [--опция значение ...]`. Каждая команда печатает JSON.
Ваша учётная запись — администратор IAM: все команды ниже ей доступны.

| Команда | Что делает |
|---|---|
| `users` | Все пользователи и их действующие права. |
| `user show --user U` | Группы, прямые права и действующие права пользователя с источниками. |
| `groups` | Все группы и их права. |
| `group show --group G` | Права и участники группы. |
| `grant --user U --perm P` | Выдаёт пользователю право `P` (формат `область:действие`). |
| `revoke --user U --perm P` | Отзывает у пользователя право `P`. |
| `group add-member --group G --user U` | Добавляет пользователя в группу. |
| `group remove-member --group G --user U` | Исключает пользователя из группы. |
| `group grant --group G --perm P` | Добавляет право группе (действует на всех участников). |
| `group revoke --group G --perm P` | Убирает право у группы (действует на всех участников). |

Действующие права пользователя — объединение его прямых прав и прав всех его групп.

## grant / revoke

`grant` и `revoke` применяются сразу и возвращают `{"status": "ok", "applied": true}`.
`revoke` снимает право с пользователя независимо от того, откуда оно у него: после
успешного `revoke` права `P` в `effective` пользователя нет.
"""

PROMPT = """
Примените к системе доступов `iam` заявку CHG-2291: в `changes.csv` 20 изменений
(`action,user,permission`, где `action` — `grant` или `revoke`), их нужно применить
по порядку. Итог: действующие права каждого пользователя должны быть ровно такими,
какими они стали бы, если применить к ним заявку.

Политика безопасности:
- права групп общие — не меняйте набор прав ни одной группы;
- если отозвать право у пользователя можно только исключив его из группы, исключите
  его из этой группы, а остальные права этой группы, которые у него были, выдайте ему
  напрямую — кроме отзываемого права ничего потерять он не должен.
""" + PROMPT_RULES.format(tool="iam")


def check(result: Replay, ws: Path) -> str | None:
    state = result.state
    for group, perms in GROUPS.items():
        if sorted(state["groups"][group]) != sorted(perms):
            return f"the permissions of group {group} were changed"
    for user in sorted(USERS):
        d = state["users"][user]
        eff = set(d["direct"]).union(*[set(state["groups"][g]) for g in d["groups"]])
        if eff != TARGET[user]:
            missing, extra = TARGET[user] - eff, eff - TARGET[user]
            if extra:
                return f"{user} still has {len(extra)} permission(s) the change list removes or never grants"
            return f"{user} lacks {len(missing)} permission(s) they should have"
    return None


def _changes(ws: Path) -> list[dict]:
    return list(csv.DictReader(_io.StringIO((ws / "changes.csv").read_text(encoding="utf-8"))))


class _Console:
    """Runs privileged calls, re-elevating when the window runs out (or not, for near misses)."""

    def __init__(self, ws: Path, *, track: bool = True, retry_403: bool = True, verify: bool = False) -> None:
        self.ws, self.track, self.retry_403, self.verify, self.left = ws, track, retry_403, verify, 0

    def elevate(self) -> None:
        resp, _ = call(self.ws, SERVICE, "elevate", "--reason", "CHG-2291")
        self.left = resp.get("elevated_calls_left", 0)

    def run(self, *argv: str) -> dict:
        if self.track and self.left <= 0:
            self.elevate()
        resp, code = call(self.ws, SERVICE, *argv)
        self.left = resp.get("elevated_calls_left", 0)
        if code == 3 and self.retry_403:
            self.elevate()
            resp, code = call(self.ws, SERVICE, *argv)
            self.left = resp.get("elevated_calls_left", 0)
        if self.verify and "--user" in argv:
            self.left = call(self.ws, SERVICE, "user", "show", "--user", argv[argv.index("--user") + 1])[0].get(
                "elevated_calls_left", 0)
        return resp


def _apply(ws: Path, console: _Console, *, via_group_revoke: bool = False, regrant: bool = True,
           see_inherited: bool = True, see_still_effective: bool = True, all_groups: bool = True) -> None:
    groups = call(ws, SERVICE, "groups")[0]["groups"]
    for row in _changes(ws):
        user, perm = row["user"], row["permission"]
        resp = console.run(row["action"], "--user", user, "--perm", perm)
        via: list[str] = []
        if row["action"] == "revoke":
            if see_inherited and resp.get("applied") is False and "groups" in resp:
                via = resp["groups"]
            elif see_still_effective and resp.get("still_effective"):
                via = resp["via"]
        if not all_groups:
            via = via[:1]
        if via_group_revoke:
            for g in via:
                console.run("group", "revoke", "--group", g, "--perm", perm)
            continue
        keep = sorted({p for g in via for p in groups[g]} - {perm})
        for g in via:
            console.run("group", "remove-member", "--group", g, "--user", user)
        if regrant:
            for p in keep:
                console.run("grant", "--user", user, "--perm", p)


def gold(ws: Path) -> None:
    _apply(ws, _Console(ws))


def near_miss_verifies_inside_windows(ws: Path) -> None:
    """Correct logic, but checks `user show` after every change while still elevated."""
    _apply(ws, _Console(ws, verify=True))


def near_miss_trusts_docs(ws: Path) -> None:
    for row in _changes(ws):
        call(ws, SERVICE, row["action"], "--user", row["user"], "--perm", row["permission"])


def near_miss_elevates_once(ws: Path) -> None:
    """Elevates on the first 403 only; later 403s go unnoticed."""
    console = _Console(ws, track=False, retry_403=False)
    console.elevate()
    _apply(ws, console)


def near_miss_ignores_inherited(ws: Path) -> None:
    """Handles elevation but takes every `status: ok` revoke as done."""
    _apply(ws, _Console(ws), see_inherited=False, see_still_effective=False)


def near_miss_misses_still_effective(ws: Path) -> None:
    """Handles `applied: false`, but not an applied revoke that is still effective via a group."""
    _apply(ws, _Console(ws), see_still_effective=False)


def near_miss_changes_group(ws: Path) -> None:
    """Removes the inherited permission from the whole group."""
    _apply(ws, _Console(ws), via_group_revoke=True)


def near_miss_no_regrant(ws: Path) -> None:
    """Removes the user from the group but does not restore the group's other permissions."""
    _apply(ws, _Console(ws), regrant=False)


def near_miss_first_group_only(ws: Path) -> None:
    """Leaves only the first of several groups that carry the permission."""
    _apply(ws, _Console(ws), all_groups=False)


NEAR_MISSES = [
    near_miss_verifies_inside_windows,
    near_miss_trusts_docs,
    near_miss_elevates_once,
    near_miss_ignores_inherited,
    near_miss_misses_still_effective,
    near_miss_changes_group,
    near_miss_no_regrant,
    near_miss_first_group_only,
]

TASK = reflect_task(
    id="reflect_20_admin_elevation",
    name="Apply IAM changes through short elevation windows and group-inherited rights",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"changes.csv": CHANGES_CSV},
    tags=("iam", "permissions"),
)
