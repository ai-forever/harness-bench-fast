<!-- Model & harness selection guidance (RU-first).
     Auto-generated block is rebuilt by scripts/build_model_selection.py (--check/--lint). -->
# Model & Harness Selection Guidance — что × чем × когда

Практическое руководство по выбору **харнесса × модели** для задач бенчмарка
`harness-bench-fast`, построенное **только на уже выполненных прогонах** (`results/*.json`).
Для премиум-моделей ₽ = **факт биллинга провайдера**, а не оценка по тарифу.

> **EN mirror:** [`model-selection-guidance.en.md`](model-selection-guidance.en.md).
> Методология и решения — в [`../CONTEXT.md`](../CONTEXT.md) и
> [`adr/0006-model-selection-guidance.md`](adr/0006-model-selection-guidance.md).

## TL;DR

- **Default-выбор для подавляющего большинства задач — DeepSeek-V4.1-Flash.** На Shot List он
  даёт **100%** (OpenClaw) / 98% (DeepSeek Harness) при стоимости порядка **~1 ₽ за решённую
  задачу**.
- **Премиум-модели на текущих данных не окупаются.** Лучший premium (Claude Sonnet 4.5, 98.1%)
  стоит **≈25× дороже** равного по результату DeepSeek. Ни одной floor/boundary-задачи premium
  не закрывает — их берёт DeepSeek.
- **Харнесс влияет на результат не меньше модели**: одна и та же модель на разных харнессах даёт
  разные score/время/шаги. Выбирайте **пару** (harness × model), а не «модель вообще».
- **Чего в данных нет:** `wave21 (hard-coupled)` не измерен вовсе; long-волна покрыта лишь
  частично (6/20); Light-391 — 3 прогона. Такие клетки помечены `unmeasured`, а не «fail».

## Матрица рекомендаций (автосинк)

Ниже — авто-блок: генерируется из `results/*.json` скриптом
`scripts/build_model_selection.py`. Руками не редактировать — правь данные/скрипт и пересобирай.

<!-- MSEL:BEGIN -->
Автосинк: 72 измеренных прогонов · тиры по ₽/решённую задачу (p33/p66 = 12.87/49.47₽)

