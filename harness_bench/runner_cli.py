"""Drive each benchmark task through an external CLI agent.

The default GigaChat runner builds an in-process `deepagents` agent. This
module is the alternative: for each task we shell out to a CLI agent (e.g.
`free-code` / Claude Code CLI) inside a fresh temp workspace, then run the
same verifier against the resulting files. That gives us an apples-to-apples
score for "what fraction of the bench would this CLI solve" without changing
the task set.

The CLI command is configurable, defaulting to:

    free-code -p --model haiku --dangerously-skip-permissions <prompt>

The prompt is passed as the last positional argument. We always set `cwd` to
the per-task temp directory and `--add-dir` is not needed because the CLI
defaults to operating on its own cwd.
"""

from __future__ import annotations

import gc
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

from harness_bench.core import Task
from harness_bench.runner import (
    TaskRun,
    _add_usage_counts,
    _load_env_from_dotenv,
    _mark_attempt,
    _one_line_detail,
    _pending_task_attempts,
    _resume_results,
    _task_attempt_label,
    _task_attempt_label_for,
    _task_sort_key,
    _write_interrupted_results_json,
    _write_partial_results_json,
    normalize_json_output_path,
    summarize,
)
from harness_bench.tasks import ALL_TASKS, get_task

DEFAULT_CLI_COMMAND = (
    "free-code -p --model haiku --dangerously-skip-permissions"
)
"""Default CLI invocation. The prompt is appended as the final argument."""

DEFAULT_TIMEOUT_SECONDS = 600
"""Per-task timeout in seconds. Some tasks need pytest + multiple file edits."""

_TRANSIENT_ERROR_PATTERN = re.compile(
    r"(?:"
    # HTTP 4xx / 5xx
    r"status\s+[45]\d\d"
    # Node.js / libuv socket errors
    r"|ECONN(?:RESET|REFUSED|ABORTED)"
    r"|ETIMEDOUT|EAI_AGAIN|ENETUNREACH|EHOSTUNREACH|EPIPE"
    # TLS / socket disconnects
    r"|socket hang up"
    r"|socket disconnected"
    r"|TLS\s+(?:connection|handshake)"
    # Generic transport blips
    r"|connection\s+(?:refused|reset|timed out|terminated|closed)"
    r"|network\s+(?:error|timeout|unreachable)"
    r"|request\s+(?:failed|timeout|aborted)"
    r"|streaming\s+request\s+failed"
    r"|fetch\s+failed"
    r")",
    re.IGNORECASE,
)
"""Detect a transient network / HTTP error in subprocess stderr/stdout.

Captures both HTTP-level 4xx/5xx and transport-level failures (TLS handshake
aborts, socket disconnects, libuv error codes) so we retry every error that
isn't actually a model-quality issue.
"""

DEFAULT_TRANSIENT_RETRIES = 5
"""How many times to retry the CLI on a transient HTTP error before giving up."""

_BACKOFF_SCHEDULE = (30, 60, 120, 240, 300)
"""Progressive backoff (seconds) between successive retries.

The IFT GigaChat endpoint applies multi-minute IP throttles, so short
exponential backoffs (1-32s) don't outlive a single lockout window. This
schedule waits for the IP to be unblocked before the next attempt.
"""

_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
"""Per-token-URL cache of (access_token, expires_at_unix_seconds)."""

_CLEANUP_RETRY_DELAYS = (0.1, 0.25, 0.5, 1.0, 2.0)
"""Short retry window for Windows temp-dir cleanup races / stale handles."""

_PROCESS_TREE_SHUTDOWN_TIMEOUT = 10
"""Seconds to wait for pipes to close after killing a timed-out CLI tree."""

_INTERRUPT_SHUTDOWN_GRACE_SECONDS = 2.0
"""Seconds to wait after Ctrl-C TERM before force-killing active CLI trees."""

_ACTIVE_PROCESS_LOCK = threading.Lock()
_ACTIVE_PROCESSES: set[subprocess.Popen[str]] = set()
"""CLI subprocesses currently owned by worker threads."""

_STOP_REQUESTED = threading.Event()
"""Set when the main runner is interrupted so worker retries/backoffs stop."""

_AGENT_METRIC_KEYS = (
    "agent_steps",
    "agent_tool_calls",
    "agent_shell_commands",
    "agent_events",
    "agent_llm_calls",
    "agent_input_tokens",
    "agent_output_tokens",
    "agent_total_tokens",
)
"""Per-task effort metrics written to result JSON when a parser recognizes a run."""


def _is_opencode_run_command(argv: list[str]) -> bool:
    if len(argv) < 2:
        return False
    return Path(argv[0]).name == "opencode" and argv[1] == "run"


def _argv_for_workspace(argv: list[str], workspace: Path) -> list[str]:
    """Return argv with an explicit workspace argument when the CLI needs one."""
    if not _is_opencode_run_command(argv):
        return list(argv)
    if any(arg == "--dir" or arg.startswith("--dir=") for arg in argv):
        return list(argv)
    return [*argv, "--dir", str(workspace)]


def _is_codex_exec_command(argv: list[str]) -> bool:
    """Return whether argv launches `codex exec` or its short alias."""
    if not argv:
        return False
    executable = Path(argv[0]).name
    return executable == "codex" and len(argv) > 1 and argv[1] in ("exec", "e")


