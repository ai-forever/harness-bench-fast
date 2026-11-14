#!/usr/bin/env bash
# Catch-up run for ai-sage/GigaChat3.5-432B-A28B in REASONING mode (--thinking ultra).
# The separate "...-Reasoning" model id was retired from the Cloud.ru catalog (404 model_not_found),
# so reasoning is now exercised as a MODE on the base model id.
# Fills the missing "reasoning (ultra)" cells for both tests: Shot-list 52 and Medium-66.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
RUNNER="$REPO_ROOT/run_openclaw_bench.py"; RESULTS="$REPO_ROOT/results"
DATE="${DATE:-20261114}"; CONC="${CONC:-1}"; THINK="${THINK:-ultra}"
export NODE_OPTIONS="--require /home/kap/.openclaw/workspace/projects/benchmark-research/preload_maxlisteners.js"
MODEL="custom-foundation-models-api-cloud-ru/ai-sage/GigaChat3.5-432B-A28B"
LONG="task_392_manuscript_continuity task_398_card_game_engine task_403_binary_format_reverse task_393_intranet_multihop task_394_review_annotation task_395_homework_grading"
REFLECT="task_413_crm_pagination task_414_batch_partial task_415_api_throttle task_412_supplier_minimum task_416_pay_units task_418_ticket_timeout_dupes task_419_storage_async_delete task_417_transfer_fees task_420_wiki_optimistic_lock task_421_catalog_case_search task_423_rooms_duration_cap task_424_payments_split_limits task_425_shipping_weight_codes task_422_calendar_timezone task_427_report_deprecated_flag task_426_kv_eventual task_429_mail_bounce task_430_orders_backorder task_428_geocode_quota task_431_admin_elevation"
FEAS="task_432_clock_contract task_434_universal_compressor task_435_archive_transfer task_436_search_capabilities task_433_aggregate_identifiability task_437_search_index task_439_ledger_reachability task_440_universal_compressor task_438_opaque_guid task_441_stateless_contract task_443_search_count task_444_stateless_contract task_445_parity_repair task_446_archive_transfer task_447_sealed_partition task_442_ledger_reachability task_449_search_capabilities task_448_aggregate_identifiability task_451_search_index task_450_maintenance_windows task_452_search_hint task_454_search_boundary task_455_maintenance_windows task_453_opaque_guid task_457_search_pool task_458_deployment_matching task_459_replication_cut task_460_replication_cut task_461_search_pool task_462_build_critical_path task_463_diagnostic_queries task_464_search_boundary task_465_search_count task_466_parity_repair task_467_sealed_partition task_468_diagnostic_queries task_469_deployment_matching task_470_search_hint task_471_clock_contract task_456_build_critical_path"
run(){
  local tag="$1"; shift
  local out="$RESULTS/oc_${tag}_${DATE}.json"
  echo ">>> [catchup $tag] model=$MODEL thinking=$THINK timeout=${1}s conc=$CONC -> $out"
  local tmo="$1"; shift
  PYTHONUNBUFFERED=1 python3 -u "$RUNNER" "$@" --model "$MODEL" --thinking "$THINK" \
    --timeout "$tmo" --concurrency "$CONC" --json-output "$out" \
    --status-file "/tmp/oc_${tag}_status.json" 2>&1 | tee "/tmp/oc_${tag}.log"
}
echo "### CATCHUP start $(date '+%F %T') branch=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD) conc=$CONC think=$THINK"
run sl-ultra       1800 --shot-list
run m66-ultra-long 7200 --tasks $LONG
run m66-ultra-refl 1800 --tasks $REFLECT
run m66-ultra-feas 3600 --tasks $FEAS
echo "### CATCHUP done $(date '+%F %T')"
