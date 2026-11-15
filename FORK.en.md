<!--
FORK NOTE (EN, 3 lines): This is the fork index of Alex-Kul777/harness-bench-fast
(fork of ai-forever/harness-bench-fast). It collects OUR benchmark runs
(Pi / OpenClaw / Hermes + wave 19). Upstream README stays authoritative for the
benchmark itself. Detailed data: docs/fork-results.en.md (EN) / docs/fork-results.md (RU).
Merge guard: keep this file on conflict against upstream.
-->

> **Fork index (EN).** Fork of [`ai-forever/harness-bench-fast`](https://github.com/ai-forever/harness-bench-fast)
> by [`Alex-Kul777`](https://github.com/Alex-Kul777). Adds Pi / OpenClaw / Hermes runs and
> wave-19 measurements on top of upstream. Detailed tables: [`docs/fork-results.en.md`](docs/fork-results.en.md).
> Upstream README remains the source of truth for the benchmark methodology and upstream rows.
> 🇷🇺 Russian-first index: [`FORK.md`](FORK.md).

# Fork `harness-bench-fast` — our results 🔵

This file is the **entry point of the fork**. It holds only our layer: Pi / OpenClaw /
Hermes runs and wave-19 measurements. Everything about the benchmark itself (task sets,
methodology, upstream rows) stays in the main [`README.md`](README.md) and is not duplicated here.

- 📊 **Detailed fork tables:** [`docs/fork-results.en.md`](docs/fork-results.en.md)
- 🧭 **Model/harness selection guidance:** [`docs/model-selection-guidance.en.html`](docs/model-selection-guidance.en.html) (RU: [`docs/model-selection-guidance.html`](docs/model-selection-guidance.html); source — [`docs/model-selection-guidance.en.md`](docs/model-selection-guidance.en.md)) — what × with what × when, from completed runs
- 🔬 **Live layout:** <https://ai-forever.github.io/harness-bench-fast/>
- 🧭 **Our fork results page:** <https://alex-kul777.github.io/harness-bench-fast/fork.en.html> (tabs Shot List / Light / **Medium (90)** / **Medium sample**; data: `docs/fork-home-results.json`, generator `scripts/build_fork_home.py`)
  - `#medium` — the full **90** tasks (historical 80 + wave21); our rows are marked `partial`.
  - `#medium-sample` — our truncated **sample**: coverage 66 of 90 + the GigaChat 3 Pro pilot (13 of 90); manifest `docs/fork-medium-pilot-manifest.json`.
- ⬆️ **Upstream:** <https://github.com/ai-forever/harness-bench-fast>
- 🧾 **Run artifacts:** `results/` (JSON reports), `runs/` (full runs), `docs/*.html` (HTML reports)

## The essentials in two lines

- **Shot List (52 tasks):** our best result is **OpenClaw + DeepSeek-V4.1-Flash (thinking: medium), 52/52 (100%)**; thinking: high — 51/52; thinking: off — 50/52; Pi + DeepSeek-V4.1-Flash and Pi + GLM-5.3 — 49/52 (94.2%).
- **Wave 19 (tasks 432–471, 40 tasks):** Pi + DeepSeek V4.1 Flash — **36/40 (90.0%)**, Wilson 76.9–96.0%.

## Wave 19 — Pi + DeepSeek V4.1 Flash

Wave-19 measurement (432–471, 40 tasks in 20 pairs) on the **Pi** runner with **DeepSeek V4.1 Flash**:
**36/40 (90.0%)**, 95% Wilson **76.9–96.0%**. One run (k=1), 94 min, 2 748 steps,
2 238 tool-calls, 9.57M input + 2.17M output (incl. 1.44M reasoning), **≈3 923 ₽**.
No infrastructure failures; a timeout counts as an ordinary fail. Raw run:
[`runs/pi_deepseek_v41flash_wave19_20261104.json`](runs/pi_deepseek_v41flash_wave19_20261104.json).

| Harness | Profile | Model | Result | % | Time | Steps | Tokens (cost) | Source |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| **Pi** | — | DeepSeek V4.1 Flash | **36/40** | **90.0%** | 174.8m | 2 748 | 48 463 320 (3 923₽) | this fork |
| Codex CLI 0.160.1 | reasoning: high | DeepSeek V4.1 Flash | 31/40 | 77.5% | — | — | — | upstream |
| Claude Code 2.1.300 | reasoning: high | DeepSeek V4.1 Flash | 30/40 | 75.0% | — | — | — | upstream |
| OpenClaw 2026.9.6 | reasoning: high | DeepSeek V4.1 Flash | 28/40 | 70.0% | — | — | — | upstream |
| DeepAgents (previous full run) | reasoning: high | DeepSeek V4.1 Flash | 27/40 | 67.5% | — | — | — | upstream |
| pi-mono 0.73.1 | reasoning: high | DeepSeek V4.1 Flash | 26/40 | 65.0% | — | — | — | upstream |

Upstream rows are the OpenRouter DeepSeek V4.1 Flash (reasoning high) columns from the
[native harness wave 19 comparison](docs/harness-wave19.html). Our run is a separate
Pi runner on the same frozen wave, so the comparison is not row-for-row.
The **+10/40 gap over upstream pi-mono (26/40)** is notable and **not yet independently
validated** (possible causes: runner settings, model version/config drift).
Marked provisional until reproduced.

## Shot List — our rows

The full shot-list table (52 tasks) lives in [`README.md`](README.md#shot-list-leaderboard-52-tasks)
together with upstream rows. Only our runs are shown here:

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

## Artifacts

| What | Where |
| --- | --- |
| Shot-list reports (ours) | `results/{pi,oc,hermes}_shotlist_*.json`, `docs/{oc,hermes}_shotlist_*.html` |
| Wave 19 runs | `results/wave19_full_ds41flash.json`, `runs/pi_deepseek_v41flash_wave19_20261104.json` |
| Full Light runs (391) | `results/{glm52,hermes,oc}_full_391.json`, `runs/*_full391_v016.json` |
| Fork docs | `docs/adr/0001-shot-list.md`, `GLOSSARY.md` |
| Agent skill | `.agents/skills/harness-bench-fast/SKILL.md` |

## How to maintain this

The fork tables are assembled from `results/*.json` by:

```bash
python3 scripts/build_fork_results.py            # rebuild docs/fork-results.md + docs/fork-results.en.md
python3 scripts/build_fork_results.py --check    # verify both files are current (CI-friendly)
```

**Merge guard.** Our layer in the main `README.md` is wrapped in the markers
`<!-- FORK:BEGIN --> … <!-- FORK:END -->`. After `git merge upstream/main` check that the
banner is intact and restore it from this file. The details and tables live in separate
files precisely so an upstream merge does not overwrite them.
