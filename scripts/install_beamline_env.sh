#!/bin/bash
# One-time setup of a beamline analysis computer (xu-bl3-anapc01/02, DAQ-USER-LAN) for SILAS.
# Run ON THAT COMPUTER, after the code is there (scripts/fetch_from_hpc.sh):
#
#   bash ~/SILAS/scripts/install_beamline_env.sh        # dry run: report + what would be done
#   bash ~/SILAS/scripts/install_beamline_env.sh --go   # do it (safe to repeat)
#
# 0. writes a report of what the computer has (scripts/beamline_probe.sh,
#    output/beamline_probe_<host>.txt): keep it, it tells what to fix if something below fails
# 1. adds the SILAS shortcuts (scripts/beamline_env.sh) to ~/.bashrc, on the xu-* computers
#    only: bl, bl_env, bl_check, bl_probe, bl_update
# 2. installs IPython (the session prompt) and tornado into ~/SILAS_py37 from the wheels in
#    third_party/py37/ (scripts/install_hpc_packages.sh with the online Python module), unless the
#    SACLA Python has them already
# 3. checks the environment with the beamline profile (envcheck)
#
# Nothing in the SACLA installation changes. Undo: delete the marked line in ~/.bashrc (a backup
# ~/.bashrc.before_silas is made) and ~/SILAS_py37.

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
ENVFILE="$HERE/beamline_env.sh"
RC="$HOME/.bashrc"
MARK="# SILAS beamline shortcuts"
LINE="case \"\$(hostname)\" in xu-*) [ -f \"$ENVFILE\" ] && source \"$ENVFILE\" ;; esac  $MARK"
export SILAS_PY_MODULE="${SILAS_PY_MODULE:-python/SACLA_python-3.7/online}"

GO=0
[ "${1:-}" = "--go" ] && GO=1

case "$(hostname)" in
  xu-*) ;;
  *) echo "NOTE: $(hostname) is not a beamline computer (xu-*): the .bashrc line will do nothing here" ;;
esac

echo "=== 0. what this computer has (report)"
bash "$HERE/beamline_probe.sh" >/dev/null 2>&1
REPORT="$ROOT/output/beamline_probe_$(hostname -s 2>/dev/null || hostname).txt"
if [ -f "$REPORT" ]; then
  echo "report: $REPORT"
  grep -E "^  (OK: python|SACLA Intel Python|module load failed)|^module command|NOTE|^ssh to|^internet" "$REPORT" | sed 's/^ */  /'
else
  echo "no report written (see: bash $HERE/beamline_probe.sh)"
fi

# shortcuts and Python, for the steps below (also in this script's shell)
# shellcheck source=/dev/null
source "$ENVFILE" >/dev/null 2>&1
bl_env || { echo "STOP: no Python 3.7 here. Send the report ($REPORT); see docs: The beamline analysis computers"; exit 1; }
echo "python: $(command -v python) ($(python -V 2>&1))"

echo "=== 1. shortcuts in $RC"
if [ -f "$RC" ] && grep -qF "$MARK" "$RC"; then
  echo "already there:"
  grep -F "$MARK" "$RC"
elif [ $GO -eq 0 ]; then
  echo "dry run: would append to $RC:"
  echo "  $LINE"
else
  cp -p "$RC" "$RC.before_silas" 2>/dev/null && echo "backup: $RC.before_silas"
  printf '\n%s\n' "$LINE" >> "$RC"
  echo "added. Open a new login (or: source $RC) to use bl / bl_check."
fi
if [ -f "$RC" ] && grep -q "hpc_env.sh" "$RC"; then
  echo "NOTE: $RC also loads the HPC shortcuts (shared home?): bl still uses the beamline profile"
fi

echo "=== 2. IPython and tornado"
if python -c 'import IPython, tornado' >/dev/null 2>&1; then
  echo "the SACLA Python has them already: nothing to install"
elif [ -d "$HOME/SILAS_py37/IPython" ] && [ -d "$HOME/SILAS_py37/tornado" ]; then
  echo "already installed in $HOME/SILAS_py37"
elif ! ls "$ROOT/third_party/py37/"*.whl >/dev/null 2>&1; then
  echo "skipped: no wheels in $ROOT/third_party/py37 (laptop: bash scripts/fetch_hpc_packages.sh, sync to"
  echo "  the HPC, then bl_update --go here). The session works without them (plain prompt)."
elif [ $GO -eq 0 ]; then
  bash "$HERE/install_hpc_packages.sh"
else
  bash "$HERE/install_hpc_packages.sh" --go || echo "not installed (plain prompt; the session works)"
fi

echo "=== 3. environment check (profile beamline)"
if [ $GO -eq 0 ]; then
  echo "dry run: would run  python -m silas.core.envcheck --profile beamline  (it is in the report above)"
else
  (cd "$ROOT" && python -m silas.core.envcheck --profile beamline)
fi

[ $GO -eq 0 ] && echo "dry run: run again with --go to do it"
[ $GO -eq 1 ] && echo "done: log in again (ssh -Y for windows), then bl starts the session"
exit 0
