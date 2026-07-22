#!/usr/bin/env python3
"""Anthropic Messages API -> GigaChat /chat translation proxy.

Lets a stock `free-code` / Claude Code CLI (which speaks the Anthropic Messages
API and honors ANTHROPIC_BASE_URL) drive a GigaChat wmcore `/chat` stand. This
is the adapter for benchmarking the free-code-imitating LoRA via `run-cli`
without forking free-code.

Run:
    uv run --no-sync python scripts/freecode_gigachat_proxy.py \
        --port 8087 --upstream http://127.0.0.1:8083 --chat-path /chat

Then point free-code at it:
    ANTHROPIC_BASE_URL=http://127.0.0.1:8087 ANTHROPIC_API_KEY=dummy \
    CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 \
    free-code -p --model sonnet --dangerously-skip-permissions "<prompt>"

Offline translation self-test (no live stand needed):
    uv run --no-sync python scripts/freecode_gigachat_proxy.py --selftest

Design notes live in memory: freecode_gigachat_bridge.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

# --------------------------------------------------------------------------
# Configuration (set in main()).
# --------------------------------------------------------------------------
UPSTREAM = "http://127.0.0.1:8083"
CHAT_PATH = "/chat"
GIGA_MODEL = "GigaChat"
DEBUG = False
_client: httpx.Client | None = None

# A large function block (free-code ships ~40 tools incl. huge MCP/Cron/Worktree
# schemas, ~19.5k suggester tokens) suppresses tool-calling on the stand — the
# model degrades to a text preamble. Pruning to the relevant agent tools (≤~17,
# matching what the model was trained on) restores reliable function_call.
# Drop tool families that are irrelevant to file-ops tasks and merely bloat the
# block: third-party MCP servers, remote/cron/worktree orchestration.
_DROP_PREFIXES = ("mcp__",)
_DROP_EXACT = {
    "CronCreate", "CronDelete", "CronList",
    "EnterWorktree", "ExitWorktree",
    "EnterPlanMode", "ExitPlanMode",
    "AskUserQuestion",
}


def _tool_allowed(name: str) -> bool:
    if any(name.startswith(p) for p in _DROP_PREFIXES):
        return False
    return name not in _DROP_EXACT


def _log(*args: Any) -> None:
    if DEBUG:
        print("[proxy]", *args, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Inbound translation: Anthropic request -> GigaChat /chat body.
# --------------------------------------------------------------------------
def _system_to_text(system: Any) -> str:
    """Anthropic `system` (str | list of blocks) -> plain text."""
    if not system:
        return ""
    if isinstance(system, str):
        return system
    parts = []
    for blk in system:
        if isinstance(blk, dict) and blk.get("type") == "text":
            parts.append(blk.get("text", ""))
        elif isinstance(blk, str):
            parts.append(blk)
    return "\n\n".join(p for p in parts if p)


def _sanitize_schema(node: Any) -> Any:
    """Make a JSON Schema acceptable to the stand's xgrammar validator.

    The stand REQUIRES every `{"type":"object"}` node to carry a `properties`
    key (empty `{}` is accepted) and rejects unknown meta keys. free-code ships
    open-ended object fields (e.g. AskUserQuestion's `answers`) without
    `properties`, which 400s. Walk the schema recursively and: drop `$schema` /
    `additionalProperties`; ensure each object node has `properties`; recurse
    into properties / items / combinators / $defs.
    """
    if isinstance(node, list):
        return [_sanitize_schema(x) for x in node]
    if not isinstance(node, dict):
        return node

    # The stand supports only a basic JSON-Schema subset: it requires a concrete
    # `type` and does not understand anyOf/oneOf/allOf or `const`. Collapse any
    # combinator into a single representative subschema (common branch type, or
    # string), unioning any enum/const values into an `enum`.
    for comb in ("anyOf", "oneOf", "allOf"):
        branches = node.get(comb)
        if isinstance(branches, list) and branches:
            subs = [_sanitize_schema(b) for b in branches if isinstance(b, dict)]
            types = {b.get("type") for b in subs if b.get("type")}
            enums: list[Any] = []
            for b in subs:
                enums.extend(b.get("enum", []))
            merged = {k: v for k, v in node.items() if k not in ("anyOf", "oneOf", "allOf")}
            merged["type"] = types.pop() if len(types) == 1 else "string"
            if enums:
                merged["enum"] = enums
            return _sanitize_schema(merged)

    out: dict[str, Any] = {}
    for k, v in node.items():
        if k in ("$schema", "additionalProperties"):
            continue
        if k == "format":
            # The stand only accepts these string formats; drop any other (e.g. "uri").
            if v in ("date", "date-time", "time"):
                out[k] = v
            continue
        if k == "const":
            out["enum"] = [v]
        elif k in ("properties", "$defs", "definitions") and isinstance(v, dict):
            out[k] = {pk: _sanitize_schema(pv) for pk, pv in v.items()}
        elif k in ("items", "additionalItems", "not"):
            out[k] = _sanitize_schema(v)
        elif k == "prefixItems":
            out[k] = [_sanitize_schema(x) for x in v] if isinstance(v, list) else v
        else:
            out[k] = v
    # The stand wants a concrete scalar/array/object type on every node.
    if "type" not in out and "enum" not in out:
        out["type"] = "string"
    if isinstance(out.get("type"), list):
        out["type"] = next((t for t in out["type"] if t != "null"), "string")
    # A `type` value must be a scalar string; some tools ship a nested object
    # there (rejected by the stand as "...type.type is wrong"). Coerce to string.
    if "type" in out and not isinstance(out["type"], str):
        out["type"] = "string"
    if out.get("type") == "object" and "properties" not in out:
        out["properties"] = {}
    return out


def _tools_to_functions(tools: list[dict] | None) -> tuple[list[dict], list[str]]:
    """Anthropic tools -> GigaChat top-level function schemas + name list."""
    funcs: list[dict] = []
    names: list[str] = []
    for t in tools or []:
        name = t.get("name")
        if not name:
            continue
        if not _tool_allowed(name):
            continue
        schema = _sanitize_schema(dict(t.get("input_schema") or {}))
        if "type" not in schema:
            schema["type"] = "object"
        if schema.get("type") == "object" and "properties" not in schema:
            schema["properties"] = {}
        funcs.append(
            {
                "name": name,
                "description": t.get("description", "") or "",
                "parameters": schema,
            }
        )
        names.append(name)
    return funcs, names


def _stringify_tool_result(content: Any) -> str:
    """Flatten an Anthropic tool_result `content` to a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for blk in content:
        if isinstance(blk, str):
            parts.append(blk)
        elif isinstance(blk, dict):
            if blk.get("type") == "text":
                parts.append(blk.get("text", ""))
            elif blk.get("type") == "image":
                parts.append("[image]")
            else:
                parts.append(json.dumps(blk, ensure_ascii=False))
    return "\n".join(parts)


def _as_json_content(text: str) -> str:
    """The stand requires function-result content to be valid JSON.

    Pass through if already JSON-parseable, else wrap as a JSON object (the
    training data used objects like {"output": ...} / {"content": ...}).
    """
    s = (text or "").strip()
    if s:
        try:
            json.loads(s)
            return s
        except (ValueError, TypeError):
            pass
    return json.dumps({"output": text}, ensure_ascii=False)


def _build_id_name_map(messages: list[dict]) -> dict[str, str]:
    """Map Anthropic tool_use.id -> tool name across the whole transcript.

    free-code replays the full transcript each call, so a single pass keeps the
    proxy stateless while still resolving tool_result -> GigaChat function name.
    """
    idmap: dict[str, str] = {}
    for m in messages:
        if m.get("role") != "assistant":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for blk in content:
            if (
                isinstance(blk, dict)
                and blk.get("type") == "tool_use"
                and blk.get("id")
                and blk.get("name")
            ):
                idmap[blk["id"]] = blk["name"]
    return idmap


def translate_request(body: dict) -> dict:
    """Anthropic /v1/messages body -> GigaChat /chat body."""
    functions, names = _tools_to_functions(body.get("tools"))
    idmap = _build_id_name_map(body.get("messages") or [])

    giga_msgs: list[dict] = []
    sys_text = _system_to_text(body.get("system"))
    # Optional action-forcing suffix. The huge free-code system prompt biases the
    # model toward narrating ("Создаю файл…") instead of calling a tool. A short
    # terse directive appended at the end (à la the deepagents profile) nudges it
    # back to acting. Gated by env so the default stays format-faithful.
    import os as _os
    _rep = _os.environ.get("PROXY_SYSTEM_REPLACE")
    if _rep:
        sys_text = _rep
    _suf = _os.environ.get("PROXY_SYSTEM_SUFFIX")
    if _suf:
        sys_text = (sys_text + "\n\n" + _suf).strip() if sys_text else _suf
    if sys_text:
        giga_msgs.append({"role": "system", "content": sys_text})

    def add_names(msg: dict) -> dict:
        # Per-turn quirk: names go on user + function messages, NEVER assistant.
        if names:
            msg["functions"] = names
        return msg

    for m in body.get("messages") or []:
        role = m.get("role")
        content = m.get("content")

        if role == "user":
            if isinstance(content, str):
                giga_msgs.append(add_names({"role": "user", "content": content}))
                continue
            text_parts: list[str] = []
            for blk in content or []:
                if not isinstance(blk, dict):
                    continue
                btype = blk.get("type")
                if btype == "text":
                    text_parts.append(blk.get("text", ""))
                elif btype == "tool_result":
                    # Flush any pending user text before the function result.
                    if text_parts:
                        giga_msgs.append(
                            add_names({"role": "user", "content": "\n".join(text_parts)})
                        )
                        text_parts = []
                    name = idmap.get(blk.get("tool_use_id", ""), "tool")
                    result = _as_json_content(_stringify_tool_result(blk.get("content")))
                    giga_msgs.append(
                        add_names({"role": "function", "name": name, "content": result})
                    )
            if text_parts:
                giga_msgs.append(add_names({"role": "user", "content": "\n".join(text_parts)}))

        elif role == "assistant":
            if isinstance(content, str):
                if content.strip():
                    giga_msgs.append({"role": "assistant", "content": content})
                continue
            text_parts = []
            tool_uses = []
            for blk in content or []:
                if not isinstance(blk, dict):
                    continue
                btype = blk.get("type")
                if btype in ("thinking", "redacted_thinking"):
                    continue  # GigaChat reasoning is off; not part of the transcript.
                if btype == "text":
                    text_parts.append(blk.get("text", ""))
                elif btype == "tool_use":
                    tool_uses.append(blk)
            text = "\n".join(p for p in text_parts if p).strip()
            if text:
                giga_msgs.append({"role": "assistant", "content": text})
            for tu in tool_uses:
                giga_msgs.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "function_call": {
                            "name": tu.get("name"),
                            "arguments": tu.get("input") or {},
                        },
                    }
                )

    giga_body: dict[str, Any] = {"model": GIGA_MODEL, "messages": giga_msgs}
    if functions:
        giga_body["functions"] = functions
        tc = body.get("tool_choice")
        if isinstance(tc, dict) and tc.get("type") == "tool" and tc.get("name"):
            giga_body["function_call"] = {"name": tc["name"]}
        else:
            giga_body["function_call"] = "auto"
    if body.get("temperature") is not None:
        giga_body["temperature"] = body["temperature"]
    return giga_body


