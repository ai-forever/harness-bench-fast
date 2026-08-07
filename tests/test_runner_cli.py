import subprocess
from pathlib import Path
from urllib.parse import quote

from harness_bench.runner_cli import (
    _argv_for_workspace,
    _claude_json_event_stats,
    _codex_json_event_stats,
    _ensure_cli_json_events,
    _gemini_json_event_stats,
    _grok_json_event_stats,
    _mini_swe_agent_traj_stats,
    _ouroboros_result_stats,
    _pi_session_stats,
    _task_run_with_cli_stats,
)


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


def test_cli_json_events_asks_claude_for_stream_json() -> None:
    argv = _ensure_cli_json_events(
        ["/opt/homebrew/bin/claude", "--model", "GigaChat-3.5", "-p"]
    )

    assert argv == [
        "/opt/homebrew/bin/claude",
        "--model",
        "GigaChat-3.5",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
    ]


def test_cli_json_events_normalizes_claude_unicode_verbose_dash() -> None:
    argv = _ensure_cli_json_events(
        ["/opt/homebrew/bin/claude", "--model", "GigaChat-3.5", "—verbose", "-p"]
    )

    assert argv == [
        "/opt/homebrew/bin/claude",
        "--model",
        "GigaChat-3.5",
        "--verbose",
        "-p",
        "--output-format",
        "stream-json",
    ]


def test_cli_json_events_rewrites_gemini_text_output_before_prompt_flag() -> None:
    argv = _ensure_cli_json_events(
        [
            "/opt/homebrew/bin/gemini",
            "-m",
            "GigaChat-3.5",
            "--output-format",
            "text",
            "-p",
        ]
    )

    assert argv == [
        "/opt/homebrew/bin/gemini",
        "-m",
        "GigaChat-3.5",
        "--output-format",
        "stream-json",
        "-p",
    ]


def test_cli_json_events_inserts_gemini_output_before_prompt_flag() -> None:
    argv = _ensure_cli_json_events(
        ["/opt/homebrew/bin/gemini", "-m", "GigaChat-3.5", "-p"]
    )

    assert argv == [
        "/opt/homebrew/bin/gemini",
        "-m",
        "GigaChat-3.5",
        "--output-format",
        "stream-json",
        "-p",
    ]


def test_cli_json_events_inserts_grok_output_before_prompt_flag() -> None:
    argv = _ensure_cli_json_events(["grok", "-m", "grok-4.5", "-p"])

    assert argv == [
        "grok",
        "-m",
        "grok-4.5",
        "--output-format",
        "streaming-json",
        "-p",
    ]


def test_cli_json_events_rewrites_grok_json_output() -> None:
    argv = _ensure_cli_json_events(
        ["grok", "--output-format", "json", "--single"]
    )

    assert argv == [
        "grok",
        "--output-format",
        "streaming-json",
        "--single",
    ]


def test_claude_json_event_stats_count_tools_and_tokens() -> None:
    stdout = "\n".join(
        [
            '{"type":"system","session_id":"s"}',
            (
                '{"type":"assistant","message":{"content":[{"type":"text",'
                '"text":"Working"}],"usage":{"input_tokens":7,'
                '"output_tokens":1}}}'
            ),
            (
                '{"type":"assistant","message":{"content":['
                '{"type":"tool_use","name":"Bash","id":"t1","input":{}},'
                '{"type":"tool_use","name":"Edit","id":"t2","input":{}}],'
                '"usage":{"input_tokens":13,"output_tokens":4}}}'
            ),
            '{"type":"user","message":{"content":[{"type":"tool_result"}]}}',
            '{"type":"result","num_turns":2,"totalTokens":50}',
        ]
    )

    assert _claude_json_event_stats(stdout) == {
        "agent_steps": 2,
        "agent_tool_calls": 2,
        "agent_shell_commands": 1,
        "agent_events": 5,
        "agent_llm_calls": 2,
        "agent_input_tokens": 20,
        "agent_output_tokens": 5,
        "agent_total_tokens": 50,
    }


