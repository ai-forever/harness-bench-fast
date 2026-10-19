"""Wiring for the GitLab RLI runner (feature/harnessbench_rli, 6ae6f3d)."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness_bench.__main__ import build_parser
from harness_bench.rli_worker import dispatch
from harness_bench.runner_openrouter import run_all
from harness_bench.tasks import ALL_TASKS
from harness_bench.versioning import TASK_SET_VERSION


def test_rli_flag_defaults_off() -> None:
    args = build_parser().parse_args(["run-openrouter", "--isolation", "none"])
    assert args.rli is False


def test_rli_rejects_bubblewrap_and_keep() -> None:
    with pytest.raises(ValueError, match="--isolation none"):
        run_all(task_ids=["unused"], rli=True, isolation="bwrap")
    with pytest.raises(ValueError, match="--keep"):
        run_all(task_ids=["unused"], rli=True, isolation="none", keep_workspace=True)


def test_rli_worker_setup_checks_task_set_and_verify(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("harness_bench.rli_worker.WORKSPACE", tmp_path)
    task_id = ALL_TASKS[0].id
    with pytest.raises(ValueError, match="task-set"):
        dispatch(
            {"operation": "setup", "args": {"task_id": task_id, "task_set_version": "0.0.0"}}
        )
    info = dispatch(
        {"operation": "setup", "args": {"task_id": task_id, "task_set_version": TASK_SET_VERSION}}
    )
    assert set(info) == {"memory", "skills"}
    verified = dispatch({"operation": "verify", "args": {"task_id": task_id}})
    assert {"passed", "message"} <= set(verified)
    echoed = dispatch({"operation": "execute", "args": {"command": "printf hi", "timeout": 30}})
    assert echoed["exit_code"] == 0
    assert "hi" in echoed["output"]
