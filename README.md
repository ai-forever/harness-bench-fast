# harness-bench

A self-contained **298-task agent benchmark** (`task-set v0.7.0`) for evaluating LLM-backed
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

# List all 298 tasks
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

# Run stock deepagents + GigaChat while bypassing the GigaChat harness
# profile even if deepagents-gigachat is installed.
uv run python -m harness_bench run-pure --concurrency 5

# Drive an external CLI agent (Claude Code, etc.). Example with
# Anthropic's free-code CLI:
uv run python -m harness_bench run-cli \
    --cli-command 'free-code -p --model haiku --dangerously-skip-permissions' \
    --concurrency 5

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

### Tasks (298 total, task-set v0.7.0)

| Module | Range | Wave |
| --- | --- | --- |
| `tasks.py` | 1–30 | core file ops (create, edit, count, sort, find) plus the `ALL_TASKS` registry |
| `tasks_extra.py` | 31–60 | multi-file refactors, dedupe, log filtering, CSV ↔ markdown |
| `tasks_more.py` | 61–100 | `.env` edits, nested JSON, dataclasses, regex extraction, INI/TOML/YAML stubs, CSV row splitting |
| `tasks_hard.py` | 101–150 | CSV / XLSX / SQLite aggregates, JSONL, Python impl + pytest, multi-file `grep`, Apache log parsing |
| `tasks_extreme.py` | 151–205 | composite pipelines, archives, project-wide refactors, algorithms with pytest, statistics, XML / markdown, three-way joins |
| `tasks_diagnostic.py` | 206–221 | paid-revenue reconciliation, inventory anomalies, pricing-API migration, latency reconstruction, tar+hash manifests, interval merge, config precedence, markdown link audit, data-quality reports, TODO/FIXME triage, category rollups, email extraction, runtime config, SQL leaderboards, import migrations, log-level summaries |
| `tasks_memory.py` | 222–253 | memory discipline: read / write / forget / refuse facts in `MEMORY.md` along with the auxiliary deliverable (LICENSE, `requirements-dev.txt`, `bio.txt`, `profile.json`, …). Exercises agent memory rather than file I/O. |
| `tasks_agentic.py` | 254–298 | benchmark-inspired agentic wave adapted from Terminal-Bench (logs, process tables, Makefile plans, checksums, permission audits), tau2-bench (policy-bound action decisions across airline / retail / banking / clinic / etc.), and SWE-bench (pytest bug-fix tasks). |

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
| `0.5.0` | 2026-06-02 | 254–262 | 262 | Agentic wave adapted from Terminal-Bench, tau2-bench, and SWE-bench patterns |
| `0.6.0` | 2026-06-02 | 263–283 | 283 | Agentic wave expanded to 10 Terminal-Bench / 10 tau2 / 10 SWE-bench tasks |
| `0.7.0` | 2026-06-02 | 284–298 | 298 | Agentic wave expanded to 15 Terminal-Bench / 15 tau2 / 15 SWE-bench tasks |

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

Unless noted, runs use `--concurrency 5`. The newest Claude Code row uses
the 298-task set (`task-set v0.7.0`); older rows in this table use the
231-task set (`task-set v0.3.0`). The `pi-mono` GigaChat row used
`--concurrency 4`; the run completed 230/231 tasks and was stopped after
`task_230_memory_forget_telegram` hung, so that task is counted as a failure
in the table. The Claude Sonnet 4.6 rows added on 2026-06-01 (`pi-mono`,
`hermes`, and `deepagents` 0.6.7, all via OpenRouter) used `--concurrency 8`,
except the `deepagents` 216 row which used `--concurrency 10`; all completed
with no agent exceptions (every miss is a verifier failure, not an infra
error). The `giga_agent` row
(2026-06-02) used `--concurrency 10` with a 1200 s/task timeout and
required `giga-agent[jupyter]` (its `local_jupyter` sandbox); a wrapper
pins the sandbox `--cwd` to each task workspace and feeds the OpenRouter
key via `OPENAI_API_KEY` (the `openai` connector). All giga_agent tasks
shared one `.giga_agent` state dir (LangGraph checkpoints + memories) —
not perfectly isolated per task, though it did not visibly affect the
score. The `hermes` row roots the agent at `$HOME` rather than the
process cwd, so it was run through a wrapper that pins `HOME` to each
task's workspace (and isolates `HERMES_HOME` to a per-task temp dir) —
without that pin hermes writes task output to the real home directory and
scores an unfair 183/231.
Raw run directories are local artifacts and are ignored by git; the table
below is a traceability summary, not a bundled replay log.
GigaChat rows labeled PROM use the active password-auth `.env` setup
(`GIGACHAT_BASE_URL=https://gigachat.sberdevices.ru/v1`); secrets are not
tracked in this repository.

