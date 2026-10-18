"""CI gold checks retain supported coverage and always close host sessions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness_bench.core import VerifyResult
from harness_bench.feasibility_tasks import trusted_service


@pytest.fixture
def annotations():
    script = Path(__file__).resolve().parents[1] / ".github/scripts/verify_gold_with_annotations.py"
    if not script.is_file():
        pytest.skip("CI annotation helper is intentionally excluded from the source distribution")
    spec = importlib.util.spec_from_file_location("gold_annotations_platform_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("failure", [None, "setup", "gold", "verify"])
def test_annotations_cleanup_runs_before_workspace_disappears(annotations, monkeypatch, failure):
    events = []

    def step(name):
        def run(workspace):
            assert workspace.is_dir()
            events.append(name)
            if name == failure:
                raise ValueError("synthetic failure")
            return VerifyResult(True)
        return run

    task = SimpleNamespace(id="task_999_fixture", gold_files={"answer": "yes"},
                           setup=step("setup"), apply_gold=step("gold"),
                           verify=step("verify"), cleanup=step("cleanup"))
    monkeypatch.setattr(annotations, "ALL_TASKS", [task])
    assert annotations.verify_gold_with_annotations() == int(failure is not None)
    assert events[-1] == "cleanup"
    assert events.count("cleanup") == 1


@pytest.mark.parametrize("missing", ["AF_UNIX", "UnixStreamServer"])
def test_missing_unix_capability_has_explicit_error(monkeypatch, tmp_path, missing):
    provider = trusted_service.socket if missing == "AF_UNIX" else trusted_service.socketserver
    monkeypatch.delattr(provider, missing, raising=False)
    assert not trusted_service.trusted_service_available()
    with pytest.raises(RuntimeError, match="Wave 19 requires Unix sockets"):
        trusted_service.HostSession(tmp_path, None, "task_999_fixture", "fingerprint")


def test_annotations_missing_unix_keeps_431_and_explicitly_skips_40(
    annotations, monkeypatch, capsys,
):
    completed = []

    def task(number):
        def setup(workspace):
            completed.append(number)
        return SimpleNamespace(id=f"task_{number}_fixture", gold_files={"answer": "yes"},
                               requires_trusted_service=number > 431, setup=setup,
                               apply_gold=lambda ws: None, verify=lambda ws: VerifyResult(True))

    monkeypatch.setattr(annotations, "ALL_TASKS", [task(n) for n in range(1, 472)])
    monkeypatch.setattr(annotations, "trusted_service_available", lambda: False)
    assert annotations.verify_gold_with_annotations() == 0
    assert completed == list(range(1, 432))
    output = capsys.readouterr().out
    assert "Gold-verification: 431/431 OK" in output
    assert "40 skipped, Unix-socket host service unavailable" in output
    assert output.count("[SKIP]") == 40
