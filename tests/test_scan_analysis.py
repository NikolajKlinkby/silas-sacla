#!/usr/bin/env python3
"""Offline scan analysis against FAKE dbpy / stpy (no SACLA library is used).

    python tests/test_scan_analysis.py     # needs output/diag/mpccd_panels.npz

A logged 6-step scan whose shots come from the simulated scans of the online test feed
(photons, pump every 4th shot off, X-rays off every 50th, signal that drops with the step):
1. the shot table: every shot once, flags, ROI values = brute force (all panels corrected)
2. quantiles per step and pump state = numpy quantiles of the brute-force values
3. splitting the shots over 3 processes gives the same table
4. the whole analysis: files written (shots.csv, steps.csv, info.json, plot.png), ratio drops
All in a temporary folder; your config is untouched.
"""
import json
import os
import sys
import tempfile
import time
import types
from typing import Any, Dict, List

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp(prefix="silas_offline_")
os.environ["SILAS_PROFILE"] = "local"
os.environ["SILAS_CONFIG"] = os.path.join(tmp, "test.json")

from silas.core import mpccd, roi, runlog, scan, scanroi  # noqa: E402
from silas.core.config import Config  # noqa: E402
from silas.core.corrections import save_dark  # noqa: E402
from silas.online import scan_feed  # noqa: E402

from silas.core import standard  # noqa: E402

cfg = Config()
standard.add_standard_detectors(cfg, beamline=3, quiet=True)   # new configs start empty
cfg.set("paths.output", tmp)
cfg.set("photon_energy_keV", 9.9)
key = mpccd.add_mpccd(cfg, "MPCCD-8-2-001", alias="sim_octal")
roi.add_roi(cfg, "spot", (-797, -757), (-577, -517), name="sim_octal")
roi.add_roi(cfg, "wide", (216, 616), (189, 589), name="sim_octal")
mpccd.set_option(cfg, "corrections.threshold", True, key)
mpccd.set_option(cfg, "corrections.threshold_keV", 2.0, key)

# the data: the online test feed, clock at the end of its first scan, nothing lost
feed = scan_feed.TestFeed(cfg, n_steps=6, run_len=24, gap=4, buffer=10 ** 9)
feed.now_tag = feed._layout(feed.per_scan)[0] - 1
save_dark(os.path.join(tmp, "dark.npz"), feed.det_ids, feed.src.pedestal, 1)
mpccd.set_option(cfg, "corrections.dark_file", os.path.join(tmp, "dark.npz"), key)
mpccd.set_option(cfg, "corrections.dark", True, key)
RUNS = {feed.run0 + s: feed._layout(s) for s in range(feed.n_steps)}      # run -> (first, last tag)
STEP = {feed.run0 + s: s + 1 for s in range(feed.n_steps)}
TAGHI = 202610


# ---------------------------------------------------------------- fake dbpy / stpy
class APIError(Exception):
    pass


def _tags(run: int) -> List[int]:
    first, last = RUNS[run]
    return list(range(first, last + 1, feed.tag_step))


def _sync(equip: str, taghi: int, tags: Any) -> Any:
    assert isinstance(tags, tuple), "dbpy wants a tuple"
    i = np.asarray(tags) // feed.tag_step
    if equip.startswith("xfel_bl_3_shutter_1"):            # laser_shutter (X-rays)
        return tuple(np.where(i % 50 == 7, 0.0, 1.0))
    if equip.startswith("xfel_bl_3_lh1_shutter_1"):        # optical_shutter (pump), 1 = on
        return tuple(np.where(i % 4 == 0, 0.0, 1.0))
    if "pd_user" in equip:                                 # i0
        return tuple(1.0 + 0.01 * (i % 7))
    raise APIError(-200, "DA_ERR_NODATA")


fdb = types.ModuleType("dbpy")  # type: Any
fdb.APIError = APIError
fdb.read_taglist_byrun = lambda bl, run: tuple(_tags(run))
fdb.read_hightagnumber = lambda bl, run: TAGHI
fdb.read_syncdatalist_float = _sync
fdb.read_beamstatuslist = lambda bl, hi, tags: tuple(1 for _ in tags)
fdb.read_config_photonenergy_in_keV = lambda bl, run: 9.9
sys.modules["dbpy"] = fdb

