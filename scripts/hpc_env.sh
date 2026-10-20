# SILAS shortcuts for the SACLA HPC. Source it (from ~/.bashrc, see install_hpc_env.sh):
#
#   source ~/SILAS/scripts/hpc_env.sh
#
#   bt_node [small|default|big|mpi] [-t hours] [-c cpus] [-m GB] [-q queue] [-X|-x] [-n]
#                                              interactive compute-node job (bt_node -h for details)
#   bt_env                                     load the SACLA Python (python/SACLA_python-3.7/offline)
#                                              and the SACLA command-line tools (SACLA_tool/offline)
#   bt_udb                                     bt_env + which UDB/TMA commands are found, auth file?
#   bt [start_session options]                 bt_env + cd ~/SILAS + start the session (profile hpc)
#   bt_check                                   bt_env + environment check (packages, SACLA libraries, paths)
#   bt_jupyter [big] [-t hours] [-c cpus] [-m GB]  login node: a Jupyter server as a batch job, prints the link
#   bt_jupyter -s                              stop it;  bt_jupyter --here: run one on this compute node
#
# On the HPC: python/SACLA_python-3.7/offline (Intel Python 3.7.9) is the only Python
# with numpy, pandas, matplotlib, h5py, scipy, mpi4py AND dbpy/stpy/olpy/ippy. It has no IPython
# and no tornado: install_hpc_env.sh --go installs IPython 7.34 and tornado 6.2 into
# ~/SILAS_py37, which the session finds by itself. Plots open in the laptop's browser
# (Tk windows from compute nodes hang). The SACLA data libraries only work on compute nodes.

export SILAS_HOME="${SILAS_HOME:-$HOME/SILAS}"
export SILAS_PROFILE="${SILAS_PROFILE:-hpc}"
SILAS_MODULE="python/SACLA_python-3.7/offline"
SILAS_TOOLS="SACLA_tool/offline"     # SACLA command-line tools (UDB, TMA, DataConvert4, ...)

_bt_on_login_node() {
  case "$(hostname)" in fep*|xhpcfep*) return 0 ;; *) return 1 ;; esac
}

_bt_node_help() {
  cat <<'HELP'
bt_node [preset] [options]      interactive compute-node job (run on the login node)

presets   small   1 h,  2 cpus,   8 GB   quick checks
          default 4 h,  6 cpus,  24 GB   (used when nothing is given; 6 workers)
          big     8 h, 16 cpus,  64 GB   offline analysis of a run
          mpi     4 h, 32 cpus, 128 GB   MPI jobs on one node
options   -t HOURS   walltime in hours          -c CPUS   number of cores
          -m GB      memory in GB               -q QUEUE  serial | long | smp
          -X / -x    force / suppress X windows (default: -X when a display is forwarded)
          -n         dry run: show the qsub command only
          -h         this help
example   bt_node big -t 12       bt_node -c 8 -m 32      bt_node small -n

queue limits (SACLA HPC):  serial  24 h, 1-48 cores, <=160 GB (default)
                           long    72 h, 1-12 cores, <=160 GB
                           smp     24 h, 1-56 cores, <=720 GB
                           (psmall/plarge are multi-node queues: use a PBS script, not bt_node)
HELP
}

