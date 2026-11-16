# Design: add-fork-run-per-task-suites

## Root cause

### Фильтр (a): артефакт без medium-волны пропускается целиком

`build_model()` в самом начале цикла делает:

```python
wave = classify_wave(stem)
if wave is None:
    continue
```

`classify_wave` знает только medium-волны (`long`/`reflect`/`feasibility`/
`composite_hard`). У Shot List (`*shotlist*`) и Light (`*light*`, `*391*`) волны нет →
`None` → артефакт не попадает ни в `groups`, ни в `wave_artifacts`, ни в `tasks_by_id`.
Поэтому у строки Shot List / Light вообще нет per-task деталей, и `render_run_page`
рендерит «пустую» секцию задач.

### Фильтр (b): запись с id вне medium-волн отбрасывается

`_task_entry()` вызывает `classify_task_wave(task_id)`, который возвращает волну только
для чисел из `TASK_ID_WAVES` (392–411, 412–431, 432–471, 512–530). Для Shot List id вида
`task_01_create_hello` … `task_65_*` (и Light 1…391) результат — `None`, и `_task_entry`
возвращает `None`, т.е. запись выпадает.

### Сопутствующее: чтение только `results`

`build_model()` собирает per-task детали из `data.get("results")`. Pi-раннер пишет
top-level `results`, а OpenClaw-раннер — top-level `tasks`. Из-за этого medium-строки
OpenClaw (например `openclaw-gigachat-3-5-432b-a28b-thinking-off`) тоже показывали 0
задач, хотя их medium-артефакты несут `tasks`.

## Decision: row-grouping для Shot List / Light

Строка конфигурации в `docs/fork-medium90-results.json` задаётся тройкой
`(harness, model, profile)` (`row_id`). Артефакты Shot List / Light **той же**
конфигурации классифицируются в тот же `rid`, что и её medium-артефакты (проверено:
`oc_shotlist_giga35base_off_20261113` и `oc_medium_long_giga35base_off_20261113` оба дают
`openclaw-gigachat-3-5-432b-a28b-thinking-off`). Решение:

- Набор строк (и их `suites`/`usage`/`sources`/`runs`) остаётся **точно** прежним —
  строки создаются только medium-артефактами. Это гарантирует, что опубликованный JSON и
  счёт/агрегаты не меняются.
- Второй проход по артефактам с `classify_wave == None`, но с известным набором
  (`classify_suite` → `shotlist`/`light`), резолвит `(harness, model, profile)` и, только
  если такой `rid` **уже** существует среди строк, добавляет per-task записи в
  `_page_details[rid]`. Конфигурации, у которых есть лишь Shot List / Light, новых строк
  не создают.
- `_page_details` — внутренний ключ (начинается с `_`), он вырезается в `render()` и
  никогда не попадает в `docs/fork-medium90-results.json`. Значит JSON байт-в-байт
  сохраняется, а меняется только HTML страниц.

## Decision: метка набора вместо метки волны

`classify_task_wave` остаётся прежним и продолжает питать wave-агрегат. Меняется только
`_task_entry`: он больше не отбрасывает запись с `wave is None`, а помечает её меткой
набора артефакта-источника (`SUITE_LABELS`: `shotlist`→«Shot List», `light`→«Light»,
`medium`→«Medium»). Поле `label` несёт отображаемое значение, поле `wave` остаётся
`None`/ключом волны для сортировки.

`render_run_page` показывает `task["label"]` в колонке Wave; для medium-записей это
прежняя метка волны, для Shot List / Light — метка набора. HTML-экранирование и
`<details>`-вывод упавших задач сохраняются без изменений.

Порядок строк в таблице: известные волны — в порядке `WAVE_ORDER`, затем записи без волны
(Shot List / Light) — по `task_id`.

## Alternatives considered

- **Добавлять Shot List / Light в wave-агрегат** (сделать `classify_wave` их знающим) —
  отклонено: меняет опубликованные `suites`/счёт и раздувает знаменатель 90.
- **Создавать отдельные строки для Shot List / Light** — отклонено: меняет набор строк
  лидерборда Medium; вне цели.
- **Читать только `results`** — отклонено: теряются per-task детали OpenClaw.
