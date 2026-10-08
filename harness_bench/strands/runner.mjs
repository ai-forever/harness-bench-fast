/** Run one Strands agent session in the benchmark task workspace. */
import { spawn } from 'node:child_process';
import { mkdir, readFile, readdir, stat, unlink, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHarness, configureLogging as configureHarnessLogging } from '@strands-agents/harness';
import { BeforeToolCallEvent, Model, configureLogging } from '@strands-agents/sdk';
import { Sandbox } from '@strands-agents/sdk/sandbox';
import { Agent } from 'undici';

class RunnerError extends Error {
  constructor(code, { cause, details } = {}) {
    super(code, { cause });
    this.name = 'RunnerError';
    this.code = code;
    this.details = details;
  }
}

// Preserve useful transport identity without emitting messages, addresses, headers or bodies.
const transportCodes = new Set(['ECONNRESET', 'ECONNREFUSED', 'ETIMEDOUT', 'EPIPE',
  'EHOSTUNREACH', 'ENETUNREACH', 'ENOTFOUND', 'EAI_AGAIN', 'UND_ERR_CONNECT_TIMEOUT',
  'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT', 'UND_ERR_SOCKET', 'UND_ERR_ABORTED',
  'UND_ERR_REQ_CONTENT_LENGTH_MISMATCH', 'UND_ERR_RES_CONTENT_LENGTH_MISMATCH',
  'UND_ERR_DESTROYED', 'UND_ERR_CLOSED']);
function causeCodes(error) {
  const codes = [];
  for (let current = error, depth = 0; current && depth < 5; current = current.cause, depth += 1) {
    if (transportCodes.has(current.code) && !codes.includes(current.code)) codes.push(current.code);
  }
  return codes;
}

function finishDetails(choice) {
  const message = choice.message;
  return { phase: 'completion',
    finish_reason: choice.finish_reason == null ? null
      : ['stop', 'tool_calls', 'length', 'content_filter', 'function_call'].includes(choice.finish_reason)
        ? choice.finish_reason : 'unknown',
    content_type: message.content === null ? 'null'
      : ['string', 'undefined'].includes(typeof message.content) ? typeof message.content : 'other',
    content_length: typeof message.content === 'string' ? message.content.length : 0,
    reasoning_length: typeof message.reasoning_content === 'string' ? message.reasoning_content.length : 0,
    tool_call_count: Array.isArray(message.tool_calls) ? message.tool_calls.length : 0 };
}

const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const requireValue = (condition, code) => { if (!condition) throw new RunnerError(code); };
const positive = (value) => typeof value === 'number' && Number.isFinite(value) && value > 0;
const integer = (value) => Number.isSafeInteger(value);
const finiteIn = (value, low, high = Infinity) => typeof value === 'number'
  && Number.isFinite(value) && value >= low && value <= high;
const quietLogger = { debug() {}, info() {}, warn() {}, error() {} };

