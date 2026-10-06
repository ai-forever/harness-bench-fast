"""Deepagents model construction only; safe to expose inside task namespaces."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

DEFAULT_OPENROUTER_MODEL = "qwen/qwen3.6-plus"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
_PROFILE_LOCK = threading.Lock()
_APPLIED_PROFILE_KEYS: set[tuple[str, str]] = set()
_COMPACTION_LOCK = threading.Lock()
_COMPACTION_INSTALLED: int | None = None
_EXECUTE_CWD_OVERRIDE = (
    "Run ONE shell command. Its working directory IS the workspace root, so use "
    "paths RELATIVE to the current directory for filesystem operations the file "
    "tools cannot do — delete/rename/move/mkdir (e.g. 'rm old.txt', 'mv a b', "
    "'rm -r dir', 'mkdir -p sub'). NEVER prefix a path with '/'. Prefer "
    "write_file / edit_file for creating or changing file content."
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


def _disable_subagents(model: Any) -> None:
    """Drop deepagents' auto-added general-purpose subagent (and the `task` tool).

    With it, a strong model fans bulk reading out to parallel subagents, so no
    single context grows; without it the whole trajectory stays in one agent.
    Registered additively on the model's key, like the `execute` override.
    """
    from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile
    from deepagents._models import get_model_identifier, get_model_provider

    provider = get_model_provider(model)
    identifier = get_model_identifier(model)
    if not provider or not identifier:
        return
    target_key = f"{provider}:{identifier}"
    with _PROFILE_LOCK:
        if ("__no_subagents__", target_key) in _APPLIED_PROFILE_KEYS:
            return
        register_harness_profile(
            target_key,
            HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
        )
        _APPLIED_PROFILE_KEYS.add(("__no_subagents__", target_key))


def real_token_count(messages: Any, *, tools: Any = None) -> int:
    """Prompt size in provider tokens, for deepagents' summarization trigger.

    deepagents' default counter assumes ~4 characters per token and calibrates
    only the message part, so on Russian-heavy tool output its "128K" is about
    230K real tokens. Here the base is the provider-reported usage of the
    latest model call plus an estimate of what was appended after it, scaled by
    the same call's tokens-per-estimate ratio. Right after a compaction that
    call's usage describes the old, longer context (its ratio against the new
    prefix is implausible), so the count falls back to an estimate at 3
    characters per token until the next call reports fresh usage.
    """
    from langchain_core.messages.utils import count_tokens_approximately as approx

    msgs = list(messages)
    for i in range(len(msgs) - 1, -1, -1):
        usage = getattr(msgs[i], "usage_metadata", None)
        if getattr(msgs[i], "type", "") == "ai" and usage and usage.get("total_tokens"):
            ratio = usage["total_tokens"] / max(approx(msgs[: i + 1], tools=tools), 1)
            if 0.8 <= ratio <= 4.0:
                return int(usage["total_tokens"] + approx(msgs[i + 1 :]) * ratio)
            break
    return int(approx(msgs, tools=tools, chars_per_token=3.0))


def _install_compaction_threshold(compact_at_tokens: int) -> None:
    """Make deepagents auto-compact once a prompt reaches `compact_at_tokens`.

    Replaces the factory `create_deep_agent` uses for its summarization
    middleware (main agent and general-purpose subagent alike) with one that
    triggers at `compact_at_tokens` real tokens (`real_token_count`), keeps
    the most recent ~12% of that verbatim, and otherwise keeps deepagents'
    behavior (history offload to `/conversation_history/`, tool-argument
    truncation). Process-wide, so one run uses one threshold.
    """
    global _COMPACTION_INSTALLED
    import deepagents.graph as graph
    from deepagents.middleware.summarization import SummarizationMiddleware

    with _COMPACTION_LOCK:
        if compact_at_tokens == _COMPACTION_INSTALLED:
            return
        if _COMPACTION_INSTALLED is not None:
            raise RuntimeError("one process can use only one --compact-at-tokens value")
        keep = max(1000, compact_at_tokens // 8)

        def factory(model: Any, backend: Any, **_: Any) -> Any:
            return SummarizationMiddleware(
                model=model,
                backend=backend,
                trigger=("tokens", compact_at_tokens),
                keep=("tokens", keep),
                token_counter=real_token_count,
                truncate_args_settings={
                    "trigger": ("tokens", compact_at_tokens),
                    "keep": ("tokens", keep),
                },
            )

        graph.create_summarization_middleware = factory
        _COMPACTION_INSTALLED = compact_at_tokens


def _point_gigachat_profile_at(workspace: Path) -> None:
    """Tell the bridged GigaChat profile which workspace this task runs in.

    Part of that profile reads the current workspace off a module global rather
    than off agent state: `MemoryTaskMiddleware` gates its nudge on
    `<workspace>/AGENTS.md` existing. `runner.py` sets it per task; this runner
    did not, so a bridged `gigachat` profile ran with the middleware silently
    inert and measured the memory wave in a weaker configuration than the
    native runner does.

    Only called when a profile was explicitly bridged, so profile-less
    OpenRouter runs are unaffected.
    """
    try:
        from deepagents_gigachat import set_workspace_path
    except ImportError:
        return
    set_workspace_path(workspace)


def build_agent(
    workspace: Path,
    *,
    api_key: str,
    model_name: str = DEFAULT_OPENROUTER_MODEL,
    recursion_limit: int = 80,
    max_tokens: int | None = None,
    harness_profile: str | None = None,
    forward_reasoning_history: bool = False,
    compact_at_tokens: int | None = None,
    prompt_cache: bool = False,
    no_subagents: bool = False,
    responses_api: bool = False,
    backend: Any | None = None,
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

    if __package__:
        from .chat_openai import ReasoningAwareChatOpenAI
    else:
        from chat_openai import ReasoningAwareChatOpenAI

    if backend is None:
        backend = LocalShellBackend(
            root_dir=workspace,
            virtual_mode=True,
            inherit_env=True,
        )
        has_memory = (workspace / "AGENTS.md").exists()
        has_skills = (workspace / ".agents" / "skills").is_dir()
    else:
        # RLI reports these after setup inside the session; the local path is not the workspace.
        has_memory = bool(getattr(backend, "memory", False))
        has_skills = bool(getattr(backend, "skills", False))
    model_kwargs: dict[str, Any] = {}
    if max_tokens is not None:
        model_kwargs["max_tokens"] = max_tokens
    if effort := os.getenv("OPENROUTER_REASONING_EFFORT"):
        if effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            raise ValueError(f"Unsupported OPENROUTER_REASONING_EFFORT: {effort}")
        model_kwargs["reasoning_effort"] = effort
    if prompt_cache:
        # OpenRouter-style gateways cache the growing prefix for Anthropic
        # models only when asked; this does not change what the model sees.
        model_kwargs["extra_body"] = {"cache_control": {"type": "ephemeral"}}
    if responses_api:
        # Some reasoning models (gpt-6-luna on 2026-09-29) reject any
        # reasoning_effort except "none" together with function tools on Chat
        # Completions, even when the field is omitted; the Responses API takes
        # both. Reasoning items come back encrypted (store=False) and are
        # replayed on every turn, which is how that API carries them forward,
        # so --forward-reasoning-history does not apply here.
        model_kwargs.update(
            use_responses_api=True,
            output_version="responses/v1",
            store=False,
            include=["reasoning.encrypted_content"],
        )
        if effort := model_kwargs.pop("reasoning_effort", None):
            model_kwargs["reasoning"] = {"effort": effort}
    model = ReasoningAwareChatOpenAI(
        model=model_name,
        base_url=os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
        api_key=api_key,
        timeout=float(os.getenv("HARNESS_BENCH_REQUEST_TIMEOUT", "600")),
        # Per-request retries (openai client backoff) for 429/5xx; a failure that
        # survives them restarts the whole task via `transient_attempts`.
        max_retries=int(os.getenv("OPENROUTER_MAX_RETRIES", "2")),
        forward_reasoning_history=forward_reasoning_history,
        **model_kwargs,
    )
    # Always close the virtual_mode shell/file-tool path split (see
    # `_EXECUTE_CWD_OVERRIDE`); merges additively with any bridged profile.
    _apply_execute_cwd_fix(model)
    if compact_at_tokens is not None:
        _install_compaction_threshold(compact_at_tokens)
    if no_subagents:
        _disable_subagents(model)
    if harness_profile:
        _apply_source_harness_profile(model, harness_profile)
        _point_gigachat_profile_at(workspace)
    # Memory tasks (222–231) ship an AGENTS.md fixture; pre-existing 221
    # tasks do not. `LocalShellBackend(virtual_mode=True)` maps
    # `/AGENTS.md` to `<workspace>/AGENTS.md`.
    memory_sources = ["/AGENTS.md"] if has_memory else None
    # Skill tasks ship `.agents/skills/`; wire SkillsMiddleware in only when
    # present so the skill-less tasks stay unchanged (see runner.build_agent).
    skill_sources = ["/.agents/skills"] if has_skills else None
    agent = create_deep_agent(
        model=model, backend=backend, memory=memory_sources, skills=skill_sources
    )
    return agent.with_config({"recursion_limit": recursion_limit})


def is_transient_model_error(exc: BaseException) -> bool:
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
