"""Public recipe wording and references must support the scored calculation."""

from __future__ import annotations

import csv
import re
from decimal import Decimal
from pathlib import Path

from harness_bench.long_tasks import t19_recipe_nutrition as nutrition


def _number(value: str) -> Decimal:
    return Decimal(value.replace(",", "."))


def _table(text: str, heading: str) -> dict[str, list[str]]:
    section = text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
    rows = [line.split("|")[1:-1] for line in section.splitlines() if line.startswith("|")]
    return {cells[0].strip(): [cell.strip() for cell in cells[1:]] for cells in rows[2:]}


def test_cocoa_mandatory_extra_matches_public_nutrition(tmp_path: Path) -> None:
    nutrition.TASK.setup(tmp_path)
    recipe = (tmp_path / "cookbook/recipes/r151.md").read_text(encoding="utf-8")
    measures = (tmp_path / "reference/measures.md").read_text(encoding="utf-8")
    with (tmp_path / "reference/nutrition.csv").open(encoding="utf-8", newline="") as stream:
        products = {row["product"]: row for row in csv.DictReader(stream)}
    spoons = _table(measures, "Стакан и ложки")
    densities = _table(measures, "Плотность жидкостей")

    # These quantities come from the rendered public task, not the recipe model.
    milk = re.search(r"^- молоко 1,5 %: ([\d,]+) л$", recipe, re.MULTILINE)
    cocoa = re.search(r"^- какао-порошок: (\d+) ст\. л\.$", recipe, re.MULTILINE)
    sugar = re.search(r"^- сахар: (\d+) ч\. л\.$", recipe, re.MULTILINE)
    extra = re.search(r"^\d+\. Всыпьте ещё (\d+) чайные ложки сахара перед подачей\.", recipe, re.MULTILINE)
    servings = re.search(r"\*\*Выход блюда:\*\* (\d+) порции\.", recipe)
    assert milk and cocoa and sugar and extra and servings
    assert "Если любите слаще" not in recipe
    assert extra[1] == "2"

    grams = {
        "Молоко 1,5 %": _number(milk[1]) * 1000 * _number(densities["Молоко 1,5 %"][0]),
        "Какао-порошок": _number(cocoa[1]) * _number(spoons["Какао-порошок"][2]),
        "Сахар белый": (_number(sugar[1]) + _number(extra[1])) * _number(spoons["Сахар белый"][3]),
    }
    actual = tuple(
        round(float(sum(mass * _number(products[product][field]) / 100
                        for product, mass in grams.items()) / _number(servings[1])), 1)
        for field in ("kcal", "protein", "fat", "carbs")
    )
    assert grams["Сахар белый"] == 48
    assert actual == nutrition.build()["truth"]["r151"]


def test_piece_potato_resolves_through_public_reference(tmp_path: Path) -> None:
    nutrition.TASK.setup(tmp_path)
    recipe = (tmp_path / "cookbook/recipes/r073.md").read_text(encoding="utf-8")
    assert "- 1 средняя картофелина\n" in recipe
    with (tmp_path / "reference/nutrition.csv").open(encoding="utf-8", newline="") as stream:
        matches = [row for row in csv.DictReader(stream)
                   if "картофелина" in row["synonyms"].split("; ")]
    assert len(matches) == 1
    potato = matches[0]
    assert potato["product"] == "Картофель"
    measures = (tmp_path / "reference/measures.md").read_text(encoding="utf-8")
    pieces = _table(measures, "Штучные продукты (масса очищенного продукта, г)")
    assert _number(pieces[potato["product"]][1]) == 150
    assert _number(potato["kcal"]) * 150 / 100 == Decimal("115.5")


def test_recipe_nutrition_gold_still_passes(tmp_path: Path) -> None:
    nutrition.TASK.setup(tmp_path)
    nutrition.TASK.apply_gold(tmp_path)
    result = nutrition.TASK.verify(tmp_path)
    assert result.passed, result.message
    assert "155/155" in result.message
