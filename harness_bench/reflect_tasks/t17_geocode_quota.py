"""Geocode a list of addresses through a service with a hidden, final quota.

Deviations from docs/geo.md (all visible in responses):
- Lookups are metered: every response carries `quota_remaining`; once it hits
  zero, `lookup` and `batch` are refused for good. The docs promise no limits.
- A single `lookup` costs one unit; its `hint` points to an undocumented
  `batch --file` (up to 10 addresses for one unit). Geocoding the list one by
  one exhausts the quota long before the end.
- Some addresses are `ambiguous` (a merged municipality with several villages
  that share the street name) and come back with bare candidates instead of a
  point; the postcode, needed to pick the right one, is only in `place --id`
  (free). Re-querying with the postcode appended does not help and burns quota.
- Mid-course: after three batches the key is downgraded to 5 rows per batch.
  A 10-row batch still answers `ok` and charges a unit, but only the first five
  rows are geocoded; the rest come back `not_processed` next to a `notice` and
  `batch_max_rows: 5`. The quota is tight (12 units, the exact plan needs 10):
  re-geocoding the skipped rows one by one runs it dry, so they must go into
  further 5-row batches.
"""

from __future__ import annotations

import csv
import io as _io
import json
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

QUOTA = 12
STREETS = [
    "Keizersgracht", "Prinsengracht", "Herengracht", "Damrak", "Rokin", "Utrechtsestraat",
    "Ferdinand Bolstraat", "Overtoom", "Kinkerstraat", "Javastraat", "Czaar Peterstraat",
    "Haarlemmerdijk", "Wibautstraat", "Van Woustraat", "Bilderdijkstraat",
]
CITIES = [("Amsterdam", "10"), ("Haarlem", "20"), ("Utrecht", "35"), ("Leiden", "23")]
# Merged municipalities: the same street exists in several villages.
AMBIGUOUS = {
    4: ("Dorpsstraat", "Zaanstad", ["Assendelft", "Krommenie", "Wormerveer"], 1),
    11: ("Kerkstraat", "Westland", ["Naaldwijk", "Monster"], 0),
    19: ("Molenweg", "Haarlemmermeer", ["Hoofddorp", "Nieuw-Vennep", "Badhoevedorp"], 2),
    26: ("Dorpsstraat", "Westland", ["De Lier", "Wateringen"], 1),
    33: ("Schoolstraat", "Zaanstad", ["Zaandijk", "Westzaan", "Assendelft"], 1),
    41: ("Kerkweg", "Haarlemmermeer", ["Zwanenburg", "Hoofddorp"], 1),
    48: ("Julianastraat", "Westland", ["Monster", "Poeldijk", "Naaldwijk"], 2),
    55: ("Raadhuisstraat", "Zaanstad", ["Wormerveer", "Koog aan de Zaan"], 0),
}
LETTERS = "ABCDEGHJKLMNPRSTVWXZ"


def _point(n: int) -> tuple[float, float]:
    return round(52.0 + (n * 7919 % 60000) / 100000, 6), round(4.3 + (n * 104729 % 90000) / 100000, 6)


def _postcode(prefix: str, n: int) -> str:
    return f"{prefix}{n * 37 % 90 + 10:02d} {LETTERS[n % 20]}{LETTERS[n * 7 % 20]}"


def _build() -> tuple[list[dict], dict[str, dict], dict[str, list[str]]]:
    rows: list[dict] = []
    places: dict[str, dict] = {}
    index: dict[str, list[str]] = {}
    for i in range(1, 61):
        aid = f"A{i:02d}"
        number = i * 13 % 170 + 2
        if i in AMBIGUOUS:
            street, city, villages, right = AMBIGUOUS[i]
            address = f"{street} {number}, {city}"
            cands = []
            for k, village in enumerate(villages):
                pid = f"P{5000 + i * 10 + k}"
                lat, lon = _point(i * 10 + k + 700)
                places[pid] = {"label": f"{street} {number}, {village} ({city})",
                               "postcode": _postcode("15", i * 3 + k), "lat": lat, "lon": lon}
                cands.append(pid)
            truth = cands[right]
        else:
            street = STREETS[i % len(STREETS)]
            city, prefix = CITIES[i % len(CITIES)]
            address = f"{street} {number}, {city}"
            truth = f"P{1000 + i * 7}"
            lat, lon = _point(i)
            places[truth] = {"label": address, "postcode": _postcode(prefix, i), "lat": lat, "lon": lon}
            cands = [truth]
        index[address.lower()] = cands
        rows.append({"id": aid, "address": address, "postcode": places[truth]["postcode"], "place": truth})
    return rows, places, index


