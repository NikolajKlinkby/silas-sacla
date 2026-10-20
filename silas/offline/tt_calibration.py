"""Time-tool calibration (HPC, stpy): edge pixel -> position of the time-tool delay motor.

    calibrate_tt(1234480)                                       # from the session (the scan of run 1234480)
    calibrate_tt(runs=range(1234480, 1234497))                  # runs without the run log
    python -m silas.offline.tt_calibration --scan 1234480 --order 2 --workers 6
    python -m silas.offline.tt_calibration --runs 1234480-1234496 --motor xfel_bl_3_st_1_motor_73/position

Parallel: under mpirun (calibrate_tt starts it so when MPI is there) rank 0 hands out the jobs
(parts of runs, about 500 tags each) to the other ranks; without MPI a pool of processes. One
line of progress per finished job. All values are in the motor's units (pulse).

A calibration scan moves the time-tool delay motor (at BL3 e.g. xfel_bl_3_st_1_motor_73/position)
one step per run. For every run (step), with the current time-tool settings (ROI,
binning, background, edge; silas.core.timetool):

  * the shots: the timing shots (X-rays on, beam OK, laser on if it has a flag) are read by a
    pool of processes (whole runs per process) and their edge is found (find_edge)
  * the set value: the median of the motor's values over the run's shots (and their spread)
  * the edge: the good edges only; median and the 15.87 / 84.13 % quantiles (the spread of
    the shots, the error bars); a histogram per step (diagnostics)
  * the calibration: a polynomial of order n, motor = P(edge pixel), fitted to the step
    medians (steps with fewer than --min-good good edges are left out and shown open)

Written to <output>/timetool/calibration_<first run>-<last run>_<time>/:
  shots.csv         one row per timing shot: run, tag, set value, edge, its parts, good, why
  steps.csv         per step: run, set value (median, spread), shots, good, edge quantiles
  calibration.json  the polynomial (numpy.polyval order: highest power first), its range of
                    edge pixels, residuals, the settings used
  histograms.png    the edge pixels of every step
  calibration.png   set value against edge pixel, the fit and its residuals
The calibration is also stored in the config (timetool.calibration), unless --no-save.

The delay motor of the experiment (in the same units) gives the nominal delay; the time tool
gives each shot's deviation from it in these motor units, P(edge) - P(0). The scan analysis
combines them (time-tool binning, silas.core.ttbin): t = motor + P(edge) - P(0).
"""
import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import db, names, sacla, timetool
from silas.core.config import Config

QUANTILES = (0.1587, 0.5, 0.8413)
DEFAULT_MOTOR = "xfel_bl_3_st_1_motor_73/position"   # BL3 time-tool delay motor (when none is given)
FS_PER_MM_DOUBLE_PASS = 6671.28                     # 2 mm of light path / c, in fs


# ====================================================================== shots (workers)
JOB_TAGS = 500       # tags per job: runs are split into parts of about this many tags (progress, balance)


def make_jobs(cfg_path: str, runs: Sequence[int], max_tags: Optional[int], motor: Optional[str], s: Dict[str, Any],
              det_id: Optional[str], n_tags: Optional[Dict[int, int]] = None,
              all_shots: bool = False) -> List[Tuple[Any, ...]]:
    """One job per part of a run (about JOB_TAGS tags each)."""
    jobs = []  # type: List[Tuple[Any, ...]]
    bl = Config(cfg_path).beamline
    for r in runs:
        n = (n_tags or {}).get(r)
        if n is None:
            n = len(db.tags(bl, r))
        if max_tags:
            n = min(n, int(max_tags))
        parts = max(1, int(np.ceil(n / float(JOB_TAGS))))
        jobs += [(cfg_path, r, max_tags, motor, s, det_id, k, parts, all_shots) for k in range(parts)]
    return jobs