# --------------------------------------------------------------------------
# Outbound translation: GigaChat response -> Anthropic content blocks / SSE.
# --------------------------------------------------------------------------
def giga_to_blocks(giga_resp: dict) -> tuple[list[dict], str, dict]:
    """GigaChat /chat response -> (anthropic_blocks, stop_reason, usage)."""
    choice = (giga_resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    fc = msg.get("function_call")
    blocks: list[dict] = []

    text = msg.get("content") or ""
    if text:
        blocks.append({"type": "text", "text": text})

    if fc:
        args = fc.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                args = {"_raw": args}
        blocks.append(
            {
                "type": "tool_use",
                "id": "toolu_" + uuid.uuid4().hex[:24],
                "name": fc.get("name"),
                "input": args or {},
            }
        )
        stop_reason = "tool_use"
    else:
        fr = choice.get("finish_reason")
        stop_reason = "max_tokens" if fr == "length" else "end_turn"

    if not blocks:
        blocks.append({"type": "text", "text": ""})

    usage_in = (giga_resp.get("usage") or {}).get("prompt_tokens", 0) or 0
    usage_out = (giga_resp.get("usage") or {}).get("completion_tokens", 0) or 0
    return blocks, stop_reason, {"input_tokens": usage_in, "output_tokens": usage_out}


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def sse_stream(model: str, blocks: list[dict], stop_reason: str, usage: dict):
    """Fabricate an Anthropic SSE event stream from a single response."""
    msg_id = "msg_" + uuid.uuid4().hex[:24]
    yield _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": usage["input_tokens"], "output_tokens": 0},
            },
        },
    )
    for i, blk in enumerate(blocks):
        if blk["type"] == "text":
            yield _sse(
                "content_block_start",
                {"type": "content_block_start", "index": i,
                 "content_block": {"type": "text", "text": ""}},
            )
            if blk["text"]:
                yield _sse(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": i,
                     "delta": {"type": "text_delta", "text": blk["text"]}},
                )
        else:  # tool_use
            yield _sse(
                "content_block_start",
                {"type": "content_block_start", "index": i,
                 "content_block": {"type": "tool_use", "id": blk["id"],
                                   "name": blk["name"], "input": {}}},
            )
            yield _sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": i,
                 "delta": {"type": "input_json_delta",
                           "partial_json": json.dumps(blk["input"], ensure_ascii=False)}},
            )
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": i})
    yield _sse(
        "message_delta",
        {"type": "message_delta",
         "delta": {"stop_reason": stop_reason, "stop_sequence": None},
         "usage": {"output_tokens": usage["output_tokens"]}},
    )
    yield _sse("message_stop", {"type": "message_stop"})


