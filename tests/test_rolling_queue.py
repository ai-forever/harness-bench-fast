import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('hbf_rolling_queue', Path(__file__).parents[1] / 'scripts/hbf_rolling_queue.py')
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)


def test_tail_reservations_preserve_global_capacity():
    old = {'concurrency': 128}
    assert queue.reservation(old, None, 1293, 16) == 128
    assert queue.reservation(old, 1200, 1293, 16) == 128
    assert queue.reservation(old, 1292, 1293, 16) == 1
    assert queue.reservation(old, 1293, 1293, 16) == 1
    assert 128 - queue.reservation(old, 1292, 1293, 16) == 127


def test_progress_ignores_previous_launch(tmp_path):
    log = tmp_path / 'run.log'
    log.write_text('[1293/1293] [PASS] old\n')
    offset = log.stat().st_size
    assert queue.progress(log, offset, 1293) is None
    with log.open('a') as stream:
        stream.write('[ 17/1293] [FAIL] current\n[4/7] [PASS] nested\n')
    assert queue.progress(log, offset, 1293) == 17


def test_checkpoint_validation(tmp_path):
    path = tmp_path / 'result.json'
    rows = [{'task_id': 'task1', 'attempt': i, 'passed': False, 'error': None} for i in (1, 2, 3)]
    path.write_text(json.dumps({'tasks': rows}))
    assert queue.checkpoint_complete(path, 3)
    rows[0].update(error='CLI timed out after 600s', failure_kind='timeout')
    path.write_text(json.dumps({'tasks': rows}))
    assert queue.checkpoint_complete(path, 3)
    rows[1]['error'] = 'Connection reset'
    path.write_text(json.dumps({'tasks': rows}))
    assert not queue.checkpoint_complete(path, 3)
    assert not queue.checkpoint_complete(path, 4)
    rows[1] = rows[2]
    path.write_text(json.dumps({'tasks': rows}))
    assert not queue.checkpoint_complete(path, 3)


def test_restart_retains_adopted_live_process(monkeypatch):
    state = {'runs': {'deepagents': {'status': 'running', 'pid': 100, 'start_ticks': '123', 'adopted': True}}}
    monkeypatch.setattr(queue, 'pid_identity', lambda pid: '123')
    assert not queue.reconcile(state, [{'name': 'deepagents'}])
    assert state['runs']['deepagents']['status'] == 'running'
    assert not state.get('blocked')


def test_pid_reuse_is_not_adopted(monkeypatch, tmp_path):
    state = {'runs': {'deepagents': {'status': 'running', 'pid': 100, 'start_ticks': '123'}}}
    monkeypatch.setattr(queue, 'pid_identity', lambda pid: '456')
    assert queue.reconcile(state, [{'name': 'deepagents', 'result': str(tmp_path / 'missing'), 'expected_attempts': 3}])
    assert state['runs']['deepagents']['status'] == 'failed'
    assert state.get('blocked')


def test_ambiguous_launch_blocks_new_work():
    state = {'runs': {'deepagents': {'status': 'launching'}}}
    queue.reconcile(state, [{'name': 'deepagents'}])
    assert 'Ambiguous' in state['blocked']


def test_opt_in_failure_is_retained_without_blocking_successors(monkeypatch, tmp_path):
    state = {'runs': {'failed': {'status': 'running', 'pid': 100, 'start_ticks': '123'}}}
    monkeypatch.setattr(queue, 'pid_identity', lambda pid: None)
    queue.reconcile(state, [{'name': 'failed', 'result': str(tmp_path/'missing'),
                             'expected_attempts': 3}], continue_on_failure=True)
    assert state['runs']['failed']['status'] == 'failed'
    assert state['runs']['failed']['error']
    assert not state.get('blocked')


def test_continue_mode_still_blocks_ambiguous_launch():
    state = {'runs': {'unknown': {'status': 'launching'}}}
    queue.reconcile(state, [{'name': 'unknown'}], continue_on_failure=True)
    assert 'Ambiguous' in state['blocked']


