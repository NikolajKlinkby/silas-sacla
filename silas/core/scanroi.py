"""ROI sums of a scan: per run (= scan step) and pump state (shared by online and offline).

Per run and pump state, for every ROI: the sum of the deposited energy over shots (keV),
the sum of its squares (shot-to-shot spread) and the number of shots. Workers add shots
and send what they collected (take()), the display merges it (merge()); means are
computed only for showing:

    mean per pixel per shot   m = S / (N * P)                    keV
    Poisson error             sqrt(S * E) / (N * P)              E = photon energy (keV):
                                                                 S / E photons, error sqrt(S / E) photons
    shot-to-shot error        sqrt((Q / N - (S / N)^2) / N) / P  Q = sum of squares
    ratio pump on / off       m_on / m_off, errors added in quadrature

Pump states: with a pump detector (config scan.pump_detector) state 0 = pump on and
1 = pump off; without one there is only state 0 = all shots.
"""
import warnings
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

PUMP_KEY = "scan.pump_detector"          # point detector (alias or true name) of the pump state
PUMP_ON_KEY = "scan.pump_on_value"       # its value when the pump hits the sample
DEFAULT_PUMP = "optical_shutter"
REFERENCES_KEY = "scan.references"       # reference point detectors (i0): a name or a list of names


def reference_settings(cfg: Any) -> List[Tuple[str, str]]:
    """[(label, true name)] of the reference detectors (config scan.references; none set: []).
    The label is the name as given (an alias), with "/" replaced, so it can be a column name.
    ValueError if one is not a point detector of the config."""
    from silas.core import names

    given = cfg.get(REFERENCES_KEY, None) or []
    if isinstance(given, str):
        given = [given]
    configured = cfg.get("point_detectors", {})
    out = []  # type: List[Tuple[str, str]]
    for g in given:
        true = names.resolve(cfg, str(g))
        if true not in configured:
            raise ValueError("reference {!r} is not a point detector of the config (add_point_detector "
                             "first, or set_references(...) with another)".format(g))
        out.append((str(g).replace("/", "_"), true))
    return out


def pump_settings(cfg: Any) -> Tuple[Optional[str], float]:
    """(true name of the pump detector or None, value meaning pump on) from the config."""
    from silas.core import names

    det = cfg.get(PUMP_KEY, DEFAULT_PUMP)
    on = float(cfg.get(PUMP_ON_KEY, 1))
    if not det:
        return None, on
    return names.resolve(cfg, det), on


COUNTERS = ("done", "lost", "xray_off", "no_pump")
"""Shots per run: done = added; lost = no longer in the online buffer / not readable;
xray_off = X-rays off or bad beam (dropped); no_pump = pump state unknown (dropped)."""


class ScanSums(object):
    """Sums of one scan (or of the part one worker has read since its last take())."""

    def __init__(self, n_rois: int, n_states: int) -> None:
        self.n_rois = int(n_rois)
        self.n_states = int(n_states)
        self.runs = {}  # type: Dict[int, Dict[str, Any]]

    def _run(self, run: int) -> Dict[str, Any]:
        r = self.runs.get(run)
        if r is None:
            r = {"S": np.zeros((self.n_states, self.n_rois)), "Q": np.zeros((self.n_states, self.n_rois)),
                 "N": np.zeros(self.n_states, np.int64)}
            for c in COUNTERS:
                r[c] = 0
            self.runs[run] = r
        return r

    def add_shot(self, run: int, state: int, roi_kev: np.ndarray) -> None:
        r = self._run(run)
        v = np.asarray(roi_kev, np.float64)
        r["S"][state] += v
        r["Q"][state] += v * v
        r["N"][state] += 1
        r["done"] += 1

    def count(self, run: int, counter: str, n: int = 1) -> None:
        self._run(run)[counter] += int(n)

    def merge(self, other: "ScanSums") -> None:
        for run, o in other.runs.items():
            r = self._run(run)
            for k in ("S", "Q", "N") + COUNTERS:
                r[k] = r[k] + o[k]

    def take(self) -> "ScanSums":
        """What was collected so far; this object starts empty again."""
        out = ScanSums(self.n_rois, self.n_states)
        out.runs, self.runs = self.runs, {}
        return out

    def totals(self) -> Dict[str, int]:
        return {c: int(sum(r[c] for r in self.runs.values())) for c in COUNTERS}


