#!/bin/bash
# Copy the SILAS code from this computer to the SACLA HPC (rsync over ssh).
#
#   bash scripts/sync_to_hpc.sh               # DRY RUN: only lists what would be copied
#   bash scripts/sync_to_hpc.sh --go          # really copy
#   bash scripts/sync_to_hpc.sh --go --delete # also delete files on the HPC that were deleted here
#   bash scripts/sync_to_hpc.sh --host sacla_HPC --dest SILAS   # defaults
#   bash scripts/sync_to_hpc.sh --user <name> --go   # without the ssh alias: <name>@xhpcfep.hpc.spring8.or.jp
#
# Needs the SACLA VPN and the ssh alias "sacla_HPC" in ~/.ssh/config, or --user (docs: setup guide,
# step 1). Never copied and never deleted on the HPC (each place keeps its own):
#   config/  output/  docs/html/  __pycache__/  .mypy_cache/  .vscode/  *.pyc  .git/
# Not copied (laptop only): sacla_local_api/ (the local SACLA libraries) and docs/sacla_manuals/
# The code goes to ~/<dest> on the HPC (home: not deleted automatically, unlike /work).

set -u
HOST="sacla_HPC"
DEST="SILAS"
GO=0
DELETE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --go)     GO=1 ;;
    --delete) DELETE=1 ;;
    --host)   HOST="$2"; shift ;;
    --user)   HOST="$2@${SILAS_HPC_HOST:-xhpcfep.hpc.spring8.or.jp}"; shift ;;
    --dest)   DEST="$2"; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)"; exit 2 ;;
  esac
  shift
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # the SILAS folder
if [ ! -f "$SRC/start_session.py" ]; then
  echo "cannot find SILAS at $SRC"; exit 1
fi

# options as an array: no word-splitting surprises (a lesson from fetch_sacla.sh)
OPTS=(-a -v --human-readable --itemize-changes
      --exclude "config/" --exclude "output/" --exclude "docs/html/"
      --exclude "__pycache__/" --exclude ".mypy_cache/" --exclude ".vscode/" --exclude "*.pyc"
      --exclude ".git/" --exclude "/sacla_local_api/" --exclude "/docs/sacla_manuals/")
[ "$GO" -eq 1 ] || OPTS+=(--dry-run)
[ "$DELETE" -eq 1 ] && OPTS+=(--delete)   # excluded folders are protected from --delete

echo "from: $SRC/"
echo "to:   $HOST:$DEST/"
[ "$GO" -eq 1 ] && echo "mode: COPY$([ "$DELETE" -eq 1 ] && echo ' + delete')" \
                || echo "mode: dry run (nothing is copied; add --go to copy)"
echo

if ! ssh -o ConnectTimeout=10 "$HOST" true; then
  echo "cannot reach $HOST: VPN on? key login set up? (ssh $HOST hostname)"; exit 1
fi

rsync "${OPTS[@]}" "$SRC/" "$HOST:$DEST/"
rc=$?
echo
if [ $rc -ne 0 ]; then
  echo "rsync failed (exit $rc)"
elif [ "$GO" -eq 1 ]; then
  echo "done. On the HPC:  cd ~/$DEST && python start_session.py --profile hpc   (on a compute node)"
else
  echo "dry run finished. Lines starting with '<f' would be sent. Run again with --go to copy."
fi
exit $rc