ROWS, PLACES, INDEX = _build()
ADDRESSES_CSV = "id,address,postcode\n" + "".join(f"{r['id']},\"{r['address']}\",{r['postcode']}\n" for r in ROWS)

_SOURCE = r'''
import csv as _csv
import io as _stdio
import re as _re

PLACES = __PLACES__
INDEX = __INDEX__
QUOTA = __QUOTA__
HINT = ("Для списков адресов выгоднее `geo batch --file <csv с колонками id,address>`: "
        "до 10 адресов за вызов, 1 единица квоты за вызов.")


def initial_state():
    return {"quota": QUOTA, "used": [], "batches": 0}


def _norm(text):
    text = _re.sub(r"\b\d{4}\s?[A-Za-z]{2}\b", " ", str(text))
    text = _re.sub(r"\s+", " ", text).strip(" ,;")
    text = _re.sub(r"\s*,\s*", ", ", text)
    return text.lower()


def _geocode(address):
    cands = INDEX.get(_norm(address))
    if not cands:
        return {"status": "not_found"}
    if len(cands) == 1:
        p = PLACES[cands[0]]
        return {"status": "ok", "place_id": cands[0], "label": p["label"], "lat": p["lat"], "lon": p["lon"]}
    return {"status": "ambiguous", "candidates": [{"place_id": c, "label": PLACES[c]["label"]} for c in cands]}


def _exhausted(state):
    return {"status": "refused", "error": "quota exhausted for this API key; lookups are no longer served",
            "quota_remaining": 0}, 4


def handle(state, words, opts, io):
    cmd = words[0] if words else ""
    if cmd == "lookup":
        address = opts.get("address")
        if not isinstance(address, str) or not address.strip():
            return {"status": "error", "error": "missing --address", "quota_remaining": state["quota"]}, 2
        if state["quota"] <= 0:
            return _exhausted(state)
        state["quota"] -= 1
        state["used"].append("lookup")
        result = _geocode(address)
        result.update({"query": address, "quota_remaining": state["quota"], "hint": HINT})
        return result, 0
    if cmd == "batch":
        path = opts.get("file")
        if not isinstance(path, str):
            return {"status": "error", "error": "missing --file", "quota_remaining": state["quota"]}, 2
        text = io.read(path)
        reader = _csv.DictReader(_stdio.StringIO(text))
        if not reader.fieldnames or "address" not in reader.fieldnames:
            return {"status": "error", "error": "batch file needs a header with an 'address' column",
                    "quota_remaining": state["quota"]}, 2
        rows = [r for r in reader if (r.get("address") or "").strip()]
        if not rows:
            return {"status": "error", "error": "batch file has no addresses", "quota_remaining": state["quota"]}, 2
        limit = 10 if state["batches"] < 3 else 5
        if len(rows) > 10:
            return {"status": "error", "error": f"batch too large: {len(rows)} rows, at most {limit} per call",
                    "quota_remaining": state["quota"]}, 2
        if state["quota"] <= 0:
            return _exhausted(state)
        state["quota"] -= 1
        state["batches"] += 1
        state["used"].append("batch")
        results = []
        for n, row in enumerate(rows, 1):
            item = {"id": (row.get("id") or str(n)).strip(), "query": row["address"]}
            if n > limit:
                item["status"] = "not_processed"
                results.append(item)
                continue
            item.update(_geocode(row["address"]))
            results.append(item)
        resp = {"status": "ok", "count": len(results), "results": results, "quota_remaining": state["quota"]}
        if limit < 10:
            resp["batch_max_rows"] = limit
            resp["notice"] = ("fair-use policy: this key is limited to %d rows per batch; rows past the limit "
                              "were not processed (quota is charged per call)" % limit)
        return resp, 0
    if cmd == "place":
        pid = opts.get("id")
        if pid not in PLACES:
            return {"status": "error", "error": "unknown place id", "quota_remaining": state["quota"]}, 2
        p = PLACES[pid]
        return {"status": "ok", "place_id": pid, "label": p["label"], "postcode": p["postcode"],
                "lat": p["lat"], "lon": p["lon"], "quota_remaining": state["quota"]}, 0
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/geo.md"}, 2
'''

SOURCE = (_SOURCE.replace("__PLACES__", json.dumps(PLACES, ensure_ascii=False, sort_keys=True))
          .replace("__INDEX__", json.dumps(INDEX, ensure_ascii=False, sort_keys=True))
          .replace("__QUOTA__", str(QUOTA)))

