"""Wave 20: hard descendants of the six practical workflow tasks 366--371.

Reference programs are emitted as standalone stdlib-only source.  Input cases
stay on the evaluator side; independent small oracles live in the tests.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import inspect
import io
import json
import posixpath
import re
import unicodedata
from fractions import Fraction

from harness_bench.general_hard.common import artifact_task, dumps, program_task
from harness_bench.general_hard.workflow_revision import build_revision as _build_revision
from harness_bench.general_hard.workflow_revision_v5 import build_v5 as _build_v5
from harness_bench.general_hard.workflow_revision_v6 import build_v6 as _build_v6


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _solve_500(data):
    snapshots = {item["id"]: item for item in data["snapshots"]}
    states = {}

    def materialize(name):
        if name in states:
            return states[name]
        item = snapshots[name]
        state = dict(materialize(item["parent"])) if item["parent"] is not None else {}
        for op in item["ops"]:
            path = op["path"]
            if op["op"] == "copy":
                source = op["source"]
                captured = {
                    path + key[len(source):]: value
                    for key, value in state.items()
                    if key == source or key.startswith(source + "/")
                }
            for key in list(state):
                if key == path or key.startswith(path + "/"):
                    del state[key]
            if op["op"] == "put":
                state[path] = data["objects"][op["object"]]
            elif op["op"] == "copy":
                state.update(captured)
        states[name] = state
        return state

    manifests = []
    blobs = {}
    logical = 0
    for name in sorted(set(data["keep"])):
        files = []
        for path, text in sorted(materialize(name).items()):
            digest, size = _sha(text), len(text.encode())
            files.append({"path": path, "sha256": digest, "size": size})
            blobs[digest] = size
            logical += size
        wire = "".join(f"{r['path']}\0{r['size']}\0{r['sha256']}\n" for r in files)
        manifests.append({"id": name, "files": files, "tree_sha256": _sha(wire)})
    existing = set(data["existing"])
    required = set(blobs)
    return {
        "snapshots": manifests,
        "required": sorted(required),
        "upload": sorted(required - existing),
        "gc": sorted(existing - required),
        "logical_bytes": logical,
        "physical_bytes": sum(blobs.values()),
    }


_PROMPT_500 = """Реализуй планировщик content-addressed incremental backup.
Вход: objects={label: UTF-8 text}, snapshots=[{id,parent,ops}], keep=[id],
existing=[SHA-256]. id уникальны, parent=null или другой id; граф ацикличен,
но snapshots не топологически отсортированы. Каждый snapshot наследует копию
состояния parent, затем применяет ops по порядку. Состояние — mapping path→text.
Пути уже канонические относительные POSIX, без пустых сегментов/./../NUL.
op put имеет path,object: удаляет path и всё под path/, затем кладёт файл.
op drop имеет path: удаляет path и всё под path/; отсутствие допустимо.
op copy имеет source,path: СНАЧАЛА захватывает все файлы source и source/,
затем удаляет path и path/, затем переносит захваченную копию с заменой
префикса source на path. Источник не удаляется; отсутствие даёт пустую копию.
Это действует и при перекрытии source/path или source==path.
После каждой операции гарантировано отсутствие файла-предка другого файла.
keep может повторяться; сохранены только перечисленные snapshots, НЕ их
предки и НЕ другие ветви. Даже содержимое удалённых файлов не удерживает blob.

Выход: snapshots по уникальному id из keep; каждый {id,files,tree_sha256}.
files по path, элементы {path,size,sha256}; size — UTF-8 bytes, sha256 —
нижний hex SHA-256 bytes. tree_sha256 хеширует конкатенацию строк
path + NUL + decimal size + NUL + sha256 + LF в порядке files.
required — все уникальные хеши содержимого сохранённых файлов по алфавиту;
upload=required-existing, gc=existing-required, также уникально и по алфавиту.
logical_bytes — сумма size по всем files всех сохранённых снимков;
physical_bytes — сумма размеров уникальных required blobs (одинаковый текст
под разными object labels — один blob). Пустые файлы тоже являются blobs.
Неизвестных object/id нет. До 100 snapshots, 200 ops в каждом, 5000 файлов."""


def _path_501(path):
    if path.startswith("/") or "\0" in path:
        raise ValueError("path")
    parts = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ValueError("path")
            parts.pop()
        else:
            parts.append(unicodedata.normalize("NFC", unicodedata.normalize("NFC", part).casefold()))
    if not parts:
        raise ValueError("path")
    return "/".join(parts)


def _frame_501(data):
    return str(len(data)).encode() + b":" + data


def _solve_501(data):
    entries = {}
    for index, row in enumerate(data["entries"]):
        try:
            path = _path_501(row["path"])
        except ValueError:
            return {"error": {"code": "path", "index": index}}
        if path in entries:
            return {"error": {"code": "collision", "index": index}}
        entries[path] = row
    for path in sorted(entries):
        parts = path.split("/")
        for n in range(1, len(parts)):
            parent = "/".join(parts[:n])
            if parent in entries and entries[parent]["kind"] != "dir":
                return {"error": {"code": "structure", "path": path}}
    roots = {}

    def resolve(path, active):
        if path in roots:
            return roots[path]
        if path in active:
            raise ValueError("cycle")
        row = entries.get(path)
        if row is None or row["kind"] not in ("file", "hardlink"):
            raise ValueError("target")
        if row["kind"] == "file":
            root = path
        else:
            try:
                target = _path_501(row["target"])
            except ValueError as exc:
                raise ValueError("target") from exc
            root = resolve(target, active | {path})
        roots[path] = root
        return root

    for path in sorted(entries):
        if entries[path]["kind"] == "hardlink":
            try:
                resolve(path, set())
            except ValueError as exc:
                return {"error": {"code": str(exc), "path": path}}
        elif entries[path]["kind"] == "file":
            roots[path] = path
    nodes = dict(entries)
    nodes[""] = {"kind": "dir"}
    for path in entries:
        parts = path.split("/")
        for n in range(1, len(parts)):
            nodes.setdefault("/".join(parts[:n]), {"kind": "dir"})
    digests = {}
    kinds = {}
    for path in sorted(nodes, key=lambda p: (-p.count("/"), -len(p), p)):
        row = nodes[path]
        kind = row["kind"]
        if kind in ("file", "hardlink"):
            text = entries[roots[path]]["text"].encode()
            payload = b"F" + _frame_501(text)
            kind = "file"
        elif kind == "symlink":
            payload = b"L" + _frame_501(row["target"].encode())
        else:
            prefix = path + "/" if path else ""
            children = sorted(
                child for child in nodes
                if child != path and child.startswith(prefix)
                and "/" not in child[len(prefix):]
            )
            payload = b"D" + b"".join(
                _frame_501(child[len(prefix):].encode()) + bytes.fromhex(digests[child])
                for child in children
            )
        digests[path] = hashlib.sha256(payload).hexdigest()
        kinds[path] = kind
    groups = {}
    for path, root in roots.items():
        groups.setdefault(root, []).append(path)
    return {
        "root": digests[""],
        "entries": [
            {"path": path, "kind": kinds[path], "sha256": digests[path]}
            for path in sorted(nodes) if path
        ],
        "hardlink_groups": sorted([sorted(paths) for paths in groups.values()]),
        "logical_bytes": sum(len(entries[root]["text"].encode()) for root in roots.values()),
        "physical_bytes": sum(len(entries[root]["text"].encode()) for root in groups),
    }


_PROMPT_501 = """Построй каноническое Merkle-дерево переносимого архива.
Вход entries: file {path,kind:"file",text}, dir {path,kind:"dir"},
symlink {path,kind:"symlink",target}, hardlink {path,kind:"hardlink",target}.
Имена нормализуются: запрети начальный / и NUL; раздели на /, пропусти
пустые сегменты и ., .. снимает предыдущий сегмент (выход выше корня —
ошибка); каждый обычный сегмент преобразуй NFC→casefold→NFC.
Пустой итоговый путь запрещён. Сохраняй нормализованное написание.

Проверяй фазы в указанном порядке и возвращай ТОЛЬКО {"error":...} при
первой ошибке. Фаза 1: entries в исходном порядке, невалидный path —
{code:"path",index:0-based}; повтор нормализованного пути —
{code:"collision",index}. Фаза 2: пути по алфавиту; если собственный
предок пути явно задан НЕ как dir, {code:"structure",path:путь-потомок}.
Фаза 3: hardlink пути по алфавиту; target нормализуется тем же способом
ОТ КОРНЯ архива, а не от родительской директории. Цепь hardlink обязана
закончиться file. Отсутствующий/невалидный target или dir/symlink на конце
даёт {code:"target",path:проверяемый исходный hardlink}; цикл —
{code:"cycle",path:проверяемый исходный hardlink}. Сами symlink НЕ разрешай.
Неявные родительские директории создаются, отдельный корень имеет путь "".

fr(bytes)=ASCII decimal byte length + b":" + bytes, без ведущих нулей.
file и hardlink: SHA256(b"F"+fr(UTF8 текст конечного file));
symlink: SHA256(b"L"+fr(UTF8 исходный target), без нормализации target);
dir: SHA256(b"D"+конкатенация fr(UTF8 basename)+32 сырых bytes хеша
каждого непосредственного ребёнка, отсортированного по basename).
Выход {root,entries,hardlink_groups,logical_bytes,physical_bytes}.
root — hex хеш корня; entries по path без корня, каждый
{path,kind,sha256}, где hardlink получает kind=file, все неявные dir включены.
hardlink_groups — список отсортированных списков путей для каждого
физического file, ВКЛЮЧАЯ одноэлементные группы, внешний список
лексикографически. logical_bytes суммирует размеры всех file/hardlink;
physical_bytes — только исходных file (равный текст разных file НЕ
дедуплицируется). Только UTF-8, hex нижнего регистра. До 500 entries."""


def _solve_502(data):
    invoices = {row["id"]: dict(row, paid=0) for row in data["invoices"]}
    events = sorted(data["events"], key=lambda row: row["seq"])
    active = {}
    toggles = {}
    for row in reversed(events):
        active[row["id"]] = toggles.get(row["id"], 0) % 2 == 0
        if row["kind"] == "reverse" and active[row["id"]]:
            toggles[row["ref"]] = toggles.get(row["ref"], 0) + 1
    payments = {}
    refunds = []
    for row in events:
        if row["kind"] == "payment" and active[row["id"]]:
            payments[row["id"]] = dict(row, net=row["amount"], applied=0)
        elif row["kind"] == "refund" and active[row["id"]]:
            payment = payments.get(row["ref"])
            taken = min(row["amount"], payment["net"]) if payment else 0
            if payment:
                payment["net"] -= taken
            refunds.append({"id": row["id"], "accepted": taken, "rejected": row["amount"] - taken})
    # First honor every earmark; only then allocate the shared pool.
    for payment in payments.values():
        invoice = invoices.get(payment.get("invoice"))
        if invoice and (invoice["customer"], invoice["currency"]) == (
            payment["customer"], payment["currency"]
        ):
            amount = min(payment["net"], invoice["amount"] - invoice["paid"])
            invoice["paid"] += amount
            payment["applied"] += amount
    ordered = sorted(invoices.values(), key=lambda row: (row["due"], row["id"]))
    for payment in payments.values():
        for invoice in ordered:
            if (invoice["customer"], invoice["currency"]) != (
                payment["customer"], payment["currency"]
            ):
                continue
            amount = min(payment["net"] - payment["applied"], invoice["amount"] - invoice["paid"])
            invoice["paid"] += amount
            payment["applied"] += amount
    return {
        "invoices": [
            {
                "id": row["id"], "paid": row["paid"],
                "balance": row["amount"] - row["paid"],
                "status": "paid" if row["paid"] == row["amount"] else (
                    "unpaid" if row["paid"] == 0 else "partial"
                ),
            }
            for row in sorted(invoices.values(), key=lambda row: row["id"])
        ],
        "payments": [
            {"id": row["id"], "net": row["net"], "applied": row["applied"],
             "credit": row["net"] - row["applied"]}
            for row in sorted(payments.values(), key=lambda row: row["id"])
        ],
        "refunds": sorted(refunds, key=lambda row: row["id"]),
        "inactive": sorted(key for key, value in active.items() if not value),
    }


_PROMPT_502 = """Восстанови invoice settlement после refunds и обратимых reversals.
Вход invoices=[{id,customer,currency,due,amount}], events с уникальными id
и уникальными целыми seq (вход НЕ отсортирован). amount — целые >=0;
due — целое. События: payment {id,seq,kind,customer,currency,amount,invoice?};
refund {id,seq,kind,ref,amount}; reverse {id,seq,kind,ref}.
refund.ref всегда ссылается на более ранний payment; reverse.ref — на
любое более раннее событие, включая refund/reverse. Все ссылки существуют.

