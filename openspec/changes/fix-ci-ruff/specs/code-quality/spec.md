# code-quality

## ADDED Requirements

### Requirement: Линт-гейт Ruff проходит на всей кодовой базе

Репозиторий SHALL проходить `uv run ruff check .` с кодом возврата `0` при конфигурации
Ruff из `pyproject.toml` (набор правил `E,F,I,UP,B,SIM`, правило `E501` игнорируется).

#### Scenario: Чистый прогон Ruff

- **WHEN** в чекауте выполняется `uv run ruff check .`
- **THEN** команда SHALL завершаться с кодом возврата `0`

#### Scenario: CI job Quality зелёный

- **WHEN** CI-workflow `.github/workflows/ci.yml` запускается на `main` или в pull request
- **THEN** шаг «Run Ruff» SHALL завершаться успешно

### Requirement: Отсутствие неопределённых имён

Модули репозитория SHALL NOT содержать неопределённых имён, обнаруживаемых правилом `F821`.

#### Scenario: Ruff не находит F821

- **WHEN** выполняется `uv run ruff check .`
- **THEN** среди нарушений SHALL NOT быть ни одного `F821`
