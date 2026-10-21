#!/bin/bash
# One-time setup of the local SACLA Python environment.
#
#   cd SILAS/sacla_local_api
#   bash scripts/setup_env.sh            # create venv (if missing) + install everything
#   bash scripts/setup_env.sh --fresh    # delete and recreate the venv (also after moving the folder:
#                                        # a venv and .env contain absolute paths)
#   PY=/path/to/python3.9 bash scripts/setup_env.sh   # use another interpreter
#
# Afterwards:  source sacla_env.sh   (and sacla_off to leave)
#
# Steps:
#   1. venv ./venv with Python 3.9 (the SACLA wheels are cp39)
#   2. pip packages from EXTRA_PKGS below (numpy pinned < 2: the wheels were built against numpy 1.x)
#   3. the SACLA wheels found in software/SACLA_tool/*/latest/python/3.9/
#   4. software/ol_simdata_mpccd/: simulator folder with only the MPCCD detectors listed
#   5. hand-written .pyi stubs (stubs/src) installed as <module>-stubs + selftest
#   6. .env file for VS Code (python.envFile) with the same variables as sacla_env.sh
#   7. smoke test: import every module and print its API version

set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
VENV=$HERE/venv
ST=$HERE/software/SACLA_tool
PY=${PY:-python3.9}
# fall back to a uv-managed Python 3.9 (uv python install 3.9) if there is no system python3.9
if ! command -v "$PY" >/dev/null && command -v uv >/dev/null; then
  PY=$(uv python find 3.9 2>/dev/null) && echo "Using uv-managed Python: $PY"
fi

# edit freely: extra packages for analysis work in the same venv
EXTRA_PKGS=(
  "numpy<2"
  h5py
  matplotlib
  ipython
  ipykernel
  mypy          # type checker used by scripts/sacla_check.sh
  pandas
  "markdown-it-py>=3.0"
  markdown
)

step() { echo; echo "=== $*"; }

# --- 1. venv
command -v "$PY" >/dev/null || { echo "ERROR: $PY not found (set PY=/path/to/python3.9)"; exit 1; }
"$PY" -c 'import sys; assert sys.version_info[:2]==(3,9), sys.version' \
  || { echo "ERROR: $PY is not Python 3.9; the SACLA wheels are cp39 only"; exit 1; }
[ "${1:-}" = "--fresh" ] && { step "Removing old venv"; rm -rf "$VENV"; }
if [ ! -x "$VENV/bin/python" ]; then
  step "Creating venv with $("$PY" -V)"
  "$PY" -m venv --prompt sacla "$VENV" || {
    echo "ERROR: venv creation failed. On Debian/Ubuntu: sudo apt install python3.9-venv"; exit 1; }
fi
PIP="$VENV/bin/python -m pip"

# --- 2. pip packages
step "Installing pip packages: ${EXTRA_PKGS[*]}"
$PIP install -q --upgrade pip wheel
$PIP install -q "${EXTRA_PKGS[@]}" || { echo "ERROR: pip install failed"; exit 1; }

