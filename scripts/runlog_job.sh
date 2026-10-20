#!/bin/bash
# Batch job (HPC): build / complete the run log WITHOUT a terminal or laptop connection.
#
#   qsub -v LAST_RUN=1234999 ~/SILAS/scripts/runlog_job.sh      # from the login node
#   qstat -u $USER                                                    # is it running?
#   tail -f ~/SILAS/output/runlog_job.log                       # its output
#
# Continues after the last run already in runlog.csv (the logger saves every 50 runs), up to
# LAST_RUN (default: the newest run). first_run comes from the hpc config. To start again
# from first_run instead, add REBUILD=1: qsub -v LAST_RUN=...,REBUILD=1 ...
#PBS -N bt_runlog
#PBS -q serial
#PBS -l select=1:ncpus=6:mem=16gb
#PBS -l walltime=24:00:00
#PBS -j oe
#PBS -o bt_runlog.pbs.log

source "$HOME/SILAS/scripts/hpc_env.sh"
bt_env || exit 1
cd "$SILAS_HOME" || exit 1
mkdir -p output
args=()
[ -n "${LAST_RUN:-}" ] && args+=(--last-run "$LAST_RUN")
[ "${REBUILD:-0}" = "1" ] && args+=(--rebuild)
echo "$(date)  run logger on $(hostname): ${args[*]}" >> output/runlog_job.log
python -u -m silas.offline.logger --workers 6 "${args[@]}" >> output/runlog_job.log 2>&1
echo "$(date)  finished (exit $?)" >> output/runlog_job.log
