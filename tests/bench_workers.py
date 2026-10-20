#!/usr/bin/env python3
"""Shots per second of the offline scan analysis against the number of workers (HPC, read-only).

    cd ~/SILAS && bt_env
    python tests/bench_workers.py --scan 1234501 --name octal
    python tests/bench_workers.py --scan 1234501 --name octal --workers 1 2 4 6 8 12 16 --shots 4000

For every number of workers N it runs the analysis's own reading (silas.offline.scan_analysis
.process: shot flags, stpy images of the ROI panels, ROI sums) on a block of the scan's shots,
split into N contiguous parts, one process each. Every test gets its OWN block of shots (the
first test the first --shots shots of the scan, the next test the next ones, ...), so the file
system cache does not make later tests look faster; if the scan has too few shots, blocks are
reused and a warning is printed.

Measured per test:
  reading     shots / time of the slowest worker (from its start to its end): the rate
  with start  shots / time including starting the worker processes (about 2 s)

Written to <output>/probe/: bench_workers_<scan>.csv, .json and .svg (the graph: measured
rates and the ideal line N x the rate of one worker). For the documentation copy the .svg to
docs/src/img/bench_workers.svg on the laptop. Nothing is written to SACLA.
"""
import argparse
import json
import os
import socket
import sys
import time
from typing import Any, Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import silas  # noqa: E402,F401  applies the profile's sys.path
from silas.core import mpccd, roi, runner  # noqa: E402
from silas.core.config import Config  # noqa: E402
from silas.offline import scan_analysis  # noqa: E402


def bench_part(payload: Tuple[Any, List[Tuple[int, int]], Dict[int, Dict[str, Any]], List[Dict[str, Any]]]) \
        -> Tuple[int, float, Dict[str, float]]:
    """One worker: the analysis's reading of its shots. Returns (shots read, seconds, timing)."""
    name, items, run_info, infos = payload
    cfg = Config()
    _, settings = mpccd.get_array(cfg, name)
    timing = {}  # type: Dict[str, float]
    t = time.perf_counter()
    rows = scan_analysis.process(cfg, settings, items, run_info, infos, timing=timing)
    return sum(1 for r in rows if r["read"]), time.perf_counter() - t, timing


