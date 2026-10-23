"""Wave 20: general data workflows descended from lite tasks 352--358.

Reference programs are never copied to task inputs. Small independent oracles and
hand-derived anchors live in the accompanying tests.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
import re
from collections import Counter, defaultdict
from textwrap import dedent

from harness_bench.general_hard.common import artifact_task, dumps, program_task


def _cli(source):
    return (
        dedent(source).strip()
        + '\n\nimport json, sys\nfor line in sys.stdin:\n    print(json.dumps(solve(json.loads(line)), ensure_ascii=False, separators=(",", ":")))\n'
    )


# 475: bounded but globally coupled session matching. Gold searches by start;
# the oracle enumerates subsets of candidate edges and checks all constraints.
MATCH_GOLD = _cli("""
def solve(x):
    starts = sorted(x['starts'], key=lambda a: a['id'])
    ends = sorted(x['ends'], key=lambda a: a['id'])
    options = []
    for a in starts:
        options.append([b for b in ends if a['kind'] in b['accept'] and a['min'] <= b['at']-a['at'] <= a['max']])
    best = None
    best_pairs = []
    def visit(i, used, pairs, cost):
        nonlocal best, best_pairs
        if best is not None and len(pairs)+len(starts)-i < -best[0]:
            return
        if i == len(starts):
            key = (-len(pairs), cost, tuple((a['id'], b['id']) for a,b in pairs))
            if best is None or key < best:
                best, best_pairs = key, pairs[:]
            return
        a = starts[i]
        for b in options[i]:
            if b['id'] in used:
                continue
            intervals = [(p['at'], q['at']) for p,q in pairs if p['channel']==a['channel']]
            intervals.append((a['at'], b['at']))
            times = sorted({t for l,r in intervals for t in (l,r)})
            if any(sum(l<=t<r for l,r in intervals)>x['capacity'][a['channel']] for t in times):
                continue
            visit(i+1, used|{b['id']}, pairs+[(a,b)], cost+(b['at']-a['at']-a['target'])**2)
        visit(i+1, used, pairs, cost)
    visit(0,set(),[],0)
    used_a={a['id'] for a,b in best_pairs}
    used_b={b['id'] for a,b in best_pairs}
    return {'pairs': [[a['id'],b['id']] for a,b in best_pairs], 'cost':best[1],
            'unmatched_starts':[a['id'] for a in starts if a['id'] not in used_a],
            'unmatched_ends':[b['id'] for b in ends if b['id'] not in used_b]}
""")


def match_oracle(x):
    """Independent exhaustive edge-subset oracle (fixtures have <=15 edges)."""
    edges = sorted(
        (a["id"], b["id"], a, b)
        for a in x["starts"]
        for b in x["ends"]
        if a["kind"] in b["accept"] and a["min"] <= b["at"] - a["at"] <= a["max"]
    )
    best = None
    answer = None
    for size in range(min(len(x["starts"]), len(x["ends"])), -1, -1):
        for subset in itertools.combinations(edges, size):
            if len({e[0] for e in subset}) != size or len({e[1] for e in subset}) != size:
                continue
            good = True
            for channel, cap in x["capacity"].items():
                # Integer endpoints: occupancy can instead be checked at every unit cell.
                selected = [e for e in subset if e[2]["channel"] == channel]
                for t in range(
                    min([e[2]["at"] for e in selected] or [0]),
                    max([e[3]["at"] for e in selected] or [0]),
                ):
                    if sum(e[2]["at"] <= t < e[3]["at"] for e in selected) > cap:
                        good = False
                        break
            if not good:
                continue
            cost = sum((e[3]["at"] - e[2]["at"] - e[2]["target"]) ** 2 for e in subset)
            pairs = [[e[0], e[1]] for e in subset]
            key = (cost, pairs)
            if best is None or key < best:
                best = key
                answer = {
                    "pairs": pairs,
                    "cost": cost,
                    "unmatched_starts": sorted(
                        a["id"] for a in x["starts"] if a["id"] not in {e[0] for e in subset}
                    ),
                    "unmatched_ends": sorted(
                        b["id"] for b in x["ends"] if b["id"] not in {e[1] for e in subset}
                    ),
                }
        if answer is not None:
            return answer
    raise AssertionError("empty matching is feasible")


def _matching_cases():
    result = []
    for seed in range(16):
        rng = random.Random(475000 + seed)
        starts = [
            {
                "id": f"s{i}",
                "at": rng.randrange(-2, 7),
                "kind": "ab"[i % 2],
                "channel": "xy"[i % 2],
                "min": rng.randrange(2),
                "max": rng.randrange(3, 8),
                "target": rng.randrange(5),
            }
            for i in range(seed % 3 + 3)
        ]
        ends = [
            {
                "id": f"e{i}",
                "at": rng.randrange(0, 11),
                "accept": (["a", "b"] if i % 3 == 0 else ["ab"[i % 2]]),
            }
            for i in range(seed % 3 + 3)
        ]
        data = {"starts": starts, "ends": ends, "capacity": {"x": 1 + seed % 2, "y": 1}}
        if seed == 0:
            data = {
                "starts": [
                    {
                        "id": "a",
                        "at": 0,
                        "kind": "k",
                        "channel": "c",
                        "min": 0,
                        "max": 9,
                        "target": 4,
                    },
                    {
                        "id": "b",
                        "at": 2,
                        "kind": "k",
                        "channel": "c",
                        "min": 0,
                        "max": 9,
                        "target": 2,
                    },
                ],
                "ends": [
                    {"id": "x", "at": 4, "accept": ["k"]},
                    {"id": "y", "at": 6, "accept": ["k"]},
                ],
                "capacity": {"c": 1},
            }
        if seed == 1:
            data = {
                "starts": [
                    {
                        "id": "a",
                        "at": 0,
                        "kind": "k",
                        "channel": "c",
                        "min": 0,
                        "max": 9,
                        "target": 4,
                    },
                    {
                        "id": "b",
                        "at": 2,
                        "kind": "k",
                        "channel": "c",
                        "min": 0,
                        "max": 9,
                        "target": 2,
                    },
                ],
                "ends": [
                    {"id": "x", "at": 4, "accept": ["k"]},
                    {"id": "y", "at": 6, "accept": ["k"]},
                ],
                "capacity": {"c": 2},
            }
        result.append((data, match_oracle(data)))
        if seed in (3, 7, 11):
            perm = json.loads(json.dumps(data))
            rng.shuffle(perm["starts"])
            rng.shuffle(perm["ends"])
            result.append((perm, match_oracle(perm)))
    result.append(
        (
            {"starts": [], "ends": [], "capacity": {}},
            {"pairs": [], "cost": 0, "unmatched_starts": [], "unmatched_ends": []},
        )
    )
    return result


TASK_475 = program_task(
    number=475,
    slug="capacity_session_matching",
    name="Reconstruct sessions under shared capacity",
    parent=353,
    prompt="""Восстанови неоднозначные сессии по двум выгрузкам. Вход: starts, ends, capacity.
Каждый start имеет уникальный id, целые at,min,max,target (0<=min<=max), kind и channel.
Каждый end имеет уникальный id, целое at и массив accept допустимых kind. Id — непустые ASCII строки.
Пара (start,end) допустима, если kind входит в accept и min<=end.at-start.at<=max.
Каждый объект может участвовать максимум в одной паре. Кроме этого, в любой момент число
выбранных интервалов [start.at,end.at) одного channel не превышает capacity[channel].
Нулевая длительность не занимает канал. Все каналы starts присутствуют в capacity, вместимость>=1.
Нужно глобальное оптимальное сопоставление: сначала максимум числа пар, затем минимум суммы
(end.at-start.at-target)^2, затем лексикографически минимальный список пар [start_id,end_id],
отсортированный по start_id (обычное сравнение строк/списков Python). Это НЕ FIFO:
локально лучшая пара может мешать лучшему полному набору. Порядок входов не имеет значения.
Выход ровно {pairs, cost, unmatched_starts, unmatched_ends}; cost — целое, непарные id по возрастанию.
Размеры: до 8 starts и 8 ends, at от -100 до100, остальные числовые поля<=200.
Ни пропуск start, ни непарный end сами по себе не расходуют вместимость.""",
    cases=_matching_cases(),
    gold=MATCH_GOLD,
    tags=("sessions", "optimization", "intervals"),
)


# 484: recursive ordered-set parser. Expected examples are hand-derived, not
# executed through the reference parser.
RANGE_GOLD = _cli(r"""
import re
class Bad(Exception): pass
class Parser:
    def __init__(self,s,env): self.s=s; self.p=0; self.env=env
    def ws(self):
        while self.p<len(self.s) and self.s[self.p] in ' \t\r\n': self.p+=1
    def take(self,t):
        self.ws()
        if self.s.startswith(t,self.p): self.p+=len(t); return True
        return False
    def number(self):
        self.ws(); m=re.match(r'-?(?:0|[1-9][0-9]*)',self.s[self.p:])
        if not m: raise Bad()
        self.p+=len(m.group()); return int(m.group())
    def atom(self):
        if self.take('('):
            value=self.expr()
            if not self.take(')'): raise Bad()
        elif self.take('$'):
            self.ws(); m=re.match(r'[A-Za-z_][A-Za-z_0-9]*',self.s[self.p:])
            if not m or m.group() not in self.env: raise Bad()
            self.p+=len(m.group()); value=self.env[m.group()][:]
        else:
            a=self.number(); value=[a]
            if self.take('..'):
                b=self.number(); step=1
                if self.take('/'):
                    step=self.number()
                    if step<=0: raise Bad()
                value=list(range(a,b+(1 if b>=a else -1),(1 if b>=a else -1)*step))
        while True:
            if self.take('~'): value=value[::-1]
            elif self.take('['):
                a=self.number()
                if not self.take(':'): raise Bad()
                b=self.number()
                step=1
                if self.take(':'): step=self.number()
                if step==0 or not self.take(']'): raise Bad()
                value=value[a:b:step]
            else: break
        return value
    def intersect(self):
        value=self.atom()
        while self.take('&'):
            other=set(self.atom()); value=[v for v in value if v in other]
        return value
    def expr(self):
        value=self.intersect()
        while True:
            if self.take('|'):
                other=self.intersect(); seen=set(value)
                for v in other:
                    if v not in seen: value.append(v); seen.add(v)
            elif self.take('\\'):
                other=set(self.intersect()); value=[v for v in value if v not in other]
            elif self.take('^'):
                other=self.intersect(); left=set(value); right=set(other)
                value=[v for v in value if v not in right]+[v for v in other if v not in left]
            else: return value
    def all(self):
        value=self.expr(); self.ws()
        if self.p!=len(self.s): raise Bad()
        return value

def solve(x):
    try:
        env={}
        for definition in x['definitions']:
            name=definition['name']
            if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',name) or name in env: raise Bad()
            env[name]=Parser(definition['expr'],env).all()
        return {'values':Parser(x['expr'],env).all()}
    except Bad:
        return {'error':'invalid'}
""")


def _range_cases():
    raw = [
        ([], "1..7/2 | 6..2/2", [1, 3, 5, 7, 6, 4, 2]),
        ([("a", "1..5"), ("b", "$a~[1:4]")], "$b \\ 3 | 3", [4, 2, 3]),
        ([], "1..5 \\ 2..4 & 3..6", [1, 2, 5]),
        ([], "(1..5 \\ 2..4) & 3..6", [5]),
        ([], "1..4 ^ 4..2", [1]),
        ([], "(1..4 ^ 4..6) | 4", [1, 2, 3, 5, 6, 4]),
        ([], "-4..3/2~[0:3]", [2, 0, -2]),
        ([], "8..-2/3[-1:-5:-2]", [-1, 5]),
        ([("x", "0..9"), ("y", "$x[2:8:2]"), ("z", "$y~ | 1")], "$z ^ ($x & 4..7)", [2, 1, 5, 7]),
        ([], "1 | 1 | (2 | 1) \\ 1 | 1", [2, 1]),
        ([], "1..9[1:8:2]~[0:2] ^ 8..2/2", [4, 2]),
        ([], "((5..1/2)~)[0:99] & (3|5|9)", [3, 5]),
        ([], " -0 | 0 | -2 .. 2 / 2 ", [0, -2, 2]),
        ([], "(1..3)[4:0:-1]", [3, 2]),
        ([], "1..3[3:0:-1]~", [2, 3]),
        ([], "1..3/0", None),
        ([], "1/2", None),
        ([], "01", None),
        ([], "1..2[0:2:0]", None),
        ([], "$missing", None),
        ([("x", "$y"), ("y", "1")], "$x", None),
        ([("x", "1"), ("x", "2")], "$x", None),
        ([("x", "1..3/0")], "5", None),
        ([], "1 |", None),
        ([], "(1|2", None),
        ([], "1 2", None),
        ([], "1..2[]", None),
        ([], "", None),
        ([], "1..2/+2", None),
        ([], "1..2[0:1:] ", None),
    ]
    return [
        (
            {"definitions": [{"name": n, "expr": e} for n, e in defs], "expr": expr},
            {"error": "invalid"} if values is None else {"values": values},
        )
        for defs, expr, values in raw
    ]


TASK_484 = program_task(
    number=484,
    slug="nested_ordered_ranges",
    name="Parse nested ordered range algebra",
    parent=358,
    prompt=r"""Реализуй интерпретатор упорядоченных множеств целых. Вход {definitions,expr}.
Грамматика (ни один нетерминал не бывает пустым):
expr := intersection (("|" | "\\" | "^") intersection)*
intersection := postfix ("&" postfix)*
postfix := atom ("~" | "[" int ":" int (":" int)? "]")*
atom := int (".." int ("/" int)?)? | "$" name | "(" expr ")"
int := -?(0|[1-9][0-9]*); name := [A-Za-z_][A-Za-z_0-9]*.
В expr оператор удаления — один обратный слеш (в JSON-строке он экранируется).
ASCII whitespace space/tab/CR/LF можно ставить между лексемами, но не внутри числа,
имени и токена '..'. Знак '-' — часть числа. +1 и 01 недопустимы; -0 равен0.
Диапазон a..b/s перечисляет a, a+direction*s, ... не перешагивая b; direction=+1 при b>=a,
иначе -1. s по умолчанию1 и обязан быть>0. Все значения каждого выражения уникальны.
A|B сохраняет A и добавляет отсутствующие элементы B в их порядке; A\\B удаляет из A
все значения B; A&B сохраняет порядок A; A^B сначала сохраняет элементы A вне B,
затем элементы B вне A. &, затем postfix имеют больший приоритет, чем |,\\,^;
операторы одного приоритета применяются слева направо. Суффикс ~ разворачивает список;
[a:b:s] — точная семантика Python list slicing, a,b обязательны, s по умолчанию1,
s не ноль, отрицательные индексы и шаг допустимы. Несколько суффиксов применяются по порядку.
Definitions — массив {name,expr}; вычисляй строго по порядку, имя должно быть новым,
ссылки только на уже вычисленные definitions. Любая ошибка даже в неиспользуемом definition
делает весь вход invalid. При корректном входе выведи {"values":[...]}, иначе ровно
{"error":"invalid"}. Не исполняй вход как код. Вход содержит не более30 definitions,
2000 символов на выражение, глубина<=30; абсолютные значения чисел<=1000,
каждый диапазон и каждый промежуточный список не длиннее2001.""",
    cases=_range_cases(),
    gold=RANGE_GOLD,
    tags=("parsing", "ordered-sets", "language"),
)

TASKS = [TASK_475, TASK_484]


REPAIR_GOLD = _cli("""
def solve(x):
    resources={r['id']:r for r in x['resources']}
    bits={k:1<<i for i,k in enumerate(sorted(resources))}
    outgoing={}
    for p in x['pieces']: outgoing.setdefault(p['a'],[]).append(p)
    states={0:{0:(0,0,0,[])}}
    for at in sorted({0,x['n']}|{p['a'] for p in x['pieces']}|{p['b'] for p in x['pieces']}):
        for mask,current in list(states.get(at,{}).items()):
            for p in outgoing.get(at,[]):
                required=sum(bits[k] for k in set(p['requires']))
                added=required & ~mask; newmask=mask|required
                new=(current[0]+p['cost']+sum(resources[k]['cost'] for k in resources if added&bits[k]),
                     current[1]+sum(resources[k]['bytes'] for k in resources if added&bits[k]),
                     current[2]+1,current[3]+[p['id']])
                old=states.setdefault(p['b'],{}).get(newmask)
                if old is None or new<old: states[p['b']][newmask]=new
    if not states.get(x['n']): return {'plan':None}
    bestmask,best=min(states[x['n']].items(),key=lambda kv:kv[1])
    return {'plan':best[3],'cost':best[0],'bytes':best[1],
            'resources':[k for k in sorted(resources) if bestmask&bits[k]]}
