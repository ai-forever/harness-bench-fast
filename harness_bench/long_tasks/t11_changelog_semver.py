"""long_11_changelog_semver — CHANGELOG и семантические версии по истории git и тредам.

Setup builds (via ``git fast-import``) a repository of a small library ``tablo``
with ~256 commits.  Every commit is produced by one operation on a world model
(the library's functions, ``__all__``, CLI flags, tests and docs) and references
an issue/PR thread ``issues/N.md`` (written next to the repository, not
committed).  API changes are categorised by the diff alone; behaviour changes
(public bodies, non-public code, changed defaults, same-release reverts) by the
maintainers' final decision in the thread, per ``CONTRIBUTING.md``.  Threads are
drafted from the model with traps (reversals, reopenings, opinions of
non-maintainers, a maintainer appointed mid-history, quotes, labels,
"regression from #M" that needs the release of #M) and can be paraphrased by an
LLM via ``REWRITE`` (``scripts/rewrite_long_texts.py``).  ``RELEASES.md`` lists
the release points (one of them is corrected in a note).

Commit hashes are computed in pure Python (git object format) from the model,
so the checker never needs git and never trusts the workspace repository; the
threads live outside git, so rewriting them never changes a hash.
"""

from __future__ import annotations

import ast
import datetime as _dt
import functools
import hashlib
import io
import json
import re
import subprocess
import textwrap
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

from .common import _GIT_ENV, git, long_task, read_text, require_share, rewritten, rng, write

TASK_ID = "long_11_changelog_semver"
PKG = "tablo"
MODULES = ("parsing", "formatting", "numfmt", "dates", "text", "stats")
TZ = "+0300"

# ---------------------------------------------------------------------------
# Function bank
# ---------------------------------------------------------------------------


@dataclass
class Param:
    name: str
    default: str | None  # source text; None = required
    ann: str | None


@dataclass
class Fn:
    name: str
    module: str
    params: list[Param]
    ret: str | None
    doc: str
    ru: str
    body: str
    ex: str
    imports: tuple[str, ...] = ()
    locals: dict[str, str] = field(default_factory=dict)
    lits: list[tuple[str, str]] = field(default_factory=list)
    fixes: list[list[str]] = field(default_factory=list)  # [old, new, what]
    renames: dict[str, str] = field(default_factory=dict)
    alts: dict[str, str] = field(default_factory=dict)
    annotated: bool = False
    doc_extra: list[str] = field(default_factory=list)
    snippets: dict[str, str] = field(default_factory=dict)  # op-added param -> body text
    aliases: list[str] = field(default_factory=list)
    applied: list[dict] = field(default_factory=list)  # behaviour changes that a revert may undo
    guards: set[str] = field(default_factory=set)


def _parse_sig(sig: str) -> tuple[list[Param], str | None]:
    src = f"def f({sig.split(' -> ')[0]}): pass"
    node = ast.parse(src).body[0]
    assert isinstance(node, ast.FunctionDef)
    args = node.args.args
    defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
    params = []
    for arg, dflt in zip(args, defaults, strict=True):
        ann = ast.get_source_segment(src, arg.annotation) if arg.annotation else None
        dsrc = ast.get_source_segment(src, dflt) if dflt is not None else None
        params.append(Param(arg.arg, dsrc, ann))
    ret = sig.split(" -> ")[1] if " -> " in sig else None
    return params, ret


def _f(module, sig_name, sig, doc, ru, body, *, ex, imports=(), loc=None, lits=(), fixes=(),
       ren=None, alt=None, annotated=False) -> Fn:
    params, ret = _parse_sig(sig)
    return Fn(
        name=sig_name, module=module, params=params, ret=ret, doc=doc, ru=ru,
        body=textwrap.dedent(body).strip("\n") + "\n", ex=ex, imports=tuple(imports),
        locals=dict(loc or {}), lits=list(lits), fixes=[list(x) for x in fixes],
        renames=dict(ren or {}), alts=dict(alt or {}), annotated=annotated,
    )


def _bank_public() -> list[Fn]:
    return [
        _f("parsing", "split_line", 'line: str, sep: str = ",", quote: str = \'"\' -> list[str]',
           "Split one line into fields, honouring quotes.",
           "разбивает одну строку на поля с учётом кавычек",
           r'''
           fields = []
           buf = []
           in_quotes = False
           for ch in line:
               if ch == quote:
                   in_quotes = not in_quotes
               elif ch == sep and not in_quotes:
                   fields.append("".join(buf))
                   buf = []
               else:
                   buf.append(ch)
           fields.append("".join(buf))
           return fields
           ''', ex='"a,b,c"', loc={"fields": "parts", "buf": "chunk", "in_quotes": "quoted"},
           fixes=[["for ch in line:", 'for ch in line.rstrip("\\r\\n"):', "trailing newline"],
                  ["return fields", "return [item.strip() for item in fields]", "whitespace around fields"]],
           ren={"sep": "delimiter"}, alt={"quote": '"\'"'}),
        _f("parsing", "parse_table", 'text: str, sep: str = ",", header: bool = True -> list',
           "Parse delimited text into rows (dicts when there is a header).",
           "превращает текст с разделителями в список строк таблицы",
           r'''
           lines = [ln for ln in text.splitlines() if ln.strip()]
           if not lines:
               return []
           rows = [_split_simple(ln, sep) for ln in lines]
           if not header:
               return rows
           names = rows[0]
           return [dict(zip(names, row)) for row in rows[1:]]
           ''', ex='"a,b\\n1,2"', loc={"lines": "raw_lines", "rows": "records", "names": "columns"},
           fixes=[["if ln.strip()]", 'if ln.strip() and not ln.lstrip().startswith("#")]', "comment lines"],
                  ["dict(zip(names, row))", "dict(zip([n.strip() for n in names], row))",
                   "spaces in header names"]],
           ren={"sep": "delimiter"}, alt={"header": "False"}),
        _f("parsing", "parse_bool", "value, default: bool = False -> bool",
           "Interpret a textual flag such as 'yes' or '0'.",
           "понимает текстовые логические значения вроде «yes» или «0»",
           r'''
           if value is None:
               return default
           text = str(value).strip().lower()
           if text in ("1", "true", "yes", "y", "on"):
               return True
           if text in ("0", "false", "no", "n", "off"):
               return False
           return default
           ''', ex='"yes"', loc={"text": "token"},
           fixes=[['("1", "true", "yes", "y", "on")', '("1", "true", "yes", "y", "on", "да")', "Russian 'да'"],
                  ['("0", "false", "no", "n", "off")', '("0", "false", "no", "n", "off", "нет")',
                   "Russian 'нет'"]],
           alt={"default": "None"}),
        _f("parsing", "parse_int", "value, default: int | None = None -> int | None",
           "Parse an integer, returning *default* when impossible.",
           "разбирает целое число, а при неудаче возвращает значение по умолчанию",
           r'''
           if value is None:
               return default
           text = str(value).strip().replace(" ", "")
           if not text:
               return default
           try:
               return int(text)
           except ValueError:
               return default
           ''', ex='"1 024"', loc={"text": "cleaned"},
           fixes=[['.replace(" ", "")', '.replace(" ", "").replace("_", "")', "underscores in numbers"],
                  ["return int(text)", "return int(float(text))", "values like '12.0'"]],
           alt={"default": "0"}),
        _f("parsing", "parse_number", 'value, decimal: str = ".", default=None -> float | None',
           "Parse a float with a configurable decimal mark.",
           "разбирает дробное число с заданным десятичным разделителем",
           r'''
           if value is None:
               return default
           text = str(value).strip()
           if decimal != ".":
               text = text.replace(".", "").replace(decimal, ".")
           try:
               return float(text)
           except ValueError:
               return default
           ''', ex='"3,5", decimal=","', loc={"text": "raw"},
           fixes=[["text = str(value).strip()\n", 'text = str(value).strip().replace("\\u00a0", "")\n',
                   "non-breaking spaces"],
                  ["return float(text)", 'return float(text.rstrip("%"))', "trailing percent sign"]],
           alt={"decimal": '","'}),
        _f("parsing", "sniff_separator", 'sample: str, candidates: str = ",;\\t|" -> str',
           "Guess the field separator of a text sample.",
           "угадывает разделитель полей по образцу текста",
           r'''
           counts = {}
           for cand in candidates:
               counts[cand] = sample.count(cand)
           best = max(counts, key=counts.get)
           if counts[best] == 0:
               return ","
           return best
           ''', ex='"a;b;c"', loc={"counts": "tally", "best": "winner", "cand": "candidate"},
           fixes=[["counts[cand] = sample.count(cand)",
                   "counts[cand] = sample.splitlines()[0].count(cand) if sample else 0", "multi-line samples"],
                  ['return ","', "return candidates[0]", "fallback separator"]],
           alt={"candidates": '",;|"'}),
        _f("parsing", "read_records", 'path, sep: str = ",", encoding: str = "utf-8" -> list[dict]',
           "Read a delimited file with a header row into dicts.",
           "читает файл с заголовком в список словарей",
           r'''
           with open(path, encoding=encoding) as fh:
               text = fh.read()
           lines = text.splitlines()
           if not lines:
               return []
           header = _split_simple(lines[0], sep)
           out = []
           for line in lines[1:]:
               values = _split_simple(line, sep)
               out.append(dict(zip(header, values)))
           return out
           ''', ex='"data.csv"', loc={"out": "records", "values": "cells", "header": "names"},
           fixes=[["with open(path, encoding=encoding) as fh:",
                   'with open(path, encoding=encoding, newline="") as fh:', "CRLF files"],
                  ["for line in lines[1:]:", "for line in (ln for ln in lines[1:] if ln.strip()):",
                   "blank lines"]],
           ren={"sep": "delimiter"}, alt={"encoding": '"utf-8-sig"'}),
        _f("formatting", "pad_cell", 'value, width: int, align: str = "left" -> str',
           "Pad a value to *width* characters.", "дополняет значение пробелами до заданной ширины",
           r'''
           text = str(value)
           if len(text) >= width:
               return text
           if align == "right":
               return text.rjust(width)
           if align == "center":
               return text.center(width)
           return text.ljust(width)
           ''', ex='"x", 5', loc={"text": "cell"},
           fixes=[["if len(text) >= width:\n    return text\n", "if len(text) >= width:\n    return text[:width]\n",
                   "overlong values"],
                  ["text = str(value)", 'text = "" if value is None else str(value)', "None values"]],
           ren={"width": "size"}, alt={"align": '"right"'}),
        _f("formatting", "render_table",
           'rows, headers=None, sep: str = " | ", border: bool = True -> str',
           "Render rows as an aligned plain-text table.", "печатает строки как выровненную текстовую таблицу",
           r'''
           table = [list(map(str, row)) for row in rows]
           if headers:
               table.insert(0, [str(h) for h in headers])
           if not table:
               return ""
           widths = [max(len(row[i]) for row in table) for i in range(len(table[0]))]
           lines = [sep.join(cell.ljust(w) for cell, w in zip(row, widths)) for row in table]
           if headers and border:
               lines.insert(1, "-+-".join("-" * w for w in widths))
           return "\n".join(lines)
           ''', ex='[["a", "b"]]', loc={"table": "grid", "widths": "col_widths", "lines": "out_lines"},
           lits=[('"-+-"', "_RULE_JOINT")],
           fixes=[['return "\\n".join(lines)', 'return "\\n".join(line.rstrip() for line in lines)',
                   "trailing spaces"],
                  ["max(len(row[i]) for row in table)", "max((len(row[i]) for row in table if i < len(row)), default=0)",
                   "ragged rows"]],
           ren={"headers": "header"}, alt={"sep": '" "', "border": "False"}),
        _f("formatting", "format_column", 'values, width: int = 10, align: str = "right" -> list[str]',
           "Format a column of values to a fixed width.", "форматирует столбец значений до фиксированной ширины",
           r'''
           result = []
           for value in values:
               text = _stringify(value)
               if align == "right":
                   result.append(text.rjust(width))
               else:
                   result.append(text.ljust(width))
           return result
           ''', ex="[1, 2.5, None]", loc={"result": "cells", "text": "shown"},
           fixes=[["result.append(text.ljust(width))",
                   'result.append(text.center(width) if align == "center" else text.ljust(width))',
                   "center alignment"],
                  ["text = _stringify(value)", "text = _stringify(value)[:width]", "overlong cells"]],
           alt={"width": "12", "align": '"left"'}),
        _f("formatting", "truncate_cell", 'text: str, limit: int = 20, marker: str = "…" -> str',
           "Shorten *text* to *limit* characters.", "обрезает текст до заданной длины с многоточием",
           r'''
           if len(text) <= limit:
               return text
           cut = limit - len(marker)
           return text[:cut] + marker
           ''', ex='"a very long cell value indeed"', loc={"cut": "keep"},
           fixes=[["cut = limit - len(marker)", "cut = max(limit - len(marker), 0)", "tiny limits"],
                  ["return text[:cut] + marker", "return text[:cut].rstrip() + marker", "space before marker"]],
           ren={"limit": "max_len"}, alt={"limit": "30", "marker": '"..."'}),
        _f("formatting", "to_markdown", "rows, headers -> str",
           "Render rows as a Markdown table.", "выводит таблицу в разметке Markdown",
           r'''
           head = "| " + " | ".join(headers) + " |"
           rule = "|" + "|".join("---" for _ in headers) + "|"
           body = ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
           return "\n".join([head, rule, *body])
           ''', ex='[[1, 2]], ["a", "b"]', loc={"head": "header_line", "body": "body_lines", "rule": "divider"},
           lits=[('"---"', "_MD_RULE")],
           fixes=[['" | ".join(str(c) for c in row)', '" | ".join(str(c).replace("|", "\\\\|") for c in row)',
                   "pipes inside cells"],
                  ['" | ".join(headers)', '" | ".join(str(h) for h in headers)', "non-string headers"]]),
        _f("formatting", "to_html", 'rows, headers=None, css_class: str = "tablo" -> str',
           "Render rows as an HTML table.", "выводит таблицу в HTML",
           r'''
           parts = ['<table class="%s">' % css_class]
           if headers:
               parts.append("<tr>" + "".join("<th>%s</th>" % h for h in headers) + "</tr>")
           for row in rows:
               parts.append("<tr>" + "".join("<td>%s</td>" % c for c in row) + "</tr>")
           parts.append("</table>")
           return "\n".join(parts)
           ''', ex='[[1, 2]]', imports=("html",), loc={"parts": "chunks"},
           fixes=[['"<td>%s</td>" % c for c in row', '"<td>%s</td>" % html.escape(str(c)) for c in row',
                   "HTML escaping in cells"],
                  ['"<th>%s</th>" % h for h in headers', '"<th>%s</th>" % html.escape(str(h)) for h in headers',
                   "HTML escaping in headers"]],
           alt={"css_class": '"table"'}),
        _f("numfmt", "format_number", 'value, ndigits: int = 2, thousands: str = " " -> str',
           "Format a number with a thousands separator.", "форматирует число с разделителем разрядов",
           r'''
           rounded = round(float(value), ndigits)
           text = f"{rounded:,.{ndigits}f}"
           return text.replace(",", thousands)
           ''', ex="1234567.891", loc={"rounded": "number", "text": "formatted"},
           fixes=[["f\"{rounded:,.{ndigits}f}\"", "f\"{rounded:,.{max(ndigits, 0)}f}\"", "negative ndigits"],
                  ['return text.replace(",", thousands)', 'return text.replace(",", thousands).replace("-0.00", "0.00")',
                   "negative zero"]],
           ren={"ndigits": "precision"}, alt={"thousands": '","'}),
        _f("numfmt", "format_percent", "value, ndigits: int = 1, sign: bool = False -> str",
           "Format a ratio as a percentage.", "показывает долю в процентах",
           r'''
           pct = float(value) * 100
           text = f"{pct:.{ndigits}f}%"
           if sign and pct > 0:
               text = "+" + text
           return text
           ''', ex="0.256", loc={"pct": "percent", "text": "label"},
           fixes=[["if sign and pct > 0:", "if sign and pct >= 0:", "sign for zero"],
                  ['text = f"{pct:.{ndigits}f}%"', 'text = f"{pct:.{ndigits}f}\\u00a0%"', "space before percent sign"]],
           ren={"sign": "show_sign"}, alt={"ndigits": "2"}),
        _f("numfmt", "format_money", 'amount, currency: str = "RUB", ndigits: int = 2 -> str',
           "Format an amount of money with a currency symbol.", "форматирует денежную сумму с символом валюты",
           r'''
           symbol = _CURRENCY_SYMBOLS.get(currency, currency)
           text = f"{abs(amount):,.{ndigits}f}".replace(",", " ")
           if amount < 0:
               return f"-{text} {symbol}"
           return f"{text} {symbol}"
           ''', ex="-1500", loc={"symbol": "sign_char", "text": "digits"},
           fixes=[['.replace(",", " ")', '.replace(",", "\\u00a0")', "non-breaking thousands separator"],
                  ["_CURRENCY_SYMBOLS.get(currency, currency)", "_CURRENCY_SYMBOLS.get(currency.upper(), currency)",
                   "lower-case currency codes"]],
           alt={"currency": '"USD"'}),
        _f("numfmt", "round_half_up", "value, ndigits: int = 0 -> float",
           "Round half away from zero (unlike built-in round).", "округляет «половину» от нуля, в отличие от round",
           r'''
           factor = 10 ** ndigits
           scaled = abs(value) * factor
           result = math.floor(scaled + 0.5) / factor
           return math.copysign(result, value)
           ''', ex="2.5", imports=("math",), loc={"factor": "multiplier", "scaled": "magnitude"},
           lits=[("0.5", "_HALF")],
           fixes=[["scaled = abs(value) * factor", "scaled = round(abs(value) * factor, 9)", "float noise"]],
           alt={"ndigits": "2"}),
        _f("numfmt", "parse_size", "text: str -> int",
           "Parse sizes like '10K' or '2.5M' into bytes.", "переводит размеры вида «10K» или «2.5M» в байты",
           r'''
           text = text.strip().upper()
           units = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}
           if text and text[-1] in units:
               return int(float(text[:-1]) * units[text[-1]])
           return int(text)
           ''', ex='"10K"', loc={"units": "multipliers"},
           fixes=[["text = text.strip().upper()", 'text = text.strip().upper().removesuffix("B")', "'KB' suffix"],
                  ["return int(float(text[:-1]) * units[text[-1]])", "return round(float(text[:-1]) * units[text[-1]])",
                   "rounding of fractional sizes"]]),
        _f("numfmt", "human_size", "num_bytes, ndigits: int = 1 -> str",
           "Format a byte count for humans.", "показывает число байт в удобных единицах",
           r'''
           size = float(num_bytes)
           for unit in ("B", "K", "M", "G"):
               if abs(size) < 1024:
                   return f"{size:.{ndigits}f}{unit}"
               size /= 1024
           return f"{size:.{ndigits}f}T"
           ''', ex="123456", loc={"size": "amount"},
           fixes=[['return f"{size:.{ndigits}f}{unit}"',
                   'return f"{int(size)}B" if unit == "B" else f"{size:.{ndigits}f}{unit}"', "whole bytes"]],
           alt={"ndigits": "2"}),
        _f("dates", "parse_date", "text: str, dayfirst: bool = True",
           "Parse a date written in one of the common formats.", "разбирает дату в одном из распространённых форматов",
           r'''
           text = text.strip()
           formats = _DATE_FORMATS if dayfirst else _DATE_FORMATS_US
           for fmt in formats:
               try:
                   return datetime.datetime.strptime(text, fmt).date()
               except ValueError:
                   continue
           raise ValueError(f"unrecognized date: {text!r}")
           ''', ex='"01.02.2023"', imports=("datetime",), loc={"formats": "candidates", "fmt": "pattern"},
           fixes=[["text = text.strip()\n", 'text = text.strip().removesuffix("г.").strip()\n', "Russian year suffix"],
                  ["for fmt in formats:", 'for fmt in (*formats, "%Y%m%d"):', "compact dates"]],
           alt={"dayfirst": "False"}),
        _f("dates", "format_date", 'value, style: str = "iso" -> str',
           "Format a date in the requested style.", "форматирует дату в выбранном стиле",
           r'''
           if style == "iso":
               return value.isoformat()
           if style == "ru":
               return value.strftime("%d.%m.%Y")
           if style == "us":
               return value.strftime("%m/%d/%Y")
           raise ValueError(f"unknown style: {style}")
           ''', ex="datetime.date(2023, 5, 1)", imports=("datetime",),
           lits=[('"%d.%m.%Y"', "_RU_DATE"), ('"%m/%d/%Y"', "_US_DATE")],
           fixes=[["return value.isoformat()", 'return value.strftime("%Y-%m-%d")', "datetime values"]],
           ren={"style": "fmt"}, alt={"style": '"ru"'}),
        _f("dates", "add_workdays", "start, days: int, holidays=()",
           "Move *days* working days forward (or back) from *start*.",
           "сдвигает дату на заданное число рабочих дней",
           r'''
           current = start
           step = 1 if days >= 0 else -1
           remaining = abs(days)
           while remaining:
               current += datetime.timedelta(days=step)
               if current.weekday() < 5 and current not in holidays:
                   remaining -= 1
           return current
           ''', ex="datetime.date(2023, 5, 5), 3", imports=("datetime",),
           loc={"current": "day", "remaining": "left", "step": "direction"},
           lits=[("5", "_SATURDAY")],
           fixes=[["current = start\n", "current = start.date() if isinstance(start, datetime.datetime) else start\n",
                   "datetime input"]],
           ren={"holidays": "skip_days"}),
        _f("dates", "month_bounds", "year: int, month: int",
           "Return the first and the last day of a month.", "возвращает первый и последний день месяца",
           r'''
           first = datetime.date(year, month, 1)
           if month == 12:
               nxt = datetime.date(year + 1, 1, 1)
           else:
               nxt = datetime.date(year, month + 1, 1)
           return first, nxt - datetime.timedelta(days=1)
           ''', ex="2023, 2", imports=("datetime",), loc={"first": "start", "nxt": "following"},
           fixes=[["first = datetime.date(year, month, 1)\n",
                   'if not 1 <= month <= 12:\n    raise ValueError(f"bad month: {month}")\n'
                   "first = datetime.date(year, month, 1)\n", "invalid month"]]),
        _f("dates", "quarter_of", "value -> int",
           "Return the quarter (1-4) of a date.", "возвращает номер квартала даты",
           r'''
           return (value.month - 1) // 3 + 1
           ''', ex="datetime.date(2023, 8, 1)",
           fixes=[["return (value.month - 1) // 3 + 1",
                   'month = value.month if hasattr(value, "month") else int(value)\nreturn (month - 1) // 3 + 1',
                   "plain month numbers"]]),
        _f("dates", "iso_week_label", "value -> str",
           "Return an ISO week label like 2023-W07.", "возвращает метку ISO-недели вида 2023-W07",
           r'''
           year, week, _ = value.isocalendar()
           return f"{year}-W{week:02d}"
           ''', ex="datetime.date(2023, 2, 14)",
           fixes=[['return f"{year}-W{week:02d}"', 'return f"{year}W{week:02d}"', "label format"]]),
        _f("text", "slugify", 'text: str, sep: str = "-", max_len=None -> str',
           "Make a URL-friendly slug.", "делает из строки «слаг» для URL",
           r'''
           normalized = unicodedata.normalize("NFKD", text)
           ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
           words = re.findall(r"[a-z0-9]+", ascii_text.lower())
           slug = sep.join(words)
           if max_len:
               slug = slug[:max_len].rstrip(sep)
           return slug
           ''', ex='"Hello, World!"', imports=("re", "unicodedata"),
           loc={"normalized": "decomposed", "words": "tokens", "slug": "result"},
           fixes=[['unicodedata.normalize("NFKD", text)', 'unicodedata.normalize("NFKD", _translit(text))',
                   "Cyrillic input"]],
           ren={"max_len": "limit"}, alt={"sep": '"_"'}),
        _f("text", "wrap_text", 'text: str, width: int = 72, indent: str = "" -> str',
           "Greedy word wrap.", "переносит текст по словам на заданную ширину",
           r'''
           words = text.split()
           lines = []
           current = indent
           for word in words:
               if len(current) + len(word) + 1 > width and current.strip():
                   lines.append(current.rstrip())
                   current = indent
               current += word + " "
           if current.strip():
               lines.append(current.rstrip())
           return "\n".join(lines)
           ''', ex='"one two three four", 8', loc={"lines": "out", "current": "line_buf"},
           fixes=[["if len(current) + len(word) + 1 > width and current.strip():",
                   "if len(current) + len(word) > width and current.strip():", "off-by-one line width"]],
           ren={"indent": "prefix"}, alt={"width": "80"}),
        _f("text", "normalize_spaces", "text: str -> str",
           "Collapse runs of spaces and tabs.", "схлопывает повторяющиеся пробелы и табуляции",
           r'''
           text = text.replace(" ", " ").replace("\t", " ")
           return re.sub(r" {2,}", " ", text).strip()
           ''', ex='"a  b\\tc"', imports=("re",),
           fixes=[['re.sub(r" {2,}", " ", text)', 're.sub(r" {2,}", " ", text.replace("\\u200b", ""))',
                   "zero-width spaces"]]),
        _f("text", "title_case", 'text: str, exceptions=("и", "в", "на", "of", "the") -> str',
           "Capitalize words except short connectives.", "делает слова с заглавной буквы, кроме коротких союзов",
           r'''
           words = text.split()
           out = []
           for i, word in enumerate(words):
               if i and word.lower() in exceptions:
                   out.append(word.lower())
               else:
                   out.append(word[:1].upper() + word[1:])
           return " ".join(out)
           ''', ex='"война и мир"', loc={"words": "parts", "out": "result"},
           fixes=[["out.append(word[:1].upper() + word[1:])", "out.append(word[:1].upper() + word[1:].lower())",
                   "all-caps words"]],
           ren={"exceptions": "small_words"}),
        _f("text", "count_words", "text: str, min_len: int = 1 -> int",
           "Count words of at least *min_len* characters.", "считает слова не короче заданной длины",
           r'''
           words = re.findall(r"\w+", text)
           return sum(1 for w in words if len(w) >= min_len)
           ''', ex='"to be or not"', imports=("re",), loc={"words": "found"},
           fixes=[['re.findall(r"\\w+", text)', 're.findall(r"\\w+(?:-\\w+)*", text)', "hyphenated words"]],
           alt={"min_len": "2"}),
        _f("text", "strip_accents", "text: str -> str",
           "Remove combining accents.", "убирает диакритические знаки",
           r'''
           decomposed = unicodedata.normalize("NFD", text)
           return "".join(ch for ch in decomposed if not unicodedata.combining(ch))
           ''', ex='"café"', imports=("unicodedata",), loc={"decomposed": "nfd"},
           fixes=[['return "".join(ch for ch in decomposed if not unicodedata.combining(ch))',
                   'return unicodedata.normalize("NFC", "".join(ch for ch in decomposed '
                   'if not unicodedata.combining(ch) or ch == "\\u0306"))', "the letter й"]]),
        _f("stats", "mean", "values -> float",
           "Arithmetic mean ignoring None.", "среднее арифметическое без учёта None",
           r'''
           data = [float(v) for v in values if v is not None]
           if not data:
               raise ValueError("mean of empty data")
           return sum(data) / len(data)
           ''', ex="[1, 2, None, 3]", loc={"data": "numbers"},
           fixes=[["if v is not None]", "if v is not None and v == v]", "NaN values"]]),
        _f("stats", "median", "values -> float",
           "Median of the values.", "медиана значений",
           r'''
           data = sorted(float(v) for v in values)
           n = len(data)
           if n == 0:
               raise ValueError("median of empty data")
           mid = n // 2
           if n % 2:
               return data[mid]
           return (data[mid - 1] + data[mid]) / 2
           ''', ex="[3, 1, 2]", loc={"data": "ordered", "mid": "middle"},
           fixes=[["sorted(float(v) for v in values)", "sorted(float(v) for v in values if v is not None)",
                   "None values"]]),
        _f("stats", "percentile", 'values, q: float, method: str = "nearest" -> float',
           "Percentile by nearest rank or linear interpolation.", "процентиль по ближайшему рангу или интерполяцией",
           r'''
           data = sorted(float(v) for v in values)
           if not data:
               raise ValueError("empty data")
           if method == "nearest":
               idx = max(0, math.ceil(q / 100 * len(data)) - 1)
               return data[idx]
           pos = (len(data) - 1) * q / 100
           lo = math.floor(pos)
           hi = min(lo + 1, len(data) - 1)
           return data[lo] + (data[hi] - data[lo]) * (pos - lo)
           ''', ex="[1, 2, 3, 4], 50", imports=("math",), loc={"data": "ordered", "pos": "rank", "idx": "index"},
           fixes=[["idx = max(0, math.ceil(q / 100 * len(data)) - 1)",
                   "idx = min(len(data) - 1, max(0, math.ceil(q / 100 * len(data)) - 1))", "q above 100"]],
           alt={"method": '"linear"'}),
        _f("stats", "summarize", "values, ndigits=None -> dict",
           "Count, sum, min and max of the values.", "сводка: количество, сумма, минимум и максимум",
           r'''
           data = [v for v in values if v is not None]
           result = {"count": len(data), "sum": sum(data)}
           result["min"] = min(data) if data else None
           result["max"] = max(data) if data else None
           if ndigits is not None:
               result["sum"] = round(result["sum"], ndigits)
           return result
           ''', ex="[1, 2, 3]", loc={"data": "present", "result": "summary"},
           fixes=[["return result", 'result["mean"] = result["sum"] / len(data) if data else None\nreturn result',
                   "missing mean"]],
           ren={"ndigits": "precision"}),
        _f("stats", "moving_average", "values, window: int = 3 -> list[float]",
           "Simple moving average.", "простое скользящее среднее",
           r'''
           if window < 1:
               raise ValueError("window must be positive")
           out = []
           acc = 0.0
           for i, v in enumerate(values):
               acc += v
               if i >= window:
                   acc -= values[i - window]
               if i >= window - 1:
                   out.append(acc / window)
           return out
           ''', ex="[1, 2, 3, 4, 5]", loc={"out": "averages", "acc": "running"},
           fixes=[["out = []\n", "values = list(values)\nout = []\n", "generator input"]],
           ren={"window": "size"}, alt={"window": "5"}),
        _f("stats", "histogram", "values, bins: int = 10 -> list[int]",
           "Equal-width histogram counts.", "гистограмма с интервалами равной ширины",
           r'''
           data = [float(v) for v in values]
           if not data:
               return []
           lo, hi = min(data), max(data)
           step = (hi - lo) / bins or 1.0
           counts = [0] * bins
           for x in data:
               idx = min(int((x - lo) / step), bins - 1)
               counts[idx] += 1
           return counts
           ''', ex="[1, 2, 2, 3], 3", loc={"counts": "buckets", "step": "bin_width"},
           fixes=[["if not data:\n    return []\n", "if not data:\n    return [0] * bins\n", "empty input"]],
           alt={"bins": "20"}),
    ]


