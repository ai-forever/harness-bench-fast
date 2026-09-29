"""long_05_parish_genealogy — генеалогия по метрическим книгам прихода (1860–1915).

Мир — модель прихода из четырёх селений: люди, браки, рождения, смерти, восприемники.
Метрические книги рендерятся из модели; каждая ссылка на человека в записи хранит,
кто это на самом деле и как именно он записан (церковная/бытовая форма имени,
фамилия по мужу, ошибка, исправленная позднее). Ответы вычисляются разрешением
ссылок; «наивные» режимы разрешения (без вариантов имён, без учёта возраста,
без исправлений, без смены фамилии) дают near-miss-ответы.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .common import long_task, norm, num, read_json, require_share, rng, write, write_json

TASK_ID = "long_05_parish_genealogy"
Y0, Y1 = 1860, 1915
OLD_ORTHO_UNTIL = 1890
N_QUESTIONS = 35
MIN_CORRECT = 32

MONTHS_G = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
            "сентября", "октября", "ноября", "декабря"]
MONTHS_G_CAP = [m.capitalize() for m in MONTHS_G]

# (бытовая форма, церковная форма, прочие формы, отчество от бытовой, отчество от церковной)
MALE = [
    ("Иван", "Иоанн", (), "Иванов", "Иоаннов"),
    ("Петр", "Петр", ("Пётр",), "Петров", "Петров"),
    ("Василий", "Василий", (), "Васильев", "Васильев"),
    ("Егор", "Георгий", ("Юрий",), "Егоров", "Георгиев"),
    ("Федор", "Феодор", ("Фёдор",), "Федоров", "Феодоров"),
    ("Яков", "Иаков", (), "Яковлев", "Иаковлев"),
    ("Кузьма", "Косма", (), "Кузьмин", "Космин"),
    ("Степан", "Стефан", (), "Степанов", "Стефанов"),
    ("Семен", "Симеон", ("Семён",), "Семенов", "Симеонов"),
    ("Осип", "Иосиф", (), "Осипов", "Иосифов"),
    ("Ефим", "Евфимий", (), "Ефимов", "Евфимиев"),
    ("Матвей", "Матфей", (), "Матвеев", "Матфеев"),
    ("Алексей", "Алексий", (), "Алексеев", "Алексиев"),
    ("Дмитрий", "Димитрий", (), "Дмитриев", "Димитриев"),
    ("Гаврила", "Гавриил", (), "Гаврилов", "Гавриилов"),
    ("Данила", "Даниил", (), "Данилов", "Даниилов"),
    ("Михаил", "Михаил", ("Михайло",), "Михайлов", "Михаилов"),
    ("Тимофей", "Тимофей", (), "Тимофеев", "Тимофеев"),
    ("Андрей", "Андрей", (), "Андреев", "Андреев"),
    ("Григорий", "Григорий", (), "Григорьев", "Григориев"),
    ("Илья", "Илия", (), "Ильин", "Илиин"),
    ("Никита", "Никита", (), "Никитин", "Никитин"),
    ("Сергей", "Сергий", (), "Сергеев", "Сергиев"),
    ("Афанасий", "Афанасий", (), "Афанасьев", "Афанасиев"),
    ("Максим", "Максим", (), "Максимов", "Максимов"),
    ("Трофим", "Трофим", (), "Трофимов", "Трофимов"),
    ("Филипп", "Филипп", (), "Филиппов", "Филиппов"),
    ("Павел", "Павел", (), "Павлов", "Павлов"),
    ("Прокофий", "Прокопий", (), "Прокофьев", "Прокопиев"),
    ("Фома", "Фома", (), "Фомин", "Фомин"),
    ("Кирилл", "Кирилл", (), "Кириллов", "Кириллов"),
    ("Никифор", "Никифор", (), "Никифоров", "Никифоров"),
]
FEMALE = [
    ("Прасковья", "Параскева", ()), ("Авдотья", "Евдокия", ()), ("Аксинья", "Ксения", ()),
    ("Агафья", "Агафия", ()), ("Настасья", "Анастасия", ()), ("Марья", "Мария", ()),
    ("Татьяна", "Татиана", ()), ("Арина", "Ирина", ()), ("Пелагея", "Пелагия", ("Палагея",)),
    ("Акулина", "Акилина", ()), ("Анна", "Анна", ()), ("Наталья", "Наталия", ()),
    ("Катерина", "Екатерина", ()), ("Лизавета", "Елисавета", ()), ("Федосья", "Феодосия", ()),
    ("Афросинья", "Евфросиния", ("Ефросинья",)), ("Марфа", "Марфа", ()), ("Дарья", "Дария", ()),
    ("Ульяна", "Иулиания", ()), ("Мавра", "Мавра", ()), ("Варвара", "Варвара", ()),
    ("Матрена", "Матрона", ("Матрёна",)), ("Анисья", "Анисия", ()), ("Фекла", "Фекла", ()),
    ("Василиса", "Василиса", ()), ("Ефимья", "Евфимия", ()), ("Степанида", "Стефанида", ()),
    ("Домна", "Домна", ()), ("Федора", "Феодора", ()), ("Ольга", "Ольга", ()),
]
M_BY = {m[0]: m for m in MALE}
F_BY = {f[0]: f for f in FEMALE}

SURNAMES = ["Лапин", "Кузьмин", "Сидоров", "Мохов", "Горшков", "Бурлаков", "Зотов", "Ерёмин", "Ляпунов",
            "Тюрин", "Шишкин", "Колобов", "Рыжов", "Пестов", "Сычёв", "Гущин", "Каштанов", "Воробьёв",
            "Прохоров", "Зуев", "Мякишев", "Ломакин", "Тарасов", "Буров"]

# (именительный, родительный, предложный с предлогом «в»)
VILLAGES = [
    ("село Покровское", "села Покровского", "в селе Покровском"),
    ("деревня Сосновка", "деревни Сосновки", "в деревне Сосновке"),
    ("деревня Заречье", "деревни Заречья", "в деревне Заречье"),
    ("деревня Малые Ключи", "деревни Малых Ключей", "в деревне Малых Ключах"),
]
VILLAGE_SHORT = ["Покровское", "Сосновка", "Заречье", "Малые Ключи"]

PRIESTS = [(1860, 1878, "священник Иоанн Смирнов", "дьячком Петром Воскресенским", "священником Иоанном Смирновым"),
           (1879, 1896, "священник Василий Преображенский", "псаломщиком Алексеем Троицким",
            "священником Василием Преображенским"),
           (1897, 1915, "священник Александр Никольский", "псаломщиком Николаем Успенским",
            "священником Александром Никольским")]

_SOFT = "гкхжшчщ"


def name_form(n: str, case: str) -> str:
    """Склонение имени (родительный, винительный, дательный, творительный)."""
    if case == "n":
        return n
    k = "gadi".index(case)
    irr = {"Павел": ("Павла", "Павла", "Павлу", "Павлом"), "Петр": ("Петра", "Петра", "Петру", "Петром"),
           "Пётр": ("Петра", "Петра", "Петру", "Петром"), "Илья": ("Ильи", "Илью", "Илье", "Ильёй"),
           "Михайло": ("Михайла", "Михайла", "Михайлу", "Михайлом")}
    if n in irr:
        return irr[n][k]
    male_cons = n[-1] not in "аяйь"
    if n.endswith("ия"):
        st = n[:-1]
        return st + ("и", "ю", "и", "ей")[k]
    if n.endswith("ья") or n.endswith("я"):
        st = n[:-1]
        return st + ("и", "ю", "е", "ей")[k]
    if n.endswith("а"):
        st = n[:-1]
        g = "и" if st[-1] in _SOFT else "ы"
        return st + (g, "у", "е", "ой")[k]
    if n.endswith("ий"):
        st = n[:-2]
        return st + ("ия", "ия", "ию", "ием")[k]
    if n[-1] in "йь":
        st = n[:-1]
        return st + ("я", "я", "ю", "ем")[k]
    assert male_cons
    return n + ("а", "а", "у", "ом")[k]


def adj_form(w: str, fem: bool, case: str) -> str:
    """Отчество в форме «Иванов/Иванова» и фамилии на -ов/-ин."""
    if case == "n":
        return w + ("а" if fem else "")
    if fem:
        return w + {"g": "ой", "a": "у", "d": "ой", "i": "ой"}[case]
    return w + {"g": "а", "a": "а", "d": "у", "i": "ым"}[case]


def old_ortho(text: str) -> str:
    """Дореформенная орфография: ъ на конце слов после согласной, і перед гласной."""
    text = re.sub(r"([бвгджзклмнпрстфхцчшщБВГДЖЗКЛМНПРСТФХЦЧШЩ])(?=[^А-Яа-яЁё]|$)", r"\1ъ", text)
    text = re.sub(r"и(?=[аеёиоуыэюяй])", "і", text)
    return text


def canon_name(tok: str) -> str | None:
    t = tok.replace("ё", "е").capitalize()
    for m in MALE:
        if t in {x.replace("ё", "е") for x in (m[0], m[1], *m[2])}:
            return m[0]
    for f in FEMALE:
        if t in {x.replace("ё", "е") for x in (f[0], f[1], *f[2])}:
            return f[0]
    return None


def canon_patr(tok: str) -> str | None:
    t = tok.replace("ё", "е").capitalize()
    for m in MALE:
        for base in (m[3], m[4]):
            b = base.replace("ё", "е")
            forms = {b, b + "а"}
            st = b[:-2] if b.endswith(("ов", "ев")) else b
            forms |= {st + "ович", st + "евич", st + "овна", st + "евна", b + "ич", b + "на"}
            if m[0] == "Илья":
                forms |= {"Ильич", "Ильинична"}
            if m[0] == "Кузьма":
                forms |= {"Кузьмич", "Кузьминична"}
            if m[0] == "Фома":
                forms |= {"Фомич", "Фоминична"}
            if m[0] == "Никита":
                forms |= {"Никитич", "Никитична"}
            if t in forms:
                return m[0]
    return None


def canon_surname(tok: str) -> str:
    t = tok.replace("ё", "е").capitalize()
    if t.endswith(("ова", "ева", "ина", "ына", "ёва")):
        t = t[:-1]
    return t


# --------------------------------------------------------------------------
# Модель
# --------------------------------------------------------------------------


@dataclass
class Person:
    pid: int
    fem: bool
    name: str  # бытовая форма
    patr: str  # бытовое имя отца
    surname: str  # фамилия при рождении (мужская форма)
    village: int
    born: date
    father: int | None = None
    mother: int | None = None
    died: date | None = None
    marriages: list = field(default_factory=list)  # [(дата, супруг)]
    birth_rec: object = None


@dataclass
class Ref:
    pid: int | None
    role: str
    name_w: str
    patr_w: str
    surname_w: str | None
    variant: bool
    nonmaiden: bool
    key: tuple
    wrong: object = "none"  # "none" — ошибки нет; иначе pid (или None), под которым ошибочно записан


@dataclass
class Record:
    kind: str  # birth | marriage | burial
    d: date
    village: int
    refs: dict
    extra: dict = field(default_factory=dict)
    year: int = 0
    no: int = 0


def ydiff(a: date, b: date) -> int:
    """Полных лет от a до b."""
    return b.year - a.year - ((b.month, b.day) < (a.month, a.day))


class Sim:
    n_founders = 7
    birth_p = 0.33

    def __init__(self, R):
        self.R = R
        self.P: list[Person] = []
        self.births: list[tuple] = []  # (дата, ребёнок)
        self.weddings: list[tuple] = []  # (дата, жених, невеста)
        self.popular: dict[int, list[int]] = {}
        self.by_name: dict[str, list[Person]] = {}

    # ---- состояние во времени ----
    def alive(self, p: Person, d: date) -> bool:
        return p.born <= d and (p.died is None or d < p.died)

    def spouse_at(self, p: Person, d: date):
        cur = None
        for md, s in p.marriages:
            if md <= d:
                cur = s
        if cur is None:
            return None
        return cur if self.alive(self.P[cur], d) else None

    def surname_at(self, p: Person, d: date) -> str:
        if not p.fem:
            return p.surname
        last = [s for md, s in p.marriages if md <= d]
        return self.P[last[-1]].surname if last else p.surname

    def village_at(self, p: Person, d: date) -> int:
        if not p.fem:
            return p.village
        last = [s for md, s in p.marriages if md <= d]
        return self.P[last[-1]].village if last else p.village

    def key(self, p: Person, d: date) -> tuple:
        return (p.name, p.patr, self.surname_at(p, d), self.village_at(p, d))

    def key_free(self, k: tuple, d: date, skip: int | None = None) -> bool:
        return not any(q.pid != skip and self.alive(q, d) and self.key(q, d) == k
                       for q in self.by_name.get(k[0], ()))

    # ---- рождение людей ----
    def new(self, fem, name, patr, surname, village, born, father=None, mother=None) -> Person:
        p = Person(len(self.P), fem, name, patr, surname, village, born, father, mother)
        self.P.append(p)
        self.by_name.setdefault(name, []).append(p)
        return p

    def pick_name(self, fem: bool, patr: str, surname: str, village: int, d: date, sibs: list[Person]) -> str | None:
        R = self.R
        dead_sibs = [s.name for s in sibs if s.fem == fem and s.died is not None and s.died <= d]
        live_names = {s.name for s in sibs if s.fem == fem and self.alive(s, d)}
        if dead_sibs and R.random() < 0.45:
            nm = R.choice(dead_sibs)
            if nm not in live_names and self.key_free((nm, patr, surname, village), d):
                return nm
        pool = [f[0] for f in FEMALE] if fem else [m[0] for m in MALE]
        for _ in range(60):
            nm = R.choice(pool)
            if nm in live_names:
                continue
            if self.key_free((nm, patr, surname, village), d):
                return nm
        return None

    def hazard(self, age: int) -> float:
        if age < 1:
            return 0.2
        if age < 5:
            return 0.045
        if age < 15:
            return 0.006
        if age < 50:
            return 0.009
        if age < 65:
            return 0.03
        if age < 80:
            return 0.08
        return 0.25

    def founders(self):
        R = self.R
        pools = [R.sample(SURNAMES, 7) for _ in range(4)]
        for v in range(4):
            for _ in range(self.n_founders):
                hb = date(R.randint(1812, 1836), R.randint(1, 12), R.randint(1, 28))
                wb = date(min(hb.year + R.randint(-1, 7), 1840), R.randint(1, 12), R.randint(1, 28))
                sur = R.choice(pools[v])
                h = None
                for _ in range(50):
                    nm = R.choice(MALE)[0]
                    k = (nm, R.choice(MALE)[0], sur, v)
                    if self.key_free(k, date(1859, 12, 31)):
                        h = self.new(False, k[0], k[1], sur, v, hb)
                        break
                if h is None:
                    continue
                wv = v if R.random() < 0.6 else R.randrange(4)
                for _ in range(50):
                    wn, wp = R.choice(FEMALE)[0], R.choice(MALE)[0]
                    if self.key_free((wn, wp, sur, v), date(1859, 12, 31)):
                        break
                w = self.new(True, wn, wp, R.choice(SURNAMES), wv, wb)
                md = date(max(hb.year + 20, wb.year + 18, 1830), R.choice([1, 2, 10, 11]), R.randint(1, 28))
                if md.year >= Y0:
                    md = date(1859, 1, R.randint(10, 28))
                h.marriages.append((md, w.pid))
                w.marriages.append((md, h.pid))
                d = md + timedelta(days=R.randint(300, 700))
                kids: list[Person] = []
                while d.year < Y0 and ydiff(wb, d) < 44:
                    fem = R.random() < 0.5
                    nm = self.pick_name(fem, h.name, sur, v, d, kids)
                    if nm and R.random() < 0.72:
                        kids.append(self.new(fem, nm, h.name, sur, v, d, h.pid, w.pid))
                    d += timedelta(days=R.randint(500, 1100))

    # ---- годовой цикл ----
    def related(self, a: Person, b: Person) -> bool:
        def anc(p: Person, depth: int) -> set:
            out, cur = set(), [p]
            for _ in range(depth):
                nxt = []
                for x in cur:
                    for y in (x.father, x.mother):
                        if y is not None:
                            out.add(y)
                            nxt.append(self.P[y])
                cur = nxt
            return out
        return bool(anc(a, 2) & anc(b, 2)) or a.pid in anc(b, 2) or b.pid in anc(a, 2)

    def rand_date(self, y: int, months=None) -> date:
        m = self.R.choice(months) if months else self.R.randint(1, 12)
        return date(y, m, self.R.randint(1, 28))

    def year(self, y: int):
        R = self.R
        jan1 = date(y, 1, 1)
        living = [p for p in self.P if self.alive(p, jan1)]
        # смерти
        for p in living:
            if R.random() < self.hazard(ydiff(p.born, jan1)):
                p.died = self.rand_date(y)
        # браки
        men = [p for p in living if not p.fem and self.alive(p, jan1)]
        R.shuffle(men)
        for g in men:
            age = ydiff(g.born, jan1)
            married = [s for _, s in g.marriages]
            if self.spouse_at(g, jan1) is not None:
                continue
            if not married:
                if not (20 <= age <= 32) or R.random() > 0.33:
                    continue
            else:
                wife = self.P[married[-1]]
                if age > 60 or wife.died is None or (jan1 - wife.died).days < 270 or R.random() > 0.55:
                    continue
            md = self.rand_date(y, [1, 1, 2, 2, 10, 10, 11, 11, 5, 7])
            if not self.alive(g, md):
                continue
            widows_ok = R.random() < (0.6 if married else 0.12)
            cands = []
            for b in self.P:
                if not b.fem or not self.alive(b, md) or b.pid == g.pid:
                    continue
                ba = ydiff(b.born, md)
                bm = [s for _, s in b.marriages]
                if bm:
                    if not widows_ok or self.spouse_at(b, md) is not None or ba > 45:
                        continue
                    last = self.P[bm[-1]]
                    if last.died is None or (md - last.died).days < 270:
                        continue
                elif not (17 <= ba <= (32 if married else 26)):
                    continue
                if b.died is not None and b.died <= md:
                    continue
                if self.related(g, b):
                    continue
                if not self.key_free((b.name, b.patr, g.surname, g.village), md, skip=b.pid):
                    continue
                cands.append(b)
            if not cands:
                continue
            b = R.choice(cands)
            g.marriages.append((md, b.pid))
            b.marriages.append((md, g.pid))
            self.weddings.append((md, g.pid, b.pid))
        # рождения
        for w in [p for p in self.P if p.fem and self.alive(p, jan1)]:
            h = self.spouse_at(w, jan1)
            if h is None or not (18 <= ydiff(w.born, jan1) <= 44) or R.random() > self.birth_p:
                continue
            hp = self.P[h]
            md = max(m for m, s in w.marriages if s == h)
            kids = [p for p in self.P if p.mother == w.pid]
            bd = self.rand_date(y)
            if (bd - md).days < 290 or (kids and (bd - max(k.born for k in kids)).days < 440):
                continue
            if not self.alive(w, bd) or not self.alive(hp, bd):
                continue
            fem = R.random() < 0.49
            sibs = [p for p in self.P if p.father == h]
            nm = self.pick_name(fem, hp.name, hp.surname, hp.village, bd, sibs)
            if nm is None:
                continue
            c = self.new(fem, nm, hp.name, hp.surname, hp.village, bd, h, w.pid)
            self.births.append((bd, c.pid))
            if R.random() < 0.2:
                dd_ = bd + timedelta(days=R.randint(3, 300))
                if dd_.year == y:
                    c.died = dd_

    def run(self):
        self.founders()
        for y in range(Y0, Y1 + 1):
            self.year(y)
        return self


# --------------------------------------------------------------------------
# Записи метрических книг
# --------------------------------------------------------------------------


class Books:
    def __init__(self, S: Sim, R):
        self.S, self.R, self.P = S, R, S.P
        self.recs: list[Record] = []
        self.corrections: list[tuple] = []  # (запись, роль, текст исправления)

    def church_p(self, d: date, kind: str) -> float:
        base = {"birth": 0.5, "marriage": 0.35, "burial": 0.3}[kind]
        return base + (0.15 if d.year <= 1878 else -0.1 if d.year >= 1897 else 0.0)

    def ref(self, pid, role, d, kind, surname=True, pid_written=None) -> Ref:
        """Ссылка на человека pid; pid_written — кого на самом деле вписали (ошибка)."""
        R = self.R
        p = self.P[pid if pid_written is None else pid_written]
        cp = self.church_p(d, kind)
        if p.fem:
            f = F_BY[p.name]
            name_w = f[1] if R.random() < cp else (R.choice(f[2]) if f[2] and R.random() < 0.3 else f[0])
        else:
            m = M_BY[p.name]
            name_w = m[1] if R.random() < cp else (R.choice(m[2]) if m[2] and R.random() < 0.3 else m[0])
        pm = M_BY[p.patr]
        patr_w = pm[4] if R.random() < cp * 0.6 else pm[3]
        sw = self.S.surname_at(p, d) if surname else None
        variant = name_w != p.name or patr_w != pm[3]
        r = Ref(pid, role, name_w, patr_w, sw, variant, bool(p.fem and sw and sw != p.surname),
                self.S.key(p, d))
        if pid_written is not None:
            r.pid, r.wrong = pid, pid_written
        return r

    def status(self, p: Person, d: date) -> str:
        if self.S.spouse_at(p, d) is not None:
            return "married"
        if any(md <= d for md, _ in p.marriages):
            return "widowed"
        return "single"

    def unique_live(self, p: Person, d: date) -> bool:
        return self.S.key_free(self.S.key(p, d), d, skip=p.pid)

    def godparents(self, c: Person, d: date):
        R, S = self.R, self.S
        f, m = self.P[c.father], self.P[c.mother]
        alive = [p for p in self.adults if S.alive(p, d)]
        men = [p for p in alive if not p.fem and ydiff(p.born, d) >= 17 and p.pid != f.pid]
        women = [p for p in alive if p.fem and ydiff(p.born, d) >= 14 and p.pid != m.pid]
        kin = {x for x in (f.father, f.mother, m.father, m.mother) if x is not None}
        kin_m = [p for p in men if p.father in kin or p.mother in kin]
        kin_w = [p for p in women if p.father in kin or p.mother in kin]
        pop = [self.P[x] for x in S.popular.get(d.year // 10 * 10, []) if S.alive(self.P[x], d) and x != f.pid]
        x = R.random()
        if pop and x < 0.28:
            g = R.choice(pop)
        elif kin_m and x < 0.6:
            g = R.choice(kin_m)
        else:
            g = R.choice(men)
        gm = R.choice(kin_w) if kin_w and R.random() < 0.4 else R.choice(women)
        return g, gm

    def build(self):
        S, R = self.S, self.R
        self.adults = [p for p in self.P if p.born.year < Y1 - 12]
        # «популярные» восприемники: по два на десятилетие
        for dec in range(1860, 1920, 10):
            cands = [p for p in self.P if not p.fem and p.born.year <= dec - 22
                     and (p.died is None or p.died.year > dec + 9)]
            S.popular[dec] = [p.pid for p in R.sample(cands, min(2, len(cands)))]
        for bd, cid in S.births:
            c = self.P[cid]
            g, gm = self.godparents(c, bd)
            refs = {"child": self.ref(cid, "child", bd, "birth", surname=False),
                    "father": self.ref(c.father, "father", bd, "birth"),
                    "mother": self.ref(c.mother, "mother", bd, "birth", surname=False),
                    "godfather": self.ref(g.pid, "godfather", bd, "birth"),
                    "godmother": self.ref(gm.pid, "godmother", bd, "birth",
                                          surname=self.status(gm, bd) != "married" or R.random() < 0.6)}
            rec = Record("birth", bd, c.village, refs, {"bapt": bd + timedelta(days=R.randint(1, 6)),
                                                         "g_status": self.status(g, bd),
                                                         "gm_status": self.status(gm, bd)})
            c.birth_rec = rec
            self.recs.append(rec)
        for md, gid, bid in S.weddings:
            g, b = self.P[gid], self.P[bid]
            maiden = not any(d0 < md for d0, _ in b.marriages)
            refs = {"groom": self.ref(gid, "groom", md, "marriage"),
                    "bride": self.ref(bid, "bride", md - timedelta(days=1), "marriage"),
                    "surety1": self.ref(R.choice([p for p in self.adults if not p.fem and S.alive(p, md)
                                                  and ydiff(p.born, md) >= 20 and p.pid != gid]).pid,
                                        "surety", md, "marriage"),
                    "surety2": self.ref(R.choice([p for p in self.adults if not p.fem and S.alive(p, md)
                                                  and ydiff(p.born, md) >= 20 and p.pid != gid]).pid,
                                        "surety", md, "marriage")}
            via_father = maiden and b.father is not None and R.random() < 0.8
            if via_father:
                refs["bride_father"] = self.ref(b.father, "bride_father", md, "marriage")
            e = {"maiden": maiden, "via_father": via_father,
                 "g_age": max(18, ydiff(g.born, md) + R.choice([0, 0, 0, 1, -1, 2, -2])),
                 "b_age": max(16, ydiff(b.born, md) + R.choice([0, 0, 0, 1, -1, 2, -2])),
                 "g_no": len([x for x in g.marriages if x[0] <= md]),
                 "b_no": len([x for x in b.marriages if x[0] <= md]),
                 "b_village": S.village_at(b, md - timedelta(days=1))}
            self.recs.append(Record("marriage", md, g.village, refs, e))
        for p in self.P:
            if p.died is None or not (Y0 <= p.died.year <= Y1):
                continue
            d = p.died
            age_days = (d - p.born).days
            st = self.status(p, d - timedelta(days=1))
            refs, e = {}, {"status": st, "age_days": age_days, "age_true": ydiff(p.born, d)}
            child = st == "single" and ydiff(p.born, d) < 15 and p.father is not None
            if child:
                refs["deceased"] = self.ref(p.pid, "deceased", d, "burial", surname=False)
                refs["father"] = self.ref(p.father, "father", d, "burial")
            elif p.fem and st == "married":
                refs["deceased"] = self.ref(p.pid, "deceased", d, "burial", surname=False)
                refs["husband"] = self.ref(S.spouse_at(p, d - timedelta(days=1)), "husband", d, "burial")
            else:
                refs["deceased"] = self.ref(p.pid, "deceased", d, "burial")
            e["child"] = child
            e["age_w"] = ydiff(p.born, d) + (0 if child else R.choice([0, 0, 0, 1, -1, 2, -2, 1]))
            e["buried"] = d + timedelta(days=R.randint(1, 3))
            e["cause"] = R.randrange(len(CAUSES_ADULT if not child else CAUSES_CHILD))
            self.recs.append(Record("burial", d, S.village_at(p, d), refs, e))
        self.recs.sort(key=lambda r: (r.d, r.kind))
        for y in range(Y0, Y1 + 1):
            for kind in ("birth", "marriage", "burial"):
                for i, r in enumerate([r for r in self.recs if r.d.year == y and r.kind == kind], 1):
                    r.year, r.no = y, i
        self.make_corrections()
        return self

    def make_corrections(self):
        R, S = self.R, self.S
        pool = [r for r in self.recs if r.year <= 1912]
        R.shuffle(pool)
        want = {"father": 7, "godfather": 4, "deceased": 5}
        for r in pool:
            if not any(want.values()):
                break
            for role in ("father", "godfather", "deceased"):
                if want[role] <= 0 or role not in r.refs or r.refs[role].wrong != "none":
                    continue
                if r.kind == "burial" and (role != "deceased" or r.extra["child"]
                                            or r.refs["deceased"].surname_w is None):
                    continue
                if r.kind != "birth" and role != "deceased":
                    continue
                true = r.refs[role].pid
                tp = self.P[true]
                d = r.d
                if role == "father":
                    alts = [p for p in self.P if not p.fem and p.pid != true and S.alive(p, d)
                            and S.village_at(p, d) == r.village and S.spouse_at(p, d) is not None]
                    bro = [p for p in alts if p.father is not None and p.father == tp.father]
                    alts = bro or alts
                elif role == "godfather":
                    alts = [p for p in self.adults if not p.fem and p.pid != true and S.alive(p, d)
                            and ydiff(p.born, d) >= 17 and p.pid != r.refs["father"].pid]
                else:
                    alts = [p for p in self.P if p.fem == tp.fem and p.pid != true and S.alive(p, d)
                            and S.village_at(p, d) == r.village and ydiff(p.born, d) >= 15
                            and self.status(p, d) == self.status(tp, d - timedelta(days=1))]
                if not alts:
                    continue
                w = R.choice(alts)
                old = r.refs[role]
                r.refs[role] = self.ref(true, old.role, d, r.kind, surname=old.surname_w is not None,
                                        pid_written=w.pid)
                self.corrections.append((r, role, min(Y1, r.year + R.randint(1, 3))))
                want[role] -= 1
                break


# --------------------------------------------------------------------------
# Разрешение ссылок и запросы
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Mode:
    variants: bool = True
    age: bool = True
    corr: bool = True
    surname: bool = True


TRUE = Mode()
NAIVE_MODES = {
    "first_match": Mode(False, False, False, False),
    "no_variants": Mode(variants=False),
    "no_age": Mode(age=False),
    "no_corrections": Mode(corr=False),
    "no_surname_change": Mode(surname=False),
}


class View:
    """Родословная, восстановленная из записей при заданном режиме разрешения ссылок."""

    def __init__(self, B: Books, mode: Mode, first: dict):
        self.B, self.P, self.mode = B, B.P, mode
        self.births_f: dict = {}
        self.births_m: dict = {}
        self.birth_of: dict = {}
        self.marr: dict = {}
        self.burial_of: dict = {}
        self.god: dict = {}
        for r in B.recs:
            res = {k: self.resolve(v, first) for k, v in r.refs.items()}
            r_res = res
            if r.kind == "birth":
                if res["child"] is not None:
                    self.birth_of.setdefault(res["child"], (r, r_res))
                if res["father"] is not None:
                    self.births_f.setdefault(res["father"], []).append((r, r_res))
                if res["mother"] is not None:
                    self.births_m.setdefault(res["mother"], []).append((r, r_res))
                if res["godfather"] is not None:
                    self.god.setdefault(res["godfather"], []).append(r)
            elif r.kind == "marriage":
                for role in ("groom", "bride"):
                    if res[role] is not None:
                        self.marr.setdefault(res[role], []).append((r, r_res, role))
            else:
                if res["deceased"] is not None:
                    self.burial_of.setdefault(res["deceased"], (r, r_res))

    def resolve(self, ref: Ref, first: dict):
        m = self.mode
        pid = ref.pid
        if ref.wrong != "none" and not m.corr:
            pid = ref.wrong
        if pid is None:
            return None
        if not m.variants and ref.variant:
            return None
        if not m.surname and ref.nonmaiden:
            return None
        if not m.age:
            pid = first.get(ref.key, pid)
        return pid

    # ---- производные ----
    def children(self, pid) -> list[int]:
        out = []
        for _r, res in self.births_f.get(pid, []) + self.births_m.get(pid, []):
            if res["child"] is not None and res["child"] not in out:
                out.append(res["child"])
        return out

    def wives(self, pid) -> list[tuple]:
        return sorted([(r.d, res["bride"], r) for r, res, role in self.marr.get(pid, []) if role == "groom"],
                      key=lambda x: x[0])

    def husbands(self, pid) -> list[tuple]:
        return sorted([(r.d, res["groom"], r) for r, res, role in self.marr.get(pid, []) if role == "bride"],
                      key=lambda x: x[0])


# --------------------------------------------------------------------------
# Рендеринг записей
# --------------------------------------------------------------------------

CAUSES_ADULT = ["от старости", "от горячки", "от чахотки", "от водянки", "от лихорадки", "от тифа",
                "от воспаления лёгких", "от удара", "от родов", "от холеры", "утонул(а) в реке", "от оспы"]
CAUSES_CHILD = ["от поноса", "от оспы", "от кори", "от скарлатины", "от коклюша", "от слабости",
                "от родимца", "от горячки", "от дифтерита"]


def plural(n: int, forms: tuple) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return forms[0]
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return forms[1]
    return forms[2]


def age_gen(n: int) -> str:
    """Возраст в родительном падеже: «21 года», «23 лет»."""
    return f"{n} {'года' if n % 10 == 1 and n % 100 != 11 else 'лет'}"


def ordinal_no(n: int) -> str:
    return {1: "первым", 2: "вторым", 3: "третьим", 4: "четвёртым"}.get(n, f"{n}-м")


class Render:
    def __init__(self, B: Books, R):
        self.B, self.R, self.P, self.S = B, R, B.P, B.S

    def full(self, ref: Ref, case: str = "n", surname: bool = True) -> str:
        p = self.P[ref.pid if ref.wrong == "none" else ref.wrong]
        parts = [name_form(ref.name_w, case), adj_form(ref.patr_w, p.fem, case)]
        if surname and ref.surname_w:
            parts.append(adj_form(ref.surname_w, p.fem, case))
        return " ".join(parts)

    def vil(self, v: int, home: int, case: str = "g") -> str:
        if v == home and self.R.random() < 0.3:
            return "того же села" if v == 0 else "той же деревни"
        return VILLAGES[v][1]

    def wvil(self, ref: Ref, rec: Record) -> str:
        return self.vil(ref.key[3], rec.village)

    def date_phrase(self, d: date) -> str:
        return self.R.choice([f"{d.day} {MONTHS_G[d.month - 1]}", f"{MONTHS_G_CAP[d.month - 1]} {d.day}-го",
                              f"{d.day}-го {MONTHS_G[d.month - 1]}"])

    def godfather(self, rec: Record) -> str:
        ref = rec.refs["godfather"]
        p = self.P[ref.pid if ref.wrong == "none" else ref.wrong]
        st = self.B.status(p, rec.d)
        title = "крестьянский сын" if st == "single" and ydiff(p.born, rec.d) < 25 and self.R.random() < 0.6 \
            else "крестьянин"
        return f"{self.wvil(ref, rec)} {title} {self.full(ref)}"

    def godmother(self, rec: Record) -> str:
        ref = rec.refs["godmother"]
        p = self.P[ref.pid]
        st = rec.extra["gm_status"]
        v = self.wvil(ref, rec)
        if st == "single":
            return f"{v} крестьянская девица {self.full(ref)}"
        if st == "widowed":
            return f"{v} крестьянская вдова {self.full(ref)}"
        if ref.surname_w:
            return f"{v} крестьянская жена {self.full(ref)}"
        h = self.P[self.S.spouse_at(p, rec.d)]
        hn = f"{name_form(h.name, 'g')} {adj_form(M_BY[h.patr][3], False, 'g')} {adj_form(h.surname, False, 'g')}"
        return f"{v} крестьянина {hn} жена {self.full(ref, surname=False)}"

    def birth(self, r: Record) -> str:
        R = self.R
        c, f, m = r.refs["child"], r.refs["father"], r.refs["mother"]
        fem = self.P[c.pid].fem
        sw = "дочь" if fem else "сын"
        rod, kr = ("родилась", "крещена") if fem else ("родился", "крещён")
        b = r.extra["bapt"]
        fv = self.vil(f.key[3], -1)
        gf, gm = self.godfather(r), self.godmother(r)
        pr = next(x for x in PRIESTS if x[0] <= r.year <= x[1])
        v = [
            f"{rod.capitalize()} {self.date_phrase(r.d)}, {kr} {b.day}-го. Родители: {fv} крестьянин "
            f"{self.full(f)} и законная жена его {self.full(m, surname=False)}, оба православного "
            f"вероисповедания. Младенцу дано имя {c.name_w}. Восприемники: {gf} и {gm}. Таинство крещения "
            f"совершал {pr[2]} с {pr[3]}.",
            f"{self.date_phrase(r.d).capitalize()} у {fv} крестьянина {self.full(f, 'g')} и законной жены "
            f"его {self.full(m, 'g', surname=False)} {rod} {sw} {c.name_w}, {kr} {self.date_phrase(b)}. "
            f"Восприемниками были {gf} да {gm}.",
            f"Младенец {c.name_w} ({sw}), {'рождена' if fem else 'рождён'} {self.date_phrase(r.d)}, {kr} "
            f"{b.day}-го числа. Отец — {fv} крестьянин {self.full(f)}, мать — законная его жена "
            f"{self.full(m, surname=False)}. Восприемники: {gf}; {gm}. Крестил {pr[2]}.",
            f"Месяца {MONTHS_G[r.d.month - 1]} {r.d.day}-го дня {rod}, а {b.day}-го {kr} {c.name_w}, {sw} "
            f"{fv} крестьянина {self.full(f, 'g')} и законной жены его {self.full(m, 'g', surname=False)}. "
            f"Восприемники: {gf} и {gm}.",
            f"Записан{'а' if fem else ''} под сим номером {sw} {fv} крестьянина {self.full(f, 'g')} и жены его "
            f"{self.full(m, 'g', surname=False)} — {c.name_w}; {rod} {self.date_phrase(r.d)}, {kr} "
            f"{self.date_phrase(b)}. При крещении восприемниками были {gf} и {gm}.",
        ]
        extra = ""
        if R.random() < 0.15:
            extra = R.choice([" Дитя слабое, крещено на дому повивальною бабкою, миропомазано в церкви.",
                              " Родители младенца — постоянные прихожане сей церкви.",
                              " Крещение совершено в церкви по причине благополучного состояния младенца.",
                              " Родился в ночь; повивальная бабка — крестьянская вдова той же деревни."])
            if fem:
                extra = extra.replace("Родился", "Родилась")
        tail = R.choice([
            f" Младенец крещён в приходской Покровской церкви; таинство крещения и миропомазания совершал {pr[2]}"
            f" с {pr[3]}.",
            f" Крещение совершено по чину в приходской церкви; таинство совершал {pr[2]}.",
            f" Родители — законнобрачные, православного вероисповедания; записал {pr[2]}.",
            f" Свидетельствую: {pr[2]}. Родители к записи руку приложили за неграмотностью через грамотея.",
        ])
        return R.choice(v) + extra + tail

    def age_w(self, r: Record) -> str:
        e = r.extra
        if e["child"]:
            days = e["age_days"]
            if days < 31:
                return f"{days} {plural(days, ('день', 'дня', 'дней'))}"
            if days < 365:
                mth = days // 30
                return f"{mth} {plural(mth, ('месяц', 'месяца', 'месяцев'))}"
            n = e["age_true"]
            return f"{n} {plural(n, ('год', 'года', 'лет'))}"
        n = e["age_w"]
        return f"{n} {plural(n, ('год', 'года', 'лет'))}"

    def burial(self, r: Record) -> str:
        R = self.R
        d = r.refs["deceased"]
        p = self.P[d.pid if d.wrong == "none" else d.wrong]
        e = r.extra
        um = "умерла" if p.fem else "умер"
        cause = (CAUSES_CHILD if e["child"] else CAUSES_ADULT)[e["cause"]]
        if cause in ("от родимца", "от слабости") and e["age_true"] >= 3:
            cause = "от горячки"
        if cause == "от родов" and not (p.fem and "husband" in r.refs and e["age_true"] <= 46):
            cause = "от горячки"
        if "(а)" in cause:
            cause = cause.replace("(а)", "а" if p.fem else "")
        if e["child"]:
            f = r.refs["father"]
            fdead = self.P[f.pid].died is not None and self.P[f.pid].died < r.d
            who = (f"{self.vil(f.key[3], -1)} {'умершего ' if fdead else ''}крестьянина {self.full(f, 'g')} "
                   f"{'дочь' if p.fem else 'сын'} {d.name_w}")
        elif "husband" in r.refs:
            h = r.refs["husband"]
            who = f"{self.vil(h.key[3], -1)} крестьянина {self.full(h, 'g')} жена {self.full(d, surname=False)}"
        else:
            st = e["status"]
            title = ("крестьянская вдова" if st == "widowed" else "крестьянская девица" if st == "single"
                     else "крестьянская жена") if p.fem else ("крестьянин" if st != "single" or
                                                               e["age_true"] > 25 else "крестьянский сын")
            who = f"{self.vil(d.key[3], -1)} {title} {self.full(d)}"
        age = self.age_w(r)
        bur = e["buried"]
        pr = next(x for x in PRIESTS if x[0] <= r.year <= x[1])
        v = [
            f"{um.capitalize()} {self.date_phrase(r.d)}, погребен{'а' if p.fem else ''} {bur.day}-го. {who[0].upper() + who[1:]}, "
            f"{age}. Причина смерти: {cause}. Погребение совершал {pr[2]} на приходском кладбище.",
            f"{self.date_phrase(r.d).capitalize()} {um} {who}, {age}, {cause}; погребен{'а' if p.fem else ''} "
            f"{self.date_phrase(bur)} на приходском кладбище.",
            f"{who[0].upper() + who[1:]}, {age}, {um} {cause} {r.d.day} {MONTHS_G[r.d.month - 1]}; "
            f"отпевание и погребение {bur.day}-го числа.",
            f"Скончал{'ась' if p.fem else 'ся'} {self.date_phrase(r.d)} {who}. Возраст: {age}. "
            f"Отчего {um}: {cause}.",
        ]
        tails = [f" Отпевание совершено в приходской церкви {pr[4]} с {pr[3]}.",
                 " Могила на кладбище при Покровской церкви.",
                 f" Запись сделал {pr[2]}."]
        if not e["child"]:
            tails.append(f" Перед кончиною {'исповедана и приобщена' if p.fem else 'исповедан и приобщён'} "
                         f"Святых Таин {pr[4]}.")
        return R.choice(v) + R.choice(tails)

    def marriage(self, r: Record) -> str:
        R = self.R
        g, b = r.refs["groom"], r.refs["bride"]
        e = r.extra
        gv = self.vil(g.key[3], -1)
        groom = f"{gv} крестьянин {self.full(g)}, {ordinal_no(e['g_no'])} браком, {age_gen(e['g_age'])}"
        bv = VILLAGES[e["b_village"]][1]
        if e["maiden"] and e["via_father"]:
            bf = r.refs["bride_father"]
            fdead = self.P[bf.pid].died is not None and self.P[bf.pid].died < r.d
            bride = (f"{bv} {'умершего ' if fdead else ''}крестьянина {self.full(bf, 'g')} дочь девица "
                     f"{b.name_w}, {age_gen(e['b_age'])}, первым браком")
        elif e["maiden"]:
            bride = f"{bv} крестьянская девица {self.full(b)}, {age_gen(e['b_age'])}, первым браком"
        else:
            bride = (f"{bv} крестьянская вдова {self.full(b)}, {age_gen(e['b_age'])}, {ordinal_no(e['b_no'])} "
                     f"браком")
        s1, s2 = r.refs["surety1"], r.refs["surety2"]
        pr = next(x for x in PRIESTS if x[0] <= r.year <= x[1])
        sur = f"по женихе — {self.vil(s1.key[3], -1)} крестьянин {self.full(s1)}; по невесте — " \
              f"{self.vil(s2.key[3], -1)} крестьянин {self.full(s2)}"
        v = [
            f"Венчаны {self.date_phrase(r.d)}. Жених: {groom}. Невеста: {bride}. Оба православного "
            f"вероисповедания. Поручители: {sur}. Таинство совершал {pr[2]}.",
            f"{self.date_phrase(r.d).capitalize()} повенчан {groom}, с невестою: {bride}. Поручители: {sur}.",
            f"Жених — {groom}; невеста — {bride}. Бракосочетание совершено {self.date_phrase(r.d)}. "
            f"Поручителями были: {sur}.",
            f"Сочетались браком {self.date_phrase(r.d)}: {groom}, и {bride}. Оглашения были в три "
            f"воскресных дня; препятствий к браку не открылось. Поручители: {sur}.",
        ]
        tail = R.choice([
            f" Обыск о брачующихся записан в обыскной книге под № {r.no}.",
            " Жених и невеста оба православного вероисповедания, живут в пределах прихода.",
            f" Венчание совершено в приходской Покровской церкви; оглашения были в три воскресных дня. "
            f"Обыск в обыскной книге под № {r.no}.",
        ])
        return R.choice(v) + tail

    def correction(self, rec: Record, role: str) -> str:
        ref = rec.refs[role]
        true, wrong = self.P[ref.pid], self.P[ref.wrong]
        part = {"birth": "первой (о родившихся)", "marriage": "второй (о бракосочетавшихся)",
                "burial": "третьей (об умерших)"}[rec.kind]
        tn = f"{true.name} {M_BY[true.patr][3]}{'а' if true.fem else ''} {adj_form(self.S.surname_at(true, rec.d), true.fem, 'n')}"
        wn = f"{ref.name_w} {adj_form(ref.patr_w, wrong.fem, 'n')} {adj_form(ref.surname_w or wrong.surname, wrong.fem, 'n')}"
        what = {"father": "отцом младенца", "godfather": "восприемником", "deceased": "умершим" if not true.fem
                else "умершей"}[role]
        v = [
            f"В записи № {rec.no} части {part} за {rec.year} год {what} ошибочно записан{'а' if wrong.fem else ''} "
            f"{wn}; по справке надлежит читать: {VILLAGES[self.S.village_at(true, rec.d)][1]} "
            f"{'крестьянка' if true.fem else 'крестьянин'} {tn}.",
            f"По указу духовной консистории исправлена запись № {rec.no} части {part} за {rec.year} год: "
            f"вместо «{wn}» следует читать «{tn}» ({VILLAGES[self.S.village_at(true, rec.d)][1]}).",
        ]
        return self.R.choice(v)

    def books(self) -> dict[str, str]:
        out = {}
        corr_by_year: dict[int, list] = {}
        for rec, role, y in self.B.corrections:
            corr_by_year.setdefault(y, []).append((rec, role))
        for y in range(Y0, Y1 + 1):
            pr = next(x for x in PRIESTS if x[0] <= y <= x[1])
            parts = [f"# Метрическая книга Покровской церкви села Покровского на {y} год\n",
                     f"Приход: село Покровское, деревни Сосновка, Заречье и Малые Ключи. Книгу вёл {pr[2]}.\n"]
            for kind, title in (("birth", "Часть первая. О родившихся"),
                                ("marriage", "Часть вторая. О бракосочетавшихся"),
                                ("burial", "Часть третья. Об умерших")):
                parts.append(f"\n## {title}\n")
                recs = [r for r in self.B.recs if r.year == y and r.kind == kind]
                if not recs:
                    parts.append("Записей нет.\n")
                for r in recs:
                    parts.append(f"**№ {r.no}.** {getattr(self, kind)(r)}\n")
            if y in corr_by_year:
                parts.append("\n## Исправления в записях прежних лет\n")
                for rec, role in corr_by_year[y]:
                    parts.append(f"- {self.correction(rec, role)}\n")
            text = "\n".join(parts)
            if y <= OLD_ORTHO_UNTIL:
                text = old_ortho(text)
            out[f"records/{y}.md"] = text
        return out


GUIDE = """# Руководство по разбору метрических книг Покровского прихода

