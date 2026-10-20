#!/usr/bin/env python3
"""Wire audit and opt-in lossless replay for tool-call reasoning. Loopback only.

Routes /audit/NAME/v1/... observe unchanged payloads; /repair/NAME/v1/...
restore reasoning only for an exact tool_call_id and tool name+arguments match.
Persistent SQLite holds reasoning, bounded per request; no in-memory histories.
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(os.environ.get("HBF_AUDIT_DIR", "/tmp/hbf-reasoning-audit"))
ROOT.mkdir(parents=True, exist_ok=True)
DB = ROOT / "reasoning.sqlite"
LOCK = threading.Lock()


def digest(s):
    return hashlib.sha256(s.encode()).hexdigest()


@contextmanager
def connect():
    c = sqlite3.connect(DB, timeout=60)
    c.execute(
        "CREATE TABLE IF NOT EXISTS calls (harness TEXT, call_id TEXT, signature TEXT, reasoning TEXT, PRIMARY KEY(harness,call_id))"
    )
    try:
        with c:
            yield c
    finally:
        c.close()


with connect() as c:
    c.execute("PRAGMA journal_mode=WAL")


def call_signature(call):
    f = call.get("function", {})
    args = f.get("arguments", "")
    with suppress(ValueError):
        args = json.loads(args) if isinstance(args, str) else args
    return digest(json.dumps([f.get("name"), args], sort_keys=True, ensure_ascii=False))


def prepare(body, harness, repair):
    checks = []
    with connect() as c:
        for msg in body.get("messages", []):
            if msg.get("role") != "assistant":
                continue
            for call in msg.get("tool_calls") or []:
                row = c.execute(
                    "SELECT signature,reasoning FROM calls WHERE harness=? AND call_id=?",
                    (harness, call.get("id")),
                ).fetchone()
                if not row and harness.startswith("openclaw"):
                    candidates = c.execute(
                        "SELECT signature,reasoning FROM calls WHERE harness=? AND replace(call_id,'_','')=?",
                        (harness, call.get("id", "").replace("_", "")),
                    ).fetchall()
                    if len(candidates) == 1:
                        row = candidates[0]
                if not row or row[0] != call_signature(call):
                    continue
                reasoning = row[1]
                supplied = msg.get("reasoning_content") or msg.get("reasoning") or ""
                text = msg.get("content") or ""
                as_text = isinstance(text, str) and reasoning in text
                native = supplied == reasoning
                restored = False
                if repair and not native and not as_text:
                    if supplied:
                        raise ValueError(
                            "Conflicting reasoning for exact tool call; refusing overwrite"
                        )
                    msg["reasoning_content"] = reasoning
                    restored = True
                checks.append(
                    {
                        "call_id": call["id"],
                        "sha256": digest(reasoning),
                        "chars": len(reasoning),
                        "native_exact": native,
                        "as_text_exact": as_text,
                        "restored": restored,
                        "sent_exact": native or restored or as_text,
                    }
                )
    return checks


def record(harness, event):
    event["time"] = time.time()
    with LOCK, (ROOT / (harness + ".jsonl")).open("a") as f:
        f.write(json.dumps(event) + "\n")


def observe(harness, msg):
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    calls = msg.get("tool_calls") or []
    if reasoning:
        with connect() as c:
            for call in calls:
                if call.get("id"):
                    c.execute(
                        "INSERT OR REPLACE INTO calls VALUES (?,?,?,?)",
                        (harness, call["id"], call_signature(call), reasoning),
                    )
    record(
        harness,
        {
            "event": "response",
            "reasoning_chars": len(reasoning),
            "reasoning_sha256": digest(reasoning) if reasoning else None,
            "tool_calls": len(calls),
            "calls": [{"id": c.get("id"), "signature": call_signature(c)} for c in calls],
        },
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def error(self, status, msg):
        data = json.dumps({"error": {"message": msg}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.error(200, "ok")

    def do_POST(self):
        try:
            self.run_request()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(type(e).__name__, str(e)[:200], flush=True)
            if not getattr(self, "started", False):
                self.error(502, str(e)[:200])

    def run_request(self):
        parts = self.path.split("/")
        if (
            len(parts) < 5
            or parts[1] not in ["audit", "repair"]
            or not parts[2].replace("-", "").isalnum()
        ):
            return self.error(404, "bad audit route")
        mode, harness = parts[1:3]
        path = "/".join(parts[3:]).split("?")[0]
        if path != "v1/chat/completions":
            return self.error(404, "audit proxy supports chat completions only")
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0 or n > 32 * 1024 * 1024:
            return self.error(413, "size limit")
        body = json.loads(self.rfile.read(n))
        checks = prepare(body, harness, mode == "repair")
        record(
            harness,
            {
                "event": "request",
                "mode": mode,
                "checks": checks,
                "reasoning": [
                    {
                        "sha256": digest(m.get("reasoning_content") or m.get("reasoning")),
                        "chars": len(m.get("reasoning_content") or m.get("reasoning")),
                    }
                    for m in body.get("messages", [])
                    if m.get("reasoning_content") or m.get("reasoning")
                ],
                "assistant_messages": sum(
                    m.get("role") == "assistant" for m in body.get("messages", [])
                ),
                "calls": [
                    {"id": c.get("id"), "signature": call_signature(c)}
                    for m in body.get("messages", [])
                    for c in (m.get("tool_calls") or [])
                ],
            },
        )
        headers = {"Authorization": "Bearer " + os.environ["HBF_API_KEY"]}
        with (
            httpx.Client(timeout=600, trust_env=False) as client,
            client.stream(
                "POST", "http://127.0.0.1:19033/v1/chat/completions", json=body, headers=headers
            ) as r,
        ):
            if not body.get("stream") or r.status_code != 200:
                raw = r.read()
                if r.status_code == 200:
                    observe(harness, json.loads(raw)["choices"][0]["message"])
                self.send_response(r.status_code)
                self.send_header("Content-Type", r.headers.get("content-type", "application/json"))
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.started = True
                self.wfile.write(raw)
                return
            self.send_response(r.status_code)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.started = True
            pending = b""
            reason = []
            calls = {}
            for chunk in r.iter_bytes():
                pending += chunk
                finished = False
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if line.startswith(b"data: ") and line[6:].strip() != b"[DONE]":
                        event = json.loads(line[6:])
                        choices = event.get("choices", [])
                        if choices:
                            finished = finished or choices[0].get("finish_reason") is not None
                            d = choices[0].get("delta", {})
                            reason.append(d.get("reasoning_content") or d.get("reasoning") or "")
                            for tc in d.get("tool_calls") or []:
                                call = calls.setdefault(
                                    tc.get("index", 0),
                                    {
                                        "id": "",
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    },
                                )
                                if tc.get("id"):
                                    call["id"] = tc["id"]
                                for key in ["name", "arguments"]:
                                    if tc.get("function", {}).get(key):
                                        call["function"][key] += tc["function"][key]
                # Save before emitting the final chunk so a fast client sees history ready.
                if (finished or b"[DONE]" in chunk) and (calls or any(reason)):
                    observe(
                        harness,
                        {
                            "reasoning_content": "".join(reason),
                            "tool_calls": list(calls.values()),
                        },
                    )
                    calls = {}
                    reason = []
                self.wfile.write(chunk)
                self.wfile.flush()
            if calls or any(reason):
                observe(
                    harness,
                    {"reasoning_content": "".join(reason), "tool_calls": list(calls.values())},
                )


if __name__ == "__main__":
    s = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("HBF_AUDIT_PORT", "19035"))), Handler)
    s.daemon_threads = True
    s.serve_forever()