/** Validate every accepted sampling field; routing and message overrides are never accepted. */
export function modelParameters(raw = '{}') {
  let value;
  try { value = JSON.parse(raw); } catch { throw new RunnerError('invalid_model_params_json'); }
  requireValue(object(value), 'model_params_must_be_object');
  const validators = {
    skip_special_tokens: (x) => typeof x === 'boolean',
    separate_reasoning: (x) => typeof x === 'boolean',
    stop_token_ids: (x) => Array.isArray(x) && x.length > 0 && x.every((v) => integer(v) && v >= 0),
    chat_template_kwargs: (x) => object(x) && Object.entries(x).every(([key, val]) =>
      ['reasoning', 'drop_history_thinking', 'use_devsystem', 'use_suggester'].includes(key)
      && typeof val === 'boolean'),
    reasoning_effort: (x) => ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'].includes(x),
    temperature: (x) => finiteIn(x, 0),
    top_p: (x) => finiteIn(x, 0, 1) && x > 0,
    top_k: (x) => integer(x) && (x > 0 || x === -1),
    min_p: (x) => finiteIn(x, 0, 1),
    max_tokens: (x) => integer(x) && x > 0,
    max_completion_tokens: (x) => integer(x) && x > 0,
    seed: integer,
    frequency_penalty: (x) => finiteIn(x, -2, 2),
    presence_penalty: (x) => finiteIn(x, -2, 2),
    repetition_penalty: positive,
    stop: (x) => (typeof x === 'string' && x.length > 0)
      || (Array.isArray(x) && x.length > 0 && x.every((v) => typeof v === 'string' && v.length > 0)),
  };
  const params = {};
  for (const [key, val] of Object.entries(value)) {
    requireValue(Object.hasOwn(validators, key) || key === 'extra_body', 'unsupported_model_parameter');
    if (key === 'extra_body') continue;
    requireValue(validators[key](val), `invalid_${key}`);
    params[key] = val;
  }
  if (Object.hasOwn(value, 'extra_body')) {
    requireValue(object(value.extra_body), 'invalid_extra_body');
    for (const [key, val] of Object.entries(value.extra_body)) {
      requireValue(['top_k', 'min_p', 'repetition_penalty', 'skip_special_tokens', 'separate_reasoning',
        'stop_token_ids', 'chat_template_kwargs'].includes(key), 'unsupported_extra_body_parameter');
      requireValue(validators[key](val), `invalid_${key}`);
      requireValue(!Object.hasOwn(params, key) || JSON.stringify(params[key]) === JSON.stringify(val), 'conflicting_model_parameters');
      params[key] = val;
    }
  }
  requireValue(!(params.max_tokens && params.max_completion_tokens), 'conflicting_token_limits');
  return params;
}

export function configuration(env = process.env) {
  const required = (key) => {
    requireValue(typeof env[key] === 'string' && env[key].trim().length > 0, `missing_${key}`);
    return env[key];
  };
  const instruction = required('STRANDS_INSTRUCTION');
  const modelId = required('STRANDS_MODEL_ID');
  const apiKey = required('OPENAI_API_KEY');
  let baseUrl;
  try { baseUrl = new URL(required('OPENAI_BASE_URL')); }
  catch { throw new RunnerError('invalid_OPENAI_BASE_URL'); }
  requireValue(['http:', 'https:'].includes(baseUrl.protocol) && baseUrl.hostname
    && !baseUrl.username && !baseUrl.password && !baseUrl.search && !baseUrl.hash
    && /\/v1\/?$/.test(baseUrl.pathname), 'invalid_OPENAI_BASE_URL');
  const maxSteps = Number(env.STRANDS_MAX_STEPS ?? 8);
  const timeoutSeconds = Number(env.STRANDS_TIMEOUT_SECONDS ?? 600);
  const requestTimeoutSeconds = Number(env.STRANDS_REQUEST_TIMEOUT_SECONDS ?? 120);
  requireValue(integer(maxSteps) && maxSteps > 0, 'invalid_STRANDS_MAX_STEPS');
  requireValue(positive(timeoutSeconds) && timeoutSeconds <= 2_147_483, 'invalid_STRANDS_TIMEOUT_SECONDS');
  requireValue(positive(requestTimeoutSeconds) && requestTimeoutSeconds <= 2_147_483,
    'invalid_STRANDS_REQUEST_TIMEOUT_SECONDS');
  return {
    instruction, modelId, apiKey, maxSteps, timeoutSeconds,
    requestTimeoutSeconds: Math.min(timeoutSeconds, requestTimeoutSeconds),
    endpoint: `${baseUrl.href.replace(/\/$/, '')}/chat/completions`,
    modelParams: modelParameters(env.STRANDS_MODEL_PARAMS_JSON ?? '{}'),
  };
}

function textContent(blocks) {
  return blocks.map((block) => {
    if (block.type === 'textBlock') return block.text;
    if (block.type === 'jsonBlock') return JSON.stringify(block.json);
    throw new RunnerError('unsupported_message_content');
  }).join('');
}

