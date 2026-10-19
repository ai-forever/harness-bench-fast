#!/usr/bin/env python3
"""Restartable Linux supervisor for bounded, overlapping harness tails."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

PROGRESS = re.compile(rb'\[\s*(\d+)\s*/\s*(\d+)\]\s+\[(?:PASS|FAIL)\]')


def pid_identity(pid):
    try:
        raw = Path(f'/proc/{pid}/stat').read_text()
        fields = raw[raw.rfind(')') + 2:].split()
        return None if fields[0] == 'Z' else fields[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def progress(log, offset, expected):
    """Only inspect the current launch; bounded tail read, never old counters."""
    path = Path(log)
    if not path.exists() or path.stat().st_size <= offset:
        return None
    with path.open('rb') as stream:
        stream.seek(max(offset, path.stat().st_size - 128 * 1024))
        data = stream.read(128 * 1024)
    found = [int(m[1]) for m in PROGRESS.finditer(data) if int(m[2]) == expected]
    return found[-1] if found and 0 <= found[-1] <= expected else None


def reservation(run, completed, expected, tail):
    if completed is None or expected - completed > tail:
        return run['concurrency']
    # Keep one slot until process exit: flushing/verifying is still work.
    return min(run['concurrency'], max(1, expected - completed))


def checkpoint_complete(path, expected):
    try:
        data = json.loads(Path(path).read_text())
        rows = data if isinstance(data, list) else data.get('tasks', [])
        if len(rows) != expected:
            return False
        identities = {(row['task_id'], row['attempt']) for row in rows}
        if len(identities) != expected:
            return False
        return all(row.get('failure_kind') != 'infrastructure' and
                   (not row.get('error') or row.get('failure_kind') == 'timeout'
                    or 'timed out' in str(row['error']).lower()) for row in rows)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def save(path, state):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as stream:
        json.dump(state, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def result_mtime(path):
    try:
        return Path(path).stat().st_mtime_ns
    except FileNotFoundError:
        return None


def reconcile(state, manifest):
    """Reattach live processes; ambiguous launches and bad exits fail closed."""
    changed = False
    for item in manifest:
        run = state['runs'].get(item['name'])
        if not run or run['status'] not in ('running', 'launching'):
            continue
        if run['status'] == 'launching':
            state['blocked'] = f"Ambiguous launch of {item['name']}; inspect processes before recovery"
            continue
        if pid_identity(run['pid']) == run['start_ticks']:
            continue
        # Benchmark exit 1 can mean normal model failures. Check artifacts, not rc.
        fresh = ('result_mtime_ns' not in run or
                 result_mtime(item['result']) != run['result_mtime_ns'])
        run['status'] = 'complete' if fresh and checkpoint_complete(item['result'], item['expected_attempts']) else 'failed'
        if run['status'] == 'failed':
            state['blocked'] = f"{item['name']} exited with incomplete/infrastructure results"
        changed = True
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--adopt', help='name,pid,concurrency,log_offset (first startup only)')
    parser.add_argument('--capacity', type=int, default=128)
    parser.add_argument('--tail', type=int, default=16)
    parser.add_argument('--min-slots', type=int, default=16)
    parser.add_argument('--max-active', type=int, default=3)
    parser.add_argument('--min-available-gib', type=float, default=8)
    parser.add_argument('--poll', type=float, default=5)
    args = parser.parse_args()
    if min(args.capacity, args.min_slots, args.max_active) < 1 or args.tail < 0:
        parser.error('invalid capacity/tail settings')
    raw = args.manifest.read_bytes()
    manifest = json.loads(raw)
    names = [item['name'] for item in manifest]
    if len(set(names)) != len(names):
        parser.error('duplicate manifest names')
    for item in manifest:
        if not isinstance(item.get('expected_attempts'), int) or item['expected_attempts'] < 1:
            parser.error('expected_attempts must be a positive integer')
        if item.get('max_concurrency', args.capacity) < 1:
            parser.error('max_concurrency must be positive')
        if not item.get('command') or not isinstance(item['command'], list):
            parser.error('command must be a nonempty argv list')
    digest = hashlib.sha256(raw).hexdigest()
    args.state.parent.mkdir(parents=True, exist_ok=True)
    lock = args.state.with_suffix('.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = json.loads(args.state.read_text()) if args.state.exists() else {'manifest_sha256': digest, 'runs': {}}
    if state['manifest_sha256'] != digest:
        parser.error('manifest differs from persisted state')
    if args.adopt and not state['runs']:
        name, pid, concurrency, offset = args.adopt.split(',')
        if name not in names or int(concurrency) > args.capacity:
            parser.error('invalid adopted harness or concurrency')
        start = pid_identity(int(pid))
        if start is None:
            parser.error('adopted process is not alive')
        state['runs'][name] = {'status': 'running', 'pid': int(pid), 'start_ticks': start,
                              'concurrency': int(concurrency), 'log_offset': int(offset), 'adopted': True}
    save(args.state, state)
    children = {}
    while True:
        for name, proc in list(children.items()):
            if proc.poll() is not None:
                state['runs'][name]['exit_code'] = proc.returncode
                del children[name]
        reconcile(state, manifest)
        save(args.state, state)
        active = [item for item in manifest if state['runs'].get(item['name'], {}).get('status') == 'running']
        pending = [item for item in manifest if item['name'] not in state['runs']]
        if not active and (not pending or state.get('blocked')):
            print(json.dumps({'event': 'finished', 'blocked': state.get('blocked')}), flush=True)
            return 1 if state.get('blocked') else 0
        reserved = sum(reservation(state['runs'][item['name']],
                       progress(item['log'], state['runs'][item['name']]['log_offset'], item['expected_attempts']),
                       item['expected_attempts'], args.tail) for item in active)
        slots = args.capacity - reserved
        available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:')) / 1024**2
        if pending and not state.get('blocked') and len(active) < args.max_active and slots >= args.min_slots and available >= args.min_available_gib:
            item = pending[0]
            slots = min(slots, item.get('max_concurrency', args.capacity), item['expected_attempts'])
            Path(item['log']).parent.mkdir(parents=True, exist_ok=True)
            Path(item['result']).parent.mkdir(parents=True, exist_ok=True)
            with Path(item['log']).open('ab') as log:
                run = {'status': 'launching', 'concurrency': slots, 'log_offset': log.tell(),
                       'result_mtime_ns': result_mtime(item['result'])}
                state['runs'][item['name']] = run
                save(args.state, state)  # Crash here requires manual inspection, never duplicate spawn.
                command = [part.replace('{concurrency}', str(slots)).replace('{result}', item['result']) for part in item['command']]
                proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
                children[item['name']] = proc
                start = pid_identity(proc.pid)
                # Immediate exit is reconciled against checkpoint next iteration.
                run.update(status='running', pid=proc.pid, start_ticks=start or 'exited')
                save(args.state, state)
                print(json.dumps({'event': 'launch', 'name': item['name'], 'pid': proc.pid, 'concurrency': slots}), flush=True)
        time.sleep(args.poll)


if __name__ == '__main__':
    raise SystemExit(main())
