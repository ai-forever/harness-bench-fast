# Tasks: reconcile-premium-ladder-cost

- [x] OpenSpec: proposal / design / tasks / spec-delta для `result-reporting`
- [x] `scripts/reconcile_ladder_billing.py`: чтение CSV-экспорт(ов), дедуп по
      `(date, key, model, in, out, cost)`, агрегация per-model для `--key kap-hermes`,
      сайдкар `results/reconciled/<run>.json`, diff «published vs provider», `--check`
- [x] Пересчитать 6 моделей лестницы (sonnet-4.5/4.6, opus-5.5/4.8, fable-5.1/5)
      из дедуплицированного экспорта; pass-рейтинги не трогать
- [x] Перепубликовать `docs/fork-results.md` + `.en.md` (regenerate pages) с
      фактическими tokens/₽
- [x] QA: `python3 scripts/build_fork_results.py --check` и `--lint` возвращают 0
- [x] QA: `npx -y @fission-ai/openspec@1.16.0 validate --all --strict` == 0
- [x] QA: строка таблицы tokens/₽ совпадает с reconcile-сайдкаром (diff ≤ 2%)
