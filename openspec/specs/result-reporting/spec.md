# result-reporting

## Purpose

Как результаты форка представляются наружу: таблицы лидерборда, форк-слой
документации и автоматическая синхронизация таблиц из JSON-прогонов.

## Requirements

### Requirement: Канонические поля таблиц

Все таблицы результатов форка SHALL содержать колонки: `Harness`, `Profile`,
`Model`, `Result`, `%`, `Time`, `Steps`, `Tokens (cost)`.

#### Scenario: Добавление таблицы результатов

- **WHEN** таблица результатов добавляется в `FORK.md` или `docs/fork-results.md`
- **THEN** она SHALL содержать все канонические колонки

### Requirement: Автосинк авто-блока

Авто-блок в `docs/fork-results.md` SHALL генерироваться скриптом между маркерами
`<!-- FORK:RESULTS:BEGIN -->` и `<!-- FORK:RESULTS:END -->`.

#### Scenario: Проверка актуальности

- **WHEN** выполняется `python3 scripts/build_fork_results.py --check`
- **THEN** код возврата SHALL быть `0`, если блок актуален, и `1` — если устарел

### Requirement: Форк-слой не теряется при мерже upstream

Форк-слой в `README.md` SHALL быть обёрнут маркерами `<!-- FORK:BEGIN -->` …
`<!-- FORK:END -->`.

#### Scenario: Мерж upstream

- **WHEN** выполняется `git merge upstream/main`
- **THEN** баннер SHALL сохраняться, а при конфликте выбирается «keep ours»

### Requirement: Линт канонических колонок

Инструмент отчётности SHALL предоставлять режим `--lint`, проверяющий, что каждая
таблица результатов в `FORK.md` и `docs/fork-results.md` содержит колонки
`Harness`, `Profile`, `Model`, `Result`, `%`, `Time`, `Steps`, `Tokens (cost)`.

#### Scenario: Все таблицы корректны

- **WHEN** запускается `python3 scripts/build_fork_results.py --lint`
- **THEN** код возврата SHALL быть `0`, если во всех результатных таблицах есть канонические колонки

#### Scenario: Таблица без колонки

- **WHEN** какая-либо результатная таблица не содержит одну из канонических колонок
- **THEN** `--lint` SHALL вывести имя файла и список отсутствующих колонок и вернуть ненулевой код

### Requirement: Данные страницы результатов форка

Форк SHALL генерировать `docs/fork-medium90-results.json` из JSON-прогонов
(`results/`) скриптом `scripts/build_fork_pages.py` по замороженной схеме (строки
конфигураций: `harness`, `model`, `profile`, `runs`, `suites` по волнам
long/reflect/feasibility, `usage`, `wilson`, `sources`). Свод по волне SHALL считаться
как UNION номеров задач, а не как сумма per-file итогов.

#### Scenario: Свод по волнам

- **WHEN** запускается `python3 scripts/build_fork_pages.py`
- **THEN** файл `docs/fork-medium90-results.json` SHALL содержать по одной строке на
  конфигурацию с `{passed,total}` по каждой измеренной волне и 95% Wilson-интервалом

#### Scenario: Перекрывающиеся артефакты волны не завышают покрытие

- **WHEN** по одной конфигурации и волне есть несколько артефактов с перекрывающимися
  номерами задач (напр. устаревший частичный прогон и догоняющий)
- **THEN** `total` волны SHALL равняться числу **уникальных** номеров задач, а `passed` —
  числу уникальных пройденных; перекрытие SHALL NOT суммироваться

#### Scenario: Нет разбираемых номеров задач

- **WHEN** ни один артефакт волны не даёт разбираемых номеров задач
- **THEN** скрипт SHALL сохранять прежнее поведение — суммировать `summary.passed` и
  `summary.total` этой волны

#### Scenario: Проверка актуальности данных

- **WHEN** выполняется `python3 scripts/build_fork_pages.py --check`
- **THEN** код возврата SHALL быть `0`, если JSON актуален, и `1` — если устарел

