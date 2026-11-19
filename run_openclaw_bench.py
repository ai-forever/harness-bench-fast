#!/usr/bin/env python3
# FORK-OWNED: not in upstream ai-forever/harness-bench-fast
"""OpenClaw Benchmark Runner — runs harness-bench-fast tasks through OpenClaw agent exec.

Uses `openclaw agent exec` to run each task in an isolated session with
the benchmark agent. Verifies results using harness-bench-fast verifiers.

Usage:
    python run_openclaw_bench.py --pilot           # 12 tasks (6 failed + 6 passed)
    python run_openclaw_bench.py --failed-only      # 6 failed tasks
    python run_openclaw_bench.py --tasks task_01_create_hello
    python run_openclaw_bench.py --all               # all 391 tasks
    python run_openclaw_bench.py --all --concurrency 3 --json-output results/oc_full_391.json --status-file /tmp/oc_bench_status.json
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import TemporaryDirectory

# Add harness-bench-fast to path
BENCH_DIR = Path(os.environ.get("BENCH_DIR", str(Path(__file__).parent)))
sys.path.insert(0, str(BENCH_DIR))

from harness_bench.artifact_metadata import artifact_metadata  # noqa: E402
from harness_bench.bench_credentials import resolve_bench_api_key  # noqa: E402
from harness_bench.cost_resolver import compute_cost_rub, resolve_model_rates  # noqa: E402
from harness_bench.tasks import ALL_TASKS, SUITES, get_task  # noqa: E402

# Dedicated benchmark credential (all models × harnesses bill the bench
# account). `openclaw agent exec` resolves the provider key from the
# OpenClaw secret store; we ALSO export the resolved bench key into the
# child environment so the run is isolated from the production budget.
CLOUD_RU_API_KEY, BENCH_CREDENTIAL = resolve_bench_api_key()


# ── Configuration ──────────────────────────────────────────────────────

DEFAULT_MODEL = "custom-foundation-models-api-cloud-ru/zai-org/GLM-5.2"
DEFAULT_TIMEOUT = 600  # seconds per task
# ₽ pricing is resolved per-model from ~/.openclaw/openclaw.json via cost_resolver.
# The default model rates are populated lazily in _get_model_rates(); callers supply
# their model arg at runtime so we cannot resolve at module load time.
def _get_model_rates(model_id: str) -> dict:
    """Return per-model ₽/1M rates (input/output/cacheRead/cacheWrite)."""
    return resolve_model_rates(model_id)

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

# `openclaw agent exec --json` OMITS `usage` entirely when token accounting is
# unavailable (cli/docs: "usage is omitted when unavailable"). An omitted usage
# is NOT a zero-token measurement: a run can execute dozens of turns and tool
# calls yet report nothing (see issue #449). Distinguish the two here so the
# runner never records 0 tokens for a run that merely failed to report usage.
_USAGE_BUCKETS = (
    "agent_input_tokens",
    "agent_output_tokens",
    "agent_total_tokens",
    "agent_reasoning_tokens",
    "agent_cache_read_tokens",
    "agent_cache_write_tokens",
)


def _reported_usage(usage: object) -> dict | None:
    """Return the envelope's `usage` mapping when the run actually reported tokens.

    Returns ``None`` (unavailable) for a missing/empty mapping, for the new
    envelope shape that marks usage unavailable
    (``usage.usageReport.state == "unavailable"``), or when no flat token
    buckets (``input``/``output``/``total``) are present. Callers must keep
    unavailable buckets as ``None`` — never coerce them to ``0`` (issue #449).
    """
    if not isinstance(usage, dict) or not usage:
        return None
    report = usage.get("usageReport")
    if isinstance(report, dict) and report.get("state") == "unavailable":
        return None
    if not any(bucket in usage for bucket in ("input", "output", "total")):
        return None
    return usage


def _find_trajectory_db(state_dir: Path | None) -> Path | None:
    """Return the state-dir sqlite that holds `trajectory_runtime_events`."""
    if not state_dir or not state_dir.is_dir():
        return None
    for db in sorted(state_dir.rglob("*.sqlite")):
        if db.name.endswith((".reindex-lock.sqlite",
                             ".generation-lock.sqlite",
                             ".generation-writer.sqlite")):
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                has = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='trajectory_runtime_events' LIMIT 1"
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if has:
            return db
    return None


def _trajectory_usage(state_dir: Path | None, session_id: str) -> dict | None:
    """Recover a run's token usage from the OpenClaw trajectory store.

    When an `agent exec` envelope omits usage
    (``usageReport.state == "unavailable"``), the run's cumulative usage is
    still recorded in ``<state-dir>/**/openclaw-agent.sqlite`` in the
    ``trajectory_runtime_events`` table (``data.usage``). Return an
    envelope-shaped usage dict for the entry with the largest ``total``, or
    ``None`` when nothing is recoverable (missing dir/db/table, bad JSON).
    """
    if not session_id:
        return None
    try:
        db = _find_trajectory_db(state_dir)
        if db is None:
            return None
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except (sqlite3.Error, OSError):
        return None
    best: dict | None = None
    best_total = 0.0
    try:
        cur = con.execute(
            "SELECT event_json FROM trajectory_runtime_events "
            "WHERE session_id = ?",
            (session_id,),
        )
        for (raw,) in cur:
            try:
                event = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            data = event.get("data") if isinstance(event, dict) else None
            usage = data.get("usage") if isinstance(data, dict) else None
            if not isinstance(usage, dict):
                continue
            total = usage.get("total")
            if not isinstance(total, (int, float)) or total <= 0:
                continue
            if total > best_total:
                best_total = float(total)
                best = {
                    "input": usage.get("input"),
                    "output": usage.get("output"),
                    "total": total,
                    "cacheRead": usage.get("cacheRead"),
                    "cacheWrite": usage.get("cacheWrite"),
                    "reasoningTokens": usage.get("reasoningTokens"),
                }
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return best


# Pinned bench config: `openclaw agent exec` otherwise runs against the
# operator's ambient config (which carries production plugins/MCP/skills) and
# is not reproducible. Pin an isolated minimal config instead (see
# openspec/changes/isolate-bench-openclaw-instance).
BENCH_CONFIG = BENCH_DIR / "bench" / "openclaw.bench.json"

# Envelope `error.kind` values that mark an infrastructure crash (not a task
# failure) and are safe to retry. A long run can hit `incomplete_turn` when the
# provider returns an incomplete/malformed tool call; that is transient, so we
# rerun the task rather than recording a false model failure.
RETRYABLE_ERROR_KINDS = ("incomplete_turn",)


def resolve_bench_config() -> Path:
    """Return the pinned bench config path (env override: OPENCLAW_BENCH_CONFIG).

    Fails explicitly when the file is missing — it must NOT silently fall back
    to the ambient config (design Q6 in isolate-bench-openclaw-instance).
    """
    path = Path(os.environ.get("OPENCLAW_BENCH_CONFIG", str(BENCH_CONFIG)))
    if not path.is_file():
        raise FileNotFoundError(
            f"bench config not found: {path} "
            "(commit bench/openclaw.bench.json or set OPENCLAW_BENCH_CONFIG)"
        )
    return path


def _envelope_error_kind(stdout: str | None, stderr: str | None,
                         returncode: int) -> str | None:
    """Best-effort classification of a failed `agent exec` run.

    Returns the envelope `error.kind` when present; falls back to a stderr /
    message match for the known incomplete-tool-call crash. Returns ``None`` for
    a successful run or an unclassified failure.
    """
    if returncode == 0:
        return None
    if stdout:
        try:
            envelope = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            envelope = None
        if isinstance(envelope, dict):
            err = envelope.get("error")
            if isinstance(err, dict):
                if err.get("kind"):
                    return str(err["kind"])
                msg = str(err.get("message") or "")
                if "incomplete" in msg.lower() or "malformed tool call" in msg.lower():
                    return "incomplete_turn"
    haystack = (stderr or "").lower()
    if "incomplete_turn" in haystack or "incomplete or malformed tool call" in haystack:
        return "incomplete_turn"
    return None

# Thread-safe print
_print_lock = threading.Lock()
def safe_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)


def run_one_task(
    task,
    *,
    model: str = DEFAULT_MODEL,
    timeout: int = DEFAULT_TIMEOUT,
    keep_workspace: bool = False,
    thinking: str | None = None,
    retries: int = 0,
) -> dict:
    """Run a single benchmark task through `openclaw agent exec`."""
    started = time.monotonic()
    workspace_keepalive = None
    state_keepalive = None

    try:
        if keep_workspace:
            workspace_path = Path(f"/tmp/oc_bench_{task.id}")
            workspace_path.mkdir(parents=True, exist_ok=True)
            state_path = Path(f"/tmp/oc_state_{task.id}")
            state_path.mkdir(parents=True, exist_ok=True)
        else:
            workspace_keepalive = TemporaryDirectory(prefix=f"oc_bench_{task.id}_")
            workspace_path = Path(workspace_keepalive.name)
            # Preserve the OpenClaw state dir for the run so usage can be
            # recovered from the trajectory when the envelope omits it
            # (`agent exec --state-dir` requires the dir to already exist).
            state_keepalive = TemporaryDirectory(prefix=f"oc_state_{task.id}_")
            state_path = Path(state_keepalive.name)

        # 1. Setup workspace with task fixtures
        task.setup(workspace_path)

        # 2. Run openclaw agent exec against the pinned bench config. Retry on
        #    a retryable infra crash (e.g. incomplete_turn) so a transient
        #    malformed tool call is not recorded as a model failure.
        bench_config = resolve_bench_config()
        cmd = [
            "openclaw", "agent", "exec",
            "--config", str(bench_config),
            "--model", model,
            "--cwd", str(workspace_path),
            "--state-dir", str(state_path),
            "--timeout", str(timeout),
            "--json",
        ]
        if thinking:
            cmd.extend(["--thinking", thinking])
        cmd.append(task.prompt)

        run_env = os.environ.copy()
        if CLOUD_RU_API_KEY:
            # Non-breaking: the provider config still governs if it ignores env.
            run_env["CLOUD_RU_API_KEY"] = CLOUD_RU_API_KEY
            run_env["CLOUD_RU_BENCH_API_KEY"] = CLOUD_RU_API_KEY

        agent_error = None
        result = None
        for attempt in range(retries + 1):
            agent_error = None
            try:
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
                if result.returncode == 0:
                    break
                stderr_tail = (result.stderr or "")[-300:]
                agent_error = f"exit={result.returncode}: {stderr_tail}"
                kind = _envelope_error_kind(result.stdout, result.stderr, result.returncode)
                if attempt < retries and kind in RETRYABLE_ERROR_KINDS:
                    safe_print(
                        f"[{task.id}] retryable infra crash ({kind}) — "
                        f"attempt {attempt + 2}/{retries + 1}",
                        flush=True,
                    )
                    time.sleep(2)
                    continue
                break
            except subprocess.TimeoutExpired:
                agent_error = f"timed out after {timeout}s"
                break
            except Exception as exc:
                agent_error = f"exception: {exc}"
                break

        elapsed = time.monotonic() - started

        # 3. Verify the result
        verify_result = task.verify(workspace_path)

        # 4. Extract token stats from JSON envelope
        # Envelope format: {ok, status, final, payloads, usage:{input,output,total,reasoningTokens},
        #                   costUsd, assistantTurns, toolSummary:{calls,tools,failures}, model, sessionId}
        token_stats = {bucket: None for bucket in _USAGE_BUCKETS}
        token_stats["agent_usage_available"] = False
        token_stats["agent_usage_source"] = None
        if result and result.stdout:
            try:
                envelope = json.loads(result.stdout)
            except (json.JSONDecodeError, AttributeError):
                envelope = None
            if isinstance(envelope, dict):
                usage = _reported_usage(envelope.get("usage"))
                session_id = envelope.get("sessionId") or ""
                usage_source = "envelope" if usage is not None else None
                if usage is None and session_id:
                    # Envelope omitted usage (usageReport.state=unavailable):
                    # recover the run's tokens from the preserved trajectory.
                    recovered = _trajectory_usage(state_path, session_id)
                    if recovered is not None:
                        usage = recovered
                        usage_source = "trajectory"
                tool_summary = envelope.get("toolSummary") or {}
                available = usage is not None
                if available:
                    token_stats = {
                        "agent_input_tokens": usage.get("input", 0),
                        "agent_output_tokens": usage.get("output", 0),
                        "agent_total_tokens": usage.get("total", 0),
                        "agent_reasoning_tokens": usage.get("reasoningTokens", 0),
                        # Explicit cache buckets (invariant:
                        # total = input + output + cacheRead + cacheWrite).
                        # Stored verbatim; only derived downstream when absent.
                        "agent_cache_read_tokens": usage.get("cacheRead"),
                        "agent_cache_write_tokens": usage.get("cacheWrite"),
                    }
                # else: keep the None buckets from above (usage unavailable).
                token_stats.update({
                    "agent_usage_available": available,
                    "agent_usage_source": usage_source,
                    "agent_steps": tool_summary.get("calls", 0),
                    "agent_turns": envelope.get("assistantTurns", 0),
                    "agent_cost_usd": envelope.get("costUsd"),
                    "agent_session_id": session_id,
                })

        return {
            "task_id": task.id,
            "passed": verify_result.passed,
            "message": verify_result.message,
            "elapsed_seconds": round(elapsed, 2),
            "error": agent_error,
            "workspace": str(workspace_path) if keep_workspace else None,
            "thinking": thinking,
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
            "agent_usage_available": False,
        }
    finally:
        if workspace_keepalive is not None and not keep_workspace:
            workspace_keepalive.cleanup()


def _write_results(results: list[dict], path: str, *,
                   harness_version: str | None = None,
                   thinking: str | None = None,
                   command: str | None = None,
                   model: str | None = None) -> None:
    """Write results in upstream-compatible format."""
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    # Buckets are None when a run's usage was unavailable; a missing
    # measurement must not be silently written as 0 tokens (issue #449).
    total_input = sum(int(r.get("agent_input_tokens") or 0) for r in results)
    total_output = sum(int(r.get("agent_output_tokens") or 0) for r in results)
    total_tokens = sum(int(r.get("agent_total_tokens") or 0) for r in results)
    total_seconds = sum(r["elapsed_seconds"] for r in results)
    total_steps = sum(r.get("agent_steps", 0) for r in results)
    # Per-model rates resolved from the live Cloud.ru catalog / config
    _rates = _get_model_rates(model or DEFAULT_MODEL)
    # Cache buckets are recorded EXPLICITLY so downstream readers never
    # re-derive them. The OpenClaw envelope exposes real provider cache fields
    # (usage.cacheRead / usage.cacheWrite, invariant total = input + output +
    # cacheRead + cacheWrite); when a task lacks them the bucket is derived ONCE
    # here from that invariant and stored per task and in the summary.
    total_cache_read = 0
    total_cache_write = 0
    total_reasoning = sum(int(r.get("agent_reasoning_tokens") or 0) for r in results)
    usage_unavailable: list[str] = []
    for r in results:
        if not r.get("agent_usage_available", True):
            # No usage reported -> keep the buckets unavailable (None); do not
            # manufacture a zero bucket from the total-invariant. Surface the
            # task so published totals are not read as complete.
            usage_unavailable.append(r.get("task_id"))
            continue
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
    cost_rub = compute_cost_rub(total_input, total_output, total_cache_read, total_cache_write, _rates)

    payload = {
        **artifact_metadata(
            harness="openclaw",
            task_ids=[r.get("task_id") for r in results],
            thinking=thinking,
            harness_version=harness_version,
            model=model or DEFAULT_MODEL,
        ),
        "command": command or f"python run_openclaw_bench.py --all --model {DEFAULT_MODEL}",
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / max(total, 1), 4),
        "tokens": total_tokens,
        "tasks": results,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / max(total, 1), 4),
            "total_seconds": round(total_seconds, 1),
            "total_steps": total_steps,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_reasoning_tokens": total_reasoning,
            "total_tokens": total_tokens,
            "total_cache_read_tokens": total_cache_read,
            "total_cache_write_tokens": total_cache_write,
            "estimated_cost_rub": round(cost_rub, 2),
            # A run whose envelope omitted `usage` executed but reported no
            # tokens; totals above are therefore lower bounds, and these task
            # ids need provider-billing backfill (issue #449).
            "usage_unavailable_tasks": sorted(t for t in usage_unavailable if t),
            "usage_unavailable_count": len(usage_unavailable),
            "bench_credential": BENCH_CREDENTIAL,
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _write_status(path: str, completed: int, total: int, passed: int, failed: int,
                  elapsed: float, last_task: str, tokens: int, cost: float):
    """Write JSON status file for monitoring."""
    remaining = total - completed
    avg_time = elapsed / max(completed, 1)
    eta = remaining * avg_time
    status = {
        "completed": completed,
        "total": total,
        "passed": passed,
        "failed": failed,
        "elapsed_seconds": round(elapsed, 1),
        "estimated_remaining_seconds": round(eta, 1),
        "total_tokens": tokens,
        "estimated_cost_rub": round(cost, 2),
        "last_task": last_task,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(path, "w") as f:
        json.dump(status, f, indent=2)


def load_checkpoint(path: str) -> dict[str, dict]:
    """Load existing results from JSON file (for --rerun-on-fail)."""
    if not Path(path).exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        tasks = data.get("tasks", data.get("results", []))
        return {t["task_id"]: t for t in tasks if isinstance(t, dict)}
    except (json.JSONDecodeError, KeyError):
        return {}


def run_concurrent(
    task_ids: list[str],
    *,
    model: str = DEFAULT_MODEL,
    timeout: int = DEFAULT_TIMEOUT,
    keep_workspace: bool = False,
    json_output: str | None = None,
    status_file: str | None = None,
    concurrency: int = 3,
    checkpoint: dict[str, dict] | None = None,
    thinking: str | None = None,
    harness_version: str | None = None,
    command: str | None = None,
    retries: int = 0,
) -> list[dict]:
    """Run tasks with ThreadPoolExecutor."""
    checkpoint = checkpoint or {}
    to_run = [tid for tid in task_ids if tid not in checkpoint or not checkpoint[tid].get("passed")]
    skipped = len(task_ids) - len(to_run)

    if skipped:
        safe_print(f"Checkpoint: {skipped} passed tasks skipped, {len(to_run)} to run")

    safe_print(f"Running {len(to_run)} tasks (concurrency={concurrency})")

    results = dict(checkpoint)  # start with checkpoint
    completed = len(checkpoint)
    passed = sum(1 for r in checkpoint.values() if r.get("passed"))
    failed = completed - passed
    start_time = time.monotonic()
    total_tokens = sum(int(r.get("agent_total_tokens") or 0) for r in checkpoint.values())
    total_input = sum(int(r.get("agent_input_tokens") or 0) for r in checkpoint.values())
    total_output = sum(int(r.get("agent_output_tokens") or 0) for r in checkpoint.values())
    lock = threading.Lock()

    def task_wrapper(tid: str, idx: int) -> dict:
        task = get_task(tid)
        safe_print(f"[{idx}/{len(task_ids)}] {task.id}: starting...", flush=True)
        run = run_one_task(task, model=model, timeout=timeout, keep_workspace=keep_workspace, thinking=thinking, retries=retries)
        status = "✅ PASS" if run["passed"] else "❌ FAIL"
        safe_print(f"[{idx}/{len(task_ids)}] {task.id}: [{status}] {run['elapsed_seconds']:.1f}s — {run['message'][:120]}", flush=True)
        if run.get("agent_usage_available") is False:
            safe_print(f"[{idx}/{len(task_ids)}] {task.id}: ⚠️ usage unavailable "
                       f"(envelope omitted `usage`) — needs billing backfill", flush=True)
        return run

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {}
        for i, tid in enumerate(to_run, skipped + 1):
            future = executor.submit(task_wrapper, tid, i)
            futures[future] = tid

        for future in as_completed(futures):
            tid = futures[future]
            try:
                run = future.result()
            except Exception as exc:
                run = {
                    "task_id": tid,
                    "passed": False,
                    "message": f"executor error: {exc}",
                    "elapsed_seconds": 0,
                    "error": str(exc),
                    "agent_usage_available": False,
                }

            with lock:
                results[tid] = run
                completed += 1
                if run["passed"]:
                    passed += 1
                else:
                    failed += 1
                total_tokens += int(run.get("agent_total_tokens") or 0)
                total_input += int(run.get("agent_input_tokens") or 0)
                total_output += int(run.get("agent_output_tokens") or 0)
                total_cache_read = sum(int(r.get("agent_cache_read_tokens") or 0) for r in results.values())
                total_cache_write = sum(int(r.get("agent_cache_write_tokens") or 0) for r in results.values())
                elapsed = time.monotonic() - start_time
                _live_rates = _get_model_rates(model)
                cost = compute_cost_rub(total_input, total_output, total_cache_read, total_cache_write, _live_rates)

                if json_output:
                    _write_results(list(results.values()), json_output,
                                   harness_version=harness_version, thinking=thinking,
                                   command=command, model=model)
                if status_file:
                    _write_status(status_file, completed, len(task_ids),
                                  passed, failed, elapsed, tid, total_tokens, cost)

    return list(results.values())


def resolve_task_ids(args):
    """Resolve task list from CLI flags (--tasks > --suite > --failed-only > --pilot > --all > --shot-list)."""
    if args.tasks:
        return args.tasks
    if getattr(args, "suite", None):
        return [t.id for t in SUITES[args.suite]]
    if args.failed_only:
        return FAILED_TASK_IDS
    if args.pilot:
        return PILOT_TASK_IDS
    if args.all:
        return [t.id for t in ALL_TASKS]
    if args.shot_list:
        shot_file = BENCH_DIR / "shot_list.txt"
        return [line.strip() for line in shot_file.read_text().splitlines() if line.strip() and not line.startswith("#")]
    return None


def detect_harness_version() -> str | None:
    """Best-effort OpenClaw CLI version for the harness_version artifact field."""
    try:
        out = subprocess.run(["openclaw", "--version"], capture_output=True,
                             text=True, timeout=30)
        line = (out.stdout or out.stderr or "").strip().splitlines()
        return line[0].strip() if line else None
    except Exception:
        return None


def resolve_harness_version(explicit: str | None) -> str | None:
    """Explicit --harness-version > env HARNESS_BENCH_HARNESS_VERSION > autodetect."""
    return explicit or os.environ.get("HARNESS_BENCH_HARNESS_VERSION") or detect_harness_version()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run harness-bench-fast via OpenClaw")
    parser.add_argument("--tasks", nargs="+", help="Specific task IDs")
    parser.add_argument("--failed-only", action="store_true")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--shot-list", action="store_true", help="Run 52-task shot list")
    parser.add_argument("--suite", choices=sorted(SUITES), help="Named task suite (harness_bench.tasks.SUITES), e.g. reflect, feasibility, long")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--thinking", default=None, help="Thinking level: off | minimal | low | medium | high | max")
    parser.add_argument("--harness-version", default=None,
                        help="Harness (CLI) version recorded as harness_version; "
                             "defaults to `openclaw --version`, env HARNESS_BENCH_HARNESS_VERSION")
    parser.add_argument("--keep", action="store_true", help="Keep workspaces")
    parser.add_argument("--json-output", default=None)
    parser.add_argument("--status-file", default=None, help="JSON status file for monitoring")
    parser.add_argument("--concurrency", type=int, default=1, help="Parallel tasks")
    parser.add_argument("--retries", type=int, default=2,
                        help="Retries per task on a retryable infra crash (incomplete_turn); default 2")
    parser.add_argument("--rerun-on-fail", action="store_true", help="Skip passed tasks from checkpoint")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    task_ids = resolve_task_ids(args)
    if task_ids is None:
        parser.print_help()
        sys.exit(1)

    json_output = args.json_output or f"/tmp/oc_bench_{int(time.time())}.json"

    # Load checkpoint
    checkpoint = {}
    if args.rerun_on_fail and json_output:
        checkpoint = load_checkpoint(json_output)
        if checkpoint:
            print(f"Loaded {len(checkpoint)} tasks from checkpoint")

    harness_version = resolve_harness_version(args.harness_version)
    command = "python run_openclaw_bench.py " + " ".join(sys.argv[1:])

    print("🧪 OpenClaw Benchmark Runner")
    print(f"   Model: {args.model}")
    print(f"   Tasks: {len(task_ids)}")
    print(f"   Concurrency: {args.concurrency}")
    print(f"   Timeout: {args.timeout}s")
    print(f"   Harness version: {harness_version}")
    if args.thinking:
        print(f"   Thinking: {args.thinking}")
    print(f"   JSON: {json_output}")
    if args.status_file:
        print(f"   Status: {args.status_file}")
    print()

    results = run_concurrent(
        task_ids,
        model=args.model,
        timeout=args.timeout,
        thinking=args.thinking,
        harness_version=harness_version,
        command=command,
        keep_workspace=args.keep,
        json_output=json_output,
        status_file=args.status_file,
        concurrency=args.concurrency,
        checkpoint=checkpoint,
        retries=args.retries,
    )

    passed = sum(1 for r in results if r["passed"])
    print()
    print("=" * 60)
    print(f"Results: {passed}/{len(results)} passed ({passed/len(results):.1%})")
    print(f"JSON: {json_output}")

    failed = [r for r in results if not r["passed"]]
    if failed:
        print(f"\nFailed tasks ({len(failed)}):")
        for r in sorted(failed, key=lambda x: x["task_id"]):
            print(f"  ❌ {r['task_id']}: {(r.get('error') or r['message'])[:100]}")


if __name__ == "__main__":
    main()
