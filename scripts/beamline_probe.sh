#!/bin/bash
# BEAMLINE (xu-bl3-anapc01/02, or any computer): what is there for SILAS? Only looks, changes
# nothing, reads no detector data. Writes a report to share (paste it, or copy it to the HPC):
#
#   bash ~/SILAS/scripts/beamline_probe.sh            # report -> ~/SILAS/output/beamline_probe_<host>.txt
#   bash ~/SILAS/scripts/beamline_probe.sh --online   # also ask olpy for the online detector list
#
# Checks: host, OS, cores, memory, disks (home, /dev/shm, /work, /UserData), environment modules
# and the SACLA Python modules, the Pythons found (SACLA's Intel Python 3.7, python3, conda),
# the Python packages SILAS uses, the SACLA libraries (envcheck with the beamline profile),
# X display / xterm / Tk, mpirun, ssh to the HPC, internet (for pip), a browser (for Jupyter).
# install_beamline_env.sh runs it first.

set -u
ONLINE=0
[ "${1:-}" = "--online" ] && ONLINE=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="$(hostname -s 2>/dev/null || hostname)"
mkdir -p "$ROOT/output"
REPORT="$ROOT/output/beamline_probe_${HOST}.txt"
INTEL_PY="/home/software/opt/intel/oneapi/intelpython/python3.7/bin/python3"
PY_MODULE="${SILAS_PY_MODULE:-python/SACLA_python-3.7/online}"
HPC_HOST="${SILAS_HPC_HOST:-xhpcfep.hpc.spring8.or.jp}"

