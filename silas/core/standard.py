"""Standard detectors and settings of a beamline: point detectors with short aliases, the MPCCD
arrays and the scan settings. Used by silas.core.setup_config (setup_config(beamline=3)) and by
add_standard_detectors(), which adds what is missing to an existing config without touching
entries or aliases that are already there. Change aliases and units afterwards with set_alias /
set_unit as usual.

Only BL3 is known so far (BEAMLINES); another beamline gets the rough default only.
"""
import copy
from typing import Any, Dict, List, Optional, Tuple

from silas.core import mpccd, names
from silas.core.config import Config

# (true name, alias, unit). Not standard: the fluence motors (xfel_bl_3_st_2_motor_4 / _2) and
# T12 (xfel_bl_3_st_2_motor_6): add them per experiment with add_point_detector.
BL3_POINT_DETECTORS = [
    ("xfel_bl_3_lh1_shutter_1_open_valid/status", "optical_shutter", None),
    ("xfel_bl_3_shutter_1_open_valid/status", "laser_shutter", None),
    ("xfel_bl_3_st_2_pd_user_6_fitting_peak/voltage", "i0_1", "V"),
    ("xfel_bl_3_st_2_pd_user_7_fitting_peak/voltage", "i0_2", "V"),
    ("xfel_bl_3_st_2_motor_1/position", "delay_m", "pulse"),      # the pump-probe delay stage
    ("xfel_bl_3_st_1_motor_73/position", "delay_tt", "pulse"),    # delay of the timing tool
]  # type: List[Tuple[str, Optional[str], Optional[str]]]

# (common name of the panels, alias, number of panels, settings on top of mpccd.DEFAULTS)
BL3_MPCCD_ARRAYS = [
    ("MPCCD-8N0-3-002", "octal", 8, {
        "tag_step": 4,                                            # 15 Hz
        "corrections": {"threshold": True, "threshold_keV": 2.0},
        "display": {"cmap": "gray_r"},
        "full_frame": {"panels": None},
    }),
    ("MPCCD-2B1-M03-001", "dual", 2, {
        "tag_step": 2,                                            # 30 Hz
        "corrections": {"threshold": True, "threshold_keV": 2.0},
        "display": {"cmap": "gray_r"},
        "full_frame": {"panels": None},
    }),
]  # type: List[Tuple[str, Optional[str], int, Dict[str, Any]]]

BL3_DETECTORS = [
    ("OPAL-266931", "tt_camera", {
        "kind": "camera"
    }),
]

BEAMLINES = {
    3: {
        "point_detectors": BL3_POINT_DETECTORS,
        "mpccd_arrays": BL3_MPCCD_ARRAYS,
        "detectors": BL3_DETECTORS,
        "default_mpccd": "MPCCD-8N0-3-002",
        "scan": {
            "pump_detector": "optical_shutter",
            "pump_on_value": 1,
            "references": ["i0_1", "i0_2"],
            "sparse_hide": True,
            "sparse_min": 30,
        },
        "timetool": {
            "detector": "OPAL-266931",
            "roi": [1,1919,147,237],
        }
    },
}  # type: Dict[int, Dict[str, Any]]

# the BL3 lists under their old names (tests/probe_scan_info.py)
POINT_DETECTORS = BL3_POINT_DETECTORS
MPCCD_ARRAYS = [(k, a, n) for k, a, n, _ in BL3_MPCCD_ARRAYS]


def known(beamline: int) -> bool:
    return int(beamline) in BEAMLINES


def array_entry(key: str, alias: Optional[str], n_panels: int, overrides: Dict[str, Any],
                ev_per_absgain_adu: Optional[float] = None) -> Dict[str, Any]:
    """The config entry of an MPCCD array: mpccd.DEFAULTS, the beamline's settings on top."""
    entry = {"kind": mpccd.KIND, "panels": ["{}-{}".format(key, i) for i in range(1, n_panels + 1)]}  # type: Dict[str, Any]
    for k, v in mpccd.DEFAULTS.items():
        entry[k] = copy.deepcopy(v)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(entry.get(k), dict):
            entry[k].update(copy.deepcopy(v))
        else:
            entry[k] = copy.deepcopy(v)
    if ev_per_absgain_adu is not None:
        entry["calibration"]["ev_per_absgain_adu"] = float(ev_per_absgain_adu)
    if alias:
        entry["alias"] = alias
    return entry

def detector_entry(key: str, alias: Optional[str], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """The config entry of a detector: the beamline's settings on top."""
    entry = {"kind": overrides.get("kind", "unknown")}  # type: Dict[str, Any]
    for k, v in overrides.items():
        entry[k] = copy.deepcopy(v)
    if alias:
        entry["alias"] = alias
    return entry

def add_standard_detectors(cfg: Config, beamline: Optional[int] = None, quiet: bool = False) -> None:
    """Add the standard point detectors and MPCCD arrays of the beamline (default: the config's)
    that are not yet in the config. Existing entries are left as they are; an alias already used
    for something else is skipped (the entry is added without it)."""
    def say(msg: str) -> None:
        if not quiet:
            print(msg)

    bl = int(beamline if beamline is not None else cfg.beamline)
    if not known(bl):
        say("no standard detectors known for BL{} (known: {})".format(bl, ", ".join(str(b) for b in BEAMLINES)))
        return
    std = BEAMLINES[bl]
    existing = cfg.get("point_detectors", {})
    for true_name, alias, unit in std["point_detectors"]:
        if true_name in existing:
            say("  exists   {}".format(true_name))
            continue
        try:
            names.add_point_detector(cfg, true_name, alias=alias, unit=unit)
            say("  added    {:<18s} {}".format(alias or "-", true_name))
        except ValueError as err:
            names.add_point_detector(cfg, true_name, unit=unit)
            say("  added    {:<18s} {}   (alias skipped: {})".format("-", true_name, err))

    for key, alias, n, overrides in std["mpccd_arrays"]:
        if key in cfg.get("detectors", {}):
            say("  exists   {}".format(key))
            continue
        taken = names.resolve(cfg, alias) != alias if alias else False
        dets = dict(cfg.get("detectors", {}))
        dets[key] = array_entry(key, None, n, overrides, cfg.get("calibration.ev_per_absgain_adu", None))
        cfg.set("detectors", dets)
        if alias and not taken:
            names.set_alias(cfg, key, alias)
        say("  added    {:<18s} {} ({} panels){}".format(
            "-" if taken else (alias or "-"), key, n, "   (alias {!r} already used)".format(alias) if taken else ""))

    for key, alias, overrides in std["detectors"]:
        if key in cfg.get("detectors", {}):
            say("  exists   {}".format(key))
            continue
        taken = names.resolve(cfg, alias) != alias if alias else False
        dets = dict(cfg.get("detectors", {}))
        dets[key] = copy.deepcopy(overrides)
        cfg.set("detectors", dets)
        if alias and not taken:
            names.set_alias(cfg, key, alias)
        say("  added    {:<18s} {}{}".format(
            "-" if taken else (alias or "-"), key, "   (alias {!r} already used)".format(alias) if taken else ""))