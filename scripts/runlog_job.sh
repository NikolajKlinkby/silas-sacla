#!/bin/bash
# Batch job (HPC): the run log WITHOUT a terminal or laptop connection, kept up to date live.
#
#   qsub ~/SILAS/scripts/runlog_job.sh                         # catch up, then follow new runs
#   qsub -v LAST_RUN=1234999 ~/SILAS/scripts/runlog_job.sh     # ... and stop when run 1234999 is done
#   qsub -v ONCE=1 ~/SILAS/scripts/runlog_job.sh               # one pass up to the newest run, then stop
#   qsub -v REBUILD=1 ~/SILAS/scripts/runlog_job.sh            # start again from first_run
#   qsub -v FIRST_RUN=1234470,LAST_RUN=1234999 ...             # another range (e.g. an old beamtime)
#   qsub -q long -l walltime=72:00:00 ~/SILAS/scripts/runlog_job.sh   # the whole beamtime
#   qstat -u $USER                                             # is the job there?
#   run_logger_status()                                        # (session) heartbeat, progress, the log
#   tail -f ~/SILAS/output/runlog_job.log                      # its output
#
# Continues after the last run already in runlog.csv and writes the log, the motor snapshots and
# the scan table every 20 runs, also while catching up. first_run comes from the hpc config.
# Without LAST_RUN / ONCE it follows new runs (every POLL seconds, default 10) until the walltime
# ends or qdel; both stop it cleanly (the log is saved and the lock removed).
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
args=(--workers 6 --poll "${POLL:-10}")
[ "${ONCE:-0}" = "1" ] || args+=(--follow)
[ -n "${LAST_RUN:-}" ] && args+=(--last-run "$LAST_RUN")
[ -n "${FIRST_RUN:-}" ] && args+=(--first-run "$FIRST_RUN")
[ "${REBUILD:-0}" = "1" ] && args+=(--rebuild)
echo "$(date)  run logger on $(hostname), job ${PBS_JOBID:-}: ${args[*]}" >> output/runlog_job.log
# exec: the logger gets PBS's SIGTERM (qdel, walltime) itself and saves before it stops
exec python -u -m silas.offline.logger "${args[@]}" >> output/runlog_job.log 2>&1
