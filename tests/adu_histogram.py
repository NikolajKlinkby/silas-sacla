#!/usr/bin/env python3
"""ADU per photon of an MPCCD from stored data: pixel histograms with photon peaks (read-only).

Run on an HPC compute node (bt_node small is enough):

    bt_env
    cd ~/SILAS
    python tests/adu_histogram.py 1234501 1234526 1234551 --det MPCCD-8N0-3-002
    python tests/adu_histogram.py 1234501 1234528 --tags 300 --det MPCCD-8N0-3-002-1

For every panel of the detector it builds, over the sampled shots of the given runs,
three integer-binned (1 ADU) histograms:

  raw        the stored pixel values as they are (stored MPCCD data can already be offset-corrected)
  corrected  minus a per-pixel pedestal (mean of the pixel's photon-free values in the
             first --ped-tags shots) and minus a per-shot offset (median of the panel)
  cluster    3x3 sums around local maxima above --seed noise sigmas, on 'corrected':
             collects the charge of a photon shared between neighbouring pixels

and finds the noise peak (sigma) and the 1- and 2-photon peaks. The photon energy is
taken from the run (dbpy.read_config_photonenergy_in_keV). Shots with the X-ray shutter
closed (xfel_shutter = 0) are histogrammed separately (pure noise), if there are any.

Results in output/adu_calib/:
  adu_hist_<det>.npz      all histograms (counts per integer ADU, offset in 'adu0')
  adu_calib_<det>.json    per panel: noise sigma, 1-/2-photon peak, ADU per photon,
                          eV per ADU, and eV per (ADU x mp_absgain)  (nominal 3.65)
  adu_hist_<det>.png      the histograms (log y; ADU below, keV on top)

Nothing is written to SACLA. Only numpy, scipy (optional), matplotlib, dbpy, stpy.
"""
import argparse
import json
import math
import os
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

XRAY_SHUTTER = "xfel_bl_3_shutter_1_open_valid/status"   # alias xfel_shutter (XFEL shutter)
W_SI_EV = 3.65                                           # eV per electron-hole pair in Si

ADU_LO = -1000          # histogram range (1 ADU bins); values outside go to the end bins
ADU_HI = 20000


# ====================================================================== accumulation (pure numpy)
def robust_sigma(x: np.ndarray) -> float:
    """Noise sigma from the median absolute deviation (photons barely affect it)."""
    x = x[np.isfinite(x)]
    med = np.median(x)
    return float(1.4826 * np.median(np.abs(x - med)))


def int_hist(values: np.ndarray, lo: int = ADU_LO, hi: int = ADU_HI) -> np.ndarray:
    """Counts per integer ADU from lo to hi (inclusive); outside values are clipped into the end bins."""
    v = np.rint(values.ravel())
    v = np.clip(v, lo, hi).astype(np.int64) - lo
    return np.bincount(v, minlength=hi - lo + 1)


def local_max_3x3(x: np.ndarray) -> np.ndarray:
    """True where a pixel is >= all 8 neighbours (and > the ones before it, to break ties)."""
    p = np.pad(x, 1, mode="constant", constant_values=-np.inf)
    c = p[1:-1, 1:-1]
    m = np.ones(x.shape, bool)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            nb = p[1 + dr:p.shape[0] - 1 + dr, 1 + dc:p.shape[1] - 1 + dc]
            m &= (c > nb) if (dr, dc) < (0, 0) else (c >= nb)
    return m


def box_sum_3x3(x: np.ndarray) -> np.ndarray:
    p = np.pad(x, 1, mode="constant", constant_values=0.0)
    s = np.zeros(x.shape, np.float64)
    for dr in (0, 1, 2):
        for dc in (0, 1, 2):
            s += p[dr:dr + x.shape[0], dc:dc + x.shape[1]]
    return s


