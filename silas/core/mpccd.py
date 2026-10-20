"""MPCCD arrays (multi-panel detectors such as the octal MPCCD) in the config.

An array is an entry of the config's "detectors" section with kind "MPCCD-array".
Its key is the common name of the panels, the panels are separate SACLA detector
IDs::

    "detectors": {
      "MPCCD-8-2-001": {
        "kind": "MPCCD-array",
        "alias": "octal",
        "panels": ["MPCCD-8-2-001-1", ..., "MPCCD-8-2-001-8"],
        "geometry":    {"manipulator_shift_um": 0.0, "panel_offsets": {}},
        "calibration": {"ev_per_absgain_adu": 3.65},
        "corrections": {"dark": true, "dark_file": null,
                        "threshold": false, "threshold_keV": 1.0},
        "display":     {"units": "keV", "cmap": "viridis", "log": false, ...},
        "rois":        {"bragg": {"rows": [316, 366], "cols": [229, 279]}},   # pixels from the origin
        "tag_step": 1
      }
    }

Missing settings fall back to DEFAULTS, so old config files keep working; settings
that no longer exist (corrections.gain, corrections.threshold_adu) are removed from
the file the first time the array is read. Energy calibration and threshold:
silas.core.energy.
Settings can be changed from the session (``set_mpccd``) or with keys in the
viewer; both write the config file, which the viewer watches.

Which array a command means when none is named: the config's "default_mpccd" (set by
use_mpccd(name), and by start_mpccd_viewer(name=...)), else the first array.
"""
import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from silas.core import names
from silas.core.config import Config

KIND = "MPCCD-array"
DEFAULT_KEY = "default_mpccd"     # top-level config key: the array used when none is named

DEFAULTS = {
    "geometry": {
        "manipulator_shift_um": 0.0,     # aperture opening (L of the SACLA assembly document)
        "panel_offsets": {},             # {"3": {"dx_um": 0, "dy_um": 0, "drot_deg": 0}} on top of SACLA's geometry
        "rounding": "floor",             # which neighbouring panel pixel an image pixel takes ("floor" ~ ippy)
        "sub_pixel": [0.0, 0.0],         # (row, column) offset before rounding; see tests/check_geometry_ippy.py
    },
    "calibration": {
        "ev_per_absgain_adu": 3.65,      # keV/ADU of a panel = mp_absgain * this / 1000 (measured 3.63-3.69)
    },
    "corrections": {
        "dark": True,                    # subtract the dark (if a dark file is set)
        "dark_file": None,               # .npz written by capture_dark()
        "threshold": False,              # set pixels below threshold_keV to 0
        "threshold_keV": 1.0,            # the one lower threshold, deposited energy (keV)
    },
    "display": {
        "units": "keV",                  # "keV" (all panels calibrated) or "ADU" (raw, per panel)
        "mode": "average",               # "average" or "latest"
        "cmap": "viridis",
        "log": False,
        "auto_limits": True,             # colour limits from percentiles of every image
        "percentiles": [1.0, 99.7],
        "vmin": None,                    # used when auto_limits is False
        "vmax": None,
        "outlines": True,
        "rois": True,                    # draw the ROIs (and their values)
        "update_s": 0.2,                 # seconds between display updates / worker reports
        "backend": None,                 # matplotlib backend, e.g. "TkAgg" (None: try until one works)
    },
    "rois": {},                          # {name: {"rows": [r0, r1], "cols": [c0, c1]}} from the origin, core.roi
    "tag_step": 1,                       # tag difference between consecutive shots (1 at 60 Hz, 2 at 30 Hz)
}  # type: Dict[str, Any]

