"""Trusted task lifecycle: isolation, immutable host evidence, and fail-closed paths."""

from __future__ import annotations

import hashlib
import importlib
import json
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness_bench import cli_isolation, harbor_export, runner, runner_cli
from harness_bench.core import Task, VerifyResult


def secure_task(**kwargs):
    return Task(
        "task_999_trusted_fixture", "Trusted fixture", "Submit through the service.",
        lambda ws: VerifyResult(True), requires_trusted_service=True,
        runtime_protocol="wave19-host-service-v1", **kwargs,
    )


@pytest.fixture
def offline_cli(monkeypatch):
    monkeypatch.setattr(runner_cli, "_load_env_from_dotenv", lambda: None)
    monkeypatch.setattr(runner_cli, "_subprocess_env_with_token", lambda: {})
    runner_cli._STOP_REQUESTED.clear()


@pytest.mark.parametrize("completion", ["success", "timeout", "export_failure"])
def test_cli_freezes_before_verification_and_exports_outside_workspace(
    monkeypatch, tmp_path, offline_cli, completion,
):
    events = []
    state = {}

    class Session:
        socket_path = tmp_path / "host-only" / "task.sock"

        def start_attempt(self, timeout, *, started_at):
            events.append("start_attempt")

        def completion_receipt(self):
            return None

        def freeze(self):
            events.append("freeze")

        def export(self, path):
            assert "freeze" in events
            assert not path.is_relative_to(state["workspace"])
            events.append("export")
            if completion == "export_failure":
                raise OSError("synthetic evidence failure")
            path.write_text(json.dumps({"events": ["submitted"]}))

    session = Session()
    monkeypatch.setitem(sys.modules, "harness_bench.feasibility_tasks.trusted_service",
                        SimpleNamespace(get_session=lambda ws: session,
                                        close_all_sessions=lambda: None))

    def setup(ws):
        state["workspace"] = ws
        events.append("setup")

    def verify(ws):
        assert "freeze" in events
        assert completion != "timeout"
        events.append("verify")
        return VerifyResult(True)

    task = secure_task(setup_callback=setup,
                       cleanup_callback=lambda ws: events.append("cleanup"))
    task.verifier = verify

    def sandbox(argv, **kwargs):
        assert kwargs["service_socket"] == session.socket_path
        assert kwargs["service_socket"].parent not in kwargs["runtime_paths"]
        events.append("sandbox")
        return argv

    def execute(argv, **kwargs):
        events.append("execute")
        if completion == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output="partial")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(runner_cli, "sandbox_argv", sandbox)
    monkeypatch.setattr(runner_cli, "_run_cli_subprocess", execute)
    result = runner_cli.run_task_cli(task, cli_command="fixture", isolation="bwrap",
                                     artifacts_root=tmp_path / "artifacts")
    assert events[0] == "setup"
    assert events[-1] == "cleanup"
    assert events.index("export") > events.index("execute")
    assert events.index("start_attempt") < events.index("execute")
    if completion == "success":
        assert result.passed
        assert result.failure_kind is None
    else:
        assert not result.passed
        assert result.failure_kind == ("timeout" if completion == "timeout" else "infrastructure")
    metadata = result.execution_history[0]
    assert metadata["runtime_protocol"] == task.runtime_protocol
    assert metadata["task_contract_sha256"] == runner_cli._task_runtime_identity(task)["task_contract_sha256"]
    if completion != "export_failure":
        assert Path(metadata["trusted_service_evidence"]).is_file()
        assert len(metadata["trusted_service_sha256"]) == 64


def test_cli_cleans_partially_started_task(monkeypatch, tmp_path, offline_cli):
    closed = []

    def setup(ws):
        raise RuntimeError("synthetic setup failure")

    task = secure_task(setup_callback=setup, cleanup_callback=lambda ws: closed.append(ws))
    result = runner_cli.run_task_cli(task, cli_command="fixture", isolation="bwrap",
                                     artifacts_root=tmp_path)
    assert not result.passed
    assert result.failure_kind == "infrastructure"
    assert len(closed) == 1


