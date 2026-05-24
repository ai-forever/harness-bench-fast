"""Command-line entry point for the benchmark.

Examples:

    # List all tasks
    uv run python -m harness_bench list

    # Run the whole benchmark against GigaChat (uses .env for credentials)
    uv run python -m harness_bench run

    # Run a couple of tasks and keep the temp workspaces for inspection
    uv run python -m harness_bench run --task task_01_create_hello --task task_06_toggle_debug --keep

    # Sanity-check verifiers without any LLM calls
    uv run python -m harness_bench verify-gold
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from harness_bench.runner import run_all, summarize, verify_gold
from harness_bench.runner_cli import DEFAULT_CLI_COMMAND, DEFAULT_TIMEOUT_SECONDS, run_all_cli
from harness_bench.runner_openrouter import DEFAULT_OPENROUTER_MODEL
from harness_bench.runner_openrouter import run_all as run_all_openrouter
from harness_bench.runner_pure import run_all as run_all_pure
from harness_bench.tasks import ALL_TASKS
from harness_bench.versioning import (
    CURRENT_TASK_SET_REVISION,
    TASK_SET_REVISIONS,
    TASK_SET_VERSION,
    validate_task_set_metadata,
)


def _exit_code(results: Sequence[object], *, allow_task_failures: bool) -> int:
    """Return the process exit code for a completed benchmark run."""
    if allow_task_failures:
        return 0
    return 0 if all(getattr(r, "passed", False) for r in results) else 1


def _cmd_list(_args: argparse.Namespace) -> int:
    for task in ALL_TASKS:
        tags = f"  [{', '.join(task.tags)}]" if task.tags else ""
        print(f"  {task.id} — {task.name}{tags}")
    print(f"\nTotal: {len(ALL_TASKS)} tasks")
    print(f"Task-set version: {TASK_SET_VERSION}")
    return 0


def _cmd_version(args: argparse.Namespace) -> int:
    errors = validate_task_set_metadata(ALL_TASKS)
    if args.json:
        payload = {
            "task_set_version": TASK_SET_VERSION,
            "task_count": len(ALL_TASKS),
            "expected_task_count": CURRENT_TASK_SET_REVISION.total_tasks,
            "revisions": [
                {
                    "version": revision.version,
                    "introduced": revision.introduced,
                    "total_tasks": revision.total_tasks,
                    "added_task_numbers": list(revision.added_task_numbers),
                    "modules": list(revision.modules),
                    "notes": revision.notes,
                }
                for revision in TASK_SET_REVISIONS
            ],
            "metadata_errors": errors,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Task-set version: {TASK_SET_VERSION}")
        print(f"Current tasks: {len(ALL_TASKS)}")
        print()
        print("Revisions:")
        for revision in TASK_SET_REVISIONS:
            print(
                f"  {revision.version:7s} {revision.introduced}  "
                f"+{revision.added_range:7s} total={revision.total_tasks:3d}  "
                f"{revision.notes}"
            )
        if errors:
            print()
            print("Metadata check failed:")
            for error in errors:
                print(f"  - {error}")
    return 1 if args.check and errors else 0


def _cmd_run(args: argparse.Namespace) -> int:
    results = run_all(
        task_ids=args.task,
        keep_workspace=args.keep,
        recursion_limit=args.recursion_limit,
        concurrency=args.concurrency,
    )
    summarize(results)
    return _exit_code(results, allow_task_failures=args.allow_task_failures)


def _cmd_run_openrouter(args: argparse.Namespace) -> int:
    results = run_all_openrouter(
        task_ids=args.task,
        model_name=args.model,
        keep_workspace=args.keep,
        recursion_limit=args.recursion_limit,
        concurrency=args.concurrency,
    )
    summarize(results)
    return _exit_code(results, allow_task_failures=args.allow_task_failures)


def _cmd_run_pure(args: argparse.Namespace) -> int:
    results = run_all_pure(
        task_ids=args.task,
        keep_workspace=args.keep,
        recursion_limit=args.recursion_limit,
        concurrency=args.concurrency,
    )
    summarize(results)
    return _exit_code(results, allow_task_failures=args.allow_task_failures)


def _cmd_run_cli(args: argparse.Namespace) -> int:
    results = run_all_cli(
        task_ids=args.task,
        cli_command=args.cli_command,
        timeout=args.timeout,
        keep_workspace=args.keep,
        concurrency=args.concurrency,
    )
    summarize(results)
    return _exit_code(results, allow_task_failures=args.allow_task_failures)


def _cmd_verify_gold(args: argparse.Namespace) -> int:
    results = verify_gold(task_ids=args.task)
    failed = [r for r in results if not r.passed]
    print()
    print("=" * 64)
    print(f"Gold-verification: {len(results) - len(failed)}/{len(results)} OK")
    if failed:
        print()
        print("Verifier failures (likely bugs in the verifier or gold solution):")
        for r in failed:
            head = (r.message or "").splitlines()
            print(f"  - {r.task_id}: {head[0] if head else ''}")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m harness_bench")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List all benchmark tasks")
    p_list.set_defaults(func=_cmd_list)

    p_version = sub.add_parser("version", help="Show benchmark task-set version metadata")
    p_version.add_argument("--json", action="store_true", help="Print metadata as JSON")
    p_version.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if task registry and version metadata disagree",
    )
    p_version.set_defaults(func=_cmd_version)

    p_run = sub.add_parser("run", help="Run benchmark with the GigaChat agent")
    p_run.add_argument(
        "--task",
        action="append",
        help="Task id (repeatable). Run all tasks if omitted.",
    )
    p_run.add_argument(
        "--keep",
        action="store_true",
        help="Keep temp workspaces for inspection",
    )
    p_run.add_argument(
        "--recursion-limit",
        type=int,
        default=80,
        help="Cap on agent loop iterations per task (default: 80)",
    )
    p_run.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Run up to N tasks in parallel (default: 1; uses a thread pool, "
        "each task still has its own isolated TemporaryDirectory).",
    )
    p_run.add_argument(
        "--allow-task-failures",
        action="store_true",
        help=(
            "Exit 0 when the harness completes even if some benchmark tasks fail. "
            "Useful for smoke tests that validate runner mechanics rather than model quality."
        ),
    )
    p_run.set_defaults(func=_cmd_run)

    p_or = sub.add_parser(
        "run-openrouter",
        help=(
            "Run benchmark with a deepagents agent backed by an OpenRouter model "
            "(no GigaChat-specific harness profile applied)."
        ),
    )
    p_or.add_argument("--task", action="append", help="Task id (repeatable)")
    p_or.add_argument(
        "--model",
        default=DEFAULT_OPENROUTER_MODEL,
        help=f"OpenRouter model id (default: {DEFAULT_OPENROUTER_MODEL}).",
    )
    p_or.add_argument("--keep", action="store_true", help="Keep temp workspaces")
    p_or.add_argument("--recursion-limit", type=int, default=80)
    p_or.add_argument("--concurrency", type=int, default=1)
    p_or.add_argument(
        "--allow-task-failures",
        action="store_true",
        help="Exit 0 when the harness completes even if some benchmark tasks fail.",
    )
    p_or.set_defaults(func=_cmd_run_openrouter)

    p_pure = sub.add_parser(
        "run-pure",
        help=(
            "Run benchmark with pure deepagents + GigaChat "
            "(without deepagents_gigachat register_harness)."
        ),
    )
    p_pure.add_argument("--task", action="append", help="Task id (repeatable)")
    p_pure.add_argument("--keep", action="store_true", help="Keep temp workspaces")
    p_pure.add_argument("--recursion-limit", type=int, default=80)
    p_pure.add_argument("--concurrency", type=int, default=1)
    p_pure.add_argument(
        "--allow-task-failures",
        action="store_true",
        help="Exit 0 when the harness completes even if some benchmark tasks fail.",
    )
    p_pure.set_defaults(func=_cmd_run_pure)

    p_gold = sub.add_parser(
        "verify-gold",
        help="Sanity-check verifiers against the gold solutions (no LLM calls)",
    )
    p_gold.add_argument("--task", action="append", help="Task id (repeatable)")
    p_gold.set_defaults(func=_cmd_verify_gold)

    p_cli = sub.add_parser(
        "run-cli",
        help=(
            "Run benchmark via an external CLI agent (default: "
            f"`{DEFAULT_CLI_COMMAND}`)."
        ),
    )
    p_cli.add_argument(
        "--task",
        action="append",
        help="Task id (repeatable). Run all tasks if omitted.",
    )
    p_cli.add_argument(
        "--keep",
        action="store_true",
        help="Keep temp workspaces for inspection",
    )
    p_cli.add_argument(
        "--cli-command",
        default=DEFAULT_CLI_COMMAND,
        help=(
            "Shell command-line prefix invoked per task. The task prompt is "
            "appended as the final argument. Default: "
            f"'{DEFAULT_CLI_COMMAND}'."
        ),
    )
    p_cli.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-task timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    p_cli.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Run up to N tasks in parallel (default: 1).",
    )
    p_cli.add_argument(
        "--allow-task-failures",
        action="store_true",
        help="Exit 0 when the harness completes even if some benchmark tasks fail.",
    )
    p_cli.set_defaults(func=_cmd_run_cli)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
