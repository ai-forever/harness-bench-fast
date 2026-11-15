#!/usr/bin/env python3
# FORK-OWNED: not in upstream ai-forever/harness-bench-fast
"""Pi Benchmark Runner — runs harness-bench-fast tasks through Pi agent in Docker.

Uses `pi-docker -p "prompt" --model cloud-ru/MODEL --mode json` to run each task
in an isolated Docker container with Cloud.ru provider. Verifies with harness-bench-fast verifiers.

Usage:
    python run_pi_bench.py --pilot           # 12 tasks (6 failed + 6 passed)
    python run_pi_bench.py --tasks task_01_create_hello
    python run_pi_bench.py --shot-list         # 52-task stratified sample
    python run_pi_bench.py --shot-list --model "Qwen/Qwen3.6-35B-A3B"
    python run_pi_bench.py --shot-list --concurrency 2 --json-output results/pi_shotlist_glm52.json
"""

import argparse
import contextlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

# Default to the local harness-bench-fast checkout.
_DEFAULT_BENCH_DIR = str(Path(__file__).parent)
BENCH_DIR = Path(os.environ.get("BENCH_DIR", _DEFAULT_BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR))

SHOT_LIST_FILE = BENCH_DIR / "shot_list.txt"

from harness_bench.artifact_metadata import artifact_metadata, resolve_harness_version  # noqa: E402
from harness_bench.bench_credentials import resolve_bench_api_key  # noqa: E402
from harness_bench.core import effective_task_timeout  # noqa: E402
from harness_bench.cost_resolver import compute_cost_rub, resolve_model_rates  # noqa: E402
from harness_bench.tasks import SUITES, get_task  # noqa: E402

DEFAULT_MODEL = "zai-org/GLM-5.2"
DEFAULT_TIMEOUT = 660
DEFAULT_CONCURRENCY = 2


def effective_timeout_for_task(timeout, task):
    """Resolve the effective per-task timeout using the shared floor/ceiling rule.

    A task floor raises an explicitly finite value; the task ceiling clamps it.
    """
    return effective_task_timeout(
        timeout,
        getattr(task, "min_timeout_seconds", None),
        getattr(task, "max_timeout_seconds", None),
    )

# Pi Docker setup
PI_IMAGE = "pi-sandbox:latest"
PI_DOCKER_SCRIPT = str(Path.home() / "pi-agent" / "pi-docker.sh")
PI_MODELS_JSON = str(Path.home() / "pi-agent" / "models.json")

# Dedicated benchmark credential. Priority: CLOUD_RU_BENCH_API_KEY ->
# CLOUD_RU_KAP_HERMES (env, then ~/.hermes/.env) -> production CLOUD_RU_API_KEY.
# BENCH_CREDENTIAL records which source was used and is written to the artifact.
CLOUD_RU_API_KEY, BENCH_CREDENTIAL = resolve_bench_api_key()

# ₽ pricing is resolved per-model from ~/.openclaw/openclaw.json via cost_resolver.
def _get_model_rates(model_id: str) -> dict:
    """Return per-model ₽/1M rates (input/output/cacheRead/cacheWrite)."""
    return resolve_model_rates(model_id)

# Thread lock for checkpoint writes
_write_lock = threading.Lock()


def _docker_kill(container_name):
    """Best-effort kill+remove of a container left running after a client timeout.

    `subprocess.TimeoutExpired` kills the docker *CLI*, not the daemon-side
    container, so without this the container keeps burning CPU (diagnosing-bugs
    reg. orphan containers).
    """
    for args in (["docker", "kill", container_name], ["docker", "rm", "-f", container_name]):
        with contextlib.suppress(Exception):
            subprocess.run(args, capture_output=True, timeout=30)


