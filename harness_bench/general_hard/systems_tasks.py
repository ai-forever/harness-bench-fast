"""Wave 20: systems tasks descended from Lite tasks 359--365.

Reference functions are deliberately standalone. Only their source is copied
into gold programs; neither these functions nor hidden cases enter workspaces.
"""

from __future__ import annotations

import inspect
import json
import textwrap

from . import systems_v4, systems_v5, systems_v6
from .common import artifact_task, dumps, program_task

TASKS = []
CASES = {}
SOLVERS = {}
ANCHORS = {}


def _program(number, slug, name, parent, prompt, solver, anchors, extra):
    cases = list(anchors) + [(value, solver(value)) for value in extra]
    CASES[number] = cases
    ANCHORS[number] = anchors
    SOLVERS[number] = solver
    gold = (
        textwrap.dedent(inspect.getsource(solver))
        + "\nimport json, sys\nfor line in sys.stdin:\n"
        + f"    print(json.dumps({solver.__name__}(json.loads(line)), ensure_ascii=False))\n"
    )
    TASKS.append(program_task(
        number=number, slug=slug, name=name, parent=parent, prompt=prompt,
        cases=cases, gold=gold, tags=("systems",),
    ))


def solve486(data):
    jobs = {j["id"]: dict(j, ready=j["release"], used=0, state="pending") for j in data["jobs"]}
    running = {}
    tokens = data["capacity"]
    blocked = 0
    log = []
    ended = {}
    for t in range(max((j["deadline"] for j in jobs.values()), default=0) + 1):
        if t and t % data["period"] == 0:
            tokens = min(data["capacity"], tokens + 1)
        for name in sorted(k for k, v in running.items() if v[0] == t):
            _, response, attempt = running.pop(name)
            job = jobs[name]
            outcome = response["result"]
            log.append({"id": name, "attempt": attempt, "end": t, "result": outcome})
            if outcome == "ok" or outcome == "fatal":
                job["state"] = outcome
                ended[name] = t
            elif attempt == len(job["responses"]):
                job["state"] = "exhausted"
                ended[name] = t
            else:
                delay = min(job["cap"], job["base"] * 2 ** (attempt - 1))
                job["ready"] = max(t + delay, t + response.get("after", 0))
                job["state"] = "pending"
            if outcome == "retry" and response.get("global", False):
                blocked = max(blocked, t + response.get("after", 0))
        for name, job in sorted(jobs.items()):
            if job["state"] == "pending" and (
                t == job["deadline"]
                or (job["ready"] <= t and t + job["responses"][job["used"]]["duration"] > job["deadline"])
            ):
                job["state"] = "deadline"
                ended[name] = t
        if t < blocked:
            continue
        ready = sorted(
            (j for j in jobs.values() if j["state"] == "pending" and j["ready"] <= t),
            key=lambda j: (j["priority"], j["id"]),
        )
        for job in ready:
            if not tokens or len(running) == data["slots"]:
                break
            response = job["responses"][job["used"]]
            job["used"] += 1
            job["state"] = "running"
            tokens -= 1
            running[job["id"]] = (t + response["duration"], response, job["used"])
            log.append({"id": job["id"], "attempt": job["used"], "start": t})
    return {
        "log": log,
        "jobs": [{"id": k, "state": j["state"], "attempts": j["used"], "end": ended[k]}
                 for k, j in sorted(jobs.items())],
    }


_RETRY_PROMPT = r"""
Построй точный дискретный симулятор общего retry-диспетчера. Вход:
{capacity,period,slots,jobs}; capacity и slots 1..4, period 1..10.
У каждого job уникальный ASCII id, release>=0, deadline<=150,
priority (меньше раньше), base>=1, cap>=1 и непустой responses:
{duration>=1,result:"ok"|"fatal"|"retry",after?:неотрицательное целое,global?:bool}.
Время — целые тики от0; initial token bucket содержит capacity токенов.
Каждый положительный тик, кратный period, добавляет ОДИН токен до capacity.
Запуск потребляет токен, завершение токен не возвращает; одновременно <=slots.
Один job не может иметь параллельных попыток.

На каждом t строго: (1) refill; (2) все завершения с end=t, по id;
(3) expiration по id; (4) запуски. Начало первой попытки >=release.
После retry попытки k (k начинается с1) ready =
end + max(min(cap,base*2**(k-1)),after по умолчанию0).
Если global=true на retry, общий запрет новых запусков действует до
max(старый запрет,end+after), включая исчерпавшую responses попытку.
В t равном концу запрета запуск разрешён. ok/fatal завершают job;
retry последнего response даёт exhausted. Остальные responses тогда не нужны.
Expiration касается только pending: если t==deadline ИЛИ ready<=t и
t+duration следующего response>deadline, заверши deadline в t.
Expiration не учитывает ожидание токенов, slots и общий запрет; это отдельный
этап, он работает и во время общего запрета. Завершение ровно в deadline допустимо.
После expiration, если нет общего запрета, выбирай pending ready<=t
по (priority,id), запускай пока есть tokens и slots. Новых завершений в t
нет, duration положительна. Все release<=deadline; ответы заданы заранее,
результат становится известен только при завершении.

Вывод ровно {log,jobs}. log в порядке событий, завершение:
{id,attempt,end,result}, запуск:{id,attempt,start}; expiration в log НЕ пишется.
jobs по id: {id,state,attempts,end}, state ok/fatal/exhausted/deadline;
end — тик терминального перехода, attempts — число фактических запусков.
Пустой jobs допустим. Ввод гарантированно соответствует описанной схеме.
"""


def _job(name, responses, **kw):
    return {"id": name, "release": 0, "deadline": 12, "priority": 0, "base": 2,
            "cap": 4, "responses": responses, **kw}


def _retry(jobs, **kw):
    return {"capacity": 2, "period": 3, "slots": 2, "jobs": jobs, **kw}


_r_ok = {"duration": 1, "result": "ok"}
_r_retry = {"duration": 1, "result": "retry"}
_r486_anchor = _retry([_job("a", [_r_retry, _r_ok])])
_program(
    486, "retry_dispatcher", "Replay a shared retry dispatcher", 359, _RETRY_PROMPT,
    solve486,
    [
        (_retry([]), {"log": [], "jobs": []}),
        (_r486_anchor, {
            "log": [{"id": "a", "attempt": 1, "start": 0},
                    {"id": "a", "attempt": 1, "end": 1, "result": "retry"},
                    {"id": "a", "attempt": 2, "start": 3},
                    {"id": "a", "attempt": 2, "end": 4, "result": "ok"}],
            "jobs": [{"id": "a", "state": "ok", "attempts": 2, "end": 4}],
        }),
    ],
    [
        _retry([_job("z", [_r_ok]), _job("a", [_r_ok]), _job("b", [_r_ok])], capacity=1),
        _retry([_job("a", [{"duration": 3, "result": "ok"}], deadline=3)]),
        _retry([_job("a", [{"duration": 3, "result": "ok"}], deadline=2)]),
        _retry([_job("a", [_r_ok], release=4, deadline=4)]),
        _retry([_job("a", [_r_retry] * 4 + [_r_ok], base=1, cap=2)]),
        _retry([_job("a", [{"duration": 1, "result": "fatal"}])]),
        _retry([_job("a", [{"duration": 1, "result": "retry", "global": True, "after": 6}]),
                _job("b", [_r_ok], release=1, deadline=4),
                _job("c", [_r_ok], release=1)]),
        _retry([_job("a", [{"duration": 2, "result": "retry", "global": True, "after": 4}, _r_ok]),
                _job("b", [{"duration": 2, "result": "retry", "global": True, "after": 7}, _r_ok]),
                _job("c", [_r_ok], priority=-1, release=2)], slots=2),
        _retry([_job("a", [_r_retry, _r_ok], deadline=3),
                _job("b", [{"duration": 3, "result": "ok"}], priority=-1)],
               capacity=1, slots=1, period=4),
        _retry([_job(str(i), [{"duration": i % 3 + 1, "result": "retry",
                              "after": i % 4, "global": i % 2 == 0}, _r_ok],
                           release=i % 2, priority=-i, deadline=10+i)
                for i in range(7)], period=2),
        _retry([_job("a", [_r_retry, _r_ok], base=20, deadline=4)]),
    ],
)


def solve487(data):
    from fractions import Fraction
    from functools import lru_cache

    jobs = sorted(data["jobs"], key=lambda j: j["id"])
    horizon = data["horizon"]

    @lru_cache(None)
    def dp(t, state):
        if t >= horizon:
            return Fraction(0), "stop"
        choices = [(Fraction(0), "stop")]
        if t + 1 <= horizon:
            choices.append((dp(t + 1, state)[0], "wait"))
        for i, j in enumerate(jobs):
            attempt, ready = state[i]
            if attempt < 0 or attempt >= len(j["attempts"]) or t < ready:
                continue
            a = j["attempts"][attempt]
            end = t + a["duration"]
            if end > min(horizon, j["deadline"]):
                continue
            yes = list(state)
            yes[i] = (-1, 0)
            no = list(state)
            no[i] = (attempt + 1, end + a["delay"])
            p = Fraction(*a["p"])
            value = p * (j["reward"] + dp(end, tuple(yes))[0]) + (1-p) * dp(end, tuple(no))[0]
            choices.append((value, j["id"]))
        order = {j["id"]: i for i, j in enumerate(jobs)}
        order["wait"], order["stop"] = len(jobs), len(jobs) + 1
        return min(choices, key=lambda pair: (-pair[0], order[pair[1]]))

    value, first = dp(0, tuple((0, j["release"]) for j in jobs))
    return {"value": [value.numerator, value.denominator], "first": first}


_POLICY_PROMPT = """
Рассчитай оптимальную АДАПТИВНУЮ retry-policy одного worker, без Monte Carlo.
Вход {horizon,jobs}; horizon 1..14, <=4 jobs с уникальными id (ASCII, кроме
wait/stop), reward положительное целое, release>=0, deadline<=horizon,
attempts (1..3) — {duration:1..5,delay:0..4,p:[числитель,знаменатель]}.
0<=числитель<=знаменатель<=10. Результаты независимы, p — вероятность успеха.
В t=0 все jobs не запущены. Доступные действия: stop; wait ровно1 тик;
attempt очередного job, если t>=ready и t+duration<=min(deadline,horizon).
Worker занят duration, затем узнаёт исход. Успех даёт reward ровно один раз
и навсегда закрывает job. Провал даёт0, расходует попытку и ставит ready=
end+delay ИМЕННО этой попытки. Готовность других jobs во время работы
не меняется. После последней неудачи job недоступен. ready первой=release.
В horizon допустимы только stop и награда за уже завершившуюся попытку.
Policy может выбирать дальнейшие действия в зависимости от всех исходов.
Нужно максимальное матожидание СУММЫ reward, без дисконтирования.
При точном равенстве ожиданий предпочитай доступный attempt по id,
затем wait, затем stop. Сравнения точными рациональными числами.
Выведи {value:[несократимый числитель,положительный знаменатель],first:действие}.
first — id, "wait" или "stop". Ввод валиден, пустые jobs допустимы.
"""


def _pj(name, p=(1, 2), **kw):
    return {"id": name, "reward": 4, "release": 0, "deadline": 6,
            "attempts": [{"duration": 1, "delay": 1, "p": list(p)}], **kw}


_program(
    487, "adaptive_retry_policy", "Optimize an adaptive retry policy exactly", 359,
    _POLICY_PROMPT, solve487,
    [
        ({"horizon": 2, "jobs": []}, {"value": [0, 1], "first": "wait"}),
        ({"horizon": 3, "jobs": [_pj("a", deadline=3,
           attempts=[{"duration": 1, "delay": 1, "p": [1, 2]}] * 2)]},
         {"value": [3, 1], "first": "a"}),
    ],
    [
        {"horizon": 6, "jobs": [_pj("a", (0, 1))]},
        {"horizon": 6, "jobs": [_pj("a", (1, 1), release=6)]},
        {"horizon": 6, "jobs": [_pj("b", (1, 1)), _pj("a", (1, 1))]},
        {"horizon": 3, "jobs": [_pj("a", deadline=3, reward=9,
            attempts=[{"duration": 3, "delay": 0, "p": [1, 2]}]),
            _pj("b", (1, 1), reward=5, deadline=3)]},
        {"horizon": 6, "jobs": [_pj("a", (1, 3), release=3),
                               _pj("b", (3, 4), reward=7)]},
        {"horizon": 6, "jobs": [_pj("a", attempts=[
            {"duration": 1, "delay": 0, "p": [0, 1]},
            {"duration": 2, "delay": 0, "p": [1, 1]}]),
            _pj("b", reward=6, deadline=2)]},
        {"horizon": 6, "jobs": [_pj("a", attempts=[
            {"duration": 2, "delay": 3, "p": [1, 2]},
            {"duration": 1, "delay": 0, "p": [1, 1]}]), _pj("b")]},
        *[{"horizon": 7, "jobs": [
            _pj(chr(97+i), reward=2+i*3+seed, release=i % 2, deadline=5+i,
                attempts=[{"duration": 1+(seed+i+k) % 3, "delay": (seed+k) % 3,
                           "p": [1+(seed+i+k) % 3, 4]} for k in range(3)])
            for i in range(3)]} for seed in range(5)],
    ],
)