def _bank_unexported() -> list[Fn]:
    return [
        _f("parsing", "guess_encoding", "data: bytes -> str",
           "Guess the text encoding of raw bytes.", "угадывает кодировку байтов",
           r'''
           for enc in ("utf-8", "cp1251", "koi8-r"):
               try:
                   data.decode(enc)
               except UnicodeDecodeError:
                   continue
               return enc
           return "latin-1"
           ''', ex='b"abc"',
           fixes=[['for enc in ("utf-8", "cp1251", "koi8-r"):',
                   'if data.startswith(b"\\xef\\xbb\\xbf"):\n    return "utf-8-sig"\n'
                   'for enc in ("utf-8", "cp1251", "koi8-r"):', "BOM"]]),
        _f("formatting", "visible_width", "text: str -> int",
           "Width of text on screen, ignoring combining marks.", "ширина текста на экране",
           r'''
           return sum(0 if unicodedata.combining(ch) else 1 for ch in text)
           ''', ex='"abc"', imports=("unicodedata",),
           fixes=[["for ch in text)", 'for ch in text if ch != "\\u200b")', "zero-width spaces"]]),
        _f("_util", "chunked", "items, size: int -> list",
           "Split *items* into lists of *size*.", "делит последовательность на куски",
           r'''
           items = list(items)
           return [items[i:i + size] for i in range(0, len(items), size)]
           ''', ex="[1, 2, 3], 2",
           fixes=[["items = list(items)\n", 'if size <= 0:\n    raise ValueError("size must be positive")\n'
                   "items = list(items)\n", "non-positive size"]]),
        _f("_util", "ensure_list", "value -> list",
           "Wrap a scalar into a list.", "оборачивает скаляр в список",
           r'''
           if value is None:
               return []
           if isinstance(value, (list, tuple)):
               return list(value)
           return [value]
           ''', ex="5",
           fixes=[["(list, tuple)", "(list, tuple, set, frozenset)", "sets"]]),
        _f("_util", "first_non_empty", "values",
           "First value that is not None or empty.", "первое непустое значение",
           r'''
           for value in values:
               if value not in (None, ""):
                   return value
           return None
           ''', ex='[None, "", "x"]',
           fixes=[['if value not in (None, ""):', 'if value not in (None, "") and str(value).strip():',
                   "whitespace-only values"]]),
    ]


def _bank_private() -> list[Fn]:
    return [
        _f("parsing", "_split_simple", "line, sep",
           "Split without quote handling.", "", r'''
           return [part.strip() for part in line.split(sep)]
           ''', ex='"a,b", ","',
           fixes=[["line.split(sep)", 'line.rstrip("\\r\\n").split(sep)', "trailing newline"],
                  ["part.strip()", "part.strip().strip('\"')", "quoted values"]]),
        _f("parsing", "_unquote", "value, quote='\"'",
           "Remove surrounding quotes.", "", r'''
           if len(value) >= 2 and value[0] == quote and value[-1] == quote:
               return value[1:-1].replace(quote * 2, quote)
           return value
           ''', ex='"\\"x\\""', fixes=[["return value\n", "return value.strip()\n", "whitespace"]]),
        _f("formatting", "_stringify", "value",
           "Text form of a cell value.", "", r'''
           if value is None:
               return ""
           if isinstance(value, float):
               return f"{value:g}"
           return str(value)
           ''', ex="1.5", loc={"value": "cell"},
           fixes=[['if value is None:\n    return ""\n', 'if value is None:\n    return "-"\n', "None placeholder"],
                  ['return f"{value:g}"', "return repr(value)", "float precision"]]),
        _f("numfmt", "_sign", "value",
           "Sign prefix of a number.", "", r'''
           return "-" if value < 0 else ""
           ''', ex="-1", fixes=[["value < 0", 'value < 0 or str(value).startswith("-")', "negative zero"]]),
        _f("dates", "_coerce_date", "value",
           "Accept date, datetime or ISO string.", "", r'''
           if isinstance(value, datetime.datetime):
               return value.date()
           if isinstance(value, str):
               return datetime.date.fromisoformat(value)
           return value
           ''', ex='"2023-01-01"', imports=("datetime",),
           fixes=[["datetime.date.fromisoformat(value)", "datetime.date.fromisoformat(value.strip())", "whitespace"]]),
        _f("text", "_translit", "text",
           "Transliterate Cyrillic to Latin.", "", r'''
           return "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())
           ''', ex='"привет"', fixes=[["for ch in text.lower())", 'for ch in text.lower().replace("ё", "е"))', "ё"]]),
        _f("stats", "_clean", "values",
           "Drop missing values and convert to float.", "", r'''
           return [float(v) for v in values if v is not None and v != ""]
           ''', ex='[1, None, ""]', fixes=[['v != ""', 'str(v).strip() != ""', "blank strings"]]),
    ]


def _bank_new() -> list[Fn]:
    return [
        _f("parsing", "parse_kv", 'line: str, sep: str = "=", pair_sep: str = ";" -> dict',
           "Parse 'a=1; b=2' into a dict.", "разбирает строку «ключ=значение» в словарь",
           r'''
           result = {}
           for pair in line.split(pair_sep):
               if not pair.strip():
                   continue
               key, _, value = pair.partition(sep)
               result[key.strip()] = value.strip()
           return result
           ''', ex='"a=1; b=2"', loc={"result": "mapping"},
           fixes=[["if not pair.strip():", "if not pair.strip() or sep not in pair:", "pairs without separator"]],
           alt={"pair_sep": '","'}),
        _f("parsing", "detect_header", "rows -> bool",
           "Guess whether the first row is a header.", "угадывает, является ли первая строка заголовком",
           r'''
           if len(rows) < 2:
               return False
           first = rows[0]
           second = rows[1]
           numeric_first = sum(1 for cell in first if cell.replace(".", "", 1).isdigit())
           numeric_second = sum(1 for cell in second if cell.replace(".", "", 1).isdigit())
           return numeric_first < numeric_second
           ''', ex='[["a", "b"], ["1", "2"]]', loc={"first": "top", "second": "next_row"},
           fixes=[["if len(rows) < 2:\n    return False\n", "if len(rows) < 2:\n    return bool(rows)\n",
                   "single-row input"]]),
        _f("formatting", "to_csv", 'rows, sep: str = ",", newline: str = "\\n" -> str',
           "Serialize rows as delimited text.", "сериализует строки в текст с разделителями",
           r'''
           lines = []
           for row in rows:
               cells = [str(c) for c in row]
               lines.append(sep.join(cells))
           return newline.join(lines)
           ''', ex="[[1, 2]]", loc={"lines": "out", "cells": "fields"},
           fixes=[["cells = [str(c) for c in row]", "cells = ['\"%s\"' % c if sep in str(c) else str(c) for c in row]",
                   "quoting"]],
           alt={"newline": '"\\r\\n"'}),
        _f("formatting", "align_decimal", "values, ndigits: int = 2 -> list[str]",
           "Right-align numbers on the decimal point.", "выравнивает числа по десятичной точке",
           r'''
           texts = [f"{float(v):.{ndigits}f}" for v in values]
           width = max((len(t) for t in texts), default=0)
           return [t.rjust(width) for t in texts]
           ''', ex="[1, 10.5]", loc={"texts": "shown", "width": "span"},
           fixes=[['[f"{float(v):.{ndigits}f}" for v in values]',
                   '[f"{float(v):.{ndigits}f}" if v is not None else "" for v in values]', "None values"]]),
        _f("formatting", "column_widths", "rows -> list[int]",
           "Maximum width of every column.", "максимальная ширина каждого столбца",
           r'''
           widths = []
           for row in rows:
               for i, cell in enumerate(row):
                   size = len(str(cell))
                   if i >= len(widths):
                       widths.append(size)
                   elif size > widths[i]:
                       widths[i] = size
           return widths
           ''', ex='[["a", "bb"]]', loc={"widths": "result", "size": "cell_len"},
           fixes=[["size = len(str(cell))", 'size = len(str(cell).rstrip())', "trailing spaces"]]),
        _f("numfmt", "format_ordinal", 'n: int, lang: str = "en" -> str',
           "English or Russian ordinal numeral.", "порядковое числительное",
           r'''
           if lang == "ru":
               return f"{n}-й"
           if 10 <= n % 100 <= 20:
               suffix = "th"
           else:
               suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
           return f"{n}{suffix}"
           ''', ex="21", loc={"suffix": "ending"},
           fixes=[["if 10 <= n % 100 <= 20:", "if 11 <= n % 100 <= 13:", "numbers 14-20"]],
           alt={"lang": '"ru"'}),
        _f("numfmt", "clamp", "value, lo, hi",
           "Limit *value* to the range [lo, hi].", "ограничивает значение диапазоном",
           r'''
           if lo > hi:
               raise ValueError("lo must not exceed hi")
           return max(lo, min(hi, value))
           ''', ex="5, 0, 3",
           fixes=[['raise ValueError("lo must not exceed hi")', "lo, hi = hi, lo", "swapped bounds"]]),
        _f("dates", "days_between", "start, end, inclusive: bool = False -> int",
           "Number of days between two dates.", "число дней между датами",
           r'''
           delta = (end - start).days
           if inclusive:
               delta += 1
           return delta
           ''', ex="datetime.date(2023, 1, 1), datetime.date(2023, 1, 5)", loc={"delta": "span"},
           fixes=[["if inclusive:\n    delta += 1\n", "if inclusive:\n    delta += 1 if delta >= 0 else -1\n",
                   "reversed ranges"]],
           alt={"inclusive": "True"}),
        _f("dates", "is_weekend", "value, weekend=(5, 6) -> bool",
           "True for Saturday and Sunday.", "проверяет, выходной ли день",
           r'''
           return value.weekday() in weekend
           ''', ex="datetime.date(2023, 1, 7)",
           fixes=[["return value.weekday() in weekend", "return _coerce_date(value).weekday() in weekend",
                   "string dates"]],
           alt={"weekend": "(6,)"}),
        _f("dates", "parse_duration", "text: str -> int",
           "Parse durations like '1d 2h 30m' into seconds.", "переводит длительность вида «1d 2h» в секунды",
           r'''
           units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
           total = 0
           for amount, unit in re.findall(r"(\d+)\s*([dhms])", text.lower()):
               total += int(amount) * units[unit]
           return total
           ''', ex='"1d 2h"', imports=("re",), loc={"units": "seconds_per", "total": "seconds"},
           fixes=[["re.findall(r\"(\\d+)\\s*([dhms])\", text.lower())",
                   "re.findall(r\"(\\d+)\\s*([dhms])\\b\", text.lower())", "unit boundaries"]]),
        _f("text", "dedupe_words", "text: str -> str",
           "Drop repeated words, keeping the first.", "убирает повторяющиеся слова",
           r'''
           seen = set()
           out = []
           for word in text.split():
               key = word.lower()
               if key not in seen:
                   seen.add(key)
                   out.append(word)
           return " ".join(out)
           ''', ex='"a b a"', loc={"seen": "known", "out": "kept"},
           fixes=[["key = word.lower()", 'key = word.lower().strip(".,;:!?")', "punctuation"]]),
        _f("text", "mask_digits", 'text: str, keep: int = 4, mask: str = "*" -> str',
           "Mask all digits except the last *keep*.", "маскирует цифры, кроме последних",
           r'''
           digits = [i for i, ch in enumerate(text) if ch.isdigit()]
           hidden = set(digits[:-keep]) if keep else set(digits)
           return "".join(mask if i in hidden else ch for i, ch in enumerate(text))
           ''', ex='"1234 5678"', loc={"digits": "positions", "hidden": "masked"},
           fixes=[["if ch.isdigit()]", 'if ch.isdigit() and ch.isascii()]', "non-ASCII digits"]],
           alt={"keep": "2"}),
        _f("text", "initials", 'name: str, sep: str = "." -> str',
           "Initials of a full name.", "инициалы по полному имени",
           r'''
           parts = [p for p in name.replace("-", " ").split() if p]
           return "".join(p[0].upper() + sep for p in parts)
           ''', ex='"Anna Maria Petrova"', loc={"parts": "words"},
           fixes=[['name.replace("-", " ")', 'name.replace("-", " ").replace(".", " ")', "dotted names"]],
           alt={"sep": '""'}),
        _f("text", "pluralize_ru", "n: int, one: str, few: str, many: str -> str",
           "Russian plural form for *n*.", "русская форма множественного числа",
           r'''
           n = abs(n) % 100
           if 11 <= n <= 19:
               return many
           tail = n % 10
           if tail == 1:
               return one
           if 2 <= tail <= 4:
               return few
           return many
           ''', ex='5, "файл", "файла", "файлов"', loc={"tail": "last_digit"},
           fixes=[["n = abs(n) % 100", "n = abs(int(n)) % 100", "float counts"]]),
        _f("stats", "mode", "values",
           "Most frequent value.", "самое частое значение",
           r'''
           counts = {}
           for v in values:
               counts[v] = counts.get(v, 0) + 1
           if not counts:
               raise ValueError("mode of empty data")
           return max(counts, key=counts.get)
           ''', ex="[1, 2, 2]", loc={"counts": "freq"},
           fixes=[["for v in values:", "for v in (x for x in values if x is not None):", "None values"]]),
        _f("stats", "stdev", "values, sample: bool = True -> float",
           "Standard deviation.", "стандартное отклонение",
           r'''
           data = [float(v) for v in values]
           n = len(data)
           if n < 2:
               raise ValueError("need at least two values")
           avg = sum(data) / n
           ss = sum((x - avg) ** 2 for x in data)
           return math.sqrt(ss / (n - 1 if sample else n))
           ''', ex="[1, 2, 3, 4]", imports=("math",), loc={"data": "numbers", "avg": "center", "ss": "sq_sum"},
           fixes=[["data = [float(v) for v in values]", "data = [float(v) for v in values if v is not None]",
                   "None values"]],
           alt={"sample": "False"}),
        _f("stats", "zscores", "values -> list[float]",
           "Standard scores of the values.", "стандартизованные значения",
           r'''
           data = [float(v) for v in values]
           avg = sum(data) / len(data)
           spread = math.sqrt(sum((x - avg) ** 2 for x in data) / len(data))
           if spread == 0:
               return [0.0 for _ in data]
           return [(x - avg) / spread for x in data]
           ''', ex="[1, 2, 3]", imports=("math",), loc={"data": "numbers", "spread": "sigma"},
           fixes=[["avg = sum(data) / len(data)", "avg = sum(data) / len(data) if data else 0.0", "empty input"]]),
        _f("stats", "cumulative", "values, start=0 -> list",
           "Running totals.", "накопленные суммы",
           r'''
           total = start
           out = []
           for v in values:
               total += v
               out.append(total)
           return out
           ''', ex="[1, 2, 3]", loc={"total": "acc", "out": "sums"},
           fixes=[["for v in values:", "for v in (x for x in values if x is not None):", "None values"]]),
        _f("formatting", "to_tsv", "rows -> str",
           "Serialize rows as tab-separated text.", "сериализует строки через табуляцию",
           r'''
           lines = ["\t".join(str(c) for c in row) for row in rows]
           return "\n".join(lines)
           ''', ex="[[1, 2]]", loc={"lines": "out"},
           fixes=[['"\\t".join(str(c) for c in row)', '"\\t".join(str(c).replace("\\t", " ") for c in row)',
                   "tabs inside cells"]]),
    ]