| # | Date | Runner | Model | Harness adapt | Result | % |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-06-03 | `claude` 2.1.160 (Claude Code CLI) | **Claude Opus 4.8** | yes (Claude Code tools + AGENTS.md inject) | **298 / 298** | **100 %** |
| 2 | 2026-05-21 | `free-code` 2.1.119 | **Claude Opus 4.7** | yes (built-in + AGENTS.md inject) | **231 / 231** | **100 %** |
| 3 | 2026-06-01 | `pi-mono` 0.75.3 | **Claude Sonnet 4.6** (via OpenRouter, native tool calls) | yes (pi tools + AGENTS.md discovery) | **229 / 231** | **99.1 %** |
| 4 | 2026-06-01 | `cowork mode` (non-public) | **Claude Sonnet 4.6** | yes (non-public agent harness) | **225 / 231** | **97.4 %** |
| 5 | 2026-06-02 | `opencode` 1.3.7 | **Qwen3.6-27B-FP8** (vLLM, native tool calls) | yes (custom openai-compatible provider, thinking sampling, formatter/LSP off) | **224 / 231** | **97.0 %** |
| 6 | 2026-05-22 | `free-code` 2.1.119 | **Claude Haiku 4.5** | yes (built-in + AGENTS.md inject) | **222 / 231** | **96.1 %** |
| 7 | 2026-05-24 | `ouroboros` | **Claude Sonnet 4.6** (via OpenRouter, native tool calls) | yes (Ouroboros CLI adapter) | **222 / 231** | **96.1 %** |
| 8 | 2026-06-02 | `giga_agent` 0.1.9 (CLI) | **Claude Sonnet 4.6** (via OpenRouter, native tool calls) | yes (LangGraph agent + local Jupyter sandbox) | **219 / 231** | **94.8 %** |
| 9 | 2026-06-01 | `deepagents` 0.6.7 | **Claude Sonnet 4.6** (via OpenRouter, native tool calls) | yes (built-in Sonnet profile + `execute` cwd-relative override) | **216 / 231** | **93.5 %** |
| 10 | 2026-05-24 | `ouroboros` | **Claude Haiku 4.5** (via OpenRouter, native tool calls) | yes (Ouroboros CLI adapter) | **215 / 231** | **93.1 %** |
| 11 | 2026-05-24 | `deepagents` | **Claude Haiku 4.5** (via OpenRouter, `max_tokens=4096`) | no | **209 / 231** | **90.5 %** |
| 12 | 2026-05-22 | `deepagents` | MiniMax-M2 (via OpenRouter) | no | 209 / 231 | 90.5 % |
| 13 | 2026-05-22 | `deepagents` | DeepSeek V3.2-exp (via OpenRouter) | no | 208 / 231 | 90.0 % |
| 14 | 2026-05-22 | `deepagents` | GLM-4.6 (via OpenRouter) | no | 206 / 231 | 89.2 % |
| 15 | 2026-06-01 | `hermes` 0.12.0 | **Claude Sonnet 4.6** (via OpenRouter, native tool calls) | yes (hermes tools + AGENTS.md; HOME pinned to workspace) | **204 / 231** | **88.3 %** |
| 16 | 2026-05-22 | `deepagents` | **GigaChat-3-Ultra** (PROM, deepagents 0.6.3 + langgraph 1.2.1) | **yes (v9 + memory wiring)** | **195 / 231** | **84.4 %** |
| 17 | 2026-05-23 | `deepagents` | **GigaChat-3-Ultra** (PROM, deepagents 0.6.3) | **yes (v10 = v9 + `AgentsMdInjectMiddleware`)** | **194 / 231** | **84.0 %** |
| 18 | 2026-05-24 | `pi-mono` 0.75.3 | GigaChat-3-Ultra (PROM, `@gigachain/pi-gigachat`) | yes (pi tools + AGENTS.md discovery) | 188 / 231 | 81.4 % |
| 19 | 2026-06-02 | `deepagents` | Qwen3.6-27B-FP8 (vLLM, deepagents defaults) | no | 187 / 231 | 81.0 % |
| 20 | 2026-05-22 | `deepagents` | DeepSeek V4 Flash (284B-A13B MoE) | no | 186 / 231 | 80.5 % |
| 21 | 2026-05-25 | `OpenHands SDK` 1.22.1 | GigaChat-3-Ultra (PROM via `gpt2giga`) | yes (SDK CLI wrapper + AGENTS.md/MEMORY.md prompt wiring) | 183 / 231 | 79.2 % |
| 22 | 2026-05-22 | `deepagents` | OpenAI gpt-oss-120b (120B dense) | no | 165 / 231 | 71.4 % |
| 23 | 2026-05-24 | `deepagents` | GigaChat-3-Ultra (PROM) | no (baseline, no profile, `run-pure`) | 164 / 231 | 71.0 % |
| 24 | 2026-05-22 | `deepagents` | Qwen3-235B-A22B-Instruct-2507 | no | 162 / 231 | 70.1 % |
| 25 | 2026-05-25 | `gigacode cli` | unknown | unknown | 151 / 231 | 65.4 % |
| 26 | 2026-05-23 | `ouroboros` | GigaChat-3-Ultra (PROM, native function-calling mode) | no | 136 / 231 | 58.9 % |
| 27 | 2026-05-22 | `deepagents` | GLM-4-32B (32B dense) | no | 76 / 231 | 32.9 % |

