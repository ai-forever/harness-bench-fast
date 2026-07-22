#!/usr/bin/env python3
"""Run harness-bench against GigaChat with the deepagents-gigachat profile while
capturing FULL per-task transcripts, then flag "tool-call-as-text" turns.

A "tool-call-as-text" turn is an assistant message that emits NO real
`tool_calls` (no function_call surfaced by the SDK) yet whose text content looks
like a tool invocation — e.g. a JSON `{"name": "write_file", "arguments": {...}}`
blob, a `<function_call>`/`<tool_call>` block, a fenced tool-code block, or a
`write_file(...)` paren call. These are the cases where GigaChat "writes the tool
instead of calling it", which is what we want to study.

This driver reuses `harness_bench.runner.build_agent` (so the deepagents-gigachat
profile is auto-registered exactly like `python -m harness_bench run`) and the
same workspace/verify flow, but additionally:
  * streams the agent (`stream_mode="values"`) so the transcript is preserved even
    when a task aborts on the LangGraph recursion limit;
  * serializes every message (assistant turns kept in full; system / tool-result
    contents truncated to keep files browseable);
  * writes one transcript JSON per task under a logs dir, an aggregate results
    JSON, and a condensed tool-as-text findings JSON.

Usage:
  uv run --no-sync python run_gigachat35_toolaudit.py --all --concurrency 5
  uv run --no-sync python run_gigachat35_toolaudit.py --smoke
  uv run --no-sync python run_gigachat35_toolaudit.py --task task_06_toggle_debug --keep
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory

from harness_bench.runner import (
    AgentRunStatsCollector,
    _ensure_credentials,
    _load_env_from_dotenv,
    build_agent,
)
from harness_bench.tasks import ALL_TASKS, get_task

# deepagents builtin tool names (from deepagents.middleware.*). Used to keep the
# tool-as-text detector precise: a JSON/paren "call" only counts when the named
# function is an actual tool the model was offered.
KNOWN_TOOLS = {
    "write_file",
    "read_file",
    "edit_file",
    "ls",
    "execute",
    "glob",
    "grep",
    "task",
    "write_todos",
    "read_todos",
    "think",
}

SMOKE = [
    "task_01_create_hello",
    "task_06_toggle_debug",
    "task_11_count_py",
    "task_14_sum_numbers",
]

_TOOLS_ALT = "|".join(sorted(KNOWN_TOOLS, key=len, reverse=True))

# --- tool-as-text detection patterns (highest confidence first) --------------
# Each entry: (confidence, pattern_name, compiled_regex). The FIRST match wins.
_DETECTORS: list[tuple[str, str, re.Pattern[str]]] = [
    # Explicit function/tool-call XML or special-token markers.
    (
        "high",
        "function_call_marker",
        re.compile(
            r"</?function_call\b|</?tool_call\b|<\|tool_call\|>|"
            r'"function_call"\s*:|<function=|<\|function_call\|>',
            re.IGNORECASE,
        ),
    ),
    # JSON object naming a known tool together with an arguments/parameters key.
    (
        "high",
        "json_name_arguments",
        re.compile(
            rf'"name"\s*:\s*"(?:{_TOOLS_ALT})".*?"(?:arguments|parameters|args)"\s*:',
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    # Reverse order: arguments/parameters key then a known tool name.
    (
        "high",
        "json_arguments_name",
        re.compile(
            rf'"(?:arguments|parameters|args)"\s*:.*?"name"\s*:\s*"(?:{_TOOLS_ALT})"',
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    # Fenced tool-invocation block. NOTE: deliberately excludes ```json and
    # ```python — models routinely *display* their result inside a ```json fence
    # in the final answer, which is legitimate explanation, not a tool-call-as-
    # text. Only explicit tool-call fence languages count here.
    (
        "medium",
        "fenced_tool_block",
        re.compile(r"```(?:tool_code|tool_call|tool_use|function_call)\b", re.IGNORECASE),
    ),
    # Bare paren call of a known tool with arg-ish content, e.g.
    #   write_file(file_path="...", content="...")  or  execute({"command": ...})
    # The arg must look like a quote, a brace, or an `identifier=` kwarg so prose
    # like "the execute(...) step" does not match.
    (
        "medium",
        "paren_call",
        re.compile(
            rf'\b(?:{_TOOLS_ALT})\s*\(\s*(?:[\'"{{]|[A-Za-z_][A-Za-z0-9_]*\s*=)',
        ),
    ),
]


def _detect_tool_as_text(content: str) -> tuple[str, str, str] | None:
    """Return (confidence, pattern_name, snippet) for the first match, else None."""
    if not content:
        return None
    for confidence, name, pattern in _DETECTORS:
        m = pattern.search(content)
        if m:
            start = max(0, m.start() - 80)
            end = min(len(content), m.end() + 200)
            snippet = content[start:end].strip()
            return confidence, name, snippet
    return None


def _content_to_text(content: object) -> str:
    """Flatten LangChain message content (str or list of blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
        return "\n".join(parts)
    return str(content) if content is not None else ""


def _message_type(message: object) -> str:
    return getattr(message, "type", None) or getattr(message, "role", None) or "unknown"


def _real_tool_calls(message: object) -> list[dict]:
    calls = getattr(message, "tool_calls", None) or []
    out: list[dict] = []
    for c in calls:
        if isinstance(c, dict):
            out.append({"name": c.get("name"), "args": c.get("args"), "id": c.get("id")})
        else:
            out.append(
                {
                    "name": getattr(c, "name", None),
                    "args": getattr(c, "args", None),
                    "id": getattr(c, "id", None),
                }
            )
    return out


def _finish_reason(message: object) -> str | None:
    rm = getattr(message, "response_metadata", None)
    if isinstance(rm, dict):
        fr = rm.get("finish_reason")
        if isinstance(fr, str):
            return fr
    return None


def _serialize_message(message: object, *, tool_result_cap: int, system_cap: int) -> dict:
    mtype = _message_type(message)
    text = _content_to_text(getattr(message, "content", ""))
    full_len = len(text)
    if mtype == "system" and full_len > system_cap:
        text = text[:system_cap] + f"\n...[truncated, {full_len} chars total]"
    elif mtype == "tool" and full_len > tool_result_cap:
        text = text[:tool_result_cap] + f"\n...[truncated, {full_len} chars total]"

    out: dict = {"type": mtype, "content": text, "content_len": full_len}

    tool_calls = _real_tool_calls(message)
    if tool_calls:
        out["tool_calls"] = tool_calls

    ak = getattr(message, "additional_kwargs", None)
    if isinstance(ak, dict) and ak.get("function_call"):
        out["additional_kwargs_function_call"] = ak["function_call"]

    fr = _finish_reason(message)
    if fr:
        out["finish_reason"] = fr

    name = getattr(message, "name", None)
    if name:
        out["name"] = name
    tcid = getattr(message, "tool_call_id", None)
    if tcid:
        out["tool_call_id"] = tcid
    return out


def _stream_capture(agent, payload, stats):
    """Stream the agent, returning (final_state, exception). The final yielded
    value carries the full message list, so transcripts survive a recursion abort."""
    cb = stats.as_callback()
    config = {"callbacks": [cb]} if cb is not None else None
    last = None
    try:
        for chunk in agent.stream(payload, stream_mode="values", config=config):
            if isinstance(chunk, dict) and "messages" in chunk:
                last = chunk
        return last, None
    except Exception as exc:  # noqa: BLE001 — preserve partial transcript + error
        return last, exc


def _analyze_messages(messages: list, *, tool_result_cap: int, system_cap: int) -> dict:
    serialized: list[dict] = []
    findings: list[dict] = []
    real_tool_call_names: list[str] = []

    for idx, message in enumerate(messages):
        s = _serialize_message(message, tool_result_cap=tool_result_cap, system_cap=system_cap)
        serialized.append(s)

        if _message_type(message) not in ("ai", "assistant"):
            continue

        calls = s.get("tool_calls") or []
        for c in calls:
            if c.get("name"):
                real_tool_call_names.append(c["name"])

        # A tool-as-text candidate: assistant turn with NO real tool_calls whose
        # text content looks like a tool invocation.
        if calls:
            continue
        text = s.get("content", "")
        det = _detect_tool_as_text(text)
        # Anomaly: SDK surfaced a function_call in additional_kwargs but did NOT
        # parse it into .tool_calls — also a "not really called" case.
        akfc = s.get("additional_kwargs_function_call")
        if det is None and not akfc:
            continue
        if det is not None:
            confidence, pattern, snippet = det
        else:
            confidence, pattern, snippet = "high", "additional_kwargs_function_call", json.dumps(
                akfc, ensure_ascii=False
            )[:280]
        findings.append(
            {
                "message_index": idx,
                "finish_reason": s.get("finish_reason"),
                "confidence": confidence,
                "pattern": pattern,
                "content_len": s.get("content_len"),
                "snippet": snippet,
                "had_additional_kwargs_function_call": bool(akfc),
            }
        )

    return {
        "messages": serialized,
        "findings": findings,
        "real_tool_call_names": real_tool_call_names,
    }


def run_one(task, *, recursion_limit: int, logs_dir: Path, keep: bool,
            tool_result_cap: int, system_cap: int, build_fn=build_agent) -> dict:
    from harness_bench.versioning import task_number

    started = time.monotonic()
    stats = AgentRunStatsCollector()
    error = None
    final_state = None
    exc = None
    workspace_keepalive: TemporaryDirectory | None = None
    try:
        if keep:
            workspace_path = Path(__import__("tempfile").mkdtemp(prefix=f"hbta_{task.id}_"))
        else:
            workspace_keepalive = TemporaryDirectory(prefix=f"hbta_{task.id}_")
            workspace_path = Path(workspace_keepalive.name)

        task.setup(workspace_path)
        try:
            agent = build_fn(workspace_path, recursion_limit=recursion_limit)
            final_state, exc = _stream_capture(
                agent, {"messages": [{"role": "user", "content": task.prompt}]}, stats
            )
        except Exception:  # noqa: BLE001 — build/setup failure
            exc = None
            error = traceback.format_exc()

        if exc is not None:
            error = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

        # Verify the workspace regardless — the agent may have done real work
        # before aborting on the recursion limit.
        try:
            verdict = task.verify(workspace_path)
            passed, message = verdict.passed, verdict.message
        except Exception as verr:  # noqa: BLE001
            passed, message = False, f"verify error: {verr}"
    finally:
        if workspace_keepalive is not None:
            workspace_keepalive.cleanup()

    messages = (final_state or {}).get("messages", []) if isinstance(final_state, dict) else []
    analysis = _analyze_messages(
        messages, tool_result_cap=tool_result_cap, system_cap=system_cap
    )
    merged = stats.merged(final_state if isinstance(final_state, dict) else None)

    transcript = {
        "task_id": task.id,
        "name": task.name,
        "prompt": task.prompt,
        "passed": bool(passed),
        "message": message,
        "error": error,
        "real_tool_call_names": analysis["real_tool_call_names"],
        "tool_as_text": analysis["findings"],
        "messages": analysis["messages"],
    }
    transcript_path = logs_dir / f"{task.id}.json"
    transcript_path.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    return {
        "task_id": task.id,
        "number": task_number(task.id),
        "name": task.name,
        "passed": bool(passed),
        "message": message,
        "error": error,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "tags": list(getattr(task, "tags", []) or []),
        "agent_steps": merged.get("agent_steps"),
        "agent_tool_calls": merged.get("agent_tool_calls"),
        "agent_llm_calls": merged.get("agent_llm_calls"),
        "agent_input_tokens": merged.get("agent_input_tokens"),
        "agent_output_tokens": merged.get("agent_output_tokens"),
        "agent_total_tokens": merged.get("agent_total_tokens"),
        "real_tool_call_names": analysis["real_tool_call_names"],
        "tool_as_text_count": len(analysis["findings"]),
        "tool_as_text": analysis["findings"],
        "transcript_file": str(transcript_path),
    }


def _write_outputs(results: list[dict], out_prefix: str, tsv: str,
                   harness_label: str = "deepagents + GigaChat profile (deepagents-gigachat)") -> None:
    rows = sorted(results, key=lambda r: (r.get("number") or 10**9, r["task_id"]))
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    tasks_with_tat = [r for r in results if r["tool_as_text_count"] > 0]
    total_tat = sum(r["tool_as_text_count"] for r in results)

    pattern_counts: dict[str, int] = {}
    confidence_counts: dict[str, int] = {}
    for r in results:
        for f in r["tool_as_text"]:
            pattern_counts[f["pattern"]] = pattern_counts.get(f["pattern"], 0) + 1
            confidence_counts[f["confidence"]] = confidence_counts.get(f["confidence"], 0) + 1

    full = {
        "task_set_version": tsv,
        "harness": harness_label,
        "model": "GigaChat-3.5-430B-A28B (IFT)",
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total else 0.0,
        "tool_as_text_total_turns": total_tat,
        "tool_as_text_task_count": len(tasks_with_tat),
        "tool_as_text_pattern_counts": pattern_counts,
        "tool_as_text_confidence_counts": confidence_counts,
        "tasks": rows,
    }
    Path(f"{out_prefix}_full.json").write_text(
        json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    findings = {
        "model": "GigaChat-3.5-430B-A28B (IFT)",
        "harness": harness_label,
        "total_tasks": total,
        "tasks_with_tool_as_text": len(tasks_with_tat),
        "tool_as_text_total_turns": total_tat,
        "pattern_counts": pattern_counts,
        "confidence_counts": confidence_counts,
        "cases": [
            {
                "task_id": r["task_id"],
                "number": r["number"],
                "passed": r["passed"],
                "count": r["tool_as_text_count"],
                "transcript_file": r["transcript_file"],
                "findings": r["tool_as_text"],
            }
            for r in rows
            if r["tool_as_text_count"] > 0
        ],
    }
    Path(f"{out_prefix}_findings.json").write_text(
        json.dumps(findings, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", action="append", help="Task id (repeatable)")
    ap.add_argument("--all", action="store_true", help="Run all 313 tasks")
    ap.add_argument("--smoke", action="store_true", help="Run a tiny smoke subset")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--recursion-limit", type=int, default=80)
    ap.add_argument("--keep", action="store_true", help="Keep temp workspaces")
    ap.add_argument("--tool-result-cap", type=int, default=8000,
                    help="Max chars kept per tool-result message (assistant turns are never truncated)")
    ap.add_argument("--system-cap", type=int, default=1500,
                    help="Max chars kept per system message")
    ap.add_argument("--out-prefix", default="gigachat_3_5_430b_profile_ift_toolaudit")
    ap.add_argument("--logs-dir", default="runs/gigachat35_toolaudit")
    ap.add_argument(
        "--pure",
        action="store_true",
        help="Run stock deepagents WITHOUT the deepagents-gigachat profile "
        "(uses harness_bench.runner_pure.build_agent).",
    )
    args = ap.parse_args()

    _load_env_from_dotenv()
    _ensure_credentials()

    if args.pure:
        from harness_bench.runner_pure import build_agent as build_fn
        harness_label = "deepagents, no profile (pure)"
    else:
        build_fn = build_agent
        harness_label = "deepagents + GigaChat profile (deepagents-gigachat)"

    try:
        from harness_bench.versioning import TASK_SET_VERSION as tsv
    except Exception:  # noqa: BLE001
        tsv = "unknown"

    if args.all:
        tasks = list(ALL_TASKS)
    elif args.smoke:
        tasks = [get_task(t) for t in SMOKE]
    elif args.task:
        tasks = [get_task(t) for t in args.task]
    else:
        tasks = [get_task(t) for t in SMOKE]

    logs_dir = Path(args.logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"GigaChat-3.5 tool-as-text audit — {len(tasks)} task(s), "
        f"concurrency={args.concurrency}, recursion_limit={args.recursion_limit}\n"
        f"logs -> {logs_dir}/  results -> {args.out_prefix}_full.json"
    )

    results: list[dict] = []
    write_lock = threading.Lock()
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _run(t):
        return run_one(
            t,
            recursion_limit=args.recursion_limit,
            logs_dir=logs_dir,
            keep=args.keep,
            tool_result_cap=args.tool_result_cap,
            system_cap=args.system_cap,
            build_fn=build_fn,
        )

    done = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(_run, t): t for t in tasks}
        for fu in as_completed(futs):
            t = futs[fu]
            try:
                r = fu.result()
            except Exception:  # noqa: BLE001 — never let one task kill the run
                r = {
                    "task_id": t.id,
                    "number": None,
                    "name": t.name,
                    "passed": False,
                    "message": "driver error",
                    "error": traceback.format_exc(),
                    "elapsed_seconds": 0.0,
                    "tags": [],
                    "agent_steps": None,
                    "agent_tool_calls": None,
                    "agent_llm_calls": None,
                    "agent_input_tokens": None,
                    "agent_output_tokens": None,
                    "agent_total_tokens": None,
                    "real_tool_call_names": [],
                    "tool_as_text_count": 0,
                    "tool_as_text": [],
                    "transcript_file": "",
                }
            results.append(r)
            done += 1
            with write_lock:
                _write_outputs(results, args.out_prefix, tsv, harness_label)
            st = "PASS" if r["passed"] else "FAIL"
            tat = r["tool_as_text_count"]
            tat_tag = f"  tool-as-text x{tat}" if tat else ""
            print(
                f"[{done:3d}/{len(tasks)}] [{st}] {r['task_id']:36s} "
                f"{r['elapsed_seconds']:6.1f}s — {str(r['message'])[:70]}{tat_tag}"
            )

    passed = sum(1 for r in results if r["passed"])
    tat_tasks = sum(1 for r in results if r["tool_as_text_count"] > 0)
    tat_turns = sum(r["tool_as_text_count"] for r in results)
    print(
        f"\nPassed: {passed}/{len(results)}  |  "
        f"tool-as-text: {tat_turns} turn(s) across {tat_tasks} task(s)\n"
        f"-> {args.out_prefix}_full.json  /  {args.out_prefix}_findings.json"
    )


if __name__ == "__main__":
    main()