def _extract_usage(obj, acc):
    """Count each assistant API response's usage EXACTLY ONCE.

    ``pi --mode json`` repeats the *same* ``usage`` object across several
    events: ``message_end`` (source of truth, one per assistant message),
    ``turn_end`` (the same message echoed) and ``agent_end.messages[]`` (the
    full transcript).  A recursive scan of the whole stream therefore
    multiplies input/output/reasoning/total by ~3x.  ``message_update`` delta
    events repeat it per chunk as well.

    We count ONLY a ``message_end`` event (``message.usage``) or a bare object
    that directly carries ``usage`` (partial/legacy streams that never wrap
    it), skip the ``message_update``/``turn_end``/``agent_end`` containers, and
    dedupe by ``responseId`` so any echo of the same call is ignored.  Cache
    buckets (``cacheRead``/``cacheWrite``) are captured explicitly instead of
    being dropped.
    """
    if not isinstance(obj, dict):
        return
    etype = obj.get("type")
    if etype in ("message_update", "turn_end", "agent_end"):
        return

    usage = None
    message = obj.get("message") if isinstance(obj.get("message"), dict) else None
    if etype == "message_end" and message is not None and message.get("role") == "assistant":
        usage = message.get("usage")
    if usage is None and isinstance(obj.get("usage"), dict):
        usage = obj.get("usage")
    if not (isinstance(usage, dict) and any(k in usage for k in ("input", "output", "totalTokens"))):
        return

    key = obj.get("responseId") or (message or {}).get("responseId")
    seen = acc.setdefault("_seen", set())
    if key is not None:
        if key in seen:
            return
        seen.add(key)

    acc["input"] += int(usage.get("input") or 0)
    acc["output"] += int(usage.get("output") or 0)
    acc["reasoning"] += int(usage.get("reasoning") or 0)
    acc["cache_read"] += int(usage.get("cacheRead") or 0)
    acc["cache_write"] += int(usage.get("cacheWrite") or 0)
    acc["total"] += int(usage.get("totalTokens") or 0)
    cost = usage.get("cost", {})
    if isinstance(cost, dict):
        acc["cost"] += cost.get("total", 0) or 0
    acc["api_calls"] += 1

    content = (message or {}).get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "toolCall":
                acc["tool_calls"] += 1


# Pilot task IDs (same as Hermes runner)
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


