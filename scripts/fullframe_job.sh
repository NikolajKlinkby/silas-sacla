#!/bin/bash
# Batch job (HPC): full-frame reduction of a scan (silas.offline.fullframe) WITHOUT a terminal.
#
#   full_frame_job(1234501)                                              # from the session: builds the qsub
#   qsub -v SCAN=1234501 ~/SILAS/scripts/fullframe_job.sh            # on the login node
#   qsub -v SCAN=1234501,PANELS=7:8,TT=1 ~/SILAS/scripts/fullframe_job.sh
#   qsub -l select=1:ncpus=16:mem=96gb -v SCAN=1234501 ...                # more memory (octal, long steps)
#   qstat -u $USER                                                         # is it running?
#   tail -f ~/SILAS/output/fullframe_job.log                         # its output
#
# Variables (qsub -v, comma-separated; values without commas):
#   SCAN            a run number of the scan, or -1 the newest (default)
#   PANELS          1-based panel numbers separated by ':' (default: set_ff_panels, else all)
#   ARRAY           MPCCD array name or alias (default: use_mpccd)
#   WORKERS         processes (default: the job's cores)
#   MAX_TAGS        shots per run, for a quick test
#   STEP_REFERENCE=1, PAIRS=1                      the options of analyse_scan
#   TT=1 / TT=0, BIN_PS, MIN_SHOTS                 time-tool binning (default: set_tt_bins)
#   CONFIG          experiment config file (default: config/hpc.json)
# Result: <output>/scans/scan_<id>/fullframe_<id>[_p<panels>].h5 (+ .json, _preview.png)
#PBS -N bt_fullframe
#PBS -q serial
#PBS -l select=1:ncpus=16:mem=64gb
#PBS -l walltime=24:00:00
#PBS -j oe
#PBS -o bt_fullframe.pbs.log

source "$HOME/SILAS/scripts/hpc_env.sh"
bt_env || exit 1
cd "$SILAS_HOME" || exit 1
mkdir -p output
[ -n "${CONFIG:-}" ] && export SILAS_CONFIG="$CONFIG"
args=(--scan "${SCAN:--1}" --workers "${WORKERS:-${NCPUS:-16}}")
[ -n "${PANELS:-}" ] && args+=(--panels "$PANELS")
[ -n "${ARRAY:-}" ] && args+=(--array "$ARRAY")
[ -n "${MAX_TAGS:-}" ] && args+=(--max-tags "$MAX_TAGS")
[ "${STEP_REFERENCE:-0}" = "1" ] && args+=(--step-reference)
[ "${PAIRS:-0}" = "1" ] && args+=(--pairs)
[ "${TT:-}" = "1" ] && args+=(--tt)
[ "${TT:-}" = "0" ] && args+=(--no-tt)
[ -n "${BIN_PS:-}" ] && args+=(--bin-ps "$BIN_PS")
[ -n "${MIN_SHOTS:-}" ] && args+=(--min-shots "$MIN_SHOTS")
echo "$(date)  full-frame reduction on $(hostname) (job ${PBS_JOBID:-?}): ${args[*]}" >> output/fullframe_job.log
python -u -m silas.offline.fullframe "${args[@]}" >> output/fullframe_job.log 2>&1
echo "$(date)  finished (exit $?)" >> output/fullframe_job.log
