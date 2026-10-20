#!/usr/bin/env python3
"""Online scan viewer logic with simulated scans (no SACLA library, no window).

    python tests/test_scan_view.py     # needs output/diag/mpccd_panels.npz

The test feed's clock is advanced by hand, so every case is exact:
1. keeping up: 3 readers follow a scan shot by shot; every usable shot is read once,
   the ROI sums equal a brute-force sum over all shots, pump on / off counted right
2. falling behind: the clock jumps to the end of the scan with a small online buffer;
   the newest shots are read first, the rest is counted as lost, nothing twice
3. the next scan: a new scan id starts from zero
4. plotting numbers: steps, keV per pixel per shot, Poisson errors, on / off ratio
5. run logger status from its lock file (heartbeat)
All in a temporary folder; your config is untouched.
"""
import os
import sys
import tempfile
import time
from typing import Any, Dict, List

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp(prefix="silas_scanview_")
os.environ["SILAS_PROFILE"] = "local"
os.environ["SILAS_CONFIG"] = os.path.join(tmp, "test.json")

from silas.core import energy, mpccd, roi, runlog, scanroi  # noqa: E402
from silas.core.config import Config  # noqa: E402
from silas.core.corrections import save_dark  # noqa: E402
from silas.online import scan_feed  # noqa: E402
from silas.online.mpccd_view import Settings  # noqa: E402
from silas.online.scan_view import ScanWorker, roi_setup  # noqa: E402

cfg = Config()
cfg.set("paths.output", tmp)
cfg.set("photon_energy_keV", 9.9)
key = mpccd.add_mpccd(cfg, "MPCCD-8-2-001", alias="sim_octal")
roi.add_roi(cfg, "spot", (-797, -757), (-577, -517), name="sim_octal")       # inside panel 1
roi.add_roi(cfg, "wide", (216, 616), (189, 589), name="sim_octal")           # panels 7, 8
mpccd.set_option(cfg, "corrections.threshold", True, key)
mpccd.set_option(cfg, "corrections.threshold_keV", 2.0, key)


def new_feed(buffer: int = 100000) -> Any:
    f = scan_feed.TestFeed(cfg, tag_step=1, n_steps=6, run_len=36, gap=6, buffer=buffer)
    f.now_tag = f.tag0
    return f


feed = new_feed()
dark_path = os.path.join(tmp, "dark.npz")
save_dark(dark_path, feed.det_ids, feed.src.pedestal, 1)
mpccd.set_option(cfg, "corrections.dark_file", dark_path, key)
mpccd.set_option(cfg, "corrections.dark", True, key)
settings = Settings("sim_octal")
assert feed.n_states == 2, "the standard config has a pump detector (optical_shutter)"


def brute_force(f: Any, info: Dict[str, Any]) -> Dict[str, Any]:
    """Expected sums of one run: every usable shot, all panels corrected in full."""
    rmap, kev, corr = roi_setup(f.det_ids, f.infos(), settings)
    assert rmap is not None
    tags = f.tags(info, 10 ** 12)
    use, state = f.flags(info, tags)
    S = np.zeros((2, len(rmap.names)))
    N = np.zeros(2, np.int64)
    for t, u, st in zip(tags, use, state):
        if not u:
            continue
        data = f.read(t, list(range(len(f.det_ids))))
        x = corr.apply([data[k] for k in range(len(f.det_ids))])
        S[st] += [sum(float(x[k].ravel()[idx].sum()) * float(kev[k]) for k, idx in parts) for parts in rmap.parts]
        N[st] += 1
    return {"S": S, "N": N, "xray_off": int((~use).sum()), "tags": len(tags)}


def run_until_idle(workers: List[ScanWorker], total: scanroi.ScanSums) -> None:
    busy = True
    while busy:
        busy = False
        for w in workers:
            busy = w.step() or busy
        for w in workers:
            total.merge(w.take()["sums"])


# ---------------------------------------------------------------- 1. keeping up
print("--- 1. keeping up: 3 readers, the clock advances 5 tags at a time")
workers = [ScanWorker(feed, settings, wid=i, nw=3) for i in range(3)]
total = scanroi.ScanSums(2, 2)
first_scan = None
t0 = time.time()
end = feed._layout(feed.n_steps - 1)[1]                 # last tag of the last step
while feed.now_tag < end + 5:
    feed.now_tag += 5
    sc = feed.scan()
    if first_scan is None and sc is not None:
        first_scan = sc["scan_id"]
    for w in workers:
        w.set_scan(sc)
    run_until_idle(workers, total)
dt = time.time() - t0
sc = feed.scan()
assert sc is not None and sc["scan_id"] == first_scan and len(sc["runs"]) == feed.n_steps
assert all(r["end_tag"] is not None for r in sc["runs"])
for info in sc["runs"]:
    exp = brute_force(feed, info)
    got = total.runs[info["run"]]
    assert got["lost"] == 0 and got["no_pump"] == 0, got
    assert got["done"] + got["xray_off"] == exp["tags"], (got["done"], got["xray_off"], exp["tags"])
    assert got["xray_off"] == exp["xray_off"]
    assert np.array_equal(got["N"], exp["N"]), (got["N"], exp["N"])
    assert np.allclose(got["S"], exp["S"], rtol=1e-6), (got["S"], exp["S"])
