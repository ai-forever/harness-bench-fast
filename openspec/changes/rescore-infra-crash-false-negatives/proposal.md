# rescore-infra-crash-false-negatives

## Why

Часть OC-прогонов записала задачу как FAIL **только** из-за инфраструктурного
краша (`MaxListeners`/`incomplete_turn`), а не провала модели. Перепрогон K=3 на
исправленном раннере (issue #462) показал, что эти задачи решаемы — значит
опубликованный скорборд **занижает** результат.

Сырые артефакты `results/*.json` — исторический факт, их не переписываем.
Переоценка вводится как **sidecar-override**, которым пользуются генераторы
страниц (по образцу `fork_ladder_override.py`).

## What Changes

- `scripts/fork_false_negative_override.py` — реестр `(run stem, task_id)` с
  доказательством (`k`, `passed_k`, артефакты перепрогона) и API:
  `apply_summary`, `apply_number`, `task_status`, `rescored_label`.
- `scripts/build_fork_results.py` — `apply_summary` повышает `passed` на 1 за
  каждую переоценённую задачу (**вариант B**; `total` не меняется).
- `scripts/build_fork_pages.py` — переоценённые задачи получают `passed=True` и
  метку в колонке Result: `решаемо (K=3: N/3)`; агрегаты волн/конфигураций
  считаются с учётом override.
- Тесты `tests/test_fork_false_negative_override.py`.

## Scope (issue #462)

Переоценены 4 задачи (K=3 ≥ 1 PASS): `task_449_search_capabilities` (2/3),
`task_470_search_hint` (1/3), `task_422_calendar_timezone` (2/3),
`task_429_mail_bounce` (2/3). `task_394_review_annotation` (0/3) — реальный
провал, остаётся FAIL.

## Out of scope

- Реальные провалы (0/k) не переоцениваются.
- ds41flash-прогоны не трогаем (там K=3 дал нестабильные провалы, не ложные
  негативы).
- Backfill ₽/токенов — отдельно (change `recover-oc-usage-from-trajectory`).

## Impact

- Публикуемые числа меняются только у затронутых строк MiniMax:
  feasibility·off 33/40→34/40 (85.0%), feasibility·medium 26/40→27/40 (67.5%),
  reflect·off 13/20→15/20 (75.0%). `long·medium` — без изменений.
