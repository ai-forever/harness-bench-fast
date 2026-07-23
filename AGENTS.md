# Repository Instructions

## Benchmark Result Tables

When reporting итоговую таблицу benchmark runs for this repository, use this table format by default:

| harness | model | passed | score | steps | tokens |
|---|---|---:|---:|---:|---:|

Rules:
- Include only completed runs with exactly 371 tasks (current task-set v0.14.0) unless the user asks for partials.
- Sort rows by `score` descending.
- Use `passed` as `<passed>/371` and `score` as a percentage with one decimal place.
- Do not include artifact links or artifact paths in the main table unless explicitly requested.
- Use the human-readable harness and model names, not only the JSON filename.
- Include `steps` and `tokens` from the run JSON when present.
- If a run artifact does not contain steps or token metrics, show `0` and note that `0` means the metric is absent from the artifact, not that nothing was spent.

## Scoring Rules

- A task interrupted by the per-task timeout or an agent hang counts as a
  normal fail (kept in the full denominator, no "partial/interrupted" note).
- Transient infrastructure errors (network failures, HTTP 5xx / 429 /
  `529 Overloaded`, connection resets, gateway timeouts) are not model
  failures. A task that died on such an error must be rerun before the run
  is reported; the retried result replaces the errored one. In-flight
  auto-retries of transient errors by runners are allowed and do not need
  to be disclosed per task.

## Model Identification

- When recording or reporting any benchmark run, always save not only the
  exact model (id/version/build) but also the reasoning level used, when the
  model or API supports one (e.g. `reasoning_effort`, `thinking` budget,
  effort presets like low/medium/high). If no level was set explicitly,
  record it as `default` — reasoning level materially changes both scores
  and token spend, so a result without it is not reproducible.

## GigaChat Models

- For any GigaChat model, always state the stand (gateway) explicitly: PROM (production, `gigachat.sberdevices.ru`) or IFT (`gigachat.ift.sberdevices.ru`). The two stands can serve different model weights, so a GigaChat score is only meaningful with the stand named.
- Use a `(PROM)` or `(IFT)` suffix on the model name in result tables and prose (e.g. `GigaChat-3-Ultra (IFT)`, `GigaChat-3-Ultra PROM`). Never report a bare `GigaChat-...` score without the stand.
- Always record the exact model version too. The API returns it in the `model` field of every chat-completion response as `Name:Version` (e.g. `GigaChat-3-Ultra:32.3.18.5`). Capture that string when running a benchmark and report the version alongside the stand, since weights change between releases.
- Observed versions (as of 2026-06-22; re-check, they change):
  - `GigaChat-3-Ultra` — `32.3.18.5` (PROM and IFT)
  - `GigaChat-3-Lightning` — `32.4.16.3` (PROM and IFT)
