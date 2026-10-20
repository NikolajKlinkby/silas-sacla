#!/bin/bash
# All local checks, then copy the code to the HPC (laptop, bash).
#
#   bash scripts/check_and_sync.sh             # checks; if all pass: sync to the HPC (needs the VPN)
#   bash scripts/check_and_sync.sh --no-sync   # checks only
#   bash scripts/check_and_sync.sh --no-test   # skip tests; run sacla_check and sync if it passes
#   bash scripts/check_and_sync.sh --user <name>   # sync without the ssh alias (as sync_to_hpc.sh --user)
#
# Checks: sacla_check (mypy against the SACLA stubs) on all code, then the
# tests that run without SACLA (fake dbpy / test data). Nothing is synced if one fails.
# Paste the output to Claude if something fails.

set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
if [ -z "${SACLA_LOCAL:-}" ]; then
  ENVSH="sacla_local_api/sacla_env.sh"
  # shellcheck disable=SC1090
  source "$ENVSH" >/dev/null || { echo "cannot activate $ENVSH (docs: Development notes, local setup)"; exit 1; }
fi
PY="$SACLA_LOCAL/venv/bin/python"
fail=0
run_tests=1
do_sync=1

SYNC_OPTS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --no-test) run_tests=0 ;;
    --no-sync) do_sync=0 ;;
    --user|--host) SYNC_OPTS+=("$1" "$2"); shift ;;
    *)
      echo "unknown option: $1"
      echo "usage: bash scripts/check_and_sync.sh [--no-test] [--no-sync] [--user NAME | --host ALIAS]"
      exit 2
      ;;
  esac
  shift
done

echo "=== sacla_check silas start_session.py scripts tests"
bash "$SACLA_LOCAL/scripts/sacla_check.sh" silas start_session.py scripts tests || fail=1

if [ "$run_tests" -eq 1 ]; then
  echo "=== tests"
  for t in tests/test_mpccd_pipeline.py tests/test_roi.py tests/test_scan_view.py tests/test_scan_analysis.py tests/test_runlog_fake.py tests/test_scans_fake.py; do
    log=$(mktemp)
    if "$PY" "$t" >"$log" 2>&1; then
      echo "  OK    $t"
    else
      echo "  FAIL  $t"; tail -n 30 "$log" | sed 's/^/        /'; fail=1
    fi
    rm -f "$log"
  done
else
  echo "=== tests skipped (--no-test)"
fi

if [ $fail -ne 0 ]; then echo "=== problems found: NOT synced"; exit 1; fi
echo "=== all checks passed"
[ "$do_sync" -eq 0 ] && exit 0
echo "=== sync to the HPC"
bash scripts/sync_to_hpc.sh --go ${SYNC_OPTS[@]+"${SYNC_OPTS[@]}"}
