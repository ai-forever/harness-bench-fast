## ADDED Requirements

### Requirement: Классификация доступности usage из конверта OC

`run_openclaw_bench._reported_usage` SHALL возвращать `None` (usage недоступен), когда
конверт `openclaw agent exec --json` не содержит фактических токенов, а именно когда
`usage.usageReport.state == "unavailable"` ИЛИ отсутствуют плоские
`input`/`output`/`total`. При недоступном usage раннер SHALL NOT записывать `0` токенов
и SHALL NOT помечать `agent_usage_available=true`.

#### Scenario: Nested-конверт с unavailable помечается недоступным

- **WHEN** `usage` = `{"usageReport": {"state": "unavailable"}, "contextUsage": {"state": "unavailable"}}`
- **THEN** `_reported_usage` SHALL вернуть `None`, а per-task запись SHALL иметь
  `agent_usage_available: false` и `None`-бакеты (не `0`)

#### Scenario: Доступный конверт с плоскими бакетами читается

- **WHEN** `usage` = `{"usageReport": {"state": "available"}, "input": 10421, "output": 161,
  "cacheRead": 20352, "cacheWrite": 0, "reasoningTokens": 19, "total": 30934}`
- **THEN** `_reported_usage` SHALL вернуть этот dict, а per-task токены SHALL быть
  заполнены фактическими числами

#### Scenario: Пустой usage остаётся недоступным

- **WHEN** `usage` отсутствует, пуст или не является dict
- **THEN** `_reported_usage` SHALL вернуть `None` (инвариант issue #449)
