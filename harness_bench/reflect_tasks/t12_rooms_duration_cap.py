"""Book workshop rooms through a service that silently caps every reservation at an hour.

Deviations from docs/rooms.md (all visible in responses):
- `reserve --duration N` answers `ok` but books at most 60 minutes; the response
  carries the real `end` and a `warnings` entry. Longer workshops have to be
  chained from consecutive reservations in one room.
- Not every room has a projector: `rooms` lists an `equipment` field the docs never
  mention, while the docs claim every room is equipped.
- Mid-course surprise: right after W2 is fully booked in its preferred room, the next
  call carries a `notices` entry: facilities reclaimed the second half of that slot for
  a priority booking and released our piece (`reservations` shows it as released). The
  docs say nobody but us can cancel our reservations.
Trap: in five preferred rooms the slot is free at the start of the workshop but taken
later on, so the second or third chained piece is refused. Booking the remainder in
another room splits the workshop; moving it needs cancelling the pieces already held
and a room that is free for the whole span, fits the group and has a projector when
one is needed (the free big rooms have none). The released half-hour of W2 invites the
same wrong fix: booking just the hole elsewhere splits W2; it has to move as a whole.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

WORKSHOPS = [
    # id, date, start, duration, attendees, projector, preferred room
    ("W1", "2026-10-12", "09:00", 120, 10, "yes", "atlas"),
    ("W2", "2026-10-12", "11:30", 90, 18, "yes", "borealis"),
    ("W3", "2026-10-12", "13:30", 180, 18, "yes", "ember"),
    ("W4", "2026-10-12", "17:00", 90, 25, "no", "cobalt"),
    ("W5", "2026-10-13", "08:30", 150, 7, "yes", "delta"),
    ("W6", "2026-10-13", "11:15", 120, 14, "yes", "fjord"),
    ("W7", "2026-10-13", "14:00", 90, 28, "no", "cobalt"),
    ("W8", "2026-10-13", "15:45", 165, 12, "yes", "atlas"),
]

TITLES = {
    "W1": "Введение в SQL", "W2": "Дизайн-ревью", "W3": "Хакатон по отчётности",
    "W4": "Планирование квартала", "W5": "Онбординг аналитиков", "W6": "Разбор инцидентов",
    "W7": "Общее собрание отдела", "W8": "Практикум по A/B-тестам",
}


def _workshops_csv() -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["id", "title", "date", "start", "duration_min", "attendees", "projector", "preferred_room"])
    for wid, date, start, dur, people, proj, room in WORKSHOPS:
        writer.writerow([wid, TITLES[wid], date, start, dur, people, proj, room])
    return buf.getvalue()


SOURCE = r'''
ROOMS = [
    {"id": "atlas", "capacity": 12, "floor": 2, "equipment": ["projector", "whiteboard"]},
    {"id": "borealis", "capacity": 20, "floor": 2, "equipment": ["projector"]},
    {"id": "cobalt", "capacity": 30, "floor": 3, "equipment": ["whiteboard"]},
    {"id": "delta", "capacity": 8, "floor": 3, "equipment": ["projector", "whiteboard"]},
    {"id": "ember", "capacity": 24, "floor": 4, "equipment": ["projector", "whiteboard"]},
    {"id": "fjord", "capacity": 16, "floor": 4, "equipment": ["projector"]},
    {"id": "gale", "capacity": 20, "floor": 5, "equipment": ["projector", "whiteboard"]},
    {"id": "harbor", "capacity": 40, "floor": 1, "equipment": ["whiteboard", "stage"]},
]
DATES = ["2026-10-12", "2026-10-13", "2026-10-14"]
OPEN, CLOSE, MAX_LEN = 8 * 60, 20 * 60, 60
BUMP = ("2026-10-12", "borealis", "12:30", "13:00", "Executive briefing")
WATCH = ("2026-10-12", "borealis", "11:30", "13:00")
OTHERS = [
    ("2026-10-12", "atlas", "10:30", "11:30", "Sales sync"),
    ("2026-10-12", "borealis", "08:00", "09:30", "Standup"),
    ("2026-10-12", "fjord", "10:00", "10:30", "Interview"),
    ("2026-10-12", "gale", "09:30", "10:00", "Interview"),
    ("2026-10-12", "delta", "13:00", "14:00", "1:1"),
    ("2026-10-12", "ember", "15:30", "16:00", "Vendor call"),
    ("2026-10-12", "borealis", "15:00", "15:30", "Budget review"),
    ("2026-10-12", "fjord", "14:00", "15:00", "Training"),
    ("2026-10-12", "cobalt", "18:00", "19:00", "Board prep"),
    ("2026-10-12", "harbor", "09:00", "12:00", "All hands"),
    ("2026-10-13", "delta", "10:00", "10:15", "Cleaning"),
    ("2026-10-13", "atlas", "08:00", "09:00", "Standup"),
    ("2026-10-13", "borealis", "08:30", "09:00", "Interview"),
    ("2026-10-13", "gale", "10:30", "11:00", "Interview"),
    ("2026-10-13", "cobalt", "09:00", "11:00", "Offsite prep"),
    ("2026-10-13", "atlas", "17:45", "18:15", "Client call"),
    ("2026-10-13", "borealis", "16:00", "16:30", "Retro"),
    ("2026-10-13", "fjord", "17:00", "18:00", "Training"),
    ("2026-10-13", "ember", "12:00", "19:00", "Workshop (external)"),
]


def mins(text):
    try:
        hh, mm = str(text).split(":")
        value = int(hh) * 60 + int(mm)
    except ValueError:
        return None
    if len(str(text).split(":")[1]) != 2:
        return None
    return value


def hhmm(value):
    return f"{value // 60:02d}:{value % 60:02d}"


def initial_state():
    bookings = []
    for n, (date, room, start, end, title) in enumerate(OTHERS, 1):
        bookings.append({"id": f"X-{n:03d}", "owner": "other", "room": room, "date": date,
                         "start": mins(start), "end": mins(end), "title": title, "active": True})
    return {"bookings": bookings, "seq": 0, "bump": {"armed": None, "done": False},
            "rooms": {r["id"]: {"capacity": r["capacity"], "projector": "projector" in r["equipment"]} for r in ROOMS}}


def room_of(rid):
    for room in ROOMS:
        if room["id"] == rid:
            return room
    return None


def busy(state, room, date):
    return sorted((b["start"], b["end"]) for b in state["bookings"]
                  if b["active"] and b["room"] == room and b["date"] == date)


def free_intervals(state, room, date):
    out, cur = [], OPEN
    for start, end in busy(state, room, date):
        if start > cur:
            out.append({"start": hhmm(cur), "end": hhmm(start)})
        cur = max(cur, end)
    if cur < CLOSE:
        out.append({"start": hhmm(cur), "end": hhmm(CLOSE)})
    return out


def show(b):
    return {"id": b["id"], "room": b["room"], "date": b["date"], "start": hhmm(b["start"]),
            "end": hhmm(b["end"]), "duration_min": b["end"] - b["start"], "title": b.get("title", "")}


def covered(state, room, date, lo, hi):
    return sum(min(b["end"], hi) - max(b["start"], lo) for b in state["bookings"]
               if b["owner"] == "me" and b["active"] and b["room"] == room and b["date"] == date
               and b["start"] < hi and lo < b["end"])


def facilities(state):
    """Once W2's slot in borealis is fully held, the next call loses part of it to a priority booking."""
    bump = state["bump"]
    if bump["done"] or bump["armed"] is None or state["clock"] <= bump["armed"]:
        return None
    bump["done"] = True
    lo, hi = mins(BUMP[2]), mins(BUMP[3])
    lost = []
    for b in state["bookings"]:
        if b["owner"] == "me" and b["active"] and b["room"] == BUMP[1] and b["date"] == BUMP[0] \
                and b["start"] < hi and lo < b["end"]:
            b["active"] = False
            b["released"] = True
            lost.append(b["id"])
    state["bookings"].append({"id": "X-900", "owner": "other", "room": BUMP[1], "date": BUMP[0],
                              "start": lo, "end": hi, "title": BUMP[4], "active": True})
    return (f"facilities released your reservation(s) {', '.join(lost)}: room {BUMP[1]} on {BUMP[0]} "
            f"{BUMP[2]}-{BUMP[3]} was reclaimed for a priority booking")


