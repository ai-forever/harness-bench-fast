#!/usr/bin/env python3
"""Loopback-only protocol adapter for native Codex and Claude Code benchmarks.

Stateless: no conversations or response bodies retained after the HTTP request.
Codex Responses stays streaming; reasoning summaries and text tool outputs normalize.
Claude Messages translates to Chat Completions and emits Anthropic SSE events.
"""
import json
import hashlib
import hmac
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx

MAX_BODY = 16 * 1024 * 1024


def text_blocks(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise ValueError('Expected text or text blocks')
    if any(b.get('type') not in ('text', 'input_text', 'output_text') for b in value):
        raise ValueError('Unsupported non-text content; refusing lossy conversion')
    return '\n'.join(b['text'] for b in value)


def normalize_responses(body):
    for item in body.get('input', []) if isinstance(body.get('input'), list) else []:
        if item.get('type') == 'reasoning' and item.get('summary'):
            # Gateway accepts reasoning content, but rejects nonempty summary blocks.
            # Preserve every summary text in reasoning content, avoiding duplicates.
            content = item.setdefault('content', [])
            existing = {block.get('text') for block in content}
            for block in item['summary']:
                if block.get('type') != 'summary_text':
                    raise ValueError('Unsupported reasoning summary')
                if block['text'] not in existing:
                    content.append({'type': 'reasoning_text', 'text': block['text']})
                    existing.add(block['text'])
            item['summary'] = []
        if item.get('type') in ('function_call_output', 'custom_tool_call_output'):
            if isinstance(item.get('output'), list):
                item['output'] = text_blocks(item['output'])
    return body


def thinking_signature(text):
    # Adapter-local integrity token, not an Anthropic model signature.
    return "hbf-v1:" + hmac.new(os.environ['HBF_API_KEY'].encode(), text.encode(), hashlib.sha256).hexdigest()


def messages_to_chat(body):
    messages = []
    if body.get('system'):
        messages.append({'role': 'system', 'content': text_blocks(body['system'])})
    for msg in body['messages']:
        content = msg['content']
        if isinstance(content, str):
            messages.append({'role': msg['role'], 'content': content})
            continue
        texts, calls, results, thinking = [], [], [], []
        for block in content:
            kind = block['type']
            if kind == 'text':
                texts.append(block['text'])
            elif kind == 'thinking':
                if msg['role'] != 'assistant' or not hmac.compare_digest(block.get('signature', ''), thinking_signature(block['thinking'])):
                    raise ValueError('Invalid adapter thinking signature')
                thinking.append(block['thinking'])
            elif kind == 'tool_use':
                calls.append({'id': block['id'], 'type': 'function', 'function': {
                    'name': block['name'], 'arguments': json.dumps(block['input'])}})
            elif kind == 'tool_result':
                results.append({'role': 'tool', 'tool_call_id': block['tool_use_id'],
                                'content': text_blocks(block.get('content', ''))})
            else:
                raise ValueError('Unsupported Messages content: ' + kind)
        # Tool results must precede the next user message.
        messages.extend(results)
        if texts or calls or thinking:
            entry = {'role': msg['role'], 'content': '\n'.join(texts) or None}
            if thinking:
                entry['reasoning_content'] = ''.join(thinking)
            if calls:
                entry['tool_calls'] = calls
            messages.append(entry)
    out = {'model': body['model'], 'messages': messages, 'stream': False}
    for key in ('max_tokens', 'temperature', 'top_p'):
        if key in body:
            out[key] = body[key]
    if body.get('stop_sequences'):
        out['stop'] = body['stop_sequences']
    if body.get('tools'):
        out['tools'] = [{'type': 'function', 'function': {
            'name': t['name'], 'description': t.get('description', ''),
            'parameters': t['input_schema']}} for t in body['tools']]
    choice = body.get('tool_choice', {})
    if choice:
        out['tool_choice'] = {'auto': 'auto', 'any': 'required', 'none': 'none'}.get(choice['type'])
        if choice['type'] == 'tool':
            out['tool_choice'] = {'type': 'function', 'function': {'name': choice['name']}}
        if out['tool_choice'] is None:
            raise ValueError('Unsupported tool choice')
    if body.get('thinking', {}).get('type') not in (None, 'disabled', 'enabled', 'adaptive'):
        raise ValueError('Unsupported thinking mode')
    # This model emits reasoning natively. Preserve it; do not pretend that the
    # Anthropic budget_tokens parameter enforces a native-model thinking budget.
    return out


def chat_to_message(data, model):
    choice = data['choices'][0]
    msg = choice['message']
    content = []
    if msg.get('reasoning_content'):
        reasoning = msg['reasoning_content']
        content.append({'type': 'thinking', 'thinking': reasoning, 'signature': thinking_signature(reasoning)})
    if msg.get('content'):
        content.append({'type': 'text', 'text': msg['content']})
    for call in msg.get('tool_calls') or []:
        content.append({'type': 'tool_use', 'id': call['id'], 'name': call['function']['name'],
                        'input': json.loads(call['function']['arguments'])})
    usage = data.get('usage', {})
    finish = choice.get('finish_reason')
    stop = 'tool_use' if msg.get('tool_calls') else ('max_tokens' if finish == 'length' else 'end_turn')
    return {'id': 'msg_' + uuid.uuid4().hex, 'type': 'message', 'role': 'assistant',
            'model': model, 'content': content, 'stop_reason': stop, 'stop_sequence': None,
            'usage': {'input_tokens': usage.get('prompt_tokens', 0),
                      'output_tokens': usage.get('completion_tokens', 0)}}


def message_events(message):
    initial = dict(message, content=[], stop_reason=None,
                   usage=dict(message['usage'], output_tokens=0))
    yield {'type': 'message_start', 'message': initial}
    for i, block in enumerate(message['content']):
        if block['type'] == 'text':
            start = {'type': 'text', 'text': ''}
            delta = {'type': 'text_delta', 'text': block['text']}
        elif block['type'] == 'thinking':
            start = {'type': 'thinking', 'thinking': '', 'signature': ''}
            delta = {'type': 'thinking_delta', 'thinking': block['thinking']}
        else:
            start = dict(block, input={})
            delta = {'type': 'input_json_delta', 'partial_json': json.dumps(block['input'])}
        yield {'type': 'content_block_start', 'index': i, 'content_block': start}
        yield {'type': 'content_block_delta', 'index': i, 'delta': delta}
        if block['type'] == 'thinking':
            yield {'type': 'content_block_delta', 'index': i, 'delta': {'type': 'signature_delta', 'signature': block['signature']}}
        yield {'type': 'content_block_stop', 'index': i}
    yield {'type': 'message_delta', 'delta': {'stop_reason': message['stop_reason'], 'stop_sequence': None},
           'usage': {'output_tokens': message['usage']['output_tokens']}}
    yield {'type': 'message_stop'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # No prompts, tool outputs or credentials in service logs.

    def send_json(self, status, data):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.send_json(200 if self.path == '/health' else 404, {'status': 'ok'})

    def do_POST(self):
        if not self.server.slots.acquire(blocking=False):
            return self.send_json(429, {'error': {'message': 'Adapter concurrency limit'}})
        try:
            self.handle_post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            print(json.dumps({'event': 'adapter_error', 'type': type(exc).__name__, 'detail': str(exc)[:300]}), flush=True)
            if not getattr(self, 'started', False):
                self.send_json(502, {'error': {'type': 'api_error', 'message': str(exc)[:300]}})
        finally:
            self.server.slots.release()

    def handle_post(self):
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= MAX_BODY:
            return self.send_json(413, {'error': {'message': 'Invalid request size'}})
        body = json.loads(self.rfile.read(length))
        path = self.path.split('?')[0]
        headers = {'Authorization': 'Bearer ' + os.environ['HBF_API_KEY']}
        upstream = os.environ.get('HBF_UPSTREAM', 'http://127.0.0.1:19033/v1')
        with httpx.Client(timeout=600, trust_env=False) as client:
            if path == '/v1/responses':
                body = normalize_responses(body)
                with client.stream('POST', upstream + '/responses', json=body, headers=headers) as response:
                    self.send_response(response.status_code)
                    self.send_header('Content-Type', response.headers.get('content-type', 'application/json'))
                    self.end_headers()
                    self.started = True
                    audit_buffer = b''
                    reasoning_input = [hashlib.sha256(block['text'].encode()).hexdigest()
                        for item in body.get('input', []) if isinstance(item, dict) and item.get('type') == 'reasoning'
                        for block in item.get('content', []) if block.get('text')]
                    for chunk in response.iter_bytes():
                        audit_buffer += chunk
                        while b'\n' in audit_buffer:
                            line, audit_buffer = audit_buffer.split(b'\n', 1)
                            if line.startswith(b'data: ') and line[6:].strip() != b'[DONE]':
                                event = json.loads(line[6:])
                                if event.get('type') == 'response.completed':
                                    hashes = []
                                    for item in event.get('response', {}).get('output', []):
                                        if item.get('type') == 'reasoning':
                                            blocks = item.get('content') or item.get('summary') or []
                                            hashes.extend(hashlib.sha256(x['text'].encode()).hexdigest() for x in blocks if x.get('text'))
                                    print(json.dumps({'event': 'codex_reasoning_roundtrip', 'input': reasoning_input, 'output': hashes}), flush=True)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                return
            if path != '/v1/messages':
                return self.send_json(404, {'error': {'message': 'Unsupported endpoint'}})
            chat_request = messages_to_chat(body)
            response = client.post(upstream + '/chat/completions', json=chat_request, headers=headers)
            if response.status_code != 200:
                return self.send_json(response.status_code, {'error': {'type': 'api_error', 'message': response.text[:1000]}})
            message = chat_to_message(response.json(), body['model'])
            fingerprint = lambda text: {'sha256': hashlib.sha256(text.encode()).hexdigest(), 'chars': len(text)}
            print(json.dumps({'event': 'thinking_roundtrip', 'id': message['id'], 'requested': body.get('thinking'),
                              'input': [fingerprint(m['reasoning_content']) for m in chat_request['messages'] if m.get('reasoning_content')],
                              'output': [fingerprint(b['thinking']) for b in message['content'] if b['type'] == 'thinking']}), flush=True)
            if not body.get('stream'):
                return self.send_json(200, message)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.started = True
            for event in message_events(message):
                self.wfile.write(('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode())
                self.wfile.flush()


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('HBF_PROXY_PORT', '19034'))), Handler)
    server.daemon_threads = True
    server.slots = threading.BoundedSemaphore(132)
    server.serve_forever()
