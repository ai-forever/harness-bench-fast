# dsh × DeepSeek-V4.1-Flash: `medium` vs `off` (shot-list, 52 задачи)

Дата: 2026-11-17. Артефакт: `results/dsh_shotlist_ds41flash_medium_20261117.json`.
Фикс, сделавший `medium` возможным: PR #30 (`fix-dsh-reasoning-efforts`).

## Итог

| метрика | `off` (2026-11-11) | `medium` (2026-11-17) |
|---|---|---|
| pass-rate | **51/52 (98.1 %)** | **51/52 (98.1 %)** |
| время | 1143 s (19.1 m) | 1346 s (22.4 m) |
| шаги | 266 (tool-calls 304) | 269 (tool-calls 288) |
| токены | 1 975 401 | 2 032 131 |
| стоимость | ~42.8 ₽ | ~44.6 ₽ |
| timeout | 660 s | 1800 s |
| упавшая задача | `task_263_terminal_du_top_dirs` | `task_263_terminal_du_top_dirs` |

## Выводы

1. **`medium` не меняет результат** относительно `off` на этом наборе: тот же 51/52, та же
   единственная упавшая задача (`task_263_terminal_du_top_dirs`).
2. Цена/время чуть выше (`+7 %` токенов, `+18 %` времени) — ожидаемо из-за reasoning.
   Стоимость осталась почти прежней.
3. Ранее заявленный вывод «dsh не поддерживает medium» **был ошибкой нашего патча**
   (модель объявлялась без `reasoningEfforts`); исправлено в PR #30, после чего `medium`
   (и `high`/`low`/`minimal`) работают.

## Провенанс запуска

```
.venv/bin/python run_dsh_bench.py --shot-list \
  --model deepseek-ai/DeepSeek-V4.1-Flash --thinking medium \
  --concurrency 2 --timeout 1800 --permission-mode workspace-write \
  --json-output results/dsh_shotlist_ds41flash_medium_20261117.json
```
`harness_version 0.2.2-rc.1` · credential `CLOUD_RU_KAP_HERMES` · `workspace-write`.
