"""Append lines to wiki pages that a link-checking bot edits at the same time.

Deviations from docs/wiki.md (all visible in responses):
- `save` does not always overwrite: when the page changed since the caller's last
  `get`, it answers `"status": "ok"` with `"applied": false`, `reason: stale revision`
  and `current_revision`. A bot edits some pages a tick or two after they are read,
  so the first save of those pages is refused (one page twice in a row).
- `get` returns at most 40 lines, with `truncated: true` and `next_offset` (docs:
  the whole page). Saving a truncated copy cuts the page's tail.
- Mid-course surprise: `save` accepts at most 4096 bytes. A longer save (only the
  long runbook) still answers `applied: true`, but with `stored_lines`,
  `received_lines` and a `warning`: the page now ends after the stored lines. The
  rest has to be sent with the undocumented `save --append` (named in the warning),
  and only the rest: appending the whole file again duplicates the head.
The trap: the stale answer suggests `--force` (and shows `current_revision`); saving
the old copy that way erases the bot's edits. The page has to be re-read in full and
the lines appended to the fresh text.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

EDITS = {
    "onboarding": [
        "- С 1 октября пропуска выдаёт ресепшен на 1 этаже.",
        "- Вводный инструктаж по ИБ — в первый вторник месяца.",
    ],
    "runbook-db": [
        "## Переключение на реплику (добавлено после инцидента INC-7781)",
        "1. Остановить запись: `db-ctl freeze --cluster main`.",
        "2. Дождаться нулевого лага на db-3 и промотировать её.",
    ],
    "faq": [
        "**Где взять счёт-фактуру?** В личном кабинете, раздел «Документы».",
    ],
    "release-notes": [
        "## 4.12.0",
        "- Экспорт отчётов в XLSX.",
        "- Исправлено округление скидок в корзине.",
    ],
    "oncall": [
        "- Эскалация после 30 минут без ответа — руководителю смены.",
        "- Журнал дежурства ведём в канале #oncall-log.",
    ],
}

SOURCE = r'''
PAGE_LINES = 40
BODY_LIMIT = 4096


def _runbook():
    lines = ["# Runbook: основной кластер БД", "", "Статус: действующий", ""]
    for n in range(1, 50):
        lines.append(f"{n}. Проверить узел db-{n % 4 + 1}: лаг репликации, место на диске, журнал ошибок (шаг {n}).")
    lines += ["", "Ответственный: команда платформы"]
    return lines


def _release_notes():
    lines = ["# Release notes", ""]
    for minor in range(11, 0, -1):
        lines += [f"## 4.{minor}.0", f"- Улучшения производительности каталога (4.{minor}).",
                  f"- Исправления ошибок, найденных в 4.{minor - 1}.", ""]
    lines += ["## 4.3.0", "- Первая публичная версия.", "- Подробности — в архиве.", ""]
    return lines[:47]


PAGES = {
    "onboarding": ["# Онбординг", "", "Добро пожаловать в Kanaal!", "",
                   "- Ноутбук выдаёт ИТ в первый день.",
                   "- Чат команды: http://chat.kanaal.example/it",
                   "- Почта настраивается автоматически.",
                   "- Доступы в 1С запрашивайте через desk.",
                   "- Вики: https://wiki.kanaal.example",
                   "", "Вопросы — в канал #welcome.", ""],
    "runbook-db": _runbook(),
    "faq": ["# FAQ", "", "**Как сменить пароль?** Через портал self-service.",
            "**Где график отпусков?** В HR-портале.", "**Как заказать пропуск гостю?** Через ресепшен.",
            "**Кто оплачивает такси?** Только по согласованию с руководителем.", "", ""],
    "release-notes": _release_notes(),
    "oncall": ["# Дежурства", "", "- График: https://oncall.kanaal.example/schedule",
               "- Телефон дежурного: +31 20 555 0101",
               "- Инструкция: http://wiki.kanaal.example/oncall-howto",
               "- Пейджер: PagerDuty, сервис KAN-PROD",
               "", "Передача смены — в 09:00.", "", ""],
    "contacts": ["# Контакты", "", "- ИТ: it@kanaal.example", "- HR: hr@kanaal.example",
                 "- Бухгалтерия: finance@kanaal.example", ""],
}

# page -> {n-th full read: (delay in ticks, edit)}; an edit is ("append", line) or ("replace", old, new)
BOT = {
    "onboarding": {1: (1, ("replace", "- Чат команды: http://chat.kanaal.example/it",
                           "- Чат команды: https://chat.kanaal.example/it-support"))},
    "runbook-db": {1: (2, ("append", "<!-- linkbot: проверено 12 ссылок -->")),
                   2: (1, ("append", "<!-- linkbot: повторная проверка, всё в порядке -->"))},
    "release-notes": {1: (3, ("append", "<!-- linkbot: проверено 3 ссылки -->"))},
    "oncall": {1: (1, ("replace", "- Инструкция: http://wiki.kanaal.example/oncall-howto",
                       "- Инструкция: https://wiki.kanaal.example/oncall-howto")),
               2: (1, ("replace", "- Телефон дежурного: +31 20 555 0101",
                       "- Телефон дежурного: +31 20 555 0199 (обновлено по справочнику)"))},
}


def initial_state():
    pages = {}
    for name, lines in PAGES.items():
        pages[name] = {"lines": list(lines), "shadow": list(lines), "revision": 1, "reads": 0,
                       "seen": None, "history": [{"revision": 1, "author": "import", "summary": "initial import"}]}
    return {"pages": pages, "bot_queue": []}


def run_bot(state):
    keep = []
    for item in state["bot_queue"]:
        if item["due"] > state["clock"]:
            keep.append(item)
            continue
        page = state["pages"][item["page"]]
        edit = item["edit"]
        for target in (page["lines"], page["shadow"]):
            if edit[0] == "append":
                target.append(edit[1])
            elif edit[1] in target:
                target[target.index(edit[1])] = edit[2]
        page["revision"] += 1
        page["history"].append({"revision": page["revision"], "author": "linkbot", "summary": "link maintenance"})
    state["bot_queue"] = keep


def opt_text(opts, key):
    val = opts.get(key)
    if isinstance(val, list):
        val = val[-1]
    return val if isinstance(val, str) and val else None


def handle(state, words, opts, io):
    run_bot(state)
    cmd = words[0] if words else ""
    if cmd == "pages":
        return {"status": "ok", "pages": [{"page": k, "revision": v["revision"]} for k, v in state["pages"].items()]}, 0
    name = opt_text(opts, "page")
    if cmd in ("get", "save", "history") and name not in state["pages"]:
        return {"status": "error", "error": "no such page; see `pages`"}, 3
    if cmd == "history":
        return {"status": "ok", "page": name, "history": state["pages"][name]["history"]}, 0
    if cmd == "get":
        page = state["pages"][name]
        try:
            offset = int(opt_text(opts, "offset") or 0)
        except ValueError:
            return {"status": "error", "error": "--offset must be an integer"}, 2
        if offset == 0:
            page["reads"] += 1
            plan = BOT.get(name, {}).get(page["reads"])
            if plan:
                state["bot_queue"].append({"page": name, "due": state["clock"] + plan[0], "edit": list(plan[1])})
        page["seen"] = page["revision"]
        chunk = page["lines"][offset:offset + PAGE_LINES]
        resp = {"status": "ok", "page": name, "revision": page["revision"], "content": "\n".join(chunk) + "\n"}
        if offset + PAGE_LINES < len(page["lines"]):
            resp["truncated"] = True
            resp["next_offset"] = offset + PAGE_LINES
            resp["hint"] = f"page is longer than {PAGE_LINES} lines; call get with --offset {offset + PAGE_LINES} for the rest"
        return resp, 0
    if cmd == "save":
        page = state["pages"][name]
        path = opt_text(opts, "file")
        if path is None:
            return {"status": "error", "error": "--file is required"}, 2
        text = io.read(path).replace("\r\n", "\n")
        lines = [line.rstrip() for line in text.split("\n")]
        while lines and lines[-1] == "":
            lines.pop()
        base = opt_text(opts, "revision")
        try:
            base = int(base) if base is not None else page["seen"]
        except ValueError:
            return {"status": "error", "error": "--revision must be an integer"}, 2
        if base != page["revision"] and opts.get("force") is not True:
            return {"status": "ok", "page": name, "applied": False, "reason": "stale revision",
                    "your_revision": base, "current_revision": page["revision"],
                    "hint": "re-read the page with get and retry, or pass --force to overwrite anyway"}, 0
        stored, size = [], 0
        for line in lines:
            size += len(line.encode("utf-8")) + 1
            if size > BODY_LIMIT:
                break
            stored.append(line)
        append = opts.get("append") is True
        if append:
            current = list(page["lines"])
            while current and current[-1] == "":
                current.pop()
            page["lines"] = current + stored + [""]
        else:
            page["lines"] = stored + [""]
        page["revision"] += 1
        page["seen"] = page["revision"]
        page["history"].append({"revision": page["revision"], "author": "you", "summary": "append" if append else "save"})
        resp = {"status": "ok", "page": name, "applied": True, "revision": page["revision"]}
        if len(stored) < len(lines):
            resp["stored_lines"] = len(stored)
            resp["received_lines"] = len(lines)
            resp["warning"] = (f"request body is limited to {BODY_LIMIT} bytes: only the first {len(stored)} of "
                               f"{len(lines)} lines were stored; send the remaining lines with "
                               "save --page P --file REST --append")
        return resp, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/wiki.md"}, 2
'''

SERVICE = Service(tool="wiki", title="the Kanaal wiki", source=SOURCE, key=b"reflect-09-wiki-5e90")

DOCS = """
# wiki — корпоративная вики