## Что лежит в каталоге

- `records/1860.md … records/1915.md` — метрические книги Покровской церкви села Покровского, по одной
  на год. Каждая книга состоит из трёх частей: о родившихся, о бракосочетавшихся, об умерших. Записи в
  каждой части пронумерованы с начала года. В конце книги может быть раздел «Исправления в записях
  прежних лет».
- `name_variants.md` — таблица вариантов имён и отчеств.
- `questions.md` — вопросы и формат ответов.

## Приход

В приход входят четыре селения: **село Покровское**, **деревня Сосновка**, **деревня Заречье** и
**деревня Малые Ключи**. В записях селение обычно указано в родительном падеже («деревни Сосновки
крестьянин…», «деревни Малых Ключей…»). Слова «того же села» / «той же деревни» означают то же
селение, что и у главного лица записи: у родителей младенца (часть первая), у жениха (часть вторая),
у умершего или его отца/мужа (часть третья).

## Орфография

Книги до 1890 года включительно писаны по старой орфографии: твёрдый знак на конце слов после
согласной («крестьянинъ», «Иванъ»), «і» перед гласной («Марія», «Василій»). При сравнении имён эти
особенности не учитываются: «Иванъ» = «Иван», «Марія» = «Мария». Буквы «е» и «ё» не различаются.