def solve488(data):
    import re
    import unicodedata
    from urllib.parse import unquote_to_bytes

    def decode(raw):
        if re.search(r"%(?![0-9a-fA-F]{2})", raw):
            raise ValueError
        value = unquote_to_bytes(raw).decode("utf-8").replace("\\", "/")
        if "%" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError
        return unicodedata.normalize("NFC", value).casefold()

    def norm(raw, base=None):
        value = decode(raw)
        if not value or value.startswith("/") or ":" in value:
            raise ValueError
        parts = list(base or [])
        for p in value.split("/"):
            if base is not None and p == ".":
                continue
            if base is not None and p == "..":
                if not parts:
                    raise ValueError
                parts.pop()
                continue
            if p in ("", ".", "..") or p[-1:] in (".", " "):
                raise ValueError
            if p.split(".")[0] in {"con", "prn", "aux", "nul", *(
                    f"{prefix}{i}" for prefix in ("com", "lpt") for i in range(1, 10))}:
                raise ValueError
            parts.append(p)
        return "/".join(parts)

    accepted, errors = {}, []
    for i, item in enumerate(data["entries"], 1):
        try:
            path = norm(item["path"])
            target = norm(item["target"], path.split("/")[:-1]) if item["type"] in ("sym", "hard") else None
        except (ValueError, UnicodeError):
            errors.append({"index": i, "error": "unsafe"})
            continue
        if path in accepted:
            errors.append({"index": i, "error": "collision"})
            continue
        parents = ["/".join(path.split("/")[:n]) for n in range(1, len(path.split("/")))]
        if any(p in accepted and accepted[p]["type"] != "dir" for p in parents) or (
            item["type"] != "dir" and any(p.startswith(path + "/") for p in accepted)
        ):
            errors.append({"index": i, "error": "ancestry"})
            continue
        accepted[path] = {"path": path, "type": item["type"]}
        if target is not None:
            accepted[path]["target"] = target
        if item["type"] == "file":
            accepted[path]["size"] = item["size"]

    def resolve(path, seen):
        if path in seen:
            return None, "cycle"
        if not path or path not in accepted:
            return None, "missing"
        item = accepted[path]
        if item["type"] in ("file", "dir"):
            return path, None
        destination, error = resolve(item["target"], seen | {path})
        if not error and item["type"] == "hard" and accepted[destination]["type"] != "file":
            return None, "not_file"
        return destination, error

    result = []
    for path, item in sorted(accepted.items()):
        item = dict(item)
        if item["type"] in ("sym", "hard"):
            resolved, error = resolve(path, set())
            item["resolved"] = resolved
            item["error"] = error
        result.append(item)
    return {"entries": result, "errors": errors}


_ARCHIVE_PROMPT = r"""
Реализуй аудит переносимого архива без извлечения. Вход {entries:[...]};
entry содержит path, type="dir"|"file"|"sym"|"hard"; file содержит size>=0,
sym/hard — target. <=80 entries. Отсутствующие промежуточные директории
не создаются в результате. Вход структурно валиден.

Нормализация имени: каждый '%' обязан начинать %HH. Декодируй percent bytes
ровно один раз строгим UTF-8 (обычные Unicode-символы передаются как UTF-8);
замени '\' на '/', примени Unicode NFC, затем casefold.
Отклони unsafe: оставшийся '%', символ U+0000..001F или007F, пустая строка,
начальный '/', любой ':', пустой компонент, компонент '.' или '..',
компонент с последним символом '.' или ASCII-пробел; а также компоненты,
у которых часть до первой точки равна con/prn/aux/nul/com1..com9/lpt1..lpt9.
Для target те же правила, НО '.' пропускается, '..' удаляет один компонент
относительно РОДИТЕЛЯ имени ссылки; выход выше корня unsafe. Пустой
результат нормализации target допустим и позже даёт missing.

Проход в исходном порядке. Сначала проверь path И target (unsafe);
затем collision, если нормализованный path уже принят (даже dir/dir);
затем ancestry: любой уже принятый строгий предок не dir ИЛИ новый type
не dir и уже принят его строгий потомок. В остальных случаях прими запись.
Неявный родитель не вызывает collision и не является записью.
Ошибочные записи не занимают имя. При нескольких причинах приоритет именно
unsafe > collision > ancestry. errors=[{index:номер с1,error}], порядок входа.

После прохода разреши ссылки по окончательной карте ПОЛНЫХ canonical path.
Это lookup целого target, НЕ OS traversal через префиксы symlink.
file/dir — терминалы; sym/hard рекурсивно следуют target. Повтор пути в
цепочке ->cycle, отсутствующий путь (включая неявную dir) ->missing.
Каждый hard на цепочке обязан закончиться file, иначе not_file; исходная
ошибка deeper-link имеет приоритет над not_file. Неуспешная ссылка остаётся
в карте, её потомки не освобождаются для повторной обработки.

Выведи ровно {entries,errors}; entries по path. file={path,type,size};
dir={path,type}; link={path,type,target,resolved,error}, где resolved —
полный canonical file/dir path или null, error null либо cycle/missing/not_file.
"""


_program(
    488, "archive_namespace_audit", "Audit portable archive names and deferred links", 360,
    _ARCHIVE_PROMPT, solve488,
    [
        ({"entries": []}, {"entries": [], "errors": []}),
        ({"entries": [{"path": "A.txt", "type": "file", "size": 3},
                     {"path": "LINK", "type": "hard", "target": "a.txt"}]},
         {"entries": [{"path": "a.txt", "type": "file", "size": 3},
                      {"path": "link", "type": "hard", "target": "a.txt",
                       "resolved": "a.txt", "error": None}], "errors": []}),
    ],
    [
        {"entries": [{"path": x, "type": "file", "size": 1} for x in
                     ["%252e%252e/x", "A\\b", "a/b", "nul.txt", "c:x", "x//y", "%ff"]]},
        {"entries": [{"path": "e\u0301", "type": "dir"}, {"path": "É", "type": "file", "size": 1}]},
        {"entries": [{"path": "a/b", "type": "file", "size": 0},
                     {"path": "a", "type": "file", "size": 2}, {"path": "a", "type": "dir"}]},
        {"entries": [{"path": "a", "type": "sym", "target": "b"},
                     {"path": "a/b", "type": "file", "size": 0}, {"path": "b", "type": "dir"}]},
        {"entries": [{"path": "a", "type": "sym", "target": "b"},
                     {"path": "b", "type": "hard", "target": "a"}]},
        {"entries": [{"path": "a", "type": "hard", "target": "b"},
                     {"path": "b", "type": "dir"}]},
        {"entries": [{"path": "a/x", "type": "sym", "target": "../a/.."},
                     {"path": "a/y", "type": "sym", "target": "../../escape"}]},
        {"entries": [{"path": "a/x", "type": "hard", "target": "../z"},
                     {"path": "z", "type": "sym", "target": "target"},
                     {"path": "target", "type": "file", "size": 99}]},
        {"entries": [{"path": "a/x", "type": "file", "size": 1},
                     {"path": "link", "type": "sym", "target": "a"}]},
        {"entries": [{"path": x, "type": "file", "size": i} for i, x in enumerate(
            ["x%2Fy", "X/y", "CON.a", "lpt0", "x.", "foo ", "bad%GG", "good%20name", "a/%00b"])]},
        {"entries": [{"path": "a", "type": "file", "size": 1},
                     {"path": "A", "type": "sym", "target": "../../x"}]},
        {"entries": [{"path": f"n{i}", "type": "sym", "target": f"n{i+1}"} for i in range(30)]
                    + [{"path": "n30", "type": "file", "size": 8}]},
    ],
)


def _apply489(tree, action, protected, sizes):
    """Prepare one transaction against a round-start snapshot, without committing."""
    trial = dict(tree)
    reads, writes, staging = set(action["guards"]), set(), 0
    if any(tree.get(path) != value for path, value in action["guards"].items()):
        return None
    for op in action["ops"]:
        kind, path = op["op"], op["path"]
        reads.add(path)
        if kind == "put":
            if path in trial:
                return None
            trial[path] = op["blob"]
            staging += sizes[op["blob"]]
            changed = {path}
        elif kind == "delete":
            if path not in trial:
                return None
            del trial[path]
            changed = {path}
        else:
            target = op["to"]
            reads.add(target)
            if path not in trial or target in trial:
                return None
            trial[target] = trial[path]
            if kind == "move":
                del trial[path]
            else:
                staging += sizes[trial[path]]
            changed = {path, target} if kind == "move" else {target}
        if any(p == q or p.startswith(q + "/") for p in changed for q in protected):
            return None
        if any(q.startswith(p + "/") for p in trial for q in trial):
            return None
        writes.update(changed)
    patch = {path: trial.get(path) for path in writes}
    return patch, reads, writes, staging


def _search489(data, limits):
    import itertools

    actions = sorted(data["actions"], key=lambda a: a["id"])
    sizes = data["sizes"]
    target = tuple(sorted(data["target"].items()))
    initial = tuple(sorted(data["initial"].items()))
    initial_size = sum(sizes[b] for _, b in initial)
    # Label = (transfer, peak, plan). Keep a Pareto frontier: a smaller past
    # peak alone does NOT dominate a lexicographically earlier plan, because
    # a later staging peak may erase the distinction.
    layer = {(initial, 0): [(0, initial_size, ())]}
    for rounds in range(limits["max_rounds"] + 1):
        goals = [label for (state, _), labels in layer.items() if state == target for label in labels]
        if goals:
            transfer, peak, plan = min(goals)
            return {"rounds": rounds, "transfer": transfer, "peak": peak,
                    "plan": [list(group) for group in plan]}
        nxt = {}
        for (state, mask), labels in layer.items():
            tree = dict(state)
            used = sum(sizes[b] for b in tree.values())
            applicable = []
            for i, action in enumerate(actions):
                if mask >> i & 1:
                    continue
                prepared = _apply489(tree, action, data["protected"], sizes)
                if prepared is not None:
                    applicable.append((i, action, prepared))
            for count in range(1, min(len(applicable), limits["workers"]) + 1):
                for group in itertools.combinations(applicable, count):
                    workers = sum(a["workers"] for _, a, _ in group)
                    transfer = sum(a["transfer"] for _, a, _ in group)
                    staging = sum(prepared[3] for _, _, prepared in group)
                    if workers > limits["workers"] or transfer > limits["network"] or used + staging > limits["disk"]:
                        continue
                    if any(x[2][2] & (y[2][1] | y[2][2]) or y[2][2] & x[2][1]
                           for x, y in itertools.combinations(group, 2)):
                        continue
                    merged = dict(tree)
                    for _, _, (patch, _, _, _) in group:
                        for path, blob in patch.items():
                            if blob is None:
                                merged.pop(path, None)
                            else:
                                merged[path] = blob
                    if any(q.startswith(p + "/") for p in merged for q in merged):
                        continue
                    end_size = sum(sizes[b] for b in merged.values())
                    # Moves are metadata-only, copies/puts reserve all newly
                    # allocated bytes until commit. The final map is checked too.
                    round_peak = max(used + staging, end_size)
                    if round_peak > limits["disk"]:
                        continue
                    key = (tuple(sorted(merged.items())), mask | sum(1 << i for i, _, _ in group))
                    group_ids = tuple(a["id"] for _, a, _ in group)
                    bucket = nxt.setdefault(key, [])
                    for cost, peak, plan in labels:
                        new = (cost + transfer, max(peak, round_peak), plan + (group_ids,))
                        # Additive transfer is primary among labels at this key.
                        if bucket and new[0] > bucket[0][0]:
                            continue
                        if bucket and new[0] < bucket[0][0]:
                            bucket.clear()
                        if any(old[1] <= new[1] and old[2] <= new[2] for old in bucket):
                            continue
                        bucket[:] = [old for old in bucket if not (new[1] <= old[1] and new[2] <= old[2])]
                        bucket.append(new)
        layer = nxt
    raise ValueError("fixture has no feasible migration within max_rounds")


def solve489(data):
    return {"scenarios": [{"id": limits["id"], **_search489(data, limits)} for limits in data["scenarios"]]}


def _action489(name, guards, ops, workers=1, transfer=0):
    return {"id": name, "guards": guards, "ops": ops, "workers": workers, "transfer": transfer}


def _put489(path, blob):
    return {"op": "put", "path": path, "blob": blob}


def _delete489(path):
    return {"op": "delete", "path": path}


def _move489(path, target):
    return {"op": "move", "path": path, "to": target}


_FS_INITIAL = {"live/app": "app1", "live/index": "index1", "live/state": "v1",
               "live/docs": "docs1", "archive/old": "archive", "vault/key": "sealed"}
_FS_TARGET = {"live/app": "app2", "live/index": "index2", "live/state": "v2",
              "live/docs": "docs2", "vault/key": "sealed"}
_FS_SIZES = {"app1": 6, "app2": 8, "index1": 4, "index2": 5, "docs1": 3, "docs2": 4,
             "v1": 1, "maintenance": 1, "v2": 1, "archive": 7, "sealed": 1,
             "chunkA": 4, "chunkB": 4, "ticket": 1}
