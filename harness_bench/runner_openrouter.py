"""Run benchmark tasks against a deep agent backed by an OpenRouter model.

This runner deliberately does NOT register the `deepagents-gigachat` harness
profile — the goal is to measure `deepagents` with a third-party model
(via OpenRouter's OpenAI-compatible API) without any GigaChat-specific prompt
or tool-description overrides.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from harness_bench.core import Task
from harness_bench.openrouter_agent import _point_gigachat_profile_at as _point_gigachat_profile_at
from harness_bench.openrouter_agent import build_agent as _runtime_build_agent
from harness_bench.openrouter_agent import is_transient_model_error as _is_transient_model_error
from harness_bench.runner import (
    AgentRunStatsCollector,
    TaskRun,
    _agent_exception_task_run,
    _load_env_from_dotenv,
    _mark_attempt,
    _one_line_detail,
    _pending_task_attempts,
    _resume_results,
    _task_attempt_label,
    _task_attempt_label_for,
    _task_run_with_agent_stats,
    _task_sort_key,
    _write_interrupted_results_json,
    _write_partial_results_json,
    invoke_agent_with_stats,
    normalize_json_output_path,
)
from harness_bench.tasks import ALL_TASKS, get_task

DEFAULT_OPENROUTER_MODEL = "qwen/qwen3.6-plus"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

_OPENROUTER_TOKEN_LOCK = threading.Lock()
_OPENROUTER_AUTH_TOKEN: tuple[str, float] | None = None
_TOKEN_REFRESH_MARGIN_SECONDS = 60.0
_INTERNAL_TAGME_BASE_URL = "https://tagme.sberdevices.ru/x/ai/llm/v1"
_INTERNAL_TAGME_AUTH_URL = (
    "https://tagme.sberdevices.ru/auth/realms/tagme-public/protocol/openid-connect/token"
)
DEFAULT_TRANSIENT_ATTEMPTS = 5
_TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}



def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _env_flag(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_internal_tagme_credentials() -> tuple[str, str] | None:
    script_path = Path(
        os.getenv("OPENROUTER_INTERNAL_TAGME_TOKEN_SCRIPT", "openrouter_connect/get_token.sh")
    )
    if not script_path.exists():
        return None
    text = script_path.read_text(encoding="utf-8")
    user_match = re.search(r'^USER="([^"]+)"', text, re.MULTILINE)
    pass_match = re.search(r'^PASS="([^"]+)"', text, re.MULTILINE)
    if not user_match or not pass_match:
        return None
    return user_match.group(1), pass_match.group(1)


def _apply_internal_tagme_defaults() -> None:
    if not _env_flag("OPENROUTER_USE_INTERNAL_TAGME"):
        return
    os.environ.setdefault("OPENROUTER_BASE_URL", _INTERNAL_TAGME_BASE_URL)
    os.environ.setdefault("OPENROUTER_AUTH_URL", _INTERNAL_TAGME_AUTH_URL)
    os.environ.setdefault("OPENROUTER_AUTH_CLIENT_ID", "api")
    os.environ.setdefault("OPENROUTER_AUTH_VERIFY_TLS", "false")
    if os.getenv("OPENROUTER_AUTH_USERNAME") and os.getenv("OPENROUTER_AUTH_PASSWORD"):
        return
    credentials = _load_internal_tagme_credentials()
    if credentials is None:
        return
    username, password = credentials
    os.environ.setdefault("OPENROUTER_AUTH_USERNAME", username)
    os.environ.setdefault("OPENROUTER_AUTH_PASSWORD", password)


def _decode_jwt_exp(token: str) -> float | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception:  # noqa: BLE001 — expiry is an optimization, not correctness.
        return None
    exp = decoded.get("exp")
    return float(exp) if isinstance(exp, int | float) else None


def _openrouter_auth_ssl_context() -> ssl.SSLContext | None:
    if _env_flag("OPENROUTER_AUTH_VERIFY_TLS", default=True):
        return None
    return ssl._create_unverified_context()  # noqa: SLF001 — mirrors curl -k for private gateways.


def _fetch_openrouter_auth_token() -> tuple[str, float]:
    auth_url = os.getenv("OPENROUTER_AUTH_URL")
    username = _env_first("OPENROUTER_AUTH_USERNAME", "OPENROUTER_USERNAME")
    password = _env_first("OPENROUTER_AUTH_PASSWORD", "OPENROUTER_PASSWORD")
    if not auth_url or not username or not password:
        raise SystemExit(
            "OPENROUTER_API_KEY is not set. Put it in .env/export it, or configure "
            "OPENROUTER_AUTH_URL with OPENROUTER_AUTH_USERNAME and "
            "OPENROUTER_AUTH_PASSWORD for password-auth gateways."
        )
    form = {
        "client_id": os.getenv("OPENROUTER_AUTH_CLIENT_ID", "api"),
        "grant_type": os.getenv("OPENROUTER_AUTH_GRANT_TYPE", "password"),
        "username": username,
        "password": password,
    }
    body = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(
        auth_url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 — user-configured auth endpoint.
            request,
            timeout=float(os.getenv("OPENROUTER_AUTH_TIMEOUT", "30")),
            context=_openrouter_auth_ssl_context(),
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SystemExit(f"Failed to fetch OPENROUTER auth token: {exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Failed to fetch OPENROUTER auth token: {exc}") from exc

    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise SystemExit("OPENROUTER auth response did not contain access_token")
    now = time.time()
    expires_at = now + float(payload.get("expires_in") or 300)
    if jwt_exp := _decode_jwt_exp(token):
        expires_at = min(expires_at, jwt_exp)
    return token, expires_at


def _openrouter_api_key() -> str:
    _apply_internal_tagme_defaults()
    if not os.getenv("OPENROUTER_AUTH_URL") and (api_key := os.getenv("OPENROUTER_API_KEY")):
        return api_key
    global _OPENROUTER_AUTH_TOKEN
    with _OPENROUTER_TOKEN_LOCK:
        if _OPENROUTER_AUTH_TOKEN is not None:
            token, expires_at = _OPENROUTER_AUTH_TOKEN
            if expires_at - time.time() > _TOKEN_REFRESH_MARGIN_SECONDS:
                return token
        _OPENROUTER_AUTH_TOKEN = _fetch_openrouter_auth_token()
        return _OPENROUTER_AUTH_TOKEN[0]


def _ensure_openrouter_key() -> None:
    _apply_internal_tagme_defaults()
    _openrouter_api_key()

















def count_compactions(workspace: Path) -> int:
    """Count deepagents auto-compactions from their on-disk history offload.

    Every summarization appends one `## Summarized at …` section to
    `conversation_history/<thread>.md` in the backend, i.e. in the workspace.
    """
    history = workspace / "conversation_history"
    if not history.is_dir():
        return 0
    return sum(
        path.read_text(encoding="utf-8", errors="replace").count("## Summarized at ")
        for path in history.glob("*.md")
    )


def _dump_trace(task_id: str, invocation_result: Any) -> None:
    """Save the full message history when `HARNESS_BENCH_TRACE_DIR` is set.

    deepagents summarizes without mutating `state["messages"]`, so the final
    state still holds every turn, including the ones compaction evicted.
    """
    trace_dir = os.getenv("HARNESS_BENCH_TRACE_DIR")
    if not trace_dir or not isinstance(invocation_result, dict):
        return
    from langchain_core.messages import messages_to_dict

    out = Path(trace_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{task_id}_{time.strftime('%Y%m%dT%H%M%S')}.json"
    messages = invocation_result.get("messages") or []
    path.write_text(
        json.dumps(messages_to_dict(messages), ensure_ascii=False, default=str),
        encoding="utf-8",
    )




def build_agent(
    workspace: Path,
    *,
    model_name: str = DEFAULT_OPENROUTER_MODEL,
    recursion_limit: int = 80,
    max_tokens: int | None = None,
    harness_profile: str | None = None,
    forward_reasoning_history: bool = False,
    compact_at_tokens: int | None = None,
    prompt_cache: bool = False,
    no_subagents: bool = False,
    responses_api: bool = False,
) -> Any:
    """Build the same model used by the standalone isolated worker."""
    _apply_internal_tagme_defaults()
    return _runtime_build_agent(workspace, api_key=_openrouter_api_key(), model_name=model_name,
        recursion_limit=recursion_limit, max_tokens=max_tokens, harness_profile=harness_profile,
        forward_reasoning_history=forward_reasoning_history, compact_at_tokens=compact_at_tokens,
        prompt_cache=prompt_cache, no_subagents=no_subagents, responses_api=responses_api)


def run_task(
    task: Task,
    *,
    model_name: str = DEFAULT_OPENROUTER_MODEL,
    keep_workspace: bool = False,
    recursion_limit: int = 80,
    max_tokens: int | None = None,
    harness_profile: str | None = None,
    transient_attempts: int = DEFAULT_TRANSIENT_ATTEMPTS,
    forward_reasoning_history: bool = False,
    compact_at_tokens: int | None = None,
    prompt_cache: bool = False,
    no_subagents: bool = False,
    responses_api: bool = False,
) -> TaskRun:
    if transient_attempts < 1:
        raise ValueError("transient_attempts must be positive")
    recursion_limit = max(recursion_limit, getattr(task, "min_recursion_limit", None) or 0)

    started = time.monotonic()
    last_run: TaskRun | None = None
    for attempt in range(1, transient_attempts + 1):
        workspace_keepalive: TemporaryDirectory | None = None
        try:
            if keep_workspace and attempt == transient_attempts:
                workspace_path = Path(
                    __import__("tempfile").mkdtemp(prefix=f"hb_or_{task.id}_")
                )
            else:
                workspace_keepalive = TemporaryDirectory(prefix=f"hb_or_{task.id}_")
                workspace_path = Path(workspace_keepalive.name)

            task.setup(workspace_path)
            stats = AgentRunStatsCollector()
            try:
                agent = build_agent(
                    workspace_path,
                    model_name=model_name,
                    recursion_limit=recursion_limit,
                    max_tokens=max_tokens,
                    harness_profile=harness_profile,
                    forward_reasoning_history=forward_reasoning_history,
                    compact_at_tokens=compact_at_tokens,
                    prompt_cache=prompt_cache,
                    no_subagents=no_subagents,
                    responses_api=responses_api,
                )
                invocation_result = invoke_agent_with_stats(
                    agent,
                    {"messages": [{"role": "user", "content": task.prompt}]},
                    stats,
                    min_timeout_seconds=getattr(task, "min_timeout_seconds", None),
                )
            except Exception as exc:  # noqa: BLE001 — retry transient model failures.
                run = _agent_exception_task_run(
                    exc,
                    task_id=task.id,
                    elapsed_seconds=time.monotonic() - started,
                    recursion_limit=recursion_limit,
                    workspace=workspace_path if keep_workspace else None,
                )
                last_run = replace(
                    run,
                    **stats.merged(),
                    **stats.extra(),
                    agent_compactions=count_compactions(workspace_path),
                )
                if _is_transient_model_error(exc) and attempt < transient_attempts:
                    continue
                if _is_transient_model_error(exc):
                    return replace(
                        last_run,
                        message=(
                            last_run.message
                            or f"transient model error after {transient_attempts} attempts"
                        ),
                    )
                return last_run
            _dump_trace(task.id, invocation_result)
            compactions = count_compactions(workspace_path)
            result = task.verify(workspace_path)
            run = _task_run_with_agent_stats(
                task_id=task.id,
                passed=result.passed,
                message=result.message,
                elapsed_seconds=time.monotonic() - started,
                stats=stats.merged(invocation_result),
                workspace=workspace_path if keep_workspace else None,
            )
            return replace(run, **stats.extra(), agent_compactions=compactions)
        finally:
            if workspace_keepalive is not None:
                workspace_keepalive.cleanup()

    if last_run is not None:
        return last_run
    raise RuntimeError("run_task retry loop fell through")


def run_all(
    task_ids: list[str] | None = None,
    *,
    model_name: str = DEFAULT_OPENROUTER_MODEL,
    keep_workspace: bool = False,
    recursion_limit: int = 80,
    max_tokens: int | None = None,
    concurrency: int = 1,
    harness_profile: str | None = None,
    attempts: int = 1,
    json_output: str | Path | None = None,
    transient_attempts: int = DEFAULT_TRANSIENT_ATTEMPTS,
    fail_on_runtime_error: bool = False,
    rerun_on_fail: bool = False,
    forward_reasoning_history: bool = False,
    compact_at_tokens: int | None = None,
    prompt_cache: bool = False,
    no_subagents: bool = False,
    responses_api: bool = False,
    isolation: str = "none",
    runtime_paths: tuple[str, ...] = (),
    artifacts_root: Path | None = None,
) -> list[TaskRun]:
    _load_env_from_dotenv()
    _ensure_openrouter_key()
    json_output = normalize_json_output_path(json_output)
    if isolation == "bwrap":
        from harness_bench.runner import _task_timeout_seconds
        from harness_bench.runner_cli import run_all_cli

        if transient_attempts < 1:
            raise ValueError("transient_attempts must be positive")
        package = Path(__file__).resolve().parent
        worker = package / "deepagents_worker.py"
        command = [sys.executable, str(worker), "--model", model_name, "--recursion-limit", str(recursion_limit)]
        for flag, value in (("--max-tokens", max_tokens), ("--harness-profile", harness_profile),
                            ("--compact-at-tokens", compact_at_tokens)):
            if value is not None:
                command += [flag, str(value)]
        for flag, enabled in (("--forward-reasoning-history", forward_reasoning_history),
                              ("--prompt-cache", prompt_cache), ("--no-subagents", no_subagents),
                              ("--responses-api", responses_api)):
            if enabled:
                command.append(flag)
        # Exact worker source files only, plus interpreter/site-packages. No
        # task registry, verifier, benchmark repo or historical artifacts mount.
        runtime = tuple(dict.fromkeys((*runtime_paths, sys.prefix, sys.base_prefix,
            *(str(package / name) for name in ("deepagents_worker.py", "openrouter_agent.py", "chat_openai.py", "agent_stats.py")))))
        return run_all_cli(task_ids, cli_command=shlex.join(command),
            timeout=int(_task_timeout_seconds()), keep_workspace=keep_workspace,
            concurrency=concurrency, attempts=attempts, json_output=json_output,
            rerun_on_fail=rerun_on_fail, isolation=isolation, runtime_paths=runtime,
            artifacts_root=artifacts_root, transient_retries=transient_attempts-1,
            fail_on_runtime_error=fail_on_runtime_error,
            extra_env_factory=lambda: {"HBF_WORKER_API_KEY": _openrouter_api_key()})
    if isolation != "none":
        raise ValueError(f"unknown isolation mode: {isolation}")

    if attempts < 1:
        raise ValueError("attempts must be positive")

    targets = [get_task(tid) for tid in task_ids] if task_ids else list(ALL_TASKS)
    results = _resume_results(
        json_output,
        targets,
        attempts,
        rerun_on_fail=rerun_on_fail,
    )
    if fail_on_runtime_error and any(run.error for run in results):
        _write_partial_results_json(results, json_output)
        return results
    pending_attempts = _pending_task_attempts(targets, attempts, results)
    if not pending_attempts:
        _write_partial_results_json(results, json_output)
        return results

    if concurrency <= 1:
        try:
            for task, attempt in pending_attempts:
                label = _task_attempt_label_for(task.id, attempt, attempts)
                print(f"[START] {label}: {task.name}")
                run = run_task(
                    task,
                    model_name=model_name,
                    keep_workspace=keep_workspace,
                    recursion_limit=recursion_limit,
                    max_tokens=max_tokens,
                    harness_profile=harness_profile,
                    transient_attempts=transient_attempts,
                    forward_reasoning_history=forward_reasoning_history,
                    compact_at_tokens=compact_at_tokens,
                    prompt_cache=prompt_cache,
                    no_subagents=no_subagents,
                    responses_api=responses_api,
                )
                run = _mark_attempt(run, attempt, attempts)
                results.append(run)
                _write_partial_results_json(results, json_output)
                status = "PASS" if run.passed else "FAIL"
                print(f"  [{status}] {run.elapsed_seconds:5.1f}s — {_one_line_detail(run)}")
                if keep_workspace and run.workspace:
                    print(f"  workspace: {run.workspace}")
                if fail_on_runtime_error and run.error:
                    results.sort(key=lambda r: (*_task_sort_key(r.task_id), r.attempt))
                    _write_partial_results_json(results, json_output)
                    return results
        except KeyboardInterrupt:
            _write_interrupted_results_json(results, json_output, pending_attempts, attempts)
            raise
        results.sort(key=lambda r: (*_task_sort_key(r.task_id), r.attempt))
        _write_partial_results_json(results, json_output)
        return results

    print_lock = threading.Lock()
    completed = len(results)
    total = len(targets) * attempts
    executor = ThreadPoolExecutor(max_workers=concurrency)
    stop_without_wait = False
    future_to_task = {}
    try:
        future_to_task = {
            executor.submit(
                run_task,
                task,
                model_name=model_name,
                keep_workspace=keep_workspace,
                recursion_limit=recursion_limit,
                max_tokens=max_tokens,
                harness_profile=harness_profile,
                transient_attempts=transient_attempts,
                forward_reasoning_history=forward_reasoning_history,
                compact_at_tokens=compact_at_tokens,
                prompt_cache=prompt_cache,
                no_subagents=no_subagents,
                responses_api=responses_api,
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
            if fail_on_runtime_error and run.error:
                stop_without_wait = True
                for pending_future in future_to_task:
                    if pending_future is not future:
                        pending_future.cancel()
                results.sort(key=lambda r: (*_task_sort_key(r.task_id), r.attempt))
                _write_partial_results_json(results, json_output)
                return results
    except KeyboardInterrupt:
        stop_without_wait = True
        for future in future_to_task:
            future.cancel()
        _write_interrupted_results_json(results, json_output, pending_attempts, attempts)
        raise
    finally:
        executor.shutdown(wait=not stop_without_wait, cancel_futures=stop_without_wait)
    results.sort(key=lambda r: (*_task_sort_key(r.task_id), r.attempt))
    _write_partial_results_json(results, json_output)
    return results
