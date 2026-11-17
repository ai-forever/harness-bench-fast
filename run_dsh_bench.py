#!/usr/bin/env python3
# FORK-OWNED: not in upstream ai-forever/harness-bench-fast
"""DeepSeek Harness (dsh) Benchmark Runner — runs harness-bench-fast tasks through
the official DeepSeek Harness CLI (``dsh``, ``@deepseek-ai/dsh``).

Uses ``dsh --profile headless --patch <patch.yml> --permission-mode workspace-write
--json '<task>'`` to run each task in an isolated workspace. The provider route is
injected through a per-run patch file (launcher flag *before* the app arguments);
usage is read from ``status.step_end.usage`` NDJSON events. Verifies with
harness-bench-fast verifiers.

Usage:
    python run_dsh_bench.py --pilot
    python run_dsh_bench.py --failed-only
    python run_dsh_bench.py --tasks task_01_create_hello
    python run_dsh_bench.py --tasks task_01_create_hello,task_13_count_csv_lines
    python run_dsh_bench.py --all
    python run_dsh_bench.py --shot-list
    python run_dsh_bench.py --shot-list --concurrency 2 --json-output results/dsh_shotlist_ds41flash.json
    python run_dsh_bench.py --all --rerun-on-fail
    python run_dsh_bench.py --shot-list --thinking high
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
from harness_bench.core import effective_task_timeout  # noqa: E402
from harness_bench.cost_resolver import compute_cost_rub, resolve_model_rates  # noqa: E402
from harness_bench.tasks import ALL_TASKS, SUITES, get_task  # noqa: E402

DEFAULT_MODEL = "deepseek-ai/DeepSeek-V4.1-Flash"
DEFAULT_TIMEOUT = 660
DEFAULT_CONCURRENCY = 2
DEFAULT_PERMISSION_MODE = "workspace-write"

# Reasoning levels accepted by ``--thinking`` (mapped to the dsh provider
# ``reasoning`` field; ``none``/``off`` both become ``off``).
THINKING_LEVELS = ("none", "off", "minimal", "low", "medium", "high", "max", "ultra")

# ``dsh`` launcher binary and an isolated harness home so user-level plugins /
# credentials never leak into a benchmark run.
DSH_BIN = os.environ.get("DSH_BIN") or shutil.which("dsh") or "/home/kap/.npm-global/bin/dsh"
DSH_HOME = os.environ.get("DSH_HOME", "/tmp/dsh-home-bench")

# Cloud.ru Foundation Models API — provider route injected via the patch file.
CLOUD_RU_BASE_URL = "https://foundation-models.api.cloud.ru/v1/"
# Dedicated benchmark credential. Priority: CLOUD_RU_BENCH_API_KEY ->
# CLOUD_RU_KAP_HERMES (env, then ~/.hermes/.env) -> production CLOUD_RU_API_KEY.
# It feeds DSH_BENCH_API_KEY (referenced by the patch's ``apiKeyEnv``); the
# source name is recorded in the run artifact (``summary.bench_credential``).
CLOUD_RU_API_KEY, BENCH_CREDENTIAL = resolve_bench_api_key()

# Provider-qualified model ids used by the OpenClaw/Pi runners
# (e.g. ``custom-foundation-models-api-cloud-ru/deepseek-ai/DeepSeek-V4.1-Flash``).
# dsh routes via the patch's provider block and expects the BARE model id.
PROVIDER_MODEL_PREFIXES = ("custom-foundation-models-api-cloud-ru/",)

# Pilot task IDs (same as the Hermes / Pi runners)
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


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without touching the network or a real API key)
# ---------------------------------------------------------------------------

def _bare_model_id(model: str) -> str:
    """Return the bare model id dsh expects, stripping a provider prefix.

    ``custom-foundation-models-api-cloud-ru/deepseek-ai/DeepSeek-V4.1-Flash`` ->
    ``deepseek-ai/DeepSeek-V4.1-Flash``. Unknown ids pass through unchanged.
    """
    for prefix in PROVIDER_MODEL_PREFIXES:
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def _patch_reasoning_level(thinking) -> str:
    """Map a ``--thinking`` level to the dsh provider ``reasoning`` value.

    ``none``/``off`` (and an unset level) become ``off``; every other supported
    level passes through verbatim.
    """
    if thinking is None:
        return "off"
    level = str(thinking).strip().lower()
    if level in ("", "none", "off"):
        return "off"
    return level


# dsh/pi-ai thinking levels (schema: off|minimal|low|medium|high|xhigh|max).
# Declaring ``reasoningEfforts`` on the model entry is what makes pi-ai treat the
# model as reasoning-capable: without it pi-ai assumes ``reasoning: false`` and
# any effort beyond ``off`` fails with ``UNSUPPORTED_REASONING_EFFORT``.
DSH_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


def _reasoning_efforts_block(indent: str) -> str:
    """YAML ``reasoningEfforts`` mapping for the model entry.

    Keys are the offered levels; values are the wire spellings dispatch sends.
    ``off`` carries no value (pi-ai reads an absent key as "supported, send
    nothing"); every other level maps to its own name (the value Cloud.ru
    accepts). ``ultra`` is intentionally absent — it is not a valid dsh level.
    """
    lines = [f"{indent}reasoningEfforts:"]
    for lvl in DSH_THINKING_LEVELS:
        lines.append(f"{indent}  {lvl}:" if lvl == "off" else f"{indent}  {lvl}: {lvl}")
    return "\n".join(lines) + "\n"


def build_provider_patch(model: str, thinking=None) -> str:
    """Return the YAML patch that injects the Cloud.ru provider route.

    The patch is passed to the dsh launcher via ``--patch`` (a launcher flag that
    precedes the app arguments). It defines the ``cloudru`` provider route
    (endpoint, credential env-var reference, and the reasoning level) plus the
    default agent model. Only the *name* of the credential env var appears here —
    never the secret itself.

    The model entry declares ``reasoningEfforts`` (so pi-ai offers the thinking
    levels) and ``compat.supportsDeveloperRole: false`` (Cloud.ru rejects the
    OpenAI ``developer`` role — it only accepts user/tool/system/assistant).
    """
    bare = _bare_model_id(model)
    level = _patch_reasoning_level(thinking)
    return (
        "- id: llm-pi-ai\n"
        "  config:\n"
        "    providers:\n"
        "      cloudru:\n"
        "        apiKeyEnv: DSH_BENCH_API_KEY\n"
        "        api: openai-completions\n"
        f"        baseURL: {CLOUD_RU_BASE_URL}\n"
        f"        reasoning: {level}\n"
        "        models:\n"
        f"          - id: {bare}\n"
        f"{_reasoning_efforts_block('            ')}"
        "            compat:\n"
        "              supportsDeveloperRole: false\n"
        "- id: agent-default-model\n"
        "  config:\n"
        "    provider: cloudru\n"
        f"    model: {bare}\n"
    )


def build_headless_command(prompt, *, patch_path, model=DEFAULT_MODEL,
                           permission_mode=DEFAULT_PERMISSION_MODE,
                           dsh_bin=DSH_BIN) -> list[str]:
    """Build the ``dsh`` headless argv for a single benchmark task.

    Shape: ``dsh --profile headless --patch <patch.yml> --permission-mode <mode>
    --json '<task>'``. Launcher flags (``--profile``/``--patch``) precede the app
    arguments; the task is one argv element (dsh joins multiple words). No secret
    is ever placed on the command line.
    """
    return [
        dsh_bin,
        "--profile", "headless",
        "--patch", str(patch_path),
        "--permission-mode", permission_mode,
        "--json",
        prompt,
    ]


def parse_dsh_jsonl(text: str) -> dict:
    """Parse ``dsh ... --json`` NDJSON from *text* into a usage/reason dict.

    Returns a dict with:

    - ``agent_input_tokens`` / ``agent_output_tokens`` / ``agent_total_tokens`` /
      ``agent_cache_read_tokens`` / ``agent_cache_write_tokens`` — summed over
      every ``status.step_end.usage`` event,
    - ``agent_steps`` — number of ``status.step_end`` events,
    - ``agent_tool_calls`` — number of ``tool_call`` events,
    - ``session_id`` — from ``session`` (falling back to ``final``),
    - ``final_text`` — the ``final.text`` payload,
    - ``reason`` — ``final.reason.kind`` (e.g. ``completed``).

    Non-JSON / non-object lines are ignored so a partially-captured or noisy
    stream still yields whatever usage arrived.
    """
    out = {
        "agent_input_tokens": 0,
        "agent_output_tokens": 0,
        "agent_total_tokens": 0,
        "agent_cache_read_tokens": 0,
        "agent_cache_write_tokens": 0,
        "agent_steps": 0,
        "agent_tool_calls": 0,
        "session_id": None,
        "final_text": None,
        "reason": None,
    }
    for raw_line in (text or "").splitlines():
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
        if etype == "session":
            sid = event.get("sessionId")
            if sid:
                out["session_id"] = str(sid)
        elif etype == "status":
            if event.get("phase") != "step_end":
                continue
            out["agent_steps"] += 1
            usage = event.get("usage") or {}
            out["agent_input_tokens"] += int(usage.get("inputTokens") or 0)
            out["agent_output_tokens"] += int(usage.get("outputTokens") or 0)
            out["agent_total_tokens"] += int(usage.get("totalTokens") or 0)
            out["agent_cache_read_tokens"] += int(usage.get("cacheReadTokens") or 0)
            out["agent_cache_write_tokens"] += int(usage.get("cacheWriteTokens") or 0)
        elif etype == "tool_call":
            out["agent_tool_calls"] += 1
        elif etype == "final":
            sid = event.get("sessionId")
            if sid:
                out["session_id"] = str(sid)
            out["final_text"] = event.get("text")
            reason = event.get("reason")
            if isinstance(reason, dict):
                out["reason"] = reason.get("kind")
    return out


def effective_timeout_for_task(timeout, task):
    """Resolve the effective per-task timeout using the shared floor/ceiling rule."""
    return effective_task_timeout(
        timeout,
        getattr(task, "min_timeout_seconds", None),
        getattr(task, "max_timeout_seconds", None),
    )


# ₽ pricing is resolved per-model from ~/.openclaw/openclaw.json via cost_resolver.
def _get_model_rates(model_id: str) -> dict:
    """Return per-model ₽/1M rates (input/output/cacheRead/cacheWrite)."""
    return resolve_model_rates(model_id)


def _has_model_rates(model_id: str) -> bool:
    """Return True when a real (non-fallback) rate exists for *model_id*.

    ``resolve_model_rates`` falls back to GLM-5.2 rates for unknown models; this
    is used only to flag the artifact, never to change the computation.
    """
    rates = resolve_model_rates(model_id)
    return rates != {"input": 239.12, "output": 751.52, "cacheRead": 0.0, "cacheWrite": 0.0}


def run_one_task(task, *, model=DEFAULT_MODEL, timeout=DEFAULT_TIMEOUT, keep_workspace=False,
                 thinking=None, permission_mode=DEFAULT_PERMISSION_MODE):
    """Run a single benchmark task through ``dsh --profile headless``."""
    started = time.monotonic()
    workspace_keepalive = None
    patch_keepalive = None
    eff = DEFAULT_TIMEOUT
    try:
        # Resolve the effective per-task timeout (task floor raises, ceiling clamps).
        eff = effective_timeout_for_task(timeout, task)

        if keep_workspace:
            workspace_path = Path(f"/tmp/dsh_bench_{task.id}")
            shutil.rmtree(workspace_path, ignore_errors=True)
            workspace_path.mkdir(parents=True, exist_ok=True)
        else:
            workspace_keepalive = TemporaryDirectory(prefix=f"dsh_bench_{task.id}_")
            workspace_path = Path(workspace_keepalive.name)

        # 1. Setup workspace with task fixtures
        task.setup(workspace_path)

        # 2. Build the provider patch (launcher flag; per-run temp file)
        patch_keepalive = TemporaryDirectory(prefix=f"dsh_patch_{task.id}_")
        patch_path = Path(patch_keepalive.name) / "patch.yml"
        patch_path.write_text(build_provider_patch(model, thinking), encoding="utf-8")

        # 3. Build the headless command
        cmd = build_headless_command(
            task.prompt,
            patch_path=patch_path,
            model=model,
            permission_mode=permission_mode,
        )

        # 4. Run dsh with cwd=workspace (the task workspace).
        agent_error = None
        stdout_text = ""
        returncode = None
        try:
            run_env = os.environ.copy()
            # Isolated harness home + dedicated benchmark credential.
            run_env["DSH_HOME"] = DSH_HOME
            run_env["DSH_BENCH_API_KEY"] = CLOUD_RU_API_KEY
            dsh_dir = str(Path(DSH_BIN).parent)
            run_env["PATH"] = dsh_dir + os.pathsep + run_env.get("PATH", "")

            result = subprocess.run(
                cmd,
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                timeout=eff + 60,
                encoding="utf-8",
                errors="replace",
                env=run_env,
            )
            stdout_text = result.stdout or ""
            returncode = result.returncode
            if result.returncode != 0:
                stderr_tail = (result.stderr or "")[-300:]
                agent_error = f"exit={result.returncode}: {stderr_tail}"
        except subprocess.TimeoutExpired:
            agent_error = f"timed out after {eff}s"
        except Exception as exc:  # noqa: BLE001
            agent_error = f"exception: {exc}"

        elapsed = time.monotonic() - started

        # 5. Parse usage / session / final reason from the NDJSON stream.
        parsed = parse_dsh_jsonl(stdout_text)
        reason = parsed.pop("reason")
        final_text = parsed.pop("final_text")
        session_id = parsed.pop("session_id")

        # 6. Verify the result.
        verify_result = task.verify(workspace_path)

        # 7. passed = exit 0 AND final reason "completed" AND verifier passed.
        passed = (
            returncode == 0
            and reason == "completed"
            and verify_result.passed
        )
        message = verify_result.message
        if reason is not None and reason != "completed":
            message = f"[reason={reason}] {message}"

        return {
            "task_id": task.id,
            "passed": passed,
            "message": message,
            "elapsed_seconds": round(elapsed, 2),
            "error": agent_error,
            "timeout": eff,
            "reason": reason,
            "final_text": final_text,
            "workspace": str(workspace_path) if keep_workspace else None,
            "session_id": session_id,
            **parsed,
        }

    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - started
        return {
            "task_id": task.id,
            "passed": False,
            "message": f"runner error: {exc}",
            "elapsed_seconds": round(elapsed, 2),
            "error": str(exc),
            "timeout": eff,
            "workspace": None,
        }
    finally:
        if workspace_keepalive is not None and not keep_workspace:
            workspace_keepalive.cleanup()
        if patch_keepalive is not None:
            patch_keepalive.cleanup()


def _rates_available(model_id: str) -> bool:
    return _has_model_rates(model_id)


def _write_results(results, path, model=None, *, timeout=None,
                   thinking=None, harness_version=None, task_ids=None,
                   permission_mode=DEFAULT_PERMISSION_MODE):
    """Write results JSON with summary. Thread-safe."""
    with _write_lock:
        passed = sum(1 for r in results if r["passed"])
        total = len(results)
        total_seconds = round(sum(r["elapsed_seconds"] for r in results), 1)
        total_in = sum(r.get("agent_input_tokens", 0) for r in results)
        total_out = sum(r.get("agent_output_tokens", 0) for r in results)
        total_tok = sum(r.get("agent_total_tokens", 0) for r in results)
        total_steps = sum(r.get("agent_steps", 0) or 0 for r in results)
        total_tool_calls = sum(r.get("agent_tool_calls", 0) or 0 for r in results)
        # Cache buckets are recorded EXPLICITLY: prefer the per-task bucket, else
        # derive it once here from ``total - input - output`` and store it back.
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
        model_id = model or DEFAULT_MODEL
        rates = _get_model_rates(model_id)
        rates_known = _rates_available(model_id)
        cost_rub = round(
            compute_cost_rub(total_in, total_out, total_cache_read, total_cache_write, rates),
            2,
        )
        task_id_list = list(task_ids) if task_ids is not None else [r.get("task_id") for r in results]
        payload = {
            **artifact_metadata(
                harness="dsh",
                task_ids=task_id_list,
                thinking=thinking,
                harness_version=harness_version,
                model=model_id,
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
                "total_cache_read_tokens": total_cache_read,
                "total_cache_write_tokens": total_cache_write,
                "total_tokens": total_tok,
                "total_steps": total_steps,
                "total_tool_calls": total_tool_calls,
                "estimated_cost_rub": cost_rub,
                "rates_available": rates_known,
                "bench_credential": BENCH_CREDENTIAL,
                "timeout": timeout,
                "permission_mode": permission_mode,
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
        total_tok = sum(r.get("agent_total_tokens", 0) for r in results) or (total_in + total_out)
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
              thinking=None, harness_version=None,
              permission_mode=DEFAULT_PERMISSION_MODE):
    """Run tasks with optional concurrency, checkpoint/resume, and progress reporting."""

    existing_results = {}
    if json_output and not fresh:
        existing_results = _load_checkpoint(json_output)
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
            run = run_one_task(task, model=model, timeout=timeout, keep_workspace=keep_workspace,
                               thinking=thinking, permission_mode=permission_mode)
            results.append(run)
            completed += 1
            status = "PASS" if run["passed"] else "FAIL"
            tokens = run.get("agent_total_tokens", 0)
            steps = run.get("agent_steps", 0) or 0
            print(f"  [{status}] {run['elapsed_seconds']:.1f}s — steps={steps} tokens={tokens} — {run['message'][:100]}", flush=True)
            if json_output:
                _write_results(results, json_output, model=model, timeout=timeout,
                               thinking=thinking, harness_version=harness_version,
                               task_ids=task_ids, permission_mode=permission_mode)
            if status_file:
                _write_status(status_file, completed, total_all, results, started_at, model=model)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            future_to_task = {}
            for tid in pending:
                task = get_task(tid)
                future = pool.submit(run_one_task, task, model=model, timeout=timeout,
                                     keep_workspace=keep_workspace, thinking=thinking,
                                     permission_mode=permission_mode)
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
                    _write_results(results, json_output, model=model, timeout=timeout,
                                   thinking=thinking, harness_version=harness_version,
                                   task_ids=task_ids, permission_mode=permission_mode)
                if status_file:
                    _write_status(status_file, completed, total_all, results, started_at, model=model)

    results.sort(key=lambda r: r["task_id"])
    return results


def _load_shot_list():
    """Load task IDs from shot_list.txt."""
    if not SHOT_LIST_FILE.exists():
        print(f"ERROR: shot list not found at {SHOT_LIST_FILE}", file=sys.stderr)
        sys.exit(1)
    return [line.strip() for line in SHOT_LIST_FILE.read_text().splitlines() if line.strip() and not line.startswith("#")]


def detect_harness_version() -> str | None:
    """Best-effort dsh harness (CLI) version for the harness_version artifact field."""
    try:
        out = subprocess.run([DSH_BIN, "--version"], capture_output=True,
                             text=True, timeout=15)
    except Exception:  # noqa: BLE001 - detection is best-effort
        return None
    lines = (out.stdout or out.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else None


def resolve_task_ids(args):
    """Resolve the task list from CLI flags; ``--tasks`` accepts comma-separated ids."""
    if args.tasks:
        task_ids: list[str] = []
        for token in args.tasks:
            task_ids.extend(part for part in token.split(",") if part)
        return task_ids
    if getattr(args, "suite", None):
        return [t.id for t in SUITES[args.suite]]
    if args.failed_only:
        return FAILED_TASK_IDS
    if args.pilot:
        return PILOT_TASK_IDS
    if args.shot_list:
        return _load_shot_list()
    if args.all:
        return [t.id for t in ALL_TASKS]
    return None


def main():
    parser = argparse.ArgumentParser(description="Run harness-bench-fast via DeepSeek Harness (dsh)")
    parser.add_argument("--tasks", nargs="+", help="Specific task IDs (space- or comma-separated)")
    parser.add_argument("--failed-only", action="store_true")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--shot-list", action="store_true", help="Run the 52-task shot list")
    parser.add_argument("--suite", choices=sorted(SUITES), help="Named task suite (harness_bench.tasks.SUITES)")
    parser.add_argument("--thinking", choices=THINKING_LEVELS, default="none",
                        help="Reasoning level injected into the provider patch "
                             "(none|off|minimal|low|medium|high|max|ultra); "
                             "recorded in the artifact `thinking` field")
    parser.add_argument("--permission-mode", default=DEFAULT_PERMISSION_MODE,
                        help="dsh permission preset (default: workspace-write)")
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

    task_ids = resolve_task_ids(args)
    if task_ids is None:
        parser.print_help()
        sys.exit(1)

    json_output = args.json_output or f"/tmp/dsh_bench_{int(time.time())}.json"
    status_file = args.status_file or "/tmp/dsh_bench_status.json"

    print("DeepSeek Harness (dsh) Benchmark Runner")
    print(f"  Model: {args.model} (bare: {_bare_model_id(args.model)})")
    print(f"  Thinking: {args.thinking} (patch reasoning: {_patch_reasoning_level(args.thinking)})")
    print(f"  Permission mode: {args.permission_mode}")
    print(f"  Tasks: {len(task_ids)}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  Timeout: {args.timeout}s")
    harness_version = resolve_harness_version(args.harness_version, detect=detect_harness_version)
    print(f"  Harness version: {harness_version}")
    print(f"  DSH_HOME: {DSH_HOME}")
    print(f"  JSON: {json_output}")
    print(f"  Status: {status_file}")
    print()

    results = run_batch(
        task_ids, model=args.model, timeout=args.timeout,
        concurrency=args.concurrency, keep_workspace=args.keep,
        json_output=json_output, status_file=status_file,
        rerun_on_fail=args.rerun_on_fail, fresh=args.fresh,
        thinking=args.thinking, harness_version=harness_version,
        permission_mode=args.permission_mode,
    )

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    total_in = sum(r.get("agent_input_tokens", 0) for r in results)
    total_out = sum(r.get("agent_output_tokens", 0) for r in results)
    total_tok = sum(r.get("agent_total_tokens", 0) for r in results)
    total_steps = sum(r.get("agent_steps", 0) or 0 for r in results)
    total_tool_calls = sum(r.get("agent_tool_calls", 0) or 0 for r in results)
    total_cache_read = sum(r.get("agent_cache_read_tokens", 0) or 0 for r in results)
    total_cache_write = sum(r.get("agent_cache_write_tokens", 0) or 0 for r in results)
    cost = round(compute_cost_rub(total_in, total_out, total_cache_read, total_cache_write,
                                  _get_model_rates(args.model)), 2)

    print()
    print("=" * 60)
    print(f"Results: {passed}/{total} passed ({passed/max(total,1):.1%})")
    print(f"Steps: {total_steps} (tool calls: {total_tool_calls})")
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
