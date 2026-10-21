"""Dark of an MPCCD array from a stored run (HPC, stpy): the mean raw image of the run's shots.

    set_dark(run=1234470)                              # session: all shots of a dark run
    set_dark(run=1234470, shots="xray_off")            # only shots with the X-ray shutter closed
    python -m silas.offline.dark --run 1234470 [--array octal] [--workers 6] [--max-shots N] [--xray-off]

The offline counterpart of capture_dark() (silas.online.dark, live at the beamline). Take a
run recorded with the X-rays blocked. shots="all" (default) averages every shot of the run, as
for the time-tool background: the X-rays can be blocked by something the flags do not show;
check the run first (start_mpccd_viewer(run=N) shows no photons). shots="xray_off": only the
shots with the X-ray shutter (alias xfel_shutter) closed.

The mean is saved to <output>/darks/dark_<array>_run<N>_<time>.npz (the same file as
capture_dark writes) and set as the array's dark_file, with dark subtraction switched on; a running
viewer picks it up. Shots a panel has no data for are skipped (counted as not readable).
"""
import argparse
import datetime
import multiprocessing
import os
import time
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import db, mpccd, names, runner, sacla
from silas.core.config import Config
from silas.core.corrections import save_dark

XRAY_SHUTTER = "xfel_shutter"      # alias of the XFEL shutter point detector (0 = X-rays off)


def _sum_part(job: Tuple[int, List[str], int, List[int]]) -> Tuple[Optional[np.ndarray], int, int]:
    """Pool worker: (sum of the raw panel images, shots summed, shots not readable) of some tags."""
    from silas.offline.scan_analysis import PanelReader

    bl, det_ids, run, tags = job
    reader = PanelReader(bl, det_ids)
    panels = list(range(len(det_ids)))
    total = None  # type: Optional[np.ndarray]
    n, bad = 0, 0
    for tag in tags:
        data = reader.read(run, int(tag), panels)
        if data is None:
            bad += 1
            continue
        x = np.stack([np.asarray(data[k], np.float64) for k in panels])
        total = x if total is None else total + x
        n += 1
    return total, n, bad


def pick_tags(cfg: Config, bl: int, run: int, shots: str, max_shots: Optional[int]) -> np.ndarray:
    """The run's tags (all, or those with the X-ray shutter closed), at most max_shots spread evenly."""
    tags = np.array(db.tags(bl, run), np.int64)
    if shots == "xray_off":
        equip = names.resolve(cfg, cfg.get(XRAY_SHUTTER, None))
        if equip is None:
            raise ValueError("no X-ray shutter defined (set {} in the config)".format(XRAY_SHUTTER))
        flag = np.array(db.read_point(equip, db.high_tag(bl, run), list(tags), errors="nan"), float)
        print("X-ray shutter ({}): {} closed, {} open, {} unknown".format(
            cfg.get(XRAY_SHUTTER, None), int(np.sum(flag == 0)), int(np.sum(flag > 0)), int(np.sum(np.isnan(flag)))))
        tags = tags[flag == 0]
    elif shots != "all":
        raise ValueError('shots must be "all" or "xray_off"')
    if max_shots and len(tags) > max_shots:
        tags = tags[np.linspace(0, len(tags) - 1, int(max_shots)).astype(int)]
    return tags


def dark_from_run(cfg: Config, run: int, array: Optional[str] = None, workers: int = runner.OFFLINE_WORKERS,
                  max_shots: Optional[int] = None, shots: str = "all", use: bool = True) -> Optional[str]:
    """Mean raw image of the run's shots, saved as a dark file and (use=True) set for the array.
    Returns the file, None if no shot could be read."""
    key, settings = mpccd.get_array(cfg, array)
    det_ids = [str(p) for p in settings["panels"]]
    bl = cfg.beamline
    run = int(run)
    present = set(db.run_detectors(bl, run))
    missing = [d for d in det_ids if d not in present]
    if missing:
        print("run {} has no data of {} (array {}): no dark made".format(run, ", ".join(missing), key))
        return None
    tags = pick_tags(cfg, bl, run, shots, max_shots)
    if not len(tags):
        print("run {}: no shot to average ({}): no dark made".format(run, shots))
        return None
    n_proc = runner.cap_workers(max(1, min(int(workers), len(tags))), "workers")
    parts = [list(int(t) for t in p) for p in np.array_split(tags, n_proc) if len(p)]
    print("dark of {} ({} panels) from run {}: averaging {} shots ({}) with {} processes ...".format(
        key, len(det_ids), run, len(tags), shots, len(parts)), flush=True)
    t0 = time.time()
    jobs = [(bl, det_ids, run, p) for p in parts]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(len(jobs), initializer=runner.worker_ignores_ctrl_c) as pool:
        results = pool.map(_sum_part, jobs)
    total = None  # type: Optional[np.ndarray]
    n, bad = 0, 0
    for s, k, b in results:
        n += k
        bad += b
        if s is not None:
            total = s if total is None else total + s
    if total is None or n == 0:
        print("no shot could be read ({} not readable): no dark made".format(bad))
        return None
    dt = time.time() - t0
    mean = total / n
    print("averaged {} shots in {:.1f} s{}; mean per panel (ADU): {}".format(
        n, dt, ", {} not readable".format(bad) if bad else "",
        ", ".join("{:.1f}".format(float(np.mean(m))) for m in mean)))
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(cfg.output_dir(), "darks", "dark_{}_run{}_{}.npz".format(key, run, stamp))
    save_dark(path, det_ids, mean, n, {"array": key, "run": run, "shots": shots, "not_readable": bad})
    print("dark saved: {}".format(path))
    if use:
        mpccd.set_option(cfg, "corrections.dark_file", path, key)
        mpccd.set_option(cfg, "corrections.dark", True, key)
        print("set as dark_file of {} (dark subtraction on)".format(key))
    return path


@sacla.requires_sacla
def run_live(args: Any) -> None:
    dark_from_run(Config(), args.run, args.array, args.workers, args.max_shots,
                  "xray_off" if args.xray_off else "all")


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="dark of an MPCCD array from a stored run (HPC)")
    ap.add_argument("--run", type=int, required=True, help="a run recorded with the X-rays blocked")
    ap.add_argument("--array", help="array name or alias (default: the default array)")
    ap.add_argument("--workers", type=int, default=runner.OFFLINE_WORKERS, help="processes (default 6)")
    ap.add_argument("--max-shots", type=int, help="at most this many shots, spread over the run")
    ap.add_argument("--xray-off", action="store_true", help="only shots with the X-ray shutter closed")
    args = ap.parse_args(list(argv) if argv is not None else None)
    run_live(args)


if __name__ == "__main__":
    main()
