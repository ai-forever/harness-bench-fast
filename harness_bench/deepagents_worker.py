"""Standalone deepagents CLI worker. This file never imports benchmark tasks.

Invoked by run-openrouter's per-task sandbox; prompt is the final argument.
Only this file and its three runtime helpers need to be visible to the worker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False, default=str), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--recursion-limit", type=int, default=80)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--harness-profile")
    parser.add_argument("--forward-reasoning-history", action="store_true")
    parser.add_argument("--compact-at-tokens", type=int)
    parser.add_argument("--prompt-cache", action="store_true")
    parser.add_argument("--no-subagents", action="store_true")
    parser.add_argument("prompt")
    args = parser.parse_args()
    stats = None
    try:
        from agent_stats import AgentRunStatsCollector
        from langchain_core.callbacks import BaseCallbackHandler
        from langchain_core.messages import messages_to_dict
        from openrouter_agent import build_agent

        class Evidence(BaseCallbackHandler):
            raise_error = True

            def on_chat_model_start(self, serialized, messages, **kwargs):
                emit(
                    {
                        "type": "hbf_model_input",
                        "messages": [messages_to_dict(batch) for batch in messages],
                    }
                )

            def on_llm_end(self, response, **kwargs):
                emit({"type": "hbf_model_output", "response": response.model_dump()})

            def on_tool_start(self, serialized, input_str, **kwargs):
                emit({"type": "hbf_tool_input", "tool": serialized.get("name"), "input": input_str})

            def on_tool_end(self, output, **kwargs):
                emit(
                    {
                        "type": "hbf_tool_output",
                        "output": output.model_dump() if hasattr(output, "model_dump") else output,
                    }
                )

        workspace = Path.cwd()
        limit = max(args.recursion_limit, int(os.getenv("HBF_TASK_MIN_RECURSION_LIMIT", "0")))
        stats = AgentRunStatsCollector()
        agent = build_agent(
            workspace,
            api_key=os.environ["HBF_WORKER_API_KEY"],
            model_name=args.model,
            recursion_limit=limit,
            max_tokens=args.max_tokens,
            harness_profile=args.harness_profile,
            forward_reasoning_history=args.forward_reasoning_history,
            compact_at_tokens=args.compact_at_tokens,
            prompt_cache=args.prompt_cache,
            no_subagents=args.no_subagents,
        )
        callbacks = [Evidence()]
        if callback := stats.as_callback():
            callbacks.append(callback)
        result = agent.invoke(
            {"messages": [{"role": "user", "content": args.prompt}]},
            config={"callbacks": callbacks},
        )
        emit({"type": "hbf_trajectory", "messages": messages_to_dict(result.get("messages", []))})
        compactions = sum(
            p.read_text(encoding="utf-8", errors="replace").count("## Summarized at ")
            for p in (workspace / "conversation_history").glob("*.md")
        )
        emit(
            {
                "type": "hbf_deepagents_result",
                "stats": {
                    **stats.merged(result),
                    **stats.extra(),
                    "agent_compactions": compactions,
                },
            }
        )
        return 0
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — return a structured result to the parent verifier
        try:
            from openrouter_agent import is_transient_model_error

            retryable = is_transient_model_error(exc)
        except ImportError:
            retryable = False
        exhausted = exc.__class__.__name__ in {"GraphRecursionError"}
        # Context/model request validation errors are model failures; launch,
        # authentication, transport, provider and worker errors invalidate a run.
        context = any(
            word in str(exc).lower()
            for word in ("context length", "context_length", "maximum context", "too many tokens")
        )
        kind = "recursion_limit" if exhausted else "context_limit" if context else "infrastructure"
        emit(
            {
                "type": "hbf_deepagents_result",
                "failure_kind": kind,
                "message": f"{type(exc).__name__}: {exc}",
                "retryable": retryable,
                "stats": {**stats.merged(), **stats.extra()} if stats else {},
            }
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
