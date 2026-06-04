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
import ssl
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
from harness_bench.runner import (
    AgentRunStatsCollector,
    TaskRun,
    _agent_exception_task_run,
    _load_env_from_dotenv,
    _mark_attempt,
    _one_line_detail,
    _task_attempt_label,
    _task_attempt_label_for,
    _task_run_with_agent_stats,
    _task_sort_key,
    _write_partial_results_json,
    invoke_agent_with_stats,
)
from harness_bench.tasks import ALL_TASKS, get_task

DEFAULT_OPENROUTER_MODEL = "qwen/qwen3.6-plus"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# OpenRouter models are constructed as `ChatOpenAI`, so deepagents resolves
# their harness key under the `openai:` provider prefix (e.g.
# `openai:anthropic/claude-sonnet-4.6`) rather than the model's native
# Anthropic key. A built-in profile registered under `anthropic:...` therefore
# never auto-applies here. `--harness-profile` bridges that gap: it copies a
# registered source profile onto the model's resolved key so it applies.
_PROFILE_LOCK = threading.Lock()
_APPLIED_PROFILE_KEYS: set[tuple[str, str]] = set()
_OPENROUTER_TOKEN_LOCK = threading.Lock()
_OPENROUTER_AUTH_TOKEN: tuple[str, float] | None = None
_TOKEN_REFRESH_MARGIN_SECONDS = 60.0
_INTERNAL_TAGME_BASE_URL = "https://tagme.sberdevices.ru/x/ai/llm/v1"
_INTERNAL_TAGME_AUTH_URL = (
    "https://tagme.sberdevices.ru/auth/realms/tagme-public/protocol/openid-connect/token"
)
DEFAULT_TRANSIENT_ATTEMPTS = 5
_TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}

# `LocalShellBackend(virtual_mode=True)` virtualizes the file tools (the model
# writes `/x`, which maps to `<workspace>/x`) but NOT the `execute` shell, which
# runs a real shell rooted at the absolute workspace path. A model that does
# rename/move/delete via the shell with the same `/x` convention (`rm /old.txt`)
# therefore hits the real system root, silently no-ops, and the agent loops
# until the recursion limit. This one-line tool-description override tells the
# model the shell's cwd IS the workspace, which closes the gap (measured
# +14 tasks for Claude Sonnet 4.6 on this bench: 202 -> 216 / 231).
_EXECUTE_CWD_OVERRIDE = (
    "Run ONE shell command. Its working directory IS the workspace root, so use "
    "paths RELATIVE to the current directory for filesystem operations the file "
    "tools cannot do — delete/rename/move/mkdir (e.g. 'rm old.txt', 'mv a b', "
    "'rm -r dir', 'mkdir -p sub'). NEVER prefix a path with '/'. Prefer "
    "write_file / edit_file for creating or changing file content."
)


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


def _is_transient_model_error(exc: BaseException) -> bool:
    try:
        import httpx
        import openai
    except ImportError:
        httpx = None  # type: ignore[assignment]
        openai = None  # type: ignore[assignment]

    if openai is not None:
        transient_openai_errors = tuple(
            error_type
            for name in (
                "APIConnectionError",
                "APITimeoutError",
                "RateLimitError",
                "InternalServerError",
            )
            if (error_type := getattr(openai, name, None)) is not None
        )
        if transient_openai_errors and isinstance(exc, transient_openai_errors):
            return True
        api_status_error = getattr(openai, "APIStatusError", None)
        if api_status_error is not None and isinstance(exc, api_status_error):
            return exc.status_code in _TRANSIENT_STATUS_CODES

    if httpx is not None and isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True

    return exc.__class__.__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "ReadTimeout",
        "ConnectTimeout",
        "TimeoutException",
    }


def _apply_source_harness_profile(model: Any, profile_spec: str) -> None:
    """Bridge a registered built-in harness profile onto `model`'s resolved key.

    `profile_spec` is the key a profile is registered under in deepagents'
    harness registry (e.g. `anthropic:claude-sonnet-4-6` for the built-in
    Claude Sonnet 4.6 profile). Because this runner builds OpenRouter models as
    `ChatOpenAI`, deepagents would resolve them under `openai:<model>` and miss
    the Anthropic-keyed built-in. We look up the source profile and re-register
    it under the model's actual `provider:identifier` key so `create_deep_agent`
    picks it up. Registration is global and idempotent per (source, target).
    """
    from deepagents import register_harness_profile
    from deepagents._models import get_model_identifier, get_model_provider
    from deepagents.profiles.harness.harness_profiles import (
        _ensure_harness_profiles_loaded,
        _get_harness_profile,
    )

    _ensure_harness_profiles_loaded()
    source = _get_harness_profile(profile_spec)
    if source is None:
        raise SystemExit(
            f"No registered harness profile found under {profile_spec!r}. "
            "Pass a built-in spec such as 'anthropic:claude-sonnet-4-6'."
        )
    provider = get_model_provider(model)
    identifier = get_model_identifier(model)
    if not provider or not identifier:
        raise SystemExit(
            "Could not derive provider/identifier from the model to apply "
            f"harness profile {profile_spec!r}."
        )
    target_key = f"{provider}:{identifier}"
    with _PROFILE_LOCK:
        if (profile_spec, target_key) in _APPLIED_PROFILE_KEYS:
            return
        register_harness_profile(target_key, source)
        _APPLIED_PROFILE_KEYS.add((profile_spec, target_key))


