<!-- Model & harness selection guidance (RU-first).
     Auto-generated block is rebuilt by scripts/build_model_selection.py (--check/--lint). -->
# Model & Harness Selection Guidance — что × чем × когда

Практическое руководство по выбору **харнесса × модели** для задач бенчмарка
`harness-bench-fast`, построенное **только на уже выполненных прогонах** (`results/*.json`).
₽ = **факт биллинга провайдера там, где есть reconciled-сайдкар**, иначе — оценка по тарифу
(см. столбец `Basis`).

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
- **Уровень reasoning — реальный, но дорогой рычаг (DeepSeek Harness).** DeepSeek-V4.1-Flash идёт от
  **51/52** на `thinking: off`/`medium` (~0.85 ₽/задачу) до **52/52 на `thinking: max`** — но это ~5×
  дороже (≈4.18 ₽/задачу, 84 мин). `medium` — точка баланса; `max` — только если важна последняя
  задача.
- **Харнесс влияет на результат не меньше модели**: одна и та же модель на разных харнессах даёт
  разные score/время/шаги. Выбирайте **пару** (harness × model), а не «модель вообще».
- **Cloud.ru batch (2026-11-19, Pi, 52 задачи):** `GLM-5.3-FlashX` — самый дешёвый
  (≈0.45 ₽/решённую задачу, 49/52 на `max`); `GPT-6.1-Sol` и `Kimi-K3` держат **51/52 стабильно на
  всех уровнях** (off/medium/max), но дороже (~3–5 ₽/задача); `Gemini-3.8-Flash` — **runaway токенов**
  на высоком reasoning (2.3M→8.8M токенов off→max) при падении score (51→50/52) и худшем ₽/задача.
  Ни одна из новых моделей не бьёт DeepSeek-V4.1-Flash по ₽/решённую задачу.
- **Чего в данных нет:** `wave21 (hard-coupled)` не измерен вовсе; long-волна покрыта лишь
  частично (6/20); Light-391 — 3 прогона. Такие клетки помечены `unmeasured`, а не «fail».

## Матрица рекомендаций (автосинк)

Ниже — авто-блок: генерируется из `results/*.json` скриптом
`scripts/build_model_selection.py`. Руками не редактировать — правь данные/скрипт и пересобирай.

<!-- MSEL:BEGIN -->
Автосинк: 102 строк (вкл. 0/N) · тиры — пер-бакет по ₽/решённую задачу · ✅ — точка на фронте Парето (внутри одного набора)

