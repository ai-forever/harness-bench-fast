"""Map messy product names to catalog SKUs.

Deviations from docs/catalog.md (all visible in responses):
- `search` is an exact, case- and accent-sensitive substring match (docs: case- and
  accent-insensitive). Lower-case, unaccented queries miss the real product and
  often hit a look-alike ("Crackers with creme fraiche", "greek yoghurt style
  dessert") instead: the obvious fallback returns a confident wrong answer.
- `browse` returns 25 items per page with `next_cursor` (docs: the whole catalog).
- `get` carries undocumented `status: discontinued` and `replaced_by` fields; some
  products exist twice with the same name and size, and only one is active.
- Mid-course surprise: after the 12th `get` the catalog moves to version 2. Three
  products (two of them usually looked up already, one that looked unique) are
  re-listed under new SKUs, the old ones become discontinued, old browse cursors
  expire, and every response from then on carries `catalog_version: 2` and a
  `notice` pointing to the undocumented `changes --since 1`. Answers gathered
  before the update have to be revisited.
Several names match products in several sizes; the size in the name (in any unit)
picks one, and it is visible only through `get`.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from pathlib import Path

from harness_bench.reflect_tasks.common import PROMPT_RULES, Replay, Service, call, reflect_task

# (line in names.txt, catalog name, size in g or ml)
NAMES = [
    ("greek YOGHURT 0,5 kg", "Greek Yoghurt", 500),
    ("  Creme fraiche 30% 20 cl", "Crème Fraîche 30%", 200),
    ("olive oil  extra virgin 1000ml", "Olive Oil Extra Virgin", 1000),
    ("CAFE CREMA BEANS 1kg", "Café Crema Beans", 1000),
    ("jalapeno salsa 450 g", "Jalapeño Salsa", 450),
    ("Brotchen mix 500 g", "Brötchen Mix", 500),
    ("Orange  juice 1,5 L", "Orange Juice", 1500),
    ("basmati rice 5000 g ", "Basmati Rice", 5000),
    ("pate de campagne 180g", "Pâté de Campagne", 180),
    ("pain d'epices 300 g", "Pain d'Épices", 300),
    ("SPARKLING WATER 0.5l", "Sparkling Water", 500),
    ("musli crunchy 750 g", "Müsli Crunchy", 750),
    ("JAMON SERRANO SLICES 100 g", "Jamón Serrano Slices", 100),
    ("gouda jeune 0,2kg", "Gouda Jeune", 200),
    ("rooibos tea 40 g", "Rooibos Tea", 40),
    ("creme brulee dessert 200g", "Crème Brûlée Dessert", 200),
    ("Tomato passata 700 g", "Tomato Passata", 700),
    ("peanut butter  smooth 350 g", "Peanut Butter Smooth", 350),
    ("crepes mix 400g", "Crêpes Mix", 400),
    ("oat drink barista 1l", "Oat Drink Barista", 1000),
    ("dark chocolate 85% 100 g", "Dark Chocolate 85%", 100),
    ("acai bowl frozen 350 g", "Açaí Bowl Frozen", 350),
    ("pina colada mix 1 l", "Piña Colada Mix", 1000),
    ("GINGER SHOT 60 ml", "Ginger Shot", 60),
    ("vanilla ice cream 0,9 l", "Vanilla Ice Cream", 900),
    ("fruhstucksflocken 500 g", "Frühstücksflocken", 500),
    ("creme de marrons 250 g", "Crème de Marrons", 250),
    ("sesame   bagels 340 g", "Sesame Bagels", 340),
    ("kombucha ginger 330ml", "Kombucha Ginger", 330),
    ("FRITES CONGELEES 1kg", "Frites Congelées", 1000),
]

SOURCE = r'''
# (name, size as shown, active?)
ITEMS = [
    ("Greek Yoghurt", "150 g", True), ("Greek Yoghurt", "500 g", True), ("Greek Yoghurt", "1 kg", True),
    ("greek yoghurt style dessert", "4 x 125 g", True),
    ("Crème Fraîche 30%", "200 ml", True), ("Crème Fraîche 30%", "0.5 l", True),
    ("Crackers with creme fraiche & chives", "150 g", True),
    ("Olive Oil Extra Virgin", "500 ml", True), ("Olive Oil Extra Virgin", "1 l", True),
    ("Olive Oil Extra Virgin", "3 l", True), ("olive oil extra virgin spray", "200 ml", True),
    ("Café Crema Beans", "250 g", True), ("Café Crema Beans", "500 g", True), ("Café Crema Beans", "1 kg", True),
    ("Cafe Crema Pods", "16 pcs", True),
    ("Jalapeño Salsa", "200 g", True), ("Jalapeño Salsa", "450 g", True), ("jalapeno chips", "150 g", True),
    ("Brötchen Mix", "500 g", True), ("Brotchen mix bread flour", "1 kg", True),
    ("Orange Juice", "330 ml", True), ("Orange Juice", "1 l", True), ("Orange Juice", "1.5 l", True),
    ("orange juice concentrate", "250 ml", True),
    ("Basmati Rice", "500 g", True), ("Basmati Rice", "1 kg", True), ("Basmati Rice", "5 kg", True),
    ("basmati rice crackers", "100 g", True),
    ("Pâté de Campagne", "90 g", True), ("Pâté de Campagne", "180 g", True),
    ("Dog snack pate de campagne style", "300 g", True),
    ("Pain d'Épices", "300 g", True), ("pain d'epices spice blend", "40 g", True),
    ("Sparkling Water", "500 ml", True), ("Sparkling Water", "1.5 l", True), ("SPARKLING WATER LEMON", "500 ml", True),
    ("Müsli Crunchy", "375 g", True), ("Müsli Crunchy", "750 g", True), ("musli crunchy bar", "30 g", True),
    ("Jamón Serrano Slices", "100 g", True), ("JAMON SERRANO SLICES XL", "250 g", True),
    ("Gouda Jeune", "200 g", True), ("Gouda Jeune", "1 kg", True), ("gouda jeune grated", "150 g", True),
    ("Rooibos Tea", "40 g", False), ("Rooibos Tea", "40 g", True), ("Rooibos Tea", "80 g", True),
    ("Crème Brûlée Dessert", "200 g", True), ("Protein bar creme brulee dessert flavour", "60 g", True),
    ("Tomato Passata", "500 g", True), ("Tomato Passata", "700 g", True), ("Tomato passata basil", "700 g", True),
    ("Peanut Butter Smooth", "350 g", True), ("Peanut Butter Crunchy", "350 g", True),
    ("Crêpes Mix", "400 g", False), ("Crêpes Mix", "400 g", True), ("crepes mix gluten free", "300 g", True),
    ("Oat Drink Barista", "1 l", True), ("Oat Drink", "1 l", True), ("oat drink barista syrup", "250 ml", True),
    ("Dark Chocolate 85%", "100 g", True), ("Dark Chocolate 70%", "100 g", True),
    ("Açaí Bowl Frozen", "350 g", True), ("Açaí Bowl Frozen", "350 g", False), ("acai bowl frozen topping", "80 g", True),
    ("Piña Colada Mix", "1 l", True), ("pina colada mix syrup", "700 ml", True),
    ("Ginger Shot", "60 ml", True), ("GINGER SHOT TURMERIC", "60 ml", True),
    ("Vanilla Ice Cream", "900 ml", True), ("Vanilla Ice Cream", "0.5 l", True), ("vanilla ice cream cones", "6 pcs", True),
    ("Frühstücksflocken", "500 g", True), ("fruhstucksflocken honey", "375 g", True),
    ("Crème de Marrons", "250 g", True), ("creme de marrons dessert cups", "4 x 100 g", True),
    ("Sesame Bagels", "340 g", True), ("Sesame Bagels Wholegrain", "340 g", True),
    ("Kombucha Ginger", "330 ml", True), ("kombucha ginger lemon", "330 ml", True),
    ("Frites Congelées", "1 kg", True), ("Frites Congelées", "2.5 kg", True), ("FRITES CONGELEES ALLUMETTES", "1 kg", True),
    ("Bananas", "1 kg", True), ("Apples Elstar", "1.5 kg", True), ("Whole Milk", "1 l", True),
    ("Semi-skimmed Milk", "1 l", True), ("Butter Unsalted", "250 g", True), ("Eggs Free Range", "10 pcs", True),
    ("Sourdough Bread", "800 g", True), ("Penne Rigate", "500 g", True), ("Spaghetti No. 5", "500 g", True),
    ("Cheddar Mature", "400 g", True), ("Honey Acacia", "350 g", True), ("Strawberry Jam", "370 g", True),
    ("Ground Coffee Espresso", "250 g", True), ("Green Tea Sencha", "50 g", True), ("Sunflower Oil", "1 l", True),
    ("Canned Chickpeas", "400 g", True), ("Coconut Milk", "400 ml", True), ("Hummus Classic", "200 g", True),
    ("Tortilla Chips", "200 g", True), ("Sparkling Apple Juice", "750 ml", True),
]
PAGE = 25
UPDATE_AFTER_GETS = 12
UPDATE_AT_TICK = 45
# catalog version 2: these products are re-listed under new SKUs, plus one new size
RELISTED = [("Greek Yoghurt", "500 g"), ("Café Crema Beans", "1 kg"), ("Ginger Shot", "60 ml")]
ADDED = [("Sparkling Water", "750 ml")]


def sku_of(i):
    return f"KN-{10000 + (i * 7919) % 90000:05d}"


def initial_state():
    return {"version": 1, "gets": 0, "updated_at": None}


def build(version):
    items = [{"sku": sku_of(i), "name": n, "size": z, "active": a} for i, (n, z, a) in enumerate(ITEMS)]
    changes = []
    if version >= 2:
        for k, (name, size) in enumerate(RELISTED + ADDED):
            new = {"sku": sku_of(len(ITEMS) + k), "name": name, "size": size, "active": True}
            for old in items:
                if (name, size) in RELISTED and old["active"] and (old["name"], old["size"]) == (name, size):
                    old["active"] = False
                    old["replaced_by"] = new["sku"]
                    changes.append({"sku": old["sku"], "change": "discontinued", "replaced_by": new["sku"]})
            items.append(new)
            changes.append({"sku": new["sku"], "change": "added", "name": name, "size": size})
    for item in items:
        if not item["active"] and "replaced_by" not in item:
            twin = [o for o in items if o["active"] and o["name"] == item["name"] and o["size"] == item["size"]]
            item["replaced_by"] = twin[0]["sku"]
    return sorted(items, key=lambda o: o["sku"]), changes


CATALOGS = {1: build(1)[0], 2: build(2)[0]}
CHANGES = build(2)[1]


def opt_text(opts, key):
    val = opts.get(key)
    if isinstance(val, list):
        val = val[-1]
    return val if isinstance(val, str) and val else None


def handle(state, words, opts, io):
    if state["version"] == 1 and (state["gets"] >= UPDATE_AFTER_GETS or state["clock"] >= UPDATE_AT_TICK):
        state["version"] = 2
        state["updated_at"] = state["clock"]
    resp, code = serve(state, words, opts)
    resp["catalog_version"] = state["version"]
    if state["version"] == 2:
        resp["notice"] = (f"the catalog was updated to version 2 at tick {state['updated_at']}: some products "
                          "were re-listed under new SKUs; see `changes --since 1`")
    return resp, code


def serve(state, words, opts):
    catalog = CATALOGS[state["version"]]
    cmd = words[0] if words else ""
    if cmd == "changes":
        since = opt_text(opts, "since") or "1"
        return {"status": "ok", "since": since, "changes": CHANGES if state["version"] > 1 and since == "1" else []}, 0
    if cmd == "search":
        q = opt_text(opts, "q")
        if q is None:
            return {"status": "error", "error": "--q is required"}, 2
        hits = [{"sku": o["sku"], "name": o["name"]} for o in catalog if q in o["name"]]
        return {"status": "ok", "q": q, "results": hits[:10]}, 0
    if cmd == "browse":
        cur = opt_text(opts, "cursor") or f"c0.v{state['version']}"
        head, _, ver = cur.partition(".v")
        if not head.startswith("c") or not head[1:].isdigit() or not ver.isdigit():
            return {"status": "error", "error": "bad cursor"}, 2
        if int(ver) != state["version"]:
            return {"status": "error", "error": f"cursor expired: the catalog changed (now version {state['version']}); "
                                                "restart browse without --cursor"}, 3
        start = int(head[1:])
        resp = {"status": "ok", "items": [{"sku": o["sku"], "name": o["name"]} for o in catalog[start:start + PAGE]]}
        if start + PAGE < len(catalog):
            resp["next_cursor"] = f"c{start + PAGE}.v{state['version']}"
        return resp, 0
    if cmd == "get":
        state["gets"] += 1
        sku = opt_text(opts, "sku")
        for o in catalog:
            if o["sku"] == sku:
                item = {"sku": o["sku"], "name": o["name"], "size": o["size"],
                        "price": round(1.19 + (int(o["sku"][3:]) % 870) / 100, 2)}
                if not o["active"]:
                    item["status"] = "discontinued"
                    item["replaced_by"] = o["replaced_by"]
                return {"status": "ok", "item": item}, 0
        return {"status": "error", "error": "no such sku"}, 3
    return {"status": "error", "error": f"unknown command {cmd!r}; see docs/catalog.md"}, 2
'''

SERVICE = Service(tool="catalog", title="the Kanaal product catalog", source=SOURCE, key=b"reflect-10-catalog-2b8d")

DOCS = """
# catalog — каталог товаров

