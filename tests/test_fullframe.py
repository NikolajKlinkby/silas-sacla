#!/usr/bin/env python3
"""Full-frame reduction against FAKE dbpy / stpy (no SACLA library is used).

    python tests/test_fullframe.py     # needs output/diag/mpccd_panels.npz (as test_scan_analysis.py)

The logged 6-step scan of test_scan_analysis.py (photons, pump every 4th shot off, X-rays off
every 50th, i0 per shot), reduced for panels chosen so that the ROI "wide" is inside them:
1. only the chosen panels are read; the file has the expected datasets
2. every pixel of every step and pump state, raw and per i0: the 15.87 / 50 / 84.13 % quantiles
   and the mean == numpy on the brute-force corrected images (all panels corrected, then cut)
3. the mean image averaged over the ROI's pixels == the ROI analysis' mean of the ROI values
4. pairs (on / off and on - off) and step_reference == brute force; FullFrame.onoff ==
   ratio_of_medians and FullFrame.difference == on - off of the brute force
5. time-tool bins (fake time-tool log): raw, per i0 and step-wise on / off and on - off == brute force;
   sparse bins have no images; the point detectors per shot and per step == the fake values / numpy
6. the statistics in real worker processes (multiprocessing) give the same file
Without h5py (e.g. a bare container) a small in-memory stand-in is used; with h5py the real one.
All in a temporary folder; your config is untouched.
"""
import json
import os
import pickle
import sys
import tempfile
import types
from typing import Any, Dict, List, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp(prefix="silas_fullframe_")
os.environ["SILAS_PROFILE"] = "local"
os.environ["SILAS_CONFIG"] = os.path.join(tmp, "test.json")

try:
    import h5py  # noqa: F401
    REAL_H5 = True
except ImportError:
    REAL_H5 = False

    class _Attrs(dict):
        pass

    class _Dataset(object):
        def __init__(self, data: np.ndarray) -> None:
            self.data = data
            self.attrs = _Attrs()

        def __getitem__(self, key: Any) -> Any:
            return self.data[key] if key != () else self.data

        def __setitem__(self, key: Any, value: Any) -> None:
            self.data[key] = value

        ndim = property(lambda self: self.data.ndim)
        shape = property(lambda self: self.data.shape)
        dtype = property(lambda self: self.data.dtype)

    class _Group(object):
        def __init__(self) -> None:
            self.items = {}  # type: Dict[str, Any]
            self.attrs = _Attrs()

        def _walk(self, name: str, create: bool) -> Any:
            node = self
            parts = name.strip("/").split("/")
            for p in parts[:-1]:
                if p not in node.items:
                    if not create:
                        raise KeyError(name)
                    node.items[p] = _Group()
                node = node.items[p]
            return node, parts[-1]

        def create_group(self, name: str) -> "_Group":
            node, last = self._walk(name, True)
            node.items[last] = _Group()
            return node.items[last]

        def create_dataset(self, name: str, shape: Any = None, dtype: Any = None, data: Any = None,
                           fillvalue: Any = None, **kw: Any) -> _Dataset:
            node, last = self._walk(name, True)
            if data is None:
                arr = np.full(shape, np.nan if fillvalue is None else fillvalue, dtype=np.dtype(dtype))
            else:
                arr = np.array(data)
            node.items[last] = _Dataset(arr)
            return node.items[last]

        def __getitem__(self, name: str) -> Any:
            node, last = self._walk(name, False)
            return node.items[last]

        def __contains__(self, name: str) -> bool:
            try:
                self[name]
                return True
            except KeyError:
                return False

        def __iter__(self) -> Any:
            return iter(self.items)

    class File(_Group):
        """Written as a folder (the path) of .npy files, read back memory-mapped."""

        def __init__(self, path: str, mode: str = "r") -> None:
            super(File, self).__init__()
            self.path, self.mode = path, mode
            if mode == "r":
                with open(os.path.join(path, "tree.pkl"), "rb") as fh:
                    tree, self.attrs = pickle.load(fh)
                self.items = self._load(tree, path)

        def _load(self, tree: Any, path: str) -> Dict[str, Any]:
            out = {}  # type: Dict[str, Any]
            for k, (kind, attrs, sub) in tree.items():
                if kind == "g":
                    g = _Group()
                    g.items, g.attrs = self._load(sub, path), attrs
                    out[k] = g
                else:
                    ds = _Dataset(np.load(os.path.join(path, sub), mmap_mode="r"))
                    ds.attrs = attrs
                    out[k] = ds
            return out

        def _save(self, items: Dict[str, Any], prefix: str) -> Dict[str, Any]:
            tree = {}  # type: Dict[str, Any]
            for k, v in items.items():
                if isinstance(v, _Group):
                    tree[k] = ("g", v.attrs, self._save(v.items, prefix + k + "."))
                else:
                    name = prefix + k + ".npy"
                    np.save(os.path.join(self.path, name), v.data)
                    tree[k] = ("d", v.attrs, name)
            return tree

        def close(self) -> None:
            if self.mode == "w":
                os.makedirs(self.path, exist_ok=True)
                tree = self._save(self.items, "")
                with open(os.path.join(self.path, "tree.pkl"), "wb") as fh:
                    pickle.dump((tree, self.attrs), fh)
                self.items = {}

        def __enter__(self) -> "File":
            return self

        def __exit__(self, *a: Any) -> None:
            self.close()

    fake_h5 = types.ModuleType("h5py")  # type: Any
    fake_h5.File = File
    sys.modules["h5py"] = fake_h5

