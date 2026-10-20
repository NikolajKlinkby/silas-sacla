#!/bin/bash
# Minimal copy of the SACLA Python APIs + MPCCD simulator data from the HPC.
# DRY RUN BY DEFAULT: nothing is downloaded unless you pass --go.
#
#   cd SILAS/sacla_local_api
#   bash scripts/fetch_sacla.sh          # dry run  -> hpc/dryrun_report.txt
#   bash scripts/fetch_sacla.sh --go     # real download into ./software/
#   bash scripts/fetch_sacla.sh --user <name> [--go]   # without the ssh alias sacla_HPC:
#                                                      # <name>@xhpcfep.hpc.spring8.or.jp
#
# What is copied (filter rules in the FILTER block below):
#   - every Python API, latest version: python/3.9/*.whl, python/*.cpp, python/setup.py, include/
#   - runtime libs needed by the wheels: DataArrayUserAPI, UserDatabaseAPI, boost 1.76,
#     hdf5 1.8.22, zeromq 4.3.4 (shared .so only)
#   - SACLA_tool/.config small files + apiconfig/
#   - .config/ol_simdata: catalog files + MPCCD detectors only (no OPAL/IPX)
#   - NOT copied: ctol_simdata (22 GB), static/icc libs, java, fortran, examples

set -u
HOST=sacla_HPC
R=/home/software
HERE=$(cd "$(dirname "$0")/.." && pwd)
DEST=$HERE/software
GO=0; CLEAN=0
while [ $# -gt 0 ]; do
  case $1 in
    --go) GO=1 ;;
    --clean) CLEAN=1 ;;   # also delete local files that the filter no longer selects
    --user) HOST="$2@${SILAS_HPC_HOST:-xhpcfep.hpc.spring8.or.jp}"; shift ;;
    --host) HOST="$2"; shift ;;
    *) echo "Unknown option $1 (use --go, --clean, --user NAME, --host ALIAS)"; exit 1 ;;
  esac
  shift
done
# NB: do NOT build the flag with $(echo -n): echo eats "-n" and prints nothing,
# which silently turned the old dry runs into real downloads.
RFLAGS=()
[ $GO -eq 0 ] && RFLAGS+=(--dry-run)
[ $CLEAN -eq 1 ] && RFLAGS+=(-v --delete --delete-excluded)

APIS="OnlineUserAPI DataAccessUserAPI TMAUserAPI UserDatabaseAPI ImageProcessingUserAPI CITIUSDataAccessUserAPI CITIUSOnlineUserAPI DataArrayUserAPI"

REPORT=$HERE/hpc/dryrun_report.txt
[ $GO -eq 0 ] && exec > >(tee "$REPORT") 2>&1
echo "Mode: $([ $GO -eq 1 ] && echo 'REAL DOWNLOAD' || echo 'DRY RUN (nothing is copied)')"
echo "Destination: $DEST"

# 1. resolve the 'latest' symlinks (one ssh call)
VERS=$(ssh $HOST "cd $R/SACLA_tool && for a in $APIS; do echo \$a \$(basename \$(readlink -f \$a/latest)); done")
echo; echo "=== Latest versions on the HPC"; echo "$VERS" | sed 's/^/  /'

# 2. source list: /home/software/./SACLA_tool/<API>/<ver>  (the /./ makes --relative keep SACLA_tool/...)
SRCS=()
while read -r a v; do SRCS+=("$R/./SACLA_tool/$a/$v"); done <<< "$VERS"
SRCS+=("$R/./SACLA_tool/.config")
# shared libraries the compiled wheels link against (.so only, no static libs/headers)
SRCS+=("$R/./local/boost-1.76.0_gcc-4.8.5/lib" "$R/./local/hdf5-1.8.22_gcc-4.8.5/lib" \
       "$R/./SACLA_tool/local/zeromq-4.3.4_gcc-4.8.5/lib")
