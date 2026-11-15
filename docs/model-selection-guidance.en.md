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
Auto-sync: 72 measured runs · tiers by ₽/solved (p33/p66 = 12.87/49.47₽)

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

**Anti-patterns — paying without winning** (dominated by a cheaper config with equal/better result):

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
