"""Tests for fork_metadata and publish_fork orchestrator.

Covers:
  - hard-fail on unresolvable harness/model stem
  - incomplete-run guard (no top-level summary)
  - metadata dedup parity: same classify results from fork_metadata module
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fork_metadata  # noqa: E402
from fork_metadata import (  # noqa: E402
    classify_harness,
    classify_model,
    classify_profile,
    collect_paths,
    resolve_stem,
)
from publish_fork import (  # noqa: E402
    _is_incomplete,
    check_incomplete_runs,
    check_unresolvable_stems,
)

# ---------------------------------------------------------------------------
# fork_metadata: basic classification
# ---------------------------------------------------------------------------

class TestClassifyHarness:
    def test_pi(self):
        assert classify_harness("pi_long_ds41flash_20261106") == "Pi"

    def test_openclaw(self):
        assert classify_harness("oc_shotlist_ds41flash") == "OpenClaw"

    def test_openclaw_full_prefix(self):
        assert classify_harness("openclaw_shotlist_x") == "OpenClaw"

    def test_hermes(self):
        assert classify_harness("hermes_full_391") == "Hermes"

    def test_dsh(self):
        assert classify_harness("dsh_shotlist_ds41flash_20261111") == "DeepSeek Harness"

    def test_dsh_thinking_suffix(self):
        assert classify_harness("dsh_shotlist_ds41flash_high_20261111") == "DeepSeek Harness"

    def test_opencode(self):
        assert classify_harness("opencode_shotlist_x") == "opencode"

    def test_unknown(self):
        assert classify_harness("unknown_stem") is None


class TestClassifyModel:
    def test_ds41flash(self):
        assert classify_model("pi_long_ds41flash_20261106") == "DeepSeek-V4.1-Flash"

    def test_glm52_via_token(self):
        assert classify_model("hermes_shotlist_glm52") == "GLM-5.2"

    def test_thinking_off_no_model(self):
        # thinking_off is not a model token; fork_metadata returns None
        # build_fork_results.py keeps a local classify_model wrapper with the fallback
        assert classify_model("hermes_shotlist_thinking_off") is None

    def test_giga35(self):
        assert classify_model("pi_medium_long_giga35_low_20261107") == "GigaChat 3.5 Reasoning"

    def test_unknown(self):
        assert classify_model("pi_long_unknownmodel") is None


class TestClassifyProfile:
    def test_thinking_high(self):
        assert classify_profile("oc_shotlist_ds41flash_high_20261107") == "thinking: high"

    def test_thinking_off(self):
        assert classify_profile("hermes_shotlist_thinking_off") == "thinking: off"

    def test_mcp_off(self):
        assert classify_profile("hermes_shotlist_mcpoff") == "MCP off"

    def test_no_profile(self):
        assert classify_profile("pi_long_ds41flash_20261106") is None


class TestResolveStem:
    def test_full_resolve(self):
        h, m, lvl = resolve_stem("pi_medium_long_giga35_low_20261107")
        assert h == "Pi"
        assert m == "GigaChat 3.5 Reasoning"
        assert lvl == "low"

    def test_no_level(self):
        h, m, lvl = resolve_stem("pi_long_ds41flash_20261106")
        assert h == "Pi"
        assert m == "DeepSeek-V4.1-Flash"
        assert lvl is None

    def test_dsh_ds41flash(self):
        h, m, lvl = resolve_stem("dsh_shotlist_ds41flash_20261111")
        assert h == "DeepSeek Harness"
        assert m == "DeepSeek-V4.1-Flash"
        assert lvl is None

    def test_dsh_ds41flash_high(self):
        h, m, lvl = resolve_stem("dsh_shotlist_ds41flash_high_20261111")
        assert h == "DeepSeek Harness"
        assert m == "DeepSeek-V4.1-Flash"
        assert lvl == "high"


# ---------------------------------------------------------------------------
# Metadata dedup parity: all three generators yield same harness/model/profile
# ---------------------------------------------------------------------------

def _stems_from_results() -> list[str]:
    """Return stems from the repo's own results/ dir."""
    repo_root = SCRIPTS.parent
    results_dir = repo_root / "results"
    if not results_dir.is_dir():
        return []
    return [p.stem for p in sorted(results_dir.glob("*.json"))]


