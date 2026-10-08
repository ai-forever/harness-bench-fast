import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdtemp, mkdir, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { Agent, getGlobalDispatcher, setGlobalDispatcher } from 'undici';
import { configuration, main, modelParameters, ChatCompletionModel } from './runner.mjs';

const runner = fileURLToPath(new URL('./runner.mjs', import.meta.url));
const key = 'test-session-key-do-not-log';
const instruction = '  Fix the test fixture.\nKeep this exact whitespace.\n';
const assistant = (content = 'Done.', extra = {}) => ({
  choices: [{ index: 0, message: { role: 'assistant', content, ...extra },
    finish_reason: extra.tool_calls?.length ? 'tool_calls' : 'stop' }],
  usage: { prompt_tokens: 100, completion_tokens: 20, total_tokens: 120 },
});
const toolCall = (id, name, args) => ({ id, type: 'function', function: { name, arguments: args } });

async function serverFixture(t, respond) {
  const requests = [];
  let failure;
  const server = createServer(async (req, res) => {
    try {
      let raw = '';
      for await (const chunk of req) raw += chunk;
      requests.push({ path: req.url, headers: req.headers, body: JSON.parse(raw) });
      await respond(requests.at(-1), res, requests.length);
    } catch (error) {
      failure = error;
      res.writeHead(500).end('{}');
    }
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    if (failure) throw failure;
  });
  return { requests, baseUrl: `http://127.0.0.1:${server.address().port}/v1` };
}

const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)); };

async function assertNoAgentState(home) {
  const entries = await readdir(home);
  if (entries.length === 0) return;
  // Docker Desktop's amd64 emulation creates these empty directories even for
  // `node --version`. Reject every other entry, including any persisted files.
  assert.deepEqual(entries, ['.cache']);
  assert.deepEqual(await readdir(join(home, '.cache')), ['rosetta']);
  assert.deepEqual(await readdir(join(home, '.cache', 'rosetta')), []);
}

async function invokeRunner(t, baseUrl, extraEnv = {}, args = []) {
  const cwd = await mkdtemp(join(tmpdir(), 'hbf-strands-test-'));
  const home = join(cwd, 'home');
  await mkdir(home);
  t.after(() => rm(cwd, { recursive: true, force: true }));
  const child = spawn(process.execPath, [runner, ...args], {
    cwd,
    env: { ...process.env, HOME: home, OPENAI_BASE_URL: baseUrl, OPENAI_API_KEY: key,
      STRANDS_INSTRUCTION: instruction, STRANDS_MODEL_ID: 'offline-model', STRANDS_MAX_STEPS: '3',
      STRANDS_TIMEOUT_SECONDS: '5', STRANDS_REQUEST_TIMEOUT_SECONDS: '2',
      STRANDS_MODEL_PARAMS_JSON: '{}', OTEL_TRACES_EXPORTER: 'none', ...extraEnv },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stdout = '';
  let stderr = '';
  child.stdout.setEncoding('utf8').on('data', (value) => { stdout += value; });
  child.stderr.setEncoding('utf8').on('data', (value) => { stderr += value; });
  const timer = setTimeout(() => child.kill('SIGKILL'), 10000);
  const code = await new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', (code, signal) => {
      if (signal) reject(new Error(`runner terminated by ${signal}`));
      else resolve(code);
    });
  }).finally(() => clearTimeout(timer));
  return { code, stdout, stderr, cwd, home };
}

