# Billing reconciliation — Cloud.ru Foundation Models vs our benchmark runs (2026-11-15)

OpenSpec change: `add-billing-reconciliation` · Workboard `e07ddcdc-2cb8-40d7-8bd6-5311b2658456`
Tool: `scripts/reconcile_billing.py` · Tests: `tests/test_reconcile_billing.py`
Input exports: `/tmp/bill_old.csv`, `/tmp/bill_new.csv` (Cloud.ru "Raw activity rows (MSK)").

## 1. TL;DR

| Finding | Value |
|---|---|
| `kap-hermes` actual (deduped) | **5 782.48 ₽** (window `2026-11-14 17:48:50 … 19:00:29`, 6 anthropic models) |
| `kap-hermes` published (pre-fix) | ≈ **17 406 ₽** → **×3.01** inflated |
| Root cause | Pi runner counted each assistant `usage` **3×**; fixed in main `3baebb6` |
| `kap-openclaw` **naive** (overlapping exports, NOT deduped) | 102 765.47 ₽ (sonnet-4.6 = **95 134.32 ₽**) |
| `kap-openclaw` **actual (deduped)** | **51 297.86 ₽** (sonnet-4.6 = **47 478.09 ₽**) |
| sonnet-4.6 spend attributable to a benchmark artifact | **0 ₽** — no `oc_*` sonnet artifact exists; 11-14 OpenClaw-harness artifacts are *GigaChat 3.5* |
| sonnet-4.6 attributed to gateway/agent own sessions | **≈ 100 % (47 478.09 ₽ deduped / 95 134.32 ₽ naive)** — inferred (window + key + concurrency), not itemizable |

> ⚠️ The headline `102 765 ₽` / `95 134 ₽` figures quoted in the proposal are the
> **double-counted** numbers produced by summing two *overlapping* exports without
> dedup. Deduplicated, the same key/models are **51 297.86 ₽ / 47 478.09 ₽**.

## 2. Export format and the dedup rule

CSV, Russian-MSK timestamps, header:

```
"date","department","api_key","model","input_tokens","output_tokens","cost"
2026-11-14 20:59:53,oait,oait,google/gemini-2.5-flash,285,57,0.04
```

`cost` is the **actual ₽** charged for that activity row (verified elsewhere to track
Cloud.ru tariffs: `cost ≈ in·in_rate + out·out_rate`).

**Exports are windows and they overlap.** The two exports here are not disjoint:

| export | full window (all keys) | `kap-openclaw` rows | `kap-hermes` rows |
|---|---|---|---|
| `bill_old.csv` | `2026-11-13 21:53:54 … 2026-11-14 20:59:53` | 2 567 (`02:11:11 … 20:22:39`) | 1 344 (`17:48:50 … 19:00:29`) |
| `bill_new.csv` | `2026-11-14 10:30:44 … 2026-11-15 09:58:41` | 2 631 (`17:32:02 … 11-15 03:02:20`) | 1 344 (identical rows) |

For `kap-hermes` the two files are **row-for-row identical** → a naive concat gives
`2 × 5 782.48 = 11 564.96 ₽`. For `kap-openclaw`, 2 561 of 2 567 old rows reappear in
new (only 5 old-only / 69 new-only rows) → naive concat ≈ 2× the truth.

**Rule:** dedupe by the **full row tuple**
`(date, department, api_key, model, input_tokens, output_tokens, cost)` **before any
summing** (cost compared as a normalised Decimal so `0.040 == 0.04`). This is
implemented in `row_key()`/`dedupe()` and covered by
`test_dedupe_overlapping_exports_counts_each_row_once`.

## 3. `kap-hermes` — reconciliation vs our published figure

`python3 scripts/reconcile_billing.py --key kap-hermes /tmp/bill_old.csv /tmp/bill_new.csv`:

