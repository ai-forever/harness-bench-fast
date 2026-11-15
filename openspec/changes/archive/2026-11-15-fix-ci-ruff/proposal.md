# Proposal: fix-ci-ruff

## Why

CI-job «Quality» (`.github/workflows/ci.yml`, шаг `uv run ruff check .`) красный на `main`
непрерывно с 2026-11-06 (коммит `5e9eef8`): **95 нарушений** Ruff. Последний зелёный CI —
`5f609ba` (2026-11-06 12:14). Нарушения накапливались и не связаны с каким-либо одним
изменением: чаще всего `E402` (импорты не в начале файла), `F401` (неиспользуемые импорты)
и `F541`. Красный lint-гейт шумит и маскирует реальные проблемы (например `F821`
undefined name `_task_message` в `scripts/build_fork_home.py`).

## What Changes

Привести репозиторий к нулю нарушений `uv run ruff check .` при текущем конфиге Ruff
(`select = E,F,I,UP,B,SIM`, `ignore = E501`) — **без** изменения самого конфига:

- Авто-фиксы (`ruff check --fix`): `F401`, `F541`, `I001`, `UP017`, `UP037`, `UP045` и др.
- Ручные правки для неавтофиксируемых:
  - `E402` — `# noqa: E402` там, где импорт намеренно идёт после `sys.path.insert(...)`;
  - `F402` — переименовать loop-переменные, затеняющие импортированный `t` (i18n);
  - `F821` — добавить хелпер `_task_message` (используется в мёртвом `render_detail`);
  - `B007`, `B904`, `B905`, `E702`, `E741`, `SIM105`, `SIM109`, `SIM110`, `F841`;
  - `tests/test_fork_sample_columns.py` — обновить guard под новую ведущую колонку `#`
    (12 колонок вместо 11), введённую ранее в `add-fork-table-ranks-and-sorting`; без
    этого `pytest` падал бы сразу после того, как Ruff станет зелёным;
  - `tests/test_runner_timeout_floors.py` — грузить раннер из корня репо (сейчас путь
    ошибочно указывал на уровень выше чекаута → в CI collection error, `pytest` exit 2);
  - `run_pi_bench.py` — убрать хардкод `/tmp/...` (заменён на `tempfile.gettempdir()`):
    на Windows `open("/tmp/...")` падал → 2 теста `test_runner_pi_cleanup` падали;
  - `tests/test_cost_cache_bucket.py` — пиновать ставки модели через committed-фикстуру
    (`tests/fixtures/cloudru-prices-ds41flash.json`), иначе в CI (без `~/.openclaw/cloudru-prices.json`)
    резолвер падал на GLM‑5.2‑fallback и тест не мог пройти детерминированно.

## How

- Отдельная ветка `fix/ci-ruff`; изменение оформлено через OpenSpec (`fix-ci-ruff`).
- Сначала `uv run ruff check . --fix`, затем точечные правки; финальная проверка →
  «All checks passed!».
- Регресс-гейты: `scripts/build_fork_results.py --check`, `scripts/build_fork_pages.py --check`,
  `scripts/build_fork_home.py --check` и `uv run pytest`.

## Out of scope

- Изменение правил/конфига Ruff в `pyproject.toml` (набор `select`, `ignore`, `line-length`).
- Рефакторинг по существу: перестроение порядка инициализации `sys.path`/импортов там, где
  `E402` подавляется `noqa`.
- Любые изменения поведения бенчмарка, результатов или генераторов отчётов.
- Починка `windows-benchmark-smoke` и прочих не-lint CI-шагов.