# --- 3. SACLA wheels (--no-deps: they declare none we need, and must not pull numpy 2)
step "Installing SACLA wheels"
WHEELS=( "$ST"/*/latest/python/3.9/*.whl )
[ -e "${WHEELS[0]}" ] || { echo "ERROR: no wheels under $ST/*/latest/python/3.9/ (run fetch_sacla.sh --go)"; exit 1; }
for w in "${WHEELS[@]}"; do echo "  $(basename "$w")"; done
$PIP install -q --no-deps --force-reinstall "${WHEELS[@]}" || { echo "ERROR: wheel install failed"; exit 1; }

# --- 4. simulator: MPCCD detectors only
# The mirror (software/SACLA_tool/.config/ol_simdata) stays identical to the HPC, so fetch_sacla.sh
# never fights with local edits. We build software/ol_simdata_mpccd/ with symlinks to the mirror
# and our own DetectorsConf.lst; sacla_env.sh points OL_SIMPATH there.
step "Simulator folder (MPCCD only)"
SRC=$ST/.config/ol_simdata
SIM=$HERE/software/ol_simdata_mpccd
if [ -f "$SRC/DetectorsConf.lst.hpc" ]; then      # undo the in-place edit of an earlier setup run
  mv -f "$SRC/DetectorsConf.lst.hpc" "$SRC/DetectorsConf.lst"
fi
if [ -f "$SRC/DetectorsConf.lst" ]; then
  rm -rf "$SIM"; mkdir -p "$SIM"
  for f in "$SRC"/*; do
    [ "$(basename "$f")" = DetectorsConf.lst ] || ln -s "$f" "$SIM/"
  done
  { grep '^#' "$SRC/DetectorsConf.lst"; grep 'MPCCD' "$SRC/DetectorsConf.lst"; } > "$SIM/DetectorsConf.lst"
  echo "  $SIM"; sed 's/^/    /' "$SIM/DetectorsConf.lst"
else
  echo "  WARNING: $SRC/DetectorsConf.lst missing"
fi

# load the runtime environment for the remaining steps
set +u   # the venv activate script is not 'set -u' safe
# shellcheck disable=SC1091
source "$HERE/sacla_env.sh" >/dev/null || exit 1

# --- 5. hand-written stubs (stubs/src/*.pyi) -> <module>-stubs packages in the venv
#     constants are filled in from the compiled modules; udbpy (no cp39 wheel) is stub-only
step "Installing SACLA API stubs"
SITE=$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
python "$HERE/stubs/build_stubs.py" "$SITE" || echo "  WARNING: stub build failed"
if [ -x "$HERE/scripts/sacla_check.sh" ] || [ -f "$HERE/scripts/sacla_check.sh" ]; then
  bash "$HERE/scripts/sacla_check.sh" --selftest | tail -1
fi

# --- 6. VS Code .env (point python.envFile at it, select venv/bin/python as interpreter)
step "Writing .env for VS Code"
env | grep -E '^(SACLA_TOOL_HOME|BLSTAFF_CONF_DIR|LLPARDIR|OL_SIMPATH|DEFAULT_SIGNAL_BL[123]|UDB_SERVER|UDB_OFFLINE_SERVER|LD_LIBRARY_PATH)=' \
  | sort > "$HERE/.env"
sed 's/^/  /' "$HERE/.env"

# --- 7. smoke test
step "Smoke test"
python - <<'PY'
import importlib
for name in ["olpy", "dbpy", "stpy", "tmapy", "ippy", "ctdapy_xfel", "ctolpy_xfel"]:
    try:
        m = importlib.import_module(name)
        v = getattr(m, "get_api_version", lambda: "?")
        print(f"  OK    {name:12s} version {v() if callable(v) else v}")
    except Exception as e:
        print(f"  FAIL  {name:12s} {type(e).__name__}: {e}")
import numpy; print(f"  numpy {numpy.__version__}")
PY

# for every compiled SACLA module: list shared libraries that cannot be found
step "Missing shared libraries (ldd 'not found')"
MISSING=0
for so in "$SITE"/{olpy,dbpy,stpy,tmapy,ippy,ctdapy_xfel,ctolpy_xfel}*.so "$SITE"/*/{olpy,dbpy,stpy,tmapy,ippy,ctdapy_xfel,ctolpy_xfel}*.so; do
  [ -f "$so" ] || continue
  nf=$(ldd "$so" 2>/dev/null | grep 'not found' | awk '{print $1}' | sort -u | tr '\n' ' ')
  [ -n "$nf" ] && { echo "  $(basename "$so"): $nf"; MISSING=1; }
done
[ $MISSING -eq 0 ] && echo "  none"

echo
echo "Done. Activate with:  source $HERE/sacla_env.sh"
