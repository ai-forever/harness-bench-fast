"""long_12_binary_format_reverse — реверс двоичного формата телеметрии ``.vtl`` по переписке.

The workspace has a partly wrong 2019 description of format v1, the required JSON
output format, 24 sample files (v1/v2/v3), raw dumps of 6 of them made by an old
tool (field layout only, no scales) and — the main material — a long developer
correspondence (``docs/mail/*.md``, 205 messages with quoting) plus a firmware
changelog.  Field meanings, scales, units, the v2/v3 changes and the record types
that occur in no sample (v3 types 8, 10, 11, 12, 13 and the 35 types of the "sensor
pack", 29 of them released) are described only in the mail, often indirectly ("the
byte after the flags"), with guesses of non-firmware people, rejected proposals,
renamed fields, reversed decisions and corrections in later messages.

The agent writes ``vtl_decode.py <file>`` printing JSON; the check decodes 40
hidden files (v1/v2/v3) and compares with the ground truth computed from the
generation model (not from the encoder or the reference decoder).

Messages have ``REWRITE`` hooks (scripts/rewrite_long_texts.py): the draft body
is paraphrased by an LLM and accepted only if an independent model answers the
message's technical questions (what this message claims, who claims it) from the
rewrite.  Without the fixture the drafts are used as is.  The first 67 messages
(``THREADS``) and the second wave (``THREADS2``) are drafted with separate RNGs and
merged by date for file numbering, so growing the second wave never changes a
first-wave draft (and its stored rewrite).  The third wave (the sensor pack,
``MSGS3``) is generated from a story of decisions over the final layouts in
``SENSORS``; its paragraphs, judge questions and threads come from ``_story()``, and
its fixture keys are the message labels ("mail/s-a03" …).
"""

from __future__ import annotations

import copy
import functools
import json
import math
import re
import struct
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .common import long_task, norm, num, require_share, rewritten, rng, run_python, write

TASK_ID = "long_12_binary_format_reverse"
MARKER = 0xB7
EVENTS = ("start", "stop", "pause", "resume")
HARSH = ("brake", "accel", "turn", "bump", "rollover")
DRIVER = ("login", "logout", "denied")
DOORS = ((0, "driver"), (1, "passenger"), (2, "side"), (3, "rear"))
WHEEL_POS = ("LO", "LI", "RI", "RO")
ZONES = (12, 12, 12, 8, 16, 20, 24, 28, 36, 40, 44, 0, -12, -20)  # quarter-hours east of UTC

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass
class Rec:
    rtype: int
    t: int  # v1: ms from the file base time; v2/v3: ticks
    f: dict


@dataclass
class Block:
    base: int
    recs: list[Rec]
    compressed: bool
    damage: str | None = None  # None | "crc" | "payload"


@dataclass
class VFile:
    name: str
    version: int
    tick: int
    zone: int  # v3: quarter-hours; header stores local time = utc + zone * 900
    device_id: int
    base_utc: int
    flags: int
    vehicle: str
    pad: int
    blocks: list[Block]
    truncate: bool = False
    data: bytes = b""
    cut_block: int | None = None
    offsets: list[int] = field(default_factory=list)


VEHICLES = (
    "Газель А123ВС77", "КамАЗ 5490 №17", "Лада Ларгус (курьер)", "Ford Transit Н456ОР", "ПАЗ-3205 маршрут 12",
    "УАЗ «Буханка» 3909", "Hyundai HD78 рефрижератор", "ГАЗон NEXT Т771ЕЕ", "Skoda Octavia такси 4411",
    "Мерседес Спринтер №3", "МАЗ-5440 тягач", "Нива Legend (егерь)", "Volvo FH16 К001КК", "Эвакуатор ГАЗ-3309",
)

NOTES = (
    "Водитель сообщил о стуке в подвеске справа", "Заправка 40 л, чек № 1234", "Плановое ТО «Сервис-Авто»",
    "Объезд — ремонт дороги на Садовой", "Ёлки у въезда на склад, парковка занята", "Клиент не вышел, ждали 15 мин",
    "Проверить давление в шинах: задняя левая 1,8", "Замена масла, пробег записан в путевой лист",
    "Разгрузка у рампы №4", "Пробка на МКАД, опоздание ~20 мин", "Tyre pressure warning, checked OK",
    "Датчик уровня топлива «прыгает»", "Мойка кузова — счёт на 1 500 руб.", "Сменил водитель: Иванов -> Петров",
    "Шум при торможении на скорости > 60 км/ч", "Погрузка завершена, пломба 00417",
    "Штраф за парковку? Уточнить у диспетчера", "Гололёд, скорость снижена", "Короткий рейс, без замечаний",
    "Въезд на территорию по пропуску №88-А", "Дверь фургона заедает, смазать петли",
)

REGIONS = (
    (55_751_244, 37_618_423), (59_938_951, 30_315_635), (56_838_011, 60_597_465), (-34_603_722, -58_381_592),
    (43_115_536, 131_885_485), (54_989_342, 73_368_212), (40_712_776, -74_005_974),
)

# axle layouts for the tyre-pressure record: list of (axle, position) pairs
AXLES = (
    ((1, 0), (1, 3), (2, 0), (2, 3)),
    ((1, 0), (1, 3), (2, 0), (2, 1), (2, 2), (2, 3)),
    ((1, 0), (1, 3), (2, 0), (2, 1), (2, 2), (2, 3), (3, 0), (3, 1), (3, 2), (3, 3)),
)


def _gen_fields(R, rtype: int, version: int, st: dict, spec: dict) -> dict:
    if rtype == 1:
        st["lat"] += R.randint(-900, 900)
        st["lon"] += R.randint(-1400, 1400)
        hdop = R.randint(50, 400) if (version == 3 or spec["hdop"]) else None
        return {"lat": st["lat"], "lon": st["lon"], "alt": R.randint(-28, 460),
                "sats": R.choice((0, 3, 5, 6, 7, 8, 9, 10, 11, 12, 14)), "hdop": hdop}
    if rtype == 2:
        speed = R.choice((0, R.randint(0, 3000), R.randint(2000, 9000), R.randint(6000, 14000)))
        if spec["nospeed"] and R.random() < 0.08:
            speed = 0xFFFF
        return {"speed": speed, "heading": R.randint(0, 179)}
    if rtype == 3:
        oil = R.randint(45, 175) if spec["oil"] and R.random() < 0.9 else None
        return {"rpm": R.choice((0, R.randint(2600, 3800), R.randint(3000, 14000), R.randint(8000, 26000))),
                "coolant": R.choice((R.randint(0, 60), R.randint(100, 135), R.randint(120, 160))),
                "throttle": R.randint(0, 100) if version == 1 else R.randint(0, 255),
                "bits": R.randint(0, 7), "oil": oil}
    if rtype == 4:
        codes = []
        for _ in range(R.randint(1, 3)):
            letter = R.choices((0, 1, 2, 3), weights=(6, 2, 2, 1))[0]
            word = (letter << 14) | (R.randint(0, 3) << 12) | (R.randint(0, 9) << 8) | (R.randint(0, 15) << 4) \
                | R.randint(0, 15)
            codes.append((word, R.randint(0, 7)))
        return {"codes": codes}
    if rtype == 5:
        return {"text": R.choice(NOTES)}
    if rtype == 6:
        return {"mv": R.randint(11200, 14700), "ca": R.choice((R.randint(-3000, -1), R.randint(0, 2500)))}
    if rtype == 7:
        st["odo"] += R.randint(200, 90_000)
        st["trip"] += R.choice((0, 0, 1))
        return {"trip": st["trip"], "event": R.randint(0, 3), "odo": st["odo"]}
    if rtype == 8 and version == 3:
        return {"kind": R.randint(0, 4), "peak": R.randint(150, 2600), "dur": R.randint(3, 250)}
    if rtype == 10 and version == 3:
        return {"wheels": [(axle, pos, R.randint(40, 230), R.randint(-25, 85)) for axle, pos in st["axles"]]}
    if rtype == 11 and version == 3:
        return {"bits": R.randint(0, 15) | (0x80 if R.random() < 0.15 else 0)}
    if rtype == 12 and version == 3:
        return {"level": R.randint(0, 1000), "liters": 0xFFFF if st["no_table"] else R.randint(0, 6000)}
    if rtype == 13 and version == 3:
        return {"event": R.choice((0, 0, 1, 1, 2)),
                "card": R.choice((R.randint(10**6, 2**32 - 1), R.randint(2**32, 10**12)))}
    if version == 3 and rtype in _sensor_gen():
        kind, st, state = _sensor_gen()[rtype]
        vals = _gen_values(R, st, state, pnull=0.25)
        return {"sensor": vals} if kind == "final" else {"raw": _pack_values(st, state, vals)}
    return {"raw": bytes(R.randrange(256) for _ in range(R.randint(2, 11)))}


def _gen_file(R, name: str, spec: dict) -> VFile:
    version = spec["version"]
    tick = R.choice((10, 20, 50, 100)) if version >= 2 else 1
    zone = R.choice(ZONES) if version == 3 else 0
    region = R.choice(REGIONS)
    st = {"lat": region[0] + R.randint(-50_000, 50_000), "lon": region[1] + R.randint(-50_000, 50_000),
          "odo": R.randint(10_000_000, 400_000_000), "trip": R.randint(1, 3000),
          "axles": R.choice(AXLES), "no_table": spec["no_table"]}
    types = [1, 1, 1, 2, 2, 3, 3] + ([6, 7] if version >= 2 else [])
    now = R.randint(0, 5000)  # ms (v1) or ticks (v2/v3)
    blocks = []
    queue: list = []
    if spec.get("sensors"):
        queue = [tid for tid in _sensor_gen() for _ in range(5)]
        R.shuffle(queue)
    for b in range(spec["blocks"]):
        now += R.randint(0, 40_000 if version == 1 else 400)
        base = max(now - R.randint(0, 2000 if version == 1 else 30), 0)
        recs = []
        n = 0 if b in spec["empty"] else R.randint(spec["rmin"], spec["rmax"])
        plan: list = [None] * n
        if queue and n:
            plan += queue[b::spec["blocks"]]
            R.shuffle(plan)
        for item in plan:
            if item is not None:
                rtype = item
            else:
                rtype = R.choice(types)
                roll = R.random()
                if roll < 0.035:
                    rtype = 4
                elif roll < 0.07:
                    rtype = 5
                elif roll < 0.07 + 0.03 * len(spec["rare"]):
                    rtype = R.choice(spec["rare"])
            now += R.randint(40, 2500) if version == 1 else R.randint(1, 120)
            t = now
            if version >= 2 and spec["backwards"] and R.random() < 0.08:
                t = max(base, now - R.randint(1, 90))
            recs.append(Rec(rtype, t, _gen_fields(R, rtype, version, st, spec)))
        blocks.append(Block(base, recs, spec["compress"] and R.random() < 0.55))
    for b in spec["bad"]:
        blocks[b].damage = R.choice(("crc", "payload"))
    flags = (1 if spec["compress"] else 0) | 2
    vf = VFile(name, version, tick, zone, R.randint(1000, 99_999), 1_600_000_000 + R.randint(0, 120_000_000),
               flags, R.choice(VEHICLES), R.randint(0, 6), blocks, truncate=spec["truncate"])
    _encode(vf, R)
    return vf


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def _uvarint(n: int) -> bytes:
    assert n >= 0
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _zigzag(n: int) -> int:
    return (n << 1) if n >= 0 else ((-n << 1) - 1)


def _svarint(n: int) -> bytes:
    return _uvarint(_zigzag(n))


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _text_codec(version: int) -> str:
    return "utf-8" if version >= 3 else "cp1251"


def _field_bytes(vf: VFile, rec: Rec, prev: list[int]) -> bytes:
    """Body after the time delta. `prev` holds the previous GPS point of the block (v3 deltas)."""
    f = rec.f
    t = rec.rtype
    v = vf.version
    if t == 1:
        if v >= 3:
            body = _svarint(f["lat"] - prev[0]) + _svarint(f["lon"] - prev[1])
            prev[:] = [f["lat"], f["lon"]]
            return body + struct.pack("<hBH", f["alt"], f["sats"], f["hdop"])
        body = struct.pack("<iihB", f["lat"], f["lon"], f["alt"], f["sats"])
        return body + (struct.pack("<H", f["hdop"]) if f["hdop"] is not None else b"")
    if t == 2:
        return struct.pack("<HB", f["speed"], f["heading"])
    if t == 3:
        body = struct.pack("<HBBB", f["rpm"], f["coolant"], f["throttle"], f["bits"])
        return body + (bytes([f["oil"]]) if f["oil"] is not None else b"")
    if t == 4:
        return bytes([len(f["codes"])]) + b"".join(struct.pack(">HB", w, s) for w, s in f["codes"])
    if t == 5:
        return f["text"].encode(_text_codec(v))
    if t == 6:
        return struct.pack("<Hh", f["mv"], f["ca"])
    if t == 7:
        return _uvarint(f["trip"]) + bytes([f["event"]]) + struct.pack("<I", f["odo"])
    if "raw" in f:
        return f["raw"]
    if "sensor" in f:
        st = SENSOR_BY_TID[t]
        return _pack_values(st, _final_state(st), f["sensor"])
    if t == 8:
        return struct.pack("<BHH", f["kind"], f["peak"], f["dur"])
    if t == 10:
        return bytes([len(f["wheels"])]) + b"".join(
            struct.pack("<BBb", (axle << 4) | pos, p, tc) for axle, pos, p, tc in f["wheels"])
    if t == 11:
        return bytes([f["bits"]])
    if t == 12:
        return struct.pack("<HH", f["level"], f["liters"])
    if t == 13:
        return bytes([f["event"]]) + _uvarint(f["card"])
    raise AssertionError(t)


def _payload(vf: VFile, blk: Block) -> bytes:
    out = bytearray(_uvarint(blk.base))
    prev_t = blk.base
    prev_gps = [0, 0]
    for rec in blk.recs:
        delta = rec.t - prev_t
        prev_t = rec.t
        dbytes = _svarint(delta) if vf.version >= 2 else _uvarint(delta)
        body = dbytes + _field_bytes(vf, rec, prev_gps)
        out += bytes([rec.rtype]) + _uvarint(len(body)) + body
    return bytes(out)


def _encode(vf: VFile, R) -> None:
    name = vf.vehicle.encode(_text_codec(vf.version))
    local = vf.base_utc + vf.zone * 900
    rest = struct.pack("<IIB", vf.device_id, local, vf.flags)
    if vf.version >= 2:
        rest += struct.pack("<H", vf.tick)
    if vf.version >= 3:
        rest += struct.pack("<b", vf.zone)
    rest += _uvarint(len(name)) + name + b"\0" * vf.pad
    data = bytearray(b"VTL" + bytes([vf.version]) + struct.pack("<H", 6 + len(rest)) + rest)
    vf.offsets = []
    for blk in vf.blocks:
        raw = _payload(vf, blk)
        stored = zlib.compress(raw, 6) if blk.compressed else raw
        head = bytes([1 if blk.compressed else 0]) + struct.pack("<H", len(stored))
        covered = head + stored if vf.version >= 3 else stored
        crc = _crc16(covered)
        if blk.damage == "crc":
            crc ^= R.choice((0x0001, 0x0100, 0x8000, 0x00FF, 0x1234))
        elif blk.damage == "payload":
            pos = R.randrange(len(stored))
            stored = stored[:pos] + bytes([stored[pos] ^ (1 << R.randrange(8))]) + stored[pos + 1:]
            if _crc16(head + stored if vf.version >= 3 else stored) == crc:
                crc ^= 1
        assert len(stored) < 65536
        vf.offsets.append(len(data))
        data += bytes([MARKER]) + head + stored
        data += struct.pack("<H" if vf.version >= 3 else ">H", crc)
    if vf.truncate:
        start = vf.offsets[-1]
        data = data[:R.randint(start + 1, len(data) - 1)]
        vf.cut_block = len(vf.blocks) - 1
    vf.data = bytes(data)


# ---------------------------------------------------------------------------
# Expected output (from the model, not from the bytes)
# ---------------------------------------------------------------------------


def _dtc(word: int) -> str:
    return "PCBU"[word >> 14] + f"{(word >> 12) & 3:X}{(word >> 8) & 15:X}{(word >> 4) & 15:X}{word & 15:X}"


def _rec_json(vf: VFile, rec: Rec) -> dict:
    out: dict = {"t_ms": vf.base_utc * 1000 + rec.t * vf.tick}
    f = rec.f
    t = rec.rtype
    if t == 1:
        out.update(type="gps", lat=f["lat"] / 1_000_000, lon=f["lon"] / 1_000_000, alt_m=f["alt"], sats=f["sats"],
                   hdop=None if f["hdop"] is None else f["hdop"] / 100)
    elif t == 2:
        out.update(type="speed", kmh=None if f["speed"] == 0xFFFF else f["speed"] / 100, heading=f["heading"] * 2)
    elif t == 3:
        out.update(type="engine", rpm=f["rpm"] / 4, coolant_c=f["coolant"] - 40,
                   throttle_pct=f["throttle"] if vf.version == 1 else f["throttle"] * 100 / 255,
                   ignition=bool(f["bits"] & 1), check_engine=bool(f["bits"] & 2), cruise=bool(f["bits"] & 4),
                   oil_c=None if f["oil"] is None else f["oil"] - 40)
    elif t == 4:
        out.update(type="fault", codes=[{"code": _dtc(w), "confirmed": bool(s & 1), "pending": bool(s & 2),
                                         "mil": bool(s & 4)} for w, s in f["codes"]])
    elif t == 5:
        out.update(type="note", text=f["text"])
    elif t == 6:
        out.update(type="battery", volts=f["mv"] / 1000, amps=f["ca"] / 100)
    elif t == 7:
        out.update(type="trip", trip_id=f["trip"], event=EVENTS[f["event"]], odometer_km=f["odo"] / 1000)
    elif "raw" in f:
        out.update(type="unknown", type_id=t, hex=f["raw"].hex())
    elif "sensor" in f:
        out.update(_sensor_json(SENSOR_BY_TID[t], f["sensor"]))
    elif t == 8:
        out.update(type="harsh", kind=HARSH[f["kind"]], peak_g=f["peak"] / 1000, duration_ms=f["dur"] * 10)
    elif t == 10:
        out.update(type="tires", wheels=[{"wheel": f"{axle}{WHEEL_POS[pos]}", "kpa": p * 4, "temp_c": tc}
                                         for axle, pos, p, tc in f["wheels"]])
    elif t == 11:
        out.update(type="doors", open=[n for bit, n in DOORS if f["bits"] >> bit & 1], alarm=bool(f["bits"] & 0x80))
    elif t == 12:
        out.update(type="fuel", level_pct=f["level"] / 10,
                   liters=None if f["liters"] == 0xFFFF else f["liters"] / 10)
    elif t == 13:
        out.update(type="driver", event=DRIVER[f["event"]], card=f["card"])
    return out


def expected(vf: VFile) -> dict:
    records: list[dict] = []
    errors: list[dict] = []
    for i, blk in enumerate(vf.blocks):
        if vf.cut_block == i:
            errors.append({"block": i, "offset": vf.offsets[i], "error": "truncated"})
            break
        if blk.damage:
            errors.append({"block": i, "offset": vf.offsets[i], "error": "crc"})
            continue
        records.extend(_rec_json(vf, r) for r in blk.recs)
    return {"format_version": vf.version, "device_id": vf.device_id, "vehicle": vf.vehicle,
            "base_time": vf.base_utc, "tick_ms": vf.tick, "records": records, "errors": errors}


def render_json(doc: dict) -> str:
    """One record per line: readable and compact."""
    head = {k: v for k, v in doc.items() if k not in ("records", "errors")}
    lines = ["{"]
    for k, v in head.items():
        lines.append(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},")
    lines.append('  "records": [')
    recs = [json.dumps(r, ensure_ascii=False) for r in doc["records"]]
    lines.extend(f"    {r}," for r in recs[:-1])
    if recs:
        lines.append(f"    {recs[-1]}")
    lines.append("  ],")
    lines.append('  "errors": [' + ", ".join(json.dumps(e) for e in doc["errors"]) + "]")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Old tool dumps (vtldump 0.9): layout only, raw integers, no scales
# ---------------------------------------------------------------------------


def _old_record(vf: VFile, rec: Rec) -> dict:
    f = rec.f
    out: dict = {"t": vf.base_utc * 1000 + rec.t * vf.tick, "type": rec.rtype}
    body = _field_bytes(vf, rec, [0, 0])
    if rec.rtype == 1:
        out["raw"] = [f["lat"], f["lon"], f["alt"], f["sats"]]
        if len(body) > 11:
            out["tail"] = body[11:].hex()
    elif rec.rtype == 2:
        out["raw"] = [f["speed"], f["heading"]]
    elif rec.rtype == 3:
        out["raw"] = [f["rpm"], f["coolant"], f["throttle"], f["bits"]]
        if len(body) > 5:
            out["tail"] = body[5:].hex()
    elif rec.rtype == 4:
        out["raw"] = [[w, s] for w, s in f["codes"]]
    else:
        out["hex"] = body.hex()
    return out


def old_dump(vf: VFile) -> str:
    assert vf.version <= 2
    head = {"tool": "vtldump 0.9", "version": vf.version, "device": vf.device_id, "vehicle": vf.vehicle,
            "base_time": vf.base_utc, "tick": vf.tick}
    lines = ["{"] + [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}," for k, v in head.items()]
    lines.append('  "blocks": [')
    blocks = []
    for i, blk in enumerate(vf.blocks):
        status = "truncated" if vf.cut_block == i else ("crc" if blk.damage else "ok")
        recs = [] if status != "ok" else [json.dumps(_old_record(vf, r), ensure_ascii=False) for r in blk.recs]
        text = f'    {{"index": {i}, "offset": {vf.offsets[i]}, "status": "{status}", "records": ['
        text += "".join(f"\n      {r}," for r in recs).rstrip(",") + ("\n    ]}" if recs else "]}")
        blocks.append(text)
        if status == "truncated":
            break
    lines.append(",\n".join(blocks))
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# File sets
# ---------------------------------------------------------------------------


def _spec(R, version: int, j: int, *, hidden: bool) -> dict:
    nb = R.randint(5, 8)
    bad = tuple(sorted(R.sample(range(nb - 1), R.choice((1, 1, 2))))) if (j % 4 == 1 or R.random() < 0.15) else ()
    if version == 1:
        rare: tuple = (9,) if j % 3 == 2 else ()
    elif version == 2:
        rare = (9, 12) if j % 2 == 0 else ((9,) if j % 4 == 1 else ())
        if hidden and j % 2 == 1:
            rare += (13,)
        if hidden:
            rare += (tuple(SENSOR_BY_TID)[j * 5 % len(SENSOR_BY_TID)],)
    else:
        rare = tuple(x for k, x in enumerate((8, 10, 11, 13, 12)) if k != j % 6) if hidden else ()
    return {"version": version, "blocks": nb, "rmin": 18, "rmax": 44,
            "compress": j % 3 == 0 or R.random() < 0.8,
            "hdop": version >= 2 or j % 2 == 0, "nospeed": j % 3 != 1, "oil": version >= 2 and j % 2 == 1,
            "backwards": version >= 2 and (j % 2 == 0 or R.random() < 0.7), "no_table": j % 3 == 0,
            "bad": bad, "truncate": j % 5 == 2, "empty": (R.randrange(nb),) if j % 6 == 4 else (),
            "rare": rare, "sensors": hidden and version == 3}


def _features(vf: VFile) -> set[str]:
    feats = {f"v{vf.version}"}
    for blk in vf.blocks:
        for rec in blk.recs:
            f = rec.f
            feats.add(f"type{rec.rtype}" + ("raw" if "raw" in f else ""))
            if rec.rtype == 1 and f["hdop"] is not None:
                feats.add("hdop")
            if rec.rtype == 2 and f["speed"] == 0xFFFF:
                feats.add("nospeed")
            if rec.rtype == 3 and f["oil"] is not None:
                feats.add("oil")
            if rec.rtype == 7 and f["event"] == 3:
                feats.add("resume")
            if rec.rtype == 11 and f["bits"] & 12 not in (0, 12):
                feats.add("door23")
            if rec.rtype == 12 and "raw" not in f and f["liters"] == 0xFFFF:
                feats.add("notable")
            if rec.rtype == 8 and "raw" not in f and f["kind"] == 4:
                feats.add("rollover")
            if rec.rtype == 13 and "raw" not in f:
                feats.add("longcard" if f["card"] >= 2**32 else "shortcard")
                if f["event"] == 2:
                    feats.add("denied")
            if "sensor" in f:
                feats |= _sensor_features(rec.rtype, f["sensor"])
            if vf.version == 2 and rec.rtype in SENSOR_BY_TID:
                feats.add("v2sensor")
    if any(b.damage for b in vf.blocks):
        feats.add("crc")
    if vf.truncate:
        feats.add("truncated")
    return feats


def _file_set(R, prefix: str, versions: list[int], *, hidden: bool) -> list[VFile]:
    seen = {1: 0, 2: 0, 3: 0}
    files = []
    for i, version in enumerate(versions):
        j = seen[version]
        seen[version] += 1
        files.append(_gen_file(R, f"{prefix}{i + 1:02d}", _spec(R, version, j, hidden=hidden)))
    return files


@functools.cache
def build() -> dict:
    R = rng(TASK_ID)
    samples = _file_set(R, "s", [1, 2, 3] * 8, hidden=False)
    decoded = [vf.name for vf in samples if vf.version <= 2][:6]
    HR = rng(TASK_ID + ":hidden")
    versions = [1] * 12 + [2] * 13 + [3] * 15
    HR.shuffle(versions)
    hidden = _file_set(HR, "h", versions, hidden=True)
    counts: dict[str, int] = {}
    for vf in hidden:
        for feat in _features(vf):
            counts[feat] = counts.get(feat, 0) + 1
    for feat in ("hdop", "nospeed", "oil", "resume", "type8", "type10", "type11", "type12", "type12raw",
                 "door23", "notable", "crc", "truncated", "type5", "type4", "type13", "type13raw", "rollover",
                 "denied", "longcard", "shortcard"):
        assert counts.get(feat, 0) >= 4, (feat, counts.get(feat))
    for feat in _sensor_required():
        assert counts.get(feat, 0) >= (4 if feat.count(":") == 1 else 3), (feat, counts.get(feat))
    assert counts.get("v2sensor", 0) >= 4, counts.get("v2sensor")
    sample_feats = set().union(*(_features(vf) for vf in samples))
    assert not sample_feats & {"type8", "type10", "type11", "type12", "type13", "type13raw"}, sample_feats
    assert not any(x.startswith("s:") or x == "v2sensor" for x in sample_feats), sample_feats
    return {"samples": samples, "decoded": decoded, "hidden": hidden}


# ---------------------------------------------------------------------------
# Reference decoder (gold)
# ---------------------------------------------------------------------------

_DECODER_SRC = r'''#!/usr/bin/env python3
"""Decode a .vtl telemetry log (format v1/v2/v3) and print JSON to stdout."""

import json
import struct
import sys
import zlib

MARKER = 0xB7
EVENTS = {0: "start", 1: "stop", 2: "pause", 3: "resume"}
HARSH = {0: "brake", 1: "accel", 2: "turn", 3: "bump", 4: "rollover"}
DRIVER = {0: "login", 1: "logout", 2: "denied"}
DOORS = ((0, "driver"), (1, "passenger"), (2, "side"), (3, "rear"))
WHEELS = {0: "LO", 1: "LI", 2: "RI", 3: "RO"}
SFMT = {"u8": "<B", "i8": "<b", "u16": "<H", "i16": "<h", "U16": ">H", "I16": ">h", "u32": "<I", "i32": "<i",
        "U32": ">I", "bool": "<B"}
# sensor pack: type -> (name, fields); field = (key, format, scale, shift, no-data value, codes, bit names,
# optional tail, counted list)
SENSORS = {}


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def uvarint(buf, pos):
    result = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def svarint(buf, pos):
    n, pos = uvarint(buf, pos)
    return (n >> 1) ^ -(n & 1), pos


def text(raw, version):
    return raw.decode("utf-8" if version >= 3 else "cp1251")


def dtc(word):
    digits = ((word >> 12) & 3, (word >> 8) & 15, (word >> 4) & 15, word & 15)
    return "PCBU"[word >> 14] + "".join("%X" % d for d in digits)


def gps(body, version, state):
    if version >= 3:
        dlat, pos = svarint(body, 0)
        dlon, pos = svarint(body, pos)
        state["lat"] += dlat
        state["lon"] += dlon
        lat, lon = state["lat"], state["lon"]
        alt, sats, hdop = struct.unpack_from("<hBH", body, pos)
    else:
        lat, lon, alt, sats = struct.unpack_from("<iihB", body)
        hdop = struct.unpack_from("<H", body, 11)[0] if len(body) >= 13 else None
    return {"type": "gps", "lat": lat / 1_000_000, "lon": lon / 1_000_000, "alt_m": alt, "sats": sats,
            "hdop": None if hdop is None else hdop / 100}


def engine(body, version):
    rpm, coolant, throttle, bits = struct.unpack_from("<HBBB", body)
    oil = body[5] - 40 if version >= 2 and len(body) >= 6 else None
    return {"type": "engine", "rpm": rpm / 4, "coolant_c": coolant - 40,
            "throttle_pct": throttle if version == 1 else throttle * 100 / 255,
            "ignition": bool(bits & 1), "check_engine": bool(bits & 2), "cruise": bool(bits & 4), "oil_c": oil}


def sensor_value(field, buf, pos):
    fmt, mul, add, null, enum, flags = field[1:7]
    if fmt == "uv":
        raw, pos = uvarint(buf, pos)
    elif fmt == "sv":
        raw, pos = svarint(buf, pos)
    else:
        (raw,) = struct.unpack_from(SFMT[fmt], buf, pos)
        pos += struct.calcsize(SFMT[fmt])
    if null is not None and raw == null:
        return None, pos
    if fmt == "bool":
        return bool(raw), pos
    if enum:
        return enum[raw], pos
    if flags:
        return [name for bit, name in enumerate(flags) if raw >> bit & 1], pos
    return raw * mul + add, pos


def sensor(spec, body):
    name, fields = spec
    out = {"type": name}
    pos = 0
    for field in fields:
        key, optional, counted = field[0], field[7], field[8]
        if optional and pos >= len(body):
            out[key] = None
        elif counted:
            count = body[pos]
            pos += 1
            out[key] = []
            for _ in range(count):
                value, pos = sensor_value(field, body, pos)
                out[key].append(value)
        else:
            out[key], pos = sensor_value(field, body, pos)
    return out


def decode_fields(rtype, body, version, state):
    if rtype == 1:
        return gps(body, version, state)
    if rtype == 2:
        speed, heading = struct.unpack_from("<HB", body)
        return {"type": "speed", "kmh": None if speed == 0xFFFF else speed / 100, "heading": heading * 2}
    if rtype == 3:
        return engine(body, version)
    if rtype == 4:
        codes = []
        for i in range(body[0]):
            word, status = struct.unpack_from(">HB", body, 1 + 3 * i)
            codes.append({"code": dtc(word), "confirmed": bool(status & 1), "pending": bool(status & 2),
                          "mil": bool(status & 4)})
        return {"type": "fault", "codes": codes}
    if rtype == 5:
        return {"type": "note", "text": text(body, version)}
    if rtype == 6:
        mv, current = struct.unpack_from("<Hh", body)
        return {"type": "battery", "volts": mv / 1000, "amps": current / 100}
    if rtype == 7:
        trip_id, pos = uvarint(body, 0)
        event = body[pos]
        (odo,) = struct.unpack_from("<I", body, pos + 1)
        return {"type": "trip", "trip_id": trip_id, "event": EVENTS[event], "odometer_km": odo / 1000}
    if version < 3:
        return None
    if rtype == 8:
        kind, peak, duration = struct.unpack_from("<BHH", body)
        return {"type": "harsh", "kind": HARSH[kind], "peak_g": peak / 1000, "duration_ms": duration * 10}
    if rtype == 10:
        wheels = []
        for i in range(body[0]):
            where, pressure, temp = struct.unpack_from("<BBb", body, 1 + 3 * i)
            wheels.append({"wheel": "%d%s" % (where >> 4, WHEELS[where & 15]), "kpa": pressure * 4,
                           "temp_c": temp})
        return {"type": "tires", "wheels": wheels}
    if rtype == 11:
        bits = body[0]
        return {"type": "doors", "open": [name for bit, name in DOORS if bits >> bit & 1],
                "alarm": bool(bits & 0x80)}
    if rtype == 12:
        level, liters = struct.unpack_from("<HH", body)
        return {"type": "fuel", "level_pct": level / 10, "liters": None if liters == 0xFFFF else liters / 10}
    if rtype == 13:
        card, _ = uvarint(body, 1)
        return {"type": "driver", "event": DRIVER[body[0]], "card": card}
    if rtype in SENSORS:
        return sensor(SENSORS[rtype], body)
    return None


def decode_block(payload, version, base_ms, tick, records):
    t, pos = uvarint(payload, 0)
    state = {"lat": 0, "lon": 0}
    while pos < len(payload):
        rtype = payload[pos]
        size, pos = uvarint(payload, pos + 1)
        body = payload[pos:pos + size]
        pos += size
        if version >= 2:
            delta, start = svarint(body, 0)
        else:
            delta, start = uvarint(body, 0)
        t += delta
        fields = decode_fields(rtype, body[start:], version, state)
        rec = {"t_ms": base_ms + t * tick}
        if fields is None:
            rec.update({"type": "unknown", "type_id": rtype, "hex": body[start:].hex()})
        else:
            rec.update(fields)
        records.append(rec)


def decode(data):
    if data[:3] != b"VTL":
        raise SystemExit("not a VTL file")
    version = data[3]
    header_len, device_id, base_time, _flags = struct.unpack_from("<HIIB", data, 4)
    pos = 15
    tick, zone = 1, 0
    if version >= 2:
        (tick,) = struct.unpack_from("<H", data, pos)
        pos += 2
    if version >= 3:
        (zone,) = struct.unpack_from("<b", data, pos)
        pos += 1
    base_time -= zone * 900
    name_len, pos = uvarint(data, pos)
    vehicle = text(data[pos:pos + name_len], version)
    records, errors = [], []
    pos = header_len
    index = 0
    while pos < len(data):
        start = pos
        if data[pos] != MARKER:
            errors.append({"block": index, "offset": start, "error": "bad_marker"})
            break
        if pos + 4 > len(data):
            errors.append({"block": index, "offset": start, "error": "truncated"})
            break
        bflags = data[pos + 1]
        (length,) = struct.unpack_from("<H", data, pos + 2)
        end = pos + 4 + length + 2
        if end > len(data):
            errors.append({"block": index, "offset": start, "error": "truncated"})
            break
        payload = data[pos + 4:pos + 4 + length]
        if version >= 3:
            (crc,) = struct.unpack_from("<H", data, pos + 4 + length)
            covered = data[pos + 1:pos + 4 + length]
        else:
            (crc,) = struct.unpack_from(">H", data, pos + 4 + length)
            covered = payload
        pos = end
        if crc16(covered) != crc:
            errors.append({"block": index, "offset": start, "error": "crc"})
            index += 1
            continue
        if bflags & 1:
            payload = zlib.decompress(payload)
        decode_block(payload, version, base_time * 1000, tick, records)
        index += 1
    return {"format_version": version, "device_id": device_id, "vehicle": vehicle, "base_time": base_time,
            "tick_ms": tick, "records": records, "errors": errors}


def main():
    with open(sys.argv[1], "rb") as fh:
        data = fh.read()
    json.dump(decode(data), sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
'''


