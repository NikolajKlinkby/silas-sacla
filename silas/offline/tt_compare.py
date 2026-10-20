"""Our time-tool edges against SACLA's TimingMonitorAnalyzer, shot by shot (HPC, stpy).

    compare_tt_sacla(1234501)                                   # from the session (the scan of run 1234501)
    compare_tt_sacla(runs=1234501, sacla_dir="/path/to/TMA/csv")  # or cfg.set("timetool.sacla_dir", ...) once
    python -m silas.offline.tt_compare --runs 1234501-1234551 --sacla-dir /path/to/TMA/csv --workers 6

For every run: our edge of every timing shot (silas.core.timetool, the current settings;
under mpirun rank 0 hands out parts of runs to the other ranks, else a pool of processes; a
line of progress per finished part) and SACLA's results of the same tags from its
files <sacla_dir>/<run>.csv (tag, derivative edge, fitted edge, optional signals) and, if there,
<run>.tma.h5 (its quality flags). SACLA's files of a run that has none are made with its
command-line tool (printed, not run):

    TimingMonitorAnalyzerCUI <its settings file> <run> <run>

The edges are in camera pixels in both. Written to <output>/timetool/compare_<runs>_<time>/:
  shots.csv     per tag: ours (edge, derivative, fit, good, why) and SACLA's (derivative, fit,
                quality), and the difference ours - SACLA (fit)
  runs.csv      per run: shots in both, good in both, median and 16-84 % of the difference,
                its robust spread, the correlation
  compare.png   ours against SACLA's (all and good shots), the differences (histogram, per run)
                and, for one run, both edges shot after shot
  delay.png     the corrected timing of every shot (pulse), for the first --plot-runs runs:
                  SACLA                csv column 3 - sacla_slope x csv column 2 (fitted edge)
                  ours                 <scanned delay motor> + P(edge) - P(0)   (P = calibrate_tt;
                                       linear: motor - |slope| x edge)
                the two shot after shot, and their difference; sacla_slope (pulse / px): the
                linear calibration of SACLA's results, --sacla-slope (default 0.0026 ps / px over
                0.00666 ps / pulse)
"""
import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import sacla, timetool
from silas.core.config import Config
from silas.offline.tt_calibration import DEFAULT_MOTOR, make_jobs, map_jobs, parse_runs, show_plots

SACLA_SLOPE = 0.0026 / 0.00666     # pulse / px: a linear calibration of SACLA's results (--sacla-slope)


def read_sacla(sacla_dir: str, run: int) -> Optional[Any]:
    """SACLA's results of a run as a DataFrame (tag, sacla_deriv, sacla_fit, sacla_good_deriv,
    sacla_good_fit), None if its CSV is not there."""
    import pandas as pd

    path = os.path.join(sacla_dir, "{}.csv".format(run))
    if not os.path.exists(path):
        return None
    a = np.genfromtxt(path, delimiter=",", skip_header=2)
    a = np.atleast_2d(a)
    df = pd.DataFrame({"tag": a[:, 0].astype(np.int64), "sacla_deriv": a[:, 1], "sacla_fit": a[:, 2],
                       "sacla_motor": a[:, 3] if a.shape[1] > 3 else np.nan})
    df["sacla_good_deriv"] = -1
    df["sacla_good_fit"] = -1
    h5 = os.path.join(sacla_dir, "{}.tma.h5".format(run))
    if os.path.exists(h5):
        try:
            import h5py

            with h5py.File(h5, "r") as f:
                tags = np.array(f["common/tag_number_list"][()], np.int64)
                q = f["cam1/results/quality_check"]
                flags = pd.DataFrame({"tag": tags,
                                      "sacla_good_deriv": np.array(q["derivative/total"][()], int),
                                      "sacla_good_fit": np.array(q["fitting/total"][()], int)})
            df = df.drop(columns=["sacla_good_deriv", "sacla_good_fit"]).merge(flags, on="tag", how="left")
            df[["sacla_good_deriv", "sacla_good_fit"]] = df[["sacla_good_deriv", "sacla_good_fit"]].fillna(-1)
        except (OSError, KeyError, ValueError) as err:
            print("run {}: SACLA quality flags not read from {} ({})".format(run, h5, err))
    return df


