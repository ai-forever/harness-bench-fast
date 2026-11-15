# Model & Harness Selection Guidance — Domain Model (CONTEXT)

> Живой глоссарий для research-ветки `research/model-selection-guidance`.
> Канонический домен бенчмарка — `GLOSSARY.md` (не дублируем, ссылаемся).
> Этот файл держит словарь **слоя рекомендаций** и пополняется по ходу grilling.
> Автор: project-a-agent, 2026-11-15.

## Scan sources
- `GLOSSARY.md` (harness-bench-fast Domain Model) — базовые термины.
- `docs/adr/0001..0005` — принятые решения (shot list, capability sample, publish).
- `AGENTS.md` — правила отчётности (суиты, версии task-set, reasoning level).
- `docs/fork-results.md`, `FORK.md` — публичный форк-слой.

## Terms (selection layer)

### Situation (ситуация)
Признак задачи/контекста пользователя, который мапится в рекомендацию. Оси:
класс сложности (Shot List / Medium / long), wave/категория, бюджет (ceiling),
латентность. Источник — суиты и wave-разметка из `GLOSSARY.md`.

### Recommendation
Явная связка `(Situation, Harness, Model, Reasoning level) → выбрать/не выбрать`,
с обоснованием и пометкой уверенности. Противопоставлена «выбору без обоснования».

### Premium justification (оправданность дорогой модели)
Условие, при котором расход на premium-модель признаётся обоснованным.
Кандидат: точка на границе Парето **и** закрытие floor/boundary-задач, которые
дешёвые конфигурации не берут. Уточняется в grilling (Q3).

### Cost per solved task (₽ на решённую задачу)
Нормализованная метрика: `₽ прогона / число пройденных задач`. Позволяет сравнивать
модели разной цены. Для лестницы ₽ = факт биллинга (reconcile-premium-ladder-cost).

### Pareto frontier (фронт Парето)
Множество конфигураций, не доминируемых по (pass-rate ↑, ₽ ↓). База для вывода
«дорого и не лучше» (доминируемые точки = anti-pattern).

### Anti-pattern (переплата без выигрыша)
Конфигурация, доминируемая более дешёвой с тем же/лучшим pass-rate. Явно
перечисляется в руководстве как «так не делай».

### Harness × Model pair
Рекомендация рассматривает пару (харнесс, модель), т.к. одна модель даёт разный
score на Pi / OpenClaw / Hermes. Отличается от «рекомендации модели вообще».

### Coverage confidence (уверенность покрытия)
Пометка вывода: сколько прогонов, полное ли покрытие суита, одна ли task-set
версия. Согласуется с `Partial measurement` и правилами `AGENTS.md` о версиях.

### Routing policy (политика маршрутизации)
Машиночитаемое правило выбора модели по классу задачи — кандидат на связку с
OpenClaw Dynamic Routing / Fallbacks. Отдельный deliverable (следующая итерация).

### Guidance generator
Скрипт, порождающий числа/матрицу руководства из `results/*.json`, по образцу
fork-генераторов (см. `Generator` в `GLOSSARY.md`), с гейтами `--check/--lint`.

## Relationships
- Situation = (suite class, wave, budget ceiling) → Recommendation
- Recommendation = (Harness × Model pair) + Premium justification + Coverage confidence
- Pareto frontier ⇒ Anti-patterns (доминируемые точки)
- Guidance generator feeds Recommendation table; narrative добавляется вручную
- Routing policy ⊃ Recommendations, но требует порогов бюджета (Q7)

## Decisions — Round 1 (accepted 2026-11-15)
- **A. Форма:** публичная `docs/model-selection-guidance.md` (+`.en.md`) + `model-selection-matrix.json`; секция на `fork.html`.
- **B. Ситуация:** оси = класс сложности (Shot List/Medium/long) + wave/категория; бюджет — ceiling; «типовые задачи» — производная от wave.
- **C. Premium:** база = фронт Парето + объяснение через floor/boundary-задачи; иначе anti-pattern.
- **D. Харнесс:** рекомендация = пара (harness × model); отдельный вывод о силе харнесс-эффекта.
- **E. Сопоставимость:** сравнивать в одной task-set версии/протоколе; partial = unmeasured; флаг уверенности (n, полнота).
- **F. Поддержка:** гибрид — числа/матрица скриптом `build_model_selection.py` c `--check/--lint`; нарратив вручную.
- **G. Routing:** сейчас — публичная страница; routing policy — отдельный драфт (нужны пороги бюджета).
- ADR: `docs/adr/0006-model-selection-guidance.md`.

## Decisions — Round 2 (accepted 2026-11-15)
- **Q1 Шаблон страницы:** TL;DR · матрица · рекомендации по ситуациям · anti-patterns · покрытие/уверенность · методология.
- **Q2 Тиры:** `Default` · `Balanced` · `Premium` (только с обоснованием), лейблы RU+EN.
- **Q3 Situation buckets:** `shot-list` · `medium` · `long-context` · `wave19 feasibility` · `hard-coupled (wave21)`.
- **Q4 Scope версий:** только текущие публичные (**v0.21.3**: Light 391 + Medium 90); исторические — вне рекомендаций.
- **Q5 Порог premium:** не доминируем И (≥ +3 п.п. pass ИЛИ закрывает ≥1 floor/boundary-задачу); иначе anti-pattern.
- **Q6 Цена:** классификация по **₽/решённую задачу**, пороги — по данным (терцили/медиана), валюта ₽.
- **Q7 Матрица:** поля `harness, model, reasoning, suite, situation_bucket, passed, total, pass_rate, cost_rub, cost_per_solved, time_min, steps, pareto, dominated_by, coverage{runs,full,version}, confidence`; генератор `scripts/build_model_selection.py` (`--check/--lint`).
