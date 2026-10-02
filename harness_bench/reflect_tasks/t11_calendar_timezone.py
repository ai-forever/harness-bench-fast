"""Book meetings given in Berlin local time across the October DST switch.

Deviations from docs/cal.md (all visible in responses):
- `--start` without an offset is read as UTC, not as Europe/Berlin local time; every
  event in a response shows both `start_utc` and `start_local`.
- The calendar has a write quota (`writes_remaining` in every write response, 15 at
  the start; refused writes count too). Once it is spent, writes are refused.
- `list` without `--from/--to` covers only the next 7 days (`window` in the answer),
  not all future events.
- Busy, past and otherwise impossible slots are all refused with the same
  `slot unavailable`.
The mid-course surprise: the offset learned from the first bookings (+2 h, CEST)
stops being right on 25 October (CET, +1 h). An agent that books everything with the
first offset and repairs afterwards runs out of writes; each booking has to be
checked against `start_local` as it is made.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

# title, local date, local time, minutes; Europe/Berlin
MEETINGS = [
    ("Планирование спринта", "2026-10-21", "10:00", 60),
    ("Созвон с поставщиком", "2026-10-21", "14:30", 30),
    ("Ревью архитектуры", "2026-10-22", "09:00", 60),
    ("Интервью: бэкенд", "2026-10-22", "11:00", 45),
    ("Демо для клиента", "2026-10-23", "13:00", 60),
    ("Проверка релиза", "2026-10-25", "10:00", 30),
    ("Планёрка", "2026-10-26", "09:30", 60),
    ("1:1 с руководителем", "2026-10-27", "16:00", 60),
    ("Воркшоп по данным", "2026-10-28", "10:00", 90),
    ("Ретро", "2026-10-29", "12:00", 60),
    ("Итоги месяца", "2026-10-30", "15:00", 60),
]

SOURCE = r'''
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc)
QUOTA = 15
FOREIGN = [
    ("Бюджетный комитет", "2026-10-23T13:00Z", 60),
    ("Обучение по безопасности", "2026-10-27T13:30Z", 60),
    ("Звонок с аудитором", "2026-10-28T13:00Z", 60),
    ("Стендап отдела продаж", "2026-10-29T09:00Z", 60),
    ("Квартальный отчёт", "2026-11-03T10:00Z", 120),
]


def last_sunday(year, month):
    day = datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(days=1)
    return day - timedelta(days=(day.weekday() + 1) % 7)


def berlin_offset(utc):
    start = last_sunday(utc.year, 3).replace(hour=1)
    end = last_sunday(utc.year, 10).replace(hour=1)
    return 2 if start <= utc < end else 1


def fmt_utc(dt):
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def fmt_local(dt):
    off = berlin_offset(dt)
    return (dt + timedelta(hours=off)).strftime("%Y-%m-%d %H:%M") + (" CEST" if off == 2 else " CET")


def parse(text):
    """`YYYY-MM-DDTHH:MM` with an optional `Z` or `+HH:MM`/`-HH:MM`; no offset means UTC."""
    if not isinstance(text, str):
        return None
    text = text.strip().replace(" ", "T")
    offset = timedelta(0)
    if text.endswith("Z"):
        text = text[:-1]
    elif len(text) > 16 and text[16] in "+-":
        sign = 1 if text[16] == "+" else -1
        try:
            hh, mm = text[17:].split(":")
            offset = sign * timedelta(hours=int(hh), minutes=int(mm))
        except ValueError:
            return None
        text = text[:16]
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc) - offset
    except ValueError:
        return None


def parse_day(text):
    return parse(text + "T00:00" if len(text) == 10 else text)


def initial_state():
    events = []
    for n, (title, start, minutes) in enumerate(FOREIGN):
        events.append({"id": f"EV-{101 + n}", "title": title, "start": start, "minutes": minutes,
                       "owner": "shared", "status": "confirmed"})
    return {"events": events, "next": 101 + len(FOREIGN), "writes": 0}


def view(e):
    start = parse(e["start"])
    return {"id": e["id"], "title": e["title"], "start_utc": fmt_utc(start), "start_local": fmt_local(start),
            "end_utc": fmt_utc(start + timedelta(minutes=e["minutes"])), "duration": e["minutes"]}


def live(state):
    return [e for e in state["events"] if e["status"] == "confirmed"]


def free(state, start, minutes, skip=None):
    if start < NOW:
        return False
    end = start + timedelta(minutes=minutes)
    for e in live(state):
        if e["id"] == skip:
            continue
        s = parse(e["start"])
        if start < s + timedelta(minutes=e["minutes"]) and s < end:
            return False
    return True


def opt_text(opts, key):
    val = opts.get(key)
    if isinstance(val, list):
        val = val[-1]
    return val if isinstance(val, str) and val.strip() else None


def find(state, eid):
    for e in live(state):
        if e["id"] == eid:
            return e
    return None


def write(state, resp, code):
    resp["writes_remaining"] = QUOTA - state["writes"]
    return resp, code


def handle(state, words, opts, io):
    cmd = words[0] if words else ""
    if cmd == "list":
        lo = parse_day(opt_text(opts, "from")) if opt_text(opts, "from") else NOW
        hi = parse_day(opt_text(opts, "to")) if opt_text(opts, "to") else None
        if hi is None and lo is not None and not opt_text(opts, "to"):
            hi = lo + timedelta(days=7)
        if lo is None or hi is None:
            return {"status": "error", "error": "bad --from/--to, expected YYYY-MM-DD or YYYY-MM-DDTHH:MM"}, 2
        shown = sorted((e for e in live(state) if lo <= parse(e["start"]) < hi), key=lambda e: parse(e["start"]))
        return {"status": "ok", "window": {"from": fmt_utc(lo), "to": fmt_utc(hi)},
                "events": [view(e) for e in shown]}, 0
    if cmd == "get":
        e = find(state, opt_text(opts, "id"))
        if e is None:
            return {"status": "error", "error": "no such event"}, 3
        return {"status": "ok", "event": view(e)}, 0
    if cmd in ("book", "move", "cancel"):
        if state["writes"] >= QUOTA:
            return write(state, {"status": "refused", "error": "write quota for this calendar is exhausted"}, 3)
        if cmd == "cancel":
            e = find(state, opt_text(opts, "id"))
            if e is None:
                return {"status": "error", "error": "no such event"}, 3
            state["writes"] += 1
            e["status"] = "cancelled"
            return write(state, {"status": "ok", "cancelled": e["id"]}, 0)
        start = parse(opt_text(opts, "start"))
        if start is None:
            return {"status": "error", "error": "bad --start, expected YYYY-MM-DDTHH:MM"}, 2
        if cmd == "move":
            e = find(state, opt_text(opts, "id"))
            if e is None:
                return {"status": "error", "error": "no such event"}, 3
            state["writes"] += 1
            if not free(state, start, e["minutes"], skip=e["id"]):
                return write(state, {"status": "refused", "error": "slot unavailable"}, 3)
            e["start"] = fmt_utc(start)
            return write(state, {"status": "ok", "event": view(e)}, 0)
        title = opt_text(opts, "title")
        try:
            minutes = int(opt_text(opts, "duration") or "")
        except ValueError:
            return {"status": "error", "error": "--title and --duration (minutes) are required"}, 2
        if title is None or not 5 <= minutes <= 480:
            return {"status": "error", "error": "--title and --duration (minutes) are required"}, 2
        state["writes"] += 1
        if not free(state, start, minutes):
            return write(state, {"status": "refused", "error": "slot unavailable"}, 3)
        e = {"id": f"EV-{state['next']}", "title": title, "start": fmt_utc(start), "minutes": minutes,
             "owner": "you", "status": "confirmed"}
        state["next"] += 1
        state["events"].append(e)
        return write(state, {"status": "ok", "event": view(e)}, 0)
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/cal.md"}, 2
'''

SERVICE = Service(tool="cal", title="the Kanaal team calendar", source=SOURCE, key=b"reflect-11-cal-9a14")

DOCS = """
# cal — календарь команды