The full /200 and /221 task-set history (older runs done before the
bench was extended), plus superseded /231 rows, lives in
[`LEGACY_RESULTS.md`](LEGACY_RESULTS.md), along with a profile-evolution
write-up for the GigaChat harness. Those numbers are **not directly
comparable** across task-set sizes and should only be used to track a
single model across time; superseded /231 rows are kept for traceability.

### What the table shows

- **Closed-source ceiling**: Claude Opus 4.8 through Claude Code now
  saturates the 298-task set, and the older Claude Opus 4.7 run saturated
  the 231-task set. Any number above ~95 % is now bench-limited rather than
  model-limited.
- **opencode + Qwen3.6-27B-FP8**: run through the generic `run-cli`
  driver with a custom OpenAI-compatible vLLM provider (thinking
  sampling `temp=0.6 top_p=0.95 top_k=20`, formatter/LSP disabled so
  edits stay byte-exact) reaches **224 / 231 (97.0 %)** — second only to
  the Opus 4.7 ceiling and ahead of every recorded Claude Haiku/Sonnet
  row. The 7 misses are content-format / missing-output-file tasks plus
  one memory-note refusal (`task_231_memory_refuse_secrets`).
- **Harness contribution on Qwen3.6-27B-FP8**: the same model on stock
  `deepagents` defaults (`run-openrouter`, no profile, no model-specific
  sampling) scores **187 / 231 (81.0 %)**, so the opencode harness
  adaptation is worth **+37 tasks** (187 → 224). The baseline misses
  skew heavily toward tasks the agent ends without writing the output
  file (many sub-2s "missing file" failures) — i.e. on bare defaults the
  model under-uses the file tools, which the opencode setup (and its
  recommended sampling) largely fixes.
- **Top Sonnet 4.6 harnesses**: given a capable agent harness, Sonnet 4.6
  nearly saturates the bench. `pi-mono` 0.75.3 (pi tools + native AGENTS.md
  discovery, Sonnet 4.6 via OpenRouter) scores **229/231** — second overall,
  just 2 tasks behind the Opus 4.7 ceiling and the best Sonnet result on the
  board. The non-public **cowork mode** agent reaches **225/231** and the
  Ouroboros CLI adapter **222/231**. These three set the upper reference for
  what Sonnet 4.6 can do here; the deepagents Sonnet row below shows how far
  a weak (prompt-only) profile falls on the *same* model. On the *same*
  pi-mono harness, swapping GigaChat-3-Ultra (188) for Sonnet 4.6 (229) is
  **+41 tasks** — the model-driven counterpart to that harness gap.
- **Ouroboros + Claude via OpenRouter**: the 2026-05-24 Sonnet run ties
  the recorded Claude-Code-style Haiku row at 222/231 and lands only
  9 tasks behind the Opus ceiling. The Haiku run through the same
  Ouroboros adapter scores 215/231, 7 tasks below both Sonnet/OpenRouter
  and the Claude-Code-style Haiku row. There is no directly comparable
  Claude Code Sonnet /231 row recorded in this table; older /200 Sonnet
  rows in `LEGACY_RESULTS.md` are not directly comparable to the current
  task set.
- **Deepagents + Sonnet 4.6 — a harness bug, now fixed (216/231, row 6)**:
  out of the box deepagents+Sonnet scored only **202/231**, ~25 tasks below the
  other strong Sonnet harnesses on the *same* model. The cause was **not** the
  model or the (prompt-only) built-in Sonnet profile — it was a backend split
  in `LocalShellBackend(virtual_mode=True)`: the **file tools are virtualized**
  (the model writes `/x`, which maps to `<workspace>/x`) but the **`execute`
  shell is not** (it runs a real shell rooted at the absolute workspace path).
  The model does rename/move/delete via the shell using that same `/x`
  convention (`rm /old.txt`, `mv /a /b`), which hits the *real* system root,
  silently no-ops, leaves the original in place, and the agent then burns its
  whole recursion budget retrying (failing tasks ran ~115-205 s vs ~15 s once
  fixed). Traceable evidence: kept workspaces showed both `oldname.txt` and
  `newname.txt` present, and grep output written to
  `<workspace>/private/var/.../count.txt` (the absolute path re-rooted inside
  the workspace).