Сначала определи активность ВСЕХ событий: событие активно, когда число
АКТИВНЫХ reverse, непосредственно ссылающихся на него, чётное.
Это однозначно из-за направления ссылок в прошлое. Таким образом,
reverse reverse восстанавливает исходное событие, а два активных reverse
одной цели взаимно отменяют эффект. Неактивные события не исполняются.
Затем активные события по seq: payment создаёт net=amount; refund уменьшает
net своего payment на min(amount,текущий net). Если payment неактивен,
accepted=0. Излишек refund отклоняется; дальнейшего долга не возникает.
reverse на этой стадии действий не производит.

По окончательным net распредели деньги в ДВЕ фазы. (1) Все активные payments
по seq: earmark invoice, если существует и совпадает customer/currency,
получает min(net,ещё не оплаченная amount); неизвестный или чужой earmark
просто игнорируется. (2) Все payments вновь по seq: остаток распределяется
на ещё неоплаченные invoices того же customer/currency по (due,id).
Не создавай переплаты: остаток остаётся credit именно данного payment.
invoice с amount=0 всегда paid.
Выход: invoices по id [{id,paid,balance,status}], status paid/partial/unpaid;
payments по id, только активные [{id,net,applied,credit}];
refunds по id, только активные [{id,accepted,rejected}];
inactive — id всех неактивных событий по алфавиту. До 100 invoices, 500 events."""


def _fraction_503(pair):
    return Fraction(pair[0], pair[1])


def _allocate_503(values):
    floors = {key: value.numerator // value.denominator for key, value in values.items()}
    remainder = round(sum(values.values(), Fraction())) - sum(floors.values())
    order = sorted(values, key=lambda key: (-(values[key] - floors[key]), key))
    for key in order[:remainder]:
        floors[key] += 1
    return floors


def _couple_503(exact, row_totals, column_totals):
    rows, columns = sorted(row_totals), sorted(column_totals)
    floors = {key: value.numerator // value.denominator for key, value in exact.items()}
    if sum(row_totals.values()) != sum(column_totals.values()):
        return {"feasible": False, "reason": "total"}
    fractional = [key for key in sorted(exact) if exact[key] != floors[key]]
    row_need = {row: row_totals[row] - sum(floors[row, col] for col in columns) for row in rows}
    col_need = {col: column_totals[col] - sum(floors[row, col] for row in rows) for col in columns}
    if any(not 0 <= row_need[row] <= sum(key[0] == row for key in fractional) for row in rows) or any(
        not 0 <= col_need[col] <= sum(key[1] == col for key in fractional) for col in columns
    ):
        return {"feasible": False, "reason": "bounds"}
    source, sink = 0, len(rows) + len(columns) + 1
    graph = [[] for _ in range(sink + 1)]

    def edge(left, right, capacity, cost, tie):
        forward = [right, len(graph[right]), capacity, cost, tie]
        reverse = [left, len(graph[left]), 0, -cost, -tie]
        graph[left].append(forward)
        graph[right].append(reverse)
        return forward

    for index, row in enumerate(rows, 1):
        edge(source, index, row_need[row], Fraction(), 0)
    for index, col in enumerate(columns, len(rows) + 1):
        edge(index, sink, col_need[col], Fraction(), 0)
    decisions = {}
    for index, (row, col) in enumerate(fractional):
        remainder = exact[row, col] - floors[row, col]
        decisions[row, col] = edge(
            rows.index(row) + 1, len(rows) + columns.index(col) + 1, 1,
            1 - 2 * remainder, 1 << (len(fractional) - 1 - index),
        )
    for _ in range(sum(row_need.values())):
        distances, previous = [None] * len(graph), [None] * len(graph)
        distances[source] = (Fraction(), 0)
        for _iteration in range(len(graph) - 1):
            changed = False
            for left, outgoing in enumerate(graph):
                if distances[left] is None:
                    continue
                for index, item in enumerate(outgoing):
                    right, _, capacity, cost, tie = item
                    if not capacity:
                        continue
                    candidate = distances[left][0] + cost, distances[left][1] + tie
                    if distances[right] is None or candidate < distances[right]:
                        distances[right], previous[right] = candidate, (left, index)
                        changed = True
            if not changed:
                break
        if distances[sink] is None:
            return {"feasible": False, "reason": "coupling"}
        node = sink
        while node != source:
            left, index = previous[node]
            item = graph[left][index]
            item[2] -= 1
            graph[node][item[1]][2] += 1
            node = left
    allocation = {
        key: floor + int(key in decisions and decisions[key][2] == 0)
        for key, floor in floors.items()
    }
    error = sum(((allocation[key] - value) ** 2 for key, value in exact.items()), Fraction())
    return {"feasible": True, "allocation": allocation, "squared_error": error}


def _solve_503(data):
    result = []
    columns = sorted(data["column_totals"])
    converted = {}
    for invoice in sorted(data["invoices"], key=lambda row: row["id"]):
        lines = sorted(invoice["lines"], key=lambda row: row["id"])
        bases = {}
        for line in lines:
            price = _fraction_503(line["unit"]) * line["qty"]
            tax = _fraction_503(line["tax"])
            bases[line["id"]] = price / (1 + tax) if line["inclusive"] else price
        weight = sum((max(base, 0) for base in bases.values()), Fraction())
        discount = _fraction_503(invoice["discount"])
        shares = {
            key: discount * max(base, 0) / weight if weight else Fraction()
            for key, base in bases.items()
        }
        net = {key: (base - shares[key]) * invoice["scale"] for key, base in bases.items()}
        tax = {
            line["id"]: net[line["id"]] * _fraction_503(line["tax"])
            for line in lines
        }
        net_minor, tax_minor = _allocate_503(net), _allocate_503(tax)
        due = sum(net_minor.values()) + sum(tax_minor.values())
        for jurisdiction in columns:
            minor = sum(
                net_minor[line["id"]] + tax_minor[line["id"]]
                for line in lines if line["jurisdiction"] == jurisdiction
            )
            converted[invoice["id"], jurisdiction] = (
                Fraction(minor, invoice["scale"])
                * _fraction_503(invoice["fx"]) * data["base_scale"]
            )
        result.append({
            "id": invoice["id"], "currency": invoice["currency"], "due_minor": due,
            "lines": [
                {
                    "id": line["id"],
                    "discount": f"{shares[line['id']].numerator}/{shares[line['id']].denominator}",
                    "net_minor": net_minor[line["id"]], "tax_minor": tax_minor[line["id"]],
                    "gross_minor": net_minor[line["id"]] + tax_minor[line["id"]],
                }
                for line in lines
            ],
        })
    coupled = _couple_503(converted, data["row_totals"], data["column_totals"])
    if not coupled["feasible"]:
        return coupled
    for invoice in result:
        invoice["base_minor"] = data["row_totals"][invoice["id"]]
        invoice["allocations"] = [{
            "jurisdiction": col,
            "exact_base": (
                f"{converted[invoice['id'], col].numerator}/"
                f"{converted[invoice['id'], col].denominator}"
            ),
            "base_minor": coupled["allocation"][invoice["id"], col],
        } for col in columns]
    error = coupled["squared_error"]
    return {
        "feasible": True, "invoices": result, "base_total": sum(data["row_totals"].values()),
        "columns": [{"jurisdiction": col, "base_minor": data["column_totals"][col]} for col in columns],
        "squared_error": f"{error.numerator}/{error.denominator}",
    }


_PROMPT_503 = """Согласуй invoice ledger и jurisdiction settlement ledger:
точные налоги/скидки, FX и глобально оптимальное COUPLED ROUNDING.
Вход {base_scale,invoices,row_totals,column_totals}.
invoice={id,currency,scale,fx,discount,lines};
line={id,qty,unit,tax,inclusive,jurisdiction}. Все рациональные числа представлены [n,d],
d>0. qty — целое, в том числе отрицательное для credit line; unit>=0,
tax>=0, fx>0, scale/base_scale — положительные целые minor-units-per-major
(например 100). IDs уникальны в своей области, input порядок произвольный.

Для строки price=qty*unit. Если inclusive=true, исходный net base=price/(1+tax),
иначе base=price. discount — фиксированная сумма в major units invoice,
не процент. Распредели её ТОЧНО пропорционально max(base,0) по строкам;
неположительные base получают нулевую скидку. Сумма положительных base
гарантированно >=discount>=0; если она 0, discount=0. Никакого округления
скидки. net_exact=(base-discount_share)*scale, tax_exact=net_exact*tax.

Определение ALLOCATE({id:rational}): target=банковское округление
(nearest, halves to even) СУММЫ всех rational к целому, включая отрицательные.
Сначала каждому floor(rational), затем target-сумма floor единиц распределить
по убыванию fractional remainder rational-floor; при равенстве id по алфавиту.
ALLOCATE пустого mapping даёт пустой mapping. Выполни ALLOCATE отдельно
для net_exact и tax_exact внутри каждого invoice. gross_minor=их сумма,
due_minor=сумма gross_minor. Нельзя округлять строки независимо.

Теперь строки матрицы — invoices по id, столбцы — jurisdiction keys из
column_totals по алфавиту. Для каждой клетки (i,j) сложи УЖЕ округлённые
gross_minor строк invoice i с jurisdiction j и вычисли точную дробь
X[i,j]=эта_сумма/scale_i * fx_i * base_scale. Если таких lines нет, X=0.
row_totals задаёт внешние integer base-minor totals каждого invoice,
column_totals — внешние integer base-minor totals каждой jurisdiction.
Их ключи точны: row_totals содержит ровно все invoice.id, каждая line.jurisdiction
есть в column_totals; могут быть пустые столбцы. Значения могут быть отрицательными.

Найди целую матрицу Y: каждая клетка РОВНО floor(X) или ceil(X);
суммы строк РОВНО row_totals, суммы столбцов РОВНО column_totals.
Все ограничения должны выполняться ОДНОВРЕМЕННО. Среди допустимых матриц
минимизируй ТОЧНУЮ сумму (Y[i,j]-X[i,j])**2 по ВСЕМ клеткам. При равенстве
выбери лексикографически минимальный вектор всех Y в row-major порядке
(сначала invoice.id, внутри jurisdiction). Это глобальный optimum:
независимое округление строк/столбцов и greedy по fractional remainder
могут нарушить ограничения или дать худшую стоимость.

Если решения нет, вывод ТОЛЬКО {feasible:false,reason}, с приоритетом:
"total" — суммы row_totals и column_totals различаются;
"bounds" — хотя бы один row/column total вне [сумма floor X,сумма ceil X];
"coupling" — все эти простые проверки прошли, но общей матрицы не существует.
Не выдумывай балансирующие клетки и не изменяй внешние totals.

Если решение есть, вывод {feasible:true,invoices,columns,base_total,squared_error}.
invoices по id, каждый {id,currency,due_minor,base_minor,lines,allocations};
base_minor=row_totals[id], lines по id, каждый
{id,discount,net_minor,tax_minor,gross_minor}; allocations содержит ВСЕ
столбцы по jurisdiction, каждый {jurisdiction,exact_base,base_minor},
exact_base=X[i,j], base_minor=Y[i,j].
columns по jurisdiction, каждый {jurisdiction,base_minor:column_totals[j]}.
base_total=сумма row_totals. discount, exact_base и squared_error —
точные сокращённые строки "n/d", включая "0/1" и "n/1".
Даже нулевые клетки, tax и discount выводятся.
До8 invoices и8 jurisdictions, до30 lines/invoice; abs(n),d,abs(qty)<=10^12.
Пустые списки допустимы; все вычисления точные. При необходимости разрешено
sys.set_int_max_str_digits(0). Алгоритм должен укладываться в общий25s бюджет;
перебор всех 2^(число дробных клеток) для8×8 не подходит."""


def _norm_504(text):
    return unicodedata.normalize("NFC", text.strip().casefold())


def _solve_504(state):
    if state["version"] == 2:
        return {"ok": True, "state": copy.deepcopy(state)}

    def fail(code):
        return {"ok": False, "error": code, "state": copy.deepcopy(state)}

    orders, payments, aliases = state["orders"], state["payments"], state["aliases"]
    if len({row["id"] for row in orders}) != len(orders):
        return fail("duplicate-order")
    if len({row["id"] for row in payments}) != len(payments):
        return fail("duplicate-payment")
    if any(not _norm_504(row["customer"]) for row in orders) or any(
        not _norm_504(row[key]) for row in aliases for key in ("left", "right")
    ):
        return fail("blank-name")
    if any(row["total"] < 0 for row in orders):
        return fail("negative-total")
    order_map = {row["id"]: row for row in orders}
    if any(row["order_id"] not in order_map for row in payments):
        return fail("orphan-payment")
    parent = {}

    def find(key):
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for row in aliases:
        left = (row["tenant"], _norm_504(row["left"]))
        right = (row["tenant"], _norm_504(row["right"]))
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)
    group_orders = {}
    for row in orders:
        root = find((row["tenant"], _norm_504(row["customer"])))
        group_orders.setdefault(root, []).append(row)
    totals = {key: 0 for key in order_map}
    for row in payments:
        totals[row["order_id"]] += row["amount"]
    if any(not 0 <= totals[key] <= row["total"] for key, row in order_map.items()):
        return fail("payment-balance")
    customers = []
    customer_ids = {}
    for root, members in group_orders.items():
        names = sorted(name for (tenant, name) in parent if find((tenant, name)) == root)
        customer_id = min(row["id"] for row in members)
        customers.append({"id": customer_id, "tenant": root[0], "name": names[0], "aliases": names})
        for row in members:
            customer_ids[row["id"]] = customer_id
    new_orders = [
        {
            "id": row["id"], "customer_id": customer_ids[row["id"]], "total": row["total"],
            "paid": totals[row["id"]],
            "status": "paid" if totals[row["id"]] == row["total"] else (
                "unpaid" if totals[row["id"]] == 0 else "partial"
            ),
        }
        for row in sorted(orders, key=lambda row: row["id"])
    ]
    return {"ok": True, "state": {
        "version": 2,
        "customers": sorted(customers, key=lambda row: row["id"]),
        "orders": new_orders,
        "payments": sorted(copy.deepcopy(payments), key=lambda row: row["id"]),
    }}


_PROMPT_504 = """Мигрируй сериализованную relational database v1→v2 атомарно.
JSON-вход — state. v1={version:1,orders,payments,aliases}.
order={id:positive int,tenant:string,customer:string,total:int};
payment={id:positive int,order_id:int,amount:int}, amount может быть <0;
alias={tenant,left,right}. Tenant сравнивается точно. Нормализация customer
и обеих сторон alias: strip()→casefold()→NFC Unicode.
alias — симметричное отношение эквивалентности ВНУТРИ tenant, нужно
транзитивное замыкание; циклы допустимы. Одинаковые нормализованные имена
в разных tenants НЕ объединяются.