def _ensure_codex_json_events(argv: list[str]) -> list[str]:
    """Ask Codex exec for JSONL events so runner metrics can count steps."""
    if not _is_codex_exec_command(argv) or "--json" in argv:
        return argv
    return [*argv[:2], "--json", *argv[2:]]


def _is_claude_print_command(argv: list[str]) -> bool:
    if not argv:
        return False
    executable = Path(argv[0]).name
    return executable == "claude" and any(arg in ("-p", "--print") for arg in argv)


def _is_gemini_prompt_command(argv: list[str]) -> bool:
    if not argv:
        return False
    executable = Path(argv[0]).name
    return executable == "gemini" and any(
        arg in ("-p", "--prompt") or arg.startswith("--prompt=") for arg in argv
    )


def _argv_with_output_format(
    argv: list[str],
    output_format: str,
    *,
    insert_before: tuple[str, ...] = (),
) -> list[str]:
    """Return argv with --output-format set without mutating the caller's list."""
    result = list(argv)
    for i, arg in enumerate(result):
        if arg in ("--output-format", "-o"):
            if i + 1 < len(result):
                result[i + 1] = output_format
                return result
            return [*result, output_format]
        if arg.startswith("--output-format="):
            result[i] = f"--output-format={output_format}"
            return result
        if arg.startswith("-o="):
            result[i] = f"-o={output_format}"
            return result

    insert_at = len(result)
    for i, arg in enumerate(result):
        if arg in insert_before:
            insert_at = i
            break
    return [*result[:insert_at], "--output-format", output_format, *result[insert_at:]]


def _ensure_claude_verbose(argv: list[str]) -> list[str]:
    result = [
        "--verbose" if arg in ("—verbose", "–verbose") else arg
        for arg in argv
    ]
    if "--verbose" in result:
        return result
    return [*result, "--verbose"]


def _ensure_claude_json_events(argv: list[str]) -> list[str]:
    """Ask Claude Code for stream-json events when it is used in print mode."""
    if not _is_claude_print_command(argv):
        return argv
    argv = _argv_with_output_format(argv, "stream-json")
    return _ensure_claude_verbose(argv)


def _ensure_gemini_json_events(argv: list[str]) -> list[str]:
    """Ask Gemini CLI for stream-json events when it is used in prompt mode."""
    if not _is_gemini_prompt_command(argv):
        return argv
    return _argv_with_output_format(
        argv,
        "stream-json",
        insert_before=("-p", "--prompt"),
    )


def _ensure_cli_json_events(argv: list[str]) -> list[str]:
    """Enable machine-readable CLI output for CLIs that expose it."""
    argv = _ensure_codex_json_events(argv)
    argv = _ensure_claude_json_events(argv)
    return _ensure_gemini_json_events(argv)