# ---------------------------------------------------------------------------
# Static documents
# ---------------------------------------------------------------------------

FORMAT_V1 = """\
# Формат журнала телеметрии VTL, версия 1

Документ описывает двоичный формат `.vtl`, в котором бортовые трекеры «Вектор-Т» пишут
журнал поездки. Описание составлено в начале 2019 года по исходникам прототипа прошивки и
с тех пор не обновлялось. Часть сведений могла оказаться неточной уже для серийных
прошивок; уточнения и изменения следующих версий обсуждались в переписке команды.

## 1. Общие соглашения

- Все многобайтовые целые — little-endian (младший байт первым).
- `u8/u16/u32` — беззнаковые целые, `i8/i16/i32` — знаковые (дополнительный код).
- `varint` — беззнаковое целое переменной длины в стиле LEB128: младшие 7 бит каждого байта —
  очередные 7 бит числа (начиная с младших), старший бит байта равен 1, если за ним следует
  ещё байт. Пример: `0x96 0x01` = 0x16 + (0x01 << 7) = 150.
- Время в формате — целые миллисекунды. Абсолютное время записи получается сложением
  базового времени файла, базы блока и дельт записей (см. раздел 5).
- Контрольная сумма — CRC-16/CCITT-FALSE: полином 0x1021, начальное значение 0xFFFF, без
  отражения битов, без финального XOR. Для строки `123456789` она равна 0x29B1.

## 2. Заголовок файла

| Смещение | Размер | Поле |
|---|---|---|
| 0 | 3 | сигнатура `VTL` (ASCII) |
| 3 | u8 | версия формата (1) |
| 4 | u16 | длина заголовка в байтах, считая от начала файла |
| 6 | u32 | идентификатор устройства |
| 10 | u32 | базовое время: секунды Unix (UTC) |
| 14 | u8 | флаги: бит 0 — в файле могут быть сжатые блоки, бит 1 — трекер с модулем GPS |
| 15 | varint + байты | название транспортного средства: длина в байтах, затем текст в UTF-8 |

После названия заголовок может быть дополнен нулевыми байтами до указанной длины: это
резерв под будущие поля. Первый блок начинается ровно со смещения «длина заголовка».

## 3. Блоки

Данные после заголовка разбиты на блоки. Трекер копит записи в памяти и сбрасывает их
блоком раз в несколько секунд или минут. Блок устроен так:

| Размер | Поле |
|---|---|
| u8 | маркер блока, всегда 0xB7 |
| u8 | флаги блока: бит 0 — полезная нагрузка сжата zlib (со стандартным заголовком zlib) |
| u16 | длина полезной нагрузки в файле (для сжатого блока — длина сжатых данных) |
| … | полезная нагрузка |
| u16 | CRC-16 полезной нагрузки в том виде, как она лежит в файле (для сжатого блока — CRC сжатых байтов) |

Флаг сжатия в заголовке файла только сообщает, что сжатые блоки возможны; сжат ли конкретный
блок, определяет его собственный флаг. Блоки идут подряд до конца файла.

## 4. Полезная нагрузка блока и записи

Полезная нагрузка (после распаковки, если блок сжат) начинается с `varint` — базы времени
блока: смещения в миллисекундах от базового времени файла. Дальше до конца нагрузки подряд
идут записи. Каждая запись:

| Размер | Поле |
|---|---|
| u8 | тип записи |
| varint | длина тела записи в байтах |
| … | тело записи |

Тело любой записи начинается с `varint` — дельты времени в миллисекундах относительно
предыдущей записи этого же блока (для первой записи блока — относительно базы блока).
Остальные поля тела зависят от типа. Длина тела позволяет пропустить запись незнакомого
типа, не разбирая её.

## 5. Время

Время записи = базовое время файла (в мс) + база блока + сумма дельт всех записей блока
до этой записи включительно. Внутри блока время не убывает. Базы разных блоков
независимы, поэтому потеря одного блока не сбивает время в остальных.

## 6. Типы записей

### 6.1. Тип 1 — GPS

| Поле | Тип | Описание |
|---|---|---|
| широта | i32 | в стотысячных долях градуса (значение / 100000 = градусы) |
| долгота | i32 | в стотысячных долях градуса |
| высота | i16 | метры над уровнем моря, может быть отрицательной |
| спутники | u8 | число спутников в решении; 0 — нет фиксации |

Южная широта и западная долгота — отрицательные значения.

### 6.2. Тип 2 — скорость

| Поле | Тип | Описание |
|---|---|---|
| скорость | u16 | сотые доли км/ч |
| курс | u8 | курс в градусах |

### 6.3. Тип 3 — двигатель

| Поле | Тип | Описание |
|---|---|---|
| обороты | u16 | обороты коленвала в минуту |
| температура ОЖ | i8 | температура охлаждающей жидкости, °C |
| дроссель | u8 | положение педали/дросселя, 0–100 % |
| флаги | u8 | бит 0 — зажигание включено, бит 1 — горит Check Engine, бит 2 — включён круиз-контроль |

### 6.4. Тип 4 — коды неисправностей

Первый байт тела (после дельты времени) — число кодов N, затем N троек по 3 байта:
двухбайтовое слово кода и байт статуса.

Слово кода хранится в порядке big-endian (так его отдаёт диагностический адаптер) и
кодирует код OBD-II в стандартной упаковке: биты 15–14 — буква (0 — P, 1 — C, 2 — B,
3 — U), биты 13–12 — первая цифра (0–3), далее три тетрады — ещё три шестнадцатеричные
цифры. Слово 0x0301 означает код `P0301`, слово 0xC123 — `U0123`.

Байт статуса: бит 0 — код ожидающий (pending), бит 1 — код подтверждён (confirmed),
бит 2 — горит лампа MIL. Остальные биты зарезервированы.

### 6.5. Тип 5 — текстовая заметка

Остаток тела после дельты времени — текст заметки в UTF-8 без завершающего нуля.
Заметки вводит водитель или диспетчер через планшет.

### 6.6. Тип 8 — акселерометр (зарезервирован)

Тип 8 зарезервирован под данные акселерометра. В прошивке прототипа не используется.

## 7. Известные особенности

- Трекер может записать блок без единой записи (только база времени) — это нормально.
- При пропадании питания последний блок может оказаться обрезан.
- Блок с неверной контрольной суммой нельзя считать достоверным.
"""

OUTPUT_FORMAT = """\
# Требуемый вывод `vtl_decode.py`

`python3 vtl_decode.py <путь к файлу .vtl>` печатает в stdout один JSON-объект (кодировка
UTF-8, форматирование и порядок ключей не важны) и завершается с кодом 0 для любого файла
с корректным заголовком — в том числе с испорченными или обрезанными блоками. Скрипт
должен понимать журналы всех версий формата, которые пишут трекеры (1, 2 и 3).

## Объект файла

```
{
  "format_version": 2,          // версия формата из заголовка
  "device_id": 48213,
  "vehicle": "Газель А123ВС77", // название ТС из заголовка
  "base_time": 1650000000,      // базовое время журнала, секунды Unix, UTC
  "tick_ms": 20,                // длительность единицы времени журнала в мс; для версии 1 — 1
  "records": [ ... ],           // все записи всех достоверных блоков в порядке следования в файле
  "errors": [ ... ]             // проблемы с блоками, в порядке обнаружения; пустой список, если их нет
}
```

## Записи

У каждой записи есть `t_ms` — абсолютное время записи в миллисекундах Unix, UTC (целое) и
`type`. Остальные поля зависят от типа; числа — точные значения после перевода в указанные
единицы, без округления. `null` — значение отсутствует в записи или помечено трекером как
«нет данных».

| type | поля |
|---|---|
| `gps` | `lat`, `lon` — градусы; `alt_m` — метры (целое); `sats` — число спутников (целое); `hdop` — геометрический фактор точности (HDOP, безразмерный) или `null` |
| `speed` | `kmh` — км/ч или `null`; `heading` — курс в градусах (целое) |
| `engine` | `rpm` — об/мин; `coolant_c` — температура ОЖ, °C (целое); `throttle_pct` — положение педали, % (0–100); `ignition`, `check_engine`, `cruise` — true/false; `oil_c` — температура масла, °C (целое) или `null` |
| `fault` | `codes` — список объектов `{"code": "P0301", "confirmed": bool, "pending": bool, "mil": bool}` в порядке записи |
| `note` | `text` — текст заметки |
| `battery` | `volts` — напряжение, В; `amps` — ток, А (отрицательный — разряд) |
| `trip` | `trip_id` — номер поездки (целое); `event` — `start`, `stop`, `pause` или `resume`; `odometer_km` — км |
| `harsh` | `kind` — `brake`, `accel`, `turn`, `bump` или `rollover` (опрокидывание, опасный крен); `peak_g` — пиковое ускорение в g; `duration_ms` — длительность, мс (целое) |
| `tires` | `wheels` — список `{"wheel": "2LI", "kpa": 820, "temp_c": 31}` в порядке записи: `wheel` — номер оси (1 — передняя), затем `L`/`R` — левая/правая сторона, затем `O`/`I` — внешнее/внутреннее колесо; `kpa` — давление, кПа (целое); `temp_c` — температура, °C (целое) |
| `doors` | `open` — список открытых дверей из `driver`, `passenger`, `side` (боковая сдвижная), `rear` (задние распашные) в этом порядке; `alarm` — сработала охранная сигнализация (true/false) |
| `fuel` | `level_pct` — уровень топлива в баке, %; `liters` — объём топлива, л, или `null` |
| `driver` | `event` — `login` (карту водителя приложили), `logout` (карту убрали) или `denied` (карта не допущена к этой машине); `card` — номер карты (целое) |
| `unknown` | `type_id` — номер типа (целое); `hex` — байты тела после дельты времени, строчными шестнадцатеричными цифрами без пробелов |

- Запись типа, который в данной версии формата не имеет описанного значения (отладочные,
  зарезервированные, будущие), выводится как `unknown`.
- Если тело записи длиннее, чем нужно для известных полей её типа, лишние байты в конце
  игнорируются (для `unknown` в `hex` попадает всё тело после дельты времени).

## Записи пакета датчиков

Начиная с прошивки 3.3 трекеры пишут записи «пакета датчиков». Их номера, устройство, единицы
и имена для выгрузки есть только в переписке (`docs/mail/`). Для каждой такой записи:

- `type` — имя записи для выгрузки, окончательно утверждённое командой прошивки; остальные
  ключи объекта, кроме `t_ms`, — утверждённые имена всех полей записи, по одному ключу на поле,
  других ключей нет;
- число — сырое значение, переведённое в единицы, которые переписка называет для этого поля
  (сначала масштаб, затем сдвиг), без округления;
- поле с кодами — строковый идентификатор кода, как он записан в переписке (например `off`);
  набор битов — список идентификаторов установленных битов по возрастанию номера бита (пустой
  список, если ни один бит не установлен); признак «да/нет» — `true`/`false`;
- поле, в которое трекер записал особое значение «нет данных», и необязательное поле в конце
  записи, которого в этой записи нет, — `null`;
- поле, перед которым в записи стоит байт с числом элементов, — список значений в порядке
  записи (`null` для элемента со значением «нет данных»);
- номер типа, которому команда прошивки не дала окончательного описания для данной версии
  журнала (в том числе запись, от которой отказались до выпуска), выводится как `unknown`.

## Ошибки блоков

Каждая ошибка — объект `{"block": <номер блока>, "offset": <смещение маркера блока от
начала файла>, "error": <вид>}`. Блоки нумеруются с 0 в порядке следования в файле,
включая испорченные.

- `crc` — контрольная сумма не совпала. Блок целиком пропускается (ни одной записи из него
  не выводится), разбор продолжается со следующего блока.
- `truncated` — файл закончился раньше, чем блок (его заголовок, нагрузка или CRC). Разбор
  на этом заканчивается.
- `bad_marker` — на месте начала блока нет маркера 0xB7. Разбор на этом заканчивается.
"""

DECODED_README = """\
# Выгрузки старого инструмента `vtldump 0.9`

В этом каталоге — выгрузки нескольких файлов из `samples/`, сделанные внутренним
инструментом `vtldump` версии 0.9 (2021 год, автор — Павел Рудаков). Это **не** требуемый
формат вывода: инструмент писался для отладки раскладки и никаких единиц не знает.

- `version`, `device`, `vehicle`, `tick` — поля заголовка; `base_time` — базовое время
  журнала, как его понимал инструмент.
- `blocks` — блоки в порядке следования: `index`, `offset` (смещение маркера), `status`
  (`ok`, `crc` — не сошлась контрольная сумма, записи не выводятся; `truncated` — блок
  обрезан, разбор остановлен).
- Запись: `t` — абсолютное время записи в миллисекундах Unix; `type` — номер типа.
  Для типов 1, 2 и 3 `raw` — целые значения полей в порядке их следования в теле записи
  (после дельты времени), как они лежат в байтах, без пересчёта в единицы; `tail` —
  байты, которые остались в теле после этих полей (hex). Для типа 4 `raw` — пары
  «слово кода, байт статуса». Для остальных типов `hex` — тело записи после дельты времени.

Инструмент знает только версии 1 и 2 формата; файлы версии 3 он не открывает.
"""

MAIL_README = """\
# Переписка команды «Вектор-Т» о журнале поездок

В каталоге — выгрузка рабочей почты за 2019–2025 годы, касающейся журнала `.vtl` и
соседних тем: онлайн-протокола ВТП, SMS-команд, конфигурации трекера, логгера «Вектор-М».
Файлы пронумерованы в хронологическом порядке; дата и время отправки — в заголовке
письма. В ответах часто процитировано предыдущее письмо (строки с `>`).

## Кто есть кто

| Участник | Роль |
|---|---|
| Олег Кравец | ведущий разработчик прошивки трекера |
| Сергей Лыков | схемотехник, пишет драйверы датчиков в прошивке |
| Дина Ахмерова | разработчик прошивки (в команде с 2022 года) |
| Марина Белова | бэкенд и онлайн-протокол ВТП |
| Павел Рудаков | внутренние инструменты (`vtldump`) |
| Тимур Галеев | внедрение и поддержка клиентов |
| Ирина Шевчук | руководитель проекта |
| Глеб Орлов | разработчик логгера «Вектор-М» (другая команда, другой формат `.vcm`) |
| Артём Зуев | разработчик прошивки, пакет датчиков (в команде с 2024 года) |
| Вера Ким | интегратор клиента «ТрансЛогистик» (не сотрудник «Вектор-Т») |

## Как читать

- Устройство журнала `.vtl` определяет прошивка. Описанием формата считаются утверждения
  команды прошивки (Олег, Сергей, Дина, Артём), в том числе когда кто-то из них подтверждает
  чужие слова. Вопросы, наблюдения и догадки остальных участников сами по себе описанием
  формата не являются.
- Если утверждения команды прошивки расходятся, действует более позднее по дате письма.
  Предложения и планы, которые позже были изменены или отклонены, не действуют.
- `docs/firmware_changelog.md` — краткие релизные заметки прошивки. Если письмо команды
  прошивки, отправленное позже заметки, уточняет или исправляет её, действует письмо.
- Сведения об онлайн-протоколе ВТП, SMS-командах, `tracker.ini` и формате `.vcm` к
  журналу `.vtl` не относятся.
"""


# ---------------------------------------------------------------------------
# Correspondence: people and background text banks
# ---------------------------------------------------------------------------

PEOPLE = {
    "oleg": ("Олег Кравец", "o.kravets@vector-t.ru", "Олег", "прошивка", ("release", "hw", "office")),
    "sergey": ("Сергей Лыков", "s.lykov@vector-t.ru", "Сергей", "схемотехника", ("hw", "field", "office")),
    "dina": ("Дина Ахмерова", "d.akhmerova@vector-t.ru", "Дина", "прошивка", ("release", "tools", "office")),
    "marina": ("Марина Белова", "m.belova@vector-t.ru", "Марина", "бэкенд", ("backend", "release", "office")),
    "pavel": ("Павел Рудаков", "p.rudakov@vector-t.ru", "Павел", "инструменты", ("tools", "backend", "office")),
    "timur": ("Тимур Галеев", "t.galeev@vector-t.ru", "Тимур", "внедрение", ("field", "office", "release")),
    "irina": ("Ирина Шевчук", "i.shevchuk@vector-t.ru", "Ирина", "руководитель проекта",
              ("release", "field", "office")),
    "gleb": ("Глеб Орлов", "g.orlov@vector-m.ru", "Глеб", "«Вектор-М»", ("vendor", "office", "hw")),
    "artem": ("Артём Зуев", "a.zuev@vector-t.ru", "Артём", "прошивка", ("hw", "release", "office")),
    "vera": ("Вера Ким", "v.kim@translogistic.ru", "Вера", "интегратор «ТрансЛогистик»", ("field", "office", "field")),
}

SLOTS = {
    "city": ("Казани", "Екатеринбурге", "Новосибирске", "Самаре", "Твери", "Туле", "Ярославле", "Краснодаре",
             "Перми", "Уфе", "Владивостоке", "Омске", "Калуге", "Вологде"),
    "depot": ("автобазе №3", "складе на Промышленной", "терминале в Химках", "парке «Северный»",
              "площадке у кольцевой", "базе «Юг»", "распределительном центре в Подольске", "гараже на Заводской"),
    "client": ("«ТрансЛогистик»", "«СеверАвто»", "«Городские перевозки»", "«ЭкоВывоз»", "«Фрешмаркет»",
               "«Мостранс-Сервис»", "«Агро-Трейд»", "«Балт-Экспедиция»", "«Уралснаб»"),
    "veh": ("Газелях", "КамАЗах", "Ларгусах", "автобусах", "рефрижераторах", "тягачах", "эвакуаторах", "фургонах"),
    "mod": ("GSM-модуль", "приёмник ГЛОНАСС", "стабилизатор питания", "контроллер заряда", "датчик температуры платы",
            "разъём питания", "модем", "аккумулятор резервного питания", "кварцевый генератор"),
    "day": ("понедельник", "вторник", "среду", "четверг", "пятницу"),
    "weekday": ("в понедельник", "во вторник", "в среду", "в четверг", "в пятницу"),
    "tool": ("стенд с имитатором CAN", "климатическая камера", "осциллограф", "программатор", "вибростол",
             "лабораторный блок питания"),
    "report": ("отчёт по пробегу", "отчёт по стоянкам", "сводка по расходу топлива", "карта треков",
               "отчёт о нарушениях скоростного режима", "реестр путевых листов"),
}

NOISE = {
    "field": (
        "На {depot} поставили ещё партию трекеров ({n} шт.), монтаж занял два дня вместо одного: в половине машин проводка "
        "после прошлых установщиков в таком виде, что проще было перекладывать заново.",
        "Клиент {client} снова спрашивал про сроки, мы пообещали ответ до конца недели, так что давайте не затягивать.",
        "В {city} водители жалуются, что планшет долго грузится на морозе; к трекеру это отношения не имеет, "
        "но жалобы идут к нам.",
        "Из {n} машин клиента {client} на связь не выходят {n2}, судя по всему, их просто не заводили с прошлой недели.",
        "Съездили на {depot}: механики сами переставили два трекера между машинами, не сказав диспетчерам, "
        "поэтому в отчётах одна и та же Газель ездила в двух городах одновременно.",
        "Диспетчер в {city} прислала фотографии щитка, по ним видно, что трекер запитан после замка зажигания, "
        "хотя в инструкции написано подключать напрямую к аккумулятору.",
        "Пилот у клиента {client} продлили ещё на месяц, им понравились отчёты, но просят выгрузку в Excel без наших "
        "служебных колонок.",
        "На {veh} в {city} пломбы на разъёмах сорваны у каждой третьей машины, служба безопасности клиента "
        "обещала разобраться сама.",
        "Отдельно попросили инструкцию для водителей на одну страницу, без технических подробностей, с картинками.",
        "В {city} опять проблемы со связью за городом: на трассе в сторону области покрытие пропадает минут на "
        "двадцать, и в это время трекер просто копит данные.",
        "Установщики из местной фирмы в {city} работают аккуратно, но медленно: не больше {n2} машин в день.",
        "Клиент {client} хочет видеть в веб-интерфейсе номера путевых листов рядом с поездками, записали в бэклог.",
        "Выяснилось, что часть машин клиента {client} зимой стоит в неотапливаемом ангаре, отсюда и странные разряды "
        "резервного аккумулятора по утрам.",
        "Коллеги в {city} просили к следующему визиту привезти запасные кронштейны, старые не подходят к новым "
        "панелям на {veh}.",
        "По итогам недели: закрыто заявок — {n}, ждут выезда — {n2}, одна висит из-за того, что клиент не может "
        "найти машину.",
    ),
    "hw": (
        "Пришла партия плат от контрактника ({n} шт.), на входном контроле отбраковали {n2} по пайке разъёма.",
        "{mod} из новой партии греется сильнее прежнего, в климатической камере при плюс семидесяти уходит в "
        "защиту через сорок минут.",
        "Поменяли поставщика корпусов, у новых чуть другая посадка, прокладка встаёт с перекосом — "
        "договорились, что они поправят пресс-форму.",
        "На вибростоле гоняли три дня, отвалился только держатель SIM-карты, остальное живо.",
        "По питанию: на {veh} скачки при запуске стартера до сорока вольт, защитный диод пока справляется, но "
        "запаса почти нет.",
        "{mod} у китайского поставщика подорожал на треть, ищу замену, пока смотрю два варианта от отечественных "
        "производителей.",
        "Паяльная станция в лаборатории снова барахлит, заказали новую, до прихода пользуемся той, что у "
        "монтажников.",
        "Провели замеры потребления в режиме сна: около {n2} миллиампер, это больше, чем хотелось бы, будем "
        "разбираться, кто не засыпает.",
        "Схему ревизии Г отдали на трассировку, срок — две недели, если трассировщик не заболеет, как в прошлый раз.",
        "Нашли, почему часть трекеров перезагружалась на кочках: люфт в клеммнике, решилось заменой винта на "
        "пружинный зажим.",
        "На испытаниях выяснилось, что при минус тридцати {mod} стартует через раз; поставщик признал проблему, "
        "обещает замену партии.",
        "Для сертификации нужны ещё два образца в корпусе, соберём {weekday}.",
        "Разводку антенны переделали, приём в гараже стал заметно лучше, но всё равно хуже, чем у конкурентов.",
        "Сборочный цех просит заранее предупреждать о смене компонентов хотя бы за месяц.",
    ),
    "release": (
        "План такой: сборка-кандидат будет {weekday}, потом неделя на тестирование у Тимура на пилоте, выпуск — после "
        "подтверждения.",
        "Регрессию по старым сценариям прогнали, упали два теста, оба из-за тестового стенда, а не прошивки.",
        "Давайте в этот раз не выпускать в пятницу: в прошлый раз обновление ушло на {n} машин, а в выходные "
        "некому было смотреть на результат.",
        "Обновление по воздуху раскатываем волнами: сначала десять машин, через сутки — сотня, потом все.",
        "Сроки сдвигаются примерно на неделю, основная причина — ожидание плат новой ревизии.",
        "Список изменений для заказчиков сократили, технические подробности оставили только во внутреннем "
        "описании.",
        "Ветку релиза замораживаем {weekday}, дальше в неё только исправления с моего согласия.",
        "Тестовые трекеры на {depot} обновили до кандидата, пока тихо, но прошло всего двое суток.",
        "На планёрке договорились, что каждый релиз сопровождается записью в changelog в тот же день, а не "
        "через месяц.",
        "Обновление откатили на {n2} машинах, где стоял старый загрузчик; это отдельная история, к формату не "
        "относится.",
        "По бюджету на следующий квартал: закупка плат согласована, командировки — пока нет.",
        "Хочу к концу месяца иметь понятный план на год вперёд, пришлите, пожалуйста, свои оценки по задачам.",
        "Заказчик {client} просит закрепить за ними конкретную версию прошивки и не обновлять без согласования.",
        "Выпуск прошёл спокойно, обращений в поддержку за первую неделю — {n2}, все не про прошивку.",
    ),
    "backend": (
        "База телеметрии разрослась до неприличных размеров, переношу старые данные в холодное хранилище.",
        "{report} у клиента {client} строился почти минуту, добавили индекс, теперь около трёх секунд.",
        "Сервер приёма перезапускали ночью из-за обновления ядра, потерь данных нет, трекеры досылают сами.",
        "Картографическую подложку переключили на другой сервер, у старого истекла лицензия.",
        "Импорт журналов с флешек теперь идёт в очереди, а не синхронно, веб-интерфейс больше не подвисает.",
        "Нашлись дубликаты поездок у клиента {client}: один и тот же журнал загрузили дважды с разными именами файлов.",
        "Для {report} нужна привязка к часовому поясу клиента, сейчас везде московское время, и клиенты в "
        "{city} путаются.",
        "Резервное копирование базы переделали, теперь полная копия раз в неделю и инкрементальные каждую ночь.",
        "Сделали страницу со списком трекеров, которые давно не выходили на связь, Тимур просил.",
        "Мониторинг сервера приёма показывает пики нагрузки в восемь утра, когда все машины разом выезжают.",
        "Выгрузку для бухгалтерии клиента {client} сделали в их формате, но пришлось вручную сопоставить коды машин.",
        "Очередь обработки иногда отстаёт на несколько минут, пока терпимо, но при росте парка станет проблемой.",
        "Переписали пересчёт пробега: раньше суммировали прямые между точками, теперь с учётом пропусков связи.",
        "Логи сервера приёма теперь хранятся тридцать дней, раньше — неделю, места хватает.",
    ),
    "tools": (
        "В vtldump добавили режим, который печатает только заголовки блоков, удобно для быстрой проверки файла.",
        "Написали скрипт, который прогоняет все файлы из архива пилотов и считает, сколько блоков не прошло "
        "проверку.",
        "Сборка инструментов теперь идёт на сервере непрерывной интеграции, бинарники лежат в общей папке.",
        "Тесты для разборщика пока покрывают только заголовок и блоки, до записей руки не дошли.",
        "Из архива пилотов вытащили файлы для регрессии ({n} шт.), сложили в общую папку с описанием, откуда каждый.",
        "Нашли утечку памяти при разборе очень больших файлов, поправили, теперь потребление ровное.",
        "Сделали сравнение двух выгрузок построчно, чтобы видеть, что поменялось после правок в разборщике.",
        "Инструмент теперь умеет читать файл прямо из архива, не распаковывая на диск.",
        "На Windows у диспетчеров всё запускается, но пути с кириллицей пришлось отдельно чинить.",
        "Переименовали ключи в выводе, чтобы они совпадали с названиями в веб-интерфейсе.",
        "Добавили в инструмент проверку, что файл не обрывается посередине блока, и понятное сообщение об этом.",
        "Документацию к инструменту переписали, старая была про версию, которой уже год никто не пользуется.",
        "Скрипт массовой проверки файлов на {n2} тысячах журналов отработал за полчаса.",
    ),
    "office": (
        "Кстати, в {day} у нас общая встреча в переговорке на третьем этаже, кто на удалёнке — по ссылке.",
        "Напоминаю про отпускной график: пришлите даты до конца месяца, иначе поставим сами.",
        "Кофемашину на кухне починили, но просили больше не засыпать в неё молотый кофе.",
        "Меня {weekday} не будет, я на выезде у клиента {client}, по срочным вопросам звоните.",
        "Пропуска на новый этаж выдают у охраны, фотографию нужно принести с собой.",
        "Если кто-то брал с полки в лаборатории {tool} — верните, пожалуйста, он нужен для испытаний.",
        "В пятницу отмечаем день рождения отдела, скидываемся у Ирины.",
        "Переписку по этой теме буду складывать в общую папку, чтобы потом не искать по почтовым ящикам.",
        "Интернет в офисе обещают починить к вечеру, провайдер меняет оборудование.",
        "На следующей неделе я работаю в {city}, связь будет, но с задержками.",
        "Парковку у офиса на выходных будут асфальтировать, машины лучше оставить на соседней улице.",
        "Спасибо всем, кто помог с переездом лаборатории, всё встало на свои места.",
    ),
    "techmisc": (
        "В API бэкенда поле скорости отдаём в км/ч с одним знаком после запятой, мобильное приложение само округляет.",
        "Отладочный UART работает на 115200: восемь бит данных, без чётности, один стоповый бит.",
        "Образ прошивки занимает около 400 килобайт, на флеше под него два слота по 512 килобайт.",
        "В CRM поле «тип договора» теперь обязательное, без него заявка на монтаж не сохраняется.",
        "SMS с кириллицей уходят в UCS-2, поэтому в одно сообщение влезает 70 символов, а не 160.",
        "В базе номер устройства сделали 64-битным: 32 бит хватило бы ещё года на три, не больше.",
        "Обновление по воздуху передаётся кусками по 1024 байта, у каждого куска свой CRC-32.",
        "На сервере приёма широта и долгота в таблице точек хранятся как double, никаких целых и множителей.",
        "Конфигуратор пишет настройки в EEPROM с адреса 0x40, первый байт там — номер ревизии структуры.",
        "В отчёте по топливу веб-интерфейс показывает литры с одним знаком после запятой, так попросили "
        "бухгалтеры.",
        "Для SIM-чипа в регистре модема есть отдельный бит, без него модем ищет обычную SIM-карту.",
        "Во внутреннем JSON-API время отдаём в миллисекундах UTC, а веб-интерфейс сам переводит в пояс клиента.",
        "Архив логов сервера теперь сжимается zstd, на наших объёмах это в разы быстрее gzip.",
        "В очереди сообщений у каждого события есть поле type, по нему воркеры решают, кто его обрабатывает.",
        "Стрелка направления на карте рисуется с шагом в пять градусов, точнее никто не просил.",
        "Модем отдаёт уровень сигнала в условных единицах от 0 до 31, в интерфейсе переводим в полоски.",
        "Загрузчик проверяет подпись образа — 64 байта в самом конце файла обновления.",
        "Номер версии прошивки в служебном пакете — три байта подряд: мажор, минор, патч.",
        "Имитатор CAN на стенде шлёт кадры с идентификатором 0x7DF, как настоящий диагностический сканер.",
        "Ограничение на размер загружаемого через веб-интерфейс файла подняли до 50 мегабайт.",
        "Счётчик перезагрузок хранится в двух байтах резервной памяти часов и переживает отключение питания.",
        "В таблице заявок добавили поле с типом крепления, монтажники просили различать скобу и клей.",
        "На складе трекеры маркируем QR-кодом с серийным номером и датой сборки, без всяких битовых полей.",
        "Серийный номер платы — восемь шестнадцатеричных цифр, первые две означают год выпуска.",
        "Регистр состояния питания читаем раз в секунду, старший бит там — признак работы от резервного "
        "аккумулятора.",
    ),
    "vendor": (
        "У нас в «Вектор-М» тоже выпуск на носу, так что отвечаю урывками.",
        "Наш логгер ставят в основном на спецтехнику, там свои проблемы с питанием и вибрацией.",
        "Общий стенд для проверки CAN-логгера и вашего трекера собрали у нас в лаборатории, приезжайте.",
        "Заказчик {client} хочет видеть данные наших логгеров и ваших трекеров на одной карте.",
        "В наших файлах другая структура, так что общий разборщик пока не получается, только общий просмотрщик.",
        "Руководство просит к концу квартала показать совместный прототип интерфейса.",
        "Поставщик корпусов у нас с вами общий, так что проблемы с посадкой, скорее всего, одинаковые.",
        "Мы перешли на новую версию компилятора, собрали всё без замечаний.",
        "Наши монтажники в {city} готовы помочь вашим, если не хватает рук.",
        "Документацию на наш формат пришлю отдельно, она у нас на английском, так исторически сложилось.",
    ),
}

GREETINGS = ("Коллеги, привет.", "Всем привет!", "Добрый день.", "Привет всем.", "Коллеги, добрый день!",
             "Здравствуйте, коллеги.", "Привет!")
CLOSINGS = ("Спасибо!", "Если что-то непонятно — спрашивайте.", "Хорошего дня.", "На связи.", "Обнимаю, до завтра.",
            "Жду возражений, если они есть.", "Пишите, если нужны подробности.", "Всем удачной недели.")


# ---------------------------------------------------------------------------
# Correspondence: technical paragraphs.  key -> (draft text, judge questions)
# A judge question is (question, answer, options | None); None means a number.
# ---------------------------------------------------------------------------

YES_NO = ("да", "нет")
BYTE_ORDER = ("старший байт первым", "младший байт первым")
FORMATS = (".vtl", ".vcm", "ВТП", "SMS-команды", "tracker.ini")

