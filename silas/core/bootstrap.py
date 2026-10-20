"""Session bootstrap: choose the profile and put its paths on sys.path.

STDLIB ONLY. This module runs before numpy or any SACLA library is imported,
because the profile's paths decide *which* numpy / SACLA modules get imported.

Every entry point calls ``apply()`` first:
  * the interactive session (start_session.py)
  * any script started from the session or from another terminal
    (``import silas`` calls apply() automatically)

Files (in SILAS/config/):
  profiles.json     profile definitions (sys_paths, optional_sys_paths, env, mode, terminal, mpirun)
                    + the default profile; OPTIONAL_PATHS (e.g. ~/SILAS_ipython) are tried for all
  <profile>.json    the experiment config of that profile (see silas.core.config)

Profile and config file are chosen in this order:
  1. environment variables SILAS_PROFILE / SILAS_CONFIG
     (set by start_session.py and inherited by every script it launches)
  2. "default" in profiles.json, and config/<profile>.json
"""
import copy
import json
import os
import sys
import warnings
from typing import Any, Dict, List

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""The SILAS folder (contains the silas package)."""

CONFIG_DIR = os.path.join(PACKAGE_ROOT, "config")
PROFILES_FILE = os.path.join(CONFIG_DIR, "profiles.json")

# Paths used at the beamline and on the HPC (Intel Python 3.7 + SACLA API).
_SACLA_API_PATH = "/home/software/SACLA_tool/DataAccessUserAPI/latest/python/lib"
_SACLA_SITE_PACKAGES = "/home/software/opt/intel/oneapi/intelpython/python3.7/lib/python3.7/site-packages"

# What `module load python/SACLA_python-3.7/offline` puts on PYTHONPATH (HPC survey):
# every SACLA library has its own folder (dbpy and stpy in separate subfolders).
_ST = "/home/software/SACLA_tool"
_HPC_PATHS = [
    _ST + "/DataAccessUserAPI/latest/python/lib/dbpy",
    _ST + "/DataAccessUserAPI/latest/python/lib/stpy",
    _ST + "/OnlineUserAPI/latest/python/lib",
    _ST + "/ImageProcessingUserAPI/latest/python/lib",
    _ST + "/TMAUserAPI/latest/python/lib",
    _ST + "/UserDatabaseAPI/latest/python/lib",
    _ST + "/CITIUSDataAccessUserAPI/latest/python/lib",
    _SACLA_SITE_PACKAGES,
]
# ... and the environment variables of module SACLA_env_offline (set only if not already set)
_HPC_ENV = {
    "SACLA_TOOL_HOME": _ST,
    "BLSTAFF_CONF_DIR": _ST + "/.config",
    "LLPARDIR": _ST + "/.config/apiconfig",
    "DEFAULT_SIGNAL_BL1": "xfel_bl_1_shutter_1_status/valid",
    "DEFAULT_SIGNAL_BL2": "xfel_bl_2_shutter_1_status/valid",
    "DEFAULT_SIGNAL_BL3": "xfel_bl_3_shutter_1_status/valid",
    "UDB_SERVER": "xuonludb01.user.xfel.cntl.local",
    "UDB_OFFLINE_SERVER": "xhoffudb01.hpc.spring8.or.jp",
}

OPTIONAL_PATHS = ["~/SILAS_py37", "~/SILAS_ipython"]
"""Added to sys.path for every profile IF they exist, silently skipped otherwise:
~/SILAS_py37 = IPython 7.34 and tornado 6.2 for Python 3.7 (scripts/install_hpc_packages.sh),
which the SACLA Python on the HPC does not have (~/SILAS_ipython: the older IPython-only
install). A profile can add its own with "optional_sys_paths"."""

DEFAULT_PROFILES = {
    "local": {
        "description": "Laptop with sacla_local_api: nothing SACLA-dependent runs, scripts are only smoke-tested",
        "mode": "smoke",
        "sys_paths": [],
        "terminal": ["gnome-terminal", "--"],
        "mpirun": "mpirun",
    },
    "beamline": {
        "description": "DAQ-USER-LAN at the beamline: online analysis, everything runs",
        "mode": "live",
        # first entry = highest priority (ends up first on sys.path). The two paths of the SACLA
        # Python on the analysis computers, plus the per-library folders found on the HPC (dbpy and stpy live in
        # lib/dbpy and lib/stpy); paths that do not exist on the anapc are skipped.
        # No "env" here: the online module's OL_SIMPATH would switch olpy to SIMULATION mode.
        "sys_paths": [_SACLA_SITE_PACKAGES, _SACLA_API_PATH] + [p for p in _HPC_PATHS if p != _SACLA_SITE_PACKAGES],
        "terminal": ["xterm", "-e"],
        "mpirun": "mpirun",
    },
    "hpc": {
        "description": "HPC-LAN compute node: offline analysis, everything runs",
        "mode": "live",
        "sys_paths": _HPC_PATHS,
        "env": _HPC_ENV,
        "terminal": None,
        "mpirun": "mpirun",
    },
}

VALID_MODES = ("smoke", "live")

# Filled in by apply(); read by the session banner and the rest of silas.
STATE = {
    "applied": False,
    "profile": None,
    "config_path": None,
    "inserted": [],
    "missing": [],
    "env_set": [],
}  # type: Dict[str, Any]


def write_json(path: str, data: Any) -> None:
    """Write JSON atomically (a reader never sees a half-written file)."""
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def read_json(path: str) -> Dict[str, Any]:
    with open(path, "r") as fh:
        return json.load(fh)


def load_profiles() -> Dict[str, Any]:
    """Content of profiles.json ({"default": ..., "profiles": {...}}); created with
    the built-in defaults if it does not exist."""
    if not os.path.exists(PROFILES_FILE):
        write_json(PROFILES_FILE, {"default": "local", "profiles": copy.deepcopy(DEFAULT_PROFILES)})
    return read_json(PROFILES_FILE)


def save_profiles(data: Dict[str, Any]) -> None:
    write_json(PROFILES_FILE, data)


def active_profile_name() -> str:
    return os.environ.get("SILAS_PROFILE") or load_profiles().get("default") or "local"


def profile(name: str = "") -> Dict[str, Any]:
    """Definition of profile ``name`` (default: the active profile)."""
    name = name or STATE["profile"] or active_profile_name()
    profiles = load_profiles()["profiles"]
    if name not in profiles:
        raise ValueError("unknown profile {!r} (known: {})".format(name, ", ".join(sorted(profiles))))
    return profiles[name]


def config_path(profile_name: str = "") -> str:
    """Experiment config in use: $SILAS_CONFIG or config/<profile>.json."""
    env = os.environ.get("SILAS_CONFIG")
    if env and not profile_name:
        return os.path.abspath(os.path.expanduser(env))
    name = profile_name or STATE["profile"] or active_profile_name()
    return os.path.join(CONFIG_DIR, name + ".json")


def insert_paths(paths: List[str], optional: bool = False) -> None:
    """Put ``paths`` at the front of sys.path, first entry first. Skips paths
    already on sys.path and records (does not insert) paths that do not exist;
    optional=True: paths that do not exist are skipped silently."""
    for p in reversed(paths):
        p = os.path.expanduser(p)
        if not os.path.isdir(p):
            if not optional and p not in STATE["missing"]:
                STATE["missing"].append(p)
            continue
        if p in sys.path:
            continue
        sys.path.insert(0, p)
        STATE["inserted"].insert(0, p)


def apply() -> Dict[str, Any]:
    """Select the profile and insert its sys.path entries. Safe to call repeatedly:
    only the first call does anything. Returns STATE."""
    if STATE["applied"]:
        return STATE
    name = active_profile_name()
    prof = profile(name)
    insert_paths(prof.get("sys_paths", []))
    insert_paths(list(prof.get("optional_sys_paths", [])) + OPTIONAL_PATHS, optional=True)
    for key, value in (prof.get("env") or {}).items():  # values from `module load` win
        if key not in os.environ:
            os.environ[key] = str(value)
            STATE["env_set"].append(key)
    if PACKAGE_ROOT not in sys.path:  # so scripts anywhere can `import silas`
        sys.path.append(PACKAGE_ROOT)

    STATE.update(applied=True, profile=name)
    STATE["config_path"] = config_path()
    # children (scripts launched from the session) inherit the same choice
    os.environ["SILAS_PROFILE"] = name
    os.environ["SILAS_CONFIG"] = STATE["config_path"]
    if STATE["missing"]:
        warnings.warn("profile {!r}: path(s) not found, not added to sys.path: {}".format(
            name, ", ".join(STATE["missing"])), stacklevel=2)
    return STATE
