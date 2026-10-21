import subprocess

from harness_bench.runner_cli import _infrastructure_error


def test_killed_cli_is_infrastructure():
    for code, stderr in [(-9, ''), (137, 'HBF_CLI_PROCESS_SIGNAL:9\n')]:
        result = subprocess.CompletedProcess(['goose'], code, '', stderr)
        assert _infrastructure_error(result) == 'CLI process killed by SIGKILL'


def test_tool_exit_or_quoted_signal_is_not_infrastructure():
    for code, stdout, stderr in [(137, '', ''), (1, 'HBF_CLI_PROCESS_SIGNAL:9', ''),
                                (0, '', 'HBF_CLI_PROCESS_SIGNAL:9')]:
        result = subprocess.CompletedProcess(['goose'], code, stdout, stderr)
        assert _infrastructure_error(result) is None
