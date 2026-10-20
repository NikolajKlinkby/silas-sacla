#!/usr/bin/env python3
"""Where does the time of the offline scan analysis go? (HPC compute node, read-only)

    cd ~/SILAS && bt_env
    python tests/probe_scan_speed.py --scan 1234501 --name octal
    python tests/probe_scan_speed.py --scan 1234501 --name octal --shots 500 --workers 1 2 4 8

Times every step of the analysis separately, with the SACLA libraries alone:

  1. run list:      dbpy tag list and high tag of a run
  2. shot flags:    one dbpy read per point detector (all tags of a run), beam status
  3. stpy readers:  opening a StorageReader + StorageBuffer per panel and run
  4. per shot:      stpy collect, read_det_data, and our ROI sum (dark, threshold, keV)
  5. scaling:       --workers N processes reading shots at the same time (contiguous tags of
                    the first runs): if shots/s stops growing with N, the storage / stpy is
                    the limit, not our code

and prints an estimate for the whole scan: once with the flags and readers per run (as now:
every worker repeats them for every run) and once with them done once per run.
Nothing is written to SACLA; the result is also saved to output/probe/scan_speed_<scan>.json.
"""
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import silas  # noqa: E402,F401  applies the profile's sys.path
from silas.core import db, mpccd, names, roi, scan, scanroi  # noqa: E402
from silas.core.config import Config  # noqa: E402


def timed(fn: Any, *args: Any) -> Tuple[Any, float]:
    t = time.perf_counter()
    out = fn(*args)
    return out, time.perf_counter() - t


def read_shots(payload: Tuple[int, List[str], int, List[int]]) -> Tuple[int, float]:
    """Scaling test, one process: read all panels of these tags (collect + read_det_data)."""
    import stpy

    bl, det_ids, run, tags = payload
    readers = [stpy.StorageReader(d, bl, (run,)) for d in det_ids]
    buffers = [stpy.StorageBuffer(r) for r in readers]
    t = time.perf_counter()
    n = 0
    for tag in tags:
        try:
            for r, b in zip(readers, buffers):
                r.collect(b, int(tag))
                b.read_det_data(0)
            n += 1
        except stpy.APIError:
            pass
    return n, time.perf_counter() - t


