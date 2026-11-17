# isolate-bench-openclaw-instance — design

## Контекст

`openclaw agent exec` — embedded-ход без Gateway (`docs/cli/agent.md`). По умолчанию он
создаёт **временный** state-dir (не трогает боевой), но **читает ambient-конфиг**
оператора и резолвит плагины из операторских roots. Значит изоляция достигается
**пиннингом конфига** (`--config`), а не отдельной установкой.

## Решения

- **Q1 — единица изоляции.** Не отдельная npm-установка, а **pinned config**
  (`bench/openclaw.bench.json`) + env-ключ. `--config` «runs against exactly that config
  file … a missing or invalid file fails the run rather than falling back to defaults».
  Это и есть «чистый инстанс» для `agent exec`: только провайдер+модели, ноль
  плагинов/MCP/скиллов.
- **Q2 — ключ.** В чистом state-dir нет боевого secret store, поэтому `apiKey` в
  bench-конфиге — SecretRef `{source:"env", provider:"default", id:"CLOUD_RU_BENCH_API_KEY"}`
  (значение подставляет владелец; в репо не хранится). Это согласуется с
  `use-dedicated-bench-account`.
- **Q3 — как раннер задаёт конфиг.** Предпочтительно **`--config <path>`** в argv
  `agent exec` (явно, видно в артефакте), а не env `OPENCLAW_CONFIG_PATH` (менее явно и
  может цепляться к другим openclaw-вызовам). Оба проверены вживую (env — как fallback).
- **Q4 — state.** Оставляем дефолтный временный state-dir exec: он уже изолирован от
  боевого (`--state-dir` требует эксклюзивного владения и не нужен нам).
- **Q5 — что НЕ изолируется.** `--cwd`-tool профиль и exec-дефолты остаются как есть;
  поведение задачи (таймауты, верификатор) не меняем.
- **Q6 — совместимость.** Если файл bench-конфига отсутствует/невалиден — прогон SHALL
  падать явно, а SHALL NOT тихо откатываться на ambient-конфиг (по семантике `--config`).

## Альтернативы

- **Отдельная инсталляция OpenClaw** (`npm i -g` в отдельный prefix) — отвергнуто:
  избыточно; `agent exec` не требует Gateway, а изоляция конфига достаточна.
- **`--isolated`** (ignore ambient config entirely) — отвергнуто: конфиг нужен, чтобы
  резолвился провайдер Cloud.ru; `--isolated` + `--auth-env-only` не даёт провайдер-рouting.
- **`--profile bench` / `OPENCLAW_STATE_DIR`** — возможен как дополнение, но задача —
  конфиг, а не state; оставлено как опция на будущее.

## Открытые вопросы (для ревью)

- Нужно ли дополнять **детекцией** краха (маркер `harness_error`) на случай, если краш
  всё же случится, или ограничиться изоляцией? (Отдельный change при согласии.)
- Хранить ли в bench-конфиге **все 24 модели** провайдера или только бенчируемые?
  Предложение: все (метаданные безвредны, future-proof).

## Дополнение (после теста-2): два рычага «починить вызов»
Тест-2 показал: pruning/compaction помогают (ход 4× длиннее, usage фиксируется), но
`incomplete_turn` остаётся. Корень — на границе провайдер/модель (битый tool-call), потому:
- **Q7 — лимит вывода.** В bench-конфиге `maxTokens` поднят 16384 → **32768**: меньше шансов,
  что крупный tool-call обрежется по лимиту вывода и станет `malformed`.
- **Q8 — ретрай.** Раннер ретраит задачу при `error.kind == "incomplete_turn"`
  (`--retries`, default 1). Классификация — `_envelope_error_kind` (envelope-first,
  fallback по stderr-тексту). Обычный провал верификатора НЕ ретраится (это провал модели,
  а не инфра-крах).
- **Q9 — что НЕ делаем.** Не трогаем сам `openclaw agent exec` (продукт). Listener-warning
  (`MaxListeners`) — teardown-шум, `NODE_OPTIONS`-хак не нужен.
