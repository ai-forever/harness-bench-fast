import subprocess
from pathlib import Path

from harness_bench.runner_cli import _argv_for_workspace, _mini_swe_agent_traj_stats


def test_opencode_command_gets_explicit_workspace_dir() -> None:
    argv = _argv_for_workspace(["opencode", "run", "--model", "x/y"], Path("/tmp/ws"))

    assert argv == ["opencode", "run", "--model", "x/y", "--dir", str(Path("/tmp/ws"))]


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


def test_mini_swe_agent_traj_stats_count_steps_and_tokens(tmp_path: Path) -> None:
    (tmp_path / "mini-swe-agent.traj.json").write_text(
        """
        {
          "info": {"model_stats": {"api_calls": 2}},
          "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
            {
              "role": "assistant",
              "content": "inspect",
              "extra": {
                "actions": [{"command": "ls -la", "tool_call_id": "a"}],
                "response": {
                  "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 3,
                    "total_tokens": 13
                  }
                }
              }
            },
            {"role": "tool", "content": "ok"},
            {
              "role": "assistant",
              "content": "finish",
              "extra": {
                "actions": [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}],
                "response": {
                  "usage": {
                    "input_tokens": 20,
                    "output_tokens": 5,
                    "total_tokens": 25
                  }
                }
              }
            },
            {"role": "exit", "content": ""}
          ]
        }
        """,
        encoding="utf-8",
    )

    assert _mini_swe_agent_traj_stats(tmp_path) == {
        "agent_events": 6,
        "agent_llm_calls": 2,
        "agent_input_tokens": 30,
        "agent_output_tokens": 8,
        "agent_total_tokens": 38,
        "agent_steps": 2,
        "agent_tool_calls": 2,
        "agent_shell_commands": 2,
    }


def test_task_run_stats_can_read_mini_traj_without_kept_workspace(tmp_path: Path) -> None:
    from harness_bench.runner_cli import _task_run_with_cli_stats

    (tmp_path / "mini-swe-agent.traj.json").write_text(
        """
        {
          "info": {"model_stats": {"api_calls": 1}},
          "messages": [
            {"role": "user", "content": "task"},
            {
              "role": "assistant",
              "content": "write",
              "extra": {
                "actions": [{"command": "printf ok"}],
                "response": {
                  "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 2,
                    "total_tokens": 9
                  }
                }
              }
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    run = _task_run_with_cli_stats(
        task_id="task_fake",
        passed=True,
        message="ok",
        elapsed_seconds=0.1,
        result=subprocess.CompletedProcess(["mini"], 0, "", ""),
        workspace=None,
        stats_workspace=tmp_path,
    )

    assert run.workspace is None
    assert run.agent_steps == 1
    assert run.agent_shell_commands == 1
    assert run.agent_llm_calls == 1
    assert run.agent_total_tokens == 9