bt_node() {
  local hours=4 ncpus=6 mem=24 queue=serial xmode=auto dry=0
  case "${1:-}" in
    small)   hours=1; ncpus=2;  mem=8;   shift ;;
    default) shift ;;
    big)     hours=8; ncpus=16; mem=64;  shift ;;
    mpi)     hours=4; ncpus=32; mem=128; shift ;;
  esac
  local OPTIND opt
  while getopts "t:c:m:q:Xxnh" opt; do
    case "$opt" in
      t) hours="$OPTARG" ;;
      c) ncpus="$OPTARG" ;;
      m) mem="$OPTARG" ;;
      q) queue="$OPTARG" ;;
      X) xmode=on ;;
      x) xmode=off ;;
      n) dry=1 ;;
      h) _bt_node_help; return 0 ;;
      *) _bt_node_help; return 2 ;;
    esac
  done
  # queue limits (Queue_Configuration.md)
  local max_h max_c max_m
  case "$queue" in
    serial) max_h=24; max_c=48;  max_m=160 ;;
    long)   max_h=72; max_c=12;  max_m=160 ;;
    smp)    max_h=24; max_c=56;  max_m=720 ;;
    *) echo "bt_node: queue '$queue' not for interactive one-node jobs (use serial, long or smp)"; return 2 ;;
  esac
  if [ "$hours" -gt "$max_h" ] || [ "$ncpus" -gt "$max_c" ] || [ "$mem" -gt "$max_m" ]; then
    echo "bt_node: queue $queue allows at most ${max_h} h, ${max_c} cores, ${max_m} GB (asked: ${hours} h, ${ncpus} cores, ${mem} GB)"
    return 2
  fi
  if ! _bt_on_login_node && [ "$dry" -eq 0 ]; then
    echo "bt_node: already on a compute node ($(hostname))"; return 1
  fi
  local opts=(-I)
  if [ "$xmode" = on ] || { [ "$xmode" = auto ] && [ -n "${DISPLAY:-}" ]; }; then
    opts+=(-X)
  elif [ "$xmode" = auto ]; then
    echo "(no DISPLAY: no windows in this job; log in with 'ssh -Y' for matplotlib windows)"
  fi
  opts+=(-l "walltime=${hours}:00:00" -l "select=1:ncpus=${ncpus}:mem=${mem}gb" -q "$queue")
  echo "qsub ${opts[*]}"
  [ "$dry" -eq 1 ] && return 0
  qsub "${opts[@]}"
}

bt_env() {
  if _bt_on_login_node; then
    echo "bt_env: on the login node $(hostname) the SACLA data libraries refuse to load: run bt_node first"
    return 1
  fi
  module load "$SILAS_MODULE" >/dev/null 2>&1 || { echo "bt_env: module load $SILAS_MODULE failed"; return 1; }
  # SACLA's command-line tools (UdbShowGroupList, ShowTMAUDBInfo, ...): a separate module
  module load "$SILAS_TOOLS" >/dev/null 2>&1 || echo "bt_env: module load $SILAS_TOOLS failed (SACLA tools not on PATH)"
}

bt_udb() {
  # Which UDB / TMA commands are found, and is there a UDB authentication file?
  bt_env || return 1
  local c missing=0
  for c in udb_MakeAuthFile udb_AddUser udb_Passwd UdbShowGroupList UdbShowInfo UdbShowUserList \
           ShowTMAUDBInfo CreateTMAID TimingMonitorAnalyzerCUI OutputTMACsvFromUDB; do
    if command -v "$c" >/dev/null 2>&1; then
      printf '  %-26s %s\n' "$c" "$(command -v "$c")"
    else
      printf '  %-26s NOT FOUND\n' "$c"; missing=1
    fi
  done
  if [ "$missing" -eq 1 ]; then
    echo "  searching /home/software/SACLA_tool for the missing ones:"
    for c in udb_MakeAuthFile udb_AddUser udb_Passwd TimingMonitorAnalyzerCUI; do
      command -v "$c" >/dev/null 2>&1 || ls -d /home/software/SACLA_tool/*/latest/bin/"$c" \
        /home/software/SACLA_tool/*/*/bin/"$c" 2>/dev/null | sed 's/^/    /'
    done
  fi
  if [ -f "$HOME/.udb/.udbaccess" ]; then
    echo "  authentication file: $HOME/.udb/.udbaccess"
  else
    echo "  authentication file: none (~/.udb/.udbaccess) -> udb_MakeAuthFile -u $USER -p"
  fi
}

bt() {
  bt_env || return 1
  cd "$SILAS_HOME" || return 1
  python start_session.py --profile "$SILAS_PROFILE" "$@"
}

bt_check() {
  bt_env || return 1
  (cd "$SILAS_HOME" && python -m silas.core.envcheck --profile "$SILAS_PROFILE")
}

if [ -n "${PS1:-}" ]; then
  if _bt_on_login_node; then
    echo "SILAS: bt_node to get a compute node, then bt to start the session"
  else
    echo "SILAS: bt to start the session, bt_check to check the environment"
  fi