class PanelAccumulator(object):
    """Pedestal (pass 1) and histograms (pass 2) of one panel."""

    def __init__(self, seed_sigma: float = 5.0, ped_cut_sigma: float = 4.0) -> None:
        self.seed_sigma = seed_sigma
        self.ped_cut_sigma = ped_cut_sigma
        # noise sigma of the first shot (raw)
        self.sigma0 = None  # type: Optional[float]
        self.ped_sum = None  # type: Optional[np.ndarray]
        self.ped_n = None  # type: Optional[np.ndarray]
        self.pedestal = None  # type: Optional[np.ndarray]
        # noise sigma after pedestal correction
        self.sigma = None  # type: Optional[float]
        n = ADU_HI - ADU_LO + 1
        self.h = {k: np.zeros(n, np.int64) for k in ("raw", "corrected", "cluster", "dark_raw", "dark_corrected")}
        self.n_shots = {"xray": 0, "dark": 0, "ped": 0}
        self.n_clusters = 0
        self.integer_fraction = None  # type: Optional[float]
        self.dtype = None  # type: Optional[str]
        self.shot_offsets = []  # type: List[float]

    # ---------------------------------------------------------------- pass 1
    def add_pedestal(self, img: np.ndarray) -> None:
        x = np.asarray(img, np.float64)
        if self.sigma0 is None:
            self.sigma0 = robust_sigma(x[::4, ::4])
            self.ped_sum = np.zeros(x.shape)
            self.ped_n = np.zeros(x.shape)
            self.dtype = str(np.asarray(img).dtype)
            sample = np.asarray(img).ravel()[::97]
            self.integer_fraction = float(np.mean(sample == np.rint(sample)))
        assert self.ped_sum is not None and self.ped_n is not None
        med = float(np.median(x[::4, ::4]))
        ok = x < med + self.ped_cut_sigma * self.sigma0          # photon-free values only
        self.ped_sum += np.where(ok, x, 0.0)
        self.ped_n += ok
        self.n_shots["ped"] += 1

    def finish_pedestal(self) -> None:
        assert self.ped_sum is not None and self.ped_n is not None
        ped = np.where(self.ped_n > 0, self.ped_sum / np.maximum(self.ped_n, 1), 0.0)
        # pixels never photon-free (e.g. a Bragg peak) keep the panel median
        bad = self.ped_n < max(3, 0.2 * self.n_shots["ped"])
        ped[bad] = np.median(ped[~bad]) if (~bad).any() else 0.0
        self.pedestal = ped.astype(np.float32)

    def correct(self, img: np.ndarray) -> Tuple[np.ndarray, float]:
        x = np.asarray(img, np.float64)
        if self.pedestal is not None:
            x = x - self.pedestal
        off = float(np.median(x[::2, ::2]))                          # per-shot offset (most pixels empty)
        return x - off, off

    # ---------------------------------------------------------------- pass 2
    def add_shot(self, img: np.ndarray, xray: bool = True) -> None:
        if self.sigma0 is None:
            self.add_pedestal(img)          # no pass 1 given: at least the noise sigma
        x, off = self.correct(img)
        if xray:
            self.n_shots["xray"] += 1
            self.shot_offsets.append(off)
            self.h["raw"] += int_hist(np.asarray(img))
            self.h["corrected"] += int_hist(x)
            if self.sigma is None:
                self.sigma = robust_sigma(x[::4, ::4])
            seed = self.seed_sigma * self.sigma
            peaks = local_max_3x3(x) & (x > seed)
            if peaks.any():
                sums = box_sum_3x3(x)[peaks]
                self.h["cluster"] += int_hist(sums)
                self.n_clusters += int(peaks.sum())
        else:
            self.n_shots["dark"] += 1
            self.h["dark_raw"] += int_hist(np.asarray(img))
            self.h["dark_corrected"] += int_hist(x)


# ====================================================================== peak analysis
def _smooth(y: np.ndarray, w: int) -> np.ndarray:
    w = max(1, int(w))
    if w <= 1:
        return y.astype(float)
    k = np.ones(w) / w
    return np.convolve(y.astype(float), k, mode="same")


def _gauss(x: np.ndarray, a: float, mu: float, s: float, c0: float, c1: float) -> np.ndarray:
    return a * np.exp(-0.5 * ((x - mu) / s) ** 2) + c0 + c1 * (x - mu)