| Bucket | Harness | Model | Reasoning | Result | % | Cost/solved | Pareto | Stem |
| --- | --- | --- | --- | ---: | ---: | ---: | :-: | --- |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: medium) | medium | 52/52 | 100.0% | 1.07₽ | ✅ | `oc_shotlist_ds41flash_medium_20261107` |
| Shot List (52) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: off) | off | 51/52 | 98.1% | 0.84₽ | ✅ | `dsh_shotlist_ds41flash_off_20261111` |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: high) | high | 51/52 | 98.1% | 1.06₽ | — | `oc_shotlist_ds41flash_high_20261107` |
| Shot List (52) | Pi | Claude Sonnet 4.5 (thinking: —) | — | 51/52 | 98.1% | 20.79₽ | — | `pi_shotlist_sonnet45_20261114` |
| Shot List (52) | OpenClaw | GPT-5.5 (thinking: off) | off | 51/52 | 98.1% | 49.47₽ | — | `oc_shotlist_gpt55_off_20261110` |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | off | 50/52 | 96.2% | 1.01₽ | — | `oc_shotlist_ds41flash_off_20261107` |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: —) | — | 50/52 | 96.2% | 4.43₽ | — | `oc_shotlist_ds41flash` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: medium) | medium | 50/52 | 96.2% | 14.98₽ | — | `pi_sonnet46_shotlist_medium_20261114` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: —) | — | 50/52 | 96.2% | 15.16₽ | — | `pi_sonnet46_shotlist_20261113` |
| Shot List (52) | OpenClaw | GPT-5.5 (thinking: high) | high | 50/52 | 96.2% | 58.07₽ | — | `oc_shotlist_gpt55_high_20261110` |
| Shot List (52) | Pi | DeepSeek-V4.1-Flash (thinking: —) | — | 49/52 | 94.2% | 0.85₽ | — | `pi_shotlist_ds41flash` |
| Shot List (52) | Pi | GLM-5.3 (thinking: —) | — | 49/52 | 94.2% | 4.51₽ | — | `pi_shotlist_glm53` |
| Shot List (52) | Hermes | DeepSeek-V4.1-Flash (thinking: off) | off | 49/52 | 94.2% | 5.90₽ | — | `hermes_shotlist_ds41flash_off_20261111` |
| Shot List (52) | Pi | GLM-5.2 (thinking: off) | — | 49/52 | 94.2% | 7.69₽ | — | `pi_shotlist_glm52_thinking_off` |
| Shot List (52) | Pi | GPT-5.5 (thinking: high) | high | 49/52 | 94.2% | 11.61₽ | — | `pi_shotlist_gpt55_high_20261110` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: —) | — | 49/52 | 94.2% | 13.33₽ | — | `pi_shotlist_sonnet46_20261114` |
| Shot List (52) | Pi | Claude Opus 4.8 (thinking: —) | — | 49/52 | 94.2% | 15.60₽ | — | `pi_shotlist_opus48_20261114` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: off) | off | 49/52 | 94.2% | 16.71₽ | — | `pi_shotlist_sonnet46_off_20261113` |
| Shot List (52) | Pi | GPT-5.5 (thinking: off) | off | 48/52 | 92.3% | 10.35₽ | — | `pi_shotlist_gpt55_off_20261110` |
| Shot List (52) | Pi | Claude Opus 5.5 (thinking: —) | — | 48/52 | 92.3% | 11.59₽ | — | `pi_shotlist_opus55_20261114` |
| Shot List (52) | OpenClaw | MiniMax-M3 (thinking: off) | off | 48/52 | 92.3% | 20.25₽ | — | `oc_shotlist_minimaxm3_off_20261109` |
| Shot List (52) | OpenClaw | MiniMax-M3 (thinking: medium) | medium | 47/52 | 90.4% | 23.81₽ | — | `oc_shotlist_minimaxm3_medium_20261109` |
| Shot List (52) | OpenClaw | GLM-5.2 (thinking: off) | — | 47/52 | 90.4% | 27.69₽ | — | `oc_shotlist_thinking_off` |
| Shot List (52) | Pi | Claude Fable 5.1 (thinking: —) | — | 47/52 | 90.4% | 31.60₽ | — | `pi_shotlist_fable51_20261114` |
| Shot List (52) | OpenClaw | MiniMax-M3 (thinking: ultra) | ultra | 46/52 | 88.5% | 23.22₽ | — | `oc_shotlist_minimaxm3_ultra_20261109` |
| Shot List (52) | Pi | Claude Fable 5 (thinking: —) | — | 46/52 | 88.5% | 27.46₽ | — | `pi_shotlist_fable5_20261114` |
| Shot List (52) | Hermes | GLM-5.2 (thinking: off) | — | 46/52 | 88.5% | 32.80₽ | — | `hermes_shotlist_thinking_off` |
| Shot List (52) | OpenClaw | GLM-5.3 (thinking: —) | — | 44/52 | 84.6% | 2.84₽ | — | `oc_shotlist_glm53` |
| Shot List (52) | Pi | Qwen3.6-35B-A3B (thinking: —) | — | 42/52 | 80.8% | 7.13₽ | — | `pi_shotlist_qwen36` |
| Shot List (52) | OpenClaw | Qwen3.6-35B-A3B (thinking: —) | — | 40/52 | 76.9% | 51.97₽ | — | `oc_shotlist_qwen36` |
| Shot List (52) | Hermes | Qwen3.6-35B-A3B (thinking: —) | — | 36/52 | 69.2% | 42.90₽ | — | `hermes_shotlist_qwen36` |
| Shot List (52) | Pi | GigaChat 3.5 Reasoning (thinking: low) | low | 35/52 | 67.3% | 10.05₽ | — | `pi_shotlist_giga35_reasoning_low_20261107` |
| Shot List (52) | Pi | GigaChat 3.5 Reasoning (thinking: medium) | medium | 34/52 | 65.4% | 10.40₽ | — | `pi_shotlist_giga35_reasoning_medium_20261107` |
| Shot List (52) | Pi | GigaChat 3.5 Reasoning (thinking: high) | high | 34/52 | 65.4% | 12.82₽ | — | `pi_shotlist_giga35_reasoning_high_20261107` |
| Shot List (52) | Hermes | GPT-OSS-120B (thinking: —) | — | 28/52 | 53.8% | 5.19₽ | — | `hermes_shotlist_oss120b` |
| Shot List (52) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: off) | off | 25/52 | 48.1% | 8.33₽ | — | `oc_shotlist_giga35base_off_20261113` |
| Shot List (52) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: ultra) | ultra | 20/52 | 38.5% | 1.94₽ | — | `oc_shotlist_giga35base_ultra_20261114` |
| Shot List (52) | OpenClaw | GPT-OSS-120B (thinking: —) | — | 17/52 | 32.7% | 46.22₽ | — | `oc_shotlist_oss120b` |
| Shot List (52) | Pi | GigaChat 3 Pro (thinking: —) | — | 16/52 | 30.8% | 59.81₽ | — | `pi_shotlist_gigachat3pro` |
| Medium · reflect (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: high) | high | 17/20 | 85.0% | 16.23₽ | ✅ | `oc_medium_reflect_ds41flash_high_20261108` |
| Medium · reflect (20) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: off) | off | 16/20 | 80.0% | 12.87₽ | ✅ | `dsh_medium_reflect_ds41flash_off_20261111` |
| Medium · reflect (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: medium) | medium | 16/20 | 80.0% | 15.15₽ | — | `oc_medium_reflect_ds41flash_medium_20261108` |
| Medium · reflect (20) | Pi | Claude Sonnet 4.6 (thinking: off) | off | 15/20 | 75.0% | 274.78₽ | — | `pi_medium_reflect_sonnet46_off_20261113` |
| Medium · reflect (20) | Pi | Claude Sonnet 4.6 (thinking: medium) | medium | 14/20 | 70.0% | 92.34₽ | — | `pi_medium_reflect_sonnet46_medium_20261113` |
| Medium · reflect (20) | Pi | GPT-5.5 (thinking: off) | off | 14/20 | 70.0% | 123.48₽ | — | `pi_medium_reflect_gpt55_off_20261110` |
| Medium · reflect (20) | OpenClaw | MiniMax-M3 (thinking: off) | off | 13/20 | 65.0% | 263.54₽ | — | `oc_medium_reflect_minimaxm3_off_20261109` |
| Medium · reflect (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | off | 12/20 | 60.0% | 18.03₽ | — | `oc_medium_reflect_ds41flash_off_20261108` |
| Medium · reflect (20) | OpenClaw | GPT-5.5 (thinking: off) | off | 12/20 | 60.0% | 320.52₽ | — | `oc_medium_reflect_gpt55_off_20261110` |
| Medium · reflect (20) | Pi | DeepSeek-V4.1-Flash (thinking: —) | — | 11/15 | 73.3% | 32.75₽ | — | `pi_reflect_ds41flash_b_20261106` |
| Medium · reflect (20) | OpenClaw | MiniMax-M3 (thinking: medium) | medium | 11/20 | 55.0% | 269.11₽ | — | `oc_medium_reflect_minimaxm3_medium_20261109` |
| Medium · reflect (20) | Pi | DeepSeek-V4.1-Flash (thinking: —) | — | 5/5 | 100.0% | 17.11₽ | — | `pi_reflect_ds41flash_20261106` |
| Medium · reflect (20) | Pi | GigaChat 3.5 Reasoning (thinking: low) | low | 2/20 | 10.0% | 1632.19₽ | — | `pi_medium_reflect_giga35_low_20261107` |
| Medium · long (20) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: off) | off | 5/6 | 83.3% | 195.89₽ | ✅ | `dsh_medium_long_ds41flash_off_20261111` |
| Medium · long (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: high) | high | 3/6 | 50.0% | 56.48₽ | ✅ | `oc_medium_long_ds41flash_high_20261109` |
| Medium · long (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: medium) | medium | 2/6 | 33.3% | 6.29₽ | ✅ | `oc_medium_long_ds41flash_medium_20261109` |
| Medium · long (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | off | 2/6 | 33.3% | 94.45₽ | — | `oc_medium_long_ds41flash_off_20261109` |
| Medium · long (20) | OpenClaw | GPT-5.5 (thinking: off) | off | 1/6 | 16.7% | 12926.41₽ | — | `oc_medium_long_gpt55_off_20261110` |
| Medium · long (20) | Pi | GPT-5.5 (thinking: off) | off | 1/6 | 16.7% | 16102.59₽ | — | `pi_medium_long_gpt55_off_20261110` |
| Medium · long (20) | Pi | Claude Sonnet 4.6 (thinking: off) | off | 1/6 | 16.7% | 55280.97₽ | — | `pi_medium_long_sonnet46_off_20261113` |
| Medium · feasibility (40) | OpenClaw | DeepSeek-V4.1-Flash (thinking: medium) | medium | 39/40 | 97.5% | 12.65₽ | ✅ | `oc_medium_feasibility_ds41flash_medium_20261108` |
| Medium · feasibility (40) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: off) | off | 39/40 | 97.5% | 12.68₽ | — | `dsh_medium_feasibility_ds41flash_off_20261111` |
| Medium · feasibility (40) | OpenClaw | DeepSeek-V4.1-Flash (thinking: high) | high | 38/40 | 95.0% | 13.40₽ | — | `oc_medium_feasibility_ds41flash_high_20261108` |
| Medium · feasibility (40) | OpenClaw | GPT-5.5 (thinking: off) | off | 38/40 | 95.0% | 259.46₽ | — | `oc_medium_feasibility_gpt55_off_20261110` |
| Medium · feasibility (40) | Pi | GPT-5.5 (thinking: off) | off | 38/40 | 95.0% | 289.77₽ | — | `pi_medium_feasibility_gpt55_off_20261110` |
| Medium · feasibility (40) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | off | 36/40 | 90.0% | 15.91₽ | — | `oc_medium_feasibility_ds41flash_off_20261108` |
| Medium · feasibility (40) | OpenClaw | MiniMax-M3 (thinking: off) | off | 33/40 | 82.5% | 261.68₽ | — | `oc_medium_feasibility_minimaxm3_off_20261109` |
| Medium · feasibility (40) | OpenClaw | MiniMax-M3 (thinking: medium) | medium | 26/40 | 65.0% | 506.65₽ | — | `oc_medium_feasibility_minimaxm3_medium_20261109` |
| Medium · feasibility (40) | Pi | Claude Sonnet 4.6 (thinking: medium) | medium | 21/40 | 52.5% | 453.55₽ | — | `pi_medium_feasibility_sonnet46_medium_20261113` |
| Medium · feasibility (40) | Pi | Claude Sonnet 4.6 (thinking: off) | off | 20/40 | 50.0% | 1640.02₽ | — | `pi_medium_feasibility_sonnet46_off_20261113` |
| Medium · feasibility (40) | Pi | GigaChat 3.5 Reasoning (thinking: low) | low | 11/40 | 27.5% | 898.30₽ | — | `pi_medium_feasibility_giga35_low_20261107` |
| Medium · feasibility (40) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: off) | off | 1/40 | 2.5% | 380.05₽ | — | `oc_medium_feasibility_giga35base_off_20261113` |
| Light (391) | Hermes | — | — | 387/391 | 99.0% | 20.09₽ | ✅ | `hermes_full_391` |

