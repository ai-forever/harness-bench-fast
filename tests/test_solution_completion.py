"""End-to-end runner scoring uses timely host submissions, not the final reply."""

import json
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness_bench import __main__ as cli
from harness_bench import runner_cli
from harness_bench.feasibility_tasks import FEASIBILITY_CASES, FEASIBILITY_TASKS
from harness_bench.feasibility_tasks.trusted_service import (
    get_session,
    trusted_service_available,
)

pytestmark = pytest.mark.skipif(
    not trusted_service_available(), reason="Unix-socket host service unavailable"
)


@pytest.mark.parametrize("case", ["timely", "late", "file_only", "wrong", "invalid_call"])
@pytest.mark.parametrize("process_exit", ["timeout", "late_exit"])
def test_trusted_submission_survives_slow_final_reply(
    monkeypatch, tmp_path, case, process_exit,
):
    original = next(t for t in FEASIBILITY_TASKS if FEASIBILITY_CASES[t.id]["kind"] == "contract")
    task = replace(original, min_timeout_seconds=10, max_timeout_seconds=10)
    entry = FEASIBILITY_CASES[task.id]
    fixture = entry["pair"][entry["side"]]
    status = fixture["spec"]["status"]
    answer = {"status": status,
              "solution" if status == "solved" else "certificate": fixture["gold"]}
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner_cli, "_load_env_from_dotenv", lambda: None)
    monkeypatch.setattr(runner_cli, "_subprocess_env_with_token", lambda: {})
    monkeypatch.setattr(runner_cli, "sandbox_argv", lambda argv, **kwargs: argv)
    monkeypatch.setattr(cli, "get_task", lambda identity: task)
    runner_cli._STOP_REQUESTED.clear()

    def execute(argv, *, cwd, timeout, **kwargs):
        assert timeout == 10
        clock[0] += 11 if case == "late" else 5
        (cwd / "answer.json").write_text(json.dumps({} if case == "wrong" else answer))
        if case != "file_only":
            session = get_session(cwd)
            if case == "invalid_call":
                session.dispatch(["submit", "--file", "missing.json"], 50)
            session.dispatch(["submit", "--file", "answer.json"], 50)
        clock[0] = 112.0
        if process_exit == "timeout":
            raise subprocess.TimeoutExpired(argv, timeout, output="")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    result = runner_cli.run_task_cli(
        task, cli_command="fixture", isolation="bwrap", keep_workspace=True,
        artifacts_root=tmp_path / "artifacts", transient_retries=0,
    )
    try:
        assert result.passed is (case == "timely")
        assert result.failure_kind == (None if case == "timely" else "timeout")
        metadata = result.execution_history[0]
        assert metadata["agent_timed_out"] is True
        if case == "timely":
            assert metadata["completion_receipt"]["elapsed_seconds"] == 5
            assert metadata["phase"] == "verify"
        args = SimpleNamespace(
            task=task.id, workspace=result.workspace,
            trusted_evidence=metadata["trusted_service_evidence"], json=True,
        )
        assert cli._cmd_verify_task(args) == (0 if case == "timely" else 1)
        if case == "timely":
            # Offline replay must not promote a timeout on a forged metadata receipt.
            path = Path(metadata["execution"]) / "execution.json"
            payload = json.loads(path.read_text())
            payload["completion_receipt"]["elapsed_seconds"] = 0
            path.write_text(json.dumps(payload))
            with pytest.raises(ValueError, match="completion receipt"):
                cli._cmd_verify_task(args)
    finally:
        shutil.rmtree(result.workspace)
