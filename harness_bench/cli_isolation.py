"""Allowlisted Linux task sandbox. No host repository/home/tmp bind mounts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from contextlib import contextmanager
from pathlib import Path

SYSTEM_RUNTIME = (
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/etc/ld.so.cache",
    "/etc/alternatives",
    "/etc/ssl/certs",
    "/etc/ssl/openssl.cnf",
    "/etc/resolv.conf",
    "/etc/hosts",
    "/etc/nsswitch.conf",
    "/etc/passwd",
    "/etc/group",
    "/etc/localtime",
    "/etc/timezone",
    "/etc/os-release",
)
FORBIDDEN_RUNTIME = {"/", "/root", "/home", "/tmp", "/var", "/var/tmp", "/data", "/workspace"}
SAFE_WORKER_FILES = ("deepagents_worker.py", "openrouter_agent.py", "chat_openai.py", "agent_stats.py", "runtime_limits.py")


def validate_trusted_runtime_paths(runtime_paths: tuple[str, ...], executable: str | None = None) -> None:
    """Reject mounts that would expose the benchmark or its reference answers.

    Standalone worker files contain only agent/runtime code. Environment
    roots remain usable; installed benchmark packages are masked separately.
    """
    package = Path(__file__).resolve().parent
    repository = package.parent
    installed_package = repository.name in {"site-packages", "dist-packages"}
    approved = {(package / name).resolve() for name in SAFE_WORKER_FILES}
    candidates = [Path(raw).expanduser().resolve() for raw in runtime_paths]
    if executable:
        resolved_executable = shutil.which(executable)
        if resolved_executable or (Path(executable).is_absolute() and Path(executable).exists()):
            candidates.append(Path(resolved_executable or executable).expanduser().resolve())
    for path in candidates:
        if path in approved:
            continue
        environment = any((parent / "pyvenv.cfg").is_file() for parent in (path, *path.parents)
                          if parent != repository and parent.is_relative_to(repository))
        exposes_checkout = not installed_package and (
            repository.is_relative_to(path)
            or (path.is_relative_to(repository) and not environment)
        )
        package_parent = next((parent for parent in (path, *path.parents)
                               if parent.name == "harness_bench"), None)
        direct_package = path.is_dir() and (path / "harness_bench").is_dir()
        installed_container = path.name in {"site-packages", "dist-packages"}
        if exposes_checkout or package_parent is not None or (direct_package and not installed_container):
            raise ValueError(f"trusted-service runtime mount exposes benchmark source or reference data: {path}")


def sandbox_argv(
    argv: list[str],
    *,
    workspace: Path,
    scratch: Path,
    home: Path,
    runtime_paths: tuple[str, ...] = (),
    state: Path | None = None,
    service_socket: Path | None = None,
    protect_benchmark: bool = False,
) -> list[str]:
    """Build a fail-closed bubblewrap command; sources refer to the host namespace.

    Runtime paths are explicit trusted dependencies, never benchmark data roots.
    The private home is writable; callers may provision selected config files
    there before launch. Credentials should ordinarily come from the environment.
    """
    if sys.platform != "linux":
        raise RuntimeError(
            "task isolation requires Linux and bubblewrap; use --isolation none only for diagnostic runs"
        )
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("task isolation requires bubblewrap (bwrap executable not found)")
    if protect_benchmark:
        validate_trusted_runtime_paths(runtime_paths, argv[0])
    roots = [Path(p) for p in SYSTEM_RUNTIME if Path(p).exists()]
    for raw in runtime_paths:
        path = Path(raw).expanduser().absolute()
        if str(path.resolve()) in FORBIDDEN_RUNTIME or not path.exists():
            raise ValueError(f"invalid sandbox runtime path: {path}")
        roots.append(path)
        if path.resolve() != path:
            roots.append(path.resolve())
    executable = shutil.which(argv[0]) or (argv[0] if Path(argv[0]).is_absolute() else None)
    if not executable:
        raise FileNotFoundError(f"CLI executable not found: {argv[0]}")
    executable_path = Path(executable).absolute()
    # Custom launcher scripts are mounted as individual files, not their parent
    # directories: a launcher's neighbors may include grader/task/source data.
    for candidate in (executable_path, executable_path.resolve()):
        if not any(candidate == root or root in candidate.parents for root in roots):
            roots.append(candidate)
    command = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--cap-drop",
        "ALL",
    ]
    # Root needs host mount permissions for runtime paths owned by another UID.
    # Bubblewrap drops child capabilities and sets no_new_privs in both modes.
    if getattr(os, "geteuid", lambda: -1)() != 0:
        command += ["--unshare-user"]
    private_home = os.path.expanduser("~")
    command += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/dev/shm",
        "--bind",
        str(scratch),
        "/tmp",
        "--dir",
        "/var/tmp",
        "--bind",
        str(home),
        private_home,
        "--bind",
        str(workspace),
        str(workspace),
        "--setenv",
        "HOME",
        private_home,
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--setenv",
        "TMP",
        "/tmp",
        "--setenv",
        "TEMP",
        "/tmp",
    ]
    # Bind home before nested runtime mounts; otherwise /root/.local disappears.
    for root in roots:
        command += ["--ro-bind", str(root.resolve()), str(root)]
    # A virtualenv may contain a wheel of this benchmark. Its task/grader
    # package is not a runtime dependency and must not enter the namespace.
    hidden = set()
    benchmark_package = Path(__file__).resolve().parent
    for root in roots:
        if protect_benchmark and root.is_dir() and benchmark_package.is_relative_to(root.resolve()):
            hidden.add(benchmark_package)
        for pattern in (
            "lib/python*/site-packages/harness_bench",
            "lib/python*/dist-packages/harness_bench",
            "local/lib/python*/site-packages/harness_bench",
            "local/lib/python*/dist-packages/harness_bench",
        ):
            if root.is_dir():
                hidden.update(root.glob(pattern))
        if root.name in {"site-packages", "dist-packages"} and (root / "harness_bench").is_dir():
            hidden.add(root / "harness_bench")
    for package in sorted(hidden):
        command += ["--tmpfs", str(package)]
    # Installed deployments can place the standalone worker in a package that
    # was just masked. Re-expose only the approved runtime files, not tasks.
    approved = {(Path(__file__).resolve().parent / name).resolve() for name in SAFE_WORKER_FILES}
    for root in roots:
        if root.resolve() in approved and any(root.is_relative_to(package) for package in hidden):
            command += ["--ro-bind", str(root.resolve()), str(root)]
    if state is not None:
        state = state.resolve()
        if str(state) in FORBIDDEN_RUNTIME or not state.is_dir():
            raise ValueError(f"invalid per-task state path: {state}")
        command += ["--bind", str(state), str(state)]
    if service_socket is not None:
        socket_path = Path(service_socket).absolute()
        if not stat.S_ISSOCK(socket_path.lstat().st_mode):
            raise ValueError("trusted service endpoint must be a Unix socket")
        # Mount only the endpoint, never its parent or the host-owned history.
        # Its short absolute address is shared with the public transport client.
        command += ["--ro-bind", str(socket_path), str(socket_path)]
    command += ["--chdir", str(workspace), "--", *argv]
    return command


@contextmanager
def bind_fds(argv: list[str]):
    """Open allowed mount sources before entering an unprivileged userns.

    Descriptors pin the exact sources during mount setup and are closed after
    spawning bubblewrap. They do not grant broader filesystem permissions.
    """
    descriptors = []
    command = list(argv)
    try:
        if sys.platform == "linux" and Path(command[0]).name == "bwrap":
            for index, arg in enumerate(command):
                if arg in {"--bind", "--ro-bind"}:
                    descriptor = os.open(command[index + 1], os.O_PATH)
                    descriptors.append(descriptor)
                    command[index] = arg + "-fd"
                    command[index + 1] = str(descriptor)
        yield command, tuple(descriptors)
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def load_manifest(path: str | Path | None) -> tuple[str, ...]:
    if path is None:
        return ()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    paths = payload.get("runtime_paths") if isinstance(payload, dict) else None
    if not isinstance(paths, list) or not all(
        isinstance(p, str) and Path(p).is_absolute() for p in paths
    ):
        raise ValueError("sandbox manifest must contain runtime_paths: [absolute paths]")
    return tuple(paths)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")
    argv = sandbox_argv(
        command,
        workspace=args.workspace.resolve(),
        scratch=args.scratch.resolve(),
        home=args.home.resolve(),
        runtime_paths=load_manifest(args.manifest),
        state=args.state,
    )
    with bind_fds(argv) as (command, descriptors):
        for descriptor in descriptors:
            os.set_inheritable(descriptor, True)
        os.execv(command[0], command)


if __name__ == "__main__":
    main()
