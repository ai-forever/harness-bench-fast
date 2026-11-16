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
Автосинк: 88 строк (вкл. 0/N) · тиры — пер-бакет по ₽/решённую задачу · ✅ — точка на фронте Парето (внутри одного набора)

| Bucket | Harness | Model | Tier | Result | Cost/solved | Basis | Pareto | Stem |
| --- | --- | --- | :-: | ---: | ---: | :-: | :-: | --- |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: medium) | Default | 52/52 | 1.07₽ | est. | ✅ | `oc_shotlist_ds41flash_medium_20261107` |
| Shot List (52) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: off) | Default | 51/52 | 0.84₽ | est. | ✅ | `dsh_shotlist_ds41flash_off_20261111` |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: high) | Default | 51/52 | 1.06₽ | est. | — | `oc_shotlist_ds41flash_high_20261107` |
| Shot List (52) | Pi | Claude Sonnet 4.5 (thinking: —) | Premium | 51/52 | 20.79₽ | billing | — | `pi_shotlist_sonnet45_20261114` |
| Shot List (52) | OpenClaw | GPT-5.5 (thinking: off) | Premium | 51/52 | 49.47₽ | est. | — | `oc_shotlist_gpt55_off_20261110` |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | Default | 50/52 | 1.01₽ | est. | — | `oc_shotlist_ds41flash_off_20261107` |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: —) | Default | 50/52 | 4.43₽ | est. | — | `oc_shotlist_ds41flash` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: medium) | Balanced | 50/52 | 14.98₽ | est. | — | `pi_sonnet46_shotlist_medium_20261114` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: —) | Balanced | 50/52 | 15.16₽ | est. | — | `pi_sonnet46_shotlist_20261113` |
| Shot List (52) | OpenClaw | GPT-5.5 (thinking: high) | Premium | 50/52 | 58.07₽ | est. | — | `oc_shotlist_gpt55_high_20261110` |
| Shot List (52) | Pi | DeepSeek-V4.1-Flash (thinking: —) | Default | 49/52 | 0.85₽ | est. | — | `pi_shotlist_ds41flash` |
| Shot List (52) | Pi | GLM-5.3 (thinking: —) | Default | 49/52 | 4.51₽ | est. | — | `pi_shotlist_glm53` |
| Shot List (52) | Hermes | DeepSeek-V4.1-Flash (thinking: off) | Default | 49/52 | 5.90₽ | est. | — | `hermes_shotlist_ds41flash_off_20261111` |
| Shot List (52) | Pi | GLM-5.2 (thinking: off) | Default | 49/52 | 7.69₽ | est. | — | `pi_shotlist_glm52_thinking_off` |
| Shot List (52) | Pi | GPT-5.5 (thinking: high) | Balanced | 49/52 | 11.61₽ | est. | — | `pi_shotlist_gpt55_high_20261110` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: —) | Balanced | 49/52 | 13.33₽ | billing | — | `pi_shotlist_sonnet46_20261114` |
| Shot List (52) | Pi | Claude Opus 4.8 (thinking: —) | Balanced | 49/52 | 15.60₽ | billing | — | `pi_shotlist_opus48_20261114` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: off) | Balanced | 49/52 | 16.71₽ | est. | — | `pi_shotlist_sonnet46_off_20261113` |
| Shot List (52) | Pi | GPT-5.5 (thinking: off) | Balanced | 48/52 | 10.35₽ | est. | — | `pi_shotlist_gpt55_off_20261110` |
| Shot List (52) | Pi | Claude Opus 5.5 (thinking: —) | Balanced | 48/52 | 11.59₽ | billing | — | `pi_shotlist_opus55_20261114` |
| Shot List (52) | OpenClaw | MiniMax-M3 (thinking: off) | Balanced | 48/52 | 20.25₽ | est. | — | `oc_shotlist_minimaxm3_off_20261109` |
| Shot List (52) | OpenClaw | MiniMax-M3 (thinking: medium) | Premium | 47/52 | 23.81₽ | est. | — | `oc_shotlist_minimaxm3_medium_20261109` |
| Shot List (52) | OpenClaw | GLM-5.2 (thinking: off) | Premium | 47/52 | 27.69₽ | est. | — | `oc_shotlist_thinking_off` |
| Shot List (52) | Pi | Claude Fable 5.1 (thinking: —) | Premium | 47/52 | 31.60₽ | billing | — | `pi_shotlist_fable51_20261114` |
| Shot List (52) | OpenClaw | MiniMax-M3 (thinking: ultra) | Premium | 46/52 | 23.22₽ | est. | — | `oc_shotlist_minimaxm3_ultra_20261109` |
| Shot List (52) | Pi | Claude Fable 5 (thinking: —) | Premium | 46/52 | 27.46₽ | billing | — | `pi_shotlist_fable5_20261114` |
| Shot List (52) | Hermes | GLM-5.2 (thinking: off) | Premium | 46/52 | 32.80₽ | est. | — | `hermes_shotlist_thinking_off` |
| Shot List (52) | OpenClaw | GLM-5.3 (thinking: —) | Default | 44/52 | 2.84₽ | est. | — | `oc_shotlist_glm53` |
| Shot List (52) | Pi | Qwen3.6-35B-A3B (thinking: —) | Default | 42/52 | 7.13₽ | est. | — | `pi_shotlist_qwen36` |
| Shot List (52) | OpenClaw | Qwen3.6-35B-A3B (thinking: —) | Premium | 40/52 | 51.97₽ | est. | — | `oc_shotlist_qwen36` |
| Shot List (52) | Hermes | Qwen3.6-35B-A3B (thinking: —) | Premium | 36/52 | 42.90₽ | est. | — | `hermes_shotlist_qwen36` |
| Shot List (52) | Pi | GigaChat 3.5 Reasoning (thinking: low) | Balanced | 35/52 | 10.05₽ | est. | — | `pi_shotlist_giga35_reasoning_low_20261107` |
| Shot List (52) | Pi | GigaChat 3.5 Reasoning (thinking: medium) | Balanced | 34/52 | 10.40₽ | est. | — | `pi_shotlist_giga35_reasoning_medium_20261107` |
| Shot List (52) | Pi | GigaChat 3.5 Reasoning (thinking: high) | Balanced | 34/52 | 12.82₽ | est. | — | `pi_shotlist_giga35_reasoning_high_20261107` |
| Shot List (52) | Hermes | GPT-OSS-120B (thinking: —) | Default | 28/52 | 5.19₽ | est. | — | `hermes_shotlist_oss120b` |
| Shot List (52) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: off) | Balanced | 25/52 | 8.33₽ | est. | — | `oc_shotlist_giga35base_off_20261113` |
| Shot List (52) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: ultra) | Default | 20/52 | 1.94₽ | est. | — | `oc_shotlist_giga35base_ultra_20261114` |
| Shot List (52) | OpenClaw | GPT-OSS-120B (thinking: —) | Premium | 17/52 | 46.22₽ | est. | — | `oc_shotlist_oss120b` |
| Shot List (52) | Pi | GigaChat 3 Pro (thinking: —) | Premium | 16/52 | 59.81₽ | est. | — | `pi_shotlist_gigachat3pro` |
| Medium · reflect (20) | Pi | DeepSeek-V4.1-Flash (thinking: —) | н/д | 5/5 | 17.11₽ | est. | ✅ | `pi_reflect_ds41flash_20261106` |
| Medium · reflect (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: high) | Default | 17/20 | 16.23₽ | est. | ✅ | `oc_medium_reflect_ds41flash_high_20261108` |
| Medium · reflect (20) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: off) | Default | 16/20 | 12.87₽ | est. | ✅ | `dsh_medium_reflect_ds41flash_off_20261111` |
| Medium · reflect (20) | Pi | DeepSeek-V4.1-Flash (thinking: —) | н/д | 11/15 | 32.75₽ | est. | ✅ | `pi_reflect_ds41flash_b_20261106` |
| Medium · reflect (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: medium) | Default | 16/20 | 15.15₽ | est. | — | `oc_medium_reflect_ds41flash_medium_20261108` |
| Medium · reflect (20) | Pi | Claude Sonnet 4.6 (thinking: off) | Premium | 15/20 | 274.78₽ | est. | — | `pi_medium_reflect_sonnet46_off_20261113` |
| Medium · reflect (20) | Pi | Claude Sonnet 4.6 (thinking: medium) | Balanced | 14/20 | 92.34₽ | est. | — | `pi_medium_reflect_sonnet46_medium_20261113` |
| Medium · reflect (20) | Pi | GPT-5.5 (thinking: off) | Balanced | 14/20 | 123.48₽ | est. | — | `pi_medium_reflect_gpt55_off_20261110` |
| Medium · reflect (20) | OpenClaw | MiniMax-M3 (thinking: off) | Balanced | 13/20 | 263.54₽ | est. | — | `oc_medium_reflect_minimaxm3_off_20261109` |
| Medium · reflect (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | Balanced | 12/20 | 18.03₽ | est. | — | `oc_medium_reflect_ds41flash_off_20261108` |
| Medium · reflect (20) | OpenClaw | GPT-5.5 (thinking: off) | Premium | 12/20 | 320.52₽ | est. | — | `oc_medium_reflect_gpt55_off_20261110` |
| Medium · reflect (20) | OpenClaw | MiniMax-M3 (thinking: medium) | Premium | 11/20 | 269.11₽ | est. | — | `oc_medium_reflect_minimaxm3_medium_20261109` |
| Medium · reflect (20) | Pi | GigaChat 3.5 Reasoning (thinking: low) | Premium | 2/20 | 1 632.19₽ | est. | — | `pi_medium_reflect_giga35_low_20261107` |
| Medium · reflect (20) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: off) | н/д | 0/20 | н/д | est. | — | `oc_medium_reflect_giga35base_off_20261113` |
| Medium · reflect (20) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: ultra) | н/д | 0/20 | н/д | est. | — | `oc_medium_reflect_giga35base_ultra_20261114` |
| Medium · reflect (20) | Pi | GigaChat 3 Pro (thinking: —) | н/д | 0/20 | н/д | est. | — | `pi_medium_reflect_gigachat3pro_20261109` |
| Medium · long (20) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: off) | н/д | 5/6 | 195.89₽ | est. | ✅ | `dsh_medium_long_ds41flash_off_20261111` |
| Medium · long (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: high) | н/д | 3/6 | 56.48₽ | est. | ✅ | `oc_medium_long_ds41flash_high_20261109` |
| Medium · long (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: medium) | н/д | 2/6 | 6.29₽ | est. | ✅ | `oc_medium_long_ds41flash_medium_20261109` |
| Medium · long (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | н/д | 2/6 | 94.45₽ | est. | — | `oc_medium_long_ds41flash_off_20261109` |
| Medium · long (20) | OpenClaw | GPT-5.5 (thinking: off) | н/д | 1/6 | 12 926.41₽ | est. | — | `oc_medium_long_gpt55_off_20261110` |
| Medium · long (20) | Pi | GPT-5.5 (thinking: off) | н/д | 1/6 | 16 102.59₽ | est. | — | `pi_medium_long_gpt55_off_20261110` |
| Medium · long (20) | Pi | Claude Sonnet 4.6 (thinking: off) | н/д | 1/6 | 55 280.97₽ | est. | — | `pi_medium_long_sonnet46_off_20261113` |
| Medium · long (20) | OpenClaw | — | н/д | 0/6 | н/д | est. | — | `oc_m66-plain-long_20261113` |
| Medium · long (20) | OpenClaw | — | н/д | 0/6 | н/д | est. | — | `oc_m66-ultra-long_20261114` |
| Medium · long (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | н/д | 0/2 | н/д | est. | — | `oc_medium_long_ds41flash_off_20261108` |
| Medium · long (20) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: off) | н/д | 0/6 | н/д | est. | — | `oc_medium_long_giga35base_off_20261113` |
| Medium · long (20) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: ultra) | н/д | 0/6 | н/д | est. | — | `oc_medium_long_giga35base_ultra_20261114` |
| Medium · long (20) | OpenClaw | MiniMax-M3 (thinking: medium) | н/д | 0/6 | н/д | est. | — | `oc_medium_long_minimaxm3_medium_20261109` |
| Medium · long (20) | OpenClaw | MiniMax-M3 (thinking: off) | н/д | 0/6 | н/д | est. | — | `oc_medium_long_minimaxm3_off_20261109` |
| Medium · long (20) | Pi | DeepSeek-V4.1-Flash (thinking: —) | н/д | 0/3 | н/д | est. | — | `pi_long_ds41flash_20261106` |
| Medium · long (20) | Pi | GigaChat 3.5 Reasoning (thinking: low) | н/д | 0/6 | н/д | est. | — | `pi_medium_long_giga35_low_20261107` |
| Medium · long (20) | Pi | GigaChat 3 Pro (thinking: —) | н/д | 0/6 | н/д | est. | — | `pi_medium_long_gigachat3pro_20261109` |
| Medium · long (20) | Pi | Claude Sonnet 4.6 (thinking: medium) | н/д | 0/6 | н/д | est. | — | `pi_medium_long_sonnet46_medium_20261113` |
| Medium · feasibility (40) | OpenClaw | DeepSeek-V4.1-Flash (thinking: medium) | Default | 39/40 | 12.65₽ | est. | ✅ | `oc_medium_feasibility_ds41flash_medium_20261108` |
| Medium · feasibility (40) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: off) | Default | 39/40 | 12.68₽ | est. | — | `dsh_medium_feasibility_ds41flash_off_20261111` |
| Medium · feasibility (40) | OpenClaw | DeepSeek-V4.1-Flash (thinking: high) | Default | 38/40 | 13.40₽ | est. | — | `oc_medium_feasibility_ds41flash_high_20261108` |
| Medium · feasibility (40) | OpenClaw | GPT-5.5 (thinking: off) | Balanced | 38/40 | 259.46₽ | est. | — | `oc_medium_feasibility_gpt55_off_20261110` |
| Medium · feasibility (40) | Pi | GPT-5.5 (thinking: off) | Balanced | 38/40 | 289.77₽ | est. | — | `pi_medium_feasibility_gpt55_off_20261110` |
| Medium · feasibility (40) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | Default | 36/40 | 15.91₽ | est. | — | `oc_medium_feasibility_ds41flash_off_20261108` |
| Medium · feasibility (40) | OpenClaw | MiniMax-M3 (thinking: off) | Balanced | 33/40 | 261.68₽ | est. | — | `oc_medium_feasibility_minimaxm3_off_20261109` |
| Medium · feasibility (40) | OpenClaw | MiniMax-M3 (thinking: medium) | Premium | 26/40 | 506.65₽ | est. | — | `oc_medium_feasibility_minimaxm3_medium_20261109` |
| Medium · feasibility (40) | Pi | Claude Sonnet 4.6 (thinking: medium) | Premium | 21/40 | 453.55₽ | est. | — | `pi_medium_feasibility_sonnet46_medium_20261113` |
| Medium · feasibility (40) | Pi | Claude Sonnet 4.6 (thinking: off) | Premium | 20/40 | 1 640.02₽ | est. | — | `pi_medium_feasibility_sonnet46_off_20261113` |
| Medium · feasibility (40) | Pi | GigaChat 3.5 Reasoning (thinking: low) | Premium | 11/40 | 898.30₽ | est. | — | `pi_medium_feasibility_giga35_low_20261107` |
| Medium · feasibility (40) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: off) | Balanced | 1/40 | 380.05₽ | est. | — | `oc_medium_feasibility_giga35base_off_20261113` |
| Medium · feasibility (40) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: ultra) | н/д | 0/40 | н/д | est. | — | `oc_medium_feasibility_giga35base_ultra_20261114` |
| Medium · feasibility (40) | Pi | GigaChat 3 Pro (thinking: —) | н/д | 0/40 | н/д | est. | — | `pi_medium_feasibility_gigachat3pro_20261109` |
| Light (391) | Hermes | — | Default | 387/391 | 20.09₽ | est. | ✅ | `hermes_full_391` |

