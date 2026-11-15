"""Regression: summary ₽ cost must price the cache-read bucket.

Before the fix the runner summed ``agent_cache_read_tokens`` (a key the task
entries never store), so cache reads — the bulk of prompt tokens on long runs —
billed as 0 and every DeepSeek-V4.1-Flash row was understated.
"""
import json
from pathlib import Path

from harness_bench.cost_resolver import compute_cost_rub, resolve_model_rates

ART = (
    Path(__file__).resolve().parents[1]
    / "results"
    / "oc_medium_feasibility_ds41flash_off_20261108.json"
)
# Pinned rates for the fixture's model so the assertion is deterministic in CI
# (CI has no ~/.openclaw/cloudru-prices.json, so the resolver would otherwise fall
# back to unrelated GLM-5.2 rates and the comparison would be meaningless).
RATES_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cloudru-prices-ds41flash.json"
MODEL = "custom-foundation-models-api-cloud-ru/deepseek-ai/DeepSeek-V4.1-Flash"


def test_summary_prices_cache_read_bucket():
    summary = json.loads(ART.read_text(encoding="utf-8"))["summary"]
    inp = summary["total_input_tokens"]
    out = summary["total_output_tokens"]
    cache = summary["total_tokens"] - inp - out
    assert cache > 0, "fixture no longer has a cache bucket"
    rates = resolve_model_rates(MODEL, cache_path=RATES_FIXTURE)
    expected = compute_cost_rub(inp, out, cache, 0, rates)
    assert abs(summary["estimated_cost_rub"] - expected) < 0.01, (
        f"estimated_cost_rub={summary['estimated_cost_rub']} != {expected:.2f}; "
        "cache-read bucket omitted"
    )