Клиент: `python3 tools/catalog <команда> [--опция значение ...]`. Каждая команда печатает JSON.

| Команда | Что делает |
|---|---|
| `search --q TEXT` | Поиск по названию: товары, в названии которых встречается `TEXT`. Поиск нечувствителен к регистру и диакритике (`creme` найдёт «Crème»). Возвращает `sku` и `name`, не больше 10 результатов. |
| `browse` | Весь каталог: `sku` и `name` каждого товара, по возрастанию `sku`. |
| `get --sku SKU` | Карточка товара: `sku`, `name`, `size` (фасовка), `price` (евро). |

Коды выхода: `0` — успех, `2` — ошибка в аргументах, `3` — товар не найден.
"""

PROMPT = """
В `names.txt` — 30 названий товаров, по одному на строку, набранных как попало:
регистр, диакритика, лишние пробелы. В конце каждой строки указана фасовка (в г, кг,
мл, сл или л). Сопоставьте каждой строке товар из каталога `catalog`: тот, у которого
название совпадает с точностью до регистра, диакритики и пробелов, а фасовка равна
указанной (с учётом единиц: 0,5 kg = 500 g, 20 cl = 200 ml). Если подходящих товаров
несколько, нужен действующий, не снятый с продажи. Сопоставление должно быть верным
для каталога на момент окончания вашей работы.

