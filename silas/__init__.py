"""SILAS: online and offline analysis of pump-probe experiments at SACLA.

    silas.core     shared by online and offline (config, session, SACLA access, ...)
    silas.online   DAQ-USER-LAN online analysis
    silas.offline  HPC-LAN offline analysis
    silas.cli      the interactive session

Importing the package runs the bootstrap (profile -> sys.path) before anything
else, so a script only needs ``import silas`` as its first import.
"""
from silas.core import bootstrap as _bootstrap

_bootstrap.apply()

__version__ = "0.1.0"