INFOS = []  # type: List[Dict[str, Any]]
for i in feed.infos():
    d = dict(i)
    d["mp_pixelsizex"] = d.pop("mp_pixelsize_x", 50.0)  # stpy key names
    d["mp_pixelsizey"] = d.pop("mp_pixelsize_y", 50.0)
    d["mp_manipulator_shift"] = 0.0
    INFOS.append(d)
READS = []  # type: List[Any]


class StorageReader(object):
    def __init__(self, det_id: str, bl: int, runs: Any) -> None:
        assert isinstance(runs, tuple), "stpy wants a tuple of runs"
        self.k = feed.det_ids.index(det_id)
        self.run = runs[0]

    def collect(self, buf: Any, tag: int) -> None:
        if tag not in _tags(self.run):
            raise APIError(-1, "no data")
        buf.tag, buf.k = tag, self.k
        READS.append((self.k, tag))


class StorageBuffer(object):
    def __init__(self, reader: Any) -> None:
        self.tag, self.k = 0, 0

    def read_det_data(self, i: int) -> Any:
        return feed.read(self.tag, [self.k])[self.k]

    def read_det_info(self, i: int) -> Dict[str, Any]:
        return dict(INFOS[self.k])


fst = types.ModuleType("stpy")  # type: Any
fst.APIError = APIError
fst.StorageReader = StorageReader
fst.StorageBuffer = StorageBuffer
sys.modules["stpy"] = fst

from silas.offline import scan_analysis  # noqa: E402

# ---------------------------------------------------------------- the run log of this scan
import pandas as pd  # noqa: E402

log = runlog.empty()
for run, (first, last) in RUNS.items():
    row = {c: "" for c in runlog.COLUMNS[1:]}  # type: Dict[str, Any]
    row.update(status="stopped", runstatus=0, step=STEP[run], total_steps=feed.n_steps, scan_id=feed.run0,
               scan_value=1100.0 + 34.0 * (STEP[run] - 1), scan_motor="xfel_bl_3_st_2_motor_1/position",
               start_tagnumber=first, end_tagnumber=last, hightagnumber=TAGHI, total_tagnumber=len(_tags(run)))
    log.loc[run] = [row[c] for c in runlog.COLUMNS[1:]]
runlog.save(log, runlog.runlog_path(cfg))
scans = pd.DataFrame([{"scan_id": feed.run0, "first_run": min(RUNS), "last_run": max(RUNS), "n_runs": len(RUNS),
                       "total_steps": feed.n_steps, "steps_done": feed.n_steps, "status": "complete",
                       "scan_motor": "xfel_bl_3_st_2_motor_1/position", "scan_motor_alias": "delay_m"}],
                     columns=scan.SCAN_COLUMNS).set_index("scan_id")
runlog.save(scans, scan.scans_path(cfg))
_, settings = mpccd.get_array(cfg, "sim_octal")

# ---------------------------------------------------------------- 1. shot table
print("--- 1. shot table")
job = scan_analysis.prepare(cfg, settings, -1, None)
assert job["scan"]["scan_id"] == feed.run0 and len(job["scan"]["runs"]) == feed.n_steps
del READS[:]          # prepare() reads one shot of every panel for the geometry
rows = scan_analysis.process(cfg, settings, job["items"], job["run_info"], job["infos"])
shots = pd.DataFrame(rows)
n_all = sum(len(_tags(r)) for r in RUNS)
assert len(shots) == n_all and not shots.duplicated(["run", "tag"]).any()
i = shots["tag"] // feed.tag_step
assert (shots["use"] == (i % 50 != 7)).all()
assert (shots["state"] == np.where(i % 4 == 0, 1, 0)).all()
assert (shots["read"] == shots["use"]).all()
rmap, kev, corr = roi.analysis_setup(feed.det_ids, INFOS, settings, shift_from_data=True)
assert rmap is not None
npx = np.asarray(rmap.n_pixels, np.float64)
brute = {}  # type: Dict[Any, np.ndarray]
for _, r in shots[shots["read"]].iterrows():
    data = feed.read(int(r["tag"]), list(range(len(feed.det_ids))))
    x = corr.apply([data[k] for k in range(len(feed.det_ids))])
    brute[(int(r["run"]), int(r["tag"]))] = np.array(
        [sum(float(x[k].ravel()[idx].sum()) * float(kev[k]) for k, idx in parts) for parts in rmap.parts]) / npx
