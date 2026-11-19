# recover-oc-usage-from-trajectory

## Why

Когда `openclaw agent exec` завершает прогон с недоступным `usage`
(`usage.{input,output,total}` отсутствуют; `usageReport.state=="unavailable"`),
OC-раннер записывает `agent_usage_available=false` и `None`-бакеты (фикс #36).
Токены при этом **реально потрачены** и лежат в траектории прогона:
`<state-dir>/agents/<agent>/agent/openclaw-agent.sqlite`, таблица
`trajectory_runtime_events` → `event_json.data.usage` для `type` =
`model.completed`/`trace.artifacts` (`{input,output,cacheRead,cacheWrite,
reasoningTokens,total,cost.total}`).

Проблема: раннер **не сохраняет** `--state-dir` (default — временный каталог,
удаляется), поэтому восстановить usage постфактум нельзя. Это давало 0 токенов и
₽-разрыв в учёте (issue #449, #447; карточка 8f99e6e8).

Провайдерская сверка требует, чтобы `usage` был полным. Источник истины по
потраченным токенам — траектория прогона.

## What Changes

1. `run_openclaw_bench.run_one_task`:
   - всегда передаёт `agent exec` **`--state-dir <dir>`** (временный per-task, пока
     прогон жив; при `--keep` — `/tmp/oc_state_<task>`);
   - при недоступном `usage` из конверта **восстанавливает** usage из
     `trajectory_runtime_events` по `sessionId` (максимальный `total`);
   - пишет новое поле **`agent_usage_source`**: `"envelope"` | `"trajectory"` |
     `null`.
2. Новый helper `_trajectory_usage(state_dir, session_id)` — устойчивый к
   отсутствию файла/таблицы/битому JSON.
3. Регресс-тесты на восстановление (синтетическая sqlite), приоритет конверта над
   траекторией, отсутствие источника.

## Out of scope

- Backfill старых артефактов 393/398/431: их `--state-dir` был временным и удалён →
  исторические токены невосстановимы (закрывается провайдерской сверкой, не кодом).
- Pi-раннер, ₽-тарифы, задвоение (см. `fix-bench-token-accounting`).

## Impact

- Изменяется `run_openclaw_bench.py` (+ тесты). Боевой `~/.openclaw` не трогаем.
- Будущие прогоны получат полный usage даже при недоступном конверте.
- Related: issue #449, #447; change `fix-oc-usage-unavailable-marker` (PR #36).