MODULE_DOCS = {
    "parsing": "Parsing of delimited text.",
    "formatting": "Rendering tables as text, Markdown and HTML.",
    "numfmt": "Number formatting helpers.",
    "dates": "Date parsing and calendar helpers.",
    "text": "Text normalisation helpers.",
    "stats": "Small descriptive statistics over columns.",
    "_util": "Internal helpers shared by several modules. Not part of the public API.",
}

BASE_CONSTS = {
    "parsing": [],
    "formatting": [],
    "numfmt": [("_CURRENCY_SYMBOLS", '{"RUB": "₽", "USD": "$", "EUR": "€"}')],
    "dates": [("_DATE_FORMATS", '("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")'),
              ("_DATE_FORMATS_US", '("%Y-%m-%d", "%m/%d/%Y")')],
    "text": [("_TRANSLIT", '{"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", '
                           '"и": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", '
                           '"с": "s", "т": "t", "у": "u", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh"}')],
    "stats": [],
    "_util": [],
}

# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------


@dataclass
class Flag:
    name: str
    type: str  # "str" | "int"
    default: str  # source text
    help: str
    ru: str
    documented: bool
    alt: str | None = None
    rename: str | None = None


def _flags_initial() -> list[Flag]:
    return [
        Flag("sep", "str", '","', "field separator", "Разделитель полей во входных данных.", True, '";"', "delimiter"),
        Flag("width", "int", "80", "maximum output width",
             "Максимальная ширина строки вывода; более длинные строки обрезаются.", True, "120"),
        Flag("align", "str", '"left"', "cell alignment",
             "Выравнивание ячеек: `left`, `right` или `center`.", True, '"right"', "justify"),
        Flag("encoding", "str", '"utf-8"', "input encoding", "Кодировка входного файла.", True, '"utf-8-sig"'),
        Flag("format", "str", '"text"', "output format",
             "Формат вывода: `text`, `markdown` или `html`.", True, '"markdown"', "output-format"),
        Flag("skip", "int", "0", "number of leading lines to skip",
             "Сколько строк в начале файла пропустить (например, служебную шапку выгрузки).", True, "1"),
        Flag("debug-dump", "str", "None", "dump parsed rows to this path",
             "Сохраняет разобранные строки в указанный файл для отладки.", False),
        Flag("legacy-quotes", "int", "0", "quote handling compatibility level",
             "Уровень совместимости со старой обработкой кавычек (0 — выключено).", False, "1"),
    ]


def _flags_new() -> list[Flag]:
    return [
        Flag("decimal", "str", '"."', "decimal mark of numbers", "Десятичный разделитель в числах.", False, '","'),
        Flag("null-value", "str", '""', "text shown for empty cells", "Текст, которым показываются пустые ячейки.",
             False, '"-"'),
        Flag("max-rows", "int", "0", "print at most N rows (0 = all)", "Печатать не больше N строк; 0 — все.",
             False, "1000"),
        Flag("theme", "str", '"plain"', "table border theme", "Стиль рамки таблицы: `plain`, `grid` или `none`.",
             False, '"grid"', "border-style"),
        Flag("precision", "int", "2", "digits after the decimal point",
             "Сколько знаков после запятой печатать у дробных чисел.", False, "3"),
        Flag("date-style", "str", '"iso"', "how to print dates", "Как печатать даты: `iso`, `ru` или `us`.",
             False, '"ru"'),
        Flag("locale", "str", '"ru"', "locale for number formatting", "Локаль для форматирования чисел.",
             False, '"en"'),
        Flag("color", "str", '"auto"', "colorize output: auto, always, never",
             "Цветной вывод: `auto`, `always` или `never`.", False, '"never"'),
        Flag("tab-size", "int", "8", "tab stop width", "Ширина табуляции при раскрытии символов табуляции.",
             False, "4"),
        Flag("trim", "int", "1", "strip spaces around cells (1/0)", "Обрезать пробелы вокруг значений ячеек (1/0).",
             False, "0"),
        Flag("columns", "str", "None", "comma-separated list of columns to print",
             "Список столбцов через запятую, которые нужно напечатать.", False),
        Flag("quote", "str", "'\"'", "quote character", "Символ кавычки во входных данных.", False, '"\'"'),
    ]


# ---------------------------------------------------------------------------
# Source manipulation helpers
# ---------------------------------------------------------------------------


def _rename_names(src: str, old: str, new: str) -> str:
    """Rename identifier `old` → `new` outside strings/attributes/keyword arguments."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return re.sub(rf"(?<![\w.]){re.escape(old)}(?!\w)", new, src)
    spots = []
    for i, tok in enumerate(toks):
        if tok.type != tokenize.NAME or tok.string != old:
            continue
        prev = toks[i - 1] if i else None
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if prev is not None and prev.type == tokenize.OP and prev.string == ".":
            continue
        if (nxt is not None and nxt.type == tokenize.OP and nxt.string == "="
                and prev is not None and prev.type == tokenize.OP and prev.string in ("(", ",")):
            continue
        spots.append(tok.start)
    lines = src.split("\n")
    for row, col in sorted(spots, reverse=True):
        line = lines[row - 1]
        assert line[col:col + len(old)] == old, (line, col, old)
        lines[row - 1] = line[:col] + new + line[col + len(old):]
    return "\n".join(lines)


def _name_tokens(src: str) -> set[str]:
    try:
        return {t.string for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type == tokenize.NAME}
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return set(re.findall(r"\w+", src))


def _token_count(src: str, text: str) -> int:
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    return sum(1 for t in toks if t.string == text and t.type in (tokenize.NUMBER, tokenize.STRING))


def _render_sig(fn: Fn) -> str:
    parts = []
    for p in fn.params:
        if fn.annotated and p.ann:
            parts.append(f"{p.name}: {p.ann}" + (f" = {p.default}" if p.default is not None else ""))
        else:
            parts.append(p.name + (f"={p.default}" if p.default is not None else ""))
    ret = f" -> {fn.ret}" if fn.annotated and fn.ret else ""
    return f"def {fn.name}({', '.join(parts)}){ret}:"


def _render_fn(fn: Fn) -> str:
    out = [_render_sig(fn)]
    if fn.doc_extra:
        out.append(f'    """{fn.doc}')
        out.append("")
        for extra in fn.doc_extra:
            out.append(f"    {extra}")
        out.append('    """')
    else:
        out.append(f'    """{fn.doc}"""')
    for line in fn.body.rstrip("\n").split("\n"):
        out.append(("    " + line) if line else "")
    text = "\n".join(out) + "\n"
    for alias in fn.aliases:
        text += f"\n\n{alias} = {fn.name}  # old name, kept for compatibility\n"
    return text


# ---------------------------------------------------------------------------
# World model
# ---------------------------------------------------------------------------

TEST_VARIANTS = (
    '        """{name}: repeated calls give the same result."""\n'
    "        first = {call}\n        second = {call}\n"
    "        self.assertEqual(repr(first), repr(second))\n        self.assertIsNotNone(first)\n",
    '        """{name}: the example from docs/guide.md works."""\n'
    "        result = {call}\n        self.assertIsNotNone(result)\n"
    "        self.assertNotIsInstance(result, Exception)\n",
    '        """{name}: is callable and has a docstring."""\n'
    "        self.assertTrue(callable({qual}))\n        self.assertTrue({qual}.__doc__)\n"
    "        self.assertEqual({qual}.__name__, \"{name}\")\n",
    '        """{name}: result can be printed."""\n'
    "        result = {call}\n        text = repr(result)\n"
    "        self.assertIsInstance(text, str)\n        self.assertTrue(len(text) > 0)\n",
    '        """{name}: input objects are not modified."""\n'
    "        before = repr(({args},))\n        {qual}({args})\n"
    "        self.assertEqual(before, repr(({args},)))\n",
)

GUIDE_TEMPLATES = (
    "### {name}\n\nФункция `{mod}.{name}` {ru}. Результат не зависит от глобального состояния, поэтому "
    "функцию удобно использовать в конвейерах обработки выгрузок: прочитали файл, привели значения к "
    "единому виду, напечатали отчёт.\n\n```python\nfrom tablo import {mod}\n\nvalue = {mod}.{name}({ex})\n"
    "print(value)\n```\n\nЕсли входные данные приходят из внешней системы, сначала проверьте их "
    "кодировку и разделитель — большинство ошибок в отчётах возникает именно там, а не в самих функциях.\n",
    "### {name}\n\n`{name}` {ru}. Её часто вызывают сразу после чтения файла, когда нужно привести "
    "значения к единому виду перед сравнением или группировкой.\n\n```python\n>>> from tablo import {mod}\n"
    ">>> {mod}.{name}({ex})\n```\n\nФункция не печатает ничего сама и не пишет в файлы; все побочные "
    "эффекты остаются на стороне вызывающего кода. Для больших выгрузок вызывайте её построчно, а не "
    "накапливайте весь файл в памяти.\n",
    "### Приём: {name}\n\nЕсли вам нужно, чтобы программа {ru_short}, используйте `{mod}.{name}`. "
    "Типичный вызов — `{name}({ex})`. В отчётах бухгалтерии и склада это встречается постоянно: "
    "выгрузки из разных систем оформлены по-разному, и без нормализации итоговые таблицы не сходятся.\n\n"
    "Совет: оберните вызов в небольшую функцию своего проекта, чтобы в одном месте задать параметры, "
    "принятые у вас по умолчанию, и не повторять их в каждом скрипте.\n",
)

COMMENTS = (
    "# keep the original order of the input",
    "# fast path for the common case",
    "# NOTE: this mirrors the behaviour of the old csv-based implementation",
    "# see issue #{n} for the discussion",
    "# the loop is intentionally simple, profiling showed no hot spot here",
    "# TODO: locale-aware handling (issue #{n})",
    "# values are already validated by the caller in most code paths",
)

DOC_EXTRAS = (
    "The input is never modified.",
    "See docs/guide.md for examples.",
    "This function is pure and thread-safe.",
    "Raises ValueError on malformed input where noted.",
    "Added in the early days of the project; behaviour is covered by tests.",
)

OPT_PARAMS = (
    ("strict", "False", "bool", 'if strict and not {p0}:\n    raise ValueError("{fname}: empty input")\n'),
    ("errors", '"strict"', "str",
     'if errors not in ("strict", "ignore"):\n    raise ValueError("errors must be \'strict\' or \'ignore\'")\n'),
    ("verbose", "False", "bool", 'if verbose:\n    print("{fname}:", repr({p0})[:60])\n'),
    ("on_empty", "None", "object", "if on_empty is not None and not {p0}:\n    return on_empty\n"),
)
REQ_PARAMS = (
    ("mode", None, "str", 'if mode not in ("fast", "safe"):\n    raise ValueError(f"unknown mode: {mode}")\n'),
    ("context", None, "dict", 'if not isinstance(context, dict):\n    raise TypeError("context must be a dict")\n'),
)


class World:
    def __init__(self, R) -> None:
        self.R = R
        self.fns: dict[str, Fn] = {}
        self.order: dict[str, list[str]] = {m: [] for m in (*MODULES, "_util")}
        self.consts = {m: list(v) for m, v in BASE_CONSTS.items()}
        self.exported: list[str] = []
        self.init_extra: list[str] = []
        self.flags = _flags_initial()
        self.new_flags = _flags_new()
        R.shuffle(self.new_flags)
        self.new_fns = _bank_new()
        R.shuffle(self.new_fns)
        self.tests: list[tuple[str, int, int]] = []  # (fn name, variant, serial)
        self.test_serial = 0
        self.guide: list[str] = []
        self.readme_notes: list[str] = []
        self.ci_versions = ["3.10", "3.11"]
        self.ci_steps: list[str] = []
        self.used_names: set[str] = set()
        self.locked: set[str] = set()
        for fn in _bank_public():
            self._add(fn, export=True)
        for fn in _bank_unexported():
            self._add(fn, export=False)
        self.init_extra.append("chunked")
        for fn in _bank_private():
            self._add(fn, export=False)
        for name in list(self.exported):
            if R.random() < 0.45:
                self.add_test(name)
        for name in R.sample(self.exported, 9):
            self.add_guide(name)
        for name in R.sample(self.exported, 12):
            self.fns[name].annotated = True

    # -- basic mutation -----------------------------------------------------
    def _add(self, fn: Fn, *, export: bool) -> None:
        assert fn.name not in self.fns
        self.fns[fn.name] = fn
        self.used_names.add(fn.name)
        self.order[fn.module].append(fn.name)
        if export:
            self.exported.append(fn.name)

    def add_test(self, name: str) -> None:
        self.test_serial += 1
        self.tests.append((name, self.R.randrange(len(TEST_VARIANTS)), self.test_serial))

    def add_guide(self, name: str) -> None:
        fn = self.fns[name]
        tpl = self.R.choice(GUIDE_TEMPLATES)
        self.guide.append(tpl.format(name=fn.name, mod=fn.module, ru=fn.ru, ex=fn.ex,
                                     ru_short=fn.ru.split(",")[0]))

    def public(self) -> list[Fn]:
        return [self.fns[n] for n in self.exported if n in self.fns]

    def alias_targets(self) -> set[str]:
        return {fn.name for fn in self.fns.values() if fn.aliases}

    def plain_public(self) -> list[Fn]:
        return [f for f in self.public() if not f.aliases and f.name not in self.locked]

    def transform(self, fn: Fn, func) -> None:
        """Apply a text transform to the body and every tracked fragment."""
        fn.body = func(fn.body)
        for fx in fn.fixes:
            fx[0] = func(fx[0])
            fx[1] = func(fx[1])
        for k in list(fn.snippets):
            fn.snippets[k] = func(fn.snippets[k])
        for rec in fn.applied:
            rec["old"] = func(rec["old"]) if rec["old"] else ""
            rec["new"] = func(rec["new"])

    # -- rendering ------------------------------------------------------------
    def render(self) -> dict[str, str]:
        files: dict[str, str] = {}
        for mod in (*MODULES, "_util"):
            files[f"{PKG}/{mod}.py"] = self._render_module(mod)
        files[f"{PKG}/__init__.py"] = self._render_init()
        files[f"{PKG}/cli.py"] = self._render_cli()
        files[f"{PKG}/__main__.py"] = 'from .cli import main\n\nraise SystemExit(main())\n'
        files["docs/cli.md"] = self._render_cli_docs()
        files["docs/guide.md"] = self._render_guide()
        files["README.md"] = self._render_readme()
        files["CONTRIBUTING.md"] = CONTRIBUTING
        files["pyproject.toml"] = PYPROJECT
        files[".github/workflows/ci.yml"] = self._render_ci()
        files.update(self._render_tests())
        return files

    def _render_module(self, mod: str) -> str:
        fns = [self.fns[n] for n in self.order[mod]]
        imports = sorted({imp for fn in fns for imp in fn.imports})
        out = [f'"""{MODULE_DOCS[mod]}"""\n']
        if imports:
            out.append("\n".join(f"import {i}" for i in imports) + "\n")
        if self.consts[mod]:
            out.append("\n".join(f"{name} = {val}" for name, val in self.consts[mod]) + "\n")
        text = "\n".join(out)
        for fn in fns:
            text += "\n\n" + _render_fn(fn)
        return text

    def _render_init(self) -> str:
        public_names = set(self.exported)
        lines = ['"""tablo — small helpers for parsing and printing delimited tables."""', ""]
        for mod in (*MODULES, "_util"):
            names = []
            for n in self.order[mod]:
                fn = self.fns[n]
                if n in public_names or n in self.init_extra:
                    names.append(n)
                names.extend(a for a in fn.aliases if a in public_names)
            if names:
                lines.append(f"from .{mod} import {', '.join(sorted(names))}")
        lines.append("")
        lines.append("__all__ = [")
        lines.extend(f'    "{n}",' for n in self.exported)
        lines.append("]")
        return "\n".join(lines) + "\n"

    def _render_cli(self) -> str:
        args = []
        for fl in self.flags:
            typ = ", type=int" if fl.type == "int" else ""
            args.append(f'    parser.add_argument("--{fl.name}"{typ}, default={fl.default}, help="{fl.help}")')
        return CLI_TEMPLATE.replace("@@ARGS@@", "\n".join(args))

    def _render_cli_docs(self) -> str:
        parts = [CLI_DOC_INTRO]
        for fl in self.flags:
            if not fl.documented:
                continue
            dflt = "не задано" if fl.default == "None" else f"`{fl.default}`"
            kind = "целое число" if fl.type == "int" else "строка"
            parts.append(f"### `--{fl.name}`\n\n{fl.ru} Значение: {kind}. По умолчанию: {dflt}.\n")
        parts.append(CLI_DOC_OUTRO)
        return "\n".join(parts)

    def _render_guide(self) -> str:
        return GUIDE_INTRO + "\n" + "\n".join(self.guide)

    def _render_readme(self) -> str:
        return README_BASE + ("\n## Notes\n\n" + "\n".join(f"- {n}" for n in self.readme_notes) + "\n"
                              if self.readme_notes else "")

    def _render_ci(self) -> str:
        vers = ", ".join(f'"{v}"' for v in self.ci_versions)
        steps = "".join(f"      - run: {s}\n" for s in self.ci_steps)
        return CI_TEMPLATE.replace("@@VERS@@", vers).replace("@@STEPS@@", steps)

    def _render_tests(self) -> dict[str, str]:
        by_mod: dict[str, list[str]] = {}
        for name, variant, serial in self.tests:
            fn = self.fns.get(name)
            if fn is None:
                continue
            qual = f"{fn.module}.{fn.name}"
            call = f"{qual}({fn.ex})"
            if variant == 4 and re.search(r"\w=", fn.ex):
                variant = 0
            text = f"    def test_{fn.name}_{serial}(self):\n" + TEST_VARIANTS[variant].format(
                call=call, qual=qual, name=fn.name, args=fn.ex)
            by_mod.setdefault(fn.module, []).append(text)
        files = {}
        for mod in (*MODULES, "_util"):
            cases = by_mod.get(mod)
            if not cases:
                continue
            cls = "".join(p.capitalize() for p in mod.strip("_").split("_")) + "Tests"
            head = (f"import datetime  # noqa: F401\nimport unittest\n\nfrom {PKG} import {mod}\n\n\n"
                    f"class {cls}(unittest.TestCase):\n")
            files[f"tests/test_{mod.strip('_')}.py"] = head + "\n".join(cases) + (
                "\n\nif __name__ == \"__main__\":\n    unittest.main()\n")
        return files


# ---------------------------------------------------------------------------
# Operations: each returns (kind, fields) and mutates the world
# ---------------------------------------------------------------------------

CAT = {
    "B": "breaking", "F": "feature", "X": "fix", "I": "internal",
}


GUARDS = (
    ("none", 'if {p0} is None:\n    raise TypeError("{fname}(): {p0} must not be None")\n',
     "None input", "None вместо данных"),
    ("bytes", 'if isinstance({p0}, bytes):\n    {p0} = {p0}.decode("utf-8")\n',
     "bytes input", "байтовые строки на входе"),
    ("blank", 'if isinstance({p0}, str) and not {p0}.strip():\n    raise ValueError("{fname}(): blank input")\n',
     "blank input", "строка из одних пробелов"),
    ("nbsp", 'if isinstance({p0}, str):\n    {p0} = {p0}.replace("\\u00a0", " ")\n',
     "non-breaking spaces", "неразрывные пробелы во входной строке"),
    ("bom", 'if isinstance({p0}, str):\n    {p0} = {p0}.lstrip("\\ufeff")\n',
     "BOM at start", "BOM в начале строки"),
    ("tuple", 'if isinstance({p0}, tuple):\n    {p0} = list({p0})\n',
     "tuple input", "кортеж вместо списка"),
    ("iter", 'if hasattr({p0}, "__next__"):\n    {p0} = list({p0})\n',
     "iterator input", "итератор вместо списка"),
)