**Anti-patterns — переплата без выигрыша** (доминируемы более дешёвой конфигурацией с тем же/лучшим результатом):

- `Claude Sonnet 4.6 (thinking: off)` @ 1640.02₽ (wave19-feasibility) ← `dsh_medium_feasibility_ds41flash_off_20261111`
- `MiniMax-M3 (thinking: medium)` @ 506.65₽ (wave19-feasibility) ← `dsh_medium_feasibility_ds41flash_off_20261111`
- `Claude Sonnet 4.6 (thinking: medium)` @ 453.55₽ (wave19-feasibility) ← `dsh_medium_feasibility_ds41flash_off_20261111`
- `GPT-5.5 (thinking: off)` @ 320.52₽ (medium-reflect) ← `dsh_medium_reflect_ds41flash_off_20261111`
- `GPT-5.5 (thinking: off)` @ 289.77₽ (wave19-feasibility) ← `dsh_medium_feasibility_ds41flash_off_20261111`
- `Claude Sonnet 4.6 (thinking: off)` @ 274.78₽ (medium-reflect) ← `dsh_medium_reflect_ds41flash_off_20261111`
- `MiniMax-M3 (thinking: medium)` @ 269.11₽ (medium-reflect) ← `dsh_medium_reflect_ds41flash_off_20261111`
- `MiniMax-M3 (thinking: off)` @ 263.54₽ (medium-reflect) ← `dsh_medium_reflect_ds41flash_off_20261111`
- `MiniMax-M3 (thinking: off)` @ 261.68₽ (wave19-feasibility) ← `dsh_medium_feasibility_ds41flash_off_20261111`
- `GPT-5.5 (thinking: off)` @ 259.46₽ (wave19-feasibility) ← `dsh_medium_feasibility_ds41flash_off_20261111`
- `GPT-5.5 (thinking: off)` @ 123.48₽ (medium-reflect) ← `dsh_medium_reflect_ds41flash_off_20261111`
- `Claude Sonnet 4.6 (thinking: medium)` @ 92.34₽ (medium-reflect) ← `dsh_medium_reflect_ds41flash_off_20261111`
<!-- MSEL:END -->