def anthropic_message(model: str, blocks: list[dict], stop_reason: str, usage: dict) -> dict:
    """Non-streaming Anthropic Messages object."""
    return {
        "id": "msg_" + uuid.uuid4().hex[:24],
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage,
    }


def estimate_tokens(body: dict) -> int:
    """Cheap token estimate for /v1/messages/count_tokens (~chars/4)."""
    chars = len(_system_to_text(body.get("system")))
    for t in body.get("tools") or []:
        chars += len(json.dumps(t, ensure_ascii=False))
    for m in body.get("messages") or []:
        chars += len(json.dumps(m.get("content"), ensure_ascii=False))
    return max(1, chars // 4)


# --------------------------------------------------------------------------
# HTTP server.
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # silence default logging
        if DEBUG:
            super().log_message(*args)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw) if raw else {}

    def _send_json(self, status: int, obj: dict) -> None:
        payload = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_chunk(self, data: bytes) -> None:
        """Write one HTTP/1.1 chunked-transfer chunk."""
        self.wfile.write(f"{len(data):X}\r\n".encode())
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def do_HEAD(self) -> None:  # noqa: N802 — SDK preconnect probe
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 — health probe
        self._send_json(200, {"status": "ok"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            body = self._read_body()
        except (ValueError, OSError) as exc:
            self._send_json(400, {"type": "error", "error": {"message": f"bad body: {exc}"}})
            return

        if path.endswith("/count_tokens"):
            self._send_json(200, {"input_tokens": estimate_tokens(body)})
            return

        if not path.endswith("/v1/messages"):
            self._send_json(404, {"type": "error", "error": {"message": f"no route {path}"}})
            return

        giga_body = translate_request(body)
        try:
            import os as _os
            _cap = _os.environ.get("PROXY_CAPTURE")
            if _cap:
                _nt = len(body.get("tools") or [])
                _nf = len(giga_body.get("functions") or [])
                _nm = len(giga_body.get("messages") or [])
                with open(_cap, "a") as _fh:
                    _fh.write(json.dumps({"path": path, "in_tools": _nt,
                                          "out_functions": _nf, "out_msgs": _nm}) + "\n")
        except Exception:
            pass
        _log("-> giga", json.dumps(giga_body, ensure_ascii=False)[:800])
        try:
            assert _client is not None
            r = _client.post(CHAT_PATH, json=giga_body)
            r.raise_for_status()
            giga_resp = r.json()
        except Exception as exc:  # noqa: BLE001 — surface upstream failure as Anthropic error
            _log("upstream error", repr(exc))
            self._send_json(
                502, {"type": "error", "error": {"type": "api_error", "message": str(exc)}}
            )
            return

        _log("<- giga", json.dumps(giga_resp, ensure_ascii=False)[:500])
        blocks, stop_reason, usage = giga_to_blocks(giga_resp)
        model = body.get("model", "gigachat")

        if body.get("stream"):
            # HTTP/1.1 chunked framing so the client sees a clean end-of-stream
            # (no Content-Length is known up front for SSE).
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for chunk in sse_stream(model, blocks, stop_reason, usage):
                self._send_chunk(chunk)
            self.wfile.write(b"0\r\n\r\n")  # terminating chunk
            self.wfile.flush()
        else:
            self._send_json(200, anthropic_message(model, blocks, stop_reason, usage))


# --------------------------------------------------------------------------
# Self-test (offline translation validation).
# --------------------------------------------------------------------------
def _selftest() -> int:
    body = {
        "model": "claude-opus-4-8",
        "stream": True,
        "system": [{"type": "text", "text": "You are Claude Code."}],
        "tools": [
            {"name": "Read", "description": "Read a file",
             "input_schema": {"$schema": "x", "type": "object",
                              "properties": {"file_path": {"type": "string"}},
                              "required": ["file_path"], "additionalProperties": False}},
            {"name": "Write", "description": "Write a file",
             "input_schema": {"type": "object",
                              "properties": {"file_path": {"type": "string"},
                                             "content": {"type": "string"}},
                              "required": ["file_path", "content"]}},
        ],
        "messages": [
            {"role": "user", "content": "Создай hello.py"},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "..."},
                {"type": "text", "text": "Читаю каталог."},
                {"type": "tool_use", "id": "toolu_abc", "name": "Read",
                 "input": {"file_path": "."}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_abc",
                 "content": [{"type": "text", "text": "empty dir"}]}]},
        ],
    }
    # nested open-ended object field (must get properties:{} recursively)
    body["tools"].append(
        {"name": "Ask", "description": "ask",
         "input_schema": {"type": "object", "properties": {
             "answers": {"type": "object"},
             "items": {"type": "array", "items": {"type": "object",
                                                  "additionalProperties": True}}},
             "required": ["answers"]}}
    )

    g = translate_request(body)
    roles = [(m["role"], "functions" in m, m.get("function_call", {}).get("name")) for m in g["messages"]]
    assert g["model"] == "GigaChat"
    assert g["messages"][0]["role"] == "system" and "functions" not in g["messages"][0]
    # tool schemas stripped of meta keys
    assert "$schema" not in g["functions"][0]["parameters"]
    assert "additionalProperties" not in g["functions"][0]["parameters"]
    assert [f["name"] for f in g["functions"]] == ["Read", "Write", "Ask"]
    # recursive sanitize: every object node has properties, no additionalProperties
    ask = g["functions"][2]["parameters"]
    assert ask["properties"]["answers"]["properties"] == {}, ask
    assert ask["properties"]["items"]["items"]["properties"] == {}, ask
    assert "additionalProperties" not in ask["properties"]["items"]["items"]
    # user msg carries names, assistant does NOT
    umsg = next(m for m in g["messages"] if m["role"] == "user")
    assert umsg["functions"] == ["Read", "Write", "Ask"], umsg
    amsg = next(m for m in g["messages"] if m["role"] == "assistant" and m.get("function_call"))
    assert "functions" not in amsg, "assistant must not carry functions"
    assert amsg["function_call"]["name"] == "Read"
    # tool_result -> function message named via id map, JSON-wrapped, with names
    fmsg = next(m for m in g["messages"] if m["role"] == "function")
    assert fmsg["name"] == "Read", fmsg
    assert json.loads(fmsg["content"]) == {"output": "empty dir"}
    assert fmsg["functions"] == ["Read", "Write", "Ask"]
    # assistant text emitted as its own message before the call
    assert any(m["role"] == "assistant" and m.get("content") == "Читаю каталог." for m in g["messages"])

    # outbound: function_call -> tool_use + SSE
    giga_resp = {
        "choices": [{"message": {"role": "assistant", "content": "",
                                 "function_call": {"name": "Write",
                                                   "arguments": {"file_path": "hello.py",
                                                                 "content": "print('hi')"}}},
                     "finish_reason": "function_call"}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 30},
    }
    blocks, stop, usage = giga_to_blocks(giga_resp)
    assert stop == "tool_use"
    tu = next(b for b in blocks if b["type"] == "tool_use")
    assert tu["name"] == "Write" and tu["input"]["file_path"] == "hello.py"
    assert usage == {"input_tokens": 1200, "output_tokens": 30}
    sse = b"".join(sse_stream("m", blocks, stop, usage)).decode()
    for ev in ("message_start", "content_block_start", "input_json_delta",
               "content_block_stop", "message_delta", "message_stop"):
        assert ev in sse, ev
    assert '"stop_reason": "tool_use"' in sse

    # text-only outbound
    blocks2, stop2, _ = giga_to_blocks(
        {"choices": [{"message": {"content": "готово"}, "finish_reason": "stop"}], "usage": {}}
    )
    assert stop2 == "end_turn" and blocks2[0]["text"] == "готово"

    # count_tokens estimate
    assert estimate_tokens(body) > 0

    print(f"selftest OK — {len(roles)} giga messages:", roles)
    return 0


# --------------------------------------------------------------------------
def main() -> int:
    global UPSTREAM, CHAT_PATH, GIGA_MODEL, DEBUG, _client
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8087)
    ap.add_argument("--upstream", default="http://127.0.0.1:8083")
    ap.add_argument("--chat-path", default="/chat")
    ap.add_argument("--giga-model", default="GigaChat")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    DEBUG = args.debug
    if args.selftest:
        return _selftest()

    UPSTREAM = args.upstream.rstrip("/")
    CHAT_PATH = args.chat_path if args.chat_path.startswith("/") else "/" + args.chat_path
    GIGA_MODEL = args.giga_model
    _client = httpx.Client(base_url=UPSTREAM, timeout=httpx.Timeout(600.0, connect=15.0))

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(
        f"[proxy] Anthropic->GigaChat on http://127.0.0.1:{args.port}  "
        f"-> {UPSTREAM}{CHAT_PATH} (model={GIGA_MODEL})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[proxy] shutting down", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