class Ops:
    def __init__(self, world: World) -> None:
        self.w = world
        self.R = world.R
        self.queues: dict[str, list[str]] = {}
        self.last: tuple[Fn, dict] | None = None

    def _pick(self, items):
        items = list(items)
        return self.R.choice(items) if items else None

    def _p0(self, fn: Fn) -> str:
        return fn.params[0].name

    def _free(self, fn: Fn, name: str) -> bool:
        return name not in _name_tokens(fn.body) and all(p.name != name for p in fn.params)

    # ---- internal ----
    def _private_like(self, underscore: bool):
        exp = set(self.w.exported)
        return [f for f in self.w.fns.values()
                if f.name not in exp and f.name.startswith("_") == underscore and f.name not in self.w.locked]

    # ---- behaviour changes (category decided in the thread) ----
    def _change_body(self, fn: Fn) -> dict | None:
        """Change executable code of `fn`: a bank fix or a generic input guard."""
        fixes = [fx for fx in fn.fixes if fn.body.count(fx[0]) == 1]
        guards = []
        if fn.params:
            p0 = self._p0(fn)
            for key, tpl, en, ru in GUARDS:
                text = tpl.replace("{p0}", p0).replace("{fname}", fn.name)
                if key not in fn.guards and text not in fn.body:
                    guards.append((key, text, en, ru))
        if fixes and (not guards or self.R.random() < 0.6):
            fx = self.R.choice(fixes)
            fn.body = fn.body.replace(fx[0], fx[1])
            fn.fixes.remove(fx)
            rec = {"old": fx[0], "new": fx[1], "what": fx[2], "what_ru": fx[2]}
        elif guards:
            key, text, en, ru = self.R.choice(guards)
            fn.body = text + fn.body
            fn.guards.add(key)
            rec = {"old": "", "new": text, "what": en, "what_ru": ru}
        else:
            return None
        fn.applied.append(rec)
        return rec

    def _revert_body(self, fn: Fn, rec: dict) -> bool:
        if fn.body.count(rec["new"]) != 1:
            return False
        fn.body = fn.body.replace(rec["new"], rec["old"], 1)
        fn.applied.remove(rec)
        if rec["old"]:
            fn.fixes.append([rec["old"], rec["new"], rec["what"]])
        return True

    def X_change(self, fn: Fn | None = None):
        pool = [fn] if fn is not None else self.w.plain_public()
        self.R.shuffle(pool)
        for cand in pool:
            rec = self._change_body(cand)
            if rec is not None:
                self.last = (cand, rec)
                return {"name": cand.name, "module": cand.module, "what": rec["what"]}
        return None

    def P_change(self):
        pool = self._private_like(True) + self._private_like(False)
        self.R.shuffle(pool)
        for cand in pool:
            rec = self._change_body(cand)
            if rec is not None:
                self.last = (cand, rec)
                return {"name": cand.name, "module": cand.module, "what": rec["what"]}
        return None

    def X_revert(self, fn: Fn, rec: dict):
        if fn.name not in self.w.fns or not self._revert_body(fn, rec):
            return None
        self.last = (fn, rec)
        return {"name": fn.name, "module": fn.module, "what": rec["what"]}

    def I_rename_local(self, pool=None):
        pool = pool if pool is not None else self.w.plain_public()
        cands = [(f, o, n) for f in pool for o, n in f.locals.items()
                 if o in _name_tokens(f.body) and self._free(f, n)]
        if not cands:
            return None
        fn, old, new = self._pick(cands)
        self.w.transform(fn, lambda s: _rename_names(s, old, new))
        del fn.locals[old]
        return {"name": fn.name, "module": fn.module, "old": old, "new": new}

    def I_private_rename(self):
        return self.I_rename_local(self._private_like(True) + self._private_like(False))

    def I_extract_literal(self):
        cands = []
        for f in self.w.plain_public():
            for lit, const in f.lits:
                if _token_count(f.body, lit) == 1 and all(c != const for c, _ in self.w.consts[f.module]):
                    cands.append((f, lit, const))
        if not cands:
            return None
        fn, lit, const = self._pick(cands)
        toks = list(tokenize.generate_tokens(io.StringIO(fn.body).readline))
        tok = next(t for t in toks if t.string == lit and t.type in (tokenize.NUMBER, tokenize.STRING))
        lines = fn.body.split("\n")
        row, col = tok.start
        lines[row - 1] = lines[row - 1][:col] + const + lines[row - 1][col + len(lit):]
        fn.body = "\n".join(lines)
        fn.lits = [x for x in fn.lits if x[0] != lit]
        self.w.consts[fn.module].append((const, lit))
        return {"name": fn.name, "module": fn.module, "const": const}

    def I_comment(self):
        fn = self._pick(self.w.plain_public())
        lines = fn.body.rstrip("\n").split("\n")
        idx = self.R.randrange(len(lines))
        indent = re.match(r"\s*", lines[idx]).group(0)
        comment = self.R.choice(COMMENTS).format(n=self.R.randint(12, 240))
        lines.insert(idx, indent + comment)
        fn.body = "\n".join(lines) + "\n"
        return {"name": fn.name, "module": fn.module}

    def I_annotate(self):
        fn = self._pick(f for f in self.w.plain_public() if not f.annotated and any(p.ann for p in f.params))
        if fn is None:
            return None
        fn.annotated = True
        return {"name": fn.name, "module": fn.module}

    def I_docstring(self):
        fn = self._pick(f for f in self.w.plain_public() if len(f.doc_extra) < 2)
        extra = self.R.choice([d for d in DOC_EXTRAS if d not in fn.doc_extra])
        fn.doc_extra.append(extra)
        return {"name": fn.name, "module": fn.module}

    def I_move(self):
        cands = [f for f in self.w.plain_public()
                 if not re.search(r"(?<![\w.])_[A-Za-z]", f.body) and not f.aliases]
        fn = self._pick(cands)
        if fn is None:
            return None
        old = fn.module
        target = self.R.choice([m for m in MODULES if m != old])
        self.w.order[old].remove(fn.name)
        self.w.order[target].append(fn.name)
        fn.module = target
        return {"name": fn.name, "module": target, "old": old}

    def I_tests(self):
        names = self.R.sample(sorted(self.w.fns), 4)
        for n in names:
            self.w.add_test(n)
        return {"name": names[0], "module": self.w.fns[names[0]].module}

    def I_docs(self):
        fn = self._pick(self.w.public())
        self.w.add_guide(fn.name)
        self.w.add_guide(self._pick(self.w.public()).name)
        return {"name": fn.name, "module": fn.module}

    def I_ci(self):
        choices = []
        for v in ("3.12", "3.13"):
            if v not in self.w.ci_versions:
                choices.append(("ver", v))
        for s in ("python -m unittest discover -s tests -v", "python -m compileall -q tablo",
                  "python -m tablo --help", "pip install ruff && ruff check tablo"):
            if s not in self.w.ci_steps:
                choices.append(("step", s))
        if len(self.w.ci_versions) > 2:
            choices.append(("drop", self.w.ci_versions[0]))
        kind, val = self._pick(choices)
        if kind == "ver":
            self.w.ci_versions.append(val)
        elif kind == "step":
            self.w.ci_steps.append(val)
        else:
            self.w.ci_versions.remove(val)
        return {"ver": val if kind != "step" else "3.x", "module": "ci", "name": "ci"}

    def I_readme(self):
        fn = self._pick(self.w.public())
        note = self.R.choice((
            f"`{fn.name}` {fn.ru}.",
            f"Для задачи «{fn.ru}» используйте `{PKG}.{fn.name}`.",
            f"Пример: `{PKG}.{fn.name}({fn.ex})`.",
        ))
        self.w.readme_notes.append(note)
        return {"name": fn.name, "module": fn.module}

    def _take_new_fn(self):
        return self.w.new_fns.pop() if self.w.new_fns else None

    def I_add_unexported(self):
        fn = self._take_new_fn()
        if fn is None:
            return None
        self.w._add(fn, export=False)
        if self.R.random() < 0.5:
            self.w.init_extra.append(fn.name)
        if self.R.random() < 0.6:
            self.w.add_test(fn.name)
        return {"name": fn.name, "module": fn.module}

    def I_flag_hidden_add(self):
        if not self.w.new_flags:
            return None
        fl = self.w.new_flags.pop()
        fl.documented = False
        self.w.flags.append(fl)
        return {"flag": fl.name, "module": "cli", "name": "cli"}

    def I_flag_hidden_default(self):
        fl = self._pick(f for f in self.w.flags if not f.documented and f.alt)
        if fl is None:
            return None
        fl.default, fl.alt = fl.alt, None
        return {"flag": fl.name, "new": fl.default, "module": "cli", "name": "cli"}

    # ---- features ----
    def F_new_func(self):
        fn = self._take_new_fn()
        if fn is None:
            return None
        self.w._add(fn, export=True)
        self.w.add_test(fn.name)
        if self.R.random() < 0.5:
            self.w.add_guide(fn.name)
        return {"name": fn.name, "module": fn.module}

    def F_export_existing(self):
        fn = self._pick(f for f in self._private_like(False) if f.module != "_util")
        if fn is None:
            return None
        self.w.exported.append(fn.name)
        if fn.name in self.w.init_extra:
            self.w.init_extra.remove(fn.name)
        return {"name": fn.name, "module": fn.module}

    def F_add_optional(self):
        cands = [(f, p) for f in self.w.plain_public() for p in OPT_PARAMS
                 if f.params and self._free(f, p[0]) and len(f.snippets) < 2]
        if not cands:
            return None
        fn, (pname, dflt, ann, snip) = self._pick(cands)
        text = snip.replace("{p0}", self._p0(fn)).replace("{fname}", fn.name)
        fn.params.append(Param(pname, dflt, ann))
        fn.body = text + fn.body
        fn.snippets[pname] = text
        if self.R.random() < 0.5:
            self.w.add_test(fn.name)
        return {"name": fn.name, "module": fn.module, "param": pname}

    def F_rename_alias(self):
        cands = [f for f in self.w.plain_public() if f.renames.get("__fn__") or f.name in FN_RENAMES]
        fn = self._pick(c for c in cands if FN_RENAMES.get(c.name) not in self.w.used_names)
        if fn is None:
            return None
        old, new = fn.name, FN_RENAMES[fn.name]
        self._rename_fn(fn, new)
        fn.aliases.append(old)
        idx = self.w.exported.index(new)
        self.w.exported.insert(idx + 1, old)
        return {"name": old, "new": new, "module": fn.module}

    def F_flag_add(self):
        if not self.w.new_flags:
            return None
        fl = self.w.new_flags.pop()
        fl.documented = True
        self.w.flags.append(fl)
        return {"flag": fl.name, "module": "cli", "name": "cli"}

    def F_flag_document(self):
        fl = self._pick(f for f in self.w.flags if not f.documented)
        if fl is None:
            return None
        fl.documented = True
        return {"flag": fl.name, "module": "cli", "name": "cli"}

    # ---- breaking ----
    def _rename_fn(self, fn: Fn, new: str) -> None:
        old = fn.name
        del self.w.fns[old]
        fn.name = new
        self.w.fns[new] = fn
        self.w.used_names.add(new)
        lst = self.w.order[fn.module]
        lst[lst.index(old)] = new
        if old in self.w.exported:
            self.w.exported[self.w.exported.index(old)] = new
        self.w.tests = [(new if n == old else n, v, s) for n, v, s in self.w.tests]

    def B_remove_func(self):
        fn = self._pick(self.w.plain_public())
        del self.w.fns[fn.name]
        self.w.order[fn.module].remove(fn.name)
        self.w.exported.remove(fn.name)
        return {"name": fn.name, "module": fn.module}

    def B_unexport(self):
        fn = self._pick(self.w.plain_public())
        self.w.exported.remove(fn.name)
        if self.R.random() < 0.5:
            self.w.init_extra.append(fn.name)
        return {"name": fn.name, "module": fn.module}

    def B_rename_func(self):
        fn = self._pick(f for f in self.w.plain_public()
                        if f.name in FN_RENAMES and FN_RENAMES[f.name] not in self.w.used_names)
        if fn is None:
            return None
        old = fn.name
        self._rename_fn(fn, FN_RENAMES[old])
        return {"name": old, "new": fn.name, "module": fn.module}

    def B_remove_alias(self):
        fn = self._pick(f for f in self.w.public() if f.aliases)
        if fn is None:
            return None
        alias = fn.aliases.pop()
        self.w.exported.remove(alias)
        return {"name": alias, "new": fn.name, "module": fn.module}

    def B_remove_param(self):
        cands = [(f, p) for f in self.w.plain_public() for p in f.snippets if f.body.count(f.snippets[p]) == 1]
        if not cands:
            return None
        fn, pname = self._pick(cands)
        fn.body = fn.body.replace(fn.snippets.pop(pname), "")
        fn.params = [p for p in fn.params if p.name != pname]
        return {"name": fn.name, "module": fn.module, "param": pname}

    def B_rename_param(self):
        cands = [(f, o, n) for f in self.w.plain_public() for o, n in f.renames.items()
                 if any(p.name == o for p in f.params) and self._free(f, n)]
        if not cands:
            return None
        fn, old, new = self._pick(cands)
        self.w.transform(fn, lambda s: _rename_names(s, old, new))
        for p in fn.params:
            if p.name == old:
                p.name = new
        del fn.renames[old]
        fn.ex = re.sub(rf"\b{old}=", f"{new}=", fn.ex)
        return {"name": fn.name, "module": fn.module, "param": old, "new": new}

    def B_change_default(self):
        cands = [(f, p) for f in self.w.plain_public() for p in f.params if p.name in f.alts]
        if not cands:
            return None
        fn, p = self._pick(cands)
        old = p.default
        p.default = fn.alts.pop(p.name)
        return {"name": fn.name, "module": fn.module, "param": p.name, "new": p.default, "old": old}

    def B_reorder(self):
        cands = []
        for f in self.w.plain_public():
            for i in range(len(f.params) - 1):
                a, b = f.params[i], f.params[i + 1]
                if a.default is not None and b.default is not None:
                    cands.append((f, i))
        if not cands:
            return None
        fn, i = self._pick(cands)
        fn.params[i], fn.params[i + 1] = fn.params[i + 1], fn.params[i]
        return {"name": fn.name, "module": fn.module, "param": fn.params[i + 1].name,
                "other": fn.params[i].name}

    def B_add_required(self):
        cands = [(f, p) for f in self.w.plain_public() for p in REQ_PARAMS if self._free(f, p[0]) and f.params]
        if not cands:
            return None
        fn, (pname, _, ann, snip) = self._pick(cands)
        pos = max(i for i, p in enumerate(fn.params) if p.default is None) + 1 if any(
            p.default is None for p in fn.params) else 0
        fn.params.insert(pos, Param(pname, None, ann))
        fn.body = snip + fn.body
        return {"name": fn.name, "module": fn.module, "param": pname}

    def B_flag_remove(self):
        fl = self._pick(f for f in self.w.flags if f.documented)
        self.w.flags.remove(fl)
        return {"flag": fl.name, "module": "cli", "name": "cli"}

    def B_flag_rename(self):
        fl = self._pick(f for f in self.w.flags if f.documented and f.rename)
        if fl is None:
            return None
        old = fl.name
        fl.name, fl.rename = fl.rename, None
        return {"flag": old, "new": fl.name, "module": "cli", "name": "cli"}

    def B_flag_default(self):
        fl = self._pick(f for f in self.w.flags if f.documented and f.alt)
        if fl is None:
            return None
        old = fl.default
        fl.default, fl.alt = fl.alt, None
        return {"flag": fl.name, "new": fl.default, "old": old, "module": "cli", "name": "cli"}

    def B_flag_undocument(self):
        fl = self._pick(f for f in self.w.flags if f.documented)
        fl.documented = False
        return {"flag": fl.name, "module": "cli", "name": "cli"}


FN_RENAMES = {
    "split_line": "split_fields", "parse_table": "read_table", "sniff_separator": "detect_separator",
    "truncate_cell": "shorten", "render_table": "format_table", "human_size": "format_size",
    "iso_week_label": "week_label", "normalize_spaces": "squash_spaces", "moving_average": "rolling_mean",
    "summarize": "describe", "quarter_of": "quarter", "count_words": "word_count", "pad_cell": "pad",
}

WEIGHTS = {
    "B": {"B_remove_func": 2, "B_unexport": 2, "B_rename_func": 2, "B_remove_alias": 2, "B_remove_param": 2,
          "B_rename_param": 2, "B_reorder": 1, "B_add_required": 1, "B_flag_remove": 1,
          "B_flag_rename": 1, "B_flag_undocument": 1},
    "F": {"F_new_func": 4, "F_export_existing": 2, "F_add_optional": 4, "F_rename_alias": 2, "F_flag_add": 2,
          "F_flag_document": 2},
    "I": {"I_tests": 12, "I_docs": 9, "I_ci": 3, "I_readme": 3, "I_private_rename": 3,
          "I_add_unexported": 4, "I_flag_hidden_add": 3, "I_flag_hidden_default": 2,
          "I_rename_local": 10, "I_comment": 5, "I_annotate": 6, "I_docstring": 5, "I_extract_literal": 5,
          "I_move": 3},
}

# ---------------------------------------------------------------------------
# Commit messages
# ---------------------------------------------------------------------------

HONEST = {
    "B_remove_func": ["Remove {name}()", "Drop {name}", "Delete deprecated {name}()"],
    "B_unexport": ["Stop exporting {name} from the package", "Make {name} internal", "Remove {name} from __all__"],
    "B_rename_func": ["Rename {name} to {new}", "{name} -> {new}"],
    "B_remove_alias": ["Drop the {name} alias", "Remove deprecated alias {name}"],
    "B_remove_param": ["Remove the {param} parameter from {name}()", "{name}: drop {param}"],
    "B_rename_param": ["Rename {name}() parameter {param} to {new}", "{name}: {param} -> {new}"],
    "B_change_default": ["Change default {param} of {name}()", "{name}: new default for {param}"],
    "B_reorder": ["Reorder parameters of {name}()", "{name}: swap {param} and {other}"],
    "B_add_required": ["{name}() now requires {param}", "Make {param} mandatory in {name}"],
    "B_flag_remove": ["Remove --{flag} option", "CLI: drop --{flag}"],
    "B_flag_rename": ["Rename --{flag} to --{new}", "CLI: --{flag} is now --{new}"],
    "B_flag_default": ["CLI: change default of --{flag}", "Use {new} as default for --{flag}"],
    "B_flag_undocument": ["docs: remove --{flag} section", "Stop documenting --{flag}"],
    "F_new_func": ["Add {name}()", "New helper {name}", "Introduce {name}"],
    "F_export_existing": ["Export {name} from the package", "Make {name} public"],
    "F_add_optional": ["{name}: add {param} option", "Add {param} parameter to {name}()"],
    "F_rename_alias": ["Rename {name} to {new}, keep old name as alias", "Add {new} (alias of {name})"],
    "F_flag_add": ["Add --{flag} option", "CLI: new --{flag} flag"],
    "F_flag_document": ["docs: describe --{flag}", "Document --{flag}"],
    "X_hook": ["Fix {name}: {what}", "{name}: handle {what}", "Fix bug in {name}", "{name}: {what}",
               "Change {name} behaviour for {what}"],
    "RV_revert": ["Undo {name} change", "{name}: go back to the old behaviour", "Revert {name} tweak"],
    "RO_revert": ["Restore old {name} behaviour", "{name}: return previous behaviour", "Revert {name} change"],
    "I_tests": ["Add tests for {name}", "tests: cover {name}", "More tests for {module}"],
    "I_docs": ["docs: describe {name}", "Update guide", "docs: examples for {name}"],
    "I_ci": ["ci: test on Python {ver}", "CI tweaks", "ci: update workflow"],
    "I_readme": ["Update README", "README: usage notes"],
    "I_private_fix": ["Fix {name}: {what}", "fix {name} ({what})", "{name}: handle {what}"],
    "I_unexported_fix": ["Fix {name}: {what}", "{name}: handle {what}"],
    "I_rename_local": ["Rename locals in {name}", "{name}: clearer variable names", "Refactor {name}"],
    "I_private_rename": ["Rename locals in {name}", "cleanup {name}"],
    "I_add_unexported": ["Add {name}()", "New {name} helper"],
    "I_flag_hidden_add": ["Add --{flag} option", "CLI: add --{flag}"],
    "I_flag_hidden_default": ["Change default of --{flag}", "--{flag} defaults to {new} now"],
    "I_comment": ["Comment {name}", "{name}: explain the code"],
    "I_annotate": ["Type hints for {name}", "Annotate {name}()", "Change {name} signature to typed version"],
    "I_docstring": ["Improve {name} docstring", "docs: {name} docstring"],
    "I_extract_literal": ["Extract constant in {name}", "{name}: move magic value to {const}"],
    "I_move": ["Move {name} to {module}", "Reorganize {old} and {module}"],
}

MISLEAD = {
    "breaking": ["small fix", "Minor cleanup", "Tidy up {module}", "refactor {module}", "fix typo", "мелкие правки",
                 "Polish {name}", "wip", "cleanup before release", "Simplify {name}",
                 "Refactor {module}\n\nNo functional changes intended."],
    "feature": ["cleanup", "Refactor {module}", "tidy up __init__", "misc", "update docs", "small tweaks in {module}",
                "style", "правки по ревью"],
    "fix": ["Refactor {name}", "cleanup {module}", "Simplify {name}", "style", "Rename things in {name}",
            "tests", "Tidy up {name}\n\nPure refactoring."],
    "internal": ["Fix {name}", "Fix edge case in {name}", "BREAKING: rework {name}", "Remove legacy code from {name}",
                 "Add {name} improvements", "{name}: new behaviour for empty input", "Fix {module} bug"],
}
MISLEAD_P = {"breaking": 0.55, "feature": 0.45, "fix": 0.35, "internal": 0.22}

