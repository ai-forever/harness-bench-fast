"""Causally unlocked, error-correcting backup retention."""
from __future__ import annotations

import copy
import itertools
import json

from .common import artifact_task


def recovery(data, selected, lost):
    labels = sorted(data['objects'])
    packets = sorted(data['packets'], key=lambda p: p['id'])
    known = sum(1 << labels.index(x) for x in data['initial_keys'])
    active = set()
    stages = []
    while True:
        unlocked = {i for i in selected if packets[i]['zone'] != lost
                    and all(known & (1 << labels.index(k)) for k in packets[i]['keys'])} - active
        active |= unlocked
        ambiguous = 0
        for delta in range(1, 1 << len(labels)):
            if delta & known:
                continue
            trusted_bad, untrusted_bad = False, 0
            for i in active:
                parity = sum(bool(delta & (1 << labels.index(x))) for x in packets[i]['xor']) % 2
                if parity:
                    if packets[i]['trusted']:
                        trusted_bad = True
                        break
                    untrusted_bad += 1
            if not trusted_bad and untrusted_bad <= 2 * data['faults']:
                ambiguous |= delta
        decoded = ((1 << len(labels)) - 1) ^ ambiguous
        new = decoded & ~known
        if not unlocked and not new:
            break
        stages.append({'unlocked': [packets[i]['id'] for i in sorted(unlocked)],
                       'recovered': [x for j, x in enumerate(labels) if new & (1 << j)]})
        known |= decoded
    return {'lost_zone': lost, 'stages': stages,
            'recoverable': [x for j, x in enumerate(labels) if known & (1 << j)]}


def solve500(data):
    from .workflow_revision_v5 import _solve_500
    names = sorted(data['policy']['candidates'])
    labels = sorted(data['objects'])
    packets = sorted(data['packets'], key=lambda p: p['id'])
    zones = sorted({p['zone'] for p in packets})
    import hashlib
    digest_labels = {hashlib.sha256(data['objects'][x].encode()).hexdigest(): x for x in labels}
    manifests = {x: _solve_500({**data, 'keep': [x], 'existing': []}) for x in names}
    required = {x: {digest_labels[h] for h in manifests[x]['required']} for x in names}
    plans = []
    for flags in itertools.product((False, True), repeat=len(names)):
        kept = [x for x, flag in zip(names, flags, strict=True) if flag]
        chosen, p = set(kept), data['policy']
        if not set(p['mandatory']) <= chosen:
            continue
        if any(x in chosen and not set(needs) <= chosen for x, needs in p['requires'].items()):
            continue
        if any(not g['min'] <= len(set(g['members']) & chosen) <= g['max'] for g in p['groups']):
            continue
        plans.append((sum(p['candidates'][x] for x in kept), kept,
                      set().union(*(required[x] for x in kept))))
    best, answer, cache, retention = None, None, {}, {}
    for mask in range(1 << len(packets)):
        selected = [i for i in range(len(packets)) if mask & (1 << i)]
        stored = sum(packets[i]['bytes'] for i in selected)
        upload = sum(packets[i]['upload'] for i in selected)
        if stored > data['max_bytes'] or upload > data['max_upload']:
            continue
        recovered = []
        for zone in zones:
            alive = tuple(i for i in selected if packets[i]['zone'] != zone)
            key = zone, alive
            if key not in cache:
                cache[key] = recovery(data, alive, zone)
            recovered.append(cache[key])
        robust = frozenset.intersection(*(frozenset(row['recoverable']) for row in recovered))
        if robust not in retention:
            valid = [(u, keep) for u, keep, need in plans if need <= robust]
            retention[robust] = min(valid, key=lambda p: (-p[0], len(p[1]), p[1])) if valid else None
        if retention[robust] is None:
            continue
        utility, kept = retention[robust]
        ids = [packets[i]['id'] for i in selected]
        score = -utility, upload, stored, len(kept), kept, len(ids), ids
        if best is None or score < best:
            best = score
            material = _solve_500({**data, 'keep': kept, 'existing': []})
            answer = {'feasible': True, 'utility': utility, 'retained': kept, 'packets': ids,
                      'upload_bytes': upload, 'stored_bytes': stored, 'recovery': recovered,
                      'snapshots': material['snapshots'], 'logical_bytes': material['logical_bytes']}
    return answer if answer is not None else {'feasible': False}