section() { printf '\n=== %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
t() { if have timeout; then timeout "$@"; else shift; "$@"; fi; }   # t SECONDS cmd ...

pkgs_of() {   # pkgs_of PYTHON: versions of the packages SILAS uses
  "$1" - <<'PY' 2>&1
import importlib, sys
print("python", sys.version.split()[0], sys.executable)
for name in ["numpy", "pandas", "matplotlib", "h5py", "scipy", "mpi4py", "IPython", "tornado",
             "tkinter", "jupyterlab", "notebook", "ipykernel", "ipympl"]:
    try:
        m = importlib.import_module(name)
        v = getattr(m, "__version__", None) or getattr(m, "TkVersion", None) or getattr(m, "version", "?")
        print("  %-11s %s" % (name, v))
    except Exception as err:
        print("  %-11s --  %s: %s" % (name, type(err).__name__, str(err).splitlines()[0] if str(err) else ""))
try:
    import matplotlib
    print("  matplotlib default backend:", matplotlib.get_backend())
except Exception:
    pass
PY
}

{
echo "SILAS beamline probe   $(date '+%Y-%m-%d %H:%M:%S')   user ${USER:-$(id -un)}   host $(hostname)"

section "system"
grep -E '^(PRETTY_NAME|VERSION)=' /etc/os-release 2>/dev/null || cat /etc/redhat-release 2>/dev/null
uname -r
echo "cores: $(nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo)"
grep -E '^(MemTotal|MemAvailable)' /proc/meminfo
echo "home: $HOME"
df -h "$HOME" /dev/shm /work /UserData /tmp 2>/dev/null

section "environment modules"
if ! type module >/dev/null 2>&1; then
  for f in /etc/profile.d/modules.sh /usr/share/Modules/init/bash /etc/profile.d/lmod.sh; do
    [ -f "$f" ] && source "$f" 2>/dev/null && break
  done
fi
if type module >/dev/null 2>&1; then
  echo "module command: yes"
  module avail 2>&1 | tr -s ' \t' '\n' | grep -iE 'python|sacla' | sort -u | sed 's/^/  /'
  echo "loading $PY_MODULE:"
  if module load "$PY_MODULE" 2>&1; then
    echo "  OK: python is $(command -v python) ($(python -V 2>&1))"
    for v in SACLA_TOOL_HOME BLSTAFF_CONF_DIR LLPARDIR OL_SIMPATH DEFAULT_SIGNAL_BL3; do
      printf '  %-18s %s\n' "$v" "${!v:-(not set)}"
    done
    [ -n "${OL_SIMPATH:-}" ] && echo "  NOTE: OL_SIMPATH is set: olpy reads the SIMULATOR, not the beamline"
  else
    echo "  module load failed"
  fi
else
  echo "module command: NO (no environment modules; the SACLA Python is used by its path)"
fi

section "Pythons"
for p in python python3 python3.7 "$INTEL_PY" /opt/anaconda*/bin/python /opt/conda/bin/python \
         "$HOME"/anaconda3/bin/python "$HOME"/miniconda3/bin/python; do
  if have "$p" || [ -x "$p" ]; then
    printf '  %-60s %s\n' "$(command -v "$p" 2>/dev/null || echo "$p")" "$("$p" -V 2>&1)"
  fi
done
[ -x "$INTEL_PY" ] || echo "  SACLA Intel Python 3.7 NOT found at $INTEL_PY"

section "packages of the SACLA Python"
PY=""
if [ "$(python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" = "3.7" ]; then
  PY="$(command -v python)"
elif [ -x "$INTEL_PY" ]; then
  PY="$INTEL_PY"
fi
if [ -n "$PY" ]; then
  PYTHONPATH="$HOME/SILAS_py37${PYTHONPATH:+:$PYTHONPATH}" pkgs_of "$PY"
  echo "  (with ~/SILAS_py37 on the path: $( [ -d "$HOME/SILAS_py37" ] && echo present || echo 'not installed yet'))"
else
  echo "  no Python 3.7 found: SILAS needs one (see docs: The beamline analysis computers)"
fi

section "SACLA libraries (envcheck, profile beamline)"
if [ -n "$PY" ]; then
  (cd "$ROOT" && t 120 "$PY" -m silas.core.envcheck --profile beamline 2>&1)
  if [ "$ONLINE" -eq 1 ]; then
    echo "--- olpy online detector list:"
    (cd "$ROOT" && SILAS_PROFILE=beamline t 60 "$PY" -c '
import silas  # noqa: F401  (beamline profile: sys.path)
import olpy
print("  ", list(olpy.read_detidlist()))' 2>&1)
  fi
fi

section "display, windows, MPI"
echo "DISPLAY=${DISPLAY:-(not set: log in with ssh -Y for windows)}"
for c in xterm gnome-terminal xdpyinfo mpirun mpiexec firefox chromium google-chrome rsync ssh git; do
  printf '  %-15s %s\n' "$c" "$(command -v "$c" 2>/dev/null || echo 'NOT FOUND')"
done
if [ -n "${DISPLAY:-}" ] && have xdpyinfo; then
  t 10 xdpyinfo 2>/dev/null | grep -E 'dimensions|depths' | head -2
fi
if [ -n "$PY" ] && [ -n "${DISPLAY:-}" ]; then
  t 20 "$PY" -c 'import tkinter; r = tkinter.Tk(); r.withdraw(); print("  Tk window: OK"); r.destroy()' 2>&1 | tail -1
fi

section "network"
if t 10 ssh -o BatchMode=yes -o ConnectTimeout=8 "$HPC_HOST" true 2>/dev/null; then
  echo "ssh to the HPC ($HPC_HOST): OK (key login)"
else
  echo "ssh to the HPC ($HPC_HOST): no key login (a password login may still work: ssh $HPC_HOST)"
fi
if have curl && t 10 curl -sSI -o /dev/null https://pypi.org/simple/ 2>/dev/null; then
  echo "internet (pypi.org): yes"
else
  echo "internet (pypi.org): no (packages come as wheels from third_party/)"
fi

section "SILAS here"
echo "folder: $ROOT"
ls -d "$ROOT"/third_party/*/ 2>/dev/null | sed 's/^/  wheels: /'
ls "$ROOT"/config 2>/dev/null | sed 's/^/  config: /'
grep -n "SILAS" "$HOME/.bashrc" 2>/dev/null | sed 's/^/  .bashrc: /'
echo
echo "end of report"
} 2>&1 | tee "$REPORT"

echo
echo "report written to $REPORT"
