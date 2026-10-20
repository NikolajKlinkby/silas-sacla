#!/bin/bash
# One-time setup of the HPC for SILAS. Run ON THE HPC (the login node is fine):
#
#   bash ~/SILAS/scripts/install_hpc_env.sh        # dry run: shows what would be done
#   bash ~/SILAS/scripts/install_hpc_env.sh --go   # do it (safe to repeat)
#
# 1. adds the SILAS shortcuts (scripts/hpc_env.sh) to ~/.bashrc: every new login and
#    every compute-node job has bt_node, bt, bt_check, bt_env
# 2. installs IPython (the session prompt) and tornado (plots in the browser) into
#    ~/SILAS_py37 (scripts/install_hpc_packages.sh, from third_party/py37/ that
#    sync_to_hpc copies over)
# 3. if their wheels are there (scripts/fetch_jupyter_packages.sh on the laptop): JupyterLab and
#    ipympl into ~/SILAS_jupyter for notebooks (scripts/install_hpc_jupyter.sh, bt_jupyter)

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENVFILE="$HERE/hpc_env.sh"
RC="$HOME/.bashrc"
MARK="# SILAS shortcuts"
LINE="[ -f \"$ENVFILE\" ] && source \"$ENVFILE\"  $MARK"

GO=0
[ "${1:-}" = "--go" ] && GO=1

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
  echo "added. Open a new login (or: source $RC) to use bt_node / bt / bt_check."
fi

echo "=== 2. IPython and tornado"
if [ -d "$HOME/SILAS_py37/IPython" ] && [ -d "$HOME/SILAS_py37/tornado" ]; then
  echo "already installed in $HOME/SILAS_py37"
elif [ $GO -eq 0 ]; then
  bash "$HERE/install_hpc_packages.sh"
else
  bash "$HERE/install_hpc_packages.sh" --go || echo "not installed (plain prompt, no plots in the browser)"
fi
echo "=== 3. Jupyter (notebooks)"
if [ -d "$HOME/SILAS_jupyter/jupyterlab" ]; then
  echo "already installed in $HOME/SILAS_jupyter"
elif ! ls "$HERE/../third_party/jupyter_py37/"*.whl >/dev/null 2>&1; then
  echo "skipped: no wheels (laptop: bash scripts/fetch_jupyter_packages.sh, then sync)"
elif [ $GO -eq 0 ]; then
  bash "$HERE/install_hpc_jupyter.sh"
else
  bash "$HERE/install_hpc_jupyter.sh" --go || echo "Jupyter not installed (the session works without it)"
fi
[ $GO -eq 0 ] && echo "dry run: run again with --go to do it"
exit 0