AUTHORS = (
    "Anna Petrova <anna.petrova@tablo.dev>", "Dmitry Sokolov <d.sokolov@tablo.dev>",
    "Igor Lebedev <igor@lebedev.me>", "Maria Kuznetsova <mkuz@tablo.dev>",
    "Pavel Orlov <porlov@users.noreply.github.com>",
)

# Release plan: slot counts per release; "I" (neutral internal work) fills the rest up to
# PER_RELEASE.  Slot codes: B/F — API change decided by the diff; X? — behaviour change of a
# public function decided in the thread (f bug, b intended, e extension, s/o regression from a
# commit of the same / an older release); P? — change of a non-public function (i not visible
# outside, f/b/e as for X); D? — changed default (f bug, b intended); RV — change reverted later
# in the same release (two commits).
PER_RELEASE = 34
PLAN = [
    {"F": 2, "Xf": 4, "Xe": 2, "Xs": 2, "Xo": 1, "Pi": 2, "Pf": 3, "Pe": 1, "Df": 1, "RV": 1},
    {"Xf": 4, "Xo": 2, "Xs": 3, "Pi": 2, "Pf": 3, "Df": 2, "RV": 1},
    {"B": 5, "F": 1, "Xf": 4, "Xb": 2, "Xe": 1, "Xs": 2, "Xo": 1, "Pi": 2, "Pf": 2, "Pb": 1, "Db": 1, "Df": 1},
    {"Xf": 4, "Xo": 1, "Xs": 3, "Pi": 2, "Pf": 3, "Df": 1, "RV": 1},
    {"Xf": 4, "Xe": 2, "Pe": 1, "Xs": 2, "Xo": 1, "Pi": 2, "Pf": 2, "RV": 1},
    {"B": 4, "F": 3, "Xf": 4, "Xb": 1, "Xs": 2, "Xo": 1, "Pi": 2, "Pf": 2, "Pb": 1, "Db": 1, "RV": 1},
    {"F": 2, "Xb": 1, "Pb": 1, "Xe": 1, "Xf": 4, "Xs": 2, "Xo": 1, "Pi": 2, "Pf": 2},
]
EXPECTED_VERSIONS = ["1.0.0", "1.1.0", "1.1.1", "2.0.0", "2.1.0", "2.2.0", "3.0.0", "4.0.0"]
UNRELEASED = {"B": 1, "Xf": 1, "I": 4}
EARLY = 11
DELAYED = 3  # release 5 (index 3 in PLAN) was built three commits after the planned point
SLOT_DEC = {"f": "bug", "b": "intended", "e": "extension", "i": "hidden", "s": "regression", "o": "regression"}
DEC_CAT = {"bug": "fix", "intended": "breaking", "extension": "feature", "hidden": "internal",
           "revert": "internal"}
TOUCH_KINDS = {"X_change", "RO_revert", "F_new_func", "F_add_optional", "I_rename_local",
               "I_extract_literal", "B_change_default"}


@dataclass
class Commit:
    kind: str
    group: str  # INIT B F I X P D RV
    subject: str
    message: str
    author: str
    ts: int
    files: dict[str, str]
    fields: dict
    thread: int
    rel: int  # release the commit was generated for: 1 early, 2..8, 9 unreleased
    dec: str | None = None  # final decision of the maintainers (behaviour changes only)
    ref: int | None = None  # regression source / reverted change / other half of a revert pair
    first: str | None = None  # superseded first decision, when the thread reverses it
    via: str | None = None  # P: the public function whose behaviour is discussed
    sha: str = ""
    release: int = 0  # actual release 1..8, 9 = unreleased
    cat: str = ""  # final category
    diff_cat: str = ""  # category by the diff alone (ignoring the threads)


def _sha1(data: bytes) -> bytes:
    return hashlib.sha1(data).digest()


def _tree_sha(files: dict[str, bytes], cache: dict) -> bytes:
    root: dict = {}
    for path, sha in files.items():
        node = root
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = sha

    def build(node: dict) -> bytes:
        entries = []
        for name, val in node.items():
            if isinstance(val, dict):
                entries.append((name + "/", b"40000 " + name.encode() + b"\0" + build(val)))
            else:
                entries.append((name, b"100644 " + name.encode() + b"\0" + val))
        entries.sort(key=lambda e: e[0].encode())
        body = b"".join(e[1] for e in entries)
        key = hashlib.sha1(body).digest()
        if key not in cache:
            cache[key] = _sha1(b"tree %d\0" % len(body) + body)
        return cache[key]

    return build(root)


def _fmt_ts(ts: int) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone(_dt.timedelta(hours=3))).strftime("%Y-%m-%d")


def _diff_cat(group: str, kind: str) -> str:
    if group in ("X", "RV"):
        return "fix"
    if group == "P":
        return "internal"
    if group == "D":
        return "breaking"
    return "internal" if kind == "INIT" else CAT[kind[0]]


def _final_cat(c: Commit, commits: list[Commit]) -> str:
    if c.group in ("X", "P", "D", "RV"):
        if c.dec == "regression":
            return "internal" if commits[c.ref].release == c.release else "fix"
        return DEC_CAT[c.dec]
    return c.diff_cat


def _message(R, subject: str, thread: int) -> str:
    head, _, rest = subject.partition("\n\n")
    style = R.randrange(6)
    if style == 0:
        head += f" (#{thread})"
    elif style == 1:
        head += f" [#{thread}]"
    else:
        word = ("Refs", "Closes", "See", "Issue")[style - 2]
        rest = (rest + "\n\n" if rest else "") + f"{word} #{thread}"
    return head + ("\n\n" + rest if rest else "") + "\n"


def _slots(R, plan: dict[str, int], size: int) -> list[str]:
    slots = [code for code, n in plan.items() for _ in range(n)]
    slots += ["I"] * (size - len(slots) - plan.get("RV", 0))
    R.shuffle(slots)
    # regressions need earlier work in the same release: keep them out of the first 40 %
    early = int(len(slots) * 0.4)
    for i in range(early):
        if slots[i] == "Xs":
            j = R.choice([k for k in range(early, len(slots)) if slots[k] != "Xs"])
            slots[i], slots[j] = slots[j], slots[i]
    return slots


class _Gen:
    """Runs the release plan: one operation per commit, remembers decisions and references."""

    def __init__(self) -> None:
        self.R = R = rng(TASK_ID)
        self.w = World(R)
        self.ops = Ops(self.w)
        self.commits: list[Commit] = []
        self.ts = int(_dt.datetime(2023, 2, 6, 11, 20, tzinfo=_dt.timezone(_dt.timedelta(hours=3))).timestamp())
        self.thread_no = 14
        self.rel = 1
        self.touches: dict[int, list[int]] = {}
        self.pending: dict[int, tuple[Fn, dict]] = {}  # RV: commit idx -> change to revert

    def commit(self, kind: str, group: str, fields: dict, *, dec=None, ref=None, thread=None,
               subject=None, cat=None) -> Commit:
        R = self.R
        if cat is None:
            cat = DEC_CAT[dec] if group in ("X", "P", "D", "RV") else _diff_cat(group, kind)
        if subject is None:
            key = {"X_change": "X_hook", "RV_change": "X_hook", "P_change": "I_private_fix"}.get(kind, kind)
            pool = MISLEAD[cat] if R.random() < MISLEAD_P[cat] else HONEST[key]
            safe = dict(fields)
            safe.setdefault("module", "core")
            safe.setdefault("name", safe.get("flag", "cli"))
            subject = R.choice(pool).format(**{k: str(v).strip('"') for k, v in safe.items()})
        if thread is None:
            self.thread_no += R.randint(1, 3)
            thread = self.thread_no
        self.ts += R.randint(2 * 3600, 44 * 3600)
        touched = self.w.fns.get(fields.get("new")) or self.w.fns.get(fields.get("name"))
        c = Commit(kind, group, subject.split("\n")[0], _message(R, subject, thread),
                   R.choice(AUTHORS), self.ts, self.w.render(), {**fields, "_ex": touched.ex if touched else ""},
                   thread, self.rel, dec=dec, ref=ref)
        c.diff_cat = _diff_cat(group, kind)
        self.commits.append(c)
        idx = len(self.commits) - 1
        if touched is not None and kind in TOUCH_KINDS:
            self.touches.setdefault(id(touched), []).append(idx)
        return c

    def _after_op(self, kind: str, fields: dict) -> None:
        w, R = self.w, self.R
        touched = w.fns.get(fields.get("new")) or w.fns.get(fields.get("name"))
        if touched is not None and kind not in ("I_tests", "I_docs"):
            if R.random() < 0.5:
                w.add_test(touched.name)
            if touched.name in w.exported and R.random() < 0.3:
                w.add_guide(touched.name)

    def plain(self, cat: str, forced: str | None = None) -> None:
        kind, fields = run_op(self.ops, self.R, cat, forced)
        self._after_op(kind, fields)
        self.commit(kind, cat, fields)

    def _via(self, fn: Fn) -> str:
        pub = self.w.plain_public() or self.w.public()
        callers = [f for f in pub if fn.name in _name_tokens(f.body)]
        same = [f for f in pub if f.module == fn.module]
        f = self.R.choice(callers or same or pub)
        return f"{f.module}.{f.name}"

    def behaviour(self, code: str) -> None:
        R, ops = self.R, self.ops
        group, dec = code[0], SLOT_DEC.get(code[1:2])
        if code == "RV":
            fields = ops.X_change()
            fn, rec = ops.last
            self._after_op("X_change", fields)
            first = R.choice(["bug", "intended", "extension"])
            c = self.commit("RV_change", "RV", fields, dec="revert")
            c.first = first
            rec["commit"] = len(self.commits) - 1
            self.w.locked.add(fn.name)
            self.pending[len(self.commits) - 1] = (fn, rec)
            return
        if group == "D":
            for kind in R.sample(["B_change_default", "B_change_default", "B_flag_default"], 3):
                fields = getattr(ops, kind)()
                if fields is not None:
                    break
            else:
                raise RuntimeError("no default to change")
            self._after_op(kind, fields)
            self.commit(kind, "D", fields, dec=dec)
            return
        if group == "P":
            fields = ops.P_change()
            if fields is None:
                raise RuntimeError("no private change left")
            fn, rec = ops.last
            self._after_op("P_change", fields)
            c = self.commit("P_change", "P", fields, dec=dec)
            c.via = self._via(fn)
            rec["commit"] = len(self.commits) - 1
            return
        if code in ("Xs", "Xo"):
            cands = []
            for f in self.w.plain_public():
                for idx in self.touches.get(id(f), []):
                    same = self.commits[idx].rel == self.rel
                    if same == (code == "Xs"):
                        cands.append((f, idx))
            R.shuffle(cands)
            for f, src in cands:
                fields = ops.X_change(f)
                if fields is not None:
                    self._after_op("X_change", fields)
                    self.commit("X_change", "X", fields, dec="regression", ref=src,
                                    cat="internal" if code == "Xs" else "fix")
                    ops.last[1]["commit"] = len(self.commits) - 1
                    return
            raise RuntimeError(f"no candidate for {code} in release {self.rel}")
        if code in ("Xf", "Xb") and R.random() < 0.25:
            olds = [(f, rec) for f in self.w.plain_public() for rec in f.applied
                    if "commit" in rec and self.commits[rec["commit"]].rel < self.rel
                    and self.commits[rec["commit"]].group == "X"]
            R.shuffle(olds)
            for f, rec in olds:
                src = rec["commit"]
                fields = ops.X_revert(f, rec)
                if fields is not None:
                    self._after_op("X_change", fields)
                    self.commit("RO_revert", "X", fields, dec=dec, ref=src)
                    return
        fields = ops.X_change()
        if fields is None:
            raise RuntimeError("no public change left")
        self._after_op("X_change", fields)
        self.commit("X_change", "X", fields, dec=dec)
        ops.last[1]["commit"] = len(self.commits) - 1

    def revert(self, orig: int) -> None:
        fn, rec = self.pending.pop(orig)
        self.w.locked.discard(fn.name)
        fields = self.ops.X_revert(fn, rec)
        assert fields is not None, "revert not applicable"
        o = self.commits[orig]
        subject = self.R.choice([f'Revert "{o.subject}"', f'Revert "{o.subject}"', None])
        c = self.commit("RV_revert", "RV", fields, dec="revert", ref=orig, thread=o.thread, subject=subject)
        c.first = o.first
        o.ref = len(self.commits) - 1
        if subject:
            c.message = c.message.replace("\n\n", f"\n\nThis reverts commit {{{{sha:{orig}}}}}.\n\n", 1) \
                if "\n\n" in c.message else c.message + f"\nThis reverts commit {{{{sha:{orig}}}}}.\n"

    def run(self, slots: list[str]) -> None:
        queue = list(slots)
        i = 0
        while i < len(queue):
            code = queue[i]
            if code.startswith("RVr"):
                self.revert(int(code[3:]))
            elif code in ("B", "F", "I"):
                self.plain(code, "B_remove_func" if code == "B" and self.rel == 9 else None)
            else:
                self.behaviour(code)
                if code == "RV":
                    pos = min(len(queue), i + 1 + self.R.randint(2, 5))
                    queue.insert(pos, f"RVr{len(self.commits) - 1}")
            i += 1


@functools.cache
def build() -> dict:
    g = _Gen()
    R = g.R
    g.commit("INIT", "INIT", {}, subject="Initial import of tablo")
    early = []
    for _ in range(EARLY):
        pick = R.choice("FFXXIII")
        early.append(pick if pick != "X" else "X" + R.choice("fbe"))
    g.run(early)
    boundaries = [len(g.commits)]  # index after the last commit of each release
    planned_r5 = None
    for ridx, plan in enumerate(PLAN):
        g.rel = ridx + 2
        if ridx == 3:  # release 5: the only feature comes after the planned point
            g.run(_slots(R, plan, PER_RELEASE - DELAYED))
            planned_r5 = len(g.commits)
            g.run(["I", "F", "I"])
        else:
            g.run(_slots(R, plan, PER_RELEASE))
        assert not g.pending
        boundaries.append(len(g.commits))
    g.rel = 9
    g.run(_slots(R, UNRELEASED, sum(UNRELEASED.values())))
    commits = g.commits

    # hashes
    blob_cache: dict[str, bytes] = {}
    tree_cache: dict = {}
    parent = None
    for c in commits:
        c.message = re.sub(r"\{\{sha:(\d+)\}\}", lambda m: commits[int(m.group(1))].sha, c.message)
        shas = {}
        for path, text in c.files.items():
            if text not in blob_cache:
                data = text.encode()
                blob_cache[text] = _sha1(b"blob %d\0" % len(data) + data)
            shas[path] = blob_cache[text]
        tree = _tree_sha(shas, tree_cache).hex()
        name, _, email = c.author.partition(" <")
        ident = f"{name} <{email} {c.ts} {TZ}"
        body = f"tree {tree}\n" + (f"parent {parent}\n" if parent else "") + \
            f"author {ident}\ncommitter {ident}\n\n{c.message}"
        raw = body.encode()
        c.sha = hashlib.sha1(b"commit %d\0" % len(raw) + raw).hexdigest()
        parent = c.sha
    shorts = [c.sha[:7] for c in commits]
    assert len(set(shorts)) == len(shorts)

    # releases: release k covers commits (boundaries[k-2], boundaries[k-1]]
    points = [b - 1 for b in boundaries]  # index of the last commit of each release
    rel_dates = []
    for p in points:
        d = _dt.date.fromisoformat(_fmt_ts(commits[p].ts)) + _dt.timedelta(days=R.randint(0, 3))
        rel_dates.append(d.isoformat())
    for i in range(1, len(rel_dates)):
        assert rel_dates[i] > rel_dates[i - 1]
    planned_point = planned_r5 - 1
    planned_date = (_dt.date.fromisoformat(_fmt_ts(commits[planned_point].ts)) + _dt.timedelta(days=1)).isoformat()
    for idx, c in enumerate(commits):
        c.release = next((k + 1 for k, p in enumerate(points) if idx <= p), 9)
        assert c.release == c.rel
    for c in commits:
        c.cat = _final_cat(c, commits)
    data = {
        "commits": commits, "points": points, "dates": rel_dates,
        "planned_point": planned_point, "planned_date": planned_date,
    }
    assert [v for v, _ in _expected_versions(data)] == EXPECTED_VERSIONS, _expected_versions(data)
    _plan_threads(data, R)
    return data


def run_op(ops: Ops, R, cat: str, forced: str | None = None) -> tuple[str, dict]:
    weights = WEIGHTS[cat]
    queue = ops.queues.setdefault(cat, [])
    for attempt in range(200):
        if forced:
            kind = forced
        elif cat in "BF" and attempt < 40:
            if not queue:
                queue.extend(sorted(weights))
                R.shuffle(queue)
            kind = queue.pop()
        else:
            kind = R.choices(list(weights), weights=list(weights.values()))[0]
        fields = getattr(ops, kind)()
        if fields is not None:
            return kind, fields
        if forced:
            raise RuntimeError(f"forced op {forced} not applicable")
    raise RuntimeError(f"no applicable op for {cat}")


# ---------------------------------------------------------------------------
# Issue / PR threads (issues/NNN.md): drafts built from the model, see REWRITE
# ---------------------------------------------------------------------------

MAINTAINERS = ("apetrova", "dsokolov")
LATE_MAINTAINER = "mkuz"
MAINT_SINCE = "2023-06-01"
CONTRIBUTORS = ("ilebedev", "porlov")
USERS = ("olga_data", "vlad-k", "ksenia-m", "petr_s", "dev-null42", "sergey_b", "n.ivanova", "timur-g",
         "alex_dataops", "rita_bi", "gleb.f", "mila-x")
LABELS = ("bug", "enhancement", "breaking-change", "regression", "needs-triage", "question", "docs", "tests",
          "good first issue", "cli", "wontfix?", "discussion")

DECIDE = {
    "bug": (
        "Разобрались. Прежнее поведение `{subj}` — ошибка: в описании функции обещано другое, и так никто не "
        "задумывал. Чиним как баг, контракт функции остаётся прежним.",
        "Решение по треду: это баг. `{subj}` с самого начала должна была обрабатывать этот случай иначе, старое "
        "поведение никто не проектировал — оно просто так получилось.",
        "Посмотрели историю и docstring: старый результат противоречит описанию. Значит, это ошибка, исправляем "
        "её, ничего нового не вводим.",
        "Итог такой: считаем это исправлением ошибки. Кто полагался на прежний результат `{subj}`, полагался на "
        "дефект.",
        "Это дефект, а не особенность. Правим, обещанное поведение `{subj}` не меняется — наоборот, наконец "
        "начинает соблюдаться.",
    ),
    "intended": (
        "Это не баг: старое поведение `{subj}` было сделано намеренно и работало ровно так, как описано. Но мы "
        "решили его поменять — это сознательное изменение контракта, пользователям придётся поправить свой код.",
        "Решение: меняем поведение `{subj}` намеренно. Раньше всё работало как задумывалось, теперь задумано "
        "иначе; кто опирался на старый результат, должен адаптироваться.",
        "Старый результат был корректным по тому, что мы сами обещали. Мы осознанно меняем правило — это "
        "изменение поведения, а не исправление ошибки.",
        "Итог: намеренное изменение. Ошибки в прежней версии `{subj}` не было, просто нам теперь нужно другое "
        "поведение, и старые вызовы начнут давать другой результат.",
    ),
    "extension": (
        "Раньше такой вход `{subj}` просто не поддерживала — вызов падал с исключением. Теперь поддерживает, а "
        "всё, что работало, работает как прежде. Это расширение возможностей.",
        "Решение: это новая поддерживаемая ситуация, а не исправление. Существующие вызовы не затронуты, просто "
        "теперь `{subj}` принимает и такие данные вместо ошибки.",
        "Итог: расширяем `{subj}`. Для прежних входов результат тот же, добавилась поддержка случая, который "
        "раньше отвергался с ошибкой.",
    ),
    "hidden": (
        "Проверили все публичные функции, которые могут дойти до `{name}`, включая `{via}`: ни одна из-за этой "
        "правки не меняет результат. Изменение чисто внутреннее, снаружи его не видно.",
        "Итог: снаружи ничего не меняется — до этой ветки в `{name}` публичные функции (и `{via}` тоже) никогда "
        "не доходят, вход отсеивается раньше. Внутренняя правка.",
        "Решение: наблюдаемое поведение библиотеки не меняется. `{via}` и остальные публичные функции дают те "
        "же результаты, что и до правки `{name}`.",
    ),
    "regression": (
        "Причина нашлась: это регрессия, её внесло изменение из #{ref}. До него `{subj}` работала правильно, "
        "после — сломалась.",
        "Бисект указал на #{ref}: именно там сломали этот случай. Это регрессия, чиним.",
        "Решение: регрессия после #{ref}. Возвращаем правильное поведение, которое было до того изменения.",
        "Разобрались: поломку принёс #{ref}, а не что-то новое. Так что это регрессия оттуда.",
    ),
    "revert": (
        "После слияния выяснилось, что изменение ломает несколько сценариев у пользователей. Решили откатить "
        "его целиком отдельным коммитом, в этом виде оно не нужно.",
        "Откатываем. Слишком много побочных эффектов, лучше вернуть как было и подумать ещё раз.",
        "Решение: изменение отменяем полностью (revert). Вернёмся к задаче позже, если вообще вернёмся.",
    ),
}

DECIDE_D = {
    "bug": (
        "Прежнее значение по умолчанию ({old}) у {subj} было ошибкой: так никогда не задумывалось, в "
        "обсуждениях всегда подразумевалось {new}. Исправляем.",
        "Решение: старый дефолт {old} — баг, его когда-то поставили по недосмотру. Меняем на {new} как "
        "исправление.",
        "Итог: значение по умолчанию {old} у {subj} — ошибка, а не чьё-то осознанное решение. Чиним.",
    ),
    "intended": (
        "Значение по умолчанию {subj} меняем с {old} на {new} намеренно: старое работало как задумано, но нам "
        "теперь нужно другое. Кто полагался на {old}, должен будет передавать его явно.",
        "Решение: это сознательная смена дефолта. Ошибки в {old} не было, просто {new} удобнее большинству, "
        "остальным придётся поправить вызовы.",
        "Итог: намеренно меняем значение по умолчанию на {new}. Прежнее было выбрано осознанно и работало, как "
        "описано.",
    ),
}