@pytest.mark.parametrize("module_name", ["runner", "runner_pure", "runner_openrouter"])
def test_unsupported_native_run_rejected_before_model_build(monkeypatch, module_name):
    module = importlib.import_module(f"harness_bench.{module_name}")
    monkeypatch.setattr(module, "build_agent", lambda *a, **kw: pytest.fail("must not call model"))
    with pytest.raises(ValueError, match="isolation bwrap"):
        module.run_task(secure_task())


@pytest.mark.parametrize("module_name", ["runner", "runner_pure", "runner_openrouter"])
def test_unsupported_native_batch_rejected_before_credentials(monkeypatch, module_name):
    module = importlib.import_module(f"harness_bench.{module_name}")
    task = secure_task()
    monkeypatch.setattr(module, "get_task", lambda identity: task)
    monkeypatch.setattr(module, "_load_env_from_dotenv", lambda: pytest.fail("must reject before credentials"))
    with pytest.raises(ValueError, match="isolation bwrap"):
        module.run_all([task.id])


def test_cli_none_rejected_before_task_setup(offline_cli):
    task = secure_task(setup_callback=lambda ws: pytest.fail("must reject before setup"))
    with pytest.raises(ValueError, match="isolation bwrap"):
        runner_cli.run_task_cli(task, cli_command="fixture", isolation="none")


def test_harbor_rejection_preserves_existing_output(monkeypatch, tmp_path):
    sentinel = tmp_path / "preserve.txt"
    sentinel.write_text("existing user output")
    monkeypatch.setattr(harbor_export, "_select_tasks", lambda ids: [secure_task()])
    with pytest.raises(ValueError, match="trusted host service"):
        harbor_export.export_harbor_dataset(tmp_path, clean=True)
    assert sentinel.read_text() == "existing user output"


@pytest.mark.parametrize("setup_fails", [False, True])
def test_gold_always_closes_task_resources(monkeypatch, setup_fails):
    events = []

    def setup(ws):
        if setup_fails:
            raise RuntimeError("setup failed")

    task = secure_task(setup_callback=setup, cleanup_callback=lambda ws: events.append("cleanup"))
    monkeypatch.setattr(runner, "get_task", lambda identity: task)
    if setup_fails:
        with pytest.raises(RuntimeError, match="setup failed"):
            runner.verify_gold([task.id])
    else:
        assert runner.verify_gold([task.id])[0].passed
    assert events == ["cleanup"]


@pytest.mark.parametrize("identity_change", ["legacy", "protocol", "prompt", "fixture"])
def test_secure_resume_rejects_incompatible_task_contract(
    monkeypatch, tmp_path, offline_cli, identity_change,
):
    task = secure_task(setup_files={"input.json": "{}"})
    identity = runner_cli._task_runtime_identity(task)
    if identity_change == "legacy":
        identity = {}
    elif identity_change == "protocol":
        identity["runtime_protocol"] = "old-protocol"
    elif identity_change == "prompt":
        task.prompt += " Changed instruction."
    else:
        task.setup_files["input.json"] = '{"changed":true}'
    run = runner.TaskRun(task.id, True, "", 0.1,
                         execution_history=[{"cli_command_sha256": hashlib.sha256(b"fixture").hexdigest(),
                                             "isolation": "bwrap", "runtime_paths": [],
                                             "timeout": runner_cli.DEFAULT_TIMEOUT_SECONDS, **identity}])
    output = tmp_path / "results.json"
    runner.write_results_json([run], output)
    monkeypatch.setattr(runner_cli, "get_task", lambda identity: task)
    monkeypatch.setattr(runner_cli, "run_task_cli", lambda *a, **kw: pytest.fail("must reject before run"))
    with pytest.raises(ValueError, match="different task/service protocol"):
        runner_cli.run_all_cli([task.id], cli_command="fixture", isolation="bwrap", json_output=output)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets unavailable")
