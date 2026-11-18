# dsh × DeepSeek-V4.1-Flash: полный shot-list на `thinking: max` — 52/52

Дата: 2026-11-18. Артефакт: `results/dsh_shotlist_ds41flash_max_20261118.json`.
Фикс, сделавший уровни > `off` возможными: PR #30 (`fix-dsh-reasoning-efforts`).

## Итог

| профиль | pass-rate | время | шаги | токены | стоимость | упавшие |
|---|---|---|---|---|---|---|
| `off` | 51/52 (98.1 %) | 19.1 m | 266 | 1 975 401 | ~42.8 ₽ | `task_263` |
| `medium` | 51/52 (98.1 %) | 22.4 m | 269 | 2 032 131 | ~44.6 ₽ | `task_263` |
| **`max`** | **52/52 (100.0 %)** | **84.1 m** | **438** | **7 106 700** | **~217 ₽** | — |

`max`: в 1 440 346 / out 461 490 токенов, tool-calls 615, timeout 1800 s.

## Выводы

1. **`max` берёт 52/52** — впервые 100 % на этом наборе для dsh × ds41flash.
   Единственная проблемная на `off`/`medium` задача `task_263_terminal_du_top_dirs`
   **пройдена** (подтверждает отдельные пробы: 3/3 на `max`).
2. Цена/время заметно выше (`×5` токенов, `×3.8` времени к `off`) — платим за
   размышление на длинных агентных задачах (`task_319` 468 s, `task_389` 467 s).
3. **Рекомендация:** `medium` — лучший баланс (тот же 51/52, что `off`, почти та же
   цена); `max` — когда нужен строго максимальный результат (52/52), цена ~217 ₽.

## Провенанс

```
.venv/bin/python run_dsh_bench.py --shot-list \
  --model deepseek-ai/DeepSeek-V4.1-Flash --thinking max \
  --concurrency 2 --timeout 1800 --permission-mode workspace-write
```
`harness_version 0.2.2-rc.1` · credential `CLOUD_RU_KAP_HERMES` · `workspace-write`.