def plot(results: List[Dict[str, Any]], title: str, path: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = [r["workers"] for r in results]
    rate = [r["shots_per_s"] for r in results]
    wall = [r["shots_per_s_with_start"] for r in results]
    one = next((r["shots_per_s"] for r in results if r["workers"] == 1), rate[0] / max(n[0], 1))
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    ax.plot([0, max(n)], [0, one * max(n)], color="0.6", ls="--", lw=1.0, label="ideal: N x one worker")
    ax.plot(n, rate, color="C0", marker="o", lw=1.6, label="reading")
    ax.plot(n, wall, color="C1", marker="s", mfc="white", lw=1.0, ls=":", label="including starting the workers")
    for x, y in zip(n, rate):
        ax.annotate("{:.0f}".format(y), (x, y), textcoords="offset points", xytext=(0, 7), ha="center",
                    fontsize=8, color="C0")
    ax.set_xlabel("workers (processes)")
    ax.set_ylabel("shots per second")
    ax.set_xlim(0, max(n) + 0.5)
    ax.set_ylim(0, max(max(rate), max(wall)) * 1.25)
    ax.set_xticks(n)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    ax.set_title(title, fontsize=9)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main() -> None:
    import multiprocessing

    ap = argparse.ArgumentParser(description="shots/s of the scan analysis against the number of workers")
    ap.add_argument("--scan", type=int, default=-1, help="as scan_info: -1 newest, or a run number of the scan")
    ap.add_argument("--name", help="MPCCD array (default: use_mpccd)")
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 6, 8, 12, 16],
                    help="numbers of workers to test (default 1 2 4 6 8 12 16; more than the cores are skipped)")
    ap.add_argument("--shots", type=int, default=3000, help="shots per test (default 3000)")
    args = ap.parse_args()

    cfg = Config()
    key, settings = mpccd.get_array(cfg, args.name)
    if not settings.get("rois"):
        sys.exit("no ROIs for {}: add_roi(...) first (only the ROI panels are read)".format(key))
    cores = scan_analysis.available_cores()
    tests = [n for n in args.workers if 1 <= n <= cores]
    skipped = [n for n in args.workers if n not in tests]
    if skipped:
        print("skipped (this job has {} cores): {}".format(cores, skipped))
    print("preparing: tag lists of the scan (dbpy) ...", flush=True)
    job = scan_analysis.prepare(cfg, settings, args.scan, None)
    items = sorted(job["items"])
    sc = job["scan"]
    rmap, _, _ = roi.analysis_setup(list(settings["panels"]), job["infos"], settings, shift_from_data=True)
    panels = len(rmap.panels) if rmap is not None else len(settings["panels"])
    host = socket.gethostname()
    print("scan {}: {} shots; {} per test; {} panel(s) read per shot; {} on {} ({} cores)".format(
        sc["scan_id"], len(items), args.shots, panels, key, host, cores), flush=True)
    if args.shots * len(tests) > len(items):
        print("warning: the scan has too few shots for a fresh block per test: blocks are reused "
              "(the file system cache may make later tests faster)")

    ctx = multiprocessing.get_context("spawn")
    results = []  # type: List[Dict[str, Any]]
    for i, n in enumerate(tests):
        start = (i * args.shots) % max(len(items) - args.shots, 1)
        block = items[start:start + args.shots]
        size = (len(block) + n - 1) // n
        payloads = [(key, block[w * size:(w + 1) * size], job["run_info"], job["infos"])
                    for w in range(n)]  # type: List[Any]
        t = time.perf_counter()
        with ctx.Pool(n, initializer=runner.worker_ignores_ctrl_c) as pool:
            parts = pool.map(bench_part, payloads)
        wall = time.perf_counter() - t
        read = sum(p[0] for p in parts)
        slowest = max(p[1] for p in parts)
        timing = {}  # type: Dict[str, float]
        for p in parts:
            for k, v in p[2].items():
                timing[k] = timing.get(k, 0.0) + v
        r = {"workers": n, "shots": read, "seconds": slowest, "seconds_with_start": wall,
             "shots_per_s": read / max(slowest, 1e-9), "shots_per_s_with_start": read / max(wall, 1e-9),
             "images_ms_per_shot": 1000.0 * timing.get("images", 0.0) / max(read, 1),
             "first_shot": list(block[0]) if block else None}
        results.append(r)
        print("{:3d} workers: {:6.0f} shots/s reading, {:6.0f} with start-up   ({} shots, {:.1f} s; stpy "
              "{:.1f} ms per shot and worker)".format(n, r["shots_per_s"], r["shots_per_s_with_start"], read,
                                                     slowest, r["images_ms_per_shot"]), flush=True)

    out = os.path.join(cfg.output_dir(), "probe")
    if not os.path.isdir(out):
        os.makedirs(out)
    base = os.path.join(out, "bench_workers_{}".format(sc["scan_id"]))
    meta = {"scan": sc["scan_id"], "array": key, "panels_read": panels, "shots_per_test": args.shots,
            "host": host, "cores": cores, "made": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(base + ".json", "w") as fh:
        json.dump({"meta": meta, "results": results}, fh, indent=1)
    with open(base + ".csv", "w") as fh:
        fh.write("workers,shots,seconds,seconds_with_start,shots_per_s,shots_per_s_with_start,images_ms_per_shot\n")
        for r in results:
            fh.write("{workers},{shots},{seconds:.3f},{seconds_with_start:.3f},{shots_per_s:.1f},"
                     "{shots_per_s_with_start:.1f},{images_ms_per_shot:.2f}\n".format(**r))
    title = "scan analysis reading speed: scan {}, {} ({} panel(s) read per shot)\n{}, {} cores, {} shots per test, " \
            "{}".format(sc["scan_id"], key, panels, host, cores, args.shots, time.strftime("%Y-%m-%d"))
    plot(results, title, base + ".svg")
    print("written: {0}.svg, {0}.csv, {0}.json".format(base))
    print("for the docs, on the LAPTOP:  scp sacla_HPC:{}.svg docs/src/img/bench_workers.svg".format(base))


if __name__ == "__main__":
    main()
