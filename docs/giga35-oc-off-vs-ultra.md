# GigaChat3.5-432B-A28B — Shot-list vs Medium-66, off vs ultra

Model id (both modes): `custom-foundation-models-api-cloud-ru/ai-sage/GigaChat3.5-432B-A28B`
Harness: openclaw (`run_openclaw_bench.py`), branch `run/giga35-oc-base-off-vs-ultra`.

Note: the separate `…-A28B-Reasoning` model id was **retired** from the Cloud.ru catalog
(404 `model_not_found`). Reasoning is exercised as a MODE (`--thinking ultra`) on the base id.

## Matrix (2026-11-13 / 2026-11-14)

| Test | plain (`--thinking off`) | reasoning (`--thinking ultra`) |
|---|---|---|
| Shot-list 52 | **25/52** (48.1%) | **20/52** (38.5%) |
| Medium-66 (6+20+40) | **1/66** (1.5%) | **0/66** (0%) |
| ├ long (6) | 0/6 | 0/6 |
| ├ reflect (20) | 0/20 | 0/20 |
| └ feas (40) | 1/40 | 0/40 |

## Cost (₽)

| wave | plain | ultra |
|---|---|---|
| shot-list | 208.23 | 38.78 |
| m66-long | 142.22 | 30.87 |
| m66-refl | 216.64 | 17.40 |
| m66-feas | 380.05 | 78.16 |
| **total** | **947.14** | **165.21** |

## Takeaway
Enabling reasoning (`ultra`) on `GigaChat3.5-432B-A28B` did **not** help — it scored lower on
both tests than plain, at lower cost. Medium-66 remains near-zero in both modes (weak solver).

## Files
- plain: `results/oc_sl-plain_20261113.json`, `results/oc_m66-plain-{long,refl,feas}_20261113.json`
- ultra: `results/oc_sl-ultra_20261114.json`, `results/oc_m66-ultra-{long,refl,feas}_20261114.json`
