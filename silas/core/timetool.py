"""Arrival-timing monitor (time tool): settings, the ROI trace of one shot, shot classes and
the background.

The time-tool camera (an OPAL, IPX or CHET detector) records, for every shot, the optical probe after a
target in which the X-rays change the transmission. Summing the camera image over one
direction of a rectangle (the ROI) gives a TRACE; the step in the trace (the edge) gives
the arrival time of the X-rays relative to the laser. Each shot is treated on its own:

    trace(image, roi, binning)          mean of the ROI's rows ("rows": trace along x) or
                                        columns ("cols": trace along y)
    correct(trace, background_trace)    divided by ("divide") or minus ("subtract") the
                                        background's trace (the same ROI and binning)

The background is the MEAN IMAGE of the shots of a background run (default: ALL its shots,
as SACLA's timing-monitor tool does; or only those with the laser on and the X-rays off), from a
stored run (background_from_run, HPC, a pool of processes) or captured live
(silas.online.tt_background). Its trace is made with the current ROI and binning
when needed, so changing the ROI needs no new background (the trace of the mean image
is the mean of the shots' traces).

Shot classes (from the SyncDB, per tag):
    "timing"      X-rays on, laser on, beam status OK   -> the edge is measured
    "background"  laser on, X-rays off                  -> used for the background
    "xray only"   X-rays on, laser off
    "other"       neither, or a flag unknown

The edge (find_edge), in CAMERA pixels (independent of the ROI), from the corrected trace:

    1. NaNs (background <= 0) filled in; the search window (edge.window, camera pixels)
    2. derivative of the trace smoothed with a Gaussian (sigma edge.smooth_px): its largest
       value of the wanted sign (edge.direction: "rising", "falling", "any") -> edge_deriv,
       refined to a fraction of a pixel with a parabola through the peak
    3. fit (edge.fit) within +- edge.fit_range_px of it: a smooth step plus a straight line,
       a + c (x - x0) + b * (1 + tanh((x - t) / w)) / 2, by least squares on a grid of t
       (0.5 px, +- 10 px around edge_deriv) and w (2 ... 80 px), a, b, c solved exactly for each;
       the best t refined with a parabola -> edge_fit, step height b, width w
    4. quality: SNR = derivative peak / its noise (robust spread away from the edge),
       |b| >= edge.min_step, |edge_fit - edge_deriv| <= edge.max_diff_px, the edge not at
       the window border -> good
    edge = edge_fit when the fit is on and worked, else edge_deriv.
The same function runs in the live readers (every shot), the viewers and the batch analysis.

Settings live in the config under "timetool" (DEFAULTS); set_option changes one.
"""
import copy
import datetime
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

KEY = "timetool"
CAMERAS = ("OPAL", "IPX", "CHET")     # SACLA's timing-monitor cameras (detector ID prefixes)
BINNINGS = ("rows", "cols")
MODES = ("divide", "subtract")
DIRECTIONS = ("any", "rising", "falling")
XRAY_SHUTTER = "xfel_shutter"         # alias of the XFEL shutter (0 = X-rays off)

DEFAULTS = {
    "detector": None,           # camera detector ID; None: the first OPAL / IPX / CHET found
    "roi": None,                # [x0, x1, y0, y1] camera pixels (inclusive); None: the whole image
    "binning": "rows",          # "rows": mean over the ROI's rows -> trace along x; "cols": along y
    "laser_detector": "none",   # point detector "time-tool laser on"; "none": no flag, always on
                                # (when the probe does not go through the pump shutter)
    "laser_on_value": 1,        # its value for "on"
    "background": {
        "file": None,           # .npz with the mean image (set_tt_background / capture_tt_background)
        "source": None,         # where it came from, e.g. "run 1234479" (for the display)
        "mode": "divide",       # "divide" or "subtract"
    },
    "edge": {
        "direction": "rising",  # in the corrected trace: "rising", "falling" or "any"
        "window": None,         # [p0, p1] camera pixels searched; None: the whole trace
        "smooth_px": 10.0,      # sigma (px) of the Gaussian for the derivative
        "fit": True,            # fit a smooth step around the derivative's peak
        "fit_range_px": 100,    # +- px of the fit
        "min_snr": 5.0,         # derivative peak / noise, at least
        "min_step": 0.02,       # step height in the corrected trace (divide: fraction), at least
        "max_diff_px": 10.0,    # fit and derivative edge agree within
    },
    "ps_per_pulse": 0.006671,   # time per motor pulse (1 pulse = 1 um of stage, double pass: 2 um / c)
    "calibration": None,        # edge pixel -> time-tool motor (calibrate_tt): {"motor", "unit", "order",
                                # "coef" (numpy.polyval), "edge_range", "rms_residual", "source", "file"}
    "sacla_dir": None,          # folder of SACLA's TimingMonitorAnalyzer results <run>.csv / <run>.tma.h5
                                # (compare_tt_sacla); None: give it to compare_tt_sacla
    "display": {
        "cmap": "viridis",
        "log": False,
        "auto_limits": True,
        "percentiles": [1.0, 99.7],
        "vmin": None,
        "vmax": None,
        "update_s": 0.2,        # seconds between display updates / reader reports (live)
        "backend": None,
    },
}  # type: Dict[str, Any]