Запишите результат в `mapping.csv` с заголовком `line,sku`: `line` — номер строки в
`names.txt` (с 1), по строке на каждое название, в порядке строк.
""" + PROMPT_RULES.format(tool="catalog")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).casefold().split())


_SIZE = re.compile(r"^(.*?)\s*(\d+(?:[.,]\d+)?)\s*(kg|g|ml|cl|l)\s*$", re.IGNORECASE)
_UNIT = {"g": 1, "kg": 1000, "ml": 1, "cl": 10, "l": 1000}


def _split(text: str) -> tuple[str, float | None]:
    m = _SIZE.match(text.strip())
    if not m:
        return _norm(text), None
    return _norm(m.group(1)), round(float(m.group(2).replace(",", ".")) * _UNIT[m.group(3).lower()], 3)


def _expected(version: int) -> list[str]:
    cat = SERVICE.namespace()["CATALOGS"][version]
    out = []
    for _, name, size in NAMES:
        hits = [o["sku"] for o in cat if o["name"] == name and o["active"] and _split("x " + o["size"])[1] == size]
        assert len(hits) == 1, (name, hits)
        out.append(hits[0])
    return out


def check(result: Replay, ws: Path) -> str | None:
    path = ws / "mapping.csv"
    if not path.exists():
        return "mapping.csv is missing"
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
    if not rows or set(rows[0]) != {"line", "sku"}:
        return "mapping.csv must have the header line,sku"
    if [(r["line"] or "").strip() for r in rows] != [str(n) for n in range(1, len(NAMES) + 1)]:
        return f"mapping.csv must have one row per line of names.txt, lines 1..{len(NAMES)} in order"
    wrong = [r["line"].strip() for r, want in zip(rows, _expected(result.state["version"]), strict=True) if (r["sku"] or "").strip() != want]
    if wrong:
        return f"{len(wrong)} of {len(NAMES)} lines are mapped to the wrong SKU (first: line {wrong[0]})"
    return None


def _browse(ws: Path, *, all_pages: bool = True) -> list[dict]:
    items, cursor = [], None
    while True:
        resp = call(ws, SERVICE, "browse", *(["--cursor", cursor] if cursor else []))[0]
        items += resp["items"]
        cursor = resp.get("next_cursor")
        if not cursor or not all_pages:
            return items


def _lines(ws: Path) -> list[str]:
    return (ws / "names.txt").read_text(encoding="utf-8").splitlines()


def _write(ws: Path, skus: list[str]) -> None:
    rows = ["line,sku"] + [f"{n},{sku}" for n, sku in enumerate(skus, 1)]
    (ws / "mapping.csv").write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")


def _resolve(ws: Path, items: list[dict], *, use_size: bool = True, use_status: bool = True,
             replan: bool = True) -> list[str]:
    cache: dict[str, dict] = {}
    seen_notice = False

    def details(sku: str) -> dict:
        nonlocal seen_notice
        if sku not in cache:
            resp = call(ws, SERVICE, "get", "--sku", sku)[0]
            seen_notice = seen_notice or "notice" in resp
            cache[sku] = resp["item"]
        return cache[sku]

    def pick() -> list[str]:
        out = []
        for line in _lines(ws):
            name, size = _split(line)
            cands = [o["sku"] for o in items if _norm(o["name"]) == name]
            if len(cands) > 1 and use_size:
                cands = [s for s in cands if _split("x " + details(s)["size"])[1] == size]
            if len(cands) > 1 and use_status:
                cands = [s for s in cands if details(s).get("status") != "discontinued"]
            out.append(cands[0] if cands else "")
        return out

    out = pick()
    if seen_notice and replan:
        # The catalog changed under us: apply the change log, then decide again.
        for change in call(ws, SERVICE, "changes", "--since", "1")[0]["changes"]:
            cache.pop(change["sku"], None)
            if change["change"] == "added":
                items = items + [{"sku": change["sku"], "name": change["name"]}]
        out = pick()
    return out


def gold(ws: Path) -> None:
    _write(ws, _resolve(ws, _browse(ws)))


def near_miss_no_replan_after_update(ws: Path) -> None:
    """Resolves carefully but keeps the listing and details fetched before the catalog update."""
    _write(ws, _resolve(ws, _browse(ws), replan=False))


def near_miss_literal_docs(ws: Path) -> None:
    out = []
    for line in _lines(ws):
        hits = call(ws, SERVICE, "search", "--q", " ".join(line.split()))[0]["results"]
        out.append(hits[0]["sku"] if hits else "")
    _write(ws, out)


def near_miss_normalised_search(ws: Path) -> None:
    """Searches the bare, lower-cased, unaccented name and trusts a unique hit."""
    out = []
    for line in _lines(ws):
        name, _ = _split(line)
        hits = call(ws, SERVICE, "search", "--q", name)[0]["results"]
        out.append(hits[0]["sku"] if hits else "")
    _write(ws, out)


def near_miss_first_browse_page(ws: Path) -> None:
    _write(ws, _resolve(ws, _browse(ws, all_pages=False)))


def near_miss_ignores_size(ws: Path) -> None:
    _write(ws, _resolve(ws, _browse(ws), use_size=False))


def near_miss_ignores_discontinued(ws: Path) -> None:
    _write(ws, _resolve(ws, _browse(ws), use_status=False))


NEAR_MISSES = [
    near_miss_literal_docs,
    near_miss_normalised_search,
    near_miss_first_browse_page,
    near_miss_ignores_size,
    near_miss_ignores_discontinued,
    near_miss_no_replan_after_update,
]

TASK = reflect_task(
    id="reflect_10_catalog_case_search",
    name="Map messy product names to SKUs through a case-sensitive, paged catalog",
    prompt=PROMPT,
    service=SERVICE,
    docs=DOCS,
    check=check,
    gold=gold,
    extra_files={"names.txt": "".join(f"{line}\n" for line, _, _ in NAMES)},
    tags=("search", "matching"),
)
