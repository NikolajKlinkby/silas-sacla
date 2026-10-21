"""Set up the experiment config of a profile (config/<profile>.json): a rough default, or the
standard setup of a beamline.

    setup_config()                                  # session: the rough default
    setup_config(beamline=3)                        # session: BL3 (detectors, MPCCD arrays, scan settings)
    setup_config(beamline=3, first_run=1234400, photon_energy_keV=9.9, output="pcmo_2026")

    bash scripts/setup_config.sh [--beamline 3] [--first-run N] [--photon-energy keV]
                                 [--output NAME|PATH] [--profile hpc] [--yes]
    python -m silas.core.setup_config ...        # the same

Both set: the beamline (default 3), the first run (asked; empty: none), the photon energy in keV
(asked; empty: from the run record), the output folder (asked: a folder name under
/UserData/<user>/, default tmp; or give a path) and the energy calibration
ev_per_absgain_adu = 3.65 for MPCCD arrays.
The beamline setup also adds the beamline's standard point detectors and MPCCD arrays (both MPCCD
arrays at BL3, the octal one is the default array) and its scan settings (pump detector,
references, sparse bins), from silas.core.standard.

Values not given are asked for at a terminal or in a notebook; without one (a batch job) or with
yes=True the defaults are taken. An existing config is replaced only after asking (yes=True: without
asking); a copy is kept as config/<profile>.json.<time>.bak. ROIs, dark files, the time-tool
background and calibration are experiment results: they are not set up here.
"""
import argparse
import copy
import datetime
import getpass
import json
import os
import shutil
import sys
from typing import Any, Dict, List, Optional

USERDATA = "/UserData"
DEFAULT_FOLDER = "tmp"


def _interactive() -> bool:
    """input() can be answered: a terminal, or a Jupyter notebook."""
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            return True
    except (AttributeError, ValueError):
        pass
    try:
        from silas.core import plotting

        return plotting.in_notebook()
    except Exception:  # noqa: BLE001
        return False


def _ask(question: str, ask: bool) -> str:
    if not ask:
        return ""
    try:
        return input(question).strip()
    except EOFError:
        return ""


def output_path(output: Optional[str]) -> str:
    """A folder name -> /UserData/<user>/<name>; a path (with / or ~) as it is."""
    name = (output or DEFAULT_FOLDER).strip()
    if os.sep in name or name.startswith("~"):
        return os.path.abspath(os.path.expanduser(name))
    return os.path.join(USERDATA, getpass.getuser(), name)


def build(profile: str, beamline: Optional[int] = 3, first_run: Optional[int] = None,
          photon_energy_keV: Optional[float] = None, output: Optional[str] = None,
          standard_setup: bool = False, ev_per_absgain_adu: float = 3.65) -> Dict[str, Any]:
    """The new config (nothing is written)."""
    from silas.core import config as config_mod
    from silas.core import standard

    data = config_mod.default_config(profile)
    bl = int(beamline if beamline is not None else config_mod.DEFAULT_BEAMLINE)
    data["beamline"] = bl
    data["first_run"] = int(first_run) if first_run is not None else None
    data["photon_energy_keV"] = float(photon_energy_keV) if photon_energy_keV is not None else None
    data["paths"] = {"output": output_path(output)}
    data["calibration"] = {"ev_per_absgain_adu": float(ev_per_absgain_adu)}
    if standard_setup and standard.known(bl):
        std = standard.BEAMLINES[bl]
        points = {}  # type: Dict[str, Any]
        if "point_detectors" in std:
            for true_name, alias, unit in std["point_detectors"]:
                entry = {}  # type: Dict[str, Any]
                if alias:
                    entry["alias"] = alias
                if unit:
                    entry["unit"] = unit
                points[true_name] = entry
            data["point_detectors"] = points
        if "mpccd_arrays" in std:
            data["detectors"] = {key: standard.array_entry(key, alias, n, overrides, ev_per_absgain_adu)
                                     for key, alias, n, overrides in std["mpccd_arrays"]}
        if "detectors" in std:
            data.setdefault("detectors", {}).update({
                    key: standard.detector_entry(key, alias, overrides)
                    for key, alias, overrides in std["detectors"]
                })
        data["default_mpccd"] = copy.deepcopy(std.get("default_mpccd", None))
        data["scan"] = copy.deepcopy(std.get("scan", {}))
        data["timetool"] = copy.deepcopy(std.get("timetool", {}))
    return data


