"""Marathon chains: the whole task set in one session, delivered as a file."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import harness_bench.runner_chain as runner_chain
from harness_bench.chains import MARATHON_ID, MARATHON_LADDER, all_chains, get_chain
from harness_bench.runner_chain import (
    TASK_LIST_FILENAME,
    ChainRunResult,
    TurnRecord,
    _dir_fingerprint,
    _marathon_prompt,
    _mark_untouched_as_not_reached,
    _task_list_document,
    chain_results_to_payload,
    run_chain_batch_agent,
    run_chain_turns,
    summarize_chains,
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


class _SolveEverythingThenBreakTheFirst:
    """Applies every gold solution, then goes back and clobbers task 1.

    The exact long-horizon failure chain mode exists to catch: work that was
    genuinely finished and then destroyed by later work in the same session.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def invoke(self, payload: dict, **_kwargs: object) -> dict:
        dirs = sorted(p for p in self.workspace.iterdir() if p.is_dir())
        for directory in dirs:
            get_task(directory.name.split("_", 1)[1]).apply_gold(directory)
        for artifact in dirs[0].rglob("*"):
            if artifact.is_file():
                artifact.write_text("clobbered by later work\n", encoding="utf-8")
        return {"messages": []}


def test_batch_delivery_reports_retention_as_unmeasured() -> None:
    """One verdict per task, taken at the end, cannot see work broken on the
    way there. Reporting zero breakage would turn an unasked question into a
    perfect score — the whole chain ran inside a single invocation."""
    spec = get_chain("S5")

    result = run_chain_batch_agent(
        spec, _SolveEverythingThenBreakTheFirst, via_file=True, progress=lambda _m: None
    )

    assert result.final_passed == spec.length - 1  # the clobbered task fails
    assert all(turn.passed_immediate is None for turn in result.turns)
    assert result.retention_measured is False
    assert result.retention_broken is None
    assert result.immediate_passed is None

    payload = chain_results_to_payload([result], delivery="file", backend_label="stub")
    assert payload["retention_measured"] is False
    assert payload["retention_broken"] is None
    assert payload["immediate_passed"] is None
    assert payload["immediate_pass_rate"] is None


def test_summary_never_claims_retention_it_did_not_measure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = run_chain_batch_agent(
        get_chain("S5"),
        _SolveEverythingThenBreakTheFirst,
        via_file=True,
        progress=lambda _m: None,
    )

    summarize_chains([result])

    out = capsys.readouterr().out
    assert "Retention: not measured" in out
    assert "survived to chain end" not in out
    assert "100.0%)" not in out


class _SolvesThenBlowsTheStepBudget:
    """Finishes the task, then dies on the recursion limit — which is what a
    model that keeps poking after the work is done actually does."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.calls = 0

    def invoke(self, payload: dict, **_kwargs: object) -> dict:
        from langgraph.errors import GraphRecursionError

        self.calls += 1
        directory = sorted(p for p in self.workspace.iterdir() if p.is_dir())[-1]
        get_task(directory.name.split("_", 1)[1]).apply_gold(directory)
        raise GraphRecursionError("Recursion limit of 80 reached")


def test_a_turn_that_runs_out_of_steps_is_still_verified() -> None:
    """The step cap ends the turn, not the work: whatever landed on disk before
    it is a real outcome. Verifying only the clean path records "never checked"
    as "failed", which shows up as `passed_final` exceeding `passed_immediate`
    for tasks that were finished all along."""
    result = run_chain_turns(
        get_chain("S5"), _SolvesThenBlowsTheStepBudget, progress=lambda _m: None
    )

    assert all(turn.error == "graph recursion limit reached" for turn in result.turns)
    assert result.immediate_passed == 5
    assert result.final_passed == 5
    assert result.retention_broken == 0


class _NeverReturns:
    """Blocks until released — a turn that outlives its timeout."""

    def __init__(self, workspace: Path, release: threading.Event) -> None:
        self.workspace = workspace
        self._release = release

    def invoke(self, payload: dict, **_kwargs: object) -> dict:
        self._release.wait(30)
        return {"messages": []}


def test_a_timeout_that_leaves_the_agent_running_is_not_a_completed_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python cannot kill the invocation thread, so a timed-out agent keeps
    writing into the workspace being scored. Such a chain must not be published
    as a finished measurement, nor skipped by a later resume."""
    monkeypatch.setattr(runner_chain, "_ORPHAN_GRACE_SECONDS", 0.1)
    release = threading.Event()
    try:
        result = run_chain_batch_agent(
            get_chain("S5"),
            lambda ws: _NeverReturns(ws, release),
            chain_timeout=0.2,
            progress=lambda _m: None,
        )
    finally:
        release.set()

    assert result.scoring_raced is True
    assert result.completed is False
    assert "timed out" in (result.error or "")


def test_a_turn_timeout_that_leaves_the_agent_running_ends_the_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Continuing would verify every later task against a tree the orphaned
    agent is still editing."""
    monkeypatch.setattr(runner_chain, "_ORPHAN_GRACE_SECONDS", 0.1)
    release = threading.Event()
    try:
        result = run_chain_turns(
            get_chain("S5"),
            lambda ws: _NeverReturns(ws, release),
            turn_timeout=0.2,
            progress=lambda _m: None,
        )
    finally:
        release.set()

    assert result.terminated_at_position == 1
    assert result.scoring_raced is True
    assert result.completed is False
    assert [turn.reached for turn in result.turns] == [True, False, False, False, False]
