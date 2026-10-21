#!/bin/bash
# Batch job (HPC): the time-tool log (the edge of every tag of every run) WITHOUT a terminal.
#
#   qsub ~/SILAS/scripts/tt_logger_job.sh                          # from first_run on, follows new runs
#   qsub -v LAST_RUN=1234551 ~/SILAS/scripts/tt_logger_job.sh       # ... and stops after run 1234551
#   qsub -v FIRST_RUN=1234501,LAST_RUN=1234551 ~/SILAS/scripts/tt_logger_job.sh
#   qsub -v REBUILD=1 ...                                                # forget the index, do every run again
#   qstat -u $USER                                                       # is it running?
#   tail -f ~/SILAS/output/tt_logger_job.log                       # its output
#
# Runs already done with the current settings are not done again (they are in
# <output>/timetool/edges/index.csv). Uses the hpc config (time-tool settings, background,
# first_run). Without LAST_RUN it follows new runs until the walltime ends or qdel.
#PBS -N bt_ttlog
#PBS -q serial
#PBS -l select=1:ncpus=6:mem=16gb
#PBS -l walltime=24:00:00
#PBS -j oe
#PBS -o bt_ttlog.pbs.log

source "$HOME/SILAS/scripts/hpc_env.sh"
bt_env || exit 1
cd "$SILAS_HOME" || exit 1
mkdir -p output
args=(--workers 6 --follow)
[ -n "${LAST_RUN:-}" ] && args+=(--last-run "$LAST_RUN")
[ -n "${FIRST_RUN:-}" ] && args+=(--first-run "$FIRST_RUN")
[ "${REBUILD:-0}" = "1" ] && args+=(--rebuild)
echo "$(date)  time-tool logger on $(hostname): ${args[*]}" >> output/tt_logger_job.log
python -u -m silas.offline.tt_logger "${args[@]}" >> output/tt_logger_job.log 2>&1
echo "$(date)  finished (exit $?)" >> output/tt_logger_job.log