def test_sandbox_binds_only_socket_not_trusted_parent(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_isolation.sys, "platform", "linux")
    monkeypatch.setattr(cli_isolation.shutil, "which", lambda name: "/usr/bin/" + name)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    endpoint = trusted / "socket"
    # macOS AF_UNIX paths are short; pytest temp paths can exceed the limit.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="hbf_sock_") as short:
        endpoint = Path(short) / "s"
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(endpoint))
            argv = cli_isolation.sandbox_argv(["python3"], workspace=tmp_path / "ws",
                scratch=tmp_path / "scratch", home=tmp_path / "home", service_socket=endpoint)
            triples = [argv[i:i + 3] for i, arg in enumerate(argv) if arg in ("--bind", "--ro-bind")]
            assert ["--ro-bind", str(endpoint), str(endpoint)] in triples
            assert not any(str(endpoint.parent) in triple[1:] for triple in triples)


def test_sandbox_rejects_non_socket_service_endpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_isolation.sys, "platform", "linux")
    monkeypatch.setattr(cli_isolation.shutil, "which", lambda name: "/usr/bin/" + name)
    endpoint = tmp_path / "not-socket"
    endpoint.write_text("not an endpoint")
    with pytest.raises(ValueError, match="Unix socket"):
        cli_isolation.sandbox_argv(["python3"], workspace=tmp_path / "ws",
            scratch=tmp_path / "scratch", home=tmp_path / "home", service_socket=endpoint)


