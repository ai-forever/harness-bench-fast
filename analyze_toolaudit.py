#!/usr/bin/env python3
"""Offline re-analysis of saved tool-audit transcripts.

Because `run_gigachat35_toolaudit.py` persists the FULL per-task transcript, we
can recompute "tool-call-as-text" findings (and task-level failure modes) without
re-running the model. This script rescans the saved transcripts with the current
detector and writes a corrected findings file plus a failure-mode summary.

Usage:
  uv run --no-sync python analyze_toolaudit.py \
      --logs-dir runs/gigachat35_toolaudit \
      --out-prefix gigachat_3_5_430b_profile_ift_toolaudit
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

from run_gigachat35_toolaudit import _detect_tool_as_text

MUTATING = {"write_file", "edit_file", "execute"}


def _ai_turns(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m.get("type") in ("ai", "assistant")]


def _scan_findings(messages: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for idx, m in enumerate(messages):
        if m.get("type") not in ("ai", "assistant"):
            continue
        if m.get("tool_calls"):  # a real tool call was emitted — not "as text"
            continue
        content = m.get("content", "") or ""
        det = _detect_tool_as_text(content)
        akfc = m.get("additional_kwargs_function_call")
        if det is None and not akfc:
            continue
        if det is not None:
            confidence, pattern, snippet = det
        else:
            confidence, pattern, snippet = (
                "high",
                "additional_kwargs_function_call",
                json.dumps(akfc, ensure_ascii=False)[:280],
            )
        findings.append(
            {
                "message_index": idx,
                "finish_reason": m.get("finish_reason"),
                "confidence": confidence,
                "pattern": pattern,
                "content_len": m.get("content_len"),
                "snippet": snippet,
            }
        )
    return findings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs-dir", default="runs/gigachat35_toolaudit")
    ap.add_argument("--out-prefix", default="gigachat_3_5_430b_profile_ift_toolaudit")
    ap.add_argument(
        "--rewrite",
        action="store_true",
        help="Rewrite each saved transcript's tool_as_text field and the _full.json "
        "per-task fields with the corrected detector (the run wrote them with the "
        "older, noisier detector).",
    )
    args = ap.parse_args()

    paths = sorted(glob.glob(str(Path(args.logs_dir) / "*.json")))
    corrected_by_task: dict[str, list[dict]] = {}
    findings_cases: list[dict] = []
    failure_rows: list[dict] = []
    pattern_counts: dict[str, int] = {}
    confidence_counts: dict[str, int] = {}
    n_tasks = 0
    n_passed = 0

    for p in paths:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        n_tasks += 1
        passed = bool(d.get("passed"))
        n_passed += int(passed)
        messages = d.get("messages", [])
        names = d.get("real_tool_call_names", []) or []
        ai = _ai_turns(messages)
        reached_final = any(m.get("finish_reason") == "stop" for m in ai)

        findings = _scan_findings(messages)
        corrected_by_task[d["task_id"]] = findings
        if args.rewrite:
            d["tool_as_text"] = findings
            Path(p).write_text(
                json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        if findings:
            for f in findings:
                pattern_counts[f["pattern"]] = pattern_counts.get(f["pattern"], 0) + 1
                confidence_counts[f["confidence"]] = confidence_counts.get(f["confidence"], 0) + 1
            findings_cases.append(
                {
                    "task_id": d["task_id"],
                    "passed": passed,
                    "count": len(findings),
                    "transcript_file": p,
                    "findings": findings,
                }
            )

        if not passed:
            mut = [n for n in names if n in MUTATING]
            failure_rows.append(
                {
                    "task_id": d["task_id"],
                    "real_tool_calls": len(names),
                    "mutating_tool_calls": len(mut),
                    "ai_turns": len(ai),
                    "reached_final_answer": reached_final,
                    "hit_loop_cap": not reached_final,
                    "first_line": (d.get("message") or "").splitlines()[0][:90],
                }
            )

    findings_out = {
        "model": "GigaChat-3.5-430B-A28B (IFT)",
        "harness": "deepagents + GigaChat profile (deepagents-gigachat)",
        "detector": "tool-call-as-text (assistant turn, no real tool_calls, content matches a tool-call pattern); ```json/```python result displays excluded",
        "total_tasks": n_tasks,
        "passed": n_passed,
        "tasks_with_tool_as_text": len(findings_cases),
        "tool_as_text_total_turns": sum(c["count"] for c in findings_cases),
        "pattern_counts": pattern_counts,
        "confidence_counts": confidence_counts,
        "cases": findings_cases,
    }
    Path(f"{args.out_prefix}_findings.json").write_text(
        json.dumps(findings_out, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    failure_rows.sort(key=lambda r: (r["hit_loop_cap"], r["mutating_tool_calls"]))
    n_zero_mut = sum(1 for r in failure_rows if r["mutating_tool_calls"] == 0)
    n_loopcap = sum(1 for r in failure_rows if r["hit_loop_cap"])
    failure_out = {
        "model": "GigaChat-3.5-430B-A28B (IFT)",
        "total_tasks": n_tasks,
        "failed_tasks": len(failure_rows),
        "failed_with_zero_mutating_tool_calls": n_zero_mut,
        "failed_hit_loop_cap": n_loopcap,
        "note": (
            "failed_with_zero_mutating_tool_calls counts failures where the model "
            "never called write_file/edit_file/execute — i.e. it narrated instead "
            "of acting. hit_loop_cap = no terminal assistant answer (recursion limit)."
        ),
        "rows": failure_rows,
    }
    Path(f"{args.out_prefix}_failuremodes.json").write_text(
        json.dumps(failure_out, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    print(f"Tasks scanned: {n_tasks}  passed: {n_passed}")
    print(
        f"tool-call-as-text: {findings_out['tool_as_text_total_turns']} turn(s) "
        f"across {len(findings_cases)} task(s)  patterns={pattern_counts or '{}'}"
    )
    print(
        f"failures: {len(failure_rows)}  | narrated-instead-of-acting "
        f"(0 mutating calls): {n_zero_mut}  | hit loop cap: {n_loopcap}"
    )
    if args.rewrite:
        full_path = Path(f"{args.out_prefix}_full.json")
        if full_path.exists():
            full = json.loads(full_path.read_text(encoding="utf-8"))
            for t in full.get("tasks", []):
                corrected = corrected_by_task.get(t["task_id"], [])
                t["tool_as_text"] = corrected
                t["tool_as_text_count"] = len(corrected)
            full["tool_as_text_total_turns"] = sum(
                len(v) for v in corrected_by_task.values()
            )
            full["tool_as_text_task_count"] = sum(
                1 for v in corrected_by_task.values() if v
            )
            full["tool_as_text_pattern_counts"] = pattern_counts
            full["tool_as_text_confidence_counts"] = confidence_counts
            full_path.write_text(
                json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            print(f"rewrote {full_path} and {len(paths)} transcript(s) with corrected detector")

    print(f"-> {args.out_prefix}_findings.json  /  {args.out_prefix}_failuremodes.json")


if __name__ == "__main__":
    main()