test('real harness executes shell, preserves exact tool/reasoning history and sampling', async (t) => {
  // Keep noncanonical JSON formatting to catch accidental SDK reserialization.
  const toolArgs = JSON.stringify({ command: "printf observed > artifact.txt; printf '%s' \"${OPENAI_API_KEY-unset}:${ANTHROPIC_API_KEY-unset}:${GOOGLE_API_KEY-unset}:${SESSION_ID-unset}\"" }, null, 2);
  const first = assistant(' \n', { reasoning_content: 'Reasoning stays separate.\n',
    tool_calls: [toolCall('call-a', 'shell', toolArgs)] });
  const { baseUrl, requests } = await serverFixture(t, (request, res, n) => {
    assert.equal(request.path, '/v1/chat/completions');
    assert.equal(request.headers.authorization, `Bearer ${key}`);
    assert.equal(request.body.model, 'offline-model');
    assert.equal(request.body.stream, false);
    assert.equal(request.body.temperature, 0);
    assert.equal(request.body.top_p, 0.85);
    assert.equal(request.body.top_k, 17);
    assert.equal(request.body.max_tokens, 256);
    assert.equal(request.body.seed, 41);
    assert.equal(request.body.presence_penalty, 0.2);
    assert.deepEqual(request.body.stop, ['end-marker']);
    assert.deepEqual(request.body.tools.map((x) => x.function.name), ['shell', 'read', 'write', 'edit']);
    assert.equal(request.body.messages[1].content, instruction);
    if (n === 1) json(res, first);
    else {
      assert.equal(n, 2);
      assert.deepEqual(request.body.messages[2], first.choices[0].message);
      assert.equal(request.body.messages[3].role, 'tool');
      assert.equal(request.body.messages[3].tool_call_id, 'call-a');
      assert.equal(JSON.parse(request.body.messages[3].content).output, 'unset:unset:unset:unset');
      json(res, assistant('Completed fixture.', { tool_calls: null, reasoning_content: null }));
    }
  });
  const outcome = await invokeRunner(t, baseUrl, { ANTHROPIC_API_KEY: key, GOOGLE_API_KEY: key, SESSION_ID: key,
    STRANDS_MODEL_PARAMS_JSON: JSON.stringify({
    temperature: 0, top_p: 0.85, top_k: 17, max_tokens: 256, seed: 41,
    presence_penalty: 0.2, stop: ['end-marker'],
  }) });
  assert.equal(outcome.code, 0, outcome.stderr);
  assert.equal(requests.length, 2);
  assert.equal(JSON.parse(outcome.stdout).model_calls, 2);
  assert.equal(JSON.parse(outcome.stdout).tool_calls, 1);
  assert.equal(await readFile(join(outcome.cwd, 'artifact.txt'), 'utf8'), 'observed');
  await assertNoAgentState(outcome.home);
  assert.deepEqual((await readdir(outcome.cwd)).sort(), ['artifact.txt', 'home']);
});