## Как записаны люди

- Крестьяне записаны в форме **Имя Отчество Фамилия**, где отчество — в старой краткой форме:
  «Петр Иванов Лапин» — Петр, сын Ивана, фамилия Лапин; «Прасковья Васильева» — Прасковья, дочь
  Василия. Отчество «Иванов» и фамилия «Иванов» различаются только положением в записи.
- Имена пишутся то в церковной, то в бытовой форме (Иоанн/Иван, Параскева/Прасковья, Евдокия/Авдотья
  и т. д.), отчества — от любой формы имени отца («Иоаннов» = «Иванов», «Георгиев» = «Егоров»).
  Все допустимые соответствия перечислены в `name_variants.md`; других вариантов нет.
- Младенец в записи о рождении назван только по имени; его отчество — имя отца, фамилия — фамилия
  отца, селение — селение отца.
- Мать младенца записана как «законная жена его Имя Отчество», без фамилии. Это жена отца на дату
  рождения (если отец был женат несколько раз — та жена, с которой он состоял в браке на эту дату).
- Женщина носит фамилию отца до замужества и фамилию мужа после венчания; вдова сохраняет фамилию
  умершего мужа, а при новом браке принимает фамилию нового мужа. После венчания женщина числится в
  селении мужа.
- Замужняя женщина может быть записана без фамилии: «крестьянина Петра Иванова Лапина жена Анна
  Семенова» — тогда её фамилия — фамилия мужа.
