"""Tests for the Cloud.ru live price source.

Covers:
- catalog parsing (``metadata.{prompt,generated,cache_read}_tokens_cost``),
- cache write/read and TTL expiry (``scripts/sync_cloudru_prices.py``),
- failure keeps the previous cache and exits non-zero,
- resolver precedence: catalog cache > openclaw.json cost block > fallback,
- verified live values: GigaChat3.5 -> 96.22/288.6/cache 0,
  DeepSeek-V4.1-Flash -> 64.94/194.81/6.5,
- unknown model -> fallback + WARNING.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harness_bench.cost_resolver import resolve_model_rates  # noqa: E402
from scripts import sync_cloudru_prices as sync  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# A trimmed live catalog payload (values verified 2026-11-08).
_CATALOG_PAYLOAD = {
    "data": [
        {
            "id": "ai-sage/GigaChat3.5-432B-A28B-Reasoning",
            "metadata": {
                "prompt_tokens_cost": 96.22,
                "generated_tokens_cost": 288.6,
                "is_billable": True,
            },
        },
        {
            "id": "deepseek-ai/DeepSeek-V4.1-Flash",
            "metadata": {
                "prompt_tokens_cost": 64.94,
                "generated_tokens_cost": 194.81,
                "cache_read_tokens_cost": 6.5,
                "is_billable": True,
            },
        },
    ]
}

_CONFIG = {
    "models": {
        "providers": {
            "custom-foundation-models-api-cloud-ru": {
                "models": [
                    {
                        "id": "deepseek-ai/DeepSeek-V4.1-Flash",
                        # obviously different from the catalog so precedence is visible
                        "cost": {"input": 1.0, "output": 2.0, "cacheRead": 3.0, "cacheWrite": 4.0},
                    },
                    {
                        "id": "some/Only-In-Config",
                        "cost": {"input": 10.0, "output": 20.0, "cacheRead": 0.5, "cacheWrite": 0.0},
                    },
                ]
            }
        }
    }
}


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture()
def config_path(tmp_path):
    return str(_write_json(tmp_path / "openclaw.json", _CONFIG))


@pytest.fixture()
def cache_path(tmp_path):
    """A catalog cache containing the two verified models."""
    cache = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "models": sync.parse_catalog(_CATALOG_PAYLOAD),
    }
    return str(_write_json(tmp_path / "cloudru-prices.json", cache))


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _patch_httpx(monkeypatch, payload=None, exc=None):
    """Replace httpx.Client with a stub returning *payload* or raising *exc*."""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            if exc is not None:
                raise exc
            return _FakeResponse(payload)

    monkeypatch.setattr("httpx.Client", _FakeClient)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("CLOUD_RU_API_KEY", "test-key")


# ---------------------------------------------------------------------------
# 1. Catalog parsing
# ---------------------------------------------------------------------------

def test_parse_catalog_extracts_rates_and_billable():
    models = sync.parse_catalog(_CATALOG_PAYLOAD)
    assert models["deepseek-ai/DeepSeek-V4.1-Flash"] == {
        "input": 64.94, "output": 194.81, "cache_read": 6.5, "is_billable": True,
    }
    gg = models["ai-sage/GigaChat3.5-432B-A28B-Reasoning"]
    assert gg["input"] == pytest.approx(96.22)
    assert gg["output"] == pytest.approx(288.6)
    assert gg["cache_read"] == 0.0  # missing cache field -> 0


def test_parse_catalog_handles_missing_metadata_and_models_key():
    payload = {"models": [{"id": "x/y"}, {"no_id": True}, "garbage"]}
    models = sync.parse_catalog(payload)
    assert models == {"x/y": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "is_billable": False}}


# ---------------------------------------------------------------------------
# 2. Sync: cache write / read / TTL
# ---------------------------------------------------------------------------

def test_sync_writes_cache(monkeypatch, tmp_path):
    _patch_httpx(monkeypatch, payload=_CATALOG_PAYLOAD)
    out = tmp_path / "cloudru-prices.json"
    rc = sync.main(["--out", str(out), "--force"])
    assert rc == 0
    data = json.loads(out.read_text())
    assert "fetched_at" in data
    assert data["models"]["deepseek-ai/DeepSeek-V4.1-Flash"]["cache_read"] == pytest.approx(6.5)


def test_sync_ttl_skips_fetch_when_fresh(monkeypatch, tmp_path):
    out = _write_json(tmp_path / "cloudru-prices.json", {
        "fetched_at": datetime.now(UTC).isoformat(),
        "models": {"a/b": {"input": 1.0, "output": 2.0, "cache_read": 0.0, "is_billable": True}},
    })
    _patch_httpx(monkeypatch, exc=AssertionError("network must not be touched"))
    rc = sync.main(["--out", str(out), "--ttl", "3600"])  # no --force
    assert rc == 0
    assert json.loads(out.read_text())["models"] == {"a/b": {"input": 1.0, "output": 2.0, "cache_read": 0.0, "is_billable": True}}


def test_sync_ttl_expiry_triggers_fetch(monkeypatch, tmp_path):
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    out = _write_json(tmp_path / "cloudru-prices.json", {"fetched_at": old, "models": {}})
    assert sync.cache_is_fresh(out, ttl=3600) is False
    _patch_httpx(monkeypatch, payload=_CATALOG_PAYLOAD)
    rc = sync.main(["--out", str(out), "--ttl", "3600"])
    assert rc == 0
    assert len(json.loads(out.read_text())["models"]) == 2


def test_sync_cache_age_seconds(tmp_path):
    out = _write_json(tmp_path / "c.json", {
        "fetched_at": (datetime.now(UTC) - timedelta(seconds=120)).isoformat(),
        "models": {},
    })
    age = sync.cache_age_seconds(out)
    assert age is not None and 100 < age < 200
    assert sync.cache_age_seconds(tmp_path / "missing.json") is None


# ---------------------------------------------------------------------------
# 3. Failure keeps the previous cache and exits non-zero
# ---------------------------------------------------------------------------

def test_sync_failure_keeps_previous_cache(monkeypatch, tmp_path, capsys):
    out = _write_json(tmp_path / "cloudru-prices.json", {
        "fetched_at": "2020-01-01T00:00:00+00:00",
        "models": {"keep/me": {"input": 1.0, "output": 1.0, "cache_read": 0.0, "is_billable": True}},
    })
    before = out.read_text()
    _patch_httpx(monkeypatch, exc=RuntimeError("boom"))
    rc = sync.main(["--out", str(out), "--force"])
    assert rc != 0
    assert out.read_text() == before  # untouched
    assert "Keeping previous cache" in capsys.readouterr().err


def test_sync_empty_catalog_keeps_previous_cache(monkeypatch, tmp_path):
    out = _write_json(tmp_path / "cloudru-prices.json", {"fetched_at": "2020-01-01T00:00:00+00:00", "models": {}})
    before = out.read_text()
    _patch_httpx(monkeypatch, payload={"data": []})
    rc = sync.main(["--out", str(out), "--force"])
    assert rc != 0
    assert out.read_text() == before


def test_sync_missing_api_key_fails(monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUD_RU_API_KEY", raising=False)
    monkeypatch.delenv("CLOUD_RU_FOUNDATION_MODELS_API_KEY", raising=False)
    out = tmp_path / "cloudru-prices.json"
    rc = sync.main(["--out", str(out), "--force"])
    assert rc != 0
    assert not out.exists()


# ---------------------------------------------------------------------------
# 4. Resolver precedence: cache > config > fallback
# ---------------------------------------------------------------------------

def test_resolver_prefers_cache_over_config(config_path, cache_path):
    rates = resolve_model_rates(
        "deepseek-ai/DeepSeek-V4.1-Flash", config_path=config_path, cache_path=cache_path
    )
    # config fixture had 1/2/3/4 -- catalog wins
    assert rates == {"input": pytest.approx(64.94), "output": pytest.approx(194.81),
                     "cacheRead": pytest.approx(6.5), "cacheWrite": 0.0}


def test_resolver_falls_back_to_config(tmp_path, config_path):
    rates = resolve_model_rates(
        "custom-foundation-models-api-cloud-ru/some/Only-In-Config",
        config_path=config_path,
        cache_path=str(tmp_path / "no-cache.json"),
    )
    assert rates["input"] == pytest.approx(10.0)
    assert rates["output"] == pytest.approx(20.0)
    assert rates["cacheRead"] == pytest.approx(0.5)


def test_resolver_unknown_model_uses_fallback_with_warning(tmp_path, capsys):
    rates = resolve_model_rates(
        "nobody/knows-this",
        config_path=str(tmp_path / "no-config.json"),
        cache_path=str(tmp_path / "no-cache.json"),
    )
    assert rates["input"] == pytest.approx(239.12)
    assert rates["output"] == pytest.approx(751.52)
    assert "WARNING" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 5. Verified live values
# ---------------------------------------------------------------------------

def test_gigachat35_values_from_cache(cache_path):
    rates = resolve_model_rates(
        "ai-sage/GigaChat3.5-432B-A28B-Reasoning", cache_path=cache_path
    )
    assert rates["input"] == pytest.approx(96.22)
    assert rates["output"] == pytest.approx(288.6)
    assert rates["cacheRead"] == pytest.approx(0.0)  # no cache field


def test_deepseek_v41_values_from_cache(cache_path):
    rates = resolve_model_rates("deepseek-ai/DeepSeek-V4.1-Flash", cache_path=cache_path)
    assert rates["input"] == pytest.approx(64.94)
    assert rates["output"] == pytest.approx(194.81)
    assert rates["cacheRead"] == pytest.approx(6.5)


def test_resolver_suffix_match_for_compound_id(cache_path):
    """Runner passes ``<provider>/<model>``; cache key is just the model id."""
    rates = resolve_model_rates(
        "custom-foundation-models-api-cloud-ru/deepseek-ai/DeepSeek-V4.1-Flash",
        cache_path=cache_path,
    )
    assert rates["input"] == pytest.approx(64.94)
    assert rates["cacheRead"] == pytest.approx(6.5)