from silas.core import mpccd, roi, runlog, scan, scanroi, timetool  # noqa: E402
from silas.core.config import Config  # noqa: E402
from silas.core.corrections import save_dark  # noqa: E402
from silas.online import scan_feed  # noqa: E402

from silas.core import standard  # noqa: E402

cfg = Config()
standard.add_standard_detectors(cfg, beamline=3, quiet=True)   # new configs start empty
cfg.set("paths.output", tmp)
cfg.set("photon_energy_keV", 9.9)
key = mpccd.add_mpccd(cfg, "MPCCD-8-2-001", alias="sim_octal")
roi.add_roi(cfg, "wide", (216, 616), (189, 589), name="sim_octal")
mpccd.set_option(cfg, "corrections.threshold", True, key)
mpccd.set_option(cfg, "corrections.threshold_keV", 2.0, key)
cfg.set("scan.references", ["i0_1"])

feed = scan_feed.TestFeed(cfg, n_steps=6, run_len=24, gap=4, buffer=10 ** 9)
feed.now_tag = feed._layout(feed.per_scan)[0] - 1
save_dark(os.path.join(tmp, "dark.npz"), feed.det_ids, feed.src.pedestal, 1)
mpccd.set_option(cfg, "corrections.dark_file", os.path.join(tmp, "dark.npz"), key)
mpccd.set_option(cfg, "corrections.dark", True, key)
RUNS = {feed.run0 + s: feed._layout(s) for s in range(feed.n_steps)}
STEP = {feed.run0 + s: s + 1 for s in range(feed.n_steps)}
TAGHI = 202610


# ---------------------------------------------------------------- fake dbpy / stpy (as test_scan_analysis)
class APIError(Exception):
    pass


def _tags(run: int) -> List[int]:
    first, last = RUNS[run]
    return list(range(first, last + 1, feed.tag_step))


def _sync(equip: str, taghi: int, tags: Any) -> Any:
    assert isinstance(tags, tuple), "dbpy wants a tuple"
    i = np.asarray(tags) // feed.tag_step
    if equip.startswith("xfel_bl_3_shutter_1"):
        return tuple(np.where(i % 50 == 7, 0.0, 1.0))
    if equip.startswith("xfel_bl_3_lh1_shutter_1"):
        return tuple(np.where(i % 4 == 0, 0.0, 1.0))
    if "pd_user_6" in equip:                               # i0_1, missing for every 9th shot
        return tuple(np.where(i % 9 == 4, np.nan, 1.0 + 0.01 * (i % 7)))
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
for inf in feed.infos():
    d = dict(inf)
    d["mp_pixelsizex"] = d.pop("mp_pixelsize_x", 50.0)
    d["mp_pixelsizey"] = d.pop("mp_pixelsize_y", 50.0)
    d["mp_manipulator_shift"] = 0.0
    INFOS.append(d)
