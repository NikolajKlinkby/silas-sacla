"""Offline scan analysis (HPC, stpy): ROI signal of a whole logged scan, with quantiles.

    analyse_scan(-1)                                       # from the session (newest scan, 6 workers)
    python -m silas.offline.scan_analysis --scan -1 --workers 6
    python -m silas.offline.scan_analysis --scan 1234501 --max-tags 100     # quick look
    mpirun -n 32 python -m silas.offline.scan_analysis --scan -1           # MPI also works

Parallel: --workers N starts N Python processes on this node (multiprocessing, no MPI
needed), at most the cores of the job; default 6 (runner.OFFLINE_WORKERS). Each process takes whole runs: it reads the run's
shot flags once (dbpy), opens its stpy readers once, and reads the images of the run's used
shots. Started with mpirun, the MPI processes share the shots instead. Measured on the HPC
(tests/probe_scan_speed.py, a two-panel MPCCD): stpy needs about 5 ms per panel and shot, and
reading stops getting faster at about 1100 shots/s (4 processes): that is the limit.

The scan is chosen as in scan_info(): -1 the newest scan of the run log, -2 the one before,
0 the first, or any run number of the scan. For every shot:

  * X-rays off (laser_shutter = 0) or bad beam status: kept in the table, not used
  * pump state from the config's scan.pump_detector (value scan.pump_on_value = on); none set:
    every shot in one group
  * the reference detectors of the config (scan.references, set_references(...), e.g. i0):
    their values are stored with the shot; a shot without a value for a reference is left
    out of everything computed with that reference
  * only the panels the ROIs cover are read; the ROI pixels are corrected (dark if set, the
    keV threshold) and summed: keV per pixel of the shot, for every ROI

Per step (repeated steps combined) and pump state, the shots' values give the point and
the error bars as quantiles (0.1587, 0.5, 0.8413): the median and the range of the middle
68.3 % of the shots, i.e. the spread of the shots (not divided by sqrt(n)). Computed:

  ROI                       quantiles of the ROI values
  reference R               quantiles of R
  ROI / R per shot          quantiles of ROI / R of every shot
  on / off                  median on / median off: unreferenced and per shot referenced (for
                            every R); errors: relative errors added in quadrature
  options (--step-reference, --pairs; analyse_scan(step_reference=True, pairs=True)):
  ROI / R per step          median(ROI) / median(R) (shots with R only), errors in quadrature,
                            and its on / off
  on / off pairs            every pump-on shot divided by the nearest pump-off shot of its run,
                            then the quantiles of these ratios: unreferenced and per shot
                            referenced (for every R)

Written to <output>/scans/scan_<id>/:
  scan_<id>.h5  everything: the shot table, the step table, the settings (attribute "info")
  shots.csv     one row per shot (run, tag, step, value, use, state, references, ROI values)
  steps.csv     per step: everything above (the quick look)
  info.json     scan, ROIs (pixels, panels), references, corrections, photon energy, timing
  plot.png      one column: ROI, ROI / R per shot (per step), on / off (see plot())
"""
import argparse
import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import db, energy, mpccd, names, roi, sacla, scan, scanroi
from silas.core.config import Config

FLAG_CHUNK = 2000
XRAY_SHUTTER = "laser_shutter"
TIMERS = ("flags", "images", "roi_sums")


# ====================================================================== reading (stpy)
class PanelReader(object):
    """stpy readers of the array's panels, one per (panel, run), made when first needed."""

    def __init__(self, bl: int, det_ids: Sequence[str]) -> None:
        self.bl = bl
        self.det_ids = list(det_ids)
        self._readers = {}  # type: Dict[Tuple[int, int], Any]

    def _get(self, k: int, run: int) -> Any:
        import stpy

        key = (k, run)
        if key not in self._readers:
            reader = stpy.StorageReader(self.det_ids[k], self.bl, (int(run),))
            self._readers[key] = (reader, stpy.StorageBuffer(reader))
        return self._readers[key]

    def info(self, k: int, run: int, tag: int) -> Dict[str, Any]:
        reader, buf = self._get(k, run)
        reader.collect(buf, int(tag))
        return dict(buf.read_det_info(0))

    def read(self, run: int, tag: int, panels: Sequence[int]) -> Optional[Dict[int, np.ndarray]]:
        """{panel index: image} of a shot, None if a panel has no data for it."""
        import stpy

        out = {}
        for k in panels:
            reader, buf = self._get(k, run)
            try:
                reader.collect(buf, int(tag))
            except stpy.APIError:
                return None
            out[k] = buf.read_det_data(0)
        return out

    def close_run(self, run: int) -> None:
        """Forget the readers of a run (a worker goes through its runs one by one)."""
        for key in [k for k in self._readers if k[1] == run]:
            del self._readers[key]


def _point(name: Optional[str], taghi: int, tags: Sequence[int]) -> np.ndarray:
    """Values of a point detector (NaN where not recorded, or all NaN if not readable)."""
    if not name:
        return np.full(len(tags), np.nan)
    out = []  # type: List[float]
    for i in range(0, len(tags), FLAG_CHUNK):
        out.extend(db.read_point(name, taghi, tags[i:i + FLAG_CHUNK], errors="nan"))
    return np.array(out, np.float64)