def test_metadata_dedup_parity_harness():
    """fork_metadata.classify_harness agrees with build_fork_pages for all repo stems."""
    # We just need to check that the imported classify_harness in build_fork_pages
    # IS fork_metadata.classify_harness (same object after refactor)
    import build_fork_pages as bfp  # already on sys.path via SCRIPTS insert
    assert bfp.classify_harness is fork_metadata.classify_harness, (
        "build_fork_pages.classify_harness must be imported from fork_metadata"
    )


def test_metadata_dedup_parity_model():
    """build_fork_pages.classify_model is fork_metadata.classify_model after refactor."""
    import build_fork_pages as bfp
    assert bfp.classify_model is fork_metadata.classify_model


def test_metadata_dedup_parity_profile():
    """build_fork_pages.classify_profile is fork_metadata.classify_profile."""
    import build_fork_pages as bfp
    assert bfp.classify_profile is fork_metadata.classify_profile


# ---------------------------------------------------------------------------
# Incomplete-run guard
# ---------------------------------------------------------------------------

class TestIsIncomplete:
    def test_no_summary_is_not_incomplete(self):
        """Old-format files without summary are skipped by generators, not hard-failed."""
        data = {"task_set_version": "v0.1", "passed": 5, "total": 10}
        assert not _is_incomplete(data)

    def test_with_summary_complete(self):
        data = {"summary": {"passed": 5, "total": 10, "pass_rate": 0.5}}
        assert not _is_incomplete(data)

    def test_in_flight_marker_in_summary_key(self):
        data = {"summary": {"passed_in_progress": 5, "total": 10}}
        assert _is_incomplete(data)

    def test_partial_marker(self):
        data = {"summary": {"passed": 3, "_partial": True, "total": 10}}
        assert _is_incomplete(data)


def test_check_incomplete_runs_with_inflight(tmp_path):
    """check_incomplete_runs returns stems for in-flight artifacts."""
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    # good run
    (result_dir / "pi_long_ds41flash_20261106.json").write_text(
        json.dumps({"summary": {"passed": 10, "total": 20, "pass_rate": 0.5}}),
        encoding="utf-8",
    )
    # in-flight run
    (result_dir / "pi_long_ds41flash_20261107.json").write_text(
        json.dumps({"summary": {"passed_in_progress": 3, "total": 20}}),
        encoding="utf-8",
    )
    incomplete = check_incomplete_runs([result_dir])
    assert "pi_long_ds41flash_20261107.json" in incomplete
    assert "pi_long_ds41flash_20261106.json" not in incomplete


# ---------------------------------------------------------------------------
# Unresolvable stem hard-fail
# ---------------------------------------------------------------------------

def test_check_unresolvable_stems_catches_unknown(tmp_path):
    """check_unresolvable_stems returns stems where harness or model is None."""
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    # valid stem
    (result_dir / "pi_long_ds41flash_20261106.json").write_text("{}", encoding="utf-8")
    # unknown harness
    (result_dir / "unknown_long_ds41flash_20261106.json").write_text("{}", encoding="utf-8")
    # unknown model
    (result_dir / "pi_long_novelmodel_20261106.json").write_text("{}", encoding="utf-8")

    unresolvable = check_unresolvable_stems([result_dir])
    assert "pi_long_ds41flash_20261106" not in unresolvable
    assert "unknown_long_ds41flash_20261106" in unresolvable
    assert "pi_long_novelmodel_20261106" in unresolvable


def test_check_unresolvable_stems_clean(tmp_path):
    """No unresolvable stems for known artifacts."""
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    for name in [
        "pi_long_ds41flash_20261106.json",
        "oc_shotlist_ds41flash_high_20261107.json",
        "hermes_shotlist_oss120b.json",
    ]:
        (result_dir / name).write_text("{}", encoding="utf-8")

    unresolvable = check_unresolvable_stems([result_dir])
    assert unresolvable == []


# ---------------------------------------------------------------------------
# collect_paths dedup
# ---------------------------------------------------------------------------

def test_collect_paths_first_wins(tmp_path):
    """First result_dir wins on name clash."""
    d1 = tmp_path / "d1"
    d1.mkdir()
    d2 = tmp_path / "d2"
    d2.mkdir()
    (d1 / "run.json").write_text("d1", encoding="utf-8")
    (d2 / "run.json").write_text("d2", encoding="utf-8")

    paths = collect_paths([d1, d2])
    assert paths["run.json"].read_text(encoding="utf-8") == "d1"
