"""Resource-bounded, process-isolated Linux verification.

The verifier itself can import agent-written Python. It must never execute in
an orchestrator thread, even when the agent CLI was already sandboxed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
from tempfile import TemporaryDirectory

DEFAULT_MEMORY_BYTES = 1024 ** 3
DEFAULT_TIMEOUT = 120
LONG_TASK_TIMEOUT = 900
MAX_RESULT_BYTES = 16384
_VERIFIER_SLOTS = threading.BoundedSemaphore(8)


def _worker(task_id: str, workspace: str, result_path: str, memory: int) -> None:
    import resource

    # Set hard limits in the new interpreter, never preexec_fn in a threaded
    # parent. Descendants inherit these limits and cannot raise them.
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from harness_bench.tasks import get_task

    task = get_task(task_id)
    outcome = task.verify(Path(workspace))
    payload = {"passed": bool(outcome.passed), "message": str(outcome.message)[:3000]}
    Path(result_path).write_text(json.dumps(payload), encoding="utf-8")


def verify_bwrap(task_id: str, workspace: Path, *, timeout: float | None = None,
                 memory_bytes: int = DEFAULT_MEMORY_BYTES):
    if timeout is None:
        # Some long verifiers run two independent 300-second test suites.
        from harness_bench.tasks import get_task
        task = get_task(task_id)
        timeout = LONG_TASK_TIMEOUT if "long" in task.tags else DEFAULT_TIMEOUT
    # CLI concurrency can be 128; limit verifier allocations separately so the
    # resource ceiling does not become 128 GiB of simultaneous Python heaps.
    with _VERIFIER_SLOTS:
        return _verify_bwrap(task_id, workspace, timeout=timeout, memory_bytes=memory_bytes)


def _verify_bwrap(task_id: str, workspace: Path, *, timeout: float,
                  memory_bytes: int):
    from harness_bench.cli_isolation import sandbox_argv, bind_fds
    from harness_bench.core import VerifyResult

    if sys.platform != "linux":
        raise RuntimeError("bubblewrap verification requires Linux")
    package = Path(__file__).resolve().parent
    # Only verifier sources and this interpreter's dependencies. Never inherit
    # CLI runtime_paths, credentials, host home, or a broad repository mount.
    runtime = tuple(dict.fromkeys((str(package), sys.prefix, sys.base_prefix)))
    with TemporaryDirectory(prefix="hb_verify_") as temporary:
        private = Path(temporary)
        scratch, home = private / "tmp", private / "home"
        scratch.mkdir()
        home.mkdir()
        result_path = scratch / "result.json"
        argv = sandbox_argv(
            [sys.executable, "-I", str(Path(__file__).resolve()), task_id,
             str(workspace.resolve()), "/tmp/result.json", str(memory_bytes)],
            workspace=workspace.resolve(), scratch=scratch, home=home,
            runtime_paths=runtime,
        )
        # Verification needs no model or external network access.
        argv[1:1] = ["--unshare-net"]
        env = {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
               "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
               "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
        with bind_fds(argv) as (command, descriptors):
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=env, start_new_session=True, pass_fds=descriptors)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return VerifyResult(False, f"Verifier timed out after {timeout}s")
        finally:
            # Also remove residual descendants; the bwrap PID namespace prevents
            # detached verifier children from surviving namespace init exit.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        if proc.returncode < 0 or proc.returncode in (137, 139):
            return VerifyResult(False, f"Verifier exceeded resource limits or crashed (exit {proc.returncode})")
        if proc.returncode:
            raise RuntimeError(f"Isolated verifier failed to start or finish (exit {proc.returncode})")
        try:
            fd = os.open(result_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESULT_BYTES:
                    raise ValueError("invalid verifier result file")
                payload = json.loads(stream.read(MAX_RESULT_BYTES + 1))
            if type(payload.get("passed")) is not bool or not isinstance(payload.get("message"), str):
                raise ValueError("invalid verifier result schema")
        except (OSError, ValueError, AttributeError) as exc:
            raise RuntimeError(f"Isolated verifier produced no valid result: {exc}") from exc
        return VerifyResult(payload["passed"], payload["message"])


if __name__ == "__main__":
    _worker(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]))
