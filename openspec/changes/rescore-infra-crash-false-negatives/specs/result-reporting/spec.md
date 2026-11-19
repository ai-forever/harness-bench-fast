## ADDED Requirements

### Requirement: Переоценка ложных негативов инфра-крашей

Форк-генераторы (`build_fork_results.py`, `build_fork_pages.py`) SHALL учитывать
sidecar-реестр `scripts/fork_false_negative_override.py`: для пары
`(run stem, task_id)` из реестра задача SHALL считаться пройденной (**вариант B**:
`passed` +1, `total` без изменений), а на детальной странице SHALL отображаться
метка `решаемо (K=N: M/N)`. Сырые артефакты `results/*.json` SHALL NOT изменяться.

#### Scenario: Переоценённая задача считается пройденной

- **WHEN** артефакт записал задачу как FAIL, а `(stem, task_id)` есть в реестре
- **THEN** run-level `passed` SHALL увеличиться на 1 (total без изменений), а
  per-task Result SHALL показывать `решаемо (K=N: M/N)`

#### Scenario: Реальный провал не переоценивается

- **WHEN** задача отсутствует в реестре (в т.ч. K=0/N)
- **THEN** её исход SHALL остаться неизменным (`fail`)

#### Scenario: Артефакты не модифицируются

- **WHEN** генераторы применяют реестр
- **THEN** файлы `results/*.json` SHALL остаться байт-в-байт прежними
