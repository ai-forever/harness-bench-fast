import itertools
import random

from harness_bench.general_hard.workflow_revision_v6 import recovery


def oracle(data, selected):
    labels = sorted(data['objects'])
    packets = sorted(data['packets'], key=lambda p: p['id'])
    known, active = set(data['initial_keys']), set()
    while True:
        active |= {i for i in selected if set(packets[i]['keys']) <= known}
        indices = sorted(active)
        noisy = [j for j, i in enumerate(indices) if not packets[i]['trusted']]
        groups = {}
        for world in range(1 << len(labels)):
            if any(world & (1 << labels.index(x)) for x in known):
                continue
            observation = sum((sum(bool(world & (1 << labels.index(x))) for x in packets[i]['xor']) % 2) << j
                              for j, i in enumerate(indices))
            for count in range(data['faults'] + 1):
                for corrupt in itertools.combinations(noisy, count):
                    received = observation ^ sum(1 << j for j in corrupt)
                    groups.setdefault(received, set()).add(world)
        new = {x for j, x in enumerate(labels)
               if all(len({bool(world & (1 << j)) for world in worlds}) == 1 for worlds in groups.values())}
        if new <= known:
            return sorted(known)
        known |= new


def test_recovery_against_worlds_and_actual_corruptions():
    rng = random.Random(500600)
    for _ in range(80):
        data = {'objects': dict.fromkeys('abc', ''), 'initial_keys': ['a'] if rng.randrange(2) else [],
                'faults': rng.randrange(2), 'packets': [
                    {'id': str(i), 'zone': 'live', 'xor': rng.sample(list('abc'), rng.randrange(1, 4)),
                     'trusted': bool(rng.randrange(2)), 'keys': rng.sample(list('abc'), rng.randrange(2))}
                    for i in range(5)]}
        assert recovery(data, range(5), 'lost')['recoverable'] == oracle(data, range(5))


def test_rank_and_self_support_mutants():
    row = {'id': 'p', 'zone': 'live', 'xor': ['a'], 'trusted': False, 'keys': []}
    data = {'objects': {'a': 'x'}, 'initial_keys': [], 'faults': 1, 'packets': [row]}
    assert recovery(data, [0], 'lost')['recoverable'] == []
    data['faults'] = 0
    assert recovery(data, [0], 'lost')['recoverable'] == ['a']
    row['trusted'], row['keys'] = True, ['a']
    assert recovery(data, [0], 'lost')['recoverable'] == []


def test_only_500_changed_and_active_lifecycle(tmp_path):
    from harness_bench.general_hard.workflow_tasks import _V5_TASKS, TASKS
    previous = {task.id: task for task in _V5_TASKS}
    for task in TASKS:
        if not task.id.startswith('task_500_'):
            assert task is previous[task.id]
        else:
            assert set(('hardening-v4', 'hardening-v5', 'hardening-v6')) <= set(task.tags)
            task.setup(tmp_path)
            assert not task.verify(tmp_path).passed
            task.apply_gold(tmp_path)
            assert task.verify(tmp_path).passed
