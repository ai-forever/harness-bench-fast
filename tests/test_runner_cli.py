from pathlib import Path

from harness_bench.runner_cli import _argv_for_workspace


def test_opencode_command_gets_explicit_workspace_dir() -> None:
    argv = _argv_for_workspace(["opencode", "run", "--model", "x/y"], Path("/tmp/ws"))

    assert argv == ["opencode", "run", "--model", "x/y", "--dir", "/tmp/ws"]


def test_opencode_command_keeps_user_supplied_dir() -> None:
    argv = _argv_for_workspace(
        ["opencode", "run", "--dir", "/custom/ws"],
        Path("/tmp/ws"),
    )

    assert argv == ["opencode", "run", "--dir", "/custom/ws"]


def test_non_opencode_command_does_not_get_dir() -> None:
    argv = _argv_for_workspace(["free-code", "-p"], Path("/tmp/ws"))

    assert argv == ["free-code", "-p"]


def test_workspace_argv_helper_does_not_mutate_input() -> None:
    original = ["free-code", "-p"]
    argv = _argv_for_workspace(original, Path("/tmp/ws"))
    argv.append("prompt")

    assert original == ["free-code", "-p"]
