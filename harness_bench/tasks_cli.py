"""Twenty CLI-composition tasks (372-391).

The wave measures one narrow skill: driving *command-line tools* correctly.
Two halves, deliberately different in what they stress.

Bespoke tools (``tools/<name>``, shipped per task, Python with no third-party
imports).  Reading ``--help`` is not a shortcut here, it is the only way in, for
two independent reasons.

First, the surface is deliberately *not* the conventional one.  These are
house-style internal tools: they take a verb (``logq rollup``, ``pktool dump``,
``depwalk order``), they spell input ``--src``/``--cap``/``--map`` rather than
``--input``, filters are value mini-languages (``--span LO..HI``,
``--pick level=ERROR,WARN``, ``--only dport=443``, ``--slice 1:5``), and output
is ``--shape`` rather than ``--format``.  Guessing a familiar-looking
invocation does not half-work — it exits non-zero, which
``tests/test_tasks_cli.py`` pins down so a later rename cannot quietly restore
guessability.

Second, the prompt states the goal but **not** the semantics that decide the
answer: that ``--span``'s upper bound is exclusive, that ``pct95`` is
nearest-rank rather than interpolated, that margins are summed before
normalisation, that corrupt records are dropped unless ``--crc keep``, that
``--seq dedupe`` keeps the first occurrence.  All of that lives only in the
epilog of ``--help``.  So even an agent that abandons the tool and reimplements
the work by hand still has to read the help to know what the right answer is.
Several tools read binary or fixed-width payloads, which makes that fallback
markedly more expensive than just invoking the tool.

Known POSIX tools.  The agent writes ``solve.sh`` and the verifier *executes*
it, having first deleted the artifact the script is supposed to produce.  Those
tasks name an allowed toolset (multi-key ``sort``, ``join`` with
``-1/-2/-a/-e/-o``, ``comm``, ``xargs -0``, ``find`` predicates, ``awk``,
``sed``) and ban general-purpose interpreters, so the work has to happen in the
shell rather than in a throwaway Python script.

Every verifier is mechanical, offline, and standard-library only.  The three
"mixed" tasks pipe a bespoke tool's output through the POSIX toolset in one
pipeline.
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import stat
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from harness_bench.core import Task, VerifyResult

# ---------------------------------------------------------------------------
# Shared verifier helpers
# ---------------------------------------------------------------------------

#: Interpreters banned from `solve.sh`. The point of the shell half of the wave
#: is composing CLI tools, and "write a Python one-liner" sidesteps it entirely.
_FORBIDDEN_INTERPRETERS = ("python", "python3", "perl", "node", "ruby", "php", "deno")

#: File the shipped tools append to when they run. Lexical checks on `solve.sh`
#: cannot tell "drove the tool" from "mentioned its name", so the tools record
#: their own invocations and the verifier reads that instead of guessing.
_TOOL_CALL_LOG = ".hb_tool_calls"

#: Common implementation names for a banned tool. Banning `awk` has to ban the
#: implementations too, or the exercise is sidestepped by spelling it `gawk`.
_TOOL_ALIASES: dict[str, tuple[str, ...]] = {
    "awk": ("gawk", "mawk", "nawk", "busybox"),
    "sed": ("gsed",),
}


def _read_text(path: Path, rel: str) -> str | VerifyResult:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return VerifyResult(False, f"{rel} is not valid UTF-8: {exc}")


def _text_equals(rel: str, expected: str):
    """Exact text match, normalising only the trailing newline."""

    def _verify(ws: Path) -> VerifyResult:
        path = ws / rel
        if not path.is_file():
            return VerifyResult(False, f"{rel} missing")
        actual = _read_text(path, rel)
        if isinstance(actual, VerifyResult):
            return actual
        if actual.rstrip("\n") != expected.rstrip("\n"):
            return VerifyResult(
                False,
                f"{rel} mismatch\nExpected:\n{expected.rstrip(chr(10))!r}\nActual:\n{actual.rstrip(chr(10))!r}",
            )
        return VerifyResult(True, f"{rel} matches expected output")

    return _verify


def _json_equals(rel: str, expected: Any):
    def _verify(ws: Path) -> VerifyResult:
        path = ws / rel
        if not path.is_file():
            return VerifyResult(False, f"{rel} missing")
        text = _read_text(path, rel)
        if isinstance(text, VerifyResult):
            return text
        try:
            actual = json.loads(text)
        except json.JSONDecodeError as exc:
            return VerifyResult(False, f"{rel} is not valid JSON: {exc}")
        if actual != expected:
            return VerifyResult(False, f"{rel} JSON mismatch\nExpected: {expected!r}\nActual:   {actual!r}")
        return VerifyResult(True, f"{rel} matches expected JSON")

    return _verify


def _csv_equals(rel: str, expected_rows: list[list[str]], *, delimiter: str = ","):
    """Compare a delimited file cell-by-cell, ignoring blank trailing lines."""

    def _verify(ws: Path) -> VerifyResult:
        path = ws / rel
        if not path.is_file():
            return VerifyResult(False, f"{rel} missing")
        text = _read_text(path, rel)
        if isinstance(text, VerifyResult):
            return text
        rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if row]
        if rows != expected_rows:
            return VerifyResult(False, f"{rel} mismatch\nExpected: {expected_rows!r}\nActual:   {rows!r}")
        return VerifyResult(True, f"{rel} matches expected rows")

    return _verify


def _strip_shell_comments(source: str) -> str:
    """Blank out `#` comments so the ban is matched against code only.

    A comment is a `#` that opens a word outside quotes — the shell's own rule,
    which keeps `${var#prefix}` and `'a#b'` intact. Without this the ban fires
    on prose: a script that says `# count edges per node`, or that restates the
    task's own "no awk, no sed" constraint, is rejected while being correct.
    """
    out = []
    for line in source.splitlines():
        quote: str | None = None
        cut = len(line)
        for i, ch in enumerate(line):
            if quote is not None:
                if ch == quote:
                    quote = None
            elif ch in "'\"":
                quote = ch
            elif ch == "#" and (i == 0 or line[i - 1].isspace()):
                cut = i
                break
        out.append(line[:cut])
    return "\n".join(out)


def _script_produces(
    script_rel: str,
    outputs: dict[str, Any],
    *,
    banned_tools: tuple[str, ...] = (),
    must_use: tuple[str, ...] = (),
    allowed_note: str = "",
    timeout: int = 90,
):
    """Verifier: delete `outputs`, run `bash <script_rel>`, then check them.

    Deleting the artifacts first means a hand-written answer file cannot pass —
    the script itself has to regenerate them. `outputs` maps a relative path to
    either an expected string (exact text) or a callable verifier.

    `banned_tools` names extra commands the task forbids on top of the
    interpreters. Tasks use it to close the shortcut that would collapse the
    exercise — banning `awk` in a `join` task, for instance, since one `awk`
    invocation can otherwise stand in for the whole toolset being measured.

    `must_use` names the inputs the answer has to be derived from — the fixture
    file, the shipped tool, or both. Regenerating an artifact is not by itself
    evidence of work: every expected output here is a handful of lines, so
    `printf 'serde,4\\nutils,3\\n' > hotspots.csv` regenerates it perfectly
    while reading nothing. Requiring the script to name its inputs closes that
    shortcut.
    """
    forbidden = _FORBIDDEN_INTERPRETERS + banned_tools + tuple(
        variant
        for name in banned_tools
        for variant in _TOOL_ALIASES.get(name, ())
    )
    # Match a bare command or one reached through a path, so `/usr/bin/python3`,
    # `./python3` and `../bin/perl` are caught rather than slipping past a
    # lookbehind that treats `/` as part of an innocent word. The optional
    # version suffix matters too: `python3.12`, `python3.11` and `python2` are
    # the same interpreter under a different filename, and a trailing-dot
    # lookahead let every one of them walk straight through the ban.
    pattern = re.compile(
        r"(?<![\w.-])(?:[\w.-]*/)*("
        + "|".join(re.escape(name) for name in forbidden)
        + r")(?:\d+(?:\.\d+)*)?(?![\w-])"
    )

    def _verify(ws: Path) -> VerifyResult:
        bash = shutil.which("bash")
        if bash is None:
            return VerifyResult(False, "bash not found on PATH; this task needs a POSIX shell")
        script = ws / script_rel
        if not script.is_file():
            return VerifyResult(False, f"{script_rel} missing")
        source = _read_text(script, script_rel)
        if isinstance(source, VerifyResult):
            return source
        # An agent editing on Windows saves CRLF; bash then reads `set -o pipefail\r`
        # and dies on "invalid option name". That is a line-ending accident, not a
        # failure to compose CLI tools, so normalise before running.
        raw = script.read_bytes()
        if b"\r\n" in raw:
            script.write_bytes(raw.replace(b"\r\n", b"\n"))
            source = source.replace("\r\n", "\n")
        code = _strip_shell_comments(source)
        # Report the command as it was written — `python3.12`, not the `python`
        # stem — so the message names what the agent actually typed.
        banned = sorted({match.group(0).rsplit("/", 1)[-1] for match in pattern.finditer(code)})
        if banned:
            return VerifyResult(
                False,
                f"{script_rel} uses a tool this task forbids: {banned!r}. {allowed_note}",
            )
        missing = [name for name in must_use if name not in code]
        if missing:
            return VerifyResult(
                False,
                f"{script_rel} never references {missing!r}; the answer must be derived from the "
                f"task's inputs, not written out literally",
            )
        # The reference check above is lexical, and lexical checks prove nothing:
        # `echo active.txt >/dev/null` satisfies it while the answer is printf'd
        # verbatim. The real test is whether the output depends on the input, so
        # after the scored run the inputs are emptied and the script is run
        # again; an answer that survives that was not computed from anything.
        for rel in outputs:
            (ws / rel).unlink(missing_ok=True)
        try:
            proc = subprocess.run(  # noqa: S603 — trusted local benchmark
                [bash, script_rel],
                cwd=ws,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return VerifyResult(False, f"{script_rel} timed out after {timeout}s")
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip()[-400:]
            return VerifyResult(False, f"{script_rel} exit={proc.returncode}; stderr: {tail!r}")
        messages = []
        for rel, expected in outputs.items():
            check = expected if callable(expected) else _text_equals(rel, expected)
            result = check(ws)
            if not result.passed:
                return VerifyResult(False, f"after running {script_rel}: {result.message}")
            messages.append(result.message)

        if must_use:
            starved = _rerun_with_empty_inputs(ws, bash, script_rel, must_use, outputs, timeout)
            if starved is not None:
                return starved
        return VerifyResult(True, f"{script_rel} ran; " + "; ".join(messages))

    return _verify


def _rerun_with_empty_inputs(
    ws: Path,
    bash: str,
    script_rel: str,
    must_use: tuple[str, ...],
    outputs: dict[str, Any],
    timeout: int,
) -> VerifyResult | None:
    """Re-run the script with its declared inputs emptied.

    Returns a failure when the expected artifacts come back anyway, which means
    the answer was baked into the script rather than computed. Returns None when
    the script's output legitimately depends on its inputs.

    Everything happens in a throwaway copy so the scored artifacts are untouched.
    """
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp) / "ws"
        shutil.copytree(ws, sandbox, symlinks=True)
        for rel in must_use:
            target = sandbox / rel
            if target.is_dir():
                for child in sorted(target.rglob("*")):
                    if child.is_file():
                        child.write_text("", encoding="utf-8")
            elif target.is_file():
                target.write_text("", encoding="utf-8")
        for rel in outputs:
            (sandbox / rel).unlink(missing_ok=True)
        try:
            subprocess.run(  # noqa: S603 — trusted local benchmark
                [bash, script_rel],
                cwd=sandbox,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None
        for rel, expected in outputs.items():
            check = expected if callable(expected) else _text_equals(rel, expected)
            if (sandbox / rel).exists() and check(sandbox).passed:
                return VerifyResult(
                    False,
                    f"{script_rel} still produced the expected {rel} after its inputs "
                    f"{list(must_use)!r} were emptied, so the answer is hardcoded rather "
                    f"than derived from the task's data",
                )
    return None


def _instrument_tools(ws: Path) -> None:
    """Make every shipped tool record that it ran, and give it the exec bit.

    `Task.setup` writes plain text files, so without the executable bit the
    agent could only run the tools through an interpreter — which several tasks
    ban.

    The recording matters for measurement. This wave exists to test whether an
    agent can drive an unfamiliar command-line tool, but the verifiers only see
    the artifact, and an artifact is equally reachable by reading the tool's
    source and reimplementing it, or by working the answer out by hand. Lexical
    checks on `solve.sh` cannot close that gap either: any check for a filename
    is satisfied by naming the file in a comment. So each tool appends its own
    name to `_TOOL_CALL_LOG` on startup, and the verifier reads that.

    The log path is absolute and baked in at setup time, so it still lands in
    the workspace if the agent runs the tool from a subdirectory.
    """
    tools = ws / "tools"
    if not tools.is_dir():
        return
    log_path = (ws / _TOOL_CALL_LOG).resolve()
    for entry in sorted(tools.iterdir()):
        if not entry.is_file():
            continue
        lines = entry.read_text(encoding="utf-8").splitlines(keepends=True)
        if lines and lines[0].startswith("#!"):
            probe = (
                "import os as _hb_os, sys as _hb_sys\n"
                "try:\n"
                f"    with open({str(log_path)!r}, 'a', encoding='utf-8') as _hb_f:\n"
                "        _hb_f.write(_hb_os.path.basename(_hb_sys.argv[0]) + '\\n')\n"
                "except OSError:\n"
                "    pass\n"
            )
            # After any `from __future__` import, which the language requires to
            # be the first statement in the file.
            after = max(
                (i for i, ln in enumerate(lines) if ln.startswith("from __future__")),
                default=0,
            )
            lines.insert(after + 1, probe)
            entry.write_text("".join(lines), encoding="utf-8", newline="")
        entry.chmod(entry.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _tools_invoked(ws: Path) -> set[str]:
    """Names of the shipped tools that actually ran in this workspace."""
    log = ws / _TOOL_CALL_LOG
    if not log.is_file():
        return set()
    return {line.strip() for line in log.read_text(encoding="utf-8").splitlines() if line.strip()}




def _normalise_newlines(ws: Path) -> None:
    """Rewrite every fixture with LF endings.

    `Task.setup` writes `setup_files` through `Path.write_text` in text mode, so
    on Windows each fixture lands with CRLF. Python verifiers never notice —
    they read back through universal newlines — but this wave feeds fixtures to
    real shell tools, which do. `join -o 1.1,2.2,2.3,1.3` on a CRLF CSV splices
    the trailing CR of the last column into the middle of its output.

    Called before the binary hooks run, so only text fixtures exist yet.
    """
    for path in ws.rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\r\n" in data:
            path.write_bytes(data.replace(b"\r\n", b"\n"))


def _setup_wave(*extra: Any):
    """Build the `setup_callback` every task in this wave uses.

    Order matters: fixtures are normalised while they are still the only files
    present, then the binary/procedural hooks run, then the tools get their
    executable bit.
    """

    def _callback(ws: Path) -> None:
        _normalise_newlines(ws)
        for hook in extra:
            hook(ws)
        _instrument_tools(ws)

    return _callback


# ---------------------------------------------------------------------------
# Bespoke tool: logq
# ---------------------------------------------------------------------------

_TOOL_LOGQ = r'''#!/usr/bin/env python3
"""logq - query engine for the .lq structured log format."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

