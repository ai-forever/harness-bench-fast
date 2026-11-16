<!-- Model & harness selection guidance (EN mirror).
     Auto-generated block is rebuilt by scripts/build_model_selection.py (--check/--lint). -->
# Model & Harness Selection Guidance — what × with what × when

Practical guidance for choosing a **harness × model** for `harness-bench-fast` tasks,
built **only from already-completed runs** (`results/*.json`). For premium models the
₽ figures are **actual provider billing**, not tariff estimates.

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
- **Not covered:** `wave21 (hard-coupled)` is not measured at all; the long wave only partially
  (6/20); Light-391 has 3 runs. Such cells are `unmeasured`, never "fail".

## Recommendation matrix (auto-sync)

The block below is generated from `results/*.json` by `scripts/build_model_selection.py`.
Do not edit by hand — change the data/script and rebuild.

<!-- MSEL:BEGIN -->
Auto-sync: 88 rows (incl. 0/N) · tiers per bucket by ₽/solved · ✅ — Pareto point (within one task set)

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

**Anti-patterns — paying without winning** (dominated by a cheaper config with equal/better result):

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

1. Source — `results/*.json`; premium-ladder ₽ come from provider billing
   (`results/reconciled/`, see `reconcile-premium-ladder-cost`).
2. Classification (harness/model/reasoning/suite) — shared `scripts/fork_metadata.py`, so stems
   match the published fork rows.
3. The Pareto frontier is computed **within each bucket** (comparable denominators), over
   `(pass ↑, cost_per_solved ↓)`; dominated points become anti-patterns.
4. "Premium justified" rule (ADR R2-Q5): not dominated **and** ≥ +3 pp pass **or** closes ≥1
   floor/boundary task. No premium qualifies on current data.
5. Comparison only within one task-set version/protocol; partial coverage = `unmeasured`.
