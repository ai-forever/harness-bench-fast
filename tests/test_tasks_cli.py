import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness_bench.tasks import get_task
from harness_bench.tasks_cli import (
    _TOOL_CFGCTL,
    _TOOL_DEPWALK,
    _TOOL_LOGQ,
    _TOOL_PKTOOL,
    _TOOL_SLICER,
    _TOOL_XTAB,
    CLI_TASKS,
    _normalise_newlines,
    _script_produces,
)

_EXPECTED = "ok\n"


def _script_ws(tmp_path: Path, script: str, **files: str) -> Path:
    (tmp_path / "solve.sh").write_text(script, encoding="utf-8")
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


def _verifier(**kwargs):
    return _script_produces("solve.sh", {"out.txt": _EXPECTED}, **kwargs)


def test_script_verifier_passes_when_script_builds_the_artifact(tmp_path: Path) -> None:
    ws = _script_ws(tmp_path, "printf 'ok\\n' > out.txt\n")
    assert _verifier()(ws).passed


def test_script_verifier_fails_on_stale_artifact(tmp_path: Path) -> None:
    # The answer is already on disk but the script does not regenerate it, so
    # the verifier's delete-then-run step has to reject it.
    ws = _script_ws(tmp_path, "true\n", **{"out.txt": _EXPECTED})
    assert not _verifier()(ws).passed


def test_script_verifier_fails_on_wrong_content(tmp_path: Path) -> None:
    ws = _script_ws(tmp_path, "printf 'nope\\n' > out.txt\n")
    assert not _verifier()(ws).passed


def test_script_verifier_fails_when_script_missing(tmp_path: Path) -> None:
    assert not _verifier()(tmp_path).passed


def test_script_verifier_fails_on_nonzero_exit(tmp_path: Path) -> None:
    ws = _script_ws(tmp_path, "printf 'ok\\n' > out.txt\nexit 3\n")
    assert not _verifier()(ws).passed


def test_script_verifier_rejects_interpreters(tmp_path: Path) -> None:
    ws = _script_ws(tmp_path, "python3 -c \"open('out.txt','w').write('ok\\n')\"\n")
    result = _verifier()(ws)
    assert not result.passed
    assert "python3" in result.message


def test_script_verifier_rejects_per_task_banned_tools(tmp_path: Path) -> None:
    ws = _script_ws(tmp_path, "awk 'BEGIN {print \"ok\"}' > out.txt\n")
    result = _verifier(banned_tools=("awk",))(ws)
    assert not result.passed
    assert "awk" in result.message


def test_script_verifier_allows_banned_substring_inside_other_words(tmp_path: Path) -> None:
    # "gawkish" and "mypython" are not invocations of awk or python; a naive
    # substring ban would fail this legitimate script.
    ws = _script_ws(tmp_path, "cat mypython_gawkish.txt > out.txt\n", **{"mypython_gawkish.txt": _EXPECTED})
    assert _verifier(banned_tools=("awk",))(ws).passed


def test_cli_wave_is_registered_and_numbered() -> None:
    assert len(CLI_TASKS) == 20
    numbers = [int(task.id.split("_")[1]) for task in CLI_TASKS]
    assert numbers == list(range(372, 392))
    assert all("cli" in task.tags for task in CLI_TASKS)
    for task in CLI_TASKS:
        assert get_task(task.id) is task


@pytest.mark.skipif(os.name == "nt", reason="Windows has no executable bit; chmod is a no-op there")
def test_bespoke_tools_are_made_executable(tmp_path: Path) -> None:
    task = get_task("task_372_cli_logq_percentile_window")
    task.setup(tmp_path)
    tool = tmp_path / "tools" / "logq"
    assert tool.is_file()
    assert tool.stat().st_mode & 0o111, "tools/* must be executable for ./tools/<name> to work"


def test_fixtures_are_normalised_to_lf(tmp_path: Path) -> None:
    # `Task.setup` writes fixtures in text mode, so on Windows they land with
    # CRLF. Python verifiers read back through universal newlines and never
    # notice, but the shell half hands fixtures to real tools that do: a CRLF
    # customers.csv made `join -o 1.1,2.2,2.3,1.3` splice the last column's CR
    # into the middle of its output.
    (tmp_path / "fixture.csv").write_bytes(b"a,b\r\n1,2\r\n")
    _normalise_newlines(tmp_path)
    assert (tmp_path / "fixture.csv").read_bytes() == b"a,b\n1,2\n"


def test_every_cli_task_normalises_its_fixtures() -> None:
    # The LF guarantee is delivered by the setup callback, so a task that ships
    # without one silently opts out of it.
    assert [task.id for task in CLI_TASKS if task.setup_callback is None] == []


def test_bespoke_tools_are_shipped_with_a_shebang(tmp_path: Path) -> None:
    # The portable half of the same requirement: `./tools/<name>` needs the
    # executable bit on POSIX, but everywhere it needs the interpreter line.
    task = get_task("task_372_cli_logq_percentile_window")
    task.setup(tmp_path)
    first_line = (tmp_path / "tools" / "logq").read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/usr/bin/env python3"


# --- the wave's premise: the tools cannot be driven without reading --help ----

#: source, a plausible invocation built purely from CLI convention, and a term
#: that only the full --help text explains.
_TOOLS = {
    "logq": (_TOOL_LOGQ, ["--input", "a.lq", "--group-by", "component", "--format", "json"], "EXCLUSIVE"),
    "pktool": (_TOOL_PKTOOL, ["--input", "a.cap", "--decode", "--format", "json"], "INCLUSIVE"),
    "xtab": (_TOOL_XTAB, ["--input", "a.csv", "--rows", "r", "--cols", "c", "--value", "v"], "BEFORE any normalisation"),
    "cfgctl": (_TOOL_CFGCTL, ["merge", "--layer", "a.json", "--format", "json"], "ORDER MATTERS"),
    "depwalk": (_TOOL_DEPWALK, ["--graph", "a.json", "--roots", "api", "--format", "list"], "DEFAULTS TO runtime"),
    "slicer": (_TOOL_SLICER, ["--input", "a.fw", "--layout", "m.txt", "--emit", "csv"], "ZERO-BASED"),
}


def _run(tmp_path: Path, name: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    tool = tmp_path / name
    tool.write_text(_TOOLS[name][0], encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(tool), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("name", sorted(_TOOLS))
def test_conventional_flag_guesses_are_rejected(tmp_path: Path, name: str) -> None:
    # The wave only measures anything if guessing a familiar-looking CLI fails.
    # If a rename ever makes one of these succeed, the task stops discriminating
    # between an agent that read the manual and one that pattern-matched.
    guess = _TOOLS[name][1]
    assert _run(tmp_path, name, guess).returncode != 0, f"{name} accepted a guessed invocation: {guess}"


@pytest.mark.parametrize("name", sorted(_TOOLS))
def test_help_documents_the_semantics_that_decide_the_answer(tmp_path: Path, name: str) -> None:
    result = _run(tmp_path, name, ["--help"])
    assert result.returncode == 0
    assert _TOOLS[name][2] in result.stdout, f"{name} --help no longer explains what the verifier depends on"
