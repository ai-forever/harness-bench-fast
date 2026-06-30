# harness-bench

## Current Results

Published benchmark results below use the 313-task set (`task-set v0.9.0`). `Steps`
and `Tokens` are shown when the runner exposes them; `—` means the metric is
absent from the run artifact, not that nothing was spent. Each row is the
latest run for that harness + model setup, except GigaChat-3-Ultra PROM
(no profile, mean of 3 runs) and GigaChat-3-Lightning IFT (mean of 9 runs).
GigaChat rows name the stand (PROM = production, IFT) and the model version
returned by the API, since weights differ across stands and releases.

Public landing page: <https://ai-forever.github.io/harness-bench-fast/>

| Harness | Model | Result | % | Steps | Tokens |
| --- | --- | ---: | ---: | ---: | ---: |
| Claude Code CLI | Claude Opus 4.8 | 313/313 | 100.0% | — | — |
| Claude Code CLI | Claude Sonnet 4.6 | 311/313 | 99.4% | — | — |
| Codex CLI | GPT-5.5 | 311/313 | 99.4% | 1,769 | 52,762,732 |
| Claude Code CLI | Claude Haiku 4.5 | 309/313 | 98.7% | — | — |
| openclaude | GLM 5.2 | 309/313 | 98.7% | — | — |
| openclaude | Claude Haiku 4.5 | 306/313 | 97.8% | — | — |
| openclaude | Claude Sonnet 4.6 | 306/313 | 97.8% | — | — |
| deepagents | deepseek/deepseek-v4-pro | 304/313 | 97.1% | 3,331 | 35,792,778 |
| deepagents | qwen/qwen3.7-max | 299/313 | 95.5% | 3,792 | 40,869,831 |
| deepagents | Qwen 3.6 Flash | 284/313 | 90.7% | 3,452 | 39,210,903 |
| deepagents | DeepSeek V4 Flash | 277/313 | 88.5% | 3,920 | 44,332,968 |
| deepagents | GPT-5 Mini | 274/313 | 87.5% | 3,390 | 43,151,046 |
| deepagents + GigaChat profile | GigaChat-3-Ultra (IFT, v32.3.18.5) | 269/313 | 85.9% | 2,432 | 2,297,304 |
| OpenHands | GigaChat-3-Ultra (PROM) | 264/313 | 84.3% | — | — |
| deepagents | GPT-4.1 | 264/313 | 84.3% | 2,852 | 28,581,495 |
| deepagents | GPT-4.1 Mini | 255/313 | 81.5% | 2,470 | 30,506,484 |
| deepagents | Qwen 3.5 Flash | 252/313 | 80.5% | 3,251 | 38,507,310 |
| deepagents + GigaChat profile | GigaChat-2-Max (PROM) | 249/313 | 79.6% | 2,743 | 22,150,602 |
| pi-mono | GigaChat-3-Ultra (PROM) | 248/313 | 79.2% | — | — |
| deepagents | GPT-5 Nano | 240/313 | 76.7% | 3,868 | 50,149,521 |
| deepagents, no profile | GigaChat-3-Ultra PROM (v32.3.18.5) | 204.7/313 | 65.4% | 2,774 | 16,257,658 |
| deepagents + GigaChat profile | GigaChat-3-Pro (PROM) | 204/313 | 65.2% | 2,588 | 5,568,426 |
| deepagents | yandex/gpt5.1-pro | 198/313 | 63.3% | 3,569 | 36,086,058 |
| deepagents + GigaChat profile | GigaChat-3-Lightning (IFT, v32.4.16.3) | 172/313 | 55.0% | 2,116 | 873,123 |
| deepagents | yandex/gpt5-pro | 171/313 | 54.6% | 2,262 | 18,864,729 |
| deepagents | GPT-4.1 Nano | 162/313 | 51.8% | 2,695 | 36,218,469 |
| deepagents | GPT-OSS-120B | 155/313 | 49.5% | 1,815 | 19,550,796 |
| deepagents | GPT-3.5 Turbo | 150/313 | 47.9% | 2,962 | 38,503,644 |
| opencode | GigaChat-3-Ultra (IFT, v32.3.18.5) | 147/313 | 47.0% | — | — |
| OpenHands | yandex/gpt5.1-pro | 140/313 | 44.7% | 1,774 | — |
| deepagents | yandex/gpt5-lite | 41/313 | 13.1% | 1,737 | 95,965,560 |

A self-contained **330-task agent benchmark** (`task-set v0.10.0`) for evaluating LLM-backed
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

# List all 330 tasks
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
# agent_total_tokens when the backend exposes usage metadata. Codex CLI runs
# auto-enable `codex exec --json` so those metrics can be read from JSONL.
# `--json-output` is checkpointed after each completed task, so completed
# results survive a later hang or interrupted run.
uv run python -m harness_bench run-cli \
    --cli-command 'codex exec -m gpt-5.5 --dangerously-bypass-approvals-and-sandbox' \
    --concurrency 5 --json-output results.json

# Repeat every selected task 5 times and print pass@K / pass^K
# task-count metrics for K=1..5. Works for run, run-openrouter,
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

### Tasks (330 total, task-set v0.10.0)

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

The latest published 313-task (`v0.9.0`) results table is kept at the top of this README. Only
the latest run per harness + model setup is listed; superseded and
older-task-set runs are not carried over. New `v0.10.0` / 330-task results should
be added once full reruns are available.

## Adding a task

1. In one of the task modules (`tasks.py`, `tasks_extra.py`,
   `tasks_more.py`, `tasks_hard.py`, `tasks_extreme.py`,
   `tasks_diagnostic.py`, `tasks_memory.py`, `tasks_skills.py` — pick the one that fits
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