def test_claude_json_event_stats_fold_cache_tokens_into_input() -> None:
    # Claude Code bills cached context under cache_read/cache_creation fields;
    # `input_tokens` alone is only the fresh delta. The parser must fold both
    # cache fields back into agent_input_tokens so totals reflect real usage.
    stdout = "\n".join(
        [
            '{"type":"system","session_id":"s"}',
            (
                '{"type":"assistant","message":{"content":[{"type":"text",'
                '"text":"Working"}],"usage":{"input_tokens":7,'
                '"cache_read_input_tokens":1000,'
                '"cache_creation_input_tokens":500,"output_tokens":1}}}'
            ),
            (
                '{"type":"assistant","message":{"content":['
                '{"type":"tool_use","name":"Bash","id":"t1","input":{}}],'
                '"usage":{"input_tokens":13,"cache_read_input_tokens":2000,'
                '"output_tokens":4}}}'
            ),
            '{"type":"result","num_turns":2,"totalTokens":3600}',
        ]
    )

    stats = _claude_json_event_stats(stdout)
    assert stats["agent_input_tokens"] == 3520  # 7+1000+500 + 13+2000
    assert stats["agent_output_tokens"] == 5
    assert stats["agent_total_tokens"] == 3600


def test_gemini_json_event_stats_count_tools_and_tokens() -> None:
    stdout = "\n".join(
        [
            '{"type":"init","session_id":"s","model":"GigaChat-3.5"}',
            '{"type":"message","role":"user","content":"do it"}',
            '{"type":"message","role":"assistant","content":"Working","delta":true}',
            '{"type":"tool_use","tool_name":"run_shell_command","tool_id":"t1"}',
            '{"type":"tool_result","tool_id":"t1","status":"success"}',
            (
                '{"type":"result","status":"success","stats":{'
                '"input_tokens":30,"output_tokens":8,"total_tokens":38,'
                '"tool_calls":3}}'
            ),
        ]
    )

    assert _gemini_json_event_stats(stdout) == {
        "agent_steps": 3,
        "agent_tool_calls": 3,
        "agent_shell_commands": 1,
        "agent_events": 6,
        "agent_llm_calls": 1,
        "agent_input_tokens": 30,
        "agent_output_tokens": 8,
        "agent_total_tokens": 38,
    }