def build_v6(tasks, old_data):
    old = next(task for task in tasks if task.id.startswith('task_500_'))
    data = copy.deepcopy(old_data)
    data['initial_keys'], data['faults'] = ['b0'], 1
    data['max_bytes'], data['max_upload'] = 102, 49
    for packet in data['packets']:
        zone, index = packet['id'].split('-')
        index = int(index)
        packet['trusted'] = zone != 'B'
        packet['keys'] = ([f'b{index-1}'] if zone == 'A' and index > 1 else
                          ['b1'] if zone == 'C' and index > 0 else
                          ['b5'] if zone == 'B' and index == 0 else [])
    prompt = old.prompt.split('В storage')[0] + '''
Новый storage contract. Packet={id,zone,xor,bytes,upload,trusted,keys}.
backup/recovery-policy.json дополнительно содержит initial_keys и faults.
xor задаёт одну GF(2) equation над object blobs, padded нулями до общей ширины.
trusted equation достоверна. До faults (0 или1) выбранных UNTRUSTED packets
могут содержать произвольно испорченные значения; какие именно, неизвестно.
Отсутствие порчи тоже допустимо. Требуется гарантированная реконструкция при
ЛЮБЫХ values blobs и допустимой порче, не угадывание по исходному plaintext.
Исходные files описывают desired content, не являются доступными recovery data.

После потери каждой ОДНОЙ zone начинай независимое восстановление заново.
Каталог zones включает все исходные packets. initial_keys — blobs, известные
бесплатно и достоверно. Иные blob values первоначально неизвестны.
Packet доступен только если он выбран, его zone не потеряна и ВСЕ keys уже
восстановлены. Keys — object labels. Цикл зависимостей сам себя не открывает.
На каждом раунде одновременно активируй все новые доступные packets; затем
восстанови все blobs, однозначные для ВСЕХ совместимых с активными equations
миров при <=faults ошибках среди активных untrusted rows. Известные blobs
фиксированы; вновь восстановленные keys открывают packets только в СЛЕДУЮЩЕМ
раунде. Остановись на least fixed point. Ошибочный packet остаётся ошибочным;
нет права выкинуть предполагаемый fault или получить бесплатный oracle.
Эквивалентный точный критерий: difference vector d по неизвестным blobs может
создавать неоднозначность, если все trusted rows имеют dot(row,d)=0, а число
untrusted rows с dot(row,d)=1 <=2*faults. Blob восстановим тогда и только тогда,
когда во ВСЕХ таких d его бит0. Известные blobs требуют d_bit=0. Rank недостаточен.

Совместно выбери retained subset policy.candidates={snapshot:positive utility}
и packets. policy.mandatory обязательны; requires={snapshot:[snapshots]} —
импликации; groups=[{members,min,max}] — overlapping quotas на retained count.
Предки только материализуют состояния, не удерживаются автоматически. Все blobs
retained snapshots должны восстановиться после любой zone loss по процессу выше.
Sum packet.bytes<=max_bytes, sum upload<=max_upload; заданные costs positive.
Цель MIN (-utility,upload_bytes,stored_bytes,len(retained),sorted retained,
len(packets),sorted packet IDs). До10 candidates,16 packets,8 blobs,4 zones,
20 snapshots. unique ASCII IDs; object contents различны; keys могут циклически
зависеть от содержимого. До9 раундов recovery; output<2MB.

Если невозможно: {feasible:false}. Иначе {feasible:true,utility,retained,packets,
upload_bytes,stored_bytes,recovery,snapshots,logical_bytes}. Все ID arrays sorted.
recovery по lost_zone: {lost_zone,stages,recoverable}; stages в порядке раундов,
каждый {unlocked:[IDs новых packets],recovered:[labels новых blobs]}, initial_keys
не включать в recovered. Раунд с обоими пустыми списками НЕ записывать.
recoverable включает initial_keys и ВСЕ восстановленные labels, sorted.
snapshots sorted id, каждый {id,files,tree_sha256}; files sorted path, каждый
{path,size,sha256}. size UTF8 bytes. tree_sha256=SHA256 concatenation по files
path+NUL+decimal size+NUL+content SHA256 hex+LF, UTF8; lowercase hex.
logical_bytes — сумма размеров всех file instances retained snapshots.
'''
    files = {f'objects/{label}.txt': value for label, value in data['objects'].items()}
    files['backup/object-index.json'] = json.dumps({label: f'objects/{label}.txt' for label in data['objects']})
    files['backup/snapshots.json'] = json.dumps(data['snapshots'])
    files['backup/recovery-policy.json'] = json.dumps({k: v for k, v in data.items() if k not in ('objects', 'snapshots')}, indent=2)
    task = artifact_task(number=500, slug=old.id.split('_', 2)[2], name=old.name,
                         parent=366, prompt=prompt, files=files, expected=solve500(data),
                         tags=('workflow', 'multi-file', 'hardening-v4', 'hardening-v5', 'hardening-v6'))
    return [task if t.id == old.id else t for t in tasks], data