_FS_ACTIONS = [
    _action489("01-maintenance", {"live/state": "v1"},
               [_delete489("live/state"), _put489("live/state", "maintenance")], transfer=1),
    _action489("02-purge", {"archive/old": "archive"}, [_delete489("archive/old")]),
    _action489("03-direct-app", {"live/state": "maintenance", "live/app": "app1"},
               [_delete489("live/app"), _put489("live/app", "app2")], workers=2, transfer=8),
    _action489("04-direct-index", {"live/state": "maintenance", "live/index": "index1"},
               [_delete489("live/index"), _put489("live/index", "index2")], transfer=5),
    _action489("05-direct-docs", {"live/state": "maintenance", "live/docs": "docs1"},
               [_delete489("live/docs"), _put489("live/docs", "docs2")], transfer=4),
    _action489("06-upload-app", {"stage/app": None}, [_put489("stage/app", "app2")], workers=2, transfer=8),
    _action489("07-upload-index", {"stage/index": None}, [_put489("stage/index", "index2")], transfer=5),
    _action489("08-upload-docs", {"stage/docs": None}, [_put489("stage/docs", "docs2")], transfer=4),
    _action489("09-chunk-a", {"chunks/a": None}, [_put489("chunks/a", "chunkA")], transfer=4),
    _action489("10-chunk-b", {"chunks/b": None}, [_put489("chunks/b", "chunkB")], transfer=4),
    _action489("11-assemble", {"chunks/a": "chunkA", "chunks/b": "chunkB", "stage/app": None},
               [_delete489("chunks/a"), _delete489("chunks/b"), _put489("stage/app", "app2")]),
    _action489("12-switch-app", {"live/state": "maintenance", "live/app": "app1", "stage/app": "app2"},
               [_delete489("live/app"), _move489("stage/app", "live/app")]),
    _action489("13-switch-index", {"live/state": "maintenance", "live/index": "index1", "stage/index": "index2"},
               [_delete489("live/index"), _move489("stage/index", "live/index")]),
    _action489("14-switch-docs", {"live/state": "maintenance", "live/docs": "docs1", "stage/docs": "docs2"},
               [_delete489("live/docs"), _move489("stage/docs", "live/docs")]),
    _action489("15-finish", {"live/state": "maintenance", "live/app": "app2", "live/index": "index2", "live/docs": "docs2"},
               [_delete489("live/state"), _put489("live/state", "v2")], transfer=1),
    _action489("16-direct-bundle", {"live/state": "v1", "live/app": "app1", "live/index": "index1", "live/docs": "docs1"},
               [_delete489("live/app"), _put489("live/app", "app2"),
                _delete489("live/index"), _put489("live/index", "index2"),
                _delete489("live/docs"), _put489("live/docs", "docs2"),
                _delete489("live/state"), _put489("live/state", "v2")], workers=3, transfer=18),
    _action489("17-unsafe-space", {"vault/key": "sealed"}, [_delete489("vault/key")]),
    _action489("18-unsafe-backup", {"live/app": "app1", "vault/copy": None},
               [{"op": "copy", "path": "live/app", "to": "vault/copy"}], transfer=0),
]
_FS_DATA = {"initial": _FS_INITIAL, "target": _FS_TARGET, "sizes": _FS_SIZES,
            "protected": ["vault"], "actions": _FS_ACTIONS,
            "scenarios": [
                {"id": "standard", "workers": 3, "network": 10, "disk": 34, "max_rounds": 8},
                {"id": "tight-disk", "workers": 3, "network": 10, "disk": 24, "max_rounds": 8},
                {"id": "narrow-link", "workers": 3, "network": 6, "disk": 34, "max_rounds": 8},
                {"id": "wide-link", "workers": 3, "network": 20, "disk": 34, "max_rounds": 8},
            ]}
SOLVERS[489] = solve489
ANCHORS[489] = [
    ({"initial": {"a": "old"}, "target": {"a": "new"}, "sizes": {"old": 4, "new": 6},
      "protected": [], "actions": [_action489("replace", {"a": "old"},
          [_delete489("a"), _put489("a", "new")], transfer=6)],
      "scenarios": [{"id": "one", "workers": 1, "network": 6, "disk": 10, "max_rounds": 1}]},
     {"scenarios": [{"id": "one", "rounds": 1, "transfer": 6, "peak": 10, "plan": [["replace"]]}]}),
    ({"initial": {"old": "blob"}, "target": {"new": "blob"}, "sizes": {"blob": 3},
      "protected": [], "actions": [_action489("move", {"old": "blob", "new": None},
          [_move489("old", "new")])],
      "scenarios": [{"id": "metadata", "workers": 1, "network": 0, "disk": 3, "max_rounds": 1}]},
     {"scenarios": [{"id": "metadata", "rounds": 1, "transfer": 0, "peak": 3, "plan": [["move"]]}]}),
]
TASKS.append(artifact_task(
    number=489, slug="atomic_tree_migration", name="Optimize a resource-constrained transactional migration",
    parent=360,
    prompt="""
Спланируй всю миграцию проекта из snapshot.json в ТОЧНЫЙ target.json.
В blobs.json размеры blobs, policy.json protected prefixes, manifest.json
перечисляет action/*.json и scenarios.json задаёт четыре независимых режима.
Это локальная плановая модель файловой системы; исходные файлы НЕ меняй.
Нужен глобально оптимальный план для КАЖДОГО режима, не просто допустимый.
Все четыре режима разрешимы в max_rounds. Размеры — целые условные байты.

Состояние — карта FILE path->blob; каталоги неявны, ссылки отсутствуют.
Пути уже безопасные POSIX relative names, case-sensitive. Blob immutable.
Физические копии считаются отдельно, даже если содержат одинаковый blob.
Один round длится1. В нём выбирается непустое подмножество ещё не выбранных
транзакций; каждый action можно использовать максимум один раз, ненужные
допустимо пропустить. Имена внутри round отсортируй по id.

Каждый action ПРОВЕРЯЕТСЯ и исполняется на СОБСТВЕННОЙ копии одного общего
состояния в начале round. guards — path->ожидаемый blob, null значит отсутствие;
все guards должны совпасть. Затем ops по порядку:
put {path,blob}: path отсутствует, создать файл;
delete {path}: точный файл существует, удалить;
move/copy {path,to}: точный source существует, target отсутствует;
перенести/скопировать содержимое. move — только metadata, copy аллоцирует
новую физическую копию. Directory move не поддержан.
После КАЖДОГО op нельзя иметь файл, который строгий предок другого файла.
Любая запись/удаление protected path или его потомка запрещена; read и copy
ИЗ protected разрешены. Нельзя освободить защищённый файл временно.

Read-set action: все guard paths, ВСЕ op.path и ВСЕ op.to (даже если путь
проверяет отсутствие или был создан ранее в том же action). Write-set:
put/delete ->path; move ->path иto; copy ->толькоto. Считай и промежуточные
writes, которые позже отменены. Два action совместимы, только если
W1∩(R2∪W2)=W2∩(R1∪W1)=пусто. Shared reads разрешены.
Нельзя использовать результат другого action из этого же round.
Коммит совместимого round применяет итоговые patches атомарно, после чего
проверь отсутствие file/ancestor collisions также в объединённом дереве.

Ресурсы round: сумма workers<=workers; сумма transfer<=network.
transfer — явно заданная стоимость action, НЕ выводи её из ops: upload
использует сеть, assemble — локальная обработка, move — metadata.
COW storage: исходные файлы занимают свои bytes ДО коммита, даже если
удаляются первым op. Каждый put/copy аллоцирует size нового blob; суммируй
ВСЕ эти allocations, даже если файл затем удалён или перемещён внутри action.
Никакая allocation не освобождается до конца round. move не аллоцирует.
Peak round=max(start_bytes+sum(allocations всех action),end_bytes).
Peak должен быть<=disk. После commit освобождаются отсутствующие в итоговом
дереве файлы и промежуточные allocations; next start_bytes — сумма размеров
файлов в итоговом дереве. Нельзя кредитовать удаление из соседнего action
для staging в том же round. Начальное использование тоже входит в peak.

Остановись на точном target, без лишних stage/chunks/archive файлов.
Минимизируй кортеж (число rounds, total_transfer, peak_bytes_за_весь_план,
plan). plan — список round-списков отсортированных id; сравнение обычное
лексикографическое строк/списков, включая правило shorter-prefix-first.
Третий критерий — MAX, не сумма: не выбрасывай lexicographically лучший
prefix лишь за больший прежний peak, если будущий peak может их уравнять.

answer.json={scenarios:[{id,rounds,transfer,peak,plan}]} в порядке scenarios.
Каждый результат должен содержать конкретный исполнимый оптимальный план.
Разрешены только перечисленные actions. Побочных файлов в ответе нет.
""",
    files={"snapshot.json": dumps(_FS_INITIAL), "target.json": dumps(_FS_TARGET),
           "blobs.json": dumps(_FS_SIZES), "policy.json": dumps({"protected": _FS_DATA["protected"]}),
           "scenarios.json": dumps(_FS_DATA["scenarios"]),
           "manifest.json": dumps([f"action/{a['id']}.json" for a in _FS_ACTIONS]),
           **{f"action/{a['id']}.json": dumps(a) for a in _FS_ACTIONS}},
    expected=solve489(_FS_DATA), tags=("systems", "filesystem", "transactions", "optimization"),
))


def solve490(data):
    import json

    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError
            out[k] = v
        return out

    seen, events, errors = {}, {}, []
    sources = sorted(data["sources"])
    for n, raw in enumerate(data["lines"], 1):
        try:
            e = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, TypeError):
            errors.append({"line": n, "error": "invalid_json"})
            continue
        if not isinstance(e, dict):
            errors.append({"line": n, "error": "not_object"})
            continue
        required = {"eid", "id", "clock", "source", "op"} | ({"value"} if e.get("op") == "put" else set())
        valid = (
            set(e) == required and e.get("op") in ("put", "delete")
            and type(e.get("id")) is int and e["id"] > 0
            and isinstance(e.get("eid"), str) and bool(e["eid"])
            and isinstance(e.get("source"), str) and e["source"] in sources
            and isinstance(e.get("clock"), dict)
            and all(k in sources and type(v) is int and v >= 0 for k, v in e["clock"].items())
        )
        if not valid:
            errors.append({"line": n, "error": "schema"})
            continue
        canonical = json.dumps(e, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if e["eid"] in seen:
            if seen[e["eid"]] != canonical:
                errors.append({"line": n, "error": "event_collision"})
            continue
        seen[e["eid"]] = canonical
        events.setdefault(e["id"], []).append(e)
    documents = []
    for key, values in sorted(events.items()):
        def dominates(a, b):
            va = [a["clock"].get(s, 0) for s in sources]
            vb = [b["clock"].get(s, 0) for s in sources]
            return all(x >= y for x, y in zip(va, vb, strict=True)) and any(
                x > y for x, y in zip(va, vb, strict=True))

        front = [a for a in values if not any(dominates(b, a) for b in values)]
        winner = max(front, key=lambda e: (e["op"] == "delete", data["sources"][e["source"]], e["eid"]))
        documents.append({
            "id": key, "frontier": sorted(e["eid"] for e in front), "conflict": len(front) > 1,
            "winner": winner["eid"], "deleted": winner["op"] == "delete",
            "value": winner.get("value"),
        })
    return {"documents": documents, "errors": errors}


_CDC_PROMPT = """
Сверни CDC JSONL с векторными часами. Вход {sources,lines}, sources —
объект source->целый приоритет (больше сильнее), lines — массив сырых JSON-строк.
<=120 строк, <=5 sources. Строгий parser запрещает duplicate keys на ЛЮБОЙ
глубине и NaN/Infinity/-Infinity. Такие строки, как синтаксические ошибки,
дают invalid_json. JSON не-object ->not_object.
Схема event: ровно eid (непустая строка), id (положительный int, не bool),
clock (object, только известные sources, значения int>=0, не bool),
source (известная строка), op ("put" или "delete"), и только для put
обязательный value (произвольный строгий JSON, null допустим). Нарушение ->schema.
Порядок validation: parser, not_object, schema, затем дедупликация.

eid глобален. Первый валидный event резервирует eid. Повтор со структурно
идентичным JSON (порядок ключей не важен, типы важны, 1 и1.0 различны)
игнорируется; несовпадающий ->event_collision, прежний не меняется.
Неправильные строки eid не резервируют. Явный clock[source]=0 отличен
от отсутствующего поля ДЛЯ дедупликации, но отсутствующая координата равна0
ДЛЯ причинного сравнения.

Для каждого id оставь maximal frontier: a доминирует b тогда и только тогда,
когда все координаты a>=b и хотя бы одна >. Равные clock не доминируют.
Из frontier выбери максимум (op=="delete", sources[source], eid).
Удаление побеждает put лишь среди конкурентных/равных clock; более поздний
причинно put оживляет удалённую запись.
Вывод {documents,errors}. documents по id:
{id,frontier:[eid по возрастанию],conflict:len(frontier)>1,winner:eid,
deleted:bool,value:выбранный value либо null для delete}.
errors в порядке строк: {line:номер с1,error}. Записи всех id, включая
удалённые, остаются в documents. Схема внешнего входа гарантирована.
"""


def _event(eid, key=1, clock=None, source="a", op="put", value="v"):
    obj = {"eid": eid, "id": key, "clock": clock or {}, "source": source, "op": op}
    if op == "put":
        obj["value"] = value
    return json.dumps(obj, ensure_ascii=False)


def _cdc(lines):
    return {"sources": {"a": 1, "b": 2, "c": 2}, "lines": lines}


_program(
    490, "causal_jsonl_reducer", "Reduce a causal JSONL change stream", 361,
    _CDC_PROMPT, solve490,
    [
        (_cdc([]), {"documents": [], "errors": []}),
        (_cdc([_event("a1", clock={"a": 1}), _event("b1", clock={"b": 1}, source="b", op="delete")]),
         {"documents": [{"id": 1, "frontier": ["a1", "b1"], "conflict": True,
                         "winner": "b1", "deleted": True, "value": None}], "errors": []}),
    ],
    [
        _cdc(["not-json", "[]", "null", '{"id":true}', '{"eid":"x","eid":"y"}']),
        _cdc([_event("x", value={"nested": [1, True, None]}),
              _event("x", value={"nested": [1, True, None]}), _event("x", value={"nested": [True, 1, None]})]),
        _cdc([_event("d", clock={"a": 2}, op="delete"), _event("p", clock={"a": 3})]),
        _cdc([_event("p", clock={"a": 3}), _event("d", clock={"a": 2}, op="delete")]),
        _cdc([_event("a", clock={"a": 1}), _event("z", clock={"a": 1}, source="c")]),
        _cdc([_event("same"), _event("same", clock={"b": 0})]),
        _cdc([_event("x", key=2), _event("x", key=1), _event("y", key=1)]),
        _cdc(['{"eid":"x","id":1,"clock":{"a":true},"source":"a","op":"delete"}',
              _event("x", op="delete")]),
        _cdc(['{"eid":"x","id":1,"clock":{},"source":"a","op":"put","value":{"a":1,"a":2}}',
              _event("x", value=3)]),
        _cdc([_event("a", value=1), _event("a", value=1.0),
              '{"eid":"nan","id":2,"clock":{},"source":"b","op":"put","value":NaN}']),
        *[_cdc([_event(f"e{i:02}", key=i % 3 + 1, source="abc"[i % 3],
                       clock={s: (i * factor + seed) % 5 for s, factor in zip("abc", (2, 3, 4), strict=True)},
                       op="delete" if (i+seed) % 7 == 0 else "put", value=i)
                for i in range(25)]) for seed in range(3)],
    ],
)


def solve491(data):
    import copy
    import json

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    db, versions, txs, revision, log = copy.deepcopy(data["initial"]), {}, {}, 0, []
    fields = {"begin": {"op", "tx"}, "commit": {"op", "tx"}, "abort": {"op", "tx"},
              "get": {"op", "tx", "key"}, "delete": {"op", "tx", "key"},
              "put": {"op", "tx", "key", "value"}, "save": {"op", "tx", "name"},
              "rollback": {"op", "tx", "name"}, "release": {"op", "tx", "name"}}
    for line, raw in enumerate(data["lines"], 1):
        record = {"line": line}
        log.append(record)
        try:
            op = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, TypeError):
            record["status"] = "invalid_json"
            continue
        if (
            not isinstance(op, dict) or not isinstance(op.get("op"), str)
            or op["op"] not in fields or set(op) != fields[op["op"]]
            or any(not isinstance(op[k], str) or not op[k] for k in set(op) - {"op", "value"})
        ):
            record["status"] = "schema"
            continue
        kind, name = op["op"], op["tx"]
        if kind == "begin":
            if name in txs:
                record["status"] = "active"
            else:
                txs[name] = {"snapshot": copy.deepcopy(db), "versions": dict(versions),
                             "reads": set(), "writes": {}, "saves": []}
                record["status"] = "ok"
            continue
        if name not in txs:
            record["status"] = "inactive"
            continue
        tx = txs[name]
        record["status"] = "ok"
        if kind == "abort":
            del txs[name]
        elif kind == "get":
            key = op["key"]
            tx["reads"].add(key)
            if key in tx["writes"]:
                exists, value = tx["writes"][key]
            else:
                exists, value = key in tx["snapshot"], tx["snapshot"].get(key)
            record.update(exists=exists, value=value if exists else None)
        elif kind in ("put", "delete"):
            tx["writes"][op["key"]] = (kind == "put", op.get("value"))
        elif kind == "commit":
            conflict = sorted(k for k in tx["reads"] | set(tx["writes"])
                              if versions.get(k, 0) != tx["versions"].get(k, 0))
            if conflict:
                record.update(status="conflict", keys=conflict)
            else:
                if tx["writes"]:
                    revision += 1
                for key, (exists, value) in tx["writes"].items():
                    if exists:
                        db[key] = value
                    else:
                        db.pop(key, None)
                    versions[key] = revision
                record["revision"] = revision
            del txs[name]
        else:
            names = [s[0] for s in tx["saves"]]
            label = op["name"]
            if kind == "save":
                if label in names:
                    record["status"] = "save_exists"
                else:
                    tx["saves"].append((label, set(tx["reads"]), copy.deepcopy(tx["writes"])))
            elif label not in names:
                record["status"] = "save_missing"
            else:
                i = names.index(label)
                if kind == "rollback":
                    _, reads, writes = tx["saves"][i]
                    tx["reads"], tx["writes"] = set(reads), copy.deepcopy(writes)
                    tx["saves"] = tx["saves"][:i+1]
                else:
                    tx["saves"] = tx["saves"][:i]
    return {"data": db, "revision": revision, "active": sorted(txs), "log": log}