test('multiple native file tools retain IDs and run sequentially', async (t) => {
  let taskPath;
  const { baseUrl } = await serverFixture(t, (request, res, n) => {
    if (n === 1) {
      taskPath = request.body.messages[1].content;
      json(res, assistant(null, { reasoning_content: '', tool_calls: [
        toolCall('write-a', 'write', JSON.stringify({ path: taskPath, content: 'before' })),
        toolCall('edit-b', 'edit', JSON.stringify({ path: taskPath, old_str: 'before', new_str: 'after' })),
        toolCall('read-c', 'read', JSON.stringify({ path: taskPath })),
      ] }));
    } else {
      assert.equal(request.body.messages[2].content, null);
      assert.equal(request.body.messages[2].reasoning_content, '');
      assert.deepEqual(request.body.messages.slice(3).map((m) => m.tool_call_id), ['write-a', 'edit-b', 'read-c']);
      assert.match(request.body.messages[5].content, /after/);
      json(res, assistant());
    }
  });
  const dir = await mkdtemp(join(tmpdir(), 'hbf-strands-files-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const outcome = await invokeRunner(t, baseUrl, { STRANDS_INSTRUCTION: join(dir, 'value.txt') });
  assert.equal(outcome.code, 0, outcome.stderr);
  assert.equal(await readFile(taskPath, 'utf8'), 'after');
});

test('real harness uses a fresh TCP connection for every model call without hidden retries', async (t) => {
  const sockets = new Set();
  const { baseUrl, requests } = await serverFixture(t, (request, res, n) => {
    assert.equal(request.headers.connection, 'close');
    assert.ok(!sockets.has(res.socket), 'model call reused a prior TCP connection');
    sockets.add(res.socket);
    if (n < 3) {
      json(res, assistant(null, { tool_calls: [toolCall(`call-${n}`, 'shell', '{"command":"pwd"}')] }));
    } else {
      assert.equal(n, 3, 'runner issued an unexpected request');
      json(res, assistant('Completed three calls.'));
    }
  });
  const outcome = await invokeRunner(t, baseUrl);
  assert.equal(outcome.code, 0, outcome.stderr);
  const event = terminalEvent(outcome);
  assert.equal(sockets.size, 3);
  assert.equal(requests.length, 3);
  assert.equal(event.stats.agent_llm_calls, 3);
  assert.equal(event.stats.agent_tool_calls, 2);
  assert.equal(event.stats.agent_total_tokens, 360);
});

test('step exhaustion is nonzero and sends no request beyond the budget', async (t) => {
  const { baseUrl, requests } = await serverFixture(t, (_request, res, n) => json(res,
    assistant(null, { tool_calls: [toolCall(`call-${n}`, 'shell', '{"command":"true"}')] })));
  const outcome = await invokeRunner(t, baseUrl, { STRANDS_MAX_STEPS: '2' });
  assert.equal(outcome.code, 3);
  assert.equal(requests.length, 2);
  assert.equal(JSON.parse(outcome.stderr).error, 'model_call_limit');
  assert.equal(JSON.parse(outcome.stderr).strands_status, 'limited');
});

test('per-model guard also bounds calls outside the normal agent turn loop', async (t) => {
  const { baseUrl, requests } = await serverFixture(t, (_request, res) => json(res, assistant()));
  const config = configuration({ OPENAI_BASE_URL: baseUrl, OPENAI_API_KEY: key,
    STRANDS_MODEL_ID: 'offline-model', STRANDS_INSTRUCTION: 'test', STRANDS_MAX_STEPS: '1' });
  const model = new ChatCompletionModel(config, new AbortController().signal);
  for await (const _event of model.stream([{ role: 'user', content: [{ type: 'textBlock', text: 'test' }] }])) {}
  await assert.rejects(async () => {
    for await (const _event of model.stream([{ role: 'user', content: [{ type: 'textBlock', text: 'test' }] }])) {}
  }, /model_call_limit/);
  assert.equal(requests.length, 1);
});

for (const [name, response] of [
  ['missing choices', {}],
  ['malformed tool arguments', assistant(null, { tool_calls: [toolCall('bad', 'shell', '{')] })],
  ['unknown tool', assistant(null, { tool_calls: [toolCall('bad', 'subagent', '{}')] })],
  ['duplicate tool IDs', assistant(null, { tool_calls: [toolCall('a', 'read', '{}'), toolCall('a', 'read', '{}')] })],
]) {
  test(`${name} fails without retry`, async (t) => {
    const { baseUrl, requests } = await serverFixture(t, (_request, res) => json(res, response));
    const outcome = await invokeRunner(t, baseUrl);
    assert.equal(outcome.code, 1);
    assert.equal(requests.length, 1);
    assert.equal(JSON.parse(outcome.stderr).strands_status, 'failed');
  });
}

test('token cap is gradable and never executes or parses a truncated tool call', async (t) => {
  const response = assistant(null, { tool_calls: [toolCall('partial', 'shell',
    '{"command":"printf leaked > leaked.txt"')] });
  response.choices[0].finish_reason = 'length';
  const { baseUrl, requests } = await serverFixture(t, (_request, res) => json(res, response));
  const outcome = await invokeRunner(t, baseUrl);
  assert.equal(outcome.code, 4, outcome.stderr);
  assert.equal(requests.length, 1);
  assert.equal(JSON.parse(outcome.stderr).strands_status, 'limited');
  assert.equal(JSON.parse(outcome.stderr).stop_reason, 'model_token_limit');
  assert.deepEqual(await readdir(outcome.cwd), ['home']);
});

test('HTTP failures never retry or expose response text and credentials', async (t) => {
  const { baseUrl, requests } = await serverFixture(t, (_request, res) => {
    res.writeHead(500).end(`secret from provider: ${key}`);
  });
  const outcome = await invokeRunner(t, baseUrl);
  assert.equal(outcome.code, 1);
  assert.equal(requests.length, 1);
  assert.equal(JSON.parse(outcome.stderr).error, 'model_http_500');
  assert.ok(!outcome.stderr.includes(key));
  assert.ok(!outcome.stdout.includes(key));
});

test('whole-session timeout cancels pending HTTP', async (t) => {
  const { baseUrl, requests } = await serverFixture(t, () => {});
  const outcome = await invokeRunner(t, baseUrl, { STRANDS_TIMEOUT_SECONDS: '0.2' });
  assert.equal(outcome.code, 124, outcome.stderr);
  assert.equal(requests.length, 1);
  assert.equal(JSON.parse(outcome.stderr).error, 'session_timeout');
});

test('per-request timeout exits 124 even with session budget remaining', async (t) => {
  const { baseUrl, requests } = await serverFixture(t, () => {});
  const outcome = await invokeRunner(t, baseUrl, { STRANDS_REQUEST_TIMEOUT_SECONDS: '0.15' });
  assert.equal(outcome.code, 124, outcome.stderr);
  assert.equal(requests.length, 1);
  assert.equal(JSON.parse(outcome.stderr).error, 'model_request_timeout');
});

for (const phase of ['headers', 'body']) {
  test(`configured request budget overrides shorter implicit ${phase} deadline`, async (t) => {
    const originalDispatcher = getGlobalDispatcher();
    const shortDispatcher = new Agent({ headersTimeout: 50, bodyTimeout: 50 });
    setGlobalDispatcher(shortDispatcher);
    t.after(async () => {
      setGlobalDispatcher(originalDispatcher);
      await shortDispatcher.destroy();
    });
    const { baseUrl, requests } = await serverFixture(t, async (_request, res) => {
      const payload = JSON.stringify(assistant());
      if (phase === 'body') {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.write(payload.slice(0, 1));
      }
      // Undici checks deadlines on a coarse timer; 1500 ms makes the 50 ms baseline deterministic.
      await new Promise((resolve) => setTimeout(resolve, 1500));
      if (phase === 'headers') json(res, assistant());
      else res.end(payload.slice(1));
    });
    const expectedCode = phase === 'headers' ? 'UND_ERR_HEADERS_TIMEOUT' : 'UND_ERR_BODY_TIMEOUT';
    await assert.rejects(async () => {
      const response = await fetch(`${baseUrl}/chat/completions`, {
        method: 'POST', body: '{}', headers: { connection: 'close' },
      });
      await response.text();
    }, (error) => error.cause?.code === expectedCode);

    const config = configuration({ OPENAI_BASE_URL: baseUrl, OPENAI_API_KEY: key,
      STRANDS_MODEL_ID: 'offline-model', STRANDS_INSTRUCTION: 'test', STRANDS_REQUEST_TIMEOUT_SECONDS: '3' });
    const model = new ChatCompletionModel(config, new AbortController().signal);
    const events = [];
    for await (const event of model.stream([{ role: 'user', content: [{ type: 'textBlock', text: 'test' }] }])) {
      events.push(event);
    }
    assert.equal(requests.length, 2); // One baseline probe and one model call, no retries.
    assert.equal(model.calls, 1);
    assert.equal(model.usage.agent_total_tokens, 120);
    assert.equal(events.at(-1).stopReason, 'endTurn');
  });
}

test('configured request deadline also cancels a stalled body with implicit deadlines disabled', async (t) => {
  const { baseUrl } = await serverFixture(t, (_request, res) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.write('{');
  });
  const outcome = await invokeRunner(t, baseUrl, { STRANDS_REQUEST_TIMEOUT_SECONDS: '0.1' });
  const event = terminalEvent(outcome);
  assert.equal(outcome.code, 124);
  assert.equal(event.error, 'model_request_timeout');
  assert.equal(event.failure_kind, 'infrastructure');
  assert.equal(event.retryable, true);
  assert.equal(event.error_details.phase, 'body');
  assert.equal(event.stats.agent_llm_calls, 1);
  assert.equal(event.usage_complete, false);
});

test('whole-session timeout kills the shell process group and descendants', async (t) => {
  const { baseUrl, requests } = await serverFixture(t, (_request, res) => json(res,
    assistant(null, { tool_calls: [toolCall('wait', 'shell', '{"command":"sleep 20; printf leaked > leaked.txt"}')] })));
  const start = performance.now();
  const outcome = await invokeRunner(t, baseUrl, { STRANDS_TIMEOUT_SECONDS: '0.3' });
  assert.equal(outcome.code, 124, outcome.stderr);
  assert.equal(requests.length, 1);
  assert.ok(performance.now() - start < 4000);
  assert.ok(!(await readdir(outcome.cwd)).includes('leaked.txt'));
});

test('shell exit cleans background descendants holding stdout open', async (t) => {
  const { baseUrl, requests } = await serverFixture(t, (_request, res, n) => json(res, n === 1
    ? assistant(null, { tool_calls: [toolCall('background', 'shell', '{"command":"sleep 20 &"}')] })
    : assistant()));
  const start = performance.now();
  const outcome = await invokeRunner(t, baseUrl);
  assert.equal(outcome.code, 0, outcome.stderr);
  assert.equal(requests.length, 2);
  assert.ok(performance.now() - start < 4000);
});

test('sampling validation rejects routing overrides, invalid numbers and contradictory fields', () => {
  assert.deepEqual(modelParameters('{"extra_body":{"top_k":-1},"temperature":0,"seed":0}'),
    { temperature: 0, seed: 0, top_k: -1 });
  for (const raw of ['[]', 'null', '{', '{"temperature":null}', '{"temperature":1e999}',
    '{"seed":true}', '{"top_p":0}', '{"top_k":0}', '{"stream":true}',
    '{"extra_body":{"model":"remote"}}', '{"top_k":2,"extra_body":{"top_k":3}}',
    '{"max_tokens":1,"max_completion_tokens":2}', '{"__proto__":{}}']) {
    assert.throws(() => modelParameters(raw));
  }
});

test('missing gateway credentials fail before any network request', async (t) => {
  const { baseUrl, requests } = await serverFixture(t, (_request, res) => json(res, assistant()));
  const outcome = await invokeRunner(t, baseUrl, { OPENAI_API_KEY: '' });
  assert.equal(outcome.code, 1);
  assert.equal(requests.length, 0);
  assert.equal(JSON.parse(outcome.stderr).error, 'missing_OPENAI_API_KEY');
});

test('--help imports the relocated runner without credentials, files or model calls', async (t) => {
  const { baseUrl, requests } = await serverFixture(t, (_request, res) => json(res, assistant()));
  const outcome = await invokeRunner(t, baseUrl, { OPENAI_API_KEY: '', STRANDS_INSTRUCTION: '' }, ['--help']);
  assert.equal(outcome.code, 0, outcome.stderr);
  assert.match(outcome.stdout, /STRANDS_MODEL_PARAMS_JSON/);
  assert.equal(requests.length, 0);
  await assertNoAgentState(outcome.home);
  assert.deepEqual(await readdir(outcome.cwd), ['home']);
});

function terminalEvent(outcome) {
  const lines = outcome.stdout.trim().split('\n');
  assert.equal(lines.length, 1, 'exactly one terminal stdout event');
  const event = JSON.parse(lines[0]);
  assert.equal(event.type, 'strands_result');
  assert.equal(event.schema_version, 1);
  assert.equal(event.requested_model, 'offline-model');
  assert.equal(event.model, 'offline-model');
  return event;
}

test('terminal event records real usage, response identity and effective reasoning parameters', async (t) => {
  const params = { temperature: 0, reasoning_effort: 'high', skip_special_tokens: false,
    separate_reasoning: true, stop: ['<|message_sep|>'], stop_token_ids: [128001],
    chat_template_kwargs: { reasoning: true, drop_history_thinking: false,
      use_devsystem: false, use_suggester: false } };
  const { baseUrl } = await serverFixture(t, (request, res, n) => {
    for (const [field, value] of Object.entries(params)) assert.deepEqual(request.body[field], value);
    json(res, { ...assistant(n === 1 ? null : 'Done.', n === 1
      ? { tool_calls: [toolCall('observe', 'shell', '{"command":"true"}')] } : {}), model: `served-${n}` });
  });
  const outcome = await invokeRunner(t, baseUrl, { STRANDS_MODEL_PARAMS_JSON: JSON.stringify(params) });
  assert.equal(outcome.code, 0, outcome.stderr);
  const event = terminalEvent(outcome);
  assert.deepEqual(event.stats, { agent_steps: 2, agent_llm_calls: 2, agent_tool_calls: 1,
    agent_input_tokens: 200, agent_output_tokens: 40, agent_total_tokens: 240 });
  assert.equal(event.usage_complete, true);
  assert.equal(event.failure_kind, null);
  assert.equal(event.error, null);
  assert.equal(event.retryable, false);
  assert.deepEqual(event.response_models, ['served-1', 'served-2']);
  assert.deepEqual(event.model_params, params);
  assert.equal(event.reasoning_effort, 'high');
  assert.equal(event.reasoning_enabled, true);
});

test('missing usage remains unknown while known partial sums are retained', async (t) => {
  const { baseUrl } = await serverFixture(t, (_request, res, n) => {
    const response = assistant(n === 1 ? null : 'Done.', n === 1
      ? { tool_calls: [toolCall('observe', 'shell', '{"command":"true"}')] } : {});
    if (n === 2) delete response.usage;
    json(res, response);
  });
  const event = terminalEvent(await invokeRunner(t, baseUrl));
  assert.equal(event.usage_complete, false);
  assert.equal(event.stats.agent_total_tokens, 120);
  assert.equal(event.stats.agent_llm_calls, 2);
  assert.equal(event.reasoning_effort, 'default');
  assert.equal(event.reasoning_enabled, null);
});

test('absent usage omits token counters instead of reporting measured zero', async (t) => {
  const { baseUrl } = await serverFixture(t, (_request, res) => {
    const response = assistant();
    delete response.usage;
    json(res, response);
  });
  const event = terminalEvent(await invokeRunner(t, baseUrl));
  assert.equal(event.usage_complete, false);
  assert.deepEqual(event.stats, { agent_steps: 1, agent_llm_calls: 1, agent_tool_calls: 0 });
});

for (const status of [429, 500, 503, 401]) {
  test(`HTTP ${status} terminal classification and partial accounting`, async (t) => {
    const { baseUrl } = await serverFixture(t, (_request, res, n) => {
      if (n === 1) json(res, assistant(null, { tool_calls: [toolCall('observe', 'shell', '{"command":"true"}')] }));
      else res.writeHead(status).end(`sensitive-provider-body ${key}`);
    });
    const outcome = await invokeRunner(t, baseUrl);
    assert.equal(outcome.code, 1);
    const event = terminalEvent(outcome);
    assert.equal(event.strands_status, 'failed');
    assert.equal(event.failure_kind, 'infrastructure');
    assert.equal(event.retryable, status !== 401);
    assert.equal(event.error, `model_http_${status}`);
    assert.equal(event.stats.agent_llm_calls, 2);
    assert.equal(event.stats.agent_tool_calls, 1);
    assert.equal(event.stats.agent_total_tokens, 120);
    assert.equal(event.usage_complete, false);
    assert.ok(!outcome.stdout.includes('sensitive-provider-body'));
    assert.ok(!outcome.stdout.includes(key));
  });
}

test('transport failure is retryable and never invents usage', async (t) => {
  const { baseUrl } = await serverFixture(t, (_request, res) => res.destroy());
  const outcome = await invokeRunner(t, baseUrl);
  const event = terminalEvent(outcome);
  assert.equal(event.error, 'model_transport_failed');
  assert.equal(event.failure_kind, 'infrastructure');
  assert.equal(event.retryable, true);
  assert.equal(event.usage_complete, false);
  assert.equal(event.stats.agent_total_tokens, undefined);
  assert.deepEqual(event.error_details, { phase: 'headers', cause_codes: ['UND_ERR_SOCKET'] });
});

test('body socket failure remains retryable transport instead of malformed JSON', async (t) => {
  const { baseUrl } = await serverFixture(t, (_request, res) => {
    res.writeHead(200, { 'content-type': 'application/json', 'content-length': '100000' });
    res.write('{"sensitive-provider-body":');
    setTimeout(() => res.destroy(), 30);
  });
  const outcome = await invokeRunner(t, baseUrl);
  const event = terminalEvent(outcome);
  assert.equal(event.error, 'model_transport_failed');
  assert.equal(event.failure_kind, 'infrastructure');
  assert.equal(event.retryable, true);
  assert.equal(event.error_details.phase, 'body');
  assert.equal(event.error_details.cause_codes.length, 1);
  // Connection: close lets Undici report the truncated declared body more precisely.
  assert.ok(['UND_ERR_SOCKET', 'UND_ERR_RES_CONTENT_LENGTH_MISMATCH']
    .includes(event.error_details.cause_codes[0]));
  assert.equal(event.usage_complete, false);
  assert.ok(!outcome.stdout.includes('sensitive-provider-body'));
});

test('complete malformed JSON remains a nonretryable protocol failure', async (t) => {
  const { baseUrl } = await serverFixture(t, (_request, res) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end('invalid sensitive-provider-body');
  });
  const outcome = await invokeRunner(t, baseUrl);
  const event = terminalEvent(outcome);
  assert.equal(event.error, 'invalid_model_response_json');
  assert.equal(event.retryable, false);
  assert.deepEqual(event.error_details, { phase: 'body' });
  assert.ok(!outcome.stdout.includes('sensitive-provider-body'));
});

