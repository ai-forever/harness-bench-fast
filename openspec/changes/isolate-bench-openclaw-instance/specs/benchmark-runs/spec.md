## ADDED Requirements

### Requirement: Изолированный bench-конфиг OC-вызова

`run_openclaw_bench.py` SHALL запускать `openclaw agent exec` против **pinned**
bench-конфига (`bench/openclaw.bench.json`) через флаг `--config`, а SHALL NOT
наследовать ambient-конфиг оператора. Bench-конфиг SHALL содержать только провайдера
`custom-foundation-models-api-cloud-ru` (baseUrl + модели) и `agents.defaults.model`, а
SHALL NOT содержать секций `plugins`, `mcp`, `skills`, `channels`, `memory`.

#### Scenario: Команда содержит pinned config

- **WHEN** раннер формирует команду задачи
- **THEN** argv SHALL содержать `--config <repo>/bench/openclaw.bench.json`, а SHALL NOT
  запускаться без явного конфига

#### Scenario: Ambient-плагины и MCP не подхватываются

- **WHEN** bench-конфиг загружается
- **THEN** он SHALL NOT определять `plugins`, `mcp.servers` и `skills`, поэтому боевые
  интеграции оператора SHALL NOT активироваться в прогоне

#### Scenario: Провайдер Cloud.ru резолвится

- **WHEN** прогон идёт с bench-конфигом, а в окружении задан `CLOUD_RU_BENCH_API_KEY`
- **THEN** провайдер `custom-foundation-models-api-cloud-ru` SHALL резолвиться, а модель
  из `--model` — запускаться

#### Scenario: Невалидный/отсутствующий bench-конфиг

- **WHEN** файл bench-конфига отсутствует или не проходит валидацию
- **THEN** прогон SHALL завершаться явной ошибкой, а SHALL NOT тихо откатываться на
  ambient-конфиг оператора

### Requirement: Ключ bench-инстанса через SecretRef окружения

Bench-конфиг SHALL задавать `models.providers.custom-foundation-models-api-cloud-ru.apiKey`
как SecretRef `{source: "env", provider: "default", id: "CLOUD_RU_BENCH_API_KEY"}`, а
SHALL NOT ссылаться на боевой secret store. Раннер SHALL обеспечивать наличие
`CLOUD_RU_BENCH_API_KEY` в окружении вызова `agent exec`.

#### Scenario: Ключ из окружения

- **WHEN** раннер запускает `agent exec` с bench-конфигом
- **THEN** `CLOUD_RU_BENCH_API_KEY` SHALL присутствовать в окружении дочернего процесса

#### Scenario: Значение ключа не хранится в репозитории

- **WHEN** bench-конфиг закоммичен
- **THEN** он SHALL NOT содержать значение ключа — только SecretRef с именем переменной

### Requirement: Настройки управления историей в bench-конфиге

Bench-конфиг SHALL задавать `agents.defaults.contextPruning` и
`agents.defaults.compaction`, чтобы история диалога ограничивалась по объёму. Без них
тело запроса растёт неограниченно (наблюдалось ~2.2 MB / ~443K prompt-токенов),
вызывая `UND_ERR_SOCKET` и `Provider returned an incomplete or malformed tool call`.

#### Scenario: Pruning/compaction присутствуют

- **WHEN** bench-конфиг загружается
- **THEN** `agents.defaults.contextPruning` и `agents.defaults.compaction` SHALL
  присутствовать (зеркалят боевые настройки)

#### Scenario: Запрос не растёт неограниченно

- **WHEN** длинный прогон выполняет много ходов
- **THEN** compaction SHALL удерживать объём активного транскрипта в пределах лимита,
  а размер запроса SHALL NOT расти без ограничения

### Requirement: Лимит вывода модели в bench-конфиге

Bench-конфиг SHALL задавать для бенчируемой модели `maxTokens` не ниже 32768, чтобы
крупные tool-call не обрезались по лимиту вывода и не превращались в
`Provider returned an incomplete or malformed tool call`.

#### Scenario: Достаточный лимит вывода

- **WHEN** bench-конфиг задаёт модель
- **THEN** её `maxTokens` SHALL быть ≥ 32768

### Requirement: Ретрай задачи при инфраструктурном крахе

`run_openclaw_bench.run_one_task` SHALL повторять прогон задачи, когда
`openclaw agent exec` завершается классифицируемым инфраструктурным крахом
(`error.kind == "incomplete_turn"`), до `--retries` раз (по умолчанию 1). Число попыток
SHALL ограничиваться: исчерпав ретраи, раннер SHALL записать задачу как проваленную.

#### Scenario: incomplete_turn ретраится

- **WHEN** первая попытка падает с `error.kind == "incomplete_turn"`, а ретраи доступны
- **THEN** раннер SHALL выполнить задачу ещё раз

#### Scenario: Обычный провал не ретраится

- **WHEN** `agent exec` завершается успешно (exit 0), но верификатор не проходит
- **THEN** раннер SHALL NOT повторять задачу (это провал модели, а не инфра-крах)
