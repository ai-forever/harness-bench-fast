"""File a batch of support tickets through a helpdesk whose gateway times out.

Deviations from docs/desk.md (all visible in responses):
- Some `create` calls end with exit 75 and "gateway timeout; the request may or may
  not have been applied". Some of them were applied, some were not; only a lookup
  tells which. A blind retry files a duplicate.
- The documented priority `urgent` does not exist: `create`/`update` silently store
  `normal` and add a `warnings` entry listing the allowed values (the highest is
  `critical`). A timed-out create never shows that warning, and two tickets from an
  interrupted earlier run already sit in the desk (one of them urgent, stored as
  `normal`).
- Mid-course surprise: after 13 creates the primary gateway fails over. From then on
  every response carries a `notice`, creates answer `gateway: backup` and store an
  empty `external_id` (with a warning to set it via `update --external-id`). The
  timeout check that worked so far (`find --external-id`) now misses tickets that
  were applied, so they must be found by title/requester in `list` and linked.
The trap: retrying timed-out creates (the obvious recovery) duplicates tickets, and
fixing priorities only where a warning was seen misses the silent ones.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

TICKETS = [
    {"external_id": "EXT-4101", "title": "Не печатает принтер на 3 этаже", "priority": "normal", "requester": "a.ivanova@kanaal.example"},
    {"external_id": "EXT-4102", "title": "Сброс пароля в 1С", "priority": "low", "requester": "p.smirnov@kanaal.example"},
    {"external_id": "EXT-4103", "title": "Упал сервер оплаты на кассах", "priority": "urgent", "requester": "ops@kanaal.example"},
    {"external_id": "EXT-4104", "title": "Новый ноутбук для стажёра", "priority": "low", "requester": "hr@kanaal.example"},
    {"external_id": "EXT-4105", "title": "Медленный VPN из филиала Утрехт", "priority": "high", "requester": "m.dekker@kanaal.example"},
    {"external_id": "EXT-4106", "title": "Доступ к общей папке маркетинга", "priority": "normal", "requester": "l.jansen@kanaal.example"},
    {"external_id": "EXT-4107", "title": "Замена картриджа в переговорной", "priority": "low", "requester": "office@kanaal.example"},
    {"external_id": "EXT-4108", "title": "Утечка данных: письмо ушло не тому клиенту", "priority": "urgent", "requester": "security@kanaal.example"},
    {"external_id": "EXT-4109", "title": "Не синхронизируется календарь", "priority": "normal", "requester": "k.bakker@kanaal.example"},
    {"external_id": "EXT-4110", "title": "Лицензия на графический редактор", "priority": "normal", "requester": "design@kanaal.example"},
    {"external_id": "EXT-4111", "title": "Склад: сканеры не видят сеть", "priority": "high", "requester": "warehouse@kanaal.example"},
    {"external_id": "EXT-4112", "title": "Перенос почтового ящика уволенного", "priority": "low", "requester": "hr@kanaal.example"},
    {"external_id": "EXT-4113", "title": "Ошибка при выгрузке отчёта в Excel", "priority": "normal", "requester": "finance@kanaal.example"},
    {"external_id": "EXT-4114", "title": "Сайт магазина отдаёт 502", "priority": "urgent", "requester": "web@kanaal.example"},
    {"external_id": "EXT-4115", "title": "Второй монитор для бухгалтерии", "priority": "low", "requester": "finance@kanaal.example"},
    {"external_id": "EXT-4116", "title": "Не приходит SMS с кодом входа", "priority": "high", "requester": "support@kanaal.example"},
    {"external_id": "EXT-4117", "title": "Обновить прошивку точки доступа", "priority": "normal", "requester": "it-ops@kanaal.example"},
    {"external_id": "EXT-4118", "title": "Гостевой Wi-Fi для конференции", "priority": "normal", "requester": "events@kanaal.example"},
    {"external_id": "EXT-4119", "title": "Остановилась линия фасовки: не отвечает контроллер", "priority": "urgent", "requester": "plant@kanaal.example"},
    {"external_id": "EXT-4120", "title": "Права администратора для разработчика", "priority": "normal", "requester": "dev@kanaal.example"},
    {"external_id": "EXT-4121", "title": "Шумит кондиционер в серверной", "priority": "high", "requester": "it-ops@kanaal.example"},
    {"external_id": "EXT-4122", "title": "Шифровальщик на ноутбуке менеджера", "priority": "urgent", "requester": "security@kanaal.example"},
    {"external_id": "EXT-4123", "title": "Настроить переадресацию телефона", "priority": "low", "requester": "reception@kanaal.example"},
    {"external_id": "EXT-4124", "title": "Сертификат сайта истекает через неделю", "priority": "high", "requester": "web@kanaal.example"},
    {"external_id": "EXT-4125", "title": "Установить Zoom в переговорной", "priority": "low", "requester": "office@kanaal.example"},
]

WANT_PRIORITY = {"low": "low", "normal": "normal", "high": "high", "urgent": "critical"}

SOURCE = r'''
ALLOWED = ["low", "normal", "high", "critical"]

UNRELATED = [
    ("EXT-3907", "Заказать бейджи для новых сотрудников", "low", "hr@kanaal.example"),
    ("EXT-3911", "Не открывается портал поставщиков", "high", "purchasing@kanaal.example"),
    ("EXT-3915", "Перенастроить резервное копирование", "normal", "it-ops@kanaal.example"),
    ("EXT-3920", "Проектор в зале Б мигает", "low", "office@kanaal.example"),
    ("EXT-3922", "Подключить новый склад к ERP", "high", "warehouse@kanaal.example"),
    # left over from yesterday's interrupted import
    ("EXT-4111", "Склад: сканеры не видят сеть", "high", "warehouse@kanaal.example"),
    ("EXT-4119", "Остановилась линия фасовки: не отвечает контроллер", "normal", "plant@kanaal.example"),
]


def initial_state():
    tickets = []
    for n, (ext, title, prio, req) in enumerate(UNRELATED):
        tickets.append({"id": f"T-{1001 + n}", "external_id": ext, "title": title, "priority": prio,
                        "requester": req, "status": "open"})
    return {"tickets": tickets, "next": 1001 + len(UNRELATED), "creates": 0}


def view(t):
    return {k: t[k] for k in ("id", "external_id", "title", "priority", "requester", "status")}


def live(state):
    return [t for t in state["tickets"] if t["status"] != "deleted"]


def by_id(state, tid):
    for t in live(state):
        if t["id"] == tid:
            return t
    return None


def norm_priority(value):
    if value in ALLOWED:
        return value, []
    return "normal", [f"priority {value!r} is not supported and was set to 'normal'; "
                      f"allowed values: {', '.join(ALLOWED)} (lowest to highest)"]


def opt_text(opts, key):
    val = opts.get(key)
    if isinstance(val, list):
        val = val[-1]
    return val if isinstance(val, str) and val.strip() else None


FAILOVER_AFTER = 13
NOTICE = ("the primary gateway is down; requests are served by the backup gateway, "
          "which does not store external ids on create")


def handle(state, words, opts, io):
    resp, code = serve(state, words, opts)
    if state["creates"] > FAILOVER_AFTER:
        resp["notice"] = NOTICE
    return resp, code


def serve(state, words, opts):
    cmd = words[0] if words else ""
    if cmd == "list":
        return {"status": "ok", "count": len(live(state)), "tickets": [view(t) for t in live(state)]}, 0
    if cmd == "find":
        ext = opt_text(opts, "external-id")
        if ext is None:
            return {"status": "error", "error": "--external-id is required"}, 2
        found = [view(t) for t in live(state) if t["external_id"] == ext]
        return {"status": "ok", "external_id": ext, "tickets": found}, 0
    if cmd == "get":
        t = by_id(state, opt_text(opts, "id"))
        if t is None:
            return {"status": "error", "error": "no such ticket"}, 3
        return {"status": "ok", "ticket": view(t)}, 0
    if cmd == "create":
        title = opt_text(opts, "title")
        req = opt_text(opts, "requester")
        if title is None or req is None:
            return {"status": "error", "error": "--title and --requester are required"}, 2
        prio, warnings = norm_priority(opt_text(opts, "priority") or "normal")
        state["creates"] += 1
        n = state["creates"]
        backup = n > FAILOVER_AFTER
        if n % 10 != 5:
            ext = "" if backup else opt_text(opts, "external-id") or ""
            t = {"id": f"T-{state['next']}", "external_id": ext,
                 "title": title, "priority": prio, "requester": req, "status": "open"}
            state["next"] += 1
            state["tickets"].append(t)
        if n % 10 in (3, 5, 6):
            return {"status": "error", "error": "gateway timeout; the request may or may not have been applied"}, 75
        resp = {"status": "ok", "gateway": "backup" if backup else "primary", "ticket": view(t)}
        if backup and opt_text(opts, "external-id"):
            warnings = warnings + ["external_id was not stored (backup gateway); set it with update --external-id"]
        if warnings:
            resp["warnings"] = warnings
        return resp, 0
    if cmd == "update":
        t = by_id(state, opt_text(opts, "id"))
        if t is None:
            return {"status": "error", "error": "no such ticket"}, 3
        warnings = []
        if opt_text(opts, "priority") is not None:
            t["priority"], warnings = norm_priority(opt_text(opts, "priority"))
        if opt_text(opts, "title") is not None:
            t["title"] = opt_text(opts, "title")
        if opt_text(opts, "external-id") is not None:
            t["external_id"] = opt_text(opts, "external-id")
        resp = {"status": "ok", "ticket": view(t)}
        if warnings:
            resp["warnings"] = warnings
        return resp, 0
    if cmd == "delete":
        t = by_id(state, opt_text(opts, "id"))
        if t is None:
            return {"status": "error", "error": "no such ticket"}, 3
        t["status"] = "deleted"
        return {"status": "ok", "deleted": t["id"]}, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/desk.md"}, 2
'''

SERVICE = Service(tool="desk", title="the Kanaal helpdesk", source=SOURCE, key=b"reflect-07-desk-b41e")

DOCS = """
# desk — служба поддержки

