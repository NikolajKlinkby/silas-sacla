"""MPI only when the script was really started with mpirun / mpiexec.

Importing mpi4py starts MPI. In a single process that is not needed, and on the HPC it
can even hang: Intel MPI tries to talk to the PBS job ("Invalid character . in PBS_JOBID")
and stalls. So world() imports mpi4py only if the launcher's environment variables are
there; otherwise the caller runs as one process.
"""
import os
from typing import Any, Tuple

LAUNCHER_VARS = ("PMI_RANK", "PMI_SIZE", "PMI_FD", "PMIX_RANK", "HYDI_CONTROL_FD", "MPI_LOCALNRANKS",
                 "OMPI_COMM_WORLD_SIZE", "OMPI_COMM_WORLD_RANK")
"""Set by mpirun / mpiexec (MPICH / Intel MPI hydra, PMIx, Open MPI) in every process."""


def launched_by_mpirun() -> bool:
    return any(v in os.environ for v in LAUNCHER_VARS)


def world() -> Tuple[Any, int, int, Any]:
    """(comm, rank, size, MPI module) under mpirun; (None, 0, 1, None) otherwise or without mpi4py."""
    if not launched_by_mpirun():
        return None, 0, 1, None
    try:
        from mpi4py import MPI
    except ImportError:
        return None, 0, 1, None
    comm = MPI.COMM_WORLD
    return comm, comm.Get_rank(), comm.Get_size(), MPI
