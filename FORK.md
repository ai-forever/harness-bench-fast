<!--
FORK NOTE (EN, 3 lines): This is the fork index of Alex-Kul777/harness-bench-fast
(fork of ai-forever/harness-bench-fast). It collects OUR benchmark runs
(Pi / OpenClaw / Hermes + wave 19). Upstream README stays authoritative for the
benchmark itself. Detailed data: docs/fork-results.md. Merge guard: keep this file
on conflict against upstream.
-->

> **Fork index (EN).** Fork of [`ai-forever/harness-bench-fast`](https://github.com/ai-forever/harness-bench-fast)
> by [`Alex-Kul777`](https://github.com/Alex-Kul777). Adds Pi / OpenClaw / Hermes runs and
> wave-19 measurements on top of upstream. Detailed tables: [`docs/fork-results.md`](docs/fork-results.md).
> Upstream README remains the source of truth for the benchmark methodology and upstream rows.

# Форк `harness-bench-fast` — наши результаты 🔵

> 🇬🇧 English mirror: [`FORK.en.md`](FORK.en.md).

Этот файл — **входная точка форка**. Здесь только наш слой: прогоны Pi / OpenClaw /
Hermes и замеры wave 19. Всё, что относится к самому бенчмарку (тасксеты, методика,
upstream-строки) — остаётся в основном [`README.md`](README.md) и не дублируется здесь.

- 📊 **Детальные таблицы форка:** [`docs/fork-results.md`](docs/fork-results.md)
- 🧭 **Руководство по выбору модели/харнесса:** [`docs/model-selection-guidance.html`](docs/model-selection-guidance.html) (EN: [`docs/model-selection-guidance.en.html`](docs/model-selection-guidance.en.html); источник — [`docs/model-selection-guidance.md`](docs/model-selection-guidance.md)) — что × чем × когда, по уже выполненным прогонам
- 🔬 **Live-лейаут:** <https://ai-forever.github.io/harness-bench-fast/>
- 🧭 **Наша форк-страница результатов:** <https://alex-kul777.github.io/harness-bench-fast/fork.html> (табы Shot List / Light / **Medium (90)** / **Medium sample**; данные: `docs/fork-home-results.json`, генератор `scripts/build_fork_home.py`)
  - `#medium` — полные **90** задач (historical 80 + wave21); наши строки помечены `partial`.
  - `#medium-sample` — наш урезанный **семпл**: охват 66 из 90 + пилот GigaChat 3 Pro (13 из 90); манифест `docs/fork-medium-pilot-manifest.json`.
- ⬆️ **Upstream:** <https://github.com/ai-forever/harness-bench-fast>
- 🧾 **Артефакты прогонов:** `results/` (JSON-отчёты), `runs/` (полные прогоны), `docs/*.html` (HTML-отчёты)

## Главное в двух строках

- **Shot List (52 задачи):** лучший наш результат — **OpenClaw + DeepSeek-V4.1-Flash (thinking: medium), 52/52 (100%)**; thinking: high — 51/52; thinking: off — 50/52; Pi + DeepSeek-V4.1-Flash и Pi + GLM-5.3 — 49/52 (94.2%).
- **Wave 19 (задачи 432–471, 40 задач):** Pi + DeepSeek V4.1 Flash — **36/40 (90.0%)**, Wilson 76.9–96.0%.

## Wave 19 — Pi + DeepSeek V4.1 Flash

Замер wave 19 (432–471, 40 задач в 20 парах) на раннере **Pi** с **DeepSeek V4.1 Flash**:
**36/40 (90.0%)**, 95% Wilson **76.9–96.0%**. Один прогон (k=1), 94 мин, 2 748 шагов,
2 238 tool-calls, 9.57M вход + 2.17M выход (вкл. 1.44M reasoning), **≈3 923 ₽**.
Инфраструктурных сбоев нет; таймаут = обычный fail. Сырой прогон:
[`runs/pi_deepseek_v41flash_wave19_20261104.json`](runs/pi_deepseek_v41flash_wave19_20261104.json).

| Harness | Profile | Model | Result | % | Time | Steps | Tokens (cost) | Источник |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| **Pi** | — | DeepSeek V4.1 Flash | **36/40** | **90.0%** | 174.8m | 2 748 | 48 463 320 (3 923₽) | этот форк |
| Codex CLI 0.160.1 | reasoning: high | DeepSeek V4.1 Flash | 31/40 | 77.5% | — | — | — | upstream |
| Claude Code 2.1.300 | reasoning: high | DeepSeek V4.1 Flash | 30/40 | 75.0% | — | — | — | upstream |
| OpenClaw 2026.9.6 | reasoning: high | DeepSeek V4.1 Flash | 28/40 | 70.0% | — | — | — | upstream |
| DeepAgents (previous full run) | reasoning: high | DeepSeek V4.1 Flash | 27/40 | 67.5% | — | — | — | upstream |
| pi-mono 0.73.1 | reasoning: high | DeepSeek V4.1 Flash | 26/40 | 65.0% | — | — | — | upstream |

Upstream-строки — колонки OpenRouter DeepSeek V4.1 Flash (reasoning high) из
[native harness wave 19 comparison](docs/harness-wave19.html). Наш прогон — отдельный
Pi-раннер на той же замороженной волне, поэтому сравнение не «строка-в-строку».
Разрыв **+10/40 над upstream pi-mono (26/40)** заметен и **пока не валидирован
независимо** (возможные причины: настройки раннера, дрейф версии/конфига модели).
Помечено как provisional до воспроизведения.

## Шот-лист — наши строки

Полная таблица шот-листа (52 задачи) живёт в [`README.md`](README.md#shot-list-leaderboard-52-tasks)
вместе с upstream-строками. Здесь — только наши прогоны:

| Harness | Profile | Model | Result | % | Time | Steps | Tokens (cost) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| OpenClaw | thinking: medium (2026-11-07) | DeepSeek-V4.1-Flash | 52/52 | 100.0% | 40.6m | — | 2 935 013 (148₽) |
| OpenClaw | thinking: high (2026-11-07) | DeepSeek-V4.1-Flash | 51/52 | 98.1% | 40.3m | — | 2 926 226 (141₽) |
| OpenClaw | thinking: off (2026-11-07) | DeepSeek-V4.1-Flash | 50/52 | 96.2% | 37.9m | — | 2 718 375 (133₽) |
| OpenClaw | thinking: off (2026-11-04) | DeepSeek-V4.1-Flash | 50/52 | 96.2% | 47.3m | 237 | 2 814 749 (165₽) |
| Pi | — (2026-11-04) | DeepSeek-V4.1-Flash | 49/52 | 94.2% | 21.4m | 330 | 1 438 179 (~150₽) |
| Pi | — (2026-11-04) | GLM-5.3 | 49/52 | 94.2% | 64.9m | 349 | 1 764 093 (~220₽) |
| Pi | thinking: off (2026-08-17) | GLM-5.2 | 49/52 | 94.2% | 102.8m | 388 | 1 913 439 (501₽) |
| Hermes | MCP off (2026-08-10) | GLM-5.2 | 48/52 | 92.3% | 93.1m | 953 | 11 442 855 |
| OpenClaw | thinking: off (2026-08-12) | GLM-5.2 | 47/52 | 90.4% | 231.9m | 247 | 1 777 320 (1 793₽) |
| OpenClaw | thinking: high (2026-08-10) | GLM-5.2 | 46/52 | 88.5% | 226.4m | — | — |
| Hermes | MCP off + thinking: off (2026-08-13) | GLM-5.2 | 46/52 | 88.5% | 68.5m | 270 | 6 209 159 (1 509₽) |
| OpenClaw | thinking: off (2026-11-04) | GLM-5.3 | 44/52 | 84.6% | 46.1m | — | 1 955 767 (~50₽+) |
| Pi | — (2026-08-17) | Qwen3.6-35B-A3B | 42/52 | 80.8% | 18.5m | 327 | 1 318 346 (362₽) |
| OpenClaw | — (2026-08-17) | Qwen3.6-35B-A3B | 40/52 | 76.9% | 195.2m | 328 | 11 672 005 (2 859₽) |
| Hermes | — (2026-08-17) | Qwen3.6-35B-A3B | 36/52 | 69.2% | 38.6m | 301 | 6 961 237 (1 737₽) |
| Hermes | — (2026-08-13) | GPT-OSS-120B | 28/52 | 53.8% | 37.9m | 427 | 8 833 747 (2 171₽) |
| OpenClaw | thinking: off (2026-08-13) | GPT-OSS-120B | 17/52 | 32.7% | 205.2m | 120 | 1 261 870 (1 080₽) |
| Pi | — (2026-11-07) | GigaChat 3 Pro | 16/52 | 30.8% | 25.7m | 2 034 | 12 883 705 (230₽) |

## Артефакты

| Что | Где |
| --- | --- |
| Shot-list отчёты (наши) | `results/{pi,oc,hermes}_shotlist_*.json`, `docs/{oc,hermes}_shotlist_*.html` |
| Wave 19 прогоны | `results/wave19_full_ds41flash.json`, `runs/pi_deepseek_v41flash_wave19_20261104.json` |
| Полные Light-прогоны (391) | `results/{glm52,hermes,oc}_full_391.json`, `runs/*_full391_v016.json` |
| Доки форка | `docs/adr/0001-shot-list.md`, `GLOSSARY.md` |
| Скилл агента | `.agents/skills/harness-bench-fast/SKILL.md` |

## Как это поддерживать

Таблицы форка собираются из `results/*.json` скриптом:

```bash
python3 scripts/build_fork_results.py            # пересобрать docs/fork-results.md
python3 scripts/build_fork_results.py --check    # только проверить, что файл актуален (CI-friendly)
```

**Merge-guard.** Наш слой в основном `README.md` обёрнут маркерами
`<!-- FORK:BEGIN --> … <!-- FORK:END -->`. После `git merge upstream/main` проверь,
что баннер на месте, и восстанови его из этого файла. Детали и таблицы живут в
отдельных файлах именно для того, чтобы мерж upstream их не затирал.
