"""Model usage collection with no task registry or verifier dependency."""

from __future__ import annotations

from typing import Any


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _usage_token_counts(usage: Any) -> tuple[int | None, int | None, int | None]:
    if not isinstance(usage, dict):
        return None, None, None

    input_tokens = (
        _coerce_int(usage.get("input_tokens"))
        or _coerce_int(usage.get("prompt_tokens"))
        or _coerce_int(usage.get("prompt_eval_count"))
    )
    # Anthropic (Claude Code) bills cached context under separate cache fields;
    # `input_tokens` is only the fresh, non-cached delta. Fold the cache reads
    # and cache creation back in so token totals reflect the real context size.
    cache_tokens = (_coerce_int(usage.get("cache_read_input_tokens")) or 0) + (
        _coerce_int(usage.get("cache_creation_input_tokens")) or 0
    )
    if cache_tokens:
        input_tokens = (input_tokens or 0) + cache_tokens
    output_tokens = (
        _coerce_int(usage.get("output_tokens"))
        or _coerce_int(usage.get("completion_tokens"))
        or _coerce_int(usage.get("eval_count"))
    )
    total_tokens = _coerce_int(usage.get("total_tokens"))
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return input_tokens, output_tokens, total_tokens


def _add_usage_counts(stats: dict[str, int], usage: Any) -> None:
    input_tokens, output_tokens, total_tokens = _usage_token_counts(usage)
    if input_tokens is not None:
        stats["agent_input_tokens"] = stats.get("agent_input_tokens", 0) + input_tokens
    if output_tokens is not None:
        stats["agent_output_tokens"] = stats.get("agent_output_tokens", 0) + output_tokens
    if total_tokens is not None:
        stats["agent_total_tokens"] = stats.get("agent_total_tokens", 0) + total_tokens


def _message_role(message: Any) -> str | None:
    role = getattr(message, "type", None) or getattr(message, "role", None)
    if isinstance(message, dict):
        role = message.get("type") or message.get("role")
    return role if isinstance(role, str) else None


def _message_tool_calls(message: Any) -> list[Any]:
    tool_calls = getattr(message, "tool_calls", None)
    if isinstance(message, dict):
        tool_calls = message.get("tool_calls", tool_calls)
    return tool_calls if isinstance(tool_calls, list) else []


