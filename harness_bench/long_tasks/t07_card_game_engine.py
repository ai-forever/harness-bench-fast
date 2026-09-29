"""long_07_card_game_engine — движок карточной игры «Туманная гавань» по правилам.

The agent reads ``rules.md`` (about 45K chars) and 250 card files (card
text in free Russian wording, flavour text, judge rulings and errata) and
writes ``engine.py`` with ``simulate(game) -> dict``. The reference engine is
kept in this module as source (``_ENGINE_SRC``) with the card table embedded;
hidden games are produced by playing the reference engine with a random but
legal decision policy and recording every decision, so the replay is exact.

Ground truth never comes from rendered text: card definitions are structured
data (``_card_defs``); texts, rulings and errata are rendered from them.

Every card file also carries 3–6 situation rulings: a concrete position at the
table resolved by the reference engine and replayed with several generator seeds
(only seed-independent quantities are asked). The body of each card file (text,
rulings, errata) has a ``REWRITE`` hook: an LLM paraphrase is used only when an
independent reader recovers the printed and effective card, every ruling's answer
and every situation's circumstances (``scripts/rewrite_long_texts.py``).
"""

from __future__ import annotations

import copy
import functools
import json
import random
import re
from collections import Counter
from pathlib import Path

from .common import long_task, rewritten, rng, run_python, write, write_json

TASK_ID = "long_07_card_game_engine"
N_HIDDEN = 100
N_EXAMPLES = 12
MIN_OK = 95

# ---------------------------------------------------------------------------
# Reference engine (written verbatim into the workspace by gold)
# ---------------------------------------------------------------------------

_ENGINE_SRC = r'''"""Движок игры «Туманная гавань»: simulate(game) -> dict (только стандартная библиотека)."""

from __future__ import annotations

import json

CARDS = json.loads(__CARDS__)
_V = set(__VARIANT__)

M31 = 2 ** 31
BASE = {"Грош", "Гривна", "Червонец", "Причал", "Склад", "Гавань", "Тина"}


class PlanError(ValueError):
    pass


def _is(name, t):
    return t is None or t in CARDS[name]["types"]


class Player:
    def __init__(self, idx, spec):
        self.idx = idx
        self.name = spec["name"]
        self.deck = list(spec["deck"])
        self.hand = []
        self.discard = []
        self.table = []
        self.pier = []
        self.glory = 0
        self.coins_total = 0
        self.draws = 0
        self.shuffles = 0

    def owned(self):
        return (self.deck + self.hand + self.discard + [e["name"] for e in self.table]
                + [e["name"] for e in self.pier])


class Replay:
    def __init__(self, turns):
        self.turns = list(turns)
        self.pos = 0
        self.plays = []
        self.buys = []
        self.choices = []

    def start_turn(self, g, p):
        if self.pos >= len(self.turns):
            raise PlanError("план закончился раньше партии")
        t = self.turns[self.pos]
        self.pos += 1
        self.plays = list(t.get("plays", []))
        self.buys = list(t.get("buys", []))

    def next_play(self, g, p):
        if not self.plays:
            return None
        e = self.plays.pop(0)
        self.choices = list(e.get("choices", []))
        return e["card"]

    def end_play(self, g, p):
        if self.choices:
            raise PlanError("лишние решения в розыгрыше")

    def choice(self, g, p, kind, eff, info):
        if not self.choices:
            raise PlanError("не хватает решений")
        return self.choices.pop(0)

    def next_buy(self, g, p):
        return self.buys.pop(0) if self.buys else None

    def finished(self):
        return self.pos == len(self.turns)


class Game:
    def __init__(self, game, decider=None, trace=False):
        self.players = [Player(i, s) for i, s in enumerate(game["players"])]
        self.n = len(self.players)
        self.supply = {k: int(v) for k, v in game["supply"].items()}
        self.trash = {}
        self.rng = int(game["seed"]) % M31
        self.max_rounds = int(game["max_rounds"])
        self.dec = decider or Replay(game["turns"])
        self.log = [] if trace else None
        self.active = -1
        self.uid = 0
        self.round = 0
        self.stats = {}
        self._buying = False
        self._cleanup = False
        self._reset_turn()

    # -- helpers -----------------------------------------------------------
    def _reset_turn(self):
        self.actions = 0
        self.buys = 0
        self.coins = 0
        self.cut = 0
        self.to_top = False
        self.played = []

    def say(self, text):
        if self.log is not None:
            self.log.append(text)

    def nm(self, q):
        return self.players[q].name

    def mark(self, card):
        self.stats[card] = self.stats.get(card, 0) + 1

    def rand(self):
        self.rng = (self.rng * 1103515245 + 12345) % M31
        return self.rng

    def order(self):
        a = self.active if self.active >= 0 else 0
        return [(a + k) % self.n for k in range(self.n)]

    def cost(self, name):
        return max(0, CARDS[name]["cost"] - self.cut)

    def shuffle_discard(self, pl):
        pile = pl.discard
        pl.discard = []
        for i in range(len(pile) - 1, 0, -1):
            j = self.rand() % (i + 1)
            pile[i], pile[j] = pile[j], pile[i]
        pl.deck = pile + pl.deck
        pl.shuffles += 1
        self.say(f"    {pl.name} перемешивает сброс ({len(pile)} карт) и кладёт его колодой")
        self.shuffle_event(pl, len(pile))

    def shuffle_event(self, pl, m):
        if self._cleanup and "shuffle_in_cleanup" not in _V:
            return
        for b in list(pl.pier):
            for trg in CARDS[b["name"]].get("triggers", []):
                if trg["on"] == "shuffle" and m >= trg.get("min", 0):
                    self.say(f"    перемешивание: постройка «{b['name']}» ({pl.name})")
                    self.mark(b["name"] + "#shuffle")
                    self.resolve(pl.idx, trg["effects"], {"card": b["name"], "entry": None})

    def top(self, pl):
        if not pl.deck:
            if not pl.discard:
                return None
            self.shuffle_discard(pl)
        return pl.deck.pop(0)

    def draw(self, pl, n):
        got = []
        for _ in range(n):
            c = self.top(pl)
            if c is None:
                break
            pl.hand.append(c)
            pl.draws += 1
            got.append(c)
        if got:
            self.say(f"    {pl.name} берёт: {', '.join(got)}")
        elif n:
            self.say(f"    {pl.name} не может взять карту: колода и сброс пусты")
        return got

    def add_coins(self, q, n):
        if q == self.active and n:
            self.coins += n
            self.players[q].coins_total += n
            self.say(f"    +{n} мон. (всего {self.coins})")
        elif n:
            self.say(f"    {self.nm(q)}: монеты вне своего хода сгорают")

    def gain(self, q, name, dest, bought=False):
        if self.supply.get(name, 0) <= 0:
            self.say(f"    стопка «{name}» пуста — {self.nm(q)} ничего не получает")
            return False
        self.supply[name] -= 1
        pl = self.players[q]
        if dest == "hand":
            pl.hand.append(name)
        elif dest == "top":
            pl.deck.insert(0, name)
        else:
            pl.discard.append(name)
        where = {"hand": "в руку", "top": "на верх колоды"}.get(dest, "в сброс")
        self.say(f"    {pl.name} {'покупает' if bought else 'получает'} «{name}» {where}")
        if bought:
            self.event("buy", q, name)
        self.event("gain", q, name)
        return True

    def trash_card(self, q, name):
        self.trash[name] = self.trash.get(name, 0) + 1
        self.say(f"    «{name}» ({self.nm(q)}) уничтожена")
        self.event("trash", q, name)

    # -- events and triggers -----------------------------------------------
    def event(self, kind, q, name, src=None):
        own = {"buy": "on_buy", "gain": "on_gain", "trash": "on_trashed"}.get(kind)
        card = CARDS[name]
        if own == "on_gain" and "buy_is_not_gain" in _V and self._buying:
            own = None
        if own and card.get(own):
            self.say(f"    свойство «{name}» ({own})")
            self.mark(name + "#" + own)
            self.resolve(q, card[own], {"card": name, "entry": None})
        owners = self.order()
        if "own_last" in _V:
            owners = owners[1:] + owners[:1]
        for owner in owners:
            for b in list(self.players[owner].pier):
                for trg in CARDS[b["name"]].get("triggers", []):
                    if self.matches(trg, kind, owner, q, name, b, src):
                        self.say(f"    срабатывает постройка «{b['name']}» ({self.nm(owner)})")
                        self.mark(b["name"] + "#" + trg["on"])
                        self.resolve(owner, trg["effects"], {"card": b["name"], "entry": None})

    def matches(self, trg, kind, owner, q, name, b, src):
        on = trg["on"]
        if on in ("buy", "gain", "trash", "play"):
            if on != kind or owner != q:
                return False
            if kind == "gain" and "buy_is_not_gain" in _V and self._buying:
                return False
            if not _is(name, trg.get("filter")):
                return False
            if on == "play":
                if src is not None and b["uid"] == src:
                    return False
                if trg.get("first"):
                    cnt = sum(1 for x in self.played if _is(x, trg.get("filter")))
                    if "first_since_built" in _V:
                        cnt = sum(1 for x in self.played[b.get("pos", 0):] if _is(x, trg.get("filter")))
                    return cnt == 1
            return True
        if on == "opp_curse":
            return kind == "gain" and name == "Тина" and owner != q
        if on == "opp_attack":
            return kind == "play" and "Интрига" in CARDS[name]["types"] and owner != q
        return False

    def reactions(self, p):
        prot = set()
        for o in self.order()[1:]:
            pl = self.players[o]
            snap = [c for c in pl.hand if "Оберег" in CARDS[c]["types"]]
            for c in snap:
                r = CARDS[c]["react"]
                self.say(f"    {pl.name} показывает оберег «{c}»")
                self.mark(c + "#react")
                if r.get("protect"):
                    prot.add(o)
                self.resolve(o, r.get("effects", []), {"card": c, "entry": None})
        return prot

    # -- effect resolution ---------------------------------------------------
    def resolve(self, q, effects, ctx):
        for eff in effects:
            getattr(self, "op_" + eff["op"])(q, eff, ctx)

    def ask(self, q, kind, eff, info=None):
        return self.dec.choice(self, q, kind, eff, info)

    def op_draw(self, q, e, ctx):
        self.draw(self.players[q], e["n"])

    def op_actions(self, q, e, ctx):
        if q == self.active:
            self.actions += e["n"]
            self.say(f"    +{e['n']} действ. (всего {self.actions})")

    def op_buys(self, q, e, ctx):
        if q == self.active:
            self.buys += e["n"]
            self.say(f"    +{e['n']} покуп. (всего {self.buys})")

    def op_coins(self, q, e, ctx):
        self.add_coins(q, e["n"])

    def op_glory(self, q, e, ctx):
        self.players[q].glory += e["n"]
        self.say(f"    {self.nm(q)}: +{e['n']} славы (всего {self.players[q].glory})")

    def _pick_hand(self, q, kind, e, limit):
        pl = self.players[q]
        pick = self.ask(q, kind, e)
        if not isinstance(pick, list) or len(pick) > limit:
            raise PlanError(f"неверное решение {kind}: {pick!r}")
        rest = list(pl.hand)
        for c in pick:
            if c not in rest:
                raise PlanError(f"карты {c!r} нет в руке")
            rest.remove(c)
        return pick

    def op_trash_hand(self, q, e, ctx):
        pl = self.players[q]
        for c in self._pick_hand(q, "trash_hand", e, e["n"]):
            pl.hand.remove(c)
            self.trash_card(q, c)

    def op_discard_draw(self, q, e, ctx):
        pl = self.players[q]
        pick = self._pick_hand(q, "discard_draw", e, e["n"])
        for c in pick:
            pl.hand.remove(c)
            pl.discard.append(c)
        if pick:
            self.say(f"    {pl.name} сбрасывает: {', '.join(pick)}")
        self.draw(pl, len(pick))

    def gain_options(self, limit, typ):
        return [c for c, k in self.supply.items() if k > 0 and self.cost(c) <= limit and _is(c, typ)]

    def op_gain(self, q, e, ctx):
        opts = self.gain_options(e["max"], e.get("type"))
        name = self.ask(q, "gain", e, opts)
        if name is None:
            if opts:
                raise PlanError("нужно выбрать карту для получения")
            return
        if name not in opts:
            raise PlanError(f"нельзя получить {name!r}")
        self.gain(q, name, e.get("dest", "discard"))

    def op_remodel(self, q, e, ctx):
        pl = self.players[q]
        cand = [c for c in pl.hand if _is(c, e.get("ttype"))]
        t = self.ask(q, "remodel_trash", e, cand)
        g = None
        if t is None:
            if cand:
                raise PlanError("нужно выбрать карту для уничтожения")
        else:
            if t not in cand:
                raise PlanError(f"карты {t!r} нет в руке или она не подходит")
            limit = self.cost(t) + e["plus"]
            pl.hand.remove(t)
            self.trash_card(q, t)
            opts = self.gain_options(limit, e.get("gtype"))
            g = self.ask(q, "remodel_gain", e, opts)
            if g is None:
                if opts:
                    raise PlanError("нужно выбрать карту для получения")
            elif g not in opts:
                raise PlanError(f"нельзя получить {g!r}")
            else:
                self.gain(q, g, e.get("dest", "discard"))
            return
        g = self.ask(q, "remodel_gain", e, [])
        if g is not None:
            raise PlanError("нечего уничтожать — получать нельзя")

    def op_choose(self, q, e, ctx):
        k = self.ask(q, "choose", e, len(e["options"]))
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= len(e["options"]):
            raise PlanError(f"неверный вариант {k!r}")
        self.say(f"    выбран вариант {k}")
        self.resolve(q, e["options"][k - 1], ctx)

    def op_target_glory(self, q, e, ctx):
        opp = [o for o in range(self.n) if o != q]
        t = self.ask(q, "target", e, opp)
        if t not in opp or isinstance(t, bool):
            raise PlanError(f"неверная цель {t!r}")
        v = self.players[t]
        v.glory = max(0, v.glory - e["n"])
        self.say(f"    {v.name} теряет славу (теперь {v.glory})")
        if e.get("self_n"):
            self.players[q].glory += e["self_n"]
            self.say(f"    {self.nm(q)}: +{e['self_n']} славы")

    def op_double(self, q, e, ctx):
        pl = self.players[q]
        opts = [c for c in pl.hand if "Действие" in CARDS[c]["types"]
                and "Контракт" not in CARDS[c]["types"] and "Постройка" not in CARDS[c]["types"]]
        name = self.ask(q, "double", e, opts)
        if name is None:
            if opts:
                raise PlanError("нужно выбрать Действие для повтора")
            return
        if name not in opts:
            raise PlanError(f"нельзя повторить {name!r}")
        pl.hand.remove(name)
        self.uid += 1
        entry = {"name": name, "pending": False, "uid": self.uid}
        pl.table.append(entry)
        for _ in range(2):
            self.say(f"  {pl.name} разыгрывает «{name}» (повтор)")
            self.play_effects(q, name, entry)

    def op_return_curse(self, q, e, ctx):
        pl = self.players[q]
        pick = self._pick_hand(q, "return_curse", e, e["n"])
        for c in pick:
            if c != "Тина":
                raise PlanError("вернуть можно только Тину")
            pl.hand.remove(c)
            self.supply[c] = self.supply.get(c, 0) + 1
        if pick:
            self.say(f"    {pl.name} возвращает в запас Тин: {len(pick)}")

    def op_if_played(self, q, e, ctx):
        cnt = sum(1 for x in self.played[: ctx["idx"]] if _is(x, e.get("type")))
        if cnt >= e["k"]:
            self.resolve(q, e["then"], ctx)
        else:
            self.say("    условие не выполнено")

    def op_coins_per(self, q, e, ctx):
        pl = self.players[q]
        zone = pl.pier if e.get("where") == "pier" else pl.table
        cnt = sum(1 for x in zone if _is(x["name"], e.get("type")))
        self.add_coins(q, cnt // e["per"])

    def op_coins_per_empty(self, q, e, ctx):
        empty = sum(1 for c, v in self.supply.items()
                    if v == 0 and (e.get("scope") != "kingdom" or c not in BASE))
        self.add_coins(q, empty * e["each"])

    def op_dig(self, q, e, ctx):
        pl = self.players[q]
        aside = []
        while True:
            c = self.top(pl)
            if c is None:
                break
            if _is(c, e["type"]):
                pl.hand.append(c)
                self.say(f"    {pl.name} находит «{c}» и берёт её в руку")
                break
            aside.append(c)
            if "reveal_to_discard" in _V:
                pl.discard.append(c)
                if len(aside) > 300:
                    break
        if aside:
            self.say(f"    открытые и сброшенные: {', '.join(aside)}")
        if "reveal_to_discard" not in _V:
            pl.discard.extend(aside)

    def op_look_take(self, q, e, ctx):
        pl = self.players[q]
        aside = []
        for _ in range(e["n"]):
            c = self.top(pl)
            if c is None:
                break
            aside.append(c)
        took = [c for c in aside if _is(c, e["type"])]
        rest = [c for c in aside if not _is(c, e["type"])]
        pl.hand.extend(took)
        pl.discard.extend(rest)
        self.say(f"    {pl.name} открывает {len(aside)}: в руку {took}, в сброс {rest}")

    def op_draw_to(self, q, e, ctx):
        pl = self.players[q]
        while len(pl.hand) < e["n"]:
            if not self.draw(pl, 1):
                break

    def op_cost_cut(self, q, e, ctx):
        if q == self.active:
            self.cut += e["n"]
            self.say(f"    скидка в этот ход: {self.cut}")

    def op_buy_to_top(self, q, e, ctx):
        if q == self.active:
            self.to_top = True
            self.say("    покупки в этот ход идут на верх колоды")

    def op_trash_self_then(self, q, e, ctx):
        pl = self.players[q]
        entry = ctx.get("entry")
        if entry is not None and any(x is entry for x in pl.table):
            pl.table = [x for x in pl.table if x is not entry]
            self.trash_card(q, entry["name"])
            self.resolve(q, e["then"], ctx)
        else:
            self.say("    карта уже не на столе — ничего не происходит")

    def op_others_draw(self, q, e, ctx):
        for o in self.order():
            if o != q:
                self.draw(self.players[o], e["n"])

    def victims(self, q, ctx):
        return [o for o in self.order() if o != q and o not in ctx.get("prot", ())]

    def op_attack_discard_to(self, q, e, ctx):
        for o in self.victims(q, ctx):
            h = self.players[o].hand
            out = []
            while len(h) > e["n"]:
                if "victim_first" in _V:
                    i = min(range(len(h)), key=lambda i: (CARDS[h[i]]["cost"], i))
                else:
                    i = min(range(len(h)), key=lambda i: (CARDS[h[i]]["cost"], -i))
                out.append(h.pop(i))
            self.players[o].discard.extend(out)
            if out:
                self.say(f"    {self.nm(o)} сбрасывает: {', '.join(out)}")

    def op_attack_curse(self, q, e, ctx):
        for o in self.victims(q, ctx):
            self.gain(o, "Тина", "discard")

    def op_attack_glory(self, q, e, ctx):
        for o in self.victims(q, ctx):
            v = self.players[o]
            v.glory = max(0, v.glory - e["n"])
            self.say(f"    {v.name} теряет славу (теперь {v.glory})")

    def op_attack_reveal_trash(self, q, e, ctx):
        for o in self.victims(q, ctx):
            pl = self.players[o]
            c = self.top(pl)
            if c is None:
                continue
            if e["lo"] <= CARDS[c]["cost"] <= e["hi"]:
                self.trash_card(o, c)
            else:
                pl.discard.append(c)
                self.say(f"    {pl.name} открывает и сбрасывает «{c}»")

    def op_if_coins_left(self, q, e, ctx):
        if q == self.active and self.coins >= e["k"]:
            self.resolve(q, e["then"], ctx)

    def op_gain_named(self, q, e, ctx):
        self.gain(q, e["card"], e.get("dest", "discard"))

    # -- «Южный ветер» -------------------------------------------------------
    def op_topdeck_from_discard(self, q, e, ctx):
        pl = self.players[q]
        name = self.ask(q, "topdeck", e, list(pl.discard))
        if name is None:
            return
        if name not in pl.discard:
            raise PlanError(f"карты {name!r} нет в сбросе")
        if "discard_lowest" in _V:
            i = pl.discard.index(name)
        else:
            i = len(pl.discard) - 1 - pl.discard[::-1].index(name)
        pl.discard.pop(i)
        pl.deck.insert(0, name)
        self.say(f"    {pl.name} кладёт «{name}» из сброса на верх колоды")

    def op_spend_glory(self, q, e, ctx):
        pl = self.players[q]
        top = min(e["n"], pl.glory)
        k = self.ask(q, "spend_glory", e, top)
        if not isinstance(k, int) or isinstance(k, bool) or not 0 <= k <= top:
            raise PlanError(f"нельзя потратить {k!r} славы")
        pl.glory -= k
        self.say(f"    {pl.name} тратит {k} славы (осталось {pl.glory})")
        self.add_coins(q, k * e["each"])

    def op_discard_for_coins(self, q, e, ctx):
        pl = self.players[q]
        pick = self._pick_hand(q, "discard_coins", e, e["n"])
        for c in pick:
            pl.hand.remove(c)
            pl.discard.append(c)
        if pick:
            self.say(f"    {pl.name} сбрасывает ради монет: {', '.join(pick)}")
        self.add_coins(q, len(pick))

    def op_mill_count(self, q, e, ctx):
        pl = self.players[q]
        aside = []
        for _ in range(e["n"]):
            c = self.top(pl)
            if c is None:
                break
            aside.append(c)
            if "reveal_to_discard" in _V:
                pl.discard.append(c)
        if "reveal_to_discard" not in _V:
            pl.discard.extend(aside)
        self.say(f"    {pl.name} открывает и сбрасывает: {', '.join(aside) or '—'}")
        self.add_coins(q, sum(1 for c in aside if _is(c, e["type"])))

    def op_gain_copy_prev(self, q, e, ctx):
        idx = ctx.get("idx", 0)
        if idx < 1:
            self.say("    до этой карты в этом ходу ничего не разыграно")
            return
        prev = self.played[idx - 1]
        if self.cost(prev) > e["max"]:
            self.say(f"    «{prev}» слишком дорога для копии")
            return
        self.gain(q, prev, e.get("dest", "discard"))

    def op_attack_topdeck(self, q, e, ctx):
        for o in self.victims(q, ctx):
            pl = self.players[o]
            h = pl.hand
            if len(h) < e["k"]:
                continue
            i = max(range(len(h)), key=lambda i: (CARDS[h[i]]["cost"], -i))
            c = h.pop(i)
            pl.deck.insert(0, c)
            self.say(f"    {pl.name} кладёт «{c}» из руки на верх колоды")

    def op_attack_gain_top(self, q, e, ctx):
        for o in self.victims(q, ctx):
            self.gain(o, e["card"], "top")

    def op_draw_per_glory(self, q, e, ctx):
        pl = self.players[q]
        self.draw(pl, min(e["cap"], pl.glory // e["per"]))

    def op_coins_per_hand(self, q, e, ctx):
        self.add_coins(q, len(self.players[q].hand) // e["per"])

    # -- turn structure ------------------------------------------------------
    def play(self, p, name):
        pl = self.players[p]
        if name not in pl.hand:
            raise PlanError(f"карты {name!r} нет в руке")
        types = CARDS[name]["types"]
        if "Действие" in types:
            if self.actions < 1:
                raise PlanError("нет действий")
            self.actions -= 1
        elif "Товар" not in types:
            raise PlanError(f"карту {name!r} нельзя сыграть")
        pl.hand.remove(name)
        self.uid += 1
        entry = {"name": name, "pending": "Контракт" in types, "uid": self.uid,
                 "pos": len(self.played)}
        if "Постройка" in types:
            pl.pier.append(entry)
        else:
            pl.table.append(entry)
        self.say(f"  {pl.name} разыгрывает «{name}»")
        self.play_effects(p, name, entry)

    def play_effects(self, p, name, entry):
        self.played.append(name)
        idx = len(self.played) - 1
        self.mark(name)
        self.event("play", p, name, src=entry["uid"])
        prot = self.reactions(p) if "Интрига" in CARDS[name]["types"] else set()
        self.resolve(p, CARDS[name].get("play", []),
                     {"card": name, "entry": entry, "idx": idx, "prot": prot})

    def buy(self, p, name):
        if self.supply.get(name, 0) <= 0:
            raise PlanError(f"стопка {name!r} пуста или её нет")
        if self.buys < 1:
            raise PlanError("нет покупок")
        c = self.cost(name)
        if self.coins < c:
            raise PlanError(f"не хватает монет на {name!r}")
        self.buys -= 1
        self.coins -= c
        self._buying = True
        self.gain(p, name, "top" if self.to_top else "discard", bought=True)
        self._buying = False

    def start_turn(self, p):
        self.active = p
        self._reset_turn()
        self.actions = 1
        self.buys = 1
        pl = self.players[p]
        self.say(f"— Раунд {self.round}, ход: {pl.name}. Рука: {', '.join(pl.hand)}")
        for e in list(pl.table):
            if e["pending"]:
                e["pending"] = False
                self.say(f"  контракт «{e['name']}» исполняется")
                self.mark(e["name"] + "#next")
                self.resolve(p, CARDS[e["name"]]["next"], {"card": e["name"], "entry": e})
        for b in list(pl.pier):
            for trg in CARDS[b["name"]].get("triggers", []):
                if trg["on"] == "start":
                    self.say(f"  начало хода: постройка «{b['name']}»")
                    self.mark(b["name"] + "#start")
                    self.resolve(p, trg["effects"], {"card": b["name"], "entry": None})

    def cleanup(self, p):
        pl = self.players[p]
        keep = []
        from_table = []
        for e in pl.table:
            if e["pending"]:
                keep.append(e)
            else:
                from_table.append(e["name"])
        pl.table = keep
        if "cleanup_hand_first" in _V:
            pl.discard.extend(pl.hand)
            pl.discard.extend(from_table)
        else:
            pl.discard.extend(from_table)
            pl.discard.extend(pl.hand)
        pl.hand = []
        self.say(f"  очистка: {pl.name}")
        self._cleanup = True
        self.draw(pl, 5)
        self._cleanup = False

    def turn(self, p):
        pl = self.players[p]
        self.dec.start_turn(self, p)
        self.start_turn(p)
        while True:
            name = self.dec.next_play(self, p)
            if name is None:
                break
            self.play(p, name)
            self.dec.end_play(self, p)
        while True:
            name = self.dec.next_buy(self, p)
            if name is None:
                break
            self.buy(p, name)
        for b in list(pl.pier):
            for trg in CARDS[b["name"]].get("triggers", []):
                if trg["on"] == "end":
                    self.say(f"  конец хода: постройка «{b['name']}» (монет осталось {self.coins})")
                    self.mark(b["name"] + "#end")
                    self.resolve(p, trg["effects"], {"card": b["name"], "entry": None})
        self.cleanup(p)

    def check_end(self):
        if self.supply.get("Гавань", 1) <= 0:
            return "гавань"
        if sum(1 for v in self.supply.values() if v <= 0) >= 3:
            return "стопки"
        if self.round >= self.max_rounds:
            return "лимит"
        return None

    def vp(self, pl, name, owned):
        c = CARDS[name]
        total = c.get("vp", 0)
        rule = c.get("vp_rule")
        if rule:
            if rule["kind"] == "per_cards":
                total += rule["each"] * (len(owned) // rule["per"])
            elif rule["kind"] == "per_type":
                cnt = sum(1 for x in owned if rule["type"] in CARDS[x]["types"])
                total += rule["each"] * (cnt // rule["per"])
            elif rule["kind"] == "per_glory":
                total += rule["each"] * (pl.glory // rule["per"])
            elif rule["kind"] == "per_distinct":
                total += rule["each"] * (len(set(owned)) // rule["per"])
            elif rule["kind"] == "per_named":
                total += rule["each"] * (owned.count(rule["card"]) // rule["per"])
        return total

    def run(self):
        self._buying = False
        for pl in self.players:
            self.draw(pl, 5)
        end = None
        while end is None:
            self.round += 1
            for p in range(self.n):
                self.turn(p)
            end = self.check_end()
        if not self.dec.finished():
            raise PlanError("в плане остались лишние ходы")
        out_players = []
        for pl in self.players:
            owned = pl.owned()
            score = sum(self.vp(pl, c, owned) for c in owned) + pl.glory
            cards = {}
            for c in sorted(owned):
                cards[c] = cards.get(c, 0) + 1
            out_players.append({
                "name": pl.name, "score": score, "glory": pl.glory, "cards": cards,
                "hand": list(pl.hand), "coins_total": pl.coins_total, "draws": pl.draws,
                "shuffles": pl.shuffles,
            })
        best = max((x["score"], x["glory"]) for x in out_players)
        winners = [i for i, x in enumerate(out_players) if (x["score"], x["glory"]) == best]
        self.say(f"Конец игры: {end}, раундов {self.round}")
        return {
            "rounds": self.round,
            "end_reason": end,
            "winners": winners,
            "players": out_players,
            "supply": dict(self.supply),
            "trash": {k: v for k, v in sorted(self.trash.items()) if v},
        }


def simulate(game: dict) -> dict:
    return Game(game).run()


def simulate_with_trace(game: dict):
    g = Game(game, trace=True)
    result = g.run()
    return result, g.log
'''

# ---------------------------------------------------------------------------
# Card definitions (structured ground truth)
# ---------------------------------------------------------------------------

ACT, TREAS, VIC, ATT, REACT, DUR, BLD, CURSE = (
    "Действие", "Товар", "Владение", "Интрига", "Оберег", "Контракт", "Постройка", "Тина")


def _e(op, **kw):
    return {"op": op, **kw}


def D(n):  # noqa: N802
    return _e("draw", n=n)


def A(n):  # noqa: N802
    return _e("actions", n=n)


def B(n):  # noqa: N802
    return _e("buys", n=n)


def C(n):  # noqa: N802
    return _e("coins", n=n)


def G(n):  # noqa: N802
    return _e("glory", n=n)


def _card(name, types, cost, **kw):
    return {"name": name, "types": list(types), "cost": cost, **kw}