READS = []  # type: List[Any]
UNREADABLE = {_tags(feed.run0 + 2)[5]}                     # one shot of step 3 has no data


class StorageReader(object):
    def __init__(self, det_id: str, bl: int, runs: Any) -> None:
        assert isinstance(runs, tuple)
        self.k = feed.det_ids.index(det_id)
        self.run = runs[0]

    def collect(self, buf: Any, tag: int) -> None:
        if tag not in _tags(self.run) or tag in UNREADABLE:
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

from silas.offline import fullframe, scan_analysis  # noqa: E402

# ---------------------------------------------------------------- run log and scan table
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
rmap, kev_all, corr_all = roi.analysis_setup(feed.det_ids, INFOS, settings, shift_from_data=True)
assert rmap is not None
SEL = sorted(rmap.panels)                                    # the ROI's panels
print("ROI 'wide' is on panels {}; reducing panels {}".format([k + 1 for k in rmap.panels], [k + 1 for k in SEL]))
fullframe.set_panels(cfg, [k + 1 for k in SEL], "sim_octal")


# fake time-tool log: edge -> shift = 0.01 x edge pulse; every 6th shot a bad edge
def fake_edges(cfg_: Any, run: int, current_only: bool = True) -> Any:
    tags = np.asarray(_tags(int(run)), np.int64)
    i = tags // feed.tag_step
    edge = (i * 37 % 200).astype(float)
    good = (i % 6 != 3)
    return pd.DataFrame({"tag": tags, "edge": edge, "good": good,
                         "shift_pulse": np.where(good, 0.01 * edge, np.nan)})


setattr(timetool, "load_edges", fake_edges)
cfg.set("timetool.calibration", {"unit": "pulse", "a": 0.01})


class Args:
    scan, array, panels, max_tags, workers = -1, "sim_octal", None, None, 1
    step_reference, pairs, tt, bin_ps, min_shots = True, True, True, 0.1, 3
    out = None  # type: Optional[str]


# ---------------------------------------------------------------- brute force
def corrected(tag: int) -> np.ndarray:
    """keV per pixel of the chosen panels: all panels corrected, then cut."""
    data = feed.read(int(tag), list(range(len(feed.det_ids))))
    x = corr_all.apply([data[k] for k in range(len(feed.det_ids))])
    return np.stack([x[k] * kev_all[k] for k in SEL]).astype(np.float32)


def flags_of(run: int) -> Dict[str, np.ndarray]:
    tags = _tags(run)
    return scan_analysis.shot_flags(cfg, 3, run, TAGHI, tags)


H, W = feed.src.base[0].shape
PIX = np.sort(np.random.default_rng(7).choice(len(SEL) * H * W, 4000, replace=False))
"""The pixels compared with numpy (np.nanquantile loops over pixels in Python: all of them would take hours)."""