test('transport diagnostics retain known nested cause codes without secrets', async (t) => {
  const originalFetch = globalThis.fetch;
  const originalLog = console.log;
  const originalError = console.error;
  const stdout = [];
  const stderr = [];
  t.after(() => { globalThis.fetch = originalFetch; console.log = originalLog; console.error = originalError; });
  const cause = Object.assign(new Error(`sensitive-provider-body ${key}`), { code: 'UND_ERR_HEADERS_TIMEOUT',
    cause: Object.assign(new Error('private host name'), { code: key }) });
  globalThis.fetch = async () => { throw new TypeError(`fetch failed ${key}`, { cause }); };
  console.log = (line) => stdout.push(line);
  console.error = (line) => stderr.push(line);
  const code = await main({ OPENAI_BASE_URL: 'http://127.0.0.1:1/v1', OPENAI_API_KEY: key,
    STRANDS_MODEL_ID: 'offline-model', STRANDS_INSTRUCTION: 'test diagnostic failure' });
  assert.equal(code, 1);
  const event = JSON.parse(stdout.at(-1));
  assert.deepEqual(event.error_details, { phase: 'headers', cause_codes: ['UND_ERR_HEADERS_TIMEOUT'] });
  assert.ok(![...stdout, ...stderr].join('').includes(key));
  assert.ok(![...stdout, ...stderr].join('').includes('private host name'));
  assert.ok(![...stdout, ...stderr].join('').includes('sensitive-provider-body'));
});