def _card_defs() -> list[dict]:
    """All cards (base, core set and the expansion) with their *effective* (post-errata) definitions."""
    e = _e
    base = [
        _card("Грош", [TREAS], 0, play=[C(1)]),
        _card("Гривна", [TREAS], 3, play=[C(2)]),
        _card("Червонец", [TREAS], 6, play=[C(3)]),
        _card("Причал", [VIC], 2, vp=1),
        _card("Склад", [VIC], 5, vp=3),
        _card("Гавань", [VIC], 8, vp=6),
        _card("Тина", [CURSE], 0, vp=-1),
    ]
    act = [ACT]
    kingdom = [
        # ---- plain actions
        _card("Юнга", act, 2, play=[D(1), A(1)]),
        _card("Рыбачья деревня", act, 3, play=[D(1), A(2)]),
        _card("Лоцман", act, 4, play=[D(2), A(1)]),
        _card("Грузчик", act, 4, play=[D(3)]),
        _card("Корабельный плотник", act, 5, play=[D(4), B(1), e("others_draw", n=1)]),
        _card("Меняла", act, 5, play=[C(2), B(1)]),
        _card("Приказчик", act, 3, play=[C(2)]),
        _card("Таможенник", act, 4, play=[e("trash_hand", n=2), C(1)]),
        _card("Старьёвщик", act, 2, play=[e("trash_hand", n=4)]),
        _card("Перекупщик", act, 4, play=[e("remodel", plus=2)]),
        _card("Корабел", act, 6, play=[e("remodel", plus=3), A(1)]),
        _card("Мастерская сетей", act, 3, play=[e("gain", max=4, dest="discard")]),
        _card("Верфь", act, 5, play=[e("gain", max=5, dest="top", type=ACT)]),
        _card("Скупщик", act, 4, play=[e("gain", max=3, dest="hand", type=TREAS), B(1)]),
        _card("Картограф", act, 3, play=[A(1), e("discard_draw", n=3)]),
        _card("Сборщик податей", act, 5, play=[A(1), e("coins_per", type=TREAS, per=2)]),
        _card("Боцман", act, 4, play=[A(1), e("choose", options=[[D(2)], [C(2)]])]),
        _card("Штурман", act, 5, play=[e("choose", options=[[D(3)], [A(1), B(1), C(1)]])]),
        _card("Зазывала", act, 3, play=[e("draw_to", n=6)]),
        _card("Смотритель мола", act, 5, play=[
            A(1), e("if_played", type=ACT, k=2, then=[C(2)])]),
        _card("Кок", act, 3, play=[A(1), C(1), G(1)]),
        _card("Причальный сторож", act, 4, play=[e("look_take", n=3, type=TREAS)]),
        _card("Водолаз", act, 4, play=[e("dig", type=TREAS), A(1)]),
        _card("Ловец жемчуга", act, 5, play=[e("dig", type=ACT), C(1)]),
        _card("Лавочник", act, 4, play=[e("cost_cut", n=1), B(1)]),
        _card("Купеческий приказ", act, 5, play=[B(1), e("cost_cut", n=1), C(1)]),
        _card("Фрахт", act, 4, play=[C(2), e("buy_to_top")]),
        _card("Лодочник", act, 3, play=[
            e("choose", options=[[e("trash_self_then", then=[C(4)])], [C(1)]])]),
        _card("Старая шхуна", act, 3, play=[
            C(2), e("choose", options=[[e("trash_self_then", then=[G(2)])], []])],
            on_trashed=[G(1)]),
        _card("Беглый матрос", act, 2, play=[
            D(1), A(1), e("if_played", type=ACT, k=1, then=[D(1)])]),
        _card("Трактирщица", act, 4, play=[D(2), e("others_draw", n=1), G(1)]),
        _card("Доносчик", act, 3, play=[C(1), e("target_glory", n=2, self_n=1)]),
        _card("Эхо прибоя", act, 5, play=[e("double")]),
        _card("Хранитель списков", act, 4, play=[e("return_curse", n=2), C(2)]),
        _card("Спасатель", act, 3, play=[e("return_curse", n=1), D(1), A(1)]),
        _card("Счетовод", act, 4, play=[D(1), A(1), e("coins_per_empty", each=1)]),
        _card("Капитан порта", act, 6, play=[D(2), A(1), B(1)]),
        _card("Ростовщик", act, 5, play=[B(1), e("trash_hand", n=1), C(2)]),
        _card("Летописец", act, 4, play=[D(1), A(1), G(1)]),
        # ---- attacks
        _card("Ведьма туманов", [ACT, ATT], 5, play=[D(2), e("attack_curse")]),
        _card("Портовая крыса", [ACT, ATT], 3, play=[C(1), e("attack_glory", n=1)]),
        _card("Шантажист", [ACT, ATT], 4, play=[C(2), e("attack_glory", n=2)]),
        _card("Пират", [ACT, ATT], 5, play=[C(2), e("attack_discard_to", n=3)]),
        _card("Абордажник", [ACT, ATT], 4, play=[D(1), A(1), e("attack_discard_to", n=4)]),
        _card("Мародёр", [ACT, ATT], 5, play=[C(1), e("attack_reveal_trash", lo=3, hi=6)]),
        _card("Контрабандист", [ACT, ATT], 4, play=[B(1), C(1), e("attack_curse")]),
        _card("Буревестник", [ACT, ATT], 6, play=[D(2), e("attack_glory", n=1), e("attack_curse")]),
        _card("Сирена", [ACT, ATT], 5, play=[D(2), e("attack_reveal_trash", lo=2, hi=5)]),
        _card("Туманный морок", [ACT, ATT], 3, play=[A(1), e("attack_discard_to", n=4), C(1)]),
        _card("Береговой вор", [ACT, ATT], 4, play=[C(2), e("attack_reveal_trash", lo=1, hi=4)]),
        _card("Штормовой колокол", [ACT, ATT], 5, play=[
            e("choose", options=[[C(2)], [D(2)]]), e("attack_glory", n=1)]),
        # ---- reactions
        _card("Янтарный амулет", [ACT, REACT], 2, play=[D(1), A(1)],
              react={"protect": True, "effects": []}),
        _card("Сторожевой пёс", [ACT, REACT], 3, play=[D(2)],
              react={"protect": True, "effects": []}),
        _card("Рыбацкий оберег", [ACT, REACT], 3, play=[A(1), C(1)],
              react={"protect": False, "effects": [D(1)]}),
        _card("Солёная подкова", [ACT, REACT], 4, play=[C(2)],
              react={"protect": True, "effects": [G(1)]}),
        _card("Штормовой фонарь", [ACT, REACT], 4, play=[D(1), A(1), G(1)],
              react={"protect": False, "effects": [D(2)]}),
        _card("Ладанка", [ACT, REACT], 2, play=[A(1), C(1)],
              react={"protect": True, "effects": [G(1)]}),
        # ---- durations
        _card("Утренний улов", [ACT, DUR], 3, play=[C(2)], next=[C(1), D(1)]),
        _card("Долгий рейс", [ACT, DUR], 5, play=[D(1)], next=[C(3)]),
        _card("Каботаж", [ACT, DUR], 4, play=[A(1), C(1)], next=[D(2)]),
        _card("Торговый договор", [ACT, DUR], 4, play=[B(1)], next=[B(1), C(2)]),
        _card("Зимовка", [ACT, DUR], 2, play=[G(1)], next=[A(1), D(1)]),
        _card("Лоцманская проводка", [ACT, DUR], 5, play=[D(2)], next=[D(2)]),
        _card("Страхование груза", [ACT, DUR], 4, play=[C(1)], next=[G(1), C(1)]),
        _card("Рыбный промысел", [ACT, DUR], 3, play=[C(1), A(1)], next=[C(1)]),
        _card("Аренда причала", [ACT, DUR], 5, play=[C(2)], next=[B(1), A(1)]),
        _card("Сезонный наём", [ACT, DUR], 6, play=[D(3)], next=[A(1), C(2)]),
        # ---- buildings
        _card("Старый маяк", [ACT, BLD], 5, play=[D(1)],
              triggers=[{"on": "start", "effects": [D(1)]}]),
        _card("Таверна «Якорь»", [ACT, BLD], 4, play=[A(1)],
              triggers=[{"on": "start", "effects": [C(1)]}]),
        _card("Рыбный рынок", [ACT, BLD], 5, play=[],
              triggers=[{"on": "buy", "filter": TREAS, "effects": [C(1)]}]),
        _card("Часовня на мысу", [ACT, BLD], 3, play=[A(1), e("trash_hand", n=1)],
              triggers=[{"on": "trash", "effects": [G(1)]}]),
        _card("Таможня", [ACT, BLD], 6, play=[D(1)],
              triggers=[{"on": "opp_curse", "effects": [G(1)]},
                        {"on": "gain", "filter": VIC, "effects": [G(1)]}]),
        _card("Сторожевая башня", [ACT, BLD], 4, play=[A(1)],
              triggers=[{"on": "opp_attack", "effects": [D(1)]}]),
        _card("Бондарня", [ACT, BLD], 4, play=[A(1)],
              triggers=[{"on": "play", "filter": TREAS, "first": True, "effects": [C(1)]}]),
        _card("Склад пряностей", [ACT, BLD], 5, play=[C(1)],
              triggers=[{"on": "end", "effects": [e("if_coins_left", k=2, then=[G(1)])]}]),
        _card("Гильдия лоцманов", [ACT, BLD], 6, play=[A(1)],
              triggers=[{"on": "play", "filter": ACT, "first": True, "effects": [A(1)]},
                        {"on": "play", "filter": ATT, "effects": [C(1)]}]),
        _card("Корабельный двор", [ACT, BLD], 5, play=[C(1)],
              triggers=[{"on": "gain", "filter": ACT, "effects": [G(1)]}]),
        _card("Биржа", [ACT, BLD], 7, play=[],
              triggers=[{"on": "start", "effects": [B(1), C(1)]}]),
        _card("Ратуша", [ACT, BLD], 6, play=[A(1)],
              triggers=[{"on": "buy", "filter": VIC, "effects": [G(2)]}]),
        # ---- treasures
        _card("Серебряный гребень", [TREAS], 4, play=[C(2), B(1)]),
        _card("Жемчужина", [TREAS], 5, play=[C(2), G(1)]),
        _card("Контрабандный ром", [TREAS], 3, play=[C(1), e("if_played", type=TREAS, k=2, then=[C(2)])]),
        _card("Векселя", [TREAS], 5, play=[e("coins_per", type=TREAS, per=2)]),
        _card("Бочонок сельди", [TREAS], 2, play=[C(1), D(1)], on_trashed=[D(1)]),
        _card("Янтарь", [TREAS], 4, play=[C(1), e("coins_per_empty", each=1)]),
        _card("Слиток меди", [TREAS], 6, play=[C(2)], on_buy=[G(1)]),
        _card("Расписка", [TREAS], 3, play=[C(2)], on_buy=[e("gain_named", card="Грош", dest="discard")]),
        # ---- victory
        _card("Пристанище", [VIC], 4, vp=2, on_gain=[G(1)]),
        _card("Дом смотрителя", [VIC], 5, vp_rule={"kind": "per_cards", "per": 8, "each": 1}),
        _card("Лабазы", [VIC], 6, vp_rule={"kind": "per_type", "type": BLD, "per": 1, "each": 2}),
        _card("Доходный дом", [VIC], 5, vp_rule={"kind": "per_type", "type": TREAS, "per": 3, "each": 1}),
        _card("Сад у маяка", [VIC], 3, vp=1, on_buy=[B(1)]),
        _card("Родовое гнездо", [VIC], 6, vp=3, on_gain=[G(2)]),
        _card("Почётная грамота", [VIC], 4, vp_rule={"kind": "per_glory", "per": 4, "each": 1}),
    ]
    # ---- expansion «Северный рейс»: the same mechanics, new combinations and wordings
    kingdom += [
        _card("Подмастерье", act, 3, play=[D(1), A(1), C(1)]),
        _card("Сторож лабаза", act, 4, play=[C(1), e("trash_hand", n=1), B(1)]),
        _card("Шкипер", act, 5, play=[D(2), A(2)]),
        _card("Юркий ялик", act, 3, play=[A(2), C(1)]),
        _card("Ярмарочный зазывала", act, 4, play=[e("draw_to", n=5), A(1)]),
        _card("Рыбная лавка", act, 3, play=[C(1), B(1), G(1)]),
        _card("Таможенный досмотр", act, 5, play=[e("trash_hand", n=3), C(2)]),
        _card("Точильщик", act, 4, play=[e("remodel", plus=1), C(1)]),
        _card("Лесопилка", act, 5, play=[e("remodel", plus=2), D(1)]),
        _card("Бакенщик", act, 3, play=[e("look_take", n=2, type=TREAS), A(1)]),
        _card("Рулевой", act, 4, play=[e("choose", options=[[A(2)], [D(1), C(1)]])]),
        _card("Карантинная служба", act, 4, play=[e("return_curse", n=3), G(1)]),
        _card("Сигнальщик", act, 3, play=[D(1), A(1), e("others_draw", n=1)]),
        _card("Портовый писарь", act, 4, play=[D(1), A(1), e("if_played", type=ACT, k=3, then=[C(3)])]),
        _card("Весовщик", act, 3, play=[A(1), e("coins_per", type=ACT, per=2)]),
        _card("Бухгалтер пристани", act, 5, play=[e("coins_per_empty", each=2), B(1)]),
        _card("Искатель кладов", act, 5, play=[e("dig", type=ACT), e("dig", type=TREAS)]),
        _card("Ныряльщик", act, 3, play=[e("dig", type=TREAS)]),
        _card("Рыбак-одиночка", act, 2, play=[e("choose", options=[[D(1)], [C(1)], [G(1)]])]),
        _card("Ярмарка", act, 5, play=[B(2), C(2)]),
        _card("Портовый кран", act, 6, play=[D(3), A(1)]),
        _card("Чайная", act, 2, play=[A(1), G(1)]),
        _card("Клерк", act, 2, play=[C(1), e("trash_hand", n=1)]),
        _card("Караван", act, 6, play=[e("gain", max=6, dest="discard"), B(1)]),
        _card("Кузница якорей", act, 4, play=[e("gain", max=4, dest="top", type=TREAS)]),
        _card("Смотровая вышка", act, 4, play=[e("look_take", n=4, type=TREAS)]),
        _card("Меняльная лавка", act, 4, play=[e("discard_draw", n=2), C(1)]),
        _card("Бродячий оркестр", act, 3, play=[A(2), e("discard_draw", n=1)]),
        _card("Судовой врач", act, 4, play=[e("return_curse", n=1), D(2)]),
        _card("Праздник урожая", act, 5, play=[e("cost_cut", n=2), B(1)]),
        _card("Торговый дом", act, 6, play=[C(3), B(1)]),
        _card("Причальная контора", act, 5, play=[e("buy_to_top"), B(2), C(1)]),
        _card("Тайный советник", act, 5, play=[e("target_glory", n=1, self_n=2), D(1)]),
        _card("Звонарь", act, 6, play=[e("double"), G(1)]),
        _card("Лоцманская лодка", act, 3, play=[
            e("choose", options=[[e("trash_self_then", then=[C(3), B(1)])], [D(1)]])]),
        _card("Ветхий баркас", act, 2, play=[
            C(1), e("choose", options=[[e("trash_self_then", then=[D(3)])], []])], on_trashed=[G(1)]),
        _card("Скупщик краденого", act, 5, play=[e("remodel", plus=3)]),
        _card("Пароходство", act, 6, play=[D(1), A(1), e("coins_per", type=ACT, per=1)]),
        _card("Счётная палата", act, 5, play=[A(1), e("if_played", type=ACT, k=2, then=[B(1), C(1)])]),
        _card("Трюмный", act, 2, play=[D(1), e("if_played", type=ACT, k=1, then=[A(1)])]),
        # attacks
        _card("Морской разбойник", [ACT, ATT], 5, play=[C(2), e("attack_reveal_trash", lo=4, hi=7)]),
        _card("Портовая сплетница", [ACT, ATT], 3, play=[D(1), e("attack_glory", n=1)]),
        _card("Злой рок", [ACT, ATT], 6, play=[C(1), e("attack_curse"), e("attack_glory", n=2)]),
        _card("Вымогатель", [ACT, ATT], 4, play=[B(1), e("attack_discard_to", n=3)]),
        _card("Штормовая ночь", [ACT, ATT], 5, play=[D(3), e("attack_discard_to", n=4)]),
        _card("Шпион", [ACT, ATT], 3, play=[D(1), A(1), e("attack_reveal_trash", lo=0, hi=2)]),
        _card("Колдунья с маяка", [ACT, ATT], 5, play=[C(2), e("attack_curse")]),
        _card("Абордажная команда", [ACT, ATT], 6, play=[
            e("choose", options=[[D(3)], [C(3)]]), e("attack_discard_to", n=3)]),
        # reactions
        _card("Счастливая монетка", [ACT, REACT], 2, play=[C(1), A(1)],
              react={"protect": False, "effects": [G(1), D(1)]}),
        _card("Спасательный круг", [ACT, REACT], 3, play=[D(1), A(1)],
              react={"protect": True, "effects": [D(1)]}),
        _card("Корабельная кошка", [ACT, REACT], 4, play=[C(1), B(1)],
              react={"protect": True, "effects": []}),
        _card("Талисман шкипера", [ACT, REACT], 3, play=[G(1), A(1)],
              react={"protect": False, "effects": [G(2)]}),
        # durations
        _card("Дальний промысел", [ACT, DUR], 4, play=[C(1)], next=[C(2), B(1)]),
        _card("Навигационная карта", [ACT, DUR], 3, play=[A(1)], next=[D(1), A(1)]),
        _card("Ссуда", [ACT, DUR], 5, play=[C(3)], next=[G(1)]),
        _card("Сезонная ярмарка", [ACT, DUR], 6, play=[B(1), C(2)], next=[B(1), C(2)]),
        _card("Вахта", [ACT, DUR], 4, play=[D(1), A(1)], next=[D(1), A(1)]),
        _card("Ночной лов", [ACT, DUR], 3, play=[G(1)], next=[C(2)]),
        _card("Экспедиция", [ACT, DUR], 6, play=[D(1)], next=[D(3)]),
        # buildings
        _card("Ремесленная слобода", [ACT, BLD], 5, play=[C(1)],
              triggers=[{"on": "gain", "filter": TREAS, "effects": [G(1)]}]),
        _card("Маячная служба", [ACT, BLD], 4, play=[D(1)],
              triggers=[{"on": "start", "effects": [A(1)]}]),
        _card("Сторожка", [ACT, BLD], 3, play=[A(1)],
              triggers=[{"on": "opp_attack", "effects": [G(1)]}]),
        _card("Кабак «Три пескаря»", [ACT, BLD], 4, play=[],
              triggers=[{"on": "play", "filter": ACT, "first": True, "effects": [C(1)]}]),
        _card("Судоремонтный док", [ACT, BLD], 6, play=[A(1)],
              triggers=[{"on": "trash", "effects": [C(1)]}]),
        _card("Меняльный двор", [ACT, BLD], 5, play=[],
              triggers=[{"on": "buy", "filter": ACT, "effects": [C(1)]}]),
        _card("Сторожевой пост", [ACT, BLD], 5, play=[C(1)],
              triggers=[{"on": "opp_curse", "effects": [D(1)]}]),
        _card("Контора купца", [ACT, BLD], 7, play=[],
              triggers=[{"on": "end", "effects": [e("if_coins_left", k=3, then=[G(2)])]}]),
        _card("Причал рыбаков", [ACT, BLD], 4, play=[A(1)],
              triggers=[{"on": "start", "effects": [G(1)]}]),
        # treasures
        _card("Серебряная чаша", [TREAS], 5, play=[C(2), e("if_played", type=TREAS, k=3, then=[G(1)])]),
        _card("Медный котёл", [TREAS], 3, play=[C(1), B(1)]),
        _card("Сундук боцмана", [TREAS], 6, play=[C(2), D(1)]),
        _card("Золотой якорь", [TREAS], 7, play=[C(4)]),
        _card("Кошель рыбака", [TREAS], 2, play=[C(1)], on_trashed=[G(1)]),
        _card("Моряцкий пай", [TREAS], 5, play=[C(1), e("coins_per", type=ACT, per=2)]),
        _card("Торговая грамота", [TREAS], 4, play=[C(2)], on_gain=[G(1)]),
        _card("Янтарные бусы", [TREAS], 4, play=[C(1), G(1)],
              on_buy=[e("gain_named", card="Гривна", dest="discard")]),
        # victory
        _card("Рыбацкая слобода", [VIC], 4, vp_rule={"kind": "per_type", "type": ACT, "per": 5, "each": 1}),
        _card("Купеческая усадьба", [VIC], 7, vp=4),
        _card("Смоляной двор", [VIC], 5, vp_rule={"kind": "per_type", "type": TREAS, "per": 4, "each": 1}),
        _card("Лоцманский остров", [VIC], 6, vp=3, on_buy=[C(2)]),
        _card("Сторожевая крепость", [VIC], 5, vp_rule={"kind": "per_glory", "per": 3, "each": 1}),
        _card("Хутор", [VIC], 3, vp=1, on_gain=[D(1)]),
    ]
    return base + kingdom + _south_defs()


def _south_defs() -> list[dict]:
    """Expansion «Южный ветер»: new mechanics (section 17 of the rules) and new combinations."""
    e = _e
    act = [ACT]
    return [
        # ---- new mechanics
        _card("Сборщица плавника", act, 3, play=[e("topdeck_from_discard"), C(1), A(1)]),
        _card("Память моряка", act, 2, play=[e("topdeck_from_discard"), D(1)]),
        _card("Ночной обходчик", act, 4, play=[D(2), e("topdeck_from_discard")]),
        _card("Ветеран флота", act, 5, play=[A(1), e("topdeck_from_discard"),
                                             e("draw_per_glory", per=3, cap=3)]),
        _card("Меценат", act, 4, play=[G(1), e("spend_glory", n=3, each=1)]),
        _card("Ростовщица", act, 5, play=[B(1), e("spend_glory", n=2, each=2)]),
        _card("Торг на пристани", act, 3, play=[e("discard_for_coins", n=3), B(1)]),
        _card("Распродажа", act, 2, play=[e("discard_for_coins", n=2), A(1)]),
        _card("Разгрузка баржи", act, 4, play=[e("mill_count", n=3, type=TREAS), A(1)]),
        _card("Опись трюма", act, 3, play=[D(1), e("mill_count", n=2, type=VIC)]),
        _card("Подражатель", act, 4, play=[A(1), e("gain_copy_prev", max=4)]),
        _card("Артель копиистов", act, 5, play=[e("gain_copy_prev", max=6), C(1)]),
        _card("Досмотрщик", [ACT, ATT], 4, play=[C(2), e("attack_topdeck", k=5)]),
        _card("Штормовой вал", [ACT, ATT], 5, play=[D(2), e("attack_topdeck", k=4)]),
        _card("Фальшивомонетчик", [ACT, ATT], 4, play=[C(1), e("attack_gain_top", card="Грош")]),
        _card("Портовый шулер", [ACT, ATT], 5, play=[
            D(1), A(1), e("attack_gain_top", card="Грош"), e("attack_glory", n=1)]),
        _card("Знаменосец", act, 4, play=[e("draw_per_glory", per=2, cap=3), A(1)]),
        _card("Герой гавани", act, 6, play=[G(1), e("draw_per_glory", per=3, cap=4), B(1)]),
        _card("Кассир", act, 3, play=[A(1), e("coins_per_hand", per=2)]),
        _card("Кошель менялы", [TREAS], 4, play=[e("coins_per_hand", per=3), C(1)]),
        _card("Мыловарня", [ACT, BLD], 5, play=[C(1)],
              triggers=[{"on": "shuffle", "effects": [G(1)]}]),
        _card("Солодовня", [ACT, BLD], 4, play=[A(1)],
              triggers=[{"on": "shuffle", "min": 8, "effects": [C(2)]}]),
        _card("Бюро находок", [ACT, BLD], 6, play=[D(1)],
              triggers=[{"on": "shuffle", "effects": [G(1), C(1)]}]),
        _card("Акцизная палата", act, 4, play=[e("coins_per_empty", each=2, scope="kingdom"), B(1)]),
        _card("Смотритель торгов", act, 5, play=[
            D(1), A(1), e("coins_per_empty", each=1, scope="kingdom")]),
        _card("Цеховой староста", act, 5, play=[A(1), e("coins_per", type=BLD, per=1, where="pier")]),
        _card("Монетный двор", act, 5, play=[
            e("remodel", plus=3, ttype=TREAS, gtype=TREAS, dest="hand")]),
        _card("Переплавка", act, 4, play=[
            e("remodel", plus=1, ttype=TREAS, gtype=TREAS, dest="hand"), C(1)]),
        _card("Серебряная пряжка", [TREAS], 4, play=[C(1), e("topdeck_from_discard")]),
        _card("Кошель с двойным дном", [TREAS], 3, play=[C(1), e("discard_for_coins", n=1)]),
        _card("Мелкая разменная", [TREAS], 1, play=[C(1), e("coins_per_hand", per=4)]),
        _card("Пристанский сквер", [VIC], 5, vp_rule={"kind": "per_distinct", "per": 5, "each": 1}),
        _card("Кладовая менял", [VIC], 4, vp_rule={"kind": "per_named", "card": "Грош", "per": 4,
                                                  "each": 1}),
        _card("Лодочная станция", [VIC], 3, vp=1,
              vp_rule={"kind": "per_named", "card": "Причал", "per": 3, "each": 1}),
        _card("Контракт с артелью", [ACT, DUR], 5, play=[A(1), e("mill_count", n=2, type=TREAS)],
              next=[e("coins_per_hand", per=2)]),
        # ---- old mechanics, new combinations
        _card("Сплавщик", act, 3, play=[D(2), e("trash_hand", n=1)]),
        _card("Бакалейщик", act, 4, play=[C(1), e("gain", max=3, dest="hand", type=TREAS)]),
        _card("Паромщик", act, 4, play=[D(1), A(1), e("choose", options=[[C(1)], [B(1)], [G(1)]])]),
        _card("Смолокур", act, 3, play=[C(2), e("others_draw", n=1)]),
        _card("Солевар", act, 4, play=[e("look_take", n=3, type=ACT), C(1)]),
        _card("Смотритель бакенов", act, 3, play=[e("dig", type=VIC), A(1), C(1)]),
        _card("Корабельный священник", act, 3, play=[e("return_curse", n=2), G(1), A(1)]),
        _card("Дозорный", act, 5, play=[A(2), e("if_played", type=TREAS, k=1, then=[D(2)])]),
        _card("Лодочник-перевозчик", act, 4, play=[e("choose", options=[
            [e("trash_self_then", then=[e("gain", max=5, dest="discard")])], [C(2)]])]),
        _card("Грузовой приказ", act, 6, play=[e("gain", max=5, dest="hand", type=ACT), A(1)]),
        _card("Шкатулка контрабандиста", act, 2, play=[e("discard_draw", n=2), G(1)]),
        _card("Береговой пират", [ACT, ATT], 6, play=[C(3), e("attack_discard_to", n=4)]),
        _card("Вербовщик", [ACT, ATT], 4, play=[D(2), e("attack_topdeck", k=5)]),
        _card("Чёрная метка", [ACT, ATT], 3, play=[A(1), e("attack_glory", n=2)]),
        _card("Волнорез", [ACT, REACT], 3, play=[D(1), C(1)],
              react={"protect": True, "effects": [C(1)]}),
        _card("Медальон вдовы", [ACT, REACT], 2, play=[G(1), A(1)],
              react={"protect": False, "effects": [G(1), D(1)]}),
        _card("Береговая охрана", [ACT, REACT], 5, play=[D(2), A(1)],
              react={"protect": True, "effects": [D(2)]}),
        _card("Штилевая ночь", [ACT, DUR], 3, play=[A(1)], next=[C(2), G(1)]),
        _card("Фрахт на юг", [ACT, DUR], 5, play=[C(2), B(1)], next=[D(1), C(1)]),
        _card("Ранний отлив", [ACT, DUR], 2, play=[D(1)], next=[A(1), C(1)]),
        _card("Зимний подряд", [ACT, DUR], 6, play=[D(2)], next=[D(2), B(1)]),
        _card("Отложенный платёж", [ACT, DUR], 4, play=[], next=[C(4)]),
        _card("Лодочная мастерская", [ACT, BLD], 4, play=[D(1)],
              triggers=[{"on": "gain", "filter": VIC, "effects": [C(1)]}]),
        _card("Караульня", [ACT, BLD], 3, play=[A(1)],
              triggers=[{"on": "opp_attack", "effects": [D(1), G(1)]}]),
        _card("Складской двор", [ACT, BLD], 5, play=[C(1)],
              triggers=[{"on": "trash", "filter": TREAS, "effects": [G(2)]}]),
        _card("Портовая часовня", [ACT, BLD], 6, play=[],
              triggers=[{"on": "start", "effects": [e("draw_per_glory", per=4, cap=2)]}]),
        _card("Гостиный двор", [ACT, BLD], 7, play=[B(1)],
              triggers=[{"on": "buy", "effects": [G(1)]}]),
        _card("Чеканный рубль", [TREAS], 5, play=[C(2), e("if_played", type=ACT, k=2, then=[C(1)])]),
        _card("Янтарная серьга", [TREAS], 6, play=[C(3)], on_trashed=[G(2)]),
        _card("Рыбачий посёлок", [VIC], 4, vp=2, on_buy=[G(1)]),
        _card("Старая верфь", [VIC], 6, vp_rule={"kind": "per_type", "type": DUR, "per": 2, "each": 1}),
        _card("Маяк на скале", [VIC], 7, vp=4, on_buy=[e("gain_named", card="Причал", dest="discard")]),
    ]