_WAL_PROMPT = """
Восстанови локальный JSONL transaction journal. Вход {initial,lines};
initial — object key->любое strict JSON, lines — массив raw JSON строк.
Строгий parser запрещает duplicate keys любой глубины и nonfinite numbers
->invalid_json; всё остальное не по схеме ->schema. Операции, с ТОЧНЫМ
набором полей: begin/commit/abort {op,tx}; get/delete {op,tx,key};
put {op,tx,key,value}; save/rollback/release {op,tx,name}.
tx,key,name — непустые строки; value — любой JSON, включая null.
После schema: begin активного tx ->active; любая иная операция
неактивного tx ->inactive. Ошибка не меняет состояние.

Глобально revision=0, все key version=0, включая отсутствующие.
begin сохраняет snapshot значений И версий. get читает свои pending writes
при наличии, иначе snapshot; добавляет key в readset даже при отсутствии.
put/delete только записывают последнее намерение в writes, без readset.
delete отсутствующего допустим. get после delete даёт exists=false,value=null;
put null даёт exists=true,value=null.
commit проверяет readset UNION keys(writes): версия каждого key сейчас
должна совпадать с snapshot version. Если нет ->conflict с keys по возрастанию.
Успешный commit с непустым writes увеличивает revision ровно на1, применяет
все writes атомарно и ставит их key version в новый revision, даже для
удаления отсутствующего или записи прежнего значения. Версии tombstones
сохраняются навсегда. Read-only commit revision не меняет. После ЛЮБОГО
commit (включая conflict) tx закрыт; abort тоже закрывает.

save сохраняет текущие writes и readset; дубликат имени в данном tx
->save_exists. rollback к отсутствующему имени ->save_missing; иначе
восстанавливает writes И readset, удаляет более новые savepoints,
сам названный сохраняет. release удаляет названный и все более новые,
не меняя reads/writes; отсутствующий ->save_missing.
Повторное begin закрытого tx создаёт новый snapshot.
Вывод {data,revision,active:[имена активных tx по возрастанию],log}.
log по входу: {line:номер с1,status}; status обычно ok или описанная ошибка.
У успешного get доп.поля exists,value; успешного commit — revision;
conflict — keys. Иных полей нет. <=200 строк и <=20 разных ключей.
"""


def _wal(ops, initial=None):
    return {"initial": initial or {}, "lines": [
        x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in ops]}


def _op(kind, tx="a", **kw):
    return {"op": kind, "tx": tx, **kw}


_program(
    491, "transaction_jsonl_replay", "Replay snapshot transactions with savepoints", 361,
    _WAL_PROMPT, solve491,
    [
        (_wal([]), {"data": {}, "revision": 0, "active": [], "log": []}),
        (_wal([_op("begin"), _op("put", key="x", value=None), _op("get", key="x"), _op("commit")]),
         {"data": {"x": None}, "revision": 1, "active": [], "log": [
             {"line": 1, "status": "ok"}, {"line": 2, "status": "ok"},
             {"line": 3, "status": "ok", "exists": True, "value": None},
             {"line": 4, "status": "ok", "revision": 1}]}),
    ],
    [
        _wal([_op("begin"), _op("begin", "b"), _op("put", key="x", value=1),
              _op("put", "b", key="x", value=2), _op("commit"), _op("commit", "b")]),
        _wal([_op("begin"), _op("get", key="x"), _op("begin", "b"),
              _op("delete", "b", key="x"), _op("commit", "b"), _op("commit")]),
        _wal([_op("begin"), _op("save", name="s"), _op("get", key="x"),
              _op("begin", "b"), _op("put", "b", key="x", value=1), _op("commit", "b"),
              _op("rollback", name="s"), _op("commit")]),
        _wal([_op("begin"), _op("put", key="x", value=1), _op("save", name="s"),
              _op("delete", key="x"), _op("get", key="x"), _op("rollback", name="s"),
              _op("get", key="x"), _op("commit")]),
        _wal([_op("begin"), _op("save", name="s"), _op("save", name="s"),
              _op("save", name="t"), _op("release", name="s"), _op("rollback", name="t")]),
        _wal([_op("begin"), _op("abort"), _op("commit"), _op("begin"), _op("commit")]),
        _wal(["{", "null", '{"op":"begin","tx":"a","tx":"b"}',
              '{"op":"put","tx":"a","key":"x","value":NaN}', _op("get", key="x"),
              _op("begin"), _op("begin"), {"op": "begin", "tx": "b", "extra": 1}]),
        _wal([_op("begin"), _op("get", key="x"), _op("begin", "b"),
              _op("put", "b", key="x", value=3), _op("commit", "b"), _op("commit")], {"x": 3}),
        _wal([_op("begin"), _op("put", key="x", value=1), _op("save", name="s"),
              _op("put", key="y", value=2), _op("save", name="t"),
              _op("put", key="x", value=3), _op("rollback", name="s"),
              _op("rollback", name="s"), _op("commit")]),
        _wal([_op("begin"), _op("get", key="a"), _op("get", key="b"),
              _op("begin", "b"), _op("put", "b", key="b", value=1),
              _op("put", "b", key="a", value=2), _op("commit", "b"), _op("commit")]),
        _wal([_op("begin"), _op("get", key="x"), _op("get", key="y"),
              _op("put", key="obj", value={"list": [False, None, {"x": "y"}]}),
              _op("save", name="s"), _op("abort")], {"x": None}),
    ],
)


def solve492(data):
    import heapq

    nodes = sorted(data["graph"])
    required = {(n, d) for n in nodes for d in data["graph"][n]}
    missing = sorted({d for _, d in required if d not in data["graph"]})
    if missing:
        return {"error": "unknown", "nodes": missing}
    edges = sorted((e["node"], e["dep"], e["cost"]) for e in data["optional"]
                   if e["node"] in data["graph"] and e["dep"] in data["graph"])

    def topo(pairs):
        remaining = {n: {d for a, d in pairs if a == n} for n in nodes}
        ready = [(data["priority"].get(n, 0), n) for n in nodes if not remaining[n]]
        heapq.heapify(ready)
        order = []
        while ready:
            _, n = heapq.heappop(ready)
            order.append(n)
            for target in nodes:
                if n in remaining[target]:
                    remaining[target].remove(n)
                    if not remaining[target]:
                        heapq.heappush(ready, (data["priority"].get(target, 0), target))
        return order if len(order) == len(nodes) else None

    if topo(required) is None:
        cycles = []

        def dfs(start, path):
            for nxt in sorted(d for a, d in required if a == path[-1]):
                if nxt == start:
                    cycles.append(path + [start])
                elif nxt not in path and nxt >= start:
                    dfs(start, path + [nxt])

        for n in nodes:
            dfs(n, [n])
        return {"error": "required_cycle", "cycle": min(cycles, key=lambda c: (len(c), c))}
    best = None
    for mask in range(1 << len(edges)):
        drop = [(a, b) for i, (a, b, _) in enumerate(edges) if mask >> i & 1]
        score = (sum(c for i, (_, _, c) in enumerate(edges) if mask >> i & 1), len(drop), drop)
        if best is not None and score >= best[0]:
            continue
        order = topo(required | {(a, b) for i, (a, b, _) in enumerate(edges) if not mask >> i & 1})
        if order is not None:
            best = (score, order)
    return {"error": None, "drop": [list(e) for e in best[0][2]],
            "cost": best[0][0], "order": best[1]}


_DEP_PROMPT = """
Исправь dependency graph с платными мягкими ограничениями. Вход:
{graph,optional,priority}. graph=node->[required deps]; повтор required
игнорируется. optional=[{node,dep,cost}], cost>=0; одинаковых optional пар
и совпадений с required нет. priority отсутствующего node равен0.
<=8 узлов, <=14 optional; optional с неизвестным node ИЛИ dep игнорируются.
Сначала unknown required: {"error":"unknown","nodes":[все неизвестные по алфавиту]}.
Затем если required имеют цикл: {"error":"required_cycle","cycle":[n0,...,n0]}.
cycle идёт В НАПРАВЛЕНИИ node->dependency, простой, кроме повторённого конца;
выбери кратчайший по числу рёбер, затем лексикографически минимальный массив
среди всех вращений всех кратчайших циклов. Self-loop — [a,a].

Иначе можно удалить только optional рёбра. Найди ациклический остаток,
минимизируя кортеж: (сумма cost удалённых, число удалённых,
лексикографический список удалённых пар [node,dep], сам список отсортирован).
Нулевые cost не разрешают лишних удалений из-за второго критерия.
Для выбранного остатка топологический order выдаётся алгоритмом Kahn:
dependencies раньше dependent, среди готовых минимальный (priority,node).
Вывод {"error":null,"drop":[пары],"cost":сумма,"order":[узлы]}.
Пустой граф допустим. Имена ASCII; внешний ввод структурно валиден.
"""


def _dep(graph, optional=(), priority=None):
    return {"graph": graph, "optional": [{"node": a, "dep": b, "cost": c} for a, b, c in optional],
            "priority": priority or {}}


_program(
    492, "weighted_dependency_repair", "Repair weighted optional dependency cycles", 362,
    _DEP_PROMPT, solve492,
    [
        (_dep({"a": [], "b": ["a"]}), {"error": None, "drop": [], "cost": 0, "order": ["a", "b"]}),
        (_dep({"a": [], "b": []}, [("a", "b", 1), ("b", "a", 1)]),
         {"error": None, "drop": [["a", "b"]], "cost": 1, "order": ["a", "b"]}),
    ],
    [
        _dep({}), _dep({"a": ["z", "x", "z"], "b": ["b"]}),
        _dep({"a": ["b"], "b": ["a"], "c": ["c"]}),
        _dep({"b": ["c"], "a": ["c"], "c": ["a", "b"]}),
        _dep({"a": [], "b": [], "c": []}, [("a", "b", 0), ("b", "a", 0), ("b", "c", 0)]),
        _dep({"a": [], "b": ["a"], "c": ["b"]},
             [("a", "c", 10), ("a", "b", 4), ("missing", "a", 9)]),
        _dep({"a": [], "b": [], "c": []},
             [("a", "b", 2), ("b", "c", 2), ("c", "a", 3), ("b", "a", 1)]),
        _dep({"a": [], "b": [], "c": ["a", "a"]}, priority={"a": 9, "b": -2, "c": -9}),
        *[_dep({c: [] for c in "abcde"},
               [(a, b, (i+seed) % 4) for i, (a, b) in enumerate(
                   [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "e"),
                    ("e", "b"), ("a", "e"), ("d", "a"), ("b", "d")])],
               {"a": seed, "e": -seed}) for seed in range(4)],
    ],
)


