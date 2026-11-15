#!/usr/bin/env python3
# FORK-OWNED: not in upstream ai-forever/harness-bench-fast
"""Hermes Benchmark Runner — runs harness-bench-fast tasks through Hermes agent.

Uses `hermes chat -q` to run each task in an isolated session with
clean config (--ignore-user-config --yolo --format stream-json). Usage buckets are
read from the stream-json result envelope. Verifies with harness-bench-fast verifiers.

Usage:
    python run_hermes_bench.py --pilot           # 12 tasks (6 failed + 6 passed)
    python run_hermes_bench.py --failed-only      # 6 failed tasks
    python run_hermes_bench.py --tasks task_01_create_hello
    python run_hermes_bench.py --all               # all 391 tasks
    python run_hermes_bench.py --shot-list         # 52-task stratified sample
    python run_hermes_bench.py --all --concurrency 3 --json-output results/hermes_full_391.json
    python run_hermes_bench.py --all --rerun-on-fail  # resume, re-run failed only
    python run_hermes_bench.py --shot-list --thinking-off  # shot list, reasoning disabled (alias: --thinking none)
    python run_hermes_bench.py --shot-list --thinking high  # shot list, explicit reasoning level
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import TemporaryDirectory

# Default to the local harness-bench-fast checkout.
_DEFAULT_BENCH_DIR = str(Path(__file__).parent)
BENCH_DIR = Path(os.environ.get("BENCH_DIR", _DEFAULT_BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR))

SHOT_LIST_FILE = BENCH_DIR / "shot_list.txt"

from harness_bench.artifact_metadata import artifact_metadata, resolve_harness_version  # noqa: E402
from harness_bench.bench_credentials import resolve_bench_api_key  # noqa: E402
from harness_bench.cost_resolver import compute_cost_rub, resolve_model_rates  # noqa: E402
from harness_bench.tasks import ALL_TASKS, get_task  # noqa: E402

DEFAULT_MODEL = "zai-org/GLM-5.2"
DEFAULT_TIMEOUT = 660
DEFAULT_CONCURRENCY = 3

# Reasoning levels accepted by `hermes chat --reasoning` (Hermes 0.22.1+).
THINKING_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
HERMES_BIN = os.environ.get("HERMES_BIN", "hermes")

# Cloud.ru Foundation Models API — provider config for --ignore-user-config mode.
CLOUD_RU_BASE_URL = "https://foundation-models.api.cloud.ru/v1/"
# Dedicated benchmark credential. Priority: CLOUD_RU_BENCH_API_KEY ->
# CLOUD_RU_KAP_HERMES (env, then ~/.hermes/.env) -> production CLOUD_RU_API_KEY.
# This is what feeds OPENAI_API_KEY for `--provider custom`; BENCH_CREDENTIAL
# records the source and is written to the run artifact.
CLOUD_RU_API_KEY, BENCH_CREDENTIAL = resolve_bench_api_key()

# Memory tasks (222-253) ship AGENTS.md fixture with instructions to read/write MEMORY.md.
# --ignore-rules would skip AGENTS.md → all memory tasks FAIL.
# Hybrid approach: memory tasks run WITHOUT --ignore-rules, all others WITH it.
_MEMORY_TASK_PREFIXES = tuple(f"task_{n}_" for n in range(222, 254))


def _is_memory_task(task_id: str) -> bool:
    """Check if task is a memory task (222-253) that needs AGENTS.md."""
    return task_id.startswith(_MEMORY_TASK_PREFIXES)


# Prefixes of provider-qualified model ids used by the OpenClaw/Pi runners
# (e.g. ``custom-foundation-models-api-cloud-ru/deepseek-ai/DeepSeek-V4.1-Flash``).
# ``hermes chat --provider custom`` routes via ``--base-url``/``--api-key`` and
# expects the BARE model id, so this leading provider segment must be dropped.
PROVIDER_MODEL_PREFIXES = ("custom-foundation-models-api-cloud-ru/",)


def _bare_model_id(model: str, provider: str = "custom") -> str:
    """Return the model id Hermes expects, stripping a provider prefix.

    For ``--provider custom`` the OpenClaw/Pi ids carry a leading provider
    segment (``<provider>/<org>/<model>``) that Hermes does not recognise, so the
    bare ``<org>/<model>`` is passed instead. Unknown prefixes pass through
    unchanged.
    """
    if provider != "custom":
        return model
    for prefix in PROVIDER_MODEL_PREFIXES:
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def build_chat_command(prompt, *, model=DEFAULT_MODEL, thinking=None,
                       is_memory=False, provider="custom",
                       api_key=None, base_url=None):
    """Build the ``hermes chat`` argv for a single benchmark task.

    Hermes >= 0.23 compatibility (see the ``fix-hermes-cli-compat`` change):

    - ``-v/--verbose`` is unsupported on the gateway/non-TTY path — passing it
      aborts the run with ``exit=2: Unsupported gateway CLI options``. Usage is
      read from ``--format stream-json`` on stdout instead.
    - ``--ignore-user-config`` drops the user's provider config, so the custom
      provider's endpoint (``--base-url``) and credential (``--api-key``) are
      passed explicitly.
    - ``--model`` is the *bare* id for the custom provider (``_bare_model_id``).

    ``--ignore-rules`` is applied for every task EXCEPT memory tasks (222-253),
    which need AGENTS.md injected. ``--reasoning <level>`` is native reasoning
    control (Hermes 0.22.1+) and only overrides ``agent.reasoning_effort`` for
    this run, so ``--ignore-user-config`` can stay on.
    """
    cmd = [
        HERMES_BIN, "chat",
        "-q", prompt,
        "--model", _bare_model_id(model, provider),
        "--provider", provider,
        "--yolo",
        "--format", "stream-json",
        "--max-turns", "120",
        "--source", "tool",
        "--ignore-user-config",
    ]
    if provider == "custom":
        cmd += ["--base-url", base_url if base_url is not None else CLOUD_RU_BASE_URL]
        cmd += ["--api-key", api_key if api_key is not None else CLOUD_RU_API_KEY]
    if thinking:
        cmd += ["--reasoning", thinking]
    if not is_memory:
        cmd.append("--ignore-rules")
    return cmd


def _parse_stream_json(stdout: str) -> tuple[dict, str | None, str | None]:
    """Parse ``hermes chat --format stream-json`` JSONL from *stdout*.

    Returns ``(token_stats, session_id, result_error)``:

    - ``token_stats`` carries ``agent_input_tokens`` / ``agent_output_tokens`` /
      ``agent_total_tokens`` / ``agent_cache_read_tokens`` /
      ``agent_cache_write_tokens`` from the terminal ``result`` event, plus
      ``agent_tool_calls`` / ``agent_steps`` (number of ``tool_use`` events,
      OpenClaw-runner parity).
    - ``session_id`` is taken from ``result`` (falling back to ``system/init``).
    - ``result_error`` is non-``None`` when the envelope reported an error or no
      ``result`` event was emitted at all (usage then unwinds to zeros so
      ``_write_results`` records 0 and the failure is visible in ``error``).
    """
    token_stats: dict = {}
    session_id: str | None = None
    result_error: str | None = None
    tool_calls = 0
    saw_result = False

    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        if etype == "tool_use":
            tool_calls += 1
        elif etype == "system":
            sid = event.get("session_id")
            if sid and session_id is None:
                session_id = str(sid)
        elif etype == "result":
            saw_result = True
            sid = event.get("session_id")
            if sid:
                session_id = str(sid)
            tokens = event.get("tokens") or {}
            token_stats.update({
                "agent_input_tokens": int(tokens.get("input") or 0),
                "agent_output_tokens": int(tokens.get("output") or 0),
                "agent_total_tokens": int(tokens.get("total") or 0),
                "agent_cache_read_tokens": int(tokens.get("cache_read") or 0),
                "agent_cache_write_tokens": int(tokens.get("cache_write") or 0),
            })
            if event.get("error"):
                result_error = str(event["error"])
            elif int(event.get("exit_code") or 0) != 0:
                result_error = f"result exit_code={int(event.get('exit_code') or 0)}"

    if not saw_result:
        token_stats = {}
        if result_error is None:
            result_error = "stream-json: no result event (usage unavailable)"
        return token_stats, session_id, result_error

    if tool_calls:
        token_stats["agent_tool_calls"] = tool_calls
        token_stats["agent_steps"] = tool_calls
    return token_stats, session_id, result_error

FAILED_TASK_IDS = [
    "task_223_memory_city_timezone_script",
    "task_293_tau2_event_ticket_exchange",
    "task_355_segment_audit",
    "task_376_cli_xtab_column_share",
    "task_378_cli_cfgctl_layered_merge",
    "task_389_cli_find_xargs_predicates",
]

PASSED_TASK_IDS = [
    "task_01_create_hello",
    "task_13_count_csv_lines",
    "task_50_fix_typo",
    "task_76_create_dataclass",
    "task_100_zero_balance",
    "task_200_impl_priority_queue",
]

PILOT_TASK_IDS = FAILED_TASK_IDS + PASSED_TASK_IDS

# Thread lock for checkpoint writes
_write_lock = threading.Lock()

# ₽ pricing is resolved per-model from ~/.openclaw/openclaw.json via cost_resolver.
def _get_model_rates(model_id: str) -> dict:
    """Return per-model ₽/1M rates (input/output/cacheRead/cacheWrite)."""
    return resolve_model_rates(model_id)


def run_one_task(task, *, model=DEFAULT_MODEL, timeout=DEFAULT_TIMEOUT, keep_workspace=False, thinking=None):
    """Run a single benchmark task through `hermes chat -q`."""
    started = time.monotonic()
    workspace_keepalive = None

    try:
        if keep_workspace:
            workspace_path = Path(f"/tmp/hermes_bench_{task.id}")
            # Вариант 2: очистка workspace перед каждой задачей — предотвращает contamination
            shutil.rmtree(workspace_path, ignore_errors=True)
            workspace_path.mkdir(parents=True, exist_ok=True)
        else:
            workspace_keepalive = TemporaryDirectory(prefix=f"hermes_bench_{task.id}_")
            workspace_path = Path(workspace_keepalive.name)

        # 1. Setup workspace with task fixtures
        task.setup(workspace_path)

        # 2. Build hermes command (supported Hermes >= 0.23 flags; see build_chat_command).
        cmd = build_chat_command(
            task.prompt,
            model=model,
            thinking=thinking,
            is_memory=_is_memory_task(task.id),
        )

        # 3. Run hermes with cwd=workspace
        agent_error = None
        session_id = None
        token_stats: dict = {}
        try:
            run_env = os.environ.copy()
            run_env["OPENAI_API_KEY"] = CLOUD_RU_API_KEY
            run_env["OPENAI_BASE_URL"] = CLOUD_RU_BASE_URL

            result = subprocess.run(
                cmd,
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                timeout=timeout + 60,
                encoding="utf-8",
                errors="replace",
                env=run_env,
            )
            if result.returncode != 0:
                stderr_tail = (result.stderr or "")[-300:]
                agent_error = f"exit={result.returncode}: {stderr_tail}"

            # Usage + session id come from the stream-json ``result`` envelope on stdout.
            token_stats, session_id, result_error = _parse_stream_json(result.stdout or "")
            if result_error:
                agent_error = f"{agent_error}; {result_error}" if agent_error else result_error

        except subprocess.TimeoutExpired:
            agent_error = f"timed out after {timeout}s"
            result = None
        except Exception as exc:
            agent_error = f"exception: {exc}"
            result = None

        elapsed = time.monotonic() - started

        # 4. Verify the result
        verify_result = task.verify(workspace_path)

        # 5. Usage statistics were parsed from the stream-json envelope right
        #    after the command ran (see `_parse_stream_json`): explicit
        #    input/output/cache buckets, the session id, and `agent_steps` =
        #    number of tool calls (OpenClaw-runner parity). When no `result`
        #    event arrived the stats stay empty → `_write_results` records zeros
        #    and the missing envelope is surfaced through `error`.

        return {
            "task_id": task.id,
            "passed": verify_result.passed,
            "message": verify_result.message,
            "elapsed_seconds": round(elapsed, 2),
            "error": agent_error,
            "workspace": str(workspace_path) if keep_workspace else None,
            "session_id": session_id,
            **token_stats,
        }

    except Exception as exc:
        elapsed = time.monotonic() - started
        return {
            "task_id": task.id,
            "passed": False,
            "message": f"runner error: {exc}",
            "elapsed_seconds": round(elapsed, 2),
            "error": str(exc),
            "workspace": None,
        }
    finally:
        if workspace_keepalive is not None and not keep_workspace:
            workspace_keepalive.cleanup()


def _write_results(results, path, model=None, *,
                   thinking=None, harness_version=None, task_ids=None):
    """Write results JSON with summary. Thread-safe."""
    with _write_lock:
        passed = sum(1 for r in results if r["passed"])
        total = len(results)
        total_seconds = round(sum(r["elapsed_seconds"] for r in results), 1)
        total_in = sum(r.get("agent_input_tokens", 0) for r in results)
        total_out = sum(r.get("agent_output_tokens", 0) for r in results)
        total_tok = sum(r.get("agent_total_tokens", 0) for r in results)
        total_steps = sum(r.get("agent_steps", 0) or 0 for r in results)
        total_reasoning = sum(int(r.get("agent_reasoning_tokens") or 0) for r in results)
        # Cache buckets are recorded EXPLICITLY (OpenClaw-runner parity) so downstream
        # readers never re-derive them: prefer an explicit per-task bucket, else derive
        # it ONCE here from ``total - input - output`` and store it back on the task row.
        total_cache_read = 0
        total_cache_write = 0
        for r in results:
            cr = r.get("agent_cache_read_tokens")
            cw = r.get("agent_cache_write_tokens")
            if cr is None or cw is None:
                derived = max(
                    int(r.get("agent_total_tokens") or 0)
                    - int(r.get("agent_input_tokens") or 0)
                    - int(r.get("agent_output_tokens") or 0),
                    0,
                )
                if cr is None:
                    cr = derived
                if cw is None:
                    cw = 0
            r["agent_cache_read_tokens"] = int(cr)
            r["agent_cache_write_tokens"] = int(cw)
            total_cache_read += int(cr)
            total_cache_write += int(cw)
        cost_rub = round(
            compute_cost_rub(total_in, total_out, total_cache_read, total_cache_write,
                             _get_model_rates(model or DEFAULT_MODEL)),
            2,
        )
        task_id_list = list(task_ids) if task_ids is not None else [r.get("task_id") for r in results]
        payload = {
            **artifact_metadata(
                harness="hermes",
                task_ids=task_id_list,
                thinking=thinking,
                harness_version=harness_version,
                model=model or DEFAULT_MODEL,
            ),
            "results": results,
            "summary": {
                "total": total,
                "passed": passed,
                "failed": total - passed,
                "pass_rate": round(passed / max(total, 1), 4),
                "total_seconds": total_seconds,
                "total_input_tokens": total_in,
                "total_output_tokens": total_out,
                "total_tokens": total_tok,
                "total_steps": total_steps,
                "total_reasoning_tokens": total_reasoning,
                "total_cache_read_tokens": total_cache_read,
                "total_cache_write_tokens": total_cache_write,
                "estimated_cost_rub": cost_rub,
                "bench_credential": BENCH_CREDENTIAL,
            },
        }
        tmp_path = str(path) + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def _write_status(path, completed, total_task_count, results, started_at, model=DEFAULT_MODEL):
    """Write status file for progress monitoring. Thread-safe."""
    with _write_lock:
        passed = sum(1 for r in results if r["passed"])
        failed = sum(1 for r in results if not r["passed"])
        elapsed = time.monotonic() - started_at
        remaining = (elapsed / max(completed, 1)) * (total_task_count - completed) if completed > 0 else 0
        total_in = sum(r.get("agent_input_tokens", 0) for r in results)
        total_out = sum(r.get("agent_output_tokens", 0) for r in results)
        total_tok = total_in + total_out
        total_cr = sum(r.get("agent_cache_read_tokens", 0) or 0 for r in results)
        total_cw = sum(r.get("agent_cache_write_tokens", 0) or 0 for r in results)
        cost_rub = round(compute_cost_rub(total_in, total_out, total_cr, total_cw, _get_model_rates(model)), 2)
        last_task = results[-1]["task_id"] if results else None
        status = {
            "completed": completed,
            "total": total_task_count,
            "passed": passed,
            "failed": failed,
            "elapsed_seconds": round(elapsed, 1),
            "estimated_remaining_seconds": round(remaining, 1),
            "total_tokens": total_tok,
            "estimated_cost_rub": cost_rub,
            "last_task": last_task,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)


def _load_checkpoint(json_path):
    """Load existing results from checkpoint file for resume."""
    if not json_path or not Path(json_path).exists():
        return {}
    try:
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
        return {r["task_id"]: r for r in data.get("results", [])}
    except (json.JSONDecodeError, KeyError):
        return {}


def run_batch(task_ids, *, model=DEFAULT_MODEL, timeout=DEFAULT_TIMEOUT,
               keep_workspace=False, json_output=None, concurrency=1,
               status_file=None, rerun_on_fail=False, fresh=False,
               thinking=None, harness_version=None):
    """Run tasks with optional concurrency, checkpoint/resume, and progress reporting."""

    # Checkpoint/resume: load existing results
    existing_results = {}
    if json_output and not fresh:
        existing_results = _load_checkpoint(json_output)
        if existing_results:
            if rerun_on_fail:
                # Keep passed tasks, re-run failed ones
                skipped = [tid for tid in task_ids if tid in existing_results and existing_results[tid]["passed"]]
                pending = [tid for tid in task_ids if tid not in skipped]
                print(f"Checkpoint: {len(skipped)} passed tasks skipped, {len(pending)} to run", flush=True)
            else:
                # Skip all tasks in checkpoint
                skipped = [tid for tid in task_ids if tid in existing_results]
                pending = [tid for tid in task_ids if tid not in existing_results]
                print(f"Resume: {len(skipped)} tasks completed, {len(pending)} remaining", flush=True)
        else:
            pending = list(task_ids)
    else:
        pending = list(task_ids)

    # Start with existing passed results (for rerun_on_fail) or empty
    if rerun_on_fail and existing_results:
        results = [existing_results[tid] for tid in task_ids if tid in existing_results and existing_results[tid]["passed"]]
    elif not fresh and existing_results:
        results = [existing_results[tid] for tid in task_ids if tid in existing_results]
    else:
        results = []

    total_all = len(task_ids)
    started_at = time.monotonic()
    completed = len(results)

    if not pending:
        print("Nothing to run — all tasks already in checkpoint.", flush=True)
        return results

    print(f"Running {len(pending)} tasks (concurrency={concurrency})", flush=True)

    if concurrency <= 1:
        # Sequential mode (for debugging)
        for tid in pending:
            task = get_task(tid)
            print(f"[{completed+1}/{total_all}] {task.id}", flush=True)
            run = run_one_task(task, model=model, timeout=timeout, keep_workspace=keep_workspace, thinking=thinking)
            results.append(run)
            completed += 1
            status = "PASS" if run["passed"] else "FAIL"
            print(f"  [{status}] {run['elapsed_seconds']:.1f}s — {run['message'][:120]}", flush=True)
            if json_output:
                _write_results(results, json_output, model=model,
                               thinking=thinking,
                               harness_version=harness_version, task_ids=task_ids)
            if status_file:
                _write_status(status_file, completed, total_all, results, started_at, model=model)
    else:
        # Parallel mode with ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            future_to_task = {}
            for tid in pending:
                task = get_task(tid)
                future = pool.submit(run_one_task, task, model=model, timeout=timeout, keep_workspace=keep_workspace, thinking=thinking)
                future_to_task[future] = tid

            for future in as_completed(future_to_task):
                tid = future_to_task[future]
                run = future.result()
                results.append(run)
                completed += 1
                status = "PASS" if run["passed"] else "FAIL"
                print(f"[{completed}/{total_all}] {tid}: [{status}] {run['elapsed_seconds']:.1f}s — {run['message'][:120]}", flush=True)
                if json_output:
                    _write_results(results, json_output, model=model,
                                   thinking=thinking,
                                   harness_version=harness_version, task_ids=task_ids)
                if status_file:
                    _write_status(status_file, completed, total_all, results, started_at, model=model)

    # Sort results by task_id for consistent output
    results.sort(key=lambda r: r["task_id"])

    return results


def _load_shot_list():
    """Load task IDs from shot_list.txt."""
    if not SHOT_LIST_FILE.exists():
        print(f"ERROR: shot list not found at {SHOT_LIST_FILE}", file=sys.stderr)
        sys.exit(1)
    return [line.strip() for line in SHOT_LIST_FILE.read_text().splitlines() if line.strip() and not line.startswith("#")]


def detect_harness_version() -> str | None:
    """Best-effort Hermes harness (CLI) version for the harness_version artifact field."""
    try:
        out = subprocess.run([HERMES_BIN, "--version"], capture_output=True,
                             text=True, timeout=15)
    except Exception:  # noqa: BLE001 - detection is best-effort
        return None
    lines = (out.stdout or out.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else None


def main():
    parser = argparse.ArgumentParser(description="Run harness-bench-fast via Hermes")
    parser.add_argument("--tasks", nargs="+", help="Specific task IDs")
    parser.add_argument("--failed-only", action="store_true")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--shot-list", action="store_true", help="Run the 52-task shot list")
    parser.add_argument("--thinking", choices=THINKING_LEVELS, default=None,
                        help="Reasoning level passed to `hermes chat --reasoning` "
                             "(none|minimal|low|medium|high|xhigh|max|ultra); "
                             "recorded in the artifact `thinking` field")
    parser.add_argument("--thinking-off", action="store_true",
                        help="Alias for --thinking none (disable model reasoning)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--harness-version", default=None,
                        help="Harness (CLI) version recorded as harness_version; "
                             "defaults to env HARNESS_BENCH_HARNESS_VERSION or autodetect")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--keep", action="store_true", help="Keep workspaces")
    parser.add_argument("--json-output", default=None)
    parser.add_argument("--status-file", default=None, help="Write progress status to this file")
    parser.add_argument("--rerun-on-fail", action="store_true", help="Resume: keep passed, re-run failed")
    parser.add_argument("--fresh", action="store_true", help="Ignore checkpoint, run from scratch")
    args = parser.parse_args()

    if args.tasks:
        task_ids = args.tasks
    elif args.failed_only:
        task_ids = FAILED_TASK_IDS
    elif args.pilot:
        task_ids = PILOT_TASK_IDS
    elif args.shot_list:
        task_ids = _load_shot_list()
    elif args.all:
        task_ids = [t.id for t in ALL_TASKS]
    else:
        parser.print_help()
        sys.exit(1)

    # Reasoning level: --thinking <level>, or --thinking-off as an alias for none.
    thinking_level = "none" if args.thinking_off else args.thinking

    json_output = args.json_output or f"/tmp/hermes_bench_{int(time.time())}.json"
    status_file = args.status_file or "/tmp/hermes_bench_status.json"

    print("Hermes Benchmark Runner")
    print(f"  Model: {args.model}")
    print(f"  Thinking: {thinking_level or 'default'}")
    print(f"  Tasks: {len(task_ids)}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  Timeout: {args.timeout}s")
    harness_version = resolve_harness_version(args.harness_version, detect=detect_harness_version)
    print(f"  Harness version: {harness_version}")
    print(f"  JSON: {json_output}")
    print(f"  Status: {status_file}")
    print()

    results = run_batch(
        task_ids, model=args.model, timeout=args.timeout,
        concurrency=args.concurrency, keep_workspace=args.keep,
        json_output=json_output, status_file=status_file,
        rerun_on_fail=args.rerun_on_fail, fresh=args.fresh,
        thinking=thinking_level, harness_version=harness_version,
    )

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    total_in = sum(r.get("agent_input_tokens", 0) for r in results)
    total_out = sum(r.get("agent_output_tokens", 0) for r in results)
    total_tok = total_in + total_out
    total_steps = sum(r.get("agent_steps", 0) or 0 for r in results)
    total_cache_read = sum(r.get("agent_cache_read_tokens", 0) or 0 for r in results)
    total_cache_write = sum(r.get("agent_cache_write_tokens", 0) or 0 for r in results)
    cost = round(compute_cost_rub(total_in, total_out, total_cache_read, total_cache_write, _get_model_rates(args.model)), 2)

    print()
    print("=" * 60)
    print(f"Results: {passed}/{total} passed ({passed/max(total,1):.1%})")
    print(f"Steps: {total_steps}")
    print(f"Tokens: {total_tok:,} (in: {total_in:,} / out: {total_out:,})")
    print(f"Cost: ~{cost:,.0f} ₽")
    print(f"JSON: {json_output}")

    failed = [r for r in results if not r["passed"]]
    if failed:
        print(f"\nFailed tasks ({len(failed)}):")
        for r in failed:
            print(f"  {r['task_id']}: {r['message'][:100]}")


if __name__ == "__main__":
    main()