# Errata: (card, path, printed value) chains. Each entry lists the steps in date order;
# a step is (date, path, new_value) or (date, "cancel", index_of_step). The *printed*
# value is what the card says; the effective value (in _card_defs) is the result.
_ERRATA = {
    "Лоцман": [("12.03.2021", ("play", 0, "n"), 2)],  # printed 1
    "Меняла": [("02.07.2020", ("play", 0, "n"), 2)],  # printed 3
    "Таможенник": [("15.01.2022", ("play", 0, "n"), 2)],  # printed 3
    "Мастерская сетей": [("09.09.2021", ("play", 0, "max"), 4)],  # printed 3
    "Скупщик": [("20.11.2020", ("play", 0, "dest"), "hand")],  # printed discard
    "Смотритель мола": [("01.02.2022", ("play", 1, "k"), 2)],  # printed 1
    "Кок": [("18.05.2021", ("cost",), 3)],  # printed 2
    "Фрахт": [("03.03.2021", ("play", 0, "n"), 2)],  # printed 1
    "Пират": [("11.10.2021", ("play", 1, "n"), 3)],  # printed 4
    "Шантажист": [("14.04.2020", ("play", 1, "n"), 2)],  # printed 1
    "Каботаж": [("23.08.2021", ("next", 0, "n"), 2)],  # printed 1
    "Бондарня": [("30.06.2022", ("triggers", 0, "effects", 0, "n"), 1)],  # printed 2
    "Янтарь": [("07.12.2021", ("cost",), 4)],  # printed 3
    "Сирена": [("05.05.2022", ("play", 1, "hi"), 5)],  # printed 6
    "Контрабандный ром": [("16.02.2021", ("play", 1, "k"), 2)],  # printed 1
    "Биржа": [("27.10.2022", ("cost",), 7)],  # printed 6
    # superseded chains: printed → first errata → second errata (effective)
    "Грузчик": [("10.01.2020", ("play", 0, "n"), 4), ("19.08.2021", ("play", 0, "n"), 3)],
    "Зазывала": [("22.03.2020", ("play", 0, "n"), 7), ("01.12.2020", ("play", 0, "n"), 6)],
    # cancelled errata: effective value is the printed one
    "Юнга": [("04.04.2021", ("play", 0, "n"), 2), ("25.06.2021", "cancel", 0)],
    "Сборщик податей": [("12.12.2020", ("play", 1, "per"), 3), ("09.02.2021", "cancel", 0)],
    "Ведьма туманов": [("30.01.2022", ("play", 0, "n"), 1), ("17.03.2022", "cancel", 0)],
    "Часовня на мысу": [("08.08.2020", ("cost",), 4), ("02.09.2020", "cancel", 0)],
    "Долгий рейс": [("14.02.2022", ("next", 0, "n"), 2), ("01.04.2022", "cancel", 0)],
    # expansion «Северный рейс»
    "Шкипер": [("11.05.2023", ("play", 1, "n"), 2)],  # printed 1
    "Таможенный досмотр": [("02.06.2023", ("play", 0, "n"), 3)],  # printed 2
    "Ярмарочный зазывала": [("19.07.2023", ("play", 0, "n"), 5)],  # printed 6
    "Караван": [("23.03.2023", ("play", 0, "max"), 6)],  # printed 5
    "Морской разбойник": [("14.09.2023", ("play", 1, "lo"), 4)],  # printed 3
    "Дальний промысел": [("08.08.2023", ("next", 0, "n"), 2)],  # printed 1
    "Золотой якорь": [("01.11.2023", ("cost",), 7)],  # printed 8
    "Контора купца": [("12.12.2023", ("triggers", 0, "effects", 0, "k"), 3)],  # printed 2
    "Лесопилка": [("26.04.2023", ("play", 0, "plus"), 2)],  # printed 1
    "Смотровая вышка": [("15.10.2023", ("play", 0, "n"), 4)],  # printed 3
    "Кузница якорей": [("04.03.2023", ("play", 0, "dest"), "top")],  # printed hand
    "Ремесленная слобода": [("28.02.2023", ("triggers", 0, "effects", 0, "n"), 1)],  # printed 2
    "Вымогатель": [("05.02.2023", ("play", 1, "n"), 4), ("30.10.2023", ("play", 1, "n"), 3)],  # printed 5
    "Экспедиция": [("06.01.2023", ("next", 0, "n"), 4), ("18.08.2023", ("next", 0, "n"), 3)],  # printed 2
    "Серебряная чаша": [("03.04.2023", ("play", 1, "k"), 2), ("21.05.2023", "cancel", 0)],  # printed 3
    "Портовый писарь": [("17.06.2023", ("play", 2, "k"), 2), ("09.09.2023", "cancel", 0)],  # printed 3
    "Счастливая монетка": [("27.07.2023", ("cost",), 3), ("02.10.2023", "cancel", 0)],  # printed 2
    # expansion «Южный ветер»: also errata to reactions, trigger conditions and victory points
    "Сборщица плавника": [("11.03.2024", ("play", 1, "n"), 1)],  # printed 2
    "Меценат": [("02.04.2024", ("play", 1, "n"), 3)],  # printed 2
    "Ростовщица": [("14.02.2024", ("play", 1, "each"), 3), ("03.06.2024", ("play", 1, "each"), 2)],
    "Торг на пристани": [("21.05.2024", ("play", 0, "n"), 3)],  # printed 2
    "Разгрузка баржи": [("09.07.2024", ("play", 0, "type"), TREAS)],  # printed VIC
    "Подражатель": [("17.01.2024", ("play", 1, "max"), 5), ("28.02.2024", "cancel", 0)],
    "Досмотрщик": [("05.08.2024", ("play", 1, "k"), 5)],  # printed 4
    "Фальшивомонетчик": [("30.09.2024", ("cost",), 4)],  # printed 3
    "Знаменосец": [("12.10.2024", ("play", 0, "cap"), 3)],  # printed 2
    "Кассир": [("04.02.2024", ("play", 1, "per"), 2), ("16.06.2024", ("play", 1, "per"), 4),
               ("01.09.2024", "cancel", 1)],  # printed 3; the second errata is cancelled -> 2
    "Солодовня": [("22.04.2024", ("triggers", 0, "min"), 8)],  # printed 10
    "Складской двор": [("13.11.2024", ("triggers", 0, "filter"), TREAS)],  # printed: any card
    "Цеховой староста": [("08.05.2024", ("cost",), 5)],  # printed 6
    "Монетный двор": [("19.03.2024", ("play", 0, "plus"), 3)],  # printed 2
    "Кладовая менял": [("25.07.2024", ("vp_rule", "per"), 4)],  # printed 5
    "Пристанский сквер": [("06.12.2024", ("vp_rule", "per"), 5)],  # printed 4
    "Волнорез": [("14.03.2024", ("react", "protect"), True)],  # printed: no protection
    "Береговая охрана": [("27.05.2024", ("react", "protect"), False), ("15.08.2024", "cancel", 0)],
    "Маяк на скале": [("03.10.2024", ("vp",), 4)],  # printed 5
    "Лодочная станция": [("18.01.2025", ("vp",), 1)],  # printed 2
    "Штилевая ночь": [("29.04.2024", ("next", 0, "n"), 2)],  # printed 1
    "Зимний подряд": [("10.06.2024", ("next", 1, "n"), 1), ("24.01.2025", ("next", 1, "n"), 1)],
    "Грузовой приказ": [("07.11.2024", ("play", 0, "type"), ACT)],  # printed: any card
    "Гостиный двор": [("20.02.2025", ("triggers", 0, "effects", 0, "n"), 1)],  # printed 2
    "Чёрная метка": [("11.09.2024", ("play", 1, "n"), 3), ("02.12.2024", ("play", 1, "n"), 2)],
    "Солевар": [("16.10.2024", ("play", 0, "n"), 3)],  # printed 4
    "Контракт с артелью": [("05.03.2025", ("next", 0, "per"), 2)],  # printed 3
}
_PRINTED = {
    "Лоцман": 1, "Меняла": 3, "Таможенник": 3, "Мастерская сетей": 3, "Скупщик": "discard",
    "Смотритель мола": 1, "Кок": 2, "Фрахт": 1, "Пират": 4, "Шантажист": 1, "Каботаж": 1,
    "Бондарня": 2, "Янтарь": 3, "Сирена": 6, "Контрабандный ром": 1, "Биржа": 6, "Грузчик": 2,
    "Зазывала": 5, "Юнга": 1, "Сборщик податей": 2, "Ведьма туманов": 2, "Часовня на мысу": 3,
    "Долгий рейс": 3,
    "Шкипер": 1, "Таможенный досмотр": 2, "Ярмарочный зазывала": 6, "Караван": 5, "Морской разбойник": 3,
    "Дальний промысел": 1, "Золотой якорь": 8, "Контора купца": 2, "Лесопилка": 1, "Смотровая вышка": 3,
    "Кузница якорей": "hand", "Ремесленная слобода": 2, "Вымогатель": 5, "Экспедиция": 2,
    "Серебряная чаша": 3, "Портовый писарь": 3, "Счастливая монетка": 2,
    "Сборщица плавника": 2, "Меценат": 2, "Ростовщица": 1, "Торг на пристани": 2,
    "Разгрузка баржи": VIC, "Подражатель": 4, "Досмотрщик": 4, "Фальшивомонетчик": 3,
    "Знаменосец": 2, "Кассир": 3, "Солодовня": 10, "Складской двор": None, "Цеховой староста": 6,
    "Монетный двор": 2, "Кладовая менял": 5, "Пристанский сквер": 4, "Волнорез": False,
    "Береговая охрана": True, "Маяк на скале": 5, "Лодочная станция": 2, "Штилевая ночь": 1,
    "Зимний подряд": 2, "Грузовой приказ": None, "Гостиный двор": 2, "Чёрная метка": 1,
    "Солевар": 4, "Контракт с артелью": 3,
}


def _get(obj, path):
    for k in path:
        obj = obj[k]
    return obj


def _set(obj, path, value):
    for k in path[:-1]:
        obj = obj[k]
    obj[path[-1]] = value


def _printed_card(card: dict) -> dict:
    """The card as printed (before errata)."""
    steps = _ERRATA.get(card["name"])
    out = copy.deepcopy(card)
    if steps:
        _set(out, steps[0][1], _PRINTED[card["name"]])
    return out


def _effective_check() -> None:
    """Replaying errata steps over the printed card must give the effective card."""
    for card in _card_defs():
        steps = _ERRATA.get(card["name"])
        if not steps:
            continue
        cur = _printed_card(card)
        history = [_get(cur, steps[0][1])]
        for _date, path, val in steps:
            if path == "cancel":
                history.pop()
                _set(cur, steps[0][1], history[-1])
            else:
                history.append(val)
                _set(cur, path, val)
        assert cur == card, card["name"]


@functools.cache
def _cards_by_name() -> dict[str, dict]:
    return {c["name"]: c for c in _card_defs()}


def _engine_source(variant: tuple[str, ...] = (), printed: bool = False) -> str:
    defs = [_printed_card(c) if printed else c for c in _card_defs()]
    table = {c["name"]: {k: v for k, v in c.items() if k != "name"} for c in defs}
    blob = json.dumps(table, ensure_ascii=False, sort_keys=True)
    return (_ENGINE_SRC.replace("__CARDS__", repr(blob))
            .replace("__VARIANT__", repr(list(variant))))


@functools.cache
def _engine_ns(variant: tuple[str, ...] = ()) -> dict:
    ns: dict = {"__name__": "hb_card_engine"}
    exec(compile(_engine_source(variant), "<card-engine>", "exec"), ns)  # noqa: S102
    return ns


# ---------------------------------------------------------------------------
# Game generation: a random legal policy plays the reference engine and every
# decision is recorded into the plan, so replaying the plan is exact.
# ---------------------------------------------------------------------------

_BASE = ("Грош", "Гривна", "Червонец", "Причал", "Склад", "Гавань", "Тина")
_NAMES = ("Вера", "Остап", "Марфа", "Тихон", "Злата", "Игнат", "Нина", "Савва", "Ульяна",
          "Фома", "Дарья", "Кузьма", "Лукерья", "Прохор", "Василиса", "Ерофей")
_CURSERS = ("Ведьма туманов", "Контрабандист", "Буревестник", "Злой рок", "Колдунья с маяка")
_NEEDS = {
    "Таможня": _CURSERS,
    "Хранитель списков": _CURSERS,
    "Спасатель": _CURSERS,
    "Бочонок сельди": ("Таможенник", "Старьёвщик", "Перекупщик", "Часовня на мысу", "Сирена"),
    "Эхо прибоя": ("Лоцман", "Юнга", "Боцман", "Пират", "Приказчик", "Кок", "Грузчик"),
    "Звонарь": ("Подмастерье", "Шкипер", "Рулевой", "Ярмарка", "Чайная", "Грузчик"),
    "Карантинная служба": _CURSERS,
    "Судовой врач": _CURSERS,
    "Сторожевой пост": _CURSERS,
    "Кошель рыбака": ("Таможенник", "Старьёвщик", "Перекупщик", "Точильщик", "Таможенный досмотр", "Клерк"),
    "Корабельный священник": _CURSERS,
    "Янтарная серьга": ("Монетный двор", "Переплавка", "Таможенник", "Старьёвщик", "Клерк", "Сплавщик"),
    "Складской двор": ("Монетный двор", "Переплавка", "Таможенный досмотр", "Старьёвщик", "Сплавщик"),
}


def _types(name):
    return _cards_by_name()[name]["types"]


def _gives_actions(name: str) -> bool:
    def walk(effs):
        for x in effs:
            if x["op"] == "actions":
                return True
            if x["op"] == "choose" and any(walk(o) for o in x["options"]):
                return True
        return False
    return walk(_cards_by_name()[name].get("play", []))


class _AI:
    """Random but legal decisions; records the plan in the replay format."""

    def __init__(self, r: random.Random, kingdom: list[str]):
        self.r = r
        self.kingdom = set(kingdom)
        self.turns: list[dict] = []
        self.cur: dict = {}
        self.entry: dict = {}

    def start_turn(self, g, p):
        self.cur = {"plays": [], "buys": []}
        self.turns.append(self.cur)

    def next_play(self, g, p):
        r = self.r
        hand = g.players[p].hand
        acts = [c for c in hand if ACT in _types(c)] if g.actions >= 1 else []
        treas = [c for c in hand if TREAS in _types(c)]
        name = None
        if acts and r.random() < 0.95:
            givers = [c for c in acts if _gives_actions(c)]
            pool = givers if givers and g.actions == 1 and len(acts) > 1 and r.random() < 0.8 else acts
            name = r.choice(pool)
        elif treas and r.random() > 0.02:
            name = r.choice(treas)
        if name is None:
            return None
        self.entry = {"card": name, "choices": []}
        self.cur["plays"].append(self.entry)
        return name

    def end_play(self, g, p):
        if not self.entry["choices"]:
            del self.entry["choices"]

    def _weight(self, c, g):
        w = 6.0 if c in self.kingdom else 1.0
        if c == "Гавань":
            w = 9.0 if g.round >= 4 else 0.5
        elif c in ("Склад", "Червонец"):
            w = 2.5
        elif c == "Грош":
            w = 0.15
        elif c == "Тина":
            w = 0.01
        return w * (1 + g.cost(c)) ** 1.7

    def _pick(self, opts, g):
        if not opts:
            return None
        return self.r.choices(opts, weights=[self._weight(c, g) for c in opts])[0]

    def choice(self, g, p, kind, eff, info):
        r = self.r
        hand = g.players[p].hand
        junk = [c for c in hand if c in ("Тина", "Грош", "Причал")]
        if kind == "trash_hand":
            pool = junk if junk and r.random() < 0.85 else list(hand)
            k = r.randint(0, min(eff["n"], len(pool)))
            val = r.sample(pool, k)
        elif kind == "discard_draw":
            k = r.randint(0, min(eff["n"], len(hand)))
            val = r.sample(hand, k)
        elif kind == "return_curse":
            cur = [c for c in hand if c == "Тина"]
            val = cur[: r.randint(0, min(eff["n"], len(cur)))] if cur else []
        elif kind in ("gain", "remodel_gain"):
            val = self._pick(info, g)
        elif kind == "remodel_trash":
            cheap = [c for c in info if c in junk]
            val = (r.choice(cheap) if cheap and r.random() < 0.7 else r.choice(info)) if info else None
        elif kind == "topdeck":
            dup = [c for c in info if info.count(c) > 1]
            pool = dup if dup and r.random() < 0.8 else info
            val = r.choice(pool) if pool and r.random() < 0.9 else None
        elif kind == "spend_glory":
            val = r.randint(0, info)
        elif kind == "discard_coins":
            pool = junk if junk and r.random() < 0.7 else list(hand)
            val = r.sample(pool, r.randint(0, min(eff["n"], len(pool))))
        elif kind == "choose":
            val = r.randint(1, info)
        elif kind == "target":
            val = r.choice(info)
        elif kind == "double":
            val = r.choice(info) if info else None
        else:  # pragma: no cover
            raise AssertionError(kind)
        self.entry["choices"].append(val)
        return val

    def next_buy(self, g, p):
        r = self.r
        if g.buys < 1:
            return None
        opts = [c for c, k in g.supply.items() if k > 0 and g.cost(c) <= g.coins and c != "Тина"]
        if "Грош" in opts and r.random() > 0.1:
            opts.remove("Грош")
        if not opts or r.random() < 0.05 or (g.coins < 3 and r.random() < 0.6):
            return None
        name = self._pick(opts, g)
        self.cur["buys"].append(name)
        return name

    def finished(self):
        return True


def _make_game(r: random.Random, kingdom: list[str], nplayers: int, focus=()) -> dict:
    cards = _cards_by_name()
    big = nplayers > 2
    supply = {"Грош": 40, "Гривна": 30, "Червонец": 20, "Причал": 12 if big else 8,
              "Склад": 12 if big else 8, "Гавань": r.choice([2, 3, 8, 12] if big else [2, 3, 8]),
              "Тина": 10 * (nplayers - 1)}
    small_piles = r.random() < 0.3
    for c in kingdom:
        if VIC in cards[c]["types"]:
            supply[c] = 12 if big else 8
        else:
            supply[c] = r.choice([4, 5, 6]) if small_piles else 10
    names = r.sample(_NAMES, nplayers)
    players = []
    for nm in names:
        deck = ["Грош"] * 7 + ["Причал"] * 3
        if r.random() < 0.5:
            deck[1] = "Гривна"
        for k, c in enumerate(focus):
            deck[2 + k] = c
        if r.random() < 0.3:
            cheap = [c for c in kingdom if cards[c]["cost"] <= 3 and VIC not in cards[c]["types"]]
            if cheap:
                deck[0] = r.choice(cheap)
        r.shuffle(deck)
        players.append({"name": nm, "deck": deck})
    return {"players": players, "supply": supply, "seed": r.randrange(1, 2 ** 31 - 1),
            "max_rounds": r.randint(12, 18), "turns": []}


def _play_game(r: random.Random, kingdom: list[str], nplayers: int, focus=()) -> dict:
    ns = _engine_ns()
    game = _make_game(r, kingdom, nplayers, focus)
    ai = _AI(r, kingdom)
    ns["Game"](copy.deepcopy(game), decider=ai).run()
    game["turns"] = ai.turns
    return game


def _choose_kingdom(r: random.Random, usage: Counter, pool: list[str]) -> list[str]:
    order = sorted(pool, key=lambda c: (usage[c], r.random()))
    king = order[:10]
    for c in list(king):
        need = _NEEDS.get(c)
        if need and not any(x in king for x in need):
            repl = [x for x in king if x not in _NEEDS and x != c
                    and not any(x in v for v in _NEEDS.values() if v)]
            king.remove(r.choice(repl) if repl else king[-1])
            king.append(r.choice(need))
    if any(REACT in _types(c) for c in king) and not any(ATT in _types(c) for c in king):
        atts = [c for c in pool if ATT in _types(c)]
        repl = [x for x in king if REACT not in _types(x) and x not in _NEEDS]
        king.remove(r.choice(repl))
        king.append(min(atts, key=lambda c: (usage[c], r.random())))
    for c in king:
        usage[c] += 1
    return sorted(set(king))


def _targeted_game(r: random.Random, usage: Counter, pool: list[str], card: str, nplayers: int) -> dict:
    """A game whose kingdom contains `card`; replayed with fresh decisions until the card is used."""
    keys = _coverage_keys()[card]
    game = None
    for _ in range(60):
        king = [c for c in _choose_kingdom(r, usage, pool) if c != card]
        need = _NEEDS.get(card)
        if need and not any(x in king for x in need):
            king[0] = r.choice(need)
        if REACT in _types(card) and not any(ATT in _types(c) for c in king):
            king[1] = next(c for c in pool if ATT in _types(c) and c not in king)
        king = sorted(set(king[:9] + [card]))
        playable = ACT in _types(card) or TREAS in _types(card)
        game = _play_game(r, king, nplayers, [card] if playable else ())
        cov = _coverage([game])
        if min(cov[k] for k in keys) >= 1:
            return game
    return game


@functools.cache
def _build() -> dict:
    r = rng(TASK_ID)
    pool = [c["name"] for c in _card_defs() if c["name"] not in _BASE]
    ns = _engine_ns()
    usage: Counter = Counter()
    hidden = []
    keys = _coverage_keys()
    seen: Counter = Counter()
    for i in range(N_HIDDEN):
        nplayers = (2, 3, 4)[i % 3] if i % 5 else r.choice((2, 3, 4))
        missing = [c for c in pool if min(seen[k] for k in keys[c]) < 1] if i >= N_HIDDEN - 20 else []
        if missing:  # the last games are aimed at cards nobody has played yet
            game = _targeted_game(r, usage, pool, missing[0], nplayers)
        else:
            king = _choose_kingdom(r, usage, pool)
            low = sorted(king, key=lambda c: (min(seen[k] for k in keys[c]), r.random()))
            focus = [c for c in low[:2] if min(seen[k] for k in keys[c]) < 3]
            game = _play_game(r, king, nplayers, focus)
        seen.update(_coverage([game]))
        hidden.append(game)
    uncovered = [c for c in pool if min(seen[k] for k in keys[c]) < 1]
    assert not uncovered, f"cards never played in hidden games: {uncovered}"
    _effective_check()
    examples = []
    ex_usage: Counter = Counter()
    ex_pool = pool[:]
    r.shuffle(ex_pool)
    ex_pool = ex_pool[:90]
    for i in range(N_EXAMPLES):
        king = _choose_kingdom(r, ex_usage, ex_pool)
        game = _play_game(r, king, (2, 3, 4, 2)[i % 4])
        examples.append(game)
    hidden_out = [ns["simulate"](copy.deepcopy(g)) for g in hidden]
    ex_out = []
    for g in examples:
        res, log = ns["simulate_with_trace"](copy.deepcopy(g))
        ex_out.append((res, log))
    return {"hidden": hidden, "hidden_out": hidden_out, "examples": examples, "examples_out": ex_out}


def _coverage_keys() -> dict[str, list[str]]:
    """Coverage keys per card: its play, each trigger kind, own on-event texts, reaction.

    Victory cards without texts are covered by being owned at the end (key ``name#own``).
    """
    out = {}
    for c in _card_defs():
        n = c["name"]
        keys = []
        if ACT in c["types"] or TREAS in c["types"]:
            keys.append(n)
        keys += [n + "#" + t["on"] for t in c.get("triggers", [])]
        keys += [n + "#" + k for k in ("on_buy", "on_gain", "on_trashed") if c.get(k)]
        if c.get("react"):
            keys.append(n + "#react")
        if c.get("next"):
            keys.append(n + "#next")
        if not keys:
            keys.append(n + "#own")
        out[n] = keys
    return out


def _coverage(games: list[dict]) -> Counter:
    ns = _engine_ns()
    total: Counter = Counter()
    for g in games:
        eng = ns["Game"](copy.deepcopy(g))
        res = eng.run()
        total.update(eng.stats)
        for pl in res["players"]:
            total.update({c + "#own": 1 for c in pl["cards"]})
    return total


# ---------------------------------------------------------------------------
# Rendering card texts in free Russian wording (several paraphrases per effect)
# ---------------------------------------------------------------------------

_FEM = {1: "одну", 2: "две", 3: "три", 4: "четыре", 5: "пять", 6: "шесть", 7: "семь"}
_NEU = {1: "одно", 2: "два", 3: "три", 4: "четыре", 5: "пять", 6: "шесть", 7: "семь"}
_MAS = {1: "один", 2: "два", 3: "три", 4: "четыре", 5: "пять", 6: "шесть", 7: "семь"}
_GEN = {1: "одной", 2: "двух", 3: "трёх", 4: "четырёх", 5: "пяти", 6: "шести", 7: "семи"}
_TYPE_ACC = {None: "карту", TREAS: "Товар", ACT: "карту Действия", VIC: "Владение", ATT: "Интригу"}
_TYPE_PL = {TREAS: ("Товар", "Товара", "Товаров"), ACT: ("Действие", "Действия", "Действий"),
            ATT: ("Интригу", "Интриги", "Интриг"), VIC: ("Владение", "Владения", "Владений"),
            BLD: ("Постройку", "Постройки", "Построек"), DUR: ("Контракт", "Контракта", "Контрактов")}
_EACH = {TREAS: "каждый Товар", ACT: "каждую карту Действия", VIC: "каждое Владение",
         BLD: "каждую Постройку"}


