# Design: fix-ci-ruff

## Подход

1. **Авто-фиксы** — `uv run ruff check . --fix`. Закрывает 68 безопасных нарушений
   (неиспользуемые импорты, сортировка импортов, `UP0xx`, `F541` и т. п.). Эти правки
   не меняют поведение.
2. **Ручные правки** — 27 оставшихся нарушений, сгруппированы по типу:

| Правило | Что сделано |
|---|---|
| `E402` | `# noqa: E402` на импортах после `sys.path.insert(BENCH_DIR)` в `run_{pi,openclaw,hermes,dsh}_bench.py` (существующий в репо стиль) |
| `F402` | loop-переменные `t` → `task` в `scripts/build_fork_home.py` (не затеняют `t` из `fork_i18n`) |
| `F821` | добавлен хелпер `_task_message(text)` рядом с `render_detail` |
| `F841` | удалена неиспользуемая `spec = importlib.util.spec_from_file_location(...)` и лишний `import importlib.util` |
| `B007` | неиспользуемые loop-переменные переименованы в `_name` / `_h` |
| `B904` | `raise SystemExit(1) from exc` в `build_medium90_data.py` |
| `B905` | `zip(ru, en, strict=True)` в тесте |
| `E702` | разбиты `d1 = ...; d1.mkdir()` на отдельные строки |
| `E741` | `l` → `line` в `run_openclaw_bench.py` |
| `SIM105` | `try/except/pass` → `contextlib.suppress(...)` (`run_pi_bench.py`, `sync_cloudru_prices.py`) |
| `SIM109` | `model_id == a or model_id == b` → `model_id in (a, b)` |
| `SIM110` | цикл раннего `return True` → `return any(...)` (`publish_fork.py`) |

## Почему `noqa` для `E402`, а не перенос импортов

В `run_*_bench.py` импорты из `harness_bench` идут **после** `sys.path.insert(0, BENCH_DIR)` —
это осознанный паттерн, чтобы пакет находился независимо от CWD. Перенос импортов вверх
сломал бы резолв, поэтому подавляем `E402` как в уже существующих строках этих файлов.

## Риски и митигация

- Авто-фиксы затрагивают импорты (`I001`, `F401`) → риск случайного удаления нужного
  импорта. Митигация: полный `uv run pytest` + `build_* --check` после правок.
- `F821` в мёртвом коде: `_task_message` добавлен, чтобы имя резолвилось; поведение
  `render_detail` не меняется (`_DETAIL_JOBS` пуст, страницы деталей генерит
  `build_fork_pages.py`).