# ====================================================================== settings
def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def settings(cfg: Any) -> Dict[str, Any]:
    """The time-tool settings: the config's "timetool" section over DEFAULTS."""
    return _merge(DEFAULTS, cfg.get(KEY, {}) or {})


def set_option(cfg: Any, key: str, value: Any) -> None:
    """Set one setting, e.g. set_option(cfg, "background.mode", "subtract")."""
    probe = DEFAULTS  # type: Any
    for part in key.split("."):
        if not isinstance(probe, dict) or part not in probe:
            raise KeyError("unknown time-tool setting {!r} (see silas.core.timetool.DEFAULTS)".format(key))
        probe = probe[part]
    if key == "binning" and value not in BINNINGS:
        raise ValueError("binning must be one of {}".format(BINNINGS))
    if key == "edge.direction" and value not in DIRECTIONS:
        raise ValueError("edge direction must be one of {}".format(DIRECTIONS))
    if key == "background.mode" and value not in MODES:
        raise ValueError("background mode must be one of {}".format(MODES))
    cfg.set(KEY + "." + key, value)


def set_roi(cfg: Any, x0: Optional[int] = None, x1: Optional[int] = None, y0: Optional[int] = None,
            y1: Optional[int] = None) -> Optional[List[int]]:
    """ROI in camera pixels, inclusive (x: column, y: row). No arguments: the whole image."""
    if x0 is None and x1 is None and y0 is None and y1 is None:
        cfg.set(KEY + ".roi", None)
        return None
    if x0 is None or x1 is None or y0 is None or y1 is None:
        raise ValueError("give all four: set_tt_roi(x0, x1, y0, y1) (or none: the whole image)")
    xa, xb = sorted((int(x0), int(x1)))
    ya, yb = sorted((int(y0), int(y1)))
    if xa < 0 or ya < 0:
        raise ValueError("ROI pixels start at 0")
    roi = [xa, xb, ya, yb]
    cfg.set(KEY + ".roi", roi)
    return roi


def describe(cfg: Any) -> str:
    """The settings as text (show_tt)."""
    s = settings(cfg)
    laser, on = laser_settings(cfg)
    bg = s["background"]
    roi = s["roi"]
    lines = [
        "time tool",
        "  camera      {}".format(s["detector"] or "auto (first OPAL / IPX / CHET of the run)"),
        "  ROI         {}".format("x {}..{}, y {}..{} (camera pixels)".format(*roi) if roi else "whole image"),
        "  binning     {} -> trace along {}".format(s["binning"], "x" if s["binning"] == "rows" else "y"),
        "  laser       {} = {:g} is on".format(laser, on) if laser else "  laser       no flag: always on (set_tt_laser)",
        "  background  {}".format("{} ({}), {}".format(bg["source"] or "?", os.path.basename(bg["file"]), bg["mode"])
                                  if bg.get("file") else "none (set_tt_background(run=N) or capture_tt_background())"),
        "  edge        {}".format(edge_text(s["edge"])),
        "  calibration {}".format(calibration_text(s.get("calibration"))),
        "  time        {:g} ps per pulse".format(float(s.get("ps_per_pulse", 0.006671))),
    ]
    return "\n".join(lines)


NO_LASER_FLAG = "none"


def laser_settings(cfg: Any) -> Tuple[Optional[str], float]:
    """(true name of the "time-tool laser on" detector, its value for on); (None, 1) when there
    is no flag (laser_detector "none" or null, the default): the laser is taken as always on."""
    from silas.core import names

    s = settings(cfg)
    det = s.get("laser_detector")
    on = s.get("laser_on_value")
    if not det or str(det).lower() == NO_LASER_FLAG:
        return None, 1.0
    return names.resolve(cfg, str(det)), float(1 if on is None else on)