for (const [kind, env, expectedError, expectedKind, retryable] of [
  ['request', { STRANDS_REQUEST_TIMEOUT_SECONDS: '0.1' }, 'model_request_timeout', 'infrastructure', true],
  ['overall', { STRANDS_TIMEOUT_SECONDS: '0.3' }, 'session_timeout', 'timeout', false],
]) {
  test(`${kind} timeout retains first completion accounting`, async (t) => {
    const { baseUrl } = await serverFixture(t, (_request, res, n) => {
      if (n === 1) json(res, assistant(null, { tool_calls: [toolCall('observe', 'shell', '{"command":"true"}')] }));
    });
    const outcome = await invokeRunner(t, baseUrl, env);
    assert.equal(outcome.code, 124);
    const event = terminalEvent(outcome);
    assert.equal(event.error, expectedError);
    assert.equal(event.failure_kind, expectedKind);
    assert.equal(event.retryable, retryable);
    assert.equal(event.stats.agent_llm_calls, 2);
    assert.equal(event.stats.agent_total_tokens, 120);
    assert.equal(event.usage_complete, false);
  });
}

for (const limit of ['step', 'token']) {
  test(`${limit} limit has measured partial usage and is gradable`, async (t) => {
    const { baseUrl } = await serverFixture(t, (_request, res, n) => {
      const response = assistant(null, { tool_calls: [toolCall(`call-${n}`, 'shell', '{"command":"true"}')] });
      if (limit === 'token') response.choices[0].finish_reason = 'length';
      json(res, response);
    });
    const outcome = await invokeRunner(t, baseUrl, { STRANDS_MAX_STEPS: '1' });
    assert.equal(outcome.code, limit === 'step' ? 3 : 4);
    const event = terminalEvent(outcome);
    assert.equal(event.strands_status, 'limited');
    assert.equal(event.failure_kind, null);
    assert.equal(event.retryable, false);
    assert.equal(event.stats.agent_total_tokens, 120);
    assert.equal(event.stats.agent_tool_calls, limit === 'step' ? 1 : 0);
    assert.equal(event.usage_complete, true);
  });
}

