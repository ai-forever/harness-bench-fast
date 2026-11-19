## ADDED Requirements

### Requirement: Сохранение state-dir прогона

`run_openclaw_bench.run_one_task` SHALL передавать `openclaw agent exec`
`--state-dir <dir>` для каждого прогона (предсоздавая каталог), чтобы траектория
оставалась доступной после завершения.

#### Scenario: state-dir проброшен в команду

- **WHEN** раннер запускает задачу
- **THEN** в аргументах `agent exec` SHALL присутствовать
  `--state-dir <существующий каталог>`

### Requirement: Восстановление usage из траектории

Когда конверт не сообщает `usage` (см. `fix-oc-usage-unavailable-marker`),
`run_openclaw_bench.run_one_task` SHALL восстановить usage из
`trajectory_runtime_events` по `sessionId`, выбрав запись с максимальным `total`.

#### Scenario: Конверт без usage — восстановление из траектории

- **WHEN** конверт сообщает `usageReport.state=="unavailable"`, но траектория
  содержит `data.usage` с ненулевым `total`
- **THEN** per-task запись SHALL иметь `agent_usage_available: true`,
  `agent_total_tokens` из траектории и `agent_usage_source: "trajectory"`

### Requirement: Приоритет конверта и поле agent_usage_source

`run_one_task` SHALL записывать `agent_usage_source` = `"envelope"` (usage из
конверта), `"trajectory"` (восстановлено) либо `null` (недоступно). Конверт SHALL
иметь приоритет над траекторией.

#### Scenario: Конверт с usage имеет приоритет

- **WHEN** конверт сообщает валидный flat `usage`
- **THEN** раннер SHALL использовать его, `agent_usage_source` = `"envelope"`, и
  SHALL NOT обращаться к траектории

#### Scenario: Нет источника — честная недоступность

- **WHEN** ни конверт, ни траектория не дают токенов
- **THEN** раннер SHALL записать `agent_usage_available: false`,
  `agent_usage_source: null` и `None`-бакеты (SHALL NOT писать `0`)

### Requirement: Устойчивость чтения траектории

`_trajectory_usage` SHALL не падать при отсутствующем `--state-dir`,
отсутствующей таблице `trajectory_runtime_events` или неразбираемом `event_json`;
в этих случаях он SHALL вернуть `None`.

#### Scenario: Битый/отсутствующий state-dir

- **WHEN** каталог `--state-dir` пуст или sqlite недоступна
- **THEN** `_trajectory_usage` SHALL вернуть `None`, а прогон SHALL продолжиться
  без исключения
