"""long_09_sql_dialect_port — перенос 70 отчётов со старого диалекта SQL на SQLite.

The workspace holds the documentation of a fictional legacy SQL dialect
(«Корунд-SQL 7.4»), the schema of a migrated shop database, 50 legacy report
queries (with long headers, stale comments and commented-out code), the
migrated SQLite database and the expected results of every report on it. The
agent writes ``sqlite/rNN.sql``: one SELECT per report that reproduces the
legacy semantics on SQLite.

Every query is written once as a Python function of a *render mode*: ``leg``
renders the legacy dialect text, ``gold`` the faithful SQLite port, and a trap
category name (``nulls``, ``intdiv``, ``case``, ...) renders the gold port with
that one dialect rule forgotten; ``naive`` forgets all of them. So legacy text,
gold and near misses share one structure and cannot drift apart.

The check runs the agent's SQL on a hidden database (other seed, more edge
rows: NULL/empty strings, midnights, month ends, mixed case) and compares the
rows with the gold port's rows on the same database.
"""

from __future__ import annotations

import datetime as dt
import functools
import math
import random
import re
import sqlite3
import time
from pathlib import Path

from .common import long_task, read_text, require_share, write

TASK_ID = "long_09_sql_dialect_port"

# ==========================================================================
# Data generator (visible and hidden databases share it)
# ==========================================================================

CITIES = [
    "Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск",
    "Самара", "Нижний Новгород", "Пермь", "Воронеж", "Тверь",
]
BRANDS = [
    ("Aurora", "AUR"), ("Nordik", "NRD"), ("Vesta", "VST"), ("Kraft", "KRF"), ("Selena", "SLN"),
    ("Termit", "TRM"), ("Briz", "BRZ"), ("Luxor", "LUX"), ("Orion", "ORN"), ("Zefir", "ZFR"),
]
# id, name, parent_id, code, singular, price range
CATEGORIES = [
    (1, "Кухонная техника", None, None, None, None),
    (2, "Чайники", 1, "KT", "Чайник", (990, 6990)),
    (3, "Кофеварки", 1, "CF", "Кофеварка", (3490, 42990)),
    (4, "Микроволновые печи", 1, "MW", "Микроволновая печь", (4990, 18990)),
    (5, "Блендеры", 1, "BL", "Блендер", (1990, 12990)),
    (6, "Климатическая техника", None, None, None, None),
    (7, "Обогреватели", 6, "HT", "Обогреватель", (1990, 15990)),
    (8, "Вентиляторы", 6, "FN", "Вентилятор", (990, 9990)),
    (9, "Увлажнители", 6, "HM", "Увлажнитель", (1990, 11990)),
    (10, "Уход за собой", None, None, None, None),
    (11, "Фены", 10, "HD", "Фен", (990, 8990)),
    (12, "Электробритвы", 10, "SH", "Электробритва", (1490, 14990)),
    (13, "Уборка и глажение", None, None, None, None),
    (14, "Пылесосы", 13, "VC", "Пылесос", (3990, 29990)),
    (15, "Роботы-пылесосы", 13, "RV", "Робот-пылесос", (9990, 59990)),
    (16, "Утюги", 13, "IR", "Утюг", (1490, 9990)),
    (17, "Разное", None, "MX", "Аксессуар", (190, 2990)),
]
COLORS = ["белый", "чёрный", "серебристый", "красный", "бежевый", "графит"]
SURNAMES = [
    ("Иванов", "Иванова"), ("Петров", "Петрова"), ("Смирнов", "Смирнова"), ("Кузнецов", "Кузнецова"),
    ("Волков", "Волкова"), ("Соколов", "Соколова"), ("Лебедев", "Лебедева"), ("Козлов", "Козлова"),
    ("Новиков", "Новикова"), ("Морозов", "Морозова"), ("Павлов", "Павлова"), ("Семёнов", "Семёнова"),
    ("Голубев", "Голубева"), ("Виноградов", "Виноградова"), ("Богданов", "Богданова"),
    ("Воробьёв", "Воробьёва"), ("Фёдоров", "Фёдорова"), ("Михайлов", "Михайлова"),
    ("Беляев", "Беляева"), ("Тарасов", "Тарасова"), ("Белов", "Белова"), ("Комаров", "Комарова"),
    ("Орлов", "Орлова"), ("Киселёв", "Киселёва"), ("Макаров", "Макарова"), ("Андреев", "Андреева"),
    ("Ковалёв", "Ковалёва"), ("Ильин", "Ильина"), ("Гусев", "Гусева"), ("Титов", "Титова"),
    ("Кудрявцев", "Кудрявцева"), ("Баранов", "Баранова"), ("Куликов", "Куликова"),
    ("Алексеев", "Алексеева"), ("Степанов", "Степанова"), ("Яковлев", "Яковлева"),
]
MALE = ["Александр", "Дмитрий", "Максим", "Сергей", "Андрей", "Алексей", "Артём", "Илья", "Кирилл",
        "Михаил", "Никита", "Матвей", "Роман", "Егор", "Иван", "Павел", "Олег", "Виктор"]
FEMALE = ["Анна", "Мария", "Елена", "Ольга", "Татьяна", "Наталья", "Ирина", "Светлана", "Юлия",
          "Екатерина", "Дарья", "Полина", "Ксения", "Вера", "Алина", "Виктория", "Софья", "Нина"]
PATRON = ["Александров", "Дмитриев", "Сергеев", "Андреев", "Алексеев", "Михайлов", "Иванов",
          "Павлов", "Олегов", "Викторов", "Николаев", "Петров", "Юрьев", "Владимиров"]
LAT_SYL = ["ka", "ri", "no", "va", "le", "mi", "to", "sa", "de", "ra", "ko", "lu", "an", "el", "or"]
DOMAINS = ["mail.example", "inbox.example", "post.example", "corp.example", "yandex.example"]
PROMOS = [
    ("WELCOME", "Скидка новому покупателю", 5), ("SPRING23", "Весна 2023", 10),
    ("SUMMER23", "Лето 2023", 10), ("TEA10", "Чайная неделя", 10), ("COOL15", "Охлаждение 15", 15),
    ("BF23", "Чёрная пятница 2023", 20), ("NY24", "Новогодняя распродажа", 15),
    ("KITCHEN12", "Кухня −12%", 12), ("CLEAN7", "Чистый дом", 7), ("VIP25", "Для VIP-клиентов", 25),
    ("SPRING24", "Весна 2024", 10), ("MAMA8", "8 Марта", 8), ("SCHOOL23", "Снова в школу", 5),
    ("WARM20", "Тёплая зима", 20), ("ROBOT30", "Роботы со скидкой", 30), ("APP5", "Заказ в приложении", 5),
    ("FRIEND10", "Приведи друга", 10), ("SUMMER24", "Лето 2024", 10), ("HAIR9", "Фены −9%", 9),
    ("OLDCODE", "Архивная акция", 5),
]
REVIEW_BODIES = [
    "Всё отлично, работает тихо.", "Доставили быстро, упаковка целая.",
    "Через месяц начал шуметь.", "Цена соответствует качеству.", "Брал в подарок, довольны.",
    "Инструкция только на английском.", "Корпус быстро царапается.", "Лучше, чем предыдущая модель.",
    "Шнур коротковат.", "Рекомендую.",
]
COMMENTS = ["Позвонить за час", "Домофон не работает", "Оставить у консьержа", "Подарочная упаковка",
            "После 18:00", "Нужен чек для юрлица", "Хрупкое"]
STORE_NAMES = ["ТЦ «Галерея»", "ТЦ «Меридиан»", "ул. Ленина, 12", "ТЦ «Мега»", "пр. Мира, 40",
               "ТЦ «Радуга»", "ул. Гагарина, 5", "ТЦ «Европа»", "Садовая, 21", "ТЦ «Октябрь»",
               "ТЦ «Флагман»", "ул. Советская, 3", "ТЦ «Столица»", "Невский, 88"]


def _case(r: random.Random, word: str, e: float) -> str:
    x = r.random()
    if x < 0.62 / max(1.0, e * 0.8):
        return word.lower()
    if x < 0.85:
        return word.upper()
    return word.title()


def _rand_date(r: random.Random, lo: dt.date, hi: dt.date, *, month_end: float = 0.0) -> dt.date:
    d = lo + dt.timedelta(days=r.randrange((hi - lo).days + 1))
    if month_end and r.random() < month_end:
        # push to one of the last days of the month (29/30/31/Feb 29)
        last = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        d = d.replace(day=max(28, last.day - r.randrange(3)))
        d = min(d, hi)
    return d


def _dtstr(d: dt.date, secs: int) -> str:
    return f"{d.isoformat()} {secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}"


SCHEMA_SQL = """
CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL, parent_id INTEGER);
CREATE TABLE products (
  id INTEGER PRIMARY KEY, sku TEXT NOT NULL, name TEXT NOT NULL, brand TEXT NOT NULL,
  category_id INTEGER NOT NULL, price REAL NOT NULL, cost REAL, weight_g INTEGER, color TEXT,
  launched_on TEXT NOT NULL, discontinued_on TEXT);
CREATE TABLE customers (
  id INTEGER PRIMARY KEY, full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT, city TEXT NOT NULL,
  segment TEXT, birth_date TEXT, registered_at TEXT NOT NULL, is_active TEXT NOT NULL, referrer_id INTEGER);
CREATE TABLE stores (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL, opened_on TEXT NOT NULL,
  closed_on TEXT, area_m2 INTEGER, manager TEXT);
CREATE TABLE promotions (
  code TEXT PRIMARY KEY, title TEXT NOT NULL, discount_pct INTEGER NOT NULL,
  valid_from TEXT NOT NULL, valid_to TEXT);
CREATE TABLE orders (
  id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, store_id INTEGER, created_at TEXT NOT NULL,
  status TEXT NOT NULL, channel TEXT NOT NULL, promo_code TEXT, delivery_fee REAL, shipped_at TEXT,
  delivered_on TEXT, comment TEXT);
CREATE TABLE order_items (
  order_id INTEGER NOT NULL, line_no INTEGER NOT NULL, product_id INTEGER NOT NULL,
  qty INTEGER NOT NULL, unit_price REAL NOT NULL, discount_pct INTEGER,
  PRIMARY KEY (order_id, line_no));
CREATE TABLE payments (
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, paid_at TEXT NOT NULL, amount REAL NOT NULL,
  method TEXT NOT NULL);
CREATE TABLE returns (
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL, qty INTEGER NOT NULL,
  reason_code TEXT, return_date_txt TEXT NOT NULL, refund REAL);
CREATE TABLE stock (
  store_id INTEGER NOT NULL, product_id INTEGER NOT NULL, qty INTEGER, counted_on TEXT NOT NULL,
  PRIMARY KEY (store_id, product_id));
CREATE TABLE reviews (
  id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, rating INTEGER,
  created_at TEXT NOT NULL, body TEXT);
"""

D_START = dt.date(2023, 1, 1)
D_END = dt.date(2024, 6, 30)