def _pl(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _cards(n, r, word=True):
    num = _FEM[n] if word and r.random() < 0.5 else str(n)
    return f"{num} {_pl(n, 'карту', 'карты', 'карт')}"


def _upto(n):
    return f"{_GEN[n]} {_pl(n, 'карты', 'карт', 'карт')}"


def _types_pl(t, n):
    return _pl(n, *_TYPE_PL[t])


def _r_draw(e, r):
    n = e["n"]
    return r.choice([
        f"+{n} {_pl(n, 'карта', 'карты', 'карт')}",
        f"возьмите {_cards(n, r)}",
        f"доберите из своей колоды ещё {_cards(n, r)}",
        f"вытяните с верха своей колоды {_cards(n, r)}",
        f"возьмите в руку {_cards(n, r)} из своей колоды",
    ])


def _r_actions(e, r):
    n = e["n"]
    return r.choice([
        f"+{n} {_pl(n, 'действие', 'действия', 'действий')}",
        f"получите ещё {_NEU[n]} {_pl(n, 'действие', 'действия', 'действий')}",
        f"в этот ход вы можете сыграть на {_NEU[n]} {_pl(n, 'Действие', 'Действия', 'Действий')} больше",
        f"прибавьте {n} к счётчику действий",
        f"у вас появляется {_NEU[n]} дополнительн{'ое' if n == 1 else 'ых'} "
        f"{_pl(n, 'действие', 'действия', 'действий')}",
    ])


def _r_buys(e, r):
    n = e["n"]
    return r.choice([
        f"+{n} {_pl(n, 'покупка', 'покупки', 'покупок')}",
        f"получите ещё {_FEM[n]} {_pl(n, 'покупку', 'покупки', 'покупок')}",
        f"в фазе покупок вы сможете купить на {_FEM[n]} {_pl(n, 'карту', 'карты', 'карт')} больше",
        f"прибавьте {n} к счётчику покупок",
    ])


def _r_coins(e, r):
    n = e["n"]
    return r.choice([
        f"+{n} {_pl(n, 'монета', 'монеты', 'монет')}",
        f"получите {_FEM[n]} {_pl(n, 'монету', 'монеты', 'монет')}",
        f"добавьте в кошелёк хода {n} {_pl(n, 'монету', 'монеты', 'монет')}",
        f"в этот ход у вас на {_FEM[n]} {_pl(n, 'монету', 'монеты', 'монет')} больше",
        f"ваш кошелёк пополняется на {n} {_pl(n, 'монету', 'монеты', 'монет')}",
    ])


def _r_glory(e, r):
    n = e["n"]
    return r.choice([
        f"+{n} славы",
        f"получите {_NEU[n]} {_pl(n, 'очко', 'очка', 'очков')} славы",
        f"положите перед собой {_MAS[n]} {_pl(n, 'жетон', 'жетона', 'жетонов')} славы",
        f"ваша слава растёт на {n}",
        f"прибавьте себе {n} к славе",
    ])


def _r_trash_hand(e, r):
    n = e["n"]
    return r.choice([
        f"уничтожьте до {_upto(n)} из руки",
        f"вы можете уничтожить из руки не больше {_upto(n)}",
        f"отправьте на свалку от нуля до {_upto(n)} из своей руки",
        f"выберите в руке не более {_upto(n)} и уничтожьте {'её' if n == 1 else 'их'}",
        f"избавьтесь от любого числа карт из руки, но не более чем от {_upto(n)}: они уничтожаются",
    ])


def _r_discard_draw(e, r):
    n = e["n"]
    return r.choice([
        f"сбросьте из руки до {_upto(n)}, затем возьмите столько карт, сколько сбросили",
        f"можете сбросить не больше {_upto(n)} из руки; сбросив, возьмите такое же число карт",
        f"обменяйте до {_upto(n)} из руки: сначала сбросьте их, а потом возьмите из колоды "
        "столько же",
        f"сбросьте любые карты из руки (не более {_upto(n)}) и доберите ровно столько, "
        "сколько сбросили",
    ])


_DEST = {
    "discard": ["", " и положите её в свой сброс", " в сброс"],
    "hand": [" и положите её прямо в руку", " прямо в руку", " — она сразу отправляется в руку"],
    "top": [" и положите её на верх своей колоды", " на верх своей колоды",
            " — она ложится на верх вашей колоды"],
}


def _r_gain(e, r):
    obj = _TYPE_ACC[e.get("type")]
    m = e["max"]
    d = _DEST[e.get("dest", "discard")][r.randrange(3)]
    if e.get("type") in (TREAS, VIC):
        d = d.replace("её", "его").replace("она сразу отправляется", "он сразу отправляется").replace(
            "она ложится", "он ложится")
    return r.choice([
        f"получите {obj} стоимостью не больше {m}{d}",
        f"заберите из запаса {obj} стоимостью до {m} включительно{d}",
        f"получите из запаса {obj} ценой не дороже {m} {_pl(m, 'монеты', 'монет', 'монет')}{d}",
        f"выберите в запасе {obj} стоимостью {m} или меньше и получите эту карту"
        + d.replace(" и положите её", ", положив её"),
    ])


def _r_remodel(e, r):
    p = e["plus"]
    w = {1: "одну монету", 2: "две монеты", 3: "три монеты"}[p]
    if e.get("ttype"):
        return r.choice([
            f"уничтожьте Товар из руки и получите Товар, который дороже уничтоженного не более чем "
            f"на {w}, прямо в руку",
            f"переплавьте Товар: отправьте его из руки на свалку и заберите из запаса в руку Товар "
            f"стоимостью не выше стоимости уничтоженного плюс {p}",
            f"выберите в руке Товар и уничтожьте его; взамен получите в руку Товар, превосходящий "
            f"его по цене не больше чем на {p}",
        ])
    return r.choice([
        "уничтожьте карту из руки и получите карту, которая стоит не более чем на "
        f"{w} дороже уничтоженной",
        "отправьте на свалку одну карту из руки; затем заберите из запаса карту стоимостью "
        f"не выше стоимости уничтоженной плюс {p}",
        "выберите карту в руке и уничтожьте её, после чего получите карту, цена которой "
        f"превышает цену уничтоженной не больше чем на {p}",
        f"перестройте карту: уничтожьте её из руки и получите взамен любую карту дороже "
        f"не более чем на {w}",
    ])


def _r_if_played(e, r, sub):
    k, t = e["k"], e["type"]
    what = f"{_NEU[k] if t == ACT else _MAS[k] if t == TREAS else k} {_types_pl(t, k)}"
    return r.choice([
        f"если в этот ход до этой карты вы уже сыграли хотя бы {what}, {sub}",
        f"если раньше в этом ходу вами было разыграно не меньше чем {what} (эта карта не "
        f"считается), {sub}",
        f"при условии, что ещё до этой карты в текущем ходу вы разыграли {what} или больше: {sub}",
    ])


def _r_coins_per(e, r):
    per, t = e["per"], e["type"]
    if e.get("where") == "pier":
        return r.choice([
            "+1 монета за каждую вашу Постройку на пристани",
            "получите по монете за каждую Постройку, которая стоит у вас на пристани",
            "сосчитайте свои Постройки на пристани и получите столько же монет",
        ])
    each = {1: _EACH[t], 2: f"каждые два {_TYPE_PL[t][1]}", 3: f"каждые три {_TYPE_PL[t][1]}"}[per]
    lying = "лежащий" if t == TREAS else "лежащую"
    return r.choice([
        f"+1 монета за {each} на вашем столе",
        f"получите по монете за {each}, лежащие у вас на столе (с округлением вниз)"
        if per > 1 else f"получите по монете за {each}, {lying} у вас на столе",
        f"пересчитайте {'Товары' if t == TREAS else 'карты Действия'} на своём столе и получите "
        f"одну монету за {each}",
    ])


def _r_coins_per_empty(e, r):
    k = e["each"]
    mon = f"{k} {_pl(k, 'монета', 'монеты', 'монет')}"
    if e.get("scope") == "kingdom":
        return r.choice([
            f"+{mon} за каждую опустевшую стопку королевства",
            f"получите по {'монете' if k == 1 else f'{k} монеты'} за каждую пустую стопку карт "
            "королевства",
            f"сосчитайте стопки королевства, в которых не осталось карт: за каждую +{mon}",
        ])
    return r.choice([
        f"+{mon} за каждую пустую стопку запаса",
        f"получите по {'монете' if k == 1 else f'{k} монеты'} за каждую стопку запаса, в которой "
        "не осталось карт",
        f"сосчитайте опустевшие стопки запаса: за каждую из них +{mon}",
    ])


def _r_dig(e, r):
    t = e["type"]
    if t == VIC:
        return r.choice([
            "открывайте карты с верха своей колоды, пока не откроете Владение; положите его в руку, "
            "а остальные открытые карты сбросьте",
            "ищите в колоде Владение: переворачивайте верхние карты по одной, пока оно не найдётся; "
            "найденное Владение — в руку, прочие открытые карты — в сброс",
        ])
    it = "его" if t == TREAS else "её"
    obj = "Товар" if t == TREAS else "карту Действия"
    return r.choice([
        f"открывайте карты с верха своей колоды, пока не откроете {obj}; положите {it} в руку, "
        "а остальные открытые карты сбросьте",
        f"переворачивайте верхние карты своей колоды одну за другой до первого "
        f"{'Товара' if t == TREAS else 'Действия'}: {'он' if t == TREAS else 'оно'} "
        "отправляется в руку, прочие открытые карты — в сброс",
        f"ищите в колоде {obj}: открывайте карты сверху, пока {'он не найдётся' if t == TREAS else 'она не найдётся'}; "
        f"найденную карту положите в руку, остальные открытые сбросьте",
    ])


def _r_look_take(e, r):
    n = e["n"]
    if e["type"] != TREAS:
        return r.choice([
            f"откройте {_FEM[n]} верхние карты своей колоды: все карты Действия из них заберите в "
            "руку, остальные сбросьте",
            f"посмотрите {n} верхние карты колоды; каждое Действие среди них идёт в руку, прочее — "
            "в сброс",
        ])
    return r.choice([
        f"посмотрите {_FEM[n]} верхние карты своей колоды: все Товары из них положите в руку, "
        "остальные сбросьте",
        f"откройте {n} верхние карты своей колоды; Товары среди них отправляются в руку, "
        "всё прочее — в сброс",
        f"откройте верхние карты колоды ({n} шт.), заберите в руку каждый открытый Товар, "
        "а остальные открытые карты сбросьте",
    ])


def _r_draw_to(e, r):
    n = e["n"]
    return r.choice([
        f"добирайте карты, пока в руке не окажется {n}",
        f"возьмите столько карт, чтобы в руке стало {n}",
        f"доведите руку до {_GEN[n]} карт, беря карты по одной",
    ])


def _r_cost_cut(e, r):
    n = e["n"]
    return r.choice([
        f"в этот ход все карты стоят на {n} меньше (но не меньше нуля)",
        f"до конца хода цены всех карт снижаются на {_FEM[n]} {_pl(n, 'монету', 'монеты', 'монет')}, "
        "но не ниже 0",
        f"скидка: в этом ходу стоимость каждой карты уменьшается на {n} (минимум 0)",
    ])


def _r_buy_to_top(e, r):
    return r.choice([
        "в этот ход купленные карты кладите не в сброс, а на верх своей колоды",
        "всё, что вы купите в этом ходу, отправляется на верх вашей колоды, а не в сброс",
        "до конца хода покупки ложатся на верх вашей колоды",
    ])


def _r_others_draw(e, r):
    n = e["n"]
    return r.choice([
        f"каждый другой игрок берёт {_cards(n, r)}",
        f"все остальные игроки добирают по {'одной карте' if n == 1 else _cards(n, r)}",
        f"остальные игроки тоже берут по {'карте' if n == 1 else _cards(n, r)}",
    ])


def _r_target_glory(e, r):
    n, s = e["n"], e["self_n"]
    return r.choice([
        f"выберите соперника: он теряет {n} славы, а вы получаете {s}",
        f"укажите одного из соперников — у него отнимается {n} славы; вы прибавляете себе {s}",
        f"донесите на соперника по выбору: минус {n} к его славе и плюс {s} к вашей",
    ])


def _r_double(e, r):
    return r.choice([
        "выберите в руке карту Действия (не Контракт и не Постройку) и разыграйте её дважды",
        "разыграйте дважды одну карту Действия из руки; Контракты и Постройки для этого не годятся",
        "возьмите из руки Действие, которое не является ни Контрактом, ни Постройкой, и "
        "разыграйте его два раза подряд",
    ])


def _r_return_curse(e, r):
    n = e["n"]
    return r.choice([
        f"можете вернуть в запас до {_GEN[n]} Тин{'ы' if n == 1 else ''} из руки",
        f"верните из руки в стопку запаса не больше {n} {_pl(n, 'Тины', 'Тин', 'Тин')}",
        f"отдайте в запас любые Тины из руки, но не более {_GEN[n]}",
    ])


def _r_attack_discard_to(e, r):
    n = e["n"]
    return r.choice([
        f"каждый соперник сбрасывает карты из руки, пока у него не останется {n}",
        f"все соперники, у которых в руке больше {_upto(n)}, сбрасывают лишние, оставляя {n}",
        f"соперники урезают руку до {_GEN[n]} карт, сбрасывая остальные",
    ])


def _r_attack_curse(e, r):
    return r.choice([
        "каждый соперник получает Тину",
        "все остальные игроки получают по Тине из запаса",
        "соперники получают по карте «Тина»",
    ])


def _r_attack_glory(e, r):
    n = e["n"]
    return r.choice([
        f"каждый соперник теряет {n} славы",
        f"у каждого соперника слава уменьшается на {n}",
        f"все соперники отдают в общий запас по {n} славы",
    ])


def _r_attack_reveal_trash(e, r):
    lo, hi = e["lo"], e["hi"]
    return r.choice([
        f"каждый соперник открывает верхнюю карту своей колоды; если её стоимость от {lo} до {hi} "
        "включительно, она уничтожается, иначе он её сбрасывает",
        f"соперники по очереди открывают верхнюю карту колоды: карта стоимостью {lo}–{hi} "
        "уничтожается, любая другая уходит в сброс",
        f"каждый соперник переворачивает верх своей колоды и уничтожает открытую карту, если она "
        f"стоит не меньше {lo} и не больше {hi}; в противном случае карта сбрасывается",
    ])


def _r_gain_named(e, r):
    return r.choice([f"получите «{e['card']}»", f"получите из запаса карту «{e['card']}»",
                     f"заберите из запаса «{e['card']}»"])


def _r_choose(e, r, seed):
    opts = [_clauses(o, seed + f"/o{i}") or "ничего не делайте" for i, o in enumerate(e["options"])]
    style = r.randrange(3)
    if style == 0:
        return "выберите одно: " + "; или ".join(opts)
    if style == 1:
        return "на выбор (первый вариант — 1, второй — 2): " + "; ".join(
            f"{i + 1}) {o}" for i, o in enumerate(opts))
    return "выберите один из вариантов — " + " — либо ".join(f"«{o}»" for o in opts)


def _r_trash_self_then(e, r, seed):
    sub = _clauses(e["then"], seed + "/then")
    return r.choice([
        f"уничтожьте эту карту; если вы это сделали, {sub}",
        f"отправьте эту карту на свалку — и только если она действительно уничтожена, {sub}",
        f"эта карта уничтожается со стола; если это удалось, {sub}",
    ])


def _r_if_coins_left(e, r, seed):
    sub = _clauses(e["then"], seed + "/then")
    k = e["k"]
    return r.choice([
        f"если у вас осталось не меньше {k} неистраченных монет, {sub}",
        f"если к этому моменту в кошельке хода лежит {k} или больше монет, {sub}",
    ])


def _r_topdeck_from_discard(e, r):
    return r.choice([
        "можете положить одну карту из своего сброса на верх своей колоды",
        "выберите в своём сбросе карту (или откажитесь от этого) и положите её на верх колоды",
        "если хотите, переложите одну карту из сброса на верх своей колоды",
        "одну карту из вашего сброса по вашему выбору можно вернуть на верх колоды",
    ])


def _r_spend_glory(e, r):
    n, k = e["n"], e["each"]
    mon = f"{k} {_pl(k, 'монета', 'монеты', 'монет')}"
    return r.choice([
        f"можете потратить до {n} славы: за каждое потраченное очко славы +{mon}",
        f"сдайте по желанию от нуля до {n} жетонов славы и получите {k} "
        f"{_pl(k, 'монету', 'монеты', 'монет')} за каждый",
        f"обменяйте не больше {n} своей славы на монеты: по {'монете' if k == 1 else mon} за очко",
    ])


def _r_discard_for_coins(e, r):
    n = e["n"]
    return r.choice([
        f"сбросьте из руки до {_upto(n)}; за каждую сброшенную карту +1 монета",
        f"можете сбросить не больше {_upto(n)} из руки и получить по монете за каждую",
        f"продайте до {_upto(n)} из руки: сбросьте их и получите по монете за каждую",
    ])


def _r_mill_count(e, r):
    n, t = e["n"], e["type"]
    return r.choice([
        f"откройте {_FEM[n]} верхние карты своей колоды и сбросьте их; за {_EACH[t]} среди них "
        "+1 монета",
        f"снимите с верха колоды {_cards(n, r)}, откройте и отправьте в сброс; получите по монете "
        f"за {_EACH[t]} среди открытых",
        f"сбросьте с верха своей колоды {_cards(n, r)} лицом вверх и получите столько монет, "
        f"сколько среди них {_TYPE_PL[t][2]}",
    ])


def _r_gain_copy_prev(e, r):
    m = e["max"]
    return r.choice([
        "получите ещё одну карту с тем же названием, что и карта, разыгранная вами в этом ходу "
        f"непосредственно перед этой, если та стоит не больше {m}",
        f"если карта, которую вы разыграли в этом ходу прямо перед этой, стоит не больше {m}, "
        "получите из запаса такую же",
        "скопируйте свой предыдущий розыгрыш этого хода: получите карту с тем же названием, если "
        f"она стоит {m} или меньше",
    ])


def _r_attack_topdeck(e, r):
    k = e["k"]
    return r.choice([
        f"каждый соперник, у которого в руке не меньше {k} карт, кладёт самую дорогую карту из руки "
        "на верх своей колоды",
        f"соперники, держащие в руке {k} карт или больше, возвращают на верх колоды по самой дорогой "
        "карте из руки",
        f"у каждого соперника с {k}+ картами в руке самая дорогая из них отправляется на верх его "
        "колоды",
    ])


def _r_attack_gain_top(e, r):
    c = e["card"]
    return r.choice([
        f"каждый соперник получает карту «{c}» на верх своей колоды",
        f"соперники получают по «{c}» из запаса, и эта карта ложится на верх их колод",
        f"все соперники получают из запаса «{c}» — прямо на верх колоды",
    ])


def _r_draw_per_glory(e, r):
    per, cap = e["per"], e["cap"]
    return r.choice([
        f"возьмите по карте за каждые {per} славы у вас, но не больше {_upto(cap)}",
        f"за каждые полные {per} славы возьмите одну карту (всего не более {_upto(cap)})",
        f"посчитайте свою славу: берите по карте на каждые {per} её очка, максимум {cap}",
    ])


def _r_coins_per_hand(e, r):
    per = e["per"]
    return r.choice([
        f"+1 монета за каждые {per} карты у вас в руке",
        f"получите по монете за каждые {per} карты, которые сейчас у вас в руке (с округлением вниз)",
        f"пересчитайте карты в руке и получите одну монету на каждые {per} из них",
    ])


_SIMPLE = {
    "draw": _r_draw, "actions": _r_actions, "buys": _r_buys, "coins": _r_coins, "glory": _r_glory,
    "trash_hand": _r_trash_hand, "discard_draw": _r_discard_draw, "gain": _r_gain,
    "remodel": _r_remodel, "coins_per": _r_coins_per, "coins_per_empty": _r_coins_per_empty,
    "dig": _r_dig, "look_take": _r_look_take, "draw_to": _r_draw_to, "cost_cut": _r_cost_cut,
    "buy_to_top": _r_buy_to_top, "others_draw": _r_others_draw, "target_glory": _r_target_glory,
    "double": _r_double, "return_curse": _r_return_curse,
    "attack_discard_to": _r_attack_discard_to, "attack_curse": _r_attack_curse,
    "attack_glory": _r_attack_glory, "attack_reveal_trash": _r_attack_reveal_trash,
    "gain_named": _r_gain_named,
    "topdeck_from_discard": _r_topdeck_from_discard, "spend_glory": _r_spend_glory,
    "discard_for_coins": _r_discard_for_coins, "mill_count": _r_mill_count,
    "gain_copy_prev": _r_gain_copy_prev, "attack_topdeck": _r_attack_topdeck,
    "attack_gain_top": _r_attack_gain_top, "draw_per_glory": _r_draw_per_glory,
    "coins_per_hand": _r_coins_per_hand,
}


def _alt_draw(e, r):
    n = e["n"]
    return r.choice([f"наберите из колоды в руку {_cards(n, r)}", f"вам положено взять {_cards(n, r)}",
                     f"снимите с колоды {_cards(n, r)} себе в руку"])


def _alt_actions(e, r):
    n = e["n"]
    return r.choice([f"счётчик действий увеличивается на {n}",
                     f"ещё {_NEU[n]} {_pl(n, 'действие', 'действия', 'действий')} в этом ходу"])


def _alt_buys(e, r):
    n = e["n"]
    return r.choice([f"счётчик покупок увеличивается на {n}",
                     f"в этом ходу у вас на {_FEM[n]} {_pl(n, 'покупку', 'покупки', 'покупок')} больше"])


def _alt_coins(e, r):
    n = e["n"]
    return r.choice([f"в кошелёк хода {_pl(n, 'идёт', 'идут', 'идут')} ещё {n} "
                     f"{_pl(n, 'монета', 'монеты', 'монет')}",
                     f"вы выручаете {_FEM[n]} {_pl(n, 'монету', 'монеты', 'монет')}"])


def _alt_glory(e, r):
    n = e["n"]
    return r.choice([f"вам достаётся {n} {_pl(n, 'очко', 'очка', 'очков')} славы",
                     f"запишите себе {_NEU[n]} {_pl(n, 'очко', 'очка', 'очков')} славы"])


def _alt_trash_hand(e, r):
    n = e["n"]
    return r.choice([f"отправьте на свалку из руки сколько угодно карт, но не более {_upto(n)}",
                     f"при желании уничтожьте из руки {'одну карту' if n == 1 else f'до {_upto(n)}'}"])


def _alt_attack_glory(e, r):
    n = e["n"]
    return r.choice([f"каждый из соперников лишается {n} славы",
                     f"слава каждого соперника падает на {n} (но не ниже нуля)"])


def _alt_attack_discard_to(e, r):
    n = e["n"]
    return r.choice([f"у каждого соперника в руке должно остаться не больше {n} карт: лишние он "
                     "сбрасывает", f"соперники сбрасывают из руки лишнее, пока карт не станет {n}"])


def _alt_others_draw(e, r):
    n = e["n"]
    return r.choice([f"все прочие игроки берут по {'одной карте' if n == 1 else _cards(n, r)}",
                     f"каждый, кроме вас, тоже берёт {_cards(n, r)}"])


def _alt_discard_draw(e, r):
    n = e["n"]
    return r.choice([f"замените до {_upto(n)} из руки: сбросьте их и возьмите из колоды столько же"])


_ALT = {"draw": _alt_draw, "actions": _alt_actions, "buys": _alt_buys, "coins": _alt_coins,
        "glory": _alt_glory, "trash_hand": _alt_trash_hand, "attack_glory": _alt_attack_glory,
        "attack_discard_to": _alt_attack_discard_to, "others_draw": _alt_others_draw,
        "discard_draw": _alt_discard_draw}


@functools.cache
def _south_names() -> frozenset[str]:
    return frozenset(c["name"] for c in _south_defs())


def _clause(e: dict, seed: str) -> str:
    r = random.Random(seed)
    op = e["op"]
    if op in _ALT and seed.split("/", 1)[0] in _south_names():
        ra = random.Random(seed + "/alt")
        if ra.random() < 0.6:
            return _ALT[op](e, ra)
    if op in _SIMPLE:
        return _SIMPLE[op](e, r)
    if op == "choose":
        return _r_choose(e, r, seed)
    if op == "trash_self_then":
        return _r_trash_self_then(e, r, seed)
    if op == "if_coins_left":
        return _r_if_coins_left(e, r, seed)
    if op == "if_played":
        return _r_if_played(e, r, _clauses(e["then"], seed + "/then"))
    raise AssertionError(op)


def _clauses(effs: list[dict], seed: str) -> str:
    parts = [_clause(e, f"{seed}/{i}") for i, e in enumerate(effs)]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + ", затем " + parts[-1]


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _sentences(effs: list[dict], seed: str) -> str:
    """Top-level effects: short resource ones grouped in a line, others as sentences."""
    out, group = [], []
    for i, e in enumerate(effs):
        c = _clause(e, f"{seed}/{i}")
        if c.startswith("+"):
            group.append(c)
            continue
        if group:
            out.append(", ".join(group) + ".")
            group = []
        out.append(_cap(c) + ".")
    if group:
        out.append(", ".join(group) + ".")
    return " ".join(out)


_TRIG_HEAD = {
    "start": ["В начале каждого вашего хода", "Каждый ваш ход, в его начале",
              "Пока постройка стоит на пристани, в начале каждого вашего хода"],
    "end": ["В конце каждого вашего хода, перед очисткой", "Каждый раз, когда ваш ход подходит к "
            "концу (до очистки)"],
    "trash": ["Всякий раз, когда уничтожается любая ваша карта",
              "Каждый раз, когда одна из ваших карт отправляется на свалку"],
    "opp_curse": ["Всякий раз, когда соперник получает Тину", "Каждый раз, когда кто-то из "
                  "соперников получает карту «Тина»"],
    "opp_attack": ["Всякий раз, когда соперник разыгрывает Интригу",
                   "Каждый раз, когда кто-либо из соперников разыгрывает Интригу"],
    "shuffle": ["Всякий раз, когда вы перемешиваете свой сброс",
                "Каждый раз, когда ваш сброс перемешивается и становится колодой"],
}
_FILTER_NOM = {None: "любая карта", TREAS: "Товар", ACT: "карта Действия", VIC: "Владение",
               ATT: "Интрига"}
_FILTER_OBJ = {None: "любую карту", TREAS: "Товар", ACT: "карту Действия", VIC: "Владение",
               ATT: "Интригу"}


def _trigger_text(t: dict, seed: str) -> str:
    r = random.Random(seed + "/head")
    on = t["on"]
    body = _clauses(t["effects"], seed + "/eff")
    obj = _FILTER_OBJ[t.get("filter")]
    if on == "shuffle" and t.get("min"):
        head = r.choice([f"Всякий раз, когда вы перемешиваете сброс, в котором не меньше {t['min']} "
                         "карт", f"Каждый раз, когда вы перемешиваете свой сброс из {t['min']} или "
                         "более карт"])
    elif on == "trash" and t.get("filter"):
        head = r.choice([f"Всякий раз, когда уничтожается ваш {_FILTER_NOM[t['filter']]}",
                         "Каждый раз, когда один из ваших Товаров отправляется на свалку"])
    elif on in _TRIG_HEAD:
        head = r.choice(_TRIG_HEAD[on])
    elif on == "buy":
        head = r.choice([f"Всякий раз, когда вы покупаете {obj}", f"Каждый раз, купив {obj}"])
    elif on == "gain":
        head = r.choice([f"Всякий раз, когда вы получаете {obj}",
                         f"Каждый раз, когда к вам приходит "
                         f"{_FILTER_NOM[t.get('filter')]} (любым способом получения)"])
    elif on == "play":
        if t.get("first"):
            head = r.choice([f"Когда вы впервые за ход разыгрываете {obj}",
                             f"Один раз за ход — при розыгрыше вашего первого в этом ходу "
                             f"{'Товара' if t.get('filter') == TREAS else 'Действия'}"])
        else:
            head = r.choice([f"Всякий раз, когда вы разыгрываете {obj}",
                             f"Каждый раз, когда вы играете {obj}"])
    else:  # pragma: no cover
        raise AssertionError(on)
    return f"{head}: {body}."


def _vp_text(card: dict, seed: str) -> str:
    r = random.Random(seed)
    parts = []
    vp = card.get("vp", 0)
    if vp:
        n = abs(vp)
        word = f"{n} {_pl(n, 'очко', 'очка', 'очков')} победы"
        parts.append(f"−{word}." if vp < 0 else r.choice([f"{word}.", f"Стоит {word}."]))
    rule = card.get("vp_rule")
    if rule:
        if rule["kind"] == "per_cards":
            parts.append(r.choice([
                f"В конце игры: 1 очко победы за каждые {rule['per']} карт, которыми вы владеете "
                "(с округлением вниз).",
                f"При подсчёте приносит по очку за каждые полные {rule['per']} карт среди всех ваших "
                "карт."]))
        elif rule["kind"] == "per_type":
            t = rule["type"]
            if rule["per"] == 1:
                parts.append(f"В конце игры: {rule['each']} очка победы за каждую вашу "
                             f"{'Постройку' if t == BLD else t}.")
            else:
                what = {TREAS: "Товара", ACT: "карт Действия", DUR: "Контракта"}[t]
                parts.append(f"В конце игры: {rule['each']} очко победы за каждые {rule['per']} "
                             f"{what}, которыми вы владеете.")
        elif rule["kind"] == "per_glory":
            parts.append(f"В конце игры: {rule['each']} очко победы за каждые {rule['per']} "
                         "славы у вас (с округлением вниз).")
        elif rule["kind"] == "per_distinct":
            parts.append(r.choice([
                f"В конце игры: 1 очко победы за каждые {rule['per']} разных названий среди ваших карт.",
                f"При подсчёте: сосчитайте, сколько различных карт (по названию) у вас есть, и получите "
                f"по очку победы за каждые полные {rule['per']}."]))
        elif rule["kind"] == "per_named":
            parts.append(r.choice([
                f"В конце игры: 1 очко победы за каждые {rule['per']} "
                f"{_pl(rule['per'], 'карту', 'карты', 'карт')} «{rule['card']}», которыми вы владеете.",
                f"При подсчёте приносит по очку за каждые полные {rule['per']} "
                f"{_pl(rule['per'], 'вашу карту', 'ваши карты', 'ваших карт')} «{rule['card']}»."]))
    return " ".join(parts)


def _react_text(card: dict) -> str:
    seed = card["name"]
    rc = card["react"]
    r = random.Random(seed + "/react")
    head = r.choice(["Когда соперник разыгрывает Интригу, а эта карта у вас в руке",
                     "Если соперник разыгрывает Интригу, пока эта карта лежит у вас в руке"])
    bits = []
    if rc.get("protect"):
        bits.append(r.choice(["эта Интрига на вас не действует",
                              "вы защищены от этой Интриги"]))
    if rc.get("effects"):
        bits.append(_clauses(rc["effects"], seed + "/reacteff"))
    return f"{head}: {', и '.join(bits)}. Эта карта остаётся в руке."


def _card_text(card: dict) -> str:
    """Full printed text block of a card (list of paragraphs)."""
    seed = card["name"]
    paras = []
    if card.get("play"):
        paras.append(_sentences(card["play"], seed + "/play"))
    if card.get("next"):
        r = random.Random(seed + "/nexthead")
        head = r.choice(["В начале вашего следующего хода", "Когда наступит ваш следующий ход, "
                         "в самом его начале", "Следующий ход, в начале"])
        paras.append(f"{head}: {_clauses(card['next'], seed + '/next')}.")
    for i, t in enumerate(card.get("triggers", [])):
        paras.append(_trigger_text(t, f"{seed}/trg{i}"))
    if card.get("react"):
        paras.append(_react_text(card))
    for key, head in (("on_buy", "Когда вы покупаете эту карту"),
                      ("on_gain", "Когда вы получаете эту карту"),
                      ("on_trashed", "Когда эту карту уничтожают")):
        if card.get(key):
            paras.append(f"{head}: {_clauses(card[key], seed + '/' + key)}.")
    if card.get("vp") or card.get("vp_rule"):
        paras.append(_vp_text(card, seed + "/vp"))
    return "\n\n".join(paras)


# ---------------------------------------------------------------------------
# Errata, rulings and flavour text of card files
# ---------------------------------------------------------------------------

def _path_seed(name: str, path: tuple) -> tuple[tuple, str]:
    """(path of the effect dict, seed its clause was rendered with)."""
    if path[0] == "play":
        return path[:2], f"{name}/play/{path[1]}"
    if path[0] == "next":
        return path[:2], f"{name}/next/{path[1]}"
    if path[0] == "triggers":
        return path[:4], f"{name}/trg{path[1]}/eff/{path[3]}"
    raise AssertionError(path)


def _block_text(card: dict, path: tuple) -> str:
    """A whole paragraph of the card text (errata to VP, reactions and trigger conditions)."""
    name = card["name"]
    if path[0] in ("vp", "vp_rule"):
        return _vp_text(card, name + "/vp")
    if path[0] == "react":
        return _react_text(card)
    return _trigger_text(card["triggers"][path[1]], f"{name}/trg{path[1]}")[:-1]


def _errata_lines(card: dict) -> list[str]:
    steps = _ERRATA.get(card["name"])
    if not steps:
        return []
    cur = _printed_card(card)
    first_path = steps[0][1]
    history = [_get(cur, first_path)]
    lines = []
    for n, (date, path, val) in enumerate(steps, 1):
        if path == "cancel":
            history.pop()
            _set(cur, first_path, history[-1])
            lines.append(
                f"{n}. Поправка от {date}: поправка №{val + 1} ({steps[val][0]}) отменена. Снова "
                "действует значение, которое было до неё.")
            continue
        if path[0] in ("vp", "vp_rule", "react") or (path[0] == "triggers" and path[2] != "effects"):
            old = _block_text(cur, path)
            _set(cur, path, val)
            history.append(val)
            lines.append(f"{n}. Поправка от {date}: вместо «{old}» следует читать "
                         f"«{_block_text(cur, path)}».")
            continue
        if path == ("cost",):
            lines.append(f"{n}. Поправка от {date}: стоимость карты — {val} "
                         f"(вместо {_get(cur, path)}).")
            history.append(val)
            _set(cur, path, val)
            continue
        epath, seed = _path_seed(card["name"], path)
        old = _clause(_get(cur, epath), seed)
        _set(cur, path, val)
        history.append(val)
        new = _clause(_get(cur, epath), seed)
        lines.append(f"{n}. Поправка от {date}: в тексте карты вместо «{old}» следует читать "
                     f"«{new}».")
    return lines


_RULINGS_OP = {
    "draw": [
        ("Что если в колоде меньше карт, чем нужно взять?",
         "Возьмите сколько есть; когда колода опустеет, перемешайте сброс по правилам раздела 4 и "
         "продолжайте. Если пусты и колода, и сброс, вы просто берёте меньше карт."),
        ("Можно ли отказаться брать карты?", "Нет, взятие обязательно."),
    ],
    "actions": [("Переходят ли неиспользованные действия на следующий ход?",
                 "Нет. Действия, покупки и монеты хода обнуляются; новый ход начинается с одного "
                 "действия и одной покупки.")],
    "coins": [("Когда можно тратить эти монеты?",
               "В фазе покупок этого же хода. Остаток в очистке сгорает.")],
    "buys": [("Обязательно ли использовать все покупки?", "Нет, лишние покупки просто сгорают.")],
    "glory": [("Слава — это карта?", "Нет, это жетоны. В конце игры каждая единица славы — очко "
               "победы, а при равенстве очков побеждает тот, у кого больше славы.")],
    "trash_hand": [
        ("Можно ли не уничтожать ни одной карты?", "Да, «до N» включает ноль: в плане это пустой "
         "список."),
        ("В каком порядке уничтожаются выбранные карты?",
         "В порядке, записанном в решении; после каждой карты сразу срабатывают все свойства «при "
         "уничтожении»."),
    ],
    "discard_draw": [
        ("Если при доборе колода кончится, попадут ли только что сброшенные карты в новую колоду?",
         "Да: к этому моменту они уже лежат в сбросе и перемешиваются вместе с ним."),
        ("Можно ли сбросить ноль карт?", "Да, тогда вы ничего и не берёте."),
    ],
    "gain": [
        ("Какая стоимость учитывается, если в этом ходу действует скидка?",
         "Текущая, то есть со всеми скидками этого хода (раздел 8)."),
        ("Срабатывают ли свойства «когда вы покупаете» у полученной карты?",
         "Нет. Получение эффектом — не покупка, поэтому срабатывают только свойства «при "
         "получении»."),
        ("Можно ли отказаться?", "Нет. Если в запасе есть подходящая карта, одну из них нужно "
         "получить; решение пустое (null) только когда подходящих карт нет."),
    ],
    "remodel": [
        ("Можно ли получить карту дешевле уничтоженной?", "Да, ограничение только сверху."),
        ("Можно ли уничтожить и получить карту с тем же названием?",
         "Да, если её стопка в запасе не пуста."),
        ("Сколько решений записывается в плане?", "Всегда два: какую карту уничтожить и какую "
         "получить. При пустой руке оба решения — null."),
    ],
    "choose": [("Как записывается выбор в плане?",
                "Числом — номером варианта в том порядке, в каком варианты напечатаны на карте, "
                "начиная с 1.")],
    "if_played": [
        ("Считается ли сама эта карта?", "Нет. Считаются только розыгрыши, случившиеся раньше в "
         "этом ходу; повторные розыгрыши «Эха прибоя» считаются каждый отдельно."),
        ("Считаются ли Контракты, исполнившиеся в начале хода?",
         "Нет, исполнение контракта в начале хода — не розыгрыш карты."),
    ],
    "coins_per": [
        ("Учитываются ли карты, оставшиеся на столе с прошлого хода?",
         "Да, если они подходят по типу: считается всё, что лежит на вашем столе. Постройки "
         "стоят на пристани и на стол не попадают."),
    ],
    "coins_per_empty": [("Считается ли пустая стопка Тины?", "Да, считается любая стопка запаса.")],
    "dig": [
        ("Что если колода кончилась посреди поиска?",
         "Перемешайте сброс и продолжайте; уже открытые карты лежат отдельно и в перемешивание "
         "не попадают."),
        ("Что если подходящей карты нет вовсе?",
         "Открывайте, пока не опустеют и колода, и сброс; затем все открытые карты сбросьте в "
         "порядке открытия."),
        ("Считается ли найденная карта взятой?", "Нет, это не взятие: она не входит в draws."),
    ],
    "look_take": [
        ("В каком порядке сбрасываются остальные карты?", "В порядке открытия."),
        ("Если в колоде меньше карт?", "Когда колода опустеет, перемешайте сброс; уже открытые "
         "карты в перемешивание не попадают."),
    ],
    "draw_to": [("Что если в руке уже столько карт или больше?", "Ничего не происходит.")],
    "cost_cut": [
        ("Суммируются ли скидки от нескольких карт?", "Да."),
        ("Влияет ли скидка на то, какие карты сбрасывает соперник при атаке?",
         "Нет, там всегда используется базовая стоимость."),
    ],
    "buy_to_top": [
        ("Касается ли это карт, полученных эффектами?", "Нет, только купленных."),
        ("Если купить несколько карт?", "Каждая следующая ложится поверх предыдущей."),
    ],
    "others_draw": [("В каком порядке берут остальные игроки?",
                     "По порядку хода, начиная с игрока слева от активного.")],
    "target_glory": [
        ("Может ли слава соперника уйти в минус?", "Нет, она не опускается ниже нуля."),
        ("Защищают ли от этого Обереги?", "Нет: эта карта — не Интрига."),
        ("Как указать соперника в плане?", "Номером игрока (нумерация с нуля в порядке хода)."),
    ],
    "double": [
        ("Тратится ли действие на повторяемую карту?", "Нет, действие тратится только на "
         "розыгрыш этой карты."),
        ("Что если повторяемая карта при первом розыгрыше уничтожила себя?",
         "Второй розыгрыш всё равно происходит, но «уничтожьте эту карту» в нём уже ничего не "
         "делает, и бонус «если вы это сделали» не выдаётся."),
        ("В каком порядке записываются решения?", "Сначала название повторяемой карты, затем "
         "решения её первого розыгрыша, затем второго."),
    ],
    "return_curse": [("Считается ли возврат уничтожением?", "Нет. Свойства «при уничтожении» не "
                      "срабатывают, а стопка Тины в запасе пополняется.")],
    "attack_discard_to": [("Какие карты сбрасывает соперник?",
                           "Не по своему выбору, а по правилу раздела 9: всякий раз самую дешёвую "
                           "по базовой стоимости, при равенстве — ту, что пришла в руку позже.")],
    "attack_curse": [("Что если Тины на всех не хватает?", "Соперники получают по очереди, "
                      "начиная слева от вас; когда стопка опустеет, остальные ничего не получают.")],
    "attack_glory": [("Может ли слава уйти в минус?", "Нет, не ниже нуля.")],
    "attack_reveal_trash": [
        ("Что если у соперника пуста колода?", "Он перемешивает сброс по обычным правилам; если "
         "пусто и там, ничего не происходит."),
        ("Какая стоимость сравнивается?", "Базовая (с учётом официальных поправок), без скидок "
         "текущего хода."),
    ],
    "trash_self_then": [("Что если эта карта уже не на столе?", "Тогда ничего не происходит.")],
    # «Южный ветер» (раздел 17)
    "coins_per@pier": [
        ("Считается ли постройка, возведённая в этом же ходу?", "Да, если она уже стоит на пристани "
         "к моменту выполнения этой части текста."),
        ("Считаются ли карты на столе?", "Нет. В отличие от эффектов «за карты на вашем столе» "
         "(16.1), здесь считается только пристань (17.12)."),
    ],
    "coins_per_empty@kingdom": [
        ("Считается ли пустая стопка Тины или Гроша?", "Нет. Базовые стопки (раздел 2) — не стопки "
         "королевства; считаются только пустые стопки карт королевства (17.11)."),
        ("А пустая стопка Владения из королевства?", "Да, если эта карта не входит в базовый набор."),
    ],
    "remodel@typed": [
        ("Что если в руке нет Товаров?", "Тогда оба решения — null, и ничего не происходит. Если "
         "Товар в руке есть, уничтожить его обязательно."),
        ("Куда идёт полученный Товар?", "Прямо в руку; его можно разыграть в этом же ходу, если фаза "
         "розыгрыша ещё не закончилась."),
        ("Можно ли получить карту, которая не является Товаром?", "Нет."),
    ],
    "topdeck_from_discard": [
        ("Обязательно ли перекладывать карту?", "Нет, можно отказаться: в плане это null. При пустом "
         "сбросе решение тоже null."),
        ("Считается ли переложенная карта взятой или полученной?", "Нет. Это просто перемещение из "
         "сброса на колоду; свойства «при получении» не срабатывают, draws не растёт."),
        ("Если после этого на карте сказано «возьмите карту»?", "Вы возьмёте как раз ту карту, "
         "которую только что положили на верх колоды: текст выполняется по порядку."),
    ],
    "spend_glory": [
        ("Можно ли потратить больше славы, чем у меня есть?", "Нет: не больше, чем указано на карте, "
         "и не больше вашей текущей славы. Решение — целое число, ноль допустим."),
        ("Учитывается ли слава, полученная раньше в тексте этой же карты?", "Да, текст выполняется "
         "по порядку, и к моменту траты слава уже начислена."),
        ("Попадают ли монеты от траты славы в coins_total?", "Да, это обычные монеты вашего хода."),
    ],
    "discard_for_coins": [
        ("Это взятие?", "Нет, карт вы не берёте: только сбрасываете и получаете монеты."),
        ("В каком порядке ложатся сброшенные карты?", "В порядке, записанном в решении."),
    ],
    "mill_count": [
        ("Когда открытые карты попадают в сброс?", "Только после того, как открыты все карты (или "
         "колода и сброс кончились); если колода опустела посреди открывания, перемешивается сброс "
         "без уже открытых карт (раздел 4.4)."),
        ("Какие карты приносят монеты?", "Только открытые этим эффектом карты указанного типа; "
         "поправки могут менять тип, смотрите их внимательно."),
        ("Это взятие?", "Нет, карты в руку не попадают и draws не растёт."),
    ],
    "gain_copy_prev": [
        ("Что считается предыдущим розыгрышем?", "Розыгрыш, записанный в этом ходу непосредственно "
         "перед розыгрышем этой карты, — Товар это или Действие. Подробности — в разделе 17.5."),
        ("А если эта карта разыграна первой в ходу?", "Тогда ничего не происходит."),
        ("С какой стоимостью сравнивается предел?", "С текущей, со скидками этого хода."),
    ],
    "attack_topdeck": [
        ("Какую карту кладёт соперник?", "Не по своему выбору: самую дорогую по базовой стоимости; "
         "при равенстве — ту, что ближе к началу руки (раздел 17.6)."),
        ("Что если у соперника в руке меньше карт, чем указано?", "С ним ничего не происходит."),
    ],
    "attack_gain_top": [
        ("Срабатывают ли у соперника свойства «при получении»?", "Да: это получение, и оно "
         "принадлежит сопернику — откликаются его постройки."),
        ("Что если стопка пуста?", "Соперники, до которых карта не дошла, ничего не получают."),
    ],
    "draw_per_glory": [
        ("Когда считается слава?", "В момент выполнения этой части текста; округление вниз."),
        ("Тратится ли слава?", "Нет, слава только считается."),
    ],
    "coins_per_hand": [
        ("Считается ли сама эта карта?", "Нет: она уже не в руке, а на столе."),
        ("Когда считаются карты в руке?", "В момент выполнения этой части текста."),
    ],
}
_RULINGS_TYPE = {
    DUR: [("Когда эта карта уходит в сброс?", "В очистке вашего следующего хода; до этого она "
           "лежит на столе."),
          ("Что раньше — эффект контракта или постройки «в начале хода»?",
           "Сначала все ваши контракты в порядке розыгрыша, затем постройки.")],
    BLD: [("Попадает ли постройка в сброс?", "Нет, она остаётся на пристани до конца игры и "
           "учитывается при подсчёте ваших карт."),
          ("Реагирует ли постройка на собственный розыгрыш?", "Нет.")],
    REACT: [("Нужно ли решение, чтобы показать оберег?", "Нет, он срабатывает автоматически; "
             "если оберегов в руке несколько, срабатывают все по порядку руки.")],
    ATT: [("Когда проверяются обереги соперников?", "Сразу после свойств построек, реагирующих на "
           "розыгрыш, и до выполнения текста этой карты.")],
}
_RULINGS_TRIG = {
    "start": ("Работает ли постройка в ход, когда её построили?", "Эффект «в начале хода» — нет: "
              "начало этого хода уже прошло."),
    "play": ("Что значит «впервые за ход», если постройку возвели посреди хода?",
             "Учитываются все розыгрыши этого хода, в том числе сделанные до появления постройки."),
    "buy": ("Когда срабатывает это свойство?", "Сразу после свойств самой купленной карты, до "
            "свойств «при получении»."),
    "gain": ("Считается ли покупка получением?", "Да. Но свойства «при получении» разрешаются "
             "после всех свойств «при покупке»."),
    "trash": ("Срабатывает ли это в чужой ход?", "Да, например при атаке. Монеты, действия и "
              "покупки, полученные вне своего хода, сгорают."),
    "opp_curse": ("А если Тину получил я сам?", "Нет, только соперник."),
    "opp_attack": ("Что раньше — это свойство или обереги?", "Сначала свойства построек, затем "
                   "обереги."),
    "end": ("Считаются ли монеты, потраченные на покупки?", "Нет, только оставшиеся после покупок."),
    "shuffle": ("Когда именно срабатывает это свойство?", "Сразу после того, как ваш сброс перемешан и "
                "стал колодой, до того как будет взята или открыта карта, из-за которой понадобилось "
                "перемешивание. Правила 17.10 и приложение Г.6 уточняют, когда оно не работает."),
}
_RULINGS_GEN = [
    ("Можно ли сыграть эту карту в фазе покупок?", "Нет, розыгрыш карт закончен, как только "
     "начались покупки."),
    ("Что происходит, если стопка этой карты в запасе пуста?", "Её нельзя купить или получить."),
    ("Можно ли разыграть карту частично?", "Нет, текст выполняется целиком, в порядке записи."),
    ("Учитывается ли эта карта в «Доме смотрителя»?", "Да, как и любая ваша карта в любой зоне."),
    ("Если у меня в руке две такие карты, какую из них я разыгрываю?", "Ту, что лежит в руке "
     "раньше (ближе к началу руки). Для итога это важно, потому что порядок руки влияет на "
     "атаки соперников."),
    ("Можно ли держать эту карту в руке, не разыгрывая?", "Да, розыгрыш всегда добровольный: "
     "план сам решает, какие карты играть."),
    ("Меняют ли поправки напечатанный текст?", "Да: действует последняя по дате неотменённая "
     "поправка; см. раздел 14 правил."),
]

_FLAVOR = [
    "Туман в гавани поднимается к полудню, и тогда видно, сколько лодок вернулось с ночного лова.",
    "Старики {na} уверяют, что раньше всё было иначе: и рыба крупнее, и цены честнее.",
    "{person} говорил{a}, что в этом городе можно продать что угодно, кроме тишины.",
    "Над причалами кричат чайки, а в конторах скрипят перья приказчиков.",
    "Кто хоть раз зимовал {na}, тот знает цену сухой одежде и горячему чаю.",
    "Ветер с моря приносит запах соли, дёгтя и свежих досок.",
    "На доске объявлений {u} кто-то снова написал мелом: «Нужны руки. Платим сразу».",
    "Поговаривают, будто {person} однажды выиграл{a} в кости целую баржу с солью.",
    "Портовые колокола отбивают склянки, и весь город сверяет по ним свою жизнь.",
    "В сумерках фонари вдоль набережной зажигают по одному, начиная от таможни.",
    "Лучше всего сделки заключаются в дождь: все сидят под навесами и никуда не спешат.",
    "Купцы с юга привозят пряности, с севера — пушнину, а с запада — новости.",
    "{person} хранил{a} в сундуке старые векселя и уверял{a}, что однажды они снова будут в цене.",
    "Рыбаки не любят говорить о погоде вслух — считается, что море подслушивает.",
    "{Na} до сих пор стоит скамья, на которой, по преданию, подписали первый договор гавани.",
    "Туманную гавань называют так не зря: бывает, что неделю не видно маяка на мысу.",
    "Каждую весну причалы чинят заново, и каждую осень шторм забирает часть работы.",
    "Говорят, что {person} знал{a} каждую мель в заливе по звуку воды.",
    "Здесь не принято спрашивать, откуда товар; принято спрашивать, сколько.",
    "Ярмарка {na} длится три дня, но разговоры о ней не утихают до следующей весны.",
    "Детям в гавани рано дают в руки весло и позже всех — кошелёк.",
    "Старые лоцманы узнают корабль по скрипу мачт раньше, чем увидят флаг.",
    "Ночью в трактирах поют про утонувшие клады, а утром идут их искать.",
    "{person} считал{a}, что удача любит тех, кто приходит на причал до рассвета.",
    "Под мостками {u} всегда стоит вода, даже в самую сухую неделю.",
    "Смотритель маяка ведёт журнал, где записаны все штормы за сорок лет.",
    "Товары здесь меряют не весом, а тем, сколько за них дадут до конца ярмарки.",
    "На стенах складов остались отметки высоты воды — самая верхняя почти под крышей.",
    "Когда в гавань заходит большой корабль, весь город выходит на набережную.",
    "{person} любил{a} повторять: «Кто считает монеты в чужом кошельке, теряет свои».",
    "В тумане звуки разносятся далеко: слышно, как на другом берегу торгуются за селёдку.",
    "Весной лёд уходит из залива за одну ночь, и утром гавань будто рождается заново.",
    "Самые старые сваи причала почернели так, что кажутся каменными.",
    "Молодые матросы мечтают о дальних рейсах, старые — о тёплой печи.",
    "{U} торгуют всем подряд: сетями, свечами, солониной и слухами.",
    "Говорят, в погребах таможни до сих пор лежат бочки, которые никто не решается открыть.",
    "{person} рассказывал{a}, что в молодости видел{a} белого кита у самого мола.",
    "По вечерам над водой стелется дым коптилен, и вся гавань пахнет рыбой и ольхой.",
    "В гавани есть правило: кто первым заметил пожар, тот первым бьёт в колокол.",
    "Лодки у причала связаны так тесно, что по ним можно перейти на другую сторону.",
    "Городской писарь уверяет, что в архиве хранятся карты мелей, которых больше нет.",
    "Если туман стоит три дня подряд, торговля замирает и все садятся за карты.",
    "Приезжие удивляются, что здесь здороваются даже с незнакомыми кораблями.",
    "Зимой причалы пустеют, и только чайки спорят за остатки улова.",
    "Хороший приказчик помнит цену каждого мешка на складе и имя каждого должника.",
    "Осенью в гавань приходят баржи с зерном, и грузчики неделю не спят.",
    "{person} утверждал{a}, что лучший товар — тот, который ещё не доплыл до берега.",
    "Писари таможни ведут книги в три столбца: что пришло, что ушло и что пропало.",
    "Весенние штормы рвут сети, и тогда в гавани слышен стук молотков до самой ночи.",
    "Мальчишки с набережной знают, у кого из капитанов можно выпросить сушёную рыбу.",
    "На причальных досках вырезаны инициалы нескольких поколений грузчиков.",
    "Если спросить в трактире, кто здесь главный, каждый назовёт другое имя.",
    "Звон монет в гавани слышен даже сквозь шум прибоя — так шутят приезжие.",
    "{person} носил{a} на шее медную монету с дыркой и никогда её не тратил{a}.",
    "Когда вода в заливе становится свинцовой, старики советуют не выходить в море.",
    "Лавки открываются с первым колоколом и закрываются, когда уходит последний покупатель.",
    "Про каждую бочку на складе здесь могут рассказать историю, и не всегда правдивую.",
    "На маяке у мыса новый смотритель, но огонь горит по-прежнему ровно.",
    "Туман умеет прятать не только корабли, но и долги.",
    "Кто торгуется громче, тот не всегда получает больше, — любят повторять на рынке.",
    "{person} каждую субботу обходил{a} все лавки и записывал{a} цены в тетрадь.",
    "Зимние вечера в гавани длинные, и за картами проходят целые ночи.",
    "Весы на рыбном рынке проверяют раз в год, и в этот день торговля идёт особенно бойко.",
    "Сваи старой пристани помнят времена, когда в гавань заходили только парусники.",
    "Говорят, что по крику чаек можно узнать, будет ли завтра улов.",
    "Счётные книги гавани хранятся в ратуше, и каждая страница в них пронумерована.",
    "{person} верил{a}, что каждая монета должна хоть раз побывать в море.",
    "В тихие дни из окон конторы видно, как по заливу ползут тени облаков.",
    "Настоящий торговец гавани умеет ждать: и прилива, и покупателя, и удачи.",
]
_PLACES = [("на Кривом мысу", "у Кривого мыса"), ("в Рыбном ряду", "у Рыбного ряда"),
           ("на Старой пристани", "у Старой пристани"), ("на Соляной улице", "у Соляной улицы"),
           ("на Таможенной площади", "у Таможенной площади"),
           ("в Лоцманской слободе", "у Лоцманской слободы"), ("на Северном молу", "у Северного мола"),
           ("в Бочарном переулке", "у Бочарного переулка"), ("на Верхнем рынке", "у Верхнего рынка")]
_PERSONS = [("старый Ефим", ""), ("вдова Прасковья", "а"), ("лоцман Архип", ""),
            ("бондарь Митрофан", ""), ("торговка Глафира", "а"), ("капитан Сабуров", ""),
            ("смотрительница Агния", "а"), ("писарь Кондратий", "")]


def _flavor(name: str) -> str:
    r = random.Random("flavor/" + name)
    k = r.randint(12, 16)
    out = []
    for tpl in r.sample(_FLAVOR, k):
        person, a = r.choice(_PERSONS)
        na, u = r.choice(_PLACES)
        text = tpl.format(na=na, u=u, Na=_cap(na), U=_cap(u), person=person, a=a)
        out.append(_cap(text))
    a, b = len(out) // 3, 2 * len(out) // 3
    return " ".join(out[:a]) + "\n\n" + " ".join(out[a:b]) + "\n\n" + " ".join(out[b:])


def _op_key(e: dict) -> str:
    """Rulings bank key: the op, refined for variants whose rulings differ."""
    if e["op"] == "coins_per" and e.get("where") == "pier":
        return "coins_per@pier"
    if e["op"] == "coins_per_empty" and e.get("scope") == "kingdom":
        return "coins_per_empty@kingdom"
    if e["op"] == "remodel" and e.get("ttype"):
        return "remodel@typed"
    return e["op"]


def _collect_ops(effs, acc):
    for e in effs:
        acc.append(_op_key(e))
        if e["op"] == "choose":
            for o in e["options"]:
                _collect_ops(o, acc)
        for key in ("then",):
            if key in e:
                _collect_ops(e[key], acc)


def _rulings(card: dict) -> list[tuple[str, str]]:
    r = random.Random("rulings/" + card["name"])
    ops: list[str] = []
    for key in ("play", "next", "on_buy", "on_gain", "on_trashed"):
        _collect_ops(card.get(key, []), ops)
    for t in card.get("triggers", []):
        _collect_ops(t["effects"], ops)
    if card.get("react"):
        _collect_ops(card["react"].get("effects", []), ops)
    items: list[tuple[str, str]] = []
    for op in dict.fromkeys(ops):
        bank = _RULINGS_OP.get(op, [])
        if bank:
            items += r.sample(bank, min(len(bank), r.choice([2, 2, 3])))
    for t in card["types"]:
        items += _RULINGS_TYPE.get(t, [])
    for t in card.get("triggers", []):
        items.append(_RULINGS_TRIG[t["on"]])
    for key, q, a in (("on_buy", "Что раньше — это свойство или постройки?",
                       "Сначала свойство самой карты, затем постройки."),
                      ("on_gain", "Срабатывает ли это при покупке?",
                       "Да, покупка — тоже получение; но после всех свойств «при покупке»."),
                      ("on_trashed", "Кто получает эффект, если карту уничтожил соперник?",
                       "Владелец карты. Монеты вне своего хода сгорают.")):
        if card.get(key):
            items.append((q, a))
    items += r.sample(_RULINGS_GEN, 3)
    seen, out = set(), []
    for q, a in items:
        if q not in seen:
            seen.add(q)
            out.append((q, a))
    return out


# ---------------------------------------------------------------------------
# Situation rulings: concrete game situations resolved by the reference engine.
# Every situation is replayed with several generator seeds; only quantities that
# do not depend on the seed are asked, so each answer follows from the rules.
# ---------------------------------------------------------------------------

_ME = "вы"
_SIMPLE_OPS = {"draw", "actions", "buys", "coins", "glory"}
_TRASHERS = ("Старьёвщик", "Клерк", "Таможенник", "Сторож лабаза", "Сплавщик", "Часовня на мысу")
_GAINERS = ("Мастерская сетей", "Караван", "Верфь", "Кузница якорей", "Скупщик", "Бакалейщик")
_DRAWERS = ("Грузчик", "Лоцман", "Шкипер", "Портовый кран", "Корабельный плотник", "Рыбачья деревня")
_DOUBLERS = ("Эхо прибоя", "Звонарь")
_FILLER = ("Грош", "Грош", "Грош", "Гривна", "Гривна", "Червонец", "Причал", "Причал", "Склад", "Тина")


def _walk_ops(effs, acc: set) -> set:
    for e in effs:
        acc.add(e["op"])
        for o in e.get("options", []):
            _walk_ops(o, acc)
        _walk_ops(e.get("then", []), acc)
    return acc


def _card_ops(card: dict) -> set:
    acc: set = set()
    for key in ("play", "next", "on_buy", "on_gain", "on_trashed"):
        _walk_ops(card.get(key, []), acc)
    for t in card.get("triggers", []):
        _walk_ops(t["effects"], acc)
    if card.get("react"):
        _walk_ops(card["react"].get("effects", []), acc)
    return acc


@functools.cache
def _pools() -> dict[str, tuple[str, ...]]:
    cards = _card_defs()

    def simple(c):
        return (c.get("play") and all(e["op"] in _SIMPLE_OPS for e in c["play"])
                and not ({DUR, BLD, ATT, REACT} & set(c["types"])))

    by = {
        "simple_act": [c["name"] for c in cards if simple(c) and ACT in c["types"]],
        "simple_treas": [c["name"] for c in cards if simple(c) and TREAS in c["types"]],
        "bld": [c["name"] for c in cards if BLD in c["types"]],
        "att": [c["name"] for c in cards if ATT in c["types"]],
        "react": [c["name"] for c in cards if REACT in c["types"]],
        "dur": [c["name"] for c in cards if DUR in c["types"]],
        "vic": [c["name"] for c in cards if VIC in c["types"]],
        "kingdom": [c["name"] for c in cards if c["name"] not in _BASE],
    }
    return {k: tuple(v) for k, v in by.items()}


def _bld_on(on: str) -> list[str]:
    cards = _cards_by_name()
    return [b for b in _pools()["bld"] if any(t["on"] == on for t in cards[b]["triggers"])]


def _pier_for(r: random.Random, card: dict) -> list[str]:
    """0–2 buildings for the active player's pier, often ones that interact with `card`."""
    ops = _card_ops(card)
    rel = ["play"]
    if ops & {"trash_hand", "remodel", "trash_self_then"}:
        rel.append("trash")
    if ops & {"gain", "remodel", "gain_named", "gain_copy_prev"}:
        rel.append("gain")
    if ops & {"draw", "dig", "look_take", "mill_count", "draw_to", "discard_draw", "draw_per_glory"}:
        rel.append("shuffle")
    if ops & {"coins_per"}:
        rel.append("start")
    out: list[str] = []
    for _ in range(r.choice([0, 1, 1, 2])):
        pool = _bld_on(r.choice(rel)) if r.random() < 0.75 else list(_pools()["bld"])
        b = r.choice(pool)
        if b not in out and b != card["name"]:
            out.append(b)
    return out


def _some(r: random.Random, k: int, extra: tuple[str, ...] = ()) -> list[str]:
    pool = list(_FILLER) + list(extra)
    return [r.choice(pool) for _ in range(k)]


def _opp_state(r: random.Random, name: str, attacked: bool) -> dict:
    hand = _some(r, r.randint(3, 6), _pools()["simple_act"][:8] + _pools()["simple_treas"][:4])
    if attacked and r.random() < 0.45:
        hand.insert(r.randint(0, len(hand)), r.choice(_pools()["react"]))
    pier = []
    if attacked and r.random() < 0.35:
        pier.append(r.choice(_bld_on("opp_attack") + _bld_on("opp_curse")))
    return {"name": name, "hand": hand, "deck": _some(r, r.choice([0, 1, 2, 3, 4])),
            "discard": _some(r, r.choice([0, 2, 3, 5])), "glory": r.randint(0, 4), "pier": pier}


def _new_spec(r: random.Random, card: dict, *, active: int = 0, n_opp: int | None = None,
              attacked: bool = False) -> dict:
    ops = _card_ops(card)
    hits_opps = attacked or bool(ops & {"others_draw", "target_glory"}) or ATT in card["types"]
    if n_opp is None:
        n_opp = r.choice([1, 1, 2]) if hits_opps else r.choice([0, 1])
    names = r.sample(_NAMES, max(n_opp, 1))
    opps = [_opp_state(r, names[i], attacked or ATT in card["types"]) for i in range(n_opp)]
    kingdom = _pools()["simple_act"][:10] + _pools()["simple_treas"][:5]
    deck_n = r.choice([0, 1, 2, 3, 4, 5, 6, 8])
    me = {"hand": _some(r, r.randint(1, 4), kingdom), "deck": _some(r, deck_n, kingdom),
          "discard": _some(r, r.choice([0, 1, 3, 4, 6, 9])), "glory": r.randint(0, 6), "pier": [],
          "table": []}
    played = []
    if active == 0:
        me["pier"] = _pier_for(r, card)
        for _ in range(r.choice([0, 0, 1, 2, 3])):
            played.append(r.choice(_pools()["simple_act"] + _pools()["simple_treas"]))
        me["table"] = [[p, False] for p in played]
    counters = {"actions": r.randint(1, 3), "buys": r.randint(1, 2), "coins": r.randint(0, 4), "cut": 0}
    if active == 0 and ops & {"gain", "remodel", "gain_copy_prev"} and r.random() < 0.3:
        counters["cut"] = 1
        played.append("Лавочник")
        me["table"].append(["Лавочник", False])
    empty: list[str] = []
    if "coins_per_empty" in ops:
        pool = [c for c in _BASE if c not in ("Тина",)] + list(r.sample(_pools()["kingdom"], 6))
        empty = r.sample(pool, r.randint(0, 4))
    curses = r.choice([0, 1, 2, 6]) if (attacked or "attack_curse" in ops) else 10
    return {"me": me, "opps": opps, "active": active, "counters": counters, "played": played,
            "empty": empty, "curses": curses, "card": card["name"], "act": {}}


def _supply_of(spec: dict) -> dict[str, int]:
    names = set(_BASE) | {spec["card"]} | set(spec["played"]) | set(_TRASHERS[:1])
    for st in [spec["me"], *spec["opps"]]:
        for zone in ("hand", "deck", "discard", "pier"):
            names |= set(st.get(zone, []))
        names |= {t[0] for t in st.get("table", [])}
    names |= set(spec["act"].get("extra", []))
    sup = {n: (40 if n == "Грош" else 10) for n in sorted(names)}
    for n in spec["empty"]:
        sup[n] = 0
    sup["Тина"] = spec["curses"]
    return sup


class _ScnAI(_AI):
    """Random legal decisions for a situation; `force` fixes the first decisions of given kinds."""

    def __init__(self, r: random.Random, kingdom, force=()):
        super().__init__(r, list(kingdom))
        self.entry = {"choices": []}
        self.force = list(force)
        self.kinds: list[tuple[int, str, object]] = []

    def choice(self, g, p, kind, eff, info):
        if self.force and self.force[0][0] == kind:
            val = self.force.pop(0)[1]
            self.entry["choices"].append(val)
        else:
            val = super().choice(g, p, kind, eff, info)
        self.kinds.append((p, kind, val))
        return val


def _scn_game(spec: dict, seed: int, dec):
    ns = _engine_ns()
    states = [spec["me"], *spec["opps"]]
    players = [{"name": _ME, "deck": list(spec["me"]["deck"])}] + [
        {"name": o["name"], "deck": list(o["deck"])} for o in spec["opps"]]
    g = ns["Game"]({"players": players, "supply": _supply_of(spec), "seed": seed, "max_rounds": 99,
                    "turns": []}, decider=dec)
    uid = 100
    for pl, st in zip(g.players, states, strict=True):
        pl.hand = list(st["hand"])
        pl.discard = list(st["discard"])
        pl.glory = st["glory"]
        pl.pier, pl.table = [], []
        for b in st.get("pier", []):
            uid += 1
            pl.pier.append({"name": b, "pending": False, "uid": uid, "pos": 0})
        for name, pending in st.get("table", []):
            uid += 1
            pl.table.append({"name": name, "pending": pending, "uid": uid, "pos": 0})
    g.uid = uid
    g.active = spec["active"]
    g._reset_turn()
    c = spec["counters"]
    g.actions, g.buys, g.coins, g.cut = c["actions"], c["buys"], c["coins"], c["cut"]
    g.played = list(spec["played"])
    return g


def _scn_exec(g, spec: dict) -> None:
    act, p = spec["act"], spec["active"]
    kind = act["kind"]
    if kind in ("play", "double"):
        g.play(p, act["play"])
    elif kind == "buy":
        g.buy(p, act["play"])
    elif kind == "next":
        g.play(p, act["play"])
        g.cleanup(p)
        g.start_turn(p)
    elif kind == "start":
        g.start_turn(p)
    elif kind == "end":
        cards = _engine_ns()["CARDS"]
        for b in list(g.players[p].pier):
            for trg in cards[b["name"]].get("triggers", []):
                if trg["on"] == "end":
                    g.resolve(p, trg["effects"], {"card": b["name"], "entry": None})
    else:  # pragma: no cover
        raise AssertionError(kind)


def _scn_snap(g) -> dict[str, int]:
    me = g.players[0]
    out = {"hand": len(me.hand), "glory": me.glory, "deck": len(me.deck), "discard": len(me.discard),
           "shuffles": me.shuffles, "curse": me.owned().count("Тина"), "trash": sum(g.trash.values())}
    if g.active == 0:
        out.update(coins=g.coins, actions=g.actions, buys=g.buys)
    for o in g.players[1:]:
        for k, v in (("hand", len(o.hand)), ("glory", o.glory), ("deck", len(o.deck)),
                     ("discard", len(o.discard)), ("curse", o.owned().count("Тина"))):
            out[f"{k}@{o.name}"] = v
    return out


def _scn_run(spec: dict, r: random.Random) -> tuple[dict, list, dict] | None:
    """Play the situation, then replay the recorded decisions with other seeds.

    Returns (snapshot of seed-independent values, decisions with their kinds, full snapshot)
    or None when the decisions do not stay legal under another seed.
    """
    ns = _engine_ns()
    dec = _ScnAI(r, _supply_of(spec), force=spec["act"].get("force", ()))
    seeds = [r.randrange(1, 2 ** 31 - 1) for _ in range(4)]
    try:
        g = _scn_game(spec, seeds[0], dec)
        _scn_exec(g, spec)
    except ns["PlanError"]:
        return None
    base = _scn_snap(g)
    stable = dict(base)
    for s in seeds[1:]:
        rep = ns["Replay"]([])
        rep.choices = list(dec.entry["choices"])
        try:
            g2 = _scn_game(spec, s, rep)
            _scn_exec(g2, spec)
        except ns["PlanError"]:
            return None
        if rep.choices:
            return None
        snap = _scn_snap(g2)
        stable = {k: v for k, v in stable.items() if snap.get(k) == v}
    return stable, dec.kinds, base


_BASE_KEYS = ("coins", "actions", "buys", "hand", "glory")


def _play_keys(card: dict, opps: list[dict]) -> list[str]:
    ops = _card_ops(card)
    keys = list(_BASE_KEYS)
    if ops & {"topdeck_from_discard", "mill_count", "dig", "look_take", "discard_draw", "discard_for_coins",
              "draw_to", "attack_gain_top"}:
        keys += ["deck", "discard"]
    if ops & {"trash_hand", "remodel", "trash_self_then", "attack_reveal_trash"}:
        keys.append("trash")
    if "return_curse" in ops:
        keys.append("curse")
    for o in opps:
        n = o["name"]
        if ops & {"attack_discard_to", "others_draw", "attack_topdeck"}:
            keys.append(f"hand@{n}")
        if ops & {"attack_glory", "target_glory"}:
            keys.append(f"glory@{n}")
        if "attack_curse" in ops:
            keys.append(f"curse@{n}")
        if ops & {"attack_topdeck", "attack_gain_top", "attack_reveal_trash"}:
            keys.append(f"deck@{n}")
    return keys


def _insert(r: random.Random, hand: list[str], *names: str) -> None:
    for n in names:
        hand.insert(r.randint(0, len(hand)), n)


def _gen_play(r, card, kind):
    spec = _new_spec(r, card)
    name = card["name"]
    if kind == "double":
        d = r.choice(_DOUBLERS)
        _insert(r, spec["me"]["hand"], d, name)
        spec["act"] = {"kind": "double", "play": d, "force": [("double", name)], "extra": [d]}
    else:
        _insert(r, spec["me"]["hand"], name)
        spec["act"] = {"kind": "play", "play": name}
    return spec, _play_keys(card, spec["opps"])


def _gen_next(r, card):
    spec = _new_spec(r, card)
    if r.random() < 0.4:
        spec["me"]["pier"] = [r.choice(_bld_on("start"))]
    _insert(r, spec["me"]["hand"], card["name"])
    spec["act"] = {"kind": "next", "play": card["name"]}
    return spec, list(_BASE_KEYS)


def _attacker_for(r, pool: list[str]) -> str:
    return r.choice(pool)


def _gen_victim(r, card, pool: list[str], *, react: bool):
    spec = _new_spec(r, card, active=1, n_opp=r.choice([1, 1, 2]), attacked=True)
    a = _attacker_for(r, pool)
    _insert(r, spec["opps"][0]["hand"], a)
    if react:
        _insert(r, spec["me"]["hand"], card["name"])
        if r.random() < 0.25:
            _insert(r, spec["me"]["hand"], r.choice(_pools()["react"]))
    else:
        spec["me"]["pier"] = [card["name"]]
        if r.random() < 0.4:
            _insert(r, spec["me"]["hand"], r.choice(_pools()["react"]))
    spec["opps"][0]["pier"] = []
    spec["counters"]["cut"] = 0
    spec["act"] = {"kind": "play", "play": a, "extra": [a]}
    ops = _card_ops(_cards_by_name()[a])
    keys = ["hand", "glory"]
    if ops & {"attack_curse"}:
        keys.append("curse")
    if ops & {"attack_topdeck", "attack_gain_top", "attack_reveal_trash"}:
        keys += ["deck", "discard"]
    if "attack_reveal_trash" in ops:
        keys.append("trash")
    if spec["curses"] == 10 and "attack_curse" in ops:
        spec["curses"] = r.choice([0, 1, 2, 5])
    return spec, keys


def _match(r, filt, pool_key: str) -> str:
    cards = _cards_by_name()
    pool = [c for c in _pools()[pool_key] if filt is None or filt in cards[c]["types"]]
    return r.choice(pool)


def _gen_trigger(r, card, trg: dict):
    on, filt, name = trg["on"], trg.get("filter"), card["name"]
    if on in ("opp_curse", "opp_attack"):
        pool = list(_CURSERS) if on == "opp_curse" else list(_pools()["att"])
        return _gen_victim(r, card, pool, react=False)
    spec = _new_spec(r, card)
    spec["me"]["pier"] = [name] + [b for b in spec["me"]["pier"][:1] if b != name]
    r.shuffle(spec["me"]["pier"])
    keys = list(_BASE_KEYS)
    if on == "start":
        spec["played"], spec["me"]["table"] = [], []
        spec["me"]["hand"] = _some(r, 5)
        if r.random() < 0.5:
            spec["me"]["table"] = [[r.choice(_pools()["dur"]), True]]
        spec["act"] = {"kind": "start"}
    elif on == "play":
        if filt == ATT:
            y = r.choice([a for a in _pools()["att"] if not _card_ops(_cards_by_name()[a]) & {
                "choose", "target_glory"}])
        else:
            y = _match(r, filt if r.random() < 0.8 else None, "simple_treas" if filt == TREAS else "simple_act")
        _insert(r, spec["me"]["hand"], y)
        spec["act"] = {"kind": "play", "play": y, "extra": [y]}
    elif on in ("buy", "gain"):
        y = _match(r, filt if r.random() < 0.7 else None, "kingdom")
        if on == "gain" and r.random() < 0.4:
            gname = r.choice(_GAINERS)
            _insert(r, spec["me"]["hand"], gname)
            ok = any(e["op"] == "gain" and e["max"] >= _cards_by_name()[y]["cost"] and _is_type(y, e.get("type"))
                     for e in _cards_by_name()[gname]["play"])
            spec["counters"]["cut"] = 0
            spec["act"] = {"kind": "play", "play": gname, "extra": [gname, y],
                           "force": [("gain", y)] if ok else []}
        else:
            spec["counters"]["coins"] = _cards_by_name()[y]["cost"] + r.randint(0, 2)
            spec["counters"]["cut"] = 0
            spec["act"] = {"kind": "buy", "play": y, "extra": [y]}
        keys = ["coins", "buys", "glory", "hand", "discard", "deck"]
    elif on == "trash":
        t = r.choice(_TRASHERS)
        victim = r.choice(spec["me"]["hand"])
        _insert(r, spec["me"]["hand"], t)
        spec["act"] = {"kind": "play", "play": t, "extra": [t], "force": [("trash_hand", [victim])]}
        keys.append("trash")
    elif on == "end":
        spec["counters"]["coins"] = r.randint(0, 5)
        spec["act"] = {"kind": "end"}
        keys = ["coins", "glory", "hand"]
    elif on == "shuffle":
        spec["me"]["deck"] = _some(r, r.choice([0, 1, 2]))
        spec["me"]["discard"] = _some(r, r.randint(4, 12))
        y = r.choice(_DRAWERS)
        _insert(r, spec["me"]["hand"], y)
        spec["act"] = {"kind": "play", "play": y, "extra": [y]}
        keys.append("shuffles")
    else:  # pragma: no cover
        raise AssertionError(on)
    return spec, keys


def _gen_own(r, card, key: str):
    name = card["name"]
    cost = card["cost"]
    if key == "on_trashed" and r.random() < 0.35:
        pool = [a for a in _pools()["att"] if any(
            e["op"] == "attack_reveal_trash" and e["lo"] <= cost <= e["hi"] for e in _cards_by_name()[a]["play"])]
        if pool:
            spec, keys = _gen_victim(r, card, pool, react=False)
            spec["me"]["pier"] = []
            spec["me"]["deck"].insert(0, name)
            return spec, keys + ["trash"]
    spec = _new_spec(r, card)
    if key == "on_trashed":
        t = r.choice(_TRASHERS)
        _insert(r, spec["me"]["hand"], t, name)
        spec["act"] = {"kind": "play", "play": t, "extra": [t], "force": [("trash_hand", [name])]}
        return spec, list(_BASE_KEYS) + ["trash"]
    gainers = [g for g in _GAINERS if any(e["op"] == "gain" and e["max"] >= cost and _is_type(name, e.get("type"))
                                         for e in _cards_by_name()[g]["play"])]
    if key == "on_gain" and gainers and r.random() < 0.5:
        gname = r.choice(gainers)
        _insert(r, spec["me"]["hand"], gname)
        spec["counters"]["cut"] = 0
        spec["act"] = {"kind": "play", "play": gname, "extra": [gname], "force": [("gain", name)]}
    else:
        spec["counters"]["coins"] = cost + r.randint(0, 2)
        spec["counters"]["cut"] = 0
        spec["act"] = {"kind": "buy", "play": name}
    return spec, ["coins", "buys", "glory", "hand", "deck", "discard"]


def _is_type(name: str, t) -> bool:
    return t is None or t in _cards_by_name()[name]["types"]


def _gen_name(name: str) -> str:
    if name.endswith("я"):
        return name[:-1] + "и"
    if name.endswith("а"):
        return name[:-1] + ("и" if name[-2] in "кгхжшчщ" else "ы")
    if name.endswith("й"):
        return name[:-1] + "я"
    return name + "а"


def _q(names) -> str:
    names = [f"«{n}»" for n in names]
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + " и " + names[-1]


_KEY_Q = {"coins": "монет в кошельке хода", "actions": "действий", "buys": "покупок",
          "hand": "карт у вас в руке", "glory": "у вас славы", "deck": "карт в вашей колоде",
          "discard": "карт в вашем сбросе", "trash": "карт на свалке", "curse": "Тин среди ваших карт",
          "shuffles": "раз за это время перемешивался ваш сброс", "vp": "очков победы принесут все эти карты",
          "score": "очков составит ваш итоговый счёт"}
_OPP_Q = {"hand": "карт в руке у {}", "glory": "славы у {}", "deck": "карт в колоде у {}",
          "discard": "карт в сбросе у {}", "curse": "Тин среди карт {}"}


def _key_text(key: str) -> str:
    if "@" in key:
        k, who = key.split("@")
        return _OPP_Q[k].format(_gen_name(who))
    return _KEY_Q[key]


def _dec_text(kind: str, val, names: dict[int, str]) -> str:
    if kind in ("trash_hand", "discard_draw", "discard_coins"):
        verb = {"trash_hand": "уничтожить", "discard_draw": "сбросить",
                "discard_coins": "сбросить ради монет"}[kind]
        return f"{verb} {_q(val)}" if val else f"{verb} — ничего"
    if kind == "return_curse":
        return f"вернуть в запас Тин: {len(val)}"
    if kind in ("gain", "remodel_gain"):
        return f"получить «{val}»" if val else "получать нечего"
    if kind == "remodel_trash":
        return f"уничтожить «{val}»" if val else "уничтожать нечего"
    if kind == "choose":
        return f"выбрать вариант {val}"
    if kind == "target":
        return f"выбрать целью игрока {names[val]}"
    if kind == "double":
        return f"повторить «{val}»" if val else "повторять нечего"
    if kind == "topdeck":
        return f"переложить на колоду «{val}»" if val else "ничего не перекладывать"
    if kind == "spend_glory":
        return f"потратить славы: {val}"
    raise AssertionError(kind)  # pragma: no cover


def _zone_text(st: dict, who: str) -> list[str]:
    out = []
    hand, sp = st["hand"], (" " + who if who else "")
    out.append(f"в руке{sp} (в порядке поступления): {_q(hand)}" if hand else "рука пуста")
    out.append(f"колода сверху вниз: {_q(st['deck'])}" if st["deck"] else "колода пуста")
    out.append(f"в сбросе (последняя — сверху): {_q(st['discard'])}" if st["discard"] else "сброс пуст")
    if st.get("pier"):
        out.append(f"на пристани: {_q(st['pier'])}")
    pend = [n for n, p in st.get("table", []) if p]
    if pend:
        out.append(f"на столе ждёт следующего хода контракт {_q(pend)}")
    out.append(f"слава — {st['glory']}")
    return out


def _scn_ops(spec: dict) -> set:
    act = spec["act"]
    inv = {spec["card"], act.get("play", "")} | set(act.get("extra", [])) | set(spec["me"].get("pier", []))
    ops: set = set()
    for n in inv - {""}:
        ops |= _card_ops(_cards_by_name()[n])
    return ops


def _zone_state(st: dict) -> dict:
    return {"hand": list(st["hand"]), "deck": list(st["deck"]), "discard": list(st["discard"]),
            "pier": list(st.get("pier", [])), "pending": [n for n, p in st.get("table", []) if p],
            "glory": st["glory"]}


def _scn_state(spec: dict) -> dict:
    """The circumstances a situation states (mirrors `_scn_text`)."""
    kind, c, ops = spec["act"]["kind"], spec["counters"], _scn_ops(spec)
    out: dict = {"you": _zone_state(spec["me"]), "opponents": {o["name"]: _zone_state(o) for o in spec["opps"]}}
    if spec["active"] != 0:
        out["actions"] = c["actions"]
    elif kind == "end":
        out["coins"] = c["coins"]
    elif kind != "start":
        out.update(actions=c["actions"], buys=c["buys"], coins=c["coins"], cut=c["cut"], played=list(spec["played"]))
    if spec["empty"] or "coins_per_empty" in ops:
        out["empty"] = list(spec["empty"])
    if ops & {"attack_curse", "return_curse"} or spec["curses"] != 10:
        out["curses"] = spec["curses"]
    if spec["act"].get("play"):
        out["card"] = spec["act"]["play"]
    return out


def _scn_text(spec: dict, kinds: list, keys: list[str]) -> str:
    me, opps, act = spec["me"], spec["opps"], spec["act"]
    names = {0: "вы", **{i + 1: o["name"] for i, o in enumerate(opps)}}
    c = spec["counters"]
    kind = act["kind"]
    parts = []
    if spec["active"] == 0:
        parts.append({"start": "Начинается ваш ход (карты в руку вы добрали в прошлой очистке).",
                      "buy": "Идёт ваш ход, фаза покупок.",
                      "end": "Ваш ход: розыгрыш и покупки закончены."}.get(kind, "Идёт ваш ход, фаза розыгрыша."))
        if kind == "end":
            parts.append(f"В кошельке осталось монет: {c['coins']}.")
        elif kind != "start":
            cut = f", скидка этого хода — {c['cut']}" if c["cut"] else ""
            parts.append(f"У вас действий: {c['actions']}, покупок: {c['buys']}, монет в кошельке: {c['coins']}{cut}.")
            parts.append(f"В этом ходу до сих пор разыграны: {_q(spec['played'])}." if spec["played"]
                         else "В этом ходу вы ещё ничего не разыгрывали.")
    else:
        a = opps[0]
        parts.append(f"Идёт ход {_gen_name(a['name'])}, фаза розыгрыша (действий у этого игрока: {c['actions']}); "
                     "вы — соперник.")
    parts.append("Ваше положение: " + "; ".join(_zone_text(me, "у вас")) + ".")
    for o in opps:
        parts.append(f"{o['name']}: " + "; ".join(_zone_text(o, "")) + ".")
    ops = _scn_ops(spec)
    sup = _supply_of(spec)
    if ops & {"gain", "remodel", "gain_copy_prev"}:
        extra = [n for n in sup if n not in _BASE]
        parts.append(f"Кроме базовых стопок, в запасе есть {_q(extra)}.")
    if spec["empty"]:
        parts.append(f"Пустые стопки запаса: {_q(spec['empty'])}.")
    elif "coins_per_empty" in ops:
        parts.append("Пустых стопок в запасе нет.")
    if ops & {"attack_curse", "return_curse"} or spec["curses"] != 10:
        parts.append(f"В стопке Тины карт: {spec['curses']}.")
    if "trash" in keys:
        parts.append("Свалка пока пуста.")
    who = "Вы" if spec["active"] == 0 else opps[0]["name"]
    if kind == "double":
        parts.append(f"Вы разыгрываете «{act['play']}» и выбираете для повтора «{spec['card']}».")
        kinds = kinds[1:]
    elif kind == "buy":
        parts.append(f"Вы покупаете «{act['play']}».")
    elif kind == "next":
        parts.append(f"Вы разыгрываете «{act['play']}», больше ничего не играете и не покупаете, ход "
                     "заканчивается очисткой. Соперники ходят, никак не затрагивая ваши карты.")
    elif kind == "play":
        verb = "разыгрываете" if spec["active"] == 0 else "разыгрывает"
        parts.append(f"{who} {verb} «{act['play']}».")
    if kinds:
        parts.append("Решения по ходу розыгрыша: " + "; ".join(
            ("" if p == spec["active"] else f"{names[p]} — ") + _dec_text(k, v, names) for p, k, v in kinds) + ".")
    when = {"next": "в начале вашего следующего хода, когда всё, что происходит в его начале, уже выполнено "
                    "(до розыгрыша карт)",
            "start": "когда всё, что происходит в начале хода, уже выполнено (до розыгрыша карт)",
            "end": "после свойств конца хода (до очистки)"}.get(kind, "после этого")
    return " ".join(parts) + f" Сколько {when} будет: " + "; ".join(_key_text(k) for k in keys) + "?"


def _ans_text(vals: dict[str, int]) -> str:
    out = [f"{_key_text(k)} — {v}" for k, v in vals.items()]
    return _cap("; ".join(out)) + "."


def _gen_vp(r: random.Random, card: dict) -> tuple[str, dict]:
    name, rule = card["name"], card.get("vp_rule") or {}
    cards = _card_defs()
    comp: Counter = Counter()
    comp[name] = r.randint(1, 4)
    for c in r.sample(["Грош", "Гривна", "Червонец", "Причал", "Склад", "Тина"], r.randint(2, 5)):
        comp[c] += r.randint(1, 7)
    kind = rule.get("kind")
    if kind == "per_type":
        pool = [c["name"] for c in cards if rule["type"] in c["types"] and c["name"] != name]
        for c in r.sample(pool, r.randint(1, 3)):
            comp[c] += r.randint(1, 4)
    if kind == "per_named":
        comp[rule["card"]] += r.randint(0, 9)
    for c in r.sample(_pools()["kingdom"], r.randint(1, 3)):
        comp[c] += r.randint(1, 3)
    glory = r.randint(0, 12)
    g = _engine_ns()["Game"]({"players": [{"name": _ME, "deck": []}], "supply": {}, "seed": 1,
                              "max_rounds": 1, "turns": []})
    pl = g.players[0]
    pl.glory = glory
    owned = [c for c, k in sorted(comp.items()) for _ in range(k)]
    vals = {"vp": sum(g.vp(pl, c, owned) for c in owned if c == name),
            "score": sum(g.vp(pl, c, owned) for c in owned) + glory}
    items = sorted(comp.items(), key=lambda kv: (-kv[1], kv[0]))
    r.shuffle(items)
    listing = ", ".join(f"{k} × «{c}»" for c, k in items)
    text = (f"Партия окончена. Все ваши карты во всех зонах (всего {len(owned)}): {listing}; ваша слава — "
            f"{glory}. Сколько очков победы принесут все ваши «{name}» вместе и каким будет ваш итоговый "
            "счёт?")
    return text, vals, {"cards": dict(comp), "glory": glory}


def _plan(card: dict) -> list:
    t = set(card["types"])
    items: list = []
    if card.get("play"):
        items += ["play", "play"]
    if ACT in t and not t & {DUR, BLD}:
        items.append("double")
    if card.get("next"):
        items.append("next")
    items += [("trig", i) for i in range(len(card.get("triggers", [])))]
    if card.get("react"):
        items.append("react")
    items += [("own", k) for k in ("on_buy", "on_gain", "on_trashed") if card.get(k)]
    if t & {VIC, CURSE}:
        items += ["vp", "vp", "vp"]
    while len(items) < 3:
        items.append("play" if t & {ACT, TREAS} else "vp")
    return items[:6]


def _one_situation(card: dict, item, idx: int) -> dict:
    name = card["name"]
    for attempt in range(80):
        r = random.Random(f"situation/{name}/{idx}/{attempt}")
        if item == "vp":
            text, vals, state = _gen_vp(r, card)
            return {"q": text, "a": _ans_text(vals), "vals": vals, "full": vals, "state": state}
        if item in ("play", "double"):
            spec, keys = _gen_play(r, card, item)
        elif item == "next":
            spec, keys = _gen_next(r, card)
        elif item == "react":
            spec, keys = _gen_victim(r, card, list(_pools()["att"]), react=True)
        elif item[0] == "trig":
            spec, keys = _gen_trigger(r, card, card["triggers"][item[1]])
        else:
            spec, keys = _gen_own(r, card, item[1])
        res = _scn_run(spec, r)
        if res is None:
            continue
        stable, kinds, base = res
        keys = [k for k in dict.fromkeys(keys) if k in stable]
        if base["shuffles"] and "shuffles" in stable and "shuffles" not in keys:
            keys.append("shuffles")
        if len(keys) < 3:
            continue
        vals = {k: stable[k] for k in keys}
        return {"q": _scn_text(spec, kinds, keys), "a": _ans_text(vals), "vals": vals, "full": stable,
                "state": _scn_state(spec)}
    raise AssertionError(f"no stable situation for {name} {item}")


@functools.cache
def _situations(name: str) -> tuple[dict, ...]:
    card = _cards_by_name()[name]
    return tuple(_one_situation(card, item, i) for i, item in enumerate(_plan(card)))


_TYPE_LINE = {ACT: "Действие", TREAS: "Товар", VIC: "Владение", ATT: "Интрига", REACT: "Оберег",
              DUR: "Контракт", BLD: "Постройка", CURSE: "Тина"}


def _translit(name: str) -> str:
    table = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o",
                      "p", "r", "s", "t", "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e",
                      "yu", "ya"], strict=True))
    s = "".join(table.get(ch, ch) for ch in name.lower())
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


