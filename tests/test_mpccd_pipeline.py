#!/usr/bin/env python3
"""MPCCD pipeline test with test data (no SACLA library, no matplotlib).

    python tests/test_mpccd_pipeline.py     # needs output/diag/mpccd_panels.npz

config -> test source -> reader (dark, keV threshold) -> dark capture -> keV
calibration (photon peak at the configured photon energy) -> assembly, all in a
temporary folder; your config is untouched.
"""
import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp(prefix="silas_mpccd_")
os.environ["SILAS_PROFILE"] = "local"
os.environ["SILAS_CONFIG"] = os.path.join(tmp, "test.json")

from silas.core import energy, mpccd  # noqa: E402
from silas.core.config import Config  # noqa: E402
from silas.core.geometry import Geometry  # noqa: E402
from silas.online import dark, mpccd_view  # noqa: E402
from silas.online.mpccd_source import TestSource  # noqa: E402

from silas.core import standard  # noqa: E402

cfg = Config()
standard.add_standard_detectors(cfg, beamline=3, quiet=True)   # new configs start empty
cfg.set("paths.output", tmp)
print("--- standard octal is in a new config:", mpccd.get_array(cfg, "octal")[0])
try:
    mpccd.add_mpccd(cfg, "MPCCD-8-2-001")  # alias "octal" is taken by the standard octal
    raise AssertionError("alias clash not detected")
except ValueError as err:
    print("  refused as expected:", err)
assert "MPCCD-8-2-001" not in cfg.get("detectors")
key = mpccd.add_mpccd(cfg, "MPCCD-8-2-001", alias="sim_octal")
print("--- array", key, "panels", cfg.get("detectors")[key]["panels"][:2], "...")
assert mpccd.get_array(cfg, "sim_octal")[0] == key

assert cfg.get("photon_energy_keV", "missing") is None, "new config: photon energy from the run record"
cfg.set("photon_energy_keV", 9.9)
print("--- reader, 2 s of test data (photons of 9.9 keV), no corrections")
settings = mpccd_view.Settings("sim_octal")
src = TestSource(photon_keV=9.9)
reader = mpccd_view.Reader(src, settings)
t_end = time.time() + 2.0
while time.time() < t_end:
    if not reader.step():
        time.sleep(0.003)
msg = reader.take()
print("  shots read: {}  missed: {}  latest tag: {}".format(msg["n"], msg["missed"], msg["latest_tag"]))
assert msg["n"] > 5 and msg["sum"].shape == (8, 1024, 512)
mean_raw = msg["sum"] / msg["n"]
print("  mean of panel 1: {:.1f} ADU (pedestal ~100 + signal)".format(float(mean_raw[0].mean())))

print("--- dark capture (test data: X-rays off)")

class A:
    shots, array, test, test_data = 30, "sim_octal", True, None

path = dark.capture(A())
settings.refresh(force=True)
assert settings.s["corrections"]["dark_file"] == path and settings.s["corrections"]["dark"]

print("--- with dark + threshold the pedestal is gone")
mpccd.set_option(cfg, "corrections.threshold", True, "sim_octal")
mpccd.set_option(cfg, "corrections.threshold_keV", 2.0, "sim_octal")
settings.refresh(force=True)
t_end = time.time() + 1.5
while time.time() < t_end:
    if not reader.step():
        time.sleep(0.003)
msg = reader.take()
corr = msg["sum"] / msg["n"]
frac0 = float((msg["latest"] == 0).mean())
print("  shots {}  mean panel 1 {:.1f} ADU  pixels zeroed by threshold in last shot {:.0%}".format(
    msg["n"], float(corr[0].mean()), frac0))
assert corr[0].mean() < mean_raw[0].mean() - 50 and frac0 > 0.3
kev = mpccd_view.kev_per_adu(settings, src.infos())
thr = energy.threshold_adu(2.0, kev).ravel()
last = msg["latest"]
for k in range(8):
    nz = last[k][last[k] != 0]
    assert nz.min() >= thr[k] - 1e-3, "panel {}: value below its threshold".format(k + 1)
print("  threshold 2 keV = {} ADU per panel; nothing below it survives".format(
    ", ".join("{:.1f}".format(t) for t in thr)))

print("--- keV calibration: the single-photon peak of every panel is at 9.9 keV")
for k in range(8):
    e_pix = last[k][last[k] != 0] * kev[k]
    h, edges = np.histogram(e_pix, bins=np.arange(2.0, 30.0, 0.2))
    peak = 0.5 * (edges[np.argmax(h)] + edges[np.argmax(h) + 1])
    assert abs(peak - 9.9) < 0.6, "panel {}: peak at {:.2f} keV".format(k + 1, peak)
print("  ok (panel 1: {:.4f} keV/ADU, 1 photon = {:.0f} ADU)".format(float(kev[0]), 9.9 / float(kev[0])))
print(energy.describe(cfg, settings.s, None, src.infos()))

print("--- old settings are removed from the file")
dets = cfg.get("detectors")
dets[key]["corrections"].update(gain=True, threshold_adu=50.0)
cfg.set("detectors", dets)
_, s2 = mpccd.get_array(cfg, key)
assert "threshold_adu" not in cfg.get("detectors")[key]["corrections"] and "gain" not in s2["corrections"]

print("--- assembly with geometry tuning from the config")
geom = Geometry.from_infos(src.det_ids, src.infos())
img = geom.assemble(list(corr), gain=False, scale=kev)
mpccd.set_option(cfg, "geometry.panel_offsets", {"3": {"dx_um": 500.0, "dy_um": 0.0, "drot_deg": 0.0}},
                 "sim_octal")
settings.refresh(force=True)
tuned = geom.tweaked(panel_offsets=settings.s["geometry"]["panel_offsets"])
moved = tuned.outlines_mm()[2][0, 0] - geom.outlines_mm()[2][0, 0]
print("  image {}  filled {}  panel 3 moved {:.2f} mm".format(img.shape, int(np.isfinite(img).sum()), moved))
assert abs(moved - 0.5) < 1e-6
print("\nOK  (files in {})".format(tmp))
