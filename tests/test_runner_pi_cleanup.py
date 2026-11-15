#!/usr/bin/env python3
"""Regression tests for run_pi_bench.py timeout handling.

Covers the two infra defects found in the v0.21.3 smoke test
(see diagnosing-bugs analysis, 2026-11-04):

  Fix A — a client-side subprocess timeout must NOT leave an orphan
          container: the runner has to `docker kill`/`rm -f` by name.
  Fix B — metrics must be recovered from the partial NDJSON stream even
          when the run is killed before the final `agent_end` event.

Run: python3 -m pytest tests/test_runner_pi_cleanup.py -q
 or: python3 tests/test_runner_pi_cleanup.py
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_pi_bench as rpb  # noqa: E402


class FakeTask:
    id = "task_fake_timeout"
    prompt = "do the thing"

    def setup(self, workspace):  # noqa: D401
        Path(workspace, "input.txt").write_text("x")

    def verify(self, workspace):
        return mock.Mock(passed=True, message="ok")


class _FakeProc:
    """Popen stand-in: writes partial NDJSON, then reports a timeout."""

    def __init__(self, cmd, stdout=None, **kw):
        self.cmd = cmd
        self._out = stdout
        self.returncode = None
        self._timed_out_once = False

    def communicate(self, timeout=None):
        if not self._timed_out_once:
            self._timed_out_once = True
            # partial stream: usage present, no agent_end yet
            self._out.write(
                '{"type":"message","role":"assistant",'
                '"usage":{"input":10,"output":5,"reasoning":2,"totalTokens":15}}\n'
            )
            self._out.flush()
            raise subprocess.TimeoutExpired(cmd="docker", timeout=timeout)
        self.returncode = 137  # SIGKILL, like `docker kill`
        return ("", "")

    def kill(self):
        self.returncode = -9


def _acc():
    return {"input": 0, "output": 0, "reasoning": 0, "total": 0,
            "cache_read": 0, "cache_write": 0,
            "cost": 0.0, "api_calls": 0, "tool_calls": 0}


class TestExtractUsage(unittest.TestCase):
    def test_counts_message_end_once_with_cache_and_tool_calls(self):
        acc = _acc()
        obj = json.loads(
            '{"type":"message_end","message":{"role":"assistant","responseId":"r1",'
            '"usage":{"input":7,"output":3,"reasoning":1,"cacheRead":70,"cacheWrite":2,'
            '"totalTokens":82,"cost":{"total":0.5}},'
            '"content":[{"type":"toolCall"},{"type":"text"}]}}'
        )
        rpb._extract_usage(obj, acc)
        self.assertEqual(acc["input"], 7)
        self.assertEqual(acc["output"], 3)
        self.assertEqual(acc["reasoning"], 1)
        self.assertEqual(acc["cache_read"], 70)
        self.assertEqual(acc["cache_write"], 2)
        self.assertEqual(acc["total"], 82)
        self.assertEqual(acc["cost"], 0.5)
        self.assertEqual(acc["api_calls"], 1)
        self.assertEqual(acc["tool_calls"], 1)

    def test_transcript_echoes_are_not_double_counted(self):
        """The same usage repeats in turn_end and agent_end.messages[] -> count once."""
        acc = _acc()
        msg = ('{"role":"assistant","responseId":"r1",'
               '"usage":{"input":7,"output":3,"totalTokens":10},'
               '"content":[{"type":"toolCall"}]}')
        rpb._extract_usage(json.loads('{"type":"message_end","message":' + msg + '}'), acc)
        rpb._extract_usage(json.loads('{"type":"turn_end","message":' + msg + '}'), acc)
        rpb._extract_usage(json.loads('{"type":"agent_end","messages":[' + msg + ']}'), acc)
        self.assertEqual(acc["api_calls"], 1, "usage echoed in turn_end/agent_end must not inflate")
        self.assertEqual(acc["input"], 7)
        self.assertEqual(acc["output"], 3)
        self.assertEqual(acc["tool_calls"], 1)

    def test_message_update_deltas_do_not_inflate_steps(self):
        acc = _acc()
        # many streaming deltas carrying the same usage
        for _ in range(50):
            rpb._extract_usage(
                json.loads('{"type":"message_update","usage":{"input":1,"output":1,"totalTokens":2}}'),
                acc,
            )
        self.assertEqual(acc["api_calls"], 0)
        self.assertEqual(acc["total"], 0)
        # a real completed message still counts
        rpb._extract_usage(
            json.loads('{"type":"message_end","usage":{"input":1,"output":1,"totalTokens":2}}'),
            acc,
        )
        self.assertEqual(acc["api_calls"], 1)


class TestDockerKill(unittest.TestCase):
    def test_issues_kill_and_rm(self):
        with mock.patch("run_pi_bench.subprocess.run") as m:
            rpb._docker_kill("pi-bench-x")
        calls = [c.args[0] for c in m.call_args_list]
        self.assertIn(["docker", "kill", "pi-bench-x"], calls)
        self.assertIn(["docker", "rm", "-f", "pi-bench-x"], calls)

    def test_swallows_errors(self):
        with mock.patch("run_pi_bench.subprocess.run", side_effect=RuntimeError("boom")):
            rpb._docker_kill("pi-bench-x")  # must not raise


class TestTimeoutCleanupAndMetrics(unittest.TestCase):
    """End-to-end style: fake Popen times out -> kill called + metrics parsed."""

    def test_timeout_kills_container_and_keeps_metrics(self):
        killed = []

        with mock.patch("run_pi_bench.subprocess.Popen", _FakeProc), \
             mock.patch("run_pi_bench._docker_kill", side_effect=lambda n: killed.append(n)):
            res = rpb.run_one_task(FakeTask(), timeout=1)

        # Fix A: container was killed by name
        self.assertTrue(killed, "container must be killed on timeout")
        self.assertTrue(killed[0].startswith("pi-bench-task_fake_timeout"))
        self.assertIn("timed out", res["error"])

        # Fix B: partial metrics survived the kill
        self.assertEqual(res["agent_input_tokens"], 10)
        self.assertEqual(res["agent_output_tokens"], 5)
        self.assertEqual(res["agent_total_tokens"], 15)
        self.assertGreater(res["agent_steps"], 0)

    def test_docker_cmd_has_name_flag(self):
        captured = {}

        class _CapProc(_FakeProc):
            def __init__(self, cmd, stdout=None, **kw):
                captured["cmd"] = cmd
                super().__init__(cmd, stdout, **kw)

        with mock.patch("run_pi_bench.subprocess.Popen", _CapProc), \
             mock.patch("run_pi_bench._docker_kill"):
            rpb.run_one_task(FakeTask(), timeout=1)

        self.assertIn("--name", captured["cmd"])
        self.assertIn("--rm", captured["cmd"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
