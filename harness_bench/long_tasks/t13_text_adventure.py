"""long_13_text_adventure — текстовый квест «Маяк на Кривой косе» через CLI.

The workspace gets ``game.py`` (a stdlib engine) and ``data/world{0..3}.dat``.
The world is split into four zones: the village (zone 0), the lighthouse cape
(zone 1), Bird Island with the wreck (zone 2) and the abandoned skete (zone 3).
Zone 0 is only obfuscated (zlib + a sha256 stream keyed by a constant). Every
later zone is encrypted with a key ``pbkdf2_hmac(sha256, phrase, salt, ITER)``
where ``phrase`` is three words the player types into a lock at the zone
border (``открыть ворота <w1> <w2> <w3>``); each word is cut to its first five
letters. The words are found only by reading texts of the previous zone
(diary pages, graves, old logbooks, icons, inscriptions, dialogue), with traps
and later corrections. Without the words the zone's locations, items, NPCs,
rules and texts are not recoverable from the workspace: the stored tag only
lets one test a guess at ~0.3 s (python 3.14) to ~1 s (3.9) per PBKDF2 call,
and the candidate space over the previous zone's vocabulary (distinct five-letter
prefixes, 1.8-3K per zone) is 5e9-2.6e10 per lock.

Every command is run as ``python3 game.py <команда>``; state lives in
``save.json`` (HMAC-signed, includes the derived zone keys) and every command
is appended to ``journal.log``. The check replays the journal through a hidden
copy of the engine and data kept in this module, starting from the initial
state, and requires the winning ending and a final state equal to the saved
one. Ground truth is the win command list ``_WIN`` (replayed during checks).
"""

from __future__ import annotations

import functools
import hashlib
import json
import random
import zlib
from pathlib import Path

from .common import long_task, read_json, read_text, write

TASK_ID = "long_13_text_adventure"
_SALT = "krivaya-kosa/1905/sv-olga"
_ITER = 4_000_000

# ---------------------------------------------------------------------------
# Game engine (written into the workspace as game.py; also used by the check)
# ---------------------------------------------------------------------------

_GAME_SRC_1 = r'''#!/usr/bin/env python3
"""«Маяк на Кривой косе» — текстовый квест.

Запуск: python3 game.py <команда>. Например: python3 game.py помощь
Состояние хранится в save.json, все команды записываются в journal.log.
Данные мира лежат в каталоге data/ рядом с игрой.
"""

import copy
import hashlib
import hmac
import json
import os
import sys
import zlib

_SALT = "__SALT__"
_ITER = __ITER__
_HERE = os.path.dirname(os.path.abspath(__file__))
_BLOBS = {}
_ZCACHE = {}
_WCACHE = {}
_KEYS = {}


def _stream(key, data):
    n = len(data)
    pad = b"".join(hashlib.sha256(key + i.to_bytes(8, "big")).digest() for i in range((n + 31) // 32))
    return (int.from_bytes(data, "big") ^ int.from_bytes(pad[:n], "big")).to_bytes(n, "big")


def _blob(name):
    if name not in _BLOBS:
        with open(os.path.join(_HERE, "data", name), "rb") as fh:
            _BLOBS[name] = fh.read()
    return _BLOBS[name]


def _unpack(key, name):
    return json.loads(zlib.decompress(_stream(key, _blob(name))).decode("utf-8"))


def base_world():
    if None not in _ZCACHE:
        _ZCACHE[None] = _unpack(hashlib.sha256(_SALT.encode()).digest(), "world0.dat")
    return _ZCACHE[None]


def derive(zone, words):
    phrase = " ".join(words).encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", phrase, (_SALT + "/" + zone).encode(), _ITER, 32)


def key_tag(key):
    return hashlib.sha256(key + b"/tag").hexdigest()[:24]


def zone_data(zone, hexkey):
    if (zone, hexkey) not in _ZCACHE:
        _ZCACHE[(zone, hexkey)] = _unpack(bytes.fromhex(hexkey), base_world()["zones"][zone]["file"])
    return _ZCACHE[(zone, hexkey)]


def merge(w, z):
    for k in ("locs", "items", "npcs"):
        w[k].update(z.get(k, {}))
    for k in ("gives", "uses"):
        w[k] = z.get(k, []) + w[k]
    for p in z.get("patch_opts", []):
        opts = w["npcs"][p["npc"]]["nodes"][p["node"]]["options"]
        opts.insert(len(opts) - 1, p["opt"])
    for k, v in z.get("top", {}).items():
        w[k] = v


def world():
    ck = tuple(sorted(_KEYS.items()))
    if ck not in _WCACHE:
        w = copy.deepcopy(base_world())
        for zone, hexkey in ck:
            merge(w, zone_data(zone, hexkey))
        _WCACHE[ck] = w
    return _WCACHE[ck]


def use_keys(state):
    _KEYS.clear()
    _KEYS.update(state.get("keys") or {})


def _sig(state):
    key = hashlib.sha256((_SALT + "::save").encode()).digest()
    msg = json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def initial_state():
    _KEYS.clear()
    w = world()
    return {
        "loc": w["start"], "t": w["start_time"], "inv": [],
        "items": {k: v["at"] for k, v in sorted(w["items"].items())},
        "flags": {}, "dialog": None, "wrong": 0, "lock_until": 0, "keys": {},
        "ending": None, "n": 0, "seen": [w["start"]],
    }
'''

_GAME_SRC_2 = r'''

# -- text helpers ---------------------------------------------------------------

def norm(text):
    text = text.lower().replace("ё", "е")
    out = []
    for ch in text:
        out.append(ch if ch.isalnum() or ch == "-" else " ")
    return "".join(out).split()


STOP = {"на", "в", "во", "к", "ко", "у", "из", "и", "с", "со", "то", "этот", "эту", "это", "же",
        "по", "за", "до", "о", "об", "под", "над", "от", "для"}


def clock(t):
    day = t // 1440 + 1
    return f"{13 + day} сентября, {t % 1440 // 60:02d}:{t % 60:02d}"


def is_low(t):
    return any(abs(t - x) <= world()["tide_half"] for x in world()["lows"])


def is_dark(t):
    m = t % 1440
    return m >= world()["dusk"] or m < world()["dawn"]


def cond(state, c):
    neg = c.startswith("!")
    if neg:
        c = c[1:]
    kind, _, arg = c.partition(":")
    if kind == "low":
        ok = is_low(state["t"])
    elif kind == "dark":
        ok = is_dark(state["t"])
    elif kind == "flag":
        ok = bool(state["flags"].get(arg))
    elif kind == "has":
        ok = arg in state["inv"]
    elif kind == "here":
        ok = state["items"].get(arg) == state["loc"]
    elif kind == "since":
        name, _, mins = arg.partition(":")
        ok = name in state["flags"] and state["t"] - int(state["flags"][name]) >= int(mins)
    elif kind == "cool":
        name, _, mins = arg.partition(":")
        ok = name not in state["flags"] or state["t"] - int(state["flags"][name]) >= int(mins)
    else:
        raise ValueError(c)
    return ok != neg


def conds(state, cs):
    return all(cond(state, c) for c in cs or [])


def apply(state, effects):
    out = []
    for e in effects or []:
        kind, _, arg = e.partition(":")
        if kind == "set":
            state["flags"][arg] = True
        elif kind == "stamp":
            state["flags"][arg] = state["t"]
        elif kind == "unset":
            state["flags"].pop(arg, None)
        elif kind == "give":
            state["items"][arg] = "inv"
            if arg not in state["inv"]:
                state["inv"].append(arg)
            out.append(f"[Получено: {world()['items'][arg]['name']}]")
        elif kind == "take":
            state["items"][arg] = ""
            if arg in state["inv"]:
                state["inv"].remove(arg)
        elif kind == "reveal":
            if state["items"].get(arg) == "":
                state["items"][arg] = state["loc"]
        elif kind == "win":
            state["ending"] = "победа"
        else:
            raise ValueError(e)
    return out


def pick_text(state, obj, key):
    for alt in obj.get(key + "_alt", []):
        if conds(state, alt["if"]):
            return alt["text"]
    return obj.get(key)

# -- world queries --------------------------------------------------------------

def loc_items(state, loc):
    return [k for k, v in state["items"].items() if v == loc]


def npcs_here(state):
    return [k for k, n in sorted(world()["npcs"].items()) if n["loc"] == state["loc"]]


def describe(state):
    w = world()
    loc = w["locs"][state["loc"]]
    parts = [f"== {loc['name']} =="]
    parts.append(pick_text(state, loc, "text"))
    for dyn in loc.get("dyn", []):
        if conds(state, dyn["if"]):
            parts.append(dyn["text"])
    if loc.get("sea"):
        parts.append(loc["sea"]["low" if is_low(state["t"]) else "high"])
    hour = state["t"] % 1440 // 60
    period = "night" if is_dark(state["t"]) else "morning" if hour < 11 else "day" if hour < 17 else "evening"
    if loc.get("outdoor"):
        opts = w["sky"][period]
        parts.append(opts[(state["n"] + len(state["loc"])) % len(opts)])
    items = loc_items(state, state["loc"])
    if items:
        parts.append("Здесь можно заметить: " + "; ".join(w["items"][i]["name"] for i in items) + ".")
    for n in npcs_here(state):
        parts.append(w["npcs"][n]["here"])
    exits = []
    for d, ex in loc["exits"].items():
        name = w["locs"][ex["to"]]["name"] if ex["to"] in w["locs"] else ex.get("label", "?")
        exits.append(f"{d} — {name}")
    parts.append("Выходы: " + "; ".join(exits) + ".")
    return "\n\n".join(parts)


def match(tokens, words):
    for tok in tokens:
        ok = False
        for wd in words:
            if tok.isdigit() or wd.isdigit():
                ok = tok == wd
            else:
                ok = tok.startswith(wd) or (len(tok) >= 4 and wd.startswith(tok))
            if ok:
                break
        if not ok:
            return False
    return bool(tokens)


def scope(state, kinds=("inv", "here", "feat", "npc")):
    w = world()
    out = []
    if "inv" in kinds:
        out += [("item", i) for i in state["inv"]]
    if "here" in kinds:
        out += [("item", i) for i in loc_items(state, state["loc"])]
    if "feat" in kinds:
        out += [("feat", f) for f in sorted(w["locs"][state["loc"]].get("feats", {}))]
    if "npc" in kinds:
        out += [("npc", n) for n in npcs_here(state)]
    return out


def obj_words(state, kind, oid):
    w = world()
    if kind == "item":
        return w["items"][oid]["words"]
    if kind == "feat":
        return w["locs"][state["loc"]]["feats"][oid]["words"]
    return w["npcs"][oid]["words"]


def obj_name(state, kind, oid):
    w = world()
    if kind == "item":
        return w["items"][oid]["name"]
    if kind == "feat":
        return w["locs"][state["loc"]]["feats"][oid]["name"]
    return w["npcs"][oid]["name"]


def resolve(state, tokens, kinds=("inv", "here", "feat", "npc")):
    tokens = [t for t in tokens if t not in STOP]
    if not tokens:
        return None, "Уточните, о чём речь."
    found = [(k, o) for k, o in scope(state, kinds) if match(tokens, obj_words(state, k, o))]
    uniq = []
    for f in found:
        if f not in uniq:
            uniq.append(f)
    if not uniq:
        return None, "Здесь нет ничего подходящего под это описание."
    if len(uniq) > 1:
        return None, "Уточните, что именно: " + "; ".join(obj_name(state, k, o) for k, o in uniq) + "."
    return uniq[0], None


def get_obj(state, kind, oid):
    w = world()
    if kind == "item":
        return w["items"][oid]
    if kind == "feat":
        return w["locs"][state["loc"]]["feats"][oid]
    return w["npcs"][oid]


# -- commands --------------------------------------------------------------------

DIRS = {"север": "север", "с": "север", "юг": "юг", "ю": "юг", "восток": "восток", "в": "восток",
        "запад": "запад", "з": "запад", "вверх": "вверх", "наверх": "вверх", "вв": "вверх",
        "вниз": "вниз", "вн": "вниз", "внутрь": "внутрь", "вовнутрь": "внутрь",
        "наружу": "наружу", "выйти": "наружу"}
VERBS = {
    "осмотреться": "look", "оглядеться": "look", "смотреть": "look", "о": "look", "look": "look",
    "идти": "go", "иди": "go", "пойти": "go", "go": "go",
    "осмотреть": "examine", "изучить": "examine", "рассмотреть": "examine", "x": "examine",
    "читать": "read", "прочитать": "read", "прочесть": "read",
    "взять": "take", "поднять": "take", "забрать": "take",
    "бросить": "drop", "положить": "drop", "оставить": "drop",
    "инвентарь": "inv", "и": "inv", "вещи": "inv",
    "говорить": "talk", "поговорить": "talk", "спросить": "talk", "заговорить": "talk",
    "ответ": "answer", "ответить": "answer", "сказать": "answer", "выбрать": "answer",
    "дать": "give", "отдать": "give", "вручить": "give",
    "использовать": "use", "применить": "use", "вставить": "use", "залить": "use",
    "зажечь": "use", "завести": "use", "протереть": "use",
    "открыть": "open", "отпереть": "open", "ввести": "open",
    "ждать": "wait", "подождать": "wait", "ж": "wait",
    "время": "time", "часы": "time",
    "помощь": "help", "справка": "help", "help": "help",
}
TIME = {"examine": 2, "read": 5, "take": 1, "drop": 1, "talk": 5, "answer": 3, "give": 3,
        "use": 5, "open": 2}


def cmd_go(state, args):
    args = [a for a in args if a not in ("на", "к")]
    if len(args) != 1 or args[0] not in DIRS:
        return "Куда идти? Направления: север, юг, восток, запад, вверх, вниз, внутрь, наружу."
    d = DIRS[args[0]]
    ex = world()["locs"][state["loc"]]["exits"].get(d)
    if not ex:
        return "Туда пути нет."
    for c, msg in ex.get("checks", []):
        if not cond(state, c):
            return msg
    if not conds(state, ex.get("need")) or ex["to"] not in world()["locs"]:
        return ex.get("fail", "Туда сейчас не пройти.")
    state["t"] += ex.get("cost", 10)
    state["loc"] = ex["to"]
    state["dialog"] = None
    if ex["to"] not in state["seen"]:
        state["seen"].append(ex["to"])
    return describe(state)


def cmd_examine(state, args):
    if not args:
        return describe(state)
    ref, err = resolve(state, args)
    if err:
        return err
    obj = get_obj(state, *ref)
    parts = [pick_text(state, obj, "desc")]
    if obj.get("on_examine") and conds(state, obj.get("examine_need")):
        if obj.get("examine_note"):
            parts.append(obj["examine_note"])
        parts += apply(state, obj.get("on_examine"))
    return "\n\n".join(parts)


def cmd_read(state, args):
    ref, err = resolve(state, args)
    if err:
        return err
    obj = get_obj(state, *ref)
    text = pick_text(state, obj, "read")
    if not text:
        return "Читать здесь нечего."
    parts = [text]
    if obj.get("on_read") and conds(state, obj.get("read_need")):
        if obj.get("read_note"):
            parts.append(obj["read_note"])
        parts += apply(state, obj.get("on_read"))
    return "\n\n".join(parts)


def cmd_take(state, args):
    ref, err = resolve(state, args, ("here", "feat", "npc"))
    if err:
        mine, _ = resolve(state, args, ("inv",))
        return "Это уже у вас." if mine else err
    kind, oid = ref
    if kind != "item" or not world()["items"][oid].get("take", True):
        obj = get_obj(state, kind, oid)
        return obj.get("take_fail", "Это взять нельзя.")
    it = world()["items"][oid]
    if not conds(state, it.get("take_need")):
        return it.get("take_fail", "Это взять нельзя.")
    state["items"][oid] = "inv"
    state["inv"].append(oid)
    extra = apply(state, it.get("on_take"))
    return "\n".join([it.get("take_text", f"Вы берёте: {it['name']}.")] + extra)


def cmd_drop(state, args):
    ref, err = resolve(state, args, ("inv",))
    if err:
        return err
    oid = ref[1]
    state["inv"].remove(oid)
    state["items"][oid] = state["loc"]
    return f"Вы оставляете здесь: {world()['items'][oid]['name']}."


def cmd_inv(state, args):
    if not state["inv"]:
        return "У вас с собой ничего нет."
    w = world()
    return "У вас с собой:\n" + "\n".join(f"- {w['items'][i]['name']}" for i in state["inv"])


def show_node(state, npc_id, node_id):
    npc = world()["npcs"][npc_id]
    node = npc["nodes"][node_id]
    text = [pick_text(state, node, "text")]
    opts = [o for o in node["options"] if conds(state, o.get("need"))]
    text.append("\n".join(f"{i}. {o['label']}" for i, o in enumerate(opts, 1)))
    text.append("(Ответьте командой: ответ <номер>)")
    return "\n\n".join(text)


def cmd_talk(state, args):
    ref, err = resolve(state, args, ("npc",))
    if err:
        return err if npcs_here(state) else "Здесь не с кем поговорить."
    npc_id = ref[1]
    npc = world()["npcs"][npc_id]
    start = npc["start"]
    for alt in npc.get("start_alt", []):
        if conds(state, alt["if"]):
            start = alt["node"]
            break
    state["dialog"] = {"npc": npc_id, "node": start}
    return show_node(state, npc_id, start)


def cmd_answer(state, args):
    dlg = state.get("dialog")
    if not dlg:
        return "Вы ни с кем не разговариваете."
    if len(args) != 1 or not args[0].isdigit():
        return "Укажите номер ответа, например: ответ 2"
    npc = world()["npcs"][dlg["npc"]]
    node = npc["nodes"][dlg["node"]]
    opts = [o for o in node["options"] if conds(state, o.get("need"))]
    k = int(args[0])
    if not 1 <= k <= len(opts):
        return "Такого варианта нет."
    opt = opts[k - 1]
    extra = apply(state, opt.get("effects"))
    parts = []
    if opt.get("reply"):
        parts.append(opt["reply"])
    parts += extra
    if opt.get("next"):
        state["dialog"] = {"npc": dlg["npc"], "node": opt["next"]}
        parts.append(show_node(state, dlg["npc"], opt["next"]))
    else:
        state["dialog"] = None
        parts.append("(Разговор окончен.)")
    return "\n\n".join(parts)
'''