CMAPS = ["viridis", "magma", "inferno", "cividis", "gray", "gray_r", "jet", "coolwarm"]


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def add_mpccd(cfg: Config, panels: Union[str, Sequence[str]], alias: Optional[str] = "octal",
              n_panels: int = 8) -> str:
    """Add an MPCCD array to the config's detectors. ``panels`` is the list of panel
    detector IDs or their common prefix: add_mpccd(cfg, "MPCCD-8-2-001") gives
    MPCCD-8-2-001-1 ... -8. Returns the array's name (key)."""
    if isinstance(panels, str):
        key = panels.rstrip("-")
        ids = ["{}-{}".format(key, i) for i in range(1, n_panels + 1)]
    else:
        ids = [str(p) for p in panels]
        key = _common_prefix(ids)
    if alias:
        owner = names.resolve(cfg, alias)
        if owner != alias and owner != key:
            raise ValueError("alias {!r} is already used for {!r}: choose another, e.g. "
                             "add_mpccd({!r}, alias=\"sim\")".format(alias, owner, key))
    entry = dict(cfg.get("detectors", {}).get(key) or {})
    entry.update(kind=KIND, panels=ids)
    for section in ("geometry", "calibration", "corrections", "display"):
        entry.setdefault(section, copy.deepcopy(DEFAULTS[section]))
    if "ev_per_absgain_adu" not in (cfg.get("detectors", {}).get(key) or {}).get("calibration", {}):
        # a new array: the config's energy calibration (setup_config), else DEFAULTS
        entry["calibration"]["ev_per_absgain_adu"] = float(
            cfg.get("calibration.ev_per_absgain_adu", DEFAULTS["calibration"]["ev_per_absgain_adu"]))
    entry.setdefault("tag_step", DEFAULTS["tag_step"])
    entry.setdefault("rois", {})
    dets = dict(cfg.get("detectors", {}))
    dets[key] = entry
    cfg.set("detectors", dets)
    if alias:
        names.set_alias(cfg, key, alias)
    return key


def _common_prefix(ids: Sequence[str]) -> str:
    if len(ids) == 1:
        return ids[0]
    prefix = ids[0]
    for s in ids[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
    return prefix.rstrip("-_") or "MPCCD-array"


def array_names(cfg: Config) -> List[str]:
    return [k for k, v in cfg.get("detectors", {}).items() if (v or {}).get("kind") == KIND]


def default_array(cfg: Config) -> Optional[str]:
    """The array used when none is named: "default_mpccd" if it is an array, else the first."""
    arrays = array_names(cfg)
    chosen = cfg.get(DEFAULT_KEY, None)
    if chosen and names.resolve(cfg, str(chosen)) in arrays:
        return names.resolve(cfg, str(chosen))
    return arrays[0] if arrays else None


def set_default_array(cfg: Config, name: str) -> str:
    """Make an array (key or alias) the default of all commands; returns its key."""
    key, _ = get_array(cfg, name)
    if cfg.get(DEFAULT_KEY, None) != key:
        cfg.set(DEFAULT_KEY, key)
    return key


def label(cfg: Config, key: str) -> str:
    """'KEY (alias)' of an array."""
    alias = (cfg.get("detectors", {}).get(key) or {}).get("alias")
    return "{} ({})".format(key, alias) if alias else key


def get_array(cfg: Config, name: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """(key, settings with defaults filled in) of an array (default: default_array())."""
    arrays = array_names(cfg)
    if not arrays:
        raise KeyError('no MPCCD array in the config: add_mpccd("MPCCD-8-2-001")')
    key = names.resolve(cfg, name) if name else str(default_array(cfg))
    if key not in arrays:
        known = ["{} (alias {})".format(a, cfg.get("detectors")[a]["alias"])
                 if cfg.get("detectors")[a].get("alias") else a for a in arrays]
        raise KeyError("{!r} is not an MPCCD array. Arrays: {}".format(name, ", ".join(known)))
    _upgrade(cfg, key)
    return key, _merge(DEFAULTS, cfg.get("detectors")[key])


LEGACY = {"corrections": ("gain", "threshold_adu")}  # settings that no longer exist


def _upgrade(cfg: Config, key: str) -> None:
    """Remove settings that no longer exist from the array's entry in the file."""
    entry = cfg.get("detectors")[key]
    if not any(k in (entry.get(sec) or {}) for sec, keys in LEGACY.items() for k in keys):
        return
    dets = copy.deepcopy(cfg.get("detectors"))
    for sec, keys in LEGACY.items():
        for k in keys:
            (dets[key].get(sec) or {}).pop(k, None)
    cfg.set("detectors", dets)
    print("{}: removed old settings {} (threshold is now corrections.threshold_keV)".format(
        key, ", ".join("{}.{}".format(s, k) for s, ks in LEGACY.items() for k in ks)))


def set_option(cfg: Config, key: str, value: Any, name: Optional[str] = None) -> None:
    """Change one setting of an array, e.g. set_option(cfg, "corrections.threshold_keV", 2.0)."""
    array, _ = get_array(cfg, name)
    dets = copy.deepcopy(cfg.get("detectors"))
    node = dets[array]
    parts = key.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value
    cfg.set("detectors", dets)


def show(cfg: Config, name: Optional[str] = None) -> None:
    """Print the settings of an array (with defaults)."""
    import json

    key, settings = get_array(cfg, name)
    print(key, json.dumps(settings, indent=2))
