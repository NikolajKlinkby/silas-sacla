#!/usr/bin/env python3
"""Smoke test of the olpy online simulator (runs locally and on the HPC).

    source sacla_env.sh
    sacla_check examples/olpy_sim_smoke.py      # static check against the stubs
    python examples/olpy_sim_smoke.py           # real run against $OL_SIMPATH

Reads a few tags from every MPCCD detector in the simulator and prints the
image shape, value range and the MPCCD geometry of panel 0.
Kept Python 3.7 compatible (HPC module python/SACLA_python-3.7).
"""
import sys
from typing import List

import numpy as np
import olpy

N_TAGS = 3


def read_detector(det_id: str, n_tags: int = N_TAGS) -> List[int]:
    reader = olpy.StorageReader(det_id)
    buf = olpy.StorageBuffer(reader)
    tags: List[int] = []
    for _ in range(n_tags):
        tag = reader.collect(buf, olpy.NEWESTTAG)
        tags.append(tag)
        for idx in range(buf.read_det_num_index()):
            img = buf.read_det_data(idx)
            info = buf.read_det_info(idx)
            print("  {:<18s} tag={:<10d} idx={} shape={} min={:9.2f} max={:9.2f} mean={:9.3f}".format(
                det_id, tag, idx, img.shape, float(np.min(img)), float(np.max(img)), float(np.mean(img))))
            if idx == 0 and "mp_pixelsize_x" in info:
                print("  {:<18s} pixel {:.1f} x {:.1f} um, pos=({:.1f}, {:.1f}, {:.1f}), rot={:.2f} deg".format(
                    "", info["mp_pixelsize_x"], info.get("mp_pixelsize_y", float("nan")),
                    info.get("mp_posx", float("nan")), info.get("mp_posy", float("nan")),
                    info.get("mp_posz", float("nan")), info.get("mp_rotationangle", float("nan"))))
    return tags


def main() -> int:
    print("olpy API version:", olpy.get_api_version())
    det_ids = [d for d in olpy.read_detidlist() if d.startswith("MPCCD")]
    print("MPCCD detectors in the simulator:", ", ".join(det_ids))
    failed = 0
    for det_id in det_ids:
        try:
            read_detector(det_id)
        except olpy.APIError as err:
            code, name = err.args
            print("  {:<18s} FAILED: {} ({})".format(det_id, name, code))
            failed += 1
    print("done: {} detectors, {} failed".format(len(det_ids), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