- Невеста-девица может быть записана через отца: «деревни Сосновки крестьянина Василия Петрова
  Лапина дочь девица Прасковья» — отчество невесты «Васильева», фамилия «Лапина», селение — Сосновка.
  Селение, указанное перед невестой, — её селение до брака.
- Умерший ребёнок записан как «крестьянина … сын (дочь) Имя»; жена — через мужа; вдова, девица и
  мужчина — полным именем.

## Возраст

В записях о браке и погребении взрослых возраст указан со слов и может отличаться от истинного
не более чем на 2 года в любую сторону. Возраст умерших детей (до 15 лет) указан точно — в днях,
месяцах или полных годах. Год рождения человека, у которого есть запись о рождении, известен точно.

## Правила отождествления

1. Два упоминания относятся к одному человеку, если совпадают: имя (с учётом вариантов), отчество
   (с учётом вариантов), фамилия на момент записи (с учётом смены фамилии при браке) и селение на
   момент записи, — и при этом возраст/годы жизни согласуются (с допуском ±2 года для указанного в
   записи возраста взрослого).
2. Одинаковые имя, отчество и фамилия в разных селениях — это разные люди.
3. В одном селении в каждый момент не живёт двух человек с одинаковыми именем, отчеством и фамилией.
   Но тёзки в одном селении встречаются в разное время: чаще всего это ребёнок, названный в честь
   умершего брата или сестры. После записи о погребении человека все последующие упоминания с тем же
   именем, отчеством, фамилией и селением относятся к другому человеку; выбирайте того, кто жив на
   дату записи и чей возраст согласуется с указанным.