_BODY_HEADS = ("## Текст карты", "## Разъяснения судейской коллегии", "## Официальные поправки")


def _yes_no(answer: str) -> str | None:
    m = re.match(r"(Да|Нет)\b", answer)
    return {"Да": "yes", "Нет": "no"}[m.group(1)] if m else None


@functools.cache
def _all_rulings(name: str) -> tuple[dict, ...]:
    """Bank rulings (key: yes/no or None) followed by engine-checked situations (key: values)."""
    card = _cards_by_name()[name]
    out = [{"q": q, "a": a, "key": _yes_no(a), "full": None, "state": None} for q, a in _rulings(card)]
    out += [{"q": s["q"], "a": s["a"], "key": s["vals"], "full": s["full"], "state": s["state"]}
            for s in _situations(name)]
    return tuple(out)


def _card_head(card: dict, number: int) -> list[str]:
    printed = _printed_card(card)
    edition = "2" if card["name"] in _ERRATA else "1"
    return [f"# «{card['name']}»", "",
            f"**Тип:** {' — '.join(_TYPE_LINE[t] for t in printed['types'])}  ",
            f"**Стоимость (по печати):** {printed['cost']}  ",
            f"**Номер в каталоге:** {number:03d} · **Тираж:** {edition}"]


def _body_draft(card: dict) -> str:
    lines = [_BODY_HEADS[0], ""]
    for para in _card_text(_printed_card(card)).split("\n\n"):
        lines += ["> " + para, ">"]
    lines[-1] = ""
    lines += [_BODY_HEADS[1], ""]
    for i, rl in enumerate(_all_rulings(card["name"]), 1):
        lines += [f"{i}. **В:** {rl['q']}  ", f"   **О:** {rl['a']}"]
    lines += ["", _BODY_HEADS[2], ""]
    lines += _errata_lines(card) or ["Поправок к этой карте не выпускалось."]
    return "\n".join(lines)