REVERSE_SELF = (
    "Пересмотрели после созвона. Моё сообщение выше отменяется.",
    "Беру свои слова назад, вывод был поспешным.",
    "Поправка к моему предыдущему сообщению — оно больше не действует.",
)
REVERSE_OTHER = (
    "Обсудили ещё раз с @{other} — решение меняем.",
    "Пересмотрели после созвона: решение @{other} выше больше не действует.",
)
REOPEN = (
    "Переоткрываю: после вопросов от пользователей стало понятно, что выше мы решили неправильно.",
    "Переоткрываю тред. Прежнее решение отменяем.",
)

CLAIM = {
    "bug": ("По-моему, это обычный баг, тут и обсуждать нечего.",
            "Это же явная ошибка — в changelog только как fix.",
            "Для меня это однозначно исправление бага."),
    "intended": ("Это же ломающее изменение, нужен мажорный релиз!",
                 "Кто-то точно на это полагался — это breaking, без вариантов.",
                 "Имейте в виду, для нас это несовместимое изменение."),
    "extension": ("Я бы назвал это новой возможностью, а не исправлением.",
                  "Это фича, а не фикс: раньше так просто нельзя было.",
                  "По сути это новая функциональность."),
    "hidden": ("Мне кажется, снаружи это вообще никто не заметит.",
               "Это же чисто внутренняя правка, пользователям всё равно.",
               "По-моему, это внутренняя кухня, в changelog не стоит."),
}

LATE = (
    "Всё равно считаю: {claim}",
    "Не соглашусь с итогом. {claim}",
    "Моё мнение не изменилось — {claim}",
    "Оставлю для истории: {claim}",
)

OPEN_X = (
    "Вызываю `{subj}({ex})` на выгрузке из учётной системы, и случай «{what}» обрабатывается не так, как я "
    "ожидаю. Результат потом уезжает в отчёт, и там всё съезжает. Версия — последняя из main. Это нормально "
    "или я что-то не так понимаю?",
    "Столкнулись с поведением `{subj}`, которое нас удивило: «{what}». Пример воспроизведения ниже, данные "
    "обезличены. Раньше мы этот случай обходили руками, но хочется понять, как правильно.",
    "Кажется, `{subj}` неправильно ведёт себя в ситуации «{what}». Проверял на двух машинах, результат "
    "одинаковый. Документацию читал, однозначного ответа там не нашёл.",
    "Вопрос по `{subj}`: что должно происходить в случае «{what}»? Сейчас функция делает не то, что написано "
    "в docs/guide.md, по крайней мере в моём прочтении. Прикладываю минимальный пример.",
)
OPEN_P = (
    "Публичная `{via}` в случае «{what}» ведёт себя странно. Покопался в коде — похоже, всё упирается во "
    "внутреннюю `{name}` из модуля `{module}`. Можно ли там поправить?",
    "Нашёл место во внутренностях: `{module}.{name}` не учитывает «{what}». Не уверен, влияет ли это на "
    "что-то снаружи, но выглядит подозрительно. Через `{via}` пытался воспроизвести — пока не очень "
    "получается.",
    "PR: правка в `{module}.{name}` для случая «{what}». Функция внутренняя, но её используют несколько "
    "публичных, в том числе, возможно, `{via}`.",
)
OPEN_D = (
    "Предлагаю поменять значение по умолчанию у {subj}: сейчас {old}, а почти все наши пользователи "
    "передают {new} явно. Прикладываю PR.",
    "Заметил, что у {subj} по умолчанию {old}. Каждый новый человек в команде на этом спотыкается. Может, "
    "сделать {new}?",
    "PR меняет значение по умолчанию {subj} с {old} на {new}. Подробности ниже, давайте решим, как это "
    "оформлять.",
)
OPEN_RO = (
    "После #{ref} `{subj}` иначе обрабатывает «{what}», и у нас поехали отчёты. Предлагаю вернуть поведение, "
    "которое было до того изменения. PR готов.",
    "Хочу обсудить возврат старого поведения `{subj}` (случай «{what}»), которое поменяли в #{ref}. Мы "
    "долго жили на новой версии, но оно того не стоит.",
)
OPEN_PR = (
    "PR: {honest}. {why}",
    "Предлагаю изменение: {honest}. {why} Дифф небольшой, посмотрите, пожалуйста.",
    "Делаю {honest_l}. {why}",
)
WHY = {
    "B": ("Эту часть API давно пора привести в порядок, старый вариант путает людей.",
          "Держать это дальше дорого: каждый релиз приходится помнить про старое поведение.",
          "Упрощаем интерфейс перед следующими большими изменениями."),
    "F": ("Это часто просили в чате, заодно закрывает пару старых вопросов.",
          "Без этого приходилось писать обёртки в каждом проекте.",
          "Небольшое удобство, которое давно напрашивалось."),
    "I": ("Чисто техническая правка, по ходу работы над соседними задачами.",
          "Наводим порядок, чтобы дальше было проще сопровождать код.",
          "Мелочь, но глаз цепляется каждый раз."),
}
CHAT = (
    "Воспроизвёл у себя:\n\n```python\n>>> from tablo import {module}\n>>> {module}.{name}({ex})\n```\n\n"
    "Вывод совпадает с тем, что описан выше. Python 3.11, macOS.",
    "+1, у нас то же самое на выгрузках из складской системы. Пока обходим предобработкой строк, но это "
    "костыль.",
    "А какая у вас версия? Мы недавно трогали этот модуль, может, уже неактуально.",
    "Версия из main на прошлой неделе. Пересобрал сейчас — поведение то же.",
    "Если что, есть похожий кейс в тестах, `tests/test_{module_s}.py`, но он проверяет немного другое.",
    "Мы на это наступили при переезде на новый формат выгрузок. Было бы здорово поправить до следующего "
    "релиза, но не горит.",
    "Посмотрел код: логика в `{name}` действительно так написана, это не случайность в данных.",
    "Добавлю контекст: у нас эти данные приходят из Excel, там такое встречается постоянно.",
    "Можно пример входных данных целиком? Хочу убедиться, что дело не в кодировке файла.",
    "Прикладываю кусок файла (данные обезличены). С разделителем и кодировкой всё в порядке, проверил.",
)
REVIEW = (
    "Посмотрел дифф, выглядит нормально. Одно замечание по стилю, оставил в коде.",
    "Можно добавить тест на этот случай? Остальное ок.",
    "CI зелёный на всех версиях Python.",
    "Поправил замечания, посмотрите ещё раз, пожалуйста.",
    "Проверил локально на наших отчётах — ничего не сломалось.",
    "Не забудьте обновить docs/guide.md, если нужно.",
    "Нет возражений.",
)
MERGE = (
    "Влито в main, спасибо!",
    "Смержено. Спасибо за PR.",
    "Изменение в main, закрою тред после релиза.",
    "Слито, CI зелёный.",
)
CLOSE = (
    "Закрываю.",
    "Спасибо всем, закрываю тред.",
    "Если всплывёт снова — открывайте новый тред со ссылкой на этот.",
    "Закрыто. Дальнейшее обсуждение — в новых тредах.",
)
QUOTE_OLD = (
    "> {quoted}\n\nЭто уже неактуально, решение выше поменялось.",
    "> {quoted}\n\nНапоминаю, что это решение отменено — ориентируйтесь на более позднее.",
)
QUOTE_AGREE = (
    "> {quoted}\n\nСпасибо, так и сделаем у себя.",
    "> {quoted}\n\nПонятно, принято.",
)
TITLES_X = ("{subj}: {what}", "Странный результат {subj} ({what})", "{subj} ведёт себя неожиданно: {what}",
            "Проблема в {subj}: {what}")
TITLES_P = ("{via}: {what}", "Внутренняя {name}: {what}", "Правка {module}.{name} ({what})")
TITLES_D = ("Значение по умолчанию: {subj}", "Сменить дефолт {subj}?", "{subj}: {old} → {new} по умолчанию")
TITLES_RO = ("Вернуть старое поведение {subj}", "Откат изменения {subj} из #{ref}")

ANALYSIS = (
    "Покопался в коде. Всё решается в нескольких строках в начале `{name}`: там входное значение сразу "
    "уходит в основной цикл, и отдельной ветки для случая «{what}» просто нет. Поэтому результат зависит от "
    "того, как этот случай ведёт себя в стандартной библиотеке, а не от нашей логики. Если добавить явную "
    "обработку, остальные ветки не затронутся — я проверил на тестах из `tests/`, они проходят.",
    "Небольшой разбор, чтобы не потерялось. Сейчас в `{subj}` путь такой: значение приходит как есть, "
    "нормализации нет, дальше идёт основная логика. На обычных данных это незаметно. На случае «{what}» "
    "логика получает то, на что не рассчитана, и отсюда эффект, который описан выше. Правка точечная, "
    "сигнатура функции не меняется.",
    "Сравнил поведение на трёх версиях из main за последние месяцы: на всех одно и то же. Так что это не "
    "что-то внезапное, функция всегда так себя вела в случае «{what}». Вопрос только в том, как к этому "
    "относиться — это уже решать мейнтейнерам.",
    "Проверил, кто ещё зависит от этого места. Через `{name}` проходят несколько сценариев: чтение выгрузок, "
    "печать отчётов, группировка по столбцам. Ни один из них не падает после правки, но результат в случае "
    "«{what}» будет другим — это надо иметь в виду тем, кто сравнивает отчёты побайтно.",
    "Написал минимальный тест, который воспроизводит ситуацию «{what}». Без правки он показывает текущее "
    "поведение, с правкой — новое. Тест приложу к PR, чтобы поведение было зафиксировано, как бы мы его в "
    "итоге ни назвали.",
)
CONTEXT = (
    "Немного контекста о том, откуда у нас такие данные. Мы собираем ежедневные отчёты из трёх систем: "
    "бухгалтерия отдаёт CSV с точкой с запятой, склад — с табуляцией, а продажи вообще выгружают из "
    "Excel. После склейки как раз и появляется «{what}». Не думаю, что мы одни такие.",
    "У нас tablo стоит в ночном конвейере: забираем выгрузки, нормализуем, печатаем сводку и отправляем "
    "руководителям. Любое изменение результата сразу видно в утренних письмах, поэтому для нас важно "
    "заранее знать, как это будет оформлено в changelog.",
    "Для понимания масштаба: у нас около сорока скриптов, которые вызывают эту функцию напрямую. "
    "Переписывать их все ради одного случая не хочется, но и жить с текущим поведением неудобно.",
    "Мы используем tablo в учебном курсе по обработке данных, студенты регулярно натыкаются на этот случай "
    "и спрашивают, баг это или так задумано. Хорошо бы иметь однозначный ответ.",
)
WORKAROUND = (
    "Временный обход, если кому-то нужно прямо сейчас: предварительно нормализовать входные данные своей "
    "функцией и только потом передавать в `{subj}`. Некрасиво, но работает.",
    "Пока мы просто оборачиваем вызов в свою функцию и отдельно разбираем случай «{what}». Когда будет "
    "решение, обёртку уберём.",
    "У себя закрепили версию библиотеки, чтобы ничего не поменялось неожиданно посреди квартала. Ждём, что "
    "решат.",
)
PLANNING = (
    "Когда примерно ближайший релиз? Хотим понять, успеет ли туда это изменение.",
    "Это попадёт в ближайший релиз или в следующий?",
    "Есть ли шанс увидеть это до конца месяца? У нас закрытие квартала.",
)
PLANNING_ANSWER = (
    "Релиз соберём, когда накопится ещё немного изменений; дата будет в RELEASES.md.",
    "Точной даты нет, но изменение будет в том релизе, куда попадёт этот коммит.",
)
PR_BODY = {
    "B": (
        "\n\nЧто сделано:\n- {honest_l};\n- обновлены тесты и примеры, где это упоминалось;\n- поправлены "
        "ссылки в документации.\n\nЗачем: старый вариант мешает дальнейшей работе над модулем `{module}`, и "
        "держать его ради совместимости мы больше не хотим. Если кто-то на это полагается — скажите в треде.",
        "\n\nКратко: {honest_l}. Внутри PR ещё мелкая чистка вокруг, но по сути изменение одно. Проверял на "
        "наших внутренних отчётах и на примерах из docs/guide.md — ничего неожиданного не всплыло.",
    ),
    "F": (
        "\n\nЧто сделано:\n- {honest_l};\n- добавлен тест;\n- пример использования будет в docs/guide.md "
        "отдельным PR.\n\nЗачем: об этом несколько раз спрашивали в чате, и у нас самих есть два места, где "
        "приходилось писать обёртку.",
        "\n\nИзменение небольшое: {honest_l}. Старые вызовы работают как раньше, я прогнал весь набор тестов "
        "на 3.10 и 3.11.",
    ),
    "I": (
        "\n\nЧто сделано:\n- {honest_l};\n- больше ничего не трогал.\n\nПоведение библиотеки не меняется, "
        "это чисто техническая правка. Тесты проходят локально.",
        "\n\nПравка по ходу дела: {honest_l}. Отдельного обсуждения, наверное, не требует, но пусть будет "
        "тред, чтобы было куда сослаться из истории.",
        "\n\nСуть: {honest_l}. Заодно поправил пару опечаток рядом. Если что-то смущает — пишите, переделаю.",
    ),
}
REVIEW_LONG = (
    "Посмотрел внимательно. Логика понятна, по коду вопросов нет. Единственное — в описании PR стоит явно "
    "написать, что именно меняется для пользователей, чтобы потом не восстанавливать это по диффу.",
    "Прогнал у себя весь набор тестов и пару наших внутренних скриптов поверх — всё зелёное. Можно вливать, "
    "когда будет второй аппрув.",
    "Мне не очень нравится название, но это вкусовщина, спорить не буду. Остальное выглядит хорошо.",
    "Проверь, пожалуйста, что docs/guide.md не ссылается на старое поведение. Я быстро поискал — вроде нет, "
    "но лучше перепроверить.",
)

MAINT_THINK = (
    "Прежде чем решать, как это оформлять, хочу посмотреть историю функции и то, что мы обещали в "
    "документации. Код в PR выглядит нормально, вопрос не в нём. Вернусь с ответом в ближайшие дни.",
    "Спасибо за подробный разбор. Пока не готовы сказать, как мы это классифицируем: нужно понять, "
    "полагался ли кто-то на текущее поведение сознательно. Если у вас есть такие примеры — приносите.",
    "Отмечу для всех: как это пойдёт в changelog, решим отдельно, когда разберёмся. Техническую часть PR "
    "можно доводить параллельно, она от этого не зависит.",
    "Посмотрели PR и тест. Технически всё в порядке. Вопрос о том, считать ли это исправлением или чем-то "
    "другим, пока открыт — дайте пару дней.",
)
EXAMPLE = (
    "Минимальный пример:\n\n```python\n>>> from tablo import {module}\n>>> {module}.{name}({ex})\n```\n\n"
    "На обычных данных результат ожидаемый. А вот если во входе встречается случай «{what}», получается "
    "другое, и дальше по конвейеру это расползается по всему отчёту.",
    "Вот как это выглядит у нас (упрощённо):\n\n```python\nfrom tablo import {module}\n\nfor row in rows:\n"
    "    value = {module}.{name}({ex})\n    report.append(value)\n```\n\nПока в данных нет «{what}», всё "
    "хорошо. Как только появляется — отчёт перестаёт сходиться с бухгалтерским.",
)
PR_TESTING = (
    "\n\nКак проверял: прогнал `python -m unittest discover -s tests` на 3.10 и 3.11, плюс вручную "
    "посмотрел вывод `python -m tablo` на паре наших реальных выгрузок. Разницы, кроме ожидаемой, нет.",
    "\n\nПроверка: весь набор тестов проходит; дополнительно сравнил результаты на трёх файлах из "
    "примеров — совпадают с тем, что было до правки, кроме затронутого места.",
    "\n\nТесты зелёные. Отдельно проверил, что `docs/guide.md` и README не ссылаются на то, что здесь "
    "меняется.",
)
AUTHOR_REPLY = (
    "Спасибо за ревью! Поправил замечания отдельным коммитом в этом же PR, посмотрите, пожалуйста.",
    "Про название — согласен, но переименовывать сейчас не буду, чтобы не раздувать дифф. Если нужно, "
    "сделаю отдельным PR.",
    "Добавил тест, как просили. Он маленький, но фиксирует поведение, о котором шла речь.",
)

PR_QA = {
    "B": (("А как теперь быть тем, у кого это используется в десятке скриптов? Есть какой-то путь миграции, "
           "кроме как переписать всё руками?",
           "Путь простой: поиск по коду и замена, я опишу это в примечании к релизу. Если где-то не получится — "
           "пишите сюда, разберём конкретный случай."),
          ("Это точно нужно делать сейчас? Может, подождать, пока накопится больше подобных изменений?",
           "Тянуть смысла нет: чем дольше живёт старый вариант, тем больше кода на него завязывается.")),
    "F": (("Можно ли это использовать вместе с остальными функциями модуля, или есть ограничения?",
           "Ограничений нет, всё сочетается как обычно. Примеры добавлю в руководство."),
          ("Классно, давно этого не хватало. А документация будет?",
           "Да, отдельным PR в docs/guide.md, чтобы не смешивать с кодом.")),
    "I": (("Это как-то повлияет на наши скрипты?",
           "Нет, снаружи ничего не меняется, это чисто внутренняя работа."),
          ("А зачем отдельный тред для такой мелочи?",
           "Чтобы из истории коммитов можно было сослаться на обсуждение, у нас так принято.")),
}

MAINT_NOTE = {
    "bug": ("Для истории: по сути это исправление ошибки, так и запишем.",
            "Считаю это обычным багфиксом, не более."),
    "intended": ("Отмечу: это намеренное изменение поведения, пусть все будут в курсе.",
                 "Это сознательное изменение, старое поведение было задумано, но больше не нужно."),
    "extension": ("По-моему, это расширение возможностей, ничего старого не ломаем.",
                  "Это новая возможность, существующие вызовы не затронуты."),
    "hidden": ("Снаружи это не видно, чисто внутренняя правка.",
               "Пользователи этого не заметят, всё внутри."),
}
FEMALE = {"apetrova", "mkuz", "olga_data", "ksenia-m", "n.ivanova", "rita_bi", "mila-x"}
_FEM = {"Воспроизвёл": "Воспроизвела", "Проверял": "Проверяла", "читал": "читала", "Покопался": "Покопалась",
        "Нашёл": "Нашла", "уверен": "уверена", "Посмотрел": "Посмотрела", "Пересобрал": "Пересобрала",
        "Поправил": "Поправила", "Заметил": "Заметила", "проверил": "проверила", "Проверил": "Проверила",
        "назвал": "назвала", "пытался": "пыталась", "Сравнил": "Сравнила", "Написал": "Написала",
        "проверял": "проверяла", "Прогнал": "Прогнала", "поискал": "поискала", "поправил": "поправила",
        "трогал": "трогала"}
VALID = {"X": ("bug", "intended", "extension"), "P": ("hidden", "bug", "intended", "extension"),
         "D": ("bug", "intended"), "RO": ("bug", "intended")}


def _fem(text: str) -> str:
    return re.sub(r"\b(" + "|".join(_FEM) + r")\b", lambda m: _FEM[m.group(1)], text)


def _day(date: str, days: int) -> str:
    return (_dt.date.fromisoformat(date) + _dt.timedelta(days=days)).isoformat()


def _type_cat(c: Commit, dec: str, ref: int | None, commits: list[Commit]) -> str:
    """Category of commit `c` if the maintainers' final decision were `dec`."""
    if dec == "regression":
        return "internal" if commits[ref].release == c.release else "fix"
    return DEC_CAT[dec]


def _ctx(c: Commit) -> dict:
    f = c.fields
    what = f.get("what", "")
    what = next((g[3] for g in GUARDS if g[2] == what), what)
    ctx = {"name": f.get("name", ""), "module": f.get("module", ""), "what": what, "ex": f.get("_ex", ""),
           "module_s": f.get("module", "").strip("_"), "via": c.via or "", "old": f.get("old", ""),
           "new": f.get("new", "")}
    if c.group == "D":
        ctx["subj"] = (f"флага `--{f['flag']}`" if "flag" in f
                       else f"параметра `{f['param']}` функции `{f['module']}.{f['name']}`")
    else:
        ctx["subj"] = f"{ctx['module']}.{ctx['name']}"
    return ctx


class _Thread:
    def __init__(self, R, n: int, start: str) -> None:
        self.R, self.n, self.start = R, n, start
        self.msgs: list[dict] = []

    def add(self, handle: str, date: str, text: str, role: str, dec: str | None = None,
            core: str | None = None) -> None:
        if handle in FEMALE:
            text = _fem(text)
        self.msgs.append({"h": handle, "date": date, "text": text, "role": role, "dec": dec, "core": core or text})

    def maint(self, date: str, avoid: str | None = None) -> str:
        pool = [m for m in (*MAINTAINERS, LATE_MAINTAINER) if m != avoid
                and (m != LATE_MAINTAINER or date >= MAINT_SINCE)]
        return self.R.choice(pool)

    def user(self, avoid: tuple = ()) -> str:
        return self.R.choice([u for u in USERS + CONTRIBUTORS if u not in avoid])

    def render(self, title: str, labels: list[str]) -> str:
        out = [f"# #{self.n}: {title}", "", f"Статус: закрыт · Метки: {', '.join(labels)}", ""]
        for m in self.msgs:
            out += ["---", "", f"### @{m['h']} · {m['date']}", "", m["text"], ""]
        return "\n".join(out)