Клиент: `python3 tools/wiki <команда> [--опция значение ...]`. Каждая команда печатает JSON.

| Команда | Что делает |
|---|---|
| `pages` | Список страниц и их текущие ревизии. |
| `get --page P` | Полный текст страницы (`content`) и номер ревизии (`revision`). |
| `save --page P --file PATH` | Записывает содержимое локального файла `PATH` (UTF-8) как новый текст страницы. Страница всегда перезаписывается целиком, ревизия увеличивается на 1. |
| `history --page P` | История ревизий страницы: номер, автор, краткое описание. |

Страницы — обычный Markdown. `save` заменяет текст страницы полностью, поэтому, чтобы
дописать строки, возьмите текущий текст через `get`, добавьте строки и сохраните.

Коды выхода: `0` — успех, `2` — ошибка в аргументах, `3` — страница не найдена.
"""

PROMPT = """
В `edits.json` для нескольких страниц вики `wiki` перечислены строки, которые нужно
дописать в конец страницы — после всего существующего текста, в указанном порядке,
каждую ровно один раз. Остальной текст страниц должен сохраниться, включая правки,
которые параллельно вносят другие участники (например, бот, чинящий ссылки). Страницы,
которых нет в `edits.json`, не меняйте.
""" + PROMPT_RULES.format(tool="wiki")


def _strip(lines: list[str]) -> list[str]:
    out = [line.rstrip() for line in lines]
    while out and out[-1] == "":
        out.pop()
    return out


def _text(lines: list[str]) -> list[str]:
    """Non-blank lines: blank-line layout is not graded."""
    return [line.rstrip() for line in lines if line.strip()]


def check(result: Replay, ws: Path) -> str | None:
    ns = SERVICE.namespace()
    for name, page in result.state["pages"].items():
        final, shadow = _text(page["lines"]), _text(page["shadow"])
        mine = EDITS.get(name, [])
        for line in mine:
            if final.count(line) != 1:
                return f"page {name}: a line from edits.json appears {final.count(line)} times instead of once"
        if not mine:
            if final != shadow:
                return f"page {name} was changed although it is not in edits.json"
            continue
        start = final.index(mine[0])
        if final[start:start + len(mine)] != mine:
            return f"page {name}: the lines from edits.json are not together in the given order"
        rest = final[:start] + final[start + len(mine):]
        if rest != shadow:
            original = _text(ns["PAGES"][name])
            if len(rest) < len(shadow) and all(line in shadow for line in rest):
                return f"page {name} lost part of its text (or another participant's edits)"
            if any(line in original and line not in shadow for line in rest):
                return f"page {name}: another participant's edits were overwritten"
            return f"page {name} does not keep its existing text intact"
        if start < len(_text(ns["PAGES"][name])):
            return f"page {name}: the lines were not appended after the existing text"
    return None


def _read_full(ws: Path, page: str) -> tuple[list[str], int, bool]:
    """All lines of the page, the revision of its last chunk, whether chunks agreed."""
    lines, offset, revisions = [], 0, set()
    while True:
        args = ["get", "--page", page] + (["--offset", str(offset)] if offset else [])
        resp = call(ws, SERVICE, *args)[0]
        revisions.add(resp["revision"])
        lines += resp["content"].split("\n")[:-1]
        if not resp.get("truncated"):
            return lines, resp["revision"], len(revisions) == 1
        offset = resp["next_offset"]


def _write(ws: Path, page: str, lines: list[str]) -> str:
    rel = f"draft-{page}.md"
    (ws / rel).write_text("\n".join(_strip(lines)) + "\n", encoding="utf-8", newline="\n")
    return rel


def _save(ws: Path, page: str, lines: list[str], *, on_cut: str = "append") -> dict:
    """Save `lines`; when the body limit cuts the save, deal with the rest as `on_cut` says."""
    rel = _write(ws, page, lines)
    resp = call(ws, SERVICE, "save", "--page", page, "--file", rel)[0]
    while resp.get("applied") and resp.get("stored_lines") is not None and on_cut != "ignore":
        if on_cut == "append_all":
            return call(ws, SERVICE, "save", "--page", page, "--file", rel, "--append")[0]
        lines = lines[resp["stored_lines"]:]
        rel = _write(ws, page, lines)
        resp = call(ws, SERVICE, "save", "--page", page, "--file", rel, "--append")[0]
    return resp


def gold(ws: Path) -> None:
    edits = json.loads((ws / "edits.json").read_text(encoding="utf-8"))
    for page, new_lines in edits.items():
        while True:
            lines, _, consistent = _read_full(ws, page)
            if not consistent:
                continue
            if _save(ws, page, _strip(lines) + new_lines).get("applied"):
                break
    for page, new_lines in edits.items():
        lines, _, _ = _read_full(ws, page)
        assert all(_strip(lines).count(line) == 1 for line in new_lines), page
    for page in edits:
        (ws / f"draft-{page}.md").unlink()


def _nm(ws: Path, *, full: bool, on_stale: str, max_rounds: int = 10, on_cut: str = "append") -> None:
    edits = json.loads((ws / "edits.json").read_text(encoding="utf-8"))
    for page, new_lines in edits.items():
        for _ in range(max_rounds):
            if full:
                lines = _read_full(ws, page)[0]
            else:
                lines = call(ws, SERVICE, "get", "--page", page)[0]["content"].split("\n")
            resp = _save(ws, page, _strip(lines) + new_lines, on_cut=on_cut)
            rel = _write(ws, page, _strip(lines) + new_lines)
            if resp.get("applied") or on_stale == "ignore":
                break
            if on_stale == "force":
                call(ws, SERVICE, "save", "--page", page, "--file", rel, "--force")
                break
            if on_stale == "revision":
                call(ws, SERVICE, "save", "--page", page, "--file", rel, "--revision", str(resp["current_revision"]))
                break


def near_miss_literal_docs(ws: Path) -> None:
    _nm(ws, full=False, on_stale="ignore")


def near_miss_ignores_body_limit(ws: Path) -> None:
    """Handles stale saves and truncated reads, but not the save that stored only part of the page."""
    _nm(ws, full=True, on_stale="reread", on_cut="ignore")


def near_miss_appends_whole_file_after_cut(ws: Path) -> None:
    _nm(ws, full=True, on_stale="reread", on_cut="append_all")


def near_miss_force_on_stale(ws: Path) -> None:
    _nm(ws, full=True, on_stale="force")


def near_miss_resave_with_current_revision(ws: Path) -> None:
    _nm(ws, full=True, on_stale="revision")


def near_miss_first_chunk_only(ws: Path) -> None:
    _nm(ws, full=False, on_stale="reread")


def near_miss_ignores_applied_flag(ws: Path) -> None:
    _nm(ws, full=True, on_stale="ignore")


def near_miss_single_retry(ws: Path) -> None:
    _nm(ws, full=True, on_stale="reread", max_rounds=2)


NEAR_MISSES = [
    near_miss_literal_docs,
    near_miss_ignores_body_limit,
    near_miss_appends_whole_file_after_cut,
    near_miss_force_on_stale,
    near_miss_resave_with_current_revision,
    near_miss_first_chunk_only,
    near_miss_ignores_applied_flag,
    near_miss_single_retry,
]

TASK = reflect_task(
    id="reflect_09_wiki_optimistic_lock",
    name="Append to wiki pages under optimistic locking, truncated reads and a concurrent bot",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"edits.json": json.dumps(EDITS, ensure_ascii=False, indent=2) + "\n"},
    tags=("concurrency", "merge"),
)
