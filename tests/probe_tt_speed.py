#!/usr/bin/env python3
"""Can we keep up with the time-tool camera? Reading and trace speed (HPC compute node, read-only).

    cd ~/SILAS && bt_env
    python tests/probe_tt_speed.py --run 1234501
    python tests/probe_tt_speed.py --run 1234501 --background-run 1234479 --workers 1 2 4 6

Shows step by step:
  1. the camera (config, or the first OPAL / IPX / CHET of the run), image size, type, values
  2. the shot classes of the run(s) from the SyncDB: timing (X-rays + laser on, beam OK),
     background (laser on, X-rays off), X-rays only, other; also for --background-run
  3. one process, --shots shots: time to read an image (stpy) and to make the trace
     (rows and cols binning, the configured ROI and the whole image), and to divide by a
     background trace
  4. --workers N ...: shots per second for read + trace with N processes (multiprocessing,
     each process its share of the tags), with the time to start the processes included
The live limit is ~33 ms per shot at 30 Hz (17 ms at 60 Hz) per reader process.
Nothing is written.
"""
import argparse
import os
import sys
import time
from typing import Any, Dict, List, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import silas  # noqa: E402,F401  applies the profile's sys.path
from silas.core import db, runner, timetool  # noqa: E402
from silas.core.config import Config  # noqa: E402


def read_and_trace(job: Tuple[str, int, int, List[int], Any, str]) -> Tuple[int, int, float]:
    """Pool worker: (shots done, not readable, seconds) for its tags."""
    import stpy

    det_id, bl, run, tags, roi, binning = job
    t0 = time.perf_counter()
    reader = stpy.StorageReader(det_id, bl, (run,))
    buf = stpy.StorageBuffer(reader)
    n, bad = 0, 0
    for tag in tags:
        try:
            reader.collect(buf, int(tag))
            img = buf.read_det_data(0)
        except stpy.APIError:
            bad += 1
            continue
        timetool.trace(img, roi, binning)
        n += 1
    return n, bad, time.perf_counter() - t0


def ms(values: List[float]) -> str:
    a = np.array(values) * 1000.0
    return "median {:.2f} ms (16-84 %: {:.2f} .. {:.2f})".format(float(np.median(a)), float(np.percentile(a, 16)),
                                                                 float(np.percentile(a, 84)))