```
rows: 1344   window: 2026-11-14 17:48:50 .. 2026-11-14 19:00:29
tokens: in=4,502,686 out=326,860

api_key          model                                                  in         out       cost ₽
kap-hermes       anthropic/claude-fable-5.1                        596,466      47,689     1,485.05
kap-hermes       anthropic/claude-fable-5                          509,403      40,147     1,263.14
kap-hermes       anthropic/claude-sonnet-4.5                     1,435,313      73,482     1,060.50
kap-hermes       anthropic/claude-opus-4.8                         674,166      44,436       764.49
kap-hermes       anthropic/claude-sonnet-4.6                       738,844      74,265       652.95
kap-hermes       anthropic/claude-opus-5.5                         548,494      46,841       556.35

TOTAL: 5,782.48 ₽
```

This window is the **Premium Anthropic ladder** (Shot List 52) run on 2026-11-14
evening — six premium anthropic models, matching `pi_shotlist_{sonnet45,sonnet46,
opus48,opus55,fable5,fable51}_20261114.json`.

**×3 reconciliation.** Our published figure for the same runs was ≈ **17 406 ₽**;
provider actual (deduped) = **5 782.48 ₽** → `17406 / 5782.48 = 3.01`.

Root cause (already fixed): the Pi runner counted each assistant `usage` **three
times** (`message_end` + `turn_end` + `agent_end.messages[]`), so published
token/₽ totals — and therefore `estimated_cost_rub` — were ~3× the truth.
Fix landed on **main commit `3baebb6`** (`fix(cost): Pi single-count + cache buckets;
reconcile gpt-5.5 with provider billing`), which de-times-3 the Pi usage and adds
explicit cache buckets. No action needed on this change: the provider export is the
ground truth (5 782.48 ₽) and the runner now matches it.

## 4. `kap-openclaw` — attribution of the sonnet-4.6 spend

### 4.1 What the proposal quoted (naive, double-counted)

Summing both exports **without** dedup for `kap-openclaw` gives **102 765.47 ₽**, of
which `anthropic/claude-sonnet-4.6` = **95 134.32 ₽**
(`in 156 138 302 / out 1 064 924`). This is exactly 2× the deduped truth and matches
the proposal numbers — i.e. those figures are an **artifact of the overlapping
exports**, not real spend.

### 4.2 Actual (deduped) breakdown by model

`python3 scripts/reconcile_billing.py --key kap-openclaw /tmp/bill_old.csv /tmp/bill_new.csv`:

```
rows: 2635   window: 2026-11-14 02:11:11 .. 2026-11-15 03:02:20
tokens: in=116,498,600 out=831,797

api_key          model                                                  in         out       cost ₽
kap-openclaw     anthropic/claude-sonnet-4.6                    77,921,472     531,765    47,478.09
kap-openclaw     ai-sage/GigaChat3.5-432B-A28B                  38,386,658     229,478     3,752.51
kap-openclaw     openai/gpt-5.5                                      3,040       6,465        35.71
kap-openclaw     deepseek-ai/DeepSeek-V4.1-Flash                   181,306      57,425        21.73
kap-openclaw     google/gemini-3.1-pro-preview                       2,294       3,019         6.97
kap-openclaw     zai-org/GLM-5.2                                     3,830       3,645         2.85

TOTAL: 51,297.86 ₽
```

sonnet-4.6 = **92.6 %** of the key's actual spend (47 478.09 / 51 297.86).

### 4.3 Breakdown by day / hour (sonnet-4.6)

Deduped, the whole sonnet-4.6 spend is on a **single evening window**:

| day | sonnet-4.6 ₽ (deduped) | naive ₽ |
|---|---|---|
| 2026-11-14 | 47 478.09 | 95 134.32 |
| 2026-11-15 | 0.00 | 0.00 |

| hour (MSK) | sonnet-4.6 ₽ (deduped) | naive ₽ |
|---|---|---|
| 2026-11-14 17:00 | 13 254.83 | 26 509.66 |
| 2026-11-14 18:00 | 34 223.26 | 68 624.66 |

