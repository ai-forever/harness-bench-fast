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
