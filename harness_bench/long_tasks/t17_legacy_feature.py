"""long_17_legacy_feature — implement a feature in a legacy warehouse app.

The workspace holds ``stockroom``: a legacy Python inventory application (~80
modules, JSON file as the database, argparse CLI, text/CSV reports, CSV import and
export, dead code, price logic duplicated in three places), its unittest suite and
``FEATURE.md`` — a precise spec of lots with expiry dates (FEFO), partial returns
and multi-currency pricing/valuation by historical rates.

The data lives in ``_data_t17/`` (every file carries an extra ``.txt`` suffix so
that the repository's pytest/ruff never pick it up):

* ``app/``    — the workspace as handed to the agent;
* ``gold/``   — overlay of the files changed/added by a real implementation;
* ``hidden/`` — behavioural acceptance tests (Python API + CLI subprocess).

``check`` runs a pristine copy of the visible tests (all must pass) and the hidden
acceptance tests (at least 95 % must pass).
"""

from __future__ import annotations

import functools
from pathlib import Path

from .common import long_task, run_hidden_pytest, write

TASK_ID = "long_17_legacy_feature"
DATA = Path(__file__).parent / "_data_t17"
SUFFIX = ".txt"
N_VISIBLE = 132
N_ACCEPTANCE = 81
MIN_SHARE = 0.95


@functools.cache
def _tree(name: str) -> tuple[tuple[str, str], ...]:
    root = DATA / name
    out = []
    for path in sorted(root.rglob("*" + SUFFIX)):
        rel = path.relative_to(root).as_posix()[: -len(SUFFIX)]
        out.append((rel, path.read_text(encoding="utf-8")))
    return tuple(out)


def app_files() -> dict[str, str]:
    return dict(_tree("app"))


def gold_files() -> dict[str, str]:
    return dict(_tree("gold"))


def hidden_files() -> dict[str, str]:
    return dict(_tree("hidden"))


def visible_test_files() -> dict[str, str]:
    return {rel: text for rel, text in _tree("app") if rel.startswith("tests/")}


PROMPT = (
    "В рабочей папке — унаследованное приложение складского учёта stockroom (пакет stockroom/,"
    " около 80 модулей разного возраста и стиля, база — JSON-файл, командная строка"
    " `python3 -m stockroom`, отчёты, печатные формы, импорт/экспорт CSV), его тесты в tests/"
    " (запуск: `python3 -m unittest discover -s tests`) и документация в docs/ и README.md.\n\n"
    "Нужно реализовать доработку, описанную в FEATURE.md: партии со сроками годности и расход"
    " по FEFO, частичные возвраты от покупателей, цены и оценку остатков в иностранной валюте по"
    " историческим курсам. Доработка затрагивает модели и хранилище (новая версия схемы базы и"
    " миграция старых файлов), бизнес-операции, расчёт цен, печатные формы, отчёты, импорт и"
    " экспорт CSV и командную строку. FEATURE.md — точная спецификация: имена функций и"
    " параметров, форматы вывода и правила расчёта нужно соблюсти буквально.\n\n"
    "Требования: все существующие тесты в tests/ должны проходить без изменений (свои тесты"
    " добавлять можно); только стандартная библиотека Python, совместимость с Python 3.9."
    " Результат проверяется скрытыми приёмочными тестами через Python API и командную строку,"
    " а также исходной копией существующих тестов. Кодовая база большая и старая — прежде чем"
    " менять, разберись, где и как считается то, что затрагивает доработка."
)


def setup(ws: Path) -> None:
    for rel, text in _tree("app"):
        write(ws, rel, text)


def gold(ws: Path) -> None:
    for rel, text in _tree("gold"):
        write(ws, rel, text)


def check(ws: Path) -> str:
    assert (ws / "stockroom" / "__init__.py").is_file(), "нет пакета stockroom/"
    passed, failed, out = run_hidden_pytest(ws, visible_test_files(), timeout=110)
    assert passed == N_VISIBLE and not failed, (
        f"исходные тесты проекта: прошло {passed} из {N_VISIBLE}, упало {failed}"
    )
    passed, failed, out = run_hidden_pytest(ws, hidden_files(), timeout=110)
    share = passed / N_ACCEPTANCE
    assert share + 1e-9 >= MIN_SHARE, (
        f"приёмочные тесты: прошло {passed} из {N_ACCEPTANCE} ({share:.0%}), нужно не меньше {MIN_SHARE:.0%}"
    )
    return f"исходные тесты {N_VISIBLE}/{N_VISIBLE}, приёмочные {passed}/{N_ACCEPTANCE}"


TASK = long_task(
    id="task_408_legacy_feature",  # registry id; TASK_ID stays the generator seed
    name="Доработка унаследованного складского приложения",
    prompt=PROMPT,
    setup=setup,
    gold=gold,
    check=check,
    tags=("code", "legacy", "feature", "reading"),
)


# --------------------------------------------------------------------------
# Near misses
# --------------------------------------------------------------------------


def _replace(ws: Path, rel: str, old: str, new: str) -> None:
    path = ws / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"near miss patch target not found in {rel}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def _restore(ws: Path, rel: str) -> None:
    write(ws, rel, app_files()[rel])


def invoice_path_not_updated(ws: Path) -> None:
    """Currency conversion added everywhere except the invoice's own price code."""
    _restore(ws, "stockroom/documents/invoice.py")


def sales_path_not_updated(ws: Path) -> None:
    """Sales report keeps the foreign price unconverted (partial returns handled)."""
    _replace(
        ws,
        "stockroom/reports/sales.py",
        "        price = q2(price * get_rate(db, currency, date))\n",
        "        pass\n",
    )


def fifo_instead_of_fefo(ws: Path) -> None:
    """Lots added, but consumption stays FIFO by receipt date."""
    _replace(
        ws,
        "stockroom/costing.py",
        '    return (0 if expiry else 1, expiry or "", layer["date"], layer["doc"], int(layer.get("line", 0)))',
        '    return (layer["date"], layer["doc"], int(layer.get("line", 0)))',
    )


def expired_lots_shipped(ws: Path) -> None:
    """FEFO order, but expired lots are still shipped."""
    _replace(ws, "stockroom/orders.py", "costing.usable_qty(db, sku, date)", "costing.usable_qty(db, sku)")
    _replace(ws, "stockroom/orders.py", "skip_expired_before=date)", "skip_expired_before=None)")


def no_partial_returns(ws: Path) -> None:
    """Everything except partial returns (the legacy full-return module is kept)."""
    _restore(ws, "stockroom/returns.py")


def breaks_existing_cli_output(ws: Path) -> None:
    """Feature complete, but an existing CLI message format was changed."""
    _replace(ws, "stockroom/cli.py", '"Приход %s проведён: строк %d, сумма %s"', '"Приход %s: %d строк, сумма %s"')


NEAR_MISSES = [
    invoice_path_not_updated,
    sales_path_not_updated,
    fifo_instead_of_fefo,
    expired_lots_shipped,
    no_partial_returns,
    breaks_existing_cli_output,
]