Window: **`2026-11-14 17:38:48 … 18:43:11`** (930 unique rows, 768 distinct seconds,
peak ≈ 95 calls/min, mean input ≈ 84 k tokens/call). That concurrency is a *fleet*
of simultaneous calls, not one interactive session.

### 4.4 Is it a benchmark run? (artifacts check)

* **No `oc_*` (OpenClaw-harness) sonnet artifact exists** anywhere in `results/` or
  `runs/`. Every OpenClaw-harness artifact dated 2026-11-14 uses
  `ai-sage/GigaChat3.5-432B-A28B`:
  `oc_m66-ultra-{feas,long,refl}_20261114`, `oc_medium_*_giga35_ultra_20261114`,
  `oc_sl-ultra_20261114` (their `estimated_cost_rub` sum ≈ **330 ₽**).
* All sonnet-4.6 artifacts in `results/` are **Pi-harness** (`pi_*`) and bill to the
  **`kap-hermes`** key, not `kap-openclaw`
  (`pi_shotlist_sonnet46_20261114`, `pi_sonnet46_shotlist*`, `pi_medium_*_sonnet46_*`).
* The `kap-openclaw` **GigaChat 3.5** spend (3 752.51 ₽, window
  `17:34:17 … 20:21:54`) *is* benchmark-shaped and matches the `oc_*_giga35_ultra`
  runs by model and timing.
* Therefore **0 ₽ of the sonnet-4.6 spend is attributable to any benchmark artifact.**

### 4.5 Comparison with the gateway/agent's own usage

The window coincides with the agent's own orchestration of the 2026-11-14 evening
benchmark wave: `kap-hermes` (Pi premium ladder) runs `17:48:50 … 19:00:29` — almost
exactly the sonnet window `17:38:48 … 18:43:11`. `kap-openclaw` is the **gateway/agent
key** (OpenClaw CLI + agent sessions); the Pi harness runs under `kap-hermes`. So the
sonnet-4.6 calls that overlap the wave but bill to `kap-openclaw` are the agent's own
(fleet/subagent and judging) LLM calls around the run, not the harness run itself.

Session-level itemisation is not available from the provider export (it carries no
session id) and the live session store is not readable from this worktree, so the
"own sessions" split is an **inference** from key identity + timing + concurrency.

### 4.6 Fractions (sonnet-4.6, `kap-openclaw`)

| bucket | ₽ (deduped) | ₽ (naive) | share |
|---|---|---|---|
| benchmark artifact (`oc_*`) | 0.00 | 0.00 | 0 % |
| gateway/agent own sessions (inferred) | 47 478.09 | 95 134.32 | ≈ 100 % |
| strictly unexplained (no artifact/session proof) | 47 478.09 | 95 134.32 | 100 % |

Net: the sonnet-4.6 spend is **not** benchmark; it is gateway/agent consumption during
the 2026-11-14 evening wave. Because it cannot be tied to an individual session, treat
it as "own sessions (leading hypothesis) / formally unexplained".

## 5. Reproduce

```bash
python3 scripts/reconcile_billing.py --key kap-hermes   /tmp/bill_old.csv /tmp/bill_new.csv   # → 5 782.48 ₽
python3 scripts/reconcile_billing.py --key kap-openclaw /tmp/bill_old.csv /tmp/bill_new.csv   # → 51 297.86 ₽
python3 scripts/reconcile_billing.py --key kap-openclaw --model anthropic/claude-sonnet-4.6 --json \
        /tmp/bill_old.csv /tmp/bill_new.csv                                                    # → 47 478.09 ₽
python3 scripts/reconcile_billing.py --compare results/ --model anthropic/claude-sonnet-4.6 \
        /tmp/bill_old.csv /tmp/bill_new.csv
python3 -m pytest tests/test_reconcile_billing.py -q
npx -y @fission-ai/openspec@1.16.0 validate --all --strict
```