t = total.totals()
print("  ok: {} steps, {} shots read once each ({} X-rays off dropped), sums == brute force; "
      "{:.0f} shots/s for 3 readers in one process".format(len(sc["runs"]), t["done"], t["xray_off"], t["done"] / dt))

# ---------------------------------------------------------------- 2. falling behind
print("--- 2. falling behind: the clock jumps to the end, buffer of 60 tags")
feed2 = new_feed(buffer=60)
feed2.now_tag = feed2._layout(feed2.n_steps - 1)[1] + 2
sc2 = feed2.scan()
w2 = [ScanWorker(feed2, settings, wid=i, nw=2) for i in range(2)]
for w in w2:
    w.set_scan(sc2)
total2 = scanroi.ScanSums(2, 2)
run_until_idle(w2, total2)
t2 = total2.totals()
all_tags = sum(len(feed2.tags(r, 10 ** 12)) for r in sc2["runs"])
assert t2["done"] + t2["lost"] + t2["xray_off"] + t2["no_pump"] == all_tags, (t2, all_tags)
assert t2["lost"] > 0 and t2["done"] > 0
newest_run = sc2["runs"][-1]["run"]
assert total2.runs[newest_run]["lost"] == 0, "the newest step is still in the buffer"
assert w2[0].backlog() == 0 and w2[1].backlog() == 0
print("  ok: {} shots of the scan: {} read (newest first), {} lost (older than the buffer), "
      "{} dropped; no backlog left".format(all_tags, t2["done"], t2["lost"], t2["xray_off"]))

# ---------------------------------------------------------------- 3. next scan
print("--- 3. the next scan starts")
feed.now_tag = feed._layout(feed.per_scan)[0] + 10     # 10 tags into step 1 of scan 2
sc3 = feed.scan()
assert sc3 is not None and sc3["scan_id"] != first_scan and len(sc3["runs"]) == 1
for w in workers:
    w.set_scan(sc3)
total3 = scanroi.ScanSums(2, 2)
run_until_idle(workers, total3)
assert set(total3.runs) == {sc3["runs"][0]["run"]}, "only the new scan's run"
print("  ok: scan {} -> scan {}: readers started from zero ({} shots of its first step)".format(
    first_scan, sc3["scan_id"], total3.totals()["done"]))

# ---------------------------------------------------------------- 4. numbers for the plot
print("--- 4. per step: keV per pixel per shot, Poisson errors, pump on / off")
rmap4, kev4, corr4 = roi_setup(feed.det_ids, feed.infos(), settings)
assert rmap4 is not None
table = scanroi.step_table(total, sc["runs"])
m = scanroi.means(table, rmap4.n_pixels, 9.9)
r, re = scanroi.ratio(m["mean"], m["err"])
assert table[0]["x_is_value"] and np.allclose(m["x"], [1100 + 34 * s for s in range(feed.n_steps)])
# by hand for one step and ROI
st, i = table[3], 1
mean_on = st["S"][0, i] / (st["N"][0] * rmap4.n_pixels[i])
assert np.isclose(m["mean"][0, 3, i], mean_on)
assert np.isclose(m["err"][0, 3, i], np.sqrt(st["S"][0, i] * 9.9) / (st["N"][0] * rmap4.n_pixels[i]))
print("  step  delay   on/off (wide)   expected")
for s in range(feed.n_steps):
    print("  {:4d}  {:5.0f}   {:5.3f} +- {:5.3f}   {:5.3f}".format(
        s + 1, m["x"][s], r[s, 1], re[s, 1], feed.factor(s + 1, True)))
early, late = np.nanmean(r[:2, 1]), np.nanmean(r[-2:, 1])
assert late < early - 0.15, "the simulated pump effect must show"
print("  ok: means and errors as defined; the simulated pump effect shows ({:.2f} -> {:.2f})".format(early, late))

# ---------------------------------------------------------------- 5. run logger status
print("--- 5. run logger status (lock file + heartbeat)")
ok, text = runlog.logger_status(cfg)
assert not ok
lock = runlog._Lock(runlog.runlog_path(cfg))
lock.acquire()
ok, text = runlog.logger_status(cfg)
assert ok, text
old = time.time() - 300
os.utime(lock.path, (old, old))
ok, text_old = runlog.logger_status(cfg)
assert not ok and "not updated" in text_old
lock.touch()
assert runlog.logger_status(cfg)[0]
lock.release()
assert not runlog.logger_status(cfg)[0]
print("  ok: none -> '{}' -> hanging detected -> heartbeat -> released".format(text))
print("\nOK  (files in {})".format(tmp))
