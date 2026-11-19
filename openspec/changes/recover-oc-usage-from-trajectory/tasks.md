# Tasks: recover-oc-usage-from-trajectory

## Реализация
- [ ] `run_one_task`: всегда `--state-dir` (temp per-run; `/tmp/oc_state_<task>` при `--keep`)
- [ ] helper `_trajectory_usage(state_dir, session_id)` (max `total`, устойчивый)
- [ ] приоритет конверта; fallback на траекторию; поле `agent_usage_source`
- [ ] `agent_cost_usd` из траектории, если конверт не дал

## QA
- [ ] RED→GREEN: тесты восстановления на синтетической sqlite
- [ ] `python3 -m pytest tests/test_oc_bench_usage_recovery.py -q` → green
- [ ] `openspec validate recover-oc-usage-from-trajectory --type change --strict` → valid
- [ ] `openspec validate --all --strict` → 0 failed
- [ ] Живой смоук: прогон с `usageReport.state=unavailable` → токены восстановлены, source=trajectory

## Архив
- [ ] `openspec archive recover-oc-usage-from-trajectory` после зелёного QA