- **The fix** is a one-line `execute` tool-description override — "the shell's
  cwd *is* the workspace; use cwd-relative paths (`rm old.txt`), never a leading
  `/`" — now applied by `runner_openrouter.py` by default
  (`_EXECUTE_CWD_OVERRIDE`). It lifts the same Sonnet model from **202 →
  216/231**, above `hermes` and just behind the Ouroboros adapter, and is what
  the row 6 figure reflects. (Flipping to `virtual_mode=False` instead is a
  *wash* at 201 — it fixes the shell ops but then the model's `/x` *writes* hit
  the real root and pollute `/tmp` across runs — so the runner keeps
  `virtual_mode=True` and applies the override.) deepagents' built-in Sonnet
  profile is prompt-only and ships no such guidance; the only profile that does
  is `deepagents-gigachat` (GigaChat-tuned). Reproduce with `run-openrouter
  --model anthropic/claude-sonnet-4.6 --harness-profile
  anthropic:claude-sonnet-4-6` (the `execute` fix is automatic).
- **Hermes CLI on Sonnet 4.6**: the `hermes` 0.12.0 agent (Sonnet 4.6 via
  OpenRouter) scores **204/231** — essentially tied with deepagents'
  prompt-only profile and ~25 tasks below the strong pi-mono/cowork/ouroboros
  harnesses on the *same* model. Two caveats shaped the run: (1) hermes roots
  the agent at `$HOME`, so without pinning `HOME` to the per-task workspace it
  writes outputs to the real home directory and scores an unfair **183/231**;
  the **204** figure is with that pin. (2) Even pinned, its dominant miss
  (most of the 27 failures) is "output file missing": on compute/aggregation
  tasks (`sum.txt`, `count.txt`, `merged.csv`, sqlite/CSV rollups) the agent
  often reports the answer conversationally instead of persisting it to the
  requested file — a write-discipline gap, not a reasoning one.
- **giga_agent (CLI) on Sonnet 4.6**: `giga_agent` 0.1.9 — a LangGraph agent
  that executes work in a local Jupyter sandbox — scores **219/231**, the
  4th-best Sonnet harness (behind pi-mono/cowork/ouroboros, ahead of the fixed
  deepagents and hermes). The headline caveat is setup, not capability: its
  `local_jupyter` sandbox needs `giga-agent[jupyter]` (`jupyter_server` +
  `ipykernel`); without those, every shell/Python-sandbox task errors and the
  score collapses to ~16% (only the direct `write_file` tasks survive). With
  the sandbox installed it runs clean (0 agent exceptions, max task 79 s) and
  its 12 misses are ordinary verifier failures — a few output-file/content
  diffs plus the perennially-hard composites (190, 196, 205) that every harness
  trips on. Note: the `cli` one-shot mode used here exists in the local source
  but not the PyPI 0.1.9 wheel.
- **Deepagents + Haiku via OpenRouter**: stock deepagents reaches
  209/231 with `max_tokens=4096`, tying MiniMax-M2 and landing 6 tasks
  behind the Ouroboros Haiku adapter run. Its misses skew toward file
  side effects (rename/delete/missing output files), exact report format,
  and a few composite data tasks.
- **OSS top tier (no adapt)**: MiniMax-M2, DeepSeek V3.2-exp, GLM-4.6
  group at 89-91 % with no model-specific harness profile.
- **GigaChat profile contribution**: the
  [`deepagents-gigachat`](https://github.com/ai-forever/deepagents-gigachat)
  v9/v10 profile adds **+31/+30 tasks** over the latest stock
  deepagents GigaChat baseline (164 → 195/194). On harness-bench it
  places GigaChat-3-Ultra
  above DeepSeek V4 Flash and the open-source mid tier.
- **Pi-mono + GigaChat**: `pi-mono` 0.75.3 via
  `@gigachain/pi-gigachat` reaches 188/231 on PROM. That puts it below
  the GigaChat-specific deepagents profile, but above the stock
  deepagents GigaChat baseline and the Ouroboros/GigaChat native
  function-calling row.
- **Old generations underperform**: GLM-4-32B (a pre-4.6 dense build)
  scores only 33 %, two generations behind GLM-4.6. Agent capability
  scaled much faster than raw quality in the open-source space during
  2025.

## Adding a task

1. In one of the task modules (`tasks.py`, `tasks_extra.py`,
   `tasks_more.py`, `tasks_hard.py`, `tasks_extreme.py`,
   `tasks_diagnostic.py`, `tasks_memory.py` — pick the one that fits
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