test('configuration errors emit one safe terminal event with no fabricated usage', async (t) => {
  const event = terminalEvent(await invokeRunner(t, 'http://127.0.0.1:1/v1', { OPENAI_API_KEY: '' }));
  assert.equal(event.error, 'missing_OPENAI_API_KEY');
  assert.equal(event.failure_kind, 'infrastructure');
  assert.equal(event.retryable, false);
  assert.equal(event.stats.agent_llm_calls, 0);
  assert.equal(event.stats.agent_total_tokens, undefined);
});

test('SGLang controls are typed and cannot smuggle routing or template overrides', () => {
  const extra = { skip_special_tokens: false, separate_reasoning: true, stop_token_ids: [128001],
    chat_template_kwargs: { reasoning: true, drop_history_thinking: false } };
  assert.deepEqual(modelParameters(JSON.stringify({ extra_body: extra })), extra);
  for (const value of [
    { skip_special_tokens: 'false' }, { separate_reasoning: 1 }, { stop_token_ids: [-1] },
    { stop_token_ids: [1.5] }, { stop_token_ids: [] }, { reasoning_effort: 'extreme' },
    { chat_template_kwargs: { reasoning: 'true' } }, { chat_template_kwargs: { tools: [] } },
    { extra_body: { chat_template: 'override' } }, { extra_body: { messages: [] } },
    { separate_reasoning: true, extra_body: { separate_reasoning: false } },
  ]) assert.throws(() => modelParameters(JSON.stringify(value)));
});