| Bucket | Harness | Model | Tier | Result | Cost/solved | Basis | Min | Steps | Pareto | Stem |
| --- | --- | --- | :-: | ---: | ---: | :-: | ---: | ---: | :-: | --- |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: medium) | Default | 52/52 | 1.07₽ | est. | 40.6 | 224 | ✅ | `oc_shotlist_ds41flash_medium_20261107` |
| Shot List (52) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: off) | Default | 51/52 | 0.84₽ | est. | 19.1 | 266 | ✅ | `dsh_shotlist_ds41flash_off_20261111` |
| Shot List (52) | Pi | GLM-5.3-FlashX (thinking: max) | Default | 49/52 | 0.45₽ | est. | 11.8 | 258 | ✅ | `pi_shotlist_glm53flashx_max_20261119` |
| Shot List (52) | Pi | GLM-5.3-FlashX (thinking: low) ★ | Default | 45/52 | 0.36₽ | est. | 10.5 | 235 | ✅ | `pi_shotlist_glm53flashx_low_20261119` |
| Shot List (52) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: max) | Default | 52/52 | 4.18₽ | est. | 84.1 | 438 | — | `dsh_shotlist_ds41flash_max_20261118` |
| Shot List (52) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: medium) | Default | 51/52 | 0.88₽ | est. | 22.4 | 269 | — | `dsh_shotlist_ds41flash_medium_20261117` |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: high) | Default | 51/52 | 1.06₽ | est. | 40.3 | 244 | — | `oc_shotlist_ds41flash_high_20261107` |
| Shot List (52) | Pi | GPT-6.1-Sol (thinking: medium) | Default | 51/52 | 2.87₽ | est. | 20.7 | 243 | — | `pi_shotlist_gpt61sol_medium_20261119` |
| Shot List (52) | Pi | GPT-6.1-Sol (thinking: off) | Default | 51/52 | 3.06₽ | est. | 21.2 | 248 | — | `pi_shotlist_gpt61sol_off_20261119` |
| Shot List (52) | Pi | GPT-6.1-Sol (thinking: max) | Default | 51/52 | 3.73₽ | est. | 26.8 | 293 | — | `pi_shotlist_gpt61sol_max_20261119` |
| Shot List (52) | Pi | Gemini-3.8-Flash (thinking: off) | Default | 51/52 | 4.04₽ | est. | 83.3 | 511 | — | `pi_shotlist_gem38flash_off_20261119` |
| Shot List (52) | Pi | Kimi-K3 (thinking: max) | Default | 51/52 | 4.17₽ | est. | 32.7 | 223 | — | `pi_shotlist_kimik3_max_20261119` |
| Shot List (52) | Pi | Kimi-K3 (thinking: medium) | Balanced | 51/52 | 4.63₽ | est. | 38.0 | 225 | — | `pi_shotlist_kimik3_medium_20261119` |
| Shot List (52) | Pi | Kimi-K3 (thinking: off) | Balanced | 51/52 | 5.08₽ | est. | 40.9 | 239 | — | `pi_shotlist_kimik3_off_20261119` |
| Shot List (52) | Pi | Claude Sonnet 4.5 (thinking: n/a) | Premium | 51/52 | 20.79₽ | billing | 25.7 | 1352 | — | `pi_shotlist_sonnet45_20261114` |
| Shot List (52) | OpenClaw | GPT-5.5 (thinking: off) | Premium | 51/52 | 49.47₽ | est. | 30.5 | 267 | — | `oc_shotlist_gpt55_off_20261110` |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | Default | 50/52 | 1.01₽ | est. | 37.9 | 244 | — | `oc_shotlist_ds41flash_off_20261107` |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: n/a) | Balanced | 50/52 | 4.43₽ | est. | 47.3 | 237 | — | `oc_shotlist_ds41flash` |
| Shot List (52) | Pi | Gemini-3.8-Flash (thinking: medium) | Balanced | 50/52 | 7.43₽ | est. | 149.9 | 644 | — | `pi_shotlist_gem38flash_medium_20261119` |
| Shot List (52) | Pi | Gemini-3.8-Flash (thinking: max) | Balanced | 50/52 | 11.55₽ | est. | 179.8 | 879 | — | `pi_shotlist_gem38flash_max_20261119` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: medium) | Premium | 50/52 | 14.98₽ | est. | 14.3 | 1056 | — | `pi_sonnet46_shotlist_medium_20261114` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: n/a) | Premium | 50/52 | 15.16₽ | est. | 14.6 | 1060 | — | `pi_sonnet46_shotlist_20261113` |
| Shot List (52) | OpenClaw | GPT-5.5 (thinking: high) | Premium | 50/52 | 58.07₽ | est. | 37.0 | 334 | — | `oc_shotlist_gpt55_high_20261110` |
| Shot List (52) | Pi | DeepSeek-V4.1-Flash (thinking: n/a) | Default | 49/52 | 0.85₽ | est. | 21.3 | 330 | — | `pi_shotlist_ds41flash` |
| Shot List (52) | Pi | GLM-5.3 (thinking: n/a) | Balanced | 49/52 | 4.51₽ | est. | 64.9 | 349 | — | `pi_shotlist_glm53` |
| Shot List (52) | Hermes | DeepSeek-V4.1-Flash (thinking: off) | Balanced | 49/52 | 5.90₽ | est. | 22.0 | 221 | — | `hermes_shotlist_ds41flash_off_20261111` |
| Shot List (52) | Pi | GLM-5.2 (thinking: off) | Balanced | 49/52 | 7.69₽ | est. | 102.8 | 388 | — | `pi_shotlist_glm52_thinking_off` |
| Shot List (52) | Pi | GPT-5.5 (thinking: high) | Balanced | 49/52 | 11.61₽ | est. | 11.6 | 299 | — | `pi_shotlist_gpt55_high_20261110` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: n/a) | Balanced | 49/52 | 13.33₽ | billing | 14.0 | 996 | — | `pi_shotlist_sonnet46_20261114` |
| Shot List (52) | Pi | Claude Opus 4.8 (thinking: n/a) | Premium | 49/52 | 15.60₽ | billing | 12.3 | 808 | — | `pi_shotlist_opus48_20261114` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: off) | Premium | 49/52 | 16.71₽ | est. | 12.8 | 1152 | — | `pi_shotlist_sonnet46_off_20261113` |
| Shot List (52) | Pi | GPT-5.5 (thinking: off) | Balanced | 48/52 | 10.35₽ | est. | 9.0 | 300 | — | `pi_shotlist_gpt55_off_20261110` |
| Shot List (52) | Pi | Claude Opus 5.5 (thinking: n/a) | Balanced | 48/52 | 11.59₽ | billing | 13.3 | 720 | — | `pi_shotlist_opus55_20261114` |
| Shot List (52) | OpenClaw | MiniMax-M3 (thinking: off) | Premium | 48/52 | 20.25₽ | est. | 295.4 | 312 | — | `oc_shotlist_minimaxm3_off_20261109` |
| Shot List (52) | OpenClaw | MiniMax-M3 (thinking: medium) | Premium | 47/52 | 23.81₽ | est. | 306.9 | 328 | — | `oc_shotlist_minimaxm3_medium_20261109` |
| Shot List (52) | OpenClaw | GLM-5.2 (thinking: off) | Premium | 47/52 | 27.69₽ | est. | 231.9 | 247 | — | `oc_shotlist_thinking_off` |
| Shot List (52) | Pi | Claude Fable 5.1 (thinking: n/a) | Premium | 47/52 | 31.60₽ | billing | 19.6 | 752 | — | `pi_shotlist_fable51_20261114` |
| Shot List (52) | Pi | GLM-5.3-FlashX (thinking: high) | Default | 46/52 | 0.53₽ | est. | 12.2 | 262 | — | `pi_shotlist_glm53flashx_high_20261119` |
| Shot List (52) | OpenClaw | MiniMax-M3 (thinking: ultra) | Premium | 46/52 | 23.22₽ | est. | 52.1 | 305 | — | `oc_shotlist_minimaxm3_ultra_20261109` |
| Shot List (52) | Pi | Claude Fable 5 (thinking: n/a) | Premium | 46/52 | 27.46₽ | billing | 13.9 | 712 | — | `pi_shotlist_fable5_20261114` |
| Shot List (52) | Hermes | GLM-5.2 (thinking: off) | Premium | 46/52 | 32.80₽ | est. | 68.5 | н/д | — | `hermes_shotlist_thinking_off` |
| Shot List (52) | OpenClaw | GLM-5.3 (thinking: n/a) | Default | 44/52 | 2.84₽ | est. | 46.1 | 148 | — | `oc_shotlist_glm53` |
| Shot List (52) | Pi | Qwen3.6-35B-A3B (thinking: n/a) | Balanced | 42/52 | 7.13₽ | est. | 18.5 | 327 | — | `pi_shotlist_qwen36` |
| Shot List (52) | OpenClaw | Qwen3.6-35B-A3B (thinking: n/a) | Premium | 40/52 | 51.97₽ | est. | 195.2 | 328 | — | `oc_shotlist_qwen36` |
| Shot List (52) | Hermes | Qwen3.6-35B-A3B (thinking: n/a) | Premium | 36/52 | 42.90₽ | est. | 38.6 | 301 | — | `hermes_shotlist_qwen36` |
| Shot List (52) | Pi | GigaChat 3.5 Reasoning (thinking: low) | Balanced | 35/52 | 10.05₽ | est. | 15.8 | 1116 | — | `pi_shotlist_giga35_reasoning_low_20261107` |
| Shot List (52) | Pi | GigaChat 3.5 Reasoning (thinking: medium) | Balanced | 34/52 | 10.40₽ | est. | 19.2 | 1176 | — | `pi_shotlist_giga35_reasoning_medium_20261107` |
| Shot List (52) | Pi | GigaChat 3.5 Reasoning (thinking: high) | Balanced | 34/52 | 12.82₽ | est. | 33.8 | 1160 | — | `pi_shotlist_giga35_reasoning_high_20261107` |
| Shot List (52) | Hermes | GPT-OSS-120B (thinking: n/a) | Balanced | 28/52 | 5.19₽ | est. | 37.9 | 427 | — | `hermes_shotlist_oss120b` |
| Shot List (52) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: off) | Balanced | 25/52 | 8.33₽ | est. | 48.1 | 429 | — | `oc_shotlist_giga35base_off_20261113` |
| Shot List (52) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: ultra) | Default | 20/52 | 1.94₽ | est. | 49.5 | 473 | — | `oc_shotlist_giga35base_ultra_20261114` |
| Shot List (52) | OpenClaw | GPT-OSS-120B (thinking: n/a) | Premium | 17/52 | 46.22₽ | est. | 205.2 | 120 | — | `oc_shotlist_oss120b` |
| Shot List (52) | Pi | GigaChat 3 Pro (thinking: n/a) | Premium | 16/52 | 59.81₽ | est. | 25.7 | 2034 | — | `pi_shotlist_gigachat3pro` |
| Medium · reflect (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: high) | Default | 17/20 | 16.23₽ | est. | 81.5 | 406 | ✅ | `oc_medium_reflect_ds41flash_high_20261108` |
| Medium · reflect (20) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: off) ★ | Default | 16/20 | 12.87₽ | est. | 95.8 | 379 | ✅ | `dsh_medium_reflect_ds41flash_off_20261111` |
| Medium · reflect (20) | Pi | DeepSeek-V4.1-Flash (thinking: n/a) | Default | 5/5 (partial) | 17.11₽ | est. | 32.0 | 336 | — | `pi_reflect_ds41flash_20261106` |
| Medium · reflect (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: medium) | Default | 16/20 | 15.15₽ | est. | 69.1 | 399 | — | `oc_medium_reflect_ds41flash_medium_20261108` |
| Medium · reflect (20) | Pi | Claude Sonnet 4.6 (thinking: off) | Premium | 15/20 | 274.78₽ | est. | 33.4 | 1836 | — | `pi_medium_reflect_sonnet46_off_20261113` |
| Medium · reflect (20) | Pi | DeepSeek-V4.1-Flash (thinking: n/a) | Default | 11/15 (partial) | 32.75₽ | est. | 137.3 | 1124 | — | `pi_reflect_ds41flash_b_20261106` |
| Medium · reflect (20) | Pi | Claude Sonnet 4.6 (thinking: medium) | Balanced | 14/20 | 92.34₽ | est. | 97.4 | 1492 | — | `pi_medium_reflect_sonnet46_medium_20261113` |
| Medium · reflect (20) | Pi | GPT-5.5 (thinking: off) | Balanced | 14/20 | 123.48₽ | est. | 25.9 | 327 | — | `pi_medium_reflect_gpt55_off_20261110` |
| Medium · reflect (20) | OpenClaw | MiniMax-M3 (thinking: off) | Balanced | 13/20 | 263.54₽ | est. | 162.0 | 698 | — | `oc_medium_reflect_minimaxm3_off_20261109` |
| Medium · reflect (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | Balanced | 12/20 | 18.03₽ | est. | 128.5 | 467 | — | `oc_medium_reflect_ds41flash_off_20261108` |
| Medium · reflect (20) | OpenClaw | GPT-5.5 (thinking: off) | Premium | 12/20 | 320.52₽ | est. | 35.1 | 264 | — | `oc_medium_reflect_gpt55_off_20261110` |
| Medium · reflect (20) | OpenClaw | MiniMax-M3 (thinking: medium) | Premium | 11/20 | 269.11₽ | est. | 109.4 | 623 | — | `oc_medium_reflect_minimaxm3_medium_20261109` |
| Medium · reflect (20) | Pi | GigaChat 3.5 Reasoning (thinking: low) | Premium | 2/20 | 1 632.19₽ | est. | 210.9 | 2176 | — | `pi_medium_reflect_giga35_low_20261107` |
| Medium · reflect (20) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: off) | н/д | 0/20 | н/д | est. | 84.6 | 935 | — | `oc_medium_reflect_giga35base_off_20261113` |
| Medium · reflect (20) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: ultra) | н/д | 0/20 | н/д | est. | 20.1 | 259 | — | `oc_medium_reflect_giga35base_ultra_20261114` |
| Medium · reflect (20) | Pi | GigaChat 3 Pro (thinking: n/a) | н/д | 0/20 | н/д | est. | 31.6 | 2948 | — | `pi_medium_reflect_gigachat3pro_20261109` |
| Medium · long (20) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: off) | Balanced | 5/6 (partial) | 195.89₽ | est. | 244.9 | 436 | ✅ | `dsh_medium_long_ds41flash_off_20261111` |
| Medium · long (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: high) | Default | 3/6 (partial) | 56.48₽ | est. | 128.2 | 183 | ✅ | `oc_medium_long_ds41flash_high_20261109` |
| Medium · long (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: medium) | Default | 2/6 (partial) | 6.29₽ | est. | 115.5 | 66 | ✅ | `oc_medium_long_ds41flash_medium_20261109` |
| Medium · long (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | Balanced | 2/6 (partial) | 94.45₽ | est. | 131.6 | 86 | — | `oc_medium_long_ds41flash_off_20261109` |
| Medium · long (20) | OpenClaw | GPT-5.5 (thinking: off) | Premium | 1/6 (partial) | 12 926.41₽ | est. | 43.2 | 209 | — | `oc_medium_long_gpt55_off_20261110` |
| Medium · long (20) | Pi | GPT-5.5 (thinking: off) | Premium | 1/6 (partial) | 16 102.59₽ | est. | 47.6 | 426 | — | `pi_medium_long_gpt55_off_20261110` |
| Medium · long (20) | Pi | Claude Sonnet 4.6 (thinking: off) | Premium | 1/6 (partial) | 55 280.97₽ | est. | 119.4 | 2718 | — | `pi_medium_long_sonnet46_off_20261113` |
| Medium · long (20) | OpenClaw | — | н/д | 0/6 (partial) | н/д | est. | 40.3 | 326 | — | `oc_m66-plain-long_20261113` |
| Medium · long (20) | OpenClaw | — | н/д | 0/6 (partial) | н/д | est. | 22.0 | 173 | — | `oc_m66-ultra-long_20261114` |
| Medium · long (20) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | н/д | 0/2 (partial) | н/д | est. | 45.6 | 4 | — | `oc_medium_long_ds41flash_off_20261108` |
| Medium · long (20) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: off) | н/д | 0/6 (partial) | н/д | est. | 40.3 | 326 | — | `oc_medium_long_giga35base_off_20261113` |
| Medium · long (20) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: ultra) | н/д | 0/6 (partial) | н/д | est. | 22.0 | 173 | — | `oc_medium_long_giga35base_ultra_20261114` |
| Medium · long (20) | OpenClaw | MiniMax-M3 (thinking: medium) | н/д | 0/6 (partial) | н/д | est. | 154.9 | 284 | — | `oc_medium_long_minimaxm3_medium_20261109` |
| Medium · long (20) | OpenClaw | MiniMax-M3 (thinking: off) | н/д | 0/6 (partial) | н/д | est. | 363.2 | 276 | — | `oc_medium_long_minimaxm3_off_20261109` |
| Medium · long (20) | Pi | DeepSeek-V4.1-Flash (thinking: n/a) | н/д | 0/3 (partial) | н/д | est. | 288.7 | 1650 | — | `pi_long_ds41flash_20261106` |
| Medium · long (20) | Pi | GigaChat 3.5 Reasoning (thinking: low) | н/д | 0/6 (partial) | н/д | est. | 62.2 | 1419 | — | `pi_medium_long_giga35_low_20261107` |
| Medium · long (20) | Pi | GigaChat 3 Pro (thinking: n/a) | н/д | 0/6 (partial) | н/д | est. | 23.5 | 1020 | — | `pi_medium_long_gigachat3pro_20261109` |
| Medium · long (20) | Pi | Claude Sonnet 4.6 (thinking: medium) | н/д | 0/6 (partial) | н/д | est. | 103.2 | 1004 | — | `pi_medium_long_sonnet46_medium_20261113` |
| Medium · feasibility (40) | OpenClaw | DeepSeek-V4.1-Flash (thinking: medium) ★ | Default | 39/40 | 12.65₽ | est. | 123.7 | 811 | ✅ | `oc_medium_feasibility_ds41flash_medium_20261108` |
| Medium · feasibility (40) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: off) | Default | 39/40 | 12.68₽ | est. | 161.8 | 727 | — | `dsh_medium_feasibility_ds41flash_off_20261111` |
| Medium · feasibility (40) | OpenClaw | DeepSeek-V4.1-Flash (thinking: high) | Default | 38/40 | 13.40₽ | est. | 105.0 | 783 | — | `oc_medium_feasibility_ds41flash_high_20261108` |
| Medium · feasibility (40) | OpenClaw | GPT-5.5 (thinking: off) | Balanced | 38/40 | 259.46₽ | est. | 61.9 | 473 | — | `oc_medium_feasibility_gpt55_off_20261110` |
| Medium · feasibility (40) | Pi | GPT-5.5 (thinking: off) | Balanced | 38/40 | 289.77₽ | est. | 125.9 | 712 | — | `pi_medium_feasibility_gpt55_off_20261110` |
| Medium · feasibility (40) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | Default | 36/40 | 15.91₽ | est. | 169.6 | 873 | — | `oc_medium_feasibility_ds41flash_off_20261108` |
| Medium · feasibility (40) | OpenClaw | MiniMax-M3 (thinking: off) | Balanced | 33/40 | 261.68₽ | est. | 266.4 | 989 | — | `oc_medium_feasibility_minimaxm3_off_20261109` |
| Medium · feasibility (40) | OpenClaw | MiniMax-M3 (thinking: medium) | Premium | 26/40 | 506.65₽ | est. | 385.8 | 1329 | — | `oc_medium_feasibility_minimaxm3_medium_20261109` |
| Medium · feasibility (40) | Pi | Claude Sonnet 4.6 (thinking: medium) | Premium | 21/40 | 453.55₽ | est. | 474.7 | 2770 | — | `pi_medium_feasibility_sonnet46_medium_20261113` |
| Medium · feasibility (40) | Pi | Claude Sonnet 4.6 (thinking: off) | Premium | 20/40 | 1 640.02₽ | est. | 250.3 | 3031 | — | `pi_medium_feasibility_sonnet46_off_20261113` |
| Medium · feasibility (40) | Pi | GigaChat 3.5 Reasoning (thinking: low) | Premium | 11/40 | 898.30₽ | est. | 517.1 | 2973 | — | `pi_medium_feasibility_giga35_low_20261107` |
| Medium · feasibility (40) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: off) | Balanced | 1/40 | 380.05₽ | est. | 49.0 | 279 | — | `oc_medium_feasibility_giga35base_off_20261113` |
| Medium · feasibility (40) | OpenClaw | GigaChat 3.5-432B-A28B (thinking: ultra) | н/д | 0/40 | н/д | est. | 62.6 | 369 | — | `oc_medium_feasibility_giga35base_ultra_20261114` |
| Medium · feasibility (40) | Pi | GigaChat 3 Pro (thinking: n/a) | н/д | 0/40 | н/д | est. | 15.0 | 1408 | — | `pi_medium_feasibility_gigachat3pro_20261109` |
| Light (391) | Hermes | — | Default | 387/391 | 20.09₽ | est. | 404.6 | н/д | — | `hermes_full_391` |

