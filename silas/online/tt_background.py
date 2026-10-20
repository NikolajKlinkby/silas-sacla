"""Make the time-tool background: the mean image of the shots of a background run.

    set_tt_background(run=1234479)                        # from the session (HPC: stored run)
    capture_tt_background(shots=300)                      # from the session (beamline: live)
    python -m silas.online.tt_background --run 1234479 [--workers 6] [--max-shots N] [--only-xray-off]
    python -m silas.online.tt_background --shots 300 [--only-xray-off] [--test]

By default ALL shots are averaged, as SACLA's timing-monitor tool does (the X-rays of a
background run may be blocked by something the shot flags do not show): block the X-rays
first. --only-xray-off: only the shots with the X-ray shutter closed and the laser on.
Stored run (HPC): averaged by a pool of processes (silas.core.timetool.background_from_run).
Live (beamline): the newest shot is taken about every 0.1 s; with --only-xray-off it is kept
until its flags reach the SyncDB (a few seconds later) and only background shots are added.
Either way the mean image is saved to <output>/timetool/ and set as the background; a
running time-tool viewer picks it up by itself.
"""
import argparse
import collections
import time
from typing import Any, Deque, List, Optional, Tuple

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import sacla, timetool
from silas.core.config import Config
from silas.online.tt_source import classify_one, open_camera

KEEP_S = 15.0        # a shot whose flags are not in the SyncDB after this long is dropped
PENDING = 60         # at most this many shots wait for their flags


def capture(args: Any) -> Optional[str]:
    """Live capture (or the test camera)."""
    cfg = Config()
    src = open_camera(cfg, test=args.test, det_id=args.camera)
    every = 0.0 if args.test else float(args.every)
    only_off = bool(args.only_xray_off) or bool(args.test)   # the test camera has edges in its X-ray shots
    pending = collections.deque()  # type: Deque[Tuple[int, float, np.ndarray]]
    total = None  # type: Optional[np.ndarray]
    n, dropped, last, t0 = 0, 0, None, time.time()
    seen = {c: 0 for c in timetool.CLASS_NAMES}
    next_take = 0.0
    last_report = time.time()
    print("capturing a time-tool background of {} shots from {} ({}) ...".format(
        args.shots, src.label, "laser ON, X-rays OFF only" if only_off else
        "ALL shots: block the X-rays first, e.g. close the shutter"), flush=True)
    while n < args.shots:
        now = time.time()
        if now >= next_take:
            tag = int(src.newest_tag())
            if tag != last:
                last = tag
                img = src.read(tag)
                if img is not None:
                    if len(pending) >= PENDING:
                        pending.popleft()
                        dropped += 1
                    pending.append((tag, now, img.astype(np.float32)))
                next_take = now + every
        while pending and not only_off:     # all shots: no need to wait for the flags
            tag, t_read, img = pending.popleft()
            total = img.astype(np.float64) if total is None else total + img
            n += 1
            if n % 50 == 0:
                print("  {} / {} shots ({:.0f} s)".format(n, args.shots, time.time() - t0), flush=True)
        while pending:
            tag, t_read, img = pending[0]
            flags = src.flags(tag)
            known = flags[0] == flags[0] and flags[1] == flags[1]
            if not known and time.time() - t_read < KEEP_S:
                break                       # its flags are not there yet (the older ones neither)
            pending.popleft()
            if not known:
                dropped += 1
                continue
            cls = classify_one(flags)
            seen[cls] += 1
            if cls == timetool.BACKGROUND:
                total = img.astype(np.float64) if total is None else total + img
                n += 1
                if n % 50 == 0:
                    print("  {} / {} background shots ({:.0f} s)".format(n, args.shots, time.time() - t0), flush=True)
        if only_off and time.time() - last_report > 20.0:
            last_report = time.time()
            print("  {} / {} background shots so far; shots seen: {}; dropped (no flags) {}".format(
                n, args.shots, ", ".join("{} {}".format(timetool.CLASS_NAMES[c], k) for c, k in seen.items()),
                dropped), flush=True)
            if n == 0:
                print("  (no background shot yet: close the X-ray shutter with the laser on)", flush=True)
        time.sleep(0.002)
    assert total is not None
    path = timetool.save_background(cfg, total / n, n, src.det_id, "live" + ("_TEST" if args.test else ""),
                                    {"last_tag": last, "test": bool(args.test), "seen": seen,
                                     "shots": "background" if only_off else "all"})
    print("background of {} shots saved: {}\nset as the time-tool background".format(n, path))
    return path


@sacla.requires_sacla
def from_run(args: Any) -> Optional[str]:
    return timetool.background_from_run(Config(), args.run, workers=args.workers, max_shots=args.max_shots,
                                        shots="background" if args.only_xray_off else "all")


@sacla.requires_sacla
def capture_live(args: Any) -> Optional[str]:
    return capture(args)


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="time-tool background: mean of laser-on, X-ray-off shots")
    ap.add_argument("--run", type=int, help="a STORED run (HPC): average its background shots")
    ap.add_argument("--workers", type=int, default=6, help="stored run: processes (default 6)")
    ap.add_argument("--max-shots", type=int, help="stored run: at most this many background shots")
    ap.add_argument("--only-xray-off", action="store_true",
                    help="only shots with the X-ray shutter closed and the laser on (default: all shots)")
    ap.add_argument("--shots", type=int, default=300, help="live: background shots to average (default 300)")
    ap.add_argument("--every", type=float, default=0.1, help="live: seconds between shots taken (default 0.1)")
    ap.add_argument("--test", action="store_true", help="live: the test camera (always --only-xray-off)")
    ap.add_argument("--camera", help="camera detector ID (default: the config's, else the first OPAL / IPX / CHET)")
    args = ap.parse_args(argv)
    try:
        if args.run is not None:
            from_run(args)
        elif args.test:
            capture(args)
        else:
            capture_live(args)
    except KeyboardInterrupt:
        print("stopped (Ctrl-C): no background saved")


if __name__ == "__main__":
    main()
