# harness-bench

## Current Results

Published benchmark results below use the full 391-task set (`task-set v0.16.0`).
`Steps` and `Tokens` are shown when the runner exposes them; `—` means the metric
is absent from the run artifact, not that nothing was spent. Each row is one full
run per harness + model setup. The `Profile` column shows the deepagents harness
profile applied: `GigaChat` = the `deepagents-gigachat` tuning profile,
`Anthropic` = the Anthropic harness profile built into deepagents, `OpenAI` =
the OpenAI harness profile built into deepagents, `none` = stock deepagents
defaults, `—` = not applicable (non-deepagents harnesses).
GigaChat rows are the IFT stand, build `32.9.23.6`.

Public landing page: <https://ai-forever.github.io/harness-bench-fast/>

| Harness | Profile | Model | Result | % | Steps | Tokens |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Kimi CLI | — | Kimi K3 | 390/391 | 99.7% | — | — |
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

Two notes on reading the table. The GigaChat profile is worth 7-10 points and
roughly halves the token spend, which the two profiled/unprofiled GigaChat pairs
show directly. And a larger model is not automatically a more capable agent:
GPT-OSS-120B places below its own 20B sibling because it frequently answers in
prose instead of calling a tool, scoring 0/15 on the VCS wave and 2/20 on
CLI-composition while remaining competitive on single-shot waves.

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

A self-contained **391-task agent benchmark** (`task-set v0.16.0`) for evaluating LLM-backed
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

# List all 391 tasks
uv run python -m harness_bench list

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

### Tasks (391 total, task-set v0.16.0)

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

### Infrastructure

| File | Purpose |
| --- | --- |
| `core.py` | `Task` (dataclass) and `VerifyResult`. Supports `setup_callback` / `gold_callback` hooks for binary fixtures (xlsx, sqlite, zip, tar). |
| `verifiers.py` | Helpers for building verifiers: `file_exists`, `file_contains`, `file_lines_equal`, `file_matches_regex`, `json_file_has`, `python_runs`, `python_callable_returns`, `pytest_passes`, `xlsx_cell_equals`, `sqlite_query_returns`, `all_of`, etc. |
| `runner.py` | Runs a task in an isolated `tempfile.TemporaryDirectory` with `LocalShellBackend(virtual_mode=True)` rooted at that directory. Drives GigaChat through `langchain-gigachat`. Optional `--concurrency` via a thread pool. Auto-loads the `deepagents-gigachat` harness profile if installed. |
| `runner_cli.py` | Alternative driver that shells out to an external CLI agent (`free-code`, `claude`, etc.). Default: `free-code -p --model haiku --dangerously-skip-permissions`. Detects Claude-Code-style CLIs and auto-injects workspace `AGENTS.md` via `--append-system-prompt`. |
| `runner_openrouter.py` | Runner for any OpenAI-compatible OpenRouter model via `langchain-openai`. Does **not** apply any harness profile — measures raw `deepagents` defaults against the chosen model. |
| `runner_pure.py` | Stock `deepagents` + GigaChat runner that bypasses `deepagents-gigachat` profile lookup even when that package is installed. Useful as a no-profile baseline, not a direct raw-API baseline. |
| `chains.py` | Chain composition for chain mode: which tasks run together, in what order, under a `CHAINSET_VERSION` of its own. Deterministic from a fixed seed and pinned by a hash test. |
| `runner_chain.py` | Chain-mode runner: one agent session, N tasks, three delivery modes. Per-task subdirectories, directory fingerprints, two-phase (retention) verification. |
| `harbor_export.py` | Additive Harbor export layer. Generates local Harbor task directories from the same Python task registry; does not replace the no-Docker local runners. |
| `__main__.py` | CLI: `list`, `version`, `run`, `run-pure`, `run-cli`, `run-openrouter`, `chains`, `run-chain`, `verify-gold`, `verify-task`, `apply-gold`, `export-harbor`. |

Each task is independent: the runner creates a fresh
`tempfile.TemporaryDirectory`, writes `setup_files` (and optionally
calls `setup_callback` for binary fixtures), then points
`LocalShellBackend` at that directory as its `root_dir`. The agent
file tools are rooted there by `virtual_mode=True`. This is not a
security sandbox: `execute` still spawns a real shell on the host and
the runners inherit environment variables. The benchmark is meant for a
trusted local environment. After the agent stops, the per-task verifier
inspects the workspace.

## Chain mode (marathon)

Every runner above gives each task a clean session, which answers *can the
model do this task*. Chain mode keeps **one session for a whole chain of
tasks** and answers a different question: can it still work after an hour of
accumulated history, and does its later work break what it already finished.

The two questions separate models far more sharply than the solo bench does,
and a solo score predicts a chain score poorly: a model that solves 87% of the
tasks one at a time can still declare itself finished a dozen tasks into a
342-task session.

