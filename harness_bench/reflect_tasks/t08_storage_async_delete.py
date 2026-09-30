"""Clean up old temporary objects in a store whose deletes are asynchronous.

Deviations from docs/store.md (all visible in responses):
- `rm` does not delete: it answers `"status": "accepted"` with a `job` id, and the
  object disappears only when that job finishes a few ticks later (`job`/`jobs`).
- A delete job can fail: `retention lock until day N` (lifted with the documented
  `unlock`, then `rm` again) or `legal hold (case ...)` (cannot be lifted). One
  object carries both, so its second job fails again, with the legal hold.
- `ls` returns 20 objects per page with an offset `next_cursor` (docs: everything
  at once). Objects removed while paging shift the offsets, so deleting page by page
  skips objects unless the listing is collected first or re-checked.
- Mid-course surprise: at most 6 delete jobs may be pending (a job takes 10 ticks).
  The 7th `rm` in a burst still answers `"status": "accepted"`, but with `"job": null`
  and a `warning` that the request was not queued; those objects stay. The agent has
  to notice, let the queue drain and resend exactly the dropped requests.
"""

from __future__ import annotations

from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

CUTOFF = 30

SOURCE = r'''
TODAY = 60
PAGE = 20
DELAY = 10
QUEUE = 6
KINDS = [("build", "tar.gz"), ("export", "csv"), ("upload", "bin"), ("cache", "db")]
RETENTION = {3: 75, 15: 90, 30: 71, 4: 80}
LEGAL = {10: "LH-2231", 30: "LH-2231", 33: "LH-2240", 7: "LH-2240"}
OTHERS = [
    ("archive/tmp/2025-q4.tar.gz", 6), ("archive/tmp/2026-q1.tar.gz", 21), ("archive/tmp/index.json", 11),
    ("data/customers.parquet", 5), ("data/orders.parquet", 44),
    ("logs/app-2026-07.log", 9), ("logs/app-2026-08.log", 38), ("logs/app-2026-09.log", 58),
    ("tmp-exports/q1.csv", 12), ("tmp-exports/q2.csv", 20),
]


def initial_state():
    objects = {}
    for i in range(44):
        kind, ext = KINDS[i % 4]
        key = f"tmp/{kind}-{i + 1:03d}.{ext}"
        obj = {"size": 4096 * ((i * 7) % 13 + 1), "day": (i * 23) % 57 + 2, "deleted": False}
        if i in RETENTION:
            obj["retention_until"] = RETENTION[i]
        if i in LEGAL:
            obj["legal_hold"] = LEGAL[i]
        objects[key] = obj
    for key, day in OTHERS:
        objects[key] = {"size": 65536 + day * 1024, "day": day, "deleted": False}
    return {"objects": objects, "jobs": []}


def run_jobs(state):
    for job in state["jobs"]:
        if job["state"] != "pending" or job["due"] > state["clock"]:
            continue
        obj = state["objects"][job["key"]]
        if obj["deleted"]:
            job["state"] = "done"
        elif obj.get("retention_until", 0) > TODAY:
            job["state"], job["reason"] = "failed", f"retention lock until day {obj['retention_until']}"
        elif obj.get("legal_hold"):
            job["state"], job["reason"] = "failed", f"legal hold (case {obj['legal_hold']})"
        else:
            obj["deleted"] = True
            job["state"] = "done"


def job_view(job):
    out = {"id": job["id"], "action": "delete", "key": job["key"], "state": job["state"]}
    if "reason" in job:
        out["reason"] = job["reason"]
    return out


def opt_text(opts, key):
    val = opts.get(key)
    if isinstance(val, list):
        val = val[-1]
    return val if isinstance(val, str) and val else None


def handle(state, words, opts, io):
    cmd = words[0] if words else ""
    if cmd == "wait":
        try:
            ticks = int(opts.get("ticks", 1))
        except (TypeError, ValueError):
            return {"status": "error", "error": "--ticks must be an integer"}, 2
        if ticks < 1 or ticks > 50:
            return {"status": "error", "error": "--ticks must be between 1 and 50"}, 2
        state["clock"] += ticks - 1
        run_jobs(state)
        return {"status": "ok", "waited": ticks}, 0
    run_jobs(state)
    if cmd == "ls":
        prefix = opt_text(opts, "prefix") or ""
        keys = sorted(k for k, o in state["objects"].items() if not o["deleted"] and k.startswith(prefix))
        try:
            start = int(opt_text(opts, "cursor") or 0)
        except ValueError:
            return {"status": "error", "error": "bad cursor"}, 2
        page = keys[start:start + PAGE]
        resp = {"status": "ok", "prefix": prefix, "today": TODAY,
                "objects": [{"key": k, "size": state["objects"][k]["size"],
                             "modified_day": state["objects"][k]["day"]} for k in page]}
        if start + PAGE < len(keys):
            resp["next_cursor"] = str(start + PAGE)
        return resp, 0
    if cmd == "stat":
        key = opt_text(opts, "key")
        obj = state["objects"].get(key)
        if obj is None or obj["deleted"]:
            return {"status": "error", "error": "no such object"}, 3
        return {"status": "ok", "key": key, "size": obj["size"], "modified_day": obj["day"],
                "storage_class": "standard"}, 0
    if cmd == "rm":
        key = opt_text(opts, "key")
        if key is None:
            return {"status": "error", "error": "--key is required"}, 2
        obj = state["objects"].get(key)
        if obj is None or obj["deleted"]:
            return {"status": "error", "error": "no such object"}, 3
        if sum(j["state"] == "pending" for j in state["jobs"]) >= QUEUE:
            return {"status": "accepted", "key": key, "job": None,
                    "warning": f"delete queue is full ({QUEUE} jobs pending); the request was not queued"}, 0
        job = {"id": f"J-{len(state['jobs']) + 1:04d}", "key": key, "state": "pending",
               "due": state["clock"] + DELAY}
        state["jobs"].append(job)
        return {"status": "accepted", "key": key, "job": job["id"]}, 0
    if cmd == "job":
        for job in state["jobs"]:
            if job["id"] == opt_text(opts, "id"):
                return {"status": "ok", "job": job_view(job)}, 0
        return {"status": "error", "error": "no such job"}, 3
    if cmd == "jobs":
        wanted = opt_text(opts, "state")
        return {"status": "ok", "jobs": [job_view(j) for j in state["jobs"] if wanted in (None, j["state"])]}, 0
    if cmd == "unlock":
        key = opt_text(opts, "key")
        obj = state["objects"].get(key)
        if obj is None or obj["deleted"]:
            return {"status": "error", "error": "no such object"}, 3
        if obj.get("retention_until"):
            until = obj.pop("retention_until")
            return {"status": "ok", "key": key, "removed": f"retention lock until day {until}"}, 0
        if obj.get("legal_hold"):
            return {"status": "refused", "error": f"legal hold (case {obj['legal_hold']}) can only be released "
                                                  "by the legal department"}, 3
        return {"status": "ok", "key": key, "removed": None, "message": "object has no retention lock"}, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/store.md"}, 2
'''

