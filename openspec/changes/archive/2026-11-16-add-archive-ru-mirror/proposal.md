# Change: add-archive-ru-mirror

## Why

`docs/archive.html` — это **апстримная** страница («Measurement archive»,
EN-only, бренд `harness-bench`, ссылки на `ai-forever`). Форк-слой русскоязычный
по умолчанию, но у русскоязычных читателей нет доступа к архиву измерений на
русском. При этом править сам апстримный файл нельзя (fork-doc-layering:
правка upstream-файла даёт конфликты при мерже).

## What Changes

- Добавить **fork-owned** страницу `docs/archive.ru.html` — русское зеркало
  архива измерений (структура и разделы как у апстрима, текст переведён).
- Языковой переключатель на зеркале: **RU (текущий) ⇄ EN** → `archive.html`.
- Ссылка на RU-зеркало из форк-слоя (`docs/fork.html` / `.en`, `FORK.md`).
- **Не редактировать** апстримный `docs/archive.html`.

## Impact

- Affected specs: `localization` (новое требование «RU-зеркало архива измерений»).
- Affected code: новые/изменённые файлы форк-слоя (`docs/archive.ru.html`,
  `scripts/build_fork_home.py` нав‑ссылка, `scripts/fork_i18n.py`), генератор
  ссылки. Апстримный `docs/archive.html` — без изменений.
- Pages: новая страница отдаётся сайтом автоматически.
