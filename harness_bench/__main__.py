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
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

from harness_bench.harbor_export import export_harbor_dataset
from harness_bench.metrics import default_metric_ks
from harness_bench.runner import (
    normalize_json_output_path,
    run_all,
    set_results_json_command,
    summarize,
    verify_gold,
    write_results_json,
)
from harness_bench.runner_cli import DEFAULT_CLI_COMMAND, DEFAULT_TIMEOUT_SECONDS, run_all_cli
from harness_bench.runner_openrouter import DEFAULT_OPENROUTER_MODEL
from harness_bench.runner_openrouter import run_all as run_all_openrouter
from harness_bench.runner_pure import run_all as run_all_pure
from harness_bench.tasks import ALL_TASKS, get_task
from harness_bench.versioning import (
    CURRENT_TASK_SET_REVISION,
    TASK_SET_REVISIONS,
    TASK_SET_VERSION,
    validate_task_set_metadata,
)


def _has_runtime_error(results: Sequence[object]) -> bool:
    return any(bool(getattr(r, "error", None)) for r in results)


def _exit_code(
    results: Sequence[object],
    *,
    allow_task_failures: bool,
    fail_on_runtime_error: bool = False,
) -> int:
    """Return the process exit code for a completed benchmark run."""
    if fail_on_runtime_error and _has_runtime_error(results):
        return 1
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


def _maybe_write_json(args: argparse.Namespace, results: list) -> None:
    json_output = getattr(args, "json_output", None)
    if json_output:
        write_results_json(results, json_output)
        print(f"\nWrote results JSON to {json_output}")


def _metric_ks_for_args(
    args: argparse.Namespace,
) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    if args.attempts == 1 and not args.pass_at and not args.pass_hat:
        return None
    return _resolve_metric_ks(args)


def _summarize_run(
    results: list,
    metric_ks: tuple[tuple[int, ...], tuple[int, ...]] | None,
) -> None:
    if metric_ks is None:
        summarize(results)
        return
    pass_at_ks, pass_hat_ks = metric_ks
    summarize(results, pass_at_ks=pass_at_ks, pass_hat_ks=pass_hat_ks)


def _cmd_run(args: argparse.Namespace) -> int:
    metric_ks = _metric_ks_for_args(args)
    results = run_all(
        task_ids=args.task,
        keep_workspace=args.keep,
        recursion_limit=args.recursion_limit,
        concurrency=args.concurrency,
        attempts=args.attempts,
        json_output=args.json_output,
    )
    _summarize_run(results, metric_ks)
    _maybe_write_json(args, results)
    return _exit_code(results, allow_task_failures=args.allow_task_failures)


def _cmd_run_openrouter(args: argparse.Namespace) -> int:
    metric_ks = _metric_ks_for_args(args)
    results = run_all_openrouter(
        task_ids=args.task,
        model_name=args.model,
        keep_workspace=args.keep,
        recursion_limit=args.recursion_limit,
        max_tokens=args.max_tokens,
        concurrency=args.concurrency,
        harness_profile=args.harness_profile,
        attempts=args.attempts,
        json_output=args.json_output,
        transient_attempts=args.transient_attempts,
        fail_on_runtime_error=args.fail_on_runtime_error,
    )
    _summarize_run(results, metric_ks)
    _maybe_write_json(args, results)
    return _exit_code(
        results,
        allow_task_failures=args.allow_task_failures,
        fail_on_runtime_error=args.fail_on_runtime_error,
    )


def _cmd_run_pure(args: argparse.Namespace) -> int:
    metric_ks = _metric_ks_for_args(args)
    results = run_all_pure(
        task_ids=args.task,
        keep_workspace=args.keep,
        recursion_limit=args.recursion_limit,
        concurrency=args.concurrency,
        attempts=args.attempts,
        json_output=args.json_output,
    )
    _summarize_run(results, metric_ks)
    _maybe_write_json(args, results)
    return _exit_code(results, allow_task_failures=args.allow_task_failures)


def _cmd_run_cli(args: argparse.Namespace) -> int:
    metric_ks = _metric_ks_for_args(args)
    results = run_all_cli(
        task_ids=args.task,
        cli_command=args.cli_command,
        timeout=args.timeout,
        keep_workspace=args.keep,
        concurrency=args.concurrency,
        attempts=args.attempts,
        json_output=args.json_output,
    )
    _summarize_run(results, metric_ks)
    _maybe_write_json(args, results)
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


def _cmd_apply_gold(args: argparse.Namespace) -> int:
    task = get_task(args.task)
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    task.apply_gold(workspace)
    print(f"Applied gold solution for {task.id} in {workspace}")
    return 0