def edge_shift(edge: Any, cal: Optional[Dict[str, Any]]) -> Any:
    """The time tool's correction of a shot, in the calibration motor's units (pulse):
    P(edge) - P(0), P = the calibration polynomial (motor = P(edge pixel)). The shot's timing is
    then <scanned delay motor> + edge_shift (for a linear P: motor - |slope| x edge, the usual
    linear correction). NaN without a calibration or edge."""
    if not cal or not cal.get("coef"):
        return np.full(np.shape(edge), np.nan) if np.ndim(edge) else np.nan
    coef = np.asarray(cal["coef"], float)
    return np.polyval(coef, np.asarray(edge, float)) - coef[-1]


def calibration_text(c: Optional[Dict[str, Any]]) -> str:
    if not c:
        return "none (calibrate_tt)"
    return "{} [{}] = polyval({}, edge px), edge {:.0f}..{:.0f} px, rms {:.3g}, from {}".format(
        c.get("motor"), c.get("unit") or "?", ["{:.6g}".format(v) for v in c.get("coef", [])],
        *(list(c.get("edge_range") or [np.nan, np.nan]) + [float(c.get("rms_residual", np.nan)), c.get("source")]))


def edge_text(e: Dict[str, Any]) -> str:
    return "{}, window {}, smoothing {:g} px, fit {}, cuts: SNR >= {:g}, step >= {:g}, fit-derivative <= {:g} px".format(
        e.get("direction"), e.get("window") or "whole trace", float(e.get("smooth_px", 10)),
        "+-{} px".format(e.get("fit_range_px")) if e.get("fit") else "off", float(e.get("min_snr", 5)),
        float(e.get("min_step", 0.02)), float(e.get("max_diff_px", 10)))


def find_camera(det_ids: Sequence[str]) -> Optional[str]:
    """The first timing-monitor camera among detector IDs."""
    for d in det_ids:
        if str(d).upper().startswith(CAMERAS):
            return str(d)
    return None


def camera(cfg: Any, det_ids: Sequence[str]) -> str:
    """The configured camera, or the first one found in det_ids."""
    s = settings(cfg)
    if s.get("detector"):
        return str(s["detector"])
    found = find_camera(det_ids)
    if found is None:
        raise ValueError("no time-tool camera (OPAL / IPX / CHET) among the detectors {}: "
                         "set_tt_camera(\"<detector id>\")".format(", ".join(det_ids) or "(none)"))
    return found


# ====================================================================== trace
def roi_box(roi: Optional[Sequence[int]], shape: Tuple[int, ...]) -> Tuple[int, int, int, int]:
    """(x0, x1, y0, y1) inclusive, clipped to an image of ``shape`` (rows, cols)."""
    ny, nx = int(shape[0]), int(shape[1])
    if not roi:
        return 0, nx - 1, 0, ny - 1
    x0, x1, y0, y1 = (int(v) for v in roi)
    x0, x1 = max(0, min(x0, nx - 1)), max(0, min(x1, nx - 1))
    y0, y1 = max(0, min(y0, ny - 1)), max(0, min(y1, ny - 1))
    return min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)


def trace(image: np.ndarray, roi: Optional[Sequence[int]], binning: str = "rows") -> np.ndarray:
    """The ROI's mean over rows (binning "rows": one value per column) or over columns
    ("cols": one value per row), float64."""
    x0, x1, y0, y1 = roi_box(roi, image.shape)
    sub = image[y0:y1 + 1, x0:x1 + 1]
    return sub.mean(axis=0 if binning == "rows" else 1, dtype=np.float64)


def trace_axis(roi: Optional[Sequence[int]], shape: Tuple[int, ...], binning: str = "rows") -> np.ndarray:
    """Camera pixel of every trace point (x for "rows", y for "cols")."""
    x0, x1, y0, y1 = roi_box(roi, shape)
    return np.arange(x0, x1 + 1) if binning == "rows" else np.arange(y0, y1 + 1)


def correct(tr: np.ndarray, background_trace: Optional[np.ndarray], mode: str = "divide") -> Optional[np.ndarray]:
    """The trace divided by / minus the background's trace (None without a background or if
    the lengths differ). Division: background points <= 0 give NaN."""
    if background_trace is None or background_trace.shape != tr.shape:
        return None
    if mode == "subtract":
        return tr - background_trace
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(background_trace > 0, tr / np.where(background_trace > 0, background_trace, 1.0), np.nan)


