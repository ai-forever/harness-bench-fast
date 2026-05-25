# Legacy benchmark results

Historical runs on the older 200-task and 221-task sets. **Not
comparable across set sizes** — the bench grew to 231 tasks on
2026-05-21 (10 new memory-discipline tasks added in
`tasks_memory.py`). In task-set version terms, the 200-task set is
`v0.1.0`, the 221-task set is `v0.2.0`, and the current 231-task set is
`v0.3.0`. Current results live in `README.md`; this file is kept for
traceability only.

The older `pi-mono` / GigaChat row on the 200-task set has been
superseded by the 2026-05-24 `pi-mono` / GigaChat-3-Ultra row in
`README.md`; it remains below only as legacy /200 evidence.

## Superseded runs on the current 231-task set

These rows used the current task-set size but were replaced by newer
same-runner or same-model measurements in `README.md`.

| Date | Runner | Model | Harness adapt | Result | % | Replaced by |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-05-22 | `deepagents` | GigaChat-3-Ultra (PROM) | no (baseline, no profile) | 154 / 231 | 66.7 % | 2026-05-24 `deepagents` / GigaChat-3-Ultra (PROM), no profile: 164 / 231 |

## Runs on the 221-task set (set existed 2026-05-19 → 2026-05-21)

| Date | Runner | Model | Harness adapt | Result | % |
| --- | --- | --- | --- | --- | --- |
| 2026-05-20 | `qwen-code` 0.15.11 | **qwen3-coder-next** (OpenRouter, `-y`) | yes (built-in) | **207 / 221** | **93.7 %** |
| 2026-05-21 | `deepagents` | GigaChat-3-Ultra (PROM, deepagents 0.6.2) | yes (v8) | 189 / 221 | 85.5 % |
| 2026-05-20 | `deepagents` | **GigaChat-3-Ultra:32.3.7.3** (PROM, deepagents 0.6.2) | **yes (v9)** | **188 / 221** | **85.1 %** |
| 2026-05-20 | `deepagents` | GigaChat-3-Ultra:32.3.18.5 (IFT, deepagents 0.6.2) | yes (v9) | 186 / 221 | 84.2 % |
| 2026-05-21 | `deepagents` | GigaChat-3-Ultra (PROM, deepagents 0.6.2 + async `ShellSafetyMiddleware`) | yes (v9) | 185 / 221 | 83.7 % |

## Runs on the 200-task set (set existed 2026-05-13 → 2026-05-19)

| Date | Runner | Model | Harness adapt | Result | % |
| --- | --- | --- | --- | --- | --- |
| 2026-05-13 | `free-code` | **Claude Opus 4.7** | yes (built-in) | **195 / 200** | **97.5 %** |
| 2026-05-15 | `pi-mono` | Claude Haiku 4.5 | yes (built-in) | 190 / 200 | 95.0 % |
| 2026-05-14 | `deepagents` | Claude Opus 4.7 | yes (built-in) | 188 / 200 | 94.0 % |
| 2026-05-13 | `free-code` | Claude Haiku 4.5 | yes (built-in) | 185 / 200 | 92.5 % |
| 2026-05-14 | `deepagents` | Claude Sonnet 4.5 | no | 185 / 200 | 92.5 % |
| 2026-05-14 | `deepagents` | GLM-5.1 | no | 180 / 200 | 90.0 % |
| 2026-05-14 | `pi-mono` | GPT-4.1-mini | ? (run by colleague) | 179 / 200 | 89.5 % |
| 2026-05-19 | `deepagents` | GigaChat-3-Ultra (IFT, deepagents 0.6.2) | yes (v8) | 177 / 200 | 88.5 % |
| 2026-05-13 | `deepagents` | Claude Haiku 4.5 | yes (built-in) | 177 / 200 | 88.5 % |
| 2026-05-14 | `deepagents` | GLM-4.6 | no | 174 / 200 | 87.0 % |
| 2026-05-14 | `deepagents` | Qwen3.5-397B-A17B | no | 172 / 200 | 86.0 % |
| 2026-05-20 | `pi-mono` 0.75.3 | **GigaChat-3-Ultra:32.3.18.5** (IFT) | yes (ext: gigachat 0.1.1) | **170 / 200** | **85.0 %** |
| 2026-05-18 | `deepagents` | GigaChat-3-Ultra (IFT, deepagents 0.5.7) | yes (v4) | 169 / 200 | 84.5 % |
| 2026-05-19 | `deepagents` | GigaChat-3-Ultra (IFT, deepagents 0.6.2) | yes (v7) | 169 / 200 | 84.5 % |
| 2026-05-14 | `deepagents` | GPT-4.1-mini | no | 168 / 200 | 84.0 % |
| 2026-05-14 | `pi-mono` | GPT-4o-mini | ? (run by colleague) | 166 / 200 | 83.0 % |
| 2026-05-15 | `deepagents` | GigaChat-2-Max | yes (v3) | 165 / 200 | 82.5 % |
| 2026-05-14 | `deepagents` | DeepSeek V4 Flash | no | 165 / 200 | 82.5 % |
| 2026-05-14 | `deepagents` | GigaChat-3-Ultra | yes (v3) | 164 / 200 | 82.0 % |
| 2026-05-14 | `deepagents` | Qwen3-Coder-30B-A3B Instruct | no | 163 / 200 | 81.5 % |
| 2026-05-14 | `pi-mono` | GPT-4.1-nano | ? (run by colleague) | 141 / 200 | 70.5 % |
| 2026-05-14 | `deepagents` | GigaChat-3-Pro | yes (v3) | 137 / 200 | 68.5 % |
| 2026-05-13 | `deepagents` | GigaChat-3-Ultra | no | 134 / 200 | 67.0 % |
| 2026-05-15 | `pi-mono` | Llama 3.3 70B Instruct | yes (built-in) | 127 / 200 | 63.5 % |
| 2026-05-14 | `deepagents` | GPT-3.5-turbo | no | 119 / 200 | 59.5 % |
| 2026-05-14 | `deepagents` | GPT-4.1-nano | no | 115 / 200 | 57.5 % |
| 2026-05-13 | `deepagents` | Llama 3.3 70B Instruct | no | 100 / 200 | 50.0 % |
| 2026-05-14 | `deepagents` | Mistral Small 3.2 24B Instruct | no | 94 / 200 | 47.0 % |