def fit_peak(x: np.ndarray, y: np.ndarray, mu0: float, s0: float) -> Dict[str, Any]:
    """Gaussian + linear background in mu0 +- 2.5 s0. Falls back to moments without scipy."""
    sel = (x > mu0 - 2.5 * s0) & (x < mu0 + 2.5 * s0)
    xs, ys = x[sel].astype(float), y[sel].astype(float)
    out = {"mu": float(mu0), "sigma": float(s0), "amp": float(ys.max() if ys.size else 0),
           "fit": "none"}  # type: Dict[str, Any]
    if xs.size < 8 or ys.sum() <= 0:
        return out
    try:
        from scipy.optimize import curve_fit

        p0 = [ys.max() - ys.min(), mu0, s0, ys.min(), 0.0]
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p, cov = curve_fit(_gauss, xs, ys, p0=p0, sigma=np.sqrt(np.maximum(ys, 1.0)), maxfev=20000)
        err = np.sqrt(np.abs(np.diag(cov))) if np.all(np.isfinite(cov)) else [math.nan] * 5
        if abs(p[1] - mu0) < 2.5 * s0 and 0 < abs(p[2]) < 5 * s0:
            return {"mu": float(p[1]), "mu_err": float(err[1]), "sigma": float(abs(p[2])),
                    "amp": float(p[0]), "fit": "gauss"}
    except Exception as err:  # noqa: BLE001  scipy missing or fit failed -> moments
        out["fit_error"] = str(err)[:120]
    b = np.linspace(ys[0], ys[-1], ys.size)        # straight-line background under the peak
    w = np.maximum(ys - b, 0)
    if w.sum() > 0:
        mu = float((w * xs).sum() / w.sum())
        out.update(mu=mu, sigma=float(math.sqrt((w * (xs - mu) ** 2).sum() / w.sum())), fit="moments")
    return out


def local_maxima(y: np.ndarray, start: int, half_window: int, min_count: float) -> List[int]:
    """Indices >= start that are the maximum within +- half_window and above min_count."""
    out = []
    n = len(y)
    for i in range(max(start, half_window), n - half_window):
        v = y[i]
        if v < min_count:
            continue
        win = y[i - half_window:i + half_window + 1]
        if v >= win.max() and v > win[0] and v > win[-1]:
            out.append(i)
    return out


def find_edge(x: np.ndarray, ys: np.ndarray, lo: float, hi: float, s: float) -> Optional[Dict[str, float]]:
    """Strongest falling edge of a (smoothed) histogram between lo and hi: where the
    single-pixel plateau of shared photons ends, at the full photon signal."""
    sel = np.where((x >= lo) & (x <= hi) & (ys > 0))[0]
    if sel.size < 10:
        return None
    ly = np.log(np.maximum(ys, 0.5))
    d = np.gradient(ly)
    ie = int(sel[np.argmin(d[sel])])
    span = max(3 * s, 0.15 * (x[ie] - lo))
    above = ys[(x > x[ie] - 2 * span) & (x < x[ie] - span)]
    below = ys[(x > x[ie] + span) & (x < x[ie] + 2 * span)]
    if not above.size or not below.size:
        return None
    plateau, floor = float(np.median(above)), float(np.median(below))
    half = 0.5 * (plateau + floor)
    # crossing of the half level next to the steepest point
    j = ie
    while j > 0 and ys[j] < half and x[j] > x[ie] - span:
        j -= 1
    while j < len(ys) - 1 and ys[j + 1] >= half and x[j] < x[ie] + span:
        j += 1
    return {"adu": float(x[j]), "steepest_adu": float(x[ie]), "plateau": plateau, "floor": floor,
            "contrast": plateau / max(floor, 0.5)}


