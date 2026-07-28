"""Benchmark task-set version metadata.

Package releases and benchmark task-set revisions are intentionally separate:
the package can receive runner or documentation fixes without changing the
task set, while every added/removed/changed task should bump
``TASK_SET_VERSION`` and append a ``TaskSetRevision`` entry.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskSetRevision:
    """A historical benchmark task-set revision."""

    version: str
    introduced: str
    total_tasks: int
    added_task_numbers: tuple[int, int]
    modules: tuple[str, ...]
    notes: str

    @property
    def added_range(self) -> str:
        """Human-readable inclusive range of task numbers added in this revision."""
        start, end = self.added_task_numbers
        return f"{start}" if start == end else f"{start}-{end}"


@dataclass(frozen=True)
class TaskWave:
    """A benchmark wave used for human-readable result breakdowns."""

    name: str
    start: int
    end: int

    @property
    def label(self) -> str:
        return f"{self.name} ({self.start}-{self.end})"

    def contains(self, task_number: int) -> bool:
        return self.start <= task_number <= self.end


TASK_WAVES: tuple[TaskWave, ...] = (
    TaskWave("core", 1, 30),
    TaskWave("extra", 31, 60),
    TaskWave("more", 61, 100),
    TaskWave("hard", 101, 150),
    TaskWave("extreme", 151, 205),
    TaskWave("diagnostic", 206, 221),
    TaskWave("memory", 222, 253),
    TaskWave("agentic", 254, 298),
    TaskWave("VCS", 299, 313),
    TaskWave("skills", 314, 330),
    TaskWave("adversarial", 331, 351),
    TaskWave("tbench-lite", 352, 371),
    TaskWave("cli", 372, 391),
)


TASK_SET_REVISIONS: tuple[TaskSetRevision, ...] = (
    TaskSetRevision(
        version="0.1.0",
        introduced="2026-05-13",
        total_tasks=200,
        added_task_numbers=(1, 200),
        modules=(
            "tasks.py",
            "tasks_extra.py",
            "tasks_more.py",
            "tasks_hard.py",
            "tasks_extreme.py",
        ),
        notes="Initial extracted file/code/data benchmark.",
    ),
    TaskSetRevision(
        version="0.2.0",
        introduced="2026-05-19",
        total_tasks=221,
        added_task_numbers=(201, 221),
        modules=("tasks_extreme.py", "tasks_diagnostic.py"),
        notes="Added advanced composites and diagnostic hard tasks.",
    ),
    TaskSetRevision(
        version="0.3.0",
        introduced="2026-05-21",
        total_tasks=231,
        added_task_numbers=(222, 231),
        modules=("tasks_memory.py",),
        notes="Added memory-discipline tasks using AGENTS.md and MEMORY.md.",
    ),
    TaskSetRevision(
        version="0.4.0",
        introduced="2026-06-02",
        total_tasks=253,
        added_task_numbers=(232, 253),
        modules=("tasks_memory.py",),
        notes=(
            "Extended the memory suite: knowledge update / contradiction "
            "resolution, temporal reasoning, abstention, preference-following, "
            "multi-hop and multi-session reasoning, information extraction."
        ),
    ),
    TaskSetRevision(
        version="0.5.0",
        introduced="2026-06-02",
        total_tasks=262,
        added_task_numbers=(254, 262),
        modules=("tasks_agentic.py",),
        notes=(
            "Added synthetic Terminal-Bench-like, tau-like, and "
            "SWE-bench-like agentic tasks."
        ),
    ),
    TaskSetRevision(
        version="0.6.0",
        introduced="2026-06-02",
        total_tasks=283,
        added_task_numbers=(263, 283),
        modules=("tasks_agentic.py",),
        notes=(
            "Expanded the agentic wave to 10 Terminal-Bench-like, "
            "10 tau-like, and 10 SWE-bench-like tasks."
        ),
    ),
    TaskSetRevision(
        version="0.7.0",
        introduced="2026-06-02",
        total_tasks=298,
        added_task_numbers=(284, 298),
        modules=("tasks_agentic.py",),
        notes=(
            "Expanded the agentic wave to 15 Terminal-Bench-like, "
            "15 tau-like, and 15 SWE-bench-like tasks."
        ),
    ),
    TaskSetRevision(
        version="0.8.0",
        introduced="2026-06-05",
        total_tasks=308,
        added_task_numbers=(299, 308),
        modules=("tasks_vcs.py",),
        notes=(
            "Added version-control tasks: Git merge-conflict resolution "
            "(ours/theirs/both/manual, diff3 base sections, multi-hunk, "
            "multi-file), multi-hunk unified-diff apply/revert, and "
            "unresolved-conflict detection with false-positive traps. Strict "
            "exact-content verifiers catch dropped markers and stray "
            "special characters."
        ),
    ),
    TaskSetRevision(
        version="0.9.0",
        introduced="2026-06-05",
        total_tasks=313,
        added_task_numbers=(309, 313),
        modules=("tasks_vcs.py",),
        notes=(
            "Added five multi-file / multi-step version-control workflows that "
            "stress step-budget and cross-file propagation: scale rename "
            "refactor across twelve conflicted call sites, module split into a "
            "package, an ordered nine-patch stack across four files plus a "
            "synthesised summary, policy-manifest-driven resolution of sixteen "
            "modules, and precedence deep-merge of five config fragments. All "
            "frontier-solvable (Opus 4.8 and GPT-5.5 pass); gold verified."
        ),
    ),

    TaskSetRevision(
        version="0.10.0",
        introduced="2026-06-30",
        total_tasks=330,
        added_task_numbers=(314, 330),
        modules=("tasks_skills.py",),
        notes=(
            "Added seventeen skill-discriminator tasks covering fictional brand "
            "and style guides, internal codebooks and policies, bespoke formats, "
            "skill selection/distractor axes, code-skill creation/repair, "
            "fictional DSL/protocol/library specs, spreadsheet reconciliation, "
            "and ArcFlux calculation methods. C1/C1b debugging prototypes were "
            "kept out after no-skill controls showed no skill uplift."
        ),
    ),

    TaskSetRevision(
        version="0.11.0",
        introduced="2026-07-02",
        total_tasks=337,
        added_task_numbers=(331, 337),
        modules=("tasks_adversarial.py",),
        notes=(
            "Added an adversarial/robustness pilot: the agent must diagnose and "
            "work around a hostile environment rather than execute a clean task. "
            "Seven obstacle families — Python 2 source to port, a broken "
            "documented build command, a Windows-1251 data file, a "
            "permission-locked (chmod 000) file, an instruction naming a "
            "nonexistent file, a hardcoded absolute path that does not resolve, "
            "and a skill whose referenced template is missing (documented "
            "inline fallback). Verifiers stay mechanical and gold-verified; the "
            "permission task loses discrimination under root (documented)."
        ),
    ),

    TaskSetRevision(
        version="0.13.0",
        introduced="2026-07-02",
        total_tasks=351,
        added_task_numbers=(338, 351),
        modules=("tasks_adversarial.py",),
        notes=(
            "Completed the adversarial/robustness wave with fourteen more "
            "obstacles: a removed-stdlib import (collections.abc), a misleading "
            ".python-version distractor, an unneeded uninstallable dependency, a "
            "set -e script aborting on a missing command, a documented npm build "
            "in a Python project, a gzip stream disguised as .txt, a BOM/NUL-"
            "polluted log to sanitize, an AGENTS.md that lies about the src "
            "layout, a wrong tests-dir misdirection, a broken import path, a "
            "submodule missing from its package, a SKILL.md with unclosed "
            "frontmatter, two contradictory (deprecated vs authoritative) "
            "skills, and a context-discipline-at-scale task (a ~100 MB log the "
            "agent must stream/grep rather than read whole). All gold-verified "
            "and offline."
        ),
    ),
    TaskSetRevision(
        version="0.14.0",
        introduced="2026-07-23",
        total_tasks=371,
        added_task_numbers=(352, 371),
        modules=("tasks_tbench_lite.py",),
        notes=(
            "Added twenty deterministic Terminal-Bench-inspired subtask tasks "
            "for calibrating weaker coding agents. All checks are mechanical, "
            "offline, and gold-verified."
        ),
    ),
    TaskSetRevision(
        version="0.15.0",
        introduced="2026-07-27",
        total_tasks=391,
        added_task_numbers=(372, 391),
        modules=("tasks_cli.py",),
        notes=(
            "Added a twenty-task CLI-composition wave. Thirteen tasks drive "
            "bespoke command-line tools shipped per task (logq, pktool, xtab, "
            "cfgctl, depwalk, slicer) built so that reading --help is the only "
            "way in. Their surface is deliberately unconventional — a leading "
            "verb, --src/--cap/--map instead of --input, value mini-languages "
            "like --span LO..HI, --pick level=ERROR,WARN and --slice 1:5, "
            "--shape instead of --format — so a guessed familiar-looking "
            "invocation exits non-zero rather than half-working. On top of "
            "that the semantics that decide the answer (exclusive upper "
            "bounds, nearest-rank percentiles, margins summed before "
            "normalisation, corrupt-record policy, first-occurrence list "
            "dedupe) appear only in the --help epilog, so even reimplementing "
            "the work by hand requires reading it. Two tools read binary or "
            "fixed-width payloads. The remaining seven tasks exercise POSIX "
            "tools (multi-key sort, join with -1/-2/-a/-e/-o, comm, grep -oE "
            "with uniq -c, find predicates with xargs -0, awk aggregation, sed "
            "ranges with capture groups): the agent writes solve.sh and the "
            "verifier deletes the artifact and executes the script, rejecting "
            "general-purpose interpreters and, per task, the one tool that "
            "would collapse the exercise. All checks are mechanical, offline, "
            "and gold-verified; the shell half needs bash on PATH. All "
            "frontier-solvable, confirmed on two independent agent harnesses "
            "and providers: Claude Code 2.1.220 driving claude-opus-5 and Kimi "
            "Code 0.29.0 driving moonshotai/kimi-k3 (both reasoning=default) "
            "each pass 20/20, the former in a median of five agent steps per "
            "task. Two harnesses agreeing rules out a quirk of one agent loop "
            "and confirms the prompts are unambiguous and the help text "
            "suffices to reach the exact expected artifact."
        ),
    ),
    TaskSetRevision(
        version="0.16.0",
        introduced="2026-07-28",
        total_tasks=391,
        added_task_numbers=(0, 0),
        modules=(
            "core.py",
            "verifiers.py",
            "tasks.py",
            "tasks_extra.py",
            "tasks_more.py",
            "tasks_hard.py",
            "tasks_extreme.py",
            "tasks_diagnostic.py",
            "tasks_memory.py",
            "tasks_agentic.py",
            "tasks_skills.py",
            "tasks_adversarial.py",
            "tasks_cli.py",
        ),
        notes=(
            "No tasks added or removed; an audit of all 391 corrected defects "
            "that gold-verification cannot see, since gold writes a "
            "precomputed constant and the verifier checks that same constant. "
            "Scores are not comparable with 0.15.0 and earlier. Four classes "
            "were fixed. (1) Winnable without work: task 35 passed on a "
            "verbatim copy of its fixture because file_lines_equal drops the "
            "blank lines the task asks to remove; task 101 quoted its own "
            "answer as a format example; task 335 passed on an untouched "
            "workspace, so an idle agent scored the same as one that spotted "
            "the contradiction; task 341 named both the file and its contents; "
            "and the ten shell tasks accepted a hardcoded printf. (2) Correct "
            "work rejected: task 89 said 'отступы сохраняй' over a block the "
            "verifier wanted dedented — half its observed failures were agents "
            "obeying the prompt; tasks 222/232 accepted one phrasing of a "
            "memory key the convention never prescribes; task 180 blessed "
            "`statistics` while pinning numpy's percentile convention; task "
            "323 said 'заменить любые пробелы' but tested run-collapsing; task "
            "349 graded the harness by checking only one of two identical "
            "skill copies. (3) Prompt requirements left ungraded: eighteen "
            "tasks forbade editing the tests with nothing but pytest_passes "
            "behind it, so rewriting the suite to `assert True` scored full "
            "marks; tasks 231/253 skipped dot-files and so could not see a "
            "secret written to .env; task 220's required deletion and task "
            "216's ordering went unchecked. (4) Platform and self-pollution: "
            "Task.setup now writes fixtures as UTF-8 with LF, so byte-level "
            "tasks are no longer unwinnable on Windows; path separators are "
            "normalised where a path is compared; solve.sh saved with CRLF no "
            "longer dies on 'set: pipefail\\r'; the interpreter ban no longer "
            "fires on comments nor is evaded by /usr/bin/python3 or gawk; and "
            "counting tasks are scoped to src/ so a helper script the agent "
            "writes is not itself counted."
        ),
    ),
)

CURRENT_TASK_SET_REVISION = TASK_SET_REVISIONS[-1]
TASK_SET_VERSION = CURRENT_TASK_SET_REVISION.version
EXPECTED_TASK_COUNT = CURRENT_TASK_SET_REVISION.total_tasks


def task_number(task_id: str) -> int | None:
    """Extract the numeric component from ids like ``task_042_name``."""
    rest = task_id.removeprefix("task_")
    head, _, _tail = rest.partition("_")
    try:
        return int(head)
    except ValueError:
        return None


def revision_for_task_id(task_id: str) -> TaskSetRevision | None:
    """Return the revision that introduced ``task_id``."""
    number = task_number(task_id)
    if number is None:
        return None
    for revision in TASK_SET_REVISIONS:
        start, end = revision.added_task_numbers
        if start <= number <= end:
            return revision
    return None


def validate_task_set_metadata(tasks: Iterable[Any]) -> list[str]:
    """Check that task registry shape matches the current version metadata."""
    task_list = list(tasks)
    errors: list[str] = []
    ids = [getattr(task, "id", "") for task in task_list]
    numbers = [task_number(task_id) for task_id in ids]

    if len(task_list) != EXPECTED_TASK_COUNT:
        errors.append(
            f"task count is {len(task_list)}, but version metadata expects "
            f"{EXPECTED_TASK_COUNT}"
        )
    if len(set(ids)) != len(ids):
        errors.append("task ids are not unique")
    if any(number is None for number in numbers):
        bad = [task_id for task_id, number in zip(ids, numbers, strict=True) if number is None]
        errors.append(f"task ids without numeric component: {bad!r}")
    else:
        expected_numbers = list(range(1, EXPECTED_TASK_COUNT + 1))
        if numbers != expected_numbers:
            errors.append(
                f"task numbers are not continuous 1..{EXPECTED_TASK_COUNT}: "
                f"got first={numbers[:5]!r}, last={numbers[-5:]!r}"
            )
    return errors