### Requirement: Страница-таблица результатов форка

Форк SHALL предоставлять статическую страницу `docs/fork-medium90.html`, которая
рендерит данные из `docs/fork-medium90-results.json` и содержит матрицу
**Harness × Wave** и per-config таблицу с 95% Wilson-интервалом. Per-config таблица
SHALL содержать канонические колонки форка `Profile`, `Time` и `Tokens (cost)`, а
ячейка `Model / harness` SHALL быть ссылкой на страницу детализации прогона
(`details_url`).

#### Scenario: Отрисовка данных

- **WHEN** страница открывается в браузере
- **THEN** она SHALL отображать строки по конфигурациям и волнам, а неизмеренные волны
  помечать как `Unmeasured`, а не как `0/total`

#### Scenario: Колонка Profile

- **WHEN** per-config таблица отрисовывается
- **THEN** она SHALL содержать колонку `Profile` (`—`, если профиль не задан)

#### Scenario: Колонка Tokens (cost) в рублях

- **WHEN** per-config таблица отрисовывается
- **THEN** колонка `Tokens (cost)` SHALL показывать токены и оценку стоимости в рублях (`₽`)
  из `row.usage.cost_rub`; при отсутствии стоимости SHALL показываться только токены

#### Scenario: Колонка Time

- **WHEN** per-config таблица отрисовывается
- **THEN** колонка `Time` SHALL показывать длительность прогона в минутах из
  `row.usage.seconds` (`—` при отсутствии)

#### Scenario: Ссылка на детализацию

- **WHEN** у строки задан `details_url`
- **THEN** ячейка `Model / harness` SHALL быть ссылкой на соответствующую страницу
  детализации прогона

### Requirement: Публикация через GitHub Pages

Форк SHALL публиковать каталог `docs/` через GitHub Pages, чтобы таблицы результатов
были видны на сайте, а не только в markdown.

#### Scenario: Доступность страницы

- **WHEN** GitHub Pages включён для ветки `main` и пути `/docs`
- **THEN** страница `fork-medium90.html` SHALL отдаваться по адресу
  `https://alex-kul777.github.io/harness-bench-fast/fork-medium90.html`

### Requirement: Реестр волн в авто-блоке

Генератор авто-блока `scripts/build_fork_results.py` SHALL регистрировать волны
новых задач как один umbrella-suite `Medium (392–471)` (long 20 + reflection 20 +
feasibility 40), чтобы прогоны этих волн попадали в `docs/fork-results.md`.

#### Scenario: Прогон новой волны

- **WHEN** в `results/` появляется JSON-прогон с именем волны long/reflect/feasibility
- **THEN** `python3 scripts/build_fork_results.py` SHALL включить его строку в авто-блок
  под сюжетом `Medium (392–471)`

### Requirement: Версионирование ассетов страницы

Ассеты опубликованной форк-страницы (`docs/fork-medium90.html`) SHALL подключаться с
query-версией (`…?v=<version>`), чтобы браузер не мог смешать устаревший закешированный
`fork-results.js` со свежей разметкой.

#### Scenario: Теги ассетов версионированы

- **WHEN** страница `docs/fork-medium90.html` публикуется
- **THEN** теги `<link>`/`<script>` SHALL содержать параметр `v`

#### Scenario: Обновление ассетов

- **WHEN** изменяется `docs/fork-results.js`
- **THEN** версия `v` в теге SHALL быть обновлена, чтобы клиенты получили новый файл

### Requirement: Страница детализации прогона

Генератор `scripts/build_fork_pages.py` SHALL создавать для каждой конфигурации
страницу `docs/fork-run-<id>.html` с разбивкой по волнам, per-task списком и
источниками прогонов. Страница SHALL перечислять задачи всех наших наборов
(Shot List, Light, Medium) без фильтра по medium-волне. Для упавших задач страница
SHALL показывать читаемую причину и раскрывающийся вывод верификатора.

