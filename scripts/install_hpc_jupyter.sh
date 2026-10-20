#!/bin/bash
# HPC: install JupyterLab, ipykernel, ipywidgets and ipympl for SILAS notebooks from the wheels
# that scripts/fetch_jupyter_packages.sh downloaded on the laptop (third_party/jupyter_py37/,
# copied by sync_to_hpc; only the wheels named in its wheels.txt).
#
#   bash ~/SILAS/scripts/install_hpc_jupyter.sh       # DRY RUN: only shows what it would do
#   bash ~/SILAS/scripts/install_hpc_jupyter.sh --go  # do it (login node is fine; safe to repeat)
#
# Installs only into ~/SILAS_jupyter, which is emptied first (nothing in the SACLA module,
# ~/.local or ~/SILAS_py37 changes). Only the Jupyter server (silas/cli/jupyter_server.py)
# puts this folder on the path. The notebooks use the SACLA Python's matplotlib, numpy, ... (the same
# as the terminal session) and IPython from ~/SILAS_py37 (install_hpc_packages.sh).
# Undo: delete the folder.

set -u
GO=0
[ "${1:-}" = "--go" ] && GO=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHEELS="$ROOT/third_party/jupyter_py37"
TARGET="$HOME/SILAS_jupyter"
PY37="$HOME/SILAS_py37"
MODULE="python/SACLA_python-3.7/offline"

pyver() { python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null; }
if [ "$(pyver)" != "3.7" ]; then          # not loaded yet: load the SACLA Python module
  type module >/dev/null 2>&1 || source /etc/profile.d/modules.sh 2>/dev/null
  module load "$MODULE" >/dev/null 2>&1
fi
PY="$(command -v python)"
ver="$(pyver)"
echo "python: $PY ($ver)"
[ "$ver" = "3.7" ] || { echo "could not get Python 3.7 (module load $MODULE failed?)"; exit 1; }
[ -d "$PY37" ] || echo "WARNING: $PY37 is missing: run install_hpc_packages.sh --go first (IPython)"

# the wheels of the last download (wheels.txt), not older ones still in the folder
LIST=()
if [ -f "$WHEELS/wheels.txt" ]; then
  while read -r w; do
    [ -n "$w" ] || continue
    [ -f "$WHEELS/$w" ] || { echo "missing wheel $WHEELS/$w: sync again"; exit 1; }
    LIST+=("$WHEELS/$w")
  done < "$WHEELS/wheels.txt"
else
  echo "no $WHEELS/wheels.txt: run scripts/fetch_jupyter_packages.sh on the laptop and sync"; exit 1
fi
echo "wheels: ${#LIST[@]} from $WHEELS/wheels.txt"
echo "target: $TARGET (emptied first)"

if [ $GO -eq 0 ]; then
  echo "DRY RUN. Would run:"
  echo "  rm -rf $TARGET"
  echo "  $PY -m pip install --no-index --no-deps --target $TARGET <the ${#LIST[@]} wheels>"
  echo "  dependency check and test with PYTHONPATH=$TARGET:$PY37"
  echo "Run again with --go to do it."
  exit 0
fi

rm -rf "$TARGET"
"$PY" -m pip install --no-index --no-deps --target "$TARGET" "${LIST[@]}" || exit 1
echo "=== dependencies for this Python"
(cd /tmp && PYTHONNOUSERSITE=1 PYTHONPATH="$TARGET:$PY37" "$PY" "$ROOT/scripts/check_target_deps.py" "$TARGET") \
  || { echo "dependencies missing: fetch them on the laptop (line above), sync, run this again"; exit 1; }
echo "=== test"
(cd /tmp && PYTHONNOUSERSITE=1 PYTHONPATH="$TARGET:$PY37" JUPYTER_PATH="$TARGET/share/jupyter" "$PY" -c '
import os
import jupyterlab, ipykernel, ipywidgets, matplotlib
print("jupyterlab", jupyterlab.__version__, "ipykernel", ipykernel.__version__, "ipywidgets", ipywidgets.__version__)
print("matplotlib", matplotlib.__version__, "from", matplotlib.__file__, "(the SACLA Python s)")
if os.environ["PYTHONPATH"].split(":")[0] in matplotlib.__file__:
    print("WARNING: matplotlib comes from the Jupyter folder, not from the SACLA Python")
import ipympl
print("ipympl", ipympl.__version__)
lab = os.path.join(os.environ["JUPYTER_PATH"], "lab", "static")
print("JupyterLab pages:", "OK" if os.path.isdir(lab) else "MISSING (%s)" % lab)
labext = os.path.join(os.environ["JUPYTER_PATH"], "labextensions")
print("lab extensions:", ", ".join(sorted(os.listdir(labext))) if os.path.isdir(labext) else "NONE (widgets will not show)")
try:
    matplotlib.use("module://ipympl.backend_nbagg")
    import matplotlib.pyplot as plt  # noqa: F401
    print("pyplot with the ipympl backend: OK")
except Exception as err:
    print("(pyplot with ipympl outside a notebook: %s: %s; it is used again in the notebook)" % (type(err).__name__, err))') || { echo "test failed: paste this output"; exit 1; }

echo "done: bt_jupyter on the login node (or hpc_jupyter.sh --start on the laptop) starts a server"