**Anti-patterns — переплата без выигрыша** (доминируемы более дешёвой конфигурацией с тем же/лучшим результатом):

- `Claude Sonnet 4.6 (thinking: off)` @ 1 640.02₽ (wave19-feasibility) ← `dsh_medium_feasibility_ds41flash_off_20261111`
- `MiniMax-M3 (thinking: medium)` @ 506.65₽ (wave19-feasibility) ← `dsh_medium_feasibility_ds41flash_off_20261111`
- `Claude Sonnet 4.6 (thinking: medium)` @ 453.55₽ (wave19-feasibility) ← `dsh_medium_feasibility_ds41flash_off_20261111`
- `GPT-5.5 (thinking: off)` @ 320.52₽ (medium-reflect) ← `dsh_medium_reflect_ds41flash_off_20261111`
- `MiniMax-M3 (thinking: off)` @ 263.54₽ (medium-reflect) ← `dsh_medium_reflect_ds41flash_off_20261111`
- `GigaChat 3 Pro (thinking: —)` @ 59.81₽ (shot-list) ← `dsh_shotlist_ds41flash_off_20261111`
- `GPT-5.5 (thinking: high)` @ 58.07₽ (shot-list) ← `dsh_shotlist_ds41flash_off_20261111`
- `GPT-OSS-120B (thinking: —)` @ 46.22₽ (shot-list) ← `dsh_shotlist_ds41flash_off_20261111`
- `GLM-5.2 (thinking: off)` @ 32.80₽ (shot-list) ← `dsh_shotlist_ds41flash_off_20261111`
- `Claude Fable 5.1 (thinking: —)` @ 31.60₽ (shot-list) ← `dsh_shotlist_ds41flash_off_20261111`
- `Claude Fable 5 (thinking: —)` @ 27.46₽ (shot-list) ← `dsh_shotlist_ds41flash_off_20261111`
- `MiniMax-M3 (thinking: ultra)` @ 23.22₽ (shot-list) ← `dsh_shotlist_ds41flash_off_20261111`
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
