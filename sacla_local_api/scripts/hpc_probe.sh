#!/bin/bash
# Read-only survey of the SACLA Python APIs on the HPC (nothing is changed or copied).
# Run from the LocalAPI folder on your laptop (VPN on):
#   ssh sacla_HPC 'bash -l -s' < scripts/hpc_probe.sh > hpc/probe_report.txt 2>&1
# Then let Claude read hpc/probe_report.txt.

sec() { echo; echo "################ $*"; }
SW=/home/software/SACLA_tool
APIS="OnlineUserAPI DataAccessUserAPI TMAUserAPI UserDatabaseAPI ImageProcessingUserAPI CITIUSDataAccessUserAPI CITIUSOnlineUserAPI HDFAccessUserAPI RunDataFormatAccessUserAPI"

sec "SYSTEM"
hostname; uname -srm
grep -E '^(NAME|VERSION)=' /etc/os-release
ldd --version 2>&1 | head -1
echo "default python3: $(command -v python3) $(python3 -V 2>&1)"

sec "MODULES (SACLA / python)"
type module >/dev/null 2>&1 || source /etc/profile.d/modules.sh 2>/dev/null
module -t avail 2>&1 | grep -iE 'sacla|python' | sort -u

for m in SACLA_env_online SACLA_env_offline SACLA_tool/online SACLA_tool/offline \
         python/SACLA_python-3.7/online python/SACLA_python-3.7/offline; do
  sec "module show $m"
  module show "$m" 2>&1 | grep -vE '^-+$' | head -80
done

sec "LATEST VERSION OF EACH API: python/ and lib_gcc/ contents + sizes"
for a in $APIS; do
  d=$(readlink -f "$SW/$a/latest" 2>/dev/null)
  echo; echo "=== $a -> $d"
  [ -d "$d" ] || continue
  du -sh "$d" 2>/dev/null
  for sub in python lib_gcc lib_so_gcc include; do
    [ -d "$d/$sub" ] || continue
    echo "--- $sub/"
    find "$d/$sub" -maxdepth 3 \( -type f -o -type l \) -printf '%10s  %P\n' 2>/dev/null | head -60
  done
done

sec "ALL WHEELS / PYTHON EXTENSIONS SHIPPED BY SACLA"
find "$SW" -maxdepth 7 \( -name '*.whl' -o -name '*py*.so' -o -name 'setup.py' \) -printf '%10s  %p\n' 2>/dev/null | head -150

sec "LICENSE / README FILES"
find "$SW" -maxdepth 4 \( -iname '*licen*' -o -iname 'readme*' \) 2>/dev/null | head -40

check_python() {
  local mode=$1
  sec "PYTHON MODULES ($mode)"
  module purge >/dev/null 2>&1
  module load "python/SACLA_python-3.7/$mode" 2>&1
  echo "python: $(command -v python3) $(python3 -V 2>&1)"
  echo "--- full environment diff vs. clean login (SACLA-relevant)"
  env | grep -iE 'sacla|^ol_|ctol|simu|detinfo|catalog|syncdb|udb|tma|^db_|mysql|api|PYTHONPATH|LD_LIBRARY_PATH' | sort
  python3 - <<'PY'
import importlib, os, subprocess, sys
print("sys.path:", *sys.path, sep="\n   ")
mods = ["olpy", "dbpy", "stpy", "tmapy", "udbpy", "ippy", "ctdapy_xfel", "ctolpy_xfel", "ctdapy", "ctolpy"]
for name in mods:
    try:
        m = importlib.import_module(name)
    except Exception as e:
        print(f"\n[{name}] import FAILED: {e!r}")
        continue
    f = getattr(m, "__file__", None)
    ver = None
    for attr in ("get_api_version", "__version__", "version"):
        v = getattr(m, attr, None)
        if v is not None:
            try:
                ver = v() if callable(v) else v
            except Exception as e:
                ver = f"{attr} -> {e!r}"
            break
    print(f"\n[{name}] file={f} version={ver}")
    print("   public names:", ", ".join(n for n in dir(m) if not n.startswith("_")))
    if f and f.endswith(".so"):
        out = subprocess.run(["ldd", f], capture_output=True, text=True).stdout
        for line in out.splitlines():
            print("   ldd:", line.strip())
PY
}
check_python online
check_python offline

sec "SIMULATION DATA (online): config file contents and sizes"
C=$SW/.config
du -sh "$C" "$C"/ol_simdata "$C"/ol_simdata/rawfiles "$C"/ctol_simdata "$C"/ctol_simdata/* 2>/dev/null
for f in "$C"/ol_simdata/*.lst "$C"/ol_simdata/*.dat "$C"/ol_simdata/*.conf \
         "$C"/DataTypeOfDetectors.conf "$C"/RunDataBL3.conf "$C"/TMA_InitParam.conf "$C"/apiconfig/README; do
  [ -f "$f" ] || continue
  echo; echo "--- $f ($(stat -c %s "$f") bytes)"; head -c 3000 "$f"
done
echo; echo "--- rawfiles (first 40)"
ls -la "$C"/ol_simdata/rawfiles 2>/dev/null | head -40
echo; echo "--- ctol_simdata/run* (first 20 each)"
for r in "$C"/ctol_simdata/run*; do echo "  $r"; ls -la "$r" | head -20; done

sec "WHICH CONFIG PATHS ARE HARD-CODED IN THE LIBRARIES?"
for a in OnlineUserAPI CITIUSOnlineUserAPI DataAccessUserAPI; do
  d=$(readlink -f "$SW/$a/latest")
  for so in $(find "$d" -name '*.so*' -type f 2>/dev/null | head -10); do
    echo "--- $so"
    strings "$so" | grep -E '/home/software|\.config|simdata|SACLA_|getenv|\.conf$|\.lst$|\.dat$' | sort -u | head -40
  done
done

sec "DONE"
