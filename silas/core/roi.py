"""Regions of interest (ROIs) of an MPCCD array.

A ROI is a rectangle in the ASSEMBLED image, given in pixels RELATIVE TO THE DETECTOR
ORIGIN (X = Y = 0), as the MPCCD viewer shows them under the mouse
("pixel (row 316, col 229) from origin"): rows count down, columns to the right, both can
be negative. Bounds are inclusive. Being relative to the origin, a ROI stays at the same
place on the detector when the image size changes (aperture shift, panel tuning).
At most MAX_ROIS per array (the scan viewer switches them with the number keys).
ROIs are stored per MPCCD array in the config::

    "rois": {"bragg_002": {"rows": [316, 366], "cols": [229, 279]}, ...}

and edited from the session (add_roi, remove_roi, ...); a running viewer draws them
within a second.

For the analysis a ROI is turned into panel pixels once (RoiMap): for every panel it
covers, the flat indices of the panel pixels inside the rectangle. Panels covered by
no ROI do not have to be read at all, and per shot only the ROI pixels are corrected
(dark, threshold) and summed. A panel pixel that is shown in two image pixels (the
panels are rotated by not exactly 90 degrees) is counted once.
"""
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from silas.core import mpccd
from silas.core.config import Config

MAX_ROIS = 10
LARGEST_PIXEL = 1200   # the octal MPCCD reaches about +-1080 pixels from the origin


# ====================================================================== config
def get_rois(cfg: Config, name: Optional[str] = None) -> Dict[str, Dict[str, List[int]]]:
    """{roi name: {"rows": [r0, r1], "cols": [c0, c1]}} of an MPCCD array (default: the first)."""
    _, settings = mpccd.get_array(cfg, name)
    return dict(settings.get("rois") or {})


def _check(rows: Sequence[int], cols: Sequence[int]) -> Tuple[List[int], List[int]]:
    if len(rows) != 2 or len(cols) != 2:
        raise ValueError("rows and cols are (first, last) pixels from the origin, e.g. rows=(316, 366)")
    r = sorted(int(v) for v in rows)
    c = sorted(int(v) for v in cols)
    return r, c


def add_roi(cfg: Config, roi: str, rows: Sequence[int], cols: Sequence[int],
            name: Optional[str] = None) -> None:
    """Add (or replace) ROI ``roi`` of an MPCCD array: rows r0..r1 and columns c0..c1
    (inclusive) in pixels from the detector origin, as shown by the viewer under the mouse."""
    if not roi or not isinstance(roi, str):
        raise ValueError("a ROI needs a name")
    r, c = _check(rows, cols)
    rois = get_rois(cfg, name)
    if roi not in rois and len(rois) >= MAX_ROIS:
        raise ValueError("at most {} ROIs: remove one first (remove_roi)".format(MAX_ROIS))
    if max(abs(v) for v in r + c) > LARGEST_PIXEL:
        print("WARNING: ROI {!r} reaches {} pixels from the detector origin, but the octal MPCCD only reaches "
              "about +-1080. ROIs are counted FROM THE ORIGIN (mouse read-out 'pixel (row, col) from origin'), "
              "not from the image corner.".format(roi, max(abs(v) for v in r + c)))
    rois[roi] = {"rows": r, "cols": c}
    mpccd.set_option(cfg, "rois", rois, name)


def remove_roi(cfg: Config, roi: str, name: Optional[str] = None) -> None:
    rois = get_rois(cfg, name)
    if roi not in rois:
        raise KeyError("no ROI {!r} (ROIs: {})".format(roi, ", ".join(rois) or "none"))
    del rois[roi]
    mpccd.set_option(cfg, "rois", rois, name)


def clear_rois(cfg: Config, name: Optional[str] = None) -> None:
    mpccd.set_option(cfg, "rois", {}, name)


def describe(rois: Dict[str, Any], roi_map: Optional["RoiMap"] = None) -> str:
    """Text table of the ROIs (with pixel counts and panels if a RoiMap is given)."""
    if not rois:
        return "no ROIs"
    if roi_map is None:   # the session has no detector geometry: no pixel counts / panels
        lines = ["{:<16s} {:>13s} {:>13s}".format("ROI", "rows", "cols"),
                 "{:<16s} {:>27s}".format("", "(pixels from the origin)")]
        for n, r in rois.items():
            lines.append("{:<16s} {:>6d}..{:<5d} {:>6d}..{:<5d}".format(
                n, r["rows"][0], r["rows"][1], r["cols"][0], r["cols"][1]))
        lines.append("(pixels and panels of each ROI: the ROI table of the MPCCD viewer)")
        return "\n".join(lines)
    lines = ["{:<16s} {:>13s} {:>13s} {:>9s}  {}".format("ROI", "rows", "cols", "pixels", "panels"),
             "{:<16s} {:>27s}".format("", "(pixels from the origin)")]
    for n, r in rois.items():
        px, panels = "-", "-"
        if n in roi_map.names:
            i = roi_map.names.index(n)
            px = str(roi_map.n_pixels[i])
            panels = ",".join(str(k + 1) for k, _ in roi_map.parts[i]) or "none (gap / outside)"
        lines.append("{:<16s} {:>6d}..{:<5d} {:>6d}..{:<5d} {:>9s}  {}".format(
            n, r["rows"][0], r["rows"][1], r["cols"][0], r["cols"][1], px, panels))
    return "\n".join(lines)


