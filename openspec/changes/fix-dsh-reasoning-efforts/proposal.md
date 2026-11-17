# fix-dsh-reasoning-efforts

## Why

`run_dsh_bench.py --thinking medium` (и любой уровень, кроме `off`) падал **мгновенно** на
всех задачах: `UNSUPPORTED_REASONING_EFFORT: pi-ai provider "cloudru" model
"deepseek-ai/DeepSeek-V4.1-Flash" does not support reasoning effort "medium"`.

Причина — **не** ограничение модели и **не** Cloud.ru API, а **неполный патч**:
запись модели в YAML-патче (``build_provider_patch``) объявлялась как
``- id: <bare>`` — **без объявления возможностей reasoning**. pi-ai в этом случае
трактует модель как **нереазонинговую** (`reasoning: false`) и разрешает только `off`:

```js
function resolveModelReasoning(provider, entry, base) {
  const efforts = entry.reasoningEfforts;
  if (efforts === undefined) return { reasoning: base?.reasoning ?? false };  // ← наш случай
  ...
}
```

Проверено пробингом dsh по всем schema-валидным уровням (`off..max`): без
`reasoningEfforts` работает **только `off`**.

Дополнительно всплыл второй дефект патча: после включения reasoning Cloud.ru
ответил `400 INVALID_REQUEST` — `message[0].role must be one of: user, tool, system,
assistant`. pi-ai слал OpenAI-роль `developer`, которую Cloud.ru не принимает.

## What Changes

`run_dsh_bench.run_dsh_bench`: `build_provider_patch` объявляет на записи модели:
- **`reasoningEfforts`** — все валидные уровни dsh (`off, minimal, low, medium, high,
  xhigh, max`); `off` без wire-значения (pi-ai = «поддерживается, ничего не слать»),
  остальные → собственное имя (значение, принимаемое Cloud.ru);
- **`compat.supportsDeveloperRole: false`** — чтобы pi-ai не слал роль `developer`.

`ultra` намеренно не объявляется (не валидный уровень dsh).

## Impact

- `run_dsh_bench.py` (``build_provider_patch``, новый ``DSH_THINKING_LEVELS`` +
  ``_reasoning_efforts_block``).
- `tests/test_runner_dsh_stream_json.py` (+2 регресс-теста).
- Артефакт пилота `results/dsh_*_medium_*` становится возможным (не входит в этот change).
- Боевой `~/.openclaw` не трогается. Секретов в патче нет (только имя env-переменной).

Links: research-ветка `research/dsh-ds41flash-thinking-medium` (PR #29) — где зафиксирован
негативный результат; этот change его опровергает/исправляет.