def analyse(hist: np.ndarray, kind: str, noise_sigma: Optional[float],
            predicted: Optional[float] = None) -> Dict[str, Any]:
    """Noise peak + 1-photon position of an integer histogram (index 0 = ADU_LO).

    single-pixel kinds: the 1-photon position is a peak if there is a clear one,
    otherwise the falling edge where the plateau of shared photons ends.
    cluster: the 1-photon peak of the 3x3 sums (zero is at 0 ADU)."""
    x = np.arange(ADU_LO, ADU_HI + 1, dtype=float)
    y = hist.astype(float).copy()
    y[0] = y[-1] = 0                                  # clipped end bins
    res = {"n_entries": int(hist.sum())}  # type: Dict[str, Any]
    if hist.sum() == 0:
        return res
    s = max(float(noise_sigma or 10.0), 1.0)
    if kind == "cluster":
        zero = 0.0
        s_c = 3.0 * s                                 # noise of a 3x3 sum
        res["noise"] = {"mu": 0.0, "sigma": s_c}
        lo, hi = (0.3 * predicted, 3.0 * predicted) if predicted else (6 * s, float(ADU_HI))
    else:
        i0 = int(np.argmax(_smooth(y, 3)))
        noise = fit_peak(x, y, x[i0], s)
        res["noise"] = noise
        zero = noise["mu"]
        s = max(noise["sigma"], 1.0)
        s_c = s
        lo, hi = (zero + 0.4 * predicted, zero + 2.5 * predicted) if predicted else (zero + 6 * s, float(ADU_HI))
        lo = max(lo, zero + 6 * s)
    ys = _smooth(y, max(3, int(s)))
    # peaks: local maxima, ranked by prominence over the valley towards lower ADU
    i_lo, i_hi = int(np.searchsorted(x, lo)), int(np.searchsorted(x, hi))
    cand = [i for i in local_maxima(ys[:i_hi], i_lo, half_window=max(5, int(2 * s_c)),
                                    min_count=max(5.0, 1e-7 * y.sum()))]
    ranked = []
    for i in cand:
        a = max(i_lo, i - int(6 * s_c))
        valley = ys[a:i].min() if i > a else 0.0
        ranked.append((ys[i] / max(valley, 0.5), i))
    ranked.sort(reverse=True)
    res["candidates_adu"] = [{"adu": float(x[i] - zero), "counts": float(ys[i]), "prominence": float(r)}
                             for r, i in ranked[:8]]
    peak = None
    if kind == "cluster":
        if ranked:
            near = [(abs(x[i] - predicted), i) for r, i in ranked
                    if predicted and 0.7 * predicted < x[i] < 1.4 * predicted and r >= 1.3]
            # the peak nearest the prediction; without one the most prominent (fluorescence lines
            # below the elastic peak can have more counts, so counts alone are not used)
            i1 = min(near)[1] if near else ranked[0][1]
            peak = fit_peak(x, y, x[i1], max(s_c, 0.05 * x[i1]))
    else:
        good = [(r, i) for r, i in ranked if r >= 1.3]
        if good:
            i1 = min(i for _, i in good)
            peak = fit_peak(x, y, x[i1], max(s, 0.03 * (x[i1] - zero)))
        edge = find_edge(x, _smooth(y, max(5, int(2 * s))), lo,
                         min(hi, zero + 1.5 * predicted) if predicted else hi, s)
        if edge:
            edge["adu"] -= zero
            edge["steepest_adu"] -= zero
            res["edge"] = edge
    if peak:
        res["photon1"] = peak
        res["peak_adu"] = peak["mu"] - zero
    res["adu_per_photon"] = res.get("peak_adu") or (res.get("edge") or {}).get("adu")
    res["method"] = "peak" if res.get("peak_adu") else ("edge" if res["adu_per_photon"] else None)
    app = res["adu_per_photon"]
    if app and zero + 2.2 * app < ADU_HI:
        sel = (x > zero + 1.75 * app) & (x < zero + 2.25 * app)
        if ys[sel].max() > 3:
            i2 = int(np.argmax(np.where(sel, ys, -1)))
            if kind == "cluster" or ys[i2] > 1.3 * ys[sel][0]:
                p2 = fit_peak(x, y, x[i2], max(s_c, 0.05 * app))
                res["photon2"] = p2
                res["adu_per_photon_from_2"] = (p2["mu"] - zero) / 2.0
    return res


