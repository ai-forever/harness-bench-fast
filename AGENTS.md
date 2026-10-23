# Repository Instructions

## Benchmark Result Tables

When reporting итоговую таблицу benchmark runs for this repository, use this table format by default:

| harness | model | passed | score | steps | tokens |
|---|---|---:|---:|---:|---:|

Rules:
- Use the canonical public suites by default: frozen Light (391 tasks, IDs 1–391) and Medium (90 tasks: IDs 392–471 plus selected wave 21). Publish an overall suite score only with complete valid coverage. The unchanged 531-task registry/default includes archival controls and is not the public leaderboard denominator. Current task-set membership version is v0.21.3. Preserve historical 80-task Medium scores under their original scope.
- Sort rows by `score` descending.
- Use `passed` as `<passed>/<suite total>` and `score` as a percentage with one decimal place.
- Do not mix task-set versions in one table. Earlier runs are not comparable: v0.14.0 scored 371 tasks, v0.16.0 corrected defects across the 391 of v0.15.0 without changing their number, v0.17.0 adds the long-context wave as tasks 392–411, v0.18.0 adds the tool-reflection wave as tasks 412–431, and v0.19.0 adds wave 19 (feasibility) as tasks 432–471. If more than one version must appear, split the table and name the task-set version in each.
- v0.19.1 changes execution/scoring protocol and clarifies task 410 without adding tasks. Preserve historical v0.19.0 scores and protocols; do not relabel them as v0.19.1. Use a fresh result JSON: resume rejects a different task-set version and wave 19 runtime/contract hash.
- Do not include artifact links or artifact paths in the main table unless explicitly requested.
- Use the human-readable harness and model names, not only the JSON filename.
- Include `steps` and `tokens` from the run JSON when present.
- Publish token usage only for the final retained attempt of each task, whether passed or failed. Exclude superseded infrastructure/recovery attempts from public tables, protocol pages and aggregates; keep all-attempt accounting private.
- If a run artifact does not contain steps or token metrics, show `0` and note that `0` means the metric is absent from the artifact, not that nothing was spent.

## Scoring Rules

- A task interrupted by the per-task timeout or an agent hang counts as a
  normal fail (kept in the full denominator, no "partial/interrupted" note).
  Exception in v0.19.1 wave 19: a timely host-recorded `submit`/`finish` plus
  a passing full verifier earns credit despite a subsequent final-reply/process
  timeout. A workspace file without submission, a late submission or an
  incorrect answer still fails. Do not rescore historical v0.19.0 outcomes.
- Transient infrastructure errors (network failures, HTTP 5xx / 429 /
  `529 Overloaded`, connection resets, gateway timeouts) are not model
  failures. A task that died on such an error must be rerun before the run
  is reported; the retried result replaces the errored one. In-flight
  auto-retries of transient errors by runners are allowed and do not need
  to be disclosed per task.

## Current runtime defaults

- Ordinary, reflection and wave 19 tasks use a 3600-second sanitary guard;
  long-context tasks retain the 7200-second floor. Setup and verification are
  outside the agent execution budget. Formal problem resource budgets are unchanged.
- Agent graph steps have no benchmark-imposed cap by default
  (`--recursion-limit 0`). Explicit finite diagnostic overrides are allowed;
  task-specific floors still raise finite caps. Record these nondefault settings.

## Model Identification

- When recording or reporting any benchmark run, always save not only the
  exact model (id/version/build) but also the reasoning level used, when the
  model or API supports one (e.g. `reasoning_effort`, `thinking` budget,
  effort presets like low/medium/high). If no level was set explicitly,
  record it as `default` — reasoning level materially changes both scores
  and token spend, so a result without it is not reproducible.

## GigaChat Models

- Distinguish production `(PROM)` and `(internal version)` in GigaChat result tables and prose. They can serve different weights, so the label is part of the measurement configuration.
- Public documentation, the landing page and other public materials must use `(internal version)` for internal models and `xxxB` if a size placeholder is needed. Never expose internal stand names, gateway URLs, exact parameter counts or model identifiers that reveal those counts. Keep exact gateway and model identifiers only in private run artifacts for reproducibility.
- Always record the exact model version too. The API returns it in the `model` field of every chat-completion response as `Name:Version` (e.g. `GigaChat-3-Ultra:32.3.18.5`). Capture that string in private run artifacts. In public reports, use the approved model label and build version, since weights change between releases.
- Observed versions (as of 2026-06-22; re-check, they change):
  - `GigaChat-3-Ultra` — `32.3.18.5` (PROM and internal version)
  - `GigaChat-3-Lightning` — `32.4.16.3` (PROM and internal version)

## Wave naming

Name waves by their task-set version: the v0.19.0 addition is **wave 19**, never by its positional index in `TASK_WAVES`. The v0.19.1 protocol revision adds no tasks and remains **wave 19**.

## Wave 21 selection

The current registry contains 531 tasks; `composite-hard` is the selected ten
original IDs 512, 518, 520, 522, 523, 524, 525, 526, 527, 530.
`general-hard` retains forty wave20 controls, and `composite-hard-calibration`
retains all twenty coupled tasks. The canonical Medium suite combines historical IDs 392–471 with those ten,
for 90 tasks. Light is frozen at IDs 1–391. Historical result scopes and scores
remain available; missing current Medium coverage does not earn a zero. Report selected-ten rescoring separately by actual harness,
model, effort, protocol and budget; it is adaptive selection, not a fresh run.
Missing coverage is unmeasured. Never combine graph-step and model-request caps.