MAGIC = "#LOGQ1"
RECORD_FIELDS = ("epoch", "level", "component", "host", "latency_ms", "message")

EPILOG = """\
VERBS
  scan    emit the records that survive the filters
  rollup  aggregate the surviving records by --by

FORMAT
  A .lq file starts with the magic line #LOGQ1. Every following non-empty line
  that does not start with '#' is a record of six '|' separated fields:

    epoch|level|component|host|latency_ms|message

  The message field may itself contain '|' characters; only the first five
  separators are structural.

  Field names accepted by --pick, --drop, --by and --cols are:
  epoch, level, component, host, latency_ms, message.

FILTERS
  --span LO..HI   epoch window. LO is INCLUSIVE, HI is EXCLUSIVE. Either side
                  may be left empty: '..1720014516' is open on the left and
                  '1720009245..' is open on the right.
  --pick F=V,V    keep records whose field F equals one of the listed values.
                  Repeatable; values inside one --pick are OR-ed, separate
                  --pick options are AND-ed.
  --drop F=V,V    remove matching records. Applied after every --pick.
  --grep RE       Python regular expression, case sensitive, tested with
                  re.search against the message field ONLY.

STATS (--stat, used by rollup)
  count          number of records in the group
  sum:latency    sum of latency_ms
  mean:latency   mean latency_ms, rounded HALF-UP to --scale digits
  max:latency    largest latency_ms
  pct95:latency  nearest-rank percentile: sort the group's latencies ascending
                 and take index ceil(0.95 * N) - 1. No interpolation, so the
                 result is always a value that actually occurs.

ORDERING
  rollup rows are ordered by value, then by key ascending. --rank hi sorts the
  value descending (the default), --rank lo ascending; either way ties still
  break by key ascending. --take is applied after ordering.
  scan rows are ordered by epoch ascending, ties broken by host ascending.

SHAPES (--shape)
  lines  rollup: key=value per line. scan: field=value;field=value per line.
  tsv    tab separated, with a header line
  csv    comma separated, with a header line
  json   rollup: list of {"key": ..., "value": ...}. scan: list of objects.
"""


def half_up(value, digits):
    quant = Decimal(1).scaleb(-digits)
    return float(Decimal(repr(value)).quantize(quant, rounding=ROUND_HALF_UP))