# ====================================================================== SACLA reading
def read_runs(args: Any) -> Dict[str, Any]:
    import dbpy
    import stpy

    from silas.core import db

    bl = args.bl
    panels_acc = {}  # type: Dict[str, PanelAccumulator]
    infos = {}  # type: Dict[str, Dict[str, Any]]
    runs_meta = []  # type: List[Dict[str, Any]]
    for run in args.runs:
        t0 = time.time()
        all_ids = list(dbpy.read_detidlist(bl, run))
        det_ids = sorted(d for d in all_ids if d.startswith(args.det + "-") and d[len(args.det) + 1:].isdigit())
        if not det_ids:
            det_ids = [d for d in all_ids if d == args.det]   # a single detector given by its full ID
        if not det_ids:
            print("run {}: no detector {}* (detectors: {})".format(run, args.det, ", ".join(dbpy.read_detidlist(bl, run))))
            continue
        energy = float(dbpy.read_config_photonenergy_in_keV(bl, run))
        taghi = db.high_tag(bl, run)
        tags = list(db.tags(bl, run))
        if len(tags) > args.tags:
            idx = np.linspace(0, len(tags) - 1, args.tags).astype(int)
            tags = [tags[i] for i in idx]
        shutter = db.read_point(XRAY_SHUTTER, taghi, tags, errors="nan")
        xray = [not (v == 0) for v in shutter]          # NaN (not recorded) counts as X-rays on
        try:
            status = dbpy.read_beamstatuslist(bl, taghi, tuple(int(t) for t in tags))
            beam_ok = [int(s) == 1 for s in status]
        except dbpy.APIError as err:
            print("  beam status not readable ({}): not used".format(err.args))
            beam_ok = [True] * len(tags)
        meta = {"run": run, "photon_energy_keV": energy, "n_tags_sampled": len(tags),
                "n_xray_off": int(sum(1 for v in xray if not v)), "n_beam_bad": int(sum(1 for v in beam_ok if not v)),
                "panels": det_ids}
        print("run {}: {} keV, {} tags sampled, X-ray shutter closed in {}, beam status not 1 in {}".format(
            run, energy, len(tags), meta["n_xray_off"], meta["n_beam_bad"]))
        for det_id in det_ids:
            reader = stpy.StorageReader(det_id, bl, (int(run),))
            buf = stpy.StorageBuffer(reader)
            acc = panels_acc.setdefault(det_id, PanelAccumulator(args.seed, args.ped_cut))
            run_acc = PanelAccumulator(args.seed, args.ped_cut)   # pedestal per run
            # pass 1: pedestal from the first --ped-tags X-ray shots of this run
            n_ped = 0
            for tag, on in zip(tags, xray):
                if n_ped >= args.ped_tags:
                    break
                try:
                    reader.collect(buf, int(tag))
                except stpy.APIError:
                    continue
                if det_id not in infos:
                    infos[det_id] = {k: (float(v) if isinstance(v, float) else v)
                                     for k, v in buf.read_det_info(0).items()}
                run_acc.add_pedestal(buf.read_det_data(0))
                n_ped += 1
            run_acc.finish_pedestal()
            acc.pedestal = run_acc.pedestal
            acc.sigma0 = acc.sigma0 or run_acc.sigma0
            acc.integer_fraction = run_acc.integer_fraction
            acc.dtype = run_acc.dtype
            acc.sigma = None                       # re-estimated with this run's pedestal
            # pass 2: histograms
            missing = 0
            for tag, on, ok in zip(tags, xray, beam_ok):
                if on and not ok:
                    continue
                try:
                    reader.collect(buf, int(tag))
                except stpy.APIError:
                    missing += 1
                    continue
                acc.add_shot(buf.read_det_data(0), xray=on)
            print("  {}: absgain {:.4f}, raw noise {:.1f} ADU, {} shots ({} missing), dtype {}, "
                  "integer-valued {:.0%}".format(det_id, infos.get(det_id, {}).get("mp_absgain", math.nan),
                                                  run_acc.sigma0 or math.nan, acc.n_shots["xray"], missing,
                                                  acc.dtype, acc.integer_fraction or 0))
        print("  run done in {:.0f} s".format(time.time() - t0))
        runs_meta.append(meta)
    return {"acc": panels_acc, "infos": infos, "runs": runs_meta}


