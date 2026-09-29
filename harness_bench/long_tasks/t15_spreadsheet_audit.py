"""Long task 15: audit of formulas in a monthly budget model stored as CSV sheets.

The workspace holds 17 CSV sheets with Excel-style formula texts, a long methodology
(`model_spec.md`) and a stdlib formula evaluator (`tools/calc.py`). About sixty errors
are injected into the correct model (wrong range, $-anchoring, hard-coded number,
wrong sheet/row/month, sign, ignored methodology exceptions — about half of them are
internally consistent rows that only the methodology shows to be wrong). The agent
fixes the CSVs and writes `audit.csv`. The check evaluates the agent's book with the
module's own copy of the evaluator and compares every value to the gold model, plus 60 key cells, and
scores the audit (sheet, cell, code) by precision/recall.

Ground truth comes from the row model below (formulas are rendered from it); errors are
mutations of single cells whose value provably changes in the gold context.
"""

from __future__ import annotations

import csv
import functools
import io
import re
from pathlib import Path

from .common import long_task, precision_recall, read_csv, require_share, rng, write, write_csv

CALC_SRC = r'''#!/usr/bin/env python3
"""Мини-вычислитель формул финансовой модели (только стандартная библиотека).

Книга — каталог с CSV-файлами, один файл = один лист, имя листа = имя файла
без .csv. Строка CSV номер N — строка листа N, столбцы A, B, C, ... Ячейка,
начинающаяся с «=», — формула; число — значение; остальное — текст.

Поддерживается: числа, + - * / ^, унарный минус, сравнения = <> < > <= >=,
скобки, ссылки D7, $D$7, D$7, $D7, межлистовые ссылки Лист!D7, диапазоны
D5:D12 и Лист!D5:O5, функции SUM, MIN, MAX, AVERAGE, ROUND, IF, ABS.
Пустая ячейка равна 0; в SUM/MIN/MAX/AVERAGE пустые и текстовые ячейки
диапазона пропускаются. ROUND округляет половину от нуля.

Использование:
  python3 tools/calc.py                      # проверка книги: формулы, ошибки
  python3 tools/calc.py PnL!D20 Revenue!C70  # значения ячеек
  python3 tools/calc.py --sheet PnL          # значения всего листа (CSV)
  python3 tools/calc.py --model other_dir ...
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path


class CellError(str):
    """Ошибка вычисления (#DIV/0!, #REF!, #CYCLE!, #VALUE!, #PARSE!)."""


_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"|(?P<ref>(?:(?P<sheet>[A-Za-z_][A-Za-z0-9_]*)!)?\$?[A-Z]{1,2}\$?[0-9]+)"
    r"|(?P<func>[A-Z]+)\s*\("
    r"|(?P<op><=|>=|<>|[-+*/^(),:=<>]))"
)
_REF = re.compile(r"(?:([A-Za-z_][A-Za-z0-9_]*)!)?\$?([A-Z]{1,2})\$?([0-9]+)$")
FUNCS = {"SUM", "MIN", "MAX", "AVERAGE", "ROUND", "IF", "ABS"}


def col_index(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n


def col_name(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def tokenize(text: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ValueError(f"не разобрать формулу около «{text[pos:pos + 12]}»")
        pos = m.end()
        for kind in ("num", "ref", "func", "op"):
            if m.group(kind) is not None:
                out.append((kind, m.group(kind)))
                break
    return out


class Parser:
    def __init__(self, text: str, sheet: str):
        self.toks = tokenize(text)
        self.i = 0
        self.sheet = sheet

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, value=None):
        tok = self.peek()
        if tok[0] is None or (value is not None and tok[1] != value):
            raise ValueError(f"ожидалось {value or 'выражение'}")
        self.i += 1
        return tok

    def parse(self):
        node = self.compare()
        if self.i != len(self.toks):
            raise ValueError("лишние символы в формуле")
        return node

    def compare(self):
        node = self.additive()
        kind, val = self.peek()
        if kind == "op" and val in ("=", "<>", "<", ">", "<=", ">="):
            self.i += 1
            node = ("bin", val, node, self.additive())
        return node

    def additive(self):
        node = self.term()
        while self.peek()[0] == "op" and self.peek()[1] in "+-":
            op = self.take()[1]
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.power()
        while self.peek()[0] == "op" and self.peek()[1] in "*/":
            op = self.take()[1]
            node = ("bin", op, node, self.power())
        return node

    def power(self):
        node = self.unary()
        while self.peek() == ("op", "^"):
            self.i += 1
            node = ("bin", "^", node, self.unary())
        return node

    def unary(self):
        kind, val = self.peek()
        if kind == "op" and val in "+-":
            self.i += 1
            inner = self.unary()
            return ("neg", inner) if val == "-" else inner
        return self.primary()

    def ref(self, text):
        m = _REF.match(text)
        sheet = m.group(1) or self.sheet
        return sheet, col_index(m.group(2)), int(m.group(3))

    def primary(self):
        kind, val = self.take()
        if kind == "num":
            return ("num", float(val))
        if kind == "ref":
            sheet, c, r = self.ref(val)
            if self.peek() == ("op", ":"):
                self.i += 1
                k2, v2 = self.take()
                if k2 != "ref":
                    raise ValueError("после «:» ожидалась ссылка")
                m2 = _REF.match(v2)
                if m2.group(1) and m2.group(1) != sheet:
                    raise ValueError("диапазон через два листа")
                _, c2, r2 = self.ref(v2)
                return ("range", sheet, min(c, c2), min(r, r2), max(c, c2), max(r, r2))
            return ("ref", sheet, c, r)
        if kind == "func":
            if val not in FUNCS:
                raise ValueError(f"неизвестная функция {val}")
            args = []
            if self.peek() != ("op", ")"):
                args.append(self.compare())
                while self.peek() == ("op", ","):
                    self.i += 1
                    args.append(self.compare())
            self.take(")")
            return ("func", val, args)
        if (kind, val) == ("op", "("):
            node = self.compare()
            self.take(")")
            return node
        raise ValueError(f"неожиданный символ «{val}»")


def deps(node, out):
    kind = node[0]
    if kind == "ref":
        out.append(node[1:])
    elif kind == "range":
        _, sheet, c1, r1, c2, r2 = node
        for c in range(c1, c2 + 1):
            for r in range(r1, r2 + 1):
                out.append((sheet, c, r))
    elif kind == "neg":
        deps(node[1], out)
    elif kind == "bin":
        deps(node[2], out)
        deps(node[3], out)
    elif kind == "func":
        for a in node[2]:
            deps(a, out)
    return out


def excel_round(x: float, n: int) -> float:
    x = float(f"{x:.12g}")
    q = Decimal(1).scaleb(-n)
    return float(Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP))


class Book:
    """Книга: {лист: {(столбец, строка): сырой текст}}."""

    def __init__(self):
        self.cells: dict[str, dict[tuple[int, int], str]] = {}

    @classmethod
    def from_dir(cls, path) -> Book:
        book = cls()
        for f in sorted(Path(path).glob("*.csv")):
            book.add_sheet(f.stem, f.read_text(encoding="utf-8-sig"))
        return book

    def add_sheet(self, name: str, text: str) -> None:
        grid = {}
        for r, row in enumerate(csv.reader(io.StringIO(text)), 1):
            for c, raw in enumerate(row, 1):
                if raw.strip() != "":
                    grid[(c, r)] = raw.strip()
        self.cells[name] = grid

    def evaluate(self) -> dict[tuple[str, int, int], object]:
        asts, values = {}, {}
        for sheet, grid in self.cells.items():
            for (c, r), raw in grid.items():
                key = (sheet, c, r)
                if raw.startswith("="):
                    try:
                        asts[key] = Parser(raw[1:], sheet).parse()
                    except (ValueError, IndexError, AttributeError) as exc:
                        values[key] = CellError(f"#PARSE! {exc}")
                else:
                    try:
                        values[key] = float(raw)
                    except ValueError:
                        values[key] = raw
        dep_map = {k: deps(a, []) for k, a in asts.items()}
        state: dict = {}
        for start in asts:
            if start in state:
                continue
            stack = [(start, iter(dep_map[start]))]
            state[start] = 1
            while stack:
                key, it = stack[-1]
                advanced = False
                for d in it:
                    if d in asts:
                        st = state.get(d)
                        if st is None:
                            state[d] = 1
                            stack.append((d, iter(dep_map[d])))
                            advanced = True
                            break
                        if st == 1:
                            values[d] = CellError("#CYCLE!")
                if not advanced:
                    stack.pop()
                    state[key] = 2
                    if key not in values:
                        values[key] = self._eval(asts[key], values)
        return values

    def _get(self, key, values):
        if key[0] not in self.cells:
            return CellError("#REF!")
        v = values.get(key)
        if v is None:
            return 0.0
        return v

    def _eval(self, node, values):
        try:
            return self._ev(node, values)
        except _Err as exc:
            return CellError(exc.args[0])
        except (ZeroDivisionError, OverflowError, TypeError, ValueError):
            return CellError("#DIV/0!")

    def _num(self, v):
        if isinstance(v, CellError):
            raise _Err(str(v))
        if isinstance(v, str):
            raise _Err("#VALUE!")
        return v

    def _ev(self, node, values):
        kind = node[0]
        if kind == "num":
            return node[1]
        if kind == "ref":
            return self._num(self._get(node[1:], values))
        if kind == "range":
            raise _Err("#VALUE!")
        if kind == "neg":
            return -self._ev(node[1], values)
        if kind == "bin":
            op = node[1]
            a, b = self._ev(node[2], values), self._ev(node[3], values)
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                return a / b
            if op == "^":
                return float(a ** b)
            cmp = {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}
            return 1.0 if cmp[op] else 0.0
        name, args = node[1], node[2]
        if name == "IF":
            if len(args) not in (2, 3):
                raise _Err("#VALUE!")
            if self._ev(args[0], values) != 0:
                return self._ev(args[1], values)
            return self._ev(args[2], values) if len(args) == 3 else 0.0
        if name in ("SUM", "MIN", "MAX", "AVERAGE"):
            nums = []
            for a in args:
                if a[0] == "range":
                    for key in deps(a, []):
                        v = self._get(key, values)
                        if isinstance(v, CellError):
                            raise _Err(str(v))
                        if isinstance(v, float) and key in values:
                            nums.append(v)
                else:
                    nums.append(self._ev(a, values))
            if name == "SUM":
                return float(sum(nums))
            if not nums:
                return 0.0 if name != "AVERAGE" else self._num(CellError("#DIV/0!"))
            if name == "MIN":
                return min(nums)
            if name == "MAX":
                return max(nums)
            return sum(nums) / len(nums)
        if name == "ROUND":
            if len(args) != 2:
                raise _Err("#VALUE!")
            return excel_round(self._ev(args[0], values), int(self._ev(args[1], values)))
        if name == "ABS":
            return abs(self._ev(args[0], values))
        raise _Err("#NAME?")


class _Err(Exception):
    pass


def parse_addr(text: str, default_sheet: str | None = None):
    m = _REF.match(text.strip().replace("$", ""))
    if not m:
        raise ValueError(f"не адрес ячейки: {text}")
    return (m.group(1) or default_sheet, col_index(m.group(2)), int(m.group(3)))


def fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.6f}".rstrip("0").rstrip(".") if v == v else "nan"
    return str(v)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Вычисляет формулы книги из CSV-листов.")
    ap.add_argument("cells", nargs="*", help="адреса вида Лист!D7")
    ap.add_argument("--model", default=None, help="каталог с листами (по умолчанию model/)")
    ap.add_argument("--sheet", help="вывести значения всего листа")
    args = ap.parse_args(argv)
    model = Path(args.model) if args.model else Path(__file__).resolve().parent.parent / "model"
    if not model.is_dir():
        model = Path("model")
    book = Book.from_dir(model)
    if not book.cells:
        print(f"нет листов в {model}", file=sys.stderr)
        return 2
    values = book.evaluate()
    if args.sheet:
        grid = book.cells.get(args.sheet)
        if grid is None:
            print(f"нет листа {args.sheet}", file=sys.stderr)
            return 2
        rows = max(r for _, r in grid)
        cols = max(c for c, _ in grid)
        out = csv.writer(sys.stdout, lineterminator="\n")
        for r in range(1, rows + 1):
            out.writerow([fmt(values.get((args.sheet, c, r), "")) for c in range(1, cols + 1)])
        return 0
    if args.cells:
        for text in args.cells:
            key = parse_addr(text)
            print(f"{text}\t{fmt(values.get(key, 0.0))}")
        return 0
    n_formula = sum(1 for g in book.cells.values() for raw in g.values() if raw.startswith("="))
    errors = [(k, v) for k, v in values.items() if isinstance(v, CellError)]
    print(f"листов: {len(book.cells)}, формул: {n_formula}, ошибок: {len(errors)}")
    for (sheet, c, r), v in sorted(errors)[:50]:
        print(f"  {sheet}!{col_name(c)}{r}: {v}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _calc_ns() -> dict:
    ns: dict = {"__name__": "hb_long15_calc"}
    exec(compile(CALC_SRC, "calc.py", "exec"), ns)  # noqa: S102 — our own evaluator source
    return ns


CALC = _calc_ns()

# --------------------------------------------------------------------------
# The world: a monthly budget model of a bakery for 2027
# --------------------------------------------------------------------------

MONTHS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
MCOL = "DEFGHIJKLMNO"
HEADER = ["Код", "Статья", "Год", *MONTHS]
SHEETS = [
    "Assumptions", "Volume", "Prices", "Revenue", "COGS", "Headcount", "Payroll",
    "Opex", "Energy", "Fleet", "Stores", "Capex", "Depreciation", "WorkingCapital", "Debt",
    "PnL", "CashFlow",
]
PRODUCTS = [
    ("P01", "Батон нарезной"),
    ("P02", "Хлеб «Бородинский»"),
    ("P03", "Багет французский"),
    ("P04", "Круассан сливочный"),
    ("P05", "Хлеб зерновой"),
    ("P06", "Пирог с капустой"),
    ("P07", "Сушки ванильные"),
    ("P08", "Лаваш тонкий"),
    ("P09", "Булочка с маком"),
    ("P10", "Хлеб «Социальный»"),
    ("P11", "Чиабатта"),
    ("P12", "Кекс творожный"),
    ("P13", "Хлеб бездрожжевой"),
    ("P14", "Бублик с кунжутом"),
    ("P15", "Слойка с вишней"),
    ("P16", "Пряник медовый"),
    ("P17", "Хлеб тостовый"),
    ("P18", "Ватрушка с творогом"),
    ("P19", "Бриошь"),
    ("P20", "Штрудель яблочный"),
    ("P21", "Хлеб с отрубями"),
    ("P22", "Сочник с творогом"),
]
DEPTS = [
    ("D01", "Производство"),
    ("D02", "Логистика"),
    ("D03", "Продажи"),
    ("D04", "Маркетинг"),
    ("D05", "ИТ"),
    ("D06", "Финансы"),
    ("D07", "Администрация"),
    ("D08", "Контроль качества"),
    ("D09", "Закупки"),
    ("D10", "Сервис и ремонт"),
    ("D11", "Склад"),
    ("D12", "Охрана труда"),
    ("D13", "Юридический отдел"),
    ("D14", "Управление персоналом"),
]
PROMO_PRODUCTS = ("P03", "P04")
ONLINE_LATE = "P07"  # online channel opens in May
FIXED_PRICE = "P10"  # no July price indexation
FIXED_MAT = "P05"  # material cost fixed all year
MONTHLY_BONUS = "D01"
NO_BONUS = "D07"  # no bonus and no salary indexation
QUARTER_ENDS = (2, 5, 8, 11)

LINES = [
    ("L01", "Туннельная печь №1 (формовой хлеб)"),
    ("L02", "Туннельная печь №2 (батоны)"),
    ("L03", "Ротационная печь сдобного цеха"),
    ("L04", "Линия круассанов"),
    ("L05", "Тестомесильный комплекс"),
    ("L06", "Холодильные камеры готовой продукции"),
    ("L07", "Газовая подовая печь"),
    ("L08", "Газовый пароконвектомат"),
    ("L09", "Линия упаковки"),
    ("L10", "Газовый парогенератор"),
    ("L11", "Камера шоковой заморозки"),
    ("L12", "Линия сушек и баранок"),
]
GAS_LINES = ("L07", "L08", "L10")
L_FIXED = "L02"  # electricity at a fixed contract price all year
L_REPAIR = "L03"  # overhaul in August: 0 hours
L_NEW = "L04"  # installed in May: 0 hours in January-April
L_NIGHT = "L05"  # three shifts (SHIFTS.MAX) from October
L_CAL = ("L06", "L10")  # run on calendar days (DAYS), not working days
L_WINTER = "L11"  # works only in November-December
L_FIXED2 = "L12"  # same fixed-price contract as L02

VANS = [
    ("V01", "ГАЗель Next, маршрут «Север»"),
    ("V02", "ГАЗель Next, маршрут «Юг»"),
    ("V03", "Электрофургон, центр города"),
    ("V04", "Фургон-рефрижератор"),
    ("V05", "ГАЗель Бизнес, маршрут «Запад»"),
    ("V06", "Фургон, маршрут «Восток»"),
    ("V07", "Грузовик междугородних рейсов"),
    ("V08", "Фургон кондитерского цеха"),
    ("V09", "Фургон сетевых поставок"),
    ("V10", "Новый фургон «Соболь»"),
    ("V11", "Пикап хозяйственной службы"),
    ("V12", "Резервный фургон"),
    ("V13", "Микроавтобус для развозки смен"),
    ("V14", "Фургон, маршрут «Юг-2»"),
    ("V15", "Второй электрофургон"),
    ("V16", "Сезонный фургон"),
    ("V17", "Фургон на метане"),
    ("V18", "Легковой автомобиль дирекции"),
    ("V19", "Фургон хлебных киосков"),
    ("V20", "Мусоровоз хозяйственной службы"),
]
V_ELECTRIC = ("V03", "V15")  # energy at the tariff of line L01, not fuel
V_METHANE = "V17"  # fuel at the gas tariff of line L07
V_SHIFTS = "V13"  # mileage x WDAYS / DAYS instead of seasonality
V_SEASON = "V16"  # works May-September only, insurance in May
V_QUARTER_INS = "V18"  # insurance in four equal parts: Jan, Apr, Jul, Oct
V_SERVICE = "V04"  # maintenance by a fixed service contract V04.SVC
V_BOUGHT_OUT = "V05"  # last lease payment in June
V_INTERCITY = ("V07", "V20")  # mileage without seasonality
V_INS_JULY = "V09"  # insurance paid in July
V_NEW = "V10"  # bought in March: no mileage/lease in Jan-Feb, insurance in March
V_RESERVE = "V12"  # mileage x RESERVE.K
OWNED_VANS = ("V07", "V11", "V20")

STORES = [
    ("S01", "Магазин на Озёрной"),
    ("S02", "Киоск в ТЦ «Меридиан»"),
    ("S03", "Магазин у вокзала"),
    ("S04", "Магазин на Садовой"),
    ("S05", "Магазин при заводе"),
    ("S06", "Сезонный павильон в парке"),
    ("S07", "Магазин в Заречье"),
    ("S08", "Мини-пекарня на Рыночной"),
    ("S09", "Магазин на Лесной"),
    ("S10", "Корнер в гипермаркете «Прайм»"),
    ("S11", "Магазин в аэропорту"),
    ("S12", "Магазин на Набережной"),
    ("S13", "Точка на фермерском рынке"),
    ("S14", "Магазин в Северном"),
    ("S15", "Кофейня-пекарня на Театральной"),
    ("S16", "Пункт самовывоза онлайн-заказов"),
    ("S17", "Магазин на Заводской"),
    ("S18", "Киоск у школы"),
]
S_TURNOVER = "S02"  # rent = share of the store's revenue
S_OPENS = "S03"  # opens in April: zeros in January-March
S_FIXED_RENT = "S05"  # long-term lease, no April indexation
S_SEASONAL = "S06"  # closes after September: zeros in October-December
S_PART_TIME = ("S08", "S13")  # sellers part-time: x PART.K
S_TURN_MIN = "S11"  # rent = MAX(minimum S11.RENT, revenue x S11.TURN)
S_OPENS_JULY = "S14"  # opens in July
S_PREMIUM = "S15"  # seller wage x (1 + S15.PREM)
S_ONLINE = "S16"  # revenue = online revenue x share
S_SCHOOL = "S18"  # closed in July-August
S_EURO = "S10"  # rent in euro x EUR of the month, no indexation

ISSUE_CODES = ("RANGE", "ABSREL", "HARDCODE", "SHEET", "SIGN", "PERIOD", "ROW", "LOGIC")


class _Row:
    __slots__ = ("code", "label", "fn", "year", "const", "monthly", "header")

    def __init__(self, code, label, fn=None, year="none", const=None, monthly=None, header=False):
        self.code, self.label, self.fn, self.year = code, label, fn, year
        self.const, self.monthly, self.header = const, monthly, header


class _Model:
    """Row registry + formula rendering helpers (row numbers resolved lazily)."""

    def __init__(self):
        self.sheets: dict[str, list[_Row]] = {s: [] for s in SHEETS}
        self.rows: dict[str, dict[str, int]] = {s: {} for s in SHEETS}

    def add(self, sheet, code, label, **kw):
        rows = self.sheets[sheet]
        rows.append(_Row(code, label, **kw))
        if code:
            assert code not in self.rows[sheet], (sheet, code)
            self.rows[sheet][code] = len(rows) + 1  # row 1 is the header

    def head(self, sheet, label):
        self.sheets[sheet].append(_Row("", label, header=True))

    def r(self, sheet, code) -> int:
        return self.rows[sheet][code]

    # references --------------------------------------------------------
    def A(self, code):  # noqa: N802 — scalar assumption, anchored
        return f"Assumptions!$C${self.r('Assumptions', code)}"

    def AM(self, code, m):  # noqa: N802 — monthly assumption, row anchored
        return f"Assumptions!{MCOL[m]}${self.r('Assumptions', code)}"

    def X(self, sheet, code, m):  # noqa: N802 — cross-sheet, same month
        return f"{sheet}!{MCOL[m]}{self.r(sheet, code)}"


def _fmt_num(v) -> str:
    if isinstance(v, int):
        return str(v)
    if float(v).is_integer():
        return str(int(v))
    return repr(round(float(v), 6))


def _build_world():
    R = rng("long_15_spreadsheet_audit")
    M = _Model()
    A, AM = M.A, M.AM

    def L(sheet):
        return lambda code, m: f"{MCOL[m]}{M.r(sheet, code)}"

    # ---------------- Assumptions ----------------
    S = "Assumptions"
    M.head(S, "Общие допущения")
    seas = [0.92, 0.9, 0.97, 1.0, 1.03, 0.98, 0.95, 0.97, 1.04, 1.06, 1.05, 1.13]
    M.add(S, "SEAS", "Индекс сезонности спроса", monthly=seas, year="avg")
    M.add(S, "DAYS", "Дней в месяце", monthly=[31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], year="sum")
    M.add(S, "IDX.PRICE", "Индексация отпускных цен с июля", const=0.06)
    M.add(S, "PROMO", "Скидка ноябрьской акции «Французская неделя»", const=0.15)
    M.add(S, "ONL.MARK", "Наценка онлайн-канала к прейскуранту", const=0.04)
    M.add(S, "RET.RATE", "Доля возвратов в рознице", const=0.018)
    M.add(S, "IDX.MAT", "Рост цен сырья с апреля", const=0.08)
    M.add(S, "PACK", "Упаковка на единицу продукции, руб.", const=1.85)
    prod = {}
    for code, name in PRODUCTS:
        M.head(S, f"{name} ({code})")
        price = round(R.uniform(38, 420), 2)
        p = {
            "BASE": R.randrange(3000, 21000, 10) * 3,
            "GROWTH": round(R.uniform(0.004, 0.016), 3),
            "RETSH": round(R.uniform(0.34, 0.6), 2),
            "ONLSH": 0 if code == FIXED_PRICE else round(R.uniform(0.05, 0.15), 2),
            "PRICE": price,
            "WHDISC": round(R.uniform(0.12, 0.22), 2),
            "UC": round(price * R.uniform(0.3, 0.46), 2),
        }
        prod[code] = p
        M.add(S, f"{code}.BASE", "Объём тренда в январе, шт.", const=p["BASE"])
        M.add(S, f"{code}.GROWTH", "Месячный рост тренда", const=p["GROWTH"])
        M.add(S, f"{code}.RETSH", "Доля розничного канала", const=p["RETSH"])
        M.add(S, f"{code}.ONLSH", "Доля онлайн-канала", const=p["ONLSH"])
        M.add(S, f"{code}.PRICE", "Прейскурантная цена, руб./шт.", const=p["PRICE"])
        M.add(S, f"{code}.WHDISC", "Оптовая скидка", const=p["WHDISC"])
        M.add(S, f"{code}.UC", "Сырьё на единицу, руб.", const=p["UC"])
    M.head(S, "Персонал")
    M.add(S, "SAL.IDX", "Индексация окладов с октября", const=0.07)
    M.add(S, "BONUS", "Квартальная премия, доля ФОТ квартала", const=0.1)
    M.add(S, "BONUS.PROD", "Ежемесячная премия производства", const=0.05)
    M.add(S, "PAYTAX", "Страховые взносы", const=0.302)
    dept = {}
    for code, name in DEPTS:
        d = {"HC0": R.randrange(4, 60) if code != "D01" else R.randrange(90, 130),
             "SAL": R.randrange(52000, 150000, 500)}
        dept[code] = d
        M.add(S, f"{code}.HC0", f"{name}: численность на 1 января", const=d["HC0"])
        M.add(S, f"{code}.SAL", f"{name}: оклад на человека в месяц, руб.", const=d["SAL"])
    M.head(S, "Операционные расходы")
    M.add(S, "RENT", "Аренда цеха и склада в месяц, руб.", const=1450000)
    M.add(S, "RENT.NEW", "Аренда по новому договору с сентября, руб.", const=1620000)
    util = [round(R.uniform(610000, 880000), -3) for _ in range(12)]
    M.add(S, "UTIL", "Коммунальные платежи, руб.", monthly=util, year="sum")
    M.add(S, "MKT", "Маркетинг, доля чистой выручки", const=0.035)
    M.add(S, "LOGI", "Доставка опта, руб. за единицу", const=4.2)
    M.add(S, "COMM", "Комиссия маркетплейса, доля онлайн-выручки", const=0.12)
    M.add(S, "IT", "Подписки и сопровождение ИТ в месяц, руб.", const=185000)
    M.head(S, "Оборотный капитал")
    M.add(S, "DSO", "Оборачиваемость дебиторки, дней", const=21)
    M.add(S, "DIO", "Оборачиваемость запасов, дней", const=9)
    M.add(S, "DPO", "Оборачиваемость кредиторки по сырью, дней", const=35)
    M.add(S, "AR0", "Дебиторская задолженность на 1 января, руб.", const=31250000)
    M.add(S, "INV0", "Запасы на 1 января, руб.", const=6480000)
    M.add(S, "AP0", "Кредиторская задолженность на 1 января, руб.", const=18900000)
    M.head(S, "Основные средства")
    M.add(S, "FA0", "Первоначальная стоимость ОС на 1 января, руб.", const=486000000)
    M.add(S, "ACC0", "Накопленная амортизация на 1 января, руб.", const=171500000)
    M.add(S, "LIFE.EQ", "Срок службы оборудования, лет", const=8)
    M.add(S, "LIFE.IT", "Срок службы ИТ-техники, лет", const=3)
    capex_eq = [round(R.uniform(900000, 6500000), -4) for _ in range(12)]
    capex_it = [0 if m in (1, 6) else round(R.uniform(150000, 900000), -3) for m in range(12)]
    M.add(S, "CAPEX.EQ", "Закупки оборудования, руб.", monthly=capex_eq, year="sum")
    M.add(S, "CAPEX.IT", "Закупки ИТ-техники, руб.", monthly=capex_it, year="sum")
    M.head(S, "Финансирование и налоги")
    M.add(S, "LOANA0", "Кредит А: остаток на 1 января, руб.", const=120000000)
    M.add(S, "RATE.A", "Кредит А: ставка годовых", const=0.145)
    a_draw = [0, 0, 25000000, 0, 0, 0, 0, 15000000, 0, 0, 0, 0]
    a_rep = [0, 0, 0, 0, 0, 6000000, 6000000, 6000000, 6000000, 6000000, 6000000, 6000000]
    M.add(S, "A.DRAW", "Кредит А: выборка, руб.", monthly=a_draw, year="sum")
    M.add(S, "A.REPAY", "Кредит А: погашение, руб.", monthly=a_rep, year="sum")
    M.add(S, "LOANB0", "Кредитная линия Б: остаток на 1 января, руб.", const=18000000)
    M.add(S, "RATE.B", "Кредитная линия Б: ставка годовых", const=0.172)
    b_draw = [round(R.uniform(2000000, 9000000), -5) for _ in range(12)]
    b_rep = [round(R.uniform(1000000, 9000000), -5) for _ in range(12)]
    for m in range(12):
        if b_draw[m] == b_rep[m]:
            b_rep[m] += 300000
    M.add(S, "B.DRAW", "Кредитная линия Б: выборка, руб.", monthly=b_draw, year="sum")
    M.add(S, "B.REPAY", "Кредитная линия Б: погашение, руб.", monthly=b_rep, year="sum")
    M.add(S, "CASH0", "Денежные средства на 1 января, руб.", const=42600000)
    M.add(S, "TAX", "Ставка налога на прибыль", const=0.25)
    M.add(S, "TAXDUE0", "Налог к уплате на 1 января, руб.", const=7350000)

    # ---------------- Volume ----------------
    S = "Volume"
    lv = L(S)
    for code, name in PRODUCTS:
        M.head(S, f"{name} ({code})")

        def trend(m, c=code):
            if m == 0:
                return f"={A(c + '.BASE')}"
            return f"={MCOL[m - 1]}{M.r('Volume', c + '.TREND')}*(1+{A(c + '.GROWTH')})"

        M.add(S, f"{code}.TREND", "Трендовый объём, шт.", fn=trend)
        M.add(S, f"{code}.UNITS", "Продажи, шт.",
              fn=lambda m, c=code: f"=ROUND({lv(c + '.TREND', m)}*{AM('SEAS', m)},0)", year="sum")
        M.add(S, f"{code}.RET", "в т.ч. розница, шт.",
              fn=lambda m, c=code: f"=ROUND({lv(c + '.UNITS', m)}*{A(c + '.RETSH')},0)", year="sum")

        def onl(m, c=code):
            if c == ONLINE_LATE and m < 4:
                return 0
            return f"=ROUND({lv(c + '.UNITS', m)}*{A(c + '.ONLSH')},0)"

        M.add(S, f"{code}.ONL", "в т.ч. онлайн, шт.", fn=onl, year="sum")
        M.add(S, f"{code}.WH", "в т.ч. опт, шт.", fn=lambda m, c=code: (
            f"={lv(c + '.UNITS', m)}-{lv(c + '.RET', m)}-{lv(c + '.ONL', m)}"), year="sum")
    M.head(S, "Итого по всем продуктам")
    for tot, part, label in (("TOT.UNITS", "UNITS", "Продажи всего, шт."),
                             ("TOT.RET", "RET", "Розница всего, шт."),
                             ("TOT.ONL", "ONL", "Онлайн всего, шт."),
                             ("TOT.WH", "WH", "Опт всего, шт.")):
        M.add(S, tot, label, year="sum", fn=lambda m, p=part: "=" + "+".join(
            lv(f"{c}.{p}", m) for c, _ in PRODUCTS))

    # ---------------- Prices ----------------
    S = "Prices"
    lp = L(S)
    for code, name in PRODUCTS:
        M.head(S, f"{name} ({code})")

        def listp(m, c=code):
            if c == FIXED_PRICE or m < 6:
                return f"={A(c + '.PRICE')}"
            return f"=ROUND({A(c + '.PRICE')}*(1+{A('IDX.PRICE')}),2)"

        M.add(S, f"{code}.LIST", "Прейскурантная цена, руб./шт.", fn=listp)

        def avgp(m, c=code):
            col = "C" if m is None else MCOL[m]
            u = f"Volume!{col}{M.r('Volume', c + '.UNITS')}"
            return f"=IF({u}=0,0,ROUND(Revenue!{col}{M.r('Revenue', c + '.TOT')}/{u},2))"

        M.add(S, f"{code}.AVGP", "Средняя цена реализации, руб./шт.", fn=avgp, year=avgp)

        def retp(m, c=code):
            if c in PROMO_PRODUCTS and m == 10:
                return f"=ROUND({lp(c + '.LIST', m)}*(1-{A('PROMO')}),2)"
            return f"={lp(c + '.LIST', m)}"

        M.add(S, f"{code}.RETP", "Розничная цена, руб./шт.", fn=retp)
        M.add(S, f"{code}.ONLP", "Онлайн-цена, руб./шт.",
              fn=lambda m, c=code: f"=ROUND({lp(c + '.LIST', m)}*(1+{A('ONL.MARK')}),2)")
        M.add(S, f"{code}.WHP", "Оптовая цена, руб./шт.",
              fn=lambda m, c=code: f"=ROUND({lp(c + '.LIST', m)}*(1-{A(c + '.WHDISC')}),2)")

    # ---------------- Revenue ----------------
    S = "Revenue"
    lr = L(S)
    for code, name in PRODUCTS:
        M.head(S, f"{name} ({code})")
        M.add(S, f"{code}.TOT", "Выручка по продукту, руб.", year="sum", fn=lambda m, c=code: (
            f"={lr(c + '.RET', m)}+{lr(c + '.ONL', m)}+{lr(c + '.WH', m)}"))

        def share(m, c=code):
            col = "C" if m is None else MCOL[m]
            g = f"{col}${M.r('Revenue', 'GROSS')}"
            return f"=IF({g}=0,0,ROUND({col}{M.r('Revenue', c + '.TOT')}/{g},4))"

        M.add(S, f"{code}.SHARE", "Доля в валовой выручке", fn=share, year=share)
        for part, pp, label in (("RET", "RETP", "Розничная выручка, руб."),
                                ("ONL", "ONLP", "Онлайн-выручка, руб."),
                                ("WH", "WHP", "Оптовая выручка, руб.")):
            M.add(S, f"{code}.{part}", label, year="sum", fn=lambda m, c=code, a=part, b=pp: (
                f"={M.X('Volume', f'{c}.{a}', m)}*{M.X('Prices', f'{c}.{b}', m)}"))
    M.head(S, "Итого")
    for tot, part, label in (("GROSS.RET", "RET", "Розничная выручка всего, руб."),
                             ("GROSS.ONL", "ONL", "Онлайн-выручка всего, руб."),
                             ("GROSS.WH", "WH", "Оптовая выручка всего, руб.")):
        M.add(S, tot, label, year="sum", fn=lambda m, p=part: "=" + "+".join(
            lr(f"{c}.{p}", m) for c, _ in PRODUCTS))
    M.add(S, "GROSS", "Валовая выручка, руб.", year="sum", fn=lambda m: (
        f"=SUM({lr('GROSS.RET', m)}:{lr('GROSS.WH', m)})"))
    M.add(S, "RETURNS", "Возвраты, руб.", year="sum", fn=lambda m: (
        f"=ROUND({lr('GROSS.RET', m)}*{A('RET.RATE')},2)"))
    M.add(S, "NET", "Чистая выручка, руб.", year="sum", fn=lambda m: (
        f"={lr('GROSS', m)}-{lr('RETURNS', m)}"))

    # ---------------- COGS ----------------
    S = "COGS"
    lc = L(S)
    for code, name in PRODUCTS:
        M.head(S, f"{name} ({code})")

        def uc(m, c=code):
            if c == FIXED_MAT or m < 3:
                return f"={A(c + '.UC')}"
            return f"=ROUND({A(c + '.UC')}*(1+{A('IDX.MAT')}),2)"

        M.add(S, f"{code}.UC", "Сырьё на единицу, руб.", fn=uc)
        M.add(S, f"{code}.MAT", "Сырьё, руб.", year="sum", fn=lambda m, c=code: (
            f"={M.X('Volume', c + '.UNITS', m)}*{lc(c + '.UC', m)}"))
        M.add(S, f"{code}.PACK", "Упаковка, руб.", year="sum", fn=lambda m, c=code: (
            f"=ROUND({M.X('Volume', c + '.UNITS', m)}*{A('PACK')},2)"))
        M.add(S, f"{code}.COST", "Себестоимость продукта, руб.", year="sum", fn=lambda m, c=code: (
            f"={lc(c + '.MAT', m)}+{lc(c + '.PACK', m)}"))

        def ucogs(m, c=code):
            col = "C" if m is None else MCOL[m]
            u = f"Volume!{col}{M.r('Volume', c + '.UNITS')}"
            return f"=IF({u}=0,0,ROUND({col}{M.r('COGS', c + '.COST')}/{u},2))"

        M.add(S, f"{code}.UCOGS", "Себестоимость единицы, руб.", fn=ucogs, year=ucogs)
    M.head(S, "Итого")
    M.add(S, "MAT.TOT", "Сырьё всего, руб.", year="sum", fn=lambda m: "=" + "+".join(
        lc(f"{c}.MAT", m) for c, _ in PRODUCTS))
    M.add(S, "PACK.TOT", "Упаковка всего, руб.", year="sum", fn=lambda m: "=" + "+".join(
        lc(f"{c}.PACK", m) for c, _ in PRODUCTS))
    M.add(S, "COGS.TOT", "Себестоимость продаж, руб.", year="sum", fn=lambda m: (
        f"={lc('MAT.TOT', m)}+{lc('PACK.TOT', m)}"))
    M.add(S, "GP", "Валовая прибыль, руб.", year="sum", fn=lambda m: (
        f"={M.X('Revenue', 'NET', m)}-{lc('COGS.TOT', m)}"))

    def gm(m):
        col = "C" if m is None else MCOL[m]
        net = f"Revenue!{col}{M.r('Revenue', 'NET')}"
        return f"=IF({net}=0,0,ROUND({col}{M.r('COGS', 'GP')}/{net},4))"

    M.add(S, "GM", "Валовая маржа", fn=gm, year=gm)

    # ---------------- Headcount ----------------
    S = "Headcount"
    lh = L(S)
    hires, leaves = {}, {}
    for code, _name in DEPTS:
        big = code == "D01"
        hires[code] = [R.choice([0, 0, 1, 1, 2, 3] if big else [0, 0, 0, 1, 1]) for _ in range(12)]
        leaves[code] = [R.choice([0, 0, 1, 2] if big else [0, 0, 0, 0, 1]) for _ in range(12)]
    # traps below need movements in specific months
    hires["D05"][7] = max(hires["D05"][7], 2)
    leaves["D05"][7] = 0
    leaves["D02"][5] = max(leaves["D02"][5], 1)
    for code, name in DEPTS:
        M.head(S, f"{name} ({code})")

        def hopen(m, c=code):
            if m == 0:
                return f"={A(c + '.HC0')}"
            return f"={MCOL[m - 1]}{M.r('Headcount', c + '.CLOSE')}"

        M.add(S, f"{code}.OPEN", "Численность на начало месяца", fn=hopen, year="first")
        M.add(S, f"{code}.HIRE", "Приём", monthly=hires[code], year="sum")
        M.add(S, f"{code}.LEAVE", "Увольнения", monthly=leaves[code], year="sum")
        M.add(S, f"{code}.CLOSE", "Численность на конец месяца", year="last", fn=lambda m, c=code: (
            f"={lh(c + '.OPEN', m)}+{lh(c + '.HIRE', m)}-{lh(c + '.LEAVE', m)}"))
        M.add(S, f"{code}.AVG", "Среднесписочная численность", year="avg", fn=lambda m, c=code: (
            f"=({lh(c + '.OPEN', m)}+{lh(c + '.CLOSE', m)})/2"))
    M.head(S, "Итого")
    for tot, part, label, yk in (("TOT.OPEN", "OPEN", "Численность на начало, всего", "first"),
                                 ("TOT.CLOSE", "CLOSE", "Численность на конец, всего", "last"),
                                 ("TOT.AVG", "AVG", "Среднесписочная численность, всего", "avg")):
        M.add(S, tot, label, year=yk, fn=lambda m, p=part: "=" + "+".join(
            lh(f"{c}.{p}", m) for c, _ in DEPTS))

    # ---------------- Payroll ----------------
    S = "Payroll"
    lpy = L(S)
    for code, name in DEPTS:
        M.head(S, f"{name} ({code})")

        def rate(m, c=code):
            if c == NO_BONUS or m < 9:
                return f"={A(c + '.SAL')}"
            return f"=ROUND({A(c + '.SAL')}*(1+{A('SAL.IDX')}),0)"

        M.add(S, f"{code}.RATE", "Оклад на человека, руб.", fn=rate)
        M.add(S, f"{code}.SAL", "Оклады, руб.", year="sum", fn=lambda m, c=code: (
            f"=ROUND({M.X('Headcount', c + '.AVG', m)}*{lpy(c + '.RATE', m)},2)"))

        def bonus(m, c=code):
            if c == NO_BONUS:
                return 0
            if c == MONTHLY_BONUS:
                return f"=ROUND({lpy(c + '.SAL', m)}*{A('BONUS.PROD')},2)"
            if m not in QUARTER_ENDS:
                return 0
            return f"=ROUND(SUM({lpy(c + '.SAL', m - 2)}:{lpy(c + '.SAL', m)})*{A('BONUS')},2)"

        M.add(S, f"{code}.BONUS", "Премии, руб.", fn=bonus, year="sum")
        M.add(S, f"{code}.TAX", "Страховые взносы, руб.", year="sum", fn=lambda m, c=code: (
            f"=ROUND(({lpy(c + '.SAL', m)}+{lpy(c + '.BONUS', m)})*{A('PAYTAX')},2)"))
        M.add(S, f"{code}.TOTAL", "Расходы на персонал, руб.", year="sum", fn=lambda m, c=code: (
            f"={lpy(c + '.SAL', m)}+{lpy(c + '.BONUS', m)}+{lpy(c + '.TAX', m)}"))
    M.head(S, "Итого")
    for tot, part, label in (("SAL.TOT", "SAL", "Оклады всего, руб."),
                             ("BONUS.TOT", "BONUS", "Премии всего, руб."),
                             ("TAX.TOT", "TAX", "Страховые взносы всего, руб.")):
        M.add(S, tot, label, year="sum", fn=lambda m, p=part: "=" + "+".join(
            lpy(f"{c}.{p}", m) for c, _ in DEPTS))
    M.add(S, "PAY.TOT", "Расходы на персонал всего, руб.", year="sum", fn=lambda m: (
        f"={lpy('SAL.TOT', m)}+{lpy('BONUS.TOT', m)}+{lpy('TAX.TOT', m)}"))

    # ---------------- Opex ----------------
    S = "Opex"
    lo = L(S)
    M.head(S, "Расходы, кроме персонала")
    M.add(S, "RENT", "Аренда, руб.", year="sum",
          fn=lambda m: f"={A('RENT')}" if m < 8 else f"={A('RENT.NEW')}")
    M.add(S, "UTIL", "Коммунальные платежи, руб.", year="sum", fn=lambda m: f"={AM('UTIL', m)}")
    M.add(S, "MKT", "Маркетинг, руб.", year="sum",
          fn=lambda m: f"=ROUND({M.X('Revenue', 'NET', m)}*{A('MKT')},2)")
    M.add(S, "LOGI", "Доставка опта, руб.", year="sum",
          fn=lambda m: f"=ROUND({M.X('Volume', 'TOT.WH', m)}*{A('LOGI')},2)")
    M.add(S, "COMM", "Комиссия маркетплейса, руб.", year="sum",
          fn=lambda m: f"=ROUND({M.X('Revenue', 'GROSS.ONL', m)}*{A('COMM')},2)")
    M.add(S, "IT", "ИТ-сопровождение, руб.", year="sum", fn=lambda m: f"={A('IT')}")
    M.add(S, "ENERGY", "Энергоресурсы производственных линий, руб.", year="sum",
          fn=lambda m: f"={M.X('Energy', 'COST.TOT', m)}")
    M.add(S, "FLEET", "Собственный автопарк, руб.", year="sum", fn=lambda m: f"={M.X('Fleet', 'TOT', m)}")
    M.add(S, "STORES", "Расходы фирменных магазинов, руб.", year="sum",
          fn=lambda m: f"={M.X('Stores', 'COST.TOT', m)}")
    travel = [round(R.uniform(90000, 260000), -3) for _ in range(12)]
    other = [round(R.uniform(150000, 420000), -3) for _ in range(12)]
    M.add(S, "TRAVEL", "Командировки, руб.", monthly=travel, year="sum")
    M.add(S, "OTHER", "Прочие расходы, руб.", monthly=other, year="sum")
    M.add(S, "EXPAY", "Итого расходы, кроме персонала, руб.", year="sum",
          fn=lambda m: f"=SUM({lo('RENT', m)}:{lo('OTHER', m)})")
    M.head(S, "Расходы на персонал по подразделениям")
    for code, name in DEPTS:
        M.add(S, f"PAY.{code}", f"{name}, руб.", year="sum",
              fn=lambda m, c=code: f"={M.X('Payroll', c + '.TOTAL', m)}")
    M.add(S, "PAY.TOT", "Итого персонал, руб.", year="sum",
          fn=lambda m: f"=SUM({lo('PAY.D01', m)}:{lo('PAY.' + DEPTS[-1][0], m)})")
    M.add(S, "OPEX.TOT", "Операционные расходы всего, руб.", year="sum",
          fn=lambda m: f"={lo('EXPAY', m)}+{lo('PAY.TOT', m)}")

    # ---------------- Capex ----------------
    S = "Capex"
    lx = L(S)
    M.add(S, "EQ", "Оборудование, руб.", year="sum", fn=lambda m: f"={AM('CAPEX.EQ', m)}")
    M.add(S, "IT", "ИТ-техника, руб.", year="sum", fn=lambda m: f"={AM('CAPEX.IT', m)}")
    M.add(S, "TOT", "Капвложения всего, руб.", year="sum", fn=lambda m: f"={lx('EQ', m)}+{lx('IT', m)}")
    for cum, base, label in (("EQ.CUM", "EQ", "Оборудование нарастающим итогом, руб."),
                             ("IT.CUM", "IT", "ИТ-техника нарастающим итогом, руб.")):
        M.add(S, cum, label, year="last", fn=lambda m, cu=cum, b=base: (
            f"={lx(b, m)}" if m == 0 else f"={lx(cu, m - 1)}+{lx(b, m)}"))

    # ---------------- Depreciation ----------------
    S = "Depreciation"
    ld = L(S)
    M.add(S, "EQ.EXIST", "Амортизация ОС на 1 января, руб.", year="sum",
          fn=lambda m: f"=ROUND({A('FA0')}/({A('LIFE.EQ')}*12),2)")
    M.add(S, "EQ.NEW", "Амортизация нового оборудования, руб.", year="sum", fn=lambda m: (
        0 if m == 0 else f"=ROUND({M.X('Capex', 'EQ.CUM', m - 1)}/({A('LIFE.EQ')}*12),2)"))
    M.add(S, "IT.NEW", "Амортизация новой ИТ-техники, руб.", year="sum", fn=lambda m: (
        0 if m == 0 else f"=ROUND({M.X('Capex', 'IT.CUM', m - 1)}/({A('LIFE.IT')}*12),2)"))
    M.add(S, "TOT", "Амортизация всего, руб.", year="sum",
          fn=lambda m: f"=SUM({ld('EQ.EXIST', m)}:{ld('IT.NEW', m)})")
    M.add(S, "GROSS", "Первоначальная стоимость ОС на конец месяца, руб.", year="last", fn=lambda m: (
        f"={A('FA0')}+{M.X('Capex', 'EQ.CUM', m)}+{M.X('Capex', 'IT.CUM', m)}"))
    M.add(S, "ACC", "Накопленная амортизация на конец месяца, руб.", year="last", fn=lambda m: (
        f"={A('ACC0')}+{ld('TOT', m)}" if m == 0 else f"={ld('ACC', m - 1)}+{ld('TOT', m)}"))
    M.add(S, "NET", "Остаточная стоимость ОС, руб.", year="last",
          fn=lambda m: f"={ld('GROSS', m)}-{ld('ACC', m)}")

    # ---------------- WorkingCapital ----------------
    S = "WorkingCapital"
    lw = L(S)
    M.add(S, "AR", "Дебиторская задолженность, руб.", year="last", fn=lambda m: (
        f"=ROUND({M.X('Revenue', 'NET', m)}*{A('DSO')}/{AM('DAYS', m)},2)"))
    M.add(S, "INV", "Запасы, руб.", year="last", fn=lambda m: (
        f"=ROUND({M.X('COGS', 'COGS.TOT', m)}*{A('DIO')}/{AM('DAYS', m)},2)"))
    M.add(S, "AP", "Кредиторская задолженность по сырью, руб.", year="last", fn=lambda m: (
        f"=ROUND({M.X('COGS', 'MAT.TOT', m)}*{A('DPO')}/{AM('DAYS', m)},2)"))
    M.add(S, "NWC", "Оборотный капитал, руб.", year="last",
          fn=lambda m: f"={lw('AR', m)}+{lw('INV', m)}-{lw('AP', m)}")
    M.add(S, "DNWC", "Прирост оборотного капитала, руб.", year="sum", fn=lambda m: (
        f"={lw('NWC', m)}-({A('AR0')}+{A('INV0')}-{A('AP0')})" if m == 0
        else f"={lw('NWC', m)}-{lw('NWC', m - 1)}"))

    # ---------------- Debt ----------------
    S = "Debt"
    lb = L(S)
    for k, name in (("A", "Кредит А"), ("B", "Кредитная линия Б")):
        M.head(S, name)
        M.add(S, f"{k}.OPEN", "Остаток на начало месяца, руб.", year="first", fn=lambda m, k=k: (
            f"={A('LOAN' + k + '0')}" if m == 0 else f"={lb(k + '.CLOSE', m - 1)}"))
        M.add(S, f"{k}.DRAW", "Выборка, руб.", year="sum", fn=lambda m, k=k: f"={AM(k + '.DRAW', m)}")
        M.add(S, f"{k}.REPAY", "Погашение, руб.", year="sum", fn=lambda m, k=k: f"={AM(k + '.REPAY', m)}")
        M.add(S, f"{k}.CLOSE", "Остаток на конец месяца, руб.", year="last", fn=lambda m, k=k: (
            f"={lb(k + '.OPEN', m)}+{lb(k + '.DRAW', m)}-{lb(k + '.REPAY', m)}"))
        if k == "A":
            M.add(S, "A.INT", "Проценты, руб.", year="sum",
                  fn=lambda m: f"=ROUND({lb('A.OPEN', m)}*{A('RATE.A')}/12,2)")
        else:
            M.add(S, "B.INT", "Проценты, руб.", year="sum", fn=lambda m: (
                f"=ROUND(({lb('B.OPEN', m)}+{lb('B.CLOSE', m)})/2*{A('RATE.B')}/12,2)"))
    M.head(S, "Итого")
    M.add(S, "INT.TOT", "Проценты всего, руб.", year="sum",
          fn=lambda m: f"={lb('A.INT', m)}+{lb('B.INT', m)}")
    M.add(S, "DEBT.TOT", "Долг на конец месяца, руб.", year="last",
          fn=lambda m: f"={lb('A.CLOSE', m)}+{lb('B.CLOSE', m)}")

    # ---------------- PnL ----------------
    S = "PnL"
    ln = L(S)

    def ratio(num_code, den_code):
        def f(m):
            col = "C" if m is None else MCOL[m]
            den = f"{col}{M.r('PnL', den_code)}"
            return f"=IF({den}=0,0,ROUND({col}{M.r('PnL', num_code)}/{den},4))"
        return f

    M.add(S, "REV", "Чистая выручка", year="sum", fn=lambda m: f"={M.X('Revenue', 'NET', m)}")
    M.add(S, "COGS", "Себестоимость продаж", year="sum", fn=lambda m: f"={M.X('COGS', 'COGS.TOT', m)}")
    M.add(S, "GP", "Валовая прибыль", year="sum", fn=lambda m: f"={ln('REV', m)}-{ln('COGS', m)}")
    M.add(S, "GM", "Валовая маржа", fn=ratio("GP", "REV"), year=ratio("GP", "REV"))
    M.add(S, "PAY", "Расходы на персонал", year="sum", fn=lambda m: f"={M.X('Opex', 'PAY.TOT', m)}")
    M.add(S, "OTH", "Прочие операционные расходы", year="sum", fn=lambda m: f"={M.X('Opex', 'EXPAY', m)}")
    M.add(S, "EBITDA", "EBITDA", year="sum",
          fn=lambda m: f"={ln('GP', m)}-{ln('PAY', m)}-{ln('OTH', m)}")
    M.add(S, "EBITDAM", "Маржа EBITDA", fn=ratio("EBITDA", "REV"), year=ratio("EBITDA", "REV"))
    M.add(S, "DEP", "Амортизация", year="sum", fn=lambda m: f"={M.X('Depreciation', 'TOT', m)}")
    M.add(S, "EBIT", "Операционная прибыль (EBIT)", year="sum",
          fn=lambda m: f"={ln('EBITDA', m)}-{ln('DEP', m)}")
    M.add(S, "INT", "Проценты к уплате", year="sum", fn=lambda m: f"={M.X('Debt', 'INT.TOT', m)}")
    M.add(S, "EBT", "Прибыль до налога", year="sum", fn=lambda m: f"={ln('EBIT', m)}-{ln('INT', m)}")
    M.add(S, "TAX", "Налог на прибыль", year="sum",
          fn=lambda m: f"=ROUND(MAX(0,{ln('EBT', m)})*{A('TAX')},2)")
    M.add(S, "NI", "Чистая прибыль", year="sum", fn=lambda m: f"={ln('EBT', m)}-{ln('TAX', m)}")
    M.add(S, "NIM", "Чистая маржа", fn=ratio("NI", "REV"), year=ratio("NI", "REV"))

    # ---------------- CashFlow ----------------
    S = "CashFlow"
    lf = L(S)
    def taxpaid(m):
        t = M.r("PnL", "TAX")
        if m == 0:
            return f"=-{A('TAXDUE0')}"
        if m in (3, 6, 9):
            return f"=-SUM(PnL!{MCOL[m - 3]}{t}:{MCOL[m - 1]}{t})"
        return 0

    M.head(S, "Операционная деятельность")
    M.add(S, "NI", "Чистая прибыль", year="sum", fn=lambda m: f"={M.X('PnL', 'NI', m)}")
    M.add(S, "DEP", "Амортизация", year="sum", fn=lambda m: f"={M.X('PnL', 'DEP', m)}")
    M.add(S, "DNWC", "Изменение оборотного капитала", year="sum",
          fn=lambda m: f"=-{M.X('WorkingCapital', 'DNWC', m)}")
    M.add(S, "TAXACR", "Начисленный налог на прибыль", year="sum", fn=lambda m: f"={M.X('PnL', 'TAX', m)}")
    M.add(S, "TAXPAID", "Уплаченный налог на прибыль", year="sum", fn=taxpaid)
    M.add(S, "CFO", "Поток от операционной деятельности", year="sum",
          fn=lambda m: f"=SUM({lf('NI', m)}:{lf('TAXPAID', m)})")
    M.head(S, "Инвестиционная деятельность")
    M.add(S, "CAPEX", "Капвложения", year="sum", fn=lambda m: f"=-{M.X('Capex', 'TOT', m)}")
    M.add(S, "CFI", "Поток от инвестиционной деятельности", year="sum", fn=lambda m: f"={lf('CAPEX', m)}")
    M.head(S, "Финансовая деятельность")
    M.add(S, "DRAW", "Получение кредитов", year="sum", fn=lambda m: (
        f"={M.X('Debt', 'A.DRAW', m)}+{M.X('Debt', 'B.DRAW', m)}"))
    M.add(S, "REPAY", "Погашение кредитов", year="sum", fn=lambda m: (
        f"=-({M.X('Debt', 'A.REPAY', m)}+{M.X('Debt', 'B.REPAY', m)})"))
    M.add(S, "CFF", "Поток от финансовой деятельности", year="sum",
          fn=lambda m: f"={lf('DRAW', m)}+{lf('REPAY', m)}")
    M.head(S, "Денежные средства")
    M.add(S, "NET", "Чистый денежный поток", year="sum",
          fn=lambda m: f"={lf('CFO', m)}+{lf('CFI', m)}+{lf('CFF', m)}")
    M.add(S, "OPEN", "Денежные средства на начало месяца", year="first", fn=lambda m: (
        f"={A('CASH0')}" if m == 0 else f"={lf('CLOSE', m - 1)}"))
    M.add(S, "CLOSE", "Денежные средства на конец месяца", year="last",
          fn=lambda m: f"={lf('OPEN', m)}+{lf('NET', m)}")
    M.add(S, "TAXBAL", "Налог на прибыль к уплате на конец месяца", year="last", fn=lambda m: (
        f"={A('TAXDUE0')}+{lf('TAXACR', m)}+{lf('TAXPAID', m)}" if m == 0
        else f"={lf('TAXBAL', m - 1)}+{lf('TAXACR', m)}+{lf('TAXPAID', m)}"))
    _build_energy(M, R)
    _build_fleet(M, R)
    _build_stores(M, R)
    return M


def _build_energy(M: _Model, R) -> None:
    A, AM = M.A, M.AM
    S = "Assumptions"
    M.head(S, "Энергоресурсы")
    wdays = [17, 19, 21, 22, 19, 21, 23, 21, 22, 22, 20, 22]
    M.add(S, "WDAYS", "Рабочих дней в месяце", monthly=wdays, year="sum")
    M.add(S, "HOURS.SHIFT", "Часов в смене", const=8)
    M.add(S, "SHIFTS.MAX", "Смен в сутки при круглосуточной работе", const=3)
    M.add(S, "ELEC.T", "Тариф на электроэнергию, руб./кВт·ч", const=7.84)
    M.add(S, "ELEC.IDX", "Индексация тарифа на электроэнергию с июля", const=0.098)
    M.add(S, "GAS.T", "Тариф на газ, руб./м³", const=9.36)
    M.add(S, "GAS.IDX", "Индексация тарифа на газ с июля", const=0.104)
    for code, name in LINES:
        gas = code in GAS_LINES
        M.add(S, f"{code}.SHIFTS", f"{name}: смен в сутки", const=R.choice([1, 2, 2]))
        power = round(R.uniform(3.2, 9.5), 1) if gas else R.randrange(18, 140)
        M.add(S, f"{code}.POWER", f"{name}: " + ("расход газа, м³/ч" if gas else "мощность, кВт"),
              const=power)
    S = "Energy"
    le = L(M, S)
    for code, name in LINES:
        M.head(S, f"{name} ({code})")
        gas = code in GAS_LINES

        def hours(m, c=code):
            if (c == L_REPAIR and m == 7) or (c == L_NEW and m < 4) or (c == L_WINTER and m < 10):
                return 0
            days = AM("DAYS" if c in L_CAL else "WDAYS", m)
            shifts = A("SHIFTS.MAX") if c == L_NIGHT and m >= 9 else A(c + ".SHIFTS")
            return f"={days}*{shifts}*{A('HOURS.SHIFT')}"

        M.add(S, f"{code}.HOURS", "Машино-часы работы", fn=hours, year="sum")
        M.add(S, f"{code}.CONS", "Потребление, " + ("м³" if gas else "кВт·ч"), year="sum",
              fn=lambda m, c=code: f"={le(c + '.HOURS', m)}*{A(c + '.POWER')}")

        def tariff(m, c=code, gas=gas):
            t, idx = ("GAS.T", "GAS.IDX") if gas else ("ELEC.T", "ELEC.IDX")
            if m < 6 or c in (L_FIXED, L_FIXED2):
                return f"={A(t)}"
            return f"=ROUND({A(t)}*(1+{A(idx)}),2)"

        M.add(S, f"{code}.TARIFF", "Тариф, руб. за единицу", fn=tariff)
        M.add(S, f"{code}.COST", "Стоимость энергоресурсов, руб.", year="sum",
              fn=lambda m, c=code: f"=ROUND({le(c + '.CONS', m)}*{le(c + '.TARIFF', m)},2)")
    M.head(S, "Итого")
    elec = [c for c, _ in LINES if c not in GAS_LINES]
    M.add(S, "KWH.TOT", "Электроэнергия всего, кВт·ч", year="sum",
          fn=lambda m: "=" + "+".join(le(f"{c}.CONS", m) for c in elec))
    M.add(S, "M3.TOT", "Газ всего, м³", year="sum",
          fn=lambda m: "=" + "+".join(le(f"{c}.CONS", m) for c in GAS_LINES))
    M.add(S, "COST.TOT", "Энергоресурсы всего, руб.", year="sum",
          fn=lambda m: "=" + "+".join(le(f"{c}.COST", m) for c, _ in LINES))


def _build_fleet(M: _Model, R) -> None:
    A, AM = M.A, M.AM
    S = "Assumptions"
    M.head(S, "Автопарк")
    M.add(S, "FUEL.P", "Цена дизельного топлива, руб./л", const=68.4)
    M.add(S, "FUEL.IDX", "Рост цены топлива с мая", const=0.065)
    M.add(S, "MAINT.KM", "Обслуживание и ремонт, руб. за км", const=4.35)
    M.add(S, "RESERVE.K", "Коэффициент пробега резервной машины", const=0.5)
    for code, name in VANS:
        M.add(S, f"{code}.KM", f"{name}: базовый пробег в месяц, км", const=R.randrange(2400, 7800, 50))
        elec = code in V_ELECTRIC
        cons = round(R.uniform(19, 32), 1) if elec else round(R.uniform(10.5, 17.5), 1)
        unit = "кВт·ч" if elec else "м³" if code == V_METHANE else "л"
        M.add(S, f"{code}.CONS", f"{name}: расход на 100 км, {unit}", const=cons)
        lease = 0 if code in OWNED_VANS else R.randrange(38000, 96000, 500)
        M.add(S, f"{code}.LEASE", f"{name}: лизинговый платёж в месяц, руб.", const=lease)
        M.add(S, f"{code}.INS", f"{name}: страховка на год, руб.", const=R.randrange(42000, 118000, 100))
    M.add(S, "V04.SVC", "Фургон-рефрижератор: сервисный контракт в месяц, руб.", const=31500)
    S = "Fleet"
    lf = L(M, S)
    M.head(S, "Общие строки")
    M.add(S, "FUEL.PRICE", "Цена топлива, руб./л", fn=lambda m: (
        f"={A('FUEL.P')}" if m < 4 else f"=ROUND({A('FUEL.P')}*(1+{A('FUEL.IDX')}),2)"))
    for code, name in VANS:
        M.head(S, f"{name} ({code})")

        def km(m, c=code):
            if (c == V_NEW and m < 2) or (c == V_SEASON and not 4 <= m <= 8):
                return 0
            if c in V_INTERCITY:
                return f"={A(c + '.KM')}"
            if c == V_SHIFTS:
                return f"=ROUND({A(c + '.KM')}*{AM('WDAYS', m)}/{AM('DAYS', m)},0)"
            extra = f"*{A('RESERVE.K')}" if c == V_RESERVE else ""
            return f"=ROUND({A(c + '.KM')}*{AM('SEAS', m)}{extra},0)"

        M.add(S, f"{code}.KM", "Пробег, км", fn=km, year="sum")

        def fuel(m, c=code):
            price = (f"Energy!{MCOL[m]}{M.r('Energy', 'L01.TARIFF')}" if c in V_ELECTRIC
                     else f"Energy!{MCOL[m]}{M.r('Energy', 'L07.TARIFF')}" if c == V_METHANE
                     else lf("FUEL.PRICE", m))
            return f"=ROUND({lf(c + '.KM', m)}*{A(c + '.CONS')}/100*{price},2)"

        M.add(S, f"{code}.FUEL", "Топливо и энергия, руб.", fn=fuel, year="sum")
        M.add(S, f"{code}.MAINT", "Обслуживание и ремонт, руб.", year="sum", fn=lambda m, c=code: (
            f"={A('V04.SVC')}" if c == V_SERVICE else f"=ROUND({lf(c + '.KM', m)}*{A('MAINT.KM')},2)"))

        def lease(m, c=code):
            if (c == V_BOUGHT_OUT and m >= 6) or (c == V_NEW and m < 2) or (c == V_SEASON and not 4 <= m <= 8):
                return 0
            return f"={A(c + '.LEASE')}"

        M.add(S, f"{code}.LEASE", "Лизинг, руб.", fn=lease, year="sum")
        pay = 6 if code == V_INS_JULY else 2 if code == V_NEW else 4 if code == V_SEASON else 0

        def ins(m, c=code, pay=pay):
            if c == V_QUARTER_INS:
                return f"=ROUND({A(c + '.INS')}/4,2)" if m in (0, 3, 6, 9) else 0
            return f"={A(c + '.INS')}" if m == pay else 0

        M.add(S, f"{code}.INS", "Страхование, руб.", fn=ins, year="sum")
        M.add(S, f"{code}.TOTAL", "Расходы на машину, руб.", year="sum",
              fn=lambda m, c=code: f"=SUM({lf(c + '.FUEL', m)}:{lf(c + '.INS', m)})")
    M.head(S, "Итого")
    for tot, part, label in (("KM.TOT", "KM", "Пробег всего, км"),
                             ("FUEL.TOT", "FUEL", "Топливо и энергия всего, руб."),
                             ("TOT", "TOTAL", "Автопарк всего, руб.")):
        M.add(S, tot, label, year="sum", fn=lambda m, p=part: "=" + "+".join(
            lf(f"{c}.{p}", m) for c, _ in VANS))


def _build_stores(M: _Model, R) -> None:
    A, AM = M.A, M.AM
    S = "Assumptions"
    M.head(S, "Фирменные магазины")
    M.add(S, "STORE.WAGE", "Оплата продавца в месяц, руб.", const=58500)
    M.add(S, "STORE.IDX", "Индексация аренды магазинов с апреля", const=0.055)
    M.add(S, "PART.K", "Доля ставки продавцов на полставки", const=0.5)
    M.add(S, "S02.TURN", "Киоск в ТЦ «Меридиан»: аренда, доля выручки киоска", const=0.11)
    M.add(S, "S11.TURN", "Магазин в аэропорту: аренда, доля выручки магазина", const=0.14)
    M.add(S, "S15.PREM", "Кофейня-пекарня: надбавка к оплате продавца-бариста", const=0.2)
    eur = [round(R.uniform(96, 108), 2) for _ in range(12)]
    M.add(S, "EUR", "Курс евро, руб.", monthly=eur, year="avg")
    for code, name in STORES:
        M.add(S, f"{code}.SH", f"{name}: доля розничной выручки", const=round(R.uniform(0.018, 0.064), 3))
        rent = R.randrange(1800, 4200, 50) if code == S_EURO else R.randrange(90000, 420000, 1000)
        label = "аренда в месяц, " + ("евро" if code == S_EURO else "руб.")
        if code == S_TURN_MIN:
            rent, label = 330000, "минимальная аренда в месяц, руб."
        M.add(S, f"{code}.RENT", f"{name}: {label}", const=rent)
        M.add(S, f"{code}.STAFF", f"{name}: продавцов, чел.", const=R.randrange(2, 7))
    S = "Stores"
    ls = L(M, S)
    for code, name in STORES:
        M.head(S, f"{name} ({code})")

        def closed(m, c=code):
            return ((c == S_OPENS and m < 3) or (c == S_SEASONAL and m >= 9) or (c == S_OPENS_JULY and m < 6)
                    or (c == S_SCHOOL and m in (6, 7)))

        M.add(S, f"{code}.REV", "Выручка магазина, руб.", year="sum", fn=lambda m, c=code: 0 if closed(m, c) else (
            f"=ROUND({M.X('Revenue', 'GROSS.ONL' if c == S_ONLINE else 'GROSS.RET', m)}*{A(c + '.SH')},2)"))

        def rent(m, c=code):
            if closed(m, c):
                return 0
            if c == S_TURNOVER:
                return f"=ROUND({ls(c + '.REV', m)}*{A('S02.TURN')},2)"
            if c == S_TURN_MIN:
                return f"=MAX({A(c + '.RENT')},ROUND({ls(c + '.REV', m)}*{A('S11.TURN')},2))"
            if c == S_EURO:
                return f"=ROUND({A(c + '.RENT')}*{AM('EUR', m)},2)"
            if m < 3 or c == S_FIXED_RENT:
                return f"={A(c + '.RENT')}"
            return f"=ROUND({A(c + '.RENT')}*(1+{A('STORE.IDX')}),2)"

        M.add(S, f"{code}.RENT", "Аренда, руб.", fn=rent, year="sum")

        def staff(m, c=code):
            if closed(m, c):
                return 0
            part = f"*{A('PART.K')}" if c in S_PART_TIME else ""
            if c == S_PREMIUM:
                part = f"*(1+{A('S15.PREM')})"
            return f"=ROUND({A(c + '.STAFF')}*{A('STORE.WAGE')}{part}*(1+{A('PAYTAX')}),2)"

        M.add(S, f"{code}.STAFF", "Продавцы с взносами, руб.", fn=staff, year="sum")
        M.add(S, f"{code}.COST", "Расходы магазина, руб.", year="sum",
              fn=lambda m, c=code: f"={ls(c + '.RENT', m)}+{ls(c + '.STAFF', m)}")
        M.add(S, f"{code}.RESULT", "Результат магазина, руб.", year="sum", fn=lambda m, c=code: (
            f"=ROUND({ls(c + '.REV', m)}*{M.X('COGS', 'GM', m)},2)-{ls(c + '.COST', m)}"))
    M.head(S, "Итого")
    for tot, part, label in (("REV.TOT", "REV", "Выручка магазинов всего, руб."),
                             ("COST.TOT", "COST", "Расходы магазинов всего, руб."),
                             ("RESULT.TOT", "RESULT", "Результат магазинов всего, руб.")):
        M.add(S, tot, label, year="sum", fn=lambda m, p=part: "=" + "+".join(
            ls(f"{c}.{p}", m) for c, _ in STORES))


def L(M: _Model, sheet):  # noqa: N802 — same-sheet reference helper
    return lambda code, m: f"{MCOL[m]}{M.r(sheet, code)}"


def _year_formula(kind, r):
    if kind == "sum":
        return f"=SUM(D{r}:O{r})"
    if kind == "avg":
        return f"=AVERAGE(D{r}:O{r})"
    if kind == "first":
        return f"=D{r}"
    if kind == "last":
        return f"=O{r}"
    return ""


def _render(M: _Model) -> dict[str, dict[tuple[int, int], str]]:
    """{sheet: {(col, row): raw}} for the correct (gold) model."""
    out = {}
    for sheet, rows in M.sheets.items():
        grid = {}
        for i, h in enumerate(HEADER, 1):
            grid[(i, 1)] = h
        for idx, row in enumerate(rows, 2):
            if row.code:
                grid[(1, idx)] = row.code
            grid[(2, idx)] = row.label
            if row.header:
                continue
            if row.const is not None:
                grid[(3, idx)] = _fmt_num(row.const)
                continue
            if row.monthly is not None:
                for m, v in enumerate(row.monthly):
                    grid[(4 + m, idx)] = _fmt_num(v)
            else:
                for m in range(12):
                    v = row.fn(m)
                    grid[(4 + m, idx)] = v if isinstance(v, str) else _fmt_num(v)
            if callable(row.year):
                grid[(3, idx)] = row.year(None)
            elif row.year != "none":
                grid[(3, idx)] = _year_formula(row.year, idx)
        out[sheet] = grid
    return out


def _grid_to_csv(grid: dict[tuple[int, int], str]) -> str:
    nrows = max(r for _, r in grid)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in range(1, nrows + 1):
        w.writerow([grid.get((c, r), "") for c in range(1, 16)])
    return buf.getvalue()


def _book(grids) -> object:
    book = CALC["Book"]()
    for sheet, grid in grids.items():
        book.cells[sheet] = {k: v for k, v in grid.items()}
    return book


def _addr(c: int, r: int) -> str:
    return f"{CALC['col_name'](c)}{r}"


# --------------------------------------------------------------------------
# Injected errors
# --------------------------------------------------------------------------


def _error_plan(M: _Model):
    """[(sheet, row_code, {m or 'Y': wrong_raw}, issue)] — each plausible, one defect each."""
    A, AM, X = M.A, M.AM, M.X
    cn = CALC["col_name"]

    def r(sheet, code):
        return M.r(sheet, code)

    def at(sheet, code, m):
        return f"{MCOL[m]}{r(sheet, code)}"

    plan = []

    def add(sheet, code, cells, issue):
        plan.append((sheet, code, cells, issue))

    # ABSREL -------------------------------------------------------------
    g = r("Assumptions", "P02.GROWTH")
    add("Volume", "P02.TREND", {m: f"={at('Volume', 'P02.TREND', m - 1)}*(1+Assumptions!{cn(4 + m - 5)}{g})"
                                for m in range(5, 12)}, "ABSREL")
    seas = r("Assumptions", "SEAS")
    add("Volume", "P09.UNITS", {m: f"=ROUND({at('Volume', 'P09.TREND', m)}*Assumptions!$D${seas},0)"
                                for m in range(1, 12)}, "ABSREL")
    wd = r("Assumptions", "P06.WHDISC")
    add("Prices", "P06.WHP", {m: f"=ROUND({at('Prices', 'P06.LIST', m)}*(1-Assumptions!{cn(4 + m - 7)}{wd}),2)"
                              for m in range(7, 12)}, "ABSREL")
    fa = r("Assumptions", "FA0")
    add("Depreciation", "EQ.EXIST", {m: f"=ROUND(Assumptions!{cn(4 + m - 1)}{fa}/({A('LIFE.EQ')}*12),2)"
                                     for m in range(1, 12)}, "ABSREL")
    # RANGE --------------------------------------------------------------
    add("Opex", "PAY.TOT", {m: f"=SUM({at('Opex', 'PAY.D01', m)}:{at('Opex', 'PAY.' + DEPTS[-2][0], m)})"
                            for m in range(12)}, "RANGE")
    gr = r("Revenue", "GROSS")
    add("Revenue", "GROSS", {"Y": f"=SUM(D{gr}:N{gr})"}, "RANGE")
    sal5 = r("Payroll", "D05.SAL")
    add("Payroll", "D05.BONUS", {8: f"=ROUND(SUM(I{sal5}:K{sal5})*{A('BONUS')},2)"}, "RANGE")
    t = r("PnL", "TAX")
    add("CashFlow", "TAXPAID", {6: f"=-SUM(PnL!F{t}:I{t})"}, "RANGE")
    # HARDCODE -----------------------------------------------------------
    add("Opex", "LOGI", {4: "STALE:0.94"}, "HARDCODE")
    add("Payroll", "D04.TAX", {m: f"=ROUND(({at('Payroll', 'D04.SAL', m)}+{at('Payroll', 'D04.BONUS', m)})*0.3,2)"
                               for m in range(12)}, "HARDCODE")
    add("PnL", "TAX", {m: f"=ROUND(MAX(0,{at('PnL', 'EBT', m)})*0.2,2)" for m in range(12)}, "HARDCODE")
    add("Revenue", "P09.RET", {8: "STALE:0.955"}, "HARDCODE")
    add("Volume", "TOT.UNITS", {"Y": "STALE:0.97"}, "HARDCODE")
    # SHEET --------------------------------------------------------------
    add("Revenue", "P04.ONL", {m: f"={X('Volume', 'P04.ONL', m)}*Volume!{MCOL[m]}{r('Prices', 'P04.ONLP')}"
                               for m in range(12)}, "SHEET")
    add("Payroll", "D03.SAL", {m: (f"=ROUND({X('Headcount', 'D03.AVG', m)}*Headcount!"
                                   f"{MCOL[m]}{r('Payroll', 'D03.RATE')},2)") for m in range(6, 12)}, "SHEET")
    add("COGS", "P08.MAT", {m: f"=Prices!{MCOL[m]}{r('Volume', 'P08.UNITS')}*{at('COGS', 'P08.UC', m)}"
                            for m in range(12)}, "SHEET")
    # ROW ----------------------------------------------------------------
    add("Opex", "MKT", {m: f"=ROUND({X('Revenue', 'GROSS', m)}*{A('MKT')},2)" for m in range(12)}, "ROW")
    add("WorkingCapital", "AP", {m: f"=ROUND({X('COGS', 'COGS.TOT', m)}*{A('DPO')}/{AM('DAYS', m)},2)"
                                 for m in range(12)}, "ROW")
    add("Revenue", "RETURNS", {m: f"=ROUND({at('Revenue', 'GROSS', m)}*{A('RET.RATE')},2)"
                               for m in range(12)}, "ROW")
    add("Revenue", "P06.WH", {9: f"={X('Volume', 'P06.WH', 9)}*{X('Prices', 'P06.RETP', 9)}"}, "ROW")
    # PERIOD -------------------------------------------------------------
    add("Depreciation", "EQ.NEW", {m: f"=ROUND({X('Capex', 'EQ.CUM', m)}/({A('LIFE.EQ')}*12),2)"
                                   for m in range(1, 12)}, "PERIOD")
    add("Opex", "COMM", {7: f"=ROUND({X('Revenue', 'GROSS.ONL', 6)}*{A('COMM')},2)"}, "PERIOD")
    add("CashFlow", "OPEN", {6: f"={at('CashFlow', 'CLOSE', 4)}"}, "PERIOD")
    add("Headcount", "D05.OPEN", {8: f"={at('Headcount', 'D05.CLOSE', 6)}"}, "PERIOD")
    add("WorkingCapital", "DNWC", {4: f"={at('WorkingCapital', 'NWC', 4)}-{at('WorkingCapital', 'NWC', 2)}"},
        "PERIOD")
    # SIGN ---------------------------------------------------------------
    add("CashFlow", "DNWC", {m: f"={X('WorkingCapital', 'DNWC', m)}" for m in range(12)}, "SIGN")
    add("PnL", "EBT", {m: f"={at('PnL', 'EBIT', m)}+{at('PnL', 'INT', m)}" for m in range(12)}, "SIGN")
    add("Revenue", "NET", {3: f"={at('Revenue', 'GROSS', 3)}+{at('Revenue', 'RETURNS', 3)}"}, "SIGN")
    add("Headcount", "D02.CLOSE", {5: (f"={at('Headcount', 'D02.OPEN', 5)}+{at('Headcount', 'D02.HIRE', 5)}"
                                       f"+{at('Headcount', 'D02.LEAVE', 5)}")}, "SIGN")
    # LOGIC --------------------------------------------------------------
    add("Prices", "P10.LIST", {m: f"=ROUND({A('P10.PRICE')}*(1+{A('IDX.PRICE')}),2)" for m in range(6, 12)},
        "LOGIC")
    add("COGS", "P05.UC", {m: f"=ROUND({A('P05.UC')}*(1+{A('IDX.MAT')}),2)" for m in range(3, 12)}, "LOGIC")
    s7 = r("Payroll", "D07.SAL")
    add("Payroll", "D07.BONUS", {m: f"=ROUND(SUM({MCOL[m - 2]}{s7}:{MCOL[m]}{s7})*{A('BONUS')},2)"
                                 for m in QUARTER_ENDS}, "LOGIC")
    add("Debt", "B.INT", {m: f"=ROUND({at('Debt', 'B.OPEN', m)}*{A('RATE.B')}/12,2)" for m in range(12)},
        "LOGIC")
    add("Volume", "P07.ONL", {m: f"=ROUND({at('Volume', 'P07.UNITS', m)}*{A('P07.ONLSH')},0)"
                              for m in range(4)}, "LOGIC")
    # Spec-only errors: every row below is internally consistent, only the methodology says it is wrong.
    add("Prices", "P12.RETP", {10: f"=ROUND({at('Prices', 'P12.LIST', 10)}*(1-{A('PROMO')}),2)"}, "LOGIC")
    add("Payroll", "D13.RATE", {m: f"={A('D13.SAL')}" for m in range(9, 12)}, "LOGIC")
    add("COGS", "P21.UC", {m: f"={A('P21.UC')}" for m in range(3, 12)}, "LOGIC")
    add("Payroll", "D02.BONUS", {m: f"=ROUND({at('Payroll', 'D02.SAL', m)}*{A('BONUS.PROD')},2)"
                                 for m in range(12)}, "LOGIC")
    add("Opex", "FLEET", {m: f"={X('Fleet', 'FUEL.TOT', m)}" for m in range(12)}, "ROW")
    add("Depreciation", "IT.NEW", {m: f"=ROUND({X('Capex', 'IT.CUM', m - 1)}/({A('LIFE.EQ')}*12),2)"
                                   for m in range(1, 12)}, "ROW")
    # Energy
    add("Energy", "L02.TARIFF", {m: f"=ROUND({A('ELEC.T')}*(1+{A('ELEC.IDX')}),2)" for m in range(6, 12)},
        "LOGIC")
    add("Energy", "L06.HOURS", {m: f"={AM('WDAYS', m)}*{A('L06.SHIFTS')}*{A('HOURS.SHIFT')}"
                                for m in range(12)}, "ROW")
    add("Energy", "L05.HOURS", {m: f"={AM('WDAYS', m)}*{A('L05.SHIFTS')}*{A('HOURS.SHIFT')}"
                                for m in range(9, 12)}, "ROW")
    add("Energy", "L07.TARIFF", {m: f"=ROUND({A('GAS.T')}*1.09,2)" for m in range(6, 12)}, "HARDCODE")
    add("Energy", "KWH.TOT", {m: "=" + "+".join(at("Energy", f"{c}.CONS", m) for c, _ in LINES)
                              for m in range(12)}, "LOGIC")
    add("Energy", "L04.HOURS", {3: f"={AM('WDAYS', 3)}*{A('L04.SHIFTS')}*{A('HOURS.SHIFT')}"}, "LOGIC")
    # Fleet
    add("Fleet", "V07.KM", {m: f"=ROUND({A('V07.KM')}*{AM('SEAS', m)},0)" for m in range(12)}, "LOGIC")
    add("Fleet", "V05.LEASE", {m: f"={A('V05.LEASE')}" for m in range(6, 12)}, "LOGIC")
    add("Fleet", "V09.INS", {0: f"={A('V09.INS')}"}, "LOGIC")
    add("Fleet", "V03.FUEL", {m: (f"=ROUND({at('Fleet', 'V03.KM', m)}*{A('V03.CONS')}/100*"
                                  f"{at('Fleet', 'FUEL.PRICE', m)},2)") for m in range(12)}, "LOGIC")
    add("Fleet", "FUEL.TOT", {m: "=" + "+".join(at("Fleet", f"{c}.FUEL", m) for c, _ in VANS[:-1])
                              for m in range(12)}, "LOGIC")
    mk = r("Assumptions", "MAINT.KM")
    add("Fleet", "V06.MAINT", {m: f"=ROUND({at('Fleet', 'V06.KM', m)}*Assumptions!{cn(3 + m)}${mk},2)"
                               for m in range(1, 12)}, "ABSREL")
    add("Fleet", "V12.KM", {m: f"=ROUND({A('V12.KM')}*{AM('SEAS', m)},0)" for m in range(12)}, "LOGIC")
    add("Fleet", "V02.FUEL", {4: (f"=ROUND({at('Fleet', 'V02.KM', 4)}*{A('V02.CONS')}/100*"
                                  f"{at('Fleet', 'FUEL.PRICE', 3)},2)")}, "PERIOD")
    # Stores
    add("Stores", "S05.RENT", {m: f"=ROUND({A('S05.RENT')}*(1+{A('STORE.IDX')}),2)" for m in range(3, 12)},
        "LOGIC")
    add("Stores", "S02.RENT", {m: f"={A('S02.RENT')}" if m < 3 else
                               f"=ROUND({A('S02.RENT')}*(1+{A('STORE.IDX')}),2)" for m in range(12)}, "LOGIC")
    add("Stores", "S06.REV", {m: f"=ROUND({X('Revenue', 'GROSS.RET', m)}*{A('S06.SH')},2)"
                              for m in range(9, 12)}, "LOGIC")
    add("Stores", "S07.REV", {m: f"=ROUND({X('Revenue', 'GROSS', m)}*{A('S07.SH')},2)" for m in range(12)},
        "ROW")
    add("Stores", "S04.RESULT", {m: (f"=ROUND({at('Stores', 'S04.REV', m)}*{X('COGS', 'GM', m)},2)"
                                     f"+{at('Stores', 'S04.COST', m)}") for m in range(12)}, "SIGN")
    add("Stores", "S08.STAFF", {m: f"=ROUND({A('S08.STAFF')}*{A('STORE.WAGE')}*{A('PART.K')}*(1+0.3),2)"
                                for m in range(12)}, "HARDCODE")
    add("Stores", "S01.RENT", {2: f"=ROUND({A('S01.RENT')}*(1+{A('STORE.IDX')}),2)"}, "LOGIC")
    add("Stores", "S10.RENT", {m: f"={A('S10.RENT')}" if m < 3 else
                               f"=ROUND({A('S10.RENT')}*(1+{A('STORE.IDX')}),2)" for m in range(12)}, "LOGIC")
    add("Fleet", "V13.KM", {m: f"=ROUND({A('V13.KM')}*{AM('SEAS', m)},0)" for m in range(12)}, "LOGIC")
    add("Fleet", "V16.LEASE", {9: f"={A('V16.LEASE')}"}, "LOGIC")
    add("Fleet", "V18.INS", {0: f"={A('V18.INS')}"}, "LOGIC")
    add("Stores", "S11.RENT", {m: f"=ROUND({at('Stores', 'S11.REV', m)}*{A('S11.TURN')},2)" for m in range(12)},
        "LOGIC")
    add("Stores", "S14.RENT", {5: f"={A('S14.RENT')}"}, "LOGIC")
    add("Stores", "S16.REV", {m: f"=ROUND({X('Revenue', 'GROSS.RET', m)}*{A('S16.SH')},2)" for m in range(12)},
        "ROW")
    add("Energy", "L11.HOURS", {9: f"={AM('WDAYS', 9)}*{A('L11.SHIFTS')}*{A('HOURS.SHIFT')}"}, "LOGIC")
    add("Stores", "S18.REV", {7: f"=ROUND({X('Revenue', 'GROSS.RET', 7)}*{A('S18.SH')},2)"}, "LOGIC")
    return plan


def _eval_in_context(raw: str, sheet: str, values) -> float | None:
    if not raw.startswith("="):
        return float(raw)
    node = CALC["Parser"](raw[1:], sheet).parse()
    book = CALC["Book"]()
    book.cells = {s: {} for s in SHEETS}  # references resolve against `values`
    v = book._eval(node, values)
    return v if isinstance(v, float) else None


def _differs(a, b) -> bool:
    if not isinstance(a, float) or not isinstance(b, float):
        return True
    return abs(a - b) > max(0.05, 1e-9 * abs(b))


@functools.cache
def build():
    M = _build_world()
    gold = _render(M)
    gold_values = _book(gold).evaluate()
    bad = [k for k, v in gold_values.items() if isinstance(v, CALC["CellError"])]
    assert not bad, bad[:5]
    broken = {s: dict(g) for s, g in gold.items()}
    instances = []  # (sheet, code, [(c, r)], issue)
    for sheet, code, cells, issue in _error_plan(M):
        row = M.r(sheet, code)
        kept = []
        for m, raw in cells.items():
            c = 3 if m == "Y" else 4 + m
            key = (sheet, c, row)
            gv = gold_values[key]
            if raw.startswith("STALE:"):
                raw = _fmt_num(round(gv * float(raw[6:]), 2 if abs(gv) < 1e6 else 0))
            wrong = _eval_in_context(raw, sheet, gold_values)
            assert raw != gold[sheet][(c, row)], (sheet, code, m)
            if not _differs(wrong, gv):
                # same value: not an erroneous cell (spec, section 2); a row error keeps its
                # wrong formula here too so the row stays consistent, but it is not audited
                if len(cells) > 1 and raw.startswith("="):
                    broken[sheet][(c, row)] = raw
                continue
            assert (c, row) not in [x for inst in instances if inst[0] == sheet for x in inst[2]]
            broken[sheet][(c, row)] = raw
            kept.append((c, row))
        assert kept, (sheet, code)
        instances.append((sheet, code, kept, issue))
    s11 = [gold_values[("Stores", 4 + m, M.r("Stores", "S11.RENT"))] for m in range(12)]
    floor = gold_values[("Assumptions", 3, M.r("Assumptions", "S11.RENT"))]
    assert 3 <= sum(v == floor for v in s11) <= 9, "S11: the minimum rent must bind in some months only"
    broken_values = _book(broken).evaluate()
    bad = [k for k, v in broken_values.items() if isinstance(v, CALC["CellError"])]
    assert not bad, bad[:5]
    audit = sorted({(s, _addr(c, r), issue) for s, _, cells, issue in instances for c, r in cells})
    key_cells = []
    for code in ("NI", "EBITDA"):
        key_cells += [("PnL", 4 + m, M.r("PnL", code)) for m in range(12)]
    key_cells += [("CashFlow", 4 + m, M.r("CashFlow", "CLOSE")) for m in range(12)]
    key_cells.append(("PnL", 3, M.r("PnL", "NI")))
    for sheet, code in (("Revenue", "NET"), ("COGS", "COGS.TOT"), ("Payroll", "PAY.TOT"),
                        ("Opex", "OPEX.TOT"), ("Depreciation", "TOT"), ("PnL", "TAX"),
                        ("CashFlow", "CFO"), ("CashFlow", "TAXBAL"), ("Debt", "INT.TOT"),
                        ("WorkingCapital", "NWC"), ("Volume", "TOT.UNITS"), ("Headcount", "TOT.AVG"),
                        ("Revenue", "GROSS"), ("Energy", "COST.TOT"), ("Energy", "KWH.TOT"),
                        ("Energy", "M3.TOT"), ("Fleet", "TOT"), ("Fleet", "KM.TOT"), ("Fleet", "FUEL.TOT"),
                        ("Stores", "REV.TOT"), ("Stores", "COST.TOT"), ("Stores", "RESULT.TOT"),
                        ("Opex", "FLEET")):
        key_cells.append((sheet, 3, M.r(sheet, code)))
    assert len(key_cells) == 60
    return {
        "model": M,
        "gold": gold,
        "broken": broken,
        "gold_values": gold_values,
        "instances": instances,
        "audit": audit,
        "key_cells": key_cells,
        "spec": _spec_text(M),
    }


# --------------------------------------------------------------------------
# model_spec.md
# --------------------------------------------------------------------------

_PRODUCT_NOTES = {
    "P01": (
        "Батон нарезной (P01) — самая массовая позиция, её везут во все каналы. Особых правил у батона нет: "
        "тренд, сезонность, каналы, цены и сырьё считаются по общим правилам разделов 4–7. В 2026 году "
        "обсуждали вывести батон в отдельную акцию к Новому году, но решение не принято, и в бюджете-2027 "
        "никакой акции по нему нет."
    ),
    "P02": (
        "«Бородинский» (P02) продаётся стабильно, с небольшим ростом тренда. Всё по общим правилам. "
        "Коммерческая служба просила учесть, что у этого хлеба в декабре традиционно высокий спрос, — это "
        "уже заложено в общий индекс сезонности, отдельного множителя для P02 нет."
    ),
    "P03": (
        "Французский багет (P03) участвует в ноябрьской акции «Французская неделя». В ноябре его "
        "розничная цена (строка P03.RETP) равна прейскурантной цене ноября, уменьшенной на скидку акции "
        "(допущение PROMO), с округлением до копеек. Скидка действует только в рознице: оптовая и "
        "онлайн-цены багета в ноябре считаются от прейскуранта без скидки, как в любом другом месяце."
    ),
    "P04": (
        "Круассан сливочный (P04) — второй участник «Французской недели»: в ноябре его розничная цена "
        "тоже снижается на скидку PROMO (от ноябрьского прейскуранта, до копеек), а опт и онлайн идут "
        "без скидки. В остальном у круассана всё стандартно, включая онлайн-канал с января."
    ),
    "P05": (
        "Хлеб зерновой (P05) делается из смеси, которую мы покупаем по долгосрочному контракту с "
        "фиксированной ценой на весь 2027 год. Поэтому рост цен сырья с апреля (IDX.MAT) к нему не "
        "применяется: сырьё на единицу P05.UC во всех двенадцати месяцах равно допущению P05.UC. Цены "
        "продажи зернового хлеба индексируются с июля на общих основаниях — контракт касается только "
        "закупки."
    ),
    "P06": (
        "Пирог с капустой (P06) — сезонный товар, но сезонность у него общая для всех продуктов, "
        "отдельного индекса нет. Оптовая скидка у пирога своя (P06.WHDISC), как и у каждого продукта; "
        "правил-исключений у P06 нет."
    ),
    "P07": (
        "Сушки ванильные (P07) до весны 2027 года продавались только в рознице и оптом. Онлайн-канал для "
        "сушек открывается в мае: в январе–апреле онлайн-продажи P07.ONL равны нулю — в этих ячейках "
        "стоит число 0, и это не ошибка, а с мая они считаются по общей формуле от доли онлайн-канала "
        "P07.ONLSH. Оптовый объём сушек, как и у всех, — остаток после розницы и онлайна, поэтому в "
        "январе–апреле весь нерозничный объём уходит в опт."
    ),
    "P08": (
        "Лаваш тонкий (P08) считается полностью по общим правилам. Иногда его путают с позицией «лаваш "
        "армянский» из архивной модели 2025 года — в текущей модели такой позиции нет."
    ),
    "P09": (
        "Булочка с маком (P09) — позиция без исключений: общий тренд, общий индекс сезонности месяца, "
        "общие правила каналов и цен."
    ),
    "P10": (
        "Хлеб «Социальный» (P10) продаётся по цене, согласованной с администрацией района на весь год. "
        "Индексация цен с июля к нему не применяется: прейскурантная цена P10.LIST во всех месяцах равна "
        "допущению P10.PRICE без округления и без множителей. В 2026 году обсуждалась индексация "
        "социального хлеба на 3 %, но решение отложено, и в модель-2027 она не входит. Онлайн социальный "
        "хлеб не продаётся — доля P10.ONLSH равна нулю, формулы онлайн-строк при этом общие. Рост цен "
        "сырья с апреля на социальный хлеб распространяется, как на все продукты, кроме зернового."
    ),
    "P11": (
        "Чиабатта (P11) — новинка 2026 года, но в 2027-м она уже считается по общим правилам без "
        "исключений."
    ),
    "P12": (
        "Кекс творожный (P12) в прошлом году участвовал в ноябрьской акции, однако в 2027 году акция "
        "«Французская неделя» распространяется только на багет и круассан; для кекса розничная цена в "
        "ноябре — обычная, равная прейскуранту."
    ),
    "P13": (
        "Хлеб бездрожжевой (P13) пекут на ржаной закваске, в документах цеха его часто называют просто "
        "«закваской». Для него тоже обсуждали долгосрочный контракт на сырьё, как для зернового хлеба, "
        "но договор так и не подписали, поэтому рост цен сырья с апреля к бездрожжевому хлебу "
        "применяется в общем порядке. Остальное — по общим правилам."
    ),
    "P14": (
        "Бублик с кунжутом (P14) продаётся в основном оптом в кофейни, розничная доля у него невелика — "
        "это уже отражено в допущении P14.RETSH. Формулы каналов у бублика общие, особых правил нет."
    ),
    "P15": (
        "Слойка с вишней (P15): вишнёвая начинка летом дорожает, и технологи просили заложить "
        "сезонную надбавку на сырьё. Финансовая служба её не приняла: для слойки действует только "
        "общий рост цен сырья с апреля, отдельной летней надбавки нет."
    ),
    "P16": (
        "Пряник медовый (P16) — продукт длительного хранения. В прошлые годы для пряников отдельно "
        "считали страховой запас; в модели-2027 запасы считаются общим правилом листа WorkingCapital, "
        "а повышенный декабрьский спрос уже заложен в общий индекс сезонности."
    ),
    "P17": (
        "Хлеб тостовый (P17) продаётся во всех трёх каналах с января — в отличие от сушек, у которых "
        "онлайн открывается только в мае. Правила общие."
    ),
    "P18": (
        "Ватрушка с творогом (P18) в 2026 году продавалась со скидкой по вторникам. Эта практика "
        "прекращена с 1 января 2027 года, и в модели для ватрушки никаких скидок, кроме обычной "
        "оптовой, нет."
    ),
    "P19": (
        "Бриошь (P19) — тоже французская выпечка, и её иногда ошибочно относят к «Французской неделе». "
        "В акцию бриошь не входит: её розничная цена в ноябре равна прейскуранту, как в любом другом "
        "месяце."
    ),
    "P20": (
        "Штрудель яблочный (P20) — новинка, запускается с января во всех каналах. Правила общие; "
        "допущения по штруделю взяты из пилотных продаж и на листе Assumptions стоят в общем порядке."
    ),
    "P21": (
        "Хлеб с отрубями (P21) по рецептуре близок к зерновому, и его смесь поставляет тот же "
        "производитель. Однако долгосрочный контракт с фиксированной ценой заключён только на смесь "
        "для зернового хлеба P05; отрубная смесь покупается по текущим ценам, поэтому рост цен сырья "
        "с апреля к P21 применяется."
    ),
    "P22": (
        "Сочник с творогом (P22) — последний продукт в списке. Правила общие; итоговые строки всех "
        "листов включают его наравне с остальными."
    ),
}

_DEPT_NOTES = {
    "D01": (
        "Производство (D01) — единственное подразделение с ежемесячной премией: каждый месяц "
        "начисляется премия, равная окладам этого месяца, умноженным на BONUS.PROD, с округлением до "
        "копеек. Квартальной премии у производства нет. Оклады производства индексируются с октября "
        "на общих основаниях."
    ),
    "D02": "Логистика (D02) — общие правила: индексация окладов с октября и квартальная премия.",
    "D03": "Продажи (D03) — общие правила оклада, индексации и квартальной премии.",
    "D04": (
        "Маркетинг (D04) — общие правила. Отдел просил поднять себе ставку премии, но просьба не "
        "согласована: ставка квартальной премии та же, BONUS."
    ),
    "D05": "ИТ (D05) — общие правила; приём сотрудников летом уже отражён в строке D05.HIRE.",
    "D06": "Финансы (D06) — общие правила.",
    "D07": (
        "Администрация (D07) работает по контрактам с фиксированным вознаграждением: премий у "
        "администрации нет ни в каком месяце (строка D07.BONUS — нули во всех месяцах), а индексация "
        "окладов с октября к ней не применяется — оклад на человека D07.RATE весь год равен допущению "
        "D07.SAL."
    ),
    "D08": "Контроль качества (D08) — общие правила.",
    "D09": "Закупки (D09) — общие правила.",
    "D10": (
        "Сервис и ремонт (D10) — общие правила. Ремонтники часто работают сверхурочно, но "
        "сверхурочные в бюджет-2027 отдельно не закладываются: они покрываются квартальной премией."
    ),
    "D11": (
        "Склад (D11) работает в три смены, но сменность уже учтена в окладе D11.SAL; правила общие — "
        "индексация с октября и квартальная премия."
    ),
    "D12": "Охрана труда (D12) — небольшое подразделение, правила общие, премия квартальная.",
    "D13": (
        "Юридический отдел (D13) переходит на контракты с фиксированным вознаграждением, как "
        "администрация, но только с 2028 года. В 2027 году у юристов общие правила: оклад "
        "индексируется с октября, квартальная премия начисляется."
    ),
    "D14": (
        "Управление персоналом (D14) — общие правила. Расходы этого подразделения включаются в итог "
        "по персоналу наравне с остальными."
    ),
}


_LINE_NOTES = {
    "L01": (
        "Туннельная печь №1 (L01) — основная печь формового хлеба. Правила общие: рабочие дни WDAYS, "
        "смены L01.SHIFTS, электрический тариф с июльской индексацией. Тариф этой печи служит "
        "ориентиром и для электрофургона автопарка (паспорт V03), поэтому строку Energy:L01.TARIFF "
        "нельзя «упрощать» и заменять ссылками на другие строки."
    ),
    "L02": (
        "Туннельная печь №2 (L02) питается от отдельного ввода, электроэнергию для которого мы покупаем "
        "по договору с энергосбытом с фиксированной ценой на весь 2027 год. Поэтому июльская индексация "
        "ELEC.IDX к L02 не применяется: тариф L02.TARIFF во всех двенадцати месяцах равен допущению "
        "ELEC.T. Часы и потребление печи — по общим правилам."
    ),
    "L03": (
        "Ротационная печь сдобного цеха (L03) в августе стоит на плановом капитальном ремонте: "
        "машино-часы L03.HOURS в августе равны нулю (в ячейке стоит число 0), в остальные месяцы — "
        "общая формула. Ремонт оплачивается подрядчику по статье прочих расходов, в лист Energy он не "
        "попадает. Сервисная служба предлагала перенести ремонт на июль, но график утверждён на август."
    ),
    "L04": (
        "Линия круассанов (L04) монтируется весной и запускается в мае: в январе–апреле её машино-часы "
        "равны нулю (числа 0), с мая считаются по общей формуле. Пусконаладочные работы в апреле идут "
        "от временного генератора подрядчика и в модель не входят, поэтому апрель — тоже ноль."
    ),
    "L05": (
        "Тестомесильный комплекс (L05) до сентября включительно работает в своём обычном режиме — "
        "L05.SHIFTS смен в сутки. С октября, под осенне-зимний пик, комплекс переходит на "
        "круглосуточную работу: число смен в формуле машино-часов берётся из допущения SHIFTS.MAX, "
        "а не из L05.SHIFTS. Рабочие дни при этом те же — WDAYS."
    ),
    "L06": (
        "Холодильные камеры готовой продукции (L06) работают без выходных: в формуле машино-часов "
        "вместо рабочих дней WDAYS используется число календарных дней месяца DAYS. Смены — L06.SHIFTS, "
        "тариф — общий электрический."
    ),
    "L07": (
        "Газовая подовая печь (L07) работает на газе: её потребление считается в кубометрах (допущение "
        "L07.POWER — расход газа в час), тариф — GAS.T, с июля — GAS.T × (1 + GAS.IDX) до копеек. В "
        "итог по электроэнергии KWH.TOT газовые линии не входят — они собираются в M3.TOT."
    ),
    "L08": (
        "Газовый пароконвектомат (L08) — вторая газовая линия, правила те же, что у L07: потребление в "
        "кубометрах, газовый тариф с июльской индексацией, в KWH.TOT не входит. Был проект перевести "
        "пароконвектомат на электричество, но он отложен до 2028 года."
    ),
    "L09": (
        "Линия упаковки (L09) — электрическая, правила общие. Не путайте её со статьёй упаковки "
        "`COGS:Pxx.PACK`: там считается стоимость плёнки и пакетов на единицу продукции, а здесь — "
        "только электроэнергия, которую потребляет сама линия."
    ),
    "L10": (
        "Газовый парогенератор (L10) снабжает паром все печи и не останавливается на выходные: как и "
        "холодильные камеры L06, он считается по календарным дням DAYS, а не по рабочим. При этом он "
        "газовый — потребление в кубометрах, газовый тариф с июльской индексацией, итог M3.TOT."
    ),
    "L11": (
        "Камера шоковой заморозки (L11) нужна только под новогодний сезон заготовок: она работает в "
        "ноябре и декабре, а в январе–октябре её машино-часы равны нулю (числа 0). В ноябре и декабре — "
        "общая формула с рабочими днями и L11.SHIFTS."
    ),
    "L12": (
        "Линия сушек и баранок (L12) подключена к тому же отдельному вводу, что и туннельная печь №2, и "
        "получает электроэнергию по тому же договору с фиксированной ценой: тариф L12.TARIFF весь год "
        "равен ELEC.T, без июльской индексации. Если эта строка выглядит не так, как у соседних "
        "электрических линий, — это не ошибка."
    ),
}

_VAN_NOTES = {
    "V01": "ГАЗель маршрута «Север» (V01) — всё по общим правилам раздела 9.2.",
    "V02": (
        "ГАЗель маршрута «Юг» (V02) — общие правила. Водитель просил учесть сезонную надбавку к расходу "
        "топлива зимой, но норматив V02.CONS утверждён один на весь год."
    ),
    "V03": (
        "Электрофургон (V03) заряжается от сети завода. Его строка V03.FUEL — это стоимость "
        "электроэнергии: пробег × V03.CONS (кВт·ч на 100 км) / 100 × тариф печи L01 того же месяца "
        "(Energy:L01.TARIFF), до копеек. Цена дизельного топлива FUEL.PRICE к электрофургону не "
        "относится. Остальное — лизинг, обслуживание по километрам, страховка в январе — общее."
    ),
    "V04": (
        "Фургон-рефрижератор (V04) обслуживается по сервисному контракту: строка V04.MAINT каждый "
        "месяц равна допущению V04.SVC, независимо от пробега. Топливо, лизинг и страховка — общие."
    ),
    "V05": (
        "ГАЗель Бизнес маршрута «Запад» (V05) выкупается из лизинга: последний лизинговый платёж — в "
        "июне, с июля по декабрь строка V05.LEASE равна нулю (числа 0). После выкупа машина остаётся в "
        "парке, пробег, топливо, обслуживание и страховка считаются как прежде."
    ),
    "V06": "Фургон маршрута «Восток» (V06) — общие правила.",
    "V07": (
        "Грузовик междугородних рейсов (V07) — собственный (лизинга нет, допущение V07.LEASE равно "
        "нулю, формула строки лизинга общая). Междугородние рейсы идут по жёсткому графику, поэтому его "
        "пробег не зависит от сезонности продаж: V07.KM в каждом месяце равен допущению V07.KM, без "
        "индекса SEAS и без округления."
    ),
    "V08": "Фургон кондитерского цеха (V08) — общие правила.",
    "V09": (
        "Фургон сетевых поставок (V09): полис ОСАГО и КАСКО у этой машины продлевается не в январе, а в "
        "июле — страховка V09.INS начисляется в июле, во все остальные месяцы, включая январь, в строке "
        "стоит 0."
    ),
    "V10": (
        "Новый «Соболь» (V10) покупается в лизинг в марте: в январе и феврале его пробег и лизинг равны "
        "нулю (числа 0), с марта — по общим правилам. Страховка V10.INS оплачивается при постановке на "
        "учёт, в марте; в январе её нет."
    ),
    "V11": "Пикап хозяйственной службы (V11) — собственный, лизинга нет; в остальном правила общие.",
    "V12": (
        "Резервный фургон (V12) выходит на линию только на замены, поэтому его пробег — базовый пробег "
        "V12.KM × индекс сезонности месяца × коэффициент RESERVE.K, до километров."
    ),
    "V13": (
        "Микроавтобус для развозки смен (V13) возит сотрудников на завод и обратно, поэтому его пробег "
        "следует не за продажами, а за рабочими днями: V13.KM × WDAYS / DAYS того же месяца, до "
        "километров. Индекс сезонности SEAS к нему не применяется."
    ),
    "V14": (
        "Фургон маршрута «Юг-2» (V14) — общие правила. Зимняя резина и шиномонтаж входят в обслуживание "
        "по километрам MAINT.KM и отдельно не считаются."
    ),
    "V15": (
        "Второй электрофургон (V15) считается так же, как первый (паспорт V03): стоимость энергии — "
        "пробег × V15.CONS / 100 × Energy:L01.TARIFF того же месяца, до копеек."
    ),
    "V16": (
        "Сезонный фургон (V16) берётся в лизинг только на летний сезон развозки мороженого-десертов, с мая "
        "по сентябрь. В январе–апреле и в октябре–декабре его пробег и лизинг равны нулю (числа 0), а "
        "годовая страховка оплачивается в мае, при получении машины; в январе её нет."
    ),
    "V17": (
        "Фургон на метане (V17) заправляется сжатым природным газом на заводской станции. Его строка "
        "V17.FUEL — пробег × V17.CONS (м³ на 100 км) / 100 × газовый тариф печи L07 того же месяца "
        "(Energy:L07.TARIFF), до копеек. Цена дизельного топлива к нему не относится."
    ),
    "V18": (
        "Легковой автомобиль дирекции (V18) застрахован по полису с рассрочкой: страховка V18.INS "
        "оплачивается четырьмя равными частями — в январе, апреле, июле и октябре по V18.INS / 4 до "
        "копеек; в остальные месяцы 0. Пробег, топливо, обслуживание и лизинг — по общим правилам."
    ),
    "V19": (
        "Фургон хлебных киосков (V19) — общие правила. Он развозит товар по фирменным магазинам, но его "
        "расходы остаются на листе Fleet и в расходы магазинов (лист Stores) не переносятся."
    ),
    "V20": (
        "Мусоровоз хозяйственной службы (V20) — собственный (лизинга нет), вывозит отходы по жёсткому "
        "графику коммунальной службы, поэтому, как и междугородний грузовик V07, его пробег не зависит "
        "от сезонности: V20.KM в каждом месяце равен допущению V20.KM. Страховка — в январе."
    ),
}

_STORE_NOTES = {
    "S01": (
        "Магазин на Озёрной (S01) — флагманский, правила общие: аренда индексируется с апреля, не "
        "раньше. Арендодатель предлагал индексацию с марта, но подписанный договор — с апреля."
    ),
    "S02": (
        "Киоск в ТЦ «Меридиан» (S02) с 2027 года платит аренду с оборота: S02.RENT каждый месяц равна "
        "выручке киоска S02.REV того же месяца × S02.TURN, до копеек. Прежняя фиксированная ставка "
        "осталась в допущении S02.RENT для справки и в расчёте не используется."
    ),
    "S03": (
        "Магазин у вокзала (S03) открывается в апреле после ремонта помещения: в январе–марте его "
        "выручка, аренда и продавцы равны нулю (числа 0); расходы и результат считаются общими "
        "формулами и дают ноль сами. С апреля — общие правила, включая апрельскую индексацию аренды."
    ),
    "S04": "Магазин на Садовой (S04) — общие правила.",
    "S05": (
        "Магазин при заводе (S05) арендует помещение у собственника завода по долгосрочному договору "
        "с неизменной ставкой: индексация аренды с апреля к нему не применяется, S05.RENT весь год "
        "равна допущению S05.RENT."
    ),
    "S06": (
        "Сезонный павильон в парке (S06) работает с января по сентябрь и закрывается на зиму: в "
        "октябре–декабре его выручка, аренда и продавцы равны нулю (числа 0)."
    ),
    "S07": (
        "Магазин в Заречье (S07) — общие правила. Его доля S07.SH, как у всех магазинов, кроме пункта "
        "самовывоза S16, — доля розничной выручки GROSS.RET, а не валовой: опт через магазины не "
        "проходит."
    ),
    "S08": (
        "Мини-пекарня на Рыночной (S08): продавцы работают на полставки, поэтому расходы на них — "
        "S08.STAFF × STORE.WAGE × PART.K × (1 + PAYTAX), до копеек."
    ),
    "S09": (
        "Магазин на Лесной (S09) — общие правила. В 2026 году его хотели закрыть, но решение "
        "отменено, и в бюджете-2027 он работает весь год."
    ),
    "S10": (
        "Корнер в гипермаркете «Прайм» (S10) арендуется у иностранной сети, ставка S10.RENT задана в "
        "евро: аренда месяца — S10.RENT × курс евро EUR того же месяца, до копеек. Апрельская "
        "индексация к корнеру не применяется — договор в валюте без индексации."
    ),
    "S11": (
        "Магазин в аэропорту (S11) платит аренду с оборота, но не меньше гарантированного минимума: "
        "аренда месяца — большее из двух чисел, минимальной аренды S11.RENT и выручки магазина S11.REV × "
        "S11.TURN (до копеек), то есть `MAX(…)`. Апрельская индексация к аэропорту не применяется."
    ),
    "S12": (
        "Магазин на Набережной (S12) — общие правила. Летом у магазина работает веранда, но её выручка "
        "входит в долю S12.SH и отдельно не выделяется."
    ),
    "S13": (
        "Точка на фермерском рынке (S13) работает только по выходным, продавцы оформлены на полставки, "
        "как в мини-пекарне S08: расходы на них умножаются на PART.K. Аренда — по общим правилам."
    ),
    "S14": (
        "Магазин в Северном (S14) открывается в июле в новом жилом квартале: в январе–июне его выручка, "
        "аренда и продавцы равны нулю (числа 0), с июля — общие правила, аренда уже с апрельской "
        "индексацией."
    ),
    "S15": (
        "Кофейня-пекарня на Театральной (S15): продавцы здесь одновременно бариста и получают надбавку, "
        "поэтому расходы на них — S15.STAFF × STORE.WAGE × (1 + S15.PREM) × (1 + PAYTAX), до копеек."
    ),
    "S16": (
        "Пункт самовывоза онлайн-заказов (S16) выдаёт заказы маркетплейса, поэтому его выручка — доля "
        "S16.SH не розничной, а онлайн-выручки `Revenue:GROSS.ONL` того же месяца, до копеек. Аренда, "
        "продавцы и результат — по общим правилам."
    ),
    "S17": (
        "Магазин на Заводской (S17) — общие правила. Он стоит рядом с магазином при заводе S05, но "
        "арендует помещение у другого собственника, поэтому исключение S05 о неизменной ставке к нему "
        "не относится."
    ),
    "S18": (
        "Киоск у школы (S18) закрыт на школьные каникулы — в июле и августе его выручка, аренда и "
        "продавцы равны нулю (числа 0). В остальные месяцы — общие правила; аренда с апреля "
        "индексируется, в сентябре киоск открывается уже по индексированной ставке."
    ),
}


def _spec_text(M: _Model) -> str:
    prod_list = "\n".join(f"| {c} | {n} |" for c, n in PRODUCTS)
    dept_list = "\n".join(f"| {c} | {n} |" for c, n in DEPTS)
    prod_notes = "\n\n".join(_PRODUCT_NOTES[c] for c, _ in PRODUCTS)
    dept_notes = "\n\n".join(_DEPT_NOTES[c] for c, _ in DEPTS)
    line_notes = "\n\n".join(_LINE_NOTES[c] for c, _ in LINES)
    van_notes = "\n\n".join(_VAN_NOTES[c] for c, _ in VANS)
    store_notes = "\n\n".join(_STORE_NOTES[c] for c, _ in STORES)
    return f"""# Методика бюджетной модели ООО «Озёрная хлебная мануфактура» на 2027 год

Документ описывает, как должна считаться каждая статья бюджетной модели. Это единственный источник
истины: если формула в книге расходится с методикой, права методика. Названия листов, коды строк и
коды допущений в тексте совпадают с книгой. Методику писали несколько человек, поэтому часть правил
изложена в общих разделах, а часть — в «паспортах» продуктов, подразделений, производственных линий,
машин и магазинов (разделы 4.1, 8.1, 9.1.1, 9.2.1 и 9.3.1); исключения из общих правил действуют только
там, где они прямо названы.

## 1. Устройство книги

Книга лежит в каталоге `model/`: один CSV-файл — один лист, имя листа совпадает с именем файла без
расширения. Листов семнадцать: Assumptions, Volume, Prices, Revenue, COGS, Headcount, Payroll, Opex,
Energy, Fleet, Stores, Capex, Depreciation, WorkingCapital, Debt, PnL, CashFlow.

Строка CSV с номером N — это строка N листа, столбцы идут по порядку A, B, C, … Строка 1 — заголовок.
Столбец A — код строки (уникален в пределах листа), столбец B — название статьи, столбец C — «Год»,
столбцы D–O — месяцы 2027 года с января по декабрь (D — январь, E — февраль, …, O — декабрь). Строки
без кода — заголовки разделов, в них ничего не считается.

Ячейка, начинающаяся со знака `=`, — формула в синтаксисе Excel; число — введённое значение. Формулы
вычисляет `tools/calc.py` (описание синтаксиса — в начале файла): поддерживаются ссылки вида `D7`,
`$C$4`, `D$4`, межлистовые ссылки `Лист!D7`, диапазоны, функции SUM, MIN, MAX, AVERAGE, ROUND, IF,
ABS. Пустая ячейка равна нулю.

В тексте методики статьи называются по кодам: запись `Volume:P03.UNITS` означает строку с кодом
`P03.UNITS` на листе Volume. Слова «того же месяца» означают тот же столбец D–O; «предыдущего
месяца» — столбец левее. «Допущение X» — значение из столбца C строки с кодом X на листе Assumptions
(для помесячных допущений — значение из столбца соответствующего месяца).

### 1.1. Где в книге допустимы числа

Числа (а не формулы) законно стоят только в следующих местах:

- на листе Assumptions — все допущения: разовые в столбце C, помесячные в столбцах D–O;
- на листе Headcount — строки приёма и увольнений `Dxx.HIRE`, `Dxx.LEAVE` (месяцы D–O);
- на листе Opex — строки `TRAVEL` и `OTHER` (месяцы D–O);
- нули, прямо предусмотренные методикой: онлайн-продажи сушек в январе–апреле, премии в месяцах
  без премии, амортизация нового оборудования и ИТ-техники в январе, уплата налога в месяцах без
  уплаты; на листе Energy — машино-часы линий в месяцы простоя (паспорта L03, L04, L11); на листе
  Fleet — страховка в месяцы без оплаты полиса, пробег и лизинг в месяцы, когда машины ещё или уже
  нет в работе или лизинг уже выплачен (паспорта V05, V10, V16); на листе Stores — выручка, аренда
  и продавцы магазинов в месяцы, когда магазин не работает (паспорта S03, S06, S14, S18).

Во всех остальных ячейках столбцов C–O (кроме заголовков разделов) должны стоять формулы. Число,
вписанное вместо формулы, — ошибка, даже если оно близко к правильному значению.

### 1.2. Округление

Где методика говорит «до копеек», используется `ROUND(…;2)` — в синтаксисе книги `ROUND(x,2)`; «до
рубля» — `ROUND(x,0)`; «до штук» — `ROUND(x,0)`; доли и маржи — `ROUND(x,4)`. Округляется только то,
что названо; промежуточные значения внутри формулы не округляются. Если про округление ничего не
сказано — не округлять.

### 1.3. Столбец «Год» (C)

- Потоки (штуки, выручка, расходы, начисления, премии, приём и увольнения, амортизация, проценты,
  денежные потоки, выборка и погашение кредитов) — сумма января–декабря: `=SUM(Dn:On)`.
- Остатки на конец месяца (численность на конец, остатки кредитов, нарастающие итоги, стоимость ОС,
  накопленная амортизация, оборотный капитал и его части, деньги на конец, налог к уплате) —
  значение декабря: `=On`.
- Остатки на начало месяца (численность на начало, остатки кредитов на начало, деньги на начало) —
  значение января: `=Dn`.
- Среднесписочная численность — среднее двенадцати месяцев: `=AVERAGE(Dn:On)`.
- Доли, маржи, средние цены и себестоимость единицы — та же формула, что и в месяцах, но от годовых
  значений столбца C соответствующих строк.
- Цены, ставки, оклад на человека, тренд — столбец «Год» не заполняется.

На листе Assumptions у помесячных допущений в столбце C стоит формула: у дней в месяце, коммунальных
платежей, закупок ОС, выборки и погашения кредитов — сумма за год, у индекса сезонности — среднее.

### 1.4. Знаки

На листах начислений (Revenue, COGS, Payroll, Opex, Energy, Fleet, Stores, Depreciation, Debt, PnL)
все суммы — положительные числа, вычитание делается в формулах итогов (результат магазина на листе
Stores — разность и может быть отрицательным). На листе CashFlow поступления денег положительны, выплаты
отрицательны: отток записывается со знаком минус прямо в строке статьи.

## 2. Как фиксировать найденные ошибки

**Ошибочная ячейка** — ячейка, собственная запись которой не соответствует методике настолько, что
даже при верных значениях всех ячеек, на которые она должна ссылаться, она даёт другое значение.
Ячейки, неверные лишь потому, что ошибка сидит выше по цепочке расчёта, ошибочными не считаются: их
не нужно ни править, ни включать в аудит. Формула, записанная иначе, но дающая то же значение
(`=D5+D6` вместо `=SUM(D5:D6)`, другой порядок множителей), — не ошибка.

Каждая ошибочная ячейка получает ровно один код — по тому, какое **минимальное исправление** делает
её верной:

| Код | Когда ставится |
|---|---|
| `RANGE` | неверный диапазон в SUM/MIN/MAX/AVERAGE: диапазон короче, длиннее или сдвинут (не те месяцы или не те строки); исправляются только границы диапазона |
| `ABSREL` | ошибка закрепления `$`: ссылка, которая должна быть закреплённой (разовое допущение в столбце C, фиксированная ячейка), при протягивании «уехала» на соседние столбцы; или ссылка закреплена `$` по столбцу там, где она должна идти за месяцем |
| `HARDCODE` | число вместо формулы, или число, вписанное в формулу вместо ссылки на допущение |
| `SHEET` | адрес ссылки верный, но лист не тот (в том числе лишнее или недостающее имя листа перед адресом); исправляется только имя листа |
| `SIGN` | неверный знак: плюс вместо минуса или наоборот, лишний или недостающий унарный минус |
| `PERIOD` | ссылка на верную строку верного листа, но на другой месяц, причём ни верная, ни ошибочная ссылка не закреплены по столбцу |
| `ROW` | ссылка на верный лист и месяц, но на другую строку — другую статью |
| `LOGIC` | всё остальное: лишний или недостающий множитель или слагаемое, не применённое или ошибочно применённое правило методики (исключение из паспорта, график премий, способ расчёта процентов) |

Если исправление подходит под одно из первых семи описаний, ставится этот код; `LOGIC` — только когда
не подходит ни одно из них. Исправление, которое требует поменять в одной ссылке сразу две вещи
(например, и лист, и строку) или заменить формулу числом 0, предусмотренным методикой, — это уже не
одно из первых семи описаний, а `LOGIC`. Замена ссылки на одно допущение ссылкой на другое допущение
(другая строка листа Assumptions) — `ROW`. Одна ошибка может затрагивать одну ячейку, несколько месяцев строки или
всю строку — в аудит вносится каждая ошибочная ячейка отдельно, включая столбец C, если ошибочна его
собственная формула.

Файл `audit.csv` в корне рабочего каталога: заголовок `sheet,cell,issue_code`, далее по строке на
каждую ошибочную ячейку: имя листа (как имя файла), адрес ячейки без `$` (например `H14`), код из
таблицы выше.

## 3. Лист Assumptions — допущения

Лист содержит только входные данные. Разовые допущения записаны в столбце C, помесячные — в
столбцах D–O. Коды допущений:

- `SEAS` — индекс сезонности спроса по месяцам (общий для всех продуктов); `DAYS` — число дней в
  месяце;
- `IDX.PRICE` — индексация прейскурантных цен с июля; `PROMO` — скидка ноябрьской акции;
  `ONL.MARK` — наценка онлайн-канала к прейскуранту; `RET.RATE` — доля возвратов розничной выручки;
  `IDX.MAT` — рост цен сырья с апреля; `PACK` — стоимость упаковки одной единицы продукции;
- по каждому продукту `Pxx`: `Pxx.BASE` — трендовый объём января, `Pxx.GROWTH` — месячный рост
  тренда, `Pxx.RETSH` — доля розничного канала, `Pxx.ONLSH` — доля онлайн-канала, `Pxx.PRICE` —
  прейскурантная цена первого полугодия, `Pxx.WHDISC` — оптовая скидка, `Pxx.UC` — сырьё на единицу
  в январе–марте;
- `SAL.IDX` — индексация окладов с октября; `BONUS` — квартальная премия (доля окладов квартала);
  `BONUS.PROD` — ежемесячная премия производства; `PAYTAX` — ставка страховых взносов; по каждому
  подразделению `Dxx.HC0` — численность на 1 января и `Dxx.SAL` — оклад на человека в месяц;
- `RENT` — аренда в месяц до августа включительно; `RENT.NEW` — аренда по новому договору, действует
  с сентября; `UTIL` — коммунальные платежи по месяцам; `MKT` — маркетинговый бюджет как доля чистой
  выручки; `LOGI` — стоимость доставки одной оптовой единицы; `COMM` — комиссия маркетплейса как
  доля онлайн-выручки; `IT` — ИТ-сопровождение в месяц;
- `DSO`, `DIO`, `DPO` — оборачиваемость дебиторской задолженности, запасов и кредиторской
  задолженности по сырью в днях; `AR0`, `INV0`, `AP0` — их остатки на 1 января;
- `FA0` — первоначальная стоимость основных средств на 1 января, `ACC0` — накопленная амортизация на
  1 января; `LIFE.EQ`, `LIFE.IT` — сроки службы оборудования и ИТ-техники в годах; `CAPEX.EQ`,
  `CAPEX.IT` — помесячные закупки;
- `LOANA0`, `RATE.A`, `A.DRAW`, `A.REPAY` — кредит А: остаток на 1 января, годовая ставка, выборка и
  погашение по месяцам; `LOANB0`, `RATE.B`, `B.DRAW`, `B.REPAY` — то же для кредитной линии Б;
- `CASH0` — деньги на 1 января; `TAX` — ставка налога на прибыль; `TAXDUE0` — налог на прибыль к
  уплате на 1 января;
- энергоресурсы: `WDAYS` — рабочие дни месяца (помесячно); `HOURS.SHIFT` — часов в смене;
  `SHIFTS.MAX` — смен в сутки при круглосуточной работе; `ELEC.T`, `ELEC.IDX` — тариф на
  электроэнергию и его индексация с июля; `GAS.T`, `GAS.IDX` — то же для газа; по каждой линии
  `Lxx.SHIFTS` — смен в сутки и `Lxx.POWER` — мощность в кВт (у газовых линий — расход газа в м³/ч);
- автопарк: `FUEL.P`, `FUEL.IDX` — цена литра дизельного топлива и её рост с мая; `MAINT.KM` —
  обслуживание в рублях за километр; `RESERVE.K` — коэффициент пробега резервной машины; по каждой
  машине `Vxx.KM` — базовый пробег в месяц, `Vxx.CONS` — расход на 100 км, `Vxx.LEASE` — лизинговый
  платёж в месяц, `Vxx.INS` — страховка за год; `V04.SVC` — сервисный контракт рефрижератора;
- магазины: `STORE.WAGE` — оплата продавца в месяц; `STORE.IDX` — индексация аренды магазинов с
  апреля; `PART.K` — доля ставки продавцов на полставки; `S02.TURN` — ставка аренды с оборота;
  `S11.TURN` — ставка аренды с оборота в аэропорту; `S15.PREM` — надбавка продавцам-бариста;
  `EUR` — курс евро по месяцам; по каждому магазину `Sxx.SH` — доля розничной выручки,
  `Sxx.RENT` — аренда в месяц, `Sxx.STAFF` — число продавцов.

Ошибки в самих допущениях не ищутся: значения допущений утверждены и меняться не должны.

## 4. Продукты

Модель ведёт двадцать два продукта:

| Код | Продукт |
|---|---|
{prod_list}

На листах Volume, Prices, Revenue и COGS каждый продукт занимает блок из шести строк: строка-заголовок
с названием продукта и пять строк с кодами `Pxx.…`. Блоки идут в порядке кодов продуктов, поэтому
строки с одинаковой позицией в блоке на этих четырёх листах имеют одинаковые номера.

### 4.1. Паспорта продуктов

{prod_notes}

## 5. Лист Volume — объёмы продаж в штуках

Блок продукта `Pxx`:

- `Pxx.TREND` — трендовый объём. В январе равен допущению `Pxx.BASE`; в каждом следующем месяце —
  тренд предыдущего месяца, умноженный на (1 + `Pxx.GROWTH`). Тренд не округляется. «Год» не
  заполняется.
- `Pxx.UNITS` — продажи в штуках: тренд того же месяца, умноженный на индекс сезонности этого месяца
  `SEAS`, с округлением до штук. Год — сумма.
- `Pxx.RET` — розничные продажи: продажи того же месяца × доля розницы `Pxx.RETSH`, до штук. Год —
  сумма.
- `Pxx.ONL` — онлайн-продажи: продажи того же месяца × доля онлайна `Pxx.ONLSH`, до штук (исключение
  для сушек — в паспорте P07). Год — сумма.
- `Pxx.WH` — оптовые продажи: остаток — продажи минус розница минус онлайн того же месяца. Год —
  сумма.

Итоги: `TOT.UNITS`, `TOT.RET`, `TOT.ONL`, `TOT.WH` — суммы соответствующих строк всех двадцати
двух продуктов за тот же месяц; год — сумма.

## 6. Лист Prices — цены, руб. за штуку

Блок продукта `Pxx`:

- `Pxx.LIST` — прейскурантная цена. В январе–июне равна допущению `Pxx.PRICE`; с июля по декабрь —
  `Pxx.PRICE` × (1 + `IDX.PRICE`) с округлением до копеек (исключение — P10, см. паспорт). Индексация
  считается от допущения, а не от цены предыдущего месяца.
- `Pxx.AVGP` — средняя цена реализации: выручка по продукту `Revenue:Pxx.TOT` того же месяца,
  делённая на продажи `Volume:Pxx.UNITS`, до копеек; при нулевых продажах — 0. В столбце «Год» — то
  же от годовых значений.
- `Pxx.RETP` — розничная цена: равна прейскурантной того же месяца (исключение — ноябрьская акция,
  см. паспорта P03 и P04).
- `Pxx.ONLP` — онлайн-цена: прейскурант того же месяца × (1 + `ONL.MARK`), до копеек.
- `Pxx.WHP` — оптовая цена: прейскурант того же месяца × (1 − `Pxx.WHDISC`), до копеек. Оптовая
  скидка у каждого продукта своя.

Столбец «Год» заполняется только у `Pxx.AVGP`.

## 7. Листы Revenue и COGS — выручка и себестоимость

### 7.1. Revenue

Блок продукта `Pxx`:

- `Pxx.TOT` — выручка по продукту: розничная + онлайн + оптовая выручка того же месяца.
- `Pxx.SHARE` — доля продукта в валовой выручке: `Pxx.TOT` / `GROSS` того же месяца, округление
  `ROUND(…,4)`, при нулевой валовой выручке 0; в столбце «Год» — от годовых значений.
- `Pxx.RET` — розничная выручка: `Volume:Pxx.RET` × `Prices:Pxx.RETP` того же месяца.
- `Pxx.ONL` — онлайн-выручка: `Volume:Pxx.ONL` × `Prices:Pxx.ONLP`.
- `Pxx.WH` — оптовая выручка: `Volume:Pxx.WH` × `Prices:Pxx.WHP`.

Выручка не округляется. Итоги: `GROSS.RET`, `GROSS.ONL`, `GROSS.WH` — суммы соответствующих строк
всех продуктов; `GROSS` — валовая выручка, сумма трёх канальных итогов; `RETURNS` — возвраты: только
розничная выручка `GROSS.RET` × `RET.RATE`, до копеек (онлайн и опт возвратов не имеют); `NET` —
чистая выручка: `GROSS` минус `RETURNS`. У всех строк, кроме долей, год — сумма.

### 7.2. COGS

Блок продукта `Pxx`:

- `Pxx.UC` — сырьё на единицу. В январе–марте равно допущению `Pxx.UC`; с апреля по декабрь —
  `Pxx.UC` × (1 + `IDX.MAT`) до копеек (исключение — P05, см. паспорт). «Год» не заполняется.
- `Pxx.MAT` — сырьё: продажи `Volume:Pxx.UNITS` того же месяца × сырьё на единицу `Pxx.UC`, без
  округления.
- `Pxx.PACK` — упаковка: продажи `Volume:Pxx.UNITS` × `PACK`, до копеек.
- `Pxx.COST` — себестоимость продукта: сырьё + упаковка.
- `Pxx.UCOGS` — себестоимость единицы: `Pxx.COST` / `Volume:Pxx.UNITS`, до копеек, при нулевых
  продажах 0; «Год» — от годовых значений.

Итоги: `MAT.TOT` и `PACK.TOT` — суммы по всем продуктам; `COGS.TOT` — `MAT.TOT` + `PACK.TOT`; `GP` —
валовая прибыль: `Revenue:NET` минус `COGS.TOT`; `GM` — валовая маржа `GP` / `Revenue:NET`,
`ROUND(…,4)`, при нулевой выручке 0 (в «Годе» — от годовых значений).

## 8. Листы Headcount и Payroll — персонал

Подразделения:

| Код | Подразделение |
|---|---|
{dept_list}

На листах Headcount и Payroll каждое подразделение занимает блок из шести строк (заголовок и пять
строк с кодами), блоки идут в порядке кодов, поэтому номера строк блоков на двух листах совпадают.

### 8.1. Паспорта подразделений

{dept_notes}

### 8.2. Headcount

- `Dxx.OPEN` — численность на начало месяца: в январе — допущение `Dxx.HC0`, далее — численность на
  конец предыдущего месяца. Год — январь.
- `Dxx.HIRE`, `Dxx.LEAVE` — приём и увольнения, вводятся числами. Год — сумма.
- `Dxx.CLOSE` — численность на конец месяца: на начало + приём − увольнения. Год — декабрь.
- `Dxx.AVG` — среднесписочная численность: полусумма численности на начало и на конец месяца (без
  округления). Год — среднее.

Итоги `TOT.OPEN`, `TOT.CLOSE`, `TOT.AVG` — суммы по всем подразделениям; год — январь, декабрь и
среднее соответственно.

### 8.3. Payroll

- `Dxx.RATE` — оклад на человека: в январе–сентябре — допущение `Dxx.SAL`; с октября — `Dxx.SAL` ×
  (1 + `SAL.IDX`), до рубля (исключение — D07). «Год» не заполняется.
- `Dxx.SAL` — оклады: среднесписочная численность `Headcount:Dxx.AVG` того же месяца × оклад на
  человека `Dxx.RATE` того же месяца, до копеек.
- `Dxx.BONUS` — премии. Общее правило — квартальная премия: в марте, июне, сентябре и декабре
  начисляется `BONUS` × сумма окладов `Dxx.SAL` трёх месяцев квартала (январь–март, апрель–июнь,
  июль–сентябрь, октябрь–декабрь), до копеек; в остальных месяцах 0. Исключения — D01 и D07.
- `Dxx.TAX` — страховые взносы: (оклады + премии того же месяца) × `PAYTAX`, до копеек.
- `Dxx.TOTAL` — расходы на персонал подразделения: оклады + премии + взносы.

Итоги: `SAL.TOT`, `BONUS.TOT`, `TAX.TOT` — суммы по всем подразделениям; `PAY.TOT` — их сумма. У всех
строк, кроме `Dxx.RATE`, год — сумма.

## 9. Лист Opex — операционные расходы

- `RENT` — аренда: январь–август — допущение `RENT`, сентябрь–декабрь — `RENT.NEW`.
- `UTIL` — коммунальные платежи: помесячное допущение `UTIL` того же месяца.
- `MKT` — маркетинг: чистая выручка `Revenue:NET` того же месяца × `MKT`, до копеек. Бюджет
  маркетинга привязан именно к чистой выручке (после возвратов), а не к валовой.
- `LOGI` — доставка опта: оптовые продажи в штуках `Volume:TOT.WH` × `LOGI`, до копеек.
- `COMM` — комиссия маркетплейса: онлайн-выручка `Revenue:GROSS.ONL` того же месяца × `COMM`, до
  копеек.
- `IT` — ИТ-сопровождение: допущение `IT` каждый месяц.
- `ENERGY` — энергоресурсы производственных линий: `Energy:COST.TOT` того же месяца.
- `FLEET` — собственный автопарк: `Fleet:TOT` того же месяца (все расходы на машины, а не только
  топливо).
- `STORES` — расходы фирменных магазинов: `Stores:COST.TOT` того же месяца (аренда и продавцы;
  выручка магазинов уже входит в розничную выручку и здесь не участвует).
- `TRAVEL`, `OTHER` — командировки и прочие расходы, вводятся числами.
- `EXPAY` — итого расходы, кроме персонала: сумма строк от `RENT` до `OTHER` включительно.
- `PAY.Dxx` — расходы на персонал подразделения: `Payroll:Dxx.TOTAL` того же месяца.
- `PAY.TOT` — итого персонал: сумма строк `PAY.Dxx` всех четырнадцати подразделений.
- `OPEX.TOT` — операционные расходы всего: `EXPAY` + `PAY.TOT`.

Год у всех строк — сумма.

### 9.1. Лист Energy — энергоресурсы производственных линий

Коммунальные платежи `UTIL` — это отопление, вода и освещение зданий; электроэнергия и газ,
которые потребляют производственные линии, считаются отдельно на листе Energy. Каждая линия
`Lxx` занимает блок из пяти строк (заголовок и четыре строки с кодами):

- `Lxx.HOURS` — машино-часы: рабочие дни месяца `WDAYS` × смен в сутки `Lxx.SHIFTS` × часов в
  смене `HOURS.SHIFT`, без округления. Год — сумма.
- `Lxx.CONS` — потребление: машино-часы того же месяца × `Lxx.POWER` (у электрических линий — кВт·ч,
  у газовых — м³). Год — сумма.
- `Lxx.TARIFF` — тариф: у электрических линий в январе–июне — `ELEC.T`, с июля —
  `ELEC.T` × (1 + `ELEC.IDX`) до копеек; у газовых — так же от `GAS.T` и `GAS.IDX`. Индексация
  считается от допущения. «Год» не заполняется.
- `Lxx.COST` — стоимость: потребление × тариф того же месяца, до копеек. Год — сумма.

Итоги: `KWH.TOT` — электроэнергия всего, сумма `Lxx.CONS` только электрических линий; `M3.TOT` —
газ всего, сумма `Lxx.CONS` газовых линий; `COST.TOT` — сумма `Lxx.COST` всех двенадцати линий. Год
у итогов — сумма. Газовые линии — L07, L08 и L10, остальные девять — электрические.

#### 9.1.1. Паспорта линий

{line_notes}

### 9.2. Лист Fleet — собственный автопарк

Строка `FUEL.PRICE` в начале листа — цена литра топлива: в январе–апреле `FUEL.P`, с мая —
`FUEL.P` × (1 + `FUEL.IDX`) до копеек; «Год» не заполняется. Каждая машина `Vxx` занимает блок из
семи строк (заголовок и шесть строк с кодами):

- `Vxx.KM` — пробег: базовый пробег `Vxx.KM` × индекс сезонности `SEAS` того же месяца, до
  километров (`ROUND(…,0)`) — развозка следует за продажами. Год — сумма.
- `Vxx.FUEL` — топливо: пробег того же месяца × `Vxx.CONS` / 100 × цена топлива `FUEL.PRICE` того
  же месяца, до копеек. Год — сумма.
- `Vxx.MAINT` — обслуживание и ремонт: пробег × `MAINT.KM`, до копеек. Год — сумма.
- `Vxx.LEASE` — лизинг: допущение `Vxx.LEASE` каждый месяц (у собственных машин допущение равно
  нулю, формула та же). Год — сумма.
- `Vxx.INS` — страхование: годовой полис оплачивается один раз, в январе, — в январе `Vxx.INS`, в
  остальные месяцы 0. Год — сумма.
- `Vxx.TOTAL` — расходы на машину: сумма строк от `Vxx.FUEL` до `Vxx.INS`. Год — сумма.

Итоги: `KM.TOT`, `FUEL.TOT`, `TOT` — суммы строк `Vxx.KM`, `Vxx.FUEL` и `Vxx.TOTAL` всех
двадцати машин; год — сумма.

#### 9.2.1. Паспорта машин

{van_notes}

### 9.3. Лист Stores — фирменные магазины

Фирменные магазины — часть розничного канала: их выручка входит в `Revenue:GROSS.RET` и отдельно не
прибавляется. Лист Stores показывает вклад каждого магазина. Сумма долей `Sxx.SH` меньше единицы:
остальная розница идёт через партнёрские торговые точки. Блок магазина `Sxx` — заголовок и пять
строк:

- `Sxx.REV` — выручка магазина: `Revenue:GROSS.RET` того же месяца × `Sxx.SH`, до копеек.
- `Sxx.RENT` — аренда: в январе–марте допущение `Sxx.RENT`, с апреля — `Sxx.RENT` × (1 +
  `STORE.IDX`) до копеек (исключения — в паспортах магазинов).
- `Sxx.STAFF` — продавцы с взносами: `Sxx.STAFF` × `STORE.WAGE` × (1 + `PAYTAX`), до копеек.
- `Sxx.COST` — расходы магазина: аренда + продавцы.
- `Sxx.RESULT` — результат магазина: выручка магазина × валовая маржа `COGS:GM` того же месяца, до
  копеек, минус расходы магазина.

Итоги: `REV.TOT`, `COST.TOT`, `RESULT.TOT` — суммы по всем магазинам. Год у всех строк — сумма.

#### 9.3.1. Паспорта магазинов

{store_notes}

## 10. Листы Capex и Depreciation — основные средства

### 10.1. Capex

- `EQ`, `IT` — закупки оборудования и ИТ-техники: помесячные допущения `CAPEX.EQ`, `CAPEX.IT` того же
  месяца. Год — сумма.
- `TOT` — капвложения всего: `EQ` + `IT`. Год — сумма.
- `EQ.CUM`, `IT.CUM` — закупки нарастающим итогом с начала года: в январе — закупки января, далее —
  нарастающий итог предыдущего месяца плюс закупки текущего. Год — декабрь.

### 10.2. Depreciation

Амортизация линейная, помесячная. Новое оборудование и техника начинают амортизироваться со
следующего месяца после покупки: в месяце покупки амортизации по ним ещё нет.

- `EQ.EXIST` — амортизация основных средств, числившихся на 1 января: `FA0` / (`LIFE.EQ` × 12), до
  копеек, одинаковая во всех месяцах (весь парк на 1 января — оборудование).
- `EQ.NEW` — амортизация оборудования, купленного в 2027 году: в январе 0; с февраля — нарастающий
  итог закупок оборудования `Capex:EQ.CUM` на конец **предыдущего** месяца / (`LIFE.EQ` × 12), до
  копеек.
- `IT.NEW` — то же для ИТ-техники: `Capex:IT.CUM` предыдущего месяца / (`LIFE.IT` × 12), до копеек;
  в январе 0.
- `TOT` — амортизация всего: сумма трёх строк выше.
- `GROSS` — первоначальная стоимость ОС на конец месяца: `FA0` + `Capex:EQ.CUM` + `Capex:IT.CUM` того
  же месяца.
- `ACC` — накопленная амортизация: в январе `ACC0` + амортизация января, далее — накопленная
  амортизация предыдущего месяца + амортизация текущего.
- `NET` — остаточная стоимость: `GROSS` − `ACC`.

Год: у `EQ.EXIST`, `EQ.NEW`, `IT.NEW`, `TOT` — сумма; у `GROSS`, `ACC`, `NET` — декабрь.

## 11. Лист WorkingCapital — оборотный капитал

Остатки на конец месяца считаются через оборачиваемость и число дней месяца `DAYS`:

- `AR` — дебиторская задолженность: чистая выручка `Revenue:NET` того же месяца × `DSO` / дней в
  месяце, до копеек.
- `INV` — запасы: себестоимость продаж `COGS:COGS.TOT` × `DIO` / дней в месяце, до копеек.
- `AP` — кредиторская задолженность: поставщики у нас только сырьевые, поэтому база — сырьё
  `COGS:MAT.TOT` (без упаковки) × `DPO` / дней в месяце, до копеек.
- `NWC` — оборотный капитал: `AR` + `INV` − `AP`.
- `DNWC` — прирост оборотного капитала за месяц: `NWC` текущего месяца минус `NWC` предыдущего; в
  январе — минус оборотный капитал на 1 января (`AR0` + `INV0` − `AP0`).

Год: у `AR`, `INV`, `AP`, `NWC` — декабрь; у `DNWC` — сумма.

## 12. Лист Debt — кредиты

Кредит А и кредитная линия Б ведутся одинаковыми блоками:

- `A.OPEN` / `B.OPEN` — остаток на начало месяца: в январе — `LOANA0` / `LOANB0`, далее — остаток на
  конец предыдущего месяца. Год — январь.
- `A.DRAW`, `A.REPAY` / `B.DRAW`, `B.REPAY` — выборка и погашение: помесячные допущения того же
  месяца. Год — сумма.
- `A.CLOSE` / `B.CLOSE` — остаток на конец: на начало + выборка − погашение. Год — декабрь.
- `A.INT` — проценты по кредиту А начисляются на остаток на начало месяца: `A.OPEN` × `RATE.A` / 12,
  до копеек.
- `B.INT` — по кредитной линии Б, которая выбирается и гасится траншами в течение месяца, проценты
  начисляются на средний остаток: (`B.OPEN` + `B.CLOSE`) / 2 × `RATE.B` / 12, до копеек. Ставки у
  кредитов разные.

Итоги: `INT.TOT` — проценты всего (`A.INT` + `B.INT`, год — сумма); `DEBT.TOT` — долг на конец месяца
(`A.CLOSE` + `B.CLOSE`, год — декабрь).

## 13. Лист PnL — отчёт о прибылях и убытках

- `REV` — чистая выручка `Revenue:NET`; `COGS` — себестоимость `COGS:COGS.TOT`; `GP` — `REV` −
  `COGS`; `GM` — `GP` / `REV`, `ROUND(…,4)`, при нулевой выручке 0.
- `PAY` — расходы на персонал `Opex:PAY.TOT`; `OTH` — прочие операционные расходы `Opex:EXPAY`.
- `EBITDA` — `GP` − `PAY` − `OTH`; `EBITDAM` — `EBITDA` / `REV`, `ROUND(…,4)`, при нулевой выручке 0.
- `DEP` — амортизация `Depreciation:TOT`; `EBIT` — `EBITDA` − `DEP`.
- `INT` — проценты `Debt:INT.TOT`; `EBT` — прибыль до налога: `EBIT` − `INT`.
- `TAX` — налог на прибыль начисляется помесячно: ставка `TAX` × прибыль до налога месяца, если она
  положительна, иначе 0 (убыток не переносится); до копеек.
- `NI` — чистая прибыль: `EBT` − `TAX`; `NIM` — `NI` / `REV`, `ROUND(…,4)`, при нулевой выручке 0.

Год: у долей (`GM`, `EBITDAM`, `NIM`) — формула от годовых значений; у остальных — сумма (в том числе
у `TAX`: годовой налог — сумма помесячных начислений).

## 14. Лист CashFlow — движение денежных средств

Операционная деятельность:

- `NI` — чистая прибыль `PnL:NI`; `DEP` — амортизация `PnL:DEP` (прибавляется: это неденежный
  расход).
- `DNWC` — изменение оборотного капитала: прирост оборотного капитала `WorkingCapital:DNWC` со знаком
  минус (рост оборотного капитала забирает деньги).
- `TAXACR` — начисленный налог `PnL:TAX` (прибавляется обратно, потому что уже вычтен в чистой
  прибыли).
- `TAXPAID` — уплаченный налог, со знаком минус: в январе уплачивается налог к уплате на 1 января
  `TAXDUE0`; в апреле — сумма начисленного налога `PnL:TAX` за январь–март; в июле — за апрель–июнь;
  в октябре — за июль–сентябрь; налог IV квартала уплачивается уже в 2028 году. В остальные месяцы 0.
- `CFO` — поток от операционной деятельности: сумма строк от `NI` до `TAXPAID` включительно.

Инвестиционная деятельность: `CAPEX` — капвложения `Capex:TOT` со знаком минус; `CFI` — равен
`CAPEX`.

Финансовая деятельность: `DRAW` — выборка кредитов А и Б (`Debt:A.DRAW` + `Debt:B.DRAW`), со знаком
плюс; `REPAY` — погашение (`Debt:A.REPAY` + `Debt:B.REPAY`) со знаком минус; `CFF` — `DRAW` +
`REPAY`.

Денежные средства: `NET` — `CFO` + `CFI` + `CFF`; `OPEN` — деньги на начало месяца: в январе `CASH0`,
далее — деньги на конец предыдущего месяца; `CLOSE` — `OPEN` + `NET`. `TAXBAL` — налог к уплате на
конец месяца: в январе `TAXDUE0` + начисленный − уплаченный налог (уплата уже отрицательна, поэтому
в формуле складываются `TAXACR` и `TAXPAID`), далее — остаток предыдущего месяца + `TAXACR` +
`TAXPAID` текущего.

Год: у `OPEN` — январь; у `CLOSE` и `TAXBAL` — декабрь; у остальных — сумма.

## 15. Контрольные соотношения

Правильно посчитанная модель выполняет соотношения ниже. Они помогают искать ошибки, но не заменяют
разделы 3–14: соотношение может выполняться и при ошибочной формуле (например, если ошибка одинаково
вошла в обе части), а методика задаёт формулу каждой строки.

1. На листе Volume для каждого продукта и месяца розница + онлайн + опт = продажи в штуках.
2. На листе Revenue валовая выручка `GROSS` равна сумме строк `Pxx.TOT` всех продуктов, а сумма
   долей `Pxx.SHARE` за месяц близка к единице (с точностью до округления долей).
3. `COGS:GP` за каждый месяц совпадает с `PnL:GP`.
4. Сумма строк `PAY.Dxx` листа Opex равна `Payroll:PAY.TOT`.
5. `Depreciation:ACC` декабря = `ACC0` + годовая амортизация `Depreciation:TOT`.
6. `WorkingCapital:DNWC` за год = `NWC` декабря − (`AR0` + `INV0` − `AP0`).
7. `Debt:A.CLOSE` декабря = `LOANA0` + годовая выборка − годовое погашение кредита А; то же для Б.
8. `CashFlow:CLOSE` декабря = `CASH0` + годовой `CashFlow:NET`.
9. `CashFlow:TAXBAL` декабря = `TAXDUE0` + годовой начисленный налог + годовой уплаченный налог
   (уплата отрицательна) и равен начисленному налогу октября–декабря.
10. Численность на начало каждого месяца, кроме января, равна численности на конец предыдущего.
11. `Opex:ENERGY`, `Opex:FLEET` и `Opex:STORES` за каждый месяц совпадают с `Energy:COST.TOT`,
    `Fleet:TOT` и `Stores:COST.TOT`.
12. `Fleet:TOT` за месяц равен сумме `Fleet:FUEL.TOT` и строк обслуживания, лизинга и страховки всех
    машин.

## 16. История методики (для справки)

Модель ведётся с 2024 года, и часть правил за это время менялась. Ниже — изменения, о которых стоит
знать, чтобы не принять старую логику, оставшуюся в чьих-то привычках или в старых файлах, за
действующую. **Действуют только правила разделов 1–14**; история приведена, чтобы объяснить, откуда в
книге могли взяться устаревшие формулы.

- Версия 2024.1. Маркетинговый бюджет считался как доля валовой выручки. С версии 2025.2 база —
  чистая выручка после возвратов (раздел 9); ставка `MKT` при этом не менялась.
- Версия 2024.1. Возвраты считались от всей валовой выручки. С 2025 года онлайн-заказы оплачиваются
  при получении и возвратов не дают, а опт возвращает товар по отдельным актам вне модели; поэтому
  возвраты — только от розничной выручки (раздел 7.1).
- Версия 2024.2. Проценты по кредитной линии Б считались, как по кредиту А, от остатка на начало
  месяца. Банк перешёл на начисление по среднему остатку в 2025 году — теперь действует правило
  раздела 12.
- Версия 2025.1. Страховые взносы закладывались по округлённой ставке. С 2026 года ставка берётся
  только из допущения `PAYTAX`; вписывать значение допущения числом в формулы нельзя ни для какого
  допущения.
- Версия 2025.1. Налог на прибыль считался по другой ставке. Действующее значение — допущение `TAX`,
  и в формулах ставка налога берётся только оттуда.
- Версия 2025.2. Кредиторская задолженность считалась от полной себестоимости. Поставщики упаковки
  перешли на предоплату, поэтому база кредиторки — только сырьё (раздел 11).
- Версия 2025.2. Амортизация нового оборудования начиналась в месяце покупки. По учётной политике с
  2026 года — со следующего месяца (раздел 10.2).
- Версия 2026.1. Ежемесячную премию получали и производство, и логистика; с 2026 года логистика
  переведена на квартальную премию, ежемесячная осталась только у производства.
- Версия 2026.1. Администрация получала квартальную премию. С 2026 года — фиксированные контракты
  без премий и без индексации окладов (паспорт D07).
- Версия 2026.2. Индексация цен проводилась с апреля. В бюджете-2027 индексация цен — с июля, а с
  апреля растут только цены сырья (разделы 6 и 7.2).
- Версия 2026.2. Ноябрьская акция распространялась на багет, круассан и кекс. В 2027 году — только
  на багет и круассан (паспорта P03, P04, P12).
- Версия 2026.1. Тариф на газ в модели индексировался на 9 % по прогнозу регулятора. Действующее
  значение индексации — только допущение `GAS.IDX`.
- Версия 2026.2. Итог `KWH.TOT` включал газовые линии, пересчитанные в «условные кВт·ч». С 2027 года
  газ учитывается отдельно, в `M3.TOT`.
- Версия 2026.2. Аренда магазинов индексировалась с марта, а киоск в «Меридиане» платил фиксированную
  ставку с общей индексацией. В бюджете-2027 индексация — с апреля, киоск платит с оборота
  (паспорт S02), корнер в «Прайме» — в евро без индексации (паспорт S10).
- Версия 2026.2. Страховка всех машин оплачивалась в январе, а электрофургон считали по цене
  дизельного топлива, как остальные машины. Теперь действуют паспорта V03, V09 и V10.
- Версия 2027.0 (действующая). Добавлены продукты P13–P22 и подразделения D11–D14, листы Energy,
  Fleet и Stores, онлайн-канал для сушек открывается в мае, аренда по новому договору — с сентября.
"""


# --------------------------------------------------------------------------
# Task: setup / gold / check
# --------------------------------------------------------------------------

PROMPT = """\
В каталоге `model/` лежит бюджетная модель ООО «Озёрная хлебная мануфактура» на 2027 год: 17 листов,
каждый — отдельный CSV-файл, в ячейках формулы в стиле Excel (`=B7*(1+Assumptions!$C$4)`) и
введённые числа. Как должна считаться каждая статья, описано в `model_spec.md` — это единственный
источник истины о методике. `tools/calc.py` — вычислитель формул книги (синтаксис и запуск описаны
в начале файла), им можно пользоваться для проверки.

При сборке модели в формулы попали ошибки: неверные диапазоны, съехавшие ссылки, числа вместо
формул, ссылки не на тот лист, строку или месяц, ошибки знака, неприменённые правила методики.

Что нужно сделать:

1. Исправить все ошибочные ячейки прямо в `model/*.csv`, чтобы каждая ячейка считалась по методике.
   Структуру книги не менять: те же листы, строки, столбцы и коды строк, допущения и другие
   введённые числа остаются как есть; там, где по методике должна быть формула, должна остаться
   формула (не вписывайте вычисленные числа). Верные ячейки можно не трогать.
2. Создать в корне рабочего каталога `audit.csv` с заголовком `sheet,cell,issue_code` — по строке на
   каждую ошибочную ячейку: имя листа, адрес без `$` (например `H14`) и код ошибки. Что считается
   ошибочной ячейкой и как выбирается код, сказано в разделе 2 методики.

Методика длинная и написана свободным текстом; исключения из общих правил разбросаны по паспортам
продуктов, подразделений, производственных линий, машин и магазинов, и каждая оговорка важна.
"""


def _setup(ws: Path) -> None:
    data = build()
    for sheet in SHEETS:
        write(ws, f"model/{sheet}.csv", _grid_to_csv(data["broken"][sheet]))
    write(ws, "model_spec.md", data["spec"])
    write(ws, "tools/calc.py", CALC_SRC, executable=True)


def _write_model(ws: Path, grids) -> None:
    for sheet in SHEETS:
        write(ws, f"model/{sheet}.csv", _grid_to_csv(grids[sheet]))


def _write_audit(ws: Path, rows) -> None:
    write_csv(ws, "audit.csv", ["sheet", "cell", "issue_code"], rows)


def _gold(ws: Path) -> None:
    data = build()
    _write_model(ws, data["gold"])
    _write_audit(ws, data["audit"])


def _num_close(a, b) -> bool:
    if not isinstance(a, float) or not isinstance(b, float):
        return False
    return abs(a - b) <= max(0.01, 1e-9 * abs(b))


def _check(ws: Path) -> str:
    data = build()
    gold, gv = data["gold"], data["gold_values"]
    for sheet in SHEETS:
        assert (ws / "model" / f"{sheet}.csv").is_file(), f"нет листа model/{sheet}.csv"
    book = CALC["Book"].from_dir(ws / "model")
    values = book.evaluate()
    total = ok = 0
    errors = []
    for sheet in SHEETS:
        agent = book.cells.get(sheet, {})
        for (c, r), raw in gold[sheet].items():
            if c < 3 or r == 1:
                continue
            total += 1
            got = agent.get((c, r), "")
            if raw.startswith("=") and not got.startswith("="):
                errors.append(f"{sheet}!{_addr(c, r)}: нужна формула")
                continue
            v = values.get((sheet, c, r), 0.0)
            if _num_close(v, gv[(sheet, c, r)]):
                ok += 1
            else:
                errors.append(f"{sheet}!{_addr(c, r)}: значение не по методике")
    bad_keys = [k for k in data["key_cells"] if not _num_close(values.get(k, 0.0), gv[k])]
    assert not bad_keys, f"ключевые ячейки считаются неверно: {len(bad_keys)} из {len(data['key_cells'])}"
    note = require_share(total, ok, min_share=0.99, what="значения ячеек", errors=errors)
    rows = read_csv(ws, "audit.csv", required=("sheet", "cell", "issue_code"))
    canon = {s.casefold(): s for s in SHEETS}
    got = set()
    for row in rows:
        sheet = canon.get(row["sheet"].strip().casefold(), row["sheet"].strip())
        cell = row["cell"].replace("$", "").strip().upper()
        got.add((sheet, cell, row["issue_code"].strip().upper()))
    expected = set(data["audit"])
    p, rc = precision_recall(expected, got)
    assert p >= 0.9 and rc >= 0.9, f"audit.csv: precision {p:.2f}, recall {rc:.2f}, нужно не меньше 0.90"
    return f"{note}; audit precision {p:.2f}, recall {rc:.2f}"


# --------------------------------------------------------------------------
# Near misses
# --------------------------------------------------------------------------

_REFX = re.compile(r"(?:([A-Za-z_][A-Za-z0-9_]*)!)?(\$?)([A-Z]{1,2})(\$?)(\d+)")


def _to_rel(raw: str, col: int) -> str:
    def sub(m):
        sheet, dc, c, dr, r = m.groups()
        cc = CALC["col_index"](c)
        part = f"C{cc}" if dc else f"C[{cc - col}]"
        return f"{sheet + '!' if sheet else ''}{dr}R{r}{part}"
    return _REFX.sub(sub, raw)


def _from_rel(pattern: str, col: int) -> str:
    def sub(m):
        sheet, dr, r, abs_c, off = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        c = int(abs_c) if abs_c else col + int(off)
        dc = "$" if abs_c else ""
        return f"{sheet + '!' if sheet else ''}{dc}{CALC['col_name'](max(c, 1))}{dr}{r}"
    return re.sub(r"(?:([A-Za-z_][A-Za-z0-9_]*)!)?(\$?)R(\d+)C(?:(\d+)|\[(-?\d+)\])", sub, pattern)


def _nm_majority_pattern(ws: Path) -> None:
    """Naive: set every month cell to the row's majority relative pattern; code everything LOGIC."""
    data = build()
    grids = {s: dict(g) for s, g in data["broken"].items()}
    audit = []
    for sheet, grid in grids.items():
        rows = sorted({r for _, r in grid if r > 1})
        for r in rows:
            cells = [(c, grid.get((c, r), "")) for c in range(4, 16)]
            pats = [_to_rel(raw, c) if raw.startswith("=") else raw for c, raw in cells]
            if not any(p.startswith("=") for p in pats):
                continue
            best = max(sorted(set(pats)), key=pats.count)
            for (c, _raw), p in zip(cells, pats, strict=True):
                if p != best:
                    grid[(c, r)] = _from_rel(best, c) if best.startswith("=") else best
                    audit.append((sheet, _addr(c, r), "LOGIC"))
    _write_model(ws, grids)
    _write_audit(ws, audit)


def _nm_ignore_passports(ws: Path) -> None:
    """Fixes everything except the exceptions from product/department passports (LOGIC)."""
    data = build()
    grids = {s: dict(g) for s, g in data["gold"].items()}
    skip = set()
    for sheet, _code, cells, issue in data["instances"]:
        if issue == "LOGIC" and sheet != "Debt":
            for c, r in cells:
                grids[sheet][(c, r)] = data["broken"][sheet][(c, r)]
                skip.add((sheet, _addr(c, r)))
    _write_model(ws, grids)
    _write_audit(ws, [a for a in data["audit"] if (a[0], a[1]) not in skip])


def _nm_generic_codes(ws: Path) -> None:
    """Right fixes, but every audit row coded LOGIC."""
    data = build()
    _write_model(ws, data["gold"])
    _write_audit(ws, [(s, c, "LOGIC") for s, c, _ in data["audit"]])


def _nm_pasted_values(ws: Path) -> None:
    """Erroneous cells replaced by their correct numbers instead of formulas."""
    data = build()
    grids = {s: dict(g) for s, g in data["gold"].items()}
    for sheet, _code, cells, _issue in data["instances"]:
        for c, r in cells:
            if grids[sheet][(c, r)].startswith("="):
                grids[sheet][(c, r)] = _fmt_num(round(data["gold_values"][(sheet, c, r)], 2))
    _write_model(ws, grids)
    _write_audit(ws, data["audit"])


def _nm_marketing_on_gross(ws: Path) -> None:
    """Misses the single spec-only ROW error in marketing (gross instead of net revenue)."""
    data = build()
    grids = {s: dict(g) for s, g in data["gold"].items()}
    audit = list(data["audit"])
    for sheet, code, cells, _issue in data["instances"]:
        if (sheet, code) == ("Opex", "MKT"):
            for c, r in cells:
                grids[sheet][(c, r)] = data["broken"][sheet][(c, r)]
                audit.remove((sheet, _addr(c, r), "ROW"))
    _write_model(ws, grids)
    _write_audit(ws, audit)


def _nm_new_sheets_patterns_only(ws: Path) -> None:
    """Fixes everything except the consistent-row errors on Energy/Fleet/Stores (spec-only)."""
    data = build()
    grids = {s: dict(g) for s, g in data["gold"].items()}
    skip = set()
    for sheet, _code, cells, issue in data["instances"]:
        if sheet in ("Energy", "Fleet", "Stores") and issue in ("LOGIC", "ROW") and len(cells) > 1:
            for c, r in cells:
                grids[sheet][(c, r)] = data["broken"][sheet][(c, r)]
                skip.add((sheet, _addr(c, r)))
    _write_model(ws, grids)
    _write_audit(ws, [a for a in data["audit"] if (a[0], a[1]) not in skip])


def _nm_untouched_audit_only(ws: Path) -> None:
    """Perfect audit, but the model itself left unfixed."""
    data = build()
    _write_model(ws, data["broken"])
    _write_audit(ws, data["audit"])


TASK = long_task(
    id="task_406_spreadsheet_audit",  # registry id; generator seed stays long_15_spreadsheet_audit
    name="Аудит формул финансовой модели по методике",
    prompt=PROMPT,
    setup=_setup,
    gold=_gold,
    check=_check,
    tags=("spreadsheet", "audit", "finance"),
)

NEAR_MISSES = [
    _nm_majority_pattern,
    _nm_ignore_passports,
    _nm_generic_codes,
    _nm_pasted_values,
    _nm_marketing_on_gross,
    _nm_new_sheets_patterns_only,
    _nm_untouched_audit_only,
]