_GAME_SRC_3 = r'''

def cmd_give(state, args):
    w = world()
    args = [a for a in args if a not in STOP]
    splits = [(args[:i], args[i:]) for i in range(1, len(args))]
    splits += [(r, l) for l, r in splits]
    for left, right in splits:
        a, _ = resolve(state, left, ("inv",))
        b, _ = resolve(state, right, ("npc",))
        if a and b:
            item, npc = a[1], b[1]
            fail = None
            for rule in w["gives"]:
                if rule["item"] == item and rule["npc"] == npc:
                    if conds(state, rule.get("need")):
                        extra = apply(state, rule.get("effects"))
                        return "\n\n".join([rule["text"]] + extra)
                    fail = fail or rule.get("fail")
            return fail or w["npcs"][npc]["refuse"]
    return "Кому и что отдать? Пример: дать свисток мите"


def cmd_use(state, args):
    w = world()
    for i, a in enumerate(args):
        if a in ("на", "в", "во", "к", "под"):
            args = args[:i]
            break
    ref, err = None, "Что использовать?"
    for cut in range(len(args), 0, -1):
        ref, err = resolve(state, args[:cut], ("inv", "here", "feat"))
        if ref:
            break
    if not ref:
        return err
    kind, oid = ref
    key = oid if kind == "item" else "feat:" + oid
    if kind == "item" and oid not in state["inv"] and not w["items"][oid].get("use_in_place"):
        return "Сначала возьмите это."
    fail = None
    for rule in w["uses"]:
        if rule["obj"] != key or rule.get("loc", state["loc"]) != state["loc"]:
            continue
        if conds(state, rule.get("need")):
            extra = apply(state, rule.get("effects"))
            return "\n\n".join([rule["text"]] + extra)
        fail = fail or rule.get("fail")
    return fail or "Ничего не происходит."


def _find_lock(state, args):
    first_err = None
    for cut in range(1, len(args) + 1):
        ref, err = resolve(state, args[:cut], ("feat", "here"))
        if ref:
            return ref, args[cut:], None
        first_err = first_err or err
    feats = world()["locs"][state["loc"]].get("feats", {})
    locks = [f for f in sorted(feats) if feats[f].get("lock")]
    if args and len(locks) == 1:
        return ("feat", locks[0]), args, None
    return None, [], first_err or "Что открыть?"


def _wrong(state, lock):
    state["wrong"] += 1
    if state["wrong"] >= 3:
        state["wrong"] = 0
        state["lock_until"] = state["t"] + lock.get("jam_min", 180)
        return lock["fail"] + "\n\n" + lock["jam"]
    return lock["fail"]


def _unlock_zone(state, lock, words):
    w = world()
    zone = lock["zone"]
    canon = [a if a.isdigit() else a[:5] for a in words]
    key = derive(zone, canon)
    if key_tag(key) != w["zones"][zone]["tag"]:
        return _wrong(state, lock)
    state["wrong"] = 0
    state["keys"][zone] = key.hex()
    use_keys(state)
    z = zone_data(zone, key.hex())
    for iid, it in sorted(z.get("items", {}).items()):
        state["items"].setdefault(iid, it["at"])
    extra = apply(state, ["set:" + lock["flag"]])
    return "\n\n".join([lock["ok"], z["intro"]] + extra)


def cmd_open(state, args):
    ref, code, err = _find_lock(state, args)
    if ref is None:
        return err
    obj = get_obj(state, *ref)
    lock = obj.get("lock")
    if not lock:
        return obj.get("open_fail", "Это не открывается.")
    if state["flags"].get(lock["flag"]):
        return lock.get("already", "Уже открыто.")
    if lock["kind"] == "key":
        if lock["key"] not in state["inv"]:
            return lock["fail"]
        extra = apply(state, ["set:" + lock["flag"]] + lock.get("effects", []))
        return "\n\n".join([lock["ok"]] + extra)
    if state["t"] < state["lock_until"]:
        return lock["blocked"]
    words = [a for a in code if a not in STOP]
    if not words:
        return lock["ask"]
    if lock["kind"] == "phrase":
        if len(words) != lock["parts"]:
            return lock["count"]
        return _unlock_zone(state, lock, words)
    digest = hashlib.sha256((_SALT + ":" + " ".join(words)).encode("utf-8")).hexdigest()
    if digest == lock["hash"]:
        state["wrong"] = 0
        extra = apply(state, ["set:" + lock["flag"]] + lock.get("effects", []))
        return "\n\n".join([lock["ok"]] + extra)
    return _wrong(state, lock)


def cmd_wait(state, args):
    mins = 30
    nums = [int(a) for a in args if a.isdigit()]
    if nums:
        mins = nums[0]
    if any(a.startswith("час") for a in args):
        mins = (nums[0] if nums else 1) * 60
    mins = max(1, min(mins, 300))
    was = is_low(state["t"])
    state["t"] += mins
    text = [f"Вы ждёте {mins} мин. Время тянется медленно; ветер то стихает, то снова набирает силу."]
    if world()["locs"][state["loc"]].get("sea") and was != is_low(state["t"]):
        text.append("Вода заметно ушла от берега." if not was else "Вода прибывает, отмель скрывается.")
    if "watch" in state["inv"]:
        text.append(f"Часы показывают: {clock(state['t'])}.")
    return "\n\n".join(text)


def cmd_time(state, args):
    if "watch" not in state["inv"]:
        return "Часов у вас нет, и остаётся только гадать по солнцу и по воде."
    return f"Карманные часы показывают: {clock(state['t'])}."


def cmd_help(state, args):
    return world()["help"]


def cmd_look(state, args):
    return describe(state)


def step(state, line):
    state = copy.deepcopy(state)
    use_keys(state)
    if state["ending"]:
        return state, world()["over"][state["ending"]]
    toks = norm(line)
    state["n"] += 1
    if not toks:
        return state, cmd_help(state, [])
    verb, args = VERBS.get(toks[0]), toks[1:]
    if verb is None and toks[0] in DIRS and len(toks) == 1:
        verb, args = "go", toks
    if verb is None:
        return state, "Не понимаю. Список команд: помощь"
    text = globals()["cmd_" + verb](state, args)
    state["t"] += TIME.get(verb, 0)
    if not state["ending"] and state["t"] >= world()["deadline"]:
        state["ending"] = "крушение"
        text += "\n\n" + world()["crash"]
    elif state["ending"] == "победа":
        text += "\n\n" + world()["victory"]
    return state, text


def replay(lines):
    state = initial_state()
    for line in lines:
        state, _ = step(state, line)
    return state


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    save_p = os.path.join(here, "save.json")
    jr_p = os.path.join(here, "journal.log")
    line = " ".join(" ".join(argv).split())
    if norm(line) in (["новая"], ["новая", "игра"], ["заново"]) or not os.path.exists(save_p):
        state = initial_state()
        with open(save_p, "w", encoding="utf-8") as fh:
            json.dump({"state": state, "sig": _sig(state)}, fh, ensure_ascii=False)
        with open(jr_p, "w", encoding="utf-8"):
            pass
        print(world()["intro"] + "\n\n" + describe(state))
        if norm(line) in (["новая"], ["новая", "игра"], ["заново"]) or not line:
            return 0
    with open(save_p, encoding="utf-8") as fh:
        try:
            saved = json.load(fh)
        except ValueError:
            saved = {}
    state = saved.get("state")
    if not isinstance(state, dict) or saved.get("sig") != _sig(state):
        print("Файл save.json повреждён или изменён вручную. Начните заново: python3 game.py новая")
        return 1
    use_keys(state)
    if not line:
        print(cmd_help(state, []))
        return 0
    state, text = step(state, line)
    with open(save_p, "w", encoding="utf-8") as fh:
        json.dump({"state": state, "sig": _sig(state)}, fh, ensure_ascii=False)
    with open(jr_p, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


# ---------------------------------------------------------------------------
# World: locations
# ---------------------------------------------------------------------------

# Game clock: minutes from 00:00 of day 1. Low waters and the tide window.
_START = 7 * 60 + 30
_LOWS = [12 * 60 + 40, 1440 + 55, 1440 + 13 * 60 + 30, 2880 + 105, 2880 + 14 * 60 + 20,
         4320 + 155]
_HALF = 60
_DUSK, _DAWN = 21 * 60, 5 * 60 + 30
_DEADLINE = 4320 + 4 * 60

_LOW_FAIL = ("Там, где в отлив тянется отмель, сейчас ходят серые волны. Вода глубокая и холодная; "
             "пешком здесь не пройти, пока море не отступит.")
_ROW_FAIL = ("Сейчас малая вода: пролив между мысом и островом обсох, из ила торчат камни, и вельбот "
             "через осушку не протащить. Грести можно, когда вода поднимется.")


def _x(to, cost=10, need=None, fail=None, label=None):
    d = {"to": to, "cost": cost}
    if need:
        d["need"] = need
    if fail:
        d["fail"] = fail
    if label:
        d["label"] = label
    return d


def _f(name, words, desc, **kw):
    return {"name": name, "words": words, "desc": desc, **kw}


_LOCS: dict[str, dict] = {}
_CUR = [0]  # zone (0..3) of the locations being declared


def _loc(lid, name, bank, text, exits, feats=None, outdoor=True, sea=None, dyn=None):
    _LOCS[lid] = {"name": name, "bank": bank, "z": _CUR[0], "core": text, "exits": exits,
                  "feats": feats or {}, "outdoor": outdoor, "sea": sea, "dyn": dyn or []}


def _code_hash(word: str) -> str:
    return hashlib.sha256((_SALT + ":" + word).encode("utf-8")).hexdigest()


_SEA_PIER = {
    "low": "Сейчас отлив: вода отошла от свай, обнажив скользкие камни, водоросли и старые якорные "
           "цепи. Лодки легли на борт в иле, и по дну между ними бродят чайки.",
    "high": "Сейчас вода стоит высоко: она плещет под самыми досками причала, и лодки покачиваются, "
            "натягивая швартовы.",
}

_loc("pier", "Причал", "village", (
    "Вы стоите на старом деревянном причале рыбацкого посёлка Кривая Коса. Доски под ногами "
    "потемнели от соли и времени, между ними видна вода. Лодка, которая привезла вас сюда, уже "
    "ушла обратно в город, и её парус белеет далеко в заливе. К сваям привязаны несколько "
    "рыбацких лодок, на одной из них кто-то забыл вёсла. На востоке виден низкий рыбный склад с "
    "распахнутыми воротами, на западе, у самой воды, растянуты на жердях сети. К северу причал "
    "переходит в мощёную набережную посёлка. Далеко на востоке, за крышами и дюнами, узкой "
    "полосой уходит в море Кривая коса, и на её конце темнеет башня маяка — погасшего уже вторую "
    "неделю."), {"север": _x("embank"), "восток": _x("fishshed"), "запад": _x("netyard")},
    {"boats": _f("рыбацкие лодки", ["лодк", "весл"], (
        "Лодки старые, просмолённые, с именами, выведенными белой краской: «Надежда», «Ласточка», "
        "«Святой Пётр». Ни одна из них не называется «Касаткой» или «Чайкой». Вёсла на дне "
        "одной из лодок рассохлись и явно давно никому не нужны."),
        take_fail="Чужие лодки лучше не трогать — в посёлке этого не любят."),
     "lighthouse": _f("маяк вдали", ["маяк", "башн"], (
        "Отсюда маяк кажется игрушечным: белая башня с тёмным фонарём наверху. Говорят, он светил "
        "здесь каждую ночь почти двадцать лет подряд, пока не исчез смотритель."))},
    sea=_SEA_PIER)

_loc("fishshed", "Рыбный склад", "indoor", (
    "Длинный дощатый сарай пропах рыбой, солью и дёгтем так, что запах, кажется, въелся в стены "
    "навсегда. Вдоль стен стоят пустые бочки, на крюках висят старые сети и связки поплавков. В "
    "углу свалены промасленные тряпки, которыми рыбаки протирают снасти и фонари. Под потолком "
    "сушатся пучки какой-то травы. Сквозь щели в стенах пробивается свет, в нём кружатся пылинки. "
    "Выход — на запад, обратно на причал."), {"запад": _x("pier")},
    {"barrels": _f("бочки", ["бочк", "бочек"], (
        "Бочки пустые, на днищах выжжены клейма рыбной артели. В одной на дне засохла чешуя. "
        "Ничего полезного здесь нет.")),
     "nets": _f("старые сети", ["сет", "поплав"], (
        "Сети старые, в дырах, давно отслужившие своё. Поплавки на них берестяные, почерневшие."))},
    outdoor=False)

_loc("netyard", "Сушильня сетей", "village", (
    "На западном краю причала, на широкой площадке, утоптанной до твёрдости камня, на высоких "
    "жердях растянуты сети. Ветер играет ими, и кажется, будто вся площадка дышит. Между жердями "
    "стоят козлы с разложенными снастями, валяются обрывки верёвок и пробковые поплавки. На юге, "
    "почти у самой воды, стоит лодочный сарай смотрителя маяка — крепкий, из толстых брёвен, с "
    "тяжёлой дверью на висячем замке. На восток — причал."),
    {"восток": _x("pier"),
     "юг": _x("boathouse", need=["flag:boathouse_open"],
              fail="Дверь лодочного сарая заперта на тяжёлый висячий замок.")},
    {"door": _f("дверь сарая", ["двер", "сара", "замок", "замк"], (
        "Дверь лодочного сарая сколочена из толстых досок и окована железом. На ней висит тяжёлый "
        "замок, а на косяке кто-то вырезал ножом: «С. Грач». Без ключа сюда не попасть."),
        desc_alt=[{"if": ["flag:boathouse_open"], "text": "Замок снят, дверь сарая приоткрыта."}],
        lock={"kind": "key", "key": "boat_key", "flag": "boathouse_open",
              "ok": "Ключ туго поворачивается в замке, и дужка со скрежетом выходит из петли. "
                    "Дверь сарая открыта — теперь можно пройти на юг.",
              "fail": "Замок висит крепко. Нужен ключ от этого сарая.",
              "already": "Сарай уже открыт."}),
     "poles": _f("жерди с сетями", ["жерд", "сет", "козл"], (
        "Сети на жердях новые, крепкие — это сети Прохора и других рыбаков. Одна из них чинёная: "
        "в ней вплетён кусок другого цвета, будто её вытащили из воды после долгого пребывания "
        "там и залатали."))})

_loc("boathouse", "Лодочный сарай смотрителя", "indoor", (
    "Внутри сарая полутемно и пахнет смолой и старым деревом. На козлах лежит перевёрнутая "
    "лодка с облупившейся краской; на её корме ещё можно разобрать название: «Буревестник». "
    "Вдоль стены — вёсла, багор, свёрнутый парус. В дальнем углу стоит окованный "
    "железом сундук с необычным замком: вместо скважины у него семь медных колёсиков с буквами, "
    "и кажется, что замок открывается словом. Выход — на север."), {"север": _x("netyard")},
    {"chest": _f("сундук", ["сундук", "замок", "замк", "колес"], (
        "Сундук тяжёлый, дубовый, углы окованы железом. Замок у него буквенный: на медных колёсиках "
        "выбиты буквы алфавита. Над замком выгравировано: «Слово знает только хозяин». Похоже, "
        "что открыть его можно, назвав слово: открыть сундук <слово>."),
        desc_alt=[{"if": ["flag:chest_open"], "text": "Сундук открыт; его крышка откинута."}],
        lock={"kind": "code", "hash": _code_hash("зарянка"), "flag": "chest_open",
              "effects": ["reveal:rowlocks"],
              "ok": "Колёсики одно за другим встают на место, в замке что-то щёлкает, и тяжёлая "
                    "крышка сундука поддаётся. Внутри, завёрнутые в промасленную холстину, лежат "
                    "две бронзовые уключины — большие, не для здешних лодок, а для тяжёлого "
                    "маячного вельбота.",
              "fail": "Колёсики прокручиваются, но замок не открывается. Слово не то.",
              "jam": "После нескольких неудачных попыток колёсики заедает. Придётся подождать, "
                     "пока механизм замка не отпустит (около трёх часов).",
              "blocked": "Колёсики замка заело после неудачных попыток; пока они не поддаются.",
              "ask": "Замок открывается словом. Назовите его: открыть сундук <слово>.",
              "already": "Сундук уже открыт."}),
     "boat": _f("перевёрнутая лодка", ["лодк", "корм", "букв", "буревестн"], (
        "Лодка смотрителя, добротная, но давно не спускавшаяся на воду. На корме белой краской "
        "выведено: «Буревестник». Под названием — дата спуска: 1902. Днище рассохлось так, что "
        "сквозь щели видно свет: без конопатки на воду её не спустить. Для дальних переходов "
        "смотритель, говорят, брал казённый вельбот, что стоит в эллинге на мысу у маяка."))},
    outdoor=False)

_loc("embank", "Набережная", "village", (
    "Мощёная булыжником набережная тянется вдоль залива. Камни мокрые от брызг и тумана. По одну "
    "сторону — вода и причал, по другую — невысокие дома рыбаков с резными наличниками. На западе "
    "над дверью висит вывеска «Товары для моря. Ф. Кожин» — это лавка. На востоке шумит трактир "
    "«Тюлень»: над крыльцом покачивается деревянный тюлень, из трубы идёт дым. К северу улица "
    "поднимается к площади с колодцем, к югу — причал."),
    {"юг": _x("pier"), "запад": _x("shop"), "восток": _x("tavern"), "север": _x("square")},
    {"signs": _f("вывески", ["вывеск", "тюлен"], (
        "Вывеска лавки выцвела, но буквы ещё видны: «Товары для моря. Ф. Кожин. Фитили, снасти, "
        "керосин, свечи». Деревянный тюлень над трактиром выкрашен в серый цвет, и ему кто-то "
        "подрисовал усы."))},
    sea={"low": "Отлив: вода отступила от парапета, у подножия набережной обнажились камни в "
                "бурых водорослях.",
         "high": "Прилив: волны подкатывают к самому парапету и иногда перехлёстывают на "
                 "мостовую."})

_loc("shop", "Лавка Фомы Кожина", "indoor", (
    "Тесная лавка до потолка забита товаром: мотки верёвки, банки с краской, связки свечей, "
    "фонари, крючки, рыболовные грузила. За прилавком — полки с жестянками и ящичками, у каждого "
    "аккуратная бумажная наклейка. На прилавке лежит толстая конторская книга. Над прилавком "
    "прибита табличка: «В долг не отпускаю. Ф. К.». Выход — на восток."), {"восток": _x("embank")},
    {"ledger": _f("конторская книга", ["книг", "контор"], (
        "Толстая книга в клеёнчатой обложке — записи о продажах и долгах."),
        read="__DOC_LEDGER__"),
     "shelves": _f("полки", ["полк", "жестян", "ящич"], (
        "На полках — всё для моря. На одном ящичке наклейка «Фитили ламповые, широкие». Ящичек "
        "пуст: последний фитиль, видимо, Фома держит при себе."))},
    outdoor=False)

_loc("tavern", "Трактир «Тюлень»", "indoor", (
    "В трактире тепло и шумно. Низкий потолок закопчён, на стенах развешаны старые вёсла, "
    "спасательные круги и засушенная рыба-луна. За длинными столами сидят рыбаки, кто-то играет "
    "в карты, кто-то дремлет над кружкой. Пахнет щами, табаком и мокрой шерстью. За стойкой "
    "хлопочет хозяйка. Узкая скрипучая лестница ведёт наверх, к комнатам для постояльцев, а в полу "
    "за стойкой — люк в погреб. Выход — на запад, на набережную."),
    {"запад": _x("embank"), "вверх": _x("room", 3), "вниз": _x("cellar", 3)},
    {"board": _f("доска на стене", ["доск", "объявл", "стен"], (
        "На стене у двери — доска для записок и объявлений."), read="__DOC_TAVERN_BOARD__")},
    outdoor=False)

_loc("room", "Комната над трактиром", "indoor", (
    "Маленькая комната под самой крышей, которую для вас сняли от имени управления маяков. Кровать "
    "с лоскутным одеялом, умывальник, стол у окна и табурет. Потолок скошен, и у окна приходится "
    "пригибаться. Из окна видны залив, крыши посёлка и далёкий погасший маяк. Лестница ведёт вниз, "
    "в трактир."), {"вниз": _x("tavern", 3)},
    {"window": _f("окно", ["окн"], (
        "Из окна видна вся Кривая коса: от посёлка она тянется на восток длинной песчаной полосой, "
        "дважды изгибаясь, и заканчивается у маяка. Южнее изгиба в отлив открывается широкая "
        "отмель, а на ней — чёрный остов разбитой шхуны и маленький скалистый остров.")),
     "table": _f("стол", ["стол"], (
        "На столе — кувшин с водой, огарок свечи и письмо из управления маяков, адресованное "
        "вам."), read="__DOC_ORDER__")},
    outdoor=False)

_loc("cellar", "Погреб трактира", "indoor", (
    "Холодный погреб с земляным полом. Вдоль стен — бочки с капустой и огурцами, на полках — "
    "бутыли и банки. Под потолком на крюках висят окорока. В углу стоит старый сундучок "
    "смотрителя, который Агафья, по её словам, взяла на сохранение: крышка открыта, внутри "
    "какие-то бумаги и тряпьё. Лестница ведёт наверх."), {"вверх": _x("tavern", 3)},
    {"trunk": _f("сундучок смотрителя", ["сундучок", "бумаг", "тряп"], (
        "В сундучке — старая рубаха, обрывки газет и счета из лавки Кожина за керосин. Ничего "
        "ценного, кроме, может быть, листков, которые лежат сверху."))},
    outdoor=False)

_loc("square", "Площадь с колодцем", "village", (
    "Небольшая площадь в центре посёлка. Посередине — колодец под двускатной крышей, с воротом и "
    "цепью. Вокруг площади — самые важные дома Кривой Косы: на западе двухэтажная контора "
    "начальника порта с флагштоком, на востоке кузница, откуда доносится запах угольной гари "
    "(правда, сейчас там тихо). На столбе у колодца прибита доска объявлений. На юг улица спускается "
    "к набережной, на север поднимается Верхняя улица."),
    {"юг": _x("embank"), "запад": _x("office"), "восток": _x("smithy"), "север": _x("street")},
    {"notice": _f("доска объявлений", ["доск", "объявл", "столб"], (
        "На доске — несколько листков, приколотых ржавыми кнопками."), read="__DOC_SQUARE__"),
     "well": _f("колодец", ["колод", "ворот", "цеп"], (
        "Колодец глубокий, вода в нём, говорят, солоноватая — море близко. На срубе вырезаны "
        "десятки инициалов и дат."))})

_loc("office", "Контора начальника порта", "indoor", (
    "Просторная комната с большим окном на залив. У окна — стол, заваленный бумагами, на стене — "
    "морская карта залива с пометками карандашом, барометр и две таблицы приливов, приколотые "
    "рядом. Под стеклом на столе лежит реестр лодок посёлка. В углу гудит железная печка. "
    "Скрипучая лестница ведёт наверх, на второй этаж, где помещаются почта и телеграф. Выход — "
    "на восток, на площадь."), {"восток": _x("square"), "вверх": _x("post", 3)},
    {"tides": _f("таблицы приливов", ["таблиц", "прилив", "отлив"], (
        "На стене две таблицы приливов. Одна свежая, другая пожелтевшая, с загнутыми углами."),
        read="__DOC_TIDES__"),
     "register": _f("реестр лодок", ["реестр", "лодок", "лодк"], (
        "Реестр лодок посёлка под стеклом на столе, аккуратно разлинованный."),
        read="__DOC_REGISTER__"),
     "map": _f("карта залива", ["карт", "залив"], (
        "Морская карта залива. Карандашом обведена Кривая коса. Южнее её второго изгиба "
        "штриховкой отмечена отмель, и на ней крестиком — место, где лежит остов шхуны «Чайка». "
        "Южнее — Птичий остров; на его западном мысу нарисован крестик в кружке и подписано "
        "«скитъ (упразд.)». От маячного мыса к острову проведён пунктир и приписано: «вельботом, "
        "не в малую воду». Возле мыса у маяка помечено: «рифы, огонь обязателен». Рядом: «Свят. "
        "Ольга» — ждём в ночь на 17-е»."))},
    outdoor=False)

_loc("post", "Почта и телеграф", "indoor", (
    "Второй этаж конторы отдан почте. За деревянной перегородкой с окошком — стол почтмейстера, "
    "весы для посылок, сургуч, штемпели. У окна на отдельном столике стоит телеграфный аппарат "
    "Морзе с медным ключом; лента из него свисает до пола. На стене — ящики с ячейками для писем "
    "по фамилиям жителей, большинство пусты. Почтмейстер уехал в город за жалованьем, и "
    "телеграфные ленты за неделю сложены стопкой в журнал. Лестница ведёт вниз, в контору."),
    {"вниз": _x("office", 3)},
    {"telegrams": _f("журнал телеграмм", ["журнал", "телеграм", "лент"], (
        "Толстый журнал, куда вклеены телеграфные ленты за последние дни."),
        read="__DOC_TELEGRAMS__"),
     "boxes": _f("ящики для писем", ["ящик", "ячейк", "писем"], (
        "Ячейки подписаны фамилиями: Зуев, Кожин, Лапина, Кузнецов, Грач... В ячейке Кожина "
        "пусто, и на её дне пыль лежит ровным слоем — сюда давно ничего не клали. В ячейке Грача — "
        "казённый пакет Управления маяков, распечатанный: предписание, копию которого вы уже "
        "получили."))},
    outdoor=False)

_loc("smithy", "Кузница", "indoor", (
    "Кузница — низкое каменное строение с широкими воротами. Внутри темно; горн холодный, на "
    "наковальне лежат клещи, по стенам развешаны подковы, серпы, цепи и полосы железа. Пол усыпан "
    "окалиной. Мешок из-под угля в углу пуст — похоже, работать кузнецу сейчас не на чем. Выход — на "
    "запад, на площадь."), {"запад": _x("square")},
    {"forge": _f("горн", ["горн", "наковал"], (
        "Горн давно остыл. Рядом стоит пустой угольный ящик. Без угля здесь ничего не выковать."),
        desc_alt=[{"if": ["flag:smith"], "text": "Горн жарко пылает, над ним дрожит воздух. "
                   "Ермолай то и дело подбрасывает уголь из мешка."}])},
    outdoor=False)

_loc("street", "Верхняя улица", "village", (
    "Улица поднимается от площади вверх по склону. Дома здесь старше и беднее, заборы покосились, "
    "в палисадниках растут мальвы и крапива. На западе — чистенький домик с синими ставнями; "
    "говорят, там живёт вдова Аграфена. На востоке — старая школа с заколоченным крыльцом, но боковая "
    "дверь открыта. На севере улица упирается в ограду часовни. На юге — площадь."),
    {"юг": _x("square"), "запад": _x("widow"), "восток": _x("school"), "север": _x("chapel")})

_loc("widow", "Дом Аграфены", "indoor", (
    "В доме чисто и тихо. Половики, герань на подоконниках, в красном углу — икона с лампадкой. "
    "На комоде стоят фотографии в рамках: мужчина с окладистой бородой в рыбацкой куртке и он же "
    "рядом с молодой женщиной. У печи сушатся связки лука. Выход — на восток, на улицу."),
    {"восток": _x("street")},
    {"photos": _f("фотографии", ["фотограф", "комод", "рамк"], (
        "На фотографиях — Трофим, покойный муж Аграфены, рыбак. На обороте одной карточки подпись: "
        "«Трофим и Груня, 1899». Трофим погиб три года назад, во время осеннего шторма; в посёлке "
        "до сих пор спорят о том, как это случилось."))},
    outdoor=False)

_loc("school", "Старая школа", "indoor", (
    "Единственный класс старой школы давно пустует. Парты сдвинуты к стене, на них лежит пыль. На "
    "стене висит большая классная доска, на которой кто-то мелом аккуратно выписал алфавит с "
    "номерами под буквами. Над доской — выцветший портрет и карта губернии. Выход — на запад."),
    {"запад": _x("street")},
    {"board": _f("классная доска", ["доск", "алфавит", "мел"], (
        "На доске мелом выписан алфавит, и под каждой буквой — её номер. Буквы Ё на доске нет."),
        read="__DOC_ALPHABET__")},
    outdoor=False)

_loc("chapel", "Часовня", "village", (
    "Небольшая деревянная часовня на пригорке, обнесённая низкой оградой. Над входом — звонница с "
    "колоколами; когда в посёлке беда или шторм, в них звонят. На звонницу со двора ведёт крутая "
    "лесенка, огороженная жердями. Внутри горят свечи, пахнет воском. У стены часовни, прямо на "
    "земле, стоит снятый треснувший колокол. За часовней, к востоку, виднеются кресты старого "
    "кладбища. На север от часовни тропинка уходит в дюны, на юг — Верхняя улица."),
    {"юг": _x("street"), "восток": _x("cemetery"), "север": _x("dunes1"), "вверх": _x("belfry", 3)},
    {"cracked": _f("треснувший колокол", ["треснув", "колокол", "трещин"], (
        "Колокол стоит на земле у стены часовни, весь в зелёной патине и мху. Трещина идёт от края "
        "юбки почти до середины. По ободу ещё читается вылитое имя — «Утешитель» — и год: 1858. "
        "Старики говорят, что в прежние годы именно в «Утешителя» били на беду, когда лодки не "
        "возвращались, и что треснул он в ту зиму, когда звонили по троим утонувшим сразу. Теперь "
        "он молчит, и звонить в него больше нельзя."))})

_loc("belfry", "Звонница часовни", "village", (
    "Тесная площадка под двускатной крышей, открытая всем ветрам. Доски пола прогибаются, "
    "между ними видно крыльцо внизу. На толстой дубовой перекладине висят три колокола. Слева — "
    "самый большой, тёмный, с широкой юбкой; по ней вылито «Благовест» и год 1871. Посередине "
    "перекладины висит средний колокол, светлее и звонче, отлитый позже: по его поясу идут "
    "выпуклые буквы «Заступник», ниже — «дар рыбацкой артели Кривой Косы, 1896». Справа — "
    "совсем маленький колокольчик, «Вестник», тонкий и певучий, его слышно далеко над заливом. "
    "От каждого колокола свисает верёвка, привязанная внизу к перилам крыльца. Отсюда видно весь "
    "посёлок, залив и далёкий маяк. Спуститься можно вниз."), {"вниз": _x("chapel", 3)},
    {"bells": _f("колокола", ["колокол", "верёвк", "веревк", "перекладин"], (
        "Три колокола висят на одной перекладине: большой «Благовест» слева, средний «Заступник» "
        "посередине, маленький «Вестник» справа. Языки подвязаны, чтобы не звонили от ветра. На "
        "перекладине кто-то вырезал ножом крестик и дату — 1902."))})

_loc("cemetery", "Кладбище", "village", (
    "Старое кладбище на склоне, обращённом к морю. Кресты покосились, многие надписи стёрлись. "
    "Среди могил выделяется одна — ухоженная, с каменной плитой и чугунной оградкой: здесь "
    "похоронена жена смотрителя маяка. Если встать к её плите лицом, то по левую руку, в той же "
    "оградке, поднимается высокий чугунный крест с литыми якорями, а по правую — низкий серый "
    "валун, поросший рыжим лишайником, с глубоко выбитыми буквами. Чуть поодаль, у самой ограды "
    "кладбища, стоит деревянный голубец под двускатной крышей, а за ним — несколько безымянных "
    "холмиков утонувших рыбаков. Выход — на запад, к часовне; на север, в углу ограды, — "
    "сторожка."), {"запад": _x("chapel"), "север": _x("lodge")},
    {"grave": _f("могила жены смотрителя", ["могил", "плит", "марф"], (
        "Каменная плита, на ней выбита надпись. У плиты кто-то оставил засохший букет вереска."),
        read="__DOC_GRAVE__"),
     "cross": _f("чугунный крест", ["крест", "чугун", "якор"], (
        "Высокий чугунный крест с литыми якорями на концах перекладины."), read=(
        "На кресте вылито:\n\nЛОЦМАНЪ КОРНИЛИЙ ЕФИМОВИЧЪ СУХАРЕВЪ\n1830 — 1890\n\n«Провёлъ "
        "черезъ рифы Кривой косы сто сорокъ судовъ и ни одного не погубилъ». Ниже, на табличке, "
        "позже прикрученной: «Отъ учениковъ»." )),
     "boulder": _f("серый валун", ["валун", "камен", "лишайн"], (
        "Низкий серый валун, поросший рыжим лишайником. Буквы выбиты глубоко, но местами их "
        "затянуло."), read=(
        "На валуне выбито:\n\nИОНА ПАХОМОВИЧЪ ВЕРЕТЕННИКОВЪ\n1821 — 1893\n\n«Сорокъ летъ при "
        "огняхъ Беломорья. Свети, пока можешь». Под надписью кто-то процарапал маленький маяк с "
        "расходящимися лучами.")),
     "golubets": _f("деревянный голубец", ["голубец", "голубц", "крыш"], (
        "Деревянный крест-голубец под двускатной крышкой, серый от дождей."), read=(
        "На доске голубца вырезано: «Трофимъ Лапинъ. Помяни, Господи, въ море погибшихъ, тела "
        "коихъ не обретены. 1902». Ниже приписано карандашом, уже почти смытым: «Троих не "
        "бросил»."))})

_loc("lodge", "Сторожка кладбища", "indoor", (
    "Крохотная бревенчатая сторожка в углу кладбищенской ограды. Здесь живёт, когда не пьёт, "
    "кладбищенский сторож; сейчас его нет, но дверь не заперта. Внутри — лопаты, лом, бухта "
    "верёвки, на лавке тулуп, на полке — толстая книга в холщовом переплёте, куда сторож "
    "записывает погребения. На стене — план кладбища, начерченный углём на доске. Выход — на юг."),
    {"юг": _x("cemetery")},
    {"burials": _f("книга погребений", ["книг", "погребен", "холщ"], (
        "Толстая книга в холщовом переплёте, страницы разлинованы от руки."),
        read="__DOC_BURIALS__"),
     "plan": _f("план кладбища", ["план", "доск", "угл"], (
        "На доске углём начерчен план: квадрат ограды, часовня, ряды могил. У одной из клеток "
        "подписано «смотрит.» и нарисована стрелка к морю; рядом ещё две клетки, подписанные "
        "совсем неразборчиво. Сторож, видно, рисовал для себя."))},
    outdoor=False)

_loc("dunes1", "Дюны за часовней", "dunes", (
    "Тропинка от часовни выводит в дюны. Песок здесь мелкий, светлый, поросший жёсткой травой и "
    "низким шиповником. Ветер с моря гонит по склонам песчаные струйки, и следы на тропе быстро "
    "заметает. С гребня дюны видно посёлок внизу и залив. Тропа ведёт на север, в глубь дюн; на юг "
    "— к часовне."), {"юг": _x("chapel"), "север": _x("dunes2")})

_loc("dunes2", "Развилка в дюнах", "dunes", (
    "Здесь тропа разветвляется у старого покосившегося столба с указателями. Одна дощечка, "
    "указывающая на восток, гласит: «К маяку». Другая, на запад: «Роща. Мельница». Третья, на "
    "север, почти стёрта, но можно прочесть: «Обрыв». Тропа, по которой вы пришли, уходит на юг, к "
    "часовне."), {"юг": _x("dunes1"), "восток": _x("spitstart", 15), "запад": _x("grove"),
                  "север": _x("cliff")},
    {"post": _f("столб с указателями", ["столб", "указател", "дощечк"], (
        "На столбе три дощечки: «К маяку» (восток), «Роща. Мельница» (запад), «Обрыв» (север). "
        "Ниже кто-то вырезал: «До маяка час ходу, если не в прилив». Надпись явно шутливая: коса "
        "не затопляется, затопляется только отмель южнее её изгиба."))})

_loc("grove", "Сосновая роща", "dunes", (
    "За дюнами начинается низкая сосновая роща. Сосны кривые, с перекрученными стволами, "
    "причёсанные ветром в одну сторону. Под ногами хрустят шишки и сухая хвоя. Здесь тише, чем на "
    "берегу, и слышно, как стучит дятел. Тропа ведёт на север, к ветряной мельнице, чьи крылья "
    "видны над деревьями; на запад — к избушке в чаще; на юг — к роднику; на восток — обратно к "
    "развилке."), {"восток": _x("dunes2"), "север": _x("mill"), "запад": _x("hut"),
                   "юг": _x("spring")})

_loc("spring", "Родник", "dunes", (
    "В ложбине среди сосен бьёт родник. Вода в нём ледяная и чистая, вытекает из-под камня в "
    "деревянный сруб и убегает ручейком в сторону моря. Над родником — маленький деревянный "
    "навес с полочкой; на полочке стоит жестяная кружка. Говорят, смотритель маяка каждое утро "
    "ходил сюда за водой. Выход — на север, в рощу."), {"север": _x("grove")},
    {"shelf": _f("навес с полочкой", ["навес", "полочк", "кружк"], (
        "На полочке под навесом — жестяная кружка с вмятиной. Под кружкой, видно, что-то лежало: "
        "на доске остался светлый прямоугольник."))})

_loc("hut", "Избушка знахарки", "dunes", (
    "В самой чаще стоит покосившаяся избушка. Когда-то здесь жила знахарка Устинья, которая "
    "лечила весь посёлок; после её смерти избушка опустела. Дверь не заперта. Внутри — печь, лавка, "
    "стол, связки высохших трав под потолком. На столе, придавленные камнем, лежат какие-то "
    "листки. Выход — на восток, в рощу."), {"восток": _x("grove")},
    {"herbs": _f("связки трав", ["трав", "связк"], (
        "Полынь, зверобой, чабрец, мята — всё высохло до хруста. Пахнет пылью и летом."))},
    outdoor=False)

_loc("mill", "Ветряная мельница", "dunes", (
    "Старая ветряная мельница на пригорке. Крылья её перекошены, одно сломано, парусина истлела. "
    "Внутри на первом ярусе — жернова и деревянные шестерни, всё в муке пополам с пылью. Сквозь "
    "дыры в крыше падает свет. Шаткая лестница ведёт наверх, к валу крыльев. Выход — на юг, в "
    "рощу."), {"юг": _x("grove"), "вверх": _x("millup", 3)},
    {"gears": _f("жернова и шестерни", ["жернов", "шестерн"], (
        "Деревянные шестерни огромные, с зубьями из дуба. Жернова давно не вращались. На полу у "
        "жерновов видны отпечатки маленьких сапог — кто-то из детей лазил сюда недавно."))},
    outdoor=False)

_loc("millup", "Верхний ярус мельницы", "dunes", (
    "Под самой крышей мельницы тесно и ветрено. Толстый вал крыльев уходит в стену. Сквозь щели "
    "видно далеко: посёлок, залив, дюны, а на востоке — вся Кривая коса и маяк. Доски пола "
    "прогибаются под ногами. Кто-то устроил здесь себе наблюдательный пункт: на ящике — огарок "
    "свечи и старая подушка. Спуститься можно вниз."), {"вниз": _x("mill", 3)},
    {"view": _f("вид из щелей", ["вид", "щел", "кос"], (
        "Отсюда видно, как коса изгибается дважды, прежде чем дойти до маяка. У второго изгиба к "
        "югу от неё тянется отмель, а на отмели — остов шхуны и скалистый островок."))},
    outdoor=False)

_loc("cliff", "Обрыв", "dunes", (
    "Дюны обрываются к морю крутым песчаным обрывом. Внизу бьётся прибой, пена взлетает почти до "
    "края. Ветер здесь такой сильный, что приходится держаться подальше от кромки. Вдоль обрыва "
    "тянется тропа на север, к смотровой площадке; на юг — обратно к развилке."),
    {"юг": _x("dunes2"), "север": _x("lookout")},
    sea={"low": "Внизу, под обрывом, в отлив обнажилась полоса мокрого песка с разбросанными "
                "камнями.",
         "high": "Прилив бьётся о самое подножие обрыва, и брызги долетают до края."})

_loc("lookout", "Смотровая площадка", "dunes", (
    "На вершине самой высокой дюны устроена смотровая площадка с перилами и скамьёй. Отсюда "
    "рыбачки высматривают возвращающиеся лодки. Видно весь залив, посёлок, косу и маяк, и даже "
    "далёкий мыс, за которым прячутся рифы. К перилам привязан медный колокольчик. Выход — на юг."),
    {"юг": _x("cliff")},
    {"bench": _f("скамья", ["скам", "перил", "колокольчик"], (
        "На скамье вырезаны десятки имён и дат. Колокольчик на перилах звонит от ветра. На одной "
        "доске вырезано: «Здесь ждала Марфа». Марфа — так звали жену смотрителя."))})

_loc("spitstart", "Начало косы", "spit", (
    "Здесь дюны сменяются узкой песчаной косой, уходящей в море на восток. По обе стороны — вода: "
    "слева открытое море с тяжёлыми волнами, справа тихий залив. По гребню косы идёт наезженная "
    "колея. Чуть к северу, в ложбине, стоит рыбацкая хижина с сетями на заборе и дымком над трубой. "
    "На западе — дюны; на восток коса тянется к маяку."),
    {"запад": _x("dunes2", 15), "север": _x("prokhor"), "восток": _x("spit1", 15)})

_loc("prokhor", "Хижина Прохора", "indoor", (
    "Рыбацкая хижина Прохора — одна комната с печью, топчаном и столом. На стенах развешаны "
    "сети, гарпун, зюйдвестка. У двери сложены вёсла, а под лавкой стоит большой мешок угля — "
    "Прохор топит им печь в сырые дни. Пахнет рыбой и махоркой. Выход — на юг."),
    {"юг": _x("spitstart")},
    {"harpoon": _f("гарпун и зюйдвестка", ["гарпун", "зюйдвестк"], (
        "Гарпун старый, китобойный, с зазубренным наконечником. Прохор, говорят, в молодости "
        "ходил на промысел далеко на север."))},
    outdoor=False)

_loc("spit1", "Кривая коса, первый изгиб", "spit", (
    "Коса сужается, и в одном месте её ширина не больше двадцати шагов. Здесь она изгибается к "
    "северу. На песке лежат выбеленные солнцем коряги, кости рыб, обрывки водорослей. К северу, в "
    "маленькой бухте, на камнях греются тюлени. Коса продолжается на восток; на запад — её начало."),
    {"запад": _x("spitstart", 15), "восток": _x("spit2", 15), "север": _x("seals")})

_loc("seals", "Тюленья бухта", "spit", (
    "Маленькая бухта с каменистым берегом. На плоских камнях лежат серые тюлени, лениво поднимая "
    "головы при вашем приближении. Вода здесь прозрачная, видно дно с ракушками. Тюлени, похоже, "
    "привыкли к людям — сюда часто приходил смотритель. Выход — на юг, на косу."), {"юг": _x("spit1")},
    {"seals": _f("тюлени", ["тюлен"], (
        "Тюленей штук десять. Один, самый большой, со шрамом на боку, смотрит на вас с явным "
        "неодобрением."), take_fail="Тюлень фыркает и сползает в воду.")},
    sea={"low": "В отлив бухта мелеет, и между камнями остаются лужи с мелкими крабами.",
         "high": "В прилив вода заливает нижние камни, и тюлени перебираются повыше."})

_loc("spit2", "Кривая коса, второй изгиб", "spit", (
    "Второй изгиб косы — самое широкое её место. Здесь стоит покосившийся знак с надписью «Отмель». "
    "К югу от косы в малую воду открывается широкая отмель, и видно, как далеко на ней чернеет "
    "остов шхуны, а за ним — скалы Птичьего острова. Но между косой и отмелью тянется глубокая "
    "промоина, размытая штормом девятьсот второго года: вода в ней не уходит и в самый сильный "
    "отлив. Пешком с косы на отмель теперь не попасть. На восток коса тянется к маяку, на "
    "запад — к первому изгибу."),
    {"запад": _x("spit1", 15), "восток": _x("spit3", 15)},
    {"sign": _f("знак у отмели", ["знак", "надпис"], (
        "На знаке крупно написано: «Отмель. Проход с косы закрыт — промоина!». Ниже, другой "
        "краской: «На остров и к шхуне — только от острова, в малую воду, час до и час после. "
        "На остров — вельботом с маячного мыса, не в малую воду». Подпись: «Смотр. С. Грач»."))},
    sea={"low": "Сейчас отлив: к югу от косы блестит мокрым песком широкая отмель, но у самой косы "
                "по-прежнему тянется тёмная полоса глубокой промоины.",
         "high": "Сейчас вода стоит высоко: отмели к югу от косы не видно, там перекатываются "
                 "волны."})

_loc("spit3", "Конец косы", "spit", (
    "Коса заканчивается каменистым мысом, на котором стоит маяк. Здесь всё время дует ветер, "
    "и волны с грохотом разбиваются о валуны. Белая башня маяка возвышается над высокой "
    "каменной стеной маячного двора, к воротам ведёт мощёная дорожка на север. На запад коса "
    "уходит обратно к посёлку."),
    {"запад": _x("spit2", 15), "север": _x("lhbase")})

_loc("lhbase", "Ворота маячного двора", "spit", (
    "Маячный двор обнесён стеной из дикого камня в полтора человеческих роста; за ней видны "
    "белая башня, крыша дома смотрителя и мачта с реем. В стене — дубовые ворота, окованные "
    "железом. Вместо замочной скважины в них врезана секретка, какие делают архангельские "
    "мастера для судовых касс: латунная доска с тремя рядами по пять медных колёсиков с "
    "буквами. Ряды подписаны мелко: «верх», «сред.», «низ». Над воротами вмурована каменная "
    "плита с вырезанной надписью. Рядом — скамейка и пустая бочка для дождевой воды. На юг "
    "дорожка ведёт обратно к концу косы."),
    {"юг": _x("spit3"),
     "север": _x("yard", 3, ["flag:gate1_open"], "Ворота маячного двора заперты на секретку.",
                 label="маячный двор (за воротами)")},
    {"plate": _f("плита над воротами", ["плит", "надпис", "камен"], (
        "Каменная плита над воротами. Буквы вырезаны глубоко и местами подкрашены."),
        read="__DOC_PLATE__"),
     "gate": _f("ворота с секреткой", ["ворот", "секретк", "замок", "замк", "колес", "колёс"], (
        "Секретка на воротах — латунная доска с тремя рядами колёсиков: «верх», «сред.», «низ». "
        "В каждом ряду пять колёсиков с буквами, так что в ряд помещаются только первые пять "
        "букв слова; всё, что длиннее, мастер просто не ставил. Набирают по слову на ряд, сверху "
        "вниз: открыть ворота <слово> <слово> <слово>. Слово можно назвать целиком — на колёса "
        "встанут его первые пять букв."),
        desc_alt=[{"if": ["flag:gate1_open"], "text": "Ворота маячного двора открыты."}],
        lock={"kind": "phrase", "zone": "z1", "parts": 3, "flag": "gate1_open", "jam_min": 120,
              "ok": "Колёсики трёх рядов встают на место, внутри доски что-то глухо щёлкает, и "
                    "тяжёлая створка ворот подаётся под рукой.",
              "fail": "Вы выставляете буквы во всех трёх рядах и тянете створку, но ворота не "
                      "поддаются. Какое-то из слов не то.",
              "count": "На секретке три ряда колёс — нужно назвать ровно три слова, сверху вниз: "
                       "открыть ворота <слово> <слово> <слово>.",
              "jam": "После третьей неудачи колёсики секретки перестают вращаться: сработал "
                     "стопор. Похоже, механизм отпустит часа через два.",
              "blocked": "Колёсики секретки застопорены после неудачных попыток; пока они не "
                         "вращаются.",
              "ask": "Секретка открывается тремя словами, по одному на ряд, сверху вниз: "
                     "открыть ворота <слово> <слово> <слово>.",
              "already": "Ворота уже открыты."})},
    sea={"low": "В отлив у подножия мыса обнажаются чёрные камни, облепленные ракушками.",
         "high": "Прилив бьёт в камни мыса, и брызги долетают до стены маячного двора."})

# ---- zone 1: the lighthouse cape --------------------------------------------

_CUR[0] = 1

_loc("lh1", "Нижний ярус маяка", "lighthouse", (
    "Внутри маяка прохладно и гулко. Круглое помещение первого яруса занято бочками, ящиками и "
    "мотками троса. Вдоль стены вверх уходит винтовая железная лестница. В стене на востоке — "
    "низкая дверца в кладовую, а в полу — окованный люк, под которым каменные ступени уходят в "
    "подвал. На полу — следы сапог в пыли, ведущие к лестнице и обратно. Выход наружу, во двор, "
    "— через дверь."), {"наружу": _x("yard", 3), "вверх": _x("stairs", 3),
                        "восток": _x("storage", 3), "вниз": _x("lhcellar", 3)},
    {"crates": _f("бочки и ящики", ["бочк", "ящик", "трос"], (
        "Бочки из-под керосина пусты — все до одной. На ящиках надписи: «Стёкла ламповые», "
        "«Сода», «Мел». Керосина здесь нет ни капли: видно, запас кончился ещё при смотрителе."))},
    outdoor=False)

_loc("storage", "Кладовая маяка", "lighthouse", (
    "Тесная кладовая без окон. На полках — жестянки с краской, запасные стёкла, ветошь, "
    "инструменты. На верстаке у стены — тиски и разобранный механизм часового привода. Похоже, "
    "смотритель что-то чинил здесь перед тем, как исчезнуть. Выход — на запад."),
    {"запад": _x("lh1", 3)},
    {"bench": _f("верстак", ["верстак", "тиск", "механизм"], (
        "На верстаке — тиски и детали часового привода, который вращает линзу. Рядом нацарапано "
        "мелом: «Рукоять лопнула. Отнести Ермолаю — пусть сварит. Угля у него нет, пусть возьмёт у "
        "Прохора». Почерк торопливый."))},
    outdoor=False)

_loc("stairs", "Винтовая лестница", "lighthouse", (
    "Железная винтовая лестница закручивается вверх вдоль стены башни. Ступени гулко отзываются "
    "на каждый шаг. Через узкие окна-бойницы видно море то с одной, то с другой стороны. На "
    "площадке посередине — дверь в вахтенную комнату смотрителя. Лестница ведёт вниз и вверх."),
    {"вниз": _x("lh1", 3), "вверх": _x("watchroom", 3)},
    {"steps": _f("ступени", ["ступен", "окн", "бойниц"], (
        "Ступеней много, и на каждой десятой мелом выведена цифра: 10, 20, 30... Последняя "
        "отметка — 90."))},
    outdoor=False)

_loc("watchroom", "Вахтенная комната", "lighthouse", (
    "Комната смотрителя: узкая койка, стол, стул, шкаф, печка-буржуйка. На столе раскрыт "
    "вахтенный журнал, рядом — чернильница и перо. На стене висит барометр и выцветшая "
    "фотография женщины в платке. Всё выглядит так, будто хозяин вышел на минуту и не вернулся. "
    "Лестница ведёт вниз и вверх, в фонарную."),
    {"вниз": _x("stairs", 3), "вверх": _x("lamproom", 3)},
    {"logbook": _f("вахтенный журнал", ["журнал", "вахтен"], (
        "Толстый вахтенный журнал в кожаном переплёте. Записи ровным почерком: дата, погода, время "
        "зажжения и гашения огня."),
        read="__DOC_LOGBOOK__", on_read=["reveal:cipher", "set:cipher_found"],
        read_need=["!flag:cipher_found"],
        read_note="Между последними страницами журнала обнаруживается сложенная записка, "
                  "исписанная странными буквами. Она выпадает на стол.",
        on_examine=["reveal:cipher", "set:cipher_found"], examine_need=["!flag:cipher_found"],
        examine_note="Когда вы листаете журнал, между последними страницами обнаруживается "
                     "сложенная записка, исписанная странными буквами. Она выпадает на стол."),
     "cupboard": _f("шкаф со старыми журналами", ["шкаф", "стар", "журнал", "1902"], (
        "В шкафу — старые вахтенные журналы по годам. Один, за 1902 год, заложен на октябре "
        "сухой веточкой вереска."), read="__DOC_STORM1902__"),
     "photo": _f("фотография", ["фотограф", "женщин"], (
        "Выцветшая фотография женщины в платке. На обороте: «Марфа. 1890»."))},
    outdoor=False)

_loc("lamproom", "Фонарная", "lighthouse", (
    "Фонарное помещение на самом верху башни. В центре на чугунном постаменте стоит огромная "
    "линза из множества стеклянных колец и призм; внутри неё — лампа с широкой горелкой. Под "
    "постаментом — часовой механизм, который вращает линзу, с гнездом для заводной рукояти. Стены "
    "почти целиком стеклянные. Дверца ведёт наружу, на галерею; лестница — вниз."),
    {"вниз": _x("watchroom", 3), "наружу": _x("gallery", 2)},
    {"lens": _f("линза", ["линз", "оправ", "стекл"], (
        "Линза собрана из десятков стеклянных колец и призм в латунной оправе. Стёкла покрыты "
        "густой копотью: последний раз лампа коптила, и никто не протёр их. В одном месте оправа "
        "пуста — не хватает одной призмы; без неё свет уйдёт в сторону."),
        desc_alt=[{"if": ["flag:prism_in"], "text": "Линза чиста и цела: все призмы на месте, "
                   "стекло блестит."},
                  {"if": ["flag:lens_clean"], "text": "Стекло линзы протёрто и блестит, но в "
                   "оправе по-прежнему пустует место одной призмы."}]),
     "lamp": _f("лампа с горелкой", ["ламп", "горелк", "огон", "маяк"], (
        "Лампа керосиновая, с широкой горелкой. Фитиля в горелке нет, резервуар сухой."),
        desc_alt=[{"if": ["flag:fuel_in"], "text": "В горелке новый фитиль, резервуар полон "
                   "керосина. Лампу осталось только зажечь."},
                  {"if": ["flag:wick_in"], "text": "В горелке новый фитиль, но резервуар лампы "
                   "сухой."}]),
     "mech": _f("часовой механизм", ["механизм", "привод", "гнезд", "часов"], (
        "Механизм вращения линзы: гири, шестерни, регулятор. Завести его можно только рукоятью, "
        "которой здесь нет."),
        desc_alt=[{"if": ["flag:wound"], "text": "Механизм заведён и мерно тикает, линза "
                   "поворачивается."}])},
    outdoor=False,
    dyn=[{"if": ["flag:lit"], "text": "Лампа горит ровным ярким пламенем, и луч маяка медленно "
          "обходит море."}])

_loc("gallery", "Галерея маяка", "lighthouse", (
    "Узкая железная галерея опоясывает фонарное помещение снаружи. Ветер здесь сбивает с ног, "
    "приходится держаться за перила. Внизу — весь мир: коса, посёлок, дюны, открытое море с белыми "
    "барашками, на юге — отмель с остовом шхуны и Птичий остров с серыми стенами заброшенного "
    "скита на западном мысу, а на востоке, за мысом, — пенная полоса над рифами. "
    "Дверца ведёт обратно внутрь."), {"внутрь": _x("lamproom", 2)},
    {"reefs": _f("рифы", ["риф", "мыс", "мор"], (
        "За мысом, в полумиле от берега, вода кипит над подводными камнями. Если корабль пойдёт "
        "ночью без огня маяка, его вынесет прямо на эти рифы."))},
    sea={"low": "В отлив к югу от косы открылась отмель: видно, как по ней можно дойти до остова "
                "шхуны и до острова.",
         "high": "Вода стоит высоко, отмели не видно: над ней только остов шхуны торчит из воды."})

_loc("yard", "Маячный двор", "cape", (
    "Двор за стеной вымощен плитняком, между плитами пробивается жёсткая трава. Посередине "
    "поднимается белая башня маяка; её дверь во двор не заперта, только прикрыта. К востоку — "
    "дом смотрителя, приземистый, под железной крышей, с палисадником, где доцветают золотые "
    "шары. К западу — мастерская с широкими воротами, к северу, ближе к обрыву, — низкое "
    "кирпичное здание туманной станции с раструбом ревуна. У стены стоит собачья будка, а рядом "
    "с ней — железная цистерна для дождевой воды. Ворота на юг ведут обратно к косе."),
    {"юг": _x("lhbase", 3), "внутрь": _x("lh1", 3), "восток": _x("khall", 3),
     "запад": _x("workshop", 3), "север": _x("fog", 3)},
    {"kennel": _f("собачья будка", ["будк", "собач", "цеп"], (
        "Будка сколочена крепко, крыша обита толем. Над лазом вырезано: «Дозоръ». Миска пуста, "
        "цепь оборвана у самого ошейника — пёс, видно, рванулся и убежал, когда хозяин не "
        "вернулся. В посёлке, говорят, его видели у трактира: кормится у Агафьи и к маяку не "
        "идёт.")),
     "cistern": _f("цистерна", ["цистерн", "дождев"], (
        "Железная цистерна почти полна дождевой водой. На боку масляной краской: «Для питья не "
        "брать — для мытья стёкол». Вода чистая, но тёплая и пахнет железом."))})

_loc("workshop", "Мастерская", "indoor", (
    "Мастерская пахнет олифой, стружкой и машинным маслом. Вдоль стен — верстаки, над ними на "
    "гвоздях развешаны пилы, рубанки, стамески, гаечные ключи, все на своих местах, обведённые "
    "краской по контуру, чтобы сразу было видно, чего не хватает. Не хватает немногого: пустует "
    "контур большого молотка и двух каких-то изогнутых деталей, похожих на рога. На токарном "
    "станке закреплена недоточенная втулка. На стене — чертёж казённого вельбота с размерами. "
    "На восток — двор, на север через низкую дверь — керосиновый склад."),
    {"восток": _x("yard", 3), "север": _x("oilstore", 3)},
    {"drawing": _f("чертёж вельбота", ["чертёж", "чертеж", "вельбот"], (
        "Чертёж шестивёсельного вельбота Управления маяков: обводы, шпангоуты, банки. Внизу "
        "рукой смотрителя приписано: «Уключины бронзовые, тяжёлые, литьё Архангельского завода. "
        "Без них на вельботе грести нечем — в простые гнёзда весло не встанет. Хранить отдельно, "
        "чтобы не угнали». Изогнутые контуры на стене — как раз размер этих уключин.")),
     "tools": _f("инструменты", ["инструмент", "пил", "рубанк", "контур"], (
        "Каждый инструмент висит на своём гвозде внутри контура, обведённого белой краской. "
        "Пустой контур молотка подписан «у Ермолая», пустые изогнутые — «уключины вельб. — "
        "убраны». Смотритель любил порядок до педантичности."))},
    outdoor=False)

_loc("oilstore", "Керосиновый склад", "indoor", (
    "Сводчатое помещение из красного кирпича, наполовину врытое в землю, чтобы летом не нагревалось. "
    "Здесь держат горючее для маяка. Вдоль стен в два яруса лежат железные бочки, на каждой мелом "
    "проставлена дата, когда её вскрыли. Все бочки гулкие и пустые. В углу — медная воронка, "
    "мерные кружки и насос. У двери на гвозде висит конторская книга расхода. Выход — на юг, в "
    "мастерскую."), {"юг": _x("workshop", 3)},
    {"barrels": _f("железные бочки", ["бочк", "бочек"], (
        "Бочки пустые все до одной. На последней мелом: «вскр. 28 авг.», и ниже: «до дна 6 сент.»."
        "")),
     "oilbook": _f("книга расхода керосина", ["книг", "расход"], (
        "Конторская книга расхода горючего, разлинованная по дням."), read="__DOC_OILBOOK__")},
    outdoor=False)

_loc("fog", "Туманная станция", "indoor", (
    "В низком кирпичном здании помещается туманная станция: ревун с огромными кожаными мехами, "
    "которые раскачивают вручную рычагом, и медный раструб, выведенный через стену к морю. В "
    "туман, когда огня не видно, ревун даёт сигналы, и суда по ним держатся подальше от рифов. "
    "На стене — наставление по подаче сигналов в застеклённой рамке и доска с расписанием "
    "вахт, исписанная мелом. Выходы: на юг — во двор, на восток — к метеорологической площадке, "
    "на север — к сигнальной мачте на самом краю мыса."),
    {"юг": _x("yard", 3), "восток": _x("meteo", 3), "север": _x("mast", 3)},
    {"horn": _f("ревун", ["ревун", "мех", "раструб", "рычаг"], (
        "Меха ревуна рассохлись по краям, но держат. Рычаг ходит туго. Если качнуть его, раструб "
        "издаёт низкий, долгий, почти звериный рёв, от которого дрожит стекло в окне.")),
     "manual": _f("наставление по сигналам", ["наставлен", "рамк", "сигнал"], (
        "Наставление в застеклённой рамке."), read="__DOC_FOGMANUAL__"),
     "watches": _f("доска с расписанием вахт", ["доск", "вахт", "расписан", "мел"], (
        "На доске мелом — расписание вахт на сентябрь. Везде одна фамилия: «Грач». В клетке 7 "
        "сентября стоит крестик и приписано: «на о-в, с Кирьяном»; дальше клетки пусты."))},
    outdoor=False)

_loc("meteo", "Метеорологическая площадка", "cape", (
    "Огороженная штакетником площадка на ровном месте мыса. Белая решётчатая будка на ножках — в "
    "ней термометры; рядом дождемер, похожий на ведро с воронкой, и высокий шест с флюгером и "
    "доской Вильда, которая показывает силу ветра. Трава вокруг выкошена, штакетник подкрашен. "
    "Смотритель трижды в день записывал показания в журнал, который держал тут же, в ящике под "
    "будкой. Выход — на запад, к туманной станции."), {"запад": _x("fog", 3)},
    {"booth": _f("будка с термометрами", ["будк", "термометр", "ящик"], (
        "В будке — сухой и смоченный термометры, минимальный и максимальный. В ящике под будкой — "
        "журнал наблюдений в клеёнчатой обложке."), read="__DOC_METEO__"),
     "vane": _f("флюгер", ["флюгер", "шест", "доск", "вильд"], (
        "Флюгер показывает северо-западный ветер. Доска Вильда отклоняется до третьего штифта: "
        "ветер свежий."))})

_loc("mast", "Сигнальная мачта", "cape", (
    "На самом краю мыса, над обрывом, стоит сигнальная мачта с реем. С рея свисают фалы; флагов "
    "на них нет — флаги хранятся в ящике у подножия мачты, свёрнутые и подписанные. Ветер здесь "
    "такой, что трудно устоять, и мачта поёт на все голоса. Вниз, к морю, ведут вырубленные в "
    "скале ступени. На юг — туманная станция. Отсюда хорошо виден Птичий остров: серые стены "
    "скита на его западном мысу, чёрный остов шхуны на отмели и узкий пролив между мысом и "
    "островом."),
    {"юг": _x("fog", 3), "вниз": _x("capesteps", 5)},
    {"flags": _f("ящик с флагами", ["ящик", "флаг"], (
        "В ящике — флаги международного свода, каждый свёрнут и подписан. Сверху лежит "
        "таблица сигналов для судов, проходящих мыс."), read="__DOC_FLAGS__")},
    sea={"low": "Внизу, в проливе между мысом и островом, сейчас малая вода: из ила торчат камни, "
                "и видно, что на лодке здесь не пройти.",
         "high": "Пролив между мысом и островом полон воды; волны идут ровно, без бурунов."})

_loc("capesteps", "Ступени в скале", "cape", (
    "Ступени вырублены прямо в скале мыса и спускаются к морю крутыми зигзагами. Сбоку натянут "
    "трос вместо перил, кое-где он проржавел до красной трухи. Ступени мокрые от брызг и "
    "скользкие от птичьего помёта. В одном месте в скале выбит крест и под ним буквы «И. П. В. "
    "1887» — метка старого смотрителя. Вверх — к сигнальной мачте, вниз — к маячной пристани."),
    {"вверх": _x("mast", 5), "вниз": _x("landing", 5)},
    {"mark": _f("выбитый крест", ["крест", "метк", "букв"], (
        "Крест выбит неглубоко, но аккуратно. Буквы «И. П. В.» и год «1887». Тот, кто выбивал, "
        "явно торопился закончить к какому-то важному дню."))})

_loc("landing", "Маячная пристань", "cape", (
    "Внизу, в маленькой бухте под мысом, устроена пристань: бревенчатый ряж, засыпанный камнем, "
    "и дощатый настил на нём. К ряжу вбиты чугунные кнехты, на одном ещё висит обрывок "
    "швартова. Над самой водой к скале прилепился эллинг — сарай с широкими воротами, из "
    "которого по бревенчатому слипу спускают на воду маячный вельбот. Ворота эллинга "
    "приоткрыты. Отсюда пролив к Птичьему острову виден как на ладони. Вверх по ступеням — "
    "сигнальная мачта; внутрь — эллинг."),
    {"вверх": _x("capesteps", 5), "внутрь": _x("boatshed", 2)},
    {"bollards": _f("кнехты", ["кнехт", "швартов", "обрывок"], (
        "Обрывок швартова перетёрт о камень, а не отрезан. Кто-то швартовался здесь недавно и "
        "уходил в спешке, или лодку сорвало."))},
    sea={"low": "Сейчас малая вода: бухта обмелела, слип обнажился до самого конца, а пролив к "
                "острову превратился в поле ила и камней.",
         "high": "Вода стоит высоко, плещет у самого настила; пролив к острову открыт."})

_loc("boatshed", "Эллинг", "indoor", (
    "В эллинге сумрачно, сквозь щели в досках пробиваются полосы света и видна вода под слипом. "
    "На тележке, на бревенчатом слипе, стоит казённый шестивёсельный вельбот, выкрашенный в "
    "белое, с синей полосой по борту и надписью «Управл. маяковъ № 3». Шесть вёсел лежат вдоль "
    "банок. Но гнёзда уключин в планширях пусты, а тележка прикована к рыму в полу тяжёлой цепью. "
    "На цепи — латунная коробка секретки с тремя рядами по пять колёсиков, точно такая же, как "
    "на воротах двора. Ворота эллинга выходят к пристани; если спустить вельбот, можно идти "
    "на юг, через пролив, к Птичьему острову."),
    {"наружу": _x("landing", 2),
     "юг": {"to": "isl_bay", "cost": 40, "label": "на вельботе к Птичьему острову",
                "checks": [
                    ["flag:g2_open", "Тележка с вельботом прикована цепью к рыму; секретка на цепи "
                                     "заперта."],
                    ["flag:oars_in", "Гнёзда уключин пусты: без уключин вёсла не встанут, и "
                                     "грести на вельботе нечем."],
                    ["!low", _ROW_FAIL]]}},
    {"whaleboat": _f("вельбот", ["вельбот", "лодк", "весл", "планшир"], (
        "Вельбот крепкий, недавно просмолённый, вёсла целы. В планширях — шесть пустых бронзовых "
        "гнёзд под уключины; уключины кто-то снял и унёс. Без них на вельботе не выгрести."),
        desc_alt=[{"if": ["flag:oars_in"], "text": "Вельбот готов: уключины стоят в гнёздах, "
                   "вёсла уложены вдоль банок."}]),
     "chain": _f("цепь с секреткой", ["цеп", "секретк", "замок", "замк", "колес", "колёс"], (
        "Цепь продета через рым в полу и через скобу тележки. На ней висит латунная коробка "
        "секретки: три ряда — «верх», «сред.», «низ», — по пять колёсиков с буквами. Набирают, "
        "как на воротах двора: открыть цепь <слово> <слово> <слово>, сверху вниз; в ряд встают "
        "первые пять букв каждого слова."),
        desc_alt=[{"if": ["flag:g2_open"], "text": "Цепь снята с рыма и лежит у стены."}],
        lock={"kind": "phrase", "zone": "z2", "parts": 3, "flag": "g2_open", "jam_min": 120,
              "ok": "Колёсики встают на место, секретка раскрывается, и цепь со звоном "
                    "сползает с рыма. Тележку с вельботом теперь можно спустить по слипу.",
              "fail": "Вы выставляете все три ряда и дёргаете секретку, но она не "
                      "раскрывается. Какое-то из слов не то.",
              "count": "В секретке три ряда колёс — нужно ровно три слова, сверху вниз: "
                       "открыть цепь <слово> <слово> <слово>.",
              "jam": "После третьей неудачи колёсики застопорило. Механизм отпустит часа "
                     "через два.",
              "blocked": "Колёсики секретки застопорены после неудачных попыток; пока они не "
                         "вращаются.",
              "ask": "Секретка открывается тремя словами, по одному на ряд, сверху вниз: "
                     "открыть цепь <слово> <слово> <слово>.",
              "already": "Цепь уже снята."})},
    outdoor=False)

_loc("khall", "Сени дома смотрителя", "indoor", (
    "Сени просторные, холодные, пахнут сухим деревом и дёгтем. На вешалке — брезентовый "
    "плащ-дождевик, зюйдвестка и рабочая куртка с медными пуговицами Управления маяков. Под "
    "вешалкой — высокие сапоги, одни в засохшей глине, другие начищены. На полке — фонарь, "
    "моток бечёвки, связка ключей без бирок. Двери ведут: на север — в горницу, на восток — на "
    "кухню, на юг — в кабинет смотрителя; приставная лестница поднимается на чердак; на запад — "
    "выход во двор."),
    {"запад": _x("yard", 3), "север": _x("kroom", 2), "восток": _x("kkitchen", 2),
     "юг": _x("kstudy", 2), "вверх": _x("kattic", 3)},
    {"coats": _f("вешалка с одеждой", ["вешалк", "плащ", "куртк", "зюйдвестк", "сапог"], (
        "В кармане куртки — огрызок карандаша и квитанция лавки Кожина на два бидона керосина, "
        "«в долг». Сапоги в глине — те, в которых смотритель ходил по посёлку; начищенные — "
        "парадные, для приезда начальства. Морских сапог, высоких, до бедра, на месте нет: в них "
        "он, видно, и ушёл.")),
     "keys": _f("связка ключей", ["ключ", "связк"], (
        "Связка ключей без бирок: от шкафов, от сундуков, от кладовой. Ни один не подходит к "
        "секреткам — у тех ключа нет вовсе, они открываются только словами."))},
    outdoor=False)

_loc("kroom", "Горница", "indoor", (
    "Горница чистая и светлая, в два окна на море. Широкая кровать под лоскутным одеялом, "
    "комод, стол с вышитой скатертью, лавки вдоль стен. В красном углу — киот с образом Николая "
    "Чудотворца и неугасимая лампадка, давно, впрочем, погасшая. На стене в рамках — "
    "фотографии. На комоде — шкатулка для рукоделия с незаконченной вышивкой: видно, что к ней "
    "не прикасались много лет, но и убрать её никто не решился. Выход — на юг, в сени."),
    {"юг": _x("khall", 2)},
    {"photos": _f("фотографии в рамках", ["фотограф", "рамк"], (
        "На одной фотографии — молодые Савелий и Марфа в лодке у причала; на корме можно "
        "разобрать буквы «...АСАТК...». Подпись: «1889, первый выход». На другой — старик с "
        "окладистой белой бородой в форменной фуражке, а рядом юноша с худым серьёзным лицом; "
        "подпись: «Иона Пахомовичъ и я, осень 1887». На третьей — белая башня маяка в лесах, "
        "подпись: «перестройка, 1904».")),
     "sewing": _f("шкатулка с вышивкой", ["шкатулк", "вышивк", "рукодел"], (
        "Незаконченная вышивка на полотенце: маяк, море и летящая птица, а по краю начаты буквы: "
        "«ГОРИ ГОРИ МОЯ...». Нитки выцвели. Иголка так и воткнута в ткань.")),
     "dresser": _f("комод", ["комод", "лент", "писем", "письм"], (
        "В верхнем ящике комода, под стопкой полотенец, — пачка писем, перевязанная выцветшей "
        "голубой лентой."), read="__DOC_MARFA__"),
     "icon": _f("киот с образом", ["киот", "образ", "икон", "лампадк"], (
        "Образ Николая Чудотворца, покровителя плавающих, в простом деревянном киоте. Лампадка "
        "сухая. За киотом заткнута веточка вербы и сложенная бумажка — поминальная записка: "
        "«о упокоении Марфы, Ионы, Трофима»."))},
    outdoor=False)

_loc("kstudy", "Кабинет смотрителя", "indoor", (
    "Узкая комната с одним окном на рифы. Всё здесь подчинено делу: конторка, за которой пишут "
    "стоя, стол с зелёным сукном, шкаф с казёнными книгами, на стене — большая карта залива и "
    "барометр-анероид. На столе, придавленная чернильницей, лежит тетрадь в клеёнчатой обложке, "
    "исписанная мелким почерком. В ящике стола — пачка писем, перевязанная бечёвкой. На "
    "подоконнике — медная подзорная труба на треноге, направленная на Птичий остров. Выход — на "
    "север, в сени."), {"север": _x("khall", 2)},
    {"notebook": _f("тетрадь в клеёнке", ["тетрад", "клеёнк", "клеенк"], (
        "Тетрадь в клеёнчатой обложке — личные записи смотрителя, не казённый журнал."),
        read="__DOC_NOTEBOOK__"),
     "letters": _f("пачка писем", ["писем", "письм", "пачк", "ящик"], (
        "Пачка писем, перевязанная бечёвкой: переписка с Управлением маяков и несколько личных."),
        read="__DOC_KLETTERS__"),
     "shelf": _f("шкаф с казёнными книгами", ["шкаф", "книг", "наставлен"], (
        "В шкафу — казённые книги: «Наставление смотрителям маячных огней», своды сигналов, "
        "лоции Белого моря. Наставление заложено на одной странице полоской бумаги."),
        read="__DOC_INSTRUCTION__"),
     "scope": _f("подзорная труба на треноге", ["труб", "подзорн", "тренож", "треног"], (
        "Труба наведена на западный мыс Птичьего острова. В окуляр видны серые стены скита, "
        "купол без креста, а у ворот — что-то светлое, будто кусок холста, привязанный к решётке. "
        "Смотритель, видно, подолгу глядел туда отсюда."))},
    outdoor=False)

_loc("kkitchen", "Кухня", "indoor", (
    "Кухня с большой русской печью, в которой давно не топили. На шестке — чугунок с засохшей "
    "кашей, на столе — хлеб, превратившийся в камень, и кружка с недопитым чаем, подёрнутым "
    "плёнкой. На стене — отрывной календарь. У двери на гвоздике — записка. Всё говорит о том, "
    "что хозяин ушёл ненадолго и собирался вернуться к ужину. На запад — сени, на восток — "
    "дверь в огород."), {"запад": _x("khall", 2), "восток": _x("garden", 2)},
    {"calendar": _f("отрывной календарь", ["календар"], (
        "Последний неоторванный листок — 7 сентября, четверг. Восход 4.52, заход 18.41 — "
        "календарь петербургский, к здешним местам не очень подходит. На обороте предыдущего "
        "листка карандашом: «керосин — Чайка, трюм; вельбот — не забыть уключины!»")),
     "note": _f("записка у двери", ["записк", "гвозд"], (
        "Записка на гвоздике, почерк смотрителя: «Ушёл на остров, к Варсонофию и на "
        "«Чайку». К вечерней воде буду. Если не вернусь — ищи у старца. С. Г.»"))},
    outdoor=False)

_loc("garden", "Огород смотрителя", "cape", (
    "За домом, под защитой стены, — огород: грядки картошки, наполовину выкопанной, капуста, "
    "лук, заросли укропа, ушедшего в семена. Посередине растёт большая старая рябина, вся в "
    "гроздьях, и ветки её легли на крышу сарайчика с дровами. Между грядками — дорожка из "
    "плоских камней. На юг дорожка ведёт к бане, на запад — дверь на кухню."),
    {"запад": _x("kkitchen", 2), "юг": _x("bath", 2)},
    {"potatoes": _f("грядки", ["грядк", "картош", "капуст"], (
        "Картошку начали копать и бросили: лопата воткнута в землю посреди грядки, рядом — "
        "полкорзины клубней. Капуста крепкая, кочаны уже завились."))})

_loc("bath", "Баня", "cape", (
    "Маленькая чёрная баня стоит в самом углу двора, у стены. Внутри — каменка, полок, кадка с "
    "водой, на стене — сухие веники. Сама банька ничем не примечательна, но за ней, между её "
    "задней стенкой и оградой, есть тихий уголок, куда не достаёт ветер. Там растут две дерева: "
    "старая рябина с корявым стволом и молодая тонкая берёзка. Под каждым деревом — по холмику, "
    "обложенному камнями, и на каждом — дощечка на колышке. На север — огород."),
    {"север": _x("garden", 2)},
    {"mound_rowan": _f("холмик под рябиной", ["холмик", "рябин"], (
        "Холмик под рябиной обложен белыми окатышами."), read=(
        "На дощечке выжжено: «ПОЛКАНЪ. 1890 — 1903. Верный другъ, сторожъ огня». Ниже вырезано "
        "мельче: «В шторм 902 г. выл на галерее всю ночь».")),
     "mound_birch": _f("холмик под берёзой", ["холмик", "берёз", "берез"], (
        "Холмик под берёзкой совсем маленький."), read=(
        "На дощечке: «Буянъ, щенокъ. 1904». Больше ничего не написано.")),
     "stove": _f("каменка", ["каменк", "полок", "веник", "кадк"], (
        "Каменка сложена из серых голышей. На полке сохнут веники — берёзовые и можжевеловые."))})

_loc("kattic", "Чердак дома смотрителя", "indoor", (
    "На чердаке сухо и тепло, пахнет пылью, нагретым железом крыши и травами. Под стропилами на "
    "натянутых бечёвках висят пучки трав, и у каждого места свой запах. У слухового окна — "
    "полынь, серая и горькая. У печной трубы — мята и душица, их Марфа сушила для чая. Под "
    "самым скатом стоит окованный сундук Марфы, и над ним висят тёмные пучки багульника с "
    "кожистыми, будто ржавыми снизу листьями — от них у чердака тяжёлый дурманный дух. Чуть "
    "дальше, у фронтона, стоит старый матросский сундук смотрителя, а над ним — пижма с жёлтыми "
    "пуговками соцветий. Лестница ведёт вниз, в сени."), {"вниз": _x("khall", 3)},
    {"herbs": _f("пучки трав", ["трав", "пучк", "бечёвк", "бечевк"], (
        "Пучки подвешены аккуратно, на каждой бечёвке — бирка с буквой: «М.» над сундуком Марфы "
        "и у трубы, «С.» — над матросским сундуком. Травы над сундуком Марфы, видно, меняли "
        "много лет подряд одни и те же, а пижму повесил кто-то другой и позже.")),
     "marfa_chest": _f("сундук Марфы", ["сундук", "марф", "окован"], (
        "Сундук Марфы не заперт. В нём аккуратно сложено бельё, полотенца, праздничный сарафан. "
        "Всё переложено сухими веточками с кожистыми листьями, и от белья тянет тем же тяжёлым "
        "запахом, что висит под скатом. Сверху лежит записочка её рукой: «Моль не любит того, "
        "что висит над сундуком. Полынь — от дурного глаза, мята — к чаю». Бельё, видно, не "
        "трогали с её смерти.")),
     "sea_chest": _f("матросский сундук", ["сундук", "матросск", "смотрит", "зелён", "зелен"], (
        "Старый матросский сундук смотрителя, крашеный зелёной краской. В нём — старые казённые "
        "журналы, связанные бечёвкой по годам. Верхний, самый ветхий, подписан «1887»."),
        read="__DOC_JOURNAL1887__")},
    outdoor=False)

_loc("lhcellar", "Подвал маяка", "indoor", (
    "Под башней — круглый подвал со сводчатым потолком. Здесь холодно и сыро, со стен сочится "
    "вода, и на полу стоят лужи. Большую часть подвала занимает кирпичная цистерна для пресной "
    "воды с деревянной крышкой. У стены — пустой угольный ларь, а в нише — ряд старых "
    "жестяных бидонов из-под керосина, все пустые и помятые. Каменные ступени ведут вверх, "
    "в нижний ярус маяка."), {"вверх": _x("lh1", 3)},
    {"cans": _f("старые бидоны", ["бидон", "жестян", "ниш"], (
        "Бидоны пустые, на некоторых — клеймо «Керосинъ. Нобель», такое же, как на грузе шхуны "
        "«Чайка». На одном мелом: «последний — 6 сент.»."), take_fail="Пустые бидоны вам ни к чему."),
     "tank": _f("цистерна", ["цистерн", "крышк"], (
        "Цистерна полна тёмной холодной воды. На крышке выцарапаны отметки уровня по месяцам."))},
    outdoor=False)

# ---- zone 2: Bird Island, the flat and the wreck -----------------------------

_CUR[0] = 2

_loc("flat", "Отмель", "flat", (
    "Вы на широкой отмели к северу от острова. Под ногами — плотный мокрый песок, изрезанный "
    "ручейками, по которым вода стекает обратно в море. Повсюду лужи с мелкими рыбками, "
    "раковины, пучки водорослей. На востоке из песка поднимается чёрный остов шхуны, на юге — "
    "скалы Птичьего острова, над которыми кружат чайки. Далеко на севере видна Кривая коса, но "
    "между ней и отмелью темнеет глубокая промоина. На запад отмель переходит в высокую "
    "песчаную банку. Помните: вода вернётся."),
    {"юг": _x("island", 15, ["low"], "Между вами и островом — глубокая вода. Ждите отлива."),
     "восток": _x("wreck", 15, ["low"], "Между вами и шхуной — глубокая вода. Ждите отлива."),
     "запад": _x("sandbank", 5)},
    sea={"low": "Сейчас отлив, и отмель открыта. Но горизонт уже темнеет от приближающейся воды.",
         "high": "Вода прибыла: вокруг плещутся волны, песок едва виден под водой, стоять можно "
                 "только на самом высоком месте."})

_loc("sandbank", "Песчаная банка", "flat", (
    "Высокая песчаная банка, которую не заливает даже в полную воду: на ней растёт жёсткая "
    "осока и гнездятся кулики. Посреди банки, наполовину занесённый песком, лежит остов "
    "небольшой рыбацкой лодки — рёбра шпангоутов торчат, как кости. На уцелевшей доске борта "
    "ещё видны буквы, выведенные когда-то белилами. Вокруг лодки кто-то воткнул в песок "
    "несколько палок с выцветшими тряпицами, словно отметил место. На восток — отмель."),
    {"восток": _x("flat", 5)},
    {"boat": _f("остов лодки", ["остов", "лодк", "доск", "букв"], (
        "На доске борта читается: «...СТОЧК...». Больше ничего не разобрать. Днище проломлено "
        "снизу, словно лодку ударило о камень вверх килем. Тряпицы на палках — белые и красные, "
        "поминальные, такие рыбачки вяжут на кресты тем, чьих тел не нашли."))},
    sea={"low": "Вокруг банки блестит мокрая отмель.",
         "high": "Банка стала островком: со всех сторон вода, до шхуны и до острова не дойти."})

_loc("wreck", "Палуба шхуны «Чайка»", "flat", (
    "Остов купеческой шхуны «Чайка», разбившейся здесь три осени назад. Мачты сломаны, борта "
    "проломлены, палуба перекошена так, что ходить приходится, держась за леера. На корме ещё "
    "видна надпись «ЧАЙКА. Архангельскъ». Через пролом в палубе можно спуститься в трюм; дверь "
    "кормовой каюты висит на одной петле, и можно войти внутрь; ближе к носу — люк в кубрик "
    "команды. На запад — отмель."),
    {"запад": _x("flat", 15, ["low"], "Вокруг шхуны глубокая вода. До отлива отсюда не уйти."),
     "вниз": _x("hold", 3), "внутрь": _x("cabin", 2), "север": _x("fore", 2)},
    {"stern": _f("надпись на корме", ["надпис", "корм"], (
        "«ЧАЙКА. Архангельскъ». Шхуна купеческая, не рыбацкая; она шла с грузом керосина и почты, "
        "когда её в шторм выбросило на отмель."))},
    sea={"low": "Отлив: шхуна стоит на песке, вокруг неё — лужи и ручейки.",
         "high": "Прилив: вода плещется у проломов в бортах, палуба мокрая."})

_loc("hold", "Трюм «Чайки»", "ship", (
    "Трюм полузатоплен даже в отлив: вода стоит по щиколотку. Сквозь проломы в бортах падают "
    "полосы света. Вдоль бортов — разбитые ящики, обрывки мешковины, битое стекло. Часть груза "
    "уцелела: у переборки стоят запаянные жестяные бидоны, над ними на крюке висит кожаная "
    "сумка. Наверх, на палубу, ведёт трап."), {"вверх": _x("wreck", 3)},
    {"crates": _f("разбитые ящики", ["ящик", "мешковин", "стекл"], (
        "Ящики разбиты, их содержимое давно унесла вода. Только на одной доске читается клеймо "
        "«Керосинъ. Нобель»."))},
    outdoor=False)

_loc("cabin", "Кормовая каюта", "ship", (
    "Каюта шкипера тесная, низкая, перекошенная вместе со всем судном. Койка вдоль борта, "
    "откидной стол, привинченный к полу стул. Иллюминатор разбит, и в каюту нанесло песку. Над "
    "койкой на переборке — светлый прямоугольник невыцветшей краски и гвоздь: здесь что-то "
    "висело много лет, а потом его сняли. В ящике стола, который разбух и еле выдвигается, "
    "лежит судовой журнал в парусиновом чехле — видно, шкипер забрал с собой не всё. Выход — "
    "наружу, на палубу."), {"наружу": _x("wreck", 2)},
    {"shiplog": _f("судовой журнал", ["журнал", "судов", "чехол", "чехл"], (
        "Судовой журнал шхуны «Чайка» в парусиновом чехле. Страницы покоробились, но записи "
        "целы."), read="__DOC_SHIPLOG__"),
     "papers": _f("судовые бумаги", ["бумаг", "коносамент", "судов"], (
        "На откидном столе, придавленная чернильницей, лежит пачка слипшихся судовых бумаг."),
        read="__DOC_CARGO__"),
     "mark": _f("светлый прямоугольник", ["прямоугольн", "гвозд", "переборк"], (
        "Прямоугольник невыцветшей краски величиной с книгу. Так остаётся от образа, который "
        "висел над койкой много лет. Гвоздь согнут, будто образ срывали в спешке."))},
    outdoor=False)

_loc("fore", "Кубрик", "ship", (
    "Носовой кубрик команды: три подвесные койки, рундуки, железная печурка. Всё залито водой и "
    "занесено песком, рундуки распахнуты. На переборке кто-то вырезал ножом имена и даты — "
    "видно, коротали долгие стоянки. На одном рундуке лежит забытая гармошка-трёхрядка без "
    "мехов. Выход — на юг, на палубу."), {"юг": _x("wreck", 2)},
    {"carvings": _f("вырезанные имена", ["имен", "вырез", "переборк", "дат"], (
        "На переборке ножом: «Сем. Дуровъ 1899», «Вас. Рябовъ юнга 1901», «Пименъ Е. 1898 — "
        "списанъ». Ниже, свежее всех, кривыми буквами: «Спасъ насъ Трофимъ, Господи помяни»."
        )),
     "boychest": _f("сундучок юнги", ["сундучок", "юнг", "конверт"], (
        "Маленький фанерный сундучок с надписью углём «В. Рябовъ». Внутри — пустая жестянка из-"
        "под леденцов и конверт без марки, надписанный детской рукой."), read="__DOC_YUNGA__"),
     "chests": _f("рундуки", ["рундук"], (
        "В рундуках — тряпьё, размокшая махорка, ложки. Ничего, что стоило бы взять."))},
    outdoor=False)

_loc("isl_bay", "Восточная бухта Птичьего острова", "island", (
    "Вельбот мягко ткнулся носом в гальку маленькой бухты на восточном берегу острова. Берег "
    "здесь пологий, усыпанный плавником и выбеленными костями птиц; выше начинается вереск. На "
    "двух кольях у воды сушится чья-то сеть, от бухты вверх уходит тропинка. На южном краю бухты "
    "под скалой стоит бревенчатая тоневая изба с дымком над трубой — кто-то живёт здесь и "
    "сейчас. Вельбот вы вытащили на берег и привязали к колу; на нём можно вернуться на север, "
    "через пролив, к маячному мысу. На запад тропинка поднимается на вересковую пустошь."),
    {"север": {"to": "boatshed", "cost": 40, "checks": [["!low", _ROW_FAIL]]},
     "запад": _x("isl_path", 5), "юг": _x("tonya", 3)},
    {"net": _f("сеть на кольях", ["сет", "кол"], (
        "Сеть чинёная-перечинёная, но ухоженная; поплавки из бересты. Хозяин, видно, живёт "
        "рыбой."))},
    sea={"low": "Сейчас малая вода: бухта обсохла, вельбот лежит на боку на гальке, а пролив к "
                "мысу превратился в поле ила и камней.",
         "high": "Вода высокая, вельбот покачивается у берега; пролив к мысу открыт."})

_loc("tonya", "Тоневая изба", "island", (
    "Тоневая изба — жильё рыбаков на время путины — низкая, с земляным полом и нарами вдоль "
    "стен. Печь-каменка топится по-чёрному, дым уходит в волоковое окошко, и стены до половины "
    "черны от копоти. У стола — бочонок с солёной рыбой. В красном углу, выше копоти, висят две "
    "иконы: большая, тёмная, старого письма — Николай Чудотворец с кораблём в руке, и рядом "
    "поменьше, в медном окладе, покоробленная, с пятнами соли на лике — святитель Спиридон в "
    "пастушьей шапочке. На гвозде у двери висит засаленная тоневая книга. Выход — на север, в "
    "бухту."), {"север": _x("isl_bay", 3)},
    {"icons": _f("иконы в красном углу", ["икон", "образ", "угол", "угл", "оклад"], (
        "Николай Чудотворец написан на толстой доске, потемневшей до черноты; держит на ладони "
        "парусник. Образ Спиридона — маленький, в медном окладе, покоробленном морской водой; на "
        "обороте оклада выцарапано: «Шх. Чайка. Благословение матушки. Н. Ш.» Лик святителя "
        "в белёсых соляных разводах.")),
     "tonebook": _f("тоневая книга", ["книг", "тонев"], (
        "Засаленная книга, куда записывают улов и всё, что случилось на тоне."),
        read="__DOC_TONEBOOK__")},
    outdoor=False)

_loc("isl_path", "Вересковая пустошь", "island", (
    "Остров оказался больше, чем казался с берега: за прибрежными скалами лежит холмистая "
    "пустошь, вся в вереске, бурая и лиловая. Ветер гонит по ней волны, как по воде. Тропинка "
    "разбегается на четыре стороны. На севере, над самой отмелью, громоздятся скалы; на юге "
    "стоит несмолкающий птичий гомон — там птичий базар; на западе, на мысу, видны серые стены "
    "скита и обломок каменного столба перед ними; на востоке — бухта."),
    {"восток": _x("isl_bay", 5), "север": _x("island", 5), "юг": _x("bazaar", 5),
     "запад": _x("beacon", 10)})

_loc("bazaar", "Птичий базар", "island", (
    "Южный склон острова обрывается к морю уступами, и на каждом уступе, на каждом выступе сидят "
    "птицы: кайры, гагарки, моевки, чайки. Гомон стоит такой, что не слышно собственного голоса, "
    "воздух полон перьев и острого запаха помёта. Птицы взлетают тучей и снова садятся. У края "
    "обрыва кто-то вбил железный штырь и привязал к нему верёвку — птицеловы спускаются по ней "
    "за яйцами. На север тропинка возвращается на пустошь, на юг ведёт к оконечности острова."),
    {"север": _x("isl_path", 5), "юг": _x("southcape", 5)},
    {"rope": _f("верёвка птицеловов", ["верёвк", "веревк", "штыр"], (
        "Верёвка новая, пеньковая, штырь вбит недавно. Скит, выходит, не спасает птиц от "
        "птицеловов — видно, поэтому смотритель и запер его ворота."))})

_loc("southcape", "Южная оконечность острова", "island", (
    "Остров кончается низким каменистым мысом, о который с трёх сторон бьются волны. Здесь "
    "пусто, только лишайник на камнях да ветер. На самом краю сложен из плоских камней гурий — "
    "пирамидка в человеческий рост, какие ставят поморы как знак для проходящих лодок. В щели "
    "между камнями гурия засунута бутылка тёмного стекла. Отсюда открытое море видно на все "
    "стороны, а на северо-востоке — белая башня маяка. На север — птичий базар."),
    {"север": _x("bazaar", 5)},
    {"cairn": _f("гурий с бутылкой", ["гури", "бутылк", "пирамид"], (
        "В бутылке — записка, свёрнутая трубочкой, но бутылка вмурована в камни намертво, и "
        "записку можно прочитать только сквозь стекло."),
        read="Сквозь стекло читается: «Здесь стоялъ на якоре карбасъ „Богородица“ 3 дня въ туманъ, "
             "1899. Хлебъ кончился, рыбу ловили. Кормщикъ Ив. Мошниковъ. Кто прочтётъ — "
             "помолись за насъ». Ниже чужой рукой: «Молился. К.»")},
    sea={"low": "В отлив от мыса в море уходит гряда рифов, над ними кипит пена.",
         "high": "В полную воду рифы скрыты, только пена выдаёт их."})

_loc("beacon", "Развалины старого знака", "island", (
    "Перед стенами скита, на голом каменистом мысу, стоят развалины старого навигационного "
    "знака: квадратное каменное основание в два человеческих роста и обломок столба на нём. "
    "Верх знака давно обрушился, и вокруг основания лежат тёсаные камни, поросшие лишайником. "
    "На западной, обращённой к морю грани основания — большой каменный крест, выбитый в "
    "камне, и две надписи: одна под самым крестом, славянской вязью, другая ниже, "
    "гражданскими буквами. Среди обломков у подножия лежит ещё один камень с вырезанными "
    "буквами. На запад — ворота скита, на восток — пустошь."),
    {"восток": _x("isl_path", 10), "запад": _x("skgate", 3)},
    {"inscription": _f("надписи на знаке", ["надпис", "крест", "грань", "основан"], (
        "Две надписи на западной грани основания, одна над другой."), read=(
        "Под крестом, славянской вязью, с титлами:\n\nСОХРАНИ ГДИ ПЛАВАЮЩИХЪ И ПУТЕШЕСТВУЮЩИХЪ\n\n"
        "Ниже, гражданскими буквами, помельче:\n\nПОСТАВЛЕНЪ ИЖДИВЕНИЕМЪ СОЛОВЕЦКОЙ ОБИТЕЛИ ВЪ "
        "1822 ГОДУ. ЗНАКЪ ДНЕВНОЙ.\n\nБуквы славянской надписи подведены когда-то охрой, и "
        "охра ещё держится в глубине резьбы.")),
     "stone": _f("камень среди обломков", ["камен", "обломк", "букв"], (
        "Тёсаный камень, скатившийся с верха знака."), read=(
        "На камне вырезано славянской вязью: «СПАСИ И СОХРАНИ». Камень лежит надписью вверх; "
        "откуда именно он упал, теперь не понять."))})

_loc("island", "Скалы над отмелью", "island", (
    "Северный край острова — нагромождение серых скал, изъеденных ветром и солью, всё в белых "
    "потёках птичьего помёта. Чайки и крачки взмывают с криками, стоит вам подойти. Между "
    "камнями — гнёзда. На западном склоне темнеет низкий вход в грот. На восток от скал в море "
    "тянется гряда плоских камней. На север, внизу, лежит отмель, а за ней — чёрный остов "
    "шхуны. На юг тропинка уходит на вересковую пустошь."),
    {"север": _x("flat", 15, ["low"], "Внизу, где в отлив лежит отмель, сейчас глубокая вода. "
                 "Придётся ждать отлива."),
     "запад": _x("grotto", 5), "восток": _x("rocks", 5), "юг": _x("isl_path", 5)},
    sea={"low": "Отлив открыл под скалами широкую отмель; по ней можно дойти до шхуны.",
         "high": "Прилив подступил к самым скалам, отмели не видно, волны бьются о камни."})

_loc("grotto", "Грот", "island", (
    "Грот неглубокий, с низким сводом. На стенах — соляные разводы, в углублении пола — лужица "
    "морской воды. У дальней стены сложена из камней небольшая ниша, как будто кто-то "
    "прятал здесь что-то от чужих глаз. На камнях у входа — следы костра и обгоревшая спичка. "
    "Выход — на восток."), {"восток": _x("island", 5)},
    {"niche": _f("ниша в стене", ["ниш", "тайник", "кладк"], (
        "Ниша сложена аккуратно, камни пригнаны один к другому. Судя по всему, её сделал "
        "человек, который часто бывал здесь."),
        on_examine=["reveal:gnote", "set:gnote_found"], examine_need=["!flag:gnote_found"],
        examine_note="Один камень в кладке сидит неплотно. Вы вынимаете его: за ним — жестяная "
                     "коробка из-под монпансье, а в ней сложенный листок. Вы кладёте его рядом.")},
    outdoor=False)

_loc("rocks", "Каменная гряда", "flat", (
    "Гряда плоских чёрных камней уходит от острова в море. Камни скользкие от водорослей. На "
    "дальнем конце гряды стоит железный шест со старым буем. Отсюда хорошо видна белая башня "
    "маяка на мысу и эллинг под ним. Выход — на запад, на остров."), {"запад": _x("island", 5)},
    {"buoy": _f("шест с буем", ["шест", "буй", "буе"], (
        "Ржавый буй на шесте когда-то служил знаком мели. Краска облезла, на нём белеют полосы "
        "птичьего помёта."))},
    sea={"low": "В отлив гряда видна целиком.",
         "high": "В прилив дальние камни гряды уходят под воду."})

_loc("skgate", "Святые ворота скита", "island", (
    "Скит обнесён стеной из валунов, скреплённых известью; стена местами осыпалась, но везде "
    "выше человеческого роста, а поверху её густо зарос шиповник. В стене — Святые ворота под "
    "каменной аркой, над которой пустует киот. Створки ворот новые, дубовые, явно навешенные "
    "недавно, и на них — латунная секретка с тремя рядами по пять колёсиков, такая же, как на "
    "маячных воротах. К решётке окошка в створке привязан кусок светлого холста. За стеной "
    "видны купол без креста, звонница и крыши келий, над одной вьётся дымок. На восток — "
    "развалины старого знака."),
    {"восток": _x("beacon", 3),
     "внутрь": _x("sk_yard", 3, ["flag:g3_open"], "Святые ворота заперты на секретку.",
                  label="скит (за воротами)")},
    {"cloth": _f("кусок холста", ["холст", "окошк", "решётк", "решетк"], (
        "К решётке привязан лоскут холста; на нём углём крупно написано: «Птицеловамъ не "
        "входить. Слова — у смотрителя. Старецъ». Холст давно намок и высох.")),
     "gate": _f("ворота с секреткой", ["ворот", "секретк", "замок", "замк", "колес", "колёс"], (
        "Секретка такая же, как на маячных воротах: три ряда — «верх», «сред.», «низ», — по пять "
        "колёсиков с буквами. Набирать: открыть ворота <слово> <слово> <слово>, сверху вниз; "
        "на колёса встают первые пять букв каждого слова."),
        desc_alt=[{"if": ["flag:g3_open"], "text": "Святые ворота открыты."}],
        lock={"kind": "phrase", "zone": "z3", "parts": 3, "flag": "g3_open", "jam_min": 120,
              "ok": "Колёсики встают на место, секретка щёлкает, и тяжёлая дубовая створка "
                    "Святых ворот медленно подаётся внутрь.",
              "fail": "Вы выставляете все три ряда и толкаете створку, но ворота не поддаются. "
                      "Какое-то из слов не то.",
              "count": "На секретке три ряда колёс — нужно ровно три слова, сверху вниз: "
                       "открыть ворота <слово> <слово> <слово>.",
              "jam": "После третьей неудачи колёсики застопорило. Механизм отпустит часа "
                     "через два.",
              "blocked": "Колёсики секретки застопорены после неудачных попыток; пока они не "
                         "вращаются.",
              "ask": "Секретка открывается тремя словами, по одному на ряд, сверху вниз: "
                     "открыть ворота <слово> <слово> <слово>.",
              "already": "Ворота уже открыты."})})

# ---- zone 3: the skete -------------------------------------------------------

_CUR[0] = 3

_loc("sk_yard", "Монастырский двор", "skete", (
    "За Святыми воротами — просторный двор, заросший подорожником и ромашкой. Когда-то он был "
    "вымощен, и кое-где сквозь траву проступают плиты. Прямо на север стоит деревянная церковь "
    "с шатровой звонницей; купол потерял крест, но гонт на нём ещё цел. На восток тянется "
    "длинный братский корпус с рядом маленьких окон, на запад — приземистая трапезная с "
    "высоким крыльцом, на юг, у стены, — сруб колодца под шатровой крышей. Над одной из труб "
    "корпуса вьётся дымок. Наружу, к воротам, — на запад через арку."),
    {"наружу": _x("skgate", 3), "север": _x("church", 3), "восток": _x("cells", 3),
     "запад": _x("refectory", 3), "юг": _x("well", 3)},
    {"slabs": _f("плиты двора", ["плит", "мостов"], (
        "На одной плите у крыльца трапезной выбито: «Лета 1790 положено основание обители сей». "
        "Остальные плиты гладкие, стёртые ногами."))})

_loc("church", "Церковь Зосимы и Савватия", "skete", (
    "Внутри церкви полумрак и запах старого дерева, воска и мышей. Иконостас в три яруса почти "
    "пуст: большинство икон увезли на Соловки, когда скит упразднили, и на их местах остались "
    "тёмные прямоугольники. Уцелели только местные образа — преподобные Зосима и Савватий с "
    "обителью на ладонях — и несколько малых икон в верхнем ряду. Перед иконостасом горит одна "
    "лампада: её, видно, поддерживает старец. На полу у левого клироса — чугунная плита с "
    "кольцом, под ней ступени вниз, в крипту. В притворе — лесенка на звонницу. В стене на "
    "восток — низкая дверь в ризницу. Выход — на юг, во двор."),
    {"юг": _x("sk_yard", 3), "вверх": _x("belltower", 3), "вниз": _x("crypt", 3),
     "восток": _x("sacristy", 2)},
    {"iconostasis": _f("иконостас", ["иконостас", "икон", "образ", "лампад"], (
        "На местном образе преподобных Зосимы и Савватия видна подпись иконописца: «писалъ инокъ "
        "Геронтий въ лето 1834». Выходит, тот же инок был и иконописцем. Лампада перед "
        "образом заправлена конопляным маслом и горит ровно."))})

_loc("belltower", "Звонница скита", "skete", (
    "Шатровая звонница над притвором. Колоколов нет — их сняли и увезли, остались только "
    "дубовые балки с железными крюками и обрывки верёвок. Зато отсюда открывается вид на весь "
    "остров: вересковая пустошь, птичий базар, скалы над отмелью, чёрный остов «Чайки», а за "
    "проливом — маячный мыс с белой башней. Сверху видно, что от южной стены скита тропинка "
    "ведёт к скале с маленькой часовней и дальше — к каменной башенке фонаря на самом краю. "
    "Вниз — в церковь."), {"вниз": _x("church", 3)},
    {"beams": _f("балки с крюками", ["балк", "крюк", "верёвк", "веревк"], (
        "На балке вырезано: «Колоколъ Геронтиевъ — на Соловки 1868». Видно, у колоколов тоже "
        "были имена, как в посёлке."))})

_loc("crypt", "Крипта", "skete", (
    "Под церковью — низкая сводчатая крипта, где хоронили строителей и настоятелей скита. "
    "Воздух сухой и неподвижный. Вдоль стен в толще кладки устроены четыре ниши, каждая "
    "закрыта каменной плитой с надписью; перед каждой — огарок свечи на полочке. Плиты "
    "подписаны: «Игуменъ Иоасафъ», «Иеромонахъ Паисий», «Инокъ Геронтий», «Игуменъ Германъ». "
    "Свечные огарки перед нишами оплыли одинаково: старец, видно, зажигает их все разом, "
    "поминая всех четверых. Ступени ведут вверх, в церковь."),
    {"вверх": _x("church", 3)},
    {"n_ioasaf": _f("ниша игумена Иоасафа", ["ниш", "плит", "иоасаф", "игумен"], (
        "Плита гладкая, без щелей: «Игуменъ Иоасафъ, основатель обители сей. 1742 — 1809». "
        "Её не трогали много лет — пыль лежит ровно.")),
     "n_paisiy": _f("ниша иеромонаха Паисия", ["ниш", "плит", "паиси", "иеромонах"], (
        "Плита: «Иеромонахъ Паисий, строитель знака на мысу. 1768 — 1829». Пыль на ней "
        "нетронута.")),
     "n_gerontiy": _f("ниша инока Геронтия", ["ниш", "плит", "геронти", "инок"], (
        "Плита: «Инокъ Геронтий, иконописецъ. 1790 — 1861». Пыль на плите ровная, как и на "
        "прочих.")),
     "n_german": _f("ниша игумена Германа", ["ниш", "плит", "герман", "игумен"], (
        "Плита: «Игуменъ Германъ, строитель колокольни и каменнаго фонаря. 1801 — 1872». Пыль "
        "ровная, нетронутая."))},
    outdoor=False)

_loc("sacristy", "Ризница", "skete", (
    "Тесная ризница без окон. Шкафы для облачений распахнуты и пусты; на полках — только "
    "обрывки парчи, медные кольца от занавесей и стопка поминальных синодиков, которые не "
    "стали увозить. На крюке висит старая епитрахиль. На столике — чернильница и перо: старец, "
    "видно, до сих пор вписывает сюда имена. Выход — на запад, в церковь."),
    {"запад": _x("church", 2)},
    {"synodik": _f("поминальный синодик", ["синодик", "помина", "стопк"], (
        "Синодик в деревянных корках, самый верхний в стопке; последние страницы исписаны "
        "свежими чернилами."), read="__DOC_SYNODIK__")},
    outdoor=False)

_loc("refectory", "Трапезная", "skete", (
    "Длинная трапезная с низким потолком на тяжёлых матицах. Посередине — длинный стол на "
    "козлах и лавки; стол застелен холстом лишь у одного края, где стоят миска, кружка и "
    "деревянная ложка. В углу — печь, в ней теплятся угли. На стене — доска с уставом "
    "трапезы, написанным столбцом. Узкая лестница ведёт наверх, в книгохранильню. Выход — на "
    "восток, во двор."), {"восток": _x("sk_yard", 3), "вверх": _x("library", 3)},
    {"rule": _f("доска с уставом", ["доск", "устав"], (
        "Устав трапезы: «В среду и пяток — сухоядение. В прочие дни — рыба, яко на море живём. "
        "За трапезою чтение, а не праздное слово». Ниже приписано позднее: «Ныне в обители "
        "один. Устав держу, как могу»."))},
    outdoor=False)

_loc("library", "Книгохранильня", "skete", (
    "Комнатка над трапезной, сухая и тёплая от печи внизу. Вдоль стен — полки, на которых "
    "осталось десятка три книг: богослужебные, жития, травник, несколько тетрадей. На "
    "отдельном аналое, под холстиной, лежит толстая летопись скита в кожаном переплёте с "
    "медными застёжками — единственная книга, которую старец, видно, бережёт особо. У окна — "
    "стол и лавка. Вниз — трапезная."), {"вниз": _x("refectory", 3)},
    {"chronicle": _f("летопись скита", ["летопис", "аналой", "застёжк", "застежк"], (
        "Летопись скита в кожаном переплёте с медными застёжками."), read="__DOC_CHRONICLE__"),
     "books": _f("книги на полках", ["книг", "полк", "травник", "тетрад"], (
        "Жития Соловецких чудотворцев, Октоих, Псалтирь, травник с рисунками. Сверху лежит "
        "хозяйственная тетрадь скита."), read="__DOC_HOUSEBOOK__")},
    outdoor=False)

_loc("cells", "Братский корпус", "skete", (
    "Длинный бревенчатый корпус с коридором во всю длину и дверями келий по обе стороны. "
    "Почти все двери заколочены крест-накрест, на полу коридора — птичьи перья и сор. Только в "
    "дальнем конце две двери не заколочены: на север, из-под двери, тянет дымом и ладаном — там "
    "келья старца; на восток — больничная келья, откуда слышно тяжёлое, хриплое дыхание. Выход "
    "— на запад, во двор."),
    {"запад": _x("sk_yard", 3), "север": _x("hermit", 2), "восток": _x("infirm", 2)},
    {"doors": _f("заколоченные двери", ["двер", "заколоч", "келий", "кель"], (
        "На досках, которыми забиты двери, мелом написаны имена прежних насельников: «о. "
        "Паисий», «о. Мисаил», «инок Геронтий», «о. Варлаам»... Надписи старые, мел въелся в "
        "дерево."))})

_loc("hermit", "Келья старца", "skete", (
    "Келья маленькая, чисто выметенная. Печурка, лавка, покрытая овчиной, аналой с раскрытой "
    "Псалтирью, в углу — множество икон, больших и малых, перед ними теплится лампадка. На "
    "стене висят чётки-лестовки и связки сушёной рыбы. Пахнет ладаном, дымом и рыбой. На "
    "подоконнике — плошка с мёдом и сухари. Выход — на юг, в коридор."),
    {"юг": _x("cells", 2)},
    {"psalter": _f("Псалтирь на аналое", ["псалтир", "аналой"], (
        "Псалтирь раскрыта на сто шестом псалме. Строки отчёркнуты ногтем: «Нисходящии в море в "
        "кораблех, творящии делание в водах многих, тии видеша дела Господня»."))},
    outdoor=False)

_loc("infirm", "Больничная келья", "skete", (
    "В больничной келье жарко натоплено. На широкой лавке под двумя тулупами лежит худой "
    "бородатый человек с запавшими глазами — смотритель маяка Савелий Грач. Лицо его горит, "
    "дыхание хриплое, он то забывается, то открывает глаза и смотрит мимо вас. Рядом на "
    "табурете — кружка с отваром и мокрая тряпица. У стены стоят морские сапоги до бедра, "
    "облепленные засохшим илом, на гвозде висит его брезентовая сумка. Выход — на запад, в "
    "коридор."), {"запад": _x("cells", 2)},
    {"satchel": _f("сумка смотрителя", ["сумк", "брезент", "смотрит"], (
        "Брезентовая сумка смотрителя: огниво, нож, моток бечёвки, сухари."),
        on_examine=["reveal:page20", "set:satchel_seen"], examine_need=["!flag:satchel_seen"],
        examine_note="На дне сумки, в клеёнчатом конверте, — листок, вырванный из дневника. Вы "
                     "вынимаете его и кладёте на табурет."),
     "boots": _f("морские сапоги", ["сапог"], (
        "Сапоги до бедра, облепленные илом отмели до самого верха. Видно, смотритель прошёл по "
        "отмели в самый край отлива и едва успел выбраться."))},
    outdoor=False)

_loc("well", "Святой колодец", "skete", (
    "У южной стены скита — колодец под шатровой крышей на резных столбах. Сруб старый, "
    "почерневший, но ворот и бадья новые. Вода в колодце пресная — чудо для острова посреди "
    "солёного моря, и в летописи, говорят, этот колодец записан среди чудес преподобного. К "
    "столбу прибит медный ковшик на цепочке. На север — двор, на юг через калитку в стене — "
    "огород."), {"север": _x("sk_yard", 3), "юг": _x("skgarden", 3)},
    {"dipper": _f("ковшик на цепочке", ["ковш", "цепочк", "ворот", "бадь"], (
        "Вода ледяная и сладковатая. На ручке ковшика выбито: «Отъ Кривой Косы, 1896»."))})

_loc("skgarden", "Монастырский огород", "skete", (
    "За стеной, на южном склоне, укрытом от северного ветра, — огород старца: несколько гряд "
    "репы и лука, грядка с мятой, ульи-колоды под навесом. Всё ухожено, прополото. Земля здесь "
    "чёрная, наношенная, видно, в корзинах за многие годы. На восток за огородом видны кресты "
    "братского кладбища, на север — калитка к колодцу."),
    {"север": _x("well", 3), "восток": _x("monkgraves", 3)},
    {"hives": _f("ульи-колоды", ["уль", "колод", "навес"], (
        "Пчёлы гудят лениво, по-осеннему. На одной колоде вырезано: «Геронтиева, 1850»."))})

_loc("monkgraves", "Братское кладбище", "skete", (
    "Небольшое кладбище иноков: деревянные кресты-голубцы, серые от времени, некоторые упали. "
    "Надписи на них вырезаны, но многие стёрлись. На самом высоком месте — свежий, недавно "
    "поставленный крест без надписи; старец, видно, готовит место себе. От кладбища тропинка "
    "уходит на юг, к скале с маленькой часовней. На запад — огород."),
    {"запад": _x("skgarden", 3), "юг": _x("rockchapel", 5)},
    {"crosses": _f("кресты иноков", ["крест", "голубц", "голубец", "надпис"], (
        "Читаются имена: «монахъ Мисаилъ 1861», «иеродиаконъ Варлаамъ 1864», «инокъ "
        "Ферапонтъ, утонулъ при ловле 1866». Строителей обители здесь нет — их хоронили под "
        "церковью, в крипте."))})

_loc("rockchapel", "Часовня на скале", "skete", (
    "На высокой скале у самого моря стоит крошечная часовня — сруб в пять венцов под "
    "луковичной главкой. Внутри едва помещаются двое. На стене — образ Богородицы "
    "«Одигитрии», путеводительницы, и под ним — доска с вырезанными именами тех, кого море не "
    "отдало. В полу часовни — лаз, и вниз, в тело скалы, уходят выбитые ступени: там пещера, "
    "где, по преданию, спасался первый насельник острова. На восток по гребню скалы тропинка "
    "ведёт к каменной башенке фонаря; на север — братское кладбище."),
    {"север": _x("monkgraves", 5), "вниз": _x("cave", 3), "восток": _x("oldlight", 5)},
    {"names": _f("доска с именами", ["доск", "имен"], (
        "Доска с именами утонувших."), read=(
        "На доске вырезаны имена, много рук, много лет. Среди последних, свежих: «Трофимъ "
        "Лапинъ, рыбакъ, 1902», «Пахомъ и Иванъ Зуевы, 1896», «инокъ Ферапонтъ». Под именем "
        "Трофима кто-то вырезал маленький якорь и три чёрточки."))})

_loc("cave", "Пещера преподобного", "skete", (
    "Узкая пещера в толще скалы, сырая и тёмная. В дальнем конце — каменное ложе, выдолбленное "
    "в стене, и над ним грубо процарапанный крест. Свет проникает только сверху, через лаз, и "
    "слышно, как где-то под ногами, в трещинах, дышит море. На ложе лежат сухие цветы вереска "
    "и медная копеечка. Вверх — часовня."), {"вверх": _x("rockchapel", 3)},
    {"legend": _f("доска со сказанием", ["доск", "сказани", "киновар"], (
        "Доска у входа в пещеру, буквы подкрашены киноварью."), read="__DOC_LEGEND__"),
     "bed": _f("каменное ложе", ["лож", "крест", "вереск"], (
        "Ложе выдолблено по росту невысокого человека. Цветы вереска свежие — их носит сюда "
        "старец."))},
    outdoor=False)

_loc("oldlight", "Монашеский фонарь", "skete", (
    "На краю скалы, над рифами, стоит каменная башенка в три сажени высотой, с восьмигранным "
    "застеклённым фонарём наверху, от которого остались только рёбра рамы. Внутрь ведёт "
    "низкая дверца, внутри — узкая лестница и площадка, где когда-то стоял масляный светильник. "
    "Над дверцей вмурована доска с надписью. У подножия башенки валяется почерневший от "
    "времени обломок толстого деревянного шеста с железной корзиной-кострищем на конце. "
    "Отсюда виден маячный мыс и белая башня. На запад — часовня на скале."),
    {"запад": _x("rockchapel", 5)},
    {"board": _f("доска над дверцей", ["доск", "надпис", "дверц"], (
        "Каменная доска над дверцей."), read=(
        "На доске вырезано:\n\nСЕЙ ФОНАРЬ КАМЕННЫЙ ПОСТАВЛЕНЪ ПРИ ИГУМЕНЕ ГЕРМАНЕ ВЪ ЛЕТО 1840 "
        "НА МЕСТЕ ДРЕВНЯГО ОГНЯ, ДА СВЕТИТЪ ПЛАВАЮЩИМЪ\n\nНиже, мелко, приписано: «Огонь "
        "угашенъ 1887, егда возжёнъ маякъ на Кривой косе»."), take_fail="Доска вмурована в камень."),
     "pole": _f("обломок шеста", ["шест", "обломк", "корзин", "кострищ"], (
        "Толстый еловый шест, почерневший, с железной корзиной на конце — в таких корзинах в "
        "старину жгли смолу и бересту, чтобы подать огонь с берега. Шест давно сломан; видно, "
        "его оставили здесь, когда ставили каменную башенку."))})


# ---------------------------------------------------------------------------
# Documents (read texts). Clues live here; fillers are added at build time.
# ---------------------------------------------------------------------------

_ALPHA = "АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
_CIPHER_KEY = "КАСАТКА"
_CIPHER_PLAIN = ("УКЛЮЧИНЫ ОТ ВЕЛЬБОТА УБРАЛ В СУНДУК В СТАРОМ ЛОДОЧНОМ САРАЕ У СУШИЛЬНИ. ЗАМОК У "
                 "СУНДУКА СЛОВЕСНЫЙ, СЛОВО ЗАРЯНКА. КЛЮЧ ОТ САРАЯ ОТДАЛ МИТЕ. ЛУКИЧУ НИ СЛОВА.")


def _vigenere(text: str, key: str, sign: int = 1) -> str:
    out, k = [], 0
    for ch in text:
        if ch in _ALPHA:
            shift = _ALPHA.index(key[k % len(key)])
            out.append(_ALPHA[(_ALPHA.index(ch) + sign * shift) % 32])
            k += 1
        else:
            out.append(ch)
    return "".join(out)


_DOCS = {
    "ORDER": (
        "Управление маяков Беломорского района.\n\nПомощнику смотрителя маяка Кривой косы.\n\n"
        "Предписывается вам по прибытии в посёлок Кривая Коса 14 сентября сего 1905 года принять "
        "маяк и восстановить его огонь. Смотритель маяка Савелий Гаврилович Грач с 7 сентября "
        "числится пропавшим без вести; маяк не горит с той же ночи. В ночь с 16 на 17 сентября "
        "мимо мыса Кривой косы проследует пароход «Святая Ольга» с пассажирами; огонь маяка к её "
        "проходу (не позднее четырёх часов утра 17 сентября) должен гореть непременно, ибо у мыса "
        "рифы.\n\nДля зажжения огня надлежит: линзу очистить от копоти и привести в исправность; "
        "в горелку вставить фитиль; наполнить резервуар лампы керосином; завести часовой механизм "
        "вращения линзы заводной рукоятью; после чего зажечь лампу. Согласно наставлению, огонь "
        "зажигается с наступлением темноты, не ранее 21 часа, и гасится на рассвете.\n\n"
        "Начальник порта Кривой Косы (Лукич) окажет вам содействие. Ключи и шифры от маяка у "
        "смотрителя, их надлежит разыскать.\n\nЗа начальника управления — подпись неразборчива."),
    "TIDES": (
        "На стене две таблицы.\n\nПервая, свежая, отпечатана в типографии:\n\n"
        "ТАБЛИЦА ПРИЛИВОВ. Залив у Кривой косы. Неделя с 14 сентября 1905 г.\n"
        "14 сентября: полная вода 06:25 и 18:50; малая вода 12:40. Заход солнца 20:58.\n"
        "15 сентября: малая вода 00:55 и 13:30; полная вода 07:15 и 19:40. Заход солнца 20:55.\n"
        "16 сентября: малая вода 01:45 и 14:20; полная вода 08:05 и 20:30. Заход солнца 20:52.\n"
        "17 сентября: малая вода 02:35 и 15:10; полная вода 09:00 и 21:25. Заход солнца 20:49.\n"
        "Примечание: отмель между Птичьим островом и остовом шхуны проходима пешком около часа "
        "до и около часа после малой воды. В остальное время — глубоко. Пролив между маячным "
        "мысом и островом в малую воду (час до и час после) обсыхает, лодкам не пройти.\n\n"
        "Вторая таблица пожелтела, углы загнуты, поверх красным карандашом написано «УСТАРЕЛА»:\n\n"
        "ТАБЛИЦА ПРИЛИВОВ. Неделя с 7 сентября 1905 г.\n"
        "7 сентября: малая вода 07:10 и 19:35. 8 сентября: малая вода 08:00 и 20:25.\n"
        "9 сентября: малая вода 08:50 и 21:15. 10 сентября: малая вода 09:40 и 22:05.\n\n"
        "Внизу под таблицами приписка рукой Лукича: «Сегодня 14-е. Смотрите свежую!»"),
    "REGISTER": (
        "РЕЕСТР ЛОДОК И СУДОВ посёлка Кривая Коса (выписка).\n\n"
        "«Надежда» — рыб. лодка, владелец Прохор Зуев, с 1893 г.\n"
        "«Ласточка» — рыб. лодка, владелец Трофим Лапин, 1896–1902 (владелец погиб), ныне у вдовы.\n"
        "«Святой Пётр» — рыб. лодка, артель, с 1899 г.\n"
        "«Касатка» — лодка смотрителя маяка С. Г. Грача, 1889–1901 (первая лодка смотрителя; "
        "продана в Онегу).\n"
        "«Буревестник» — лодка смотрителя маяка С. Г. Грача, спущена в 1902 г.\n"
        "«Чайка» — шхуна купца Сорокина, порт приписки Архангельск; не здешняя. Разбита штормом "
        "на отмели у Кривой косы в октябре 1902 г., команда спасена местными рыбаками.\n"
        "«Зарница» — рыб. лодка, владелец Ермолай Кузнецов, с 1900 г."),
    "GRAVE": (
        "На плите выбито:\n\nМАРФА ИЛЬИНИЧНА ГРАЧ\n1861 — 1897\n\n«Каждый вечер ждала меня с "
        "моря на «Касатке» и светила мне, как маяк. Теперь я свечу за тебя. — С.»\n\n"
        "Ниже, мелкими буквами, позже добавлено: «Господи, упокой душу рабы Твоея»."),
    "PLATE": (
        "На плите над дверью вырезано старинными буквами:\n\nМАЯКЪ КРИВОЙ КОСЫ\n"
        "ЗАЛОЖЕНЪ 1885 · ЗАЖЖЁНЪ ВПЕРВЫЕ 1887 · ПЕРЕСТРОЕНЪ 1904\n\n"
        "Под надписью — якорь и три волны."),
    "ALPHABET": (
        "На доске аккуратно выписан алфавит, под каждой буквой — её номер (урок счёта для "
        "младших):\n\n" + "  ".join(f"{ch}-{i + 1}" for i, ch in enumerate(_ALPHA)) +
        "\n\nВнизу приписано: «Буквы Ё в нашем алфавите для счёта нет. Нумеруем с единицы»."),
    "SQUARE": (
        "Листки на доске объявлений:\n\n1. «Внимание! Отмель у Кривой косы проходима только в малую "
        "воду. Время — по таблице в конторе порта. В прошлом году утонули двое. Начальник порта».\n"
        "2. «Кузнец Ермолай принимает работу, как только будет уголь. Уголь привезут не раньше "
        "октября».\n3. «Продаётся сеть, почти новая. Спросить Прохора на косе».\n"
        "4. «Кто найдёт медный свисток — верните Мите, он всегда в сушильне у сетей».\n"
        "5. Старый листок, почти смытый дождём: «...маяк ...огонь ...не позднее ...»."),
    "TAVERN_BOARD": (
        "На доске в трактире:\n\n«Щи — 5 коп., уха — 7 коп., чай — 2 коп. В долг не наливаем. "
        "Агафья».\n«Митька! Свисток твой никто не видал, не реви. Ищи сам, где лазил».\n"
        "«Вечером в субботу — песни. Гармонь Прохора»."),
    "LEDGER": (
        "Записи в книге Фомы Кожина (последние страницы):\n\n"
        "«Смотрителю Грачу — керосину 2 бидона, в долг (последний раз!)».\n"
        "«Лукичу — свечи, 10 шт., уплачено».\n"
        "«Фитиль ламповый широкий, последний — НЕ ПРОДАВАТЬ, держу для маяка. Отдам только тому, "
        "кто принесёт весточку о Петруше»."),
    "LOGBOOK": (
        "Последние записи в вахтенном журнале маяка Кривой косы:\n\n"
        "«1 сент. Ветер СЗ, 5 баллов. Огонь зажжён в 21:05, погашен в 05:40».\n"
        "«2 сент. Ветер З, 4 балла, туман к утру. Огонь зажжён в 21:00, погашен в 05:45. В тумане "
        "качал ревун с 3 до 5 ч.».\n"
        "«3 сент. Лопнула заводная рукоять привода линзы. Вращаю вручную. Надо к Ермолаю, но у него "
        "нет угля; у Прохора есть мешок».\n"
        "«4 сент. Линза сильно закоптилась, протереть нечем — ветошь вся вышла».\n"
        "«5 сент. Лукич опять лез с вопросами про призму. Снял её с линзы и на вельботе отвёз на "
        "остров, к старцу Варсонофию: там надёжнее, чем в любом сундуке. Вернулся к вечерней воде. "
        "Уключины с вельбота снял и убрал, а куда — записал для себя шифром, записку держу здесь, "
        "в журнале. Вельбот посадил на цепь; слова к секретке — в тетради, в кабинете».\n"
        "«6 сент. Керосин кончился. Последний фитиль сгорел. Огонь не зажжён».\n"
        "«7 сент. Кирьян с острова пришёл на карбасе за хлебом. Иду с ним на остров, а оттуда в "
        "малую воду на «Чайку» — в трюме должны были уцелеть бидоны. Вельбот не беру, пусть "
        "стоит на цепи. Если не вернусь — ищите у старца»."),
    "CIPHER": (
        "Сложенная записка, почерк смотрителя:\n\n«Для себя. Алфавит — 32 буквы, без Ё; А — ноль, "
        "Б — один, и так до Я — тридцать один. К номеру каждой буквы текста прибавлен номер "
        "соответствующей буквы ключевого слова (ключ повторяется по кругу); если вышло больше "
        "тридцати одного — вычтено тридцать два. Пробелы и знаки препинания оставлены как есть и "
        "ключ не сдвигают. Ключ — имя моей первой лодки.»\n\nНиже — строка шифра:\n\n"
        + _vigenere(_CIPHER_PLAIN, _CIPHER_KEY)),
    "LETTER": (
        "Конверт размок, но письмо внутри уцелело. «Дорогой тятенька! Пишу вам из Архангельска. "
        "Служу исправно, жалованье получаю, в Покров обещают отпустить домой. Не сердитесь, что "
        "долго не писал. Кланяйтесь Агафье Семёновне и всем нашим. Ваш сын Пётр Кожин». На конверте: "
        "«Кривая Коса, Фоме Кожину, в лавку». Штемпель — сентябрь 1902 года."),
    "NEWS": (
        "«Архангельские губернские ведомости», пожелтевший номер за октябрь 1902 года. Заметка "
        "обведена карандашом: «Шторм у Кривой косы. В ночь на 12 октября шхуна «Чайка» купца "
        "Сорокина выброшена на отмель. Команда из трёх человек спасена местным рыбаком, который сам "
        "погиб. Имя героя в посёлке почему-то называть не спешат»."),
}


_DOCS.update({
    "BURIALS": (
        "Книга погребений кладбища посёлка Кривая Коса. Записи разными почерками, последние — "
        "корявым почерком сторожа.\n\n"
        "«1886. Мая 3. Анисья Кожина, 61 г., от горячки. Отпевал о. Никодим».\n"
        "«1890. Марта 12. Корнилий Ефимов Сухарев, лоцман, 60 л. Крест чугунный от учеников, у "
        "ограды к морю».\n"
        "«1893. Декабря 2. Иона Пахомов Веретенников, смотритель маяка Кривой косы в отставке, 72 "
        "л. Положен у ограды к морю, по правую руку от лоцмана Сухарева, место чистое. Камень "
        "привёз помощник его С. Грач с Кемского берега».\n"
        "«1896. Октября 30. Пахом и Иван Зуевы, братья, рыбаки. Тела не обретены. Поминальный "
        "крест у часовни».\n"
        "«1897. Февраля 9. Марфа Ильинична Грач, 36 л., жена смотрителя маяка. Положена между "
        "лоцманом Сухаревым и смотрителем Веретенниковым: муж выкупил место, сказал — пусть лежит "
        "между теми, кто светил и водил. Плита каменная».\n"
        "«1899. Августа 17. Младенец Лапиных, некрещёный».\n"
        "«1902. Октября 14. Трофим Лапин, рыбак, 38 л. Утонул в шторм на 12-е число. Тело не "
        "обретено. Поставлен голубец поодаль, у ограды. Вдова просила не писать, от чего погиб, "
        "а в трактире говорят разное».\n"
        "«1904. Января 5. Матрёна Зуева, 80 л., от старости».\n\n"
        "На полях против записи 1893 года другой рукой: «Смотри не перепутай, сторож: по правую "
        "руку от Марфы, если к плитам лицом, — Иона Пахомыч, по левую — лоцман. Так и "
        "передай, кто спросит. С. Г.»"),
    "TELEGRAMS": (
        "Телеграфные ленты, вклеенные в журнал по дням:\n\n"
        "«11 сент. АРХАНГЕЛЬСК УПРАВЛЕНИЕ МАЯКОВ — НАЧАЛЬНИКУ ПОРТА КРИВОЙ КОСЫ. СООБЩИТЕ "
        "ПРИЧИНУ НЕГОРЕНИЯ ОГНЯ. СМОТРИТЕЛЬ ГРАЧ НЕ ОТВЕЧАЕТ».\n"
        "«12 сент. КРИВАЯ КОСА — УПРАВЛЕНИЮ МАЯКОВ. СМОТРИТЕЛЬ ГРАЧ ПРОПАЛ СЕДЬМОГО. КЕРОСИН "
        "КОНЧИЛСЯ. ПРОШУ ПОМОЩНИКА. ЛУКИЧ».\n"
        "«12 сент. УПРАВЛЕНИЕ МАЯКОВ — КРИВАЯ КОСА. ПОМОЩНИК ВЫЕЗЖАЕТ ЧЕТЫРНАДЦАТОГО. КЕРОСИН "
        "БАРЖЕЙ НЕ РАНЕЕ ОКТЯБРЯ. ОГОНЬ К ПРОХОДУ СВЯТОЙ ОЛЬГИ ОБЯЗАТЕЛЕН».\n"
        "«13 сент. ОНЕГА ПАРОХОДСТВО — ВСЕМ ПОРТАМ. ПАРОХОД СВЯТАЯ ОЛЬГА ВЫХОДИТ ОНЕГИ "
        "ШЕСТНАДЦАТОГО ВЕЧЕРОМ ПРОХОД МЫСА КРИВОЙ КОСЫ ОКОЛО ЧЕТЫРЁХ УТРА СЕМНАДЦАТОГО».\n"
        "«13 сент. КЕМЬ — КРИВАЯ КОСА КУЗНЕЦУ КУЗНЕЦОВУ. УГОЛЬ ОТГРУЖЕН НЕ БУДЕТ ДО ПОКРОВА».\n\n"
        "Внизу приклеена ещё одна лента, старая, пожелтевшая, видно, хранилась отдельно: «1902 "
        "ОКТЯБРЯ 20. АРХАНГЕЛЬСК — КРИВАЯ КОСА ФОМЕ КОЖИНУ. ПИСЬМА ОТ ПЕТРА НЕ ПОЛУЧАЛИ. СУДНО "
        "С ПОЧТОЙ РАЗБИТО. ЖДИТЕ». На полях чья-то рука написала: «так и не дождался»."),
})

_PAGES = {
    "page9": ("9", (
        "Страница 9. «Ворота двора я запер секреткой, что привёз из Архангельска: три ряда по "
        "пять колёс. Сначала поставил простые слова, какие первыми в голову пришли, — МАЯК, "
        "ОГОНЬ, МОРЕ, сверху вниз. Да Лукич при мне стоял и смотрел, а язык у него длинный: через "
        "неделю в трактире знали все. Сменил. Новые слова выбрал такие, что чужой не угадает, а "
        "свой человек, кто знает меня и посёлок, соберёт. Каждое записал на отдельной странице и "
        "разнёс страницы по своим местам: страница 11 — верхний ряд, 14 — средний, 17 — нижний. "
        "Если что переменю — допишу на последней странице тетради, двадцать третьей»."
    )),
    "page11": ("11", (
        "Страница 11. «Верхний ряд — имя той, что двенадцать лет носила меня по заливу, пока я "
        "не продал её в Онегу. Кто из стариков помнит, тот и скажет, как её звали; в конторе у "
        "Лукича она тоже записана. Не путай с той, что стоит теперь в сарае: та при мне всего "
        "три года»."
    )),
    "page14": ("14", (
        "Страница 14. «Средний ряд — фамилия старика, при котором я впервые поднялся на галерею в "
        "ночь первого огня. Он меня учил всему: и фитиль подрезать, и линзу мыть, и молчать, "
        "когда надо. Лежит он теперь рядом с моей Марфой — по правую руку от неё, если встать к "
        "плитам лицом. Имя его ты и так услышишь, а фамилию прочтёшь на камне»."
    )),
    "page17": ("17", (
        "Страница 17. «Нижний ряд — последнее слово второй строки той песни, что Марфа пела у "
        "окна, когда ждала меня с моря. Агафья её помнит: они певали вместе, пока Марфа была "
        "жива. Только смотри: в трактире поют по-своему, переиначили. Мне нужно так, как пела "
        "Марфа»."
    )),
    "page23": ("23", (
        "Страница 23. «Последняя страница, как обещал. После ссоры с Лукичом верхний ряд я "
        "переменил: про лодку он выведал, старый лис, — видно, сам полистал реестр. Теперь в "
        "верхнем ряду не имя лодки, а имя колокола, в который у нас бьют на беду. Не того, в "
        "который били прежде, — тот треснул и стоит на земле, — а того, что висит и бьёт теперь. "
        "Отец Никодим скажет, который это; имя вылито на самом колоколе. Средний и нижний ряды я "
        "не трогал»."
    )),
    "page20": ("20", (
        "Страница 20. «Три года молчу, и больше не могу. Трофим Лапин в ту ночь не был пьян, как "
        "болтают в трактире. Я видел с галереи: он вышел на своей «Ласточке» в самый шторм к "
        "«Чайке», снял с неё троих и довёз до косы, а когда пошёл за оставшейся почтой, лодку "
        "опрокинуло у банки. Груне я так и не сказал — не хватило духу: она бы спросила, почему я "
        "не пошёл с ним. А я не пошёл, потому что огонь нельзя оставить, — и до сих пор не знаю, "
        "прав ли. Если меня не станет, пусть кто-нибудь скажет ей правду. Она заслужила»."
    )),
}

_DOCS.update({
    "NOTEBOOK": (
        "Тетрадь в клеёнке. Записи без дат, вперемешку, мелким ровным почерком смотрителя.\n\n"
        "«Картошку копать до Покрова, не позже. В прошлом году затянул — половина померзла».\n\n"
        "«Мите показать выбленочный узел и штык. Руки у него ловкие, голова светлая. Если бы у "
        "нас с Марфой был сын — пусть был бы такой».\n\n"
        "«Управление опять пишет про экономию керосина. Сидели бы они на галерее в ноябре, "
        "экономисты. Огонь либо горит, либо нет; середины у огня не бывает».\n\n"
        "«Вельбот посадил на цепь и запер такой же секреткой, что и ворота, — брал две у "
        "архангельского мастера. Сперва хотел поставить на цепь те же слова, что на воротах, "
        "да раздумал: кто прошёл в ворота, тот ещё не свой. Слова для вельбота выбрал из "
        "самого дома: кто пожил у меня и смотрел по сторонам, тот найдёт, а с посёлка их не "
        "угадать.\n\nВерхний ряд — имя судна, что первым прошло под нашим огнём в ту ночь, "
        "когда его зажгли впервые. Я сам это записывал в первый журнал, мальчишкой, и напутал "
        "от радости, а Иона Пахомыч поправил меня красными чернилами; верь поправке, а не мне. "
        "Журнал тот — в моём матросском сундуке на чердаке, сверху.\n\nСредний ряд — трава, "
        "которой Марфа перекладывала бельё от моли. Она и теперь висит на чердаке, над её "
        "сундуком: я каждый год меняю пучки, как она меняла.\n\nНижний ряд — кличка пса, что "
        "лежит за баней под рябиной»."
        "\n\n«Уключины от вельбота держать отдельно от вельбота. Об этом — в журнале, шифром».\n\n"
        "«Барометр на метеоплощадке врёт на две линии вниз. Поправлять при записи».\n\n"
        "«Кирьяну отвезти соли и спичек, если пойду на остров. Старцу — масла для лампады, "
        "конопляного, не деревянного: деревянное коптит».\n\n"
        "«Груня опять глядела на меня у колодца. Надо сказать ей. Надо. Не могу»."),
    "JOURNAL1887": (
        "Самый ветхий журнал, подписанный «1887». На первой странице: «Журналъ огня маяка "
        "Кривой косы. Смотритель И. П. Веретенниковъ. Помощникъ С. Грачъ». Записи ведёт "
        "помощник, неровным юношеским почерком; поверх некоторых — красные чернила "
        "смотрителя.\n\n"
        "«12 окт. 1887. Линзу собрали и выверили. Горелку испытали днёмъ, пламя ровное».\n\n"
        "«13 окт. Ветеръ З, 3 балла, дождь. Ждёмъ бумаги изъ Управления на зажжение».\n\n"
        "«14 окт. 1887. Бумага пришла съ почтой. Огонь зажженъ впервые въ 17 ч. 40 м. Ветеръ СЗ, "
        "4 балла, ясно. Первымъ мимо нашего огня прошёлъ пароходъ „Кемь“ Архангельско-Мурманскаго "
        "общества, въ 17 ч. 20 м., курсомъ на Онегу, и далъ три гудка. Въ 22 ч. 10 м. подъ огнёмъ "
        "прошла шхуна „Благодать“, кормщикъ Мошниковъ, привѣтствовала огонь фонаремъ на мачтѣ. "
        "Въ 23 ч. 45 м. прошёлъ карбасъ „Страстотерпецъ“ съ Соловковъ. После полуночи — никого».\n\n"
        "Поверх этой записи — красными чернилами, крупно: «Помощнику Грачу. Смотри на часы, а не "
        "на радость. „Кемь“ прошла въ 17.20, за двадцать минутъ до зажжения, засветло, и гудѣла "
        "намъ, а не огню. Подъ нашимъ огнёмъ первой прошла „Благодать“. Исправить и впредь "
        "писать, что видишь, а не что хочешь. И. В.»\n\n"
        "«15 окт. Ветеръ С, 5 баллов. Огонь зажженъ въ 17 ч. 35 м. Прошли: шхуна „Надежда“, "
        "пароходъ „Кемь“ обратнымъ рейсомъ».\n\n"
        "«16 окт. Туманъ. Качали ревунъ всю ночь въ две смены. Руки не держатъ перо».\n\n"
        "На полях последней страницы тем же юношеским почерком, но много позже, другими "
        "чернилами: «Прав был Иона Пахомыч. Всю жизнь потом это помню»."),
    "OILBOOK": (
        "Книга расхода горючего маяка Кривой косы, сентябрь 1905 г.\n\n"
        "«1 сент. Израсходовано на огонь 9 фунт. Остаток — неполн. бочка».\n"
        "«2 сент. — 9 ф. Туман, ревун».\n"
        "«3 сент. — 10 ф. (привод стоит, вращаю вручную, пламя держу выше)».\n"
        "«4 сент. — 9 ф.»\n"
        "«5 сент. — 8 ф. Остаток на одну ночь, не более».\n"
        "«6 сент. — до дна. Огонь не зажжён. Кожин в долг больше не отпускает; баржа не раньше "
        "октября».\n\n"
        "Ниже, крупно: «ГДЕ ВЗЯТЬ: на „Чайке“ в трюме — бидоны Нобеля, запаянные, должны "
        "уцелеть. Идти от острова, по отмели, в малую воду; час до, час после. С косы не "
        "пройти — промоина»."),
    "INSTRUCTION": (
        "«Наставление смотрителямъ маячныхъ огней», страница, заложенная полоской бумаги:\n\n"
        "«§ 14. Огонь зажигается съ наступлениемъ темноты. По местному распоряжению Управления "
        "для маяка Кривой косы — не ранее 21 часа, и гасится на разсвете.\n"
        "§ 15. Передъ зажжениемъ смотритель обязанъ: очистить стекла линзы отъ копоти; проверить "
        "целость линзы и всехъ призмъ; вставить исправный фитиль; наполнить резервуаръ лампы "
        "керосиномъ; завести часовой механизмъ вращения линзы.\n"
        "§ 16. Зажигать лампу надлежитъ сухими спичками или отъ фонаря; огонь неподвижной линзы "
        "съ моря принимается за чужой и вводитъ суда въ заблуждение.\n"
        "§ 17. Въ туманъ подавать сигналы ревуномъ по наставлению, висящему на станции».\n\n"
        "На полоске бумаги, которой заложена страница, рукой смотрителя: «Всё по порядку, а "
        "спички — последними»."),
})

_DOCS.update({
    "KLETTERS": (
        "Пачка писем из ящика стола.\n\n"
        "1. Управление маяков, март 1905: «Смотрителю Грачу. На ваш рапорт сообщаем: вельбот "
        "№ 3 остаётся за маяком Кривой косы; бронзовые уключины к нему отлиты в Архангельске и "
        "высылаются с первым пароходом. Хранить их надлежит отдельно от вельбота во избежание "
        "самовольного пользования. Расход керосина сократить до девяти фунтов в ночь».\n\n"
        "2. Управление маяков, июль 1905: «Сообщаем, что с 1 сентября сего года вам "
        "назначается помощник. До его прибытия огонь поддерживать неукоснительно. В ночь с 16 на "
        "17 сентября мимо мыса проследует пароход „Святая Ольга“»\n\n"
        "3. Письмо из Кеми, от Марфиной сестры Прасковьи, 1904: «Саввушка, получила твоё, "
        "поплакала. Марфино полотенце с маяком, что она не довышила, держи у себя, не отдавай "
        "никому. И песню её не забывай — ту, про свечечку. Я её и сама пою, как она пела, "
        "слово в слово, а не как у вас в трактире орут. Кланяйся Агаше».\n\n"
        "4. Записка без даты, карандашом, почерк Лукича: «Савелий! Ну прости ты меня, дурака, за "
        "ворота. Сболтнул спьяну. Про лодку твою я не нарочно выспросил — в реестре сам увидел. "
        "Не держи зла. Лукич».\n\n"
        "5. Старый конверт, 1893, от Ионы Пахомовича, уже из посёлка: «Савелий, я своё отсветил. "
        "Маяк теперь твой. Помни: не радоваться, а смотреть на часы. Кемь не забывай» — и "
        "приписка: «это я про ту запись, а не про город, не обижайся»."),
    "FOGMANUAL": (
        "НАСТАВЛЕНИЕ ПО ПОДАЧЕ ТУМАННЫХЪ СИГНАЛОВЪ. Маякъ Кривой косы.\n\n"
        "1. Въ туманъ, снегопадъ и мглу, когда огонь маяка не виденъ далее двухъ миль, подаются "
        "сигналы ревуномъ.\n"
        "2. Сигналъ: два долгихъ звука по пяти секундъ съ промежуткомъ въ две секунды, затемъ "
        "молчание пятьдесятъ секундъ. Повторять непрерывно.\n"
        "3. Заслышавъ отвѣтный гудокъ съ судна, продолжать сигналы до его отхода за мысъ.\n"
        "4. Меха смазывать саломъ разъ въ месяцъ, кожу беречь отъ мороза.\n\n"
        "Ниже приписано мелом на раме: «Одному качать тяжело. Звать Кирьяна или Митю»."),
    "METEO": (
        "Журнал метеорологических наблюдений, сентябрь 1905 (последние записи):\n\n"
        "«4 сент. 7 ч.: бар. 758, т-ра 9, ветер СЗ 3. 13 ч.: бар. 757, 12, СЗ 3. 21 ч.: бар. 757, "
        "8, З 2».\n"
        "«5 сент. 7 ч.: бар. 756, 8, З 2. 13 ч.: 755, 11, ЮЗ 3. 21 ч.: 752, 9, ЮЗ 4. Падает».\n"
        "«6 сент. 7 ч.: бар. 749, 7, Ю 5, дождь. 13 ч.: 748, 8, Ю 6. 21 ч.: 750, 7, ЮЗ 5».\n"
        "«7 сент. 7 ч.: бар. 753, 6, З 4. Ухожу на остров. Дальше записывать некому».\n\n"
        "На внутренней стороне обложки: «Поправка барометра −2 линии. С. Г.»"),
    "FLAGS": (
        "Таблица сигналов для судов у мыса Кривой косы:\n\n"
        "«Флаг К — „Имею для вас сообщение“. Флаг L — „Остановитесь немедленно“. Сочетание N-C — "
        "„Бедствие, нужна помощь“. Шар на рее — „Проход у рифов опасен, держитесь мористее“. "
        "Два шара — „Туман у рифов, слушайте ревун“».\n\n"
        "Под таблицей: «Поднимать только днём. Ночью — огонь и ревун». Флаги в ящике сухие, "
        "свёрнуты правильно; не хватает только красного флага B — „Имею опасный груз“: его, "
        "по отметке, взяли на „Чайку“ в 1902 году и не вернули."),
})

_DOCS.update({
    "SHIPLOG": (
        "Судовой журнал шхуны «Чайка», порт приписки Архангельск, владелец купец Сорокин. "
        "Шкипер Никанор Ананьевич Шелепов. Последние страницы:\n\n"
        "«8 окт. 1902. Вышли из Архангельска. Груз: керосин Нобеля в бидонах, 140 шт.; почта на "
        "Онегу и Кривую Косу, три сумки. Команда: шкипер Шелепов, матрос Семён Дуров, юнга "
        "Василий Рябов. Кок Пимен Ершов списан на берег по болезни, нового не взяли».\n\n"
        "«10 окт. Ветер ЮВ, 4. Идём хорошо. Юнга учит компас».\n\n"
        "«11 окт. К вечеру ветер зашёл к С и засвежел до шторма. Огонь Кривой косы видим с 21 ч. "
        "Держим мористее, но сносит».\n\n"
        "«12 окт., пишу на берегу, в посёлке. Около полуночи шхуну выбросило на отмель у Кривой "
        "косы, пробило днище. Около 2 ч. к борту подошла рыбацкая лодка „Ласточка“, рыбак Трофим "
        "Лапин. Первым спустили в лодку юнгу Рябова, за ним сошёл матрос Дуров, последним, как "
        "положено по морскому закону, — я, шкипер. Лапин довёз нас до косы. Груз и судно "
        "оставлены. Шкипер Н. Шелепов».\n\n"
        "Ниже — другими чернилами, почерк тот же, но буквы крупнее и неровнее:\n\n"
        "«20 окт. 1902. Пишу правду, раз уж записал неправду. Последним с „Чайки“ сошёл не я. "
        "Когда мы трое уже сидели в лодке, Трофим велел держать её у борта, а сам полез обратно "
        "на палубу — за почтовыми сумками и за образом святителя Спиридона из моей каюты, "
        "матушкиным благословением, который я в страхе оставил. Он спустился последним, с двумя "
        "сумками — третью не нашёл в темноте, — и образ сунул мне за пазуху. Довёз нас до косы, "
        "развернул лодку и пошёл за третьей сумкой. Больше мы его не видели. Образ я не "
        "удержал: на косе волной вырвало из рук. Бог мне судья. Шкипер Н. Шелепов»."),
    "TONEBOOK": (
        "Тоневая книга Кирьяна Мошникова, Птичий остров. Записи крупными печатными буквами, с "
        "ошибками.\n\n"
        "«1902 ОКТ 12. ШТОРМ. СЕТИ ВСЕ ПОРВАЛО. ЧАЙКУ ВЫБРОСИЛО НА ОТМЕЛЬ. ЛАСТОЧКУ ВИДЕЛ НА "
        "БАНКЕ ВВЕРХ ДНОМ».\n"
        "«1902 ОКТ 15. НА МОЁМ БЕРЕГУ НАШОЛ ОБРАЗ В МЕДНОМ ОКЛАДЕ. НА ОБОРОТЕ ШХ ЧАЙКА. СВЯТИТЕЛЬ "
        "СПИРИДОН. ПОВЕСИЛ В УГОЛ РЯДОМ С ДЕДОВЫМ НИКОЛОЙ. ПУСТЬ ВИСИТ ПОКА ХОЗЯИН НЕ СПРОСИТ».\n"
        "«1903 ИЮН. ТРЕСКИ 40 ПУД. СЁМГИ 3».\n"
        "«1904 АВГ. СТАРЕЦ ЗАНЕМОГ. НОСИЛ ЕМУ РЫБУ И ДРОВА».\n"
        "«1905 АВГ. ПТИЦЕЛОВЫ С ОНЕГИ ОПЯТЬ ЛАЗИЛИ В СКИТ. СМОТРИТЕЛЬ ПОВЕСИЛ НА ВОРОТА "
        "ЗАМОК С БУКВАМИ. СЛОВА ЗНАЕТ ОН ДА СТАРЕЦ. СТАРЕЦ ВОРОТ НЕ ОТПИРАЕТ, СТАР, СЛАБ».\n"
        "«1905 СЕНТ 5. СМОТРИТЕЛЬ ПРИХОДИЛ НА ВЕЛЬБОТЕ, НОСИЛ СТАРЦУ СВЁРТОК, ТЯЖОЛЫЙ».\n"
        "«1905 СЕНТ 7. ХОДИЛ НА МЫС ЗА ХЛЕБОМ. СМОТРИТЕЛЬ СО МНОЙ ОБРАТНО НА ОСТРОВ. В МАЛУЮ ВОДУ "
        "ПОШОЛ НА ЧАЙКУ. К ВЕЧЕРУ НЕ ВЕРНУЛСЯ».\n"
        "«1905 СЕНТ 8. НАШОЛ СМОТРИТЕЛЯ НА СКАЛАХ НАД ОТМЕЛЬЮ. ЖИВОЙ, ГОРИТ ВЕСЬ. ПОВОЛОК К "
        "СТАРЦУ. ВОРОТА ОН САМ ОТПЕР, БОРМОТАЛ СЛОВА, Я НЕ РАЗОБРАЛ. СТАРЕЦ ПРИНЯЛ».\n"
        "«1905 СЕНТ 12. СМОТРИТЕЛЬ ВСЁ В ЖАРУ. ПРО МАЯК ТВЕРДИТ. НА МЫС ПЛЫТЬ НЕ НА ЧЕМ — "
        "КАРБАС ТЕЧЁТ»."),
    "GNOTE": (
        "Листок из коробки, почерк смотрителя, писано карандашом:\n\n"
        "«Для себя и для того, кто придёт после меня. Скит я запер от птицеловов своей "
        "секреткой — третьей, последней у меня. Старец слова знает, но ворот сам не отопрёт: "
        "стар, да и не велено ему. Слова такие, что их соберёт только тот, кто прошёл остров и "
        "«Чайку» своими ногами.\n\nВерхний ряд — имя того, кто в ту ночь последним покинул "
        "„Чайку“. Шкипер записал в судовом журнале одно, а потом, по совести, другое — верь "
        "тому, что написано после. Журнал его так и остался в каюте, в столе.\n\nСредний ряд — "
        "имя святого с того образа, что висел над койкой шкипера, а теперь висит у Кирьяна в "
        "тоневой избе. Не спутай: у Кирьяна в углу их два.\n\nНижний ряд — первое слово той "
        "надписи на старом знаке, что вырезана под самым крестом, славянской вязью; не той, что "
        "ниже, гражданскими буквами, и не той, что на упавшем камне»."),
    "CHRONICLE": (
        "Летопись скита преподобных Зосимы и Савватия на Птичьем острове. Писана разными "
        "руками, уставом и скорописью.\n\n"
        "«Лета 1790 игумен Иоасаф с тремя братиями пришёл на остров сей и положил основание "
        "обители, и поставил церковь древяну во имя преподобных».\n\n"
        "«Лета 1822 иеромонах Паисий иждивением Соловецкой обители поставил на мысу знак "
        "каменный с крестом, дабы плавающие видели его днём и миновали рифы. Огня на знаке том не "
        "было, ибо знак дневной».\n\n"
        "«Лета 1829 преставился отец Паисий. Положен в крипте».\n\n"
        "«Лета 1831, по многим кораблекрушениям у рифов, инок Геронтий, иконописец, умолил "
        "игумена и на скале у рифов поставил шест еловый с корзиною железною и в осенние ночи "
        "жёг в ней смолу и бересту. Сей был первый огонь на острове нашем, и многие плавающие "
        "благодарили».\n\n"
        "«Лета 1840 игумен Герман на месте Геронтиева шеста поставил фонарь каменный со "
        "светильником масляным, а шест сломанный оставили у подножия на память».\n\n"
        "Приписка на полях, другой рукой, позднее: «Иные из рыбаков сказывают, будто первый "
        "огонь на острове зажёг ещё отец Паисий на своём знаке. Неправда то: знак его дневной "
        "и огня не имел. Первый огонь — Геронтиев. Так и записываю, дабы не путали. Варсонофий».\n\n"
        "«Лета 1861 преставился инок Геронтий. Положен в крипте, рядом с отцом Паисием».\n\n"
        "«Лета 1868 колокола взяты на Соловки».\n\n"
        "«Лета 1887 возжён маяк на Кривой косе, и фонарь наш угашен за ненадобностью».\n\n"
        "«Лета 1894 скит упразднён, братия переведена на Соловки. Остался один инок Варсонофий, "
        "по обету, при гробах строителей».\n\n"
        "«Лета 1905, сентября 5. Смотритель Савелий принёс стекло от маячного огня и просил "
        "сохранить. Положил я его у гроба того из братии, кто первым возжёг огонь на острове: "
        "пусть огонь бережёт огонь. Отдам тому, кто придёт от Савелия и скажет, у чьего гроба "
        "лежит стекло»."),
    "SYNODIK": (
        "Поминальный синодик скита. Последние страницы, свежие чернила:\n\n"
        "«О упокоении: игумена Иоасафа, иеромонаха Паисия, инока Геронтия, игумена Германа, "
        "монаха Мисаила, иеродиакона Варлаама, инока Ферапонта».\n\n"
        "«О упокоении в море погибших: Пахома, Иоанна, Трофима, и иже с ними».\n\n"
        "«О здравии: болящего Саввы (Савелия), Кирилла (Кирьяна), рабы Божией Агриппины "
        "(Аграфены), отрока Димитрия».\n\n"
        "На отдельном листке, вложенном между страниц: «Савелий в жару просит: скажите Груне. "
        "Что сказать — не говорит. Господи, вразуми»."),
})

_DOCS.update({
    "MARFA": (
        "Пачка писем, перевязанная выцветшей голубой лентой. Писала Марфа, когда Савелий уезжал "
        "в Архангельск на курсы при Управлении маяков, зимой 1891 года.\n\n"
        "«Саввушка, здравствуй. У нас мороз, залив встал, Прохор с братьями ходит на лёд за "
        "навагой. Иона Пахомыч ворчит, что без тебя ему на галерею лазить тяжело, а сам лазит по "
        "три раза за ночь. Я ему щи ношу. Касатку твою вытащили на берег и укрыли лапником, не "
        "бойся».\n\n"
        "«Саввушка, пишу опять, хоть ты и не ответил. У Зуевых родилась двойня, мальчишки. "
        "Агаша сватает мне своего брата в крёстные, а я говорю — подожди, пусть Савелий "
        "вернётся. Вечерами сижу у окна со свечой, как ты уходишь, так и сижу. Пою нашу — «гори, "
        "гори» — а в трактире опять переврали, орут по-своему. Мне смешно, а Агаше обидно».\n\n"
        "«Саввушка, отец Никодим говорит, что средний колокол, что артель обещала, отольют "
        "не скоро — артель ещё деньги собирает, — а пока на беду бьют в старого „Утешителя“, и он уже звенит с "
        "дребезгом. Боюсь я его звона. Приезжай скорей»."
        "\n\n«Саввушка, весна. Лёд пошёл. Иона Пахомыч велел передать, что журнал за тебя ведёт "
        "сам и что ты ему должен бутылку за каждую ночь. Я не сержусь, что ты мало пишешь. Я "
        "знаю: ты не мастер писать, ты мастер светить. Твоя М.»"),
    "STORM1902": (
        "Вахтенный журнал маяка Кривой косы за октябрь 1902 года, записи смотрителя С. Грача.\n\n"
        "«11 окт. Ветер к вечеру С, 8 баллов, шторм. Огонь зажжён в 17.55. Барометр 738, "
        "падает. Качал ревун с 20 ч., хотя тумана нет: боюсь, в такую мглу огня не видно дальше "
        "мили».\n\n"
        "«12 окт. 0 ч. 15 м. Видел огни шхуны, идущей под зарифленными парусами к югу от "
        "мыса; её сносит на отмель. В 0 ч. 40 м. огни шхуны стали неподвижны — села. Поднял "
        "шар на рее, звонил в колокол станции. В 1 ч. 50 м. видел фонарь лодки, идущей от "
        "косы к шхуне. В 2 ч. 30 м. фонарь лодки у борта шхуны. В 3 ч. 10 м. фонарь лодки у "
        "косы. В 3 ч. 25 м. фонарь лодки снова идёт к шхуне. В 3 ч. 50 м. фонаря не видно».\n\n"
        "«12 окт., утро. Ветер стихает. Команда шхуны „Чайка“ — трое — на косе, живы. Лодки "
        "„Ласточка“ нет. Трофима Лапина нет. Огонь погашен в 6.40».\n\n"
        "Ниже, другими чернилами и явно позже: «Я видел всё. Я должен был сказать. Не сказал. "
        "Господи, прости»."),
    "YUNGA": (
        "Письмо в конверте без марки, почерк детский, крупный, с ошибками. Видно, так и не "
        "отправлено.\n\n"
        "«Дорогая мамынька. Пишу вам с Кривой косы, куда нас выбросило. Шхуна наша Чайка "
        "разбилась, а мы живые все трое. Нас снял рыбак дядя Трофим на лодке Ласточка. Меня "
        "спустили первым, я плакал, стыдно. Потом Семён, потом шкипер. А дядя Трофим полез "
        "обратно на палубу за почтой и за образом шкиперским и спустился последним, с сумками, "
        "и образ шкиперу за пазуху сунул. А потом он нас довёз и опять пошёл к шхуне за третьей "
        "сумкой, и не вернулся. Шкипер велел никому не говорить, что он сам последним не был, "
        "а я говорю вам, мамынька, потому что это правда. Кланяюсь низко. Ваш сын Василий»."),
    "HOUSEBOOK": (
        "Хозяйственная тетрадь скита, последние годы, почерк старца:\n\n"
        "«1903. Рыбы наловлено и засолено 12 пуд. Дров от Кирьяна — 4 сажени. Масла "
        "конопляного для лампад от смотрителя Савелия — полпуда».\n"
        "«1904. Колодец чистил сам, три дня. Кирьян помогал. Ульев пять, мёду 2 пуда, "
        "Геронтиева колода опять пустая — пчёлы не живут в ней с тех пор, как я помню».\n"
        "«1905, авг. Птицеловы с Онеги. Разорили гнёзда у базара, в церковь лазили, искали "
        "серебро. Савелий навесил на ворота новые створки и замок свой, архангельский. Слова "
        "сказал мне на ухо. Сам я ворот не отпираю, сил нет».\n"
        "«1905, сент. 5. Савелий привёз стекло от огня. Положил я его, куда он просил: к тому, "
        "кто первым засветил на острове. Записал в летопись, как положено».\n"
        "«1905, сент. 8. Кирьян принёс Савелия. Жар. Молюсь»."),
})

_DOCS.update({
    "CARGO": (
        "Пачка судовых бумаг, слипшихся от сырости; читаются не все.\n\n"
        "Коносамент: «Принято на шхуну „Чайка“ от Товарищества братьев Нобель керосину в "
        "запаянных бидонах сто сорок штук, для доставки в Онегу. Бидоны не бросать, от огня "
        "беречь». На обороте карандашом: «сняли в Онеге 0; на Кривой Косе — не заходить».\n\n"
        "Письмо купца Сорокина шкиперу: «Никанор! Почту возьми, три сумки, за неё платят "
        "казённые — не отказывайся. Одна на Кривую Косу, две на Онегу. Иди мористее мыса, "
        "там рифы, огонь у Грача надёжный, на него держи. И не гони в шторм — керосин не "
        "убежит, а шхуна у меня одна».\n\n"
        "Записка на клочке, почерк шкипера, видно, писана уже после крушения: «Образ "
        "Спиридона матушкин — потерял. Трофиму свечу поставить. Сорокину написать — не могу»."),
    "LEGEND": (
        "На доске у входа в пещеру вырезано и подкрашено киноварью сказание о начале острова. "
        "Буквы старые, слова простые:\n\n"
        "«Пришёл на остров сей старец-пустынник, имени его никто не ведает, в лета давние, до "
        "обители. Жил в пещере сей, молился за плавающих, и когда видел в море лодью в беде — "
        "выходил на скалу и махал белым платом, и лодьи уходили от рифов. Воды на острове не "
        "было; помолился старец, и открылся колодец, что и доныне у южной стены.\n\n"
        "Иные рыбаки сказывают, будто старец и огонь на скале жёг; но того в летописи нет, и "
        "огня при нём на острове не было — не было ни масла, ни смолы, одни камни. Платом "
        "махал — то правда, а огонь — это уж люди прибавили.\n\n"
        "А когда преставился старец, пришли на остров иноки соловецкие и поставили обитель, "
        "и первым был игумен Иоасаф»."),
})


# ---------------------------------------------------------------------------
# Items (zone = zone of the start location, or explicit z= for hidden items)
# ---------------------------------------------------------------------------

def _it(name, words, at, desc, z=None, **kw):
    return {"name": name, "words": words, "at": at, "desc": desc, "_z": z, **kw}


_ITEMS = {
    "watch": _it("карманные часы", ["час", "карманн"], "room", (
        "Старые карманные часы в латунном корпусе, на цепочке. Идут исправно; на крышке "
        "гравировка: «Помощнику смотрителя — от Управления маяков». С ними можно узнать время "
        "командой «время».")),
    "wet_matches": _it("отсыревшие спички", ["спичк", "отсырев", "мокр"], "room", (
        "Коробок спичек, забытый на подоконнике. Спички отсырели насквозь: головки размокли и "
        "крошатся. Ими ничего не зажжёшь.")),
    "rag": _it("промасленная ветошь", ["ветош", "тряпк"], "fishshed", (
        "Кусок мягкой промасленной ветоши — такой рыбаки протирают фонари и стёкла. Пригодится, "
        "чтобы снять копоть.")),
    "coal": _it("мешок угля", ["мешок", "мешк", "угл", "угол"], "prokhor", (
        "Тяжёлый мешок с каменным углём. Кузнецу бы пригодился."),
        take_need=["flag:prokhor_ok"],
        take_fail="Прохор хмуро смотрит на вас: «Не твоё — не трогай. Спроси сперва»."),
    "whistle": _it("медный свисток", ["свисток", "свистк"], "mill", (
        "Маленький медный свисток на шнурке, начищенный до блеска. На боку нацарапано: «Д. М.» — "
        "видимо, отцовский. Детская вещь, кто-то наверняка его ищет.")),
    "boat_key": _it("ключ от лодочного сарая", ["ключ"], "", (
        "Большой железный ключ с биркой «Сарай. С. Г.»."), z=0),
    "rowlocks": _it("бронзовые уключины", ["уключин", "бронзов"], "", (
        "Две тяжёлые бронзовые уключины на коротких штырях, завёрнутые в промасленную холстину. "
        "На каждой клеймо: «Арх. з-дъ. Управл. маяковъ». Для рыбацкой лодки великоваты — это "
        "уключины от казённого вельбота."), z=0),
    "rum": _it("бутылка рома", ["бутылк", "ром"], "cellar", (
        "Запылённая бутылка ямайского рома с яркой этикеткой. Такой, говорят, любил и смотритель.")),
    "news": _it("старая газета", ["газет"], "cellar", (
        "Пожелтевшая газета, сложенная так, что сверху оказалась заметка, обведённая карандашом."),
        read="__DOC_NEWS__"),
    "spyglass": _it("подзорная труба", ["труб", "подзорн"], "lookout", (
        "Старая медная подзорная труба. Если посмотреть в неё на маяк, видно, что стёкла фонаря "
        "закопчены, а на галерее никого нет.")),
    "rope": _it("моток верёвки", ["моток", "верёвк", "веревк"], "netyard", (
        "Моток крепкой пеньковой верёвки. Ничем особенным не примечателен.")),
    "wormwood": _it("пучок полыни", ["пучок", "полын"], "hut", (
        "Пучок сушёной полыни. Горький запах. Говорят, отгоняет моль и дурные сны; Устинья-"
        "знахарка, по слухам, совала её всем хозяйкам в сундуки.")),
    "prayerbook": _it("молитвенник", ["молитвенник"], "chapel", (
        "Потрёпанный молитвенник на аналое."), take=False,
        take_fail="Молитвенник принадлежит часовне. Отец Никодим этого не одобрит."),
    "broken_crank": _it("сломанная заводная рукоять", ["рукоят", "сломан"], "storage", (
        "Железная заводная рукоять часового механизма. Она лопнула у самого основания; чтобы ею "
        "снова пользоваться, её надо сварить в кузнице.")),
    "lantern": _it("пустой фонарь «летучая мышь»", ["фонар", "летуч"], "storage", (
        "Керосиновый фонарь «летучая мышь». Резервуар пуст, стекло треснуло. Толку от него "
        "никакого.")),
    "cipher": _it("записка с шифром", ["записк", "шифр"], "", (
        "Сложенный вчетверо листок из журнала, исписанный буквами, которые не складываются в "
        "слова."), z=1, read="__DOC_CIPHER__"),
    "crank": _it("починенная заводная рукоять", ["рукоят", "почин", "заводн"], "", (
        "Заводная рукоять, аккуратно сваренная кузнецом. Шов ещё тёмный от окалины, но держит "
        "крепко."), z=1),
    "mailbag": _it("кожаная почтовая сумка", ["сумк", "почтов"], "hold", (
        "Кожаная почтовая сумка с медной пряжкой, разбухшая от воды. Внутри — слипшиеся конверты, "
        "почти все размокли в кашу."),
        read=("Вы перебираете размокшие конверты: адреса расплылись, бумага превратилась в "
              "кашу. Прочитать ничего нельзя. Но, может быть, стоит осмотреть сумку целиком."),
        on_examine=["reveal:letter", "set:bag_seen"], examine_need=["!flag:bag_seen"],
        examine_note="Среди размокших конвертов один уцелел: его прикрывала кожаная подкладка. "
                     "Вы вынимаете его и кладёте рядом."),
    "letter": _it("письмо Фоме Кожину", ["письм", "конверт"], "", (
        "Письмо в размокшем конверте, адресованное Фоме Кожину, в лавку."), z=2,
        read="__DOC_LETTER__"),
    "kerosene": _it("запаянный бидон с керосином", ["бидон", "керосин"], "hold", (
        "Жестяной бидон, запаянный на заводе. На боку клеймо «Керосинъ. Нобель». Бидон полон: "
        "слышно, как внутри плещется.")),
    "wick": _it("новый ламповый фитиль", ["фитил"], "", (
        "Широкий плетёный фитиль для маячной лампы, новый, в бумажной обёртке."), z=2),
    "gnote": _it("записка смотрителя из грота", ["записк", "листок", "грот"], "", (
        "Листок, сложенный вчетверо, из жестяной коробки из-под монпансье. Карандаш, почерк "
        "смотрителя."), z=2, read="__DOC_GNOTE__"),
    "shell": _it("большая раковина", ["раковин"], "rocks", (
        "Большая спиральная раковина. Если приложить к уху, слышно море.")),
    "feather": _it("чаячье перо", ["перо", "перыш"], "island", "Длинное белое перо чайки."),
    "prism": _it("стеклянная призма", ["призм"], "", (
        "Тяжёлая стеклянная призма в латунной рамке — одна из деталей линзы маяка. Грани "
        "идеально отшлифованы и отбрасывают радужные блики."), z=3),
    "dry_matches": _it("коробок сухих спичек", ["спичк", "коробок", "сух"], "", (
        "Жестяная коробочка с сухими спичками. Такие не отсыреют даже в шторм."), z=3),
}
_PAGE_AT = {"page9": "cellar", "page11": "millup", "page14": "hut", "page17": "spring",
            "page23": "lookout", "page20": ""}
for _pid, (_num, _txt) in _PAGES.items():
    _ITEMS[_pid] = _it(f"страница дневника {_num}", ["страниц", "лист", "дневник", _num],
                       _PAGE_AT[_pid], (
        f"Листок, вырванный из дневника смотрителя; в углу номер страницы — {_num}. Бумага "
        "плотная, почерк ровный, чернила местами расплылись."), z=3 if _pid == "page20" else None,
        read=f"__PAGE_{_pid}__")
_ITEMS["page20"]["on_read"] = ["set:read20"]



# ---------------------------------------------------------------------------
# NPCs and dialogue trees
# ---------------------------------------------------------------------------

def _o(label, reply=None, nxt="main", need=None, effects=None):
    d = {"label": label}
    if reply:
        d["reply"] = reply
    if nxt:
        d["next"] = nxt
    if need:
        d["need"] = need
    if effects:
        d["effects"] = effects
    return d


_BYE = "Попрощаться"

_NPCS = {
    "lukich": {
        "name": "Лукич, начальник порта", "loc": "office", "words": ["лукич", "начальник"],
        "here": "За столом сидит начальник порта Лукич — грузный, седой, в форменной фуражке. Он "
                "поднимает на вас усталые глаза.",
        "refuse": "Лукич отмахивается: «Это мне ни к чему. Оставь себе».",
        "nodes": {"main": {"text": (
            "Лукич откладывает перо. «А, помощник смотрителя! Прибыл, значит. Ну, садись. Беда у нас: "
            "маяк не горит уже неделю, Савелий пропал, а через три дня мимо мыса пойдёт «Святая "
            "Ольга». Спрашивай, что знаю — расскажу»."),
            "options": [
                _o("Спросить о смотрителе", (
                    "«Савелий Грач — мужик крепкий, упрямый. Двадцать лет на маяке. Седьмого "
                    "числа ушёл в малую воду на отмель, к «Чайке», и не вернулся. Искали — "
                    "нашли только следы, их вода смыла. Кирьян с острова, говорят, его туда и "
                    "отвёз. Может, на острове пересидел, а может, и сгинул. Бог знает. Бумаги "
                    "его, ключи — всё у него было. Слова на воротах двора он после нашей ссоры "
                    "сменил, мне не сказал»."
                )),
                _o("Спросить о приливах и отмели", (
                    "«Таблица вон на стене, свежая — на эту неделю, с четырнадцатого. Сегодня как "
                    "раз четырнадцатое. Старую не смотри, она устарела, я её для порядка держу. "
                    "По отмели ходят около часа до малой воды и около часа после — не больше. "
                    "Кто задержится — сидит на острове или на шхуне до следующего отлива, а это "
                    "полсуток. Хорошо ещё, если сидит»."
                )),
                _o("Спросить про ворота маячного двора", (
                    "«Там у него секретка архангельская, три ряда колёс. Прежние слова я знал — "
                    "видел, как он набирал, — и однажды сболтнул в трактире, каюсь. Савелий "
                    "узнал и сменил. Какие новые — не знаю. Одно, правда, я потом угадал: про "
                    "его первую лодку, в реестре подсмотрел, — да он, говорят, и его сменил, как "
                    "узнал. Он всё в дневник свой записывал, а дневник по листочку растащил по "
                    "всей округе, чудак»."
                )),
                _o("Спросить о призме линзы", (
                    "«Линза у него была в порядке, только призму одну он снял — сказал, для "
                    "сохранности. Я спросил куда — он и ощетинился. Думаю, спрятал где-то у "
                    "себя или на острове, у старца: он туда часто ездил на казённом вельботе. "
                    "Лодочный сарай у него на замке, ключ он кому-то отдал, не мне»."
                )),
                _o("Спросить об острове", (
                    "«Птичий остров-то? Там скит был соловецкий, упразднили его лет десять "
                    "назад, один старец остался. Да Кирьян, рыбак, в тоневой избе живёт. Савелий "
                    "к старцу ездил, масло ему возил для лампад. На казённом вельботе ездил, с "
                    "маячной пристани. С косы туда не пройдёшь — промоина»."
                )),
                _o("Спросить про «Святую Ольгу»", (
                    "«Пароход пассажирский, идёт из Онеги. Мимо нашего мыса пройдёт в ночь с "
                    "шестнадцатого на семнадцатое, к четырём утра будет у рифов. Если огня не "
                    "будет — вынесет на камни, как «Чайку». Так что к той ночи маяк должен "
                    "гореть, хоть умри. А лучше — раньше: огонь зажигают с темнотой, после "
                    "девяти вечера»."
                )),
                _o(_BYE, "«Ступай. И поторопись», — говорит Лукич.", None),
            ]}},
        "start": "main",
    },
    "agafya": {
        "name": "Агафья, хозяйка трактира", "loc": "tavern", "words": ["агафь", "хозяйк", "трактирщиц"],
        "here": "За стойкой хлопочет хозяйка трактира Агафья — румяная, в белом переднике, с "
                "полотенцем через плечо.",
        "refuse": "Агафья смеётся: «Оставь себе, милок, мне и без того хлопот хватает».",
        "nodes": {"main": {"text": (
            "«Здравствуй, здравствуй! Комната твоя наверху, я прибралась. Щей хочешь? Нет? Ну, "
            "тогда спрашивай, что надо, — я в посёлке всё про всех знаю»."),
            "options": [
                _o("Спросить о посёлке", (
                    "«Посёлок у нас маленький: рыбаки, лавка Фомы, кузня Ермолая, контора "
                    "Лукича, часовня отца Никодима. Прохор живёт на косе, в хижине, — он лучший "
                    "рыбак. Вдова Аграфена — на Верхней улице, с синими ставнями. Все друг про "
                    "друга всё знают, а правды никто не скажет»."
                )),
                _o("Спросить о Мите", (
                    "«Митька-то? Сирота, при сетях живёт, в сушильне. Вчера ревел: свисток "
                    "потерял, отцовский, медный. Лазил, говорит, по мельнице — там, небось, и "
                    "обронил. Хороший мальчишка, Савелий его любил, ключи ему доверял»."
                )),
                _o("Спросить о Фоме", (
                    "«Фома всё сына ждёт, Петрушу. Тот в Архангельске служит, а писем нет с той "
                    "осени, как «Чайка» разбилась, — на ней же почта шла. Фома с тех пор сам "
                    "не свой. Скажи ему кто, что сын жив, — он последнюю рубаху отдаст»."
                )),
                _o("Спросить о сундучке смотрителя", (
                    "«Савелий оставил у меня сундучок на хранение, я его в погреб снесла — люк "
                    "за стойкой. Смотри, коли надо; там тряпьё да бумажки»."
                )),
                _o("Спросить о Прохоре", (
                    "«Прохор-то? Лучший рыбак на косе, да бирюк. С чужими не разговаривает, "
                    "пока ему бутылку не поставишь, — такой уж обычай. Савелий к нему всегда с "
                    "ромом ходил. Ром у меня в погребе стоит, ямайский; возьми бутылку, для "
                    "маяка не жалко, после сочтёмся»."
                )),
                _o("Спросить о Марфе, жене смотрителя", (
                    "Агафья вздыхает. «Марфуша... Подруга моя была. Как Савелий в море уйдёт на "
                    "«Касатке», она сядет у окна со свечой и поёт — тихо так, а на всю улицу "
                    "слышно. Мы с ней вдвоём певали. Слушай, я тебе напою, как она пела:\n\n"
                    "Ты гори, гори, моя свечечка,\nнад водою гори над холодною,\nчтобы "
                    "сокол мой видел с моря свет,\nне сбивался с пути-дороженьки.\n\n"
                    "А мужики у меня в трактире переиначили, орут спьяну: «над водою гори над "
                    "студёною», да ещё третью строку по-своему — «чтобы милый мой». Марфа так "
                    "не пела, и Савелий, бывало, сердился, когда слышал. Похоронили её в "
                    "девяносто седьмом, у ограды, между лоцманом и стариком Ионой Пахомычем, "
                    "что прежде Савелия маяк держал»."
                )),
                _o("Спросить о Трофиме", (
                    "«Трофим Лапин утонул три года назад, в тот шторм. Говорят, пьяный был, в "
                    "море полез. Грушу, вдову его, жалко: гордая, ни у кого ничего не просит»."
                )),
                _o(_BYE, "«Заходи ещё!» — кричит Агафья вслед.", None),
            ]}},
        "start": "main",
    },
    "foma": {
        "name": "Фома Кожин, лавочник", "loc": "shop", "words": ["фом", "кожин", "лавочник"],
        "here": "За прилавком стоит лавочник Фома Кожин — сухой, остроносый, в очках на шнурке.",
        "refuse": "Фома поджимает губы: «Не надо мне этого. В лавке и так тесно».",
        "nodes": {"main": {"text": (
            "Фома поправляет очки. «Чего изволите? Предупреждаю сразу: в долг не отпускаю. Даже "
            "маяку. Особенно маяку — смотритель мне за два бидона керосина должен»."),
            "options": [
                _o("Спросить про фитиль", (
                    "«Фитиль широкий, маячный, у меня один и остался — последний. Держу. Не "
                    "продаю: денег у вас, я вижу, нет, а в долг — сказал уже. Вот если бы "
                    "весточку мне кто принёс о Петруше, о сыне... тогда бы и фитиль отдал, и "
                    "что хочешь»."
                ), need=["!flag:wick_given"]),
                _o("Спросить про керосин", (
                    "«Керосина нет ни капли: весь ушёл на маяк, а новый привезут не раньше "
                    "октября. Разве что на «Чайке» — она ведь с керосином шла, от Нобеля. В "
                    "трюме бидоны запаянные были; может, какие и уцелели»."
                )),
                _o("Спросить о сыне", (
                    "Фома долго протирает очки. «Петруша в Архангельске служит, в конторе "
                    "пароходства. Писал каждый месяц, аккуратный. А с той осени — ни строчки. "
                    "Телеграфировал я — ответили: почта на разбитой шхуне была, ждите. Жду. "
                    "Третий год жду. Может, он думает, что писал уже, а может, и нет его. Не "
                    "знаю я ничего»."
                )),
                _o("Спросить о смотрителе", (
                    "«Грач? Упрямец. Всё сам, всё молчком. Последний раз был шестого, просил "
                    "керосину — я отказал. Может, зря... Он сказал, что на «Чайку» пойдёт»."
                )),
                _o(_BYE, "Фома кивает и снова утыкается в книгу.", None),
            ]}},
        "start": "main",
    },
    "ermolay": {
        "name": "Ермолай, кузнец", "loc": "smithy", "words": ["ермола", "кузнец"],
        "here": "У холодного горна сидит кузнец Ермолай — широкоплечий, с опалённой бородой.",
        "refuse": "Ермолай качает головой: «Это не по моей части».",
        "nodes": {"main": {"text": (
            "Ермолай поднимается вам навстречу. «Здорово. Работы нет — угля нет. Горн без угля "
            "что печь без дров. Если что сковать или сварить — неси уголь, сделаю»."),
            "text_alt": [{"if": ["flag:smith"], "text": (
                "Ермолай, не отрываясь от горна, кивает вам. «Рукоять твоя у меня. Сварить — дело "
                "недолгое, а вот остывать ей часа два, не меньше, иначе лопнет снова»."
            )}],
            "options": [
                _o("Спросить о работе", (
                    "«Угля нет с августа: баржа не пришла. У Прохора есть мешок, он им печь "
                    "топит, — у него проси. Принесёшь уголь и работу — сделаю»."
                ), need=["!flag:smith"]),
                _o(_BYE, "«Бывай», — басит Ермолай.", None),
            ]}},
        "start": "main",
    },
    "agrafena": {
        "name": "Аграфена, вдова рыбака", "loc": "widow", "words": ["аграфен", "вдов", "груш", "грун"],
        "here": "У окна сидит вдова Аграфена — прямая, строгая, в чёрном платке. Она штопает сеть.",
        "refuse": "Аграфена качает головой: «Не надо мне ничего».",
        "nodes": {"main": {"text": (
            "Аграфена откладывает работу и смотрит на вас внимательно. «Новый смотритель? Ну, "
            "садись. Зачем пришёл?»"),
            "options": [
                _o("Спросить о Трофиме", (
                    "Аграфена поджимает губы. «Три года уж. Говорят, пьяный в шторм полез, "
                    "дурак. А я не верю. Не пил он в ту осень, мне ли не знать. Да что толку — "
                    "никто правды не скажет. Савелий, может, знал: он с маяка всё видит. "
                    "Заходил ко мне не раз, мялся, будто сказать хочет, — и не говорил»."
                )),
                _o("Попросить спичек", (
                    "«Спичек? Сухие у меня есть, в жестянке, от сырости берегу. Только вот что: "
                    "сперва скажи мне, что Савелий про моего Трофима знал. Не может быть, чтобы "
                    "он ничего не оставил»."
                ), need=["!flag:told"]),
                _o(_BYE, "Аграфена кивает и возвращается к сети.", None),
            ]}},
        "start": "main",
    },
    "nikodim": {
        "name": "отец Никодим", "loc": "chapel", "words": ["никодим", "отец", "батюшк", "священник"],
        "here": "У входа в часовню стоит отец Никодим — худой, седобородый, в поношенной рясе.",
        "refuse": "Отец Никодим мягко отводит вашу руку: «Оставь, сын мой».",
        "nodes": {"main": {"text": (
            "«Мир тебе. Ты, верно, новый помощник смотрителя? Помолюсь за тебя. О чём хочешь "
            "спросить?»"),
            "options": [
                _o("Спросить о колоколах", (
                    "«Колоколов было четыре. Прежде на беду били в «Утешителя», да он треснул "
                    "в ту зиму, когда звонили по братьям Зуевым, — сняли его, стоит теперь у "
                    "стены. Теперь звоним в три: в большой — к службе, в средний — на беду, "
                    "как лодки не вернулись или шторм, в малый — когда лодки возвращаются. "
                    "Имена у них вылиты на поясе, подымись на звонницу — прочтёшь»."
                )),
                _o("Спросить о смотрителе", (
                    "«Савелий — вдовец. Жена его, Марфа, лежит у нас на кладбище, он каждый год "
                    "приходит на её могилу. Учил его маячному делу покойный Иона Пахомыч, "
                    "первый наш смотритель; Савелий при нём мальчишкой был, когда огонь впервые "
                    "зажгли, и схоронил его сам, рядом потом и Марфу положил. Человек он замкнутый, но совестливый. Мучило его "
                    "что-то в последнее время, будто грех на душе»."
                )),
                _o("Спросить о шторме 1902 года", (
                    "«Страшная была ночь. «Чайку» на отмель выбросило, людей с неё рыбаки сняли. "
                    "Трофим Лапин в ту ночь погиб. Что там было на самом деле — один Бог знает "
                    "да, может, Савелий: он всю ночь на галерее простоял»."
                )),
                _o(_BYE, "«Ступай с Богом».", None),
            ]}},
        "start": "main",
    },
    "prokhor": {
        "name": "Прохор, рыбак", "loc": "prokhor", "words": ["прохор", "рыбак"],
        "here": "У печи сидит рыбак Прохор — жилистый старик с трубкой в зубах, чинит сеть.",
        "refuse": "Прохор хмыкает: «На что мне это?»",
        "nodes": {"main": {"text": (
            "Прохор вынимает трубку изо рта. «А, маячный. Слыхал про тебя. Садись, коли пришёл. "
            "Спрашивай, только недолго — мне к вечерней воде сети ставить»."),
            "text_alt": [{"if": ["!flag:prokhor_warm"], "text": (
                "Прохор даже не поднимает головы от сети. «Чего надо? Некогда мне лясы точить. "
                "Всякий приезжий лезет с расспросами, а толку — ноль. Вот Савелий, бывало, "
                "придёт с бутылкой, посидим, потолкуем по-людски... А ты кто такой? Иди себе»."
            )}],
            "options": [
                _o("Спросить о сетях", (
                    "«В тот октябрьский шторм, когда «Чайку» разбило, сетей я потерял шесть — "
                    "все, что стояли. Одну потом Митька нашёл на отмели, я её залатал, вон она "
                    "в сушильне висит. Так что насовсем пропало пять. Остальные новые, в долг у "
                    "Фомы брал, до сих пор отдаю»."
                ), need=["flag:prokhor_warm"]),
                _o("Спросить о лодках смотрителя", (
                    "«Первая его лодка — «Касатка». Хорошая была лодка, на ней он ещё жену "
                    "катал. Продал её в Онегу, когда Марфы не стало. Потом, в девятьсот втором, "
                    "справил «Буревестник» — тот в сарае у него стоит. А «Чайка» — это не его, "
                    "это купеческая шхуна, что на отмели лежит, не путай»."
                ), need=["flag:prokhor_warm"]),
                _o("Попросить угля для кузницы", (
                    "«Уголь? Для Ермолая? Для маяка, значит. Бери мешок, что под лавкой, — для "
                    "маяка не жалко. Сам натоплю дровами»."
                ), need=["flag:prokhor_warm", "!flag:prokhor_ok"], effects=["set:prokhor_ok"]),
                _o("Спросить об отмели", (
                    "«С косы на отмель теперь не пройти — промоину в девятьсот втором размыло. "
                    "На остров Савелий ходил на казённом вельботе, с маячной пристани, только "
                    "не в малую воду: пролив тогда обсыхает. А уж с острова, в малую воду, час "
                    "до и час после, по отмели можно дойти до «Чайки». Время у Лукича в "
                    "таблице. На острове Кирьян живёт, в тоневой избе, да старец в скиту. "
                    "Савелий туда часто ходил, в грот лазил, что-то прятал»."
                ), need=["flag:prokhor_warm"]),
                _o(_BYE, "Прохор снова берётся за сеть.", None),
            ]}},
        "start": "main",
    },
    "mitya": {
        "name": "Митя", "loc": "netyard", "words": ["мит", "мальчик", "мальчишк"],
        "here": "Между жердями сидит мальчишка лет десяти — Митя, — и распутывает поплавки.",
        "refuse": "Митя шмыгает носом: «Не надо мне».",
        "nodes": {"main": {"text": (
            "Митя поднимает голову. «Вы — новый смотритель? Дядя Савелий говорил, что пришлют. "
            "Он меня узлам учил»."),
            "options": [
                _o("Спросить про лодочный сарай", (
                    "«Ключ от сарая у меня — дядя Савелий оставил, сказал: отдашь тому, кто "
                    "маяк зажигать будет. Только... я свисток потерял, папкин, медный. На "
                    "мельнице лазил и обронил. Найдёте — отдам ключ, честное слово»."
                ), need=["!flag:key_given"]),
                _o("Спросить про латаную сеть", (
                    "«Это Прохорова сеть. Я её на отмели нашёл, после шторма, в песок "
                    "замытую. Прохор мне за неё пятак дал»."
                )),
                _o("Спросить о смотрителе", (
                    "«Дядя Савелий хороший. Он мне про Птичий остров рассказывал — там грот, "
                    "а в гроте тайник. И про мельницу — он там наверху сидел и на море "
                    "смотрел. Он всё писал в тетрадку»."
                )),
                _o(_BYE, "Митя снова возится с поплавками.", None),
            ]}},
        "start": "main",
    },
}

_NPCS["kiryan"] = {
    "name": "Кирьян, рыбак с острова", "loc": "tonya", "words": ["кирьян", "рыбак", "старик"],
    "here": "На нарах сидит Кирьян — кряжистый старик с седой бородой лопатой, в вязаной фуфайке; "
            "он чинит вершу и поглядывает на вас из-под лохматых бровей.",
    "refuse": "Кирьян отводит руку: «Оставь. У меня всё есть, что Бог дал».",
    "nodes": {"main": {"text": (
        "Кирьян откладывает вершу. «С маяка, что ли? Вельбот-то Савельев узнал. Ну, садись, "
        "раз добрался. Чего спросить хочешь?»"),
        "options": [
            _o("Спросить о смотрителе", (
                "«Седьмого числа я на мыс за хлебом ходил, на карбасе. Савелий со мной и "
                "вернулся — вельбот, говорит, пусть на цепи стоит. В малую воду пошёл с "
                "острова на «Чайку», за керосином. К вечеру не вернулся. Утром нашёл я его на "
                "скалах над отмелью — живой, да весь горит. Видать, вода его застала, полночи в "
                "ледяной воде просидел. Поволок я его к старцу в скит. Ворота Савелий сам "
                "отпер, бормотал что-то, я не разобрал. С тех пор там и лежит, в жару»."
            )),
            _o("Спросить об иконах в углу", (
                "«Большой — Никола, дедов ещё, с ним дед мой на Мурман ходил. А малый, в "
                "окладе, — святитель Спиридон. Этого море принесло: через три дня после того "
                "шторма, как «Чайку» разбило, лежал у меня на берегу, в бухте. На обороте "
                "нацарапано — «Шх. Чайка». Висел, видно, у шкипера в каюте. Шкипер не "
                "спросил, так и висит у меня»."
            )),
            _o("Спросить о Трофиме Лапине", (
                "Кирьян мрачнеет. «Трофим... Я в ту ночь на острове был, всё видел, что в "
                "темноте увидишь. Его «Ласточка» два раза к «Чайке» ходила. Второй раз не "
                "вернулась — наутро на банке лежала, вверх дном. А в посёлке болтают — пьяный, "
                "мол. Язык бы им вырвать. Я Груне говорил, да она мне не верит: я, говорит, "
                "старый, мне почудилось. Савелий — тот видел больше моего, с галереи-то»."
            )),
            _o("Спросить о ските и старце", (
                "«Скит упразднён давно, братию на Соловки свели. Один старец Варсонофий "
                "остался, при гробах. Слаб уже, ворот не отпирает. Птицеловы онежские туда "
                "лазили, так Савелий ворота навесил новые, с замком буквенным. Слова знает он да "
                "старец. Мне не сказали, и не надо мне. Савелий говорил: кто остров обойдёт да "
                "на «Чайке» побывает, тот и сам сложит»."
            )),
            _o("Спросить о «Чайке»", (
                "«Купеческая, Сорокина. Шкипер на ней был Шелепов, Никанор, молодой, горячий. "
                "Он потом в посёлке неделю жил, пил, плакал, а после уехал и больше не "
                "показывался. Трюм у неё и сейчас не пустой: бидоны там, почта. Я не лазил — "
                "грех чужое брать. А Савелий полез, за керосином, для огня. Для огня можно»."
            )),
            _o("Спросить об отмели и воде", (
                "«С острова на отмель — со скал, на север, в малую воду. Час до, час после, "
                "больше не стой: вода приходит быстро, по промоинам, сперва тихо, потом бегом. "
                "С отмели на восток — «Чайка», на запад — банка, её не заливает. А на мыс, к "
                "маяку, в малую воду не выгребешь — пролив обсыхает»."
            )),
            _o(_BYE, "«Ступай с Богом», — Кирьян снова берётся за вершу.", None),
        ]}},
    "start": "main",
}

_HERMIT_WRONG = ("Старец долго молчит, перебирая лестовку. «Нет. Не там. Гадаешь, а не знаешь. "
                 "Поди почитай летопись, что в книгохранильне, да приходи не раньше, чем через "
                 "два часа: я помолюсь, чтобы тебя вразумило»." )
_NPCS["varsonofy"] = {
    "name": "старец Варсонофий", "loc": "hermit", "words": ["варсонофи", "старец", "старц", "инок"],
    "here": "На лавке у печурки сидит старец Варсонофий — крохотный, согбенный, с белой бородой "
            "до пояса и неожиданно ясными синими глазами.",
    "refuse": "Старец качает головой: «Не надо мне ничего, чадо. Всё у меня есть».",
    "nodes": {
        "main": {"text": (
            "Старец поднимает глаза от Псалтири. «Пришёл-таки. От Савелия, значит, коли ворота "
            "отпер. Садись. О чём спросишь?»"),
            "options": [
                _o("Спросить о смотрителе", (
                    "«Кирьян его принёс восьмого числа, горячего, как уголь. Лихорадка. Пою его "
                    "отварами, молюсь. Иногда приходит в себя и всё про маяк спрашивает, да про "
                    "Груню какую-то, да про страницу. Поди к нему, в больничную келью, — может, "
                    "тебя узнает, хоть ты ему и чужой»."
                )),
                _o("Спросить о ските", (
                    "«Обитель наша стоит с девяностого года прошлого века. Были у нас и "
                    "строители, и иконописцы, и огонь на острове горел задолго до вашего маяка. "
                    "Всё записано в летописи, в книгохранильне над трапезной. Я стар, имена "
                    "путаю, а летопись не путает»."
                )),
                _o("Спросить о стекле от маяка", (
                    "«Савелий принёс его пятого числа и просил сохранить. Я положил стекло у "
                    "гроба того из братии, кто первым возжёг огонь на нашем острове, — пусть "
                    "огонь бережёт огонь. Отдам тому, кто придёт от Савелия и скажет, у чьего "
                    "гроба оно лежит. Скажешь — отдам»."
                ), "ask", need=["!flag:prism_given", "cool:hw:120"]),
                _o("Спросить о стекле от маяка", (
                    "Старец отворачивается к иконам. «Рано. Сказал уже: поди почитай летопись. "
                    "Через два часа приходи»."
                ), need=["!flag:prism_given", "!cool:hw:120"]),
                _o("Спросить о Трофиме Лапине", (
                    "Старец крестится. «Раб Божий Трофим. Поминаю его каждый день. Савелий "
                    "мне всё рассказал на исповеди, да не мне его тайну открывать. Он её "
                    "записал, говорит, и носит с собой. Кому надо — тот прочтёт»."
                )),
                _o("Спросить о монашеском фонаре", (
                    "«Огонь у нас на острове горел задолго до вашего маяка — для рыбаков, для "
                    "лодий соловецких. Сперва один, потом другой, потом каменный фонарь "
                    "поставили. Всё записано в летописи; я стар и имена путаю, а спорить об "
                    "этом рыбаки любят. Ты летописи верь, а не рыбакам»."
                )),
                _o("Спросить о воротах и словах", (
                    "«Слова Савелий мне сказал, да не мне их повторять. Раз ты здесь — ты их и "
                    "так знаешь»."
                )),
                _o(_BYE, "«Храни тебя Бог», — старец снова склоняется над Псалтирью.", None),
            ]},
        "ask": {"text": "«Ну, говори. У чьего гроба лежит Савельево стекло?»",
                "options": [
                    _o("У гроба игумена Иоасафа", _HERMIT_WRONG, None, effects=["stamp:hw"]),
                    _o("У гроба иеромонаха Паисия", _HERMIT_WRONG, None, effects=["stamp:hw"]),
                    _o("У гроба инока Геронтия", (
                        "Старец кивает, и лицо его светлеет. «Геронтиев огонь... Верно. Прочёл, "
                        "значит». Он берёт свечу и уходит в церковь; слышно, как внизу, в "
                        "крипте, скрипит камень. Вернувшись, он кладёт перед вами свёрток в "
                        "мягкой замше. «Неси. И зажги»."
                    ), None, effects=["give:prism", "set:prism_given"]),
                    _o("У гроба игумена Германа", _HERMIT_WRONG, None, effects=["stamp:hw"]),
                    _o("Сказать, что ещё не знаете", "«Узнаешь — приходи».", "main"),
                ]},
    },
    "start": "main",
}

_NPCS["grach"] = {
    "name": "Савелий Грач, смотритель", "loc": "infirm", "words": ["савели", "грач", "смотрител"],
    "here": "Смотритель Савелий Грач лежит на лавке под тулупами; он то забывается, то смотрит на "
            "вас мутными от жара глазами.",
    "refuse": "Смотритель не понимает, что вы ему протягиваете, и бессильно отводит руку.",
    "nodes": {"main": {"text": (
        "Смотритель с трудом поворачивает голову. Губы его пересохли. «Кто... Лукич? Нет... "
        "Не Лукич...» Он пытается приподняться и снова падает на подушку."),
        "options": [
            _o("Сказать, что вы новый помощник смотрителя", (
                "Глаза смотрителя на миг проясняются. «Помощник... Прислали, значит... Огонь... "
                "Шестнадцатое... «Ольга»... Рукоять у Ермолая... нет, не у Ермолая, лопнула... "
                "керосин на «Чайке»... Фома... фитиль у Фомы, последний...» Он хватает вас за "
                "рукав. «Груне скажи. В сумке... двадцатая... Скажи ей»."
            ), effects=["set:grach_seen"]),
            _o("Спросить о призме", (
                "«Стекло... у старца... Я положил... нет, он положил... к тому, кто первый... "
                "огонь...» Смотритель замолкает, и непонятно, помнит ли он, о чём говорил."
            )),
            _o("Спросить, как он сюда попал", (
                "«Вода... Вода пришла, а я в трюме... бидоны... Не донёс... Скалы... Кирьян...» "
                "Он закрывает глаза и долго хрипло дышит."
            )),
            _o(_BYE, "Смотритель уже не слышит: он снова забылся тяжёлым сном.", None),
        ]}},
    "start": "main",
}

# Dialogue options that live in a later zone but belong to a zone-0 NPC.
_PATCH_OPTS = {
    1: [{"npc": "ermolay", "node": "main", "opt": _o("Спросить, готова ли рукоять", (
            "«Не готова ещё. Сварил, остывает. Два часа от того, как ты принёс, не "
            "меньше. Погуляй пока»."
        ), need=["flag:smith", "!since:smith:120"])},
        {"npc": "ermolay", "node": "main", "opt": _o("Забрать рукоять", (
            "Ермолай снимает рукоять с полки и протягивает вам. «Держи. Шов крепкий, "
            "теперь хоть сто лет заводи»."
        ), need=["since:smith:120", "!flag:crank_done"], effects=["give:crank", "set:crank_done"])}],
    3: [{"npc": "agrafena", "node": "main", "opt": _o("Рассказать правду о Трофиме", (
            "Вы пересказываете то, что написал смотритель на двадцатой странице своего "
            "дневника. Аграфена слушает молча, не шевелясь. Потом отворачивается к окну и "
            "долго молчит. «Значит, троих спас... А я, дура, три года людям в глаза смотреть "
            "не могла». Она встаёт, достаёт с полки жестяную коробочку. «Возьми. Сухие, в "
            "шторм не отсыреют. Зажги маяк — чтоб больше никто не тонул»."
        ), need=["flag:read20", "!flag:told"], effects=["set:told", "give:dry_matches"])}],
}



# ---------------------------------------------------------------------------
# Rules for giving and using
# ---------------------------------------------------------------------------

_SMITH_OK = ("Ермолай берёт сломанную рукоять и мешок угля, раздувает горн и через несколько минут "
             "уже стучит молотом. «Сварю, — говорит он, не оборачиваясь. — Только ей остывать "
             "часа два. Раньше не отдам, лопнет. Приходи и спроси»." )
_GIVE_RUM = {"item": "rum", "npc": "prokhor", "effects": ["take:rum", "set:prokhor_warm"],
             "text": "Прохор впервые поднимает на вас глаза, берёт бутылку, разглядывает этикетку и "
                     "прячет её за печь. «Ямайский... Савелий такой же носил. Ну, садись, маячный, "
                     "раз по-людски пришёл. Спрашивай — отвечу»."}
_GIVES_Z = {
    0: [{"item": "whistle", "npc": "mitya", "effects": ["take:whistle", "give:boat_key", "set:key_given"],
         "text": "Митя хватает свисток, прижимает к груди и тут же свистит так, что с жердей "
                 "взлетают чайки. «Нашёлся! Спасибо! Вот, держите ключ от сарая — я обещал»."},
        _GIVE_RUM,
        {"item": "coal", "npc": "ermolay", "need": ["flag:__never"],
         "fail": "«Уголь — это дело, — говорит Ермолай. — Только что чинить-то? Неси работу»."}],
    1: [{"item": "broken_crank", "npc": "ermolay", "need": ["has:coal"],
         "effects": ["take:broken_crank", "take:coal", "stamp:smith"], "text": _SMITH_OK,
         "fail": "Ермолай вертит рукоять в руках. «Сварить можно. Только горн холодный — угля нет. "
                 "У Прохора есть мешок, у него попроси»."},
        {"item": "coal", "npc": "ermolay", "need": ["has:broken_crank"],
         "effects": ["take:broken_crank", "take:coal", "stamp:smith"], "text": _SMITH_OK,
         "fail": "«Уголь — это дело, — говорит Ермолай. — Только что чинить-то? Неси работу»."}],
    2: [{"item": "letter", "npc": "foma", "effects": ["take:letter", "give:wick", "set:wick_given"],
         "text": "Фома берёт размокший конверт, узнаёт почерк и долго молчит, сняв очки. «Петруша... "
                 "жив, значит, тогда был жив... служит...» Он отворачивается, а потом, не говоря ни "
                 "слова, достаёт из-под прилавка свёрток и кладёт перед вами. «Фитиль. Бери. И "
                 "керосин тебе нашёл бы, да нету»."}],
}


def _lamp_rules(obj: str) -> list[dict]:
    loc = "lamproom"
    base = [] if obj == "dry_matches" else ["has:dry_matches"]
    rules = [
        {"obj": obj, "loc": loc, "need": base + ["!flag:wick_in"],
         "text": "Вы подносите огонь к горелке, но гореть нечему: фитиля в ней нет."},
        {"obj": obj, "loc": loc, "need": base + ["!flag:fuel_in"],
         "text": "Фитиль сухой, резервуар лампы пуст — без керосина огонь не удержится."},
        {"obj": obj, "loc": loc, "need": base + ["!flag:prism_in"],
         "text": "Лампу зажечь можно, но линза неисправна: без недостающей призмы (и на "
                 "закопчённом стекле) огонь маяка не будет виден. Сначала приведите линзу в порядок."},
        {"obj": obj, "loc": loc, "need": base + ["!flag:wound"],
         "text": "Линза неподвижна: часовой механизм не заведён. Неподвижный огонь с моря примут "
                 "за чужой. Сначала заведите механизм."},
        {"obj": obj, "loc": loc, "need": base + ["!dark"],
         "text": "Ещё светло. По наставлению огонь зажигают с наступлением темноты, не ранее 21 "
                 "часа; зажечь сейчас — значит сжечь керосин впустую. Дождитесь темноты."},
        {"obj": obj, "loc": loc, "need": base + ["dark"],
         "effects": ["take:dry_matches", "set:lit", "win"],
         "text": "Вы чиркаете сухой спичкой. Огонёк дрожит, касается фитиля — и лампа вспыхивает "
                 "ровным жёлтым пламенем. Линза подхватывает свет, механизм мерно тикает, и "
                 "над морем, впервые за две недели, проходит луч маяка Кривой косы."},
    ]
    if obj != "dry_matches":
        rules.append({"obj": obj, "loc": loc,
                      "text": "Чем зажечь? Нужны спички — и желательно сухие."})
    return rules


_USES_Z1 = [
    {"obj": "rag", "loc": "lamproom", "need": ["!flag:lens_clean"], "effects": ["set:lens_clean"],
     "text": "Вы долго и тщательно протираете стекло линзы промасленной ветошью. Копоть сходит "
             "неохотно, но в конце концов кольца и призмы начинают блестеть."},
    {"obj": "rag", "loc": "lamproom", "text": "Линза и так чиста."},
    {"obj": "feat:lens", "loc": "lamproom", "need": ["has:rag", "!flag:lens_clean"],
     "effects": ["set:lens_clean"],
     "text": "Вы протираете линзу ветошью, пока стекло не начинает блестеть."},
    {"obj": "prism", "loc": "lamproom", "need": ["flag:lens_clean"],
     "effects": ["take:prism", "set:prism_in"],
     "text": "Вы осторожно вставляете призму в пустое гнездо оправы. Она входит плотно, с "
             "лёгким щелчком. Теперь линза цела."},
    {"obj": "prism", "loc": "lamproom",
     "text": "Стекло линзы покрыто копотью; ставить призму на грязную оправу бессмысленно — "
             "сначала очистите линзу."},
    {"obj": "wick", "loc": "lamproom", "effects": ["take:wick", "set:wick_in"],
     "text": "Вы вставляете новый фитиль в горелку и подкручиваете его на нужную высоту."},
    {"obj": "kerosene", "loc": "lamproom", "need": ["flag:wick_in"],
     "effects": ["take:kerosene", "set:fuel_in"],
     "text": "Вы вскрываете бидон и аккуратно заливаете керосин в резервуар лампы. Фитиль "
             "пропитывается и темнеет."},
    {"obj": "kerosene", "loc": "lamproom",
     "text": "В горелке нет фитиля — заливать керосин в пустую горелку не стоит. Сначала фитиль."},
    {"obj": "crank", "loc": "lamproom", "need": ["!flag:wound"], "effects": ["take:crank", "set:wound"],
     "text": "Вы вставляете рукоять в гнездо и заводите механизм. Гири поднимаются, шестерни "
             "оживают, и линза начинает медленно поворачиваться."},
    {"obj": "feat:mech", "loc": "lamproom", "need": ["has:crank", "!flag:wound"],
     "effects": ["take:crank", "set:wound"],
     "text": "Вы вставляете рукоять в гнездо и заводите механизм. Линза начинает вращаться."},
    {"obj": "feat:mech", "loc": "lamproom", "need": ["!flag:wound"],
     "text": "Завести механизм нечем: нужна заводная рукоять."},
    {"obj": "broken_crank", "loc": "lamproom",
     "text": "Рукоять лопнула у основания: в гнезде она просто проворачивается. Её надо сварить."},
    {"obj": "wet_matches", "text": "Спички отсырели: головки крошатся и не загораются.",
     "loc": "lamproom"},
    {"obj": "rowlocks", "loc": "boatshed", "need": ["!flag:oars_in"],
     "effects": ["take:rowlocks", "set:oars_in"],
     "text": "Вы вставляете тяжёлые бронзовые уключины в гнёзда планширя — они входят плотно, с "
             "металлическим стуком, — и закладываете в них вёсла. Вельбот готов к выходу."},
]
_USES_Z1 += _lamp_rules("dry_matches") + _lamp_rules("feat:lamp")
_USES_Z = {
    0: [{"obj": "boat_key", "loc": "netyard", "need": ["!flag:boathouse_open"],
         "effects": ["set:boathouse_open"],
         "text": "Ключ туго поворачивается в замке, дужка выходит из петли. Лодочный сарай открыт."},
        {"obj": "wet_matches", "text": "Спички отсырели: головки крошатся и не загораются."}],
    1: _USES_Z1,
}
# ---------------------------------------------------------------------------
# Atmosphere banks (fillers added to descriptions at build time)
# ---------------------------------------------------------------------------

_FILL = {
    "village": [
        "Посёлок живёт морем. На заборах сушатся сети и рубахи, у крылец стоят вёсла, на "
        "подоконниках — стеклянные поплавки, зелёные и синие, как бутылочное стекло. Где-то "
        "стучит топор, где-то скрипит ворот колодца, лает собака. Женщины в тёмных платках "
        "несут вёдра, и каждая, проходя, внимательно оглядывает вас: чужой человек здесь "
        "виден сразу.",
        "Дома в Кривой Косе ставили из плавника и корабельного леса, и в стенах кое-где видны "
        "старые нагели и следы от шпангоутов. Крыши крыты дёрном и тёсом, на коньках — резные "
        "петушки и рыбы. Окна маленькие, с двойными рамами, чтобы зимой не выдувало тепло.",
        "Ветер несёт с залива запах соли, водорослей и дыма. Над крышами кружат чайки, "
        "перекликаясь резкими голосами, а на коньке ближайшего дома неподвижно сидит баклан, "
        "расправив крылья, словно сушит бельё. Туман то сгущается, то редеет, и тогда на миг "
        "открывается серое, в белых барашках, море.",
        "Мостовая здесь выложена крупными окатанными камнями, между которыми пробивается "
        "трава. В колеях стоит вода. У стены дома лежит перевёрнутая лодка с пробитым днищем — "
        "видно, её давно собирались починить, да так и не собрались. Под лодкой спит рыжий кот.",
        "Из открытого окна доносится песня: женский голос тянет что-то протяжное про мужа, "
        "ушедшего в море, и про свечу, что горит на окне. Песня обрывается, хлопает ставня, и "
        "снова слышны только ветер и чайки.",
        "У каждого дома — поленница, укрытая берестой, и бочка для дождевой воды. На верёвках "
        "вялится рыба: треска, камбала, навага. Её запах смешивается с запахом дёгтя, которым "
        "смолят лодки, и с дымом печей, в которых сжигают плавник.",
        "Мимо проходит старик с вязанкой хвороста и кивает вам, как старому знакомому. Двое "
        "мальчишек гонят обруч по лужам. На крыльце соседнего дома женщина чистит рыбу, и "
        "вокруг неё уже собрались кошки, терпеливые и молчаливые.",
        "Посёлок невелик: от причала до часовни — пять минут ходу. Но здесь, кажется, у каждого "
        "камня своя история, у каждого дома — свой утонувший и свой спасённый. Об этом не "
        "говорят громко, но это чувствуется в том, как люди то и дело поглядывают на море.",
    ],
    "indoor": [
        "Внутри тихо, только потрескивают доски да где-то капает вода. Воздух тёплый и "
        "спёртый, пахнет деревом, дымом и чем-то съестным. Сквозь маленькое окно пробивается "
        "серый свет, и в нём медленно плывут пылинки.",
        "Стены здесь проконопачены мхом, и из щелей кое-где торчат его сухие пряди. Потолок "
        "низкий, балки потемнели от времени и дыма. На гвоздях висят вещи, которые, судя по "
        "пыли, давно никто не трогал.",
        "Половицы скрипят при каждом шаге, и кажется, что дом прислушивается к вам. В углу "
        "паутина, в паутине — высохшая муха. Снаружи доносится шум ветра и далёкий гул прибоя.",
        "Здесь всё устроено просто и крепко, без украшений: всё, что есть, служит делу. Люди у "
        "моря не любят лишнего — лишнее тонет первым.",
        "Свет здесь тусклый, и глазам нужно время, чтобы привыкнуть. Постепенно из полумрака "
        "проступают очертания вещей, мелкие детали, следы чьей-то жизни, прерванной или "
        "просто отложенной до лучших времён.",
        "Где-то за стеной возится мышь. Пахнет сыростью: у моря сырость проникает всюду, "
        "сколько ни топи печь. Дерево от неё темнеет, железо ржавеет, бумага желтеет и "
        "коробится.",
    ],
    "dunes": [
        "Дюны тянутся волна за волной, светлые, поросшие редкой жёсткой травой. Ветер "
        "непрерывно переносит песок, и гребни дюн курятся, будто дымятся. В ложбинах тихо и "
        "тепло, на гребнях — ветрено и холодно. Песок забивается в сапоги и в складки одежды.",
        "Над дюнами высоко кружит ястреб. В траве посвистывают какие-то мелкие птицы, "
        "вспархивают из-под самых ног и снова падают в траву. Цветёт последний вереск, "
        "лиловый, медовый, и над ним гудят запоздалые шмели.",
        "Отсюда, сверху, видно, как узка полоса земли, на которой живут люди: с одной стороны "
        "— море, с другой — дюны и лес, а между ними — посёлок, прижавшийся к заливу. Всё "
        "кажется маленьким и хрупким перед этой огромной серой водой.",
        "Тропа петляет между дюнами, то пропадая в песке, то снова появляясь. Кто-то отметил "
        "её воткнутыми палками с обрывками тряпок, чтобы не сбиться в тумане. Некоторые палки "
        "упали, и их занесло песком.",
        "Сосны здесь невысокие, кряжистые, с корнями, вылезшими из песка, как узловатые "
        "пальцы. Под ними лежат кучки шишек и серебристый мох. Смолой пахнет так сильно, что "
        "запах моря почти не чувствуется.",
        "Ветер доносит издалека гул прибоя — ровный, неумолчный, как дыхание огромного зверя. "
        "Иногда в нём слышится что-то похожее на голоса или на звон колокола, но это только "
        "ветер и вода.",
        "Песок хранит следы: птичьи крестики, заячьи петли, круглые отпечатки лисьих лап. "
        "Попадаются и человечьи следы — большие, от сапог, — но ветер уже наполовину их "
        "замёл, и не разобрать, куда они вели.",
    ],
    "spit": [
        "Коса — узкая полоса песка и гальки, по обе стороны которой вода: слева открытое море с "
        "тяжёлой зыбью, справа тихий залив. Иногда волна перехлёстывает через гребень, и тогда "
        "на песке остаются пена, водоросли и мелкие ракушки.",
        "Ветер здесь не стихает никогда. Он свистит в сухой траве, рвёт полы одежды, бросает "
        "в лицо солёные брызги. Идти приходится, наклонившись вперёд, и каждый шаг даётся с "
        "трудом, особенно по сыпучему песку.",
        "На песке лежат выброшенные морем вещи: обломки досок, пробковые поплавки, стеклянный "
        "шар от сети, обрывок каната, чья-то рукавица. Море отдаёт всё, что забрало, но не "
        "всегда тем, кто потерял.",
        "Над косой низко проносятся крачки, ныряют в воду и тут же выныривают с серебристыми "
        "рыбками в клювах. На мокрых камнях у кромки воды стоят кулики на длинных ногах и "
        "невозмутимо смотрят на море.",
        "Маяк впереди то приближается, то будто отступает: на косе трудно судить о расстоянии. "
        "Белая башня на фоне серого неба кажется то совсем близкой, то далёкой, как "
        "мираж.",
        "Под ногами хрустит галька, перемешанная с ракушками. Кое-где из песка торчат старые "
        "сваи — остатки прежней дороги к маяку, которую смыло штормом лет двадцать назад.",
    ],
    "lighthouse": [
        "Маяк построен на совесть: стены толщиной в сажень, камень к камню, известь, которую "
        "не берёт ни мороз, ни соль. Внутри башни гулко, каждый звук повторяется эхом, и "
        "кажется, что где-то наверху кто-то ходит.",
        "Здесь всё подчинено одному делу — огню. Всё, что есть в башне, служит тому, чтобы "
        "каждую ночь над морем проходил луч: запасы, инструменты, журналы, стёкла. Без огня "
        "маяк — просто каменная труба на мысу.",
        "Сквозь толстые стены доносится шум моря — приглушённый, ровный, как далёкий гром. "
        "Иногда в него вплетается пронзительный крик чайки, и тогда эхо подхватывает его и "
        "несёт вверх по башне.",
        "Пахнет керосином, известью и солёной сыростью. На всём лежит тонкий слой пыли — "
        "неделю никто не прибирался здесь, и маяк уже начал дичать, как дичает дом без "
        "хозяина.",
        "На стенах кое-где видны отметки карандашом: даты, цифры, короткие записи — "
        "смотритель, видно, привык записывать всё, что происходит, даже на стенах.",
    ],
    "flat": [
        "Отмель огромна и пустынна. Мокрый песок блестит, как зеркало, отражая небо, и "
        "кажется, что идёшь по облакам. Под ногами хлюпает, песок пружинит, и в ямках от "
        "следов тут же выступает вода.",
        "Повсюду — следы жизни, оставленной морем: морские звёзды, раковины, клубки водорослей, "
        "мелкие крабы, торопливо удирающие боком. В лужах мечутся мальки, запертые отливом до "
        "следующей воды.",
        "Здесь особенно чувствуется, что время отмерено. Море отступило, но оно вернётся — и "
        "вернётся быстро, по ручейкам и промоинам, сначала незаметно, потом всё быстрее. Старые "
        "рыбаки говорят: на отмели нельзя зевать.",
        "Чайки кружат над отмелью с криками, ссорятся из-за добычи, садятся на воду в лужах. "
        "Вдалеке, у самой кромки моря, стоят цапли, неподвижные, как вбитые в песок колья.",
        "Ветер гонит по песку тонкую водяную рябь. Горизонт размыт: не понять, где кончается "
        "отмель и начинается море. Только чёрный остов шхуны и скалы острова служат "
        "ориентирами в этом сером просторе.",
    ],
}

_FILL_MORE = {
    "village": [
        "Возле одного из домов стоит скамейка, отполированная до блеска поколениями сидевших на "
        "ней стариков. Сейчас на ней никого нет, только лежит забытая кем-то трубка и кисет. "
        "Отсюда хорошо видно залив, и, наверное, здесь по вечерам обсуждают улов, погоду и "
        "соседей — в том порядке, в каком это важно для посёлка.",
        "Под ногами хрустят рыбьи кости и ракушки: здесь чистят улов прямо на улице, а остатки "
        "достаются чайкам и кошкам. Запах стоит крепкий, но местные его, кажется, не замечают. "
        "Для них это просто запах дома.",
        "Где-то хлопает калитка, и из-за забора выглядывает любопытное лицо — девочка в платке "
        "разглядывает вас во все глаза, а потом с визгом убегает. Слышно, как она кричит в доме: "
        "«Мамка, мамка, новый маячник пришёл!»",
        "Небо над посёлком низкое, облака несутся с моря рваными клочьями. В разрывах иногда "
        "проглядывает синева, и тогда всё вокруг на мгновение становится ярким и чётким: белые "
        "наличники, красные рябины, чёрные просмолённые борта лодок. Потом облака смыкаются, и "
        "краски снова гаснут.",
        "У забора растёт старая рябина, вся в тяжёлых красных гроздьях. Говорят, если рябины "
        "много — зима будет суровой. В этом году её столько, что ветки гнутся до земли, и "
        "старухи качают головами.",
        "Вдоль улицы тянутся дощатые мостки, чтобы в распутицу не тонуть в грязи. Доски "
        "прогибаются и пружинят под ногами, некоторые подгнили, и через них приходится "
        "перешагивать. Под мостками журчит ручеёк дождевой воды, стекающий к морю.",
    ],
    "indoor": [
        "На стене висит старый календарь, отрывной, с пожелтевшими листками. Последний "
        "оторванный листок — начало сентября. Видно, с тех пор его никто не трогал, и время здесь "
        "будто остановилось.",
        "У печки сложены дрова — берёзовые и сосновые, вперемешку с плавником, выбеленным морем. "
        "Плавник горит жарко и с треском, разбрасывая синеватые искры: в нём соль, которую море "
        "вбивало в дерево годами.",
        "На полке стоят глиняные кружки, одна с отбитой ручкой, и жестяная коробка из-под чая с "
        "полустёртым рисунком — китайские домики и мостик над рекой. Такие коробки здесь "
        "хранят годами и держат в них всё что угодно, кроме чая.",
        "Окно запотело изнутри, и кто-то вывел на стекле пальцем рожицу — теперь она медленно "
        "расплывается. Снаружи за стеклом угадывается серое небо и мелькание чаек.",
        "В воздухе висит запах — смесь дыма, рыбы, сырой шерсти и дёгтя. Это запах всех домов "
        "Кривой Косы; через день перестаёшь его замечать, а через год, говорят, начинаешь по "
        "нему скучать, где бы ни был.",
    ],
    "dunes": [
        "Ветер стихает на минуту, и становится слышно, как шуршит песок, осыпаясь со склона, как "
        "поскрипывают сосны и как где-то далеко кричит одинокая птица. Потом ветер налетает "
        "снова, и все звуки тонут в его ровном гуле.",
        "Кое-где из песка торчат старые колья — остатки изгороди, которой когда-то пытались "
        "удержать дюны. Изгородь не помогла: песок перешагнул через неё и пошёл дальше, к лесу, "
        "медленно и неотвратимо, как ходят только пески и ледники.",
        "Над вереском поднимается тонкий медовый запах, смешанный с запахом нагретой смолы. "
        "Если закрыть глаза, можно забыть, что море совсем рядом, — но его выдаёт гул, который "
        "не смолкает ни на минуту.",
        "Под ногами попадаются следы старых костров: чёрные круги, угли, обгоревшие камни. Сюда "
        "приходили летом — жечь костры на Иванов день, прыгать через огонь, петь. Сейчас здесь "
        "пусто, и только ветер разносит золу.",
        "На склоне дюны видна нора — лисья или барсучья, с утоптанной площадкой у входа. Рядом "
        "валяются перья и косточки. Хозяин, видно, дома, но показываться не спешит.",
    ],
    "spit": [
        "Море слева тяжёлое, свинцовое, с белыми гребнями. Волны идут одна за другой, "
        "неторопливо, но с такой силой, что песок под ногами вздрагивает от каждого удара. "
        "Залив справа, напротив, почти спокоен, по нему лишь бежит мелкая рябь.",
        "Далеко в море виден парус — крохотный, белый, то появляющийся, то исчезающий за "
        "волнами. Кто-то из рыбаков возвращается к посёлку или, наоборот, уходит на промысел, "
        "пока погода позволяет.",
        "На косе нет ни деревьев, ни кустов, только жёсткая трава, которая растёт пучками и "
        "держит песок корнями. Если бы не она, коса давно ушла бы в море. Трава пригибается под "
        "ветром и снова выпрямляется, и так без конца.",
        "На песке у кромки воды лежит выброшенная морем медуза — прозрачная, как стекло, с "
        "лиловым крестом посередине. Рядом — отпечатки птичьих лап и длинная борозда: что-то "
        "тащили к воде или из воды.",
        "Отсюда хорошо видно, как коса изгибается, повторяя очертания берега, и как узка она в "
        "некоторых местах. В сильный шторм, говорят, волны перекатываются через неё насквозь, "
        "и тогда маяк на несколько дней оказывается отрезанным от посёлка.",
    ],
    "lighthouse": [
        "Железные детали здесь выкрашены в чёрный цвет, камень побелён, и этот чёрно-белый "
        "порядок кажется нарочно строгим, как мундир. Смотритель, видно, любил, чтобы всё было "
        "на своих местах.",
        "Ветер свистит в щелях, и где-то наверху мерно постукивает незакреплённая ставенка. От "
        "этого звука кажется, что башня живая и что у неё есть сердце, которое бьётся медленно и "
        "терпеливо.",
        "На полу — пятна от керосина и следы мела, которым, наверное, отмечали что-то важное. "
        "Всё здесь пропитано запахом горючего, и кажется, что стоит поднести спичку к стене — и "
        "камень вспыхнет.",
        "Свет проникает сюда скупо: окна маленькие, стены толстые. Но и в этом скупом свете видно, "
        "что здесь много лет жил человек, который знал цену каждой вещи и каждому часу.",
    ],
    "flat": [
        "Под ногами то и дело попадаются пустые раковины мидий, чёрные и перламутровые, и "
        "хрустят, как битое стекло. Кое-где из песка торчат обломки досок, почерневшие и "
        "обросшие ракушками, — это всё, что осталось от старых кораблей.",
        "Воздух над отмелью дрожит, и дальние предметы расплываются и двоятся. Остов шхуны то "
        "кажется совсем рядом, то словно отодвигается к горизонту. Рыбаки говорят, что отмель "
        "любит шутить с глазами.",
        "Слышно, как далеко-далеко шумит прибой у кромки моря. Этот шум то стихает, то "
        "становится громче, и в нём чудится обещание: вода вернётся, обязательно вернётся, и "
        "тогда здесь не останется ничего, кроме волн.",
        "Песок испещрён следами: птичьими, крабьими и вашими собственными. Ваши следы быстро "
        "наполняются водой и расплываются, будто отмель не хочет помнить, что здесь кто-то был.",
    ],
}
for _k, _v in _FILL_MORE.items():
    _FILL[_k] = _FILL[_k] + _v

_SKY = {
    "morning": ["Утро пасмурное: солнце едва пробивается сквозь туман, всё вокруг серо-жемчужное.",
                "Утреннее солнце низко стоит над морем, и длинные тени лежат на земле."],
    "day": ["День в разгаре: облака несутся по небу, то и дело открывая бледное солнце.",
            "Полдень. Свет ровный и рассеянный, море под ним кажется оловянным."],
    "evening": ["Вечереет: небо на западе окрашивается в медь и розовое, море темнеет.",
                "Солнце клонится к закату, длинные лучи золотят гребни волн."],
    "night": ["Ночь. Темно, только на небе мерцают редкие звёзды и шумит невидимое море.",
              "Стоит глубокая ночь: море и небо слились в одну черноту."],
}

_DETAIL = [
    "Вы разглядываете всё внимательно, не торопясь. Мелочи здесь говорят больше, чем кажется на "
    "первый взгляд: царапины, потёртости, следы рук — всё это оставили люди, которые жили и "
    "работали здесь задолго до вас. У моря вещи служат долго и стареют быстро.",
    "Соль и ветер сделали своё дело: краска облупилась, дерево посерело, металл покрылся "
    "рыжими пятнами. Но видно, что за этим когда-то ухаживали — подкрашивали, подтягивали, "
    "чинили. Теперь, похоже, руки до этого ни у кого не доходят.",
    "Если присмотреться, можно заметить, что здесь недавно кто-то был: пыль в одном месте "
    "стёрта, в другом — отпечаток пальца. Впрочем, это может быть и давний след: в сыром "
    "воздухе всё сохраняется долго и странно.",
    "Ничего особенного, на первый взгляд. Но в таком месте, как Кривая Коса, никогда не "
    "знаешь, что окажется важным: иногда ответ лежит на самом виду, и его просто никто не "
    "замечает.",
    "Вы проводите рукой по поверхности — шершавой, холодной, чуть влажной. Запах соли и "
    "старого дерева. Где-то снаружи хлопает на ветру незакреплённая ставня.",
    "Свет падает неровно, и тени делают знакомые вещи странными. Вы на миг представляете, "
    "как всё это выглядело при прежнем хозяине, — и вещи будто отвечают вам молчанием.",
]
_DETAIL += [
    "Вокруг пахнет морем — этот запах здесь повсюду, он въедается в дерево, в ткань, в кожу. "
    "Сквозь него пробиваются другие: дым, смола, сырость, железо. Каждое место в Кривой Косе "
    "пахнет по-своему, но море слышно в каждом.",
    "Вы задерживаетесь чуть дольше, чем нужно, будто ожидая, что вещь сама расскажет свою "
    "историю. Но вещи здесь молчаливы, как и люди: всё, что они могут сказать, написано на них "
    "самих — в царапинах, в стёртых углах, в выцветшей краске.",
    "Где-то неподалёку слышен голос чайки, потом ещё один, и вот уже целая стая кричит над "
    "берегом. Вы невольно оглядываетесь, но ничего особенного не видите: просто чайки делят "
    "добычу, как делили её здесь всегда.",
    "Холодный сквозняк касается лица. В таких местах всегда кажется, что за спиной кто-то "
    "стоит, — но стоит обернуться, и никого нет, только ветер и шорох песка.",
]
_PAPER = [
    "Бумага пожелтела и покоробилась от сырости, края обтрепались. Чернила кое-где "
    "расплылись, но текст можно разобрать, если читать медленно и внимательно.",
    "Почерк неровный, торопливый, буквы то наклоняются вперёд, то встают прямо — видно, "
    "писали в разном настроении и, может быть, при разном свете. На полях — какие-то "
    "пометки, перечёркнутые слова, кляксы.",
    "Лист сложен в несколько раз, на сгибах бумага протёрлась почти до дыр. Кто-то носил его "
    "с собой долго, доставал, перечитывал и снова прятал.",
]
_MUSINGS = [
    "«Ночью опять шёл дождь. Я сидел на галерее и думал, что огонь маяка — это как слово, "
    "сказанное в темноту: не знаешь, кто его услышит, но говорить надо. Марфа это понимала "
    "лучше меня».",
    "«Море сегодня спокойное, как стекло. В такие дни хочется верить, что всё обойдётся. Но "
    "я слишком долго здесь живу, чтобы верить морю. Оно спокойно только до поры».",
    "«Прохор опять ворчал, что я всё прячу и никому не доверяю. Может, он и прав. Но в "
    "посёлке, где все всё про всех знают, иначе нельзя: скажешь одному — узнают все».",
    "«Думал сегодня о старом смотрителе, что учил меня. Он говорил: маяк не прощает лени. "
    "Каждую ночь, каждую, без пропусков. Если один раз не зажжёшь — кто-нибудь не вернётся».",
    "«Митька опять приходил, спрашивал про узлы. Толковый мальчишка. Если со мной что "
    "случится, пусть ему достанется моя лодка. Надо будет сказать Лукичу — хотя нет, лучше "
    "записать».",
    "«Чайки сегодня кричали особенно громко — к ветру. Барометр падает. Завтра будет шторм, а "
    "керосина мало. Надо что-то придумать».",
    "«Иногда мне кажется, что я разговариваю не с дневником, а с Марфой. Она всегда слушала "
    "молча, а потом говорила одно слово — и всё становилось ясно».",
    "«Грач — птица осторожная. Так и я: гнездо своё прячу, а что важное — раскладываю по "
    "разным местам, чтобы никто не собрал всё сразу, кроме того, кто по-настоящему ищет».",
]

_HELP = """Команды (вводятся по-русски: python3 game.py <команда>):
  осмотреться (о)             — описание места, где вы находитесь
  идти <направление>          — север/юг/восток/запад/вверх/вниз/внутрь/наружу; можно просто «север» или «с»
  осмотреть <предмет>         — рассмотреть предмет, человека или деталь обстановки
  читать <предмет>            — прочитать записку, страницу, надпись, таблицу
  взять <предмет>, бросить <предмет>, инвентарь (и)
  говорить <кто>              — начать разговор; затем «ответ <номер>» — выбрать реплику
  дать <предмет> <кому>       — отдать предмет
  использовать <предмет> [на/в <что>] — применить предмет (синонимы: вставить, залить, зажечь, завести, протереть)
  открыть <что> [слово или слова] — открыть дверь, замок, сундук, секретку
                              (например: открыть сундук слово; открыть ворота слово слово слово)
  ждать [минут]               — подождать (по умолчанию 30 минут, не больше 300); «ждать 2 часа»
  время                       — узнать время (если есть часы)
  помощь                      — эта справка
  новая                       — начать игру заново (журнал очищается)
Предметы называйте так, как они названы в описаниях (падеж неважен), при необходимости с уточнением: «читать страницу 11».
Игровое время идёт: переходы занимают 3–15 минут, разговоры и чтение — несколько минут."""

_INTRO = """«МАЯК НА КРИВОЙ КОСЕ»

Сентябрь 1905 года. Лодка из города высаживает вас на причале рыбацкого посёлка Кривая Коса и тут же
уходит обратно. Вы — новый помощник смотрителя маяка. В кармане — предписание Управления маяков:
смотритель пропал, маяк погас, а в ночь с 16 на 17 сентября мимо рифов у мыса пройдёт пароход
«Святая Ольга». Огонь должен гореть. Письмо с предписанием вы оставили в комнате над трактиром,
которую для вас сняли; там же ваши вещи."""

_VICTORY = """*** Маяк на Кривой косе горит! ***

Луч медленно обходит тёмное море, выхватывая из ночи гребни волн, мыс и пенную полосу над рифами.
Где-то внизу, в посёлке, звонит средний колокол часовни — люди выходят из домов и смотрят на огонь.
Пароход «Святая Ольга» пройдёт мимо рифов благополучно. А на Птичьем острове, в больничной келье
скита, старец Варсонофий подводит к окну смотрителя Савелия Грача, и тот, увидев луч, впервые за
много дней засыпает спокойно. Игра пройдена."""

_CRASH = """Четыре часа утра 17 сентября. Маяк так и не загорелся. Из темноты доносится протяжный гудок,
потом — страшный скрежет железа о камни рифов... Игра окончена. Начните заново: python3 game.py новая"""

_OVER = {
    "победа": "Маяк горит, игра пройдена. Чтобы начать заново: python3 game.py новая",
    "крушение": "Игра окончена: маяк не загорелся вовремя. Начните заново: python3 game.py новая",
}

_FILL["cape"] = [
    "Мыс открыт всем ветрам. Здесь не растёт ничего выше колена: низкая вороника, мох, "
    "кустики морошки, прижатые к камню. Ветер приходит с моря без помех и так давит в грудь, "
    "что приходится наклоняться вперёд, как на косе, только здесь ему не мешают даже дюны.",
    "Под ногами — серый гранит, отполированный льдом тысячи лет назад и исчерченный "
    "бороздами, как будто по нему протащили огромные бороны. В трещинах скопилась дождевая "
    "вода, и в каждой такой лужице отражается кусочек бегущего неба.",
    "Со всех сторон слышно море — не так, как в посёлке, приглушённо, а в полный голос: "
    "внизу, у подножия мыса, волны разбиваются о камни с глухим пушечным ударом, и после "
    "каждого удара в воздухе долго висит мелкая солёная пыль.",
    "Над мысом всё время кружат чайки-моевки, и их крик — «кить-вэйк, кить-вэйк» — не "
    "смолкает ни на минуту. Иногда одна из них зависает прямо над головой, неподвижно, на "
    "распахнутых крыльях, и смотрит вниз круглым жёлтым глазом.",
    "Всё здесь сделано надолго и на совесть: камень пригнан к камню, железо просмолено, "
    "дерево пропитано олифой. Но соль и ветер всё равно своё берут: краска шелушится, болты "
    "обрастают ржавой бахромой, и видно, что без хозяина мыс начинает дичать.",
    "Отсюда, с мыса, особенно ясно видно, почему здесь нужен огонь: за оконечностью, в "
    "полумиле, море вдруг вскипает белым над невидимыми камнями, и даже днём, в тихую "
    "погоду, от этого вида становится не по себе.",
    "Туман приходит с моря внезапно: только что был виден остров, и вот уже вместо него — "
    "серая стена, и вокруг мокро и глухо, и даже чайки замолкают. Потом ветер рвёт туман в "
    "клочья, и остров снова выступает из него, как корабль.",
    "Земли здесь мало, её приносили на мыс мешками, и каждая грядка, каждый клочок травы — "
    "чья-то многолетняя работа. Кажется, что мыс держится не на граните, а на упрямстве "
    "людей, которые на нём жили.",
]
_FILL["island"] = [
    "Остров живёт птицами. Они повсюду: на скалах, на валунах, в вереске, в воздухе. Крики "
    "не смолкают ни на минуту, и к ним быстро привыкаешь, как к шуму прибоя. Пахнет рыбой, "
    "помётом, водорослями и мёдом цветущего вереска — странная смесь, которую не спутаешь ни с "
    "чем.",
    "Вереск здесь низкий, жёсткий, пружинит под ногами. Среди него попадаются кустики "
    "голубики с последними сизыми ягодами, пучки белого ягеля и серые камни, облепленные "
    "рыжим лишайником, словно ржавчиной.",
    "С острова видно всё побережье: длинную косу с посёлком у её основания, дюны, белую "
    "башню маяка на мысу. Отсюда кажется, что посёлок совсем близко, — но между ним и островом "
    "лежат вода и отмель, и пешком туда не дойти никогда.",
    "Ветер на острове переменчивый: то дует с моря, то вдруг заходит с берега и приносит "
    "запах дыма из посёлка. Облака несутся низко, их тени скользят по вереску, и остров "
    "становится то бурым, то лиловым, то почти чёрным.",
    "Под ногами попадаются кости птиц, выбеленные солнцем, и пустые скорлупки яиц — голубые, "
    "пятнистые, зеленоватые. Где-то в камнях шипит потревоженная гага, прикрывая крыльями "
    "поздний выводок.",
    "Тропинки на острове протоптаны не людьми, а временем: их протоптали монахи, потом "
    "рыбаки, потом птицеловы, и каждая ведёт туда, где кому-то когда-то было нужно. Многие "
    "уже заросли, и только камни, уложенные поперёк ручьёв, выдают, что здесь ходили.",
    "Море вокруг острова беспокойное даже в тихий день: у камней оно всё время вздыхает, "
    "поднимается и опадает, и водоросли на прибрежных валунах то всплывают, как волосы, то "
    "снова ложатся на камень.",
    "Здесь очень тихо — если не считать птиц и моря. Нет ни стука топора, ни лая собак, ни "
    "скрипа колодезного ворота. Остров кажется местом, где время остановилось лет сто назад и "
    "с тех пор не знает, куда ему идти.",
]
_FILL["skete"] = [
    "В скиту стоит особая тишина — не пустая, а будто наполненная: так тихо бывает в церкви "
    "после службы, когда все разошлись, а запах ладана ещё держится. Даже птичий гомон с "
    "базара доносится сюда приглушённо, как из-за толстой стены.",
    "Всё в скиту построено из того, что давал остров и море: плавник, камень, мох. Брёвна "
    "стен — корабельный лес, выброшенный штормами, в них ещё видны дыры от нагелей и следы "
    "железных скоб. Монахи не брезговали ничем, что посылал Бог.",
    "Трава между строениями выкошена узкими дорожками — ровно столько, сколько нужно одному "
    "старому человеку, чтобы дойти от кельи до церкви, до колодца и до огорода. Остальное "
    "заросло иван-чаем и крапивой выше пояса.",
    "На всём здесь лежит печать запустения и заботы одновременно: окна келий заколочены, "
    "крыши просели, но на тропинках нет сора, у крыльца стоит веник, а перед иконами горят "
    "лампады. Один человек держит целую обитель, сколько хватает сил.",
    "Сквозь щели в стенах пробивается свет, и в его полосах медленно кружится пыль. Пахнет "
    "сухим деревом, воском, мышами и чуть-чуть — морем, которое здесь чувствуется везде, даже "
    "в самой глубине строений.",
    "Время от времени откуда-то доносится тихий стук — то ли ставня на ветру, то ли старец "
    "колет дрова, то ли просто старое дерево оседает, вздыхая. Эти звуки не пугают, а "
    "успокаивают: в скиту кто-то жив.",
    "Над скитом всё время кружат чайки, но на крыши не садятся: говорят, монахи когда-то "
    "отгоняли их молитвой, и с тех пор птицы помнят. Правда это или нет, но на куполе не видно "
    "ни одного белого потёка.",
    "Если прислушаться, можно различить, как где-то бормочет вода — это ручей из колодца "
    "уходит под землю и выбивается на берегу. Вода — главное богатство острова; без неё здесь "
    "не выжил бы ни один монах.",
]
_FILL["ship"] = [
    "Всё судно перекошено, и от этого кружится голова: пол уходит вбок, переборки клонятся, "
    "и глаз никак не может найти прямую линию. Приходится держаться за что попало и ставить "
    "ногу осторожно, нащупывая, выдержит ли доска.",
    "Дерево набухло от воды и почернело, железо рассыпается рыжими хлопьями, стоит его "
    "тронуть. Пахнет гнилыми водорослями, ржавчиной, керосином и чем-то сладковатым, "
    "тяжёлым — так пахнет долгая смерть корабля.",
    "Сквозь щели и проломы пробиваются полосы серого света, и в них видно, как в воздухе "
    "висит мелкая водяная пыль. Где-то внизу, под настилом, плещет и булькает вода, то "
    "поднимаясь, то уходя.",
    "Каждый порыв ветра отзывается в корпусе долгим стоном: шхуна ещё скрипит, как живая, "
    "хотя жить ей осталось, может быть, одну-две зимы. Лёд и шторма добьют её, и отмель "
    "затянет обломки песком.",
    "На всём — песок: в углах, в щелях, на полках, в складках мокрой парусины. Море за три года "
    "нанесло его столько, что кое-где он лежит по щиколотку, и в нём попадаются ракушки и "
    "мелкие крабы, не успевшие уйти с водой.",
    "Вещи людей, живших здесь, лежат там, где их застал шторм: кружка, сапог, рукавица, "
    "обрывок письма, разбухший до неузнаваемости. Никто не пришёл за ними, и теперь они "
    "принадлежат морю.",
]


# ---------------------------------------------------------------------------
# Build zones, encrypt them, produce game.py and data files
# ---------------------------------------------------------------------------

# The three words of each zone lock (only their first five letters matter).
_ANSWERS = {
    "z1": ("заступник", "веретенников", "холодною"),  # bell for trouble, old keeper, Marfa's song
    "z2": ("благодать", "багульник", "полкан"),  # first ship under the light, herb, dog
    "z3": ("трофим", "спиридон", "сохрани"),  # last off the wreck, icon, sign inscription
}
# Wrong but plausible phrases (traps in the texts), used by a near miss.
_TRAPS = {
    "z1": ("касатка", "веретенников", "холодною"),
    "z2": ("кемь", "полынь", "полкан"),
    "z3": ("шелепов", "спиридон", "сохрани"),
}

_ZONE_INTRO = {
    "z1": ("Вы толкаете створку и входите в маячный двор. Вблизи башня кажется огромной; "
           "ветер гудит в её стёклах. Дом смотрителя стоит с незапертой дверью, будто хозяин "
           "вышел на минуту."),
    "z2": ("Тележка с вельботом свободна. Если поставить уключины и дождаться воды, можно "
           "спустить вельбот по слипу и идти через пролив к Птичьему острову."),
    "z3": ("Из-за ворот тянет дымом и ладаном. Где-то в глубине скита кашляет человек, и "
           "старческий голос нараспев читает псалом."),
}


def _canon(words) -> list[str]:
    return [w if w.isdigit() else w[:5] for w in words]


def _doc(key: str) -> str:
    if key.startswith("__PAGE_"):
        pid = key[len("__PAGE_"):-2]
        r = random.Random("page/" + pid)
        mus = r.sample(_MUSINGS, 4)
        return ("Листок из дневника смотрителя.\n\n" + r.choice(_PAPER) + "\n\n" + mus[0] + "\n\n"
                + mus[1] + "\n\n" + _PAGES[pid][1] + "\n\n" + mus[2] + "\n\n" + mus[3])
    name = key.strip("_")[len("DOC_"):]
    r = random.Random("doc/" + name)
    return _DOCS[name] + "\n\n" + "\n\n".join(r.sample(_PAPER, 2))


def _with_detail(text: str, seed: str, k: int = 3) -> str:
    r = random.Random(seed)
    return text + "\n\n" + "\n\n".join(r.sample(_DETAIL, k))


def _fix_obj(obj: dict, seed: str) -> dict:
    out = {k: v for k, v in obj.items() if k != "_z"}
    out["desc"] = _with_detail(obj["desc"], seed)
    if "desc_alt" in obj:
        out["desc_alt"] = [{"if": a["if"], "text": _with_detail(a["text"], seed)} for a in obj["desc_alt"]]
    if isinstance(obj.get("read"), str) and obj["read"].startswith("__"):
        out["read"] = _doc(obj["read"])
    return out


def _item_zone(it: dict) -> int:
    return it["_z"] if it["_z"] is not None else _LOCS[it["at"]]["z"]


@functools.cache
def _zones() -> list[dict]:
    """World data per zone: [zone0 (base), zone1, zone2, zone3]."""
    r = random.Random(TASK_ID + "/world")
    decks: dict[str, list[int]] = {}
    zones: list[dict] = [{"locs": {}, "items": {}, "npcs": {}, "gives": [], "uses": []} for _ in range(4)]
    for lid, loc in _LOCS.items():
        bank = _FILL[loc["bank"]]
        deck = decks.setdefault(loc["bank"], [])
        paras: list[int] = []
        while len(paras) < 5:
            if not deck:
                deck.extend(r.sample(range(len(bank)), len(bank)))
            i = deck.pop()
            if i not in paras:
                paras.append(i)
        fill = [bank[i] for i in paras]
        text = "\n\n".join(fill[:2] + [loc["core"]] + fill[2:])
        out = {"name": loc["name"], "text": text, "exits": loc["exits"], "outdoor": loc["outdoor"],
               "feats": {fid: _fix_obj(f, f"{lid}/{fid}") for fid, f in loc["feats"].items()}}
        if loc["sea"]:
            out["sea"] = loc["sea"]
        if loc["dyn"]:
            out["dyn"] = loc["dyn"]
        zones[loc["z"]]["locs"][lid] = out
    for iid, it in _ITEMS.items():
        zones[_item_zone(it)]["items"][iid] = _fix_obj(it, "item/" + iid)
    for nid, npc in _NPCS.items():
        zones[_LOCS[npc["loc"]]["z"]]["npcs"][nid] = npc
    for z in range(4):
        zones[z]["gives"] = _GIVES_Z.get(z, [])
        zones[z]["uses"] = _USES_Z.get(z, [])
        if z:
            zones[z]["patch_opts"] = _PATCH_OPTS.get(z, [])
            zones[z]["intro"] = _ZONE_INTRO[f"z{z}"]
    zones[1]["top"] = {"victory": _VICTORY}
    zones[0].update({
        "start": "pier", "start_time": _START, "lows": _LOWS, "tide_half": _HALF, "dusk": _DUSK,
        "dawn": _DAWN, "deadline": _DEADLINE, "sky": _SKY, "help": _HELP, "intro": _INTRO,
        "victory": "", "crash": _CRASH, "over": _OVER,
    })
    return zones


def _game_source() -> str:
    return ((_GAME_SRC_1 + _GAME_SRC_2 + _GAME_SRC_3)
            .replace("__SALT__", _SALT).replace("__ITER__", str(_ITER)))


def _exec_engine() -> dict:
    ns: dict = {"__name__": "hb_lighthouse_game", "__file__": "/nonexistent/game.py"}
    exec(compile(_game_source(), "<lighthouse-game>", "exec"), ns)  # noqa: S102
    return ns


@functools.cache
def _keys() -> dict[str, bytes]:
    ns = _exec_engine()
    return {z: ns["derive"](z, _canon(words)) for z, words in _ANSWERS.items()}


def _pack(ns: dict, key: bytes, data: dict) -> bytes:
    raw = zlib.compress(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8"), 9)
    return ns["_stream"](key, raw)


@functools.cache
def _blobs() -> dict[str, bytes]:
    ns = _exec_engine()
    zones = _zones()
    keys = _keys()
    base = dict(zones[0])
    base["zones"] = {z: {"file": f"world{z[1]}.dat", "tag": ns["key_tag"](k)} for z, k in keys.items()}
    out = {"world0.dat": _pack(ns, hashlib.sha256(_SALT.encode()).digest(), base)}
    for z, k in keys.items():
        out[f"world{z[1]}.dat"] = _pack(ns, k, zones[int(z[1])])
    return out


@functools.cache
def _engine() -> dict:
    """Hidden engine copy: game.py source + world data held in memory (never read from ws)."""
    ns = _exec_engine()
    ns["_BLOBS"].update(_blobs())
    return ns


_WIN = """\
осмотреться
север
восток
вверх
читать стол
взять часы
вниз
говорить агафья
ответ 2
ответ 5
ответ 6
ответ 8
вниз
взять ром
взять страницу
читать страницу 9
вверх
запад
север
запад
читать таблицы
читать реестр
говорить лукич
ответ 3
ответ 7
восток
север
север
говорить никодим
ответ 1
ответ 4
вверх
осмотреть колокола
вниз
восток
читать валун
запад
север
север
запад
север
взять свисток
вверх
взять страницу
читать страницу 11
вниз
юг
запад
взять страницу
читать страницу 14
восток
юг
взять страницу
читать страницу 17
север
восток
север
север
взять страницу
читать страницу 23
юг
юг
восток
север
дать ром прохору
говорить прохор
ответ 3
ответ 5
взять мешок
юг
восток
восток
восток
север
открыть ворота заступник веретенников холодною
север
внутрь
восток
взять рукоять
запад
вверх
вверх
читать вахтенный журнал
взять записку
читать записку
вверх
осмотреть линзу
вниз
вниз
вниз
наружу
восток
юг
читать тетрадь
север
вверх
читать матросский сундук
вниз
восток
восток
юг
читать холмик под рябиной
север
запад
запад
запад
север
север
вниз
вниз
внутрь
открыть цепь благодать багульник полкан
наружу
вверх
вверх
юг
юг
юг
юг
запад
запад
запад
запад
юг
юг
юг
юг
восток
дать рукоять ермолаю
запад
юг
юг
запад
говорить митя
ответ 1
ответ 4
дать свисток мите
открыть дверь
юг
открыть сундук зарянка
взять уключины
север
восток
восток
взять ветошь
запад
север
север
север
север
север
север
восток
восток
восток
восток
север
север
север
север
вниз
вниз
внутрь
использовать уключины
ждать 125
юг
юг
говорить кирьян
ответ 1
ответ 2
ответ 4
ответ 7
север
запад
север
запад
осмотреть нишу
взять записку
читать записку из грота
восток
юг
запад
читать надписи
восток
север
ждать 300
ждать 205
север
восток
внутрь
читать журнал
наружу
вниз
взять бидон
взять сумку
осмотреть сумку
взять письмо
вверх
запад
юг
юг
запад
запад
открыть ворота трофим спиридон сохрани
внутрь
запад
вверх
читать летопись
вниз
восток
восток
восток
говорить смотритель
ответ 1
ответ 4
осмотреть сумку смотрителя
взять страницу
читать страницу 20
запад
север
говорить старец
ответ 3
ответ 3
юг
запад
наружу
восток
восток
восток
север
наружу
вверх
вверх
юг
юг
юг
юг
запад
запад
запад
запад
юг
юг
юг
запад
говорить аграфена
ответ 3
восток
юг
восток
говорить ермолай
ответ 1
запад
юг
запад
дать письмо фоме
восток
север
север
север
север
север
восток
восток
восток
восток
север
север
внутрь
вверх
вверх
вверх
использовать ветошь
использовать призму
вставить фитиль в горелку
залить керосин в лампу
завести механизм
время
использовать сухие спички
"""


# ---------------------------------------------------------------------------
# Workspace, check, near misses
# ---------------------------------------------------------------------------

def _win_lines() -> list[str]:
    return [ln.strip() for ln in _WIN.splitlines() if ln.strip() and not ln.startswith("#")]


def _write_play(ws: Path, lines: list[str], state: dict | None = None) -> dict:
    """Write journal.log and save.json exactly as game.py would after `lines`."""
    ns = _engine()
    final = ns["replay"](lines) if state is None else state
    write(ws, "save.json", json.dumps({"state": final, "sig": ns["_sig"](final)}, ensure_ascii=False))
    write(ws, "journal.log", "".join(ln + "\n" for ln in lines))
    return final


def _setup(ws: Path) -> None:
    write(ws, "game.py", _game_source(), executable=True)
    for name, data in sorted(_blobs().items()):
        write(ws, f"data/{name}", data)


def _gold(ws: Path) -> None:
    _write_play(ws, _win_lines())


def _check(ws: Path) -> str:
    ns = _engine()
    lines = [ln.strip() for ln in read_text(ws, "journal.log").splitlines() if ln.strip()]
    assert lines, "journal.log пуст: игра не велась"
    saved = read_json(ws, "save.json")
    assert isinstance(saved, dict) and isinstance(saved.get("state"), dict), "save.json: неверная структура"
    assert saved.get("sig") == ns["_sig"](saved["state"]), "save.json изменён вручную (подпись не сходится)"
    final = json.loads(json.dumps(ns["replay"](lines), ensure_ascii=False))
    assert final == saved["state"], "журнал не воспроизводит сохранённое состояние игры"
    assert final.get("ending") == "победа", "маяк не зажжён: победный финал не достигнут"
    return f"журнал: {len(lines)} команд, финал «победа» воспроизведён"


def _replace_line(lines: list[str], prefix: str, new: str) -> list[str]:
    i = next(k for k, ln in enumerate(lines) if ln.startswith(prefix))
    return lines[:i] + [new] + lines[i + 1:]


def nm_journal_truncated(ws: Path) -> None:
    """The journal lost its last command (the lighting); the save is still the final one."""
    lines = _win_lines()
    write(ws, "journal.log", "".join(ln + "\n" for ln in lines[:-1]))


def nm_tampered_save(ws: Path) -> None:
    """Played until the lamp room, then edited save.json by hand to claim the ending."""
    ns = _engine()
    lines = _win_lines()[:-6]
    state = ns["replay"](lines)
    sig = ns["_sig"](state)
    state["ending"] = "победа"
    state["flags"]["lit"] = True
    write(ws, "save.json", json.dumps({"state": state, "sig": sig}, ensure_ascii=False))
    write(ws, "journal.log", "".join(ln + "\n" for ln in lines))


def nm_skips_zone_unlock(ws: Path) -> None:
    """Journal without the command that unlocks the whaleboat chain (zone 2 decoded elsewhere)."""
    lines = [ln for ln in _win_lines() if not ln.startswith("открыть цепь")]
    _write_play(ws, lines)


def nm_wrong_key_phrase(ws: Path) -> None:
    """Gate of the lighthouse yard opened with the superseded top word (the first boat)."""
    lines = _replace_line(_win_lines(), "открыть ворота " + _ANSWERS["z1"][0][:4],
                          "открыть ворота " + " ".join(_TRAPS["z1"]))
    _write_play(ws, lines)


def nm_wrong_hermit_answer(ws: Path) -> None:
    """Answered the hermit with the builder of the day-mark instead of the first fire."""
    lines = _win_lines()
    i = lines.index("говорить старец")
    j = next(k for k in range(i, len(lines)) if lines[k] == "ответ 3" and lines[k - 1] == "ответ 3")
    lines = lines[:j] + ["ответ 2"] + lines[j + 1:]
    _write_play(ws, lines)


def nm_ignores_smith_delay(ws: Path) -> None:
    """Journal from an engine that hands back the crank right away (no two-hour wait)."""
    lines = _win_lines()
    i = lines.index("дать рукоять ермолаю")
    j = lines.index("говорить ермолай", i)
    moved = lines[:i + 1] + [lines[j], lines[j + 1]] + [
        ln for k, ln in enumerate(lines[i + 1:], i + 1) if k not in (j, j + 1)]
    _write_play(ws, _win_lines())
    write(ws, "journal.log", "".join(ln + "\n" for ln in moved))


def nm_no_save(ws: Path) -> None:
    """Journal kept, save.json deleted."""
    (ws / "save.json").unlink()


NEAR_MISSES = [nm_journal_truncated, nm_tampered_save, nm_skips_zone_unlock, nm_wrong_key_phrase,
               nm_wrong_hermit_answer, nm_ignores_smith_delay, nm_no_save]

_PROMPT = """\
В рабочем каталоге — текстовый квест `game.py` «Маяк на Кривой косе» (данные мира — в каталоге
`data/`). Играть в него нужно через командную строку: `python3 game.py <команда>`, например
`python3 game.py помощь` или `python3 game.py осмотреться`. Каждая команда — отдельный запуск.
Игра сама хранит состояние в `save.json` (с подписью) и записывает каждую команду в `journal.log`;
`python3 game.py новая` начинает игру заново и очищает журнал.

Задача: пройти игру до победного финала — зажечь маяк. В игре идут игровые часы, и некоторые
действия возможны только в определённое время. Мир большой, описания и документы длинные, а нужные
сведения разбросаны по местам, документам, надписям и разговорам с жителями, иногда противоречат
друг другу или уточняются позже; читай внимательно. Части мира открываются по мере прохождения:
их данные зашифрованы и становятся доступны только в игре.

Проверка: скрытая копия движка заново проигрывает `journal.log` с начальной позиции; она должна
дойти до победного финала и получить в точности то состояние, что записано в `save.json`. Не
редактируй `save.json`, `journal.log`, `game.py` и `data/` вручную — только команды игры.
"""

TASK = long_task(
    id="task_404_text_adventure",  # registry id; TASK_ID stays the generator seed
    name="Текстовый квест «Маяк на Кривой косе» через CLI",
    prompt=_PROMPT,
    setup=_setup,
    gold=_gold,
    check=_check,
    tags=("interactive", "cli", "game"),
)