def test_failed_job_does_not_prevent_next_job(tmp_path):
    import subprocess
    import sys
    import pytest
    if sys.platform != 'linux':
        pytest.skip('queue uses Linux /proc')
    manifest = []
    for name in ('broken', 'next'):
        result = tmp_path / (name + '.json')
        code = ('raise SystemExit(2)' if name == 'broken' else
                f'import json; open({str(result)!r}, "w").write(json.dumps({{"tasks": '
                '[{"task_id":"smoke", "attempt":1, "passed":True, "error":None}]}))')
        manifest.append({'name':name, 'expected_attempts':1, 'command':[sys.executable,'-c',code],
                         'result':str(result), 'log':str(tmp_path/(name+'.log'))})
    path = tmp_path/'manifest.json'; path.write_text(json.dumps(manifest))
    state = tmp_path/'state.json'
    result = subprocess.run([sys.executable, queue.__file__, '--manifest', str(path),
                             '--state', str(state), '--capacity','1', '--min-slots','1',
                             '--max-active','1', '--min-available-gib','0', '--poll','.01',
                             '--continue-on-run-failure'], capture_output=True, timeout=10)
    assert result.returncode == 1  # The first failure remains visible.
    runs = json.loads(state.read_text())['runs']
    assert runs['broken']['status'] == 'failed'
    assert runs['next']['status'] == 'complete'


def test_atomic_state_save(tmp_path):
    path = tmp_path / 'state.json'
    queue.save(path, {'runs': {}})
    assert json.loads(path.read_text()) == {'runs': {}}
    assert not path.with_name('state.json.tmp').exists()


def test_normal_model_failures_exit_one_still_complete(monkeypatch, tmp_path):
    result = tmp_path / 'result.json'
    result.write_text(json.dumps({'tasks': [{'task_id': 'task1', 'attempt': 1, 'passed': False, 'error': None}]}))
    state = {'runs': {'harness': {'status': 'running', 'pid': 100, 'start_ticks': '123', 'exit_code': 1, 'result_mtime_ns': None}}}
    monkeypatch.setattr(queue, 'pid_identity', lambda pid: None)
    assert queue.reconcile(state, [{'name': 'harness', 'result': str(result), 'expected_attempts': 1}])
    assert state['runs']['harness']['status'] == 'complete'
    assert not state.get('blocked')


def test_infrastructure_exit_does_not_cancel_other_active_work(monkeypatch, tmp_path):
    state = {'runs': {'failed': {'status': 'running', 'pid': 100, 'start_ticks': '123'},
                      'active': {'status': 'running', 'pid': 200, 'start_ticks': '456'}}}
    monkeypatch.setattr(queue, 'pid_identity', lambda pid: '456' if pid == 200 else None)
    queue.reconcile(state, [{'name': 'failed', 'result': str(tmp_path / 'missing'), 'expected_attempts': 1},
                            {'name': 'active'}])
    assert state['blocked']
    assert state['runs']['active']['status'] == 'running'


def test_stale_complete_checkpoint_is_not_accepted(monkeypatch, tmp_path):
    result = tmp_path / 'result.json'
    result.write_text(json.dumps({'tasks': [{'task_id': 'task1', 'attempt': 1, 'passed': True, 'error': None}]}))
    state = {'runs': {'harness': {'status': 'running', 'pid': 100, 'start_ticks': '123',
                                  'result_mtime_ns': result.stat().st_mtime_ns}}}
    monkeypatch.setattr(queue, 'pid_identity', lambda pid: None)
    queue.reconcile(state, [{'name': 'harness', 'result': str(result), 'expected_attempts': 1}])
    assert state['runs']['harness']['status'] == 'failed'
    assert state['blocked']


def test_live_controls_preserve_defaults_and_reject_invalid(tmp_path):
    import importlib.util
    import json
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('queue_controls', Path(__file__).parents[1] / 'scripts/hbf_rolling_queue.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    defaults = {'capacity': 128, 'max_active': 3, 'per_harness_cap': 64}
    path = tmp_path / 'controls.json'
    path.write_text(json.dumps({'capacity': 192, 'max_active': 4}))
    assert module.read_controls(path, defaults) == {'capacity': 192, 'max_active': 4, 'per_harness_cap': 64}
    path.write_text('{"capacity": 0}')
    import pytest
    with pytest.raises(ValueError):
        module.read_controls(path, defaults)
