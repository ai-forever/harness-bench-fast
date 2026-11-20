<!-- Fork results (EN): Detailed fork-scoped benchmark tables for Alex-Kul777/harness-bench-fast.
     English mirror of docs/fork-results.md. Auto-generated block is rebuilt by scripts/build_fork_results.py. -->

# Fork results · `harness-bench-fast`

Detailed tables for **our** fork layer. Overview and headline numbers live in [`../FORK.md`](../FORK.md) (RU) / [`../FORK.en.md`](../FORK.en.md) (EN).
Upstream methodology and upstream rows stay in [`../README.md`](../README.md).

> **RU:** Это зеркало русскоязычного файла [`fork-results.md`](fork-results.md). RU-версия — дефолт.

Our **Medium** sets live on the single fork page:
[`docs/fork.html#medium`](fork.html#medium) — the full **90** tasks (with a `partial` marker for
incomplete coverage) and [`docs/fork.html#medium-sample`](fork.html#medium-sample) — the truncated
**sample** (our 66 coverage + pilot 13; pilot: [`docs/fork-medium-pilot-manifest.json`](fork-medium-pilot-manifest.json)).

## Contents
- [Summary table (auto-sync)](#summary-table-auto-sync)
- [Shot List (52 tasks) — our rows](#shot-list-52-tasks--our-rows)
- [Wave 19 (432–471) — our run](#wave-19-432471--our-run)
- [Full Light runs (391)](#full-light-runs-391)
- [Artifacts and protocols](#artifacts-and-protocols)

---

## Summary table (auto-sync)

The block below is **generated** by `scripts/build_fork_results.py` from `results/*.json`.
Do not edit by hand — change the script/JSON and rebuild.

> ℹ️ The premium-ladder `Claude …` rows (Shot List, key `kap-hermes`) are **reconciled with the
> provider's actual billing**: their ₽ column is the billed amount, not a tariff estimate.

<!-- FORK:RESULTS:BEGIN -->
Auto-sync: 120 runs

| Harness | Suite | Profile | Model | Result | % | Time | Steps | Tokens (cost) |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| DeepSeek Harness | Shot List (52) | thinking: max | DeepSeek-V4.1-Flash (thinking: max) | 52/52 | 100.0% | 84.1m | 438 | 7 106 700 (217₽) |
| DeepSeek Harness | Shot List (52) | thinking: medium | DeepSeek-V4.1-Flash (thinking: medium) | 51/52 | 98.1% | 22.4m | 269 | 2 032 131 (45₽) |
| DeepSeek Harness | Shot List (52) | thinking: off | DeepSeek-V4.1-Flash (thinking: off) | 51/52 | 98.1% | 19.1m | 266 | 1 975 401 (43₽) |
| DeepSeek Harness | Shot List (52) | thinking: high | GLM-5.3-FlashX (thinking: high) | 44/52 | 84.6% | 14.3m | 205 | 1 216 084 (31₽) |
| DeepSeek Harness | Shot List (52) | thinking: low | GLM-5.3-FlashX (thinking: low) | 43/52 | 82.7% | 17.1m | 217 | 1 305 013 (32₽) |
| DeepSeek Harness | Shot List (52) | thinking: max | GLM-5.3-FlashX (thinking: max) | 51/52 | 98.1% | 23.2m | 281 | 2 070 362 (66₽) |
| DeepSeek Harness | Shot List (52) | thinking: max | GPT-6.1-Sol (thinking: max) | 48/52 | 92.3% | 106.6m | 447 | 2 525 980 (625₽) |
| DeepSeek Harness | Shot List (52) | thinking: medium | GPT-6.1-Sol (thinking: medium) | 44/52 | 84.6% | 121.4m | 360 | 1 801 431 (333₽) |
| DeepSeek Harness | Shot List (52) | thinking: off | GPT-6.1-Sol (thinking: off) | 49/52 | 94.2% | 44.6m | 333 | 1 669 883 (369₽) |
| DeepSeek Harness | Shot List (52) | thinking: max | Gemini-3.8-Flash (thinking: max) | 52/52 | 100.0% | 142.0m | 1 046 | 30 152 903 (1 051₽) |
| DeepSeek Harness | Shot List (52) | thinking: medium | Gemini-3.8-Flash (thinking: medium) | 52/52 | 100.0% | 119.5m | 764 | 15 087 535 (666₽) |
| DeepSeek Harness | Shot List (52) | thinking: off | Gemini-3.8-Flash (thinking: off) | 52/52 | 100.0% | 49.1m | 469 | 3 296 914 (220₽) |
| DeepSeek Harness | Shot List (52) | thinking: max | Kimi-K3 (thinking: max) | 52/52 | 100.0% | 43.4m | 251 | 1 668 265 (423₽) |
| DeepSeek Harness | Shot List (52) | thinking: medium | Kimi-K3 (thinking: medium) | 52/52 | 100.0% | 27.0m | 211 | 1 275 039 (287₽) |
| DeepSeek Harness | Shot List (52) | thinking: off | Kimi-K3 (thinking: off) | 50/52 | 96.2% | 39.4m | 235 | 1 555 801 (398₽) |
| DeepSeek Harness | Medium (392-471) | thinking: off | DeepSeek-V4.1-Flash (thinking: off) | 39/40 | 97.5% | 161.8m | 727 | 20 621 016 (494₽) |
| DeepSeek Harness | Medium (392-471) | thinking: off | DeepSeek-V4.1-Flash (thinking: off) | 16/20 | 80.0% | 95.8m | 379 | 8 316 142 (206₽) |
| DeepSeek Harness | Medium (392-471) | thinking: off | DeepSeek-V4.1-Flash (thinking: off) | 5/6 | 83.3% | 244.9m | 436 | 74 296 407 (979₽) |
| Hermes | Shot List (52) | thinking: off | DeepSeek-V4.1-Flash (thinking: off) | 49/52 | 94.2% | 22.0m | 221 | 4 030 565 (289₽) |
| Hermes | Shot List (52) | thinking: off | GLM-5.2 (thinking: off) | 46/52 | 88.5% | 68.5m | — | 6 209 159 (1 509₽) |
| Hermes | Shot List (52) | — | GPT-OSS-120B | 28/52 | 53.8% | 37.9m | 427 | 8 833 747 (145₽) |
| Hermes | Shot List (52) | — | Qwen3.6-35B-A3B | 36/52 | 69.2% | 38.6m | 301 | 6 961 237 (1 544₽) |
| Hermes | Light (391) | — | — | 387/391 | 99.0% | 404.6m | — | 31 770 687 (7 775₽) |
| OpenClaw | Shot List (52) | — | DeepSeek-V4.1-Flash | 50/52 | 96.2% | 47.3m | 237 | 2 814 749 (222₽) |
| OpenClaw | Shot List (52) | thinking: high | DeepSeek-V4.1-Flash (thinking: high) | 51/52 | 98.1% | 40.3m | 244 | 2 926 226 (54₽) |
| OpenClaw | Shot List (52) | thinking: medium | DeepSeek-V4.1-Flash (thinking: medium) | 52/52 | 100.0% | 40.6m | 224 | 2 935 013 (55₽) |
| OpenClaw | Shot List (52) | thinking: off | DeepSeek-V4.1-Flash (thinking: off) | 50/52 | 96.2% | 37.9m | 244 | 2 718 375 (50₽) |
| OpenClaw | Shot List (52) | thinking: off | GLM-5.2 (thinking: off) | 47/52 | 90.4% | 231.9m | 247 | 1 777 320 (1 302₽) |
| OpenClaw | Shot List (52) | — | GLM-5.3 | 44/52 | 84.6% | 46.1m | 148 | 1 955 767 (125₽) |
| OpenClaw | Shot List (52) | thinking: high | GLM-5.3-FlashX (thinking: high) | 49/52 | 94.2% | 44.3m | 244 | 2 655 706 (76₽) |
| OpenClaw | Shot List (52) | thinking: high | GPT-5.5 (thinking: high) | 50/52 | 96.2% | 37.0m | 334 | 3 117 683 (2 904₽) |
| OpenClaw | Shot List (52) | thinking: off | GPT-5.5 (thinking: off) | 51/52 | 98.1% | 30.5m | 267 | 2 756 135 (2 523₽) |
| OpenClaw | Shot List (52) | thinking: off | GPT-6.1-Sol (thinking: off) | 50/52 | 96.2% | 50.0m | 280 | 2 950 601 (978₽) |
| OpenClaw | Shot List (52) | — | GPT-OSS-120B | 17/52 | 32.7% | 205.2m | 120 | 1 261 870 (786₽) |
| OpenClaw | Shot List (52) | thinking: off | Gemini-3.8-Flash (thinking: off) | 52/52 | 100.0% | 54.8m | 472 | 9 000 184 (402₽) |
| OpenClaw | Shot List (52) | thinking: off | GigaChat 3.5-432B-A28B (thinking: off) | 25/52 | 48.1% | 48.1m | 429 | 6 585 042 (208₽) |
| OpenClaw | Shot List (52) | thinking: ultra | GigaChat 3.5-432B-A28B (thinking: ultra) | 20/52 | 38.5% | 49.5m | 473 | 8 819 332 (39₽) |
| OpenClaw | Shot List (52) | thinking: medium | Kimi-K3 (thinking: medium) | 52/52 | 100.0% | 45.6m | 219 | 2 340 020 (581₽) |
| OpenClaw | Shot List (52) | thinking: medium | MiniMax-M3 (thinking: medium) | 47/52 | 90.4% | 306.9m | 328 | 4 052 449 (1 119₽) |
| OpenClaw | Shot List (52) | thinking: off | MiniMax-M3 (thinking: off) | 48/52 | 92.3% | 295.4m | 312 | 3 650 102 (972₽) |
| OpenClaw | Shot List (52) | thinking: ultra | MiniMax-M3 (thinking: ultra) | 46/52 | 88.5% | 52.1m | 305 | 3 892 497 (1 068₽) |
| OpenClaw | Shot List (52) | — | Qwen3.6-35B-A3B | 40/52 | 76.9% | 195.2m | 328 | 2 109 433 (2 079₽) |
| OpenClaw | Light (391) | — | — | 378/391 | 96.7% | 1473.3m | — | 0 (0₽) |
| OpenClaw | Medium (392-471) | — | — | 0/6 | 0.0% | 40.3m | 326 | 22 645 277 (142₽) |
| OpenClaw | Medium (392-471) | — | — | 0/6 | 0.0% | 22.0m | 173 | 7 195 797 (31₽) |
| OpenClaw | Medium (392-471) | thinking: high | DeepSeek-V4.1-Flash (thinking: high) | 38/40 | 95.0% | 105.0m | 783 | 25 623 769 (509₽) |
| OpenClaw | Medium (392-471) | thinking: high | DeepSeek-V4.1-Flash (thinking: high) | 17/20 | 85.0% | 81.5m | 406 | 14 660 719 (276₽) |
| OpenClaw | Medium (392-471) | thinking: high | DeepSeek-V4.1-Flash (thinking: high) | 3/6 | 50.0% | 128.2m | 183 | 8 929 176 (169₽) |
| OpenClaw | Medium (392-471) | thinking: medium | DeepSeek-V4.1-Flash (thinking: medium) | 39/40 | 97.5% | 123.7m | 811 | 24 275 573 (493₽) |
| OpenClaw | Medium (392-471) | thinking: medium | DeepSeek-V4.1-Flash (thinking: medium) | 16/20 | 80.0% | 69.1m | 399 | 11 144 179 (242₽) |
| OpenClaw | Medium (392-471) | thinking: medium | DeepSeek-V4.1-Flash (thinking: medium) | 2/6 | 33.3% | 115.5m | 66 | 303 527 (13₽) |
| OpenClaw | Medium (392-471) | thinking: off | DeepSeek-V4.1-Flash (thinking: off) | 36/40 | 90.0% | 169.6m | 873 | 29 641 266 (573₽) |
| OpenClaw | Medium (392-471) | thinking: off | DeepSeek-V4.1-Flash (thinking: off) | 12/20 | 60.0% | 128.5m | 467 | 10 009 328 (216₽) |
| OpenClaw | Medium (392-471) | thinking: off | DeepSeek-V4.1-Flash (thinking: off) | 2/6 | 33.3% | 131.6m | 86 | 8 425 060 (189₽) |
| OpenClaw | Medium (392-471) | thinking: off | DeepSeek-V4.1-Flash (thinking: off) | 0/2 | 0.0% | 45.6m | 4 | 9 429 226 (202₽) |
| OpenClaw | Medium (392-471) | thinking: off | GPT-5.5 (thinking: off) | 38/40 | 95.0% | 61.9m | 473 | 10 786 591 (9 860₽) |
| OpenClaw | Medium (392-471) | thinking: off | GPT-5.5 (thinking: off) | 12/20 | 60.0% | 35.1m | 264 | 3 987 306 (3 846₽) |
| OpenClaw | Medium (392-471) | thinking: off | GPT-5.5 (thinking: off) | 1/6 | 16.7% | 43.2m | 209 | 14 469 503 (12 926₽) |
| OpenClaw | Medium (392-471) | thinking: off | GigaChat 3.5-432B-A28B (thinking: off) | 1/40 | 2.5% | 49.0m | 279 | 9 226 537 (380₽) |
| OpenClaw | Medium (392-471) | thinking: off | GigaChat 3.5-432B-A28B (thinking: off) | 0/6 | 0.0% | 40.3m | 326 | 22 645 277 (142₽) |
| OpenClaw | Medium (392-471) | thinking: off | GigaChat 3.5-432B-A28B (thinking: off) | 0/20 | 0.0% | 84.6m | 935 | 17 827 537 (217₽) |
| OpenClaw | Medium (392-471) | thinking: ultra | GigaChat 3.5-432B-A28B (thinking: ultra) | 0/40 | 0.0% | 62.6m | 369 | 15 557 817 (78₽) |
| OpenClaw | Medium (392-471) | thinking: ultra | GigaChat 3.5-432B-A28B (thinking: ultra) | 0/6 | 0.0% | 22.0m | 173 | 7 195 797 (31₽) |
| OpenClaw | Medium (392-471) | thinking: ultra | GigaChat 3.5-432B-A28B (thinking: ultra) | 0/20 | 0.0% | 20.1m | 259 | 4 749 821 (17₽) |
| OpenClaw | Medium (392-471) | thinking: medium | MiniMax-M3 (thinking: medium) | 27/40 | 67.5% | 385.8m | 1 329 | 50 707 607 (13 173₽) |
| OpenClaw | Medium (392-471) | thinking: medium | MiniMax-M3 (thinking: medium) | 11/20 | 55.0% | 109.4m | 623 | 10 614 066 (2 960₽) |
| OpenClaw | Medium (392-471) | thinking: medium | MiniMax-M3 (thinking: medium) | 0/6 | 0.0% | 154.9m | 284 | 14 488 379 (3 707₽) |
| OpenClaw | Medium (392-471) | thinking: off | MiniMax-M3 (thinking: off) | 34/40 | 85.0% | 266.4m | 989 | 33 114 679 (8 636₽) |
| OpenClaw | Medium (392-471) | thinking: off | MiniMax-M3 (thinking: off) | 15/20 | 75.0% | 162.0m | 698 | 12 178 668 (3 426₽) |
| OpenClaw | Medium (392-471) | thinking: off | MiniMax-M3 (thinking: off) | 0/6 | 0.0% | 363.2m | 276 | 7 360 375 (1 913₽) |
| Pi | Shot List (52) | — | Claude Fable 5 | 46/52 | 88.5% | 13.9m | 712 | 549 550 (1 263₽) |
| Pi | Shot List (52) | — | Claude Fable 5.1 | 47/52 | 90.4% | 19.6m | 752 | 644 155 (1 485₽) |
| Pi | Shot List (52) | — | Claude Opus 4.8 | 49/52 | 94.2% | 12.3m | 808 | 718 602 (764₽) |
| Pi | Shot List (52) | — | Claude Opus 5.5 | 48/52 | 92.3% | 13.3m | 720 | 595 335 (556₽) |
| Pi | Shot List (52) | — | Claude Sonnet 4.5 | 51/52 | 98.1% | 25.7m | 1 352 | 1 508 795 (1 060₽) |
| Pi | Shot List (52) | — | Claude Sonnet 4.6 | 50/52 | 96.2% | 14.6m | 1 060 | 2 657 016 (758₽) |
| Pi | Shot List (52) | — | Claude Sonnet 4.6 | 49/52 | 94.2% | 14.0m | 996 | 813 109 (653₽) |
| Pi | Shot List (52) | thinking: medium | Claude Sonnet 4.6 (thinking: medium) | 50/52 | 96.2% | 14.3m | 1 056 | 2 632 875 (749₽) |
| Pi | Shot List (52) | thinking: off | Claude Sonnet 4.6 (thinking: off) | 49/52 | 94.2% | 12.8m | 1 152 | 3 038 157 (819₽) |
| Pi | Shot List (52) | — | DeepSeek-V4.1-Flash | 49/52 | 94.2% | 21.3m | 330 | 1 438 179 (42₽) |
| Pi | Shot List (52) | thinking: off | GLM-5.2 (thinking: off) | 49/52 | 94.2% | 102.8m | 388 | 1 913 439 (377₽) |
| Pi | Shot List (52) | — | GLM-5.3 | 49/52 | 94.2% | 64.9m | 349 | 1 764 093 (221₽) |
| Pi | Shot List (52) | thinking: high | GLM-5.3-FlashX (thinking: high) | 46/52 | 88.5% | 12.2m | 262 | 540 292 (24₽) |
| Pi | Shot List (52) | thinking: low | GLM-5.3-FlashX (thinking: low) | 45/52 | 86.5% | 10.5m | 235 | 405 974 (16₽) |
| Pi | Shot List (52) | thinking: max | GLM-5.3-FlashX (thinking: max) | 49/52 | 94.2% | 11.8m | 258 | 517 470 (22₽) |
| Pi | Shot List (52) | thinking: high | GPT-5.5 (thinking: high) | 49/52 | 94.2% | 11.6m | 299 | 467 135 (569₽) |
| Pi | Shot List (52) | thinking: off | GPT-5.5 (thinking: off) | 48/52 | 92.3% | 9.0m | 300 | 433 896 (497₽) |
| Pi | Shot List (52) | thinking: max | GPT-6.1-Sol (thinking: max) | 51/52 | 98.1% | 26.8m | 293 | 589 808 (190₽) |
| Pi | Shot List (52) | thinking: medium | GPT-6.1-Sol (thinking: medium) | 51/52 | 98.1% | 20.7m | 243 | 437 490 (146₽) |
| Pi | Shot List (52) | thinking: off | GPT-6.1-Sol (thinking: off) | 51/52 | 98.1% | 21.2m | 248 | 460 633 (156₽) |
| Pi | Shot List (52) | thinking: max | Gemini-3.8-Flash (thinking: max) | 50/52 | 96.2% | 179.8m | 879 | 8 747 589 (578₽) |
| Pi | Shot List (52) | thinking: medium | Gemini-3.8-Flash (thinking: medium) | 50/52 | 96.2% | 149.9m | 644 | 4 067 191 (372₽) |
| Pi | Shot List (52) | thinking: off | Gemini-3.8-Flash (thinking: off) | 51/52 | 98.1% | 83.3m | 511 | 2 267 300 (206₽) |
| Pi | Shot List (52) | — | GigaChat 3 Pro | 16/52 | 30.8% | 25.7m | 2 034 | 12 883 705 (957₽) |
| Pi | Shot List (52) | thinking: high | GigaChat 3.5 Reasoning (thinking: high) | 34/52 | 65.4% | 33.8m | 1 160 | 3 796 473 (436₽) |
| Pi | Shot List (52) | thinking: low | GigaChat 3.5 Reasoning (thinking: low) | 35/52 | 67.3% | 15.8m | 1 116 | 3 041 748 (352₽) |
| Pi | Shot List (52) | thinking: medium | GigaChat 3.5 Reasoning (thinking: medium) | 34/52 | 65.4% | 19.2m | 1 176 | 2 974 629 (354₽) |
| Pi | Shot List (52) | thinking: max | Kimi-K3 (thinking: max) | 51/52 | 98.1% | 32.7m | 223 | 522 344 (213₽) |
| Pi | Shot List (52) | thinking: medium | Kimi-K3 (thinking: medium) | 51/52 | 98.1% | 38.0m | 225 | 551 804 (236₽) |
| Pi | Shot List (52) | thinking: off | Kimi-K3 (thinking: off) | 51/52 | 98.1% | 40.9m | 239 | 614 257 (259₽) |
| Pi | Shot List (52) | — | Qwen3.6-35B-A3B | 42/52 | 80.8% | 18.5m | 327 | 1 318 346 (299₽) |
| Pi | Medium (392-471) | thinking: medium | Claude Sonnet 4.6 (thinking: medium) | 21/40 | 52.5% | 474.7m | 2 770 | 84 810 504 (9 525₽) |
| Pi | Medium (392-471) | thinking: medium | Claude Sonnet 4.6 (thinking: medium) | 14/20 | 70.0% | 97.4m | 1 492 | 11 603 358 (1 293₽) |
| Pi | Medium (392-471) | thinking: medium | Claude Sonnet 4.6 (thinking: medium) | 0/6 | 0.0% | 103.2m | 1 004 | 63 667 285 (5 018₽) |
| Pi | Medium (392-471) | thinking: off | Claude Sonnet 4.6 (thinking: off) | 20/40 | 50.0% | 250.3m | 3 031 | 131 943 515 (32 800₽) |
| Pi | Medium (392-471) | thinking: off | Claude Sonnet 4.6 (thinking: off) | 15/20 | 75.0% | 33.4m | 1 836 | 16 340 118 (4 122₽) |
| Pi | Medium (392-471) | thinking: off | Claude Sonnet 4.6 (thinking: off) | 1/6 | 16.7% | 119.4m | 2 718 | 228 309 339 (55 281₽) |
| Pi | Medium (392-471) | — | DeepSeek-V4.1-Flash | 11/15 | 73.3% | 137.3m | 1 124 | 15 124 377 (360₽) |
| Pi | Medium (392-471) | — | DeepSeek-V4.1-Flash | 5/5 | 100.0% | 32.0m | 336 | 2 949 354 (86₽) |
| Pi | Medium (392-471) | — | DeepSeek-V4.1-Flash | 0/3 | 0.0% | 288.7m | 1 650 | 78 233 678 (1 201₽) |
| Pi | Medium (392-471) | thinking: off | GPT-5.5 (thinking: off) | 38/40 | 95.0% | 125.9m | 712 | 11 850 369 (11 011₽) |
| Pi | Medium (392-471) | thinking: off | GPT-5.5 (thinking: off) | 14/20 | 70.0% | 25.9m | 327 | 1 544 688 (1 729₽) |
| Pi | Medium (392-471) | thinking: off | GPT-5.5 (thinking: off) | 1/6 | 16.7% | 47.6m | 426 | 17 986 241 (16 103₽) |
| Pi | Medium (392-471) | — | GigaChat 3 Pro | 0/40 | 0.0% | 15.0m | 1 408 | 27 408 162 (2 009₽) |
| Pi | Medium (392-471) | — | GigaChat 3 Pro | 0/6 | 0.0% | 23.5m | 1 020 | 22 942 929 (1 695₽) |
| Pi | Medium (392-471) | — | GigaChat 3 Pro | 0/20 | 0.0% | 31.6m | 2 948 | 25 638 927 (1 894₽) |
| Pi | Medium (392-471) | thinking: low | GigaChat 3.5 Reasoning (thinking: low) | 11/40 | 27.5% | 517.1m | 2 973 | 94 222 758 (9 881₽) |
| Pi | Medium (392-471) | thinking: low | GigaChat 3.5 Reasoning (thinking: low) | 2/20 | 10.0% | 210.9m | 2 176 | 31 410 934 (3 264₽) |
| Pi | Medium (392-471) | thinking: low | GigaChat 3.5 Reasoning (thinking: low) | 0/6 | 0.0% | 62.2m | 1 419 | 65 534 148 (6 470₽) |
| — | Wave 19 (40) | — | DeepSeek-V4.1-Flash | 36/40 | 90.0% | 174.8m | 2 748 | 48 463 320 (1 284₽) |
<!-- FORK:RESULTS:END -->

---

## Shot List (52 tasks) — our rows

A stratified sample of 52 tasks (~13% of 391). The full table (with upstream) lives in
[`README.md`](../README.md#shot-list-leaderboard-52-tasks). Only our runs are shown here.
`Time` is wall-clock minutes; `Cost` is an estimated spend in roubles.

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
| Pi | — (2026-11-07) | GigaChat 3 Pro | 16/52 | 30.8% | 25.7m | 2034 | 12 883 705 (230₽) |

## Wave 19 (432–471) — our run

Wave 19 is 40 tasks in 20 pairs (feasibility). Our measurement is Pi + DeepSeek V4.1 Flash.

| Harness | Profile | Model | Result | % | Time | Steps | Tokens (cost) | Source |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| **Pi** | — | DeepSeek V4.1 Flash | **36/40** | **90.0%** | 174.8m | 2 748 | 48 463 320 (3 923₽) | `runs/pi_deepseek_v41flash_wave19_20261104.json` |
| Codex CLI 0.160.1 | reasoning: high | DeepSeek V4.1 Flash | 31/40 | 77.5% | — | — | — | upstream |
| Claude Code 2.1.300 | reasoning: high | DeepSeek V4.1 Flash | 30/40 | 75.0% | — | — | — | upstream |
| OpenClaw 2026.9.6 | reasoning: high | DeepSeek V4.1 Flash | 28/40 | 70.0% | — | — | — | upstream |
| DeepAgents (previous full run) | reasoning: high | DeepSeek V4.1 Flash | 27/40 | 67.5% | — | — | — | upstream |
| pi-mono 0.73.1 | reasoning: high | DeepSeek V4.1 Flash | 26/40 | 65.0% | — | — | — | upstream |

Run details: k=1, 94 min, 2 748 steps, 2 238 tool-calls,
9.57M input + 2.17M output (incl. 1.44M reasoning), ≈3 923 ₽; no infrastructure failures.
The gap of **+10/40 over upstream pi-mono (26/40)** is provisional, not independently validated.

A small smoke/sample for the wave: `results/sample_wave19.json` (5/5), `results/smoke_wave_reflect.json` (1/1).

## Full Light runs (391)

Our full Light runs (tasks 1–391). Values come from our JSON; this is **not** a recomputation of
the upstream table.

| Harness | Profile | Model | Result | % | Time | Steps | Tokens (cost) | JSON |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| Hermes | MCP off | GLM-5.2 | 387/391 | 99.0% | 404.6m | — | 31 770 687 (7 775₽) | `results/hermes_full_391.json` |
| Pi | high | GLM-5.2 | 385/391 | 98.5% | — | — | — | `runs/pi_glm52_high_full391_v016.json` |
| OpenClaw | — | GLM-5.2 | 378/391 | 96.7% | 1473.3m | — | 0 (0₽) | `results/oc_full_391.json` |
| Hermes | MCP off | GLM-5.2 | — | — | — | — | — | `runs/hermes_glm52_mcpoff_full391_v016.json` |
| OpenClaw | — | GLM-5.2 | — | — | — | — | — | `runs/openclaw_glm52_full391_v016.json` |

> A dash means the run is kept as a raw `runs/*_v016.json`; the current summary is taken from `results/`.

## Artifacts and protocols

| What | Where |
| --- | --- |
| Shot-list JSON (ours) | `results/{pi,oc,hermes}_shotlist_*.json` |
| Shot-list HTML | `docs/{oc,hermes}_shotlist_*.html`, `docs/oc_shotlist_thinking_off.html` |
| Wave 19 | `results/wave19_full_ds41flash.json`, `results/sample_wave19.json` |
| Wave 19 diagnostics | `results/diag_wave19.json`, `results/diag_wave19_fixc.json` |
| Light (391) JSON | `results/{glm52,hermes,oc}_full_391.json`, `runs/*_full391_v016.json` |
| Fork docs | `docs/adr/0001-shot-list.md`, `GLOSSARY.md` |
| Skill | `.agents/skills/harness-bench-fast/SKILL.md` |

**Merge guard.** On an upstream conflict these files are "keep ours". The main `README.md`
holds only a short banner between `<!-- FORK:BEGIN -->` and `<!-- FORK:END -->`.
Rebuild script: `python3 scripts/build_fork_results.py`.
