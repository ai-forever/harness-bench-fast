"""long_20_math_solutions_check — проверка ученических решений.

40 задач восьми типов, 150 пошаговых решений с пронумерованными строками.
Каждое решение порождается генератором выкладки из параметров задачи:
все значения вычисляются по модели. Ошибка вносится на одном шаге:

* ``err`` — неверное значение/преобразование, дальше выкладка честно ведётся
  с ошибочным значением (так первая неверная строка единственна);
* ``typo`` — в строке записано неверное значение, но дальше ученик
  пользуется верным (ответ верный → ``flawed_reasoning``);
* ``retract`` — ученик сам явно отменяет ошибочную строку и исправляет её
  (по правилам это не ошибка → ``correct``);
* структурные ошибки (деление на выражение с переменной, забытое ОДЗ,
  неверная формула, неверная модель в текстовой задаче) — ветки генератора.

Номер первой неверной строки проверяется двумя способами: генератор
запоминает строку, где внесена ошибка, и она совпадает с первой строкой,
которой решение отличается от безошибочного решения тем же методом.
"""

from __future__ import annotations

import functools
import math
import random
from fractions import Fraction as Fr
from pathlib import Path

from .common import long_task, read_csv, require_share, rng, write, write_csv

TASK_ID = "long_20_math_solutions_check"
VERDICTS = ("correct", "wrong_answer", "flawed_reasoning")


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


class Reject(Exception):
    """Сочетание параметров/ошибки дало некрасивую выкладку — выбрать другое."""


# ---------------------------------------------------------------------------
# Форматирование
# ---------------------------------------------------------------------------

MINUS = "−"


def fm(v) -> str:
    v = Fr(v)
    body = str(abs(v.numerator)) if v.denominator == 1 else f"{abs(v.numerator)}/{v.denominator}"
    return (MINUS if v < 0 else "") + body


def fp(v) -> str:
    """Число в скобках, если отрицательное (для произведений)."""
    return f"({fm(v)})" if Fr(v) < 0 else fm(v)


def dec(v) -> str:
    """Десятичная запись для «житейских» величин (скорости, часы)."""
    v = Fr(v)
    if v.denominator == 1:
        return fm(v)
    den = v.denominator
    while den % 2 == 0:
        den //= 2
    while den % 5 == 0:
        den //= 5
    if den != 1:
        return fm(v)
    text = f"{float(abs(v)):.6f}".rstrip("0").rstrip(".").replace(".", ",")
    return (MINUS if v < 0 else "") + text


def poly(terms: list[tuple[object, str]]) -> str:
    """[(коэф, 'x²'|'x'|'')] → «2x² − 3x + 1»; нулевые слагаемые пропускаются."""
    out = []
    for coef, var in terms:
        c = Fr(coef)
        if c == 0:
            continue
        mag = abs(c)
        body = (fm(mag) if (mag != 1 or not var) else "") + var
        if not out:
            out.append((MINUS if c < 0 else "") + body)
        else:
            out.append((f" {MINUS} " if c < 0 else " + ") + body)
    return "".join(out) or "0"


def lin(a, b, var: str = "x") -> str:
    return poly([(a, var), (b, "")])


def signed(v) -> str:
    """« + 5» / « − 5» для дописывания слагаемого."""
    return f" {MINUS} {fm(abs(Fr(v)))}" if Fr(v) < 0 else f" + {fm(v)}"


def times_lin(k, a, b) -> str:
    """k·(ax + b) с аккуратной записью множителя."""
    inner = lin(a, b)
    if k == 1:
        return f"({inner})"
    if k == -1:
        return f"{MINUS}({inner})"
    return f"{fm(k)}({inner})"


def slip(R: random.Random, v, *, allow_sign: bool = True) -> Fr:
    """Правдоподобная арифметическая описка."""
    v = Fr(v)
    opts = [v + 1, v - 1, v + 2, v - 2, v + 10, v - 10]
    if allow_sign and v != 0:
        opts += [-v, -v]
    opts = [o for o in opts if o != v]
    return R.choice(opts)


# ---------------------------------------------------------------------------
# Писатель решения
# ---------------------------------------------------------------------------

RETRACT = [
    "Стоп, в строке {n} я {g_osh} — исправляю: {fix}",
    "Перепроверил{g_a} строку {n}: там ошибка, правильно так: {fix}",
    "Нет, в строке {n} неверно, зачёркиваю её. Верно: {fix}",
    "Исправление к строке {n} (там я {g_osh} в счёте): {fix}",
]


class W:
    def __init__(self, R: random.Random, fault: dict | None, female: bool) -> None:
        self.R = R
        self.fault = fault or {}
        self.female = female
        self.lines: list[str] = []
        self.fault_line: int | None = None
        self.propagated = False
        self.retracted = False
        self.retract_line: int | None = None

    # --- род ученика
    def g(self, male: str, female: str) -> str:
        return female if self.female else male

    # --- строки
    def say(self, *variants: str) -> None:
        self.lines.append(self.R.choice(variants))

    def add(self, text: str) -> None:
        self.lines.append(text)

    def kind(self, step: str) -> str | None:
        return self.fault.get("kind") if self.fault.get("step") == step else None

    def mark(self) -> None:
        """Следующая добавляемая строка — место структурной ошибки."""
        assert self.fault_line is None
        self.fault_line = len(self.lines) + 1
        self.propagated = True

    def step(self, name: str, true, wrong_fn, render):
        """Строка со значением. Возвращает значение, которым ученик пользуется дальше."""
        kind = self.kind(name)
        if kind is None:
            self.lines.append(render(true))
            return true
        wrong = wrong_fn(true)
        if wrong == true:
            raise Reject("ошибка не меняет значение")
        assert self.fault_line is None
        self.fault_line = len(self.lines) + 1
        wrong_text = render(wrong)
        if wrong_text == render(true):
            raise Reject("ошибка не видна в строке")
        self.lines.append(wrong_text)
        if kind == "err":
            self.propagated = True
            return wrong
        if kind == "typo":
            return true
        if kind == "retract":
            fix = render(true)
            tpl = self.R.choice(RETRACT)
            self.lines.append(tpl.format(
                n=self.fault_line, fix=fix,
                g_osh=self.g("ошибся", "ошиблась"), g_a=self.g("", "а"),
            ))
            self.retract_line = self.fault_line
            self.fault_line = None  # отменённая строка ошибкой не считается
            self.retracted = True
            return true
        raise ValueError(kind)


# ---------------------------------------------------------------------------
# Тип 1. Линейное уравнение с дробями + значение выражения
# ---------------------------------------------------------------------------


def _frac_term(a, b, d) -> str:
    return f"({lin(a, b)})/{d}"


def _t1_eq(c) -> str:
    a1, b1, a2, b2, a3, b3, d1, d2, d3 = c
    return f"{_frac_term(a1, b1, d1)} {MINUS} {_frac_term(a2, b2, d2)} = {_frac_term(a3, b3, d3)}"


def _t1_expr(p) -> str:
    e2, e1, e0 = p["expr"]
    return poly([(e2, "x²"), (e1, "x"), (e0, "")])