def shot_flags(cfg: Config, bl: int, run: int, taghi: int, tags: Sequence[int]) -> Dict[str, np.ndarray]:
    """Per shot: use (X-rays on and beam OK), state (0 on, 1 off, -1 unknown; 0 without a pump
    detector), and "ref_<label>" for every reference detector (NaN where not recorded)."""
    import dbpy

    xray = _point(names.resolve(cfg, XRAY_SHUTTER), taghi, tags)
    use = ~(xray == 0)
    beam = np.ones(len(tags), bool)
    for i in range(0, len(tags), FLAG_CHUNK):
        chunk = tuple(int(t) for t in tags[i:i + FLAG_CHUNK])
        try:
            beam[i:i + len(chunk)] = np.array(dbpy.read_beamstatuslist(bl, taghi, chunk)) == 1
        except dbpy.APIError:
            pass
    use &= beam
    pump, on_value = scanroi.pump_settings(cfg)
    if pump:
        p = _point(pump, taghi, tags)
        state = np.where(np.isnan(p), -1, np.where(p == on_value, 0, 1)).astype(np.int64)
    else:
        state = np.zeros(len(tags), np.int64)
    out = {"use": use, "state": state}
    for label, true in scanroi.reference_settings(cfg):
        out["ref_" + label] = _point(true, taghi, tags)
    return out