def qstats(stack: np.ndarray) -> np.ndarray:
    """(4, pixels) with NaN / inf left out, via numpy per pixel (nanquantile); for full images only
    the pixels PIX."""
    m = np.asarray(stack).reshape(len(stack), -1)
    if m.shape[1] == len(SEL) * H * W:
        m = m[:, PIX]
    s = np.where(np.isfinite(m), m, np.nan).astype(np.float64)
    with np.errstate(invalid="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            q = np.nanquantile(s, scanroi.QUANTILES, axis=0)
            m = np.nanmean(s, axis=0)
    return np.concatenate([q, m[None]], axis=0).astype(np.float32)


def close(a: np.ndarray, b: np.ndarray, what: str) -> None:
    a, b = np.asarray(a), np.asarray(b)
    if a.size != b.size:                  # a full image against numpy on the pixels PIX
        a = a.reshape(-1)[PIX] if a.size > b.size else a.reshape(-1)
        b = b.reshape(-1)[PIX] if b.size > a.size else b.reshape(-1)
    ok = np.allclose(a, b, rtol=2e-5, atol=1e-5, equal_nan=True)
    if not ok:
        bad = ~np.isclose(a, b, rtol=2e-5, atol=1e-5, equal_nan=True)
        idx = np.argwhere(bad)[0]
        raise AssertionError("{}: {} pixels differ, e.g. at {}: {} vs {}".format(
            what, int(bad.sum()), tuple(idx), a[tuple(idx)], b[tuple(idx)]))


# ---------------------------------------------------------------- 0. column_stats == numpy
rng = np.random.default_rng(1)
V = rng.poisson(0.7, size=(300, 37)).astype(np.float32)
V[5, :10] = np.nan
V[6, :] = np.nan
V[7, 3] = np.inf
got = fullframe.column_stats(V)
close(got, qstats(V.T), "column_stats")
print("--- 0. column_stats == numpy (quantiles, mean; NaN / inf left out)")

# ---------------------------------------------------------------- 1. the reduction
print("--- 1. reduction (1 process)")
del READS[:]
out = fullframe.Reduction(Config(), Args()).run()
assert out is not None and os.path.exists(out), out
assert out.endswith("fullframe_{}{}.h5".format(feed.run0, fullframe.panels_tag(SEL, 8)))
read_panels = sorted(set(k for k, _ in READS))
assert read_panels == SEL, (read_panels, SEL)
ff = fullframe.FullFrame(out)
H, W = feed.src.base[0].shape
for name in ("steps/raw", "steps/per_i0_1", "steps/raw_with_i0_1", "steps/pairs_raw", "steps/pairs_per_i0_1",
             "bins/raw", "bins/per_i0_1", "bins/onoff_raw", "bins/onoff_per_i0_1",
             "steps/pairs_diff_raw", "steps/pairs_diff_per_i0_1", "bins/diff_raw", "bins/diff_per_i0_1"):
    assert name in ff.f, name
assert ff.f["steps/raw"].shape == (feed.n_steps, 2, 4, len(SEL), H, W), ff.f["steps/raw"].shape
assert os.path.exists(out[:-3] + "_preview.png") and os.path.exists(out[:-3] + ".json")
print("  ok: only panels {} read; datasets and shapes right; {!r}".format([k + 1 for k in SEL], ff))

# ---------------------------------------------------------------- 2. every pixel per step
print("--- 2. every pixel, every step and state, raw and per i0 == numpy")
images = {}  # type: Dict[Any, np.ndarray]
per_step = {}  # type: Dict[int, Dict[str, Any]]
for j, run in enumerate(sorted(RUNS)):
    fl = flags_of(run)
    tags = np.asarray(_tags(run))
    keep = fl["use"] & (fl["state"] >= 0) & ~np.isin(tags, list(UNREADABLE))
    t, st, R = tags[keep], fl["state"][keep], fl["ref_i0_1"][keep]
    imgs = np.stack([corrected(x) for x in t])
    for x, im in zip(t, imgs):
        images[(run, int(x))] = im
    per_step[j] = {"tags": t, "state": st, "R": R, "imgs": imgs}
    for s, sname in enumerate(("on", "off")):
        m = st == s
        exp = qstats(imgs[m])
        for k, stat in enumerate(fullframe.STATS):
            close(ff.image("raw", step=j, state=sname, stat=stat), exp[k], "raw step {} {} {}".format(j, sname, stat))
        mv = m & np.isfinite(R)
        exp_r = qstats(imgs[mv] / R[mv][:, None, None, None].astype(np.float32))
        exp_w = qstats(imgs[mv])
        for k, stat in enumerate(fullframe.STATS):
            close(ff.image("i0_1", step=j, state=sname, stat=stat), exp_r[k], "per i0 step {} {}".format(j, sname))
            close(ff.image("raw_with_i0_1", step=j, state=sname, stat=stat), exp_w[k], "raw with i0 {}".format(j))
        assert int(ff.steps["n_" + sname].iloc[j]) == int(m.sum())
        assert int(ff.steps["n_{}_i0_1".format(sname)].iloc[j]) == int(mv.sum())
assert int(ff.steps["n_unreadable"].iloc[2]) == 1
print("  ok: {} steps x 2 states x 4 stats x {} pixels; the unreadable shot is left out".format(
    feed.n_steps, len(SEL) * H * W))

# ---------------------------------------------------------------- 3. consistent with the ROI analysis
print("--- 3. ROI 'wide' from the mean images == the ROI analysis")
job = scan_analysis.prepare(cfg, settings, -1, None)
rows = scan_analysis.process(cfg, settings, job["items"], job["run_info"], job["infos"])
shots_roi = pd.DataFrame(rows)
for j, run in enumerate(sorted(RUNS)):
    for s, sname in enumerate(("on", "off")):
        mean_img = ff.image("raw", step=j, state=sname, stat="mean")
        from_ff = sum(float(mean_img[SEL.index(k)].ravel()[idx].sum()) for k, idx in rmap.parts[0]) / rmap.n_pixels[0]
        part = shots_roi[(shots_roi["run"] == run) & (shots_roi["state"] == s) & shots_roi["read"]]
        assert np.isclose(from_ff, part["roi_wide"].mean(), rtol=1e-5), (j, sname, from_ff, part["roi_wide"].mean())
print("  ok: mean over the ROI's pixels of the per-pixel mean == mean of the ROI values, every step and state")

# ---------------------------------------------------------------- 4. pairs, step reference, on / off
print("--- 4. pairs, step reference, on / off")
for j in range(feed.n_steps):
    d = per_step[j]
    on = np.nonzero(d["state"] == 0)[0]
    off = np.nonzero(d["state"] == 1)[0]
    near = off[scanroi.nearest_off(d["tags"][on], d["tags"][off])]
    with np.errstate(invalid="ignore", divide="ignore"):
        exp = qstats(d["imgs"][on] / d["imgs"][near])
    close(ff.image("pairs", step=j, stat="median"), exp[1], "pairs step {}".format(j))
    ok = np.isfinite(d["R"])
    on2 = np.nonzero((d["state"] == 0) & ok)[0]
    off2 = np.nonzero((d["state"] == 1) & ok)[0]
    near2 = off2[scanroi.nearest_off(d["tags"][on2], d["tags"][off2])]
    Rf = d["R"].astype(np.float32)[:, None, None, None]
    with np.errstate(invalid="ignore", divide="ignore"):
        exp2 = qstats((d["imgs"][on2] / Rf[on2]) / (d["imgs"][near2] / Rf[near2]))
    close(ff.image("pairs_i0_1", step=j, stat="q84"), exp2[2], "pairs per i0 step {}".format(j))
    close(ff.image("pairs_diff", step=j, stat="median"), qstats(d["imgs"][on] - d["imgs"][near])[1],
          "pairs on - off step {}".format(j))
    close(ff.image("pairs_diff_i0_1", step=j, stat="mean"),
          qstats(d["imgs"][on2] / Rf[on2] - d["imgs"][near2] / Rf[near2])[3], "pairs on - off per i0 step {}".format(j))
    a = qstats(d["imgs"][d["state"] == 0])
    b = qstats(d["imgs"][d["state"] == 1])
    with np.errstate(invalid="ignore", divide="ignore"):
        r_exp = scanroi.ratio_of_medians(a[1], a[1] - a[0], a[2] - a[1], b[1], b[1] - b[0], b[2] - b[1])
        r_got = ff.onoff("raw", step=j)
    for x, y, nm in zip(r_got, r_exp, ("ratio", "lo", "hi")):
        close(x, y, "onoff {} step {}".format(nm, j))
    d_got = ff.difference("raw", step=j)
    d_exp = (a[1] - b[1], np.sqrt((a[1] - a[0]) ** 2 + (b[2] - b[1]) ** 2), np.sqrt((a[2] - a[1]) ** 2 + (b[1] - b[0]) ** 2))
    for x, y, nm in zip(d_got, d_exp, ("diff", "lo", "hi")):
        close(x, y, "on - off {} step {}".format(nm, j))
    close(ff.difference("raw", step=j, stat="mean")[0], a[3] - b[3], "on - off of the means step {}".format(j))
    with np.errstate(invalid="ignore", divide="ignore"):
        sr = ff.step_referenced("i0_1", step=j)
    Rv = d["R"][(d["state"] == 0) & ok]
    with np.errstate(invalid="ignore", divide="ignore"):
        close(sr[0], ff.image("raw_with_i0_1", step=j, state="on") / np.float32(np.median(Rv)), "step ref")
print("  ok")

# ---------------------------------------------------------------- 5. time-tool bins
print("--- 5. time-tool bins")
bt = ff.bins
assert bt is not None and len(bt) > 3
shots_ff = ff.shots
n_img = 0
for i, b in enumerate(bt["bin"]):
    members = shots_ff[shots_ff["bin"] == b]
    assert len(members) == int(bt["n"].iloc[i])
    img = ff.image("raw", bin=i)
    if not bt["computed"].iloc[i]:
        assert np.isnan(img).all() and int(bt["n"].iloc[i]) < 3
        continue
    n_img += 1
    stack = np.stack([images[(int(r), int(t))] for r, t in zip(members["run"], members["tag"])])
    close(ff.image("raw", bin=i, stat="median"), qstats(stack)[1], "bin {} raw".format(i))
    R = members["ref_i0_1"].to_numpy(np.float64)
    okR = np.isfinite(R)
    close(ff.image("i0_1", bin=i, stat="q16"),
          qstats(stack[okR] / R[okR].astype(np.float32)[:, None, None, None])[0], "bin {} per i0".format(i))
    # step-wise on / off: shot / median off image of its step
    offmed = np.stack([ff.image("raw", step=int(j), state="off") for j in members["step_index"]])
    with np.errstate(invalid="ignore", divide="ignore"):
        close(ff.image("onoff", bin=i, stat="mean"), qstats(stack / offmed)[3], "bin {} onoff".format(i))
        offmed_r = np.stack([ff.image("i0_1", step=int(j), state="off") for j in members["step_index"]])
        exp = qstats((stack[okR] / R[okR].astype(np.float32)[:, None, None, None]) / offmed_r[okR])
    close(ff.image("onoff_i0_1", bin=i, stat="median"), exp[1], "bin {} onoff per i0".format(i))
    close(ff.image("diff", bin=i, stat="q16"), qstats(stack - offmed)[0], "bin {} on - off".format(i))
    close(ff.image("diff_i0_1", bin=i, stat="mean"),
          qstats(stack[okR] / R[okR].astype(np.float32)[:, None, None, None] - offmed_r[okR])[3],
          "bin {} on - off per i0".format(i))
assert n_img == int(bt["computed"].sum()) and n_img < len(bt)
print("  ok: {} bins, {} with images (>= 3 shots), every statistic == numpy".format(len(bt), n_img))
geom_shape = ff.assemble(ff.image("raw", step=0)).shape
print("  assembled image {} ; info: {} shots used".format(geom_shape, ff.info["shots"]["used"]))

# ---------------------------------------------------------------- 5b. point detectors
print("--- 5b. point detectors per shot and per step")
pts = ff.points
assert len(pts) == len(ff.shots), (len(pts), len(ff.shots))
assert "i0_1" in pts.columns and "delay_m" not in pts.columns, list(pts.columns)       # delay_m: the scanned motor
assert "optical_shutter" not in pts.columns, list(pts.columns)                          # the pump detector
for label, meta in ff.point_info.items():
    assert all(isinstance(v, str) for v in meta.values()), meta                         # no None (HDF5 attributes)
    assert "/" not in label, label
    assert isinstance(ff.f["point_detectors"][label].attrs["detector"], str)
for run in sorted(RUNS):
    fl = flags_of(run)
    in_run = (ff.shots["run"] == run).to_numpy()
    by_tag = dict(zip(np.asarray(_tags(run)), fl["ref_i0_1"]))
    exp = np.array([by_tag[t] for t in ff.shots["tag"].to_numpy()[in_run]])
    close(pts["i0_1"].to_numpy()[in_run], exp, "i0_1 per shot, run {}".format(run))
ps = ff.point_steps
assert ps is not None and len(ps) == len(ff.steps)
for j in range(feed.n_steps):
    for s, sname in enumerate(("on", "off")):
        m = (ff.shots["step_index"] == j) & (ff.shots["state"] == s) & ff.shots["read"].astype(bool)
        v = pts["i0_1"][m].to_numpy()
        v = v[np.isfinite(v)]
        assert int(ps["i0_1_{}_n".format(sname)].iloc[j]) == len(v)
        close(ps["i0_1_{}_mean".format(sname)].iloc[j], v.mean(), "i0_1 mean step {} {}".format(j, sname))
        close(ps["i0_1_{}_median".format(sname)].iloc[j], np.median(v), "i0_1 median step {} {}".format(j, sname))
        assert int(ps["i0_1_{}_n".format(sname)].iloc[j]) == int(ff.steps["n_{}_i0_1".format(sname)].iloc[j])
print("  ok: {} point detectors per shot ({}); per step and state == numpy".format(len(pts.columns),
                                                                                   ", ".join(pts.columns)))
ff.close()

# ---------------------------------------------------------------- 6. real worker processes
print("--- 6. statistics in 3 worker processes (multiprocessing, fork) give the same numbers")
import multiprocessing  # noqa: E402


class HybridPool(object):
    """dbpy / stpy are fakes of this process: reading here, the statistics in real processes."""

    def __init__(self) -> None:
        # fork: "spawn" would run this test script again in every worker (it has no __main__ guard);
        # the tasks are pickled either way, as in the real reduction
        self.pool = multiprocessing.get_context("fork").Pool(3)

    def map(self, func: Any, items: Any, chunksize: int = 1) -> Any:
        if func is fullframe._block_job:
            return self.pool.map(func, items, chunksize=chunksize)
        return [func(x) for x in items]

    def terminate(self) -> None:
        self.pool.terminate()

    def join(self) -> None:
        self.pool.join()


class Args2(Args):
    out = os.path.join(tmp, "parallel.h5")
    pairs, step_reference = False, False


pool = HybridPool()
try:
    red = fullframe.Reduction(Config(), Args2(), pool=pool)
    red.n_proc = 3
    out2 = red.run()
finally:
    pool.terminate()
    pool.join()
assert out2 is not None
f1, f2 = fullframe.FullFrame(out), fullframe.FullFrame(out2)
assert f1.bins is not None
for j in range(feed.n_steps):
    for sname in ("on", "off"):
        close(f2.image("raw", step=j, state=sname, stat="q16"), f1.image("raw", step=j, state=sname, stat="q16"),
              "parallel raw")
        close(f2.image("i0_1", step=j, state=sname, stat="mean"), f1.image("i0_1", step=j, state=sname, stat="mean"),
              "parallel per i0")
for i in range(len(f1.bins)):
    close(f2.image("onoff", bin=i), f1.image("onoff", bin=i), "parallel bins")
    close(f2.image("diff", bin=i), f1.image("diff", bin=i), "parallel bins on - off")
assert "steps/pairs_raw" not in f2.f
info = json.load(open(out2[:-3] + ".json"))
print("  ok ({} h5py); timing {}".format("real" if REAL_H5 else "stand-in for", {
    k: round(v, 2) for k, v in info["timing_s"].items()}))
print("\nOK  (files in {})".format(tmp))