Клиент: `python3 tools/cal <команда> [--опция значение ...]`. Каждая команда печатает JSON.
Календарь ведётся в часовом поясе Europe/Berlin; время во всех опциях — местное.

| Команда | Что делает |
|---|---|
| `list [--from YYYY-MM-DD] [--to YYYY-MM-DD]` | События календаря. Без `--from/--to` — все будущие события. |
| `get --id EV-NNN` | Одно событие. |
| `book --title T --start YYYY-MM-DDTHH:MM --duration MIN` | Создаёт событие с началом `--start` (местное время) длительностью `MIN` минут. |
| `move --id EV-NNN --start YYYY-MM-DDTHH:MM` | Переносит событие на новое начало (местное время), длительность сохраняется. |
| `cancel --id EV-NNN` | Отменяет событие. |

Пересекающиеся события календарь не допускает: такое бронирование отклоняется.

Коды выхода: `0` — успех, `2` — ошибка в аргументах, `3` — отказ (событие не найдено,
слот недоступен).
"""

PROMPT = """
Забронируйте в календаре `cal` встречи из `meetings.csv`. Время в файле — местное
время Берлина (Europe/Berlin) на дату встречи; обратите внимание, что в ночь на
25 октября 2026 Европа переходит с летнего времени на зимнее. Каждая встреча должна
оказаться в календаре ровно одним событием с тем же названием, началом в нужный момент
времени и указанной длительностью. Ошибочно созданных событий остаться не должно;
чужие события календаря не трогайте.
""" + PROMPT_RULES.format(tool="cal")


def _berlin_to_utc(date: str, time: str) -> datetime:
    """Europe/Berlin wall time to UTC for 2026 (DST from 29 March to 25 October, 01:00 UTC)."""
    naive = datetime.strptime(f"{date}T{time}", "%Y-%m-%dT%H:%M").replace(tzinfo=UTC)
    summer = naive - timedelta(hours=2)
    if datetime(2026, 3, 29, 1, tzinfo=UTC) <= summer < datetime(2026, 10, 25, 1, tzinfo=UTC):
        return summer
    return naive - timedelta(hours=1)


def check(result: Replay, ws: Path) -> str | None:
    events = result.state["events"]
    initial = {e["id"]: e for e in SERVICE.initial_state()["events"]}
    for eid, orig in initial.items():
        now = next(e for e in events if e["id"] == eid)
        if now != orig:
            return f"the shared event {orig['title']!r} was changed or cancelled"
    mine = [e for e in events if e["owner"] == "you" and e["status"] == "confirmed"]
    for title, date, time, minutes in MEETINGS:
        hits = [e for e in mine if e["title"] == title]
        if len(hits) != 1:
            return f"meeting {title!r} has {len(hits)} events instead of exactly one"
        want = _berlin_to_utc(date, time).strftime("%Y-%m-%dT%H:%MZ")
        if hits[0]["start"] != want:
            return f"meeting {title!r} starts at the wrong moment"
        if hits[0]["minutes"] != minutes:
            return f"meeting {title!r} has the wrong duration"
    extra = [e for e in mine if e["title"] not in {m[0] for m in MEETINGS}]
    if extra:
        return f"{len(extra)} stray event(s) left in the calendar"
    return None


def _meetings(ws: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO((ws / "meetings.csv").read_text(encoding="utf-8"))))


def _local(resp: dict) -> str:
    return resp["event"]["start_local"][:16]


def gold(ws: Path) -> None:
    call(ws, SERVICE, "list", "--from", "2026-10-21", "--to", "2026-11-01")
    offset: timedelta | None = None
    for m in _meetings(ws):
        wall = datetime.strptime(f"{m['date']}T{m['time']}", "%Y-%m-%dT%H:%M")
        want = wall.strftime("%Y-%m-%d %H:%M")
        if offset is None:
            # Probe: book as documented and read back how the calendar placed it.
            resp, code = call(ws, SERVICE, "book", "--title", m["title"], "--start", wall.strftime("%Y-%m-%dT%H:%M"),
                              "--duration", m["minutes"])
        else:
            start = (wall - offset).strftime("%Y-%m-%dT%H:%MZ")
            resp, code = call(ws, SERVICE, "book", "--title", m["title"], "--start", start, "--duration", m["minutes"])
        assert code == 0, resp
        if _local(resp) != want:
            placed_utc = datetime.strptime(resp["event"]["start_utc"], "%Y-%m-%dT%H:%MZ")
            shift = datetime.strptime(_local(resp), "%Y-%m-%d %H:%M") - placed_utc
            offset = shift
            fixed = (wall - offset).strftime("%Y-%m-%dT%H:%MZ")
            resp, code = call(ws, SERVICE, "move", "--id", resp["event"]["id"], "--start", fixed)
            assert code == 0 and _local(resp) == want, resp
        elif offset is None:
            offset = timedelta(0)
    listed = call(ws, SERVICE, "list", "--from", "2026-10-21", "--to", "2026-11-01")[0]["events"]
    assert sum(e["title"] in {m["title"] for m in _meetings(ws)} for e in listed) == len(MEETINGS)


def _book_all(ws: Path, start_of) -> dict[str, dict]:
    booked = {}
    for m in _meetings(ws):
        resp, code = call(ws, SERVICE, "book", "--title", m["title"], "--start", start_of(m), "--duration", m["minutes"])
        if code == 0:
            booked[m["title"]] = resp["event"]
    return booked


def near_miss_literal_docs(ws: Path) -> None:
    _book_all(ws, lambda m: f"{m['date']}T{m['time']}")


def _minus(m: dict[str, str], hours: int) -> str:
    wall = datetime.strptime(f"{m['date']}T{m['time']}", "%Y-%m-%dT%H:%M")
    return (wall - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%MZ")


def near_miss_fixed_offset(ws: Path) -> None:
    """Learns +2 h from the first bookings and applies it to every meeting."""
    _book_all(ws, lambda m: _minus(m, 2))


def near_miss_fixed_offset_then_repair(ws: Path) -> None:
    """Books everything with +2 h, then repairs from a full listing: runs out of writes."""
    booked = _book_all(ws, lambda m: _minus(m, 2))
    for m in _meetings(ws):
        want = _berlin_to_utc(m["date"], m["time"]).strftime("%Y-%m-%dT%H:%MZ")
        if m["title"] in booked and booked[m["title"]]["start_utc"] != want:
            call(ws, SERVICE, "move", "--id", booked[m["title"]]["id"], "--start", want)
        elif m["title"] not in booked:
            call(ws, SERVICE, "book", "--title", m["title"], "--start", want, "--duration", m["minutes"])


def near_miss_rebook_without_cancel(ws: Path) -> None:
    _book_all(ws, lambda m: f"{m['date']}T{m['time']}")
    _book_all(ws, lambda m: _berlin_to_utc(m["date"], m["time"]).strftime("%Y-%m-%dT%H:%MZ"))


def near_miss_cancel_listed_then_rebook(ws: Path) -> None:
    _book_all(ws, lambda m: f"{m['date']}T{m['time']}")
    for e in call(ws, SERVICE, "list")[0]["events"]:
        if e["title"] in {m[0] for m in MEETINGS}:
            call(ws, SERVICE, "cancel", "--id", e["id"])
    _book_all(ws, lambda m: _berlin_to_utc(m["date"], m["time"]).strftime("%Y-%m-%dT%H:%MZ"))


NEAR_MISSES = [
    near_miss_literal_docs,
    near_miss_fixed_offset,
    near_miss_fixed_offset_then_repair,
    near_miss_rebook_without_cancel,
    near_miss_cancel_listed_then_rebook,
]

TASK = reflect_task(
    id="task_422_calendar_timezone",
    name="Book local-time meetings across a DST switch through a UTC-only, write-limited calendar",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"meetings.csv": "title,date,time,minutes\n"
                 + "".join(f"{t},{d},{h},{n}\n" for t, d, h, n in MEETINGS)},
    tags=("time", "budget"),
)
