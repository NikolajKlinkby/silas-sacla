#!/bin/bash
# LAPTOP: download JupyterLab and matplotlib's notebook widget for the HPC's SACLA Python 3.7, as
# wheels, into third_party/jupyter_py37/ (sync_to_hpc copies it over; the HPC has no internet):
#   jupyterlab 3.6     the notebook server (the last series for Python 3.7)
#   ipykernel 6.16     the notebooks' Python kernel
#   ipywidgets 7.7     interactive widgets in the notebook
#   ipympl 0.7.0       matplotlib figures that take keys and the mouse in a notebook (the viewers);
#                      the last version that works with the SACLA Python's matplotlib 3.1
# and their dependencies. The notebooks use the SACLA Python's own matplotlib, numpy, Pillow, ...
# (the same packages as the terminal session): pip downloads them as dependencies, they are deleted
# here (NOT_TAKEN). The list of wheels to install is written to wheels.txt (older wheels left in the
# folder, also on the HPC, are ignored).
#
#   bash scripts/fetch_jupyter_packages.sh
#   bash scripts/fetch_jupyter_packages.sh "somepkg<2" ...   # also these (what install_hpc_jupyter.sh reports missing)
#
# pip evaluates dependency markers with the laptop's Python, not 3.7, so backports that only
# Python 3.7 needs are named here explicitly (BACKPORTS); install_hpc_jupyter.sh checks the rest
# with the HPC's Python (scripts/check_target_deps.py).
#
# Then sync, and on the HPC: bash ~/SILAS/scripts/install_hpc_jupyter.sh --go

set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
DEST="third_party/jupyter_py37"
PIP="${SACLA_LOCAL:+$SACLA_LOCAL/venv/bin/pip}"
[ -n "$PIP" ] && [ -x "$PIP" ] || PIP="$(command -v pip3 || command -v pip)"
[ -n "$PIP" ] || { echo "no pip found"; exit 1; }
rm -rf "$DEST"               # start clean: no wheels of an older selection
mkdir -p "$DEST"
PKGS=("jupyterlab>=3.6,<4" "ipykernel>=6.16,<6.17" "ipywidgets>=7.7,<8" "jupyterlab_widgets>=1.1,<2"
      "widgetsnbextension>=3.6,<4" "ipympl==0.7.0")
# Python < 3.9 only (markers the laptop's pip skips): jsonschema 4.17 and others need them
BACKPORTS=("importlib_resources>=1.4,<6" "pkgutil_resolve_name>=1.3.10" "importlib_metadata>=4.8.3,<7"
           "typing_extensions>=4.7,<4.8" "zipp<3.16")
PKGS+=("${BACKPORTS[@]}" "$@")
# the SACLA Python has these (the session's versions): not installed for the notebooks
NOT_TAKEN=(matplotlib numpy setuptools kiwisolver cycler fonttools Pillow pyparsing)
echo "=== $PIP download ${PKGS[*]} for Python 3.7 (Linux x86_64) -> $DEST"
"$PIP" download "${PKGS[@]}" --dest "$DEST" \
    --python-version 37 --implementation cp --abi cp37m --abi abi3 --abi none \
    --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux1_x86_64 \
    --platform manylinux_2_5_x86_64 --platform manylinux2010_x86_64 --platform manylinux_2_12_x86_64 \
    --platform any --only-binary=:all: || exit 1
for n in "${NOT_TAKEN[@]}"; do rm -f "$DEST/$n"-*.whl; done
(cd "$DEST" && ls -1 *.whl > wheels.txt)
echo "=== wheels ($(wc -l < "$DEST/wheels.txt"), list in $DEST/wheels.txt):"
cat "$DEST/wheels.txt"
du -sh "$DEST"
echo "next: bash scripts/sync_to_hpc.sh --go, then on the HPC: bash ~/SILAS/scripts/install_hpc_jupyter.sh --go"
