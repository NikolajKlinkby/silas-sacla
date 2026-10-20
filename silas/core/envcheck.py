"""Check the Python environment of this computer: what works here?

    check_environment()                    # in the session
    python -m silas.core.envcheck       # without the session (e.g. if the session fails)
    python -m silas.core.envcheck --profile hpc

Reports host, Python, the profile's sys.path additions and environment variables,
the versions of the Python packages we use, and which SACLA libraries import and
from where. Importing a SACLA library only loads it; nothing is read from SACLA.
"""
import importlib
import os
import shutil
import socket
import sys
from typing import Any, Dict, List

PACKAGES = ["numpy", "pandas", "matplotlib", "h5py", "scipy", "mpi4py", "IPython"]
SACLA_MODULES = ["dbpy", "stpy", "olpy", "ippy", "tmapy", "udbpy"]
ENV_VARS = ["SACLA_TOOL_HOME", "BLSTAFF_CONF_DIR", "LLPARDIR", "OL_SIMPATH", "UDB_SERVER",
            "UDB_OFFLINE_SERVER", "DEFAULT_SIGNAL_BL3", "PBS_JOBID", "DISPLAY", "PYTHONPATH"]


def _version(mod: Any) -> str:
    for attr in ("__version__", "version"):
        v = getattr(mod, attr, None)
        if isinstance(v, str):
            return v
    getter = getattr(mod, "get_api_version", None)
    if getter is not None:
        try:
            return ".".join(str(x) for x in getter())
        except Exception:  # noqa: BLE001
            pass
    return "?"


def _probe(name: str) -> Dict[str, Any]:
    try:
        mod = importlib.import_module(name)
    except Exception as err:  # noqa: BLE001  ImportError, "prohibited at fep01", missing .so, ...
        return {"ok": False, "error": "{}: {}".format(type(err).__name__, str(err).splitlines()[0] if str(err) else "")}
    return {"ok": True, "version": _version(mod), "file": getattr(mod, "__file__", None) or "(namespace?)"}


def check(verbose: bool = True) -> Dict[str, Any]:
    """Collect (and print) the environment report; returns it as a dict."""
    from silas.core import bootstrap

    host = socket.gethostname()
    report = {
        "host": host,
        "login_node": host.startswith(("fep", "xhpcfep")),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "profile": bootstrap.STATE.get("profile"),
        "sys_path_added": list(bootstrap.STATE.get("inserted", [])),
        "sys_path_missing": list(bootstrap.STATE.get("missing", [])),
        "env_set_by_profile": list(bootstrap.STATE.get("env_set", [])),
        "env": {k: os.environ.get(k) for k in ENV_VARS},
        "mpirun": shutil.which(str(bootstrap.profile().get("mpirun", "mpirun"))) if bootstrap.STATE.get("profile") else None,
        "packages": {p: _probe(p) for p in PACKAGES},
        "sacla": {m: _probe(m) for m in SACLA_MODULES},
    }  # type: Dict[str, Any]
    if verbose:
        _print(report)
    return report


def _print(r: Dict[str, Any]) -> None:
    lines = []  # type: List[str]
    lines.append("host      {}{}".format(r["host"], "   <- LOGIN NODE: SACLA data libraries are refused here, "
                                                    "use a compute node (qsub -I)" if r["login_node"] else ""))
    lines.append("python    {}  ({})".format(r["python"], r["executable"]))
    lines.append("profile   {}".format(r["profile"]))
    for p in r["sys_path_added"]:
        lines.append("  sys.path+ {}".format(p))
    for p in r["sys_path_missing"]:
        lines.append("  MISSING   {}".format(p))
    if r["env_set_by_profile"]:
        lines.append("  env set by profile: {}".format(", ".join(r["env_set_by_profile"])))
    lines.append("mpirun    {}".format(r["mpirun"] or "not found"))
    lines.append("")
    lines.append("packages")
    for name, p in r["packages"].items():
        lines.append("  {:<12s} {}".format(name, p["version"] if p["ok"] else "--  " + p["error"]))
    lines.append("SACLA libraries")
    for name, p in r["sacla"].items():
        lines.append("  {:<12s} {}".format(name, "{:<8s} {}".format(p["version"], p["file"]) if p["ok"]
                                           else "--  " + p["error"]))
    lines.append("environment")
    for k, v in r["env"].items():
        if k == "PYTHONPATH" and v:
            lines.append("  PYTHONPATH")
            lines.extend("      " + x for x in v.split(os.pathsep))
        else:
            lines.append("  {:<20s} {}".format(k, v if v is not None else "-"))
    print("\n".join(lines))


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="check what works in this Python environment")
    ap.add_argument("--profile", help="profile to apply first (default: as the session would choose)")
    args = ap.parse_args()
    from silas.core import bootstrap  # importing the package already applied the default profile

    if args.profile and args.profile != bootstrap.STATE["profile"]:
        os.environ["SILAS_PROFILE"] = args.profile
        os.environ.pop("SILAS_CONFIG", None)
        bootstrap.STATE.update(applied=False, inserted=[], missing=[], env_set=[])
        bootstrap.apply()
    check()


if __name__ == "__main__":
    main()
