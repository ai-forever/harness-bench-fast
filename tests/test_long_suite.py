"""Long-task suite registry and the long-context runner options."""

from __future__ import annotations

import importlib
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from harness_bench.long_tasks import _MODULES, LONG_TASKS
from harness_bench.long_tasks.common import load_texts, rewritten, text_sha
from harness_bench.openrouter_agent import real_token_count
from harness_bench.runner_openrouter import count_compactions
from harness_bench.tasks import ALL_TASKS, SUITES, get_task
from harness_bench.versioning import task_number


def test_long_wave_is_tasks_392_to_411() -> None:
    ids = [task.id for task in LONG_TASKS]
    assert len(ids) == 20
    assert len(set(ids)) == len(ids)
    assert [task_number(task_id) for task_id in ids] == list(range(392, 412))
    assert ids == [task.id for task in ALL_TASKS[391:411]]
    assert SUITES["long"] is LONG_TASKS
    assert SUITES["default"] is ALL_TASKS
    for task in LONG_TASKS:
        assert get_task(task.id) is task
        assert task.min_timeout_seconds and task.min_timeout_seconds >= 3600
        assert task.min_recursion_limit and task.min_recursion_limit >= 1000
    for name in _MODULES:
        module = importlib.import_module(f"harness_bench.long_tasks.{name}")
        seed = getattr(module, "TASK_ID", None)
        if seed is not None:
            assert seed.startswith("long_")
            assert seed != module.TASK.id


def test_rewritten_text_requires_matching_draft_hash() -> None:
    task_id = "long_03_review_annotation"
    store = load_texts(task_id)
    assert store, "the t03 rewrite fixture should ship with the package"
    key, entry = next(iter(store.items()))
    # A draft that does not match the stored hash never gets the rewrite.
    assert rewritten(task_id, key, "some other draft") == "some other draft"
    assert set(entry) >= {"sha", "text"} and len(entry["sha"]) == len(text_sha("x"))


def _ai(content: str, total_tokens: int) -> AIMessage:
    return AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": total_tokens - 10,
            "output_tokens": 10,
            "total_tokens": total_tokens,
        },
    )


def test_real_token_count_uses_the_latest_usage() -> None:
    history = [HumanMessage(content="x" * 400), _ai("y" * 40, 250)]
    base = real_token_count(history)
    assert base == 250
    grown = real_token_count([*history, ToolMessage(content="z" * 4000, tool_call_id="1")])
    assert grown > base + 1000


def test_real_token_count_ignores_usage_from_before_a_compaction() -> None:
    # A preserved AI message whose usage describes a much longer, pre-summary
    # context must not keep the count above the threshold.
    stale = _ai("short", 200_000)
    count = real_token_count([HumanMessage(content="summary " * 50), stale])
    assert count < 10_000


def test_count_compactions_reads_history_offload(tmp_path: Path) -> None:
    assert count_compactions(tmp_path) == 0
    history = tmp_path / "conversation_history"
    history.mkdir()
    (history / "a.md").write_text("## Summarized at 1\n\nx\n\n## Summarized at 2\n", encoding="utf-8")
    (history / "b.md").write_text("## Summarized at 3\n", encoding="utf-8")
    assert count_compactions(tmp_path) == 3