#### Scenario: Генерация детализации

- **WHEN** запускается `python3 scripts/build_fork_pages.py`
- **THEN** по каждой строке SHALL создаваться `docs/fork-run-<id>.html`, а в JSON
  появляться поле `details_url`

#### Scenario: Источники экранированы

- **WHEN** страница детализации рендерит имена источников и сообщения задач
- **THEN** текст SHALL быть HTML-экранирован

#### Scenario: Оформленный вывод упавшей задачи

- **WHEN** задача имеет `passed=false` или структурированный `message`
- **THEN** колонка `Note` SHALL показывать короткую причину (ошибку, `reason` или ключевые
  поля верификатора), а ячейка SHALL содержать раскрывающийся `<details>` с
  pretty-printed JSON верификатора и текстом `error` (если он есть)

#### Scenario: Ошибка как причина

- **WHEN** у задачи непустой `error`
- **THEN** колонка `Note` SHALL показывать текст ошибки (например, `timed out after 3600s`)

#### Scenario: Per-task список для всех наших наборов

- **WHEN** прогон относится к Shot List или Light (его задачи не принадлежат medium-волнам)
- **THEN** страница `docs/fork-run-<id>.html` SHALL перечислять per-task записи из
  `results`/`tasks` этого прогона, не отбрасывая их из-за отсутствия волны

#### Scenario: Метка набора вместо волны

- **WHEN** id записи не отображается ни на одну известную волну
- **THEN** колонка волны SHALL показывать метку набора (Shot List / Light / Medium)

### Requirement: Данные о длительности прогона

Генератор форк-страницы SHALL агрегировать per-task `elapsed_seconds` из наших
прогонов и из апстрим-замеров
(`docs/{codex-deepseek,miniswe-deepseek,claudecode-sonnet}-medium90-*.json`) и SHALL
записывать `rows[].suites[<wave>].duration = {n,mean,median,max}` и top-level
`duration_by_wave` (наши + три апстрим-конфигурации, секунды).

#### Scenario: Джойн по задачам

- **WHEN** запускается `python3 scripts/build_fork_pages.py`
- **THEN** JSON SHALL содержать по каждой измеренной волне mean/median/max секунд
  для `Pi (ours)` и для доступных апстрим-конфигураций

#### Scenario: Нет данных по конфигурации

- **WHEN** у конфигурации нет per-task времени для волны
- **THEN** она SHALL отсутствовать в списке этой волны (а SHALL NOT показываться как 0)

### Requirement: Колонка Duration на странице

Страница `docs/fork-medium90.html` SHALL показывать колонку `Duration` в таблице
per-config и таблицу `Duration by wave` (строки — конфигурации, столбцы — волны).

#### Scenario: Отрисовка длительности

- **WHEN** страница открывается
- **THEN** ячейки SHALL показывать секунды (mean с `n`) или `—`, и текст SHALL быть
  экранирован

### Requirement: Вид capability-сэмпла

Форк SHALL публиковать cost-bounded capability sample (20 задач = 25% Medium) отдельным
видом: `docs/fork-sample.html` + данные `docs/fork-sample-results.json`, собранные из
манифеста `docs/fork-sample-manifest.json`. Волна long SHALL помечаться `invalid`, если её
таймаут был ниже флора волны.

#### Scenario: Генерация вида

- **WHEN** запускается `python3 scripts/build_fork_pages.py`
- **THEN** SHALL создаваться `docs/fork-sample-results.json` и `docs/fork-sample.html`
  по задачам манифеста

#### Scenario: Невалидная волна помечена

- **WHEN** волна long измерена с таймаутом ниже флора (7200)
- **THEN** её статус SHALL отображаться как `invalid` с причиной, а не как обычный fail

#### Scenario: Ссылка на манифест

- **WHEN** страница сэмпла отображается
- **THEN** она SHALL содержать ссылку на `docs/fork-sample-manifest.json`

