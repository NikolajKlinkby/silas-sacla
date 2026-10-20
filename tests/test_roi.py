#!/usr/bin/env python3
"""ROI test with test data (no SACLA library, no matplotlib).

    python tests/test_roi.py     # needs output/diag/mpccd_panels.npz

1. ROIs in the config: add, replace, remove, bad input refused
2. RoiMap: which panels and panel pixels a ROI covers (one ROI inside a panel, one across
   a panel gap, one in the empty centre); checked against the assembled image
3. sums with dark + threshold on the ROI pixels only == full correction of all panels
4. speed: ROI pixels of the needed panels vs. correcting every panel of a shot
All in a temporary folder; your config is untouched.
"""
import os
import sys
import tempfile
import time
from typing import List, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp(prefix="silas_roi_")
os.environ["SILAS_PROFILE"] = "local"
os.environ["SILAS_CONFIG"] = os.path.join(tmp, "test.json")

from silas.core import energy, mpccd, roi  # noqa: E402
from silas.core.config import Config  # noqa: E402
from silas.core.corrections import Corrections  # noqa: E402
from silas.core.geometry import Geometry  # noqa: E402
from silas.online.mpccd_source import TestSource  # noqa: E402

cfg = Config()
cfg.set("paths.output", tmp)
key = mpccd.add_mpccd(cfg, "MPCCD-8-2-001", alias="sim_octal")
src = TestSource(photon_keV=9.9)
geom = Geometry.from_infos(src.det_ids, src.infos())
H, W = geom.shape
mask = geom.mask()
print("assembled image {} x {}, centre (row, col) = {}".format(H, W, geom.centre))