BLOCKS: dict[str, tuple[str, tuple]] = {
    "X_roles": (
        "По журналу поездок договоримся так: всё, что касается его устройства, решают Олег и Сергей, потому что "
        "формат пишет прошивка. Бэкенд, инструменты и поддержка — потребители журнала: задавайте вопросы, "
        "присылайте наблюдения, но ваши версии — это версии, а не описание формата, пока прошивка их не подтвердила.",
        (("Кто по письму решает вопросы устройства журнала?",
          "команда прошивки", ("команда прошивки", "бэкенд", "поддержка")),),
    ),
    "P_coords_q": (
        "Прогнал vtldump по выгрузкам с машин, которые точно ездят по Москве. Первые два четырёхбайтовых числа "
        "в записи с координатами делю, как велит документ, на сто тысяч — и получаю широту за пятьсот градусов. "
        "Мне кажется, документ прав, а в трекере ошибка масштаба; или я неправильно режу запись. Подскажите, куда "
        "копать, пока я не переписал полпрограммы.",
        (("На какое число Павел делит сырое значение координаты?", "100000", None),
         ("Утверждает ли Павел, что знает правильный масштаб?", "нет", YES_NO)),
    ),
    "P_crc_guess": (
        "И второе: контрольные суммы блоков у меня не сходятся почти никогда. Читаю два последних байта блока "
        "младшим вперёд, как все прочие числа в формате, считаю CCITT-FALSE по нагрузке — мимо. Пробовал по "
        "распакованным данным — тоже мимо. Совпадает изредка, видимо, случайно.",
        (("В каком порядке байтов Павел читает контрольную сумму?", "младший байт первым", BYTE_ORDER),),
    ),
    "F_coords": (
        "Павел, по координатам ты упёрся в ошибку документа, трекер тут ни при чём. Оба четырёхбайтовых поля в "
        "начале записи с точкой — это градусы, умноженные на миллион, а не на сто тысяч: шесть знаков после "
        "запятой, ровно как отдаёт приёмник. Отсюда и твоя Москва за пятьсот градусов. Знак прежний: юг и "
        "запад — с минусом.",
        (("На какое число по письму надо делить сырое значение координаты, чтобы получить градусы?",
          "1000000", None),),
    ),
    "F_crc_be": (
        "С суммой проще. Два байта, которыми заканчивается каждый блок, в отличие от остальных многобайтовых "
        "чисел файла лежат старшим байтом вперёд: их так выдаёт аппаратный вычислитель CRC в контроллере, и никто "
        "не стал переворачивать. Считается она, как и написано, по нагрузке в том виде, в каком та лежит в файле, "
        "то есть для сжатого блока — по сжатым байтам.",
        (("В каком порядке байтов по письму хранится контрольная сумма блока?", "старший байт первым",
          BYTE_ORDER),),
    ),
    "P_heading_q": (
        "По записи со скоростью: байт, который идёт после двухбайтовой скорости, в документе назван курсом в "
        "градусах. Но ни в одном файле я не видел там значения больше 179, хотя машины явно ездят и на запад. "
        "Если это градусы, то половины направлений просто нет. Есть версия, что трекер как-то ограничивает "
        "курс, но звучит странно.",
        (("Какое наибольшее значение этого байта видел Павел?", "179", None),),
    ),
    "P_rpm_q": (
        "И по записи двигателя: первое двухбайтовое поле на холостом ходу у меня около 3200, на трассе доходит "
        "до десяти-двенадцати тысяч. Для оборотов в минуту многовато, если это не спорткар. Пока вывожу как есть.",
        (("Какое значение первого поля Павел видит на холостом ходу?", "3200", None),),
    ),
    "F_heading2": (
        "Байт после скорости — да, направление движения, но одна его единица — это два градуса: иначе полный "
        "круг в байт не влезает. Значит, 179 — это 358°, а 45 — северо-восток. Ничего не ограничивается, просто "
        "шаг грубый.",
        (("Сколько градусов по письму соответствует единице сырого значения направления?", "2", None),),
    ),
    "F_rpm4": (
        "Первое поле записи двигателя мы не пересчитываем: кладём то, что блок управления отдаёт по "
        "диагностике, а он считает в четвертях оборота. Твои 3200 на холостых — это 800 об/мин. Делить на "
        "четыре, дробная часть допустима.",
        (("На какое число по письму надо делить первое поле записи двигателя, чтобы получить об/мин?",
          "4", None),),
    ),
    "T_mojibake": (
        "Коллеги, диспетчеры присылают выгрузки, где вместо заметок водителей сплошные вопросики и ромбики. Наш "
        "просмотрщик открывает текст как юникод — по документу. Название машины в шапке файла тоже иногда "
        "превращается в ромбики, если в нём кириллица. Латиница и цифры при этом читаются нормально. Не знаю, "
        "чья это проблема — наша или трекера.",
        (("Сообщает ли автор, в какой кодировке трекер пишет текст?", "нет", YES_NO),),
    ),
    "F_cp1251": (
        "Тимур, это документ ошибается, а не просмотрщик. Планшет водителя отдаёт текст в однобайтовой "
        "кириллице — той самой, что в Windows у диспетчеров, кодовая страница 1251, — и трекер пишет его без "
        "перекодирования. Название машины в шапку вводят через программу настройки на том же Windows, так что "
        "оно в той же кодировке. Юникода в журнале нет вообще.",
        (("В какой кодировке по письму записаны заметки и название машины?", "cp1251",
          ("cp1251", "utf-8", "koi8-r")),),
    ),
    "P_status_q": (
        "Разбираю записи с кодами неисправностей. Буквы и цифры собираются, как в документе, а вот байт "
        "статуса смущает: у большинства кодов с горящей лампой стоит самый младший бит, а по документу "
        "младший — «ожидающий». Ожидающий код с горящей лампой бывает, но не у всех же подряд?",
        (("Какой бит, по наблюдению Павла, стоит у большинства кодов с горящей лампой?", "бит 0",
          ("бит 0", "бит 1", "бит 2")),),
    ),
    "F_status_wrong": (
        "Павел, по статусу в документе всё верно: самый младший бит — код ещё не подтверждён и ждёт следующего "
        "цикла, соседний с ним — подтверждён, третий снизу — лампа. То, что у тебя с лампой почти всегда "
        "младший бит, — особенность машин, которые ты смотришь, на старых блоках управления так бывает.",
        (("Что по этому письму означает бит 0 байта статуса?", "ожидающий",
          ("подтверждённый", "ожидающий", "лампа MIL")),),
    ),
    "F_coolant40": (
        "По температуре охлаждающей жидкости, пока Павел не спросил: в документе записан знаковый байт в "
        "градусах, это неправда с первой серийной прошивки. Байт беззнаковый и сдвинут на сорок: ноль в нём — "
        "минус сорок, 130 — девяносто. Так отдаёт сама диагностика, мы кладём как есть. Отрицательных значений "
        "в знаковом смысле там не ищите.",
        (("Сколько по письму надо вычесть из сырого значения температуры, чтобы получить °C?", "40", None),),
    ),
    "F_status_fix": (
        "Возвращаюсь к вопросу Павла про статус кодов. Олег, ты ответил по документу, а я полез в код драйвера "
        "диагностики, который этот байт собирает: самым младшим битом он ставит «подтверждён», следующим — "
        "«ожидает подтверждения». Лампа — третий снизу, тут всё сходится. В документе первые два переставлены, "
        "и ты, похоже, ответил по нему.",
        (("Что по этому письму означает бит 0 байта статуса?", "подтверждённый",
          ("подтверждённый", "ожидающий", "лампа MIL")),
         ("Что по этому письму означает бит 1 байта статуса?", "ожидающий",
          ("подтверждённый", "ожидающий", "лампа MIL"))),
    ),
    "F_status_confirm": (
        "Да, Сергей прав, я смотрел не ту ветку: в прототипе порядок был как в документе, в серийном коде его "
        "поменяли. Моё мартовское письмо в части статуса забудьте.",
        (("Чью версию по статусу подтверждает автор?", "Сергея", ("Сергея", "документа", "Павла")),),
    ),
    "F_speed_ffff": (
        "В 1.3 поменялось поведение при отвале датчика скорости. Раньше, если модуль не ответил, трекер писал "
        "ноль, и в отчётах машина «стояла» посреди трассы. Теперь в таком случае в двух байтах скорости все "
        "биты — единицы. Это не 655 км/ч, это «нет данных». Байт направления в той же записи при этом честный, "
        "он от приёмника.",
        (("Какое сырое значение скорости по письму означает «нет данных»?", "65535", None),),
    ),
    "M_speed_guess": (
        "Сергей, я правильно понимаю, что в старых файлах, до 1.3, «нет данных» — это ноль, и такие скорости "
        "надо выводить пустыми, а не нулём?",
        (("Какое значение Марина предлагает считать отсутствием данных в старых файлах?", "0", None),),
    ),
    "F_speed_zero": (
        "Нет, ноль — это ноль, стоящая машина, в любых файлах. «Нет данных» — только когда все биты единицы. В "
        "старых прошивках такого значения не бывает, но и ноль в них честный.",
        (("Как по письму трактовать нулевую скорость?", "стоянка, 0 км/ч", ("стоянка, 0 км/ч", "нет данных")),),
    ),
    "F_hdop": (
        "С 1.4 запись с точкой стала длиннее на два байта: в самом конце, после числа спутников, — "
        "геометрический фактор точности в том виде, как его отдаёт приёмник, беззнаковым двухбайтовым. Трекеры "
        "на более старых прошивках этих байтов не пишут, так что по длине записи видно, есть они или нет. "
        "Остальная раскладка не менялась.",
        (("Сколько байт по письму добавлено в конец записи?", "2", None),),
    ),
    "P_hdop_q": (
        "Олег, а масштаб какой? Вижу значения от пятидесяти до четырёхсот. Я бы предположил десятые, но тогда "
        "точность сорок — это совсем плохо, а спутников при этом по десять. Или приёмник так врёт?",
        (("Какой масштаб в первую очередь предполагает Павел?", "десятые", ("десятые", "сотые", "целые")),),
    ),
    "F_hdop_units": (
        "Павел, там сотые: 150 — это полтора. Приёмник отдаёт фактор с двумя знаками после запятой, мы умножаем "
        "на сто и кладём целым. Где хвоста нет, точность неизвестна — подставлять ноль не надо, это враньё.",
        (("На какое число по письму делить сырое значение?", "100", None),),
    ),
}

BLOCKS.update({
    "F_v2_tick": (
        "Коллеги, с 2.0 журнал становится второй версии — это видно по четвёртому байту файла. В шапке почти "
        "всё по-старому, но сразу за байтом флагов вставляем два байта, младшим вперёд: сколько миллисекунд "
        "длится один «тик». Длина названия машины и само название из-за этого уезжают на два байта дальше.",
        (("Сколько байт по письму вставлено в заголовок после байта флагов?", "2", None),
         ("В каких единицах это значение?", "миллисекунды", ("миллисекунды", "микросекунды", "секунды"))),
    ),
    "F_v2_ticks": (
        "Зачем это нужно: во второй версии все времена внутри блоков — и отсчёт в начале нагрузки, и приращения "
        "у записей — идут в тиках, а не в миллисекундах, так приращения короче. Чтобы получить миллисекунды, "
        "умножаем на длину тика из шапки. Базовое время файла по-прежнему в секундах.",
        (("В каких единицах по письму база блока во второй версии?", "тики", ("тики", "миллисекунды", "секунды")),),
    ),
    "M_v2_delta_guess": (
        "Олег, раз приращения теперь бывают со знаком, как вы их кодируете — дополнительным кодом внутри varint? "
        "Тогда минус единица займёт десять байт, это же хуже, чем было.",
        (("Какой способ кодирования предполагает Марина?", "дополнительный код",
          ("дополнительный код", "zigzag", "отдельный байт знака")),),
    ),
    "F_v2_zigzag": (
        "Нет, не дополнительным кодом. Приращение времени во второй версии может быть отрицательным — модуль "
        "навигации иногда отдаёт точку с опозданием, и она ложится после более свежих, — поэтому знак "
        "переносим в младший бит, как sint в protobuf: 0 → 0, −1 → 1, 1 → 2, −2 → 3 и так далее, а дальше "
        "обычный varint. Записи в выгрузке оставляйте в том порядке, в каком они лежат в блоке, сортировать "
        "ничего не надо.",
        (("Каким числом по письму кодируется приращение −1 до записи в varint?", "1", None),
         ("В каком порядке по письму выводить записи?", "как в файле", ("как в файле", "по времени"))),
    ),
    "M_gps_delta_proposal": (
        "Раз уж экономим байты: координаты тоже можно писать разностью с предыдущей точкой, это почти всегда "
        "пара сотен единиц. Будет раза в три компактнее. Может, заодно во второй версии?",
        (("Что предлагает автор?", "разностное кодирование координат",
          ("разностное кодирование координат", "сжатие всех блоков", "отказ от высоты")),),
    ),
    "F_gps_delta_rejected": (
        "Марина, идея хорошая, но в 2.0 её не будет — не успеваем с тестами. Во второй версии точка пишется "
        "целиком, как в первой: по четыре байта на каждую ось. Вернёмся к этому в следующей версии формата.",
        (("Как по письму записаны координаты во второй версии?", "целиком",
          ("целиком", "разностью с предыдущей точкой")),),
    ),
    "F_battery_ma": (
        "Новые записи второй версии. Шестой тип — питание: сначала два байта напряжения на клеммах в "
        "милливольтах, затем два байта тока со знаком в миллиамперах; минус — разряд, плюс — заряд.",
        (("В каких единицах по этому письму ток в шестом типе?", "мА", ("мА", "10 мА", "А")),),
    ),
    "F_trip": (
        "Седьмой тип — события поездки: сначала номер поездки varint-ом, потом байт события — ноль старт, "
        "единица стоп, двойка пауза — и в конце четыре байта одометра в метрах, младшим вперёд.",
        (("В каких единицах по письму одометр?", "метры", ("метры", "километры", "сотни метров")),
         ("Каким кодом по письму обозначена пауза?", "2", None)),
    ),
    "F_battery_fix": (
        "Олег, по току поправлю: датчик на шунте отдаёт значение в десятках миллиампер, и прошивка кладёт его "
        "без пересчёта. Так что единица в тех двух байтах — десять миллиампер, −150 — это полтора ампера "
        "разряда. Напряжение — да, милливольты.",
        (("Сколько миллиампер по письму в единице поля тока?", "10", None),),
    ),
    "F_battery_confirm": (
        "Сергей прав, я переписал из черновика ТЗ. В шестом типе ток в единицах по десять миллиампер.",
        (("Сколько миллиампер по письму в единице поля тока?", "10", None),),
    ),
    "M_event3": (
        "В событиях поездки на тестовом стенде вижу значение 3, в списке его нет. Это ошибка прошивки или я "
        "что-то не так разбираю?",
        (("Какое значение события увидела Марина?", "3", None),),
    ),
    "F_trip_resume": (
        "Не ошибка: в последней сборке добавили продолжение поездки после паузы, у него код 3. Полный список: "
        "0 старт, 1 стоп, 2 пауза, 3 продолжение.",
        (("Какой код по письму у продолжения поездки?", "3", None),),
    ),
    "F_oil": (
        "Про совместимость, чтобы потом никто не удивлялся. С 2.0 в записи двигателя после байта с флажками "
        "может идти ещё один байт — температура масла, если датчик подключён; кодируется так же, как "
        "температура охлаждающей жидкости. Нет датчика — нет байта, запись короче. Конец записи надо "
        "определять по её длине, а не по таблице размеров.",
        (("Сколько байт по письму может добавиться в конец записи двигателя?", "1", None),),
    ),
    "P_oil_guess": (
        "Сергей, «так же, как охлаждайка» — это значит просто градусы? Я бы в vtldump вывел как есть.",
        (("Как Павел собирается выводить этот байт?", "как есть", ("как есть", "со сдвигом")),),
    ),
    "F_oil_offset": (
        "Нет, «так же» — это со сдвигом: вычесть сорок. Байт 130 — девяносто градусов масла.",
        (("Сколько по письму надо вычесть из сырого значения?", "40", None),),
    ),
    "F_debug_types": (
        "Напоминание: записи девятого и двенадцатого типа в файлах второй версии — это отладочные дампы из "
        "тестовых сборок, формат не фиксирован, расшифровывать их не нужно. В серийных трекерах их быть не "
        "должно, но на пилотах попадаются.",
        (("Нужно ли по письму расшифровывать двенадцатый тип в файлах второй версии?", "нет", YES_NO),),
    ),
    "F_throttle255": (
        "Ещё одно изменение 2.0, о котором забыли написать: байт положения педали в записи двигателя теперь "
        "использует весь диапазон — 255 означает педаль в пол, ноль — отпущенную. Процент получается как "
        "значение, умноженное на сто и делённое на 255. Раньше там было сразу 0–100.",
        (("Какое сырое значение по письму соответствует 100 %?", "255", None),),
    ),
    "M_throttle_q": (
        "Сергей, это для всех файлов? У меня в базе лежат журналы первой версии, их тоже пересчитывать?",
        (("Задаёт ли автор вопрос или утверждает факт?", "вопрос", ("вопрос", "утверждение")),),
    ),
    "F_throttle_v1": (
        "Только для второй версии журнала. В файлах первой версии байт педали — сразу проценты, 0–100, "
        "пересчитывать не надо.",
        (("Как по письму трактовать этот байт в файлах первой версии?", "проценты 0–100",
          ("проценты 0–100", "шкала 0–255")),),
    ),
    "F_changelog_typo": (
        "Кто-то спрашивал про строку в changelog к 2.1 — «ток АКБ теперь в мА». Это опечатка при сборке "
        "релизных заметок: в 2.1 поменяли фильтрацию тока, а не единицы. Единица по-прежнему десять "
        "миллиампер, во всех файлах второй версии.",
        (("Сколько миллиампер по письму в единице поля тока во второй версии?", "10", None),),
    ),
    "X_dina": (
        "Представляю Дину Ахмерову: с этой недели она в команде прошивки и будет вести журнал вместе с Олегом. "
        "По вопросам формата её ответы так же окончательны, как ответы Олега и Сергея.",
        (("В какую команду вошла Дина?", "прошивка", ("прошивка", "бэкенд", "поддержка")),),
    ),
})

BLOCKS.update({
    "F_zone_proposal": (
        "Планы на 3.0 по журналу. Первое: заказчики просят местное время. Предлагаю хранить в шапке базовое "
        "время по местным часам парка, а сразу за длиной тика — смещение пояса в минутах, два байта со знаком.",
        (("Какой размер поля смещения предлагается (в байтах)?", "2", None),
         ("В каких единицах предлагается смещение?", "минуты", ("минуты", "четверти часа", "часы"))),
    ),
    "F_crc32_proposal": (
        "Второе: хочу у блоков перейти на CRC-32, шестнадцати бит на больших сжатых блоках маловато.",
        (("Какую контрольную сумму предлагает автор?", "CRC-32", ("CRC-32", "CRC-16", "CRC-8")),),
    ),
    "F_utf8": (
        "Третье, это уже решено: в третьей версии все строки — и название машины, и заметки водителя — пишем в "
        "UTF-8, новый планшет по-другому не умеет. В файлах первой и второй версии, понятно, всё остаётся как "
        "было.",
        (("В какой кодировке по письму строки в третьей версии?", "utf-8", ("cp1251", "utf-8", "koi8-r")),),
    ),
    "F_gps_delta_v3": (
        "Олег, добавлю про координаты, раз уж обещали Марине ещё в двадцать первом. В третьей версии запись с "
        "точкой начинается (после приращения времени) не с двух четырёхбайтовых чисел, а с двух приращений к "
        "предыдущей такой же записи в этом же блоке — varint со знаком, тем же способом, что и приращение "
        "времени. Первая точка в блоке считается от нуля, то есть фактически несёт полную координату. Масштаб "
        "прежний, миллионные доли. После них — высота двумя байтами, спутники одним и фактор точности двумя, "
        "теперь всегда.",
        (("От чего по письму отсчитывается первая точка блока?", "от нуля",
          ("от нуля", "от последней точки предыдущего блока", "от базы блока")),
         ("Сколько байт по письму занимает фактор точности?", "2", None)),
    ),
    "F_harsh": (
        "Новые записи третьей версии. Восьмой тип — тот, что в старом документе висел зарезервированным под "
        "акселерометр, — наконец используется: события резкого вождения. Первый байт после приращения времени — "
        "вид события: 0 — торможение, 1 — разгон, 2 — поворот, 3 — удар (кочка, яма). Затем два байта пикового "
        "ускорения в тысячных долях g и два байта длительности в миллисекундах.",
        (("Какой вид события по письму обозначен кодом 2?", "поворот", ("торможение", "разгон", "поворот", "удар")),
         ("В каких единицах по этому письму длительность?", "мс", ("мс", "10 мс", "с"))),
    ),
    "F_fuel_proposal": (
        "Двенадцатый номер, который раньше занимали отладочные дампы, отдаём под датчик уровня в баке: один "
        "байт, шаг полпроцента.",
        (("Какой шаг уровня (в процентах) предлагается?", "0.5", None),),
    ),
    "F_tires_proposal": (
        "Десятый тип — давление в шинах по данным беспроводных датчиков: байт с числом колёс, затем по три "
        "байта на колесо — где стоит, давление, температура. В первом из трёх байтов старшая тетрада — номер "
        "оси, считая от передней с единицы, младшая — место на оси: 0 — левое внешнее, 1 — левое внутреннее, "
        "2 — правое внутреннее, 3 — правое внешнее. Давление — в шагах по 2,5 кПа, температура — байтом.",
        (("Что по письму означает младшая тетрада 2?", "правое внутреннее",
          ("левое внешнее", "левое внутреннее", "правое внутреннее", "правое внешнее")),
         ("Какой шаг давления (кПа) по этому письму?", "2.5", None)),
    ),
    "M_tire_temp_guess": (
        "Дина, температура шины — со сдвигом на сорок, как у охлаждающей жидкости? Иначе зимой не влезет.",
        (("Какой сдвиг предполагает Марина?", "40", None),),
    ),
    "F_tire_temp_signed": (
        "Нет, здесь байт знаковый, градусы как есть, без сдвига: от −128 до 127 шинам хватает с запасом.",
        (("Какой сдвиг по письму у температуры шины?", "0", None),),
    ),
    "F_doors_initial": (
        "Одиннадцатый тип — двери. Один байт после приращения времени, по биту на дверь: бит 0 — водительская, "
        "бит 1 — пассажирская, бит 2 — задние распашные, бит 3 — боковая сдвижная; единица — открыта. Старший "
        "бит — сработала охранная сигнализация. Остальные биты пока нули.",
        (("Какая дверь по этому письму соответствует биту 2?", "задние распашные",
          ("водительская", "пассажирская", "задние распашные", "боковая сдвижная")),
         ("Что по письму означает старший бит?", "сигнализация",
          ("сигнализация", "зажигание", "багажник"))),
    ),
    "F_harsh_duration_fix": (
        "Дина, про восьмой тип: длительность датчик отдаёт не в миллисекундах, а в сотых долях секунды, и мы её "
        "не пересчитываем. Проверил на стенде: удар в 120 мс пишется как 12.",
        (("Сколько миллисекунд по письму в единице длительности?", "10", None),),
    ),
    "F_zone_final": (
        "Что в итоге ушло в 3.0 — журнал третьей версии. Шапка: за двумя байтами тика — один знаковый байт "
        "смещения пояса, но не в минутах, как я предлагал осенью, а в четвертях часа; базовое время в шапке — "
        "по местным часам парка. Длина названия машины и название идут после этого байта.",
        (("Сколько байт по письму занимает смещение пояса?", "1", None),
         ("В каких единицах по письму смещение?", "четверти часа", ("минуты", "четверти часа", "часы"))),
    ),
    "F_crc_v3": (
        "Контрольная сумма: от CRC-32 отказались, остался тот же шестнадцатибитный алгоритм, но теперь сумма "
        "лежит младшим байтом вперёд, как всё остальное, и считается не только по нагрузке, а по байтам блока "
        "от байта флагов до конца нагрузки — то есть флаги и длина тоже защищены. Маркер в сумму не входит.",
        (("В каком порядке байтов по письму хранится сумма в третьей версии?", "младший байт первым", BYTE_ORDER),
         ("По каким байтам по письму считается сумма?", "флаги, длина и нагрузка",
          ("только нагрузка", "флаги, длина и нагрузка", "маркер, флаги, длина и нагрузка"))),
    ),
    "F_v3_rest": (
        "Всё остальное — запись скорости, двигателя (педаль во всю ширину байта, возможный байт масла), коды "
        "неисправностей, питание, поездки — как во второй версии, приращения времени со знаком и в тиках.",
        (("Как по письму кодируются приращения времени в третьей версии?", "со знаком, в тиках",
          ("со знаком, в тиках", "без знака, в миллисекундах")),),
    ),
    "F_fuel_final": (
        "Бак: вместо байта в полпроцента сделали два двухбайтовых поля — уровень в десятых долях процента и "
        "объём в десятых долях литра по тарировочной таблице; если таблицы для машины нет, во втором поле все "
        "единицы.",
        (("В каких долях процента по письму уровень?", "0.1", None),
         ("Какое сырое значение объёма по письму означает отсутствие таблицы?", "65535", None)),
    ),
    "P_zone_q": (
        "А как из этого получить UTC? Прибавить смещение к базовому времени?",
        (("Какую операцию предлагает Павел?", "прибавить", ("прибавить", "вычесть")),),
    ),
    "F_zone_sign": (
        "Павел, наоборот. В шапке местное время, а смещение — на сколько пояс впереди UTC. Значит, UTC = местное "
        "минус смещение. Москва пишет +12 — двенадцать четвертей, три часа, — и из базового времени надо "
        "вычесть 10800 секунд. Все отметки записей считаются от базового времени, так что после поправки они "
        "тоже в UTC.",
        (("Сколько секунд по письму вычесть при смещении +12?", "10800", None),),
    ),
    "F_tires_final": (
        "По шинам: шаг 2,5 кПа, который был в плане, в релиз не попал — грузовые шины качают до 900 кПа, в байт "
        "с таким шагом не влезает. В 3.0 шаг давления — 4 кПа. Остальная раскладка десятого типа — как писала Дина.",
        (("Какой шаг давления (кПа) по письму ушёл в релиз?", "4", None),),
    ),
    "F_doors_fix": (
        "Нашли неприятность с дверями. На плате 3.0 входы задней и боковой двери разведены наоборот, а прошивку "
        "править не стали — дешевле договориться. Поэтому на деле бит 2 в записи дверей загорается, когда "
        "открыта боковая сдвижная, а бит 3 — когда задние распашные. Водительская, пассажирская и сигнализация — "
        "как было.",
        (("Какая дверь по письму на деле соответствует биту 2?", "боковая сдвижная",
          ("водительская", "пассажирская", "задние распашные", "боковая сдвижная")),),
    ),
    "M_doors_q": (
        "То есть описание Дины от ноября в этой части неверное, и в выгрузке надо называть реальную дверь, а не "
        "ту, что написана у бита?",
        (("Задаёт ли автор вопрос или утверждает факт?", "вопрос", ("вопрос", "утверждение")),),
    ),
    "F_doors_confirm": (
        "Да. Выгрузка должна называть ту дверь, которая реально открыта: бит 2 — боковая, бит 3 — задняя. Моё "
        "ноябрьское письмо в этой части считайте устаревшим.",
        (("Какая дверь по письму соответствует биту 3?", "задние распашные",
          ("водительская", "пассажирская", "задние распашные", "боковая сдвижная")),),
    ),
    "T_fuel_obs": (
        "По-моему, объём в баке пишется в целых литрах: на Газелях вижу во втором поле значения около "
        "шестидесяти-семидесяти, похоже на правду. Но я не уверен, это на глаз.",
        (("Какой масштаб объёма предполагает автор?", "целые литры", ("целые литры", "десятые литра")),),
    ),
    "F_type12_v3": (
        "Уточнение про двенадцатый тип, раз были вопросы: бак — только в файлах третьей версии. В журналах "
        "первой и второй версии двенадцатый по-прежнему отладочный мусор, выводим его как неизвестный, даже если "
        "файл записан уже после выхода 3.0 — старые прошивки ещё ездят.",
        (("Как по письму выводить двенадцатый тип в файле второй версии?", "как неизвестный",
          ("как неизвестный", "как бак")),),
    ),
    "F_gps_hdop_always": (
        "И по точке: в третьей версии два байта фактора точности есть всегда, единица — сотая, как и раньше.",
        (("Какая по письму единица фактора точности?", "0.01", None),),
    ),
    "F_tick_us_rejected": (
        "Про идею из 3.1 считать тик в микросекундах: собрали, посмотрели и откатили до релиза. Длина тика в "
        "шапке по-прежнему в миллисекундах, во всех версиях.",
        (("В каких единицах по письму длина тика?", "миллисекунды", ("миллисекунды", "микросекунды")),),
    ),
})

BLOCKS.update({
    "D_vcm1": (
        "Раз вы разбираете свой журнал, поделюсь, как устроен наш .vcm у CAN-логгера, — вдруг пригодится для "
        "общего просмотрщика. Координаты у нас в десятимиллионных долях градуса, курс — двумя байтами в сотых "
        "долях градуса, температуру пишем знаковым байтом как есть, без сдвигов. Контрольная сумма — CRC-32, "
        "младшим байтом вперёд. С вашим журналом совместима только сигнатура в начале файла.",
        (("К какому формату относятся эти сведения?", ".vcm", FORMATS),
         ("На какое число делить координату в этом формате?", "10000000", None)),
    ),
    "D_vtp1": (
        "Для справки, как это выглядит в онлайн-канале, чтобы не путать. Пакет ВТП начинается с байта 0x7E, "
        "второй байт после маркера — номер пакета, потом длина двумя байтами, в конце — CRC-8. Ток и "
        "напряжение в онлайн-пакетах приходят в миллиамперах и милливольтах, температура — уже в градусах, без "
        "сдвига, координаты — в стотысячных долях, как в старом документе на журнал; может, оттуда и ошибка в "
        "документе.",
        (("К какому протоколу относятся эти сведения?", "ВТП", FORMATS),
         ("В каких единицах ток в этом протоколе?", "мА", ("мА", "10 мА", "А"))),
    ),
    "D_sms": (
        "По SMS-командам для монтажников: STATUS# возвращает строку через запятую, третий параметр в ней — "
        "напряжение питания в десятых долях вольта, четвёртый — число спутников, пятый — температура платы со "
        "сдвигом на пятьдесят. Команда RESET# перезагружает трекер без потери журнала.",
        (("К чему относятся эти сведения?", "SMS-команды", FORMATS),
         ("Какой сдвиг у температуры платы в ответе?", "50", None)),
    ),
    "D_vcm2": (
        "У нас в логгере в этом году тоже появилась точность решения, но одним байтом, в десятых, и в начале "
        "записи, перед координатами. Высоту мы при этом выкинули совсем: для спецтехники она никому не нужна.",
        (("К какому формату относятся эти сведения?", ".vcm", FORMATS),),
    ),
    "D_ini": (
        "По конфигурации: в tracker.ini в секции [log] первый параметр — период сброса блока в секундах, второй — "
        "максимальное число записей в блоке, третий — уровень сжатия от нуля до девяти. Ставить сжатие выше "
        "шести смысла нет, выигрыш копеечный, а процессор греется.",
        (("К чему относятся эти сведения?", "tracker.ini", FORMATS),),
    ),
    "D_vtp2": (
        "В онлайн-пакете ВТП-2 ток аккумулятора идёт в миллиамперах, напряжение — в сотых долях вольта, а "
        "одометр — в сотнях метров; на бэкенде всё пересчитываем при приёме. С журналом не путайте: там свои "
        "единицы, это другой канал и другой код.",
        (("К какому протоколу относятся эти сведения?", "ВТП", FORMATS),
         ("В каких единицах одометр в этом протоколе?", "сотни метров", ("метры", "сотни метров", "километры"))),
    ),
    "D_vcm3": (
        "Про двери у нас в логгере, раз вы тоже за них взялись: один байт, бит 0 — задняя дверь, бит 1 — боковая, "
        "бит 2 — водительская, бит 3 — пассажирская, бит 4 — капот. Так исторически сложилось на спецтехнике, "
        "менять не будем.",
        (("К какому формату относятся эти сведения?", ".vcm", FORMATS),
         ("Какая дверь в этом формате у бита 0?", "задняя", ("задняя", "боковая", "водительская", "пассажирская"))),
    ),
    "D_vtp3": (
        "В ВТП-3 часовой пояс передаём в каждом пакете, одним байтом в часах, а время — сразу в UTC, так что "
        "онлайн-данные пересчитывать не нужно. Давление в шинах по онлайн-каналу идёт в десятых долях бара.",
        (("К какому протоколу относятся эти сведения?", "ВТП", FORMATS),
         ("В каких единицах пояс в этом протоколе?", "часы", ("минуты", "четверти часа", "часы"))),
    ),
    "D_vcm4": (
        "У нас в .vcm бак пишется одним байтом в процентах, объёма нет вообще, а резкие манёвры — тремя осями "
        "по два байта в сотых долях g. Если будете делать общий отчёт, учтите, что точность у нас грубее.",
        (("К какому формату относятся эти сведения?", ".vcm", FORMATS),),
    ),
    "D_sms2": (
        "Добавили SMS-команду TPMS#: в ответе давление каждого колеса в килопаскалях целым числом и температура "
        "со сдвигом на сорок, колёса перечисляются по часовой стрелке от левого переднего.",
        (("К чему относятся эти сведения?", "SMS-команды", FORMATS),),
    ),
    "D_ini2": (
        "В tracker.ini появилась секция [zone]: смещение пояса в минутах, которое трекер показывает на экране "
        "планшета. На журнал это никак не влияет, это только для водителя.",
        (("К чему относятся эти сведения?", "tracker.ini", FORMATS),),
    ),
})

NO_FACTS_Q = ("Сообщает ли письмо что-либо об устройстве, единицах или кодах полей журнала .vtl?", "нет", YES_NO)

# additional layout questions for the judge (field order, position, units)
EXTRA_Q: dict[str, tuple] = {
    "F_hdop": (("Где по письму стоят добавленные байты?", "в конце записи, после числа спутников",
                ("в конце записи, после числа спутников", "перед координатами", "после высоты")),),
    "F_v2_ticks": (("Как по письму получить миллисекунды из значения в тиках?", "умножить на длину тика",
                    ("умножить на длину тика", "разделить на длину тика")),),
    "F_battery_ma": (("В каких единицах по письму напряжение?", "мВ", ("мВ", "10 мВ", "В")),
                     ("Что идёт в записи первым?", "напряжение", ("напряжение", "ток"))),
    "F_trip": (("Что идёт в записи первым?", "номер поездки", ("номер поездки", "событие", "одометр")),),
    "F_oil": (("После какого байта по письму может идти добавленный байт?", "байт флагов",
               ("байт флагов", "байт оборотов", "байт педали")),),
    "F_gps_delta_v3": (("Как по письму кодируются приращения координат?", "varint со знаком",
                        ("varint со знаком", "четыре байта со знаком", "два байта со знаком")),),
    "F_harsh": (("Что по письму идёт в записи раньше?", "пиковое ускорение", ("пиковое ускорение", "длительность")),
                ("Какая доля g в единице пикового ускорения?", "0.001", None)),
    "F_tires_proposal": (("Что по письму обозначает старшая тетрада первого байта колеса?", "номер оси",
                          ("номер оси", "место на оси")),
                         ("Сколько байт по письму приходится на одно колесо?", "3", None)),
    "F_doors_initial": (("Какая дверь по этому письму соответствует биту 3?", "боковая сдвижная",
                         ("водительская", "пассажирская", "задние распашные", "боковая сдвижная")),),
    "F_zone_final": (("Каким по письму является базовое время в заголовке третьей версии?", "местное время",
                      ("местное время", "UTC")),),
    "F_fuel_final": (("Какое поле по письму идёт первым?", "уровень", ("уровень", "объём")),),
    "F_doors_fix": (("Какая дверь по письму на деле соответствует биту 3?", "задние распашные",
                     ("водительская", "пассажирская", "задние распашные", "боковая сдвижная")),),
}