# ====================================================================== edge
_KERNELS = {}  # type: Dict[float, Tuple[np.ndarray, np.ndarray]]
FIT_WIDTHS = np.geomspace(2.0, 80.0, 12)        # px, the step widths tried by the fit
EDGE_KEYS = ("edge", "edge_deriv", "edge_fit", "step", "width", "snr", "good")


def _kernels(sigma: float) -> Tuple[np.ndarray, np.ndarray]:
    """(Gaussian, its derivative) for smoothing with ``sigma`` px (cached)."""
    sigma = max(float(sigma), 0.5)
    if sigma not in _KERNELS:
        half = int(np.ceil(4 * sigma))
        u = np.arange(-half, half + 1, dtype=np.float64)
        g = np.exp(-0.5 * (u / sigma) ** 2)
        g /= g.sum()
        _KERNELS[sigma] = (g, -u / sigma ** 2 * g)
    return _KERNELS[sigma]


def _filter(y: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Convolution with a mirrored border (the result has the length of y)."""
    half = len(kernel) // 2
    if len(y) <= half:
        pad = np.pad(y, half, mode="edge")
    else:
        pad = np.pad(y, half, mode="reflect")
    return np.convolve(pad, kernel, mode="valid")


def _parabola(y0: float, y1: float, y2: float) -> float:
    """Offset (-0.5 .. 0.5) of the extremum of the parabola through 3 equally spaced points."""
    den = y0 - 2.0 * y1 + y2
    if den == 0 or not np.isfinite(den):
        return 0.0
    return float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))


def _fill_nan(y: np.ndarray) -> np.ndarray:
    ok = np.isfinite(y)
    if ok.all() or not ok.any():
        return np.nan_to_num(y)
    idx = np.arange(len(y))
    return np.interp(idx, idx[ok], y[ok])


def _fit_step(x: np.ndarray, y: np.ndarray, t0: float) -> Optional[Dict[str, float]]:
    """Least-squares fit of a + c (x - t0) + b (1 + tanh((x - t) / w)) / 2 near t0: a grid of
    t (0.5 px, +- 10 px) and w (FIT_WIDTHS), a, b, c exact for each; t refined by a parabola."""
    if len(x) < 8:
        return None
    ts = t0 + np.arange(-10.0, 10.01, 0.5)
    u = x - t0
    s_ = 0.5 * (1.0 + np.tanh((x[None, None, :] - ts[None, :, None]) / FIT_WIDTHS[:, None, None]))
    n = float(len(x))
    su, suu = u.sum(), (u * u).sum()
    sy, suy, syy = y.sum(), (u * y).sum(), (y * y).sum()
    ss = s_.sum(axis=2)
    sus = (s_ * u).sum(axis=2)
    sss = (s_ * s_).sum(axis=2)
    ssy = (s_ * y).sum(axis=2)
    shape = ss.shape
    gram = np.empty(shape + (3, 3))
    gram[..., 0, 0], gram[..., 0, 1], gram[..., 0, 2] = n, su, ss
    gram[..., 1, 0], gram[..., 1, 1], gram[..., 1, 2] = su, suu, sus
    gram[..., 2, 0], gram[..., 2, 1], gram[..., 2, 2] = ss, sus, sss
    rhs = np.empty(shape + (3,))
    rhs[..., 0], rhs[..., 1], rhs[..., 2] = sy, suy, ssy
    try:
        coef = np.linalg.solve(gram, rhs[..., None])[..., 0]
    except np.linalg.LinAlgError:
        return None
    sse = syy - (coef * rhs).sum(axis=-1)
    iw, it = np.unravel_index(int(np.argmin(sse)), shape)
    dt = 0.0
    if 0 < it < len(ts) - 1:
        dt = 0.5 * _parabola(float(sse[iw, it - 1]), float(sse[iw, it]), float(sse[iw, it + 1]))
    at_border = it in (0, len(ts) - 1)
    a, c, b = (float(v) for v in coef[iw, it])
    return {"t": float(ts[it]) + dt, "w": float(FIT_WIDTHS[iw]), "a": a, "b": b, "c": c,
            "rms": float(np.sqrt(max(float(sse[iw, it]), 0.0) / n)), "at_border": float(at_border)}


def step_model(x: np.ndarray, fit: Dict[str, float], t0: float) -> np.ndarray:
    """The fitted step at x (for drawing)."""
    return fit["a"] + fit["c"] * (x - t0) + fit["b"] * 0.5 * (1.0 + np.tanh((x - fit["t"]) / fit["w"]))


def find_edge(corrected: np.ndarray, axis: np.ndarray, edge: Dict[str, Any],
              details: bool = False) -> Dict[str, Any]:
    """The edge of one shot's corrected trace (see the module docstring), in camera pixels.
    {"edge", "edge_deriv", "edge_fit", "step", "width", "snr", "good", "why"}; NaN / False if
    there is none. details=True also gives "smooth" (the smoothed trace), "fit_x", "fit_y"."""
    out = {"edge": np.nan, "edge_deriv": np.nan, "edge_fit": np.nan, "step": np.nan, "width": np.nan,
           "snr": np.nan, "good": False, "why": ""}  # type: Dict[str, Any]
    y = _fill_nan(np.asarray(corrected, np.float64))
    x = np.asarray(axis, np.float64)
    if len(y) < 10:
        out["why"] = "trace too short"
        return out
    g, dg = _kernels(float(edge.get("smooth_px", 10.0)))
    d = _filter(y, dg)
    if details:
        out["smooth"] = _filter(y, g)
    lo, hi = 0, len(y) - 1
    win = edge.get("window")
    if win:
        lo = int(np.clip(np.searchsorted(x, min(win)), 0, len(y) - 1))
        hi = int(np.clip(np.searchsorted(x, max(win), side="right") - 1, 0, len(y) - 1))
    margin = min(len(g) // 2, max((hi - lo) // 4, 1))     # skip the smoothing's border
    if lo == 0:
        lo = margin
    if hi == len(y) - 1:
        hi = len(y) - 1 - margin
    if hi - lo < 5:
        out["why"] = "search window too small"
        return out
    direction = edge.get("direction", "any")
    sign = {"rising": 1.0, "falling": -1.0}.get(direction, 0.0)
    seg = d[lo:hi + 1] * sign if sign else np.abs(d[lo:hi + 1])
    k = lo + int(np.argmax(seg))
    peak = float(seg[k - lo])
    sgn = sign if sign else float(np.sign(d[k]) or 1.0)
    off = _parabola(float(d[k - 1] * sgn), float(d[k] * sgn), float(d[k + 1] * sgn)) if 0 < k < len(y) - 1 else 0.0
    edge_deriv = float(x[k] + off)
    # noise of the derivative away from the edge (median absolute deviation)
    keep = np.ones(hi - lo + 1, bool)
    away = int(max(3 * float(edge.get("smooth_px", 10.0)), 10))
    keep[max(0, k - lo - away):k - lo + away + 1] = False
    rest = d[lo:hi + 1][keep]
    noise = 1.4826 * float(np.median(np.abs(rest - np.median(rest)))) if rest.size > 5 else np.nan
    snr = peak / noise if noise and np.isfinite(noise) and noise > 0 else np.nan
    out.update(edge_deriv=edge_deriv, snr=snr, edge=edge_deriv)
    why = []  # type: List[str]
    if k - lo < 2 or hi - k < 2:
        why.append("at the window border")
    if not (snr >= float(edge.get("min_snr", 5.0))):
        why.append("SNR {:.1f} < {:g}".format(snr, float(edge.get("min_snr", 5.0))))
    step = float(np.sign(sgn) * peak * np.sqrt(2 * np.pi) * float(edge.get("smooth_px", 10.0)))  # step of a sharp edge
    if edge.get("fit", True):
        r = int(edge.get("fit_range_px", 100))
        i0, i1 = max(0, k - r), min(len(y), k + r + 1)
        fit = _fit_step(x[i0:i1], y[i0:i1], edge_deriv)
        if fit is None or fit["at_border"] or (sign and np.sign(fit["b"]) != sign):
            why.append("fit failed")
        else:
            out.update(edge_fit=fit["t"], edge=fit["t"], width=fit["w"])
            step = fit["b"]
            if abs(fit["t"] - edge_deriv) > float(edge.get("max_diff_px", 10.0)):
                why.append("fit {:.1f} px from the derivative".format(abs(fit["t"] - edge_deriv)))
            if details:
                out["fit_x"] = x[i0:i1]
                out["fit_y"] = step_model(x[i0:i1], fit, edge_deriv)
    out["step"] = step
    if not (abs(step) >= float(edge.get("min_step", 0.02))):
        why.append("step {:.3g} < {:g}".format(abs(step), float(edge.get("min_step", 0.02))))
    out["good"] = not why
    out["why"] = "; ".join(why)
    return out


def shot_edge(image: np.ndarray, s: Dict[str, Any], background_trace: Optional[np.ndarray]) -> Dict[str, Any]:
    """trace -> corrected -> edge for one image with the settings ``s``. Without a background
    the edge is looked for in the raw trace (marked not good)."""
    roi, binning = s.get("roi"), s.get("binning", "rows")
    tr = trace(image, roi, binning)
    axis = trace_axis(roi, image.shape, binning)
    corr = correct(tr, background_trace, s["background"].get("mode", "divide"))
    res = find_edge(corr if corr is not None else tr, axis, s["edge"])
    if corr is None:
        res["good"] = False
        res["why"] = "no background" + ("; " + res["why"] if res["why"] else "")
    return res


# ====================================================================== shot classes
TIMING, BACKGROUND, XRAY_ONLY, OTHER = 0, 1, 2, 3
CLASS_NAMES = {TIMING: "timing", BACKGROUND: "background", XRAY_ONLY: "xray only", OTHER: "other"}


def classify(xray: np.ndarray, laser: np.ndarray, beam_ok: np.ndarray) -> np.ndarray:
    """Class per shot from the X-ray shutter (0 off, else on; NaN unknown), the laser flag
    (1 on, 0 off, NaN unknown) and the beam status (True OK)."""
    xray = np.asarray(xray, float)
    laser = np.asarray(laser, float)
    beam_ok = np.asarray(beam_ok, bool)
    out = np.full(len(xray), OTHER, np.int8)
    x_on = np.isfinite(xray) & (xray != 0)
    x_off = np.isfinite(xray) & (xray == 0)
    l_on = np.isfinite(laser) & (laser == 1)
    l_off = np.isfinite(laser) & (laser == 0)
    out[x_on & l_on & beam_ok] = TIMING
    out[x_off & l_on] = BACKGROUND
    out[x_on & l_off] = XRAY_ONLY
    return out


def laser_flag(values: Sequence[float], on_value: float) -> np.ndarray:
    """Raw laser detector values -> 1 on, 0 off, NaN unknown."""
    v = np.asarray(values, float)
    out = np.where(v == on_value, 1.0, 0.0)
    out[~np.isfinite(v)] = np.nan
    return out


def run_flags(cfg: Any, run: int, tags: Optional[Sequence[int]] = None) -> Dict[str, Any]:
    """Per tag of a run (dbpy): {"tags", "xray", "laser", "beam_ok", "cls", "taghi"}.
    Flags that cannot be read are NaN (class "other")."""
    import dbpy

    from silas.core import db, names

    bl = cfg.beamline
    taghi = db.high_tag(bl, run)
    tags = list(tags if tags is not None else db.tags(bl, run))
    n = len(tags)
    try:
        if cfg.get(XRAY_SHUTTER, None) is None:
            xray = np.ones(n, float)  # no shutter flag: always on
            print("no X-ray shutter defined (set {} in the config)".format(XRAY_SHUTTER))
            print("all shots will be treated as X-rays on (shutter open).")
        else:
            xray = np.array(db.read_point(names.resolve(cfg, cfg.get(XRAY_SHUTTER, None)), taghi, tags, errors="nan"), float)
    except (KeyError, ValueError):
        xray = np.full(n, np.nan)
    det, on = laser_settings(cfg)
    if det:
        laser = laser_flag(db.read_point(det, taghi, tags, errors="nan"), on)
    else:
        laser = np.ones(n)               # no laser flag: always on
    beam = np.zeros(n, bool)
    for i in range(0, n, 5000):
        chunk = tuple(int(t) for t in tags[i:i + 5000])
        try:
            beam[i:i + len(chunk)] = np.array(dbpy.read_beamstatuslist(bl, taghi, chunk)) == 1
        except dbpy.APIError as err:
            print("beam status of {} tags not readable ({}): taken as not OK".format(len(chunk), err.args))
    return {"tags": np.array(tags, np.int64), "xray": xray, "laser": laser, "beam_ok": beam,
            "cls": classify(xray, laser, beam), "taghi": taghi}


def class_counts(cls: np.ndarray) -> str:
    return ", ".join("{} {}".format(CLASS_NAMES[c], int((cls == c).sum())) for c in (TIMING, BACKGROUND, XRAY_ONLY, OTHER))


# ====================================================================== background
# ====================================================================== edges of every tag (time-tool log)
EDGE_COLUMNS = ["tag", "edge", "edge_deriv", "edge_fit", "step", "width", "snr", "good", "cls", "sid"]


def settings_id(s: Dict[str, Any]) -> str:
    """Short id of everything that changes an edge (ROI, binning, background, edge settings);
    stored with every edge, so edges made with other settings are recognised."""
    import hashlib
    import json

    key = json.dumps([s.get("roi"), s.get("binning"), s["background"].get("file"), s["background"].get("mode"),
                      s.get("edge")], sort_keys=True)
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def edges_dir(cfg: Any) -> str:
    path = os.path.join(cfg.output_dir(), "timetool", "edges")
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def edges_path(cfg: Any, run: int) -> str:
    return os.path.join(edges_dir(cfg), "run_{}.csv".format(int(run)))


def read_edges_csv(path: str) -> Any:
    """A run's edge file; lines cut short (a logger stopped while writing) are skipped."""
    import io

    import pandas as pd

    with open(path) as fh:
        lines = fh.read().splitlines()
    if not lines:
        return pd.DataFrame(columns=EDGE_COLUMNS)
    n = lines[0].count(",")
    good = [lines[0]] + [ln for ln in lines[1:] if ln.count(",") == n and ln.strip()]
    return pd.read_csv(io.StringIO("\n".join(good) + "\n"), dtype={"sid": str})


def load_edges(cfg: Any, run: int, current_only: bool = True) -> Any:
    """The time-tool log of a run (DataFrame, one row per tag, sorted, without repeats) with the
    correction of every shot from the CURRENT calibration: shift_pulse = P(edge) - P(0) and
    shift_ps = shift_pulse x ps_per_pulse (NaN where the edge is not good or there is no
    calibration). current_only: only rows made with the current settings (sid). Empty if the
    run has no log yet."""
    import pandas as pd

    path = edges_path(cfg, run)
    if not os.path.exists(path):
        return pd.DataFrame(columns=EDGE_COLUMNS + ["shift_pulse", "shift_ps"])
    df = read_edges_csv(path)
    df = df.drop_duplicates("tag", keep="last").sort_values("tag").reset_index(drop=True)
    s = settings(cfg)
    if current_only and "sid" in df:
        df = df[df["sid"].astype(str) == settings_id(s)].reset_index(drop=True)
    shift = edge_shift(df["edge"].to_numpy(float), s.get("calibration"))
    shift = np.where(df["good"].astype(bool).to_numpy(), shift, np.nan)
    df["shift_pulse"] = shift
    df["shift_ps"] = shift * float(s.get("ps_per_pulse", 0.006671))
    return df


def background_dir(cfg: Any) -> str:
    path = os.path.join(cfg.output_dir(), "timetool")
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def save_background(cfg: Any, mean: np.ndarray, n: int, det_id: str, source: str, meta: Dict[str, Any],
                    use: bool = True) -> str:
    """Save a mean image as the background file; use=True: set it in the config."""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    label = "".join(c if (c.isalnum() or c in "-_") else "_" for c in source.replace(" ", "")).strip("_")
    path = os.path.join(background_dir(cfg), "background_{}_{}_{}.npz".format(det_id, label, stamp))
    np.savez_compressed(path, mean=mean.astype(np.float32), n_shots=int(n), det_id=det_id, source=source,
                        meta=repr(meta))
    if use:
        set_option(cfg, "background.file", path)
        set_option(cfg, "background.source", source)
    return path


class Background(object):
    """The background mean image, loaded once per file; trace() for any ROI / binning."""

    def __init__(self) -> None:
        self.path = None  # type: Optional[str]
        self.mean = None  # type: Optional[np.ndarray]
        self.n = 0
        self.det_id = ""
        self.error = ""

    def load(self, path: Optional[str]) -> bool:
        """(Re)load if the file changed. Returns True if a background is available."""
        if path == self.path:
            return self.mean is not None
        self.path, self.mean, self.n, self.det_id, self.error = path, None, 0, "", ""
        if not path:
            return False
        try:
            with np.load(path) as d:
                self.mean = d["mean"].astype(np.float32)
                self.n = int(d["n_shots"])
                self.det_id = str(d["det_id"])
        except (OSError, KeyError, ValueError) as err:
            self.error = "background file not readable: {} ({})".format(path, err)
            print(self.error)
        return self.mean is not None

    def trace(self, roi: Optional[Sequence[int]], binning: str, shape: Tuple[int, ...]) -> Optional[np.ndarray]:
        if self.mean is None:
            return None
        if self.mean.shape != tuple(shape[:2]):
            self.error = "background image {} does not match the camera image {}".format(self.mean.shape, shape)
            return None
        return trace(self.mean, roi, binning)


def _sum_part(job: Tuple[str, int, str, List[int]]) -> Tuple[Optional[np.ndarray], int, int, float]:
    """Pool worker: (sum of the images, shots summed, shots not readable, seconds) of some tags."""
    import stpy

    det_id, bl, run_s, tags = job
    run = int(run_s)
    t0 = time.time()
    reader = stpy.StorageReader(det_id, bl, (run,))
    buf = stpy.StorageBuffer(reader)
    total = None  # type: Optional[np.ndarray]
    n, bad = 0, 0
    for tag in tags:
        try:
            reader.collect(buf, int(tag))
            img = buf.read_det_data(0)
        except stpy.APIError:
            bad += 1
            continue
        total = img.astype(np.float64) if total is None else total + img
        n += 1
    return total, n, bad, time.time() - t0


def background_from_run(cfg: Any, run: int, workers: int = 6, max_shots: Optional[int] = None,
                        use: bool = True, shots: str = "all") -> Optional[str]:
    """Mean image of a background run's shots (HPC: stpy), by ``workers`` processes; saved and
    (use=True) set as the background. None if no shot fits.
    shots="all" (default): every shot of the run, as SACLA's timing-monitor tool does (in a
    background run the X-rays can be blocked by something the flags do not show) - check in the
    viewer that its shots have no edge. shots="background": only the shots with the laser on and the X-ray shutter closed."""
    import multiprocessing

    from silas.core import db, runner

    bl = cfg.beamline
    det_id = camera(cfg, db.run_detectors(bl, run))
    print("background from run {} ({}): reading the shot flags (dbpy) ...".format(run, det_id), flush=True)
    f = run_flags(cfg, run)
    if shots not in ("background", "all"):
        raise ValueError('shots must be "background" or "all"')
    pick = f["tags"] if shots == "all" else f["tags"][f["cls"] == BACKGROUND]
    print("run {}: {} tags: {}".format(run, len(f["tags"]), class_counts(f["cls"])))
    if shots == "all":
        print("using ALL {} shots of the run (shots=\"all\"), whatever their flags".format(len(pick)))
    if not len(pick):
        laser, _ = laser_settings(cfg)
        print("no shot with the laser ON ({}) and the X-rays OFF ({}) in run {}: no background made. "
              "Is the laser detector right (set_tt_laser)? If the X-rays were blocked by something the "
              "flags do not show, check the shots in the viewer (no edge?) and use shots=\"all\"."
              .format(laser or "no flag", cfg.get(XRAY_SHUTTER, None), run))
        return None
    if max_shots and len(pick) > max_shots:
        pick = pick[np.linspace(0, len(pick) - 1, int(max_shots)).astype(int)]
    n_proc = runner.cap_workers(max(1, min(int(workers), len(pick))), "workers")
    parts = [list(int(t) for t in p) for p in np.array_split(pick, n_proc) if len(p)]
    print("averaging {} shots with {} processes ...".format(len(pick), len(parts)), flush=True)
    t0 = time.time()
    jobs = [(det_id, bl, str(run), p) for p in parts]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(len(jobs), initializer=runner.worker_ignores_ctrl_c) as pool:
        results = pool.map(_sum_part, jobs)
    total = None  # type: Optional[np.ndarray]
    n, bad = 0, 0
    for s, k, b, _ in results:
        n += k
        bad += b
        if s is not None:
            total = s if total is None else total + s
    if total is None or n == 0:
        print("no shot could be read ({} not readable)".format(bad))
        return None
    dt = time.time() - t0
    print("averaged {} shots in {:.1f} s ({:.0f} shots/s){}".format(n, dt, n / max(dt, 1e-9),
                                                                    ", {} not readable".format(bad) if bad else ""))
    source = "run {}{}".format(run, " (all shots)" if shots == "all" else "")
    path = save_background(cfg, total / n, n, det_id, source,
                           {"run": run, "tags_used": n, "shots": shots, "laser": laser_settings(cfg)[0]}, use=use)
    print("background saved: {}{}".format(path, "\nset as the time-tool background" if use else ""))
    return path