def _apply_execute_cwd_fix(model: Any) -> None:
    """Register the `execute` cwd-relative override onto `model`'s resolved key.

    Works around the `virtual_mode=True` file-tool/shell split described on
    `_EXECUTE_CWD_OVERRIDE`. Registered additively, so it composes with any
    `--harness-profile` the caller also bridges onto the same key. Idempotent
    per resolved key, and a no-op when provider/identifier cannot be derived.
    """
    from deepagents import HarnessProfile, register_harness_profile
    from deepagents._models import get_model_identifier, get_model_provider

    provider = get_model_provider(model)
    identifier = get_model_identifier(model)
    if not provider or not identifier:
        return
    target_key = f"{provider}:{identifier}"
    with _PROFILE_LOCK:
        if ("__execute_cwd_fix__", target_key) in _APPLIED_PROFILE_KEYS:
            return
        register_harness_profile(
            target_key,
            HarnessProfile(tool_description_overrides={"execute": _EXECUTE_CWD_OVERRIDE}),
        )
        _APPLIED_PROFILE_KEYS.add(("__execute_cwd_fix__", target_key))


def build_agent(
    workspace: Path,
    *,
    model_name: str = DEFAULT_OPENROUTER_MODEL,
    recursion_limit: int = 80,
    max_tokens: int | None = None,
    harness_profile: str | None = None,
) -> Any:
    """Build a stock `deepagents` agent backed by an OpenRouter model.

    No `register_harness()` call — the GigaChat-specific prompt / overrides are
    intentionally bypassed. The `execute` cwd-relative override
    (`_EXECUTE_CWD_OVERRIDE`) is always applied to fix the `virtual_mode=True`
    shell/file-tool path split. When `harness_profile` is set, a registered
    built-in deepagents harness profile (e.g. `anthropic:claude-sonnet-4-6`) is
    additionally bridged onto the model's resolved key.
    """
    from deepagents import create_deep_agent
    from deepagents.backends import LocalShellBackend
    from langchain_openai import ChatOpenAI

    _apply_internal_tagme_defaults()
    backend = LocalShellBackend(
        root_dir=workspace,
        virtual_mode=True,
        inherit_env=True,
    )
    model_kwargs: dict[str, Any] = {}
    if max_tokens is not None:
        model_kwargs["max_tokens"] = max_tokens
    model = ChatOpenAI(
        model=model_name,
        base_url=os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
        api_key=_openrouter_api_key(),
        timeout=600,
        **model_kwargs,
    )
    # Always close the virtual_mode shell/file-tool path split (see
    # `_EXECUTE_CWD_OVERRIDE`); merges additively with any bridged profile.
    _apply_execute_cwd_fix(model)
    if harness_profile:
        _apply_source_harness_profile(model, harness_profile)
    # Memory tasks (222–231) ship an AGENTS.md fixture; pre-existing 221
    # tasks do not. `LocalShellBackend(virtual_mode=True)` maps
    # `/AGENTS.md` to `<workspace>/AGENTS.md`.
    memory_sources = ["/AGENTS.md"] if (workspace / "AGENTS.md").exists() else None
    agent = create_deep_agent(model=model, backend=backend, memory=memory_sources)
    return agent.with_config({"recursion_limit": recursion_limit})


def run_task(
    task: Task,
    *,
    model_name: str = DEFAULT_OPENROUTER_MODEL,
    keep_workspace: bool = False,
    recursion_limit: int = 80,
    max_tokens: int | None = None,
    harness_profile: str | None = None,
    transient_attempts: int = DEFAULT_TRANSIENT_ATTEMPTS,
) -> TaskRun:
    if transient_attempts < 1:
        raise ValueError("transient_attempts must be positive")

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
                )
                invocation_result = invoke_agent_with_stats(
                    agent,
                    {"messages": [{"role": "user", "content": task.prompt}]},
                    stats,
                )
            except Exception as exc:  # noqa: BLE001 — retry transient model failures.
                run = _agent_exception_task_run(
                    exc,
                    task_id=task.id,
                    elapsed_seconds=time.monotonic() - started,
                    recursion_limit=recursion_limit,
                    workspace=workspace_path if keep_workspace else None,
                )
                last_run = replace(run, **stats.merged())
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
            result = task.verify(workspace_path)
            return _task_run_with_agent_stats(
                task_id=task.id,
                passed=result.passed,
                message=result.message,
                elapsed_seconds=time.monotonic() - started,
                stats=stats.merged(invocation_result),
                workspace=workspace_path if keep_workspace else None,
            )
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
) -> list[TaskRun]:
    _load_env_from_dotenv()
    _ensure_openrouter_key()

    if attempts < 1:
        raise ValueError("attempts must be positive")

    targets = [get_task(tid) for tid in task_ids] if task_ids else list(ALL_TASKS)

    if concurrency <= 1:
        results: list[TaskRun] = []
        for task in targets:
            for attempt in range(1, attempts + 1):
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
                )
                run = _mark_attempt(run, attempt, attempts)
                results.append(run)
                _write_partial_results_json(results, json_output)
                status = "PASS" if run.passed else "FAIL"
                print(f"  [{status}] {run.elapsed_seconds:5.1f}s — {_one_line_detail(run)}")
                if keep_workspace and run.workspace:
                    print(f"  workspace: {run.workspace}")
        return results

    print_lock = threading.Lock()
    completed = 0
    total = len(targets) * attempts
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
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
            ): (task, attempt)
            for task in targets
            for attempt in range(1, attempts + 1)
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
    results.sort(key=lambda r: (*_task_sort_key(r.task_id), r.attempt))
    _write_partial_results_json(results, json_output)
    return results