test('SIGTERM cancels detached tool descendants and emits one final event', async (t) => {
  const { baseUrl } = await serverFixture(t, (_request, res) => json(res, assistant(null,
    { tool_calls: [toolCall('interrupt', 'shell', '{"command":"kill -TERM $PPID; sleep 20; printf leaked > leaked.txt"}')] })));
  const start = performance.now();
  const outcome = await invokeRunner(t, baseUrl);
  assert.equal(outcome.code, 1, outcome.stderr);
  const event = terminalEvent(outcome);
  assert.equal(event.error, 'session_interrupted');
  assert.equal(event.retryable, false);
  assert.equal(event.stats.agent_total_tokens, 120);
  assert.ok(performance.now() - start < 4000);
  assert.ok(!(await readdir(outcome.cwd)).includes('leaked.txt'));
});

test('malformed provider protocol is infrastructure failure without automatic retry', async (t) => {
  const { baseUrl } = await serverFixture(t, (_request, res) => json(res, { choices: [] }));
  const event = terminalEvent(await invokeRunner(t, baseUrl));
  assert.equal(event.error, 'invalid_model_choices');
  assert.equal(event.failure_kind, 'infrastructure');
  assert.equal(event.retryable, false);
});

for (const apiKey of ['0', '1', '"']) {
  test(`metadata redaction preserves numeric JSON for API key ${JSON.stringify(apiKey)}`, async (t) => {
    const { baseUrl } = await serverFixture(t, (_request, res) => json(res, assistant()));
    const outcome = await invokeRunner(t, baseUrl, { OPENAI_API_KEY: apiKey,
      STRANDS_MODEL_PARAMS_JSON: JSON.stringify({ temperature: 0, top_p: 1, stop: [apiKey] }) });
    assert.equal(outcome.code, 0, outcome.stderr);
    const event = terminalEvent(outcome);
    assert.deepEqual(event.model_params, { temperature: 0, top_p: 1, stop: ['[redacted-session-key]'] });
  });
}


