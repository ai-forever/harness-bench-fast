# Proposal: reconcile-premium-ladder-cost

## Why

Премиум-лестница (Shot List 52 × 6 Anthropic-моделей, ключ `kap-hermes`,
2026-11-14 17:48–19:00) опубликована с ₽, посчитанными из
`agent_input_tokens`/`agent_output_tokens` раннера. Эти прогоны шли на ревизии
раннера, где `_extract_usage` рекурсивно обходил весь Pi-NDJSON и учитывал один и
тот же `usage`-блок **трижды** (input и output). Поэтому наши ₽ завышены ровно ×3.

Биллинг Cloud.ru по ключу `kap-hermes` (дедуплицированный экспорт) даёт **5 782 ₽**
фактически против **17 406 ₽** в форке (**×3.01**). Исправляющий single-count уже в
`main` (change `fix-bench-token-accounting`, commit `3baebb6`), но опубликованные
строки лестницы остались несогласованными.

## What Changes

- Пересчитать и перепубликовать строки премиум-лестницы (6 моделей) в
  `docs/fork-results.md` и `docs/fork-results.en.md`: tokens/₽ брать из
  дедуплицированного экспорта провайдера по ключу `kap-hermes`; pass-рейтинги и
  время/шаги не меняются.
- Новый скрипт `scripts/reconcile_ladder_billing.py` (idempotent): читает CSV-экспорт(ы)
  активности, дедуплицирует, агрегирует per-model, пишет сайдкар
  `results/reconciled/pi_shotlist_<token>_20261114.json` с фактическими tokens/₽ и
  печатает diff «published vs provider».
- Зафиксировать в `design.md` доказательство ×3 (пример: один usage-блок в NDJSON
  встречается 3 раза) и порядок действий при будущем расхождении.

**Вне scope:** новые прогоны моделей; изменение pass-рейтингов; правка самого раннера
(single-count уже в `main`); доступность `sonnet-5` / `sonnet-5.5`.

## Capabilities

### New Capabilities
- (нет)

### Modified Capabilities
- `result-reporting`: ADDED-требование «Сверка публикуемых ₽ с биллингом провайдера».
