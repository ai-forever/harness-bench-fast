"""Fetch article metadata from a feed API that throttles and relocates articles.

Deviations from docs/feed.md (all visible in responses):
- More than 5 `get` requests within 10 ticks are answered `status: throttled` with
  `retry_after` and exit code 0 (docs: no rate limit). Asking again before
  `retry_after` has passed is a violation; the third violation suspends the key for
  good. The documented `wait --ticks N` (described as a generic pause) is the way out.
- Some ids answer `status: moved` with `location` (exit 0); one relocation leads to
  another relocation. The record must carry the requested id with the data found
  at the final location.
Hammering the throttled endpoint (the obvious retry loop) locks the agent out.
- Mid-course surprise: from the 16th served request the API falls back to a
  degraded index for 15 ticks: `status: ok`, but `author` and `words` are null,
  with `partial: true`, `missing` and `retry_after`. Records fetched in that window
  must be fetched again once it is over (still within the rate limit).
"""

from __future__ import annotations

import json
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

ADJ = ["Quiet", "Northern", "Hidden", "Broken", "Electric", "Slow", "Paper", "Salt", "Glass", "Last",
       "Winter", "Open", "Copper", "Lost", "Bright"]
NOUN = ["Harbour", "Ledger", "Circuit", "Orchard", "Archive", "Signal", "Bridge", "Market", "Garden",
        "Engine", "Border", "Library", "Tide", "Workshop", "Canal", "Factory", "Island"]
AUTHORS = ["M. de Vries", "L. Okafor", "S. Lindgren", "R. Havel", "T. Nakamura", "A. Moreau",
           "J. Kowalski", "E. Brandt", "K. Oduya", "P. Rossi"]

IDS = [f"a{1000 + (i * 389) % 4000}" for i in range(1, 31)]
# requested id -> new location (chains allowed)
MOVED = {IDS[3]: "a5101", IDS[8]: "a5102", IDS[13]: "a5103", "a5103": "a5104",
         IDS[19]: IDS[26], IDS[24]: "a5105"}


def _articles() -> dict[str, list]:
    out = {}
    for n, aid in enumerate(IDS + ["a5101", "a5102", "a5103", "a5104", "a5105"]):
        if aid in MOVED:
            continue
        num = int(aid[1:])
        out[aid] = [f"The {ADJ[num % len(ADJ)]} {NOUN[(num // 7 + n) % len(NOUN)]}",
                    AUTHORS[(num * 3 + n) % len(AUTHORS)], 400 + (num * 53) % 2600]
    return out


ARTICLES = _articles()

SOURCE = r'''
ARTICLES = __ARTICLES__
MOVED = __MOVED__
WINDOW = 10
CAP = 5
MAX_VIOLATIONS = 3
DEGRADE_AT = 16
DEGRADE_TICKS = 15


def initial_state():
    return {"served": [], "retry_at": 0, "violations": 0, "suspended": False, "served_total": 0,
            "degraded_until": -1}


def handle(state, words, opts, io):
    cmd = words[0] if words else ""
    clock = state["clock"]
    if cmd == "wait":
        try:
            ticks = int(str(opts.get("ticks")))
        except ValueError:
            return {"status": "error", "error": "--ticks must be an integer"}, 2
        if not 1 <= ticks <= 100:
            return {"status": "error", "error": "--ticks must be between 1 and 100"}, 2
        state["clock"] += ticks
        return {"status": "ok", "waited": ticks}, 0
    if cmd == "get":
        aid = opts.get("id")
        if not isinstance(aid, str):
            return {"status": "error", "error": "--id ID is required"}, 2
        if state["suspended"]:
            return {"status": "error", "error": "API key suspended for repeated rate-limit violations"}, 1
        if clock < state["retry_at"]:
            state["violations"] += 1
            if state["violations"] >= MAX_VIOLATIONS:
                state["suspended"] = True
                return {"status": "error", "error": "API key suspended for repeated rate-limit violations"}, 1
            return {"status": "throttled", "retry_after": state["retry_at"] - clock,
                    "message": "retried before retry_after: violation recorded "
                               f"({state['violations']} of {MAX_VIOLATIONS - 1} allowed before suspension)"}, 0
        recent = [t for t in state["served"] if t > clock - WINDOW]
        if len(recent) >= CAP:
            state["retry_at"] = min(recent) + WINDOW
            return {"status": "throttled", "retry_after": state["retry_at"] - clock,
                    "message": f"rate limit: {CAP} requests per {WINDOW} ticks. Requests before retry_after "
                               "count as violations; repeated violations suspend the key."}, 0
        state["served"] = recent + [clock]
        state["served_total"] += 1
        if state["served_total"] == DEGRADE_AT:
            state["degraded_until"] = clock + DEGRADE_TICKS
        if aid in MOVED:
            return {"status": "moved", "id": aid, "location": MOVED[aid], "message": "article moved permanently"}, 0
        if aid not in ARTICLES:
            return {"status": "not_found", "id": aid}, 4
        title, author, words_count = ARTICLES[aid]
        if clock <= state["degraded_until"]:
            return {"status": "ok", "article": {"id": aid, "title": title, "author": None, "words": None},
                    "partial": True, "missing": ["author", "words"], "retry_after": state["degraded_until"] - clock + 1,
                    "message": "served from the fallback index; the full record is available again after "
                               "retry_after ticks"}, 0
        return {"status": "ok", "article": {"id": aid, "title": title, "author": author, "words": words_count}}, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/feed.md"}, 2
'''.replace("__ARTICLES__", repr(ARTICLES)).replace("__MOVED__", repr(MOVED))