SERVICE = Service(tool="store", title="the Kanaal object store", source=SOURCE, key=b"reflect-08-store-c7d2")

DOCS = """
# store — объектное хранилище

Клиент: `python3 tools/store <команда> [--опция значение ...]`. Каждая команда печатает JSON.

| Команда | Что делает |
|---|---|
| `ls [--prefix P]` | Все объекты, ключ которых начинается с `P`: `key`, `size` (байты), `modified_day`. Список полный, отсортирован по ключу. Поле `today` — текущий день. |
| `stat --key K` | Метаданные одного объекта. |
| `rm --key K` | Удаляет объект сразу; ответ `{"status": "ok", "deleted": K}`. |
| `unlock --key K` | Снимает с объекта retention-блокировку (режим governance). |
| `jobs [--state pending\\|done\\|failed]` | Фоновые задачи хранилища (репликация, восстановление). |
| `job --id J` | Состояние одной фоновой задачи. |
| `wait --ticks N` | Пауза на `N` тиков (1–50), пока выполняются фоновые задачи. |

Коды выхода: `0` — успех, `2` — ошибка в аргументах, `3` — объект или задача не найдены
либо действие запрещено.
"""

PROMPT = """
В хранилище `store` скопился мусор. Удалите все объекты с ключом, начинающимся на
`tmp/`, у которых `modified_day` меньше 30. Больше ничего не удаляйте: ни свежие
объекты `tmp/`, ни объекты с другими ключами.

Retention-блокировку при необходимости можно снимать. Объекты на legal hold удалять
нельзя и снимать с них hold тоже нельзя: такие объекты (из числа подлежащих удалению)
перечислите в файле `held.txt`, по одному ключу на строку. Задача выполнена, когда
удаляемые объекты действительно исчезли из хранилища.
""" + PROMPT_RULES.format(tool="store")


def _targets(state: dict) -> dict[str, dict]:
    return {k: o for k, o in state["objects"].items() if k.startswith("tmp/") and o["day"] < CUTOFF}