Сначала ошибки с абсолютным приоритетом в порядке:
duplicate-order (повтор order.id), duplicate-payment (повтор payment.id),
blank-name (пустое нормализованное имя в order либо alias),
negative-total, orphan-payment (нет order_id), payment-balance (СУММА всех
amount одного order вне [0,total]). Для ошибки вернуть
{ok:false,error:код,state:НЕИЗМЕНЁННЫЙ исходный объект}; не частичную v2.
Не проверяй balance после каждого payment: важен окончательный net.

Для успеха v2 содержит ТОЛЬКО version:2,customers,orders,payments.
Один customer на equivalence class с хотя бы одним order.
customer.id=min order.id этой группы (НЕ ordinal), tenant исходный,
aliases — все нормализованные имена класса, включая имена только из aliases,
по алфавиту; name=минимальное из них. Неиспользованные классы отбрасываются.
customers по id. order v2={id,customer_id,total,paid,status}, по id.
paid=net payments; status paid при paid==total (включая 0==0),
unpaid при paid==0, иначе partial. payments сохраняются точно, по id.
Выход {ok:true,state:новая v2}. Если version=2, сразу вернуть
{ok:true,state:исходный объект} без перестановок и проверок: это повторный
запуск уже завершённой миграции. version только 1/2, до 5000 строк."""


def _solve_505(data):
    if data["version"] == 2:
        segments = copy.deepcopy(data["segments"])
    else:
        latest = {}
        for row in data["records"]:
            if row["recorded"] > data["cutoff"]:
                continue
            key = row["id"]
            if key not in latest or (row["recorded"], row["revision"]) > (
                latest[key]["recorded"], latest[key]["revision"]
            ):
                latest[key] = row
        rows = [row for row in latest.values() if not row["deleted"]]
        segments = []
        for entity in sorted({row["entity"] for row in rows}):
            selected = [row for row in rows if row["entity"] == entity]
            boundaries = sorted({
                edge for row in selected for edge in (row["start"], row["end"])
                if edge is not None
            })
            edges = [None, *boundaries, None]
            entity_segments = []
            for index in range(len(edges) - 1):
                left, right = edges[index], edges[index + 1]
                candidates = [
                    row for row in selected
                    if (row["start"] is None or left is not None and row["start"] <= left)
                    and (row["end"] is None or right is not None and row["end"] >= right)
                ]
                if not candidates:
                    continue
                winner = max(candidates, key=lambda row: (
                    row["priority"], row["recorded"], row["revision"], row["id"]
                ))
                if winner["value"] is None:
                    continue
                if entity_segments and entity_segments[-1]["end"] == left and (
                    entity_segments[-1]["value"] == winner["value"]
                ):
                    entity_segments[-1]["end"] = right
                    entity_segments[-1]["sources"] = sorted(
                        set(entity_segments[-1]["sources"]) | {winner["id"]}
                    )
                else:
                    entity_segments.append({
                        "entity": entity, "start": left, "end": right,
                        "value": winner["value"], "sources": [winner["id"]],
                    })
            segments.extend(entity_segments)
    answers = []
    for query in data["queries"]:
        matching = [
            row for row in segments
            if row["entity"] == query["entity"]
            and (row["start"] is None or row["start"] <= query["at"])
            and (row["end"] is None or query["at"] < row["end"])
        ]
        answers.append(matching[0]["value"] if matching else None)
    return {"version": 2, "segments": segments, "answers": answers}


_PROMPT_505 = """Мигрируй effective-dated legacy history к disjoint interval store.
v1-вход {version:1,cutoff,records,queries}. record={id,recorded,revision,
deleted,entity,start,end,priority,value}. id — logical record id; его
редакции могут менять entity и интервалы. recorded/revision/priority — целые.
start/end — целые или null: start=null означает -∞, end=null означает +∞.
Непустые интервалы [start,end); value — строка либо null (маскирующая запись).
deleted=true — удаление logical record; остальные его поля игнорируются.
Пары (id,recorded,revision) уникальны. queries=[{entity,at:integer}].

1. Отбрось records с recorded>cutoff. Для каждого id оставь максимум
(recorded,revision), затем отбрось deleted=true. Нельзя сначала отбрасывать
deleted или независимо группировать по entity.
2. В каждой точке entity выигрывает покрывающая строка с максимальным
(priority,recorded,revision,id), id сравнивается лексикографически.
value=null подавляет более слабые строки и оставляет дыру; отсутствие
кандидатов тоже дыра. Значения не протягиваются через дырки.
3. Собери максимальные непустые интервалы с постоянным ненулевым value.
Соседние интервалы равного value слей даже при смене победителя; sources —
отсортированный набор id, реально выигрывавших на непустых частях результата.
Проигравшие и null-строки в sources не входят.
Выход {version:2,segments,answers}; segments по (entity,start), -∞ первым;
segment={entity,start,end,value,sources}, бесконечные границы снова null.
answers в исходном порядке queries: значение из полученных segments либо null.

Для идемпотентного повторного запуска предусмотрен вход
{version:2,segments,queries}: segments уже каноничны, верни их без изменений
и только вычисли answers. До 200 records, 100 queries, координаты до 10^15."""


def _line_506(line):
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith(("\r", "\n")):
        return line[:-1], line[-1:]
    return line, ""


def _value_506(body):
    quote = None
    index = 0
    comment = len(body)
    while index < len(body):
        char = body[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in "#;" and (index == 0 or body[index - 1] in " \t"):
            comment = index
            break
        index += 1
    value_end = len(body[:comment].rstrip(" \t"))
    tail = body[:value_end]
    slashes = len(tail) - len(tail.rstrip("\\"))
    continued = comment == len(body) and quote is None and slashes % 2 == 1
    return value_end, continued


def _spans_506(text):
    lines = text.splitlines(keepends=True)
    section = ""
    index = 0
    offset = 0
    spans = []
    while index < len(lines):
        line = lines[index]
        body, _ = _line_506(line)
        header = re.fullmatch(r"[ \t]*\[([A-Za-z0-9_.-]+)\][ \t]*(?:[;#].*)?", body)
        if header:
            section = header[1].casefold()
        match = re.match(r"([ \t]*([A-Za-z0-9_.-]+)[ \t]*[:=])([ \t]*)(.*)", body)
        if not match:
            offset += len(line)
            index += 1
            continue
        rest = match[4]
        value_end, continued = _value_506(rest)
        start = offset + match.start(4)
        end = start + value_end
        if value_end == 0 and not continued:
            start = end = offset + match.end(1)
        start_section, key = section, match[2].casefold()
        while continued:
            offset += len(lines[index])
            index += 1
            body, _ = _line_506(lines[index])
            value_end, continued = _value_506(body)
            end = offset + value_end
        spans.append((start_section, key, start, end))
        offset += len(lines[index])
        index += 1
    return spans


def _solve_506(data):
    text = data["text"]
    counts = []
    for edit in data["edits"]:
        matches = [
            (start, end) for section, key, start, end in _spans_506(text)
            if section == edit["section"].casefold() and key == edit["key"].casefold()
        ]
        if edit["which"] != "all":
            n = edit["which"] - 1
            matches = matches[n:n + 1]
        counts.append(len(matches))
        replacement = json.dumps(edit["value"], ensure_ascii=False)
        for start, end in reversed(matches):
            text = text[:start] + replacement + text[end:]
    return {"text": text, "applied": counts}


_PROMPT_506 = """Выполни lossless редактирование набора INI по edit-plan.json.
Каждый job задаёт source (путь файла) и edits в порядке исполнения.
Секции/ключи состоят из ASCII букв, цифр, _ . -, сравниваются без регистра.
До первого заголовка section="". Заголовок — строка [section] с возможными
горизонтальными пробелами вокруг и комментарием #/; после ]. Секции и ключи
могут повторяться. Assignment: отступ, key, пробелы, = или :, пробелы, value.
Прочие строки сохраняются и не интерпретируются.

В value одинарные/двойные кавычки защищают # и ;. Внутри кавычек backslash
защищает следующий символ. Снаружи #/; начинается inline comment только
в начале value или после пробела/tab. Quoting сбрасывается на каждой
физической строке. Continuation: нет inline comment, на конце value после
удаления spaces/tabs нечётное число backslashes, и мы вне кавычек.
Тогда следующая физическая строка целиком принадлежит value, даже если
выглядит как header/assignment; continuation может повторяться.
Данные синтаксически корректны и continuation всегда имеет следующую строку.

edit={section,key,which,value}, which="all" или 1-based номер совпадения
во ВСЁМ текущем документе (повторные секции не сбрасывают счётчик).
Каждый edit заново разбирает результат предыдущего. Нет совпадений → 0.
Заменяй value span на json.dumps(value,ensure_ascii=False): всегда double
quotes, JSON escaping. Span начинается после пробелов за delimiter, кончается
перед завершающими spaces/tabs и inline comment на ПОСЛЕДНЕЙ строке.
Все промежуточные continuation строки входят в span и исчезают.
Prefix первой и suffix последней строки сохраняются ПОСИМВОЛЬНО, включая
CRLF/LF/CR, комментарии, EOF без newline. Если исходный value пустой
(только whitespace и/или comment), вставь новый value непосредственно
после delimiter; всё исходное whitespace остаётся suffix.

Выход answer.json={files:{source:полный изменённый text},audit:[...]}.
audit в порядке jobs, элемент {source,applied:[число замен на каждый edit]}.
Это подготовленные изменённые конфиги; исходные файлы read-only.
Нельзя использовать configparser с пересериализацией: формат тоже проверяется."""


def _expand_507(template, lookup):
    output = []
    index = 0
    while index < len(template):
        if template.startswith("$$", index):
            output.append("$")
            index += 2
        elif template.startswith("${", index):
            end = template.index("}", index + 2)
            token = template[index + 2:end]
            name, separator, fallback = token.partition(":-")
            exists, value = lookup(name)
            if not exists:
                if not separator:
                    raise ValueError("missing")
                value = fallback
            output.append(value)
            index = end + 1
        else:
            output.append(template[index])
            index += 1
    return "".join(output)


def _solve_507(data):
    settings = {}

    def visit(path, section, variables, active):
        if path in active:
            raise ValueError("include-cycle")
        if path not in data["files"]:
            raise ValueError("missing-file")
        variables = dict(variables)

        def early(name):
            namespace, key = name.split(":", 1)
            mapping = data["env"] if namespace == "ENV" else variables
            return key in mapping, mapping.get(key)

        for number, raw in enumerate(data["files"][path].splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("@let "):
                name, value = line[5:].split("=", 1)
                variables[name.strip()] = _expand_507(value.strip(), early)
            elif line.startswith("@include "):
                target = _expand_507(line[9:].strip(), early)
                included = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
                if target.startswith("/") or included == ".." or included.startswith("../"):
                    raise ValueError("path")
                visit(included, section, variables, active | {path})
            elif line.startswith("["):
                section = line[1:-1]
            else:
                key, value = line.split("=", 1)
                settings[(section, key.strip())] = (value.strip(), dict(variables), path, number)

    try:
        visit(data["entry"], "global", {}, set())
        resolved = {}

        def resolve(key, active):
            if key in resolved:
                return resolved[key]
            if key in active:
                raise ValueError("cycle")
            template, variables, _, _ = settings[key]

            def lookup(name):
                if name.startswith("ENV:"):
                    name = name[4:]
                    return name in data["env"], data["env"].get(name)
                if name.startswith("VAR:"):
                    name = name[4:]
                    return name in variables, variables.get(name)
                target = tuple(name.split(".", 1)) if "." in name else (key[0], name)
                if target not in settings:
                    return False, None
                return True, resolve(target, active | {key})

            resolved[key] = _expand_507(template, lookup)
            return resolved[key]

        values, origins = {}, {}
        for key in sorted(settings):
            values.setdefault(key[0], {})[key[1]] = resolve(key, set())
            row = settings[key]
            origins.setdefault(key[0], {})[key[1]] = f"{row[2]}:{row[3]}"
        return {"values": values, "origins": origins}
    except ValueError as exc:
        return {"error": str(exc)}


_PROMPT_507 = """Разреши несколько deployment-конфигураций со scoped INI includes.
В jobs.json jobs=[{id,entry,env}], файлы под config/; пути entry и include
относятся к workspace. Каждая строка после strip: пустая/#/; игнорируется;
[section] меняет секцию; key=value сохраняет stripped value template;
@let NAME=template задаёт локальную переменную; @include path-template
включает файл. Имена section/key/NAME — ASCII identifier без точки,
регистрозависимы. Начальная section="global".

