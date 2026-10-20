#!/bin/bash
# LAPTOP: connect to the Jupyter server of SILAS on the SACLA HPC (VPN on).
#
#   bash scripts/hpc_jupyter.sh                  # the running server: open the tunnel, print + open the link
#   bash scripts/hpc_jupyter.sh --start [opts]   # first start one (batch job; opts as bt_jupyter: big, -t 12, ...)
#   bash scripts/hpc_jupyter.sh --stop           # stop the running server (qdel)
#   bash scripts/hpc_jupyter.sh --host sacla_HPC # the ssh alias of the HPC login node (default)
#   bash scripts/hpc_jupyter.sh --user <name>    # without the alias: <name>@xhpcfep.hpc.spring8.or.jp
#
# The tunnel runs in this terminal until Ctrl-C (the server keeps running; connect again any
# time). The server writes its node, port and token to ~/SILAS/output/jupyter/current.json
# on the HPC (silas/cli/jupyter_server.py); this script reads it over ssh.

set -u
HOST="sacla_HPC"
START=0
STOP=0
OPTS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --start) START=1 ;;
    --stop)  STOP=1 ;;
    --host)  HOST="$2"; shift ;;
    --user)  HOST="$2@${SILAS_HPC_HOST:-xhpcfep.hpc.spring8.or.jp}"; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) OPTS+=("$1") ;;
  esac
  shift
done

remote() {   # a command on the login node with the SILAS shortcuts loaded
  ssh -o ConnectTimeout=15 "$HOST" "bash -lc 'source ~/SILAS/scripts/hpc_env.sh >/dev/null 2>&1; $*'"
}

if ! ssh -o ConnectTimeout=15 "$HOST" true; then
  echo "cannot reach $HOST: VPN on? key login set up? (ssh $HOST hostname)"; exit 1
fi
if [ "$STOP" -eq 1 ]; then
  remote bt_jupyter -s
  exit $?
fi
if [ "$START" -eq 1 ]; then
  remote bt_jupyter "${OPTS[@]}" || exit 1
fi

json="$(ssh "$HOST" 'cat ~/SILAS/output/jupyter/current.json' 2>/dev/null)"
if [ -z "$json" ]; then
  echo "no Jupyter server known on $HOST: start one with  bash scripts/hpc_jupyter.sh --start"
  exit 1
fi
field() { printf '%s' "$json" | python3 -c 'import json, sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"; }
node="$(field host)"; port="$(field port)"; user="$(field user)"; url="$(field url)"; job="$(field job)"
if [ -n "$job" ] && ! ssh "$HOST" "bash -lc 'qstat $job'" >/dev/null 2>&1; then
  echo "the last server (job $job on $node) is no longer running: start one with  bash scripts/hpc_jupyter.sh --start"
  exit 1
fi
echo "Jupyter on $node (job ${job:--}), port $port"
echo "tunnel: ssh -N -L $port:127.0.0.1:$port -J $HOST $user@$node   (Ctrl-C closes it; the server keeps running)"
echo
echo "open:   $url"
echo
( sleep 4; command -v xdg-open >/dev/null 2>&1 && xdg-open "$url" >/dev/null 2>&1 ) &
exec ssh -N -L "$port:127.0.0.1:$port" -J "$HOST" "$user@$node"
