"""Per-shot corrections of MPCCD panel images (shared by online and offline).

Order, applied to every shot before averaging (the threshold is not linear):
  1. dark subtraction   raw - dark (dark = mean of shots without X-rays, raw ADU)
  2. threshold          pixels below the threshold -> 0. The threshold is set in keV
                        (corrections.threshold_keV) and converted once to ADU for every
                        panel (silas.core.energy), so the data are compared as they are.

The result stays in ADU of each panel: no per-shot rescaling. Conversion to keV
(keV/ADU per panel from mp_absgain) is done where needed, e.g. once per display update.

Dark files are .npz files with the panel IDs, the mean raw images and the
number of shots; written by capture_dark() (online) and later from dark runs
(offline).
"""
import datetime
import json
import os
from typing import Any, Dict, List, Optional, Sequence

import numpy as np


def save_dark(path: str, det_ids: Sequence[str], dark: np.ndarray, n_shots: int,
              meta: Optional[Dict[str, Any]] = None) -> str:
    """Write a dark file (dark: array (n_panels, rows, cols) of mean raw ADU)."""
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    info = dict(meta or {}, n_shots=int(n_shots),
                created=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, det_ids=np.array(list(det_ids)), dark=np.asarray(dark, np.float32),
                        meta=json.dumps(info))
    os.replace(tmp, path)
    return path


def load_dark(path: str, det_ids: Sequence[str]) -> np.ndarray:
    """Dark images in the order of ``det_ids`` (error if a panel is missing)."""
    with np.load(os.path.expanduser(path)) as d:
        ids = [str(x) for x in d["det_ids"]]
        dark = d["dark"]
    missing = [i for i in det_ids if i not in ids]
    if missing:
        raise ValueError("dark file {} has no data for {}".format(path, ", ".join(missing)))
    return np.stack([dark[ids.index(i)] for i in det_ids]).astype(np.float32)


def settings_id(settings: Dict[str, Any], kev_per_adu: Any = None) -> str:
    """Stable text id of correction settings (to tell averages with different settings apart)."""
    data = dict(settings)
    if kev_per_adu is not None:
        data["_kev_per_adu"] = [round(float(k), 7) for k in kev_per_adu]
    return json.dumps(data, sort_keys=True)


class Corrections(object):
    """Apply dark and threshold to a stack of panel images (result in ADU of each panel)."""

    def __init__(self, settings: Dict[str, Any], kev_per_adu: Any,
                 det_ids: Sequence[str]) -> None:
        from silas.core import energy

        self.settings = dict(settings)
        self.kev_per_adu = np.asarray(kev_per_adu, np.float32)
        self.id = settings_id(self.settings, self.kev_per_adu)
        self.threshold_keV = float(settings.get("threshold_keV", 0.0))
        self.threshold_adu = energy.threshold_adu(self.threshold_keV, self.kev_per_adu)  # (n, 1, 1)
        self.dark = None  # type: Optional[np.ndarray]
        self.messages = []  # type: List[str]
        if not np.all(np.isfinite(self.threshold_adu)):
            self.messages.append("some panels have no mp_absgain: their threshold is not applied")
            self.threshold_adu = np.where(np.isfinite(self.threshold_adu), self.threshold_adu,
                                          -np.inf).astype(np.float32)
        if settings.get("dark") and settings.get("dark_file"):
            try:
                self.dark = load_dark(settings["dark_file"], det_ids)
            except (OSError, ValueError, KeyError) as err:
                self.messages.append("dark not used: {}".format(err))

    def apply(self, panels: Sequence[np.ndarray]) -> np.ndarray:
        """Corrected images as one float32 array (n_panels, rows, cols), ADU of each panel."""
        x = np.array(panels, dtype=np.float32)  # copy
        if self.dark is not None:
            x -= self.dark
        if self.settings.get("threshold"):
            x[x < self.threshold_adu] = 0.0
        return x

    def describe(self) -> str:
        parts = []
        parts.append("dark" if self.dark is not None else "no dark")
        if self.settings.get("threshold"):
            parts.append("threshold {:g} keV".format(self.threshold_keV))
        else:
            parts.append("no threshold")
        return ", ".join(parts)
