"""Reasoning traces must survive a full request/response round trip.

`ReasoningAwareChatOpenAI` hooks two private `ChatOpenAI` methods. If upstream
renames them the hooks stop firing silently — no exception, just a reasoning
model scoring ~10 points lower because it no longer sees its own thoughts. These
tests drive a real HTTP round trip against a local stub so a broken hook fails
loudly instead.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from harness_bench.chat_openai import ReasoningAwareChatOpenAI

THOUGHT = "First list the directory, then answer."
TOOL_CALL = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "ls", "arguments": "{}"},
}


class _StubServer:
    """Minimal chat-completions endpoint recording every request body."""

    def __init__(self, reasoning_key: str | None) -> None:
        self.requests: list[dict] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
                length = int(self.headers["Content-Length"])
                recorder.requests.append(json.loads(self.rfile.read(length)))
                message: dict = {"role": "assistant", "content": None, "tool_calls": [TOOL_CALL]}
                if len(recorder.requests) > 1:
                    message = {"role": "assistant", "content": "done"}
                if reasoning_key is not None:
                    message[reasoning_key] = THOUGHT
                body = json.dumps(
                    {
                        "id": "chatcmpl-stub",
                        "object": "chat.completion",
                        "created": 0,
                        "model": "stub",
                        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                return

        self._server = HTTPServer(("127.0.0.1", 0), Handler)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def __enter__(self) -> _StubServer:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()


def _second_request(reasoning_key: str | None, *, forward: bool) -> tuple[AIMessage, dict]:
    """Run two turns of an agent loop; return the first reply and the second request."""
    with _StubServer(reasoning_key) as server:
        model = ReasoningAwareChatOpenAI(
            model="stub",
            base_url=server.base_url,
            api_key="stub-key",
            forward_reasoning_history=forward,
        )
        history: list = [SystemMessage("be an agent"), HumanMessage("count the files")]
        reply = model.invoke(history)
        history += [reply, ToolMessage(content="a.txt", tool_call_id="call_1")]
        model.invoke(history)
        return reply, server.requests[1]


def _assistant_turns(request: dict) -> Iterator[dict]:
    return (m for m in request["messages"] if m["role"] == "assistant")


@pytest.mark.parametrize("reasoning_key", ["reasoning_content", "reasoning"])
def test_reasoning_is_captured_onto_the_message(reasoning_key: str) -> None:
    """Both provider spellings land in `additional_kwargs`, flag or not."""
    reply, _ = _second_request(reasoning_key, forward=False)

    assert reply.additional_kwargs[reasoning_key] == THOUGHT


@pytest.mark.parametrize("reasoning_key", ["reasoning_content", "reasoning"])
def test_reasoning_is_replayed_under_its_own_key(reasoning_key: str) -> None:
    """A forwarded trace goes back on the assistant turn, under the key it arrived on."""
    _, request = _second_request(reasoning_key, forward=True)

    assistant = list(_assistant_turns(request))
    assert len(assistant) == 1
    assert assistant[0][reasoning_key] == THOUGHT
    # The trace must ride on the assistant turn only, never on user/tool turns.
    others = [m for m in request["messages"] if m["role"] != "assistant"]
    assert not any(key in m for m in others for key in ("reasoning", "reasoning_content"))


@pytest.mark.parametrize("reasoning_key", ["reasoning_content", "reasoning"])
def test_reasoning_is_withheld_when_forwarding_is_off(reasoning_key: str) -> None:
    """Default runs stay byte-identical to stock `ChatOpenAI` requests."""
    _, request = _second_request(reasoning_key, forward=False)

    assert THOUGHT not in json.dumps(request)


def test_models_without_reasoning_are_untouched() -> None:
    """Enabling the flag against a plain model must not invent fields."""
    _, request = _second_request(None, forward=True)

    for message in request["messages"]:
        assert "reasoning" not in message
        assert "reasoning_content" not in message