SERVICE = Service(tool="geo", title="the Polder geocoding API", source=SOURCE, key=b"reflect-17-geo-4c1e")

DOCS = """
# geo — геокодер адресов

Клиент: `python3 tools/geo <команда> [--опция значение ...]`. Каждая команда печатает JSON.

| Команда | Что делает |
|---|---|
| `lookup --address "УЛИЦА ДОМ, ГОРОД"` | Геокодирует один адрес. |
| `place --id PLACE_ID` | Карточка объекта: `label`, `postcode`, `lat`, `lon`. |

## lookup

Ответ всегда содержит одну лучшую точку для адреса:

```json
{"status": "ok", "place_id": "P1234", "label": "Voorbeeldstraat 1, Amsterdam", "lat": 52.370216, "lon": 4.895168}
```

Если адрес неизвестен, `status` равен `not_found`. Индекс можно дописывать к адресу —
он учитывается при поиске. Координаты — WGS84, до 6 знаков после запятой.

## place

Возвращает сведения об объекте по `place_id` из ответа `lookup`, включая почтовый индекс.

## Ограничения

API внутреннее, лимитов на число запросов нет: геокодируйте адреса по одному в цикле.
"""

PROMPT = """
В файле `addresses.csv` 60 адресов доставки (`id,address,postcode`). Получите через
геокодер `geo` координаты каждого адреса и сохраните их в `coords.csv` с заголовком
`id,lat,lon` — ровно 60 строк в порядке `addresses.csv`, координаты в том виде, в
каком их вернул сервис (6 знаков после запятой). Координаты должны относиться именно
к тому объекту, у которого почтовый индекс совпадает с `postcode` из `addresses.csv`.
Ключ API у нас один на весь месяц, другого не будет.
""" + PROMPT_RULES.format(tool="geo")


def check(result: Replay, ws: Path) -> str | None:
    path = ws / "coords.csv"
    if not path.is_file():
        return "coords.csv was not written"
    rows = list(csv.reader(_io.StringIO(path.read_text(encoding="utf-8-sig"))))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows or [c.strip().lower() for c in rows[0]] != ["id", "lat", "lon"]:
        return "coords.csv must start with the header id,lat,lon"
    body = rows[1:]
    if [r[0].strip() for r in body] != [r["id"] for r in ROWS]:
        return "coords.csv must list all 60 ids in the order of addresses.csv"
    wrong = []
    for row, cells in zip(ROWS, body, strict=True):
        place = PLACES[row["place"]]
        try:
            lat, lon = float(cells[1]), float(cells[2])
        except (IndexError, ValueError):
            wrong.append(row["id"])
            continue
        if abs(lat - place["lat"]) > 5e-7 or abs(lon - place["lon"]) > 5e-7:
            wrong.append(row["id"])
    if wrong:
        return f"{len(wrong)} addresses have wrong or missing coordinates (e.g. {wrong[0]})"
    return None


def _addresses(ws: Path) -> list[dict]:
    return list(csv.DictReader(_io.StringIO((ws / "addresses.csv").read_text(encoding="utf-8"))))


def _batch(ws: Path, rows: list[dict], name: str, resp_out: list | None = None) -> list[dict]:
    buf = _io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["id", "address"])
    for r in rows:
        writer.writerow([r["id"], r["address"]])
    (ws / name).write_text(buf.getvalue(), encoding="utf-8")
    resp, code = call(ws, SERVICE, "batch", "--file", name)
    if resp_out is not None:
        resp_out.append(resp)
    return resp.get("results", []) if code == 0 else []