def setup_config(beamline: Optional[int] = None, first_run: Optional[int] = None,
                 photon_energy_keV: Optional[float] = None, output: Optional[str] = None,
                 path: Optional[str] = None, profile: Optional[str] = None, yes: bool = False,
                 ask: Optional[bool] = None) -> Optional[str]:
    """Write a new config (see the module text). beamline=None: the rough default (beamline 3,
    no detectors); beamline=N: the standard setup of BL N. path: the config file (default: the
    profile's). Returns the path written, None if cancelled."""
    from silas.core import bootstrap, standard

    ask = _interactive() and not yes if ask is None else bool(ask)
    prof = profile or bootstrap.STATE.get("profile") or bootstrap.active_profile_name()
    target = os.path.abspath(os.path.expanduser(path)) if path else bootstrap.config_path(prof)
    standard_setup = beamline is not None
    bl = int(beamline) if beamline is not None else 3
    print("setting up {}: {}".format(target, "the standard setup of BL{}".format(bl) if standard_setup
                                       else "the rough default (no detectors)"))
    if standard_setup and not standard.known(bl):
        print("  no standard detectors known for BL{} (known: {}): the rough default for BL{}".format(
            bl, ", ".join(str(b) for b in standard.BEAMLINES), bl))

    if first_run is None:
        a = _ask("first run of the beamtime (empty: none, set it later with cfg.set('first_run', N)): ", ask)
        first_run = int(a) if a else None
    if photon_energy_keV is None:
        a = _ask("photon energy in keV (empty: from the run record of each run): ", ask)
        photon_energy_keV = float(a) if a else None
    if output is None:
        a = _ask("output folder under {} (empty: {}; or a full path): ".format(
            os.path.join(USERDATA, getpass.getuser()), DEFAULT_FOLDER), ask)
        output = a or None

    data = build(prof, bl, first_run, photon_energy_keV, output, standard_setup)
    parent = os.path.dirname(data["paths"]["output"].rstrip(os.sep))
    if parent and not os.path.isdir(parent):
        print("  NOTE: {} does not exist on this computer (fine for a config made for SACLA)".format(parent))

    if os.path.exists(target):
        old = bootstrap.read_json(target)
        print("  the existing config is replaced (detectors: {}, point detectors: {}, output: {})".format(
            len(old.get("detectors", {})), len(old.get("point_detectors", {})), (old.get("paths") or {}).get("output")))
        if not yes:
            if not ask:
                print("  not replaced: give yes=True (--yes) to replace it without asking")
                return None
            if _ask("  replace it? [y/N] ", ask).lower() not in ("y", "yes"):
                print("  cancelled, nothing changed")
                return None
        backup = "{}.{}.bak".format(target, datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(target, backup)
        print("  copy of the old one: {}".format(backup))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    bootstrap.write_json(target, data)
    _summary(target, data)
    return target


def _summary(target: str, data: Dict[str, Any]) -> None:
    lines = ["written: {}".format(target),
             "  beamline {}, first run {}, photon energy {}".format(
                 data["beamline"], data["first_run"] if data["first_run"] is not None else "not set",
                 "{:g} keV".format(data["photon_energy_keV"]) if data["photon_energy_keV"] is not None
                 else "from the run record"),
             "  output {}".format(data["paths"]["output"]),
             "  ev_per_absgain_adu {:g}".format(data["calibration"]["ev_per_absgain_adu"])]
    if data["detectors"]:
        lines.append("  MPCCD arrays: {} (default {})".format(", ".join(
            "{} ({})".format(v.get("alias", "-"), k) for k, v in data["detectors"].items()), data.get("default_mpccd")))
    if data["point_detectors"]:
        lines.append("  point detectors: {}".format(", ".join(
            v.get("alias", k) for k, v in data["point_detectors"].items())))
    if "scan" in data:
        lines.append("  scan: {}".format(json.dumps(data["scan"])))
    print("\n".join(lines))


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="set up the experiment config of a profile (see the module text)")
    ap.add_argument("--beamline", type=int, help="the standard setup of this beamline (3); without: the rough default")
    ap.add_argument("--first-run", type=int, help="first run of the beamtime (default: asked)")
    ap.add_argument("--photon-energy", type=float, help="photon energy in keV (default: asked)")
    ap.add_argument("--output", help="output folder name under /UserData/<user>/, or a path (default: asked, tmp)")
    ap.add_argument("--profile", help="profile whose config is set up (default: as the session chooses)")
    ap.add_argument("--config", help="the config file to write (default: config/<profile>.json)")
    ap.add_argument("--yes", action="store_true", help="ask nothing: defaults, and replace an existing config")
    a = ap.parse_args(argv)
    setup_config(beamline=a.beamline, first_run=a.first_run, photon_energy_keV=a.photon_energy, output=a.output,
                 path=a.config, profile=a.profile, yes=a.yes)


if __name__ == "__main__":
    main()
