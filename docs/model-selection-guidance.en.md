<!-- Model & harness selection guidance (EN mirror).
     Auto-generated block is rebuilt by scripts/build_model_selection.py (--check/--lint). -->
# Model & Harness Selection Guidance — what × with what × when

Practical guidance for choosing a **harness × model** for `harness-bench-fast` tasks,
built **only from already-completed runs** (`results/*.json`). ₽ is **actual provider billing
where a reconciled sidecar exists**, otherwise a tariff estimate (see the `Basis` column).

> **RU-first:** [`model-selection-guidance.md`](model-selection-guidance.md).
> Method & decisions — [`../CONTEXT.md`](../CONTEXT.md) and
> [`adr/0006-model-selection-guidance.md`](adr/0006-model-selection-guidance.md).

## TL;DR

- **Default choice for the vast majority of tasks — DeepSeek-V4.1-Flash.** On Shot List it scores
  **100%** (OpenClaw) / 98% (DeepSeek Harness) at roughly **~1 ₽ per solved task**.
- **Premium models do not pay off on current data.** The best premium (Claude Sonnet 4.5, 98.1%)
  costs **≈25× more** than an equal-scoring DeepSeek. No floor/boundary task is closed by premium.
- **The harness matters as much as the model**: the same model scores differently across harnesses.
  Choose the **(harness × model) pair**, not "a model".
- **Reasoning level is a real but costly lever (DeepSeek Harness).** DeepSeek-V4.1-Flash moves from
  **51/52** at `thinking: off`/`medium` (~0.85 ₽/solved) to **52/52 at `thinking: max`** — at ~5× the
  cost (≈4.18 ₽/solved, 84 min). `medium` is the balance point; reach for `max` only when that last
  task matters.
- **Not covered:** `wave21 (hard-coupled)` is not measured at all; the long wave only partially
  (6/20); Light-391 has 3 runs. Such cells are `unmeasured`, never "fail".

## Recommendation matrix (auto-sync)

The block below is generated from `results/*.json` by `scripts/build_model_selection.py`.
Do not edit by hand — change the data/script and rebuild.

<!-- MSEL:BEGIN -->
Auto-sync: 90 rows (incl. 0/N) · tiers per bucket by ₽/solved · ✅ — Pareto point (within one task set)

