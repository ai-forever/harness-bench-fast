# Tasks: fix-dsh-reasoning-efforts

## Диагностика (сделано)
- [x] Воспроизвести: `--thinking medium` → 52/52 fail, `UNSUPPORTED_REASONING_EFFORT`
- [x] Пробинг всех уровней dsh: без правок только `off` работает
- [x] Найти причину в исходнике pi-ai: `resolveModelReasoning` без `reasoningEfforts`
- [x] Найти вторую причину: `400 INVALID_REQUEST` из-за роли `developer`

## Реализация
- [x] `build_provider_patch`: объявить `reasoningEfforts` (off..max; `off` без значения)
- [x] `build_provider_patch`: `compat.supportsDeveloperRole: false`
- [x] `DSH_THINKING_LEVELS` + `_reasoning_efforts_block` helper

## QA
- [x] `tests/test_runner_dsh_stream_json.py` +2 теста (reasoningEfforts, compat) → 17 passed
- [x] Проба dsh `medium` с фиксом → completed
- [x] Регресс `off` / `high` → OK
- [x] Сквозной пилот через исправленный раннер: 4/4 PASS на `medium`
- [ ] `npx -y @fission-ai/openspec@1.16.0 validate --all --strict` → 0

## Архив
- [ ] `openspec archive fix-dsh-reasoning-efforts` после merge
