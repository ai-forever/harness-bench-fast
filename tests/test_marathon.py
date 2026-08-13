"""Marathon mode: the whole task set handed to one session, scored at the end."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

import harness_bench.runner_marathon as runner_marathon
from harness_bench.marathon import (
    MARATHON_VERSION,
    marathon_length,
    marathon_task_ids,
)
from harness_bench.runner import set_results_json_command
from harness_bench.runner_marathon import (
    TASK_LIST_FILENAME,
    MarathonResult,
    TaskRecord,
    _dir_fingerprint,
    _kickoff_prompt,
    _mark_reached,
    _score,
    _task_list_document,
    run_marathon,
    summarize_marathon,
)
from harness_bench.tasks import get_task
from harness_bench.versioning import task_number

# --------------------------------------------------------------- the order


def test_marathon_order_is_pinned_to_its_version() -> None:
    """The order is a seeded shuffle over the task registry, so adding or
    removing one task renumbers positions that published runs are reported
    against. Changing it is fine; changing it silently is not, so bump
    MARATHON_VERSION and this hash together."""
    digest = hashlib.sha256(
        json.dumps(list(marathon_task_ids())).encode()
    ).hexdigest()[:16]

    assert (MARATHON_VERSION, marathon_length(), digest) == ("v1", 342, "bada4344d1cf6cae")


def test_marathon_holds_every_eligible_task_once() -> None:
    ids = marathon_task_ids()
    assert len(set(ids)) == len(ids)
    for task_id in ids:
        get_task(task_id)  # raises on an unknown id


def test_excluded_waves_never_appear() -> None:
    """memory and skills are wired to the workspace root and cannot be scoped
    to one task's subdirectory, let alone share a root with each other."""
    for task_id in marathon_task_ids():
        number = task_number(task_id)
        assert number is not None
        assert not 222 <= number <= 253, f"memory task {task_id}"
        assert not 314 <= number <= 330, f"skills task {task_id}"


def test_order_is_not_the_registry_order() -> None:
    """Shuffled on purpose: the registry runs easy-to-hard, so registry order
    would make "reached position 60" mean "did the 60 easiest tasks"."""
    ids = list(marathon_task_ids())
    assert ids != sorted(ids)


def test_first_n_is_a_prefix_so_positions_mean_the_same_thing() -> None:
    ids = marathon_task_ids()
    for n in (5, 50, 200):
        assert ids[:n] == marathon_task_ids()[:n]


# --------------------------------------------------------------- delivery


def test_task_list_document_holds_every_prompt() -> None:
    ids = marathon_task_ids()[:5]
    tasks = [get_task(task_id) for task_id in ids]
    subdirs = [f"t0{i}_{t.id}" for i, t in enumerate(tasks, start=1)]

    document = _task_list_document(tasks, subdirs)

    for index, (task, subdir) in enumerate(zip(tasks, subdirs, strict=True), start=1):
        assert f"## Задача {index}/{len(tasks)} — каталог `{subdir}/`" in document
        assert task.prompt in document


def test_kickoff_prompt_points_at_the_file_without_inlining_tasks() -> None:
    """The point of file delivery: 342 prompts are ~31k tokens and do not fit a
    32k window before any work has started."""
    prompt = _kickoff_prompt(342, f"/{TASK_LIST_FILENAME}")

    assert f"/{TASK_LIST_FILENAME}" in prompt
    assert "342" in prompt
    assert len(prompt) < 1000


# --------------------------------------------------------------- how far


def _result_with(subdirs: list[str]) -> MarathonResult:
    return MarathonResult(
        task_ids=tuple(f"task_{i}" for i in range(len(subdirs))),
        tasks=[
            TaskRecord(position=i, task_id=f"task_{i}", subdir=subdir)
            for i, subdir in enumerate(subdirs, start=1)
        ],
    )


def _seeded(tmp_path: Path, subdirs: list[str]) -> dict[str, str]:
    for subdir in subdirs:
        (tmp_path / subdir).mkdir()
        (tmp_path / subdir / "fixture.txt").write_text("start", encoding="utf-8")
    return {subdir: _dir_fingerprint(tmp_path / subdir) for subdir in subdirs}


def test_untouched_directories_are_not_reached(tmp_path: Path) -> None:
    """Nothing watches the agent walk the list, so an untouched directory is
    the only evidence a task was never worked on. Without it "solved 1 of 3"
    cannot be told apart from "stopped after task 1"."""
    subdirs = ["t1", "t2", "t3"]
    before = _seeded(tmp_path, subdirs)
    (tmp_path / "t1" / "fixture.txt").write_text("solved", encoding="utf-8")

    result = _result_with(subdirs)
    _mark_reached(result, tmp_path, before)

    assert [task.reached for task in result.tasks] == [True, False, False]
    assert result.frontier == 1


def test_deleting_a_file_counts_as_reaching_the_task(tmp_path: Path) -> None:
    """Some tasks are solved by removing something; the fingerprint covers the
    file list, not just contents."""
    before = _seeded(tmp_path, ["t1"])
    (tmp_path / "t1" / "fixture.txt").unlink()

    result = _result_with(["t1"])
    _mark_reached(result, tmp_path, before)

    assert result.tasks[0].reached is True


def test_an_agent_that_quits_early_is_recorded_as_such(tmp_path: Path) -> None:
    """No exception, no timeout, and most of the list untouched: the agent
    decided it was done. The most common long-horizon ending, and not a crash."""
    subdirs = ["t1", "t2", "t3"]
    before = _seeded(tmp_path, subdirs)
    (tmp_path / "t1" / "fixture.txt").write_text("solved", encoding="utf-8")

    result = _result_with(subdirs)
    _score(result, [get_task(t) for t in marathon_task_ids()[:3]], tmp_path, before,
           lambda _m: None)

    assert result.stop_reason == "agent stopped before the end of the list"