def robust_sigma(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    return float(1.4826 * np.median(np.abs(x - np.median(x)))) if x.size else np.nan


def run_table(shots: Any, runs: List[int]) -> Any:
    import pandas as pd

    out = []  # type: List[Dict[str, Any]]
    for run in runs:
        p = shots[shots["run"] == run]
        both = p[np.isfinite(p["edge"]) & np.isfinite(p["sacla_fit"])]
        good = both[both["good"].astype(bool) & (both["sacla_good_fit"] != 0)]
        d = good["diff"].to_numpy(float)
        row = {}  # type: Dict[str, Any]
        row.update({"run": run, "ours": int(p["ours"].fillna(False).astype(bool).sum()),
               "sacla": int(np.isfinite(p["sacla_fit"]).sum()),
               "both": int(len(both)), "good_both": int(len(good)),
               "ours_good_only": int((p["good"].astype(bool) & ~np.isfinite(p["sacla_fit"])).sum())})
        if d.size:
            q = np.percentile(d, [15.87, 50, 84.13])
            row.update(diff_median=float(q[1]), diff_lo=float(q[0]), diff_hi=float(q[2]), diff_sigma=robust_sigma(d),
                       corr=float(np.corrcoef(good["edge"], good["sacla_fit"])[0, 1]) if d.size > 2 else np.nan)
        out.append(row)
    return pd.DataFrame(out)


def plot(plt: Any, shots: Any, table: Any, title: str, path: str) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    (a1, a2), (a3, a4) = axes
    both = shots[np.isfinite(shots["edge"]) & np.isfinite(shots["sacla_fit"])]
    good = both[both["good"].astype(bool) & (both["sacla_good_fit"] != 0)]
    bad = both[~(both["good"].astype(bool) & (both["sacla_good_fit"] != 0))]
    a1.plot(bad["sacla_fit"].to_numpy(float), bad["edge"].to_numpy(float), ".", color="C3", ms=2,
            label="not good in one of them ({})".format(len(bad)))
    a1.plot(good["sacla_fit"].to_numpy(float), good["edge"].to_numpy(float), ".", color="C0", ms=2,
            label="good in both ({})".format(len(good)))
    if len(both):
        lo, hi = np.nanpercentile(np.r_[both["edge"], both["sacla_fit"]], [0.5, 99.5])
        a1.plot([lo, hi], [lo, hi], "-", color="0.4", lw=1, label="ours = SACLA")
    a1.set_xlabel("SACLA fitted edge (camera pixel)")
    a1.set_ylabel("our edge (camera pixel)")
    a1.legend(fontsize=8)
    d = good["diff"].to_numpy(float)
    if d.size:
        q = np.percentile(d, [0.5, 15.87, 50, 84.13, 99.5])
        a2.hist(d, bins=np.linspace(q[0], q[4], 80), color="C0")
        for v, ls in ((q[1], ":"), (q[2], "-"), (q[3], ":")):
            a2.axvline(v, color="C3", ls=ls)
        a2.set_title("median {:.2f} px, 16-84 %: {:.2f} .. {:.2f} px, robust sigma {:.2f} px".format(
            q[2], q[1], q[3], robust_sigma(d)), fontsize=9)
    a2.set_xlabel("ours - SACLA (fit), good in both (px)")
    t = table.iloc[0:0]
    if "diff_median" in table.columns:
        t = table[np.isfinite(table["diff_median"].to_numpy(float))]
    if len(t):
        m = t["diff_median"].to_numpy(float)
        a3.errorbar(t["run"].to_numpy(int), m, yerr=[m - t["diff_lo"].to_numpy(float), t["diff_hi"].to_numpy(float) - m],
                    fmt="o", capsize=3)
        a3.axhline(0.0, color="0.5", lw=0.8)
    a3.ticklabel_format(axis="x", useOffset=False, style="plain")
    a3.set_xlabel("run")
    a3.set_ylabel("ours - SACLA per run (px): median, 16-84 %")
    if len(shots):
        run0 = int(table.sort_values("good_both", ascending=False)["run"].iloc[0])
        p = shots[shots["run"] == run0].sort_values("tag")
        i = np.arange(len(p))
        a4.plot(i, p["sacla_fit"].to_numpy(float), "-", color="0.5", lw=0.8, label="SACLA fit")
        a4.plot(i, p["edge"].to_numpy(float), "-", color="C0", lw=0.8, label="ours")
        ng = ~p["good"].astype(bool).to_numpy()
        a4.plot(i[ng], p["edge"].to_numpy(float)[ng], "x", color="C3", ms=4, label="ours not good")
        a4.set_xlabel("shot of run {}".format(run0))
        a4.set_ylabel("edge (camera pixel)")
        a4.legend(fontsize=8)
    for a in (a1, a2, a3, a4):
        a.grid(alpha=0.3)
    fig.suptitle(title)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=100)