def run_edges(job: Tuple[Any, ...]) -> Dict[str, Any]:
    """Worker: the edges of the timing shots of one part of a run, and the motor's values.
    job = (config path, run, max_tags, motor or None, settings, camera or None, part, parts
    [, all_shots]); all_shots=True: every shot (its class in "cls"), not only timing shots."""
    import stpy

    cfg_path, run, max_tags, motor, s, det_id, part, n_parts = job[:8]
    all_shots = bool(job[8]) if len(job) > 8 else False
    t0 = time.time()
    cfg = Config(cfg_path)
    bl = cfg.beamline
    tags = list(db.tags(bl, run))
    if max_tags and len(tags) > max_tags:
        tags = [tags[i] for i in np.linspace(0, len(tags) - 1, int(max_tags)).astype(int)]
    tags = [int(t) for t in np.array_split(np.array(tags, np.int64), n_parts)[part]]
    f = timetool.run_flags(cfg, run, tags)
    values = np.array(db.read_point(motor, f["taghi"], tags, errors="nan"), float) if motor \
        else np.full(len(tags), np.nan)
    sid = timetool.settings_id(s)
    det = det_id or timetool.camera(cfg, db.run_detectors(bl, run))
    reader = stpy.StorageReader(det, bl, (run,))
    buf = stpy.StorageBuffer(reader)
    bg = timetool.Background()
    bg.load(s["background"].get("file"))
    bt = None  # type: Optional[np.ndarray]
    t1 = time.time()
    t_read, t_edge = 0.0, 0.0
    rows = []  # type: List[Dict[str, Any]]
    unread = 0
    for i, tag in enumerate(tags):
        if not all_shots and int(f["cls"][i]) != timetool.TIMING:
            continue
        ta = time.perf_counter()
        try:
            reader.collect(buf, int(tag))
            img = buf.read_det_data(0)
        except stpy.APIError:
            unread += 1
            continue
        tb = time.perf_counter()
        if bt is None:
            bt = bg.trace(s.get("roi"), s.get("binning", "rows"), img.shape)
        e = timetool.shot_edge(img, s, bt)
        t_read += tb - ta
        t_edge += time.perf_counter() - tb
        row = {"run": run, "tag": int(tag), "set_value": float(values[i])}  # type: Dict[str, Any]
        row.update({k: e[k] for k in timetool.EDGE_KEYS})
        row["why"] = e["why"]
        row["cls"] = int(f["cls"][i])
        row["sid"] = sid
        rows.append(row)
    return {"run": run, "part": part, "n_parts": n_parts, "rows": rows, "n_tags": len(tags),
            "classes": timetool.class_counts(f["cls"]), "unread": unread, "seconds": time.time() - t0,
            "t_setup": t1 - t0, "t_read": t_read, "t_edge": t_edge, "det_id": det}


def _progress(res: Dict[str, Any], k: int, total: int, t0: float) -> None:
    el = time.time() - t0
    n = len(res["rows"])
    good = sum(1 for r in res["rows"] if r["good"])
    print("  [{}/{}] run {}{}: {} tags, {} timing shots, {} good edges in {:.1f} s (flags + open {:.1f} s; per shot "
          "read {:.1f} ms, edge {:.1f} ms)   elapsed {:.0f} s, about {:.0f} s left".format(
              k, total, res["run"], " part {}/{}".format(res["part"] + 1, res["n_parts"]) if res["n_parts"] > 1 else "",
              res["n_tags"], n, good, res["seconds"], res["t_setup"], 1000 * res["t_read"] / max(n, 1),
              1000 * res["t_edge"] / max(n, 1), el, el / k * (total - k)), flush=True)


