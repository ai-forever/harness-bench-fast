# Tasks: add-billing-reconciliation

- [x] OpenSpec: proposal / design / tasks / spec-delta для `billing-reconciliation`
- [x] `scripts/reconcile_billing.py`: чтение 1+ CSV, дедуп, агрегация по
      `(department, api_key, model)`, фильтры `--key/--model/--since/--until`, `--json`
- [x] `tests/test_reconcile_billing.py`: дедуп перекрытия, агрегация, фильтры (фикстуры)
- [x] Разбор `kap-openclaw` (95 134 ₽ sonnet-4.6): разбивка по времени/модели,
      атрибуция бенч vs сессии → `docs/reconciliation-2026-11-15.md`
- [x] QA: `python3 -m pytest tests/test_reconcile_billing.py -q` green
- [x] QA: `npx -y @fission-ai/openspec@1.16.0 validate --all --strict` == 0
- [x] QA: прогон `reconcile_billing.py` на реальных экспортах даёт итог `kap-hermes`
      ≈ **5 782 ₽** (сверено вручную)
