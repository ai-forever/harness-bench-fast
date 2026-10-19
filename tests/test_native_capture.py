import json
import subprocess
import sys
import tracemalloc

import pytest

from harness_bench.cli_artifacts import record_execution
from harness_bench.native_capture import diagnostic_tail, terminal_output, DIAGNOSTIC_BYTES
from harness_bench.runner_cli import _run_cli_subprocess


EVENT = {"type": "hbf_deepagents_result", "stats": {"agent_total_tokens": 123}}


def test_large_single_line_evidence_is_spooled_without_parent_heap_growth(tmp_path):
    code = '''import sys
for _ in range(64):
    sys.stdout.write('x' * (1024 * 1024))
sys.stdout.write('\\n' + %r + '\\n')
sys.stderr.write('e' * (2 * 1024 * 1024))
''' % json.dumps(EVENT)
    tracemalloc.start()
    try:
        result = _run_cli_subprocess([sys.executable, '-c', code], cwd=tmp_path,
                                    timeout=30, env=None, capture_directory=tmp_path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert json.loads(result.stdout) == EVENT
    assert len(result.stderr) == DIAGNOSTIC_BYTES
    assert (tmp_path / 'stdout.log').stat().st_size > 64 * 1024**2
    assert peak < 4 * 1024**2
    record_execution(tmp_path, stdout=result.stdout, stderr=result.stderr,
                     metadata={}, trace_roots=(), preserve_output_files=True)
    assert (tmp_path / 'stdout.log').stat().st_size > 64 * 1024**2


def test_duplicate_terminal_events_remain_detectable(tmp_path):
    path = tmp_path / 'stdout.log'
    path.write_text('\n'.join([json.dumps(EVENT)] * 3))
    assert len(terminal_output(path).splitlines()) == 2


def test_fake_terminal_in_model_content_is_not_a_terminal(tmp_path):
    path = tmp_path / 'stdout.log'
    path.write_text(json.dumps({'type': 'hbf_model_input', 'text': 'hbf_deepagents_result'})+'\n'+json.dumps(EVENT))
    assert json.loads(terminal_output(path)) == EVENT


def test_timeout_retains_full_disk_evidence(tmp_path):
    code = "import sys,time; print('before timeout', flush=True); time.sleep(30)"
    with pytest.raises(subprocess.TimeoutExpired):
        _run_cli_subprocess([sys.executable, '-c', code], cwd=tmp_path,
                            timeout=0.2, env=None, capture_directory=tmp_path)
    assert (tmp_path / 'stdout.log').read_text() == 'before timeout\n'


def test_native_runner_retains_evidence_and_metrics(tmp_path, monkeypatch):
    import shlex
    from harness_bench import runner_cli
    from harness_bench.core import Task, VerifyResult

    monkeypatch.setattr(runner_cli, '_load_env_from_dotenv', lambda: None)
    script = tmp_path / 'worker.py'
    script.write_text('print("x" * (8 * 1024 * 1024))\nprint(' + repr(json.dumps(EVENT)) + ')\n')
    task = Task('unit_native_capture', 'fixture', 'prompt', lambda ws: VerifyResult(True, 'ok'))
    result = runner_cli.run_task_cli(task,
        cli_command=shlex.join([sys.executable, str(script)]), isolation='none',
        extra_env={'HBF_WORKER_API_KEY': 'offline'}, artifacts_root=tmp_path / 'artifacts')
    assert result.passed
    assert result.agent_total_tokens == 123
    log = next((tmp_path / 'artifacts').glob('*/stdout.log'))
    assert log.stat().st_size > 8 * 1024**2
    assert json.loads(terminal_output(log)) == EVENT


def test_oversized_terminal_cannot_expand_parent_heap(tmp_path):
    from harness_bench.native_capture import MAX_TERMINAL_BYTES
    path = tmp_path / 'stdout.log'
    path.write_text(json.dumps({**EVENT, 'message': 'x' * (MAX_TERMINAL_BYTES * 4)}) + '\n')
    assert terminal_output(path) == ''  # Runner rejects missing terminal as infrastructure.


def test_all_cli_capture_is_disk_backed_with_streamed_metrics(tmp_path):
    from harness_bench.runner_cli import _codex_json_event_stats, _claude_json_event_stats
    from harness_bench.native_capture import DiskOutput
    code = '''import json,sys
for _ in range(12000):
 print(json.dumps({"type":"item.completed","item":{"type":"command_execution","aggregated_output":"x"*4096}}))
print(json.dumps({"type":"turn.completed","usage":{"input_tokens":123,"output_tokens":456}}))
'''
    tracemalloc.start()
    try:
        result = _run_cli_subprocess([sys.executable, '-c', code], cwd=tmp_path,
                                    timeout=30, env=None, capture_directory=tmp_path)
        assert isinstance(result.stdout, DiskOutput)
        stats = _codex_json_event_stats(result.stdout)
        assert stats['agent_steps'] == 12000
        assert stats['agent_total_tokens'] == 579
        # A second parser must not construct a list of all payloads either.
        _claude_json_event_stats(result.stdout)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024**2
    assert (tmp_path / 'stdout.log').stat().st_size > 47 * 1024**2
    assert len(result.stdout) <= DIAGNOSTIC_BYTES


def test_huge_json_content_preserves_metrics_and_following_events(tmp_path):
    from harness_bench.native_capture import DiskOutput, output_lines
    from harness_bench.runner_cli import _claude_json_event_stats
    p = tmp_path / 'stdout.log'
    event = {'type':'assistant','message':{'content':[{'type':'text','text':'a\\b"ю'*500000},
            {'type':'tool_use','name':'Bash'}], 'usage':{'input_tokens':23,'output_tokens':45}}}
    p.write_text(json.dumps(event)+'\n'+json.dumps({'type':'result','num_turns':1})+'\n')
    stats = _claude_json_event_stats(DiskOutput(p))
    assert stats['agent_total_tokens'] == 68
    assert stats['agent_tool_calls'] == 1
    assert stats['agent_events'] == 2
    assert len(list(output_lines(DiskOutput(p)))) == 2


def test_non_native_timeout_retains_bounded_diagnostics_and_full_files(tmp_path):
    code = "import sys,time; print('x'*8000000,flush=True); print('timeout-marker',flush=True); time.sleep(10)"
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        _run_cli_subprocess([sys.executable,'-c',code],cwd=tmp_path,timeout=.5,env=None,capture_directory=tmp_path)
    assert 'timeout-marker' in exc.value.stdout
    assert len(exc.value.stdout) <= DIAGNOSTIC_BYTES
    assert (tmp_path/'stdout.log').stat().st_size > 8000000


def test_pretty_trajectory_projection_preserves_usage(tmp_path):
    from harness_bench.native_capture import metric_document
    from harness_bench.runner_cli import _mini_swe_agent_traj_stats
    path = tmp_path / 'mini-swe-agent.traj.json'
    path.write_text(json.dumps({'info':{'model_stats':{'api_calls':2}},'messages':[
        {'role':'assistant','content':'x'*8000000,'extra':{'actions':[{'command':'echo hi'}],
         'response':{'usage':{'input_tokens':12,'output_tokens':34}}}}]},indent=2))
    assert len(metric_document(path)['messages'][0]['content']) == 4096
    stats = _mini_swe_agent_traj_stats(tmp_path)
    assert stats['agent_total_tokens'] == 46
    assert stats['agent_steps'] == 1
    assert stats['agent_llm_calls'] == 2
