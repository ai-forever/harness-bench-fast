# isolate-bench-openclaw-instance

## Why

Бенч-раннер запускает `openclaw agent exec` против **ambient** конфига оператора
(`~/.openclaw/openclaw.json`) — того же, что обслуживает живой Gateway. Из документов
OpenClaw (`docs/cli/agent.md`): `agent exec` «runs against your ordinary OpenClaw config,
so configured providers, credentials, and agentRuntime harness selection apply exactly as
they do elsewhere», а «plugins continue to resolve from the operator's ordinary plugin
roots».

Следствия для бенча:
1. **Связанность с боевым инстансом**: прогон подхватывает боевые **плагины**
   (`plugins.allow`: telegram, memory-core, memory-wiki, tavily, openrouter, локальный
   telegram-footer-plugin, …), **MCP-серверы** (`mcp.servers`: playwright-mcp, context7,
   sequential-thinking, github) и **скиллы**. Это делает прогон невоспроизводимым и
   машинно-зависимым.
2. **Регрессия истории**: чистый bench-конфиг **выбрасывает**
   `agents.defaults.contextPruning` + `agents.defaults.compaction`, которые есть в
   боевом конфиге. Из-за этого история в `agent exec` не компактится — тело запроса
   растёт до ~2.2 MB / ~443K prompt-токенов (см. диагноз §9), что ведёт к
   `UND_ERR_SOCKET` и `Provider returned an incomplete or malformed tool call`.
3. **Нет фиксации ключа**: `models.providers.*.apiKey` берётся из **secret store**
   боевого инстанса (`{source:"store", id:"CLOUD_RU_API_KEY"}`), а не из выделенного
   бенч-ключа `CLOUD_RU_BENCH_API_KEY` (см. `use-dedicated-bench-account`).

> ⚠️ **Уточнение по диагнозу.** Ранее предполагалось, что источник краха —
> ambient-плагины/MCP (лик `connect`-листенеров). Полный stderr **опроверг** это:
> `MaxListenersExceededWarning` — **teardown-шум**, а не причина; сам сбой —
> `incomplete/malformed tool call` (`kind: incomplete_turn`) на раздутом контексте.
> Поэтому изоляция **не** «лечит» краш; она даёт воспроизводимость и убирает регрессию
> истории (возврат pruning/compaction).

Диагноз: `docs/research/2026-11-17-openclaw-exec-crash-diagnosis.md`. Карточка: `3cae8a52`.

## What Changes

- **`bench/openclaw.bench.json`** — минимальный, изолированный конфиг: только
  провайдер `custom-foundation-models-api-cloud-ru` (baseUrl + модели) и
  `agents.defaults.model`; **без** плагинов, MCP, скиллов, memory, TTS, каналов.
  Ключ — через SecretRef `{source:"env", provider:"default", id:"CLOUD_RU_BENCH_API_KEY"}`
  (значение в репозитории не хранится). Возвращены `agents.defaults.contextPruning` /
  `compaction`; `maxTokens` модели поднят до 32768 (обрезка tool-call → `incomplete_turn`).
- **`run_openclaw_bench.py`** — запускает `openclaw agent exec` с **pinned** конфигом
  (`--config <repo>/bench/openclaw.bench.json`), а SHALL NOT наследовать ambient-конфиг
  оператора; дополнительно прокидывает `CLOUD_RU_BENCH_API_KEY` в окружение вызова.
  Плюс **ретрай задачи** при классифицируемом инфра-крахе (`error.kind ==
  "incomplete_turn"`) — флаг `--retries` (default 2).
- **`bench/README.md`** — назначение, запуск, что изолировано, что осталось общим.
- **Регресс-тест** `tests/test_oc_bench_isolation.py`: команда `agent exec` содержит
  `--config …/bench/openclaw.bench.json`; конфиг-файл парсится и содержит ровно одного
  провайдера и **ноль** секций `plugins`/`mcp`/`skills`; `incomplete_turn` ретраится, а
  обычный провал — нет.
- **OpenSpec-дельта `benchmark-runs`** — ADDED «Изолированный bench-конфиг OC-вызова»,
  «Управление историей», «Лимит вывода модели», «Ретрай при инфра-крахе».

## Capabilities

### New Capabilities

_(нет)_

### Modified Capabilities

- `benchmark-runs`: ADDED «Изолированный bench-конфиг OC-вызова» (pinned config,
  env-ключ, отсутствие ambient-плагинов/MCP), «Управление историей»,
  «Лимит вывода модели», «Ретрай при инфра-крахе».

## Out of scope

- Починка самого `openclaw agent exec` (listener leak / обработка пред-диспатч rejection /
  guardrail Cloud.ru) — продукт OpenClaw, вне репо; фиксируется диагнозом и карточкой.
- **Детекция** краха в артефакте (отдельный change, см. отклонённый
  `detect-oc-harness-crash`) — оставлено на усмотрение, здесь не делается.
- Смена тарифов/моделей, методологии pass/fail.
- Изменение боевого `~/.openclaw/*` (не трогаем).

## Impact

- Новые файлы: `bench/openclaw.bench.json`, `bench/README.md`,
  `tests/test_oc_bench_isolation.py`, `openspec/changes/isolate-bench-openclaw-instance/*`.
- Изменяется: `run_openclaw_bench.py` (аргумент `--config` в вызове `agent exec`).
- Боевой инстанс `~/.openclaw` не изменяется; существующие `results/*.json` не переписываются.
- Links: Workboard `3cae8a52`; диагноз `docs/research/2026-11-17-openclaw-exec-crash-diagnosis.md`.