def plot_delay(plt: Any, shots: Any, runs: List[int], motor: str, title: str, path: str) -> Optional[Dict[str, float]]:
    """Corrected timing of every shot, ours and SACLA's (linear calibration), for some runs."""
    p = shots[shots["run"].isin(runs)].sort_values(["run", "tag"])
    p = p[np.isfinite(p["delay_ours"]) | np.isfinite(p["delay_sacla"])]
    if not len(p):
        return None
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True, gridspec_kw={"height_ratios": [2.5, 1]})
    i = np.arange(len(p))
    ours = p["delay_ours"].to_numpy(float)
    theirs = p["delay_sacla"].to_numpy(float)
    good = p["good"].astype(bool).to_numpy()
    a1.plot(i, p["motor"].to_numpy(float), "-", color="0.75", lw=1.0, label="scanned motor ({})".format(motor))
    a1.plot(i, theirs, ".", color="0.3", ms=3, label="SACLA: csv[3] - {:.4f} x csv[2]".format(SACLA_SLOPE))
    a1.plot(i[good], ours[good], ".", color="C0", ms=3, label="ours: motor + P(edge) - P(0), good edges")
    a1.plot(i[~good], ours[~good], "x", color="C3", ms=4, label="ours, edge not good")
    d = ours - theirs
    a2.plot(i, d, ".", color="C0", ms=3)
    a2.set_ylabel("ours - SACLA (pulse)")
    a2.set_xlabel("shot (runs {})".format(", ".join(str(r) for r in runs)))
    a1.set_ylabel("timing (pulse)")
    for k in np.flatnonzero(np.diff(p["run"].to_numpy(int))) + 1:
        for a in (a1, a2):
            a.axvline(k - 0.5, color="0.5", lw=0.8, ls="--")
    a1.legend(fontsize=8, loc="best")
    for a in (a1, a2):
        a.grid(alpha=0.3)
    ok = np.isfinite(d) & good
    out = None  # type: Optional[Dict[str, float]]
    if ok.any():
        q = np.percentile(d[ok], [15.87, 50, 84.13])
        out = {"median": float(q[1]), "lo": float(q[0]), "hi": float(q[2]), "sigma": robust_sigma(d[ok]),
               "spread_ours": robust_sigma(ours[ok]), "spread_sacla": robust_sigma(theirs[ok])}
        a2.set_title("ours - SACLA (good edges): median {:.2f}, 16-84 %: {:.2f} .. {:.2f}, robust sigma {:.2f} pulse"
                     .format(q[1], q[0], q[2], out["sigma"]), fontsize=9)
    fig.suptitle(title)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=100)
    return out