def handle(state, words, opts, io):
    notice = facilities(state)
    resp, code = serve(state, words, opts)
    if notice:
        resp["notices"] = [notice]
    return resp, code


def serve(state, words, opts):
    cmd = words[0] if words else ""
    if cmd == "rooms":
        return {"status": "ok", "rooms": [dict(r) for r in ROOMS]}, 0
    if cmd == "free":
        date = opts.get("date")
        if date not in DATES:
            return {"status": "error", "error": f"unknown date {date!r}; bookable dates: {', '.join(DATES)}"}, 2
        rid = opts.get("room")
        if rid is not None and room_of(rid) is None:
            return {"status": "error", "error": f"unknown room {rid!r}"}, 2
        rooms = [rid] if rid is not None else [r["id"] for r in ROOMS]
        return {"status": "ok", "date": date,
                "rooms": [{"room": r, "free": free_intervals(state, r, date)} for r in rooms]}, 0
    if cmd == "reserve":
        rid, date = opts.get("room"), opts.get("date")
        room = room_of(rid)
        if room is None:
            return {"status": "error", "error": f"unknown room {rid!r}"}, 2
        if date not in DATES:
            return {"status": "error", "error": f"unknown date {date!r}; bookable dates: {', '.join(DATES)}"}, 2
        start = mins(opts.get("start", ""))
        if start is None or start % 5:
            return {"status": "error", "error": "--start must be HH:MM on a 5-minute step"}, 2
        try:
            dur = int(opts.get("duration", ""))
            people = int(opts.get("attendees", ""))
        except (TypeError, ValueError):
            return {"status": "error", "error": "--duration and --attendees must be whole numbers"}, 2
        if dur <= 0 or dur > 480 or people <= 0:
            return {"status": "error", "error": "--duration must be 1..480 minutes, --attendees positive"}, 2
        if people > room["capacity"]:
            return {"status": "refused", "error": f"room {rid} seats {room['capacity']} people"}, 3
        booked = min(dur, MAX_LEN)
        end = start + booked
        if start < OPEN or end > CLOSE:
            return {"status": "refused", "error": "rooms are bookable 08:00-20:00"}, 3
        for s, e in busy(state, rid, date):
            if s < end and start < e:
                return {"status": "refused", "error": "room is already booked for part of this interval",
                        "conflict": {"start": hhmm(s), "end": hhmm(e)}}, 3
        state["seq"] += 1
        b = {"id": f"R-{state['seq']:04d}", "owner": "me", "room": rid, "date": date, "start": start, "end": end,
             "title": str(opts.get("title", "")), "attendees": people, "active": True}
        state["bookings"].append(b)
        resp = {"status": "ok", "reservation": show(b)}
        lo, hi = mins(WATCH[2]), mins(WATCH[3])
        if state["bump"]["armed"] is None and covered(state, WATCH[1], WATCH[0], lo, hi) == hi - lo:
            state["bump"]["armed"] = state["clock"]
        if booked < dur:
            resp["warnings"] = [f"reservation shortened to {MAX_LEN} minutes (longest single reservation)"]
        return resp, 0
    if cmd == "reservations":
        date = opts.get("date")
        mine = [dict(show(b), state="active" if b["active"] else "released by facilities")
                for b in state["bookings"] if b["owner"] == "me" and (b["active"] or b.get("released"))
                and (date is None or b["date"] == date)]
        return {"status": "ok", "reservations": sorted(mine, key=lambda r: (r["date"], r["start"], r["room"]))}, 0
    if cmd == "cancel":
        rid = opts.get("id")
        for b in state["bookings"]:
            if b["id"] == rid and b["active"]:
                if b["owner"] != "me":
                    return {"status": "refused", "error": "you can cancel only your own reservations"}, 3
                b["active"] = False
                return {"status": "ok", "cancelled": rid}, 0
        return {"status": "error", "error": "no active reservation with that id"}, 2
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/rooms.md"}, 2
'''

SERVICE = Service(tool="rooms", title="the office room booking service", source=SOURCE, key=b"reflect-12-rooms-4c1e")

DOCS = """
# rooms — бронирование переговорных

Клиент: `python3 tools/rooms <команда> [--опция значение ...]`. Каждая команда печатает JSON.
Время — местное, в формате `HH:MM`, шаг 5 минут. Переговорные бронируются с 08:00 до 20:00.

| Команда | Что делает |
|---|---|
| `rooms` | Список переговорных: `id`, `capacity` (мест), `floor`. |
| `free --date YYYY-MM-DD [--room ID]` | Свободные интервалы на дату: по одной комнате или по всем. |
| `reserve --room ID --date YYYY-MM-DD --start HH:MM --duration MIN --attendees N [--title TEXT]` | Бронь на `MIN` минут (до 480) с `--start`. Ответ — созданная бронь с `id`, `start`, `end`. |
| `reservations [--date YYYY-MM-DD]` | Ваши действующие брони. |
| `cancel --id ID` | Отмена вашей брони. |

Правила:

- Одна бронь покрывает весь запрошенный интервал: мероприятие любой длины до 8 часов
  бронируется одной командой `reserve`.
- Если комната занята хотя бы на часть интервала, `reserve` отказывает (`status: refused`,
  код выхода 3) и показывает мешающую бронь в `conflict`. Если `--attendees` больше
  `capacity`, отказ с тем же кодом.
- Все переговорные оборудованы проектором и экраном, так что выбирать комнату нужно только
  по вместимости.
- Брони других сотрудников видны в `free` как занятое время; отменять их нельзя. Ваши
  брони тоже никто, кроме вас, не отменит и не изменит.
"""

PROMPT = """
Забронируйте переговорные для воркшопов из `workshops.csv` (дата, начало, длительность в
минутах, число участников, нужен ли проектор, предпочтительная комната).

Требования:
- каждый воркшоп целиком, от начала до конца без разрывов, проходит в одной комнате;
- комната вмещает всех участников, а если в колонке `projector` стоит `yes`, в ней есть проектор;
- если предпочтительная комната подходит и свободна на весь интервал воркшопа, используйте её,
  иначе выберите любую другую подходящую;
- в итоге у вас не должно остаться никаких других броней, кроме покрывающих воркшопы
  (лишние или ошибочные брони отмените).
""" + PROMPT_RULES.format(tool="rooms")


def _mins(text: str) -> int:
    hh, mm = text.split(":")
    return int(hh) * 60 + int(mm)


def check(result: Replay, ws: Path) -> str | None:
    state = result.state
    rooms = state["rooms"]
    mine = [b for b in state["bookings"] if b["owner"] == "me" and b["active"]]
    others = [b for b in state["bookings"] if b["owner"] != "me" and b["active"]]
    used: set[str] = set()
    for wid, date, start, dur, people, proj, pref in WORKSHOPS:
        lo, hi = _mins(start), _mins(start) + dur
        parts = [b for b in mine if b["date"] == date and b["start"] < hi and lo < b["end"]]
        if not parts:
            return f"workshop {wid} has no reservation"
        used.update(b["id"] for b in parts)
        if any(b["start"] < lo or b["end"] > hi for b in parts):
            return f"a reservation for workshop {wid} runs outside the workshop time"
        if len({b["room"] for b in parts}) > 1:
            return f"workshop {wid} is split across several rooms"
        room = parts[0]["room"]
        if sum(b["end"] - b["start"] for b in parts) != dur:
            return f"workshop {wid} is not covered for its full duration"
        if rooms[room]["capacity"] < people:
            return f"the room booked for workshop {wid} is too small"
        if proj == "yes" and not rooms[room]["projector"]:
            return f"the room booked for workshop {wid} has no projector"
        pref_ok = (rooms[pref]["capacity"] >= people and (proj != "yes" or rooms[pref]["projector"])
                   and not any(b["room"] == pref and b["date"] == date and b["start"] < hi and lo < b["end"]
                               for b in others))
        if pref_ok and room != pref:
            return f"workshop {wid} was moved although its preferred room was free and suitable"
    stray = [b for b in mine if b["id"] not in used]
    if stray:
        return f"{len(stray)} reservation(s) do not belong to any workshop"
    return None


def _read_workshops(ws: Path) -> list[dict[str, str]]:
    with (ws / "workshops.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _rooms(ws: Path) -> list[dict]:
    return call(ws, SERVICE, "rooms")[0]["rooms"]


def _covers(free: list[dict], lo: int, hi: int) -> bool:
    return any(_mins(f["start"]) <= lo and hi <= _mins(f["end"]) for f in free)


def _chain(ws: Path, room: str, w: dict[str, str]) -> tuple[list[str], bool]:
    """Book consecutive pieces until the workshop is covered; returns ids and success."""
    lo, hi = _mins(w["start"]), _mins(w["start"]) + int(w["duration_min"])
    ids, cur = [], lo
    while cur < hi:
        resp, code = call(ws, SERVICE, "reserve", "--room", room, "--date", w["date"],
                          "--start", f"{cur // 60:02d}:{cur % 60:02d}", "--duration", str(hi - cur),
                          "--attendees", w["attendees"], "--title", w["id"])
        if code != 0:
            return ids, False
        ids.append(resp["reservation"]["id"])
        cur = _mins(resp["reservation"]["end"])
    return ids, True


def gold(ws: Path) -> None:
    rooms = _rooms(ws)
    for w in _read_workshops(ws):
        lo, hi = _mins(w["start"]), _mins(w["start"]) + int(w["duration_min"])
        people, need_proj = int(w["attendees"]), w["projector"] == "yes"
        fits = [r["id"] for r in rooms if r["capacity"] >= people
                and (not need_proj or "projector" in r.get("equipment", []))]
        free = {r["room"]: r["free"] for r in call(ws, SERVICE, "free", "--date", w["date"])[0]["rooms"]}
        order = [w["preferred_room"]] + [r for r in fits if r != w["preferred_room"]]
        room = next(r for r in order if r in fits and _covers(free[r], lo, hi))
        ids, ok = _chain(ws, room, w)
        assert ok, (w, room, ids)
    _repair_released(ws, rooms)
    left = call(ws, SERVICE, "reservations")[0]["reservations"]
    assert sum(r["state"] == "active" for r in left) == 19, left


def _repair_released(ws: Path, rooms: list[dict]) -> None:
    """A released piece leaves a hole: move the whole workshop to a room free for its full span."""
    listed = call(ws, SERVICE, "reservations")[0]["reservations"]
    for w in _read_workshops(ws):
        lo, hi = _mins(w["start"]), _mins(w["start"]) + int(w["duration_min"])
        mine = [r for r in listed if r["date"] == w["date"] and _mins(r["start"]) < hi and lo < _mins(r["end"])]
        if all(r["state"] == "active" for r in mine):
            continue
        for r in mine:
            if r["state"] == "active":
                call(ws, SERVICE, "cancel", "--id", r["id"])
        people, need_proj = int(w["attendees"]), w["projector"] == "yes"
        free = {r["room"]: r["free"] for r in call(ws, SERVICE, "free", "--date", w["date"])[0]["rooms"]}
        room = next(r["id"] for r in rooms if r["capacity"] >= people
                    and (not need_proj or "projector" in r["equipment"]) and _covers(free[r["id"]], lo, hi))
        ids, ok = _chain(ws, room, w)
        assert ok, (w, room, ids)


def near_miss_no_replan_after_release(ws: Path) -> None:
    """Solves everything up front but never notices that facilities released a piece."""
    rooms = _rooms(ws)
    for w in _read_workshops(ws):
        lo, hi = _mins(w["start"]), _mins(w["start"]) + int(w["duration_min"])
        fits = [r["id"] for r in rooms if r["capacity"] >= int(w["attendees"])
                and (w["projector"] != "yes" or "projector" in r["equipment"])]
        free = {r["room"]: r["free"] for r in call(ws, SERVICE, "free", "--date", w["date"])[0]["rooms"]}
        room = next(r for r in [w["preferred_room"], *fits] if r in fits and _covers(free[r], lo, hi))
        _chain(ws, room, w)
    call(ws, SERVICE, "reservations")


def near_miss_rebooks_hole_elsewhere(ws: Path) -> None:
    """Notices the released piece and books just that hole in another free room."""
    near_miss_no_replan_after_release(ws)
    for r in call(ws, SERVICE, "reservations")[0]["reservations"]:
        if r["state"] != "active":
            free = {x["room"]: x["free"] for x in call(ws, SERVICE, "free", "--date", r["date"])[0]["rooms"]}
            room = next(x for x in ("gale", "ember", "fjord") if _covers(free[x], _mins(r["start"]), _mins(r["end"])))
            call(ws, SERVICE, "reserve", "--room", room, "--date", r["date"], "--start", r["start"],
                 "--duration", str(r["duration_min"]), "--attendees", "18", "--title", "W2")


def near_miss_single_reservation(ws: Path) -> None:
    """Docs literally: one reserve per workshop in the preferred room."""
    for w in _read_workshops(ws):
        call(ws, SERVICE, "reserve", "--room", w["preferred_room"], "--date", w["date"], "--start", w["start"],
             "--duration", w["duration_min"], "--attendees", w["attendees"], "--title", w["id"])


def near_miss_split_remainder(ws: Path) -> None:
    """Chains pieces, but books a refused piece in whatever room is free for it."""
    rooms = _rooms(ws)
    for w in _read_workshops(ws):
        lo, hi = _mins(w["start"]), _mins(w["start"]) + int(w["duration_min"])
        room, cur = w["preferred_room"], lo
        while cur < hi:
            resp, code = call(ws, SERVICE, "reserve", "--room", room, "--date", w["date"],
                              "--start", f"{cur // 60:02d}:{cur % 60:02d}", "--duration", str(hi - cur),
                              "--attendees", w["attendees"], "--title", w["id"])
            if code == 0:
                cur = _mins(resp["reservation"]["end"])
                continue
            free = {r["room"]: r["free"] for r in call(ws, SERVICE, "free", "--date", w["date"])[0]["rooms"]}
            room = next(r["id"] for r in rooms if r["capacity"] >= int(w["attendees"])
                        and ("projector" in r["equipment"] or w["projector"] != "yes")
                        and _covers(free[r["id"]], cur, min(hi, cur + 60)))


def _move_on_conflict(ws: Path, *, check_projector: bool, cancel_pieces: bool) -> None:
    rooms = _rooms(ws)
    for w in _read_workshops(ws):
        lo, hi = _mins(w["start"]), _mins(w["start"]) + int(w["duration_min"])
        ids, ok = _chain(ws, w["preferred_room"], w)
        if ok:
            continue
        if cancel_pieces:
            for rid in ids:
                call(ws, SERVICE, "cancel", "--id", rid)
        free = {r["room"]: r["free"] for r in call(ws, SERVICE, "free", "--date", w["date"])[0]["rooms"]}
        room = next(r["id"] for r in rooms if r["capacity"] >= int(w["attendees"])
                    and (not check_projector or w["projector"] != "yes" or "projector" in r["equipment"])
                    and _covers(free[r["id"]], lo, hi))
        _chain(ws, room, w)


def near_miss_trusts_projector_claim(ws: Path) -> None:
    """Handles the cap and moves whole workshops, but believes every room has a projector."""
    _move_on_conflict(ws, check_projector=False, cancel_pieces=True)


def near_miss_keeps_first_pieces(ws: Path) -> None:
    """Moves the whole workshop to a suitable room but leaves the pieces already booked."""
    _move_on_conflict(ws, check_projector=True, cancel_pieces=False)


def near_miss_ignores_preference(ws: Path) -> None:
    """Checks `free` up front but takes the first suitable room instead of the preferred one."""
    rooms = _rooms(ws)
    for w in _read_workshops(ws):
        lo, hi = _mins(w["start"]), _mins(w["start"]) + int(w["duration_min"])
        free = {r["room"]: r["free"] for r in call(ws, SERVICE, "free", "--date", w["date"])[0]["rooms"]}
        room = next(r["id"] for r in rooms if r["capacity"] >= int(w["attendees"])
                    and (w["projector"] != "yes" or "projector" in r["equipment"])
                    and _covers(free[r["id"]], lo, hi))
        _chain(ws, room, w)
    _repair_released(ws, rooms)


NEAR_MISSES = [
    near_miss_no_replan_after_release,
    near_miss_rebooks_hole_elsewhere,
    near_miss_single_reservation,
    near_miss_split_remainder,
    near_miss_trusts_projector_claim,
    near_miss_keeps_first_pieces,
    near_miss_ignores_preference,
]

TASK = reflect_task(
    id="reflect_12_rooms_duration_cap",
    name="Book long workshops when every reservation is silently capped at an hour",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"workshops.csv": _workshops_csv()},
    tags=("booking", "scheduling"),
)
