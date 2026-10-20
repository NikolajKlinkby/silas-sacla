# Activate the local SACLA Python environment (online-simulation mode).
#
#   source SILAS/sacla_local_api/sacla_env.sh
#   sacla_off            # undo everything again
#
# Mirrors the HPC modules SACLA_env_online + python/SACLA_python-3.7/online,
# but with Python 3.9 (venv) and the files copied to ./software.
# bash only (uses BASH_SOURCE).

if [ -n "${SACLA_LOCAL:-}" ]; then
  echo "SACLA env already active ($SACLA_LOCAL). Run sacla_off first."
  return 0 2>/dev/null || exit 0
fi

export SACLA_LOCAL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -x "$SACLA_LOCAL/venv/bin/python" ]; then
  echo "No venv found. Run: bash $SACLA_LOCAL/scripts/setup_env.sh"
  unset SACLA_LOCAL
  return 1 2>/dev/null || exit 1
fi

# remember what we change, so sacla_off can restore it
_SACLA_OLD_LD_LIBRARY_PATH="${LD_LIBRARY_PATH-__unset__}"

_ST="$SACLA_LOCAL/software/SACLA_tool"

# --- same variables as the HPC module SACLA_env_online
export SACLA_TOOL_HOME="$_ST"
export BLSTAFF_CONF_DIR="$_ST/.config"
export LLPARDIR="$_ST/.config/apiconfig"
# olpy simulator: MPCCD-only view built by setup_env.sh (full HPC copy: $_ST/.config/ol_simdata)
export OL_SIMPATH="$SACLA_LOCAL/software/ol_simdata_mpccd"
export DEFAULT_SIGNAL_BL1=xfel_bl_1_shutter_1_status/valid
export DEFAULT_SIGNAL_BL2=xfel_bl_2_shutter_1_status/valid
export DEFAULT_SIGNAL_BL3=xfel_bl_3_shutter_1_status/valid
export UDB_SERVER=xuonludb01.user.xfel.cntl.local     # not reachable from here, set for completeness
export UDB_OFFLINE_SERVER=xhoffudb01.hpc.spring8.or.jp
# CTOL_SIMPATH (CITIUS simulator) is left unset: no ctol_simdata copied.
# To use one later:  export CTOL_SIMPATH=$_ST/.config/ctol_simdata/<run>/bl2_<run>_L1_master.h5

# --- shared libraries the compiled wheels expect (the HPC RPATHs do not exist here)
_SACLA_LIBS=""
for d in "$_ST/DataArrayUserAPI/latest/lib_gcc" \
         "$_ST/UserDatabaseAPI/latest/lib" \
         "$SACLA_LOCAL/software/local/boost-1.76.0_gcc-4.8.5/lib" \
         "$SACLA_LOCAL/software/local/hdf5-1.8.22_gcc-4.8.5/lib" \
         "$_ST/local/zeromq-4.3.4_gcc-4.8.5/lib" \
         "$SACLA_LOCAL/software/compat/lib"; do      # CentOS 7 libmysqlclient/openssl 1.0, last = lowest priority
  [ -d "$d" ] && _SACLA_LIBS="$_SACLA_LIBS${_SACLA_LIBS:+:}$d"
done
export LD_LIBRARY_PATH="$_SACLA_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
unset d _ST _SACLA_LIBS

# --- the venv (prompt shows "(sacla)", set when the venv was created)
source "$SACLA_LOCAL/venv/bin/activate"

# static smoke test of scripts against the API stubs (see scripts/sacla_check.sh)
sacla_check() { bash "$SACLA_LOCAL/scripts/sacla_check.sh" "$@"; }

sacla_off() {
  deactivate 2>/dev/null
  if [ "$_SACLA_OLD_LD_LIBRARY_PATH" = "__unset__" ]; then unset LD_LIBRARY_PATH
  else export LD_LIBRARY_PATH="$_SACLA_OLD_LD_LIBRARY_PATH"; fi
  unset SACLA_LOCAL SACLA_TOOL_HOME BLSTAFF_CONF_DIR LLPARDIR OL_SIMPATH CTOL_SIMPATH \
        DEFAULT_SIGNAL_BL1 DEFAULT_SIGNAL_BL2 DEFAULT_SIGNAL_BL3 UDB_SERVER UDB_OFFLINE_SERVER \
        _SACLA_OLD_LD_LIBRARY_PATH
  unset -f sacla_off sacla_check
  echo "SACLA env deactivated."
}

echo "SACLA env active: python $(python -c 'import sys;print(sys.version.split()[0])'), OL_SIMPATH=$OL_SIMPATH  (sacla_off to leave)"