# ---------------------------------------------------------------- 1. config
print("--- 1. ROIs in the config")
# a ROI inside one panel: around the middle of panel 1's pixels in the image
d0 = geom._dst[0]
r_mid, c_mid = int(np.median(d0 // W)), int(np.median(d0 % W))
inside = ((r_mid - 20, r_mid + 20), (c_mid - 30, c_mid + 30))
# a ROI across a gap: a row through the image centre region where the mask changes
row = geom.centre[0] + 300
cols_on = np.nonzero(mask[row])[0]
jump = cols_on[np.argmax(np.diff(cols_on) > 1)]          # last column before a gap
across = ((row - 10, row + 10), (int(jump) - 25, int(jump) + 40))
centre = ((geom.centre[0] - 5, geom.centre[0] + 5), (geom.centre[1] - 5, geom.centre[1] + 5))


Rect = Tuple[Tuple[int, int], Tuple[int, int]]


def rel(rect: Rect) -> Rect:
    """image pixels -> pixels from the origin (what add_roi takes)"""
    (r0, r1), (c0, c1) = rect
    return (r0 - geom.centre[0], r1 - geom.centre[0]), (c0 - geom.centre[1], c1 - geom.centre[1])


for roi_name, rect in (("inside", inside), ("across", across), ("centre", centre)):
    rows, cols = rel(rect)
    roi.add_roi(cfg, roi_name, rows, cols, name="sim_octal")
roi.add_roi(cfg, "tmp", (5, 1), (1, 2), name="sim_octal")      # reversed bounds are sorted
assert roi.get_rois(cfg, "sim_octal")["tmp"] == {"rows": [1, 5], "cols": [1, 2]}
roi.remove_roi(cfg, "tmp", name="sim_octal")
for bad in [((1, 2, 3), (1, 2)), ((1,), (1, 2))]:
    try:
        roi.add_roi(cfg, "bad", *bad, name="sim_octal")
        raise AssertionError("bad ROI accepted: {}".format(bad))
    except ValueError:
        pass
for j in range(roi.MAX_ROIS - 3):
    roi.add_roi(cfg, "extra{}".format(j), (0, 1), (0, 1), name="sim_octal")
try:
    roi.add_roi(cfg, "one_too_many", (0, 1), (0, 1), name="sim_octal")
    raise AssertionError("more than {} ROIs accepted".format(roi.MAX_ROIS))
except ValueError:
    pass
for j in range(roi.MAX_ROIS - 3):
    roi.remove_roi(cfg, "extra{}".format(j), name="sim_octal")
rois = roi.get_rois(cfg, "sim_octal")
assert list(rois) == ["inside", "across", "centre"]
print("  ok: add / replace / remove, bad input and an 11th ROI refused; stored:", list(rois))

# ---------------------------------------------------------------- 2. RoiMap
print("--- 2. panels and pixels of every ROI")
rmap = roi.RoiMap(geom, rois)
print(roi.describe(rois, rmap))
# every panel pixel gets a unique id; the ids seen in the ROI rectangle of the assembled
# image must be exactly the panel pixels of the RoiMap
ids = [np.arange(p.size, dtype=np.float64) + k * 1e6 for k, p in enumerate(src.base)]
img = np.full(geom.shape, -1.0)
flat = img.reshape(-1)
for k in range(len(ids)):
    flat[geom._dst[k]] = ids[k][geom._src[k]]
for i, name in enumerate(rmap.names):
    (r0, r1), (c0, c1) = [[v + geom.centre[0] for v in rmap.rects[i]["rows"]],
                          [v + geom.centre[1] for v in rmap.rects[i]["cols"]]]
    seen = set(np.unique(img[r0:r1 + 1, c0:c1 + 1]).tolist()) - {-1.0}
    mine = set(float(k * 1e6 + j) for k, idx in rmap.parts[i] for j in idx)
    assert seen == mine, name
assert len(rmap.parts[0]) == 1, "inside: one panel"
assert len(rmap.parts[1]) == 2, "across: two panels"
assert rmap.n_pixels[2] == 0 and not rmap.parts[2], "centre: no pixels"
# relative to the origin: the same panels in a bigger image (other size and centre) give the
# same panel pixels for every ROI
bigger = Geometry([p for p in geom.panels], geom.manipulator_shift_um, margin=60, rounding=geom.rounding)
assert bigger.shape != geom.shape and bigger.centre != geom.centre
rmap2 = roi.RoiMap(bigger, rois)
for a, b in zip(rmap.parts, rmap2.parts):
    assert [k for k, _ in a] == [k for k, _ in b] and all(np.array_equal(x, y) for (_, x), (_, y) in zip(a, b))
print("  ok: same pixels in an image of another size ({} vs {}): ROIs are tied to the detector".format(
    bigger.shape, geom.shape))
print("  ok: same pixels as in the assembled image; panels to read: {} of {}".format(
    [k + 1 for k in rmap.panels], len(src.det_ids)))

# ---------------------------------------------------------------- 3. sums
print("--- 3. ROI sums with dark and threshold == full correction")
cfg.set("photon_energy_keV", 9.9)
dark = (src.pedestal + 0.0).astype(np.float32)                      # a perfect dark
kev = energy.kev_per_adu(energy.absgains(src.infos()))
thr = energy.threshold_adu(2.0, kev)
corr = Corrections({"dark": False, "threshold": True, "threshold_keV": 2.0}, kev, src.det_ids)
corr.dark = dark
shots = []  # type: List[List[np.ndarray]]
for i in range(20):
    got_shot = src.read(src.tag0 + i)
    if got_shot is not None:
        shots.append(got_shot)
if not shots:
    shots.append(list(src.pedestal + src.photons[0]))
for shot in shots[:5]:
    full = corr.apply(shot)                                          # every pixel of every panel
    ref = np.array([sum(float(full[k].ravel()[idx].sum()) * float(kev[k]) for k, idx in parts)
                    for parts in rmap.parts])
    got = rmap.sums({k: shot[k] for k in rmap.panels}, kev, dark, thr)
    assert np.allclose(got, ref, rtol=1e-6, atol=1e-6), (got, ref)
means = rmap.means(got)
print("  ok on {} shots; last shot keV: {}   keV/pixel: {}".format(
    min(5, len(shots)), np.round(got, 2).tolist(), np.round(means, 4).tolist()))

# ---------------------------------------------------------------- 4. speed
print("--- 4. speed per shot")
shot = shots[0]
n = 30
t0 = time.time()
for _ in range(n):
    corr.apply(shot)
t_full = (time.time() - t0) / n
t0 = time.time()
for _ in range(n):
    rmap.sums({k: shot[k] for k in rmap.panels}, kev, dark, thr)
t_roi = (time.time() - t0) / n
print("  correct all {} panels: {:.1f} ms   ROI pixels only ({} pixels in {} panels): {:.2f} ms".format(
    len(src.det_ids), 1000 * t_full, sum(rmap.n_pixels), len(rmap.panels), 1000 * t_roi))
print("  (reading: only {} of {} panels would be read from the detector)".format(len(rmap.panels), len(src.det_ids)))
print("\nOK  (files in {})".format(tmp))