fi

_bt_json() {
  # value of a "key": "value" line of a json file written with indent (no jq needed)
  sed -n "s/^ *\"$1\": \"\{0,1\}\([^\",]*\)\"\{0,1\},\{0,1\}$/\1/p" "$2" | head -n 1
}

bt_jupyter() {
  # A Jupyter server for SILAS notebooks (silas/cli/jupyter_server.py, scripts/jupyter_job.sh).
  #   login node:    bt_jupyter [small|default|big] [-t hours] [-c cpus] [-m GB] [-q queue] [-n]
  #                  submits the batch job (default 8 h, 6 cores, 24 GB), waits for it, prints tunnel + link
  #                  bt_jupyter -s   stops the running one (qdel)
  #   compute node:  bt_jupyter --here   runs the server here, in the foreground (Ctrl-C stops)
  local info="$SILAS_HOME/output/jupyter/current.json"
  if [ "${1:-}" = "--here" ]; then
    shift
    bt_env || return 1
    cd "$SILAS_HOME" || return 1
    python -u -m silas.cli.jupyter_server "$@"
    return
  fi
  if [ "${1:-}" = "-s" ]; then
    local job; job="$(_bt_json job "$info" 2>/dev/null)"
    [ -n "$job" ] || { echo "bt_jupyter: no running server known ($info)"; return 1; }
    echo "qdel $job"; qdel "$job"
    return
  fi
  if ! _bt_on_login_node; then
    echo "bt_jupyter: on a compute node use  bt_jupyter --here  (or run bt_jupyter on the login node)"
    return 1
  fi
  local hours=8 ncpus=6 mem=24 queue=serial dry=0
  case "${1:-}" in
    small)   hours=2; ncpus=2;  mem=8;  shift ;;
    default) shift ;;
    big)     hours=8; ncpus=16; mem=64; shift ;;
  esac
  local OPTIND opt
  while getopts "t:c:m:q:nh" opt; do
    case "$opt" in
      t) hours="$OPTARG" ;;
      c) ncpus="$OPTARG" ;;
      m) mem="$OPTARG" ;;
      q) queue="$OPTARG" ;;
      n) dry=1 ;;
      *) echo "bt_jupyter [small|default|big] [-t hours] [-c cpus] [-m GB] [-q queue] [-n];  bt_jupyter -s;  bt_jupyter --here"
         return 2 ;;
    esac
  done
  local cmd=(qsub -l "walltime=${hours}:00:00" -l "select=1:ncpus=${ncpus}:mem=${mem}gb" -q "$queue"
             "$SILAS_HOME/scripts/jupyter_job.sh")
  echo "${cmd[*]}"
  [ "$dry" -eq 1 ] && return 0
  local job; job="$("${cmd[@]}")" || { echo "bt_jupyter: qsub failed"; return 1; }
  echo "job $job submitted; waiting for the server (Ctrl-C stops waiting, not the job) ..."
  local i
  for i in $(seq 1 180); do
    if [ -f "$info" ] && [ "$(_bt_json job "$info")" = "$job" ]; then
      sleep 2
      echo "=============================================================================="
      echo "Jupyter is running on $(_bt_json host "$info") (job $job, ${hours} h). On your LAPTOP (VPN on):"
      echo
      echo "    bash scripts/hpc_jupyter.sh        (in your SILAS folder: tunnel + link)"
      echo
      echo "or in a new terminal (it prints nothing and stays open):"
      echo
      _bt_json tunnel "$info"
      echo
      echo "(without the sacla_HPC alias in ~/.ssh/config:)"
      _bt_json tunnel_no_alias "$info"
      echo
      echo "then open:"
      echo
      _bt_json url "$info"
      echo
      echo "Stop it with: bt_jupyter -s    (output: $SILAS_HOME/output/jupyter/jupyter_job.log)"
      echo "=============================================================================="
      return 0
    fi
    sleep 5
  done
  echo "bt_jupyter: the job has not started within 15 minutes (qstat -u $USER); its output will be in"
  echo "  $SILAS_HOME/output/jupyter/jupyter_job.log and the link in $info"
}