### Requirement: Единая форк-страница

Форк SHALL публиковать наши результаты одной страницей `docs/fork.html` (+ данные
`docs/fork-home-results.json`, генератор `scripts/build_fork_home.py`) с наборами
Shot List (52), Light (391), **Medium (90)** и **Medium sample** — переключаемыми
табами на одном URL. Таблицы конфигов SHALL иметь ровно девять колонок; активный таб
SHALL следовать URL-hash (`#shotlist` / `#light` / `#medium` / `#medium-sample`).

#### Scenario: Единая страница отображается

- **WHEN** открывается `docs/fork.html`
- **THEN** SHALL присутствовать четыре таба-набора и таблица активного набора

#### Scenario: Табы переключают набор

- **WHEN** пользователь выбирает таб (Shot List / Light / Medium / Medium sample)
- **THEN** SHALL отображаться ровно этот набор, а URL-hash SHALL становиться
  `#shotlist` / `#light` / `#medium` / `#medium-sample` (`aria-pressed` отражает активный таб)

#### Scenario: Ровно девять колонок

- **WHEN** таблица конфигов набора отображается
- **THEN** её заголовок SHALL содержать ровно девять колонок
  (`Harness`, `Profile`, `Model`, `Result`, `%`, `Time`, `Steps`, `Tokens (cost)`, `Version`),
  а `ours`/`upstream` SHALL разделяться групповыми строками, не колонкой

#### Scenario: Version заполнена

- **WHEN** строка отображается
- **THEN** `Version` SHALL содержать task-set version (наши) или `source_version` (upstream),
  либо `—` при отсутствии

#### Scenario: Волновая разбивка Medium

- **WHEN** активен таб Medium
- **THEN** волновая разбивка (long / reflect / feasibility / composite hard) SHALL быть
  доступна в раскрывающемся блоке `details`

#### Scenario: Старые URL не ломаются

- **WHEN** открывается `fork-medium90.html` или `fork-light.html`
- **THEN** он SHALL перенаправлять на `docs/fork.html#medium` / `#light`, активируя нужный таб

### Requirement: Upstream-сравнение по наборам

Единая форк-страница `docs/fork.html` SHALL показывать upstream-строки для каждого набора
(Shot List, Light, Medium) отдельной группой `Upstream comparison`, отделённой от наших
строк групповой строкой (не колонкой). Источники upstream: README Shot-List таблица
(наши харнессы исключены), `docs/leaderboard-results.json` (`light`, `medium`).

#### Scenario: Upstream-группа присутствует

- **WHEN** активен таб Shot List / Light / Medium
- **THEN** под нашими строками SHALL присутствовать группа `Upstream comparison`
  с upstream-строками этого набора (где источник доступен)

#### Scenario: Наши и upstream не смешиваются

- **WHEN** таблица отображается
- **THEN** наши строки SHALL идти в группе `Our runs`, upstream — в отдельной группе,
  без отдельной колонки-дискриминатора

#### Scenario: Upstream-строка в схеме

- **WHEN** показывается upstream-строка
- **THEN** она SHALL следовать той же схеме из девяти колонок; `Version` SHALL содержать
  `source_version`, либо `—` при отсутствии

### Requirement: Фильтр-поиск на форк-странице

Страница `docs/fork.html` SHALL содержать панель фильтра с полем поиска
`Search model or harness` и счётчиком совпадений (`aria-live`). Ввод запроса SHALL
фильтровать строки активного набора по подстроке (модель/харнесс/профиль/версия),
скрывать группы без совпадений и показывать сообщение при нуле совпадений. Без JS
страница SHALL оставаться читаемой (все строки видны).

#### Scenario: Поиск фильтрует строки

- **WHEN** пользователь вводит запрос в поле поиска
- **THEN** SHALL оставаться только строки активного набора, содержащие запрос;
  счётчик SHALL показывать число совпадений