# second wave of the correspondence (drafts generated with a separate RNG, so the first 67 stay byte-identical)
BLOCKS.update({
    "F_card_initial": (
        "К весеннему выпуску у журнала третьей версии появится ещё одна запись — под тринадцатым номером, для "
        "считывателя карт водителей, который ставим на новые планшеты. Сразу после приращения времени идёт номер "
        "карты: четыре байта, старшим вперёд, в том виде, как его выдаёт считыватель. За ним один байт — что "
        "произошло: ноль — карту приложили, единица — убрали.",
        (("Какой номер типа по письму у новой записи?", "13", None),
         ("Сколько байт по этому письму занимает номер карты?", "4", None),
         ("Каким кодом по письму обозначено, что карту убрали?", "1", None)),
    ),
    "T_card_obs": (
        "Дина, на пилоте в Самаре сверили журналы с пластиком — номера карт не совпадают ни в какую. Беру четыре "
        "байта сразу после времени, как ты писала: старшим вперёд получается мусор, младшим вперёд — тоже. Причём у "
        "части записей после времени остаётся меньше пяти байт, туда номер и байт события вообще не влезают. Может, "
        "у нас какой-то другой считыватель?",
        (("Сколько байт номера карты берёт автор?", "4", None),
         ("Утверждает ли автор, что знает правильное устройство записи?", "нет", YES_NO)),
    ),
    "F_card_fix": (
        "Тимур, считыватель тот же, дело в описании. В сборке, которая ушла в 3.0.2, запись карты устроена не так, "
        "как в апрельском письме Дины: первым после приращения времени идёт байт события, а номер карты — за ним, "
        "varint-ом без знака, тем же способом, что длина записи. У новых карт номера длиннее четырёх байт, в "
        "фиксированное поле они не помещались. Четыре байта старшим вперёд были только в прототипе.",
        (("Что по письму идёт в записи первым после приращения времени?", "байт события",
          ("байт события", "номер карты")),
         ("Как по письму закодирован номер карты?", "varint без знака",
          ("varint без знака", "четыре байта, старший первым", "четыре байта, младший первым"))),
    ),
    "F_card_confirm": (
        "Да, Сергей прав: я описывала прототип, так что моё апрельское письмо в части порядка полей и номера карты "
        "забудьте. Коды событий прежние — ноль приложили, единица убрали, — и в релизе добавился ещё один: двойка, "
        "если карта не из списка допущенных к этой машине. Номер карты пишется и в этом случае.",
        (("Каким кодом по письму обозначена карта не из списка допущенных?", "2", None),
         ("Чью версию устройства записи подтверждает автор?", "Сергея", ("Сергея", "свою апрельскую", "Тимура"))),
    ),
    "M_card_v2_q": (
        "Коллеги, в архиве пилотов нашлись журналы второй версии, записанные уже этим летом, и в них попадаются "
        "записи с тем же номером типа, что у карт. Я правильно понимаю, что их надо разбирать так же, как в третьей "
        "версии, — байт события и номер?",
        (("Задаёт ли автор вопрос или утверждает факт?", "вопрос", ("вопрос", "утверждение")),),
    ),
    "F_card_v2": (
        "Нет. Карты пишет только журнал третьей версии. В файлах первой и второй версии записи с этим номером — "
        "остатки отладки тестовых сборок, как девятый тип; выводите их как неизвестные, ничего внутри не разбирая.",
        (("Как по письму выводить такие записи в файлах второй версии?", "как неизвестные",
          ("как неизвестные", "как карты водителя")),),
    ),
    "T_harsh4_obs": (
        "На эвакуаторах в Перми в записях резкого вождения первым байтом иногда стоит четвёрка — в октябрьском "
        "описании Дины такого вида нет. По времени совпадает с тем, что водители жалуются на крен при погрузке. Я "
        "бы предположил, что это просто очень сильный удар, какая-нибудь вторая степень, и выводил бы как удар.",
        (("Какое значение первого байта видит автор?", "4", None),
         ("Как автор предлагает выводить этот вид?", "как удар", ("как удар", "как опрокидывание"))),
    ),
    "F_harsh_rollover": (
        "Тимур, это не удар. В 3.0.2 для эвакуаторов и спецтехники в ту же запись добавили пятый вид события: "
        "четвёрка — опрокидывание или опасный крен. Пиковое ускорение и длительность после него — как у остальных "
        "видов.",
        (("Что по письму означает код 4?", "опрокидывание", ("удар", "опрокидывание", "торможение")),),
    ),
    "P_harsh_dur_q": (
        "Олег, а длительность у опрокидывания в миллисекундах? Крен ведь длится секундами, а в два байта "
        "миллисекунд влезет едва минута.",
        (("В каких единицах автор предполагает длительность?", "мс", ("мс", "10 мс", "с")),),
    ),
    "F_harsh_dur_same": (
        "Павел, единица длительности во всех видах этой записи одна — сотая доля секунды, я писал об этом ещё в "
        "декабре двадцать второго. Для крена хватает с запасом: два байта — это почти одиннадцать минут.",
        (("Сколько миллисекунд по письму в единице длительности?", "10", None),),
    ),
    "P_amps_changelog": (
        "Судя по релизным заметкам к 2.1, ток аккумулятора теперь пишется в миллиамперах. Переделываю vtldump: для "
        "файлов, записанных после выхода 2.1, делю это поле на тысячу, а не на сто. Если кто-то против — скажите.",
        (("На какое число автор собирается делить поле тока?", "1000", None),),
    ),
    "M_crc_scope_q": (
        "Олег, в онлайн-канале мы проверяем сумму уже после распаковки. В журнале так же? Хочу на бэкенде "
        "проверять файлы, которые загружают с флешек, и не хочу распаковывать лишнее.",
        (("Задаёт ли автор вопрос или утверждает факт?", "вопрос", ("вопрос", "утверждение")),),
    ),
    "F_crc_scope": (
        "Нет, в журнале сумма считается по тем байтам, что лежат в файле между длиной и самой суммой, так что сжатый "
        "блок проверяется до распаковки. Порядок её байтов — как я писал Павлу в феврале.",
        (("Проверяется ли по письму сжатый блок до распаковки?", "да", YES_NO),),
    ),
    "P_delta_v1_q": (
        "В паре файлов первой версии время внутри блока у меня идёт назад — я читаю приращение как число со "
        "знаком, по аналогии с координатами. Или его надо читать как-то иначе?",
        (("Как автор читает приращение?", "со знаком", ("со знаком", "без знака")),),
    ),
    "F_delta_v1": (
        "Павел, в первой версии приращение без знака: обычный varint в миллисекундах, как длина записи. Назад время "
        "там не ходит — у тебя просто младший бит превращается в знак.",
        (("Как по письму кодируется приращение в первой версии?", "без знака", ("без знака", "со знаком")),
         ("В каких единицах по письму это приращение?", "миллисекунды", ("миллисекунды", "тики", "секунды"))),
    ),
    "M_crc_v2_q": (
        "Раз во второй версии столько всего поменялось, может, и сумма блока теперь лежит младшим байтом вперёд, "
        "как всё остальное? Хочу заранее поправить проверку на бэкенде.",
        (("Задаёт ли автор вопрос или утверждает факт?", "вопрос", ("вопрос", "утверждение")),),
    ),
    "F_crc_v2_be": (
        "Нет, во второй версии сумма та же, что в первой: два байта в конце блока, старшим вперёд, и только по "
        "нагрузке. Трогать её будем не раньше следующей версии формата.",
        (("В каком порядке байтов по письму хранится сумма во второй версии?", "старший байт первым", BYTE_ORDER),),
    ),
    "P_empty_block_q": (
        "Нашёл блоки, в которых после базы времени ничего нет. vtldump считает их битыми. Выбрасывать их с "
        "ошибкой?",
        (("Задаёт ли автор вопрос или утверждает факт?", "вопрос", ("вопрос", "утверждение")),),
    ),
    "F_empty_block": (
        "Не выбрасывать и ошибкой не считать: трекер сбрасывает блок по таймеру, даже если записей не набралось. "
        "Если сумма сошлась — это обычный блок, просто пустой.",
        (("Является ли по письму такой блок ошибкой?", "нет", YES_NO),),
    ),
    "T_zlib_q": (
        "У клиента часть блоков наш просмотрщик не может распаковать и падает на середине файла. Может, трекер "
        "сжимает все блоки подряд, а заголовок у сжатых данных какой-то свой?",
        (("Утверждает ли автор, что знает причину?", "нет", YES_NO),),
    ),
    "F_zlib": (
        "Не все подряд: сжат только тот блок, у которого в байте сразу за маркером стоит младший бит, и заголовок "
        "там стандартный. Флаг в шапке файла лишь говорит, что такие блоки могут встретиться. Просмотрщик, видимо, "
        "пытается распаковать всё, что видит.",
        (("По какому признаку по письму определить сжатый блок?", "младший бит байта за маркером",
          ("младший бит байта за маркером", "флаг в заголовке файла")),),
    ),
    "D_vcm_card": (
        "Мы в «Вектор-М» тоже прикрутили карты водителей: в .vcm номер карты хранится шестью байтами в "
        "двоично-десятичном виде, старшей тетрадой вперёд, а событие — отдельным битом в байте состояния. Сделали "
        "так, чтобы номер с пластика читался прямо из дампа.",
        (("К какому формату относятся эти сведения?", ".vcm", FORMATS),
         ("Сколько байт занимает номер карты в этом формате?", "6", None)),
    ),
    "D_vtp_card": (
        "В ВТП-3 событие карты идёт отдельным пакетом: номер карты — десятью символами ASCII, как на пластике, "
        "событие — латинской буквой: I — приложили, O — убрали. На бэкенде сопоставляем со справочником водителей.",
        (("К какому протоколу относятся эти сведения?", "ВТП", FORMATS),
         ("Сколько символов занимает номер карты в этом протоколе?", "10", None)),
    ),
    "D_sms_card": (
        "Для монтажников: SMS-команда CARDS# возвращает список допущенных карт через точку с запятой, CARDADD# с "
        "номером добавляет карту, CARDDEL# удаляет. В ответе на STATUS# седьмым параметром теперь идёт номер "
        "последней приложенной карты.",
        (("К чему относятся эти сведения?", "SMS-команды", FORMATS),),
    ),
    "D_ini_card": (
        "В tracker.ini появилась секция [cards]: первый параметр — разрешать ли поездку без карты, второй — "
        "сколько секунд ждать карту после включения зажигания, третий — пищать ли при чужой карте.",
        (("К чему относятся эти сведения?", "tracker.ini", FORMATS),),
    ),
    "D_vcm5": (
        "Раз спрашивали про время: в .vcm мы пишем его в сотых долях секунды от полуночи, дату — отдельной записью "
        "раз в сутки, часовой пояс — в минутах в заголовке. Контрольная сумма у нас на весь файл, а не на блок.",
        (("К какому формату относятся эти сведения?", ".vcm", FORMATS),
         ("В каких единицах пояс в этом формате?", "минуты", ("минуты", "четверти часа", "часы"))),
    ),
})

EXTRA_Q.update({
    "F_card_initial": (("Что по этому письму идёт в записи первым после приращения времени?", "номер карты",
                        ("номер карты", "байт события")),),
    "F_card_fix": (("Чем по письму было поле номера карты в прототипе?", "четыре байта, старший первым",
                    ("четыре байта, старший первым", "varint без знака")),),
})

# second wave: (label, date, sender, subject | None, parent label, block keys, size); merged with THREADS by date
THREADS2 = (
    ("n01", "2019-10-14", "marina", "Контрольная сумма и сжатие", None, ("M_crc_scope_q",), "mid"),
    ("n02", "2019-10-15", "oleg", None, "n01", ("F_crc_scope",), "mid"),
    ("n03", "2019-12-20", "irina", "Итоги 2019 года", None, (), "long"),
    ("n04", "2020-02-10", "pavel", "Время в первой версии", None, ("P_delta_v1_q",), "mid"),
    ("n05", "2020-02-11", "sergey", None, "n04", ("F_delta_v1",), "mid"),
    ("n06", "2020-07-06", "timur", "Не распаковывается", None, ("T_zlib_q",), "long"),
    ("n07", "2020-07-07", "sergey", None, "n06", ("F_zlib",), "mid"),
    ("n08", "2020-10-19", "gleb", "Время в наших логах", None, ("D_vcm5",), "long"),
    ("n09", "2021-08-16", "marina", "Сумма во второй версии", None, ("M_crc_v2_q",), "mid"),
    ("n10", "2021-08-17", "oleg", None, "n09", ("F_crc_v2_be",), "short"),
    ("n11", "2021-10-20", "pavel", "2.1 и ток", None, ("P_amps_changelog",), "long"),
    ("n12", "2022-04-11", "timur", "Весенние выезды", None, (), "long"),
    ("n13", "2022-06-20", "pavel", "Пустые блоки", None, ("P_empty_block_q",), "mid"),
    ("n14", "2022-06-21", "dina", None, "n13", ("F_empty_block",), "mid"),
    ("n15", "2023-04-24", "dina", "Ещё одна запись к 3.0.2", None, ("F_card_initial",), "long"),
    ("n16", "2023-06-05", "timur", None, "n15", ("T_card_obs",), "mid"),
    ("n17", "2023-06-06", "sergey", None, "n16", ("F_card_fix",), "mid"),
    ("n18", "2023-06-06", "dina", None, "n17", ("F_card_confirm",), "short"),
    ("n19", "2023-06-13", "marina", "Тринадцатый тип в старых журналах", None, ("M_card_v2_q",), "mid"),
    ("n20", "2023-06-14", "dina", None, "n19", ("F_card_v2",), "short"),
    ("n21", "2023-07-10", "gleb", "Карты водителей у нас", None, ("D_vcm_card",), "long"),
    ("n22", "2023-08-21", "timur", "Эвакуаторы в Перми", None, ("T_harsh4_obs",), "long"),
    ("n23", "2023-08-22", "oleg", None, "n22", ("F_harsh_rollover",), "mid"),
    ("n24", "2023-08-22", "pavel", None, "n23", ("P_harsh_dur_q",), "short"),
    ("n25", "2023-08-23", "sergey", None, "n24", ("F_harsh_dur_same",), "short"),
    ("n26", "2023-09-25", "marina", "Карты в онлайн-канале", None, ("D_vtp_card",), "long"),
    ("n27", "2023-11-13", "timur", "Команды для карт", None, ("D_sms_card",), "long"),
    ("n28", "2024-01-15", "irina", "Планы на 2024 год", None, ("D_ini_card",), "long"),
)

# extra background sentences, used (together with NOISE) only by the second wave
NOISE2 = {
    "field": (
        "В {city} клиент {client} попросил переставить трекеры с проданных машин на новые, и половина кронштейнов "
        "при этом не пережила демонтажа.",
        "На {depot} охрана не пустила монтажников без заявки за сутки, потеряли полдня на согласования.",
        "Водители на {veh} научились выдёргивать разъём, когда едут по личным делам; клиент просит отчёт о "
        "пропаданиях питания.",
        "Пилот в {city} закрыли досрочно: заказчик {client} доволен и переходит на годовой договор.",
        "Диспетчер клиента {client} путает поездки двух машин с похожими номерами, попросили добавить в "
        "интерфейс цвет кузова.",
    ),
    "hw": (
        "Новая партия SIM-чипов пришла с другой прошивкой оператора, модем их видит через раз, разбираемся с "
        "поставщиком.",
        "{mod} на плате ревизии Д пришлось сдвинуть на два миллиметра: мешал крепёжному винту.",
        "Разъём питания заменили на вариант с защёлкой, вибростол он пережил без замечаний.",
        "Климатические испытания новой ревизии прошли, кроме одного образца, у которого треснул корпус на "
        "минус сорока.",
        "Считыватель для новых планшетов закупаем у того же поставщика, что и в прошлом году, цена почти не "
        "изменилась.",
    ),
    "release": (
        "Кандидат в выпуск раскатали на тестовый парк {weekday}, замечаний пока два, оба по интерфейсу планшета.",
        "Выпуск задерживается: ждём подтверждения от сертификационной лаборатории.",
        "Сборки теперь подписываются на сервере, ключ лежит только там, вручную подписывать больше ничего не надо.",
        "Откатили кандидат на {n2} машинах из-за зависания модема при слабом сигнале, к журналу это не относится.",
    ),
    "backend": (
        "Переезд базы на новый сервер прошёл ночью, простой — около сорока минут, трекеры за это время досылали "
        "данные сами.",
        "Отчёт «{report}» для клиента {client} теперь строится в фоне и приходит на почту ссылкой.",
        "Справочник водителей клиента {client} загрузили из их кадровой системы, половину фамилий пришлось "
        "чистить вручную.",
        "Добавили в веб-интерфейс фильтр по типу техники, диспетчеры давно просили.",
    ),
    "tools": (
        "Разборщик теперь печатает смещение каждого блока в шестнадцатеричном виде, так удобнее сверять с "
        "дампом.",
        "Регрессионный набор пополнили {n} файлами с пилотов, прогон занимает около минуты.",
        "Поправили вывод на Windows: кириллица в названиях машин больше не превращается в вопросики в консоли.",
        "Сделали режим, в котором инструмент останавливается на первом битом блоке, — для отладки удобнее.",
    ),
    "office": (
        "Переговорку на третьем этаже закрыли на ремонт, собираемся пока в кабинете Ирины.",
        "Новые ноутбуки для команды приедут к концу месяца, старые сдаём в бухгалтерию.",
        "Корпоративный календарь переехал в новую систему, проверьте, что встречи не потерялись.",
        "{weekday} в офисе отключат воду с утра до обеда, кофе будет только из автомата внизу.",
    ),
    "techmisc": (
        "В служебном пакете модема первые два байта — длина, дальше идёт тело, а в конце однобайтовая сумма по "
        "модулю 256.",
        "Номер IMEI в базе храним строкой из пятнадцати цифр, никаких упаковок в байты.",
        "В загрузчике таблица разделов — по восемь байт на запись: адрес начала и длина, оба младшим вперёд.",
        "Планшет водителя общается с трекером по RS-485 на 57600, кадры начинаются с 0xAA 0x55.",
        "Уровень заряда резервного аккумулятора модем отдаёт в процентах одним байтом, 255 — неизвестно.",
        "Файл настроек на флешке читается построчно, строки длиннее 120 символов обрезаются без предупреждения.",
        "Сертификат для TLS хранится во флеше в формате DER, около 900 байт.",
        "В CAN-шине грузовиков скорость приходит в 1/256 км/ч, но это только для отладки на стенде.",
        "Часы реального времени сбиваются примерно на две секунды в сутки, раз в сутки подводим их по GPS.",
        "Идентификатор прошивки в веб-интерфейсе показываем как хеш сборки, первые восемь символов.",
    ),
    "vendor": (
        "У нас в «Вектор-М» новая версия просмотрщика, пришлю ссылку, как соберём инсталлятор.",
        "Наш технолог приедет к вам на следующей неделе обсудить общий кронштейн.",
        "Мы тоже столкнулись с новой партией SIM-чипов, похоже, проблема у оператора, а не у нас.",
    ),
}
NOISE_ALL = {topic: NOISE[topic] + NOISE2.get(topic, ()) for topic in NOISE}


# (label, date, sender, subject | None for a reply, parent label, block keys, size)
THREADS = (
    ("m01", "2019-02-11", "irina", "Журнал поездок: кто за что", None, ("X_roles",), "long"),
    ("m02", "2019-02-18", "pavel", "vtldump: первые результаты", None, ("P_coords_q", "P_crc_guess"), "long"),
    ("m03", "2019-02-19", "oleg", None, "m02", ("F_coords", "F_crc_be"), "mid"),
    ("m04", "2019-02-25", "pavel", "Ещё два вопроса по записям", None, ("P_heading_q", "P_rpm_q"), "long"),
    ("m05", "2019-02-26", "oleg", None, "m04", ("F_heading2", "F_rpm4"), "mid"),
    ("m06", "2019-03-04", "timur", "Ромбики вместо текста в выгрузках", None, ("T_mojibake",), "long"),
    ("m07", "2019-03-05", "oleg", None, "m06", ("F_cp1251",), "mid"),
    ("m08", "2019-03-12", "gleb", "Привет от «Вектор-М»", None, ("D_vcm1",), "long"),
    ("m09", "2019-03-20", "pavel", "Статусы кодов неисправностей", None, ("P_status_q",), "mid"),
    ("m10", "2019-03-21", "oleg", None, "m09", ("F_status_wrong",), "short"),
    ("m11", "2019-04-02", "sergey", "Температура охлаждайки", None, ("F_coolant40",), "long"),
    ("m12", "2019-04-15", "marina", "ВТП и журнал: что где", None, ("D_vtp1",), "long"),
    ("m13", "2019-05-06", "sergey", None, "m10", ("F_status_fix",), "mid"),
    ("m14", "2019-05-07", "oleg", None, "m13", ("F_status_confirm",), "short"),
    ("m15", "2019-06-10", "irina", "План на лето", None, ("D_sms",), "long"),
    ("m16", "2019-09-03", "timur", "Итоги пилотов за лето", None, (), "long"),
    ("m17", "2020-01-20", "sergey", "1.3: отвал датчика скорости", None, ("F_speed_ffff",), "long"),
    ("m18", "2020-01-21", "marina", None, "m17", ("M_speed_guess",), "short"),
    ("m19", "2020-01-21", "sergey", None, "m18", ("F_speed_zero",), "short"),
    ("m20", "2020-04-14", "oleg", "1.4: что нового в журнале", None, ("F_hdop",), "long"),
    ("m21", "2020-04-15", "pavel", None, "m20", ("P_hdop_q",), "mid"),
    ("m22", "2020-04-16", "sergey", None, "m21", ("F_hdop_units",), "mid"),
    ("m23", "2020-06-01", "gleb", "Новости «Вектор-М»", None, ("D_vcm2",), "long"),
    ("m24", "2020-09-15", "irina", "Осенние задачи", None, ("D_ini",), "long"),
    ("m25", "2020-11-10", "timur", "Подготовка к зиме", None, (), "long"),
    ("m26", "2021-03-14", "oleg", "Журнал 2.0", None, ("F_v2_tick", "F_v2_ticks"), "long"),
    ("m27", "2021-03-14", "marina", None, "m26", ("M_v2_delta_guess",), "mid"),
    ("m28", "2021-03-15", "oleg", None, "m27", ("F_v2_zigzag",), "mid"),
    ("m29", "2021-03-22", "marina", "Идея про координаты", None, ("M_gps_delta_proposal",), "long"),
    ("m30", "2021-03-23", "oleg", None, "m29", ("F_gps_delta_rejected",), "mid"),
    ("m31", "2021-04-02", "oleg", "Новые записи 2.0", None, ("F_battery_ma", "F_trip"), "long"),
    ("m32", "2021-04-03", "sergey", None, "m31", ("F_battery_fix",), "mid"),
    ("m33", "2021-04-03", "oleg", None, "m32", ("F_battery_confirm",), "short"),
    ("m34", "2021-04-20", "marina", "Непонятное значение в событиях", None, ("M_event3",), "mid"),
    ("m35", "2021-04-20", "oleg", None, "m34", ("F_trip_resume",), "short"),
    ("m36", "2021-05-11", "sergey", "Совместимость декодеров", None, ("F_oil",), "long"),
    ("m37", "2021-05-12", "pavel", None, "m36", ("P_oil_guess",), "short"),
    ("m38", "2021-05-12", "sergey", None, "m37", ("F_oil_offset",), "short"),
    ("m39", "2021-05-30", "oleg", "Отладочные записи на пилотах", None, ("F_debug_types",), "long"),
    ("m40", "2021-06-07", "sergey", "Педаль в 2.0", None, ("F_throttle255",), "long"),
    ("m41", "2021-06-08", "marina", None, "m40", ("M_throttle_q",), "short"),
    ("m42", "2021-06-08", "sergey", None, "m41", ("F_throttle_v1",), "short"),
    ("m43", "2021-07-19", "marina", "ВТП-2 в проде", None, ("D_vtp2",), "long"),
    ("m44", "2021-10-04", "irina", "Планы на зиму", None, (), "long"),
    ("m45", "2021-11-02", "oleg", "Про релизные заметки 2.1", None, ("F_changelog_typo",), "mid"),
    ("m46", "2021-12-01", "timur", "Отчёт поддержки за год", None, (), "long"),
    ("m47", "2022-02-14", "irina", "Пополнение в команде", None, ("X_dina",), "long"),
    ("m48", "2022-05-18", "gleb", "Двери и прочее", None, ("D_vcm3",), "long"),
    ("m49", "2022-09-05", "oleg", "Планы на 3.0", None, ("F_zone_proposal", "F_crc32_proposal", "F_utf8"), "long"),
    ("m50", "2022-09-06", "dina", None, "m49", ("F_gps_delta_v3",), "long"),
    ("m51", "2022-10-10", "dina", "Новые записи 3.0", None, ("F_harsh", "F_fuel_proposal", "F_tires_proposal"),
     "long"),
    ("m52", "2022-10-11", "marina", None, "m51", ("M_tire_temp_guess",), "short"),
    ("m53", "2022-10-11", "dina", None, "m52", ("F_tire_temp_signed",), "short"),
    ("m54", "2022-11-01", "dina", "Ещё одна запись для 3.0", None, ("F_doors_initial",), "long"),
    ("m55", "2022-12-05", "sergey", None, "m51", ("F_harsh_duration_fix",), "mid"),
    ("m56", "2023-01-16", "oleg", "Что ушло в 3.0", None, ("F_zone_final", "F_crc_v3", "F_v3_rest", "F_fuel_final"),
     "long"),
    ("m57", "2023-01-17", "pavel", None, "m56", ("P_zone_q",), "short"),
    ("m58", "2023-01-17", "dina", None, "m57", ("F_zone_sign",), "mid"),
    ("m59", "2023-02-20", "sergey", "Датчики давления", None, ("F_tires_final",), "long"),
    ("m60", "2023-03-06", "sergey", "Неприятность с платой 3.0", None, ("F_doors_fix",), "long"),
    ("m61", "2023-03-07", "marina", None, "m60", ("M_doors_q",), "short"),
    ("m62", "2023-03-07", "dina", None, "m61", ("F_doors_confirm",), "short"),
    ("m63", "2023-04-10", "timur", "Пилот 3.0 в регионах", None, ("T_fuel_obs", "D_sms2"), "long"),
    ("m64", "2023-06-19", "dina", "Ответы на вопросы по 3.0", None, ("F_type12_v3", "F_gps_hdop_always"), "long"),
    ("m65", "2023-09-11", "oleg", "3.1", None, ("F_tick_us_rejected", "D_ini2"), "long"),
    ("m66", "2024-02-05", "irina", "Итоги 2023 года", None, ("D_vtp3",), "long"),
    ("m67", "2024-03-12", "gleb", "Общий просмотрщик", None, ("D_vcm4",), "long"),
)

SIZES = {"long": (6000, 8800), "mid": (2600, 4000), "short": (700, 1500)}


# ---------------------------------------------------------------------------
# Sensor pack (firmware 3.3–3.5, journals of version 3 only): 33 record types described only in the
# third wave of the correspondence.  SENSORS holds the final (released) layouts — the ground truth.
# MSGS3 is the story: every type starts from its proposal and changes only by firmware-team
# statements; the result of the story is asserted to equal SENSORS.
# ---------------------------------------------------------------------------

FMTS = {"u8": ("<B", 1), "i8": ("<b", 1), "u16": ("<H", 2), "i16": ("<h", 2), "U16": (">H", 2), "I16": (">h", 2),
        "u32": ("<I", 4), "i32": ("<i", 4), "U32": (">I", 4), "bool": ("<B", 1), "uv": ("", 0), "sv": ("", 0)}
FMT_TEXT = {
    "u8": "один байт без знака", "i8": "один байт со знаком", "u16": "два байта без знака, младшим вперёд",
    "i16": "два байта со знаком, младшим вперёд", "U16": "два байта без знака, старшим вперёд",
    "I16": "два байта со знаком, старшим вперёд", "u32": "четыре байта без знака, младшим вперёд",
    "i32": "четыре байта со знаком, младшим вперёд", "U32": "четыре байта без знака, старшим вперёд",
    "bool": "один байт: единица — да, ноль — нет", "uv": "varint без знака, как длина записи",
    "sv": "varint со знаком (zigzag, как приращение времени)",
}
FMT_SHORT = {
    "u8": "1 байт без знака", "i8": "1 байт со знаком", "u16": "2 байта без знака, младшим вперёд",
    "i16": "2 байта со знаком, младшим вперёд", "U16": "2 байта без знака, старшим вперёд",
    "I16": "2 байта со знаком, старшим вперёд", "u32": "4 байта без знака, младшим вперёд",
    "i32": "4 байта со знаком, младшим вперёд", "U32": "4 байта без знака, старшим вперёд", "bool": "1 байт да/нет",
    "uv": "varint без знака", "sv": "varint со знаком",
}
FMT_ALT = {"u8": "i8", "i8": "u8", "u16": "i16", "i16": "u16", "U16": "u16", "I16": "i16", "u32": "U32", "U32": "u32",
           "i32": "u32", "uv": "u32", "sv": "uv", "bool": "u8"}
FRACTIONS = {0.001953125: "1/512", 0.00390625: "1/256", 0.03125: "1/32", 0.125: "1/8"}
UNIT_GEN = {"°C": "градуса", "%": "процента", "л": "литра", "ч": "часа", "кг": "килограмма", "В": "вольта",
            "А": "ампера", "с": "секунды", "‰": "промилле", "°": "градуса", "м": "метра", "км": "километра"}
UNIT_PREP = {"%": "процентах", "мин": "минутах", "ppm": "ppm", "с": "секундах", "кг": "килограммах", "°C": "градусах",
             "ч": "часах", "л": "литрах", "об/мин": "оборотах в минуту", "В": "вольтах", "мс": "миллисекундах",
             "°": "градусах", "км": "километрах", "км/ч": "километрах в час", "м": "метрах", "‰": "промилле",
             "А": "амперах", "м³": "кубометрах", "л/ч": "литрах в час", "км/л": "километрах на литр",
             "кПа": "килопаскалях", "дБм": "дБм", "мл": "миллилитрах", "бар": "барах", "мм": "миллиметрах",
             "гПа": "гектопаскалях", "g": "g", "г/м²": "граммах на квадратный метр"}
ATTRS = ("key", "fmt", "mul", "add", "null", "enum", "flags")
FIRMWARE = ("oleg", "sergey", "dina", "artem")


@dataclass(frozen=True)
class SF:
    """A sensor-pack field in its final (released) form; `enum`/`flags` items are "id:gloss"."""

    key: str
    what: str
    fmt: str = "u8"
    mul: float = 1
    add: float = 0
    unit: str = ""
    null: int | None = None
    enum: tuple = ()
    flags: tuple = ()
    lo: int = 0
    hi: int = 100
    opt: str = ""  # optional trailing field: when it is present
    group: tuple = ()  # (count noun, per-item phrase, min count, max count): a count byte, then the items
    note: str = ""
    gone: bool = False  # proposed, removed before the release
    late: bool = False  # not in the proposal, added later


@dataclass(frozen=True)
class ST:
    key: str
    tid: int
    name: str
    rw: str  # complement of «запись …»
    intro: str
    fields: tuple
    dropped: bool = False


def _frac(x) -> str | None:
    for value, text in FRACTIONS.items():
        if not isinstance(x, bool) and math.isclose(x, value):
            return text
    return None


def _num_ru(x) -> str:
    frac = _frac(x)
    if frac:
        return frac
    text = str(int(x)) if float(x).is_integer() else f"{x:.10g}".replace(".", ",")
    return text.replace("-", "−")


def _hexpat(fmt: str, value: int) -> str:
    size = FMTS[fmt][1]
    return f"0x{value & ((1 << 8 * size) - 1):0{2 * size}X}"


def _ids(items: tuple) -> tuple:
    return tuple(x.split(":")[0] for x in items)


def _p_mul(mul, unit: str, R) -> str:
    frac = _frac(mul)
    if frac:
        return R.choice((f"единица — {frac} {unit}", f"в долях по {frac} {unit}"))
    if mul == 1:
        return R.choice((f"сразу в {UNIT_PREP[unit]}", f"в целых {UNIT_PREP[unit]}"))
    word = {0.1: "десятых", 0.01: "сотых", 0.001: "тысячных"}.get(mul)
    if word and unit in UNIT_GEN and R.random() < 0.6:
        return f"в {word} долях {UNIT_GEN[unit]}"
    n = _num_ru(mul)
    return R.choice((f"единица — {n} {unit}", f"шаг {n} {unit}", f"одна единица — {n} {unit}"))


def _p_add(add, mul, unit: str, R) -> str:
    if not add:
        return "без сдвига"
    a = _num_ru(abs(add))
    if mul == 1:
        return R.choice((f"со сдвигом на {a}: ноль в нём означает −{a} {unit}", f"со сдвигом: из значения вычесть {a}"))
    return f"со сдвигом: после пересчёта вычесть {a} {unit}"


def _p_null(fmt: str, null, R) -> str:
    if null is None:
        return "особого значения «нет данных» у него нет"
    h = _hexpat(fmt, null)
    return R.choice((f"{h} — нет данных", f"значение {h} означает «нет данных»",
                     f"{h} трекер пишет, когда датчик не ответил"))


def _p_enum(items: tuple) -> str:
    return "коды: " + ", ".join(f"{i} — `{x.split(':')[0]}` ({x.split(':')[1]})" for i, x in enumerate(items))


def _p_flags(items: tuple) -> str:
    return "по биту на значение: " + ", ".join(f"бит {i} — `{x.split(':')[0]}` ({x.split(':')[1]})"
                                                for i, x in enumerate(items))


def _p_key(key: str, R) -> str:
    return R.choice((f"в выгрузке — `{key}`", f"имя в выгрузке — `{key}`", f"в выгрузку идёт как `{key}`"))


def _meta(st: ST) -> dict:
    return {f.key: f for f in st.fields}


def _fstate(f: SF) -> dict:
    return {"key": f.key, "fmt": f.fmt, "mul": f.mul, "add": f.add, "null": f.null, "enum": f.enum, "flags": f.flags}


def _final_state(st: ST) -> dict:
    return {"tid": st.tid, "name": st.name, "dropped": st.dropped,
            "order": [f.key for f in st.fields if not f.gone], "f": {f.key: _fstate(f) for f in st.fields}}


def _apply(state: dict, changes: dict) -> None:
    for path, value in changes.items():
        if path == "@order":
            state["order"] = list(value)
        elif path.startswith("@"):
            state[path[1:]] = value
        else:
            fid, attr = path.split(".")
            if attr == "+":
                state["order"].insert(state["order"].index(value) + 1 if value else len(state["order"]), fid)
            elif attr == "-":
                state["order"].remove(fid)
            else:
                state["f"][fid][attr] = value


def _initial_state(st: ST, overrides: dict) -> dict:
    state = _final_state(st)
    state["dropped"] = False
    state["order"] = [f.key for f in st.fields if not f.late]
    _apply(state, overrides)
    return state


def _get(state: dict, path: str):
    if path == "@order":
        return tuple(state["order"])
    if path.startswith("@"):
        return state[path[1:]]
    fid, attr = path.split(".")
    if attr in "+-":
        return fid in state["order"]
    return state["f"][fid][attr]


def _same_state(a: dict, b: dict) -> bool:
    if a["dropped"] or b["dropped"]:
        return a["dropped"] == b["dropped"]
    return (a["tid"], a["name"], a["order"]) == (b["tid"], b["name"], b["order"]) and all(
        a["f"][fid][x] == b["f"][fid][x] for fid in a["order"] for x in ATTRS)


def _table(states: dict) -> dict:
    """Decoder table {tid: (name, fields)} of the types a decoder with these beliefs would parse."""
    table = {}
    for tkey, s in states.items():
        if s["dropped"] or s["tid"] is None:
            continue
        meta = _meta(SENSOR_BY_KEY[tkey.split("#")[0]])
        fields = []
        for fid in s["order"]:
            fs = s["f"][fid]
            fields.append((fs["key"], fs["fmt"], fs["mul"], fs["add"], fs["null"], _ids(fs["enum"]),
                           _ids(fs["flags"]), bool(meta[fid].opt), bool(meta[fid].group)))
        table[s["tid"]] = (s["name"], tuple(fields))
    return dict(sorted(table.items()))


# --- values: generation, packing, meaning -----------------------------------


def _fmt_range(fmt: str) -> tuple[int, int]:
    if fmt == "uv":
        return 0, 2**40
    if fmt == "sv":
        return -(2**30), 2**30
    code, size = FMTS[fmt]
    if code[1].islower():
        return -(1 << (8 * size - 1)), (1 << (8 * size - 1)) - 1
    return 0, (1 << (8 * size)) - 1


def _gen_one(R, sf: SF, fs: dict, pnull: float) -> int:
    fmt = fs["fmt"]
    if fmt == "bool":
        return R.randint(0, 1)
    if fs["null"] is not None and R.random() < pnull:
        return fs["null"]
    if fs["enum"]:
        return R.randrange(len(fs["enum"]))
    if fs["flags"]:
        return R.randrange(1 << len(fs["flags"]))
    lo, hi = _fmt_range(fmt)
    lo, hi = max(lo, sf.lo), min(hi, sf.hi)
    if lo > hi:
        lo, hi = _fmt_range(fmt)[0], min(_fmt_range(fmt)[1], 200)
    value = R.randint(lo, -1) if lo < 0 < hi and R.random() < 0.3 else R.randint(lo, hi)
    while value == fs["null"]:
        value = R.randint(lo, hi)
    return value