def _card_skeleton(text: str) -> tuple:
    """Section headers, ruling and errata numbering, errata dates and quoted card names."""
    heads = re.findall(r"(?m)^#.*$", text)
    parts = re.split(r"(?m)^## .*$", text)
    if len(parts) != 4:
        return (tuple(heads),)
    rul, err = parts[2], parts[3]
    names = tuple(sorted(n for n in _cards_by_name() if f"«{n}»" in text))
    return (tuple(h.strip() for h in heads), tuple(re.findall(r"(?m)^(\d+)\. ", rul)),
            tuple(re.findall(r"(?m)^(\d+)\. ", err)), tuple(re.findall(r"\d\d\.\d\d\.\d{4}", err)), names)


def _skel_ok(draft: str, text: str) -> bool:
    return _card_skeleton(text) == _card_skeleton(draft)


def _body_text(card: dict) -> str:
    draft = _body_draft(card)
    text = rewritten(TASK_ID, card["name"], draft).strip()
    return text if text == draft or _skel_ok(draft, text) else draft


def _card_file(card: dict, number: int) -> str:
    lines = _card_head(card, number) + ["", "## Легенда", "", _flavor(card["name"]), "", _body_text(card)]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# rules.md
# ---------------------------------------------------------------------------

_RULES_1 = """# «Туманная гавань». Правила турнирной редакции

Этот документ — полный свод правил настольной карточной игры «Туманная гавань» в редакции
турнирного комитета. Он написан для судей и для тех, кто проводит партии по записи: по
начальной раскладке и плану ходов итог партии определяется однозначно. Всё, что не сказано
на картах, определяется этими правилами; то, что сказано на карте, имеет приоритет над общими
правилами. Каждая карта описана в отдельном файле каталога `cards/`: там напечатан её текст, а
также разъяснения судейской коллегии и официальные поправки. Приложения в конце документа
описывают формат записи партии, решения игроков и формат итога; приложение Г содержит
поправки к самим правилам, и они имеют силу над основным текстом.

## 1. Обзор

В игре участвуют от двух до четырёх торговых домов гавани. Каждый игрок начинает со своей
небольшой колоды и по ходу партии покупает новые карты из общего запаса: товары приносят
монеты, действия дают разные возможности, владения приносят очки победы в конце игры. Кроме
очков на картах, игроки копят славу — жетоны, каждый из которых в конце игры тоже стоит одно
очко победы. Побеждает тот, у кого больше всего очков.

Игроки ходят по очереди, в порядке, в котором они перечислены в записи партии (игрок 0,
игрок 1 и так далее). Игрок, чей сейчас ход, называется активным. «Слева от игрока» сидит
следующий по порядку хода; за последним снова идёт игрок 0. Круг из ходов всех игроков
называется раундом; первый раунд имеет номер 1.

## 2. Карты и их типы

У каждой карты есть название, один или несколько типов и стоимость в монетах. Названия
уникальны: карты с одинаковым названием полностью одинаковы. Типы:

- **Товар** — карты, которые разыгрываются ради монет. Розыгрыш Товара не тратит действие.
- **Действие** — карты с разнообразными эффектами. Розыгрыш карты Действия тратит одно
  действие. У некоторых Действий есть дополнительный тип: **Интрига** (атакует соперников),
  **Оберег** (защищает владельца от Интриг), **Контракт** (приносит пользу и в следующем ходу),
  **Постройка** (остаётся в игре до конца партии). Такие карты — тоже карты Действия: когда
  где-либо сказано «карта Действия» или «Действие», имеются в виду все карты с типом
  Действие, включая Интриги, Обереги, Контракты и Постройки.
- **Владение** — карты, приносящие очки победы. Их нельзя разыграть.
- **Тина** — карта-помеха стоимостью 0, в конце игры отнимает одно очко победы. Её нельзя
  разыграть.

Базовые карты есть в каждой партии: Товары «Грош» (стоимость 0, +1 монета), «Гривна»
(стоимость 3, +2 монеты) и «Червонец» (стоимость 6, +3 монеты); Владения «Причал»
(стоимость 2, 1 очко), «Склад» (стоимость 5, 3 очка) и «Гавань» (стоимость 8, 6 очков); и
«Тина». Остальные карты называются картами королевства; в каждой партии используется набор
из десяти таких карт, но движок должен уметь разыгрывать любую карту каталога.

Стоимость, напечатанная на карте (с учётом официальных поправок, раздел 14), называется
базовой стоимостью. Текущая стоимость — базовая минус скидки текущего хода (раздел 8).

## 3. Зоны

Каждая карта в любой момент находится ровно в одной зоне.

- **Запас** — общие стопки карт, из которых карты покупают и получают. Для каждой стопки
  известно число карт; все карты стопки одинаковы. Стопка может опустеть; пустая стопка
  остаётся в запасе с числом 0.
- **Колода** игрока — упорядоченная стопка карт рубашкой вверх. Верхняя карта колоды — первая
  в записи.
- **Рука** игрока — упорядоченный ряд карт. Карта, пришедшая в руку (взятая, полученная в
  руку, найденная), кладётся в конец руки. Порядок руки важен: он определяет порядок срабатывания
  оберегов и выбор сбрасываемых карт при атаке, а также выводится в итоге партии.
- **Сброс** игрока — упорядоченная стопка лицом вверх. Каждая карта, попадающая в сброс,
  ложится сверху (в конец записи). Нижняя карта сброса — та, что попала туда раньше всех.
- **Стол** игрока — карты, разыгранные в этом ходу, и Контракты, ожидающие своего следующего
  хода. Порядок стола — порядок, в котором карты на него легли.
- **Пристань** игрока — его Постройки. Постройки лежат на пристани в порядке возведения (от
  самой старой к самой новой) и никогда её не покидают. Пристань — не стол.
- **Свалка** — общая стопка уничтоженных карт. Уничтоженные карты не возвращаются в игру.

Карты игрока (которыми он «владеет») — все карты в его колоде, руке, сбросе, на столе и на
пристани. Во время некоторых эффектов карты могут быть открыты и отложены в сторону; такие
карты лежат отдельно до конца эффекта и не принадлежат ни колоде, ни сбросу (см. 4.4).

## 4. Взятие карт и перемешивание

**4.1. Взятие.** «Взять карту» — переместить верхнюю карту своей колоды в конец руки. Если
эффект велит взять несколько карт, они берутся по одной. Взятие — единственный способ,
которым карты переходят из колоды в руку «взятием»; карта, положенная в руку другим способом
(получение в руку, найденная при поиске, отобранная из открытых), взятой не считается.

**4.2. Пустая колода.** Если нужно взять или открыть карту, а колода пуста, игрок перемешивает
свой сброс (4.3), и получившаяся стопка становится его колодой. Если пусты и колода, и сброс,
карта не берётся (не открывается), а оставшаяся часть такого взятия пропадает. Сброс
перемешивается только в этот момент — не заранее и не тогда, когда в колоде ещё есть карты.

**4.3. Генератор и перемешивание.** В партии есть один общий генератор псевдослучайных чисел.
Его состояние — целое число s; в начале партии s равно зерну `seed` из записи партии.
Каждый вызов генератора вычисляет новое состояние по формуле

    s = (s × 1103515245 + 12345) mod 2^31

и возвращает это новое s. Перемешивание сброса устроено так. Возьмите карты сброса списком L
в порядке снизу вверх (L[0] — нижняя карта сброса, то есть та, что попала в сброс раньше всех;
последний элемент — верхняя карта). Пусть m — число карт. Для i от m−1 вниз до 1 включительно:
вызовите генератор, j = результат mod (i+1), поменяйте местами L[i] и L[j]. Получившийся
список становится колодой, причём L[0] — её верхняя карта. Сброс после этого пуст. Если в
сбросе одна карта, генератор не вызывается, но перемешивание всё равно считается состоявшимся.
Генератор ни для чего, кроме перемешивания, не используется; его состояние общее для всех
игроков и сохраняется между перемешиваниями.

**4.4. Открытые карты.** Некоторые эффекты велят открывать верхние карты колоды. Открытая
карта снимается с колоды и лежит в стороне, пока эффект не решит её судьбу. Если во время
открывания колода опустела, перемешивается только сброс — открытые карты в перемешивание не
попадают. Когда эффект велит сбросить открытые карты, они ложатся в сброс в порядке открытия.

## 5. Подготовка к партии

Запись партии задаёт для каждого игрока имя и начальную колоду — список карт сверху вниз, —
а также содержимое запаса (число карт в каждой стопке), зерно генератора и предельное число
раундов. Начальные колоды уже упорядочены и перед партией не перемешиваются. Затем каждый
игрок, начиная с игрока 0 и далее по порядку, берёт 5 карт. У игроков нет славы, их сброс,
стол и пристань пусты, свалка пуста.

## 6. Ход игрока

Ход состоит из пяти фаз: начало хода, розыгрыш, покупки, конец хода и очистка.

**6.1. Начало хода.** У активного игрока одно действие, одна покупка и ноль монет; скидки и
особые правила прошлого хода больше не действуют. Затем по порядку:
1. исполняются все его Контракты, ожидающие этого хода, — в том порядке, в котором они лежат
   на столе (раздел 12);
2. срабатывают свойства его Построек «в начале хода» — от самой старой постройки к самой
   новой, у каждой постройки её свойства — в порядке печати.

**6.2. Розыгрыш.** Игрок разыгрывает карты из руки по одной, в порядке, записанном в плане
хода. Товары разыгрываются бесплатно; карта Действия требует одного действия и тратит его
(если действий нет, разыграть её нельзя). Владения и Тину разыграть нельзя. Товары и Действия
можно разыгрывать вперемешку в любом порядке. Если в руке несколько карт с одним названием,
разыгрывается та из них, что лежит в руке раньше (ближе к началу руки).

**6.3. Порядок розыгрыша одной карты.**
1. Карта убирается из руки; если это Действие, тратится одно действие.
2. Карта кладётся на стол (Постройка — на пристань, в её конец).
3. Розыгрыш считается состоявшимся: это событие «розыгрыш», на которое могут откликнуться
   свойства Построек (раздел 10).
4. Если карта — Интрига, срабатывают обереги соперников (раздел 11).
5. Выполняется текст карты — все его предложения по порядку записи. Если эффект требует решения
   игрока, решение берётся из плана (приложение Б).

Текст выполняется полностью, даже если в его ходе сама карта покинула стол.

**6.4. Покупки.** Затем игрок покупает карты из запаса в порядке, записанном в плане хода.
Для каждой покупки нужна свободная покупка, непустая стопка и не меньше монет, чем текущая
стоимость карты. Покупка тратит одну покупку и столько монет, сколько стоит карта. Купленная
карта кладётся в сброс покупателя (если в этом ходу не действует эффект, который велит класть
купленные карты на верх колоды). Затем разрешаются свойства «при покупке», а после них —
свойства «при получении» (раздел 10). Монеты, полученные от этих свойств, можно тратить на
следующие покупки этого же хода. После покупок карты больше не разыгрываются.

**6.5. Конец хода и очистка.** Сначала срабатывают свойства Построек активного игрока «в конце
хода» (от старой к новой). Затем очистка (ред. 2019): игрок кладёт в сброс все карты из руки в
порядке руки, после этого — все карты со стола в порядке стола, кроме Контрактов, которые
были разыграны в этом ходу и ещё ждут своего следующего хода (они остаются на столе).
Неистраченные монеты, действия и покупки сгорают. После этого игрок берёт 5 новых карт.

**6.6. Проверка конца игры** выполняется после очистки последнего игрока раунда (раздел 13).
"""

