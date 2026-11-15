# Proposal: add-billing-reconciliation

## Why

Провайдер (Cloud.ru Foundation Models, «Raw activity rows (MSK)») отдаёт CSV-экспорт
активности с фактическими токенами и ₽. Нужен повторяемый способ сверять эти списания
с нашими прогонами и разносить крупные расходы по ключам/проектам.

Обнаружен расход по ключу `kap-openclaw`: **102 765 ₽** за 2026-11-14→11-15, из них
`anthropic/claude-sonnet-4.6` — **95 134 ₽**. Источник расхода требует атрибуции:
наши бенч-прогоны vs собственное потребление gateway/агента. Дополнительно выяснилось,
что два экспорта могут полностью перекрываться по окну — без дедупа суммы удваиваются.

## What Changes

- Новый скрипт `scripts/reconcile_billing.py`: читает один/несколько CSV-экспортов,
  дедуплицирует строки, агрегирует фактические tokens/₽ по `(department, api_key, model)`,
  фильтры `--key`, `--model`, `--since`, `--until`, вывод `--json`.
- Новая capability `billing-reconciliation` (дедуп, агрегация, сверка с оценкой прогонов).
- Отчёт-разбор `docs/reconciliation-2026-11-15.md` с атрибуцией расхода `kap-openclaw`
  (95 134 ₽ sonnet-4.6) — какие прогоны/сессии его породили.
- Автотесты `tests/test_reconcile_billing.py` (дедуп на фикстуре-перекрытии, агрегация,
  фильтры).

**Вне scope:** правка провайдерских лимитов; отмена/возврат списаний; изменение раннеров.

## Capabilities

### New Capabilities
- `billing-reconciliation`: сверка фактического биллинга Cloud.ru с нашими прогонами.

### Modified Capabilities
- (нет)

## References
- Workboard card: `e07ddcdc-2cb8-40d7-8bd6-5311b2658456`
