"""Buffered SSE compatibility, using a loopback JSON-only endpoint."""

import copy
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from scripts.openai_stream_proxy import completion_to_sse, make_server


def completion():
    return {
        "id": "chatcmpl-original", "object": "chat.completion", "created": 123,
        "model": "fixture:exact-build", "system_fingerprint": "original-fingerprint",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Готово",
                     "reasoning_content": "original reasoning"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18,
                  "prompt_tokens_details": {"cached_tokens": 4}},
    }


def sse_chunks(raw):
    lines = raw.decode().splitlines()
    assert lines[-2:] == ["data: [DONE]", ""]
    return [json.loads(line[6:]) for line in lines if line.startswith("data: {")]


def test_sse_preserves_content_model_finish_reason_tools_and_usage():
    response = completion()
    calls = [{"id": f"call_{i}", "type": "function",
              "function": {"name": "write", "arguments": '{"content": "тест\\n"}'}}
             for i in range(2)]
    response["choices"][0]["message"]["tool_calls"] = calls
    response["choices"][0]["finish_reason"] = "length"
    original = copy.deepcopy(response)
    chunks = sse_chunks(completion_to_sse(response, include_usage=True))
    assert response == original
    assert all(c["object"] == "chat.completion.chunk" and c["model"] == response["model"]
               and c["id"] == response["id"] for c in chunks)
    assert chunks[0]["choices"][0]["delta"] == {
        "role": "assistant", "content": "Готово", "reasoning_content": "original reasoning",
    }
    assert [c["choices"][0]["delta"]["tool_calls"][0] for c in chunks[1:3]] == [
        {**call, "index": i} for i, call in enumerate(calls)
    ]
    assert chunks[-2]["choices"][0]["finish_reason"] == "length"
    assert chunks[-1]["choices"] == [] and chunks[-1]["usage"] == response["usage"]


def test_usage_is_optional_and_missing_usage_is_not_invented():
    response = completion()
    assert all("usage" not in c for c in sse_chunks(completion_to_sse(response, include_usage=False)))
    del response["usage"]
    assert all("usage" not in c for c in sse_chunks(completion_to_sse(response, include_usage=True)))


@pytest.mark.parametrize("response", [{}, {"choices": []}, {"choices": [{
    "index": 0, "message": {"role": "assistant", "content": "unfinished"}, "finish_reason": None,
}]}])
def test_incomplete_responses_are_rejected(response):
    with pytest.raises(ValueError):
        completion_to_sse(response, include_usage=True)


@contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_relay_changes_only_stream_settings_and_preserves_errors():
    seen = []
    reply = completion()

    class JsonOnlyEndpoint(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):  # noqa: N802
            assert self.path == "/v1/models"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"object":"list","data":[]}')

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            assert self.path == "/v1/chat/completions"
            assert self.headers["Authorization"] == "Bearer fixture-key"
            status = body.get("fixture_status", 200)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "2")
            self.end_headers()
            payload = reply if status == 200 else {"error": {"message": "fixture overload"}}
            self.wfile.write(json.dumps(payload).encode())

    opener = build_opener(ProxyHandler({}))
    with (
        running(ThreadingHTTPServer(("127.0.0.1", 0), JsonOnlyEndpoint)) as upstream,
        running(make_server(upstream, port=0)) as base,
    ):
        def post(body):
            request = Request(base + "/chat/completions", json.dumps(body).encode(),
                              headers={"Authorization": "Bearer fixture-key", "Content-Type": "application/json"})
            return opener.open(request, timeout=5)

        body = {"model": "fixture-model", "messages": [{"role": "user", "content": "Привет"}],
                "tools": [{"type": "function", "function": {"name": "write"}}],
                "temperature": 0.7, "max_tokens": 123, "stream": True,
                "stream_options": {"include_usage": True}, "extra_parameter": "unchanged"}
        with post(body) as response:
            assert response.headers.get_content_type() == "text/event-stream"
            assert sse_chunks(response.read())[-1]["usage"] == reply["usage"]
        assert seen == [{k: v for k, v in {**body, "stream": False}.items() if k != "stream_options"}]
        with post({**body, "stream": False}) as response:
            assert response.headers.get_content_type() == "application/json"
            assert json.loads(response.read()) == reply
        with pytest.raises(HTTPError) as error:
            post({**body, "fixture_status": 503})
        assert error.value.code == 503 and error.value.headers["Retry-After"] == "2"
        assert json.loads(error.value.read())["error"]["message"] == "fixture overload"
        reply["choices"][0]["finish_reason"] = None
        with pytest.raises(HTTPError) as error:
            post(body)
        assert error.value.code == 502
        with opener.open(base + "/models", timeout=5) as response:
            assert json.loads(response.read())["data"] == []
        with pytest.raises(HTTPError) as error:
            opener.open(Request(base + "/responses", b'{}'), timeout=5)
        assert error.value.code == 404


def test_invalid_upstream_is_rejected_before_binding():
    with pytest.raises(ValueError, match="without credentials, query, or fragment"):
        make_server("http://localhost:9000/v1?not-a-base-url", port=0)