def compare(args: Any) -> Optional[str]:
    import pandas as pd

    from silas.core import mpi, names, scan

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
    given = args.sacla_dir or s.get("sacla_dir")
    if not given:
        say("where are SACLA's results? compare_tt_sacla(..., sacla_dir=<folder of <run>.csv>) or once "
            "cfg.set(\"timetool.sacla_dir\", <folder>)")
        return None
    sacla_dir = os.path.abspath(os.path.expanduser(str(given)))
    have = [r for r in runs if os.path.exists(os.path.join(sacla_dir, "{}.csv".format(r)))]
    missing = [r for r in runs if r not in have]
    if missing:
        say("no SACLA results in {} for runs {}: make them with\n  TimingMonitorAnalyzerCUI <settings file> "
            "<first run> <last run>\n(its output folders set to your own, see the time-tool docs)".format(
                sacla_dir, ", ".join(str(r) for r in missing)))
    if not have:
        return None
    if not s["background"].get("file"):
        say("no time-tool background: set_tt_background(run=...) first")
        return None
    cal = s.get("calibration")
    say("comparing our time-tool edges with SACLA's: {} ({} runs with SACLA results)".format(label, len(have)))
    say("  ROI {}, binning {}, background {} ({})".format(s.get("roi") or "whole image", s.get("binning"),
                                                         s["background"].get("source"), s["background"].get("mode")))
    say("  edge: {}".format(timetool.edge_text(s["edge"])))
    say("  calibration: {}".format(timetool.calibration_text(cal)))
    say("  scanned delay motor (ours): {}".format(motor), flush=True)
    t0 = time.time()
    jobs = make_jobs(cfg.path, have, args.max_tags, motor, s, args.camera)
    parts = map_jobs(jobs, args.workers)
    if parts is None:                 # an MPI worker: done
        return None
    rows = [row for p in parts for row in p["rows"]]
    print("our edges of {} timing shots in {:.1f} s".format(len(rows), time.time() - t0), flush=True)
    if not rows:
        return None
    ours = pd.DataFrame(rows).rename(columns={"set_value": "motor"})
    ours["ours"] = True
    sacla_parts = []
    for r in have:
        df = read_sacla(sacla_dir, r)
        if df is not None:
            df["run"] = r
            sacla_parts.append(df)
    theirs = pd.concat(sacla_parts, ignore_index=True)
    shots = ours.merge(theirs, on=["run", "tag"], how="outer").sort_values(["run", "tag"])
    shots["good"] = shots["good"].fillna(False).astype(bool)
    shots["ours"] = shots["ours"].fillna(False).astype(bool)
    shots["diff"] = shots["edge"] - shots["sacla_fit"]
    shots["diff_deriv"] = shots["edge_deriv"] - shots["sacla_deriv"]
    shots["shift_ours"] = timetool.edge_shift(shots["edge"].to_numpy(float), cal)
    shots["delay_ours"] = shots["motor"] + shots["shift_ours"]
    shots["delay_sacla"] = shots["sacla_motor"] - args.sacla_slope * shots["sacla_fit"]
    table = run_table(shots, have)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(timetool.background_dir(cfg), "compare_{}-{}_{}".format(have[0], have[-1], stamp))
    os.makedirs(out)
    shots.to_csv(os.path.join(out, "shots.csv"), index=False)
    table.to_csv(os.path.join(out, "runs.csv"), index=False)
    pd.set_option("display.width", 180)
    print(table.to_string(index=False, float_format=lambda v: "{:.4g}".format(v)))
    both = shots[np.isfinite(shots["edge"]) & np.isfinite(shots["sacla_fit"])]
    good = both[both["good"] & (both["sacla_good_fit"] != 0)]
    d = good["diff"].to_numpy(float)
    dd = good["diff_deriv"].to_numpy(float)
    summary = {}  # type: Dict[str, Any]
    summary.update({"source": label, "runs": have, "sacla_dir": sacla_dir, "shots_ours": int(len(ours)),
               "shots_sacla": int(len(theirs)), "both": int(len(both)), "good_both": int(len(good)),
               "ours_good": int(both["good"].sum()), "sacla_good_fit": int((both["sacla_good_fit"] == 1).sum()),
               "settings": s, "made": time.strftime("%Y-%m-%d %H:%M:%S")})
    if d.size:
        q = np.percentile(d, [15.87, 50, 84.13])
        summary.update(diff_median=float(q[1]), diff_lo=float(q[0]), diff_hi=float(q[2]), diff_sigma=robust_sigma(d),
                       diff_deriv_median=float(np.median(dd)), diff_deriv_sigma=robust_sigma(dd),
                       corr=float(np.corrcoef(good["edge"], good["sacla_fit"])[0, 1]) if d.size > 2 else None)
        print("all runs: {} shots in both, {} good in both (ours {}, SACLA fit {})".format(
            summary["both"], summary["good_both"], summary["ours_good"], summary["sacla_good_fit"]))
        print("ours - SACLA (fit): median {:.2f} px, 16-84 %: {:.2f} .. {:.2f} px, robust sigma {:.2f} px, "
              "correlation {:.4f}".format(q[1], q[0], q[2], summary["diff_sigma"], summary["corr"] or np.nan))
        print("ours - SACLA (derivative edges): median {:.2f} px, robust sigma {:.2f} px".format(
            summary["diff_deriv_median"], summary["diff_deriv_sigma"]))
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1, default=str)
    from silas.offline.tt_calibration import _plt

    plt, show = _plt(args.show)
    plot(plt, shots, table, "time-tool edges: ours - SACLA, {}".format(label), os.path.join(out, "compare.png"))
    if cal:
        first = have[:max(1, int(args.plot_runs))]
        dsum = plot_delay(plt, shots, first, motor, "corrected timing per shot (pulse): ours and SACLA's, runs {}".format(
            ", ".join(str(r) for r in first)), os.path.join(out, "delay.png"))
        if dsum:
            print("timing ours - SACLA (runs {}, good edges): median {:.2f} pulse, 16-84 %: {:.2f} .. {:.2f}, robust "
                  "sigma {:.2f} pulse; shot-to-shot spread ours {:.2f}, SACLA {:.2f} pulse".format(
                      ", ".join(str(r) for r in first), dsum["median"], dsum["lo"], dsum["hi"], dsum["sigma"],
                      dsum["spread_ours"], dsum["spread_sacla"]))
            summary["delay_first_runs"] = dsum
            with open(os.path.join(out, "summary.json"), "w") as fh:
                json.dump(summary, fh, indent=1, default=str)
    else:
        print("no calibration (calibrate_tt): the timing per shot is not compared")
    print("results in {}".format(out), flush=True)
    if show:
        show_plots(plt)
    from silas.core import plotting

    plotting.close_all(plt)     # (in a notebook the shown widgets stay: zoom, pan)
    return out