| Bucket | Harness | Model | Tier | Result | Cost/solved | Basis | Min | Steps | Pareto | Stem |
| --- | --- | --- | :-: | ---: | ---: | :-: | ---: | ---: | :-: | --- |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: medium) | Default | 52/52 | 1.07₽ | est. | 40.6 | 224 | ✅ | `oc_shotlist_ds41flash_medium_20261107` |
| Shot List (52) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: off) ★ | Default | 51/52 | 0.84₽ | est. | 19.1 | 266 | ✅ | `dsh_shotlist_ds41flash_off_20261111` |
| Shot List (52) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: max) | Default | 52/52 | 4.18₽ | est. | 84.1 | 438 | — | `dsh_shotlist_ds41flash_max_20261118` |
| Shot List (52) | DeepSeek Harness | DeepSeek-V4.1-Flash (thinking: medium) | Default | 51/52 | 0.88₽ | est. | 22.4 | 269 | — | `dsh_shotlist_ds41flash_medium_20261117` |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: high) | Default | 51/52 | 1.06₽ | est. | 40.3 | 244 | — | `oc_shotlist_ds41flash_high_20261107` |
| Shot List (52) | Pi | Claude Sonnet 4.5 (thinking: n/a) | Premium | 51/52 | 20.79₽ | billing | 25.7 | 1352 | — | `pi_shotlist_sonnet45_20261114` |
| Shot List (52) | OpenClaw | GPT-5.5 (thinking: off) | Premium | 51/52 | 49.47₽ | est. | 30.5 | 267 | — | `oc_shotlist_gpt55_off_20261110` |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: off) | Default | 50/52 | 1.01₽ | est. | 37.9 | 244 | — | `oc_shotlist_ds41flash_off_20261107` |
| Shot List (52) | OpenClaw | DeepSeek-V4.1-Flash (thinking: n/a) | Default | 50/52 | 4.43₽ | est. | 47.3 | 237 | — | `oc_shotlist_ds41flash` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: medium) | Balanced | 50/52 | 14.98₽ | est. | 14.3 | 1056 | — | `pi_sonnet46_shotlist_medium_20261114` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: n/a) | Balanced | 50/52 | 15.16₽ | est. | 14.6 | 1060 | — | `pi_sonnet46_shotlist_20261113` |
| Shot List (52) | OpenClaw | GPT-5.5 (thinking: high) | Premium | 50/52 | 58.07₽ | est. | 37.0 | 334 | — | `oc_shotlist_gpt55_high_20261110` |
| Shot List (52) | Pi | DeepSeek-V4.1-Flash (thinking: n/a) | Default | 49/52 | 0.85₽ | est. | 21.3 | 330 | — | `pi_shotlist_ds41flash` |
| Shot List (52) | Pi | GLM-5.3 (thinking: n/a) | Default | 49/52 | 4.51₽ | est. | 64.9 | 349 | — | `pi_shotlist_glm53` |
| Shot List (52) | Hermes | DeepSeek-V4.1-Flash (thinking: off) | Default | 49/52 | 5.90₽ | est. | 22.0 | 221 | — | `hermes_shotlist_ds41flash_off_20261111` |
| Shot List (52) | Pi | GLM-5.2 (thinking: off) | Balanced | 49/52 | 7.69₽ | est. | 102.8 | 388 | — | `pi_shotlist_glm52_thinking_off` |
| Shot List (52) | Pi | GPT-5.5 (thinking: high) | Balanced | 49/52 | 11.61₽ | est. | 11.6 | 299 | — | `pi_shotlist_gpt55_high_20261110` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: n/a) | Balanced | 49/52 | 13.33₽ | billing | 14.0 | 996 | — | `pi_shotlist_sonnet46_20261114` |
| Shot List (52) | Pi | Claude Opus 4.8 (thinking: n/a) | Balanced | 49/52 | 15.60₽ | billing | 12.3 | 808 | — | `pi_shotlist_opus48_20261114` |
| Shot List (52) | Pi | Claude Sonnet 4.6 (thinking: off) | Balanced | 49/52 | 16.71₽ | est. | 12.8 | 1152 | — | `pi_shotlist_sonnet46_off_20261113` |
| Shot List (52) | Pi | GPT-5.5 (thinking: off) | Balanced | 48/52 | 10.35₽ | est. | 9.0 | 300 | — | `pi_shotlist_gpt55_off_20261110` |
| Shot List (52) | Pi | Claude Opus 5.5 (thinking: n/a) | Balanced | 48/52 | 11.59₽ | billing | 13.3 | 720 | — | `pi_shotlist_opus55_20261114` |
| Shot List (52) | OpenClaw | MiniMax-M3 (thinking: off) | Premium | 48/52 | 20.25₽ | est. | 295.4 | 312 | — | `oc_shotlist_minimaxm3_off_20261109` |
| Shot List (52) | OpenClaw | MiniMax-M3 (thinking: medium) | Premium | 47/52 | 23.81₽ | est. | 306.9 | 328 | — | `oc_shotlist_minimaxm3_medium_20261109` |
| Shot List (52) | OpenClaw | GLM-5.2 (thinking: off) | Premium | 47/52 | 27.69₽ | est. | 231.9 | 247 | — | `oc_shotlist_thinking_off` |
| Shot List (52) | Pi | Claude Fable 5.1 (thinking: n/a) | Premium | 47/52 | 31.60₽ | billing | 19.6 | 752 | — | `pi_shotlist_fable51_20261114` |
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
| Shot List (52) | Hermes | GPT-OSS-120B (thinking: n/a) | Default | 28/52 | 5.19₽ | est. | 37.9 | 427 | — | `hermes_shotlist_oss120b` |
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

**Anti-patterns — paying without winning** (dominated by a cheaper config with equal/better result):

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

## How to read it (situation recommendations)

- **`shot-list`** — the only densely comparable bucket (4 harnesses, full set).
  Action: **Default = DeepSeek-V4.1-Flash** on OpenClaw or DeepSeek Harness.
- **`medium-reflect` / `long-context` / `wave19-feasibility`** — DeepSeek stays on the Pareto
  frontier; the long bucket is partial → low-confidence decisions.
- **`light-391`** — no single-harness comparable pair → `unmeasured` only.
- **`hard-coupled (wave21)`** — not measured at all → no recommendation until runs exist.

## Coverage & confidence

- `pass_rate` = passed/total from JSON (re-derived to catch stale fields).
- `cost_per_solved` = ₽ / solved tasks — the price axis and tiers.
- `coverage.full` = whether the full suite was measured; `coverage.version` = task-set version.
- Confidence: `high` (≥2 runs), `medium` (1 run, full suite), `low` (1 run, partial suite).

## Methodology

1. Source — `results/*.json`; premium-ladder ₽ come from provider billing **where a reconciled
   sidecar exists** (`results/reconciled/`), otherwise a tariff estimate (the `Basis` column).
2. Classification (harness/model/reasoning/suite) — shared `scripts/fork_metadata.py`, so stems
   match the published fork rows.
3. The Pareto frontier is computed **within a comparable set** (`bucket × total` = one task-set
   version), over `pass_rate ↑` and `cost_per_solved ↓`; a lone set yields no frontier; dominated → anti-patterns.
4. "Premium justified" rule (ADR R2-Q5): not dominated **and** ≥ +3 pp pass **or** closes ≥1
   floor/boundary task. No premium qualifies on current data.
5. Comparison only within one task-set version/protocol; partial coverage = `unmeasured`.
6. Tiers are terciles of ₽/solved **within a comparable set**, not global; `★` marks the
   cheapest frontier pick of a bucket (on a full set).
