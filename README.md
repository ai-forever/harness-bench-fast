# harness-bench

A self-contained **231-task agent benchmark** (`task-set v0.3.0`) for evaluating LLM-backed
coding agents on file-operation work: create / edit / refactor source
files, transform CSV / JSON / JSONL / XLSX, run pytest, search across a
project tree, write and use `MEMORY.md` per repo conventions, and chain
all of that into multi-step pipelines.

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

# List all 231 tasks
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
```

`.env` at the repo root is auto-loaded by every runner.

## What's inside

### Tasks (231 total, task-set v0.3.0)

| Module | Range | Wave |
| --- | --- | --- |
| `tasks.py` | 1–30 | core file ops (create, edit, count, sort, find) plus the `ALL_TASKS` registry |
| `tasks_extra.py` | 31–60 | multi-file refactors, dedupe, log filtering, CSV ↔ markdown |
| `tasks_more.py` | 61–100 | `.env` edits, nested JSON, dataclasses, regex extraction, INI/TOML/YAML stubs, CSV row splitting |
| `tasks_hard.py` | 101–150 | CSV / XLSX / SQLite aggregates, JSONL, Python impl + pytest, multi-file `grep`, Apache log parsing |
| `tasks_extreme.py` | 151–205 | composite pipelines, archives, project-wide refactors, algorithms with pytest, statistics, XML / markdown, three-way joins |
| `tasks_diagnostic.py` | 206–221 | paid-revenue reconciliation, inventory anomalies, pricing-API migration, latency reconstruction, tar+hash manifests, interval merge, config precedence, markdown link audit, data-quality reports, TODO/FIXME triage, category rollups, email extraction, runtime config, SQL leaderboards, import migrations, log-level summaries |
| `tasks_memory.py` | 222–231 | memory discipline: read / write / forget / refuse facts in `MEMORY.md` along with the auxiliary deliverable (LICENSE, `requirements-dev.txt`, `bio.txt`, `profile.json`, …). Exercises agent memory rather than file I/O. |

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

### Infrastructure

| File | Purpose |
| --- | --- |
| `core.py` | `Task` (dataclass) and `VerifyResult`. Supports `setup_callback` / `gold_callback` hooks for binary fixtures (xlsx, sqlite, zip, tar). |
| `verifiers.py` | Helpers for building verifiers: `file_exists`, `file_contains`, `file_lines_equal`, `file_matches_regex`, `json_file_has`, `python_runs`, `python_callable_returns`, `pytest_passes`, `xlsx_cell_equals`, `sqlite_query_returns`, `all_of`, etc. |
| `runner.py` | Runs a task in an isolated `tempfile.TemporaryDirectory` with `LocalShellBackend(virtual_mode=True)` rooted at that directory. Drives GigaChat through `langchain-gigachat`. Optional `--concurrency` via a thread pool. Auto-loads the `deepagents-gigachat` harness profile if installed. |
| `runner_cli.py` | Alternative driver that shells out to an external CLI agent (`free-code`, `claude`, etc.). Default: `free-code -p --model haiku --dangerously-skip-permissions`. Detects Claude-Code-style CLIs and auto-injects workspace `AGENTS.md` via `--append-system-prompt`. |
| `runner_openrouter.py` | Runner for any OpenAI-compatible OpenRouter model via `langchain-openai`. Does **not** apply any harness profile — measures raw `deepagents` defaults against the chosen model. |
| `runner_pure.py` | Stock `deepagents` + GigaChat runner that bypasses `deepagents-gigachat` profile lookup even when that package is installed. Useful as a no-profile baseline, not a direct raw-API baseline. |
| `__main__.py` | CLI: `list`, `version`, `run`, `run-pure`, `run-cli`, `run-openrouter`, `verify-gold`. |

Each task is independent: the runner creates a fresh
`tempfile.TemporaryDirectory`, writes `setup_files` (and optionally
calls `setup_callback` for binary fixtures), then points
`LocalShellBackend` at that directory as its `root_dir`. The agent
file tools are rooted there by `virtual_mode=True`. This is not a
security sandbox: `execute` still spawns a real shell on the host and
the runners inherit environment variables. The benchmark is meant for a
trusted local environment. After the agent stops, the per-task verifier
inspects the workspace.

## Results

All runs use `--concurrency 5` on the 231-task set (`task-set v0.3.0`).
Raw run directories are local artifacts and are ignored by git; the table
below is a traceability summary, not a bundled replay log.

| # | Date | Runner | Model | Harness adapt | Result | % |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-05-21 | `free-code` 2.1.119 | **Claude Opus 4.7** | yes (built-in + AGENTS.md inject) | **231 / 231** | **100 %** |
| 2 | 2026-05-22 | `free-code` 2.1.119 | **Claude Haiku 4.5** | yes (built-in + AGENTS.md inject) | **222 / 231** | **96.1 %** |
| 3 | 2026-05-22 | `deepagents` | MiniMax-M2 (via OpenRouter) | no | 209 / 231 | 90.5 % |
| 4 | 2026-05-22 | `deepagents` | DeepSeek V3.2-exp (via OpenRouter) | no | 208 / 231 | 90.0 % |
| 5 | 2026-05-22 | `deepagents` | GLM-4.6 (via OpenRouter) | no | 206 / 231 | 89.2 % |
| 6 | 2026-05-22 | `deepagents` | **GigaChat-3-Ultra** (PROM, deepagents 0.6.3 + langgraph 1.2.1) | **yes (v9 + memory wiring)** | **195 / 231** | **84.4 %** |
| 7 | 2026-05-23 | `deepagents` | **GigaChat-3-Ultra** (PROM, deepagents 0.6.3) | **yes (v10 = v9 + `AgentsMdInjectMiddleware`)** | **194 / 231** | **84.0 %** |
| 8 | 2026-05-22 | `deepagents` | DeepSeek V4 Flash (284B-A13B MoE) | no | 186 / 231 | 80.5 % |
| 9 | 2026-05-22 | `deepagents` | OpenAI gpt-oss-120b (120B dense) | no | 165 / 231 | 71.4 % |
| 10 | 2026-05-22 | `deepagents` | Qwen3-235B-A22B-Instruct-2507 | no | 162 / 231 | 70.1 % |
| 11 | 2026-05-22 | `deepagents` | GigaChat-3-Ultra (PROM) | no (baseline, no profile) | 154 / 231 | 66.7 % |
| 12 | 2026-05-23 | `ouroboros` | GigaChat-3-Ultra (PROM, native function-calling mode) | no | 136 / 231 | 58.9 % |
| 13 | 2026-05-22 | `deepagents` | GLM-4-32B (32B dense) | no | 76 / 231 | 32.9 % |

The full /200 and /221 task-set history (older runs done before the
bench was extended) lives in
[`LEGACY_RESULTS.md`](LEGACY_RESULTS.md), along with a profile-evolution
write-up for the GigaChat harness. Those numbers are **not directly
comparable** to the /231 rows above and should only be used to track a
single model across time.

### What the table shows

- **Closed-source ceiling**: Claude Opus 4.7 and Haiku 4.5 saturate the
  bench (100 % and 96 %). Any number above ~95 % is now bench-limited
  rather than model-limited.
- **OSS top tier (no adapt)**: MiniMax-M2, DeepSeek V3.2-exp, GLM-4.6
  group at 89-91 % with no model-specific harness profile.
- **GigaChat profile contribution**: the
  [`deepagents-gigachat`](https://github.com/ai-forever/deepagents-gigachat)
  v9/v10 profile adds **+41 tasks** over stock deepagents on the same
  model (154 → 195/194). On harness-bench it places GigaChat-3-Ultra
  above DeepSeek V4 Flash and the open-source mid tier.
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
