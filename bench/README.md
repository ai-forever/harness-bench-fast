# bench/ — изолированный OpenClaw-конфиг для бенчмарка

`openclaw.bench.json` — минимальный конфиг, против которого `run_openclaw_bench.py`
запускает `openclaw agent exec`. Назначение: **не** зависеть от боевого
`~/.openclaw/openclaw.json` и **не** подхватывать боевые плагины/MCP/скиллы.

## Что внутри
- `models.providers.custom-foundation-models-api-cloud-ru` — baseUrl + 24 модели.
- `agents.defaults.model.primary` — DeepSeek-V4.1-Flash (дефолт для прогонов).
- `apiKey` — SecretRef `{ source: "env", provider: "default", id: "CLOUD_RU_BENCH_API_KEY" }`.

## Чего нет (намеренно)
`plugins`, `mcp`, `skills`, `channels`, `memory`, `tts` — ноль. Изолирует прогон от
боевых интеграций оператора (telegram, playwright-mcp, github-mcp, memory-wiki, …).

## Запуск
```bash
export CLOUD_RU_BENCH_API_KEY=...        # значение задаёт владелец; в репо не хранится
python3 run_openclaw_bench.py \
  --tasks task_403_binary_format_reverse --thinking off \
  --model custom-foundation-models-api-cloud-ru/deepseek-ai/DeepSeek-V4.1-Flash \
  --timeout 7200
# раннер передаёт --config bench/openclaw.bench.json в `openclaw agent exec`
```
Ручной смоук:
```bash
openclaw agent exec --config "$(pwd)/bench/openclaw.bench.json" \
  --model custom-foundation-models-api-cloud-ru/deepseek-ai/DeepSeek-V4.1-Flash \
  --thinking off --json --timeout 300 --cwd /tmp/smoke "Create smoke_ok.txt containing OK"
```

## Почему так
`agent exec` по умолчанию читает ambient-конфиг оператора и резолвит операторские
плагины (`docs/cli/agent.md`). Пиннинг `--config` + env-ключ даёт чистый, воспроизводимый
инстанс без правок боевого `~/.openclaw`. Диагноз связанного краша:
`docs/research/2026-11-17-openclaw-exec-crash-diagnosis.md`.