def gen_db(seed: str, e: float, n_orders: int) -> bytes:
    """Generate the shop database; `e` scales the rate of edge values."""
    r = random.Random(seed)
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA_SQL)
    con.executemany("INSERT INTO categories VALUES (?,?,?)", [(c[0], c[1], c[2]) for c in CATEGORIES])

    # products --------------------------------------------------------------
    products = []
    used_sku = set()
    leafs = [c for c in CATEGORIES if c[3]]
    pid = 0
    for c in leafs:
        for _ in range(r.randint(7, 12) if c[0] != 17 else 6):
            pid += 1
            brand, bcode = r.choice(BRANDS)
            while True:
                num = r.randint(1, 99)
                key = (bcode, c[3], num)
                if key not in used_sku:
                    used_sku.add(key)
                    break
            x = r.random()
            if x < 0.07 * e:
                sku = f"{bcode}-{c[3]}{num:02d}"
            elif x < 0.13 * e:
                sku = f"{bcode}-{c[3]}{num:04d}"
            else:
                sku = f"{bcode}-{c[3]}{num:03d}"
            if r.random() < 0.08 * e:
                sku += " " * r.choice([1, 1, 2])
            lo, hi = c[5]
            price = float(r.randrange(lo // 10, hi // 10) * 10 - 10 + 9.99) if r.random() < 0.5 else \
                float(r.randrange(lo // 100, hi // 100 + 1) * 100 - 10)
            price = round(max(price, 99.0), 2)
            cost = None if r.random() < 0.08 * e else round(price * r.uniform(0.52, 0.82), 2)
            weight = None if r.random() < 0.1 * e else r.randrange(150, 9000, 10)
            y = r.random()
            color = None if y < 0.08 * e else ("" if y < 0.15 * e else r.choice(COLORS))
            launched = _rand_date(r, dt.date(2020, 1, 1), dt.date(2024, 3, 31), month_end=0.08 * e)
            disc = None
            if r.random() < 0.14:
                disc = _rand_date(r, max(launched + dt.timedelta(days=90), dt.date(2023, 1, 1)),
                                  dt.date(2024, 6, 30), month_end=0.1 * e)
                disc = disc.isoformat()
            products.append((pid, sku, f"{c[4]} {brand} {c[3]}-{num}", brand, c[0], price, cost,
                             weight, color, launched.isoformat(), disc))
    con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?)", products)
    prod = {p[0]: p for p in products}

    # customers -------------------------------------------------------------
    n_cust = n_orders // 8
    customers = []
    for cid in range(1, n_cust + 1):
        male = r.random() < 0.5
        sur = r.choice(SURNAMES)[0 if male else 1]
        first = r.choice(MALE if male else FEMALE)
        pat = r.choice(PATRON) + ("ич" if male else "на")
        handle = "".join(r.choice(LAT_SYL) for _ in range(r.randint(2, 4))) + str(r.randint(1, 99))
        dom = r.choice(DOMAINS)
        if r.random() < 0.12 * e:
            dom = dom.upper() if r.random() < 0.5 else dom.title()
        email = f"{handle}@{dom}"
        x = r.random()
        phone = "" if x < 0.07 * e else (None if x < 0.12 * e else
                                         f"+7 9{r.randint(10, 99)} {r.randint(100, 999)}-"
                                         f"{r.randint(10, 99)}-{r.randint(10, 99)}")
        x = r.random()
        seg = None if x < 0.06 * e else ("" if x < 0.11 * e else
                                         _case(r, r.choice(["retail"] * 6 + ["wholesale"] * 2 + ["vip"]), e))
        birth = None if r.random() < 0.2 else _rand_date(
            r, dt.date(1955, 1, 1), dt.date(2005, 12, 31), month_end=0.06 * e).isoformat()
        reg = _rand_date(r, dt.date(2021, 1, 1), dt.date(2024, 6, 20), month_end=0.06 * e)
        secs = 0 if r.random() < 0.02 * e else r.randrange(7 * 3600, 86400)
        x = r.random()
        active = "Y" if x < 0.78 else ("N" if x < 0.92 else r.choice(["y", "n", "Y "]))
        ref = r.randint(1, cid - 1) if cid > 5 and r.random() < 0.2 else None
        customers.append((cid, f"{sur} {first} {pat}", email, phone, r.choice(CITIES), seg, birth,
                          _dtstr(reg, secs), active, ref))
    con.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?)", customers)
    cust_reg = {c[0]: c[7] for c in customers}

    # stores ----------------------------------------------------------------
    stores = []
    for sid in range(1, len(STORE_NAMES) + 1):
        opened = _rand_date(r, dt.date(2015, 1, 1), dt.date(2023, 2, 28), month_end=0.1 * e)
        closed = None
        if r.random() < 0.22:
            closed = _rand_date(r, dt.date(2023, 6, 1), dt.date(2024, 6, 30), month_end=0.2 * e).isoformat()
        area = None if r.random() < 0.15 * e else r.randrange(120, 900, 10)
        x = r.random()
        mgr = None if x < 0.1 * e else ("" if x < 0.18 * e else
                                        f"{r.choice(SURNAMES)[0]} {r.choice(MALE)[0]}.")
        if e >= 2 and sid in (2, 9):
            opened = dt.date(r.randint(2016, 2022), r.choice([3, 5, 8, 10, 12]), 31)
        stores.append((sid, STORE_NAMES[sid - 1], r.choice(CITIES), opened.isoformat(), closed, area, mgr))
    con.executemany("INSERT INTO stores VALUES (?,?,?,?,?,?,?)", stores)

    # promotions ------------------------------------------------------------
    promos = []
    for code, title, pct in PROMOS:
        vf = _rand_date(r, dt.date(2022, 10, 1), dt.date(2024, 4, 30), month_end=0.15 * e)
        vt = None if r.random() < 0.2 else _rand_date(
            r, vf + dt.timedelta(days=20), vf + dt.timedelta(days=200), month_end=0.2 * e).isoformat()
        promos.append((code, title, pct, vf.isoformat(), vt))
    con.executemany("INSERT INTO promotions VALUES (?,?,?,?,?)", promos)

    # orders / items / payments / returns -------------------------------------
    orders, items, pays, rets = [], [], [], []
    pay_id = ret_id = 0
    weights = [r.random() ** 2.5 + 0.02 for _ in range(n_cust)]
    cust_ids = list(range(1, n_cust + 1))
    for oid in range(1, n_orders + 1):
        cid = r.choices(cust_ids, weights)[0]
        day = _rand_date(r, D_START, D_END, month_end=0.06 * e)
        reg_day = cust_reg[cid][:10]
        if day.isoformat() < reg_day:
            day = dt.date.fromisoformat(reg_day) + dt.timedelta(days=r.randint(0, 5))
            day = min(day, D_END)
        x = r.random()
        secs = 0 if x < 0.012 * e else r.randrange(8 * 3600, 86400)
        created = _dtstr(day, secs)
        ch = r.choices(["web", "app", "store", "phone"], [40, 30, 22, 8])[0]
        store = None
        if ch == "store":
            open_stores = [s for s in stores if s[3] <= day.isoformat() and (s[4] is None or s[4] > day.isoformat())]
            store = r.choice(open_stores or stores)[0]
        age = (D_END - day).days
        if age > 25:
            status = r.choices(["delivered", "cancelled", "returned", "shipped", "paid"], [76, 9, 7, 5, 3])[0]
        else:
            status = r.choices(["new", "paid", "shipped", "delivered", "cancelled"], [15, 25, 25, 30, 5])[0]
        x = r.random()
        promo = None
        if x < 0.26:
            valid = [p for p in promos if p[3] <= day.isoformat() and (p[4] is None or p[4] >= day.isoformat())]
            code = r.choice(valid)[0] if valid and r.random() < 0.85 else r.choice(promos)[0]
            if r.random() < 0.3 * e:
                code = code.lower() if r.random() < 0.6 else code.title()
            if r.random() < 0.09 * e:
                code += " "
            promo = code
        elif x < 0.26 + 0.07 * e:
            promo = ""
        fee = None if ch == "store" else (None if r.random() < 0.05 * e else
                                          r.choice([0.0, 0.0, 199.0, 349.0, 499.0]))
        shipped = delivered = None
        if status in ("shipped", "delivered", "returned") and ch != "store":
            if r.random() > 0.03 * e:
                s2 = secs + r.randrange(3 * 3600, 5 * 86400)
                shipped = _dtstr(day + dt.timedelta(days=s2 // 86400), s2 % 86400)
            if status != "shipped":
                delivered = (day + dt.timedelta(days=r.randint(1, 9))).isoformat()
        elif status in ("delivered", "returned"):
            delivered = day.isoformat()
        x = r.random()
        comment = None if x < 0.7 else ("" if x < 0.7 + 0.14 * e else r.choice(COMMENTS))
        orders.append((oid, cid, store, created, _case(r, status, e), _case(r, ch, e), promo, fee,
                       shipped, delivered, comment))
        total = 0.0
        lines = []
        for ln in range(1, r.choices([1, 2, 3, 4], [45, 30, 17, 8])[0] + 1):
            cands = [p for p in products if p[9] <= day.isoformat()] or products
            p = r.choice(cands)
            qty = r.choices([1, 2, 3, 4, 6], [60, 22, 10, 5, 3])[0]
            up = round(p[5] * r.uniform(0.92, 1.04), 2)
            disc = None if r.random() < 0.6 else r.choice([5, 10, 15, 20, 25])
            items.append((oid, ln, p[0], qty, up, disc))
            lines.append((p[0], qty, up))
            total += qty * up * (100 - (disc or 0)) / 100
        total = round(total + (fee or 0), 2)
        if status not in ("new", "cancelled"):
            parts = [total] if r.random() > 0.1 else [round(total * 0.4, 2), round(total - round(total * 0.4, 2), 2)]
            if r.random() < 0.04:
                parts[-1] = round(parts[-1] - r.choice([100, 250.5, 0.5]), 2)
            for k, amt in enumerate(parts):
                pay_id += 1
                meth = r.choice(["card", "cash", "sbp"] if ch == "store" else ["card", "card", "sbp"])
                ps = secs + r.randrange(60, 3 * 3600) + k * 7200
                pays.append((pay_id, oid, _dtstr(day + dt.timedelta(days=ps // 86400), ps % 86400),
                             amt, _case(r, meth, e)))
        if delivered and (status == "returned" or r.random() < 0.05):
            chosen = lines if status == "returned" and r.random() < 0.6 else [r.choice(lines)]
            for p_id, qty, up in chosen:
                ret_id += 1
                rq = qty if r.random() < 0.7 else r.randint(1, qty)
                rd = dt.date.fromisoformat(delivered) + dt.timedelta(days=r.randint(1, 24))
                x = r.random()
                reason = None if x < 0.1 * e else ("" if x < 0.17 * e else _case(
                    r, r.choice(["DEFECT", "DEFECT", "SIZE", "CHANGED_MIND", "DAMAGED"]), e))
                refund = None if r.random() < 0.1 else round(rq * up, 2)
                rets.append((ret_id, oid, p_id, rq, reason, rd.strftime("%d.%m.%Y"), refund))
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?)", orders)
    con.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", items)
    con.executemany("INSERT INTO payments VALUES (?,?,?,?,?)", pays)
    con.executemany("INSERT INTO returns VALUES (?,?,?,?,?,?,?)", rets)

    # stock -----------------------------------------------------------------
    stock = []
    for s in stores:
        for p in products:
            if r.random() < 0.35:
                qty = None if r.random() < 0.07 * e else r.randint(0, 40)
                cnt = _rand_date(r, dt.date(2024, 1, 1), dt.date(2024, 6, 28), month_end=0.15 * e)
                stock.append((s[0], p[0], qty, cnt.isoformat()))
    con.executemany("INSERT INTO stock VALUES (?,?,?,?)", stock)

    # reviews ---------------------------------------------------------------
    reviews = []
    for rid in range(1, n_orders // 2 + 1):
        p = r.choice(products)
        x = r.random()
        rating = None if x < 0.06 * e else r.choices([1, 2, 3, 4, 5], [6, 7, 14, 33, 40])[0]
        day = _rand_date(r, D_START, D_END, month_end=0.05 * e)
        x = r.random()
        body = None if x < 0.2 else ("" if x < 0.2 + 0.1 * e else r.choice(REVIEW_BODIES))
        reviews.append((rid, p[0], r.randint(1, n_cust), rating,
                        _dtstr(day, 0 if r.random() < 0.01 * e else r.randrange(86400)), body))
    con.executemany("INSERT INTO reviews VALUES (?,?,?,?,?,?)", reviews)
    con.commit()
    data = con.serialize()
    con.close()
    del prod
    return data


# ==========================================================================
# Render-mode helpers. m is "leg", "gold", a trap category or "naive".
# ==========================================================================

CATS = ("nulls", "intdiv", "case", "concat", "empty", "dateadd", "datelit", "decode", "greatest",
        "len", "dow", "dmonth", "dday", "week", "toppct", "outer", "compat", "strcmp",
        "addmonths", "substr0", "dweek", "nextday", "lpad")


def nv(m: str, cat: str) -> bool:
    return m == "naive" or m == cat


def A(m: str, leg: str, gold: str, cat: str | None = None, naive: str | None = None) -> str:
    """Generic alternative: legacy text / gold text / naive text for `cat`."""
    if m == "leg":
        return leg
    if cat and naive is not None and nv(m, cat):
        return naive
    return gold


def _iso(d: str) -> str:
    dd, mm, yy = d.split(".")
    return f"{yy}-{mm}-{dd}"


def S(m: str, col: str, op: str, lit: str) -> str:
    """String comparison with a literal: case-insensitive, trailing spaces ignored."""
    assert lit.isascii()
    if m == "leg" or nv(m, "case"):
        return f"{col} {op} '{lit}'"
    return f"upper(rtrim({col})) {op} '{lit.upper().rstrip()}'"


def SIN(m: str, col: str, lits: list[str], neg: bool = False) -> str:
    kw = "NOT IN" if neg else "IN"
    if m == "leg" or nv(m, "case"):
        return f"{col} {kw} (" + ", ".join(f"'{x}'" for x in lits) + ")"
    return f"upper(rtrim({col})) {kw} (" + ", ".join(f"'{x.upper()}'" for x in lits) + ")"


def SJ(m: str, a: str, b: str) -> str:
    """String equality between two columns (join)."""
    if m == "leg" or nv(m, "case"):
        return f"{a} = {b}"
    return f"upper(rtrim({a})) = upper(rtrim({b}))"


def E(m: str, col: str) -> str:
    """Nullable text column: '' is NULL in the dialect."""
    if m == "leg" or nv(m, "empty"):
        return col
    return f"NULLIF({col}, '')"


def NVL(m: str, a: str, b: str) -> str:
    return f"NVL({a}, {b})" if m == "leg" else f"COALESCE({a}, {b})"


def NVL2(m: str, a: str, b: str, c: str) -> str:
    return f"NVL2({a}, {b}, {c})" if m == "leg" else f"CASE WHEN {a} IS NOT NULL THEN {b} ELSE {c} END"


def IIF(m: str, c: str, a: str, b: str) -> str:
    return f"IIF({c}, {a}, {b})" if m == "leg" else f"CASE WHEN {c} THEN {a} ELSE {b} END"


def CAT(m: str, *parts: str, compat: bool = False) -> str:
    """Concatenation: in the dialect NULL counts as ''; COMPAT(5) propagates NULL."""
    if m == "leg":
        return " || ".join(parts)
    plain = " || ".join(parts)
    coalesced = "NULLIF(" + " || ".join(
        p if (p.startswith("'") and p.endswith("'") and p != "''") else f"COALESCE({p}, '')" for p in parts
    ) + ", '')"
    if compat:
        return coalesced if nv(m, "compat") and m != "naive" else plain
    return plain if nv(m, "concat") else coalesced


def DIV(m: str, a: str, b: str, compat: bool = False) -> str:
    """Division of two integer expressions."""
    if m == "leg":
        return f"{a} / {b}"
    if compat:
        return f"CAST({a} AS REAL) / {b}" if (m == "compat") else f"{a} / {b}"
    return f"{a} / {b}" if nv(m, "intdiv") else f"CAST({a} AS REAL) / {b}"


def DESC(m: str, expr: str, compat: bool = False) -> str:
    if m == "leg":
        return f"{expr} DESC"
    if compat:
        return f"{expr} DESC NULLS FIRST" if m == "compat" else f"{expr} DESC NULLS LAST"
    return f"{expr} DESC" if nv(m, "nulls") else f"{expr} DESC NULLS FIRST"


def ASC(m: str, expr: str, compat: bool = False, word: bool = False) -> str:
    if m == "leg":
        return f"{expr} ASC" if word else expr
    if compat and m != "compat" and m != "naive":
        return f"{expr} NULLS LAST"
    return expr


def DL(m: str, d: str) -> str:
    """Date literal DD.MM.YYYY compared with a DATE."""
    return f"'{d}'" if m == "leg" else f"'{_iso(d)}'"


def DT(m: str, col: str, op: str, d: str) -> str:
    """DATETIME column compared with a date literal (literal = midnight)."""
    if m == "leg":
        return f"{col} {op} '{d}'"
    if nv(m, "datelit"):
        return f"date({col}) {op} '{_iso(d)}'"
    return f"{col} {op} '{_iso(d)} 00:00:00'"


def DTB(m: str, col: str, a: str, b: str) -> str:
    if m == "leg":
        return f"{col} BETWEEN '{a}' AND '{b}'"
    if nv(m, "datelit"):
        return f"date({col}) BETWEEN '{_iso(a)}' AND '{_iso(b)}'"
    return f"{col} BETWEEN '{_iso(a)} 00:00:00' AND '{_iso(b)} 00:00:00'"


def _months_mod(n: str | int, extra: int = 0) -> str:
    if isinstance(n, int):
        k = n + extra
        return f"'{'+' if k >= 0 else ''}{k} months'"
    return f"'+' || ({n}{' + ' + str(extra) if extra else ''}) || ' months'"


def ADDM(m: str, d: str, n: str | int, unit: str = "month", datetime_: bool = False) -> str:
    """DATEADD('month'|'quarter'|'year', n, d) with the day clipped to the month end."""
    mult = {"month": 1, "quarter": 3, "year": 12}[unit]
    if m == "leg":
        return f"DATEADD('{unit}', {n}, {d})"
    nn: str | int = n * mult if isinstance(n, int) else (f"{n}" if mult == 1 else f"({n}) * {mult}")
    fn = "datetime" if datetime_ else "date"
    if nv(m, "dateadd"):
        return f"{fn}({d}, {_months_mod(nn)})"
    last = f"date({d}, 'start of month', {_months_mod(nn, 1)}, '-1 day')"
    clipped = f"{last} || ' ' || time({d})" if datetime_ else last
    return (f"CASE WHEN CAST(strftime('%d', {d}) AS INTEGER) > CAST(strftime('%d', {last}) AS INTEGER) "
            f"THEN {clipped} ELSE {fn}({d}, {_months_mod(nn)}) END")


def ADDD(m: str, d: str, n: int, datetime_: bool = False) -> str:
    if m == "leg":
        return f"DATEADD('day', {n}, {d})"
    fn = "datetime" if datetime_ else "date"
    return f"{fn}({d}, '{'+' if n >= 0 else ''}{n} days')"


def DDAY(m: str, a: str, b: str) -> str:
    if m == "leg":
        return f"DATEDIFF('day', {a}, {b})"
    if nv(m, "dday"):
        return f"CAST(julianday({b}) - julianday({a}) AS INTEGER)"
    return f"CAST(julianday(date({b})) - julianday(date({a})) AS INTEGER)"


def DMON(m: str, a: str, b: str) -> str:
    if m == "leg":
        return f"DATEDIFF('month', {a}, {b})"
    if nv(m, "dmonth"):
        return f"CAST((julianday({b}) - julianday({a})) / 30 AS INTEGER)"
    return (f"((CAST(strftime('%Y', {b}) AS INTEGER) * 12 + CAST(strftime('%m', {b}) AS INTEGER)) - "
            f"(CAST(strftime('%Y', {a}) AS INTEGER) * 12 + CAST(strftime('%m', {a}) AS INTEGER)))")


def DYEAR(m: str, a: str, b: str) -> str:
    if m == "leg":
        return f"DATEDIFF('year', {a}, {b})"
    if nv(m, "dmonth"):
        return f"CAST((julianday({b}) - julianday({a})) / 365.25 AS INTEGER)"
    return f"(CAST(strftime('%Y', {b}) AS INTEGER) - CAST(strftime('%Y', {a}) AS INTEGER))"


def TW(m: str, d: str) -> str:
    if m == "leg":
        return f"TRUNC({d}, 'IW')"
    return f"date({d}, 'weekday 1')" if nv(m, "week") else f"date({d}, '-6 days', 'weekday 1')"


def TM(m: str, d: str) -> str:
    return f"TRUNC({d}, 'MM')" if m == "leg" else f"date({d}, 'start of month')"


def TQ(m: str, d: str) -> str:
    if m == "leg":
        return f"TRUNC({d}, 'Q')"
    return (f"date({d}, 'start of month', '-' || ((CAST(strftime('%m', {d}) AS INTEGER) - 1) % 3) "
            f"|| ' months')")


def TY(m: str, d: str) -> str:
    return f"TRUNC({d}, 'YYYY')" if m == "leg" else f"date({d}, 'start of year')"


def TD(m: str, d: str) -> str:
    return f"TRUNC({d})" if m == "leg" else f"date({d})"


def LASTDAY(m: str, d: str) -> str:
    return f"LAST_DAY({d})" if m == "leg" else f"date({d}, 'start of month', '+1 month', '-1 day')"


def DOW(m: str, d: str) -> str:
    if m == "leg":
        return f"DAYOFWEEK({d})"
    if nv(m, "dow"):
        return f"CAST(strftime('%w', {d}) AS INTEGER)"
    return f"((CAST(strftime('%w', {d}) AS INTEGER) + 6) % 7 + 1)"


def EXTRACT(m: str, part: str, d: str) -> str:
    if m == "leg":
        return f"EXTRACT({part} FROM {d})"
    f = {"YEAR": "%Y", "MONTH": "%m", "DAY": "%d", "HOUR": "%H"}[part]
    return f"CAST(strftime('{f}', {d}) AS INTEGER)"


def LEN(m: str, x: str) -> str:
    if m == "leg":
        return f"LEN({x})"
    return f"length({x})" if nv(m, "len") else f"length(rtrim({x}))"


def LENGTH(m: str, x: str) -> str:
    return f"LENGTH({x})" if m == "leg" else f"length({x})"


def GREATEST(m: str, *a: str) -> str:
    if m == "leg":
        return f"GREATEST({', '.join(a)})"
    if nv(m, "greatest"):
        return f"max({', '.join(a)})"
    if len(a) == 2:
        return f"COALESCE(max({a[0]}, {a[1]}), {a[0]}, {a[1]})"
    assert len(a) == 3
    x, y, z = a
    return f"COALESCE(max({x}, {y}, {z}), max({x}, {y}), max({x}, {z}), max({y}, {z}), {x}, {y}, {z})"


def LEAST(m: str, *a: str) -> str:
    if m == "leg":
        return f"LEAST({', '.join(a)})"
    if nv(m, "greatest"):
        return f"min({', '.join(a)})"
    assert len(a) == 2
    return f"COALESCE(min({a[0]}, {a[1]}), {a[0]}, {a[1]})"


def DECODE(m: str, x: str, pairs: list[tuple[str | None, str]], default: str | None = None,
           text: bool = True) -> str:
    """DECODE: NULL matches NULL; string search values compare case-insensitively."""
    if m == "leg":
        args = [x]
        for v, res in pairs:
            args += ["NULL" if v is None else (f"'{v}'" if text else v), res]
        if default is not None:
            args.append(default)
        return "DECODE(" + ", ".join(args) + ")"
    if nv(m, "decode"):
        whens = " ".join(f"WHEN {'NULL' if v is None else (repr_s(v) if text else v)} THEN {res}"
                         for v, res in pairs)
        return f"CASE {x} {whens}" + (f" ELSE {default}" if default is not None else "") + " END"
    whens = []
    for v, res in pairs:
        if v is None:
            whens.append(f"WHEN {x} IS NULL THEN {res}")
        elif text:
            whens.append(f"WHEN upper(rtrim({x})) = '{v.upper()}' THEN {res}")
        else:
            whens.append(f"WHEN {x} = {v} THEN {res}")
    return "CASE " + " ".join(whens) + (f" ELSE {default}" if default is not None else "") + " END"


def repr_s(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def TOCHAR(m: str, d: str, fmt: str) -> str:
    if m == "leg":
        return f"TO_CHAR({d}, '{fmt}')"
    simple = {"DD.MM.YYYY": "%d.%m.%Y", "YYYY-MM": "%Y-%m", "MM.YYYY": "%m.%Y", "YYYY": "%Y",
              "HH24": "%H", "DD.MM": "%d.%m", "YYYY-MM-DD": "%Y-%m-%d", "MM": "%m"}
    if fmt in simple:
        return f"strftime('{simple[fmt]}', {d})"
    if fmt == 'YYYY"-Q"Q':
        return f"strftime('%Y', {d}) || '-Q' || ((CAST(strftime('%m', {d}) AS INTEGER) + 2) / 3)"
    raise ValueError(fmt)


def NUM2(m: str, x: str) -> str:
    """TO_CHAR(x, 'FM999999990.00')"""
    return f"TO_CHAR({x}, 'FM999999990.00')" if m == "leg" else f"printf('%.2f', {x})"


def TODATE(m: str, s: str) -> str:
    if m == "leg":
        return f"TO_DATE({s}, 'DD.MM.YYYY')"
    return f"(substr({s}, 7, 4) || '-' || substr({s}, 4, 2) || '-' || substr({s}, 1, 2))"


def TOP(m: str, n: int, pct: bool = False) -> str:
    return (f"TOP {n} PERCENT " if pct else f"TOP {n} ") if m == "leg" else ""


def LIMIT(m: str, n: int) -> str:
    return "" if m == "leg" else f"LIMIT {n}"


def HINT(m: str, text: str = "COMPAT(5)") -> str:
    return f"/*+ {text} */ " if m == "leg" else ""


def ADDMON(m: str, d: str, n: int) -> str:
    """ADD_MONTHS(d, n) for a DATE: the last day of a month maps to the last day of the target month."""
    if m == "leg":
        return f"ADD_MONTHS({d}, {n})"
    if nv(m, "addmonths"):  # read as DATEADD('month', ...)
        return ADDM(m, d, n)
    last_src = f"date({d}, 'start of month', '+1 month', '-1 day')"
    last_dst = f"date({d}, 'start of month', {_months_mod(n, 1)}, '-1 day')"
    return (f"CASE WHEN {d} = {last_src} OR CAST(strftime('%d', {d}) AS INTEGER) > "
            f"CAST(strftime('%d', {last_dst}) AS INTEGER) THEN {last_dst} ELSE date({d}, {_months_mod(n)}) END")


def SUB0(m: str, x: str, ln: str | int) -> str:
    """SUBSTR(x, 0, n): position 0 is read as 1 by the dialect (SQLite would return n - 1 chars)."""
    if m == "leg":
        return f"SUBSTR({x}, 0, {ln})"
    return f"substr({x}, 0, {ln})" if nv(m, "substr0") else f"substr({x}, 1, {ln})"


def DWEEK(m: str, a: str, b: str) -> str:
    """DATEDIFF('week', a, b): Monday boundaries crossed."""
    if m == "leg":
        return f"DATEDIFF('week', {a}, {b})"
    if nv(m, "dweek"):
        return f"CAST((julianday({b}) - julianday({a})) / 7 AS INTEGER)"
    return (f"CAST((julianday(date({b}, '-6 days', 'weekday 1')) - "
            f"julianday(date({a}, '-6 days', 'weekday 1'))) / 7 AS INTEGER)")


def DQTR(m: str, a: str, b: str) -> str:
    """DATEDIFF('quarter', a, b): quarter boundaries crossed."""
    if m == "leg":
        return f"DATEDIFF('quarter', {a}, {b})"
    if nv(m, "dweek"):
        return f"(({DMON('gold', a, b)}) / 3)"

    def qn(d: str) -> str:
        return f"(CAST(strftime('%Y', {d}) AS INTEGER) * 4 + (CAST(strftime('%m', {d}) AS INTEGER) + 2) / 3)"
    return f"({qn(b)} - {qn(a)})"


_WD_NUM = {"MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6, "SUN": 0}


def NEXTDAY(m: str, d: str, day: str) -> str:
    """NEXT_DAY(d, 'MON'): the first such weekday strictly after d."""
    if m == "leg":
        return f"NEXT_DAY({d}, '{day}')"
    if nv(m, "nextday"):
        return f"date({d}, 'weekday {_WD_NUM[day]}')"
    return f"date({d}, '+1 day', 'weekday {_WD_NUM[day]}')"


def LPAD(m: str, x: str, n: int, c: str, num: bool = False) -> str:
    """LPAD(x, n, c): pads on the left, a longer string is cut to its first n characters."""
    if m == "leg":
        return f"LPAD({x}, {n}, '{c}')"
    s = f"CAST({x} AS TEXT)" if num else x
    pad = f"'{c * n}'"
    if nv(m, "lpad"):
        return f"substr({pad} || {s}, -{n}, {n})"
    return f"CASE WHEN length({s}) >= {n} THEN substr({s}, 1, {n}) ELSE substr({pad} || {s}, -{n}, {n}) END"


# ==========================================================================
# The 70 legacy reports. Each function renders the body in mode `m`.
# ==========================================================================

QUERIES: list[tuple[str, str, str, str, tuple[str, ...], object]] = []


def report(slug: str, title: str, dept: str, purpose: str, notes: tuple[str, ...] = ()):
    def deco(fn):
        QUERIES.append((slug, title, dept, purpose, notes, fn))
        return fn
    return deco


REV = "oi.qty * oi.unit_price * (100 - {d}) / 100"


def rev(m: str) -> str:
    return REV.format(d=NVL(m, "oi.discount_pct", "0"))


@report("monthly_channel_revenue", "Выручка по месяцам и каналам продаж, первое полугодие 2024",
        "коммерческий департамент",
        "Помесячная выручка с учётом построчных скидок по каналам продаж. Учитываются оплаченные,\n"
        "отгруженные и доставленные заказы, созданные в первом полугодии 2024 года.",
        ("Канал приводится к верхнему регистру: в старых выгрузках встречаются 'Web' и 'WEB'.",
         "Стоимость доставки в выручку не входит (см. отчёт R43)."))
def q01(m):
    return f"""SELECT {TM(m, 'o.created_at')} AS month_start,
       UPPER(o.channel) AS channel,
       COUNT(DISTINCT o.id) AS orders_cnt,
       SUM(oi.qty) AS items,
       ROUND(SUM({rev(m)}), 2) AS revenue
  FROM orders o
  JOIN order_items oi ON oi.order_id = o.id
 WHERE {SIN(m, 'o.status', ['paid', 'shipped', 'delivered'])}
   -- период: всё первое полугодие (так записано в ТЗ)
   AND {DTB(m, 'o.created_at', '01.01.2024', '30.06.2024')}
 GROUP BY {TM(m, 'o.created_at')}, UPPER(o.channel)
 ORDER BY 1, 2"""


@report("product_return_rate", "Доля возвратов по товарам (топ-25)", "служба качества",
        "Товары с наибольшей долей возвращённых единиц среди доставленных и возвращённых заказов.\n"
        "Доля считается в процентах от проданного количества.",
        ("Товары без возвратов тоже попадают в расчёт с нулевой долей.",
         "Раньше отчёт показывал топ-20, сейчас — топ-25."))
def q02(m):
    frm = A(m,
            """  FROM products p,
       (SELECT oi.product_id, SUM(oi.qty) AS sold_qty
          FROM order_items oi, orders o
         WHERE o.id = oi.order_id
           AND {st}
         GROUP BY oi.product_id) s,
       (SELECT r.product_id, SUM(r.qty) AS ret_qty
          FROM returns r
         GROUP BY r.product_id) rt
 WHERE s.product_id = p.id
   AND rt.product_id(+) = p.id
   AND s.sold_qty >= 5""",
            """  FROM products p
  JOIN (SELECT oi.product_id, SUM(oi.qty) AS sold_qty
          FROM order_items oi JOIN orders o ON o.id = oi.order_id
         WHERE {st}
         GROUP BY oi.product_id) s ON s.product_id = p.id
  LEFT JOIN (SELECT r.product_id, SUM(r.qty) AS ret_qty
          FROM returns r
         GROUP BY r.product_id) rt ON rt.product_id = p.id
 WHERE s.sold_qty >= 5""").format(st=SIN(m, "o.status", ["DELIVERED", "RETURNED"]))
    return f"""SELECT {TOP(m, 25)}p.id,
       RTRIM(p.sku) AS sku,
       p.name,
       s.sold_qty,
       {NVL(m, 'rt.ret_qty', '0')} AS ret_qty,
       -- доля в процентах; старый вариант с ROUND(..., 0) убран в 2021 г.
       {DIV(m, '100 * ' + NVL(m, 'rt.ret_qty', '0'), 's.sold_qty')} AS return_pct
{frm}
 ORDER BY {DESC(m, 'return_pct')}, p.id
{LIMIT(m, 25)}"""


@report("customers_without_phone", "Клиенты без телефона по городам", "колл-центр",
        "Сколько активных клиентов в каждом городе не оставили номер телефона и какова их доля.",
        ("Колл-центр использует отчёт для планирования рассылок по e-mail.",
         "Признак активности хранится как Y/N, регистр в старых записях не выдержан."))
def q03(m):
    ph = E(m, "c.phone")
    nophone = f"SUM(CASE WHEN {ph} IS NULL THEN 1 ELSE 0 END)"
    return f"""SELECT c.city,
       COUNT(*) AS customers,
       COUNT({ph}) AS with_phone,
       {nophone} AS without_phone,
       -- доля без телефона, проценты с одним знаком
       ROUND({DIV(m, '100 * ' + nophone, 'COUNT(*)')}, 1) AS pct_without
  FROM customers c
 WHERE {S(m, 'c.is_active', '=', 'Y')}
 GROUP BY c.city
 ORDER BY c.city"""


@report("new_customers_contacts", "Контакты новых клиентов I квартала 2024", "колл-центр",
        "Последние 40 зарегистрированных в I квартале 2024 г. клиентов со строкой контакта для\n"
        "обзвона. Сегмент выводится в верхнем регистре.",
        ("Строка контакта собирается конкатенацией; если телефона нет — строка всё равно выводится.",))
def q04(m):
    seg = NVL(m, "UPPER(" + E(m, "c.segment") + ")", "'НЕ ЗАДАН'")
    return f"""SELECT {TOP(m, 40)}c.id,
       {CAT(m, 'c.full_name', "', '", 'c.city', "', тел. '", E(m, 'c.phone'))} AS contact_line,
       {seg} AS segment,
       {TOCHAR(m, 'c.registered_at', 'DD.MM.YYYY')} AS reg_date
  FROM customers c
 WHERE {DT(m, 'c.registered_at', '>=', '01.01.2024')}
   AND {DT(m, 'c.registered_at', '<', '01.04.2024')}
 ORDER BY {DESC(m, 'c.registered_at')}, c.id
{LIMIT(m, 40)}"""


@report("delivery_sla_by_channel", "Сроки доставки по каналам", "логистика",
        "Средний и максимальный срок доставки в днях от создания заказа до доставки, число\n"
        "заказов, доставленных дольше трёх дней. Заказы с 1 июля 2023 г.",
        ("Срок считается функцией DATEDIFF по календарным дням.",
         "Самовывоз из магазина даёт срок 0 дней и тоже учитывается."))
def q05(m):
    d = DDAY(m, "o.created_at", "o.delivered_on")
    return f"""SELECT UPPER(o.channel) AS channel,
       COUNT(*) AS delivered_orders,
       ROUND(AVG({d}), 2) AS avg_days,
       MAX({d}) AS max_days,
       SUM({IIF(m, d + ' > 3', '1', '0')}) AS late_orders
  FROM orders o
 WHERE {S(m, 'o.status', '=', 'Delivered')}
   AND o.delivered_on IS NOT NULL
   AND {DT(m, 'o.created_at', '>=', '01.07.2023')}
 GROUP BY UPPER(o.channel)
 ORDER BY 1"""


@report("warranty_expiry_2025h1", "Окончание гарантии в первом полугодии 2025", "сервисный центр",
        "Сколько проданных единиц выходит из гарантии по месяцам первого полугодия 2025 г. в разрезе\n"
        "корневых категорий. Срок гарантии зависит от корневой категории товара.",
        ("Гарантия отсчитывается от даты доставки.",
         "Сроки: кухня 12 мес., климат 24, уход 18, уборка 24, прочее 6."))
def q06(m):
    months = DECODE(m, "c.parent_id", [(None, "6"), ("1", "12"), ("6", "24"), ("10", "18"), ("13", "24")],
                    "6", text=False)
    exp = ADDM(m, "o.delivered_on", months)
    root = NVL(m, "pc.name", "c.name")
    frm = A(m, """  FROM orders o, order_items oi, products p, categories c, categories pc
 WHERE oi.order_id = o.id
   AND p.id = oi.product_id
   AND c.id = p.category_id
   AND pc.id(+) = c.parent_id""", """  FROM orders o
  JOIN order_items oi ON oi.order_id = o.id
  JOIN products p ON p.id = oi.product_id
  JOIN categories c ON c.id = p.category_id
  LEFT JOIN categories pc ON pc.id = c.parent_id
 WHERE 1 = 1""")
    return f"""SELECT {TM(m, exp)} AS expiry_month,
       {root} AS root_category,
       COUNT(*) AS lines_cnt,
       SUM(oi.qty) AS units
{frm}
   AND o.delivered_on IS NOT NULL
   AND {exp} BETWEEN {DL(m, '01.01.2025')} AND {DL(m, '30.06.2025')}
 GROUP BY {TM(m, exp)}, {root}
 ORDER BY 1, 2"""


@report("promo_usage", "Использование промокодов", "отдел маркетинга",
        "Сколько заказов оформлено по каждому промокоду, сколько из них в период действия акции\n"
        "и сколько кодов введено с лишними пробелами.",
        ("Отменённые заказы не учитываются.",
         "Промокод в заказе вводит сам покупатель, поэтому регистр произвольный."))
def q07(m):
    inper = A(m, "o.created_at BETWEEN p.valid_from AND NVL(p.valid_to, '31.12.2099')",
              "o.created_at BETWEEN p.valid_from || ' 00:00:00' AND COALESCE(p.valid_to, '2099-12-31') || ' 00:00:00'",
              "datelit", "date(o.created_at) BETWEEN p.valid_from AND COALESCE(p.valid_to, '2099-12-31')")
    return f"""SELECT p.code,
       p.title,
       p.discount_pct,
       COUNT(o.id) AS orders_cnt,
       SUM({IIF(m, inper, '1', '0')}) AS in_period,
       SUM({IIF(m, LEN(m, 'o.promo_code') + ' <> ' + LENGTH(m, 'o.promo_code'), '1', '0')}) AS with_spaces
  FROM promotions p
  JOIN orders o ON {SJ(m, 'o.promo_code', 'p.code')}
 WHERE {S(m, 'o.status', '<>', 'cancelled')}
 GROUP BY p.code, p.title, p.discount_pct
 ORDER BY {DESC(m, '4')}, 1"""


@report("weekday_profile", "Профиль заказов по дням недели, I квартал 2024", "аналитика",
        "Число интернет-заказов (сайт и приложение), средний чек и вечерние заказы по дням недели.",
        ("День недели — номер от 1 до 7.",))
def q08(m):
    return f"""SELECT {DOW(m, 'o.created_at')} AS dow,
       COUNT(*) AS orders_cnt,
       ROUND(AVG(t.total), 2) AS avg_check,
       SUM({IIF(m, EXTRACT(m, 'HOUR', 'o.created_at') + ' >= 18', '1', '0')}) AS evening_orders
  FROM orders o
  JOIN (SELECT order_id, SUM(qty * unit_price) AS total
          FROM order_items
         GROUP BY order_id) t ON t.order_id = o.id
 WHERE {SIN(m, 'UPPER(o.channel)', ['WEB', 'APP'])}
   AND {DTB(m, 'o.created_at', '01.01.2024', '31.03.2024')}
 GROUP BY {DOW(m, 'o.created_at')}
 ORDER BY 1"""


@report("weekly_orders_running", "Заказы по неделям с нарастающим итогом, II квартал 2024", "аналитика",
        "Число неотменённых заказов по календарным неделям и нарастающий итог с начала квартала.")
def q09(m):
    w = TW(m, "o.created_at")
    return f"""SELECT {w} AS week_start,
       COUNT(*) AS orders_cnt,
       SUM(COUNT(*)) OVER (ORDER BY {w}) AS running_cnt
  FROM orders o
 WHERE {DT(m, 'o.created_at', '>=', '01.04.2024')}
   AND {DT(m, 'o.created_at', '<', '01.07.2024')}
   AND {SIN(m, 'o.status', ['cancelled'], neg=True)}
 GROUP BY {w}
 ORDER BY 1"""


@report("repeat_purchase_3m", "Повторные покупки в течение трёх месяцев, когорты 2023", "CRM",
        "Для клиентов, сделавших первый неотменённый заказ в 2023 г., — доля тех, кто сделал ещё\n"
        "хотя бы один неотменённый заказ в течение трёх месяцев после первого.",
        ("Окно — ровно три календарных месяца от момента первого заказа (DATEADD).",))
def q10(m):
    lim = ADDM(m, "f2.first_at", 3, datetime_=True)
    st = S(m, "o.status", "<>", "cancelled")
    st2 = S(m, "o2.status", "<>", "cancelled")
    rep = f"""(SELECT DISTINCT o2.customer_id
          FROM orders o2, f f2
         WHERE o2.customer_id = f2.customer_id
           AND o2.created_at > f2.first_at
           AND o2.created_at <= {lim}
           AND {st2})"""
    frm = A(m, f"""  FROM f,
       {rep} r
 WHERE r.customer_id(+) = f.customer_id""", f"""  FROM f
  LEFT JOIN {rep} r ON r.customer_id = f.customer_id
 WHERE 1 = 1""")
    return f"""WITH f AS (
  SELECT o.customer_id, MIN(o.created_at) AS first_at
    FROM orders o
   WHERE {st}
   GROUP BY o.customer_id
)
SELECT {TM(m, 'f.first_at')} AS cohort,
       COUNT(*) AS new_customers,
       COUNT(r.customer_id) AS repeaters,
       ROUND({DIV(m, '100 * COUNT(r.customer_id)', 'COUNT(*)')}, 1) AS repeat_pct
{frm}
   AND {DT(m, 'f.first_at', '>=', '01.01.2023')}
   AND {DT(m, 'f.first_at', '<', '01.01.2024')}
 GROUP BY {TM(m, 'f.first_at')}
 ORDER BY 1"""


@report("top_customers_2023", "Лучшие клиенты 2023 года (верхние 5 %)", "CRM",
        "Верхние 5 % клиентов по сумме платежей, проведённых в 2023 году.",
        ("Считаются платежи, а не заказы: частичные оплаты суммируются.",))
def q11(m):
    base = f"""SELECT c.id, c.full_name, {NVL(m, 'UPPER(' + E(m, 'c.segment') + ')', "'-'")} AS segment,
         ROUND(SUM(p.amount), 2) AS paid_2023
    FROM customers c
    JOIN orders o ON o.customer_id = c.id
    JOIN payments p ON p.order_id = o.id
   WHERE {DTB(m, 'p.paid_at', '01.01.2023', '31.12.2023')}
   GROUP BY c.id, c.full_name, c.segment"""
    if m == "leg":
        return """SELECT TOP 5 PERCENT c.id, c.full_name, NVL(UPPER(c.segment), '-') AS segment,
       ROUND(SUM(p.amount), 2) AS paid_2023
  FROM customers c
  JOIN orders o ON o.customer_id = c.id
  JOIN payments p ON p.order_id = o.id
 WHERE p.paid_at BETWEEN '01.01.2023' AND '31.12.2023'
 GROUP BY c.id, c.full_name, c.segment
 ORDER BY 4 DESC, c.id"""
    cnt = "COUNT(*) * 5 / 100" if nv(m, "toppct") else "(COUNT(*) * 5 + 99) / 100"
    return f"""WITH base AS (
  {base}
)
SELECT id, full_name, segment, paid_2023
  FROM base
 ORDER BY {DESC(m, 'paid_2023')}, id
 LIMIT (SELECT {cnt} FROM base)"""


@report("store_stock_top", "Крупнейшие остатки пылесосов в действующих магазинах", "закупки",
        "30 позиций с наибольшим учётным остатком пылесосов и роботов-пылесосов в открытых магазинах.",
        ("Остаток NULL означает, что позицию не пересчитывали.",
         "Длина артикула выводится для контроля качества справочника."))
def q12(m):
    return f"""SELECT {TOP(m, 30)}s.name AS store,
       RTRIM(p.sku) AS sku,
       {LEN(m, 'p.sku')} AS sku_len,
       st.qty,
       st.counted_on
  FROM stock st
  JOIN stores s ON s.id = st.store_id
  JOIN products p ON p.id = st.product_id
 WHERE s.closed_on IS NULL
   AND p.category_id IN (14, 15)
 ORDER BY {DESC(m, 'st.qty')}, s.id, p.id
{LIMIT(m, 30)}"""


@report("sku_format_check", "Контроль формата артикулов", "справочник НСИ",
        "Товары, у которых длина артикула отличается от стандартных 9 символов (XXX-YYNNN).\n"
        "Выводится также бренд, восстановленный по префиксу артикула.",
        ("Завершающие пробелы в артикулах появились при импорте из 1С в 2020 г.",))
def q13(m):
    brand = DECODE(m, "SUBSTR(p.sku, 1, 3)", [(c, f"'{b}'") for b, c in BRANDS], "'?'")
    return f"""SELECT p.id,
       {CAT(m, "'['", 'p.sku', "']'")} AS sku_raw,
       {LEN(m, 'p.sku')} AS len_sku,
       {LENGTH(m, 'p.sku')} AS full_len,
       p.brand,
       {brand} AS brand_by_sku
  FROM products p
 WHERE {LEN(m, 'p.sku')} <> 9
 ORDER BY p.id"""


@report("return_reasons", "Причины возвратов", "служба качества",
        "Возвраты в разрезе причин: число возвратов, единиц, сумма и число возвратов с суммой.",
        ("Коды причин вводят операторы пунктов выдачи, регистр не выдержан.",
         "Пустой код означает, что причина не указана."))
def q14(m):
    x = E(m, "r.reason_code")
    lab = DECODE(m, x, [("DEFECT", "'Брак'"), ("SIZE", "'Не подошёл размер'"),
                        ("CHANGED_MIND", "'Передумал'"), ("DAMAGED", "'Повреждён при доставке'"),
                        (None, "'Причина не указана'")], "'Прочее'")
    return f"""SELECT {lab} AS reason,
       COUNT(*) AS returns_cnt,
       SUM(r.qty) AS items,
       ROUND(SUM({NVL(m, 'r.refund', '0')}), 2) AS refund_sum,
       COUNT(r.refund) AS with_refund
  FROM returns r
 GROUP BY {lab}
 ORDER BY {DESC(m, '2')}, 1"""


@report("customer_last_activity", "Последняя активность клиентов из Казани и Самары", "CRM",
        "30 клиентов с самой поздней активностью: активность — более поздняя из дат последнего\n"
        "заказа и последней отгрузки.",
        ("Если отгрузок не было, активность определяется по заказу.",))
def q15(m):
    act = GREATEST(m, "MAX(o.created_at)", "MAX(o.shipped_at)")
    return f"""SELECT {TOP(m, 30)}c.id,
       c.full_name,
       c.city,
       MAX(o.created_at) AS last_order_at,
       MAX(o.shipped_at) AS last_shipped_at,
       {act} AS last_activity
  FROM customers c
  JOIN orders o ON o.customer_id = c.id
 WHERE c.city IN ('Казань', 'Самара')
   AND {DT(m, 'o.created_at', '<', '01.01.2024')}
 GROUP BY c.id, c.full_name, c.city
 ORDER BY {DESC(m, 'last_activity')}, c.id
{LIMIT(m, 30)}"""


@report("age_groups_first_order", "Возрастные группы на момент первого заказа", "аналитика",
        "Распределение клиентов по возрастным группам на момент первого заказа и средний чек\n"
        "первого заказа в группе.",
        ("Возраст считается как DATEDIFF в годах между датой рождения и датой первого заказа.",
         "Клиенты без даты рождения выделены в отдельную группу."))
def q16(m):
    age = DYEAR(m, "c.birth_date", "f.first_at")
    return f"""WITH f AS (
  SELECT o.customer_id, MIN(o.created_at) AS first_at, MIN(o.id) AS first_id
    FROM orders o
   GROUP BY o.customer_id
),
t AS (
  SELECT order_id, SUM(qty * unit_price) AS total
    FROM order_items
   GROUP BY order_id
)
SELECT CASE WHEN c.birth_date IS NULL THEN 'нет данных'
            WHEN {age} < 25 THEN '18-24'
            WHEN {age} < 35 THEN '25-34'
            WHEN {age} < 45 THEN '35-44'
            WHEN {age} < 55 THEN '45-54'
            ELSE '55+' END AS age_group,
       COUNT(*) AS customers,
       ROUND(AVG(t.total), 2) AS avg_first_check
  FROM f
  JOIN customers c ON c.id = f.customer_id
  JOIN t ON t.order_id = f.first_id
 GROUP BY 1
 ORDER BY 1"""


@report("returns_by_month", "Возвраты по месяцам 2024 года", "служба качества",
        "Число возвратов, единиц и сумм по месяцам даты возврата начиная с 2024 г.; отдельно —\n"
        "возвраты в пределах месяца после доставки.",
        ("Дата возврата хранится текстом в формате ДД.ММ.ГГГГ (наследие старого модуля).",))
def q17(m):
    rd = TODATE(m, "r.return_date_txt")
    lim = ADDM(m, "o.delivered_on", 1)
    return f"""SELECT {TM(m, rd)} AS month_start,
       COUNT(*) AS returns_cnt,
       SUM(r.qty) AS items,
       ROUND(SUM({NVL(m, 'r.refund', '0')}), 2) AS refund_sum,
       SUM({IIF(m, rd + ' <= ' + lim, '1', '0')}) AS within_month
  FROM returns r
  JOIN orders o ON o.id = r.order_id
 WHERE {rd} >= {DL(m, '01.01.2024')}
 GROUP BY {TM(m, rd)}
 ORDER BY 1"""


@report("returns_since_may", "Возвраты нескольких единиц с мая 2024", "служба качества",
        "Список крупных возвратов (больше одной единицы, сумма свыше 30 000 руб.) с 1 мая 2024 г.",
        ("Фильтр по дате написан по текстовому полю даты возврата.",))
def q18(m):
    cond = A(m, "r.return_date_txt >= '01.05.2024'", "r.return_date_txt >= '01.05.2024'", "strcmp",
             TODATE(m, "r.return_date_txt") + " >= '2024-05-01'")
    order = A(m, "r.return_date_txt", "r.return_date_txt", "strcmp", TODATE(m, "r.return_date_txt"))
    return f"""SELECT r.id,
       r.order_id,
       r.return_date_txt,
       r.qty,
       {NVL(m, 'r.refund', '0')} AS refund
  FROM returns r
 WHERE {cond}
   AND r.qty > 1
   AND r.refund > 30000
 ORDER BY {order}, r.id"""


@report("payment_reconciliation", "Сверка оплат с суммами заказов", "бухгалтерия",
        "40 заказов с наибольшим расхождением между суммой платежей и суммой заказа (позиции со\n"
        "скидками плюс доставка). Выводятся также способы оплаты.",
        ("Расхождения до 1 копейки не показываются.",))
def q19(m):
    if m == "leg":
        methods = "LISTAGG(DISTINCT UPPER(pm.method), '+') WITHIN GROUP (ORDER BY UPPER(pm.method))"
        pay = f"""(SELECT pm.order_id, SUM(pm.amount) AS paid,
               {methods} AS methods
          FROM payments pm
         GROUP BY pm.order_id)"""
    else:
        pay = """(SELECT pm.order_id, SUM(pm.amount) AS paid,
               (SELECT group_concat(mm, '+') FROM (SELECT DISTINCT upper(pm2.method) AS mm
                                                     FROM payments pm2
                                                    WHERE pm2.order_id = pm.order_id
                                                    ORDER BY 1)) AS methods
          FROM payments pm
         GROUP BY pm.order_id)"""
    diff = "ROUND(pp.paid - (t.total + " + NVL(m, "o.delivery_fee", "0") + "), 2)"
    return f"""SELECT {TOP(m, 40)}o.id,
       UPPER(o.status) AS status,
       ROUND(t.total + {NVL(m, 'o.delivery_fee', '0')}, 2) AS order_sum,
       ROUND(pp.paid, 2) AS paid,
       {diff} AS diff,
       pp.methods
  FROM orders o
  JOIN (SELECT oi.order_id, SUM({rev(m)}) AS total
          FROM order_items oi
         GROUP BY oi.order_id) t ON t.order_id = o.id
  JOIN {pay} pp ON pp.order_id = o.id
 WHERE ABS({diff}) > 0.01
 ORDER BY {DESC(m, 'ABS(' + diff + ')')}, o.id
{LIMIT(m, 40)}"""


@report("brands_by_category", "Бренды в категориях действующего ассортимента", "закупки",
        "По каждой категории — число товаров и брендов, список брендов и ценовой диапазон для\n"
        "товаров, не снятых с продажи на 30.06.2024.")
def q20(m):
    lst = ("LISTAGG(DISTINCT p.brand, ', ') WITHIN GROUP (ORDER BY p.brand)" if m == "leg" else
           "replace(group_concat(DISTINCT p.brand ORDER BY p.brand), ',', ', ')")
    return f"""SELECT c.name AS category,
       COUNT(*) AS products,
       COUNT(DISTINCT p.brand) AS brands,
       {lst} AS brand_list,
       MIN(p.price) AS min_price,
       MAX(p.price) AS max_price,
       ROUND(AVG(p.price), 2) AS avg_price
  FROM products p
  JOIN categories c ON c.id = p.category_id
 WHERE p.discontinued_on IS NULL
    OR p.discontinued_on > {DL(m, '30.06.2024')}
 GROUP BY c.name
 ORDER BY c.name"""


@report("median_check_by_channel", "Медианный чек по каналам, 2024", "аналитика",
        "Медиана и среднее суммы доставленного заказа (позиции со скидками) по каналам за 2024 г.")
def q21(m):
    if m == "leg":
        return """SELECT UPPER(o.channel) AS channel,
       COUNT(*) AS orders_cnt,
       MEDIAN(t.total) AS median_check,
       ROUND(AVG(t.total), 2) AS avg_check
  FROM orders o
  JOIN (SELECT oi.order_id, ROUND(SUM(oi.qty * oi.unit_price * (100 - NVL(oi.discount_pct, 0)) / 100), 2) AS total
          FROM order_items oi
         GROUP BY oi.order_id) t ON t.order_id = o.id
 WHERE o.status = 'delivered'
   AND o.created_at >= '01.01.2024'
 GROUP BY UPPER(o.channel)
 ORDER BY 1"""
    return f"""WITH t AS (
  SELECT UPPER(o.channel) AS channel, tt.total
    FROM orders o
    JOIN (SELECT oi.order_id, ROUND(SUM({rev(m)}), 2) AS total
            FROM order_items oi
           GROUP BY oi.order_id) tt ON tt.order_id = o.id
   WHERE {S(m, 'o.status', '=', 'delivered')}
     AND {DT(m, 'o.created_at', '>=', '01.01.2024')}
),
r AS (
  SELECT channel, total,
         ROW_NUMBER() OVER (PARTITION BY channel ORDER BY total) AS rn,
         COUNT(*) OVER (PARTITION BY channel) AS cnt
    FROM t
)
SELECT channel,
       MAX(cnt) AS orders_cnt,
       AVG(CASE WHEN rn IN ((cnt + 1) / 2, (cnt + 2) / 2) THEN total END) AS median_check,
       ROUND(AVG(total), 2) AS avg_check
  FROM r
 GROUP BY channel
 ORDER BY 1"""


@report("top_margin_per_category", "Три самых маржинальных товара в каждой категории", "закупки",
        "Ранжирование действующих товаров категории по абсолютной марже (цена минус себестоимость).",
        ("Себестоимость заполнена не у всех товаров.",))
def q22(m):
    return f"""SELECT * FROM (
  SELECT p.category_id,
         p.id,
         p.name,
         p.price,
         p.cost,
         ROUND(p.price - p.cost, 2) AS margin,
         RANK() OVER (PARTITION BY p.category_id ORDER BY {DESC(m, 'p.price - p.cost')}) AS rnk
    FROM products p
   WHERE p.discontinued_on IS NULL
) x
 WHERE x.rnk <= 3
 ORDER BY x.category_id, x.rnk, x.id"""


@report("store_monthly_growth", "Помесячная динамика выручки магазинов, 2024", "розница",
        "Выручка каждого магазина по месяцам первого полугодия 2024 г. и прирост к предыдущему\n"
        "месяцу в процентах.",
        ("Учитываются только заказы, оформленные в магазинах (store_id заполнен).",))
def q23(m):
    return f"""WITH mr AS (
  SELECT o.store_id,
         {TM(m, 'o.created_at')} AS month_start,
         ROUND(SUM({rev(m)}), 2) AS revenue
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.id
   WHERE o.store_id IS NOT NULL
     AND {SIN(m, 'o.status', ['cancelled', 'returned'], neg=True)}
     AND {DT(m, 'o.created_at', '>=', '01.01.2024')}
   GROUP BY o.store_id, {TM(m, 'o.created_at')}
)
SELECT s.name AS store,
       mr.month_start,
       mr.revenue,
       LAG(mr.revenue) OVER (PARTITION BY mr.store_id ORDER BY mr.month_start) AS prev_revenue,
       ROUND((mr.revenue - LAG(mr.revenue) OVER (PARTITION BY mr.store_id ORDER BY mr.month_start)) * 100
             / LAG(mr.revenue) OVER (PARTITION BY mr.store_id ORDER BY mr.month_start), 1) AS growth_pct
  FROM mr
  JOIN stores s ON s.id = mr.store_id
 ORDER BY mr.store_id, mr.month_start"""


@report("vip_contacts_compat", "Контакты VIP-клиентов (режим совместимости)", "CRM",
        "30 VIP-клиентов, упорядоченных по дате рождения, со строкой контакта и средним числом\n"
        "единиц в заказе. Отчёт перенесён из версии 5 без переписывания и работает в режиме COMPAT(5).",
        ("Не убирать подсказку: сверка с бумажными архивами 2016 года.",))
def q24(m):
    return f"""SELECT {HINT(m)}{TOP(m, 30)}c.id,
       {CAT(m, 'c.full_name', "' / '", E(m, 'c.phone'), compat=True)} AS contact,
       c.birth_date,
       COUNT(DISTINCT o.id) AS orders_cnt,
       {DIV(m, 'SUM(oi.qty)', 'COUNT(DISTINCT o.id)', compat=True)} AS items_per_order
  FROM customers c
  JOIN orders o ON o.customer_id = c.id
  JOIN order_items oi ON oi.order_id = o.id
 WHERE {S(m, 'c.segment', '=', 'VIP')}
 GROUP BY c.id, c.full_name, c.phone, c.birth_date
 ORDER BY {DESC(m, 'c.birth_date', compat=True)}, c.id
{LIMIT(m, 30)}"""


@report("delivered_by_city", "Клиенты с доставленными заказами 2024 г. по городам", "логистика",
        "Для активных клиентов каждого города: всего клиентов, сколько из них получили хотя бы\n"
        "один доставленный заказ, оформленный в 2024 г., и сколько таких заказов.",
        ("Клиенты без доставленных заказов тоже учитываются в графе customers.",))
def q25(m):
    if m == "leg":
        frm = """  FROM customers c, orders o
 WHERE o.customer_id(+) = c.id
   AND o.status(+) = 'DELIVERED'
   AND o.created_at(+) >= '01.01.2024'
   AND c.is_active = 'Y'"""
    elif nv(m, "outer"):
        frm = f"""  FROM customers c
  LEFT JOIN orders o ON o.customer_id = c.id
 WHERE {S(m, 'o.status', '=', 'DELIVERED')}
   AND {DT(m, 'o.created_at', '>=', '01.01.2024')}
   AND {S(m, 'c.is_active', '=', 'Y')}"""
    else:
        frm = f"""  FROM customers c
  LEFT JOIN orders o ON o.customer_id = c.id
                    AND {S(m, 'o.status', '=', 'DELIVERED')}
                    AND {DT(m, 'o.created_at', '>=', '01.01.2024')}
 WHERE {S(m, 'c.is_active', '=', 'Y')}"""
    return f"""SELECT c.city,
       COUNT(DISTINCT c.id) AS customers,
       COUNT(DISTINCT o.customer_id) AS with_delivered,
       COUNT(o.id) AS delivered_orders
{frm}
 GROUP BY c.city
 ORDER BY c.city"""


@report("phone_by_segment", "Наличие телефона по сегментам", "колл-центр",
        "Число клиентов в разрезе сегмента и признака наличия телефона.",
        ("Сегмент приводится к верхнему регистру, отсутствующий сегмент показывается как '(нет)'.",))
def q26(m):
    seg = NVL(m, "UPPER(" + E(m, "c.segment") + ")", "'(нет)'")
    flag = NVL2(m, E(m, "c.phone"), "'есть'", "'нет'")
    return f"""SELECT {seg} AS segment,
       {flag} AS has_phone,
       COUNT(*) AS customers,
       SUM({IIF(m, S(m, 'c.is_active', '=', 'Y'), '1', '0')}) AS active
  FROM customers c
 GROUP BY {seg}, {flag}
 ORDER BY 1, 2"""


@report("order_comments", "Комментарии к заказам по месяцам 2024", "клиентский сервис",
        "Сколько заказов содержит комментарий покупателя. Три варианта подсчёта оставлены для\n"
        "сопоставимости с отчётами прошлых лет.",
        ("Вариант v1 исторически всегда давал 0 — так и должно остаться.",))
def q27(m):
    v1 = A(m, "o.comment <> ''", "NULLIF(o.comment, '') <> NULL", "empty", "o.comment <> ''")
    return f"""SELECT {TOCHAR(m, 'o.created_at', 'YYYY-MM')} AS ym,
       COUNT(*) AS orders_cnt,
       SUM(CASE WHEN {v1} THEN 1 ELSE 0 END) AS with_comment_v1,
       SUM(CASE WHEN {LEN(m, E(m, 'o.comment'))} > 0 THEN 1 ELSE 0 END) AS with_comment_v2,
       COUNT({E(m, 'o.comment')}) AS with_comment_v3
  FROM orders o
 WHERE {DT(m, 'o.created_at', '>=', '01.01.2024')}
 GROUP BY {TOCHAR(m, 'o.created_at', 'YYYY-MM')}
 ORDER BY 1"""


@report("top_rated_products", "Товары с лучшими оценками (верхние 10 %)", "маркетинг",
        "Верхние 10 % товаров по средней оценке среди товаров, у которых не меньше пяти оценок.",
        ("Отзывы без оценки в среднем не участвуют.",))
def q28(m):
    if m == "leg":
        return """SELECT TOP 10 PERCENT p.id,
       p.name,
       COUNT(rv.rating) AS ratings,
       ROUND(AVG(rv.rating), 3) AS avg_rating
  FROM products p
  JOIN reviews rv ON rv.product_id = p.id
 GROUP BY p.id, p.name
HAVING COUNT(rv.rating) >= 5
 ORDER BY AVG(rv.rating) DESC, p.id"""
    cnt = "COUNT(*) * 10 / 100" if nv(m, "toppct") else "(COUNT(*) * 10 + 99) / 100"
    return f"""WITH base AS (
  SELECT p.id, p.name, COUNT(rv.rating) AS ratings, ROUND(AVG(rv.rating), 3) AS avg_rating,
         AVG(rv.rating) AS avg_raw
    FROM products p
    JOIN reviews rv ON rv.product_id = p.id
   GROUP BY p.id, p.name
  HAVING COUNT(rv.rating) >= 5
)
SELECT id, name, ratings, avg_rating
  FROM base
 ORDER BY {DESC(m, 'avg_raw')}, id
 LIMIT (SELECT {cnt} FROM base)"""


@report("weekend_share_weekly", "Доля заказов выходного дня по неделям, май–июнь 2024", "аналитика",
        "По каждой неделе: всего заказов и доля заказов, сделанных в субботу и воскресенье.")
def q29(m):
    w = TW(m, "o.created_at")
    wk = f"SUM(CASE WHEN {DOW(m, 'o.created_at')} IN (6, 7) THEN 1 ELSE 0 END)"
    return f"""SELECT {w} AS week_start,
       COUNT(*) AS orders_cnt,
       {wk} AS weekend_orders,
       {DIV(m, '100 * ' + wk, 'COUNT(*)')} AS weekend_pct
  FROM orders o
 WHERE {DT(m, 'o.created_at', '>=', '29.04.2024')}
   AND {DT(m, 'o.created_at', '<', '01.07.2024')}
 GROUP BY {w}
 ORDER BY 1"""


@report("churn_months", "Давность последней покупки", "CRM",
        "Распределение клиентов по числу месяцев от последнего неотменённого заказа до 30.06.2024.",
        ("Месяцы считаются функцией DATEDIFF('month', ...).",))
def q30(m):
    mon = DMON(m, "MAX(o.created_at)", DL(m, "30.06.2024"))
    return f"""SELECT CASE WHEN x.months = 0 THEN '0'
            WHEN x.months = 1 THEN '1'
            WHEN x.months = 2 THEN '2'
            WHEN x.months <= 5 THEN '3-5'
            WHEN x.months <= 11 THEN '6-11'
            ELSE '12+' END AS bucket,
       COUNT(*) AS customers,
       MIN(x.months) AS min_months
  FROM (SELECT o.customer_id, {mon} AS months
          FROM orders o
         WHERE {S(m, 'o.status', '<>', 'CANCELLED')}
         GROUP BY o.customer_id) x
 GROUP BY 1
 ORDER BY 3"""


@report("store_codes_summary", "Сводка заказов по кодам магазинов", "розница",
        "Число заказов 2024 г. и сумма доставки по кодам магазинов. Интернет-заказы (без магазина)\n"
        "собираются в строку с кодом 'S'.",
        ("Код магазина — буква S и трёхзначный номер с ведущими нулями.",))
def q31(m):
    lp = "LPAD(o.store_id, 3, '0')" if m == "leg" else \
        "CASE WHEN o.store_id IS NULL THEN NULL ELSE substr('000' || o.store_id, -3, 3) END"
    code = CAT(m, "'S'", lp)
    name = NVL(m, "s.name", "'интернет-заказы'")
    frm = A(m, """  FROM orders o, stores s
 WHERE s.id(+) = o.store_id""", """  FROM orders o
  LEFT JOIN stores s ON s.id = o.store_id
 WHERE 1 = 1""")
    return f"""SELECT {code} AS store_code,
       {name} AS store_name,
       COUNT(*) AS orders_cnt,
       ROUND(SUM({NVL(m, 'o.delivery_fee', '0')}), 2) AS fees
{frm}
   AND {DT(m, 'o.created_at', '>=', '01.01.2024')}
 GROUP BY {code}, {name}
 ORDER BY 1"""


@report("basket_size_2023", "Размер корзины по месяцам 2023", "аналитика",
        "Среднее число единиц в заказе и доля строк со скидкой по месяцам 2023 г.")
def q32(m):
    return f"""SELECT {TOCHAR(m, 'o.created_at', 'MM.YYYY')} AS period,
       COUNT(DISTINCT o.id) AS orders_cnt,
       SUM(oi.qty) AS items,
       {DIV(m, 'SUM(oi.qty)', 'COUNT(DISTINCT o.id)')} AS items_per_order,
       {DIV(m, '100 * COUNT(oi.discount_pct)', 'COUNT(*)')} AS discounted_lines_pct
  FROM orders o
  JOIN order_items oi ON oi.order_id = o.id
 WHERE {DTB(m, 'o.created_at', '01.01.2023', '31.12.2023')}
 GROUP BY {TOCHAR(m, 'o.created_at', 'MM.YYYY')}
 ORDER BY MIN(o.created_at)"""


@report("store_lifecycle", "Жизненный цикл магазинов", "розница",
        "Магазины с датами открытия и закрытия, окончанием полугодового испытательного срока,\n"
        "сроком работы в месяцах на 30.06.2024 и датой последнего события.",
        ("Закрытые магазины показываются после действующих.",))
def q33(m):
    return f"""SELECT s.id,
       s.name,
       s.opened_on,
       s.closed_on,
       {LASTDAY(m, 's.opened_on')} AS first_month_end,
       {ADDM(m, 's.opened_on', 6)} AS probation_end,
       {DMON(m, 's.opened_on', NVL(m, 's.closed_on', DL(m, '30.06.2024')))} AS months_open,
       {GREATEST(m, 's.opened_on', 's.closed_on')} AS last_event,
       {NVL(m, E(m, 's.manager'), "'вакансия'")} AS manager,
       {NVL(m, 's.area_m2', '0')} AS area_m2
  FROM stores s
 ORDER BY {DESC(m, 's.closed_on')}, s.id"""


@report("recount_due", "Плановые пересчёты остатков по месяцам", "склад",
        "Пересчёт остатка позиции назначается через квартал после последнего пересчёта. Отчёт\n"
        "показывает, на какой месяц приходятся плановые пересчёты.",
        ("Квартал прибавляется функцией DATEADD('quarter', 1, ...).",))
def q34(m):
    due = ADDM(m, "st.counted_on", 1, unit="quarter")
    return f"""SELECT {TM(m, due)} AS due_month,
       COUNT(*) AS positions,
       COUNT(DISTINCT st.store_id) AS stores,
       SUM(st.qty) AS units,
       MIN({due}) AS first_due,
       MAX({due}) AS last_due
  FROM stock st
 GROUP BY {TM(m, due)}
 ORDER BY 1"""


@report("discount_bands", "Строки заказов по размеру скидки", "маркетинг",
        "Строки заказов 2024 г. в разрезе диапазонов скидки.")
def q35(m):
    band = DECODE(m, "oi.discount_pct", [(None, "'без скидки'"), ("5", "'малая'"), ("10", "'малая'"),
                                         ("15", "'средняя'"), ("20", "'средняя'")], "'крупная'", text=False)
    return f"""SELECT {band} AS band,
       COUNT(*) AS lines_cnt,
       SUM(oi.qty) AS units,
       ROUND(SUM({rev(m)}), 2) AS revenue
  FROM order_items oi
  JOIN orders o ON o.id = oi.order_id
 WHERE {DT(m, 'o.created_at', '>=', '01.01.2024')}
 GROUP BY {band}
 ORDER BY 1"""


@report("lost_customers", "Клиенты, не вернувшиеся в 2024 году", "CRM",
        "Активные клиенты, которые делали неотменённые заказы во втором полугодии 2023 г., но не\n"
        "сделали ни одного неотменённого заказа в первом полугодии 2024 г.")
def q36(m):
    minus = "MINUS" if m == "leg" else "EXCEPT"
    return f"""SELECT x.id, c.full_name, c.city
  FROM (SELECT o.customer_id AS id
          FROM orders o
         WHERE {DTB(m, 'o.created_at', '01.07.2023', '31.12.2023')}
           AND {S(m, 'o.status', '<>', 'Cancelled')}
        {minus}
        SELECT o.customer_id
          FROM orders o
         WHERE {DT(m, 'o.created_at', '>=', '01.01.2024')}
           AND {S(m, 'o.status', '<>', 'Cancelled')}) x
  JOIN customers c ON c.id = x.id
 WHERE {S(m, 'c.is_active', '=', 'y')}
 ORDER BY x.id"""


@report("email_domains", "Почтовые домены клиентов", "маркетинг",
        "Число клиентов по почтовым доменам (без учёта регистра), в том числе активных и с\n"
        "телефоном. Корпоративный домен исключён.")
def q37(m):
    dom = "LOWER(SUBSTR(c.email, INSTR(c.email, '@') + 1))"
    return f"""SELECT {dom} AS domain,
       COUNT(*) AS customers,
       SUM({IIF(m, S(m, 'c.is_active', '=', 'Y'), '1', '0')}) AS active,
       COUNT({E(m, 'c.phone')}) AS with_phone
  FROM customers c
 WHERE c.email NOT LIKE '%@corp%'
 GROUP BY {dom}
 ORDER BY {DESC(m, '2')}, 1"""


@report("reviews_by_rating", "Отзывы по оценкам", "маркетинг",
        "Число отзывов по каждой оценке и сколько из них без текста.",
        ("Отзывы без оценки показываются отдельной строкой.",))
def q38(m):
    lab = DECODE(m, "rv.rating", [(None, "'без оценки'"), ("1", "'очень плохо'"), ("2", "'плохо'"),
                                  ("3", "'нормально'"), ("4", "'хорошо'"), ("5", "'отлично'")], text=False)
    return f"""SELECT rv.rating,
       {lab} AS label,
       COUNT(*) AS reviews,
       COUNT(*) - COUNT({E(m, 'rv.body')}) AS without_text
  FROM reviews rv
 GROUP BY rv.rating, {lab}
 ORDER BY {DESC(m, 'rv.rating')}"""


@report("referral_chain", "Кто кого привёл: клиенты 2024 года", "CRM",
        "Первые 50 клиентов, зарегистрированных в 2024 г., со строкой «клиент ← пригласивший» и\n"
        "моментом окончания пробного месяца.",
        ("Если пригласившего нет, строка всё равно выводится.",))
def q39(m):
    frm = A(m, """  FROM customers c, customers ref
 WHERE ref.id(+) = c.referrer_id""", """  FROM customers c
  LEFT JOIN customers ref ON ref.id = c.referrer_id
 WHERE 1 = 1""")
    return f"""SELECT {TOP(m, 50)}c.id,
       {CAT(m, 'c.full_name', "' ← '", 'ref.full_name')} AS chain,
       {NVL(m, 'ref.city', "'-'")} AS referrer_city,
       {IIF(m, 'c.city = ref.city', "'да'", "'нет'")} AS same_city,
       {ADDM(m, 'c.registered_at', 1, datetime_=True)} AS trial_end
{frm}
   AND {DT(m, 'c.registered_at', '>=', '01.01.2024')}
 ORDER BY c.id
{LIMIT(m, 50)}"""


@report("first_day_orders", "Заказы первого числа месяца и полуночные заказы", "аналитика",
        "По месяцам 2023–2024: сколько заказов оформлено в первый день месяца и сколько ровно в\n"
        "полночь (признак пакетной загрузки заказов из маркетплейса).")
def q40(m):
    return f"""SELECT {TM(m, 'o.created_at')} AS month_start,
       COUNT(*) AS orders_cnt,
       SUM({IIF(m, TD(m, 'o.created_at') + ' = ' + TM(m, 'o.created_at'), '1', '0')}) AS first_day,
       SUM({IIF(m, A(m, 'o.created_at = TRUNC(o.created_at)',
                         "o.created_at = date(o.created_at) || ' 00:00:00'"), '1', '0')}) AS at_midnight
  FROM orders o
 GROUP BY {TM(m, 'o.created_at')}
 ORDER BY 1"""


@report("hourly_online", "Почасовая активность онлайн-каналов", "аналитика",
        "Число заказов из приложения и с сайта по часам суток, отдельно будни и выходные, 2024 г.")
def q41(m):
    wk = IIF(m, DOW(m, "o.created_at") + " >= 6", "'выходной'", "'будний'")
    return f"""SELECT {EXTRACT(m, 'HOUR', 'o.created_at')} AS hh,
       {wk} AS day_type,
       SUM({IIF(m, S(m, 'o.channel', '=', 'app'), '1', '0')}) AS app_orders,
       SUM({IIF(m, S(m, 'o.channel', '=', 'web'), '1', '0')}) AS web_orders
  FROM orders o
 WHERE {SIN(m, 'o.channel', ['app', 'web'])}
   AND {DT(m, 'o.created_at', '>=', '01.01.2024')}
 GROUP BY {EXTRACT(m, 'HOUR', 'o.created_at')}, {wk}
 ORDER BY 1, 2"""


@report("quarterly_revenue", "Выручка по кварталам с нарастающим итогом", "финансы",
        "Выручка доставленных и отгруженных заказов по кварталам и нарастающий итог.")
def q42(m):
    q = TOCHAR(m, "o.created_at", 'YYYY"-Q"Q')
    return f"""SELECT {q} AS quarter,
       ROUND(SUM({rev(m)}), 2) AS revenue,
       ROUND(SUM(SUM({rev(m)})) OVER (ORDER BY {q}), 2) AS running_revenue
  FROM orders o
  JOIN order_items oi ON oi.order_id = o.id
 WHERE {SIN(m, 'o.status', ['Delivered', 'Shipped'])}
 GROUP BY {q}
 ORDER BY 1"""


@report("delivery_fee_stats", "Статистика платной доставки", "логистика",
        "По онлайн-каналам: число заказов, заказов с известной стоимостью доставки, средняя\n"
        "стоимость, сумма, число бесплатных и число заказов без оплаты доставки.")
def q43(m):
    return f"""SELECT UPPER(o.channel) AS channel,
       COUNT(*) AS orders_cnt,
       COUNT(o.delivery_fee) AS fee_known,
       ROUND(AVG(o.delivery_fee), 2) AS avg_fee,
       ROUND(SUM({NVL(m, 'o.delivery_fee', '0')}), 2) AS fee_sum,
       SUM({IIF(m, 'o.delivery_fee = 0', '1', '0')}) AS free_cnt,
       SUM({IIF(m, 'o.delivery_fee > 0', '0', '1')}) AS unpaid_cnt
  FROM orders o
 WHERE o.store_id IS NULL
   AND {S(m, 'o.status', '<>', 'cancelled')}
 GROUP BY UPPER(o.channel)
 ORDER BY 1"""


@report("yoy_monthly", "Выручка по месяцам год к году", "финансы",
        "Выручка каждого месяца первого полугодия 2024 г. и того же месяца предыдущего года.")
def q44(m):
    mr = f"""(SELECT {TM(m, 'o.created_at')} AS month_start,
               ROUND(SUM({rev(m)}), 2) AS revenue
          FROM orders o
          JOIN order_items oi ON oi.order_id = o.id
         WHERE {SIN(m, 'o.status', ['cancelled'], neg=True)}
         GROUP BY {TM(m, 'o.created_at')})"""
    frm = A(m, f"""  FROM {mr} cur,
       {mr} prv
 WHERE prv.month_start(+) = DATEADD('year', -1, cur.month_start)""", f"""  FROM {mr} cur
  LEFT JOIN {mr} prv ON prv.month_start = {ADDM(m, 'cur.month_start', -1, unit='year')}
 WHERE 1 = 1""")
    return f"""SELECT cur.month_start,
       cur.revenue,
       prv.revenue AS revenue_prev_year,
       ROUND(cur.revenue / prv.revenue * 100 - 100, 1) AS yoy_pct
{frm}
   AND cur.month_start >= {DL(m, '01.01.2024')}
 ORDER BY 1"""


@report("birthdays_july", "Именинники июля", "маркетинг",
        "Активные клиенты с днём рождения в июле: дата, «возраст» на 01.07.2024 для поздравительного\n"
        "шаблона и обращение по имени.",
        ("Возраст вычисляется DATEDIFF('year', ...), как в шаблоне рассылки 2018 года.",))
def q45(m):
    first = "SUBSTR(c.full_name, INSTR(c.full_name, ' ') + 1, INSTR(SUBSTR(c.full_name, INSTR(c.full_name, ' ') + 1), ' ') - 1)"
    return f"""SELECT c.id,
       {TOCHAR(m, 'c.birth_date', 'DD.MM')} AS bday,
       {DYEAR(m, 'c.birth_date', DL(m, '01.07.2024'))} AS age_label,
       {CAT(m, "'Дорогой клиент, '", first, "'!'")} AS greeting
  FROM customers c
 WHERE {EXTRACT(m, 'MONTH', 'c.birth_date')} = 7
   AND {S(m, 'c.is_active', '=', 'Y')}
 ORDER BY {EXTRACT(m, 'DAY', 'c.birth_date')}, c.id"""


@report("stock_cover_vacuum", "Запас пылесосов в днях продаж", "закупки",
        "На сколько дней хватит суммарного остатка по открытым магазинам при темпе продаж последних\n"
        "30 дней (до 30.06.2024).",
        ("Продажи — все неотменённые заказы, созданные после 31.05.2024.",))
def q46(m):
    cover = DIV(m, "st.stock_qty", "(" + DIV(m, "sd.sold_qty", "30") + ")")
    return f"""SELECT {TOP(m, 20)}p.id,
       RTRIM(p.sku) AS sku,
       st.stock_qty,
       sd.sold_qty,
       ROUND({cover}, 1) AS days_cover
  FROM products p
  JOIN (SELECT s2.product_id, SUM({NVL(m, 's2.qty', '0')}) AS stock_qty
          FROM stock s2
          JOIN stores s ON s.id = s2.store_id
         WHERE s.closed_on IS NULL
         GROUP BY s2.product_id) st ON st.product_id = p.id
  JOIN (SELECT oi.product_id, SUM(oi.qty) AS sold_qty
          FROM order_items oi
          JOIN orders o ON o.id = oi.order_id
         WHERE {DT(m, 'o.created_at', '>', '31.05.2024')}
           AND {S(m, 'o.status', '<>', 'cancelled')}
         GROUP BY oi.product_id) sd ON sd.product_id = p.id
 WHERE p.category_id IN (14, 15, 16)
 ORDER BY {DESC(m, 'days_cover')}, p.id
{LIMIT(m, 20)}"""


@report("store_rank_compat", "Рейтинг магазинов по площади (режим совместимости)", "розница",
        "Магазины по убыванию площади: выручка 2024 г., выручка на квадратный метр, среднее число\n"
        "единиц в заказе и подпись управляющего. Отчёт работает в режиме COMPAT(5).")
def q47(m):
    return f"""SELECT {HINT(m)}s.id,
       s.name,
       s.area_m2,
       ROUND(SUM({rev(m)}), 2) AS revenue,
       ROUND(SUM({rev(m)}) / s.area_m2, 0) AS revenue_per_m2,
       {DIV(m, 'SUM(oi.qty)', 'COUNT(DISTINCT o.id)', compat=True)} AS items_per_order,
       {CAT(m, "'Упр.: '", E(m, 's.manager'), compat=True)} AS manager_label
  FROM stores s
  JOIN orders o ON o.store_id = s.id
  JOIN order_items oi ON oi.order_id = o.id
 WHERE {DT(m, 'o.created_at', '>=', '01.01.2024')}
 GROUP BY s.id, s.name, s.area_m2, s.manager
 ORDER BY {DESC(m, 's.area_m2', compat=True)}, s.id"""


@report("frequent_buyers", "Частые покупатели: единицы на заказ", "CRM",
        "25 клиентов с не менее чем четырьмя заказами, по убыванию даты рождения, со средним числом\n"
        "единиц в заказе и строкой контакта.",
        ("В 2019 г. отчёт переводили в режим совместимости, потом вернули обычный режим (см. историю).",))
def q48(m):
    comp = m == "compat"   # the hint here is inside a CTE and does not apply
    naive_like = comp or m == "naive"
    ipo = "p.items / p.orders_cnt" if (m == "leg" or naive_like) else "CAST(p.items AS REAL) / p.orders_cnt"
    if m == "leg":
        contact = "c.city || ' ' || c.phone"
    elif naive_like:
        contact = "c.city || ' ' || NULLIF(c.phone, '')" if comp else "c.city || ' ' || c.phone"
    else:
        contact = CAT(m, "c.city", "' '", E(m, "c.phone"))
    if m == "leg":
        order = "c.birth_date DESC"
    elif naive_like:
        order = "c.birth_date DESC NULLS LAST"
    else:
        order = DESC(m, "c.birth_date")
    hint = "/*+ COMPAT(5) */ " if m == "leg" else ""
    return f"""-- SELECT /*+ COMPAT(5) */ TOP 25 ...   (вариант 2019 года, отключён)
WITH per_cust AS (
  SELECT {hint}o.customer_id, COUNT(*) AS orders_cnt, SUM(t.items) AS items
    FROM orders o
    JOIN (SELECT order_id, SUM(qty) AS items FROM order_items GROUP BY order_id) t ON t.order_id = o.id
   GROUP BY o.customer_id
  HAVING COUNT(*) >= 4
)
SELECT {TOP(m, 25)}c.id,
       c.full_name,
       p.orders_cnt,
       p.items,
       {ipo} AS items_per_order,
       {contact} AS contact
  FROM per_cust p
  JOIN customers c ON c.id = p.customer_id
 ORDER BY {order}, c.id
{LIMIT(m, 25)}"""


@report("root_category_share", "Доля корневых категорий в выручке, I полугодие 2024", "финансы",
        "Выручка по корневым категориям и их доля в общей выручке полугодия.")
def q49(m):
    root = NVL(m, "par.name", "cat.name")
    frm = A(m, """  FROM orders o, order_items oi, products p, categories cat, categories par
 WHERE oi.order_id = o.id
   AND p.id = oi.product_id
   AND cat.id = p.category_id
   AND par.id(+) = cat.parent_id""", """  FROM orders o
  JOIN order_items oi ON oi.order_id = o.id
  JOIN products p ON p.id = oi.product_id
  JOIN categories cat ON cat.id = p.category_id
  LEFT JOIN categories par ON par.id = cat.parent_id
 WHERE 1 = 1""")
    return f"""SELECT {root} AS root_category,
       ROUND(SUM({rev(m)}), 2) AS revenue,
       ROUND(SUM({rev(m)}) * 100 / SUM(SUM({rev(m)})) OVER (), 2) AS share_pct
{frm}
   AND {SIN(m, 'o.status', ['cancelled'], neg=True)}
   AND {DTB(m, 'o.created_at', '01.01.2024', '30.06.2024')}
 GROUP BY {root}
 ORDER BY 2 DESC, 1"""


@report("rfm_segments", "RFM-сегментация клиентов на 30.06.2024", "CRM",
        "Каждому клиенту присваиваются баллы давности (R), частоты (F) и денежной суммы (M), код\n"
        "RFM — склейка трёх цифр. Отчёт показывает число клиентов по кодам.",
        ("Давность — DATEDIFF('day', последний заказ, 30.06.2024).",
         "Баллы M — терцили NTILE(3) по сумме покупок."))
def q50(m):
    rec = DDAY(m, "MAX(o.created_at)", DL(m, "30.06.2024"))
    tc = (lambda x: f"TO_CHAR({x})") if m == "leg" else (lambda x: f"CAST({x} AS TEXT)")
    return f"""WITH base AS (
  SELECT o.customer_id AS id,
         {rec} AS recency_days,
         COUNT(DISTINCT o.id) AS frequency,
         ROUND(SUM(oi.qty * oi.unit_price), 2) AS monetary
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.id
   WHERE {SIN(m, 'o.status', ['cancelled', 'new'], neg=True)}
   GROUP BY o.customer_id
),
scored AS (
  SELECT id,
         {IIF(m, 'recency_days <= 30', '3', IIF(m, 'recency_days <= 120', '2', '1'))} AS r,
         {IIF(m, 'frequency >= 5', '3', IIF(m, 'frequency >= 2', '2', '1'))} AS f,
         NTILE(3) OVER (ORDER BY monetary, id) AS mn
    FROM base
)
SELECT {tc('r')} || {tc('f')} || {tc('mn')} AS rfm,
       COUNT(*) AS customers
  FROM scored
 GROUP BY {tc('r')} || {tc('f')} || {tc('mn')}
 ORDER BY {DESC(m, '2')}, 1"""


# ==========================================================================
# dialect.md
# ==========================================================================

@report("service_visit_plan", "План сервисных визитов через полгода после доставки", "сервисный центр",
        "Сколько доставленных в первом полугодии 2024 г. заказов подходит к плановому сервисному\n"
        "визиту в каждый день второго полугодия. Визит назначается ровно через шесть месяцев\n"
        "после доставки функцией ADD_MONTHS.",
        ("Раньше срок считали через DATEADD('month', 6, ...); в 2022 г. перешли на ADD_MONTHS, "
         "чтобы доставка в последний день месяца давала визит в последний день месяца.",))
def q51(m):
    visit = ADDMON(m, "o.delivered_on", 6)
    return f"""SELECT {visit} AS visit_date,
       COUNT(*) AS orders_cnt,
       SUM({IIF(m, S(m, 'o.channel', '=', 'store'), '1', '0')}) AS store_orders
  FROM orders o
 WHERE {S(m, 'o.status', '=', 'delivered')}
   AND o.delivered_on BETWEEN {DL(m, '01.01.2024')} AND {DL(m, '30.06.2024')}
 GROUP BY {visit}
 ORDER BY 1"""


@report("promo_review_dates", "Даты пересмотра акций", "отдел маркетинга",
        "По каждой акции — дата пересмотра условий (через три месяца после старта, ADD_MONTHS) и\n"
        "дата окончания; для бессрочных акций вместо даты окончания выводится пересмотр через год.",
        ("Пересмотр через год считается тоже через ADD_MONTHS.",))
def q52(m):
    return f"""SELECT UPPER(p.code) AS code,
       p.title,
       p.valid_from,
       {ADDMON(m, 'p.valid_from', 3)} AS review_date,
       {NVL(m, 'p.valid_to', ADDMON(m, 'p.valid_from', 12))} AS end_or_review
  FROM promotions p
 ORDER BY {ASC(m, 'p.valid_from')}, 1"""


@report("store_audit_due", "Сроки аудита магазинов", "розница",
        "Дата первого аудита магазина — через 18 месяцев после открытия. Показываются все магазины,\n"
        "в том числе закрытые, с признаком закрытия.",
        ("Для закрытых магазинов аудит формально тоже назначен — так требует ревизионная служба.",))
def q53(m):
    closed = IIF(m, "s.closed_on IS NULL", "'работает'", "'закрыт'")
    return f"""SELECT s.id,
       s.name,
       s.opened_on,
       {ADDMON(m, 's.opened_on', 18)} AS audit_due,
       {closed} AS state
  FROM stores s
 ORDER BY 4, s.id"""


@report("shelf_labels_sku", "Артикулы для ценников", "розница",
        "Артикулы действующих товаров категорий «Чайники» и «Фены» в формате для принтера ценников:\n"
        "ровно 9 символов, короткие дополняются слева нулями.",
        ("Принтер печатает только 9 знаков; длинные артикулы по договорённости обрезаются LPAD.",
         "Хвостовые пробелы артикула перед печатью убираются."))
def q54(m):
    return f"""SELECT p.id,
       {LPAD(m, 'RTRIM(p.sku)', 9, '0')} AS label_sku,
       {LEN(m, 'p.sku')} AS sku_len,
       p.price
  FROM products p
 WHERE p.category_id IN (2, 11)
   AND p.discontinued_on IS NULL
 ORDER BY p.id"""


@report("vip_internal_codes", "Внутренние коды VIP-клиентов", "CRM",
        "Внутренний код клиента: буква C, пятизначный номер и первые три буквы города в верхнем\n"
        "регистре. Только клиенты сегмента VIP.",
        ("Первые три буквы берутся SUBSTR(..., 0, 3) — так написал автор первой версии.",))
def q55(m):
    code = CAT(m, "'C'", LPAD(m, "c.id", 5, "0", num=True), "'-'", SUB0(m, "UPPER(c.city)", 3))
    return f"""SELECT c.id,
       {code} AS client_code,
       c.full_name,
       {TOCHAR(m, 'c.registered_at', 'DD.MM.YYYY')} AS reg_date
  FROM customers c
 WHERE {S(m, 'c.segment', '=', 'vip')}
 ORDER BY c.id"""


@report("city_prefix_stats", "Клиенты по префиксам городов", "колл-центр",
        "Группировка клиентов по первым трём буквам города (для разбивки обзвона между сменами)\n"
        "с числом клиентов, числом телефонов и долей активных.",
        ("Префикс нужен операторам: они работают по спискам «Мос», «Сан», «Каз» и т. д.",))
def q56(m):
    pref = SUB0(m, "c.city", 3)
    act = f"SUM({IIF(m, S(m, 'c.is_active', '=', 'Y'), '1', '0')})"
    return f"""SELECT {pref} AS city_prefix,
       COUNT(*) AS customers,
       COUNT({E(m, 'c.phone')}) AS with_phone,
       ROUND({DIV(m, '100 * ' + act, 'COUNT(*)')}, 2) AS active_pct
  FROM customers c
 GROUP BY {pref}
 ORDER BY 1"""


@report("corp_email_handles", "Корпоративные адреса клиентов", "маркетинг",
        "Первые 50 клиентов с адресами в домене corp.example: имя почтового ящика (часть адреса до\n"
        "@) и признак наличия телефона.",
        ("Домен в адресах записан в разном регистре.",))
def q57(m):
    handle = SUB0(m, "c.email", "INSTR(c.email, '@') - 1")
    return f"""SELECT {TOP(m, 50)}c.id,
       {handle} AS mailbox,
       {NVL2(m, E(m, 'c.phone'), "'да'", "'нет'")} AS has_phone
  FROM customers c
 WHERE c.email LIKE '%@corp.example'
 ORDER BY c.id
{LIMIT(m, 50)}"""


@report("weeks_to_first_order", "Через сколько недель после регистрации — первый заказ", "CRM",
        "Клиенты, зарегистрированные в 2024 г., распределены по числу недель между регистрацией и\n"
        "первым неотменённым заказом (DATEDIFF по неделям).",
        ("Недели считаются от понедельника.",))
def q58(m):
    wk = DWEEK(m, "c.registered_at", "f.first_at")
    return f"""WITH f AS (
  SELECT o.customer_id, MIN(o.created_at) AS first_at
    FROM orders o
   WHERE {S(m, 'o.status', '<>', 'cancelled')}
   GROUP BY o.customer_id
)
SELECT {wk} AS weeks_to_first,
       COUNT(*) AS customers
  FROM customers c
  JOIN f ON f.customer_id = c.id
 WHERE {DT(m, 'c.registered_at', '>=', '01.01.2024')}
 GROUP BY {wk}
 ORDER BY 1"""


@report("delivery_week_shift", "Доставка через границу недели", "логистика",
        "Доставленные в 2024 г. заказы по каналам: сколько доставлено на той же календарной неделе,\n"
        "на следующей и позже (DATEDIFF по неделям между созданием и доставкой).",
        ("Неделя — с понедельника по воскресенье.",))
def q59(m):
    wk = DWEEK(m, "o.created_at", "o.delivered_on")
    return f"""SELECT UPPER(o.channel) AS channel,
       SUM({IIF(m, wk + ' = 0', '1', '0')}) AS same_week,
       SUM({IIF(m, wk + ' = 1', '1', '0')}) AS next_week,
       SUM({IIF(m, wk + ' >= 2', '1', '0')}) AS later,
       ROUND(AVG({wk}), 3) AS avg_weeks
  FROM orders o
 WHERE o.delivered_on IS NOT NULL
   AND {DT(m, 'o.created_at', '>=', '01.01.2024')}
 GROUP BY UPPER(o.channel)
 ORDER BY 1"""


@report("product_life_quarters", "Жизненный цикл товаров в кварталах", "закупки",
        "Сколько кварталов товар находится (находился) в продаже: от запуска до снятия, а для\n"
        "действующих — до 30.06.2024. Средние и максимальные значения по категориям.",
        ("Кварталы считаются функцией DATEDIFF('quarter', ...), доступной с версии 7.3.",))
def q60(m):
    q = DQTR(m, "p.launched_on", NVL(m, "p.discontinued_on", DL(m, "30.06.2024")))
    return f"""SELECT c.name AS category,
       COUNT(*) AS products_cnt,
       ROUND(AVG({q}), 3) AS avg_quarters,
       MAX({q}) AS max_quarters
  FROM products p
  JOIN categories c ON c.id = p.category_id
 GROUP BY c.name
 ORDER BY 1"""


@report("followup_monday_calls", "Обзвон после доставки: понедельники", "клиентский сервис",
        "Обзвон доставленных в июне 2024 г. заказов проводится в первый понедельник после\n"
        "доставки (NEXT_DAY). Число заказов и клиентов на каждый понедельник.",
        ("Если доставка пришлась на понедельник, звоним в следующий понедельник.",))
def q61(m):
    nd = NEXTDAY(m, "o.delivered_on", "MON")
    return f"""SELECT {nd} AS call_day,
       COUNT(*) AS orders_cnt,
       COUNT(DISTINCT o.customer_id) AS customers
  FROM orders o
 WHERE o.delivered_on BETWEEN {DL(m, '01.06.2024')} AND {DL(m, '30.06.2024')}
   AND {SIN(m, 'o.status', ['delivered', 'returned'])}
 GROUP BY {nd}
 ORDER BY 1"""


@report("settlement_fridays", "Перечисления эквайринга по пятницам", "бухгалтерия",
        "Банк перечисляет деньги по платежам в первую пятницу после дня платежа. Суммы платежей\n"
        "мая 2024 г. по дням перечисления и способам оплаты.",
        ("Способ оплаты приводится к верхнему регистру.",))
def q62(m):
    nd = NEXTDAY(m, TD(m, "p.paid_at"), "FRI")
    return f"""SELECT {nd} AS settle_day,
       UPPER(p.method) AS method,
       COUNT(*) AS payments_cnt,
       ROUND(SUM(p.amount), 2) AS amount
  FROM payments p
 WHERE {DT(m, 'p.paid_at', '>=', '01.05.2024')}
   AND {DT(m, 'p.paid_at', '<', '01.06.2024')}
 GROUP BY {nd}, UPPER(p.method)
 ORDER BY 1, 2"""


@report("negative_reviews_sundays", "Разбор негативных отзывов по воскресеньям", "маркетинг",
        "Отзывы с оценкой 1–2 за 2024 г. разбираются в ближайшее воскресенье после публикации.\n"
        "Число отзывов, отзывов без текста и товаров на каждое воскресенье.",)
def q63(m):
    nd = NEXTDAY(m, TD(m, "r.created_at"), "SUN")
    return f"""SELECT {nd} AS review_day,
       COUNT(*) AS reviews_cnt,
       SUM({IIF(m, E(m, 'r.body') + ' IS NULL', '1', '0')}) AS without_text,
       COUNT(DISTINCT r.product_id) AS products_cnt
  FROM reviews r
 WHERE r.rating <= 2
   AND {DT(m, 'r.created_at', '>=', '01.01.2024')}
 GROUP BY {nd}
 ORDER BY 1"""


@report("stock_next_recount", "Следующий пересчёт остатков", "склад",
        "Следующий пересчёт позиции назначается на первый понедельник после даты, отстоящей на\n"
        "три месяца (ADD_MONTHS) от последнего пересчёта. Число позиций и единиц по датам.",
        ("Позиции с неизвестным остатком тоже пересчитываются.",))
def q64(m):
    nd = NEXTDAY(m, ADDMON(m, "st.counted_on", 3), "MON")
    return f"""SELECT {nd} AS recount_day,
       COUNT(*) AS positions,
       SUM(st.qty) AS units,
       SUM({IIF(m, 'st.qty IS NULL', '1', '0')}) AS unknown_qty
  FROM stock st
 GROUP BY {nd}
 ORDER BY 1"""


@report("receipt_short_names", "Краткие названия для чеков", "розница",
        "Действующие товары брендов Aurora, Orion и Zefir: краткое название (первые 20 символов)\n"
        "и восьмизначный код для кассы (артикул без хвостовых пробелов через LPAD до 8 знаков).",
        ("Касса принимает не больше 8 знаков кода; длинные артикулы обрезаются.",))
def q65(m):
    return f"""SELECT p.id,
       {SUB0(m, 'p.name', 20)} AS short_name,
       {LPAD(m, 'RTRIM(p.sku)', 8, '0')} AS till_code,
       p.brand
  FROM products p
 WHERE {SIN(m, 'p.brand', ['AURORA', 'ORION', 'ZEFIR'])}
   AND p.discontinued_on IS NULL
 ORDER BY p.id"""


@report("store_manager_initials", "Магазины и инициалы управляющих", "розница",
        "Список магазинов по убыванию площади с первой буквой фамилии управляющего и точкой (для\n"
        "таблички у входа).",
        ("Если управляющий не назначен, на табличке остаётся одна точка.",))
def q66(m):
    ini = CAT(m, SUB0(m, E(m, "s.manager"), 1), "'.'")
    return f"""SELECT s.id,
       s.name,
       {ini} AS manager_initial,
       s.area_m2
  FROM stores s
 ORDER BY {DESC(m, 's.area_m2')}, s.id"""


@report("app_orders_week_no", "Заказы из приложения по номерам недель года, IV квартал 2023", "аналитика",
        "Номер недели считается как число недельных границ от 1 января плюс один; первая неделя\n"
        "года — неполная. Число заказов из приложения и средний чек по номерам недель.",
        ("Номер недели нужен для сверки с маркетинговым календарём.",))
def q67(m):
    wk = DWEEK(m, TY(m, "o.created_at"), "o.created_at") + " + 1"
    return f"""SELECT {wk} AS week_no,
       COUNT(*) AS orders_cnt,
       ROUND(AVG(t.total), 2) AS avg_check
  FROM orders o
  JOIN (SELECT order_id, SUM(qty * unit_price) AS total
          FROM order_items
         GROUP BY order_id) t ON t.order_id = o.id
 WHERE {S(m, 'o.channel', '=', 'app')}
   AND {DTB(m, 'o.created_at', '01.10.2023', '31.12.2023')}
 GROUP BY {wk}
 ORDER BY 1"""


@report("sms_phone_masks", "Номера для SMS-шлюза, Казань", "колл-центр",
        "Первые 60 клиентов из Казани с телефоном: номер в формате шлюза — первые 12 символов\n"
        "(LPAD до 12 знаков; короткие номера дополняются звёздочками слева).",
        ("Шлюз отрезает хвост номера сам, поэтому обрезка до 12 знаков не мешает.",))
def q68(m):
    return f"""SELECT {TOP(m, 60)}c.id,
       {LPAD(m, E(m, 'c.phone'), 12, '*')} AS gateway_phone,
       {NVL(m, 'UPPER(' + E(m, 'c.segment') + ')', "'-'")} AS segment
  FROM customers c
 WHERE c.city = 'Казань'
   AND {E(m, 'c.phone')} IS NOT NULL
 ORDER BY c.id
{LIMIT(m, 60)}"""


@report("retention_by_quarter", "Удержание когорт 2023 года по кварталам", "CRM",
        "Для клиентов, сделавших первый неотменённый заказ в 2023 г., — сколько из них делали\n"
        "неотменённые заказы в каждом следующем квартале (номер квартала от первого заказа по\n"
        "DATEDIFF('quarter', ...); 0 — квартал первого заказа).",
        ("Заказы клиента в одном квартале считаются один раз.",))
def q69(m):
    qn = DQTR(m, "f.first_at", "o.created_at")
    st = S(m, "o.status", "<>", "cancelled")
    return f"""WITH f AS (
  SELECT o.customer_id, MIN(o.created_at) AS first_at
    FROM orders o
   WHERE {st}
   GROUP BY o.customer_id
)
SELECT {qn} AS quarter_no,
       COUNT(DISTINCT o.customer_id) AS active_customers,
       COUNT(*) AS orders_cnt
  FROM orders o
  JOIN f ON f.customer_id = o.customer_id
 WHERE {st}
   AND {DT(m, 'f.first_at', '<', '01.01.2024')}
 GROUP BY {qn}
 ORDER BY 1"""


@report("launch_month_codes", "Коды месяцев запуска товаров", "справочник НСИ",
        "Для товаров, запущенных в 2023–2024 гг.: код месяца запуска вида ГГГГ-ММ, первые четыре\n"
        "символа названия бренда в верхнем регистре и число товаров.",
        ("Первые четыре символа бренда — SUBSTR(..., 0, 4), так в справочнике поставщиков.",))
def q70(m):
    ym = TOCHAR(m, "p.launched_on", "YYYY-MM")
    br = SUB0(m, "UPPER(p.brand)", 4)
    return f"""SELECT {ym} AS launch_ym,
       {br} AS brand4,
       COUNT(*) AS products_cnt,
       SUM({IIF(m, 'p.discontinued_on IS NULL', '1', '0')}) AS active_cnt
  FROM products p
 WHERE p.launched_on >= {DL(m, '01.01.2023')}
 GROUP BY {ym}, {br}
 ORDER BY 1, 2"""

DIALECT_1 = """# Корунд-SQL 7.4 — справочник по семантике для переноса отчётов

Документ подготовлен группой миграции «Корунд-Отчёты → SQLite». Он описывает те части
диалекта Корунд-SQL, которые встречаются в отчётах магазина, и то, как именно сервер
«Корунд-Отчёты» вычислял результат. Цель переноса — **получить на SQLite ровно те же строки,
которые выдавал старый сервер**, включая известные странности старых отчётов.

Промышленный сервер работал на версии **7.4**. Раздел 17 «История изменений» содержит
поправки, внесённые в версиях 7.1–7.4; они **имеют приоритет** над описанием в основных
разделах (основные разделы писались для версии 7.0 и не все были обновлены). Изменения,
запланированные на версию 8.0, на промышленном сервере не действовали.

Содержание:

1. Общие сведения о запросе отчёта
2. Лексика: комментарии, подсказки, литералы
3. Типы данных
4. NULL и пустая строка
5. Сравнение строк и LIKE
6. Числа и арифметика
7. Строковые функции
8. Условные функции
9. Даты и время
10. Агрегатные функции
11. Соединения, в том числе синтаксис (+)
12. SELECT: TOP, сортировка, группировка, операции над множествами
13. Оконные функции
14. Подсказки и режимы совместимости
15. Типичные идиомы старых отчётов
16. Соглашения переноса в SQLite
17. История изменений

---

## 1. Общие сведения о запросе отчёта

Каждый отчёт — это один оператор SELECT (возможно, с предложением WITH), сохранённый в файле
`.sql`. Файл начинается с заголовка из комментариев: название, заказчик, история изменений,
примечания. **Заголовок и комментарии не являются спецификацией.** Их писали разные люди в
разные годы, часть из них устарела (например, в комментарии написано «топ-10», а в коде стоит
`TOP 15`, или в комментарии упомянут фильтр, который давно убран). При переносе
воспроизводится поведение кода, а не намерение, описанное в комментарии.

Закомментированный код (строки, начинающиеся с `--`, и блоки `/* ... */`) не выполняется,
даже если выглядит как рабочий фрагмент запроса.

Сервер не использовал параметров: все даты в отчётах зашиты литералами. Функция `SYSDATE`
в отчётах магазина не встречается.

## 2. Лексика: комментарии, подсказки, литералы

### 2.1. Регистр ключевых слов и имён

Ключевые слова, имена таблиц, столбцов и функций не чувствительны к регистру: `select`,
`Select` и `SELECT` равнозначны, `O.Created_At` — то же, что `o.created_at`.

### 2.2. Комментарии

* `-- текст` — комментарий до конца строки.
* `/* текст */` — блочный комментарий, может занимать несколько строк.
* `/*+ текст */` — **подсказка** (см. раздел 14). Подсказкой считается только блок, у которого
  знак `+` стоит сразу после `/*`, и только если он расположен **непосредственно после
  ключевого слова SELECT основного (внешнего) запроса**. Подсказка в любом другом месте — в
  подзапросе, в части WITH, после списка столбцов, внутри строкового комментария `--` —
  является обычным комментарием и ни на что не влияет.

### 2.3. Строковые литералы

Строковый литерал заключается в одинарные кавычки: `'текст'`. Кавычка внутри литерала
удваивается: `'д''Артаньян'`. Пустой литерал `''` — это NULL (раздел 4).

### 2.4. Литералы дат

Отдельного синтаксиса для дат в старых отчётах почти не использовали. Вместо этого строковый
литерал в формате `'ДД.ММ.ГГГГ'` (например, `'05.03.2024'`) или `'ДД.ММ.ГГГГ ЧЧ:МИ:СС'`
**неявно превращается в дату**, если:

* он сравнивается (`=`, `<>`, `!=`, `<`, `<=`, `>`, `>=`, `BETWEEN`, `IN`) с выражением типа
  DATE или DATETIME;
* он передан аргументом в функцию, которая ожидает дату (`DATEADD`, `DATEDIFF`, `TRUNC`,
  `TO_CHAR`, `LAST_DAY`, `EXTRACT`, `DAYOFWEEK`);
* он стоит в `NVL`, `COALESCE`, `NVL2`, `DECODE`, `IIF`, `GREATEST`, `LEAST` или в ветке
  `CASE` рядом с выражением типа DATE или DATETIME (результат тогда имеет тип даты).

Литерал `'ДД.ММ.ГГГГ'` без времени обозначает **полночь** этого дня (00:00:00). Это важно
при сравнении с DATETIME: условие `created_at <= '31.03.2024'` пропускает заказ, созданный
31.03.2024 ровно в 00:00:00, но не пропускает заказ, созданный 31.03.2024 в 00:00:01 или
позже. Точно так же `created_at BETWEEN '01.03.2024' AND '31.03.2024'` не включает почти весь
день 31 марта.

Если литерал сравнивается со **строковым** выражением (например, со столбцом типа VARCHAR, в
котором дата хранится текстом), никакого преобразования не происходит: сравниваются две
строки посимвольно. Так, условие `return_date_txt >= '01.05.2024'` для текстового столбца
сравнивает строки `'ДД.ММ.ГГГГ'` лексикографически — сначала день, потом месяц, потом год.
Старые отчёты, написанные так, работали «неправильно», но именно такой результат и получали
пользователи; при переносе его нужно воспроизвести.

Кроме того, поддерживается синтаксис ANSI: `DATE '2024-03-05'` и
`TIMESTAMP '2024-03-05 10:00:00'`. Семантика та же.

### 2.5. Числовые литералы

`42` — целое, `42.5` и `1e3` — дробные (NUMBER). Отрицательные числа записываются с унарным
минусом.

## 3. Типы данных

| Тип Корунд-SQL | Смысл | Как хранится после миграции в SQLite |
|---|---|---|
| INTEGER | целое | INTEGER |
| NUMBER(p, s) | десятичное с фиксированной точностью | REAL |
| VARCHAR(n) | строка | TEXT (значение перенесено как есть, включая пустые строки и завершающие пробелы) |
| DATE | календарная дата без времени | TEXT `'YYYY-MM-DD'` |
| DATETIME | дата и время с точностью до секунды | TEXT `'YYYY-MM-DD HH:MM:SS'` |
| CHAR(1) | флаг | TEXT |

Типы столбцов конкретных таблиц приведены в `schema.md`.

При сравнении DATE с DATETIME значение DATE рассматривается как полночь этого дня. Например,
`o.created_at >= p.valid_from` для заказа, созданного в 09:15 в день начала акции, истинно, а
`o.created_at <= p.valid_to` для заказа, созданного в 09:15 в последний день акции, ложно.

Результат функций над датами имеет тип, описанный в разделе 9: например, `TRUNC` всегда
возвращает DATE, а `DATEADD` возвращает значение того же типа, что и аргумент.

## 4. NULL и пустая строка

В Корунд-SQL **строка нулевой длины и NULL — одно и то же значение**. Это касается и
литерала `''`, и пустых строк, хранящихся в таблицах (при миграции пустые строки перенесены в
SQLite как есть, поэтому в SQLite они отличаются от NULL, а в старой системе — нет), и
результатов функций, которые дают строку нулевой длины.

Следствия:

* `phone IS NULL` истинно и для NULL, и для пустой строки; `phone IS NOT NULL` — только для
  непустых значений.
* `COUNT(phone)` не считает ни NULL, ни пустые строки.
* `NVL(phone, 'нет')` возвращает `'нет'` и для NULL, и для пустой строки.
* Сравнение с литералом `''` — это сравнение с NULL, результат всегда «неизвестно».
  Поэтому условие `comment <> ''` **никогда не выполняется**, а условие `comment = ''` тоже
  никогда не выполняется. В старых отчётах встречается `CASE WHEN comment <> '' THEN 1 ELSE 0
  END` — такое выражение всегда даёт 0.
* `UPPER(segment)` для пустого сегмента — NULL.
* Результат конкатенации, оказавшийся пустым, — NULL.

Логика трёх значений — стандартная: `NULL = NULL` — неизвестно; `NOT (неизвестно)` —
неизвестно; строка попадает в результат WHERE/HAVING только при значении «истина»;
`x NOT IN (подзапрос)` при наличии NULL в подзапросе не выполняется ни для одной строки.

## 5. Сравнение строк и LIKE

### 5.1. Операторы сравнения

Операторы `=`, `<>`, `!=`, `<`, `<=`, `>`, `>=`, `BETWEEN`, `IN`, `NOT IN` при сравнении двух
строк работают по коллации сервера **CI_PAD**:

* **без учёта регистра** букв: `'PAID' = 'paid'` и `'Paid' = 'pAID'` истинны;
* **без учёта завершающих пробелов**: `'SPRING24 ' = 'SPRING24'` истинно; ведущие пробелы
  значимы.

То же правило действует в условиях соединения (`ON`, условия `(+)`), в `DECODE` и в простой
форме `CASE x WHEN 'a' THEN ...` (кроме NULL — см. раздел 8).

Коллация распространяется только на **сравнения**. Группировка (`GROUP BY`), `DISTINCT`,
`COUNT(DISTINCT ...)`, `LISTAGG(DISTINCT ...)`, операции над множествами (`UNION`, `MINUS`,
`INTERSECT`), `PARTITION BY`, сортировка `ORDER BY`, а также `MIN`/`MAX` по строкам работают
с **точными** значениями: `'Web'` и `'WEB'` — разные группы, строки сортируются в двоичном
порядке кодов символов (как в SQLite по умолчанию). Поэтому в старых отчётах перед
группировкой по каналу или статусу обычно стоит `UPPER(...)`.

Коллация определена для всех букв, включая кириллицу. В данных магазина кириллические
значения (города, имена, названия) хранятся в едином каноническом написании, так что при
переносе на практике важны латинские значения: статусы, каналы, сегменты, способы оплаты,
промокоды, коды причин, флаги.

### 5.2. LIKE

`x LIKE 'шаблон'` — сопоставление с шаблоном: `%` — любая последовательность символов,
`_` — ровно один символ, `ESCAPE '\\'` задаёт экранирующий символ. `LIKE` **не учитывает
регистр** букв, но, в отличие от операторов сравнения, **пробелы в LIKE значимы**, включая
завершающие. `NOT LIKE` — отрицание. Для NULL результат LIKE — неизвестно.
"""

DIALECT_2 = """
## 6. Числа и арифметика

* `+`, `-`, `*` — обычные операции. Если хотя бы один операнд NULL, результат NULL.
* `/` — **всегда точное (дробное) деление**, в том числе для двух целых: `7 / 2 = 3.5`,
  `SUM(qty) / COUNT(*)` для целых даёт дробное число. (В режиме `COMPAT(5)` поведение другое,
  см. раздел 14.)
* Деление на ноль не вызывает ошибку, а даёт NULL.
* `DIV(a, b)` — целая часть частного с отбрасыванием дробной части (к нулю): `DIV(7, 2) = 3`,
  `DIV(-7, 2) = -3`.
* `MOD(a, b)` — остаток, знак совпадает со знаком делимого: `MOD(-7, 2) = -1`.
* `ROUND(x)` и `ROUND(x, n)` — округление половины от нуля: `ROUND(2.5) = 3`,
  `ROUND(-2.5) = -3`, `ROUND(1.005, 2) = 1.01` (для значений NUMBER). Отрицательное n
  округляет до десятков, сотен и т. д.
* `TRUNC(x)` и `TRUNC(x, n)` для чисел — отбрасывание знаков к нулю. Не путать с `TRUNC` от
  даты (раздел 9): вид операции определяется типом аргумента.
* `CEIL(x)`, `FLOOR(x)`, `ABS(x)`, `SIGN(x)`, `POWER(x, y)`, `SQRT(x)` — как обычно.
* Сравнение целого с дробным выполняется по значению: `3 = 3.0` истинно.

Пример. Выражение `100 * returned / sold` при `returned = 3`, `sold = 7` даёт
`42.857142857...`, а не 42.

## 7. Строковые функции

* `a || b` — конкатенация. **NULL (и пустая строка) в конкатенации считается пустой
  строкой**: `'тел. ' || NULL = 'тел. '`. Если результат оказался пустым, он равен NULL
  (раздел 4).
* `CONCAT(a, b, ...)` — то же, что `a || b || ...`.
* `LEN(s)` — число символов строки.
* `SUBSTR(s, pos)`, `SUBSTR(s, pos, len)` — подстрока, позиции с 1; отрицательная `pos`
  отсчитывается от конца строки. О позиции 0 см. приложение В.4.
* `INSTR(s, sub)` — позиция первого вхождения (с 1), 0 если не найдено. Поиск **с учётом**
  регистра.
* `UPPER(s)`, `LOWER(s)` — перевод регистра.
* `TRIM(s)`, `LTRIM(s)`, `RTRIM(s)` — удаление пробелов с обеих сторон, слева, справа.
* `LEFT(s, n)`, `RIGHT(s, n)` — первые или последние n символов.
* `REPLACE(s, a, b)` — замена всех вхождений (с учётом регистра).
* `LPAD(s, n, c)` — дополняет строку слева символом c до длины n; если строка длиннее n,
  она обрезается до n первых символов. Числовой аргумент сначала переводится в строку:
  `LPAD(7, 3, '0') = '007'`. `LPAD(NULL, 3, '0')` — NULL.
* `RPAD(s, n, c)` — то же справа.
* `TO_CHAR(n)` — число в строку в каноническом виде: целые без дробной части (`'42'`),
  дробные с точкой.

Длина и завершающие пробелы: см. также поправку версии 7.3 в разделе 17.

## 8. Условные функции

* `NVL(a, b)` — a, если a не NULL, иначе b.
* `NVL2(a, b, c)` — b, если a не NULL, иначе c.
* `COALESCE(a, b, ...)` — первый не-NULL аргумент.
* `NULLIF(a, b)` — NULL, если a = b, иначе a.
* `IIF(условие, a, b)` — a, если условие истинно; b, если условие ложно **или неизвестно**.
* `CASE WHEN ... THEN ... ELSE ... END` — стандартный. Без ELSE — NULL. Простая форма
  `CASE x WHEN v1 THEN r1 ... END` сравнивает x с каждым vi оператором `=`, поэтому ветка
  `WHEN NULL` в простой форме никогда не срабатывает.
* `DECODE(x, v1, r1, v2, r2, ..., [default])` — возвращает ri для первого vi, «совпавшего» с x,
  иначе default (если default не указан — NULL). Отличия DECODE от CASE:
  - **NULL совпадает с NULL**: `DECODE(reason, NULL, 'не указана', ...)` для NULL (и, значит,
    для пустой строки) возвращает `'не указана'`;
  - строковые значения сравниваются по правилам раздела 5.1 (без учёта регистра и
    завершающих пробелов): `DECODE('defect ', 'DEFECT', 'Брак')` даёт `'Брак'`;
  - значения проверяются по порядку, срабатывает первое совпадение.
* `GREATEST(a, b, ...)`, `LEAST(a, b, ...)` — наибольший и наименьший из аргументов.
  Если хотя бы один аргумент NULL, результат NULL. (См. поправку версии 7.1 в разделе 17.)

## 9. Даты и время

### 9.1. Прибавление интервалов

`DATEADD(unit, n, d)` прибавляет к дате d целое число n единиц (n может быть отрицательным).
Единицы: `'day'`, `'week'` (7 дней), `'month'`, `'quarter'` (3 месяца), `'year'` (12 месяцев).

Для `'month'`, `'quarter'` и `'year'` номер дня сохраняется, а если в целевом месяце такого дня
нет, берётся **последний день целевого месяца** (перехода на следующий месяц не бывает):

| Выражение | Результат |
|---|---|
| `DATEADD('month', 1, '31.01.2024')` | 29.02.2024 |
| `DATEADD('month', 1, '31.01.2023')` | 28.02.2023 |
| `DATEADD('month', 1, '29.02.2024')` | 29.03.2024 |
| `DATEADD('quarter', 1, '31.03.2024')` | 30.06.2024 |
| `DATEADD('month', -3, '31.05.2024')` | 29.02.2024 |
| `DATEADD('year', 1, '29.02.2024')` | 28.02.2025 |
| `DATEADD('month', 6, '31.08.2023')` | 29.02.2024 |

Время суток у DATETIME сохраняется: `DATEADD('month', 1, '31.01.2024 10:20:30')` =
`29.02.2024 10:20:30`. Результат имеет тот же тип, что и d (DATE или DATETIME).

`d + n` и `d - n` для числа n — прибавление и вычитание n дней; `d1 - d2` — разность в днях
(для DATETIME — дробная).

### 9.2. Разность дат

`DATEDIFF(unit, a, b)` — **число границ единиц, пересечённых при переходе от a к b** (b − a),
а не число полных единиц:

* `'day'` — разность календарных дат; время суток не учитывается:
  `DATEDIFF('day', '31.03.2024 23:59:00', '01.04.2024 00:01:00') = 1`;
* `'month'` — `(год(b) * 12 + месяц(b)) − (год(a) * 12 + месяц(a))`:
  `DATEDIFF('month', '31.01.2024', '01.02.2024') = 1`, а
  `DATEDIFF('month', '01.01.2024', '31.01.2024') = 0`;
* `'year'` — `год(b) − год(a)`: `DATEDIFF('year', '31.12.2000', '01.01.2024') = 24`, хотя
  полных лет прошло 23.

Если a или b — NULL, результат NULL.

### 9.3. Усечение даты

`TRUNC(d)` и `TRUNC(d, fmt)` возвращают **DATE** (без времени):

| fmt | Результат |
|---|---|
| не указан или `'DD'` | та же дата без времени |
| `'IW'` | понедельник ISO-недели, в которую входит d (неделя с понедельника по воскресенье) |
| `'MM'` | первое число месяца |
| `'Q'` | первое число квартала (01.01, 01.04, 01.07, 01.10) |
| `'YYYY'` | 1 января |

Пример: `TRUNC('09.06.2024 18:00:00', 'IW')` (воскресенье) = `03.06.2024` (понедельник);
`TRUNC('03.06.2024', 'IW')` = `03.06.2024`.

`LAST_DAY(d)` — последний день месяца, тип DATE.

### 9.4. Части даты

* `EXTRACT(YEAR FROM d)`, `EXTRACT(MONTH FROM d)`, `EXTRACT(DAY FROM d)`,
  `EXTRACT(HOUR FROM d)` — целые числа (для DATE час равен 0).
* `DAYOFWEEK(d)` — номер дня недели: **1 — понедельник, ..., 6 — суббота, 7 — воскресенье**.

### 9.5. Преобразования

* `TO_DATE(s, 'DD.MM.YYYY')` — строка в DATE.
* `TO_CHAR(d, fmt)` — дата в строку. Элементы формата: `DD` (день, 2 цифры), `MM` (месяц,
  2 цифры), `YYYY` (год), `YY` (2 цифры года), `HH24` (час 00–23), `MI` (минуты), `SS`
  (секунды), `Q` (номер квартала 1–4). Разделители `.`, `-`, `/`, `:`, пробел выводятся как
  есть; произвольный текст заключается в двойные кавычки: `TO_CHAR(d, 'YYYY"-Q"Q')` даёт
  `'2024-Q2'`.
* `TO_CHAR(n, 'FM999999990.00')` — число с ровно двумя знаками после точки без ведущих
  пробелов: `'1234.50'`, `'0.05'`.
"""

DIALECT_3 = """
## 10. Агрегатные функции

* `COUNT(*)` — число строк; `COUNT(x)` — число строк, где x не NULL (напомним: пустая строка —
  тоже NULL); `COUNT(DISTINCT x)` — число различных точных значений.
* `SUM(x)`, `AVG(x)`, `MIN(x)`, `MAX(x)` — NULL игнорируются. `AVG` от целых — дробное число.
  На пустом множестве (или если все значения NULL) результат NULL, `COUNT` — 0.
* `MEDIAN(x)` — медиана не-NULL значений: при нечётном их количестве — среднее по порядку
  значение, при чётном — среднее арифметическое двух средних значений.
* `LISTAGG(x, sep) WITHIN GROUP (ORDER BY ...)` — склейка значений через разделитель sep в
  указанном порядке; NULL пропускаются; если все значения NULL — результат NULL.
  `LISTAGG(DISTINCT x, sep) WITHIN GROUP (ORDER BY x)` — склейка различных точных значений.
* Агрегат можно использовать внутри оконной функции: `SUM(COUNT(*)) OVER (ORDER BY ...)`.

## 11. Соединения

Поддерживается синтаксис ANSI: `JOIN ... ON`, `LEFT JOIN ... ON`, `CROSS JOIN`.

В старых отчётах чаще встречается перечисление таблиц через запятую с условиями в WHERE и
внешние соединения в стиле **(+)**. Пометка `(+)` ставится у столбцов **необязательной**
таблицы: условие `o.customer_id(+) = c.id` означает, что для каждой строки `c` сохраняется
результат, даже если подходящей строки `o` нет (тогда столбцы `o` равны NULL). Это
эквивалентно `c LEFT JOIN o ON o.customer_id = c.id`.

Правила для (+):

1. **Все** условия WHERE, в которых столбцы необязательной таблицы помечены `(+)`, относятся к
   условию соединения, то есть к части `ON` левого соединения, — в том числе условия со
   сравнением с литералом: `o.status(+) = 'DELIVERED'` отбирает, какие строки `o`
   присоединяются, но не отбрасывает строки `c`, для которых таких `o` нет.
2. Условие, в котором столбец необязательной таблицы стоит **без** `(+)`, применяется после
   соединения как обычный фильтр WHERE; поскольку для строк без пары этот столбец равен NULL,
   такие строки отбрасываются (соединение фактически становится внутренним).
3. Условия, не касающиеся необязательной таблицы, — обычные фильтры WHERE.
4. Строки сравниваются по правилам раздела 5.1 и в условиях соединения.

## 12. SELECT

### 12.1. TOP

`SELECT TOP n ...` возвращает первые n строк результата **после** сортировки ORDER BY.
`SELECT TOP n PERCENT ...` возвращает первые k строк, где k — n процентов от числа строк
результата (до применения TOP), округлённые **вниз**. (См. поправку версии 7.2.)
При наличии подсказки TOP пишется после неё: `SELECT /*+ ... */ TOP 10 ...`, при DISTINCT —
после DISTINCT.

### 12.2. ORDER BY

Сортировать можно по выражению, по псевдониму столбца и по номеру позиции в списке SELECT
(`ORDER BY 2 DESC, 1`). По умолчанию ASC.

**Положение NULL при сортировке.** NULL (а значит, и пустые строки) располагаются **первыми
при любом направлении сортировки**: и при `ASC`, и при `DESC`. Явное указание `NULLS FIRST` или
`NULLS LAST` после направления переопределяет это правило для данного выражения. Правило
действует и внутри `OVER (ORDER BY ...)` оконных функций, и в `WITHIN GROUP (ORDER BY ...)`.

Строки сортируются в двоичном порядке с учётом регистра (раздел 5.1). Даты — хронологически.

### 12.3. GROUP BY и HAVING

`GROUP BY` принимает выражения, а также номера позиций списка SELECT (`GROUP BY 1`).
Группировка — по точным значениям (раздел 5.1); все NULL и пустые строки образуют одну группу
NULL. `HAVING` фильтрует группы.

### 12.4. Операции над множествами

`UNION` (с удалением дубликатов), `UNION ALL`, `INTERSECT`, `MINUS` (разность: строки первого
запроса, которых нет во втором, без дубликатов). ORDER BY в конце относится ко всему
результату.

### 12.5. Подзапросы и WITH

Скалярные подзапросы, `IN (подзапрос)`, `EXISTS`, подзапросы во FROM с псевдонимом,
предложение `WITH имя AS (...)` — как в стандарте SQL.

## 13. Оконные функции

`ROW_NUMBER()`, `RANK()`, `DENSE_RANK()`, `NTILE(n)`, `LAG(x [, k [, default]])`,
`LEAD(...)`, агрегаты `SUM`, `AVG`, `COUNT`, `MIN`, `MAX` с `OVER (PARTITION BY ... ORDER BY
...)`. Рамка по умолчанию при наличии ORDER BY — от начала раздела до текущей строки
включительно (с учётом строк с равным ключом), без ORDER BY — весь раздел. `NTILE(n)`
распределяет строки по n группам как можно равномернее, при неравенстве первые группы на одну
строку больше. `RANK` даёт равным значениям одинаковый ранг с пропуском следующих рангов.
Правило положения NULL из раздела 12.2 действует внутри OVER.

## 14. Подсказки и режимы совместимости

Подсказка — комментарий вида `/*+ ... */` сразу после SELECT основного запроса (раздел 2.2).
Подсказки `INDEX(...)`, `FULL(...)`, `PARALLEL(n)`, `FIRST_ROWS`, `NO_CACHE` влияют только на
план выполнения и на результат не влияют.

Подсказка **`COMPAT(5)`** включает для всего оператора режим совместимости с версией 5.x. Он
был нужен для отчётов, перенесённых из старой системы без переписывания. В этом режиме:

1. деление `/` двух целых значений — **целочисленное** с отбрасыванием дробной части к нулю
   (`7 / 2 = 3`); если хотя бы один операнд дробный, деление обычное;
2. конкатенация `||` и `CONCAT` с NULL (или пустой строкой) дают **NULL**;
3. NULL при сортировке располагаются **последними** при любом направлении (если не указано
   явно `NULLS FIRST`/`NULLS LAST`), в том числе внутри OVER.

Остальные правила (пустая строка = NULL, коллация, даты, DECODE и т. д.) в режиме `COMPAT(5)`
не меняются. Подсказка `COMPAT(5)`, расположенная не сразу после SELECT основного запроса,
ни на что не влияет.

## 15. Типичные идиомы старых отчётов

* `UPPER(o.channel)` в списке SELECT и в GROUP BY — чтобы `'web'`, `'Web'` и `'WEB'` попали в
  одну группу. В условиях WHERE `UPPER` не обязателен: сравнение и так без учёта регистра.
* `TRUNC(o.created_at) = TRUNC(o.created_at, 'MM')` — заказ сделан первого числа месяца.
* `o.created_at = TRUNC(o.created_at)` — заказ создан ровно в полночь (DATETIME сравнивается с
  DATE, т. е. с полночью того же дня).
* `NVL(oi.discount_pct, 0)` — строка без скидки.
* `ROUND(SUM(...), 2)` — денежные суммы.
* `'S' || LPAD(store_id, 3, '0')` — код магазина.
* Вложенные `IIF(..., IIF(...))` вместо CASE.

## 16. Соглашения переноса в SQLite

Эти соглашения согласованы с заказчиками и используются при сверке результатов.

1. **Хранение.** База `data/shop.sqlite` — результат миграции данных: DATE хранится текстом
   `'YYYY-MM-DD'`, DATETIME — текстом `'YYYY-MM-DD HH:MM:SS'`, NUMBER — REAL. Строки
   перенесены как есть: пустые строки остались пустыми строками, регистр и завершающие пробелы
   сохранены. Столбец `returns.return_date_txt` в старой системе имел тип VARCHAR и хранит дату
   текстом `'ДД.ММ.ГГГГ'`; он перенесён без изменений.
2. **Вывод дат.** Значения типа DATE выводятся строкой `'YYYY-MM-DD'`, DATETIME —
   `'YYYY-MM-DD HH:MM:SS'`. Результат `TO_CHAR` — строка ровно в заданном формате.
3. **Вывод чисел.** Числа сравниваются по значению с допуском 1e-6; целое 3 и дробное 3.0
   считаются равными. Представление (целое или REAL) не важно, важно значение.
4. **NULL и пустая строка** в результате считаются одинаковыми (в старой системе это одно и то же).
5. **Столбцы** — в том же порядке и в том же количестве, что в SELECT старого отчёта; имена
   столбцов не сверяются.
6. **Строки** — в том же порядке, что выдавал старый сервер (порядок задан ORDER BY каждого
   отчёта и однозначен).
7. **Точность воспроизведения.** Переносится поведение, а не замысел: известные ошибки старых
   отчётов (например, лексикографическое сравнение текстовых дат или условие `<> ''`)
   воспроизводятся. Комментарии и заголовки отчётов не являются спецификацией.
8. Перенесённый отчёт — один оператор SELECT (допускается WITH) для SQLite 3 из стандартной
   библиотеки Python; пользовательские функции и расширения не регистрируются, база
   открывается только для чтения.

## 17. История изменений

Изменения перечислены по версиям. Промышленный сервер — версия 7.4, поэтому действуют **все**
изменения версий 7.1–7.4. Если описание в основных разделах расходится с этим разделом, верен
этот раздел.

### Версия 7.1

* `GREATEST` и `LEAST` **игнорируют NULL-аргументы**: результат вычисляется по не-NULL
  аргументам и равен NULL, только если все аргументы NULL. Пример:
  `GREATEST(created_at, shipped_at)` для неотгруженного заказа равно `created_at`.
  (Раздел 8 описывает поведение 7.0.)
* Исправлена ошибка, из-за которой `LISTAGG` не пропускал пустые строки.
* Ускорено выполнение `DISTINCT` по индексированным столбцам (на результат не влияет).

### Версия 7.2

* `TOP n PERCENT`: число строк теперь округляется **вверх** (k = CEIL(N · n / 100), где N —
  число строк результата). При N = 57 и `TOP 5 PERCENT` возвращается 3 строки
  (5 % от 57 = 2.85). (Раздел 12.1 описывает поведение 7.0.)
* Добавлена функция `MEDIAN`.
* Подсказка `FIRST_ROWS` больше не меняет порядок строк без ORDER BY (в отчётах магазина
  порядок всегда задан явно).

### Версия 7.3

* `LEN(s)` теперь возвращает длину строки **без учёта завершающих пробелов**:
  `LEN('AUR-KT017  ') = 9`. Ведущие пробелы учитываются. Для полной длины со всеми пробелами
  добавлена функция **`LENGTH(s)`**. (Раздел 7 описывает поведение 7.0.)
* `DATEDIFF` принимает единицу `'quarter'`.

### Версия 7.4

* `TO_CHAR` для дат понимает элемент `Q` и текст в двойных кавычках.
* Исправлена утечка памяти при `LISTAGG` на больших группах.
* Изменений семантики уже существовавших функций, влияющих на отчёты магазина, нет.
* В служебных сборках 7.4.2–7.4.5 добавлены функции `ADD_MONTHS` и `NEXT_DAY` и единица
  `'week'` для `DATEDIFF`; они и уточнения к `SUBSTR` и `LPAD` описаны в приложении В.

### Запланировано в версии 8.0 (на промышленном сервере НЕ действует)

* Сортировка NULL станет как в стандарте: первыми при ASC и последними при DESC.
* Деление двух целых станет целочисленным по умолчанию.
* Пустая строка перестанет быть равной NULL.
* `LIKE` станет чувствительным к регистру.

Эти изменения так и не были выпущены: система выводится из эксплуатации на версии 7.4.
"""
DIALECT_MD = DIALECT_1 + DIALECT_2 + DIALECT_3


# ==========================================================================
# schema.md
# ==========================================================================

SCHEMA_MD = """# Схема базы магазина (после миграции)

База `data/shop.sqlite` — копия промышленной базы «Корунд» магазина бытовой техники на конец
июня 2024 года, перенесённая в SQLite. Типы в колонке «Корунд» — исходные типы старой системы;
как они хранятся в SQLite, описано в разделе 16 `dialect.md`. В колонке «NULL» отмечены
столбцы, в которых допускаются отсутствующие значения; в столбцах VARCHAR с отметкой «да» в
данных встречаются и NULL, и пустые строки (в старой системе это одно и то же). В столбцах без
отметки значения всегда заполнены.

Значения, которые вводились вручную или приходили из разных систем, не нормализованы: регистр
букв и завершающие пробелы в них произвольны. Это относится к столбцам, помеченным «(ручной
ввод)».

## categories — категории товаров

| Столбец | Корунд | NULL | Описание |
|---|---|---|---|
| id | INTEGER | | идентификатор |
| name | VARCHAR(60) | | название |
| parent_id | INTEGER | да | родительская категория; NULL — корневая категория |

Категории двухуровневые: корневые категории и подкатегории. Товары привязаны к подкатегориям,
кроме категории «Разное», которая сама является корневой.

## products — товары

| Столбец | Корунд | NULL | Описание |
|---|---|---|---|
| id | INTEGER | | идентификатор |
| sku | VARCHAR(12) | | артикул `БРЕНД-КАТНОМЕР`, стандартная длина 9 символов (ручной ввод) |
| name | VARCHAR(120) | | название |
| brand | VARCHAR(30) | | бренд (из справочника, написание единое) |
| category_id | INTEGER | | категория |
| price | NUMBER(12,2) | | текущая цена |
| cost | NUMBER(12,2) | да | себестоимость |
| weight_g | INTEGER | да | вес в граммах |
| color | VARCHAR(20) | да | цвет |
| launched_on | DATE | | дата начала продаж |
| discontinued_on | DATE | да | дата снятия с продажи |

## customers — клиенты

| Столбец | Корунд | NULL | Описание |
|---|---|---|---|
| id | INTEGER | | идентификатор |
| full_name | VARCHAR(120) | | фамилия, имя, отчество через пробел |
| email | VARCHAR(80) | | e-mail (ручной ввод) |
| phone | VARCHAR(20) | да | телефон |
| city | VARCHAR(40) | | город |
| segment | VARCHAR(20) | да | сегмент: retail, wholesale, vip (ручной ввод) |
| birth_date | DATE | да | дата рождения |
| registered_at | DATETIME | | момент регистрации |
| is_active | CHAR(1) | | признак активности Y/N (ручной ввод) |
| referrer_id | INTEGER | да | клиент, который пригласил данного |

## stores — розничные магазины

| Столбец | Корунд | NULL | Описание |
|---|---|---|---|
| id | INTEGER | | идентификатор |
| name | VARCHAR(60) | | название |
| city | VARCHAR(40) | | город |
| opened_on | DATE | | дата открытия |
| closed_on | DATE | да | дата закрытия; NULL — магазин работает |
| area_m2 | INTEGER | да | торговая площадь |
| manager | VARCHAR(60) | да | управляющий |

## promotions — акции и промокоды

| Столбец | Корунд | NULL | Описание |
|---|---|---|---|
| code | VARCHAR(20) | | промокод (заглавными буквами) |
| title | VARCHAR(80) | | название акции |
| discount_pct | INTEGER | | размер скидки, % |
| valid_from | DATE | | первый день действия |
| valid_to | DATE | да | последний день действия; NULL — бессрочно |

## orders — заказы

| Столбец | Корунд | NULL | Описание |
|---|---|---|---|
| id | INTEGER | | идентификатор |
| customer_id | INTEGER | | клиент |
| store_id | INTEGER | да | магазин для заказов, оформленных в рознице; NULL — интернет-заказ |
| created_at | DATETIME | | момент создания |
| status | VARCHAR(12) | | new, paid, shipped, delivered, cancelled, returned (ручной ввод) |
| channel | VARCHAR(10) | | web, app, store, phone (ручной ввод) |
| promo_code | VARCHAR(20) | да | промокод, введённый покупателем (ручной ввод) |
| delivery_fee | NUMBER(10,2) | да | стоимость доставки; NULL для розничных заказов и неизвестных значений |
| shipped_at | DATETIME | да | момент отгрузки |
| delivered_on | DATE | да | дата доставки (для розничных заказов — дата покупки) |
| comment | VARCHAR(200) | да | комментарий покупателя |

## order_items — строки заказов

| Столбец | Корунд | NULL | Описание |
|---|---|---|---|
| order_id | INTEGER | | заказ |
| line_no | INTEGER | | номер строки |
| product_id | INTEGER | | товар |
| qty | INTEGER | | количество |
| unit_price | NUMBER(12,2) | | цена за единицу |
| discount_pct | INTEGER | да | скидка на строку, %; NULL — без скидки |

## payments — платежи

| Столбец | Корунд | NULL | Описание |
|---|---|---|---|
| id | INTEGER | | идентификатор |
| order_id | INTEGER | | заказ |
| paid_at | DATETIME | | момент платежа |
| amount | NUMBER(12,2) | | сумма |
| method | VARCHAR(10) | | card, cash, sbp (ручной ввод) |

## returns — возвраты

| Столбец | Корунд | NULL | Описание |
|---|---|---|---|
| id | INTEGER | | идентификатор |
| order_id | INTEGER | | заказ |
| product_id | INTEGER | | товар |
| qty | INTEGER | | возвращено единиц |
| reason_code | VARCHAR(20) | да | DEFECT, SIZE, CHANGED_MIND, DAMAGED (ручной ввод) |
| return_date_txt | VARCHAR(10) | | дата возврата **текстом** в формате `ДД.ММ.ГГГГ` |
| refund | NUMBER(12,2) | да | сумма к возврату |

## stock — остатки в магазинах

| Столбец | Корунд | NULL | Описание |
|---|---|---|---|
| store_id | INTEGER | | магазин |
| product_id | INTEGER | | товар |
| qty | INTEGER | да | учтённый остаток; NULL — позиция не пересчитана |
| counted_on | DATE | | дата последнего пересчёта |

## reviews — отзывы

| Столбец | Корунд | NULL | Описание |
|---|---|---|---|
| id | INTEGER | | идентификатор |
| product_id | INTEGER | | товар |
| customer_id | INTEGER | | автор |
| rating | INTEGER | да | оценка 1–5; NULL — отзыв без оценки |
| created_at | DATETIME | | момент публикации |
| body | VARCHAR(2000) | да | текст отзыва |
"""


# ==========================================================================
# Legacy file headers (history, notes, distractors)
# ==========================================================================

AUTHORS = ["А. Пешков", "И. Громова", "С. Лаптев", "Н. Воронцова", "Д. Юсупов", "Е. Кравец",
           "О. Мельник", "Р. Сафин", "Т. Белоусова", "В. Жарков"]
HISTORY = [
    "первая версия", "переписан на новую таблицу платежей", "добавлена сортировка по заказу аналитиков",
    "убран фильтр по региону (теперь все города)", "исправлена опечатка в псевдониме столбца",
    "добавлены комментарии", "оптимизация: подзапрос вынесен в WITH", "проверка после обновления сервера, без изменений логики",
    "прогон на копии базы после обновления сервера, расхождений нет", "добавлен столбец по просьбе заказчика",
    "изменён период отчёта", "округление сумм до копеек", "уточнены условия по статусам заказов",
    "формат дат приведён к ДД.ММ.ГГГГ", "добавлена подсказка PARALLEL для ночного прогона",
    "подсказка PARALLEL удалена", "переименованы столбцы для выгрузки в Excel",
    "сверено с бухгалтерией, расхождений нет", "временно отключён фильтр по каналу 'phone'",
    "вернули прежнюю сортировку", "перенесён из старого сервера отчётов",
    "добавлен фильтр по статусу после аудита", "исправлено деление на ноль в показателе доли",
    "отчёт переведён на ночной прогон", "удалён неиспользуемый столбец", "добавлена сортировка по коду",
    "сверка с отделом продаж: расхождения объяснены возвратами", "расширен период по просьбе заказчика",
    "в заголовок добавлено описание выходных столбцов", "проверено после переезда базы на новый кластер",
    "исправлен псевдоним таблицы в подзапросе", "добавлена подсказка COMPAT(5) на время сверки, затем убрана",
]
NOTES = [
    "Отчёт выгружается в Excel, заказчик сам строит по нему диаграммы.",
    "Не запускать в рабочее время: запрос тяжёлый.",
    "Цифры в отчёте могут не совпадать с 1С из-за разного учёта возвратов — это нормально.",
    "По словам заказчика, NULL в дате доставки означает самовывоз (не проверено).",
    "Вопросы по отчёту — в канал #reports.",
    "Статусы заказов в разных филиалах вводили по-разному, см. справочник статусов.",
    "Отчёт используется в ежемесячной презентации для правления.",
    "Раньше отчёт строился по витрине DM_SALES, витрина удалена в 2021 году.",
    "Даты в отчёте — по времени сервера (Москва).",
    "Копия отчёта для франчайзи лежит в папке /reports/franchise (не поддерживается).",
    "Сортировку не менять: на ней завязан макрос в Excel.",
    "Сумма с доставкой считается в отдельном отчёте.",
    "Отчёт сверяли с ручной выборкой за один месяц — совпало.",
    "Результат рассылается по почте руководителям направлений в 07:00.",
    "При переносе на новую систему проверить отдельно пограничные даты.",
    "Заказчик просил не округлять промежуточные значения.",
    "В 2020 г. отчёт на месяц отключали из-за переделки справочника товаров.",
    "Если столбец пустой, значит данных за период нет — это не ошибка.",
    "Порядок строк важен: отчёт вставляется в презентацию как есть.",
    "Похожий отчёт для франчайзи считается по-другому, не путать.",
]
SCHEDULES = ["ежедневно в 06:00", "по понедельникам в 07:30", "ежемесячно, 2-го числа, 05:00",
             "ежеквартально", "по запросу", "ежемесячно, 5-го числа, 06:00", "по пятницам в 18:00"]
OLD_FRAGMENTS = [
    "--   AND o.channel <> 'phone'   -- отключено 2021",
    "--   AND o.created_at >= '01.01.2022'",
    "-- ORDER BY 2 DESC",
    "--   AND c.city IN ('Москва', 'Санкт-Петербург')",
    "-- SELECT TOP 100 *",
    "--   AND NVL(oi.discount_pct, 0) < 50",
    "--   , MAX(o.created_at) AS last_at",
    "--   AND o.store_id IS NOT NULL",
    "-- GROUP BY UPPER(o.status)",
    "--   AND p.brand <> 'Zefir'   -- Zefir вернули в ассортимент",
]


# (condition on legacy body, [(question, answer), ...]) — paraphrased restatements of dialect rules
DISCUSS = [
    (lambda b, c: bool(re.search(r"(created_at|paid_at) BETWEEN '", b)), [
        ("В отчёте почти нет заказов за последний день периода, это ошибка?",
         "Нет. Литерал даты без времени — это полночь, BETWEEN с моментом создания отсекает всё, что "
         "позже 00:00:00 последнего дня. Так отчёт работает с первой версии, менять не будем."),
        ("Почему за 30-е число в выгрузке только заказы, созданные ровно в полночь?",
         "Верхняя граница BETWEEN — дата без времени, сервер сравнивает DATETIME с полуночью этой даты."),
    ]),
    (lambda b, c: " DESC" in b and not c, [
        ("Строки с пустыми значениями стоят в самом начале, хотя сортировка по убыванию.",
         "Так сортирует Корунд: NULL всегда первыми, при ASC и при DESC. Макрос в Excel на это рассчитан."),
        ("Можно ли убрать пустые значения из начала отчёта?",
         "Можно только явным NULLS LAST, но заказчик попросил оставить как есть: пустые — первыми."),
    ]),
    (lambda b, c: bool(re.search(r"[\w)] / [\w(]", b)) and not c, [
        ("Показатель выводится с дробной частью, хотя оба слагаемых целые.",
         "В Корунд-SQL деление всегда дробное, даже для целых чисел; целочисленное — только DIV()."),
        ("Почему в столбце с долей 42.857..., а не 42?",
         "Деление / не отбрасывает дробную часть. Округлять не просили."),
    ]),
    (lambda b, c: "DECODE(" in b, [
        ("Пустые значения попадают в строку «не указана», а не в «прочее» — это правильно?",
         "Да: в DECODE NULL совпадает с NULL, а пустая строка для сервера — тот же NULL. Регистр "
         "кода при сравнении в DECODE тоже не важен."),
    ]),
    (lambda b, c: "(+)" in b, [
        ("Почему в отчёте есть строки без пары во второй таблице?",
         "Соединение внешнее (пометка (+)). Условия с (+) относятся к самому соединению и строки основной "
         "таблицы не отбрасывают."),
    ]),
    (lambda b, c: c, [
        ("Можно ли убрать странную подсказку в начале запроса?",
         "Нельзя: с COMPAT(5) деление целых целочисленное, NULL в конкатенации даёт NULL, а пустые значения "
         "при сортировке идут последними. Без неё цифры поменяются."),
        ("Почему среднее без дробной части?",
         "Отчёт работает в режиме совместимости COMPAT(5): там деление двух целых отбрасывает дробную часть."),
    ]),
    (lambda b, c: "LEN(" in b, [
        ("LEN показывает 9 символов, а в ячейке артикул с пробелом на конце.",
         "С версии 7.3 LEN не считает завершающие пробелы; полную длину даёт LENGTH."),
    ]),
    (lambda b, c: "PERCENT" in b, [
        ("Сколько строк должно быть в отчёте «верхние N процентов»?",
         "N процентов от числа строк с округлением вверх (так стало в 7.2; до этого округляли вниз)."),
    ]),
    (lambda b, c: "DATEADD('month'" in b or "DATEADD('quarter'" in b, [
        ("Для даты 31-го числа месяц вперёд даёт 30-е или 28/29-е — это баг?",
         "Нет, DATEADD по месяцам не перескакивает в следующий месяц, а берёт последний день целевого."),
    ]),
    (lambda b, c: "DATEDIFF(" in b, [
        ("Разница между 31.12 и 01.01 в годах получается 1, хотя прошёл один день.",
         "DATEDIFF считает пересечённые границы единиц, а не полные единицы. Так задумано."),
    ]),
    (lambda b, c: "'IW'" in b, [
        ("С какого дня начинается неделя в отчёте?",
         "С понедельника: TRUNC(..., 'IW') даёт понедельник ISO-недели, воскресенье относится к прошедшей неделе."),
    ]),
    (lambda b, c: "DAYOFWEEK" in b, [
        ("Какой день недели под номером 1?",
         "Понедельник. Суббота — 6, воскресенье — 7."),
    ]),
    (lambda b, c: " || " in b and not c, [
        ("Если телефона нет, строка контакта всё равно выводится?",
         "Да, NULL в конкатенации считается пустой строкой."),
    ]),
    (lambda b, c: "ADD_MONTHS(" in b, [
        ("Доставка 30.04, а визит в отчёте на 31.10 — откуда лишний день?",
         "Это ADD_MONTHS: последний день месяца переходит в последний день целевого месяца. "
         "Заказчик согласен, так и надо."),
        ("Почему не DATEADD('month', ...), как в других отчётах?",
         "Сервисный регламент требует, чтобы конец месяца оставался концом месяца. DATEADD так не умеет."),
        ("Для даты 30.04 ADD_MONTHS даёт 31.05? Похоже на ошибку сервера.",
         "Я тоже так думал, но в новой системе, говорят, будет 30.05. Пока оставляем как есть."),
    ]),
    (lambda b, c: "NEXT_DAY(" in b, [
        ("Если событие в понедельник, NEXT_DAY(..., 'MON') вернёт тот же день?",
         "Нет, следующую неделю: NEXT_DAY всегда строго после исходной даты."),
        ("Обзвон после доставки в понедельник назначен через неделю — это баг?",
         "Нет, NEXT_DAY берёт следующий понедельник, а не текущий. Так договорились с сервисом."),
    ]),
    (lambda b, c: "DATEDIFF('week'" in b or "DATEDIFF('quarter'" in b, [
        ("Между воскресеньем и понедельником DATEDIFF по неделям даёт 1, хотя прошёл день.",
         "Считаются границы недель, как и для месяцев: перевернули лист календаря — плюс один."),
        ("Почему разница между 15.11 и 15.01 — один квартал, а не ноль?",
         "Пересечена одна граница квартала (1 января). DATEDIFF считает границы, не полные кварталы."),
    ]),
    (lambda b, c: bool(re.search(r"SUBSTR\([^()]*(\([^()]*\))?[^()]*, 0, ", b)), [
        ("SUBSTR с нулевой позицией — это не ошибка? В других системах вернётся на символ меньше.",
         "У нас позиция 0 равна позиции 1. Отчёт выдаёт полные три (четыре) символа, так и должно быть."),
        ("Может, поправить SUBSTR(..., 0, ...) на SUBSTR(..., 1, ...)?",
         "Результат тот же, поэтому трогать не стали — боимся задеть макросы."),
    ]),
    (lambda b, c: "LPAD(" in b, [
        ("LPAD обрезает длинные значения — остаётся начало или конец?",
         "Начало: остаются первые n символов. Хвост отрезается."),
        ("Код 10-значного артикула на ценнике без последней цифры — так и задумано?",
         "Да, принтер берёт 9 знаков; LPAD оставляет первые девять. Претензий от магазинов не было."),
    ]),
    (lambda b, c: "return_date_txt >=" in b, [
        ("В отчёт попадают возвраты 2023 года, хотя фильтр «с мая 2024».",
         "Фильтр сравнивает текстовое поле со строкой посимвольно (ДД.ММ.ГГГГ). Заказчик в курсе, "
         "отчёт сверяется в таком виде, исправлять не будем."),
    ]),
]
GENERIC = [
    ("Можно ли добавить в отчёт выручку с доставкой?", "Нет, для этого есть отдельный отчёт."),
    ("Отчёт долго строится утром.", "Перенесли запуск на ночь, логику не меняли."),
    ("Цифры не совпадают с 1С.", "Разный учёт возвратов и частичных оплат, расхождение ожидаемо."),
    ("Нужна ли разбивка по регионам?", "Отложено до перехода на новую систему."),
    ("Можно ли выгружать отчёт в CSV?", "Да, выгрузка настроена, разделитель — запятая."),
    ("Почему в названиях столбцов латиница?", "Для совместимости с макросами Excel."),
    ("Можно ли запускать отчёт за произвольный период?", "Нет, период зашит в запрос; нужен новый — пишите заявку."),
    ("В отчёте нет заказов по телефону, это нормально?", "Смотрите условия отчёта: каналы в каждом свои."),
    ("Цифры за прошлый месяц поменялись задним числом.", "Возвраты и отмены доезжают с опозданием, это ожидаемо."),
    ("Кто владелец отчёта?", "Заказчик указан в заголовке; технически — отдел отчётности."),
    ("Можно ли добавить столбец с регионом?", "Регион в базе не хранится, только город."),
]


COLDESC = {
    "active": "число активных клиентов (признак Y)", "age_group": "возрастная группа",
    "age_label": "«возраст» для шаблона поздравления", "app_orders": "заказы из мобильного приложения",
    "area_m2": "торговая площадь, м²", "at_midnight": "заказы, созданные ровно в полночь",
    "avg_check": "средний чек, руб.", "avg_days": "средний срок, дней", "avg_fee": "средняя стоимость доставки, руб.",
    "avg_first_check": "средний чек первого заказа, руб.", "avg_price": "средняя цена, руб.",
    "avg_rating": "средняя оценка", "band": "диапазон скидки", "bday": "день и месяц рождения",
    "birth_date": "дата рождения", "brand": "бренд по справочнику", "brand_by_sku": "бренд по префиксу артикула",
    "brand_list": "список брендов через запятую", "brands": "число брендов", "bucket": "интервал давности, мес.",
    "category": "категория", "category_id": "код категории", "chain": "строка «клиент ← пригласивший»",
    "channel": "канал продаж (верхний регистр)", "city": "город", "closed_on": "дата закрытия",
    "code": "промокод", "cohort": "месяц первого заказа", "contact": "строка контакта",
    "contact_line": "строка контакта для обзвона", "cost": "себестоимость, руб.",
    "counted_on": "дата пересчёта", "customers": "число клиентов", "day_type": "будний или выходной день",
    "days_cover": "запас в днях продаж", "delivered_orders": "число доставленных заказов",
    "diff": "расхождение оплат и суммы заказа, руб.", "discount_pct": "размер скидки, %",
    "discounted_lines_pct": "доля строк со скидкой, %", "domain": "почтовый домен", "dow": "номер дня недели",
    "due_month": "месяц планового пересчёта", "evening_orders": "заказы после 18:00",
    "expiry_month": "месяц окончания гарантии", "fee_known": "заказы с известной стоимостью доставки",
    "fee_sum": "сумма стоимости доставки, руб.", "fees": "сумма доставки, руб.", "first_day": "заказы первого числа",
    "first_due": "первая плановая дата", "first_month_end": "последний день месяца открытия",
    "free_cnt": "заказы с бесплатной доставкой", "full_len": "полная длина артикула", "full_name": "ФИО",
    "greeting": "обращение для рассылки", "growth_pct": "прирост к предыдущему месяцу, %",
    "has_phone": "признак наличия телефона", "hh": "час суток", "id": "идентификатор",
    "in_period": "заказы в период действия акции", "items": "число единиц товара",
    "items_per_order": "единиц на заказ", "label": "подпись оценки", "last_activity": "момент последней активности",
    "last_due": "последняя плановая дата", "last_event": "дата последнего события",
    "last_order_at": "момент последнего заказа", "last_shipped_at": "момент последней отгрузки",
    "late_orders": "заказы со сроком больше трёх дней", "len_sku": "длина артикула (LEN)",
    "lines_cnt": "число строк заказов", "manager": "управляющий", "manager_label": "подпись управляющего",
    "margin": "маржа, руб.", "max_days": "максимальный срок, дней", "max_price": "максимальная цена, руб.",
    "median_check": "медианный чек, руб.", "methods": "способы оплаты", "min_months": "минимум месяцев в интервале",
    "min_price": "минимальная цена, руб.", "month_start": "первый день месяца", "months_open": "срок работы, мес.",
    "name": "наименование", "new_customers": "новые клиенты когорты", "opened_on": "дата открытия",
    "order_id": "номер заказа", "order_sum": "сумма заказа с доставкой, руб.", "orders_cnt": "число заказов",
    "paid": "оплачено, руб.", "paid_2023": "платежи за 2023 год, руб.", "pct_without": "доля без телефона, %",
    "period": "месяц в формате ММ.ГГГГ", "positions": "число позиций", "prev_revenue": "выручка предыдущего месяца",
    "price": "цена, руб.", "probation_end": "окончание испытательного срока магазина", "products": "число товаров",
    "qty": "количество", "quarter": "квартал", "rating": "оценка", "ratings": "число оценок",
    "reason": "причина возврата", "referrer_city": "город пригласившего", "refund": "сумма возврата, руб.",
    "refund_sum": "сумма возвратов, руб.", "reg_date": "дата регистрации", "repeat_pct": "доля повторных покупателей, %",
    "repeaters": "повторные покупатели", "ret_qty": "возвращено единиц", "return_date_txt": "дата возврата (текст)",
    "return_pct": "доля возвратов, %", "returns_cnt": "число возвратов", "revenue": "выручка, руб.",
    "revenue_per_m2": "выручка на м², руб.", "revenue_prev_year": "выручка того же месяца прошлого года",
    "reviews": "число отзывов", "rfm": "код RFM", "rnk": "ранг в категории", "root_category": "корневая категория",
    "running_cnt": "нарастающий итог заказов", "running_revenue": "нарастающий итог выручки",
    "same_city": "из одного ли города", "segment": "сегмент", "share_pct": "доля в выручке, %", "sku": "артикул",
    "sku_len": "длина артикула (LEN)", "sku_raw": "артикул в скобках (для контроля пробелов)",
    "sold_qty": "продано единиц", "status": "статус заказа", "stock_qty": "остаток, единиц", "store": "магазин",
    "store_code": "код магазина", "store_name": "название магазина", "stores": "число магазинов",
    "title": "название акции", "trial_end": "окончание пробного месяца", "units": "единиц товара",
    "unpaid_cnt": "заказы без оплаты доставки", "web_orders": "заказы с сайта", "week_start": "понедельник недели",
    "weekend_orders": "заказы в выходные", "weekend_pct": "доля заказов в выходные, %",
    "with_comment_v1": "заказы с комментарием, вариант 1", "with_comment_v2": "заказы с комментарием, вариант 2",
    "with_comment_v3": "заказы с комментарием, вариант 3", "with_delivered": "клиенты с доставленными заказами",
    "with_phone": "клиенты с телефоном", "with_refund": "возвраты с суммой", "with_spaces": "коды с лишними пробелами",
    "within_month": "возвраты в пределах месяца после доставки", "without_phone": "клиенты без телефона",
    "without_text": "отзывы без текста", "ym": "месяц в формате ГГГГ-ММ", "yoy_pct": "изменение год к году, %",
    "active_cnt": "из них в продаже", "active_customers": "клиенты с заказами в квартале",
    "active_pct": "доля активных клиентов, %", "amount": "сумма платежей, руб.",
    "audit_due": "дата аудита (ADD_MONTHS)", "avg_quarters": "среднее число кварталов",
    "avg_weeks": "средний срок в неделях", "brand4": "первые 4 символа бренда",
    "call_day": "понедельник обзвона", "city_prefix": "первые 3 буквы города", "client_code": "внутренний код клиента",
    "end_or_review": "окончание акции или годовой пересмотр", "gateway_phone": "номер для шлюза (12 знаков)",
    "label_sku": "артикул для ценника (9 знаков)", "later": "доставлено через 2 недели и позже",
    "launch_ym": "месяц запуска ГГГГ-ММ", "mailbox": "имя почтового ящика", "manager_initial": "инициал управляющего",
    "max_quarters": "максимум кварталов", "method": "способ оплаты", "next_week": "доставлено на следующей неделе",
    "payments_cnt": "число платежей", "products_cnt": "число товаров", "quarter_no": "квартал от первого заказа",
    "recount_day": "день следующего пересчёта", "review_date": "дата пересмотра условий",
    "review_day": "воскресенье разбора", "reviews_cnt": "число отзывов", "same_week": "доставлено на той же неделе",
    "settle_day": "пятница перечисления", "short_name": "краткое название (20 знаков)",
    "state": "состояние магазина", "store_orders": "заказы в магазинах", "till_code": "код для кассы (8 знаков)",
    "unknown_qty": "позиции без остатка", "valid_from": "начало акции", "visit_date": "дата сервисного визита",
    "week_no": "номер недели года", "weeks_to_first": "недель до первого заказа",
}


def legacy_text(num: int, slug: str, title: str, dept: str, purpose: str, notes: tuple[str, ...],
                body: str, columns: list[str] = ()) -> str:
    r = random.Random(f"{TASK_ID}/hdr/{num}")
    bar = "-- " + "=" * 76
    lines = [bar, f"-- Отчёт R{num:02d}. {title}", f"-- Файл: r{num:02d}_{slug}.sql"
             "            Система: Корунд-Отчёты 7.4",
             f"-- Заказчик: {dept} ({r.choice(AUTHORS)})", "-- Назначение:"]
    lines += [f"--   {x}" for x in purpose.split("\n")]
    if columns:
        lines.append("-- Выходные столбцы:")
        for i, c in enumerate(columns, 1):
            lines.append(f"--   {i:2d}. {c:22s} {COLDESC[c]}")
    lines += [f"-- Расписание: {r.choice(SCHEDULES)}", "-- История изменений:"]
    hist = ["первая версия"] + r.sample(HISTORY[1:], r.randint(4, 9))
    start = dt.date(r.randint(2012, 2017), r.randint(1, 12), r.randint(1, 28))
    span = (dt.date(2024, 5, 31) - start).days
    days = sorted(r.sample(range(span), len(hist)))
    for off, h in zip(days, hist, strict=True):
        d = start + dt.timedelta(days=off)
        lines.append(f"--   {d.strftime('%d.%m.%Y')}  {r.choice(AUTHORS):14s} {h}")
    lines.append("-- Примечания:")
    for n in list(notes) + r.sample(NOTES, r.randint(2, 4)):
        lines.append(f"--   * {n}")
    compat = bool(re.match(r"\s*SELECT /\*\+ COMPAT\(5\)", body))
    talk = []
    for cond, variants in DISCUSS:
        if cond(body, compat):
            talk += r.sample(variants, min(len(variants), r.choice([1, 2])))
    talk += r.sample(GENERIC, r.randint(2, 3))
    r.shuffle(talk)
    lines.append("-- Обсуждение (из заявок в поддержку отчётов):")
    stamps = sorted(dt.date(r.randint(2016, 2023), r.randint(1, 12), r.randint(1, 27)) for _ in talk)
    for (q, a), d1 in zip(talk, stamps, strict=True):
        lines.append(f"--   {d1.strftime('%d.%m.%Y')} {r.choice(AUTHORS)}: {q}")
        lines.append(f"--   {(d1 + dt.timedelta(days=1)).strftime('%d.%m.%Y')} {r.choice(AUTHORS)}: {a}")
    lines.append(bar)
    lines.append("")
    out = body.split("\n")
    old_version = _old_version(r, body)
    # sprinkle stale commented-out fragments between lines (never inside a clause)
    for frag in r.sample(OLD_FRAGMENTS, r.randint(2, 4)):
        pos = r.randint(1, max(1, len(out) - 1))
        out.insert(pos, frag)
    if r.random() < 0.5:
        return "\n".join(lines + old_version + [""] + out) + "\n;\n"
    return "\n".join(lines + out) + "\n;\n\n" + "\n".join(old_version) + "\n"


def _old_version(r: random.Random, body: str) -> list[str]:
    """A commented-out older variant of the query (kept in legacy files 'for history')."""
    text = re.sub(r"'(\d\d)\.(\d\d)\.(20\d\d)'",
                  lambda mm: f"'{mm.group(1)}.{mm.group(2)}.{int(mm.group(3)) - 1}'", body)
    text = re.sub(r"TOP (\d+)", lambda mm: f"TOP {max(5, int(mm.group(1)) - 5)}", text)
    src = text.split("\n")
    ands = [i for i, ln in enumerate(src) if ln.lstrip().startswith("AND ") and "(+)" not in ln]
    if ands:
        del src[r.choice(ands)]
    year = r.randint(2019, 2023)
    head = r.choice([
        f"Старая версия запроса (до {year} г.), оставлена для истории. НЕ ИСПОЛЬЗУЕТСЯ.",
        f"Вариант {year} года — закомментирован после сверки с заказчиком.",
        f"Предыдущая редакция ({year}). Не удалять до окончания сверки.",
    ])
    if r.random() < 0.5 and "*/" not in text:
        return [f"/* {head}", *src, "*/"]
    return [f"-- {head}", *(f"-- {ln}" for ln in src)]


# ==========================================================================
# Pilot migration notes (worked examples; not among the 70 reports)
# ==========================================================================

def _p1(m):
    return f"""SELECT UPPER(o.status) AS status,
       COUNT(*) AS orders_cnt,
       SUM({IIF(m, E(m, "o.promo_code") + " IS NULL", "0", "1")}) AS with_promo
  FROM orders o
 WHERE {SIN(m, 'o.channel', ['web', 'app'])}
   AND {DTB(m, 'o.created_at', '01.10.2023', '31.10.2023')}
 GROUP BY UPPER(o.status)
 ORDER BY 1"""


def _p2(m):
    return f"""SELECT p.id,
       {LEN(m, 'p.sku')} AS sku_len,
       {NVL(m, E(m, 'p.color'), "'не указан'")} AS color,
       {CAT(m, 'p.brand', "' / '", E(m, 'p.color'))} AS label
  FROM products p
 WHERE p.category_id = 2
 ORDER BY p.id"""


def _p3(m):
    frm = A(m, """  FROM customers c, (SELECT o.customer_id, COUNT(*) AS cnt
                       FROM orders o, returns r
                      WHERE r.order_id = o.id
                      GROUP BY o.customer_id) x
 WHERE x.customer_id(+) = c.id""", """  FROM customers c
  LEFT JOIN (SELECT o.customer_id, COUNT(*) AS cnt
               FROM orders o JOIN returns r ON r.order_id = o.id
              GROUP BY o.customer_id) x ON x.customer_id = c.id
 WHERE 1 = 1""")
    return f"""SELECT {TOP(m, 10)}c.id,
       x.cnt AS returns_cnt
{frm}
   AND c.city = 'Тверь'
 ORDER BY {DESC(m, 'x.cnt')}, c.id
{LIMIT(m, 10)}"""


def _p4(m):
    return f"""SELECT pr.code,
       pr.valid_to,
       {ADDM(m, 'pr.valid_to', 1)} AS archive_on
  FROM promotions pr
 WHERE pr.valid_to IS NOT NULL
 ORDER BY pr.code"""


def _p5(m):
    lab = DECODE(m, "pm.method", [("CARD", "'карта'"), ("CASH", "'наличные'"), ("SBP", "'СБП'")], "'иное'")
    return f"""SELECT {lab} AS method,
       COUNT(*) AS payments,
       {DIV(m, 'COUNT(DISTINCT pm.order_id)', 'COUNT(*)')} AS orders_per_payment
  FROM payments pm
 GROUP BY {lab}
 ORDER BY 1"""


PILOT = [
    (_p1, "Статусы интернет-заказов за октябрь 2023",
     "Первая версия переноса считала заказы только со статусами в нижнем регистре и включала весь день "
     "31 октября. Исправлено: сравнение статуса и канала без учёта регистра, верхняя граница периода — "
     "полночь 31.10.2023 (дата без времени). Пустой промокод считается отсутствующим — в SQLite это "
     "надо учитывать явно."),
    (_p2, "Чайники: артикулы и цвета",
     "Расхождения были в длине артикула (в SQLite length считает завершающие пробелы, а LEN в 7.4 — нет) "
     "и в строках, где цвет — пустая строка: в старой системе это NULL, поэтому NVL подставляет "
     "«не указан», а склейка не превращается в NULL."),
    (_p3, "Клиенты из Твери с наибольшим числом возвратов",
     "Первый перенос выдавал сначала клиентов с возвратами. Старый сервер ставит NULL первыми и при DESC, "
     "поэтому в первых строках — клиенты без возвратов (x.cnt = NULL) по возрастанию id. Добавлено "
     "NULLS FIRST."),
    (_p4, "Даты архивации промокодов",
     "Для акций, заканчивающихся 31-го числа, SQLite без дополнительных мер переносит дату в следующий "
     "месяц; старый сервер берёт последний день целевого месяца. Переписано с явной проверкой."),
    (_p5, "Способы оплаты",
     "DECODE сравнивает без учёта регистра, поэтому 'card' и 'CARD' попадают в одну группу «карта». "
     "Деление двух COUNT в старой системе дробное — в SQLite нужно явное приведение к REAL."),
]


def render_pilot() -> str:
    out = ["""# Пилотная миграция: разобранные примеры

В пилотной фазе проекта были перенесены пять вспомогательных отчётов, которые **не входят** в
70 основных. Для каждого приведены исходный текст на Корунд-SQL, принятый перенос на SQLite и
замечания по ошибкам первой попытки. Переносы проверены сверкой со старым сервером.
"""]
    for n, (fn, title, note) in enumerate(PILOT, 1):
        out.append(f"""
## П{n}. {title}

Исходный текст (Корунд-SQL):

```sql
{fn("leg")}
```

Принятый перенос (SQLite):

```sql
{fn("gold")}
```

Замечания: {note}
""")
    return "".join(out)


# ==========================================================================
# Build, run, compare
# ==========================================================================

N = 70
VISIBLE_SEED = f"{TASK_ID}/visible"
HIDDEN_SEED = f"{TASK_ID}/hidden"


def _render(i: int, mode: str) -> str:
    slug, title, _dept, _purpose, _notes, fn = QUERIES[i - 1]
    return f"-- r{i:02d}: перенос отчёта «{title}» на SQLite\n{fn(mode)}\n;\n"


def _run(db: bytes, sql: str, timeout: float = 20.0) -> list[tuple]:
    con = sqlite3.connect(":memory:")
    try:
        con.deserialize(db)
        con.execute("PRAGMA query_only = ON")
        deadline = time.monotonic() + timeout
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20000)
        return con.execute(sql).fetchmany(20000)
    finally:
        con.close()


def _cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return str(int(v)) if not math.isnan(v) else "nan"
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)


def _csv(names: list[str], rows: list[tuple]) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(names)
    for row in rows:
        w.writerow([_cell(v) for v in row])
    return buf.getvalue()


@functools.cache
def build() -> dict:
    assert len(QUERIES) == N
    vis = gen_db(VISIBLE_SEED, 1.0, 3600)
    files: dict[str, str] = {"dialect.md": DIALECT_MD, "schema.md": SCHEMA_MD, "pilot_migration.md": render_pilot()}
    for i, (slug, title, dept, purpose, notes, fn) in enumerate(QUERIES, 1):
        con = sqlite3.connect(":memory:")
        con.deserialize(vis)
        cur = con.execute(_render(i, "gold"))
        rows = cur.fetchall()
        names = [d[0] for d in cur.description]
        con.close()
        files[f"legacy/r{i:02d}_{slug}.sql"] = legacy_text(i, slug, title, dept, purpose, notes, fn("leg"), names)
        files[f"expected/r{i:02d}.csv"] = _csv(names, rows)
    return {"db": vis, "files": files}


@functools.cache
def hidden() -> tuple[bytes, tuple]:
    db = gen_db(HIDDEN_SEED, 2.5, 3000)
    results = tuple(_run(db, _render(i, "gold")) for i in range(1, N + 1))
    return db, results


def _norm(v: object) -> object:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


def _same(a: object, b: object) -> bool:
    a, b = _norm(a), _norm(b)
    if a is None or b is None:
        return a is b
    if isinstance(a, float) or isinstance(b, float):
        try:
            x, y = float(a), float(b)
        except (TypeError, ValueError):
            return False
        return abs(x - y) <= 1e-6 + 1e-9 * abs(y)
    return a == b


def _rows_equal(got: list[tuple], exp: tuple | list) -> bool:
    if len(got) != len(exp):
        return False
    for g, e in zip(got, exp, strict=True):
        if len(g) != len(e) or not all(_same(x, y) for x, y in zip(g, e, strict=True)):
            return False
    return True


# ==========================================================================
# Task callables
# ==========================================================================

def setup(ws: Path) -> None:
    b = build()
    for rel, text in b["files"].items():
        write(ws, rel, text)
    write(ws, "data/shop.sqlite", b["db"])


def gold(ws: Path) -> None:
    for i in range(1, N + 1):
        write(ws, f"sqlite/r{i:02d}.sql", _render(i, "gold"))


def check(ws: Path) -> str:
    db, expected = hidden()
    ok = 0
    errors = []
    for i in range(1, N + 1):
        rel = f"sqlite/r{i:02d}.sql"
        if not (ws / rel).is_file():
            errors.append(f"r{i:02d}: нет файла")
            continue
        sql = read_text(ws, rel)
        try:
            rows = _run(db, sql)
        except Exception as exc:  # noqa: BLE001 — any SQL error fails this report
            errors.append(f"r{i:02d}: ошибка выполнения ({type(exc).__name__})")
            continue
        if _rows_equal(rows, expected[i - 1]):
            ok += 1
        else:
            errors.append(f"r{i:02d}: результат на проверочной базе не совпадает")
    return require_share(N, ok, min_share=0.96, what="отчёты", errors=errors)


def _near(mode: str, name: str):
    def damage(ws: Path) -> None:
        for i in range(1, N + 1):
            write(ws, f"sqlite/r{i:02d}.sql", _render(i, mode))
    damage.__name__ = name
    return damage


NEAR_MISSES = [
    _near("naive", "naive_syntax_only_port"),
    _near("nulls", "forget_nulls_first_on_desc"),
    _near("intdiv", "integer_division_kept"),
    _near("compat", "ignore_compat5_hint"),
    _near("dateadd", "dateadd_month_overflow"),
    _near("datelit", "date_literal_as_whole_day"),
    _near("case", "case_sensitive_string_compare"),
    _near("empty", "empty_string_is_not_null"),
    _near("addmonths", "add_months_as_dateadd"),
    _near("substr0", "substr_zero_position_sqlite"),
    _near("dweek", "datediff_week_quarter_as_elapsed"),
    _near("nextday", "next_day_includes_same_day"),
    _near("lpad", "lpad_keeps_string_tail"),
]

PROMPT = (
    "Компания выводит из эксплуатации старый сервер отчётов «Корунд-Отчёты» (диалект SQL "
    "«Корунд-SQL 7.4»). Нужно перенести 70 отчётов магазина на SQLite так, чтобы они выдавали "
    "ровно те же результаты, что и старый сервер.\n\n"
    "Что есть в рабочей папке:\n"
    "- `dialect.md` — справочник по семантике диалекта Корунд-SQL и соглашения переноса "
    "(раздел 16), включая историю изменений версий (раздел 17) и приложения;\n"
    "- `schema.md` — описание таблиц базы;\n"
    "- `legacy/rNN_*.sql` — 70 исходных отчётов (r01…r70) на Корунд-SQL, с заголовками, "
    "историей изменений и комментариями;\n"
    "- `data/shop.sqlite` — база магазина, перенесённая в SQLite;\n"
    "- `expected/rNN.csv` — результаты старых отчётов на этой базе (первая строка — имена "
    "столбцов, пустое поле — NULL, дробные числа округлены до 6 знаков).\n\n"
    "Задача: для каждого отчёта создай файл `sqlite/rNN.sql` (r01.sql … r70.sql) — один оператор "
    "SELECT (допускается WITH) для SQLite 3 из стандартного модуля Python `sqlite3`, без "
    "пользовательских функций и расширений. Переносится поведение старого кода по правилам "
    "`dialect.md`, в том числе известные ошибки старых отчётов; комментарии в старых файлах "
    "могут быть устаревшими. Столбцы — в том же порядке, что в старом отчёте; строки — в том же "
    "порядке.\n\n"
    "Проверка: каждый файл выполняется на другой базе с той же схемой, но другими данными "
    "(с большим количеством пограничных значений), и результат сравнивается с тем, что выдал "
    "бы старый сервер (числа — с допуском 1e-6, NULL и пустая строка считаются одинаковыми). "
    "Совпадение результатов на `data/shop.sqlite` с `expected/` необходимо, но не достаточно. "
    "Засчитывается, если верны не меньше 68 отчётов из 70. Материала много, справочник и "
    "каждый отчёт важно прочитать целиком."
)

TASK = long_task(
    id="task_400_sql_dialect_port",  # registry id; TASK_ID stays the generator seed
    name="Перенос 70 отчётов со старого диалекта SQL на SQLite",
    prompt=PROMPT,
    setup=setup,
    gold=gold,
    check=check,
    tags=("sql", "port", "reading"),
)

DIALECT_APP = """
---

## Приложение А. Разобранные примеры

Примеры взяты из заявок в поддержку за 2016–2023 годы. В каждом приведено выражение
Корунд-SQL, входные значения и результат, который выдавал сервер 7.4.

**А.1. Пустая строка в NVL.** Клиент с `phone = ''`: `NVL(phone, 'нет')` → `'нет'`;
`NVL2(phone, 'есть', 'нет')` → `'нет'`; `COUNT(phone)` эту строку не считает;
`phone IS NULL` → истина.

**А.2. Конкатенация.** `full_name || ', тел. ' || phone` при `phone = NULL` →
`'Иванова Анна Сергеевна, тел. '`. Тот же отчёт с подсказкой `COMPAT(5)` вернул бы NULL.
`'' || ''` → NULL.

**А.3. Деление.** `SUM(qty) / COUNT(DISTINCT order_id)` при сумме 7 и числе заказов 4 →
`1.75`. В режиме `COMPAT(5)` → `1`. `DIV(7, 4)` → `1` в любом режиме. `5 / 0` → NULL.

**А.4. Регистр.** `status = 'delivered'` истинно для `'DELIVERED'`, `'Delivered'`,
`'delivered'`. `status IN ('paid', 'shipped')` истинно для `'PAID'` и для `'Shipped'`.
`is_active = 'Y'` истинно для `'y'` и для `'Y '` (завершающий пробел не учитывается).
`GROUP BY status` даст три разные группы для `'PAID'`, `'Paid'` и `'paid'`.

**А.5. Промокоды.** `o.promo_code = p.code`: введённый покупателем `'spring24 '` совпадает
с кодом `'SPRING24'`. `LEN('spring24 ')` → 8, `LENGTH('spring24 ')` → 9.

**А.6. LIKE.** `email LIKE '%@MAIL%'` истинно для `'ivan@mail.example'`. `'abc ' LIKE 'abc'`
— ложь (пробел в LIKE значим).

**А.7. Сортировка.** Значения `stock.qty`: 5, NULL, 12, 0. `ORDER BY qty DESC` →
NULL, 12, 5, 0. `ORDER BY qty` → NULL, 0, 5, 12. `ORDER BY qty DESC NULLS LAST` →
12, 5, 0, NULL. С подсказкой `COMPAT(5)`: `ORDER BY qty DESC` → 12, 5, 0, NULL;
`ORDER BY qty` → 0, 5, 12, NULL.

**А.8. TOP.** `SELECT TOP 3 ... ORDER BY qty DESC` для данных из А.7 → NULL, 12, 5.
`TOP 10 PERCENT` при 57 строках результата → 6 строк (10 % от 57 = 5.7, округление вверх).

**А.9. Даты и полночь.** Заказ создан `'2024-03-31 00:00:00'`: условие
`created_at <= '31.03.2024'` — истина. Заказ `'2024-03-31 08:12:40'`: то же условие — ложь.
`created_at >= '01.03.2024'` — истина для обоих. `TRUNC(created_at) = '31.03.2024'` — истина
для обоих.

**А.10. DATEADD.** `DATEADD('month', 1, '30.01.2024')` → 29.02.2024;
`DATEADD('month', 12, '29.02.2024')` → 28.02.2025; `DATEADD('month', 1, '28.02.2024')` →
28.03.2024 (день сохраняется, «прилипания» к концу месяца нет); `DATEADD('day', -30,
'30.06.2024')` → 31.05.2024.

**А.11. DATEDIFF.** `DATEDIFF('day', '10.03.2024 23:30:00', '11.03.2024')` → 1.
`DATEDIFF('month', '31.03.2024', '01.04.2024')` → 1. `DATEDIFF('month', '01.03.2024',
'31.03.2024')` → 0. `DATEDIFF('year', '15.08.1990', '01.07.2024')` → 34 (полных лет 33).

**А.12. TRUNC.** Для `'2024-05-19 14:00:00'` (воскресенье): `TRUNC(d)` → 2024-05-19,
`TRUNC(d, 'IW')` → 2024-05-13, `TRUNC(d, 'MM')` → 2024-05-01, `TRUNC(d, 'Q')` → 2024-04-01,
`TRUNC(d, 'YYYY')` → 2024-01-01. `DAYOFWEEK(d)` → 7.

**А.13. DECODE и CASE.** `DECODE(reason, 'DEFECT', 'Брак', NULL, 'Не указана', 'Прочее')`:
для `'defect'` → `'Брак'`; для `''` → `'Не указана'`; для `NULL` → `'Не указана'`; для
`'SIZE'` → `'Прочее'`. А `CASE reason WHEN NULL THEN 'Не указана' ELSE 'Прочее' END` для NULL
даёт `'Прочее'`.

**А.14. GREATEST.** `GREATEST('2024-01-10 10:00:00', NULL)` → `'2024-01-10 10:00:00'`
(версия 7.4). `GREATEST(NULL, NULL)` → NULL. `LEAST(3, NULL, 1)` → 1.

**А.15. Внешнее соединение.**

    FROM customers c, orders o
    WHERE o.customer_id(+) = c.id AND o.status(+) = 'DELIVERED'

Клиент без доставленных заказов остаётся в результате с NULL в столбцах `o`. Если убрать
`(+)` у `o.status`, такой клиент пропадёт.

**А.16. Текстовая дата.** Для `return_date_txt = '02.01.2023'` условие
`return_date_txt >= '01.05.2024'` истинно (строка `'02...'` больше строки `'01...'`), а
`TO_DATE(return_date_txt, 'DD.MM.YYYY') >= '01.05.2024'` — ложно.

**А.17. Подсказка не на своём месте.**

    WITH x AS (SELECT /*+ COMPAT(5) */ customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id)
    SELECT customer_id, n / 3 FROM x

Подсказка стоит в части WITH, а не после SELECT основного запроса, поэтому режим совместимости
не включается и `n / 3` — обычное дробное деление.

**А.18. Медиана и LISTAGG.** `MEDIAN` по значениям 10, 40, 20, 30 → 25.
`LISTAGG(DISTINCT method, '+') WITHIN GROUP (ORDER BY method)` для `'CARD'`, `'card'`,
`'SBP'` → `'CARD+SBP+card'` (DISTINCT и сортировка — по точным значениям).

## Приложение Б. Краткая памятка переносчику

Памятку составили участники пилотной миграции. Она повторяет правила из основных разделов
и раздела 17 другими словами; при расхождении верен основной текст с учётом раздела 17.

* Пустая строка для старого сервера — это отсутствие значения. Всё, что верно для NULL, верно
  и для `''`.
* Строки в условиях сравниваются «по-человечески»: большие и маленькие буквы не различаются,
  хвостовые пробелы отбрасываются. В группировке и сортировке — нет.
* При сортировке отсутствующие значения всегда наверху, куда бы ни шла сортировка. Исключение —
  явное NULLS LAST и отчёты с COMPAT(5), где они внизу.
* Косая черта всегда делит «по-настоящему», с дробной частью. Исключение — COMPAT(5).
* Склейка строк не «ломается» от пустого куска. Исключение — COMPAT(5).
* Дата без времени — это полночь. Сравнивая момент времени с такой датой, помните, что конец
  дня в неё не входит.
* Прибавляя месяцы, сервер никогда не перепрыгивает в следующий месяц.
* DATEDIFF — это счётчик перевёрнутых листков календаря, а не секундомер.
* Неделя начинается в понедельник, у понедельника номер 1.
* GREATEST и LEAST в 7.4 пропускают пустые аргументы.
* TOP ... PERCENT в 7.4 округляет число строк вверх.
* LEN в 7.4 не видит хвостовых пробелов, LENGTH видит всё.
* В DECODE отсутствующее значение совпадает с NULL, а регистр не важен.
* Внешнее соединение со знаком (+) держит все свои помеченные условия внутри соединения.
* Текстовая дата без TO_DATE сравнивается как текст.
* План 8.0 не наступил: ни одно из его изменений не действовало.
* ADD_MONTHS — не то же самое, что DATEADD по месяцам: последний день месяца «прилипает» к
  последнему дню целевого месяца.
* NEXT_DAY никогда не возвращает сам исходный день — только следующий такой же день недели.
* Недели и кварталы в DATEDIFF — тоже перевёрнутые листки календаря: границы, а не полные
  недели и кварталы.
* Нулевая позиция в SUBSTR — это первая позиция.
* LPAD не только дополняет, но и обрезает — и оставляет начало строки.
"""
DIALECT_APP_V = """
## Приложение В. Функции и единицы, добавленные в сборках 7.4.x

Эти функции появились в служебных сборках 7.4.2–7.4.5 и есть на промышленном сервере. В
основные разделы справочника они не вошли, поэтому описаны здесь; при расхождении с
разделами 7 и 9 верно это приложение.

### В.1. ADD_MONTHS

`ADD_MONTHS(d, n)` прибавляет к дате d целое число месяцев n (n может быть отрицательным).
В отличие от `DATEADD('month', n, d)` (раздел 9.1), у ADD_MONTHS есть правило **«конца
месяца»**: если d — последний день своего месяца, результат — **последний день целевого
месяца**, даже если в целевом месяце дней больше. Если d — не последний день месяца, номер дня
сохраняется, а если такого дня в целевом месяце нет, берётся последний день целевого месяца
(как у DATEADD). Результат — DATE; `ADD_MONTHS(NULL, n)` — NULL.

| Выражение | ADD_MONTHS | DATEADD('month', ...) для сравнения |
|---|---|---|
| `ADD_MONTHS('30.04.2024', 1)` | 31.05.2024 | 30.05.2024 |
| `ADD_MONTHS('29.02.2024', 1)` | 31.03.2024 | 29.03.2024 |
| `ADD_MONTHS('28.02.2023', 12)` | 29.02.2024 | 28.02.2024 |
| `ADD_MONTHS('31.01.2024', 1)` | 29.02.2024 | 29.02.2024 |
| `ADD_MONTHS('30.01.2024', 1)` | 29.02.2024 | 29.02.2024 |
| `ADD_MONTHS('15.06.2024', 6)` | 15.12.2024 | 15.12.2024 |
| `ADD_MONTHS('30.06.2024', -1)` | 31.05.2024 | 30.05.2024 |
| `ADD_MONTHS('30.09.2023', 3)` | 31.12.2023 | 30.12.2023 |

Обратите внимание: 30.01.2024 — не последний день января, поэтому для него правило конца
месяца не действует, и результат определяется обычным отсечением по концу февраля.

### В.2. NEXT_DAY

`NEXT_DAY(d, 'DAY')` — ближайшая дата **строго после** d, приходящаяся на указанный день
недели. Дни недели задаются трёхбуквенными английскими сокращениями: `'MON'`, `'TUE'`,
`'WED'`, `'THU'`, `'FRI'`, `'SAT'`, `'SUN'` (регистр не важен). Если d само приходится на этот
день недели, результат — **через неделю**, а не d. Время суток отбрасывается, результат — DATE.

| Выражение | Результат |
|---|---|
| `NEXT_DAY('05.06.2024', 'MON')` (среда) | 10.06.2024 |
| `NEXT_DAY('10.06.2024', 'MON')` (понедельник) | 17.06.2024 |
| `NEXT_DAY('09.06.2024', 'SUN')` (воскресенье) | 16.06.2024 |
| `NEXT_DAY('08.06.2024', 'SUN')` (суббота) | 09.06.2024 |
| `NEXT_DAY('31.05.2024', 'FRI')` (пятница) | 07.06.2024 |

### В.3. DATEDIFF по неделям и кварталам

Как и для остальных единиц (раздел 9.2), `DATEDIFF` по неделям и кварталам считает
**пересечённые границы**, а не полные единицы.

* `'week'` — число границ недель (полуночей с воскресенья на понедельник) между a и b, т. е.
  разность понедельников их недель, делённая на 7: `(TRUNC(b, 'IW') − TRUNC(a, 'IW')) / 7`.
  `DATEDIFF('week', '09.06.2024', '10.06.2024') = 1` (воскресенье → понедельник), а
  `DATEDIFF('week', '10.06.2024', '16.06.2024') = 0` (понедельник → воскресенье той же недели).
  Время суток не учитывается.
* `'quarter'` — `(год(b) · 4 + квартал(b)) − (год(a) · 4 + квартал(a))`:
  `DATEDIFF('quarter', '31.03.2024', '01.04.2024') = 1`,
  `DATEDIFF('quarter', '01.01.2024', '31.03.2024') = 0`,
  `DATEDIFF('quarter', '15.11.2023', '15.01.2024') = 1` (из IV квартала в I),
  `DATEDIFF('quarter', '01.04.2023', '30.06.2024') = 4`.

Если a или b — NULL, результат NULL. Если b раньше a, результат отрицательный.

### В.4. SUBSTR с позицией 0

`SUBSTR(s, 0, n)` работает так же, как `SUBSTR(s, 1, n)`: позиция 0 считается первой.
Так было во всех версиях 7.x, но в разделе 7 это не отмечено. Примеры:
`SUBSTR('Москва', 0, 3) = 'Мос'`, `SUBSTR('ivan@corp.example', 0, INSTR('ivan@corp.example',
'@') - 1) = 'ivan'`, `SUBSTR(NULL, 0, 3)` — NULL, `SUBSTR('', 0, 3)` — NULL (пустая строка —
это NULL, раздел 4).

### В.5. LPAD: подробности

Уточнение к разделу 7. `LPAD(s, n, c)`:

* если строка короче n — слева дописываются символы c до длины n: `LPAD('AUR-KT17', 9, '0') =
  '0AUR-KT17'`;
* если строка ровно n символов — она не меняется;
* если строка **длиннее** n — остаются её **первые** n символов (обрезается конец, а не начало):
  `LPAD('AUR-KT0017', 9, '0') = 'AUR-KT001'`, `LPAD('+7 912 345-67-89', 12, '*') =
  '+7 912 345-6'`;
* число сначала переводится в строку: `LPAD(42, 5, '0') = '00042'`;
* `LPAD(NULL, n, c)` и `LPAD('', n, c)` — NULL.

### В.6. Разобранные заявки по новым функциям

**Заявка 1 (сервисный центр, 2023).** «Доставка 30.06.2023, визит по регламенту через шесть
месяцев. Отчёт показывает 31.12.2023, а в карточке клиента 30.12.2023 — где правда?» Ответ
отдела отчётности: отчёт считает `ADD_MONTHS(delivered_on, 6)`; 30.06 — последний день июня,
поэтому результат — последний день декабря, 31.12.2023. Карточка клиента считается другой
системой и к отчёту отношения не имеет. Для доставки 29.06.2023 оба источника дают 29.12.2023.

**Заявка 2 (бухгалтерия, 2024).** «Платёж прошёл в пятницу 07.06.2024 в 10:15. Почему в отчёте о
перечислениях он стоит на 14.06, а не на 07.06?» Ответ: `NEXT_DAY(TRUNC(paid_at), 'FRI')` — это
первая пятница строго после дня платежа. Для платежа в пятницу это следующая пятница. Платёж в
четверг 06.06.2024 попадает на 07.06.2024.

**Заявка 3 (CRM, 2024).** «Клиент зарегистрировался в воскресенье 07.01.2024 в 23:40, первый
заказ — в понедельник 08.01.2024 в 09:00. Отчёт пишет "1 неделя до первого заказа", хотя прошло
меньше суток.» Ответ: `DATEDIFF('week', ...)` считает границы недель; между воскресеньем и
понедельником граница есть, поэтому 1. Если бы оба события были в одну неделю (например,
понедельник и воскресенье), результат был бы 0.

**Заявка 4 (НСИ, 2022).** «В коде бренда для справочника поставщиков стоит SUBSTR(..., 0, 4).
Разве позиция 0 не ошибка?» Ответ: для Корунд-SQL позиция 0 и позиция 1 равнозначны, отчёт
выдаёт первые четыре символа (`'AURO'` для Aurora). При переносе на другие СУБД нужно помнить,
что там позиция 0 может означать другое.

**Заявка 5 (розница, 2023).** «На ценнике у товара с артикулом AUR-KT0017 напечатано
AUR-KT001. Куда делась семёрка?» Ответ: `LPAD(..., 9, '0')` для строки длиннее девяти символов
оставляет первые девять. Это известное ограничение принтера ценников, исправлять в отчёте не
будем.

**Заявка 6 (финансы, 2023).** «Товар запущен 15.11.2023 и снят 15.01.2024. Отчёт о жизненном
цикле пишет "1 квартал", хотя прошло два месяца, а у соседнего товара (01.01.2024 – 31.03.2024)
— "0 кварталов", хотя прошло три месяца без одного дня.» Ответ: `DATEDIFF('quarter', ...)`
считает пересечённые границы кварталов (1 января в первом случае, ни одной — во втором).
"""
DIALECT_MD = DIALECT_1 + DIALECT_2 + DIALECT_3 + DIALECT_APP + DIALECT_APP_V