def test_a_finished_run_is_not_reported_as_quitting_early(tmp_path: Path) -> None:
    subdirs = ["t1", "t2"]
    before = _seeded(tmp_path, subdirs)
    for subdir in subdirs:
        (tmp_path / subdir / "fixture.txt").write_text("solved", encoding="utf-8")

    result = _result_with(subdirs)
    _score(result, [get_task(t) for t in marathon_task_ids()[:2]], tmp_path, before,
           lambda _m: None)

    assert result.stop_reason is None
    assert result.frontier == 2


def test_a_real_failure_keeps_its_own_stop_reason(tmp_path: Path) -> None:
    before = _seeded(tmp_path, ["t1", "t2"])
    (tmp_path / "t1" / "fixture.txt").write_text("solved", encoding="utf-8")

    result = _result_with(["t1", "t2"])
    result.stop_reason = "context overflow: boom"
    _score(result, [get_task(t) for t in marathon_task_ids()[:2]], tmp_path, before,
           lambda _m: None)

    assert result.stop_reason == "context overflow: boom"


# --------------------------------------------------------------- end to end


class _SolvesTheFirstThenStops:
    """Applies one gold solution and declares the job done."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.prompt: str | None = None

    def invoke(self, payload: dict, **_kwargs: object) -> dict:
        self.prompt = payload["messages"][0]["content"]
        first = sorted(p for p in self.workspace.iterdir() if p.is_dir())[0]
        get_task(first.name.split("_", 1)[1]).apply_gold(first)
        return {"messages": []}


def _stub_factory(agents: list, cls) -> object:
    def make(_backend, **_kwargs):
        def factory(workspace):
            agent = cls(workspace)
            agents.append(agent)
            return agent
        return factory
    return make


def test_end_to_end_scores_only_what_the_agent_reached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agents: list[_SolvesTheFirstThenStops] = []
    monkeypatch.setattr(
        runner_marathon, "make_agent_factory", _stub_factory(agents, _SolvesTheFirstThenStops)
    )

    result = run_marathon(first=5, json_output=tmp_path / "m.json", progress=lambda _m: None)

    assert TASK_LIST_FILENAME in (agents[0].prompt or "")
    # the task list went to the file, not into the prompt
    assert get_task(marathon_task_ids()[0]).prompt not in (agents[0].prompt or "")
    assert result.solved == 1
    assert result.frontier == 1
    assert [t.reached for t in result.tasks] == [True, False, False, False, False]
    assert result.stop_reason == "agent stopped before the end of the list"
    assert result.completed is True

    payload = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    assert payload["mode"] == "marathon"
    assert payload["marathon_version"] == MARATHON_VERSION
    assert payload["passed"] == 1
    assert payload["frontier"] == 1
    assert payload["pass_rate_of_reached"] == 1.0


class _NeverReturns:
    def __init__(self, workspace: Path, release: threading.Event) -> None:
        self.workspace = workspace
        self._release = release

    def invoke(self, payload: dict, **_kwargs: object) -> dict:
        self._release.wait(30)
        return {"messages": []}


def test_a_timeout_that_leaves_the_agent_running_is_not_a_completed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python cannot kill the invocation thread, so a timed-out agent keeps
    writing into the workspace being scored. Such a run must not be published
    as a finished measurement."""
    monkeypatch.setattr(runner_marathon, "_ORPHAN_GRACE_SECONDS", 0.1)
    release = threading.Event()

    def make(_backend, **_kwargs):
        return lambda workspace: _NeverReturns(workspace, release)

    monkeypatch.setattr(runner_marathon, "make_agent_factory", make)
    try:
        result = run_marathon(first=3, timeout=0.2, progress=lambda _m: None)
    finally:
        release.set()

    assert result.scoring_raced is True
    assert result.completed is False
    assert "timed out" in (result.error or "")


def test_the_plan_is_on_disk_before_the_agent_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A full run writes nothing else for hours; a run killed before the end
    still has to say what it was running and in what order."""
    out = tmp_path / "m.json"
    seen: dict[str, object] = {}

    def make(_backend, **_kwargs):
        def factory(workspace):
            seen["payload"] = json.loads(out.read_text(encoding="utf-8"))
            return _SolvesTheFirstThenStops(workspace)
        return factory

    monkeypatch.setattr(runner_marathon, "make_agent_factory", make)
    set_results_json_command("python -m harness_bench run-marathon")
    try:
        run_marathon(first=4, json_output=out, progress=lambda _m: None)
    finally:
        set_results_json_command(None)

    payload = seen["payload"]
    assert payload["total"] == 4  # type: ignore[index]
    assert payload["command"] == "python -m harness_bench run-marathon"  # type: ignore[index]
    assert [t["task_id"] for t in payload["tasks"]] == list(marathon_task_ids()[:4])  # type: ignore[index]
    assert payload["completed"] is False  # type: ignore[index]


def test_summary_reports_how_far_and_how_many(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    agents: list[_SolvesTheFirstThenStops] = []
    monkeypatch.setattr(
        runner_marathon, "make_agent_factory", _stub_factory(agents, _SolvesTheFirstThenStops)
    )

    summarize_marathon(run_marathon(first=5, progress=lambda _m: None))

    out = capsys.readouterr().out
    assert "solved            : 1/5" in out
    assert "got as far as     : task 1 of 5" in out
    assert "agent stopped before the end of the list" in out
