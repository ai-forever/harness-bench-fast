#!/usr/bin/env python3
"""Bounded HBF → real Strands SDK smoke; offline unless --live is explicit."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
import shutil
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness_bench import runner_cli  # noqa: E402
from harness_bench.runner import results_to_payload  # noqa: E402
from harness_bench.tasks import get_task  # noqa: E402

TASKS = ("task_01_create_hello", "task_05_greet", "task_35_remove_blank_lines")


def write_json(path: Path, value: object, api_key: str = "") -> None:
    def redact(item):
        if isinstance(item, str):
            return item.replace(api_key, "[REDACTED]") if api_key else item
        if isinstance(item, dict):
            return {redact(key): redact(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [redact(val) for val in item]
        return item

    text = json.dumps(redact(value), indent=2, default=str)
    path.write_text(text + "\n", encoding="utf-8")


def configuration(live: bool) -> dict[str, str]:
    bundled_runner = ROOT / "harness_bench" / "strands" / "runner.mjs"
    runner = Path(os.environ.get("STRANDS_RUNNER", str(bundled_runner)))
    if not runner.is_absolute() or not runner.is_file():
        raise ValueError("STRANDS_RUNNER must name an existing absolute runner.mjs path")
    node = shutil.which(os.environ.get("STRANDS_NODE") or "node")
    if not node:
        raise ValueError("Node not found; set STRANDS_NODE or add Node >=22 to PATH")
    env = {"STRANDS_RUNNER": str(runner), "STRANDS_NODE": str(Path(node).resolve()),
           "GIGACHAT_TOKEN_URL": ""}
    if live:
        for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "STRANDS_MODEL_ID"):
            if not os.environ.get(key):
                raise ValueError(f"--live requires {key}")
            env[key] = os.environ[key]
        defaults = {"STRANDS_MAX_STEPS": "16", "STRANDS_TIMEOUT_SECONDS": "240",
                    "STRANDS_REQUEST_TIMEOUT_SECONDS": "90",
                    "STRANDS_MODEL_PARAMS_JSON": '{"temperature":0,"max_tokens":4096}'}
        env.update({key: os.environ.get(key, value) for key, value in defaults.items()})
    else:
        env.update(OPENAI_API_KEY="fixture-key", STRANDS_MODEL_ID="fixture-strands",
                   STRANDS_MAX_STEPS="4", STRANDS_TIMEOUT_SECONDS="20",
                   STRANDS_REQUEST_TIMEOUT_SECONDS="5",
                   STRANDS_MODEL_PARAMS_JSON='{"temperature":0,"max_tokens":512}')
    params = json.loads(env["STRANDS_MODEL_PARAMS_JSON"])
    if not isinstance(params, dict):
        raise ValueError("STRANDS_MODEL_PARAMS_JSON must be a JSON object")
    if "chat_template_kwargs" in params and not isinstance(params["chat_template_kwargs"], dict):
        raise ValueError("chat_template_kwargs must be a JSON object")
    steps = int(env["STRANDS_MAX_STEPS"])
    if steps < 1:
        raise ValueError("STRANDS_MAX_STEPS must be positive")
    for key in ("STRANDS_TIMEOUT_SECONDS", "STRANDS_REQUEST_TIMEOUT_SECONDS"):
        number = float(env[key])
        if not math.isfinite(number) or not 0 < number <= 2_147_483:
            raise ValueError(f"{key} must be finite and between 0 and 2147483")
    return env


def probe_models(env: dict[str, str]) -> dict:
    request = Request(env["OPENAI_BASE_URL"].rstrip("/") + "/models",
                      headers={"Authorization": "Bearer " + env["OPENAI_API_KEY"]})
    with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
        models = json.load(response)
    if env["STRANDS_MODEL_ID"] not in [item.get("id") for item in models.get("data", [])]:
        raise ValueError("STRANDS_MODEL_ID is absent from the endpoint /models response")
    return models


@contextmanager
def fixture_server():
    requests: list[dict] = []
    errors: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body)
                index = len(requests)
                if index == 2:
                    self.send_response(429)
                    self.end_headers()
                    self.wfile.write(b'{"error":"fixture overload"}')
                    return
                message = {"role": "assistant", "content": "Completed."}
                finish = "stop"
                if index in (1, 3):
                    name, arguments = "shell", {
                        "command": "pwd && test ! -e .strands-smoke-attempt && touch .strands-smoke-attempt"}
                    reason, call_id = "Find the fresh task directory.", f"fixture-pwd-{index}"
                elif index == 4:
                    tool_result = json.loads(body["messages"][-1]["content"])
                    task_dir = Path(tool_result["output"].strip())
                    if not task_dir.is_absolute():
                        raise ValueError("fixture shell did not return an absolute task directory")
                    name, arguments = "write", {"path": str(task_dir / "hello.py"),
                                                 "content": 'print("Hello, world!")\n'}
                    reason, call_id = "Use the write tool.", "fixture-write"
                elif index != 5:
                    raise ValueError("unexpected fixture request count")
                if index in (1, 3, 4):
                    message.update(content=None, reasoning_content=reason, tool_calls=[{
                        "id": call_id, "type": "function", "function": {
                            "name": name, "arguments": json.dumps(arguments)}}])
                    finish = "tool_calls"
                payload = {"model": "fixture-strands-build-1", "choices": [{
                    "index": 0, "message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())
            except Exception as error:
                errors.append(type(error).__name__)
                self.send_error(500, "fixture protocol failure")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests, errors
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def validate_fixture(result, requests: list[dict], errors: list[str]) -> None:
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(not errors, "fixture server error")
    require(result.passed, "fixture task did not pass")
    require(len(requests) == 5, "fixture must make five provider requests including 429")
    history = result.execution_history or []
    require(len(history) == 2, "429 must create two physical attempts")
    require(history[0]["failure_kind"] == "infrastructure", "429 must be infrastructure")
    require(history[0]["execution"] != history[1]["execution"], "retry must have fresh evidence")
    first = json.loads(requests[1]["messages"][-1]["content"])
    second = json.loads(requests[3]["messages"][-1]["content"])
    require(first["output"].strip() != second["output"].strip(), "retry must use a fresh workspace")
    require(not any(m["role"] == "tool" for m in requests[2]["messages"]), "retry must reset history")
    require(result.agent_steps == result.agent_llm_calls == 3, "scored attempt must have three model calls")
    require(result.agent_tool_calls == 2 and result.agent_total_tokens == 360, "fixture usage mismatch")
    require(history[0]["strands"]["stats"]["agent_total_tokens"] == 120, "retain failed attempt usage")
    require(history[1]["strands"]["response_models"] == ["fixture-strands-build-1"], "missing model build")
    require(requests[-1]["messages"][-2]["reasoning_content"] == "Use the write tool.", "reasoning history lost")
    require(requests[-1]["messages"][-1]["tool_call_id"] == "fixture-write", "tool history lost")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="explicitly enable three sequential provider tasks")
    parser.add_argument("--output-dir", type=Path, help="new private evidence directory (must not exist)")
    args = parser.parse_args(argv)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output_dir or ROOT / "jobs" / f"strands-smoke-{stamp}-{uuid4().hex[:8]}").resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    print(f"Private smoke evidence: {output}", flush=True)
    env: dict[str, str] = {}
    results = []
    manifest = {"started_at": stamp, "mode": "live" if args.live else "offline-fixture",
                "scope": "three-task diagnostic" if args.live else "real SDK loopback fixture",
                "full_benchmark": False, "isolation": "none", "status": "running"}
    write_json(output / "manifest.json", manifest)
    try:
        env = configuration(args.live)
        outer_timeout = max(270, math.ceil(float(env["STRANDS_TIMEOUT_SECONDS"]) + 30)) if args.live else 30
        params = json.loads(env["STRANDS_MODEL_PARAMS_JSON"])
        paths = [Path(env["STRANDS_RUNNER"]), Path(env["STRANDS_RUNNER"]).with_name("package-lock.json"),
                 ROOT / "scripts/hb-strands", ROOT / "harness_bench/runner_cli.py", Path(__file__)]
        manifest.update(configuration={key: value for key, value in env.items() if key != "OPENAI_API_KEY"},
                        model_params=params, reasoning_effort=params.get("reasoning_effort", "default"),
                        reasoning_enabled=params.get("chat_template_kwargs", {}).get("reasoning"),
                        task_ids=list(TASKS if args.live else TASKS[:1]), timeout_seconds=outer_timeout,
                        transient_retries=1,
                        hashes={str(path): hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                                for path in paths})
        write_json(output / "manifest.json", manifest, env["OPENAI_API_KEY"])

        def run(task_id):
            result = runner_cli.run_task_cli(get_task(task_id), cli_command=shlex.quote(str(ROOT / "scripts/hb-strands")),
                timeout=outer_timeout, transient_retries=1, isolation="none",
                artifacts_root=output / "artifacts", extra_env=env)
            results.append(result)
            write_json(output / "results.json", results_to_payload(results), env["OPENAI_API_KEY"])
            return result

        if args.live:
            manifest["models_probe"] = probe_models(env)
            write_json(output / "manifest.json", manifest, env["OPENAI_API_KEY"])
            for task_id in TASKS:
                run(task_id)
        else:
            with fixture_server() as (url, requests, errors):
                env["OPENAI_BASE_URL"] = url
                manifest["configuration"]["OPENAI_BASE_URL"] = url
                original_backoff = runner_cli._BACKOFF_SCHEDULE
                try:
                    runner_cli._BACKOFF_SCHEDULE = (0,)
                    result = run(TASKS[0])
                finally:
                    runner_cli._BACKOFF_SCHEDULE = original_backoff
                    write_json(output / "fixture-requests.json", requests, env["OPENAI_API_KEY"])
                validate_fixture(result, requests, errors)
                manifest.update(fixture_requests=len(requests), live_model_calls=0, fixture_checks="passed")
        payload = results_to_payload(results)
        success = payload["measurement_valid"] and all(result.passed for result in results)
        manifest.update(status="passed" if success else "failed", measurement_valid=payload["measurement_valid"])
        return 0 if success else 1
    except Exception as error:
        # Exception strings can contain endpoint URLs, credentials or provider bodies.
        manifest.update(status="error", error_type=type(error).__name__)
        print(f"Smoke failed ({type(error).__name__}); inspect private evidence.", file=sys.stderr)
        return 1
    finally:
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        write_json(output / "manifest.json", manifest, env.get("OPENAI_API_KEY", ""))
        print(f"Smoke status: {manifest['status']}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
