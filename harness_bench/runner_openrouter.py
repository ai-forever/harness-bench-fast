"""Run benchmark tasks against a deep agent backed by an OpenRouter model.

This runner deliberately does NOT register the `deepagents-gigachat` harness
profile — the goal is to measure `deepagents` with a third-party model
(via OpenRouter's OpenAI-compatible API) without any GigaChat-specific prompt
or tool-description overrides.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from harness_bench.core import Task
from harness_bench.runner import (
    TaskRun,
    _agent_exception_task_run,
    _load_env_from_dotenv,
    _one_line_detail,
    _task_sort_key,
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


def _ensure_openrouter_key() -> None:
    if not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit(
            "OPENROUTER_API_KEY is not set. Put it in .env or export it before running."
        )


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
        api_key=os.getenv("OPENROUTER_API_KEY"),
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
) -> TaskRun:
    workspace_keepalive: TemporaryDirectory | None = None
    try:
        if keep_workspace:
            workspace_path = Path(__import__("tempfile").mkdtemp(prefix=f"hb_or_{task.id}_"))
        else:
            workspace_keepalive = TemporaryDirectory(prefix=f"hb_or_{task.id}_")
            workspace_path = Path(workspace_keepalive.name)

        task.setup(workspace_path)
        started = time.monotonic()
        try:
            agent = build_agent(
                workspace_path,
                model_name=model_name,
                recursion_limit=recursion_limit,
                max_tokens=max_tokens,
                harness_profile=harness_profile,
            )
            agent.invoke({"messages": [{"role": "user", "content": task.prompt}]})
        except Exception as exc:  # noqa: BLE001 — surface as task failure
            return _agent_exception_task_run(
                exc,
                task_id=task.id,
                elapsed_seconds=time.monotonic() - started,
                recursion_limit=recursion_limit,
                workspace=workspace_path if keep_workspace else None,
            )
        result = task.verify(workspace_path)
        return TaskRun(
            task_id=task.id,
            passed=result.passed,
            message=result.message,
            elapsed_seconds=time.monotonic() - started,
            workspace=workspace_path if keep_workspace else None,
        )
    finally:
        if workspace_keepalive is not None:
            workspace_keepalive.cleanup()


def run_all(
    task_ids: list[str] | None = None,
    *,
    model_name: str = DEFAULT_OPENROUTER_MODEL,
    keep_workspace: bool = False,
    recursion_limit: int = 80,
    max_tokens: int | None = None,
    concurrency: int = 1,
    harness_profile: str | None = None,
) -> list[TaskRun]:
    _load_env_from_dotenv()
    _ensure_openrouter_key()

    targets = [get_task(tid) for tid in task_ids] if task_ids else list(ALL_TASKS)

    if concurrency <= 1:
        results: list[TaskRun] = []
        for task in targets:
            print(f"[START] {task.id}: {task.name}")
            run = run_task(
                task,
                model_name=model_name,
                keep_workspace=keep_workspace,
                recursion_limit=recursion_limit,
                max_tokens=max_tokens,
                harness_profile=harness_profile,
            )
            results.append(run)
            status = "PASS" if run.passed else "FAIL"
            print(f"  [{status}] {run.elapsed_seconds:5.1f}s — {_one_line_detail(run)}")
            if keep_workspace and run.workspace:
                print(f"  workspace: {run.workspace}")
        return results

    print_lock = threading.Lock()
    completed = 0
    total = len(targets)
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
            ): task
            for task in targets
        }
        for future in as_completed(future_to_task):
            run = future.result()
            results.append(run)
            with print_lock:
                completed += 1
                status = "PASS" if run.passed else "FAIL"
                print(
                    f"[{completed:3d}/{total}] [{status}] {run.task_id:32s} "
                    f"{run.elapsed_seconds:5.1f}s — {_one_line_detail(run)}"
                )
                if keep_workspace and run.workspace:
                    print(f"           workspace: {run.workspace}")
    results.sort(key=lambda r: _task_sort_key(r.task_id))
    return results