DECIDE_RO = {
    "bug": ("Изменение из #{ref} было ошибкой. Возвращаем `{subj}` прежнее поведение — это исправление, а не "
            "новая политика.",
            "Решение: то, что сделали в #{ref}, оказалось дефектом. Чиним, возвращая поведение `{subj}`, "
            "которое было до него."),
    "intended": ("В #{ref} всё было сделано правильно и работало как задумано, но мы сознательно возвращаемся к "
                 "старому контракту `{subj}`. Это намеренное изменение поведения: кто успел привыкнуть к версии "
                 "из #{ref}, должен адаптироваться.",
                 "Решение: ошибки в #{ref} не было, но мы намеренно меняем поведение `{subj}` обратно. Для тех, "
                 "кто уже опирается на текущий результат, это изменение контракта."),
}


def _first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = parts[0]
    for p in parts[1:]:
        if len(out) > 60:
            break
        out += " " + p
    return out


def _decision_text(R, grp: str, dec: str, ctx: dict) -> str:
    if grp == "D":
        return R.choice(DECIDE_D[dec]).format(**ctx)
    if grp == "RO":
        return R.choice(DECIDE_RO[dec]).format(**ctx)
    if grp == "P" and dec != "hidden":
        return (f"Эта правка в `{ctx['name']}` меняет результат публичной `{ctx['via']}`. "
                + R.choice(DECIDE[dec]).format(**{**ctx, "subj": ctx["via"]}))
    return R.choice(DECIDE[dec]).format(**ctx)


def _compose_dep(R, c: Commit, commits: list[Commit]) -> tuple[_Thread, str]:
    grp = "RO" if c.kind == "RO_revert" else c.group
    ctx = _ctx(c)
    ctx["ref"] = commits[c.ref].thread if c.ref is not None and c.group != "RV" else ""
    d0 = _fmt_ts(c.ts)
    th = _Thread(R, c.thread, d0)
    titles, openers = {"P": (TITLES_P, OPEN_P), "D": (TITLES_D, OPEN_D),
                       "RO": (TITLES_RO, OPEN_RO)}.get(grp, (TITLES_X, OPEN_X))
    title = R.choice(titles).format(**ctx).replace("`", "")
    chat_pool = [t for t in CHAT if "```" not in t] if grp in ("P", "D") else list(CHAT)
    if "flag" in c.fields:
        chat_pool = [t for t in chat_pool if "{name}" not in t and "{module" not in t]
    final = "revert" if grp == "RV" else c.dec
    valid = VALID.get(grp, VALID["X"])
    final_cat = "internal" if grp == "RV" else _type_cat(c, c.dec, c.ref, commits)
    other = [t for t in valid if grp == "RV" or _type_cat(c, t, c.ref, commits) != final_cat]
    reporter = th.user()
    pre: list[tuple] = [(reporter, R.choice(openers).format(**ctx), "open", None)]
    if grp != "D":
        pool = [t for i, t in enumerate(ANALYSIS) if i != 2 or c.dec not in ("regression",) and grp != "RO"]
        pre.append((th.user((reporter,)), R.choice(pool).format(**ctx), "chat", None))
    extra = [(R.choice(CONTEXT), "u"), (R.choice(WORKAROUND), "u"), (R.choice(PLANNING), "p")]
    for t, kind in R.sample(extra, R.randint(1, 3)):
        pre.append((th.user(), t.format(**ctx), "chat", None))
        if kind == "p":
            pre.append(("@maint", R.choice(PLANNING_ANSWER), "chat", None))
    if grp in ("X", "RV", "RO"):
        pre.insert(1, (reporter, R.choice(EXAMPLE).format(**ctx), "chat", None))
    for t in R.sample(chat_pool, R.randint(1, 3)):
        pre.append((th.user((reporter,)), t.format(**ctx), "chat", None))
    pre.append(("@maint", R.choice(MAINT_THINK), "think", None))
    if c.dec == "regression" and R.random() < 0.5:
        decoys = sorted({x.thread for x in commits if x.thread < c.thread and x.thread != ctx["ref"]})
        if decoys:
            m = R.choice(decoys[-40:])
            pre.append((th.user(), f"Может, это после #{m}? Там вроде тоже что-то меняли рядом с `{ctx['name']}`.",
                        "chat", None))
    if R.random() < 0.5:
        ct = R.choice([t for t in valid if t in CLAIM])
        pre.append((th.user(), R.choice(CLAIM[ct]), "claim", ct))
    if R.random() < 0.5 and other:
        mt = R.choice(other)
        pre.insert(1, (LATE_MAINTAINER, _decision_text(R, grp, mt, ctx), "maria_pre", mt))
    first = grp != "RV" and bool(other) and R.random() < 0.4
    first_dec = c.first if grp == "RV" else (R.choice(other) if first else None)
    reopen = first and R.random() < 0.5
    offsets = sorted(R.sample(range(-30, 0), len(pre) + 2))
    dates = [_day(d0, o) for o in offsets]
    for (h, text, role, dec), date in zip(pre, dates, strict=False):
        if role == "maria_pre" and date >= MAINT_SINCE:
            continue
        th.add(th.maint(date) if h == "@maint" else h, date, text, role, dec)
    a = th.maint(dates[-2])
    if first_dec:
        th.add(a, dates[-2], _decision_text(R, grp, first_dec, ctx), "first", first_dec)
    if grp != "RV" and not reopen:
        b = th.maint(dates[-1])
        prefix = R.choice(REVERSE_SELF if b == a else REVERSE_OTHER).format(other=a) + " " if first else ""
        core = _decision_text(R, grp, final, ctx)
        th.add(b, dates[-1], prefix + core, "final", final, core)
    th.add(th.maint(d0), d0, R.choice(MERGE), "merge")
    post = 1
    if reopen:
        rd = _day(d0, R.randint(2, 6))
        core = _decision_text(R, grp, final, ctx)
        th.add(th.maint(rd), rd, R.choice(REOPEN) + " " + core, "final", final, core)
        post = 7
    if first_dec and grp != "RV" and R.random() < 0.6:
        quoted = _first_sentence(next(m["text"] for m in th.msgs if m["role"] == "first"))
        th.add(th.maint(_day(d0, post + 1)), _day(d0, post + 1), R.choice(QUOTE_OLD).format(quoted=quoted),
               "quote")
    elif grp != "RV" and R.random() < 0.4:
        quoted = _first_sentence(next(m["core"] for m in th.msgs if m["role"] == "final"))
        th.add(th.user(), _day(d0, post + 1), R.choice(QUOTE_AGREE).format(quoted=quoted), "quote")
    if grp == "RV":
        rdate = _fmt_ts(commits[c.ref].ts)
        th.add(th.maint(rdate), rdate, R.choice(DECIDE["revert"]), "final", "revert")
        th.add(th.maint(rdate), rdate, "Откат влит в main.", "merge")
        post = (_dt.date.fromisoformat(rdate) - _dt.date.fromisoformat(d0)).days
    if R.random() < 0.4:
        lt = R.choice([t for t in (other if grp != "RV" else valid) if t in CLAIM] or ["bug"])
        claim = R.choice(CLAIM[lt])
        th.add(th.user(), _day(d0, post + 2), R.choice(LATE).format(claim=claim[:1].lower() + claim[1:]),
               "late", lt)
    th.add(th.maint(_day(d0, post + 3)), _day(d0, post + 3), R.choice(CLOSE), "close")
    return th, title


def _honest(R, c: Commit) -> str:
    if c.kind == "INIT":
        return "Initial import of tablo"
    safe = {k: str(v).strip('"') for k, v in c.fields.items()}
    safe.setdefault("module", "core")
    safe.setdefault("name", safe.get("flag", "cli"))
    return R.choice(HONEST[c.kind]).format(**safe).split("\n")[0]


def _compose_pr(R, c: Commit) -> tuple[_Thread, str]:
    ctx = _ctx(c)
    d0 = _fmt_ts(c.ts)
    th = _Thread(R, c.thread, d0)
    honest = _honest(R, c)
    group = c.group if c.group in WHY else "I"
    author = R.choice([*CONTRIBUTORS, *MAINTAINERS, *((LATE_MAINTAINER,) if d0 >= MAINT_SINCE else ())])
    fmt = {"honest": honest, "honest_l": honest[:1].lower() + honest[1:], "why": R.choice(WHY[group]),
           "module": ctx["module"]}
    opener = R.choice(OPEN_PR).format(**fmt) + R.choice(PR_BODY[group]).format(**fmt) + R.choice(PR_TESTING)
    msgs: list[tuple] = [(author, opener, "open", None)]
    reviewers = [h for h in (*CONTRIBUTORS, *MAINTAINERS) if h != author]
    for t in R.sample(REVIEW + REVIEW_LONG, R.randint(2, 4)):
        msgs.append((R.choice(reviewers), t, "review", None))
    if R.random() < 0.7:
        msgs.append((author, R.choice(AUTHOR_REPLY), "review", None))
    if R.random() < 0.6:
        q, a = R.choice(PR_QA[group])
        msgs.append((th.user(), q, "chat", None))
        msgs.append((author, a, "chat", None))
    if c.fields.get("_ex") and R.random() < 0.85:
        t = R.choice([t for t in CHAT if "```" not in t and ("{name}" in t or "{module_s}" in t)])
        msgs.append((th.user(), t.format(**ctx), "chat", None))
    if R.random() < 0.5:
        note = R.choice(sorted(MAINT_NOTE))
        msgs.append(("@maint", R.choice(MAINT_NOTE[note]), "note", note))
    if R.random() < 0.3:
        ct = R.choice(sorted(CLAIM))
        msgs.append((th.user(), R.choice(CLAIM[ct]), "claim", ct))
    offsets = sorted(R.sample(range(-16, 0), len(msgs)))
    for (h, text, role, dec), o in zip(msgs, offsets, strict=True):
        date = _day(d0, o)
        th.add(th.maint(date) if h == "@maint" else h, date, text, role, dec)
    th.add(th.maint(d0), d0, R.choice(MERGE), "merge")
    if R.random() < 0.5:
        th.add(th.maint(_day(d0, 1)), _day(d0, 1), R.choice(CLOSE), "close")
    return th, honest


def _plan_threads(data: dict, R) -> None:
    commits = data["commits"]
    threads: dict[int, dict] = {}
    for c in commits:
        if c.thread in threads:
            continue
        if c.group in ("X", "P", "D", "RV"):
            th, title = _compose_dep(R, c, commits)
        else:
            th, title = _compose_pr(R, c)
        decided = [m for m in th.msgs if m["role"] in ("first", "final", "note")]
        voices = [m for m in th.msgs if m["dec"]]
        final = decided[-1]["dec"] if decided else "none"
        if c.group in ("X", "P", "D", "RV"):
            assert final == ("revert" if c.group == "RV" else c.dec), (c.thread, final, c.dec)
        n_labels = R.randint(1, 2)
        labels = R.sample(LABELS, n_labels)
        threads[c.thread] = {
            "n": c.thread,
            "text": th.render(title, labels),
            "msgs": th.msgs,
            "final": final,
            "ref": commits[c.ref].thread if c.dec == "regression" or c.kind == "RO_revert" else None,
            "first": decided[0]["dec"] if decided else "none",
            "voice": voices[-1]["dec"] if voices else "none",
            "commits": [i for i, x in enumerate(commits) if x.thread == c.thread],
        }
    data["threads"] = threads


# ---------------------------------------------------------------------------
# Versions and changelog
# ---------------------------------------------------------------------------

SECTIONS = (("breaking", "Breaking"), ("feature", "Features"), ("fix", "Fixes"))


def _releases(data: dict, points: list[int]) -> list[list[int]]:
    out = []
    prev = -1
    for p in points:
        out.append(list(range(prev + 1, p + 1)))
        prev = p
    return out


def _versions(cats_per_release: list[list[str]], reset: bool = True) -> list[str]:
    major, minor, patch = 1, 0, 0
    out = ["1.0.0"]
    for cats in cats_per_release[1:]:
        if "breaking" in cats:
            major += 1
            if reset:
                minor = patch = 0
        elif "feature" in cats:
            minor += 1
            if reset:
                patch = 0
        else:
            patch += 1
        out.append(f"{major}.{minor}.{patch}")
    return out


def _changelog(data: dict, *, classify=None, points=None, dates=None, reset=True) -> str:
    commits = data["commits"]
    classify = classify or (lambda c: c.cat)
    points = points or data["points"]
    dates = dates or data["dates"]
    rels = _releases(data, points)
    cats = [[classify(commits[i]) for i in r] if k else [] for k, r in enumerate(rels)]
    versions = _versions(cats, reset=reset)
    blocks = []
    for k in range(len(rels) - 1, -1, -1):
        lines = [f"## v{versions[k]} ({dates[k]})", ""]
        if k:
            for key, title in SECTIONS:
                items = [commits[i] for i in rels[k] if classify(commits[i]) == key]
                if items:
                    lines.append(f"### {title}")
                    lines.extend(f"- {c.sha[:7]} {c.subject}" for c in items)
                    lines.append("")
        blocks.append("\n".join(lines).rstrip() + "\n")
    return "# Changelog\n\n" + "\n".join(blocks)


def _expected_versions(data: dict) -> list[tuple[str, str]]:
    rels = _releases(data, data["points"])
    cats = [[data["commits"][i].cat for i in r] if k else [] for k, r in enumerate(rels)]
    return list(zip(_versions(cats), data["dates"], strict=True))


# ---------------------------------------------------------------------------
# Static texts
# ---------------------------------------------------------------------------

CONTRIBUTING = """\
# Как мы ведём tablo

## Код и тесты

- Код пакета лежит в `tablo/`, тесты — в `tests/` (`python -m unittest discover -s tests`).
- Приватные функции называются с подчёркиванием (`_split_simple`). Модуль `tablo/_util.py` —
  внутренний целиком, даже если имена в нём без подчёркивания.
- Сообщения коммитов у нас пишут как придётся, поэтому при подготовке релиза категорию
  изменения определяют содержимое коммита (дифф) и решение мейнтейнеров в треде, на который
  ссылается коммит, но никогда не сообщение коммита.

## Треды и решения мейнтейнеров

- Каждый коммит ссылается в сообщении на тред трекера `#N` (issue или PR). Архив тредов
  выгружен из трекера в каталог `issues/` рядом с репозиторием (`issues/N.md`), в git он не
  коммитится. Откат (revert) ссылается на тот же тред, что и отменяемое изменение.
- Мейнтейнеры: @apetrova (Анна Петрова), @dsokolov (Дмитрий Соколов), а начиная с
  2023-06-01 также @mkuz (Мария Кузнецова). Сообщения @mkuz, написанные до 2023-06-01, —
  мнение участника, а не решение.
- Решение принимают только мейнтейнеры. Мнения остальных участников (авторов issue,
  контрибьюторов, пользователей), метки трекера и заголовок треда ничего не решают.
- Если мейнтейнеры меняли решение (пересмотрели, переоткрыли тред), действует последнее по
  времени решение — даже если оно принято после слияния коммита или после выхода релиза.
  Цитата (строки, начинающиеся с `>`) — не новое решение, а ссылка на старое. Сообщения
  мейнтейнеров без решения («вернусь позже», «влито», ответы на вопросы) его не меняют.
- Виды решений (формулировки в тредах свободные, важен смысл):
  - **ошибка** — прежнее поведение неверно: противоречит описанию, так не задумывалось; его
    исправляют;
  - **намеренное изменение** — прежнее поведение было задумано и работало как описано, но его
    сознательно меняют; тем, кто на него полагался, придётся менять свой код;
  - **расширение** — раньше такой случай не поддерживался (вызов падал с ошибкой), теперь
    поддерживается, а всё, что работало, работает как прежде;
  - **регрессия из #M** — поломку внёс конкретный более ранний коммит; мейнтейнер называет
    тред #M этого коммита;
  - **снаружи не видно** — (для кода вне публичного API) правка не меняет результат ни одной
    публичной функции;
  - **откат** — изменение решено отменить целиком отдельным коммитом.

## Что входит в публичный API

1. **Публичные функции** — ровно имена, перечисленные в `__all__` файла `tablo/__init__.py`
   (включая псевдонимы вида `old_name = new_name`, если старое имя стоит в `__all__`).
   Больше ничего публичным не считается: ни функции без подчёркивания, которых нет в
   `__all__` (даже если они импортируются в `tablo/__init__.py` или упоминаются в
   документации), ни функции `tablo/_util.py` и `tablo/cli.py`, ни приватные `_имена`.
2. **Документированные флаги CLI** — флаги, которые одновременно объявлены в парсере
   (`build_parser` в `tablo/cli.py`) и описаны отдельным разделом (заголовок вида ``### `--флаг` ``) в
   `docs/cli.md`. Флаг, который есть в парсере, но не описан в `docs/cli.md`, считается
   экспериментальным и в публичный API не входит. Значение флага по умолчанию — это
   `default=` в парсере.
3. **Сигнатура** публичной функции — имена параметров, их порядок, обязательность и
   значения по умолчанию. Аннотации типов в сигнатуру не входят.

## Категории изменений

Каждый коммит относится ровно к одной категории. Если в коммите несколько изменений,
берётся старшая: **breaking > feature > fix > internal**.

### А. Категорию определяет дифф

Для этих изменений решение в треде не учитывается, что бы там ни говорилось.

**breaking** — несовместимое изменение API:

- имя пропало из `__all__`: функция удалена, исключена из `__all__` (даже если сама
  функция осталась в модуле), переименована без сохранения старого имени, удалён
  псевдоним;
- у публичной функции удалён или переименован параметр, изменён порядок параметров,
  добавлен обязательный параметр (без значения по умолчанию) или новый параметр вставлен
  не в конец списка;
- документированный флаг CLI удалён из парсера, переименован, или из `docs/cli.md` удалён
  его раздел (флаг перестал быть документированным).

**feature** — новая возможность API:

- в `__all__` появилось новое имя: новая функция; уже существующая функция добавлена в
  `__all__`; функция переименована, а старое имя оставлено в `__all__` как псевдоним;
- у публичной функции добавлен новый необязательный параметр (со значением по умолчанию)
  в конец списка параметров;
- появился новый документированный флаг: флаг добавлен в парсер вместе с разделом в
  `docs/cli.md`, или у уже существующего флага появился раздел в `docs/cli.md`.

**internal**:

- нейтральные правки любых функций, не меняющие поведение: комментарии, docstring,
  аннотации типов, переименование локальной переменной (последовательно по всему телу),
  вынос литерала в приватную константу модуля с тем же значением, перенос функции в
  другой модуль без изменения её имени, сигнатуры и тела;
- добавление функции, которая не попала в `__all__`;
- недокументированные флаги: добавление, удаление, смена значения по умолчанию;
- тесты, документация (кроме появления или исчезновения раздела флага в `docs/cli.md`),
  README, CI, конфигурация.

### Б. Категорию определяет решение в треде

1. Изменён исполняемый код в теле публичной функции, а её сигнатура не изменилась:
   ошибка → **fix**; намеренное изменение → **breaking**; расширение → **feature**;
   регрессия из #M → **internal**, если коммит треда #M входит в тот же релиз, что и
   исправление (пользователи поломку не видели), иначе **fix**.
2. Изменён исполняемый код вне публичного API (приватные функции, функции вне `__all__`,
   модуль `_util.py`): снаружи не видно → **internal**; если мейнтейнеры решили, что правка
   меняет результат публичной функции, — как в п. 1 по виду решения (ошибка → fix,
   намеренное изменение → breaking, расширение → feature).
3. Изменено значение по умолчанию у параметра публичной функции или у документированного
   флага CLI: **breaking**; но если мейнтейнеры решили, что прежнее значение было ошибкой,
   — **fix**.
4. Откат: если изменение из п. 1–3 откатили отдельным коммитом в том же релизе, то и
   изменение, и откат — **internal** (в релизе от них ничего не осталось). Возврат прежнего
   поведения, изменённого в более раннем релизе, — обычное изменение по п. 1 со своим
   решением в треде.

## Версии

- Мы следуем semver. Первый релиз — `v1.0.0`; коммиты до него (включительно) не
  классифицируются, и его раздел в CHANGELOG состоит только из заголовка.
- Релиз N включает коммиты после точки релиза N−1 до точки релиза N включительно.
- Если в релизе есть хотя бы один breaking-коммит — увеличивается MAJOR, а MINOR и PATCH
  обнуляются; иначе, если есть feature — увеличивается MINOR, PATCH обнуляется; иначе
  увеличивается PATCH (даже если в релизе только internal-коммиты).
- Коммиты после последней точки релиза ещё не выпущены и в CHANGELOG не попадают.

## Формат CHANGELOG.md

```
# Changelog

## v2.0.0 (2024-01-15)

### Breaking
- 1a2b3c4 Первая строка сообщения коммита
### Features
- 5d6e7f8 Первая строка сообщения коммита
### Fixes
- 9a8b7c6 Первая строка сообщения коммита

## v1.0.0 (2023-03-01)
```

- Релизы — от новых к старым; в заголовке версия с префиксом `v` и дата публикации релиза
  (из RELEASES.md) в формате `YYYY-MM-DD`.
- Подразделы — в порядке Breaking, Features, Fixes; пустые подразделы не пишутся.
  Internal-коммиты не перечисляются.
- Внутри подраздела коммиты идут в хронологическом порядке, по одному на строку:
  `- <первые 7 символов хеша> <первая строка сообщения>`.
"""

PYPROJECT = """\
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "tablo"
description = "Small helpers for parsing and printing delimited tables"
requires-python = ">=3.10"
dynamic = ["version"]

[project.scripts]
tablo = "tablo.cli:main"
"""

CLI_TEMPLATE = '''\
"""Command-line interface: ``python -m tablo FILE``."""

import argparse
import sys


def build_parser():
    parser = argparse.ArgumentParser(prog="tablo", description="Pretty-print delimited text tables.")
    parser.add_argument("path", nargs="?", default="-", help="input file, '-' for stdin")
@@ARGS@@
    return parser


def _read_rows(text, sep):
    return [line.split(sep) for line in text.splitlines() if line.strip()]


def _render(rows, width):
    out = []
    for row in rows:
        line = " | ".join(cell.strip() for cell in row)
        out.append(line[:width] if width else line)
    return "\\n".join(out)


def main(argv=None):
    args = build_parser().parse_args(argv)
    options = vars(args)
    if args.path == "-":
        text = sys.stdin.read()
    else:
        with open(args.path, encoding=options.get("encoding") or "utf-8") as fh:
            text = fh.read()
    rows = _read_rows(text, options.get("sep") or options.get("delimiter") or ",")
    print(_render(rows, options.get("width") or 0))
    return 0
'''

