#!/usr/bin/env python3
"""Build the Russian mirror of the upstream Measurement archive (OpenSpec add-archive-ru-mirror).

Reads the UPSTREAM page ``docs/archive.html`` (never modified) and writes a
fork-owned ``docs/archive.ru.html``:

* prose is translated via an explicit EN->RU replacement map;
* every identifier, number, URL, ``data-wave-id``/``data-harness``/``data-group``
  and table cell is kept VERBATIM (spec: canonical identifiers are not translated);
* fork chrome is added: language chip RU⇄EN, a "translation" disclaimer, and a
  link back to the fork page. Upstream styling (``styles.css``) is preserved.

Usage:
    python3 scripts/build_archive_ru.py            # write docs/archive.ru.html
    python3 scripts/build_archive_ru.py --check    # exit 1 if stale
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "docs" / "archive.html"
OUT = REPO_ROOT / "docs" / "archive.ru.html"

UPSTREAM_REPO = "https://github.com/ai-forever/harness-bench-fast"
FORK_REPO = "https://github.com/Alex-Kul777/harness-bench-fast"

# Banner + chip CSS (self-contained; upstream styles.css still applies).
BANNER_CSS = (
    "<style>"
    ".fork-banner{font:14px/1.5 system-ui,sans-serif;background:#eef3ff;"
    "border-bottom:1px solid #c7d4f7;padding:.5rem .9rem;display:flex;flex-wrap:wrap;"
    "gap:.5rem 1rem;align-items:center;justify-content:space-between}"
    ".fork-banner a{color:#1b3a8f}"
    ".langchip{display:inline-flex;gap:.35rem;align-items:center}"
    ".langchip a,.langchip span{padding:.05rem .45rem;border:1px solid #c7d4f7;border-radius:.45rem;text-decoration:none}"
    ".langchip .active{background:#1b3a8f;color:#fff;border-color:#1b3a8f}"
    "</style>"
)

# One-line EN->RU replacements applied to the raw document (longest first at run time).
REPL: list[tuple[str, str]] = [
    # --- head ---
    ('<html lang="en">', '<html lang="ru">'),
    ("<title>harness-bench · Measurement archive</title>",
     "<title>harness-bench · Архив измерений (RU)</title>"),
    ('content="harness-bench task-set v0.19.0 (471 tasks), including wave 19: '
     'evidence-backed stopping. Published scores are from v0.18.0 and earlier."',
     'content="harness-bench, набор v0.19.0 (471 задача), включая волну 19. '
     'Опубликованные результаты — из v0.18.0 и ранее. Русское зеркало апстримной страницы."'),
    # --- hero ---
    ("Current task set: v0.19.0 · Wave 19", "Текущий набор: v0.19.0 · Волна 19"),
    ("471 mechanically verified agent tasks", "471 механически проверенная агентская задача"),
    ("Compare agent harnesses on coding, tools, memory and long-context tasks.\n"
     "            Every result is checked mechanically. Wave 19 adds 40 tasks: solve, diagnose or stop "
     "within 120 seconds. Individual task credit is the main score; matched-pair credit is an additional "
     "metric. Calibration of the released protocol is pending.",
     "Сравнение агентских харнессов на задачах по коду, инструментам, памяти и длинному контексту.\n"
     "            Каждый результат проверяется механически. Волна 19 добавляет 40 задач: решить, "
     "поставить диагноз или остановиться за 120 секунд. Основной счёт — по отдельным задачам; "
     "счёт по парам — дополнительная метрика. Калибровка выпущенного протокола — в процессе."),
    ("View results", "Смотреть результаты"),
    ("Run the bench", "Запустить бенч"),
    ('<span class="metric">471</span><span class="label">tasks</span>',
     '<span class="metric">471</span><span class="label">задача</span>'),
    ('<span class="metric">40</span><span class="label">wave 19 tasks · 20 pairs</span>',
     '<span class="metric">40</span><span class="label">задач волны 19 · 20 пар</span>'),
    ('<span class="metric">80</span><span class="label">full v0.18.0 runs published</span>',
     '<span class="metric">80</span><span class="label">полных прогонов v0.18.0 опубликовано</span>'),
    ('<span class="metric">v0.19.0</span><span class="label">task-set version</span>',
     '<span class="metric">v0.19.0</span><span class="label">версия набора</span>'),
    ("No v0.19.0 model results yet. Published scores below use 431 or fewer tasks and are historical.",
     "Результатов моделей на v0.19.0 пока нет. Опубликованные ниже результаты используют 431 задачу "
     "или меньше и являются историческими."),
    # --- task strip labels ---
    ("Files, code, data & memory", "Файлы, код, данные и память"),
    ("Agent workflows & Git", "Агентские сценарии и Git"),
    ("Skills & adversarial tasks", "Скиллы и состязательные задачи"),
    ("Terminal & CLI composition", "Терминал и композиция CLI"),
    ("Long context", "Длинный контекст"),
    ("Wave 18 · Tool reflection", "Волна 18 · Рефлексия инструментов"),
    ("Wave 19 · Feasibility", "Волна 19 · Реализуемость"),
    # --- section headings ---
    ("Historical leaderboard · v0.18.0 · 431 tasks", "Исторический лидерборд · v0.18.0 · 431 задача"),
    ("Tools and budgets vary across runs. Token counts are raw telemetry; 0 means unavailable.",
     "Инструменты и бюджеты отличаются между прогонами. Счётчики токенов — сырая телеметрия; "
     "0 означает «нет данных»."),
    ("Measurement notes: models, budgets and audits", "Заметки об измерениях: модели, бюджеты и аудиты"),
    ("Historical full-run scores on 431 tasks.", "Исторические результаты полных прогонов на 431 задаче."),
    ("Light / Medium leaderboard →", "Лидерборд Light / Medium →"),
    ("Runs with zero solved tasks", "Прогоны с нулём решённых задач"),
    ("No runs match these filters.", "Нет прогонов, подходящих под эти фильтры."),
    ("Earlier results: task-set v0.17.0, 411 tasks", "Ранние результаты: набор v0.17.0, 411 задач"),
    ("Full 411-task runs. Different task-set versions are not directly comparable.",
     "Полные прогоны на 411 задачах. Разные версии набора напрямую несопоставимы."),
    ("Measurement notes: v0.17 and cowork", "Заметки об измерениях: v0.17 и cowork"),
    ("Earlier results: task-set v0.16.0, 391 tasks", "Ранние результаты: набор v0.16.0, 391 задача"),
    ("Measurement notes: v0.16", "Заметки об измерениях: v0.16"),
    # --- v016 filter bar ---
    ('aria-label="Leaderboard filters"', 'aria-label="Фильтры лидерборда"'),
    ('data-filter-group="all" aria-pressed="true">All models<', 'data-filter-group="all" aria-pressed="true">Все модели<'),
    ('data-filter-group="gigachat" aria-pressed="false">GigaChat<', 'data-filter-group="gigachat" aria-pressed="false">GigaChat<'),
    ('data-filter-group="other" aria-pressed="false">Other models<', 'data-filter-group="other" aria-pressed="false">Другие модели<'),
    ("<span>Harness</span>", "<span>Харнесс</span>"),
    ("All harnesses", "Все харнессы"),
    ("Claude Code CLI", "Claude Code CLI"),
    ("of 14 shown", "из 14 показано"),
    # --- method + footer ---
    ("Verification", "Проверка"),
    ("No LLM judge", "Без LLM-судьи"),
    ("Each task is checked mechanically: exact files, JSON shape and\n"
     "            values, pytest outcomes, SQLite/XLSX checks, regex matches, and\n"
     "            deterministic command outputs.",
     "Каждая задача проверяется механически: точные файлы, структура и значения JSON, "
     "результаты pytest, проверки SQLite/XLSX, совпадения regex и детерминированные "
     "выводы команд."),
    ("harness-bench · Measurement archive · historical task sets",
     "harness-bench · Архив измерений · исторические наборы"),
    ("Source on GitHub", "Исходники на GitHub"),
    # --- meta lines (leaderboard rows) ---
    ("@mean1 · Long wave ", "@mean1 · Длинная волна "),
    ("@mean3 · SD 0.6 pp · Long mean 63.3% · Reflection mean 85.0% · Mean steps ",
     "@mean3 · SD 0.6 п.п. · среднее long 63.3% · среднее reflection 85.0% · средние шаги "),
    ("@mean1 · Mimir local CLI · ", "@mean1 · Mimir local CLI · "),
    ("Long wave ", "Длинная волна "),
    ("Reflection ", "Рефлексия "),
    ("Steps ", "Шаги "),
    ("Tokens ", "Токены "),
    ("(metrics absent)", "(метрики отсутствуют)"),
    ("(metric absent)", "(метрика отсутствует)"),
    ("(raw; 0 means absent)", "(сырые; 0 = нет данных)"),
    ("(coverage ", "(покрытие "),
    ("(native audit, including child sessions)", "(нативный аудит, вкл. дочерние сессии)"),
    ("(raw, unaudited)", "(сырые, без аудита)"),
    ("(raw)", "(сырые)"),
    ("Steps — · Tokens —", "Шаги — · Токены —"),
    ("Mean steps ", "Средние шаги "),
    ("Mean tokens ", "Средние токены "),
    ("Measurement notes", "Заметки об измерениях"),
    ("Zero-score runs (", "Прогоны с нулём ("),
    ("Task set composition", "Состав набора задач"),
    ("Current benchmark summary", "Текущая сводка бенчмарка"),
    ("Completed v0.18.0 results", "Завершённые результаты v0.18.0"),
    ('<p class="eyebrow">Task-set v0.18.0</p>', '<p class="eyebrow">Набор v0.18.0</p>'),
    ('<span class="eyebrow">History</span>', '<span class="eyebrow">История</span>'),
    ('<a href="#tasks">Task Set</a>', '<a href="#tasks">Набор задач</a>'),
    ('<a href="#history">History</a>', '<a href="#history">История</a>'),
    ("· 431 tasks</a>", "· 431 задача</a>"),
    ("· 411 tasks</a>", "· 411 задач</a>"),
]

# Whole-paragraph translations, keyed by a unique EN marker; the enclosing <p>
# inner HTML is replaced entirely (tags/links preserved in the RU version).
PARAS: list[tuple[str, str]] = [
    ("completed October 17, 2026; 377/431",
     "<strong>Secret-700-SFT + native deepagents 0.6.12:</strong> завершено 17 октября 2026; "
     "377/431 (87.5%), 54 провала, нет незавершённых задач и неразрешённых инфраструктурных сбоев. "
     "Проверены все 431 уникальная задача. Обычные 368/391 (94.1%), длинный контекст 0/20, "
     "рефлексия инструментов 9/20 (45.0%). Девятнадцать длинных задач упали по таймауту после 7 200 с; "
     "оставшаяся не прошла верификатор. k=1, Linux bwrap, параллелизм 2; reasoning включён, effort "
     "по умолчанию, история передана; temperature 0.8, top_p 0.95, макс. вывод 81 920 токенов на ответ. "
     "Таймаут задачи 900 с, пороги long/reflection 7 200/1 800 с. Ограничения reasoning интерактивного "
     "клиента не применялись."),
    ("Secret-700-SFT telemetry",
     "Телеметрия Secret-700-SFT: шаги покрывают 402/431 задач; токены — 409/431. Сырые счётчики токенов "
     "содержат дублирующее usage и не включают финальную телеметрию части прерванных задач. Нативные "
     "трассы и API-итоги пока не сверены; это не подтверждённые расходы или стоимость. Валидация счёта "
     "не зависит от аудита телеметрии. <a href=\"secret-700-sft-progress.json\">Публичный агрегат</a> "
     "заменяет прежний снимок на 409 задач. Сырые трассы и идентификаторы бэкенда остаются приватными."),
    ("MultiTool CLI 1.14.33",
     "<strong>MultiTool CLI 1.14.33:</strong> завершено 2026-10-14, @mean1 на всех 431 задачах, Linux bwrap, "
     "параллелизм 4. GLM-5.3 (<code>z-ai/glm-5.3</code>, high): обычные 386/391, long 10/20, reflection 15/20; "
     "Claude Haiku 4.5 (<code>anthropic/claude-haiku-4.5</code>, default): 371/391, 1/20, 4/20. Кастомный профиль "
     "оценки: макс. вывод 16 384 токенов на ответ (нативный дефолт CLI 32 000), таймаут 900 с с порогами "
     "long 7200 с и reflection 1800 с. Шаги считают нативные попытки инструментов; токены включают input, "
     "output, reasoning и cache по всем родительским и дочерним сессиям, по нативной SQLite-телеметрии для "
     "всех 431 задач. Итоги токенов — не денежная стоимость. Попытки, затронутые транспортом, заменены после "
     "починки стриминга; транзиентных инфраструктурных сбоев не осталось. Шесть long-провалов GLM исчерпали "
     "бюджет вывода и сохранены. API-ответы подтвердили запрошенные ID моделей, но отпечатка сборки не дали."),
    ("cowork + GLM-5.3 (OpenRouter, high):</strong> 376/431",
     "<strong>cowork + GLM-5.3 (OpenRouter, high):</strong> 376/431 (87.2%), k=1, завершено двумя "
     "непересекающимися партиями 13–14 октября: сохранено 367/411 плюс 9/20 reflection. Устаревшие определения "
     "задач и CLI-раннер проверены без изменений. Локальный CLI Mimir с сокращёнными оценочными инструментами, "
     "Linux bwrap, temperature 0.2, max_tokens 16 384, 80 вызовов инструментов, таймаут 900 с с порогами задач. "
     "Нативная телеметрия покрывает 429/431; шаги и токены — нижние оценки, когда у задач по таймауту нет "
     "финальной телеметрии. Прежний технический ретрай задачи 397 остаётся раскрытым в заметках v0.17."),
    ("Scores and ranks here use the full historical",
     "Счёт и ранги здесь используют полный исторический набор из 431 задачи. @mean3 остаётся средним по трём "
     "прогонам. Счёт использует определения v0.18.0 для этого снимка результата на 431 задаче; волна 19 не "
     "включена. Шаги и токены ниже — итоги полного прогона."),
    ("Completed, validated full runs measured",
     "Завершённые валидированные полные прогоны, измерены 2026-10-02–14. Матрица PROM ещё идёт; неполные прогоны "
     "и инфраструктурные сбои исключены. Матрица PROM использует по одному прогону на конфигурацию (k=1), "
     "восемь слотов задач на матрицу, изоляцию bwrap. Отдельный прогон maestro harness использовал четырёх "
     "воркеров и bwrap."),
    ("All listed PROM matrix runs scored 0/20",
     "Все перечисленные прогоны матрицы PROM набрали 0/20 на длинном контексте. Рефлексия: GigaChat 2 Reasoning "
     "+ mini-SWE-agent: 8/20; GigaChat 2 Reasoning + OpenCode: 1/20; GigaChat 2 Reasoning + deepagents: 1/20; "
     "GigaChat 2 Pro + mini-SWE-agent: 1/20; GigaChat 2 Reasoning + deepagents (GigaChat profile): 2/20; "
     "maestro harness: 1/20 (и 0/20 long); остальные перечисленные прогоны 0/20. Инструменты, профили и бюджеты "
     "различаются; эти одиночные прогоны не изолируют эффект харнесса."),
    ("GigaChat 2 and GigaChat 3 Lightning (PROM): build",
     "GigaChat 2 и GigaChat 3 Lightning (PROM): сборка 32.4.16.3. GigaChat 2 Pro и GigaChat 3 Pro (PROM): сборка "
     "32.4.30.3. GigaChat 2 Max, GigaChat 3 Ultra и GigaChat 3.5 (PROM): сборка 32.9.16.9. GigaChat 2 Reasoning "
     "(PROM): сборка 3.5.16.9, reasoning medium. Для не-Reasoning моделей reasoning: default для deepagents, "
     "pi-mono, mini-SWE-agent и OpenCode; не запрашивался для OpenHands; medium (нативный дефолт) для Hermes. "
     "Версии рантайма и детали интеграции — в README."),
    ("GigaChat 2 Max + Hermes completed with 0/431",
     "GigaChat 2 Max + Hermes завершились с 0/431 и без инфраструктурных сбоев, зафиксированных раннером; "
     "причина полностью провального результата выясняется."),
    ("Three deepagents runs without profile",
     "‡ Три прогона deepagents без профиля (GigaChat 2 Pro, GigaChat 2 Reasoning и GigaChat 3 Pro) имели "
     "HTTP 422 CONTEXT_TOO_LONG, ошибочно классифицированный как инфраструктурные ошибки. Это нормальные "
     "провалы модели/харнесса. Исправленные производные отчёты сохраняют полный знаменатель и исходные счёты; "
     "исходные отчёты не изменены."),
    ("Telemetry: 0 means absent from the artifact.",
     "Телеметрия: 0 означает «нет в артефакте». * Токены deepagents — сырые, без аудита счётчики с известным "
     "дублирующим учётом usage, а не подтверждённые расходы или стоимость. Счётчики CLI ждут нативного аудита "
     "трасс, кроме зафиксированных метрик mini-SWE-agent и maestro harness."),
    ("maestro harness:</strong>",
     "<strong>maestro harness:</strong> <a href=\"https://github.com/AIRI-Institute/maestro-core/tree/9c2ac0d4aedb4bb8bfd53e9694bbbac05c4a725c\">MAESTRO CARL</a> "
     "с кастомным адаптером shell-кодинга и реплеем истории reasoning — не готовый апстримный кодинг-агент. "
     "Измерено 2026-10-05, k=1; без compaction, self-critic и субагентов. Обычные 247/391 (63.2%), long 0/20, "
     "reflection 1/20; инфраструктурных сбоев нет. Бюджет: 200 ходов модели / 900 с с нативными порогами задач, "
     "max_tokens 16384. Шаги считают завершённые ответы модели; итоги токенов сверены с нативными трассами для "
     "всех 431 задач. Время выполнения 63.8 минуты. Этот результат описывает данную конфигурацию адаптера; сырые "
     "трассы приватны."),
    ("Earlier task-set versions are historical",
     "Ранние версии набора историчны и не сравнимы напрямую с v0.18.0."),
    ("One complete k=1 run on the full v0.18.0",
     "Один полный прогон k=1 на наборе v0.18.0, включая волны длинного контекста и рефлексии инструментов. "
     "dsh 0.2.0-rc.2 headless, профиль hbf, DeepSeek V4.1 Flash (<code>deepseek-v4.1-flash</code>), reasoning "
     "high, параллелизм 3, изоляция Linux bwrap; измерено 2026-10-03/04."),
    ("Server-outage attempts were replaced",
     "Попытки из-за падения сервера заменены восстановительными. Последующий повтор снова попал на сбой и "
     "исключён; это не mean@3. Шаги и токены покрывают 430/431 задач и являются нижними оценками, т.к. usage для "
     "задачи по таймауту отсутствует. См. <a href=\"https://github.com/ai-forever/harness-bench-fast#results-task-set-v0180-431-tasks\">заметки измерений в README</a>."),
    ("Free-code 2.1.251-free-code.1 + DeepSeek",
     "Free-code 2.1.251-free-code.1 + DeepSeek V4.1 Flash Uncensored FP8 (high): 419/431 (97.2%), @mean3, "
     "измерено 2026-10-04/05. Отдельные прогоны: 421, 416, 420; выборочное стандартное отклонение 0.6 п.п. "
     "Средний обычный счёт 99.6%, long 63.3%, reflection 85.0%; pass@3 425/431 (98.6%), pass^3 409/431 (94.9%). "
     "Сбоев сервера нет. Шаги и токены — округлённые средние по прогонам; покрытие телеметрии 430/431, 428/431, "
     "430/431, токены независимо не аудированы."),
    ("Pi-mono 0.73.1 + DeepSeek",
     "Pi-mono 0.73.1 + DeepSeek V4.1 Flash Uncensored FP8 (high): 423/431 (98.1%), @mean1, измерено 2026-10-05, "
     "параллелизм 3, bwrap; обычные 391/391, long 13/20, reflection 19/20. Инфраструктурных сбоев не "
     "зафиксировано. Шаги и токены отсутствуют в артефакте (показаны как 0). Обе конфигурации используют "
     "таймаут 900 с с порогами задач 7200 с для long и 1800 с для reflection. Результат dsh — @mean1."),
    ("Additional completed DeepSeek",
     "Дополнительные завершённые прогоны DeepSeek V4.1 Flash Uncensored FP8 (high), измерены 2026-10-05/06: "
     "mini-SWE-agent 2.4.6, deepagents 0.6.12, OpenCode 1.18.33 и Hermes (ревизия 5307e93252ac), каждый @mean1 "
     "на всех 431 задачах, параллелизм 3, Linux bwrap. Прерванные задания возобновлялись с чекпоинтов "
     "завершённых задач; инфраструктурных сбоев в финальных отчётах не осталось. Сырые счётчики токенов "
     "независимо не аудированы; телеметрия deepagents покрывает 429/431 задач и имеет известный дублирующий "
     "учёт usage. Ноль означает отсутствие телеметрии."),
    ("GLM-5.3 Flash Uncensored",
     "GLM-5.3 Flash Uncensored (<code>glm-5.3-flash-uncensored</code>), reasoning high: восемь завершённых "
     "прогонов @mean1, измерены 2026-10-06/07 на v0.18.0, Linux bwrap, параллелизм 3, с теми же замороженными "
     "версиями харнессов, что и матрица DeepSeek. Инфраструктурные попытки заменены ретраями; в финальных "
     "отчётах инфраструктурных ошибок нет. Телеметрия токенов сырая и независимо не аудирована; ноль означает "
     "отсутствие."),
    ("The DeepSeek V4.1 Flash and GLM-5.3 Flash measurements",
     "Измерения DeepSeek V4.1 Flash и GLM-5.3 Flash здесь используют снимки dealignai uncensored FP8, как "
     "указал владелец стенда: <a href=\"https://huggingface.co/dealignai/DeepSeek-V4.1-Flash-UNCENSORED-FP8\">dealignai/DeepSeek-V4.1-Flash-UNCENSORED-FP8</a> "
     "и <a href=\"https://huggingface.co/dealignai/GLM-5.3-Flash-UNCENSORED-FP8\">dealignai/GLM-5.3-Flash-UNCENSORED-FP8</a>. "
     "Эти счёты описывают модифицированные снимки и не являются измерениями оригинальных базовых чекпоинтов."),
    ("commit 8c76e8b0c)",
     "GigaCode Universal Agent <a href=\"https://gitverse.ru/gigacode-ai/universal-agent\">gc-cli 0.4.0</a> "
     "(коммит 8c76e8b0c) + DeepSeek V4.1 Flash Uncensored FP8 (high): 419/431 (97.2%), @mean1, измерено "
     "2026-10-09, параллелизм 3, Linux bwrap через run-cli; обычные 389/391, long 12/20, reflection 18/20. "
     "Инфраструктурных сбоев, ретраев и таймаутов нет. Reasoning основного цикла high, вспомогательные вызовы "
     "finish-gate/summary без thinking; guard'ы и встроенные скиллы отключены. Шаги считают ответы модели; токены "
     "— собственные счётчики агента (покрытие 431/431), независимо не аудированы. Таймаут 900 с с порогами "
     "7200 с для long и 1800 с для reflection."),
    ("0.4.0 + GLM-5.3 (<code>z-ai/glm-5.3</code>, upstream",
     "GigaCode Universal Agent gc-cli 0.4.0 + GLM-5.3 (<code>z-ai/glm-5.3</code>, апстрим-веса через "
     "OpenRouter, reasoning high): 423/431 (98.1%), @mean1, измерено 2026-10-09/10 с той же схемой "
     "run-cli/bwrap; обычные 388/391, long 16/20, reflection 19/20. Инфраструктурных сбоев нет; две long-задачи "
     "упали по таймауту 7200 с и считаются провалами. Шаги и токены — собственные счётчики агента с покрытием "
     "429/431 (нижние оценки), независимо не аудированы. Это не снимок <code>glm-5.3-flash-uncensored</code> "
     "FP8 из матрицы на восьми харнессах и с ним несопоставимо."),
    ("harness matrix, all six runs completed",
     "Матрица харнессов GLM-5.3 (<code>z-ai/glm-5.3</code>, апстрим-веса через OpenRouter, reasoning high), "
     "все шесть прогонов завершены 2026-10-09/10, каждый @mean1 на всех 431 задачах с теми же замороженными "
     "рантаймами, что и матрица DeepSeek, Linux bwrap, параллелизм 3: OpenCode 413/431 (обычные 386, long 12, "
     "reflection 15), deepagents 408/431 (381/14/13), mini-SWE-agent 407/431 (385/9/13), pi-mono 396/431 "
     "(376/6/14), OpenHands 404/431 (384/6/14), Hermes 402/431 (387/3/12). Инфраструктурных сбоев не осталось; "
     "задачи по таймауту считаются провалами. Телеметрия токенов сырая и независимо не аудирована; ноль "
     "означает отсутствие. Несопоставимо со строками <code>glm-5.3-flash-uncensored</code> FP8."),
    ("further OpenRouter runs with the same",
     "GigaCode Universal Agent gc-cli 0.4.0, дальнейшие прогоны через OpenRouter с той же схемой run-cli/bwrap, "
     "@mean1: Kimi-K3 (<code>moonshotai/kimi-k3</code>, reasoning high), измерено 2026-10-10/11: 417/431 "
     "(96.7%), обычные 387/391, long 13/20, reflection 17/20; шесть транзиентных ошибок шлюза заменены "
     "ретраями, инфраструктурных сбоев не осталось; пять задач упали по таймауту и считаются провалами; "
     "покрытие usage 426/431. GPT-6 Luna (<code>openai/gpt-6-luna</code>), измерено 2026-10-11: 388/431 "
     "(90.0%), обычные 376/391, long 3/20, reflection 9/20. Шлюз принимает вызовы инструментов для этой модели "
     "на Chat Completions только с <code>reasoning_effort: none</code>, поэтому у прогона нет reasoning и он "
     "несопоставим со строками с высоким effort; три задачи упали по таймауту; покрытие usage 428/431. "
     "Токены — собственные счётчики агента, независимо не аудированы."),
    ("Codex CLI 0.159.3 + GPT-6.1 Sol",
     "Codex CLI 0.159.3 + GPT-6.1 Sol (<code>gpt-6.1-sol</code>), reasoning low: завершено @mean1 2026-10-07, "
     "420/431 (97.4%). Обычные 388/391, long 19/20, reflection 13/20; инфраструктурных сбоев в финальном отчёте "
     "нет. Linux bwrap, параллелизм 3, ChatGPT OAuth, мультиагент отключён, без пользовательских конфигов. "
     "Таймаут задачи 900 с с порогами 7200 с для long и 1800 с для reflection. Нативные сессии подтверждают "
     "модель и low effort. Токены — сырая телеметрия раннера, независимо не аудированы. Сетевой транспорт — "
     "end-to-end TLS через Mac CONNECT/SSH-туннель; раннер и выполнение задач оставались на стенде."),
    ("cowork:</strong> Mimir local CLI",
     "<strong>cowork:</strong> локальный CLI Mimir · измерено 13 октября. GLM-5.3 high: 16 384 токена / "
     "80 вызовов инструментов; Haiku: 2 048 / 12."),
    ("Every run covers the full 411-task set",
     "Каждый прогон покрывает полный набор 411 задач, включая волну длинного контекста (задачи 392–411). "
     "По одному полному прогону на связку харнесс + модель, измерено 2026-09-29/30, кроме cowork (2026-10-13). "
     "DeepSeek V4.1 Flash, GPT-6 Luna и GLM-5.3 идут на стоковом deepagents с reasoning effort high (GPT-6 Luna "
     "через Responses API); Claude Haiku 4.5 со встроенным профилем Anthropic. Строки deepagents GigaChat "
     "используют <code>deepagents-gigachat</code> 0.0.4 через нативный раннер, сборки 32.9.16.9 (xxxB, internal "
     "version) и 3.5.16.9 (Ultra Reasoning, PROM); все прогоны Ultra Reasoning используют клиентский обход "
     "дефекта function-calling в PROM (см. README). Четыре CLI-прогона GigaChat используют mini-SWE-agent "
     "2.4.6 или pi-mono 0.73.1 в bwrap: reasoning default для internal version, medium с реплеем истории "
     "reasoning для PROM. Инструменты, изоляция и бюджеты различаются между конфигурациями; это одиночные "
     "прогоны, а не контролируемое сравнение одних харнессов. Счётчики токенов CLI аудированы по нативным "
     "трассам и могут недосчитывать прерванные ответы. Исторические итоги токенов deepagents — телеметрия "
     "раннера, которая может пересчитывать дублирующие поля usage; это не независимо подтверждённые оценки "
     "стоимости. Счёт mini-SWE PROM включает одну поправку на сервисную телеметрию, ошибочно принятую за "
     "утечку секрета; см. <a href=\"https://github.com/ai-forever/harness-bench-fast#historical-results-task-set-v0170-411-tasks\">заметки аудита в README</a>."),
    ("cowork:</strong> local CLI adapter",
     "<strong>cowork:</strong> локальный CLI-адаптер вокруг цикла модели Mimir, продакшн-промпт и сокращённые "
     "оценочные инструменты; без полного стека Yggdrasil/Restate/MCP/HITL. Измерено 2026-10-13, k=1, "
     "замороженный v0.17.0, OpenRouter, Linux bwrap, параллелизм 8, temperature 0.2. GLM-5.3 high: 16 384 токена "
     "на ответ / 80 вызовов инструментов; Haiku 4.5 default: 2 048 / 12. Таймаут 900 с с порогами long-волны. "
     "Нативные метрики покрывают 411/411 сохранённых попыток задач. С учётом инфраструктурных попыток: GLM "
     "87 296 288 токенов, Haiku 37 809 500. Задача GLM 397 получила один технический ретрай после HTTP 400 "
     "неясного происхождения и стримингового реплея с 502; ретрай упал на лимите инструментов, поэтому счёт не "
     "изменился. Бюджеты различаются; оценки дисперсии по повторам нет. Детали аудита — в README."),
    ("Results below use the full 391-task set",
     "Результаты ниже используют полный набор из 391 задачи (набор v0.16.0). Текущий набор — v0.18.0 (431 "
     "задача); эти строки не являются прогонами по нему, и аудит v0.16.0 уже сделал ранние счёты "
     "несопоставимыми. Приведён один полный прогон на связку харнесс + модель (k=1). 13 архивных прогонов "
     "benchmark-server сверены с их JSON-артефактами 2026-10-01; отдельно записанный прогон Pi также сохранён. "
     "Это дата верификации, а не измерения. Разные харнессы и профили измеряют полные конфигурации, а не только "
     "веса модели. Reasoning — default, если не помечено явно; исторические артефакты не устанавливают "
     "провайдерский дефолт. Фильтр по группе моделей или одному харнессу; шаги и токены показываются только там, "
     "где харнесс их отдаёт. Строки GigaChat — internal version, сборка 32.9.23.6; строки с профилем GigaChat "
     "измерены с <code>deepagents-gigachat</code> 0.0.3 (см. README о влияющем на результат изменении пина "
     "на 0.0.4). Один прогон на конфигурацию не устанавливает повторяемость или значимость малых различий счёта."),
]


def _translate(text: str) -> str:
    for en, ru in sorted(REPL, key=lambda p: -len(p[0])):
        text = text.replace(en, ru)
    for marker, ru_inner in PARAS:
        text = _replace_element_containing(text, marker, ru_inner, tag="p")
    return text


def _replace_element_containing(doc: str, marker: str, ru_inner: str, tag: str = "p") -> str:
    """Replace the inner HTML of the first <tag>...</tag> element that contains *marker*."""
    i = doc.find(marker)
    if i == -1:
        print(f"[warn] marker not found: {marker[:60]!r}", file=sys.stderr)
        return doc
    open_at = doc.rfind(f"<{tag}", 0, i)
    close_at = doc.find(f"</{tag}>", i)
    if open_at == -1 or close_at == -1:
        print(f"[warn] element bounds not found: {marker[:60]!r}", file=sys.stderr)
        return doc
    open_end = doc.find(">", open_at)
    if open_end == -1 or open_end > close_at:
        return doc
    return doc[: open_end + 1] + ru_inner + doc[close_at:]


def _inject_chrome(text: str) -> str:
    # RU⇄EN chip + disclaimer under the site header.
    banner = (
        '<div class="fork-banner" lang="ru">'
        '<span>Перевод; источник — англоязычная версия апстрима '
        '(<a href="archive.html">archive.html</a>). '
        'Форк: <a href="fork.html">fork.html</a></span>'
        '<span class="langchip"><span class="active">RU</span>'
        '<a href="archive.html" hreflang="en">EN</a></span>'
        "</div>"
    )
    text = text.replace("</head>", BANNER_CSS + "\n  </head>", 1)
    # insert banner right after the closing </header>
    idx = text.find("</header>")
    if idx != -1:
        text = text[: idx + len("</header>")] + "\n    " + banner + text[idx + len("</header>"):]
    # footer: add fork/upstream repo links
    text = text.replace(
        '<a href="https://github.com/ai-forever/harness-bench-fast">Исходники на GitHub</a>',
        f'<a href="{UPSTREAM_REPO}">Исходники на GitHub (upstream)</a> · '
        f'<a href="{FORK_REPO}">Репозиторий форка</a> · '
        '<a href="fork.html">Результаты форка</a>',
    )
    # nav: add a link to the fork page next to the GitHub link
    text = text.replace(
        '<a href="https://github.com/ai-forever/harness-bench-fast">GitHub</a>',
        '<a href="https://github.com/ai-forever/harness-bench-fast">GitHub</a>'
        '<a href="fork.html">Форк</a>',
    )
    # canonical EN mirror pointer
    text = text.replace(
        "</head>",
        '<link rel="alternate" hreflang="en" href="archive.html">\n  </head>',
        1,
    )
    return text


def build() -> str:
    src = SRC.read_text(encoding="utf-8")
    return _inject_chrome(_translate(src))


def main(argv: list[str]) -> int:
    check = "--check" in argv
    out = build()
    current = OUT.read_text(encoding="utf-8") if OUT.exists() else None
    if check:
        if current != out:
            print("STALE: docs/archive.ru.html — run build_archive_ru.py", file=sys.stderr)
            return 1
        print("docs/archive.ru.html: up to date")
        return 0
    if current != out:
        OUT.write_text(out, encoding="utf-8")
        print("docs/archive.ru.html: written")
    else:
        print("docs/archive.ru.html: up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
