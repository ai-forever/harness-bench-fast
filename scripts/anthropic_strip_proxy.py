#!/usr/bin/env python3
"""Thin Anthropic Messages pass-through that strips params gpt2giga rejects.

free-code / Claude Code 2.x sends request fields that the gpt2giga Anthropic
endpoint hard-rejects with HTTP 400 (e.g. `context_management` -> "Stateful
context management is not supported."). This shim sits between the CLI and
gpt2giga: it deletes the offending top-level keys from the JSON body and relays
the (possibly streaming) response verbatim. Everything else is untouched.

    free-code (ANTHROPIC_BASE_URL) -> this shim -> gpt2giga -> GigaChat

Run:
    uv run --no-sync python scripts/anthropic_strip_proxy.py \
        --port 8092 --upstream http://127.0.0.1:8090
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

UPSTREAM = "http://127.0.0.1:8090"
# Top-level Anthropic request keys gpt2giga refuses (protocol/anthropic/params.py
# ANTHROPIC_REJECTED_PARAMS). Drop them so the request goes through.
STRIP_KEYS = ("context_management", "container", "mcp_servers")
# Message content block types gpt2giga refuses in request content
# (ANTHROPIC_UNSUPPORTED_CONTENT_BLOCK_MESSAGES). GigaChat has reasoning off, so
# dropping replayed thinking blocks is lossless for this bench.
STRIP_BLOCK_TYPES = ("thinking", "redacted_thinking")


def _strip_message_blocks(body: dict) -> int:
    """Remove unsupported content blocks (e.g. thinking) from messages. Returns count."""
    removed = 0
    for msg in body.get("messages") or []:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        kept = []
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") in STRIP_BLOCK_TYPES:
                removed += 1
                continue
            kept.append(blk)
        # Never leave an assistant/user message with empty content — insert a
        # placeholder text block so the transcript stays well-formed.
        if not kept:
            kept = [{"type": "text", "text": ""}]
        msg["content"] = kept
    return removed
DEBUG = False
_client: httpx.Client | None = None


def _log(*args: Any) -> None:
    if DEBUG:
        print("[strip]", *args, file=sys.stderr, flush=True)


def _forward_headers(headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in ("host", "content-length", "connection", "accept-encoding"):
            continue
        out[k] = v
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        if DEBUG:
            super().log_message(*args)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 — relay GETs (e.g. /v1/models, health)
        assert _client is not None
        try:
            r = _client.get(self.path, headers=_forward_headers(self.headers))
        except Exception as exc:  # noqa: BLE001
            self._error(502, str(exc))
            return
        self._relay_simple(r)

    def do_POST(self) -> None:  # noqa: N802
        raw = self._read_body()
        stripped = 0
        try:
            body = json.loads(raw) if raw else {}
            if isinstance(body, dict):
                for k in STRIP_KEYS:
                    if k in body:
                        del body[k]
                        stripped += 1
                stripped += _strip_message_blocks(body)
            raw = json.dumps(body, ensure_ascii=False).encode()
        except (ValueError, TypeError):
            pass  # not JSON — forward untouched
        if stripped:
            _log(f"stripped {stripped} key(s) from {self.path}")

        headers = _forward_headers(self.headers)
        headers["Content-Type"] = "application/json"
        want_stream = False
        with contextlib.suppress(ValueError, TypeError):
            want_stream = bool(json.loads(raw).get("stream"))

        assert _client is not None
        try:
            if want_stream:
                self._relay_stream(headers, raw)
            else:
                r = _client.post(self.path, content=raw, headers=headers)
                self._relay_simple(r)
        except Exception as exc:  # noqa: BLE001
            self._error(502, str(exc))

    def _relay_simple(self, r: httpx.Response) -> None:
        payload = r.content
        self.send_response(r.status_code)
        ctype = r.headers.get("content-type", "application/json")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _relay_stream(self, headers: dict[str, str], raw: bytes) -> None:
        assert _client is not None
        with _client.stream("POST", self.path, content=raw, headers=headers) as r:
            self.send_response(r.status_code)
            self.send_header(
                "Content-Type", r.headers.get("content-type", "text/event-stream")
            )
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for chunk in r.iter_raw():
                if not chunk:
                    continue
                self.wfile.write(f"{len(chunk):X}\r\n".encode())
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    def _error(self, status: int, message: str) -> None:
        _log("error", status, message)
        payload = json.dumps(
            {"type": "error", "error": {"type": "api_error", "message": message}}
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> int:
    global UPSTREAM, DEBUG, _client
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8092)
    ap.add_argument("--upstream", default="http://127.0.0.1:8090")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    UPSTREAM = args.upstream.rstrip("/")
    DEBUG = args.debug
    _client = httpx.Client(base_url=UPSTREAM, timeout=httpx.Timeout(600.0, connect=15.0))

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(
        f"[strip] Anthropic strip-proxy on http://127.0.0.1:{args.port} -> {UPSTREAM} "
        f"(strips {', '.join(STRIP_KEYS)})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[strip] shutting down", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
