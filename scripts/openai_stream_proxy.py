#!/usr/bin/env python3
"""Expose buffered Chat Completions as SSE for clients that require streaming.

    python3 scripts/openai_stream_proxy.py --upstream http://127.0.0.1:9000/v1

Point the client's base URL at http://127.0.0.1:9010/v1. The upstream receives
stream=false; its complete, already-parsed response is encoded as SSE. This
does not provide incremental generation or change prompts, tools, or sampling.
Only Chat Completions and model discovery are supported. No provider calls are
made at startup, and requests are never retried by this proxy.
"""

from __future__ import annotations

import argparse
import json
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
}


def completion_to_sse(completion: dict[str, Any], *, include_usage: bool) -> bytes:
    """Preserve completed choices, tool IDs/arguments, finish reasons, and usage."""
    if not isinstance(completion, dict) or not completion.get("choices"):
        raise ValueError("upstream did not return Chat Completions choices")
    base = {k: v for k, v in completion.items() if k not in {"choices", "usage", "object"}}
    base["object"] = "chat.completion.chunk"
    chunks = []
    for choice in completion["choices"]:
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("upstream choice has no assistant message")
        if not choice.get("finish_reason"):
            raise ValueError("upstream choice has no finish_reason")
        delta = {k: v for k, v in message.items() if k != "tool_calls"}
        index = choice["index"]
        chunks.append({**base, "choices": [{"index": index, "delta": delta,
                                           "finish_reason": None, "logprobs": choice.get("logprobs")}]})
        for tool_index, call in enumerate(message.get("tool_calls") or []):
            chunks.append({**base, "choices": [{
                "index": index, "delta": {"tool_calls": [{**call, "index": tool_index}]},
                "finish_reason": None,
            }]})
        chunks.append({**base, "choices": [{"index": index, "delta": {},
                                           "finish_reason": choice["finish_reason"]}]})
    if include_usage and completion.get("usage") is not None:
        chunks.append({**base, "choices": [], "usage": completion["usage"]})
    return b"".join(
        b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n"
        for chunk in chunks
    ) + b"data: [DONE]\n\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: Any) -> None:
        pass  # Do not log request bodies or credentials.

    def _send(self, status: int, body: bytes, headers: Any = None) -> None:
        self.send_response(status)
        connection_headers = {h.strip().lower() for h in (headers or {}).get("Connection", "").split(",")}
        for name, value in (headers or {}).items():
            if name.lower() not in _HOP_HEADERS | connection_headers:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, json.dumps({"error": {"message": message,
                                                "type": "stream_proxy_error"}}).encode(),
                   {"Content-Type": "application/json"})

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/v1/models":
            self._error(404, "only /v1/models and /v1/chat/completions are supported")
            return
        self._forward(None, False, False)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._error(404, "only /v1/chat/completions is supported for POST")
            return
        if self.headers.get("Transfer-Encoding"):
            self._error(400, "send a JSON request with Content-Length")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("empty request")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("request must be an object")
            want_stream = body.get("stream") is True
            options = body.get("stream_options") or {}
            include_usage = options.get("include_usage") is True
            body = {**body, "stream": False}
            body.pop("stream_options", None)
            payload = json.dumps(body, ensure_ascii=False).encode()
        except (ValueError, TypeError, AttributeError):
            self._error(400, "invalid Chat Completions JSON request")
            return
        self._forward(payload, want_stream, include_usage)

    def _forward(self, payload: bytes | None, want_stream: bool, include_usage: bool) -> None:
        connection_headers = {h.strip().lower() for h in self.headers.get("Connection", "").split(",")}
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in _HOP_HEADERS | connection_headers}
        headers.update({"Content-Type": "application/json", "Accept": "application/json",
                        "Accept-Encoding": "identity"})
        request = Request(self.server.upstream + self.path.removeprefix("/v1"),
                          data=payload, headers=headers, method=self.command)
        try:
            with self.server.opener.open(request, timeout=self.server.timeout_seconds) as response:
                status, raw, response_headers = response.status, response.read(), response.headers
        except HTTPError as exc:
            # Preserve status, body, and Retry-After so the client owns retries.
            with exc:
                self._send(exc.code, exc.read(), exc.headers)
            return
        except (URLError, OSError):
            self._error(502, "upstream request failed or timed out")
            return
        if want_stream:
            try:
                raw = completion_to_sse(json.loads(raw), include_usage=include_usage)
            except (ValueError, TypeError, KeyError, AttributeError):
                self._error(502, "upstream returned an invalid or incomplete Chat Completions response")
                return
            response_headers = {"Content-Type": "text/event-stream; charset=utf-8",
                                "Cache-Control": "no-cache"}
        with suppress(BrokenPipeError, ConnectionResetError):
            self._send(status, raw, response_headers)


def make_server(upstream: str, *, port: int = 9010, timeout: float = 600) -> ThreadingHTTPServer:
    url = urlsplit(upstream)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.query or url.fragment):
        raise ValueError("upstream must be an HTTP(S) base URL without credentials, query, or fragment")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if url.hostname in {"127.0.0.1", "localhost", "::1"} and url.port == server.server_port:
        server.server_close()
        raise ValueError("upstream must not point back to this proxy")
    server.upstream = upstream.rstrip("/")
    server.timeout_seconds = timeout
    # A local endpoint must not be redirected through ambient HTTP_PROXY settings.
    server.opener = build_opener(ProxyHandler({}))
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, help="existing Chat Completions base URL, including /v1")
    parser.add_argument("--port", type=int, default=9010)
    parser.add_argument("--timeout", type=float, default=600, help="upstream socket timeout in seconds")
    args = parser.parse_args()
    server = make_server(args.upstream, port=args.port, timeout=args.timeout)
    print(f"Buffered SSE proxy: http://127.0.0.1:{server.server_port}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