def t1_params(R: random.Random) -> dict:
    while True:
        d1, d2, d3 = R.choice([(2, 3, 6), (3, 4, 6), (2, 5, 10), (4, 6, 3), (3, 2, 4), (6, 4, 12), (5, 2, 4)])
        a1 = R.choice([1, 2, 3, 5, 4])
        a2, a3 = (R.choice([1, 2, 3, 5, -1, -2, 4]) for _ in range(2))
        x0 = R.choice([-5, -4, -3, -2, 2, 3, 4, 5, 6, 7])
        b1 = R.randint(-9, 9)
        while (a1 * x0 + b1) % d1:
            b1 += 1
        b2 = R.randint(-9, 9)
        while (a2 * x0 + b2) % d2:
            b2 += 1
        v = Fr(a1 * x0 + b1, d1) - Fr(a2 * x0 + b2, d2)
        b3 = v * d3 - a3 * x0
        if b3.denominator != 1 or 0 in (b1, b2, b3):
            continue
        L = math.lcm(d1, d2, d3)
        K = (L // d1) * a1 - (L // d2) * a2 - (L // d3) * a3
        if K in (0, 1, -1):
            continue
        expr = (R.choice([2, 3, -1, -2]), R.choice([-5, -3, -2, 2, 4, 5]), R.randint(-9, 9))
        return dict(type=1, c=(a1, b1, a2, b2, a3, int(b3), d1, d2, d3), x=x0, expr=expr)


def t1_statement(p) -> str:
    return (f"Решите уравнение {_t1_eq(p['c'])}. В ответ запишите значение выражения "
            f"{_t1_expr(p)} при найденном значении x.")


def t1_answer(p):
    e2, e1, e0 = p["expr"]
    x = p["x"]
    return e2 * x * x + e1 * x + e0


def t1_methods(p) -> list[str]:
    return ["lcd", "prod"]
T1_ERR = ["restate", "factors", "expand_sign", "expand1", "expand2", "expand3", "collect", "move", "simplify", "divide",
          "sq", "t2", "t1", "sum"]
T1_TYPO = ["factors", "expand1", "expand3", "collect", "simplify", "sq", "t2", "t1"]


def t1_gen(p, method: str, w: W):
    R = w.R

    def misread(c):
        c = list(c)
        i = R.choice([1, 3, 5])
        c[i] = slip(R, c[i])
        if c[i] == 0:
            raise Reject("ноль")
        return tuple(int(v) for v in c)

    c = w.step("restate", p["c"], misread,
               lambda c: f"Нужно решить уравнение {_t1_eq(c)} и найти значение выражения "
                         f"{_t1_expr(p)} при найденном x.")
    a1, b1, a2, b2, a3, b3, d1, d2, d3 = c
    w.say("Сначала избавлюсь от дробей, потом решу линейное уравнение, а в конце подставлю корень в выражение.",
          "План такой: убрать знаменатели, привести подобные, найти x и посчитать выражение.",
          "Уравнение линейное, только с дробями. Буду действовать по шагам, чтобы не запутаться в знаках.")
    if method == "lcd":
        L = math.lcm(d1, d2, d3)
        w.say(f"Знаменатели дробей: {d1}, {d2} и {d3}. Их наименьшее общее кратное равно {L}.",
              f"Общий знаменатель для {d1}, {d2} и {d3} — число {L}, на него и буду умножать.")
    else:
        L = d1 * d2 * d3
        w.say(f"Чтобы не искать НОК, умножу обе части на произведение знаменателей: {d1}·{d2}·{d3} = {L}.",
              f"Проще всего умножить всё на {d1}·{d2}·{d3} = {L}: числа будут побольше, зато ничего не потеряю.")
    m_true = (L // d1, L // d2, L // d3)

    def bad_factors(m):
        m = list(m)
        i = R.randrange(3)
        m[i] = m[i] + R.choice([-1, 1, 2]) if m[i] > 2 else m[i] + 1
        return tuple(m)

    m1, m2, m3 = w.step("factors", m_true, bad_factors,
                        lambda m: f"Дополнительные множители: {L} : {d1} = {m[0]}, {L} : {d2} = {m[1]}, "
                                  f"{L} : {d3} = {m[2]}.")
    w.add(f"Умножаем обе части на {L}: {times_lin(m1, a1, b1)} {MINUS} {times_lin(m2, a2, b2)} = "
          f"{times_lin(m3, a3, b3)}.")
    w.say(f"Раскрываю скобки по одной. Первую скобку умножаю на {m1}, вторую — на {m2}, правую — на {m3}. "
          "Перед второй скобкой стоит минус, поэтому при её раскрытии знаки внутри меняются.",
          "Теперь раскрываю скобки. Делаю это отдельно для каждой, потому что со знаками у второй скобки "
          "легко ошибиться: минус перед ней меняет знак у каждого слагаемого.",
          f"Раскрываем скобки. Множители {m1}, {m2} и {m3} уже известны, остаётся аккуратно умножить "
          "каждое слагаемое, помня про минус перед второй скобкой.")
    e1v = w.step("expand1", (m1 * a1, m1 * b1),
                 lambda e: (e[0], int(slip(R, e[1], allow_sign=False))),
                 lambda e: f"{times_lin(m1, a1, b1)} = {lin(e[0], e[1])}")

    def bad_exp2(e):
        if w.kind("expand_sign") is not None:
            return (e[0], -e[1])
        return (e[0], int(slip(R, e[1], allow_sign=False)))

    name2 = "expand_sign" if w.kind("expand_sign") else "expand2"
    e2v = w.step(name2, (-m2 * a2, -m2 * b2), bad_exp2,
                 lambda e: f"{MINUS}{times_lin(m2, a2, b2)} = {lin(e[0], e[1])}")
    e3v = w.step("expand3", (m3 * a3, m3 * b3),
                 lambda e: (e[0], int(slip(R, e[1], allow_sign=False))),
                 lambda e: f"{times_lin(m3, a3, b3)} = {lin(e[0], e[1])}")
    ex = (e1v[0], e1v[1], e2v[0], e2v[1], e3v[0], e3v[1])
    w.add(f"Получаем уравнение без дробей: {poly([(ex[0], 'x'), (ex[1], ''), (ex[2], 'x'), (ex[3], '')])} = "
          f"{poly([(ex[4], 'x'), (ex[5], '')])}.")
    w.say("Дроби исчезли, дальше обычное линейное уравнение. Главное теперь — не потерять знак при "
          "сложении подобных.",
          "От знаменателей избавились. Теперь можно работать с уравнением как с обычным линейным.",
          "Знаменателей больше нет, так что дальше всё стандартно.")
    w.say("Привожу подобные слагаемые в левой части:", "Собираю подобные слева:",
          "В левой части складываю слагаемые с x отдельно и числа отдельно:")
    col_true = (ex[0] + ex[2], ex[1] + ex[3])

    def bad_collect(cc):
        i = R.randrange(2)
        cc = list(cc)
        cc[i] = int(slip(R, cc[i]))
        return tuple(cc)

    A, B = w.step("collect", col_true, bad_collect,
                  lambda cc: f"{poly([(cc[0], 'x'), (cc[1], '')])} = {poly([(ex[4], 'x'), (ex[5], '')])}")
    C, D = ex[4], ex[5]
    w.say("Переношу слагаемые с x в левую часть, а числа — в правую, меняя знаки на противоположные:",
          "Слагаемые с x — влево, числа — вправо; при переносе через знак «=» знак меняется:",
          "Теперь перенос: всё с x оставляю слева, свободные члены отправляю направо.")
    mv_true = (A, -C, D, -B)

    def bad_move(mv):
        mv = list(mv)
        if C != 0 and R.random() < 0.5:
            mv[1] = C
        else:
            mv[3] = B
        return tuple(mv)

    mv = w.step("move", mv_true, bad_move,
                lambda mv: f"{poly([(mv[0], 'x'), (mv[1], 'x')])} = {poly([(mv[2], ''), (mv[3], '')])}")
    kn_true = (mv[0] + mv[1], mv[2] + mv[3])

    def bad_simpl(kn):
        kn = list(kn)
        kn[1] = int(slip(R, kn[1]))
        return tuple(kn)

    K, N = w.step("simplify", kn_true, bad_simpl, lambda kn: f"{poly([(kn[0], 'x')])} = {fm(kn[1])}")
    if K == 0:
        raise Reject("K=0")
    x_true = Fr(N, K)

    def bad_div(x):
        if N != 0 and Fr(K, N) != x and R.random() < 0.5:
            return Fr(K, N)
        return slip(R, x)

    x = w.step("divide", x_true, bad_div, lambda x: f"x = {fm(N)} : {fp(K)} = {fm(x)}")
    w.say("Корень уравнения найден, осталась вторая часть пункта — значение выражения.",
          "С уравнением разобрались. Теперь выражение.",
          "Итак, x известен; перехожу к вычислению выражения.")
    if x.denominator != 1 and w.propagated:
        w.say("Получилась дробь, ну что ж, считаю с дробью.", "Корень дробный, но так бывает.")
    e2, e1, e0 = p["expr"]
    w.say(f"Теперь найду значение выражения {_t1_expr(p)} при x = {fm(x)}. Считаю по частям.",
          f"Подставляю x = {fm(x)} в выражение {_t1_expr(p)}, по частям, чтобы не ошибиться.")
    sq = w.step("sq", x * x, lambda v: slip(R, v, allow_sign=False), lambda v: f"x² = {fp(x)}² = {fm(v)}")
    t2 = w.step("t2", e2 * sq, lambda v: slip(R, v), lambda v: _coef_line(e2, "x²", sq, v))
    t1 = w.step("t1", e1 * x, lambda v: slip(R, v), lambda v: _coef_line(e1, "x", x, v))
    total = w.step("sum", t2 + t1 + e0, lambda v: slip(R, v),
                   lambda v: f"{fm(t2)}{signed(t1)}{signed(e0)} = {fm(v)}" if e0 else f"{fm(t2)}{signed(t1)} = {fm(v)}")
    _t1_check(p, w, c, x)
    w.add(f"Ответ: {fm(total)}.")
    return total


def _coef_line(c, var: str, val, res) -> str:
    if c == 1:
        return f"{var} = {fm(res)}"
    if c == -1:
        return f"{MINUS}{var} = {fm(res)}"
    return f"{fm(c)}·{var} = {fm(c)}·{fp(val)} = {fm(res)}"


def _t1_check(p, w: W, c, x) -> None:
    a1, b1, a2, b2, a3, b3, d1, d2, d3 = p["c"]
    if w.R.random() < 0.45:
        return
    if w.propagated and w.R.random() < 0.5:
        return
    v1, v2, v3 = Fr(a1 * x + b1, d1), Fr(a2 * x + b2, d2), Fr(a3 * x + b3, d3)
    w.say("Проверю корень подстановкой в исходное уравнение.", "Для надёжности сделаю проверку.")
    w.add(f"Левая часть при x = {fm(x)}: {fm(a1 * x + b1)}/{d1} {MINUS} {fp(a2 * x + b2)}/{d2} = "
          f"{fm(v1)} {MINUS} {fp(v2)} = {fm(v1 - v2)}.")
    right = v3 if not w.propagated else v1 - v2
    w.add(f"Правая часть: {fp(a3 * x + b3)}/{d3} = {fm(right)}. Совпадает, корень найден верно.")


# ---------------------------------------------------------------------------
# Тип 2. Квадратное уравнение в нестандартной записи + выражение от корней
# ---------------------------------------------------------------------------

_SUP = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def sup(n) -> str:
    return str(n).translate(_SUP)


def _isqrt_exact(v: Fr) -> Fr | None:
    if v < 0:
        return None
    n, d = v.numerator, v.denominator
    rn, rd = math.isqrt(n), math.isqrt(d)
    if rn * rn == n and rd * rd == d:
        return Fr(rn, rd)
    return None


T2_Q = {
    "sum_sq": "сумму квадратов его корней",
    "diff": "разность большего и меньшего корней",
    "big3": "утроенный больший корень, уменьшенный на меньший корень",
}


def _t2_eq(c) -> str:
    p_, q_, s_, A, B, C = c
    left = f"({lin(p_, q_)})({lin(1, s_)})"
    return f"{left} = {poly([(A, 'x²'), (B, 'x'), (C, '')])}"


def t2_params(R: random.Random) -> dict:
    while True:
        a = R.choice([1, 1, 2, 3])
        r1, r2 = sorted(R.sample([-7, -6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6, 8], 2))
        p_ = R.choice([2, 3, 4, 5])
        q_, s_ = R.choice([-5, -3, -1, 1, 2, 4, 7]), R.choice([-4, -2, 1, 3, 5, 6])
        A = p_ - a
        B = p_ * s_ + q_ + a * (r1 + r2)
        C = q_ * s_ - a * r1 * r2
        if A == 0 or 0 in (B, C):
            continue
        q = R.choice(list(T2_Q))
        return dict(type=2, c=(p_, q_, s_, A, B, C), a=a, roots=(r1, r2), q=q)


def _t2_value(q, r1, r2):
    lo, hi = min(r1, r2), max(r1, r2)
    return {"sum_sq": r1 * r1 + r2 * r2, "diff": hi - lo, "big3": 3 * hi - lo}[q]


def t2_statement(p) -> str:
    return f"Решите уравнение {_t2_eq(p['c'])}. В ответ запишите {T2_Q[p['q']]}."


def t2_answer(p):
    return _t2_value(p["q"], *p["roots"])


T2_ERR = ["restate", "expandL", "move", "disc", "root_sign", "root1", "qcalc"]
T2_TYPO = ["expandL", "disc", "root1", "qcalc"]


def t2_methods(p) -> list[str]:
    out = ["disc"]
    if p["q"] == "sum_sq":
        out.append("vieta")
    return out


def t2_gen(p, method: str, w: W):
    R = w.R

    def misread(c):
        c = list(c)
        i = R.choice([1, 2, 5])
        c[i] = int(slip(R, c[i]))
        return tuple(c)

    c = w.step("restate", p["c"], misread,
               lambda c: f"Условие: решить уравнение {_t2_eq(c)} и найти {T2_Q[p['q']]}.")
    p_, q_, s_, A, B, C = c
    w.say("Это квадратное уравнение, только записано не в стандартном виде. Сначала приведу его к виду "
          "ax² + bx + c = 0.",
          "Уравнение квадратное, но скобки мешают. План: раскрыть скобки, перенести всё в одну часть, "
          "а потом решать.",
          "Сначала надо привести уравнение к стандартному виду, иначе формулу корней не применить.")
    L_true = (p_, p_ * s_ + q_, q_ * s_)

    def bad_L(L):
        L = list(L)
        i = R.choice([1, 2])
        L[i] = int(slip(R, L[i]))
        return tuple(L)

    L = w.step("expandL", L_true, bad_L,
               lambda L: f"Левая часть: ({lin(p_, q_)})({lin(1, s_)}) = "
                         f"{poly([(p_, 'x²'), (p_ * s_, 'x'), (q_, 'x'), (q_ * s_, '')])} = "
                         f"{poly([(L[0], 'x²'), (L[1], 'x'), (L[2], '')])}.")
    w.say("Переношу все слагаемые из правой части в левую с противоположными знаками и привожу подобные:",
          "Теперь всё переношу влево, чтобы справа остался ноль:",
          "Переносим правую часть налево (знаки меняются) и приводим подобные.")
    abc_true = (L[0] - A, L[1] - B, L[2] - C)

    def bad_move(t):
        t = list(t)
        i = R.choice([1, 2])
        t[i] = [L[1] + B, L[2] + C][i - 1]
        return tuple(t)

    a, b, cc = w.step("move", abc_true, bad_move,
                      lambda t: f"{poly([(t[0], 'x²'), (t[1], 'x'), (t[2], '')])} = 0")
    if a == 0:
        raise Reject("не квадратное")
    if method == "vieta":
        return _t2_vieta(p, w, a, b, cc)
    w.say("Получилось приведённое к стандартному виду уравнение, коэффициенты: "
          f"a = {fm(a)}, b = {fm(b)}, c = {fm(cc)}.",
          f"Стандартный вид получен: a = {fm(a)}, b = {fm(b)}, c = {fm(cc)}.")
    w.say("Решаю через дискриминант.", "Считаю дискриминант.", "Дальше — стандартно, через дискриминант.")
    D_true = b * b - 4 * a * cc

    def bad_disc(D):
        if cc < 0 and R.random() < 0.6:
            return b * b + 4 * a * cc  # потерян знак у −4ac
        return slip(R, D, allow_sign=False)

    D = w.step("disc", D_true, bad_disc,
               lambda D: f"D = b² − 4ac = {fp(b)}² − 4·{fp(a)}·{fp(cc)} = {fm(b * b)}{signed(-4 * a * cc)} = {fm(D)}")
    sq = _isqrt_exact(Fr(D))
    if sq is None or sq == 0:
        raise Reject("дискриминант не квадрат")
    w.add(f"D > 0, значит, корней два; √D = √{fm(D)} = {fm(sq)}.")
    r1_true = (-b, 2 * a, Fr(-b - sq, 2 * a))

    def bad_root1(r):
        if w.kind("root_sign"):
            return (b, 2 * a, Fr(b - sq, 2 * a))
        if a != 1 and R.random() < 0.5:
            return (-b, a, Fr(-b - sq, a))
        return (-b, 2 * a, slip(R, r[2]))

    def den_text(den):
        return f"(2·{fp(a)})" if den == 2 * a else fm(den)

    name = "root_sign" if w.kind("root_sign") else "root1"
    nb, den, x1 = w.step(name, r1_true, bad_root1,
                         lambda r: f"x₁ = ({fm(r[0])} − {fm(sq)})/{den_text(r[1])} = {fm(r[2])}")
    x2 = Fr(nb + sq, den)
    w.add(f"x₂ = ({fm(nb)} + {fm(sq)})/{den_text(den)} = {fm(x2)}")
    val = _t2_finish(p, w, x1, x2)
    return val


def _t2_finish(p, w: W, x1, x2):
    R = w.R
    lo, hi = min(x1, x2), max(x1, x2)
    q = p["q"]
    w.say(f"Корни уравнения: {fm(lo)} и {fm(hi)}.", f"Итак, получились корни {fm(lo)} и {fm(hi)}.")
    if q == "sum_sq":
        render = lambda v: f"x₁² + x₂² = {fp(x1)}² + {fp(x2)}² = {fm(x1 * x1)} + {fm(x2 * x2)} = {fm(v)}"  # noqa: E731
        true = x1 * x1 + x2 * x2
    elif q == "diff":
        render = lambda v: f"Больший корень минус меньший: {fm(hi)} − {fp(lo)} = {fm(v)}"  # noqa: E731
        true = hi - lo
    else:
        render = lambda v: f"3·{fp(hi)} − {fp(lo)} = {fm(3 * hi)}{signed(-lo)} = {fm(v)}"  # noqa: E731
        true = 3 * hi - lo
    val = w.step("qcalc", true, lambda v: slip(R, v), render)
    if not w.propagated and R.random() < 0.5:
        a, b, c = _t2_std(p)
        w.say("Проверю по теореме Виета.", "Сделаю проверку через теорему Виета.")
        w.add(f"x₁ + x₂ = {fm(x1 + x2)}, и −b/a = {fm(Fr(-b, a))} — совпадает; "
              f"x₁·x₂ = {fm(x1 * x2)}, и c/a = {fm(Fr(c, a))} — тоже совпадает.")
    w.add(f"Ответ: {fm(val)}.")
    return val


def _t2_std(p):
    p_, q_, s_, A, B, C = p["c"]
    return p_ - A, p_ * s_ + q_ - B, q_ * s_ - C


def _t2_vieta(p, w: W, a, b, c):
    R = w.R
    w.say("Сами корни искать не обязательно: сумму квадратов можно найти по теореме Виета.",
          "Можно обойтись без дискриминанта: x₁² + x₂² выражается через сумму и произведение корней.")
    D = b * b - 4 * a * c
    w.add(f"Корни существуют, так как D = {fp(b)}² − 4·{fp(a)}·{fp(c)} = {fm(D)} > 0.")
    s_ = w.step("vsum", Fr(-b, a), lambda v: -v, lambda v: f"x₁ + x₂ = −b/a = {fm(v)}")
    p2 = w.step("vprod", Fr(c, a), lambda v: slip(R, v), lambda v: f"x₁·x₂ = c/a = {fm(v)}")
    w.add("x₁² + x₂² = (x₁ + x₂)² − 2x₁x₂")
    val = w.step("vcalc", s_ * s_ - 2 * p2, lambda v: slip(R, v),
                 lambda v: f"x₁² + x₂² = {fp(s_)}² − 2·{fp(p2)} = {fm(s_ * s_)}{signed(-2 * p2)} = {fm(v)}")
    w.add(f"Ответ: {fm(val)}.")
    return val


# ---------------------------------------------------------------------------
# Тип 3. Общий множитель в обеих частях (потеря корня при делении)
# ---------------------------------------------------------------------------

T3_Q = {"max": "наибольший корень", "sum": "сумму всех корней", "prod": "произведение всех корней"}


def _t3_eq(c, swap: bool) -> str:
    a, b, cc, d, e = c
    f = f"({lin(1, -a)})"
    left = f"{f}({lin(b, cc)})"
    right = f"({lin(d, e)}){f}" if swap else f"{f}({lin(d, e)})"
    return f"{left} = {right}"


def t3_params(R: random.Random) -> dict:
    while True:
        a = R.choice([-6, -5, -4, -3, -2, 2, 3, 4, 5, 7])
        b, d = R.sample([1, 2, 3, 4, 5], 2)
        r = R.choice([-8, -6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 6, 9])
        if r == a:
            continue
        cc = R.choice([-9, -7, -5, -3, -1, 1, 2, 4, 6, 8])
        e = cc + r * (b - d)  # (b − d)r + cc − e = 0
        if e == 0 or cc == e:
            continue
        q = R.choice(list(T3_Q))
        return dict(type=3, c=(a, b, cc, d, e), swap=R.random() < 0.5, roots=(a, r), q=q)


def t3_statement(p) -> str:
    return f"Решите уравнение {_t3_eq(p['c'], p['swap'])}. В ответ запишите {T3_Q[p['q']]}."


def _t3_value(q, roots):
    if q == "max":
        return max(roots)
    if q == "sum":
        return sum(roots)
    out = Fr(1)
    for v in roots:
        out *= v
    return out


def t3_answer(p):
    return _t3_value(p["q"], p["roots"])


def t3_methods(p) -> list[str]:
    return ["factor", "expand"]


T3_ERR = ["restate", "inner", "root_r", "qcalc"]
T3_TYPO = ["root_r", "qcalc"]
T3_STRUCT = ["divide"]


def t3_gen(p, method: str, w: W):
    R = w.R

    def misread(c):
        c = list(c)
        i = R.choice([2, 4])
        c[i] = int(slip(R, c[i]))
        if c[i] == 0 or c[2] == c[4]:
            raise Reject("ноль")
        return tuple(c)

    c = w.step("restate", p["c"], misread,
               lambda c: f"Дано уравнение {_t3_eq(c, p['swap'])}, нужно найти {T3_Q[p['q']]}.")
    a, b, cc, d, e = c
    fac = f"(x {MINUS} {fm(a)})" if a > 0 else f"(x + {fm(-a)})"
    w.say(f"Замечаю, что в обеих частях есть одинаковый множитель {fac}.",
          f"И слева, и справа стоит множитель {fac} — это ключ к решению.")
    if method == "expand":
        return _t3_expand(p, w, c, fac)
    if w.fault.get("step") == "divide":
        w.mark()
        w.add(f"Сокращаю обе части на {fac}: {lin(b, cc)} = {lin(d, e)}.")
        k, m = b - d, e - cc
        w.add(f"{poly([(k, 'x')])} = {fm(m)}")
        r = Fr(m, k)
        if k != 1:
            w.add(f"x = {fm(m)} : {fp(k)} = {fm(r)}")
        roots = [r]
        w.say(f"Уравнение имеет единственный корень x = {fm(r)}.", f"Получился один корень: x = {fm(r)}.")
    else:
        w.add(f"Делить на {fac} нельзя — он может быть равен нулю, и тогда потеряется корень. "
              f"Поэтому переношу всё в левую часть: {_t3_eq(c, False).replace(' = ', f' {MINUS} ')} = 0.")
        w.add(f"Выношу общий множитель за скобки: {fac}(({lin(b, cc)}) {MINUS} ({lin(d, e)})) = 0.")

        def bad_inner(km):
            if R.random() < 0.5:
                return (km[0], cc + e)
            return (km[0], int(slip(R, km[1])))

        k, m = w.step("inner", (b - d, cc - e), bad_inner,
                      lambda km: f"{fac}({lin(km[0], km[1])}) = 0")
        if m == 0:
            raise Reject("m=0")
        w.say(f"Во второй скобке {w.g('раскрыл', 'раскрыла')} скобки и {w.g('привёл', 'привела')} подобные, получилось "
              "произведение двух линейных множителей.",
              "Выражение в больших скобках упростилось, осталось произведение двух скобок.")
        w.say("Произведение равно нулю, когда хотя бы один из множителей равен нулю.",
              "Произведение равно нулю, если один из множителей равен нулю, а другой при этом имеет смысл.")
        w.add(f"{fac[1:-1]} = 0, откуда x = {fm(a)}.")
        r = w.step("root_r", Fr(-m, k), lambda v: slip(R, v),
                   lambda v: f"{lin(k, m)} = 0, откуда x = {fm(-m)} : {fp(k)} = {fm(v)}.")
        roots = [Fr(a), r] if r != a else [Fr(a)]
        w.add(f"Корни уравнения: {fm(min(roots))} и {fm(max(roots))}." if len(roots) == 2
              else f"Оба множителя дают x = {fm(a)}, так что корень один.")
    return _t3_finish(p, w, roots)


def _t3_finish(p, w: W, roots):
    R = w.R
    q = p["q"]
    true = _t3_value(q, roots)
    if q == "max":
        render = (lambda v: f"Наибольший из корней: {fm(v)}.") if len(roots) > 1 else \
            (lambda v: f"Корень один, значит, он и наибольший: {fm(v)}.")
    elif q == "sum":
        render = (lambda v: f"Сумма корней: {' + '.join(fp(x) for x in roots)} = {fm(v)}.") if len(roots) > 1 \
            else (lambda v: f"Корень один, поэтому сумма корней равна {fm(v)}.")
    else:
        render = (lambda v: f"Произведение корней: {'·'.join(fp(x) for x in roots)} = {fm(v)}.") \
            if len(roots) > 1 else (lambda v: f"Корень один, поэтому произведение равно {fm(v)}.")
    def bad_q(v):
        if len(roots) < 2:
            raise Reject("один корень")
        return slip(R, v)

    val = w.step("qcalc", true, bad_q, render)
    if not w.propagated and len(roots) > 1 and R.random() < 0.5:
        w.say("Проверю оба корня подстановкой.", "Подстановкой проверю корни.")
        for x in roots:
            _t3_check_line(p, w, x)
    w.add(f"Ответ: {fm(val)}.")
    return val


def _t3_check_line(p, w: W, x) -> None:
    a, b, cc, d, e = p["c"]
    f = x - a
    lv, rv = f * (b * x + cc), f * (d * x + e)
    w.add(f"При x = {fm(x)}: левая часть {fp(f)}·{fp(b * x + cc)} = {fm(lv)}, "
          f"правая {fp(f)}·{fp(d * x + e)} = {fm(rv)} — верно.")


def _t3_expand(p, w: W, c, fac):
    R = w.R
    a, b, cc, d, e = c
    w.say("Решу напрямую: раскрою скобки и получу обычное квадратное уравнение.",
          "Можно просто раскрыть все скобки — получится квадратное уравнение, его и решу.")
    L = (b, cc - a * b, -a * cc)
    Rr = (d, e - a * d, -a * e)
    w.add(f"Левая часть: {_t3_eq(c, False).split(' = ')[0]} = {poly([(L[0], 'x²'), (L[1], 'x'), (L[2], '')])}.")
    w.add(f"Правая часть: {_t3_eq(c, p['swap']).split(' = ')[1]} = {poly([(Rr[0], 'x²'), (Rr[1], 'x'), (Rr[2], '')])}.")
    A, B, C = L[0] - Rr[0], L[1] - Rr[1], L[2] - Rr[2]
    w.add(f"Переношу всё влево: {poly([(A, 'x²'), (B, 'x'), (C, '')])} = 0.")
    D = B * B - 4 * A * C
    w.add(f"D = {fp(B)}² − 4·{fp(A)}·{fp(C)} = {fm(D)}.")
    sq = _isqrt_exact(Fr(D))
    if not sq:
        raise Reject("D")
    x1 = w.step("root1", Fr(-B - sq, 2 * A), lambda v: slip(R, v),
                lambda v: f"x₁ = ({fm(-B)} − {fm(sq)})/(2·{fp(A)}) = {fm(v)}")
    x2 = Fr(-B + sq, 2 * A)
    w.add(f"x₂ = ({fm(-B)} + {fm(sq)})/(2·{fp(A)}) = {fm(x2)}")
    roots = sorted({x1, x2})
    w.add(f"Корни уравнения: {fm(roots[0])} и {fm(roots[1])}.")
    return _t3_finish(p, w, roots)


# ---------------------------------------------------------------------------
# Тип 4. Дробное уравнение с посторонним корнем
# ---------------------------------------------------------------------------

T4_Q = {
    "sum": "его корень (если корней несколько — их сумму)",
    "min": "наименьший корень",
    "max": "наибольший корень",
}


def _t4_den(r) -> str:
    return f"(x {MINUS} {fm(r)})" if r > 0 else f"(x + {fm(-r)})"


def _t4_eq(c) -> str:
    u, v, t, k, r = c
    den = _t4_den(r)
    return f"({poly([(1, 'x²'), (u, 'x')])})/{den} = ({lin(v, t)})/{den}{signed(k)}"


def t4_params(R: random.Random) -> dict:
    while True:
        r, s_ = R.sample([-6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6, 7], 2)
        u = R.choice([-5, -3, -2, 1, 2, 3, 4])
        k = R.choice([-3, -2, 2, 3, 4])
        v = u - k + r + s_
        t = k * r - r * s_
        if 0 in (v, t):
            continue
        q = R.choice(list(T4_Q))
        return dict(type=4, c=(u, v, t, k, r), r=r, s=s_, q=q)


def t4_statement(p) -> str:
    return f"Решите уравнение {_t4_eq(p['c'])}. В ответ запишите {T4_Q[p['q']]}."


def _t4_value(q, roots):
    if q == "sum":
        return sum(roots)
    return min(roots) if q == "min" else max(roots)


def t4_answer(p):
    return _t4_value(p["q"], [p["s"]])


def t4_methods(p) -> list[str]:
    return ["odz_first", "check_last", "vieta"]


T4_ERR = ["restate", "mult", "expand_k", "collect", "disc", "root1", "qcalc"]
T4_TYPO = ["collect", "disc", "qcalc"]
T4_STRUCT = ["no_odz"]


def t4_gen(p, method: str, w: W):
    R = w.R

    def misread(c):
        c = list(c)
        i = R.choice([0, 2])
        c[i] = int(slip(R, c[i]))
        if c[i] == 0:
            raise Reject("ноль")
        return tuple(c)

    c = w.step("restate", p["c"], misread,
               lambda c: f"Решаем уравнение {_t4_eq(c)}; требуется найти {T4_Q[p['q']]}.")
    u, v, t, k, r = c
    den = _t4_den(r)
    if method == "odz_first":
        w.add(f"ОДЗ: знаменатель {den} не должен обращаться в нуль, значит, x ≠ {fm(r)}.")
    w.say(f"Умножаю обе части уравнения на {den}, чтобы избавиться от дробей:",
          f"Домножаю обе части на общий знаменатель {den}:",
          f"Избавляюсь от знаменателя: умножаю обе части на {den}.")
    kx = f"{fm(k)}{den}" if abs(k) != 1 else (den if k == 1 else f"{MINUS}{den}")

    def bad_mult(_):
        return "forgot"

    mult = w.step("mult", "ok", bad_mult,
                  lambda m: f"{poly([(1, 'x²'), (u, 'x')])} = {lin(v, t)}{signed(k) if m == 'forgot' else ''}"
                            + ("" if m == "forgot" else f" {'+' if k > 0 else MINUS} {kx.lstrip(MINUS)}"))
    if mult == "forgot":
        rhs = (v, t + k)
    else:
        rhs = w.step("expand_k", (v + k, t - k * r),
                     lambda e: (e[0], t + k * r),
                     lambda e: f"{poly([(1, 'x²'), (u, 'x')])} = {poly([(v, 'x'), (t, ''), (k, 'x'), (e[1] - t, '')])}")
    w.say("Дроби ушли, но об ограничении на знаменатель забывать нельзя — вернусь к нему в конце.",
          "Знаменатель исчез, получилось целое уравнение. Его корни потом надо сверить со знаменателем.",
          "Теперь уравнение без дробей, решаю его как обычное.")
    w.say("Переношу всё в левую часть и привожу подобные:", "Собираю всё слева:",
          "Все слагаемые — в левую часть, подобные — вместе:")
    BC_true = (u - rhs[0], -rhs[1])
    B, C = w.step("collect", BC_true, lambda bc: (bc[0], int(slip(R, bc[1]))),
                  lambda bc: f"{poly([(1, 'x²'), (bc[0], 'x'), (bc[1], '')])} = 0")
    if method == "vieta":
        roots = _t4_vieta_roots(p, w, B, C)
    else:
        D = w.step("disc", B * B - 4 * C, lambda d: slip(R, d, allow_sign=False),
                   lambda d: f"D = {fp(B)}² − 4·{fp(C)} = {fm(d)}")
        sq = _isqrt_exact(Fr(D))
        if not sq:
            raise Reject("D")
        x1 = w.step("root1", Fr(-B - sq, 2), lambda x: slip(R, x),
                    lambda x: f"x₁ = ({fm(-B)} − {fm(sq)})/2 = {fm(x)}")
        x2 = Fr(-B + sq, 2)
        w.add(f"x₂ = ({fm(-B)} + {fm(sq)})/2 = {fm(x2)}")
        roots = sorted({x1, x2})
    good = [x for x in roots if x != r]
    if w.fault.get("step") == "no_odz" and r in roots and len(roots) == 2:
        w.mark()
        w.add(f"Оба найденных числа являются корнями уравнения: x = {fm(roots[0])} и x = {fm(roots[1])}.")
        final = roots
    elif w.fault.get("step") == "no_odz":
        raise Reject("нет постороннего корня")
    elif r in roots:
        if method == "odz_first":
            w.add(f"x = {fm(r)} не входит в ОДЗ, поэтому это посторонний корень.")
        else:
            w.add(f"Проверка: при x = {fm(r)} знаменатель {den} обращается в нуль, значит, x = {fm(r)} — "
                  "посторонний корень.")
        w.add(f"Уравнение имеет единственный корень x = {fm(good[0])}.")
        final = good
    else:
        w.add("Ни один из найденных корней не обращает знаменатель в нуль, оба подходят.")
        final = roots
    return _t4_finish(p, w, final)


def _t4_vieta_roots(p, w: W, B, C):
    w.say("Корни подберу по теореме, обратной теореме Виета.",
          "Здесь удобно подобрать корни по теореме Виета: коэффициент при x² равен 1.")
    w.add(f"x₁ + x₂ = {fm(-B)}, x₁·x₂ = {fm(C)}.")
    D = B * B - 4 * C
    sq = _isqrt_exact(Fr(D))
    if not sq:
        raise Reject("D")
    x1, x2 = Fr(-B - sq, 2), Fr(-B + sq, 2)
    if x1.denominator != 1:
        raise Reject("нецелые")
    w.add(f"Подходят числа {fm(x1)} и {fm(x2)}: {fp(x1)} + {fp(x2)} = {fm(x1 + x2)}, "
          f"{fp(x1)}·{fp(x2)} = {fm(x1 * x2)}.")
    return sorted({x1, x2})


def _t4_finish(p, w: W, roots):
    R = w.R
    q = p["q"]
    true = _t4_value(q, roots)
    if len(roots) == 1:
        render = lambda v: f"Корень один, поэтому в ответ идёт он сам: {fm(v)}."  # noqa: E731
    elif q == "sum":
        render = lambda v: f"Сумма корней: {fp(roots[0])} + {fp(roots[1])} = {fm(v)}."  # noqa: E731
    else:
        word = "Наименьший" if q == "min" else "Наибольший"
        render = lambda v: f"{word} из корней: {fm(v)}."  # noqa: E731

    def bad_q(v):
        if len(roots) < 2 or q != "sum":
            raise Reject("нечего считать")
        return slip(R, v)

    val = w.step("qcalc", true, bad_q, render)
    w.add(f"Ответ: {fm(val)}.")
    return val


# ---------------------------------------------------------------------------
# Тип 5. Арифметическая прогрессия: по двум членам найти сумму
# ---------------------------------------------------------------------------


def _sub(n) -> str:
    return str(n).translate(str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉"))


def t5_params(R: random.Random) -> dict:
    while True:
        a1 = R.randint(-20, 25)
        d = R.choice([-4, -3, -2, 2, 3, 4, 5, 6, 7])
        k, m = sorted(R.sample(range(2, 13), 2))
        n = R.choice([12, 15, 16, 18, 20, 24, 25, 30])
        if a1 == 0 or m - k < 2:
            continue
        return dict(type=5, a1=a1, d=d, k=k, m=m, n=n, ak=a1 + (k - 1) * d, am=a1 + (m - 1) * d)


def t5_statement(p) -> str:
    return (f"В арифметической прогрессии a{_sub(p['k'])} = {fm(p['ak'])}, a{_sub(p['m'])} = {fm(p['am'])}. "
            f"Найдите сумму первых {p['n']} членов прогрессии.")


def t5_answer(p):
    a1, d, n = p["a1"], p["d"], p["n"]
    return Fr((2 * a1 + (n - 1) * d) * n, 2)


def t5_methods(p) -> list[str]:
    return ["system", "direct"]


T5_ERR = ["restate", "sub", "d", "a1", "an", "S"]
T5_TYPO = ["sub", "a1", "an"]
T5_STRUCT = ["formula_n", "formula_s"]


def t5_gen(p, method: str, w: W):
    R = w.R
    k, m, n = p["k"], p["m"], p["n"]

    def misread(v):
        return (v[0], int(slip(R, v[1])))

    ak, am = w.step("restate", (p["ak"], p["am"]), misread,
                    lambda v: f"Дано: a{_sub(k)} = {fm(v[0])}, a{_sub(m)} = {fm(v[1])}. Найти S{_sub(n)}.")
    wrong_n = w.fault.get("step") == "formula_n"
    if wrong_n:
        w.mark()
        w.add("Формула n-го члена арифметической прогрессии: aₙ = a₁ + n·d.")
        sk, sm = k, m
    else:
        w.say("Формула n-го члена арифметической прогрессии: aₙ = a₁ + (n − 1)d.",
              "Пользуюсь формулой aₙ = a₁ + (n − 1)d.")
        sk, sm = k - 1, m - 1
    if method == "system":
        w.add(f"Составляю систему: a₁ + {sk}d = {fm(ak)} и a₁ + {sm}d = {fm(am)}.")
        w.say("Вычитаю из второго уравнения первое, a₁ при этом уничтожается:",
              "Вычту первое уравнение из второго:")
        diff = w.step("sub", am - ak, lambda v: slip(R, v),
                      lambda v: f"{sm - sk}d = {fm(am)} − {fp(ak)} = {fm(v)}")
    else:
        w.say(f"Между членами a{_sub(k)} и a{_sub(m)} ровно {m - k} {_plural(m - k, 'шаг', 'шага', 'шагов')} "
              "прогрессии, поэтому разность найду сразу.",
              "Разность можно найти без системы: от одного данного члена до другого d прибавляется "
              f"{m - k} {_plural(m - k, 'раз', 'раза', 'раз')}.")
        diff = w.step("sub", am - ak, lambda v: slip(R, v),
                      lambda v: f"a{_sub(m)} − a{_sub(k)} = {fm(am)} − {fp(ak)} = {fm(v)} = {m - k}d")
    w.say("Отсюда находится разность прогрессии.", "Делю на коэффициент при d.",
          "Осталось разделить, чтобы получить d.")
    d = w.step("d", Fr(diff, m - k), lambda v: slip(R, v), lambda v: f"d = {fm(diff)} : {m - k} = {fm(v)}")
    a1 = w.step("a1", ak - sk * d, lambda v: ak + sk * d,
                lambda v: f"a₁ = {fm(ak)} − {sk}·{fp(d)} = {fm(v)}")
    w.say("Первый член и разность известны, теперь можно считать сумму.",
          "Всё готово для суммы: известны a₁ и d.",
          "Для суммы сначала найду последний из суммируемых членов.")
    if w.fault.get("step") == "formula_s":
        w.mark()
        w.add("Сумма первых n членов: Sₙ = (a₁ + aₙ)·n.")
        half = False
    else:
        w.say("Сумма первых n членов: Sₙ = (a₁ + aₙ)/2 · n.", "Для суммы беру формулу Sₙ = (a₁ + aₙ)·n/2.")
        half = True
    an = w.step("an", a1 + (n - 1 if not wrong_n else n) * d, lambda v: slip(R, v),
                lambda v: f"a{_sub(n)} = {fm(a1)} + {n - 1 if not wrong_n else n}·{fp(d)} = {fm(v)}")
    S_true = (a1 + an) * n / 2 if half else (a1 + an) * n
    S = w.step("S", S_true, lambda v: slip(R, v),
               lambda v: f"S{_sub(n)} = ({fm(a1)} + {fp(an)}){'/2' if half else ''}·{n} = {fm(v)}")
    if not w.propagated and R.random() < 0.4:
        w.add(f"Проверка: a{_sub(k)} = {fm(a1)} + {k - 1}·{fp(d)} = {fm(a1 + (k - 1) * d)} — совпадает с условием.")
    w.add(f"Ответ: {fm(S)}.")
    return S


# ---------------------------------------------------------------------------
# Тип 6. Геометрическая прогрессия
# ---------------------------------------------------------------------------


def t6_params(R: random.Random) -> dict:
    while True:
        b1 = R.choice([-3, -2, -1, 1, 2, 3, 4, 5])
        q = R.choice([-3, -2, 2, 3])
        k = R.choice([1, 2, 3])
        gap = R.choice([2, 3])
        m = k + gap
        n = R.choice([5, 6, 7])
        if gap == 2:
            cond = "все её члены положительны" if (b1 > 0 and q > 0) else (
                "её члены чередуются по знаку" if q < 0 else None)
            if cond is None:
                continue
        else:
            cond = None
        return dict(type=6, b1=b1, q=q, k=k, m=m, n=n, bk=b1 * q ** (k - 1), bm=b1 * q ** (m - 1), cond=cond)


def t6_statement(p) -> str:
    extra = f", причём {p['cond']}" if p["cond"] else ""
    return (f"В геометрической прогрессии b{_sub(p['k'])} = {fm(p['bk'])}, b{_sub(p['m'])} = {fm(p['bm'])}{extra}. "
            f"Найдите сумму первых {p['n']} её членов.")


def t6_answer(p):
    b1, q, n = p["b1"], p["q"], p["n"]
    return Fr(b1 * (q ** n - 1), q - 1)


def t6_methods(p) -> list[str]:
    return ["formula", "list"]


T6_ERR = ["ratio", "q", "b1", "pow", "S"]
T6_TYPO = ["ratio", "b1", "pow"]
T6_STRUCT = ["formula_b"]


def t6_gen(p, method: str, w: W):
    R = w.R
    k, m, n, bk, bm = p["k"], p["m"], p["n"], p["bk"], p["bm"]
    gap = m - k
    w.add(f"Известно: b{_sub(k)} = {fm(bk)}, b{_sub(m)} = {fm(bm)}" + (f", {p['cond']}." if p["cond"] else ".")
          + f" Нужно найти S{_sub(n)}.")
    wrong_b = w.fault.get("step") == "formula_b"
    if wrong_b:
        w.mark()
        w.add("Формула n-го члена: bₙ = b₁·qⁿ.")
        ek = k
    else:
        w.say("Формула n-го члена: bₙ = b₁·qⁿ⁻¹.", "Использую формулу bₙ = b₁·qⁿ⁻¹.")
        ek = k - 1
    w.say("Если разделить один член на другой, b₁ сократится и останется степень q.",
          "Чтобы найти знаменатель, делю больший по номеру член на меньший: b₁ сокращается.")
    w.add(f"Делю b{_sub(m)} на b{_sub(k)}: q{sup(gap)} = {fm(bm)} : {fp(bk)}.")
    ratio = w.step("ratio", Fr(bm, bk), lambda v: slip(R, v), lambda v: f"q{sup(gap)} = {fm(v)}")
    qtrue = p["q"] if ratio == Fr(bm, bk) else None
    if qtrue is None:
        root = round(abs(float(ratio)) ** (1 / gap))
        if root ** gap != abs(ratio):
            raise Reject("корень")
        qtrue = root if (gap == 2 and p["q"] > 0) or (gap == 3 and ratio > 0) else -root
    if gap == 2:
        w.add(f"Отсюда q = {fm(abs(qtrue))} или q = {MINUS}{fm(abs(qtrue))}.")
        def render(v):
            if v == qtrue:
                return f"Так как {p['cond']}, подходит q = {fm(v)}."
            return f"Беру {'положительное' if v > 0 else 'отрицательное'} значение: q = {fm(v)}."
    else:
        render = lambda v: f"q = ∛{fp(ratio)} = {fm(v)}"  # noqa: E731
    q = w.step("q", qtrue, lambda v: -v, render)
    b1 = w.step("b1", Fr(bk, q ** ek), lambda v: slip(R, v),
                lambda v: f"b₁ = b{_sub(k)} : q{sup(ek) if ek != 1 else ''} = {fm(bk)} : {fp(q ** ek)} = {fm(v)}"
                if ek else f"b₁ = {fm(v)}")
    if method == "list":
        w.say("Членов немного, поэтому просто выпишу их и сложу.", "Сложу члены напрямую, их всего несколько.")
        terms = [b1 * q ** i for i in range(n)]
        w.add(", ".join(f"b{_sub(i + 1)} = {fm(t)}" for i, t in enumerate(terms)) + ".")
        S = w.step("S", sum(terms), lambda v: slip(R, v),
                   lambda v: f"S{_sub(n)} = {' + '.join(fp(t) for t in terms)} = {fm(v)}")
    else:
        w.say("Сумма первых n членов: Sₙ = b₁(qⁿ − 1)/(q − 1).", "Формула суммы: Sₙ = b₁·(qⁿ − 1)/(q − 1).")
        P = w.step("pow", q ** n, lambda v: slip(R, v), lambda v: f"q{sup(n)} = {fp(q)}{sup(n)} = {fm(v)}")
        S = w.step("S", b1 * (P - 1) / (q - 1), lambda v: slip(R, v),
                   lambda v: f"S{_sub(n)} = {fp(b1)}·({fm(P)} − 1)/({fm(q)} − 1) = {fp(b1)}·{fp(P - 1)}/{fp(q - 1)} = {fm(v)}")
    if not w.propagated and R.random() < 0.5:
        w.add(f"Проверка: b{_sub(m)} = b₁·q{sup(m - 1)} = {fp(b1)}·{fp(q ** (m - 1))} = {fm(b1 * q ** (m - 1))} — "
              "совпадает с условием.")
    w.add(f"Ответ: {fm(S)}.")
    return S


# ---------------------------------------------------------------------------
# Тип 7. Комбинаторика: выбор группы
# ---------------------------------------------------------------------------


def _C(n: int, k: int) -> int:
    return math.comb(n, k) if 0 <= k <= n else 0


def _A(n: int, k: int) -> int:
    return math.perm(n, k) if 0 <= k <= n else 0


def _cline(n: int, k: int, value, perm: bool = False) -> str:
    top = "·".join(str(n - i) for i in range(k))
    if k == 1:
        return f"{'A' if perm else 'C'}({n}, 1) = {fm(value)}"
    if perm:
        return f"A({n}, {k}) = {top} = {fm(value)}"
    bottom = "·".join(str(i + 1) for i in range(k))
    return f"C({n}, {k}) = {top}/({bottom}) = {math.prod(range(n - k + 1, n + 1))} : {math.factorial(k)} = {fm(value)}"


def t7_params(R: random.Random) -> dict:
    m, f = R.randint(5, 10), R.randint(4, 9)
    k = R.choice([3, 4, 5])
    q = R.choice(["atleast", "both", "exact"])
    j = R.randint(1, min(f, k - 1))
    if q == "both" and (f < k or m < k):
        q = "atleast"
    return dict(type=7, m=m, f=f, k=k, q=q, j=j)


def _t7_cond(p) -> str:
    if p["q"] == "atleast":
        return "есть хотя бы одна девочка"
    if p["q"] == "both":
        return "есть хотя бы один мальчик и хотя бы одна девочка"
    j = p["j"]
    return f"ровно {j} {_plural(j, 'девочка', 'девочки', 'девочек')}"


def t7_statement(p) -> str:
    m, f, k = p["m"], p["f"], p["k"]
    return (f"В классе {m} {_plural(m, 'мальчик', 'мальчика', 'мальчиков')} и {f} "
            f"{_plural(f, 'девочка', 'девочки', 'девочек')}. Сколькими способами можно выбрать команду из {k} "
            f"человек, в которой {_t7_cond(p)}? Порядок членов команды не важен.")


def t7_answer(p):
    m, f, k, j = p["m"], p["f"], p["k"], p["j"]
    N = m + f
    if p["q"] == "atleast":
        return _C(N, k) - _C(m, k)
    if p["q"] == "both":
        return _C(N, k) - _C(m, k) - _C(f, k)
    return _C(f, j) * _C(m, k - j)


def t7_methods(p) -> list[str]:
    return ["complement", "cases"] if p["q"] != "exact" else ["direct"]


T7_ERR = ["total", "boys", "girls", "diff", "cf", "cm", "prod", "case"]
T7_TYPO = ["total", "boys", "cf", "cm"]
T7_STRUCT = ["perm", "wrong_compl", "sum_rule"]


def t7_gen(p, method: str, w: W):
    R = w.R
    m, f, k, j = p["m"], p["f"], p["k"], p["j"]
    N = m + f
    w.add(f"В классе {m} {_plural(m, 'мальчик', 'мальчика', 'мальчиков')} и {f} "
          f"{_plural(f, 'девочка', 'девочки', 'девочек')}, всего {N} человек; выбираем {k}, причём {_t7_cond(p)}.")
    perm = w.fault.get("step") == "perm"
    if perm:
        w.mark()
        w.add(f"Выбрать {k} человек из n можно A(n, {k}) = n!/(n − {k})! способами.")
    else:
        w.say(f"Порядок не важен, поэтому считаю сочетаниями: C(n, {k}) = n!/({k}!·(n − {k})!).",
              "Команда — это неупорядоченный набор, значит, нужны сочетания C(n, k) = n!/(k!(n − k)!).")
    cnt = _A if perm else _C

    def cline(n, kk, v):
        return _cline(n, kk, v, perm)

    w.say("Буду аккуратно считать сочетания: сначала числитель, потом знаменатель, потом делю.",
          "Числа небольшие, поэтому посчитаю всё явно, без калькулятора.",
          "Все вычисления распишу подробно, чтобы было видно, откуда берутся числа.")
    if p["q"] == "exact":
        w.say(f"Девочек выбираю отдельно от мальчиков: {j} из {f} и {k - j} из {m}.",
              f"Нужно выбрать {j} из {f} девочек и ещё {k - j} из {m} мальчиков.")
        cf = w.step("cf", cnt(f, j), lambda v: slip(R, v, allow_sign=False), lambda v: cline(f, j, v))
        cm = w.step("cm", cnt(m, k - j), lambda v: slip(R, v, allow_sign=False), lambda v: cline(m, k - j, v))
        if w.fault.get("step") == "sum_rule":
            w.mark()
            w.add(f"Выбираем либо девочек, либо мальчиков, поэтому по правилу суммы: {fm(cf)} + {fm(cm)} = {fm(cf + cm)}.")
            ans = cf + cm
        else:
            ans = w.step("prod", cf * cm, lambda v: slip(R, v, allow_sign=False),
                         lambda v: f"Каждый выбор девочек сочетается с каждым выбором мальчиков, по правилу "
                                   f"произведения: {fm(cf)}·{fm(cm)} = {fm(v)}.")
    elif method == "complement":
        w.say("Удобнее посчитать все команды и вычесть неподходящие.",
              "Посчитаю через дополнение: из всех команд уберу те, что не подходят.")
        total = w.step("total", cnt(N, k), lambda v: slip(R, v, allow_sign=False),
                       lambda v: "Всего команд: " + cline(N, k, v))
        if w.fault.get("step") == "wrong_compl":
            w.mark()
            w.add("Команды без девочек составлены из одних девочек: " + cline(f, k, cnt(f, k)) + ".")
            boys = cnt(f, k)
        else:
            boys = w.step("boys", cnt(m, k), lambda v: slip(R, v, allow_sign=False),
                          lambda v: "Команды без девочек — только из мальчиков: " + cline(m, k, v))
        sub = boys
        if p["q"] == "both":
            girls = w.step("girls", cnt(f, k), lambda v: slip(R, v, allow_sign=False),
                           lambda v: "Команды без мальчиков — только из девочек: " + cline(f, k, v))
            sub = boys + girls
            ans = w.step("diff", total - sub, lambda v: slip(R, v),
                         lambda v: f"Подходящих команд: {fm(total)} − {fm(boys)} − {fm(girls)} = {fm(v)}.")
        else:
            ans = w.step("diff", total - sub, lambda v: slip(R, v),
                         lambda v: f"Подходящих команд: {fm(total)} − {fm(boys)} = {fm(v)}.")
    else:
        lo = 1
        hi = min(f, k) if p["q"] == "atleast" else min(f, k - 1)
        w.say(f"Переберу случаи по числу девочек в команде: от {lo} до {hi}.",
              f"Разберу отдельно случаи с {lo}–{hi} девочками в команде и сложу результаты.")
        parts = []
        L = "A" if perm else "C"
        for g in range(lo, hi + 1):
            val = cnt(f, g) * cnt(m, k - g)
            name = "case" if g == lo else f"case{g}"
            got = w.step(name, val, lambda v: slip(R, v, allow_sign=False),
                         lambda v, g=g: f"{g} {_plural(g, 'девочка', 'девочки', 'девочек')}: "
                                        f"{L}({f}, {g})·{L}({m}, {k - g}) = {fm(cnt(f, g))}·{fm(cnt(m, k - g))} = {fm(v)}")
            parts.append(got)
        ans = sum(parts)
        w.add(f"Всего: {' + '.join(fm(x) for x in parts)} = {fm(ans)}.")
    if method == "complement" and not w.propagated and R.random() < 0.5:
        hi = min(f, k) if p["q"] == "atleast" else min(f, k - 1)
        terms = [_C(f, g) * _C(m, k - g) for g in range(1, hi + 1)]
        w.add(f"Проверю перебором по числу девочек: {' + '.join(fm(t) for t in terms)} = {fm(sum(terms))} — "
              "сходится.")
    w.add(f"Ответ: {fm(ans)}.")
    return ans


# ---------------------------------------------------------------------------
# Тип 8. Движение навстречу
# ---------------------------------------------------------------------------

T8_PAIRS = [
    ("велосипедист", "велосипедиста", "мотоциклист", "мотоциклиста", (12, 20), (15, 30)),
    ("автобус", "автобуса", "легковой автомобиль", "легкового автомобиля", (40, 60), (10, 30)),
    ("пешеход", "пешехода", "велосипедист", "велосипедиста", (4, 6), (8, 12)),
    ("грузовик", "грузовика", "мотоциклист", "мотоциклиста", (45, 60), (10, 25)),
    ("теплоход", "теплохода", "катер", "катера", (18, 26), (6, 14)),
]
T8_MIN = [12, 15, 20, 24, 30, 36, 40, 45, 48]


def t8_params(R: random.Random) -> dict:
    while True:
        pair = R.randrange(len(T8_PAIRS))
        _, _, _, _, (xlo, xhi), (vlo, vhi) = T8_PAIRS[pair]
        x = R.randint(xlo, xhi)
        v = R.randint(vlo, vhi)
        h, mm = R.choice([0, 1, 1, 2]), R.choice(T8_MIN)
        T = h + Fr(mm, 60)
        S = (2 * x + v) * T
        if S.denominator != 1 or (h == 0 and mm < 30):
            continue
        return dict(type=8, pair=pair, x=x, v=v, h=h, mm=mm, S=int(S))


def _t8_time(h, mm) -> str:
    return f"{h} ч {mm} мин" if h else f"{mm} мин"


def t8_statement(p) -> str:
    s_nom, s_gen, f_nom, f_gen, *_ = T8_PAIRS[p["pair"]]
    return (f"Из двух пунктов, расстояние между которыми {p['S']} км, одновременно навстречу друг другу "
            f"отправились {s_nom} и {f_nom}. Скорость {f_gen} на {p['v']} км/ч больше скорости {s_gen}. "
            f"Они встретились через {_t8_time(p['h'], p['mm'])}. Найдите скорость {s_gen} (в км/ч).")


def t8_answer(p):
    return Fr(p["x"])


def t8_methods(p) -> list[str]:
    return ["equation", "actions"]


T8_ERR = ["restate", "time", "div", "minus", "half"]
T8_TYPO = ["div", "minus"]


def _num(v) -> str:
    return dec(v)


def t8_gen(p, method: str, w: W):
    R = w.R
    s_nom, s_gen, f_nom, f_gen, *_ = T8_PAIRS[p["pair"]]

    def misread(sv):
        return (sv[0], int(slip(R, sv[1], allow_sign=False)))

    S, v = w.step("restate", (p["S"], p["v"]), misread,
                  lambda sv: f"Дано: расстояние {sv[0]} км, до встречи прошло {_t8_time(p['h'], p['mm'])}, "
                             f"скорость {f_gen} на {sv[1]} км/ч больше скорости {s_gen}.")
    h, mm = p["h"], p["mm"]
    T_true = h + Fr(mm, 60)
    w.say("Скорости даны в км/ч, поэтому время обязательно нужно перевести в часы.",
          "Время дано в часах и минутах, а скорость — в км/ч; единицы надо согласовать.",
          "Сначала единицы измерения, иначе ответ получится неверным.")

    def bad_time(_):
        return h + Fr(mm, 100)

    T = w.step("time", T_true, bad_time,
               lambda t: f"Перевожу время в часы: {_t8_time(h, mm)} = {h + Fr(mm, 60) if False else ''}"
                         f"{(str(h) + ' + ') if h else ''}{mm}/60 ч = {_num(t)} ч." if t == T_true else
               f"Перевожу время в часы: {_t8_time(h, mm)} = {_num(t)} ч.")
    if method == "equation":
        w.add(f"Пусть x км/ч — скорость {s_gen}, тогда скорость {f_gen} равна (x + {v}) км/ч.")
        w.say(f"Они движутся навстречу, поэтому скорость сближения равна x + (x + {v}) = (2x + {v}) км/ч.",
              f"При движении навстречу скорости складываются: x + x + {v} = 2x + {v} (км/ч).")
        w.add(f"За {_num(T)} ч они вместе проехали всё расстояние: (2x + {v})·{_num(T)} = {S}.")
        q = w.step("div", Fr(S) / T, lambda z: slip(R, z, allow_sign=False),
                   lambda z: f"2x + {v} = {S} : {_num(T)} = {_num(z)}")
        r2 = w.step("minus", q - v, lambda z: q + v if R.random() < 0.5 else slip(R, z, allow_sign=False),
                    lambda z: f"2x = {_num(q)} − {v} = {_num(z)}")
        x = w.step("half", r2 / 2, lambda z: slip(R, z, allow_sign=False),
                   lambda z: f"x = {_num(r2)} : 2 = {_num(z)}")
    else:
        w.say("Решу по действиям.", "Решу без уравнения, по действиям.")
        q = w.step("div", Fr(S) / T, lambda z: slip(R, z, allow_sign=False),
                   lambda z: f"1) Скорость сближения: {S} : {_num(T)} = {_num(z)} (км/ч).")
        r2 = w.step("minus", q - v, lambda z: q + v if R.random() < 0.5 else slip(R, z, allow_sign=False),
                    lambda z: f"2) Если бы {f_nom} ехал со скоростью {s_gen}, скорость сближения была бы на {v} км/ч "
                              f"меньше: {_num(q)} − {v} = {_num(z)} (км/ч).")
        x = w.step("half", r2 / 2, lambda z: slip(R, z, allow_sign=False),
                   lambda z: f"3) Это удвоенная скорость {s_gen}: {_num(r2)} : 2 = {_num(z)} (км/ч).")
    if x <= 0:
        raise Reject("скорость")
    if not w.propagated and R.random() < 0.5:
        a, b = x * T, (x + v) * T
        w.add(f"Проверка: за {_num(T)} ч {s_nom} проедет {_num(x)}·{_num(T)} = {_num(a)} км, {f_nom} — "
              f"{_num(x + v)}·{_num(T)} = {_num(b)} км, вместе {_num(a + b)} км. Верно.")
    w.add(f"Ответ: {_num(x)} км/ч.")
    return x


# ---------------------------------------------------------------------------
# Реестр типов
# ---------------------------------------------------------------------------

TYPES = {
    1: dict(params=t1_params, statement=t1_statement, answer=t1_answer, methods=t1_methods, gen=t1_gen,
            err=T1_ERR, typo=T1_TYPO, struct=[]),
    2: dict(params=t2_params, statement=t2_statement, answer=t2_answer, methods=t2_methods, gen=t2_gen,
            err=T2_ERR, typo=T2_TYPO, struct=[]),
    3: dict(params=t3_params, statement=t3_statement, answer=t3_answer, methods=t3_methods, gen=t3_gen,
            err=T3_ERR, typo=T3_TYPO, struct=T3_STRUCT),
    4: dict(params=t4_params, statement=t4_statement, answer=t4_answer, methods=t4_methods, gen=t4_gen,
            err=T4_ERR, typo=T4_TYPO, struct=T4_STRUCT),
    5: dict(params=t5_params, statement=t5_statement, answer=t5_answer, methods=t5_methods, gen=t5_gen,
            err=T5_ERR, typo=T5_TYPO, struct=T5_STRUCT),
    6: dict(params=t6_params, statement=t6_statement, answer=t6_answer, methods=t6_methods, gen=t6_gen,
            err=T6_ERR, typo=T6_TYPO, struct=T6_STRUCT),
    7: dict(params=t7_params, statement=t7_statement, answer=t7_answer, methods=t7_methods, gen=t7_gen,
            err=T7_ERR, typo=T7_TYPO, struct=T7_STRUCT),
    8: dict(params=t8_params, statement=t8_statement, answer=t8_answer, methods=t8_methods, gen=t8_gen,
            err=T8_ERR, typo=T8_TYPO, struct=[]),
}
PART_LETTERS = ["а", "б", "в", "г"]
PART_INTRO = {
    1: ["Линейное уравнение с дробями, после него — подстановка в выражение.",
        "Здесь уравнение с дробями; решаю его, а потом считаю значение выражения.",
        "Уравнение первой степени, но с тремя дробями."],
    2: ["Квадратное уравнение, записанное со скобками.", "Тут квадратное уравнение, его надо сначала упростить.",
        "Уравнение сводится к квадратному."],
    3: ["Уравнение, где в обеих частях есть одинаковый множитель.",
        "Уравнение с общим множителем в левой и правой частях.", "Уравнение с повторяющейся скобкой."],
    4: ["Дробно-рациональное уравнение, здесь важен знаменатель.", "Уравнение с переменной в знаменателе.",
        "Дробное уравнение, одинаковые знаменатели."],
    5: ["Задача на арифметическую прогрессию.", "Арифметическая прогрессия, известны два члена.",
        "Прогрессия арифметическая, нужна сумма."],
    6: ["Задача на геометрическую прогрессию.", "Геометрическая прогрессия: известны два члена, нужна сумма.",
        "Геометрическая прогрессия."],
    7: ["Задача по комбинаторике.", "Комбинаторная задача про выбор команды.", "Задача на сочетания."],
    8: ["Текстовая задача на движение.", "Задача на движение навстречу друг другу.",
        "Текстовая задача, движение."],
}
N_PROBLEMS = 40
N_PARTS = 4
N_SOLUTIONS = 150

FIRST_M = ["Андрей", "Мария", "Илья", "Дарья", "Кирилл", "Анна", "Тимофей", "Софья", "Егор", "Алиса",
           "Матвей", "Полина", "Артём", "Вероника", "Глеб", "Ульяна", "Фёдор", "Ева", "Семён", "Злата"]
LAST = [("Ковалёв", "Ковалёва"), ("Смирнов", "Смирнова"), ("Орлов", "Орлова"), ("Никитин", "Никитина"),
        ("Белов", "Белова"), ("Жуков", "Жукова"), ("Громов", "Громова"), ("Лебедев", "Лебедева"),
        ("Зайцев", "Зайцева"), ("Макаров", "Макарова"), ("Фролов", "Фролова"), ("Соколов", "Соколова")]
CLASSES = ["9 «А»", "9 «Б»", "9 «В»", "10 «А»", "10 «Б»"]


@functools.cache
def build_problems() -> list[dict]:
    R = rng(TASK_ID + "/problems")
    counts = {t: 0 for t in TYPES}
    problems = []
    for i in range(N_PROBLEMS):
        order = sorted(TYPES, key=lambda t: (counts[t], R.random()))
        types = order[:N_PARTS]
        R.shuffle(types)
        parts = []
        for t in types:
            counts[t] += 1
            parts.append(TYPES[t]["params"](R))
        problems.append(dict(no=i + 1, parts=parts))
    return problems


def _answer_text(v) -> str:
    return fm(v)


def _render_solution(prob: dict, methods: list[str], fault: dict | None, seed: str, female: bool):
    """Порождает строки решения. fault: {'part': i, 'step': ..., 'kind': ...}."""
    R = rng(seed)
    part_fault = None
    w = W(R, None, female)
    w.say(f"Решаю задачу {prob['no']} по пунктам.", f"Задача {prob['no']}. Пункты решаю по порядку.",
          f"Решение задачи {prob['no']}, все четыре пункта.")
    answers = []
    for i, (p, meth) in enumerate(zip(prob["parts"], methods, strict=True)):
        w.add(f"Пункт {PART_LETTERS[i]}). " + R.choice(PART_INTRO[p["type"]]))
        w.fault = {k: v for k, v in fault.items() if k != "part"} if fault and fault["part"] == i else {}
        if w.fault:
            part_fault = i
        answers.append(TYPES[p["type"]]["gen"](p, meth, w))
    w.fault = {}
    w.say(f"Все пункты решены, ещё раз просмотрел{w.g('', 'а')} выкладки.",
          "Задача решена полностью.", "На этом всё, выписываю ответы по всем пунктам.")
    w.add("Итоговые ответы: " + "; ".join(f"{PART_LETTERS[i]}) {_answer_text(a)}" for i, a in enumerate(answers))
          + ".")
    return w, answers, part_fault


def _true_answers(prob) -> list:
    return [Fr(TYPES[p["type"]]["answer"](p)) for p in prob["parts"]]


INTENTS = {"correct": 32, "alt": 18, "retract": 14, "wrong": 52, "flawed": 34}
EXPECT = {"correct": "correct", "alt": "correct", "retract": "correct", "wrong": "wrong_answer",
          "flawed": "flawed_reasoning"}


def _flawed_structs(p) -> list[str]:
    """Структурные ошибки, которые в этой задаче не меняют ответ."""
    t = p["type"]
    if t == 2 and p["q"] in ("sum_sq", "diff"):
        return ["root_sign"]
    if t == 3 and p["q"] == "max" and p["roots"][1] > p["roots"][0]:
        return ["divide"]
    if t == 4 and ((p["q"] == "min" and p["s"] < p["r"]) or (p["q"] == "max" and p["s"] > p["r"])):
        return ["no_odz"]
    return []


def _plan(R: random.Random, prob: dict, intent: str) -> tuple[list[str], dict | None]:
    parts = prob["parts"]
    methods = []
    for p in parts:
        ms = TYPES[p["type"]]["methods"](p)
        methods.append(ms[0] if len(ms) == 1 or R.random() < 0.75 else R.choice(ms[1:]))
    if intent == "alt":
        cand = [i for i, p in enumerate(parts) if len(TYPES[p["type"]]["methods"](p)) > 1]
        i = R.choice(cand)
        methods[i] = R.choice(TYPES[parts[i]["type"]]["methods"](parts[i])[1:])
    if intent in ("correct", "alt"):
        return methods, None
    i = R.randrange(len(parts))
    spec = TYPES[parts[i]["type"]]
    if intent == "retract":
        return methods, dict(part=i, step=R.choice(spec["typo"]), kind="retract")
    if intent == "flawed":
        cand = [j for j, p in enumerate(parts) if _flawed_structs(p)]
        if cand and R.random() < 0.5:
            i = R.choice(cand)
            spec = TYPES[parts[i]["type"]]
        fs = _flawed_structs(parts[i])
        if fs and R.random() < 0.8:
            step = fs[0]
            return methods, dict(part=i, step=step, kind="err" if step == "root_sign" else "struct")
        return methods, dict(part=i, step=R.choice(spec["typo"]), kind="typo")
    if spec["struct"] and R.random() < 0.35:
        return methods, dict(part=i, step=R.choice(spec["struct"]), kind="struct")
    step = R.choice(spec["err"])
    return methods, dict(part=i, step=step, kind="err")


def _first_diff(a: list[str], b: list[str]) -> int | None:
    for n, (x, y) in enumerate(zip(a, b, strict=False), 1):
        if x != y:
            return n
    return None if len(a) == len(b) else min(len(a), len(b)) + 1


@functools.cache
def build() -> list[dict]:
    problems = build_problems()
    R = rng(TASK_ID + "/plan")
    slots = [i for i in range(N_PROBLEMS) for _ in range(3)]
    slots += R.sample(range(N_PROBLEMS), N_SOLUTIONS - len(slots))
    R.shuffle(slots)
    intents = [k for k, n in INTENTS.items() for _ in range(n)]
    R.shuffle(intents)
    sols = []
    for idx, (pi, intent) in enumerate(zip(slots, intents, strict=True), 1):
        prob = problems[pi]
        truth = _true_answers(prob)
        female = R.random() < 0.5
        name = f"{R.choice(FIRST_M[1::2] if female else FIRST_M[0::2])} {R.choice(LAST)[1 if female else 0]}"
        klass = R.choice(CLASSES)
        for attempt in range(400):
            seed = f"{TASK_ID}/s{idx}/{attempt}"
            methods, fault = _plan(rng(seed + "/plan"), prob, intent)
            try:
                w, answers, _ = _render_solution(prob, methods, fault, seed, female)
            except Reject:
                continue
            if fault and fault["kind"] == "retract":
                if not w.retracted:
                    continue
            elif fault and w.fault_line is None:
                continue
            ok = [Fr(a) for a in answers] == truth
            if fault is None or fault["kind"] == "retract":
                verdict = "correct" if ok else None
            else:
                verdict = "flawed_reasoning" if ok else "wrong_answer"
            if verdict != EXPECT[intent]:
                continue
            line = None
            if verdict != "correct":
                clean, clean_answers, _ = _render_solution(prob, methods, None, seed, female)
                assert [Fr(a) for a in clean_answers] == truth, "чистое решение неверно"
                line = _first_diff(w.lines, clean.lines)
                assert line == w.fault_line, (idx, line, w.fault_line, fault)
            break
        else:
            raise RuntimeError(f"не удалось построить решение {idx} ({intent})")
        sols.append(dict(id=f"s{idx:03d}", problem=prob["no"], student=name, klass=klass, lines=w.lines,
                         verdict=verdict, line=line, intent=intent, fault=fault, methods=methods,
                         retract_line=w.retract_line))
    return sols


def _solution_text(s: dict) -> str:
    who = "Ученица" if s["student"].split()[1].endswith("а") else "Ученик"
    head = f"# Решение {s['id']}\n\nЗадача: {s['problem']}\n{who}: {s['student']}, {s['klass']}\n\n"
    body = "\n".join(f"{n:>2}. {text}" for n, text in enumerate(s["lines"], 1))
    return head + body + "\n"


# ---------------------------------------------------------------------------
# Правила проверки
# ---------------------------------------------------------------------------

RULES = """\
# Правила проверки решений

Методобъединение, контрольная «Алгебра, 9–10 класс». Документ для проверяющих.

## 1. Что проверяем

В `problems.md` — 40 задач, в каждой четыре пункта (а, б, в, г). В папке
`solutions/` — решения учеников, по одному файлу на решение (`s001.md` … `s150.md`).
В шапке файла указаны номер задачи и ученик; дальше идут **пронумерованные строки**
решения. Номер строки — число в начале строки; шапка не нумеруется.

Каждый пункт решения заканчивается строкой `Ответ: …` — это ответ ученика к пункту.
В конце решения ученик ещё раз выписывает все ответы (строка «Итоговые ответы»).

Каждое решение нужно проверить целиком и поставить один вердикт.

## 2. Вердикты

* `correct` — ответы ко всем четырём пунктам верны и в решении нет ни одной неверной
  строки.
* `wrong_answer` — ответ хотя бы к одному пункту неверен.
* `flawed_reasoning` — ответы ко всем пунктам верны, но в решении есть хотя бы одна
  неверная строка (ошибки компенсировали друг друга, ошибка не повлияла на ответ,
  в промежуточной строке описка, а дальше ученик считал с верным числом, и т. п.).

Для `wrong_answer` и `flawed_reasoning` указывается **номер первой неверной строки**
во всём решении (самой ранней по номеру). Для `correct` номер не указывается.

## 3. Какая строка считается неверной

Строка неверна, если выполняется хотя бы одно из условий.

1. **Ложное вычисление или равенство**: `7·8 = 54`, `(−3)² = −9`, `96 : (−24) = 4`,
   `C(8, 2) = 8·7/(1·2) = 56 : 2 = 26`.
2. **Неверное преобразование**, не следующее из условия и предыдущих верных строк:
   потерян знак при раскрытии скобок или при переносе слагаемого через знак «=»,
   неверно приведены подобные, неверно найден дополнительный множитель, неверно
   переведены единицы (например, 1 ч 20 мин ≠ 1,2 ч).
3. **Неверная формула.** Если ученик записал неверную формулу (например,
   `aₙ = a₁ + n·d` или `bₙ = b₁·qⁿ`, сумма прогрессии без деления на 2, число
   размещений вместо числа сочетаний там, где порядок не важен, правило суммы вместо
   правила произведения), неверной считается **строка, где эта формула или это
   правило впервые записаны** (а не строка, где в неё подставили числа).
4. **Неверно переписано условие**: в строке, где ученик пересказывает данные,
   стоит не то число — эта строка неверна.
5. **Деление на выражение с переменной.** Деление (сокращение) обеих частей
   уравнения на выражение с переменной, которое может обращаться в нуль, без
   отдельного рассмотрения случая, когда оно равно нулю, — ошибка. Неверной
   считается строка, где выполнено деление, **даже если потерянный корень в итоге
   не повлиял на ответ** (например, спрашивался наибольший корень, а потерян меньший).
   Деление на ненулевое число ошибкой не является.
6. **Посторонний корень.** Умножение обеих частей на выражение с переменной само по
   себе не ошибка. Ошибка — утверждение, что число, обращающее знаменатель исходного
   уравнения в нуль, является корнем (например, «оба найденных числа — корни»).
   Неверна строка с этим утверждением.
7. **Выбор, противоречащий условию**: например, из двух значений знаменателя
   геометрической прогрессии выбрано то, которое противоречит условию, или неверно
   извлечён кубический корень.
8. **Следствия ошибки.** Строка, правильно выведенная из неверной строки, тоже
   содержит ложное утверждение, но первой ошибкой она не является. Нас интересует
   только самая ранняя неверная строка. Строка проверки, в которой ученик
   «подогнал» результат (заявил, что части равны, хотя это не так), тоже неверна.

## 4. Что ошибкой не является

* **Другой верный способ решения.** Теорема Виета вместо дискриминанта; умножение на
  произведение знаменателей вместо наименьшего общего кратного; раскрытие всех скобок
  вместо вынесения общего множителя; перебор случаев вместо подсчёта через дополнение;
  решение текстовой задачи по действиям вместо уравнения; явное выписывание и сложение
  членов прогрессии вместо формулы суммы. Нестандартное решение оценивается по тем же
  правилам: если в нём нет неверных строк и ответы верны, это `correct`.
* **Комментарии** без математических утверждений: план решения, пояснения, что
  ученик собирается делать дальше.
* **Исправленная учеником строка.** Если ученик в следующей строке явно отменил
  предыдущую («Стоп, в строке 12 я ошибся — исправляю: …», «Нет, в строке 12 неверно,
  зачёркиваю её. Верно: …», «Исправление к строке 12 …») и привёл верную строку,
  отменённая строка ошибкой **не считается**, а исправление проверяется как обычная
  строка.
* **Форма ответа.** Обыкновенная дробь вместо десятичной, `4/3` вместо `1⅓` и т. п.
  Ответ сравнивается по значению.

## 5. Порядок проверки (рекомендуемый)

1. Решите сами каждый пункт задачи и сравните ответы ученика с верными.
2. Пройдите решение по строкам сверху вниз и найдите самую раннюю неверную строку,
   учитывая разделы 3 и 4. Помните, что в `flawed_reasoning` ответы верны, а
   ошибка всё равно есть.

## 6. Формат результата

Файл `review.csv` с заголовком `solution_id,verdict,first_error_line` и строкой на
каждое решение:

```
solution_id,verdict,first_error_line
s001,correct,
s002,wrong_answer,17
s003,flawed_reasoning,42
```

`solution_id` — имя файла без расширения; `verdict` — `correct`, `wrong_answer` или
`flawed_reasoning`; `first_error_line` — целое число (пусто для `correct`).
"""


def _problems_md() -> str:
    out = ["# Задачи контрольной работы\n",
           "Каждая задача состоит из четырёх пунктов. Ответ к пункту — число (целое или "
           "обыкновенная дробь).\n"]
    for prob in build_problems():
        out.append(f"\n## Задача {prob['no']}\n")
        for i, p in enumerate(prob["parts"]):
            out.append(f"\n{PART_LETTERS[i]}) {TYPES[p['type']]['statement'](p)}\n")
    return "".join(out)


# ---------------------------------------------------------------------------
# setup / gold / check
# ---------------------------------------------------------------------------


def setup(ws: Path) -> None:
    write(ws, "rules.md", RULES)
    write(ws, "problems.md", _problems_md())
    for s in build():
        write(ws, f"solutions/{s['id']}.md", _solution_text(s))


def _write_review(ws: Path, rows: list[tuple[str, str, object]]) -> None:
    write_csv(ws, "review.csv", ["solution_id", "verdict", "first_error_line"],
              [(sid, v, "" if line is None else line) for sid, v, line in rows])


def gold(ws: Path) -> None:
    _write_review(ws, [(s["id"], s["verdict"], s["line"]) for s in build()])


def check(ws: Path) -> str:
    rows = read_csv(ws, "review.csv", required=("solution_id", "verdict", "first_error_line"))
    got: dict[str, tuple[str, str]] = {}
    for n, row in enumerate(rows, 2):
        sid = row["solution_id"].strip().removesuffix(".md")
        verdict = row["verdict"].strip().casefold()
        assert verdict in VERDICTS, f"review.csv:{n}: недопустимый вердикт {row['verdict']!r}"
        got[sid] = (verdict, row["first_error_line"].strip())
    sols = build()
    v_ok = 0
    v_err = []
    wrong_total = 0
    l_ok = 0
    l_err = []
    for s in sols:
        verdict, line = got.get(s["id"], ("", ""))
        if verdict == s["verdict"]:
            v_ok += 1
        elif len(v_err) < 5:
            v_err.append(s["id"])
        if s["verdict"] != "correct":
            wrong_total += 1
            try:
                val = int(float(line)) if line else None
            except ValueError:
                val = None
            if val == s["line"]:
                l_ok += 1
            elif len(l_err) < 5:
                l_err.append(s["id"])
    note1 = require_share(len(sols), v_ok, min_share=0.95, what="вердикты", errors=v_err)
    note2 = require_share(wrong_total, l_ok, min_share=0.90, what="строка первой ошибки", errors=l_err)
    return f"{note1}; {note2}"


# ---------------------------------------------------------------------------
# Ближние промахи
# ---------------------------------------------------------------------------


def naive_answers_only(ws: Path) -> None:
    """Сравнить итоговые ответы с верными; строку ошибки взять из строки «Ответ» пункта."""
    problems = build_problems()
    rows = []
    for s in build():
        truth = _true_answers(problems[s["problem"] - 1])
        answers = []
        answer_lines = []
        for n, text in enumerate(s["lines"], 1):
            if text.startswith("Ответ: "):
                raw = text[len("Ответ: "):].split()[0].rstrip(".").replace(MINUS, "-").replace(",", ".")
                answers.append(Fr(raw))
                answer_lines.append(n)
        bad = [i for i, (a, t) in enumerate(zip(answers, truth, strict=False)) if a != t]
        if bad:
            rows.append((s["id"], "wrong_answer", answer_lines[bad[0]]))
        else:
            rows.append((s["id"], "correct", None))
    _write_review(ws, rows)


def off_by_one(ws: Path) -> None:
    """Номер строки-следствия вместо строки с ошибкой."""
    _write_review(ws, [(s["id"], s["verdict"], None if s["line"] is None else s["line"] + 1) for s in build()])


def flawed_as_wrong(ws: Path) -> None:
    """Любая ошибка в выкладке считается неверным ответом."""
    _write_review(ws, [(s["id"], "wrong_answer" if s["verdict"] != "correct" else "correct", s["line"])
                       for s in build()])


def retract_counted(ws: Path) -> None:
    """Отменённая учеником строка засчитана как ошибка."""
    rows = []
    for s in build():
        if s["retract_line"]:
            rows.append((s["id"], "flawed_reasoning", s["retract_line"]))
        else:
            rows.append((s["id"], s["verdict"], s["line"]))
    _write_review(ws, rows)


NEAR_MISSES = [naive_answers_only, off_by_one, flawed_as_wrong, retract_counted]


TASK = long_task(
    id="task_411_math_solutions_check",  # registry id; TASK_ID stays the generator seed
    name="Проверка 150 ученических решений: вердикт и первая неверная строка",
    prompt=(
        "В problems.md — 40 задач контрольной работы (в каждой четыре пункта), в папке solutions/ —"
        " 150 решений учеников (s001.md … s150.md) с пронумерованными строками. Проверь каждое"
        " решение по правилам rules.md: поставь вердикт correct, wrong_answer или flawed_reasoning"
        " и для неверных решений укажи номер первой неверной строки. Решения написаны свободно,"
        " встречаются нестандартные способы и исправления, сделанные самими учениками; правила"
        " определяют, что считать ошибкой, и учитывать нужно каждое решение.\n\n"
        "Результат запиши в review.csv в корне рабочей папки с заголовком"
        " solution_id,verdict,first_error_line — по строке на каждое решение (solution_id — имя"
        " файла без .md, для correct поле first_error_line оставь пустым). Входные файлы не изменяй."
    ),
    setup=setup,
    gold=gold,
    check=check,
    tags=("reading", "math", "review"),
)