4. Упомянутый в записи отец (в том числе «умершего крестьянина…») должен был быть жив к моменту
   зачатия ребёнка и старше его не менее чем на 18 лет.
5. Восприемником (крёстным отцом, крёстной матерью) может быть только человек, живой на дату записи;
   восприемнику-мужчине — не меньше 17 лет, восприемнице — не меньше 14.
6. Порядковый номер брака жениха и невесты («первым браком», «вторым браком») указан в записи о
   браке. «Первая жена» — невеста из записи о браке, где мужчина венчался первым браком; «второй брак»
   женщины — запись, где она венчалась вторым браком.

## Исправления

Раздел «Исправления в записях прежних лет» в книге более позднего года исправляет запись прежнего
года (указаны номер записи, часть и год). Исправление имеет приоритет над исходной записью: ошибочно
вписанный человек к этой записи не относится.

## Пределы

Учитываются только записи этих книг (1860–1915). События до 1860 года и вне прихода в книгах не
отражены и в ответах не учитываются.
"""


def variants_md() -> str:
    lines = ["# Варианты имён и отчеств", "",
             "Слева — бытовая форма (в ответах используйте её или любую форму из той же строки).", "",
             "## Мужские имена", "", "| Бытовая форма | Церковная и прочие формы | Отчество (краткая форма) |",
             "|---|---|---|"]
    for m in MALE:
        others = ", ".join(x for x in dict.fromkeys((m[1], *m[2])) if x != m[0]) or "—"
        pat = ", ".join(dict.fromkeys((m[3], m[4])))
        lines.append(f"| {m[0]} | {others} | {pat} (жен. {', '.join(x + 'а' for x in dict.fromkeys((m[3], m[4])))}) |")
    lines += ["", "## Женские имена", "", "| Бытовая форма | Церковная и прочие формы |", "|---|---|"]
    for f in FEMALE:
        others = ", ".join(x for x in dict.fromkeys((f[1], *f[2])) if x != f[0]) or "—"
        lines.append(f"| {f[0]} | {others} |")
    lines += ["", "Похожие имена из разных строк — разные имена (например, Евдокия и Евфимия, Агафья и Акулина).", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Вопросы
# --------------------------------------------------------------------------


def fio(p: Person) -> str:
    return f"{p.name} {M_BY[p.patr][3]}{'а' if p.fem else ''} {adj_form(p.surname, p.fem, 'n')}"


def describe(p: Person) -> str:
    if p.birth_rec is None:
        return f"{fio(p)} (умер {VILLAGES[p.village][2]} в {p.died.year} году)"
    born = "родилась" if p.fem else "родился"
    return f"{fio(p)} ({born} {VILLAGES[p.village][2]} в {p.born.year} году)"


@dataclass
class Q:
    tpl: str
    text: str
    kind: str  # int | year | surname | fio | village
    fn: object
    subj: int

    def ans(self, V: View) -> str:
        try:
            v = self.fn(V)
        except (KeyError, IndexError, TypeError, AttributeError):
            return ""
        return "" if v is None else str(v)


def _second(lst):
    return lst[1] if len(lst) >= 2 else None


def candidates(B: Books) -> list[Q]:
    P = B.P
    out: list[Q] = []
    seen: dict[tuple, int] = {}
    for p in P:
        if p.birth_rec is not None:
            k = (p.name, p.patr, p.surname, p.village, p.born.year)
            seen[k] = seen.get(k, 0) + 1
    subj = [p for p in P if p.birth_rec is not None and seen[(p.name, p.patr, p.surname, p.village, p.born.year)] == 1]
    dseen: dict[tuple, int] = {}
    for p in P:
        if not p.fem and p.died is not None and Y0 <= p.died.year <= Y1:
            k = (p.name, p.patr, p.surname, p.village, p.died.year)
            dseen[k] = dseen.get(k, 0) + 1
    subj += [p for p in P if not p.fem and p.birth_rec is None and p.died is not None and Y0 <= p.died.year <= Y1
             and dseen[(p.name, p.patr, p.surname, p.village, p.died.year)] == 1]

    def name_of(V, pid):
        return None if pid is None else fio(P[pid])

    for p in subj:
        d = describe(p)
        if not p.fem:
            out.append(Q("A", f"{d}. Сколько детей родилось у него по записям о рождении?", "int",
                         lambda V, x=p.pid: len(V.births_f.get(x, [])) or None, p.pid))
            out.append(Q("D", f"{d}. В каком году умерла его первая жена?", "year",
                         lambda V, x=p.pid: _first_wife_death(V, x), p.pid))
            out.append(Q("H", f"{d}. Сколько его детей умерли, не дожив до пяти лет?", "int",
                         lambda V, x=p.pid: sum(
                             1 for c in V.children(x) if c in V.burial_of and c in V.birth_of
                             and _under5(V.birth_of[c][0].d, V.burial_of[c][0].d)), p.pid))
            out.append(Q("L", f"{d}. Сколько раз он был восприемником (крёстным отцом) по записям о рождении?",
                         "int", lambda V, x=p.pid: len(V.god.get(x, [])) or None, p.pid))
        out.append(Q("E", f"{d}. Сколько у {'неё' if p.fem else 'него'} внуков и внучек по записям о рождении "
                     f"(детей {'её' if p.fem else 'его'} сыновей и дочерей)?", "int",
                     lambda V, x=p.pid: sum(len(V.children(c)) for c in V.children(x)) or None, p.pid))
        out.append(Q("B", f"{d}. Какова девичья фамилия {'её' if p.fem else 'его'} бабушки по отцу?", "surname",
                     lambda V, x=p.pid: _gm_maiden(V, x), p.pid))
        out.append(Q("M", f"{d}. Из какого селения была {'её' if p.fem else 'его'} мать до замужества?", "village",
                     lambda V, x=p.pid: _mother_village(V, x), p.pid))
        out.append(Q("O", f"{d}. Кто был {'её' if p.fem else 'его'} крёстным отцом (восприемником)?", "fio",
                     lambda V, x=p.pid: name_of(V, V.birth_of[x][1]["godfather"]), p.pid))
        if p.birth_rec is not None:
            out.append(Q("K", f"{d}. В каком году {'она умерла' if p.fem else 'он умер'}?", "year",
                         lambda V, x=p.pid: V.burial_of[x][0].year, p.pid))
        out.append(Q("P", f"{d}. Сколько у {'неё' if p.fem else 'него'} родных братьев и сестёр (от тех же отца "
                     f"и матери) по записям о рождении?", "int", lambda V, x=p.pid: _siblings(V, x), p.pid))
        if p.fem:
            out.append(Q("I", f"{d}. Под какой фамилией она вступила во второй брак (фамилия в записи о "
                         f"втором браке до венчания)?", "surname",
                         lambda V, x=p.pid: canon_surname(_second(V.husbands(x))[2].refs["bride"].surname_w), p.pid))
            out.append(Q("J", f"{d}. Кто был её мужем во втором браке?", "fio",
                         lambda V, x=p.pid: name_of(V, _second(V.husbands(x))[1]), p.pid))
    for dec in range(1860, 1920, 10):
        def top(V, dec=dec):
            cnt = {}
            for g, rs in V.god.items():
                n = sum(1 for r in rs if dec <= r.year <= dec + 9)
                if n:
                    cnt[g] = n
            if not cnt:
                return None
            best = sorted(cnt.items(), key=lambda x: -x[1])
            if len(best) > 1 and best[1][1] >= best[0][1] - 1:
                return None
            return fio(P[best[0][0]])
        out.append(Q("C", f"Кто чаще всех был восприемником (крёстным отцом) в записях о рождении за "
                     f"{dec}–{min(dec + 9, Y1)} годы? Считайте людей, а не написания имён.", "fio", top, -dec))
    return out


def _first_wife_death(V: View, x: int):
    for _d, b, r in V.wives(x):
        if r.extra["g_no"] == 1:
            return V.burial_of[b][0].year
    return None


def _under5(b: date, d: date) -> bool:
    return (d.year, d.month, d.day) < (b.year + 5, b.month, b.day)


def _gm_maiden(V: View, x: int):
    f = V.birth_of[x][1]["father"]
    gm = V.birth_of[f][1]["mother"]
    hs = V.husbands(gm)
    if not hs:
        return None
    r = hs[0][2]
    if not r.extra["maiden"]:
        return None
    if r.extra["via_father"]:
        return canon_surname(r.refs["bride_father"].surname_w)
    return canon_surname(r.refs["bride"].surname_w)


def _mother_village(V: View, x: int):
    r, res = V.birth_of[x]
    m, f = res["mother"], res["father"]
    for _d, g, rr in V.husbands(m):
        if g == f:
            return VILLAGE_SHORT[rr.extra["b_village"]] if rr.extra["maiden"] else None
    return None


def _siblings(V: View, x: int):
    r, res = V.birth_of[x]
    n = sum(1 for rr, rs in V.births_f.get(res["father"], []) if rs["mother"] == res["mother"]
            and rs["child"] != x)
    return n or None


QUOTAS = {"A": 3, "B": 3, "C": 2, "D": 3, "E": 3, "H": 3, "I": 3, "J": 2, "K": 4, "L": 2, "M": 3, "O": 3, "P": 1}


def select(B: Books, R, views: dict) -> list:
    pool = []
    for qq in candidates(B):
        a = qq.ans(views["true"])
        if not a:
            continue
        diffs = {k: qq.ans(views[k]) != a for k in NAIVE_MODES}
        pool.append((qq, a, diffs))
    R.shuffle(pool)
    chosen, counts, left = [], {k: 0 for k in NAIVE_MODES}, dict(QUOTAS)
    avail: dict[str, int] = {}
    for qq, _, _ in pool:
        avail[qq.tpl] = avail.get(qq.tpl, 0) + 1
    spare = 0
    for t in left:
        if avail.get(t, 0) < left[t]:
            spare += left[t] - avail.get(t, 0)
            left[t] = avail.get(t, 0)
    for t in ("K", "D", "H", "A", "O"):
        add = min(spare, 2)
        left[t] += add
        spare -= add
    used_subj: set = set()
    while any(left.values()):
        best, bs = None, -1.0
        for i, (qq, a, diffs) in enumerate(pool):
            if left.get(qq.tpl, 0) <= 0 or qq.subj in used_subj:
                continue
            sc = sum(max(0, 8 - counts[k]) + 0.2 for k, v in diffs.items() if v) + R.random() * 0.5
            if qq.tpl == "H" and a == "0":
                sc -= 3
            if sc > bs:
                best, bs = i, sc
        if best is None:
            raise RuntimeError("не хватает кандидатов")
        qq, a, diffs = pool.pop(best)
        chosen.append((qq, a, diffs))
        used_subj.add(qq.subj)
        left[qq.tpl] -= 1
        for k, v in diffs.items():
            counts[k] += v
    R.shuffle(chosen)
    return chosen


# --------------------------------------------------------------------------
# Сборка и проверка
# --------------------------------------------------------------------------

QHEAD = """# Вопросы по метрическим книгам Покровского прихода

