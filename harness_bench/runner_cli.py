"""Drive each benchmark task through an external CLI agent.

The default GigaChat runner builds an in-process `deepagents` agent. This
module is the alternative: for each task we shell out to a CLI agent (e.g.
`free-code` / Claude Code CLI) inside a fresh temp workspace, then run the
same verifier against the resulting files. That gives us an apples-to-apples
score for "what fraction of the bench would this CLI solve" without changing
the task set.

The CLI command is configurable, defaulting to:

    free-code -p --model haiku --dangerously-skip-permissions <prompt>

The prompt is passed as the last positional argument. We always set `cwd` to
the per-task temp directory and `--add-dir` is not needed because the CLI
defaults to operating on its own cwd.
"""

from __future__ import annotations

import gc
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

from harness_bench.core import Task
from harness_bench.runner import (
    TaskRun,
    _load_env_from_dotenv,
    _one_line_detail,
    _task_sort_key,
    summarize,
)
from harness_bench.tasks import ALL_TASKS, get_task

DEFAULT_CLI_COMMAND = (
    "free-code -p --model haiku --dangerously-skip-permissions"
)
"""Default CLI invocation. The prompt is appended as the final argument."""

DEFAULT_TIMEOUT_SECONDS = 600
"""Per-task timeout in seconds. Some tasks need pytest + multiple file edits."""

_TRANSIENT_ERROR_PATTERN = re.compile(
    r"(?:"
    # HTTP 4xx / 5xx
    r"status\s+[45]\d\d"
    # Node.js / libuv socket errors
    r"|ECONN(?:RESET|REFUSED|ABORTED)"
    r"|ETIMEDOUT|EAI_AGAIN|ENETUNREACH|EHOSTUNREACH|EPIPE"
    # TLS / socket disconnects
    r"|socket hang up"
    r"|socket disconnected"
    r"|TLS\s+(?:connection|handshake)"
    # Generic transport blips
    r"|connection\s+(?:refused|reset|timed out|terminated|closed)"
    r"|network\s+(?:error|timeout|unreachable)"
    r"|request\s+(?:failed|timeout|aborted)"
    r"|streaming\s+request\s+failed"
    r"|fetch\s+failed"
    r")",
    re.IGNORECASE,
)
"""Detect a transient network / HTTP error in subprocess stderr/stdout.

Captures both HTTP-level 4xx/5xx and transport-level failures (TLS handshake
aborts, socket disconnects, libuv error codes) so we retry every error that
isn't actually a model-quality issue.
"""

DEFAULT_TRANSIENT_RETRIES = 5
"""How many times to retry the CLI on a transient HTTP error before giving up."""

_BACKOFF_SCHEDULE = (30, 60, 120, 240, 300)
"""Progressive backoff (seconds) between successive retries.

The IFT GigaChat endpoint applies multi-minute IP throttles, so short
exponential backoffs (1-32s) don't outlive a single lockout window. This
schedule waits for the IP to be unblocked before the next attempt.
"""

_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
"""Per-token-URL cache of (access_token, expires_at_unix_seconds)."""

_CLEANUP_RETRY_DELAYS = (0.1, 0.25, 0.5, 1.0, 2.0)
"""Short retry window for Windows temp-dir cleanup races / stale handles."""

