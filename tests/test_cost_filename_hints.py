"""Regression: resolver must infer the model from the artifact filename.

Pi/OpenClaw artifacts sometimes expose no ``model`` field at all
(``pi_shotlist_gigachat3pro.json``).  Before the fix the resolver had no filename
hint, so such a run was silently priced with the GLM-5.2 fallback.  The last-resort
filename hint map must resolve ``gigachat3pro`` -> ``GigaChat/GigaChat-3-Pro``.
"""

from __future__ import annotations

import json

import pytest

from harness_bench.cost_resolver import resolve_model_rates

# Hermetic catalog cache (values verified against the live Cloud.ru catalog).
_CATALOG_CACHE = {
    "fetched_at": "2026-11-09T00:00:00+00:00",
    "models": {
        "GigaChat/GigaChat-3-Pro": {
            "input": 73.03, "output": 176.39, "cache_read": 0.0, "is_billable": True,
        },
        "deepseek-ai/DeepSeek-V4.1-Flash": {
            "input": 64.94, "output": 194.81, "cache_read": 6.5, "is_billable": True,
        },
    },
}


@pytest.fixture(autouse=True)
def _hermetic_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cloudru-prices.json"
    cache.write_text(json.dumps(_CATALOG_CACHE), encoding="utf-8")
    monkeypatch.setenv("CLOUDRU_PRICES_CACHE", str(cache))


def test_filename_hint_gigachat3pro():
    """Empty model id + gigachat3pro filename -> GigaChat-3-Pro, not GLM-5.2 fallback."""
    rates = resolve_model_rates("", filename="pi_shotlist_gigachat3pro.json")
    assert rates["input"] == pytest.approx(73.03)
    assert rates["output"] == pytest.approx(176.39)
    assert rates["cacheRead"] == pytest.approx(0.0)


def test_filename_hint_ds41flash():
    rates = resolve_model_rates("", filename="pi_medium_long_ds41flash_20261108.json")
    assert rates["input"] == pytest.approx(64.94)
    assert rates["output"] == pytest.approx(194.81)


def test_explicit_model_wins_over_filename_hint():
    """A concrete model id resolves normally; the filename hint is a last resort."""
    rates = resolve_model_rates(
        "custom-foundation-models-api-cloud-ru/deepseek-ai/DeepSeek-V4.1-Flash",
        filename="pi_shotlist_gigachat3pro.json",
    )
    assert rates["input"] == pytest.approx(64.94)


def test_unknown_filename_still_falls_back(capsys):
    rates = resolve_model_rates("", filename="pi_shotlist_mystery.json")
    assert rates["input"] == pytest.approx(239.12)
    assert "WARNING" in capsys.readouterr().err