# rsync syntax for several paths on one host: host:path1 :path2 :path3 ...
RSRC=("$HOST:${SRCS[0]}"); for s in "${SRCS[@]:1}"; do RSRC+=(":$s"); done
echo; echo "=== Sources (${#SRCS[@]})"; printf '  %s\n' "${SRCS[@]}"

# 3. filter rules: first match wins; paths are relative to /home/software/
FILTER=$(mktemp)
cat > "$FILTER" <<'EOF'
- /SACLA_tool/.config/ctol_simdata/
+ */
+ /SACLA_tool/*/*/python/3.9/*.whl
+ /SACLA_tool/*/*/python/*.cpp
+ /SACLA_tool/*/*/python/setup.py
+ /SACLA_tool/*/*/include/**
+ /SACLA_tool/DataArrayUserAPI/*/lib_gcc/**
+ /SACLA_tool/UserDatabaseAPI/*/lib/*.so*
+ /local/boost-1.76.0_gcc-4.8.5/lib/*.so*
+ /local/hdf5-1.8.22_gcc-4.8.5/lib/*.so*
+ /SACLA_tool/local/zeromq-4.3.4_gcc-4.8.5/lib/*.so*
+ /SACLA_tool/.config/ol_simdata/*.lst
+ /SACLA_tool/.config/ol_simdata/*.dat
+ /SACLA_tool/.config/ol_simdata/ol_simdata_det(MPCCD-*
+ /SACLA_tool/.config/ol_simdata/rawfiles/ol_simdata_det(MPCCD-*
+ /SACLA_tool/.config/apiconfig/**
+ /SACLA_tool/.config/*
- *
EOF

# 4. one rsync call. -R keep layout, -m drop empty dirs, -n dry run
echo; echo "=== rsync $([ $GO -eq 1 ] && echo transfer || echo 'dry run'): file list"
mkdir -p "$DEST"
OUT=$(rsync -aRm "${RFLAGS[@]}" --stats --out-format='%l %n' \
      --filter=". $FILTER" "${RSRC[@]}" "$DEST/" 2>&1)
RC=$?
rm -f "$FILTER"

# per-file list (skip directories) and per-API subtotal
echo "$OUT" | grep -iE 'error|warning|vanished|No such' | sed 's/^/  !! /'
echo "$OUT" | awk '$1 ~ /^[0-9]+$/ && $2 !~ /\/$/ {printf "  %10s  %s\n", $1, $2}'
echo; echo "=== Subtotal per top folder"
echo "$OUT" | awk '$1 ~ /^[0-9]+$/ && $2 !~ /\/$/ {split($2,p,"/"); k=p[2]; s[k]+=$1; n[k]++}
                   END {for (k in s) printf "  %-28s %8.1f MB  %4d files\n", k, s[k]/1048576, n[k]}' | sort
if [ $CLEAN -eq 1 ]; then
  echo; echo "=== Local files $([ $GO -eq 1 ] && echo deleted || echo 'that WOULD be deleted') (not selected by the filter)"
  echo "$OUT" | grep '^deleting ' | sed 's/^/  /'
fi
echo; echo "=== rsync summary"
echo "$OUT" | grep -E 'Number of (regular )?files|Total (transferred )?file size|rsync error|No such file'

# 4b. CentOS 7 system libraries that this laptop lacks.
#     On the HPC: ldd (full transitive closure) of every SACLA python extension -> system libs.
#     Here: keep only sonames that ldconfig does not know; copy them flat to software/compat/lib
#     (-L stores the real file under the soname). Libraries the laptop has are never copied.
echo; echo "=== CentOS 7 compat libraries -> software/compat/lib"
NEEDED=$(ssh $HOST "R=$R bash -s" <<'REMOTE'
ST=$R/SACLA_tool
export LD_LIBRARY_PATH=$ST/DataArrayUserAPI/latest/lib_gcc:$ST/UserDatabaseAPI/latest/lib:$R/local/boost-1.76.0_gcc-4.8.5/lib:$R/local/hdf5-1.8.22_gcc-4.8.5/lib:$ST/local/zeromq-4.3.4_gcc-4.8.5/lib
find $ST/*/latest/python/lib -name '*.so' 2>/dev/null | xargs -n1 ldd 2>/dev/null \
  | awk '$2=="=>" && $3 ~ /^\/(usr\/)?lib64\// {print $1, $3}' | sort -u