def _gen_values(R, st: ST, state: dict, pnull: float = 0.2) -> dict:
    meta = _meta(st)
    out: dict = {}
    for fid in state["order"]:
        sf, fs = meta[fid], state["f"][fid]
        if sf.opt and R.random() < 0.35:
            break
        if sf.group:
            out[fid] = [_gen_one(R, sf, fs, pnull) for _ in range(R.randint(sf.group[2], sf.group[3]))]
        else:
            out[fid] = _gen_one(R, sf, fs, pnull)
    return out


def _pack(fmt: str, raw: int) -> bytes:
    if fmt == "uv":
        return _uvarint(raw)
    if fmt == "sv":
        return _svarint(raw)
    return struct.pack(FMTS[fmt][0], raw)


def _pack_values(st: ST, state: dict, vals: dict) -> bytes:
    meta = _meta(st)
    out = bytearray()
    for fid in state["order"]:
        if fid not in vals:
            break
        fmt = state["f"][fid]["fmt"]
        if meta[fid].group:
            out += bytes([len(vals[fid])]) + b"".join(_pack(fmt, x) for x in vals[fid])
        else:
            out += _pack(fmt, vals[fid])
    return bytes(out)


def _sval(fs: dict, raw: int):
    if fs["null"] is not None and raw == fs["null"]:
        return None
    if fs["fmt"] == "bool":
        return bool(raw)
    if fs["enum"]:
        ids = _ids(fs["enum"])
        return ids[raw] if raw < len(ids) else f"код {raw}"
    if fs["flags"]:
        return [name for bit, name in enumerate(_ids(fs["flags"])) if raw >> bit & 1]
    return raw * fs["mul"] + fs["add"]


def _sensor_json(st: ST, vals: dict) -> dict:
    """Expected output of a released sensor record, from the generated raw values and the final spec."""
    state = _final_state(st)
    meta = _meta(st)
    out: dict = {"type": state["name"]}
    for fid in state["order"]:
        fs = state["f"][fid]
        if fid not in vals:
            out[fs["key"]] = None
        elif meta[fid].group:
            out[fs["key"]] = [_sval(fs, x) for x in vals[fid]]
        else:
            out[fs["key"]] = _sval(fs, vals[fid])
    return out


def _val_ru(x) -> str:
    text = str(int(x)) if float(x).is_integer() else f"{x:.10g}".replace(".", ",")
    return text.replace("-", "−")


def _vtext(sf: SF, fs: dict, value) -> str:
    if value is None:
        return "нет данных"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, list):
        return ", ".join(f"`{x}`" for x in value) or "ни одного"
    if isinstance(value, str):
        return f"`{value}`"
    return f"{_val_ru(value)} {sf.unit}".strip()


def _example_parts(st: ST, state: dict, shown: dict, vals: dict) -> tuple[list[str], bytes]:
    """Bytes laid out by `state`, explained by the beliefs in `shown` (the same field sizes)."""
    meta = _meta(st)
    parts: list[str] = []
    body = bytearray()

    def one(sf: SF, fid: str, raw: int, label: str) -> str:
        b = _pack(state["f"][fid]["fmt"], raw)
        body.extend(b)
        fs2 = shown["f"][fid]
        if fs2["fmt"] not in ("uv", "sv") and FMTS[fs2["fmt"]][1] == len(b):
            raw = struct.unpack(FMTS[fs2["fmt"]][0], b)[0]
        text = f"`{b.hex(' ').upper()}` — {label}: "
        if fs2["fmt"] != "bool" and not fs2["enum"] and not fs2["flags"] and fs2["null"] != raw and (
                fs2["mul"] != 1 or fs2["add"]):
            text += f"{_val_ru(raw)} → "
        return text + _vtext(sf, fs2, _sval(fs2, raw))

    for fid in state["order"]:
        sf = meta[fid]
        if fid not in vals:
            parts.append(f"дальше байтов нет — поля «{sf.what}» в этой записи нет")
            break
        if sf.group:
            body.append(len(vals[fid]))
            parts.append(f"`{len(vals[fid]):02X}` — число {sf.group[0]}: {len(vals[fid])}")
            parts.extend(one(sf, fid, x, f"{sf.what} №{k}") for k, x in enumerate(vals[fid], 1))
        else:
            parts.append(one(sf, fid, vals[fid], sf.what))
    return parts, bytes(body)


def _shown_value(state: dict, shown: dict, fid: str, raw: int):
    b = _pack(state["f"][fid]["fmt"], raw)
    fs2 = shown["f"][fid]
    if fs2["fmt"] not in ("uv", "sv") and FMTS[fs2["fmt"]][1] == len(b):
        raw = struct.unpack(FMTS[fs2["fmt"]][0], b)[0]
    return _sval(fs2, raw)


# --- mail text of the sensor pack -------------------------------------------

_POS_FIRST = ("Сразу после приращения времени —", "Первым после приращения времени идёт", "Первое поле —")
_POS_MID = ("Затем —", "Дальше —", "За ним —", "Следом —", "Потом —", "После него —")
_POS_LAST = ("И последним —", "В конце —", "Замыкает запись")


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _tid_text(tid: int, R) -> str:
    return R.choice((f"{tid} (0x{tid:02X})", f"0x{tid:02X}, то есть {tid}", f"{tid}, в шестнадцатеричном виде 0x{tid:02X}"))


def _field_text(sf: SF, fs: dict, pos: str, R, *, compact: bool = False) -> str:
    fmt = fs["fmt"]
    ftext = FMT_SHORT[fmt] if compact else FMT_TEXT[fmt]
    seg = [f"сначала один байт — число {sf.group[0]}, затем {sf.group[1]} {ftext}" if sf.group else ftext]
    if fs["enum"]:
        seg.append(_p_enum(fs["enum"]))
    elif fs["flags"]:
        seg.append(_p_flags(fs["flags"]))
    elif fmt != "bool":
        if sf.unit:
            seg.append(_p_mul(fs["mul"], sf.unit, R))
        if fs["add"]:
            seg.append(_p_add(fs["add"], fs["mul"], sf.unit, R))
    if fs["null"] is not None:
        seg.append(_p_null(fmt, fs["null"], R))
    head = f"{pos} {sf.what}" if pos else _cap(sf.what)
    if sf.note and not compact:
        head += f" ({sf.note})"
    text = head + ": " + ", ".join(seg)
    if sf.opt:
        text += f"; {sf.opt}" + ("" if compact else ", и если запись на этом кончается, значения нет")
    return f"{text}; " + (f"`{fs['key']}`" if compact else _p_key(fs["key"], R)) + "."


def _attr_text(sf: SF, fs: dict, attr: str, R) -> str:
    if attr == "key":
        return f"в выгрузке `{fs['key']}`"
    if attr == "fmt":
        return FMT_TEXT[fs["fmt"]]
    if attr == "mul":
        return _p_mul(fs["mul"], sf.unit, R)
    if attr == "add":
        return _p_add(fs["add"], fs["mul"], sf.unit, R)
    if attr == "null":
        return _p_null(fs["fmt"], fs["null"], R)
    if attr == "enum":
        return _p_enum(fs["enum"])
    return _p_flags(fs["flags"])


def _by_field(paths) -> dict:
    grouped: dict = {}
    for path in paths:
        if not path.startswith("@"):
            fid, attr = path.split(".")
            grouped.setdefault(fid, []).append(attr)
    return grouped


def _field_lines(st: ST, state: dict, paths, R, *, ask: bool = False, old: dict | None = None) -> list[str]:
    meta = _meta(st)
    out = []
    for fid, attrs in _by_field(paths).items():
        sf = meta[fid]
        text = f"В записи {st.rw} поле «{sf.what}» — " + ("это " if ask else "")
        text += ", ".join(_attr_text(sf, state["f"][fid], a, R) for a in attrs)
        if old is not None:
            text += " (раньше было: " + ", ".join(_attr_text(sf, old["f"][fid], a, R) for a in attrs) + ")"
        out.append(text + ("?" if ask else "."))
    return out


def _change_text(st: ST, before: dict, after: dict, changes: dict, R, *, old: bool) -> list[str]:
    meta = _meta(st)
    out = []
    if changes.get("@dropped") is False:
        out.append(f"Запись {st.rw} возвращается в журнал.")
    if "@tid" in changes:
        out.append(f"Номер типа у записи {st.rw} — {_tid_text(after['tid'], R)}.")
    if "@name" in changes:
        out.append(f"Запись {st.rw} в выгрузке называется теперь `{after['name']}`, а не `{before['name']}`.")
    if "@order" in changes:
        whats = [meta[f].what for f in after["order"]]
        out.append(f"Порядок полей в записи {st.rw} другой: сразу после приращения времени — {whats[0]}, затем "
                   + ", затем ".join(whats[1:]) + ".")
    rest = []
    for path in changes:
        if path.startswith("@"):
            continue
        fid, attr = path.split(".")
        if attr == "+":
            idx = after["order"].index(fid)
            pos = f"после поля «{meta[after['order'][idx - 1]].what}» —" if idx else "сразу после приращения времени —"
            out.append(f"В записи {st.rw} появляется новое поле. " + _cap(_field_text(meta[fid], after["f"][fid], pos, R)))
        elif attr == "-":
            out.append(f"Поля «{meta[fid].what}» в записи {st.rw} больше нет: за предыдущим полем сразу идёт следующее.")
        elif attr == "key":
            out.append(f"Поле «{meta[fid].what}» записи {st.rw} в выгрузке называем `{after['f'][fid]['key']}` "
                       f"(было `{before['f'][fid]['key']}`).")
        else:
            rest.append(path)
    return out + _field_lines(st, after, rest, R, old=before if old else None)


@functools.cache
def _history() -> dict:
    """Every value each (type, path) takes anywhere in the story — answer options for the judge."""
    hist: dict = {}

    def add(t: str, path: str, value) -> None:
        value = tuple(value) if isinstance(value, list) else value
        bucket = hist.setdefault((t, path), [])
        if value not in bucket:
            bucket.append(value)

    for st in SENSORS:
        fin = _final_state(st)
        add(st.key, "@name", fin["name"])
        for fid, fs in fin["f"].items():
            for attr in ATTRS:
                add(st.key, f"{fid}.{attr}", fs[attr])
    for *_head, events in MSGS3:
        for ev in events:
            for arg in ev[2:]:
                if isinstance(arg, dict):
                    for path, value in arg.items():
                        add(ev[1], path, value)
    return hist


def _num_answer(x) -> str:
    return str(int(x)) if float(x).is_integer() else repr(float(x))


def _q_path(st: ST, state: dict, path: str, R, who: str = "по этому письму", ref: dict | None = None) -> tuple:
    meta = _meta(st)
    hist = _history()
    if path == "@tid":
        return (f"Какой номер типа (десятичным числом) {who} у записи {st.rw}?", str(state["tid"]), None)
    if path == "@name":
        opts = {state["name"], *hist.get((st.key, "@name"), ())}
        return (f"Как {who} называется в выгрузке запись {st.rw}?", state["name"], tuple(sorted(opts)))
    if path == "@order":
        whats = tuple(meta[f].what for f in state["order"])
        return (f"Какое поле {who} идёт в записи {st.rw} первым после приращения времени?", whats[0],
                tuple(sorted(whats)))
    fid, attr = path.split(".")
    sf = meta[fid]
    w = f"«{sf.what}» в записи {st.rw}"
    if attr == "+":
        idx = state["order"].index(fid)
        prev = meta[state["order"][idx - 1]].what if idx else "приращение времени"
        opts = {meta[f].what for f in state["order"] if f != fid} | {"приращение времени"}
        return (f"После чего {who} идёт поле {w}?", prev, tuple(sorted(opts)))
    if attr == "-":
        return (f"Есть ли {who} в записи {st.rw} поле «{sf.what}»?", "нет", YES_NO)
    fs = state["f"][fid]
    values = hist.get((st.key, path), [])
    if attr == "key":
        opts = {fs["key"], *values} | {state["f"][f]["key"] for f in state["order"][:2]}
        return (f"Как {who} называется в выгрузке поле {w}?", fs["key"], tuple(sorted(opts)))
    if attr == "fmt":
        opts = {FMT_SHORT[v] for v in values} | {FMT_SHORT[fs["fmt"]], FMT_SHORT[FMT_ALT[fs["fmt"]]]}
        return (f"Как {who} записано поле {w}?", FMT_SHORT[fs["fmt"]], tuple(sorted(opts)))
    if attr == "mul":
        if any(_frac(v) for v in [*values, fs["mul"]]):
            opts = {f"{_num_ru(v)} {sf.unit}" for v in [*values, fs["mul"]]} | {f"{x} {sf.unit}" for x in FRACTIONS.values()}
            return (f"Какая {who} единица у поля {w}?", f"{_num_ru(fs['mul'])} {sf.unit}", tuple(sorted(opts)))
        return (f"Сколько {sf.unit} {who} в одной единице поля {w}?", _num_answer(fs["mul"]), None)
    if attr == "add":
        return (f"Сколько {sf.unit} {who} вычитается из поля {w} после пересчёта масштаба?", _num_answer(-fs["add"]),
                None)
    if attr == "null":
        def label(v) -> str:
            return "нет такого значения" if v is None else _hexpat(fs["fmt"], v)

        opts = {label(v) for v in values} | {label(fs["null"]), "нет такого значения"}
        return (f"Какое сырое значение {who} означает «нет данных» в поле {w}?", label(fs["null"]), tuple(sorted(opts)))
    items = _ids(fs[attr])
    pick = R.choice(items)
    if ref is not None:
        other = _ids(ref["f"][fid][attr])
        moved = [x for i, x in enumerate(items) if x not in other or other.index(x) != i]
        pick = moved[0] if moved else pick
    if attr == "enum":
        return (f"Каким кодом {who} обозначено `{pick}` в поле {w}?", str(items.index(pick)), None)
    return (f"Какой бит {who} отвечает за `{pick}` в поле {w}?", str(items.index(pick)), None)


def _cands(st: ST, state: dict) -> list[str]:
    meta = _meta(st)
    hist = _history()
    out = []
    for fid in state["order"]:
        fs, sf = state["f"][fid], meta[fid]
        out.append(f"{fid}.key")
        out.append(f"{fid}.fmt")
        if fs["enum"] or fs["flags"]:
            out.append(f"{fid}.{'enum' if fs['enum'] else 'flags'}")
        elif fs["fmt"] != "bool" and sf.unit:
            out.append(f"{fid}.mul")
            if any(hist.get((st.key, f"{fid}.add"), [0])):
                out.append(f"{fid}.add")
        if fs["null"] is not None or len(hist.get((st.key, f"{fid}.null"), ())) > 1:
            out.append(f"{fid}.null")
    return out


def _prop_questions(st: ST, state: dict, R) -> list[tuple]:
    fin = _final_state(st)
    paths = ["@tid"]
    if state["dropped"] is False and not st.dropped:
        if state["name"] != fin["name"]:
            paths.append("@name")
        if state["order"] != fin["order"]:
            paths.append("@order")
        cands = _cands(st, state)
        paths += [p for p in cands if p.split(".")[0] in fin["f"] and _get(state, p) != _get(fin, p)]
        rest = [p for p in cands if p not in paths]
        paths += R.sample(rest, max(0, min(len(rest), 5 - len(paths))))
    else:
        paths += R.sample(_cands(st, state), 1)
    return [_q_path(st, state, p, R, ref=fin) for p in paths[:6]]


def _all_paths(state: dict) -> dict:
    said = {"@tid": state["tid"], "@name": state["name"], "@dropped": state["dropped"], "@order": tuple(state["order"])}
    for fid in state["order"]:
        said.update({f"{fid}.{a}": state["f"][fid][a] for a in ATTRS})
    return said


_PREFIX = {"oleg": "F", "sergey": "F", "dina": "F", "artem": "F", "marina": "M", "pavel": "P", "timur": "T",
           "vera": "V", "irina": "X", "gleb": "D"}
_FW_KINDS = {"prop", "fix", "revert", "yes", "no", "drop", "example", "sumok", "parseok"}
_OTHER_KINDS = {"ask", "claim", "sum", "parse"}
_ASK_Q = ("Задаёт ли автор вопрос или утверждает факт?", "вопрос", ("вопрос", "утверждение"))


_LAMPS = ("oil:давление масла", "coolant:перегрев двигателя", "brake:тормозная система", "abs:ABS",
          "battery:зарядка", "engine:неисправность двигателя", "glow:свечи накала", "adblue:AdBlue",
          "tpms:давление в шинах")
_LAMPS_PROP = (_LAMPS[0], _LAMPS[1], _LAMPS[3], _LAMPS[2], *_LAMPS[4:])
_SEATS = ("driver:водитель", "passenger:пассажир", "middle:средний")

SENSORS = (
    # --- CAN bus -------------------------------------------------------------------------------------------
    ST("can_fuel", 0x20, "can_fuel", "расхода топлива по CAN",
       "Первая запись — топливо из шины двигателя. Сами мы ничего не считаем: берём у блока управления двигателем "
       "общий счётчик израсходованного топлива, мгновенный расход и то, что он называет экономичностью, плюс "
       "уровень в баке, если его отдаёт щиток.",
       (SF("total_l", "общий израсходованный объём", "u32", 0.5, unit="л", null=0xFFFFFFFF, lo=20_000, hi=9_000_000,
           note="счётчик за всю жизнь двигателя, он не обнуляется"),
        SF("rate_lph", "мгновенный расход", "u16", 0.05, unit="л/ч", null=0xFFFF, lo=0, hi=4000),
        SF("economy_kml", "мгновенная экономичность", "u16", 1 / 512, unit="км/л", null=0xFFFF, lo=0, hi=12_000,
           note="на стоянке блок отдаёт ноль"),
        SF("level_pct", "уровень в баке по щитку", "u8", 0.4, unit="%", null=0xFF, lo=0, hi=250,
           note="это тот же поплавок, что и стрелка на приборке"),
        SF("idle_l", "израсходовано на холостом ходу", "u32", 0.5, unit="л", null=0xFFFFFFFF, lo=1000, hi=900_000))),
    ST("can_hours", 0x21, "can_hours", "моточасов",
       "Вторая — моточасы, как их ведёт блок двигателя: общее время работы и время на холостом ходу, а заодно "
       "сколько осталось проехать до планового обслуживания — это блок тоже считает сам.",
       (SF("idle_h", "время работы на холостом ходу", "u32", 0.05, unit="ч", null=0xFFFFFFFF, lo=400, hi=1_500_000),
        SF("engine_h", "общее время работы двигателя", "u32", 0.05, unit="ч", null=0xFFFFFFFF, lo=2000,
           hi=3_000_000),
        SF("service_km", "пробег до планового обслуживания", "i16", 5, unit="км", null=0x7FFF, lo=-2000, hi=12_000,
           note="минус — обслуживание просрочено"),
        SF("starts", "число пусков двигателя", "u16", null=0xFFFF, lo=0, hi=60_000, note="счётчик пусков стартером"),
        SF("pto_h", "время работы отбора мощности", "u32", 0.05, unit="ч", null=0xFFFFFFFF, lo=0, hi=400_000,
           opt="это поле есть только у машин с коробкой отбора мощности", late=True))),
    ST("dash_lamps", 0x22, "dash_lamps", "ламп приборной панели",
       "Третья — контрольные лампы на приборной панели, те, что горят у водителя перед глазами. Щиток отдаёт их "
       "по шине набором битов, мы копируем набор как есть, ничего не перекладывая.",
       (SF("lamps", "набор горящих ламп", "u16", flags=_LAMPS),
        SF("blinking", "набор мигающих ламп", "u16", flags=_LAMPS, note="мигание — более срочное, чем ровный свет"),
        SF("level", "степень тревоги", "u8", enum=("info:для сведения", "warning:предупреждение",
                                                   "stop:немедленная остановка"), null=0xFF),
        SF("source", "адрес блока, который зажёг лампу", "u8", lo=0, hi=253, note="адрес на шине, как в диагностике"),
        SF("speed_kmh", "скорость в момент изменения", "u8", unit="км/ч", null=0xFF, lo=0, hi=120))),
    ST("adblue", 0x23, "adblue", "бака AdBlue",
       "Четвёртая — бак мочевины на машинах Евро-5 и Евро-6: сколько раствора осталось, какой он температуры и "
       "сколько его сейчас уходит.",
       (SF("level_pct", "уровень в баке мочевины", "u8", 0.4, unit="%", null=0xFF, lo=0, hi=250),
        SF("temp_c", "температура раствора", "u8", add=-40, unit="°C", null=0xFF, lo=10, hi=120,
           note="раствор замерзает при минус одиннадцати, поэтому её и пишем"),
        SF("dose_lph", "текущий расход раствора", "u16", 0.01, unit="л/ч", null=0xFFFF, lo=0, hi=900),
        SF("tank_l", "объём бака мочевины", "u8", unit="л", lo=20, hi=90,
           note="берётся из настройки, чтобы переводить проценты в литры"),
        SF("quality", "качество раствора", "u8", enum=("ok:норма", "diluted:разбавлен", "wrong:не мочевина"),
           null=0xFF, opt="его отдают только двигатели с датчиком качества", late=True))),
    ST("can_gear", 0x24, "can_gear", "коробки передач",
       "Пятая — коробка передач: какая передача включена, какую просит водитель и сколько тормозит ретардер.",
       (SF("gear", "включённая передача", "i8", null=0x7F, lo=-2, hi=16, note="минус — задний ход, ноль — нейтраль"),
        SF("requested", "запрошенная передача", "i8", lo=-2, hi=16),
        SF("retarder_pct", "момент ретардера", "u8", unit="%", lo=0, hi=100)), dropped=True),
    ST("brakes", 0x25, "brakes", "тормозов",
       "И ещё одна запись в CAN-часть, её не было в плане Олега, — тормоза: насколько нажата педаль, что делает "
       "ретардер, вмешивается ли ABS и какое давление в тормозном контуре.",
       (SF("pedal_pct", "положение педали тормоза", "u8", 0.4, unit="%", null=0xFF, lo=0, hi=250),
        SF("retarder_pct", "момент ретардера", "i8", unit="%", null=-128, lo=-125, hi=125,
           note="минус — ретардер тормозит, плюс — помогает разгону"),
        SF("abs", "состояние ABS", "u8", enum=("off:не вмешивается", "active:тормозит колесо", "error:неисправность"),
           null=3),
        SF("air_kpa", "давление в тормозном контуре", "u8", 8, unit="кПа", null=0xFF, lo=40, hi=150),
        SF("parking", "стояночный тормоз затянут", "bool"))),
    ST("pto", 0x55, "pto", "коробки отбора мощности",
       "Шестая — коробка отбора мощности на спецтехнике: мусоровозах, кранах, бетоновозах. Пишем, включена ли она, "
       "как крутится вал и сколько она работает с последнего включения.",
       (SF("state", "состояние", "u8", enum=("off:выключена", "on:включена", "fault:неисправность")),
        SF("shaft_rpm", "обороты вала", "u16", 0.125, unit="об/мин", null=0xFFFF, lo=0, hi=16_000),
        SF("engaged_s", "время с момента включения", "uv", unit="с", lo=0, hi=200_000),
        SF("load_pct", "нагрузка на вал", "u8", unit="%", null=0xFF, lo=0, hi=100),
        SF("hydraulic_bar", "давление в гидросистеме надстройки", "u16", 0.1, unit="бар", null=0xFFFF, lo=0,
           hi=3500))),
    # --- tachograph ----------------------------------------------------------------------------------------
    ST("tacho_activity", 0x28, "tacho_activity", "режимов по тахографу",
       "Первая — режим работы водителя, как его видит тахограф. У тахографа два слота для карт, водителя и "
       "сменщика, и запись пишется на каждый слот отдельно, когда режим меняется.",
       (SF("slot", "слот", "u8", enum=("driver:водитель", "codriver:сменщик")),
        SF("activity", "режим", "u8", enum=("rest:отдых", "available:готовность", "work:работа", "drive:вождение"),
           null=7),
        SF("card", "карта в слоте", "bool"),
        SF("crew", "работа экипажем", "bool", note="когда в машине два водителя"),
        SF("mode_min", "сколько минут водитель уже в этом режиме", "u16", unit="мин", null=0xFFFF, lo=0, hi=900))),
    ST("tacho_times", 0x29, "tacho_times", "счётчиков времени тахографа",
       "Вторая — счётчики, которые тахограф ведёт для водителя в первом слоте: сколько ещё можно ехать до "
       "обязательного отдыха, сколько он уже проехал за сутки и за неделю.",
       (SF("drive_left_min", "оставшееся время вождения", "u16", unit="мин", null=0xFFFF, lo=0, hi=270),
        SF("daily_min", "время вождения за сутки", "u16", unit="мин", null=0xFFFF, lo=0, hi=600),
        SF("weekly_min", "время вождения за неделю", "u16", unit="мин", null=0xFFFF, lo=0, hi=3360),
        SF("rest_left_min", "время до конца обязательного отдыха", "u16", unit="мин", null=0xFFFF, lo=0, hi=660),
        SF("break_min", "накопленный перерыв", "u8", unit="мин", null=0xFF, lo=0, hi=45, late=True))),
    ST("tacho_speed", 0x2A, "tacho_speed", "скорости и пробега по тахографу",
       "Третья — скорость и пробег так, как их считает тахограф. Это юридически значимые цифры, и с нашими по GPS "
       "они сходятся не всегда, поэтому пишем их отдельно.",
       (SF("kmh", "скорость по тахографу", "U16", 1 / 256, unit="км/ч", null=0xFFFF, lo=0, hi=30_000),
        SF("distance_km", "пробег по тахографу", "U32", 0.005, unit="км", null=0xFFFFFFFF, lo=1000, hi=400_000_000),
        SF("overspeed", "превышение скорости", "bool"),
        SF("direction", "направление движения", "u8", enum=("forward:вперёд", "reverse:назад")),
        SF("rpm", "обороты двигателя по тахографу", "U16", 0.125, unit="об/мин", null=0xFFFF, lo=0, hi=20_000))),
    ST("tacho_card", 0x2B, "tacho_card", "карт в тахографе",
       "Четвёртая — вставка и извлечение карт в тахографе.",
       (SF("slot", "слот", "u8", enum=("driver:водитель", "codriver:сменщик")),
        SF("event", "событие", "u8", enum=("inserted:вставлена", "removed:извлечена")),
        SF("card", "номер карты", "uv", lo=10**6, hi=10**12)), dropped=True),
    # --- trailer -------------------------------------------------------------------------------------------
    ST("trailer", 0x30, "trailer", "сцепки с прицепом",
       "Первая — сцепка с прицепом и расцепка. Данные берём у блока EBS прицепа по разъёму ISO 7638: он сообщает "
       "свой идентификатор, число осей и длину.",
       (SF("event", "событие", "u8", enum=("coupled:сцепили", "uncoupled:расцепили", "mismatch:прицеп не из списка")),
        SF("trailer_id", "идентификатор прицепа", "uv", lo=100_000, hi=10**11),
        SF("axles", "число осей прицепа", "u8", null=0, lo=1, hi=4, note="ноль — блок не сообщил"),
        SF("length_m", "длина прицепа", "u8", 0.1, unit="м", null=0xFF, lo=40, hi=160),
        SF("abs_ok", "ABS прицепа исправна", "bool"))),
    ST("trailer_ebs", 0x31, "trailer_ebs", "тормозной системы прицепа",
       "Вторая — блок EBS прицепа: суммарная нагрузка на его оси, износ колодок, температура тормозов и "
       "предупреждения, которые блок зажигает у водителя.",
       (SF("load_kg", "суммарная нагрузка на оси прицепа", "u16", 2, unit="кг", null=0xFFFF, lo=1000, hi=18_000),
        SF("pad_wear_pct", "износ колодок", "u8", 0.4, unit="%", null=0xFF, lo=0, hi=250),
        SF("brake_c", "температура тормозов", "u16", 0.03125, -273, unit="°C", null=0xFFFF, lo=8000, hi=20_000),
        SF("warnings", "предупреждения", "u8", flags=("abs:ABS прицепа", "red:красная лампа EBS",
                                                      "amber:жёлтая лампа EBS", "supply:низкое давление питания")),
        SF("lift_axle", "подъёмная ось прицепа поднята", "bool"))),
    ST("trailer_lights", 0x32, "trailer_lights", "фонарей прицепа",
       "Третья — фонари прицепа: какие горят и какие перегорели, по модулю контроля ламп.",
       (SF("lamps", "горящие фонари", "u8", flags=("left:левый поворот", "right:правый поворот", "stop:стоп",
                                                   "reverse:задний ход", "fog:противотуманный")),
        SF("failed", "перегоревшие фонари", "u8", flags=("left:левый поворот", "right:правый поворот", "stop:стоп",
                                                         "reverse:задний ход", "fog:противотуманный")),
        SF("current_a", "ток цепи фонарей", "u8", 0.1, unit="А", lo=0, hi=200)), dropped=True),
    ST("trailer_power", 0x33, "trailer_power", "питания прицепа",
       "Четвёртая — питание прицепа по разъёму: какое напряжение ушло на прицеп, какой ток он берёт и есть ли "
       "питание на отдельной линии ABS.",
       (SF("volts", "напряжение на разъёме", "u16", 0.05, unit="В", null=0xFFFF, lo=180, hi=600),
        SF("amps", "ток", "i16", 0.1, unit="А", null=0x7FFF, lo=-300, hi=1200,
           note="минус — прицеп сам отдаёт ток, так бывает у рефрижераторов с генератором"),
        SF("abs_line", "питание на линии ABS", "bool"),
        SF("socket", "разъём", "u8", enum=("iso7638:ISO 7638", "iso12098:ISO 12098", "adapter:переходник")),
        SF("lamp_amps", "ток освещения прицепа", "u8", 0.1, unit="А", null=0xFF, lo=0, hi=200))),
    # --- reefer --------------------------------------------------------------------------------------------
    ST("reefer_zone", 0x38, "reefer_zone", "температур отсека рефрижератора",
       "Первая — температуры по отсеку рефрижератора: уставка, воздух на входе установки и на выходе. "
       "Многотемпературные фургоны пишут по записи на каждый отсек, отсеки считаются от кабины.",
       (SF("zone", "номер отсека", "u8", lo=1, hi=3),
        SF("setpoint_c", "уставка", "i16", 0.1, unit="°C", null=0x7FFF, lo=-300, hi=150),
        SF("return_c", "температура возвратного воздуха", "i16", 0.1, unit="°C", null=0x7FFF, lo=-320, hi=300,
           note="то есть воздуха, который установка забирает из кузова"),
        SF("supply_c", "температура подаваемого воздуха", "i16", 0.1, unit="°C", null=0x7FFF, lo=-350, hi=280),
        SF("humidity_pct", "влажность в отсеке", "u8", 0.5, unit="%", null=0xFF, lo=0, hi=200),
        SF("coil_c", "температура испарителя", "i16", 0.1, unit="°C", null=0x7FFF, lo=-400, hi=200,
           opt="её отдают только установки с датчиком на испарителе"))),
    ST("reefer_mode", 0x39, "reefer_mode", "режима рефрижератора",
       "Вторая — режим установки, код тревоги и вентилятор. Пишется при каждом изменении и раз в десять минут.",
       (SF("mode", "режим", "u8", enum=("off:выключена", "cool:охлаждение", "heat:нагрев", "defrost:оттайка",
                                        "standby:ожидание")),
        SF("alarm", "код тревоги установки", "u16", lo=0, hi=99, note="ноль — тревоги нет"),
        SF("fan", "вентилятор", "u8", enum=("off:стоит", "low:малые обороты", "high:большие обороты"), null=0xFF),
        SF("door_stop", "остановка установки открытой дверью", "bool"),
        SF("compressor_pct", "загрузка компрессора", "u8", unit="%", null=0xFF, lo=0, hi=100))),
    ST("reefer_unit", 0x3A, "reefer_unit", "состояния рефрижераторной установки",
       "Третья — сама установка: наработка её двигателя, топливо в её собственном баке, напряжение её аккумулятора "
       "и работает ли она от сети на стоянке.",
       (SF("run_h", "наработка установки", "uv", 0.1, unit="ч", lo=100, hi=400_000),
        SF("fuel_pct", "топливо в баке установки", "u8", unit="%", null=0xFF, lo=0, hi=100),
        SF("battery_v", "напряжение аккумулятора установки", "u8", 0.2, unit="В", null=0xFF, lo=50, hi=150),
        SF("on_grid", "работа от сети", "bool"),
        SF("ambient_c", "температура снаружи", "i8", unit="°C", null=-128, lo=-40, hi=50))),
    ST("reefer_doors", 0x3B, "reefer_doors", "дверей рефрижератора",
       "Четвёртая — двери отсеков рефрижератора: какой отсек, открыта ли дверь и сколько секунд она была открыта.",
       (SF("zone", "номер отсека", "u8", lo=1, hi=3),
        SF("open", "дверь открыта", "bool"),
        SF("open_s", "сколько дверь была открыта", "u16", unit="с", lo=0, hi=3600)), dropped=True),
    # --- loads and body ------------------------------------------------------------------------------------
    ST("axle_loads", 0x40, "axle_loads", "нагрузок на оси",
       "Первая — нагрузки на оси: по датчикам давления в пневмоподвеске или по тензодатчикам, если они стоят. "
       "Прицепные оси сюда не входят, у прицепа своя запись.",
       (SF("loads_kg", "нагрузка на ось", "u16", 10, unit="кг", null=0xFFFF, lo=200, hi=1300,
           group=("осей", "на каждую ось, от передней к задней,", 2, 5)),
        SF("total_kg", "суммарная нагрузка", "u16", 20, unit="кг", null=0xFFFF, lo=100, hi=2000),
        SF("lifted", "число поднятых осей", "u8", lo=0, hi=2, note="у машин с подъёмными осями"),
        SF("overload", "перегруз", "bool"),
        SF("axle_limit_kg", "допустимая нагрузка на ось", "u16", 10, unit="кг", lo=600, hi=1150,
           note="её вводят при установке по паспорту машины"))),
    ST("body_tilt", 0x41, "body_tilt", "наклона кузова",
       "Вторая — наклон машины по встроенному акселерометру: продольный и поперечный, а у самосвалов ещё и подъём "
       "кузова.",
       (SF("pitch_deg", "продольный наклон", "i16", 0.1, unit="°", lo=-300, hi=300, note="плюс — нос вверх"),
        SF("roll_deg", "поперечный наклон", "i16", 0.1, unit="°", lo=-250, hi=250, note="плюс — крен на правый борт"),
        SF("body_up", "кузов поднят", "bool"),
        SF("body_angle_deg", "угол подъёма кузова", "u8", 0.5, unit="°", null=0xFF, lo=0, hi=110),
        SF("vibration_g", "вибрация кузова", "u8", 0.02, unit="g", lo=0, hi=200))),
    ST("weight", 0x42, "weight", "веса",
       "Третья — вес: полная масса машины и масса груза, как их считает весовой модуль, и откуда он их взял.",
       (SF("gross_kg", "полная масса", "u16", 20, unit="кг", null=0xFFFF, lo=150, hi=2200),
        SF("payload_kg", "масса груза", "i16", 20, unit="кг", null=0x7FFF, lo=-40, hi=1500,
           note="после тарирования пустой машины бывает и чуть меньше нуля"),
        SF("method", "источник", "u8", enum=("suspension:пневмоподвеска", "load_cells:тензодатчики", "ebs:блок EBS")),
        SF("calibrated", "признак тарирования", "bool"),
        SF("trailer_kg", "масса прицепа", "u16", 20, unit="кг", null=0xFFFF, lo=0, hi=1800))),
    ST("mixer_drum", 0x43, "mixer_drum", "барабана бетоносмесителя",
       "Четвёртая — барабан бетоносмесителя: как он крутится, сколько воды долили в смесь, сколько смеси в "
       "барабане и какой она температуры.",
       (SF("rpm", "обороты барабана", "i16", 0.1, unit="об/мин", lo=-150, hi=180, note="минус — вращение на выгрузку"),
        SF("direction", "направление вращения", "u8", enum=("mix:замес", "unload:выгрузка"), gone=True),
        SF("water_l", "долитая вода", "u16", 0.1, unit="л", null=0xFFFF, lo=0, hi=3000),
        SF("load_m3", "объём смеси", "u8", 0.1, unit="м³", lo=0, hi=120),
        SF("drum_temp_c", "температура смеси", "i8", unit="°C", null=-128, lo=-10, hi=45),
        SF("slump_mm", "осадка конуса", "u8", 2, unit="мм", null=0xFF, lo=5, hi=120,
           note="подвижность смеси по датчику давления в приводе барабана"))),
    # --- cabin ---------------------------------------------------------------------------------------------
    ST("adas", 0x48, "adas", "предупреждений ассистента водителя",
       "Первая — предупреждения камеры ассистента водителя: что она увидела, на какой скорости ехала машина, "
       "сколько оставалось до столкновения и до впереди идущей машины.",
       (SF("kind", "вид предупреждения", "u8", enum=("fcw:угроза столкновения", "ldw_left:съезд с полосы влево",
                                                     "ldw_right:съезд с полосы вправо", "pcw:пешеход",
                                                     "hmw:опасная дистанция")),
        SF("speed_kmh", "скорость", "u8", unit="км/ч", null=0xFF, lo=0, hi=140),
        SF("ttc_s", "время до столкновения", "u8", 0.1, unit="с", null=0xFF, lo=3, hi=60),
        SF("distance_m", "дистанция до впереди идущей машины", "u8", 0.5, unit="м", null=0xFF, lo=4, hi=200),
        SF("headway_s", "интервал до впереди идущей машины", "u8", 0.1, unit="с", null=0xFF, lo=5, hi=50))),
    ST("fatigue", 0x49, "fatigue", "усталости водителя",
       "Вторая — камера усталости, которая смотрит на водителя: что она заметила, насколько уверена и сколько это "
       "длилось.",
       (SF("kind", "что замечено", "u8", enum=("eyes_closed:закрытые глаза", "yawn:зевота", "phone:телефон",
                                               "smoking:курение", "no_belt:не пристёгнут",
                                               "distracted:взгляд в сторону")),
        SF("confidence_pct", "уверенность камеры", "u8", unit="%", lo=30, hi=100),
        SF("duration_ms", "длительность", "u16", 10, unit="мс", lo=20, hi=3000),
        SF("speed_kmh", "скорость в этот момент", "u8", unit="км/ч", null=0xFF, lo=0, hi=130),
        SF("head_deg", "поворот головы водителя", "i8", unit="°", null=-128, lo=-90, hi=90, note="плюс — вправо"))),
    ST("seatbelts", 0x4A, "seatbelts", "ремней безопасности",
       "Третья — ремни безопасности: какие пристёгнуты. Пишется при каждом изменении.",
       (SF("fastened", "пристёгнутые ремни", "u8", flags=_SEATS),
        SF("occupied", "занятые сиденья", "u8", flags=_SEATS, late=True),
        SF("speed_kmh", "скорость в момент изменения", "u8", unit="км/ч", null=0xFF, lo=0, hi=120),
        SF("buzzer", "зуммер включён", "bool"),
        SF("unbuckled_s", "сколько секунд ремень водителя не пристёгнут", "u16", unit="с", null=0xFFFF, lo=0,
           hi=3600))),
    ST("cabin_air", 0x4B, "cabin_air", "воздуха в кабине",
       "Четвёртая — воздух в кабине: температура, влажность и углекислый газ.",
       (SF("temp_c", "температура в кабине", "i8", unit="°C", lo=-30, hi=50),
        SF("humidity_pct", "влажность в кабине", "u8", 0.5, unit="%", lo=0, hi=200),
        SF("co2_ppm", "углекислый газ", "u16", unit="ppm", lo=300, hi=5000)), dropped=True),
    ST("alcolock", 0x4C, "alcolock", "алкозамка",
       "Пятая — алкозамок: результат теста перед поездкой, измеренная концентрация, номер попытки и температура "
       "прибора — на морозе он врёт, и сервису это важно.",
       (SF("result", "результат", "u8", enum=("pass:норма", "fail:превышение", "refused:отказ от теста",
                                              "error:ошибка прибора")),
        SF("bac_permille", "концентрация", "u16", 0.001, unit="‰", null=0xFFFF, lo=0, hi=2500),
        SF("attempt", "номер попытки", "u8", lo=1, hi=5),
        SF("device_c", "температура прибора", "i8", unit="°C", null=-128, lo=-35, hi=50),
        SF("breath_ml", "объём выдоха", "u16", unit="мл", lo=800, hi=3000))),
    # --- cargo and security --------------------------------------------------------------------------------
    ST("cargo_temps", 0x50, "cargo_temps", "температур груза",
       "Первая — щупы температуры в самом грузе, не в воздухе: их втыкают в коробки с лекарствами и в туши. Щупы "
       "подключены к отдельному регистратору, и запись несёт его номер, заряд и показания всех щупов.",
       (SF("logger_id", "номер регистратора", "u16", lo=1, hi=60_000),
        SF("battery_pct", "заряд регистратора", "u8", unit="%", null=0xFF, lo=0, hi=100),
        SF("temps_c", "температура щупа", "i16", 0.01, unit="°C", null=0x7FFF, lo=-2500, hi=2500,
           group=("щупов", "по каждому щупу", 1, 4)),
        SF("alarm", "выход за допустимую температуру", "bool"))),
    ST("humidity", 0x51, "humidity", "влажности в кузове",
       "Вторая — влажность в кузове, для фармацевтики и цветов: относительная влажность, температура самого "
       "датчика и точка росы, которую он считает сам.",
       (SF("rh_pct", "относительная влажность", "u16", 0.01, unit="%", null=0xFFFF, lo=500, hi=10_000),
        SF("temp_c", "температура датчика влажности", "i16", 0.1, unit="°C", null=0x7FFF, lo=-300, hi=400),
        SF("dew_c", "точка росы", "i16", 0.1, unit="°C", null=0x7FFF, lo=-400, hi=250),
        SF("condensation", "признак конденсата", "bool"),
        SF("pressure_hpa", "давление воздуха в кузове", "u16", 0.1, unit="гПа", null=0xFFFF, lo=9000, hi=10_500))),
    ST("seal", 0x52, "seal", "электронных пломб",
       "Третья — электронные пломбы на дверях кузова и на горловинах баков: пломба сама шлёт трекеру по радио свой "
       "номер, что случилось, заряд батареи и уровень сигнала.",
       (SF("seal_id", "номер пломбы", "uv", lo=1000, hi=10**9),
        SF("event", "событие", "u8", enum=("closed:закрыта", "opened:вскрыта", "tamper:попытка взлома",
                                           "low_battery:садится батарея")),
        SF("battery_pct", "заряд батареи пломбы", "u8", unit="%", null=0xFF, lo=0, hi=100),
        SF("rssi_dbm", "уровень сигнала пломбы", "i8", unit="дБм", lo=-110, hi=-30),
        SF("openings", "счётчик вскрытий пломбы", "u16", lo=0, hi=5000))),
    ST("fuel_drain", 0x53, "fuel_drain", "сливов топлива",
       "Четвёртая — слив топлива: трекер сам замечает резкое падение уровня в баке и пишет, сколько ушло, за какое "
       "время, из какого бака и ехала ли машина.",
       (SF("liters", "слитый объём", "u16", 0.1, unit="л", lo=50, hi=4000),
        SF("duration_s", "длительность слива", "u16", unit="с", lo=10, hi=1800),
        SF("tank", "номер бака", "u8", lo=1, hi=2),
        SF("moving", "машина ехала", "bool"),
        SF("level_after_pct", "уровень в баке после слива", "u8", 0.4, unit="%", null=0xFF, lo=0, hi=250))),
    ST("panic", 0x54, "panic", "тревожной кнопки",
       "Пятая — тревожная кнопка: какая нажата, сколько её держали и включена ли тихая тревога.",
       (SF("button", "кнопка", "u8", enum=("cabin:в кабине", "cargo:в кузове")),
        SF("pressed_s", "сколько держали", "u16", unit="с", lo=0, hi=60),
        SF("silent", "тихая тревога", "bool")), dropped=True),
    # --- municipal vehicles (3.5.1) ------------------------------------------------------------------------
    ST("spreader", 0x58, "spreader", "пескоразбрасывателя",
       "Первая — пескосоляной разбрасыватель на дорожной технике: сколько реагента уходит на квадратный метр, какой "
       "ширины полоса, что за реагент и сколько его осталось в бункере.",
       (SF("rate_gm2", "норма распределения", "u16", unit="г/м²", null=0xFFFF, lo=0, hi=400),
        SF("width_m", "ширина полосы", "u8", 0.1, unit="м", null=0xFF, lo=20, hi=120),
        SF("hopper_pct", "остаток в бункере", "u8", unit="%", null=0xFF, lo=0, hi=100),
        SF("material", "реагент", "u8", enum=("sand:песок", "salt:соль", "mix:смесь", "brine:рассол")),
        SF("disc_rpm", "обороты разбрасывающего диска", "u16", unit="об/мин", null=0xFFFF, lo=0, hi=900))),
    ST("bin_lift", 0x59, "bin_lift", "подъёма контейнеров",
       "Вторая — подъёмник мусоровоза: какой контейнер подняли (по метке RFID), с какой стороны машины, сколько он "
       "весил до и после опорожнения и сколько длился цикл.",
       (SF("tag", "метка контейнера", "uv", lo=10**6, hi=10**12),
        SF("side", "сторона", "u8", enum=("left:слева", "right:справа", "rear:сзади")),
        SF("full_kg", "вес до опорожнения", "u16", 0.5, unit="кг", null=0xFFFF, lo=0, hi=2000),
        SF("empty_kg", "вес после опорожнения", "u16", 0.5, unit="кг", null=0xFFFF, lo=0, hi=600),
        SF("cycle_s", "длительность цикла", "u8", unit="с", lo=5, hi=90))),
)
SENSOR_BY_KEY = {st.key: st for st in SENSORS}
SENSOR_BY_TID = {st.tid: st for st in SENSORS if not st.dropped}