def run_one_task(task, *, model=DEFAULT_MODEL, timeout=DEFAULT_TIMEOUT, keep_workspace=False,
                 thinking_level=None, allow_below_floor=False):
    """Run a single benchmark task through Pi agent in Docker.

    Pi runs in a Docker container with:
    - --mode json for structured output (tokens, steps, tool calls)
    - --no-session for ephemeral sessions (no persistence between tasks)
    - --approve to auto-approve project-local files
    - workspace mounted as /workspace
    - models.json mounted read-only from host
    """
    started = time.monotonic()
    workspace_keepalive = None

    # Resolve the per-task effective timeout: a task floor raises an explicitly
    # finite value and the ceiling clamps it (frozen protocol / ADR-0004).
    task_min = getattr(task, "min_timeout_seconds", None)
    below_floor = (task_min is not None and math.isfinite(timeout) and timeout < task_min)
    if below_floor and not allow_below_floor:
        msg = (f"skipped: --timeout {timeout}s is below task floor {task_min}s for "
               f"{task.id}; pass --allow-below-floor to run a non-comparable, cost-capped run")
        print(f"  [SKIP] {msg}", flush=True)
        return {
            "task_id": task.id,
            "passed": False,
            "skipped": True,
            "comparable": False,
            "message": msg,
            "error": msg,
            "elapsed_seconds": 0.0,
            "workspace": None,
        }
    eff = effective_timeout_for_task(timeout, task)
    comparable = not below_floor

    try:
        if keep_workspace:
            workspace_path = Path(tempfile.gettempdir()) / f"pi_bench_{task.id}"
            shutil.rmtree(workspace_path, ignore_errors=True)
            workspace_path.mkdir(parents=True, exist_ok=True)
        else:
            workspace_keepalive = TemporaryDirectory(prefix=f"pi_bench_{task.id}_")
            workspace_path = Path(workspace_keepalive.name)

        # 1. Setup workspace with task fixtures
        task.setup(workspace_path)

        # 1b. wave19 tasks expose a host-owned Unix socket; .svc/connection.json
        #     stores its ABSOLUTE host path. Mount that directory into the
        #     container at the same path so the public taskctl transport can
        #     connect (Fix C: otherwise submit -> "task service unavailable").
        socket_mount = []
        try:
            _conn = Path(workspace_path, ".svc", "connection.json")
            if _conn.is_file():
                _sock = json.loads(_conn.read_text(encoding="utf-8")).get("socket")
                if _sock:
                    _sock_dir = str(Path(_sock).parent)
                    socket_mount = ["-v", f"{_sock_dir}:{_sock_dir}"]
        except Exception:
            socket_mount = []

        # 2. Build Docker command directly (bypass pi-docker wrapper for --mode json)
        #    pi-docker.sh doesn't pass through --mode json, so we call docker directly.
        cloud_ru_model = f"cloud-ru/{model}"
        container_name = f"pi-bench-{task.id}-{int(started)}"
        cmd = [
            "docker", "run", "--rm",
            "--name", container_name,
            "-e", f"CLOUD_RU_API_KEY={CLOUD_RU_API_KEY}",
            "-e", f"HBF_TASK_TIMEOUT_SECONDS={eff:g}",
            "-v", f"{workspace_path}:/workspace",
            *socket_mount,
            "-v", f"{PI_MODELS_JSON}:/root/.pi/agent/models.json:ro",
            "-v", "pi-agent-home:/root/.pi/agent/sessions",
            PI_IMAGE,
            "--model", cloud_ru_model,
        ]
        if thinking_level:
            cmd += ["--thinking", thinking_level]
        cmd += [
            "--mode", "json",
            "--no-session",
            "--approve",
            "-p", task.prompt,
        ]

        # 3. Run Pi with cwd=workspace (though workspace is mounted, not cwd)
        #    Stream stdout to a file (not capture_output) so a timing-out run
        #    still yields partial NDJSON for metrics/diagnosis (Fix B), and
        #    kill the container by name on timeout (Fix A).
        agent_error = None
        result = None
        out_path = Path(tempfile.gettempdir()) / f"pi_bench_out_{task.id}_{int(started)}.ndjson"
        try:
            run_env = os.environ.copy()
            with open(out_path, "w", encoding="utf-8") as out_file:
                proc = subprocess.Popen(
                    cmd,
                    stdout=out_file,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=run_env,
                    stdin=subprocess.DEVNULL,
                )
                try:
                    _, stderr = proc.communicate(timeout=eff + 60)
                except subprocess.TimeoutExpired:
                    agent_error = f"timed out after {eff}s"
                    _docker_kill(container_name)
                    try:
                        _, stderr = proc.communicate(timeout=30)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        stderr = None
            stdout_text = out_path.read_text(encoding="utf-8", errors="replace")
            if not agent_error and proc.returncode not in (0, None):
                agent_error = f"exit={proc.returncode}: {(stderr or '')[-300:]}"
            result = SimpleNamespace(stdout=stdout_text, returncode=proc.returncode)

        except Exception as exc:
            agent_error = f"exception: {exc}"

        elapsed = time.monotonic() - started

        # 4. Verify the result
        verify_result = task.verify(workspace_path)

        # 5. Extract token/step stats from NDJSON (works on partial streams too)
        #    Pi --mode json emits one JSON object per line; usage lives under
        #    assistant messages. _extract_usage scans the whole stream, so a
        #    killed/timed-out run still yields partial metrics (Fix B).
        token_stats = {}
        if result and result.stdout:
            acc = {"input": 0, "output": 0, "reasoning": 0, "total": 0,
                   "cache_read": 0, "cache_write": 0,
                   "cost": 0.0, "api_calls": 0, "tool_calls": 0}
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _extract_usage(obj, acc)

            if acc["total"] > 0 or acc["api_calls"] > 0:
                token_stats = {
                    "agent_input_tokens": acc["input"],
                    "agent_output_tokens": acc["output"],
                    "agent_reasoning_tokens": acc["reasoning"],
                    "agent_cache_read_tokens": acc["cache_read"],
                    "agent_cache_write_tokens": acc["cache_write"],
                    "agent_total_tokens": acc["total"],
                    "agent_tool_calls": acc["tool_calls"],
                    "agent_api_calls": acc["api_calls"],
                    "agent_steps": acc["api_calls"],  # steps = API calls (agent turns)
                }

        return {
            "task_id": task.id,
            "passed": verify_result.passed,
            "message": verify_result.message,
            "elapsed_seconds": round(elapsed, 2),
            "error": agent_error,
            "timeout": eff,
            "comparable": comparable,
            "workspace": str(workspace_path) if keep_workspace else None,
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
            "timeout": eff,
            "comparable": comparable,
            "workspace": None,
        }
    finally:
        if workspace_keepalive is not None and not keep_workspace:
            workspace_keepalive.cleanup()


