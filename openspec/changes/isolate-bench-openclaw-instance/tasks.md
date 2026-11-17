# Tasks: isolate-bench-openclaw-instance

> OpenSpec: propose → review → apply. Владелец согласовал apply (2026-11-17).

## Диагностика (сделано)
- [x] Понять вызов: `agent exec` читает ambient-конфиг оператора + плагины/MCP
      (`docs/cli/agent.md`)
- [x] Подтвердить, что ambient-конфиг содержит plugins/MCP/skills/memory
- [x] Собрать `bench/openclaw.bench.json` (только Cloud.ru провайдер + модели, env-ключ)
- [x] Смоук изолированного вызова (env-конфиг → provider + usage OK), 2× зелёный
- [x] Решающий тест: OC-нога `task_403` на чистом инстансе → **краш воспроизвёлся** (не plugins/MCP)
- [x] Полный stderr: причина = `incomplete_turn` на раздутом контексте; MaxListeners — teardown-шум
- [x] Вернуть `contextPruning`/`compaction` в bench-конфиг (регрессия истории)
- [x] Тест-2 (clean + pruning/compaction): 1056 s, 38 шагов, usage ЕСТЬ; но `incomplete_turn` остаётся

## Реализация
- [x] `run_openclaw_bench.py`: `--config <repo>/bench/openclaw.bench.json` в argv `agent exec` (`resolve_bench_config`)
- [x] `run_openclaw_bench.py`: `CLOUD_RU_BENCH_API_KEY` попадает в env вызова
- [x] `run_openclaw_bench.py`: ретрай задачи при `error.kind == "incomplete_turn"` (`--retries`, default 1)
- [x] `bench/README.md`: назначение/запуск/что изолировано
- [x] `bench/openclaw.bench.json`: финализирован (Cloud.ru + pruning/compaction; `maxTokens` 32768; без plugins/mcp/skills)

## QA
- [x] RED→GREEN: `tests/test_oc_bench_isolation.py` (argv `--config`; конфиг без plugins/mcp/skills; ретрай)
- [x] `python3 -m pytest tests/test_oc_bench_isolation.py -q` → 7 passed
- [x] `npx -y @fission-ai/openspec@1.16.0 validate isolate-bench-openclaw-instance --strict` → valid
- [ ] Смоук: `agent exec --config bench/openclaw.bench.json …` → провайдер резолвится, usage есть
- [ ] **Решающий-3** по слитому варианту (pinned config + maxTokens 32768 + retry 1): OC-нога
      длинной задачи больше НЕ падает с `incomplete_turn` (или подтверждаем провайдер-баг → эскалация)
- [ ] `npx -y @fission-ai/openspec@1.16.0 validate --all --strict` → 0
- [ ] `python3 scripts/build_fork_results.py --check` → 0

## Архив
- [ ] `openspec archive isolate-bench-openclaw-instance` после зелёного QA
