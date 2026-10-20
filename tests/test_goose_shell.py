import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="Goose shell wrapper requires POSIX /bin/bash"
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/hbf-goose-shell"


def test_preserves_streams_and_exit_status(tmp_path):
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "-c", "printf hello; printf error >&2; exit 7"],
        env={**os.environ, "TMPDIR": str(tmp_path)},
        capture_output=True,
    )
    assert (run.returncode, run.stdout, run.stderr) == (7, b"hello", b"error")


def test_large_output_kept_on_disk_with_bounded_preview(tmp_path):
    command = f"""{sys.executable} -c 'import os; [os.write(1,b"x"*65536) for _ in range(1024)]; os.write(2,b"done")' """
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "-c", command],
        env={**os.environ, "TMPDIR": str(tmp_path)},
        capture_output=True,
    )
    assert run.returncode == 0
    assert len(run.stdout) < 11_000
    assert b"full output (67108864 bytes)" in run.stdout
    assert run.stderr == b"done"
    path = next(tmp_path.glob("hbf-goose-shell-*/stdout.log"))
    assert path.stat().st_size == 64 * 1024**2


def test_shell_dies_when_goose_kills_capture_wrapper(tmp_path):
    import time

    import pytest

    if sys.platform != "linux":
        pytest.skip("Linux parent-death signal")
    pidfile = tmp_path / "child.pid"
    with (tmp_path / "log").open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), "-c", f"echo $$ > {pidfile}; exec sleep 60"],
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(100):
                if pidfile.exists():
                    break
                time.sleep(0.01)
            child = int(pidfile.read_text())
            proc.kill()
            proc.wait(timeout=5)
            for _ in range(100):
                status = Path(f"/proc/{child}/stat")
                if not status.exists() or status.read_text().split(") ")[1].startswith("Z"):
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("shell survived capture wrapper")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
