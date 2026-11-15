#!/usr/bin/env python3
"""Locale catalog for the fork layer (RU default + EN mirror).

Single source of UI strings for the fork generators (build_fork_home.py,
build_fork_pages.py, build_fork_results.py). Spec prose may be Russian, but the
canonical result-table column headers stay English in BOTH locales (see
result-reporting lint), so those keys keep the same value in ru and en.

Usage:
    from fork_i18n import t, DEFAULT_LOCALE, LOCALIZED_SUFFIX, locale_filename

    t("en", "nav_fork_index")        # "Fork index"
    t("ru", "subset_pilot", n=13)    # "пилот: 13 из 90 (урезанный)"
    locale_filename("fork", "en")    # "fork.en"
"""
from __future__ import annotations

DEFAULT_LOCALE = "ru"
LOCALES = ("ru", "en")
LOCALIZED_SUFFIX = {"ru": "", "en": ".en"}


def locale_filename(stem: str, locale: str) -> str:
    """``('fork', 'en') -> 'fork.en'``; ``('fork', 'ru') -> 'fork'``."""
    return f"{stem}{LOCALIZED_SUFFIX.get(locale, '')}"


def other_locale(locale: str) -> str:
    return "en" if locale == "ru" else "ru"


CATALOG: dict[str, dict[str, str]] = {
    "ru": {
        # ---- shared chrome ----
        "html_lang": "ru",
        "lang_switch": "EN",
        "lang_switch_title": "Switch to English",
        "fork_index": "Индекс форка",
        "capability_sample": "Capability sample (20)",
        "nav_guidance": "Выбор модели",
        "brand_home": "harness_bench_fast",
        # ---- fork.html ----
        "home_title": "harness_bench_fast · Результаты форка",
        "home_desc": "Результаты форка harness_bench_fast: Shot List (52), Light (391) и Medium (90) на одной странице с табами. Механически проверено.",
        "home_eyebrow": "Результаты форка · наши прогоны",
        "home_h1": "Наши прогоны.<br>Три набора, одна страница.",
        "home_lead": "Наши прогоны Pi / OpenClaw / Hermes на замороженных наборах Shot List (52), Light (391) и Medium (90). Реальные задачи, механически проверено.",
        "tab_shotlist_sub": "стратифицированная выборка",
        "tab_light_sub": "замороженный набор",
        "tab_medium_sub": "long · reflect · feasibility · wave21",
        "tab_medium_sample_sub": "наши урезанные · truncated",
        "search_label": "Поиск по модели или харнессу",
        "search_placeholder": "напр. Pi, OpenClaw, DeepSeek",
        "empty_search": "Нет строк по запросу.",
        "home_fine_print": (
            "Без LLM-судьи. Каждая задача проверена механически. Прочерк — нет замера, "
            "а не провал. <code>Version</code> = версия тасксета для наших прогонов, "
            "<code>source_version</code> апстрима — для upstream-строк."
        ),
        "footer_note": "Без LLM-судьи. Каждая задача проверена механически.",
        "footer_link": "Индекс форка →",
        "group_our_runs": "Наши прогоны",
        "group_upstream": "Апстрим для сравнения",
        "group_sample_ours": "Наши урезанные наборы (относительно полных 90)",
        "group_sample_upstream": "Апстрим на тех же задачах (только наш покрытый набор)",
        "note_sample": (
            "Наши <strong>урезанные</strong> Medium-наборы: суммарный охват прогнанных задач "
            "и пилоты. Знаменатель — полный Medium (90); в колонке <code>Coverage</code> видно, "
            "что покрытие неполное (например 66/90 и 13/90). Нижняя группа — "
            "<strong>апстрим-конфиги, пересчитанные на точно тех же задачах, что прогнали мы</strong> "
            "(наши 66), т.е. сколько из наших задач решил апстрим."
        ),
        "waves_summary": "Разбивка по волнам (long / reflect / feasibility)",
        "wave_col_config": "Конфигурация",
        "link_full_90": "Полные 90 →",
        "link_fork_tables": "Таблицы форка →",
        "autosync_header": "Автосинк: {n} прогонов",
        "subset_our_runs": "наши прогнанные: {n} из 90",
        "subset_pilot": "пилот: {n} из 90 (урезанный)",
        "subset_same_tasks": "те же {n} задач",
        "subset_our_tasks": "наши {n} задач",
        "status_partial": "partial",
        "status_complete": "полный",
        "status_pilot": "пилот (урезанный)",
        "status_upstream_subset": "апстрим (подмножество)",
        "status_upstream_notask": "апстрим · нет per-task",
        # ---- detail pages (fork-detail-*) ----
        "detail_title_suffix": "детализация прогона форка",
        "detail_eyebrow": "Детализация прогона форка",
        "detail_h2": "Задачи ({n})",
        "detail_th_task": "Задача",
        "detail_th_result": "Результат",
        "detail_th_seconds": "Секунды",
        "detail_th_tokens": "Токены",
        "detail_th_message": "Сообщение",
        "detail_meta_profile": "Профиль",
        "detail_meta_version": "Версия",
        "detail_meta_result": "Результат",
        "detail_meta_time": "Время",
        "detail_meta_steps": "Шаги",
        "detail_meta_tokens_cost": "Токены (стоимость)",
        "detail_back": "← Результаты форка",
        "detail_fine_print": "Без LLM-судьи. Каждая задача проверена механически. Источник:",
        # ---- sample / run pages (built by build_fork_pages.py) ----
        "nav_fork_results": "Результаты форка",
        "nav_manifest": "Манифест",
        "sample_title": "Capability sample ({n}) · Форк · harness_bench_fast",
        "sample_eyebrow": "Форк · capability sample · 25% от Medium 80 (392–471)",
        "sample_h1": "Capability sample ({n})",
        "sample_lead": "{model} на {harness} · {n} задач из Medium 80 (392–471). Задачи стратифицированы по бэндам из опубликованных per-config исходов (ceiling 3/3, boundary 1–2/3, floor 0/3); cost-weighted разбивка по волнам: long 3 / reflect 5 / feasibility 12.",
        "sample_rule_note": "Источник истины: <a href=\"{manifest}\">fork-sample-manifest.json</a> · правило: <a href=\"{adr}\">ADR-0003</a>",
        "sample_waves_title": "Волны",
        "sample_tasks_title": "Задачи",
        "sample_sources_title": "Источники",
        "sample_waves_note": "Волна long измерена с таймаутом 2400s, ниже её флора 7200s, поэтому помечена <strong>invalid</strong>, а не failed.",
        "sample_tasks_note": "Per-task исход по каждой из {n} задач манифеста. Неизмеренная задача — не провал, просто нет записи.",
        "sample_sources_note": "Замороженные артефакты allow-list, собранные в этот семпл (сначала <code>results/</code>, затем <code>../results/</code>).",
        "sample_fine_print": "Статическая страница, сгенерирована <code>scripts/build_fork_pages.py</code> из замороженных артефактов allow-list в <code>results/</code>. Время — wall-clock секунды; токены — компактно записанные суммы. <strong>invalid</strong> — таймаут волны ниже флора (замер не является честным сигналом способности), а не обычный fail. Неизмеренная задача — не провал.",
        "sample_footer_link": "Форк Medium 80 →",
        "status_invalid": "invalid ({reason})",
        "status_measured": "измерено",
        "run_title": "{title} · детализация прогона форка",
        "run_eyebrow": "Детализация прогона форка · Medium 80 · задачи 392–471",
        "run_lead": "{runs} {run_word} · {waves} {wave_word} измерено",
        "run_word": "прогон",
        "run_word_plural": "прогона",
        "wave_word": "волна",
        "wave_word_plural": "волны",
        "run_waves_title": "Волны",
        "run_tasks_title": "Задачи",
        "run_sources_title": "Источники",
        "run_back_medium": "← Форк Medium 80",
        "run_waves_note": "Per-wave исходы этой конфигурации. Неизмеренная волна опущена (не провал).",
        "run_tasks_note": "Per-task исход каждой записанной задачи для этой конфигурации.",
        "run_tasks_empty": "Для этой конфигурации нет per-task записей.",
        "run_waves_empty": "Нет измеренных волн для этого прогона.",
        "run_sources_note": "Артефакты прогона, собранные в эту строку (сначала <code>results/</code>, затем <code>../results/</code>).",
        "run_fine_print": "Статическая страница, сгенерирована <code>scripts/build_fork_pages.py</code> из сохранённых run JSON в <code>results/</code>. Время — wall-clock минуты; токены — компактные суммы; <strong>Tokens (cost)</strong> добавляет оценку расхода в рублях (₽) — оценка, не счёт. Неизмеренная волна — не провал.",
    },
    "en": {
        # ---- shared chrome ----
        "html_lang": "en",
        "lang_switch": "RU",
        "lang_switch_title": "Переключить на русский",
        "fork_index": "Fork index",
        "capability_sample": "Capability sample (20)",
        "nav_guidance": "Model selection",
        "brand_home": "harness_bench_fast",
        # ---- fork.html ----
        "home_title": "harness_bench_fast · Fork results",
        "home_desc": "Fork results for harness_bench_fast: Shot List (52), Light (391) and Medium (90) on one tabbed page. Mechanically verified.",
        "home_eyebrow": "Fork results · our runs",
        "home_h1": "Our runs.<br>Three suites, one page.",
        "home_lead": "Our Pi / OpenClaw / Hermes runs on the frozen Shot List (52), Light (391) and Medium (90) suites. Real tasks, mechanically verified.",
        "tab_shotlist_sub": "stratified sample",
        "tab_light_sub": "frozen suite",
        "tab_medium_sub": "long · reflect · feasibility · wave21",
        "tab_medium_sample_sub": "our subsets · truncated",
        "search_label": "Search model or harness",
        "search_placeholder": "e.g. Pi, OpenClaw, DeepSeek",
        "empty_search": "No rows match your search.",
        "home_fine_print": (
            "No LLM judge. Every task is checked mechanically. A dash means no measurement yet, "
            "not a failure. <code>Version</code> = task-set version for our runs, upstream "
            "<code>source_version</code> for upstream rows."
        ),
        "footer_note": "No LLM judge. Every task is checked mechanically.",
        "footer_link": "Fork index →",
        "group_our_runs": "Our runs",
        "group_upstream": "Upstream comparison",
        "group_sample_ours": "Our subsets (truncated vs the full 90)",
        "group_sample_upstream": "Upstream on the same tasks (restricted to our covered subset)",
        "note_sample": (
            "Our <strong>truncated</strong> Medium subsets: aggregate coverage of the run tasks "
            "and pilots. The denominator is the full Medium (90); the <code>Coverage</code> column "
            "makes the incomplete coverage explicit (e.g. 66/90 and 13/90). The lower group shows "
            "<strong>upstream configs rescored on exactly the tasks we ran</strong> (our 66) — i.e. "
            "how many of our tasks the upstream solved."
        ),
        "waves_summary": "Wave breakdown (long / reflect / feasibility)",
        "wave_col_config": "Configuration",
        "link_full_90": "Full 90 →",
        "link_fork_tables": "Fork tables →",
        "autosync_header": "Auto-sync: {n} runs",
        "subset_our_runs": "our runs: {n} of 90",
        "subset_pilot": "pilot: {n} of 90 (truncated)",
        "subset_same_tasks": "the same {n} tasks",
        "subset_our_tasks": "our {n} tasks",
        "status_partial": "partial",
        "status_complete": "complete",
        "status_pilot": "pilot (truncated)",
        "status_upstream_subset": "upstream (subset)",
        "status_upstream_notask": "upstream · no per-task",
        # ---- detail pages (fork-detail-*) ----
        "detail_title_suffix": "fork run detail",
        "detail_eyebrow": "Fork run detail",
        "detail_h2": "Tasks ({n})",
        "detail_th_task": "Task",
        "detail_th_result": "Result",
        "detail_th_seconds": "Seconds",
        "detail_th_tokens": "Tokens",
        "detail_th_message": "Message",
        "detail_meta_profile": "Profile",
        "detail_meta_version": "Version",
        "detail_meta_result": "Result",
        "detail_meta_time": "Time",
        "detail_meta_steps": "Steps",
        "detail_meta_tokens_cost": "Tokens (cost)",
        "detail_back": "← Fork results",
        "detail_fine_print": "No LLM judge. Every task is checked mechanically. Source:",
        # ---- sample / run pages (built by build_fork_pages.py) ----
        "nav_fork_results": "Fork results",
        "nav_manifest": "Manifest",
        "sample_title": "Capability sample ({n}) · Fork · harness_bench_fast",
        "sample_eyebrow": "Fork · capability sample · 25% of Medium 80 (392–471)",
        "sample_h1": "Capability sample ({n})",
        "sample_lead": "{model} on {harness} · {n} tasks drawn from the Medium 80 suite (392–471). Tasks are stratified by band from published per-config outcomes (ceiling 3/3, boundary 1–2/3, floor 0/3); the cost-weighted wave split is long 3 / reflect 5 / feasibility 12.",
        "sample_rule_note": "Source of truth: <a href=\"{manifest}\">fork-sample-manifest.json</a> · rule: <a href=\"{adr}\">ADR-0003</a>",
        "sample_waves_title": "Waves",
        "sample_tasks_title": "Tasks",
        "sample_sources_title": "Sources",
        "sample_waves_note": "The long wave was measured with a 2400s timeout, below its 7200s floor, so it is marked <strong>invalid</strong> rather than failed.",
        "sample_tasks_note": "Per-task outcome for each of the {n} manifest tasks. An unmeasured task is not a failure — it simply has no recorded result yet.",
        "sample_sources_note": "Frozen allow-list artifacts aggregated into this sample (checkout <code>results/</code> first, then <code>../results/</code>).",
        "sample_fine_print": "Static page generated by <code>scripts/build_fork_pages.py</code> from the frozen allow-list artifacts in <code>results/</code>. Times are wall-clock seconds; tokens are compact recorded totals. <strong>invalid</strong> means the wave timeout was below its floor (the measurement is not a fair capability signal), not a normal fail. An unmeasured task is not a failure.",
        "sample_footer_link": "Fork Medium 80 →",
        "status_invalid": "invalid ({reason})",
        "status_measured": "measured",
        "run_title": "{title} · Fork run details",
        "run_eyebrow": "Fork run details · Medium 80 · tasks 392–471",
        "run_lead": "{runs} {run_word} · {waves} {wave_word} measured",
        "run_word": "run",
        "run_word_plural": "runs",
        "wave_word": "wave",
        "wave_word_plural": "waves",
        "run_waves_title": "Waves",
        "run_tasks_title": "Tasks",
        "run_sources_title": "Sources",
        "run_back_medium": "← Fork Medium 80",
        "run_waves_note": "Per-wave outcomes for this configuration. A wave that was not measured is omitted (not a failure).",
        "run_tasks_note": "Per-task outcome of every recorded task run for this configuration.",
        "run_tasks_empty": "No per-task entries were recorded for this configuration.",
        "run_waves_empty": "No measured waves for this run.",
        "run_sources_note": "Run artifacts aggregated into this row (checkout <code>results/</code> first, then <code>../results/</code>).",
        "run_fine_print": "Static page generated by <code>scripts/build_fork_pages.py</code> from saved run JSON in <code>results/</code>. Times are wall-clock minutes; tokens are compact recorded totals; <strong>Tokens (cost)</strong> adds an estimated spend in roubles (₽) — an estimate, not a bill. An unmeasured wave is not a failure.",
    },
}


def t(locale: str, key: str, **fmt) -> str:
    """Translate a key for a locale; fall back to the default locale on a miss."""
    loc = locale if locale in CATALOG else DEFAULT_LOCALE
    value = CATALOG[loc].get(key)
    if value is None:
        value = CATALOG[DEFAULT_LOCALE].get(key, key)
    return value.format(**fmt) if fmt else value