Include получает текущую section и КОПИЮ переменных вызывающего файла.
Его смена section/@let не выходит наружу, но записи settings общие.
После возвращения вызывающий продолжает со своей section и variables.
Повторное включение допустимо, cycle только в активном стеке.
Путь относителен директории включающего файла; . и .. нормализуются.
Абсолютный путь или выход выше workspace → error "path";
отсутствие файла → "missing-file"; цикл → "include-cycle".

@let и include template вычисляются НЕМЕДЛЕННО и содержат только
${VAR:NAME}/${ENV:NAME}; let видит предыдущее значение своего NAME.
Assignment сохраняет SNAPSHOT variables на момент записи. Последнее
assignment (section,key) побеждает. Его template разрешается ПОСЛЕ чтения
всех includes: ${key} ссылается на итоговый key своей секции,
${section.key} — другой секции, ${VAR:NAME} — captured variables,
${ENV:NAME} — job.env. Ссылки на settings рекурсивны.
Везде ${ref:-fallback} даёт literal fallback ТОЛЬКО если непосредственно
указанный ref отсутствует; ошибку внутри существующего ref не подавляет.
Fallback не содержит } и не интерполируется. $$ превращается в literal $
(поэтому $${x} → literal ${x}); вставленные значения повторно не разбирай.
Пустая существующая строка НЕ вызывает fallback. Остальной $ буквальный.
Все ${...} синтаксически корректны; вложенных ${...} нет.

Ошибки парсинга/include/@let определяются в порядке чтения. Затем разрешай
итоговые settings по (section,key); первая ошибка missing reference даёт
"missing", рекурсивный цикл settings — "cycle". Для ошибочного job только
{error:код}. Для успеха {values:{section:{key:string}},origins:{section:
{key:"path:physical_line_number"}}}. origins указывают победившие assignment,
номера строк с 1; entry пути канонические. Выход answer.json={job.id:результат}.
Исходники read-only; отдельно взятые jobs не делят settings или variables."""


def _solve_508(data):
    files = dict(data["files"])
    audit = []

    def check_guards(state, guards):
        return all(
            (path not in state if expected is None else path in state and _sha(state[path]) == expected)
            for path, expected in guards.items()
        )

    for batch in data["batches"]:
        changed = dict(files)
        error, failed_op = None, None
        if not check_guards(changed, batch["guards"]):
            error, failed_op = "guard", 0
        else:
            for index, op in enumerate(batch["ops"], 1):
                path = op["path"]
                kind = op["op"]
                if kind == "create":
                    if path in changed:
                        error = "exists"
                    else:
                        changed[path] = op["text"]
                elif path not in changed:
                    error = "missing"
                elif kind == "delete":
                    del changed[path]
                else:
                    raw = changed[path].encode()
                    spans = op["spans"]
                    for span in spans:
                        a, b = span["start"], span["end"]
                        if not 0 <= a <= b <= len(raw):
                            error = "range"
                            break
                        try:
                            raw[:a].decode()
                            raw[:b].decode()
                        except UnicodeDecodeError:
                            error = "boundary"
                            break
                    ordered = sorted(spans, key=lambda s: (s["start"], s["end"]))
                    if error is None and any(
                        b["start"] < a["end"] or b["start"] == a["start"]
                        for a, b in zip(ordered, ordered[1:], strict=False)
                    ):
                        error = "overlap"
                    if error is None and any(
                        raw[s["start"]:s["end"]] != s["old"].encode() for s in ordered
                    ):
                        error = "preimage"
                    if error is None:
                        for span in reversed(ordered):
                            raw = raw[:span["start"]] + span["new"].encode() + raw[span["end"]:]
                        changed[path] = raw.decode()
                if error:
                    failed_op = index
                    break
            if error is None and not check_guards(changed, batch["post"]):
                error, failed_op = "post", len(batch["ops"]) + 1
        if error is None:
            files = changed
        audit.append({
            "id": batch["id"], "committed": error is None,
            "failed_op": failed_op, "error": error,
        })
    return {"files": files, "hashes": {path: _sha(text) for path, text in files.items()}, "audit": audit}


_PROMPT_508 = """Реализуй content-hash guarded atomic multi-file edits.
Вход {files:{path:text},batches:[{id,guards,ops,post}]}. Paths — непрозрачные
уникальные строки, text — Unicode, хеши SHA256 UTF-8 нижний hex.
guards/post mapping path→hash или null: hash требует существующий файл
с этим хешем, null требует ОТСУТСТВИЕ файла (пустой существующий не подходит).
Для каждого batch сначала guards, затем ops на приватной копии по порядку,
затем post. Любая ошибка полностью откатывает batch; следующие batch идут
по последнему committed состоянию. Успешный batch меняет files атомарно.

ops: create {op,path,text} требует отсутствие; delete {op,path} — наличие;
edit {op,path,spans} — наличие, spans=[{start,end,old,new}].
start/end — смещения в UTF-8 BYTES ТЕКУЩЕГО файла до этой edit, [start,end).
Все spans одной edit одновременны; следующих ops — уже новая версия.
Проверки edit: сначала spans в исходном порядке: 0<=start<=end<=byte_size,
иначе range; обе границы должны быть UTF-8 boundaries, иначе boundary.
Затем sorted spans по (start,end): соседние конфликтуют, если
next.start<previous.end ИЛИ next.start==previous.start, иначе допустимы.
Это даёт overlap; insertion на конце replacement разрешён, на начале нет;
две insertion на одной позиции запрещены. Затем проверь все old точно по
исходным byte slices; несовпадение → preimage. Наконец примени все spans.
Пустой spans допустим. new/old — Unicode text; offsets не пересчитываются.

Выход {files,hashes,audit}; hashes для всех итоговых files.
audit по batch: {id,committed,failed_op,error}; успех → null/null.
Failure: guard/0, exists или missing/1-based op index, range/boundary/
overlap/preimage/1-based op index, post/(len(ops)+1).
create на существующем → exists; edit/delete отсутствующего → missing.
Верни только первую ошибку batch, даже если post тоже не подходит.
До 40 batches по 50 ops, 2 MB суммарного текста."""


def _solve_509(data):
    state = dict(data["state"])
    versions = {key: 0 for key in state}
    transactions = {}
    reads, completions = [], []
    for index, event in enumerate(data["events"], 1):
        kind, name = event["op"], event["tx"]
        if kind == "begin":
            transactions[name] = {
                "snapshot": dict(state), "versions": dict(versions), "writes": {}, "savepoints": [],
            }
            continue
        tx = transactions[name]
        if kind in ("read", "add"):
            key = event["key"]
            value = tx["writes"].get(key, tx["snapshot"].get(key))
            if kind == "read":
                reads.append({"event": index, "tx": name, "key": key,
                              "exists": value is not None, "value": value})
            else:
                tx["writes"][key] = (value if value is not None else 0) + event["value"]
        elif kind == "set":
            tx["writes"][event["key"]] = event["value"]
        elif kind == "delete":
            tx["writes"][event["key"]] = None
        elif kind == "save":
            tx["savepoints"].append((event["name"], dict(tx["writes"])))
        elif kind in ("rollback", "release"):
            position = next(i for i, pair in enumerate(tx["savepoints"]) if pair[0] == event["name"])
            if kind == "rollback":
                tx["writes"] = dict(tx["savepoints"][position][1])
                tx["savepoints"] = tx["savepoints"][:position + 1]
            else:
                tx["savepoints"] = tx["savepoints"][:position]
        elif kind == "abort":
            completions.append({"event": index, "tx": name, "status": "aborted", "conflicts": []})
            del transactions[name]
        else:
            conflicts = sorted(
                key for key in tx["writes"] if versions.get(key, 0) != tx["versions"].get(key, 0)
            )
            if not conflicts:
                for key, value in tx["writes"].items():
                    if value is None:
                        state.pop(key, None)
                    else:
                        state[key] = value
                    versions[key] = versions.get(key, 0) + 1
            completions.append({
                "event": index, "tx": name,
                "status": "conflict" if conflicts else "committed", "conflicts": conflicts,
            })
            del transactions[name]
    return {"state": state, "versions": versions, "reads": reads, "completions": completions}


_PROMPT_509 = """Исполни transaction schedule со snapshot isolation и savepoints.
Вход {state:{key:integer},events}. Events в указанном порядке; каждый
{op,tx,...}. begin создаёт snapshot committed values И per-key versions.
Начальная version каждого существующего ключа 0; отсутствующего тоже 0.
Tx id начинается только один раз; все последующие команды активной tx;
к концу все tx завершены. Read-your-writes; чужие commits после begin
не видны чтениям. op read {key}; set {key,value:int}; add {key,value:int}
прибавляет к видимому значению (отсутствие=0); delete {key} сохраняет write
intent удаления, даже отсутствующего. Существующие значения никогда null.

save {name} запоминает write buffer, имена активных savepoints уникальны.
rollback {name} восстанавливает buffer выбранной точки, удаляет более
поздние savepoints, СОХРАНЯЕТ выбранную. release {name} удаляет выбранную
и все более поздние точки, но writes не меняет. Названная точка существует.
Savepoint не обновляет snapshot; read audit никогда не откатывается.
abort закрывает tx без записи. commit сравнивает ТОЛЬКО версии ключей
нынешнего write buffer с версиями begin. Если хотя бы одна изменилась,
вся tx откатывается и закрывается со status=conflict, conflicts=sorted keys.
Иначе commit атомарно применяет writes и увеличивает version КАЖДОГО
записанного ключа на1, даже same-value set и deletion отсутствующего.
Version удалённого ключа сохраняется как tombstone. Read-set не проверяй:
write skew разрешён, read-only commit всегда успешен.

Выход {state,versions,reads,completions}. versions включает исходные ключи
и все когда-либо committed-written, даже уже отсутствующие.
reads в порядке событий [{event,tx,key,exists,value}], event=1-based позиция
во ВСЁМ events, value=null если отсутствует. completions для commit/abort
[{event,tx,status,conflicts}], status committed/conflict/aborted, conflicts=[]
кроме conflict. До 2000 events, 100 одновременно активных tx."""


def _solve_510(data):
    events = {}
    for index, row in enumerate(data["records"]):
        if row["run"] != data["run"] or row["observed"] > data["cutoff"]:
            continue
        key = row["shard"], row["seq"]
        if key not in events or (row["observed"], index) > events[key][:2]:
            events[key] = row["observed"], index, row
    attempts = {}
    for _, _, row in events.values():
        key = row["test"], row["attempt"]
        rank = row["revision"], row["observed"], row["shard"], row["seq"]
        if key not in attempts or rank > attempts[key][0]:
            attempts[key] = rank, row
    selected = [pair[1] for pair in attempts.values() if pair[1]["status"] != "withdrawn"]
    expected = set(data["expected"])
    unexpected = sorted({row["test"] for row in selected} - expected)
    selected = [row for row in selected if row["test"] in expected]
    by_test, flaky, retried, infra = [], [], [], []
    counts = dict.fromkeys(("passed", "failed", "skipped", "infra", "running", "missing"), 0)
    for test in sorted(expected):
        rows = [row for row in selected if row["test"] == test]
        if len(rows) > 1:
            retried.append(test)
        if any(row["status"] == "infra" for row in rows):
            infra.append(test)
        eligible = [row for row in rows if row["status"] != "cancelled"]
        final = max(eligible, key=lambda row: row["attempt"]) if eligible else None
        if final and final["status"] == "passed" and any(
            row["status"] == "failed" and row["attempt"] < final["attempt"] for row in rows
        ):
            flaky.append(test)
        status = final["status"] if final else "missing"
        counts[status] += 1
        by_test.append({
            "test": test, "status": status, "attempt": final["attempt"] if final else None,
            "duration_ms": final["duration_ms"] if final else 0,
        })
    slowest = sorted(
        (row for row in by_test if row["status"] in ("passed", "failed", "skipped")),
        key=lambda row: (-row["duration_ms"], row["test"]),
    )[:3]
    return {
        "total": len(expected), "counts": counts, "tests": by_test,
        "attempts_total": len(selected), "duration_ms": sum(row["duration_ms"] for row in selected),
        "flaky": flaky, "retried": retried, "infra_tests": infra, "unexpected": unexpected,
        "slowest": [{"test": row["test"], "duration_ms": row["duration_ms"]} for row in slowest],
    }


_PROMPT_510 = """Собери test shard report с retries, исправлениями и cutoff.
Вход {run,cutoff,expected:[test IDs],records}. expected может повторяться.
record={run,shard,seq,observed,test,attempt,revision,status,duration_ms}.
seq/observed/revision — целые >=0; attempt>=1; duration_ms>=0.
status passed/failed/skipped/infra/running/cancelled/withdrawn.
shard/test/run — строки. Вход records — журнал доставки, его порядок важен
ТОЛЬКО для последнего tie-break первого шага.

