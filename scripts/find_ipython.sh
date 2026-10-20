#!/bin/bash
# Look for an IPython that works with the SILAS Python on the HPC (read-only).
#
#   bt_env                                   # load python/SACLA_python-3.7/offline first
#   bash ~/SILAS/scripts/find_ipython.sh [folder ...]   # default: /home/software /opt
#
# For every IPython package found (installed in some site-packages, or unpacked in a conda
# "pkgs" cache) it prints its version and then really tries it with THIS python:
#   import IPython + start its shell machinery, with only that folder added to sys.path.
# IPython 8 and newer need Python >= 3.8; for Python 3.7 the last one is 7.34.
# Nothing is installed or changed. Takes a few minutes (searches the file system).

set -u
PY="$(command -v python)"
echo "=== python: $PY  ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"
"$PY" - <<'EOF'
try:
    import IPython
    print("IPython already importable here:", IPython.__version__, IPython.__file__)
except ImportError as err:
    print("IPython not importable here:", err)
import site, sys
print("user site-packages:", site.getusersitepackages(), "(enabled)" if site.ENABLE_USER_SITE else "(DISABLED)")
EOF
command -v ipython ipython3 2>/dev/null | sed 's/^/ipython command on PATH: /'
module avail 2>&1 | tr ' ' '\n' | grep -i -E 'ipython|jupyter|anaconda|conda|python' | sort -u | sed 's/^/module: /'

ROOTS=("$@")
[ ${#ROOTS[@]} -eq 0 ] && ROOTS=(/home/software /opt)
echo "=== searching ${ROOTS[*]} for IPython packages (this takes a while)"
CANDS=$(mktemp)
for r in "${ROOTS[@]}"; do
  [ -d "$r" ] || continue
  # installed packages: .../site-packages/IPython ; conda cache: .../pkgs/ipython-7.x-*/lib/python3.7/site-packages/IPython
  timeout 600 find "$r" -maxdepth 10 -type d -name IPython -path '*site-packages*' 2>/dev/null >> "$CANDS"
done
sort -u -o "$CANDS" "$CANDS"
echo "found $(wc -l < "$CANDS") IPython folders"

echo "=== trying each with $PY"
while read -r dir; do
  [ -f "$dir/__init__.py" ] || continue
  sp="$(dirname "$dir")"
  ver=$(sed -n 's/^_version_major = \([0-9]*\).*/\1/p;s/^_version_minor = \([0-9]*\).*/\1/p;s/^_version_patch = \([0-9]*\).*/\1/p' \
        "$dir/core/release.py" 2>/dev/null | paste -sd. -)
  res=$(cd /tmp && PYTHONNOUSERSITE=1 PYTHONPATH="$sp" timeout 60 "$PY" - 2>&1 <<'EOF'
import IPython
from IPython.terminal.interactiveshell import TerminalInteractiveShell   # the real prompt (prompt_toolkit)
print("OK", IPython.__version__, IPython.__file__)
EOF
)
  if echo "$res" | grep -q '^OK'; then
    echo "WORKS    IPython $ver  $sp"
  else
    echo "fails    IPython ${ver:-?}  $sp   -> $(echo "$res" | tail -n 1 | cut -c1-120)"
  fi
done < "$CANDS"
rm -f "$CANDS"
echo "=== done. A 'WORKS' line: that folder can be added to the hpc profile's sys_paths."