@sacla.requires_sacla
def run_live(args: Any) -> None:
    compare(args)


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="our time-tool edges against SACLA's TimingMonitorAnalyzer results")
    ap.add_argument("--scan", type=int, default=-1, help="a scan as in scan_info (-1 newest, a run number, ...)")
    ap.add_argument("--runs", nargs="+", help="runs instead of a scan of the run log, e.g. 1234501-1234551")
    ap.add_argument("--sacla-dir", help="folder with SACLA's <run>.csv / <run>.tma.h5 (default: the config's "
                                        "timetool.sacla_dir)")
    ap.add_argument("--motor", help="the scanned delay motor (default: the scan's motor, else {})".format(DEFAULT_MOTOR))
    ap.add_argument("--max-tags", type=int, help="at most this many tags per run (evenly spread)")
    ap.add_argument("--workers", type=int, default=6, help="processes without mpirun (default 6; under mpirun "
                                                           "the MPI processes)")
    ap.add_argument("--sacla-slope", type=float, default=SACLA_SLOPE,
                    help="SACLA's calibration, pulse / px (default 0.0026 / 0.00666 = {:.5f})".format(SACLA_SLOPE))
    ap.add_argument("--plot-runs", type=int, default=2, help="runs in the timing plot (default the first 2)")
    ap.add_argument("--camera", help="camera detector ID (default: the config's, else the first OPAL)")
    ap.add_argument("--show", action="store_true", help="also show the plot (window, or the browser on the HPC)")
    try:
        run_live(ap.parse_args(argv))
    except KeyboardInterrupt:
        print("stopped (Ctrl-C)")


if __name__ == "__main__":
    main()