def solve493(data):
    jobs = {j["id"]: j for j in data["jobs"]}
    ids = sorted(jobs)
    order = []
    while len(order) < len(ids):
        ready = [n for n in ids if n not in order and all(d["id"] in order for d in jobs[n]["deps"])]
        order.append(ready[0])
    horizon = len(data["capacity"])
    used = [0] * horizon
    locks = [set() for _ in range(horizon)]
    placed = {}
    best = None

    def search(index, cost):
        nonlocal best
        if index == len(order):
            makespan = max((e for _, e in placed.values()), default=0)
            score = (makespan, cost, tuple(placed[n][0] for n in ids))
            if best is None or score < best[0]:
                best = (score, dict(placed))
            return
        name = order[index]
        j = jobs[name]
        start_min = max([j["release"]] + [placed[d["id"]][1] + d["lag"] for d in j["deps"]])
        fixed = {placed[d["id"]][1] + d["lag"] for d in j["deps"] if d["exact"]}
        if len(fixed) > 1:
            return
        starts = sorted(fixed) if fixed else range(start_min, j["deadline"] - j["duration"] + 1)
        for start in starts:
            end = start + j["duration"]
            if start < start_min or end > min(horizon, j["deadline"]):
                continue
            if best is not None and end > best[0][0]:
                continue
            if any(used[t] + j["demand"] > data["capacity"][t]
                   or set(j["locks"]) & locks[t] for t in range(start, end)):
                continue
            for t in range(start, end):
                used[t] += j["demand"]
                locks[t].update(j["locks"])
            placed[name] = (start, end)
            search(index + 1, cost + j["demand"] * sum(data["tariff"][start:end]))
            del placed[name]
            for t in range(start, end):
                used[t] -= j["demand"]
                locks[t].difference_update(j["locks"])

    search(0, 0)
    if best is None:
        return {"feasible": False}
    load = [sum(jobs[n]["demand"] for n, (s, e) in best[1].items() if s <= t < e)
            for t in range(horizon)]
    return {"feasible": True, "makespan": best[0][0], "cost": best[0][1], "peak": max(load, default=0),
            "schedule": [{"id": n, "start": best[1][n][0], "end": best[1][n][1]} for n in ids]}


def _sched_job(name, duration, demand, deps=(), **kw):
    return {"id": name, "duration": duration, "demand": demand, "release": 0, "deadline": 18,
            "locks": [], "deps": [{"id": n, "lag": lag, "exact": exact} for n, lag, exact in deps], **kw}


_SCHEDULE_DATA = {
    "capacity": [3, 3, 3, 2, 2, 0, 3, 3, 3, 2, 3, 3, 3, 3, 3, 3, 2, 3],
    "tariff": [4, 4, 2, 1, 1, 0, 1, 3, 4, 4, 1, 1, 3, 2, 1, 1, 1, 1],
    "jobs": [
        _sched_job("build", 2, 2, locks=["disk"], deadline=5),
        _sched_job("schema", 2, 1, locks=["disk"], deadline=5),
        _sched_job("lint", 3, 1, release=1, deadline=9),
        _sched_job("unit", 2, 2, [("build", 0, False)], deadline=11),
        _sched_job("migrate", 2, 1, [("schema", 1, False)], locks=["db"], deadline=12),
        _sched_job("probe", 1, 1, [("migrate", 0, True)], locks=["db"], deadline=13),
        _sched_job("package", 2, 2, [("unit", 0, False), ("lint", 0, False)], locks=["disk"], deadline=16),
        _sched_job("deploy", 2, 2, [("package", 1, False), ("probe", 0, False)], locks=["db"]),
    ],
}
SOLVERS[493] = solve493
ANCHORS[493] = [
    ({"capacity": [1, 1, 1], "tariff": [5, 1, 2],
      "jobs": [_sched_job("a", 1, 1, deadline=3)]},
     {"feasible": True, "makespan": 1, "cost": 5, "peak": 1,
      "schedule": [{"id": "a", "start": 0, "end": 1}]}),
    ({"capacity": [1, 0, 1], "tariff": [1, 1, 1],
      "jobs": [_sched_job("a", 2, 1, deadline=3)]}, {"feasible": False}),
]
TASKS.append(artifact_task(
    number=493, slug="calendar_dependency_schedule", name="Schedule a release under calendars and exact lags",
    parent=362,
    prompt="""
По jobs/*.json, manifest.json и calendar.json найди ГЛОБАЛЬНО оптимальный
непрерываемый план сборки. Время — целые слоты; [start,end), end=start+duration.
release<=start, end<=deadline. deps содержит {id,lag,exact}: start>=end(dep)+lag,
а при exact=true требуется РАВЕНСТВО. Все зависимости обязаны выполняться.
В каждом слоте сумма demand<=capacity[t]. jobs с общим именем в locks
не пересекаются даже при достаточной capacity. Занятость locks только
в интервале выполнения, lag сам ресурсов/locks не занимает. Граф ацикличен.

Минимизируй по очереди: (1) makespan=max(end); (2) cost=
sum по jobs demand*sum(tariff[t] за её слоты); (3) tuple start всех jobs
в лексикографическом порядке id. Критерии строго лексикографические,
меньшая cost НЕ оправдывает больший makespan.
Вывод {feasible:true,makespan,cost,peak,schedule}; peak — максимальная
суммарная demand, schedule=[{id,start,end}] по id. Если плана нет,
ровно {feasible:false}. Все задачи должны присутствовать, пропуски запрещены.
""",
    files={
        "calendar.json": dumps({k: v for k, v in _SCHEDULE_DATA.items() if k != "jobs"}),
        "manifest.json": dumps([f"jobs/{j['id']}.json" for j in _SCHEDULE_DATA["jobs"]]),
        **{f"jobs/{j['id']}.json": dumps(j) for j in _SCHEDULE_DATA["jobs"]},
    },
    expected=solve493(_SCHEDULE_DATA), tags=("systems", "optimization", "scheduling"),
))


def solve494(data):
    import copy
    import json
    import re

    root, owner = {}, {}

    def pointer(parts):
        return "".join("/" + p.replace("~", "~0").replace("/", "~1") for p in parts)

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if not k.startswith("SECRET_")}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    def merge(old, patch, path, name):
        if not isinstance(patch, dict):
            owner[path] = name
            return copy.deepcopy(patch)
        if not isinstance(old, dict):
            old = {}
        out = copy.deepcopy(old)
        for k, v in patch.items():
            if v is None:
                out.pop(k, None)
            else:
                out[k] = merge(out.get(k), v, path + (k,), name)
        if not out:
            owner[path] = name
        return out

    for layer in data["layers"]:
        root = merge(root, clean(layer["data"]), (), layer["name"])
    if () not in owner:
        owner[()] = data["layers"][-1]["name"]
    active, cache = set(), {}

    def raw_at(parts):
        node = root
        for p in parts:
            if not isinstance(node, dict) or p not in node:
                raise KeyError
            node = node[p]
        return node

    def evaluate(path):
        if path in cache:
            return cache[path]
        active.add(path)
        value = raw_at(path)
        if isinstance(value, dict) and value:
            out, origins = {}, {}
            for key in sorted(value):
                out[key], child = evaluate(path + (key,))
                origins.update({(key,) + p: s for p, s in child.items()})
            result = out, origins
        elif not isinstance(value, str):
            result = copy.deepcopy(value), {(): {owner[path]}}
        else:
            tokens, literal, i = [], "", 0
            while i < len(value):
                if value[i] != "$":
                    literal += value[i]
                    i += 1
                elif value.startswith("$$", i):
                    literal += "$"
                    i += 2
                elif value.startswith("${", i):
                    if literal:
                        tokens.append(("text", literal))
                        literal = ""
                    end = value.find("}", i + 2)
                    if end < 0:
                        raise ValueError("syntax", pointer(path))
                    ref = value[i+2:end]
                    if not ((ref.startswith("/") and not re.search(r"~(?![01])", ref))
                            or (ref.startswith("ENV:") and len(ref) > 4)):
                        raise ValueError("syntax", pointer(path))
                    tokens.append(("ref", ref))
                    i = end + 1
                else:
                    raise ValueError("syntax", pointer(path))
            if literal:
                tokens.append(("text", literal))
            pieces, sources = [], {owner[path]}
            whole = len(tokens) == 1 and tokens[0][0] == "ref"
            for kind, item in tokens:
                if kind == "text":
                    pieces.append(item)
                    continue
                if item.startswith("ENV:"):
                    if item[4:] not in data["env"]:
                        raise ValueError("missing", pointer(path))
                    target, origins = data["env"][item[4:]], {(): {"env:" + item[4:]}}
                else:
                    parts = tuple(p.replace("~1", "/").replace("~0", "~") for p in item[1:].split("/"))
                    try:
                        raw_at(parts)
                    except KeyError:
                        raise ValueError("missing", pointer(path)) from None
                    if parts in active:
                        raise ValueError("cycle", pointer(path))
                    target, origins = evaluate(parts)
                if whole:
                    result = copy.deepcopy(target), {p: s | {owner[path]} for p, s in origins.items()}
                    break
                if isinstance(target, (dict, list)):
                    raise ValueError("non_scalar", pointer(path))
                sources.update(s for group in origins.values() for s in group)
                pieces.append(target if isinstance(target, str)
                              else json.dumps(target, ensure_ascii=False, separators=(",", ":")))
            else:
                result = "".join(pieces), {(): sources}
        active.remove(path)
        cache[path] = result
        return result

    try:
        resolved, origins = evaluate(())
    except ValueError as exc:
        return {"error": exc.args[0], "path": exc.args[1]}
    return {"error": None, "resolved": resolved,
            "provenance": {pointer(p): sorted(s) for p, s in sorted(origins.items())}}


_CONFIG_PROMPT = r"""
Разреши layered config с typed interpolation и транзитивной provenance.
Вход {layers:[{name,data}],env}; 1..8 слоёв, имена уникальны и не начинаются
с "env:". Каждый data — object. env — object строка->строка.
Поздний слой сильнее. Перед merge рекурсивно удали ключи SECRET_* из ВСЕХ
объектов, включая объекты внутри массивов. Не интерпретируй их значения.
Object рекурсивно merge, non-object заменяется целиком; null как значение
ключа patch удаляет этот ключ. Null внутри массива — обычное значение.
Список атомарен. Если object patch накладывается на non-object/отсутствие,
начни с{}. Пустой object patch на существующий непустой object ничего не
удаляет. Перезапись тем же значением всё равно меняет provenance.

У каждого исходного leaf после merge есть writer — последний слой,
записавший leaf. Массивы и пустые objects считаются листьями.
Для пустого object writer — последний слой, merge которого оставил этот
object пустым (в том числе удалением последнего ключа). Корневой {}
имеет writer последнего слоя, оставившего его пустым.

Затем интерполяция только в СТРОКОВЫХ листьях, не внутри атомарных массивов.
"$$" -> буквальный "$"; "${/path}" -> reference; "${ENV:NAME}" -> env string.
Любой иной '$', незакрытый token, пустой ENV name, reference не с'/'
либо некорректный JSON Pointer escape (~ кроме ~0/~1) ->syntax.
Сначала распарси ВСЮ строку на tokens, потом вычисляй references слева направо.
Pointer адресует исходное дерево ПОСЛЕ merge, ДО интерполяции, только object
keys; массивы индексировать нельзя. ~1 означает '/', ~0 означает '~';
"/" адресует пустой ключ, root не адресуется. Нет key/env ->missing.
Ровно один reference без окружающего текста — typed copy, включая object,
list, bool, number, null; скопированные значения уже рекурсивно разрешены.
Иначе reference должен дать scalar: string вставляется буквально, остальные
scalar — результат Python 3.12 json.dumps(value,ensure_ascii=False,
separators=(',',':')) (например 1e-7 превращается в "1e-07"); object/list
->non_scalar. Полученные строки повторно НЕ парсятся.

Вывод успеха: {error:null,resolved,provenance}. provenance — JSON Pointer
конечного leaf -> отсортированный список всех источников, участвовавших
в нём. Literal leaf несёт writer. При typed copy каждый лист копии получает
свои target sources UNION writer строки. Embedded reference даёт union
writer и всех источников referenced scalar. ENV источник "env:NAME".
Массив и пустой object остаются одним leaf (у корня pointer="").

При ошибке вывод ровно {error:"syntax"|"missing"|"cycle"|"non_scalar",path}.
Выбирай ПЕРВУЮ ошибку детерминированного DFS: object keys по Unicode
лексикографическому порядку, references слева направо; referenced node
вычисляется сразу, успешные результаты кешируются. Активные nodes включают
object ancestors. Reference на активный node ->cycle в path строки,
содержащей этот reference. Ошибки missing/non_scalar/syntax также указывают
path строки, которая их непосредственно вызвала. Не продолжай после ошибки.
<=80 исходных leaves, исходная глубина<=12, значения strict JSON. После
полного typed expansion гарантировано <=3000 конечных leaves и <=1 MB
UTF-8 компактной JSON сериализации всего результата, включая provenance.
"""


def _cfg(*layers, env=None):
    return {"layers": [{"name": f"L{i}", "data": d} for i, d in enumerate(layers)],
            "env": env or {}}