""")


def repair_oracle(x):
    resources = {r["id"]: r for r in x["resources"]}
    answers = []

    def paths(at, chosen):
        if at == x["n"]:
            needed = sorted({r for p in chosen for r in p["requires"]})
            cost = sum(p["cost"] for p in chosen) + sum(resources[r]["cost"] for r in needed)
            size = sum(resources[r]["bytes"] for r in needed)
            ids = [p["id"] for p in chosen]
            answers.append(((cost, size, len(ids), ids), needed))
            return
        for p in x["pieces"]:
            if p["a"] == at:
                paths(p["b"], chosen + [p])

    paths(0, [])
    if not answers:
        return {"plan": None}
    best, needed = min(answers)
    return {"plan": best[3], "cost": best[0], "bytes": best[1], "resources": needed}


def _repair_cases():
    cases = []
    for seed in range(18):
        rng = random.Random(seed + 479000)
        n = seed % 5 + 2
        resources = [
            {"id": f"r{i}", "cost": rng.randrange(7), "bytes": rng.randrange(1, 10)}
            for i in range(3)
        ]
        pieces = []
        for a in range(n):
            for b in range(a + 1, n + 1):
                if rng.random() < 0.6:
                    pieces.append(
                        {
                            "id": f"p{a}{b}",
                            "a": a,
                            "b": b,
                            "cost": rng.randrange(6),
                            "requires": [r["id"] for r in resources if rng.random() < 0.35],
                        }
                    )
        x = {"n": n, "resources": resources, "pieces": pieces}
        if seed == 0:
            x = {
                "n": 4,
                "resources": [{"id": "r", "cost": 5, "bytes": 9}],
                "pieces": [
                    {"id": "a", "a": 0, "b": 2, "cost": 1, "requires": ["r"]},
                    {"id": "b", "a": 2, "b": 4, "cost": 1, "requires": ["r"]},
                    {"id": "c", "a": 0, "b": 4, "cost": 8, "requires": []},
                ],
            }
        if seed == 1:
            x = {
                "n": 2,
                "resources": [],
                "pieces": [{"id": "a", "a": 0, "b": 1, "cost": 0, "requires": []}],
            }
        cases.append((x, repair_oracle(x)))
        if seed % 5 == 3:
            y = json.loads(json.dumps(x))
            y["pieces"].reverse()
            y["resources"].reverse()
            cases.append((y, repair_oracle(y)))
    return cases


TASK_479 = program_task(
    number=479,
    slug="shared_segment_repair",
    name="Plan repairs with shared donor costs",
    parent=355,
    prompt="""Составь минимальный план восстановления непрерывного файла [0,n).
Вход: n>0; resources — уникальные {id,cost,bytes}; pieces — уникальные
{id,a,b,cost,requires}, 0<=a<b<=n. Все id — ASCII строки, суммы целые неотрицательные.
Кусок восстанавливает ровно [a,b). План — цепочка кусочков: первый a=0, каждый следующий
начинается в b предыдущего, последний заканчивается в n; дыр и перекрытий нет.
Для использования piece нужны все перечисленные resources. Ресурс загружается один раз
на весь план: стоимость плана=sum(piece.cost)+sum(cost уникальных необходимых resources),
bytes=sum(bytes этих ресурсов). requires содержит только известные id, без повторов.
Оптимизируй последовательно: минимальная стоимость, минимальные bytes, минимум числа
кусочков, лексикографически минимальный массив piece.id в порядке восстановления.
Это не сортировка списка id: порядок определяется координатами. При отсутствии цепочки
выведи ровно {"plan":null}; иначе {plan:[id,...],cost:int,bytes:int,resources:[id,...]},
последний массив отсортирован. n<=10^12, pieces<=35, resources<=12.
Порядок записей в обоих входных массивах не влияет на результат.""",
    cases=_repair_cases(),
    gold=REPAIR_GOLD,
    tags=("segments", "optimization", "shared-cost"),
)


CONTACT_GOLD = _cli("""
from functools import lru_cache
def solve(x):
    ids=sorted(x['ids']); n=len(ids); index={v:i for i,v in enumerate(ids)}
    must=[(1<<index[a])|(1<<index[b]) for a,b in x['must']]
    cannot=[(1<<index[a])|(1<<index[b]) for a,b in x['cannot']]
    weights=[((1<<index[e['a']])|(1<<index[e['b']]),e['weight']) for e in x['evidence']]
    valid={}; score={}
    for m in range(1,1<<n):
        valid[m]=all(m&p in (0,p) for p in must) and all(m&p!=p for p in cannot)
        score[m]=sum(w for p,w in weights if m&p==p)
    @lru_cache(None)
    def best(mask):
        if not mask: return (0,0,())
        first=mask&-mask; sub=mask; answer=None
        while sub:
            if sub&first and valid[sub]:
                rest=best(mask^sub)
                if rest is not None:
                    group=tuple(ids[i] for i in range(n) if sub&(1<<i))
                    groups=tuple(sorted((group,)+rest[2]))
                    candidate=(rest[0]-score[sub],rest[1]+1,groups)
                    if answer is None or candidate<answer: answer=candidate
            sub=(sub-1)&mask
        return answer
    answer=best((1<<n)-1)
    if answer is None: return {'groups':None}
    return {'groups':[list(g) for g in answer[2]],'score':-answer[0]}
""")


def contact_partition_oracle(x):
    ids = sorted(x["ids"])
    best = None

    def recurse(i, groups):
        nonlocal best
        if i == len(ids):
            owner = {v: k for k, g in enumerate(groups) for v in g}
            if any(owner[a] != owner[b] for a, b in x["must"]) or any(
                owner[a] == owner[b] for a, b in x["cannot"]
            ):
                return
            score = sum(e["weight"] for e in x["evidence"] if owner[e["a"]] == owner[e["b"]])
            canonical = sorted(sorted(g) for g in groups)
            key = (-score, len(groups), canonical)
            if best is None or key < best:
                best = key
            return
        for g in groups:
            g.append(ids[i])
            recurse(i + 1, groups)
            g.pop()
        groups.append([ids[i]])
        recurse(i + 1, groups)
        groups.pop()

    recurse(0, [])
    return {"groups": None} if best is None else {"groups": best[2], "score": -best[0]}


def _contact_partition_cases():
    cases = []
    for seed in range(17):
        rng = random.Random(481000 + seed)
        ids = [chr(97 + i) for i in range(3 + seed % 5)]
        x = {"ids": ids, "must": [], "cannot": [], "evidence": []}
        for a, b in itertools.combinations(ids, 2):
            x["evidence"].append({"a": a, "b": b, "weight": rng.randrange(-5, 8)})
            if rng.random() < 0.1:
                x["cannot"].append([a, b])
            if rng.random() < 0.07:
                x["must"].append([a, b])
        if seed == 0:
            x = {
                "ids": ["a", "b", "c"],
                "must": [],
                "cannot": [["a", "c"]],
                "evidence": [{"a": "a", "b": "b", "weight": 4}, {"a": "b", "b": "c", "weight": 6}],
            }
        if seed == 1:
            x = {
                "ids": ["a", "b", "c"],
                "must": [["a", "b"], ["b", "c"]],
                "cannot": [["a", "c"]],
                "evidence": [],
            }
        cases.append((x, contact_partition_oracle(x)))
        if seed in (2, 8, 15):
            y = json.loads(json.dumps(x))
            y["ids"].reverse()
            y["evidence"].reverse()
            cases.append((y, contact_partition_oracle(y)))
    cases.append(
        ({"ids": [], "must": [], "cannot": [], "evidence": []}, {"groups": [], "score": 0})
    )
    return cases


TASK_481 = program_task(
    number=481,
    slug="constrained_contact_partition",
    name="Resolve conflicting contact identities globally",
    parent=356,
    prompt="""Раздели контактные id на непересекающиеся группы одной личности. Вход:
ids — уникальные ASCII строки (0..9 элементов); must/cannot — массивы пар разных известных id;
evidence — записи {a,b,weight} для разных известных id, weight — целое от -100 до100.
Каждый id должен попасть ровно в одну группу. Каждая must-пара обязана быть в одной группе
(включая транзитивные следствия); каждая cannot-пара — в разных. score — сумма weight ВСЕХ
записей evidence, оба конца которых в одной группе. Повторные/обратные пары evidence суммируются,
негативные связи тоже учитываются. Отсутствие evidence не является запретом объединения.
Оптимум: максимальный score, затем минимальное число групп, затем лексикографически
минимальный список групп. Для сравнения и вывода каждую группу сортируй по id и весь список
групп тоже лексикографически. Жадное объединение сильнейших рёбер не гарантирует оптимум.
Выход {groups:[[id,...],...],score:int}. При несовместимых обязательных связях и запретах
выход ровно {"groups":null}. Порядок входных записей не влияет на результат.""",
    cases=_contact_partition_cases(),
    gold=CONTACT_GOLD,
    tags=("contacts", "global-optimization", "constraints"),
)


SATURATION_GOLD = _cli("""
def union(intervals):
    out=[]
    for a,b in sorted(intervals):
        if a>=b: continue
        if out and a<=out[-1][1]: out[-1][1]=max(out[-1][1],b)
        else: out.append([a,b])
    return out

def subtract(intervals,holes):
    result=[]
    for a,b in intervals:
        parts=[(a,b)]
        for l,r in holes:
            parts=[q for u,v in parts for q in ((u,min(v,l)),(max(u,r),v)) if q[0]<q[1]]
        result+=parts
    return result