def main() -> None:
    import dbpy
    import stpy

    ap = argparse.ArgumentParser(description="time the steps of the offline scan analysis")
    ap.add_argument("--scan", type=int, default=-1, help="as scan_info: -1 newest, or a run number of the scan")
    ap.add_argument("--name", help="MPCCD array (default: use_mpccd)")
    ap.add_argument("--runs", type=int, default=3, help="runs to time the per-run steps on (default 3)")
    ap.add_argument("--shots", type=int, default=300, help="shots to time the per-shot steps on (default 300)")
    ap.add_argument("--workers", type=int, nargs="*", default=[1, 2, 4, 8], help="scaling test (default 1 2 4 8)")
    args = ap.parse_args()

    cfg = Config()
    bl = cfg.beamline
    key, settings = mpccd.get_array(cfg, args.name)
    det_ids = list(settings["panels"])
    sc = scan.scan_runs(cfg, args.scan)
    runs = [int(r["run"]) for r in sc["runs"]]
    print("scan {}: {} runs, array {} ({} panels)".format(sc["scan_id"], len(runs), key, len(det_ids)))
    res = {"scan": sc["scan_id"], "runs": len(runs), "array": key}  # type: Dict[str, Any]

    # ---------------------------------------------------------------- 1. run list
    t_tags = []  # type: List[float]
    t_high = []  # type: List[float]
    n_tags = []  # type: List[int]
    all_tags = {}  # type: Dict[int, List[int]]
    for run in runs[:args.runs]:
        tags, dt = timed(db.tags, bl, run)
        all_tags[run] = list(tags)
        t_tags.append(dt)
        n_tags.append(len(tags))
        _, dt = timed(db.high_tag, bl, run)
        t_high.append(dt)
    res["tags_per_run"] = float(np.mean(n_tags))
    res["t_taglist_s"] = float(np.mean(t_tags))
    res["t_hightag_s"] = float(np.mean(t_high))
    print("1. run list: tag list {:.3f} s, high tag {:.3f} s per run ({:.0f} tags per run)".format(
        res["t_taglist_s"], res["t_hightag_s"], res["tags_per_run"]))

    # ---------------------------------------------------------------- 2. shot flags
    run0 = runs[0]
    tags0 = all_tags[run0]
    taghi = db.high_tag(bl, run0)
    pump, _ = scanroi.pump_settings(cfg)
    wanted = [("laser_shutter", names.resolve(cfg, "laser_shutter"))]
    if pump:
        wanted.append(("pump", pump))
    wanted += scanroi.reference_settings(cfg)          # the reference detectors (set_references)
    flags = {}  # type: Dict[str, float]
    for label, true in wanted:
        _, dt = timed(db.read_point, true, taghi, tags0, "nan")
        flags[label] = dt
    t = time.perf_counter()
    for i in range(0, len(tags0), 2000):
        try:
            dbpy.read_beamstatuslist(bl, taghi, tuple(int(x) for x in tags0[i:i + 2000]))
        except dbpy.APIError:
            pass
    flags["beam_status"] = time.perf_counter() - t
    res["t_flags_s"] = flags
    t_flags = sum(flags.values())
    print("2. shot flags of one run ({} tags): {:.3f} s in total".format(len(tags0), t_flags))
    for label, dt in flags.items():
        print("     {:<14s} {:.3f} s".format(label, dt))

    # ---------------------------------------------------------------- 3. stpy readers
    infos = []  # type: List[Dict[str, Any]]
    t_open = []  # type: List[float]
    for run in runs[:args.runs]:
        t = time.perf_counter()
        rs = [stpy.StorageReader(d, bl, (run,)) for d in det_ids]
        bs = [stpy.StorageBuffer(r) for r in rs]
        t_open.append(time.perf_counter() - t)
        if not infos:
            for r, b in zip(rs, bs):
                r.collect(b, int(all_tags[run][0]))
                infos.append(dict(b.read_det_info(0)))
    res["t_open_s"] = float(np.mean(t_open))
    print("3. stpy readers for {} panels: {:.3f} s per run".format(len(det_ids), res["t_open_s"]))

    # ---------------------------------------------------------------- 4. per shot
    rmap, kev, corr = roi.analysis_setup(det_ids, infos, settings, shift_from_data=True)
    panels = rmap.panels if rmap is not None else list(range(len(det_ids)))
    readers = [stpy.StorageReader(det_ids[k], bl, (run0,)) for k in panels]
    buffers = [stpy.StorageBuffer(r) for r in readers]
    thr = corr.threshold_adu if corr.settings.get("threshold") else None
    shots = tags0[:args.shots]
    t_col, t_read, t_sum = 0.0, 0.0, 0.0
    n_ok = 0
    for tag in shots:
        data = {}  # type: Dict[int, Any]
        try:
            for k, r, b in zip(panels, readers, buffers):
                t = time.perf_counter()
                r.collect(b, int(tag))
                t_col += time.perf_counter() - t
                t = time.perf_counter()
                data[k] = b.read_det_data(0)
                t_read += time.perf_counter() - t
        except stpy.APIError:
            continue
        if rmap is not None:
            t = time.perf_counter()
            rmap.sums(data, kev, corr.dark, thr)
            t_sum += time.perf_counter() - t
        n_ok += 1
    n_ok = max(n_ok, 1)
    per = {"collect": t_col / n_ok, "read_det_data": t_read / n_ok, "roi_sums": t_sum / n_ok}
    res["t_shot_s"] = per
    res["panels_read"] = len(panels)
    t_shot = sum(per.values())
    print("4. per shot ({} shots, {} panel(s) read): {:.2f} ms".format(n_ok, len(panels), 1000 * t_shot))
    for label, dt in per.items():
        print("     {:<14s} {:.2f} ms".format(label, 1000 * dt))

    # ---------------------------------------------------------------- 5. scaling
    import multiprocessing

    from silas.core import runner

    ctx = multiprocessing.get_context("spawn")
    scaling = {}  # type: Dict[str, float]
    det_read = [det_ids[k] for k in panels]
    for j, n in enumerate(args.workers):
        # a run not read before in this probe (so the file system cache does not help), the
        # same number of shots in total, split into contiguous blocks
        run = runs[(j + 1) % len(runs)]
        tags = list(db.tags(bl, run))[:args.shots]
        m = max(1, len(tags) // n)
        payloads = [(bl, det_read, run, tags[w * m:(w + 1) * m]) for w in range(n)]  # type: List[Any]
        t = time.perf_counter()
        with ctx.Pool(n, initializer=runner.worker_ignores_ctrl_c) as pool:
            parts = pool.map(read_shots, payloads)
        wall = time.perf_counter() - t
        done = sum(p[0] for p in parts)
        inner = max(p[1] for p in parts)
        scaling[str(n)] = done / max(inner, 1e-9)
        print("5. {} worker(s), run {}: {:.0f} shots/s reading ({} shots; {:.1f} s incl. starting "
              "the processes)".format(n, run, scaling[str(n)], done, wall))
    res["scaling_shots_per_s"] = scaling

    # ---------------------------------------------------------------- estimate
    n_runs = len(runs)
    n_shots = int(res["tags_per_run"] * n_runs)
    per_run = res["t_taglist_s"] + res["t_hightag_s"]
    now_w = max(args.workers)
    best = max(scaling.values()) if scaling else 1.0 / max(t_shot, 1e-9)
    est_now = n_runs * per_run + (n_runs * (t_flags + res["t_open_s"]) * now_w
                                  + n_shots * t_shot) / now_w
    est_once = n_runs * (per_run + t_flags) + n_runs * res["t_open_s"] + n_shots / best
    res["estimate_s"] = {"as_now": est_now, "flags_once": est_once}
    print("\nestimate for the whole scan ({} runs, ~{} shots, {} workers):".format(n_runs, n_shots, now_w))
    print("  as now (every worker reads the flags and opens readers for every run): {:.0f} s".format(est_now))
    print("  flags once per run, each worker a block of runs:                       {:.0f} s".format(est_once))
    print("  of which reading the images at the best measured rate:                 {:.0f} s".format(n_shots / best))
    out = os.path.join(cfg.output_dir(), "probe")
    if not os.path.isdir(out):
        os.makedirs(out)
    path = os.path.join(out, "scan_speed_{}.json".format(sc["scan_id"]))
    with open(path, "w") as fh:
        json.dump(res, fh, indent=1)
    print("saved", path)


if __name__ == "__main__":
    main()