_program(
    494, "config_provenance_interpolation", "Resolve typed configuration with transitive provenance",
    363, _CONFIG_PROMPT, solve494,
    [
        (_cfg({"port": 3, "uri": "x:${/port}"}, {"port": 5}),
         {"error": None, "resolved": {"port": 5, "uri": "x:5"},
          "provenance": {"/port": ["L1"], "/uri": ["L0", "L1"]}}),
        (_cfg({"a": "${/b}", "b": "${/a}"}),
         {"error": "cycle", "path": "/b"}),
    ],
    [
        _cfg({"x": {"a": 1}, "y": "${/x}"}, {"x": {"b": 2}}),
        _cfg({"x": {"a": 1}}, {"x": {"a": None}}),
        _cfg({"a": [1, {"SECRET_X": "bad", "x": "${/missing}"}], "b": "${/a}"}),
        _cfg({"a/b": {"~x": False}, "copy": "${/a~1b/~0x}"}),
        _cfg({"a": "$${/missing} $$ cash", "b": "${ENV:N}"}, env={"N": "$raw"}),
        _cfg({"a": {"x": 1}, "b": "before${/a}after"}),
        _cfg({"a": "${/missing}", "b": "$broken"}),
        _cfg({"a": "${/missing}$broken"}),
        _cfg({"a": "${/a~2b}"}),
        _cfg({"a": {"x": "${/a}"}}),
        _cfg({"x": 1, "SECRET_ROOT": "bad"}, {"x": {}}, {"x": {}}),
        _cfg({"": None, "a": "${/}"}, {"": "ok"}),
        _cfg({"a": "${ENV:NOPE}"}),
        _cfg({"a": "${/b}", "b": "${/c}", "c": 3}, {"c": 4}),
        _cfg({"a": "${/b}", "b": {"x": 1}}, {"b": 2}),
        _cfg({"a": 1}, {"a": None}),
        _cfg({"a": [None, True], "b": "${/a/0}"}),
        _cfg({"z": "${/x}", "x": "${ENV:N}"}, env={"N": ""}),
        _cfg({"x": 1e-7, "y": "${/x}ms"}),
    ],
)


def solve495(data):
    import copy
    import json

    profiles = data["profiles"]
    results = []
    for query in data["queries"]:
        cache, stack = {}, []

        def mro(name, stack=stack, cache=cache):
            if name in stack:
                raise ValueError({"status": "cycle", "cycle": stack[stack.index(name):] + [name]})
            if name not in profiles:
                raise ValueError({"status": "unknown", "at": name})
            if name in cache:
                return cache[name]
            stack.append(name)
            parents = profiles[name]["parents"]
            seqs = [list(mro(p)) for p in parents] + [list(parents)]
            result = [name]
            while any(seqs):
                seqs = [s for s in seqs if s]
                candidate = next((s[0] for s in seqs if all(s[0] not in t[1:] for t in seqs)), None)
                if candidate is None:
                    raise ValueError({"status": "inconsistent", "at": name,
                                      "heads": [s[0] for s in seqs]})
                result.append(candidate)
                for s in seqs:
                    if s[0] == candidate:
                        s.pop(0)
            stack.pop()
            cache[name] = result
            return result

        try:
            lineage = mro(query["profile"])
            config, writers = {}, {}
            for profile in reversed(lineage):
                for index, op in enumerate(profiles[profile]["ops"], 1):
                    key, kind = op["key"], op["op"]
                    if kind == "set":
                        config[key], writers[key] = copy.deepcopy(op["value"]), profile
                    elif kind == "delete":
                        config.pop(key, None)
                        writers.pop(key, None)
                    elif key in config and not isinstance(config[key], list):
                        raise ValueError({"status": "type", "at": profile, "operation": index})
                    else:
                        exists = key in config
                        old = config.get(key, [])
                        def encode(v):
                            return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                        wanted = {encode(v) for v in op["values"]}
                        if kind == "append":
                            new = copy.deepcopy(old)
                            present = {encode(v) for v in new}
                            for v in op["values"]:
                                if encode(v) not in present:
                                    new.append(copy.deepcopy(v))
                                    present.add(encode(v))
                            changed = not exists or encode(new) != encode(old)
                        else:
                            new = [v for v in old if encode(v) not in wanted]
                            changed = exists and encode(new) != encode(old)
                        if changed:
                            config[key], writers[key] = new, profile
            results.append({"id": query["id"], "status": "ok", "mro": lineage,
                            "config": config, "writers": writers})
        except ValueError as exc:
            results.append({"id": query["id"], **exc.args[0]})
    return {"results": results}


def _profile(parents=(), *ops):
    return {"parents": list(parents), "ops": list(ops)}


def _set(key, value):
    return {"op": "set", "key": key, "value": value}


_PROFILES = {
    "base": _profile((), _set("features", ["read", "audit", "read"]), _set("pool", 2),
                     _set("nullable", None), _set("obsolete", "v1")),
    "secure": _profile(("base",), {"op": "append", "key": "features", "values": ["audit", "tls"]},
                       _set("pool", 5)),
    "fast": _profile(("base",), {"op": "remove", "key": "features", "values": ["audit"]},
                     _set("pool", 12)),
    "regional": _profile(("base",), _set("region", "east"),
                         {"op": "delete", "key": "obsolete"}),
    "prod": _profile(("secure", "fast", "regional"),
                     {"op": "append", "key": "features", "values": ["metrics", "tls"]},
                     _set("nullable", False)),
    "canary": _profile(("fast", "secure", "regional"),
                       {"op": "remove", "key": "features", "values": ["read"]}),
    "left": _profile(("secure", "fast")),
    "right": _profile(("fast", "secure")),
    "broken": _profile(("left", "right")),
    "cycle_a": _profile(("cycle_b",)),
    "cycle_b": _profile(("cycle_c",)),
    "cycle_c": _profile(("cycle_a",)),
    "unknown": _profile(("not_installed", "base")),
    "bad_type": _profile(("base",), {"op": "append", "key": "pool", "values": [7]}),
    "typed": _profile((), _set("items", [1, True, 1.0, {"a": 1}]),
                      {"op": "remove", "key": "items", "values": [True]},
                      {"op": "append", "key": "items", "values": [1, 1.0, False, {"a": 1}]}),
    "empty": _profile((), {"op": "remove", "key": "missing", "values": ["x"]},
                      {"op": "append", "key": "new", "values": []}),
    "cleanup": _profile(("prod",), {"op": "delete", "key": "features"},
                        {"op": "append", "key": "features", "values": ["minimal"]}),
}
_PROFILE_QUERIES = [{"id": f"q{i:02}", "profile": name} for i, name in enumerate(
    ["prod", "canary", "broken", "cycle_a", "unknown", "bad_type", "typed", "empty",
     "cleanup", "secure", "fast", "regional", "base"])]
_PROFILE_DATA = {"profiles": _PROFILES, "queries": _PROFILE_QUERIES}
SOLVERS[495] = solve495
ANCHORS[495] = [
    ({"profiles": {"base": _profile((), _set("x", 1)),
                   "a": _profile(("base",), _set("x", 2)),
                   "b": _profile(("base",), _set("x", 3)),
                   "c": _profile(("a", "b"))},
      "queries": [{"id": "q", "profile": "c"}]},
     {"results": [{"id": "q", "status": "ok", "mro": ["c", "a", "b", "base"],
                   "config": {"x": 2}, "writers": {"x": "a"}}]}),
    ({"profiles": {"a": _profile(("b",)), "b": _profile(("a",))},
      "queries": [{"id": "q", "profile": "a"}]},
     {"results": [{"id": "q", "status": "cycle", "cycle": ["a", "b", "a"]}]}),
]
TASKS.append(artifact_task(
    number=495, slug="c3_profile_resolution", name="Resolve profile inheritance and list edits",
    parent=363,
    prompt="""
В profiles/*.json лежат профили конфигурации, в requests.json — запросы.
Для каждого profile независимо рассчитай C3 linearization: L(P)=[P]+
merge(L(parent1),...,L(parentN),[parent1,...,parentN]). C3 merge выбирает
head первой слева непустой последовательности, который не встречается
в tail НИ ОДНОЙ последовательности, затем удаляет его из heads всех
последовательностей. Порядок parents значим, дубликатов parents нет.
DFS родителей слева направо; кеш успешных L локален каждому запросу.
Unknown ->status unknown,at имя. Повтор активного профиля ->cycle,
cycle — суффикс DFS stack с этого имени плюс повтор имени в конце.
Нет допустимого C3 head ->inconsistent,at текущий профиль,
heads — heads оставшихся непустых последовательностей в текущем порядке,
с повторами. Первая DFS ошибка прекращает только текущий запрос.

При успешном C3 примени ops профилей в ОБРАТНОМ порядке L, каждый профиль
ровно один раз. Config — плоская карта, ключи с точками не раскрываются.
set сохраняет value и writer=профиль, даже если значение прежнее.
delete удаляет key и writer, отсутствие допустимо.
append/remove имеют values-массив. Если key есть и не list ->type,
at=профиль,operation=номер op с1. append к отсутствующему создаёт[];
потом добавляет лишь отсутствующие values в исходном порядке, сохраняя
старые дубликаты. remove удаляет ВСЕ элементы, равные любому values.
Равенство строгое по JSON типам (true !=1 !=1.0), object key order не важен.
remove отсутствующего — no-op; append пустого к отсутствующему создаёт[].
writer при append/remove меняется только если поле создано или список
изменился. При type не возвращай частичную конфигурацию.

answer.json={results:[...]} в requests-порядке. Успех:
{id,status:"ok",mro,config,writers}. Ошибка: {id,status,...поля выше}.
Дополнительных полей у ошибок нет. Профили и requests менять нельзя.
""",
    files={"requests.json": dumps(_PROFILE_QUERIES),
           **{f"profiles/{name}.json": dumps(profile) for name, profile in _PROFILES.items()}},
    expected=solve495(_PROFILE_DATA), tags=("systems", "config", "inheritance"),
))


def solve496(data):
    import copy

    absent = object()
    conflicts = []

    def eq(a, b):
        if a is absent or b is absent:
            return a is b
        if type(a) is not type(b):
            return False
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(eq(a[k], b[k]) for k in a)
        if isinstance(a, list):
            return len(a) == len(b) and all(eq(x, y) for x, y in zip(a, b, strict=True))
        return a == b

    def clone(x):
        return absent if x is absent else copy.deepcopy(x)

    def child(path, key):
        return path + "/" + key.replace("~", "~0").replace("/", "~1")

    def conflict(path, kind, b, o, t):
        choice = data["policy"].get(path, "unresolved")
        conflicts.append({"path": path, "kind": kind, "resolution": choice})
        return clone({"base": b, "ours": o, "theirs": t, "unresolved": b}[choice])

    def keyed(value):
        return isinstance(value, list) and all(
            isinstance(v, dict) and isinstance(v.get("id"), str) for v in value
        ) and len({v["id"] for v in value}) == len(value)

    def merge(b, o, t, path):
        if eq(o, t):
            return clone(o)
        if eq(o, b):
            return clone(t)
        if eq(t, b):
            return clone(o)
        if isinstance(o, dict) and isinstance(t, dict) and (isinstance(b, dict) or b is absent):
            base = {} if b is absent else b
            result = {}
            for key in sorted(set(base) | set(o) | set(t)):
                value = merge(base.get(key, absent), o.get(key, absent), t.get(key, absent), child(path, key))
                if value is not absent:
                    result[key] = value
            return result
        if keyed(b) and keyed(o) and keyed(t):
            bm, om, tm = ({v["id"]: v for v in side} for side in (b, o, t))
            values = {}
            for key in sorted(set(bm) | set(om) | set(tm)):
                value = merge(bm.get(key, absent), om.get(key, absent), tm.get(key, absent), child(path, "@" + key))
                if value is not absent:
                    values[key] = value
            order_b, order_o, order_t = ([v["id"] for v in side if v["id"] in values] for side in (b, o, t))
            edges = set()
            for seq in (order_o, order_t):
                edges.update(zip(seq, seq[1:], strict=False))
            remaining = set(values)
            order = []
            rank = {key: i for i, key in enumerate(order_b)}
            while remaining:
                ready = [key for key in remaining if not any(y == key and x in remaining for x, y in edges)]
                if not ready:
                    chosen = conflict(child(path, "$order"), "order", order_b, order_o, order_t)
                    order = []
                    for key in [*chosen, *order_b, *sorted(values)]:
                        if key not in order:
                            order.append(key)
                    break
                key = min(ready, key=lambda k: (rank.get(k, len(order_b)), k))
                remaining.remove(key)
                order.append(key)
            return [values[key] for key in order]
        kind = "delete_modify" if b is not absent and (o is absent or t is absent) else "value"
        return conflict(path, kind, b, o, t)

    merged = merge(data["base"], data["ours"], data["theirs"], "")
    return {"merged": merged, "conflicts": sorted(conflicts, key=lambda c: (c["path"], c["kind"]))}