def map_jobs(jobs: List[Tuple[Any, ...]], workers: int,
             on_result: Optional[Any] = None) -> Optional[List[Dict[str, Any]]]:
    """run_edges over the jobs, with a line of progress per finished job. Under mpirun: rank 0
    hands out the jobs one by one to the other ranks and returns all results (the other ranks
    return None). Otherwise: a pool of `workers` processes (multiprocessing, "spawn").
    on_result(result): called (rank 0) for every finished job, in the order they finish."""
    import multiprocessing

    from silas.core import mpi, runner

    comm, rank, size, MPI = mpi.world()
    t0 = time.time()
    out = []  # type: List[Dict[str, Any]]
    if size > 1:
        if rank > 0:
            while True:
                i = comm.recv(source=0, tag=31)
                if i is None:
                    return None
                comm.send(run_edges(jobs[i]), dest=0, tag=32)
        print("{} jobs on {} MPI processes ...".format(len(jobs), size - 1), flush=True)
        nxt, active = 0, 0
        for w in range(1, size):
            if nxt < len(jobs):
                comm.send(nxt, dest=w, tag=31)
                nxt += 1
                active += 1
            else:
                comm.send(None, dest=w, tag=31)
        while active:
            st = MPI.Status()
            res = comm.recv(source=MPI.ANY_SOURCE, tag=32, status=st)
            out.append(res)
            _progress(res, len(out), len(jobs), t0)
            if on_result is not None:
                on_result(res)
            w = st.Get_source()
            if nxt < len(jobs):
                comm.send(nxt, dest=w, tag=31)
                nxt += 1
            else:
                comm.send(None, dest=w, tag=31)
                active -= 1
        return out
    n = runner.cap_workers(max(1, min(int(workers), len(jobs))), "workers")
    print("{} jobs on {} process{} ...".format(len(jobs), n, "es" if n > 1 else ""), flush=True)
    if n > 1:
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(n, initializer=runner.worker_ignores_ctrl_c) as pool:
            for res in pool.imap_unordered(run_edges, jobs):
                out.append(res)
                _progress(res, len(out), len(jobs), t0)
                if on_result is not None:
                    on_result(res)
    else:
        for job in jobs:
            out.append(run_edges(job))
            _progress(out[-1], len(out), len(jobs), t0)
            if on_result is not None:
                on_result(out[-1])
    return out


def show_plots(plt: Any) -> None:
    """The plots in a window, or in the browser on the HPC (until Ctrl-C)."""
    from silas.core import plotting

    if plotting.is_web(plt):
        print("the plots are served in the browser (link above); press Ctrl-C here when you are done", flush=True)
    plotting.show(plt)


# ====================================================================== statistics
def step_table(shots: Any, runs: Sequence[int], min_good: int) -> Any:
    """Per run: set value, shots, good, edge quantiles."""
    import pandas as pd

    out = []  # type: List[Dict[str, Any]]
    for run in runs:
        part = shots[shots["run"] == run]
        good = part[part["good"].astype(bool) & np.isfinite(part["edge"])]
        sv = part["set_value"].to_numpy(float)
        sv = sv[np.isfinite(sv)]
        row = {"run": run, "set_value": float(np.median(sv)) if sv.size else np.nan,
               "set_lo": float(np.percentile(sv, 15.87)) if sv.size else np.nan,
               "set_hi": float(np.percentile(sv, 84.13)) if sv.size else np.nan,
               "shots": int(len(part)), "good": int(len(good))}
        if len(good):
            q = np.quantile(good["edge"].to_numpy(float), QUANTILES)
            row.update(edge_lo=float(q[0]), edge=float(q[1]), edge_hi=float(q[2]))
        else:
            row.update(edge_lo=np.nan, edge=np.nan, edge_hi=np.nan)
        row["used"] = bool(len(good) >= min_good and np.isfinite(row["set_value"]))
        out.append(row)
    return pd.DataFrame(out)


def fit(steps: Any, order: int) -> Optional[Dict[str, Any]]:
    """motor = P(edge) on the used steps; None if too few."""
    use = steps[steps["used"]]
    if len(use) < order + 1:
        return None
    x = use["edge"].to_numpy(float)
    y = use["set_value"].to_numpy(float)
    coef = np.polyfit(x, y, int(order))
    resid = y - np.polyval(coef, x)
    deriv = np.polyder(coef)
    mid = float(np.median(x))
    slope = float(np.polyval(deriv, mid)) if len(deriv) else 0.0
    return {"order": int(order), "coef": [float(c) for c in coef], "edge_range": [float(x.min()), float(x.max())],
            "rms_residual": float(np.sqrt(np.mean(resid ** 2))), "residuals": [float(r) for r in resid],
            "steps_used": int(len(use)), "slope_at_median": slope, "median_edge": mid}