Клиент: `python3 tools/desk <команда> [--опция значение ...]`. Каждая команда печатает JSON.

| Команда | Что делает |
|---|---|
| `list` | Все тикеты (кроме удалённых): `id`, `external_id`, `title`, `priority`, `requester`, `status`. |
| `find --external-id X` | Тикеты с данным внешним идентификатором. |
| `get --id T-NNNN` | Один тикет. |
| `create --title T --requester EMAIL --priority P [--external-id X]` | Заводит тикет, возвращает его в поле `ticket`. |
| `update --id T-NNNN [--priority P] [--title T] [--external-id X]` | Меняет приоритет, заголовок или внешний идентификатор тикета. |
| `delete --id T-NNNN` | Удаляет тикет. |

Приоритеты (от низшего к высшему): `low`, `normal`, `high`, `urgent`.

`--external-id` — ваш идентификатор из внешней системы; сервис хранит его как есть
и не проверяет уникальность.

Коды выхода: `0` — успех, `2` — ошибка в аргументах, `3` — тикет не найден.
"""

PROMPT = """
Перенесите заявки из `tickets.json` в службу поддержки `desk`. Каждая заявка должна
оказаться в `desk` ровно одним тикетом (не удалённым) с тем же `external_id`,
`title` и `requester`. Приоритет переносится как есть, но `urgent` в файле означает
самый высокий приоритет из тех, что поддерживает сервис. Вчерашний перенос прервался
на полпути: часть заявок, возможно, уже заведена. Чужие тикеты (с `external_id` не из
файла) не трогайте.