_MERGE_PROMPT = r"""
Реализуй структурный three-way merge: вход {base,ours,theirs,policy}.
Первые три — JSON objects, policy=JSON Pointer->"base"|"ours"|"theirs".
Отсутствующий key — отдельное значение ABSENT, не null; равенство строгое
по типам (bool !=int !=float), порядок object keys не важен.
В КАЖДОМ узле сначала: ours==theirs ->ours; ours==base ->theirs;
theirs==base ->ours. Эти shortcuts действуют и для целых объектов/списков,
и для ABSENT, до следующих правил.

Если обе стороны objects и base object ИЛИ ABSENT, рекурсивно merge union
keys в сортированном порядке; ABSENT base трактуется как пустой object.
Результат ABSENT удаляет key. Иначе, если ВСЕ три значения — массивы
objects с уникальными string id (пустой массив подходит), это keyed array:
merge элементы по id рекурсивно, отсутствующий элемент=ABSENT.
Path элемента — child token "@"+id; далее обычные поля. id не удаляются
внутри существующих элементов, все входные keyed элементы валидны.
После merge values: отфильтруй удалённые id из каждого исходного порядка.
Каждая пара СОСЕДНИХ surviving id в ours и theirs даёт directed order edge.
Toposort по этим edges; среди готовых выбирай минимум
(индекс в surviving base, id); отсутствующий в base индекс=len(base survivors).

Если order edges цикличны, один conflict kind="order" по child token "$order".
Его resolution выбирает порядок соответствующей стороны; unresolved
использует base. Итоговый порядок: выбранные surviving id, затем ещё не
включённые surviving base id, затем ещё не включённые id лексикографически.
Частичный topological prefix при cycle отбрасывается.

Другие расхождения дают conflict: kind="delete_modify", если base не ABSENT
и одна сторона ABSENT, иначе "value". Policy применяется только по ТОЧНОМУ
path, наследования нет. Resolution=policy[path] или "unresolved"; выбранное
значение берётся как есть (рекурсивно уже не merge), unresolved берёт base.
Даже разрешённый policy конфликт обязательно записывается.

Path — стандартные JSON Pointer escapes (~0/~1), root "". "$order" и
"@id" являются отдельными токенами, тоже escaping. Вход не использует
"$order" как реальный object key; id могут содержать slash/tilde.
Вывод {merged,conflicts}, conflicts=[{path,kind,resolution}] сортированы
по (path,kind). <=80 узлов во входе, глубина<=10, policy может содержать
неиспользованные пути. Неразрешённые конфликты не являются ошибкой программы.
"""


def _merge(base, ours, theirs, policy=None):
    return {"base": base, "ours": ours, "theirs": theirs, "policy": policy or {}}


_program(
    496, "structural_three_way_merge", "Merge keyed structures and competing order constraints",
    364, _MERGE_PROMPT, solve496,
    [
        (_merge({"x": 1}, {"x": 2}, {"x": 1, "y": 3}),
         {"merged": {"x": 2, "y": 3}, "conflicts": []}),
        (_merge({"x": 1}, {}, {"x": 2}, {"/x": "theirs"}),
         {"merged": {"x": 2},
          "conflicts": [{"path": "/x", "kind": "delete_modify", "resolution": "theirs"}]}),
    ],
    [
        _merge({"x": None}, {}, {"x": 1}),
        _merge({}, {"x": {"a": 1}}, {"x": {"b": 2}}),
        _merge({"x": 0}, {"x": False}, {"x": 0.0}),
        _merge({"x": {"a": 1}}, {"x": 2}, {"x": {"a": 3}}, {"/x/a": "ours"}),
        _merge({"x": [1, 2]}, {"x": [2, 1]}, {"x": [1, 2, 3]}, {"/x": "ours"}),
        _merge({"x": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]},
               {"x": [{"id": "b", "v": 2}, {"id": "a", "v": 1}]},
               {"x": [{"id": "a", "v": 3}, {"id": "b", "v": 2}]}),
        _merge({"x": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]},
               {"x": [{"id": "b", "v": 2}, {"id": "a", "v": 1}]},
               {"x": [{"id": "a", "v": 3}, {"id": "b", "v": 2}]},
               {"/x/$order": "ours"}),
        _merge({"x": []}, {"x": [{"id": "b", "v": 1}]}, {"x": [{"id": "a", "v": 2}]}),
        _merge({"x": [{"id": "a/b", "v": 1}]}, {"x": []},
               {"x": [{"id": "a/b", "v": 2}]}, {"/x/@a~1b": "theirs"}),
        _merge({"x": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]},
               {"x": [{"id": "a", "v": 3}, {"id": "c", "v": 0}]},
               {"x": [{"id": "a", "v": 1}, {"id": "b", "v": 4}, {"id": "d", "v": 5}]},
               {"/x/@b": "ours"}),
        _merge({"a/b": {"~x": 1}}, {"a/b": {"~x": 2}}, {"a/b": {"~x": 3}},
               {"/a~1b/~0x": "theirs"}),
        _merge({}, {"x": [{"id": "a"}]}, {"x": [{"id": "b"}]}, {"/x": "theirs"}),
        _merge({"x": [{"id": "a"}]}, {"x": []}, {"x": []}),
    ],
)


def solve497(data):
    lines = data["lines"]
    hunks = sorted(data["hunks"], key=lambda h: h["id"])
    choices = []
    for h in hunks:
        old, before, after = h["old"], h["before"], h["after"]
        choices.append([
            p for p in range(len(lines) - len(old) + 1)
            if lines[p:p+len(old)] == old and p >= len(before)
            and lines[p-len(before):p] == before
            and lines[p+len(old):p+len(old)+len(after)] == after
        ])
    best, selected = None, []

    def overlap(p, size, q, other_size):
        if size == 0:
            return q <= p <= q + other_size
        if other_size == 0:
            return p <= q <= p + size
        return max(p, q) < min(p+size, q+other_size)

    def search(i, weight, count, distance):
        nonlocal best
        if i == len(hunks):
            vector = tuple(p if p is not None else len(lines) + 1 for p in selected)
            score = (-weight, -count, distance, vector)
            if best is None or score < best[0]:
                best = score, list(selected)
            return
        h = hunks[i]
        for p in choices[i]:
            if any(q is not None and overlap(p, len(h["old"]), q, len(hunks[j]["old"]))
                   for j, q in enumerate(selected)):
                continue
            selected.append(p)
            search(i+1, weight+h["weight"], count+1, distance+abs(p-h["expected"]))
            selected.pop()
        selected.append(None)
        search(i+1, weight, count, distance)
        selected.pop()

    search(0, 0, 0, 0)
    edits = sorted((p, h) for h, p in zip(hunks, best[1], strict=True) if p is not None)
    result, cursor = [], 0
    for p, h in edits:
        result.extend(lines[cursor:p])
        result.extend(h["new"])
        cursor = p + len(h["old"])
    result.extend(lines[cursor:])
    return {
        "lines": result,
        "applied": [{"id": h["id"], "position": p} for h, p in zip(hunks, best[1], strict=True) if p is not None],
        "rejected": [h["id"] for h, p in zip(hunks, best[1], strict=True) if p is None],
        "weight": -best[0][0], "displacement": best[0][2],
    }


_PATCH_PROMPT = """
Перенеси набор контекстных patch hunks на изменённый файл ГЛОБАЛЬНО оптимально.
Вход {lines:[строки],hunks:[{id,old,new,before,after,expected,weight}]}.
id уникальны ASCII, old/new/before/after — массивы строк, expected>=0 —
ожидаемый ZERO-BASED offset в исходном lines, weight>=0.
<=6 hunks, <=40 lines, у каждого hunk <=8 допустимых позиций.
Candidate position p: lines[p:p+len(old)]==old, непосредственно перед
p лежит весь before, непосредственно после old лежит весь after.
Контексты полностью внутри файла. Пустые контексты ограничений не дают,
old=[] — insertion между строками, p от0 до len(lines) включительно.
ВСЕ matches вычисляются на исходном lines, никогда на промежуточном тексте.

Выбери для каждого hunk одну candidate position ИЛИ reject. Два непустых
old-контура не могут пересекаться, соседние [a,b) и[b,c) разрешены.
Insertion в p конфликтует с любым replacement [a,b), если a<=p<=b
(ОБА края включены), и с другой insertion в том же p.
Пересечение контекстов разрешено: before/after ничего не резервируют.

Минимизируй кортеж (-сумма weight принятых,-их число,
сумма abs(position-expected),vector). vector — выбранные позиции по id,
reject кодируется len(lines)+1. Таким образом важность первична, но при
равном весе больше hunks лучше, даже с weight=0; затем ближе expected,
затем лексикографически меньшие позиции.
Примени выбранные edits одновременно по исходным offsets, сохрани весь
остальной текст. Вывод {lines,applied,rejected,weight,displacement};
applied=[{id,position}] по id, rejected=[id] по id.
Вес, offsets и все критерии целочисленны. Внешний ввод валиден.
"""


def _hunk(name, old, new, **kw):
    return {"id": name, "old": old, "new": new, "before": [], "after": [],
            "expected": 0, "weight": 1, **kw}


_program(
    497, "optimal_context_patch", "Rebase conflicting patches with global matching",
    364, _PATCH_PROMPT, solve497,
    [
        ({"lines": ["a", "b"], "hunks": [_hunk("x", ["b"], ["B"], expected=1)]},
         {"lines": ["a", "B"], "applied": [{"id": "x", "position": 1}],
          "rejected": [], "weight": 1, "displacement": 0}),
        ({"lines": ["x", "x"], "hunks": [
            _hunk("a", ["x"], ["A"]), _hunk("b", ["x"], ["B"])]},
         {"lines": ["A", "B"], "applied": [{"id": "a", "position": 0}, {"id": "b", "position": 1}],
          "rejected": [], "weight": 2, "displacement": 1}),
    ],
    [
        {"lines": [], "hunks": [_hunk("x", [], ["hello"])]},
        {"lines": ["a"], "hunks": [_hunk("a", ["missing"], ["x"])]},
        {"lines": ["a", "b", "c"], "hunks": [
            _hunk("a", ["a", "b"], ["X"], weight=3), _hunk("b", ["b", "c"], ["Y"], weight=4)]},
        {"lines": ["a", "b"], "hunks": [
            _hunk("a", ["a", "b"], ["X"], weight=2), _hunk("b", ["a"], ["A"]),
            _hunk("c", ["b"], ["B"])]},
        {"lines": ["a", "b"], "hunks": [
            _hunk("a", ["a"], ["A"], weight=2), _hunk("b", [], ["B"], before=["a"], after=["b"])]},
        {"lines": ["a", "b", "c"], "hunks": [
            _hunk("a", ["a"], [], weight=0), _hunk("b", ["b"], ["B"], before=["a"])]},
        {"lines": ["x", "a", "x", "b", "x"], "hunks": [
            _hunk("a", ["x"], ["X"], before=["a"]), _hunk("b", ["x"], ["Y"], after=["b"])]},
        {"lines": ["a", "b"], "hunks": [
            _hunk("a", [], ["X"], before=["a"]), _hunk("b", [], ["Y"], after=["b"])]},
        {"lines": ["a", "b"], "hunks": [
            _hunk("a", ["a"], ["b"]), _hunk("b", ["b"], ["c"], expected=1)]},
        {"lines": ["x"] * 6, "hunks": [
            _hunk(chr(97+i), ["x"], [str(i)], expected=5-i, weight=i % 2) for i in range(6)]},
        {"lines": ["a", "b", "a", "b", "a", "b"], "hunks": [
            _hunk("left", ["a", "b"], ["L"], expected=3),
            _hunk("right", ["a", "b"], ["R"], expected=2),
            _hunk("insert", [], ["I"], before=["b"], after=["a"], weight=3)]},
        {"lines": ["end"], "hunks": []},
    ],
)


