# Tasks — add-archive-ru-mirror

## 1. Страница RU-зеркала
- [x] 1.1 Создать `docs/archive.ru.html` на основе структуры `docs/archive.html`:
      перевести смысловые блоки на русский, сохранить разделы (`tasks`, `history`,
      секции измерений), числа/версии/имена моделей — как есть.
- [x] 1.2 Шапка/футер — форк-хром (как на `fork.html`), включая футер с репо-ссылками.
- [x] 1.3 Языковой чип «RU · EN» (RU активен; EN → `archive.html`); `<html lang="ru">`.
- [x] 1.4 Плашка-дисклеймер: «перевод; источник — EN-версия апстрима».

## 2. Навигация к зеркалу
- [x] 2.1 Добавить ссылку на `archive.ru.html` в форк-навигацию (`scripts/build_fork_home.py`,
      ключ i18n) и в `FORK.md`/`FORK.en.md`.

## 3. QA / Testing
- [x] 3.1 HTML валиден; все внутренние/внешние ссылки резолвятся (нет 404).
- [x] 3.2 Апстримный `docs/archive.html` **не изменён** (`git diff -- quiet`).
- [x] 3.3 Числа в зеркале совпадают с апстримом (выборочная сверка ≥ 5 значений).
- [x] 3.4 Гейты форка: `build_fork_home.py --check`, `build_fork_pages.py --check`.
- [x] 3.5 `openspec validate add-archive-ru-mirror --strict` → OK.
- [ ] 3.6 Pages: `archive.ru.html` → 200; чип ведёт на `archive.html`.

## 4. Оформление
- [ ] 4.1 PR в отдельной ветке `feat/archive-ru-mirror`; после мержа — `openspec archive`.