_PROCESS_TREE_SHUTDOWN_TIMEOUT = 10
"""Seconds to wait for pipes to close after killing a timed-out CLI tree."""


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    """Terminate a CLI process and its children without changing the CLI command.

    ``run-cli`` often launches Windows agents through ``cmd /c`` because the
    real executable can be a ``.cmd`` wrapper. Killing only that immediate
    ``cmd.exe`` leaves the actual agent process alive with inherited stdout /
    stderr handles, so ``communicate()`` can block long after the configured
    per-task timeout. On Windows, ``taskkill /T`` kills the whole tree rooted at
    the wrapper process. On POSIX, start a new process group and terminate that
    group.
    """
    if os.name == "nt":
        subprocess.run(  # noqa: S603,S607 — Windows process-tree cleanup helper
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    try:
        os.killpg(proc.pid, 15)
    except ProcessLookupError:
        return
    except OSError:
        proc.kill()


def _run_cli_subprocess(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    """Run the CLI command with a hard per-task process-tree timeout."""
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        start_new_session = True

    proc = subprocess.Popen(  # noqa: S603 — trusted local benchmark command
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        env=env,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(proc)
        try:
            proc.communicate(timeout=_PROCESS_TREE_SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _cleanup_workspace_keepalive(
    workspace_keepalive: TemporaryDirectory[str],
    *,
    task_id: str,
) -> Path | None:
    """Best-effort cleanup of a per-task temp workspace.

    On Windows, a CLI agent or shell child can keep a file handle/cwd inside the
    workspace for a moment after ``subprocess.run`` returns. ``TemporaryDirectory``
    then raises ``OSError: [WinError 145] The directory is not empty`` and used
    to abort the whole benchmark run after an otherwise completed task. Retry a
    few times, then leave the workspace behind with a warning instead of turning
    cleanup into a harness crash.
    """
    workspace = Path(workspace_keepalive.name)
    last_exc: OSError | None = None
    for attempt, delay in enumerate((0.0, *_CLEANUP_RETRY_DELAYS), start=1):
        if delay:
            gc.collect()
            time.sleep(delay)
        try:
            workspace_keepalive.cleanup()
            return None
        except OSError as exc:
            last_exc = exc
            if attempt <= len(_CLEANUP_RETRY_DELAYS):
                continue

    print(
        f"[WARN] cleanup failed for {task_id} workspace {workspace}: {last_exc}; "
        "leaving it for inspection",
        file=sys.stderr,
    )
    return workspace


def _get_gigachat_access_token() -> str | None:
    """Fetch / refresh a GigaChat IFT access token from `GIGACHAT_TOKEN_URL`.

    Activates only when all of `GIGACHAT_TOKEN_URL`, `GIGACHAT_USER`, and
    `GIGACHAT_PASSWORD` are set in the environment. The IFT endpoint exposes
    `POST /v1/token` with HTTP basic auth (user:password) and replies with
    JSON `{"tok": "<jwt>", "exp": <unix_ms>}`. We cache the token in process
    until 60 seconds before its reported expiry so concurrent task threads
    share one fetch.

    Returns the bearer token on success, or `None` when the env is not set
    up for token-based auth (caller should then leave the subprocess env
    alone). Network errors propagate.
    """
    token_url = os.getenv("GIGACHAT_TOKEN_URL")
    user = os.getenv("GIGACHAT_USER")
    password = os.getenv("GIGACHAT_PASSWORD")
    if not (token_url and user and password):
        return None
    with _TOKEN_LOCK:
        now = time.time()
        cached = _TOKEN_CACHE.get(token_url)
        if cached and cached[1] - 60 > now:
            return cached[0]
        import httpx  # noqa: PLC0415 — lazy: only used when token URL is set

        resp = httpx.post(
            token_url,
            auth=(user, password),
            verify=os.getenv("GIGACHAT_VERIFY_SSL_CERTS", "").lower() not in ("false", "0", "no"),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        tok = data["tok"]
        exp_ms = data.get("exp")
        exp = float(exp_ms) / 1000.0 if exp_ms else now + 600
        _TOKEN_CACHE[token_url] = (tok, exp)
        return tok


def _subprocess_env_with_token() -> dict[str, str] | None:
    """Build a copy of `os.environ` with a fresh `GIGACHAT_ACCESS_TOKEN`.

    Returns `None` when token-based auth is not configured, so the caller
    can fall back to inheriting the parent process env unchanged. When a
    token is available, the returned dict also clears `GIGACHAT_USER` /
    `GIGACHAT_PASSWORD` / `GIGACHAT_CREDENTIALS` so the downstream CLI does
    not try its own OAuth handshake.
    """
    token = _get_gigachat_access_token()
    if not token:
        return None
    env = dict(os.environ)
    env["GIGACHAT_ACCESS_TOKEN"] = token
    for k in ("GIGACHAT_USER", "GIGACHAT_PASSWORD", "GIGACHAT_CREDENTIALS"):
        env.pop(k, None)
    return env


def _get_gigachat_prom_access_token() -> str | None:
    """Fetch / refresh a GigaChat PROM access token via the ngw OAuth gateway.

    Activates only when `GIGACHAT_PROM_CREDENTIALS` is set (base64 of
    `client_id:client_secret`). Optional `GIGACHAT_PROM_AUTH_URL` overrides
    the gateway (default `https://ngw.devices.sberbank.ru:9443/api/v2/oauth`),
    `GIGACHAT_PROM_SCOPE` overrides the scope (default `GIGACHAT_API_PERS`).
    Cached per-AUTH_URL until 60s before expiry like the IFT helper.

    Returns the bearer token on success, or `None` when PROM env is not
    configured.
    """
    creds = os.getenv("GIGACHAT_PROM_CREDENTIALS")
    if not creds:
        return None
    auth_url = os.getenv(
        "GIGACHAT_PROM_AUTH_URL",
        "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
    )
    scope = os.getenv("GIGACHAT_PROM_SCOPE", "GIGACHAT_API_PERS")
    with _TOKEN_LOCK:
        now = time.time()
        cached = _TOKEN_CACHE.get(auth_url)
        if cached and cached[1] - 60 > now:
            return cached[0]
        import httpx  # noqa: PLC0415

        resp = httpx.post(
            auth_url,
            headers={
                "Authorization": f"Basic {creds}",
                "RqUID": "00000000-0000-0000-0000-000000000001",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"scope": scope},
            verify=os.getenv("GIGACHAT_VERIFY_SSL_CERTS", "").lower()
            not in ("false", "0", "no"),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        tok = data["access_token"]
        exp_ms = data.get("expires_at")
        exp = float(exp_ms) / 1000.0 if exp_ms else now + 600
        _TOKEN_CACHE[auth_url] = (tok, exp)
        return tok


def _subprocess_env_with_prom_token() -> dict[str, str] | None:
    """Build a copy of `os.environ` with a PROM `GIGACHAT_ACCESS_TOKEN`.

    PROM-PERS exposes a different model line-up than IFT (no GigaChat-3-*),
    so the chat URL is also swapped to `gigachat.devices.sberbank.ru/api/v1`
    and the model name is overridden via `GIGACHAT_PROM_MODEL`
    (default `GigaChat-2-Max`, the closest PROM-PERS analogue to
    GigaChat-3-Ultra on this bench).
    """
    token = _get_gigachat_prom_access_token()
    if not token:
        return None
    env = dict(os.environ)
    env["GIGACHAT_ACCESS_TOKEN"] = token
    env["GIGACHAT_BASE_URL"] = os.getenv(
        "GIGACHAT_PROM_BASE_URL",
        "https://gigachat.devices.sberbank.ru/api/v1",
    )
    for k in ("GIGACHAT_USER", "GIGACHAT_PASSWORD", "GIGACHAT_CREDENTIALS"):
        env.pop(k, None)
    return env


def _swap_model_in_cli_command(cli_command: str, new_model: str) -> str:
    """Replace ``--model X`` (or ``-m X``) in the CLI command with `new_model`.

    Used by the PROM fallback to swap GigaChat-3-Ultra (IFT-only) for
    GigaChat-2-Max (best PROM-PERS analogue) without forcing the caller to
    pass two cli-command strings.
    """
    parts = shlex.split(cli_command)
    for i, p in enumerate(parts):
        if p in ("--model", "-m") and i + 1 < len(parts):
            parts[i + 1] = new_model
            break
    return shlex.join(parts)


def run_task_cli(
    task: Task,
    *,
    cli_command: str = DEFAULT_CLI_COMMAND,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    keep_workspace: bool = False,
    transient_retries: int = DEFAULT_TRANSIENT_RETRIES,
) -> TaskRun:
    """Run a single task via the CLI agent and return its `TaskRun` result.

    Transient HTTP errors (4xx/5xx status codes printed by the CLI itself —
    typically rate-limit 403 / 429 or 5xx server blips from the upstream
    provider) are retried up to `transient_retries` times with exponential
    backoff before the task is counted as a real failure. Each retry runs in
    a fresh per-task temp workspace so the agent starts from clean fixtures.
    """
    _load_env_from_dotenv()
    workspace_keepalive: TemporaryDirectory | None = None
    base_argv = shlex.split(cli_command)
    last_result: subprocess.CompletedProcess[str] | None = None
    last_transient_excerpt: str | None = None
    started = time.monotonic()

    # AGENTS.md is the runtime-tool / memory-discipline convention used by
    # `deepagents` (and Codex CLI / Cursor): the file lives in the workspace
    # and is auto-read into the system prompt. Claude Code (`free-code`)
    # uses its own host-side memory at ~/.claude/projects/... and does NOT
    # auto-discover AGENTS.md, so tasks that depend on it (e.g.
    # `tasks_memory.py`) fail by design. When we detect a Claude-Code-like
    # CLI, inject the workspace AGENTS.md via `--append-system-prompt` so
    # the agent sees the same ambient instructions an AGENTS.md-native
    # runtime would.
    inject_agents_md = any(
        "free-code" in arg or arg.endswith("/claude") or arg == "claude"
        for arg in base_argv[:2]
    )

    try:
        for attempt in range(transient_retries + 1):
            if keep_workspace:
                workspace_path = Path(mkdtemp(prefix=f"hb_cli_{task.id}_"))
                workspace_keepalive = None
            else:
                workspace_keepalive = TemporaryDirectory(prefix=f"hb_cli_{task.id}_")
                workspace_path = Path(workspace_keepalive.name)

            task.setup(workspace_path)

            argv = list(base_argv)
            agents_md = workspace_path / "AGENTS.md"
            if inject_agents_md and agents_md.exists():
                argv += [
                    "--append-system-prompt",
                    agents_md.read_text(encoding="utf-8"),
                ]
            argv += [task.prompt]

            try:
                last_result = _run_cli_subprocess(
                    argv,
                    cwd=workspace_path,
                    timeout=timeout,
                    env=_subprocess_env_with_token(),
                )
            except subprocess.TimeoutExpired:
                return TaskRun(
                    task_id=task.id,
                    passed=False,
                    message="",
                    elapsed_seconds=time.monotonic() - started,
                    error=f"CLI timed out after {timeout}s",
                    workspace=workspace_path if keep_workspace else None,
                )
            except FileNotFoundError as exc:
                return TaskRun(
                    task_id=task.id,
                    passed=False,
                    message="",
                    elapsed_seconds=time.monotonic() - started,
                    error=f"CLI executable not found: {exc}",
                    workspace=workspace_path if keep_workspace else None,
                )
            except Exception:  # noqa: BLE001 — surface as failure
                return TaskRun(
                    task_id=task.id,
                    passed=False,
                    message="",
                    elapsed_seconds=time.monotonic() - started,
                    error=traceback.format_exc(),
                    workspace=workspace_path if keep_workspace else None,
                )

            outcome = task.verify(workspace_path)
            if outcome.passed:
                return TaskRun(
                    task_id=task.id,
                    passed=True,
                    message=outcome.message,
                    elapsed_seconds=time.monotonic() - started,
                    workspace=workspace_path if keep_workspace else None,
                )

            # Decide whether to retry. We retry on any transient network error
            # (HTTP 4xx/5xx or transport-level disconnect). Other failures
            # (verifier mismatch, model wrote the wrong content) get surfaced
            # immediately — retrying wouldn't change the outcome.
            combined = ((last_result.stderr or "") + "\n" + (last_result.stdout or ""))
            m = _TRANSIENT_ERROR_PATTERN.search(combined)
            if m and attempt < transient_retries:
                last_transient_excerpt = m.group(0)
                # Clean up the failed-attempt workspace before retrying, then
                # sleep with a progressive backoff (30s, 60s, 120s, 240s, 300s)
                # — long enough to outlive multi-minute IP throttles on the
                # IFT endpoint.
                if workspace_keepalive is not None:
                    _cleanup_workspace_keepalive(workspace_keepalive, task_id=task.id)
                    workspace_keepalive = None
                delay = _BACKOFF_SCHEDULE[min(attempt, len(_BACKOFF_SCHEDULE) - 1)]
                time.sleep(delay)
                continue

            # Verifier failed and the error isn't transient (or budget exhausted).
            # If we tried to retry at all, prefer the "gave up" wording so the
            # log surfaces retry exhaustion clearly even when the CLI also
            # exited non-zero.
            message = outcome.message
            if last_transient_excerpt:
                message = (
                    f"{outcome.message} | gave up after {transient_retries} transient retries "
                    f"({last_transient_excerpt!r})"
                )
            elif last_result.returncode != 0:
                tail = (last_result.stderr or last_result.stdout).strip()[-300:]
                message = f"{outcome.message} | CLI exit={last_result.returncode}: {tail!r}"

            # PROM fallback: when the primary path (typically IFT) exhausted
            # its retry budget on transient errors AND PROM credentials are
            # configured in the environment, retry the task once on PROM with
            # the closest available model (default GigaChat-2-Max). This isn't
            # "apples-to-apples" — the model is different — but it answers
            # "would this task have passed if our infra weren't throttling?".
            if last_transient_excerpt:
                prom_env = _subprocess_env_with_prom_token()
                if prom_env is not None:
                    prom_model = os.getenv("GIGACHAT_PROM_MODEL", "GigaChat-2-Max")
                    prom_argv = shlex.split(
                        _swap_model_in_cli_command(cli_command, prom_model)
                    ) + [task.prompt]
                    # Clean prior workspace and re-set up for PROM attempt.
                    if workspace_keepalive is not None:
                        _cleanup_workspace_keepalive(workspace_keepalive, task_id=task.id)
                        workspace_keepalive = None
                    if keep_workspace:
                        workspace_path = Path(mkdtemp(prefix=f"hb_cli_prom_{task.id}_"))
                    else:
                        workspace_keepalive = TemporaryDirectory(
                            prefix=f"hb_cli_prom_{task.id}_"
                        )
                        workspace_path = Path(workspace_keepalive.name)
                    task.setup(workspace_path)
                    try:
                        prom_result = _run_cli_subprocess(
                            prom_argv,
                            cwd=workspace_path,
                            timeout=timeout,
                            env=prom_env,
                        )
                    except subprocess.TimeoutExpired:
                        prom_result = None
                    except Exception:  # noqa: BLE001
                        prom_result = None
                    if prom_result is not None:
                        prom_outcome = task.verify(workspace_path)
                        prom_tag = f" [PROM-fallback model={prom_model}]"
                        if prom_outcome.passed:
                            return TaskRun(
                                task_id=task.id,
                                passed=True,
                                message=prom_outcome.message + prom_tag,
                                elapsed_seconds=time.monotonic() - started,
                                workspace=workspace_path if keep_workspace else None,
                            )
                        # PROM-fallback also failed — surface its message for visibility.
                        message = f"{message} | PROM-fallback({prom_model}): {prom_outcome.message}"

            return TaskRun(
                task_id=task.id,
                passed=False,
                message=message,
                elapsed_seconds=time.monotonic() - started,
                workspace=workspace_path if keep_workspace else None,
            )

        # Unreachable — the loop above always returns. Keep a deterministic
        # fallback so static analysis doesn't complain.
        raise RuntimeError("run_task_cli retry loop fell through")
    finally:
        if workspace_keepalive is not None:
            _cleanup_workspace_keepalive(workspace_keepalive, task_id=task.id)


def run_all_cli(
    task_ids: list[str] | None = None,
    *,
    cli_command: str = DEFAULT_CLI_COMMAND,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    keep_workspace: bool = False,
    concurrency: int = 1,
) -> list[TaskRun]:
    """Run a subset (or all) of the benchmark via the CLI agent."""
    _load_env_from_dotenv()
    targets = [get_task(tid) for tid in task_ids] if task_ids else list(ALL_TASKS)

    if concurrency <= 1:
        results: list[TaskRun] = []
        for task in targets:
            print(f"[START] {task.id}: {task.name}")
            run = run_task_cli(
                task,
                cli_command=cli_command,
                timeout=timeout,
                keep_workspace=keep_workspace,
            )
            results.append(run)
            status = "PASS" if run.passed else "FAIL"
            print(f"  [{status}] {run.elapsed_seconds:5.1f}s — {_one_line_detail(run)}")
            if keep_workspace and run.workspace:
                print(f"  workspace: {run.workspace}")
        return results

    print_lock = threading.Lock()
    completed = 0
    total = len(targets)
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_task = {
            executor.submit(
                run_task_cli,
                task,
                cli_command=cli_command,
                timeout=timeout,
                keep_workspace=keep_workspace,
            ): task
            for task in targets
        }
        for future in as_completed(future_to_task):
            run = future.result()
            results.append(run)
            with print_lock:
                completed += 1
                status = "PASS" if run.passed else "FAIL"
                print(
                    f"[{completed:3d}/{total}] [{status}] {run.task_id:36s} "
                    f"{run.elapsed_seconds:5.1f}s — {_one_line_detail(run)}"
                )
                if keep_workspace and run.workspace:
                    print(f"           workspace: {run.workspace}")
    results.sort(key=lambda r: _task_sort_key(r.task_id))
    return results


__all__ = [
    "DEFAULT_CLI_COMMAND",
    "DEFAULT_TIMEOUT_SECONDS",
    "run_all_cli",
    "run_task_cli",
    "summarize",
]

# Keep `os` imported in case future versions need to inspect env / PATH for
# locating the CLI binary or setting per-task env vars.
_ = os