```bash
# What chains exist and what is in them
uv run python -m harness_bench chains
uv run python -m harness_bench chains --chain M1 --json

# Smoke the runner on a 5-task chain
uv run python -m harness_bench run-chain --chain S5

# The ten standard 20-task chains
uv run python -m harness_bench run-chain --all-standard --json-output chains.json

# Marathon: every eligible task in one session, via an external CLI agent
uv run python -m harness_bench run-chain --chain ALL --delivery file --keep \
  --chain-timeout 14400 --recursion-limit 5000 \
  --cli-command 'kimi-cli --quiet --yolo -m k3 -p'
```

### Chains

Chains are versioned **separately from the task set** (`CHAINSET_VERSION`,
currently `v1`) and built by a seeded shuffle, so every model runs the same
tasks in the same order. `S5` is a smoke chain; `M1`–`M10` are disjoint
20-task chains; `M1a`/`M1b` are their halves, directly comparable to the full
chain at half the depth; `P2`/`P3` are `M1` rotated, which separates position
effects from task difficulty; `E100` is a 100-task endurance chain; and `ALL`
is every eligible task, with `A50`/`A100`/`A200` as *prefixes* of it, so
position 40 means the same thing in all four.

Chains cover **342 of the 391 tasks**. The memory (222–253) and skills
(314–330) waves are excluded by construction: both are wired to the workspace
root and cannot be scoped to one task's subdirectory. Chain numbers are
therefore **not comparable with the solo leaderboard** above.

Order is shuffled on purpose. The registry runs roughly easy-to-hard, so on a
registry-ordered chain "reached position 60" would mean "did the 60 easiest
tasks" and depth would be confounded with difficulty.

### Delivery modes

| `--delivery` | How the tasks arrive | Retention measured |
| --- | --- | --- |
| `turns` (default) | Each task is a new user message appended to one growing history. deepagents backends only. | **yes** |
| `batch` | All tasks in a single combined prompt, worked through in one invocation. Also works with `--cli-command`, which makes chains possible for agents with no resume API. | no |
| `file` | The task list is written to `TASKS.md` and the agent is told to work through it. | no |

`file` is the only delivery that fits a long chain: 342 prompts come to ~31k
tokens, which does not fit a 32k window *before any work starts*. Paging
through the list is itself part of what long-horizon work measures.

Only `turns` hands control back between tasks, so only `turns` can verify a
task twice — right after its own turn and again at the end of the chain. That
difference is retention: how much finished work the session destroyed later.
`batch` and `file` verify once, at the end, and report retention as **not
measured** rather than as zero breakage.

### Reading the results

- **`reached`** — batch and file deliveries cannot watch the agent walk the
  list, so the runner fingerprints every task directory before the run and
  treats an untouched one as never reached. Without this, "solved 50 of 342"
  would read the same whether the agent worked through everything and failed
  292 or stopped at task 50.
- **`terminated_at_position`** with `termination_reason` — where and why the
  chain ended. `agent stopped before the end of the list` is not a crash: the
  agent decided it was done, which is the most common long-horizon failure and
  must not be confused with running out of context.
- **`retention_broken`** — `null` when the delivery could not measure it.
- **`completed`** — the chain was scored and the numbers are usable. An agent
  that failed, looped or quit early still counts as completed; a chain scored
  while a timed-out agent was **still running** does not, and a resume re-runs
  it instead of publishing it.
- **`scoring_raced`** — a timeout does not stop an in-process agent (Python
  cannot kill the thread), so the runner waits it out and, if it is still
  going, says the verdicts were taken against a moving workspace.

### Flags that matter on long chains

- `--context-window N` — the agent's input budget. Without it the deepagents
  summarization middleware waits for a fixed 170k tokens and a smaller window
  overflows before compaction ever triggers. Note the middleware counts tokens
  by a 4-chars-per-token heuristic that under-counts non-English text by
  roughly 1.4×, so set this **below** the real window.
- `--recursion-limit N` — the step cap. The default (80 per turn, or
  `min(1200, 60 × length)` for a whole batched chain) starves a long marathon;
  budget several steps per task.
- `--chain-timeout N` — whole-chain wall clock. The default is 300s × length,
  which for 342 tasks is over a day.
- `--keep` — batch and file deliveries write their JSON only when the chain
  ends, so on a multi-hour run keep the workspace: verification does not
  mutate it, and a killed run can still be scored offline. The artifact does
  record the plan (task order, subdirectories, command) from the start.

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

Local no-Docker execution remains the canonical development loop:
`run`, `run-cli`, `run-pure`, `run-openrouter`, `verify-gold`,
`verify-task`, and `apply-gold` all run directly on the host. Docker is only
needed when invoking Harbor's own local runner.

## Results

The published results table (full 351-task set, `v0.13.0`) is kept at the top of
this README. Only one run per harness + model setup is listed; superseded and
older-task-set (313-task) runs are not carried over.

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
   `tasks_tbench_lite.py`, `tasks_cli.py` — pick the one that fits
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