def _iter_json_payloads(stdout: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    if payloads:
        return payloads

    stripped = stdout.strip()
    if not stripped:
        return []
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _add_metric_count(stats: dict[str, int], key: str, value: object) -> None:
    parsed = _int_or_none(value)
    if parsed is not None:
        stats[key] = stats.get(key, 0) + parsed


def _set_metric_max(stats: dict[str, int], key: str, value: object) -> None:
    parsed = _int_or_none(value)
    if parsed is not None:
        stats[key] = max(stats.get(key, 0), parsed)


def _usage_stats_from_mapping(usage: object) -> dict[str, int]:
    stats: dict[str, int] = {}
    if not isinstance(usage, dict):
        return stats

    _add_usage_counts(stats, usage)

    if "agent_input_tokens" not in stats:
        for key in ("inputTokens", "promptTokenCount", "prompt_token_count"):
            if key in usage:
                _add_metric_count(stats, "agent_input_tokens", usage[key])
                break
    if "agent_output_tokens" not in stats:
        for key in ("outputTokens", "candidatesTokenCount", "candidates_token_count"):
            if key in usage:
                _add_metric_count(stats, "agent_output_tokens", usage[key])
                break

    for key in ("totalTokens", "totalTokenCount", "total_token_count"):
        if key in usage:
            parsed = _int_or_none(usage[key])
            if parsed is not None:
                stats["agent_total_tokens"] = parsed
                break

    return stats


def _merge_metric_counts(stats: dict[str, int], extra: dict[str, int]) -> None:
    for key, value in extra.items():
        stats[key] = stats.get(key, 0) + value


def _with_default_agent_metrics(stats: dict[str, int]) -> dict[str, int]:
    return {key: stats.get(key, 0) for key in _AGENT_METRIC_KEYS}


def _tool_name_is_shell(name: object) -> bool:
    if not isinstance(name, str):
        return False
    return name.lower() in {
        "bash",
        "shell",
        "run_shell_command",
        "execute_shell_command",
    }


def _claude_tool_use_names(content: object) -> list[str]:
    if not isinstance(content, list):
        return []
    names: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") != "tool_use":
            continue
        name = part.get("name")
        if isinstance(name, str):
            names.append(name)
    return names


def _looks_like_claude_payload(payload: dict[str, object]) -> bool:
    payload_type = payload.get("type")
    if any(key in payload for key in ("totalToolUseCount", "totalTokens", "toolStats")):
        return True
    if payload_type == "assistant":
        return isinstance(payload.get("message"), dict) or "message" in payload
    if payload_type == "user":
        return isinstance(payload.get("message"), dict)
    if payload_type == "system":
        return "subtype" in payload or "session_id" in payload
    if payload_type == "result":
        return "stats" not in payload and any(
            key in payload
            for key in (
                "subtype",
                "is_error",
                "num_turns",
                "duration_ms",
                "duration_api_ms",
                "total_cost_usd",
                "usage",
            )
        )
    return False


def _claude_json_event_stats(stdout: str) -> dict[str, int] | None:
    """Extract effort metrics from Claude Code JSON / stream-json output."""
    payloads = _iter_json_payloads(stdout)
    if not payloads:
        return None

    saw_claude_event = False
    events = 0
    tool_calls = 0
    shell_commands = 0
    llm_calls = 0
    stream_usage_stats: dict[str, int] = {}
    result_usage_stats: dict[str, int] = {}
    result_tool_calls: int | None = None
    result_shell_commands: int | None = None

    for payload in payloads:
        payload_type = payload.get("type")
        if not _looks_like_claude_payload(payload):
            continue

        saw_claude_event = True
        events += 1

        if payload_type == "assistant":
            llm_calls += 1
            message = payload.get("message")
            if isinstance(message, dict):
                _merge_metric_counts(
                    stream_usage_stats,
                    _usage_stats_from_mapping(message.get("usage")),
                )
                names = _claude_tool_use_names(message.get("content"))
            else:
                names = _claude_tool_use_names(payload.get("content"))
            tool_calls += len(names)
            shell_commands += sum(1 for name in names if _tool_name_is_shell(name))
        elif payload_type == "result":
            _set_metric_max(result_usage_stats, "agent_total_tokens", payload.get("totalTokens"))
            turns = _int_or_none(payload.get("num_turns"))
            if turns is not None:
                llm_calls = max(llm_calls, turns)

        _merge_metric_counts(
            result_usage_stats,
            _usage_stats_from_mapping(payload.get("usage")),
        )
        _set_metric_max(result_usage_stats, "agent_total_tokens", payload.get("totalTokens"))

        total_tool_use_count = _int_or_none(payload.get("totalToolUseCount"))
        if total_tool_use_count is not None:
            result_tool_calls = max(result_tool_calls or 0, total_tool_use_count)
        tool_stats = payload.get("toolStats")
        if isinstance(tool_stats, dict):
            bash_count = _int_or_none(tool_stats.get("bashCount"))
            if bash_count is not None:
                result_shell_commands = max(result_shell_commands or 0, bash_count)

    if not saw_claude_event:
        return None

    if result_tool_calls is not None:
        tool_calls = max(tool_calls, result_tool_calls)
    if result_shell_commands is not None:
        shell_commands = max(shell_commands, result_shell_commands)

    stats = {
        "agent_steps": tool_calls,
        "agent_tool_calls": tool_calls,
        "agent_shell_commands": shell_commands,
        "agent_events": events,
    }
    if llm_calls:
        stats["agent_llm_calls"] = llm_calls

    token_stats = stream_usage_stats or result_usage_stats
    if result_usage_stats.get("agent_total_tokens", 0) > token_stats.get(
        "agent_total_tokens",
        0,
    ):
        token_stats = {
            **token_stats,
            "agent_total_tokens": result_usage_stats["agent_total_tokens"],
        }
    stats.update(token_stats)
    return _with_default_agent_metrics(stats)


def _gemini_json_event_stats(stdout: str) -> dict[str, int] | None:
    """Extract effort metrics from Gemini CLI JSON / stream-json output."""
    payloads = _iter_json_payloads(stdout)
    if not payloads:
        return None

    saw_gemini_event = False
    events = 0
    tool_calls = 0
    shell_commands = 0
    llm_calls = 0
    result_stats: dict[str, int] = {}

    for payload in payloads:
        payload_type = payload.get("type")
        looks_gemini = payload_type in {
            "init",
            "message",
            "tool_use",
            "tool_result",
            "error",
            "result",
        } or "stats" in payload
        if not looks_gemini:
            continue

        saw_gemini_event = True
        events += 1

        if payload_type == "message" and payload.get("role") == "assistant":
            llm_calls += 1
        elif payload_type == "tool_use":
            tool_calls += 1
            if _tool_name_is_shell(payload.get("tool_name") or payload.get("name")):
                shell_commands += 1

        stats_payload = payload.get("stats")
        if isinstance(stats_payload, dict):
            _merge_metric_counts(
                result_stats,
                _usage_stats_from_mapping(stats_payload),
            )
            _set_metric_max(result_stats, "agent_steps", stats_payload.get("tool_calls"))
            _set_metric_max(
                result_stats,
                "agent_tool_calls",
                stats_payload.get("tool_calls"),
            )

    if not saw_gemini_event:
        return None

    tool_calls = max(tool_calls, result_stats.get("agent_tool_calls", 0))
    stats = {
        "agent_steps": max(tool_calls, result_stats.get("agent_steps", 0)),
        "agent_tool_calls": tool_calls,
        "agent_shell_commands": shell_commands,
        "agent_events": events,
    }
    if llm_calls:
        stats["agent_llm_calls"] = llm_calls
    stats.update(result_stats)
    return _with_default_agent_metrics(stats)


def _codex_json_event_stats(stdout: str) -> dict[str, int] | None:
    """Count Codex JSONL action events emitted by `codex exec --json`.

    `agent_steps` counts completed non-message action items, which maps to
    concrete actions such as file edits and shell commands rather than prose
    messages. The raw event count is kept separately for audit/debugging.
    """
    events = 0
    steps = 0
    tool_calls = 0
    shell_commands = 0
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    saw_codex_event = False

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            continue
        if payload["type"] not in {
            "thread.started",
            "turn.started",
            "turn.completed",
            "turn.failed",
            "item.started",
            "item.completed",
            "error",
        }:
            continue
        saw_codex_event = True
        events += 1
        if payload["type"] == "turn.completed":
            usage_stats: dict[str, int] = {}
            _add_usage_counts(usage_stats, payload.get("usage"))
            input_tokens += usage_stats.get("agent_input_tokens", 0)
            output_tokens += usage_stats.get("agent_output_tokens", 0)
            total_tokens += usage_stats.get("agent_total_tokens", 0)
            continue
        if payload["type"] != "item.completed":
            continue
        item = payload.get("item")
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "agent_message":
            continue
        steps += 1
        if item_type == "command_execution":
            shell_commands += 1
        else:
            tool_calls += 1

    if not saw_codex_event:
        return None
    result = {
        "agent_steps": steps,
        "agent_tool_calls": tool_calls,
        "agent_shell_commands": shell_commands,
        "agent_events": events,
    }
    if input_tokens:
        result["agent_input_tokens"] = input_tokens
    if output_tokens:
        result["agent_output_tokens"] = output_tokens
    if total_tokens:
        result["agent_total_tokens"] = total_tokens
    return _with_default_agent_metrics(result)


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _mini_swe_agent_traj_candidates(workspace: Path) -> list[Path]:
    """Return possible mini-SWE-agent trajectory paths for a task workspace."""
    candidates = [workspace / "mini-swe-agent.traj.json"]
    configured = os.getenv("MSWEA_OUTPUT_PATH")
    if configured:
        configured_path = Path(configured)
        if not configured_path.is_absolute():
            configured_path = workspace / configured_path
        if configured_path not in candidates:
            candidates.append(configured_path)
    return candidates


def _mini_swe_agent_traj_stats(workspace: Path | None) -> dict[str, int] | None:
    """Extract effort metrics from a mini-SWE-agent trajectory JSON file."""
    if workspace is None:
        return None

    payload = None
    for path in _mini_swe_agent_traj_candidates(workspace):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        break
    if not isinstance(payload, dict):
        return None

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None

    stats: dict[str, int] = {"agent_events": len(messages)}
    info = payload.get("info")
    if isinstance(info, dict):
        model_stats = info.get("model_stats")
        if isinstance(model_stats, dict):
            api_calls = _int_or_none(model_stats.get("api_calls"))
            if api_calls is not None:
                stats["agent_llm_calls"] = api_calls

    steps = 0
    tool_calls = 0
    shell_commands = 0
    llm_calls = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        llm_calls += 1
        extra = message.get("extra")
        if not isinstance(extra, dict):
            continue
        actions = extra.get("actions")
        if isinstance(actions, list):
            steps += len(actions)
            tool_calls += len(actions)
            shell_commands += sum(
                1
                for action in actions
                if isinstance(action, dict) and isinstance(action.get("command"), str)
            )
        response = extra.get("response")
        if isinstance(response, dict):
            _add_usage_counts(stats, response.get("usage"))
            _add_usage_counts(stats, response.get("usage_metadata"))

    if steps:
        stats["agent_steps"] = steps
    if tool_calls:
        stats["agent_tool_calls"] = tool_calls
    if shell_commands:
        stats["agent_shell_commands"] = shell_commands
    stats.setdefault("agent_llm_calls", llm_calls)
    return stats


def _task_run_with_cli_stats(
    *,
    task_id: str,
    passed: bool,
    message: str,
    elapsed_seconds: float,
    result: subprocess.CompletedProcess[str] | None,
    error: str | None = None,
    workspace: Path | None = None,
    stats_workspace: Path | None = None,
) -> TaskRun:
    stats = _codex_json_event_stats(result.stdout or "") if result is not None else None
    if stats is None and result is not None:
        stats = _claude_json_event_stats(result.stdout or "")
    if stats is None and result is not None:
        stats = _gemini_json_event_stats(result.stdout or "")
    if stats is None:
        stats = _mini_swe_agent_traj_stats(stats_workspace or workspace)
    return TaskRun(
        task_id=task_id,
        passed=passed,
        message=message,
        elapsed_seconds=elapsed_seconds,
        error=error,
        workspace=workspace,
        **(stats or {}),
    )


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    """Terminate a CLI process and its children without changing the CLI command.

    ``run-cli`` often launches Windows agents through ``cmd /c`` because the
    real executable can be a ``.cmd`` wrapper. Killing only that immediate
    ``cmd.exe`` leaves the actual agent process alive with inherited stdout /
    stderr handles, so ``communicate()`` can block long after the configured
    per-task timeout. On Windows, ``taskkill /T`` kills the whole tree rooted at
    the wrapper process. On POSIX, start a new process group and terminate that
    group.
    """
    if os.name == "nt":
        subprocess.run(  # noqa: S603,S607 — Windows process-tree cleanup helper
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        proc.kill()


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(  # noqa: S603,S607 — Windows process-tree cleanup helper
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        proc.kill()


def _process_is_running(proc: subprocess.Popen[str]) -> bool:
    try:
        return proc.poll() is None
    except AttributeError:
        return getattr(proc, "returncode", None) is None


def _register_active_process(proc: subprocess.Popen[str]) -> None:
    with _ACTIVE_PROCESS_LOCK:
        _ACTIVE_PROCESSES.add(proc)


def _unregister_active_process(proc: subprocess.Popen[str]) -> None:
    with _ACTIVE_PROCESS_LOCK:
        _ACTIVE_PROCESSES.discard(proc)


def _terminate_all_active_processes() -> None:
    with _ACTIVE_PROCESS_LOCK:
        procs = list(_ACTIVE_PROCESSES)
    for proc in procs:
        try:
            _terminate_process_tree(proc)
        except Exception:  # noqa: BLE001 — best-effort interrupt cleanup.
            continue
    deadline = time.monotonic() + _INTERRUPT_SHUTDOWN_GRACE_SECONDS
    while time.monotonic() < deadline:
        if all(not _process_is_running(proc) for proc in procs):
            return
        time.sleep(0.05)
    for proc in procs:
        if not _process_is_running(proc):
            continue
        try:
            _kill_process_tree(proc)
        except Exception:  # noqa: BLE001 — best-effort interrupt cleanup.
            continue


def _sleep_interruptibly(seconds: float) -> None:
    if _STOP_REQUESTED.wait(seconds):
        raise KeyboardInterrupt


def _run_cli_subprocess(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    """Run the CLI command with a hard per-task process-tree timeout."""
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        start_new_session = True

    proc = subprocess.Popen(  # noqa: S603 — trusted local benchmark command
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    _register_active_process(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(proc)
        try:
            proc.communicate(timeout=_PROCESS_TREE_SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            proc.communicate()
        raise
    except BaseException:
        _terminate_process_tree(proc)
        raise
    finally:
        _unregister_active_process(proc)
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _cleanup_workspace_keepalive(
    workspace_keepalive: TemporaryDirectory[str],
    *,
    task_id: str,
) -> Path | None:
    """Best-effort cleanup of a per-task temp workspace.

    On Windows, a CLI agent or shell child can keep a file handle/cwd inside the
    workspace for a moment after ``subprocess.run`` returns. ``TemporaryDirectory``
    then raises ``OSError: [WinError 145] The directory is not empty`` and used
    to abort the whole benchmark run after an otherwise completed task. Retry a
    few times, then leave the workspace behind with a warning instead of turning
    cleanup into a harness crash.
    """
    workspace = Path(workspace_keepalive.name)
    last_exc: OSError | None = None
    for attempt, delay in enumerate((0.0, *_CLEANUP_RETRY_DELAYS), start=1):
        if delay:
            gc.collect()
            time.sleep(delay)
        try:
            workspace_keepalive.cleanup()
            return None
        except OSError as exc:
            last_exc = exc
            if attempt <= len(_CLEANUP_RETRY_DELAYS):
                continue

    print(
        f"[WARN] cleanup failed for {task_id} workspace {workspace}: {last_exc}; "
        "leaving it for inspection",
        file=sys.stderr,
    )
    return workspace


def _get_gigachat_access_token() -> str | None:
    """Fetch / refresh a GigaChat IFT access token from `GIGACHAT_TOKEN_URL`.

    Activates only when all of `GIGACHAT_TOKEN_URL`, `GIGACHAT_USER`, and
    `GIGACHAT_PASSWORD` are set in the environment. The IFT endpoint exposes
    `POST /v1/token` with HTTP basic auth (user:password) and replies with
    JSON `{"tok": "<jwt>", "exp": <unix_ms>}`. We cache the token in process
    until 60 seconds before its reported expiry so concurrent task threads
    share one fetch.

    Returns the bearer token on success, or `None` when the env is not set
    up for token-based auth (caller should then leave the subprocess env
    alone). Network errors propagate.
    """
    token_url = os.getenv("GIGACHAT_TOKEN_URL")
    user = os.getenv("GIGACHAT_USER")
    password = os.getenv("GIGACHAT_PASSWORD")
    if not (token_url and user and password):
        return None
    with _TOKEN_LOCK:
        now = time.time()
        cached = _TOKEN_CACHE.get(token_url)
        if cached and cached[1] - 60 > now:
            return cached[0]
        import httpx  # noqa: PLC0415 — lazy: only used when token URL is set

        resp = httpx.post(
            token_url,
            auth=(user, password),
            verify=os.getenv("GIGACHAT_VERIFY_SSL_CERTS", "").lower() not in ("false", "0", "no"),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        tok = data["tok"]
        exp_ms = data.get("exp")
        exp = float(exp_ms) / 1000.0 if exp_ms else now + 600
        _TOKEN_CACHE[token_url] = (tok, exp)
        return tok


def _subprocess_env_with_token() -> dict[str, str] | None:
    """Build a copy of `os.environ` with a fresh `GIGACHAT_ACCESS_TOKEN`.

    Returns `None` when token-based auth is not configured, so the caller
    can fall back to inheriting the parent process env unchanged. When a
    token is available, the returned dict also clears `GIGACHAT_USER` /
    `GIGACHAT_PASSWORD` / `GIGACHAT_CREDENTIALS` so the downstream CLI does
    not try its own OAuth handshake.
    """
    token = _get_gigachat_access_token()
    if not token:
        return None
    env = dict(os.environ)
    env["GIGACHAT_ACCESS_TOKEN"] = token
    for k in ("GIGACHAT_USER", "GIGACHAT_PASSWORD", "GIGACHAT_CREDENTIALS"):
        env.pop(k, None)
    return env


def _get_gigachat_prom_access_token() -> str | None:
    """Fetch / refresh a GigaChat PROM access token via the ngw OAuth gateway.

    Activates only when `GIGACHAT_PROM_CREDENTIALS` is set (base64 of
    `client_id:client_secret`). Optional `GIGACHAT_PROM_AUTH_URL` overrides
    the gateway (default `https://ngw.devices.sberbank.ru:9443/api/v2/oauth`),
    `GIGACHAT_PROM_SCOPE` overrides the scope (default `GIGACHAT_API_PERS`).
    Cached per-AUTH_URL until 60s before expiry like the IFT helper.

    Returns the bearer token on success, or `None` when PROM env is not
    configured.
    """
    creds = os.getenv("GIGACHAT_PROM_CREDENTIALS")
    if not creds:
        return None
    auth_url = os.getenv(
        "GIGACHAT_PROM_AUTH_URL",
        "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
    )
    scope = os.getenv("GIGACHAT_PROM_SCOPE", "GIGACHAT_API_PERS")
    with _TOKEN_LOCK:
        now = time.time()
        cached = _TOKEN_CACHE.get(auth_url)
        if cached and cached[1] - 60 > now:
            return cached[0]
        import httpx  # noqa: PLC0415

        resp = httpx.post(
            auth_url,
            headers={
                "Authorization": f"Basic {creds}",
                "RqUID": "00000000-0000-0000-0000-000000000001",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"scope": scope},
            verify=os.getenv("GIGACHAT_VERIFY_SSL_CERTS", "").lower()
            not in ("false", "0", "no"),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        tok = data["access_token"]
        exp_ms = data.get("expires_at")
        exp = float(exp_ms) / 1000.0 if exp_ms else now + 600
        _TOKEN_CACHE[auth_url] = (tok, exp)
        return tok


def _subprocess_env_with_prom_token() -> dict[str, str] | None:
    """Build a copy of `os.environ` with a PROM `GIGACHAT_ACCESS_TOKEN`.

    PROM-PERS exposes a different model line-up than IFT (no GigaChat-3-*),
    so the chat URL is also swapped to `gigachat.devices.sberbank.ru/api/v1`
    and the model name is overridden via `GIGACHAT_PROM_MODEL`
    (default `GigaChat-2-Max`, the closest PROM-PERS analogue to
    GigaChat-3-Ultra on this bench).
    """
    token = _get_gigachat_prom_access_token()
    if not token:
        return None
    env = dict(os.environ)
    env["GIGACHAT_ACCESS_TOKEN"] = token
    env["GIGACHAT_BASE_URL"] = os.getenv(
        "GIGACHAT_PROM_BASE_URL",
        "https://gigachat.devices.sberbank.ru/api/v1",
    )
    for k in ("GIGACHAT_USER", "GIGACHAT_PASSWORD", "GIGACHAT_CREDENTIALS"):
        env.pop(k, None)
    return env


def _swap_model_in_cli_command(cli_command: str, new_model: str) -> str:
    """Replace ``--model X`` (or ``-m X``) in the CLI command with `new_model`.

    Used by the PROM fallback to swap GigaChat-3-Ultra (IFT-only) for
    GigaChat-2-Max (best PROM-PERS analogue) without forcing the caller to
    pass two cli-command strings.
    """
    parts = shlex.split(cli_command)
    for i, p in enumerate(parts):
        if p in ("--model", "-m") and i + 1 < len(parts):
            parts[i + 1] = new_model
            break
    return shlex.join(parts)


def run_task_cli(
    task: Task,
    *,
    cli_command: str = DEFAULT_CLI_COMMAND,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    keep_workspace: bool = False,
    transient_retries: int = DEFAULT_TRANSIENT_RETRIES,
) -> TaskRun:
    """Run a single task via the CLI agent and return its `TaskRun` result.

    Transient HTTP errors (4xx/5xx status codes printed by the CLI itself —
    typically rate-limit 403 / 429 or 5xx server blips from the upstream
    provider) are retried up to `transient_retries` times with exponential
    backoff before the task is counted as a real failure. Each retry runs in
    a fresh per-task temp workspace so the agent starts from clean fixtures.
    """
    _load_env_from_dotenv()
    workspace_keepalive: TemporaryDirectory | None = None
    base_argv = _ensure_cli_json_events(shlex.split(cli_command))
    last_result: subprocess.CompletedProcess[str] | None = None
    last_transient_excerpt: str | None = None
    started = time.monotonic()

    # AGENTS.md is the runtime-tool / memory-discipline convention used by
    # `deepagents` (and Codex CLI / Cursor): the file lives in the workspace
    # and is auto-read into the system prompt. Claude Code (`free-code`)
    # uses its own host-side memory at ~/.claude/projects/... and does NOT
    # auto-discover AGENTS.md, so tasks that depend on it (e.g.
    # `tasks_memory.py`) fail by design. When we detect a Claude-Code-like
    # CLI, inject the workspace AGENTS.md via `--append-system-prompt` so
    # the agent sees the same ambient instructions an AGENTS.md-native
    # runtime would.
    inject_agents_md = any(
        "free-code" in arg or arg.endswith("/claude") or arg == "claude"
        for arg in base_argv[:2]
    )

    try:
        for attempt in range(transient_retries + 1):
            if keep_workspace:
                workspace_path = Path(mkdtemp(prefix=f"hb_cli_{task.id}_"))
                workspace_keepalive = None
            else:
                workspace_keepalive = TemporaryDirectory(prefix=f"hb_cli_{task.id}_")
                workspace_path = Path(workspace_keepalive.name)

            task.setup(workspace_path)

            if _STOP_REQUESTED.is_set():
                raise KeyboardInterrupt

            argv = _argv_for_workspace(base_argv, workspace_path)
            agents_md = workspace_path / "AGENTS.md"
            if inject_agents_md and agents_md.exists():
                argv += [
                    "--append-system-prompt",
                    agents_md.read_text(encoding="utf-8"),
                ]
            argv += [task.prompt]

            try:
                last_result = _run_cli_subprocess(
                    argv,
                    cwd=workspace_path,
                    timeout=timeout,
                    env=_subprocess_env_with_token(),
                )
            except subprocess.TimeoutExpired:
                return TaskRun(
                    task_id=task.id,
                    passed=False,
                    message="",
                    elapsed_seconds=time.monotonic() - started,
                    error=f"CLI timed out after {timeout}s",
                    workspace=workspace_path if keep_workspace else None,
                )
            except FileNotFoundError as exc:
                return TaskRun(
                    task_id=task.id,
                    passed=False,
                    message="",
                    elapsed_seconds=time.monotonic() - started,
                    error=f"CLI executable not found: {exc}",
                    workspace=workspace_path if keep_workspace else None,
                )
            except Exception:  # noqa: BLE001 — surface as failure
                return TaskRun(
                    task_id=task.id,
                    passed=False,
                    message="",
                    elapsed_seconds=time.monotonic() - started,
                    error=traceback.format_exc(),
                    workspace=workspace_path if keep_workspace else None,
                )

            outcome = task.verify(workspace_path)
            if outcome.passed:
                return _task_run_with_cli_stats(
                    task_id=task.id,
                    passed=True,
                    message=outcome.message,
                    elapsed_seconds=time.monotonic() - started,
                    result=last_result,
                    workspace=workspace_path if keep_workspace else None,
                    stats_workspace=workspace_path,
                )

            # Decide whether to retry. We retry on any transient network error
            # (HTTP 4xx/5xx or transport-level disconnect). Other failures
            # (verifier mismatch, model wrote the wrong content) get surfaced
            # immediately — retrying wouldn't change the outcome.
            combined = ((last_result.stderr or "") + "\n" + (last_result.stdout or ""))
            m = _TRANSIENT_ERROR_PATTERN.search(combined)
            if m and attempt < transient_retries:
                last_transient_excerpt = m.group(0)
                # Clean up the failed-attempt workspace before retrying, then
                # sleep with a progressive backoff (30s, 60s, 120s, 240s, 300s)
                # — long enough to outlive multi-minute IP throttles on the
                # IFT endpoint.
                if workspace_keepalive is not None:
                    _cleanup_workspace_keepalive(workspace_keepalive, task_id=task.id)
                    workspace_keepalive = None
                delay = _BACKOFF_SCHEDULE[min(attempt, len(_BACKOFF_SCHEDULE) - 1)]
                _sleep_interruptibly(delay)
                continue

            # Verifier failed and the error isn't transient (or budget exhausted).
            # If we tried to retry at all, prefer the "gave up" wording so the
            # log surfaces retry exhaustion clearly even when the CLI also
            # exited non-zero.
            message = outcome.message
            if last_transient_excerpt:
                message = (
                    f"{outcome.message} | gave up after {transient_retries} transient retries "
                    f"({last_transient_excerpt!r})"
                )
            elif last_result.returncode != 0:
                tail = (last_result.stderr or last_result.stdout).strip()[-300:]
                message = f"{outcome.message} | CLI exit={last_result.returncode}: {tail!r}"

            # PROM fallback: when the primary path (typically IFT) exhausted
            # its retry budget on transient errors AND PROM credentials are
            # configured in the environment, retry the task once on PROM with
            # the closest available model (default GigaChat-2-Max). This isn't
            # "apples-to-apples" — the model is different — but it answers
            # "would this task have passed if our infra weren't throttling?".
            if last_transient_excerpt:
                prom_env = _subprocess_env_with_prom_token()
                if prom_env is not None:
                    prom_model = os.getenv("GIGACHAT_PROM_MODEL", "GigaChat-2-Max")
                    prom_base_argv = _ensure_codex_json_events(
                        shlex.split(_swap_model_in_cli_command(cli_command, prom_model))
                    )
                    # Clean prior workspace and re-set up for PROM attempt.
                    if workspace_keepalive is not None:
                        _cleanup_workspace_keepalive(workspace_keepalive, task_id=task.id)
                        workspace_keepalive = None
                    if keep_workspace:
                        workspace_path = Path(mkdtemp(prefix=f"hb_cli_prom_{task.id}_"))
                    else:
                        workspace_keepalive = TemporaryDirectory(
                            prefix=f"hb_cli_prom_{task.id}_"
                        )
                        workspace_path = Path(workspace_keepalive.name)
                    task.setup(workspace_path)
                    prom_argv = [
                        *_argv_for_workspace(prom_base_argv, workspace_path),
                        task.prompt,
                    ]
                    try:
                        prom_result = _run_cli_subprocess(
                            prom_argv,
                            cwd=workspace_path,
                            timeout=timeout,
                            env=prom_env,
                        )
                    except subprocess.TimeoutExpired:
                        prom_result = None
                    except Exception:  # noqa: BLE001
                        prom_result = None
                    if prom_result is not None:
                        prom_outcome = task.verify(workspace_path)
                        prom_tag = f" [PROM-fallback model={prom_model}]"
                        if prom_outcome.passed:
                            return _task_run_with_cli_stats(
                                task_id=task.id,
                                passed=True,
                                message=prom_outcome.message + prom_tag,
                                elapsed_seconds=time.monotonic() - started,
                                result=prom_result,
                                workspace=workspace_path if keep_workspace else None,
                                stats_workspace=workspace_path,
                            )
                        # PROM-fallback also failed — surface its message for visibility.
                        message = f"{message} | PROM-fallback({prom_model}): {prom_outcome.message}"

            return _task_run_with_cli_stats(
                task_id=task.id,
                passed=False,
                message=message,
                elapsed_seconds=time.monotonic() - started,
                result=last_result,
                workspace=workspace_path if keep_workspace else None,
                stats_workspace=workspace_path,
            )

        # Unreachable — the loop above always returns. Keep a deterministic
        # fallback so static analysis doesn't complain.
        raise RuntimeError("run_task_cli retry loop fell through")
    finally:
        if workspace_keepalive is not None:
            _cleanup_workspace_keepalive(workspace_keepalive, task_id=task.id)


def run_all_cli(
    task_ids: list[str] | None = None,
    *,
    cli_command: str = DEFAULT_CLI_COMMAND,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    keep_workspace: bool = False,
    concurrency: int = 1,
    attempts: int = 1,
    json_output: str | Path | None = None,
    rerun_on_fail: bool = False,
) -> list[TaskRun]:
    """Run a subset (or all) of the benchmark via the CLI agent."""
    _load_env_from_dotenv()
    _STOP_REQUESTED.clear()
    json_output = normalize_json_output_path(json_output)
    if attempts < 1:
        raise ValueError("attempts must be positive")

    targets = [get_task(tid) for tid in task_ids] if task_ids else list(ALL_TASKS)
    results = _resume_results(
        json_output,
        targets,
        attempts,
        rerun_on_fail=rerun_on_fail,
    )
    pending_attempts = _pending_task_attempts(targets, attempts, results)
    if not pending_attempts:
        _write_partial_results_json(results, json_output)
        return results

    if concurrency <= 1:
        try:
            for task, attempt in pending_attempts:
                label = _task_attempt_label_for(task.id, attempt, attempts)
                print(f"[START] {label}: {task.name}")
                run = run_task_cli(
                    task,
                    cli_command=cli_command,
                    timeout=timeout,
                    keep_workspace=keep_workspace,
                )
                run = _mark_attempt(run, attempt, attempts)
                results.append(run)
                _write_partial_results_json(results, json_output)
                status = "PASS" if run.passed else "FAIL"
                print(f"  [{status}] {run.elapsed_seconds:5.1f}s — {_one_line_detail(run)}")
                if keep_workspace and run.workspace:
                    print(f"  workspace: {run.workspace}")
        except KeyboardInterrupt:
            _STOP_REQUESTED.set()
            _terminate_all_active_processes()
            _write_interrupted_results_json(results, json_output, pending_attempts, attempts)
            raise
        results.sort(key=lambda r: (*_task_sort_key(r.task_id), r.attempt))
        _write_partial_results_json(results, json_output)
        return results

    print_lock = threading.Lock()
    completed = len(results)
    total = len(targets) * attempts
    executor = ThreadPoolExecutor(max_workers=concurrency)
    interrupted = False
    future_to_task = {}
    try:
        future_to_task = {
            executor.submit(
                run_task_cli,
                task,
                cli_command=cli_command,
                timeout=timeout,
                keep_workspace=keep_workspace,
            ): (task, attempt)
            for task, attempt in pending_attempts
        }
        for future in as_completed(future_to_task):
            _task, attempt = future_to_task[future]
            run = _mark_attempt(future.result(), attempt, attempts)
            results.append(run)
            _write_partial_results_json(results, json_output)
            with print_lock:
                completed += 1
                status = "PASS" if run.passed else "FAIL"
                print(
                    f"[{completed:3d}/{total}] [{status}] "
                    f"{_task_attempt_label(run):40s} "
                    f"{run.elapsed_seconds:5.1f}s — {_one_line_detail(run)}"
                )
                if keep_workspace and run.workspace:
                    print(f"           workspace: {run.workspace}")
    except KeyboardInterrupt:
        interrupted = True
        _STOP_REQUESTED.set()
        _terminate_all_active_processes()
        for future in future_to_task:
            future.cancel()
        _write_interrupted_results_json(results, json_output, pending_attempts, attempts)
        raise
    finally:
        executor.shutdown(wait=not interrupted, cancel_futures=interrupted)
    results.sort(key=lambda r: (*_task_sort_key(r.task_id), r.attempt))
    _write_partial_results_json(results, json_output)
    return results


__all__ = [
    "DEFAULT_CLI_COMMAND",
    "DEFAULT_TIMEOUT_SECONDS",
    "_argv_for_workspace",
    "run_all_cli",
    "run_task_cli",
    "summarize",
]

# Keep `os` imported in case future versions need to inspect env / PATH for
# locating the CLI binary or setting per-task env vars.
_ = os