got = shots[shots["read"]].set_index(["run", "tag"])[["roi_spot", "roi_wide"]]
for k, v in brute.items():
    assert np.allclose(got.loc[k].to_numpy(dtype=float), v, rtol=1e-6), (k, got.loc[k], v)
read_panels = sorted(set(k for k, _ in READS))
assert read_panels == rmap.panels, (read_panels, rmap.panels)
print("  ok: {} shots once each, flags right, ROI values == brute force, only panels {} read".format(
    len(shots), [k + 1 for k in read_panels]))

# ---------------------------------------------------------------- 2. quantiles
print("--- 2. quantiles per step and pump state")
table, steps = scan_analysis.summarise(shots, rmap.names, job["scan"]["runs"], 2)
for st in table:
    run = st["runs"][0]
    for s in (0, 1):
        vals = np.array([v for (r, t), v in brute.items()
                         if r == run and shots.set_index(["run", "tag"]).loc[(r, t), "state"] == s])
        exp = np.quantile(vals, scanroi.QUANTILES, axis=0)
        assert np.allclose(st["q"][s], exp), (run, s)
        assert st["n"][s] == len(vals)
print("  ok: median and 15.87 / 84.13 % quantiles == numpy on the brute-force values, every step and state")

# ---------------------------------------------------------------- 3. split over processes
print("--- 3. 3 processes give the same shot table")
parts = [scan_analysis.process(cfg, settings, job["items"][r::3], job["run_info"], job["infos"]) for r in range(3)]
split = pd.DataFrame([row for p in parts for row in p]).sort_values(["run", "tag"]).reset_index(drop=True)
whole = shots.sort_values(["run", "tag"]).reset_index(drop=True)
pd.testing.assert_frame_equal(split, whole)
print("  ok")

# ---------------------------------------------------------------- 4. whole analysis


class Args:
    scan, array, max_tags, show = -1, "sim_octal", None, False


out = scan_analysis.analyse(Args())
assert out is not None
for f in ("shots.csv", "steps.csv", "info.json", "plot.png"):
    assert os.path.exists(os.path.join(out, f)), f
info = json.load(open(os.path.join(out, "info.json")))
st = pd.read_csv(os.path.join(out, "steps.csv"))
print("--- 4. files in {}".format(out))
print("  step  delay   wide on/off   [16 %, 84 %]   expected")
for _, r in st.iterrows():
    print("  {:4.0f}  {:5.0f}   {:6.3f}       [{:5.3f}, {:5.3f}]   {:5.3f}".format(
        r["step"], r["x"], r["wide_ratio"], r["wide_ratio_lo"], r["wide_ratio_hi"], feed.factor(int(r["step"]), True)))
assert st["wide_ratio"].iloc[-1] < st["wide_ratio"].iloc[0] - 0.15
assert info["shots"]["used"] == int(shots["read"].sum()) and info["rois"]["wide"]["pixels"] == rmap.n_pixels[1]
print("  ok: shots {}".format(info["shots"]))

# ---------------------------------------------------------------- 5. viewer replay of a stored run
print("--- 5. the MPCCD viewer's replay of a stored run (stpy)")
from silas.online.mpccd_source import open_source  # noqa: E402

run0 = min(RUNS)
src = open_source(feed.det_ids, False, run=run0, bl=3, rate_hz=1000.0)
infos = src.infos()
assert len(infos) == len(feed.det_ids) and "mp_pixelsizex" in infos[0]
t_a = src.newest_tag()
time.sleep(0.01)
t_b = src.newest_tag()
assert src.real_tag(t_a) in _tags(run0) and t_b >= t_a
imgs = src.read(_tags(run0)[3])
assert imgs is not None and len(imgs) == len(feed.det_ids) and imgs[0].shape == feed.src.base[0].shape
time.sleep(0.2)
t_c = src.newest_tag()
assert t_c > _tags(run0)[-1] and src.real_tag(t_c) in _tags(run0), "loops: tags count on past the run"
assert src.read(t_c) is not None
print("  ok: replays the run's shots in order, in a loop (tags count on)")
print("\nOK  (files in {})".format(tmp))