_MIGRATION_FILES = {
    "src/blueledger/__init__.py": "from .api import total\n",
    "src/blueledger/api.py": "def total(values):\n    return sum(values)\n",
    "src/blueledger/cli.py": (
        "import blueledger.api\nfrom greenledger.core import run\n"
        "def main():\n    return blueledger.api.total([run()])\n"
    ),
    "src/greenledger/__init__.py": "from .core import run\n",
    "src/greenledger/core.py": "def run():\n    return 7\n",
    "app.py": (
        "import blueledger.api\nimport greenledger.core\nimport blueledger_extra\n"
        "from blueledger import api as ledger_api\nfrom greenledger.core import run\n"
        "label = 'blueledger.api'\n"
        "def global_use():\n    return blueledger.api.total([greenledger.core.run()])\n"
        "def shadow(blueledger):\n    return blueledger.api\n"
        "def local_shadow():\n    blueledger = object()\n    return blueledger\n"
        "items = [blueledger.api for blueledger in []]\n"
        "value = blueledger.api.total([run()])\n"
    ),
    "plugin.py": (
        "import importlib as il\nfrom importlib import import_module as load\n"
        "from blueledger.api import total as add\nimport greenledger.core as core\n"
        "A = il.import_module('blueledger.api')\n"
        "B = load('greenledger.core')\n"
        "C = load('blueledger_extra')\n"
        "def use(load):\n    return load('blueledger.api')\n"
        "log = 'greenledger.core'\n"
    ),
    "tests/test_api.py": (
        "from blueledger.api import total\nfrom greenledger.core import run\n"
        "def test_api():\n    assert total([run()]) == 7\n"
        "fixture = 'blueledger'\n"
    ),
    "pyproject.toml": (
        "[project]\nname = \"blue-ledger\"\n"
        "dependencies = [\"green-ledger[fast]>=2\", \"blue-ledger-extra>=1\"]\n"
        "[project.scripts]\nledger = \"blueledger.cli:main\"\n"
        "[project.entry-points.\"demo.plugins\"]\ncore = \"greenledger.core:run\"\n"
        "[tool.history]\nprevious = \"blueledger.cli:main\"\n"
    ),
    "requirements.txt": (
        "blue-ledger[cli]>=1; python_version >= '3.12'\n"
        "green-ledger==2\nblue-ledger-extra>=1\n# blue-ledger historical spelling\n"
    ),
    "README.md": (
        "# Ledger\nArchive: python -m blueledger.cli\n"
        "<!-- active:start -->\nRun `python -m blueledger.cli`.\n"
        "API: `greenledger.core`; literal `blueledger_extra` stays.\n"
        "<!-- active:end -->\nHistorical import: `blueledger.api`.\n"
    ),
    "Dockerfile": (
        'ENV PREVIOUS_MODULE="blueledger.cli"\n'
        'CMD ["python", "-m", "blueledger.cli"]\n'
    ),
    ".github/workflows/test.yml": (
        "steps:\n  - run: python -m pytest tests\n"
        "  - run: python -m greenledger.core\n"
        "  - run: echo blueledger.cli\n"
    ),
    "CHANGELOG.md": "1.0: blueledger and greenledger shipped together.\n",
    "tests/data/legacy_blueledger.json": '{"module":"blueledger.api","distribution":"blue-ledger"}\n',
    "migrations/legacy_blueledger.sql": "-- historical blueledger table\n",
}
_MIGRATION_RULES = {
    "modules": {"blueledger": "greenledger", "greenledger": "ledger_core"},
    "distributions": {"blue-ledger": "green-ledger", "green-ledger": "ledger-core"},
    "source_root": "src",
    "exclude": ["CHANGELOG.md", "tests/data/legacy_blueledger.json", "migrations/legacy_blueledger.sql"],
}
_MIGRATION_EXPECTED = dict(_MIGRATION_FILES)
_MIGRATION_EXPECTED["app.py"] = (
    "import greenledger.api\nimport ledger_core.core\nimport blueledger_extra\n"
    "from greenledger import api as ledger_api\nfrom ledger_core.core import run\n"
    "label = 'blueledger.api'\n"
    "def global_use():\n    return greenledger.api.total([ledger_core.core.run()])\n"
    "def shadow(blueledger):\n    return blueledger.api\n"
    "def local_shadow():\n    blueledger = object()\n    return blueledger\n"
    "items = [blueledger.api for blueledger in []]\n"
    "value = greenledger.api.total([run()])\n"
)
_MIGRATION_EXPECTED["plugin.py"] = (
    "import importlib as il\nfrom importlib import import_module as load\n"
    "from greenledger.api import total as add\nimport ledger_core.core as core\n"
    "A = il.import_module('greenledger.api')\n"
    "B = load('ledger_core.core')\n"
    "C = load('blueledger_extra')\n"
    "def use(load):\n    return load('blueledger.api')\n"
    "log = 'greenledger.core'\n"
)
_MIGRATION_EXPECTED["tests/test_api.py"] = (
    "from greenledger.api import total\nfrom ledger_core.core import run\n"
    "def test_api():\n    assert total([run()]) == 7\n"
    "fixture = 'blueledger'\n"
)
_MIGRATION_EXPECTED["src/blueledger/cli.py"] = (
    "import greenledger.api\nfrom ledger_core.core import run\n"
    "def main():\n    return greenledger.api.total([run()])\n"
)
_MIGRATION_EXPECTED["pyproject.toml"] = (
    "[project]\nname = \"green-ledger\"\n"
    "dependencies = [\"ledger-core[fast]>=2\", \"blue-ledger-extra>=1\"]\n"
    "[project.scripts]\nledger = \"greenledger.cli:main\"\n"
    "[project.entry-points.\"demo.plugins\"]\ncore = \"ledger_core.core:run\"\n"
    "[tool.history]\nprevious = \"blueledger.cli:main\"\n"
)
_MIGRATION_EXPECTED["requirements.txt"] = (
    "green-ledger[cli]>=1; python_version >= '3.12'\n"
    "ledger-core==2\nblue-ledger-extra>=1\n# blue-ledger historical spelling\n"
)
_MIGRATION_EXPECTED["README.md"] = (
    "# Ledger\nArchive: python -m blueledger.cli\n"
    "<!-- active:start -->\nRun `python -m greenledger.cli`.\n"
    "API: `ledger_core.core`; literal `blueledger_extra` stays.\n"
    "<!-- active:end -->\nHistorical import: `blueledger.api`.\n"
)
_MIGRATION_EXPECTED["Dockerfile"] = (
    'ENV PREVIOUS_MODULE="blueledger.cli"\n'
    'CMD ["python", "-m", "greenledger.cli"]\n'
)
_MIGRATION_EXPECTED[".github/workflows/test.yml"] = (
    "steps:\n  - run: python -m pytest tests\n"
    "  - run: python -m ledger_core.core\n"
    "  - run: echo blueledger.cli\n"
)
_MIGRATION_MOVES = []
_MIGRATION_FINAL = {}
for _path, _content in _MIGRATION_EXPECTED.items():
    _parts = _path.split("/")
    if len(_parts) > 2 and _parts[0] == "src" and _parts[1] in _MIGRATION_RULES["modules"]:
        _new_path = "/".join(["src", _MIGRATION_RULES["modules"][_parts[1]], *_parts[2:]])
        _MIGRATION_MOVES.append({"from": _path, "to": _new_path})
    else:
        _new_path = _path
    _MIGRATION_FINAL[_new_path] = _content
TASKS.append(artifact_task(
    number=498, slug="simultaneous_namespace_migration", name="Plan a simultaneous package namespace migration",
    parent=365,
    prompt="""
В tree/ находится небольшой Python project, migration.json задаёт
ОДНОВРЕМЕННУЮ миграцию namespace. Составь answer.json={moves,files} —
полный итоговый виртуальный project; исходный tree/ физически не меняй.
files — карта path (БЕЗ tree/) -> полный текст файла после миграции,
включая неизменённые. moves=[{from,to}] только реально перемещённых файлов,
по from. Directory moves описывай отдельными file moves, без dir entries.
Маппинги применяются ОДИН раз к ИСХОДНЫМ именам, не каскадом:
blueledger ->greenledger, а исходный greenledger ->ledger_core.

1. src/<module-root>/... перемещается согласно modules. Относительные
imports сохраняются. Python: обнови абсолютные import/from module roots,
только целый первый dotted компонент (blueledger_extra не подходит).
Explicit import aliases сохраняй. При bare "import old.sub" меняется
привязанный root, поэтому обнови все Name references к ЭТОЙ module-scope
привязке, включая свободные ссылки внутри функций. Не меняй references,
которые связываются с одноимённым параметром, локальной переменной или
target comprehension. from-import локальные имена/aliases не меняются.
Python scopes и shadowing обычные; в данных нет exec, star imports,
global/nonlocal, классов, f-strings или неоднозначной динамики.

2. Строковый module argument обновляй ТОЛЬКО у importlib.import_module,
включая importlib alias и from-import alias, если имя вызова в данном
scope действительно связано с importlib. Shadowed load, обычные строки,
fixture names и комментарии сохраняй.
3. pyproject: distribution map только project.name и имена dependencies;
module map только module часть project.scripts и project.entry-points.
tool.history не трогай. requirements.txt: только distribution names
в активных requirements, сохрани extras/version/markers/comments.
4. README: замени полные dotted module names в backticks только внутри
<!-- active:start -->...<!-- active:end -->. В python -m командах
изменяется module argument. Вне блока всё историческое.
5. Dockerfile: module argument JSON CMD ["python","-m",...] меняется,
ENV сохраняется. Workflow: module argument команды python -m меняется,
echo и остальная часть строки сохраняются.
6. Все exclude файлы неизменны и не перемещаются.

Во всех файлах сохрани исходные кавычки, пробелы, LF и весь остальной текст:
разрешены только перечисленные token replacements. В файлах применены
обычные unescaped имена; форматтер не нужен. Проверяется полный files map,
поэтому нельзя потерять неизменённый файл или оставить старый src path.
""",
    files={"migration.json": dumps(_MIGRATION_RULES),
           **{f"tree/{path}": text for path, text in _MIGRATION_FILES.items()}},
    expected={"moves": sorted(_MIGRATION_MOVES, key=lambda item: item["from"]),
              "files": _MIGRATION_FINAL},
    tags=("systems", "refactoring", "python"),
))


def solve499(data):
    from functools import lru_cache

    files, rename = data["files"], data["rename"]
    unknown = sorted(set(rename) - set(files))
    if unknown:
        return {"error": "unknown", "names": unknown}
    targets = list(rename.values())
    duplicate = sorted({t for t in targets if targets.count(t) > 1})
    if duplicate:
        return {"error": "duplicate", "names": duplicate}
    blocked = sorted({t for t in targets if t in files and t not in rename})
    if blocked:
        return {"error": "blocked", "names": blocked}
    ids = sorted(files)
    goals = tuple(rename.get(n, n) for n in ids)
    sizes = [files[n] for n in ids]
    temp = data["temp"]

    @lru_cache(None)
    def search(state):
        if state == goals:
            return 0, 0, ()
        occupied = set(state)
        best = None
        for i, name in enumerate(state):
            if name == goals[i]:
                continue
            actions = [(goals[i], 0)] if goals[i] not in occupied else []
            if name != temp and temp not in occupied:
                actions.append((temp, sizes[i]))
            for target, cost in actions:
                nxt = list(state)
                nxt[i] = target
                future = search(tuple(nxt))
                if future is None:
                    continue
                score = (cost + future[0], 1 + future[1], ((name, target),) + future[2])
                if best is None or score < best:
                    best = score
        return best

    optimum = search(tuple(ids))
    return {
        "error": None, "temp_bytes": optimum[0],
        "steps": [{"from": a, "to": b} for a, b in optimum[2]],
        "final": {target: source for source, target in zip(ids, goals, strict=True)},
    }


_RENAME_PROMPT = """
Спланируй безопасное batch rename без overwrite с ОДНИМ временным именем.
Вход {files,rename,temp}; files=name->неотрицательный размер в bytes,
rename=source->target, temp — отдельное имя, отсутствует во всех keys/values.
Имена case-sensitive ASCII, плоские, без slash; <=8 files.
Сначала ошибки в таком приоритете: unknown (sources не существуют);
duplicate (target встречается >1 раза); blocked (target существует,
но не является source в rename). Вывод ошибки {error:код,names:[по алфавиту]}.
Self-rename — допустимый source, участвует в duplicate validation.

После проверки каждому исходному file назначена цель rename.get(name,name).
Identity file = его ИСХОДНОЕ имя. Одна операция атомарно перемещает file
из занятого имени в свободное, содержимое не меняется. Разрешено двигать
только file, который ещё не в своей окончательной цели. Его destination:
либо собственная окончательная цель (если свободна), либо temp (если
свободен и file сейчас не в temp). Из temp можно идти лишь в свою цель.
Другие временные имена запрещены. Уже достигший цели file больше не движется.

Найди план, минимизирующий лексикографически:
(1) temp_bytes — сумма размеров files при КАЖДОМ перемещении В temp;
(2) число операций;
(3) полный список пар [from,to] в порядке выполнения, обычное
лексикографическое сравнение строк/списков.
Прямой rename в final цель не добавляет temp_bytes. Нулевые размеры допустимы,
поэтому второй критерий нужен. Можно произвольно чередовать компоненты
графа; недостаточно отдельно отсортировать циклы и цепочки.
Вывод {error:null,temp_bytes,steps:[{from,to}],final};
final=окончательное имя->исходная identity, включая неизменённые files.
Пустые maps допустимы. Оптимальность должна быть точной.
"""


def _rename(files, rename):
    return {"files": files, "rename": rename, "temp": "~tmp"}


_program(
    499, "minimal_spill_rename", "Optimize cycle-safe renames with one scratch name", 365,
    _RENAME_PROMPT, solve499,
    [
        (_rename({"a": 2, "b": 1}, {"a": "b", "b": "a"}),
         {"error": None, "temp_bytes": 1, "steps": [
             {"from": "b", "to": "~tmp"}, {"from": "a", "to": "b"}, {"from": "~tmp", "to": "a"}],
          "final": {"b": "a", "a": "b"}}),
        (_rename({"a": 3, "b": 4}, {"a": "b"}),
         {"error": "blocked", "names": ["b"]}),
    ],
    [
        _rename({}, {}),
        _rename({"a": 1}, {"missing": "a", "a": "a"}),
        _rename({"a": 1, "b": 2}, {"a": "b", "b": "b"}),
        _rename({"a": 1, "b": 2, "c": 3}, {"a": "b", "b": "c", "c": "d"}),
        _rename({"a": 1, "b": 1}, {"a": "b", "b": "a"}),
        _rename({"a": 0, "b": 0}, {"a": "b", "b": "c"}),
        _rename({"a": 3, "b": 0, "c": 4}, {"a": "b", "b": "c", "c": "a"}),
        _rename({"a": 1, "b": 3, "c": 2, "d": 1}, {"a": "b", "b": "a", "c": "d", "d": "c"}),
        _rename({"a": 8, "b": 3, "c": 2, "d": 1, "e": 4},
                {"a": "b", "b": "c", "c": "a", "d": "z", "e": "d"}),
        _rename({"a": 0, "b": 0, "c": 0, "d": 0}, {"a": "b", "b": "a", "c": "d", "d": "c"}),
        _rename({"a": 1, "b": 2}, {"a": "a"}),
        _rename({chr(97+i): (i*7) % 5 for i in range(8)},
                {chr(97+i): chr(97+(i+1) % 8) for i in range(8)}),
    ],
)

# Stable numeric order is part of the task-set contract.
TASKS.sort(key=lambda task: int(task.id.split("_")[1]))

# The old algorithms remain executable regressions. Active task contracts and
# hidden suites use the independently versioned semantic hardening below.
LEGACY_CASES = CASES
LEGACY_TASKS = TASKS
TASKS = systems_v4.TASKS
CASES = systems_v4.CASES
SOLVERS = systems_v4.SOLVERS
ANCHORS = systems_v4.ANCHORS

# Selective post-calibration v5: the other ten v4 tasks remain byte-identical.
_revised_systems = {int(task.id.split("_")[1]): task for task in systems_v5.TASKS}
TASKS = [_revised_systems.get(int(task.id.split("_")[1]), task) for task in TASKS]
CASES = {**CASES, **systems_v5.CASES}
SOLVERS = {**SOLVERS, **systems_v5.SOLVERS}
ANCHORS = {**ANCHORS, **systems_v5.ANCHORS}

# Only 487 advances again; all other task contracts remain frozen at v4/v5.
TASKS = [systems_v6.TASK if task.id == systems_v6.TASK.id else task for task in TASKS]
CASES = {**CASES, 487: systems_v6.CASES}
SOLVERS = {**SOLVERS, 487: systems_v6.solve487}
ANCHORS = {**ANCHORS, 487: systems_v6.CASES[:2]}