# ====================================================================== report
def summarise(acc: Dict[str, PanelAccumulator], infos: Dict[str, Dict[str, Any]], energy_keV: float,
              use_prior: bool = True) -> Dict[str, Any]:
    out = {}  # type: Dict[str, Any]
    for det_id, a in sorted(acc.items()):
        g = float(infos.get(det_id, {}).get("mp_absgain", math.nan))
        pred = energy_keV * 1000.0 / (W_SI_EV * g) if g == g and g > 0 else None
        prior = pred if use_prior else None
        p = {"mp_absgain": g, "predicted_adu_per_photon_3.65": pred, "n_shots": a.n_shots,
             "n_clusters": a.n_clusters, "dtype": a.dtype, "integer_fraction": a.integer_fraction,
             "raw_noise_sigma": a.sigma0, "corrected_noise_sigma": a.sigma,
             "shot_offset_median": float(np.median(a.shot_offsets)) if a.shot_offsets else None,
             "shot_offset_std": float(np.std(a.shot_offsets)) if a.shot_offsets else None}  # type: Dict[str, Any]
        for kind in ("raw", "corrected", "cluster", "dark_corrected"):
            if a.h[kind].sum() == 0:
                continue
            r = analyse(a.h[kind], kind, a.sigma if kind != "raw" else a.sigma0, prior)
            app = r.get("adu_per_photon")
            if app:
                r["eV_per_adu"] = energy_keV * 1000.0 / app
                r["eV_per_absgain_adu"] = r["eV_per_adu"] / g if g == g and g > 0 else None
            p[kind] = r
        out[det_id] = p
    return out


def print_summary(summary: Dict[str, Any], energy: float) -> None:
    def f(v: Any, fmt: str = "{:.1f}") -> str:
        return fmt.format(v) if isinstance(v, (int, float)) and v == v else "-"

    print("\n" + "=" * 112)
    print("photon energy {:g} keV;  prediction: N_ph = ADU * absgain * 3.65 / E_eV  (CASS / Cheetah convention)".format(energy))
    hdr = "{:<20s} {:<15s} {:>6s} {:>8s} {:>7s} {:>8s} {:>8s} {:>9s} {:>8s} {:>8s} {:>10s}"
    print(hdr.format("panel", "histogram", "noise", "1ph ADU", "method", "peak", "edge", "2ph/2", "pred.",
                     "eV/ADU", "eV/(ADU*g)"))
    for det_id, p in summary.items():
        for kind in ("raw", "corrected", "cluster", "dark_corrected"):
            r = p.get(kind)
            if not r:
                continue
            print(hdr.format(det_id, kind, f(r.get("noise", {}).get("sigma")), f(r.get("adu_per_photon")),
                             str(r.get("method") or "-"), f(r.get("peak_adu")), f((r.get("edge") or {}).get("adu")),
                             f(r.get("adu_per_photon_from_2")), f(p.get("predicted_adu_per_photon_3.65")),
                             f(r.get("eV_per_adu"), "{:.3f}"), f(r.get("eV_per_absgain_adu"), "{:.3f}")))
            if r.get("candidates_adu"):
                app = r.get("adu_per_photon")
                print("{:<37s} peak candidates: {}".format("", ", ".join(
                    "{:.0f}{} ({:.1f}x)".format(c["adu"], " = {:.2f} keV".format(c["adu"] / app * energy) if app else "",
                                               c["prominence"]) for c in r["candidates_adu"][:6])))
    print("-" * 112)
    print("recommended: the cluster peak (all charge of a photon). A single pixel never reaches it fully")
    print("(charge sharing): its plateau edge = the largest fraction one pixel collects.")
    for det_id, p in summary.items():
        c = p.get("cluster") or {}
        e = (p.get("corrected") or {}).get("edge") or {}
        if c.get("adu_per_photon"):
            print("  {}: {:.1f} ADU per {:g} keV photon = {:.3f} eV/ADU = absgain x {:.3f} eV;  "
                  "single-pixel edge at {} of a photon".format(
                      det_id, c["adu_per_photon"], energy, c["eV_per_adu"], c.get("eV_per_absgain_adu") or math.nan,
                      "{:.0%}".format(e["adu"] / c["adu_per_photon"]) if e.get("adu") else "-"))
    print("=" * 112)


