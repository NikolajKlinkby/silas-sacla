#!/bin/bash
# LAPTOP: download the extra packages the HPC's SACLA Python 3.7 does not have, as wheels,
# into third_party/py37/ (sync_to_hpc copies it over; the HPC has no internet):
#   ipython 7.34.0   the session's IPython prompt (the last IPython for Python 3.7)
#   tornado 6.2      the browser view of the plots (matplotlib WebAgg; Tk windows hang on the HPC)
# and their dependencies. setuptools is not taken: the HPC Python has its own.
#
#   bash scripts/fetch_hpc_packages.sh
#
# Then sync, and on the HPC: bash ~/SILAS/scripts/install_hpc_packages.sh --go
# (or install_hpc_env.sh --go, which does it too).

set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
DEST="third_party/py37"
PIP="${SACLA_LOCAL:+$SACLA_LOCAL/venv/bin/pip}"
[ -n "$PIP" ] && [ -x "$PIP" ] || PIP="$(command -v pip3 || command -v pip)"
[ -n "$PIP" ] || { echo "no pip found"; exit 1; }
mkdir -p "$DEST"
echo "=== $PIP download ipython 7.34.0 + tornado 6.2 for Python 3.7 (Linux x86_64) -> $DEST"
"$PIP" download "ipython==7.34.0" "tornado==6.2" --dest "$DEST" \
    --python-version 37 --implementation cp --abi cp37m --abi abi3 --abi none \
    --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux1_x86_64 \
    --platform any --only-binary=:all: || exit 1
rm -f "$DEST"/setuptools-*.whl
echo "=== wheels:"
ls -1 "$DEST"
du -sh "$DEST"
echo "next: bash scripts/sync_to_hpc.sh --go, then on the HPC: bash ~/SILAS/scripts/install_hpc_packages.sh --go"