def _proposals() -> dict:
    """Each type as first proposed in the mail."""
    props: dict = {}
    for message in _chrono():
        for ev in message[6]:
            if ev[0] == "prop" and ev[1] not in props:
                props[ev[1]] = _initial_state(SENSOR_BY_KEY[ev[1]], ev[2])
    return props


@functools.cache
def _sensor_gen() -> dict:
    """tid -> ("final", type, final state) for released types, ("junk", type, proposal) for numbers that
    pilot builds wrote without a released meaning (dropped types, the abandoned number of the PTO record)."""
    props = _proposals()
    out: dict = {}
    for st in SENSORS:
        out[st.tid] = ("junk", st, props[st.key]) if st.dropped else ("final", st, _final_state(st))
        if props[st.key]["tid"] != st.tid:
            out[props[st.key]["tid"]] = ("junk", st, props[st.key])
    return dict(sorted(out.items()))


def _sensor_features(rtype: int, vals: dict) -> set[str]:
    st = SENSOR_BY_TID[rtype]
    fin = _final_state(st)
    meta = _meta(st)
    out = {f"s:{st.key}"}
    for fid in fin["order"]:
        fs, sf, tag = fin["f"][fid], meta[fid], f"s:{st.key}.{fid}"
        if fid not in vals:
            out.add(tag + ":absent")
            continue
        if sf.opt:
            out.add(tag + ":present")
        for v in vals[fid] if sf.group else [vals[fid]]:
            if fs["null"] is not None and v == fs["null"]:
                out.add(tag + ":null")
            elif fs["enum"]:
                out.add(f"{tag}:e{v}")
            elif fs["fmt"] == "bool":
                out.add(f"{tag}:b{v}")
            elif fs["flags"]:
                out.add(tag + (":flags" if v else ":noflags"))
            elif v < 0:
                out.add(tag + ":neg")
    return out


def _sensor_required() -> list[str]:
    """Features every hidden set must exercise: each type, null marker, sign, code, flag and optional tail."""
    req = [f"type{tid}raw" for tid, (kind, _st, _s) in _sensor_gen().items() if kind == "junk"]
    for st in SENSORS:
        if st.dropped:
            continue
        fin, meta = _final_state(st), _meta(st)
        req.append(f"s:{st.key}")
        for fid in fin["order"]:
            fs, sf, tag = fin["f"][fid], meta[fid], f"s:{st.key}.{fid}"
            if sf.opt:
                req += [tag + ":absent", tag + ":present"]
            if fs["null"] is not None:
                req.append(tag + ":null")
            if fs["enum"]:
                req += [f"{tag}:e{i}" for i in range(len(fs["enum"]))]
            elif fs["flags"]:
                req.append(tag + ":flags")
            elif fs["fmt"] == "bool":
                req += [tag + ":b0", tag + ":b1"]
            elif sf.lo < 0:
                req.append(tag + ":neg")
    return req


def _decoder_src(states: dict) -> str:
    """The reference decoder with a sensor-pack table built from `states` (beliefs about each type)."""
    lines = "".join(f"    {tid}: {spec!r},\n" for tid, spec in _table(states).items())
    return _DECODER_SRC.replace("SENSORS = {}\n", "SENSORS = {\n" + lines + "}\n")


DECODER = _decoder_src({st.key: _final_state(st) for st in SENSORS})


@functools.cache
def _chrono() -> tuple:
    """The story in date order (stable for messages of one day)."""
    return tuple(sorted(MSGS3, key=lambda m: m[1]))


def _run(skip: frozenset = frozenset(), trust: bool = False) -> dict:
    """Beliefs about every sensor type after the story; `skip` drops events, `trust` believes everyone."""
    states: dict = {}
    for label, _date, _sender, _subject, _parent, _size, events in _chrono():
        for i, ev in enumerate(events):
            kind, t = ev[0], ev[1]
            if (label, i) in skip:
                continue
            if kind == "prop":
                states[t] = _initial_state(SENSOR_BY_KEY[t], ev[2])
            elif kind in ("fix", "revert", "yes"):
                _apply(states[t], ev[2])
            elif kind == "drop":
                states[t]["dropped"] = True
            elif kind == "note" and len(ev) > 4 and ev[4]:
                _apply(states[t], ev[4])
            elif trust and kind in _OTHER_KINDS and ev[2]:
                _apply(states[t], ev[2])
    return states


def _value_questions(st: ST, state: dict, shown: dict, vals: dict, R, who: str) -> list[tuple]:
    meta = _meta(st)
    out = []
    fids = [f for f in state["order"] if f in vals and not meta[f].group]
    R.shuffle(fids)
    for fid in fids:
        sf, fs = meta[fid], shown["f"][fid]
        value = _shown_value(state, shown, fid, vals[fid])
        if fs["enum"] and value is not None:
            out.append((f"Какое значение поля «{sf.what}» получилось {who}?", value, _ids(fs["enum"])))
        elif isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
            unit = f" (в {sf.unit})" if sf.unit else ""
            out.append((f"Какое значение поля «{sf.what}»{unit} получилось {who}?", _num_answer(value), None))
        if len(out) == 2:
            break
    return out


@functools.cache
def _story() -> dict:
    """Draft text and judge questions of every sensor-pack paragraph, plus the third-wave threads."""
    states: dict = {}
    blocks: dict = {}
    stated: dict = {}
    threads = []
    pending: dict = {}
    by_label = {m[0]: m for m in MSGS3}
    for label, date, sender, subject, parent, size, events in _chrono():
        fw = sender in FIRMWARE
        addr = PEOPLE[by_label[parent][2]][2] if parent else "Коллеги"
        keys = []
        for i, ev in enumerate(events):
            kind, t = ev[0], ev[1]
            assert (kind in _FW_KINDS and fw) or (kind in _OTHER_KINDS and not fw) or kind == "note", (label, kind)
            R = rng(f"{TASK_ID}:s3:{label}:{i}")
            st = SENSOR_BY_KEY.get(t)
            said: dict = {}
            why = (ev[2] if kind in ("drop", "sumok") and len(ev) > 2 else
                   ev[3] if kind in ("fix", "revert", "yes", "no") and len(ev) > 3 else "")
            if kind == "prop":
                s = states[t] = _initial_state(st, ev[2])
                order = s["order"]
                parts = [_field_text(_meta(st)[fid], s["f"][fid],
                                     R.choice(_POS_FIRST if j == 0 else _POS_LAST if j == len(order) - 1 else _POS_MID),
                                     R) for j, fid in enumerate(order)]
                text = (f"{st.intro} Номер типа — {_tid_text(s['tid'], R)}, в выгрузке запись называется "
                        f"`{s['name']}`.\n\n" + " ".join(parts))
                qs = _prop_questions(st, s, R)
                said = _all_paths(s)
            elif kind in ("fix", "revert", "yes"):
                before = copy.deepcopy(states[t])
                _apply(states[t], ev[2])
                s = states[t]
                lead = {"fix": "", "yes": f"{addr}, да, так и есть. ",
                        "revert": R.choice(("Тут возвращаемся к прежнему варианту. ", "Одно решение откатываем. "))}[kind]
                text = lead + " ".join(_change_text(st, before, s, ev[2], R, old=kind == "fix")) + (f" {why}" if why else "")
                qs = [_q_path(st, s, p, R, ref=before) for p in ev[2] if p != "@dropped"]
                said = {p: _get(s, p) for p in ev[2]}
            elif kind == "no":
                s = states[t]
                text = f"{addr}, нет. " + " ".join(_field_lines(st, s, ev[2], R)) + (f" {why}" if why else "")
                qs = [_q_path(st, s, p, R) for p in ev[2]]
                said = {p: _get(s, p) for p in ev[2]}
            elif kind in ("ask", "claim"):
                tmp = copy.deepcopy(states[t])
                _apply(tmp, ev[2])
                text = ev[3] + "".join(" " + x for x in _field_lines(st, tmp, ev[2], R, ask=kind == "ask"))
                qs = [_q_path(st, tmp, p, R, who="по словам автора") for p in ev[2]] + list(ev[4] if len(ev) > 4 else ())
                if kind == "ask":
                    qs.append(_ASK_Q)
            elif kind == "drop":
                states[t]["dropped"] = True
                text = (f"От записи {st.rw} отказываемся. {why} Тип {states[t]['tid']} в выпуск не идёт; если пилотные "
                        "сборки записи с этим номером всё-таки пишут, это не описанные данные.")
                qs = [(f"Войдёт ли по этому письму запись {st.rw} в выпуск?", "нет", YES_NO)]
                said = {"@dropped": True}
            elif kind == "example":
                s = states[t]
                ER = rng(f"{TASK_ID}:ex:{label}:{i}")
                pieces = []
                for k in range(ev[2] if len(ev) > 2 else 2):
                    vals = _gen_values(ER, st, s, pnull=0.25)
                    parts, body = _example_parts(st, s, s, vals)
                    if k == 0:
                        first, qvals = body, vals
                        place = R.choice((f"Для сверки — живая запись {st.rw} со стенда",
                                          f"Пример записи {st.rw} с машины клиента {R.choice(SLOTS['client'])}",
                                          f"Вот одна запись {st.rw} из журнала пилота в {R.choice(SLOTS['city'])}"))
                        pieces.append(f"{place}: после приращения времени лежат байты `{body.hex(' ').upper()}`. "
                                      "Разбор: " + "; ".join(parts) + ".")
                    else:
                        head = R.choice(("Ещё одна запись того же типа", "Вторая запись", "И ещё одна, для сравнения")
                                        if k == 1 else ("Третья запись", "И последняя", "Ещё одна запись"))
                        pieces.append(f"{head}: `{body.hex(' ').upper()}` — " + "; ".join(parts) + ".")
                text = " ".join(pieces)
                qs = _value_questions(st, s, s, qvals, R, "в первом примере из этого письма") or [
                    ("Сколько байт в первом примере лежит после приращения времени?", str(len(first)), None)]
                said = _all_paths(s)
            elif kind == "sum":
                tmp = copy.deepcopy(states[t])
                _apply(tmp, ev[2])
                pending[("sum", t)] = ev[2]
                meta = _meta(st)
                a = "а" if sender in ("marina", "vera", "irina") else ""
                text = (R.choice((f"Сверяюсь, прежде чем писать разбор, — проверьте, как я понял{a}.",
                                  f"Проверьте, пожалуйста, как я понял{a}.",
                                  f"Записываю, как понял{a}, поправьте, если не так."))
                        + f" Запись {st.rw}: номер {tmp['tid']}, в выгрузке `{tmp['name']}`. "
                        + " ".join(_field_text(meta[f], tmp["f"][f], "", R, compact=True) for f in tmp["order"])
                        + " " + R.choice(("Всё так?", f"Где я ошиб{'лась' if a else 'ся'}?",
                                          "Поправьте, если что-то не так.")))
                qs = [_q_path(st, tmp, p, R, who="по описанию автора") for p in list(ev[2])[:2]] or [
                    (f"Какой номер типа (десятичным числом) по описанию автора у записи {st.rw}?", str(tmp["tid"]), None)]
                qs.append(("Просит ли автор проверить описание?", "да", YES_NO))
            elif kind == "sumok":
                errs = pending.pop(("sum", t))
                s = states[t]
                if errs:
                    text = (f"{addr}, по записи {st.rw} почти всё так, кроме: "
                            + " ".join(_field_lines(st, s, errs, R)) + " Остальное верно.")
                    qs = [_q_path(st, s, p, R) for p in errs]
                    said = {p: _get(s, p) for p in errs}
                else:
                    text = f"{addr}, по записи {st.rw} всё так, как ты пишешь, можно делать."
                    qs = [(f"Подтверждает ли автор описание записи {st.rw} целиком?", "да", YES_NO)]
                    said = _all_paths(s)
                text += f" {why}" if why else ""
            elif kind == "parse":
                s = states[t]
                tmp = copy.deepcopy(s)
                _apply(tmp, ev[2])
                RR = rng(f"{TASK_ID}:parse:{label}:{i}")
                err_fids = list(_by_field(ev[2]))
                vals = _gen_values(RR, st, s, pnull=0.1)
                while any(f not in vals or vals[f] == s["f"][f]["null"] for f in err_fids):
                    vals = _gen_values(RR, st, s, pnull=0.1)
                pending[("parse", t)] = (ev[2], vals)
                parts, body = _example_parts(st, s, tmp, vals)
                text = (R.choice(("Разбираю запись", "Пробую руками разобрать запись", "Сверяю свой разбор записи"))
                        + f" {st.rw} из журнала пилота: после приращения времени лежат байты `{body.hex(' ').upper()}`. "
                        "У меня выходит "
                        "так: " + "; ".join(parts) + ". Сходится?")
                qs = _value_questions(st, s, tmp, {f: vals[f] for f in err_fids}, R, "у автора")[:1]
                qs.append(("Уверен ли автор в своём разборе?", "нет", YES_NO))
            elif kind == "parseok":
                errs, vals = pending.pop(("parse", t))
                s = states[t]
                meta = _meta(st)
                lines = []
                for fid, attrs in _by_field(errs).items():
                    raws = vals[fid] if meta[fid].group else [vals[fid]]
                    value = ", ".join(_vtext(meta[fid], s["f"][fid], _sval(s["f"][fid], x)) for x in raws)
                    lines.append(f"Поле «{meta[fid].what}» — " + ", ".join(_attr_text(meta[fid], s["f"][fid], a, R)
                                                                            for a in attrs) + f", так что там {value}.")
                text = f"{addr}, не совсем. " + " ".join(lines) + " Остальное разобрано верно."
                qs = [_q_path(st, s, p, R) for p in errs][:2]
                qs += _value_questions(st, s, s, {f: vals[f] for f in _by_field(errs)}, R, "по этому письму")[:1]
                said = {p: _get(s, p) for p in errs}
            else:  # note: hand-written paragraph, optionally a firmware decision
                text, qs = ev[2], list(ev[3])
                if len(ev) > 4 and ev[4]:
                    _apply(states[t], ev[4])
                    said = {p: _get(states[t], p) for p in ev[4]}
            key = f"{_PREFIX[sender]}_s_{label}_{i}"
            blocks[key] = (text, tuple(qs))
            if fw and said:
                stated[key] = (t, said)
            keys.append(key)
        threads.append((label, date, sender, subject, parent, tuple(keys), size))
    assert not pending, pending
    replay = _run()
    for st in SENSORS:
        assert _same_state(states[st.key], _final_state(st)), st.key
        assert _same_state(replay[st.key], states[st.key]), st.key
        fin = _final_state(st)
        assert all(_meta(st)[f].opt == "" for f in fin["order"][:-1]), st.key
    superseded = {k for k, (t, said) in stated.items()
                  if any(_get(_final_state(SENSOR_BY_KEY[t]), p) != v for p, v in said.items())}
    return {"blocks": blocks, "threads": tuple(threads), "superseded": frozenset(superseded)}


def _fin(t: str, fid: str, attr: str = "enum"):
    return getattr(_meta(SENSOR_BY_KEY[t])[fid], attr)