# ====================================================================== pixels
class RoiMap(object):
    """The panel pixels of every ROI for one geometry.

    names        ROI names (config order)
    parts[i]     [(panel index k, flat panel pixel indices), ...] of ROI i (only panels it covers)
    n_pixels[i]  number of panel pixels in ROI i (for the mean per pixel)
    panels       sorted panel indices covered by any ROI: the only panels to read
    """

    def __init__(self, geom: Any, rois: Dict[str, Any]) -> None:
        self.names = list(rois)
        self.rects = [rois[n] for n in self.names]
        self.parts = []  # type: List[List[Tuple[int, np.ndarray]]]
        self.n_pixels = []  # type: List[int]
        H, W = geom.shape
        row0, col0 = geom.centre                    # image pixel of the detector origin
        for rect in self.rects:
            r0, r1 = (int(v) + row0 for v in rect["rows"])
            c0, c1 = (int(v) + col0 for v in rect["cols"])
            parts = []
            for k, (dst, src) in enumerate(zip(geom._dst, geom._src)):
                rr, cc = dst // W, dst % W
                sel = (rr >= r0) & (rr <= r1) & (cc >= c0) & (cc <= c1)
                if sel.any():
                    parts.append((k, np.unique(src[sel])))
            self.parts.append(parts)
            self.n_pixels.append(int(sum(len(idx) for _, idx in parts)))
        self.panels = sorted(set(k for parts in self.parts for k, _ in parts))

    def sums(self, panels: Dict[int, np.ndarray], kev_per_adu: Any,
             dark: Optional[np.ndarray] = None, threshold_adu: Any = None) -> np.ndarray:
        """Deposited energy (keV) in every ROI for one shot.

        panels         {panel index: raw panel image (ADU)}; only self.panels are needed
        kev_per_adu    keV per ADU of every panel (silas.core.energy.kev_per_adu)
        dark           dark of ALL panels (n_panels, rows, cols) in ADU, or None
        threshold_adu  threshold of every panel in ADU (energy.threshold_adu), or None

        Only the ROI pixels are corrected: value - dark, below threshold -> 0."""
        out = np.zeros(len(self.names), np.float64)
        thr = None if threshold_adu is None else np.asarray(threshold_adu, np.float64).ravel()
        for i, parts in enumerate(self.parts):
            total = 0.0
            for k, idx in parts:
                v = np.asarray(panels[k]).ravel()[idx].astype(np.float64)
                if dark is not None:
                    v = v - dark[k].ravel()[idx]
                if thr is not None:
                    v = np.where(v < thr[k], 0.0, v)
                total += float(v.sum()) * float(kev_per_adu[k])
            out[i] = total
        return out

    def means(self, sums: np.ndarray) -> np.ndarray:
        """keV per pixel of every ROI (sums / number of pixels; NaN for an empty ROI)."""
        n = np.asarray(self.n_pixels, np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(n > 0, np.asarray(sums, np.float64) / n, np.nan)



def analysis_setup(det_ids: Sequence[str], infos: Sequence[Dict[str, Any]], settings: Dict[str, Any],
                   shift_from_data: bool = False) -> Tuple[Optional["RoiMap"], np.ndarray, Any]:
    """(ROI pixels or None, keV per ADU of every panel, Corrections) for an MPCCD array's
    settings (mpccd.get_array) and the panels' detector info. Shared by online and offline.

    shift_from_data: take the aperture shift from the data (stpy gives mp_manipulator_shift;
    offline) instead of the setting geometry.manipulator_shift_um (online, olpy has none)."""
    from silas.core import energy
    from silas.core.corrections import Corrections
    from silas.core.geometry import Geometry

    g = settings["geometry"]
    shift = None if shift_from_data else float(g.get("manipulator_shift_um", 0.0))
    geom = Geometry.from_infos(list(det_ids), list(infos), manipulator_shift_um=shift,
                               rounding=g.get("rounding", "floor"),
                               sub_pixel=tuple(g.get("sub_pixel", (0.0, 0.0))))
    geom = geom.tweaked(panel_offsets=g.get("panel_offsets") or {})
    rois = settings.get("rois") or {}
    rmap = RoiMap(geom, rois) if rois else None
    kev = energy.kev_per_adu(energy.absgains(infos), energy.ev_per_absgain_adu(settings))
    return rmap, kev, Corrections(settings["corrections"], kev, list(det_ids))
