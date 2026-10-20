"""Capture a dark (mean of shots without X-rays) for an MPCCD array, online.

    capture_dark(shots=300)                         # from the session
    python -m silas.online.dark --shots 300 [--test]

Close the shutter (or wait for a run without beam) first: every shot read is
averaged. The dark is saved to <output>/darks/ and set as the array's dark_file,
with dark subtraction switched on; a running viewer picks it up by itself.
"""
import argparse
import datetime
import os
import time
from typing import Any, List, Optional

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import mpccd, sacla
from silas.core.config import Config
from silas.core.corrections import save_dark
from silas.online.mpccd_source import open_source


def capture(args: Any) -> str:
    cfg = Config()
    key, settings = mpccd.get_array(cfg, args.array)
    src = open_source(settings["panels"], args.test, args.test_data, int(settings.get("tag_step", 1)))
    if args.test:
        src.xrays_on = False  # test data: pedestal + noise only
    total = None  # type: Optional[np.ndarray]
    n, last, t0 = 0, None, time.time()
    print("capturing a dark of {} shots for {} ({} panels)...".format(args.shots, key, len(src.det_ids)))
    while n < args.shots:
        tag = src.newest_tag()
        if tag == last:
            time.sleep(0.005)
            continue
        last = tag
        images = src.read(tag)
        if images is None:
            continue
        x = np.array(images, dtype=np.float64)
        total = x if total is None else total + x
        n += 1
        if n % 50 == 0:
            print("  {} / {} shots ({:.1f} s)".format(n, args.shots, time.time() - t0))
    assert total is not None
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(cfg.output_dir(), "darks", "dark_{}_{}{}.npz".format(key, stamp, "_TEST" if args.test else ""))
    save_dark(path, src.det_ids, total / n, n, {"array": key, "last_tag": last, "test": bool(args.test)})
    mpccd.set_option(cfg, "corrections.dark_file", path, key)
    mpccd.set_option(cfg, "corrections.dark", True, key)
    print("dark saved: {}\nset as dark_file of {} (dark subtraction on)".format(path, key))
    return path


@sacla.requires_sacla
def capture_live(args: Any) -> None:
    capture(args)


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="capture an MPCCD dark online")
    ap.add_argument("--shots", type=int, default=300, help="number of shots to average (default 300)")
    ap.add_argument("--array", help="array name or alias (default: the first MPCCD array)")
    ap.add_argument("--test", action="store_true", help="use test data instead of the detector")
    ap.add_argument("--test-data", help="test data file (default: output/diag/mpccd_panels.npz)")
    args = ap.parse_args(argv)
    if args.test:
        capture(args)
    else:
        capture_live(args)


if __name__ == "__main__":
    main()
