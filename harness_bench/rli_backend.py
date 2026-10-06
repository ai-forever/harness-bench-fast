from __future__ import annotations

import asyncio
import base64
import json
import os
import shlex
import threading
from typing import TYPE_CHECKING

from deepagents.backends.protocol import (
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)

from harness_bench.core import VerifyResult
from harness_bench.versioning import TASK_SET_VERSION

if TYPE_CHECKING:
    from gigarli import StepNDJSONResultEvent, StepRequest


class RLIBackend(SandboxBackendProtocol):
    """Preserve LocalShellBackend's virtual paths while executing every tool in RLI."""

    def __init__(self, task_id: str, *, timeout: int) -> None:
        from gigarli import RLIClient

        self.task_id = task_id
        self.timeout = timeout
        self.client = RLIClient(
            verify=os.getenv("RLI_VERIFY_SSL", "true").lower() != "false",
            timeout=120,
        )
        self.session_id: str | None = None
        self.closed = False
        self.error: Exception | None = None
        self.memory = False
        self.skills = False
        self._lock = threading.Lock()

    @property
    def id(self) -> str:
        if self.session_id is None or self.closed:
            raise RuntimeError("RLI session is not open")
        return self.session_id

    def start(self) -> None:
        from gigarli import CreateSessionRequest, SessionEnv

        session = asyncio.run(
            self.client.create_session(
                CreateSessionRequest(
                    env=SessionEnv(
                        name=os.getenv("HARNESS_BENCH_RLI_ENV", "harness-bench-fast"),
                        version=os.getenv("HARNESS_BENCH_RLI_VERSION", "0.16.0-rli.1"),
                        task_id=self.task_id,
                    ),
                    ttl_ms=(self.timeout + 600) * 1000,
                )
            )
        )
        self.session_id = session.session_id
        print(f"[RLI] {self.task_id}: {self.session_id}", flush=True)
        info = self._call("setup", task_id=self.task_id, task_set_version=TASK_SET_VERSION)
        self.memory = info["memory"]
        self.skills = info["skills"]

    def close(self) -> None:
        from gigarli import CloseSessionRequest

        self.closed = True
        if self.session_id is not None:
            asyncio.run(self.client.close_session(CloseSessionRequest(session_id=self.session_id)))

    async def _step(self, request: StepRequest) -> StepNDJSONResultEvent:
        from gigarli import StepNDJSONResultEvent

        async for event in self.client.step_ndjson(request):
            if isinstance(event, StepNDJSONResultEvent):
                return event
        raise RuntimeError("RLI stream ended without a command result")

    def _call(self, operation: str, **args):
        from gigarli import RequestLimits, StepAction, StepActionArgs, StepRequest

        if self.error is not None:
            raise self.error
        payload = base64.b64encode(
            json.dumps(
                {
                    "operation": operation,
                    "args": args,
                }
            ).encode()
        ).decode("ascii")
        command = shlex.join(
            [
                "/opt/harness-bench/bin/python",
                "-I",
                "-m",
                "harness_bench.rli_worker",
            ]
        )
        command += f" <<'HARNESS_RLI_REQUEST'\n{payload}\nHARNESS_RLI_REQUEST\n"
        try:
            with self._lock:
                if self.error is not None:
                    raise self.error
                result = asyncio.run(
                    self._step(
                        StepRequest(
                            session_id=self.id,
                            mode="single",
                            actions=[
                                StepAction(
                                    type="env_action",
                                    name="bash",
                                    args=StepActionArgs(cmd=command),
                                )
                            ],
                            limits=RequestLimits(deadline_ms=min(self.timeout + 60, 1500) * 1000),
                        )
                    )
                )
            if result.timed_out or result.exit_code != 0:
                raise RuntimeError(
                    f"RLI {operation} failed in {self.session_id}: "
                    f"exit={result.exit_code}, timed_out={result.timed_out}; "
                    f"{result.stderr or result.stdout}"
                )
            return json.loads(result.stdout)
        except Exception as exc:
            # Tool middleware can turn exceptions into model-visible messages.
            # Keep infrastructure errors so verification cannot publish a score.
            self.error = exc
            raise

    def verify(self) -> VerifyResult:
        return VerifyResult(**self._call("verify", task_id=self.task_id))

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return ExecuteResponse(
            **self._call(
                "execute",
                command=command,
                timeout=min(timeout or 120, self.timeout),
            )
        )

    def ls(self, path: str) -> LsResult:
        return LsResult(**self._call("ls", path=path))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return ReadResult(**self._call("read", file_path=file_path, offset=offset, limit=limit))

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(**self._call("write", file_path=file_path, content=content))

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        return EditResult(
            **self._call(
                "edit",
                file_path=file_path,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all,
            )
        )

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        return GrepResult(**self._call("grep", pattern=pattern, path=path, glob=glob))

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return GlobResult(**self._call("glob", pattern=pattern, path=path))

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [
            FileUploadResponse(**item)
            for item in self._call(
                "upload",
                files=[
                    (path, base64.b64encode(content).decode("ascii")) for path, content in files
                ],
            )
        ]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            FileDownloadResponse(
                path=item["path"],
                error=item["error"],
                content=base64.b64decode(item["content"]) if item["content"] is not None else None,
            )
            for item in self._call("download", paths=paths)
        ]
