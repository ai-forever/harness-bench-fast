# Design: fix-dsh-reasoning-efforts

## Диагностика (как нашли)

1. `--thinking medium` → 52/52 fail моментально, `steps=1`, токенов 0.
2. Прямая проба dsh: `UNSUPPORTED_REASONING_EFFORT` — «модель не поддерживает
   reasoning effort medium».
3. Пробинг всех schema-валидных уровней через dsh: без правок работает только `off`.
4. Исходник pi-ai (`dsh-llm-pi-ai/lib/index.js`): `resolveModelReasoning` при
   **отсутствии** `reasoningEfforts` возвращает `{reasoning: base?.reasoning ?? false}`
   → модель объявляется нереазонинговой. Лечится объявлением `reasoningEfforts`.
5. После объявления `reasoningEfforts` всплыл `400 INVALID_REQUEST` (роль `developer`)
   → `compat.supportsDeveloperRole: false` (как в боевом OpenClaw-конфиге).

## Решения

- **Q1 — форма `reasoningEfforts`.** Dict: ключ = уровень, значение = wire-значение.
  `off` — без значения (pi-ai: «поддерживается, ничего не слать»). Остальные →
  собственное имя (`medium: medium`) — ровно то, что принимает Cloud.ru.
- **Q2 — какие уровни.** Все валидные dsh-уровни: `off, minimal, low, medium, high,
  xhigh, max`. `ultra` отсутствует — не валидный уровень dsh (схема `reasoning`).
- **Q3 — compat.** `supportsDeveloperRole: false` — Cloud.ru не принимает роль `developer`.
  Симметрично `compat.supportsDeveloperRole=false` в боевом OC-конфиге.
- **Q4 — где.** Динамически в `build_provider_patch` (модель-агностично; работает для
  любой бенчируемой модели провайдера `cloudru`).

## Альтернативы

- **Правка каталога pi-ai** (заставить считать модель reasonging) — отвергнуто:
  правка стороннего пакета, вне репо; у `build_provider_patch` уже есть механизм патча.
- **Оставить только off** — отвергнуто: цель — прогон на `medium`.

## Проверка (вживую)

- Проба dsh `medium` с фиксом → `EXIT=0`, файл создан, `reason: completed`.
- Регресс: `off` и `high` с фиксом → OK.
- Сквозной пилот через исправленный раннер: 4/4 PASS на `medium`, 114 999 токенов, ~3 ₽.
- Юнит-тесты: `tests/test_runner_dsh_stream_json.py` → 17 passed.
