"""Tests for model detection in ``scripts/backfill_cost_rub.py``.

Regression for "cause 4": Pi artifacts carry only ``results`` + ``summary`` -- no
``model`` field, no per-task ``model``, no ``command``. ``_detect_model`` therefore
returned ``None`` and the cost resolver silently priced the run with the documented
GLM-5.2 fallback (239.12/751.52) instead of the model's real rates.

Detection must now ALSO infer the model from the artifact FILENAME/family when every
field is absent (``*_ds41flash_*`` -> DeepSeek-V4.1-Flash; ``*_giga35_*`` /
``*_gigachat35_*`` -> GigaChat 3.5 Reasoning), while a genuinely unknown artifact keeps
the documented fallback **with** a WARNING. Explicit fields must still win over the name.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
for _p in (str(_ROOT), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backfill_cost_rub import _detect_model, _detect_model_from_name, _recompute_file  # noqa: E402

from harness_bench.cost_resolver import compute_cost_rub  # noqa: E402

# ---------------------------------------------------------------------------
# Hermetic catalog cache (values verified against the live Cloud.ru catalog)
# ---------------------------------------------------------------------------

_DEEPSEEK_ID = "custom-foundation-models-api-cloud-ru/deepseek-ai/DeepSeek-V4.1-Flash"
_GIGA35_ID = "ai-sage/GigaChat3.5-432B-A28B-Reasoning"

_CATALOG_CACHE = {
    "fetched_at": "2026-11-09T00:00:00+00:00",
    "models": {
        "deepseek-ai/DeepSeek-V4.1-Flash": {
            "input": 64.94, "output": 194.81, "cache_read": 6.5, "is_billable": True,
        },
        "ai-sage/GigaChat3.5-432B-A28B-Reasoning": {
            "input": 96.22, "output": 288.6, "cache_read": 0.0, "is_billable": True,
        },
    },
}


@pytest.fixture(autouse=True)
def _hermetic_cache(tmp_path, monkeypatch):
    """Point the resolver at a fixture catalog cache, never the machine's real one."""
    cache = tmp_path / "cloudru-prices.json"
    cache.write_text(json.dumps(_CATALOG_CACHE), encoding="utf-8")
    monkeypatch.setenv("CLOUDRU_PRICES_CACHE", str(cache))


def _pi_like_artifact(name: str, directory: Path, *, in_tok: int, out_tok: int, cr_tok: int = 0) -> Path:
    """Write a Pi-style artifact: only ``results`` + ``summary``, no model field."""
    path = directory / name
    path.write_text(
        json.dumps(
            {
                "results": [{"task_id": 1, "passed": True, "agent_input_tokens": 10}],
                "summary": {
                    "total_input_tokens": in_tok,
                    "total_output_tokens": out_tok,
                    "total_cache_read_tokens": cr_tok,
                    "estimated_cost_rub": 0.0,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


_NO_CONFIG = "/nonexistent/openclaw.json"


# ---------------------------------------------------------------------------
# Filename/family inference
# ---------------------------------------------------------------------------

class TestDetectModelFromName:
    def test_ds41flash(self):
        assert _detect_model_from_name("pi_shotlist_ds41flash.json") == _DEEPSEEK_ID

    def test_giga35(self):
        assert _detect_model_from_name("pi_medium_long_giga35_low_20261107.json") == _GIGA35_ID

    def test_gigachat35(self):
        assert _detect_model_from_name("oc_shotlist_gigachat35_high.json") == _GIGA35_ID

    def test_unknown(self):
        assert _detect_model_from_name("pi_shotlist_mystery.json") is None


class TestDetectModel:
    def test_filename_fallback_when_fields_absent(self):
        data = {"results": [], "summary": {}}
        assert _detect_model(data, name="oc_shotlist_ds41flash_high_20261107.json") == _DEEPSEEK_ID

    def test_explicit_field_wins_over_filename(self):
        # oc_shotlist_ds41flash.json actually stores a GLM-5.2 model field: data wins.
        data = {"model": "custom-foundation-models-api-cloud-ru/zai-org/GLM-5.2"}
        assert _detect_model(data, name="oc_shotlist_ds41flash.json") == (
            "custom-foundation-models-api-cloud-ru/zai-org/GLM-5.2"
        )

    def test_per_task_model_used(self):
        data = {"results": [{"model": "some/provider/model"}]}
        assert _detect_model(data, name="pi_shotlist_mystery.json") == "some/provider/model"

    def test_unknown_returns_none(self):
        assert _detect_model({"results": [], "summary": {}}, name="pi_shotlist_mystery.json") is None


# ---------------------------------------------------------------------------
# _recompute_file: Pi-like artifact must NOT use the fallback
# ---------------------------------------------------------------------------

def test_pi_like_artifact_resolves_deepseek_not_fallback(tmp_path, capsys):
    path = _pi_like_artifact(
        "pi_shotlist_ds41flash.json", tmp_path, in_tok=1_000_000, out_tok=1_000_000, cr_tok=1_000_000
    )
    _recompute_file(path, dry_run=True, config_path=_NO_CONFIG)
    out = capsys.readouterr().out

    assert "deepseek-ai/DeepSeek-V4.1-Flash" in out
    assert "(unknown" not in out  # not the fallback path
    expected = compute_cost_rub(
        1_000_000, 1_000_000, 1_000_000, 0,
        {"input": 64.94, "output": 194.81, "cacheRead": 6.5, "cacheWrite": 0.0},
    )
    assert f"{expected:.2f}" in out  # 266.25 ₽, not the GLM-5.2 fallback 990.64 ₽


def test_pi_like_giga35_artifact_resolves_gigachat_not_fallback(tmp_path, capsys):
    path = _pi_like_artifact(
        "pi_medium_long_giga35_low_20261107.json", tmp_path, in_tok=1_000_000, out_tok=1_000_000
    )
    _recompute_file(path, dry_run=True, config_path=_NO_CONFIG)
    out = capsys.readouterr().out

    assert "GigaChat3.5-432B-A28B-Reasoning" in out
    assert "(unknown" not in out
    expected = compute_cost_rub(
        1_000_000, 1_000_000, 0, 0,
        {"input": 96.22, "output": 288.6, "cacheRead": 0.0, "cacheWrite": 0.0},
    )
    assert f"{expected:.2f}" in out  # 384.82 ₽


def test_unknown_artifact_still_falls_back_with_warning(tmp_path, capsys):
    path = _pi_like_artifact("pi_shotlist_mystery.json", tmp_path, in_tok=1_000_000, out_tok=1_000_000)
    _recompute_file(path, dry_run=True, config_path=_NO_CONFIG)
    captured = capsys.readouterr()

    assert "(unknown" in captured.out  # reported as fallback
    assert "WARNING" in captured.err  # documented warning, never silent
    fallback = compute_cost_rub(
        1_000_000, 1_000_000, 0, 0,
        {"input": 239.12, "output": 751.52, "cacheRead": 0.0, "cacheWrite": 0.0},
    )
    assert f"{fallback:.2f}" in captured.out  # 990.64 ₽ GLM-5.2 fallback