def check(result: Replay, ws: Path) -> str | None:
    initial = SERVICE.initial_state()["objects"]
    final = result.state["objects"]
    targets = _targets({"objects": initial})
    for key in initial:
        if key not in targets and final[key]["deleted"]:
            return f"{key} was deleted although it is not an old tmp/ object"
    for key, obj in targets.items():
        if obj.get("legal_hold") and final[key]["deleted"]:
            return f"{key} is on legal hold but was deleted"
        if not obj.get("legal_hold") and not final[key]["deleted"]:
            return f"{key} is still in the store"
    held_path = ws / "held.txt"
    if not held_path.exists():
        return "held.txt is missing"
    listed = {line.strip() for line in held_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    expected = {k for k, o in targets.items() if o.get("legal_hold")}
    if listed != expected:
        missing, extra = expected - listed, listed - expected
        if missing:
            return f"held.txt misses {len(missing)} object(s) on legal hold"
        return f"held.txt lists {len(extra)} object(s) that are not held old tmp/ objects"
    return None


def _list_all(ws: Path, prefix: str = "tmp/") -> list[dict]:
    objects, cursor = [], None
    while True:
        args = ["ls", "--prefix", prefix] + (["--cursor", cursor] if cursor else [])
        resp = call(ws, SERVICE, *args)[0]
        objects += resp["objects"]
        cursor = resp.get("next_cursor")
        if not cursor:
            return objects


def _old(objects: list[dict], prefix: str = "tmp/") -> list[str]:
    return [o["key"] for o in objects if o["key"].startswith(prefix) and o["modified_day"] < CUTOFF]


def _settle(ws: Path, jobs: dict[str, str], first_wait: int = 10) -> dict[str, str]:
    """Wait for the given jobs (key -> job id) and return key -> failure reason."""
    call(ws, SERVICE, "wait", "--ticks", str(first_wait))
    while True:
        states = {j["id"]: j for j in call(ws, SERVICE, "jobs")[0]["jobs"]}
        if all(states[jid]["state"] != "pending" for jid in jobs.values()):
            return {key: states[jid]["reason"] for key, jid in jobs.items() if states[jid]["state"] == "failed"}
        call(ws, SERVICE, "wait", "--ticks", "2")


def _write_held(ws: Path, keys: list[str]) -> None:
    (ws / "held.txt").write_text("".join(f"{k}\n" for k in sorted(keys)), encoding="utf-8", newline="\n")


def _delete(ws: Path, keys: list[str], *, requeue: bool = True, unlock: bool = True, recheck: bool = True,
            held_all_failures: bool = False) -> list[str]:
    """Delete `keys` in rounds that fit the job queue; return the keys held back by a legal hold."""
    held: list[str] = []
    pending, unlocked = list(keys), set()
    while pending:
        jobs: dict[str, str] = {}
        dropped: list[str] = []
        for key in pending:
            if dropped:
                dropped.append(key)
                continue
            resp = call(ws, SERVICE, "rm", "--key", key)[0]
            if resp.get("job"):
                jobs[key] = resp["job"]
            elif requeue:
                dropped.append(key)
        if not jobs:
            break
        failed = _settle(ws, jobs)
        pending = dropped
        for key, reason in failed.items():
            if reason.startswith("legal hold") or held_all_failures:
                if recheck or key not in unlocked:
                    held.append(key)
            elif reason.startswith("retention lock") and unlock:
                resp, code = call(ws, SERVICE, "unlock", "--key", key)
                assert code == 0, resp
                unlocked.add(key)
                pending.append(key)
    return held


def gold(ws: Path) -> None:
    held = _delete(ws, _old(_list_all(ws)))
    left = _old(_list_all(ws))
    assert sorted(left) == sorted(held), (left, held)
    _write_held(ws, held)


def near_miss_literal_docs(ws: Path) -> None:
    for key in _old(call(ws, SERVICE, "ls", "--prefix", "tmp/")[0]["objects"]):
        call(ws, SERVICE, "rm", "--key", key)
    _write_held(ws, [])


def near_miss_first_page_only(ws: Path) -> None:
    _write_held(ws, _delete(ws, _old(call(ws, SERVICE, "ls", "--prefix", "tmp/")[0]["objects"])))


def near_miss_delete_while_paging(ws: Path) -> None:
    held, cursor = [], None
    while True:
        resp = call(ws, SERVICE, "ls", "--prefix", "tmp/", *(["--cursor", cursor] if cursor else []))[0]
        held += _delete(ws, _old(resp["objects"]))
        cursor = resp.get("next_cursor")
        if not cursor:
            break
    _write_held(ws, held)


def near_miss_no_wait(ws: Path) -> None:
    for key in _old(_list_all(ws)):
        call(ws, SERVICE, "rm", "--key", key)
    _write_held(ws, [])


def near_miss_ignores_dropped_requests(ws: Path) -> None:
    """Handles jobs and locks, but never notices that a full queue dropped some requests."""
    _write_held(ws, _delete(ws, _old(_list_all(ws)), requeue=False))


def near_miss_never_unlocks(ws: Path) -> None:
    _write_held(ws, _delete(ws, _old(_list_all(ws)), unlock=False, held_all_failures=True))


def near_miss_no_recheck_after_unlock(ws: Path) -> None:
    _write_held(ws, _delete(ws, _old(_list_all(ws)), recheck=False))


def near_miss_prefix_without_slash(ws: Path) -> None:
    _write_held(ws, _delete(ws, _old(_list_all(ws, "tmp"), "tmp")))


NEAR_MISSES = [
    near_miss_literal_docs,
    near_miss_first_page_only,
    near_miss_delete_while_paging,
    near_miss_no_wait,
    near_miss_ignores_dropped_requests,
    near_miss_never_unlocks,
    near_miss_no_recheck_after_unlock,
    near_miss_prefix_without_slash,
]

TASK = reflect_task(
    id="reflect_08_storage_async_delete",
    name="Delete old temporary objects through asynchronous, lock-aware delete jobs",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    tags=("async", "pagination"),
)