def _cmd_verify_task(args: argparse.Namespace) -> int:
    task = get_task(args.task)
    workspace = Path(args.workspace)
    result = task.verify(workspace)
    if args.json:
        print(
            json.dumps(
                {
                    "task_id": task.id,
                    "workspace": str(workspace),
                    "passed": result.passed,
                    "message": result.message,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        status = "OK" if result.passed else "BAD"
        print(f"[{status}] {task.id}: {result.message}")
    return 0 if result.passed else 1


def _cmd_export_harbor(args: argparse.Namespace) -> int:
    result = export_harbor_dataset(
        args.output,
        task_ids=args.task,
        org=args.org,
        dataset=args.dataset,
        clean=args.clean,
    )
    print(f"Exported {result.task_count} Harbor task(s) to {result.output_dir}")
    return 0


def _add_json_output(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--json-output",
        dest="json_output",
        nargs="?",
        const="",
        default=None,
        type=normalize_json_output_path,
        metavar="PATH",
        help=(
            "Write a machine-readable JSON report (aggregate pass_rate plus a "
            "per-task breakdown with tags) to this path. Bare filenames are "
            "stored under jobs/. If PATH is omitted, a timestamped JSON file "
            "is created under jobs/. If the file exists, completed task "
            "attempts are loaded from it and skipped."
        ),
    )


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected a positive integer, got {value!r}"
        ) from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value!r}")
    return parsed


def _add_metric_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-k",
        "--attempts",
        type=_positive_int,
        default=1,
        help=(
            "Run each selected task N independent times. Default: 1. "
            "Use N > 1 to compute pass@K / pass^K for K=1..N."
        ),
    )
    parser.add_argument(
        "--pass-at",
        "--pass@",
        dest="pass_at",
        action="append",
        type=_positive_int,
        metavar="K",
        help=(
            "Print pass@K (at least one of K attempts passes). Repeatable. "
            "Defaults to all K values from 1 to --attempts."
        ),
    )
    parser.add_argument(
        "--pass-hat",
        "--pass-caret",
        "--pass^",
        dest="pass_hat",
        action="append",
        type=_positive_int,
        metavar="K",
        help=(
            "Print pass^K (all K attempts pass). Repeatable. "
            "Defaults to all K values from 1 to --attempts."
        ),
    )


def _resolve_metric_ks(args: argparse.Namespace) -> tuple[tuple[int, ...], tuple[int, ...]]:
    default_pass_at, default_pass_hat = default_metric_ks(args.attempts)
    pass_at_ks = tuple(args.pass_at) if args.pass_at else default_pass_at
    pass_hat_ks = tuple(args.pass_hat) if args.pass_hat else default_pass_hat
    too_large = [k for k in (*pass_at_ks, *pass_hat_ks) if k > args.attempts]
    if too_large:
        joined = ", ".join(str(k) for k in too_large)
        raise SystemExit(
            f"Metric k cannot exceed --attempts ({args.attempts}); got: {joined}"
        )
    return pass_at_ks, pass_hat_ks


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
    _add_metric_args(p_run)
    _add_json_output(p_run)
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
    p_or.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help=(
            "Optional ChatOpenAI max_tokens override. Leave unset to preserve "
            "the provider/model default."
        ),
    )
    p_or.add_argument("--concurrency", type=int, default=1)
    p_or.add_argument(
        "--transient-attempts",
        type=int,
        default=5,
        help=(
            "Total attempts for transient model HTTP/timeout/transport errors "
            "before counting the task as failed (default: 5)."
        ),
    )
    _add_metric_args(p_or)
    p_or.add_argument(
        "--harness-profile",
        dest="harness_profile",
        default=None,
        help=(
            "Bridge a registered built-in deepagents harness profile onto the "
            "chosen OpenRouter model so it actually applies (e.g. "
            "'anthropic:claude-sonnet-4-6' for the built-in Claude Sonnet 4.6 "
            "profile). The GigaChat profile is never registered by this runner."
        ),
    )
    _add_json_output(p_or)
    p_or.add_argument(
        "--allow-task-failures",
        action="store_true",
        help="Exit 0 when the harness completes even if some benchmark tasks fail.",
    )
    p_or.add_argument(
        "--fail-on-runtime-error",
        action="store_true",
        help=(
            "Exit non-zero and stop scheduling more tasks when a task fails with "
            "an agent/runtime exception recorded in the JSON error field."
        ),
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
    _add_metric_args(p_pure)
    _add_json_output(p_pure)
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

    p_apply_gold = sub.add_parser(
        "apply-gold",
        help="Apply one task's gold solution to a workspace (no Docker required)",
    )
    p_apply_gold.add_argument("--task", required=True, help="Task id")
    p_apply_gold.add_argument(
        "--workspace",
        default=".",
        help="Workspace path to modify (default: current directory)",
    )
    p_apply_gold.set_defaults(func=_cmd_apply_gold)

    p_verify_task = sub.add_parser(
        "verify-task",
        help="Run one task verifier against a workspace (no Docker required)",
    )
    p_verify_task.add_argument("--task", required=True, help="Task id")
    p_verify_task.add_argument(
        "--workspace",
        default=".",
        help="Workspace path to inspect (default: current directory)",
    )
    p_verify_task.add_argument("--json", action="store_true", help="Print result as JSON")
    p_verify_task.set_defaults(func=_cmd_verify_task)

    p_harbor = sub.add_parser(
        "export-harbor",
        help="Generate a local Harbor dataset from the benchmark task registry",
    )
    p_harbor.add_argument(
        "--output",
        default="harbor_dataset",
        help="Output directory (default: harbor_dataset)",
    )
    p_harbor.add_argument(
        "--task",
        action="append",
        help="Task id (repeatable). Export all tasks if omitted.",
    )
    p_harbor.add_argument("--org", default="ai-forever", help="Harbor org namespace")
    p_harbor.add_argument(
        "--dataset",
        default="harness-bench-fast",
        help="Harbor dataset name",
    )
    p_harbor.add_argument(
        "--clean",
        action="store_true",
        help="Delete the output directory before exporting",
    )
    p_harbor.set_defaults(func=_cmd_export_harbor)

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
    _add_metric_args(p_cli)
    _add_json_output(p_cli)
    p_cli.add_argument(
        "--allow-task-failures",
        action="store_true",
        help="Exit 0 when the harness completes even if some benchmark tasks fail.",
    )
    p_cli.set_defaults(func=_cmd_run_cli)

    return parser


def _command_for_argv(argv: list[str] | None) -> str:
    args = sys.argv[1:] if argv is None else argv
    return shlex.join(["python", "-m", "harness_bench", *args])


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    set_results_json_command(_command_for_argv(argv))
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrupted by user; shutdown complete.", file=sys.stderr)
        return 130
    finally:
        set_results_json_command(None)


if __name__ == "__main__":
    sys.exit(main())