Вычисляй в таком порядке:
1. Отбрось чужой run и observed>cutoff (cutoff включительно).
2. Дедуп event identity (shard,seq): максимум (observed,0-based input index).
Новая доставка может исправлять ЛЮБЫЕ поля события, включая test/attempt.
3. Для каждого (test,attempt) выбери максимум
(revision,observed,shard,seq); сравнения строк лексикографические.
4. Победитель withdrawn полностью убирает attempt; старую revision не
восстанавливай. Все остальные победители — surviving logical attempts.
5. unexpected — test IDs surviving attempts, отсутствующие в unique expected,
по алфавиту. Далее статистика ТОЛЬКО expected; unexpected её не меняют.

Для каждого expected final — surviving attempt с максимальным attempt,
ИСКЛЮЧАЯ cancelled. Поэтому более поздний infra/running скрывает предыдущий
passed, а cancelled — нет. Если final нет, status=missing, attempt=null,
duration_ms=0; иначе поля из final.
counts имеет ВСЕ ключи passed,failed,skipped,infra,running,missing.
total=unique expected count. attempts_total и duration_ms считают ВСЕ
surviving attempts expected, ВКЛЮЧАЯ cancelled/infra/running, по победившей
revision, без дубликатов. retried — >=2 surviving attempts, включая cancelled.
flaky — final passed и есть более ранний surviving failed. infra_tests —
есть любой surviving infra. Все списки test IDs по алфавиту.
slowest — top3 ТОЛЬКО finals passed/failed/skipped по (-duration_ms,test),
элементы {test,duration_ms}; отменённые/infra/running/missing не входят.

Выход {total,counts,tests,attempts_total,duration_ms,flaky,retried,infra_tests,
unexpected,slowest}. tests содержит по одной записи для каждого уникального
ID из expected и отсортирован ЛЕКСИКОГРАФИЧЕСКИ по полю test,
НЕЗАВИСИМО от исходного порядка expected. Например expected=["z","a","z"]
даёт tests=[{test:"a",...},{test:"z",...}]. Элементы tests —
{test,status,attempt,duration_ms}. До 200 expected и 5000 records."""


def _solve_511(data):
    tests = sorted(data["tests"], key=lambda row: row["id"])
    index = {row["id"]: i for i, row in enumerate(tests)}
    n = len(tests)
    forbidden = sum(1 << i for i, row in enumerate(tests) if row["known_good"])
    failures = []
    for shard in data["shards"]:
        mask = sum(1 << index[name] for name in set(shard["tests"]))
        if shard["status"] == "passed":
            forbidden |= mask
        elif shard["status"] == "failed":
            failures.append(mask)
    costs = [0] * (1 << n)
    cardinalities = [0] * (1 << n)
    best = None
    hypotheses = []
    for mask in range(1 << n):
        if mask:
            bit = mask & -mask
            rest = mask ^ bit
            costs[mask] = costs[rest] + tests[bit.bit_length() - 1]["fault_cost"]
            cardinalities[mask] = cardinalities[rest] + 1
        if mask & forbidden or any(not mask & fail for fail in failures):
            continue
        score = costs[mask], cardinalities[mask]
        if best is None or score < best:
            best, hypotheses = score, [mask]
        elif score == best:
            hypotheses.append(mask)
    if best is None:
        return {"consistent": False}

    def names(mask):
        return [row["id"] for i, row in enumerate(tests) if mask & (1 << i)]

    hypotheses.sort(key=names)
    possible, certain = 0, (1 << n) - 1
    for mask in hypotheses:
        possible |= mask
        certain &= mask
    allowed = sum(1 << i for i, row in enumerate(tests) if row["can_rerun"])
    signatures = {}
    for hindex, mask in enumerate(hypotheses):
        signatures.setdefault(mask & allowed, []).append(hindex)
    unresolved = sorted(group for group in signatures.values() if len(group) > 1)
    plan, rerun_cost = None, None
    if not unresolved:
        differences = []
        for a, first in enumerate(hypotheses):
            for second in hypotheses[a + 1:]:
                differences.append(first ^ second)
        best_plan = None
        for mask in range(1 << n):
            if mask & ~allowed:
                continue
            cost = sum(row["rerun_cost"] for i, row in enumerate(tests) if mask & (1 << i))
            score = cost, mask.bit_count(), names(mask)
            if best_plan is not None and score >= best_plan:
                continue
            if all(mask & difference for difference in differences):
                best_plan = score
        rerun_cost, _, plan = best_plan
    return {
        "consistent": True, "fault_cost": best[0], "fault_count": best[1],
        "hypotheses": [names(mask) for mask in hypotheses],
        "certain_bad": names(certain), "possibly_bad": names(possible),
        "rerun": plan, "rerun_cost": rerun_cost, "unresolved": unresolved,
    }


_PROMPT_511 = """Найди точные минимальные диагнозы CI и минимальный rerun plan.
Вход tests=[{id,fault_cost,rerun_cost,known_good,can_rerun}], shards=[{tests:
[id],status}]. status passed/failed/infra. Все IDs существуют и уникальны
в tests, но могут повторяться внутри shard. fault_cost — positive int,
rerun_cost — nonnegative int. Не более16 tests.

Гипотеза H — множество неисправных tests. known_good не входит в H.
passed shard требует H∩shard=∅; failed — H∩shard≠∅; infra не накладывает
ограничений. Пустой failed shard делает систему противоречивой.
Оптимальные гипотезы минимизируют ЛЕКСИКОГРАФИЧЕСКИ
(сумма fault_cost,число faulty tests). Нужны ВСЕ такие гипотезы, не один
greedy hitting set. Каждый список ID отсортируй, hypotheses — по самим
спискам лексикографически. Гарантируется <=128 оптимальных гипотез.
Если допустимых H нет, вывод ТОЛЬКО {consistent:false}.

Один rerun test даёт идеальный бинарный ответ: faulty iff test∈H.
План — фиксированное НЕадаптивное множество tests с can_rerun=true.
Оно обязано давать разные векторы ответов для любых двух оптимальных H.
Среди таких планов минимизируй (сумма rerun_cost,число tests,sorted ID list).
Если полное различение невозможно, rerun и rerun_cost=null;
unresolved — группы 0-based индексов hypotheses, неразличимых даже при
rerun ВСЕХ доступных tests, только группы размера>1, внутри индексы
по возрастанию, группы лексикографически. Иначе unresolved=[].
При одной гипотезе нужен пустой план стоимость0.