# The story: (label, date, sender, subject | None for a reply, parent label, size, events).  Events:
#   ("prop", type, overrides)            firmware proposal: the final layout with `overrides` applied
#   ("fix"|"revert"|"yes", type, changes, why)   firmware decision (yes = confirms the parent's guess)
#   ("no", type, paths, why)             firmware rejects a guess, restating the current value
#   ("ask"|"claim", type, guesses, lead[, questions])   question / claim of a non-firmware person
#   ("drop", type, why)   ("example", type)   ("sum", type, errors) + ("sumok", type[, why])
#   ("parse", type, errors) + ("parseok", type)   ("note", type, text, questions[, changes])
MSGS3 = (
    ("s01", "2024-04-01", "irina", "Пакет датчиков: кто за что", None, "long", (
        ("note", None, "С апреля в команде прошивки Артём Зуев: он пришёл делать пакет датчиков — новые записи журнала "
         "для CAN-шины, тахографов, прицепов, рефрижераторов и прочей навесной электроники. По устройству этих записей "
         "его слово так же окончательно, как слово Олега, Сергея и Дины. Ещё к переписке подключаем Веру Ким из "
         "«ТрансЛогистик»: она интегратор со стороны клиента и будет присылать наблюдения с их машин, но её письма "
         "описанием формата не являются.",
         (("В какую команду вошёл Артём?", "прошивка", ("прошивка", "бэкенд", "поддержка")),
          ("Являются ли по письму письма Веры описанием формата?", "нет", YES_NO))),)),
    ("s02", "2024-04-02", "oleg", None, "s01", "mid", (
        ("note", None, "Пара общих правил про пакет датчиков, чтобы потом не спорить. Все его записи появляются только "
         "в журнале третьей версии, начиная с прошивки 3.3; номера типов у них — от 0x20 до 0x5F. В журналах первой и "
         "второй версии записи с такими номерами тоже попадаются, но это остатки отладки: разбирать их нечего, как "
         "девятый тип. Имена записей и полей для выгрузки ведёт Дина в реестре, и в выгрузке действует последнее "
         "утверждённое имя. Пока обсуждаем, в письмах будут и планы, и возражения; окончательно то, что сказал "
         "кто-то из нас четверых последним по времени.",
         (("В журналах какой версии по письму бывают записи пакета датчиков?", "3", None),
          ("Как по письму выводить записи с номерами пакета в журналах второй версии?", "как неизвестные",
           ("как неизвестные", "как записи пакета датчиков")))),)),
    # --- CAN bus ---------------------------------------------------------------------------------------------
    ("a03", "2024-04-15", "oleg", "Пакет датчиков, часть 1: CAN-шина", None, "long", (
        ("note", None, "Начинаем пакет датчиков с того, что можно взять с CAN-шины грузовиков без дополнительного "
         "железа. Ниже шесть записей; всё это пока план к 3.3, возражения присылайте до конца месяца.", ()),
        ("prop", "can_fuel", {"total_l.key": "fuel_total", "rate_lph.mul": 0.1}),
        ("prop", "can_hours", {"@order": ["engine_h", "idle_h", "service_km", "starts"]}),
        ("prop", "dash_lamps", {"@name": "can_lamps", "lamps.flags": _LAMPS_PROP, "blinking.flags": _LAMPS_PROP}),
        ("prop", "adblue", {"level_pct.mul": 1}),
        ("prop", "can_gear", {}),
        ("prop", "pto", {"@tid": 0x26, "shaft_rpm.mul": 1}))),
    ("a04", "2024-04-16", "marina", None, "a03", "short", (
        ("ask", "can_fuel", {"total_l.mul": 1}, "Олег, пара вопросов, пока не начала разбор на бэкенде."),
        ("ask", "can_hours", {"engine_h.mul": 1}, "И про моточасы."))),
    ("a05", "2024-04-17", "sergey", None, "a04", "mid", (
        ("fix", "can_fuel", {"rate_lph.mul": 0.05}, "Блок двигателя отдаёт мгновенный расход с разрешением 0,05 л/ч, и "
         "мы кладём его без пересчёта, так что десятые доли в плане Олега — ошибка."),
        ("no", "can_fuel", ["total_l.mul"], "Полулитры — так считает сам блок управления, мы не делим и не умножаем."),
        ("no", "can_hours", ["engine_h.mul"], "Счётчик у блока идёт шагами по три минуты, в целые часы его никто не "
         "округляет."))),
    ("a06", "2024-04-22", "pavel", "CAN: лампы на щитке", None, "mid", (
        ("ask", "dash_lamps", {"lamps.flags": _LAMPS, "blinking.flags": _LAMPS},
         "Смотрю выгрузки с пилотных машин на сборке 3.3-beta. В записи ламп, когда в кодах неисправностей висит "
         "ошибка ABS, у меня горит четвёртый бит снизу, а когда водитель жалуется на тормоза — третий. По плану Олега "
         "должно быть наоборот. Может, биты переставлены и на самом деле так:"),)),
    ("a07", "2024-04-23", "artem", None, "a06", "short", (
        ("yes", "dash_lamps", {"lamps.flags": _LAMPS, "blinking.flags": _LAMPS}, "В плане два бита переставлены: "
         "щиток отдаёт тормозную систему раньше ABS, а мы ничего не перекладываем. Это касается и горящих, и мигающих "
         "ламп."),)),
    ("a08", "2024-05-06", "artem", "Моточасы, AdBlue и тормоза", None, "long", (
        ("fix", "can_hours", {"@order": ["idle_h", "engine_h", "service_km", "starts"]}, "Блок отдаёт холостой ход первым в "
         "том же сообщении, а мы копируем сообщение целиком, не переставляя."),
        ("fix", "can_hours", {"pto_h.+": "starts"}, "Если у машины есть коробка отбора мощности, блок отдаёт и её "
         "счётчик, и мы дописываем его в хвост."),
        ("fix", "adblue", {"level_pct.mul": 0.4}, "Датчик отдаёт уровень шагами по 0,4 %, полный бак — 250; в плане "
         "было «сразу проценты», это не так."),
        ("prop", "brakes", {"retarder_pct.fmt": "u8", "retarder_pct.null": 0xFF}))),
    ("a09", "2024-05-07", "marina", None, "a08", "mid", (
        ("sum", "adblue", {"temp_c.add": 0}),
        ("sum", "can_hours", {}))),
    ("a10", "2024-05-08", "oleg", None, "a09", "short", (
        ("sumok", "adblue", "Температуру блок отдаёт со сдвигом, как охлаждающую жидкость, мы её не пересчитываем."),
        ("sumok", "can_hours"))),
    ("a11", "2024-05-20", "oleg", "Передача и ВОМ", None, "mid", (
        ("drop", "can_gear", "На половине парка сигнала передачи на шине нет вовсе, а где есть, у каждого "
         "производителя он свой, и ретардер тоже."),
        ("note", "pto", "С коробкой отбора мощности к 3.3 не успеваем: блоки у производителей надстроек слишком "
         "разные. Запись откладываем, номер 0x26 (38) под неё больше не держим. Пилотные сборки 3.3-beta её писали — "
         "разбирать такие записи не нужно, это неописанные данные.",
         (("Войдёт ли по письму запись коробки отбора мощности в 3.3?", "нет", YES_NO),), {"@dropped": True}))),
    ("a12", "2024-05-27", "dina", "Реестр имён: CAN", None, "mid", (
        ("fix", "can_fuel", {"total_l.key": "total_l"}, "В реестре все объёмы оканчиваются на _l, общий счётчик "
         "приводим к тому же виду."),
        ("fix", "dash_lamps", {"@name": "dash_lamps"}, "Префикс can_ в реестре оставляем записям, которые копируют "
         "сигнал шины без обработки, а набор ламп щиток собирает сам."))),
    ("a13", "2024-06-03", "marina", "CAN для бэкенда", None, "long", (
        ("sum", "can_fuel", {"level_pct.mul": 1}),
        ("sum", "dash_lamps", {"level.enum": ("warning:предупреждение", "stop:немедленная остановка",
                                              "info:для сведения")}),
        ("sum", "brakes", {}))),
    ("a14", "2024-06-04", "artem", None, "a13", "mid", (
        ("sumok", "can_fuel", "Уровень щиток отдаёт так же, как датчик мочевины, шагами по 0,4 %."),
        ("sumok", "dash_lamps", "Коды степени тревоги идут по нарастанию: сведения, предупреждение, остановка."),
        ("sumok", "brakes"))),
    ("a15", "2024-06-17", "timur", "3.3 на пилоте", None, "long", (
        ("ask", "brakes", {}, "На пилоте в Перми после 3.3 в записи тормозов во втором байте после приращения времени "
         "бывают значения за двести — 230, 245, — причём ровно тогда, когда водитель тормозит ретардером на спуске. "
         "Если это проценты, то какие-то странные. Может, трекер что-то путает?",
         (("Какие значения этого байта видит автор?", "за двести", ("за двести", "до ста", "отрицательные")),)),)),
    ("a16", "2024-06-18", "artem", None, "a15", "mid", (
        ("fix", "brakes", {"retarder_pct.fmt": "i8", "retarder_pct.null": -128}, "Ретардер отдаёт момент со знаком, "
         "и 230 — это на самом деле −26 %, то есть торможение; в моём майском письме байт описан как беззнаковый, это "
         "ошибка, в 3.3 он ушёл со знаком."),
        ("example", "brakes"),
        ("fix", "adblue", {"quality.+": None}, "На новых двигателях блок отдаёт ещё и качество раствора, с 3.3.1 "
         "пишем и его."))),
    ("a17", "2024-07-08", "pavel", "Разбор записей CAN", None, "mid", (
        ("parse", "can_hours", {"engine_h.mul": 0.1}),
        ("parse", "adblue", {"dose_lph.mul": 0.1}))),
    ("a18", "2024-07-09", "sergey", None, "a17", "short", (
        ("parseok", "can_hours"),
        ("parseok", "adblue"))),
    ("a19", "2024-07-22", "artem", "Примеры: топливо, моточасы, лампы", None, "mid", (
        ("example", "can_fuel"), ("example", "can_hours"), ("example", "dash_lamps"), ("example", "adblue"))),
    # --- tachograph ------------------------------------------------------------------------------------------
    ("b01", "2024-04-29", "dina", "Пакет датчиков, часть 2: тахограф", None, "long", (
        ("note", None, "Вторая часть пакета — тахограф. Трекер читает его по отдельной линии, как бортовой компьютер; "
         "в журнал идут четыре записи. План к 3.3.", ()),
        ("prop", "tacho_activity", {"activity.null": 0xFF}),
        ("prop", "tacho_times", {"drive_left_min.key": "left_min"}),
        ("prop", "tacho_speed", {"kmh.fmt": "u16", "distance_km.fmt": "u32", "distance_km.key": "dist_km",
                                 "rpm.fmt": "u16"}),
        ("prop", "tacho_card", {}))),
    ("b02", "2024-04-30", "pavel", None, "b01", "short", (
        ("ask", "tacho_times", {}, "Дина, счётчики времени — в секундах? Тогда недельное время в два байта не "
         "влезет: 56 часов — это двести тысяч секунд с лишним.",
         (("В каких единицах автор предполагает счётчики?", "секунды", ("секунды", "минуты", "часы")),)),
        ("ask", "tacho_speed", {"kmh.fmt": "u16"}, "И скорость по тахографу — как всё остальное в журнале?"))),
    ("b03", "2024-05-02", "sergey", None, "b02", "mid", (
        ("note", "tacho_times", "Павел, все счётчики времени в записи тахографа — в минутах, как их отдаёт сам "
         "тахограф; секунд там нет.",
         (("В каких единицах по письму счётчики времени тахографа?", "минуты", ("секунды", "минуты", "часы")),)),
        ("fix", "tacho_speed", {"kmh.fmt": "U16", "distance_km.fmt": "U32", "rpm.fmt": "U16"}, "Интерфейс тахографа отдаёт числа "
         "старшим байтом вперёд, и мы их не переворачиваем; Дина описала по черновику, где было иначе."))),
    ("b04", "2024-05-13", "vera", "Тахограф у «ТрансЛогистик»", None, "long", (
        ("claim", "tacho_activity", {"activity.enum": ("drive:вождение", "work:работа", "available:готовность",
                                                       "rest:отдых")},
         "Мы подключили разбор журналов к нашей системе учёта рабочего времени. У тахографов, которые стоят у нас, "
         "режимы кодируются по-своему, и в журнале, судя по всему, так же."),)),
    ("b05", "2024-05-14", "dina", None, "b04", "mid", (
        ("no", "tacho_activity", ["activity.enum"], "Прошивка перекладывает коды любого тахографа в свои, так что от "
         "модели тахографа журнал не зависит."),
        ("fix", "tacho_activity", {"activity.null": 7}, "Когда тахограф не знает режим, он отдаёт семёрку, и мы кладём "
         "её как есть; 0xFF в этом байте не бывает."),
        ("drop", "tacho_card", "Карты водителей и так пишет тринадцатая запись от считывателя на планшете, дублировать "
         "её тахографом не будем."))),
    ("b06", "2024-06-17", "dina", "Реестр имён: тахограф", None, "mid", (
        ("fix", "tacho_times", {"drive_left_min.key": "drive_left_min"}, "Из `left_min` непонятно, остаток чего; в "
         "реестре пишем полностью."),
        ("fix", "tacho_speed", {"distance_km.key": "distance_km"}, "Сокращений в именах реестра не держим."),
        ("fix", "tacho_times", {"break_min.+": None}, "Тахографы нового поколения отдают и накопленный перерыв, с 3.3 "
         "он дописывается в конец записи."),
        ("example", "tacho_times"))),
    ("b07", "2024-07-01", "marina", "Тахограф для бэкенда", None, "long", (
        ("sum", "tacho_activity", {"crew.key": "team"}),
        ("sum", "tacho_times", {}),
        ("sum", "tacho_speed", {"kmh.mul": 0.01}))),
    ("b08", "2024-07-02", "dina", None, "b07", "mid", (
        ("sumok", "tacho_activity"),
        ("sumok", "tacho_times"),
        ("sumok", "tacho_speed", "Скорость тахограф считает в 1/256 км/ч, это его родная единица."))),
    ("b09", "2024-09-09", "sergey", "Тахографы второго поколения", None, "long", (
        ("fix", "tacho_speed", {"distance_km.mul": 0.01}, "Тахографы второго поколения отдают пробег шагами по 10 м, "
         "и с 3.4 мы пишем его так для всех машин."),)),
    ("b10", "2024-11-05", "dina", "Примеры: тахограф", None, "mid", (
        ("example", "tacho_speed"), ("example", "tacho_activity"))),
    # --- trailer ---------------------------------------------------------------------------------------------
    ("c01", "2024-07-01", "sergey", "Пакет датчиков, часть 3: прицеп", None, "long", (
        ("note", None, "Третья часть пакета — прицеп. Всё, что ниже, трекер получает от блоков прицепа по разъёму "
         "ABS/EBS; план к 3.4.", ()),
        ("prop", "trailer", {"trailer_id.fmt": "u32", "event.enum": ("coupled:сцепили", "uncoupled:расцепили")}),
        ("prop", "trailer_ebs", {"brake_c.fmt": "u8", "brake_c.mul": 1, "brake_c.add": -40, "brake_c.null": 0xFF}),
        ("prop", "trailer_lights", {}),
        ("prop", "trailer_power", {"amps.mul": 0.01, "volts.key": "voltage"}))),
    ("c02", "2024-07-02", "marina", None, "c01", "mid", (
        ("sum", "trailer_ebs", {"pad_wear_pct.mul": 1}),)),
    ("c03", "2024-07-03", "sergey", None, "c02", "mid", (
        ("sumok", "trailer_ebs", "Износ блок отдаёт так же, как все проценты на шине, шагами по 0,4 %."),
        ("fix", "trailer_ebs", {"brake_c.fmt": "u16", "brake_c.mul": 0.03125, "brake_c.add": -273,
                                "brake_c.null": 0xFFFF},
         "Пока отвечал Марине, проверил на стенде: блок EBS отдаёт температуру тормозов по стандарту шины, и мы "
         "кладём её как есть. Байт со сдвигом 40 был в плане, но триста градусов тормозов в него не влезают."))),
    ("c04", "2024-07-15", "timur", "Прицепы на пилоте", None, "long", (
        ("ask", "trailer", {}, "На пилоте с прицепами Schmitz у части сцепок идентификатор прицепа не сходится с "
         "табличкой, а у новых прицепов после идентификатора байты как будто съезжают: число осей получается то 128, "
         "то 147. И ещё: одна машина цепляла чужой прицеп, а в журнале было обычное «сцепили». Что-то не так с "
         "записью?",
         (("Сходится ли у автора идентификатор прицепа с табличкой?", "нет", YES_NO),)),)),
    ("c05", "2024-07-16", "artem", None, "c04", "mid", (
        ("fix", "trailer", {"trailer_id.fmt": "uv"}, "Новые блоки EBS отдают идентификатор длиннее четырёх байт, "
         "поэтому в 3.4 он пишется varint-ом; четыре байта были только в плане."),
        ("fix", "trailer", {"event.enum": _fin("trailer", "event")}, "Чужой прицеп — отдельное событие, мы добавили "
         "его в 3.4, раньше он писался как обычная сцепка."),
        ("example", "trailer"))),
    ("c06", "2024-08-05", "sergey", "Прицеп: что меняется к 3.4", None, "mid", (
        ("drop", "trailer_lights", "Модуль контроля ламп прицепа почти никто не ставит, на пилоте он был у одного "
         "клиента."),
        ("fix", "trailer_power", {"amps.mul": 0.1}, "Шунт у нас на 150 А, в сотых долях такой ток в два байта со "
         "знаком не помещается."),
        ("example", "trailer_ebs"))),
    ("c07", "2024-08-06", "pavel", None, "c06", "short", (
        ("ask", "trailer_power", {"volts.mul": 0.01}, "Сергей, раз уж ток поменялся, то напряжение на разъёме тоже "
         "по-новому?"),)),
    ("c08", "2024-09-24", "marina", "Прицеп для бэкенда", None, "long", (
        ("sum", "trailer", {"axles.null": None}),
        ("sum", "trailer_power", {"socket.enum": ("iso12098:ISO 12098", "iso7638:ISO 7638", "adapter:переходник")}))),
    ("c09", "2024-09-25", "artem", None, "c08", "mid", (
        ("sumok", "trailer", "Ноль в этом байте блок пишет, когда не знает своих осей."),
        ("sumok", "trailer_power"),
        ("example", "trailer_power"))),
    # --- reefer ----------------------------------------------------------------------------------------------
    ("d01", "2024-07-22", "dina", "Пакет датчиков, часть 4: рефрижератор", None, "long", (
        ("note", None, "Четвёртая часть — рефрижераторы. Установки Carrier и Thermo King отдают данные по своему "
         "последовательному порту, трекер их читает и пишет четыре записи. План к 3.4.", ()),
        ("prop", "reefer_zone", {"setpoint_c.key": "set_c", "setpoint_c.null": -32768, "return_c.null": -32768,
                                 "supply_c.null": -32768, "coil_c.null": -32768}),
        ("prop", "reefer_mode", {"mode.enum": ("cool:охлаждение", "heat:нагрев", "defrost:оттайка")}),
        ("prop", "reefer_unit", {"run_h.mul": 1, "battery_v.mul": 0.1, "ambient_c.fmt": "u8", "ambient_c.add": -40,
                                 "ambient_c.null": 0xFF}),
        ("prop", "reefer_doors", {}))),
    ("d02", "2024-07-23", "pavel", None, "d01", "short", (
        ("ask", "reefer_zone", {"setpoint_c.mul": 1}, "Дина, уставку же водитель задаёт целыми градусами, на пульте "
         "нет десятых."),)),
    ("d03", "2024-07-24", "dina", None, "d02", "mid", (
        ("no", "reefer_zone", ["setpoint_c.mul"], "Пульт показывает целые, но установка отдаёт уставку точнее, и мы "
         "кладём как есть."),
        ("fix", "reefer_zone", {"setpoint_c.null": 0x7FFF, "return_c.null": 0x7FFF, "supply_c.null": 0x7FFF,
                                "coil_c.null": 0x7FFF},
         "Посмотрела протоколы обеих установок: когда датчик отвалился, они отдают 0x7FFF, а не то, что я писала, и "
         "мы не перекодируем."))),
    ("d04", "2024-08-12", "sergey", "Реф: питание и наработка", None, "mid", (
        ("fix", "reefer_unit", {"battery_v.mul": 0.2}, "Установки бывают и на 24 В, а в десятых долях байт кончается "
         "на 25,5 В."),
        ("fix", "reefer_unit", {"run_h.mul": 0.1}, "Сервису целых часов мало: установка отдаёт наработку в десятых "
         "долях часа, так и пишем."),
        ("fix", "reefer_unit", {"ambient_c.fmt": "i8", "ambient_c.add": 0, "ambient_c.null": -128}, "Наружный датчик "
         "у установок свой, и отдаёт он градусы со знаком, без сдвига; мы кладём как есть."),
        ("drop", "reefer_doors", "Двери фургона уже идут одиннадцатой записью, а отдельных датчиков на дверях отсеков "
         "почти ни у кого нет."))),
    ("d05", "2024-08-26", "marina", "Режимы рефа для бэкенда", None, "mid", (
        ("sum", "reefer_mode", {"alarm.fmt": "u8"}),
        ("sum", "reefer_zone", {"coil_c.null": None}),
        ("sum", "reefer_unit", {}))),
    ("d06", "2024-08-27", "dina", None, "d05", "mid", (
        ("sumok", "reefer_mode", "Кодов тревоги у Thermo King больше двухсот, в байт они не помещаются."),
        ("sumok", "reefer_zone", "Испаритель, если датчик отвалился, отдаёт то же особое значение, что и воздух."),
        ("sumok", "reefer_unit"),
        ("fix", "reefer_mode", {"mode.enum": _fin("reefer_mode", "mode")}, "Заодно про режимы: в 3.4 их считаем с "
         "нуля — ноль теперь «установка выключена», остальные сдвигаются на единицу, и в конце добавилось ожидание."))),
    ("d07", "2024-09-02", "dina", "Реестр имён: рефрижератор", None, "mid", (
        ("fix", "reefer_zone", {"setpoint_c.key": "setpoint_c"}, "Сокращение `set` путают с глаголом, в реестре "
         "пишем полностью."),
        ("example", "reefer_zone"),
        ("example", "reefer_unit"))),
    ("d08", "2024-09-23", "vera", "Рефы у клиентов", None, "long", (
        ("claim", "reefer_unit", {"fuel_pct.mul": 0.5}, "Сверили журналы с показаниями на пультах установок у "
         "«Фрешмаркета», и вот что получается."),)),
    ("d09", "2024-11-18", "pavel", "Разбор записи рефа", None, "mid", (
        ("parse", "reefer_zone", {"return_c.mul": 0.01}),)),
    ("d10", "2024-11-19", "sergey", None, "d09", "short", (
        ("parseok", "reefer_zone"),
        ("example", "reefer_mode"))),
    # --- loads and body --------------------------------------------------------------------------------------
    ("e01", "2024-08-19", "artem", "Пакет датчиков, часть 5: нагрузки и кузов", None, "long", (
        ("note", None, "Пятая часть — нагрузки и кузов: весовые модули, акселерометр, датчики на надстройках. План к "
         "3.4.", ()),
        ("prop", "axle_loads", {"loads_kg.mul": 20}),
        ("prop", "body_tilt", {"body_up.key": "tipper"}),
        ("prop", "weight", {"payload_kg.fmt": "u16", "payload_kg.null": 0xFFFF}),
        ("prop", "mixer_drum", {"rpm.fmt": "u16"}))),
    ("e02", "2024-08-20", "vera", None, "e01", "short", (
        ("note", "axle_loads", "У нас весовой модуль считает оси с задней, так что в списке нагрузок первой будет "
         "задняя ось — учтите в разборе.",
         (("С какой оси по словам автора начинается список нагрузок?", "с задней", ("с передней", "с задней")),)),)),
    ("e03", "2024-08-21", "artem", None, "e02", "short", (
        ("note", "axle_loads", "Вера, в журнале оси всегда идут от передней к задней: трекер переставляет их сам, как "
         "бы их ни считал весовой модуль.",
         (("С какой оси по письму начинается список нагрузок в журнале?", "с передней", ("с передней", "с задней")),)),
        ("fix", "axle_loads", {"loads_kg.mul": 10}, "И поправка к моему плану: датчики новой партии дают нагрузку "
         "точнее, так и пишем."))),
    ("e04", "2024-09-16", "marina", "Вес и наклон для бэкенда", None, "long", (
        ("sum", "body_tilt", {"pitch_deg.mul": 0.01, "roll_deg.mul": 0.01}),
        ("sum", "weight", {"method.enum": ("load_cells:тензодатчики", "suspension:пневмоподвеска", "ebs:блок EBS")}),
        ("sum", "axle_loads", {}),
        ("sum", "mixer_drum", {}))),
    ("e05", "2024-09-17", "sergey", None, "e04", "mid", (
        ("sumok", "body_tilt", "Акселерометр отдаёт углы с точностью до десятой градуса, не лучше."),
        ("sumok", "weight"),
        ("sumok", "axle_loads"),
        ("sumok", "mixer_drum"),
        ("fix", "weight", {"payload_kg.fmt": "i16", "payload_kg.null": 0x7FFF}, "И к весу: масса груза после "
         "тарирования бывает отрицательной, так что в 3.4 поле со знаком."))),
    ("e06", "2024-09-30", "artem", "Бетоносмесители и самосвалы", None, "mid", (
        ("fix", "mixer_drum", {"direction.-": True, "rpm.fmt": "i16"}, "Направление теперь в знаке оборотов: минус — "
         "выгрузка, отдельный байт выкинули."),
        ("fix", "body_tilt", {"body_up.key": "body_up"}, "`tipper` в реестре не прижился: поднятый кузов бывает не "
         "только у самосвалов."),
        ("fix", "weight", {"gross_kg.mul": 10}, "Полную массу с 3.4 пишем так же подробно, как нагрузки на оси."))),
    ("e07", "2024-10-07", "artem", "Примеры: нагрузки и кузов", None, "mid", (
        ("example", "axle_loads"), ("example", "mixer_drum"), ("example", "body_tilt"))),
    ("r34", "2024-10-21", "dina", "Что ушло в 3.4", None, "long", (
        ("note", None, "Коротко, что из пакета датчиков ушло в 3.4 и чем выпуск отличается от писем.", ()),
        ("revert", "tacho_speed", {"distance_km.mul": 0.005}, "Прошивка сама пересчитывает пробег тахографов второго "
         "поколения в прежние шаги, так что во всех журналах шаг один; сентябрьское письмо Сергея в этой части "
         "устарело, и строка про 10 м в релизных заметках — тоже."),
        ("revert", "weight", {"gross_kg.mul": 20}, "Весовые модули второй партии не умеют шаг 10 кг, поэтому полная "
         "масса в выпуске пишется так, как в первом описании Артёма."),
        ("fix", "trailer_power", {"volts.key": "volts"}, "Напряжения в реестре называем по единице, как ток."))),
    ("e08", "2024-11-04", "pavel", "Разбор записи веса", None, "short", (
        ("parse", "weight", {"gross_kg.mul": 10}),)),
    ("e09", "2024-11-05", "dina", None, "e08", "short", (
        ("parseok", "weight"),
        ("example", "weight"))),
    # --- cabin ----------------------------------------------------------------------------------------------
    ("f01", "2024-11-11", "dina", "Пакет датчиков, часть 6: кабина", None, "long", (
        ("note", None, "Шестая часть — всё, что в кабине: камеры, ремни, воздух, алкозамок. План к 3.5.", ()),
        ("prop", "adas", {"kind.enum": ("fcw:угроза столкновения", "ldw:съезд с полосы", "pcw:пешеход",
                                        "hmw:опасная дистанция")}),
        ("prop", "fatigue", {"confidence_pct.key": "conf"}),
        ("prop", "seatbelts", {}),
        ("prop", "cabin_air", {}),
        ("prop", "alcolock", {"bac_permille.mul": 0.01}))),
    ("f02", "2024-11-12", "timur", None, "f01", "short", (
        ("ask", "fatigue", {"duration_ms.mul": 1}, "Дина, клиенты спрашивают про камеры усталости."),
        ("ask", "adas", {"ttc_s.mul": 0.01}, "И по ассистенту водителя."))),
    ("f03", "2024-11-13", "dina", None, "f02", "mid", (
        ("no", "fatigue", ["duration_ms.mul"], "Как и у резкого вождения, длительность — в сотых долях секунды."),
        ("no", "adas", ["ttc_s.mul"], "Больше двадцати пяти секунд до столкновения никому не интересно, точнее камера "
         "всё равно не меряет."),
        ("fix", "fatigue", {"confidence_pct.key": "confidence_pct"}, "`conf` в реестре путают с конфигурацией."))),
    ("f04", "2024-11-25", "oleg", "Камеры ADAS второго поколения", None, "mid", (
        ("fix", "adas", {"kind.enum": _fin("adas", "kind")}, "Камеры второго поколения различают, в какую сторону "
         "машина уходит с полосы, и в 3.5 это два разных кода, поэтому пешеход и дистанция сдвигаются."),)),
    ("f05", "2024-12-09", "artem", "Ремни и воздух в кабине", None, "mid", (
        ("fix", "seatbelts", {"occupied.+": "fastened"}, "Непристёгнутый ремень на пустом сиденье ничего не значит, "
         "поэтому датчики занятости пишем в ту же запись."),
        ("drop", "cabin_air", "Датчик углекислого газа сняли с заказа, а температуру в кабине никто из клиентов не "
         "просил."))),
    ("f06", "2025-01-20", "marina", "Кабина для бэкенда", None, "long", (
        ("sum", "alcolock", {"result.enum": ("fail:превышение", "pass:норма", "refused:отказ от теста",
                                             "error:ошибка прибора")}),
        ("sum", "seatbelts", {}),
        ("sum", "adas", {"distance_m.mul": 1}),
        ("sum", "fatigue", {}))),
    ("f07", "2025-01-21", "dina", None, "f06", "mid", (
        ("sumok", "alcolock"),
        ("sumok", "seatbelts"),
        ("sumok", "adas", "Дистанцию камера отдаёт в полуметрах."),
        ("sumok", "fatigue"),
        ("fix", "alcolock", {"bac_permille.mul": 0.001}, "И поправка к моему ноябрьскому письму: алкозамки, которые мы "
         "в итоге берём, меряют концентрацию точнее."))),
    ("f08", "2025-02-10", "artem", "Примеры: кабина", None, "mid", (
        ("example", "adas"), ("example", "fatigue"), ("example", "alcolock"), ("example", "seatbelts"))),
    # --- cargo and security ----------------------------------------------------------------------------------
    ("g01", "2025-01-13", "oleg", "Пакет датчиков, часть 7: груз и охрана", None, "long", (
        ("note", None, "Последняя часть пакета к 3.5 — груз и охрана. Заодно возвращаем запись коробки отбора "
         "мощности, отложенную весной.", ()),
        ("prop", "cargo_temps", {"@name": "cargo_probes", "temps_c.mul": 0.1}),
        ("prop", "humidity", {"rh_pct.mul": 0.1, "pressure_hpa.mul": 1}),
        ("prop", "seal", {"event.enum": ("opened:вскрыта", "closed:закрыта", "tamper:попытка взлома"),
                          "openings.fmt": "u8"}),
        ("prop", "fuel_drain", {"@name": "fuel_theft", "liters.mul": 1}),
        ("prop", "panic", {}),
        ("fix", "pto", {"@dropped": False, "@tid": 0x55, "shaft_rpm.mul": 0.125}, "Старый номер не возвращаем: "
         "пилотные сборки 3.3-beta успели написать там записей по весеннему плану. Остальное — как в весеннем "
         "письме."))),
    ("g02", "2025-01-14", "pavel", None, "g01", "short", (
        ("ask", "humidity", {"dew_c.fmt": "u16"}, "Олег, точка росы в кузове ниже нуля бывает только зимой, может, её "
         "проще писать без знака?"),
        ("ask", "fuel_drain", {"liters.mul": 0.1}, "И по сливам: датчик уровня у нас везде в десятых литра, может, и "
         "слив так же?"))),
    ("g03", "2025-01-15", "sergey", None, "g02", "mid", (
        ("no", "humidity", ["dew_c.fmt"], "Минус у точки росы — обычное дело и летом, в рефрижераторе."),
        ("yes", "fuel_drain", {"liters.mul": 0.1}, "Слив считаем по тому же датчику уровня, так что и объём в тех же "
         "долях; в плане Олега целые литры — ошибка."),
        ("fix", "cargo_temps", {"temps_c.mul": 0.01}, "Щупы, которые берём, меряют точнее, и для фармацевтики это "
         "важно."),
        ("fix", "humidity", {"rh_pct.mul": 0.01, "pressure_hpa.mul": 0.1}, "Датчик влажности отдаёт оба значения "
         "точнее, так и кладём."))),
    ("g04", "2025-02-03", "dina", "Пломбы и тревожная кнопка", None, "mid", (
        ("fix", "seal", {"event.enum": _fin("seal", "event")}, "Пломбы, которые прошли испытания, считают «закрыта» "
         "нулём, а «вскрыта» единицей и умеют предупреждать о севшей батарее."),
        ("fix", "seal", {"openings.fmt": "u16"}, "Счётчик вскрытий у них не сбрасывается и за год переваливает за "
         "двести пятьдесят."),
        ("drop", "panic", "Тревожную кнопку заводим на вход сигнализации: она и так видна по старшему биту записи "
         "дверей."))),
    ("g05", "2025-02-17", "dina", "Реестр имён: груз и охрана", None, "mid", (
        ("fix", "cargo_temps", {"@name": "cargo_temps"}, "В реестре имя записи говорит, что в ней, а не чем мерили."),
        ("fix", "fuel_drain", {"@name": "fuel_drain"}, "Клиенты обижаются: слив бывает законным, например на сервисе."),
        ("example", "seal"),
        ("example", "humidity"))),
    ("g06", "2025-02-24", "marina", "Груз и ВОМ для бэкенда", None, "long", (
        ("sum", "pto", {"engaged_s.fmt": "u32"}),
        ("sum", "cargo_temps", {"temps_c.null": -32768}),
        ("sum", "fuel_drain", {}),
        ("sum", "humidity", {}),
        ("sum", "seal", {"battery_pct.null": None}))),
    ("g07", "2025-02-25", "oleg", None, "g06", "mid", (
        ("sumok", "pto", "Время с включения растёт без ограничений, поэтому varint."),
        ("sumok", "cargo_temps"),
        ("sumok", "fuel_drain"),
        ("sumok", "humidity"),
        ("sumok", "seal", "Когда пломба не сообщила заряд, трекер пишет особое значение, а не ноль."))),
    ("g08", "2025-03-24", "dina", "Что ушло в 3.5", None, "mid", (
        ("note", None, "Пакет датчиков в 3.5 закончен; кроме того, что обсуждали в письмах, изменений нет. Для сверки — "
         "по примеру на запись.", ()),
        ("example", "cargo_temps", 3), ("example", "pto", 3), ("example", "fuel_drain", 3), ("example", "adas"),
        ("example", "fatigue"), ("example", "seatbelts"), ("example", "alcolock"), ("example", "humidity"),
        ("example", "seal"))),
    ("g09", "2025-04-07", "artem", "Про релизные заметки 3.5", None, "mid", (
        ("note", "seal", "В заметках к 3.5 написано, что заряд батареи пломбы пишется в десятых долях процента. Это "
         "ошибка при сборке заметок: заряд — целые проценты, от 0 до 100, как и было в письме Олега; 0xFF — нет "
         "данных.",
         (("В каких единицах по письму заряд батареи пломбы?", "целые проценты",
           ("целые проценты", "десятые доли процента")),)),)),
    ("g10", "2025-05-12", "timur", "Пломбы на пилоте", None, "long", (
        ("ask", "seal", {"battery_pct.mul": 0.1}, "На пилоте с пломбами у «ЭкоВывоза» диспетчеры видят заряд 87 и не "
         "понимают, много это или мало. Судя по заметкам к 3.5, получается так:"),)),
    ("g11", "2025-05-13", "artem", None, "g10", "short", (
        ("no", "seal", ["battery_pct.mul"], "87 — это 87 %, пломба почти полная."),)),
    # --- municipal vehicles (3.5.1) --------------------------------------------------------------------------
    ("h01", "2025-04-14", "artem", "Пакет датчиков к 3.5.1: коммунальная техника", None, "long", (
        ("note", None, "К 3.5.1 добавляем две записи для коммунальной техники — их попросили «Мостранс-Сервис» и "
         "«ЭкоВывоз». Номера берём сразу за пакетом 3.5.", ()),
        ("prop", "spreader", {"rate_gm2.mul": 10}),
        ("prop", "bin_lift", {"side.enum": ("rear:сзади", "left:слева", "right:справа"), "full_kg.key": "gross_kg",
                              "empty_kg.key": "tare_kg"}))),
    ("h02", "2025-04-15", "timur", None, "h01", "short", (
        ("ask", "spreader", {"material.enum": ("salt:соль", "sand:песок", "mix:смесь", "brine:рассол")},
         "Артём, на дорожной технике у «Мостранс-Сервиса» пульт разбрасывателя показывает соль первой строкой. Может, "
         "и коды такие же?"),)),
    ("h03", "2025-04-16", "sergey", None, "h02", "mid", (
        ("no", "spreader", ["material.enum"], "Коды реагентов — наши, от пульта они не зависят."),
        ("fix", "spreader", {"rate_gm2.mul": 1}, "И к плану Артёма: контроллер разбрасывателя отдаёт норму в целых "
         "граммах, делить или умножать ничего не надо."),
        ("fix", "bin_lift", {"side.enum": _fin("bin_lift", "side")}, "Подъёмники нумеруют стороны слева направо, а "
         "задний идёт последним."))),
    ("h04", "2025-04-28", "marina", "Коммунальная техника для бэкенда", None, "mid", (
        ("sum", "spreader", {"width_m.mul": 1}),
        ("sum", "bin_lift", {}))),
    ("h05", "2025-04-29", "artem", None, "h04", "mid", (
        ("sumok", "spreader", "Ширину контроллер отдаёт с точностью до десяти сантиметров."),
        ("sumok", "bin_lift"))),
    ("h06", "2025-05-19", "dina", "Реестр имён и контрольные записи к 3.5.1", None, "long", (
        ("fix", "bin_lift", {"full_kg.key": "full_kg", "empty_kg.key": "empty_kg"}, "`gross` и `tare` в реестре уже "
         "заняты массой машины, для контейнеров пишем «полный» и «пустой»."),
        ("example", "spreader", 3),
        ("example", "bin_lift", 3))),
    ("h07", "2025-06-02", "pavel", "vtldump: коммунальная техника", None, "mid", (
        ("parse", "bin_lift", {"full_kg.mul": 1}),
        ("parse", "spreader", {"disc_rpm.mul": 10}))),
    ("h08", "2025-06-03", "artem", None, "h07", "short", (
        ("parseok", "bin_lift"),
        ("parseok", "spreader"))),
    # --- more hand checks by the tools author -----------------------------------------------------------------
    ("q01", "2024-08-26", "pavel", "Разбор записей тахографа", None, "mid", (
        ("parse", "tacho_speed", {"kmh.mul": 0.01}),
        ("parse", "tacho_activity", {"activity.enum": ("drive:вождение", "work:работа", "available:готовность",
                                                       "rest:отдых")}))),
    ("q02", "2024-08-27", "dina", None, "q01", "short", (
        ("parseok", "tacho_speed"),
        ("parseok", "tacho_activity"))),
    ("q03", "2024-10-14", "pavel", "Разбор записей прицепа", None, "mid", (
        ("parse", "trailer_ebs", {"brake_c.add": -40}),
        ("parse", "trailer_power", {"volts.mul": 0.01}))),
    ("q04", "2024-10-15", "sergey", None, "q03", "short", (
        ("parseok", "trailer_ebs"),
        ("parseok", "trailer_power"))),
    ("q05", "2025-02-26", "pavel", "Разбор записей кабины", None, "mid", (
        ("parse", "adas", {"ttc_s.mul": 0.01}),
        ("parse", "alcolock", {"bac_permille.mul": 0.01}))),
    ("q06", "2025-02-27", "dina", None, "q05", "short", (
        ("parseok", "adas"),
        ("parseok", "alcolock"))),
    ("q07", "2025-03-10", "pavel", "Разбор записей груза", None, "mid", (
        ("parse", "cargo_temps", {"temps_c.mul": 0.1}),
        ("parse", "humidity", {"rh_pct.mul": 0.1}))),
    ("q08", "2025-03-11", "oleg", None, "q07", "short", (
        ("parseok", "cargo_temps"),
        ("parseok", "humidity"))),
    # --- release test vectors ----------------------------------------------------------------------------------
    ("v33", "2024-06-25", "artem", "Контрольные записи к 3.3", None, "long", (
        ("note", None, "Для тех, кто пишет разбор: живые записи пакета датчиков с машин на 3.3, по нескольку на тип, "
         "с разбором.", ()),
        ("example", "can_fuel", 3), ("example", "can_hours", 3), ("example", "dash_lamps", 3), ("example", "adblue", 3),
        ("example", "brakes", 3), ("example", "tacho_activity", 3), ("example", "tacho_times", 3),
        ("example", "tacho_speed", 3))),
    ("v34", "2024-10-28", "dina", "Контрольные записи к 3.4: прицеп и рефрижератор", None, "long", (
        ("example", "trailer", 3), ("example", "trailer_ebs", 3), ("example", "trailer_power", 3),
        ("example", "reefer_zone", 3), ("example", "reefer_mode", 3), ("example", "reefer_unit", 3))),
    ("v35", "2024-10-29", "artem", "Контрольные записи к 3.4: нагрузки и кузов", None, "long", (
        ("example", "axle_loads", 3), ("example", "body_tilt", 3), ("example", "weight", 3),
        ("example", "mixer_drum", 3))),
    ("p01", "2024-07-29", "pavel", "vtldump: CAN-часть пакета", None, "long", (
        ("note", None, "Дописываю в vtldump поддержку пакета датчиков. Прежде чем выкладывать, выписал, как понял "
         "каждую запись CAN-части; поправьте, где не так.", ()),
        ("sum", "can_fuel", {"economy_kml.mul": 1 / 256}),
        ("sum", "can_hours", {"service_km.fmt": "u16"}),
        ("sum", "dash_lamps", {}),
        ("sum", "adblue", {}),
        ("sum", "brakes", {"air_kpa.mul": 4}))),
    ("p02", "2024-07-30", "artem", None, "p01", "mid", (
        ("sumok", "can_fuel", "Экономичность блок отдаёт в 1/512 км/л, это стандартная единица шины."),
        ("sumok", "can_hours", "Просроченное обслуживание — это минус, без знака его не записать."),
        ("sumok", "dash_lamps"),
        ("sumok", "adblue"),
        ("sumok", "brakes", "Давление в контуре блок отдаёт шагами по 8 кПа."))),
    ("p03", "2024-08-13", "pavel", "vtldump: тахограф", None, "long", (
        ("sum", "tacho_activity", {"slot.enum": ("codriver:сменщик", "driver:водитель")}),
        ("sum", "tacho_times", {"break_min.null": None}),
        ("sum", "tacho_speed", {"direction.enum": ("reverse:назад", "forward:вперёд")}))),
    ("p04", "2024-08-14", "dina", None, "p03", "mid", (
        ("sumok", "tacho_activity", "Слоты тахограф нумерует так же, как они стоят в приборе: сначала водитель."),
        ("sumok", "tacho_times"),
        ("sumok", "tacho_speed"))),
    ("p05", "2024-10-01", "pavel", "vtldump: прицеп", None, "long", (
        ("sum", "trailer", {"length_m.mul": 1}),
        ("sum", "trailer_ebs", {"load_kg.mul": 1}),
        ("sum", "trailer_power", {"amps.null": -32768}))),
    ("p06", "2024-10-02", "sergey", None, "p05", "mid", (
        ("sumok", "trailer", "Длину блок отдаёт с точностью до десяти сантиметров."),
        ("sumok", "trailer_ebs"),
        ("sumok", "trailer_power"))),
    ("p07", "2024-11-11", "pavel", "vtldump: нагрузки и кузов", None, "long", (
        ("sum", "axle_loads", {"total_kg.mul": 10}),
        ("sum", "body_tilt", {"body_angle_deg.mul": 1}),
        ("sum", "weight", {}),
        ("sum", "mixer_drum", {"water_l.mul": 1}))),
    ("p08", "2024-11-12", "artem", None, "p07", "mid", (
        ("sumok", "axle_loads", "Сумму модуль считает грубее, чем нагрузки по осям."),
        ("sumok", "body_tilt"),
        ("sumok", "weight"),
        ("sumok", "mixer_drum", "Расходомер воды на бетоносмесителях точнее литра."))),
    ("p09", "2024-12-02", "pavel", "vtldump: рефрижератор", None, "long", (
        ("sum", "reefer_zone", {}),
        ("sum", "reefer_mode", {"fan.null": None}),
        ("sum", "reefer_unit", {"run_h.fmt": "u32"}))),
    ("p10", "2024-12-03", "dina", None, "p09", "mid", (
        ("sumok", "reefer_zone"),
        ("sumok", "reefer_mode", "Вентилятор у старых установок не опрашивается, для них есть особое значение."),
        ("sumok", "reefer_unit", "Наработка растёт без ограничений, поэтому varint."))),
    ("p11", "2025-02-17", "pavel", "vtldump: кабина", None, "long", (
        ("sum", "adas", {}),
        ("sum", "fatigue", {"kind.enum": ("yawn:зевота", "eyes_closed:закрытые глаза", "phone:телефон",
                                          "smoking:курение", "no_belt:не пристёгнут", "distracted:взгляд в сторону")}),
        ("sum", "seatbelts", {}),
        ("sum", "alcolock", {"device_c.fmt": "u8"}))),
    ("p12", "2025-02-18", "dina", None, "p11", "mid", (
        ("sumok", "adas"),
        ("sumok", "fatigue"),
        ("sumok", "seatbelts"),
        ("sumok", "alcolock", "Прибор на морозе бывает и ниже нуля, поэтому байт со знаком."))),
    ("p13", "2025-03-03", "pavel", "vtldump: груз и охрана", None, "long", (
        ("sum", "cargo_temps", {"logger_id.fmt": "U16"}),
        ("sum", "humidity", {"dew_c.null": -32768}),
        ("sum", "seal", {"seal_id.fmt": "u32"}),
        ("sum", "fuel_drain", {"duration_s.mul": 10}),
        ("sum", "pto", {}))),
    ("p14", "2025-03-04", "oleg", None, "p13", "mid", (
        ("sumok", "cargo_temps"),
        ("sumok", "humidity"),
        ("sumok", "seal", "Номера пломб у нового поставщика длиннее четырёх байт."),
        ("sumok", "fuel_drain"),
        ("sumok", "pto"))),
)