@pytest.mark.parametrize("mutation", ["timeout", "infrastructure", "bad_hash", "other_task", "other_protocol", "honest"])
def test_verify_cli_uses_host_execution_not_just_correct_contents(monkeypatch, tmp_path, mutation):
    from contextlib import contextmanager

    from harness_bench import __main__ as cli

    task = secure_task()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    execution = tmp_path / "execution"
    execution.mkdir()
    evidence = execution / "trusted_service.json"
    evidence.write_text('{"synthetic":"correct content"}')
    metadata = {"task_id": task.id, "isolation": "bwrap", "phase": "verify",
                "failure_kind": None, "passed": True,
                "trusted_service_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                **runner_cli._task_runtime_identity(task)}
    if mutation in ("timeout", "infrastructure"):
        metadata.update(failure_kind=mutation, phase="cli", passed=False)
    elif mutation == "bad_hash":
        metadata["trusted_service_sha256"] = "0" * 64
    elif mutation == "other_task":
        metadata["task_id"] = "different-task"
    elif mutation == "other_protocol":
        metadata["runtime_protocol"] = "different-protocol"
    (execution / "execution.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(cli, "get_task", lambda identity: task)

    @contextmanager
    def evidence_context(ws, path):
        assert mutation == "honest", "invalid host execution must not reach content verification"
        yield

    monkeypatch.setitem(sys.modules, "harness_bench.feasibility_tasks.trusted_service",
                        SimpleNamespace(MAX_EVIDENCE_BYTES=16_000_000,
                                        evidence_context=evidence_context,
                                        get_session=lambda ws: None,
                                        close_all_sessions=lambda: None))
    args = SimpleNamespace(task=task.id, workspace=workspace, trusted_evidence=evidence, json=True)
    if mutation in ("bad_hash", "other_task", "other_protocol"):
        with pytest.raises(ValueError, match="host execution metadata"):
            cli._cmd_verify_task(args)
    else:
        assert cli._cmd_verify_task(args) == (0 if mutation == "honest" else 1)


def test_verify_cli_rejects_missing_host_evidence(monkeypatch, tmp_path):
    from harness_bench import __main__ as cli
    task = secure_task()
    monkeypatch.setattr(cli, "get_task", lambda identity: task)
    args = SimpleNamespace(task=task.id, workspace=tmp_path, trusted_evidence=None, json=True)
    with pytest.raises(ValueError, match="requires --trusted-evidence"):
        cli._cmd_verify_task(args)


def test_standalone_apply_gold_rejects_secure_task_before_workspace_write(monkeypatch, tmp_path):
    from harness_bench import __main__ as cli
    task = secure_task()
    monkeypatch.setattr(cli, "get_task", lambda identity: task)
    workspace = tmp_path / "must-not-create"
    with pytest.raises(ValueError, match="active trusted host service"):
        cli._cmd_apply_gold(SimpleNamespace(task=task.id, workspace=workspace))
    assert not workspace.exists()


@pytest.mark.parametrize("relative", [".", "harness_bench", "harness_bench/feasibility_tasks", "harness_bench/tasks.py", "tests"])
def test_trusted_runtime_rejects_checkout_data(relative):
    repository = Path(cli_isolation.__file__).resolve().parent.parent
    with pytest.raises(ValueError, match="exposes benchmark"):
        cli_isolation.validate_trusted_runtime_paths((str(repository / relative),))


def test_trusted_runtime_rejects_foreign_repo_and_direct_package(tmp_path):
    foreign = tmp_path / "another-checkout"
    package = foreign / "harness_bench"
    package.mkdir(parents=True)
    for path in (foreign, package):
        with pytest.raises(ValueError, match="exposes benchmark"):
            cli_isolation.validate_trusted_runtime_paths((str(path),))


def test_trusted_runtime_allows_only_approved_standalone_worker_files():
    package = Path(cli_isolation.__file__).resolve().parent
    approved = tuple(str(package / name) for name in cli_isolation.SAFE_WORKER_FILES)
    cli_isolation.validate_trusted_runtime_paths(approved)
    with pytest.raises(ValueError, match="exposes benchmark"):
        cli_isolation.validate_trusted_runtime_paths((str(package / "core.py"),))


def test_nested_dist_packages_are_masked(monkeypatch, tmp_path):
    runtime = tmp_path / "usr"
    package = runtime / "local/lib/python3.12/dist-packages/harness_bench"
    package.mkdir(parents=True)
    monkeypatch.setattr(cli_isolation.sys, "platform", "linux")
    monkeypatch.setattr(cli_isolation.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli_isolation, "SYSTEM_RUNTIME", (str(runtime),))
    argv = cli_isolation.sandbox_argv(["python3"], workspace=tmp_path / "ws",
        scratch=tmp_path / "scratch", home=tmp_path / "home", protect_benchmark=True)
    masked = {argv[i + 1] for i, item in enumerate(argv) if item == "--tmpfs"}
    assert str(package) in masked


def test_cli_rejects_source_mount_before_starting_service(tmp_path, offline_cli):
    package = Path(cli_isolation.__file__).resolve().parent
    task = secure_task(setup_callback=lambda ws: pytest.fail("must reject before setup"))
    with pytest.raises(ValueError, match="exposes benchmark"):
        runner_cli.run_task_cli(task, cli_command="fixture", isolation="bwrap",
            runtime_paths=(str(package),), artifacts_root=tmp_path)


def test_installed_worker_can_use_environment_while_package_stays_hidden(monkeypatch, tmp_path):
    environment = tmp_path / "venv"
    package = environment / "lib/python3.12/site-packages/harness_bench"
    package.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("home = /usr/bin\n")
    for name in (*cli_isolation.SAFE_WORKER_FILES, "cli_isolation.py", "tasks.py"):
        (package / name).write_text("# synthetic fixture\n")
    monkeypatch.setattr(cli_isolation, "__file__", str(package / "cli_isolation.py"))
    workers = tuple(str(package / name) for name in cli_isolation.SAFE_WORKER_FILES)
    cli_isolation.validate_trusted_runtime_paths((str(environment), str(package.parent), *workers))
    with pytest.raises(ValueError, match="exposes benchmark"):
        cli_isolation.validate_trusted_runtime_paths((str(package),))
    monkeypatch.setattr(cli_isolation.sys, "platform", "linux")
    monkeypatch.setattr(cli_isolation.shutil, "which", lambda name: "/usr/bin/" + name)
    argv = cli_isolation.sandbox_argv(["python3"], workspace=tmp_path / "ws",
        scratch=tmp_path / "scratch", home=tmp_path / "home",
        runtime_paths=(str(environment), *workers), protect_benchmark=True)
    mask_index = next(i for i, arg in enumerate(argv)
                      if arg == "--tmpfs" and argv[i + 1] == str(package))
    later_mounts = [argv[i:i + 3] for i in range(mask_index, len(argv)) if argv[i] == "--ro-bind"]
    assert {entry[1] for entry in later_mounts} == set(workers)