REMOTE
)
# sonames this laptop already provides (ldconfig -p lines start with a TAB, so take field 1)
LOCAL_SONAMES=$( { /sbin/ldconfig -p 2>/dev/null || ldconfig -p; } | awk 'NR>1 {print $1}' | sort -u)
[ -z "$LOCAL_SONAMES" ] && { echo "  !! ldconfig -p returned nothing; refusing to guess, skipping compat libs"; NEEDED=""; }
# safety net: never copy the C runtime / toolchain libraries, whatever ldconfig says
NEVER='^(ld-linux.*|libc|libm|libdl|libpthread|librt|libresolv|libutil|libnsl|libcrypt|libanl|libBrokenLocale|libnss_[a-z]+|libgcc_s|libstdc\+\+|libz|libselinux|libpcre)\.so'
COMPAT=()
while read -r soname path; do
  [ -z "$soname" ] && continue
  [[ $soname =~ $NEVER ]] && continue
  grep -qxF "$soname" <<< "$LOCAL_SONAMES" && continue      # laptop already has it
  COMPAT+=("$path")
done <<< "$NEEDED"
CT=0
# real run: rebuild compat/lib from scratch so libraries from earlier runs cannot linger
[ $GO -eq 1 ] && [ -n "$LOCAL_SONAMES" ] && rm -rf "$DEST/compat/lib"
if [ ${#COMPAT[@]} -gt 0 ]; then
  CSRC=("$HOST:${COMPAT[0]}"); for c in "${COMPAT[@]:1}"; do CSRC+=(":$c"); done
  mkdir -p "$DEST/compat/lib"
  CF=(); [ $GO -eq 0 ] && CF+=(--dry-run)
  COUT=$(rsync -aL "${CF[@]}" --stats --out-format='%l %n' "${CSRC[@]}" "$DEST/compat/lib/" 2>&1)
  echo "  needed (not on this laptop): $(printf '%s ' "${COMPAT[@]##*/}")"
  echo "$COUT" | awk '$1 ~ /^[0-9]+$/ {printf "  %10s  %s\n", $1, $2}'
  echo "$COUT" | grep -iE 'error|No such' | sed 's/^/  !! /'
  CT=$(echo "$COUT" | awk -F': ' '/Total transferred file size/ {gsub(/[^0-9]/,"",$2); print $2}')
else
  echo "  nothing missing"
fi
OUT="$OUT
Total transferred file size: $(( $(echo "$OUT" | awk -F': ' '/Total transferred file size/ {gsub(/[^0-9]/,"",$2); print $2}') + ${CT:-0} ))"

TOTAL=$(echo "$OUT" | awk -F': ' '/Total transferred file size/ {gsub(/[^0-9]/,"",$2); t=$2} END {print t}')
FREE=$(df -B1 --output=avail "$DEST" | tail -1)
echo
echo "=== Total $([ $GO -eq 1 ] && echo transferred || echo 'that WOULD be downloaded'): $(numfmt --to=iec "${TOTAL:-0}")"
echo "=== Free space: $(numfmt --to=iec "$FREE")"
[ $RC -ne 0 ] && echo "!!! rsync exited with code $RC (see summary above)"

# 5. after a real download: recreate 'latest' symlinks
if [ $GO -eq 1 ]; then
  while read -r a v; do
    [ -d "$DEST/SACLA_tool/$a/$v" ] && ln -sfn "$v" "$DEST/SACLA_tool/$a/latest"
  done <<< "$VERS"
  echo "latest -> symlinks recreated. Next: bash scripts/setup_env.sh"
else
  echo; echo "Dry run only. Report saved to hpc/dryrun_report.txt. Re-run with --go to download."
fi