def plot(acc: Dict[str, PanelAccumulator], summary: Dict[str, Any], energy: float, path: str, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = np.arange(ADU_LO, ADU_HI + 1)
    n = len(acc)
    fig, axes = plt.subplots(n, 2, figsize=(15, 4.2 * n), squeeze=False)
    for row, (det_id, a) in enumerate(sorted(acc.items())):
        s = summary[det_id]
        app = (s.get("cluster") or {}).get("adu_per_photon") or (s.get("corrected") or {}).get("adu_per_photon") \
            or s.get("predicted_adu_per_photon_3.65") or 1000.0
        for col, xmax in enumerate((min(ADU_HI, 6.5 * app), min(ADU_HI, 1.6 * app))):
            ax = axes[row, col]
            for kind, color in (("raw", "0.6"), ("corrected", "C0"), ("cluster", "C3"), ("dark_corrected", "k")):
                h = a.h[kind]
                if h.sum() == 0:
                    continue
                sel = (x >= -min(400, 0.5 * app)) & (x <= xmax)
                ax.semilogy(x[sel], np.maximum(_smooth(h, 3)[sel], 0.3), color=color, lw=0.8, label=kind)
            for k in range(1, 7):
                if k * app < xmax:
                    ax.axvline(k * app, color="C2", ls=":", lw=0.8)
            pred = s.get("predicted_adu_per_photon_3.65")
            if pred:
                ax.axvline(pred, color="C1", ls="--", lw=0.8, label="absgain*3.65 prediction")
            ax.set_xlabel("ADU   (dotted: n x {:.0f} ADU)".format(app))
            ax.set_ylabel("pixels / clusters per ADU")
            ax.set_title("{}  absgain {:.3f}".format(det_id, s["mp_absgain"]), fontsize=10)
            sec = ax.secondary_xaxis("top", functions=(lambda v, a_=app: v * energy / a_,
                                                       lambda v, a_=app: v * a_ / energy))
            sec.set_xlabel("keV  (at {:.0f} ADU per {:g} keV photon)".format(app, energy))
            if row == 0 and col == 0:
                ax.legend(fontsize=8)
    fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def save(acc: Dict[str, PanelAccumulator], summary: Dict[str, Any], meta: Dict[str, Any], out_dir: str,
         det: str, energy: float) -> None:
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    arrays = {"adu0": np.array(ADU_LO)}
    for det_id, a in acc.items():
        for kind, h in a.h.items():
            if h.sum():
                arrays["{}__{}".format(det_id, kind)] = h
    np.savez_compressed(os.path.join(out_dir, "adu_hist_{}.npz".format(det)), **arrays)
    with open(os.path.join(out_dir, "adu_calib_{}.json".format(det)), "w") as fh:
        json.dump({"meta": meta, "panels": summary}, fh, indent=1, default=float)
    try:
        plot(acc, summary, energy, os.path.join(out_dir, "adu_hist_{}.png".format(det)),
             "{}  runs {}  {:g} keV".format(det, ", ".join(str(r["run"]) for r in meta["runs"]), energy))
    except Exception as err:  # noqa: BLE001  the numbers matter more than the picture
        print("plot failed:", err)
    print("results in", out_dir)


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="MPCCD ADU per photon from pixel histograms (read-only)")
    ap.add_argument("runs", type=int, nargs="+", help="run numbers (e.g. the first, middle and last step of a scan)")
    ap.add_argument("--bl", type=int, default=3)
    ap.add_argument("--det", required=True, help="detector ID or panel prefix, e.g. MPCCD-8N0-3-002")
    ap.add_argument("--tags", type=int, default=300, help="shots sampled per run (default 300)")
    ap.add_argument("--ped-tags", type=int, default=100, help="shots for the per-pixel pedestal (default 100)")
    ap.add_argument("--seed", type=float, default=5.0, help="cluster seed in noise sigmas (default 5)")
    ap.add_argument("--ped-cut", type=float, default=4.0, help="photon-free cut for the pedestal, sigmas")
    ap.add_argument("--no-prior", action="store_true",
                    help="search the whole ADU range (default: 0.4-2.5 x the absgain*3.65 prediction)")
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "adu_calib"))
    args = ap.parse_args(argv)
    import silas  # noqa: F401  applies the profile's sys.path

    t0 = time.time()
    res = read_runs(args)
    if not res["acc"]:
        print("no data read")
        return
    energies = sorted(set(r["photon_energy_keV"] for r in res["runs"]))
    if len(energies) > 1:
        print("WARNING: different photon energies {} -> using the first".format(energies))
    energy = energies[0]
    summary = summarise(res["acc"], res["infos"], energy, not args.no_prior)
    print_summary(summary, energy)
    meta = {"runs": res["runs"], "det": args.det, "infos": res["infos"], "photon_energy_keV": energy,
            "args": vars(args), "seconds": time.time() - t0}
    save(res["acc"], summary, meta, args.out, args.det, energy)


if __name__ == "__main__":
    main()