## Historical notes (v3 → v9 profile evolution)

These notes describe the evolution of the GigaChat-deepagents profile
during the /200 and /221 era. They no longer apply directly to the
current /231 results, but explain why specific middleware exists.

### v9 (current; pinned to `deepagents` 0.6.x stack)

Extends v8 with two defensive middleware that fire on tasks outside
this bench but are neutral on harness_bench within run-to-run noise:

- **`ShellSafetyMiddleware`** rejects obviously dangerous shell
  patterns (`rm -rf /`, unscoped `chmod 777`, `curl … | sh`, etc.)
  before they reach `execute`, so a user running the plugin against a
  real workspace can't lose data to an over-eager model.
- **`ToolContractMiddleware`** validates each tool-call's arguments
  against the tool's declared schema before invocation and rewrites
  the assistant turn with a corrective system note if the shape is
  off. On other suites this catches arg-name typos that langgraph
  would otherwise surface as `model_node_exc`.

On four back-to-back 200-task runs the score sat at 176, 182, 177,
181 (avg 179, median 179) — a +2 shift over v8's 177 that's at the
edge of the documented ±5 noise band in `EXPERIMENTS_PLAN.md`.
Keeping both middleware because they are clear wins on the broader
internal suite and at worst neutral here.

v9 also added an async `awrap_tool_call` hook to
`ShellSafetyMiddleware` so the safety check fires on langgraph
1.2.x's async tool-runner path (matches the existing sync
`wrap_tool_call`).

### v8

Builds on top of v7's recovery fixes (path-semantics, script-pattern,
LoopBreaker) and adds two new ones found by tracing residual v7
failures:

- **`edit_file` description hardened around the `<line_no>\t` prefix
  leak.** Per-step traces of v7 model-node failures (e.g. on
  `task_30_add_todo`) showed the model copying `read_file` output
  verbatim — including the `     3\t` display prefix — into
  `edit_file.old_string`, which then never matched the actual file
  bytes. The new description opens with an explicit "STRIP the
  '<line_no>\t' prefix" rule, complete with a worked example and a
  reminder that "String not found" errors after a recent read are
  almost always this leak.
- **`LoopBreakerMiddleware` widened + the post-injection 400 fixed.**
  The original loop detector only triggered on three byte-identical
  `(tool, args)` tuples. It now also triggers on three consecutive
  error results from the same tool even when args drift slightly
  (e.g. the model edits the surrounding context but keeps the prefix
  leak), and the nudge calls out the prefix-leak as the most likely
  cause. The nudge itself was switched from `SystemMessage` to
  `HumanMessage`: GigaChat enforces "system message must be the first
  message" at the API layer, so a mid-conversation `SystemMessage`
  produced a hard `400 BadRequest` that langgraph surfaced as
  `During task with name 'model'`. That single bug masked most of
  v7's residual `model_node_exc` failures.

### v7, v6, v4, v3

- **v7** — closest-to-stock pin on `deepagents` 0.6.x; kept the
  upstream toolset (`write_todos`, `task`) unchanged and only layered
  in path/script/loop-breaker fixes.
- **v6** — additionally disabled `TodoListMiddleware` and the
  auto-added general-purpose subagent on the theory that the inflated
  0.6.x descriptions for `write_todos` (3.6 KB) and `task` (6.9 KB)
  were the regression's root cause; re-enabling them later kept PASS
  at 169 and *reduced* recursion-limit fails from 8 to 2, so the
  divergence wasn't worth it.
- **v4** — equivalent pin for `deepagents` 0.5.7.
- **v3** — original expanded-prompt pin.

Without any profile, GigaChat-3-Ultra scored 134/200 on the same
bench. The agent runs with `max_retries=20` in `runner.py` so
transient IFT bursts of 500/403 are ridden out instead of dropping
tasks.