def solve(x):
    h=x['horizon']; w=x['window']; result=[]
    durations={}
    for resource in sorted(x['capacity']):
        holes=union([(max(0,a),min(h,b)) for a,b in x['maintenance'].get(resource,[])])
        intervals={}
        for r in x['requests']:
            if r['resource']==resource:
                active=union(subtract(union([(max(0,a),min(h,b)) for a,b in r['intervals']]),holes))
                intervals[r['id']]=active
                durations[r['id']]=sum(b-a for a,b in active)
        delta={0:0,h:0}
        for rows in intervals.values():
            for a,b in rows: delta[a]=delta.get(a,0)+1; delta[b]=delta.get(b,0)-1
        times=sorted(delta); load=0; spans=[]
        for i,a in enumerate(times[:-1]):
            load+=delta[a]; spans.append((a,times[i+1],load))
        capacity=x['capacity'][resource]
        excess=[(a,b,max(0,n-capacity)) for a,b,n in spans]
        def area(t): return sum(max(0,min(b,t+w)-max(a,t))*n for a,b,n in excess)
        candidates={0,h-w}
        for a,b,n in excess:
            for t in (a,b,a-w,b-w): candidates.add(max(0,min(h-w,t)))
        best=min(candidates,key=lambda t:(-area(t),t))
        overloaded=union([(a,b) for a,b,n in excess if n>0])
        vals=sorted(durations[r['id']] for r in x['requests'] if r['resource']==resource)
        result.append({'resource':resource,'busy':sum(b-a for a,b,n in spans if n),
                       'excess':sum((b-a)*n for a,b,n in excess),
                       'longest_overload':max([b-a for a,b in overloaded] or [0]),
                       'best_window':[best,best+w],'window_excess':area(best),
                       'p95':vals[(95*len(vals)+99)//100-1] if vals else None})
    return {'resources':result,'durations':[{'id':k,'duration':durations[k]} for k in sorted(durations)]}
""")


def saturation_oracle(x):
    h = x["horizon"]
    w = x["window"]
    out = []
    durations = {}
    for resource, cap in sorted(x["capacity"].items()):
        rows = []
        for r in x["requests"]:
            if r["resource"] != resource:
                continue
            cells = {t for a, b in r["intervals"] for t in range(max(0, a), min(h, b))}
            cells -= {
                t
                for a, b in x["maintenance"].get(resource, [])
                for t in range(max(0, a), min(h, b))
            }
            rows.append(cells)
            durations[r["id"]] = len(cells)
        load = [sum(t in cells for cells in rows) for t in range(h)]
        extra = [max(0, v - cap) for v in load]
        best = max(range(h - w + 1), key=lambda t: (sum(extra[t : t + w]), -t))
        longest = run = 0
        for n in extra:
            run = run + 1 if n else 0
            longest = max(longest, run)
        vals = sorted(durations[r["id"]] for r in x["requests"] if r["resource"] == resource)
        out.append(
            {
                "resource": resource,
                "busy": sum(v > 0 for v in load),
                "excess": sum(extra),
                "longest_overload": longest,
                "best_window": [best, best + w],
                "window_excess": sum(extra[best : best + w]),
                "p95": vals[math.ceil(0.95 * len(vals)) - 1] if vals else None,
            }
        )
    return {
        "resources": out,
        "durations": [{"id": k, "duration": v} for k, v in sorted(durations.items())],
    }


def _saturation_cases():
    cases = []
    for seed in range(17):
        rng = random.Random(483000 + seed)
        h = 6 + seed % 8
        x = {
            "horizon": h,
            "window": 1 + seed % h,
            "capacity": {"a": 1 + seed % 2, "b": 1, "empty": 1},
            "maintenance": {"a": [[2, 3], [3, 4]], "b": []},
            "requests": [],
        }
        for j in range(3 + seed % 4):
            rows = []
            for _ in range(3):
                a, b = sorted([rng.randrange(-2, h + 3), rng.randrange(-2, h + 3)])
                rows.append([a, b])
            x["requests"].append({"id": f"r{j}", "resource": "ab"[j % 2], "intervals": rows})
        if seed == 0:
            x = {
                "horizon": 10,
                "window": 4,
                "capacity": {"a": 1},
                "maintenance": {"a": [[4, 6]]},
                "requests": [
                    {"id": "x", "resource": "a", "intervals": [[0, 8], [2, 7]]},
                    {"id": "y", "resource": "a", "intervals": [[2, 10]]},
                ],
            }
        cases.append((x, saturation_oracle(x)))
        if seed in (3, 8):
            y = json.loads(json.dumps(x))
            y["requests"].reverse()
            for r in y["requests"]:
                r["intervals"].reverse()
            cases.append((y, saturation_oracle(y)))
    # A scale transform preserves all interval relations. Expected is transformed
    # from the independently enumerated unit-cell oracle; no reference solver.
    x, expected = cases[0]
    scale = 10**9
    y = json.loads(json.dumps(x))
    ans = json.loads(json.dumps(expected))
    y["horizon"] *= scale
    y["window"] *= scale
    for rows in y["maintenance"].values():
        for pair in rows:
            pair[:] = [v * scale for v in pair]
    for r in y["requests"]:
        for pair in r["intervals"]:
            pair[:] = [v * scale for v in pair]
    for r in ans["resources"]:
        for k in ("busy", "excess", "longest_overload", "window_excess", "p95"):
            if r[k] is not None:
                r[k] *= scale
        r["best_window"] = [v * scale for v in r["best_window"]]
    for r in ans["durations"]:
        r["duration"] *= scale
    cases.append((y, ans))
    return cases


TASK_483 = program_task(
    number=483,
    slug="latency_overlap_windows",
    name="Measure retry overlap and overload windows",
    parent=357,
    prompt="""По ретраям вычисли реальную занятость и перегрузку. Вход {horizon,window,capacity,
maintenance,requests}. Время — целые миллисекунды, 1<=window<=horizon<=10^12;
capacity — объект resource:положительная целая вместимость; maintenance — объект resource:
список [a,b]; requests — уникальные {id,resource,intervals:[[a,b],...]}. Все resource известны,
все интервалы полуоткрытые, a<=b; интервалы могут лежать вне [0,horizon).
Для КАЖДОГО request сначала объедини его интервалы (перекрывающиеся retries — одна активность),
обрежь по [0,horizon), вычти объединение maintenance этого resource. Пустые интервалы исчезают.
В каждый момент load(resource) — число различных активных request. excess=max(0,load-capacity).
Выход {resources:[...],durations:[...]}. durations отсортирован по id, элементы {id,duration}
с длиной оставшегося множества времени, включая нулевые. resources по имени, включая пустые:
{resource,busy,excess,longest_overload,best_window,window_excess,p95}.
busy — мера времени load>0; excess — интеграл excess; longest_overload — максимальная
длина непрерывного интервала excess>0 (соседние перегруженные отрезки с разной величиной excess
объединяются). best_window=[t,t+window] максимизирует интеграл excess в этом окне среди
целых 0<=t<=horizon-window; при равенстве выбери минимальный t. window_excess — этот интеграл.
p95 — длительность request на позиции ceil(0.95*N) в отсортированном списке длительностей
всех N запросов resource, включая нули; при N=0 null. До80 requests и400 интервалов.
Нельзя перебирать миллисекунды: большие горизонты проверяются отдельно.""",
    cases=_saturation_cases(),
    gold=SATURATION_GOLD,
    tags=("latency", "interval-algebra", "sweep-line"),
)


COMPRESSED_GOLD = _cli("""
def cut(segment,a,b):
    u,v=segment; lo,hi=sorted(segment); parts=[]
    for l,r in ((lo,min(hi,a-1)),(max(lo,b+1),hi)):
        if l<=r: parts.append((l,r))
    return parts if u<=v else [(r,l) for l,r in reversed(parts)]

def solve(x):
    runs=[]; sizes=[]
    for op in x['ops']:
        kind=op['op']
        if kind=='reverse': runs=[(b,a) for a,b in reversed(runs)]
        else:
            a,b=op['a'],op['b']; lo,hi=sorted((a,b))
            if kind=='add':
                remaining=[(lo,hi)]
                for u,v in runs:
                    l,r=sorted((u,v)); remaining=[p for q in remaining for p in cut(q,l,r)]
                runs+=remaining if a<=b else [(v,u) for u,v in reversed(remaining)]
            elif kind=='remove': runs=[p for q in runs for p in cut(q,lo,hi)]
            else:
                front=[]; tail=[]
                for u,v in runs:
                    front+=cut((u,v),lo,hi)
                    l,r=max(min(u,v),lo),min(max(u,v),hi)
                    if l<=r: tail.append((l,r) if u<=v else (r,l))
                runs=front+tail
        sizes.append(sum(abs(b-a)+1 for a,b in runs))
    n=sum(abs(b-a)+1 for a,b in runs); answers=[]
    for q in x['queries']:
        if q['op']=='rank':
            pos=0; answer=None
            for a,b in runs:
                if min(a,b)<=q['value']<=max(a,b): answer=pos+abs(q['value']-a); break
                pos+=abs(b-a)+1
            answers.append(answer)
        elif q['op']=='select':
            index=q['index']; answer=None
            if 0<=index<n:
                for a,b in runs:
                    length=abs(b-a)+1
                    if index<length: answer=a+(1 if b>=a else -1)*index; break
                    index-=length
            answers.append(answer)
        else:
            lo=max(0,q['start']); hi=min(n,q['stop']); total=0; pos=0
            for a,b in runs:
                length=abs(b-a)+1; l=max(lo,pos); r=min(hi,pos+length)
                if l<r:
                    direction=1 if b>=a else -1
                    first=a+direction*(l-pos); last=a+direction*(r-pos-1)
                    total+=(first+last)*(r-l)//2
                pos+=length
            answers.append(total)
    return {'sizes':sizes,'length':n,'answers':answers}
""")


def compressed_oracle(x):
    result = []
    sizes = []
    for op in x["ops"]:
        if op["op"] == "reverse":
            result.reverse()
        else:
            a, b = op["a"], op["b"]
            lo, hi = sorted((a, b))
            if op["op"] == "add":
                for v in range(a, b + (1 if b >= a else -1), 1 if b >= a else -1):
                    if v not in result:
                        result.append(v)
            elif op["op"] == "remove":
                result = [v for v in result if not lo <= v <= hi]
            else:
                result = [v for v in result if not lo <= v <= hi] + [
                    v for v in result if lo <= v <= hi
                ]
        sizes.append(len(result))
    answers = []
    for q in x["queries"]:
        if q["op"] == "rank":
            answers.append(result.index(q["value"]) if q["value"] in result else None)
        elif q["op"] == "select":
            answers.append(result[q["index"]] if 0 <= q["index"] < len(result) else None)
        else:
            answers.append(sum(result[max(0, q["start"]) : max(0, q["stop"])]))
    return {"sizes": sizes, "length": len(result), "answers": answers}


def _compressed_cases():
    cases = []
    for seed in range(18):
        rng = random.Random(485000 + seed)
        ops = []
        for _ in range(8 + seed % 6):
            kind = rng.choice(["add", "add", "remove", "move", "reverse"])
            ops.append(
                {"op": kind}
                if kind == "reverse"
                else {"op": kind, "a": rng.randrange(-10, 11), "b": rng.randrange(-10, 11)}
            )
        queries = [{"op": "select", "index": i} for i in (-1, 0, 1, 5, 15, 30)]
        queries += [{"op": "rank", "value": i} for i in (-10, -3, 0, 5, 10)]
        queries += [
            {"op": "sum", "start": a, "stop": b}
            for a, b in [(-5, 6), (2, 16), (16, 2), (20, 50), (-6, -2)]
        ]
        x = {"ops": ops, "queries": queries}
        cases.append((x, compressed_oracle(x)))
    m = 10**12
    cases.append(
        (
            {
                "ops": [
                    {"op": "add", "a": m, "b": -m},
                    {"op": "remove", "a": -m + 1, "b": m - 1},
                    {"op": "add", "a": 0, "b": 2},
                    {"op": "move", "a": m, "b": m},
                    {"op": "reverse"},
                ],
                "queries": [{"op": "select", "index": i} for i in range(6)]
                + [{"op": "rank", "value": -m}, {"op": "sum", "start": 0, "stop": 9}],
            },
            {
                "sizes": [2 * m + 1, 2, 5, 5, 5],
                "length": 5,
                "answers": [m, 2, 1, 0, -m, None, 4, 3],
            },
        )
    )
    cases.append(
        (
            {
                "ops": [{"op": "add", "a": 1, "b": m}, {"op": "move", "a": 1, "b": m // 2}],
                "queries": [
                    {"op": "select", "index": 0},
                    {"op": "select", "index": m // 2},
                    {"op": "rank", "value": m},
                    {"op": "sum", "start": 0, "stop": m},
                ],
            },
            {
                "sizes": [m, m],
                "length": m,
                "answers": [m // 2 + 1, 1, m // 2 - 1, m * (m + 1) // 2],
            },
        )
    )
    cases.append(
        (
            {
                "ops": [],
                "queries": [
                    {"op": "sum", "start": -2, "stop": 3},
                    {"op": "rank", "value": 0},
                    {"op": "select", "index": 0},
                ],
            },
            {"sizes": [], "length": 0, "answers": [0, None, None]},
        )
    )
    return cases


TASK_485 = program_task(
    number=485,
    slug="compressed_ordered_ranges",
    name="Edit enormous ordered ranges without expansion",
    parent=358,
    prompt="""Поддержи упорядоченное множество целых, изначально пустое. Вход {ops,queries}.
Операции применяются по порядку:
{op:"add",a,b}: перечисли все целые от a до b включительно в направлении b и добавь
только отсутствующие в конец, сохраняя этот порядок; уже присутствующие не перемещаются.
{op:"remove",a,b}: удали присутствующие значения из числового замкнутого [min(a,b),max(a,b)].
{op:"move",a,b}: извлеки присутствующие значения этого числового интервала и поставь в конец
в том же относительном порядке, в каком они были; остальные сохраняют порядок.
{op:"reverse"}: разверни весь порядок. Множество всегда уникально.
Все queries относятся к окончательному состоянию:
{op:"rank",value} — индекс с0 либо null при отсутствии;
{op:"select",index} — значение либо null, если index<0 или index>=length;
{op:"sum",start,stop} — сумма значений с индексами max(0,start)<=i<min(length,stop),
при пустом интервале0. Отрицательные индексы здесь НЕ считаются с конца.
Выход {sizes:[длина после каждой операции],length:итоговая длина,answers:[ответы queries]}.
До100 операций и100 запросов, целые a,b,value,index,start,stop от -10^12 до10^12.
Промежуточное множество может содержать2*10^12+1 значений: перечисление элементов недопустимо.
Нужны точные целые суммы, порядок после удаления и повторного добавления важен.""",
    cases=_compressed_cases(),
    gold=COMPRESSED_GOLD,
    tags=("ranges", "compression", "rank-select"),
)

TASKS += [TASK_479, TASK_481, TASK_483, TASK_485]


def _jsonl(rows):
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def _visible(rows, seen):
    winners = {}
    for row in rows:
        if row["seen"] <= seen and (
            row["id"] not in winners
            or (row["rev"], row["seen"]) > (winners[row["id"]]["rev"], winners[row["id"]]["seen"])
        ):
            winners[row["id"]] = row
    deleted = sorted(k for k, r in winners.items() if r.get("op") == "delete")
    return [r for r in winners.values() if r.get("op") != "delete"], deleted


def _union(rows):
    result = []
    for a, b in sorted(rows):
        if a >= b:
            continue
        if result and a <= result[-1][1]:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return result


def _reconstruction_view(rows, policy, query, offsets):
    visible, deleted = _visible(rows, query["seen"])
    outside = sorted(
        r["id"] for r in visible if r["local"] - offsets[r["host"]] >= query["horizon"]
    )
    chosen = [
        dict(r, at=r["local"] - offsets[r["host"]]) for r in visible if r["id"] not in outside
    ]
    starts = {r["id"]: r for r in chosen if r["kind"] == "start"}
    finishes = {r["id"]: r for r in chosen if r["kind"] == "finish"}
    return starts, finishes, outside, deleted


def _reconstruction_options(starts, finishes, policy):
    """Gold searches starts with capacity pruning and retains all maximal matchings."""
    ids = sorted(starts)
    options = {}
    for key in ids:
        rule = policy["sessions"][key]
        options[key] = [
            f
            for f in sorted(finishes)
            if rule["label"] in finishes[f]["accept"]
            and rule["min"] <= finishes[f]["at"] - starts[key]["at"] <= rule["max"]
        ]
    best_count = -1
    winners = []

    def search(index, pairs, used, cost):
        nonlocal best_count, winners
        if len(pairs) + len(ids) - index < best_count:
            return
        if index == len(ids):
            for key in pairs:
                for parent in policy["sessions"][key]["requires"]:
                    if parent not in pairs or finishes[pairs[parent]]["at"] > starts[key]["at"]:
                        return
            count = len(pairs)
            if count > best_count:
                best_count, winners = count, []
            if count == best_count:
                winners.append((tuple(sorted(pairs.items())), cost))
            return
        key = ids[index]
        rule = policy["sessions"][key]
        for finish in options[key]:
            if finish in used:
                continue
            intervals = [
                (starts[k]["at"], finishes[f]["at"])
                for k, f in pairs.items()
                if policy["sessions"][k]["resource"] == rule["resource"]
            ]
            intervals.append((starts[key]["at"], finishes[finish]["at"]))
            if any(
                sum(a <= at < b for a, b in intervals) > policy["capacity"][rule["resource"]]
                for at in {a for a, b in intervals}
            ):
                continue
            pairs[key] = finish
            duration = finishes[finish]["at"] - starts[key]["at"]
            search(
                index + 1,
                pairs,
                used | {finish},
                cost + rule["weight"] * abs(duration - rule["target"]),
            )
            del pairs[key]
        search(index + 1, pairs, used, cost)

    search(0, {}, set(), 0)
    return best_count, winners


def _reconstruction_transition(before, after, common, policy):
    before, after = dict(before), dict(after)
    changed = [key for key in sorted(common) if before.get(key) != after.get(key)]
    return sum(policy["sessions"][key]["change_cost"] for key in changed), len(changed)


def _reconstruction_result(rows, policy, queries, offsets, history):
    snapshots = []
    changes = []
    duration_cost = 0
    prior_starts = None
    prior_pairs = None
    for qi, (query, pairs_tuple) in enumerate(zip(queries, history, strict=True)):
        starts, finishes, outside, deleted = _reconstruction_view(rows, policy, query, offsets)
        pairs = dict(pairs_tuple)
        output_pairs = []
        dependencies = []
        for key, finish in sorted(pairs.items()):
            rule = policy["sessions"][key]
            duration = finishes[finish]["at"] - starts[key]["at"]
            cost = rule["weight"] * abs(duration - rule["target"])
            duration_cost += cost
            output_pairs.append(
                {
                    "start": key,
                    "finish": finish,
                    "start_at": starts[key]["at"],
                    "end_at": finishes[finish]["at"],
                    "duration": duration,
                    "cost": cost,
                }
            )
            for parent in sorted(rule["requires"]):
                dependencies.append(
                    {
                        "start": key,
                        "requires": parent,
                        "slack": starts[key]["at"] - finishes[pairs[parent]]["at"],
                    }
                )
        resources = []
        for resource, _capacity in sorted(policy["capacity"].items()):
            intervals = [
                (r["start_at"], r["end_at"])
                for r in output_pairs
                if policy["sessions"][r["start"]]["resource"] == resource
            ]
            resources.append(
                {
                    "resource": resource,
                    "occupied": sum(b - a for a, b in _union(intervals)),
                    "peak": max(
                        [sum(a <= at < b for a, b in intervals) for at in {a for a, b in intervals}]
                        or [0]
                    ),
                }
            )
        if prior_starts is not None:
            for key in sorted(set(prior_starts) & set(starts)):
                if prior_pairs.get(key) != pairs.get(key):
                    changes.append(
                        {
                            "from": queries[qi - 1]["name"],
                            "to": query["name"],
                            "start": key,
                            "before": prior_pairs.get(key),
                            "after": pairs.get(key),
                            "cost": policy["sessions"][key]["change_cost"],
                        }
                    )
        prior_starts, prior_pairs = starts, pairs
        snapshots.append(
            {
                "name": query["name"],
                "pairs": output_pairs,
                "unmatched_starts": sorted(set(starts) - set(pairs)),
                "unmatched_finishes": sorted(set(finishes) - set(pairs.values())),
                "outside": outside,
                "deleted": deleted,
                "resources": resources,
                "dependencies": dependencies,
            }
        )
    clock_cost = sum(
        next(option["cost"] for option in policy["clocks"][host] if option["offset"] == offset)
        for host, offset in offsets.items()
    )
    change_cost = sum(row["cost"] for row in changes)
    return {
        "clock_offsets": offsets,
        "objective": {
            "pairs": sum(len(p) for p in history),
            "cost": clock_cost + duration_cost + change_cost,
            "changes": len(changes),
        },
        "cost_breakdown": {"clock": clock_cost, "duration": duration_cost, "changes": change_cost},
        "snapshots": snapshots,
        "changes": changes,
    }


def session_report(rows, policy, queries):
    """Joint clock inference and optimal restatement, not independent FIFO snapshots."""
    hosts = sorted(policy["clocks"])
    best_key = None
    best_offsets = None
    best_history = None
    for choices in itertools.product(*(policy["clocks"][h] for h in hosts)):
        offsets = {h: option["offset"] for h, option in zip(hosts, choices, strict=True)}
        if any(
            not relation["min"]
            <= offsets[relation["left"]] - offsets[relation["right"]]
            <= relation["max"]
            for relation in policy["relations"]
        ):
            continue
        views = [_reconstruction_view(rows, policy, query, offsets) for query in queries]
        alternatives = [_reconstruction_options(view[0], view[1], policy) for view in views]
        cardinality = sum(count for count, options in alternatives)
        if best_key is not None and cardinality < -best_key[0]:
            continue
        states = {pairs: (cost, 0, (pairs,)) for pairs, cost in alternatives[0][1]}
        for i, (_count, options) in enumerate(alternatives[1:], 1):
            common = set(views[i - 1][0]) & set(views[i][0])
            next_states = {}
            for pairs, cost in options:
                optimum = None
                for previous, value in states.items():
                    transition_cost, changes = _reconstruction_transition(
                        previous, pairs, common, policy
                    )
                    candidate = (
                        value[0] + cost + transition_cost,
                        value[1] + changes,
                        value[2] + (pairs,),
                    )
                    if optimum is None or candidate < optimum:
                        optimum = candidate
                next_states[pairs] = optimum
            states = next_states
        value = min(states.values())
        clock_cost = sum(option["cost"] for option in choices)
        key = (
            -cardinality,
            value[0] + clock_cost,
            value[1],
            tuple(offsets[h] for h in hosts),
            value[2],
        )
        if best_key is None or key < best_key:
            best_key, best_offsets, best_history = key, offsets, value[2]
    if best_key is None:
        raise ValueError("fixture has no permitted clock hypothesis")
    return _reconstruction_result(rows, policy, queries, best_offsets, best_history)


def reconstruction_small_oracle(rows, policy, queries):
    """Independent Cartesian assignment/sequence enumeration, for small test anchors."""
    hosts = sorted(policy["clocks"])
    best = None
    for choices in itertools.product(*(policy["clocks"][h] for h in hosts)):
        offsets = dict(zip(hosts, (c["offset"] for c in choices), strict=True))
        if any(
            offsets[r["left"]] - offsets[r["right"]] not in range(r["min"], r["max"] + 1)
            for r in policy["relations"]
        ):
            continue
        all_views = []
        all_options = []
        for query in queries:
            # Independent winner selection by sorting, not _visible/view.
            winners = {}
            for row in sorted(rows, key=lambda r: (r["rev"], r["seen"])):
                if row["seen"] <= query["seen"]:
                    winners[row["id"]] = row
            accepted = {
                k: r
                for k, r in winners.items()
                if r.get("op") != "delete" and r["local"] - offsets[r["host"]] < query["horizon"]
            }
            starts = sorted(k for k, r in accepted.items() if r["kind"] == "start")
            finishes = sorted(k for k, r in accepted.items() if r["kind"] == "finish")
            times = {k: r["local"] - offsets[r["host"]] for k, r in accepted.items()}
            options = []
            for vector in itertools.product([None] + finishes, repeat=len(starts)):
                pairs = dict((k, f) for k, f in zip(starts, vector, strict=True) if f is not None)
                if len(set(pairs.values())) != len(pairs):
                    continue
                valid = True
                cost = 0
                for key, finish in pairs.items():
                    rule = policy["sessions"][key]
                    duration = times[finish] - times[key]
                    if (
                        rule["label"] not in accepted[finish]["accept"]
                        or not rule["min"] <= duration <= rule["max"]
                    ):
                        valid = False
                    if any(
                        parent not in pairs or times[pairs[parent]] > times[key]
                        for parent in rule["requires"]
                    ):
                        valid = False
                    cost += rule["weight"] * abs(duration - rule["target"])
                for resource, cap in policy["capacity"].items():
                    for t in range(min(times.values(), default=0), max(times.values(), default=0)):
                        if (
                            sum(
                                times[k] <= t < times[f]
                                for k, f in pairs.items()
                                if policy["sessions"][k]["resource"] == resource
                            )
                            > cap
                        ):
                            valid = False
                if valid:
                    options.append((tuple(sorted(pairs.items())), cost))
            all_views.append(set(starts))
            all_options.append(options)
        for sequence in itertools.product(*all_options):
            history = tuple(p for p, cost in sequence)
            cost = sum(c for p, c in sequence) + sum(c["cost"] for c in choices)
            changes = 0
            for i in range(1, len(sequence)):
                previous, current = dict(history[i - 1]), dict(history[i])
                for start in all_views[i - 1] & all_views[i]:
                    if previous.get(start) != current.get(start):
                        changes += 1
                        cost += policy["sessions"][start]["change_cost"]
            key = (
                -sum(len(p) for p in history),
                cost,
                changes,
                tuple(offsets[h] for h in hosts),
                history,
            )
            if best is None or key < best:
                best = key
    return best


def _session_fixture():
    start_times = [0, 2, 4, 7, 10, 13, 16, 18, 22]
    start_hosts = [
        "collector",
        "desktop",
        "collector",
        "mobile",
        "collector",
        "desktop",
        "collector",
        "mobile",
        "collector",
    ]
    labels = ["alpha", "beta", "alpha", "gamma", "alpha", "beta", "gamma", "alpha", "beta"]
    resources = ["blue", "blue", "red", "red", "blue", "red", "blue", "red", "blue"]
    minimum = [4, 3, 2, 3, 2, 2, 0, 3, 1]
    maximum = [14, 13, 12, 13, 14, 12, 10, 13, 11]
    targets = [8, 7, 6, 8, 6, 7, 6, 8, 7]
    dependencies = {5: ["s0"], 6: ["s2"], 7: ["s1", "s4"], 8: ["s4"]}
    policy = {
        "clocks": {
            "collector": [{"offset": 0, "cost": 0}],
            "desktop": [
                {"offset": -2, "cost": 1},
                {"offset": 1, "cost": 0},
                {"offset": 4, "cost": 2},
            ],
            "mobile": [
                {"offset": -3, "cost": 0},
                {"offset": 0, "cost": 2},
                {"offset": 3, "cost": 1},
            ],
        },
        "relations": [{"left": "mobile", "right": "desktop", "min": -4, "max": 4}],
        "capacity": {"blue": 2, "red": 2},
        "sessions": {
            f"s{i}": {
                "label": labels[i],
                "resource": resources[i],
                "min": minimum[i],
                "max": maximum[i],
                "target": targets[i],
                "weight": 1 + i % 3,
                "change_cost": 7 + i % 4 * 3,
                "requires": dependencies.get(i, []),
            }
            for i in range(9)
        },
    }
    rows = [
        {
            "id": f"s{i}",
            "kind": "start",
            "host": start_hosts[i],
            "local": at,
            "rev": 1,
            "seen": i + 1,
            "op": "put",
        }
        for i, at in enumerate(start_times)
    ]
    end_times = [8, 10, 12, 15, 18, 20, 23, 27, 30, 32]
    accepts = [
        ["alpha", "beta"],
        ["alpha", "beta"],
        ["alpha", "gamma"],
        ["beta", "gamma"],
        ["alpha", "beta"],
        ["alpha", "gamma"],
        ["beta", "gamma"],
        ["alpha", "beta"],
        ["alpha", "beta", "gamma"],
        ["alpha", "gamma"],
    ]
    ends = [
        {
            "id": f"f{i}",
            "kind": "finish",
            "host": "desktop" if i % 2 == 0 else "mobile",
            "local": at,
            "accept": accepts[i],
            "rev": 1,
            "seen": 10 + i,
            "op": "put",
        }
        for i, at in enumerate(end_times)
    ]
    rows += ends
    rows += [
        {
            "id": "boundary",
            "kind": "finish",
            "host": "collector",
            "local": 60,
            "accept": ["alpha", "beta", "gamma"],
            "rev": 1,
            "seen": 20,
            "op": "put",
        },
        {
            "id": "moving",
            "kind": "finish",
            "host": "collector",
            "local": 65,
            "accept": ["alpha", "beta", "gamma"],
            "rev": 1,
            "seen": 21,
            "op": "put",
        },
    ]
    corrections = [
        dict(ends[1], rev=2, seen=55, local=13),
        dict(ends[3], rev=2, seen=56, accept=["alpha", "beta"]),
        {"id": "f5", "rev": 2, "seen": 58, "op": "delete"},
        dict(ends[6], id="f10", rev=1, seen=59, local=25, accept=["gamma"]),
        dict(rows[8], rev=2, seen=60, local=24),
        dict(ends[2], rev=2, seen=61, local=10),
        dict(rows[-1], rev=2, seen=62, local=29),
        dict(ends[5], rev=3, seen=100, local=21),
        dict(ends[3], rev=3, seen=101, local=19, accept=["alpha", "gamma"]),
        dict(rows[7], rev=2, seen=102, local=22),
        dict(ends[8], rev=2, seen=103, local=28),
        dict(rows[-1], rev=3, seen=104, local=65),
    ]
    queries = [
        {"name": "initial", "seen": 40, "horizon": 60},
        {"name": "corrected", "seen": 80, "horizon": 60},
        {"name": "restated", "seen": 120, "horizon": 60},
    ]
    random.Random(47420).shuffle(rows)
    random.Random(47421).shuffle(corrections)
    return rows, corrections, policy, queries


_SESSION_ROWS, _SESSION_CORRECTIONS, _SESSION_POLICY, _SESSION_QUERIES = _session_fixture()
TASK_474 = artifact_task(
    number=474,
    slug="session_expiry_restatement",
    name="Jointly reconstruct corrected sessions and device clocks",
    parent=353,
    prompt="""Восстанови совместно три версии журнала сессий. Устройства имеют неизвестные постоянные
сдвиги часов, finish может относиться к нескольким start, а опубликованное сопоставление
нежелательно менять. Нужен ОДИН глобальный оптимум, не три независимых отчёта.

Файлы events/*.jsonl и corrections.jsonl — единая история. Для каждого снимка snapshots.json
сначала среди seen<=snapshot.seen для каждого id выбери максимум (rev,seen); delete удаляет,
put заменяет запись целиком. Затем UTC at=local-offset[host] и оставь только at<horizon.
Нижней границы времени нет. id сохраняет kind между ревизиями. Все пары (id,rev,seen) уникальны.
policy.json содержит: clocks[host] — допустимые {offset,cost}; relations — ограничения
min<=offset[left]-offset[right]<=max; capacity — ресурс:вместимость; sessions[start_id] —
{label,resource,min,max,target,weight,change_cost,requires}. Выбери по одному offset для каждого
host, ОДНИ И ТЕ ЖЕ для всех снимков; clock cost начисляется один раз. Все поля целочисленные.

В каждом снимке выбирай пары start→finish. Каждый start и finish используется максимум один раз.
Пара допустима, если label старта в finish.accept и min<=finish.at-start.at<=max.
Выбранная пара занимает resource старта на полуоткрытом [start.at,finish.at); нулевая
длительность не занимает ресурс. Ни в один момент число выбранных пар на ресурсе не превышает
capacity. Непарные события ресурс не занимают. Если start выбран, ВСЕ его requires должны
тоже быть выбранными start этого снимка и end каждой их пары<=at данного start.
requires ацикличны; ограничения проверяются для всего набора, порядок перебора не важен.
Стоимость пары = weight*abs(duration-target).

Между соседними снимками рассмотри каждый start_id, активный В ОБОИХ снимках после всех
фильтров. Его assignment — finish.id либо null. Если assignment изменился (в том числе
null↔id), начисляется change_cost этого start и одно change. Если start отсутствует хотя бы
в одном из двух снимков, штрафа нет. Одинаковый finish.id после исправления времени не change.

Оптимизируй строго в порядке:
1) максимальная сумма числа пар по всем снимкам;
2) минимальная общая cost = clock cost + сумма стоимостей пар + сумма штрафов changes;
3) минимальное количество changes;
4) лексикографически минимальный вектор offsets по отсортированным именам host;
5) лексикографически минимальный список списков пар [start_id,finish_id]: снимки по файлу,
внутри каждого пары по start_id, обычное сравнение строк/списков Python.
Пустые сопоставления допустимы; допустимая гипотеза clocks гарантирована.

answer.json ровно:
{clock_offsets:{host:offset}, objective:{pairs,cost,changes},
cost_breakdown:{clock,duration,changes}, snapshots:[...], changes:[...]}.
Каждый snapshot в порядке файла: {name,pairs:[{start,finish,start_at,end_at,duration,cost}],
unmatched_starts:[id],unmatched_finishes:[id],outside:[id],deleted:[id],
resources:[{resource,occupied,peak}],dependencies:[{start,requires,slack}]}.
pairs по start; unmatched/outside/deleted по id. outside — все put-победители с at>=horizon;
deleted — все tombstone-победители. resources включает все ресурсы по имени: occupied — длина
объединения выбранных интервалов, peak — максимальная одновременность, пустой ресурс0/0.
dependencies перечисляет каждый requires выбранного start по (start,requires),
slack=start.at-end требуемой пары. changes — {from:имя,to:имя,start,before:finish_id|null,
after:finish_id|null,cost}, сначала порядок соседних снимков, затем start_id.
Все числовые результаты точные целые. Отчёт должен доказывать выбранный глобальный вариант:
локально лучший отдельный снимок или минимальные clock priors могут быть глобально хуже.""",
    files={
        "events/primary.jsonl": _jsonl(_SESSION_ROWS[:11]),
        "events/secondary.jsonl": _jsonl(_SESSION_ROWS[11:]),
        "corrections.jsonl": _jsonl(_SESSION_CORRECTIONS),
        "policy.json": dumps(_SESSION_POLICY),
        "snapshots.json": dumps(_SESSION_QUERIES),
    },
    expected=session_report(
        _SESSION_ROWS + _SESSION_CORRECTIONS, _SESSION_POLICY, _SESSION_QUERIES
    ),
    tags=("sessions", "temporal", "global-reconstruction", "clock-inference"),
)
TASKS.append(TASK_474)


def log_report(rows, calibrations, mutes, config):
    reports = []
    for query in config["snapshots"]:
        selected, deleted = _visible(rows, query["seen"])
        levels = ["DEBUG", "INFO", "WARN", "ERROR"]
        counts = {s: {k: 0 for k in levels} for s in config["services"]}
        outside = []
        invalid = []
        muted = []
        errors = []
        for row in selected:
            if (
                row.get("level") not in levels
                or row.get("service") not in counts
                or not row.get("fingerprint")
            ):
                invalid.append(row["id"])
                continue
            valid = [
                c
                for c in calibrations
                if c["host"] == row["host"] and c["start"] <= row["local"] < c["end"]
            ]
            if not valid:
                invalid.append(row["id"])
                continue
            c = min(valid, key=lambda c: (-c["priority"], -c["start"], c["id"]))
            at = row["local"] - c["offset"]
            if not 0 <= at < config["horizon"]:
                outside.append(row["id"])
                continue
            if any(m["service"] == row["service"] and m["start"] <= at < m["end"] for m in mutes):
                muted.append(row["id"])
                continue
            counts[row["service"]][row["level"]] += 1
            if row["level"] == "ERROR":
                errors.append(dict(row, at=at))
        incidents = []
        for key in sorted({(r["service"], r["fingerprint"]) for r in errors}):
            group = sorted(
                [r for r in errors if (r["service"], r["fingerprint"]) == key],
                key=lambda r: (r["at"], r["id"]),
            )
            clusters = []
            for r in group:
                if not clusters or r["at"] - clusters[-1][-1]["at"] > config["gap"]:
                    clusters.append([])
                clusters[-1].append(r)
            for cluster in clusters:
                incidents.append(
                    {
                        "service": key[0],
                        "fingerprint": key[1],
                        "start": cluster[0]["at"],
                        "end": cluster[-1]["at"],
                        "ids": [r["id"] for r in cluster],
                    }
                )
        exposure = []
        for service in sorted(counts):
            intervals = _union(
                (r["at"], min(config["horizon"], r["at"] + config["linger"]))
                for r in errors
                if r["service"] == service
            )
            buckets = [
                sum(max(0, min(b, end) - max(a, start)) for a, b in intervals)
                for start, end in zip(config["buckets"][:-1], config["buckets"][1:], strict=True)
            ]
            exposure.append(
                {
                    "service": service,
                    "seconds": sum(b - a for a, b in intervals),
                    "buckets": buckets,
                }
            )
        reports.append(
            {
                "snapshot": query["name"],
                "overall": {k: sum(v[k] for v in counts.values()) for k in levels},
                "by_service": counts,
                "incidents": incidents,
                "exposure": exposure,
                "invalid": sorted(invalid),
                "outside": sorted(outside),
                "muted": sorted(muted),
                "deleted": deleted,
            }
        )
    return reports


def _log_fixture():
    rows = []
    for i in range(68):
        row = {
            "id": f"L{i:03}",
            "rev": 1,
            "seen": i + 1,
            "op": "put",
            "local": (i * 13) % 112 - 4,
            "host": "h" + str(i % 3),
            "service": ["api", "search", "queue"][i % 3],
            "fingerprint": ["timeout", "quota", "parse"][i % 3 if i % 4 else 0],
            "level": ["ERROR", "INFO", "ERROR", "WARN", "DEBUG"][i % 5],
            "message": "previous level=ERROR service=wrong; text is not a field",
        }
        if i % 17 == 0:
            row["level"] = "error"
        rows.append(row)
    corrections = []
    for j in range(14):
        r = dict(rows[j * 4])
        r.update(rev=2, seen=80 + j)
        if j % 4 == 0:
            r = {"id": r["id"], "rev": 2, "seen": 80 + j, "op": "delete"}
        else:
            r["level"] = "ERROR"
            r["local"] += j % 6
            r["fingerprint"] = "timeout"
        corrections.append(r)
    corrections.append(dict(rows[0], rev=3, seen=110, level="ERROR", local=25))
    calibrations = [
        {
            "id": f"base{i}",
            "host": f"h{i}",
            "start": -50,
            "end": 200,
            "offset": i * 3 - 2,
            "priority": 0,
        }
        for i in range(3)
    ]
    calibrations += [
        {"id": "patch-b", "host": "h1", "start": 30, "end": 70, "offset": 8, "priority": 2},
        {"id": "patch-a", "host": "h1", "start": 30, "end": 70, "offset": 5, "priority": 2},
        {"id": "h2-after", "host": "h2", "start": 55, "end": 150, "offset": -4, "priority": 1},
    ]
    mutes = [
        {"service": "api", "start": 18, "end": 27},
        {"service": "search", "start": 42, "end": 48},
        {"service": "queue", "start": 0, "end": 6},
        {"service": "queue", "start": 90, "end": 100},
    ]
    config = {
        "services": ["api", "queue", "search"],
        "horizon": 100,
        "gap": 9,
        "linger": 7,
        "buckets": [0, 25, 50, 75, 100],
        "snapshots": [
            {"name": "published", "seen": 75},
            {"name": "corrected", "seen": 100},
            {"name": "restored", "seen": 120},
        ],
    }
    random.Random(472).shuffle(rows)
    random.Random(4721).shuffle(corrections)
    return rows, corrections, calibrations, mutes, config


_LOG_ROWS, _LOG_CORRECTIONS, _LOG_CALIBRATIONS, _LOG_MUTES, _LOG_CONFIG = _log_fixture()
TASK_472 = artifact_task(
    number=472,
    slug="corrected_log_incidents",
    name="Restate incident windows across clocks and revisions",
    parent=352,
    prompt="""Восстанови отчёт об инцидентах из logs/*.jsonl и corrections.jsonl для каждого
снимка config.json. Для каждого id выбери среди seen<=снимок максимальную (rev,seen);
победитель op=delete удаляет запись. Пара (id,rev,seen) уникальна; ревизия заменяет запись целиком.
Структурированные поля level/service/fingerprint проверяются только у победителя: level строго
DEBUG/INFO/WARN/ERROR, service из config.services, fingerprint непустой. message игнорируется.
Для корректной строки найди калибровки того же host, где start<=local<end. Приоритет выбора:
максимальный priority, затем максимальный start, затем минимальный id. Нет калибровки — invalid.
Время UTC-секунд at=local-offset. Калибровки статичны и одинаковы для всех снимков.
После проверки и калибровки: at вне [0,horizon) даёт outside; иначе событие в любом
mute интервале своего service [start,end) даёт muted; остальные входят в счётчики.
Invalid/outside/muted взаимно исключаются именно в этом порядке. Ошибки message не ищи.
Для ERROR отдельно по (service,fingerprint) сортируй по (at,id); новый инцидент начинается,
если разрыв с предыдущей ERROR строго больше config.gap. Не-ERROR кластер не разрывает.
Инцидент {service,fingerprint,start,end,ids}: start/end — время первой/последней ERROR,
ids в порядке (at,id). Сортировка инцидентов (service,fingerprint,start).
Exposure каждого service — объединение [at,min(horizon,at+linger)) всех его принятых ERROR
без повторного счёта перекрытий; последующее mute-окно это уже начатое exposure не обрезает.
answer.json — массив в порядке config.snapshots. Каждый элемент ровно:
{snapshot:name,overall:{DEBUG,INFO,WARN,ERROR},by_service:{service:{те же4 уровня}},
incidents:[...],exposure:[{service,seconds,buckets}],invalid:[id],outside:[id],muted:[id],deleted:[id]}.
Все service включая нулевые; exposure по service; buckets — длина пересечения объединения
с последовательными полуоткрытыми интервалами config.buckets. Аудит id сортируется;
deleted содержит все tombstone-победители, без фильтра времени. Счётчики — целые.""",
    files={
        "logs/east.jsonl": _jsonl(_LOG_ROWS[:34]),
        "logs/west.jsonl": _jsonl(_LOG_ROWS[34:]),
        "corrections.jsonl": _jsonl(_LOG_CORRECTIONS),
        "calibrations.json": dumps(_LOG_CALIBRATIONS),
        "mutes.json": dumps(_LOG_MUTES),
        "config.json": dumps(_LOG_CONFIG),
    },
    expected=log_report(_LOG_ROWS + _LOG_CORRECTIONS, _LOG_CALIBRATIONS, _LOG_MUTES, _LOG_CONFIG),
    tags=("logs", "bitemporal", "intervals"),
)
TASKS.append(TASK_472)


def catalog_report(products, prices, movements, reservations, recipes, warehouses, orders, queries):
    reports = []

    def normalize(v):
        return v.strip().upper()

    def flatten(sku):
        if sku not in recipes:
            return {sku: 1}
        total = Counter()
        for child, n in recipes[sku].items():
            for leaf, qty in flatten(child).items():
                total[leaf] += n * qty
        return dict(total)

    for query in queries:
        seen = query["seen"]
        at = query["at"]
        visible, _ = _visible(products, seen)
        active = {normalize(r["sku"]): r for r in visible}
        price_rows, _ = _visible(prices, seen)
        chosen_prices = {}
        for sku in active:
            candidates = [
                r for r in price_rows if normalize(r["sku"]) == sku and r["start"] <= at < r["end"]
            ]
            if candidates:
                p = min(candidates, key=lambda r: (-r["priority"], -r["start"], r["id"]))
                chosen_prices[sku] = p["cents"] * (10000 - p["discount_bps"]) // 10000
        stock = Counter()
        held = Counter()
        orphan = set()
        movements_now, _ = _visible(movements, seen)
        for r in movements_now:
            if r["at"] <= at:
                sku = normalize(r["sku"])
                if sku not in active:
                    orphan.add(r["id"])
                    continue
                stock[(sku, r["warehouse"])] += r["delta"]
        reserve_now, _ = _visible(reservations, seen)
        for r in reserve_now:
            if r["state"] == "held" and r["start"] <= at < r["until"]:
                sku = normalize(r["sku"])
                if sku not in active:
                    orphan.add(r["id"])
                    continue
                held[(sku, r["warehouse"])] += r["qty"]
        leaves = sorted(set(active) - set(recipes))
        available = {}
        bins = []
        for sku in leaves:
            for wh in sorted(warehouses):
                net = stock[(sku, wh)] - held[(sku, wh)]
                bins.append(
                    {
                        "sku": sku,
                        "warehouse": wh,
                        "stock": stock[(sku, wh)],
                        "reserved": held[(sku, wh)],
                        "available": max(0, net) if warehouses[wh] and active[sku]["active"] else 0,
                    }
                )
            available[sku] = sum(r["available"] for r in bins if r["sku"] == sku)
        sell = []
        excluded = []
        for sku in sorted(active):
            requirements = flatten(sku)
            reason = None
            if not active[sku]["active"]:
                reason = "inactive"
            elif sku not in chosen_prices:
                reason = "no_price"
            elif any(leaf not in active or not active[leaf]["active"] for leaf in requirements):
                reason = "inactive_component"
            if reason:
                excluded.append({"sku": sku, "reason": reason})
                continue
            cap = min(available.get(leaf, 0) // qty for leaf, qty in requirements.items())
            sell.append(
                {
                    "sku": sku,
                    "name": active[sku]["name"],
                    "price_cents": chosen_prices[sku],
                    "standalone_available": cap,
                    "requirements": requirements,
                }
            )
        eligible = [r for r in sell if orders.get(r["sku"], 0) > 0]
        best = None
        allocation = None
        used_best = None
        for vector in itertools.product(*(range(orders[r["sku"]] + 1) for r in eligible)):
            used = Counter()
            for r, qty in zip(eligible, vector, strict=True):
                for leaf, amount in r["requirements"].items():
                    used[leaf] += qty * amount
            if any(qty > available.get(leaf, 0) for leaf, qty in used.items()):
                continue
            units = sum(vector)
            revenue = sum(qty * r["price_cents"] for qty, r in zip(vector, eligible, strict=True))
            key = (-units, -revenue, vector)
            if best is None or key < best:
                best = key
                allocation = vector
                used_best = used
        reports.append(
            {
                "snapshot": query["name"],
                "bins": bins,
                "catalog": sell,
                "excluded": excluded,
                "orphan_entries": sorted(orphan),
                "allocation": [
                    {"sku": r["sku"], "quantity": q}
                    for r, q in zip(eligible, allocation, strict=True)
                    if q
                ],
                "units": -best[0],
                "revenue_cents": -best[1],
                "remaining": {sku: available[sku] - used_best[sku] for sku in leaves},
            }
        )
    return reports


def _catalog_fixture():
    names = {
        "A": "Adapter",
        "B": "Battery",
        "C": "Cable",
        "D": "Dock",
        "E": "Extension",
        "K1": "Travel kit",
        "K2": "Office kit",
        "K3": "Team kit",
        "K4": "Spare kit",
    }
    products = [
        {
            "id": "p" + sku,
            "sku": sku.lower(),
            "name": name,
            "active": True,
            "rev": 1,
            "seen": 1,
            "op": "put",
        }
        for sku, name in names.items()
    ]
    products += [
        dict(products[3], rev=2, seen=65, active=False),
        dict(products[3], rev=3, seen=90, active=True),
    ]
    recipes = {
        "K1": {"A": 1, "C": 2},
        "K2": {"K1": 1, "B": 1},
        "K3": {"K1": 2, "D": 1},
        "K4": {"B": 2, "E": 1},
    }
    prices = []
    for i, sku in enumerate(names):
        prices.append(
            {
                "id": "price-" + sku,
                "sku": sku,
                "cents": 101 + i * 213,
                "discount_bps": i * 113,
                "start": 0,
                "end": 40,
                "priority": 0,
                "rev": 1,
                "seen": 1,
                "op": "put",
            }
        )
    prices += [
        dict(
            prices[5], id="special-z", start=10, end=25, priority=2, cents=1201, discount_bps=1750
        ),
        dict(
            prices[5], id="special-a", start=10, end=25, priority=2, cents=1401, discount_bps=1750
        ),
        dict(prices[2], rev=2, seen=70, start=18, end=40, cents=777, discount_bps=333),
        {"id": "price-E", "rev": 2, "seen": 85, "op": "delete"},
    ]
    movements = []
    for i, sku in enumerate("ABCDE"):
        for j, wh in enumerate(("east", "west", "quarantine")):
            movements.append(
                {
                    "id": f"m{i}{j}",
                    "sku": sku.lower(),
                    "warehouse": wh,
                    "delta": 4 + i + j,
                    "at": 2,
                    "rev": 1,
                    "seen": 2,
                    "op": "put",
                }
            )
    movements += [
        dict(movements[0], id="negative", at=14, seen=15, delta=-8),
        dict(movements[4], rev=2, seen=67, delta=2),
        dict(movements[6], id="late-restock", at=9, seen=80, delta=7),
        dict(movements[0], id="orphan-stock", sku="unknown", at=3, delta=99),
    ]
    reservations = [
        {
            "id": f"r{i}",
            "sku": "ABCDE"[i % 5],
            "warehouse": "east" if i % 2 else "west",
            "qty": 2 + i % 3,
            "start": 5 + i,
            "until": 19 + i,
            "state": "held",
            "rev": 1,
            "seen": 10 + i,
            "op": "put",
        }
        for i in range(9)
    ]
    reservations += [
        dict(reservations[0], rev=2, seen=60, state="cancelled"),
        dict(reservations[3], rev=2, seen=75, qty=12),
    ]
    orders = {"A": 2, "B": 1, "K1": 3, "K2": 2, "K3": 2, "K4": 2}
    queries = [
        {"name": "opening", "at": 10, "seen": 50},
        {"name": "reconciled", "at": 20, "seen": 80},
        {"name": "late_restatement", "at": 20, "seen": 100},
    ]
    return (
        products,
        prices,
        movements,
        reservations,
        recipes,
        {"east": True, "west": True, "quarantine": False},
        orders,
        queries,
    )


(
    _CAT_PRODUCTS,
    _CAT_PRICES,
    _CAT_MOVES,
    _CAT_HOLDS,
    _CAT_RECIPES,
    _CAT_WAREHOUSES,
    _CAT_ORDERS,
    _CAT_QUERIES,
) = _catalog_fixture()
TASK_476 = artifact_task(
    number=476,
    slug="catalog_bundle_allocation",
    name="Reconcile dated stock and allocate nested catalog kits",
    parent=354,
    prompt="""Собери аудит каталога и оптимальное исполнение заказов для snapshots.json.
products.jsonl, prices.jsonl, movements.jsonl, reservations.jsonl — независимые истории:
в каждой сначала фильтруй seen<=снимок.seen, затем для каждого id максимум (rev,seen),
op=delete удаляет. Ревизия полностью заменяет строку. SKU нормализуй strip+upper.
Победители products имеют уникальные SKU. Их active/name актуальны для снимка независимо от at.
Для цены оставь start<=snapshot.at<end и выбери по SKU максимальный priority, затем start,
затем минимальный id. price_cents=floor(cents*(10000-discount_bps)/10000).
Stock по (SKU,warehouse) — сумма delta победителей movements с at<=snapshot.at.
Reserved — сумма qty победителей reservations со state=held и start<=at<until.
Неизвестный SKU в движении/действующей резервации не учитывай, но включи id в orphan_entries.
В каждом складе отдельно available=max(0,stock-reserved), а если warehouse=false или
product.active=false, available=0. Нельзя покрыть долг одного склада запасом другого.
recipes.json — ациклические наборы: количества вложенных наборов рекурсивно раскрываются
до листовых SKU, совпавшие листья суммируются. Запасы самих наборов не используются.
В bins выведи все листовые SKU продуктов x все склады по (sku,warehouse), поля
{sku,warehouse,stock,reserved,available}. Для каждого продукта исключение выбирай в порядке:
inactive, no_price, inactive_component (хотя бы один лист отсутствует/неактивен).
Остальные входят в catalog, включая нулевой запас: {sku,name,price_cents,standalone_available,
requirements:{leaf:quantity}}. standalone_available=min(floor(суммарный available листа/requirement)).
Все массивы продуктов по SKU. excluded=[{sku,reason}].
orders.json задаёт максимальный спрос по продаваемому SKU. Выбери целые количества0..спрос,
одновременно укладывающиеся в ОДИН общий листовой запас. Цель: максимальное число единиц
проданных SKU (набор считается одной), затем максимальная выручка, затем лексикографически
МИНИМАЛЬНЫЙ вектор количеств по возрастающим eligible SKU (catalog со спросом>0, нули участвуют).
answer.json — массив по snapshots: {snapshot:name,bins,catalog,excluded,orphan_entries,
allocation:[{sku,quantity}],units,revenue_cents,remaining:{leaf:неиспользованный available}}.
allocation только положительные количества по SKU; orphan_entries отсортирован без повторов.
remaining включает все листовые SKU. Все деньги и количества — точные целые.""",
    files={
        "products.jsonl": _jsonl(_CAT_PRODUCTS),
        "prices.jsonl": _jsonl(_CAT_PRICES),
        "movements.jsonl": _jsonl(_CAT_MOVES),
        "reservations.jsonl": _jsonl(_CAT_HOLDS),
        "recipes.json": dumps(_CAT_RECIPES),
        "warehouses.json": dumps(_CAT_WAREHOUSES),
        "orders.json": dumps(_CAT_ORDERS),
        "snapshots.json": dumps(_CAT_QUERIES),
    },
    expected=catalog_report(
        _CAT_PRODUCTS,
        _CAT_PRICES,
        _CAT_MOVES,
        _CAT_HOLDS,
        _CAT_RECIPES,
        _CAT_WAREHOUSES,
        _CAT_ORDERS,
        _CAT_QUERIES,
    ),
    tags=("catalog", "bitemporal", "bundle-optimization"),
)
TASKS.append(TASK_476)


LOGFMT_GOLD = _cli(r"""
import re

def parse(s):
    fields={}; i=0; n=len(s)
    while i<n and s[i]==' ': i+=1
    while i<n:
        m=re.match(r'[a-z_]+=',s[i:])
        if not m: return None
        key=m.group()[:-1]; i+=len(m.group())
        if key in fields: return None
        if i<n and s[i]=='"':
            i+=1; value=''; closed=False
            while i<n:
                ch=s[i]; i+=1
                if ch=='"': closed=True; break
                if ch=='\\':
                    if i==n or s[i] not in {'"','\\','n','t'}: return None
                    ch={'"':'"','\\':'\\','n':'\n','t':'\t'}[s[i]]; i+=1
                elif ord(ch)<32: return None
                value+=ch
            if not closed: return None
        else:
            start=i
            while i<n and s[i]!=' ':
                if s[i] in '"\\' or ord(s[i])<33: return None
                i+=1
            value=s[start:i]
            if not value: return None
        fields[key]=value
        if i<n and s[i]!=' ': return None
        while i<n and s[i]==' ': i+=1
    if set(fields) not in ({'ts','svc','level','id'},{'ts','svc','level','id','msg'}): return None
    if not re.fullmatch(r'-?(0|[1-9][0-9]*)',fields['ts']): return None
    if not re.fullmatch(r'[a-z][a-z0-9_-]*',fields['svc']): return None
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',fields['id']): return None
    if fields['level'] not in ('DEBUG','INFO','WARN','ERROR'): return None
    fields['ts']=int(fields['ts']); return fields

def solve(x):
    invalid=[]; late=[]; duplicates=[]; accepted=[]; seen=set(); high=None
    for i,line in enumerate(x['lines'],1):
        r=parse(line)
        if r is None: invalid.append(i); continue
        watermark=None if high is None else high-x['lateness']
        high=r['ts'] if high is None else max(high,r['ts'])
        if watermark is not None and r['ts']<watermark: late.append(i); continue
        key=(r['svc'],r['id'])
        if key in seen: duplicates.append(i); continue
        seen.add(key); accepted.append(r)
    services=[]
    for svc in sorted({r['svc'] for r in accepted}):
        rows=sorted([r for r in accepted if r['svc']==svc],key=lambda r:(r['ts'],r['id']))
        counts={level:sum(r['level']==level for r in rows) for level in ('DEBUG','INFO','WARN','ERROR')}
        bursts=[]; current=[]; last=None
        for r in rows:
            if r['level']!='ERROR':
                if current: bursts.append(current)
                current=[]; last=None
            else:
                if current and r['ts']-last>x['gap']: bursts.append(current); current=[]
                current.append(r['id']); last=r['ts']
        if current: bursts.append(current)
        services.append({'svc':svc,'counts':counts,'bursts':bursts})
    return {'invalid':invalid,'late':late,'duplicates':duplicates,
            'accepted':[r['svc']+'/'+r['id'] for r in accepted],'services':services}
""")


def logfmt_oracle(x):
    # A regular-language scanner and JSON string decoder, independent from the
    # character-at-a-time reference lexer.
    token = re.compile(r'([a-z_]+)=("(?:[^"\\\x00-\x1f]|\\["\\nt])*"|[^ "\\\x00-\x20]+)(?: +|$)')
    parsed = []
    invalid = []
    for i, line in enumerate(x["lines"], 1):
        s = line.strip(" ")
        position = 0
        pairs = []
        while position < len(s):
            m = token.match(s, position)
            if m is None:
                break
            v = m[2]
            pairs.append((m[1], json.loads(v) if v.startswith('"') else v))
            position = m.end()
        r = dict(pairs)
        valid = (
            position == len(s)
            and len(r) == len(pairs)
            and set(r) in ({"ts", "svc", "level", "id"}, {"ts", "svc", "level", "id", "msg"})
            and re.fullmatch(r"-?(0|[1-9][0-9]*)", r.get("ts", "")) is not None
            and re.fullmatch(r"[a-z][a-z0-9_-]*", r.get("svc", "")) is not None
            and re.fullmatch(r"[A-Za-z0-9_.-]+", r.get("id", "")) is not None
            and r.get("level") in ("DEBUG", "INFO", "WARN", "ERROR")
        )
        if not valid:
            invalid.append(i)
        else:
            r["ts"] = int(r["ts"])
            parsed.append((i, r))
    accepted = []
    late = []
    duplicates = []
    for index, (i, r) in enumerate(parsed):
        previous = [p["ts"] for _, p in parsed[:index]]
        if previous and r["ts"] < max(previous) - x["lateness"]:
            late.append(i)
        elif any(p["svc"] == r["svc"] and p["id"] == r["id"] for _, p in accepted):
            duplicates.append(i)
        else:
            accepted.append((i, r))
    services = []
    for svc in sorted({r["svc"] for _, r in accepted}):
        rows = sorted((r for _, r in accepted if r["svc"] == svc), key=lambda r: (r["ts"], r["id"]))
        starts = []
        for i, r in enumerate(rows):
            if r["level"] == "ERROR" and (
                i == 0 or rows[i - 1]["level"] != "ERROR" or r["ts"] - rows[i - 1]["ts"] > x["gap"]
            ):
                starts.append(i)
        bursts = []
        for start in starts:
            group = [rows[start]["id"]]
            i = start + 1
            while (
                i < len(rows)
                and rows[i]["level"] == "ERROR"
                and rows[i]["ts"] - rows[i - 1]["ts"] <= x["gap"]
            ):
                group.append(rows[i]["id"])
                i += 1
            bursts.append(group)
        services.append(
            {
                "svc": svc,
                "counts": {
                    k: sum(r["level"] == k for r in rows)
                    for k in ("DEBUG", "INFO", "WARN", "ERROR")
                },
                "bursts": bursts,
            }
        )
    return {
        "invalid": invalid,
        "late": late,
        "duplicates": duplicates,
        "accepted": [r["svc"] + "/" + r["id"] for _, r in accepted],
        "services": services,
    }


def _logfmt_cases():
    def good(t, i, level="ERROR", svc="api"):
        return f"ts={t} svc={svc} level={level} id={i}"

    cases = [
        {
            "lateness": 3,
            "gap": 2,
            "lines": [
                good(5, "a"),
                good(2, "b"),
                good(1, "c"),
                good(6, "a"),
                good(7, "d", "INFO"),
                good(8, "e"),
            ],
        },
        {
            "lateness": 10,
            "gap": 5,
            "lines": [
                'msg="level=ERROR svc=bad" id=a level=INFO svc=api ts=1',
                'ts=2 svc=api level=ERROR id=b msg="quote: \\""',
            ],
        },
    ]
    specials = [
        "",
        "ts=01 svc=a level=ERROR id=x",
        "ts=1 svc=a level=ERROR id=x extra=z",
        "ts=1 svc=a level=ERROR id=x id=y",
        "ts=1\tsvc=a level=ERROR id=x",
        "ts=1 svc=a level=error id=x",
        'ts=1 svc=a level=ERROR id=x msg="bad\\q"',
        'ts=1 svc=a level=ERROR id=x msg="unclosed',
        'ts=1 svc=a level=ERROR id=x msg=""tail',
        'ts="-0" svc="a" level="ERROR" id="x" msg="\\n\\t\\\\\\""',
        '  ts=1  svc=a level=ERROR id=x msg=""   ',
        "ts=1 svc=A level=ERROR id=x",
    ]
    for j, special in enumerate(specials):
        cases.append(
            {
                "lateness": j % 4,
                "gap": j % 3,
                "lines": [
                    good(0, "z"),
                    special,
                    good(3, "b"),
                    good(2, "c", "INFO"),
                    good(3, "b"),
                    good(4, "d"),
                    good(2, "b"),
                    good(1, "a", svc="other"),
                ],
            }
        )
    cases.append(
        {
            "lateness": 0,
            "gap": 0,
            "lines": [good(-2, "b"), good(-2, "a"), good(-2, "c", "INFO"), good(-2, "d")],
        }
    )
    cases.append({"lateness": 99, "gap": 99, "lines": []})
    return [(x, logfmt_oracle(x)) for x in cases]


TASK_473 = program_task(
    number=473,
    slug="watermarked_logfmt",
    name="Parse logfmt and reconcile event-time watermarks",
    parent=352,
    prompt=r"""Вход {lines,lateness,gap}; lateness/gap — неотрицательные целые. Разбери каждую строку
строгого logfmt, затем обработай поток. Строка — одна или более пар key=value, разделённых
одним или более ASCII spaces; внешние spaces допустимы, tabs разделителями не являются.
key=[a-z_]+, без повторов. Значение либо непустая последовательность символов без spaces,
кавычки, обратного слеша и ASCII control (<33), либо строка в двойных кавычках.
В кавычках допустимы обычные символы кроме " и ASCII control (<32); escape только
\", \\, \n, \t. После закрывающей кавычки обязателен пробел или конец. Склеенных quoted/unquoted
значений нет. Кавычки декодируются; escapes за пределами quoted значения запрещены.
Обязательные ключи ровно ts,svc,level,id; дополнительно разрешён только msg.
ts после декодирования соответствует -?(0|[1-9][0-9]*); svc=[a-z][a-z0-9_-]*;
id=[A-Za-z0-9_.-]+; level строго DEBUG,INFO,WARN,ERROR. msg любой, включая пустой.
Невалидная строка целиком игнорируется и не меняет watermark.
Для каждой валидной строки сравни ts с max_ts всех ПРЕДЫДУЩИХ валидных строк минус lateness:
строго меньше — late. Затем обнови max_ts даже у late/duplicate. Если не late, а пара
(svc,id) уже принималась, это duplicate. Иначе строка accepted. Важен приоритет late перед duplicate.
После прохода для каждого service сортируй accepted по (ts,id) и выдели ERROR bursts:
соседние записи должны обе быть ERROR и иметь разрыв<=gap; любая не-ERROR разрывает burst.
Выход ровно {invalid:[номера],late:[номера],duplicates:[номера],accepted:["svc/id",...],services:[...]}.
Номера строк с1 в порядке входа; accepted также по входу. services по svc, только принятые,
элемент {svc,counts:{DEBUG,INFO,WARN,ERROR},bursts:[[id,...],...]}; все счётчики включая нули.
До100 строк по1000 символов; |ts|<=10^9. Декои внутри msg не являются полями.""",
    cases=_logfmt_cases(),
    gold=LOGFMT_GOLD,
    tags=("logs", "lexer", "watermark"),
)
TASKS.append(TASK_473)


WAREHOUSE_GOLD = _cli("""
def solve(x):
    lots={r['id']:r for r in x['lots']}; free={(r['id'],r['warehouse']):r['qty'] for r in x['lots']}
    held={}; audit=[]; shipped={}; expired={}
    def add(d,k,n): d[k]=d.get(k,0)+n
    def advance(t):
        for h in held.values():
            if h['state']=='held' and h['until']<=t:
                for a in h['allocations']: add(free,(a['lot'],a['warehouse']),a['qty'])
                h['state']='expired'
        for (lot,wh),qty in list(free.items()):
            if lots[lot]['expires']<=t:
                add(expired,lots[lot]['sku'],qty); free[(lot,wh)]=0
    for e in sorted(x['events'],key=lambda e:(e['at'],e['id'])):
        if e['at']>x['horizon']: continue
        advance(e['at']); status='accepted'
        if e['op'] in ('reserve','transfer'):
            warehouses=e['warehouses'] if e['op']=='reserve' else [e['source']]
            until=e['until'] if e['op']=='reserve' else e['at']
            candidates=sorted([(lots[lot]['expires'],warehouses.index(wh),lot,wh,qty)
                               for (lot,wh),qty in free.items() if qty and wh in warehouses and lots[lot]['sku']==e['sku'] and lots[lot]['expires']>until])
            allocation=[]; need=e['qty']
            for expiry,priority,lot,wh,qty in candidates:
                take=min(need,qty)
                if take: allocation.append({'lot':lot,'warehouse':wh,'qty':take}); need-=take
            if need: status='insufficient'
            else:
                for a in allocation:
                    free[(a['lot'],a['warehouse'])]-=a['qty']
                    if e['op']=='transfer': add(free,(a['lot'],e['destination']),a['qty'])
            if e['op']=='reserve': held[e['id']]={'id':e['id'],'state':'rejected' if need else 'held','until':e['until'],'allocations':[] if need else allocation}
        else:
            h=held.get(e['hold'])
            if h is None or h['state']!='held': status='inactive_hold'
            else:
                for a in h['allocations']:
                    if e['op']=='cancel': add(free,(a['lot'],a['warehouse']),a['qty'])
                    else: add(shipped,lots[a['lot']]['sku'],a['qty'])
                h['state']='cancelled' if e['op']=='cancel' else 'shipped'
        audit.append({'id':e['id'],'status':status})
    advance(x['horizon']); reserved={}
    for h in held.values():
        if h['state']=='held':
            for a in h['allocations']: add(reserved,(a['lot'],a['warehouse']),a['qty'])
    inventory=[{'lot':lot,'warehouse':wh,'free':free.get((lot,wh),0),'held':reserved.get((lot,wh),0)}
               for lot,wh in sorted(set(free)|set(reserved)) if free.get((lot,wh),0)+reserved.get((lot,wh),0)]
    skus=sorted({r['sku'] for r in x['lots']})
    return {'inventory':inventory,'holds':[held[k] for k in sorted(held)],'audit':audit,
            'shipped':{k:shipped.get(k,0) for k in skus},'expired_units':{k:expired.get(k,0) for k in skus}}
""")


def warehouse_oracle(x):
    # Unit tokens are a second representation: no aggregate stock arithmetic.
    units = [
        {
            "lot": r["id"],
            "sku": r["sku"],
            "wh": r["warehouse"],
            "expiry": r["expires"],
            "state": "free",
            "hold": None,
        }
        for r in x["lots"]
        for _ in range(r["qty"])
    ]
    holds = {}
    audit = []

    def advance(t):
        for h in holds.values():
            if h["state"] == "held" and h["until"] <= t:
                for u in units:
                    if u["state"] == "held" and u["hold"] == h["id"]:
                        u["state"] = "free"
                        u["hold"] = None
                h["state"] = "expired"
        for u in units:
            if u["state"] == "free" and u["expiry"] <= t:
                u["state"] = "expired"

    for e in sorted(x["events"], key=lambda r: (r["at"], r["id"])):
        if e["at"] > x["horizon"]:
            continue
        advance(e["at"])
        status = "accepted"
        if e["op"] in ("reserve", "transfer"):
            whs = e["warehouses"] if e["op"] == "reserve" else [e["source"]]
            until = e["until"] if e["op"] == "reserve" else e["at"]
            candidates = [
                u
                for u in units
                if u["state"] == "free"
                and u["sku"] == e["sku"]
                and u["wh"] in whs
                and u["expiry"] > until
            ]
            candidates.sort(key=lambda u: (u["expiry"], whs.index(u["wh"]), u["lot"]))
            if len(candidates) < e["qty"]:
                status = "insufficient"
                chosen = []
            else:
                chosen = candidates[: e["qty"]]
            counts = {}
            for u in chosen:
                key = (u["lot"], u["wh"])
                counts[key] = counts.get(key, 0) + 1
                if e["op"] == "reserve":
                    u["state"] = "held"
                    u["hold"] = e["id"]
                else:
                    u["wh"] = e["destination"]
            if e["op"] == "reserve":
                holds[e["id"]] = {
                    "id": e["id"],
                    "state": "rejected" if status != "accepted" else "held",
                    "until": e["until"],
                    "allocations": [
                        {"lot": lot, "warehouse": wh, "qty": qty}
                        for (lot, wh), qty in counts.items()
                    ],
                }
        elif e["hold"] not in holds or holds[e["hold"]]["state"] != "held":
            status = "inactive_hold"
        else:
            h = holds[e["hold"]]
            for u in units:
                if u["state"] == "held" and u["hold"] == h["id"]:
                    u["state"] = "free" if e["op"] == "cancel" else "shipped"
                    u["hold"] = None
            h["state"] = "cancelled" if e["op"] == "cancel" else "shipped"
        audit.append({"id": e["id"], "status": status})
    advance(x["horizon"])
    inventory = []
    for lot, wh in sorted({(u["lot"], u["wh"]) for u in units if u["state"] in ("free", "held")}):
        inventory.append(
            {
                "lot": lot,
                "warehouse": wh,
                "free": sum(
                    u["lot"] == lot and u["wh"] == wh and u["state"] == "free" for u in units
                ),
                "held": sum(
                    u["lot"] == lot and u["wh"] == wh and u["state"] == "held" for u in units
                ),
            }
        )
    skus = sorted({r["sku"] for r in x["lots"]})
    return {
        "inventory": inventory,
        "holds": [holds[k] for k in sorted(holds)],
        "audit": audit,
        "shipped": {k: sum(u["sku"] == k and u["state"] == "shipped" for u in units) for k in skus},
        "expired_units": {
            k: sum(u["sku"] == k and u["state"] == "expired" for u in units) for k in skus
        },
    }


def _warehouse_cases():
    cases = []
    for seed in range(16):
        rng = random.Random(477000 + seed)
        lots = [
            {
                "id": f"l{i}",
                "sku": "AB"[i % 2],
                "warehouse": "xy"[i % 2],
                "qty": 2 + i % 3,
                "expires": 6 + i * 3,
            }
            for i in range(6)
        ]
        events = []
        for i in range(10):
            op = ["reserve", "transfer", "reserve", "ship", "cancel"][i % 5]
            at = i // 2
            e = {"id": f"e{i:02}", "op": op, "at": at}
            if op == "reserve":
                e.update(
                    sku="AB"[rng.randrange(2)],
                    qty=rng.randrange(1, 7),
                    warehouses=["x", "y"] if i % 2 else ["y", "x"],
                    until=at + 2 + seed % 5,
                )
            elif op == "transfer":
                e.update(
                    sku="AB"[rng.randrange(2)],
                    qty=rng.randrange(1, 5),
                    source="xy"[i % 2],
                    destination="yx"[i % 2],
                )
            else:
                e["hold"] = f"e{rng.choice([0, 2, 5, 7]):02}"
            events.append(e)
        x = {"lots": lots, "events": events, "horizon": 5 + seed % 12}
        cases.append((x, warehouse_oracle(x)))
        if seed in (3, 9):
            y = json.loads(json.dumps(x))
            y["events"].reverse()
            y["lots"].reverse()
            cases.append((y, warehouse_oracle(y)))
    x = {
        "lots": [{"id": "l", "sku": "A", "warehouse": "x", "qty": 3, "expires": 10}],
        "events": [
            {
                "id": "h",
                "at": 1,
                "op": "reserve",
                "sku": "A",
                "qty": 2,
                "warehouses": ["x"],
                "until": 5,
            },
            {"id": "s", "at": 5, "op": "ship", "hold": "h"},
        ],
        "horizon": 5,
    }
    cases.insert(0, (x, warehouse_oracle(x)))
    cases.append(
        (
            {"lots": [], "events": [], "horizon": 10},
            {"inventory": [], "holds": [], "audit": [], "shipped": {}, "expired_units": {}},
        )
    )
    return cases


TASK_477 = program_task(
    number=477,
    slug="expiring_warehouse_ledger",
    name="Replay expiring inventory and atomic reservations",
    parent=354,
    prompt="""Вход {lots,events,horizon}. Все часы — неотрицательные целые. lots — начальные партии
{id,sku,warehouse,qty,expires}, id уникальны, qty>0, expires>0. В момент0 все единицы свободны.
Events имеют уникальный id и at; replay только at<=horizon, по (at,id), независимо от входного порядка.
Перед каждым событием и в horizon сначала освободи все held-резервы с until<=текущее время
(state=expired), затем спиши все свободные единицы партий с expires<=время в expired_units.
reserve: {id,at,op:"reserve",sku,qty,warehouses:[...],until}, until>at, qty>0, склады без повторов.
Выбирай только свободные единицы нужного SKU в этих складах с expires СТРОГО БОЛЬШЕ until.
Порядок выбора: expires, затем позиция склада в warehouses, затем lot.id. Если всего мало,
отказ атомарный status=insufficient и hold state=rejected, allocations=[]; иначе hold id=event.id,
state=held, until заданный, allocations — выбранные группы {lot,warehouse,qty} в порядке выбора.
transfer: {id,at,op:"transfer",sku,qty,source,destination}, source!=destination.
Перемещает только свободные единицы, из source в destination, по (expires,lot.id),
сохраняя lot.id/sku/expires. Мало единиц — атомарный insufficient; success=accepted.
cancel/ship: {id,at,op:"cancel"|"ship",hold}. Только hold state=held можно соответственно
освободить на исходные склады или отгрузить. Новый state cancelled/shipped; success=accepted.
Неизвестный либо уже не-held резерв даёт inactive_hold. Исторические allocations не меняются.
Активный резерв не может потерять партию до своего until благодаря правилу expires>until.
Выход {inventory,holds,audit,shipped,expired_units}. inventory по (lot,warehouse), только
строки с free+held>0: {lot,warehouse,free,held}. holds включает все обработанные reserve,
по id: {id,state,until,allocations}. audit для всех обработанных событий по replay:
{id,status}. shipped/expired_units — объекты SKU:суммарное количество с нулями для всех SKU lots.
До30 партий, суммарно<=200 единиц, до50 events; исходная партия может оказаться в нескольких складах.""",
    cases=_warehouse_cases(),
    gold=WAREHOUSE_GOLD,
    tags=("catalog", "inventory", "event-replay"),
)
TASKS.append(TASK_477)


def _segment_digest(body):
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def segment_report(files, target):
    valid = []
    invalid = []
    for name, text in sorted(files.items()):
        filename = name.removeprefix("segments/")
        m = re.fullmatch(
            r"segment_([0-9]{4})_([0-9]{4})\.g([0-9]{3})\.copy([1-9][0-9]*)\.json", filename
        )
        reason = None
        if m is None:
            reason = "name"
        else:
            try:
                value = json.loads(text)
            except (ValueError, TypeError):
                reason = "json"
                value = None
            if reason is None:
                keys = {"start", "end", "generation", "parent", "records", "digest"}
                if (
                    not isinstance(value, dict)
                    or set(value) != keys
                    or any(type(value[k]) is not int for k in ("start", "end", "generation"))
                    or not isinstance(value["parent"], str)
                    or not isinstance(value["digest"], str)
                    or not isinstance(value["records"], list)
                    or any(
                        not isinstance(r, dict)
                        or set(r) != {"seq", "value"}
                        or type(r["seq"]) is not int
                        or not isinstance(r["value"], str)
                        for r in value["records"]
                    )
                ):
                    reason = "schema"
                elif (
                    (value["start"], value["end"], value["generation"])
                    != tuple(map(int, m.groups()[:3]))
                    or not 0 <= value["start"] <= value["end"] < target
                    or [r["seq"] for r in value["records"]]
                    != list(range(value["start"], value["end"] + 1))
                ):
                    reason = "range"
                elif value["digest"] != _segment_digest(
                    {k: v for k, v in value.items() if k != "digest"}
                ):
                    reason = "checksum"
                else:
                    valid.append((filename, value))
        if reason:
            invalid.append({"file": filename, "reason": reason})
    grouped = defaultdict(list)
    for filename, value in valid:
        grouped[value["digest"]].append((filename, value))
    representatives = {digest: min(rows, key=lambda p: p[0]) for digest, rows in grouped.items()}
    duplicates = sorted(
        [sorted(name for name, v in rows) for rows in grouped.values() if len(rows) > 1]
    )
    chains = [[]]
    reachable = set()

    def extend(chain):
        end = chain[-1][1]["end"] + 1 if chain else 0
        parent = chain[-1][1]["digest"] if chain else "ROOT"
        for name, value in representatives.values():
            if value["start"] == end and value["parent"] == parent:
                reachable.add(name)
                next_chain = chain + [(name, value)]
                chains.append(next_chain)
                extend(next_chain)

    extend([])
    chain = min(
        chains,
        key=lambda c: (
            -(c[-1][1]["end"] + 1 if c else 0),
            -sum(v["generation"] for n, v in c),
            len(c),
            [n for n, v in c],
        ),
    )
    covered = chain[-1][1]["end"] + 1 if chain else 0
    content = "".join(
        f"{r['seq']}:{r['value']}\n" for name, value in chain for r in value["records"]
    )
    chosen = [name for name, value in chain]
    return {
        "selected": chosen,
        "covered": covered,
        "generation_sum": sum(v["generation"] for name, v in chain),
        "missing": [[covered, target]] if covered < target else [],
        "records_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "invalid": invalid,
        "duplicates": duplicates,
        "unreachable": sorted(
            name for name, v in representatives.values() if name not in reachable
        ),
        "unused": sorted(reachable - set(chosen)),
    }


def _segment_fixture():
    files = {}
    nodes = {}

    def add(label, start, end, generation, parent, copy=1):
        body = {
            "start": start,
            "end": end,
            "generation": generation,
            "parent": nodes[parent]["digest"] if parent else "ROOT",
            "records": [{"seq": i, "value": f"{label}:{i % 7}"} for i in range(start, end + 1)],
        }
        value = dict(body, digest=_segment_digest(body))
        nodes[label] = value
        name = f"segments/segment_{start:04}_{end:04}.g{generation:03}.copy{copy}.json"
        files[name] = dumps(value)
        return name

    add("a1", 0, 3, 2, None)
    add("a2", 4, 9, 2, "a1")
    add("a3", 10, 15, 2, "a2")
    add("a4", 16, 23, 2, "a3")
    add("b1", 0, 5, 1, None)
    add("b2", 6, 11, 4, "b1")
    add("b3", 12, 23, 3, "b2")
    add("c1", 0, 7, 1, None)
    add("c2", 8, 15, 5, "c1")
    add("c3", 16, 23, 2, "c2", 2)
    add("deadend", 6, 8, 99, "b1")
    add("deadend2", 9, 13, 99, "deadend")
    orphan = add("orphan", 14, 17, 8, "a1")
    files["segments/segment_0000_0005.g001.copy2.json"] = dumps(nodes["b1"])
    files["segments/segment_0012_0023.g003.copy9.json"] = dumps(nodes["b3"])
    files["segments/segment_0000_0005.g001.copy01.json"] = dumps(nodes["b1"])
    files["segments/segment_0000_0002.g001.copy1.json"] = '{"broken":\n'
    bad = dict(nodes["b1"])
    bad["generation"] = True
    files["segments/segment_0000_0005.g001.copy3.json"] = dumps(bad)
    bad = json.loads(json.dumps(nodes["a2"]))
    bad["records"][1]["seq"] = 99
    files["segments/segment_0004_0009.g002.copy3.json"] = dumps(bad)
    bad = dict(nodes["a3"])
    bad["digest"] = "0" * 64
    files["segments/segment_0010_0015.g002.copy3.json"] = dumps(bad)
    files["segments/notes.txt"] = "Recovered storage inventory; not a segment.\n"
    assert orphan in files
    return files


_SEGMENT_FILES = _segment_fixture()
TASK_478 = artifact_task(
    number=478,
    slug="segment_chain_recovery",
    name="Recover the best verifiable segment chain",
    parent=355,
    prompt="""Восстанови журнал [0,target) из candidate файлов segments; target лежит в manifest.json.
Имя строго segment_NNNN_MMMM.gGGG.copyK.json: N/M ровно4 ASCII цифры, G ровно3,
K положительное десятичное без ведущих нулей. Прочие имена invalid=name.
Содержимое должно быть JSON (иначе json), объект ровно с start,end,generation,parent,records,digest.
start/end/generation — целые, НЕ bool; parent/digest строки; records список объектов ровно
{seq:int не bool,value:string}. Нарушение типов/ключей — schema. Затем проверка range:
start/end/generation совпадают с N/M/G, 0<=start<=end<target, records.seq равен всем целым
start..end в этом порядке без дыр/дублей. Далее checksum: digest равен lowercase hex SHA256
канонического JSON объекта БЕЗ digest: sort_keys=True, ensure_ascii=False,separators=(',',':'),UTF-8,
без завершающего newline. Порядок причин name,json,schema,range,checksum; первая применимая.
Валидные копии с одинаковым digest объединяй, представитель — минимальное имя файла.
Все поля, включая parent, входят в digest. Криптографических коллизий во входе нет.
Цепочка начинается с start=0,parent="ROOT"; следующий start=предыдущий.end+1 и parent
равен digest предыдущего. Можно закончить до target. Глобальный выбор: максимальный covered
(последний end+1), затем максимальная сумма generation, затем минимум файлов, затем
лексикографически минимальный массив имён в порядке цепочки. Пустая цепочка допустима с covered=0.
answer.json ровно {selected:[имена],covered,generation_sum,missing:[[covered,target]] либо[],
records_sha256,invalid:[{file,reason}],duplicates:[[имена...],...],unreachable:[имена],unused:[имена]}.
Имена в выводе без префикса segments/. invalid по file. duplicates только группы>1,
каждая по имени и список групп лексикографически. unreachable — представители, которые
не входят НИ В ОДНУ корректную цепочку от ROOT; unused — достижимые представители вне выбранной.
Оба по имени. records_sha256 — SHA256 UTF-8 конкатенации строк 'seq:value\\n' всех records
выбранной цепочки (реальный перевод строки, не два символа). Для пустой цепочки хеш пустых байтов.""",
    files={**_SEGMENT_FILES, "manifest.json": dumps({"target": 24})},
    expected=segment_report(_SEGMENT_FILES, 24),
    tags=("segments", "integrity", "dag", "audit"),
)
TASKS.append(TASK_478)


def _normalize_contact(field, value):
    if field == "email":
        return value.strip().lower()
    if field == "name":
        return " ".join(value.split())
    value = value.strip()
    digits = "".join(ch for ch in value if ch in "0123456789")
    return ("+" if value.startswith("+") else "") + digits if digits else ""


def temporal_contacts_report(rows, links, config):
    reports = []
    for query in config["snapshots"]:
        selected, deleted = _visible(rows, query["seen"])
        people = {r["id"]: r for r in selected if r["effective"] <= query["at"]}
        values = {}
        tokens = defaultdict(list)
        for key, r in people.items():
            values[key] = {}
            for field, fact in r["fields"].items():
                if fact["at"] <= query["at"]:
                    value = "" if fact["erase"] else _normalize_contact(field, fact["value"])
                    if value or fact["erase"]:
                        values[key][field] = (value, fact["at"])
                    if field in ("email", "phone") and value:
                        tokens[(field, value)].append(key)
        edges = []
        suppressed = []
        for (kind, value), ids in sorted(tokens.items()):
            ids = sorted(ids)
            if len(ids) > config["hub_limit"]:
                suppressed.append({"kind": kind, "value": value, "ids": ids})
            else:
                edges += list(itertools.combinations(ids, 2))
        live_links, _ = _visible(links, query["seen"])
        used = []
        ignored = []
        for r in sorted(live_links, key=lambda r: r["id"]):
            if r["a"] not in people or r["b"] not in people:
                reason = "absent"
            elif not r["start"] <= query["at"] < r["end"]:
                reason = "expired"
            elif r["confidence"] < config["minimum_confidence"]:
                reason = "weak"
            else:
                reason = None
            if reason:
                ignored.append({"id": r["id"], "reason": reason})
            else:
                edges.append((r["a"], r["b"]))
                used.append(r["id"])
        neighbors = {key: set() for key in people}
        for a, b in edges:
            neighbors[a].add(b)
            neighbors[b].add(a)
        groups = []
        seen = set()
        for root in sorted(people):
            if root in seen:
                continue
            pending = [root]
            component = set()
            while pending:
                key = pending.pop()
                if key in component:
                    continue
                component.add(key)
                pending += list(neighbors[key] - component)
            seen |= component
            ids = sorted(component)
            fields = {}
            provenance = {}
            for field in ("name", "email", "phone"):
                candidates = [(key, values[key][field]) for key in ids if field in values[key]]
                if not candidates:
                    fields[field] = ""
                    provenance[field] = None
                else:
                    key, (value, at) = min(
                        candidates,
                        key=lambda kv: (
                            -kv[1][1],
                            -config["source_priority"][people[kv[0]]["source"]],
                            kv[0],
                        ),
                    )
                    fields[field] = value
                    provenance[field] = key
            groups.append(
                {"canonical_id": ids[0], "source_ids": ids, **fields, "provenance": provenance}
            )
        reports.append(
            {
                "snapshot": query["name"],
                "groups": groups,
                "suppressed": suppressed,
                "links_used": used,
                "links_ignored": ignored,
                "deleted_contacts": deleted,
            }
        )
    return reports


def _contacts_fixture():
    rows = []
    for i in range(18):
        email = f"person{i // 2}@example.test" if i % 5 else "support@example.test"
        phone = f"+7 (900) 000-{i // 3:04}" if i % 4 else ""
        rows.append(
            {
                "id": f"u{i:02}",
                "rev": 1,
                "seen": i + 1,
                "op": "put",
                "effective": 0,
                "source": ["crm", "signup", "support"][i % 3],
                "fields": {
                    "name": {"value": f"  Person  {i} ", "at": i + 5, "erase": False},
                    "email": {
                        "value": email.upper() if i % 2 else email,
                        "at": i + 3,
                        "erase": False,
                    },
                    "phone": {"value": phone, "at": i + 4, "erase": False},
                },
            }
        )
    corrections = []
    for j, i in enumerate((0, 3, 5, 7, 10, 12, 16)):
        r = json.loads(json.dumps(rows[i]))
        r.update(rev=2, seen=55 + j)
        if i in (5, 12):
            r = {"id": r["id"], "rev": 2, "seen": 55 + j, "op": "delete"}
        else:
            r["fields"]["email"] = {
                "value": "support@example.test" if j % 2 else f"person{(i + 2) // 2}@example.test",
                "at": 42 + j,
                "erase": False,
            }
            r["fields"]["name"] = {"value": "", "at": 45, "erase": True}
        corrections.append(r)
    corrections.append(dict(rows[5], rev=3, seen=100))
    links = [
        {
            "id": "l1",
            "rev": 1,
            "seen": 20,
            "op": "put",
            "a": "u01",
            "b": "u10",
            "start": 0,
            "end": 80,
            "confidence": 90,
        },
        {
            "id": "l2",
            "rev": 1,
            "seen": 20,
            "op": "put",
            "a": "u02",
            "b": "u16",
            "start": 0,
            "end": 50,
            "confidence": 99,
        },
        {
            "id": "l3",
            "rev": 1,
            "seen": 20,
            "op": "put",
            "a": "u04",
            "b": "u17",
            "start": 0,
            "end": 80,
            "confidence": 79,
        },
        {
            "id": "l4",
            "rev": 1,
            "seen": 20,
            "op": "put",
            "a": "u05",
            "b": "u17",
            "start": 0,
            "end": 80,
            "confidence": 85,
        },
        {
            "id": "l5",
            "rev": 1,
            "seen": 20,
            "op": "put",
            "a": "u99",
            "b": "u06",
            "start": 80,
            "end": 90,
            "confidence": 20,
        },
        {"id": "l1", "rev": 2, "seen": 65, "op": "delete"},
        {
            "id": "l3",
            "rev": 2,
            "seen": 105,
            "op": "put",
            "a": "u04",
            "b": "u17",
            "start": 0,
            "end": 80,
            "confidence": 95,
        },
    ]
    config = {
        "hub_limit": 2,
        "minimum_confidence": 80,
        "source_priority": {"crm": 3, "signup": 2, "support": 1},
        "snapshots": [
            {"name": "published", "seen": 40, "at": 50},
            {"name": "corrected", "seen": 80, "at": 50},
            {"name": "restored", "seen": 120, "at": 50},
        ],
    }
    random.Random(480).shuffle(rows)
    random.Random(4801).shuffle(corrections)
    return rows, corrections, links, config


_CONTACT_ROWS, _CONTACT_CORRECTIONS, _CONTACT_LINKS, _CONTACT_CONFIG = _contacts_fixture()
TASK_480 = artifact_task(
    number=480,
    slug="temporal_contact_provenance",
    name="Resolve identity snapshots with hub suppression and erasures",
    parent=356,
    prompt="""Восстанови контакты в трёх снимках config.json из contacts/*.jsonl и corrections.jsonl;
links.jsonl — отдельная история подтверждённых связей. В каждой истории для каждого id среди
seen<=snapshot.seen выбери максимум (rev,seen); delete удаляет, put полностью заменяет.
Контакт участвует, только если effective<=snapshot.at. Поле участвует, если fields[field].at<=at.
Нормализация: name=' '.join(value.split()) с Unicode whitespace; email=strip().lower();
phone: strip(), оставить только ASCII цифры, добавить ведущий '+' только если исходная
strip-строка начиналась '+'. Без цифр phone пуст. Другой канонизации нет.
Поле erase=true — явная очистка: значение пустое, но это кандидат на итоговое поле.
Обычное пустое нормализованное поле кандидатом НЕ является. Очищенные и будущие поля
не создают идентификационных связей. name связей никогда не создаёт.
Для каждого непустого нормализованного email и отдельно phone посчитай число активных id:
если оно строго больше hub_limit, токен полностью подавляется; иначе все его владельцы
соединяются. Объединение email и phone транзитивно, типы токенов не смешиваются.
Для выбранных неделетнутых links причина игнорирования проверяется в порядке absent
(нет одного из контактов), expired (не start<=at<end), weak (confidence<minimum_confidence).
Остальные links добавляют неориентированное ребро; подавление токена не запрещает явное ребро.
Группы — компоненты итогового графа, включая одиночные id. canonical_id=min id.
name/email/phone выбираются НЕ из одной записи: среди кандидатов группы максимальный field.at,
затем максимальный config.source_priority[source], затем минимальный контакт id.
Явная очистка участвует в тех же правилах и может стереть поле всей группы. Если кандидатов
нет — пустая строка и provenance=null; иначе provenance=id победителя, даже при очистке.
answer.json — массив по snapshots: {snapshot:name,groups:[{canonical_id,source_ids,name,email,
phone,provenance:{name,email,phone}}],suppressed:[{kind,value,ids}],links_used:[id],
links_ignored:[{id,reason}],deleted_contacts:[id]}. groups по canonical_id; source_ids по id;
suppressed по (kind,value), ids по id; link-массивы по id; deleted_contacts — все tombstone
победители независимо от effective, по id. Не добавляй другие поля.""",
    files={
        "contacts/crm.jsonl": _jsonl(_CONTACT_ROWS[:9]),
        "contacts/imported.jsonl": _jsonl(_CONTACT_ROWS[9:]),
        "corrections.jsonl": _jsonl(_CONTACT_CORRECTIONS),
        "links.jsonl": _jsonl(_CONTACT_LINKS),
        "config.json": dumps(_CONTACT_CONFIG),
    },
    expected=temporal_contacts_report(
        _CONTACT_ROWS + _CONTACT_CORRECTIONS, _CONTACT_LINKS, _CONTACT_CONFIG
    ),
    tags=("contacts", "temporal", "provenance", "graph"),
)
TASKS.append(TASK_480)


def latency_report(gateways, attempts, cancellations, queries):
    reports = []
    for query in queries:
        horizon = query["horizon"]
        gw, _ = _visible(gateways, query["seen"])
        gateway = {r["id"]: r for r in gw if r["start"] < horizon}
        att, _ = _visible(attempts, query["seen"])
        cancel, _ = _visible(cancellations, query["seen"])
        good = []
        invalid = []
        for r in sorted(att, key=lambda r: r["id"]):
            if r["request"] not in gateway:
                reason = "unknown_request"
            elif r["start"] < gateway[r["request"]]["start"]:
                reason = "before_start"
            elif r["finish"] is not None and r["finish"] < r["start"]:
                reason = "backwards"
            elif r["start"] >= horizon:
                reason = "after_horizon"
            else:
                reason = None
            if reason:
                invalid.append({"id": r["id"], "reason": reason})
            else:
                good.append(r)
        rows = []
        for request, g in sorted(gateway.items()):
            aa = [r for r in good if r["request"] == request]
            candidates = [
                (
                    min(g["deadline"], horizon),
                    3 if g["deadline"] <= horizon else 4,
                    "",
                    "timed_out" if g["deadline"] <= horizon else "open",
                )
            ]
            candidates += [
                (r["finish"], 0, r["id"], "complete")
                for r in aa
                if r["result"] == "ok" and r["finish"] is not None and r["finish"] <= horizon
            ]
            candidates += [
                (r["at"], 1, r["id"], "cancelled")
                for r in cancel
                if r["request"] == request and g["start"] <= r["at"] <= horizon
            ]
            if aa and all(
                r["result"] == "error" and r["finish"] is not None and r["finish"] <= horizon
                for r in aa
            ):
                candidates.append((max(r["finish"] for r in aa), 2, "", "failed"))
            end, priority, terminal, status = min(candidates)
            included = [r for r in aa if r["start"] <= end]
            intervals = [
                (r["start"], min(end, r["finish"] if r["finish"] is not None else end))
                for r in included
            ]
            work = sum(b - a for a, b in intervals)
            useful = 0
            if status == "complete":
                winner = next(r for r in included if r["id"] == terminal)
                useful = end - winner["start"]
            rows.append(
                {
                    "request": request,
                    "endpoint": g["endpoint"],
                    "status": status,
                    "terminal_id": terminal or None,
                    "latency": end - g["start"],
                    "attempts": len(included),
                    "work": work,
                    "busy": sum(b - a for a, b in _union(intervals)),
                    "wasted": work - useful,
                    "unfinished": sorted(
                        r["id"] for r in included if r["finish"] is None or r["finish"] > end
                    ),
                    "post_terminal": sorted(r["id"] for r in aa if r["start"] > end),
                }
            )
        summary = []
        for endpoint in sorted({r["endpoint"] for r in rows}):
            group = [r for r in rows if r["endpoint"] == endpoint]
            latencies = sorted(r["latency"] for r in group if r["status"] == "complete")
            summary.append(
                {
                    "endpoint": endpoint,
                    "counts": {
                        status: sum(r["status"] == status for r in group)
                        for status in ("complete", "failed", "cancelled", "timed_out", "open")
                    },
                    "p50": latencies[(len(latencies) + 1) // 2 - 1] if latencies else None,
                    "p95": latencies[(95 * len(latencies) + 99) // 100 - 1] if latencies else None,
                }
            )
        reports.append(
            {
                "snapshot": query["name"],
                "requests": rows,
                "endpoints": summary,
                "invalid_attempts": invalid,
            }
        )
    return reports


def _latency_fixture():
    gateways = [
        {
            "id": f"r{i:02}",
            "start": i * 4,
            "deadline": i * 4 + 18 + i % 3,
            "endpoint": ["/search", "/items", "/export"][i % 3],
            "rev": 1,
            "seen": i + 1,
            "op": "put",
        }
        for i in range(16)
    ]
    attempts = []
    for i, g in enumerate(gateways):
        for j in range(1 + i % 3):
            start = g["start"] + j * 3
            finish = start + 3 + (i * 3 + j) % 15
            result = "ok" if (i + j) % 4 == 0 else "error"
            if i % 7 == 3 and j == 0:
                finish = None
                result = None
            attempts.append(
                {
                    "id": f"a{i:02}-{j}",
                    "request": g["id"],
                    "start": start,
                    "finish": finish,
                    "result": result,
                    "rev": 1,
                    "seen": 20 + len(attempts),
                    "op": "put",
                }
            )
    attempts += [
        dict(attempts[0], id="bad-before", start=-1),
        dict(attempts[1], id="bad-backwards", finish=0),
        dict(attempts[2], id="bad-orphan", request="missing"),
        dict(attempts[3], id="bad-future", start=130, finish=140),
    ]
    corrections = []
    for j, index in enumerate((0, 3, 7, 11, 16, 20)):
        r = dict(attempts[index])
        r.update(rev=2, seen=90 + j)
        if j == 2:
            r = {"id": r["id"], "rev": 2, "seen": 90 + j, "op": "delete"}
        else:
            r["finish"] = r["start"] + 5
            r["result"] = "ok" if j % 2 else "error"
        corrections.append(r)
    corrections.append(dict(attempts[0], rev=3, seen=125, finish=18, result="ok"))
    gateways += [
        dict(gateways[0], rev=2, seen=85, deadline=18),
        dict(gateways[4], rev=2, seen=115, endpoint="/search"),
    ]
    cancels = [
        {
            "id": f"c{i}",
            "request": f"r{i * 2:02}",
            "at": i * 8 + 9,
            "rev": 1,
            "seen": 40 + i,
            "op": "put",
        }
        for i in range(7)
    ]
    cancels += [
        {"id": "c0", "request": "r00", "at": 18, "rev": 2, "seen": 120, "op": "put"},
        {"id": "c2", "rev": 2, "seen": 100, "op": "delete"},
    ]
    queries = [
        {"name": "published", "seen": 80, "horizon": 100},
        {"name": "corrected", "seen": 110, "horizon": 100},
        {"name": "restated", "seen": 140, "horizon": 100},
    ]
    random.Random(482).shuffle(attempts)
    random.Random(4821).shuffle(corrections)
    return gateways, attempts, corrections, cancels, queries


_LAT_GW, _LAT_ATTEMPTS, _LAT_CORRECTIONS, _LAT_CANCELS, _LAT_QUERIES = _latency_fixture()
TASK_482 = artifact_task(
    number=482,
    slug="retry_terminal_races",
    name="Reconcile latency and wasted retries under terminal races",
    parent=357,
    prompt="""Согласуй gateway.jsonl, workers/*.jsonl, attempt-corrections.jsonl и cancellations.jsonl
для snapshots.json. Gateway, attempts (оба worker файла+corrections), cancellations — три
независимых истории: по id среди seen<=снимок выбери максимальную (rev,seen), delete удаляет,
put полностью заменяет. Gateway участвует только при start<horizon. Часы целые миллисекунды.
Валидируй выбранные attempts в порядке причин: unknown_request (нет gateway снимка),
before_start (attempt.start<gateway.start), backwards (finish!=null и finish<start),
after_horizon (start>=horizon). Первая причина — invalid_attempts; остальные допускаются.
finish=null соответствует result=null; остальные result=ok/error. Неизвестные cancellation
игнорируй; для известного request допускай только gateway.start<=at<=horizon.
Для каждого request построй терминальные кандидаты:
- каждый ok finish<=horizon: complete в finish, terminal_id=attempt.id;
- каждая допустимая cancellation: cancelled в at, terminal_id=cancellation.id;
- если есть хотя бы один валидный attempt и ВСЕ его валидные attempts завершились error
с finish<=horizon: failed в максимальный finish, terminal_id=null;
- deadline<=horizon: timed_out в deadline, иначе open в horizon, terminal_id=null.
Выбери минимальное время, при равенстве complete раньше cancelled раньше failed раньше
timed_out раньше open; внутри complete/cancelled минимальный id. Сам терминал не отменяет
валидирование последующих записей и не меняет условие ALL для failed.
В attempts/count/work входят только валидные попытки со start<=terminal time, остальные
идут в post_terminal. Каждая включённая работает на [start,min(finish,terminal)), где
null finish заменяется terminal. work — сумма длин с перекрытиями; busy — длина объединения;
wasted=work-длина победившей попытки при complete, иначе work. latency=terminal-gateway.start.
unfinished — включённые попытки с finish=null или finish>terminal. Оба массива id отсортированы.
answer.json — массив по snapshots: {snapshot:name,requests:[{request,endpoint,status,terminal_id,
latency,attempts,work,busy,wasted,unfinished,post_terminal}],endpoints:[{endpoint,counts,p50,p95}],
invalid_attempts:[{id,reason}]}. requests по request, endpoints по endpoint, invalid_attempts по id.
counts содержит complete,failed,cancelled,timed_out,open включая нули. p50/p95 только по
complete latency, nearest-rank ceil(p*N), при N=0 null. Ни дедлайн, ни отмена не означают
автоматическое удаление уже работающей попытки из work. Не добавляй другие поля.""",
    files={
        "gateway.jsonl": _jsonl(_LAT_GW),
        "workers/east.jsonl": _jsonl(_LAT_ATTEMPTS[:18]),
        "workers/west.jsonl": _jsonl(_LAT_ATTEMPTS[18:]),
        "attempt-corrections.jsonl": _jsonl(_LAT_CORRECTIONS),
        "cancellations.jsonl": _jsonl(_LAT_CANCELS),
        "snapshots.json": dumps(_LAT_QUERIES),
    },
    expected=latency_report(_LAT_GW, _LAT_ATTEMPTS + _LAT_CORRECTIONS, _LAT_CANCELS, _LAT_QUERIES),
    tags=("latency", "retries", "race-resolution", "audit"),
)
TASKS.append(TASK_482)
TASKS.sort(key=lambda task: int(task.id.split("_")[1]))


# Earlier reference functions remain available for baseline regression checks.
# Only these substantially hardened tasks are registered in the active task set.
from harness_bench.general_hard.data_v4 import build_data_v4  # noqa: E402

LEGACY_TASKS = TASKS
TASKS = build_data_v4(LEGACY_TASKS, globals())

# V5 replaces only the calibrated-too-easy asynchronous recovery descendant.
from harness_bench.general_hard.data_v5 import harden_479_v5  # noqa: E402

TASKS = harden_479_v5(TASKS)

from harness_bench.general_hard.data_v6 import harden_479_v6  # noqa: E402

TASKS = harden_479_v6(TASKS)