# ---------------------------------------------------------------------------
# Correspondence: drafts and rendering
# ---------------------------------------------------------------------------

_SLOT_RE = re.compile(r"\{(\w+)\}")


def _fill(R, text: str) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key == "n":
            return str(R.randint(12, 140))
        if key == "n2":
            return str(R.randint(2, 9))
        return R.choice(SLOTS[key])

    return _SLOT_RE.sub(sub, text)


def _noise_paragraph(R, topic: str, used: set[str], bank: dict = NOISE) -> str:
    """3–5 background sentences of one topic, not repeating sentences already used in this message."""
    fresh = [s for s in bank[topic] if s not in used]
    if len(fresh) < 3:
        used.difference_update(bank[topic])
        fresh = list(bank[topic])
    picked = R.sample(fresh, min(len(fresh), R.randint(3, 5)))
    used.update(picked)
    sentences = [_fill(R, s) for s in picked]
    return " ".join(x[:1].upper() + x[1:] for x in sentences)


@dataclass
class Mail:
    num: int  # file number, chronological over both waves
    label: str
    date: str
    time: str
    sender: str
    subject: str
    parent: int | None
    blocks: tuple
    draft: str
    key: str = ""  # rewrite-fixture key; stable for the first wave ("mail/001" … "mail/067")
    parent_label: str | None = None


def _draft(R, sender: str, parent: Mail | None, blocks: tuple, size: str, bank: dict) -> tuple[str, str]:
    """Draft body and send time of one message (the RNG call order is part of the fixture contract)."""
    facts = [_block(k)[0] for k in blocks]
    target = R.randint(*SIZES[size])
    topics = PEOPLE[sender][4]
    noise: list[str] = []
    used: set[str] = set()
    total = sum(len(f) + 2 for f in facts)
    while total < target:
        open_topics = [t for t in bank if sum(x not in used for x in bank[t]) >= 3] or list(bank)
        weights = [6 if t == topics[0] else 4 if t == "techmisc" else 3 if t in topics else 1
                   for t in open_topics]
        paragraph = _noise_paragraph(R, R.choices(open_topics, weights=weights)[0], used, bank)
        noise.append(paragraph)
        total += len(paragraph) + 2
    if size == "short":
        paras = facts + noise
    else:
        spots = sorted(R.randint(min(1, len(noise)), len(noise)) for _ in facts)
        paras = []
        for j in range(len(noise) + 1):
            paras.extend(f for f, s in zip(facts, spots, strict=True) if s == j)
            if j < len(noise):
                paras.append(noise[j])
    if parent is not None and R.random() < 0.7:
        greeting = (f"{PEOPLE[parent.sender][2]}, привет." if R.random() < 0.5
                    else f"{PEOPLE[parent.sender][2]}, коллеги,")
    else:
        greeting = R.choice(GREETINGS)
    draft = "\n\n".join([greeting, *paras, R.choice(CLOSINGS)])
    return draft, f"{R.randint(8, 21):02d}:{R.randint(0, 59):02d}"


@functools.cache
def build_mail() -> list[Mail]:
    by_label: dict[str, Mail] = {}
    waves = ((THREADS, rng(TASK_ID + ":mail"), NOISE, "mail/{:03d}"),
             (THREADS2, rng(TASK_ID + ":mail2"), NOISE_ALL, "mail/b{:02d}"),
             (_story()["threads"], rng(TASK_ID + ":mail3"), NOISE_ALL, "mail/s-{label}"))
    for wave, (threads, R, bank, key_fmt) in enumerate(waves):
        for idx, (label, date, sender, subject, parent, blocks, size) in enumerate(threads, 1):
            pm = by_label[parent] if parent else None
            if subject is None:
                assert pm is not None
                subject = "Re: " + pm.subject.removeprefix("Re: ")
            draft, time = _draft(R, sender, pm, blocks, size, bank)
            by_label[label] = Mail(wave * 1000 + idx, label, date, time, sender, subject, None, blocks, draft,
                                   key_fmt.format(idx, label=label), parent)
    mails = sorted(by_label.values(), key=lambda m: (m.date, m.num))
    for n, mail in enumerate(mails, 1):
        mail.num = n
    for mail in mails:
        if mail.parent_label:
            mail.parent = by_label[mail.parent_label].num
            assert mail.parent < mail.num, mail.label
    return mails


def _block(key: str) -> tuple:
    return BLOCKS[key] if key in BLOCKS else _story()["blocks"][key]


def _body(mail: Mail) -> str:
    return rewritten(TASK_ID, mail.key, mail.draft)


def _quote(text: str, limit: int = 1800) -> str:
    paras = [p for p in text.split("\n\n") if p.strip()]
    taken = [paras[0]]
    for p in paras[1:]:
        if sum(len(x) for x in taken) + len(p) > limit:
            break
        taken.append(p)
    lines = "\n\n".join(taken).splitlines()
    return "\n".join(f"> {line}" if line.strip() else ">" for line in lines)


def render_mail(mail: Mail, mails: list[Mail]) -> str:
    name, email, _short, role, _ = PEOPLE[mail.sender]
    to = "vtl-team@vector-t.ru" + {"gleb": "; m-team@vector-m.ru", "vera": "; it@translogistic.ru"}.get(mail.sender, "")
    org = {"gleb": "«Вектор-М»", "vera": "«ТрансЛогистик»"}.get(mail.sender, f"«Вектор-Т», {role}")
    lines = [f"# {mail.subject}", "", f"**От:** {name} <{email}>  ", f"**Кому:** {to}  ",
             f"**Дата:** {mail.date} {mail.time}  ", f"**Тема:** {mail.subject}  "]
    if mail.parent is not None:
        lines.append(f"**В ответ на:** {mail.parent:03d}.md  ")
    lines += ["", _body(mail), "", "--", name, org]
    if mail.parent is not None:
        pm = mails[mail.parent - 1]
        lines += ["", f"{PEOPLE[pm.sender][0]} писал(а) {pm.date} в {pm.time}:", _quote(_body(pm))]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Firmware changelog
# ---------------------------------------------------------------------------

CHANGELOG_VERSIONS = (
    ("1.0", "2018-11-20", ("Первый серийный выпуск. Журнал поездок `.vtl` (версия формата 1).",)),
    ("1.1", "2019-01-15", ()),
    ("1.2", "2019-06-24", ()),
    ("1.3", "2020-01-27", ("Журнал: при отказе датчика скорости пишется особое значение «нет данных» вместо нуля.",)),
    ("1.4", "2020-04-20", ("Журнал: в запись координат добавлен фактор точности приёмника.",)),
    ("1.5", "2020-10-05", ()),
    ("2.0", "2021-04-26", ("Журнал версии 2: длительность тика в заголовке, время внутри блоков в тиках, "
                           "приращения времени со знаком.",
                           "Журнал: новые записи — питание (тип 6) и события поездки (тип 7).",
                           "Журнал: температура масла в записи двигателя при наличии датчика.",
                           "Журнал: положение педали записывается в полном диапазоне байта.")),
    ("2.0.1", "2021-05-24", ("Журнал: событие поездки «продолжение после паузы».",)),
    ("2.1", "2021-10-18", ("Питание: ток АКБ теперь в мА.", "Питание: новая фильтрация измерений тока.")),
    ("2.2", "2022-03-14", ()),
    ("2.3", "2022-08-01", ()),
    ("3.0", "2023-01-30", ("Журнал версии 3: местное время и часовой пояс в заголовке, строки в UTF-8, "
                           "координаты приращениями, изменена контрольная сумма блоков.",
                           "Журнал: новые записи — резкое вождение, давление в шинах, двери, уровень топлива.")),
    ("3.0.1", "2023-03-20", ()),
    ("3.0.2", "2023-05-15", ("Журнал: новая запись — карта водителя (считыватель на планшете).",
                             "Журнал: резкое вождение — новый вид события для эвакуаторов и спецтехники.")),
    ("3.1", "2023-10-02", ()),
    ("3.2", "2024-02-19", ()),
    ("3.3", "2024-06-10", ("Журнал: пакет датчиков, часть 1 — CAN-шина (топливо, моточасы, лампы панели, AdBlue, "
                           "тормоза) и тахограф.",)),
    ("3.3.1", "2024-07-29", ("Журнал: качество раствора в записи бака AdBlue.",)),
    ("3.4", "2024-10-14", ("Журнал: пакет датчиков — прицеп, рефрижератор, нагрузки на оси, наклон кузова, вес, "
                           "бетоносмеситель.", "Журнал: пробег по тахографу пишется шагами по 10 м.")),
    ("3.4.1", "2024-12-16", ()),
    ("3.5", "2025-03-17", ("Журнал: пакет датчиков — кабина (ADAS, усталость, ремни, алкозамок), груз и охрана (щупы, "
                           "влажность, пломбы, сливы топлива), коробка отбора мощности.",
                           "Журнал: заряд батареи пломбы — в десятых долях процента.")),
    ("3.5.1", "2025-06-16", ("Журнал: записи коммунальной техники — разбрасыватель и подъёмник контейнеров.",)),
)

CHANGELOG_NOISE = (
    "Исправлена перезагрузка при слабом сигнале GSM во время передачи данных.",
    "Ускорен холодный старт приёмника ГЛОНАСС/GPS.",
    "Снижено потребление в режиме сна.",
    "Исправлена потеря настроек APN после обновления по воздуху.",
    "Добавлена поддержка SIM-чипов второго поставщика.",
    "Уточнена фильтрация «прыжков» координат на стоянке.",
    "Исправлено зависание при переполнении флеш-памяти, старые блоки перезаписываются по кругу.",
    "Обновлён загрузчик; откат на прошлую версию прошивки больше не требует кабеля.",
    "Индикация светодиодом состояния связи.",
    "Исправлена ошибка в расписании выхода на связь при смене часового пояса сервера.",
    "Добавлена SMS-команда для удалённого перезапуска модема.",
    "Настройки трекера читаются из `tracker.ini` на флешке при старте.",
    "Исправлен счётчик моточасов при кратковременном пропадании зажигания.",
    "Поддержка нового модуля CAN-шины для грузовой техники.",
    "Снижен трафик онлайн-канала за счёт группировки пакетов ВТП.",
    "Исправлена редкая порча последнего блока журнала при выключении питания.",
    "Трекер больше не шлёт пустые пакеты при отсутствии фиксации.",
    "Обновлён стек TLS для онлайн-канала.",
    "Порог определения стоянки вынесен в настройки.",
    "Исправлено определение напряжения бортовой сети 24 В.",
    "Ускорена выгрузка журнала по USB.",
    "Отключена отладочная печать в серийных сборках.",
    "Исправлена работа сторожевого таймера при долгой записи во флеш.",
    "Добавлена проверка целостности образа прошивки перед установкой.",
    "Мелкие исправления стабильности.",
)


def changelog() -> str:
    R = rng(TASK_ID + ":changelog")
    out = ["# Прошивка трекера «Вектор-Т»: релизные заметки", "",
           "Заметки пишутся в день выпуска и перечисляют изменения кратко. Новые версии — внизу.", ""]
    for version, date, lines in CHANGELOG_VERSIONS:
        items = list(lines) + R.sample(CHANGELOG_NOISE, R.randint(4, 8))
        R.shuffle(items)
        out += [f"## {version} ({date})", ""] + [f"- {item}" for item in items] + [""]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# LLM rewriting of the messages (scripts/rewrite_long_texts.py)
# ---------------------------------------------------------------------------

# firmware-team statements that a later message corrects (the rewrite must keep them wrong)
SUPERSEDED = {"F_status_wrong", "F_battery_ma", "F_zone_proposal", "F_crc32_proposal", "F_fuel_proposal",
              "F_tires_proposal", "F_doors_initial", "F_harsh", "F_card_initial"}


def _block_role(key: str) -> str:
    if key.startswith("F_"):
        role = ("утверждение команды прошивки о журнале .vtl: сохрани все числа, порядок байтов и позиции полей "
                "точно; описывай поля так же косвенно, как черновик (через положение, соседние поля, роль), не "
                "добавляй названий полей и номеров типов, которых в черновике нет")
        if key in SUPERSEDED or (key.startswith("F_s_") and key in _story()["superseded"]):
            role += ("; позже в переписке это утверждение исправлено — НЕ исправляй его здесь и не добавляй "
                     "оговорок, оно должно остаться таким же уверенным")
        return role
    if key[:2] in ("P_", "M_", "T_", "V_"):
        return ("вопрос, наблюдение или догадка участника не из команды прошивки — должно остаться "
                "вопросом/догадкой с теми же числами, не превращай в утверждение")
    if key.startswith("D_"):
        return ("сведения о другом формате или протоколе (не .vtl) — должно остаться ясно, к чему они относятся; "
                "числа сохрани")
    return "организационное сообщение — смысл сохрани"


def _questions(mail: Mail) -> list[tuple]:
    return [q for k in mail.blocks for q in (*_block(k)[1], *EXTRA_Q.get(k, ()))] or [NO_FACTS_Q]


def _rewrite_items() -> list[dict]:
    items = []
    for mail in build_mail():
        name, _email, _short, role, _ = PEOPLE[mail.sender]
        frags = [f"- «{_block(k)[0]}» — {_block_role(k)}" for k in mail.blocks]
        checks = [f"- {q} → {a}" for q, a, _opts in _questions(mail)]
        brief = (
            f"Письмо от {name} ({role}), {mail.date}, тема «{mail.subject}».\n"
            + ("Технические фрагменты черновика и что с ними нужно сохранить:\n" + "\n".join(frags) + "\n"
               if frags else "В письме нет сведений о журнале .vtl — не добавляй их.\n")
            + "Независимый читатель должен по твоему тексту ответить на эти вопросы так же:\n" + "\n".join(checks)
        )
        items.append({"key": mail.key, "draft": mail.draft, "brief": brief, "questions": _questions(mail),
                      "header": f"От: {name} ({role})\nДата: {mail.date} {mail.time}\nТема: {mail.subject}"})
    return items


_WRITER_SYSTEM = (
    "Ты переписываешь письма из рабочей почты небольшой команды, которая делает автомобильные GPS-трекеры "
    "«Вектор-Т» (прошивка, железо, бэкенд, поддержка). Письмо должно звучать как настоящее: свои слова, свой "
    "порядок мыслей, живые обороты, разная длина фраз, без канцелярита и без опечаток. Не повторяй фразы "
    "черновика дословно — перескажи каждую мысль по-своему; бытовые подробности можно менять и добавлять, "
    "общую длину сохрани (±25 %). Приветствие и прощание — в стиле автора; подпись не добавляй, её допишут.\n\n"
    "Жёсткие требования к техническим фрагментам (их список и роли даны в задании): каждое техническое "
    "утверждение должно сохраниться с теми же числами и тем же смыслом, в той же уверенности (вопрос остаётся "
    "вопросом, догадка — догадкой, утверждение — утверждением) и от того же лица. Не добавляй новых "
    "технических сведений о формате журнала .vtl, не исправляй утверждения, даже если они неверны, не называй "
    "поля и типы записей прямее, чем черновик. Технические фрагменты не выноси в начало письма — пусть они "
    "стоят среди остального текста, как в черновике.\n\n"
    "Справка об участниках переписки:\n" + MAIL_README
)


def _judge_messages(item: dict, text: str) -> list[dict]:
    lines = []
    for i, (q, _a, opts) in enumerate(item["questions"], 1):
        hint = f" Варианты: {' | '.join(opts)}." if opts else " Ответ — число."
        lines.append(f"{i}. {q}{hint}")
    system = (
        "Ты читаешь письмо из рабочей переписки команды трекеров «Вектор-Т». Справка о переписке:\n"
        + MAIL_README + "\n\nСтарое описание формата журнала (2019), к которому письма иногда отсылают:\n"
        + FORMAT_V1 + "\n\nОтветь на вопросы строго по тому, что утверждается в ЭТОМ письме (даже если "
        "утверждение неверно или позже исправлено). Ответ — только JSON-объект вида {\"1\": \"...\", "
        "\"2\": \"...\"}: для вопроса с вариантами — один вариант дословно, для числового — число."
    )
    user = f"{item['header']}\n\n{text}\n\nВопросы:\n" + "\n".join(lines)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _answer_ok(got, answer: str, opts) -> bool:
    if opts:
        return norm(got) == norm(answer)
    value = num(got)
    if value is None:
        m = re.search(r"-?\d+(?:[.,]\d+)?", str(got))
        value = num(m.group(0)) if m else None
    return value is not None and math.isclose(value, float(answer), rel_tol=1e-9, abs_tol=1e-12)


def _judge_accept(item: dict, reply: str) -> tuple[bool, str]:
    match = re.search(r"\{.*\}", reply, re.S)
    try:
        got = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        got = None
    if not isinstance(got, dict):
        return False, "независимый читатель не смог ответить на вопросы по тексту"
    wrong = []
    for i, (q, answer, opts) in enumerate(item["questions"], 1):
        if not _answer_ok(got.get(str(i), ""), answer, opts):
            wrong.append(f"«{q}» — читатель ответил «{got.get(str(i), '')}», а по черновику «{answer}»")
    if not wrong:
        return True, ""
    return False, "по твоему тексту неверно восстановлено: " + "; ".join(wrong) + ". Сделай эти места однозначнее."


REWRITE = {
    "items": _rewrite_items,
    "writer_system": _WRITER_SYSTEM,
    "judge_messages": _judge_messages,
    "judge_accept": _judge_accept,
}


# ---------------------------------------------------------------------------
# setup / gold / check
# ---------------------------------------------------------------------------


def setup(ws: Path) -> None:
    data = build()
    write(ws, "docs/format_v1.md", FORMAT_V1)
    write(ws, "docs/output_format.md", OUTPUT_FORMAT)
    write(ws, "docs/firmware_changelog.md", changelog())
    write(ws, "docs/mail/README.md", MAIL_README)
    mails = build_mail()
    for mail in mails:
        write(ws, f"docs/mail/{mail.num:03d}.md", render_mail(mail, mails))
    write(ws, "decoded/README.md", DECODED_README)
    for vf in data["samples"]:
        write(ws, f"samples/{vf.name}.vtl", vf.data)
        if vf.name in data["decoded"]:
            write(ws, f"decoded/{vf.name}.json", old_dump(vf))


def gold(ws: Path) -> None:
    write(ws, "vtl_decode.py", DECODER)


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return math.isfinite(b) and abs(a - b) <= 1e-6
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return a is None and b is None


def check(ws: Path) -> str:
    assert (ws / "vtl_decode.py").is_file(), "нет файла vtl_decode.py"
    hidden = build()["hidden"]
    ok = 0
    errors = []
    with tempfile.TemporaryDirectory(prefix="hb_vtl_") as tmp:
        for vf in hidden:
            path = Path(tmp) / f"{vf.name}.vtl"
            path.write_bytes(vf.data)
            proc = run_python(ws, ["vtl_decode.py", str(path)], timeout=30)
            if proc.returncode != 0:
                errors.append(f"{vf.name} (v{vf.version}): код возврата {proc.returncode}")
                continue
            try:
                got = json.loads(proc.stdout)
            except json.JSONDecodeError:
                errors.append(f"{vf.name} (v{vf.version}): вывод не JSON")
                continue
            if _same(expected(vf), got):
                ok += 1
            else:
                errors.append(f"{vf.name} (v{vf.version}): результат расходится с эталоном")
    return require_share(len(hidden), ok, min_share=0.95, what="скрытые файлы", errors=errors)


# ---------------------------------------------------------------------------
# Near misses: plausible variants of the decoder
# ---------------------------------------------------------------------------


def _patched(*pairs: tuple[str, str]) -> str:
    src = DECODER
    for old, new in pairs:
        assert src.count(old) == 1, old
        src = src.replace(old, new)
    return src


def _nm(name: str, doc: str, *pairs: tuple[str, str]):
    def miss(ws: Path) -> None:
        write(ws, "vtl_decode.py", _patched(*pairs))

    miss.__name__ = name
    miss.__doc__ = doc
    return miss


_P_TEXT_CP1251 = ('raw.decode("utf-8" if version >= 3 else "cp1251")', 'raw.decode("cp1251", "replace")')
_P_NO_ZONE = ("    base_time -= zone * 900\n", "")
_P_NO_V3_TYPES = ("    if version < 3:\n        return None\n", "    return None\n")
_P_STATUS_DOC = ('"confirmed": bool(status & 1), "pending": bool(status & 2)',
                 '"confirmed": bool(status & 2), "pending": bool(status & 1)')
_P_HEADING_RAW = ('"heading": heading * 2', '"heading": heading')
_P_SPEED_RAW = ('"kmh": None if speed == 0xFFFF else speed / 100', '"kmh": speed / 100')
_P_THROTTLE_RAW = ('"throttle_pct": throttle if version == 1 else throttle * 100 / 255', '"throttle_pct": throttle')
_P_OIL_NONE = ("oil = body[5] - 40 if version >= 2 and len(body) >= 6 else None", "oil = None")
_P_HDOP_NONE = ('"hdop": None if hdop is None else hdop / 100', '"hdop": None')
_P_NO_BATTERY_TRIP = ("    if rtype == 6:\n", "    if rtype == -6:\n")
_P_NO_TRIP = ("    if rtype == 7:\n", "    if rtype == -7:\n")

nm_samples_and_v1_doc = _nm(
    "nm_samples_and_v1_doc",
    "Decoder built only from samples + format_v1.md: framing and v2/v3 layout fitted on the files, "
    "semantics from the 2019 document, record types without documentation left unknown.",
    ('"lat": lat / 1_000_000, "lon": lon / 1_000_000', '"lat": lat / 100_000, "lon": lon / 100_000'),
    ('"rpm": rpm / 4, "coolant_c": coolant - 40', '"rpm": rpm, "coolant_c": coolant - 256 * (coolant > 127)'),
    _P_TEXT_CP1251, _P_NO_ZONE, _P_NO_V3_TYPES, _P_STATUS_DOC, _P_HEADING_RAW, _P_SPEED_RAW, _P_THROTTLE_RAW,
    _P_OIL_NONE, _P_HDOP_NONE, _P_NO_BATTERY_TRIP, _P_NO_TRIP,
)
nm_samples_doc_common_sense = _nm(
    "nm_samples_doc_common_sense",
    "Samples + format_v1.md + common OBD knowledge (lat 1e-6, rpm/4, coolant-40, cp1251 from the dumps), no mail.",
    _P_TEXT_CP1251, _P_NO_ZONE, _P_NO_V3_TYPES, _P_STATUS_DOC, _P_HEADING_RAW, _P_SPEED_RAW, _P_THROTTLE_RAW,
    _P_OIL_NONE, _P_HDOP_NONE, _P_NO_BATTERY_TRIP, _P_NO_TRIP,
)
nm_no_mail_only_types = _nm("nm_no_mail_only_types", "v3 types 8/10/11/12 (only in the mail) left unknown.",
                            _P_NO_V3_TYPES)
nm_status_first_answer = _nm("nm_status_first_answer", "Fault status bits as in Oleg's first (retracted) reply.",
                             _P_STATUS_DOC)
nm_battery_ma = _nm("nm_battery_ma", "Battery current in mA (first message; corrected; changelog typo).",
                    ('"amps": current / 100', '"amps": current / 1000'))
nm_no_zone = _nm("nm_no_zone", "v3 header time taken as UTC (zone byte skipped).", _P_NO_ZONE)
nm_zone_added = _nm("nm_zone_added", "v3 zone offset added instead of subtracted (Pavel's guess).",
                    ("    base_time -= zone * 900\n", "    base_time += zone * 900\n"))
nm_crc_v3_payload = _nm("nm_crc_v3_payload", "v3 CRC over the payload only.",
                        ("covered = data[pos + 1:pos + 4 + length]", "covered = payload"))
nm_gps_v3_absolute = _nm("nm_gps_v3_absolute", "v3 coordinate deltas taken as absolute values.",
                         ('        state["lat"] += dlat\n        state["lon"] += dlon\n',
                          '        state["lat"] = dlat\n        state["lon"] = dlon\n'))
nm_v3_cp1251 = _nm("nm_v3_cp1251", "v3 strings still decoded as cp1251.", _P_TEXT_CP1251)
nm_type12_everywhere = _nm(
    "nm_type12_everywhere", "Type 12 decoded as fuel in v1/v2 files too.",
    ("    if version < 3:\n        return None\n", "    if version < 3 and rtype != 12:\n        return None\n"))
nm_harsh_ms = _nm("nm_harsh_ms", "Harsh-event duration in ms (Dina's first message).",
                  ('"duration_ms": duration * 10', '"duration_ms": duration'))
nm_tires_planned_step = _nm("nm_tires_planned_step", "Tyre pressure in the planned 2.5 kPa steps.",
                            ('"kpa": pressure * 4', '"kpa": pressure * 2.5'))
nm_tire_temp_offset = _nm("nm_tire_temp_offset", "Tyre temperature with the coolant offset (Marina's guess).",
                          ('"temp_c": temp}', '"temp_c": (temp & 0xFF) - 40}'))
nm_doors_initial = _nm("nm_doors_initial", "Door bits as in Dina's November message (before the board fix).",
                       ('(2, "side"), (3, "rear")', '(2, "rear"), (3, "side")'))
nm_fuel_whole_liters = _nm("nm_fuel_whole_liters", "Fuel volume in whole litres (Timur's guess).",
                           ("else liters / 10}", "else liters}"))
nm_throttle_scaled_v1 = _nm("nm_throttle_scaled_v1", "Full-byte pedal scale applied to v1 files too.",
                            ('"throttle_pct": throttle if version == 1 else throttle * 100 / 255',
                             '"throttle_pct": throttle * 100 / 255'))
nm_oil_raw = _nm("nm_oil_raw", "Oil temperature without the offset (Pavel's guess).",
                 ("oil = body[5] - 40 if", "oil = body[5] if"))
nm_hdop_tenths = _nm("nm_hdop_tenths", "HDOP in tenths (Pavel's guess).",
                     ('"hdop": None if hdop is None else hdop / 100', '"hdop": None if hdop is None else hdop / 10'))
nm_speed_no_null = _nm("nm_speed_no_null", "All-ones speed printed as a number.", _P_SPEED_RAW)
nm_card_prototype = _nm(
    "nm_card_prototype", "Driver card as in Dina's April message: 4-byte big-endian number, then the event byte.",
    ('        card, _ = uvarint(body, 1)\n        return {"type": "driver", "event": DRIVER[body[0]], "card": card}\n',
     '        (card,) = struct.unpack_from(">I", body)\n'
     '        return {"type": "driver", "event": DRIVER.get(body[4], "login"), "card": card}\n'))
nm_no_driver = _nm("nm_no_driver", "v3 type 13 (driver card, only in the mail) left unknown.",
                   ("    if rtype == 13:\n", "    if rtype == -13:\n"))
nm_card_all_versions = _nm(
    "nm_card_all_versions", "Type 13 decoded as a driver card in v1/v2 files too (Marina's question).",
    ("    if version < 3:\n        return None\n", "    if version < 3 and rtype != 13:\n        return None\n"))
nm_no_denied = _nm("nm_no_denied", "Driver events without the later 'denied' code (unknown codes read as login).",
                   ('"event": DRIVER[body[0]]', '"event": {0: "login", 1: "logout"}.get(body[0], "login")'))
nm_rollover_as_bump = _nm("nm_rollover_as_bump", "Harsh-event kind 4 read as a bump (Timur's guess).",
                          ('3: "bump", 4: "rollover"}', '3: "bump", 4: "bump"}'))
nm_no_resume = _nm("nm_no_resume", "Trip events without the later 'resume' code.",
                   ('EVENTS = {0: "start", 1: "stop", 2: "pause", 3: "resume"}',
                    'EVENTS = {0: "start", 1: "stop", 2: "pause"}'))

def _finals() -> dict:
    return {st.key: _final_state(st) for st in SENSORS}


def _nm_sensors(name: str, doc: str, beliefs, *pairs: tuple[str, str]):
    def miss(ws: Path) -> None:
        src = _decoder_src(beliefs())
        for old, new in pairs:
            assert src.count(old) == 1, old
            src = src.replace(old, new)
        write(ws, "vtl_decode.py", src)

    miss.__name__ = name
    miss.__doc__ = doc
    return miss


def _with(base: dict, **extra) -> dict:
    return {**base, **extra}


def _old_names() -> dict:
    out = _finals()
    for t, prop in _proposals().items():
        if t in out and not out[t]["dropped"]:
            out[t]["name"] = prop["name"]
            for fid in out[t]["order"]:
                if fid in prop["f"] and fid in prop["order"]:
                    out[t]["f"][fid]["key"] = prop["f"][fid]["key"]
    return out


def _changelog_beliefs() -> dict:
    out = _finals()
    out["seal"]["f"]["battery_pct"]["mul"] = 0.1
    out["tacho_speed"]["f"]["distance_km"]["mul"] = 0.01
    return out


def _no_optional_tail() -> dict:
    out = _finals()
    for t, state in out.items():
        state["order"] = [f for f in state["order"] if not _meta(SENSOR_BY_KEY[t])[f].opt]
    return out


nm_sensor_first_proposals = _nm_sensors(
    "nm_sensor_first_proposals", "Sensor-pack types decoded as first proposed (numbers, names, layouts).", _proposals)
nm_sensor_no_reversal = _nm_sensors(
    "nm_sensor_no_reversal", "Later reversals ignored (tachograph distance step, gross weight step).",
    lambda: _run(skip=frozenset((m[0], i) for m in MSGS3 for i, ev in enumerate(m[6]) if ev[0] == "revert")))
nm_sensor_dropped_known = _nm_sensors(
    "nm_sensor_dropped_known", "Types dropped before the release decoded by their proposals.",
    lambda: _finals() | {k: v for k, v in _proposals().items() if SENSOR_BY_KEY[k].dropped})
nm_sensor_old_names = _nm_sensors("nm_sensor_old_names", "Renamed records and fields keep their first names.",
                                  _old_names)
nm_sensor_trust_everyone = _nm_sensors(
    "nm_sensor_trust_everyone", "Guesses, claims and summaries of non-firmware people taken as decisions.",
    lambda: _run(trust=True))
nm_sensor_changelog = _nm_sensors("nm_sensor_changelog", "Release notes believed over later firmware mail.",
                                  _changelog_beliefs)
nm_sensor_pto_old_number = _nm_sensors(
    "nm_sensor_pto_old_number", "The abandoned 3.3-beta number of the PTO record decoded as PTO too.",
    lambda: _with(_finals(), **{"pto#old": _proposals()["pto"]}))
nm_sensor_no_optional = _nm_sensors("nm_sensor_no_optional", "Optional tail fields of sensor records not output.",
                                    _no_optional_tail)
nm_sensor_unknown = _nm_sensors("nm_sensor_unknown", "Sensor pack not decoded at all (every type unknown).", dict)
nm_sensor_in_v2 = _nm_sensors(
    "nm_sensor_in_v2", "Sensor-pack numbers decoded in v1/v2 journals too.", _finals,
    ("    if version < 3:\n        return None\n", "    if version < 3 and rtype not in SENSORS:\n        return None\n"))

NEAR_MISSES = [
    nm_samples_and_v1_doc, nm_samples_doc_common_sense, nm_no_mail_only_types, nm_status_first_answer,
    nm_battery_ma, nm_no_zone, nm_zone_added, nm_crc_v3_payload, nm_gps_v3_absolute, nm_v3_cp1251,
    nm_type12_everywhere, nm_harsh_ms, nm_tires_planned_step, nm_tire_temp_offset, nm_doors_initial,
    nm_fuel_whole_liters, nm_throttle_scaled_v1, nm_oil_raw, nm_hdop_tenths, nm_speed_no_null, nm_no_resume,
    nm_card_prototype, nm_no_driver, nm_card_all_versions, nm_no_denied, nm_rollover_as_bump,
    nm_sensor_first_proposals, nm_sensor_no_reversal, nm_sensor_dropped_known, nm_sensor_old_names,
    nm_sensor_trust_everyone, nm_sensor_changelog, nm_sensor_pto_old_number, nm_sensor_no_optional,
    nm_sensor_unknown, nm_sensor_in_v2,
]

PROMPT = """\
Нужно написать декодер журналов телеметрии трекеров «Вектор-Т» (двоичный формат .vtl, \
версии 1, 2 и 3). Скрипт vtl_decode.py в корне рабочего каталога: `python3 vtl_decode.py \
<файл.vtl>` печатает в stdout JSON строго в формате docs/output_format.md (там же — как \
поступать с испорченными и обрезанными блоками и с записями неизвестных типов). Только \
стандартная библиотека Python.

Материалы:
- docs/format_v1.md — старое (2019 года) описание версии 1; оно неполное, часть сведений в нём \
неверна;
- docs/mail/ — рабочая переписка команды за 2019–2025 годы: уточнения к описанию, смысл и единицы \
полей, изменения версий 2 и 3, новые типы записей. Кто есть кто и чьи утверждения считаются \
описанием формата — в docs/mail/README.md. Письма написаны свободно, сведения о формате \
разбросаны по ним, часто упомянуты мимоходом или косвенно, а более поздние письма бывают \
поправками к ранним, поэтому учитывать нужно каждое письмо;
- docs/firmware_changelog.md — краткие релизные заметки прошивки;
- samples/ — 24 реальных файла всех трёх версий;
- decoded/ — выгрузки 6 из этих файлов старым отладочным инструментом (что в них есть и чего \
нет — в decoded/README.md).

Декодер будет проверяться на других файлах всех трёх версий, в том числе с типами записей, \
которых нет в samples/, поэтому он должен правильно обрабатывать всё, что описано в материалах, \
а не только то, что встречается в образцах.
"""

TASK = long_task(
    id="task_403_binary_format_reverse",  # registry id; TASK_ID stays the generator seed
    name="Декодер двоичного формата телеметрии .vtl по переписке разработчиков",
    prompt=PROMPT,
    setup=setup,
    gold=gold,
    check=check,
    tags=("binary", "reverse-engineering", "code", "reading"),
)