#### Scenario: Пустые группы скрываются

- **WHEN** в группе не осталось совпадающих строк
- **THEN** её групповую строку SHALL быть скрыто (равно как и строки — скрыты)

#### Scenario: Нет совпадений

- **WHEN** ни одна строка не совпадает
- **THEN** SHALL показываться сообщение «No rows match your search.»

#### Scenario: Сброс при смене таба

- **WHEN** пользователь переключает таб
- **THEN** фильтр SHALL переприменяться к новому набору

### Requirement: Дата прогона в колонке Profile

Страница `docs/fork.html` SHALL добавлять дату совершения бенчмарка к значению колонки
`Profile` в формате `профиль (YYYY-MM-DD)`. Дата SHALL определяться по доступным источникам
(`session_id` задач, имя файла `_YYYYMMDD`, курируемая README-таблица Shot List для наших
строк; `measurement_date` для upstream). При отсутствии даты колонка SHALL оставаться без
суффикса (дата не выдумывается).

#### Scenario: Дата добавлена

- **WHEN** для строки доступна дата прогона
- **THEN** `Profile` SHALL отображаться как `профиль (YYYY-MM-DD)` (напр. `thinking: off (2026-11-04)`)

#### Scenario: Дата недоступна

- **WHEN** для строки нет источника даты
- **THEN** `Profile` SHALL отображаться без суффикса даты (SHALL NOT выдумывать значение)

#### Scenario: Upstream-дата

- **WHEN** строка — upstream и имеет `measurement_date`
- **THEN** дата SHALL браться из `measurement_date`

### Requirement: Детализация прогона со строки

Страница `docs/fork.html` SHALL делать строки, у которых есть страница детализации,
кликабельными: клик по строке (или по харнесс-ячейке) SHALL открывать соответствующую
`docs/fork-run-<id>.html`. Строки без доступной детализации SHALL оставаться
некликабельными.

#### Scenario: Клик открывает детализацию

- **WHEN** пользователь кликает по строке прогона с доступной детализацией
- **THEN** SHALL открываться `docs/fork-run-<id>.html` этого прогона

#### Scenario: Строка без детализации

- **WHEN** у строки нет страницы детализации
- **THEN** она SHALL оставаться некликабельной (без `data-detail`)

### Requirement: Детализация для всех наших наборов

Все наши строки на `docs/fork.html` (Shot List, Light, Medium) SHALL иметь доступную
страницу детализации прогона и быть кликабельными. Для Medium SHALL использоваться
`docs/fork-run-<id>.html`; для Shot List и Light — `docs/fork-detail-<stem>.html`
(генератор `scripts/build_fork_home.py`). Страница детализации SHALL показывать сводку
прогона и таблицу задач (результат, секунды, токены, сообщение).

#### Scenario: Клик открывает детализацию любого набора

- **WHEN** пользователь кликает по нашей строке в любом наборе
- **THEN** SHALL открываться страница детализации этого прогона
  (`fork-run-<id>.html` для Medium, `fork-detail-<stem>.html` для Shot List/Light)

#### Scenario: Детализация показывает задачи

- **WHEN** открыта страница детализации
- **THEN** SHALL присутствовать сводка (Profile/Version/Result/Time/Steps/Tokens) и
  таблица задач с результатом, секундами, токенами и сообщением

#### Scenario: Отсутствие коллизии генераторов

- **WHEN** запускается `scripts/build_fork_pages.py`
- **THEN** страницы `docs/fork-detail-*.html` SHALL NOT удаляться (отдельный префикс)

### Requirement: Уровень ризонинга в колонке Model

Колонка `Model` наших строк на `docs/fork.html` SHALL включать провайдера и уровень
ризонинга из профиля прогона в формате `модель (провайдер) (уровень)` (напр.
`DeepSeek-V4.1-Flash (cloud.ru) (thinking: high)`), зеркаля upstream (напр.
`DeepSeek V4.1 Flash (OpenRouter) (high)`). Для наших строк провайдер — `cloud.ru`. Для
профилей без ризонинга (`MCP off`, `—`) суффикс уровня SHALL отсутствовать.