**Anti-patterns — переплата без выигрыша** (доминируемы более дешёвой конфигурацией с тем же/лучшим результатом):

- `Claude Sonnet 4.6 (thinking: off)` @ 55 280.97₽ (long-context) ← `dsh_medium_long_ds41flash_off_20261111`
- `GPT-5.5 (thinking: off)` @ 16 102.59₽ (long-context) ← `dsh_medium_long_ds41flash_off_20261111`
- `MiniMax-M3 (thinking: medium)` @ 506.65₽ (wave19-feasibility) ← `dsh_medium_feasibility_ds41flash_off_20261111`
- `GigaChat 3 Pro (thinking: n/a)` @ 59.81₽ (shot-list) ← `dsh_shotlist_ds41flash_max_20261118`
- `GPT-OSS-120B (thinking: n/a)` @ 46.22₽ (shot-list) ← `dsh_shotlist_ds41flash_max_20261118`
- `GLM-5.2 (thinking: off)` @ 32.80₽ (shot-list) ← `dsh_shotlist_ds41flash_max_20261118`
- `Claude Fable 5.1 (thinking: n/a)` @ 31.60₽ (shot-list) ← `dsh_shotlist_ds41flash_max_20261118`
- `Claude Fable 5 (thinking: n/a)` @ 27.46₽ (shot-list) ← `dsh_shotlist_ds41flash_max_20261118`
- `Claude Sonnet 4.5 (thinking: n/a)` @ 20.79₽ (shot-list) ← `dsh_shotlist_ds41flash_max_20261118`
- `Claude Opus 4.8 (thinking: n/a)` @ 15.60₽ (shot-list) ← `dsh_shotlist_ds41flash_max_20261118`
- `Claude Opus 5.5 (thinking: n/a)` @ 11.59₽ (shot-list) ← `dsh_shotlist_ds41flash_max_20261118`
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

1. Источник — `results/*.json`; ₽ премиум-лестницы — из биллинга провайдера **там, где есть**
   reconciled-сайдкар (`results/reconciled/`), иначе — оценка по тарифу (столбец `Basis`).
2. Классификация (harness/model/reasoning/suite) — общий модуль `scripts/fork_metadata.py`,
   поэтому стемы совпадают со строками опубликованного форка.
3. Фронт Парето — **внутри сравнимого набора** (`bucket × total` = одна task-set версия), по
   `pass_rate ↑` и `cost_per_solved ↓`; одиночный набор фронта не даёт; доминируемые → anti-patterns.
4. Правило «premium оправдан» (ADR R2-Q5): точка не доминируема **и** даёт ≥ +3 п.п. pass
   **или** закрывает ≥1 floor/boundary-задачу. На текущих данных таких premium нет.
5. Сравнение только внутри одной task-set версии/протокола; partial-покрытие = `unmeasured`.
6. Тиры — терцили ₽/решённую задачу **внутри сравнимого набора**, а не глобально; `★` —
   самый дешёвый фронт-выбор бакета (на полном наборе).