def main() -> None:
    ap = argparse.ArgumentParser(description="time-tool camera: reading and trace speed")
    ap.add_argument("--run", type=int, required=True, help="a stored run with the camera")
    ap.add_argument("--background-run", type=int, help="also show the shot classes of this run")
    ap.add_argument("--shots", type=int, default=200, help="shots for the one-process timing (default 200)")
    ap.add_argument("--workers", type=int, nargs="*", default=[1, 2, 4, 6],
                    help="process counts for the parallel test (default 1 2 4 6; none: skip)")
    ap.add_argument("--parallel-shots", type=int, default=1200, help="shots for each parallel test (default 1200)")
    args = ap.parse_args()
    cfg = Config()
    bl = cfg.beamline
    s = timetool.settings(cfg)
    roi, binning = s.get("roi"), s.get("binning", "rows")

    # ---------------------------------------------------------------- 1. camera
    import stpy

    det_id = timetool.camera(cfg, db.run_detectors(bl, args.run))
    tags = list(db.tags(bl, args.run))
    reader = stpy.StorageReader(det_id, bl, (args.run,))
    buf = stpy.StorageBuffer(reader)
    reader.collect(buf, tags[0])
    img = buf.read_det_data(0)
    info = dict(buf.read_det_info(0))
    print("1. camera {} in run {} ({} tags, tag step {})".format(det_id, args.run, len(tags),
                                                                 tags[1] - tags[0] if len(tags) > 1 else "?"))
    print("   image {} x {} (rows x cols), {}, values {:.1f} .. {:.1f}, mean {:.1f}, integers: {}".format(
        img.shape[0], img.shape[1], img.dtype, float(img.min()), float(img.max()), float(img.mean()),
        bool(np.all(img == np.round(img)))))
    print("   detector info: {}".format(", ".join("{}={}".format(k, v) for k, v in sorted(info.items())
                                                  if not k.startswith("mp_"))))
    print("   ROI {} -> {}, binning {}".format(roi or "whole image", timetool.roi_box(roi, img.shape), binning))

    # ---------------------------------------------------------------- 2. classes
    laser, on = timetool.laser_settings(cfg)
    print("2. shot classes (X-ray shutter = {}, laser = {} == {:g}):".format(
        timetool.XRAY_SHUTTER, laser or "not set", on))
    flags = {}  # type: Dict[int, Any]
    for r in [args.run] + ([args.background_run] if args.background_run else []):
        f = timetool.run_flags(cfg, r)
        flags[r] = f
        print("   run {}: {} tags: {}".format(r, len(f["tags"]), timetool.class_counts(f["cls"])))
        for name in ("xray", "laser"):
            v = np.asarray(f[name], float)
            counts = ["{:g}: {}".format(u, int(np.sum(v == u))) for u in sorted(set(v[np.isfinite(v)].tolist()))]
            n_nan = int(np.sum(~np.isfinite(v)))
            if n_nan:
                counts.append("NaN (not readable): {}".format(n_nan))
            print("     {:5s} {}".format(name, ", ".join(counts[:8]) or "-"))

    # ---------------------------------------------------------------- 3. one process
    n = min(args.shots, len(tags))
    pick = tags[:n]
    t_read = []  # type: List[float]
    t_rows = []  # type: List[float]
    t_cols = []  # type: List[float]
    t_full = []  # type: List[float]
    t_corr = []  # type: List[float]
    bg = None  # type: Any
    for tag in pick:
        t0 = time.perf_counter()
        try:
            reader.collect(buf, tag)
            im = buf.read_det_data(0)
        except stpy.APIError:
            continue
        t1 = time.perf_counter()
        tr = timetool.trace(im, roi, "rows")
        t2 = time.perf_counter()
        timetool.trace(im, roi, "cols")
        t3 = time.perf_counter()
        timetool.trace(im, None, binning)
        t4 = time.perf_counter()
        if bg is None:
            bg = tr.copy()
        timetool.correct(tr, bg, "divide")
        t5 = time.perf_counter()
        t_read.append(t1 - t0)
        t_rows.append(t2 - t1)
        t_cols.append(t3 - t2)
        t_full.append(t4 - t3)
        t_corr.append(t5 - t4)
    print("3. one process, {} shots:".format(len(t_read)))
    print("   read (stpy collect + read_det_data)   {}".format(ms(t_read)))
    print("   trace, ROI, rows binning              {}".format(ms(t_rows)))
    print("   trace, ROI, cols binning              {}".format(ms(t_cols)))
    print("   trace, whole image                    {}".format(ms(t_full)))
    print("   divide by a background trace          {}".format(ms(t_corr)))
    total = float(np.median(t_read) + np.median(t_rows) + np.median(t_corr))
    print("   per shot (read + trace + divide): {:.1f} ms -> {:.0f} shots/s in one process".format(
        1000 * total, 1.0 / max(total, 1e-9)))

    # ---------------------------------------------------------------- 4. parallel
    if not args.workers:
        return
    import multiprocessing

    m = min(args.parallel_shots, len(tags))
    work = tags[:m]
    print("4. read + trace of {} shots with N processes (incl. starting them):".format(m))
    ctx = multiprocessing.get_context("spawn")
    for w in args.workers:
        w = runner.cap_workers(w, "workers")
        jobs = [(det_id, bl, args.run, [int(t) for t in p], roi, binning) for p in np.array_split(work, w) if len(p)]
        t0 = time.perf_counter()
        with ctx.Pool(len(jobs), initializer=runner.worker_ignores_ctrl_c) as pool:
            res = pool.map(read_and_trace, jobs)
        dt = time.perf_counter() - t0
        done = sum(r[0] for r in res)
        inner = max(r[2] for r in res)
        print("   {:2d} processes: {:5.0f} shots/s   ({} shots in {:.2f} s; slowest process {:.2f} s = {:.0f} shots/s "
              "without the start)".format(w, done / dt, done, dt, inner, done / max(inner, 1e-9)), flush=True)


if __name__ == "__main__":
    main()
