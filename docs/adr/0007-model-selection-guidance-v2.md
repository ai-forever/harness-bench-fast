# ADR-0007: Model-selection guidance v2 — fixes from the page review

Date: 2026-11-16
Status: accepted
Deciders: gateway-owner, project-a-agent
Source: grilling session (grill-with-docs → grilling-design-tree), Round 3

## Context

Публичная страница `docs/model-selection-guidance.html` (слой «что × чем × когда») прошла
ревью (`docs/reviews/2026-11-16-model-selection-guidance-review.md`, PR #20). Найдены
дефекты, искажавшие выводы:

- **F-D1** — `Tier` вычислялся, но не отображался.
- **F-D2** — фронт Парето строился по **абсолютному `passed`**, а бакет `medium-reflect`
  смешивал знаменатели {5,15,20} → прогон 5/5 = 100% помечался доминируемым 80–85%.
- **F-D3** — прогоны с 0 решённых **молча исключались** (survivorship; `unmeasured` смешивался с «0/N»).
- **F-D4** — столбец `Cost/solved` смешивал **факт биллинга** (premium) и **оценку по тарифу**.
- **F-D5** — терцили/тиры считались глобально при разных знаменателях.
- **F-D9** — не было CI-гейта `--check/--lint` для guidance.
- **F-UX1** — TL;DR/«Как читать» из `.md` не рендерились в HTML; дубли столбцов; `—` перегружен.

## Decision

Round 3 (grilling) — принято:

1. **Tier scope — пер-бакет.** Терцили `₽/решённую задачу` считаются внутри бакета и только по
   полному набору (`total == BUCKET_FULL[bucket]`); прочие строки — без тира.
2. **Pareto — по `pass_rate`**, и только внутри одного набора (`bucket × total`). Абсолютный
   `passed` — display-only (`x/y`).
3. **0/N показываем** как провал/ceiling-marker; `unmeasured` — только реально не запущенные;
   `0/N` исключаются из ₽/solved, тиров и фронта.
4. **Cost basis** — один столбец `Cost/solved` + видимый признак `Basis` (`billing` — сайдкар
   `results/reconciled/*`; `est.` — оценка по тарифу).
5. **HTML parity** — TL;DR и «Как читать» рендерятся в HTML **из `.md`** (единый источник нарратива).
6. **Columns/UX** — убраны дубли (`%`, `Reasoning` — свёрнут в `Model`); добавлен `Tier`;
   `Bucket` — групповые строки; `Stem` — ссылка на `fork-detail-<stem>.html`; разряды в числах;
   `—` = «не Pareto», `н/д` = «нет данных».
7. **Delivery/CI** — ветка `feat/guidance-v2`; CI-гейт `build_model_selection.py --lint` + `--check`.

## Consequences

- (+) Выводы воспроизводимы: «100% прогон доминируем» больше не возникает; негативные (0/N)
  результаты видимы; «×25» сравним по базису.
- (+) Обещанные ADR-0006 сущности (тиры) реально видны; HTML и `.md` не расходятся по смыслу.
- (+) CI ловит дрейф docs ↔ `results/*.json` (как у fork-генераторов).
- (−) Таблица выросла (72 → 88 строк) — больше «шума» от 0/N; смягчено группировкой по бакету.
- (−) Тиры теперь не единый порог, а локальный — межбакетное сравнение «по тиру» некорректно.
- (риск) `—`/`н/д` требуют легенды (добавлена) и внимания при переводах.

## Follow-ups

- Прогоны для `wave21 (hard-coupled)` (не измерен) и добор `long` (6/20) — снять `unmeasured`.
- Routing policy (Dynamic Routing/Fallbacks) — отдельный драфт (нужны пороги бюджета от владельца).
