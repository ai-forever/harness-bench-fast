#!/usr/bin/env python3
"""Benchmark the ai-assistant FULL harness (Runtime + banking coordinator) on
harness-bench-fast tasks, with GigaChat-3.5 served via the gpt2giga proxy.

Each task runs in an isolated subprocess whose cwd IS the task workspace, so the
ai-assistant `code` agent's cwd-relative file tools (write_file/edit_file/shell)
land in the workspace. The standard harness-bench verifier then checks the
workspace. Subprocess isolation removes the global-cwd race and gives a hard
per-task timeout, so tasks can run concurrently.

Modes:
  (orchestrator)  python run_ai_assistant_bench.py [--task ID ...] [--concurrency N]
  (drive worker)  python run_ai_assistant_bench.py --drive TASK_ID --workspace DIR
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import TemporaryDirectory

ENGINE = "/Users/krestnikov/giga/ai-assistant/engine"

SMOKE = [
    "task_01_create_hello",
    "task_05_greet",
    "task_06_toggle_debug",
    "task_11_count_py",
]


def _drive(task_id: str, workspace: str) -> None:
    """Subprocess: drive the ai-assistant full Runtime on one task in cwd=workspace."""
    sys.path.insert(0, ENGINE)
    sys.path.insert(1, str(Path(ENGINE).parent))  # repo root — для harness.* импортов
    import logging
    logging.basicConfig(level=logging.ERROR)
    for noisy in ("runtime", "coordinator", "skill_worker", "agent_worker",
                  "llm_client", "function_registry"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    import config as aa_config
    rt_cfg = aa_config.CONFIG["runtime"]
    # Give the full harness a fair shot at multi-step coding work.
    rt_cfg["max_agent_tool_rounds"] = 40
    rt_cfg["max_coordinator_rounds"] = 30
    rt_cfg["max_skill_tool_rounds"] = 30
    rt_cfg["agent_timeout"] = 240
    rt_cfg["skill_timeout"] = 240

    import llm.llm_client as llm_client
    from config import SBOL_SCENARIOS_DIR
    from registry import FunctionRegistry, load_environment
    from runtime import Runtime

    from harness_bench.tasks import get_task

    metrics = {"llm_calls": 0, "in": 0, "out": 0}

    def hook(component, model, messages, result, latency):
        metrics["llm_calls"] += 1
        usage = getattr(result, "usage", None) or {}
        metrics["in"] += usage.get("prompt_tokens", 0) or 0
        metrics["out"] += usage.get("completion_tokens", 0) or 0
        if os.environ.get("AA_BENCH_DEBUG_SYSPROMPT"):
            with open("/tmp/aa_bench_llm_calls_debug.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(
                    {"component": component,
                     "messages": [str(m)[:2000] for m in messages],
                     "result": str(getattr(result, "content", result))[:2000],
                     "tool_calls": str(getattr(result, "tool_calls", None))[:2000]},
                    ensure_ascii=False) + "\n")

    llm_client.register_llm_hook(hook)

    task = get_task(task_id)
    env = load_environment(SBOL_SCENARIOS_DIR / "environments" / "minimal")
    registry = FunctionRegistry(env)
    ws = Path(workspace)
    rt = Runtime(registry, env.customer_id, work_dir=ws)
    os.chdir(ws)
    # Паритет с runner_cli.py: для CC-style CLI бенч инжектит workspace
    # AGENTS.md через --append-system-prompt, т.е. в system prompt агента,
    # который реально пишет файлы. У нас файлы пишут process/skill workers,
    # а не координатор, поэтому одного упоминания в user message мало —
    # координатор теряет конвенции при делегировании. Патчим билдеры
    # system prompt'ов (в этом субпроцессе), добавляя AGENTS.md хвостом.
    prompt = task.prompt
    agents_md = ws / "AGENTS.md"
    if agents_md.is_file():
        agents_text = agents_md.read_text(encoding="utf-8")
        block = (
            "\n\n## Инструкции воркспейса (AGENTS.md)\n"
            "В рабочей директории лежит AGENTS.md с обязательными конвенциями. "
            "Следуй им при любой работе с файлами:\n\n" + agents_text
        )
        import context.builder as ctx_builder
        _orig_proc = ctx_builder.build_process_system_prompt
        _orig_skill = ctx_builder.build_skill_system_prompt

        def _proc_patched(*a, **kw):
            out = _orig_proc(*a, **kw) + block
            if os.environ.get("AA_BENCH_DEBUG_SYSPROMPT"):
                Path("/tmp/aa_bench_worker_sysprompt.txt").write_text(out, encoding="utf-8")
            return out

        def _skill_patched(*a, **kw):
            return _orig_skill(*a, **kw) + block

        ctx_builder.build_process_system_prompt = _proc_patched
        ctx_builder.build_skill_system_prompt = _skill_patched
        # Воркеры импортируют билдеры по имени — патчим и там.
        import workers.process_worker as _pw
        _pw.build_process_system_prompt = _proc_patched
        import workers.skill_worker as _sw
        _sw.build_skill_system_prompt = _skill_patched
        # Координатор дистиллирует инструкцию воркеру и теряет исходное
        # сообщение пользователя — memory-правила AGENTS.md («пользователь
        # сообщил факт → сохрани в MEMORY.md») для воркера не срабатывают.
        # CC-style CLI видит сырое сообщение + AGENTS.md одним агентом.
        # Паритет: прокидываем оригинал в task воркера.
        _user_block = (
            "<original_user_message>\n"
            f"{task.prompt}\n"
            "</original_user_message>\n\n"
            "Инструкция координатора: "
        )
        _orig_run = _pw.ProcessWorker.run
        _orig_run_async = _pw.ProcessWorker.run_async

        def _run_patched(self, task, context="", cancel_event=None):
            return _orig_run(self, _user_block + task, context, cancel_event)

        async def _run_async_patched(self, task, context="", cancel_check=None):
            return await _orig_run_async(self, _user_block + task, context, cancel_check)

        _pw.ProcessWorker.run = _run_patched
        _pw.ProcessWorker.run_async = _run_async_patched
        # Координатору конвенции доносим через user message (файлы он не пишет).
        prompt = (
            "<workspace-instructions source=\"AGENTS.md\">\n"
            f"{agents_text}\n"
            "</workspace-instructions>\n\n"
            f"{prompt}"
        )
    try:
        rt.process_user_message(prompt)
    except Exception as exc:  # noqa: BLE001 — report, don't crash the orchestrator
        print(json.dumps({"drive_error": str(exc)[:300], **metrics}))
        return
    print(json.dumps(metrics))


def _run_task(task, timeout: int) -> dict:
    started = time.monotonic()
    metrics = {"llm_calls": 0, "in": 0, "out": 0}
    drive_error = None
    timed_out = False
    tmp = TemporaryDirectory(prefix=f"aa_{task.id}_")
    ws = Path(tmp.name)
    try:
        task.setup(ws)
        cmd = [sys.executable, os.path.abspath(__file__),
               "--drive", task.id, "--workspace", str(ws)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            for line in reversed((proc.stdout or "").strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        d = json.loads(line)
                        for k in metrics:
                            metrics[k] = d.get(k, 0)
                        drive_error = d.get("drive_error")
                        break
                    except json.JSONDecodeError:
                        continue
        except subprocess.TimeoutExpired:
            timed_out = True
        result = task.verify(ws)
        passed, message = result.passed, result.message
    except Exception as exc:  # noqa: BLE001
        passed, message = False, f"runner error: {exc}"
    finally:
        tmp.cleanup()

    if timed_out:
        message = f"[timeout {timeout}s] {message}"
    if drive_error and not passed:
        message = f"{message} | drive_error: {drive_error}"

    return {
        "task_id": task.id,
        "name": task.name,
        "passed": bool(passed),
        "message": message,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "agent_llm_calls": metrics["llm_calls"],
        "agent_input_tokens": metrics["in"],
        "agent_output_tokens": metrics["out"],
        "agent_total_tokens": metrics["in"] + metrics["out"],
        "timed_out": timed_out,
    }


def _write_json(path, results, tsv, harness_label="ai-assistant (full Runtime + coordinator) via gpt2giga"):
    rows = sorted(results, key=lambda x: x["task_id"])
    passed = sum(1 for x in results if x["passed"])
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "task_set_version": tsv,
                "harness": harness_label,
                "model": os.environ.get("HARNESS_LLM_MODEL", "GigaChat-3.5-432B-A28B") + " (IFT, via gpt2giga)",
                "total": len(results),
                "passed": passed,
                "pass_rate": passed / max(len(results), 1),
                "tasks": rows,
            },
            fh,
            ensure_ascii=False,
            indent=1,
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drive")
    ap.add_argument("--workspace")
    ap.add_argument("--task", action="append", help="Task id (repeatable)")
    ap.add_argument("--all", action="store_true", help="Run all tasks")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--json-output", default="runs/ai_assistant_full_gigachat35_ift.json")
    ap.add_argument("--harness-label", default="ai-assistant (full Runtime + coordinator) via gpt2giga",
                    help="Label written into the JSON 'harness' field (e.g. 'flat-harness').")
    args = ap.parse_args()

    if args.drive:
        _drive(args.drive, args.workspace)
        return

    from harness_bench.tasks import ALL_TASKS, get_task
    try:
        from harness_bench.versioning import TASK_SET_VERSION as tsv
    except Exception:  # noqa: BLE001
        tsv = "0.9.0"

    if args.all:
        tasks = list(ALL_TASKS)
    else:
        ids = args.task or SMOKE
        tasks = [get_task(t) for t in ids]

    print(f"ai-assistant FULL harness — {len(tasks)} task(s), "
          f"concurrency={args.concurrency}, timeout={args.timeout}s")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(_run_task, t, args.timeout): t for t in tasks}
        for done, fu in enumerate(as_completed(futs), start=1):
            r = fu.result()
            results.append(r)
            st = "PASS" if r["passed"] else "FAIL"
            print(f"[{done:3d}/{len(tasks)}] [{st}] {r['task_id']:34s} "
                  f"{r['elapsed_seconds']:6.1f}s — {r['message'][:80]}")
            _write_json(args.json_output, results, tsv, args.harness_label)

    passed = sum(1 for x in results if x["passed"])
    print(f"\nPassed: {passed}/{len(results)} "
          f"({100 * passed / max(len(results), 1):.1f}%)  -> {args.json_output}")


if __name__ == "__main__":
    main()
