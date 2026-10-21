import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('follow_logs', Path(__file__).parents[1] / 'scripts/hbf_follow_logs.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def follower(root):
    return module.Follower(root, root / 'all-harnesses.log', root / 'state.json')


def test_new_harness_attaches_without_restart_and_offsets_survive_restart(tmp_path):
    first = tmp_path / 'dsh-bwrap-pass3.log'
    first.write_text('one\n')
    f = follower(tmp_path)
    f.pump()
    later = tmp_path / 'gc-ua-bwrap-pass3.log'
    later.write_text('two\n')
    f.pump()
    follower(tmp_path).pump()
    assert (tmp_path / 'all-harnesses.log').read_text() == '[dsh] one\n[gc-ua] two\n'


def test_partial_line_and_rotation(tmp_path):
    source = tmp_path / 'dsh-bwrap-pass3.log'
    source.write_text('partial')
    f = follower(tmp_path)
    f.pump()
    assert not (tmp_path / 'all-harnesses.log').read_text()
    with source.open('a') as stream:
        stream.write(' done\n')
    f.pump()
    source.rename(tmp_path / 'old.log')
    source.write_text('new\n')
    f.pump()
    source.write_text('x\n')
    f.pump()
    assert (tmp_path / 'all-harnesses.log').read_text() == '[dsh] partial done\n[dsh] new\n[dsh] x\n'


def test_noisy_stream_is_bounded_and_other_harness_is_not_starved(tmp_path):
    (tmp_path / 'a-bwrap-pass3.log').write_bytes(b'x' * (module.CHUNK * 10) + b'\n')
    (tmp_path / 'z-bwrap-pass3.log').write_text('small\n')
    f = module.Follower(tmp_path, tmp_path / 'all-harnesses.log', tmp_path / 'state.json', initial_tail=10**9)
    f.pump()
    output = (tmp_path / 'all-harnesses.log').read_text()
    assert '[z] small\n' in output
    assert len(output) < module.CHUNK * 5