def parse_records(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        sys.exit("logq: cannot read %s: %s" % (path, exc))
    lines = text.splitlines()
    if not lines or lines[0].strip() != MAGIC:
        sys.exit("logq: %s is not a .lq file (missing %s header)" % (path, MAGIC))
    records = []
    for raw in lines[1:]:
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = raw.split("|", 5)
        if len(parts) != 6:
            continue
        try:
            epoch = int(parts[0])
            latency = int(parts[4])
        except ValueError:
            continue
        records.append(
            {
                "epoch": epoch,
                "level": parts[1],
                "component": parts[2],
                "host": parts[3],
                "latency_ms": latency,
                "message": parts[5],
            }
        )
    return records


def parse_selectors(options):
    """Turn a list of 'field=v1,v2' strings into [(field, {values}), ...]."""
    parsed = []
    for raw in options or []:
        field, sep, values = raw.partition("=")
        if not sep:
            sys.exit("logq: expected FIELD=VALUE[,VALUE], got %r (see --help)" % raw)
        if field not in RECORD_FIELDS:
            sys.exit("logq: unknown field %r (see --help)" % field)
        parsed.append((field, set(values.split(","))))
    return parsed


def parse_span(raw):
    if raw is None:
        return None, None
    if ".." not in raw:
        sys.exit("logq: --span must be written LO..HI (see --help), got %r" % raw)
    low, _, high = raw.partition("..")
    try:
        return (int(low) if low else None), (int(high) if high else None)
    except ValueError:
        sys.exit("logq: --span bounds must be integers, got %r" % raw)


def apply_filters(records, args):
    low, high = parse_span(args.span)
    picks = parse_selectors(args.pick)
    drops = parse_selectors(args.drop)
    pattern = re.compile(args.grep) if args.grep else None
    out = []
    for rec in records:
        if low is not None and rec["epoch"] < low:
            continue
        if high is not None and rec["epoch"] >= high:
            continue
        if any(str(rec[field]) not in values for field, values in picks):
            continue
        if pattern is not None and not pattern.search(rec["message"]):
            continue
        if any(str(rec[field]) in values for field, values in drops):
            continue
        out.append(rec)
    return out


def aggregate(records, field, stat, digits):
    groups = {}
    for rec in records:
        groups.setdefault(rec[field], []).append(rec)
    result = {}
    for key, rows in groups.items():
        latencies = sorted(row["latency_ms"] for row in rows)
        if stat == "count":
            result[key] = len(rows)
        elif stat == "sum:latency":
            result[key] = sum(latencies)
        elif stat == "mean:latency":
            result[key] = half_up(sum(latencies) / len(latencies), digits)
        elif stat == "max:latency":
            result[key] = max(latencies)
        elif stat == "pct95:latency":
            index = math.ceil(0.95 * len(latencies)) - 1
            result[key] = latencies[max(index, 0)]
    return result


def emit(rows, header, fmt, stream):
    if fmt == "json":
        json.dump(rows, stream, ensure_ascii=False)
        stream.write("\n")
        return
    if fmt == "lines":
        for row in rows:
            stream.write(";".join("%s=%s" % (key, row[key]) for key in header) + "\n")
        return
    delimiter = "\t" if fmt == "tsv" else ","
    writer = csv.writer(stream, delimiter=delimiter, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([row[key] for key in header])


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="logq",
        description="Filter and aggregate .lq structured logs.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("verb", choices=["scan", "rollup"], help="scan emits records, rollup aggregates them")
    parser.add_argument("--src", required=True, metavar="FILE", help="path to a .lq file")
    parser.add_argument("--span", metavar="LO..HI", help="epoch window, LO inclusive and HI exclusive")
    parser.add_argument("--pick", action="append", metavar="F=V,V", help="keep records matching (repeatable)")
    parser.add_argument("--drop", action="append", metavar="F=V,V", help="remove records matching (repeatable)")
    parser.add_argument("--grep", metavar="RE", help="regex over the message field")
    parser.add_argument("--by", choices=["level", "component", "host"], help="rollup key")
    parser.add_argument(
        "--stat",
        choices=["count", "sum:latency", "mean:latency", "max:latency", "pct95:latency"],
        default="count",
        help="rollup statistic (default: count)",
    )
    parser.add_argument("--scale", type=int, default=2, metavar="N", help="digits for mean:latency (default: 2)")
    parser.add_argument("--take", type=int, metavar="N", help="keep only the first N rows after ordering")
    parser.add_argument("--rank", choices=["hi", "lo"], default="hi", help="rollup value order (default: hi)")
    parser.add_argument(
        "--cols",
        metavar="LIST",
        help="comma separated fields for scan (default: all of %s)" % ",".join(RECORD_FIELDS),
    )
    parser.add_argument("--shape", choices=["lines", "tsv", "csv", "json"], default="lines", help="output shape")
    args = parser.parse_args(argv)

    records = apply_filters(parse_records(args.src), args)

    if args.verb == "rollup":
        if not args.by:
            sys.exit("logq: rollup needs --by FIELD (see --help)")
        totals = aggregate(records, args.by, args.stat, args.scale)
        reverse = args.rank == "hi"
        keys = sorted(totals, key=lambda key: (-totals[key] if reverse else totals[key], key))
        rows = [{"key": key, "value": totals[key]} for key in keys]
        if args.take is not None:
            rows = rows[: args.take]
        emit(rows, ["key", "value"], args.shape, sys.stdout)
        return 0

    fields = args.cols.split(",") if args.cols else list(RECORD_FIELDS)
    unknown = [name for name in fields if name not in RECORD_FIELDS]
    if unknown:
        sys.exit("logq: unknown field(s): %s" % ",".join(unknown))
    records.sort(key=lambda rec: (rec["epoch"], rec["host"]))
    if args.take is not None:
        records = records[: args.take]
    emit([{name: rec[name] for name in fields} for rec in records], fields, args.shape, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


# ---------------------------------------------------------------------------
# Bespoke tool: pktool
# ---------------------------------------------------------------------------

_TOOL_PKTOOL = r'''#!/usr/bin/env python3
"""pktool - decoder for the .cap binary packet-record format."""

from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
from pathlib import Path

MAGIC = b"PKT1"
RECORD = ">BIIHHHBB"
RECORD_SIZE = struct.calcsize(RECORD)
PROTOCOLS = {1: "icmp", 6: "tcp", 17: "udp"}
FLAG_BITS = {"fin": 0x01, "syn": 0x02, "ack": 0x04, "rst": 0x08}
FIELDS = ("index", "proto", "src", "dst", "sport", "dport", "length", "flags")

EPILOG = """\
VERBS
  dump   emit one row per surviving packet
  tally  emit per-protocol totals instead of packets

FORMAT
  A .cap file is the 4 byte magic PKT1 followed by fixed 17 byte records packed
  big-endian as >BIIHHHBB:

    proto(1) src(4) dst(4) sport(2) dport(2) length(2) flags(1) checksum(1)

  checksum is the sum of the record's first 16 bytes modulo 256. Records whose
  stored checksum disagrees are corrupt and --crc decides their fate. A trailing
  partial record is always dropped.

  proto is 1=icmp, 6=tcp, 17=udp; any other value decodes as proto-<n>.
  flags is a bitmask: fin=0x01 syn=0x02 ack=0x04 rst=0x08. The rendered flags
  column lists the set names in fin,syn,ack,rst order joined by '+', or '-'
  when no bit is set.
  index is the record's zero-based position in the file, assigned BEFORE any
  filtering, so it survives --only, --slice and friends. Columns available to
  --cols and --order: index, proto, src, dst, sport, dport, length, flags.

CORRUPT RECORDS (--crc)
  drop  leave corrupt records out of the result (THE DEFAULT)
  keep  treat them as ordinary packets
  halt  abort with an error on the first one

FILTERS
  --only KEY=V,V  keep packets whose column KEY is one of the listed values.
                  Repeatable; values inside one --only are OR-ed, separate
                  --only options are AND-ed. Works on proto, dport and sport.
  --bits A+B      require every named flag bit to be set, e.g. syn+ack.
  --net PREFIX    literal prefix test on the dotted-quad source address, so
                  '10.2.' matches 10.2.0.0/16.
  --len MIN..MAX  packet length window, BOTH ENDS INCLUSIVE. Either side may be
                  left empty: '200..' has no upper bound.

ORDER OF OPERATIONS
  decode -> --crc policy -> filters -> --order -> --slice

  --order FIELD:asc or FIELD:desc; without it the file order is kept.
  --slice SKIP:COUNT drops SKIP rows and then keeps COUNT of them. Either part
  may be empty, so ':5' takes the first five and '1:' drops the first one.

SHAPES (--shape)
  tsv/csv carry a header line, json emits a list of objects. --cols picks and
  orders the columns; the default is every column.
"""


def render_ip(value):
    return "%d.%d.%d.%d" % ((value >> 24) & 255, (value >> 16) & 255, (value >> 8) & 255, value & 255)


def render_flags(value):
    names = [name for name, bit in FLAG_BITS.items() if value & bit]
    return "+".join(names) if names else "-"


def decode(path, policy):
    try:
        blob = Path(path).read_bytes()
    except OSError as exc:
        sys.exit("pktool: cannot read %s: %s" % (path, exc))
    if not blob.startswith(MAGIC):
        sys.exit("pktool: %s is not a .cap file (bad magic)" % path)
    body = blob[len(MAGIC) :]
    packets = []
    for index, offset in enumerate(range(0, len(body) - RECORD_SIZE + 1, RECORD_SIZE)):
        chunk = body[offset : offset + RECORD_SIZE]
        proto, src, dst, sport, dport, length, flags, checksum = struct.unpack(RECORD, chunk)
        if sum(chunk[:-1]) % 256 != checksum:
            if policy == "halt":
                sys.exit("pktool: bad checksum in record %d" % index)
            if policy == "drop":
                continue
        packets.append(
            {
                "index": index,
                "proto": PROTOCOLS.get(proto, "proto-%d" % proto),
                "src": render_ip(src),
                "dst": render_ip(dst),
                "sport": sport,
                "dport": dport,
                "length": length,
                "flags": render_flags(flags),
                "_flagbits": flags,
            }
        )
    return packets


def parse_only(options):
    parsed = []
    for raw in options or []:
        key, sep, values = raw.partition("=")
        if not sep:
            sys.exit("pktool: expected KEY=VALUE[,VALUE], got %r (see --help)" % raw)
        if key not in FIELDS:
            sys.exit("pktool: unknown column %r (see --help)" % key)
        parsed.append((key, set(values.split(","))))
    return parsed


def parse_range(raw, option):
    if raw is None:
        return None, None
    if ".." not in raw:
        sys.exit("pktool: %s must be written MIN..MAX (see --help), got %r" % (option, raw))
    low, _, high = raw.partition("..")
    try:
        return (int(low) if low else None), (int(high) if high else None)
    except ValueError:
        sys.exit("pktool: %s bounds must be integers, got %r" % (option, raw))


def parse_slice(raw):
    if raw is None:
        return 0, None
    if ":" not in raw:
        sys.exit("pktool: --slice must be written SKIP:COUNT (see --help), got %r" % raw)
    skip, _, count = raw.partition(":")
    try:
        return (int(skip) if skip else 0), (int(count) if count else None)
    except ValueError:
        sys.exit("pktool: --slice parts must be integers, got %r" % raw)


def keep(packet, only, bits, args):
    if any(str(packet[key]) not in values for key, values in only):
        return False
    for name in bits:
        if not packet["_flagbits"] & FLAG_BITS[name]:
            return False
    if args.net and not packet["src"].startswith(args.net):
        return False
    low, high = parse_range(args.len, "--len")
    if low is not None and packet["length"] < low:
        return False
    if high is not None and packet["length"] > high:
        return False
    return True


def emit(rows, header, fmt, stream):
    if fmt == "json":
        json.dump(rows, stream, ensure_ascii=False)
        stream.write("\n")
        return
    writer = csv.writer(stream, delimiter="\t" if fmt == "tsv" else ",", lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([row[key] for key in header])


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="pktool",
        description="Decode and summarise .cap binary packet captures.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("verb", choices=["dump", "tally"], help="dump emits packets, tally emits totals")
    parser.add_argument("--cap", required=True, metavar="FILE", help="path to a .cap file")
    parser.add_argument("--only", action="append", metavar="KEY=V,V", help="keep matching packets (repeatable)")
    parser.add_argument("--bits", metavar="A+B", help="require these flag bits, e.g. syn+ack")
    parser.add_argument("--net", metavar="PREFIX", help="literal prefix test on the source address")
    parser.add_argument("--len", metavar="MIN..MAX", help="packet length window, both ends inclusive")
    parser.add_argument(
        "--crc",
        choices=["drop", "keep", "halt"],
        default="drop",
        help="what to do with corrupt records (default: drop)",
    )
    parser.add_argument("--order", metavar="FIELD:DIR", help="sort key, e.g. length:desc (default: file order)")
    parser.add_argument("--slice", metavar="SKIP:COUNT", help="drop SKIP rows then keep COUNT")
    parser.add_argument("--cols", metavar="LIST", help="comma separated columns to emit")
    parser.add_argument("--shape", choices=["tsv", "csv", "json"], default="tsv", help="output shape")
    args = parser.parse_args(argv)

    bits = []
    if args.bits:
        bits = args.bits.split("+")
        unknown = [name for name in bits if name not in FLAG_BITS]
        if unknown:
            sys.exit("pktool: unknown flag bit(s): %s (see --help)" % ",".join(unknown))
    only = parse_only(args.only)

    packets = [pkt for pkt in decode(args.cap, args.crc) if keep(pkt, only, bits, args)]

    if args.verb == "tally":
        totals = {}
        for pkt in packets:
            entry = totals.setdefault(pkt["proto"], {"proto": pkt["proto"], "packets": 0, "bytes": 0, "max_length": 0})
            entry["packets"] += 1
            entry["bytes"] += pkt["length"]
            entry["max_length"] = max(entry["max_length"], pkt["length"])
        rows = [totals[key] for key in sorted(totals)]
        emit(rows, ["proto", "packets", "bytes", "max_length"], args.shape, sys.stdout)
        return 0

    if args.order:
        field, _, direction = args.order.partition(":")
        if field not in FIELDS:
            sys.exit("pktool: unknown --order column %r (see --help)" % field)
        if direction not in ("", "asc", "desc"):
            sys.exit("pktool: --order direction must be asc or desc, got %r" % direction)
        packets.sort(key=lambda pkt: pkt[field], reverse=direction == "desc")
    skip, count = parse_slice(args.slice)
    packets = packets[skip:]
    if count is not None:
        packets = packets[:count]

    fields = args.cols.split(",") if args.cols else list(FIELDS)
    unknown = [name for name in fields if name not in FIELDS]
    if unknown:
        sys.exit("pktool: unknown field(s): %s" % ",".join(unknown))
    emit([{name: pkt[name] for name in fields} for pkt in packets], fields, args.shape, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


# ---------------------------------------------------------------------------
# Bespoke tool: xtab
# ---------------------------------------------------------------------------

_TOOL_XTAB = r'''#!/usr/bin/env python3
"""xtab - cross-tabulate a delimited table."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

EPILOG = """\
WHAT IT DOES
  Reads a delimited file with a header line and pivots it: one output row per
  distinct --down value, one output column per distinct --across value, each
  cell aggregating the --cell column of the matching input rows.

  The vocabulary is deliberate: 'down' is the axis that runs down the page and
  'across' is the one that runs along it.

STATS (--stat)
  count  needs no --cell; every other statistic requires it
  sum, mean, max, min operate on the --cell column parsed as a float

EMPTY CELLS
  A down/across pair with no input rows is filled with --blank (default 0). The
  blank value is inserted BEFORE --norm and is excluded from mean.

MARGINS AND NORMALISATION
  --margins appends a TOTAL column (the row's sum) and a TOTAL row (the
  column's sum). Margins are always SUMS, even when --stat is mean or max, and
  they are computed from the aggregated cells BEFORE any normalisation.
  --norm divides each cell by its row total (down), its column total (across)
  or the grand total (grand) and multiplies by 100. The result is rounded
  half-up to --scale digits; a zero denominator yields 0. When --margins and
  --norm are combined the TOTAL cells are normalised too, so a --norm down
  table shows 100 in every TOTAL cell.

ORDERING
  --order-down and --order-across accept 'name' (lexicographic ascending, the
  default) or 'total' (by that row's or column's sum, LARGEST FIRST, ties
  broken by name ascending). Those sums are the aggregated cells, so under
  --stat mean the ordering follows the sum of the means, not the raw input.
  A TOTAL row or column added by --margins is always pinned last.

SHAPES (--shape)
  csv/tsv print the row-label column first with an empty header cell; json
  emits {"columns": [...], "rows": [{"key": ..., "cells": {...}}, ...]}.
  Numbers that are whole are printed without a trailing .0.
"""


def half_up(value, digits):
    quant = Decimal(1).scaleb(-digits)
    return float(Decimal(repr(value)).quantize(quant, rounding=ROUND_HALF_UP))


def render(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="xtab",
        description="Cross-tabulate a delimited table.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("verb", choices=["pivot"], help="the only verb; kept explicit for symmetry")
    parser.add_argument("--src", required=True, metavar="FILE", help="delimited input file with a header line")
    parser.add_argument("--sep", default=",", metavar="CH", help="input delimiter (default: ,)")
    parser.add_argument("--down", required=True, metavar="COL", help="column whose values become output rows")
    parser.add_argument("--across", required=True, metavar="COL", help="column whose values become output columns")
    parser.add_argument("--cell", metavar="COL", help="column to aggregate (required unless --stat count)")
    parser.add_argument("--stat", choices=["count", "sum", "mean", "max", "min"], default="sum", help="statistic")
    parser.add_argument("--blank", default="0", metavar="V", help="value for empty cells (default: 0)")
    parser.add_argument("--scale", type=int, default=2, metavar="N", help="rounding digits (default: 2)")
    parser.add_argument("--order-down", choices=["name", "total"], default="name", help="row order")
    parser.add_argument("--order-across", choices=["name", "total"], default="name", help="column order")
    parser.add_argument("--norm", choices=["none", "down", "across", "grand"], default="none", help="normalise")
    parser.add_argument("--margins", action="store_true", help="append a TOTAL row and column")
    parser.add_argument("--keep-down", metavar="LIST", help="comma separated allow-list of row keys")
    parser.add_argument("--skip-across", metavar="LIST", help="comma separated deny-list of column keys")
    parser.add_argument("--shape", choices=["csv", "tsv", "json"], default="csv", help="output shape")
    args = parser.parse_args(argv)

    if args.stat != "count" and not args.cell:
        sys.exit("xtab: --cell is required unless --stat count (see --help)")

    try:
        text = Path(args.src).read_text(encoding="utf-8")
    except OSError as exc:
        sys.exit("xtab: cannot read %s: %s" % (args.src, exc))
    table = list(csv.DictReader(text.splitlines(), delimiter=args.sep))
    if not table:
        sys.exit("xtab: %s has no data rows" % args.src)
    for column in (args.down, args.across) + ((args.cell,) if args.cell else ()):
        if column not in table[0]:
            sys.exit("xtab: no such column: %s" % column)

    keep_down = set(args.keep_down.split(",")) if args.keep_down else None
    skip_across = set(args.skip_across.split(",")) if args.skip_across else set()

    buckets = {}
    for record in table:
        row_key, col_key = record[args.down], record[args.across]
        if keep_down is not None and row_key not in keep_down:
            continue
        if col_key in skip_across:
            continue
        try:
            number = float(record[args.cell]) if args.cell else 0.0
        except (TypeError, ValueError):
            sys.exit("xtab: %s is not numeric in row %r" % (args.cell, record))
        buckets.setdefault((row_key, col_key), []).append(number)

    row_keys = sorted({key[0] for key in buckets})
    col_keys = sorted({key[1] for key in buckets})

    try:
        blank = float(args.blank)
    except ValueError:
        blank = args.blank

    cells = {}
    for row_key in row_keys:
        for col_key in col_keys:
            values = buckets.get((row_key, col_key))
            if not values:
                cells[(row_key, col_key)] = blank
            elif args.stat == "count":
                cells[(row_key, col_key)] = len(values)
            elif args.stat == "sum":
                cells[(row_key, col_key)] = half_up(sum(values), args.scale)
            elif args.stat == "mean":
                cells[(row_key, col_key)] = half_up(sum(values) / len(values), args.scale)
            elif args.stat == "max":
                cells[(row_key, col_key)] = max(values)
            else:
                cells[(row_key, col_key)] = min(values)

    def numeric(value):
        return value if isinstance(value, (int, float)) else 0.0

    row_totals = {rk: sum(numeric(cells[(rk, ck)]) for ck in col_keys) for rk in row_keys}
    col_totals = {ck: sum(numeric(cells[(rk, ck)]) for rk in row_keys) for ck in col_keys}
    grand = sum(row_totals.values())

    if args.order_down == "total":
        row_keys.sort(key=lambda rk: (-row_totals[rk], rk))
    if args.order_across == "total":
        col_keys.sort(key=lambda ck: (-col_totals[ck], ck))

    if args.margins:
        for rk in row_keys:
            cells[(rk, "TOTAL")] = half_up(row_totals[rk], args.scale)
        for ck in col_keys:
            cells[("TOTAL", ck)] = half_up(col_totals[ck], args.scale)
        cells[("TOTAL", "TOTAL")] = half_up(grand, args.scale)
        col_totals["TOTAL"] = grand
        row_totals["TOTAL"] = grand
        col_keys = col_keys + ["TOTAL"]
        row_keys = row_keys + ["TOTAL"]

    if args.norm != "none":
        for rk in row_keys:
            for ck in col_keys:
                denominator = {
                    "down": row_totals[rk],
                    "across": col_totals[ck],
                    "grand": grand,
                }[args.norm]
                value = numeric(cells[(rk, ck)])
                cells[(rk, ck)] = half_up(100.0 * value / denominator, args.scale) if denominator else 0

    if args.shape == "json":
        payload = {
            "columns": col_keys,
            "rows": [
                {"key": rk, "cells": {ck: cells[(rk, ck)] for ck in col_keys}} for rk in row_keys
            ],
        }
        json.dump(payload, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    writer = csv.writer(sys.stdout, delimiter="\t" if args.shape == "tsv" else ",", lineterminator="\n")
    writer.writerow([""] + col_keys)
    for rk in row_keys:
        writer.writerow([rk] + [render(cells[(rk, ck)]) for ck in col_keys])
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


# ---------------------------------------------------------------------------
# Bespoke tool: cfgctl
# ---------------------------------------------------------------------------

_TOOL_CFGCTL = r'''#!/usr/bin/env python3
"""cfgctl - layer, query and diff JSON configuration fragments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EPILOG = """\
VERBS
  fold   combine stacked layers into one document
  read   pull a single dotted key out of the folded document
  drift  compare two documents key by key
  paths  list the dotted leaf keys of the folded document

STACKING
  --stack is repeatable and ORDER MATTERS: layers are applied left to right and
  a later layer wins. --nest deep (the default) merges nested objects key by
  key; --nest flat replaces a top-level key outright, so a later layer's object
  hides the earlier one entirely rather than being merged into it.

LISTS (--seq)
  Decides what happens when both sides hold a list:
    last    later layer wins outright (THE DEFAULT)
    chain   earlier list followed by later list
    dedupe  chain, then drop duplicates keeping the FIRST occurrence

VARIANTS
  --variant NAME overlays the subtree at variants.NAME as if it were one more
  final layer. The top-level "variants" key is ALWAYS stripped from the output,
  whether or not --variant was given.

POST-PROCESSING, IN THIS ORDER
  1. layers folded
  2. --variant overlay
  3. --put KEY=VALUE, repeatable, dotted path, creates missing parents. The
     value is parsed as JSON when it parses (so true, 7 and [1,2] arrive typed)
     and kept as a string otherwise.
  4. --wipe KEY, repeatable, dotted path; a missing key is not an error
  5. --prune-null removes every key whose value is null, recursively
  6. "variants" stripped

SHAPES (--shape)
  json  pretty printed with two-space indent, keys sorted only with --ordered
  flat  one dotted-key=value line per leaf, always sorted by key, lists joined
        with commas, booleans as true/false, null as an empty value
  env   like flat but the key is upper-cased with dots replaced by underscores
"""


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        sys.exit("cfgctl: cannot read %s: %s" % (path, exc))
    except json.JSONDecodeError as exc:
        sys.exit("cfgctl: %s is not valid JSON: %s" % (path, exc))


def merge(left, right, nest, seq):
    if nest == "flat":
        out = dict(left)
        out.update(right)
        return out
    if not isinstance(left, dict) or not isinstance(right, dict):
        return right
    out = dict(left)
    for key, value in right.items():
        if key in out and isinstance(out[key], list) and isinstance(value, list):
            if seq == "chain":
                out[key] = list(out[key]) + list(value)
            elif seq == "dedupe":
                seen, merged = set(), []
                for item in list(out[key]) + list(value):
                    marker = json.dumps(item, sort_keys=True)
                    if marker not in seen:
                        seen.add(marker)
                        merged.append(item)
                out[key] = merged
            else:
                out[key] = value
        elif key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = merge(out[key], value, nest, seq)
        else:
            out[key] = value
    return out


def dotted_set(document, path, value):
    parts = path.split(".")
    node = document
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def dotted_unset(document, path):
    parts = path.split(".")
    node = document
    for part in parts[:-1]:
        node = node.get(part)
        if not isinstance(node, dict):
            return
    node.pop(parts[-1], None)


def dotted_get(document, path):
    node = document
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


def drop_nulls(node):
    if isinstance(node, dict):
        return {key: drop_nulls(value) for key, value in node.items() if value is not None}
    return node


def leaves(node, prefix=""):
    if isinstance(node, dict):
        out = []
        for key, value in node.items():
            out.extend(leaves(value, "%s.%s" % (prefix, key) if prefix else key))
        return out
    return [(prefix, node)]


def scalar(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(scalar(item) for item in value)
    return str(value)


def render(document, fmt, sort_keys):
    if fmt == "json":
        return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=sort_keys) + "\n"
    lines = []
    for key, value in sorted(leaves(document)):
        name = key.upper().replace(".", "_") if fmt == "env" else key
        lines.append("%s=%s" % (name, scalar(value)))
    return "".join(line + "\n" for line in lines)


def build(args):
    document = {}
    for path in args.stack:
        document = merge(document, load(path), args.nest, args.seq)
    if args.variant:
        overlay, found = dotted_get(document, "variants.%s" % args.variant)
        if not found:
            sys.exit("cfgctl: no such variant: %s" % args.variant)
        document = merge(document, overlay, args.nest, args.seq)
    for assignment in args.put or []:
        key, _, raw = assignment.partition("=")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        dotted_set(document, key, value)
    for key in args.wipe or []:
        dotted_unset(document, key)
    if args.prune_null:
        document = drop_nulls(document)
    document.pop("variants", None)
    return document


def add_layer_args(parser):
    parser.add_argument("--stack", action="append", required=True, metavar="FILE", help="layer file (repeatable)")
    parser.add_argument("--nest", choices=["deep", "flat"], default="deep", help="how objects combine")
    parser.add_argument(
        "--seq",
        choices=["last", "chain", "dedupe"],
        default="last",
        help="how lists combine (default: last)",
    )
    parser.add_argument("--variant", metavar="NAME", help="overlay variants.NAME as a final layer")
    parser.add_argument("--put", action="append", metavar="KEY=VALUE", help="set a dotted key (repeatable)")
    parser.add_argument("--wipe", action="append", metavar="KEY", help="remove a dotted key (repeatable)")
    parser.add_argument("--prune-null", action="store_true", help="remove keys whose value is null")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="cfgctl",
        description="Layer, query and diff JSON configuration fragments.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    p_fold = sub.add_parser("fold", help="combine stacked layers into one document")
    add_layer_args(p_fold)
    p_fold.add_argument("--shape", choices=["json", "flat", "env"], default="json", help="output shape")
    p_fold.add_argument("--ordered", action="store_true", help="sort object keys in json output")

    p_read = sub.add_parser("read", help="pull one dotted key")
    add_layer_args(p_read)
    p_read.add_argument("--at", required=True, metavar="PATH", help="dotted key to read")
    p_read.add_argument("--else", dest="fallback", metavar="V", help="printed when the key is absent")

    p_drift = sub.add_parser("drift", help="compare two documents")
    p_drift.add_argument("--was", required=True, metavar="FILE", help="the reference document")
    p_drift.add_argument("--now", required=True, metavar="FILE", help="the document to inspect")
    p_drift.add_argument(
        "--pick",
        choices=["added", "removed", "changed", "all"],
        default="all",
        help="restrict the report to one class of change (default: all)",
    )

    p_paths = sub.add_parser("paths", help="list dotted leaf keys")
    add_layer_args(p_paths)
    p_paths.add_argument("--under", metavar="PATH", help="only keys under this dotted prefix")

    args = parser.parse_args(argv)

    if args.verb == "fold":
        sys.stdout.write(render(build(args), args.shape, args.ordered))
        return 0

    if args.verb == "read":
        value, found = dotted_get(build(args), args.at)
        if not found:
            if args.fallback is None:
                sys.exit("cfgctl: no such key: %s" % args.at)
            sys.stdout.write(args.fallback + "\n")
            return 0
        sys.stdout.write((json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else scalar(value)) + "\n")
        return 0

    if args.verb == "paths":
        names = [key for key, _ in leaves(build(args))]
        if args.under:
            names = [key for key in names if key == args.under or key.startswith(args.under + ".")]
        sys.stdout.write("".join(name + "\n" for name in sorted(names)))
        return 0

    left = dict(leaves(load(args.was)))
    right = dict(leaves(load(args.now)))
    report = {"added": {}, "removed": {}, "changed": {}}
    for key in sorted(set(left) | set(right)):
        if key not in left:
            report["added"][key] = right[key]
        elif key not in right:
            report["removed"][key] = left[key]
        elif left[key] != right[key]:
            report["changed"][key] = {"from": left[key], "to": right[key]}
    if args.pick != "all":
        report = {args.pick: report[args.pick]}
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


# ---------------------------------------------------------------------------
# Bespoke tool: depwalk
# ---------------------------------------------------------------------------

_TOOL_DEPWALK = r'''#!/usr/bin/env python3
"""depwalk - walk a declared dependency graph."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EPILOG = """\
VERBS
  reach  the transitive closure reachable from --at, those nodes included
  order  a topological order of that closure
  loops  every elementary cycle in the graph, ignoring --at

GRAPH FORMAT
  A JSON object mapping a package name to its record:

    {"api": {"deps": [{"name": "core", "scope": "runtime", "optional": false}]}}

  scope defaults to runtime and optional defaults to false when omitted. A name
  that only ever appears as a dependency is still a node; it simply has no
  outgoing edges.

EDGE FILTERING (applied before any walk)
  --edges takes a comma separated list of scopes and DEFAULTS TO runtime alone,
  so dev and test edges are invisible until you ask for them. Optional edges are
  dropped unless --with-soft is given. --up flips every surviving edge, turning
  "what does X need" into "what breaks if X changes".

WALKS
  reach          the closure from --at; --tips narrows it to nodes that have no
                 outgoing edges left after filtering
  order          repeatedly takes the alphabetically first node whose remaining
                 prerequisites have all been emitted, so a dependency always
                 precedes its dependents. Exits non-zero on a cycle.
  loops          reports cycles; --at and --hops are ignored

  --hops limits how far reach walks: 1 means direct edges only. It is ignored by
  order and loops. Without --at the closure is every node in the graph.

SHAPES (--shape)
  list  one name per line, alphabetically sorted except under order, which
        keeps topological order
  json  {"nodes": [...], "count": N}; loops emit {"cycles": [[...]]}
  dot   a digraph of the surviving edges, one "a" -> "b"; line per edge, sorted

  Each reported cycle is rotated so it starts at its alphabetically smallest
  member, is listed without repeating that member at the end, and the list of
  cycles is sorted.
"""


def load_graph(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        sys.exit("depwalk: cannot read %s: %s" % (path, exc))
    except json.JSONDecodeError as exc:
        sys.exit("depwalk: %s is not valid JSON: %s" % (path, exc))
    if not isinstance(raw, dict):
        sys.exit("depwalk: %s must hold a JSON object" % path)
    return raw


def build_edges(raw, scopes, include_optional, reverse):
    edges = {}
    for name, record in raw.items():
        edges.setdefault(name, set())
        for dep in (record or {}).get("deps", []):
            target = dep["name"]
            edges.setdefault(target, set())
            if dep.get("scope", "runtime") not in scopes:
                continue
            if dep.get("optional", False) and not include_optional:
                continue
            if reverse:
                edges[target].add(name)
            else:
                edges[name].add(target)
    return edges


def closure(edges, roots, depth):
    seen = set(roots)
    frontier = list(roots)
    level = 0
    while frontier and (depth is None or level < depth):
        nxt = []
        for node in frontier:
            for target in edges.get(node, ()):
                if target not in seen:
                    seen.add(target)
                    nxt.append(target)
        frontier = nxt
        level += 1
    return seen


def topo(edges, nodes):
    remaining = {node: {dep for dep in edges.get(node, ()) if dep in nodes} for node in nodes}
    order = []
    while remaining:
        ready = sorted(node for node, deps in remaining.items() if not deps)
        if not ready:
            return None
        pick = ready[0]
        order.append(pick)
        del remaining[pick]
        for deps in remaining.values():
            deps.discard(pick)
    return order


def find_cycles(edges):
    cycles = set()
    path = []
    on_path = set()

    def walk(node):
        path.append(node)
        on_path.add(node)
        for target in sorted(edges.get(node, ())):
            if target in on_path:
                loop = path[path.index(target) :]
                pivot = loop.index(min(loop))
                cycles.add(tuple(loop[pivot:] + loop[:pivot]))
            else:
                walk(target)
        path.pop()
        on_path.discard(node)

    for node in sorted(edges):
        walk(node)
    return [list(cycle) for cycle in sorted(cycles)]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="depwalk",
        description="Walk a declared dependency graph.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("verb", choices=["reach", "order", "loops"], help="which walk to run")
    parser.add_argument("--map", required=True, metavar="FILE", help="dependency graph JSON")
    parser.add_argument("--at", metavar="LIST", help="comma separated starting nodes (default: every node)")
    parser.add_argument("--hops", type=int, metavar="N", help="limit reach to N hops")
    parser.add_argument("--up", action="store_true", help="follow edges backwards (dependents)")
    parser.add_argument(
        "--edges",
        metavar="LIST",
        default="runtime",
        help="comma separated scopes to follow (default: runtime)",
    )
    parser.add_argument("--with-soft", action="store_true", help="also follow optional edges")
    parser.add_argument("--tips", action="store_true", help="keep only nodes with no outgoing edges")
    parser.add_argument("--shape", choices=["list", "json", "dot"], default="list", help="output shape")
    args = parser.parse_args(argv)

    scopes = set(args.edges.split(","))
    unknown_scopes = sorted(scopes - {"runtime", "dev", "test"})
    if unknown_scopes:
        sys.exit("depwalk: unknown scope(s): %s (see --help)" % ",".join(unknown_scopes))

    raw = load_graph(args.map)
    edges = build_edges(raw, scopes, args.with_soft, args.up)

    if args.verb == "loops":
        cycles = find_cycles(edges)
        if args.shape == "json":
            json.dump({"cycles": cycles}, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            sys.stdout.write("".join(" -> ".join(cycle) + "\n" for cycle in cycles))
        return 0

    roots = args.at.split(",") if args.at else sorted(edges)
    unknown = [name for name in roots if name not in edges]
    if unknown:
        sys.exit("depwalk: unknown start node(s): %s" % ",".join(unknown))

    nodes = closure(edges, roots, None if args.verb == "order" else args.hops)

    if args.verb == "order":
        ordered = topo(edges, nodes)
        if ordered is None:
            sys.exit("depwalk: the closure contains a cycle; rerun the loops verb")
        names = ordered
    else:
        names = sorted(nodes)
        if args.tips:
            names = [name for name in names if not edges.get(name)]

    if args.shape == "json":
        json.dump({"nodes": names, "count": len(names)}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    if args.shape == "dot":
        lines = ['"%s" -> "%s";' % (src, dst) for src in sorted(nodes) for dst in sorted(edges.get(src, ())) if dst in nodes]
        sys.stdout.write("digraph deps {\n" + "".join("  " + line + "\n" for line in sorted(lines)) + "}\n")
        return 0
    sys.stdout.write("".join(name + "\n" for name in names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


# ---------------------------------------------------------------------------
# Bespoke tool: slicer
# ---------------------------------------------------------------------------

_TOOL_SLICER = r'''#!/usr/bin/env python3
"""slicer - extract fields from fixed-width records using a layout file."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

OPERATORS = ("==", "!=", ">=", "<=", ">", "<", "~")

EPILOG = """\
VERB
  cut  the only verb: slice records out of a fixed-width file

MAP FILE
  One field per line, four colon separated columns:

    name:offset:width:type

  offset is ZERO-BASED and width is a character count, so name:0:8:str takes
  columns 0..7. type is one of str, int, float. Blank lines and lines starting
  with '#' are ignored. Fields may be declared in any order; --take decides what
  is emitted and in what order.

RECORDS
  Every input line must be at least as long as the highest offset+width in the
  map, and every int/float field must parse. A line that fails either check is
  invalid: by default it is skipped silently, --halt-on-bad aborts on the first
  one, and --tally-bad reports the count on stderr.

PADDING
  Values are stripped of --pad (default: a space) on both sides before typing,
  unless --raw is given. Stripping happens BEFORE --keep is evaluated.

--keep FILTERS
  Repeatable, AND-ed, written as one argument: "field OP value" with SPACES
  AROUND THE OPERATOR. OP is one of == != >= <= > < ~ where ~ is a Python
  regular expression tested with re.search against the untyped string value.
  Comparisons on int/float fields are numeric; on str fields they are
  lexicographic.

ORDER OF OPERATIONS
  parse -> validity policy -> --keep -> --order -> --slice

  --order FIELD:asc or FIELD:desc; without it the file order is kept.
  --slice SKIP:COUNT drops SKIP rows then keeps COUNT. Either part may be empty.

SHAPES (--shape)
  csv/tsv carry a header line unless --bare is given; json emits a list of
  objects. Float values keep the precision they were parsed with.
"""


def load_layout(path):
    fields = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        sys.exit("slicer: cannot read %s: %s" % (path, exc))
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.strip().split(":")
        if len(parts) != 4:
            sys.exit("slicer: bad layout line: %s" % line)
        name, offset, width, kind = parts
        if kind not in ("str", "int", "float"):
            sys.exit("slicer: unknown type %r for field %s" % (kind, name))
        fields.append({"name": name, "offset": int(offset), "width": int(width), "type": kind})
    if not fields:
        sys.exit("slicer: layout %s declares no fields" % path)
    return fields


def parse_line(line, fields, trim, pad):
    record, raw = {}, {}
    for field in fields:
        chunk = line[field["offset"] : field["offset"] + field["width"]]
        if len(chunk) < field["width"]:
            return None, None
        text = chunk.strip(pad) if trim else chunk
        raw[field["name"]] = text
        if field["type"] == "int":
            try:
                record[field["name"]] = int(text)
            except ValueError:
                return None, None
        elif field["type"] == "float":
            try:
                record[field["name"]] = float(text)
            except ValueError:
                return None, None
        else:
            record[field["name"]] = text
    return record, raw


def parse_slice(raw):
    if raw is None:
        return 0, None
    if ":" not in raw:
        sys.exit("slicer: --slice must be written SKIP:COUNT (see --help), got %r" % raw)
    skip, _, count = raw.partition(":")
    try:
        return (int(skip) if skip else 0), (int(count) if count else None)
    except ValueError:
        sys.exit("slicer: --slice parts must be integers, got %r" % raw)


def compile_predicate(expression, types):
    for operator in OPERATORS:
        token = " %s " % operator
        if token in expression:
            name, _, literal = expression.partition(token)
            name, literal = name.strip(), literal.strip()
            if name not in types:
                sys.exit("slicer: --where references unknown field: %s" % name)
            if operator == "~":
                pattern = re.compile(literal)
                return lambda record, raw, name=name, pattern=pattern: bool(pattern.search(raw[name]))
            if types[name] in ("int", "float"):
                wanted = float(literal)
                getter = lambda record, raw, name=name: float(record[name])
            else:
                wanted = literal
                getter = lambda record, raw, name=name: record[name]
            checks = {
                "==": lambda value: value == wanted,
                "!=": lambda value: value != wanted,
                ">=": lambda value: value >= wanted,
                "<=": lambda value: value <= wanted,
                ">": lambda value: value > wanted,
                "<": lambda value: value < wanted,
            }[operator]
            return lambda record, raw: checks(getter(record, raw))
    sys.exit("slicer: cannot parse --where %r (expected 'field OP value')" % expression)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="slicer",
        description="Extract fields from fixed-width records.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("verb", choices=["cut"], help="the only verb; kept explicit for symmetry")
    parser.add_argument("--src", required=True, metavar="FILE", help="fixed-width data file")
    parser.add_argument("--map", required=True, metavar="FILE", help="field map description")
    parser.add_argument("--take", metavar="LIST", help="comma separated fields to emit (default: map order)")
    parser.add_argument("--keep", action="append", metavar="EXPR", help='filter, e.g. "amount >= 100" (repeatable)')
    parser.add_argument("--pad", default=" ", metavar="C", help="padding character to strip (default: space)")
    parser.add_argument("--raw", dest="trim", action="store_false", help="keep padding as-is")
    parser.add_argument("--halt-on-bad", action="store_true", help="abort on the first invalid line")
    parser.add_argument("--tally-bad", action="store_true", help="report the invalid-line count on stderr")
    parser.add_argument("--order", metavar="FIELD:DIR", help="sort key, e.g. qty:desc (default: file order)")
    parser.add_argument("--slice", metavar="SKIP:COUNT", help="drop SKIP rows then keep COUNT")
    parser.add_argument("--bare", dest="header", action="store_false", help="omit the csv/tsv header line")
    parser.add_argument("--shape", choices=["csv", "tsv", "json"], default="csv", help="output shape")
    args = parser.parse_args(argv)

    fields = load_layout(args.map)
    types = {field["name"]: field["type"] for field in fields}
    selected = args.take.split(",") if args.take else [field["name"] for field in fields]
    unknown = [name for name in selected if name not in types]
    if unknown:
        sys.exit("slicer: unknown field(s) in --take: %s" % ",".join(unknown))
    predicates = [compile_predicate(expression, types) for expression in args.keep or []]

    try:
        lines = Path(args.src).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        sys.exit("slicer: cannot read %s: %s" % (args.src, exc))

    rows, invalid = [], 0
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        record, raw = parse_line(line, fields, args.trim, args.pad)
        if record is None:
            invalid += 1
            if args.halt_on_bad:
                sys.exit("slicer: invalid record on line %d" % number)
            continue
        if all(predicate(record, raw) for predicate in predicates):
            rows.append(record)

    if args.order:
        field, _, direction = args.order.partition(":")
        if field not in types:
            sys.exit("slicer: unknown --order field: %s" % field)
        if direction not in ("", "asc", "desc"):
            sys.exit("slicer: --order direction must be asc or desc, got %r" % direction)
        rows.sort(key=lambda record: record[field], reverse=direction == "desc")
    skip, count = parse_slice(args.slice)
    rows = rows[skip:]
    if count is not None:
        rows = rows[:count]

    if args.tally_bad:
        sys.stderr.write("slicer: %d invalid line(s)\n" % invalid)

    if args.shape == "json":
        json.dump([{name: row[name] for name in selected} for row in rows], sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    writer = csv.writer(sys.stdout, delimiter="\t" if args.shape == "tsv" else ",", lineterminator="\n")
    if args.header:
        writer.writerow(selected)
    for row in rows:
        writer.writerow([row[name] for name in selected])
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SERVICE_LQ = """\
#LOGQ1
# generated fixture, do not edit by hand
1720008256|INFO|api|node-1|30|flush ok
1720008458|INFO|cache|node-1|8|request ok
1720008587|DEBUG|db|node-3|7|heartbeat
1720008777|DEBUG|db|node-3|11|pool stats | attempt=3
1720009024|WARN|cache|node-9|387|queue depth high
1720009183|INFO|api|node-1|8|request ok | attempt=2
1720009245|ERROR|cache|node-3|705|connection reset
1720009353|WARN|db|node-9|525|retry scheduled
1720009561|WARN|db|node-7|205|queue depth high
1720009727|INFO|worker|node-3|19|job done
1720009779|DEBUG|db|node-7|9|pool stats
1720009895|DEBUG|api|node-3|37|pool stats
1720010070|INFO|worker|node-9|34|request ok
1720010241|WARN|worker|node-7|878|retry scheduled
1720010281|WARN|worker|node-9|636|retry scheduled | attempt=2
1720010405|ERROR|worker|node-7|885|connection reset | attempt=2
1720010540|INFO|worker|node-3|25|request ok
1720010789|ERROR|cache|node-9|535|upstream timeout
1720010909|INFO|db|node-1|8|job done
1720010983|INFO|api|node-3|25|request ok
1720011158|INFO|cache|node-7|26|flush ok
1720011407|WARN|db|node-1|900|queue depth high | attempt=4
1720011570|ERROR|worker|node-1|62|write failed | attempt=2
1720011795|ERROR|cache|node-9|309|write failed | attempt=2
1720011879|INFO|api|node-3|32|request ok
1720012022|ERROR|worker|node-3|722|write failed
1720012181|INFO|worker|node-1|29|job done
1720012361|WARN|worker|node-9|730|retry scheduled
1720012591|WARN|worker|node-3|649|retry scheduled | attempt=3
1720012703|DEBUG|worker|node-3|31|heartbeat
1720012913|INFO|worker|node-9|5|job done
1720013094|DEBUG|api|node-7|5|heartbeat
1720013261|INFO|api|node-1|4|flush ok | attempt=4
1720013353|WARN|cache|node-3|405|queue depth high
1720013400|WARN|worker|node-7|362|slow query
1720013441|INFO|cache|node-3|31|request ok
1720013618|DEBUG|api|node-9|4|heartbeat
1720013844|INFO|worker|node-1|23|job done
1720014086|ERROR|cache|node-9|209|connection reset | attempt=3
1720014330|WARN|api|node-3|707|slow query
1720014516|WARN|api|node-9|731|slow query
1720014582|ERROR|api|node-1|321|connection reset
1720014643|DEBUG|api|node-1|23|cache probe | attempt=3
1720014791|INFO|db|node-7|16|job done
1720014984|INFO|worker|node-9|11|job done
1720015209|ERROR|worker|node-3|337|connection reset
1720015289|INFO|api|node-7|33|job done
1720015426|ERROR|worker|node-9|497|upstream timeout
"""

_SALES_CSV = """\
region,quarter,channel,amount
north,Q1,online,1200.50
north,Q1,retail,800.00
north,Q2,online,1500.25
north,Q2,retail,640.75
south,Q1,online,900.00
south,Q1,retail,1100.00
south,Q2,online,1750.00
south,Q2,retail,250.00
south,Q3,online,300.00
east,Q1,online,2000.00
east,Q2,retail,1250.50
east,Q3,online,410.25
east,Q3,retail,89.75
west,Q1,retail,150.00
west,Q2,online,2300.00
west,Q3,retail,700.00
north,Q3,online,560.00
south,Q3,retail,140.00
"""

_CONF_BASE = """\
{
  "service": {"name": "billing", "port": 8080, "workers": 2, "debug": true},
  "limits": {"rps": 100, "burst": 20, "timeout_ms": 3000},
  "features": ["invoices", "refunds"],
  "logging": {"level": "info", "sink": null},
  "variants": {
    "prod": {"service": {"debug": false, "workers": 8}, "limits": {"rps": 1000}, "features": ["audit"]},
    "canary": {"service": {"workers": 3}, "limits": {"burst": 5}}
  }
}
"""

_CONF_OVERLAY = """\
{
  "service": {"port": 9090},
  "limits": {"timeout_ms": 1500},
  "features": ["refunds", "reports"],
  "logging": {"sink": "stdout"},
  "tracing": {"endpoint": null, "sample_rate": 0.1, "propagate": true}
}
"""

_DEPS_JSON = """\
{
  "api":      {"deps": [{"name": "core"}, {"name": "httpx"}, {"name": "pytest", "scope": "test"}, {"name": "tracing", "optional": true}]},
  "core":     {"deps": [{"name": "utils"}, {"name": "serde"}]},
  "httpx":    {"deps": [{"name": "serde"}, {"name": "tls"}]},
  "serde":    {"deps": [{"name": "utils"}]},
  "utils":    {"deps": []},
  "tls":      {"deps": [{"name": "utils"}]},
  "tracing":  {"deps": [{"name": "serde"}]},
  "pytest":   {"deps": [{"name": "pluggy"}]},
  "pluggy":   {"deps": []},
  "worker":   {"deps": [{"name": "core"}, {"name": "queue"}]},
  "queue":    {"deps": [{"name": "serde"}, {"name": "worker"}]}
}
"""

_STOCK_LAYOUT = """\
# name:offset:width:type
id:0:6:int
sku:6:10:str
warehouse:16:4:str
qty:20:6:int
price:26:9:float
status:35:12:str
"""

#: id, sku, warehouse, qty, price, status
_STOCK_ROWS = (
    (1001, "BOLT-M8", "WH1", 240, 1.25, "active"),
    (1002, "BOLT-M10", "WH1", 18, 1.95, "active"),
    (1003, "NUT-M8", "WH2", 900, 0.35, "active"),
    (1004, "WASHER-8", "WH2", 0, 0.10, "discontinued"),
    (1005, "SCREW-4", "WH3", 120, 0.75, "active"),
    (1006, "BOLT-M12", "WH1", 64, 3.40, "hold"),
    (1007, "NUT-M10", "WH3", 310, 0.55, "active"),
    (1008, "PLATE-A", "WH2", 7, 12.90, "active"),
    (1009, "PLATE-B", "WH2", 45, 15.25, "hold"),
    (1010, "SCREW-6", "WH1", 520, 0.85, "active"),
    (1011, "BOLT-M6", "WH3", 95, 0.95, "discontinued"),
    (1012, "NUT-M12", "WH1", 150, 0.80, "active"),
)


def _write_stock_records(ws: Path) -> None:
    """Fixed-width records plus two deliberately invalid lines.

    Written from a table rather than a literal so the column padding cannot rot
    in review: a stray trailing space would silently shift every later field.
    """
    lines = [
        f"{rid:<6d}{sku:<10}{wh:<4}{qty:<6d}{price:<9.2f}{status:<12}"
        for rid, sku, wh, qty, price, status in _STOCK_ROWS
    ]
    # A short line and a non-numeric qty: both must survive as skippable junk.
    lines.insert(4, "1099  TRUNCATED")
    lines.insert(9, f"{1098:<6d}{'BAD-QTY':<10}{'WH2':<4}{'n/a':<6}{'1.00':<9}{'active':<12}")
    (ws / "stock").mkdir(parents=True, exist_ok=True)
    (ws / "stock" / "records.fw").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


#: proto, src, dst, sport, dport, length, flags, corrupt
_CAPTURE_PACKETS = (
    (6, "10.2.0.11", "10.0.0.1", 51001, 443, 512, 0x06, False),
    (6, "10.2.0.11", "10.0.0.1", 51001, 443, 240, 0x04, False),
    (17, "10.2.0.12", "10.0.0.53", 40000, 53, 96, 0x00, False),
    (6, "10.1.0.5", "10.0.0.1", 52002, 443, 800, 0x06, False),
    (6, "10.2.0.13", "10.0.0.1", 51003, 80, 1400, 0x06, False),
    (1, "10.2.0.14", "10.0.0.9", 0, 0, 64, 0x00, False),
    (6, "10.2.0.15", "10.0.0.1", 51004, 443, 199, 0x06, False),
    (6, "10.2.0.16", "10.0.0.1", 51005, 443, 640, 0x02, False),
    (6, "10.2.0.17", "10.0.0.1", 51006, 443, 980, 0x06, True),
    (6, "10.2.0.18", "10.0.0.1", 51007, 22, 320, 0x06, False),
    (17, "10.2.0.19", "10.0.0.53", 40001, 53, 128, 0x00, False),
    (6, "192.168.1.4", "10.0.0.1", 53001, 443, 1200, 0x06, False),
    (6, "10.2.0.20", "10.0.0.1", 51008, 443, 256, 0x06, False),
    (6, "10.2.0.21", "10.0.0.1", 51009, 443, 256, 0x0C, False),
    (6, "10.2.0.22", "10.0.0.1", 51010, 80, 704, 0x06, False),
    (1, "10.1.0.6", "10.0.0.9", 0, 0, 48, 0x00, False),
    (6, "10.2.0.23", "10.0.0.1", 51011, 443, 448, 0x06, False),
    (17, "10.2.0.24", "10.0.0.53", 40002, 53, 512, 0x00, True),
    (6, "10.2.0.25", "10.0.0.1", 51012, 443, 1024, 0x06, False),
    (6, "10.2.0.26", "10.0.0.1", 51013, 443, 200, 0x06, False),
    (6, "10.1.0.7", "10.0.0.1", 52003, 80, 384, 0x06, False),
    (6, "10.2.0.27", "10.0.0.1", 51014, 443, 896, 0x01, False),
    (17, "10.2.0.28", "10.0.0.53", 40003, 53, 640, 0x00, False),
    (6, "10.2.0.29", "10.0.0.1", 51015, 443, 576, 0x06, False),
    (6, "10.2.0.30", "10.0.0.1", 51016, 443, 768, 0x06, False),
    (1, "10.2.0.31", "10.0.0.9", 0, 0, 1500, 0x00, False),
    (6, "10.2.0.32", "10.0.0.1", 51017, 8080, 832, 0x06, False),
    (6, "10.2.0.33", "10.0.0.1", 51018, 443, 96, 0x06, False),
)


def _write_capture(ws: Path) -> None:
    """Pack the binary .cap fixture, corrupting two checksums on purpose."""
    blob = bytearray(b"PKT1")
    for proto, src, dst, sport, dport, length, flags, corrupt in _CAPTURE_PACKETS:
        octets = [int(part) for part in src.split(".")]
        src_int = (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]
        octets = [int(part) for part in dst.split(".")]
        dst_int = (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]
        body = struct.pack(">BIIHHHB", proto, src_int, dst_int, sport, dport, length, flags)
        checksum = sum(body) % 256
        if corrupt:
            checksum = (checksum + 7) % 256
        blob += body + bytes([checksum])
    (ws / "net").mkdir(parents=True, exist_ok=True)
    (ws / "net" / "capture.cap").write_bytes(bytes(blob))


_HELP_HINT = "Точную семантику флагов смотри в выводе --help этой утилиты, гадать не нужно."

_SHELL_RULES = (
    "Реши задачу шелл-скриптом solve.sh в корне рабочей директории: он должен сам"
    " создавать результат, запускаться как `bash solve.sh` и завершаться с кодом 0."
    " Слова python, python3, perl, node, ruby, php и deno в скрипте недопустимы —"
    " работа должна происходить в самих CLI-утилитах."
)


# ---------------------------------------------------------------------------
# 372. logq: percentile over a half-open time window
# ---------------------------------------------------------------------------
_TASK_372_EXPECTED = [
    {"key": "worker", "value": 885},
    {"key": "api", "value": 707},
    {"key": "cache", "value": 705},
]

TASK_372 = Task(
    id="task_372_cli_logq_percentile_window",
    name="logq: p95 latency per component over a window",
    tags=("cli", "bespoke-tool", "logq", "medium"),
    prompt=(
        "В logs/service.lq лежит журнал в собственном формате, читать его умеет утилита"
        " ./tools/logq. Нужен отчёт report.json по проблемным записям: только уровни ERROR"
        " и WARN, хост node-1 исключить целиком, нижняя граница окна по epoch — 1720009245,"
        " верхняя — 1720014516 (передай их утилите как есть, границы она трактует сама)."
        " Сгруппируй по компоненту, посчитай для каждого 95-й процентиль latency_ms и"
        " оставь три компонента с наибольшим значением. Записывай в report.json ровно то,"
        " что утилита выдаёт в режиме JSON, без переформатирования. " + _HELP_HINT
    ),
    setup_files={"tools/logq": _TOOL_LOGQ, "logs/service.lq": _SERVICE_LQ},
    setup_callback=_setup_wave(),
    gold_files={"report.json": json.dumps(_TASK_372_EXPECTED, ensure_ascii=False) + "\n"},
    verifier=_json_equals("report.json", _TASK_372_EXPECTED),
)


# ---------------------------------------------------------------------------
# 373. logq: rounded mean, regex on the message field, ascending order
# ---------------------------------------------------------------------------
TASK_373 = Task(
    id="task_373_cli_logq_mean_by_component",
    name="logq: mean latency of failure messages",
    tags=("cli", "bespoke-tool", "logq", "medium"),
    prompt=(
        "Утилита ./tools/logq читает журнал logs/service.lq. Собери latency.csv: возьми"
        " только записи уровней ERROR и WARN, у которых в тексте сообщения встречается"
        " timeout, reset или failed, сгруппируй по компоненту и посчитай среднюю latency_ms"
        " с одним знаком после запятой. Отсортируй по возрастанию среднего. В latency.csv"
        " положи вывод утилиты в формате CSV как есть, вместе с её строкой заголовка. "
        + _HELP_HINT
    ),
    setup_files={"tools/logq": _TOOL_LOGQ, "logs/service.lq": _SERVICE_LQ},
    setup_callback=_setup_wave(),
    gold_files={"latency.csv": "key,value\napi,321.0\ncache,439.5\nworker,500.6\n"},
    verifier=_csv_equals(
        "latency.csv",
        [["key", "value"], ["api", "321.0"], ["cache", "439.5"], ["worker", "500.6"]],
    ),
)


# ---------------------------------------------------------------------------
# 374. pktool: filtered decode of a binary capture
# ---------------------------------------------------------------------------
TASK_374 = Task(
    id="task_374_cli_pktool_filter_decode",
    name="pktool: filter a binary capture and slice columns",
    tags=("cli", "bespoke-tool", "pktool", "binary", "hard"),
    prompt=(
        "net/capture.cap — бинарный дамп, разбирает его ./tools/pktool. Нужен flows.csv:"
        " только TCP-пакеты, у которых одновременно установлены флаги syn и ack, длина не"
        " меньше 200, адрес источника начинается на 10.2. и порт назначения 443."
        " Отсортируй по длине по убыванию, отбрось самый большой пакет и возьми следующие"
        " пять. Колонки — index, src, dport, length, формат CSV с заголовком, ровно как их"
        " печатает утилита. " + _HELP_HINT
    ),
    setup_files={"tools/pktool": _TOOL_PKTOOL},
    setup_callback=_setup_wave(_write_capture),
    gold_files={
        "flows.csv": (
            "index,src,dport,length\n"
            "24,10.2.0.30,443,768\n"
            "23,10.2.0.29,443,576\n"
            "0,10.2.0.11,443,512\n"
            "16,10.2.0.23,443,448\n"
            "12,10.2.0.20,443,256\n"
        )
    },
    verifier=_csv_equals(
        "flows.csv",
        [
            ["index", "src", "dport", "length"],
            ["24", "10.2.0.30", "443", "768"],
            ["23", "10.2.0.29", "443", "576"],
            ["0", "10.2.0.11", "443", "512"],
            ["16", "10.2.0.23", "443", "448"],
            ["12", "10.2.0.20", "443", "256"],
        ],
    ),
)


# ---------------------------------------------------------------------------
# 375. pktool: per-protocol totals including corrupt records
# ---------------------------------------------------------------------------
_TASK_375_EXPECTED = [
    {"proto": "icmp", "packets": 1, "bytes": 1500, "max_length": 1500},
    {"proto": "tcp", "packets": 20, "bytes": 12635, "max_length": 1400},
    {"proto": "udp", "packets": 3, "bytes": 1280, "max_length": 640},
]

TASK_375 = Task(
    id="task_375_cli_pktool_stats_corrupt",
    name="pktool: protocol totals that keep corrupt records",
    tags=("cli", "bespoke-tool", "pktool", "binary", "medium"),
    prompt=(
        "Нужна сводка по протоколам из бинарного дампа net/capture.cap, разбирает его"
        " ./tools/pktool. Учитывай пакеты длиной не меньше 100 байт. Записи с испорченной"
        " контрольной суммой утилита по умолчанию выбрасывает — в этой сводке они нужны и"
        " должны попасть в подсчёт наравне с остальными. Результат положи в"
        " proto_stats.json в JSON-формате утилиты, без изменений. " + _HELP_HINT
    ),
    setup_files={"tools/pktool": _TOOL_PKTOOL},
    setup_callback=_setup_wave(_write_capture),
    gold_files={"proto_stats.json": json.dumps(_TASK_375_EXPECTED, ensure_ascii=False) + "\n"},
    verifier=_json_equals("proto_stats.json", _TASK_375_EXPECTED),
)


# ---------------------------------------------------------------------------
# 376. xtab: column-normalised cross-tab with totals
# ---------------------------------------------------------------------------
TASK_376 = Task(
    id="task_376_cli_xtab_column_share",
    name="xtab: quarterly share of each region",
    tags=("cli", "bespoke-tool", "xtab", "medium"),
    prompt=(
        "data/sales.csv — плоская таблица продаж, сводит её ./tools/xtab. Построй share.csv:"
        " строки — регионы, колонки — кварталы, значения — суммы amount, пересчитанные в"
        " проценты внутри своей колонки с одним знаком после запятой. Добавь итоговую строку"
        " и итоговую колонку. Строки упорядочи по убыванию их итога. Файл должен содержать"
        " вывод утилиты в CSV как есть. " + _HELP_HINT
    ),
    setup_files={"tools/xtab": _TOOL_XTAB, "data/sales.csv": _SALES_CSV},
    setup_callback=_setup_wave(),
    gold_files={
        "share.csv": (
            ",Q1,Q2,Q3,TOTAL\n"
            "north,32.5,27.8,25.5,29.3\n"
            "south,32.5,26,20,27.7\n"
            "east,32.5,16.3,22.7,23.4\n"
            "west,2.4,29.9,31.8,19.6\n"
            "TOTAL,100,100,100,100\n"
        )
    },
    verifier=_csv_equals(
        "share.csv",
        [
            ["", "Q1", "Q2", "Q3", "TOTAL"],
            ["north", "32.5", "27.8", "25.5", "29.3"],
            ["south", "32.5", "26", "20", "27.7"],
            ["east", "32.5", "16.3", "22.7", "23.4"],
            ["west", "2.4", "29.9", "31.8", "19.6"],
            ["TOTAL", "100", "100", "100", "100"],
        ],
    ),
)


# ---------------------------------------------------------------------------
# 377. xtab: means, with columns ordered by their aggregated totals
# ---------------------------------------------------------------------------
_TASK_377_EXPECTED = {
    "columns": ["west", "east", "north", "south"],
    "rows": [
        {
            "key": "online",
            "cells": {"west": 2300.0, "east": 1205.13, "north": 1086.92, "south": 983.33},
        },
        {
            "key": "retail",
            "cells": {"west": 425.0, "east": 670.13, "north": 720.38, "south": 496.67},
        },
    ],
}

TASK_377 = Task(
    id="task_377_cli_xtab_mean_pivot",
    name="xtab: mean cheque by channel and region",
    tags=("cli", "bespoke-tool", "xtab", "medium"),
    prompt=(
        "По data/sales.csv с помощью ./tools/xtab собери mean.json: строки — каналы продаж,"
        " колонки — регионы, в ячейках средний amount с двумя знаками после запятой."
        " Колонки расположи по убыванию их итога. Итоговые строку и колонку не добавляй."
        " В mean.json положи JSON-вывод утилиты без изменений. " + _HELP_HINT
    ),
    setup_files={"tools/xtab": _TOOL_XTAB, "data/sales.csv": _SALES_CSV},
    setup_callback=_setup_wave(),
    gold_files={"mean.json": json.dumps(_TASK_377_EXPECTED, ensure_ascii=False) + "\n"},
    verifier=_json_equals("mean.json", _TASK_377_EXPECTED),
)


# ---------------------------------------------------------------------------
# 378. cfgctl: layered merge with a profile overlay
# ---------------------------------------------------------------------------
_TASK_378_ENV = """\
FEATURES=invoices,refunds,reports,audit
LIMITS_BURST=50
LIMITS_RPS=1000
LIMITS_TIMEOUT_MS=1500
LOGGING_LEVEL=info
LOGGING_SINK=stdout
SERVICE_DEBUG=false
SERVICE_PORT=9090
SERVICE_WORKERS=8
TRACING_PROPAGATE=true
TRACING_SAMPLE_RATE=0.1
"""

TASK_378 = Task(
    id="task_378_cli_cfgctl_layered_merge",
    name="cfgctl: layer configs into an env file",
    tags=("cli", "bespoke-tool", "cfgctl", "hard"),
    prompt=(
        "Утилита ./tools/cfgctl собирает конфигурацию из слоёв. Нужен effective.env:"
        " возьми conf/base.json, поверх него conf/overlay.json, а сверху — набор настроек"
        " prod, который лежит внутри base. Списки не должны затирать друг друга — их нужно"
        " объединить, сохранив первое"
        " вхождение каждого элемента. Дополнительно выставь limits.burst в 50, убери ключ"
        " service.name и выброси все ключи со значением null. Результат — вывод утилиты в"
        " формате env, без правок руками. " + _HELP_HINT
    ),
    setup_files={
        "tools/cfgctl": _TOOL_CFGCTL,
        "conf/base.json": _CONF_BASE,
        "conf/overlay.json": _CONF_OVERLAY,
    },
    setup_callback=_setup_wave(),
    gold_files={"effective.env": _TASK_378_ENV},
    verifier=_text_equals("effective.env", _TASK_378_ENV),
)


# ---------------------------------------------------------------------------
# 379. cfgctl: diff restricted to changed keys
# ---------------------------------------------------------------------------
_TASK_379_EXPECTED = {
    "changed": {
        "limits.rps": {"from": 1000, "to": 250},
        "logging.level": {"from": "info", "to": "debug"},
        "service.port": {"from": 8080, "to": 9090},
    }
}

TASK_379 = Task(
    id="task_379_cli_cfgctl_diff_changed",
    name="cfgctl: report only changed config keys",
    tags=("cli", "bespoke-tool", "cfgctl", "easy"),
    prompt=(
        "conf/expected.json — эталон конфигурации сервиса, conf/actual.json — то, что"
        " реально уехало на стенд. С помощью ./tools/cfgctl собери drift.json: только те"
        " ключи, которые есть в обоих файлах, но с разными значениями. Появившиеся и"
        " пропавшие ключи в отчёт попасть не должны. Положи вывод утилиты как есть. "
        + _HELP_HINT
    ),
    setup_files={
        "tools/cfgctl": _TOOL_CFGCTL,
        "conf/expected.json": (
            '{\n'
            '  "service": {"name": "billing", "port": 8080, "workers": 8, "replicas": 3},\n'
            '  "limits": {"rps": 1000, "burst": 20, "timeout_ms": 1500},\n'
            '  "logging": {"level": "info", "format": "json"}\n'
            '}\n'
        ),
        "conf/actual.json": (
            '{\n'
            '  "service": {"name": "billing", "port": 9090, "workers": 8},\n'
            '  "limits": {"rps": 250, "burst": 20, "timeout_ms": 1500, "concurrency": 16},\n'
            '  "logging": {"level": "debug", "format": "json"}\n'
            '}\n'
        ),
    },
    setup_callback=_setup_wave(),
    gold_files={"drift.json": json.dumps(_TASK_379_EXPECTED, indent=2, sort_keys=True) + "\n"},
    verifier=_json_equals("drift.json", _TASK_379_EXPECTED),
)


# ---------------------------------------------------------------------------
# 380. depwalk: topological build order including test edges
# ---------------------------------------------------------------------------
_TASK_380_ORDER = "pluggy\npytest\nutils\nserde\ncore\ntls\nhttpx\napi\n"

TASK_380 = Task(
    id="task_380_cli_depwalk_build_order",
    name="depwalk: topological build order for api",
    tags=("cli", "bespoke-tool", "depwalk", "medium"),
    prompt=(
        "conf/deps.json описывает граф зависимостей, ходит по нему ./tools/depwalk."
        " Построй build_order.txt — порядок сборки всего, что нужно пакету api, включая"
        " тестовые зависимости, но без необязательных. Зависимость обязана стоять раньше"
        " того, кто её использует. Сам api тоже входит в список. Файл — вывод утилиты"
        " построчно, как есть. " + _HELP_HINT
    ),
    setup_files={"tools/depwalk": _TOOL_DEPWALK, "conf/deps.json": _DEPS_JSON},
    setup_callback=_setup_wave(),
    gold_files={"build_order.txt": _TASK_380_ORDER},
    verifier=_text_equals("build_order.txt", _TASK_380_ORDER),
)


# ---------------------------------------------------------------------------
# 381. slicer: filtered extraction from fixed-width records
# ---------------------------------------------------------------------------
TASK_381 = Task(
    id="task_381_cli_slicer_restock",
    name="slicer: restock list from fixed-width stock records",
    tags=("cli", "bespoke-tool", "slicer", "medium"),
    prompt=(
        "stock/records.fw — записи фиксированной ширины, stock/layout.txt описывает поля,"
        " разбирает их ./tools/slicer. Собери restock.csv по позициям со статусом active и"
        " остатком меньше 200 штук: колонки id, sku, warehouse, qty, отсортированные по"
        " возрастанию остатка. Битые строки в файле есть, они должны просто выпасть из"
        " выборки, а не ронять разбор. Положи вывод утилиты в CSV с заголовком как есть. "
        + _HELP_HINT
    ),
    setup_files={"tools/slicer": _TOOL_SLICER, "stock/layout.txt": _STOCK_LAYOUT},
    setup_callback=_setup_wave(_write_stock_records),
    gold_files={
        "restock.csv": (
            "id,sku,warehouse,qty\n"
            "1008,PLATE-A,WH2,7\n"
            "1002,BOLT-M10,WH1,18\n"
            "1005,SCREW-4,WH3,120\n"
            "1012,NUT-M12,WH1,150\n"
        )
    },
    verifier=_csv_equals(
        "restock.csv",
        [
            ["id", "sku", "warehouse", "qty"],
            ["1008", "PLATE-A", "WH2", "7"],
            ["1002", "BOLT-M10", "WH1", "18"],
            ["1005", "SCREW-4", "WH3", "120"],
            ["1012", "NUT-M12", "WH1", "150"],
        ],
    ),
)


# ---------------------------------------------------------------------------
# 382. mixed: depwalk --format dot piped through grep/cut/sort/uniq
# ---------------------------------------------------------------------------
_TASK_382_GOLD = """\
#!/usr/bin/env bash
set -euo pipefail

./tools/depwalk reach --map conf/deps.json --edges runtime,dev,test --with-soft --shape dot \\
  | grep -oE '\\-> "[^"]+"' \\
  | cut -d'"' -f2 \\
  | sort \\
  | uniq -c \\
  | sort -k1,1nr -k2,2 \\
  | head -3 \\
  | awk '{print $2","$1}' > hotspots.csv
"""

TASK_382 = Task(
    id="task_382_cli_mixed_depwalk_hotspots",
    name="depwalk + POSIX: most depended-on packages",
    tags=("cli", "mixed", "depwalk", "pipeline", "hard"),
    prompt=(
        "conf/deps.json — граф зависимостей, ./tools/depwalk умеет выгружать его рёбра в"
        " формате dot. Найди три пакета, на которые ссылается наибольшее число рёбер, учитывая"
        " все области зависимостей и необязательные тоже. Результат — hotspots.csv, по строке"
        " на пакет в виде имя,количество, по убыванию количества; при равенстве — по имени"
        " по возрастанию. Считать рёбра нужно уже поверх вывода depwalk обычными утилитами"
        " командной строки. " + _SHELL_RULES
    ),
    setup_files={"tools/depwalk": _TOOL_DEPWALK, "conf/deps.json": _DEPS_JSON},
    setup_callback=_setup_wave(),
    gold_files={"solve.sh": _TASK_382_GOLD},
    verifier=_script_produces(
        "solve.sh",
        {"hotspots.csv": "serde,4\nutils,3\ncore,2\n"},
        must_use=("conf/deps.json", "tools/depwalk"),
        allowed_note="Вся обработка должна быть на CLI-утилитах.",
    ),
)


# ---------------------------------------------------------------------------
# 383. mixed: pktool decode piped into awk aggregation
# ---------------------------------------------------------------------------
_TASK_383_GOLD = """\
#!/usr/bin/env bash
set -euo pipefail

./tools/pktool dump --cap net/capture.cap --crc keep --shape tsv \\
  | tail -n +2 \\
  | awk -F'\\t' '{pkts[$6]++; bytes[$6]+=$7} END {for (p in pkts) if (pkts[p] >= 2) printf "%s\\t%d\\t%d\\n", p, pkts[p], bytes[p]}' \\
  | sort -k3,3nr -k1,1n > ports.tsv
"""

TASK_383 = Task(
    id="task_383_cli_mixed_pktool_ports",
    name="pktool + awk: busiest destination ports",
    tags=("cli", "mixed", "pktool", "pipeline", "hard"),
    prompt=(
        "Разбери бинарный дамп net/capture.cap утилитой ./tools/pktool и посчитай нагрузку"
        " по портам назначения. Записи с испорченной контрольной суммой в этом подсчёте"
        " нужно оставить. Для каждого порта посчитай число пакетов и суммарную длину, оставь"
        " только порты минимум с двумя пакетами. Результат — ports.tsv, колонки через"
        " табуляцию: порт, число пакетов, сумма длин; сортировка по сумме длин по убыванию,"
        " при равенстве — по номеру порта по возрастанию, без строки заголовка."
        " Агрегировать нужно поверх вывода pktool. " + _SHELL_RULES
    ),
    setup_files={"tools/pktool": _TOOL_PKTOOL},
    setup_callback=_setup_wave(_write_capture),
    gold_files={"solve.sh": _TASK_383_GOLD},
    verifier=_script_produces(
        "solve.sh",
        {"ports.tsv": "443\t16\t9091\n80\t3\t2488\n0\t3\t1612\n53\t4\t1376\n"},
        must_use=("tools/pktool",),
        allowed_note="Агрегация должна быть сделана утилитами командной строки.",
    ),
)


# ---------------------------------------------------------------------------
# 384. mixed: slicer output aggregated with awk
# ---------------------------------------------------------------------------
_TASK_384_GOLD = """\
#!/usr/bin/env bash
set -euo pipefail

./tools/slicer cut --src stock/records.fw --map stock/layout.txt \\
    --keep "status != discontinued" --take warehouse,qty,price --shape tsv \\
  | tail -n +2 \\
  | awk -F'\\t' '{value[$1] += $2 * $3; items[$1]++} END {for (w in value) printf "%s,%d,%.2f\\n", w, items[w], value[w]}' \\
  | sort -t, -k3,3nr > warehouse_value.csv
"""

TASK_384 = Task(
    id="task_384_cli_mixed_slicer_warehouse_value",
    name="slicer + awk: stock value per warehouse",
    tags=("cli", "mixed", "slicer", "pipeline", "hard"),
    prompt=(
        "stock/records.fw — записи фиксированной ширины с раскладкой в stock/layout.txt,"
        " разбирает их ./tools/slicer. Посчитай стоимость запаса по складам: бери все"
        " корректные позиции, кроме снятых с производства, и для каждого склада сложи"
        " произведение остатка на цену. Результат — warehouse_value.csv, по строке на склад"
        " в виде склад,число_позиций,стоимость с двумя знаками после запятой, отсортированный"
        " по стоимости по убыванию, без заголовка. Умножение и суммирование делай поверх"
        " вывода slicer. " + _SHELL_RULES
    ),
    setup_files={"tools/slicer": _TOOL_SLICER, "stock/layout.txt": _STOCK_LAYOUT},
    setup_callback=_setup_wave(_write_stock_records),
    gold_files={"solve.sh": _TASK_384_GOLD},
    verifier=_script_produces(
        "solve.sh",
        {"warehouse_value.csv": "WH1,5,1114.70\nWH2,3,1091.55\nWH3,2,260.50\n"},
        must_use=("stock/layout.txt", "tools/slicer"),
        allowed_note="Считать нужно утилитами командной строки.",
    ),
)


# ---------------------------------------------------------------------------
# 385. sort: multi-key ordering with an explicit tie-break
# ---------------------------------------------------------------------------
_SERVERS_CSV = """\
host,region,cpu_pct,mem_mb,uptime_days
web-07,eu-west,91,15800,42
db-02,us-east,97,64200,310
cache-11,eu-west,64,8100,7
web-03,us-east,91,15600,120
worker-19,ap-south,88,32000,64
db-05,eu-west,97,64000,88
web-12,ap-south,55,15400,15
worker-02,us-east,73,32100,201
cache-04,us-east,64,8000,33
web-21,eu-west,88,15900,9
db-09,ap-south,45,63800,410
worker-07,eu-west,91,31900,150
"""

_TASK_385_GOLD = """\
#!/usr/bin/env bash
set -euo pipefail

tail -n +2 servers.csv \\
  | sort -t, -k3,3nr -k1,1 \\
  | head -6 \\
  | cut -d, -f1,2,3 > hotlist.csv
"""

TASK_385 = Task(
    id="task_385_cli_sort_multikey",
    name="sort: top loaded hosts with a deterministic tie-break",
    tags=("cli", "posix", "sort", "pipeline", "medium"),
    prompt=(
        "В servers.csv есть строка заголовка и колонки host,region,cpu_pct,mem_mb,uptime_days."
        " Собери hotlist.csv — шесть самых загруженных хостов по cpu_pct по убыванию; при"
        " равной загрузке выше идёт хост с меньшим именем в лексикографическом порядке."
        " В файле должны остаться только колонки host, region, cpu_pct, без строки"
        " заголовка. Разрешены sort, cut, head, tail, tr, grep; awk и sed в этой задаче"
        " использовать нельзя, порядок должен обеспечивать сам sort. " + _SHELL_RULES
    ),
    setup_files={"servers.csv": _SERVERS_CSV},
    setup_callback=_setup_wave(),
    gold_files={"solve.sh": _TASK_385_GOLD},
    verifier=_script_produces(
        "solve.sh",
        {
            "hotlist.csv": (
                "db-02,us-east,97\n"
                "db-05,eu-west,97\n"
                "web-03,us-east,91\n"
                "web-07,eu-west,91\n"
                "worker-07,eu-west,91\n"
                "web-21,eu-west,88\n"
            )
        },
        must_use=("servers.csv",),
        banned_tools=("awk", "sed"),
        allowed_note="Разрешены только sort, cut, head, tail, tr и grep.",
    ),
)


# ---------------------------------------------------------------------------
# 386. join: left outer join with a placeholder for unmatched rows
# ---------------------------------------------------------------------------
_ORDERS_CSV = """\
order_id,customer_id,amount
A-1004,C-31,250.00
A-1001,C-12,1200.50
A-1007,C-77,89.90
A-1002,C-12,340.00
A-1009,C-45,15.25
A-1003,C-08,720.00
A-1006,C-99,410.10
A-1005,C-45,60.00
A-1008,C-31,1500.00
"""

_CUSTOMERS_CSV = """\
customer_id,name,tier
C-12,Northwind,gold
C-31,Contoso,silver
C-45,Fabrikam,bronze
C-08,Adventure,gold
C-52,Litware,silver
"""

_TASK_386_GOLD = """\
#!/usr/bin/env bash
set -euo pipefail

tail -n +2 orders.csv | sort -t, -k2,2 > orders.sorted
tail -n +2 customers.csv | sort -t, -k1,1 > customers.sorted

join -t, -1 2 -2 1 -a 1 -e UNKNOWN -o '1.1,2.2,2.3,1.3' orders.sorted customers.sorted \\
  | sort -t, -k1,1 > enriched.csv

rm -f orders.sorted customers.sorted
"""

_TASK_386_EXPECTED = (
    "A-1001,Northwind,gold,1200.50\n"
    "A-1002,Northwind,gold,340.00\n"
    "A-1003,Adventure,gold,720.00\n"
    "A-1004,Contoso,silver,250.00\n"
    "A-1005,Fabrikam,bronze,60.00\n"
    "A-1006,UNKNOWN,UNKNOWN,410.10\n"
    "A-1007,UNKNOWN,UNKNOWN,89.90\n"
    "A-1008,Contoso,silver,1500.00\n"
    "A-1009,Fabrikam,bronze,15.25\n"
)

TASK_386 = Task(
    id="task_386_cli_join_left_outer",
    name="join: left outer join two CSVs on a non-first column",
    tags=("cli", "posix", "join", "pipeline", "hard"),
    prompt=(
        "orders.csv содержит order_id,customer_id,amount, а customers.csv —"
        " customer_id,name,tier; в обоих есть строка заголовка. Собери enriched.csv, где к"
        " каждому заказу подставлены имя и тариф клиента: колонки order_id,name,tier,amount,"
        " сортировка по order_id по возрастанию, без заголовка. Заказы, для которых клиент"
        " не найден, обязаны остаться в выводе, а имя и тариф у них должны быть UNKNOWN."
        " Соединение должна делать утилита join; awk и sed в этой задаче запрещены. "
        + _SHELL_RULES
    ),
    setup_files={"orders.csv": _ORDERS_CSV, "customers.csv": _CUSTOMERS_CSV},
    setup_callback=_setup_wave(),
    gold_files={"solve.sh": _TASK_386_GOLD},
    verifier=_script_produces(
        "solve.sh",
        {"enriched.csv": _TASK_386_EXPECTED},
        must_use=("customers.csv", "orders.csv"),
        banned_tools=("awk", "sed"),
        allowed_note="Соединять нужно утилитой join.",
    ),
)


# ---------------------------------------------------------------------------
# 387. comm: intersection minus a third set
# ---------------------------------------------------------------------------
_TASK_387_GOLD = """\
#!/usr/bin/env bash
set -euo pipefail

sort -u enrolled.txt > enrolled.sorted
sort -u active.txt > active.sorted
sort -u optedout.txt > optedout.sorted

comm -12 enrolled.sorted active.sorted > both.tmp
comm -23 both.tmp optedout.sorted > campaign.txt

rm -f enrolled.sorted active.sorted optedout.sorted both.tmp
"""

TASK_387 = Task(
    id="task_387_cli_comm_set_algebra",
    name="comm: set algebra over three id lists",
    tags=("cli", "posix", "comm", "pipeline", "medium"),
    prompt=(
        "enrolled.txt, active.txt и optedout.txt — списки идентификаторов пользователей,"
        " по одному в строке, неотсортированные, в enrolled.txt есть повторы. Собери"
        " campaign.txt: идентификаторы, которые есть и в enrolled.txt, и в active.txt, но"
        " отсутствуют в optedout.txt. Результат — по одному идентификатору в строке, без"
        " повторов, отсортированный по возрастанию. Множества нужно считать утилитой comm;"
        " awk и sed запрещены. " + _SHELL_RULES
    ),
    setup_files={
        "enrolled.txt": "u-1042\nu-1007\nu-1099\nu-1003\nu-1055\nu-1021\nu-1007\nu-1088\n",
        "active.txt": "u-1021\nu-1003\nu-1042\nu-1077\nu-1099\nu-1012\n",
        "optedout.txt": "u-1099\nu-1055\nu-1012\nu-1003\n",
    },
    setup_callback=_setup_wave(),
    gold_files={"solve.sh": _TASK_387_GOLD},
    verifier=_script_produces(
        "solve.sh",
        {"campaign.txt": "u-1021\nu-1042\n"},
        must_use=("active.txt", "enrolled.txt", "optedout.txt"),
        banned_tools=("awk", "sed"),
        allowed_note="Пересечение и разность нужно считать утилитой comm.",
    ),
)


# ---------------------------------------------------------------------------
# 388. grep -oE | sort | uniq -c: frequency ranking
# ---------------------------------------------------------------------------
_ACCESS_LOG = """\
10.0.0.4 - - [12/Jul/2026:10:00:01 +0000] "GET /api/orders HTTP/1.1" 200 512
10.0.0.5 - - [12/Jul/2026:10:00:02 +0000] "GET /api/users HTTP/1.1" 404 128
10.0.0.4 - - [12/Jul/2026:10:00:03 +0000] "POST /api/orders HTTP/1.1" 500 64
10.0.0.9 - - [12/Jul/2026:10:00:04 +0000] "GET /health HTTP/1.1" 200 12
10.0.0.5 - - [12/Jul/2026:10:00:05 +0000] "GET /api/users HTTP/1.1" 404 128
10.0.0.7 - - [12/Jul/2026:10:00:06 +0000] "GET /api/reports HTTP/1.1" 503 96
10.0.0.4 - - [12/Jul/2026:10:00:07 +0000] "GET /api/orders HTTP/1.1" 404 128
10.0.0.5 - - [12/Jul/2026:10:00:08 +0000] "GET /static/logo.png HTTP/1.1" 200 8192
10.0.0.9 - - [12/Jul/2026:10:00:09 +0000] "POST /api/orders HTTP/1.1" 500 64
10.0.0.7 - - [12/Jul/2026:10:00:10 +0000] "GET /api/users HTTP/1.1" 404 128
10.0.0.4 - - [12/Jul/2026:10:00:11 +0000] "GET /api/reports HTTP/1.1" 503 96
10.0.0.5 - - [12/Jul/2026:10:00:12 +0000] "GET /api/orders HTTP/1.1" 200 512
10.0.0.9 - - [12/Jul/2026:10:00:13 +0000] "GET /api/billing HTTP/1.1" 502 32
10.0.0.7 - - [12/Jul/2026:10:00:14 +0000] "GET /api/users HTTP/1.1" 404 128
10.0.0.4 - - [12/Jul/2026:10:00:15 +0000] "GET /api/orders HTTP/1.1" 404 128
"""

_TASK_388_GOLD = """\
#!/usr/bin/env bash
set -euo pipefail

grep -E '" (4|5)[0-9][0-9] [0-9]+$' access.log \\
  | grep -oE '"[A-Z]+ [^ ]+' \\
  | cut -d' ' -f2 \\
  | sort \\
  | uniq -c \\
  | sort -k1,1nr -k2,2 \\
  | head -4 \\
  | tr -s ' ' \\
  | sed -E 's/^ ?([0-9]+) (.*)$/\\2,\\1/' > top_errors.csv
"""

TASK_388 = Task(
    id="task_388_cli_grep_uniq_ranking",
    name="grep + uniq -c: most frequent failing paths",
    tags=("cli", "posix", "grep", "pipeline", "hard"),
    prompt=(
        "access.log — лог веб-сервера в комбинированном формате: код ответа и размер стоят"
        " в конце строки после закавыченного запроса. Собери top_errors.csv — четыре пути,"
        " которые чаще всего отвечали с кодом 4xx или 5xx. Формат строки: путь,количество;"
        " сортировка по количеству по убыванию, при равенстве — по пути по возрастанию;"
        " заголовка нет. Путь — это второе поле внутри кавычек, без метода и без версии"
        " протокола. Обрати внимание, что uniq -c выравнивает счётчик пробелами, а в файле"
        " лишних пробелов быть не должно. awk в этой задаче запрещён. " + _SHELL_RULES
    ),
    setup_files={"access.log": _ACCESS_LOG},
    setup_callback=_setup_wave(),
    gold_files={"solve.sh": _TASK_388_GOLD},
    verifier=_script_produces(
        "solve.sh",
        {"top_errors.csv": "/api/orders,4\n/api/users,4\n/api/reports,2\n/api/billing,1\n"},
        must_use=("access.log",),
        banned_tools=("awk",),
        allowed_note="Считать частоты нужно через sort и uniq -c.",
    ),
)


# ---------------------------------------------------------------------------
# 389. find + xargs -0: predicate-driven file selection
# ---------------------------------------------------------------------------
def _write_log_tree(ws: Path) -> None:
    """Log tree with exact byte sizes so `find -size` is deterministic."""
    files = {
        "logs/svc-a/app.log": 40,
        "logs/svc-a/debug.log": 5,
        "logs/svc-a/notes.txt": 90,
        "logs/svc-b/app.log": 25,
        "logs/svc-b/audit.log": 60,
        "logs/svc-b/trace.log": 7,
        "logs/archive/app.log": 200,
        "logs/archive/old.log": 150,
        "logs/root.log": 12,
    }
    for rel, count in files.items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(("x" * 60 + "\n") * count, encoding="utf-8", newline="\n")


_TASK_389_GOLD = """\
#!/usr/bin/env bash
set -euo pipefail

find logs -type f -name '*.log' -not -path 'logs/archive/*' -size +499c -print0 \\
  | xargs -0 wc -l \\
  | grep -v ' total$' \\
  | tr -s ' ' \\
  | sed 's/^ //' \\
  | sed -E 's/^([0-9]+) (.*)$/\\2,\\1/' \\
  | sort -t, -k2,2nr -k1,1 > big_logs.csv
"""

TASK_389 = Task(
    id="task_389_cli_find_xargs_predicates",
    name="find + xargs: line counts of selected log files",
    tags=("cli", "posix", "find", "xargs", "pipeline", "hard"),
    prompt=(
        "Под каталогом logs/ лежит дерево файлов. Собери big_logs.csv по обычным файлам с"
        " расширением .log, которые весят строго больше 499 байт и не лежат внутри"
        " logs/archive/. Для каждого посчитай число строк. Формат строки:"
        " путь,число_строк, где путь записан относительно рабочей директории и начинается с"
        " logs/; сортировка по числу строк по убыванию, при равенстве — по пути по"
        " возрастанию; заголовка нет. Отбор файлов должен делать find, а список ему"
        " передавай через xargs с нулевым разделителем. Учти, что wc при нескольких файлах"
        " добавляет итоговую строку и выравнивает числа пробелами. awk запрещён. "
        + _SHELL_RULES
    ),
    setup_files={},
    setup_callback=_setup_wave(_write_log_tree),
    gold_files={"solve.sh": _TASK_389_GOLD},
    verifier=_script_produces(
        "solve.sh",
        {
            "big_logs.csv": (
                "logs/svc-b/audit.log,60\n"
                "logs/svc-a/app.log,40\n"
                "logs/svc-b/app.log,25\n"
                "logs/root.log,12\n"
            )
        },
        must_use=("logs",),
        banned_tools=("awk",),
        allowed_note="Отбирать файлы нужно через find и xargs -0.",
    ),
)


# ---------------------------------------------------------------------------
# 390. awk: grouped aggregation with formatted output
# ---------------------------------------------------------------------------
_TRANSACTIONS_CSV = """\
tx_id,category,amount,currency,status
T-001,groceries,42.50,EUR,settled
T-002,transport,3.20,EUR,settled
T-003,groceries,17.05,EUR,pending
T-004,utilities,120.00,EUR,settled
T-005,transport,2.80,EUR,settled
T-006,groceries,88.90,EUR,settled
T-007,dining,64.10,EUR,failed
T-008,utilities,75.40,EUR,settled
T-009,dining,31.25,EUR,settled
T-010,transport,12.00,EUR,pending
T-011,dining,19.95,EUR,settled
T-012,groceries,5.60,EUR,settled
T-013,utilities,220.15,EUR,settled
T-014,dining,44.00,EUR,settled
"""

_TASK_390_GOLD = """\
#!/usr/bin/env bash
set -euo pipefail

tail -n +2 transactions.csv \\
  | awk -F, '$5 == "settled" {sum[$2] += $3; n[$2]++} \\
             END {for (c in sum) printf "%s,%d,%.2f,%.2f\\n", c, n[c], sum[c], sum[c]/n[c]}' \\
  | sort -t, -k3,3nr > category_report.csv
"""

TASK_390 = Task(
    id="task_390_cli_awk_group_report",
    name="awk: per-category totals and averages",
    tags=("cli", "posix", "awk", "pipeline", "medium"),
    prompt=(
        "transactions.csv содержит tx_id,category,amount,currency,status и строку заголовка."
        " Собери category_report.csv только по проводкам со статусом settled: по строке на"
        " категорию в виде категория,количество,сумма,среднее, где сумма и среднее — с двумя"
        " знаками после запятой. Сортировка по сумме по убыванию, заголовка нет."
        " Группировку и арифметику делай в awk. " + _SHELL_RULES
    ),
    setup_files={"transactions.csv": _TRANSACTIONS_CSV},
    setup_callback=_setup_wave(),
    gold_files={"solve.sh": _TASK_390_GOLD},
    verifier=_script_produces(
        "solve.sh",
        {
            "category_report.csv": (
                "utilities,3,415.55,138.52\n"
                "groceries,3,137.00,45.67\n"
                "dining,3,95.20,31.73\n"
                "transport,2,6.00,3.00\n"
            )
        },
        must_use=("transactions.csv",),
        allowed_note="Группировать нужно средствами командной строки.",
    ),
)


# ---------------------------------------------------------------------------
# 391. sed: address range plus capture-group rewrite
# ---------------------------------------------------------------------------
_DEPLOY_CONF = """\
# global deployment settings
[meta]
owner = platform
# ticket = OPS-91
revision = 17

[runtime]
# these are read by the supervisor
image_tag = v2.14.3
replicas = 6
max_restarts = 2

# a blank line and a comment inside the block
health_path = /healthz
grace_period = 45

[limits]
cpu = 2000m
memory = 4Gi
"""

_TASK_391_GOLD = """\
#!/usr/bin/env bash
set -euo pipefail

sed -n '/^\\[runtime\\]$/,/^\\[/p' deploy.conf \\
  | grep -E '^[a-z_]+ = ' \\
  | sed -E 's/^([a-z_]+) = (.*)$/runtime.\\1=\\2/' \\
  | sort > runtime.env
"""

TASK_391 = Task(
    id="task_391_cli_sed_section_extract",
    name="sed: extract one ini section and rewrite its keys",
    tags=("cli", "posix", "sed", "pipeline", "hard"),
    prompt=(
        "deploy.conf разбит на секции вида [имя]. Вытащи только секцию [runtime] — она"
        " заканчивается на следующем заголовке в квадратных скобках — и собери runtime.env."
        " Каждую пару вида ключ = значение перепиши в runtime.ключ=значение, без пробелов"
        " вокруг знака равенства. Комментарии, пустые строки и сами заголовки секций в"
        " результат попадать не должны, значения менять нельзя. Строки отсортируй по"
        " возрастанию. Вырезать диапазон и переписывать строки нужно через sed; awk"
        " запрещён. " + _SHELL_RULES
    ),
    setup_files={"deploy.conf": _DEPLOY_CONF},
    setup_callback=_setup_wave(),
    gold_files={"solve.sh": _TASK_391_GOLD},
    verifier=_script_produces(
        "solve.sh",
        {
            "runtime.env": (
                "runtime.grace_period=45\n"
                "runtime.health_path=/healthz\n"
                "runtime.image_tag=v2.14.3\n"
                "runtime.max_restarts=2\n"
                "runtime.replicas=6\n"
            )
        },
        must_use=("deploy.conf",),
        banned_tools=("awk",),
        allowed_note="Вырезать секцию и переписывать строки нужно через sed.",
    ),
)


CLI_TASKS: list[Task] = [
    TASK_372,
    TASK_373,
    TASK_374,
    TASK_375,
    TASK_376,
    TASK_377,
    TASK_378,
    TASK_379,
    TASK_380,
    TASK_381,
    TASK_382,
    TASK_383,
    TASK_384,
    TASK_385,
    TASK_386,
    TASK_387,
    TASK_388,
    TASK_389,
    TASK_390,
    TASK_391,
]
