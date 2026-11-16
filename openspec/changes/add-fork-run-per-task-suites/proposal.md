# Proposal: add-fork-run-per-task-suites

## Why

Страницы детализации прогона `docs/fork-run-<id>.html` показывали **пустую** таблицу
задач для строк Shot List / Light. Пример — live-страница
`fork-run-openclaw-gigachat-3-5-432b-a28b-thinking-off.html`: 0 задач, хотя JSON
прогона (`results/oc_shotlist_giga35base_off_20261113.json`) несёт 52 per-task записи.
Medium-строки, у которых артефакты лежат в массиве `results`, работают (40 задач).

Две причины (см. design.md):

- **(a)** `build_model()` пропускает любой артефакт, у которого `classify_wave(stem)`
  равен `None` — у Shot List / Light нет medium-волны, поэтому их артефакты не попадают
  ни в строку, ни в её per-task детализацию.
- **(b)** `_task_entry()` / `classify_task_wave()` отбрасывают каждую запись, чей номер id
  вне medium-волн (392–411, 412–431, 432–471, 512–530) — то есть все задачи Shot List
  (id `task_01…task_65`) и Light.

Плюс `build_model()` читает per-task записи **только** из `results`, тогда как
OpenClaw-раннер пишет их в `tasks`, поэтому medium-строки OpenClaw тоже теряли детали.

## What Changes

1. `scripts/build_fork_pages.py`:
   - per-task DETAIL-список строки собирается из артефактов **всех** наших наборов
     (Shot List, Light, Medium) и из обоих массивов (`results` **или** `tasks`);
   - `_task_entry` SHALL сохранять записи, чей id не отображается на medium-волну,
     помечая их меткой набора (Shot List / Light / Medium) вместо метки волны;
   - `render_run_page` SHALL показывать в колонке Wave метку набора для таких записей.
2. `tests/test_fork_run_page_tasks.py` (новый): синтетический Shot List артефакт →
   `fork-run-` страница перечисляет его задачи (RED→GREEN регресс).

## Capabilities

### Modified Capabilities
- `result-reporting`: страница детализации прогона (`docs/fork-run-<id>.html`) перечисляет
  per-task записи для всех наших наборов, без medium-wave фильтра; колонка Wave
  откатывается к метке набора, когда id не принадлежит известной волне.

## Out of scope

- **`docs/fork-detail-<stem>.html` (генератор `scripts/build_fork_home.py`) — уже
  исправлен** (см. `tests/test_fork_detail_tasks.py`); здесь НЕ трогаем.
- Никаких изменений счёта/агрегатов: `suites`, `wilson`, `usage`, `sources`, `runs`
  и опубликованный `docs/fork-medium90-results.json` остаются прежними — новые записи
  идут **только** в `_page_details` (внутренний, не публикуемый ключ).
- Не меняем `classify_wave` и логику wave-агрегата; не добавляем новые строки-конфигурации.
- Не публикуем/не архивируем изменение в рамках этой задачи (активный change).

Ссылки: Workboard card `f49c38ef`.
