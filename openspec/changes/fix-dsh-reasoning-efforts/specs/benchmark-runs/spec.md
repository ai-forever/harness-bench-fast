## ADDED Requirements

### Requirement: Объявление reasoning-возможностей модели в dsh-патче

`run_dsh_bench.build_provider_patch` SHALL объявлять на записи модели ключ
`reasoningEfforts` со всеми валидными уровнями dsh (`off, minimal, low, medium, high,
xhigh, max`). Уровень `off` SHALL не иметь wire-значения, остальные SHALL отображаться
на собственное имя. Без `reasoningEfforts` pi-ai считает модель нереазонинговой и
отклоняет любой уровень кроме `off` с `UNSUPPORTED_REASONING_EFFORT`.

#### Scenario: Уровень выше off принимается

- **WHEN** `--thinking medium` (или иной уровень > off)
- **THEN** патч SHALL объявлять `reasoningEfforts` с этим уровнем, и модель SHALL
  считаться reasoning-capable (запрос SHALL NOT падать с `UNSUPPORTED_REASONING_EFFORT`)

#### Scenario: off остаётся рабочим

- **WHEN** уровень `off` (или `none`, или не задан)
- **THEN** патч SHALL объявлять `reasoningEfforts.off` без значения, а dispatch SHALL NOT
  слать reasoning-параметр

#### Scenario: ultra не объявляется

- **WHEN** патч сгенерирован
- **THEN** в `reasoningEfforts` SHALL NOT присутствовать `ultra` (не валидный уровень dsh)

### Requirement: Совместимость роли сообщений для Cloud.ru

dsh-патч SHALL задавать `compat.supportsDeveloperRole: false` на записи модели, потому
что Cloud.ru принимает только роли `user, tool, system, assistant` и отклоняет
OpenAI-роль `developer` (`400 INVALID_REQUEST`).

#### Scenario: Запрос не содержит developer-роли

- **WHEN** прогон идёт по патчу с `reasoningEfforts`
- **THEN** `compat.supportsDeveloperRole` SHALL быть `false`, и запрос SHALL NOT
  отклоняться Cloud.ru из-за роли `developer`
