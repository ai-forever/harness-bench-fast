"""Marathon chains: the whole task set in one session, delivered as a file."""

from __future__ import annotations

from pathlib import Path

from harness_bench.chains import MARATHON_ID, MARATHON_LADDER, all_chains, get_chain
from harness_bench.runner_chain import (
    TASK_LIST_FILENAME,
    ChainRunResult,
    TurnRecord,
    _dir_fingerprint,
    _marathon_prompt,
    _mark_untouched_as_not_reached,
    _task_list_document,
    run_chain_batch_agent,
)
from harness_bench.tasks import get_task


def test_marathon_covers_every_eligible_task() -> None:
    """`ALL` is the whole eligible set, each task exactly once."""
    marathon = get_chain(MARATHON_ID)
    eligible = {
        task_id
        for chain_id, spec in all_chains().items()
        if chain_id != MARATHON_ID
        for task_id in spec.task_ids
    }
    assert len(marathon.task_ids) == len(set(marathon.task_ids))
    assert eligible <= set(marathon.task_ids)


def test_ladder_chains_are_prefixes_of_the_marathon() -> None:
    """A50/A100/A200 nest, so a position means the same thing in each."""
    marathon = get_chain(MARATHON_ID).task_ids
    for length in MARATHON_LADDER:
        assert get_chain(f"A{length}").task_ids == marathon[:length]


def test_marathon_order_is_not_the_registry_order() -> None:
    """Shuffled on purpose: registry order runs easy-to-hard, which would make
    "reached position 60" mean "did the 60 easiest tasks"."""
    ids = get_chain(MARATHON_ID).task_ids
    assert list(ids) != sorted(ids)


def test_task_list_document_holds_every_prompt() -> None:
    spec = get_chain("S5")
    tasks = [get_task(task_id) for task_id in spec.task_ids]
    subdirs = [f"t0{i}_{t.id}" for i, t in enumerate(tasks, start=1)]

    document = _task_list_document(tasks, subdirs)

    for index, (task, subdir) in enumerate(zip(tasks, subdirs, strict=True), start=1):
        assert f"## Задача {index}/{len(tasks)} — каталог `{subdir}/`" in document
        assert task.prompt in document


def test_marathon_prompt_points_at_the_file_without_inlining_tasks() -> None:
    """The kickoff prompt must stay small — the point of file delivery."""
    prompt = _marathon_prompt(342, f"/{TASK_LIST_FILENAME}")

    assert f"/{TASK_LIST_FILENAME}" in prompt
    assert "342" in prompt
    assert len(prompt) < 1000


def _result_with(subdirs: list[str]) -> ChainRunResult:
    return ChainRunResult(
        chain_id="TEST",
        task_ids=tuple(f"task_{i}" for i in range(len(subdirs))),
        delivery="file",
        turns=[
            TurnRecord(position=i, task_id=f"task_{i}", subdir=subdir, reached=True)
            for i, subdir in enumerate(subdirs, start=1)
        ],
    )


def test_untouched_directories_are_not_reached(tmp_path: Path) -> None:
    """Batch delivery cannot watch the agent walk the list, so an untouched
    directory is the only evidence that a task was never worked on."""
    subdirs = ["t1", "t2", "t3"]
    for subdir in subdirs:
        (tmp_path / subdir).mkdir()
        (tmp_path / subdir / "fixture.txt").write_text("start", encoding="utf-8")
    before = {subdir: _dir_fingerprint(tmp_path / subdir) for subdir in subdirs}
    (tmp_path / "t1" / "fixture.txt").write_text("solved", encoding="utf-8")

    result = _result_with(subdirs)
    _mark_untouched_as_not_reached(result, tmp_path, before)

    assert [turn.reached for turn in result.turns] == [True, False, False]
    assert result.terminated_at_position == 2
    assert result.termination_reason == "agent stopped before the end of the list"


def test_finished_marathon_is_not_reported_as_terminated(tmp_path: Path) -> None:
    subdirs = ["t1", "t2"]
    for subdir in subdirs:
        (tmp_path / subdir).mkdir()
        (tmp_path / subdir / "fixture.txt").write_text("start", encoding="utf-8")
    before = {subdir: _dir_fingerprint(tmp_path / subdir) for subdir in subdirs}
    for subdir in subdirs:
        (tmp_path / subdir / "fixture.txt").write_text("solved", encoding="utf-8")

    result = _result_with(subdirs)
    _mark_untouched_as_not_reached(result, tmp_path, before)

    assert all(turn.reached for turn in result.turns)
    assert result.terminated_at_position is None


def test_a_real_crash_keeps_its_own_termination_reason(tmp_path: Path) -> None:
    """"Agent stopped early" must not overwrite a context overflow."""
    for subdir in ("t1", "t2"):
        (tmp_path / subdir).mkdir()
        (tmp_path / subdir / "fixture.txt").write_text("start", encoding="utf-8")
    before = {s: _dir_fingerprint(tmp_path / s) for s in ("t1", "t2")}
    (tmp_path / "t1" / "fixture.txt").write_text("solved", encoding="utf-8")

    result = _result_with(["t1", "t2"])
    result.termination_reason = "context overflow: boom"
    _mark_untouched_as_not_reached(result, tmp_path, before)

    assert result.termination_reason == "context overflow: boom"


class _StubAgent:
    """Solves the first task only, then declares the job done."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self.prompt: str | None = None

    def invoke(self, payload: dict, **_kwargs: object) -> dict:
        self.prompt = payload["messages"][0]["content"]
        first = sorted(p for p in self._workspace.iterdir() if p.is_dir())[0]
        (first / "hello.txt").write_text("Hello, World!\n", encoding="utf-8")
        return {"messages": []}


def test_file_delivery_writes_the_list_and_scores_only_reached_tasks() -> None:
    """End-to-end: the agent gets a path, and quitting early is visible."""
    spec = get_chain("S5")
    agents: list[_StubAgent] = []

    def factory(workspace: Path) -> _StubAgent:
        agent = _StubAgent(workspace)
        agents.append(agent)
        return agent

    result = run_chain_batch_agent(spec, factory, via_file=True, progress=lambda _m: None)

    assert result.completed
    assert TASK_LIST_FILENAME in (agents[0].prompt or "")
    # The task list went to the file, not into the prompt.
    assert get_task(spec.task_ids[0]).prompt not in (agents[0].prompt or "")
    assert [turn.reached for turn in result.turns] == [True, False, False, False, False]
    assert result.terminated_at_position == 2