def _tool_call_name(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        name = tool_call.get("name") or tool_call.get("function", {}).get("name")
        return name if isinstance(name, str) else ""
    name = getattr(tool_call, "name", "")
    return name if isinstance(name, str) else ""


def _message_usage(message: Any) -> list[Any]:
    usages: list[Any] = []
    usage_metadata = getattr(message, "usage_metadata", None)
    if isinstance(message, dict):
        usage_metadata = message.get("usage_metadata", usage_metadata)
    if usage_metadata:
        usages.append(usage_metadata)

    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(message, dict):
        response_metadata = message.get("response_metadata", response_metadata)
    if isinstance(response_metadata, dict):
        for key in ("token_usage", "usage", "usage_metadata"):
            if response_metadata.get(key):
                usages.append(response_metadata[key])
    return usages


def agent_stats_from_result(invocation_result: Any) -> dict[str, int]:
    """Best-effort step/token extraction from a LangGraph/deepagents result."""
    stats: dict[str, int] = {}
    if not isinstance(invocation_result, dict):
        return stats

    messages = invocation_result.get("messages")
    if not isinstance(messages, list):
        return stats

    events = len(messages)
    steps = 0
    tool_calls = 0
    shell_commands = 0
    llm_calls = 0
    for message in messages:
        role = _message_role(message)
        if role not in ("human", "user", "system"):
            steps += 1
        calls = _message_tool_calls(message)
        if calls:
            tool_calls += len(calls)
            llm_calls += 1
            for call in calls:
                if _tool_call_name(call) == "execute":
                    shell_commands += 1
        elif role in ("ai", "assistant"):
            llm_calls += 1
        for usage in _message_usage(message):
            _add_usage_counts(stats, usage)

    stats["agent_events"] = events
    stats["agent_steps"] = steps
    stats["agent_tool_calls"] = tool_calls
    stats["agent_shell_commands"] = shell_commands
    stats["agent_llm_calls"] = llm_calls
    return stats


class AgentRunStatsCollector:
    """LangChain callback collector for token usage emitted during agent runs."""

    def __init__(self) -> None:
        self.stats: dict[str, int] = {}
        self.peak_input_tokens = 0
        self.cost_usd: float | None = None
        self._endpoint_errors: list[BaseException] = []

    def as_callback(self) -> Any:
        try:
            from langchain_core.callbacks import BaseCallbackHandler
        except ImportError:
            return None

        outer = self

        class _Handler(BaseCallbackHandler):
            def on_llm_error(self, error: BaseException, **_kwargs: Any) -> None:
                if _is_endpoint_unavailable(error):
                    outer._endpoint_errors.append(error)

            def on_llm_end(self, response: Any, **_kwargs: Any) -> None:
                outer.stats["agent_llm_calls"] = outer.stats.get("agent_llm_calls", 0) + 1
                usages: list[Any] = []
                llm_output = getattr(response, "llm_output", None)
                if isinstance(llm_output, dict):
                    _add_usage_counts(outer.stats, llm_output.get("token_usage"))
                    _add_usage_counts(outer.stats, llm_output.get("usage"))
                    usages += [llm_output.get("token_usage"), llm_output.get("usage")]
                for generations in getattr(response, "generations", []) or []:
                    for generation in generations:
                        message = getattr(generation, "message", None)
                        if message is not None:
                            for usage in _message_usage(message):
                                _add_usage_counts(outer.stats, usage)
                                usages.append(usage)
                outer._observe_call(usages)

        return _Handler()

    def endpoint_unavailable(self, error: BaseException | None) -> bool:
        """Only errors observed at the model boundary can invalidate a run."""
        seen: set[int] = set()
        while error is not None and id(error) not in seen:
            if any(error is observed for observed in self._endpoint_errors):
                return True
            seen.add(id(error))
            error = error.__cause__ or error.__context__
        return False

    def _observe_call(self, usages: list[Any]) -> None:
        """Track the peak single-call prompt and the gateway cost of one call.

        The same usage often appears several times per call (raw `token_usage`
        plus `usage_metadata`), so the call's prompt is the max over them and
        its cost is taken once.
        """
        call_input = max(
            (tokens for usage in usages if (tokens := _usage_token_counts(usage)[0])),
            default=0,
        )
        if call_input > self.peak_input_tokens:
            self.peak_input_tokens = call_input
        for usage in usages:
            if isinstance(usage, dict) and isinstance(usage.get("cost"), int | float):
                self.cost_usd = (self.cost_usd or 0.0) + float(usage["cost"])
                break

    def extra(self) -> dict[str, Any]:
        """Peak prompt / cost fields for `TaskRun`, when they were observed."""
        extra: dict[str, Any] = {}
        if self.peak_input_tokens:
            extra["agent_peak_input_tokens"] = self.peak_input_tokens
        if self.cost_usd is not None:
            extra["agent_cost_usd"] = round(self.cost_usd, 6)
        return extra

    def merged(self, invocation_result: Any | None = None) -> dict[str, int]:
        stats = dict(self.stats)
        if invocation_result is not None:
            for key, value in agent_stats_from_result(invocation_result).items():
                if key.startswith("agent_") and key.endswith("_tokens") or key == "agent_llm_calls":
                    stats[key] = max(stats.get(key, 0), value)
                else:
                    stats[key] = value
        return stats


def _is_endpoint_unavailable(error: BaseException) -> bool:
    """Connection/access/overload failures, excluding invalid model requests."""
    import httpx

    if isinstance(error, httpx.TransportError):
        return True
    status_code = None
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
    try:
        import openai
    except ImportError:
        pass
    else:
        if isinstance(error, openai.APIConnectionError):
            return True
        if isinstance(error, openai.APIStatusError):
            status_code = error.status_code
    try:
        from gigachat.exceptions import ResponseError
    except ImportError:
        pass
    else:
        if isinstance(error, ResponseError):
            status_code = error.status_code
    return status_code is not None and (
        status_code in {401, 403, 404, 408, 409, 429} or 500 <= status_code < 600
    )