/** Chat Completions adapter retaining full history and exact provider tool argument strings. */
export class ChatCompletionModel extends Model {
  calls = 0;
  usageResponses = 0;
  usage = { agent_input_tokens: 0, agent_output_tokens: 0, agent_total_tokens: 0 };
  responseModels = new Set();
  #config;
  #signal;
  #assistantResponses = new Map();
  constructor(config, signal) { super(); this.#config = config; this.#signal = signal; }
  getConfig() {
    const { modelId, modelParams } = this.#config;
    return { modelId, temperature: modelParams.temperature, topP: modelParams.top_p,
      maxTokens: modelParams.max_tokens ?? modelParams.max_completion_tokens };
  }
  updateConfig() { throw new RunnerError('runtime_model_changes_not_supported'); }

  formatMessages(messages, options = {}) {
    const result = [];
    if (options.systemPrompt) result.push({ role: 'system', content: typeof options.systemPrompt === 'string'
      ? options.systemPrompt : textContent(options.systemPrompt) });
    for (const message of messages) {
      if (message.role === 'user') {
        let pending = [];
        const flush = () => {
          if (pending.length) result.push({ role: 'user', content: textContent(pending) });
          pending = [];
        };
        for (const block of message.content) {
          if (block.type === 'toolResultBlock') {
            flush();
            result.push({ role: 'tool', tool_call_id: block.toolUseId, content: textContent(block.content) });
          } else pending.push(block);
        }
        flush();
      } else if (message.role === 'assistant') {
        const toolBlocks = message.content.filter((b) => b.type === 'toolUseBlock');
        if (toolBlocks.length) {
          // SDK aggregation parses tool JSON and removes whitespace-only text blocks. Restore the
          // original assistant envelope so a subsequent request keeps the exact captured prefix.
          const cached = this.#assistantResponses.get(toolBlocks[0].toolUseId);
          requireValue(cached && cached.tool_calls.length === toolBlocks.length, 'uncaptured_tool_history');
          for (let i = 0; i < toolBlocks.length; i += 1) {
            const block = toolBlocks[i];
            const call = cached.tool_calls[i];
            requireValue(call.id === block.toolUseId && call.function.name === block.name
              && JSON.stringify(JSON.parse(call.function.arguments)) === JSON.stringify(block.input),
            'mutated_tool_history');
          }
          result.push(structuredClone(cached));
        } else {
          const text = [];
          const reasoning = [];
          for (const block of message.content) {
            if (block.type === 'textBlock') text.push(block.text);
            else if (block.type === 'reasoningBlock' && typeof block.text === 'string') reasoning.push(block.text);
            else throw new RunnerError('unsupported_assistant_content');
          }
          result.push({ role: 'assistant', content: text.join(''),
            ...(reasoning.length ? { reasoning_content: reasoning.join('') } : {}) });
        }
      } else throw new RunnerError('unsupported_message_role');
    }
    return result;
  }

  async *stream(messages, options = {}) {
    requireValue(this.calls < this.#config.maxSteps, 'model_call_limit');
    this.#signal?.throwIfAborted();
    const request = {
      ...this.#config.modelParams,
      model: this.#config.modelId,
      messages: this.formatMessages(messages, options),
      stream: false,
    };
    const toolSpecs = options.toolSpecs ?? [];
    if (toolSpecs.length) {
      request.tools = toolSpecs.map((spec) => ({ type: 'function', function: {
        name: spec.name, description: spec.description,
        parameters: spec.inputSchema ?? { type: 'object', properties: {} },
      } }));
      const choice = options.toolChoice;
      if (!choice || choice.auto) request.tool_choice = 'auto';
      else if (choice.any) request.tool_choice = 'required';
      else if (choice.tool?.name) request.tool_choice = { type: 'function', function: { name: choice.tool.name } };
      else throw new RunnerError('unsupported_tool_choice');
    }
    const requestController = new AbortController();
    const timer = setTimeout(() => requestController.abort(), this.#config.requestTimeoutSeconds * 1000);
    const signal = AbortSignal.any([requestController.signal, this.#signal, options.cancelSignal].filter(Boolean));
    // Native fetch otherwise applies Undici's independent 300-second header/body deadlines.
    // The request/session AbortSignals own the budget, including connecting and reading the body.
    const dispatcher = new Agent({ headersTimeout: 0, bodyTimeout: 0, connect: { timeout: 0 } });
    let response;
    let phase = 'headers';
    this.calls += 1;
    try {
      const http = await fetch(this.#config.endpoint, {
        method: 'POST', redirect: 'error', signal, dispatcher,
        // Serving gateways and tunnels can close idle pooled sockets between tool turns.
        // A fresh connection per model call avoids stale reuse without hidden request retries.
        headers: { authorization: `Bearer ${this.#config.apiKey}`, 'content-type': 'application/json',
          connection: 'close' },
        body: JSON.stringify(request),
      });
      if (!http.ok) {
        await http.body?.cancel();
        throw new RunnerError(`model_http_${http.status}`);
      }
      phase = 'body';
      // Body I/O failures must remain retryable transport failures; only JSON syntax is protocol failure.
      const body = await http.text();
      try { response = JSON.parse(body); } catch (cause) {
        throw new RunnerError('invalid_model_response_json', { cause, details: { phase } });
      }
    } catch (error) {
      if (error instanceof RunnerError) throw error;
      throw new RunnerError(requestController.signal.aborted ? 'model_request_timeout'
        : signal.aborted ? 'model_request_cancelled' : 'model_transport_failed',
      { cause: error, details: { phase, cause_codes: causeCodes(error) } });
    } finally {
      clearTimeout(timer);
      await dispatcher.destroy();
    }

    // Capture provider accounting before validating the completion: limits and malformed
    // messages still consumed tokens. Missing usage never becomes a measured zero.
    if (typeof response?.model === 'string' && response.model.length <= 256) {
      this.responseModels.add(response.model);
    }
    if (object(response?.usage) && ['prompt_tokens', 'completion_tokens', 'total_tokens'].every((key) =>
      integer(response.usage[key]) && response.usage[key] >= 0)) {
      this.usageResponses += 1;
      this.usage.agent_input_tokens += response.usage.prompt_tokens;
      this.usage.agent_output_tokens += response.usage.completion_tokens;
      this.usage.agent_total_tokens += response.usage.total_tokens;
    }
    requireValue(object(response) && Array.isArray(response.choices) && response.choices.length === 1,
      'invalid_model_choices');
    const choice = response.choices[0];
    const message = choice?.message;
    requireValue(object(message) && message.role === 'assistant', 'invalid_assistant_message');
    // The provider already generated this completion. A policy token cap is gradable even when
    // its final tool arguments are incomplete; do not parse or execute any truncated tool call.
    if (choice.finish_reason === 'length') throw new RunnerError('model_token_limit');
    requireValue(message.content === undefined || message.content === null || typeof message.content === 'string',
      'unsupported_assistant_content');
    requireValue(message.reasoning_content === undefined || message.reasoning_content === null
      || typeof message.reasoning_content === 'string', 'unsupported_reasoning_content');
    requireValue(!message.refusal && !message.function_call && !message.audio, 'unsupported_assistant_response');
    requireValue(message.tool_calls === undefined || message.tool_calls === null || Array.isArray(message.tool_calls),
      'invalid_tool_calls');
    const calls = message.tool_calls ?? [];
    // An attempted action with no executable calls is a failed agent action. Never
    // reconstruct calls from raw content left behind by the provider's tool parser.
    if (choice.finish_reason === 'tool_calls' && calls.length === 0) {
      throw new RunnerError('invalid_tool_call', { details: finishDetails(choice) });
    }
    const knownTools = new Set(toolSpecs.map((s) => s.name));
    const seen = new Set();
    for (const call of calls) {
      requireValue(object(call) && typeof call.id === 'string' && call.id.length > 0
        && call.type === 'function' && object(call.function)
        && knownTools.has(call.function.name) && typeof call.function.arguments === 'string', 'invalid_tool_call');
      requireValue(!seen.has(call.id) && !this.#assistantResponses.has(call.id), 'duplicate_tool_call_id');
      seen.add(call.id);
      let input;
      try { input = JSON.parse(call.function.arguments); } catch { throw new RunnerError('invalid_tool_arguments'); }
      requireValue(object(input), 'invalid_tool_arguments');
    }
    if (!((choice.finish_reason === 'tool_calls' && calls.length > 0)
      || (choice.finish_reason === 'stop' && calls.length === 0 && typeof message.content === 'string'
        && message.content.trim().length > 0))) {
      throw new RunnerError('invalid_model_finish', { details: finishDetails(choice) });
    }
    if (calls.length) {
      const original = { role: 'assistant',
        ...(Object.hasOwn(message, 'content') ? { content: message.content } : {}),
        ...(Object.hasOwn(message, 'reasoning_content') ? { reasoning_content: message.reasoning_content } : {}),
        tool_calls: structuredClone(calls) };
      for (const call of calls) this.#assistantResponses.set(call.id, original);
    }
    if (response.usage) requireValue(['prompt_tokens', 'completion_tokens', 'total_tokens'].every((key) =>
      integer(response.usage[key]) && response.usage[key] >= 0), 'invalid_model_usage');
    yield { type: 'modelMessageStartEvent', role: 'assistant' };
    const emitBlock = function* (delta, start) {
      yield { type: 'modelContentBlockStartEvent', ...(start ? { start } : {}) };
      yield { type: 'modelContentBlockDeltaEvent', delta };
      yield { type: 'modelContentBlockStopEvent' };
    };
    if (message.reasoning_content) yield* emitBlock({ type: 'reasoningContentDelta', text: message.reasoning_content });
    if (message.content) yield* emitBlock({ type: 'textDelta', text: message.content });
    for (const call of calls) yield* emitBlock({ type: 'toolUseInputDelta', input: call.function.arguments },
      { type: 'toolUseStart', toolUseId: call.id, name: call.function.name });
    if (response.usage) {
      const usage = response.usage;
      yield { type: 'modelMetadataEvent', usage: { inputTokens: usage.prompt_tokens,
        outputTokens: usage.completion_tokens, totalTokens: usage.total_tokens } };
    }
    yield { type: 'modelMessageStopEvent', stopReason: calls.length ? 'toolUse' : 'endTurn' };
  }
}

/** Execute tools in the task workspace; HBF owns isolation and this adapter handles cancellation. */
class TaskEnvironment extends Sandbox {
  constructor(signal) { super(); this.signal = signal; }
  async *executeStreaming(command, options = {}) {
    this.signal.throwIfAborted();
    const env = { ...process.env, ...options.env };
    for (const key of Object.keys(env)) {
      if (key.startsWith('STRANDS_') || ['OPENAI_API_KEY', 'OPENAI_BASE_URL', 'ANTHROPIC_API_KEY',
        'ANTHROPIC_BASE_URL', 'GOOGLE_API_KEY', 'GOOGLE_BASE_URL', 'GOOGLE_API_URL', 'SESSION_ID'].includes(key)) delete env[key];
    }
    const signal = AbortSignal.any([this.signal, options.signal].filter(Boolean));
    const result = await new Promise((resolveResult, reject) => {
      let timedOut = false;
      let timeout;
      let stdout = '';
      let stderr = '';
      let size = 0;
      let outputExceeded = false;
      const child = spawn('sh', ['-c', command], {
        cwd: options.cwd ?? process.cwd(), env, detached: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      });
      const kill = () => {
        if (child.pid) {
          try { process.kill(-child.pid, 'SIGKILL'); } catch (error) {
            if (error.code !== 'ESRCH') child.kill('SIGKILL');
          }
        }
      };
      const collect = (name, chunk) => {
        size += Buffer.byteLength(chunk);
        if (size > 4 * 1024 * 1024) { outputExceeded = true; kill(); return; }
        if (name === 'stdout') stdout += chunk;
        else stderr += chunk;
      };
      child.stdout.setEncoding('utf8').on('data', (chunk) => collect('stdout', chunk));
      child.stderr.setEncoding('utf8').on('data', (chunk) => collect('stderr', chunk));
      // A background child may keep stdout open after sh exits; terminate it before waiting for close.
      child.once('exit', kill);
      child.once('error', () => reject(new RunnerError('shell_execution_failed')));
      child.once('close', (code) => {
        clearTimeout(timeout);
        signal.removeEventListener('abort', kill);
        // Kill any descendants the command left in its owned process group.
        kill();
        if (signal.aborted) reject(new RunnerError('tool_cancelled'));
        else if (outputExceeded) reject(new RunnerError('shell_output_limit'));
        else resolveResult({ type: 'executionResult', exitCode: timedOut ? 124 : (code ?? 1),
          stdout, stderr, outputFiles: [] });
      });
      signal.addEventListener('abort', kill, { once: true });
      if (signal.aborted) kill();
      if (options.timeout !== undefined) timeout = setTimeout(() => { timedOut = true; kill(); }, options.timeout * 1000);
    });
    yield result;
  }
  async *executeCodeStreaming() { throw new RunnerError('execute_code_not_supported'); }
  async readFile(path) { this.signal.throwIfAborted(); return readFile(resolve(path), { signal: this.signal }); }
  async writeFile(path, content) {
    this.signal.throwIfAborted();
    await mkdir(dirname(resolve(path)), { recursive: true });
    await writeFile(resolve(path), content, { signal: this.signal });
  }
  async removeFile(path) { this.signal.throwIfAborted(); await unlink(resolve(path)); }
  async listFiles(path) {
    this.signal.throwIfAborted();
    return Promise.all((await readdir(resolve(path))).sort().map(async (name) => {
      const info = await stat(resolve(path, name));
      return { name, isDir: info.isDirectory(), size: info.size };
    }));
  }
}

export async function run(config, observation = {}) {
  // SDK diagnostics can contain tool arguments, request bodies, or endpoint credentials.
  configureLogging(quietLogger);
  configureHarnessLogging(quietLogger);
  const controller = new AbortController();
  let interrupted = false;
  const interrupt = () => { interrupted = true; controller.abort(); };
  // The SDK barrel import installs unused Bash-tool handlers that call process.exit(0).
  // This disposable profile owns cancellation; keep those handlers from bypassing child
  // cleanup and the terminal event, and restore callers' handlers when run() finishes.
  const signalHandlers = new Map(['SIGTERM', 'SIGINT'].map((signal) => [signal, process.rawListeners(signal)]));
  for (const signal of signalHandlers.keys()) {
    process.removeAllListeners(signal);
    process.on(signal, interrupt);
  }
  const timer = setTimeout(() => controller.abort(), config.timeoutSeconds * 1000);
  const model = new ChatCompletionModel(config, controller.signal);
  let toolCalls = 0;
  let agent;
  try {
    agent = await createHarness({
      model, printer: false, retryStrategy: null, toolExecutor: 'sequential',
      builtinTools: { '*': false, shell: true, read: { media: false }, write: true, edit: true },
      builtinPlugins: [], skills: false, session: false, memory: false, backgroundTasks: false,
      contextManager: false, caching: false, sandbox: new TaskEnvironment(controller.signal),
    });
    agent.addHook(BeforeToolCallEvent, () => { toolCalls += 1; });
    const result = await agent.invoke(config.instruction, {
      cancelSignal: controller.signal, limits: { turns: config.maxSteps },
    });
    requireValue(!controller.signal.aborted, 'session_timeout');
    requireValue(['endTurn', 'stopSequence'].includes(result.stopReason),
      result.stopReason === 'limitTurns' ? 'model_call_limit' : 'incomplete_session');
    return { result, modelCalls: model.calls, toolCalls };
  } catch (error) {
    if (controller.signal.aborted) throw new RunnerError(interrupted ? 'session_interrupted' : 'session_timeout');
    throw error;
  } finally {
    clearTimeout(timer);
    for (const [signal, handlers] of signalHandlers) {
      process.removeListener(signal, interrupt);
      for (const handler of handlers) process.on(signal, handler);
    }
    Object.assign(observation, { model_calls: model.calls, tool_calls: toolCalls,
      usage_complete: model.calls > 0 && model.usageResponses === model.calls,
      response_models: [...model.responseModels],
      stats: { agent_steps: model.calls, agent_llm_calls: model.calls, agent_tool_calls: toolCalls,
        ...(model.usageResponses ? model.usage : {}) } });
    // SDK 1.19 has cancel(), but no shutdown(). There are no sessions, MCP clients or background
    // tasks in this profile. Aborting our shared signal also terminates owned shell process groups.
    controller.abort();
    agent?.cancel();
  }
}

function errorCode(error) {
  for (let current = error, depth = 0; current && depth < 5; current = current.cause, depth += 1) {
    if (current instanceof RunnerError) return current.code;
  }
  return 'runtime_failure';
}

function errorDetails(error) {
  for (let current = error, depth = 0; current && depth < 5; current = current.cause, depth += 1) {
    if (current instanceof RunnerError) return current.details;
  }
  return undefined;
}

export async function main(env = process.env) {
  const observation = { model_calls: 0, tool_calls: 0, usage_complete: false,
    response_models: [], stats: { agent_steps: 0, agent_llm_calls: 0, agent_tool_calls: 0 } };
  let config;
  let terminal;
  const redact = (value) => typeof value === 'string'
    ? (env.OPENAI_API_KEY ? value.replaceAll(env.OPENAI_API_KEY, '[redacted-session-key]') : value)
    : null;
  const redactParameters = (value) => typeof value === 'string' ? redact(value)
    : Array.isArray(value) ? value.map(redactParameters)
      : object(value) ? Object.fromEntries(Object.entries(value).map(([key, val]) => [key, redactParameters(val)]))
        : value;
  try {
    config = configuration(env);
    // Harness telemetry reads process-global env. The disposable runner never exports traces.
    process.env.OTEL_TRACES_EXPORTER = 'none';
    process.env.OTEL_SDK_DISABLED = 'true';
    const { result } = await run(config, observation);
    const output = result.lastMessage.content.filter((b) => b.type === 'textBlock').map((b) => b.text).join('');
    terminal = { strands_status: 'completed', stop_reason: result.stopReason,
      failure_kind: null, retryable: false, error: null, output: redact(output) };
    return 0;
  } catch (error) {
    const code = errorCode(error);
    const limitExit = { model_call_limit: 3, model_token_limit: 4 }[code];
    const retryable = /^model_http_(429|5\d\d)$/.test(code)
      || ['model_transport_failed', 'model_request_timeout'].includes(code);
    const infrastructure = !config || retryable || /^model_http_\d{3}$/.test(code)
      || ['invalid_model_response_json', 'invalid_model_choices',
      'invalid_assistant_message', 'invalid_model_usage', 'invalid_model_finish'].includes(code);
    terminal = { strands_status: limitExit ? 'limited' : 'failed', error: code,
      failure_kind: infrastructure ? 'infrastructure' : code === 'session_timeout' ? 'timeout' : null,
      retryable, ...(limitExit ? { stop_reason: code } : {}),
      ...(errorDetails(error) ? { error_details: errorDetails(error) } : {}) };
    // Emit a compact diagnostic on stderr alongside the terminal event on stdout.
    console.error(JSON.stringify({ strands_status: terminal.strands_status, error: code,
      ...(limitExit ? { stop_reason: code } : {}),
      ...(terminal.error_details ? { error_details: terminal.error_details } : {}) }));
    if (limitExit) return limitExit;
    return ['session_timeout', 'model_request_timeout'].includes(code) ? 124 : 1;
  } finally {
    console.log(JSON.stringify({ type: 'strands_result', schema_version: 1,
      ...terminal, ...observation,
      requested_model: redact(config?.modelId ?? env.STRANDS_MODEL_ID),
      model: redact(config?.modelId ?? env.STRANDS_MODEL_ID),
      response_models: observation.response_models.map(redact),
      reasoning_effort: config?.modelParams.reasoning_effort ?? 'default',
      reasoning_enabled: config?.modelParams.chat_template_kwargs?.reasoning ?? null,
      max_steps: config?.maxSteps ?? null,
      timeout_seconds: config?.timeoutSeconds ?? null,
      request_timeout_seconds: config?.requestTimeoutSeconds ?? null,
      model_params: config ? redactParameters(config.modelParams) : null }));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const args = process.argv.slice(2);
  if (args.length === 1 && ['--help', '-h'].includes(args[0])) {
    console.log('Usage: node runner.mjs\n'
      + 'Required environment: STRANDS_INSTRUCTION, STRANDS_MODEL_ID, OPENAI_BASE_URL, OPENAI_API_KEY\n'
      + 'Optional environment: STRANDS_MODEL_PARAMS_JSON ({}), STRANDS_MAX_STEPS (8),\n'
      + 'STRANDS_TIMEOUT_SECONDS (600), STRANDS_REQUEST_TIMEOUT_SECONDS (120).\n'
      + 'Exit codes: 0 completed, 1 failed, 2 CLI error, 3 step limit, 4 token limit, 124 timeout.\n'
      + 'Runs one disposable session in the current task directory. --help makes no model call.');
  } else if (args.length) {
    console.error(JSON.stringify({ strands_status: 'failed', error: 'unsupported_command_arguments' }));
    process.exitCode = 2;
  } else process.exitCode = await main();
}
