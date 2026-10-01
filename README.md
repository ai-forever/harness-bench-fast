# harness-bench

[Live leaderboard and benchmark results](https://ai-forever.github.io/harness-bench-fast/)

Current task set: **411 tasks, `task-set v0.17.0`**. Tasks 392–411 are the long-context wave. New runs are measured on this version.

## Results (task-set v0.17.0, 411 tasks)

| Harness | Profile | Model | Result | % | Long wave (392–411) | Steps | Tokens |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| deepagents | none | DeepSeek V4.1 Flash (high) | 406/411 | 98.8% | 18/20 | 10,457 | 1,693,784,997 |
| deepagents | none | GPT-6 Luna (high) | 390/411 | 94.9% | 12/20 | 9,822 | 360,640,300 |
| deepagents | none | GLM-5.3 (high) | 389/411 | 94.6% | 14/20 | 8,031 | 1,005,651,882 |
| mini-SWE-agent | — | GigaChat 3.5 Ultra Reasoning (PROM)² | 366/411 | 89.1% | 0/20 | 5,898 | 60,635,573 |
| deepagents | Anthropic | Claude Haiku 4.5 | 361/411 | 87.8% | 1/20 | 7,326 | 600,214,572 |
| deepagents | GigaChat | GigaChat 3.5 xxxB (internal version) | 348/411 | 84.7% | 0/20 | 5,207 | 61,842,876 |
| pi-mono | — | GigaChat 3.5 xxxB (internal version)² | 316/411 | 76.9% | 0/20 | 5,027 | 331,507,322 |
| pi-mono | — | GigaChat 3.5 Ultra Reasoning (PROM)² | 299/411 | 72.7% | 0/20 | 3,291 | 139,229,464 |
| mini-SWE-agent | — | GigaChat 3.5 xxxB (internal version)² | 237/411 | 57.7% | 0/20 | 5,044 | 44,772,995 |
| deepagents | GigaChat | GigaChat 3.5 Ultra Reasoning (PROM)¹ | 222/411 | 54.0% | 0/20 | 3,879 | 38,611,899 |

Each row is one full run, measured 2026-09-29/30. DeepSeek V4.1 Flash
(`deepseek/deepseek-v4.1-flash`), GPT-6 Luna (`openai/gpt-6-luna`) and GLM-5.3
(`z-ai/glm-5.3`) ran through `run-openrouter` with stock deepagents (no built-in profile
exists for them), `OPENROUTER_REASONING_EFFORT=high` and `--isolation none`; DeepSeek and
GLM with `--forward-reasoning-history`, GPT-6 Luna through `--responses-api` because it
accepts reasoning with tools only there. Claude Haiku 4.5 ran through
`run-openrouter` with the built-in `anthropic:claude-haiku-4-5` profile
(`--harness-profile`, `--prompt-cache`, `--isolation none`, reasoning `default`); it solves
one long-wave task (399). The deepagents GigaChat rows used `deepagents-gigachat` **0.0.4** through
the native `run` command (`--concurrency 3`, default timeout and step limit; the long-wave
floors apply). Model builds: GigaChat 3.5 xxxB (internal version), `32.9.16.9`,
and `GigaChat-3.5-Ultra-Reasoning:3.5.16.9`; reasoning level `default`.
On tasks 1–391 the internal-version run scores 348/391, in line with the six profile-0.0.4 runs
on v0.16.0 below (352.8 ± 2.8). Neither GigaChat model solves a long-wave task in any of the listed harnesses.

¹ On PROM this model plans several parallel function calls, returns only one of them,
and keeps all of them under `functions_state_id`; the next request with a single
function result then fails with `422 Function calls count does not match function
results count` (88 of the first 294 tasks as-is). This row was measured with a wrapper
that omits `functions_state_id` from outgoing messages, after which no 422 occurred and
the model issues the remaining calls one at a time. It measures the model under that
workaround, not the stock client.

² The four CLI runs were measured on 2026-09-30 with mini-SWE-agent **2.4.6**
and pi-mono **0.73.1**, without a deepagents profile, in a Linux bwrap sandbox.
Each covers all 411 tasks: concurrency 3, timeout 900 s (7200 s for the long
wave), max_tokens 16,384 per response, no artificial CLI step cap. Model builds
match the deepagents rows: internal version `32.9.16.9` with reasoning `default`;
Ultra Reasoning PROM `3.5.16.9` with reasoning `medium` and reasoning-history
replay. PROM uses the same state-ID workaround described above. These compare
complete configurations: tools, isolation and budgets differ, and PROM reasoning
also differs from its deepagents run. One run per setup; variation across repeats
has not been measured.

CLI steps and tokens were audited against native traces, including usage saved
on service turns after mini-SWE format errors and completed responses before
timeouts. Token counts are lower bounds for interrupted responses. The mini-SWE
PROM score includes one verifier correction (task 253): a service trace in the
workspace root was mistaken for a model-produced secret leak. Replaying the saved
deterministic commands without model calls reproduced the original failure;
moving only telemetry into an excluded dot-directory changed it to a pass
(365 → 366). Raw results remain unchanged in private artifacts.

## Earlier results (task-set v0.16.0, 391 tasks)

These runs are the full 391-task set. v0.17.0 adds twenty tasks, so a score here is not a score on the current set.
`Steps` and `Tokens` are shown when the runner exposes them; `—` means the metric
is absent from the run artifact, not that nothing was spent. Each row is one full
run per harness + model setup (**k=1**). The 13 archived v0.16.0 runs on the
benchmark server were checked against their JSON artifacts on **2026-10-01**;
the table also retains the separately recorded Pi run. This is the date of
verification, not the date the runs were measured. Different harnesses and
profiles measure complete configurations, not a controlled comparison of model
weights. Reasoning level is `default` unless a row explicitly records another
level; for historical artifacts this does not establish the provider default. The `Profile` column shows the deepagents harness
profile applied: `GigaChat` = the `deepagents-gigachat` tuning profile,
`Anthropic` = the Anthropic harness profile built into deepagents, `OpenAI` =
the OpenAI harness profile built into deepagents, `none` = stock deepagents
defaults, `—` = not applicable (non-deepagents harnesses).
GigaChat rows use the internal version, build `32.9.23.6`.

**Every `GigaChat`-profile row in the v0.16.0 table below was produced with `deepagents-gigachat`
0.0.3.** The pin now installs **0.0.4**, which is a result-affecting change, so a
fresh install no longer reproduces those rows. Measured on GigaChat 3.5 (internal version),
k=4 per version with versions interleaved on one stand: 0.0.3 342.0/391 (87.5%,
sd 3.9) against 0.0.4 **352.8/391 (90.2%**, sd 2.8 over 6 runs, steps 3,262,
tokens 6,049,158) — **+10.8 tasks**, with every 0.0.4 run above every 0.0.3 run.
The gain is 0.0.4's deterministic-output gate, which blocks writing a derived
value that was never computed. GigaChat 3 Ultra / Pro / Lightning have **not**
been re-measured on 0.0.4, so their rows stay as published rather than mixing two
profile versions in one table.

On weaker models the same gate costs rather than pays: on the GigaChat-3.1-10B
reasoning line it drives the agent to retry the blocked write until the graph
budget is gone (−14.5 pp, step-limit hits 3.6% → 33.6%). Pin `0.0.3` if your
model cannot act on a tool-level refusal; see
[deepagents-gigachat#7](https://github.com/ai-forever/deepagents-gigachat/issues/7).

Public landing page: <https://ai-forever.github.io/harness-bench-fast/>

| Harness | Profile | Model | Result | % | Steps | Tokens |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Kimi CLI | — | Kimi K3 | 390/391 | 99.7% | — | — |
| Pi | — | DeepSeek V4 Flash 0731 (high) | 387/391 | 99.0% | 1,694 | 10,004,355 |
| Claude Code CLI | — | Claude Haiku 4.5 | 380/391 | 97.2% | 1,645 | 176,430,286 |
| opencode | — | GLM-5.2 (self-hosted) | 362/391 | 92.6% | — | — |
| deepagents | GigaChat | GigaChat 3.5 | 340/391 | 87.0% | 3,316 | 4,319,421 |
| deepagents | GigaChat | GigaChat 3 Ultra | 340/391 | 87.0% | 3,258 | 4,415,043 |
| deepagents | none | DeepSeek V4 Flash | 320/391 | 81.8% | 5,048 | 61,715,547 |
| deepagents | none | GigaChat 3 Ultra | 312/391 | 79.8% | 3,591 | 7,994,643 |
| deepagents | none | GigaChat 3.5 | 302/391 | 77.2% | 3,542 | 7,371,576 |
| deepagents | none | Qwen3 Coder 30B-A3B | 284/391 | 72.6% | 5,467 | 90,234,558 |
| deepagents | GigaChat | GigaChat 3 Pro | 241/391 | 61.6% | 2,993 | 3,975,672 |
| deepagents | none | GPT-OSS-20B | 193/391 | 49.4% | 2,727 | 29,137,983 |
| deepagents | none | GPT-OSS-120B | 186/391 | 47.6% | 2,193 | 23,831,283 |
| deepagents | GigaChat | GigaChat 3 Lightning | 178/391 | 45.5% | 2,520 | 2,275,821 |

Three notes on reading the table. The GigaChat profile is worth 7-10 points and
roughly halves the token spend, which the two profiled/unprofiled GigaChat pairs
show directly. A larger model is not automatically a more capable agent:
GPT-OSS-120B places below its own 20B sibling because it frequently answers in
prose instead of calling a tool, scoring 0/15 on the VCS wave and 2/20 on
CLI-composition while remaining competitive on single-shot waves. And each row
is a single full run with non-deterministic sampling: repeat runs of the same
mid-scale setup differ by ±1-2 pp, so rows within ~3 points of each other are
a tie, not a ranking — models near the ceiling are much more stable.

<details>
<summary>Earlier results on task-set v0.13.0 (351 tasks) — not comparable</summary>

The v0.16.0 audit changed task semantics across the set, so these scores cannot
be compared with the table above. Kept for reference only. The
`deepagents + GigaChat profile / GigaChat 3.5` row averages 3 independent full
runs (317, 311, 308 passed), with Steps/Tokens as the mean of per-run sums.

| Harness | Profile | Model | Result | % | Steps | Tokens |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Claude Code CLI | — | Claude Opus 4.8 | 351/351 | 100.0% | — | — |
| deepagents | none | grok-4.5 | 346/351 | 98.6% | 3,164 | 30,776,418 |
| Claude Code CLI | — | Claude Sonnet 4.6 | 341/351 | 97.2% | — | — |
| Claude Code CLI | — | Claude Haiku 4.5 | 340/351 | 96.9% | — | — |
| deepagents | none | GLM-5.2 | 340/351 | 96.9% | 3,966 | 41,664,423 |
| deepagents | none | DeepSeek V4 Pro | 339/351 | 96.6% | 4,014 | 44,552,076 |
| deepagents | none | GLM-5.1 | 335/351 | 95.4% | 3,802 | 39,320,469 |
| deepagents | OpenAI | GPT-5.6 Luna | 330/351 | 94.0% | 4,392 | 40,181,133 |
| deepagents | Anthropic | Claude Haiku 4.5 | 328/351 | 93.4% | 3,682 | 50,549,085 |
| deepagents | none | Qwen 3.7 Max | 326/351 | 92.9% | 4,154 | 48,563,241 |
| deepagents | none | DeepSeek V3.2 | 326/351 | 92.9% | 6,413 | 98,708,199 |
| deepagents | none | Qwen 3.6 Flash | 325/351 | 92.6% | 4,334 | 49,387,938 |
| deepagents | GigaChat | GigaChat 3.5 | 312/351 | 88.9% | 2,887 | 4,715,017 |
| deepagents | none | DeepSeek V4 Flash | 310/351 | 88.3% | 4,082 | 46,732,794 |
| deepagents | GigaChat | GigaChat 3 Ultra | 303/351 | 86.3% | 2,776 | 3,424,473 |
| deepagents | none | GPT-4.1 | 300/351 | 85.5% | 3,382 | 34,199,277 |
| opencode (gpt2giga) | — | GigaChat 3.5 | 298/351 | 84.9% | — | — |
| deepagents | GigaChat | GigaChat 2 Max | 292/351 | 83.2% | 2,714 | 3,209,751 |
| deepagents | none | Qwen 3.5 Flash | 288/351 | 82.1% | 3,507 | 44,553,189 |
| deepagents | none | GigaChat 3.5 | 287/351 | 81.8% | 3,374 | 8,486,826 |
| deepagents | none | MiniMax M2.7 | 282/351 | 80.3% | 3,387 | 38,231,538 |
| deepagents | none | Qwen3-Coder-30B-A3B | 258/351 | 73.5% | 3,849 | 57,997,536 |
| deepagents | GigaChat | GigaChat 3 Pro | 241/351 | 68.7% | 2,823 | 2,400,507 |
| deepagents | none | yandex/gpt5.1-pro | 220/351 | 62.7% | 4,030 | 42,623,784 |
| deepagents | none | GPT-OSS-120B | 185/351 | 52.7% | 2,054 | 22,079,085 |
| deepagents | none | yandex/gpt5-pro | 180/351 | 51.3% | 2,407 | 20,845,269 |
| deepagents | GigaChat | GigaChat 3 Lightning | 179/351 | 51.0% | 2,232 | 1,739,949 |
| deepagents | none | Llama 4 Maverick | 57/351 | 16.2% | 1,391 | 17,065,878 |
| deepagents | none | yandex/gpt5-lite | 37/351 | 10.5% | 1,666 | 118,843,500 |

</details>

A self-contained **411-task agent benchmark** (`task-set v0.17.0`) for evaluating LLM-backed
coding agents on file-operation work: create / edit / refactor source
files, transform CSV / JSON / JSONL / XLSX, run pytest, search across a
project tree, write and use `MEMORY.md` per repo conventions, and chain
all of that into multi-step pipelines.

This benchmark is part of the
[`GigaChain`](https://github.com/ai-forever/gigachain) project.

Every task is **mechanically verified** — no LLM-as-judge. Verifiers use
exact content checks where byte-for-byte output matters, plus regex
matches, line lists, JSON parsing, importing a Python module and calling
a function, running `pytest`, comparing SQLite query results, comparing
XLSX cells, and so on.

The benchmark exists to track how well an agent harness + model
combination handles **realistic coding tasks** with adversarially-chosen
edge cases (ambiguous prompts dropped; only honest, scoped-to-tool tests
remain). It started life as `harness_bench/` inside
[`deepagents-gigachat`](https://github.com/ai-forever/deepagents-gigachat)
and was extracted into its own repo once it matured.

## Quick start

```bash
# Install the bench in a fresh venv. The `[gigachat]` extra adds the
# GigaChat client; `[openrouter]` adds the OpenAI-compatible client used
# by `run-openrouter`.
uv venv && uv pip install -e ".[gigachat,openrouter]"

# Optional: install the public GigaChat harness profile. Exact v9/v10
# result reproduction may require installing the matching local
# deepagents-gigachat wheel/source instead; after installing a local
# wheel, use `uv run --no-sync ...` so uv does not re-resolve it back
# to the public profile.
uv pip install -e ".[gigachat-profile]"

# List all 411 tasks
uv run python -m harness_bench list

# Any contiguous block of task numbers (inclusive; list, run*, verify-gold,
# export-harbor), e.g. everything except the long-context wave:
uv run python -m harness_bench run-openrouter --model deepseek/deepseek-v4-flash \
    --from-task 1 --to-task 391
# A run over a subset is a partial run: it does not go into the 411-task table.

# Show the benchmark task-set version and revision history
uv run python -m harness_bench version --check

# Run the whole bench against GigaChat (needs GIGACHAT_USER /
# GIGACHAT_PASSWORD in .env or env, plus GIGACHAT_BASE_URL pointing at
# the production gateway):
uv run python -m harness_bench run --concurrency 5

# Run against any OpenAI-compatible OpenRouter model (needs
# OPENROUTER_API_KEY):
uv run python -m harness_bench run-openrouter \
    --model deepseek/deepseek-v4-flash --concurrency 5

# Internal OpenAI-compatible gateways can use password auth instead of a
# static API key. The runner fetches and refreshes a bearer token without
# printing it:
# OPENROUTER_USE_INTERNAL_TAGME=1  # local shortcut for the ignored tagme example
# OPENROUTER_BASE_URL=https://gateway.example/x/ai/llm/v1
# OPENROUTER_AUTH_URL=https://gateway.example/auth/realms/.../token
# OPENROUTER_AUTH_USERNAME=...
# OPENROUTER_AUTH_PASSWORD=...
# OPENROUTER_AUTH_CLIENT_ID=api
# OPENROUTER_AUTH_VERIFY_TLS=false  # only for private gateways that need curl -k
uv run python -m harness_bench run-openrouter \
    --model gpt-4.1-nano --concurrency 5
# run-openrouter retries transient HTTP/timeout/transport model errors up to
# 5 total attempts per task before counting them as task failures. Override
# with --transient-attempts if needed.
# Add --forward-reasoning-history to replay a reasoning model's own thoughts
# back to it across agent turns. Providers spell the trace differently
# (`reasoning_content` on vLLM/SGLang, `reasoning` on OpenRouter-style
# gateways); both are captured and echoed back under the key they arrived on.
# By default the trace is kept only in the AIMessage metadata and omitted from
# subsequent requests, so runs stay comparable with previously published rows.
#
# Whether replayed thoughts help depends on the chat template, and forwarding
# grows the prompt on every turn: on an internal SGLang reasoning stand it was
# worth +9.6 pp of pass rate for +35% tokens, while most gateway models ignore
# a replayed trace entirely. Treat it as part of the run configuration and
# never compare runs that differ in it.

# Run stock deepagents + GigaChat while bypassing the GigaChat harness
# profile even if deepagents-gigachat is installed.
uv run python -m harness_bench run-pure --concurrency 5

# Drive an external CLI agent (Claude Code, etc.). Example with
# Anthropic's free-code CLI:
#
# IMPORTANT: Claude-Code-style CLIs (Claude Code `claude`, `free-code`,
# OpenClaude `openclaude`, …) ship a built-in host-side "auto-memory"
# feature. During a run it reframes the memory-discipline tasks
# (`tasks_memory.py`, 222-253) toward its own ~/.claude memory store /
# index format instead of writing the literal workspace `MEMORY.md` and
# deliverables the strict verifiers expect, which silently corrupts that
# wave (e.g. Claude Sonnet 4.6 scored 20/32 with it on vs 31/32 off).
# ALWAYS set CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 when benchmarking these
# CLIs so the memory wave is scored fairly. This is targeted (unlike
# `--bare`, it does not break OAuth/keychain auth).
CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 \
uv run python -m harness_bench run-cli \
    --cli-command 'free-code -p --model haiku --dangerously-skip-permissions' \
    --concurrency 5

# Runner JSON writes best-effort per-task effort metrics:
# agent_steps / agent_tool_calls / agent_shell_commands / agent_events,
# plus agent_llm_calls / agent_input_tokens / agent_output_tokens /
# agent_total_tokens when the backend exposes usage metadata. Codex, Claude
# Code, and Gemini CLI runs auto-enable JSON/stream-json output so those metrics
# can be read from machine-readable events. The top-level JSON summary also
# includes `steps` and `tokens` totals for README-style result tables.
# JSON checkpointing is enabled by default for run commands. A fresh run writes
# to `jobs/<timestamp>.json`; pass `--json-output results.json` to use
# `jobs/results.json`, or pass an explicit path. If the JSON file already
# exists, completed task attempts are loaded from it and skipped so the run
# continues from the checkpoint. JSON reports also include the command that
# launched the run. Ctrl-C writes `run_status: "interrupted"` plus
# `interrupted_attempts`; those attempts are rerun from clean workspaces on the
# next continue when you rerun with the same JSON path. CLI per-task timeouts
# are also marked `rerun_on_continue` and rerun from clean workspaces on the
# next launch with the same `--json-output` file.
# Add `--rerun-on-fail` when continuing to rerun every saved task attempt whose
# `passed` field is false. Passing attempts stay checkpointed. With `-k N`, only
# the failed attempt numbers are rerun, once per invocation.
uv run python -m harness_bench run-cli \
    --cli-command 'codex exec -m gpt-5.5 --dangerously-bypass-approvals-and-sandbox' \
    --concurrency 5

# Drive mini-SWE-agent through any OpenAI-compatible gateway, including local
# gpt2giga. Install `mini-swe-agent` once for faster startup; the wrapper can
# also fall back to `uvx` when `mini` is not installed. Keep the wrapper path
# absolute because `run-cli` launches the agent from each per-task temp
# workspace. The wrapper runs mini-SWE-agent's non-interactive DefaultAgent
# directly and writes `mini-swe-agent.traj.json`; `run-cli` parses that
# trajectory for agent_steps / agent_llm_calls / token metrics.
uv tool install mini-swe-agent
HB_MINI_SWE_AGENT="$(pwd -P)/scripts/hb-mini-swe-agent"
OPENAI_API_KEY=0 \
OPENAI_API_BASE=http://127.0.0.1:8090/v1 \
MSWEA_MODEL_NAME='openai/GigaChat-3-Ultra' \
MSWEA_COST_TRACKING=ignore_errors \
uv run python -m harness_bench run-cli \
    --cli-command "$HB_MINI_SWE_AGENT" \
    --timeout 900 --concurrency 5 \
    --json-output mini_swe_agent_gigachat_3_ultra.json
# Smoke-test one task first by adding:
#     --task task_01_create_hello --keep

# Repeat every selected task 5 times and print pass@K / pass^K
# percentage metrics for K=1..5. Works for run, run-openrouter,
# run-pure, and run-cli.
uv run python -m harness_bench run-cli \
    --cli-command 'free-code -p --model haiku --dangerously-skip-permissions' \
    --attempts 5 --concurrency 5

# Restrict the repeated-attempt summary to specific K values and write
# the full per-attempt report as JSON.
uv run python -m harness_bench run-cli \
    --cli-command 'free-code -p --model haiku --dangerously-skip-permissions' \
    --attempts 5 --pass@ 1 --pass@ 5 --pass^ 5 \
    --json-output results.json

# Summarize an existing completed JSON run without rerunning tasks. For a
# 313-task run with --attempts 5, this prints Passed attempts, pass@K/pass^K
# for K=1..5, and the per-wave breakdown.
uv run python -m harness_bench summarize-json jobs/results.json

# Drive `opencode` against any OpenAI-compatible deployment (example:
# Qwen3.6-27B-FP8 served by vLLM). Point OPENCODE_CONFIG at a config
# that registers a custom openai-compatible provider, sets the thinking
# sampling (temp=0.6 top_p=0.95 top_k=20) and DISABLES formatter/LSP so
# edits stay byte-exact for the verifiers (otherwise opencode auto-runs
# a formatter and rewrites quotes/whitespace, failing exact checks):
OPENCODE_CONFIG=/path/to/opencode-vllm.json \
uv run python -m harness_bench run-cli \
    --cli-command 'opencode run -m vllm/qwen3.6-27b' \
    --timeout 900 --concurrency 5

# Windows/Git Bash + cmd.exe CLIs with non-ASCII prompts/artifacts: force UTF-8
# in both the outer shell and the Windows console before launching the runner.
# `cmd.exe //c` is intentional for Git Bash/MSYS; keep `cmd /c` inside
# `--cli-command` because that string is parsed by Python's subprocess, not MSYS.
cmd.exe //c "chcp 65001 >nul" && \
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 LANG=C.UTF-8 LC_ALL=C.UTF-8 \
uv run python -m harness_bench run-cli \
    --timeout 600 \
    --cli-command 'cmd /c gigacode --approval-mode=auto-edit' \
    --task task_05_greet --task task_35_remove_blank_lines

# Verify the gold solutions without calling any model. Useful when
# adding a new task — confirms the verifier accepts a hand-written
# "perfect" solution.
uv run python -m harness_bench verify-gold

# Direct no-Docker workspace checks for one task. This is the same
# verifier/oracle surface used by the Harbor export.
uv run python -m harness_bench verify-task \
    --task task_06_toggle_debug --workspace /path/to/workspace
uv run python -m harness_bench apply-gold \
    --task task_06_toggle_debug --workspace /path/to/workspace
```

`.env` at the repo root is auto-loaded by every runner.

## What's inside

### Tasks (411 total, task-set v0.17.0)

| Module | Range | Wave |
| --- | --- | --- |
| `tasks.py` | 1–30 | core file ops (create, edit, count, sort, find) plus the `ALL_TASKS` registry |
| `tasks_extra.py` | 31–60 | multi-file refactors, dedupe, log filtering, CSV ↔ markdown |
| `tasks_more.py` | 61–100 | `.env` edits, nested JSON, dataclasses, regex extraction, INI/TOML/YAML stubs, CSV row splitting |
| `tasks_hard.py` | 101–150 | CSV / XLSX / SQLite aggregates, JSONL, Python impl + pytest, multi-file `grep`, Apache log parsing |
| `tasks_extreme.py` | 151–205 | composite pipelines, archives, project-wide refactors, algorithms with pytest, statistics, XML / markdown, three-way joins |
| `tasks_diagnostic.py` | 206–221 | paid-revenue reconciliation, inventory anomalies, pricing-API migration, latency reconstruction, tar+hash manifests, interval merge, config precedence, markdown link audit, data-quality reports, TODO/FIXME triage, category rollups, email extraction, runtime config, SQL leaderboards, import migrations, log-level summaries |
| `tasks_memory.py` | 222–253 | memory discipline: read / write / forget / refuse facts in `MEMORY.md` along with the auxiliary deliverable (LICENSE, `requirements-dev.txt`, `bio.txt`, `profile.json`, …). Exercises agent memory rather than file I/O. |
| `tasks_agentic.py` | 254–298 | benchmark-like synthetic agentic wave: Terminal-Bench-like terminal workflows (logs, process tables, Makefile plans, checksums, permission audits), tau-like policy-bound action decisions (airline, retail, banking, clinic, etc.), and SWE-bench-like pytest bug-fix tasks. |
| `tasks_vcs.py` | 299–313 | version-control work: Git merge-conflict resolution (ours/theirs/both/manual, diff3 base sections, multi-hunk, multi-file), unified-diff apply/revert, unresolved-conflict detection, plus multi-file/multi-step workflows (scaled rename refactors, module split, ordered patch stacks, manifest-driven resolution, config deep-merge). |
| `tasks_skills.py` | 314–330 | skill-discriminator wave: fictional brand/style guides, internal codebooks and policies, bespoke fixed formats, distractor/selection/negative-control skill axes, code-skill creation/repair, fictional DSL/protocol/library specs, spreadsheet reconciliation, and ArcFlux calculation methods. |
| `tasks_adversarial.py` | 331–351 | adversarial/robustness wave: the agent must diagnose and work around a hostile environment — broken Python versions and imports, unreadable/mis-encoded/permission-locked files, instructions that contradict the environment, broken build commands and skills, and a ~100 MB log that must be streamed rather than read whole. |
| `tasks_tbench_lite.py` | 352–371 | calibrated Terminal-Bench-inspired workflows: multi-source joins, event reconstruction, parsers, config precedence, conflict resolution, package refactors, SQLite migration, deterministic manifests, and retry-aware aggregation. |
| `tasks_cli.py` | 372–391 | CLI-composition wave. Thirteen tasks drive bespoke per-task tools (`logq`, `pktool`, `xtab`, `cfgctl`, `depwalk`, `slicer`) built so that reading `--help` is unavoidable: the surface is deliberately unconventional (a leading verb, `--src`/`--cap`/`--map`, mini-languages like `--span LO..HI` and `--pick level=ERROR,WARN`, `--shape` not `--format`), so a guessed invocation exits non-zero — and the semantics that decide the answer (exclusive bounds, nearest-rank percentiles, margins before normalisation, corrupt-record policy) appear only in the `--help` epilog. Two read binary or fixed-width payloads. Seven exercise POSIX tools (multi-key `sort`, `join -1/-2/-a/-e/-o`, `comm`, `grep -oE` with `uniq -c`, `find` predicates with `xargs -0`, `awk`, `sed` ranges): the agent writes `solve.sh` and the verifier deletes the artifact, runs the script, and rejects general-purpose interpreters. **Requires `bash` on `PATH`.** |
| `long_tasks/` | 392–411 | Long-context wave. Twenty tasks that require reading a large body of material (a novel, an intranet, parish registers, mail, a codebase, git history, a text adventure, and so on) and that floor each run at 7200 s and 3000 steps, so a full-set run includes them. Select only this wave with `--suite long`. |

Task prompts are in **Russian** — the bench is deliberately bilingual
to keep models honest. The verifiers and gold answers are English / data
only.

### Task-set revisions

Benchmark task-set versions live in `harness_bench/versioning.py` and are
separate from the Python package version. Bump the task-set version when a
task is added, removed, or materially changed; runner-only or documentation
changes do not need a task-set bump.

| Version | Introduced | Added tasks | Total | Notes |
| --- | --- | --- | --- | --- |
| `0.1.0` | 2026-05-13 | 1–200 | 200 | Initial extracted file/code/data benchmark |
| `0.2.0` | 2026-05-19 | 201–221 | 221 | Advanced composites and diagnostic hard tasks |
| `0.3.0` | 2026-05-21 | 222–231 | 231 | Memory-discipline tasks using `AGENTS.md` and `MEMORY.md` |
| `0.4.0` | 2026-06-02 | 232–253 | 253 | Extended memory suite: knowledge update, contradiction resolution, temporal reasoning, abstention, preferences, multi-hop/multi-session |
| `0.5.0` | 2026-06-02 | 254–262 | 262 | Agentic wave of synthetic Terminal-Bench-like, tau-like, and SWE-bench-like tasks |
| `0.6.0` | 2026-06-02 | 263–283 | 283 | Agentic wave expanded to 10 Terminal-Bench-like / 10 tau-like / 10 SWE-bench-like tasks |
| `0.7.0` | 2026-06-02 | 284–298 | 298 | Agentic wave expanded to 15 Terminal-Bench-like / 15 tau-like / 15 SWE-bench-like tasks |
| `0.8.0` | 2026-06-05 | 299–308 | 308 | Version-control tasks: Git merge-conflict resolution, multi-hunk unified-diff apply/revert, unresolved-conflict detection |
| `0.9.0` | 2026-06-05 | 309–313 | 313 | Multi-file / multi-step version-control workflows (rename refactor, module split, patch stack, manifest-driven resolution, config deep-merge) |
| `0.10.0` | 2026-06-30 | 314–330 | 330 | Skill-discriminator wave with fictional skills, codebooks, policies, bespoke formats, selection/distractor axes, code-skill authoring/repair, and ArcFlux methods |
| `0.11.0` | 2026-07-02 | 331–337 | 337 | Adversarial/robustness pilot: Python 2 port, broken build command, Windows-1251 file, permission-locked file, instruction naming a nonexistent file, hardcoded path, skill with missing template |
| `0.13.0` | 2026-07-02 | 338–351 | 351 | Adversarial wave completed: removed-stdlib import, misleading `.python-version`, unneeded uninstallable dependency, `set -e` abort, npm-in-a-Python-project, gzip-masquerade, BOM/NUL log, AGENTS.md wrong layout, wrong tests dir, broken import path, broken package layout, malformed SKILL.md frontmatter, contradictory skills, and a ~100 MB log the agent must stream/grep rather than read whole |
| `0.14.0` | 2026-07-23 | 352–371 | 371 | Calibrated Terminal-Bench-inspired wave with deterministic, offline, gold-verified multi-step tasks |
| `0.15.0` | 2026-07-27 | 372–391 | 391 | CLI-composition wave: bespoke tools (`logq`, `pktool`, `xtab`, `cfgctl`, `depwalk`, `slicer`) with a deliberately unguessable surface, so `--help` must be read before anything runs, plus POSIX pipeline tasks (`sort`, `join`, `comm`, `grep`/`uniq -c`, `find`/`xargs -0`, `awk`, `sed`) whose `solve.sh` the verifier executes |
| `0.16.0` | 2026-07-28 | — | 391 | Audit pass over all 391 tasks: no tasks added or removed, but defects gold-verification cannot see were corrected — tasks winnable without work, prompts whose verifier rejected the work they described, requirements the verifier never checked (notably “do not edit the tests”), and platform/self-pollution issues. **Not score-comparable with v0.15.0.** |
| `0.17.0` | 2026-09-26 | 392–411 | 411 | Long-context wave, promoted from the separate `--suite long` into the scored set. Registry ids are `task_392_*` … `task_411_*`; generator seeds and paraphrase fixtures stay on `long_NN_*`, so the tasks themselves did not change. Each task floors the runner at 7200 s and 3000 steps. **Not score-comparable with v0.16.0.** |

### Infrastructure

| File | Purpose |
| --- | --- |
| `core.py` | `Task` (dataclass) and `VerifyResult`. Supports `setup_callback` / `gold_callback` hooks for binary fixtures (xlsx, sqlite, zip, tar). |
| `verifiers.py` | Helpers for building verifiers: `file_exists`, `file_contains`, `file_lines_equal`, `file_matches_regex`, `json_file_has`, `python_runs`, `python_callable_returns`, `pytest_passes`, `xlsx_cell_equals`, `sqlite_query_returns`, `all_of`, etc. |
| `runner.py` | Runs a task in an isolated `tempfile.TemporaryDirectory` with `LocalShellBackend(virtual_mode=True)` rooted at that directory. Drives GigaChat through `langchain-gigachat`. Optional `--concurrency` via a thread pool. Auto-loads the `deepagents-gigachat` harness profile if installed. |
| `runner_cli.py` | Alternative driver that shells out to an external CLI agent (`free-code`, `claude`, etc.). Default: `free-code -p --model haiku --dangerously-skip-permissions`. Detects Claude-Code-style CLIs and auto-injects workspace `AGENTS.md` via `--append-system-prompt`. |
| `runner_openrouter.py` | Runner for any OpenAI-compatible OpenRouter model via `langchain-openai`. Does **not** apply any harness profile — measures raw `deepagents` defaults against the chosen model. |
| `runner_pure.py` | Stock `deepagents` + GigaChat runner that bypasses `deepagents-gigachat` profile lookup even when that package is installed. Useful as a no-profile baseline, not a direct raw-API baseline. |
| `harbor_export.py` | Additive Harbor export layer. Generates local Harbor task directories from the same Python task registry; does not replace the no-Docker local runners. |
| `__main__.py` | CLI: `list`, `version`, `run`, `run-pure`, `run-cli`, `run-openrouter`, `verify-gold`, `verify-task`, `apply-gold`, `export-harbor`. |

Each task is independent: the runner creates a fresh
`tempfile.TemporaryDirectory`, writes `setup_files` (and optionally
calls `setup_callback` for binary fixtures), then points
`LocalShellBackend` at that directory as its `root_dir`. The agent
file tools are rooted there by `virtual_mode=True`. This is not a
security sandbox: `execute` still spawns a real shell on the host and
the runners inherit environment variables. The benchmark is meant for a
trusted local environment. After the agent stops, the per-task verifier
inspects the workspace.

## Long-context wave (tasks 392–411)

Twenty tasks (`harness_bench/long_tasks/tNN_*.py`, registry ids
`task_392_*` … `task_411_*`) built to push an agent harness through **context
compaction**: each task needs tens to hundreds of agent steps and reading
0.4–1.4M characters of material (novels, intranets, parish registers, e-mail
threads, code bases, git history, binary logs, a text adventure…). They are
part of `ALL_TASKS` as of task-set v0.17.0. `--suite long` (`list`, `run*`,
`verify-gold`) selects only this wave. Every task has a minimum timeout
(7200 s) and recursion limit (3000) that runners apply on top of their own
settings, and is mechanically verified like the rest of the bench. Generator
seeds and the paraphrase fixtures in `_texts/` keep the earlier `long_NN_*`
names, so promoting the wave did not regenerate the workspaces.

Strong models avoid reading whenever a shortcut exists, so the suite is designed
against the shortcuts we observed with Claude Opus 5.5:

- structured data is processed by scripts → the answer depends on natural
  language that has to be read;
- procedurally generated prose collapses to a few hundred templates → texts are
  paraphrased by an LLM and every paraphrase is accepted only when an
  independent LLM, given just the task's rules, recovers the generator's ground
  truth from it (`scripts/rewrite_long_texts.py`, fixtures in
  `harness_bench/long_tasks/_texts/`; a stored paraphrase is used only if its
  draft hash matches, so ground truth always comes from the generator);
- local data can be decoded → the text adventure encrypts each zone with a key
  derived from answers found by reading the previous zone;
- deepagents' general-purpose subagent spreads reading over parallel contexts →
  measure with `--no-subagents`.

```bash
# self-check without a model: untouched fails, gold passes, near misses fail
uv run python scripts/check_long_tasks.py
# deepagents + Opus 5.5, compaction at 128K real tokens, one context
uv run python -m harness_bench run-openrouter --suite long \
    --model anthropic/claude-opus-5.5 --compact-at-tokens 128000 \
    --no-subagents --prompt-cache --concurrency 6
```

Measured 2026-09-25 with deepagents 0.6.12 + Claude Opus 5.5 (`anthropic/claude-opus-5.5`
via an OpenRouter-compatible gateway, reasoning `default`), `--compact-at-tokens 128000
--no-subagents --prompt-cache`. One row per task version; "runs" is how many runs of that
version were made, "compactions" lists the count per run. Every run of every task passed.

The 2026-09-25 runs addressed these same tasks under the previous ids `long_01_*` … `long_20_*`.
The current versions of tasks 398 and 405 were measured 2026-09-29 in the same
configuration through another gateway, with `--isolation none` on macOS; their traces
show no reads outside the task workspace.

| Task | What has to be read | Runs | Compactions | Peak prompt |
| --- | --- | ---: | --- | ---: |
| task_392_manuscript_continuity | 18-chapter novel vs. a character bible | 2 | 6, 5 | 127K |
| task_393_intranet_multihop | 340-page intranet, dated orders | 2 | 1, 7 | 127K |
| task_394_review_annotation | 800 LLM-paraphrased reviews + guidelines | 2 | 3, 5 | 127K |
| task_395_homework_grading | 90 students' code, e-mails, rubric | 1 | 1 | 125K |
| task_396_parish_genealogy | 1,456 parish register records | 2 | 1, 1 | 128K |
| task_397_conference_schedule | 327 paraphrased letters, rules | 1 | 8 | 127K |
| task_398_card_game_engine | 250 cards, errata, partly paraphrased rulings | 1 | 3 | 128K |
| task_399_port_js_library | 4,200-line JS library → Python | 2 | 1, 1 | 127K |
| task_400_sql_dialect_port | 70 legacy queries + dialect manual | 2 | 1, 3 | 119K |
| task_401_review_comments | 158 review threads over a 5K-line project | 2 | 3, 1 | 128K |
| task_402_changelog_semver | 238 commits + 251 paraphrased issue threads | 2 | 1, 3 | 120K |
| task_403_binary_format_reverse | 205 paraphrased developer e-mails, samples | 1 | 8 context drops | 128K |
| task_404_text_adventure | encrypted 91-location quest | 3 | 0, 1, 1 | 128K |
| task_405_paper_reproduce | methods, appendices, paraphrased query log, CSVs | 1 | 6 | 128K |
| task_406_spreadsheet_audit | 17 formula sheets + model spec | 1 | 1 | 113K |
| task_407_courier_routes | 280 paraphrased courier voice notes | 1 | 2 | 123K |
| task_408_legacy_feature | 78-module legacy app + feature spec | 2 | 1, 1 | 125K |
| task_409_procurement_datasheets | 183 datasheets, price lists, 45 requests | 2 | 1, 1 | 124K |
| task_410_recipe_nutrition | 155 recipes, measures, nutrition table | 2 | 1, 1 | 124K |
| task_411_math_solutions_check | 150 step-by-step student solutions | 2 | 3, 3 | 133K |

A task with one compaction and a peak near 128K compacts in some runs and not in
others (task 404 did not in one of three runs).

`run-openrouter` options added for this suite:

- `--compact-at-tokens N` — deepagents summarizes once a prompt reaches N
  **provider tokens** (usage of the last model call plus an estimate of what was
  appended since). deepagents' own counter assumes ~4 characters per token and
  undercounts Russian tool output and tool schemas: on one trace its count was
  55–69K while the real prompt was 125K.
- `--no-subagents` — drop the auto-added general-purpose subagent (`task` tool).
- `--prompt-cache` — ask the gateway to cache the prompt prefix (top-level
  `cache_control`; cuts cost ~20× for Anthropic models, inputs unchanged).
- `OPENROUTER_MAX_RETRIES` — per-request retries for 429/5xx (default 2).
- `HARNESS_BENCH_TRACE_DIR` — dump the full message history of every task.

Result JSON gains `agent_peak_input_tokens`, `agent_compactions` and
`agent_cost_usd` when observable.

## Tool-reflection suite (`--suite reflect`, outside the scored set)

Twenty tasks (`harness_bench/reflect_tasks/tNN_*.py`, ids `reflect_01_*` …
`reflect_20_*`) in which the agent works a small stateful service through a closed
client in `tools/<name>`, documented in `docs/<name>.md`. The service deviates from its
documentation in ways that show up only in its responses: an ambiguous refusal for
correct arguments (an order below a supplier minimum, an over-limit charge), an `"ok"`
that did less than asked (a partial batch, a capped booking, a backordered line), a
gateway timeout that did apply the write, an undocumented field or hint (`next_cursor`,
`quota_remaining`, `--consistent`). Each task also has a mid-course surprise that
invalidates the plan after the agent has acted (stock recounted, a card replaced, a fee
tier changing), and a tight success condition. Following the documentation literally
fails; reading what the tool returned and re-planning passes.

The client journals every call with an HMAC chain; the verifier replays the journal
through the same service from its initial state and checks the result, so editing the
service's state files gains nothing. The suite is selected with `--suite reflect` (or by
id) and is **not** part of `ALL_TASKS`: task-set v0.17.0 is unchanged. Each task floors
the run at 1800 s and 400 steps.

```bash
# self-check without a model: untouched fails, gold passes, near misses fail,
# the verifier ignores the state file and rejects an altered journal
uv run python scripts/check_reflect_tasks.py
uv run python -m harness_bench run-openrouter --suite reflect --attempts 2 --model <model>
```

Calibration, 2026-09-30, two attempts per task, `run-openrouter` in the bwrap sandbox
(GigaChat: native `run` with `deepagents-gigachat` 0.0.4); passed attempts out of two:

| task | GigaChat 3.5 xxxB (internal version) | gpt-oss-120b | Qwen3.6-35B-A3B | Claude Haiku 4.5 | DeepSeek V4.1 Flash (high) | GPT-6 Luna (high) | Claude Opus 5.5 |
|---|---:|---:|---:|---:|---:|---:|---:|
| reflect_01_supplier_minimum | 0/2 | 0/2 | 1/2 | 1/2 | 2/2 | 2/2 | 1/2 |
| reflect_02_crm_pagination | 0/2 | 0/2 | 2/2 | 1/2 | 2/2 | 1/2 | 2/2 |
| reflect_03_batch_partial | 0/2 | 0/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| reflect_04_api_throttle | 0/2 | 0/2 | 0/2 | 1/2 | 0/2 | 2/2 | 0/2 |
| reflect_05_pay_units | 0/2 | 0/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| reflect_06_transfer_fees | 1/2 | 0/2 | 1/2 | 2/2 | 0/2 | 2/2 | 0/2 |
| reflect_07_ticket_timeout_dupes | 0/2 | 0/2 | 1/2 | 1/2 | 2/2 | 2/2 | 2/2 |
| reflect_08_storage_async_delete | 0/2 | 0/2 | 0/2 | 1/2 | 2/2 | 2/2 | 2/2 |
| reflect_09_wiki_optimistic_lock | 0/2 | 0/2 | 0/2 | 0/2 | 2/2 | 1/2 | 2/2 |
| reflect_10_catalog_case_search | 0/2 | 0/2 | 2/2 | 0/2 | 2/2 | 2/2 | 2/2 |
| reflect_11_calendar_timezone | 0/2 | 0/2 | 2/2 | 0/2 | 0/2 | 0/2 | 2/2 |
| reflect_12_rooms_duration_cap | 0/2 | 0/2 | 2/2 | 1/2 | 2/2 | 2/2 | 1/2 |
| reflect_13_payments_split_limits | 0/2 | 0/2 | 2/2 | 0/2 | 2/2 | 2/2 | 2/2 |
| reflect_14_shipping_weight_codes | 0/2 | 0/2 | 2/2 | 1/2 | 2/2 | 2/2 | 2/2 |
| reflect_15_kv_eventual | 0/2 | 0/2 | 0/2 | 0/2 | 1/2 | 0/2 | 2/2 |
| reflect_16_report_deprecated_flag | 0/2 | 0/2 | 0/2 | 1/2 | 2/2 | 1/2 | 2/2 |
| reflect_17_geocode_quota | 0/2 | 0/2 | 2/2 | 0/2 | 2/2 | 1/2 | 1/2 |
| reflect_18_mail_bounce | 0/2 | 0/2 | 1/2 | 0/2 | 2/2 | 2/2 | 2/2 |
| reflect_19_orders_backorder | 0/2 | 0/2 | 0/2 | 1/2 | 0/2 | 1/2 | 1/2 |
| reflect_20_admin_elevation | 0/2 | 0/2 | 0/2 | 0/2 | 1/2 | 2/2 | 0/2 |
| **total** | **1/40** | **0/40** | **22/40** | **15/40** | **30/40** | **31/40** | **30/40** |

## Harbor export

The repo can generate a local Harbor dataset without changing the native
benchmark flow:

```bash
# One-task smoke export
uv run python -m harness_bench export-harbor \
    --output harbor_dataset --task task_06_toggle_debug --clean

# Full dataset export
uv run python -m harness_bench export-harbor --output harbor_dataset --clean
```

Each exported Harbor task contains:

- `instruction.md` from the task prompt.
- `environment/Dockerfile` plus a `setup.tar` with the initial workspace.
- `solution/solve.sh` that calls `python -m harness_bench apply-gold`.
- `tests/test.sh` that calls `python -m harness_bench verify-task` and writes
  `/logs/verifier/reward.txt`.

The Docker image contains only task setup and runtime dependencies. The
benchmark registry / gold data is copied into Harbor `solution/` and `tests/`
payloads, so normal agents do not get the gold answers baked into the image.

`run-cli` and `run-openrouter` default to a separate Linux bubblewrap sandbox
for each task agent. Fixture setup and verification stay in the host parent;
the worker sees its workspace, private scratch/home, and allowlisted runtime
files. It cannot read the host benchmark registry, gold data or old workspaces.
Bubblewrap (`bwrap`) must be installed on the runner. `--isolation none` retains
host execution for diagnostics. The native GigaChat `run` / `run-pure` commands
still execute on the host and are not isolated by this change. Docker is only
needed when invoking Harbor's own local runner.

For custom CLI dependencies, pass `--sandbox-manifest runtime.json` containing
`{"runtime_paths": ["/absolute/trusted/runtime"]}`. Allowlist package directories
or exact launcher/config files; do not mount benchmark/data roots. The native
OpenRouter worker automatically mounts its Python environment and four runtime
source files, with installed `harness_bench` task packages masked. Editable
runtime dependencies outside the environment need an explicit manifest entry.
Network access remains available for the configured model endpoint. Set
`OPENROUTER_REASONING_EFFORT=medium` (or another model-supported level) to
explicitly select reasoning effort for the native OpenRouter worker; retain
`--forward-reasoning-history` when measuring reasoning models.
Some models accept `reasoning_effort` together with tools only on the Responses
API (on 2026-09-29 `openai/gpt-6-luna` rejected every level except `none` on Chat
Completions); `--responses-api` switches the worker to it, sends the effort as
`reasoning.effort` and replays the encrypted reasoning items on every turn.

Both isolated runners save each execution under `RESULT.json.artifacts/` (or
`--artifacts-dir DIR`): the prompt, stdout/stderr, execution metadata, and native
traces found before workspace cleanup, plus full regular-file snapshots of the
private scratch/home (including native session databases/configs). Symlink escapes
are skipped. Files of at least 1 MiB are copied into verified SHA256 objects
outside the sandbox and then hardlinked into each archived task; live source
files are never hardlinked. `content_objects.json` records every object reference.
Artifact execution directories have mode 0700. The OpenRouter worker additionally emits
model inputs/outputs, tool events, complete final history and usage. These are
client-side records. For an external session-aware wire recorder, set
`HBF_WIRE_BASE_URL=http://127.0.0.1:PORT/RUN/HARNESS`. Each physical execution
gets its own `.../EXECUTION/v1` endpoint in `HBF_API_BASE` and
`OPENROUTER_BASE_URL`; its metadata records the matching `wire_session`.
The recorder must forward that route to the original model endpoint without
changing request or response bodies. Wire recording is opt-in.

Timeouts remain scored failures when a JSON run resumes. Infrastructure failures
are separately marked and may be retried on the same model; every retry and
explicit `--rerun-on-fail` keeps execution history. There is no automatic PROM
model fallback. Any infrastructure failure makes `measurement_valid=false`,
`pass_rate=null` and a nonzero exit even with `--allow-task-failures`. Token/step
coverage fields distinguish missing telemetry from measured zero. Use a fresh
JSON filename when migrating old unisolated runs; resume refuses to mix them
with the isolated worker settings.

## Results

The results tables are kept at the top of this README: the current task set
v0.17.0 (411 tasks) first, then the earlier v0.16.0 (391 tasks) table, which is not
comparable with it. Only one run per harness + model setup is
listed; superseded and older-task-set runs are not carried over (the last
v0.13.0 table is kept in a collapsed section for reference only).

### Scoring rules

- A task that hits the per-task wall-clock timeout or hangs counts as a
  normal fail: it stays in the denominator and gets no partial credit.
- Transient infrastructure errors are **not** model failures. If a task dies
  on a network failure or an API infrastructure response (HTTP 5xx, 429,
  `529 Overloaded`, connection reset, gateway timeout) rather than on the
  model's own behavior, the task may be rerun and the retried result is
  recorded. Runners may also auto-retry such errors in-flight
  (`run-openrouter` already retries up to 5 attempts per task); a retried
  task is scored the same as any other task.

## Adding a task

1. In one of the task modules (`tasks.py`, `tasks_extra.py`,
   `tasks_more.py`, `tasks_hard.py`, `tasks_extreme.py`,
   `tasks_diagnostic.py`, `tasks_memory.py`, `tasks_skills.py`,
   `tasks_tbench_lite.py`, `tasks_cli.py`, `long_tasks/` — pick the one that fits
   the wave / difficulty) describe a `Task(...)` — id, prompt,
   `setup_files`, `gold_files`, `verifier`.
2. Wire it into the corresponding module's `*_TASKS` list — it gets
   pulled into `ALL_TASKS` automatically via `tasks.py`.
3. Append a new entry in `harness_bench/versioning.py`, bump
   `TASK_SET_VERSION`, and update the total task count. Use a new minor
   version for a new task wave (for example `0.4.0`) and a patch version
   for verifier/gold fixes that change scoring semantics.
4. `uv run python -m harness_bench version --check` — confirms task ids,
   task count, and version metadata agree.
5. `uv run python -m harness_bench verify-gold --task <new_id>` —
   confirms the verifier accepts the gold solution.
6. `uv run python -m harness_bench run --task <new_id>` — sanity-check
   against a live model.

## License

MIT — see [`LICENSE`](LICENSE).
