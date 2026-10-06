"""Runs only inside the task's RLI environment."""

from __future__ import annotations

import base64
import json
import sys
from dataclasses import asdict
from pathlib import Path

from deepagents.backends import LocalShellBackend

from harness_bench.tasks import get_task
from harness_bench.versioning import TASK_SET_VERSION

WORKSPACE = Path("/workspace")


def dispatch(request: dict) -> dict | list:
    args = request["args"]
    backend = LocalShellBackend(root_dir=WORKSPACE, virtual_mode=True, inherit_env=True)
    match request["operation"]:
        case "setup":
            if args["task_set_version"] != TASK_SET_VERSION:
                raise ValueError("RLI image and runner task-set versions differ")
            WORKSPACE.mkdir(parents=True, exist_ok=True)
            get_task(args["task_id"]).setup(WORKSPACE)
            result = {
                "memory": (WORKSPACE / "AGENTS.md").exists(),
                "skills": (WORKSPACE / ".agents/skills").is_dir(),
            }
        case "verify":
            result = asdict(get_task(args["task_id"]).verify(WORKSPACE))
        case "execute":
            result = asdict(backend.execute(**args))
        case "ls":
            result = asdict(backend.ls(**args))
        case "read":
            result = asdict(backend.read(**args))
        case "write":
            result = asdict(backend.write(**args))
            result.pop("files_update", None)
        case "edit":
            result = asdict(backend.edit(**args))
            result.pop("files_update", None)
        case "grep":
            result = asdict(backend.grep(**args))
        case "glob":
            result = asdict(backend.glob(**args))
        case "upload":
            result = [
                asdict(item)
                for item in backend.upload_files(
                    [(path, base64.b64decode(content)) for path, content in args["files"]]
                )
            ]
        case "download":
            result = []
            for item in backend.download_files(args["paths"]):
                data = asdict(item)
                if item.content is not None:
                    data["content"] = base64.b64encode(item.content).decode("ascii")
                result.append(data)
        case _:
            raise ValueError(f"Unknown RLI operation: {request['operation']}")
    return result


def main() -> None:
    request = json.loads(base64.b64decode(sys.stdin.buffer.read()))
    try:
        result = dispatch(request)
    except ValueError as exc:
        if request["operation"] not in {"ls", "read", "write", "edit", "grep", "glob"}:
            raise
        result = {"error": str(exc)}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
