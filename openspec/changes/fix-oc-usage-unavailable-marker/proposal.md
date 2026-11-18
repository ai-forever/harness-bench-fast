# fix-oc-usage-unavailable-marker

## Why

После прогонов OC-раннера (в т.ч. перепрогонов ложных негативов, 2026-11-18) все
успешные задачи получили `agent_usage_available: true`, но **0 токенов**.

Причина — форма конверта `openclaw agent exec --json`. Конверт отдаёт usage одним из
двух видов:
- **доступный** (смоук): `{"usageReport":{"state":"available"},"input":10421,"output":161,
  "cacheRead":20352,"cacheWrite":0,"reasoningTokens":19,"total":30934,...}`;
- **недоступный** (часть путей, напр. retry/continuation): `{"usageReport":{"state":
  "unavailable"},"contextUsage":{"state":"unavailable"}}` — **без** плоских бакетов.

`run_openclaw_bench._reported_usage` возвращает `None` только для пустого/не-dict usage,
а любой **непустой** dict считает доступным. Поэтому «недоступный» конверт
(`usageReport.state == "unavailable"`) проходит как доступный, раннер читает
`usage.get("input", 0)` = 0 → в артефакт пишется `available: true` и **0 токенов**.
Это занижает токены/₽ и маскирует недоступность измерения.

## What Changes

- `run_openclaw_bench._reported_usage`: считать usage **недоступным** (`None`), когда
  конверт не содержит фактических токенов:
  - `usage.usageReport.state == "unavailable"`, **или**
  - отсутствуют плоские `input`/`output`/`total`.
- Сохраняется инвариант issue #449: недоступное измерение пишется как `None`
  (`agent_usage_available: false`), а SHALL NOT как `0`.
- Регресс-тесты на три формы конверта (available-flat, unavailable-nested, пусто).

## Out of scope

- Pi-раннер (там своя логика; см. `fix-bench-token-accounting`).
- Стоимость/тарифы, cache-политика, задвоение — отдельные change-ы.
- Пересчёт уже опубликованных артефактов (backfill) — отдельная задача.

## Impact

- Изменяется только `run_openclaw_bench._reported_usage` (+ тесты).
- Боевой `~/.openclaw` не трогаем. Артефакты новых прогонов получат корректный
  `agent_usage_available`/токены.
- Related: issue #449 (usage omitted → unavailable), change `fix-bench-token-accounting`.
- Workboard/Issue: TBD (создаётся вместе с PR).
