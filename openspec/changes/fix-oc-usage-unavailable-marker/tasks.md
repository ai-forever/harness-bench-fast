# Tasks: fix-oc-usage-unavailable-marker

## Реализация
- [ ] `run_openclaw_bench._reported_usage`: `None` при `usageReport.state=="unavailable"`
      или отсутствии плоских `input`/`output`/`total`
- [ ] Не писать `0` при недоступном usage (сохранить `None`-бакеты)

## QA
- [ ] RED→GREEN: регресс-тесты на 3 формы конверта (available-flat / unavailable-nested / пусто)
- [ ] `python3 -m pytest tests/test_runner_usage_unavailable.py -q` → green
- [ ] `npx -y @fission-ai/openspec@1.16.0 validate fix-oc-usage-unavailable-marker --type change --strict` → valid
- [ ] `npx -y @fission-ai/openspec@1.16.0 validate --all --strict` → 0
- [ ] Смоук: артефакт с доступным конвертом содержит реальные токены; с недоступным —
      `agent_usage_available:false` (не 0)

## Архив
- [ ] `openspec archive fix-oc-usage-unavailable-marker` после зелёного QA