def process(cfg: Config, settings: Dict[str, Any], items: Sequence[Tuple[int, int]],
            run_info: Dict[int, Dict[str, Any]], infos: List[Dict[str, Any]],
            reader: Any = None, progress: Optional[str] = None,
            timing: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
    """Rows of the shot table for ``items`` [(run, tag), ...] (this process's share).
    timing: a dict to add the seconds spent on flags, images and ROI sums to."""
    det_ids = list(settings["panels"])
    rmap, kev, corr = roi.analysis_setup(det_ids, infos, settings, shift_from_data=True)
    if rmap is None:
        return []
    reader = reader or PanelReader(cfg.beamline, det_ids)
    thr = corr.threshold_adu if corr.settings.get("threshold") else None
    npx = np.asarray(rmap.n_pixels, np.float64)
    cols = ["roi_" + n for n in rmap.names]
    ref_cols = ["ref_" + label for label, _ in scanroi.reference_settings(cfg)]
    clock = timing if timing is not None else {}
    for k in TIMERS:
        clock.setdefault(k, 0.0)
    rows = []  # type: List[Dict[str, Any]]
    by_run = {}  # type: Dict[int, List[int]]
    for run, tag in items:
        by_run.setdefault(int(run), []).append(int(tag))
    t0, done = time.time(), 0
    for run, tags in sorted(by_run.items()):
        info = run_info[run]
        t = time.perf_counter()
        taghi = int(info.get("taghi") or db.high_tag(cfg.beamline, run))
        flags = shot_flags(cfg, cfg.beamline, run, taghi, tags)
        clock["flags"] += time.perf_counter() - t
        for i, tag in enumerate(tags):
            row = {"run": run, "tag": tag, "step": info.get("step"), "value": info.get("value"),
                   "use": bool(flags["use"][i]), "state": int(flags["state"][i]),
                   "read": False}  # type: Dict[str, Any]
            for c in ref_cols:
                row[c] = float(flags[c][i])
            values = [math.nan] * len(cols)  # type: List[Any]
            if row["use"] and row["state"] >= 0:
                t = time.perf_counter()
                data = reader.read(run, tag, rmap.panels)
                clock["images"] += time.perf_counter() - t
                if data is not None:
                    t = time.perf_counter()
                    sums = rmap.sums(data, kev, corr.dark, thr)
                    with np.errstate(invalid="ignore", divide="ignore"):
                        values = list(np.where(npx > 0, sums / npx, np.nan))
                    clock["roi_sums"] += time.perf_counter() - t
                    row["read"] = True
            row.update(zip(cols, values))
            rows.append(row)
            done += 1
            if progress and done % 2000 == 0:
                print("{} {} / {} shots ({:.0f} shots/s)".format(progress, done, len(items),
                                                                  done / max(time.time() - t0, 1e-6)), flush=True)
        if isinstance(reader, PanelReader):
            reader.close_run(run)
    return rows


def _work(payload: Tuple[Optional[str], List[Tuple[int, int]], Dict[int, Dict[str, Any]],
                          List[Dict[str, Any]], str]) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
    """One multiprocessing worker: process() for its runs (runs in a fresh Python)."""
    name, items, run_info, infos, label = payload
    cfg = Config()
    _, settings = mpccd.get_array(cfg, name)
    timing = {}  # type: Dict[str, float]
    rows = process(cfg, settings, items, run_info, infos, progress=label, timing=timing)
    return rows, timing


def split_by_run(items: Sequence[Tuple[int, int]], n: int) -> List[List[Tuple[int, int]]]:
    """Whole runs per worker, about the same number of shots each (largest runs first)."""
    by_run = {}  # type: Dict[int, List[Tuple[int, int]]]
    for run, tag in items:
        by_run.setdefault(int(run), []).append((int(run), int(tag)))
    parts = [[] for _ in range(n)]  # type: List[List[Tuple[int, int]]]
    for run in sorted(by_run, key=lambda r: -len(by_run[r])):
        smallest = min(range(n), key=lambda w: len(parts[w]))
        parts[smallest].extend(by_run[run])
    return [p for p in parts if p]


def run_parallel(name: Optional[str], items: List[Tuple[int, int]], run_info: Dict[int, Dict[str, Any]],
                 infos: List[Dict[str, Any]], n: int,
                 timing: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
    """process() over n processes on this node (multiprocessing, "spawn": every worker opens its
    own stpy / dbpy connections), whole runs per process. Returns all rows; adds the workers'
    seconds (summed over the workers) to ``timing``."""
    import multiprocessing

    from silas.core import runner

    ctx = multiprocessing.get_context("spawn")
    blocks = split_by_run(items, n)
    payloads = [(name, b, run_info, infos, "[worker {}]".format(i)) for i, b in enumerate(blocks)]
    with ctx.Pool(len(payloads), initializer=runner.worker_ignores_ctrl_c) as pool:
        parts = pool.map(_work, payloads)
    if timing is not None:
        for _, t in parts:
            for k, v in t.items():
                timing[k] = timing.get(k, 0.0) + v
    return [r for rows, _ in parts for r in rows]


def available_cores() -> int:
    """Cores this process may use (runner.available_cores)."""
    from silas.core import runner

    return runner.available_cores()


# ====================================================================== results
def _subset_arrays(shots: Any, cols: Sequence[str], runs: Sequence[Dict[str, Any]],
                   n_states: int) -> Dict[str, np.ndarray]:
    return scanroi.quantile_arrays(scanroi.quantile_steps(shots, cols, runs, n_states))


def results(shots: Any, roi_names: Sequence[str], refs: Sequence[str], runs: Sequence[Dict[str, Any]],
            n_states: int, step_reference: bool = False, pairs: bool = False) -> Dict[str, Any]:
    """Everything of the analysis per step (see the module text). Arrays with lo / hi as
    distances from the median: states x steps x ROIs, or steps x ROIs for on / off.
    step_reference: also median(ROI) / median(R) per step; pairs: also on / off of nearest-off pairs."""
    rois = ["roi_" + n for n in roi_names]
    used = shots[shots["read"]] if len(shots) else shots
    table = scanroi.quantile_steps(used, rois, runs, n_states)
    raw = scanroi.quantile_arrays(table)
    out = {"table": table, "rois": list(roi_names), "refs": list(refs), "n_states": n_states,
           "raw": raw, "ref": {}, "step_reference": bool(step_reference), "pairs": bool(pairs)}  # type: Dict[str, Any]
    two = n_states == 2 and len(table) > 0
    if two:
        out["raw_onoff"] = scanroi.quantile_ratio(raw)
        if pairs:
            out["raw_pairs"] = scanroi.pair_quantiles(used, rois, runs)
    for label in refs:
        rc = "ref_" + label
        sub = used[np.isfinite(used[rc].to_numpy(np.float64))].copy()
        per_shot = []  # type: List[str]
        for c in rois:
            col = "{}_per_{}".format(c, label)
            with np.errstate(invalid="ignore", divide="ignore"):
                sub[col] = sub[c].to_numpy(np.float64) / sub[rc].to_numpy(np.float64)
            per_shot.append(col)
        a_ref = _subset_arrays(sub, [rc], runs, n_states)
        shot = _subset_arrays(sub, per_shot, runs, n_states)
        r = {"ref": a_ref, "shot": shot}  # type: Dict[str, Any]
        if two:
            r["shot_onoff"] = scanroi.quantile_ratio(shot)
            if pairs:
                r["pairs"] = scanroi.pair_quantiles(sub, per_shot, runs)
        if step_reference:
            a_roi = _subset_arrays(sub, rois, runs, n_states)
            step = scanroi.ratio_of_medians(a_roi["med"], a_roi["lo"], a_roi["hi"],
                                            a_ref["med"], a_ref["lo"], a_ref["hi"])
            r["step"] = {"med": step[0], "lo": step[1], "hi": step[2]}
            if two:
                r["step_onoff"] = scanroi.ratio_of_medians(step[0][0], step[1][0], step[2][0],
                                                           step[0][1], step[1][1], step[2][1])
        out["ref"][label] = r
    return out


def steps_frame(res: Dict[str, Any]) -> Any:
    """The step table (steps.csv): one row per step, columns as named in the module text."""
    import pandas as pd

    table = res["table"]
    n_states = res["n_states"]
    labels = ["on", "off"] if n_states == 2 else ["all"]
    qn = ("q16", "median", "q84")
    rows = []  # type: List[Dict[str, Any]]
    for j, st in enumerate(table):
        row = {"x": st["x"], "step": st["step"], "value": st["value"],
               "runs": ";".join(str(r) for r in st["runs"])}  # type: Dict[str, Any]
        for s, lab in enumerate(labels):
            row["n_" + lab] = int(st["n"][s])
        for i, name in enumerate(res["rois"]):
            for s, lab in enumerate(labels):
                for k, qk in enumerate(qn):
                    row["{}_{}_{}".format(name, lab, qk)] = st["q"][s, k, i]
        rows.append(row)
    df = pd.DataFrame(rows)
    if not len(df):
        return df
    extra = {}  # type: Dict[str, Any]

    def bounds(prefix: str, med: np.ndarray, lo: np.ndarray, hi: np.ndarray, low: str, high: str,
               mid: str = "") -> None:
        extra[prefix + mid] = med
        extra[prefix + low] = med - lo
        extra[prefix + high] = med + hi

    two = n_states == 2
    if two:
        r, lo, hi = res["raw_onoff"]
        for i, name in enumerate(res["rois"]):
            bounds(name + "_ratio", r[:, i], lo[:, i], hi[:, i], "_lo", "_hi")
        if "raw_pairs" in res:
            p = res["raw_pairs"]
            extra["n_pairs"] = p["n"]
            for i, name in enumerate(res["rois"]):
                bounds(name + "_pair_ratio", p["med"][:, i], p["lo"][:, i], p["hi"][:, i], "_q16", "_q84")
    for label, rr in res["ref"].items():
        a_ref = rr["ref"]
        for s, lab in enumerate(labels):
            extra["n_{}_{}".format(lab, label)] = a_ref["n"][s]
            bounds("{}_{}".format(label, lab), a_ref["med"][s, :, 0], a_ref["lo"][s, :, 0], a_ref["hi"][s, :, 0],
                   "_q16", "_q84", mid="_median")
        for i, name in enumerate(res["rois"]):
            base = "{}_per_{}".format(name, label)
            for s, lab in enumerate(labels):
                if "step" in rr:
                    st = rr["step"]
                    bounds("{}_step_{}".format(base, lab), st["med"][s, :, i], st["lo"][s, :, i],
                           st["hi"][s, :, i], "_lo", "_hi")
                sh = rr["shot"]
                bounds("{}_shot_{}".format(base, lab), sh["med"][s, :, i], sh["lo"][s, :, i], sh["hi"][s, :, i],
                       "_q16", "_q84")
            if two:
                for kind in ("step", "shot"):
                    if kind + "_onoff" in rr:
                        r, lo, hi = rr[kind + "_onoff"]
                        bounds("{}_{}_ratio".format(base, kind), r[:, i], lo[:, i], hi[:, i], "_lo", "_hi")
                if "pairs" in rr:
                    p = rr["pairs"]
                    bounds(base + "_pair_ratio", p["med"][:, i], p["lo"][:, i], p["hi"][:, i], "_q16", "_q84")
        if "pairs" in rr:
            extra["n_pairs_" + label] = rr["pairs"]["n"]
    return pd.concat([df, pd.DataFrame(extra, index=df.index)], axis=1)


def summarise(shots: Any, roi_names: Sequence[str], runs: Sequence[Dict[str, Any]], n_states: int,
              refs: Sequence[str] = ()) -> Tuple[Any, Any]:
    """(quantile table of the ROIs, steps DataFrame) from the shot table."""
    res = results(shots, roi_names, refs, runs, n_states)
    return res["table"], steps_frame(res)


def write_h5(path: str, shots: Any, steps: Any, info: Dict[str, Any], bins: Any = None, off_steps: Any = None) -> bool:
    """The shot table, the step table and the info (JSON attribute) in one HDF5 file.
    False (with a message) if h5py is missing."""
    try:
        import h5py
    except ImportError:
        print("h5py is not installed here: no .h5 file (the .csv files are written)")
        return False

    def put(group: Any, frame: Any) -> None:
        for col in frame.columns:
            data = frame[col].to_numpy()
            if data.dtype == object:          # numbers with None (e.g. motor value): float with NaN
                try:
                    data = np.array([np.nan if v is None else float(v) for v in data], np.float64)
                except (TypeError, ValueError):
                    data = np.array([str(v) for v in data], dtype="S")
            group.create_dataset(str(col), data=data)

    tmp = path + ".tmp"
    with h5py.File(tmp, "w") as f:
        put(f.create_group("shots"), shots)
        put(f.create_group("steps"), steps)
        if bins is not None:
            put(f.create_group("bins"), bins)
        if off_steps is not None:
            put(f.create_group("off_steps"), off_steps)
        f.attrs["info"] = json.dumps(info, default=str)
        f.attrs["quantiles"] = np.asarray(scanroi.QUANTILES)
    os.replace(tmp, path)
    return True


def plot(res: Dict[str, Any], title: str, xlabel: str, path: Optional[str], show: bool = False,
         ref_units: Optional[Dict[str, str]] = None, x_scale: float = 1.0) -> None:
    """One column of panels, sharing the x axis; pump on solid, pump off dashed; colour = ROI.

      ROI                      the ROI values (keV per pixel per shot)
      ROI / R per shot         one panel per reference: median of ROI / R of the shots
      ROI / R per step         (step_reference) one panel per reference: median ROI / median R
      on / off                 ratio of the medians, per shot referenced (one marker per reference;
                               unreferenced only when no reference is set); per step referenced
                               (step_reference, dash-dot); nearest-off pairs (pairs, dotted)"""
    import matplotlib

    from silas.core import plotting

    plt = None  # type: Any
    if show:
        try:
            plt = plotting.pyplot()
        except RuntimeError as err:
            print("plot window not possible ({}); the plot is only saved".format(err))
            show = False
    if not show:
        matplotlib.use("Agg")
        import matplotlib.pyplot as pyplot_agg

        plt = pyplot_agg

    refs = list(res["refs"])
    units = ref_units or {}
    two = res["n_states"] == 2 and "raw_onoff" in res
    markers = ["s", "^", "D", "v", "P", "X", "*", "h"]
    panels = [("raw", None)]  # type: List[Tuple[str, Optional[str]]]
    panels += [("shot", r) for r in refs]
    if res.get("step_reference"):
        panels += [("step", r) for r in refs]
    n_rows = len(panels) + (1 if two else 0)
    ratios = [3.0] * len(panels) + ([2.6] if two else [])
    fig, axes = plt.subplots(n_rows, 1, figsize=(12, 2.9 * len(panels) + (3.2 if two else 0) + 1.2), sharex=True,
                             squeeze=False, gridspec_kw={"height_ratios": ratios})
    fig.subplots_adjust(left=0.1, right=0.76, hspace=0.12, top=0.95)
    fig.suptitle(title)
    x = res["raw"]["x"] * float(x_scale)

    def traces(ax: Any, med: np.ndarray, lo: np.ndarray, hi: np.ndarray, marker: str = "o") -> None:
        for i, name in enumerate(res["rois"]):
            c = "C{}".format(i % 10)
            ax.errorbar(x, med[0, :, i], yerr=[lo[0, :, i], hi[0, :, i]], color=c, marker=marker, ms=4, lw=1.2,
                        capsize=2, label=name + (" on" if res["n_states"] == 2 else ""))
            if res["n_states"] == 2:
                ax.errorbar(x, med[1, :, i], yerr=[lo[1, :, i], hi[1, :, i]], color=c, marker=marker, ms=4,
                            mfc="white", ls="--", lw=1.0, capsize=2, label=name + " off")

    def unit(label: str) -> str:
        return " per {}".format(units[label]) if units.get(label) else ""

    for row, (kind, label) in enumerate(panels):
        ax = axes[row, 0]
        if kind == "raw":
            a = res["raw"]
            traces(ax, a["med"], a["lo"], a["hi"])
            ax.set_ylabel("ROI\n(keV per pixel per shot)")
        else:
            m = markers[refs.index(str(label)) % len(markers)]
            d = res["ref"][label][kind]
            traces(ax, d["med"], d["lo"], d["hi"], m)
            ax.set_ylabel("{}\n(keV per pixel{})".format(
                "median of ROI / {} per shot".format(label) if kind == "shot"
                else "median ROI / median {}\nper step".format(label), unit(str(label))))
    if two:
        ax = axes[-1, 0]
        r, lo, hi = res["raw_onoff"]
        for i, name in enumerate(res["rois"]):
            c = "C{}".format(i % 10)
            if not refs:        # unreferenced on / off: only without references (in steps.csv anyway)
                ax.errorbar(x, r[:, i], yerr=[lo[:, i], hi[:, i]], color=c, marker="o", ms=4, lw=1.2, capsize=2,
                            label=name)
            if "raw_pairs" in res and not refs:
                p = res["raw_pairs"]
                ax.errorbar(x, p["med"][:, i], yerr=[p["lo"][:, i], p["hi"][:, i]], color=c, marker="o", mfc="white",
                            ms=4, ls=":", lw=1.0, capsize=2, label=name + ": pairs")
            for j, label in enumerate(refs):
                rr = res["ref"][label]
                m = markers[j % len(markers)]
                r2, lo2, hi2 = rr["shot_onoff"]
                ax.errorbar(x, r2[:, i], yerr=[lo2[:, i], hi2[:, i]], color=c, marker=m, ms=4, lw=1.0, capsize=2,
                            label="{} / {} per shot".format(name, label))
                if "step_onoff" in rr:
                    r3, lo3, hi3 = rr["step_onoff"]
                    ax.errorbar(x, r3[:, i], yerr=[lo3[:, i], hi3[:, i]], color=c, marker=m, mfc="white", ms=4,
                                ls="-.", lw=1.0, capsize=2, label="{} / {} per step".format(name, label))
                if "pairs" in rr:
                    p = rr["pairs"]
                    ax.errorbar(x, p["med"][:, i], yerr=[p["lo"][:, i], p["hi"][:, i]], color=c, marker=m,
                                mfc="white", ms=4, ls=":", lw=1.0, capsize=2,
                                label="{} / {} per shot: pairs".format(name, label))
        ax.axhline(1.0, color="0.5", lw=0.8)
        ax.set_ylabel("pump on / off\n(ratio of medians{})".format(
            "; dotted: nearest-off pairs" if res.get("pairs") else ""))
    for ax in axes[:, 0]:
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    axes[-1, 0].set_xlabel(xlabel)
    if path:
        fig.savefig(path, dpi=110)
    if show:
        plotting.show(plt, fig)   # a window, the browser on the HPC (prints the link), or the notebook
        plotting.close(plt, fig)  # (a notebook widget stays open: zoom, pan)
    else:
        plt.close(fig)


def load_h5(path: str) -> Dict[str, Any]:
    """An analysis' scan_<id>.h5 as {"shots", "steps", "bins", "off_steps": DataFrames (the last
    two only with time-tool binning), "info": dict}; text columns decoded."""
    import h5py
    import pandas as pd

    out = {}  # type: Dict[str, Any]
    with h5py.File(path, "r") as f:
        for g in ("shots", "steps", "bins", "off_steps"):
            if g in f:
                data = {}
                for k in f[g]:
                    v = f[g][k][()]
                    if v.dtype.kind == "S":
                        v = v.astype(str)
                    data[k] = v
                out[g] = pd.DataFrame(data)
        out["info"] = json.loads(f.attrs["info"])
    return out


# ====================================================================== time-tool binning
def make_missing_tt_log(cfg: Config, runs: List[int], workers: int) -> None:
    """Make the time-tool log of the runs that have none with the current settings (the HPC
    logger's code: silas.offline.tt_logger), before the binning."""
    from silas.core import timetool
    from silas.core.runlog import _Lock
    from silas.offline import tt_logger

    s = timetool.settings(cfg)
    missing = [r for r in runs if not len(timetool.load_edges(cfg, r))]
    if not missing:
        return
    if not s["background"].get("file"):
        print("time-tool binning: runs {} have no time-tool log, and without a background (set_tt_background) "
              "none can be made".format(", ".join(str(r) for r in missing)))
        return
    print("time-tool binning: making the time-tool log of {} runs without one ({} .. {}) ...".format(
        len(missing), missing[0], missing[-1]), flush=True)
    logger = tt_logger.EdgeLogger(cfg, workers)
    lock = _Lock(tt_logger.index_path(cfg))
    held = False
    try:
        lock.acquire()
        held = True
        logger.heartbeat = lock.touch
    except RuntimeError as err:
        print("({}; making these runs here anyway)".format(err))
    try:
        logger.process(missing, s)
    finally:
        if held:
            lock.release()


def tt_binning(cfg: Config, shots: Any, roi_names: Sequence[str], labels: Sequence[str], sc: Dict[str, Any],
               n_states: int, bin_ps: float) -> Optional[Dict[str, Any]]:
    """The shots in bins of their time-tool corrected timing (silas.core.ttbin): adds the
    timing columns to ``shots``; returns {"bins", "panels", "info"} or None (no calibration,
    no time-tool log, ...: the step analysis is not affected)."""
    from silas.core import timetool, ttbin

    notes = ttbin.attach_timing(cfg, shots, sc.get("motor_true"))
    if notes["unit_warning"]:
        print(notes["unit_warning"])
    if notes["no_calibration"]:
        print("time-tool binning: no time-tool calibration (calibrate_tt): no bins")
        return None
    if notes["missing_runs"]:
        print("time-tool binning: runs without a time-tool log (with the current settings), left out: {} "
              "(make_tt_log)".format(", ".join(str(r) for r in notes["missing_runs"])))
    on = shots["read"].to_numpy(bool) & (shots["state"].to_numpy() == 0)
    good = on & shots["edge_good"].to_numpy(bool) & np.isfinite(shots["t_pulse"].to_numpy(np.float64))
    if not good.any():
        print("time-tool binning: no pump-on shot with a good edge and a motor value: no bins")
        return None
    rois = ["roi_" + n for n in roi_names]
    per = []  # type: List[str]
    for label in labels:
        for c in rois:
            col = "{}_per_{}".format(c, label)
            with np.errstate(invalid="ignore", divide="ignore"):
                shots[col] = shots[c].to_numpy(np.float64) / shots["ref_" + label].to_numpy(np.float64)
            per.append(col)
    onoff = ttbin.stepwise_onoff(shots, rois + per, sc["runs"]) if n_states == 2 else []
    ps = float(timetool.settings(cfg).get("ps_per_pulse", 0.006671))
    bins, grid = ttbin.bin_table(shots, rois + per + onoff, bin_ps / ps, ps)
    off = ttbin.off_steps(shots, rois + per, sc["runs"], ps) if n_states == 2 else None
    panels = [("ROI, pump on\n(keV per pixel per shot)", rois, "roi")]  # type: List[Tuple[str, List[str], str]]
    for label in labels:
        panels.append(("ROI / {} per shot\npump on".format(label), [c for c in per if c.endswith("_per_" + label)],
                       "roi"))
    if onoff:
        cols = [c for c in onoff if "_per_" in c] if labels else [c for c in onoff]
        panels.append(("pump on / off\n(shot / median off\nof its step)", cols, "onoff"))
    b = ttbin.settings(cfg)
    n = bins["n"].to_numpy(np.int64)
    t0 = float(grid.t0) if grid.t0 is not None else float("nan")
    info = {"bin_ps": bin_ps, "bin_pulse": bin_ps / ps, "ps_per_pulse": ps, "t0_pulse": grid.t0,
            "bins": int(len(bins)), "sparse_min": int(b["sparse_min"]),
            "sparse_bins": int((n < int(b["sparse_min"])).sum()), "shots_binned": int(n.sum()),
            "pump_on_read": int(on.sum()), "pump_on_bad_edge": int((on & ~good).sum()),
            "missing_runs": notes["missing_runs"], "calibration": timetool.settings(cfg).get("calibration"),
            "onoff": "step-wise: every pump-on shot / median of the pump-off shots of its step"}
    print("time-tool binning: {} pump-on shots in {} bins of {:g} ps ({:.4g} pulse; bin 0 at {:.2f} pulse = "
          "{:.3f} ps); {} pump-on shots without a good edge left out; {} sparse bins (< {} shots)".format(
              info["shots_binned"], info["bins"], bin_ps, bin_ps / ps, t0, t0 * ps,
              info["pump_on_bad_edge"], info["sparse_bins"], info["sparse_min"]), flush=True)
    return {"bins": bins, "panels": panels, "info": info, "off": off}


# ====================================================================== main
def prepare(cfg: Config, settings: Dict[str, Any], which: int, max_tags: Optional[int],
            reader: Any = None) -> Dict[str, Any]:
    """Scan, shot list and detector info (rank 0)."""
    sc = scan.scan_runs(cfg, which)
    if not sc["runs"]:
        raise LookupError("scan {} has no runs in the run log".format(sc["scan_id"]))
    unfinished = [r["run"] for r in sc["runs"] if r.get("end_tag") is None]
    if unfinished:
        print("note: runs {} are not finished (in the log); their tags so far are used".format(unfinished))
    det_ids = list(settings["panels"])
    items = []  # type: List[Tuple[int, int]]
    for r in sc["runs"]:
        tags = list(db.tags(cfg.beamline, int(r["run"])))
        if max_tags and len(tags) > max_tags:
            idx = np.linspace(0, len(tags) - 1, max_tags).astype(int)
            tags = [tags[i] for i in idx]
        items += [(int(r["run"]), int(t)) for t in tags]
    first_run, first_tag = items[0]
    reader = reader or PanelReader(cfg.beamline, det_ids)
    infos = [reader.info(k, first_run, first_tag) for k in range(len(det_ids))]
    return {"scan": sc, "items": items, "infos": infos,
            "run_info": {int(r["run"]): r for r in sc["runs"]}}


def analyse(args: Any) -> Optional[str]:
    """The whole analysis (MPI if started with mpirun). Returns the result folder (rank 0)."""
    import pandas as pd

    from silas.core import mpi

    comm, rank, size, MPI = mpi.world()  # MPI only under mpirun (see core/mpi.py)
    cfg = Config()
    key, settings = mpccd.get_array(cfg, args.array)
    if not settings.get("rois"):
        if rank == 0:
            print("no ROIs for {}: add_roi(...) first".format(key))
        return None
    refs = scanroi.reference_settings(cfg)          # ValueError if one is not configured
    job = None  # type: Optional[Dict[str, Any]]
    t0 = time.time()
    phases = {}  # type: Dict[str, float]
    if rank == 0:
        job = prepare(cfg, settings, args.scan, args.max_tags)
        sc = job["scan"]
        print("scan {}: {} runs, {} shots, motor {}, references: {}".format(
            sc["scan_id"], len(sc["runs"]), len(job["items"]), sc["motor"] or "?",
            ", ".join(label for label, _ in refs) or "none (set_references)"), flush=True)
        phases["prepare"] = time.time() - t0
    if comm is not None and size > 1:
        job = comm.bcast(job, root=0)
    assert job is not None
    t1 = time.time()
    timing = {}  # type: Dict[str, float]
    asked = int(getattr(args, "workers", 1) or 1)
    n_proc = min(asked, available_cores(), len(job["run_info"]))
    if size == 1 and asked > available_cores():
        print("{} workers asked, this job has {} cores: using {}".format(asked, available_cores(), n_proc))
    if size == 1 and n_proc > 1:
        print("{} worker processes on this node, whole runs each".format(n_proc), flush=True)
        rows = run_parallel(key, job["items"], job["run_info"], job["infos"], n_proc, timing)
    else:
        mine = job["items"][rank::size]
        rows = process(cfg, settings, mine, job["run_info"], job["infos"],
                       progress="[rank {}]".format(rank) if size > 1 else "[1 process]", timing=timing)
        n_proc = max(size, 1)
    if comm is not None and size > 1:
        gathered = comm.gather(rows, root=0)
        if rank != 0:
            return None
        rows = [r for part in gathered for r in part]
    phases["read"] = time.time() - t1
    t2 = time.time()
    shots = pd.DataFrame(rows).sort_values(["run", "tag"]).reset_index(drop=True)
    rmap, kev, corr = roi.analysis_setup(list(settings["panels"]), job["infos"], settings, shift_from_data=True)
    assert rmap is not None
    pump, on_value = scanroi.pump_settings(cfg)
    n_states = 2 if pump else 1
    sc = job["scan"]
    labels = [label for label, _ in refs]
    step_reference = bool(getattr(args, "step_reference", False))
    pairs = bool(getattr(args, "pairs", False))
    res = results(shots, rmap.names, labels, sc["runs"], n_states, step_reference=step_reference, pairs=pairs)
    steps = steps_frame(res)
    out = os.path.join(cfg.output_dir(), "scans", "scan_{}".format(sc["scan_id"]))
    if not os.path.isdir(out):
        os.makedirs(out)
    from silas.core import ttbin

    tt_on = ttbin.settings(cfg)["tt_binning"] if getattr(args, "tt", None) is None else bool(args.tt)
    tt = None  # type: Optional[Dict[str, Any]]
    if tt_on:
        bin_ps = float(args.bin_ps) if getattr(args, "bin_ps", None) else float(ttbin.settings(cfg)["tt_bin_ps"])
        make_missing_tt_log(cfg, sorted(set(int(r) for r in shots["run"])), int(getattr(args, "workers", 6) or 6))
        tt = tt_binning(cfg, shots, rmap.names, labels, sc, n_states, bin_ps)
    phases["statistics"] = time.time() - t2
    t3 = time.time()
    shots.to_csv(os.path.join(out, "shots.csv"), index=False)
    if tt is not None:
        tt["bins"].to_csv(os.path.join(out, "bins.csv"), index=False)
        if tt["off"] is not None:
            tt["off"].to_csv(os.path.join(out, "off_steps.csv"), index=False)
    steps.to_csv(os.path.join(out, "steps.csv"), index=False)
    e_ph = energy.photon_energy(cfg, int(sc["runs"][0]["run"]))
    point = cfg.get("point_detectors", {})
    ref_units = {label: str((point.get(true) or {}).get("unit") or "") for label, true in refs}
    info = {"scan": {k: v for k, v in sc.items() if k != "runs"}, "runs": sc["runs"],
            "array": key, "panels": settings["panels"],
            "rois": {n: {"rect": r, "pixels": rmap.n_pixels[i], "panels": [k + 1 for k, _ in rmap.parts[i]]}
                     for i, (n, r) in enumerate(zip(rmap.names, rmap.rects))},
            "references": {label: {"detector": true, "unit": ref_units[label]} for label, true in refs},
            "corrections": settings["corrections"], "calibration": settings.get("calibration"),
            "keV_per_adu": [float(k) for k in kev], "photon_energy_keV": e_ph,
            "pump_detector": pump, "pump_on_value": on_value, "quantiles": list(scanroi.QUANTILES),
            "error_bars": "spread of the shots: quantiles 15.87 / 84.13 % (not divided by sqrt(n)); ratios of "
                          "medians: relative errors added in quadrature",
            "shots": {"total": int(len(shots)), "used": int(shots["read"].sum()),
                      "xray_off": int((~shots["use"]).sum()),
                      "pump_unknown": int(((shots["state"] < 0) & shots["use"]).sum())},
            "options": {"step_reference": step_reference, "pairs": pairs},
            "tt_binning": tt["info"] if tt is not None else None,
            "max_tags": args.max_tags, "workers": n_proc, "made": time.strftime("%Y-%m-%d %H:%M:%S")}
    for label in labels:
        info["shots"]["used_with_" + label] = int((shots["read"] & np.isfinite(shots["ref_" + label])).sum())
    h5_path = os.path.join(out, "scan_{}.h5".format(sc["scan_id"]))
    write_h5(h5_path, shots, steps, info, bins=tt["bins"] if tt is not None else None,
             off_steps=tt["off"] if tt is not None else None)
    phases["write"] = time.time() - t3
    info["timing_s"] = {"phases": phases, "workers_summed": timing}
    with open(os.path.join(out, "info.json"), "w") as fh:
        json.dump(info, fh, indent=1, default=str)
    t4 = time.time()
    xlabel = sc["motor"] if res["table"] and res["table"][0]["x_is_value"] else "scan step (motor value not known)"
    title = "scan {} ({} runs) - {}".format(sc["scan_id"], len(sc["runs"]), key)
    s = info["shots"]
    print("shots: {} total, {} used, {} X-rays off / bad beam, {} pump unknown{}".format(
        s["total"], s["used"], s["xray_off"], s["pump_unknown"],
        "".join(", {} with {}".format(s["used_with_" + label], label) for label in labels)))
    print("time: prepare {:.1f} s, reading {:.1f} s ({} workers; summed over them: flags {:.1f} s, images "
          "{:.1f} s, ROI sums {:.1f} s), statistics {:.1f} s, writing {:.1f} s".format(
              phases.get("prepare", 0.0), phases["read"], n_proc, timing.get("flags", 0.0),
              timing.get("images", 0.0), timing.get("roi_sums", 0.0), phases["statistics"], phases["write"]))
    print("results in {}  ({:.0f} s without the plot)".format(out, t4 - t0), flush=True)
    x_scale = 1.0
    if tt_on and res["table"] and res["table"][0]["x_is_value"]:      # time-tool binning: the axis in ps
        from silas.core import timetool

        x_scale = float(timetool.settings(cfg).get("ps_per_pulse", 0.006671))
        xlabel = "{} x ps_per_pulse (ps)".format(xlabel)
    plot(res, title, xlabel, os.path.join(out, "plot.png"), show=args.show and tt is None, ref_units=ref_units,
         x_scale=x_scale)
    if tt is not None:
        ttbin.plot_bins(cfg, tt["bins"], tt["panels"], title + " - time-tool bins of {:g} ps".format(tt["info"]["bin_ps"]),
                        os.path.join(out, "plot_tt.png"), show=args.show, off=tt["off"])
    return out


@sacla.requires_sacla
def run_live(args: Any) -> None:
    analyse(args)


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="offline scan analysis (stpy): ROI quantiles per step")
    ap.add_argument("--scan", type=int, default=-1, help="-1 newest (default), -2, 0, 1, ... or a run number")
    ap.add_argument("--array", help="MPCCD array name or alias (default: use_mpccd)")
    ap.add_argument("--max-tags", type=int, help="only this many shots per run (evenly spread), for a quick look")
    ap.add_argument("--show", action="store_true", help="also open the plot (window, or the browser on the HPC)")
    ap.add_argument("--workers", type=int, default=6, help="processes on this node (multiprocessing, no MPI; "
                                                           "0 or 1: one; at most the job's cores; default 6)")
    ap.add_argument("--step-reference", action="store_true",
                    help="also reference per step: median(ROI) / median(reference)")
    ap.add_argument("--tt", dest="tt", action="store_true", default=None,
                    help="time-tool binning on (default: the config's scan.tt_binning)")
    ap.add_argument("--no-tt", dest="tt", action="store_false", help="time-tool binning off")
    ap.add_argument("--bin-ps", type=float, help="time-tool bin width in ps (default: the config's scan.tt_bin_ps)")
    ap.add_argument("--pairs", action="store_true",
                    help="also on / off of nearest-off pairs (every pump-on shot / the nearest pump-off shot)")
    run_live(ap.parse_args(argv))


if __name__ == "__main__":
    main()
