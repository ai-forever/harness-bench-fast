import builtins
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from harness_bench.long_tasks.t04_homework_grading import _runner, evaluate


def test_parallel_fixture_evaluation_preserves_runner_streams(monkeypatch):
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    monkeypatch.setattr(builtins, "_hbf_stdio_events", (entered, release), raising=False)
    streams = (sys.stdin, sys.stdout, sys.stderr)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = []
        try:
            for i in range(2):
                source = (
                    "import builtins\n"
                    f"builtins._hbf_stdio_events[0][{i}].set()\n"
                    f"builtins._hbf_stdio_events[1][{i}].wait(5)\n"
                    "print('fixture debug output')\n"
                )
                futures.append(pool.submit(evaluate, source))
                assert entered[i].wait(5)
            assert (sys.stdin, sys.stdout, sys.stderr) == streams
            release[0].set()
            futures[0].result(timeout=5)
            release[1].set()
            futures[1].result(timeout=5)
            assert (sys.stdin, sys.stdout, sys.stderr) == streams
        finally:
            for event in release:
                event.set()


def test_host_evaluation_matches_standalone_runner(capsys):
    sources = [
        "print('debug')\ndef digit_stats(n):\n return (0, 0, 0)\n",
        "n = int(input('n = '))\n",
        "raise ValueError('invalid submission')\n",
    ]
    runner = _runner()["evaluate_source"]
    for source in sources:
        assert runner(source, capture_stdio=False) == runner(source)
    assert capsys.readouterr() == ("", "")