# ====================================================================== plots
def _plt(show: bool) -> Tuple[Any, bool]:
    import matplotlib

    from silas.core import plotting

    if show:
        try:
            return plotting.pyplot(), True
        except RuntimeError as err:
            print("plot window not possible ({}); the plots are only saved".format(err))
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt, False


def plot_histograms(plt: Any, shots: Any, steps: Any, motor: str, path: str) -> Any:
    n = len(steps)
    cols = 4 if n > 6 else max(n, 1)
    rows = int(np.ceil(n / float(cols))) or 1
    fig, axes = plt.subplots(rows, cols, figsize=(3.6 * cols, 2.5 * rows + 0.8), squeeze=False)
    good = shots[shots["good"].astype(bool) & np.isfinite(shots["edge"])]
    lo, hi = (np.percentile(good["edge"], [0.5, 99.5]) if len(good) else (0.0, 1.0))
    pad = 0.05 * max(hi - lo, 10.0)
    bins = np.linspace(lo - pad, hi + pad, 60)
    for ax, (_, st) in zip(axes.ravel(), steps.iterrows()):
        e = good[good["run"] == st["run"]]["edge"].to_numpy(float)
        ax.hist(e, bins=bins, color="C0" if st["used"] else "0.6")
        if np.isfinite(st["edge"]):
            ax.axvline(st["edge"], color="C3", lw=1.2)
            ax.axvspan(st["edge_lo"], st["edge_hi"], color="C3", alpha=0.12)
        ax.set_title("run {}: {:.6g}\n{} good of {} timing shots".format(
            int(st["run"]), st["set_value"], int(st["good"]), int(st["shots"])), fontsize=8)
        ax.tick_params(labelsize=7)
    for ax in axes.ravel()[n:]:
        ax.axis("off")
    fig.suptitle("edge pixels per step (good edges; red: median and 16-84 %)   set value: {}".format(motor), fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=100)
    return fig


def plot_calibration(plt: Any, shots: Any, steps: Any, cal: Optional[Dict[str, Any]], motor: str, unit: str,
                     title: str, path: str) -> Any:
    fig, (ax, rax) = plt.subplots(2, 1, figsize=(10, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1.2]})
    good = shots[shots["good"].astype(bool) & np.isfinite(shots["edge"])]
    ax.plot(good["edge"].to_numpy(float), good["set_value"].to_numpy(float), ".", color="0.75", ms=2,
            label="good shots")
    for used, mfc, label in ((True, "C0", "step median (16-84 % of the shots)"), (False, "white", "step left out")):
        st = steps[steps["used"] == used]
        st = st[np.isfinite(st["edge"])]
        if len(st):
            e, lo, hi = (st[c].to_numpy(float) for c in ("edge", "edge_lo", "edge_hi"))
            ax.errorbar(e, st["set_value"].to_numpy(float), xerr=[e - lo, hi - e],
                        fmt="o", color="C0", mfc=mfc, capsize=3, label=label)
    yl = "{} [{}]".format(motor, unit) if unit else motor
    ax.set_ylabel("set value: " + yl)
    if cal is not None:
        xs = np.linspace(cal["edge_range"][0], cal["edge_range"][1], 300)
        ax.plot(xs, np.polyval(cal["coef"], xs), "-", color="C3", lw=1.5,
                label="fit, order {}: rms {:.3g}".format(cal["order"], cal["rms_residual"]))
        used = steps[steps["used"]]
        e, lo, hi = (used[c].to_numpy(float) for c in ("edge", "edge_lo", "edge_hi"))
        rax.errorbar(e, np.array(cal["residuals"]), xerr=[e - lo, hi - e],
                     fmt="o", color="C3", capsize=3)
        rax.axhline(0.0, color="0.5", lw=0.8)
        txt = "set value = " + " + ".join("{:.6g} x^{}".format(c, len(cal["coef"]) - 1 - i)
                                          for i, c in enumerate(cal["coef"]))
        txt += "\nslope at edge {:.0f} px: {:.4g} {}/px".format(cal["median_edge"], cal["slope_at_median"],
                                                               unit or "units")
        if unit.lower() == "mm":
            txt += " = {:.3g} fs/px (double-pass delay line)".format(abs(cal["slope_at_median"]) * FS_PER_MM_DOUBLE_PASS)
        rax.set_title(txt, fontsize=8, family="monospace", loc="left")
    rax.set_ylabel("residual")
    rax.set_xlabel("edge (camera pixel)")
    ax.legend(fontsize=8, loc="best")
    ax.set_title(title)
    for a in (ax, rax):
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    return fig