Для успеха выход {consistent:true,fault_cost,fault_count,hypotheses,
certain_bad,possibly_bad,rerun,rerun_cost,unresolved}; certain_bad —
пересечение всех оптимальных H, possibly_bad — объединение, оба по алфавиту.
До80 shards; задача требует точного оптимума, приближённые ответы не подходят."""


def _invoice_503(name="i", lines=None, **kwargs):
    return {
        "id": name, "currency": "USD", "scale": 100, "fx": [1, 1], "discount": [0, 1],
        "lines": lines or [], **kwargs,
    }


def _item_503(name="a", unit=(1, 1), qty=1, tax=(0, 1), inclusive=False, jurisdiction="j0"):
    return {"id": name, "unit": list(unit), "qty": qty, "tax": list(tax),
            "inclusive": inclusive, "jurisdiction": jurisdiction}


def _matrix_case_503(numerators, row_totals, column_totals, denominators=10):
    denominators = [denominators] * len(numerators) if isinstance(denominators, int) else denominators
    return {
        "base_scale": 1,
        "row_totals": {f"i{i}": value for i, value in enumerate(row_totals)},
        "column_totals": {f"j{j}": value for j, value in enumerate(column_totals)},
        "invoices": [
            _invoice_503(f"i{i}", [
                _item_503(f"line{j}", (abs(value), 1), qty=-1 if value < 0 else 1,
                          jurisdiction=f"j{j}")
                for j, value in enumerate(row) if value
            ], scale=1, fx=[1, denominators[i]], currency=f"CUR{i % 3}")
            for i, row in enumerate(numerators)
        ],
    }


def _balanced_matrix_case_503(numerators, denominators=10):
    denominators = [denominators] * len(numerators) if isinstance(denominators, int) else denominators
    chosen = []
    for i, row in enumerate(numerators):
        values = []
        for j, numerator in enumerate(row):
            value = Fraction(numerator, denominators[i])
            floor = value.numerator // value.denominator
            values.append(floor + int(value != floor and (i + j) % 3 == 0))
        chosen.append(values)
    return _matrix_case_503(
        numerators, [sum(row) for row in chosen],
        [sum(row[j] for row in chosen) for j in range(len(chosen[0]))], denominators,
    )


def _v1_504(orders=None, payments=None, aliases=None):
    return {"version": 1, "orders": orders or [], "payments": payments or [], "aliases": aliases or []}


def _order_504(number=1, customer="Alice", tenant="t", total=100):
    return {"id": number, "customer": customer, "tenant": tenant, "total": total}


def _row_505(name="r", entity="x", start=None, end=None, value="A", **kwargs):
    return {
        "id": name, "entity": entity, "start": start, "end": end, "value": value,
        "recorded": 1, "revision": 1, "priority": 0, "deleted": False, **kwargs,
    }


def _history_505(records=None, queries=None, cutoff=10):
    return {"version": 1, "records": records or [], "queries": queries or [], "cutoff": cutoff}


def _batch_508(name, ops=None, guards=None, post=None):
    return {"id": name, "ops": ops or [], "guards": guards or {}, "post": post or {}}


def _edit_508(path, spans):
    return {"op": "edit", "path": path, "spans": [
        {"start": a, "end": b, "old": old, "new": new} for a, b, old, new in spans
    ]}


def _event_509(tx, op, **kwargs):
    return {"tx": tx, "op": op, **kwargs}


def _record_510(test="a", attempt=1, status="passed", **kwargs):
    return {
        "run": "r", "shard": "s", "seq": attempt, "observed": attempt,
        "test": test, "attempt": attempt, "revision": 0, "status": status, "duration_ms": 10,
        **kwargs,
    }


def _report_510(records=None, expected=None, cutoff=100):
    return {"run": "r", "cutoff": cutoff, "expected": ["a"] if expected is None else expected,
            "records": records or []}


def _test_511(name, fault=1, rerun=1, good=False, available=True):
    return {"id": name, "fault_cost": fault, "rerun_cost": rerun,
            "known_good": good, "can_rerun": available}


def _shard_511(tests, status="failed"):
    return {"tests": list(tests), "status": status}


_INPUTS = {
    501: [
        {"entries": [{"path": "a", "kind": "file", "text": "A"},
                     {"path": "b", "kind": "hardlink", "target": "a"}]},
        {"entries": [{"path": "X/../café", "kind": "file", "text": "é"},
                     {"path": "CAFE\u0301", "kind": "file", "text": "other"}]},
        {"entries": []},
        {"entries": [{"path": "../a", "kind": "file", "text": ""}]},
        {"entries": [{"path": "a/..", "kind": "dir"}]},
        {"entries": [{"path": "x", "kind": "symlink", "target": "somewhere"},
                     {"path": "x/a", "kind": "file", "text": "a"}]},
        {"entries": [{"path": "three", "kind": "hardlink", "target": "./Two"},
                     {"path": "one", "kind": "file", "text": "hello"},
                     {"path": "TWO", "kind": "hardlink", "target": "ONE"}]},
        {"entries": [{"path": "a", "kind": "hardlink", "target": "b"},
                     {"path": "b", "kind": "hardlink", "target": "a"}]},
        {"entries": [{"path": "z", "kind": "hardlink", "target": "absent"},
                     {"path": "a", "kind": "hardlink", "target": "../escape"}]},
        {"entries": [{"path": "d", "kind": "dir"},
                     {"path": "l", "kind": "hardlink", "target": "d"}]},
        {"entries": [{"path": "d/文.txt", "kind": "file", "text": "λ\n"},
                     {"path": "d/empty", "kind": "file", "text": ""},
                     {"path": "L", "kind": "symlink", "target": "../D/文.txt"},
                     {"path": "explicit", "kind": "dir"}]},
        {"entries": [{"path": "s", "kind": "file", "text": "same"},
                     {"path": "t", "kind": "file", "text": "same"},
                     {"path": "u", "kind": "hardlink", "target": "s"}]},
        {"entries": [{"path": "Straße/a", "kind": "file", "text": "a"},
                     {"path": "STRASSE", "kind": "dir"},
                     {"path": "e\u0301/./b", "kind": "file", "text": "b"}]},
        {"entries": [{"path": "bad", "kind": "hardlink", "target": "missing"},
                     {"path": "X", "kind": "file", "text": ""},
                     {"path": "x/a", "kind": "file", "text": ""},
                     {"path": "/absolute", "kind": "dir"}]},
        {"entries": [{"path": "symlink", "kind": "symlink", "target": "/etc/does-not-exist"},
                     {"path": "h", "kind": "hardlink", "target": "symlink"}]},
        {"entries": [{"path": "a//b/../c", "kind": "dir"},
                     {"path": "a/c/z", "kind": "file", "text": "\0☃\r\n"}]},
    ],
    503: [
        _matrix_case_503([[5, 5], [5, 5]], [1, 1], [1, 1]),
        _matrix_case_503([[90, 80], [85, 10]], [1, 1], [1, 1], 100),
        {"base_scale": 1, "invoices": [], "row_totals": {}, "column_totals": {}},
        _matrix_case_503([[5]], [0], [1]),
        _matrix_case_503([[5]], [2], [2]),
        _matrix_case_503([[5, 0, 0], [5, 0, 0], [0, 5, 5]], [1, 1, 0], [1, 1, 0]),
        _matrix_case_503([[-15, 5], [5, -15]], [-1, -1], [-1, -1]),
        _matrix_case_503([[10, 0], [0, -10]], [1, -1], [1, -1]),
        _matrix_case_503([[500000000001, 500000000000], [500000000000, 500000000000]],
                         [1, 1], [1, 1], 1000000000000),
        {"base_scale": 100, "row_totals": {"i": 1040},
         "column_totals": {"home": 1160, "away": -120},
         "invoices": [_invoice_503(lines=[
             _item_503("sale", (12, 1), tax=(1, 5), inclusive=True, jurisdiction="home"),
             _item_503("return", (1, 1), qty=-1, tax=(1, 5), jurisdiction="away"),
         ], discount=[1, 3])]},
        {"base_scale": 100, "row_totals": {"i": 0}, "column_totals": {"x": 0, "y": 0},
         "invoices": [_invoice_503(lines=[
             _item_503("b", (1, 200), jurisdiction="y"),
             _item_503("a", (1, 200), jurisdiction="x"),
         ], fx=[1, 2])]},
        {"base_scale": 100, "row_totals": {"i": 0}, "column_totals": {"j0": 0},
         "invoices": [_invoice_503(lines=[_item_503("net", (1, 200), tax=(1, 1))])]},
        {"base_scale": 100, "row_totals": {"i": 0}, "column_totals": {"j0": 0},
         "invoices": [_invoice_503(lines=[
             _item_503("a", (1, 1), tax=(1, 2)), _item_503("b", (3, 1), tax=(2, 3)),
         ], discount=[4, 1])]},
        _balanced_matrix_case_503([[6, 8, -2, 4], [3, -17, 9, 2], [10, 5, 11, -5]]),
        _matrix_case_503([[5] * 8 for _ in range(8)], [4] * 8, [4] * 8),
        _balanced_matrix_case_503([[(13 * i + 7 * j) % 29 - 14 for j in range(8)]
                                   for i in range(8)], 17),
        _balanced_matrix_case_503([[2, 1], [3, -8], [9, 8]], [3, 7, 11]),
        _matrix_case_503([[5] * 8 for _ in range(8)], [8] * 8, [8] * 8),
        _matrix_case_503([[], []], [0, 0], []),
    ],
    504: [
        _v1_504([_order_504(4, " Alice "), _order_504(9, "ALICE")],
                [{"id": 1, "order_id": 4, "amount": 40}]),
        _v1_504([_order_504(7, "B"), _order_504(2, "C")], aliases=[
            {"tenant": "t", "left": "A", "right": "B"},
            {"tenant": "t", "left": "b", "right": "C"},
        ]),
        _v1_504(),
        _v1_504([_order_504(1, "A", "tenant-a"), _order_504(2, "A", "tenant-b")]),
        _v1_504([_order_504(1, "é"), _order_504(2, " E\u0301 ")]),
        _v1_504([_order_504(), _order_504()]),
        _v1_504([_order_504()], [{"id": 1, "order_id": 1, "amount": 5},
                               {"id": 1, "order_id": 1, "amount": 5}]),
        _v1_504([_order_504(customer=" \t")]),
        _v1_504([_order_504(total=-1)]),
        _v1_504([_order_504()], [{"id": 4, "order_id": 2, "amount": 0}]),
        _v1_504([_order_504()], [{"id": 1, "order_id": 1, "amount": 101}]),
        _v1_504([_order_504()], [{"id": 1, "order_id": 1, "amount": -1}]),
        _v1_504([_order_504()], [{"id": 2, "order_id": 1, "amount": -50},
                               {"id": 1, "order_id": 1, "amount": 150}]),
        _v1_504([_order_504(11, "C", total=0)], aliases=[
            {"tenant": "t", "left": "C", "right": "B"},
            {"tenant": "t", "left": "B", "right": "A"},
            {"tenant": "t", "left": "A", "right": "C"},
            {"tenant": "unused", "left": "x", "right": "y"},
        ]),
        {"version": 2, "customers": [], "orders": [], "payments": [], "opaque": {"keep": [3, 1]}},
        _v1_504([_order_504(1, " ", total=-1), _order_504(1)], aliases=[
            {"tenant": "t", "left": " ", "right": "B"},
        ]),
    ],
    505: [
        _history_505([_row_505(start=0, end=10)],
                     [{"entity": "x", "at": at} for at in (-1, 0, 9, 10)]),
        _history_505([_row_505("low", start=0, end=10),
                      _row_505("high", start=3, end=7, value=None, priority=1)],
                     [{"entity": "x", "at": at} for at in (2, 3, 6, 7)]),
        _history_505(),
        _history_505([_row_505()], [{"entity": "x", "at": -(10 ** 15)}]),
        _history_505([_row_505("a", end=0), _row_505("b", start=0)]),
        _history_505([_row_505("a", start=0, end=4, priority=3),
                      _row_505("b", start=2, end=6, value="B", priority=2)]),
        _history_505([_row_505("a", entity="old"),
                      _row_505("a", entity="new", revision=2)],
                     [{"entity": name, "at": 0} for name in ("old", "new")]),
        _history_505([_row_505("a"), _row_505("a", recorded=2, deleted=True)]),
        _history_505([_row_505("a"), _row_505("a", recorded=11, deleted=True)]),
        _history_505([_row_505("a", end=-3), _row_505("b", start=-1, value="B")]),
        _history_505([_row_505("a", start=0, end=2),
                      _row_505("z", start=1, end=3, value="B")]),
        _history_505([_row_505("a", start=0, end=2),
                      _row_505("b", start=2, end=5),
                      _row_505("loser", start=0, end=5, priority=-1)]),
        {"version": 2, "segments": [
            {"entity": "x", "start": None, "end": 4, "value": "", "sources": ["source"]},
        ], "queries": [{"entity": "x", "at": 3}, {"entity": "x", "at": 4}]},
        _history_505([
            _row_505(f"{i:03}", start=i, end=i + 10, value=str(i % 3), priority=i % 5,
                     recorded=i % 4, revision=i % 7) for i in range(100)
        ], [{"entity": "x", "at": i} for i in range(-1, 105, 3)]),
        _history_505([_row_505("a", recorded=1, revision=99, value="old"),
                      _row_505("a", recorded=2, revision=0, value="new")]),
    ],
    508: [
        {"files": {"a": "abc def"}, "batches": [
            _batch_508("one", [_edit_508("a", [(4, 7, "def", "D"), (0, 3, "abc", "A")])]),
        ]},
        {"files": {"a": "x", "b": "y"}, "batches": [
            _batch_508("bad", [_edit_508("a", [(0, 1, "x", "z")]),
                               _edit_508("b", [(0, 1, "WRONG", "q")])]),
            _batch_508("good", [_edit_508("a", [(0, 1, "x", "ok")])]),
        ]},
        {"files": {}, "batches": []},
        {"files": {"a": ""}, "batches": [_batch_508("guard", guards={"a": None})]},
        {"files": {}, "batches": [_batch_508("post", [
            {"op": "create", "path": "a", "text": "hi"}], post={"a": _sha("different")})]},
        {"files": {"a": ""}, "batches": [_batch_508("exists", [
            {"op": "create", "path": "a", "text": "hi"}])]},
        {"files": {}, "batches": [_batch_508("missing", [{"op": "delete", "path": "x"}])]},
        {"files": {"a": "x"}, "batches": [_batch_508("range", [_edit_508("a", [(2, 2, "", "")])])]},
        {"files": {"a": "é!"}, "batches": [
            _batch_508("boundary", [_edit_508("a", [(1, 1, "", "X")])]),
            _batch_508("unicode", [_edit_508("a", [(2, 3, "!", "☃"), (0, 2, "é", "ü")])]),
        ]},
        {"files": {"a": "abcd"}, "batches": [_batch_508("overlap", [
            _edit_508("a", [(0, 2, "ab", "x"), (1, 3, "bc", "y")])])]},
        {"files": {"a": "ab"}, "batches": [_batch_508("insert", [
            _edit_508("a", [(0, 1, "a", "A"), (1, 1, "", "x"), (2, 2, "", "z")])])]},
        {"files": {"a": "ab"}, "batches": [_batch_508("same-start", [
            _edit_508("a", [(0, 0, "", "x"), (0, 1, "a", "A")])])]},
        {"files": {"a": "a"}, "batches": [_batch_508("chain", [
            _edit_508("a", [(0, 1, "a", "long")]),
            _edit_508("a", [(0, 4, "long", "é")]),
            {"op": "create", "path": "b", "text": ""},
            {"op": "delete", "path": "b"},
        ], guards={"a": _sha("a"), "b": None}, post={"a": _sha("é"), "b": None})]},
        {"files": {"a": "abc"}, "batches": [_batch_508("empty", [_edit_508("a", [])])]},
        {"files": {"a": "a"}, "batches": [_batch_508("priority", [
            _edit_508("a", [(0, 1, "wrong", ""), (4, 3, "", "")])])]},
        {"files": {"a": "a"}, "batches": [_batch_508("dupe-insertion", [
            _edit_508("a", [(1, 1, "", "x"), (1, 1, "", "y")])])]},
    ],
    509: [],
    510: [
        _report_510([_record_510(status="failed"), _record_510(attempt=2, duration_ms=3)]),
        _report_510(expected=["z", "a", "z"]),
        _report_510([_record_510(status="failed"),
                     _record_510(status="passed", revision=2, seq=2, observed=2),
                     _record_510(attempt=2, status="infra", seq=3, observed=3)]),
        _report_510([_record_510(observed=10), _record_510("b", observed=11, seq=2)],
                    expected=["a", "b"], cutoff=10),
        _report_510([_record_510(run="other", observed=10), _record_510(status="failed")]),
        _report_510([_record_510(status="failed"), _record_510(status="passed")]),
        _report_510([_record_510(status="passed", revision=9),
                     _record_510(status="failed", revision=8, observed=20, seq=2)]),
        _report_510([_record_510(status="failed"),
                     _record_510(status="withdrawn", revision=2, seq=2)]),
        _report_510([_record_510("a", status="failed"),
                     _record_510("b", status="passed", observed=2)], expected=["a", "b"]),
        _report_510([_record_510(), _record_510(attempt=2, status="cancelled", duration_ms=7)]),
        _report_510([_record_510(status="infra"),
                     _record_510(attempt=2, status="passed", duration_ms=2)]),
        _report_510([_record_510(status="passed"),
                     _record_510(attempt=2, status="running", duration_ms=200)]),
        _report_510([_record_510("external"), _record_510("gone", status="withdrawn", seq=8)],
                    expected=[]),
        _report_510([_record_510(test, status=status, seq=i, shard=test, duration_ms=20)
                     for i, (test, status) in enumerate([
                         ("d", "passed"), ("c", "failed"), ("b", "skipped"), ("a", "passed"),
                     ])], expected=["d", "c", "b", "a"]),
        _report_510([_record_510(shard="a", status="failed"),
                     _record_510(shard="z", status="passed"),
                     _record_510(shard="z", status="skipped", seq=2, observed=1)]),
        _report_510([_record_510(status="cancelled")]),
        _report_510([
            _record_510(f"t{i % 17:02}", attempt=1 + i % 5, status=(
                "passed", "failed", "infra", "cancelled", "withdrawn", "running", "skipped"
            )[i % 7], seq=i % 97, shard=f"s{i % 3}", revision=i % 4,
                        observed=i // 3, duration_ms=i % 31)
            for i in range(500)
        ], expected=[f"t{i:02}" for i in range(15)], cutoff=120),
    ],
    511: [
        {"tests": [_test_511("a"), _test_511("b")], "shards": [_shard_511("ab")]},
        {"tests": [_test_511("a"), _test_511("b"), _test_511("c")],
         "shards": [_shard_511("ab"), _shard_511("bc")]},
        {"tests": [], "shards": []},
        {"tests": [_test_511("a")], "shards": [_shard_511("")]},
        {"tests": [_test_511("a")], "shards": [_shard_511("a"), _shard_511("a", "passed")]},
        {"tests": [_test_511("a", good=True), _test_511("b")], "shards": [_shard_511("ab")]},
        {"tests": [_test_511("a", fault=2), _test_511("b"), _test_511("c")],
         "shards": [_shard_511("ab"), _shard_511("ac")]},
        {"tests": [_test_511("a", fault=3), _test_511("b"), _test_511("c")],
         "shards": [_shard_511("ab"), _shard_511("ac")]},
        {"tests": [_test_511("a", rerun=5), _test_511("b"), _test_511("c", rerun=0)],
         "shards": [_shard_511("abc")]},
        {"tests": [_test_511("a", available=False), _test_511("b", available=False),
                   _test_511("c")], "shards": [_shard_511("abc")]},
        {"tests": [_test_511("a"), _test_511("b")],
         "shards": [_shard_511("a", "infra"), _shard_511("b", "passed")]},
        {"tests": [_test_511("b", rerun=0), _test_511("a", rerun=0)],
         "shards": [_shard_511("abba")]},
        {"tests": [_test_511(chr(97 + i), rerun=i % 3) for i in range(8)],
         "shards": [_shard_511(pair) for pair in ("ab", "cd", "ef", "gh")]},
        {"tests": [_test_511(f"t{i:02}", rerun=i % 5) for i in range(16)],
         "shards": [_shard_511([f"t{i:02}" for i in range(16)])]},
    ],
}


def _schedule_509(state, specifications):
    return {"state": state, "events": [_event_509(*args[:2], **(args[2] if len(args) == 3 else {}))
                                      for args in specifications]}


_INPUTS[509] = [
    _schedule_509({"x": 1}, [
        ("a", "begin"), ("a", "add", {"key": "x", "value": 2}),
        ("a", "read", {"key": "x"}), ("a", "commit"),
    ]),
    _schedule_509({"x": 1}, [
        ("a", "begin"), ("b", "begin"), ("b", "set", {"key": "x", "value": 9}),
        ("b", "commit"), ("a", "read", {"key": "x"}),
        ("a", "add", {"key": "x", "value": 1}), ("a", "commit"),
    ]),
    _schedule_509({}, []),
    _schedule_509({"x": 1, "y": 1}, [
        ("a", "begin"), ("b", "begin"), ("a", "read", {"key": "y"}),
        ("b", "read", {"key": "x"}), ("a", "set", {"key": "x", "value": 0}),
        ("b", "set", {"key": "y", "value": 0}), ("a", "commit"), ("b", "commit"),
    ]),
    _schedule_509({"x": 1}, [
        ("a", "begin"), ("b", "begin"), ("b", "set", {"key": "x", "value": 2}),
        ("b", "commit"), ("c", "begin"), ("c", "set", {"key": "x", "value": 1}),
        ("c", "commit"), ("a", "set", {"key": "x", "value": 3}), ("a", "commit"),
    ]),
    _schedule_509({"x": 1}, [
        ("a", "begin"), ("b", "begin"), ("b", "set", {"key": "x", "value": 1}),
        ("b", "commit"), ("a", "delete", {"key": "x"}), ("a", "commit"),
    ]),
    _schedule_509({}, [
        ("a", "begin"), ("b", "begin"), ("b", "delete", {"key": "x"}), ("b", "commit"),
        ("a", "add", {"key": "x", "value": 1}), ("a", "commit"),
    ]),
    _schedule_509({"x": 0}, [
        ("a", "begin"), ("a", "set", {"key": "x", "value": 3}),
        ("a", "read", {"key": "x"}), ("a", "abort"),
    ]),
    _schedule_509({"x": 1}, [
        ("a", "begin"), ("a", "save", {"name": "s"}), ("a", "delete", {"key": "x"}),
        ("a", "read", {"key": "x"}), ("a", "save", {"name": "t"}),
        ("a", "set", {"key": "z", "value": 9}), ("a", "rollback", {"name": "s"}),
        ("a", "read", {"key": "x"}), ("a", "commit"),
    ]),
    _schedule_509({}, [
        ("a", "begin"), ("a", "save", {"name": "s"}),
        ("a", "set", {"key": "x", "value": 1}), ("a", "save", {"name": "t"}),
        ("a", "set", {"key": "y", "value": 2}), ("a", "release", {"name": "s"}),
        ("a", "save", {"name": "t"}), ("a", "delete", {"key": "x"}),
        ("a", "rollback", {"name": "t"}), ("a", "commit"),
    ]),
    _schedule_509({"x": 1}, [
        ("a", "begin"), ("b", "begin"), ("b", "delete", {"key": "x"}), ("b", "commit"),
        ("a", "read", {"key": "x"}), ("a", "commit"),
    ]),
    _schedule_509({}, [
        ("a", "begin"), ("a", "save", {"name": "s"}),
        ("a", "set", {"key": "x", "value": 1}), ("a", "rollback", {"name": "s"}),
        ("a", "set", {"key": "y", "value": 1}), ("a", "rollback", {"name": "s"}),
        ("a", "commit"),
    ]),
    _schedule_509({"x": 0, "y": 0}, [
        ("a", "begin"), ("b", "begin"), ("b", "set", {"key": "x", "value": 1}),
        ("b", "commit"), ("a", "set", {"key": "x", "value": 2}),
        ("a", "set", {"key": "y", "value": 2}), ("a", "commit"),
    ]),
    _schedule_509({"x": 0}, [
        ("a", "begin"), ("a", "save", {"name": "s"}),
        ("a", "set", {"key": "x", "value": 1}), ("b", "begin"),
        ("b", "set", {"key": "x", "value": 9}), ("b", "commit"),
        ("a", "rollback", {"name": "s"}), ("a", "set", {"key": "y", "value": 3}),
        ("a", "commit"),
    ]),
]


def _artifact_500():
    objects = {
        "v1": "# Guide\nalpha\n", "v2": "# Guide\nalpha\nbeta\n",
        "v3": "# Guide\nβeta\n", "unicode": "café\n文\n", "empty": "",
        "alias": "# Guide\nalpha\n", "config": "port=8080\n",
        "config2": "port=9090\n", "flat": "directory replaced by file\n",
        "archive": "2026-10-01\0signed\n", "retired": "unretained old bytes\n",
        "lf": "a\nb\n", "crlf": "a\r\nb\r\n",
    }
    snapshots = [
        {"id": "root", "parent": None, "ops": [
            {"op": "put", "path": "docs/readme", "object": "v1"},
            {"op": "put", "path": "data/文.txt", "object": "unicode"},
            {"op": "put", "path": "data/empty", "object": "empty"},
            {"op": "put", "path": "config/app.ini", "object": "config"},
            {"op": "put", "path": "retired.bin", "object": "retired"},
        ]},
        {"id": "draft", "parent": "root", "ops": [
            {"op": "copy", "source": "docs", "path": "docs/previous"},
            {"op": "put", "path": "docs/readme", "object": "v2"},
            {"op": "drop", "path": "retired.bin"},
        ]},
        {"id": "release", "parent": "draft", "ops": [
            {"op": "copy", "source": "data", "path": "mirror"},
            {"op": "copy", "source": "mirror", "path": "mirror"},
            {"op": "drop", "path": "data/文.txt"},
            {"op": "put", "path": "docs/README.alias", "object": "alias"},
            {"op": "put", "path": "lines.txt", "object": "lf"},
        ]},
        {"id": "experiment", "parent": "draft", "ops": [
            {"op": "drop", "path": "docs"},
            {"op": "put", "path": "docs", "object": "flat"},
            {"op": "put", "path": "scratch", "object": "retired"},
        ]},
        {"id": "hotfix", "parent": "release", "ops": [
            {"op": "copy", "source": "docs/previous", "path": "docs"},
            {"op": "put", "path": "config/app.ini", "object": "config2"},
            {"op": "put", "path": "lines.txt", "object": "crlf"},
            {"op": "drop", "path": "mirror"},
        ]},
        {"id": "archive", "parent": "root", "ops": [
            {"op": "copy", "source": "data/empty", "path": "empty-copy"},
            {"op": "drop", "path": "retired.bin"},
            {"op": "copy", "source": "absent", "path": "docs"},
            {"op": "put", "path": "archive/index", "object": "archive"},
            {"op": "drop", "path": "data/empty"},
        ]},
        {"id": "empty", "parent": None, "ops": [
            {"op": "drop", "path": "missing"},
            {"op": "copy", "source": "missing", "path": "else"},
        ]},
        {"id": "unretained-tip", "parent": "experiment", "ops": [
            {"op": "drop", "path": "data"}, {"op": "put", "path": "draft.txt", "object": "v3"},
        ]},
    ]
    existing = [_sha(objects[key]) for key in ("v1", "unicode", "empty", "retired", "config")]
    existing += [_sha("already orphaned\n"), _sha(objects["v1"])]
    data = {"objects": objects, "snapshots": snapshots,
            "keep": ["release", "hotfix", "archive", "empty", "release"], "existing": existing}
    files = {f"objects/{name}.txt": value for name, value in objects.items()}
    files["backup/object-index.json"] = dumps({name: f"objects/{name}.txt" for name in objects})
    files["backup/snapshots-a.json"] = dumps(list(reversed(snapshots[::2])))
    files["backup/snapshots-b.json"] = dumps(list(reversed(snapshots[1::2])))
    files["backup/retention.json"] = dumps({"keep": data["keep"]})
    files["storage/inventory.json"] = dumps({"existing": existing})
    prompt = _PROMPT_500.replace(
        "Вход: objects={label: UTF-8 text}, snapshots=[{id,parent,ops}], keep=[id],\nexisting=[SHA-256].",
        "Прочитай backup/object-index.json (label→путь UTF-8 файла), сами objects,\n"
        "оба backup/snapshots-*.json, backup/retention.json и storage/inventory.json.\n"
        "Логический вход: objects={label:text}, snapshots=объединённые списки,\n"
        "keep=retention.keep, existing=inventory.existing.",
    )
    return artifact_task(
        number=500, slug="incremental_backup_gc", name="Plan incremental backup retention and blob GC",
        parent=366, prompt=prompt, files=files, expected=_solve_500(data),
        tags=("filesystem", "hashing", "snapshot", "multi-file"),
    )


def _artifact_502():
    invoices = [
        {"id": "i-a-old", "customer": "Acme, Inc", "currency": "USD", "due": 1, "amount": 100},
        {"id": "i-a-new", "customer": "Acme, Inc", "currency": "USD", "due": 3, "amount": 140},
        {"id": "i-a-eur", "customer": "Acme, Inc", "currency": "EUR", "due": 0, "amount": 90},
        {"id": "i-b-old", "customer": "B", "currency": "USD", "due": 1, "amount": 75},
        {"id": "i-b-new", "customer": "B", "currency": "USD", "due": 1, "amount": 60},
        {"id": "i-zero", "customer": "B", "currency": "USD", "due": -1, "amount": 0},
        {"id": "i-unpaid", "customer": "C", "currency": "GBP", "due": 2, "amount": 1000},
    ]
    events = [
        {"id": "p1", "seq": 1, "kind": "payment", "customer": "Acme, Inc",
         "currency": "USD", "amount": 80},
        {"id": "p2", "seq": 2, "kind": "payment", "customer": "Acme, Inc",
         "currency": "USD", "amount": 130, "invoice": "i-a-new"},
        {"id": "p3", "seq": 3, "kind": "payment", "customer": "B",
         "currency": "USD", "amount": 110, "invoice": "i-a-old"},
        {"id": "f1", "seq": 4, "kind": "refund", "ref": "p2", "amount": 20},
        {"id": "r1", "seq": 5, "kind": "reverse", "ref": "f1"},
        {"id": "r2", "seq": 6, "kind": "reverse", "ref": "r1"},
        {"id": "p4", "seq": 7, "kind": "payment", "customer": "Acme, Inc",
         "currency": "EUR", "amount": 120, "invoice": "i-a-old"},
        {"id": "r3", "seq": 8, "kind": "reverse", "ref": "p1"},
        {"id": "r4", "seq": 9, "kind": "reverse", "ref": "p1"},
        {"id": "f2", "seq": 10, "kind": "refund", "ref": "p4", "amount": 150},
        {"id": "r5", "seq": 11, "kind": "reverse", "ref": "f2"},
        {"id": "p5", "seq": 12, "kind": "payment", "customer": "B",
         "currency": "USD", "amount": 95, "invoice": "i-b-old"},
        {"id": "r6", "seq": 13, "kind": "reverse", "ref": "p5"},
        {"id": "f3", "seq": 14, "kind": "refund", "ref": "p5", "amount": 25},
        {"id": "f4", "seq": 15, "kind": "refund", "ref": "p3", "amount": 10},
        {"id": "p6", "seq": 16, "kind": "payment", "customer": "Acme, Inc",
         "currency": "USD", "amount": 40, "invoice": "missing"},
        {"id": "r7", "seq": 17, "kind": "reverse", "ref": "r2"},
        {"id": "r8", "seq": 18, "kind": "reverse", "ref": "r7"},
        {"id": "p7", "seq": 19, "kind": "payment", "customer": "D",
         "currency": "USD", "amount": 7},
        {"id": "f5", "seq": 20, "kind": "refund", "ref": "p7", "amount": 12},
        {"id": "f6", "seq": 21, "kind": "refund", "ref": "p7", "amount": 2},
        {"id": "p8", "seq": 22, "kind": "payment", "customer": "B",
         "currency": "USD", "amount": 70, "invoice": "i-zero"},
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=["id", "customer", "currency", "due", "amount"])
    writer.writeheader()
    writer.writerows(reversed(invoices))
    files = {"accounting/invoices.csv": stream.getvalue()}
    for n in range(3):
        files[f"accounting/events-{n}.jsonl"] = "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in reversed(events[n::3])
        )
    prompt = _PROMPT_502.replace(
        "Вход invoices=[{id,customer,currency,due,amount}], events с уникальными id",
        "Прочитай accounting/invoices.csv (due/amount целые, CSV quoting обязателен)\n"
        "и объедини все accounting/events-*.jsonl. Получатся invoices и events.\n"
        "invoices=[{id,customer,currency,due,amount}], events с уникальными id",
    )
    return artifact_task(
        number=502, slug="invoice_event_settlement", name="Reconcile reversible invoice event ledgers",
        parent=367, prompt=prompt, files=files,
        expected=_solve_502({"invoices": invoices, "events": events}),
        tags=("accounting", "csv", "event-sourcing", "multi-file"),
    )


def _artifact_506():
    files = {
        "configs/service.ini": (
            "; managed manually\n"
            "global = old ; keep-global\n"
            "[SERVER] # primary\n"
            "port = 8080 ; do not remove\n"
            "token: \"a # b; c\"  # quoted\n"
            "message = first \\\n"
            "[pretend-section] \\\n"
            "  last ; continued-tail\n"
            "[server]\n"
            "PORT=8081\t; second\n"
            "empty=   # empty comment\n"
            "[Other]\n"
            "port = 7000\n"
        ),
        "configs/windows.ini": (
            "[Server]\r\n"
            "port : 8080\t # windows\r\n"
            "token = 'literal ; # ok' ; suffix\r\n"
            "path = C:\\\\tmp\\\\\r\n"
            "port = 8082\r\n"
            "[server]\r\n"
            "port=8083"
        ),
        "configs/mixed.ini": (
            "# header\r"
            "[s]\r"
            "key=one \\\r\n"
            "  two \\\n"
            "    three\t; tail\r"
            "KEY = 'escaped \\' # still quoted'\n"
            "key=     \n"
            "url=https://example/#fragment ; real comment\n"
            "[t]\n"
            "key=untouched"
        ),
        "configs/workers.ini": (
            "[pool]\n"
            "name = \"one\\\\\" # one\n"
            "name : \"two\\\" # quoted\" ; two\n"
            "Name=three#not-comment\n"
            "count=4\n"
            "[POOL]\n"
            "name=four\n"
            "count=8\n"
        ),
    }
    jobs = [
        {"source": "configs/service.ini", "edits": [
            {"section": "server", "key": "port", "which": 2, "value": "9091"},
            {"section": "SERVER", "key": "port", "which": 1, "value": "9090"},
            {"section": "server", "key": "message", "which": "all", "value": "line1\nline2 # safe"},
            {"section": "server", "key": "empty", "which": "all", "value": ""},
            {"section": "", "key": "global", "which": "all", "value": "root"},
            {"section": "server", "key": "missing", "which": 1, "value": "no insertion"},
        ]},
        {"source": "configs/windows.ini", "edits": [
            {"section": "SERVER", "key": "port", "which": "all", "value": "9090"},
            {"section": "server", "key": "token", "which": 1, "value": '文 "quote" \\slash'},
            {"section": "server", "key": "port", "which": 3, "value": "last"},
        ]},
        {"source": "configs/mixed.ini", "edits": [
            {"section": "s", "key": "key", "which": 1, "value": "collapsed"},
            {"section": "s", "key": "KEY", "which": 3, "value": "empty value"},
            {"section": "s", "key": "key", "which": "all", "value": "uniform"},
            {"section": "s", "key": "url", "which": 1, "value": "https://new/#literal"},
        ]},
        {"source": "configs/workers.ini", "edits": [
            {"section": "pool", "key": "name", "which": 2, "value": "changed"},
            {"section": "pool", "key": "count", "which": "all", "value": "16"},
            {"section": "pool", "key": "name", "which": 4, "value": "last\nworker"},
            {"section": "absent", "key": "name", "which": "all", "value": "absent"},
        ]},
    ]
    expected = {"files": {}, "audit": []}
    for job in jobs:
        answer = _solve_506({"text": files[job["source"]], "edits": job["edits"]})
        expected["files"][job["source"]] = answer["text"]
        expected["audit"].append({"source": job["source"], "applied": answer["applied"]})
    files["edit-plan.json"] = dumps({"jobs": jobs})
    return artifact_task(
        number=506, slug="lossless_ini_edit_plan", name="Apply lossless multi-file INI changes",
        parent=369, prompt=_PROMPT_506, files=files, expected=expected,
        tags=("config", "lossless", "parsing", "multi-file"),
    )


def _artifact_507():
    files = {
        "config/prod.ini": (
            "@let REGION=${ENV:REGION:-local}\n"
            "@let LABEL=parent\n"
            "[server]\n"
            "port=8080\n"
            "host=${VAR:REGION}.example\n"
            "@include child/service.ini\n"
            "after=${VAR:LABEL}\n"
            "endpoint=https://${host}:${port}/api\n"
            "@include child/override.ini\n"
            "escaped=$${server.port}\n"
            "fallback=${missing:-$cash}\n"
        ),
        "config/child/service.ini": (
            "@let LABEL=child-${VAR:LABEL}\n"
            "captured=${VAR:LABEL}\n"
            "[database]\n"
            "host=db.${server.host}\n"
            "dsn=${host}:${ENV:DB_PORT:-5432}\n"
            "@include ../shared/common.ini\n"
        ),
        "config/shared/common.ini": (
            "user=${ENV:USER:-guest}\n"
            "empty=${ENV:EMPTY:-fallback}\n"
        ),
        "config/child/override.ini": (
            "@let LABEL=must-not-leak\n"
            "port=9090\n"
            "[other]\n"
            "ref=${server.captured}\n"
        ),
        "config/repeated.ini": (
            "@let LABEL=first\n"
            "[one]\n"
            "@include child/piece.ini\n"
            "@let LABEL=second\n"
            "[two]\n"
            "@include child/piece.ini\n"
            "copied=${one.value}|${two.value}\n"
        ),
        "config/child/piece.ini": "value=${VAR:LABEL}\n@let LABEL=changed-in-child\n",
        "config/cycles/a.ini": "@include b.ini\n",
        "config/cycles/b.ini": "@include ./a.ini\n",
        "config/late-cycle.ini": "[a]\nx=${b.y}\n[b]\ny=${a.x}\n",
        "config/missing.ini": "x=${absent}\n",
        "config/missing-nested.ini": "x=${y:-not-used}\ny=${absent}\n",
        "config/escape.ini": "@include ../../not-allowed.ini\n",
        "config/include-missing.ini": "@include absent.ini\n",
        "config/early.ini": (
            "@let X=${ENV:NAME:-first}\n"
            "@let X=${VAR:X}-again\n"
            "@include ${VAR:PATH:-child/piece.ini}\n"
            "@let LABEL=${VAR:X}\n"
            "late=${VAR:LABEL}\n"
        ),
    }
    # The early include intentionally captures no LABEL and fails only at resolution.
    jobs = [
        {"id": "prod-eu", "entry": "config/prod.ini",
         "env": {"REGION": "eu", "DB_PORT": "6000", "EMPTY": "", "USER": "service"}},
        {"id": "prod-default", "entry": "config/prod.ini", "env": {}},
        {"id": "scopes", "entry": "config/repeated.ini", "env": {}},
        {"id": "include-cycle", "entry": "config/cycles/a.ini", "env": {}},
        {"id": "setting-cycle", "entry": "config/late-cycle.ini", "env": {}},
        {"id": "missing", "entry": "config/missing.ini", "env": {}},
        {"id": "nested-missing", "entry": "config/missing-nested.ini", "env": {}},
        {"id": "escape", "entry": "config/escape.ini", "env": {}},
        {"id": "absent-file", "entry": "config/include-missing.ini", "env": {}},
        {"id": "captured-absence", "entry": "config/early.ini", "env": {"NAME": "chosen"}},
    ]
    expected = {job["id"]: _solve_507({"files": files, **job}) for job in jobs}
    files["jobs.json"] = dumps({"jobs": jobs})
    return artifact_task(
        number=507, slug="scoped_ini_includes", name="Resolve lexical INI includes and captured variables",
        parent=369, prompt=_PROMPT_507, files=files, expected=expected,
        tags=("config", "includes", "interpolation", "multi-file"),
    )


_SOLVERS = {
    500: _solve_500, 501: _solve_501, 502: _solve_502, 503: _solve_503,
    504: _solve_504, 505: _solve_505, 506: _solve_506, 507: _solve_507,
    508: _solve_508, 509: _solve_509, 510: _solve_510, 511: _solve_511,
}

_HELPERS = {
    501: [_path_501, _frame_501],
    503: [_fraction_503, _allocate_503, _couple_503],
    504: [_norm_504],
    505: [],
    508: [_sha],
    509: [],
    510: [],
    511: [],
}

_PROGRAM_META = {
    501: ("portable_merkle_archive", "Build canonical Unicode Merkle archives", 366, _PROMPT_501),
    503: ("rational_invoice_fx", "Optimize coupled invoice and jurisdiction settlement", 367, _PROMPT_503),
    504: ("atomic_relational_migration", "Migrate tenant identities with atomic validation", 368, _PROMPT_504),
    505: ("temporal_history_migration", "Migrate revisioned effective-date histories", 368, _PROMPT_505),
    508: ("atomic_guarded_edits", "Apply guarded UTF-8 edit transactions", 370, _PROMPT_508),
    509: ("snapshot_savepoint_schedule", "Execute snapshot transactions and savepoints", 370, _PROMPT_509),
    510: ("retry_shard_reconciliation", "Reconcile corrected retry shards at a cutoff", 371, _PROMPT_510),
    511: ("exact_ci_diagnosis", "Find exact CI diagnoses and distinguishing reruns", 371, _PROMPT_511),
}


def _gold_program(number):
    functions = [*_HELPERS[number], _SOLVERS[number]]
    imports = (
        "import copy\nimport hashlib\nimport json\nimport posixpath\nimport re\n"
        "import sys\nimport unicodedata\nfrom fractions import Fraction\n\n"
        "sys.set_int_max_str_digits(0)\n\n"
    )
    body = "\n\n".join(inspect.getsource(function) for function in functions)
    main = (
        f"\n\nfor _line in sys.stdin:\n"
        f"    print(json.dumps(_solve_{number}(json.loads(_line)), ensure_ascii=False))\n"
    )
    return imports + body + main


_CASES = {
    number: [(copy.deepcopy(data), _SOLVERS[number](copy.deepcopy(data))) for data in inputs]
    for number, inputs in _INPUTS.items()
}

TASKS = [_artifact_500(), _artifact_502(), _artifact_506(), _artifact_507()]
for _number, (_slug, _name, _parent, _prompt) in _PROGRAM_META.items():
    TASKS.append(program_task(
        number=_number, slug=_slug, name=_name, parent=_parent, prompt=_prompt,
        cases=_CASES[_number], gold=_gold_program(_number),
        tags=("workflow", "exact",),
    ))
TASKS.sort(key=lambda task: task.id)

# Keep the pilot references above available for independent regression tests.
# Only revision-four contracts and fixtures are registered in the active wave.
TASKS, _CASES, _REVISION_INPUTS, _REVISION_ARTIFACTS, _REVISION_SOLVERS = _build_revision(
    globals(), TASKS
)

_V4_TASKS, _V4_CASES = TASKS, _CASES
TASKS, _V5_CASES, _V5_INPUTS, _V5_ARTIFACTS, _V5_SOLVERS = _build_v5(
    globals(), TASKS, _REVISION_ARTIFACTS
)
_CASES = {**_V4_CASES, **_V5_CASES}
_V5_TASKS = TASKS
TASKS, _V6_ARTIFACT_500 = _build_v6(TASKS, _V5_ARTIFACTS[500])