#### Scenario: Профиль с ризонингом

- **WHEN** профиль прогона содержит уровень ризонинга (`thinking: off`, `high`, …)
- **THEN** `Model` SHALL отображаться как `модель (провайдер) (уровень)`

#### Scenario: Профиль без ризонинга

- **WHEN** профиль не описывает ризонинг (`MCP off`, `—`)
- **THEN** `Model` SHALL отображаться без суффикса ризонинга

#### Scenario: Провайдер неизвестен

- **WHEN** провайдер строки не определён
- **THEN** `Model` SHALL отображаться без суффикса провайдера (значение SHALL NOT
  выдумываться)

### Requirement: Версия харнеса в колонке Harness

Колонка `Harness` наших строк SHALL включать версию харнеса (`харнес версия`) **когда она
записана в артефакте прогона** (`harness_version`/`openclaw_version`/`cli_version`/
`runner_version`), зеркаля upstream (`pi-mono 0.73.1`). При отсутствии записи суффикс SHALL
отсутствовать (версии SHALL NOT выдумываться).

#### Scenario: Версия записана

- **WHEN** артефакт прогона содержит поле версии харнеса
- **THEN** `Harness` SHALL отображаться как `харнес версия`

#### Scenario: Версия не записана

- **WHEN** артефакт не содержит версии харнеса
- **THEN** `Harness` SHALL отображаться без суффикса (значение SHALL NOT выдумываться)

### Requirement: Полный Medium на форк-странице

Таб `#medium` SHALL показывать полный набор Medium (90 задач: historical 392–471 плюс
wave 21 512–530). Наши прогоны с неполным покрытием SHALL попадать в таблицу с видимой
пометкой `partial` и опорным знаменателем 90.

#### Scenario: Неполный охват виден

- **WHEN** наш прогон покрывает только часть 90 задач
- **THEN** ячейка `Result` SHALL показывать `X/Y (partial Y/90)`, а строка SHALL попадать
  в таблицу `#medium`

#### Scenario: Знаменатель 90 и апстрим из 90-борда

- **WHEN** таблица `#medium` отображается
- **THEN** полный набор SHALL задавать знаменатель 90 (включая wave21), а апстрим-строки
  SHALL браться из 90-задачного борда (`docs/medium90-results.json`)

### Requirement: Урезанный Medium-семпл

Форк SHALL публиковать таб `#medium-sample` с нашими урезанными Medium-наборами
(суммарный охват прогнанных задач и пилоты) из манифеста, показывая неполное покрытие
относительно 90.

#### Scenario: Семпл-таб доступен

- **WHEN** открывается `docs/fork.html#medium-sample`
- **THEN** SHALL отображаться таблица наших наборов с колонкой `Coverage`
  (например `66/90`, `13/90`)

#### Scenario: Пилот помечен урезанным

- **WHEN** строка — пилот
- **THEN** её `Status`/`Coverage` SHALL показывать, что охват неполный (например
  `pilot (truncated)`, `13/90`), а источник SHALL быть в манифесте

### Requirement: Двуязычные страницы результатов

Страницы форка (`docs/fork.html`, `docs/fork-sample.html`, `docs/fork-run-<id>.html`,
`docs/fork-detail-<stem>.html`) SHALL публиковаться в русской локали (дефолт) и английском
зеркале (суффикс `.en.html`); при этом таблицы конфигов SHALL сохранять канонические
английские заголовки в обеих локалях.

#### Scenario: EN-зеркало страницы

- **WHEN** генерируется `docs/fork.html`
- **THEN** SHALL также генерироваться `docs/fork.en.html` с теми же данными и EN-подписями

#### Scenario: Канонические колонки в обеих локалях

