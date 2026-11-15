#!/usr/bin/env python3
"""Publish the fork layer in one command.

Runs generators (RU+EN), gates on --check/--lint on a clean git archive of the
staged tree (no sibling ../results/), commits explicit paths, pushes only when
all checks pass (fail-closed), and polls Pages for the live update.

Usage:
    python3 scripts/publish_fork.py               # full publish
    python3 scripts/publish_fork.py --no-push     # generate + commit, skip push
    python3 scripts/publish_fork.py --no-commit   # generate only, skip commit/push
    python3 scripts/publish_fork.py --no-publish  # skip Pages poll
    python3 scripts/publish_fork.py --dry-run     # read-only: show what would run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fork_metadata import (  # noqa: E402
    classify_harness,
    classify_model,
    collect_paths,  # noqa: E402
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"

# Ordered generator invocations: (script, extra_args)
GENERATORS: list[tuple[str, list[str]]] = [
    ("scripts/build_fork_results.py", []),
    ("scripts/build_fork_pages.py", []),
    ("scripts/build_fork_home.py", []),
]

# Explicit paths committed to git (relative to REPO_ROOT)
COMMIT_PATHS: list[str] = [
    "docs/fork-results.md",
    "docs/fork-results.en.md",
    "docs/fork-medium90-results.json",
    "docs/fork-home-results.json",
    "docs/fork.html",
    "docs/fork.en.html",
    "docs/fork-sample-results.json",
    "docs/fork-sample.html",
    "docs/fork-sample.en.html",
]

# Dynamic paths: detail pages (glob patterns)
COMMIT_GLOBS: list[str] = [
    "docs/fork-run-*.html",
    "docs/fork-detail-*.html",
]

PAGES_URL = "https://alex-kul777.github.io/harness-bench-fast/"
PAGES_POLL_INTERVAL = 10  # seconds
PAGES_POLL_TIMEOUT = 300  # seconds

# ---------------------------------------------------------------------------
# In-flight / incomplete run guard
# ---------------------------------------------------------------------------

IN_FLIGHT_MARKERS = ("_in_progress", "_partial", "_running", "_wip")


def _is_incomplete(data: dict) -> bool:
    """Return True if the JSON has a summary but it carries in-flight markers.

    Files without a summary key are old-format runs that generators already
    skip gracefully — they are not treated as incomplete here.
    In-flight/partial runs that DO have a summary but are not finished are the
    real risk; those get the hard-fail.
    """
    summary = data.get("summary")
    if not isinstance(summary, dict):
        # No summary at all: old-format; generators skip it silently.
        return False
    # In-flight markers in summary keys or values
    return any(any(m in str(key).lower() for m in IN_FLIGHT_MARKERS) for key in summary)


def check_incomplete_runs(result_dirs: list[Path] | None = None) -> list[str]:
    """Return list of incomplete run file names."""
    paths = collect_paths(result_dirs)
    incomplete = []
    for name, path in paths.items():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if _is_incomplete(data):
            incomplete.append(name)
    return incomplete


def check_unresolvable_stems(result_dirs: list[Path] | None = None) -> list[str]:
    """Return list of stems that cannot be resolved to (harness, model)."""
    paths = collect_paths(result_dirs)
    unresolvable = []
    for _name, path in paths.items():
        stem = path.stem
        h = classify_harness(stem)
        m = classify_model(stem)
        if h is None or m is None:
            # Check if it's a RUN_META_OVERRIDES candidate — skip that logic here;
            # hard-fail only on actual unresolvable stems that build_fork_pages would skip
            unresolvable.append(stem)
    return unresolvable


# ---------------------------------------------------------------------------
# Concurrency guard
# ---------------------------------------------------------------------------

def _detect_active_run_bench() -> bool:
    """Return True if any run_bench process is currently active."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "run_bench"],
            capture_output=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        # pgrep not available; fall back to ps
        try:
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True, text=True,
            )
            return "run_bench" in result.stdout
        except Exception:
            return False


def _newer_sibling_jsons(cutoff_mtime: float) -> list[Path]:
    """Return sibling ../results/*.json files newer than cutoff_mtime."""
    sibling = REPO_ROOT.parent / "results"
    if not sibling.is_dir():
        return []
    return [p for p in sibling.glob("*.json") if p.stat().st_mtime > cutoff_mtime]