SERVICE = Service(tool="feed", title="the Kustpost article feed", source=SOURCE, key=b"reflect-04-feed-e25a")

DOCS = """
# feed — API статей

Клиент: `python3 tools/feed <команда> [--опция значение ...]`. Каждая команда печатает JSON.

| Команда | Что делает |
|---|---|
| `get --id ID` | Метаданные статьи. |
| `wait --ticks N` | Пауза на `N` тиков (1–100), например между повторными проверками. |

Ответ `get`:

```json
{"status": "ok", "article": {"id": "a1234", "title": "...", "author": "...", "words": 1520}}
```

Если статьи нет: `{"status": "not_found", "id": "..."}`, код выхода 4.

Ограничений на частоту запросов нет: `get` можно вызывать сколько угодно раз подряд.
"""

PROMPT = """
В файле `ids.txt` — 30 идентификаторов статей, по одному в строке. Соберите их
метаданные через API `feed` в файл `articles.jsonl` в корне рабочей папки: ровно 30 строк
в том же порядке, что в `ids.txt`, каждая строка — JSON-объект с полями `id` (идентификатор
ровно как в `ids.txt`), `title`, `author` (строки) и `words` (целое число) — значения
статьи из `feed`.
""" + PROMPT_RULES.format(tool="feed")


def _resolve(aid: str) -> list:
    while aid in MOVED:
        aid = MOVED[aid]
    return ARTICLES[aid]


def check(result: Replay, ws: Path) -> str | None:
    path = ws / "articles.jsonl"
    if not path.is_file():
        return "articles.jsonl is missing"
    lines = [ln for ln in path.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
    if len(lines) != len(IDS):
        return f"articles.jsonl has {len(lines)} lines, expected {len(IDS)}"
    for n, (line, aid) in enumerate(zip(lines, IDS, strict=True), 1):
        try:
            rec = json.loads(line)
        except ValueError:
            return f"line {n} of articles.jsonl is not JSON"
        if not isinstance(rec, dict) or rec.get("id") != aid:
            return f"line {n} of articles.jsonl does not carry the id from ids.txt"
        title, author, words = _resolve(aid)
        if rec.get("title") != title or rec.get("author") != author:
            return f"line {n} of articles.jsonl has wrong title or author"
        if rec.get("words") != words or isinstance(rec.get("words"), bool):
            return f"line {n} of articles.jsonl has a wrong word count"
    return None


def _ids(ws: Path) -> list[str]:
    return [ln.strip() for ln in (ws / "ids.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]


def _write(ws: Path, records: list[dict]) -> None:
    with (ws / "articles.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _fetch(ws: Path, aid: str, *, respect_wait: bool = True, follow: int = 99, refetch: bool = True) -> dict:
    cur, hops = aid, 0
    for _ in range(40):
        resp, _ = call(ws, SERVICE, "get", "--id", cur)
        if resp.get("status") == "throttled" or (refetch and resp.get("partial")):
            if respect_wait:
                call(ws, SERVICE, "wait", "--ticks", str(resp["retry_after"]))
            continue
        if resp.get("status") == "moved" and hops < follow:
            cur, hops = resp["location"], hops + 1
            continue
        return resp
    return {}


def _record(aid: str, resp: dict) -> dict:
    art = resp.get("article") or {}
    return {"id": aid, "title": art.get("title"), "author": art.get("author"), "words": art.get("words")}


def gold(ws: Path) -> None:
    records = []
    for aid in _ids(ws):
        resp = _fetch(ws, aid)
        assert resp.get("status") == "ok", resp
        records.append(_record(aid, resp))
    _write(ws, records)


def near_miss_no_replan_on_partial(ws: Path) -> None:
    _write(ws, [_record(aid, _fetch(ws, aid, refetch=False)) for aid in _ids(ws)])


def near_miss_literal(ws: Path) -> None:
    _write(ws, [_record(aid, call(ws, SERVICE, "get", "--id", aid)[0]) for aid in _ids(ws)])


def near_miss_hammers_on_throttle(ws: Path) -> None:
    _write(ws, [_record(aid, _fetch(ws, aid, respect_wait=False)) for aid in _ids(ws)])


def near_miss_ignores_moves(ws: Path) -> None:
    records = []
    for aid in _ids(ws):
        resp = _fetch(ws, aid, follow=0)
        if resp.get("status") == "ok":
            records.append(_record(aid, resp))
    _write(ws, records)


def near_miss_single_hop(ws: Path) -> None:
    _write(ws, [_record(aid, _fetch(ws, aid, follow=1)) for aid in _ids(ws)])


def near_miss_records_new_id(ws: Path) -> None:
    records = []
    for aid in _ids(ws):
        resp = _fetch(ws, aid)
        records.append(_record(resp["article"]["id"], resp))
    _write(ws, records)


NEAR_MISSES = [
    near_miss_literal,
    near_miss_hammers_on_throttle,
    near_miss_ignores_moves,
    near_miss_single_hop,
    near_miss_records_new_id,
    near_miss_no_replan_on_partial,
]

TASK = reflect_task(
    id="reflect_04_api_throttle",
    name="Collect article metadata from a feed that throttles and relocates articles",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"ids.txt": "".join(f"{aid}\n" for aid in IDS)},
    tags=("rate-limit", "redirects"),
)
