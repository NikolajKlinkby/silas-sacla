#!/bin/bash
# BEAMLINE (xu-bl3-anapc01/02): copy the SILAS code (and the wheels in third_party/) from your
# folder on the SACLA HPC to this computer. The laptop is not on DAQ-USER-LAN, so the code goes
# laptop -> HPC (sync_to_hpc.sh) -> beamline (this script).
#
#   bash fetch_from_hpc.sh                     # DRY RUN: lists what would be copied
#   bash fetch_from_hpc.sh --go                # copy
#   bash fetch_from_hpc.sh --go --config       # also copy the HPC's experiment config to config/beamline.json
#                                              # (only if there is none here; --force replaces it)
#   bash fetch_from_hpc.sh --user <user> --host xhpcfep.hpc.spring8.or.jp --src SILAS   # defaults: $USER, ...
#
# Never copied (each place keeps its own): config/ output/ docs/html/ __pycache__/ .mypy_cache/ *.pyc
# The first time, before SILAS is here, take the script itself from the HPC:
#   scp <user>@xhpcfep.hpc.spring8.or.jp:SILAS/scripts/fetch_from_hpc.sh . && bash fetch_from_hpc.sh --go

set -u
HPC_HOST="${SILAS_HPC_HOST:-xhpcfep.hpc.spring8.or.jp}"
HPC_USER="${USER:-$(id -un)}"
SRC="SILAS"
DEST="${SILAS_HOME:-$HOME/SILAS}"
GO=0
CONFIG=0
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --go)     GO=1 ;;
    --config) CONFIG=1 ;;
    --force)  FORCE=1 ;;
    --user)   HPC_USER="$2"; shift ;;
    --host)   HPC_HOST="$2"; shift ;;
    --src)    SRC="$2"; shift ;;
    --dest)   DEST="$2"; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)"; exit 2 ;;
  esac
  shift
done
REMOTE="$HPC_USER@$HPC_HOST"
EXCLUDES=(config/ output/ docs/html/ __pycache__/ .mypy_cache/ .vscode/ "*.pyc")

echo "from: $REMOTE:$SRC/"
echo "to:   $DEST/"
[ "$GO" -eq 1 ] && echo "mode: COPY" || echo "mode: dry run (nothing is copied; add --go to copy)"
echo
mkdir -p "$DEST" || exit 1

if command -v rsync >/dev/null 2>&1 && ssh -o ConnectTimeout=10 "$REMOTE" 'command -v rsync' >/dev/null 2>&1; then
  OPTS=(-a -v --human-readable --itemize-changes)
  for e in "${EXCLUDES[@]}"; do OPTS+=(--exclude "$e"); done
  [ "$GO" -eq 1 ] || OPTS+=(--dry-run)
  rsync "${OPTS[@]}" "$REMOTE:$SRC/" "$DEST/" || { echo "rsync failed"; exit 1; }
else
  # no rsync on one side: tar through ssh (copies everything except the excluded folders)
  # (GNU tar: folders without the trailing /; ./ = only at the top of SILAS)
  TAROPTS=("--exclude='./config'" "--exclude='./output'" "--exclude='./docs/html'"
           "--exclude='__pycache__'" "--exclude='.mypy_cache'" "--exclude='.vscode'" "--exclude='*.pyc'")
  echo "(no rsync: tar over ssh)"
  if [ "$GO" -eq 0 ]; then
    ssh "$REMOTE" "cd $SRC && tar ${TAROPTS[*]} -cf - . " | tar -tvf - | tail -n 30
    echo "... (last 30 entries shown)"
  else
    ssh "$REMOTE" "cd $SRC && tar ${TAROPTS[*]} -cf - . " | tar -C "$DEST" -xf - || { echo "copy failed"; exit 1; }
  fi
fi

if [ "$CONFIG" -eq 1 ]; then
  echo
  here="$DEST/config/beamline.json"
  if [ -f "$here" ] && [ "$FORCE" -eq 0 ]; then
    echo "config: $here already exists: kept (--force replaces it)"
  elif [ "$GO" -eq 0 ]; then
    echo "config: dry run: would copy $REMOTE:$SRC/config/hpc.json -> $here"
  else
    mkdir -p "$DEST/config"
    [ -f "$here" ] && cp -p "$here" "$here.before_fetch" && echo "config: backup $here.before_fetch"
    if scp -q "$REMOTE:$SRC/config/hpc.json" "$here"; then
      echo "config: $here (the HPC's experiment config: detectors, ROIs, time tool, ...)"
      grep -n '"output"' "$here" | sed 's/^/  check this path exists here: /'
    else
      echo "config: no config/hpc.json on the HPC"
    fi
  fi
fi
echo
[ "$GO" -eq 1 ] && echo "done. Next: bash $DEST/scripts/install_beamline_env.sh   (first time), or bl_check"
exit 0
