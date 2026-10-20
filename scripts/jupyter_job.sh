#!/bin/bash
# Batch job (HPC): a Jupyter server (JupyterLab) for SILAS notebooks on a compute node.
#
#   bt_jupyter                                        # on the login node: submits this, waits, prints the link
#   qsub ~/SILAS/scripts/jupyter_job.sh          # the same by hand (8 h, 6 cores, 24 GB)
#   qsub -l walltime=12:00:00 -l select=1:ncpus=16:mem=64gb ~/SILAS/scripts/jupyter_job.sh
#   qsub -v JUPYTER_ARGS=--classic ...                 # the classic notebook instead of JupyterLab
#   qdel <job>                                         # stop it (bt_jupyter -s does the same)
#
# The connection (node, port, token) goes to ~/SILAS/output/jupyter/current.json, the
# output to ~/SILAS/output/jupyter/jupyter_job.log. On the laptop, in the SILAS
# folder: bash scripts/hpc_jupyter.sh  (tunnel + link). See silas/cli/jupyter_server.py.
#PBS -N bt_jupyter
#PBS -q serial
#PBS -l select=1:ncpus=6:mem=24gb
#PBS -l walltime=08:00:00
#PBS -j oe
#PBS -o bt_jupyter.pbs.log

source "$HOME/SILAS/scripts/hpc_env.sh"
bt_env || exit 1
cd "$SILAS_HOME" || exit 1
mkdir -p output/jupyter
echo "$(date)  Jupyter server on $(hostname) (job ${PBS_JOBID:-?}) ${JUPYTER_ARGS:-}" >> output/jupyter/jupyter_job.log
# shellcheck disable=SC2086
python -u -m silas.cli.jupyter_server ${JUPYTER_ARGS:-} >> output/jupyter/jupyter_job.log 2>&1
echo "$(date)  finished (exit $?)" >> output/jupyter/jupyter_job.log
