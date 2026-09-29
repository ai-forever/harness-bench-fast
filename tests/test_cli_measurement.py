"""Regression checks for measurement validity; no model/API calls."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness_bench import runner_cli
from harness_bench.__main__ import _exit_code
from harness_bench.cli_isolation import load_manifest, sandbox_argv
from harness_bench.runner import (
    TaskRun,
    _payload_reruns_on_continue,
    results_to_payload,
    write_results_json,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(runner_cli, "_load_env_from_dotenv", lambda: None)
    monkeypatch.setattr(runner_cli, "_subprocess_env_with_token", lambda: {})
    runner_cli._STOP_REQUESTED.clear()


def task(passed=False):
    return SimpleNamespace(
        id="task_01_fake",
        name="fake",
        prompt="solve fixture",
        setup=lambda cwd: (cwd / "input.txt").write_text("input"),
        verify=lambda cwd: SimpleNamespace(passed=passed, message="fixture result"),
    )


@pytest.mark.parametrize(
    "stderr",
    [
        "head: cannot open '/root/.local/bin/mini' for reading: No such file or directory",
        'Error: Unknown provider "papaya"',
        "/opt/hbf-cli/launch.sh: /dev/null: No such file or directory",
        "Error: ENOSPC: no space left on device",
    ],
)
def test_startup_failures_invalidate_measurement_even_if_verifier_passes(
    monkeypatch, tmp_path, stderr
):
    monkeypatch.setattr(
        runner_cli,
        "_run_cli_subprocess",
        lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", stderr),
    )
    run = runner_cli.run_task_cli(task(True), cli_command="fixture-cli", artifacts_root=tmp_path)
    assert not run.passed
    assert run.failure_kind == "infrastructure"
    payload = results_to_payload([run])
    assert payload["measurement_valid"] is False and payload["pass_rate"] is None
    assert _exit_code([run], allow_task_failures=True) == 1
    execution = Path(run.execution_history[0]["execution"])
    assert (execution / "stderr.log").read_text() == stderr


def test_tool_errors_do_not_become_infrastructure_or_trigger_retries(monkeypatch, tmp_path):
    transcript = json.dumps(
        {"type": "tool_result", "content": 'HTTP status 503; Error: Unknown provider "papaya"'}
    )
    monkeypatch.setattr(
        runner_cli,
        "_run_cli_subprocess",
        lambda *a, **kw: subprocess.CompletedProcess(
            a, 1, transcript, "ModuleNotFoundError: user_solution"
        ),
    )
    run = runner_cli.run_task_cli(task(), cli_command="fixture-cli", artifacts_root=tmp_path)
    assert run.failure_kind is None and not run.passed
    assert len(run.execution_history) == 1


def test_retry_preserves_original_model_and_all_execution_evidence(monkeypatch, tmp_path):
    commands = []

    def execute(argv, **kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "HTTP status 503")

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    monkeypatch.setattr(runner_cli, "_sleep_interruptibly", lambda delay: None)
    monkeypatch.setattr(
        runner_cli,
        "_subprocess_env_with_prom_token",
        lambda: pytest.fail("must never fall back to another model"),
    )
    run = runner_cli.run_task_cli(
        task(),
        cli_command="fixture-cli --model original",
        transient_retries=1,
        artifacts_root=tmp_path,
    )
    assert len(commands) == len(run.execution_history) == 2
    assert all(cmd[cmd.index("--model") + 1] == "original" for cmd in commands)
    assert run.failure_kind == "infrastructure"
    assert all(
        Path(h["execution"], "stderr.log").read_text() == "HTTP status 503"
        for h in run.execution_history
    )


def test_timeout_retains_stdout_and_native_scratch_trace(monkeypatch, tmp_path):
    def execute(argv, **kwargs):
        scratch = Path(kwargs["env"]["TMPDIR"])
        (scratch / "mini-swe-agent.traj.json").write_text('{"messages":[]}')
        raise subprocess.TimeoutExpired(
            argv, 1, output=b"partial response", stderr=b"partial stderr"
        )

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    run = runner_cli.run_task_cli(
        task(), cli_command="fixture-cli", timeout=1, artifacts_root=tmp_path
    )
    assert run.failure_kind == "timeout"
    artifact = Path(run.execution_history[0]["execution"])
    assert (artifact / "stdout.log").read_text() == "partial response"
    assert list((artifact / "private_state").rglob("mini-swe-agent.traj.json"))
    assert _payload_reruns_on_continue({"error": run.error, "rerun_on_continue": True}) is False


def test_resume_preserves_prior_infrastructure_executions(monkeypatch, tmp_path):
    fake = task(True)
    monkeypatch.setattr(runner_cli, "get_task", lambda task_id: fake)
    monkeypatch.setattr(
        runner_cli,
        "_run_cli_subprocess",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, "new evidence", ""),
    )
    path = tmp_path / "results.json"
    write_results_json(
        [
            TaskRun(
                fake.id,
                False,
                "",
                1,
                error="startup failed",
                failure_kind="infrastructure",
                execution_history=[{"execution": "old-artifact", "failure_kind": "infrastructure"}],
            )
        ],
        path,
    )
    results = runner_cli.run_all_cli([fake.id], cli_command="fixture-cli", json_output=path)
    history = results[0].execution_history
    assert history[0]["execution"] == "old-artifact" and history[1]["resume"]
    assert results[0].passed
    assert json.loads(path.read_text())["tasks"][0]["executions"] == 2


def test_real_subprocess_timeout_retains_complete_drain(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        runner_cli._run_cli_subprocess(
            [
                sys.executable,
                "-c",
                "import time; print('before timeout',flush=True); time.sleep(10)",
            ],
            cwd=tmp_path,
            timeout=0.1,
            env=None,
        )
    assert "before timeout" in exc.value.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="bwrap argv uses POSIX paths; isolation is Linux-only")
def test_allowlist_has_no_host_root_and_keeps_runtime_under_private_home(monkeypatch, tmp_path):
    from harness_bench import cli_isolation

    monkeypatch.setattr(cli_isolation.sys, "platform", "linux")
    monkeypatch.setattr(
        cli_isolation.shutil,
        "which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else "/usr/bin/python3",
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    command = sandbox_argv(
        ["python3", "--version"],
        workspace=tmp_path / "work",
        scratch=tmp_path / "scratch",
        home=tmp_path / "home",
        runtime_paths=(str(runtime),),
    )
    binds = [command[i + 1 : i + 3] for i, value in enumerate(command) if value == "--ro-bind"]
    assert ["/", "/"] not in binds
    assert command.index(str(tmp_path / "home")) < command.index(str(runtime))
    assert "--unshare-pid" in command
    with pytest.raises(ValueError, match="invalid sandbox"):
        sandbox_argv(
            ["python3"], workspace=tmp_path, scratch=tmp_path, home=tmp_path, runtime_paths=("/",)
        )


def test_manifest_rejects_relative_or_nonlist_paths(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"runtime_paths":["relative"]}')
    with pytest.raises(ValueError):
        load_manifest(path)


def test_enospc_saving_trace_keeps_primary_timeout_and_invalidates_measurement(
    monkeypatch, tmp_path
):
    import errno

    def execute(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1, output="partial")

    def full_disk(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    monkeypatch.setattr(runner_cli, "record_execution", full_disk)
    run = runner_cli.run_task_cli(
        task(), cli_command="fixture-cli", timeout=1, artifacts_root=tmp_path
    )
    assert run.failure_kind == "infrastructure" and not run.passed
    assert "CLI timed out" in run.error and "Cannot save complete CLI evidence" in run.error
    assert run.execution_history[0]["primary_failure_kind"] == "timeout"
    assert "No space left" in run.execution_history[0]["artifact_error"]


def test_artifact_initialization_failure_is_a_result(monkeypatch, tmp_path):
    def full_disk(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(runner_cli, "new_execution", full_disk)
    run = runner_cli.run_task_cli(task(), cli_command="fixture-cli", artifacts_root=tmp_path)
    assert run.failure_kind == "infrastructure"
    assert run.execution_history[0]["phase"] == "artifacts"


def test_failed_private_scratch_cleanup_preserves_primary_error(monkeypatch, tmp_path):
    retained = []

    def failed_cleanup(path, **kwargs):
        if Path(path).name.startswith("hb_cli_private_"):
            retained.append(path)
            raise OSError("cleanup denied")
        return real_cleanup(path, **kwargs)

    real_cleanup = runner_cli.shutil.rmtree
    monkeypatch.setattr(runner_cli.shutil, "rmtree", failed_cleanup)
    monkeypatch.setattr(
        runner_cli,
        "_run_cli_subprocess",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 1, "", "Error: Unknown provider papaya"
        ),
    )
    run = runner_cli.run_task_cli(task(), cli_command="fixture-cli", artifacts_root=tmp_path)
    assert run.failure_kind == "infrastructure" and "Unknown provider" in run.error
    assert "cleanup denied" in run.execution_history[0]["scratch_cleanup_warning"]
    for path in retained:
        real_cleanup(path)


def test_isolated_resume_rejects_legacy_unisolated_measurements(monkeypatch, tmp_path):
    output = tmp_path / "legacy.json"
    write_results_json([TaskRun("task_01_fake", True, "old", 1)], output)
    with pytest.raises(ValueError, match="new --json-output"):
        runner_cli.run_all_cli(["task_01_fake"], isolation="bwrap", json_output=output)


def test_allocation_failure_is_infrastructure(monkeypatch, tmp_path):
    def full_disk(**kwargs):
        raise OSError("no space for workspace")

    monkeypatch.setattr(runner_cli, "TemporaryDirectory", full_disk)
    run = runner_cli.run_task_cli(task(True), cli_command="fixture-cli", artifacts_root=tmp_path)
    assert not run.passed and run.failure_kind == "infrastructure"
    assert run.execution_history[0]["phase"] == "allocation"


@pytest.mark.parametrize(
    "returncode,stdout",
    [(1, "bootstrap failed"), (0, ""), (0, '{"type":"hbf_deepagents_result","stats":{}}\n' * 2)],
)
def test_native_worker_requires_one_terminal_event_before_verifier(
    monkeypatch, tmp_path, returncode, stdout
):
    monkeypatch.setattr(
        runner_cli,
        "_run_cli_subprocess",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, returncode, stdout, ""),
    )
    run = runner_cli.run_task_cli(
        task(True),
        cli_command="worker",
        artifacts_root=tmp_path,
        extra_env={"HBF_WORKER_API_KEY": "offline"},
    )
    assert not run.passed and run.failure_kind == "infrastructure"


@pytest.mark.parametrize("kind", ["recursion_limit", "model_error", "infrastructure"])
def test_native_worker_usage_and_reasoned_failure_survive(monkeypatch, tmp_path, kind):
    event = {
        "type": "hbf_deepagents_result",
        "failure_kind": kind,
        "message": "Traceback: failure details",
        "retryable": kind == "infrastructure",
        "stats": {
            "agent_steps": 4,
            "agent_input_tokens": 85,
            "agent_output_tokens": 7,
            "agent_peak_input_tokens": 30,
        },
    }
    calls = []

    def execute(*args, **kwargs):
        calls.append(1)
        return subprocess.CompletedProcess(args, 1, json.dumps(event), "HTTP 503")

    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    monkeypatch.setattr(runner_cli, "_sleep_interruptibly", lambda seconds: None)
    run = runner_cli.run_task_cli(
        task(True),
        cli_command="worker",
        artifacts_root=tmp_path,
        extra_env={"HBF_WORKER_API_KEY": "offline"},
    )
    assert not run.passed and run.failure_kind == kind
    assert bool(run.error) == (kind == "infrastructure")
    assert run.message == event["message"]
    assert len(calls) == (runner_cli.DEFAULT_TRANSIENT_RETRIES + 1 if kind == "infrastructure" else 1)
    assert (
        run.agent_steps == 4 and run.agent_input_tokens == 85 and run.agent_peak_input_tokens == 30
    )


def test_complete_private_state_includes_hermes_database_and_pi_config_without_host_links(tmp_path):
    from harness_bench.cli_artifacts import new_execution, record_execution

    state = tmp_path / "state"
    state.mkdir()
    (state / "state.db").write_bytes(b"sqlite fixture")
    (state / "models.json").write_text('{"providers":{}}')
    unrelated = tmp_path / "foreign-secret"
    unrelated.write_text("must not copy")
    (state / "foreign").symlink_to(unrelated)
    target = new_execution(tmp_path / "evidence", "task", "prompt")
    record_execution(
        target, stdout="", stderr="", metadata={}, trace_roots=(), state_roots=(state,)
    )
    assert (target / "private_state/0/state.db").read_bytes() == b"sqlite fixture"
    assert (target / "private_state/0/models.json").is_file()
    assert not (target / "private_state/0/foreign").exists()


def test_large_private_state_is_immutable_deduplicated_evidence(tmp_path):
    from harness_bench.cli_artifacts import CAS_MIN_BYTES, new_execution, record_execution

    state = tmp_path / 'state'
    state.mkdir()
    source = state / 'runtime.bin'
    initial = b'A' * CAS_MIN_BYTES
    source.write_bytes(initial)
    targets = []
    for value in (initial, initial, b'B' * CAS_MIN_BYTES):
        source.write_bytes(value)
        target = new_execution(tmp_path / 'evidence', 'task', 'prompt')
        record_execution(target, stdout='', stderr='', metadata={}, trace_roots=(), state_roots=(state,))
        targets.append(target)
    archived = [target / 'private_state/0/runtime.bin' for target in targets]
    assert archived[0].stat().st_ino == archived[1].stat().st_ino
    assert archived[0].stat().st_ino != archived[2].stat().st_ino
    source.write_bytes(b'changed live source')
    assert archived[0].read_bytes() == archived[1].read_bytes() == initial
    assert archived[2].read_bytes() == b'B' * CAS_MIN_BYTES
    assert not archived[0].stat().st_mode & 0o222
    refs = [json.loads((target / 'content_objects.json').read_text())['files'][0] for target in targets]
    assert refs[0]['sha256'] == refs[1]['sha256'] != refs[2]['sha256']
    assert len(list((tmp_path / 'evidence/_objects').iterdir())) == 2


def test_each_physical_execution_has_its_own_wire_session(monkeypatch, tmp_path):
    captured = []

    def execute(argv, **kwargs):
        captured.append(kwargs['env'])
        return subprocess.CompletedProcess(argv, 1, '', 'HTTP status 503')

    monkeypatch.setenv('HBF_WIRE_BASE_URL', 'http://127.0.0.1:12345/round01/mini')
    monkeypatch.setattr(runner_cli, '_run_cli_subprocess', execute)
    monkeypatch.setattr(runner_cli, '_sleep_interruptibly', lambda delay: None)
    run = runner_cli.run_task_cli(task(), cli_command='fixture', transient_retries=1, artifacts_root=tmp_path)
    assert len(captured) == 2
    assert captured[0]['HBF_API_BASE'] != captured[1]['HBF_API_BASE']
    for env, entry in zip(captured, run.execution_history, strict=True):
        expected = f"http://127.0.0.1:12345/round01/mini/{entry['wire_session']}/v1"
        assert env['HBF_API_BASE'] == env['OPENROUTER_BASE_URL'] == expected
        assert entry['wire_session'] == Path(entry['execution']).name