def _write_results(results, path, timeout=None, model=None, *,
                   thinking=None, harness_version=None, task_ids=None):
    """Write results JSON with summary. Thread-safe."""
    with _write_lock:
        passed = sum(1 for r in results if r["passed"])
        total = len(results)
        total_seconds = round(sum(r["elapsed_seconds"] for r in results), 1)
        total_in = sum(r.get("agent_input_tokens", 0) for r in results)
        total_out = sum(r.get("agent_output_tokens", 0) for r in results)
        total_reason = sum(r.get("agent_reasoning_tokens", 0) for r in results)
        total_tok = sum(r.get("agent_total_tokens", 0) for r in results)
        total_cache_read = sum(r.get("agent_cache_read_tokens", 0) or 0 for r in results)
        total_cache_write = sum(r.get("agent_cache_write_tokens", 0) or 0 for r in results)
        total_steps = sum(r.get("agent_steps", 0) or 0 for r in results)
        total_tool_calls = sum(r.get("agent_tool_calls", 0) or 0 for r in results)
        _rates = _get_model_rates(model or DEFAULT_MODEL)
        cost_rub = round(compute_cost_rub(total_in, total_out, total_cache_read, total_cache_write, _rates), 2)
        task_id_list = list(task_ids) if task_ids is not None else [r.get("task_id") for r in results]
        payload = {
            **artifact_metadata(
                harness="pi",
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
                "total_reasoning_tokens": total_reason,
                "total_cache_read_tokens": total_cache_read,
                "total_cache_write_tokens": total_cache_write,
                "total_tokens": total_tok,
                "total_steps": total_steps,
                "total_tool_calls": total_tool_calls,
                "estimated_cost_rub": cost_rub,
                "bench_credential": BENCH_CREDENTIAL,
                "timeout": timeout,
                "task_timeouts": {
                    r["task_id"]: r.get("timeout") for r in results if r.get("timeout") is not None
                },
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
        total_cr = sum(r.get("agent_cache_read_tokens", 0) or 0 for r in results)
        total_cw = sum(r.get("agent_cache_write_tokens", 0) or 0 for r in results)
        total_tok = total_in + total_out
        _rates = _get_model_rates(model or DEFAULT_MODEL)
        cost_rub = round(compute_cost_rub(total_in, total_out, total_cr, total_cw, _rates), 2)
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
               thinking_level=None, allow_below_floor=False, harness_version=None):
    """Run tasks with optional concurrency, checkpoint/resume, and progress reporting."""

    existing_results = {}
    if json_output and not fresh:
        existing_results = _load_checkpoint(json_path=json_output)
        if existing_results:
            if rerun_on_fail:
                skipped = [tid for tid in task_ids if tid in existing_results and existing_results[tid]["passed"]]
                pending = [tid for tid in task_ids if tid not in skipped]
                print(f"Checkpoint: {len(skipped)} passed tasks skipped, {len(pending)} to run", flush=True)
            else:
                skipped = [tid for tid in task_ids if tid in existing_results]
                pending = [tid for tid in task_ids if tid not in existing_results]
                print(f"Resume: {len(skipped)} tasks completed, {len(pending)} remaining", flush=True)
        else:
            pending = list(task_ids)
    else:
        pending = list(task_ids)

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
        for tid in pending:
            task = get_task(tid)
            print(f"[{completed+1}/{total_all}] {task.id}", flush=True)
            run = run_one_task(task, model=model, timeout=timeout, keep_workspace=keep_workspace, thinking_level=thinking_level, allow_below_floor=allow_below_floor)
            results.append(run)
            completed += 1
            status = "PASS" if run["passed"] else "FAIL"
            tokens = run.get("agent_total_tokens", 0)
            steps = run.get("agent_steps", 0) or 0
            print(f"  [{status}] {run['elapsed_seconds']:.1f}s — steps={steps} tokens={tokens} — {run['message'][:100]}", flush=True)
            if json_output:
                _write_results(results, json_output, timeout=timeout, model=model,
                               thinking=thinking_level, harness_version=harness_version,
                               task_ids=task_ids)
            if status_file:
                _write_status(status_file, completed, total_all, results, started_at, model=model)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            future_to_task = {}
            for tid in pending:
                task = get_task(tid)
                future = pool.submit(run_one_task, task, model=model, timeout=timeout, keep_workspace=keep_workspace, thinking_level=thinking_level, allow_below_floor=allow_below_floor)
                future_to_task[future] = tid

            for future in as_completed(future_to_task):
                tid = future_to_task[future]
                run = future.result()
                results.append(run)
                completed += 1
                status = "PASS" if run["passed"] else "FAIL"
                tokens = run.get("agent_total_tokens", 0)
                steps = run.get("agent_steps", 0) or 0
                print(f"[{completed}/{total_all}] {tid}: [{status}] {run['elapsed_seconds']:.1f}s — steps={steps} tokens={tokens} — {run['message'][:100]}", flush=True)
                if json_output:
                    _write_results(results, json_output, timeout=timeout, model=model,
                                   thinking=thinking_level, harness_version=harness_version,
                                   task_ids=task_ids)
                if status_file:
                    _write_status(status_file, completed, total_all, results, started_at, model=model)

    results.sort(key=lambda r: r["task_id"])
    return results


def _load_shot_list():
    if not SHOT_LIST_FILE.exists():
        print(f"ERROR: shot list not found at {SHOT_LIST_FILE}", file=sys.stderr)
        sys.exit(1)
    return [line.strip() for line in SHOT_LIST_FILE.read_text().splitlines() if line.strip() and not line.startswith("#")]


def detect_harness_version() -> str | None:
    """Best-effort Pi harness (CLI) version for the harness_version artifact field."""
    for cmd in ([PI_DOCKER_SCRIPT, "--version"], ["pi", "--version"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception:  # noqa: BLE001 - detection is best-effort
            continue
        lines = (out.stdout or out.stderr or "").strip().splitlines()
        if lines:
            return lines[0].strip()
    return None


def resolve_task_ids(args):
    """Resolve the task list from CLI flags (--tasks > --suite > --pilot > --shot-list)."""
    if args.tasks:
        return args.tasks
    if getattr(args, "suite", None):
        return [t.id for t in SUITES[args.suite]]
    if args.pilot:
        return PILOT_TASK_IDS
    if args.shot_list:
        return _load_shot_list()
    return None


def main():
    parser = argparse.ArgumentParser(description="Run harness-bench-fast via Pi agent (Docker)")
    parser.add_argument("--tasks", nargs="+", help="Specific task IDs")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--shot-list", action="store_true", help="Run the 52-task shot list")
    parser.add_argument("--suite", choices=sorted(SUITES), help="Named task suite (harness_bench.tasks.SUITES), e.g. reflect, feasibility, long")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model ID (without cloud-ru/ prefix)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--allow-below-floor", action="store_true",
                        help="Run tasks even when --timeout is below the task floor "
                             "(recorded as non-comparable)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--keep", action="store_true", help="Keep workspaces")
    parser.add_argument("--json-output", default=None)
    parser.add_argument("--status-file", default=None)
    parser.add_argument("--rerun-on-fail", action="store_true")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--thinking", default=None, help="Pi thinking level: off, minimal, low, medium, high, xhigh, max")
    parser.add_argument("--harness-version", default=None,
                        help="Harness (CLI) version recorded as harness_version; "
                             "defaults to env HARNESS_BENCH_HARNESS_VERSION or autodetect")
    args = parser.parse_args()

    task_ids = resolve_task_ids(args)
    if task_ids is None:
        parser.print_help()
        sys.exit(1)

    json_output = args.json_output or str(Path(tempfile.gettempdir()) / f"pi_bench_{int(time.time())}.json")
    status_file = args.status_file or str(Path(tempfile.gettempdir()) / "pi_bench_status.json")

    print("Pi Benchmark Runner (Docker + Cloud.ru)")
    print(f"  Model: cloud-ru/{args.model}")
    print(f"  Tasks: {len(task_ids)}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  Timeout: {args.timeout}s"
          + (" (below-floor allowed -> non-comparable)" if args.allow_below_floor else ""))
    if args.thinking:
        print(f"  Thinking: {args.thinking}")
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
        thinking_level=args.thinking, allow_below_floor=args.allow_below_floor,
        harness_version=harness_version,
    )

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    total_in = sum(r.get("agent_input_tokens", 0) for r in results)
    total_out = sum(r.get("agent_output_tokens", 0) for r in results)
    total_reason = sum(r.get("agent_reasoning_tokens", 0) for r in results)
    total_tok = sum(r.get("agent_total_tokens", 0) for r in results)
    total_cache_read = sum(r.get("agent_cache_read_tokens", 0) or 0 for r in results)
    total_cache_write = sum(r.get("agent_cache_write_tokens", 0) or 0 for r in results)
    total_steps = sum(r.get("agent_steps", 0) or 0 for r in results)
    total_tool_calls = sum(r.get("agent_tool_calls", 0) or 0 for r in results)
    cost = round(compute_cost_rub(total_in, total_out, total_cache_read, total_cache_write, _get_model_rates(args.model if hasattr(args, 'model') else DEFAULT_MODEL)), 2)

    print()
    print("=" * 60)
    print(f"Results: {passed}/{total} passed ({passed/max(total,1):.1%})")
    print(f"Steps: {total_steps} (tool calls: {total_tool_calls})")
    print(f"Tokens: {total_tok:,} (in: {total_in:,} / out: {total_out:,} / reasoning: {total_reason:,})")
    print(f"Cost: ~{cost:,.0f} ₽")
    print(f"JSON: {json_output}")

    failed = [r for r in results if not r["passed"]]
    if failed:
        print(f"\nFailed tasks ({len(failed)}):")
        for r in failed:
            print(f"  {r['task_id']}: {r['message'][:100]}")


if __name__ == "__main__":
    main()