def test_grok_json_event_stats_count_session_tools_and_tokens(
    tmp_path: Path,
    monkeypatch,
) -> None:
    grok_home = tmp_path / "grok-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_id = "019f65a5-6201-7562-8e7d-94b7d80a512d"
    session_dir = (
        grok_home
        / "sessions"
        / quote(str(workspace.resolve()), safe="")
        / session_id
    )
    session_dir.mkdir(parents=True)
    (session_dir / "updates.jsonl").write_text(
        "\n".join(
            [
                (
                    '{"method":"session/update","params":{"update":{'
                    '"sessionUpdate":"tool_call","toolCallId":"shell-1",'
                    '"title":"run_terminal_command","_meta":{"x.ai/tool":{'
                    '"name":"run_terminal_command","kind":"execute"}}}}}'
                ),
                (
                    '{"method":"session/update","params":{"update":{'
                    '"sessionUpdate":"tool_call_update","toolCallId":"shell-1"}}}'
                ),
                (
                    '{"method":"session/update","params":{"update":{'
                    '"sessionUpdate":"tool_call","toolCallId":"write-1",'
                    '"title":"write","_meta":{"x.ai/tool":{'
                    '"name":"write","kind":"write"}}}}}'
                ),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GROK_HOME", str(grok_home))
    stdout = "\n".join(
        [
            '{"type":"thought","data":"Working"}',
            '{"type":"text","data":"DONE"}',
            (
                '{"type":"end","stopReason":"EndTurn",'
                f'"sessionId":"{session_id}",'
                '"usage":{"input_tokens":2713,"cache_read_input_tokens":23552,'
                '"output_tokens":68,"reasoning_tokens":60,"total_tokens":26333},'
                '"num_turns":2,"modelUsage":{"grok-4.5":{'
                '"inputTokens":2713,"outputTokens":68,'
                '"cacheReadInputTokens":23552,"modelCalls":2}}}'
            ),
        ]
    )

    assert _grok_json_event_stats(stdout, workspace=workspace) == {
        "agent_steps": 2,
        "agent_tool_calls": 2,
        "agent_shell_commands": 1,
        "agent_events": 3,
        "agent_llm_calls": 2,
        "agent_input_tokens": 26265,
        "agent_output_tokens": 68,
        "agent_total_tokens": 26333,
    }


def test_task_run_stats_dispatches_to_grok_parser() -> None:
    stdout = "\n".join(
        [
            '{"type":"text","data":"OK"}',
            (
                '{"type":"end","sessionId":"session-1",'
                '"usage":{"input_tokens":10,"cache_read_input_tokens":20,'
                '"output_tokens":3,"total_tokens":33},'
                '"num_turns":1}'
            ),
        ]
    )

    run = _task_run_with_cli_stats(
        task_id="task_fake",
        passed=True,
        message="ok",
        elapsed_seconds=0.1,
        result=subprocess.CompletedProcess(["grok"], 0, stdout, ""),
    )

    assert run.agent_steps == 0
    assert run.agent_llm_calls == 1
    assert run.agent_input_tokens == 30
    assert run.agent_output_tokens == 3
    assert run.agent_total_tokens == 33


def test_codex_and_claude_parsers_ignore_gemini_events() -> None:
    stdout = "\n".join(
        [
            '{"type":"init","session_id":"s","model":"GigaChat-3.5"}',
            '{"type":"message","role":"assistant","content":"Working","delta":true}',
            '{"type":"tool_use","tool_name":"run_shell_command","tool_id":"t1"}',
            (
                '{"type":"result","status":"success","stats":{'
                '"input_tokens":30,"output_tokens":8,"total_tokens":38,'
                '"tool_calls":1}}'
            ),
        ]
    )

    assert _codex_json_event_stats(stdout) is None
    assert _claude_json_event_stats(stdout) is None


def test_task_run_stats_dispatches_to_gemini_parser() -> None:
    stdout = "\n".join(
        [
            '{"type":"init","session_id":"s","model":"GigaChat-3.5"}',
            '{"type":"tool_use","tool_name":"run_shell_command","tool_id":"t1"}',
            (
                '{"type":"result","status":"success","stats":{'
                '"input_tokens":30,"output_tokens":8,"total_tokens":38,'
                '"tool_calls":1}}'
            ),
        ]
    )

    run = _task_run_with_cli_stats(
        task_id="task_fake",
        passed=True,
        message="ok",
        elapsed_seconds=0.1,
        result=subprocess.CompletedProcess(["gemini"], 0, stdout, ""),
    )

    assert run.agent_steps == 1
    assert run.agent_shell_commands == 1
    assert run.agent_total_tokens == 38


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


def test_ouroboros_result_stats_reads_cumulative_usage(tmp_path: Path) -> None:
    (tmp_path / ".ouroboros_result.json").write_text(
        """
        {
          "status": "completed",
          "loop_outcome": {
            "usage": {
              "prompt_tokens": 109284,
              "completion_tokens": 249,
              "total_rounds": 4
            },
            "trace_refs": {
              "llm_call_refs": [
                {"llm_call_id": "a"},
                {"llm_call_id": "b"},
                {"llm_call_id": "c"},
                {"llm_call_id": "d"}
              ]
            }
          }
        }
        """,
        encoding="utf-8",
    )

    stats = _ouroboros_result_stats(tmp_path)
    assert stats is not None
    assert stats["agent_input_tokens"] == 109284
    assert stats["agent_output_tokens"] == 249
    assert stats["agent_total_tokens"] == 109533
    assert stats["agent_steps"] == 4
    assert stats["agent_llm_calls"] == 4


def test_ouroboros_result_stats_absent_file_returns_none(tmp_path: Path) -> None:
    assert _ouroboros_result_stats(tmp_path) is None


def test_task_run_stats_dispatches_to_ouroboros_parser(tmp_path: Path) -> None:
    (tmp_path / ".ouroboros_result.json").write_text(
        '{"loop_outcome": {"usage": {"prompt_tokens": 50, "completion_tokens": 6}}}',
        encoding="utf-8",
    )

    run = _task_run_with_cli_stats(
        task_id="task_fake",
        passed=True,
        message="ok",
        elapsed_seconds=0.1,
        result=subprocess.CompletedProcess(["ouroboros"], 0, "final answer text", ""),
        workspace=tmp_path,
    )

    assert run.agent_input_tokens == 50
    assert run.agent_output_tokens == 6
    assert run.agent_total_tokens == 56


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


_PI_SESSION = """\
{"type":"session","version":3,"id":"019fdba3-b90c-7440-b88b-94148b3635cf","timestamp":"2026-08-07T09:52:47.884Z","cwd":"/tmp/ws"}
{"type":"model_change","id":"83443fa4","parentId":null,"timestamp":"2026-08-07T09:52:49.057Z","provider":"cpa","modelId":"deepseek-v4-flash"}
{"type":"message","id":"8ff4de59","parentId":"630b66b5","timestamp":"2026-08-07T09:52:49.083Z","message":{"role":"user","content":[{"type":"text","text":"create hello.py"}],"timestamp":1786096369078}}
{"type":"message","id":"5d3d9f54","parentId":"332c1a89","timestamp":"2026-08-07T09:52:54.567Z","message":{"role":"assistant","content":[{"type":"thinking","thinking":"plan"},{"type":"toolCall","id":"call_1","name":"write","arguments":{"path":"hello.py","content":"print(\\"Hello, world!\\")"}}],"api":"openai-completions","provider":"cpa","model":"deepseek-v4-flash","usage":{"input":4518,"output":107,"cacheRead":0,"cacheWrite":0,"reasoning":42,"totalTokens":4625,"cost":{"total":0.00066248}},"stopReason":"toolUse","timestamp":1786096369126}}
{"type":"message","id":"66bbc527","parentId":"5d3d9f54","timestamp":"2026-08-07T09:52:54.573Z","message":{"role":"toolResult","toolCallId":"call_1","toolName":"write","content":[{"type":"text","text":"ok"}],"isError":false,"timestamp":1786096374573}}
{"type":"message","id":"1cb6865a","parentId":"f1940f84","timestamp":"2026-08-07T09:53:00.148Z","message":{"role":"assistant","content":[{"type":"toolCall","id":"call_2","name":"bash","arguments":{"command":"python3 hello.py"}}],"api":"openai-completions","provider":"cpa","model":"deepseek-v4-flash","usage":{"input":4647,"output":54,"cacheRead":0,"cacheWrite":0,"totalTokens":4701,"cost":{"total":0.0006657}},"stopReason":"toolUse","timestamp":1786096374600}}
{"type":"message","id":"40259b98","parentId":"1cb6865a","timestamp":"2026-08-07T09:53:00.178Z","message":{"role":"toolResult","toolCallId":"call_2","toolName":"bash","content":[{"type":"text","text":"Hello, world!\\n"}],"isError":false,"timestamp":1786096380178}}
{"type":"message","id":"dc6dea21","parentId":"c7d21010","timestamp":"2026-08-07T09:53:02.858Z","message":{"role":"assistant","content":[{"type":"text","text":"Done."}],"api":"openai-completions","provider":"cpa","model":"deepseek-v4-flash","usage":{"input":798,"output":61,"cacheRead":3840,"cacheWrite":0,"totalTokens":4699,"cost":{"total":0.000139552}},"stopReason":"stop","timestamp":1786096380179}}
"""


def _write_pi_session(tmp_path: Path) -> Path:
    path = tmp_path / "2026-08-07T09-52-47-884Z_019fdba3-b90c-7440-b88b-94148b3635cf.jsonl"
    path.write_text(_PI_SESSION, encoding="utf-8")
    return path


def test_pi_session_stats_count_tools_and_tokens(tmp_path: Path) -> None:
    _write_pi_session(tmp_path)

    assert _pi_session_stats(tmp_path) == {
        "agent_steps": 2,
        "agent_tool_calls": 2,
        "agent_shell_commands": 1,
        "agent_events": 3,
        "agent_llm_calls": 3,
        "agent_input_tokens": 13803,
        "agent_output_tokens": 222,
        "agent_total_tokens": 14025,
    }


def test_pi_session_stats_folds_cache_into_input(tmp_path: Path) -> None:
    _write_pi_session(tmp_path)

    stats = _pi_session_stats(tmp_path)
    # input folds cacheRead into input per the harness convention:
    # 4518 + 4647 + (798 fresh + 3840 cacheRead) = 13803
    assert stats["agent_input_tokens"] == 13803
    assert stats["agent_total_tokens"] == 14025
    # Without cache folding, input would be 9963 — sanity-check the fold happened.
    assert stats["agent_input_tokens"] != 4518 + 4647 + 798


def test_pi_session_stats_absent_file_returns_none(tmp_path: Path) -> None:
    assert _pi_session_stats(tmp_path) is None


def test_task_run_stats_dispatches_to_pi_parser(tmp_path: Path) -> None:
    _write_pi_session(tmp_path)

    run = _task_run_with_cli_stats(
        task_id="task_fake",
        passed=True,
        message="ok",
        elapsed_seconds=0.1,
        result=subprocess.CompletedProcess(["pi"], 0, "Done.", ""),
        workspace=tmp_path,
    )

    assert run.agent_steps == 2
    assert run.agent_shell_commands == 1
    assert run.agent_llm_calls == 3
    assert run.agent_input_tokens == 13803
    assert run.agent_output_tokens == 222
    assert run.agent_total_tokens == 14025
