#!/usr/bin/env python3
"""Regression: gpt-5.5 run ₽-cost must reconcile with the provider export.

Context (change ``fix-bench-token-accounting``): the published gpt-5.5 rows
understated billing because

  * the Pi runner counted each assistant ``usage`` three times
    (``message_end`` + ``turn_end`` + ``agent_end.messages[]``) and dropped the
    ``cacheRead``/``cacheWrite`` buckets, and
  * the OpenClaw runner re-derived the cache bucket instead of reading the
    envelope's explicit ``cacheRead``.

Cloud.ru bills gpt-5.5 as ``cost = input_tokens*854 + output_tokens*5124``
(verified +-0.02% against the export), where ``input_tokens`` is the *full*
input including cached prefix — i.e. cached input is billed at the INPUT rate.

The export is split by billing account: the Pi wave was billed under ``oait``
and the OpenClaw wave under ``kap-openclaw`` (the runner's Pi container uses a
different key than the OpenClaw CLI).  The committed fixture is a truncated
slice carrying both accounts, so the reconciliation is asserted per account.

Run: python3 -m pytest tests/test_cost_gpt55_reconciliation.py -q
"""
import csv
import glob
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gpt55_provider_slice.csv"

# gpt-5.5 provider rates (₽ / 1M tokens), verified against the export.
RATE_IN = 854.0
RATE_OUT = 5124.0
G56 = "gpt-5.5"          # model id
FILE_TOKEN = "gpt55"      # artifact filename token

# Which billing account each harness's artifacts map to (see docstring).
ACCOUNT_FOR_HARNESS = {"pi": "oait", "openclaw": "kap-openclaw"}
# harness -> artifact filename prefix
PREFIX_FOR_HARNESS = {"pi": "pi", "openclaw": "oc"}


def _fixture_totals(api_key):
    rows = [r for r in csv.DictReader(FIXTURE.open(encoding="utf-8")) if r["api_key"] == api_key]
    return (
        sum(int(r["input_tokens"]) for r in rows),
        sum(int(r["output_tokens"]) for r in rows),
        sum(float(r["cost"]) for r in rows),
    )


def _artifacts(prefix):
    files = sorted(glob.glob(str(REPO / "results" / f"{prefix}_*{FILE_TOKEN}*20261110.json")))
    return [json.loads(Path(f).read_text(encoding="utf-8")) for f in files]


def _billed_rub(inp, out, cache_read, cache_write=0):
    """Provider billing model: cached input billed at the INPUT rate."""
    return (inp + cache_read + cache_write) / 1e6 * RATE_IN + out / 1e6 * RATE_OUT


def _buckets(payload):
    s = payload["summary"]
    return (
        s.get("total_input_tokens", 0),
        s.get("total_output_tokens", 0),
        s.get("total_cache_read_tokens", 0),
        s.get("total_cache_write_tokens", 0),
    )


def test_fixture_rate_model_reproduces_provider_cost():
    for key in ACCOUNT_FOR_HARNESS.values():
        inp, out, cost = _fixture_totals(key)
        assert inp and out, f"fixture missing rows for {key}"
        recomputed = _billed_rub(inp, out, 0)
        assert abs(recomputed - cost) / cost <= 0.005, (
            f"{key}: cost={cost:.2f} != input*854+output*5124={recomputed:.2f}")


def test_pi_gpt55_artifacts_reconcile_with_provider_within_1pct():
    """The Pi wave (the runner that had the x3 + dropped-cache bug) must match
    its provider account within +-1% once cache buckets are recorded."""
    _, _, provider_cost = _fixture_totals(ACCOUNT_FOR_HARNESS["pi"])
    ours = sum(_billed_rub(*_buckets(a)) for a in _artifacts("pi"))
    rel = (ours - provider_cost) / provider_cost
    assert abs(rel) <= 0.01, (
        f"Pi gpt-5.5 {ours:.2f} ₽ vs provider {provider_cost:.2f} ₽ ({rel:+.2%})")


def test_all_gpt55_artifacts_reconcile_with_combined_provider():
    """Both waves combined must track the combined provider accounts closely
    (residual = runs not captured in the published artifacts)."""
    combined = sum(_fixture_totals(k)[2] for k in ACCOUNT_FOR_HARNESS.values())
    ours = 0.0
    for _h, prefix in PREFIX_FOR_HARNESS.items():
        ours += sum(_billed_rub(*_buckets(a)) for a in _artifacts(prefix))
    assert abs(ours - combined) / combined <= 0.10, (
        f"combined {ours:.2f} ₽ vs provider {combined:.2f} ₽")


def test_summary_cost_matches_recorded_buckets():
    """estimated_cost_rub must be derived from the recorded buckets (no
    re-derivation, no dropped cache bucket)."""
    for prefix in PREFIX_FOR_HARNESS.values():
        for art in _artifacts(prefix):
            s = art["summary"]
            expected = _billed_rub(*_buckets(art))
            assert abs(s["estimated_cost_rub"] - expected) < 0.05, (
                f"{prefix}: {s['estimated_cost_rub']} != {expected:.2f}")
            # invariant: total = input + output + cacheRead + cacheWrite
            assert s["total_tokens"] == (
                s["total_input_tokens"] + s["total_output_tokens"]
                + s["total_cache_read_tokens"] + s["total_cache_write_tokens"]), prefix


def test_cache_buckets_are_recorded_not_dropped():
    for prefix in PREFIX_FOR_HARNESS.values():
        for art in _artifacts(prefix):
            assert art["summary"]["total_cache_read_tokens"] > 0, prefix
            for t in (art.get("results") or art.get("tasks") or []):
                assert t.get("agent_cache_read_tokens") is not None, prefix