В конце запишите `created.csv` с заголовком `external_id,ticket_id` — по строке на
каждую заявку из файла, в порядке файла; `ticket_id` — id её тикета в `desk`.
""" + PROMPT_RULES.format(tool="desk")


def _file_rows(ws: Path) -> list[dict[str, str]] | str:
    path = ws / "created.csv"
    if not path.exists():
        return "created.csv is missing"
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
    if not rows or set(rows[0]) != {"external_id", "ticket_id"}:
        return "created.csv must have the header external_id,ticket_id"
    return [{k: (v or "").strip() for k, v in row.items()} for row in rows]


def check(result: Replay, ws: Path) -> str | None:
    tickets = [t for t in result.state["tickets"] if t["status"] != "deleted"]
    ns = SERVICE.namespace()
    for ext, title, prio, req in ns["UNRELATED"][:5]:
        mine = [t for t in tickets if t["external_id"] == ext]
        if len(mine) != 1 or (mine[0]["title"], mine[0]["priority"], mine[0]["requester"]) != (title, prio, req):
            return f"the unrelated ticket {ext} was changed or deleted"
    wanted = {t["external_id"] for t in TICKETS} | {u[0] for u in ns["UNRELATED"]}
    stray = sorted({t["external_id"] or "(empty)" for t in tickets if t["external_id"] not in wanted})
    if stray:
        return f"the desk has tickets with unexpected external ids: {', '.join(stray[:3])}"
    ids = {}
    for spec in TICKETS:
        mine = [t for t in tickets if t["external_id"] == spec["external_id"]]
        if len(mine) != 1:
            return f"{spec['external_id']} has {len(mine)} live tickets instead of exactly one"
        t = mine[0]
        if (t["title"], t["requester"]) != (spec["title"], spec["requester"]):
            return f"{spec['external_id']} has the wrong title or requester"
        if t["priority"] != WANT_PRIORITY[spec["priority"]]:
            return f"{spec['external_id']} has priority {t['priority']!r}, not the one the file asks for"
        ids[spec["external_id"]] = t["id"]
    rows = _file_rows(ws)
    if isinstance(rows, str):
        return rows
    if [r["external_id"] for r in rows] != [t["external_id"] for t in TICKETS]:
        return "created.csv must list every ticket of tickets.json once, in file order"
    wrong = [r["external_id"] for r in rows if r["ticket_id"] != ids[r["external_id"]]]
    if wrong:
        return f"created.csv points {wrong[0]} to the wrong ticket"
    return None


def _write_csv(ws: Path, ids: dict[str, str]) -> None:
    lines = ["external_id,ticket_id"] + [f"{t['external_id']},{ids.get(t['external_id'], '')}" for t in TICKETS]
    (ws / "created.csv").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _load(ws: Path) -> list[dict[str, str]]:
    return json.loads((ws / "tickets.json").read_text(encoding="utf-8"))


def _learn(resp: dict, mapping: dict[str, str]) -> None:
    """A warning lists the allowed values lowest to highest: map `urgent` to the last one."""
    for warning in resp.get("warnings", []):
        if "allowed values:" in warning:
            allowed = warning.split("allowed values:", 1)[1].split("(")[0]
            mapping["urgent"] = [v.strip() for v in allowed.split(",")][-1]


def _file(ws: Path, spec: dict[str, str], *, prio: dict[str, str], on_timeout: str, failover: bool,
          fix_warned: bool = False) -> str | None:
    """Create one ticket; return its id (None if the agent believes it is not there)."""
    for _ in range(4):
        resp, code = call(ws, SERVICE, "create", "--title", spec["title"], "--requester", spec["requester"],
                          "--priority", prio[spec["priority"]], "--external-id", spec["external_id"])
        backup = "notice" in resp
        if code == 0:
            tid = resp["ticket"]["id"]
            if fix_warned and any("allowed values" in w for w in resp.get("warnings", [])):
                _learn(resp, prio)
                call(ws, SERVICE, "update", "--id", tid, "--priority", prio[spec["priority"]])
            if failover and resp["ticket"]["external_id"] != spec["external_id"]:
                call(ws, SERVICE, "update", "--id", tid, "--external-id", spec["external_id"])
            return tid
        if on_timeout == "assume_applied":
            return None
        if on_timeout == "find":
            found = call(ws, SERVICE, "find", "--external-id", spec["external_id"])[0]["tickets"]
            if found:
                return found[0]["id"]
            if failover and backup:
                orphans = [t for t in call(ws, SERVICE, "list")[0]["tickets"]
                           if t["external_id"] == "" and t["title"] == spec["title"]
                           and t["requester"] == spec["requester"]]
                if orphans:
                    call(ws, SERVICE, "update", "--id", orphans[0]["id"], "--external-id", spec["external_id"])
                    return orphans[0]["id"]
    return None


def gold(ws: Path) -> None:
    mapping = {"low": "low", "normal": "normal", "high": "high", "urgent": "urgent"}
    existing = {t["external_id"] for t in call(ws, SERVICE, "list")[0]["tickets"]}
    for spec in _load(ws):
        if spec["external_id"] not in existing:
            assert _file(ws, spec, prio=mapping, on_timeout="find", failover=True, fix_warned=True), spec
    tickets = call(ws, SERVICE, "list")[0]["tickets"]
    ids = {}
    for spec in _load(ws):
        mine = [t for t in tickets if t["external_id"] == spec["external_id"]]
        assert len(mine) == 1, mine
        ids[spec["external_id"]] = mine[0]["id"]
        while mine[0]["priority"] != mapping[spec["priority"]] or (spec["priority"] == "urgent" and mapping["urgent"] == "urgent"):
            resp = call(ws, SERVICE, "update", "--id", mine[0]["id"], "--priority", mapping[spec["priority"]])[0]
            _learn(resp, mapping)
            mine = [resp["ticket"]]
    _write_csv(ws, ids)


def _run(ws: Path, *, precheck: bool = True, prio: dict[str, str] | None = None, on_timeout: str = "find",
         failover: bool = True, fix_warned: bool = False) -> None:
    prio = dict(prio or WANT_PRIORITY)
    existing = {t["external_id"]: t["id"] for t in call(ws, SERVICE, "list")[0]["tickets"]} if precheck else {}
    ids = dict(existing)
    for spec in _load(ws):
        if spec["external_id"] not in existing:
            tid = _file(ws, spec, prio=prio, on_timeout=on_timeout, failover=failover, fix_warned=fix_warned)
            if tid:
                ids[spec["external_id"]] = tid
    _write_csv(ws, ids)


AS_DOCUMENTED = {"low": "low", "normal": "normal", "high": "high", "urgent": "urgent"}


def near_miss_literal_docs(ws: Path) -> None:
    _run(ws, precheck=False, prio=AS_DOCUMENTED, on_timeout="retry", failover=False)


def near_miss_blind_retry(ws: Path) -> None:
    _run(ws, on_timeout="retry")


def near_miss_timeout_means_applied(ws: Path) -> None:
    _run(ws, on_timeout="assume_applied")


def near_miss_keeps_urgent(ws: Path) -> None:
    _run(ws, prio=AS_DOCUMENTED)


def near_miss_fixes_only_warned(ws: Path) -> None:
    _run(ws, prio=AS_DOCUMENTED, fix_warned=True)


def near_miss_no_replan_after_failover(ws: Path) -> None:
    """Handles timeouts and priorities, but keeps trusting find --external-id after the failover."""
    _run(ws, failover=False)


def near_miss_links_but_trusts_find(ws: Path) -> None:
    """Links external ids after the failover, but still dedupes timeouts only through find."""
    prio = dict(WANT_PRIORITY)
    existing = {t["external_id"]: t["id"] for t in call(ws, SERVICE, "list")[0]["tickets"]}
    ids = dict(existing)
    for spec in _load(ws):
        if spec["external_id"] in existing:
            continue
        for _ in range(4):
            resp, code = call(ws, SERVICE, "create", "--title", spec["title"], "--requester", spec["requester"],
                              "--priority", prio[spec["priority"]], "--external-id", spec["external_id"])
            if code == 0:
                ids[spec["external_id"]] = resp["ticket"]["id"]
                if resp["ticket"]["external_id"] != spec["external_id"]:
                    call(ws, SERVICE, "update", "--id", resp["ticket"]["id"], "--external-id", spec["external_id"])
                break
            found = call(ws, SERVICE, "find", "--external-id", spec["external_id"])[0]["tickets"]
            if found:
                ids[spec["external_id"]] = found[0]["id"]
                break
    _write_csv(ws, ids)


NEAR_MISSES = [
    near_miss_literal_docs,
    near_miss_blind_retry,
    near_miss_timeout_means_applied,
    near_miss_keeps_urgent,
    near_miss_fixes_only_warned,
    near_miss_no_replan_after_failover,
    near_miss_links_but_trusts_find,
]

TASK = reflect_task(
    id="reflect_07_ticket_timeout_dupes",
    name="File tickets exactly once through a timing-out helpdesk with an undocumented priority set",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"tickets.json": json.dumps(TICKETS, ensure_ascii=False, indent=2) + "\n"},
    tags=("idempotency", "retries"),
)