Отвечайте строго по записям книг `records/` с учётом правил из `guide.md` (варианты имён — в
`name_variants.md`). Человек в вопросе задан именем, отчеством и фамилией при рождении в бытовой форме,
селением и годом рождения — по этим данным найдите его запись о рождении. Все подсчёты — только по
записям книг 1860–1915 годов.

## Формат ответов

Запишите ответы в `answers.json` — JSON-объект `{"Q01": ..., "Q35": ...}`. Тип ответа указан после
вопроса:

- **число** и **год** — целое число;
- **фамилия** — фамилия в мужской форме, как у отца её носительницы («Лапин», а не «Лапина»);
- **ФИО** — «Имя Отчество Фамилия» в именительном падеже, отчество в краткой форме, как в книгах
  («Петр Иванов Лапин»); имя и отчество можно дать в любой форме из `name_variants.md`;
- **селение** — название селения в именительном падеже: Покровское, Сосновка, Заречье или Малые Ключи.

## Вопросы

"""
KIND_LABEL = {"int": "число", "year": "год", "surname": "фамилия", "fio": "ФИО", "village": "селение"}


@functools.cache
def build():
    R = rng(TASK_ID)
    S = Sim(R).run()
    B = Books(S, R).build()
    first: dict = {}
    for r in B.recs:
        for ref in r.refs.values():
            pid = ref.pid if ref.wrong == "none" else ref.wrong
            if pid is None:
                continue
            cur = first.get(ref.key)
            if cur is None or B.P[pid].born < B.P[cur].born:
                first[ref.key] = pid
    views = {"true": View(B, TRUE, first)}
    for k, m in NAIVE_MODES.items():
        views[k] = View(B, m, first)
    chosen = select(B, R, views)
    files = Render(B, R).books()
    files["guide.md"] = GUIDE
    files["name_variants.md"] = variants_md()
    lines, answers, kinds = [], {}, {}
    naive = {k: {} for k in NAIVE_MODES}
    for i, (qq, a, _) in enumerate(chosen, 1):
        qid = f"Q{i:02d}"
        lines.append(f"**{qid}.** {qq.text} *(ответ: {KIND_LABEL[qq.kind]})*\n")
        answers[qid], kinds[qid] = a, qq.kind
        for k in NAIVE_MODES:
            naive[k][qid] = qq.ans(views[k])
    files["questions.md"] = QHEAD + "\n".join(lines)
    return {"files": files, "answers": answers, "kinds": kinds, "naive": naive, "books": B, "chosen": chosen}


def _canon(kind: str, v):
    if kind in ("int", "year"):
        x = num(v)
        return None if x is None else round(x)
    s = norm(v).replace("«", "").replace("»", "")
    if kind == "surname":
        return canon_surname(s)
    if kind == "village":
        s = re.sub(r"^(село|деревня|с\.|д\.)\s+", "", s)
        return s
    toks = s.split()
    if len(toks) != 3:
        return None
    return (canon_name(toks[0]), canon_patr(toks[1]), canon_surname(toks[2]))


def setup(ws: Path) -> None:
    for rel, text in build()["files"].items():
        write(ws, rel, text)


def gold(ws: Path) -> None:
    write_json(ws, "answers.json", build()["answers"])


def check(ws: Path) -> str:
    b = build()
    got = read_json(ws, "answers.json")
    assert isinstance(got, dict), "answers.json должен содержать JSON-объект"
    ok, errs = 0, []
    for qid, exp in b["answers"].items():
        kind = b["kinds"][qid]
        if qid in got and got[qid] is not None and _canon(kind, got[qid]) == _canon(kind, exp):
            ok += 1
        else:
            errs.append(f"{qid} неверно" if qid in got else f"{qid} нет ответа")
    return require_share(len(b["answers"]), ok, min_share=MIN_CORRECT / N_QUESTIONS, what="ответы", errors=errs)


PROMPT = (
    "В каталоге records/ — метрические книги Покровского прихода за 1860–1915 годы (по книге на год: "
    "рождения и крещения, браки, погребения), написанные в стиле эпохи. В guide.md — описание формата "
    "записей и правила отождествления людей (варианты имён, отчества в старой форме, возраст со слов, "
    "смена фамилии при замужестве и повторном браке, тёзки, исправления), в name_variants.md — таблица "
    "вариантов имён. В questions.md — 35 генеалогических вопросов. Ответь на все вопросы строго по "
    "записям и правилам и запиши ответы в answers.json в корне рабочего каталога — JSON-объект вида "
    '{"Q01": ..., ..., "Q35": ...}; формат каждого ответа указан в questions.md. Записей больше тысячи, '
    "они написаны свободным текстом, более поздние книги могут исправлять записи прежних лет — учитывай "
    "каждую запись. Файлы с книгами не изменяй."
)


def _naive(mode_name: str):
    def damage(ws: Path) -> None:
        write_json(ws, "answers.json", build()["naive"][mode_name])
    damage.__name__ = f"naive_{mode_name}"
    return damage


def year_off_by_one(ws: Path) -> None:
    """Год смерти/рождения по возрасту со слов: на год раньше."""
    b = build()
    out = dict(b["answers"])
    for qid, k in b["kinds"].items():
        if k in ("year", "int"):
            out[qid] = str(int(out[qid]) - 1)
    write_json(ws, "answers.json", out)


NEAR_MISSES = [_naive(k) for k in NAIVE_MODES] + [year_off_by_one]

TASK = long_task(
    id="task_396_parish_genealogy",  # registry id; TASK_ID stays the generator seed
    name="Генеалогия по метрическим книгам прихода",
    prompt=PROMPT,
    setup=setup,
    gold=gold,
    check=check,
    tags=("reading", "genealogy", "entity-resolution"),
)
