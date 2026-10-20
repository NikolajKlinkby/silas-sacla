#!/usr/bin/env python3
"""Cross-check silas.core.geometry against SACLA's ippy.reconstruction.

    source sacla_local_api/sacla_env.sh
    python tests/check_geometry_ippy.py      # needs output/diag/mpccd_panels.npz
                                             # (from tests/diag_mpccd_geometry.py)

Tries the rounding rules of the pixel map (which neighbouring panel pixel an image
pixel takes) and prints how many pixels agree with ippy for each, then writes
output/diag/geometry_check.png with the best one: our assembly with panel outlines
and real-space axes (mm), next to ippy's.
"""
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from silas.core.geometry import Geometry  # noqa: E402  (numpy only, no bootstrap side effects needed)

DIAG = os.path.join(ROOT, "output", "diag")


def main() -> None:
    import ippy

    d = np.load(os.path.join(DIAG, "mpccd_panels.npz"))
    images = list(d["images"])
    infos = json.loads(str(d["info_json"]))
    det_ids = [str(x) for x in d["det_ids"]]

    geom = Geometry.from_infos(det_ids, infos)
    print(geom)
    srcs = geom.ippy_sources(images)
    t = time.time()
    theirs = ippy.reconstruction(srcs, geom.shape[1], geom.shape[0], int(round(geom.pixel_um)), 0)
    print("ippy.reconstruction: {:.1f} ms, succeeded {}".format(
        1e3 * (time.time() - t), [s.get("succeeded") for s in srcs]))

    # which rounding rule does ippy use? (only decides which neighbouring panel pixel is taken)
    print("\nrounding  sub_pixel(row, col)   equal values   only ours  only ippy")
    results = []
    for rounding in ("trunc", "floor", "rint"):
        for sr in (-0.5, 0.0, 1e-6, 0.5):
            for sc in (-0.5, 0.0, 1e-6, 0.5):
                g = Geometry.from_infos(det_ids, infos, rounding=rounding, sub_pixel=(sr, sc))
                ours = g.assemble(images)
                both = np.isfinite(ours) & (theirs != 0)
                eq = float(np.isclose(ours[both], theirs[both], rtol=1e-4, atol=1e-3).mean())
                only_o = int((np.isfinite(ours) & (theirs == 0)).sum())
                only_i = int((~np.isfinite(ours) & (theirs != 0)).sum())
                results.append((eq, rounding, (sr, sc), only_o, only_i))
                print("{:8s}  ({:+.6g}, {:+.6g})      {:7.2f} %   {:9d}  {:9d}".format(
                    rounding, sr, sc, 100 * eq, only_o, only_i))
    best = max(results, key=lambda r: (r[0], -r[3] - r[4]))
    print("\nbest match with ippy: rounding={!r} sub_pixel={} -> {:.2f} % equal".format(best[1], best[2], 100 * best[0]))
    print('to use it: set_mpccd("geometry.rounding", {!r}); set_mpccd("geometry.sub_pixel", {})'.format(
        best[1], list(best[2])))
    geom = Geometry.from_infos(det_ids, infos, rounding=best[1], sub_pixel=best[2])
    _plot(geom, geom.assemble(images), theirs)


def _plot(geom: Geometry, ours: np.ndarray, theirs: np.ndarray) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    finite = ours[np.isfinite(ours)]
    lo, hi = np.percentile(finite, [2, 99.7])
    fig, axes = plt.subplots(1, 2, figsize=(18, 9))
    ext = geom.extent_mm()
    for ax, img, title in ((axes[0], ours, "silas.core.geometry"),
                           (axes[1], np.where(theirs == 0, np.nan, theirs), "ippy.reconstruction")):
        im = ax.imshow(img, extent=ext, vmin=lo, vmax=hi, cmap="viridis", interpolation="nearest")
        for k, poly in enumerate(geom.outlines_mm()):
            ax.plot(poly[:, 0], poly[:, 1], "w-", lw=0.6)
            ax.text(poly[0, 0], poly[0, 1], str(k + 1), color="w", fontsize=9)
        ax.set_title(title)
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
    fig.colorbar(im, ax=axes, shrink=0.8)
    path = os.path.join(DIAG, "geometry_check.png")
    fig.savefig(path, dpi=80)
    print("saved", path)


if __name__ == "__main__":
    main()
