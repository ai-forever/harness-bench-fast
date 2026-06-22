# Repository Instructions

## Benchmark Result Tables

When reporting итоговую таблицу benchmark runs for this repository, use this table format by default:

| harness | model | passed | score | steps | tokens |
|---|---|---:|---:|---:|---:|

Rules:
- Include only completed runs with exactly 313 tasks unless the user asks for partials.
- Sort rows by `score` descending.
- Use `passed` as `<passed>/313` and `score` as a percentage with one decimal place.
- Do not include artifact links or artifact paths in the main table unless explicitly requested.
- Use the human-readable harness and model names, not only the JSON filename.
- Include `steps` and `tokens` from the run JSON when present.
- If a run artifact does not contain steps or token metrics, show `0` and note that `0` means the metric is absent from the artifact, not that nothing was spent.

## GigaChat Models

- For any GigaChat model, always state the stand (gateway) explicitly: PROM (production, `gigachat.sberdevices.ru`) or IFT (`gigachat.ift.sberdevices.ru`). The two stands can serve different model weights, so a GigaChat score is only meaningful with the stand named.
- Use a `(PROM)` or `(IFT)` suffix on the model name in result tables and prose (e.g. `GigaChat-3-Ultra (IFT)`, `GigaChat-3-Ultra PROM`). Never report a bare `GigaChat-...` score without the stand.
- Always record the exact model version too. The API returns it in the `model` field of every chat-completion response as `Name:Version` (e.g. `GigaChat-3-Ultra:32.3.18.5`). Capture that string when running a benchmark and report the version alongside the stand, since weights change between releases.
- Observed versions (as of 2026-06-22; re-check, they change):
  - `GigaChat-3-Ultra` — `32.3.18.5` (PROM and IFT)
  - `GigaChat-3-Lightning` — `32.4.16.3` (PROM and IFT)