CLI_DOC_INTRO = """\
# Командная строка

Команда `python -m tablo [ФАЙЛ] [ФЛАГИ]` читает таблицу из файла (или из stdin, если файл
не указан или равен `-`) и печатает её в выбранном формате. Ниже перечислены флаги, на
которые можно полагаться: их поведение и значения по умолчанию меняются только в новых
мажорных версиях.

## Флаги
"""

CLI_DOC_OUTRO = """
## Коды возврата

`0` — успех, `2` — ошибка в аргументах командной строки (сообщение печатает argparse).
"""

GUIDE_INTRO = """\
# Руководство по tablo

tablo — небольшая библиотека для разбора и печати табличных текстовых выгрузок: CSV с
разными разделителями, отчёты из учётных систем, выгрузки из таблиц. В руководстве собраны
примеры использования отдельных функций. Список того, что входит в публичный API, — в
`CONTRIBUTING.md`.
"""

README_BASE = """\
# tablo

Small helpers for parsing and printing delimited tables.

```python
import tablo

rows = tablo.parse_table(open("report.csv").read())
```

Command line: `python -m tablo report.csv --width 100`. See `docs/cli.md` and `docs/guide.md`.
"""

CI_TEMPLATE = """\
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: [@@VERS@@]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
@@STEPS@@      - run: python -m unittest discover -s tests
"""

# ---------------------------------------------------------------------------
# setup / gold / check
# ---------------------------------------------------------------------------


def _releases_md(data: dict) -> str:
    commits = data["commits"]
    points = list(data["points"])
    dates = list(data["dates"])
    rows = []
    for k, p in enumerate(points):
        if k == 4:
            p_show, d_show = data["planned_point"], data["planned_date"]
        else:
            p_show, d_show = p, dates[k]
        rows.append(f"| {k + 1} | {commits[p_show].sha[:7]} | {d_show} |")
    actual = commits[points[4]].sha[:7]
    planned = commits[data["planned_point"]].sha[:7]
    return (
        "# Релизы tablo\n\n"
        "Номера версий в репозитории никогда не проставлялись: ни тегов, ни поля version.\n"
        "Ниже — точки, с которых собирались релизы (короткий хеш последнего коммита, вошедшего\n"
        "в релиз), и даты публикации. Первый релиз считается версией v1.0.0; дальше версии\n"
        "вычисляются по правилам из CONTRIBUTING.md.\n\n"
        "| Релиз | Коммит | Дата публикации |\n|---|---|---|\n" + "\n".join(rows) + "\n\n"
        "## Примечания\n\n"
        f"- Таблицу заполнили по плану релизов. Релиз 5 планировали собрать с коммита {planned},\n"
        f"  но сборку задержали: фактически релиз 5 собран с коммита {actual} и опубликован\n"
        f"  {dates[4]}. Строку в таблице исправить забыли — верны данные этого примечания.\n"
        "- Всё, что закоммичено после точки релиза 8, ещё не выпущено.\n"
    )


def _fast_import_stream(data: dict) -> bytes:
    out = bytearray()
    prev: dict[str, str] = {}
    for n, c in enumerate(data["commits"], 1):
        name, _, email = c.author.partition(" <")
        ident = f"{name} <{email} {c.ts} {TZ}".encode()
        msg = c.message.encode()
        out += b"commit refs/heads/main\nmark :%d\n" % n
        out += b"author " + ident + b"\ncommitter " + ident + b"\n"
        out += b"data %d\n" % len(msg) + msg + b"\n"
        if n > 1:
            out += b"from :%d\n" % (n - 1)
        for path in sorted(prev):
            if path not in c.files:
                out += f"D {path}\n".encode()
        for path, text in sorted(c.files.items()):
            if prev.get(path) != text:
                raw = text.encode()
                out += f"M 100644 inline {path}\n".encode() + b"data %d\n" % len(raw) + raw + b"\n"
        prev = c.files
    out += b"done\n"
    return bytes(out)


# ---------------------------------------------------------------------------
# LLM rewriting of the threads (scripts/rewrite_long_texts.py)
# ---------------------------------------------------------------------------

_SKEL_RE = re.compile(r"^(?:# (#\d+):|### (@\S+ · \d{4}-\d{2}-\d{2})\s*$)", re.M)
DEC_RU = {"bug": "ошибка", "intended": "намеренное изменение", "extension": "расширение",
          "hidden": "снаружи не видно", "regression": "регрессия", "revert": "откат", "none": "нет решения"}
_ROLE_RU = {
    "open": "исходное сообщение треда (симптом или описание PR), без решения",
    "chat": "обсуждение, без решения",
    "review": "ревью/служебное, без решения",
    "think": "мейнтейнер пишет, что решение будет позже, — само по себе не решение",
    "claim": "мнение НЕ мейнтейнера «{dec}» — не решение",
    "late": "мнение НЕ мейнтейнера «{dec}» уже после итогового решения — не решение",
    "maria_pre": "@mkuz ДО 2023-06-01 (ещё не мейнтейнер) формулирует как решение «{dec}» — это не решение",
    "first": "решение мейнтейнера «{dec}», которое позже в треде отменено",
    "final": "ИТОГОВОЕ решение мейнтейнера «{dec}»",
    "note": "замечание мейнтейнера о виде изменения «{dec}» (последнее такое замечание = итог треда)",
    "quote": "цитата более раннего сообщения со строкой «>», сама по себе не новое решение",
    "merge": "служебное: изменение влито",
    "close": "служебное: закрытие треда",
}


def _skeleton(text: str) -> list[str]:
    return [a or b for a, b in _SKEL_RE.findall(text)]


def _thread_text(t: dict) -> str:
    """Stored rewrite of the thread, unless it lost the thread number or message headers."""
    text = rewritten(TASK_ID, f"issue_{t['n']}", t["text"])
    if text != t["text"] and _skeleton(text) != _skeleton(t["text"]):
        return t["text"]
    return text


def _change_ru(c: Commit, data: dict) -> str:
    f = c.fields
    if c.group == "D":
        what = f"--{f['flag']}" if "flag" in f else f"параметра {f['param']} функции {f['module']}.{f['name']}"
        return f"меняет значение по умолчанию {what} с {f.get('old')} на {f.get('new')}"
    if c.group == "P":
        return (f"меняет код непубличной функции {f['module']}.{f['name']} (случай «{f['what']}»); "
                f"в треде обсуждается её влияние на публичную {c.via}")
    if c.kind == "RO_revert":
        ref = data["commits"][c.ref].thread
        return f"возвращает прежнее поведение {f['module']}.{f['name']}, изменённое в треде #{ref}"
    if c.group in ("X", "RV"):
        return f"меняет код тела публичной функции {f['module']}.{f['name']} (случай «{f['what']}»)"
    return "изменение, категория которого определяется диффом (тред на неё не влияет): " + c.subject


def _rewrite_items() -> list[dict]:
    data = build()
    items = []
    for n, t in sorted(data["threads"].items()):
        c = data["commits"][t["commits"][0]]
        lines = []
        for k, m in enumerate(t["msgs"], 1):
            role = _ROLE_RU[m["role"]].format(dec=DEC_RU.get(m["dec"] or "none"))
            if m["role"] == "final" and m["dec"] == "regression":
                role += f" (регрессию внёс тред #{t['ref']})"
            lines.append(f"{k}. @{m['h']} · {m['date']} — {role}")
        final = DEC_RU[t["final"]] + (f" из #{t['ref']}" if t["final"] == "regression" else "")
        brief = (
            f"Тред #{n}. Коммит(ы) этого треда: {_change_ru(c, data)}.\n"
            f"Итоговое решение мейнтейнеров в треде: {final}. Независимый читатель, знающий только правила "
            "CONTRIBUTING, должен однозначно прийти к этому же итогу.\n"
            "Сообщения по порядку и их роли:\n" + "\n".join(lines)
        )
        items.append({"key": f"issue_{n}", "draft": t["text"], "brief": brief, "final": t["final"],
                      "ref": t["ref"], "skeleton": _skeleton(t["text"])})
    return items


def _contrib_threads() -> str:
    a = CONTRIBUTING.index("## Треды и решения мейнтейнеров")
    b = CONTRIBUTING.index("## Что входит в публичный API")
    return CONTRIBUTING[a:b].strip()


_WRITER_SYSTEM = (
    "Ты переписываешь выгруженные из трекера треды обсуждений небольшой Python-библиотеки tablo так, "
    "чтобы они звучали как настоящая переписка разработчиков и пользователей: живой язык, у каждого "
    "участника свой тон, разная длина сообщений, уместные подробности. Не используй обороты черновика "
    "дословно — перескажи каждое сообщение своими словами. Общая длина — как у черновика (±30 %).\n\n"
    "Жёсткие требования:\n"
    "- строку «# #N: …» оставь с тем же номером (название после двоеточия можно перефразировать), строку "
    "«Статус: …» и все строки «### @ник · дата» сохрани в точности, в том же порядке, с разделителями «---»;\n"
    "- каждое сообщение остаётся у того же автора и с той же ролью; решения мейнтейнеров формулируй своими "
    "словами, но однозначно по смыслу, без новых решений и без потери существующих; отменённое решение "
    "должно остаться явно отменённым; мнения не-мейнтейнеров остаются мнениями; цитаты — цитатами со "
    "строками «>»;\n"
    "- сохрани имена функций, модулей, параметров и флагов в обратных кавычках, номера тредов #N, значения "
    "по умолчанию и блоки кода.\n\n"
    "Правила проекта о тредах и решениях:\n" + _contrib_threads()
)


def _judge_messages(item: dict, text: str) -> list[dict]:
    item["_skel_ok"] = _skeleton(text) == item["skeleton"]
    return [
        {"role": "system", "content": _contrib_threads() + "\n\nПрочитай тред и определи по этим правилам "
         "итоговое решение мейнтейнеров. Ответь только JSON-объектом вида "
         '{"decision": "bug|intended|extension|hidden|regression|revert|none", "ref": null}: bug — ошибка, '
         "intended — намеренное изменение, extension — расширение, hidden — снаружи не видно, regression — "
         "регрессия (тогда ref — номер треда, который её внёс, числом), revert — откат, none — мейнтейнеры "
         "не высказывались о виде изменения."},
        {"role": "user", "content": text},
    ]


def _judge_accept(item: dict, reply: str) -> tuple[bool, str]:
    if not item.get("_skel_ok", True):
        return False, "нарушены строки «# #N:» или «### @ник · дата»: сохрани их в точности и в том же порядке"
    match = re.search(r"\{.*\}", reply, re.S)
    try:
        got = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        got = None
    if not isinstance(got, dict):
        return False, "независимый читатель не смог определить итоговое решение"
    dec = str(got.get("decision", "")).strip()
    ref = got.get("ref")
    if dec == item["final"] and (dec != "regression" or str(ref).lstrip("#") == str(item["ref"])):
        return True, ""
    return False, (
        f"независимый читатель по правилам получил итог «{DEC_RU.get(dec, dec)}»"
        + (f" из #{ref}" if dec == "regression" else "")
        + f", а должно быть «{DEC_RU[item['final']]}»"
        + (f" из #{item['ref']}" if item["final"] == "regression" else "")
        + ": сделай роли сообщений и итоговое решение однозначнее, не меняя их смысла"
    )


REWRITE = {
    "items": _rewrite_items,
    "writer_system": _WRITER_SYSTEM,
    "judge_messages": _judge_messages,
    "judge_accept": _judge_accept,
}


def setup(ws: Path) -> None:
    data = build()
    git(ws, "init", "-q", "--object-format=sha1")
    env = {**__import__("os").environ, **_GIT_ENV, "HOME": str(ws)}
    subprocess.run(["git", "fast-import", "--quiet", "--done"], cwd=ws, env=env,
                   input=_fast_import_stream(data), check=True, capture_output=True)
    git(ws, "reset", "-q", "--hard", "main")
    head = git(ws, "rev-parse", "HEAD").strip()
    assert head == data["commits"][-1].sha, "git hash mismatch"
    write(ws, "RELEASES.md", _releases_md(data))
    for n, t in data["threads"].items():
        write(ws, f"issues/{n}.md", _thread_text(t))


def gold(ws: Path) -> None:
    write(ws, "CHANGELOG.md", _changelog(build()))


_HEAD_RE = re.compile(r"^##\s+v?(\d+)\.(\d+)\.(\d+)\s*\(\s*(\d{4}-\d{2}-\d{2})\s*\)\s*$")
_SEC_RE = re.compile(r"^###\s+(\w+)")
_ITEM_RE = re.compile(r"^\s*[-*]\s+`?([0-9a-fA-F]{7,40})`?\b")
_SEC_MAP = {"breaking": "breaking", "features": "feature", "feature": "feature", "fixes": "fix", "fix": "fix"}


def check(ws: Path) -> str:
    data = build()
    commits = data["commits"]
    text = read_text(ws, "CHANGELOG.md")
    got_versions = []
    listed: list[tuple[str, str, str]] = []  # (date, section, hash)
    date = None
    section = None
    for line in text.splitlines():
        if m := _HEAD_RE.match(line.strip()):
            date = m.group(4)
            got_versions.append((f"{int(m.group(1))}.{int(m.group(2))}.{int(m.group(3))}", date))
            section = None
            continue
        if m := _SEC_RE.match(line.strip()):
            section = _SEC_MAP.get(m.group(1).lower(), "?")
            continue
        if (m := _ITEM_RE.match(line)) and date is not None:
            listed.append((date, section or "?", m.group(1).lower()))
    expected_versions = _expected_versions(data)
    assert got_versions, "в CHANGELOG.md не найдено ни одного заголовка релиза нужного формата"
    ok_versions = len(set(got_versions) & set(expected_versions))
    assert len(got_versions) == len(expected_versions) and ok_versions == len(expected_versions), (
        f"версии и даты релизов: совпало {ok_versions} из {len(expected_versions)}"
        f" (в файле {len(got_versions)} заголовков)"
    )
    date_of_release = {k + 1: d for k, (_, d) in enumerate(expected_versions)}
    by_prefix = {c.sha[:7]: i for i, c in enumerate(commits)}
    predicted: dict[int, tuple[str, str]] = {}
    extra = 0
    for d, sec, h in listed:
        idx = by_prefix.get(h[:7])
        if idx is None or not commits[idx].sha.startswith(h) or idx in predicted:
            extra += 1
            continue
        predicted[idx] = (d, sec)
    total = correct = 0
    errors = []
    for idx, c in enumerate(commits):
        if c.release < 2 or c.release > 8:
            if idx in predicted:
                extra += 1
            continue
        total += 1
        want_date = date_of_release[c.release]
        got = predicted.get(idx, (want_date, "internal"))
        if got == (want_date, c.cat):
            correct += 1
        else:
            errors.append(c.sha[:7])
    return require_share(total + extra, correct, min_share=0.95, what="классификация коммитов",
                         errors=[f"коммит {e} классифицирован неверно" for e in errors])


# ---------------------------------------------------------------------------
# Near misses
# ---------------------------------------------------------------------------

_KW_BREAK = re.compile(r"\b(remove|drop|delete|rename|breaking|mandatory|requires|stop)\b|->", re.I)
_KW_FEAT = re.compile(r"\b(add|new|introduce|export|public|support|document|option)\b", re.I)
_KW_FIX = re.compile(r"\b(fix|bug|handle|correct)\b", re.I)


def _message_class(c: Commit) -> str:
    s = c.subject
    if _KW_BREAK.search(s):
        return "breaking"
    if _KW_FEAT.search(s):
        return "feature"
    if _KW_FIX.search(s):
        return "fix"
    return "internal"


def nm_ast_diff_only(ws: Path) -> None:
    """An exact AST diff of every commit, threads ignored: body change = fix, private = internal, default = breaking."""
    write(ws, "CHANGELOG.md", _changelog(build(), classify=lambda c: c.diff_cat))


_THREAD_KW = (
    ("breaking", r"ломающ|несовместим|breaking|мажор|намеренн|сознательн"),
    ("feature", r"расширени|нов\w* возможност|фич|feature|функциональност"),
    ("fix", r"\bбаг|ошибк|дефект|исправлени|\bfix"),
    ("internal", r"внутренн|не видно|не заметит|регресси|откат"),
)


def nm_thread_keywords(ws: Path) -> None:
    """Read each commit's thread, take the category of the last category keyword; the diff is ignored."""
    pattern = re.compile("|".join(f"(?P<{cat}>{rx})" for cat, rx in _THREAD_KW), re.I)

    def classify(c: Commit) -> str:
        path = ws / "issues" / f"{c.thread}.md"
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        hits = list(pattern.finditer(text))
        return hits[-1].lastgroup if hits else "internal"

    write(ws, "CHANGELOG.md", _changelog(build(), classify=classify))


def _by_thread_decision(pick):
    def classify(c: Commit) -> str:
        data = build()
        if c.group not in ("X", "P", "D", "RV"):
            return c.cat
        dec = pick(data["threads"][c.thread], c)
        if dec in (None, "none"):
            return c.cat
        if dec == "revert":
            return "internal"
        return _type_cat(c, dec, c.ref, data["commits"])
    return classify


def nm_first_decision(ws: Path) -> None:
    """Take the first maintainer decision in each thread and ignore later reversals and reopenings."""
    write(ws, "CHANGELOG.md", _changelog(build(), classify=_by_thread_decision(lambda t, c: t["first"])))


def nm_last_voice(ws: Path) -> None:
    """Take the last category opinion in the thread, whoever wrote it (users, @mkuz before 2023-06-01)."""
    write(ws, "CHANGELOG.md", _changelog(build(), classify=_by_thread_decision(lambda t, c: t["voice"])))


def nm_regression_is_always_fix(ws: Path) -> None:
    """Treat every regression fix as fix, even when the regression never left the release."""
    write(ws, "CHANGELOG.md", _changelog(build(), classify=lambda c: "fix" if c.dec == "regression" else c.cat))


def nm_revert_pairs_by_decision(ws: Path) -> None:
    """Classify a change reverted in the same release by its first decision, and the revert as fix."""
    write(ws, "CHANGELOG.md", _changelog(build(), classify=lambda c: (
        (DEC_CAT[c.first] if c.kind == "RV_change" else "fix") if c.group == "RV" else c.cat)))


def nm_message_classifier(ws: Path) -> None:
    """Classify commits by keywords in their messages."""
    write(ws, "CHANGELOG.md", _changelog(build(), classify=_message_class))


def nm_ignore_release_correction(ws: Path) -> None:
    """Use the planned release-5 point from the table instead of the corrected one."""
    data = build()
    points = list(data["points"])
    dates = list(data["dates"])
    points[4] = data["planned_point"]
    dates[4] = data["planned_date"]
    write(ws, "CHANGELOG.md", _changelog(data, points=points, dates=dates))


def nm_any_body_change_is_fix(ws: Path) -> None:
    """AST-diff approach: any change of a public body (renamed locals, extracted literals) = fix."""
    write(ws, "CHANGELOG.md", _changelog(
        build(), classify=lambda c: "fix" if c.kind in ("I_rename_local", "I_extract_literal") else c.cat))


_NAIVE_PUBLIC = {
    "I_add_unexported": "feature", "I_flag_hidden_add": "feature", "I_flag_hidden_default": "breaking",
    "F_export_existing": "internal", "F_flag_document": "internal",
    "B_unexport": "internal", "B_flag_undocument": "internal",
}


def nm_every_def_is_public(ws: Path) -> None:
    """Treat every non-underscore def and every parser flag as public, ignoring __all__ and docs/cli.md."""
    write(ws, "CHANGELOG.md", _changelog(build(), classify=lambda c: _NAIVE_PUBLIC.get(c.kind, c.cat)))


def nm_semver_without_reset(ws: Path) -> None:
    """Bump MAJOR/MINOR without resetting the lower components."""
    write(ws, "CHANGELOG.md", _changelog(build(), reset=False))


NEAR_MISSES = [
    nm_ast_diff_only,
    nm_thread_keywords,
    nm_first_decision,
    nm_last_voice,
    nm_regression_is_always_fix,
    nm_revert_pairs_by_decision,
    nm_message_classifier,
    nm_ignore_release_correction,
    nm_any_body_change_is_fix,
    nm_every_def_is_public,
    nm_semver_without_reset,
]

PROMPT = """\
В текущем каталоге — git-репозиторий библиотеки tablo (история на несколько сотен \
коммитов). Версии в репозитории никогда не проставлялись. Нужно восстановить историю \
релизов и написать CHANGELOG.md в корне репозитория.

- Точки релизов и даты публикации — в RELEASES.md (обязательно прочитай примечания).
- Что считается публичным API, как относить коммит к категориям breaking / feature / fix / \
internal, как вычислять версии и в каком формате писать CHANGELOG.md — в CONTRIBUTING.md.
- Каждый коммит ссылается на тред обсуждения из трекера; архив тредов — в каталоге issues/ \
(issues/N.md для #N).

Сообщения коммитов писались небрежно и часто не отражают суть изменения. Категория коммита \
определяется по правилам CONTRIBUTING.md: по его диффу, а для части изменений — по итоговому \
решению мейнтейнеров в треде. Треды написаны свободным языком, решения в них пересматривались, \
поэтому важен каждый коммит и каждый тред. Историю репозитория и файлы в issues/ не меняй. \
Результат — файл CHANGELOG.md со всеми релизами (от v1.0.0 до последнего) в формате из \
CONTRIBUTING.md.
"""

TASK = long_task(
    id="task_402_changelog_semver",  # registry id; TASK_ID stays the generator seed
    name="CHANGELOG и semver по диффам истории git",
    prompt=PROMPT,
    setup=setup,
    gold=gold,
    check=check,
    tags=("git", "semver", "reading", "classification"),
)