def step_table(sums: ScanSums, runs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Combine runs of the same scan step (a repeated step is several runs).

    runs: [{"run", "step", "value"}] of the scan (value = scanned motor position or NaN).
    Returns one dict per step, sorted by x: {"x", "step", "value", "runs", "S", "Q", "N", counters}."""
    steps = {}  # type: Dict[Any, Dict[str, Any]]
    for info in runs:
        run = int(info["run"])
        key = info.get("step")
        key = int(key) if key is not None and key == key else ("run", run)
        st = steps.get(key)
        if st is None:
            st = {"step": key if not isinstance(key, tuple) else None, "value": info.get("value"),
                  "runs": [], "S": np.zeros((sums.n_states, sums.n_rois)),
                  "Q": np.zeros((sums.n_states, sums.n_rois)), "N": np.zeros(sums.n_states, np.int64)}
            for c in COUNTERS:
                st[c] = 0
            steps[key] = st
        st["runs"].append(run)
        r = sums.runs.get(run)
        if r is not None:
            for k in ("S", "Q", "N") + COUNTERS:
                st[k] = st[k] + r[k]
    out = list(steps.values())
    use_value = bool(out) and all(s["value"] is not None and s["value"] == s["value"] for s in out)
    for s in out:
        s["x"] = float(s["value"]) if use_value else float(s["step"] if s["step"] is not None else s["runs"][0])
    out.sort(key=lambda s: s["x"])
    for s in out:
        s["x_is_value"] = use_value
    return out


def means(table: List[Dict[str, Any]], n_pixels: Sequence[int], photon_keV: Optional[float],
          error: str = "poisson") -> Dict[str, np.ndarray]:
    """Arrays for plotting from step_table(): x (n_steps,), mean and err (n_states, n_steps, n_rois)
    in keV per pixel per shot, n (n_states, n_steps). Steps without shots give NaN.
    error: "poisson" (needs the photon energy, else NaN) or "spread" (shot-to-shot)."""
    if not table:
        return {"x": np.zeros(0), "mean": np.zeros((0, 0, 0)), "err": np.zeros((0, 0, 0)), "n": np.zeros((0, 0))}
    P = np.asarray(n_pixels, np.float64)[None, None, :]
    S = np.stack([s["S"] for s in table], axis=1)            # (states, steps, rois)
    Q = np.stack([s["Q"] for s in table], axis=1)
    N = np.stack([s["N"] for s in table], axis=1).astype(np.float64)[:, :, None]
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where((N > 0) & (P > 0), S / (N * P), np.nan)
        if error == "spread":
            var = np.maximum(Q / N - (S / N) ** 2, 0.0)
            err = np.where((N > 1) & (P > 0), np.sqrt(var / N) / P, np.nan)
        elif photon_keV:
            err = np.where((N > 0) & (P > 0), np.sqrt(np.maximum(S, 0.0) * float(photon_keV)) / (N * P), np.nan)
        else:
            err = np.full(mean.shape, np.nan)
    return {"x": np.array([s["x"] for s in table]), "mean": mean, "err": err, "n": N[:, :, 0]}


def ratio(mean: np.ndarray, err: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Pump on / pump off (states 0 / 1) with the error from both: (ratio, err), (steps, rois)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        r = mean[0] / mean[1]
        e = np.abs(r) * np.sqrt((err[0] / mean[0]) ** 2 + (err[1] / mean[1]) ** 2)
    return r, e


# ====================================================================== offline: quantiles of single shots
QUANTILES = (0.1587, 0.5, 0.8413)
"""Point = median, error bars = these quantiles (the +-1 sigma range of a normal distribution)."""


def quantile_steps(shots: Any, roi_cols: Sequence[str], runs: Sequence[Dict[str, Any]], n_states: int,
                   q: Sequence[float] = QUANTILES) -> List[Dict[str, Any]]:
    """Per step (repeated steps combined) and pump state: quantiles of the per-shot ROI values.

    shots      pandas DataFrame, one row per USED shot, columns "run", "state" and ``roi_cols``
               (keV per pixel of the shot)
    runs       [{"run", "step", "value"}] of the scan (as for step_table)
    Returns one dict per step, sorted by x: {"x", "x_is_value", "step", "value", "runs",
    "n": (n_states,), "q": (n_states, len(q), n_rois)} with NaN where a state has no shot."""
    template = step_table(ScanSums(len(roi_cols), n_states), runs)   # steps, x and their runs
    out = []  # type: List[Dict[str, Any]]
    for st in template:
        part = shots[shots["run"].isin(st["runs"])]
        qa = np.full((n_states, len(q), len(roi_cols)), np.nan)
        n = np.zeros(n_states, np.int64)
        for s in range(n_states):
            vals = part.loc[part["state"] == s, list(roi_cols)].to_numpy(dtype=np.float64)
            n[s] = len(vals)
            if len(vals):
                qa[s] = np.quantile(vals, list(q), axis=0)
        out.append({"x": st["x"], "x_is_value": st["x_is_value"], "step": st["step"], "value": st["value"],
                    "runs": st["runs"], "n": n, "q": qa})
    return out


def quantile_arrays(table: List[Dict[str, Any]]) -> Dict[str, np.ndarray]:
    """For plotting: x (steps,), med, lo, hi (states, steps, rois) where lo / hi are the
    distances from the median to the lower / upper quantile (matplotlib's asymmetric yerr)."""
    if not table:
        z = np.zeros((0, 0, 0))
        return {"x": np.zeros(0), "med": z, "lo": z, "hi": z, "n": np.zeros((0, 0))}
    qa = np.stack([s["q"] for s in table], axis=1)          # (states, steps, 3, rois)
    med = qa[:, :, 1, :]
    return {"x": np.array([s["x"] for s in table]), "med": med, "lo": med - qa[:, :, 0, :],
            "hi": qa[:, :, 2, :] - med, "n": np.stack([s["n"] for s in table], axis=1)}


def ratio_of_medians(a: np.ndarray, a_lo: np.ndarray, a_hi: np.ndarray,
                     b: np.ndarray, b_lo: np.ndarray, b_hi: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """a / b with lower / upper errors (distances): the relative errors of both are added in
    quadrature; the lower error of the ratio from the lower error of a and the upper error of
    b, and the other way round."""
    with np.errstate(invalid="ignore", divide="ignore"):
        r = a / b
        lo = np.abs(r) * np.sqrt((a_lo / a) ** 2 + (b_hi / b) ** 2)
        hi = np.abs(r) * np.sqrt((a_hi / a) ** 2 + (b_lo / b) ** 2)
    return r, lo, hi


def quantile_ratio(a: Dict[str, np.ndarray]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pump on / off of the medians (steps, rois) with lower / upper errors (ratio_of_medians)."""
    return ratio_of_medians(a["med"][0], a["lo"][0], a["hi"][0], a["med"][1], a["lo"][1], a["hi"][1])


def nearest_off(tags_on: Sequence[int], tags_off: Sequence[int]) -> np.ndarray:
    """For every pump-on tag the index (into tags_off, which must be sorted) of the nearest
    pump-off tag; equally near: the earlier one."""
    on = np.asarray(tags_on, np.int64)
    off = np.asarray(tags_off, np.int64)
    j = np.searchsorted(off, on)
    left = np.clip(j - 1, 0, len(off) - 1)
    right = np.clip(j, 0, len(off) - 1)
    return np.where(np.abs(off[right] - on) < np.abs(on - off[left]), right, left)


def pair_quantiles(shots: Any, cols: Sequence[str], runs: Sequence[Dict[str, Any]],
                   q: Sequence[float] = QUANTILES) -> Dict[str, np.ndarray]:
    """On / off shot by shot: every pump-on shot divided by the nearest pump-off shot (by tag) of
    the same run, then per step (repeated steps combined) the quantiles of these ratios.

    shots   pandas DataFrame of the shots to use: "run", "tag", "state" (0 on, 1 off) and ``cols``
    Returns x (steps,), med, lo, hi (steps, cols) with lo / hi as distances from the median, and
    n (steps,) the number of pairs. NaN where a step has no pair."""
    template = step_table(ScanSums(len(cols), 2), runs)
    med = np.full((len(template), len(cols)), np.nan)
    lo = np.full_like(med, np.nan)
    hi = np.full_like(med, np.nan)
    n = np.zeros(len(template), np.int64)
    for i, st in enumerate(template):
        ratios = []  # type: List[np.ndarray]
        for run in st["runs"]:
            part = shots[shots["run"] == run]
            on = part[part["state"] == 0].sort_values("tag")
            off = part[part["state"] == 1].sort_values("tag")
            if not len(on) or not len(off):
                continue
            j = nearest_off(on["tag"].to_numpy(), off["tag"].to_numpy())
            with np.errstate(invalid="ignore", divide="ignore"):
                ratios.append(on[list(cols)].to_numpy(np.float64) / off[list(cols)].to_numpy(np.float64)[j])
        if ratios:
            vals = np.concatenate(ratios, axis=0)
            n[i] = len(vals)
            with warnings.catch_warnings():       # 0 / 0 (e.g. empty ROI after the threshold): NaN
                warnings.simplefilter("ignore", RuntimeWarning)
                qa = np.nanquantile(vals, list(q), axis=0)
            med[i], lo[i], hi[i] = qa[1], qa[1] - qa[0], qa[2] - qa[1]
    return {"x": np.array([s["x"] for s in template]), "med": med, "lo": lo, "hi": hi, "n": n}
