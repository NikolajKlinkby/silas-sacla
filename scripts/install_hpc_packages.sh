#!/bin/bash
# HPC (or a beamline computer with Python 3.7): install the extra packages for SILAS
# (IPython 7.34, tornado 6.2 and their dependencies) from the wheels that
# scripts/fetch_hpc_packages.sh downloaded on the laptop (third_party/py37/, copied by
# sync_to_hpc). Also run by install_hpc_env.sh --go.
#
#   bash ~/SILAS/scripts/install_hpc_packages.sh       # DRY RUN: only shows what it would do
#   bash ~/SILAS/scripts/install_hpc_packages.sh --go  # do it (login node is fine; safe to repeat)
#
# Installs only into ~/SILAS_py37 (nothing in the SACLA module or ~/.local changes) and
# tests IPython, tornado and matplotlib's browser backend with the SACLA Python. The session
# finds the folder by itself (silas.core.bootstrap.OPTIONAL_PATHS). Undo: delete the folder.

set -u
GO=0
[ "${1:-}" = "--go" ] && GO=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHEELS="$ROOT/third_party/py37"
TARGET="$HOME/SILAS_py37"
MODULE="${SILAS_PY_MODULE:-python/SACLA_python-3.7/offline}"   # beamline: .../online
INTEL_PY="/home/software/opt/intel/oneapi/intelpython/python3.7/bin"   # used when there is no module

pyver() { python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null; }
if [ "$(pyver)" != "3.7" ]; then          # not loaded yet: load the SACLA Python module
  type module >/dev/null 2>&1 || source /etc/profile.d/modules.sh 2>/dev/null
  module load "$MODULE" >/dev/null 2>&1
fi
if [ "$(pyver)" != "3.7" ] && [ -x "$INTEL_PY/python3" ]; then
  PATH="$INTEL_PY:$PATH"; hash -r
fi
PY="$(command -v python)"
ver="$(pyver)"
echo "python: $PY ($ver)"
[ "$ver" = "3.7" ] || { echo "could not get Python 3.7 (module load $MODULE failed, no $INTEL_PY)"; exit 1; }
ls "$WHEELS"/*.whl >/dev/null 2>&1 || { echo "no wheels in $WHEELS: run scripts/fetch_hpc_packages.sh on the laptop and sync"; exit 1; }
echo "wheels: $(ls "$WHEELS"/*.whl | wc -l) in $WHEELS"
echo "target: $TARGET"

if [ $GO -eq 0 ]; then
  echo "DRY RUN. Would run:"
  echo "  $PY -m pip install --no-index --no-deps --upgrade --target $TARGET $WHEELS/*.whl"
  echo "  test: IPython, tornado, matplotlib WebAgg with PYTHONPATH=$TARGET"
  echo "Run again with --go to do it."
  exit 0
fi

"$PY" -m pip install --no-index --no-deps --upgrade --target "$TARGET" "$WHEELS"/*.whl || exit 1
echo "=== test"
(cd /tmp && PYTHONNOUSERSITE=1 PYTHONPATH="$TARGET" "$PY" -c '
import IPython
from IPython.terminal.interactiveshell import TerminalInteractiveShell
print("IPython", IPython.__version__, "from", IPython.__file__)
import tornado
print("tornado", tornado.version, "from", tornado.__file__)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.switch_backend("WebAgg")
print("matplotlib", matplotlib.__version__, "WebAgg backend OK")') || { echo "test failed: paste this output"; exit 1; }

echo "done: the session (bt) opens IPython, and plots on the HPC open in the browser"