_RULES_2 = """
## 7. Ресурсы хода и слава

- **Действия** позволяют разыгрывать карты Действия. Эффект «+N действий» увеличивает их число.
- **Покупки** позволяют покупать карты. Эффект «+N покупок» увеличивает их число.
- **Монеты** (кошелёк хода) тратятся на покупки. Эффект «+N монет» добавляет монеты в кошелёк.
- **Слава** — жетоны, которые остаются у игрока до конца партии. Слава не бывает
  отрицательной: если эффект велит отнять больше, чем есть, слава становится равной нулю.

Действия, покупки и монеты существуют только в ход их владельца. Если эффект даёт монеты,
действия или покупки игроку не в его ход (например, свойство постройки срабатывает в чужой
ход), они сгорают немедленно и ни на что не влияют. Взятие карт и слава работают в любой ход.
Скидки (раздел 8) и правило «купленные карты кладите на верх колоды» тоже действуют только
в ход того, кто их получил.

## 8. Стоимость и скидки

Эффект вида «в этот ход все карты стоят на N меньше» уменьшает текущую стоимость каждой карты
на N до конца хода; стоимость не опускается ниже нуля. Скидки от нескольких эффектов
складываются. Текущая стоимость используется при покупке и везде, где эффект этого хода
сравнивает стоимость карты с числом (например, «получите карту стоимостью не больше 4» или
«получите карту, которая стоит не более чем на 2 дороже уничтоженной»). Базовая стоимость (без
скидок) используется в двух случаях: когда соперник сбрасывает карты по правилу 9.6 и когда
атака сравнивает стоимость открытой карты с диапазоном (9.7).

## 9. Операции с картами

**9.1. Получение.** «Получить карту» — взять её из соответствующей стопки запаса (стопка
уменьшается на 1) и положить в свой сброс, если эффект не указывает другое место («в руку»,
«на верх колоды»). Если стопка пуста, карта не получается и ничего не происходит. Формулировки
«заберите из запаса» и «получите из запаса» означают получение, а не взятие. Покупка тоже
является получением. После получения разрешаются свойства «при получении» (раздел 10).

**9.2. Эффект с выбором карты для получения** («получите карту стоимостью не больше N»,
«получите Товар…») требует решения: название карты из запаса. Подходящая карта — непустая
стопка нужного типа с текущей стоимостью не больше предела. Если подходящие карты есть, одну
из них нужно получить; если их нет, решение — `null`, и ничего не происходит.

**9.3. Уничтожение.** Уничтоженная карта уходит на свалку. Уничтожить можно карту из руки (если
эффект так говорит), разыгранную карту со стола («уничтожьте эту карту») или открытую карту
при атаке. После уничтожения каждой карты разрешаются свойства «при уничтожении» (раздел 10),
причём уничтожение карты — событие для её владельца, кто бы её ни уничтожил. «Отправить на
свалку» — то же, что уничтожить.

**9.4. Сброс.** Сброшенные карты ложатся в сброс по одной в указанном порядке.

**9.5. Возврат в запас.** Карта, возвращённая в запас, кладётся обратно в свою стопку (стопка
увеличивается на 1). Возврат — не уничтожение и не сброс.

**9.6. Вынужденный сброс при атаке.** Когда атака велит сопернику сбрасывать карты из руки,
пока не останется N, соперник не выбирает сам: он раз за разом сбрасывает карту с наименьшей
базовой стоимостью, а при равенстве стоимостей (ред. 2019) — ту из них, что пришла в руку
раньше (стоит ближе к началу руки). Сброшенные так карты ложатся в сброс в порядке сброса. Если
в руке N карт или меньше, ничего не происходит.

**9.7. Атака с открытием.** Когда атака велит соперникам открыть верхнюю карту колоды, каждый
пострадавший соперник по очереди открывает свою верхнюю карту (при пустой колоде — по правилу
4.2; если карт нет совсем, с ним ничего не происходит) и поступает с ней так, как сказано на
карте атаки, сравнивая с диапазоном её базовую стоимость. Границы диапазона включаются.

**9.8. «Добирайте, пока в руке не станет N»** — брать карты по одной, пока в руке меньше N карт
и взятие возможно.

**9.9. Поиск** («открывайте карты, пока не откроете Товар») — открывать карты по одной; первая
подходящая открытая карта кладётся в руку (это не взятие), поиск на ней заканчивается, а все
остальные открытые карты сбрасываются в порядке открытия. Если подходящей карты нет, открытие
продолжается, пока не опустеют и колода, и сброс; открытые карты при этом не перемешиваются.

**9.10. «Посмотрите N верхних карт»** — открыть N карт (или меньше, если карт не хватает); все
подходящие из них положить в руку в порядке открытия, остальные сбросить в порядке открытия.

## 10. Свойства и порядок их разрешения

Свойства — тексты, срабатывающие на события, а не при розыгрыше: «когда вы покупаете эту
карту», «когда вы получаете эту карту», «когда эту карту уничтожают», свойства Построек
(«всякий раз, когда…», «в начале каждого вашего хода», «в конце каждого вашего хода»), свойства
Контрактов «в начале вашего следующего хода» и свойства оберегов.

**10.1. События.** Событиями являются: розыгрыш карты, покупка карты, получение карты,
уничтожение карты. Покупка порождает два события: сначала «покупка», затем «получение»; все
свойства, откликающиеся на покупку, разрешаются раньше всех свойств, откликающихся на
получение. Свойство «когда вы получаете…» срабатывает при любом получении, в том числе при
покупке; свойство «когда вы покупаете…» — только при покупке.

**10.2. Порядок.** Когда происходит событие, откликнувшиеся на него свойства разрешаются в
таком порядке:
1. свойство самой карты, с которой случилось событие (например, «когда вы покупаете эту
   карту»);
2. свойства Построек активного игрока — от самой старой постройки к самой новой, у каждой
   постройки — в порядке печати;
3. свойства Построек остальных игроков — по порядку хода, начиная с игрока слева от
   активного, у каждого игрока — от старой постройки к новой.

**10.3. Вложенность.** Каждое свойство разрешается полностью сразу, как только до него дошла
очередь. Если во время его разрешения происходит новое событие (например, свойство велит
получить карту), свойства, откликнувшиеся на новое событие, разрешаются немедленно, до
продолжения прерванного текста.

**10.4. Кому принадлежит событие.** Свойства «когда вы покупаете/получаете/разыгрываете…» и
«всякий раз, когда уничтожается ваша карта» откликаются только на события, случившиеся с
картами их владельца. Свойства «когда соперник…» откликаются на события соперников. Постройка
не откликается на свой собственный розыгрыш, но откликается на все последующие события.

**10.5. «Впервые за ход».** Свойство, срабатывающее при первом за ход розыгрыше карты
определённого типа, срабатывает, только если текущий розыгрыш — первый розыгрыш карты этого
типа в этом ходу. Учитываются все розыгрыши хода, включая сделанные до того, как постройка
появилась на пристани, и повторные розыгрыши.

**10.6. Эффекты владельца.** Свойство выполняется от имени владельца карты или постройки: он
берёт карты, получает славу и т. д. Правило раздела 7 о сгорающих монетах действует и здесь.

## 11. Интриги и обереги

Интрига — карта Действия, текст которой содержит атаку: предложения вида «каждый соперник…»,
«соперники…», «все остальные игроки получают…». Когда разыгрывается Интрига (шаг 4 пункта
6.3), до выполнения её текста каждый соперник по порядку хода, начиная слева от активного,
проверяет свою руку: все Обереги, лежащие в этот момент в его руке, срабатывают — по порядку
руки. Оберег, в тексте которого сказано, что Интрига на владельца не действует, защищает его
от атак этого розыгрыша; остальные эффекты оберега (взять карты, получить славу) выполняются в
любом случае. Оберег остаётся в руке. Решений от соперника не требуется.

Затем выполняется текст Интриги. Каждое предложение атаки применяется к незащищённым
соперникам по порядку хода, начиная слева от активного. Части текста, не являющиеся атакой
(«+2 монеты», «возьмите 2 карты»), выполняются активным игроком как обычно. Защита действует
только на тот розыгрыш, при котором сработал оберег; при повторном розыгрыше той же Интриги
обереги проверяются заново.

## 12. Контракты и Постройки

**Контракт.** При розыгрыше выполняется его основной текст, и карта остаётся на столе. В начале
следующего хода владельца (шаг 1 пункта 6.1) выполняется его текст «в начале вашего следующего
хода». После этого Контракт лежит на столе как обычная разыгранная карта и уходит в сброс в
очистке этого хода. Исполнение контракта в начале хода — не розыгрыш: оно не тратит действие и
не считается розыгрышем ни для каких условий. Контракт нельзя повторить эффектом «разыграйте
дважды».

**Постройка.** При розыгрыше Постройка тратит действие, кладётся на пристань и выполняет свой
основной текст (если он есть). Затем её свойства работают до конца игры. Постройки не
сбрасываются, но являются картами владельца. Постройку нельзя повторить эффектом «разыграйте
дважды». Свойство «в начале каждого вашего хода» не срабатывает в тот ход, когда постройку
возвели.

## 13. Конец игры и подсчёт очков

После очистки последнего игрока в раунде проверяется конец игры. Игра заканчивается, если
выполнено хотя бы одно условие:
1. стопка «Гавань» в запасе пуста — причина `гавань`;
2. пусты хотя бы три стопки запаса (любые, включая базовые и Тину) — причина `стопки`;
3. номер завершившегося раунда равен предельному числу раундов из записи — причина `лимит`.
Если выполнено несколько условий, причиной считается первое по этому списку.

Очки игрока — сумма очков победы всех его карт (во всех зонах, включая стол и пристань) плюс
его слава. Очки карт с условием («за каждые 8 карт», «за каждую Постройку») вычисляются в конце
игры по всем картам игрока, с округлением вниз. Каждая Тина отнимает одно очко. Побеждает
игрок с наибольшим числом очков; если таких несколько (ред. 2019), победителями считаются все
они.

## 14. Поправки и разъяснения на картах

В файле каждой карты напечатан её текст в том виде, в каком он был отпечатан, а ниже —
разъяснения судейской коллегии и официальные поправки. Поправки перечислены по датам. Каждая
поправка заменяет указанный фрагмент текста или стоимость; более поздняя поправка к тому же
фрагменту заменяет более раннюю. Поправка может быть отменена: тогда восстанавливается
значение, которое действовало до отменённой поправки. Действует последнее по дате
неотменённое значение. Базовая стоимость — стоимость после поправок. Разъяснения не меняют
правил, а поясняют их применение к конкретной карте. Часть разъяснений — разборы конкретных
положений за столом с итоговыми числами; они рассчитаны по этим правилам и действующим (с учётом
поправок) текстам карт.
"""

_RULES_3 = """
## 15. Словарь формулировок

Тексты карт написаны живым языком, и один и тот же эффект на разных картах может звучать
по-разному. Ниже — значения типичных формулировок.

- «+N карт», «возьмите N карт», «доберите ещё N», «вытяните с верха колоды N карт», «возьмите в
  руку N карт из своей колоды» — взятие N карт (4.1).
- «+N действий», «получите ещё N действий», «прибавьте N к счётчику действий», «вы можете
  сыграть на N Действий больше», «у вас появляется N дополнительных действий» — +N действий.
- «+N покупок», «получите ещё N покупок», «вы сможете купить на N карт больше», «прибавьте N к
  счётчику покупок» — +N покупок.
- «+N монет», «получите N монет», «добавьте в кошелёк хода N монет», «в этот ход у вас на N
  монет больше», «кошелёк пополняется на N» — +N монет.
- «+N славы», «получите N очков славы», «положите перед собой N жетонов славы», «слава растёт
  на N», «прибавьте себе N к славе» — +N славы. «Теряет N славы», «слава уменьшается на N»,
  «отдают в общий запас по N славы» — минус N славы, не ниже нуля.
- «Получите…», «заберите из запаса…», «получите из запаса…» — получение (9.1), по умолчанию в
  сброс. «Положите её в руку», «прямо в руку» — получение в руку; «на верх своей колоды» —
  получение на верх колоды.
- «Уничтожьте», «отправьте на свалку», «избавьтесь… они уничтожаются» — уничтожение (9.3).
  «До N карт», «не больше N», «не более N», «от нуля до N» — любое число от 0 до N.
- «Каждый соперник…», «все соперники…», «соперники…», «все остальные игроки получают…» в тексте
  Интриги — атака (раздел 11). «Каждый другой игрок берёт…», «остальные игроки тоже берут…» на
  карте, которая не является Интригой, — не атака: это касается всех соперников, и обереги не
  срабатывают.
- «Если в этот ход до этой карты вы уже сыграли хотя бы N…» и подобные — условие о числе
  розыгрышей карт указанного типа в этом ходу до текущего розыгрыша; сама карта не считается,
  повторные розыгрыши считаются каждый.
- «Выберите одно», «на выбор», «выберите один из вариантов» — выбор варианта; варианты
  нумеруются с 1 в порядке печати.
- «Уничтожьте эту карту; если вы это сделали, …» — карта уничтожается со стола, и бонус
  выдаётся, только если карта действительно была на столе и была уничтожена.
- «Разыграйте её дважды» — выбранная карта убирается из руки, кладётся на стол и разыгрывается
  два раза подряд по пункту 6.3 (шаги 3–5), не тратя действий.
- Если на карте несколько предложений или частей, соединённых словами «затем», «и», запятой,
  они выполняются по порядку записи.

## 16. Дополнение «Северный рейс»

Каталог `cards/` включает карты базового набора и карты дополнения «Северный рейс». Карты
дополнения пронумерованы в том же каталоге вперемешку с остальными, подчиняются тем же
правилам и могут оказаться в одном королевстве с любыми другими картами. Новых типов и новых
зон дополнение не вводит: всё, что сказано в разделах 1–15 и в приложениях, относится к ним
в полной мере. Официальные поправки к картам дополнения выпускались в 2023 году и действуют
так же, как поправки к базовым картам (раздел 14): и их цепочки, и отмены. Ниже собраны
пояснения, которые турнирный комитет счёл нужным дать из-за новых сочетаний эффектов.

**16.1. Подсчёт карт на столе.** Эффекты вида «+1 монета за каждое Действие на вашем столе»
или «по монете за каждые два Товара, лежащие у вас на столе» считают карты, которые в момент
выполнения эффекта лежат на *вашем столе* (раздел 3). Сама разыгрываемая карта уже лежит на
столе и учитывается, если подходит по типу. Контракты, разыгранные в прошлом ходу и ещё не
ушедшие в очистку, тоже лежат на столе и учитываются. Постройки лежат не на столе, а на
пристани, и не учитываются никогда. Карта, которую разыграли дважды эффектом «разыграйте её
дважды», лежит на столе в одном экземпляре и учитывается один раз, хотя разыграна дважды.
Карты соперников не учитываются. Округление при делении — всегда вниз.

**16.2. Несколько поисков подряд.** Если на карте два поиска («открывайте карты, пока не
откроете…») один за другим, они выполняются по очереди, как два отдельных эффекта. Открытые и
не взятые в руку карты первого поиска отправляются в сброс сразу по окончании первого поиска,
до начала второго; поэтому, если во время второго поиска колода кончится, они будут
перемешаны вместе с остальным сбросом. Карты, открытые вторым поиском, ложатся в сброс по его
окончании (как в 9.9).

**16.3. Эффекты в фазе покупок.** Некоторые карты дополнения дают ресурсы в момент покупки или
получения карты — то есть уже во время фазы покупок. Монеты, полученные так («Когда вы
покупаете эту карту: +2 монеты», срабатывание постройки при покупке и т. п.), сразу
прибавляются к монетам хода и могут быть потрачены на следующие покупки этого же хода, если
остались покупки. Покупки, полученные так, тоже можно использовать сразу. Карты, взятые в руку
в фазе покупок, разыграть уже нельзя: фаза розыгрыша закончилась, и такие карты уйдут в сброс
в очистке вместе с остальной рукой.

**16.4. Взятие карт вне своего хода.** Если свойство постройки или оберег велит взять карты,
когда ход не ваш, вы берёте их в руку по обычным правилам взятия (раздел 4, в том числе с
перемешиванием сброса). Эти карты остаются у вас в руке; в начале своего хода вы не добираете
и не сбрасываете лишнее, так что ваша рука в этот ход будет больше пяти карт. Монеты вне
своего хода сгорают (раздел 7), слава не сгорает (приложение Г.4).

**16.5. Выбор из трёх вариантов.** Если на карте три варианта выбора, в плане они нумеруются
1, 2 и 3 в порядке печати (приложение Б). Вариант «ничего не делайте» тоже имеет номер.

**16.6. Повтор карты, которая уничтожает себя.** Если карту с эффектом «уничтожьте эту карту;
если вы это сделали…» разыгрывают дважды и при первом розыгрыше она уже уничтожена, при втором
розыгрыше этот эффект ничего не даёт (карты уже нет на столе), остальные эффекты второго
розыгрыша выполняются как обычно. Бонус «когда эту карту уничтожают» при этом выдаётся один
раз — при самом уничтожении.

**16.7. Скидки.** Скидки от нескольких карт складываются (раздел 8); стоимость карты не может
стать меньше нуля. Скидка действует до конца хода, в том числе на получение карт эффектами
«получите карту стоимостью не больше N».

**16.8. Тина и пустые стопки.** Если соперник должен получить Тину, а стопка Тины пуста, он её
не получает, и свойства, срабатывающие «когда соперник получает Тину», не срабатывают. Пустая
стопка Тины считается пустой стопкой запаса наравне с остальными — и для эффектов «за каждую
пустую стопку», и для конца игры (раздел 13).

**16.9. Остаток монет в конце хода.** Свойства вида «в конце вашего хода, если у вас осталось
не меньше N монет…» проверяют монеты, оставшиеся после всех покупок хода, — до очистки.
Монеты, потраченные на покупки, не считаются.

**16.10. Покупка и получение с несколькими свойствами.** Если у купленной карты есть и
свойство «когда вы покупаете эту карту», и свойство «когда вы получаете эту карту», порядок
такой (10.1–10.2): свойство покупки самой карты, затем свойства Построек, откликнувшиеся на
покупку, затем свойство получения самой карты, затем свойства Построек, откликнувшиеся на
получение. Получение карты эффектом другой карты — не покупка: свойство «когда вы покупаете»
при этом не срабатывает, а постройки, откликающиеся на покупку, молчат.

**16.11. Новые формулировки.** На картах дополнения встречаются и такие обороты: «доведите руку
до N карт» — берите по одной карте, пока в руке меньше N (если колода и сброс пусты, остановитесь);
«заберите в руку каждый открытый Товар» — все Товары среди открытых карт идут в руку, остальные
открытые карты — в сброс; «перестройте карту» — то же, что уничтожить карту из руки и получить
карту дороже не более чем на указанное число монет (9.2–9.3); «сосчитайте опустевшие стопки» —
пустые стопки запаса, включая Тину (16.8).

## 17. Дополнение «Южный ветер»

Третий набор карт, «Южный ветер», тоже лежит в каталоге `cards/` вперемешку с остальными. В
отличие от «Северного рейса», он вводит новые эффекты и одно новое событие. Новых типов карт и
новых зон нет. Всё сказанное в разделах 1–16 действует и для этих карт; ниже описано только
новое. Поправки к картам этого набора выпускались в 2024–2025 годах и, помимо чисел в тексте и
стоимости, затрагивают целые абзацы: условия срабатывания построек, свойства оберегов и очки
победы. Такая поправка приводит абзац целиком в новой редакции.

**17.1. Карта из сброса на верх колоды.** Эффект «можете положить карту из своего сброса на
верх колоды» требует решения: название карты, лежащей в вашем сбросе, или `null`, если вы
отказываетесь (при пустом сбросе решение всегда `null`). Выбранная карта перекладывается из
сброса на верх колоды; это не взятие, не получение и не сброс. Если в сбросе несколько карт с
выбранным названием, перекладывается нижняя из них — та, что попала в сброс раньше всех.

**17.2. Трата славы.** «Можете потратить до N славы: за каждое потраченное очко +K монет» —
решение: целое число от 0 до меньшего из N и вашей текущей славы. Слава уменьшается на это
число, вы получаете K монет за каждое потраченное очко.

**17.3. Сброс ради монет.** «Сбросьте из руки до N карт; за каждую +1 монета» — решение: список
названий (0…N), как у эффекта «сбросьте и возьмите столько же». Карты сбрасываются по порядку
списка, затем вы получаете по монете за каждую сброшенную. Карт вы при этом не берёте.

**17.4. Открыть и сбросить.** «Откройте N верхних карт своей колоды и сбросьте их; за каждый
Товар среди них +1 монета» — карты открываются по одной и откладываются в сторону (4.4): если
колода опустела, перемешивается сброс без уже открытых карт; если карт нет совсем, открывается
сколько есть. Когда открывание закончено, все открытые карты ложатся в сброс в порядке открытия,
и только затем вы получаете монеты за открытые карты указанного на карте типа. Это не взятие.

**17.5. Копия предыдущего розыгрыша.** «Получите карту с тем же названием, что и карта,
разыгранная вами в этом ходу непосредственно перед этой, если та стоит не больше N» — смотрите
на розыгрыш, который в этом ходу произошёл прямо перед текущим розыгрышем этой карты. Считаются
все розыгрыши — Товаров и Действий, в том числе повторные: если карту разыгрывают дважды эффектом
«разыграйте её дважды», то перед её первым розыгрышем стоит розыгрыш карты, которая велела её
повторить, а перед вторым — первый розыгрыш этой же карты. Исполнение Контракта в начале хода —
не розыгрыш. Если перед этой картой в этом ходу ничего не разыгрывалось, ничего не происходит.
Предел N сравнивается с текущей стоимостью (раздел 8). Карта получается по правилам 9.1 (в
сброс), решения не требуется; если такой стопки в запасе нет или она пуста, ничего не
происходит.

**17.6. Атака «самая дорогая карта — на верх колоды».** «Каждый соперник, у которого в руке не
меньше K карт, кладёт самую дорогую карту из руки на верх своей колоды» — соперник не выбирает:
это карта с наибольшей базовой стоимостью, а при равенстве — та из них, что ближе к началу руки.
Если в руке меньше K карт, с соперником ничего не происходит. Это не сброс.

**17.7. Атака с получением на верх колоды.** «Каждый соперник получает «Грош» на верх своей
колоды» — это получение (9.1) соперником: стопка запаса уменьшается, карта ложится на верх его
колоды, после чего разрешаются свойства «при получении» — и они принадлежат сопернику (например,
его постройка, откликающаяся на получение Товара). Соперники получают по очереди, начиная слева от
активного; когда стопка опустеет, остальные ничего не получают.

**17.8. Карты за славу.** «Возьмите по карте за каждые N славы, но не больше M карт» — число карт
равно меньшему из M и (ваша слава ÷ N с округлением вниз); слава считается в момент выполнения
этой части текста и не тратится.

**17.9. Монеты за карты в руке.** «+1 монета за каждые N карт у вас в руке» — считаются карты,
которые лежат в руке в момент выполнения этой части текста; разыгрываемая карта уже не в руке.
Если такой текст стоит в части «в начале вашего следующего хода» Контракта, считается рука в
начале хода — после исполнения контрактов, стоящих на столе раньше этого.

**17.10. Событие «перемешивание».** Перемешивание сброса (4.3) — новое событие. Оно принадлежит
игроку, чей сброс перемешан, и на него откликаются только его собственные Постройки со свойством
«всякий раз, когда вы перемешиваете свой сброс…» — от старой к новой. Свойство разрешается сразу
после перемешивания, до того как будет взята или открыта карта, ради которой перемешивали.
Условие «сброс, в котором не меньше N карт» проверяет число перемешанных карт. Свойство работает
в любом ходу: слава не сгорает никогда, а монеты вне своего хода сгорают (раздел 7). Если
перемешивание случилось в очистке своего хода, монеты от свойства добавляются в кошелёк хода и
учитываются в `coins_total`, хотя потратить их уже нельзя.

**17.11. Стопки королевства.** Стопка королевства — стопка запаса с картой королевства, то есть
любая стопка, кроме семи базовых (раздел 2). Эффекты «за каждую опустевшую стопку королевства»
не считают пустые базовые стопки, в том числе Тину; эффекты «за каждую пустую стопку запаса»
считают все стопки (16.8).

**17.12. Постройки на пристани.** «+1 монета за каждую вашу Постройку на пристани» считает вашу
пристань, включая Постройку, возведённую в этом же ходу раньше. Стол не учитывается.

**17.13. Переплавка Товара.** «Уничтожьте Товар из руки и получите Товар дороже не более чем на
N прямо в руку» — перестройка (9.2–9.3) с двумя ограничениями: уничтожить можно только Товар, и
получить можно только Товар; полученная карта идёт в руку. Решений два, как у перестройки;
первое — `null` только если в руке нет ни одного Товара.

**17.14. Новые правила подсчёта очков.** «1 очко победы за каждые N разных названий среди ваших
карт» — считается число различных названий среди всех ваших карт (во всех зонах), включая базовые
карты и Тину. «1 очко за каждые N карт «X»» — считается число ваших карт с этим названием.
Округление всегда вниз.

## Приложение А. Запись партии (вход `simulate`)

Партия задаётся словарём (JSON-объектом):

```json
{
  "players": [{"name": "Вера", "deck": ["Грош", "Причал", "..."]}, {"name": "Остап", "deck": ["..."]}],
  "supply": {"Грош": 40, "Гривна": 30, "Червонец": 20, "Причал": 8, "Склад": 8, "Гавань": 8,
             "Тина": 10, "Лоцман": 10, "...": 10},
  "seed": 1234567,
  "max_rounds": 15,
  "turns": [
    {"plays": [{"card": "Лоцман"}, {"card": "Перекупщик", "choices": ["Грош", "Гривна"]},
               {"card": "Грош"}], "buys": ["Гривна"]},
    {"plays": [], "buys": []}
  ]
}
```

- `players` — игроки в порядке хода; `deck` — начальная колода сверху вниз.
- `supply` — все стопки запаса этой партии и число карт в каждой. Других стопок в партии нет.
- `seed` — начальное состояние генератора (4.3); `max_rounds` — предельное число раундов.
- `turns` — план всех ходов партии подряд: первый элемент — ход игрока 0 в раунде 1, затем ход
  игрока 1 и так далее; после последнего игрока — снова игрок 0 следующего раунда. План
  содержит ровно столько ходов, сколько длится партия.
- В каждом ходе `plays` — карты, которые игрок разыгрывает, в порядке розыгрыша, с решениями
  (приложение Б; ключ `choices` может отсутствовать, это то же, что пустой список), а `buys` —
  названия покупаемых карт в порядке покупок. Когда список `plays` исчерпан, фаза розыгрыша
  заканчивается; когда исчерпан `buys` — фаза покупок.

План всегда допустим при правильном применении правил. Если движок обнаруживает, что план
требует невозможного (карты нет в руке, не хватает монет или действий, решение не подходит),
это означает ошибку в движке; разумно в таком случае выбросить `ValueError`.

## Приложение Б. Решения в плане

Решения розыгрыша записываются в `choices` одним списком в том порядке, в каком их требует
выполнение текста карты (включая вложенные тексты: сначала номер варианта, затем решения
выбранного варианта; для «разыграйте дважды» — название карты, затем решения её первого
розыгрыша, затем второго). Решения нужны только для эффектов из этого списка:

| Эффект | Значение решения |
|---|---|
| уничтожить до N карт из руки | список названий (0…N элементов); карты убираются из руки и уничтожаются по порядку списка, для каждого названия — первая такая карта в руке |
| сбросить до N карт и взять столько же | список названий (0…N); сначала все сбрасываются по порядку списка, затем берётся столько карт, сколько сброшено |
| вернуть в запас до N Тин | список из 0…N строк `"Тина"` |
| получить карту по стоимости (9.2) | название карты или `null`, если подходящих нет |
| уничтожить карту из руки и получить карту дороже не более чем на N | два решения: название уничтожаемой карты из руки (`null` только при пустой руке), затем название получаемой карты (`null`, если ничего не уничтожено или подходящих нет); полученная карта идёт в сброс |
| выбрать вариант | целое число, номер варианта с 1 |
| выбрать соперника | номер игрока (с 0) |
| разыграть Действие дважды | название карты из руки или `null`, если подходящих нет |
| положить карту из сброса на верх колоды (17.1) | название карты из сброса или `null` (отказ или пустой сброс) |
| потратить до N славы (17.2) | целое число от 0 до min(N, слава) |
| сбросить до N карт ради монет (17.3) | список названий (0…N) |
| уничтожить Товар и получить Товар (17.13) | два решения, как у перестройки |

Свойства, обереги, атаки и контракты решений не требуют. Сколько решений в `choices`, столько
и должно быть использовано при розыгрыше.

## Приложение В. Итог партии (выход `simulate`)

```json
{
  "rounds": 14,
  "end_reason": "лимит",
  "winners": [1],
  "players": [
    {"name": "Вера", "score": 17, "glory": 3, "cards": {"Грош": 7, "Причал": 3, "Лоцман": 2},
     "hand": ["Грош", "Лоцман", "Причал", "Грош", "Грош"], "coins_total": 88, "draws": 131,
     "shuffles": 12}
  ],
  "supply": {"Грош": 40, "Гривна": 21, "...": 0},
  "trash": {"Грош": 5}
}
```

- `rounds` — число сыгранных раундов; `end_reason` — `гавань`, `стопки` или `лимит` (раздел 13).
- `winners` — номера победителей по возрастанию.
- `players` — в порядке хода: `score` — очки (раздел 13), `glory` — слава в конце, `cards` — все
  карты игрока во всех зонах по названиям (только ненулевые количества), `hand` — рука после
  последней очистки в её порядке, `coins_total` — сумма всех монет, добавленных в его кошелёк за
  партию в его собственные ходы (траты не вычитаются, сгоревшие вне хода не считаются),
  `draws` — сколько карт он взял за партию (4.1; включая начальные 5 карт и добор при очистке),
  `shuffles` — сколько раз перемешивался его сброс.
- `supply` — все стопки запаса с остатками, включая нулевые; `trash` — содержимое свалки по
  названиям (только ненулевые).

## Приложение Г. Поправки турнирного комитета к правилам

Следующие поправки приняты после выхода основного текста и имеют силу над ним. Пункты основного
текста, помеченные «(ред. 2019)», в соответствующей части не действуют.

**Г.1. К пункту 6.5 (порядок очистки).** В очистке игрок сначала кладёт в сброс карты со стола
(в порядке стола, кроме Контрактов, ожидающих следующего хода), и только затем — карты из руки
в порядке руки. Прежний порядок «сначала рука, затем стол» отменён.

**Г.2. К пункту 9.6 (вынужденный сброс).** При равенстве базовых стоимостей соперник сбрасывает
ту из карт, что пришла в руку позже (стоит ближе к концу руки), а не раньше.

**Г.3. К разделу 13 (победители).** Если наибольшее число очков у нескольких игроков, из них
побеждает тот, у кого больше славы; если равны и очки, и слава, победителями считаются все
такие игроки.

**Г.4. Уточнение к разделу 7.** Слава, полученная вне своего хода (например, от оберега или
свойства постройки), не сгорает — сгорают только монеты, действия и покупки.

**Г.5. К пункту 17.1 (какая копия перекладывается).** Если в сбросе несколько карт с выбранным
названием, на верх колоды перекладывается верхняя из них — та, что попала в сброс позже всех, а
не нижняя. Остальные карты сброса сохраняют свой порядок.

**Г.6. К пункту 17.10 (перемешивание в очистке).** Свойства, откликающиеся на перемешивание, не
срабатывают, если сброс перемешан во время очистки (в том числе при доборе пяти карт в её конце).
Перемешивания в любой другой момент — при взятии или открытии карт в своём или чужом ходу —
вызывают эти свойства как прежде. Последнее предложение пункта 17.10 утрачивает силу.
"""