def concurrency_guard(dry_run: bool = False) -> tuple[list[Path], str | None]:
    """Guard against concurrent runs.

    Returns (moved_files, tmp_dir) where moved_files are the sibling jsons
    temporarily moved to tmp_dir. The caller must call restore_guard() after.
    If a run_bench is active, raises SystemExit.
    """
    if _detect_active_run_bench():
        print("error: active run_bench process detected; will not publish mid-run.", file=sys.stderr)
        raise SystemExit(1)

    # Snapshot: files newer than repo's last commit
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%ct"],
            capture_output=True, text=True, check=True,
        )
        cutoff = float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        cutoff = time.time() - 3600  # fallback: 1h ago

    newer = _newer_sibling_jsons(cutoff)
    if not newer:
        return [], None

    if dry_run:
        print(f"[dry-run] would isolate {len(newer)} sibling result(s) during generation")
        return [], None

    tmp_dir = tempfile.mkdtemp(prefix="fork_publish_guard_")
    moved: list[Path] = []
    for src in newer:
        dst = Path(tmp_dir) / src.name
        shutil.move(str(src), str(dst))
        moved.append(src)
    print(f"[guard] temporarily moved {len(moved)} sibling result(s) to {tmp_dir}")
    return moved, tmp_dir


def restore_guard(moved: list[Path], tmp_dir: str | None) -> None:
    """Restore files moved by concurrency_guard."""
    if not moved or tmp_dir is None:
        return
    target = REPO_ROOT.parent / "results"
    target.mkdir(exist_ok=True)
    for original_path in moved:
        src = Path(tmp_dir) / original_path.name
        if src.exists():
            shutil.move(str(src), str(original_path))
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"[guard] restored {len(moved)} sibling result(s)")


# ---------------------------------------------------------------------------
# Generator runners
# ---------------------------------------------------------------------------

def run_generator(script: str, extra: list[str], dry_run: bool) -> int:
    """Run a generator script from REPO_ROOT. Returns exit code."""
    cmd = [sys.executable, str(REPO_ROOT / script)] + extra
    print(f"  {' '.join(cmd)}")
    if dry_run:
        print("  [dry-run] skipped")
        return 0
    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    return result.returncode


def run_generators(dry_run: bool) -> int:
    """Run all generators in order. Returns 0 on success, 1 on first failure."""
    for script, extra in GENERATORS:
        rc = run_generator(script, extra, dry_run)
        if rc != 0:
            print(f"error: generator {script} failed (exit {rc})", file=sys.stderr)
            return 1
    return 0


# ---------------------------------------------------------------------------
# Check gate on clean git archive tree
# ---------------------------------------------------------------------------

def run_checks_on_archive(dry_run: bool) -> int:
    """Run --check and --lint on a clean git archive (no sibling ../results/).

    Stages current files first, then extracts a git archive and runs checks.
    Returns 0 if all pass.
    """
    if dry_run:
        print("[dry-run] would run --check/--lint on git archive")
        return 0

    print("[check] staging files...")
    # Stage all tracked modified files so git archive captures them
    subprocess.run(["git", "-C", str(REPO_ROOT), "add", "--update"], check=True)

    with tempfile.TemporaryDirectory(prefix="fork_archive_check_") as tmp:
        archive_root = Path(tmp) / "repo"
        archive_root.mkdir()
        # git archive of the staged index
        cmd = ["git", "-C", str(REPO_ROOT), "archive", "HEAD"]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
            subprocess.run(["tar", "-x", "-C", str(archive_root)], stdin=proc.stdout, check=True)
            proc.wait()
        # Also overlay the staged (index) files via git checkout-index
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "checkout-index", "-a", "-f",
             f"--prefix={archive_root}/"],
            check=True,
        )

        # No ../results/ in this clean tree — verify
        sibling = archive_root.parent / "results"
        assert not sibling.exists(), "sibling results should not exist in archive tree"

        scripts_path = archive_root / "scripts"
        sys_path_prefix = str(scripts_path)

        def archive_check(script_name: str, args: list[str]) -> int:
            cmd = [sys.executable, str(scripts_path / script_name)] + args
            env = os.environ.copy()
            env["PYTHONPATH"] = sys_path_prefix + os.pathsep + env.get("PYTHONPATH", "")
            result = subprocess.run(cmd, cwd=str(archive_root), env=env)
            return result.returncode

        checks = [
            ("build_fork_results.py", ["--check"]),
            ("build_fork_results.py", ["--lint"]),
            ("build_fork_pages.py", ["--check"]),
            ("build_fork_home.py", ["--check"]),
        ]
        for script, args in checks:
            print(f"  [archive] python3 scripts/{script} {' '.join(args)}")
            rc = archive_check(script, args)
            if rc != 0:
                print(f"error: {script} {' '.join(args)} failed on clean archive (exit {rc})",
                      file=sys.stderr)
                return 1
        print("[check] all clean-archive checks passed")
    return 0


# ---------------------------------------------------------------------------
# Commit and push
# ---------------------------------------------------------------------------

def collect_commit_paths() -> list[str]:
    """Collect explicit commit paths (literals + globs)."""
    paths = list(COMMIT_PATHS)
    for pattern in COMMIT_GLOBS:
        for p in sorted(REPO_ROOT.glob(pattern)):
            rel = str(p.relative_to(REPO_ROOT))
            if rel not in paths:
                paths.append(rel)
    return paths