- **WHEN** отображается таблица конфигов в любой локали
- **THEN** её заголовок SHALL содержать канонические английские колонки
  (`Harness`, `Profile`, `Model`, `Result`, `%`, `Time`, `Steps`, `Tokens (cost)`, `Version`)

#### Scenario: URL-hash сохраняется при смене локали

- **WHEN** активен таб и пользователь переключает локаль
- **THEN** URL-hash (например `#medium`) SHALL сохраняться на целевой странице

### Requirement: Двуязычный авто-блок результатов

Авто-блок в `docs/fork-results.md` SHALL генерироваться в RU-версии, а EN-зеркало
`docs/fork-results.en.md` SHALL содержать тот же набор строк с EN-подписями; канонические
английские колонки SHALL присутствовать в обоих файлах.

#### Scenario: Зеркало авто-блока

- **WHEN** выполняется `python3 scripts/build_fork_results.py`
- **THEN** SHALL обновляться авто-блок в `docs/fork-results.md` и его EN-зеркале
  `docs/fork-results.en.md`

#### Scenario: Паритет строк авто-блока

- **WHEN** сравниваются строки RU- и EN-авто-блока
- **THEN** множества строк данных SHALL совпадать (отличаются только подписи/заголовки групп)

### Requirement: Заполнение колонки Steps

Колонка `Steps` в таблицах результатов форка SHALL заполняться значением шагов агента:
сначала агрегат `summary.total_steps`, затем top-level `steps`, затем fallback — сумма
per-task `agent_steps` из `tasks[]`. Значение `—` SHALL показываться только если шаги
отсутствуют во всех источниках.

#### Scenario: Агрегат отсутствует, per-task данные есть

- **WHEN** JSON-прогон не содержит `summary.total_steps` и top-level `steps`, но в `tasks[]`
  есть `agent_steps`
- **THEN** колонка `Steps` SHALL показывать сумму `agent_steps` (а SHALL NOT показывать `—`)

#### Scenario: Агрегат присутствует

- **WHEN** JSON-прогон содержит `summary.total_steps`
- **THEN** колонка `Steps` SHALL показывать значение агрегата (без пересчёта)

#### Scenario: Данных нет вовсе

- **WHEN** шаги отсутствуют и в агрегате, и в per-task записях
- **THEN** колонка `Steps` SHALL показывать `—`

#### Scenario: Единый источник для обеих поверхностей

- **WHEN** запускаются `scripts/build_fork_home.py` и `scripts/build_fork_results.py`
- **THEN** обе поверхности SHALL применять одинаковый приоритет источников и давать
  согласованное значение `Steps` для одного и того же прогона

### Requirement: Канонические колонки и метаданные семпл-таба

Таблица `#medium-sample` на `docs/fork.html` SHALL использовать канонические колонки
форка (`Harness`, `Profile`, `Model`, `Result`, `%`, `Time`, `Steps`, `Tokens (cost)`)
наряду с колонками наших урезанных наборов (`Subset`, `Coverage`, `Status`). Строки
семпла SHALL проходить те же преобразования метаданных, что и строки `#medium`: дата в
`Profile`, уровень ризонинга и провайдер в `Model`, версия в `Harness`.

#### Scenario: Колонка Steps в семпл-табе

- **WHEN** таблица `#medium-sample` отрисовывается
- **THEN** она SHALL содержать колонку `Steps` (значение `—` только при отсутствии шагов
  во всех источниках)

#### Scenario: Дата в Profile семпла

- **WHEN** для строки семпла доступна дата прогона
- **THEN** `Profile` SHALL отображаться как `профиль (YYYY-MM-DD)`

#### Scenario: Версия в Harness семпла

- **WHEN** артефакт прогона строки семпла содержит `harness_version`
- **THEN** `Harness` SHALL отображаться как `харнес версия`

#### Scenario: Провайдер и уровень в Model семпла

- **WHEN** строка семпла принадлежит нашему прогону с ризонингом
- **THEN** `Model` SHALL отображаться как `модель (cloud.ru) (уровень)`