def _rules_md() -> str:
    return _RULES_1 + _RULES_2 + _RULES_3


# ---------------------------------------------------------------------------
# LLM rewriting of card bodies (scripts/rewrite_long_texts.py): item = card.
# Ground truth = the printed and the effective (post-errata) card in the module's
# effect representation plus the key of every ruling (yes/no for bank rulings,
# engine values for situations); an independent model given rules.md must
# recover all of it from the rewritten body.
# ---------------------------------------------------------------------------

_DEFAULTS = {"dest": "discard", "where": "table", "scope": "all", "first": False, "min": 0}


def _norm_effs(effs) -> list:
    out = []
    for e in effs or []:
        if not isinstance(e, dict):
            return [repr(e)]
        d = {}
        for k, v in e.items():
            if v is None or (k in _DEFAULTS and v == _DEFAULTS[k]):
                continue
            if k in ("then",):
                v = _norm_effs(v)
            elif k == "options":
                v = [_norm_effs(o) for o in v] if isinstance(v, list) else repr(v)
            elif isinstance(v, float) and v.is_integer():
                v = int(v)
            d[k] = v
        out.append(d)
    return out


def _norm_card(c) -> dict | None:
    if not isinstance(c, dict):
        return None
    out: dict = {"cost": c.get("cost"), "types": sorted(c.get("types") or [])}
    for key in ("play", "next", "on_buy", "on_gain", "on_trashed"):
        if c.get(key):
            out[key] = _norm_effs(c[key])
    trg = []
    for t in c.get("triggers") or []:
        if not isinstance(t, dict):
            return None
        d = {k: v for k, v in t.items() if k != "effects" and v is not None and _DEFAULTS.get(k, object()) != v}
        d["effects"] = _norm_effs(t.get("effects"))
        trg.append(d)
    if trg:
        out["triggers"] = trg
    rc = c.get("react")
    if isinstance(rc, dict):
        out["react"] = {"protect": bool(rc.get("protect")), "effects": _norm_effs(rc.get("effects"))}
    if c.get("vp"):
        out["vp"] = c["vp"]
    if isinstance(c.get("vp_rule"), dict):
        out["vp_rule"] = {k: v for k, v in c["vp_rule"].items() if v is not None}
    return json.loads(json.dumps(out, ensure_ascii=False))


def _card_json(c: dict) -> dict:
    return {k: v for k, v in c.items() if k != "name"}


def _errata_brief(card: dict) -> str:
    steps = _ERRATA.get(card["name"])
    if not steps:
        return "Поправок нет; действующий текст совпадает с напечатанным."
    live = [i for i, st in enumerate(steps) if st[1] != "cancel"]
    for st in steps:
        if st[1] == "cancel":
            live.remove(st[2])
    out = []
    for i, (date, path, val) in enumerate(steps):
        if path == "cancel":
            out.append(f"№{i + 1} ({date}) отменяет поправку №{val + 1}")
        else:
            state = "действует" if live and i == live[-1] else "не действует (заменена или отменена)"
            out.append(f"№{i + 1} ({date}) меняет {'/'.join(map(str, path))} на {val!r} — {state}")
    return "Поправки: " + "; ".join(out) + "."


def _rw_items() -> list[dict]:
    items = []
    for card in _card_defs():
        name = card["name"]
        rulings = _all_rulings(name)
        lines = []
        for i, rl in enumerate(rulings, 1):
            if isinstance(rl["key"], dict):
                vals = ", ".join(f"{_key_text(k)} = {v}" for k, v in rl["key"].items())
                lines.append(f"{i}. разбор ситуации; в вопросе сохранить все обстоятельства (карты, их порядок, "
                             f"числа, решения), в ответе — ровно эти значения: {vals}")
            elif rl["key"]:
                lines.append(f"{i}. ответ по существу — «{'да' if rl['key'] == 'yes' else 'нет'}»")
            else:
                lines.append(f"{i}. общее пояснение; смысл ответа сохранить")
        head = "\n".join(_card_head(card, _card_numbers()[name]))
        brief = (
            f"Карта «{name}».\n{head}\n\n"
            f"Действующий смысл карты (после поправок): {_card_text(card)}\n"
            f"Напечатанный смысл (до поправок): {_card_text(_printed_card(card))}\n"
            f"{_errata_brief(card)}\n"
            "Разъяснения по номерам:\n" + "\n".join(lines)
        )
        items.append({"key": name, "draft": _body_draft(card), "brief": brief, "head": head,
                      "printed": _norm_card(_card_json(_printed_card(card))),
                      "effective": _norm_card(_card_json(card)),
                      "rulings": [(rl["key"], rl["full"], rl["state"]) for rl in rulings]})
    return items


_WRITER_SYSTEM = (
    "Ты редактор каталога карт настольной игры «Туманная гавань». Тебе дают тело файла одной карты: "
    "напечатанный текст карты, разъяснения судейской коллегии (вопрос — ответ) и официальные поправки. "
    "Черновик собран из шаблонов; перепиши его живым языком турнирного судьи и редактора: свои слова, свой "
    "порядок изложения внутри пункта, разная длина фраз, без канцелярита и опечаток, не повторяй обороты "
    "черновика дословно. Разборы ситуаций пиши как рассказ о положении за столом (у кого что в руке, в колоде, "
    "на пристани), а не как анкету; ответ — связной фразой. Длина — от 1,0 до 1,4 длины черновика.\n\n"
    "Жёсткие требования:\n"
    "- три заголовка «## Текст карты», «## Разъяснения судейской коллегии», «## Официальные поправки» "
    "оставь дословно и в том же порядке; других строк, начинающихся с «#», не добавляй;\n"
    "- разъяснения и поправки остаются пронумерованными пунктами «N. …» с теми же номерами в начале строки, "
    "по одному пункту на номер; внутри пункта можно писать несколько строк, но без новых номеров в начале "
    "строк; даты поправок (ДД.ММ.ГГГГ) сохрани;\n"
    "- названия карт пиши в «ёлочках» точно как в черновике, в именительном падеже (например, «сыграл карту "
    "«Грош»»); не добавляй названий карт, которых нет в черновике, и не убирай упомянутые;\n"
    "- текст карты сохраняет напечатанный (до поправок) смысл: те же эффекты, числа, условия и порядок; "
    "поправки по-прежнему указывают, какой фрагмент и на что меняется, и какая поправка какую отменяет; "
    "цитату из текста карты в поправке перефразируй так же, как перефразировал сам текст;\n"
    "- в разборе ситуации сохрани все обстоятельства: каждую карту в каждой зоне и порядок карт там, где он "
    "указан, все числа, решения игроков и что именно спрашивается; в ответе — ровно те же значения, "
    "ничего не добавляй и не пересчитывай;\n"
    "- ответы «да»/«нет» остаются ответами того же смысла; не добавляй новых правил, чисел и исключений.\n\n"
    "Правила игры (для понимания):\n\n"
)

_JUDGE_SCHEMA = """\
Прочитай файл одной карты (шапка + тело) и ответь только JSON-объектом:
{"printed": <карта по напечатанному тексту, до поправок>, "effective": <карта после всех поправок>,
 "rulings": {"<номер разъяснения>": <ответ>}}

Карта: {"cost": стоимость, "types": [типы по-русски], "play": [эффекты розыгрыша], "next": [эффекты в начале \
следующего хода (Контракт)], "triggers": [{"on": событие, "filter": тип или null, "first": true если только \
первый такой розыгрыш за ход, "min": минимум перемешанных карт, "effects": [...]}], "react": {"protect": \
защищает ли от Интриги, "effects": [...]}, "on_buy": [...], "on_gain": [...], "on_trashed": [...], "vp": очки, \
"vp_rule": {...}}. Пустые поля не пиши. События on: start (начало вашего хода), end (конец хода, после \
покупок), play (вы разыгрываете карту типа filter), buy, gain, trash (ваша карта типа filter), opp_curse \
(соперник получил Тину), opp_attack (соперник разыграл Интригу), shuffle (ваш сброс перемешан).
vp_rule: {"kind": "per_cards", "per", "each"} — each очков за каждые per ваших карт; {"kind": "per_type", \
"type", "per", "each"}; {"kind": "per_glory", "per", "each"}; {"kind": "per_distinct", "per", "each"} — за \
каждые per разных названий; {"kind": "per_named", "card", "per", "each"}.
Эффекты по порядку текста; T — тип по-русски («Действие», «Товар», «Владение», «Интрига», «Постройка», \
«Контракт», «Оберег») или null:
draw{n} взять; actions{n}; buys{n}; coins{n}; glory{n}; trash_hand{n} уничтожить до n карт из руки; \
discard_draw{n} сбросить до n и взять столько же; gain{max, dest: discard|hand|top, type: T} получить карту \
стоимостью до max; remodel{plus, ttype, gtype, dest} уничтожить карту из руки и получить дороже не более \
чем на plus; choose{options: [[эффекты], ...]}; if_played{type, k, then} если до этой карты в этом ходу \
разыграно не меньше k карт типа; coins_per{type, per, where: table|pier} монета за каждые per карт типа на \
столе/пристани; coins_per_empty{each, scope: all|kingdom}; dig{type} открывать до первой карты типа; \
look_take{n, type}; draw_to{n}; cost_cut{n}; buy_to_top; others_draw{n}; target_glory{n, self_n}; double; \
return_curse{n}; attack_discard_to{n}; attack_curse; attack_glory{n}; attack_reveal_trash{lo, hi}; \
gain_named{card, dest}; trash_self_then{then}; if_coins_left{k, then}; topdeck_from_discard; \
spend_glory{n, each}; discard_for_coins{n}; mill_count{n, type}; gain_copy_prev{max}; attack_topdeck{k}; \
attack_gain_top{card}; draw_per_glory{per, cap}; coins_per_hand{per}. Эффект — объект {"op": ..., параметры}.

rulings — по каждому пронумерованному разъяснению: "yes" или "no", если ответ по существу «да» или «нет»; \
"other" для прочих общих ответов; для разбора ситуации — объект {"state": обстоятельства, "answer": числа ответа}.
state: {"you": {"hand": [карты в руке в указанном порядке], "deck": [колода сверху вниз], "discard": [сброс в \
порядке поступления, верхняя — последней], "pier": [постройки на пристани], "pending": [контракты на столе, \
ждущие следующего хода], "glory": слава}, "opponents": {"Имя": {те же поля}}, "actions", "buys", "coins", "cut" \
(скидка хода, 0 если не названа), "played": [карты, уже разыгранные в этом ходу, по порядку], "empty": [пустые \
стопки запаса, если о них сказано], "curses": число карт в стопке Тины (если названо), "card": карта, которую в \
ситуации разыгрывают или покупают (при повторе — карта, которая повторяет)}. Названия — точно как в «ёлочках», \
по элементу на каждую карту. Чего в ситуации нет, не указывай. Для подсчёта очков в конце партии state = \
{"cards": {"название": количество}, "glory": слава}.
answer — числа, названные в ответе, по ключам: coins (монет в кошельке хода), actions, buys, hand (карт в руке), glory (слава), deck (карт в колоде), \
discard (карт в сбросе), trash (карт на свалке), curse (Тин среди карт), shuffles (сколько раз перемешан \
сброс), vp (очков от этих карт), score (итоговый счёт); для соперника — ключ с его именем: hand@Имя, \
glory@Имя, deck@Имя, discard@Имя, curse@Имя. Без ключей «вы»: числа без имени относятся к игроку, о котором \
спрашивают как о «вас»."""


def _writer_system() -> str:
    return _WRITER_SYSTEM + _rules_md()


def _judge_messages(item: dict, text: str) -> list[dict]:
    item["_skel_ok"] = _skel_ok(item["draft"], text)
    system = "Правила игры «Туманная гавань»:\n\n" + _rules_md() + "\n\n" + _JUDGE_SCHEMA
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"{item['head']}\n\n{text}"}]


def _yn(ans) -> str | None:
    return {"yes": "yes", "да": "yes", "no": "no", "нет": "no"}.get(str(ans).strip().casefold())


def _int(v) -> int | None:
    try:
        f = float(str(v).replace(",", "."))
    except ValueError:
        return None
    return int(f) if f.is_integer() else None


def _nm(x) -> str:
    t = str(x).strip()
    if t not in _cards_by_name() and t.startswith("«") and t.endswith("»"):
        t = t[1:-1].strip()
    return t


def _zones_ok(want: dict, got) -> bool:
    if not isinstance(got, dict):
        return False
    for z in ("hand", "deck", "discard", "pier", "pending"):
        g = got.get(z) or []
        if not isinstance(g, list) or [_nm(x) for x in g] != want[z]:
            return False
    return _int(got.get("glory")) == want["glory"]


def _state_ok(want: dict, got) -> bool:
    if not isinstance(got, dict):
        return False
    for k, v in want.items():
        g = got.get(k)
        if k == "you":
            ok = _zones_ok(v, g)
        elif k == "opponents":
            g = g or {}
            ok = isinstance(g, dict) and set(map(_nm, g)) == set(v) and all(
                _zones_ok(v[_nm(n)], z) for n, z in g.items())
        elif k == "cards":
            ok = isinstance(g, dict) and {_nm(a): _int(b) for a, b in g.items()} == v
        elif k in ("empty", "played"):
            ok = isinstance(g, list) and (sorted(map(_nm, g)) == sorted(v) if k == "empty" else list(map(_nm, g)) == v)
        elif k == "card":
            ok = _nm(g) == v
        else:
            ok = _int(0 if g is None and k == "cut" else g) == v
        if not ok:
            return False
    return True


def _judge_accept(item: dict, reply: str) -> tuple[bool, str]:
    if not item.get("_skel_ok", True):
        return False, ("нарушена форма: заголовки разделов, номера пунктов, даты поправок и названия карт в «» "
                       "должны совпадать с черновиком")
    match = re.search(r"\{.*\}", reply, re.S)
    try:
        got = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        got = None
    if not isinstance(got, dict):
        return False, "независимый читатель не смог разобрать карту"
    bad = []
    for part in ("printed", "effective"):
        if _norm_card(got.get(part)) != item[part]:
            bad.append("напечатанный текст карты" if part == "printed" else "действие поправок")
    rulings = got.get("rulings") if isinstance(got.get("rulings"), dict) else {}
    for i, (key, full, state) in enumerate(item["rulings"], 1):
        got_i = rulings.get(str(i))
        if isinstance(key, str) and _yn(got_i) != key:
            bad.append(f"ответ разъяснения {i}")
        elif isinstance(key, dict):
            ans = got_i.get("answer") if isinstance(got_i, dict) else None
            if not isinstance(ans, dict) or any(_int(ans.get(k)) != v for k, v in key.items()) or any(
                    k in full and _int(v) != full[k] for k, v in ans.items()):
                bad.append(f"числа в ответе разъяснения {i}")
            if not _state_ok(state, got_i.get("state") if isinstance(got_i, dict) else None):
                bad.append(f"обстоятельства ситуации в разъяснении {i}")
    if not bad:
        return True, ""
    return False, ("независимый читатель по правилам понял иначе: " + "; ".join(bad[:8])
                   + ". Сделай эти места однозначными, ничего не добавляя.")


REWRITE = {
    "items": _rw_items,
    "writer_system": _writer_system(),
    "judge_messages": _judge_messages,
    "judge_accept": _judge_accept,
}


# ---------------------------------------------------------------------------
# Workspace, check, gold, near misses
# ---------------------------------------------------------------------------

def _dump_game(g: dict) -> str:
    js = functools.partial(json.dumps, ensure_ascii=False)
    lines = ["{", '  "players": [']
    lines.append(",\n".join(f"    {js(p)}" for p in g["players"]))
    lines += ["  ],", f'  "supply": {js(g["supply"])},', f'  "seed": {g["seed"]},',
              f'  "max_rounds": {g["max_rounds"]},', '  "turns": [']
    lines.append(",\n".join(f"    {js(t)}" for t in g["turns"]))
    lines += ["  ]", "}"]
    return "\n".join(lines) + "\n"


@functools.cache
def _card_numbers() -> dict[str, int]:
    names = [c["name"] for c in _card_defs()]
    r = random.Random("card-numbers")
    r.shuffle(names)
    return {n: i + 1 for i, n in enumerate(names)}


def _setup(ws: Path) -> None:
    b = _build()
    write(ws, "rules.md", _rules_md())
    nums = _card_numbers()
    for card in _card_defs():
        n = nums[card["name"]]
        write(ws, f"cards/{n:03d}_{_translit(card['name'])}.md", _card_file(card, n))
    for i, (game, (res, _log)) in enumerate(zip(b["examples"], b["examples_out"], strict=True), 1):
        write(ws, f"examples/game_{i:02d}.json", _dump_game(game))
        write_json(ws, f"examples/game_{i:02d}.expected.json", res)


def _gold(ws: Path) -> None:
    write(ws, "engine.py", _engine_source())


_DRIVER = r'''
import json, signal, sys
games = json.loads(sys.stdin.read())
out = []
try:
    import engine
except BaseException as exc:  # noqa: BLE001
    print("@@RESULT@@" + json.dumps({"import_error": f"{type(exc).__name__}: {exc}"[:300]}))
    sys.exit(0)
def _alarm(*_):
    raise TimeoutError("timeout")
if hasattr(signal, "SIGALRM"):
    signal.signal(signal.SIGALRM, _alarm)
for g in games:
    if hasattr(signal, "SIGALRM"):
        signal.alarm(10)
    try:
        res = engine.simulate(g)
        out.append({"ok": json.loads(json.dumps(res, ensure_ascii=False))})
    except BaseException as exc:  # noqa: BLE001
        out.append({"err": f"{type(exc).__name__}: {exc}"[:200]})
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
sys.stdout.write("@@RESULT@@" + json.dumps(out, ensure_ascii=False))
'''

_PLAYER_KEYS = ("name", "score", "glory", "cards", "hand", "coins_total", "draws", "shuffles")


def _norm_result(res) -> dict | None:
    if not isinstance(res, dict):
        return None
    try:
        players = [
            {k: ({c: v for c, v in p[k].items() if v} if k == "cards" else p[k]) for k in _PLAYER_KEYS}
            for p in res["players"]
        ]
        return {
            "rounds": res["rounds"], "end_reason": res["end_reason"],
            "winners": sorted(res["winners"]), "players": players,
            "supply": dict(res["supply"]),
            "trash": {c: v for c, v in res["trash"].items() if v},
        }
    except (KeyError, TypeError, AttributeError):
        return None


def _diff_fields(exp: dict, got: dict | None) -> str:
    if got is None:
        return "неверная структура итога"
    bad = [k for k in ("rounds", "end_reason", "winners", "supply", "trash") if exp[k] != got[k]]
    if len(exp["players"]) != len(got["players"]):
        bad.append("players")
    else:
        for i, (a, b) in enumerate(zip(exp["players"], got["players"], strict=True)):
            bad += [f"players[{i}].{k}" for k in _PLAYER_KEYS if a[k] != b[k]]
    return ", ".join(bad[:4])


def _check(ws: Path) -> str:
    assert (ws / "engine.py").is_file(), "нет файла engine.py"
    b = _build()
    games = [copy.deepcopy(g) for g in b["hidden"]]
    import tempfile
    with tempfile.TemporaryDirectory(prefix="hb_t07_") as tmp:
        driver = Path(tmp) / "driver.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        proc = run_python(ws, [str(driver)], stdin=json.dumps(games, ensure_ascii=False), timeout=110)
    out = proc.stdout
    assert "@@RESULT@@" in out, "engine.py: проверка не выполнилась: " + " | ".join(
        (proc.stderr or "").strip().splitlines()[-3:])
    payload = json.loads(out.split("@@RESULT@@", 1)[1])
    assert not (isinstance(payload, dict) and "import_error" in payload), (
        f"engine.py не импортируется: {payload.get('import_error') if isinstance(payload, dict) else ''}")
    assert isinstance(payload, list) and len(payload) == len(games), "неверный ответ драйвера"
    ok = 0
    errors = []
    for i, (item, exp) in enumerate(zip(payload, b["hidden_out"], strict=True), 1):
        want = _norm_result(exp)
        if "err" in item:
            errors.append(f"партия {i}: исключение {item['err'][:80]}")
            continue
        got = _norm_result(item["ok"])
        if got == want:
            ok += 1
        else:
            errors.append(f"партия {i}: расходится {_diff_fields(want, got)}")
    total = len(games)
    note = f"партии: верно {ok}/{total}"
    if ok < MIN_OK:
        raise AssertionError(f"{note}, нужно не меньше {MIN_OK}. Примеры: " + "; ".join(errors[:5]))
    return note


def _nm_engine(variant=(), printed=False):
    def miss(ws: Path) -> None:
        write(ws, "engine.py", _engine_source(variant, printed=printed))
    return miss


def nm_ignores_errata(ws: Path) -> None:
    """Engine built from printed card texts, ignoring official errata."""
    _nm_engine(printed=True)(ws)


def nm_cleanup_hand_first(ws: Path) -> None:
    """Cleanup discards hand before the table (rules 6.5 text, ignoring appendix Г.1)."""
    _nm_engine(("cleanup_hand_first",))(ws)


def nm_buy_is_not_gain(ws: Path) -> None:
    """Purchases do not fire 'when you gain' properties."""
    _nm_engine(("buy_is_not_gain",))(ws)


def nm_victim_discards_earliest(ws: Path) -> None:
    """Forced discard tie broken by the earliest card in hand (rules 9.6 text, ignoring Г.2)."""
    _nm_engine(("victim_first",))(ws)


def nm_revealed_cards_reshuffled(ws: Path) -> None:
    """Cards revealed by a search go straight to the discard and get reshuffled."""
    _nm_engine(("reveal_to_discard",))(ws)


def nm_topdeck_lowest_copy(ws: Path) -> None:
    """Card put back from the discard is the bottom copy (rules 17.1 text, ignoring appendix Г.5)."""
    _nm_engine(("discard_lowest",))(ws)


def nm_shuffle_fires_in_cleanup(ws: Path) -> None:
    """Shuffle properties also fire during cleanup (rules 17.10 text, ignoring appendix Г.6)."""
    _nm_engine(("shuffle_in_cleanup",))(ws)


NEAR_MISSES = [nm_ignores_errata, nm_cleanup_hand_first, nm_buy_is_not_gain,
               nm_victim_discards_earliest, nm_revealed_cards_reshuffled, nm_topdeck_lowest_copy,
               nm_shuffle_fires_in_cleanup]

_PROMPT = """\
В рабочем каталоге — правила вымышленной карточной игры «Туманная гавань» (`rules.md`) и все её
карты (`cards/`, по файлу на карту: напечатанный текст, художественный текст, разъяснения
судейской коллегии и официальные поправки). Тексты карт написаны свободным языком, одинаковые
эффекты на разных картах сформулированы по-разному; поправки и разъяснения имеют силу, а
приложения к правилам (включая поправки к самим правилам) — тоже. Материала много, и значение
имеет каждая карта и каждый раздел правил.

Задача: написать на Python (только стандартная библиотека) модуль `engine.py` в корне рабочего
каталога с функцией `simulate(game: dict) -> dict`. Она проигрывает партию по записи —
начальные колоды, запас, зерно генератора, предельное число раундов и план ходов с решениями
игроков — и возвращает итог партии. Форматы входа и выхода, генератор и перемешивание, порядок
разрешения эффектов описаны в `rules.md` (разделы и приложения А–Г). Движок должен уметь
разыгрывать любую карту из `cards/`.

В `examples/` — 12 партий: запись (`game_NN.json`) и ожидаемый итог (`game_NN.expected.json`).
В примерах встречаются не все карты.

Проверка: `engine.simulate` запускается на 100 скрытых партиях, в которых сыграна каждая карта
каталога (базового набора и обоих дополнений); партия засчитывается при точном совпадении итога
(все поля приложения В). Нужно не меньше 95 из 100. Одна партия должна считаться быстрее
нескольких секунд. Входные файлы не изменяй.
"""

TASK = long_task(
    id="task_398_card_game_engine",  # registry id; TASK_ID stays the generator seed
    name="Движок карточной игры «Туманная гавань» по правилам и картам",
    prompt=_PROMPT,
    setup=_setup,
    gold=_gold,
    check=_check,
    tags=("code", "rules", "simulation"),
)