def git_commit(message: str, dry_run: bool) -> int:
    """Stage and commit explicit paths. Returns 0 on success."""
    paths = collect_commit_paths()
    # Only stage files that exist
    existing = [p for p in paths if (REPO_ROOT / p).exists()]
    if not existing:
        print("nothing to commit")
        return 0
    if dry_run:
        print(f"[dry-run] would git add {len(existing)} paths and commit: {message!r}")
        return 0
    subprocess.run(["git", "-C", str(REPO_ROOT), "add", "--"] + existing, check=True)
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "commit", "-m", message,
         "--allow-empty"],
    )
    return result.returncode


def git_push(dry_run: bool) -> int:
    """Push current branch to origin. Returns 0 on success."""
    if dry_run:
        print("[dry-run] would git push")
        return 0
    result = subprocess.run(["git", "-C", str(REPO_ROOT), "push"])
    return result.returncode


# ---------------------------------------------------------------------------
# Pages poll
# ---------------------------------------------------------------------------

def poll_pages(url: str = PAGES_URL, timeout: int = PAGES_POLL_TIMEOUT,
               interval: int = PAGES_POLL_INTERVAL, dry_run: bool = False) -> bool:
    """Poll Pages until a 200 is received or timeout. Returns True on success."""
    if dry_run:
        print(f"[dry-run] would poll {url}")
        return True
    try:
        import urllib.error
        import urllib.request
    except ImportError:
        print("[pages] urllib not available, skip poll", file=sys.stderr)
        return False

    deadline = time.time() + timeout
    cache_buster = hashlib.sha1(str(time.time()).encode()).hexdigest()[:8]
    poll_url = f"{url}?_cb={cache_buster}"
    print(f"[pages] polling {url} (timeout={timeout}s)...")
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(poll_url, timeout=10) as resp:
                if resp.status == 200:
                    print("[pages] 200 OK")
                    return True
        except Exception:
            pass
        time.sleep(interval)
    print(f"[pages] timed out after {timeout}s", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-publish", action="store_true", help="Skip Pages poll")
    p.add_argument("--no-commit", action="store_true", help="Skip git commit (and push)")
    p.add_argument("--no-push", action="store_true", help="Skip git push")
    p.add_argument("--dry-run", action="store_true", help="Read-only: print what would happen")
    p.add_argument("--commit-message", default="chore(fork): regenerate published pages",
                   help="Commit message")
    p.add_argument("run_json", nargs="?", default=None,
                   help="Optional path to the run JSON that triggered this publish "
                        "(used by the runners' --publish auto-call)")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    dry_run = args.dry_run

    print("=== publish_fork.py ===")

    # 0. Optional explicit run JSON (the runners' --publish auto-call passes it)
    if args.run_json:
        run_path = Path(args.run_json)
        if not run_path.is_file():
            print(f"error: run JSON not found: {run_path}", file=sys.stderr)
            return 1
        try:
            run_data = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read run JSON {run_path}: {exc}", file=sys.stderr)
            return 1
        if not isinstance(run_data, dict) or "summary" not in run_data:
            print(f"error: incomplete run (no top-level summary): {run_path.name}",
                  file=sys.stderr)
            return 1
        print(f"[run] explicit run JSON accepted: {run_path.name}")

    # 1. Hard-fail: check for incomplete runs (no summary / in-flight markers)
    print("[guard] checking for incomplete runs...")
    incomplete = check_incomplete_runs()
    if incomplete:
        print(f"error: {len(incomplete)} incomplete run(s) detected (no summary or in-flight markers):",
              file=sys.stderr)
        for name in sorted(incomplete):
            print(f"  {name}", file=sys.stderr)
        print("fix or remove these before publishing", file=sys.stderr)
        return 1

    # 2. Concurrency guard: detect active run_bench, isolate newer sibling results
    moved, tmp_dir = concurrency_guard(dry_run)

    try:
        # 3. Run generators
        print("[generate] running generators...")
        rc = run_generators(dry_run)
        if rc != 0:
            return rc

        # 4. Check gate on clean git archive
        print("[check] running --check/--lint on clean archive...")
        rc = run_checks_on_archive(dry_run)
        if rc != 0:
            print("error: checks failed on clean archive; aborting (no push)", file=sys.stderr)
            return rc

    finally:
        restore_guard(moved, tmp_dir)

    # 5. Commit
    if not args.no_commit:
        print("[commit] committing pages...")
        rc = git_commit(args.commit_message, dry_run)
        if rc != 0:
            print(f"error: git commit failed (exit {rc})", file=sys.stderr)
            return rc

    # 6. Push (fail-closed: only if checks passed, which they did to reach here)
    if not args.no_commit and not args.no_push:
        print("[push] pushing to origin...")
        rc = git_push(dry_run)
        if rc != 0:
            print(f"error: git push failed (exit {rc}); commit remains on branch", file=sys.stderr)
            return rc
        print("[push] done")

    # 7. Pages poll
    if not args.no_publish and not args.no_commit and not args.no_push:
        poll_pages(dry_run=dry_run)

    print("=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