## Как читать (рекомендации по ситуациям)

- **`shot-list`** — единственный плотно сравнимый бакет (4 харнесса, полный набор).
  Экшн: **Default = DeepSeek-V4.1-Flash** на OpenClaw или DeepSeek Harness.
- **`medium-reflect` / `long-context` / `wave19-feasibility`** — DeepSeek остаётся на границе
  Парето; long-бакет измерен частично → решения с низкой уверенностью.
- **`light-391`** — сопоставимой пары «один харнесс» нет → только `unmeasured`.
- **`hard-coupled (wave21)`** — не измерен вовсе → рекомендаций нет до прогонов.

## Покрытие и уверенность

- `pass_rate` = passed/total из JSON (пере-выведено, чтобы поймать устаревшие поля).
- `cost_per_solved` = ₽ / число решённых задач — ось цены и тиров.
- `coverage.full` = полный ли набор измерен; `coverage.version` = версия task-set.
- Уверенность: `high` (≥2 прогона), `medium` (1 прогон, полный набор), `low` (1 прогон, partial).

## Методология

1. Источник — `results/*.json`; для премиум-лестницы ₽ берутся из биллинга провайдера
   (`results/reconciled/`, см. `reconcile-premium-ladder-cost`).
2. Классификация (harness/model/reasoning/suite) — общий модуль `scripts/fork_metadata.py`,
   поэтому стемы совпадают со строками опубликованного форка.
3. Фронт Парето строится **внутри каждого бакета** (сопоставимые знаменатели), по
   `(pass ↑, cost_per_solved ↓)`; доминируемые точки → anti-patterns.
4. Правило «premium оправдан» (ADR R2-Q5): точка не доминируема **и** даёт ≥ +3 п.п. pass
   **или** закрывает ≥1 floor/boundary-задачу. На текущих данных таких premium нет.
5. Сравнение только внутри одной task-set версии/протокола; partial-покрытие = `unmeasured`.