def _write(ws: Path, rows: list[dict], coords: dict[str, tuple[float, float]]) -> None:
    lines = ["id,lat,lon"] + [f"{r['id']},{coords[r['id']][0]:.6f},{coords[r['id']][1]:.6f}"
                              for r in rows if r["id"] in coords]
    (ws / "coords.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _resolve(ws: Path, rows: list[dict], results: list[dict], pick_first: bool = False) -> dict[str, tuple[float, float]]:
    by_id = {r["id"]: r for r in rows}
    coords: dict[str, tuple[float, float]] = {}
    for item in results:
        if item.get("status") == "ok":
            coords[item["id"]] = (item["lat"], item["lon"])
        elif item.get("status") == "ambiguous":
            for cand in item["candidates"]:
                info, _ = call(ws, SERVICE, "place", "--id", cand["place_id"])
                if pick_first or info.get("postcode") == by_id[item["id"]]["postcode"]:
                    coords[item["id"]] = (info["lat"], info["lon"])
                    break
    return coords


def _batch_all(ws: Path, rows: list[dict], *, replan: bool = True) -> list[dict]:
    """Batch everything, following `batch_max_rows` and resubmitting `not_processed` rows."""
    queue, size, done, n = list(rows), 10, [], 0
    while queue:
        chunk, queue = queue[:size], queue[size:]
        n += 1
        seen: list[dict] = []
        results = _batch(ws, chunk, f"batch_{n}.csv", seen)
        if not seen or seen[0].get("status") != "ok":
            break
        skipped = {r["id"] for r in results if r.get("status") == "not_processed"}
        done += [r for r in results if r["id"] not in skipped]
        if replan:
            size = seen[0].get("batch_max_rows", size)
            queue = [r for r in chunk if r["id"] in skipped] + queue
    return done


def gold(ws: Path) -> None:
    rows = _addresses(ws)
    first, _ = call(ws, SERVICE, "lookup", "--address", rows[0]["address"])
    # The very first answer shows quota_remaining and the batch hint: switch at once.
    assert first.get("quota_remaining", 0) >= 6 and "batch" in first.get("hint", ""), first
    coords = _resolve(ws, rows, _batch_all(ws, rows))
    _write(ws, rows, coords)


def near_miss_one_by_one(ws: Path) -> None:
    """Follows the docs: a lookup per address, ambiguous answers resolved via place."""
    rows = _addresses(ws)
    results = []
    for r in rows:
        resp, code = call(ws, SERVICE, "lookup", "--address", r["address"])
        if code == 0:
            results.append({"id": r["id"], **resp})
    _write(ws, rows, _resolve(ws, rows, results))


def near_miss_batch_first_candidate(ws: Path) -> None:
    """Finds batch, but takes the first candidate of an ambiguous address."""
    rows = _addresses(ws)
    call(ws, SERVICE, "lookup", "--address", rows[0]["address"])
    results = _batch_all(ws, rows)
    _write(ws, rows, _resolve(ws, rows, results, pick_first=True))


def near_miss_switches_late(ws: Path) -> None:
    """Geocodes the first ten one by one before switching to batch."""
    rows = _addresses(ws)
    results = []
    for r in rows[:10]:
        resp, _ = call(ws, SERVICE, "lookup", "--address", r["address"])
        results.append({"id": r["id"], **resp})
    results += _batch_all(ws, rows[10:])
    _write(ws, rows, _resolve(ws, rows, results))


def near_miss_requery_with_postcode(ws: Path) -> None:
    """Uses batch, but re-queries ambiguous addresses with the postcode appended."""
    rows = _addresses(ws)
    by_id = {r["id"]: r for r in rows}
    call(ws, SERVICE, "lookup", "--address", rows[0]["address"])
    results = _batch_all(ws, rows)
    fixed = []
    for item in results:
        if item.get("status") == "ambiguous":
            r = by_id[item["id"]]
            resp, code = call(ws, SERVICE, "lookup", "--address", f"{r['address']}, {r['postcode']}")
            if code == 0 and resp.get("status") == "ok":
                item = {"id": r["id"], **resp}
        fixed.append(item)
    _write(ws, rows, _resolve(ws, rows, fixed, pick_first=True))


def near_miss_ignores_downgrade(ws: Path) -> None:
    """Keeps sending 10-row batches and never resubmits the `not_processed` rows."""
    rows = _addresses(ws)
    call(ws, SERVICE, "lookup", "--address", rows[0]["address"])
    _write(ws, rows, _resolve(ws, rows, _batch_all(ws, rows, replan=False)))


def near_miss_skipped_one_by_one(ws: Path) -> None:
    """Notices the skipped rows after the fact and geocodes them with single lookups."""
    rows = _addresses(ws)
    call(ws, SERVICE, "lookup", "--address", rows[0]["address"])
    results = _batch_all(ws, rows, replan=False)
    have = {r["id"] for r in results}
    for r in rows:
        if r["id"] not in have:
            resp, code = call(ws, SERVICE, "lookup", "--address", r["address"])
            if code == 0:
                results.append({"id": r["id"], **resp})
    _write(ws, rows, _resolve(ws, rows, results))


NEAR_MISSES = [
    near_miss_ignores_downgrade,
    near_miss_skipped_one_by_one,
    near_miss_one_by_one,
    near_miss_batch_first_candidate,
    near_miss_switches_late,
    near_miss_requery_with_postcode,
]

TASK = reflect_task(
    id="reflect_17_geocode_quota",
    name="Geocode addresses under a hidden final quota with ambiguous matches",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"addresses.csv": ADDRESSES_CSV},
    tags=("geocoding", "quota"),
)