# ====================================================================== main
def parse_runs(text: Sequence[str]) -> List[int]:
    """'1234480-1234496' and / or single runs."""
    out = []  # type: List[int]
    for part in text:
        for p in str(part).split(","):
            p = p.strip()
            if not p:
                continue
            if "-" in p:
                a, b = p.split("-", 1)
                out += list(range(int(a), int(b) + 1))
            else:
                out.append(int(p))
    return sorted(set(out))


def calibrate(args: Any) -> Optional[str]:
    import pandas as pd

    from silas.core import mpi, scan

    _, rank, _, _ = mpi.world()
    say = print if rank == 0 else (lambda *a, **k: None)  # type: Any
    cfg = Config()
    s = timetool.settings(cfg)
    if args.runs:
        runs = parse_runs(args.runs)
        label = "runs {}-{}".format(runs[0], runs[-1])
        motor = args.motor or DEFAULT_MOTOR
    else:
        sc = scan.scan_runs(cfg, args.scan)
        runs = [int(r["run"]) for r in sc["runs"]]
        label = "scan {} (runs {}-{})".format(sc["scan_id"], runs[0], runs[-1])
        motor = args.motor or sc["motor_true"] or DEFAULT_MOTOR
    motor = names.resolve(cfg, motor)
    unit = str(args.unit or names.entry(cfg, motor).get("unit") or "pulse")
    if not s["background"].get("file"):
        say("no time-tool background: the edges are looked for in the raw trace and never good. "
            "set_tt_background(run=...) first")
        return None
    say("time-tool calibration: {}, {} runs, set value {} [{}]".format(label, len(runs), motor, unit))
    say("  ROI {}, binning {}, background {} ({})".format(s.get("roi") or "whole image", s.get("binning"),
                                                         s["background"].get("source"), s["background"].get("mode")))
    say("  edge: {}".format(timetool.edge_text(s["edge"])), flush=True)
    t0 = time.time()
    jobs = make_jobs(cfg.path, runs, args.max_tags, motor, s, args.camera)
    parts = map_jobs(jobs, args.workers)
    if parts is None:                 # an MPI worker: done
        return None
    dt = time.time() - t0
    rows = [row for p in parts for row in p["rows"]]
    n_tags = sum(p["n_tags"] for p in parts)
    print("read {} timing shots of {} tags in {:.1f} s ({:.0f} shots/s)".format(
        len(rows), n_tags, dt, len(rows) / max(dt, 1e-9)))
    for p in parts:
        if p["unread"]:
            print("  run {}: {} shots not readable".format(p["run"], p["unread"]))
    if not rows:
        print("no timing shots: shot classes per run: " + "; ".join("{}: {}".format(p["run"], p["classes"])
                                                                   for p in parts))
        return None
    shots = pd.DataFrame(rows)
    steps = step_table(shots, runs, args.min_good)
    cal = fit(steps, args.order)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(timetool.background_dir(cfg), "calibration_{}-{}_{}".format(runs[0], runs[-1], stamp))
    os.makedirs(out)
    shots.to_csv(os.path.join(out, "shots.csv"), index=False)
    steps.to_csv(os.path.join(out, "steps.csv"), index=False)
    pd.set_option("display.width", 160)
    print(steps.to_string(index=False, float_format=lambda v: "{:.6g}".format(v)))
    info = {"source": label, "runs": runs, "motor": motor, "unit": unit, "camera": parts[0]["det_id"],
            "order": args.order, "min_good": args.min_good, "fit": cal, "settings": s,
            "quantiles": list(QUANTILES), "max_tags": args.max_tags, "made": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": "set value = numpy.polyval(fit.coef, edge pixel)"}
    path = os.path.join(out, "calibration.json")
    with open(path, "w") as fh:
        json.dump(info, fh, indent=1, default=str)
    if cal is None:
        print("too few steps with at least {} good edges for an order-{} fit: no calibration".format(
            args.min_good, args.order))
    else:
        print("calibration: set value = polyval({}, edge)   rms residual {:.3g} {}   edge range {:.0f}..{:.0f} px".format(
            ["{:.6g}".format(c) for c in cal["coef"]], cal["rms_residual"], unit, *cal["edge_range"]))
        line = "slope at edge {:.0f} px: {:.4g} {}/px".format(cal["median_edge"], cal["slope_at_median"], unit or "units")
        print(line)
        if not args.no_save:
            timetool.set_option(cfg, "calibration", {
                "motor": motor, "unit": unit, "order": cal["order"], "coef": cal["coef"],
                "edge_range": cal["edge_range"], "rms_residual": cal["rms_residual"], "source": label,
                "file": path})
            print("stored as the time-tool calibration (config timetool.calibration)")
    plt, show = _plt(args.show)
    title = "time-tool calibration: {} ({} steps used)".format(label, cal["steps_used"] if cal else 0)
    plot_histograms(plt, shots, steps, motor, os.path.join(out, "histograms.png"))
    plot_calibration(plt, shots, steps, cal, motor, unit, title, os.path.join(out, "calibration.png"))
    print("results in {}".format(out), flush=True)
    if show:
        show_plots(plt)
    from silas.core import plotting

    plotting.close_all(plt)     # (in a notebook the shown widgets stay: zoom, pan)
    return out


@sacla.requires_sacla
def run_live(args: Any) -> None:
    calibrate(args)


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="time-tool calibration: edge pixel -> delay motor position")
    ap.add_argument("--scan", type=int, default=-1, help="the calibration scan: -1 newest, a run number, ... "
                                                         "(as scan_info; default -1)")
    ap.add_argument("--runs", nargs="+", help="runs instead of a scan of the run log, e.g. 1234480-1234496")
    ap.add_argument("--motor", help="the set value's point detector (default: the scan's motor, else {})".format(
        DEFAULT_MOTOR))
    ap.add_argument("--unit", help="its unit, if the config does not have one (default pulse)")
    ap.add_argument("--order", type=int, default=1, help="order of the polynomial (default 1)")
    ap.add_argument("--min-good", type=int, default=20, help="good edges a step needs to be used (default 20)")
    ap.add_argument("--max-tags", type=int, help="at most this many tags per run (evenly spread)")
    ap.add_argument("--workers", type=int, default=6, help="processes without mpirun (default 6; under mpirun "
                                                           "the MPI processes)")
    ap.add_argument("--camera", help="camera detector ID (default: the config's, else the first OPAL)")
    ap.add_argument("--show", action="store_true", help="also show the plots (window, or the browser on the HPC)")
    ap.add_argument("--no-save", action="store_true", help="do not store the calibration in the config")
    try:
        run_live(ap.parse_args(argv))
    except KeyboardInterrupt:
        print("stopped (Ctrl-C)")


if __name__ == "__main__":
    main()