### Requirement: Row place column in fork tables

The fork page `docs/fork.html` SHALL render a leading place column (`#`) as the first
column of every configuration table (Shot List, Light, Medium, Medium sample). Each data
row SHALL carry a row number equal to its sequential position in the current display
order, and the numbering SHALL be continuous across the `Our runs` and `Upstream
comparison` groups. Group header rows SHALL NOT carry a place number.

#### Scenario: Place number on each data row

- **WHEN** a fork table is rendered
- **THEN** every data row SHALL have a place number in its first column, and group header
  rows SHALL NOT have a number

#### Scenario: Numbering continues across groups

- **WHEN** a table has an `Our runs` group followed by an `Upstream comparison` group
- **THEN** the first upstream row's number SHALL continue the sequence after the last
  `Our runs` row, rather than restarting at 1

#### Scenario: Number follows the current display order

- **WHEN** rows are reordered by a column sort
- **THEN** the place numbers SHALL be recomputed so that `#` reflects the new display order

### Requirement: Column sorting on fork tables

Configuration tables on `docs/fork.html` SHALL be sortable by clicking a column header.
Sorting SHALL apply within each group separately, group header rows SHALL stay in place,
and rows with a missing value (`—` or empty) SHALL be placed after the populated rows
regardless of sort direction.

#### Scenario: Sortable metric and text columns

- **WHEN** a fork table is rendered
- **THEN** the numeric metrics `%`, `Coverage`, `Time`, `Steps`, `Tokens (cost)` and
  `Result`, as well as the text columns (`Harness`, `Profile`, `Model`, `Subset`,
  `Version`, `Status`), SHALL be clickable and sortable

#### Scenario: First click sorts ascending

- **WHEN** the user clicks a sortable column header
- **THEN** the data rows SHALL be sorted by that column in ascending order

#### Scenario: Second click sorts descending

- **WHEN** the user clicks the same column header again
- **THEN** the data rows SHALL be sorted by that column in descending order

#### Scenario: Sorting respects groups

- **WHEN** a sortable column is sorted
- **THEN** the rows SHALL be ordered within each group, and the group header rows SHALL
  remain at the head of their own group

#### Scenario: Missing values sort last

- **WHEN** a column is sorted and some rows show `—`
- **THEN** the rows with `—` SHALL be placed after rows that have a value, in both the
  ascending and the descending direction

#### Scenario: Numeric and ratio metrics parse correctly

- **WHEN** a numeric or ratio column (`%`, `Coverage`, `Time`, `Steps`, `Tokens (cost)`,
  `Result`) is sorted
- **THEN** rows SHALL be ordered by the underlying numeric value rather than by the
  displayed text (thousands separators and the `X/Y` ratio SHALL be parsed, not compared
  as strings)

### Requirement: Сверка публикуемых ₽ с биллингом провайдера

Опубликованная в форке оценка стоимости волны SHALL быть согласована с фактическим
биллингом провайдера, когда доступен экспорт активности; при расхождении строки SHALL
перепубликовываться по данным биллинга, а не по сырым токенам раннера.

#### Scenario: Экспорт провайдера совпадает с раннером

- **WHEN** опубликованные ₽ волны сверяются с дедуплицированным экспортом провайдера
- **THEN** расхождение SHALL быть в пределах допуска, а строка — считаться согласованной

#### Scenario: Расхождение из-за многократного учёта usage

- **WHEN** сырые токены раннера учитывают один и тот же usage более одного раза
- **THEN** опубликованные tokens/₽ SHALL быть пересчитаны по биллингу провайдера, а
  pass-рейтинг SHALL NOT меняться

#### Scenario: Перекрывающиеся экспорты

- **WHEN** во вход подано несколько экспортов с перекрывающимися строками
- **THEN** строки SHALL дедуплицироваться по `(date, key, model, input, output, cost)`
  перед суммированием
