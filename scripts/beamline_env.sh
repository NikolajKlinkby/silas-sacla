# SILAS shortcuts for the beamline analysis computers (xu-bl3-anapc01/02, DAQ-USER-LAN).
# Source it (from ~/.bashrc, see install_beamline_env.sh):
#
#   source ~/SILAS/scripts/beamline_env.sh
#
#   bl [start_session options]     bl_env + cd ~/SILAS + start the session (profile beamline)
#   bl_env                         the SACLA Python 3.7 for online analysis: module
#                                  python/SACLA_python-3.7/online, or SACLA's Intel Python by its path
#   bl_check                       bl_env + environment check (packages, SACLA libraries, paths)
#   bl_probe [--online]            report of what this computer has (scripts/beamline_probe.sh)
#   bl_update [--go]               copy the newest code from the HPC (scripts/fetch_from_hpc.sh; dry run
#                                  without --go)
#
# Unlike the HPC there are no batch jobs here: the session, the viewers (windows through ssh -Y) and
# the online loggers run on this computer. ~/SILAS_py37 (IPython, tornado) is found by the
# session by itself, as on the HPC.

export SILAS_HOME="${SILAS_HOME:-$HOME/SILAS}"
# beamline, also if a shared ~/.bashrc set the HPC profile before
[ "${SILAS_PROFILE:-hpc}" = "hpc" ] && export SILAS_PROFILE=beamline
SILAS_PY_MODULE="${SILAS_PY_MODULE:-python/SACLA_python-3.7/online}"
SILAS_INTEL_PY="/home/software/opt/intel/oneapi/intelpython/python3.7/bin"

_bl_pyver() { python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null; }

bl_env() {
  [ "$(_bl_pyver)" = "3.7" ] && [ -n "${SILAS_BL_ENV:-}" ] && return 0
  if ! type module >/dev/null 2>&1; then
    local f
    for f in /etc/profile.d/modules.sh /usr/share/Modules/init/bash /etc/profile.d/lmod.sh; do
      [ -f "$f" ] && source "$f" 2>/dev/null && break
    done
  fi
  if type module >/dev/null 2>&1 && module load "$SILAS_PY_MODULE" >/dev/null 2>&1; then
    :
  elif [ -x "$SILAS_INTEL_PY/python3" ]; then
    # no module: SACLA's Intel Python by its path; the beamline profile adds the SACLA libraries
    export PATH="$SILAS_INTEL_PY:$PATH"
    hash -r
  fi
  if [ "$(_bl_pyver)" != "3.7" ]; then
    echo "bl_env: no Python 3.7 (module $SILAS_PY_MODULE failed and $SILAS_INTEL_PY is missing);"
    echo "        run bl_probe and see docs: The beamline analysis computers"
    return 1
  fi
  if [ -n "${OL_SIMPATH:-}" ]; then
    echo "bl_env: NOTE OL_SIMPATH is set ($OL_SIMPATH): olpy reads the SIMULATOR, not the detectors"
  fi
  export SILAS_BL_ENV=1
}

bl() {
  bl_env || return 1
  cd "$SILAS_HOME" || return 1
  python start_session.py --profile "$SILAS_PROFILE" "$@"
}

bl_check() {
  bl_env || return 1
  (cd "$SILAS_HOME" && python -m silas.core.envcheck --profile "$SILAS_PROFILE")
}

bl_probe() {
  bash "$SILAS_HOME/scripts/beamline_probe.sh" "$@"
}

bl_update() {
  bash "$SILAS_HOME/scripts/fetch_from_hpc.sh" "$@"
}

if [ -n "${PS1:-}" ]; then
  echo "SILAS: bl to start the session, bl_check to check the environment"
  [ -z "${DISPLAY:-}" ] && echo "  (no DISPLAY: log in with ssh -Y for the viewers' windows)"
fi