test('reasoning-only stop violates the provider finish contract and is not gradable', async (t) => {
  const response = assistant(null, { reasoning_content: 'Unparsed reasoning without a final answer.' });
  const { baseUrl } = await serverFixture(t, (_request, res) => json(res, response));
  const outcome = await invokeRunner(t, baseUrl);
  assert.equal(outcome.code, 1);
  const event = terminalEvent(outcome);
  assert.equal(event.strands_status, 'failed');
  assert.equal(event.error, 'invalid_model_finish');
  assert.equal(event.failure_kind, 'infrastructure');
  assert.equal(event.retryable, false);
  assert.equal(event.stats.agent_total_tokens, 120);
  assert.equal(event.usage_complete, true);
  assert.deepEqual(event.error_details, { phase: 'completion', finish_reason: 'stop',
    content_type: 'null', content_length: 0, reasoning_length: 42, tool_call_count: 0 });
});

for (const [label, call] of [
  ['invalid arguments', toolCall('bad', 'shell', '{')],
  ['unknown tool', toolCall('bad', 'subagent', '{}')],
]) {
  test(`model-generated ${label} remains an agent failure`, async (t) => {
    const { baseUrl } = await serverFixture(t, (_request, res) => json(res,
      assistant(null, { tool_calls: [call] })));
    const event = terminalEvent(await invokeRunner(t, baseUrl));
    assert.equal(event.strands_status, 'failed');
    assert.equal(event.failure_kind, null);
    assert.equal(event.retryable, false);
  });
}

for (const [label, toolCalls] of [['empty', []], ['null', null], ['missing', undefined]]) {
  test(`tool_calls finish with ${label} calls is a scored agent failure without text repair`, async (t) => {
    const raw = '<|function_call|>{"name":"write","arguments":{"path":"must-not-exist","content":\'broken JSON\'}}';
    const response = assistant(raw, { tool_calls: toolCalls });
    response.choices[0].finish_reason = 'tool_calls';
    const { baseUrl, requests } = await serverFixture(t, (_request, res) => json(res, response));
    const outcome = await invokeRunner(t, baseUrl);
    const event = terminalEvent(outcome);
    assert.equal(outcome.code, 1);
    assert.equal(event.strands_status, 'failed');
    assert.equal(event.failure_kind, null);
    assert.equal(event.error, 'invalid_tool_call');
    assert.equal(event.retryable, false);
    assert.equal(event.stats.agent_total_tokens, 120);
    assert.equal(event.stats.agent_llm_calls, 1);
    assert.equal(event.stats.agent_tool_calls, 0);
    assert.equal(event.usage_complete, true);
    assert.equal(requests.length, 1);
    assert.deepEqual(await readdir(outcome.cwd), ['home']);
    assert.deepEqual(event.error_details, { phase: 'completion', finish_reason: 'tool_calls',
      content_type: 'string', content_length: raw.length, reasoning_length: 0, tool_call_count: 0 });
    assert.ok(!outcome.stdout.includes(raw));
  });
}
