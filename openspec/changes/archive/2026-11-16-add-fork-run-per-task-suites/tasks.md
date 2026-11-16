# Tasks: add-fork-run-per-task-suites

## Диагностика
- [x] Подтвердить пустую таблицу задач у Shot List/Light строки
  (`grep -c task_ docs/fork-run-openclaw-gigachat-3-5-432b-a28b-thinking-off.html` == 0)
- [x] Подтвердить фильтр (a): `classify_wave('oc_shotlist_giga35base_off_20261113') is None`
- [x] Подтвердить фильтр (b): `classify_task_wave('task_01_create_hello') is None`
- [x] Подтвердить, что Shot List и Medium того же конфига дают одинаковый `row_id`
- [x] Подтвердить, что OpenClaw-артефакты пишут per-task записи в `tasks`, Pi — в `results`

## Реализация
- [x] `build_fork_pages.build_model`: собирать per-task детали из `results` **или** `tasks`
- [x] `build_fork_pages`: второй проход по Shot List/Light артефактам → в `_page_details`
      только для уже существующих строк (без новых строк и без изменения агрегатов)
- [x] `_task_entry`: сохранять записи без medium-волны, помечая меткой набора (SUITE_LABELS)
- [x] `render_run_page`: колонка Wave показывает `label` (метка набора как fallback)
- [x] Сортировка задач: известные волны по `WAVE_ORDER`, затем записи без волны

## Тесты
- [x] `tests/test_fork_run_page_tasks.py`: синтетический Shot List артефакт →
      страница перечисляет его задачи (RED→GREEN)
- [x] Проверить, что опубликованный `docs/fork-medium90-results.json` не изменился

## QA / верификация
- [x] `npx -y @fission-ai/openspec@1.16.0 validate --all --strict` → exit 0
- [x] `python3 scripts/build_fork_pages.py` затем `python3 scripts/build_fork_pages.py --check` → exit 0
- [x] `python3 -m pytest tests/test_fork_wave_task_union.py tests/test_fork_publish_orchestrator.py tests/test_fork_run_page_tasks.py -q` → green
- [x] Spot-check: `grep -c task_ docs/fork-run-openclaw-gigachat-3-5-432b-a28b-thinking-off.html` > 0
- [x] ruff чистый для новых правок (`ruff check scripts/build_fork_pages.py tests/test_fork_run_page_tasks.py`)
